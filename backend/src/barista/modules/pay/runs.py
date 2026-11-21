"""KỲ LƯƠNG (0150 §7): nháp (tính lại thoải mái) → gửi duyệt (đầu vào đóng băng, chuỗi ``pay_run``) → chốt (ghi nghĩa vụ từng phiếu qua
``acct.recognize_payable``, ngày hạch toán có chủ đích). "Đã chi" không lưu ở đây — đọc từ Tài chính. Kỳ chạy bóng chỉ để so số."""

from collections.abc import Iterable
from datetime import date
from typing import Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.api import (
    Ctx,
    Day,
    Denied,
    Input,
    Invalid,
    NoInput,
    NotFound,
    can_run,
    entities,
    field,
    money,
    month_of,
    regulations,
    today,
    ui,
)
from barista.modules.pay import LAW, PAYABLE, RUN_KIND, RUN_STATUS, engine, m
from barista.modules.pay.policy import components_now
from barista.modules.pay.work import RecordWork, digest, record

LOCKABLE = "Kỳ chạy bóng chỉ để so số với công cụ cũ — không gửi duyệt, không chốt, không ghi sổ, người lao động không thấy"


def _need_payroll(ctx: Ctx) -> None:
    if not ctx.p.has_scope("pay", "all") or not ctx.p.has_scope("pay.slips", "all"):
        raise Denied("Cần quyền phụ trách lương cả công ty")


def _rules(day: date, *, pit: bool) -> dict[str, Any]:
    v = regulations.version_on(LAW, day)
    if v is None:
        return {}
    return {code: {**(p["value"] if isinstance(p["value"], dict) else {"value": p["value"]}), "ref": f"{v.ref}:{code}", "status": p["status"],
                   "verified_by": p["verified_by"], "label": p["label"]}
            for code, p in v.params.items() if code.startswith("PIT_") == pit}


def law_on(day: date, period_end: date | None = None) -> dict[str, Any]:
    """Quy tắc luật của một phiếu, kèm trạng thái xác minh RIÊNG từng quy tắc. Mỗi loại một ngày căn cứ (0150 §4.4): thuế (``PIT_*``) theo NGÀY CHI;
    bảo hiểm, công đoàn, làm thêm theo THÁNG CÔNG (cuối tháng công). ``period_end`` trống ⇒ mọi quy tắc theo ``day``."""
    return {**_rules(period_end or day, pit=False), **_rules(day, pit=True)}


def posting_day(period: date) -> date:
    """Ngày hạch toán = cuối tháng công; kỳ chốt trước cuối tháng (chi ngày 25) ⇒ ngày chốt — sổ cái không ghi trước cho tương lai (0150 §7, [HỎI B2])."""
    return min(regulations.period_end(period), today())


async def _lock(ctx: Ctx, entity: UUID) -> None:
    await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"pay.run:{ctx.p.tenant_id}:{entity}",))


async def _run(ctx: Ctx, run_id: UUID, *, lock: bool = False) -> dict[str, Any]:
    row = await ctx.one("SELECT * FROM pay.runs WHERE id=%s" + (" FOR UPDATE" if lock else ""), (run_id,))
    if not row:
        raise NotFound("Không thấy kỳ lương")
    return row


async def _clear(ctx: Ctx, run_id: UUID) -> None:
    for sql in ("DELETE FROM pay.slip_entries WHERE slip_id IN (SELECT id FROM pay.slips WHERE run_id=%s)",
                "DELETE FROM pay.slip_lines WHERE slip_id IN (SELECT id FROM pay.slips WHERE run_id=%s)",
                "DELETE FROM pay.slips WHERE run_id=%s"):
        await ctx.exec(sql, (run_id,))


_OPEN_ENTRIES = """SELECT e.id,e.party_id,e.work_date,e.kind,e.unit,e.quantity,e.amount_vnd,e.role_key,e.component,e.source
   FROM pay.work_entries e WHERE e.entity_id=%(entity)s AND e.work_date<=%(end)s AND NOT EXISTS (
     SELECT 1 FROM pay.slip_entries se JOIN pay.slips s ON s.id=se.slip_id JOIN pay.runs r ON r.id=s.run_id
     WHERE se.entry_id=e.id AND (r.status<>'draft' OR NOT %(shadow)s))
   ORDER BY e.work_date,e.created_at,e.id"""


async def _fixed(ctx: Ctx, run: dict[str, Any], rates: list[dict[str, Any]], end: date) -> list[dict[str, Any]]:
    """LƯƠNG CỐ ĐỊNH của tháng công thành DÒNG CÔNG (review 09/11): khoá ``pay.fixed:<người>:<tháng>:<khoản>`` — một tháng trả một lần dù bao nhiêu
    pháp nhân / kỳ (khoá duy nhất của sổ công + một dòng công một phiếu); phiếu dừng thì dòng ở lại cho kỳ sau. Mức đổi trong tháng chưa chốt ⇒ thêm
    dòng chênh (``…:2``, ``…:3``), không sửa dòng cũ. Kỳ chạy bóng KHÔNG ghi gì: trả dòng chênh ảo để tính thử."""
    latest: dict[tuple[UUID, str], dict[str, Any]] = {}
    for r in rates:   # mức tháng mới nhất có hiệu lực tới cuối tháng công (rates đã xếp theo lúc ghi)
        if r["unit"] == "month" and r["party_id"] and r["effective_from"] <= end:   # khoản cố định hoặc theo ngày công (mức tháng)
            k = (r["party_id"], r["component"])
            if k not in latest or r["effective_from"] >= latest[k]["effective_from"]:
                latest[k] = r
    virtual = []
    for (party, code), r in sorted(latest.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        prefix = f"pay.fixed:{party}:{run['period']:%Y-%m}:{code}"
        have = await ctx.one("""SELECT coalesce(sum(amount_vnd),0)::bigint AS v,count(*) AS n FROM pay.work_entries
                                WHERE key=%(k)s OR left(key, length(%(k)s) + 1) = %(k)s || ':'""", {"k": prefix})
        assert have is not None
        delta = int(r["amount_vnd"]) - int(have["v"])
        if not delta:
            continue
        key = prefix if not have["n"] else f"{prefix}:{have['n'] + 1}"
        if run["kind"] == "shadow":
            virtual.append({"id": f"virtual:{key}", "party_id": party, "work_date": run["period"], "kind": "amount", "unit": "", "quantity": 0,
                            "amount_vnd": delta, "role_key": "", "component": code, "source": "pay.fixed"})
        else:
            await record(ctx, RecordWork(source="pay.fixed", source_ref=f"{run['period']:%Y-%m}:{code}", key=key, party_id=party, entity_id=run["entity_id"],
                                         work_date=run["period"], kind="amount", amount_vnd=delta, component=code,
                                         evidence={"rate_id": str(r["id"]), "amount_vnd": int(r["amount_vnd"])}))
    return virtual


def _by_rule(refs: Iterable[str]) -> dict[str, set[str]]:
    """``<bộ>@<phiên bản>:<mã>`` → ``{<bộ>:<mã>: {phiên bản…}}`` (bỏ khoản công ty ``company:…``)."""
    out: dict[str, set[str]] = {}
    for ref in refs:
        pack_ver, _, code = ref.partition(":")
        pack, _, ver = pack_ver.partition("@")
        if code and ver:
            out.setdefault(f"{pack}:{code}", set()).add(ver)
    return out


def law_changes(before: Iterable[str], after: Iterable[str]) -> list[dict[str, Any]]:
    """Quy tắc luật CÙNG MÃ mà phiên bản đổi giữa hai lần tính (``before`` = ``rule_ref`` đã lưu, ``after`` = đang áp / vừa tính). Phát hành phiên bản
    luật HỒI TỐ làm kỳ nháp đổi số khi tính lại — danh sách này là cờ báo, không im lặng (0154). Kỳ đã gửi duyệt / chốt không tính lại nên không đổi."""
    old, new = _by_rule(before), _by_rule(after)
    return [{"rule": k, "from": sorted(old[k]), "to": sorted(new[k])} for k in sorted(old.keys() & new.keys()) if old[k] != new[k]]


async def _slip_refs(ctx: Ctx, run_id: UUID) -> tuple[list[str], int]:
    """(``rule_ref`` luật đã lưu trên các dòng phiếu của kỳ, tổng thực nhận) — dựa vào dòng đã lưu, không tính lại."""
    net = int(await ctx.val("SELECT coalesce(sum(net_vnd) FILTER (WHERE held_reason = ''), 0) FROM pay.slips WHERE run_id=%s", (run_id,)))
    return await _law_refs(ctx, run_id), net


async def build(ctx: Ctx, run: dict[str, Any]) -> dict[str, Any]:
    """Tính lại MỌI phiếu của kỳ nháp từ dữ liệu hiện tại: dòng công chưa vào phiếu nào (tới cuối tháng công), mức, khoản, loại hợp đồng tại ngày chi,
    quy tắc luật tại ngày chi, tiền ứng còn cấn. Mỗi phiếu lưu bản chụp đầu vào + mã băm; phiếu không dừng của kỳ thật GIỮ dòng công của nó."""
    shadow = run["kind"] == "shadow"
    await _lock(ctx, run["entity_id"])
    refs_before, net_before = await _slip_refs(ctx, run["id"])
    await _clear(ctx, run["id"])
    end = regulations.period_end(run["period"])
    rates = await ctx.all("SELECT id,component,unit,role_key,party_id,amount_vnd,effective_from FROM pay.rates ORDER BY created_at,id")
    virtual = await _fixed(ctx, run, rates, end)
    rows = [*await ctx.all(_OPEN_ENTRIES, {"entity": run["entity_id"], "end": end, "shadow": shadow}), *virtual]
    comps = {k: v for k, v in (await components_now(ctx)).items() if v["active"]}
    by_party: dict[UUID, list[dict[str, Any]]] = {}
    for e in rows:
        by_party.setdefault(e["party_id"], []).append(e)
    ids = sorted(by_party, key=str)
    await ctx.exec("UPDATE pay.runs SET computed_at=now() WHERE id=%s", (run["id"],))
    if not ids:
        return {"slips": 0, "held": 0, "law_changes": []}
    terms = await ctx.call_system("hr.terms_on", party_ids=ids, on=run["pay_date"])
    law = law_on(run["pay_date"], end)
    prior = await _prior(ctx, run, ids)
    sources = sorted({c["source"] for c in comps.values() if c["calculator"] == "advance_offset"})
    advances = await ctx.call_system("acct.advances_left", party_ids=ids, entity_id=run["entity_id"], sources=sources) if sources else {}
    snap = {code: {k: c[k] for k in ("code", "version", "label", "kind", "calculator", "taxable", "expense_account", "source")} for code, c in comps.items()}
    held_n = 0
    for pid in ids:
        who = await ctx.call_system("parties.resolve_for_payment", party_id=pid)
        mine = by_party.get(pid, [])
        units = {e["unit"] for e in mine if e["kind"] == "qty"}
        inputs = {
            "version": 2, "run_kind": run["kind"], "period": run["period"].isoformat(), "period_end": end.isoformat(),
            "pay_date": run["pay_date"].isoformat(), "party": {"id": str(pid), "name": who["display_name"]}, "terms": terms.get(str(pid)),
            "entries": [{"id": str(e["id"]), "work_date": e["work_date"].isoformat(), "kind": e["kind"], "unit": e["unit"], "quantity": str(e["quantity"]),
                         "amount_vnd": e["amount_vnd"], "role_key": e["role_key"], "component": e["component"], "source": e["source"]} for e in mine],
            "rates": [{"id": str(r["id"]), "component": r["component"], "unit": r["unit"], "role_key": r["role_key"],
                       "party_id": str(r["party_id"]) if r["party_id"] else None, "amount_vnd": r["amount_vnd"], "effective_from": r["effective_from"].isoformat()}
                      for r in rates if r["unit"] in units and r["party_id"] in (None, pid)],
            "components": snap, "law": law, "advances_vnd": int(advances.get(str(pid), 0)), "prior": prior.get(str(pid), {}),
        }
        out = engine.compute(inputs)
        held = "; ".join(dict.fromkeys(out["held"]))
        held_n += bool(held)
        sid = await ctx.val(
            """INSERT INTO pay.slips(run_id,run_kind,party_id,user_id,entity_id,period,pay_date,name,gross_vnd,deductions_vnd,net_vnd,held_reason,
                                     unverified,inputs,inputs_hash,figures)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (run["id"], run["kind"], pid, who["user_id"], run["entity_id"], run["period"], run["pay_date"], who["display_name"], out["gross_vnd"],
             out["deductions_vnd"], out["net_vnd"], held, out["unverified"], Jsonb(inputs), digest(inputs), Jsonb(out["figures"])),
        )
        for x in out["lines"]:
            await ctx.exec("""INSERT INTO pay.slip_lines(slip_id,seq,component,label,kind,amount_vnd,basis,rule_ref,account,unverified)
                              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                           (sid, x["seq"], x["component"], x["label"], x["kind"], x["amount_vnd"], x["basis"], x["rule_ref"], x["account"], x["unverified"]))
        if not held and not shadow:
            for e in mine:   # khoá DB: một dòng công tối đa một phiếu (dòng ảo chỉ có ở kỳ bóng)
                await ctx.exec("INSERT INTO pay.slip_entries(slip_id,entry_id) VALUES(%s,%s)", (sid, e["id"]))
    refs_after, net_after = await _slip_refs(ctx, run["id"])
    changes = law_changes(refs_before, refs_after)
    return {"slips": len(ids), "held": held_n, "law_changes": changes, **({"net_before_vnd": net_before, "net_after_vnd": net_after} if changes else {})}


async def _prior(ctx: Ctx, run: dict[str, Any], ids: list[UUID]) -> dict[str, dict[str, Any]]:
    """Phiếu ĐÃ CHỐT trước đó của cùng người: cùng THÁNG CHI (thuế luỹ kế — thu nhập chịu thuế, bảo hiểm đã trừ, thuế đã khấu trừ) và cùng THÁNG CÔNG
    (bảo hiểm chỉ đóng một lần mỗi tháng công). Đọc từ ``figures`` của phiếu đã chốt — số đã ghi sổ, không tính lại."""
    rows = await ctx.all(
        """SELECT party_id,
                  coalesce(sum((figures->>'taxable_vnd')::bigint) FILTER (WHERE date_trunc('month', pay_date) = date_trunc('month', %(pay)s::date)),0)::bigint AS t,
                  coalesce(sum((figures->>'insurance_vnd')::bigint) FILTER (WHERE date_trunc('month', pay_date) = date_trunc('month', %(pay)s::date)),0)::bigint AS i,
                  coalesce(sum((figures->>'pit_vnd')::bigint) FILTER (WHERE date_trunc('month', pay_date) = date_trunc('month', %(pay)s::date)),0)::bigint AS x,
                  bool_or(coalesce((figures->>'insured')::boolean, false) AND period = %(period)s) AS insured
           FROM pay.slips WHERE party_id = ANY(%(ids)s) AND entity_id = %(entity)s AND run_id <> %(run)s AND payable_id IS NOT NULL
           GROUP BY party_id""",
        {"pay": run["pay_date"], "period": run["period"], "ids": ids, "run": run["id"], "entity": run["entity_id"]})
    return {str(r["party_id"]): {"taxable_vnd": int(r["t"]), "insurance_vnd": int(r["i"]), "pit_vnd": int(r["x"]), "insured_period": bool(r["insured"])}
            for r in rows if r["t"] or r["i"] or r["x"] or r["insured"]}


class DraftRun(Input):
    period: Day = field("Tháng công", example="01/10/2026", help="Ngày bất kỳ trong tháng công")
    pay_date: Day = field("Ngày chi", example="05/11/2026", help="Thuế khấu trừ theo ngày chi")
    kind: Literal["regular", "shadow"] = field("Loại kỳ", "regular", options={"regular": RUN_KIND["regular"], "shadow": RUN_KIND["shadow"]},
                                               help="Chạy bóng: tính cả quy tắc chưa xác minh để kế toán so với công cụ cũ — không chốt, không chi")
    entity_code: str = field("Pháp nhân", "", options_query="admin.entity_options", help="Trống = pháp nhân mặc định")


@m.command("draft_run", resource="pay", title="Soạn kỳ lương", agent="propose", risk="low")
async def draft_run(ctx: Ctx, inp: DraftRun) -> dict[str, Any]:
    """Kỳ NHÁP từ sổ công: mỗi người một phiếu, tính bằng hàm thuần. Không tiền nào đi đâu — gửi duyệt / chốt là lệnh khác của người."""
    _need_payroll(ctx)
    entity = await entities.resolve(ctx, inp.entity_code)
    period = month_of(inp.period)
    if inp.pay_date < period:
        raise Invalid("Ngày chi trước tháng công")
    if period > month_of(today()):
        raise Invalid("Chưa lập kỳ cho tháng chưa tới")
    await _lock(ctx, entity)
    if inp.kind == "regular" and await ctx.val("SELECT 1 FROM pay.runs WHERE entity_id=%s AND period=%s AND kind='regular' AND status='locked'",
                                               (entity, period)):
        raise Invalid(f"Tháng {period:%m/%Y} của pháp nhân này đã chốt kỳ lương — khoản phát sinh thêm vào kỳ tháng sau (kỳ ngoài kỳ: lát sau)")
    if inp.kind == "regular" and (open_ := await ctx.one("SELECT period,status FROM pay.runs WHERE entity_id=%s AND kind='regular' AND status<>'locked'",
                                                         (entity,))):
        raise Invalid(f"Đang có kỳ {open_['period']:%m/%Y} {RUN_STATUS[open_['status']].lower()} — tính lại / chốt / xoá kỳ đó trước")
    seq = int(await ctx.val("SELECT coalesce(max(seq),0)+1 FROM pay.runs WHERE entity_id=%s AND period=%s AND kind=%s", (entity, period, inp.kind)))
    rid = await ctx.val("INSERT INTO pay.runs(entity_id,period,pay_date,kind,seq,created_by) VALUES(%s,%s,%s,%s,%s,core.me()) RETURNING id",
                        (entity, period, inp.pay_date, inp.kind, seq))
    out = await build(ctx, await _run(ctx, rid))
    return {"run_id": rid, **out, "summary": f"Kỳ {period:%m/%Y} ({RUN_KIND[inp.kind].lower()}): {out['slips']} phiếu, {out['held']} phiếu đang dừng"}


class RunRef(Input):
    run_id: UUID = field("Kỳ lương", hidden=True)


@m.command("compute_run",
           purpose="Tính lại kỳ lương nháp để số trong phiếu khớp dữ liệu công và mức trả mới nhất.", reversible="Tính lại lần nữa; kỳ vẫn là nháp",
           resource="pay", title="Tính lại kỳ nháp", agent="propose", risk="low")
async def compute_run(ctx: Ctx, inp: RunRef) -> dict[str, Any]:
    _need_payroll(ctx)
    run = await _run(ctx, inp.run_id, lock=True)
    if run["status"] != "draft":
        raise Invalid("Chỉ tính lại kỳ nháp — kỳ đã gửi duyệt thì rút về nháp trước")
    out = await build(ctx, run)
    summary = f"Đã tính lại: {out['slips']} phiếu, {out['held']} phiếu đang dừng"
    if out["law_changes"]:   # luật đổi phiên bản (hồi tố / mới có hiệu lực) giữa hai lần tính: nói rõ, không để số đổi im lặng (0154)
        summary += (f". CHÚ Ý: {len(out['law_changes'])} quy tắc luật đổi phiên bản ({', '.join(c['rule'] for c in out['law_changes'])}) — "
                    f"tổng thực nhận {money(out['net_before_vnd'])} → {money(out['net_after_vnd'])}")
    return {"run_id": run["id"], **out, "summary": summary}


@m.command("discard_run",
           purpose="Bỏ kỳ lương nháp lập sai để dòng công quay về chưa vào kỳ.", irreversible=True, effect="Xoá kỳ nháp cùng các phiếu đã tính; dòng công trở lại chưa vào kỳ và phải lập kỳ mới.",
           resource="pay", action="manage", title="Xoá kỳ nháp", agent="deny")
async def discard_run(ctx: Ctx, inp: RunRef) -> dict[str, Any]:
    _need_payroll(ctx)
    run = await _run(ctx, inp.run_id, lock=True)
    if run["status"] != "draft":
        raise Invalid("Chỉ xoá kỳ nháp")
    await _clear(ctx, run["id"])
    await ctx.exec("DELETE FROM pay.runs WHERE id=%s", (run["id"],))
    return {"run_id": run["id"], "summary": f"Đã xoá kỳ nháp {run['period']:%m/%Y} — dòng công trở lại chưa vào kỳ"}


async def _law_refs(ctx: Ctx, run_id: UUID) -> list[str]:
    """Mọi quy tắc luật các dòng phiếu của kỳ dựa vào (một dòng có thể nối nhiều mã bằng " + ") — trừ khoản của công ty."""
    rows = await ctx.all("SELECT DISTINCT l.rule_ref FROM pay.slip_lines l JOIN pay.slips s ON s.id=l.slip_id WHERE s.run_id=%s", (run_id,))
    return sorted({ref for r in rows for ref in r["rule_ref"].split(" + ") if not ref.startswith("company:")})


@m.command("submit_run",
           purpose="Gửi kỳ lương lên người duyệt để chốt số liệu trước khi chi.", irreversible=True, effect="Đầu vào kỳ lương bị đóng băng và phiếu duyệt gửi tới người duyệt; phiếu đang dừng rời khỏi kỳ, dòng công của họ chờ kỳ sau.",
           resource="pay", action="manage", title="Gửi duyệt kỳ lương", agent="deny", risk="money")
async def submit_run(ctx: Ctx, inp: RunRef) -> dict[str, Any]:
    """Đóng băng đầu vào, mở phiếu duyệt ``pay_run``. Phiếu đang DỪNG (thiếu mức, quy tắc chưa xác minh…) không đi theo: rời kỳ, dòng công của họ
    chờ kỳ sau. Ngày hạch toán phải còn trong kỳ kế toán mở (0119)."""
    _need_payroll(ctx)
    run = await _run(ctx, inp.run_id, lock=True)
    if run["kind"] != "regular":
        raise Invalid(LOCKABLE)
    if run["status"] != "draft":
        raise Invalid("Kỳ không còn nháp")
    slips = await ctx.all("SELECT id,name,held_reason,net_vnd FROM pay.slips WHERE run_id=%s ORDER BY name", (run["id"],))
    ready = [s for s in slips if not s["held_reason"]]
    held = [s for s in slips if s["held_reason"]]
    if not ready:
        raise Invalid("Không có phiếu nào tính được" + (f": {held[0]['name']} — {held[0]['held_reason']}" if held else ""))
    for s in held:
        await ctx.exec("DELETE FROM pay.slip_lines WHERE slip_id=%s", (s["id"],))
        await ctx.exec("DELETE FROM pay.slips WHERE id=%s", (s["id"],))
    if bad := regulations.require_verified(await _law_refs(ctx, run["id"])):
        raise Invalid(f"Quy tắc chưa xác minh: {', '.join(bad)} — chờ kế toán xác minh")
    day = posting_day(run["period"])
    chk = await ctx.call_system("acct.period_open", entity_id=run["entity_id"], on=day)
    if not chk["open"]:
        raise Invalid(chk["message"])
    n = await ctx.val("UPDATE pay.runs SET status='submitted',submission=submission+1,posted_on=%s WHERE id=%s RETURNING submission",
                      (day, run["id"]))
    # tóm tắt không có tên / số tiền: người đọc phiếu duyệt không có quyền Lương không thấy lương (người duyệt xem kỳ ở Lương › Kỳ lương)
    aid = await ctx.request_approval(
        chain="pay_run", command="pay.lock_run", input={"run_id": str(run["id"]), "submission": n},
        summary=f"Chốt kỳ lương {run['period']:%m/%Y}: {len(ready)} phiếu, hạch toán {day:%d/%m/%Y} — xem ở Lương › Kỳ lương",
        on_reject="pay.reopen_run",
    )
    await ctx.exec("UPDATE pay.runs SET approval_id=%s WHERE id=%s", (aid, run["id"]))
    left = f" — {len(held)} phiếu đang dừng để kỳ sau: {', '.join(s['name'] for s in held)}" if held else ""
    return {"run_id": run["id"], "approval_id": aid, "slips": len(ready), "held": len(held), "summary": f"Đã gửi duyệt {len(ready)} phiếu{left}"}


class LockIn(Input):
    run_id: UUID
    submission: int


@m.command("lock_run", resource="pay", title="Chốt kỳ lương sau duyệt", internal=True, agent="deny", risk="money")
async def lock_run(ctx: Ctx, inp: LockIn) -> dict[str, Any]:
    """Sau duyệt: mỗi phiếu thành MỘT nghĩa vụ ``payable:<người>`` (chi phí theo khoản, thuế khấu trừ, cấn ứng đúng số trên phiếu) cùng ngày hạch toán;
    kỳ khoá ⇒ DB từ chối. Trigger của kỳ từ chối nếu người duyệt có phiếu trong kỳ."""
    if not ctx.p.approved_by:
        raise Denied("Chốt kỳ lương cần phiếu đã được người khác duyệt")
    run = await _run(ctx, inp.run_id, lock=True)
    if run["status"] != "submitted" or run["submission"] != inp.submission:
        raise Invalid("Kỳ lương đã đổi sau lần gửi duyệt này — gửi duyệt lại")
    if bad := regulations.require_verified(await _law_refs(ctx, run["id"])):
        raise Invalid(f"Quy tắc chưa xác minh: {', '.join(bad)}")
    # tách bạch nhiệm vụ: nói rõ ở đây; trigger của kỳ là hàng rào cuối (đường ghi nào cũng vậy)
    if await ctx.val("SELECT 1 FROM pay.slips WHERE run_id=%s AND user_id=ANY(%s) LIMIT 1", (run["id"], list(ctx.p.approved_by))):
        raise Denied("Người duyệt có phiếu trong kỳ lương này — cần người khác duyệt (0150 §8); rút kỳ về nháp và gửi lại")
    day: date = run["posted_on"]   # ngày đã chốt lúc gửi duyệt — người duyệt thấy đúng ngày này
    slips = await ctx.all("SELECT * FROM pay.slips WHERE run_id=%s ORDER BY name,id", (run["id"],))
    for s in slips:
        lines = await ctx.all("SELECT * FROM pay.slip_lines WHERE slip_id=%s ORDER BY seq", (s["id"],))
        spent: dict[str, int] = {}
        for x in lines:
            if x["kind"] in ("earning", "deduction"):
                spent[x["account"]] = spent.get(x["account"], 0) + (x["amount_vnd"] if x["kind"] == "earning" else -x["amount_vnd"])
        expenses = sorted(((a, v) for a, v in spent.items() if v), key=lambda t: (-t[1], t[0]))
        offset = sum(x["amount_vnd"] for x in lines if x["kind"] == "offset")
        sources = sorted({c["source"] for c in s["inputs"]["components"].values() if c["calculator"] == "advance_offset"}) if offset else []
        got = await ctx.call_system(
            "acct.recognize_payable", source="pay.slip", source_ref=str(s["id"]), party_id=s["party_id"], entity_id=s["entity_id"],
            amount_vnd=sum(v for _, v in expenses), expense_account=expenses[0][0],
            expenses=[{"account": a, "amount_vnd": v} for a, v in expenses] if len(expenses) > 1 else [],
            deductions=[{"account": x["account"], "amount_vnd": x["amount_vnd"], "basis": x["rule_ref"]} for x in lines
                        if x["kind"] == "withholding" and x["amount_vnd"] > 0],
            label=f"Lương & thù lao {run['period']:%m/%Y}", basis=f"pay.run:{run['id']}", on=day.isoformat(),
            allow_offset_from=sources, offset_vnd=offset, liability_account=PAYABLE, confidential="pay.slips",
            due_on=s["pay_date"].isoformat(),   # 0211: Dòng tiền dự phóng xếp phần thực nhận vào đúng tháng chi lương
        )
        await ctx.exec("UPDATE pay.slips SET payable_id=%s,owner_id=user_id WHERE id=%s", (got["payable_id"], s["id"]))
    await ctx.exec("UPDATE pay.runs SET status='locked',approved_by=%s,locked_at=now() WHERE id=%s", (list(ctx.p.approved_by), run["id"]))
    for s in slips:
        await ctx.notify([s["user_id"]], kind="pay", title=f"Phiếu lương kỳ {run['period']:%m/%Y} đã chốt", body=f"Thực nhận {money(s['net_vnd'])}",
                         push="Phiếu lương của anh/chị đã chốt", open={"query": "pay.slip", "input": {"slip_id": str(s["id"])}})
    return {"run_id": run["id"], "slips": len(slips), "posted_on": day.isoformat()}


@m.command("reopen_run", resource="pay", title="Kỳ lương bị từ chối", internal=True, agent="deny")
async def reopen_run(ctx: Ctx, inp: LockIn) -> None:
    await ctx.exec("UPDATE pay.runs SET status='draft',approval_id=NULL,posted_on=NULL WHERE id=%s AND status='submitted' AND submission=%s",
                   (inp.run_id, inp.submission))


@m.command("return_to_draft",
           purpose="Rút kỳ lương đang chờ duyệt về nháp để sửa số liệu.", reversible="Tính lại rồi Gửi duyệt kỳ lương (phiếu duyệt mới)",
           resource="pay", action="manage", title="Rút kỳ về nháp", agent="deny")
async def return_to_draft(ctx: Ctx, inp: RunRef) -> dict[str, Any]:
    """Kỳ đang chờ duyệt về nháp để sửa (phiếu duyệt cũ không chốt được nữa — lần gửi mới là phiếu mới)."""
    _need_payroll(ctx)
    run = await _run(ctx, inp.run_id, lock=True)
    if run["status"] != "submitted":
        raise Invalid("Kỳ không ở trạng thái chờ duyệt")
    await ctx.exec("UPDATE pay.runs SET status='draft',approval_id=NULL,posted_on=NULL WHERE id=%s", (run["id"],))
    return {"run_id": run["id"], "summary": f"Kỳ {run['period']:%m/%Y} đã về nháp — tính lại rồi gửi duyệt lại"}


class PayRun(RunRef):
    recipient_instruction: str = field("Tài khoản nhận", kind="textarea", max_length=300, example="Theo bảng kê tài khoản kỳ lương 10/2026, đã kiểm",
                                       help="Lát này chưa lưu tài khoản ngân hàng từng người — ghi căn cứ chuyển khoản của cả lô")
    reason: str = field("Lý do chi", max_length=300, example="Chi kỳ lương 10/2026")


@m.command("request_payment",
           purpose="Đề nghị chi cho cả kỳ lương đã chốt để mỗi phiếu có lệnh chi và người lao động nhận đúng hạn.", irreversible=True, effect="Gửi phiếu duyệt chi cho cả kỳ lương đã chốt; sau khi duyệt, mỗi phiếu thành một lệnh chi và tiền ra khỏi tài khoản công ty.",
           resource="pay", action="read", title="Đề nghị chi kỳ lương", agent="deny", risk="money")
async def request_payment(ctx: Ctx, inp: PayRun) -> dict[str, Any]:
    """MỘT đề nghị chi cho cả kỳ đã chốt (mỗi phiếu một lệnh chi) qua chuỗi chi nghĩa vụ của Tài chính — quyền thật là quyền lập đề nghị chi của kế
    toán (``acct.request_payment_batch`` chạy bằng quyền người bấm)."""
    if not ctx.p.has_scope("pay", "all"):
        raise Denied("Cần quyền xem kỳ lương cả công ty")
    run = await _run(ctx, inp.run_id)
    if run["status"] != "locked":
        raise Invalid("Chỉ đề nghị chi kỳ đã chốt")
    ids = [r["payable_id"] for r in await ctx.all("SELECT payable_id FROM pay.slips WHERE run_id=%s AND payable_id IS NOT NULL", (run["id"],))]
    states = await ctx.call_system("acct.payable_states", payable_ids=ids) if ids else {}
    todo = [i for i in ids if states.get(str(i), {}).get("left_vnd", 0) > 0]
    if not todo:
        raise Invalid("Không còn phiếu nào chưa chi")
    return await ctx.call("acct.request_payment_batch", payable_ids=todo, recipient_instruction=inp.recipient_instruction, reason=inp.reason)


# ── Xem ──

def _paid_state(st: dict[str, int] | None, net: int) -> str:
    if st is None:
        return "none"
    if net <= 0 or (st["left_vnd"] <= 0 and st["pending_vnd"] <= 0):
        return "paid"
    return "pending" if st["pending_vnd"] else "unpaid"


PAID = {"none": "—", "unpaid": "Chưa chi", "pending": "Đang chuyển", "paid": "Đã chi"}
PAID_TONE = {"unpaid": "orange", "pending": "blue", "paid": "green"}


@m.query("runs", resource="pay", min_scope="all", title="Kỳ lương")
async def runs(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all(
        """SELECT r.id,r.period,r.pay_date,r.kind,r.status,r.posted_on,r.seq,
                  count(s.id) AS slips,count(s.id) FILTER (WHERE s.held_reason<>'') AS held,coalesce(sum(s.net_vnd),0)::bigint AS net_vnd
           FROM pay.runs r LEFT JOIN pay.slips s ON s.run_id=r.id GROUP BY r.id ORDER BY r.period DESC,r.created_at DESC LIMIT 200""")
    for r in rows:
        r["state"] = "shadow" if r["kind"] == "shadow" else r["status"]
        r["label"] = f"{r['period']:%m/%Y}" + (f" (lần {r['seq']})" if r["seq"] > 1 else "")
    return ui.table(
        rows,
        [ui.col("label", "Tháng công"), ui.col("pay_date", "Ngày chi", "date"),
         ui.col("state", "Trạng thái", "badge", labels={**RUN_STATUS, "shadow": "Chạy bóng"},
                tones={"draft": "gray", "submitted": "blue", "locked": "green", "shadow": "purple"}),
         ui.col("slips", "Phiếu", "number"), ui.col("held", "Đang dừng", "number"), ui.col("net_vnd", "Thực nhận", "money"),
         ui.col("posted_on", "Hạch toán", "date")],
        title="Kỳ lương", limit=200, empty="Chưa có kỳ lương — soạn kỳ từ sổ công",
    )


@m.query("run", resource="pay", min_scope="all", title="Kỳ lương")
async def run(ctx: Ctx, inp: RunRef) -> dict[str, Any]:
    r = await _run(ctx, inp.run_id)
    slips = await ctx.all("""SELECT id,name,gross_vnd,deductions_vnd,net_vnd,held_reason,unverified,payable_id,figures FROM pay.slips
                             WHERE run_id=%s ORDER BY name""", (r["id"],))
    ids = [s["payable_id"] for s in slips if s["payable_id"]]
    states = await ctx.call_system("acct.payable_states", payable_ids=ids) if ids else {}
    for s in slips:
        s["state"] = "held" if s["held_reason"] else ("shadow" if r["kind"] == "shadow" else "ok")
        s["paid"] = _paid_state(states.get(str(s["payable_id"])), s["net_vnd"]) if s["payable_id"] else "none"
        s["note"] = s["held_reason"] or ("Số chưa xác minh — chỉ để so" if s["unverified"] else "")
    blocks: list[dict[str, Any]] = []
    if r["status"] == "draft" and slips:   # luật đang hiệu lực đã khác luật phiếu đã tính (phát hành mới / hồi tố) ⇒ "Tính lại" sẽ đổi số
        stale = law_changes(await _law_refs(ctx, r["id"]), [x["ref"] for x in law_on(r["pay_date"], regulations.period_end(r["period"])).values()])
        if stale:
            blocks.append(ui.b_callout("warn", "Luật đã đổi phiên bản từ lúc tính", "Bấm “Tính lại” sẽ đổi số các phiếu theo phiên bản mới — xem trước:",
                                       facts=[f"{c['rule']}: {', '.join(c['from'])} → {', '.join(c['to'])}" for c in stale]))
    if r["kind"] == "shadow":
        blocks.append(ui.b_callout("accent", "Kỳ chạy bóng", LOCKABLE + ". Mọi số dựa vào quy tắc chưa xác minh mang dấu “chưa xác minh”."))
    held = [s for s in slips if s["held_reason"]]
    if held and r["kind"] != "shadow":
        blocks.append(ui.b_callout("warn", f"{len(held)} phiếu đang dừng", "Gửi duyệt thì các phiếu này rời kỳ, dòng công chờ kỳ sau.",
                                   facts=[f"{s['name']}: {s['held_reason']}" for s in held[:10]]))
    ok = [s for s in slips if not s["held_reason"]]
    blocks.append(ui.b_kpis([ui.kpi("Phiếu tính được", len(ok)), ui.kpi("Thu nhập", sum(s["gross_vnd"] for s in ok), "money"),
                             ui.kpi("Khấu trừ", sum(s["deductions_vnd"] for s in ok), "money"),
                             ui.kpi("Thực nhận", sum(s["net_vnd"] for s in ok), "money", tone="green"),
                             ui.kpi("Doanh nghiệp đóng", sum(int((s["figures"] or {}).get("employer_vnd") or 0) for s in ok), "money",
                                    hint="Bảo hiểm + kinh phí công đoàn — chưa ghi sổ (cần lệnh của Tài chính)")]))
    page = ui.table(slips, [ui.col("name", "Người"), ui.col("gross_vnd", "Thu nhập", "money"), ui.col("deductions_vnd", "Khấu trừ", "money"),
                            ui.col("net_vnd", "Thực nhận", "money"),
                            ui.col("state", "Phiếu", "badge", labels={"ok": "Tính được", "held": "Đang dừng", "shadow": "Chạy bóng"},
                                   tones={"ok": "green", "held": "orange", "shadow": "purple"}),
                            ui.col("paid", "Chi", "badge", labels=PAID, tones=PAID_TONE), ui.col("note", "Ghi chú", width=280)],
                    title="Phiếu lương", empty="Kỳ chưa có phiếu — sổ công chưa có dòng nào tới cuối tháng công")
    page["open"] = {"query": "pay.slip", "bind": {"slip_id": "id"}, "title": "Phiếu lương"}
    blocks.append(ui.b_table(page))
    acts = []
    if r["status"] == "draft":
        acts += [ui.act("pay.compute_run", "Tính lại", {"run_id": str(r["id"])})]
        if r["kind"] == "regular":
            acts += [ui.act("pay.submit_run", "Gửi duyệt", {"run_id": str(r["id"])}, "primary")]
        acts += [ui.act("pay.discard_run", "Xoá kỳ nháp", {"run_id": str(r["id"])}, "destructive")]
    elif r["status"] == "submitted":
        acts += [ui.act("pay.return_to_draft", "Rút về nháp", {"run_id": str(r["id"])})]
    elif any(s["paid"] == "unpaid" for s in slips) and can_run(ctx.p, "acct.request_payment_batch"):
        acts += [ui.act("pay.request_payment", "Đề nghị chi", {"run_id": str(r["id"])}, "primary")]
    sub = f"{RUN_KIND[r['kind']]} · {RUN_STATUS[r['status']]} · chi ngày {r['pay_date']:%d/%m/%Y}" + (
        f" · hạch toán {r['posted_on']:%d/%m/%Y}" if r["posted_on"] else "")
    return ui.card(f"Kỳ lương {r['period']:%m/%Y}", blocks, subtitle=sub, icon="banknote", actions=acts)


class SlipRef(Input):
    slip_id: UUID = field("Phiếu", hidden=True)


@m.query("slip", resource="pay.slips", title="Phiếu lương")
async def slip(ctx: Ctx, inp: SlipRef) -> dict[str, Any]:
    """Một phiếu, đọc từ dòng đã lưu + bản chụp đầu vào: mỗi dòng nói căn cứ (công × mức, quy tắc luật nào) — trợ lý giải thích từ thẻ này, không tự
    tính. RLS: người lao động chỉ thấy phiếu đã chốt của mình."""
    s = await ctx.one("SELECT * FROM pay.slips WHERE id=%s", (inp.slip_id,))
    if not s:
        raise NotFound("Không thấy phiếu lương")
    lines = await ctx.all("SELECT * FROM pay.slip_lines WHERE slip_id=%s ORDER BY seq", (s["id"],))
    tone = {"earning": "ok", "deduction": "warn", "withholding": "warn", "offset": "muted", "employer": "muted"}

    def item(x: dict[str, Any]) -> dict[str, Any]:
        return {"title": x["label"], "subtitle": f"{x['basis']} · {x['rule_ref']}" + (" · CHƯA XÁC MINH" if x["unverified"] else ""),
                "badge": ("" if x["kind"] in ("earning", "employer") else "− ") + money(x["amount_vnd"]), "tone": tone[x["kind"]]}

    items = [item(x) for x in lines if x["kind"] != "employer"]
    employer = [x for x in lines if x["kind"] == "employer"]
    blocks: list[dict[str, Any]] = []
    if s["run_kind"] == "shadow":
        blocks.append(ui.b_callout("accent", "Phiếu chạy bóng — chỉ để so số", "Không chốt, không chi, người lao động không thấy."))
    if s["held_reason"]:
        blocks.append(ui.b_callout("warn", "Phiếu đang dừng", s["held_reason"]))
    if s["unverified"]:
        blocks.append(ui.b_callout("warn", "Chờ kế toán xác minh", "Số dựa vào quy tắc luật chưa xác minh: " + ", ".join(s["unverified"])))
    paid = ""
    if s["payable_id"]:
        st = (await ctx.call_system("acct.payable_states", payable_ids=[s["payable_id"]])).get(str(s["payable_id"]))
        paid = PAID[_paid_state(st, s["net_vnd"])]
    blocks.append(ui.b_kpis([ui.kpi("Thu nhập", s["gross_vnd"], "money"), ui.kpi("Khấu trừ", s["deductions_vnd"], "money"),
                             ui.kpi("Thực nhận", s["net_vnd"], "money", tone="green", hint=paid)]))
    blocks.append(ui.b_list(items, "Các khoản"))
    if employer:   # chi phí của công ty, không trừ vào lương — bảng kê vẫn cho người lao động thấy công ty đóng gì cho họ
        blocks.append(ui.b_list([item(x) for x in employer], f"Doanh nghiệp đóng thêm (không trừ vào lương): {money(sum(x['amount_vnd'] for x in employer))}"))
    terms = s["inputs"].get("terms") or {}
    blocks.append(ui.b_text(f"Ngày chi {s['pay_date']:%d/%m/%Y} · loại hợp đồng tại ngày chi: {terms.get('contract_kind') or 'chưa ghi'}"
                            + (f" ({terms['term_months']} tháng)" if terms.get("term_months") else "")
                            + (f" · lương đóng bảo hiểm {money(terms['insured_vnd'])}, vùng {terms.get('work_region') or '—'}" if terms.get("insured_vnd") else "")
                            + (f" · {terms['dependants']} người phụ thuộc" if terms.get("dependants") else "")
                            + f" · {len(s['inputs'].get('entries') or [])} dòng công · mã bản chụp {s['inputs_hash'][:12]}", "Căn cứ"))
    return ui.card(f"Phiếu lương {s['period']:%m/%Y} — {s['name']}", blocks, subtitle=f"Ngày chi {s['pay_date']:%d/%m/%Y}" + (f" · {paid}" if paid else ""),
                   icon="doc.text")


@m.query("my_slips", resource="pay.slips", title="Phiếu lương của tôi")
async def my_slips(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Phiếu ĐÃ CHỐT của chính người xem (màn điện thoại): kỳ, ngày chi, thực nhận, đã chi chưa."""
    rows = await ctx.all("SELECT id,period,pay_date,net_vnd,gross_vnd,payable_id FROM pay.slips WHERE owner_id=core.me() ORDER BY pay_date DESC LIMIT 60")
    ids = [r["payable_id"] for r in rows if r["payable_id"]]
    states = await ctx.call_system("acct.payable_states", payable_ids=ids) if ids else {}
    for r in rows:
        r["label"] = f"Kỳ {r['period']:%m/%Y}"
        r["paid"] = _paid_state(states.get(str(r["payable_id"])), r["net_vnd"])
    return ui.table(rows, [ui.col("label", "Kỳ"), ui.col("net_vnd", "Thực nhận", "money"), ui.col("paid", "Chi", "badge", labels=PAID, tones=PAID_TONE),
                           ui.col("pay_date", "Ngày chi", "date")],
                    title="Phiếu lương của tôi", limit=60, empty="Chưa có phiếu lương đã chốt")


class CompareIn(Input):
    slip_id: UUID | None = field("Phiếu", None, hidden=True, help="Trống = phiếu đã chốt mới nhất của chính người xem")


def _facts(inputs: dict[str, Any]) -> dict[str, float]:
    """Dữ kiện đọc từ BẢN CHỤP đầu vào của phiếu (không tính lại): ngày công có lương / không lương, giờ làm thêm, người phụ thuộc."""
    out = {"paid": 0.0, "unpaid": 0.0, "std": 0.0, "ot": 0.0, "dependants": float((inputs.get("terms") or {}).get("dependants") or 0)}
    for e in inputs.get("entries") or []:
        if e["kind"] == "qty" and e["role_key"] in engine.ATTENDANCE:
            out[engine.ATTENDANCE[e["role_key"]]] += float(e["quantity"])
        elif e["kind"] == "qty" and e["role_key"] in engine.OT_ROLES:
            out["ot"] += float(e["quantity"])
    return out


@m.query("compare_slips", resource="pay.slips", title="Vì sao phiếu lương kỳ này khác kỳ trước")
async def compare_slips(ctx: Ctx, inp: CompareIn) -> dict[str, Any]:
    """So một phiếu ĐÃ CHỐT với phiếu đã chốt liền trước của cùng người, từng khoản + dữ kiện bản chụp (ngày công, làm thêm, người phụ thuộc) — trợ lý
    trả lời "vì sao lương tháng này thấp hơn" TỪ THẺ NÀY, không tự tính. RLS: người lao động chỉ thấy phiếu của mình."""
    if inp.slip_id:
        s = await ctx.one("SELECT * FROM pay.slips WHERE id=%s AND payable_id IS NOT NULL", (inp.slip_id,))
    else:
        s = await ctx.one("SELECT * FROM pay.slips WHERE owner_id=core.me() ORDER BY pay_date DESC,period DESC LIMIT 1")
    if not s:
        raise NotFound("Chưa có phiếu lương đã chốt để so")
    prev = await ctx.one("""SELECT * FROM pay.slips WHERE party_id=%s AND payable_id IS NOT NULL AND id<>%s AND (period,pay_date) < (%s,%s)
                            ORDER BY period DESC,pay_date DESC LIMIT 1""", (s["party_id"], s["id"], s["period"], s["pay_date"]))

    async def by_component(slip_id: UUID) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for x in await ctx.all("SELECT component,label,kind,amount_vnd,basis FROM pay.slip_lines WHERE slip_id=%s AND kind<>'employer' ORDER BY seq", (slip_id,)):
            row = out.setdefault(x["component"], {"label": x["label"], "vnd": 0, "basis": x["basis"]})
            row["vnd"] += x["amount_vnd"] if x["kind"] == "earning" else -x["amount_vnd"]
        return out

    now = await by_component(s["id"])
    before = await by_component(prev["id"]) if prev else {}
    rows = []
    for code in dict.fromkeys([*now, *before]):
        a, b = before.get(code, {}).get("vnd", 0), now.get(code, {}).get("vnd", 0)
        if a != b:
            rows.append({"id": code, "label": (now.get(code) or before[code])["label"], "before": a, "now": b, "change": b - a,
                         "basis": now.get(code, {}).get("basis", "Không có ở kỳ này")})
    rows.sort(key=lambda r: r["change"])
    f_now, f_before = _facts(s["inputs"]), _facts(prev["inputs"]) if prev else None
    facts = [f"Ngày công chuẩn của tháng: {f_now['std']:g}" + (f" (kỳ trước {f_before['std']:g})" if f_before else ""),
             f"Ngày công có lương: {f_now['paid']:g}" + (f" (kỳ trước {f_before['paid']:g})" if f_before else ""),
             f"Ngày không lương: {f_now['unpaid']:g}" + (f" (kỳ trước {f_before['unpaid']:g})" if f_before else ""),
             f"Giờ làm thêm: {f_now['ot']:g}" + (f" (kỳ trước {f_before['ot']:g})" if f_before else ""),
             f"Người phụ thuộc: {f_now['dependants']:g}" + (f" (kỳ trước {f_before['dependants']:g})" if f_before else "")]
    blocks = [ui.b_kpis([ui.kpi("Thực nhận kỳ này", s["net_vnd"], "money"),
                         ui.kpi("Thực nhận kỳ trước", prev["net_vnd"] if prev else 0, "money", hint="" if prev else "Chưa có kỳ trước"),
                         ui.kpi("Chênh lệch", s["net_vnd"] - (prev["net_vnd"] if prev else 0), "money")]),
              ui.b_callout("accent", "Dữ kiện của hai kỳ (từ bản chụp đầu vào)", facts=facts),
              ui.b_table(ui.table(rows, [ui.col("label", "Khoản"), ui.col("before", "Kỳ trước", "money"), ui.col("now", "Kỳ này", "money"),
                                         ui.col("change", "Chênh", "money"), ui.col("basis", "Căn cứ kỳ này", width=320)],
                                  title="Khoản khác nhau (khấu trừ mang dấu âm)", empty="Hai kỳ giống nhau từng khoản"))]
    sub = f"Kỳ {s['period']:%m/%Y}" + (f" so với kỳ {prev['period']:%m/%Y}" if prev else " — chưa có kỳ trước để so")
    return ui.card(f"Phiếu lương {s['period']:%m/%Y}: vì sao khác kỳ trước", blocks, subtitle=sub, icon="doc.text.magnifyingglass")


@m.query("rules", resource="pay.policy", title="Quy tắc luật áp dụng")
async def rules(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Các phiên bản của bộ quy định lương và trạng thái xác minh TỪNG quy tắc — chưa xác minh thì không tính ở kỳ thật."""
    rows = []
    for v in regulations.packs().get(LAW, ()):
        for code, p in v.params.items():
            val = p["value"] if isinstance(p["value"], dict) else {}
            rows.append({"id": f"{v.ref}:{code}", "ref": f"{v.ref}:{code}", "label": p["label"], "from": v.effective_from,
                         "value": (f"{val.get('rate_bps', 0) / 100:g}% từ {money(int(val.get('min_payment_vnd', 0)))} / lần chi" if val else str(p["value"])),
                         "status": p["status"], "by": p["verified_by"], "basis": p["basis"]})
    pending = sum(r["status"] != "verified" for r in rows)
    return ui.table(
        rows,
        [ui.col("from", "Hiệu lực từ", "date"), ui.col("label", "Quy tắc", width=280), ui.col("value", "Mức"),
         ui.col("status", "Trạng thái", "badge", labels={"assumption": "Chờ kế toán xác minh", "verified": "Đã xác minh"},
                tones={"assumption": "orange", "verified": "green"}),
         ui.col("by", "Người xác minh"), ui.col("basis", "Căn cứ", width=320)],
        title="Quy tắc luật áp dụng", empty="Chưa có bộ quy định lương",
        kpis=[ui.kpi("Chờ xác minh", pending, tone="orange" if pending else "green")],
        note="Quy tắc chưa xác minh chỉ tính ở kỳ chạy bóng. Xác minh = kế toán đối chiếu văn bản gốc rồi ghi tên, ngày vào bộ quy định (bản phát hành mới)",
    )


@m.query("anomalies", resource="pay", min_scope="all", title="Bất thường lương")
async def anomalies(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Việc cần người xử lý trước khi lập / chốt kỳ: dòng công chưa có mức, người chưa ghi loại hợp đồng, phiếu đang dừng, quy tắc chưa xác minh.
    Trợ lý BÁO từ bảng này — không tự sửa."""
    out: list[dict[str, Any]] = []
    rows = await ctx.all("""SELECT e.id,e.party_id,e.work_date,e.kind,e.unit,e.role_key FROM pay.work_entries e
                            WHERE NOT EXISTS (SELECT 1 FROM pay.slip_entries se WHERE se.entry_id=e.id) ORDER BY e.work_date LIMIT 5000""")
    rates = [{**r, "party_id": str(r["party_id"]) if r["party_id"] else None, "effective_from": r["effective_from"].isoformat(), "id": str(r["id"])}
             for r in await ctx.all("SELECT id,component,unit,role_key,party_id,amount_vnd,effective_from FROM pay.rates ORDER BY created_at,id")]
    ids = list({r["party_id"] for r in rows})
    names = await ctx.call_system("parties.names", party_ids=ids) if ids else {}
    no_rate: dict[str, int] = {}
    for e in rows:
        if e["kind"] == "qty" and engine.pick_rate(rates, party=str(e["party_id"]), unit=e["unit"], role_key=e["role_key"],
                                                   day=e["work_date"].isoformat()) is None:
            no_rate[str(e["party_id"])] = no_rate.get(str(e["party_id"]), 0) + 1
    for pid, n in no_rate.items():
        out.append({"id": f"rate:{pid}", "what": "no_rate", "who": names.get(pid, {}).get("name", ""), "detail": f"{n} dòng công chưa có mức trả"})
    terms = await ctx.call_system("hr.terms_on", party_ids=ids, on=today()) if ids else {}
    for pid in ids:
        if str(pid) not in terms:
            out.append({"id": f"terms:{pid}", "what": "no_terms", "who": names.get(str(pid), {}).get("name", ""), "detail": "Chưa ghi loại hợp đồng"})
    for s in await ctx.all("""SELECT s.id,s.name,s.held_reason FROM pay.slips s JOIN pay.runs r ON r.id=s.run_id
                              WHERE r.kind='regular' AND r.status='draft' AND s.held_reason<>'' ORDER BY s.name"""):
        out.append({"id": f"held:{s['id']}", "what": "held", "who": s["name"], "detail": s["held_reason"]})
    for _code, rule in sorted(law_on(today()).items()):
        if rule["status"] != "verified":
            out.append({"id": f"rule:{rule['ref']}", "what": "unverified", "who": "", "detail": f"{rule['label']} ({rule['ref']}) — chờ kế toán xác minh"})
    return ui.table(out, [ui.col("what", "Loại", "badge", labels={"no_rate": "Thiếu mức", "no_terms": "Thiếu loại hợp đồng", "held": "Phiếu dừng",
                                                                     "unverified": "Quy tắc chưa xác minh"},
                                 tones={"no_rate": "orange", "no_terms": "orange", "held": "red", "unverified": "purple"}),
                          ui.col("who", "Người"), ui.col("detail", "Chi tiết", width=360)],
                    title="Bất thường lương", empty="Không có gì bất thường", kpis=[ui.kpi("Cần xử lý", len(out), tone="orange" if out else "green")])



@m.pack_blockers()
async def pack_blockers(ctx: Ctx, inp: NoInput) -> list[str]:
    """Không tắt Lương khi còn: kỳ chưa chốt; dòng công do GÓI KHÁC ghi (buổi đã xác nhận, hoa hồng đã mở) — giảm / huỷ những khoản đó về sau phải đi
    ngược đúng đường đã đi (dòng âm trong sổ công này), tắt gói thì đường đó đứt (review 09/11)."""
    out = []
    if n := int(await ctx.val("SELECT count(*) FROM pay.runs WHERE status<>'locked' AND kind<>'shadow'") or 0):
        out.append(f"{n} kỳ lương chưa chốt")
    if n := int(await ctx.val("SELECT count(*) FROM pay.work_entries WHERE split_part(source,'.',1)<>'pay'") or 0):
        out.append(f"{n} dòng công từ gói khác (buổi đã xác nhận, quyền hưởng đã mở) — điều chỉnh về sau đi qua sổ công này")
    return out
