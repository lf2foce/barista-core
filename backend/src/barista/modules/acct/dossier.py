"""HỒ SƠ ĐIỀU TRA KHOẢN LỆCH (ADR 0223) — tiền về (dòng sao kê) không khớp công nợ ⇒ code dựng hồ sơ, đề xuất việc, giao một người, theo tới khi xong.

* PHÁT HIỆN (vòng quét, quyền hệ thống): dòng sao kê còn mở quá N ngày (``acct.discrepancy_rules.stale_days``, mặc định 3) HOẶC dòng còn mới nhưng có khoản phải
  thu QUÁ HẠN trúng đủ mạnh. Mỗi dòng một hồ sơ (UNIQUE) — ``acct.discrepancy_dossiers``.
* PHẦN TÍNH ở ``dossier_logic.py`` (hàm thuần): ứng viên có điểm, lời giải thích theo mẫu (thiếu thuế GTGT, nhiều khoản một lần chuyển, trả một phần, bên trả giữ
  phí, tiền của pháp nhân khác, chuyển trùng, chưa rõ bên chuyển) kèm bằng chứng + độ chắc. Tin nháp hỏi bên trả dựng bằng mẫu từ sự thật — model không tham gia.
* HÀNH ĐỘNG là thẻ đề xuất: mỗi bước = MỘT lệnh Tài chính có sẵn (``apply_line``, ``write_off_dossier`` → ``credit_receivable``…) — hồ sơ KHÔNG ghi sổ cái, tiền chỉ
  đi qua các lệnh đó khi người xác nhận. Soạn tin hỏi bên trả chỉ là bản nháp, không bao giờ tự gửi.
* THEO TỚI KHI XONG: một việc theo dõi loại ``discrepancy`` (``work.items``) giao cho người phụ trách (chính sách là dữ liệu: luật theo pháp nhân / nguồn khoản →
  người phụ trách khoản phải thu → kế toán). Tự đóng khi dòng đã phân bổ HẾT và các khoản hồ sơ nhắm tới không còn nợ (``resolved``), khi có xoá phần thiếu có duyệt
  (``written_off``), khi dòng bị bỏ qua / người đóng hồ sơ (``dismissed``). Hoa hồng và gói nguồn phản ứng qua ``acct.payment_received`` sẵn có.
"""

import logging
import secrets
from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.api import Action as A
from barista.api import (
    Conflict,
    Ctx,
    Desk,
    Event,
    Input,
    Invalid,
    Money,
    NoInput,
    NotFound,
    Open,
    Screen,
    WorkIn,
    company_settings,
    dong,
    entities,
    field,
    kpi,
    line,
    today,
    ui,
    zone,
)
from barista.kernel import registry
from barista.modules.acct import G_IN, G_SETUP, m
from barista.modules.acct import dossier_logic as L
from barista.modules.acct.common import _vnd
from barista.modules.acct.reconcile import FEE_TOLERANCE

log = logging.getLogger("barista.acct.dossier")

STALE_DAYS = 3        # mặc định khi công ty chưa đặt luật
RESOLVE_DAYS = 5      # hạn xử lý của việc theo dõi (ngày kể từ lúc dựng hồ sơ)
MAX_PER_SCAN = 200

STATUS = {"open": "Đang xử lý", "resolved": "Đã xong", "written_off": "Đã xoá phần thiếu", "dismissed": "Đã đóng (không phải khoản lệch)"}
STATUS_TONES = {"open": "orange", "resolved": "green", "written_off": "blue", "dismissed": "gray"}
CONF_TONES = {"high": "green", "medium": "orange", "low": "red"}
DECISION = {"": "Chưa quyết", "vat_follow_up": "Thiếu thuế GTGT — chờ bên trả bù", "other_entity": "Tiền của pháp nhân khác", "asked_payer": "Đã hỏi bên trả"}
TRIGGER = {"stale_line": "Dòng tiền về chưa xử lý quá hạn", "overdue_receivable": "Khoản quá hạn có dòng tiền trúng", "manual": "Kế toán lập tay"}

m.trigger("acct.dossier_opened", "Mở hồ sơ khoản lệch", {"code": "Mã hồ sơ", "pattern": "Mẫu", "left_vnd": "Tiền chưa ghi (đồng)"}, open=("acct.dossier", {"code": "code"}))
m.trigger("acct.dossier_closed", "Đóng hồ sơ khoản lệch", {"code": "Mã hồ sơ", "status": "Kết quả"}, open=("acct.dossier", {"code": "code"}))


# ── Chính sách (dữ liệu công ty) ──

async def _rules(ctx: Ctx) -> list[dict[str, Any]]:
    return await ctx.all("SELECT * FROM acct.discrepancy_rules WHERE active")


def _param(rules: list[dict[str, Any]], entity: Any, name: str, default: int) -> int:
    """Ngưỡng: luật của pháp nhân (nguồn trống) thắng luật chung; không có ⇒ mặc định trong code."""
    for want in (entity, None):
        for r in rules:
            if r["source"] == "" and r["entity_id"] == want and r[name] is not None:
                return int(r[name])
    return default


def _rule_owner(rules: list[dict[str, Any]], entity: Any, source: str) -> UUID | None:
    best: tuple[tuple[int, bool], UUID] | None = None
    for r in rules:
        if r["owner_id"] and r["entity_id"] in (None, entity) and (r["source"] == "" or source.startswith(r["source"])):
            cand = ((len(r["source"]), r["entity_id"] is not None), r["owner_id"])
            if best is None or cand[0] > best[0]:
                best = cand
    return best[1] if best else None


async def _active(ctx: Ctx, user_id: UUID | None) -> bool:
    return user_id is not None and bool(await ctx.val("SELECT 1 FROM core.memberships WHERE user_id = %s AND active", (user_id,)))


async def _owner_for(ctx: Ctx, rules: list[dict[str, Any]], entity: Any, targets: list[dict[str, Any]]) -> tuple[UUID | None, str]:
    """Người phụ trách: luật công ty (pháp nhân × nguồn khoản) → người phụ trách khoản phải thu → người có quyền ghi Kế toán cả công ty."""
    for t in targets:
        if (o := _rule_owner(rules, entity, t.get("source") or "")) and await _active(ctx, o):
            return o, "luật phân công của công ty"
    if not targets and (o := _rule_owner(rules, entity, "")) and await _active(ctx, o):
        return o, "luật phân công của công ty"
    for t in targets:
        if t.get("owner_id") and await _active(ctx, t["owner_id"]):
            return t["owner_id"], "người phụ trách khoản phải thu"
    owners = await ctx.call_system("admin.members_with_grant", resource="acct", action="write", scope="all")
    return (UUID(owners[0]), "kế toán (mặc định)") if owners else (None, "chưa có ai")


# ── Dữ liệu cho phần tính ──

async def _vat_info(ctx: Ctx, recs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Dữ liệu thuế của công ty cho từng khoản: hoá đơn GTGT còn hiệu lực (chính xác) → loại thuế × bộ quy định khi công ty bật thuế (giả định, chưa có hoá đơn)."""
    from barista.modules.acct.vat import VAT_ON, _plan

    out: dict[str, dict[str, Any]] = {}
    ids = [r["id"] for r in recs if r["status"] in ("open", "partial")]
    if not ids:
        return out
    invoiced = {r["receivable_id"]: r for r in await ctx.all(
        "SELECT receivable_id, rate_bps, tax_vnd FROM acct.vat_invoices WHERE kind = 'invoice' AND cancelled_on IS NULL AND receivable_id = ANY(%s)", (ids,))}
    on = await company_settings.enabled(ctx.conn, ctx.p.tenant_id, VAT_ON)
    for r in recs:
        if (inv := invoiced.get(r["id"])) is not None:
            out[r["code"]] = {"kind": "invoice", "rate_bps": inv["rate_bps"], "tax_vnd": inv["tax_vnd"]}
        elif on and r["status"] == "open" and r["nature"] == "revenue":
            try:
                p = await _plan(ctx, r, "", "", today())
            except Invalid:
                continue   # chưa có loại thuế áp cho khoản này ⇒ không có dữ liệu thuế ⇒ không giả định mức nào
            if p["price_mode"] == "inclusive" and p["tax"]:
                out[r["code"]] = {"kind": "category", "rate_bps": p["rate"].rate_bps, "tax_vnd": p["tax"]}
    return out


async def _world(ctx: Ctx) -> dict[str, Any]:
    """Mọi thứ phần tính cần, đọc MỘT lần cho cả vòng quét."""
    rows = await ctx.all("""SELECT id, code, source, party, amount_vnd, paid_vnd, due_on, status, nature, match_hints, match_keys, entity_id, invoice_no, owner_id
                            FROM acct.receivables WHERE status IN ('open', 'partial') OR (status = 'paid' AND issued_on >= core.today() - 180)""")
    vat = await _vat_info(ctx, rows)
    return {"rules": await _rules(ctx), "recs": L.prepare(rows), "vat": vat, "entities": await entities.names(ctx),
            "invoiced": {c for c, v in vat.items() if v["kind"] == "invoice"}}


def _line_dict(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": r["id"], "received_on": r["received_on"], "amount_vnd": r["amount_vnd"], "left": r["amount_vnd"] + r["deducted_vnd"] - r["allocated_vnd"],
            "memo": r["memo"], "payer": r["payer"], "bank_ref": r["bank_ref"], "entity_id": r["entity_id"], "status": r["status"]}


async def _analyse(ctx: Ctx, w: dict[str, Any], ln: dict[str, Any]) -> dict[str, Any]:
    cands = L.score_candidates(ln, w["recs"], w["vat"], today())
    others = await ctx.all("""SELECT id, received_on, amount_vnd, payer, memo, bank_ref, status, allocated_vnd, entity_id FROM acct.statement_lines
                              WHERE entity_id = %s AND id <> %s AND amount_vnd = %s AND status <> 'ignored' AND received_on BETWEEN %s AND %s""",
                           (ln["entity_id"], ln["id"], ln["amount_vnd"], ln["received_on"] - timedelta(days=L.DUP_WINDOW_DAYS), ln["received_on"] + timedelta(days=L.DUP_WINDOW_DAYS)))
    expl = L.explain(ln, w["recs"], cands, others, w["vat"], w["entities"], _param(w["rules"], ln["entity_id"], "fee_tolerance_vnd", FEE_TOLERANCE))
    return {"cands": cands, "expl": expl}


def _facts(ln: dict[str, Any], an: dict[str, Any], dossier: str, w: dict[str, Any]) -> dict[str, Any]:
    primary = an["expl"][0]
    who = (ln["payer"] or "").strip() or "bên chuyển"
    acts = L.actions_for(dossier, ln, primary, w["invoiced"], who)
    return {"line": {"id": str(ln["id"]), "received_on": ln["received_on"].isoformat(), "amount_vnd": ln["amount_vnd"], "left_vnd": ln["left"], "payer": ln["payer"],
                     "memo": ln["memo"], "bank_ref": ln["bank_ref"]},
            "candidates": an["cands"], "explanations": an["expl"], "actions": acts}


# ── Dựng / làm mới / đóng ──

async def _event(ctx: Ctx, dossier_id: UUID, kind: str, text: str, data: dict[str, Any] | None = None) -> None:
    await ctx.exec("INSERT INTO acct.discrepancy_events (dossier_id, kind, text, data, created_by) VALUES (%s, %s, %s, %s, core.me())",
                   (dossier_id, kind, text[:1000], Jsonb(data or {})))


async def _create(ctx: Ctx, w: dict[str, Any], ln: dict[str, Any], trigger: str) -> dict[str, Any] | None:
    an = await _analyse(ctx, w, ln)
    primary = an["expl"][0]
    targets = [r for r in w["recs"] if r["code"] in primary["codes"]]
    owner, why = await _owner_for(ctx, w["rules"], ln["entity_id"], targets)
    team = await ctx.home_team(owner) if owner else None
    due = today() + timedelta(days=_param(w["rules"], ln["entity_id"], "resolve_days", RESOLVE_DAYS))
    for _ in range(3):
        code = "KL-" + secrets.token_hex(4).upper()
        facts = _facts(ln, an, code, w)
        did = await ctx.val(
            """INSERT INTO acct.discrepancy_dossiers (code, line_id, entity_id, trigger, pattern, confidence, facts, target_codes, owner_id, team_id, due_on, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, core.me()) ON CONFLICT DO NOTHING RETURNING id""",
            (code, ln["id"], ln["entity_id"], trigger, primary["pattern"], primary["confidence"], Jsonb(facts), primary["codes"], owner, team, due))
        if did is not None:
            break
        if await ctx.val("SELECT 1 FROM acct.discrepancy_dossiers WHERE line_id = %s", (ln["id"],)):
            return None   # dòng này đã có hồ sơ (vòng quét chạy chồng)
    else:
        return None
    await _event(ctx, did, "opened", f"Dựng hồ sơ ({TRIGGER[trigger].lower()}): {primary['label'].lower()} — {conf_label(primary)}", {"pattern": primary["pattern"]})
    ctx.emit("acct.dossier_opened", code=code, pattern=primary["pattern"], left_vnd=ln["left"])
    if owner is not None:
        await _event(ctx, did, "assigned", f"Giao cho người phụ trách theo {why}")
        who = (ln["payer"] or "").strip() or "chưa rõ bên chuyển"
        await ctx.call_system("work.open", kind="discrepancy", owner_id=str(owner), subject_ref=code, title=f"Khoản lệch {_vnd(ln['left'])} — {who}"[:300],
                              note=f"Tiền về {_vnd(ln['amount_vnd'])} ngày {ln['received_on']:%d/%m/%Y} chưa khớp công nợ. {primary['summary']}"[:1000])
    return {"code": code, "dossier_id": did, "pattern": primary["pattern"], "owner_id": owner}


def conf_label(e: dict[str, Any]) -> str:
    return L.CONFIDENCE[e["confidence"]].lower()


async def _evaluate(ctx: Ctx, d: dict[str, Any]) -> str | None:
    """Kiểm điều kiện xong BẰNG CODE: trả trạng thái mới nếu hồ sơ phải đóng, None nếu còn mở. Đóng rồi thì đánh thức việc theo dõi."""
    if d["status"] != "open":
        return None
    ln = await ctx.one("SELECT status, amount_vnd + deducted_vnd - allocated_vnd AS left FROM acct.statement_lines WHERE id = %s", (d["line_id"],))
    if ln is None:
        return None
    new, note = None, ""
    if ln["status"] == "ignored":
        new, note = "dismissed", "Dòng sao kê đã được bỏ qua."
    elif ln["status"] == "applied":
        owing = await ctx.all("SELECT code, amount_vnd - paid_vnd AS rest FROM acct.receivables WHERE code = ANY(%s) AND status NOT IN ('void', 'paid')", (d["target_codes"],))
        owing = [r for r in owing if r["rest"] > 0]
        if not owing:
            new = "written_off" if d["written_off_vnd"] else "resolved"
            note = "Dòng tiền đã phân bổ hết; các khoản hồ sơ nhắm tới không còn nợ."
    if new is None:
        return None
    await _close(ctx, d, new, note)
    return new


async def _close(ctx: Ctx, d: dict[str, Any], status: str, note: str) -> None:
    await ctx.exec("UPDATE acct.discrepancy_dossiers SET status = %s, closed_at = now(), closed_by = core.me(), resolution_note = %s WHERE id = %s AND status = 'open'",
                   (status, note[:1000], d["id"]))
    await _event(ctx, d["id"], "closed", f"{STATUS[status]}. {note}")
    ctx.emit("acct.dossier_closed", code=d["code"], status=status)
    await ctx.call_system("work.wake", kind="discrepancy", subject_ref=d["code"], text=f"Hồ sơ {d['code']}: {STATUS[status].lower()}.")


async def _refresh(ctx: Ctx, w: dict[str, Any], d: dict[str, Any]) -> bool:
    """Tính lại ứng viên / lời giải thích theo dữ liệu hiện tại (dòng đã ghi một phần, khoản mới lập…). Đóng nếu đủ điều kiện. True = còn mở."""
    if await _evaluate(ctx, d):
        return False
    row = await ctx.one("SELECT * FROM acct.statement_lines WHERE id = %s", (d["line_id"],))
    if row is None or row["status"] != "open":
        return True
    ln = _line_dict(row)
    an = await _analyse(ctx, w, ln)
    primary = an["expl"][0]
    facts = _facts(ln, an, d["code"], w)
    changed = primary["pattern"] != d["pattern"] or primary["codes"] != d["target_codes"] or ln["left"] != d["facts"].get("line", {}).get("left_vnd")
    await ctx.exec("""UPDATE acct.discrepancy_dossiers SET facts = %s, pattern = %s, confidence = %s, target_codes = %s, refreshed_at = now() WHERE id = %s""",
                   (Jsonb(facts), primary["pattern"], primary["confidence"], primary["codes"], d["id"]))
    if changed:
        await _event(ctx, d["id"], "refreshed", f"Làm mới: {primary['label'].lower()} — {conf_label(primary)}; chưa ghi {_vnd(ln['left'])}")
    return True


# ── Vòng quét ──

@m.command("scan_discrepancies", resource="acct", title="Dựng và làm mới hồ sơ khoản lệch", internal=True,
           # BẬT THEO CÔNG TY (soát 14/11): chỉ công ty đã đặt ít nhất một luật phân công khoản lệch còn hiệu lực, và chỉ dòng nhận TỪ NGÀY đặt luật
           # đầu tiên — bật không làm bùng hàng loạt hồ sơ / việc cho dòng tồn cũ (dòng cũ: lập tay ``acct.build_dossier``)
           sweep="""SELECT tenant_id FROM acct.statement_lines l WHERE l.status = 'open' AND l.received_on < core.today_of(l.tenant_id)
                       AND l.received_on >= (SELECT min(r.created_at)::date FROM acct.discrepancy_rules r WHERE r.tenant_id = l.tenant_id AND r.active)
                       AND NOT EXISTS (SELECT 1 FROM acct.discrepancy_dossiers d WHERE d.tenant_id = l.tenant_id AND d.line_id = l.id)
                    UNION SELECT tenant_id FROM acct.discrepancy_dossiers WHERE status = 'open' AND refreshed_at < now() - interval '20 minutes'""")
async def scan_discrepancies(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Vòng quét (quyền hệ thống): dòng sao kê còn mở quá ngưỡng ngày — hoặc còn mới nhưng trúng khoản phải thu quá hạn — chưa có hồ sơ ⇒ dựng hồ sơ + mở việc
    theo dõi; hồ sơ đang mở lâu chưa làm mới ⇒ tính lại và tự đóng khi đủ điều kiện. Dòng trống ứng viên vẫn có hồ sơ ("chưa rõ bên chuyển")."""
    w = await _world(ctx)
    opened = 0
    since = await ctx.val("SELECT min(created_at)::date FROM acct.discrepancy_rules WHERE active")
    lines = [] if since is None else await ctx.all(
        """SELECT * FROM acct.statement_lines l WHERE status = 'open' AND received_on >= %s
             AND NOT EXISTS (SELECT 1 FROM acct.discrepancy_dossiers d WHERE d.line_id = l.id)
           ORDER BY received_on, id LIMIT %s""", (since, MAX_PER_SCAN))
    for r in lines:
        ln = _line_dict(r)
        age = (today() - ln["received_on"]).days
        if age >= _param(w["rules"], ln["entity_id"], "stale_days", STALE_DAYS):
            trigger = "stale_line"
        elif L.best_overdue(L.score_candidates(ln, w["recs"], w["vat"], today())):
            trigger = "overdue_receivable"
        else:
            continue
        if await _create(ctx, w, ln, trigger):
            opened += 1
    refreshed = closed = 0
    for d in await ctx.all("SELECT * FROM acct.discrepancy_dossiers WHERE status = 'open' AND refreshed_at < now() - interval '20 minutes' ORDER BY refreshed_at LIMIT %s",
                           (MAX_PER_SCAN,)):
        refreshed += 1
        closed += 0 if await _refresh(ctx, w, d) else 1
    return {"opened": opened, "refreshed": refreshed, "closed": closed}


@m.on("acct.payment_received")
async def recheck_on_payment(ctx: Ctx, event: Event) -> None:
    """Có phiếu thu mới ⇒ kiểm lại hồ sơ của dòng đó / hồ sơ nhắm tới khoản đó: đủ điều kiện thì tự đóng (đánh thức việc theo dõi)."""
    code = event.payload["code"]
    for d in await ctx.all("""SELECT * FROM acct.discrepancy_dossiers WHERE status = 'open' AND (%s = ANY(target_codes)
                              OR line_id IN (SELECT p.line_id FROM acct.payments p JOIN acct.receivables r ON r.id = p.receivable_id WHERE r.code = %s))""", (code, code)):
        await _evaluate(ctx, d)


# ── Lệnh của kế toán ──

class Ref(Input):
    code: str = field("Hồ sơ khoản lệch", options_query="acct.dossier_options")


async def _dossier(ctx: Ctx, code: str, *, lock: bool = False) -> dict[str, Any]:
    d = await ctx.one("SELECT * FROM acct.discrepancy_dossiers WHERE code = %s" + (" FOR UPDATE" if lock else ""), (code.strip().upper(),))
    if d is None:
        raise NotFound(f"Không có hồ sơ khoản lệch mã {code}")
    return d


async def _need_open(d: dict[str, Any]) -> None:
    if d["status"] != "open":
        raise Conflict(f"Hồ sơ {d['code']} đã đóng ({STATUS[d['status']].lower()})")


class LineRef(Input):
    line_id: UUID = field("Dòng sao kê", hidden=True)


@m.command("build_dossier", resource="acct", title="Lập hồ sơ khoản lệch cho một dòng tiền về")
async def build_dossier(ctx: Ctx, inp: LineRef) -> dict[str, Any]:
    """Kế toán lập tay cho một dòng còn mở (không đợi vòng quét). Mỗi dòng một hồ sơ: đã có ⇒ trả hồ sơ cũ."""
    ctx.require("acct", "write")
    row = await ctx.one("SELECT * FROM acct.statement_lines WHERE id = %s AND status = 'open'", (inp.line_id,))
    if row is None:
        raise NotFound("Không thấy dòng sao kê đang chờ đối soát")
    if old := await ctx.val("SELECT code FROM acct.discrepancy_dossiers WHERE line_id = %s", (inp.line_id,)):
        return {"code": old, "created": False, "summary": f"Dòng này đã có hồ sơ {old}"}
    got = await _create(ctx, await _world(ctx), _line_dict(row), "manual")
    if got is None:
        raise Conflict("Dòng này vừa có hồ sơ")
    return {"code": got["code"], "created": True, "pattern": got["pattern"], "summary": f"Đã lập hồ sơ {got['code']}"}


@m.command("refresh_dossier", resource="acct", title="Làm mới hồ sơ khoản lệch", agent="allow")
async def refresh_dossier(ctx: Ctx, inp: Ref) -> dict[str, Any]:
    d = await _dossier(ctx, inp.code, lock=True)
    await _need_open(d)
    still = await _refresh(ctx, await _world(ctx), d)
    return {"code": d["code"], "open": still, "summary": "Đã làm mới" if still else "Hồ sơ đủ điều kiện nên đã tự đóng"}


@m.command("draft_dossier_ask", resource="acct", title="Soạn tin nháp hỏi bên trả (không gửi)", agent="allow")
async def draft_dossier_ask(ctx: Ctx, inp: Ref) -> dict[str, Any]:
    """Tin nháp dựng bằng mẫu từ các con số của hồ sơ, lưu vào hồ sơ để kế toán xem / sửa / gửi. Hệ thống KHÔNG gửi gì ra ngoài."""
    d = await _dossier(ctx, inp.code, lock=True)
    await _need_open(d)
    facts = d["facts"]
    ln = {**facts["line"], "received_on": date.fromisoformat(facts["line"]["received_on"])}
    cand = facts["candidates"][0] if facts["candidates"] else None
    text = L.draft_ask(ln, facts["explanations"][0], cand)
    await ctx.exec("UPDATE acct.discrepancy_dossiers SET draft = %s, draft_at = now() WHERE id = %s", (text, d["id"]))
    await _event(ctx, d["id"], "draft", "Soạn tin nháp hỏi bên trả (chưa gửi)")
    return {"code": d["code"], "draft": text, "summary": "Đã soạn tin nháp — chưa gửi"}


class Mark(Input):
    code: str = field("Hồ sơ khoản lệch", options_query="acct.dossier_options")
    mark: Literal["vat_follow_up", "other_entity", "asked_payer"] = field("Ghi nhận", options={k: v for k, v in DECISION.items() if k})
    note: str = field("Ghi chú", "", max_length=1000, kind="textarea")


@m.command("mark_dossier", resource="acct", title="Ghi nhận hướng xử lý khoản lệch (không ghi sổ)")
async def mark_dossier(ctx: Ctx, inp: Mark) -> dict[str, Any]:
    """Không đụng tiền: ghi hướng xử lý để theo dõi — thiếu thuế GTGT chờ bên trả bù, tiền của pháp nhân khác (kế toán chuyển nội bộ), đã hỏi bên trả. Hồ sơ vẫn mở
    tới khi code thấy xong (dòng phân bổ hết, khoản không còn nợ)."""
    ctx.require("acct", "write")
    d = await _dossier(ctx, inp.code, lock=True)
    await _need_open(d)
    await ctx.exec("UPDATE acct.discrepancy_dossiers SET decision = %s, decision_note = %s, decided_by = core.me(), decided_at = now() WHERE id = %s",
                   (inp.mark, inp.note.strip(), d["id"]))
    await _event(ctx, d["id"], "decision", f"{DECISION[inp.mark]}. {inp.note.strip()}", {"mark": inp.mark})
    return {"code": d["code"], "mark": inp.mark, "summary": f"Ghi nhận: {DECISION[inp.mark].lower()}"}


class Dismiss(Input):
    code: str = field("Hồ sơ khoản lệch", options_query="acct.dossier_options")
    reason: str = field("Lý do", max_length=500, example="Chuyển nội bộ giữa hai tài khoản công ty, đã ghi tay")


@m.command("dismiss_dossier", resource="acct", title="Đóng hồ sơ — không phải khoản lệch")
async def dismiss_dossier(ctx: Ctx, inp: Dismiss) -> dict[str, Any]:
    ctx.require("acct", "write")
    d = await _dossier(ctx, inp.code, lock=True)
    await _need_open(d)
    if not inp.reason.strip():
        raise Invalid("Cần lý do đóng hồ sơ")
    await _close(ctx, d, "dismissed", inp.reason.strip())
    return {"code": d["code"], "summary": f"Đã đóng hồ sơ {d['code']}"}


class WriteOff(Input):
    code: str = field("Hồ sơ khoản lệch", options_query="acct.dossier_options")
    receivable: str = field("Khoản phải thu", options_query="acct.receivable_options")
    amount_vnd: Money = field("Số tiền xoá", ge=1, example="2.000.000")
    reason: str = field("Lý do", max_length=500)


@m.command("write_off_dossier", resource="acct", title="Xoá phần thiếu của khoản lệch (cần duyệt)", risk="money")
async def write_off_dossier(ctx: Ctx, inp: WriteOff) -> dict[str, Any]:
    """Xoá phần thiếu của một khoản hồ sơ nhắm tới — CHỈ sau phiếu duyệt do người khác duyệt (chuỗi ``receivable_correction``, như giấy báo có). Tiền đi qua đúng
    ``acct.credit_receivable`` (giảm phải thu, bút toán sổ cái, chống lặp theo mã hồ sơ); hồ sơ chỉ ghi lại và tự đóng khi dòng tiền đã phân bổ hết."""
    from barista.modules.acct.corrections import _need_approval, _need_books

    _need_books(ctx)
    _need_approval(ctx)
    d = await _dossier(ctx, inp.code, lock=True)
    await _need_open(d)
    code = inp.receivable.strip().upper()
    if code not in d["target_codes"]:
        raise Invalid(f"{code} không phải khoản hồ sơ {d['code']} nhắm tới ({', '.join(d['target_codes']) or 'không có'})")
    out = await ctx.call("acct.credit_receivable", code=code, amount_vnd=inp.amount_vnd, reason=inp.reason.strip() or f"Xoá phần thiếu, hồ sơ {d['code']}",
                         corrects=f"{d['code']}:{code}")
    if out.get("created", True):
        await ctx.exec("UPDATE acct.discrepancy_dossiers SET written_off_vnd = written_off_vnd + %s WHERE id = %s", (inp.amount_vnd, d["id"]))
    await _event(ctx, d["id"], "write_off", f"Xoá {_vnd(inp.amount_vnd)} của {code} (đã duyệt): {inp.reason.strip()}", {"receivable": code, "amount_vnd": inp.amount_vnd})
    d = await _dossier(ctx, d["code"])
    closed = await _evaluate(ctx, d)
    return {"code": d["code"], "receivable": code, "credited_vnd": inp.amount_vnd, "closed": closed or "",
            "summary": f"Xoá {_vnd(inp.amount_vnd)} của {code}" + (" · hồ sơ đã đóng" if closed else "")}


class Assign(Input):
    code: str = field("Hồ sơ khoản lệch", options_query="acct.dossier_options")
    owner_email: str = field("Người phụ trách", options_query="admin.member_options")


@m.command("assign_dossier", resource="acct", action="manage", title="Giao lại hồ sơ khoản lệch", agent="deny")
async def assign_dossier(ctx: Ctx, inp: Assign) -> dict[str, Any]:
    """Đổi người phụ trách: việc theo dõi cũ dừng (bàn giao), mở việc mới cho người mới — cùng một hồ sơ."""
    d = await _dossier(ctx, inp.code, lock=True)
    await _need_open(d)
    uid = await ctx.val("SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant() WHERE u.email = %s AND mb.active",
                        (inp.owner_email.strip().lower(),))
    if uid is None:
        raise NotFound(f"{inp.owner_email} không phải thành viên đang hoạt động của công ty này")
    team = await ctx.home_team(uid)
    await ctx.exec("UPDATE acct.discrepancy_dossiers SET owner_id = %s, team_id = %s WHERE id = %s", (uid, team, d["id"]))
    await _event(ctx, d["id"], "assigned", f"Giao lại cho {inp.owner_email.strip().lower()}")
    ln = d["facts"]["line"]
    await ctx.call_system("work.handoff", kind="discrepancy", owner_id=str(uid), subject_ref=d["code"],
                          title=f"Khoản lệch {_vnd(ln['left_vnd'])} — {(ln['payer'] or '').strip() or 'chưa rõ bên chuyển'}"[:300], note=f"{ctx.p.name} giao lại hồ sơ {d['code']}.")
    return {"code": d["code"], "summary": f"Đã giao {d['code']} cho {inp.owner_email.strip().lower()}"}


# ── Luật phân công + ngưỡng (dữ liệu công ty) ──

class SetRule(Input):
    entity_code: str = field("Pháp nhân", "", options_query="admin.entity_options", placeholder="Mọi pháp nhân", group="Thêm chi tiết")
    source: str = field("Nguồn khoản phải thu", "", options_query="acct.receivable_source_options", max_length=100, placeholder="Mọi nguồn",
                        help="Luật riêng cho khoản phải thu của một nguồn; trống = luật chung")
    owner_email: str = field("Người phụ trách", "", options_query="admin.member_options", placeholder="Luật này chỉ đặt ngưỡng")
    stale_days: int | None = field("Dòng tiền chưa xử lý quá (ngày) thì dựng hồ sơ", None, ge=0, le=90, group="Ngưỡng",
                                   placeholder="Giữ / mặc định 3", help="Chỉ đọc ở luật không chọn nguồn")
    fee_tolerance_vnd: Money | None = field("Chênh nhỏ coi là phí / khấu trừ (đồng)", None, placeholder="Giữ / mặc định 50.000", group="Ngưỡng")
    resolve_days: int | None = field("Hạn xử lý (ngày)", None, ge=1, le=90, placeholder="Giữ / mặc định 5", group="Ngưỡng")
    active: bool = field("Đang dùng", True, group="Thêm chi tiết")
    note: str = field("Ghi chú", "", max_length=500, group="Thêm chi tiết")


@m.query("receivable_source_options", resource="acct", title="Nguồn khoản phải thu")
async def receivable_source_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Nguồn đang có khoản phải thu (``<module>.<loại>``), nhãn kèm tên phân hệ — người chọn, không gõ tiền tố."""
    out = []
    for r in await ctx.all("SELECT DISTINCT source FROM acct.receivables ORDER BY source"):
        mod, _, kind = r["source"].partition(".")
        title = registry.MODULES.get(mod, mod)
        out.append({"value": r["source"], "label": f"{title} › {kind}" if kind else title})
    return out


@m.command("set_discrepancy_rule", resource="acct", action="manage", title="Đặt luật phân công / ngưỡng khoản lệch", category="setup")
async def set_discrepancy_rule(ctx: Ctx, inp: SetRule) -> dict[str, Any]:
    eid = await entities.resolve(ctx, inp.entity_code) if inp.entity_code.strip() else None
    owner = None
    if inp.owner_email.strip():
        owner = await ctx.val("SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant() WHERE u.email = %s AND mb.active",
                              (inp.owner_email.strip().lower(),))
        if owner is None:
            raise NotFound(f"{inp.owner_email} không phải thành viên đang hoạt động của công ty này")
    rid = await ctx.val(
        """INSERT INTO acct.discrepancy_rules (entity_id, source, owner_id, stale_days, fee_tolerance_vnd, resolve_days, active, note, updated_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, core.me())
           ON CONFLICT (tenant_id, coalesce(entity_id, '00000000-0000-0000-0000-000000000000'::uuid), source) DO UPDATE SET
               owner_id = EXCLUDED.owner_id, stale_days = coalesce(EXCLUDED.stale_days, acct.discrepancy_rules.stale_days),
               fee_tolerance_vnd = coalesce(EXCLUDED.fee_tolerance_vnd, acct.discrepancy_rules.fee_tolerance_vnd),
               resolve_days = coalesce(EXCLUDED.resolve_days, acct.discrepancy_rules.resolve_days), active = EXCLUDED.active, note = EXCLUDED.note,
               updated_by = core.me(), updated_at = now() RETURNING id""",
        (eid, inp.source.strip(), owner, inp.stale_days, inp.fee_tolerance_vnd, inp.resolve_days, inp.active, inp.note.strip()))
    return {"rule_id": rid, "summary": "Đã lưu luật khoản lệch"}


@m.query("discrepancy_rules", resource="acct", title="Luật phân công khoản lệch", min_scope="all")
async def discrepancy_rules(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("""SELECT r.id, coalesce(e.code, 'Mọi pháp nhân') AS entity, r.source, u.name AS owner, r.stale_days, r.fee_tolerance_vnd, r.resolve_days, r.active, r.note
                            FROM acct.discrepancy_rules r LEFT JOIN core.legal_entities e ON e.id = r.entity_id LEFT JOIN core.users u ON u.id = r.owner_id ORDER BY e.code NULLS FIRST, r.source""")
    return ui.table(rows, [ui.col("entity", "Pháp nhân"), ui.col("source", "Nguồn"), ui.col("owner", "Người phụ trách"), ui.col("stale_days", "Quá (ngày)", "number"),
                           ui.col("fee_tolerance_vnd", "Chênh nhỏ (đ)", "money"), ui.col("resolve_days", "Hạn xử lý (ngày)", "number"), ui.col("note", "Ghi chú")],
                    title="Luật phân công khoản lệch", empty="Chưa có luật — mặc định: người phụ trách khoản phải thu, rồi kế toán; dòng quá 3 ngày dựng hồ sơ",
                    note="Luật cụ thể (pháp nhân + nguồn) thắng luật chung. Ngưỡng ngày / chênh nhỏ / hạn đọc từ luật không chọn nguồn.")


# ── Màn hình ──

@m.query("dossier_options", resource="acct", title="Hồ sơ khoản lệch đang mở")
async def dossier_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    rows = await ctx.all("SELECT code, facts->'line'->>'left_vnd' AS left_vnd, facts->'line'->>'payer' AS payer FROM acct.discrepancy_dossiers WHERE status = 'open' ORDER BY created_at DESC LIMIT 200")
    return [{"value": r["code"], "label": f"{r['code']} · {_vnd(int(r['left_vnd'] or 0))} · {r['payer'] or 'chưa rõ bên chuyển'}"} for r in rows]


class DossierFilter(Input):
    status: Literal["", "open", "resolved", "written_off", "dismissed"] = field("Trạng thái", "open", options={"": "Tất cả", **STATUS})


@m.query("dossiers", resource="acct", title="Khoản lệch cần xử lý", min_scope="all")
async def dossiers(ctx: Ctx, inp: DossierFilter) -> dict[str, Any]:
    """Danh sách hồ sơ khoản lệch toàn công ty (số trên thẻ = kết quả truy vấn này — code tính, không phải chữ của model)."""
    rows = await ctx.all("""SELECT d.code AS id, d.code, d.status, d.pattern, d.confidence, d.decision, d.due_on, d.created_at::date AS opened_on, d.target_codes,
                                   (d.facts->'line'->>'left_vnd')::bigint AS left_vnd, d.facts->'line'->>'payer' AS payer, d.facts->'line'->>'memo' AS memo,
                                   u.name AS owner, d.due_on < core.today() AND d.status = 'open' AS late
                            FROM acct.discrepancy_dossiers d LEFT JOIN core.users u ON u.id = d.owner_id
                            WHERE (%s = '' OR d.status = %s) ORDER BY d.status = 'open' DESC, d.due_on, d.created_at LIMIT 500""", (inp.status, inp.status))
    for r in rows:
        r["targets"] = ", ".join(r["target_codes"])
        r["state"] = r["status"]
    openn = [r for r in rows if r["status"] == "open"]
    return ui.table(rows, [
        ui.col("state", "Trạng thái", "badge", labels=STATUS, tones=STATUS_TONES), ui.col("left_vnd", "Tiền chưa ghi", "money"),
        ui.col("pattern", "Dấu hiệu chính", "badge", labels=L.PATTERNS), ui.col("confidence", "Độ chắc", "badge", labels=L.CONFIDENCE, tones=CONF_TONES),
        ui.col("payer", "Bên chuyển"), ui.col("targets", "Khoản nhắm tới"), ui.col("owner", "Người phụ trách"), ui.col("due_on", "Hạn", "date"),
        ui.col("decision", "Hướng xử lý", "badge", labels=DECISION), ui.col("memo", "Nội dung chuyển khoản", width=240), ui.col("code", "Mã hồ sơ")],
        title="Khoản lệch cần xử lý", empty="Không có khoản lệch nào cần xử lý",
        kpis=[ui.kpi("Đang mở", len(openn), tone="orange" if openn else "green"), ui.kpi("Quá hạn xử lý", sum(1 for r in openn if r["late"]), tone="red"),
              ui.kpi("Tiền chưa ghi", sum(r["left_vnd"] for r in openn), "money"),
              ui.kpi("Chưa rõ bên chuyển", sum(1 for r in openn if r["pattern"] == "unknown"), tone="red")],
        note="Hồ sơ do code dựng từ sao kê + công nợ; lời giải thích có bằng chứng và độ chắc — 'chưa chắc' nghĩa là chỉ trùng khớp số học, không phải nguyên nhân đã xác nhận.")


def _say_dossiers(o: Any) -> list[Any]:
    n, late = int(kpi(o, "Đang mở") or 0), int(kpi(o, "Quá hạn xử lý") or 0)
    left = int(kpi(o, "Tiền chưa ghi") or 0)
    return [line("bad" if late else "warn", f"{n} khoản tiền về lệch công nợ cần xử lý" + (f", {late} quá hạn" if late else ""),
                 f"{_vnd(left)} chưa ghi — hồ sơ có sẵn ứng viên, lời giải thích và việc đề xuất", "Khoản tiền về nào đang lệch công nợ, nên xử lý thế nào?") if n else None]


@m.query("dossier", resource="acct", title="Hồ sơ khoản lệch")
async def dossier(ctx: Ctx, inp: Ref) -> dict[str, Any]:
    """Chi tiết một hồ sơ: ứng viên có điểm, lời giải thích + bằng chứng + độ chắc, việc đề xuất (mỗi nút = một lệnh Tài chính có sẵn), tin nháp, nhật ký.
    Số liệu là ảnh chụp lúc dựng / làm mới (ghi giờ) — bấm Làm mới để tính lại."""
    d = await _dossier(ctx, inp.code)
    f, ln = d["facts"], d["facts"]["line"]
    owner = await ctx.val("SELECT name FROM core.users WHERE id = %s", (d["owner_id"],)) if d["owner_id"] else None
    blocks: list[dict[str, Any]] = [ui.b_kpis([
        ui.kpi("Tiền về", ln["amount_vnd"], "money"), ui.kpi("Chưa ghi", ln["left_vnd"], "money", tone="orange" if ln["left_vnd"] else "green"),
        ui.kpi("Trạng thái", STATUS[d["status"]], "text", tone=STATUS_TONES[d["status"]]), ui.kpi("Hạn xử lý", d["due_on"].strftime("%d/%m/%Y"), "text",
                                                                                           tone="red" if d["status"] == "open" and d["due_on"] < today() else None)])]
    if d["decision"]:
        blocks.append(ui.b_callout("accent", DECISION[d["decision"]], d["decision_note"]))
    if d["resolution_note"]:
        blocks.append(ui.b_callout("ok", STATUS[d["status"]], d["resolution_note"]))
    for i, e in enumerate(f["explanations"]):
        facts = [f"{k}: {_fmt(v)}" for k, v in e["evidence"].items() if v not in ("", None, [])]
        blocks.append(ui.b_callout("ok" if e["sure"] else "warn", f"{'Dấu hiệu chính' if i == 0 else 'Dấu hiệu khác'}: {e['label']} — {L.CONFIDENCE[e['confidence']]}", e["summary"],
                                   facts=facts))
    if f["candidates"]:
        rows = [{**c, "id": c["code"], "why": "; ".join(c["why"])} for c in f["candidates"]]
        blocks.append(ui.b_table(ui.table(rows, [ui.col("code", "Khoản"), ui.col("party", "Bên trả"), ui.col("rest_vnd", "Còn phải thu", "money"), ui.col("score", "Điểm khớp", "number"),
                                                 ui.col("why", "Vì sao", width=320), ui.col("due_on", "Hạn", "date")], title="Khoản phải thu ứng viên",
                                          note="Điểm chỉ để xếp hạng, không phải xác suất.")))
    if d["status"] == "open":
        for a in f["actions"]:
            blocks.append(ui.b_callout("accent", a["label"], a["why"] + (" Cần người khác duyệt." if a.get("approval") else "") + (" Có ghi sổ qua lệnh Tài chính." if a.get("money") else ""),
                                       actions=[ui.act(s["command"], s["title"], s["input"]) for s in a["steps"]]))
    if d["draft"]:
        blocks.append(ui.b_callout("muted", "Tin nháp hỏi bên trả — CHƯA GỬI", "Kế toán xem lại và gửi bằng kênh của mình.", draft=d["draft"]))
    evs = await ctx.all("SELECT kind, text, created_at FROM acct.discrepancy_events WHERE dossier_id = %s ORDER BY created_at, id", (d["id"],))
    blocks.append(ui.b_list([{"title": e["text"], "subtitle": e["created_at"].strftime("%d/%m/%Y %H:%M")} for e in evs], "Nhật ký"))
    acts = [ui.act("acct.refresh_dossier", "Làm mới", {"code": d["code"]}), ui.act("acct.draft_dossier_ask", "Soạn tin hỏi bên trả", {"code": d["code"]})] if d["status"] == "open" else []
    sub = (f"{ln['received_on'][8:10]}/{ln['received_on'][5:7]}/{ln['received_on'][:4]} · {(ln['payer'] or '').strip() or 'chưa rõ bên chuyển'} · phụ trách: {owner or 'chưa giao'}"
           f" · chụp lúc {d['refreshed_at'].astimezone(zone()).strftime('%d/%m %H:%M')}")
    return ui.card(f"{d['code']} — {L.PATTERNS.get(d['pattern'], d['pattern'])}", blocks, subtitle=sub, icon="magnifyingglass", actions=acts)


def _fmt(v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(_fmt(x) for x in v)
    if isinstance(v, int) and not isinstance(v, bool) and abs(v) >= 1000:
        return _vnd(v)
    return str(v)


m.screens("accounting", (
    Screen("discrepancies", "Khoản lệch cần xử lý", "exclamationmark.triangle", "acct.dossiers", tint="red", min_scope="all", group=G_IN,
           desk=Desk("decide", say=_say_dossiers, menu=True, rank=25), open=Open("acct.dossier", {"code": "code"}, "Hồ sơ khoản lệch"), row_click="open",
           row_actions=(A("acct.refresh_dossier", "Làm mới", bind={"code": "code"}, when={"state": ["open"]}),
                        A("acct.dismiss_dossier", "Đóng hồ sơ", bind={"code": "code"}, when={"state": ["open"]}))),
), rank=22)
m.screens("accounting", (
    Screen("discrepancy_rules", "Luật phân công khoản lệch", "person.badge.key", "acct.discrepancy_rules", min_scope="all", pinnable=False,
           group=G_SETUP, actions=(A("acct.set_discrepancy_rule", style="primary"),)),
), rank=90)


# ── Gợi ý buổi sáng: hồ sơ của MÌNH — mỗi hồ sơ một thẻ đề xuất (bước đầu của hành động chính; người xác nhận mới chạy) ──

@m.query("discrepancy_suggestions", resource="acct", title="Gợi ý buổi sáng (khoản lệch)", agent="deny")
async def discrepancy_suggestions(ctx: Ctx, inp: NoInput) -> list[dict[str, Any]]:
    rows = await ctx.all("""SELECT code, facts, due_on FROM acct.discrepancy_dossiers WHERE status = 'open' AND owner_id = core.me() ORDER BY due_on, created_at LIMIT 3""")
    out = []
    for i, r in enumerate(rows):
        f = r["facts"]
        act = next((a for a in f["actions"] if a["money"]), None) or (f["actions"][0] if f["actions"] else None)
        if act is None:
            continue
        step = act["steps"][0]
        e = f["explanations"][0]
        late = (today() - r["due_on"]).days
        out.append({"command": step["command"], "input": step["input"], "key": f"dossier:{r['code']}",
                    "reason": f"Hồ sơ {r['code']}: {_vnd(f['line']['left_vnd'])} tiền về chưa ghi — {e['label'].lower()} ({L.CONFIDENCE[e['confidence']].lower()})"
                              + (f", quá hạn xử lý {late} ngày" if late > 0 else ""), "rank": 60 + i * 5})
    return out


m.suggests("acct.discrepancy_suggestions", rank=22)


# ── Việc theo dõi "khoản lệch" (0021 / 0076): xong khi hồ sơ đóng — kiểm bằng code, quyền người phụ trách ──

@m.work_kind("discrepancy", "Khoản lệch tiền về", subject_help="Mã hồ sơ khoản lệch (KL-…)", tools=("acct.dossier", "acct.receivables", "acct.reconcile"))
async def work_discrepancy(ctx: Ctx, inp: WorkIn) -> dict[str, Any]:
    d = await ctx.one("SELECT * FROM acct.discrepancy_dossiers WHERE code = %s", (inp.subject.strip().upper(),))   # người phụ trách không còn thấy ⇒ NotFound ⇒ việc dừng
    if d is None:
        raise NotFound(f"Không có hồ sơ {inp.subject}")
    f = d["facts"]
    ln, e = f["line"], f["explanations"][0]
    late = max(0, (today() - d["due_on"]).days)
    who = (ln["payer"] or "").strip() or "chưa rõ bên chuyển"
    state = "done" if d["status"] in ("resolved", "written_off") else "cancelled" if d["status"] == "dismissed" else "open"
    facts = {"code": d["code"], "pattern": d["pattern"], "confidence": d["confidence"], "left": ln["left_vnd"], "due": d["due_on"].isoformat(), "late_days": late,
             "decision": d["decision"], "targets": d["target_codes"]}
    return {
        "ref": d["code"], "title": f"Khoản lệch {dong(ln['left_vnd'])} — {who}", "state": state, "facts": facts,
        "summary": f"{dong(ln['left_vnd'])} từ {who} chưa ghi hết — {e['label'].lower()} ({L.CONFIDENCE[e['confidence']].lower()}). {e['summary']}",
        "closing": f"Hồ sơ {d['code']}: {STATUS[d['status']].lower()}. {d['resolution_note']}".strip(),
        "task": (f"Hồ sơ khoản lệch {d['code']} đã quá hạn xử lý {late} ngày. {dong(ln['left_vnd'])} từ {who} chưa ghi hết. Dấu hiệu chính (code tính): {e['summary']} "
                 "Hãy viết 2–3 câu báo người phụ trách: việc nên làm tiếp theo (mở hồ sơ, xác nhận thẻ đề xuất, hỏi bên trả bằng tin nháp). Không bịa số, không tự gửi tin, "
                 "không nêu nguyên nhân nào ngoài lời giải thích của hồ sơ."),
        "fallback": f"Hồ sơ {d['code']} quá hạn {late} ngày: {dong(ln['left_vnd'])} từ {who} chưa ghi hết. Bạn mở hồ sơ, xem việc đề xuất và xử lý."}
