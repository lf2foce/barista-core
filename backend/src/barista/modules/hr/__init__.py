"""Hồ sơ làm việc mỏng (0107): mã nhân viên theo pháp nhân, chức danh, từ–đến + lịch sử đội / người quản lý, tham chiếu định danh của danh mục.

Bổ sung bởi 0150: ĐIỀU KIỆN TRẢ theo hợp đồng (``hr.terms``) là tài nguyên RIÊNG — người được xem hồ sơ làm việc không mặc nhiên thấy; có LOẠI hợp đồng (cộng tác / có thời hạn bao nhiêu tháng…), gói Lương đọc qua ``hr.terms_on``. Mức trả ở ``pay.rates``; mã định danh mã hoá: lát 2.
Lát 3 thêm vào điều kiện trả: lương đóng bảo hiểm, vùng lương tối thiểu nơi làm việc, đoàn viên; và NGƯỜI PHỤ THUỘC đăng ký giảm trừ gia cảnh
(``hr.dependants``) — cùng tài nguyên riêng ``hr.terms``; gói Lương chỉ nhận SỐ người phụ thuộc, không tên.
0160 / 0256: ``hr.structure_on`` cho Hoa hồng đề xuất người hưởng theo cơ cấu tại ngày ký. ĐỘI và TRƯỞNG ĐỘI theo ngày nằm ở LÕI (``core.team_terms`` —
Quản trị ghi, quyền và hoa hồng cùng đọc); module này chỉ giữ cây QUẢN LÝ TRỰC TIẾP theo người (``hr.assignments``).
KHÔNG có bảng nghĩa vụ ở đây — tiền đi qua Tài chính. Hoa hồng chỉ đọc cơ cấu MỘT LẦN khi đề xuất người hưởng (0160); khoản hưởng chụp người và đội
ngay trên giao dịch (0104), nên đổi đội về sau không đổi tiền cũ.
"""

from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from barista.api import Action as A
from barista.api import (
    Ctx,
    Day,
    Denied,
    InfoGroup,
    Input,
    Invalid,
    Module,
    Money,
    NoInput,
    NotFound,
    Open,
    Pack,
    Screen,
    entities,
    field,
    money,
    today,
    ui,
)

Pack(
    "hr",
    "Hồ sơ làm việc",
    "Mã nhân viên theo pháp nhân, chức danh, lịch sử đội và người quản lý; chứng chỉ / giấy phép hành nghề có hạn và nhắc hết hạn. Không gồm lương, thuế, bảo hiểm",
    kind="capability",
    requires=("directory",),
    grants=(
        ("admin", "hr", "all", "read write manage"),
        ("office", "hr", "all", "read write manage"),
        ("director", "hr", "all", "read"),
        ("accountant", "hr", "all", "read"),
        # chứng chỉ (0202): mỗi người thấy của CHÍNH mình; trưởng đội thấy trạng thái chứng chỉ của đội (KHÔNG thấy số / bản scan — tài nguyên riêng);
        # phụ trách nhân sự / hành chính cả công ty ghi và xem đủ. Giám đốc xem trạng thái cả công ty, không xem số.
        ("admin", "hr.credentials", "all", "read write manage"), ("office", "hr.credentials", "all", "read write manage"),
        ("hr_officer", "hr.credentials", "all", "read write manage"), ("director", "hr.credentials", "all", "read"),
        ("team_lead", "hr.credentials", "team", "read"), ("staff", "hr.credentials", "own", "read"), ("sale", "hr.credentials", "own", "read"),
        ("admin", "hr.credential_private", "all", "read write manage"), ("office", "hr.credential_private", "all", "read write manage"),
        ("hr_officer", "hr.credential_private", "all", "read write manage"),
        ("team_lead", "hr.credential_private", "own", "read"), ("staff", "hr.credential_private", "own", "read"),
        ("sale", "hr.credential_private", "own", "read"),
        ("tech", "hr", "all", "read write"),   # vai Công nghệ (rà 18/11): hồ sơ làm việc / đội / quản lý để nhập và sửa cơ cấu — không hr.terms (lương)
    ),
    # 0239 phần 3: nhóm thông tin nhạy cảm của hồ sơ nhân sự
    info_groups=(InfoGroup("contract_terms", "Điều kiện hợp đồng (lương đóng bảo hiểm, người phụ thuộc)", "hr.terms",
                           "Phần điều kiện trả của hồ sơ làm việc và các màn lương đọc từ đó"),
                 InfoGroup("credential_numbers", "Số chứng chỉ hành nghề & bản scan", "hr.credential_private",
                           "Số chứng chỉ, bản scan trên hồ sơ chứng chỉ; trạng thái / hạn theo quyền Chứng chỉ")),
    rules="""- CHỨNG CHỈ NHÂN SỰ: "ai thiếu / sắp hết hạn / đã hết hạn chứng chỉ X" ⇒ hr__credential_watch (loại, khoảng ngày) — đọc tên, loại, ngày hết hạn trên
  THẺ; không có số chứng chỉ trong kết quả và trợ lý không hỏi / không nhắc lại số chứng chỉ. Ghi / thu hồi chứng chỉ chỉ là thẻ đề xuất cho người
  phụ trách nhân sự bấm. Hết hạn KHÔNG có nghĩa là bị cấm làm việc — công ty chưa đặt luật chặn nào; chỉ nói "đã hết hạn theo hồ sơ".
""",
)

m = Module("hr", "Hồ sơ làm việc", pack="hr", resources={"hr": "Hồ sơ làm việc (mã nhân viên, chức danh, đội, quản lý)",
                                                          "hr.terms": "Điều kiện trả theo hợp đồng (loại hợp đồng, lương đóng bảo hiểm, người phụ thuộc)",
                                                          "hr.credentials": "Chứng chỉ / giấy phép hành nghề của nhân sự (loại, hạn, trạng thái)",
                                                          "hr.credential_private": "Số chứng chỉ và bản scan (dữ liệu cá nhân)"})


# 0160 (review): lùi ngày quá hạn mức này phải có LÝ DO (ghi vào dòng + nhật ký thao tác) — sửa quá khứ đổi người hưởng dựng từ cơ cấu của giao dịch cũ
BACKDATE_DAYS = 7


def _backdate(valid_from: date, reason: str) -> str:
    """Lý do lùi ngày ('' = không lùi quá hạn mức). Lùi quá hạn mức mà không lý do ⇒ từ chối."""
    if valid_from >= today() - timedelta(days=BACKDATE_DAYS):
        return ""
    if not reason.strip():
        raise Invalid(f"Lùi ngày hiệu lực quá {BACKDATE_DAYS} ngày (từ {valid_from:%d/%m/%Y}) cần ghi lý do — lịch sử cơ cấu ảnh hưởng người hưởng hoa hồng đã dựng từ nó")
    return reason.strip()


async def _lock(ctx: Ctx, party: UUID) -> None:
    await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"hr:{ctx.p.tenant_id}:{party}",))


async def _names(ctx: Ctx, ids: list[UUID]) -> dict[str, dict[str, str]]:
    """Tên theo dòng hồ sơ làm việc đang hiện (id đã nằm trong dữ liệu của module): đọc bằng hợp đồng hẹp của danh mục."""
    return await ctx.call_system("parties.names", party_ids=ids) if ids else {}


async def _seen(ctx: Ctx, ids: list[UUID]) -> dict[str, dict[str, str]]:
    """Người do NGƯỜI GỌI chọn (ô nhập): chỉ nhận người họ thấy trong danh mục, bằng quyền của họ — không dùng quyền hệ thống (0125 §B)."""
    return await ctx.call_as("parties.names", ctx.p, party_ids=ids)


async def _need_party(ctx: Ctx, party: UUID) -> None:
    if not ctx.p.has_scope("hr", "all"):
        raise Denied("Cần phạm vi cả công ty")
    if str(party) not in await _seen(ctx, [party]):
        raise NotFound("Không thấy cá nhân")


class SetEmployment(Input):
    party_id: UUID = field("Người", kind="select", options_query="parties.options")
    title: str = field("Chức danh", "", max_length=120, example="Trưởng phòng kinh doanh")
    started_on: Day | None = field("Từ ngày", None, example="01/11/2026", placeholder="Hôm nay", help="Đổi chức danh ⇒ nhập từ ngày mới, dòng cũ tự đóng")
    employee_code: str = field("Mã nhân viên", "", max_length=40, example="NV0012", group="Thêm chi tiết", placeholder="Máy đặt số kế tiếp",
                               help="Để trống thì máy đặt mã kế tiếp theo kiểu mã đang dùng (vd NV0013)")
    entity_code: str = field("Pháp nhân", "", options_query="admin.entity_options", placeholder="Chưa xác định pháp nhân", group="Thêm chi tiết")


async def _next_employee_code(ctx: Ctx, entity: Any) -> str:
    """Mã nhân viên kế tiếp theo kiểu mã công ty đang dùng (tiền tố + số, vd NV0012 ⇒ NV0013); chưa có mã nào ⇒ NV0001 (0225: không bắt nghĩ ra mã)."""
    import re
    from collections import Counter

    have = [r["employee_code"] for r in await ctx.all("SELECT employee_code FROM hr.employments")]
    parts = [m for c in have if (m := re.fullmatch(r"(\D*)(\d+)", c))]
    prefix = Counter(m.group(1) for m in parts).most_common(1)[0][0] if parts else "NV"
    same = [m for m in parts if m.group(1) == prefix]
    width = max((len(m.group(2)) for m in same), default=4)
    n = max((int(m.group(2)) for m in same), default=0) + 1
    taken = {c for c in have}
    while f"{prefix}{n:0{width}d}" in taken:
        n += 1
    return f"{prefix}{n:0{width}d}"


@m.command("set_employment", resource="hr", action="manage", title="Ghi hồ sơ làm việc", agent="propose")
async def set_employment(ctx: Ctx, inp: SetEmployment) -> dict[str, Any]:
    await _need_party(ctx, inp.party_id)
    entity = await entities.resolve(ctx, inp.entity_code) if inp.entity_code else None
    await _lock(ctx, inp.party_id)
    started_on = inp.started_on or today()
    code = inp.employee_code.strip()
    if not code:   # người đã có hồ sơ đang mở: giữ mã cũ (đổi chức danh không đổi mã); người mới: mã kế tiếp
        code = await ctx.val("SELECT employee_code FROM hr.employments WHERE party_id=%s AND entity_id IS NOT DISTINCT FROM %s AND ended_on IS NULL",
                             (inp.party_id, entity)) or await _next_employee_code(ctx, entity)
    # Mã trùng người khác: báo rõ tại đây; trigger giữ lần cuối nếu có đường ghi khác.
    if await ctx.val(
        "SELECT 1 FROM hr.employments WHERE party_id<>%s AND employee_code=%s AND entity_id IS NOT DISTINCT FROM %s",
        (inp.party_id, code, entity),
    ):
        raise Invalid(f"Mã {code} đã thuộc người khác trong pháp nhân này")
    current = await ctx.one(
        "SELECT id,started_on FROM hr.employments WHERE party_id=%s AND entity_id IS NOT DISTINCT FROM %s AND ended_on IS NULL",
        (inp.party_id, entity),
    )
    if current:
        if started_on <= current["started_on"]:
            raise Invalid(f"Đang có hồ sơ từ {current['started_on']:%d/%m/%Y}: ngày mới phải sau ngày đó")
        await ctx.exec("UPDATE hr.employments SET ended_on=%s WHERE id=%s", (started_on - timedelta(days=1), current["id"]))
    row = await ctx.val(
        """INSERT INTO hr.employments(party_id,entity_id,employee_code,title,started_on,created_by)
           VALUES(%s,%s,%s,%s,%s,core.me()) RETURNING id""",
        (inp.party_id, entity, code, inp.title, started_on),
    )
    return {"employment_id": row}


class EndEmployment(Input):
    employment_id: UUID = field("Hồ sơ", hidden=True)
    ended_on: Day | None = field("Ngày nghỉ", None, example="30/11/2026", placeholder="Hôm nay")


@m.command("end_employment", resource="hr", action="manage", title="Ghi ngày nghỉ", agent="propose")
async def end_employment(ctx: Ctx, inp: EndEmployment) -> dict[str, Any]:
    if not ctx.p.has_scope("hr", "all"):
        raise Denied("Cần phạm vi cả công ty")
    ended_on = inp.ended_on or today()
    row = await ctx.one("SELECT party_id,started_on,ended_on FROM hr.employments WHERE id=%s", (inp.employment_id,))
    if not row:
        raise NotFound("Không thấy hồ sơ")
    if row["ended_on"] is not None:
        raise Invalid("Hồ sơ này đã đóng")
    if ended_on < row["started_on"]:
        raise Invalid("Ngày nghỉ trước ngày bắt đầu")
    await _lock(ctx, row["party_id"])
    await ctx.exec("UPDATE hr.employments SET ended_on=%s WHERE id=%s", (ended_on, inp.employment_id))
    await ctx.exec("UPDATE hr.assignments SET valid_to=%s, edited_by=core.me(), edited_at=now() WHERE party_id=%s AND valid_to IS NULL AND valid_from<=%s",
                   (ended_on, row["party_id"], ended_on))
    await credentials.resync(ctx, [row["party_id"]])
    return {"employment_id": inp.employment_id}


class SetAssignment(Input):
    party_id: UUID = field("Người", kind="select", options_query="parties.options")
    manager_party_id: UUID | None = field("Người quản lý trực tiếp", None, kind="select", options_query="parties.options", placeholder="Không có quản lý trực tiếp")
    valid_from: Day | None = field("Từ ngày", None, example="01/11/2026", placeholder="Hôm nay", help="Phân công trước đó tự đóng vào ngày liền trước")
    reason: str = field("Lý do lùi ngày", "", max_length=300, help=f"Bắt buộc khi lùi quá {BACKDATE_DAYS} ngày so với hôm nay (ghi vào nhật ký)", group="Thêm chi tiết")


@m.command("set_assignment", resource="hr", action="manage", title="Ghi người quản lý trực tiếp", agent="propose")
async def set_assignment(ctx: Ctx, inp: SetAssignment) -> dict[str, Any]:
    """Cây quản lý theo NGƯỜI và cây đội là hai thứ: không suy cái này từ cái kia. Đội của một người ghi ở Quản trị (lõi — 0256), không ở đây."""
    await _need_party(ctx, inp.party_id)
    if inp.manager_party_id == inp.party_id:
        raise Invalid("Người quản lý không thể là chính người này")
    if inp.manager_party_id and str(inp.manager_party_id) not in await _seen(ctx, [inp.manager_party_id]):
        raise NotFound("Không thấy người quản lý")
    valid_from = inp.valid_from or today()
    why = _backdate(valid_from, inp.reason)
    await _lock(ctx, inp.party_id)
    current = await ctx.one("SELECT id,valid_from,manager_party_id FROM hr.assignments WHERE party_id=%s AND valid_to IS NULL", (inp.party_id,))
    if current:
        if valid_from <= current["valid_from"]:
            raise Invalid(f"Đang có phân công từ {current['valid_from']:%d/%m/%Y}: ngày mới phải sau ngày đó")
        await ctx.exec("UPDATE hr.assignments SET valid_to=%s, edited_by=core.me(), edited_at=now() WHERE id=%s", (valid_from - timedelta(days=1), current["id"]))
    elif inp.manager_party_id is None:
        raise Invalid("Người này đang không có quản lý trực tiếp")
    row = None
    if inp.manager_party_id:
        row = await ctx.val(
            """INSERT INTO hr.assignments(party_id,manager_party_id,valid_from,created_by,edited_by,backdate_reason)
               VALUES(%s,%s,%s,core.me(),core.me(),%s) RETURNING id""",
            (inp.party_id, inp.manager_party_id, valid_from, why),
        )
    return {"assignment_id": row}


# ── Đội / trưởng đội tại một ngày: đọc LÕI (``core.team_terms``, khoảng nửa mở [valid_from, valid_to) — 0256) ──

_HOME_ON = """SELECT DISTINCT ON (tt.user_id) tt.user_id, tt.team_id, t.name AS team, tt.home_by AS editor, tt.home_at AS edited_at,
                     tt.edited_by AS touched_by, tt.edited_at AS touched_at
              FROM core.team_terms tt JOIN core.teams t ON t.id = tt.team_id
              WHERE tt.user_id = ANY(%(u)s) AND tt.is_home AND tt.valid_from <= %(d)s AND (tt.valid_to IS NULL OR tt.valid_to > %(d)s)
              ORDER BY tt.user_id, tt.valid_from DESC"""
_LEAD_ON = """SELECT DISTINCT ON (tt.team_id) tt.team_id, tt.user_id, tt.head_role, tt.lead_by AS editor, tt.lead_at AS edited_at,
                     tt.edited_by AS touched_by, tt.edited_at AS touched_at
              FROM core.team_terms tt
              WHERE tt.team_id = ANY(%(t)s) AND tt.is_lead AND tt.valid_from <= %(d)s AND (tt.valid_to IS NULL OR tt.valid_to > %(d)s)
              ORDER BY tt.team_id, tt.valid_from DESC"""
# cấp cha của đội tại ngày đó; ngày trước khi có nhật ký ⇒ dòng sớm nhất (cây lúc bắt đầu ghi — không có căn cứ nào khác)
_PARENT_ON = """SELECT parent_id, edited_by AS editor, edited_at FROM core.team_parent_log
                WHERE team_id = %(t)s AND (valid_to IS NULL OR valid_to > %(d)s) ORDER BY valid_from LIMIT 1"""


async def _logins(ctx: Ctx, parties: list[UUID]) -> dict[str, UUID]:
    """party_id → tài khoản (người không có tài khoản không thuộc đội nào của lõi)."""
    got = await ctx.call_system("parties.logins", party_ids=parties) if parties else {}
    return {k: UUID(v) for k, v in got.items()}


class StaffOn(Input):
    on: Day | None = field("Tại ngày", None, placeholder="Hôm nay", help="Xem được người, chức danh, đội, quản lý tại một ngày đã qua")


@m.query("staff", resource="hr", min_scope="all", title="Hồ sơ làm việc")
async def staff(ctx: Ctx, inp: StaffOn) -> dict[str, Any]:
    day: date = inp.on or today()
    rows = await ctx.all(
        """SELECT e.id,e.party_id,e.employee_code,e.title,e.entity_id,e.started_on,e.ended_on,
                  a.manager_party_id,(e.ended_on IS NULL) AS is_open
           FROM hr.employments e
           LEFT JOIN hr.assignments a ON a.tenant_id=e.tenant_id AND a.party_id=e.party_id
                AND a.valid_from<=%(d)s AND (a.valid_to IS NULL OR a.valid_to>=%(d)s)
           WHERE e.started_on<=%(d)s AND (e.ended_on IS NULL OR e.ended_on>=%(d)s) ORDER BY e.employee_code LIMIT 2000""",
        {"d": day},
    )
    names = await _names(ctx, list({r["party_id"] for r in rows} | {r["manager_party_id"] for r in rows if r["manager_party_id"]}))
    ents = await entities.names(ctx)
    users = await _logins(ctx, [r["party_id"] for r in rows])
    homes = {r["user_id"]: r["team"] for r in await ctx.all(_HOME_ON, {"u": list(users.values()), "d": day})} if users else {}
    for r in rows:
        r["team"] = homes.get(users.get(str(r["party_id"])), "")
        r["name"] = names.get(str(r["party_id"]), {}).get("name", "")
        r["manager"] = names.get(str(r["manager_party_id"]), {}).get("name", "") if r["manager_party_id"] else ""
        r["entity"] = ents.get(str(r["entity_id"]), {}).get("name", "") if r["entity_id"] else ""
        r["open"] = "yes" if r.pop("is_open") else "no"
    return ui.table(
        rows,
        [
            ui.col("employee_code", "Mã NV"),
            ui.col("name", "Họ tên"),
            ui.col("title", "Chức danh"),
            ui.col("team", "Đội"),
            ui.col("manager", "Quản lý trực tiếp"),
            ui.col("entity", "Pháp nhân"),
            ui.col("started_on", "Từ ngày", "date"),
            ui.col("ended_on", "Đến ngày", "date"),
        ],
        title=f"Hồ sơ làm việc tại {day:%d/%m/%Y}",
        limit=2000,
        empty="Chưa có hồ sơ",
        kpis=[ui.kpi("Đang làm tại ngày này", len(rows)), ui.kpi("Chưa có quản lý hoặc đội", sum(1 for r in rows if not r["manager"] or not r["team"]),
                                                                  hint="Thiếu người quản lý trực tiếp (ghi ở đây) hoặc thiếu đội (ghi ở Quản trị › Người dùng)")],
    )


class PartyRef(Input):
    party_id: UUID = field("Người", hidden=True)


@m.query("history", resource="hr", min_scope="all", title="Lịch sử làm việc của một người")
async def history(ctx: Ctx, inp: PartyRef) -> dict[str, Any]:
    jobs = await ctx.all("SELECT id,employee_code,title,started_on,ended_on FROM hr.employments WHERE party_id=%s ORDER BY started_on DESC", (inp.party_id,))
    moves = await ctx.all("SELECT id,manager_party_id,valid_from,valid_to FROM hr.assignments WHERE party_id=%s ORDER BY valid_from DESC", (inp.party_id,))
    user = (await _logins(ctx, [inp.party_id])).get(str(inp.party_id))
    teams = await ctx.all(
        """SELECT t.name AS team, tt.is_lead, tt.is_home, tt.valid_from, tt.valid_to - 1 AS valid_to, tt.since_unknown
           FROM core.team_terms tt JOIN core.teams t ON t.id = tt.team_id WHERE tt.user_id = %s ORDER BY tt.valid_from DESC, t.name""", (user,)) if user else []
    for r in teams:
        r["as"] = ("Trưởng đội" if r["is_lead"] else "Thành viên") + ("" if r["is_home"] else " (đội tham gia thêm)")
        r["from_note"] = "Trước ngày này: chưa ghi" if r.pop("since_unknown") else ""
    names = await _names(ctx, list({inp.party_id} | {r["manager_party_id"] for r in moves if r["manager_party_id"]}))
    for r in moves:
        r["manager"] = names.get(str(r["manager_party_id"]), {}).get("name", "") if r["manager_party_id"] else ""
    return ui.card(
        names.get(str(inp.party_id), {}).get("name", "Lịch sử làm việc"),
        [
            ui.b_table(ui.table(jobs, [ui.col("employee_code", "Mã NV"), ui.col("title", "Chức danh"), ui.col("started_on", "Từ ngày", "date"),
                                       ui.col("ended_on", "Đến ngày", "date")], title="Hồ sơ", empty="Chưa có hồ sơ")),
            ui.b_table(ui.table(teams, [ui.col("team", "Đội"), ui.col("as", "Vai trò trong đội"), ui.col("valid_from", "Từ ngày", "date"),
                                        ui.col("valid_to", "Đến ngày", "date"), ui.col("from_note", "Ghi chú")], title="Đội",
                                empty="Chưa thuộc đội nào — ghi ở Quản trị › Người dùng")),
            ui.b_table(ui.table(moves, [ui.col("manager", "Quản lý trực tiếp"), ui.col("valid_from", "Từ ngày", "date"),
                                        ui.col("valid_to", "Đến ngày", "date")], title="Quản lý trực tiếp", empty="Chưa ghi người quản lý")),
        ],
        icon="person.text.rectangle",
    )


# ── Cơ cấu tại một ngày (0160; đội + trưởng đội ở lõi — 0256) ──

_ASSIGNMENT_ON = """SELECT manager_party_id,coalesce(edited_by,created_by) AS editor,edited_at FROM hr.assignments
                    WHERE party_id=%s AND valid_from<=%s AND (valid_to IS NULL OR valid_to>=%s) ORDER BY valid_from DESC LIMIT 1"""


@m.query("org", resource="hr", min_scope="all", title="Cơ cấu đội theo ngày")
async def org(ctx: Ctx, inp: StaffOn) -> dict[str, Any]:
    """Cây đội kèm trưởng đội TẠI MỘT NGÀY (khuyết hiện rõ) — chính cơ cấu mà mẫu chia hoa hồng theo vai cơ cấu đọc tại ngày chốt."""
    day: date = inp.on or today()
    rows = await ctx.all("""SELECT t.id,t.code,t.name,t.kind,p.code AS parent FROM core.teams t LEFT JOIN core.teams p ON p.id=t.parent_id
                            ORDER BY t.code LIMIT 2000""")
    heads = {str(r["team_id"]): r["user_id"] for r in await ctx.all(_LEAD_ON, {"t": [r["id"] for r in rows], "d": day})} if rows else {}
    names = {r["id"]: r["name"] for r in await ctx.all("SELECT id, name FROM core.users WHERE id = ANY(%s)", (list(set(heads.values())),))} if heads else {}
    for r in rows:
        h = heads.get(str(r["id"]))
        r["head"] = names.get(h, "") if h else ""
        r["vacant"] = "no" if h else "yes"
    return ui.table(rows, [ui.col("code", "Mã đội"), ui.col("name", "Đội"), ui.col("kind", "Loại"), ui.col("parent", "Thuộc"),
                           ui.col("head", "Trưởng đội"), ui.col("vacant", "Khuyết", "badge", labels={"yes": "Khuyết", "no": "—"}, tones={"yes": "orange"})],
                    title=f"Cơ cấu đội tại {day:%d/%m/%Y}", limit=2000, empty="Chưa có đội",
                    kpis=[ui.kpi("Đội khuyết trưởng", sum(1 for r in rows if r["vacant"] == "yes"))])


@m.titles(resource="hr")
async def current_titles(ctx: Ctx, inp: NoInput) -> dict[str, str]:
    """Chức danh đang hiệu lực hôm nay của người có tài khoản — NHÃN ở màn chọn vai (lõi gọi, ``Module.titles``). Nhiều pháp nhân ⇒ hồ sơ bắt đầu sau cùng."""
    day = today()
    rows = await ctx.all("""SELECT DISTINCT ON (party_id) party_id,title FROM hr.employments
                            WHERE title<>'' AND started_on<=%s AND (ended_on IS NULL OR ended_on>=%s) ORDER BY party_id,started_on DESC""", (day, day))
    users = await ctx.call_system("parties.logins", party_ids=[r["party_id"] for r in rows]) if rows else {}
    return {users[str(r["party_id"])]: r["title"] for r in rows if str(r["party_id"]) in users}


class StructureOn(Input):
    party_id: UUID
    on: date
    team_id: UUID | None = None    # đội bên gọi đã ghi lúc đó: dùng khi người không có phân công phủ ngày đó (vd người ngoài công ty)
    depth: int = Field(5, ge=1, le=10)


@m.query("structure_on", resource="hr", title="Cơ cấu quanh một người tại một ngày", internal=True, agent="deny", family="structure",
         why="Gói khai consumes(\"structure\") đề xuất người hưởng theo mẫu chia có vai cơ cấu (0160): đội + chuỗi người quản lý trực tiếp của người đứng doanh số TẠI NGÀY KÝ, "
             "cây đội từ đội đó lên gốc kèm trưởng đội tại ngày đó (trống = khuyết). Chỉ mã người, mã / tên / loại đội — không chức danh, không điều "
             "kiện trả, không hồ sơ; kèm mã người SỬA các dòng cơ cấu đã dùng (để người đó không tự xác nhận). [id: module gọi tự có — party_id là người đứng doanh số ghi trên khoản đang chia của gói gọi, "
             "không do người dùng nêu]")
async def structure_on(ctx: Ctx, inp: StructureOn) -> dict[str, Any]:
    first = await ctx.one(_ASSIGNMENT_ON, (inp.party_id, inp.on, inp.on))
    facts: list[dict[str, Any]] = [first] if first else []   # mọi dòng cơ cấu đã dùng — người sửa chúng không tự xác nhận (review 11/11/2026)
    user = (await _logins(ctx, [inp.party_id])).get(str(inp.party_id))
    home = (await ctx.all(_HOME_ON, {"u": [user], "d": inp.on}) if user else [])
    if home:
        facts.append(home[0])
    team, source = (home[0]["team_id"], "assignment") if home else (inp.team_id, "deal" if inp.team_id else "")
    managers: list[str] = []
    seen, cur, row = {inp.party_id}, inp.party_id, first
    while row and row["manager_party_id"] and row["manager_party_id"] not in seen and len(managers) < inp.depth:
        cur = row["manager_party_id"]
        seen.add(cur)
        managers.append(str(cur))
        row = await ctx.one(_ASSIGNMENT_ON, (cur, inp.on, inp.on))
        if row:
            facts.append(row)
    chain: list[dict[str, Any]] = []
    walked: set[UUID] = set()
    while team and team not in walked and len(chain) < 20:   # cây đội TẠI NGÀY ĐÓ (nhật ký cấp cha — 0256)
        walked.add(team)
        t = await ctx.one("SELECT id,code,name,kind FROM core.teams WHERE id=%s", (team,))
        if t is None:
            break
        chain.append({"team_id": str(t["id"]), "code": t["code"], "name": t["name"], "kind": t["kind"], "head_party_id": None, "head_role": None})
        up = await ctx.one(_PARENT_ON, {"t": team, "d": inp.on})
        if up:
            facts.append(up)
        team = up["parent_id"] if up else None
    if chain:
        heads = {str(r["team_id"]): r for r in await ctx.all(_LEAD_ON, {"t": [UUID(c["team_id"]) for c in chain], "d": inp.on})}
        for c in chain:
            h = heads.get(c["team_id"])
            if h:   # trưởng đội là tài khoản; bên gọi cần mã cá nhân trong danh mục (người hưởng)
                c["head_party_id"] = str((await ctx.call_system("parties.ensure_member", user_id=h["user_id"]))["party_id"])
                c["head_role"] = h["head_role"]   # vai công ty của trưởng lúc nhận chức (chụp trên dòng); None = chưa ghi
                facts.append(h)
    # dòng của lõi mang người sửa THEO CĂN CỨ (thành viên / trưởng — base/113) + người chạm dòng lần cuối (sửa lùi ngày, đóng dòng): tính cả hai
    facts += [{"editor": f["touched_by"], "edited_at": f["touched_at"]} for f in facts if f.get("touched_by")]
    stamped = [f for f in facts if f["editor"]]
    return {"team_id": chain[0]["team_id"] if chain else None, "team_source": source if chain else "", "managers": managers, "teams": chain,
            "editors": sorted({str(f["editor"]) for f in stamped}),
            "last_editor": str(max(stamped, key=lambda f: f["edited_at"])["editor"]) if stamped else None}


# ── Điều kiện trả theo hợp đồng (0150 §5, lát 1: chỉ loại hợp đồng) ──

CONTRACT_KIND = {"service": "Cộng tác / dịch vụ (không hợp đồng lao động)", "fixed_term": "Hợp đồng lao động có thời hạn",
                 "indefinite": "Hợp đồng lao động không thời hạn", "probation": "Thử việc"}


REGIONS = {"I": "Vùng I", "II": "Vùng II", "III": "Vùng III", "IV": "Vùng IV"}


class SetTerms(Input):
    party_id: UUID = field("Người", kind="select", options_query="parties.options")
    contract_kind: Literal["service", "fixed_term", "indefinite", "probation"] = field("Loại hợp đồng", options=CONTRACT_KIND)
    term_months: int | None = field("Thời hạn (tháng)", None, ge=1, le=36, help="Chỉ với hợp đồng có thời hạn")
    insured_vnd: Money | None = field("Lương đóng bảo hiểm / tháng", None, gt=0, example="12tr",
                                      help="Hợp đồng lao động: tiền lương làm căn cứ đóng BHXH, BHYT, BHTN (theo hợp đồng). Cộng tác: để trống")
    work_region: Literal["I", "II", "III", "IV"] | None = field("Vùng nơi làm việc", None, options=REGIONS,
                                                                 help="Vùng lương tối thiểu theo địa bàn nơi làm việc — sàn lương, trần BHTN")
    union_member: bool = field("Đoàn viên công đoàn", False, help="Đoàn viên đóng đoàn phí trên phiếu lương")
    effective_from: Day = field("Từ ngày", example="01/11/2026", help="Đổi điều kiện ⇒ thêm dòng mới từ ngày mới; dòng cũ giữ làm lịch sử")
    note: str = field("Ghi chú", "", max_length=500, example="Theo hợp đồng dịch vụ số 12/2026")


async def _payee(ctx: Ctx, party_id: UUID) -> dict[str, Any]:
    """Người do NGƯỜI GHI chọn (đã kiểm thấy được bằng quyền của họ) — cá nhân, không phải chính người ghi."""
    if not ctx.p.has_scope("hr.terms", "all"):
        raise Denied("Cần phạm vi cả công ty")
    if str(party_id) not in await _seen(ctx, [party_id]):
        raise NotFound("Không thấy cá nhân")
    party = await ctx.call_system("parties.resolve_for_payment", party_id=party_id)
    if party["kind"] != "person":
        raise Invalid("Điều kiện trả chỉ ghi cho cá nhân")
    if party["user_id"] is not None and party["user_id"] == ctx.p.user_id:
        raise Denied("Không tự ghi điều kiện trả của chính mình — nhờ người phụ trách khác")
    return party


@m.command("set_terms",
           purpose="Ghi loại hợp đồng, lương đóng bảo hiểm và vùng của người lao động, để thuế và bảo hiểm trong phiếu lương tính đúng từ ngày hiệu lực.", reversible="Ghi điều kiện mới với ngày hiệu lực khác (điều kiện chỉ thêm, bản cũ giữ trong lịch sử)",
           resource="hr.terms", action="manage", title="Ghi điều kiện trả theo hợp đồng", agent="deny", risk="money")
async def set_terms(ctx: Ctx, inp: SetTerms) -> dict[str, Any]:
    """Loại hợp đồng quyết người đó bị khấu trừ thuế theo lần chi hay luỹ tiến, có đóng bảo hiểm không; lương đóng bảo hiểm và vùng quyết số bảo hiểm
    (0150 §6) ⇒ trợ lý không ghi; người ghi không ghi cho chính mình."""
    if (inp.contract_kind == "fixed_term") != (inp.term_months is not None):
        raise Invalid("Hợp đồng có thời hạn thì ghi số tháng; loại khác để trống")
    if inp.contract_kind == "service" and (inp.insured_vnd is not None or inp.union_member):
        raise Invalid("Cộng tác / dịch vụ không đóng bảo hiểm bắt buộc theo hợp đồng này — để trống lương đóng bảo hiểm")
    party = await _payee(ctx, inp.party_id)
    await _lock(ctx, inp.party_id)
    if await ctx.val("SELECT 1 FROM hr.terms WHERE party_id=%s AND effective_from=%s", (inp.party_id, inp.effective_from)):
        raise Invalid(f"Đã có điều kiện từ {inp.effective_from:%d/%m/%Y} — chọn ngày khác")
    tid = await ctx.val(
        """INSERT INTO hr.terms(party_id,user_id,contract_kind,term_months,insured_vnd,work_region,union_member,effective_from,note,created_by)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,core.me()) RETURNING id""",
        (inp.party_id, party["user_id"], inp.contract_kind, inp.term_months, inp.insured_vnd, inp.work_region, inp.union_member,
         inp.effective_from, inp.note),
    )
    extra = (f", đóng bảo hiểm trên {money(inp.insured_vnd)}" if inp.insured_vnd else "") + (f", {REGIONS[inp.work_region].lower()}" if inp.work_region else "")
    return {"terms_id": tid, "summary": f"{party['display_name']}: {CONTRACT_KIND[inp.contract_kind].lower()}{extra} từ {inp.effective_from:%d/%m/%Y}"}


_TERMS_ON = """SELECT DISTINCT ON (party_id) party_id,contract_kind,term_months,insured_vnd,work_region,union_member,effective_from FROM hr.terms
               WHERE party_id=ANY(%s) AND effective_from<=%s ORDER BY party_id,effective_from DESC"""
# người phụ thuộc tính cho THÁNG chứa ngày hỏi: đăng ký từ trước cuối tháng, chưa thôi trước đầu tháng (giảm trừ tính theo tháng — 0150 §6)
_DEPENDANTS_ON = """SELECT party_id,count(*)::int AS n FROM hr.dependants WHERE party_id=ANY(%(p)s)
                    AND registered_from <= (date_trunc('month', %(d)s::date) + interval '1 month - 1 day')::date
                    AND (registered_to IS NULL OR registered_to >= date_trunc('month', %(d)s::date)::date) GROUP BY party_id"""


@m.query("terms", resource="hr.terms", title="Điều kiện trả theo hợp đồng")
async def terms(ctx: Ctx, inp: StaffOn) -> dict[str, Any]:
    day: date = inp.on or today()
    rows = await ctx.all("""SELECT DISTINCT ON (party_id) id,party_id,contract_kind,term_months,insured_vnd,work_region,union_member,effective_from,note
                            FROM hr.terms WHERE effective_from<=%s ORDER BY party_id,effective_from DESC LIMIT 2000""", (day,))
    names = await _names(ctx, list({r["party_id"] for r in rows}))
    deps = {str(r["party_id"]): r["n"] for r in await ctx.all(_DEPENDANTS_ON, {"p": [r["party_id"] for r in rows], "d": day})} if rows else {}
    for r in rows:
        r["name"] = names.get(str(r["party_id"]), {}).get("name", "")
        r["dependants"] = deps.get(str(r["party_id"]), 0)
        r["union"] = "yes" if r.pop("union_member") else "no"
    rows.sort(key=lambda r: r["name"])
    return ui.table(rows, [ui.col("name", "Người"), ui.col("contract_kind", "Loại hợp đồng", "badge", labels=CONTRACT_KIND,
                                                           tones={"service": "orange", "fixed_term": "blue", "indefinite": "green", "probation": "gray"}),
                           ui.col("term_months", "Số tháng", "number"), ui.col("insured_vnd", "Lương đóng bảo hiểm", "money"),
                           ui.col("work_region", "Vùng", "badge", labels=REGIONS), ui.col("union", "Đoàn viên", "badge", labels={"yes": "Có", "no": "—"}),
                           ui.col("dependants", "Người phụ thuộc", "number"), ui.col("effective_from", "Từ ngày", "date"), ui.col("note", "Ghi chú")],
                    title=f"Điều kiện trả tại {day:%d/%m/%Y}", limit=2000, empty="Chưa ghi điều kiện trả cho ai")


class TermsOn(Input):
    party_ids: list[UUID]
    on: date


@m.query("terms_on", resource="hr.terms", title="Điều kiện trả tại một ngày", internal=True, agent="deny", callers=("pay",),
         why="pay lập phiếu lương: điều kiện hiệu lực ngày chi của đúng các người có trong kỳ — loại hợp đồng + số tháng (khấu trừ theo lần chi hay "
             "luỹ tiến, có đóng bảo hiểm), lương đóng bảo hiểm, vùng, đoàn viên, SỐ người phụ thuộc đăng ký trong tháng chi (giảm trừ gia cảnh); "
             "không tên người phụ thuộc, không ghi chú, không lịch sử."
             " [id: module gọi tự có — party_ids là người có dòng trong sổ công của pay (nguồn ghi bằng quyền của nguồn)]")
async def terms_on(ctx: Ctx, inp: TermsOn) -> dict[str, dict[str, Any]]:
    deps = {str(r["party_id"]): r["n"] for r in await ctx.all(_DEPENDANTS_ON, {"p": inp.party_ids, "d": inp.on})}
    return {str(r["party_id"]): {"contract_kind": r["contract_kind"], "term_months": r["term_months"], "insured_vnd": r["insured_vnd"],
                                 "work_region": r["work_region"], "union_member": r["union_member"], "dependants": deps.get(str(r["party_id"]), 0),
                                 "effective_from": r["effective_from"].isoformat()}
            for r in await ctx.all(_TERMS_ON, (inp.party_ids, inp.on))}


RELATION = {"child": "Con", "spouse": "Vợ / chồng", "parent": "Cha / mẹ", "other": "Người khác đang nuôi dưỡng"}


class AddDependant(Input):
    party_id: UUID = field("Người lao động", kind="select", options_query="parties.options")
    name: str = field("Họ tên người phụ thuộc", max_length=120, example="Nguyễn Văn An")
    relation: Literal["child", "spouse", "parent", "other"] = field("Quan hệ", "child", options=RELATION)
    registered_from: Day = field("Giảm trừ từ tháng", example="01/01/2026", help="Ngày bất kỳ trong tháng bắt đầu được giảm trừ")
    note: str = field("Ghi chú", "", max_length=300, example="Đã nộp tờ khai đăng ký người phụ thuộc")


@m.command("add_dependant",
           purpose="Đăng ký người phụ thuộc theo hồ sơ đã nộp, để thuế thu nhập cá nhân của người lao động được giảm trừ đúng.", reversible="Bấm Thôi đăng ký người phụ thuộc (kỳ đã giảm trừ giữ nguyên)",
           resource="hr.terms", action="manage", title="Đăng ký người phụ thuộc", agent="deny", risk="money")
async def add_dependant(ctx: Ctx, inp: AddDependant) -> dict[str, Any]:
    """Người phụ thuộc giảm trừ thuế TNCN (0150 §5) — đổi số thuế ⇒ trợ lý không ghi, người ghi không ghi cho chính mình."""
    party = await _payee(ctx, inp.party_id)
    if not inp.name.strip():
        raise Invalid("Cần họ tên người phụ thuộc")
    if await ctx.val("""SELECT 1 FROM hr.dependants WHERE party_id=%s AND lower(btrim(name))=lower(%s) AND relation=%s AND registered_to IS NULL""",
                     (inp.party_id, inp.name.strip(), inp.relation)):
        raise Invalid("Người phụ thuộc này đã đăng ký và còn hiệu lực — không đăng ký hai lần (giảm trừ hai lần)")
    did = await ctx.val("""INSERT INTO hr.dependants(party_id,user_id,name,relation,registered_from,note,created_by)
                           VALUES(%s,%s,%s,%s,%s,%s,core.me()) RETURNING id""",
                        (inp.party_id, party["user_id"], inp.name.strip(), inp.relation, inp.registered_from.replace(day=1), inp.note))
    return {"dependant_id": did, "summary": f"{party['display_name']}: thêm người phụ thuộc ({RELATION[inp.relation].lower()}) từ {inp.registered_from:%m/%Y}"}


class EndDependant(Input):
    dependant_id: UUID = field("Người phụ thuộc", hidden=True)
    registered_to: Day = field("Thôi giảm trừ từ sau ngày", example="31/12/2026")


@m.command("end_dependant",
           purpose="Người phụ thuộc hết điều kiện giảm trừ, nên thôi tính giảm trừ từ ngày chọn để thuế không bị giảm sai.", irreversible=True, effect="Thôi giảm trừ thuế cho người phụ thuộc này sau ngày chọn; muốn giảm trừ tiếp phải đăng ký lại như người mới.",
           resource="hr.terms", action="manage", title="Thôi đăng ký người phụ thuộc", agent="deny", risk="money")
async def end_dependant(ctx: Ctx, inp: EndDependant) -> dict[str, Any]:
    row = await ctx.one("SELECT party_id,registered_from,registered_to FROM hr.dependants WHERE id=%s", (inp.dependant_id,))
    if not row:
        raise NotFound("Không thấy người phụ thuộc")
    await _payee(ctx, row["party_id"])
    if row["registered_to"] is not None or inp.registered_to < row["registered_from"]:
        raise Invalid("Đã thôi đăng ký, hoặc ngày thôi trước ngày bắt đầu")
    await ctx.exec("UPDATE hr.dependants SET registered_to=%s WHERE id=%s", (inp.registered_to, inp.dependant_id))
    return {"dependant_id": inp.dependant_id, "summary": f"Thôi giảm trừ sau {inp.registered_to:%d/%m/%Y}"}


@m.query("dependants", resource="hr.terms", title="Người phụ thuộc")
async def dependants(ctx: Ctx, inp: StaffOn) -> dict[str, Any]:
    """Người phụ thuộc đang đăng ký (mỗi người lao động thấy của chính mình; phụ trách lương / nhân sự thấy hết)."""
    day: date = inp.on or today()
    rows = await ctx.all("""SELECT id,party_id,name,relation,registered_from,registered_to,note FROM hr.dependants
                            WHERE registered_to IS NULL OR registered_to >= %s ORDER BY party_id,registered_from LIMIT 2000""", (day,))
    names = await _names(ctx, list({r["party_id"] for r in rows}))
    for r in rows:
        r["employee"] = names.get(str(r["party_id"]), {}).get("name", "")
        r["open"] = "yes" if r["registered_to"] is None else "no"
    return ui.table(rows, [ui.col("employee", "Người lao động"), ui.col("name", "Người phụ thuộc"), ui.col("relation", "Quan hệ", "badge", labels=RELATION),
                           ui.col("registered_from", "Từ", "date"), ui.col("registered_to", "Đến", "date"), ui.col("note", "Ghi chú")],
                    title="Người phụ thuộc giảm trừ gia cảnh", limit=2000, empty="Chưa đăng ký người phụ thuộc nào")


m.screens(
    "admin",
    (
        Screen(
            "hr_staff",
            "Hồ sơ làm việc",
            "person.text.rectangle",
            "hr.staff",
            tint="blue",
            group="Nhân sự",
            min_scope="all",
            actions=(A("hr.set_employment", style="primary"), A("hr.set_assignment")),
            row_actions=(A("hr.end_employment", "Ghi ngày nghỉ", bind={"employment_id": "id"}, when={"open": ["yes"]}),),
            row_click="open", open=Open("hr.history", {"party_id": "party_id"}, "Lịch sử"),
        ),
        Screen("hr_org", "Cơ cấu đội theo ngày", "point.3.connected.trianglepath.dotted", "hr.org", group="Nhân sự", min_scope="all",
               actions=(A("admin.set_team_lead", style="primary"),)),
    ),
    rank=25,
)

from barista.modules.hr import credentials  # noqa: E402 — chứng chỉ nhân sự (0202): đăng ký thao tác của module
