"""SỔ CÔNG (0150 §4.1–4.2): một khe ``record_work`` cho mọi nguồn, chỉ thêm, khoá của nguồn; nhập Excel; khoản nhập tay qua duyệt."""

import hashlib
import json
import re
from datetime import date
from typing import Any, Literal
from uuid import UUID

from psycopg import errors as pg
from psycopg.types.json import Jsonb
from pydantic import Field

from barista.api import Conflict, Ctx, Day, Denied, Input, Invalid, Money, NoInput, NotFound, entities, field, money, ui
from barista.modules.pay import UNITS, m

_SOURCE = re.compile(r"[a-z_]+\.[a-z_]+")
_ROLE = re.compile(r"[a-z0-9_.]{0,60}")
UNIT_KEYS = Literal["", "session", "hour", "shift", "day", "piece"]


class RecordWork(Input):
    source: str
    source_ref: str
    key: str
    party_id: UUID | None = None
    user_id: UUID | None = None
    entity_id: UUID | None = None
    work_date: date
    kind: Literal["qty", "amount"]
    unit: UNIT_KEYS = ""
    quantity: float = 0
    amount_vnd: int | None = None
    role_key: str = ""
    component: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    reverses_key: str = ""


@m.command(
    "record_work", resource="pay", title="Ghi công vào sổ công", internal=True, agent="deny", risk="money", callers=("timekeeping",), family="work_log",
    why="KHE NỐI DUY NHẤT của nguồn công (0150 §4.1; gói ngoài lõi khai consumes(\"work_log\")): nguồn tự quyết một đơn vị công (buổi đã xác nhận, quyền hưởng đã mở) rồi gọi; chỉ ghi dòng có "
        "nguồn thuộc CHÍNH module gọi, khoá bắt đầu bằng nguồn đó; chỉ thêm, gọi lại cùng khoá phải cùng nội dung; dòng đảo chỉ đảo dòng cùng nguồn; "
        "chỉ cá nhân; trả entry_id, không đọc gì khác của pay."
        " [id: module gọi tự có — người làm của một đơn vị công gói nguồn đã xác nhận (vd người dạy của buổi đã xác nhận), người hưởng gói nguồn đã chốt (lệnh đặt người hưởng của gói kiểm bằng quyền người "
        "đặt), người có dòng bảng công timekeeping đã chốt (close_lines của kỳ chốt, người chốt khác người được chốt — DB giữ); pay tự dịch đăng nhập sang "
        "định danh bằng parties.ensure_member]",
)
async def record_work(ctx: Ctx, inp: RecordWork) -> dict[str, Any]:
    return await record(ctx, inp)


async def record(ctx: Ctx, inp: RecordWork, *, by: UUID | None = None, approved_by: tuple[UUID, ...] = ()) -> dict[str, Any]:
    """Thân của khe nối — cũng là đường của nhập Excel / khoản nhập tay (nguồn ``pay.*``)."""
    if not _SOURCE.fullmatch(inp.source) or not inp.source_ref.strip() or not inp.key.startswith(inp.source + ":"):
        raise Invalid("Nguồn / khoá công không hợp lệ (khoá phải bắt đầu bằng nguồn)")
    ctx.owned_by(inp.source.split(".")[0], "Công")
    if not _ROLE.fullmatch(inp.role_key) or (inp.component and not re.fullmatch(r"[A-Z][A-Z0-9_]{1,29}", inp.component)):
        raise Invalid("Mã vai / khoản không hợp lệ")
    if inp.kind == "qty":
        if not inp.unit or inp.amount_vnd is not None or not inp.quantity or (inp.quantity < 0 and not inp.reverses_key):
            raise Invalid("Công theo số lượng cần đơn vị và số lượng dương (số âm chỉ ở dòng đảo)")
    elif inp.unit or inp.quantity or not inp.amount_vnd:
        raise Invalid("Công theo tiền cần số tiền khác 0, không kèm đơn vị / số lượng")
    if (inp.party_id is None) == (inp.user_id is None):
        raise Invalid("Cần đúng một trong hai: định danh hoặc đăng nhập của người được trả")
    party = inp.party_id or (await ctx.call_system("parties.ensure_member", user_id=inp.user_id))["party_id"]
    who = await ctx.call_system("parties.resolve_for_payment", party_id=party)
    if who["kind"] != "person":
        raise Invalid("Sổ công chỉ cho cá nhân — tổ chức nhận tiền đi thẳng Tài chính")
    entity = inp.entity_id or await entities.default(ctx)
    if not await ctx.val("SELECT id FROM core.legal_entities WHERE id=%s AND active", (entity,)):
        raise NotFound("Không thấy pháp nhân")
    payload = json.loads(inp.model_dump_json())
    await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"pay.work:{ctx.p.tenant_id}:{inp.key}",))
    old = await ctx.one("SELECT id,payload FROM pay.work_entries WHERE key=%s", (inp.key,))
    if old:
        if old["payload"] != payload:
            raise Conflict("IDEMPOTENCY_CONFLICT: dòng công cùng khoá đã ghi với nội dung khác — sửa bằng dòng đảo + khoá mới")
        return {"entry_id": old["id"], "replayed": True}
    reverses = None
    if inp.reverses_key:
        o = await ctx.one("SELECT * FROM pay.work_entries WHERE key=%s", (inp.reverses_key,))
        if (o is None or o["source"] != inp.source or o["party_id"] != party or o["kind"] != inp.kind or o["unit"] != inp.unit
                or o["role_key"] != inp.role_key or o["component"] != inp.component
                or (inp.kind == "qty" and float(o["quantity"]) != -inp.quantity) or (inp.kind == "amount" and o["amount_vnd"] != -(inp.amount_vnd or 0))):
            raise Invalid("Dòng đảo phải đảo đúng một dòng cùng nguồn, cùng người, ngược dấu")
        reverses = o["id"]
    try:
        async with ctx.conn.transaction():
            eid = await ctx.val(
                """INSERT INTO pay.work_entries(party_id,user_id,entity_id,work_date,kind,unit,quantity,amount_vnd,component,role_key,source,source_ref,
                                                key,reverses_id,evidence,payload,created_by,approved_by)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (party, who["user_id"], entity, inp.work_date, inp.kind, inp.unit, inp.quantity, inp.amount_vnd, inp.component, inp.role_key,
                 inp.source, inp.source_ref, inp.key, reverses, Jsonb(inp.evidence), Jsonb(payload), by or ctx.p.user_id, list(approved_by)),
            )
    except pg.UniqueViolation as e:
        raise Conflict("Dòng công này đã được đảo") from e
    except pg.CheckViolation as e:   # tách bạch nhiệm vụ ở DB (trigger work_entries_self)
        raise Denied(e.diag.message_primary or "Không ghi khoản lương cho chính mình") from e
    return {"entry_id": eid, "replayed": False}


async def _person(ctx: Ctx, person: str) -> UUID:
    """Người do NGƯỜI NHẬP nêu (ô Excel / form): email thành viên, hoặc tên đúng như danh mục chọn — chỉ người họ thấy (0125 §B)."""
    text = person.strip()
    if "@" in text:
        uid = await ctx.val("SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id=u.id AND mb.tenant_id=core.tenant() "
                            "WHERE lower(u.email)=lower(%s)", (text,))
        if uid is None:
            raise NotFound(f"Không có thành viên {text}")
        party = (await ctx.call_system("parties.ensure_member", user_id=uid))["party_id"]
        seen = {o["value"] for o in await ctx.call("parties.options")}
        if str(party) not in seen:
            raise NotFound(f"Không thấy {text} trong danh mục bạn được xem")
        return party
    hits = [o["value"] for o in await ctx.call("parties.options") if o["label"].rsplit(" (", 1)[0].strip().lower() == text.lower()]
    if len(hits) != 1:
        raise Invalid(f"“{text}”: {'không thấy' if not hits else 'trùng tên'} trong danh mục — ghi email thành viên cho chắc")
    return UUID(hits[0])


def _need_payroll(ctx: Ctx) -> None:
    if not ctx.p.has_scope("pay", "all") or ctx.p.user_id is None:
        raise Denied("Cần quyền phụ trách lương cả công ty")


class ImportWork(Input):
    person: str = field("Người", max_length=200, example="lan@cong-ty-mau.demo", help="Email thành viên, hoặc tên đúng như danh mục")
    work_date: Day = field("Ngày làm", example="05/11/2026")
    unit: Literal["session", "hour", "shift", "day", "piece"] = field("Đơn vị", "piece", options=UNITS)
    quantity: float = field("Số lượng", 1, gt=0, le=10000)
    role_key: str = field("Loại việc", "", max_length=60, example="goi_dau",
                          help="Mã chữ thường không dấu, khớp với mức trả theo loại việc; trống = mức chung của đơn vị")
    ref: str = field("Mã chứng từ", max_length=120, example="PDV-0012", help="Mã phiếu / dòng ở sổ gốc — nhập lại cùng mã không ghi hai lần")
    note: str = field("Ghi chú", "", max_length=300)


@m.command("import_work",
           purpose="Ghi dòng công từ sổ gốc của công ty để kỳ lương sau tính đúng số lượng công của từng người.", irreversible=True, effect="Dòng công vào sổ công và được tính vào kỳ lương kế tiếp; sổ công chỉ thêm, không sửa hoặc xoá được.",
           resource="pay", title="Nhập công", importable=True, agent="deny", risk="money")
async def import_work(ctx: Ctx, inp: ImportWork) -> dict[str, Any]:
    """Một dòng công từ sổ gốc của công ty (bảng Excel lượt dịch vụ, ca, sản phẩm…) — nhập cả file qua Nhập dữ liệu, chạy ĐÚNG lệnh này từng dòng.
    Chỉ số lượng: tiền do mức trả quyết lúc lập kỳ."""
    _need_payroll(ctx)
    if not inp.ref.strip():
        raise Invalid("Cần mã chứng từ")
    party = await _person(ctx, inp.person)
    out = await record(ctx, RecordWork(source="pay.import", source_ref=inp.ref.strip(), key=f"pay.import:{inp.ref.strip()}", party_id=party,
                                       work_date=inp.work_date, kind="qty", unit=inp.unit, quantity=inp.quantity, role_key=inp.role_key.strip().lower(),
                                       evidence={"note": inp.note} if inp.note else {}))
    return {**out, "summary": f"Đã ghi {inp.quantity:g} {UNITS[inp.unit].lower()} ngày {inp.work_date:%d/%m/%Y} ({inp.ref.strip()})"}


class RequestEntry(Input):
    person: str = field("Người", max_length=200, example="nv.minh@cong-ty-mau.demo", help="Email thành viên, hoặc tên đúng như danh mục")
    component: str = field("Khoản", options_query="pay.manual_component_options")
    amount_vnd: Money = field("Số tiền", gt=0, example="500k")
    work_date: Day = field("Thuộc ngày", example="31/10/2026", help="Ngày của khoản — vào kỳ lương gồm ngày này")
    reason: str = field("Lý do", kind="textarea", max_length=500, example="Thưởng hoàn thành chỉ tiêu tháng 10")


@m.command("request_entry",
           purpose="Xin duyệt khoản thưởng, phạt hoặc khấu trừ một lần có lý do, để người thứ hai kiểm trước khi vào lương.", irreversible=True, effect="Gửi phiếu duyệt khoản lương nhập tay tới người duyệt; sau khi duyệt, khoản vào sổ công của kỳ lương và không xoá được.",
           resource="pay", title="Xin nhập tay khoản lương", agent="deny")
async def request_entry(ctx: Ctx, inp: RequestEntry) -> dict[str, Any]:
    """Thưởng, phạt, khấu trừ một lần: có lý do, qua chuỗi duyệt ``pay_entry`` — người xin không tự duyệt, trợ lý không xin."""
    _need_payroll(ctx)
    if not inp.reason.strip():
        raise Invalid("Cần lý do")
    party = await _person(ctx, inp.person)
    c = await ctx.one("SELECT DISTINCT ON (code) * FROM pay.components WHERE code=%s ORDER BY code,version DESC", (inp.component,))
    if not c or not c["active"] or c["calculator"] != "manual":
        raise Invalid("Khoản này không phải khoản nhập tay đang dùng")
    who = (await ctx.call_system("parties.resolve_for_payment", party_id=party))
    if who["user_id"] is not None and who["user_id"] == ctx.p.user_id:
        raise Denied("Không tự xin khoản lương cho chính mình")
    # chi tiết (người, số tiền, lý do) nằm ở bảng của Lương dưới RLS; phiếu duyệt chỉ mang mã — người đọc phiếu duyệt / nhật ký không thấy lương (0150 §8)
    rid = await ctx.val("""INSERT INTO pay.entry_requests(party_id,component,amount_vnd,work_date,reason,requested_by)
                           VALUES(%s,%s,%s,%s,%s,core.me()) RETURNING id""", (party, c["code"], inp.amount_vnd, inp.work_date, inp.reason.strip()))
    aid = await ctx.request_approval(chain="pay_entry", command="pay.record_manual", input={"request_id": str(rid)},
                                     summary=f"Khoản lương nhập tay chờ duyệt — mở Lương › Khoản nhập tay (mã {str(rid)[:8]})",
                                     on_reject="pay.reject_entry")
    await ctx.exec("UPDATE pay.entry_requests SET approval_id=%s WHERE id=%s", (aid, rid))
    return {"request_id": rid, "approval_id": aid, "summary": f"Đã gửi duyệt {c['label'].lower()} {money(inp.amount_vnd)} cho {who['display_name']}"}


class EntryRequestRef(Input):
    request_id: UUID


@m.command("record_manual", resource="pay", title="Ghi khoản lương nhập tay sau duyệt", internal=True, agent="deny", risk="money")
async def record_manual(ctx: Ctx, inp: EntryRequestRef) -> dict[str, Any]:
    """Sau duyệt: một dòng công ``pay.manual``. Người duyệt / người xin là người được trả ⇒ DB từ chối (trigger work_entries_self)."""
    if not ctx.p.approved_by:
        raise Denied("Khoản nhập tay cần phiếu đã được người khác duyệt")
    r = await ctx.one("SELECT * FROM pay.entry_requests WHERE id=%s FOR UPDATE", (inp.request_id,))
    if not r or r["status"] != "requested":
        raise Invalid("Đề nghị không còn chờ ghi")
    out = await record(ctx, RecordWork(source="pay.manual", source_ref=str(r["id"]), key=f"pay.manual:{r['id']}", party_id=r["party_id"],
                                       work_date=r["work_date"], kind="amount", amount_vnd=r["amount_vnd"], component=r["component"],
                                       evidence={"reason": r["reason"]}), by=r["requested_by"], approved_by=ctx.p.approved_by)
    await ctx.exec("UPDATE pay.entry_requests SET status='recorded',entry_id=%s WHERE id=%s", (out["entry_id"], r["id"]))
    return out


@m.command("reject_entry", resource="pay", title="Khoản nhập tay bị từ chối", internal=True, agent="deny")
async def reject_entry(ctx: Ctx, inp: EntryRequestRef) -> None:
    await ctx.exec("UPDATE pay.entry_requests SET status='rejected' WHERE id=%s AND status='requested'", (inp.request_id,))


@m.query("entry_requests", resource="pay", min_scope="all", title="Khoản nhập tay")
async def entry_requests(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Khoản thưởng / phạt / khấu trừ nhập tay và trạng thái duyệt — chỉ người có quyền Lương cả công ty (người duyệt xem chi tiết ở đây)."""
    rows = await ctx.all("SELECT id,party_id,component,amount_vnd,work_date,reason,status,created_at FROM pay.entry_requests "
                         "ORDER BY created_at DESC LIMIT 500")
    names = await ctx.call_system("parties.names", party_ids=list({r["party_id"] for r in rows})) if rows else {}
    for r in rows:
        r["name"] = names.get(str(r["party_id"]), {}).get("name", "")
        r["code"] = str(r["id"])[:8]
    return ui.table(rows, [ui.col("code", "Mã"), ui.col("name", "Người"), ui.col("component", "Khoản"), ui.col("amount_vnd", "Số tiền", "money"),
                           ui.col("work_date", "Thuộc ngày", "date"), ui.col("reason", "Lý do", width=280),
                           ui.col("status", "Trạng thái", "badge", labels={"requested": "Chờ duyệt", "recorded": "Đã ghi sổ công", "rejected": "Bị từ chối"},
                                  tones={"requested": "orange", "recorded": "green", "rejected": "gray"})],
                    title="Khoản nhập tay", limit=500, empty="Chưa có khoản nhập tay")


@m.query("manual_component_options", resource="pay.policy", title="Khoản nhập tay")
async def manual_component_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    rows = await ctx.all("SELECT DISTINCT ON (code) code,label,active,calculator FROM pay.components ORDER BY code,version DESC")
    return [{"value": r["code"], "label": f"{r['label']} ({r['code']})"} for r in rows if r["active"] and r["calculator"] == "manual"]


SOURCE_LABEL = {"pay.import": "Nhập Excel", "pay.manual": "Nhập tay (đã duyệt)"}


class EntriesIn(Input):
    open_only: bool = field("Chỉ dòng chưa vào kỳ đã chốt", True)


@m.query("entries", resource="pay", min_scope="all", title="Sổ công")
async def entries(ctx: Ctx, inp: EntriesIn) -> dict[str, Any]:
    """Sổ công cả công ty (phụ trách lương, kế toán) — dòng nào đã vào phiếu của kỳ nào. Người lao động đọc dòng của mình qua RLS, không qua màn này."""
    rows = await ctx.all(
        """SELECT e.id,e.party_id,e.work_date,e.kind,e.unit,e.quantity,e.amount_vnd,e.role_key,e.component,e.source,e.source_ref,
                  (e.reverses_id IS NOT NULL) AS is_reversal,r.status AS run_status,r.period
           FROM pay.work_entries e LEFT JOIN pay.slip_entries se ON se.entry_id=e.id LEFT JOIN pay.slips s ON s.id=se.slip_id
           LEFT JOIN pay.runs r ON r.id=s.run_id
           WHERE NOT %s OR r.status IS DISTINCT FROM 'locked' ORDER BY e.work_date DESC,e.created_at DESC LIMIT 1000""",
        (inp.open_only,),
    )
    names = await ctx.call_system("parties.names", party_ids=list({r["party_id"] for r in rows})) if rows else {}
    for r in rows:
        r["name"] = names.get(str(r["party_id"]), {}).get("name", "")
        r["what"] = (f"{float(r['quantity']):g} {str(UNITS.get(r['unit']) or r['unit']).lower()}" + (f" · {r['role_key']}" if r["role_key"] else "")
                     if r["kind"] == "qty" else (r["component"] or "tiền nguồn"))
        r["origin"] = SOURCE_LABEL.get(r["source"], r["source"].split(".")[0])
        r["state"] = {"locked": "paid", "submitted": "pending", "draft": "draft"}.get(r.pop("run_status") or "", "open")
    open_n = sum(1 for r in rows if r["state"] == "open")
    return ui.table(
        rows,
        [ui.col("work_date", "Ngày", "date"), ui.col("name", "Người"), ui.col("what", "Công"), ui.col("amount_vnd", "Tiền nguồn", "money"),
         ui.col("origin", "Nguồn"), ui.col("source_ref", "Mã nguồn"),
         ui.col("state", "Tình trạng", "badge", labels={"open": "Chưa vào kỳ", "draft": "Trong kỳ nháp", "pending": "Kỳ chờ duyệt", "paid": "Đã chốt"},
                tones={"open": "orange", "draft": "gray", "pending": "blue", "paid": "green"})],
        title="Sổ công", limit=1000, empty="Chưa có dòng công nào",
        kpis=[ui.kpi("Chưa vào kỳ", open_n, tone="orange" if open_n else None)],
        note="Sổ chỉ thêm: nguồn sửa bằng dòng đảo, không sửa dòng cũ",
    )


def digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
