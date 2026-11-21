"""Tenant-scoped people/organizations; identity is not an employee record or a login."""

import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, LiteralString
from uuid import UUID

from barista.api import Action as A
from barista.api import (
    Conflict,
    Ctx,
    Day,
    Denied,
    InfoGroup,
    Input,
    Invalid,
    Module,
    NoInput,
    NotFound,
    Open,
    Pack,
    Screen,
    SearchIn,
    SearchTerms,
    dong,
    field,
    parse_phone,
    phone_display,
    regulations,
    today,
    ui,
    zone,
)

Pack(
    "directory",
    "Cá nhân & tổ chức",
    "Định danh dùng chung, độc lập tài khoản đăng nhập",
    kind="capability",
    grants=(
        ("admin", "parties", "all", "read write manage"),
        ("accountant", "parties", "all", "read write manage"),
        ("director", "parties", "all", "read"),
        ("admin", "parties.contacts", "all", "read write manage"),
        ("director", "parties.contacts", "all", "read"),
        ("office", "parties", "all", "read"),
    ),
    # 0239 phần 3 (rà Codex F2 — nhãn nói đúng chỗ áp): liên hệ ở danh mục cá nhân; SĐT trên khách / lead theo nhóm "Khách & lead" của gói Khách hàng
    info_groups=(InfoGroup("contacts", "Liên hệ & sự đồng ý của từng người", "parties.contacts",
                           "SĐT, email, quan hệ, sự đồng ý ở Cá nhân & tổ chức; SĐT trên màn khách / lead theo quyền Khách & lead"),),
)

m = Module("parties", "Cá nhân & tổ chức", pack="directory", resources={"parties": "Danh mục cá nhân & tổ chức",
                                                                       "parties.contacts": "Liên hệ, quan hệ & sự đồng ý của từng người"})


PARTY_KIND = {"person": "Cá nhân", "organization": "Tổ chức"}
# Một quy tắc cho "còn nhận quyền hưởng mới": chưa bị ngừng dùng, và nếu gắn đăng nhập thì thành viên đang hoạt động (không cờ thứ hai để lệch).
ACTIVE: LiteralString = (
    "p.active AND p.merged_into IS NULL AND (p.user_id IS NULL OR EXISTS(SELECT 1 FROM core.memberships mb "
    "WHERE mb.tenant_id=p.tenant_id AND mb.user_id=p.user_id AND mb.active))"
)


class CreateParty(Input):
    display_name: str = field("Tên", max_length=200, example="Công ty TNHH Giới thiệu Mẫu")
    kind: Literal["person", "organization"] = field("Loại", "person", options=PARTY_KIND)


@m.command("create", resource="parties", action="manage", title="Thêm cá nhân / tổ chức", agent="propose")
async def create(ctx: Ctx, inp: CreateParty) -> dict[str, Any]:
    if not ctx.p.has_scope("parties", "all"):
        raise Denied("Cần phạm vi cả công ty")
    if not inp.display_name.strip():
        raise Invalid("Cần tên cá nhân / tổ chức")
    row = await ctx.one(
        "INSERT INTO parties.parties (kind, display_name) VALUES (%s, %s) RETURNING id", (inp.kind, inp.display_name)
    )
    assert row
    return {"party_id": row["id"]}


class UpdateParty(Input):
    party_id: UUID = field("Bên", hidden=True)
    display_name: str = field("Tên", max_length=200)
    active: bool = field("Đang dùng", True, help="Bỏ chọn: không thêm được quyền hưởng mới; nghĩa vụ đã ghi vẫn chi trả được")
    listed: bool = field("Hiện trong danh mục chọn", True, help="Bỏ chọn: không hiện ở ô chọn bên nhận tiền; tìm theo tên vẫn thấy")


@m.command("update", resource="parties", action="manage", title="Sửa cá nhân / tổ chức", agent="propose")
async def update(ctx: Ctx, inp: UpdateParty) -> dict[str, Any]:
    """Sửa tên không đổi chứng từ đã ghi (lệnh chi, khoản hưởng giữ tên lúc lập). Người đang là thành viên công ty thì ngừng từ Quản trị
    (khoá thành viên), không ngừng riêng ở đây để hai nơi không lệch."""
    if not ctx.p.has_scope("parties", "all"):
        raise Denied("Cần phạm vi cả công ty")
    if not inp.display_name.strip():
        raise Invalid("Cần tên cá nhân / tổ chức")
    row = await ctx.one("SELECT p.user_id,p.display_name," + ACTIVE + " AS usable FROM parties.parties p WHERE p.id=%s FOR UPDATE", (inp.party_id,))
    if not row:
        raise NotFound("Không thấy cá nhân / tổ chức")
    if not inp.active and row["usable"] and row["user_id"]:
        raise Invalid("Người này đang là thành viên công ty: khoá thành viên ở Quản trị, không ngừng dùng ở đây")
    await ctx.exec("UPDATE parties.parties SET display_name=%s,active=%s,listed=%s WHERE id=%s", (inp.display_name, inp.active, inp.listed, inp.party_id))
    if row["display_name"] != inp.display_name:
        ctx.emit("parties.party_renamed", party_id=inp.party_id, name=inp.display_name)
    out: dict[str, Any] = {"party_id": inp.party_id}
    if not inp.active:
        left = await ctx.call_system_optional("acct.party_open_vnd", party_id=inp.party_id)
        if left and any(left.values()):
            out["warning"] = (
                f"Đã ngừng dùng nhưng còn phải trả {dong(left['owed'])}, đang ứng {dong(left['advanced'])}, "
                f"phải thu hồi {dong(left['recovery'])}{_hidden(left)}: các khoản này vẫn xử lý được bình thường."
            )
    return out


def _hidden(left: dict[str, int]) -> str:
    """Khoản mật (vd lương) chỉ được báo là CÓ — số tiền chỉ người có quyền nguồn đó xem (review 09/11)."""
    return f", và {left['confidential']} khoản mật chưa xong (chỉ người có quyền xem chi tiết)" if left.get("confidential") else ""


class PartyRef(Input):
    party_id: UUID


@m.query(
    "resolve_for_payment",
    resource="parties",
    title="Định danh bên nhận",
    internal=True,
    agent="deny",
    callers=("acct", "hr", "pay"), family="identity",
    why="Nguồn (module nêu tên, hoặc gói khai consumes(\"identity\")) đã kiểm quyền hồ sơ/chi: xác nhận đúng tenant cho một party_id, kể cả bên đã ngừng (nghĩa vụ đã ghi vẫn phải trả được; "
    "`active` để bên gọi chặn quyền hưởng MỚI); chỉ trả id/kind/name/user_id (liên kết login, không quyền đại diện)/active, "
    "không thông tin định danh hay ngân hàng. hr.set_terms / pay cần kind (chỉ cá nhân) và user_id (chủ dòng cho RLS, chặn tự ghi cho mình)."
    " [id: module gọi tự có — party_id từ khoản / phiếu của gói nguồn và acct, sổ công của pay; id do người dùng gõ (lệnh đặt người hưởng của gói, hr.set_terms, "
    "pay.request_entry / import_work) đã kiểm bằng call_as / ctx.call quyền người đặt trước]",
)
async def resolve_for_payment(ctx: Ctx, inp: PartyRef) -> dict[str, Any]:
    row = await ctx.one("SELECT p.id,p.kind,p.display_name,p.user_id," + ACTIVE + " AS active FROM parties.parties p WHERE p.id=%s", (inp.party_id,))
    if not row:
        raise NotFound("Không thấy bên nhận")
    return row


class SupplierIn(Input):
    name: str
    kind: Literal["person", "organization"] = "organization"
    tax_code: str = ""


async def _ensure_org(ctx: Ctx, inp: SupplierIn, source: str) -> dict[str, Any]:
    """Khớp mã số thuế (định danh tax_code) ⇒ bên đó; không mã ⇒ TỔ CHỨC đang dùng có cùng tên chuẩn hoá (cá nhân không khớp theo tên — dễ trùng
    người khác); không thấy ⇒ tạo bên mới hiện trong danh mục chọn, kèm mã số thuế nếu có. Một nơi cho bên bán (``ensure_supplier``) và bên nợ
    (``ensure_counterparty``)."""
    from barista.api import name_key

    name, tax = inp.name.strip(), "".join(inp.tax_code.split())
    if not name:
        raise Invalid("Cần tên bên đối tác")
    if tax and not all(c.isdigit() or c == "-" for c in tax):
        raise Invalid(f"Mã số thuế “{inp.tax_code}” chỉ gồm chữ số (và gạch nối chi nhánh)")
    if tax and (got := await ctx.one("""SELECT p.id, p.display_name FROM parties.identifiers i JOIN parties.parties p ON p.id = i.party_id
                                        WHERE i.kind = 'tax_code' AND i.value = %s""", (tax,))):
        return {"party_id": got["id"], "name": got["display_name"], "created": False}
    if inp.kind == "organization":
        key = name_key(name)
        for r in await ctx.all("SELECT p.id, p.display_name FROM parties.parties p WHERE p.kind = 'organization' AND " + ACTIVE + " ORDER BY p.created_at, p.id"):
            if name_key(r["display_name"]) == key:
                return {"party_id": r["id"], "name": r["display_name"], "created": False}
    pid = await ctx.val("INSERT INTO parties.parties (kind, display_name) VALUES (%s, %s) RETURNING id", (inp.kind, name))
    if tax:
        await ctx.exec("INSERT INTO parties.identifiers (party_id, kind, value, source) VALUES (%s, 'tax_code', %s, %s)", (pid, tax, source))
    return {"party_id": pid, "name": name, "created": True}


@m.command(
    "ensure_supplier",
    resource="parties",
    title="Tìm hoặc tạo bên bán",
    internal=True,
    agent="deny",
    callers=("acct",),
    why="acct.record_expense (kế toán đã qua quyền Kế toán cả công ty) ghi bên bán của phiếu chi phí / hoá đơn mua vào theo TÊN và MÃ SỐ THUẾ người "
    "nhập ghi trên dòng (không phải mã người). Khớp mã số thuế (định danh tax_code) ⇒ bên đó; không mã ⇒ TỔ CHỨC đang dùng có cùng tên chuẩn hoá "
    "(cá nhân không khớp theo tên — dễ trùng người khác); không thấy ⇒ tạo bên mới hiện trong danh mục chọn, kèm mã số thuế nếu có. Trả CHỈ "
    "{party_id, name, created} — không liên hệ, không bên khác.",
)
async def ensure_supplier(ctx: Ctx, inp: SupplierIn) -> dict[str, Any]:
    return await _ensure_org(ctx, inp, "acct.expense")


@m.command(
    "ensure_counterparty",
    resource="parties",
    title="Tìm hoặc tạo bên nợ",
    internal=True,
    agent="deny",
    callers=("acct",), family="identity",
    why="Gói khai consumes(\"identity\") ghi BÊN ĐỐI TÁC của đối tượng nó theo tên + mã số thuế (vd thoả thuận nhận hàng với đối tác — 0182, người có quyền ghi thoả thuận); "
    "acct.issue_receivable / add_opening_line (người nhập đã qua quyền Kế toán hoặc gói nguồn) ghi BÊN NỢ của khoản phải thu theo mã người; nơi chỉ có "
    "TÊN (file nhập, gói ngoài lõi, số dư đầu kỳ, chạy bù khoản cũ — 0177) thì acct hỏi qua đây cùng quy tắc khớp với ensure_supplier: mã số thuế ⇒ bên đó; "
    "không mã ⇒ TỔ CHỨC đang dùng có cùng tên chuẩn hoá (cá nhân không khớp theo tên); không thấy ⇒ tạo bên mới. Trả CHỈ {party_id, name, created} — "
    "không liên hệ, không bên khác.",
)
async def ensure_counterparty(ctx: Ctx, inp: SupplierIn) -> dict[str, Any]:
    return await _ensure_org(ctx, inp, "acct.receivable")


class PartyOn(Input):
    party_id: UUID
    on: date


@m.query("referrers_on", resource="parties", title="Người giới thiệu một người tại một ngày", internal=True, agent="deny", family="structure",
         why="Gói khai consumes(\"structure\") đề xuất người hưởng theo mẫu chia (0160): ai GIỚI THIỆU người này (quan hệ refers, bên giới thiệu → người được giới thiệu) "
             "hiệu lực tại ngày ký. Chỉ trả mã người giới thiệu — không liên hệ, không quan hệ khác. [id: module gọi tự có — party_id là khách của "
             "đối tượng gói đang chia (crm.customer_party), không do người dùng nêu]")
async def referrers_on(ctx: Ctx, inp: PartyOn) -> dict[str, Any]:
    rows = await ctx.all("""SELECT DISTINCT party_id FROM parties.relations WHERE kind = 'refers' AND related_party_id = %s AND started_on <= %s
                            AND (ended_on IS NULL OR ended_on >= %s) ORDER BY party_id""", (inp.party_id, inp.on, inp.on))
    return {"party_ids": [str(r["party_id"]) for r in rows]}


class PartyIds(Input):
    party_ids: list[UUID]


@m.query(
    "names",
    resource="parties",
    title="Tên các bên",
    internal=True,
    agent="deny",
    callers=("acct", "hr", "pay"), family="identity",
    why="Màn Phải trả & ứng và Cảnh báo chi (kế toán cả công ty), Hồ sơ làm việc (người có quyền hr cả công ty), kỳ lương / sổ công / mức trả (người có quyền "
    "pay), gói khai consumes(\"identity\") hiện người trên dòng của nó mà người xem đã thấy qua RLS (vd người đứng doanh số ngoài công ty — 0151) cần tên thay vì "
    "mã, và biết party có tồn tại trong công ty: theo đúng các party_id bên gọi đang xử lý, chỉ trả tên + loại."
    " [id: module gọi tự có — party_ids là các dòng module gọi đang hiện (khoản phải trả, hồ sơ làm việc); id do người dùng gõ phải đi call_as (hr._seen, schedule._resource_fields, meet.invite)]",
)
async def names(ctx: Ctx, inp: PartyIds) -> dict[str, dict[str, str]]:
    rows = await ctx.all("SELECT id,display_name,kind FROM parties.parties WHERE id=ANY(%s)", (inp.party_ids,))
    return {str(r["id"]): {"name": r["display_name"], "kind": r["kind"]} for r in rows}


@m.query(
    "logins",
    resource="parties",
    title="Tài khoản đăng nhập của các bên",
    internal=True,
    agent="deny",
    callers=("hr",),
    why="hr.current_titles (chức danh hiện tại làm NHÃN người ở màn chọn vai — lõi chạy bằng quyền hệ thống): đổi party_id của các dòng hồ sơ làm "
    "việc đang hiệu lực sang tài khoản đăng nhập liên kết; hr.remind_credentials (vòng quét nhắc chứng chỉ hết hạn — 0202): tài khoản của QUẢN LÝ TRỰC TIẾP ghi ở "
    "hồ sơ phân công để báo cùng người có chứng chỉ. Chỉ trả cặp party_id → user_id của bên có tài khoản; không tên, không liên hệ."
    " [id: module gọi tự có — party_ids lấy từ hr.employments / hr.assignments, không do người dùng nêu]",
)
async def logins(ctx: Ctx, inp: PartyIds) -> dict[str, str]:
    rows = await ctx.all("SELECT id,user_id FROM parties.parties WHERE id=ANY(%s) AND user_id IS NOT NULL", (inp.party_ids,))
    return {str(r["id"]): str(r["user_id"]) for r in rows}


class UserRef(Input):
    user_id: UUID


@m.command(
    "ensure_member",
    resource="parties",
    title="Định danh thành viên",
    internal=True,
    agent="deny",
    callers=("hr", "pay"), family="identity",
    why="Gói gọi (pay, gói khai consumes(\"identity\")) lấy user từ hồ sơ / phiếu đã có quyền (pay: người nguồn công nêu bằng đăng nhập, vd người dạy của buổi đã xác nhận; "
    "hr.record_credential: người phụ trách nhân sự cả công ty nêu email thành viên trên dòng Excel — hr kiểm lại party trả về bằng quyền người ghi): kiểm "
    "membership cùng tenant; tạo một identity person nếu chưa có; trả party_id, không tạo login hoặc cấp quyền đại diện."
    " [id: module gọi tự có — user_id lấy từ phiếu / hồ sơ của module gọi, kiểm thành viên cùng công ty]",
)
async def ensure_member(ctx: Ctx, inp: UserRef) -> dict[str, Any]:
    existing = await ctx.val("SELECT id FROM parties.parties WHERE user_id=%s", (inp.user_id,))
    if existing:   # kể cả người đã rời: giữ định danh lịch sử để trả nốt nghĩa vụ, không tạo lại login
        return {"party_id": existing}
    name = await ctx.val(
        "SELECT u.name FROM core.users u JOIN core.memberships m ON m.user_id = u.id "
        "WHERE u.id = %s AND m.tenant_id = core.tenant()",
        (inp.user_id,),
    )
    if name is None:
        raise NotFound("Không thấy thành viên")
    pid = await ctx.val(
        "INSERT INTO parties.parties (kind, display_name, user_id) VALUES ('person', %s, %s) "
        "ON CONFLICT (tenant_id, user_id) DO UPDATE SET user_id = EXCLUDED.user_id RETURNING id",
        (name, inp.user_id),
    )
    return {"party_id": pid}


class PartyFilter(Input):
    q: str = field("Tìm theo tên hoặc liên hệ", "", help="Có từ khoá thì tìm cả người ngoài danh mục chọn (khách, người được giám hộ…)")


@m.query("list", resource="parties", min_scope="all", title="Cá nhân & tổ chức")
async def list_parties(ctx: Ctx, inp: PartyFilter) -> dict[str, Any]:
    t = SearchTerms(inp.q)   # cùng cách khớp với ⌘K (0087): tên có dấu hay không, từng từ; liên hệ (email, mã số thuế, SĐT) chứa nguyên từ khoá
    everyone = ctx.p.can("parties.contacts", "read")   # không có quyền này: chỉ tìm trong danh mục chọn, không tới khách (0111)
    rows = await ctx.all(
        "SELECT p.id,p.kind,p.display_name,p.user_id IS NOT NULL AS has_login,p.user_id IS NULL AS external,p.listed,"
        + ACTIVE + " AS active FROM parties.parties p"
        " WHERE p.merged_into IS NULL AND ((%(q)s = '' AND p.listed) OR (%(q)s <> '' AND (p.listed OR %(all)s) AND (" + t.match("p.display_name") + " OR EXISTS ("
        "SELECT 1 FROM parties.identifiers i WHERE i.party_id = p.id AND i.value ILIKE '%%' || %(q)s || '%%'))))"
        " ORDER BY " + t.rank("p.display_name") + ", p.display_name LIMIT 500", {**t.params, "q": t.q, "all": everyone}
    )
    for r in rows:
        r["state"] = "active" if r["active"] else "inactive"
        r["login"] = "Có" if r["has_login"] else ""
    return ui.table(
        rows,
        [
            ui.col("display_name", "Tên", width=280),
            ui.col("kind", "Loại", "badge", labels=PARTY_KIND),
            ui.col("login", "Đăng nhập"),
            ui.col("state", "Trạng thái", "badge", labels={"active": "Đang dùng", "inactive": "Ngừng dùng"}, tones={"active": "green", "inactive": "gray"}),
        ],
        title="Cá nhân & tổ chức",
        empty="Chưa có ai",
        limit=500,
        note="Người ngoài không cần đăng nhập. Thành viên công ty tự có ở đây; ngừng người ngoài chỉ chặn quyền hưởng mới.",
    )


@m.search("search_parties", "Cá nhân & tổ chức", resource="parties", rank=45, more=("parties.list", {}))
async def search_parties(ctx: Ctx, inp: SearchIn) -> list[dict[str, Any]]:
    """⌘K (0087): người / tổ chức trong DANH MỤC CHỌN — nhân viên, đối tác, nhà cung cấp — theo tên (có dấu hay không, từng từ) hoặc mã số
    thuế / email / SĐT đã ghi (khớp nguyên chuỗi). Khách ngoài danh mục không ở đây: nhóm "Khách" của crm lo, theo quyền crm. Ai có quyền xem liên hệ
    mở hồ sơ người đó; còn lại mở danh mục đã lọc theo tên."""
    t = SearchTerms(inp.q)
    rows = await ctx.all(
        "SELECT p.id, p.kind, p.display_name, p.user_id IS NOT NULL AS member, " + ACTIVE + " AS active FROM parties.parties p"
        " WHERE p.merged_into IS NULL AND p.listed AND (" + t.match("p.display_name") + " OR EXISTS ("
        "SELECT 1 FROM parties.identifiers i WHERE i.party_id = p.id AND (i.value ILIKE %(_q)s"
        + (" OR (i.kind = 'phone' AND i.value LIKE %(_d)s)" if t.digits else "") + ")))"   # SĐT lưu E.164: "0912…" khớp "+84912…"
        " ORDER BY " + t.rank("p.display_name") + ", p.display_name LIMIT %(_n)s", {**t.params, "_q": "%" + t.q.replace("%", "").replace("_", "") + "%"})
    profile = ctx.p.can("parties.contacts", "read") and ctx.p.has_scope("parties.contacts", "all")
    return [{"title": r["display_name"], "subtitle": "" if r["active"] else "Ngừng dùng",
             "badge": "Thành viên" if r["member"] else PARTY_KIND[r["kind"]], "tone": "accent" if r["active"] else "muted",
             "open": {"query": "parties.person", "input": {"party_id": str(r["id"])}} if profile
             else {"query": "parties.list", "input": {"q": r["display_name"]}}} for r in rows]


@m.query("options", resource="parties", title="Chọn cá nhân / tổ chức", creates="parties.create")
async def options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    rows = await ctx.all("SELECT p.id,p.kind,p.display_name FROM parties.parties p WHERE p.listed AND " + ACTIVE + " ORDER BY p.display_name LIMIT 1000")
    return [{"value": str(r["id"]), "label": f"{r['display_name']} ({PARTY_KIND[r['kind']].lower()})"} for r in rows]


# ── Định danh người (0111): nhiều kênh liên hệ, quan hệ, sự đồng ý, gộp bằng con trỏ ──

IDENTIFIER_KIND = {"phone": "SĐT", "email": "Email", "zalo": "Zalo", "facebook": "Facebook", "other": "Khác"}
RELATION_KIND = {"guardian_of": "Là người giám hộ của", "pays_for": "Trả tiền cho", "represents": "Đại diện cho", "refers": "Giới thiệu"}
CONSENT_PURPOSE = {"marketing": "Nhận tiếp thị", "care": "Chăm sóc & nhắc lịch", "data_processing": "Xử lý dữ liệu cá nhân"}
CONSENT_STATE = {"granted": "Đủ đồng ý", "missing_child": "Thiếu đồng ý của trẻ", "missing_guardian": "Thiếu đồng ý của người giám hộ",
                 "withdrawn": "Đã rút / từ chối", "not_recorded": "Chưa ghi nhận"}
# Ngưỡng tuổi là luật (bất biến 5, 0067, 0112): chỉ đọc ở bộ quy định, không viết số trong code.
LAW = "vn-personal-data"
regulations.declare(LAW, required=("dual_consent_from_age", "majority_age"))   # hợp đồng mã (0154): hai ngưỡng tuổi đọc theo tên ở ``age_rule``


@dataclass(frozen=True)
class AgeRule:
    dual_from: int
    majority: int
    ref: str
    assumed: bool


def age_rule(on: date) -> AgeRule | None:
    """Phiên bản hiệu lực ngày ``on``. Trước phiên bản đầu tiên ⇒ None: không có luật để áp, xét như chưa biết tuổi (0067)."""
    v = regulations.version_on(LAW, on)
    if v is None:
        return None
    return AgeRule(int(v.params["dual_consent_from_age"]["value"]), int(v.params["majority_age"]["value"]), v.ref, v.status == "assumption")


def age_on(born: date, on: date) -> int:
    """Số tuổi đã đủ. Sinh 29/02: năm không nhuận đủ tuổi từ 01/03 (cách đọc thận trọng, chưa hỏi luật sư)."""
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def comes_of_age(born: date, years: int) -> date:
    """Ngày đủ ``years`` tuổi — cùng cách đọc với ``age_on``."""
    y = born.year + years
    leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
    return date(y, 3, 1) if (born.month, born.day) == (2, 29) and not leap else born.replace(year=y)


def required_consents(age: int | None, rule: AgeRule | None, has_guardian: bool) -> tuple[str, ...]:
    """Ai phải đồng ý. Chưa biết tuổi mà đang có người giám hộ ⇒ xét như trẻ từ tuổi đồng ý kép; chưa biết tuổi, không người giám hộ ⇒ chính
    người đó (kèm cờ ``age_known`` để đường gửi sau này tự quyết)."""
    if age is None or rule is None:
        return ("subject", "guardian") if has_guardian else ("subject",)
    if age >= rule.majority:
        return ("subject",)
    return ("subject", "guardian") if age >= rule.dual_from else ("guardian",)


def effective_consent(required: tuple[str, ...], subject: bool | None, guardians: list[bool]) -> str:
    """Trạng thái hiệu lực của MỘT mục đích. ``subject``: lần ghi mới nhất của chính người này; ``guardians``: lần ghi mới nhất của từng người giám
    hộ ĐANG hiệu lực, ghi trong lúc quan hệ hiện tại đang mở. Chỉ tính lời của người ĐANG có tiếng nói theo tuổi: dưới tuổi đồng ý kép thì lời của trẻ
    chỉ để biết (không chặn, không mở); đủ tuổi thì lời của người giám hộ cũ không tính. Một lời "không" của người đang có tiếng nói thắng mọi lời
    "có": hai bên khác ý là chưa được phép."""
    counted = guardians if "guardian" in required else []
    own = subject if "subject" in required else None
    votes = ([] if own is None else [own]) + counted
    if False in votes:
        return "withdrawn"
    missing_subject = "subject" in required and own is not True
    missing_guardian = "guardian" in required and True not in counted
    if not missing_subject and not missing_guardian:
        return "granted"
    if not votes:
        return "not_recorded"
    return "missing_child" if missing_subject else "missing_guardian"


def normalized(kind: str, value: str) -> str:
    """MỘT cách chuẩn hoá trước khi so / ghi: SĐT về dạng E.164 +84… (0199), email chữ thường; kênh khác giữ nguyên, chỉ bỏ khoảng trắng hai đầu."""
    v = (value or "").strip()
    if kind == "phone":
        try:
            return parse_phone(v) or ""
        except ValueError as e:
            raise Invalid(f"SĐT không hợp lệ: {v}") from e
    return v.lower() if kind == "email" else v


async def _root(ctx: Ctx, party_id: UUID) -> UUID:
    """Đi theo con trỏ gộp tới người đang giữ lại. Không có dòng ⇒ NotFound (khác công ty hoặc không có quyền)."""
    root = await ctx.val(
        """WITH RECURSIVE up AS (SELECT id, merged_into FROM parties.parties WHERE id = %s
                                 UNION SELECT p.id, p.merged_into FROM parties.parties p JOIN up ON p.id = up.merged_into)
           SELECT id FROM up WHERE merged_into IS NULL""", (party_id,))
    if root is None:
        raise NotFound("Không thấy cá nhân / tổ chức")
    return root


async def _cluster(ctx: Ctx, party_id: UUID) -> list[UUID]:
    root = await _root(ctx, party_id)
    rows = await ctx.all(
        """WITH RECURSIVE down AS (SELECT id FROM parties.parties WHERE id = %s
                                   UNION SELECT p.id FROM parties.parties p JOIN down ON p.merged_into = down.id)
           SELECT id FROM down""", (root,))
    return [r["id"] for r in rows]


async def _roots(ctx: Ctx, ids: list[UUID]) -> dict[UUID, UUID]:
    """Mỗi định danh ⇒ người đang giữ lại, một câu SQL cho cả danh sách."""
    rows = await ctx.all(
        """WITH RECURSIVE up AS (SELECT id AS start, id, merged_into FROM parties.parties WHERE id = ANY(%s)
                                 UNION SELECT up.start, p.id, p.merged_into FROM parties.parties p JOIN up ON p.id = up.merged_into)
           SELECT start, id AS root FROM up WHERE merged_into IS NULL""", (ids,))
    return {r["start"]: r["root"] for r in rows}


async def _birth(ctx: Ctx, ids: list[UUID]) -> tuple[date | None, bool]:
    """Ngày sinh của một người (mọi định danh đã gộp) + cờ hai định danh ghi khác nhau. Khác nhau ⇒ lấy ngày TRẺ hơn: giữ bảo vệ lâu hơn cho tới
    khi người có quyền sửa."""
    born = [r["born_on"] for r in await ctx.all("SELECT DISTINCT born_on FROM parties.birth_dates WHERE party_id = ANY(%s)", (ids,))]
    return (max(born) if born else None), len(born) > 1


async def _open_guardians(ctx: Ctx, ids: list[UUID]) -> list[UUID]:
    """Định danh đang có quan hệ ``guardian_of`` còn hiệu lực với người này. ``pays_for`` không bao giờ vào đây."""
    return list(await _guardians_since(ctx, ids))


async def _guardians_since(ctx: Ctx, ids: list[UUID]) -> dict[UUID, Any]:
    """Định danh người giám hộ đang hiệu lực ⇒ lúc quan hệ HIỆN TẠI được mở. Lời của người đó chỉ tính nếu ghi từ lúc này: kết thúc rồi mở lại giám
    hộ không làm sống lại lời đã cho trước đó (0112)."""
    rows = await ctx.all("""SELECT party_id, min(created_at) AS since FROM parties.relations
                            WHERE kind = 'guardian_of' AND ended_on IS NULL AND related_party_id = ANY(%s) GROUP BY party_id""", (ids,))
    return {r["party_id"]: r["since"] for r in rows}


async def _note_candidate(ctx: Ctx, a: UUID, b: UUID, reason: str) -> None:
    await ctx.exec("""INSERT INTO parties.merge_candidates (party_id, other_party_id, reason) VALUES (%s, %s, %s)
                      ON CONFLICT (tenant_id, party_id, other_party_id) DO NOTHING""", (min(a, b), max(a, b), reason))


class IdentifierIn(Input):
    kind: str
    value: str


class EnsurePerson(Input):
    name: str
    identifiers: list[IdentifierIn]
    source: str = ""
    reuse: bool = True   # False ⇒ luôn tạo định danh MỚI (không gắn vào khách / đồng nghiệp trùng liên hệ) — liên hệ đã thuộc người khác giữ nguyên chỗ cũ


@m.command("ensure_person", resource="parties", title="Tìm hoặc tạo định danh theo liên hệ", internal=True, agent="deny",
           callers=("crm",), family="identity",
           why="crm thêm khách / nhận lead và gói khai consumes(\"identity\") ghi người "
               "của đối tượng chính nó từ LIÊN HỆ người dùng gõ (vd người trả tiền của một gói bán, người đứng doanh số ngoài công ty, người hưởng / người "
               "nhận hộ không đăng nhập khi nhập lịch sử — 0151, chủ nhà ký gửi — 0171) — người gọi đã qua quyền của module đó, "
               "thường không có quyền danh mục: "
               "khớp theo liên hệ đã chuẩn hoá; chưa có ⇒ tạo người ngoài danh mục chọn; khớp hai người khác nhau ⇒ dùng người đầu, ghi nghi trùng, "
               "KHÔNG tự gộp. Trả party_id + created.")
async def ensure_person(ctx: Ctx, inp: EnsurePerson) -> dict[str, Any]:
    wanted = [(i.kind, normalized(i.kind, i.value)) for i in inp.identifiers]
    wanted = [(k, v) for k, v in wanted if v]
    if not wanted:
        raise Invalid("Cần ít nhất một cách liên hệ")
    if not inp.reuse:
        pid = await ctx.val("INSERT INTO parties.parties (kind, display_name, listed) VALUES ('person', %s, false) RETURNING id", (inp.name,))
        for kind, value in wanted:
            await ctx.exec("""INSERT INTO parties.identifiers (party_id, kind, value, source) VALUES (%s, %s, %s, %s)
                              ON CONFLICT (tenant_id, kind, value) DO NOTHING""", (pid, kind, value, inp.source))
        return {"party_id": pid, "created": True}
    matchable = [(k, v) for k, v in wanted if k != "other"]   # "khác" là chữ tự do: giữ để liên hệ, không dùng để nhận ra người
    hits = await ctx.all("""SELECT i.kind, i.party_id, p.created_at FROM parties.identifiers i JOIN parties.parties p ON p.id = i.party_id
                            WHERE (i.kind, i.value) IN (SELECT * FROM unnest(%s::text[], %s::text[])) ORDER BY p.created_at, i.party_id""",
                         ([k for k, _ in matchable], [v for _, v in matchable]))
    phone_hit = next((h["party_id"] for h in hits if h["kind"] == "phone"), None)
    by_phone = await _root(ctx, phone_hit) if phone_hit else None
    others: list[UUID] = []
    for h in hits:
        r = await _root(ctx, h["party_id"])
        if r != by_phone and r not in others:
            others.append(r)
    has_phone = any(k == "phone" for k, _ in wanted)
    if by_phone:
        pid, suspects = by_phone, others
    elif has_phone:   # số mới mà email / kênh khác trùng người đã có số khác: có thể là hai người dùng chung email ⇒ người mới + nghi trùng
        phones = {r["party_id"] for r in await ctx.all("SELECT party_id FROM parties.identifiers WHERE kind = 'phone' AND party_id = ANY(%s)", (others,))}
        fit = [r for r in others if r not in phones]
        pid, suspects = (fit[0], [r for r in others if r != fit[0]]) if fit else (None, others)
    else:
        pid, suspects = (others[0], others[1:]) if others else (None, [])
    created = pid is None
    if pid is None:
        pid = await ctx.val("INSERT INTO parties.parties (kind, display_name, listed) VALUES ('person', %s, false) RETURNING id", (inp.name,))
    for other in suspects:
        await _note_candidate(ctx, pid, other, "Một lần nhập có liên hệ trùng với người khác")
    for kind, value in wanted:   # liên hệ đã thuộc người khác thì giữ nguyên chỗ cũ
        await ctx.exec("""INSERT INTO parties.identifiers (party_id, kind, value, source) VALUES (%s, %s, %s, %s)
                          ON CONFLICT (tenant_id, kind, value) DO NOTHING""", (pid, kind, value, inp.source))
    return {"party_id": pid, "created": created}


class ClusterIn(Input):
    party_id: UUID


@m.query("cluster", resource="parties", title="Mọi định danh của một người", internal=True, agent="deny", callers=("crm",),
         why="crm tra khách theo người (đã gộp): bên gọi đang xử lý một party_id của chính nó. Trả danh sách id cùng một người, không tên, không liên hệ."
             " [id: module gọi tự có — party_id lấy từ khách / liên hệ crm đang xử lý, trong phạm vi người gọi thấy]")
async def cluster(ctx: Ctx, inp: ClusterIn) -> list[UUID]:
    return await _cluster(ctx, inp.party_id)


async def consent_states(ctx: Ctx, party_id: UUID) -> dict[str, Any]:
    """Trạng thái đồng ý HIỆU LỰC theo từng mục đích của MỘT người, đọc xuyên mọi định danh đã gộp (dòng giữ ở định danh được chọn nên tách lại
    không lẫn). Người giám hộ tính theo người (đã gộp), chỉ khi quan hệ còn hiệu lực lúc đọc."""
    ids = await _cluster(ctx, party_id)
    on = today()
    rule, (born, conflict) = age_rule(on), await _birth(ctx, ids)
    age = age_on(born, on) if born else None
    found = await _guardians_since(ctx, ids)
    since: dict[UUID, Any] = {}
    for pid, root in ((await _roots(ctx, list(found))).items() if found else ()):
        since[root] = min(since.get(root, found[pid]), found[pid])
    active = set(since)
    required = required_consents(age, rule, bool(active))
    rows = await ctx.all("SELECT purpose, granted, given_by_party_id, at FROM parties.consents WHERE party_id = ANY(%s) ORDER BY at DESC, id", (ids,))
    by = list({r["given_by_party_id"] for r in rows if r["given_by_party_id"]})
    givers = await _roots(ctx, by) if by else {}
    names = {r["id"]: r["display_name"] for r in await ctx.all("SELECT id, display_name FROM parties.parties WHERE id = ANY(%s)", (list(active),))}
    seen: dict[str, dict[str, Any]] = {}
    for r in rows:   # mới nhất trước: lần đầu gặp một người ở một mục đích là ý hiện tại của người đó
        one = seen.setdefault(r["purpose"], {"subject": None, "guardians": {}, "at": None})
        if r["given_by_party_id"] is None:
            if one["subject"] is None:
                one["subject"], one["at"] = r["granted"], max(one["at"] or r["at"], r["at"])
        elif (g := givers.get(r["given_by_party_id"])) is not None and g in active and g not in one["guardians"] and r["at"] >= since[g]:
            # chỉ người giám hộ đang hiệu lực, lời ghi từ lúc quan hệ hiện tại mở; còn lại là vết, không tính
            one["guardians"][g] = r["granted"]
            one["at"] = max(one["at"] or r["at"], r["at"])
    out = []
    for purpose in [*CONSENT_PURPOSE, *sorted(set(seen) - set(CONSENT_PURPOSE))]:
        one = seen.get(purpose, {"subject": None, "guardians": {}, "at": None})
        state = effective_consent(required, one["subject"], list(one["guardians"].values()))
        out.append({"purpose": purpose, "label": CONSENT_PURPOSE.get(purpose, purpose), "state": state, "state_label": CONSENT_STATE[state],
                    "at": one["at"], "subject": one["subject"], "recorded": purpose in seen,
                    "guardians": [{"party_id": g, "name": names.get(g, ""), "granted": v} for g, v in one["guardians"].items()]})
    return {"consents": out, "required": list(required), "age_known": age is not None, "birth_conflict": conflict, "age": age, "born_on": born,
            "has_guardian": bool(active), "rule_ref": rule.ref if rule else None, "rule_assumed": bool(rule and rule.assumed)}


@m.query("contact_card", resource="parties", title="Liên hệ và sự đồng ý của một người", internal=True, agent="deny", callers=("crm",),
         why="crm.journey, sau khi đọc khách bằng quyền người xem: chỉ cho người đang xem được khách. Trả liên hệ (loại, giá trị) và trạng thái đồng ý "
             "HIỆU LỰC (0112) theo mục đích đã có dòng ghi, gộp mọi định danh của người đó; không ngày sinh, không tên người giám hộ, không quan hệ, "
             "không thông tin ngân hàng."
             " [id: người gọi thấy — party_id của khách mà crm.journey đọc bằng RLS của người xem]")
async def contact_card(ctx: Ctx, inp: ClusterIn) -> dict[str, Any]:
    ids = await _cluster(ctx, inp.party_id)
    contacts = await ctx.all("SELECT kind, value FROM parties.identifiers WHERE party_id = ANY(%s) ORDER BY kind, value", (ids,))
    s = await consent_states(ctx, inp.party_id)
    unsure = "" if s["age_known"] else " (chưa có ngày sinh để xét tuổi)"
    consents = [{"purpose": c["purpose"], "state": c["state"], "at": c["at"],
                 "state_label": c["state_label"] + (unsure if c["state"] == "granted" else "")} for c in s["consents"] if c["recorded"]]
    return {"contacts": contacts, "consents": consents}


class CallConsentIn(ClusterIn):
    purpose: Literal["marketing", "care", "data_processing"]


@m.query("call_consent", resource="parties", title="Sự đồng ý của người được mời vào cuộc gọi", internal=True, agent="deny", callers=("meet",), family="consent",
         why="meet mời một người ngoài vào cuộc gọi trực tiếp, và kiểm lại mỗi lần người đó mở link (0114 bổ sung 07/11): bên gọi đã kiểm người mời "
             "thấy cuộc gọi, hoặc phiên link đúng lời mời. Chỉ đọc trạng thái HIỆU LỰC sẵn có (consent_states, 0112) cho một mục đích: có phải trẻ em "
             "(cần người giám hộ đồng ý), đã biết tuổi chưa, trạng thái + nhãn. Không tên, không ngày sinh, không liên hệ, không người giám hộ."
             " Gói khai consumes(\"consent\") (vd tóm tắt cuộc gọi tổng đài — 0149): trước khi gửi nội dung của khách cho model, đọc trạng thái mục đích"
             " data_processing của khách đã khớp với đối tượng của gói (bên gọi đọc đối tượng đó bằng RLS của người bấm)."
             " [id: người gọi thấy — meet.invite kiểm định danh bằng parties.names qua call_as (quyền người mời); lúc người ngoài mở link dùng định danh đã lưu trên lời mời;"
             " gói tiêu thụ họ lấy party_id từ dòng của chính nó mà người bấm thấy qua RLS]")
async def call_consent(ctx: Ctx, inp: CallConsentIn) -> dict[str, Any]:
    s = await consent_states(ctx, inp.party_id)
    c = next(x for x in s["consents"] if x["purpose"] == inp.purpose)
    return {"child": "guardian" in s["required"], "age_known": s["age_known"], "state": c["state"], "state_label": c["state_label"]}


def _who_said(c: dict[str, Any]) -> str:
    said = [] if c["subject"] is None else [f"Chính người này: {'đồng ý' if c['subject'] else 'không'}"]
    return " · ".join(said + [f"{g['name']}: {'đồng ý' if g['granted'] else 'không'}" for g in c["guardians"]])


def _age_note(s: dict[str, Any]) -> str:
    parts = [] if s["age_known"] else ["Chưa có ngày sinh: chưa xét được tuổi" + (", đang tính như trẻ vì có người giám hộ." if s["has_guardian"] else ".")]
    if s["birth_conflict"]:
        parts.append("Các định danh đã gộp ghi ngày sinh khác nhau: đang tính theo ngày trẻ hơn, cần sửa cho đúng.")
    if s["rule_ref"]:
        parts.append(f"Ngưỡng tuổi theo {s['rule_ref']}{' (giả định, chờ luật sư xác nhận)' if s['rule_assumed'] else ''}. Đây không phải kết luận tuân thủ.")
    return " ".join(parts)


@m.query("consent_state", resource="parties.contacts", min_scope="all", title="Trạng thái đồng ý của một người",
         description="Theo từng mục đích: đủ đồng ý, thiếu đồng ý của trẻ, thiếu đồng ý của người giám hộ, đã rút / từ chối, hay chưa ghi nhận. Ngưỡng tuổi "
                     "lấy từ bộ quy định đang chờ luật sư xác nhận; đây không phải kết luận tuân thủ.")
async def consent_state(ctx: Ctx, inp: ClusterIn) -> dict[str, Any]:
    s = await consent_states(ctx, inp.party_id)
    rows = [{"id": c["purpose"], "label": c["label"], "state": c["state"], "at": c["at"], "who": _who_said(c)} for c in s["consents"]]
    return ui.table(rows, [
        ui.col("label", "Mục đích", width=200),
        ui.col("state", "Trạng thái", "badge", labels=CONSENT_STATE,
               tones={"granted": "green", "missing_child": "orange", "missing_guardian": "orange", "withdrawn": "red", "not_recorded": "gray"}),
        ui.col("who", "Ai đã nói gì", width=280), ui.col("at", "Lần ghi mới nhất", "datetime"),
    ], title="Trạng thái đồng ý", empty="Chưa có mục đích nào", note=_age_note(s))


@m.query("guardians", resource="parties.contacts", title="Người giám hộ đang hiệu lực", internal=True, agent="deny",
         description="Cửa DUY NHẤT để module khác hỏi 'ai được xem thay người này' (0112): chỉ guardian_of còn hiệu lực khi người này chưa đủ tuổi; "
                     "pays_for không bao giờ có ở đây. Chưa khai callers vì chưa có module gọi (test_contracts_are_alive chặn hợp đồng không ai gọi); "
                     "gói đầu tiên dùng nó khai callers + why tại đây.")
async def guardians(ctx: Ctx, inp: ClusterIn) -> list[UUID]:
    ids = await _cluster(ctx, inp.party_id)
    on, (born, _) = today(), await _birth(ctx, ids)
    rule = age_rule(on)
    if born and rule and age_on(born, on) >= rule.majority:
        return []   # đã đủ tuổi nhưng vòng quét chưa tới lượt: không để lọt khoảng đó
    found = await _open_guardians(ctx, ids)
    return list(dict.fromkeys((await _roots(ctx, found)).values())) if found else []


class AddDependant(Input):
    name: str = field("Tên", max_length=200, example="Nguyễn Bảo An")
    born_on: Day = field("Ngày sinh", example="15/03/2017", audit=False)
    guardian_party_id: UUID = field("Người giám hộ", options_query="parties.options")
    source: str = field("Căn cứ ngày sinh", "", max_length=120, example="Giấy khai sinh")


@m.command("add_dependant", resource="parties.contacts", action="manage", title="Thêm người được giám hộ", agent="propose")
async def add_dependant(ctx: Ctx, inp: AddDependant) -> dict[str, Any]:
    """Trẻ chưa có liên hệ riêng (``ensure_person`` bắt buộc có liên hệ): tạo định danh ngoài danh mục chọn, ghi ngày sinh và quan hệ ``guardian_of``
    còn hiệu lực trong CÙNG lần ghi (0112). Gói ngành gọi qua hợp đồng nội bộ ``add_dependant_for`` (họ ``identity``); đây là lệnh của người quản lý
    liên hệ."""
    if not ctx.p.has_scope("parties.contacts", "all"):
        raise Denied("Cần phạm vi cả công ty")
    if not inp.name.strip():
        raise Invalid("Cần tên")
    guardian = await _root(ctx, inp.guardian_party_id)
    if await ctx.val("SELECT kind FROM parties.parties WHERE id = %s", (guardian,)) != "person":
        raise Invalid("Người giám hộ phải là cá nhân")
    on, rule = today(), age_rule(today())
    if inp.born_on > on:
        raise Invalid("Ngày sinh ở tương lai")
    if rule and age_on(inp.born_on, on) >= rule.majority:
        raise Invalid(f"Người này đã đủ {rule.majority} tuổi: thêm như người thường, không qua giám hộ")
    pid = await ctx.val("INSERT INTO parties.parties (kind, display_name, listed) VALUES ('person', %s, false) RETURNING id", (inp.name.strip(),))
    await ctx.exec("INSERT INTO parties.birth_dates (party_id, born_on, source, recorded_by) VALUES (%s, %s, %s, %s)",
                   (pid, inp.born_on, inp.source, ctx.p.user_id))
    rid = await ctx.val("""INSERT INTO parties.relations (party_id, related_party_id, kind, created_by) VALUES (%s, %s, 'guardian_of', %s)
                           RETURNING id""", (inp.guardian_party_id, pid, ctx.p.user_id))
    return {"party_id": pid, "relation_id": rid, "summary": f"Thêm {inp.name.strip()}, người giám hộ đã ghi"}


def same_name(name: str) -> str:
    """Tên để so khi khớp lại một người được giám hộ: NFC, không phân biệt hoa thường, gộp khoảng trắng."""
    return " ".join(unicodedata.normalize("NFC", name).casefold().split())


@m.command("add_dependant_for", resource="parties.contacts", title="Thêm người được giám hộ (gói khác gọi)", internal=True, agent="deny",
           family="identity",
           why="Gói khai consumes(\"identity\") ghi một người chưa có liên hệ riêng vào đối tượng của nó (vd người học của một gói bán / số dư nhập), người "
               "giám hộ là người vừa khớp qua ensure_person (người gọi chỉ có quyền trên đối tượng của gói, không có quyền liên hệ cả công ty). KHỚP LẠI trước khi tạo: người được giám hộ đang hiệu lực của cùng người giám hộ, cùng "
               "ngày sinh, cùng tên chuẩn hoá ⇒ trả người đó (gọi lại không tạo người thứ hai). Tạo mới thì cùng kiểm như lệnh thường (đủ tuổi bị từ "
               "chối, ngày sinh tương lai, người giám hộ là cá nhân). Trả party_id, created; không ngày sinh."
               " [id: module gọi tự có — guardian_party_id là người vừa khớp qua ensure_person từ LIÊN HỆ người dùng gõ — không nhận mã định danh từ đầu vào]")
async def add_dependant_for(ctx: Ctx, inp: AddDependant) -> dict[str, Any]:
    guardian = await _root(ctx, inp.guardian_party_id)
    # hai lần gọi cùng lúc cho cùng người giám hộ chạy lần lượt: lần sau thấy người lần trước vừa tạo
    await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended('parties.dependant:' || %s::text, 0))", (guardian,))
    rows = await ctx.all("""SELECT DISTINCT p.id, p.display_name, p.created_at FROM parties.relations r
                            JOIN parties.parties p ON p.id = r.related_party_id JOIN parties.birth_dates b ON b.party_id = p.id
                            WHERE r.kind = 'guardian_of' AND r.ended_on IS NULL AND r.party_id = ANY(%s) AND b.born_on = %s
                            ORDER BY p.created_at, p.id""", (await _cluster(ctx, guardian), inp.born_on))
    want = same_name(inp.name)
    for r in rows:
        if same_name(r["display_name"]) == want:
            return {"party_id": await _root(ctx, r["id"]), "created": False}
    out = await add_dependant(ctx, inp)
    return {"party_id": out["party_id"], "created": True}


@m.query("consent_summary", resource="parties.contacts", title="Trạng thái đồng ý (gói khác gọi)", internal=True, agent="deny", family="consent",
         why="Gói khai consumes(\"consent\") kiểm một người của đối tượng nó (vd người học) đã đủ đồng ý trước khi ghi đối tượng đó, sau khi chính lệnh "
             "đó tạo hoặc nhận người ấy. Trả trạng thái HIỆU LỰC "
             "theo mục đích (mã + nhãn), ai phải đồng ý theo tuổi, cờ biết tuổi và phiên bản quy định; mỗi mục đích thêm lần ghi mới nhất và ai đã nói "
             "gì THEO VAI (chính người đó / từng người giám hộ đang hiệu lực: có / không); không ngày sinh, không tên người giám hộ, không liên hệ."
             " [id: module gọi tự có — party_id do gói tự dựng ngay trong lệnh qua họ identity (người của đối tượng, người trả)]")
async def consent_summary(ctx: Ctx, inp: ClusterIn) -> dict[str, Any]:
    return _summary(await consent_states(ctx, inp.party_id))


def _summary(s: dict[str, Any]) -> dict[str, Any]:
    return {"states": {c["purpose"]: c["state"] for c in s["consents"]}, "labels": {c["purpose"]: c["state_label"] for c in s["consents"]},
            "purpose_labels": {c["purpose"]: c["label"] for c in s["consents"]},
            "detail": {c["purpose"]: {"at": c["at"], "subject": c["subject"], "guardians": [g["granted"] for g in c["guardians"]]}
                       for c in s["consents"]},
            "required": s["required"], "age_known": s["age_known"], "rule_ref": s["rule_ref"], "rule_assumed": s["rule_assumed"]}


class ClustersIn(Input):
    party_ids: list[UUID] = field("Định danh", max_length=1000)


@m.query("consent_summaries", resource="parties.contacts", title="Trạng thái đồng ý của nhiều người (gói khác gọi)", internal=True, agent="deny",
         family="consent",
         why="Gói khai consumes(\"consent\") hiện trạng thái đồng ý trên bảng / thẻ đối tượng của nó cho người ĐANG xem được các dòng đó (eval 08/11: "
             "trợ lý nói đồng ý đã rút là còn) — "
             "MỘT lần gọi cho cả bảng. Trả như consent_summary cho từng người (trạng thái hiệu lực theo mục đích, ngày ghi mới nhất, ai nói gì theo VAI); "
             "không ngày sinh, không tên người giám hộ, không liên hệ."
             " [id: người gọi thấy — party_id trên các dòng của gói gọi (vd người học của suất học) đọc bằng RLS của người xem]")
async def consent_summaries(ctx: Ctx, inp: ClustersIn) -> dict[str, dict[str, Any]]:
    return {str(pid): _summary(await consent_states(ctx, pid)) for pid in dict.fromkeys(inp.party_ids)}


class SetBirthDate(Input):
    party_id: UUID = field("Người", hidden=True)
    born_on: Day | None = field("Ngày sinh", None, help="Để trống = xoá ngày sinh đã ghi", audit=False)
    source: str = field("Căn cứ", "", max_length=120, example="Giấy khai sinh")


@m.command("set_birth_date", resource="parties.contacts", action="manage", title="Ghi ngày sinh", agent="propose")
async def set_birth_date(ctx: Ctx, inp: SetBirthDate) -> dict[str, Any]:
    """Ngày sinh thuộc liên hệ & đồng ý, không thuộc danh mục: kế toán chọn bên nhận tiền không đọc được. Nhật ký thao tác không chép giá trị."""
    if not ctx.p.has_scope("parties.contacts", "all"):
        raise Denied("Cần phạm vi cả công ty")
    kind = await ctx.val("SELECT kind FROM parties.parties WHERE id = %s", (inp.party_id,))
    if kind is None:
        raise NotFound("Không thấy cá nhân / tổ chức")
    if kind != "person":
        raise Invalid("Tổ chức không có ngày sinh")
    if inp.born_on is None:
        await ctx.exec("DELETE FROM parties.birth_dates WHERE party_id = %s", (inp.party_id,))
        return {"party_id": inp.party_id}
    if inp.born_on > today():
        raise Invalid("Ngày sinh ở tương lai")
    await ctx.exec("""INSERT INTO parties.birth_dates (party_id, born_on, source, recorded_by) VALUES (%s, %s, %s, %s)
                      ON CONFLICT (tenant_id, party_id) DO UPDATE SET born_on = EXCLUDED.born_on, source = EXCLUDED.source,
                          recorded_by = EXCLUDED.recorded_by, at = now()""", (inp.party_id, inp.born_on, inp.source, ctx.p.user_id))
    out: dict[str, Any] = {"party_id": inp.party_id}
    if (await _birth(ctx, await _cluster(ctx, inp.party_id)))[1]:
        out["warning"] = "Định danh đã gộp với người này ghi ngày sinh khác: đang tính theo ngày trẻ hơn, cần sửa cho đúng."
    return out


class AddIdentifier(Input):
    party_id: UUID = field("Người", hidden=True)
    kind: Literal["phone", "email", "zalo", "facebook", "other"] = field("Loại liên hệ", "zalo", options=IDENTIFIER_KIND)
    value: str = field("Giá trị", max_length=200)
    source: str = field("Nguồn", "", hidden=True)


async def _attach(ctx: Ctx, inp: AddIdentifier) -> dict[str, Any]:
    value = normalized(inp.kind, inp.value)
    if not value:
        raise Invalid("Cần giá trị liên hệ")
    owner = await ctx.val("SELECT party_id FROM parties.identifiers WHERE kind = %s AND value = %s", (inp.kind, value))
    root = await _root(ctx, inp.party_id)
    if owner is not None and await _root(ctx, owner) != root:
        await _note_candidate(ctx, root, await _root(ctx, owner), f"Cùng {IDENTIFIER_KIND.get(inp.kind, inp.kind)}")
        return {"party_id": inp.party_id, "kind": inp.kind, "value": value, "conflict": True}   # trả về, không báo lỗi: lỗi sẽ huỷ cả dòng nghi trùng vừa ghi
    if owner is None:
        await ctx.exec("INSERT INTO parties.identifiers (party_id, kind, value, source) VALUES (%s, %s, %s, %s)", (inp.party_id, inp.kind, value, inp.source))
    return {"party_id": inp.party_id, "kind": inp.kind, "value": value}


@m.command("add_identifier", resource="parties.contacts", action="manage", title="Thêm liên hệ", agent="propose")
async def add_identifier(ctx: Ctx, inp: AddIdentifier) -> dict[str, Any]:
    return await _attach(ctx, inp)


@m.command("attach_contact", resource="parties", title="Thêm liên hệ cho khách", internal=True, agent="deny", callers=("crm",),
           why="crm.add_contact, sau khi kiểm người gọi thấy khách đó bằng RLS của CRM. Ghi đúng một liên hệ cho party của khách đó; liên hệ của người khác ⇒ "
               "không chuyển, ghi nghi trùng. Trả loại + giá trị đã chuẩn hoá."
               " [id: người gọi thấy — party_id của khách mà crm đọc bằng RLS của người gọi]")
async def attach_contact(ctx: Ctx, inp: AddIdentifier) -> dict[str, Any]:
    return await _attach(ctx, inp)


class RemoveIdentifier(Input):
    party_id: UUID = field("Người", hidden=True)
    kind: str = field("Loại liên hệ", hidden=True)
    value: str = field("Giá trị", hidden=True)


@m.command("remove_identifier",
           purpose="Bỏ một cách liên hệ đã sai hoặc không còn dùng khỏi hồ sơ người đó.", reversible="Thêm lại liên hệ bằng “Thêm liên hệ”",
           resource="parties.contacts", action="manage", title="Bỏ liên hệ", agent="propose")
async def remove_identifier(ctx: Ctx, inp: RemoveIdentifier) -> dict[str, Any]:
    n = await ctx.exec("DELETE FROM parties.identifiers WHERE party_id = %s AND kind = %s AND value = %s", (inp.party_id, inp.kind, inp.value))
    if not n:
        raise NotFound("Không thấy liên hệ này")
    return {"party_id": inp.party_id}


class Relate(Input):
    party_id: UUID = field("Người", hidden=True)
    kind: Literal["guardian_of", "pays_for", "represents", "refers"] = field("Quan hệ", "guardian_of", options=RELATION_KIND)
    related_party_id: UUID = field("Với", options_query="parties.options")
    note: str = field("Ghi chú", "", max_length=500)


async def _relate(ctx: Ctx, inp: Relate) -> dict[str, Any]:
    if await _root(ctx, inp.party_id) == await _root(ctx, inp.related_party_id):
        raise Invalid("Hai bên là cùng một người")
    if inp.kind == "guardian_of":   # vòng quét đủ tuổi sẽ kết thúc ngay: từ chối từ đầu cho hai nơi nói cùng một điều
        rule, born = age_rule(today()), (await _birth(ctx, await _cluster(ctx, inp.related_party_id)))[0]
        if born and rule and age_on(born, today()) >= rule.majority:
            raise Invalid(f"Người này đã đủ {rule.majority} tuổi: không ghi quan hệ giám hộ")
    a, b = inp.party_id, inp.related_party_id   # ghi đúng định danh được chọn: tách lại thì quan hệ vẫn đúng người
    rid = await ctx.val("""INSERT INTO parties.relations (party_id, related_party_id, kind, note, created_by) VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (tenant_id, party_id, related_party_id, kind) WHERE ended_on IS NULL DO NOTHING RETURNING id""",
                        (a, b, inp.kind, inp.note, ctx.p.user_id))
    if rid is None:
        raise Conflict("Quan hệ này đã có")
    return {"relation_id": rid}


@m.command("relate", resource="parties.contacts", action="manage", title="Thêm quan hệ", agent="propose")
async def relate(ctx: Ctx, inp: Relate) -> dict[str, Any]:
    return await _relate(ctx, inp)


class EndRelation(Input):
    relation_id: UUID = field("Quan hệ", hidden=True)
    ended_on: Day | None = field("Kết thúc từ ngày", None, help="Trống = hôm nay")
    reason: str = field("Lý do", "", max_length=200)


@m.command("end_relation", resource="parties.contacts", action="manage", title="Kết thúc quan hệ", agent="propose")
async def end_relation(ctx: Ctx, inp: EndRelation) -> dict[str, Any]:
    n = await ctx.exec("""UPDATE parties.relations SET ended_on = coalesce(%s, core.today()), ended_by = %s, ended_reason = %s
                          WHERE id = %s AND ended_on IS NULL""", (inp.ended_on, ctx.p.user_id, inp.reason, inp.relation_id))
    if not n:
        raise NotFound("Không thấy quan hệ đang hiệu lực")
    return {"relation_id": inp.relation_id}


@m.command("end_guardians_of_age", resource="parties.contacts", title="Kết thúc giám hộ khi đủ tuổi", internal=True, agent="deny",
           sweep="""SELECT DISTINCT r.tenant_id FROM parties.relations r WHERE r.kind = 'guardian_of' AND r.ended_on IS NULL
                    AND EXISTS (SELECT 1 FROM parties.birth_dates b WHERE b.tenant_id = r.tenant_id)""")
async def end_guardians_of_age(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Giám hộ tự hết lúc người được giám hộ đủ tuổi theo bộ quy định (0112). ``pays_for`` và quan hệ khác không đụng. Vết ghi ngay trên quan hệ
    (ngày, lý do, phiên bản quy định), phát sự kiện, báo hai bên nếu có đăng nhập và báo người quản lý liên hệ để họ báo người ngoài hệ thống."""
    on = today()
    rule = age_rule(on)
    if rule is None:
        return {"ended": 0}
    rows = await ctx.all(
        """WITH RECURSIVE open AS (SELECT id AS rel, related_party_id AS party FROM parties.relations WHERE kind = 'guardian_of' AND ended_on IS NULL),
           up AS (SELECT o.rel, p.id, p.merged_into FROM open o JOIN parties.parties p ON p.id = o.party
                  UNION SELECT up.rel, p.id, p.merged_into FROM parties.parties p JOIN up ON p.id = up.merged_into),
           down AS (SELECT rel, id FROM up WHERE merged_into IS NULL
                    UNION SELECT d.rel, p.id FROM parties.parties p JOIN down d ON p.merged_into = d.id)
           SELECT d.rel, max(b.born_on) AS born_on FROM down d JOIN parties.birth_dates b ON b.party_id = d.id GROUP BY d.rel""")
    ended = 0
    for row in rows:
        if age_on(row["born_on"], on) < rule.majority:
            continue
        reached = comes_of_age(row["born_on"], rule.majority)
        rel = await ctx.one(
            """UPDATE parties.relations SET ended_on = greatest(started_on, %s), ended_reason = %s, ended_rule = %s
               WHERE id = %s AND ended_on IS NULL RETURNING id, party_id, related_party_id, ended_on""",
            (reached, f"Đủ {rule.majority} tuổi ngày {reached:%d/%m/%Y}", f"{rule.ref}:majority_age", row["rel"]))
        if rel is None:   # hai vòng quét cùng lúc: bên kia đã kết thúc, không báo lần hai
            continue
        ended += 1
        await _tell_guardian_ended(ctx, rel, rule)
    return {"ended": ended}


async def _tell_guardian_ended(ctx: Ctx, rel: dict[str, Any], rule: AgeRule) -> None:
    guardian, person = rel["party_id"], rel["related_party_id"]
    roots = await _roots(ctx, [guardian, person])
    people = {r["id"]: r for r in await ctx.all("SELECT id, display_name, user_id FROM parties.parties WHERE id = ANY(%s)", (list(roots.values()),))}
    g, c = people[roots[guardian]], people[roots[person]]
    title = f"{c['display_name']} đã đủ {rule.majority} tuổi: {g['display_name']} không còn là người giám hộ"
    body = "Từ nay chỉ chính người này quyết định đồng ý về dữ liệu của mình. Quan hệ trả tiền (nếu có) vẫn giữ."
    push, key = "Một quan hệ giám hộ vừa kết thúc", f"guardian_ended:{rel['id']}"   # màn khoá không mang tên người
    both = [c["user_id"], g["user_id"]]
    keepers = [r["user_id"] for r in await ctx.all(
        """SELECT DISTINCT mb.user_id FROM core.memberships mb JOIN core.grants gr ON gr.tenant_id = mb.tenant_id AND gr.role = mb.role
           WHERE mb.active AND gr.resource = 'parties.contacts' AND 'manage' = ANY(gr.actions)""") if r["user_id"] not in both]
    await ctx.notify(both, kind="guardian_ended", title=title, body=body, push=push, key=key)
    await ctx.notify(keepers, kind="guardian_ended", title=title, body=body + " Người chưa có đăng nhập thì nhờ báo trực tiếp.", push=push, key=key,
                     open={"query": "parties.person", "input": {"party_id": str(roots[person])}})
    ctx.emit("parties.guardian_ended", relation_id=rel["id"], guardian_party_id=guardian, party_id=person, ended_on=rel["ended_on"],
             rule=f"{rule.ref}:majority_age")


class RecordConsent(Input):
    party_id: UUID = field("Người", hidden=True)
    purpose: Literal["marketing", "care", "data_processing"] = field("Mục đích", "marketing", options=CONSENT_PURPOSE)
    granted: bool = field("Đồng ý", True, help="Bỏ chọn = rút / từ chối. Lần ghi mới nhất là trạng thái hiện tại; các lần trước vẫn giữ")
    given_by_party_id: UUID | None = field("Người giám hộ đồng ý", None, options_query="parties.options",
                                           help="Trống = chính người này. Chọn người khác thì người đó phải đang là người giám hộ của người này")
    source: str = field("Ghi nhận qua", "", max_length=120, example="Phiếu đăng ký giấy")
    note: str = field("Ghi chú", "", max_length=500)


async def _record_consent(ctx: Ctx, inp: RecordConsent) -> dict[str, Any]:
    """Mỗi dòng nói rõ của ai: ``given_by_party_id`` trống = chính người này; có = người giám hộ, phải đang có ``guardian_of`` còn hiệu lực với
    người này (xét xuyên định danh đã gộp). Ngưỡng tuổi theo bộ quy định."""
    root, ids = await _root(ctx, inp.party_id), await _cluster(ctx, inp.party_id)
    giver = inp.given_by_party_id
    if giver is not None and await _root(ctx, giver) == root:
        giver = None   # định danh khác của chính người này
    on, rule = today(), age_rule(today())
    born = (await _birth(ctx, ids))[0]
    age = age_on(born, on) if born else None
    if giver is not None:
        if not await ctx.val("""SELECT 1 FROM parties.relations WHERE kind = 'guardian_of' AND ended_on IS NULL
                                AND party_id = ANY(%s) AND related_party_id = ANY(%s) LIMIT 1""", (await _cluster(ctx, giver), ids)):
            raise Invalid("Người được chọn không phải người giám hộ đang hiệu lực của người này: ghi quan hệ giám hộ trước, hoặc để trống nếu chính người đó đồng ý")
        if age is not None and rule and age >= rule.majority:
            raise Invalid(f"Người này đã đủ {rule.majority} tuổi: chỉ chính người đó đồng ý")
    elif age is not None and rule and age < rule.dual_from:   # lời của trẻ ở tuổi này không có hiệu lực: không ghi để khỏi tưởng là đã chặn / đã mở
        raise Invalid(f"Người này chưa đủ {rule.dual_from} tuổi: người giám hộ đồng ý hoặc từ chối thay (chọn người giám hộ)")
    cid = await ctx.val("""INSERT INTO parties.consents (party_id, purpose, granted, given_by_party_id, source, note, recorded_by)
                           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (inp.party_id, inp.purpose, inp.granted, giver, inp.source, inp.note, ctx.p.user_id))   # đúng định danh được chọn: tách lại không lẫn
    return {"consent_id": cid, "party_id": inp.party_id, "granted": inp.granted}


@m.command("record_consent", resource="parties.contacts", title="Ghi sự đồng ý", agent="propose")
async def record_consent(ctx: Ctx, inp: RecordConsent) -> dict[str, Any]:
    return await _record_consent(ctx, inp)


@m.command("record_contact_consent", resource="parties", title="Ghi sự đồng ý của khách", internal=True, agent="deny", callers=("crm",), family="consent",
           why="crm.record_consent, sau khi kiểm người gọi thấy khách đó bằng RLS của CRM; gói khai consumes(\"consent\") khi ghi đối tượng của nó (vd bán "
               "gói / nhập số dư), cho người chỉ lấy từ dòng của gói mà người gọi thấy (RLS), người được giám hộ vừa tạo / khớp của người trả, hoặc chính "
               "người trả (người trả chỉ từ liên hệ qua "
               "ensure_person — không nhận mã định danh từ đầu vào), với phiếu đồng ý người bán đánh dấu (người giám hộ phải đang hiệu lực, tuổi xét như lệnh ghi thường). Thêm đúng một dòng đồng ý / "
               "rút cho party đó; ghi tên người ghi. Trả id dòng."
               " [id: người gọi thấy — party_id của khách crm đọc bằng RLS của người gọi; với gói tiêu thụ họ là người của đối tượng / người trả gói tự dựng, không nhận mã từ đầu vào]")
async def record_contact_consent(ctx: Ctx, inp: RecordConsent) -> dict[str, Any]:
    return await _record_consent(ctx, inp)


class Merge(Input):
    party_id: UUID = field("Người bị gộp", hidden=True)
    into_party_id: UUID = field("Gộp vào", options_query="parties.options")


@m.command("merge", resource="parties.contacts", action="manage", title="Gộp hai định danh là một người", agent="propose")
async def merge(ctx: Ctx, inp: Merge) -> dict[str, Any]:
    """Gộp bằng con trỏ (0111): không chuyển dòng nào ở module khác, tách lại được. Người có đăng nhập luôn là bên giữ lại."""
    if not ctx.p.has_scope("parties.contacts", "all"):
        raise Denied("Cần phạm vi cả công ty")
    await ctx.conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('parties.merge:' || core.tenant()::text, 0))")   # gộp ngược chiều đồng thời không tạo vòng
    a, b = await _root(ctx, inp.party_id), await _root(ctx, inp.into_party_id)
    if a == b:
        raise Invalid("Hai định danh này đã là một người")
    rows = {r["id"]: r for r in await ctx.all("SELECT id, kind, user_id, display_name, listed FROM parties.parties WHERE id = ANY(%s) FOR UPDATE", ([a, b],))}
    if rows[a]["kind"] != rows[b]["kind"]:
        raise Invalid("Không gộp cá nhân với tổ chức")
    if rows[a]["user_id"] and rows[b]["user_id"]:
        raise Invalid("Hai người đều có tài khoản đăng nhập — không gộp được")
    # bên giữ lại: người có đăng nhập, rồi người đang trong danh mục chọn (không làm mất bên nhận tiền khỏi ô chọn)
    loser, keeper = (b, a) if rows[a]["user_id"] or (rows[a]["listed"] and not rows[b]["listed"] and not rows[b]["user_id"]) else (a, b)
    await ctx.exec("UPDATE parties.parties SET merged_into = %s WHERE id = %s", (keeper, loser))
    await ctx.exec("""UPDATE parties.merge_candidates SET status = 'merged', decided_by = %s, decided_at = now()
                      WHERE status = 'open' AND party_id = %s AND other_party_id = %s""", (ctx.p.user_id, min(a, b), max(a, b)))
    out: dict[str, Any] = {"party_id": keeper, "merged": loser, "summary": f"Đã gộp {rows[loser]['display_name']} vào {rows[keeper]['display_name']}"}
    left = await ctx.call_system_optional("acct.party_open_vnd", party_id=loser)
    if left and any(left.values()):
        out["warning"] = (f"{rows[loser]['display_name']} còn phải trả {dong(left['owed'])}, đang ứng {dong(left['advanced'])}, phải thu hồi "
                          f"{dong(left['recovery'])}{_hidden(left)}: "
                          "các khoản này vẫn ở định danh gốc và xử lý bình thường, số dư không tự cộng với người giữ lại.")
    if (await _birth(ctx, await _cluster(ctx, keeper)))[1]:
        note = "Hai định danh ghi ngày sinh khác nhau: đang tính theo ngày trẻ hơn, cần sửa cho đúng."
        out["warning"] = f"{out['warning']} {note}" if out.get("warning") else note
    return out


class Unmerge(Input):
    party_id: UUID = field("Người bị gộp", hidden=True)


@m.command("unmerge",
           purpose="Tách hai định danh đã gộp nhầm để tiền và lịch sử trở về đúng từng người.", reversible="Gộp lại bằng “Gộp hai định danh là một người”",
           resource="parties.contacts", action="manage", title="Tách định danh đã gộp", agent="propose")
async def unmerge(ctx: Ctx, inp: Unmerge) -> dict[str, Any]:
    if not ctx.p.has_scope("parties.contacts", "all"):
        raise Denied("Cần phạm vi cả công ty")
    n = await ctx.exec("UPDATE parties.parties SET merged_into = NULL WHERE id = %s AND merged_into IS NOT NULL", (inp.party_id,))
    if not n:
        raise NotFound("Định danh này không ở trạng thái đã gộp")
    await ctx.exec("""UPDATE parties.merge_candidates SET status = 'dismissed', decided_by = %s, decided_at = now()
                      WHERE status = 'merged' AND %s IN (party_id, other_party_id)""", (ctx.p.user_id, inp.party_id))
    return {"party_id": inp.party_id}


class CandidateRef(Input):
    candidate_id: UUID = field("Cặp nghi trùng", hidden=True)


@m.command("dismiss_candidate",
           purpose="Xác nhận hai người nghi trùng không phải cùng một người để hệ thống không gợi ý gộp nữa.", reversible="Vẫn gộp được bằng “Gộp hai định danh là một người” nếu sau này thấy là cùng một người",
           resource="parties.contacts", action="manage", title="Không phải cùng một người", agent="propose")
async def dismiss_candidate(ctx: Ctx, inp: CandidateRef) -> dict[str, Any]:
    n = await ctx.exec("UPDATE parties.merge_candidates SET status = 'dismissed', decided_by = %s, decided_at = now() WHERE id = %s AND status = 'open'",
                       (ctx.p.user_id, inp.candidate_id))
    if not n:
        raise NotFound("Không thấy cặp nghi trùng đang mở")
    return {"candidate_id": inp.candidate_id}


@m.query("merge_candidates", resource="parties.contacts", min_scope="all", title="Người nghi trùng")
async def merge_candidates(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all(
        """SELECT mc.id, mc.reason, mc.party_id, mc.other_party_id, a.display_name AS name, b.display_name AS other_name, mc.created_at
           FROM parties.merge_candidates mc JOIN parties.parties a ON a.id = mc.party_id JOIN parties.parties b ON b.id = mc.other_party_id
           WHERE mc.status = 'open' AND a.merged_into IS NULL AND b.merged_into IS NULL ORDER BY mc.created_at DESC LIMIT 500""")
    return ui.table(rows, [
        ui.col("name", "Người thứ nhất", width=220), ui.col("other_name", "Người thứ hai", width=220),
        ui.col("reason", "Vì sao nghi", width=240), ui.col("created_at", "Từ lúc", "datetime"),
    ], title="Người nghi trùng", empty="Không có cặp nào nghi trùng", limit=500, kpis=[ui.kpi("Đang chờ xem", len(rows))],
        note="Hệ thống không tự gộp. Gộp xong vẫn tách lại được; tiền và lịch sử giữ nguyên ở định danh gốc.")


@m.query("person", resource="parties.contacts", min_scope="all", title="Thông tin một người")
async def person(ctx: Ctx, inp: ClusterIn) -> dict[str, Any]:
    root = await _root(ctx, inp.party_id)
    ids = await _cluster(ctx, root)
    p = await ctx.one("SELECT id, kind, display_name FROM parties.parties WHERE id = %s", (root,))
    assert p
    contacts = await ctx.all("SELECT party_id, kind, value, source FROM parties.identifiers WHERE party_id = ANY(%s) ORDER BY kind, value", (ids,))
    rels = await ctx.all(
        """SELECT r.id, r.kind, r.note, r.started_on, a.display_name AS who, b.display_name AS whom FROM parties.relations r
           JOIN parties.parties a ON a.id = r.party_id JOIN parties.parties b ON b.id = r.related_party_id
           WHERE r.ended_on IS NULL AND (r.party_id = ANY(%(i)s) OR r.related_party_id = ANY(%(i)s)) ORDER BY r.started_on DESC""", {"i": ids})
    consents = await ctx.all("""SELECT c.purpose, c.granted, c.source, c.at, g.display_name AS given_by FROM parties.consents c
                                LEFT JOIN parties.parties g ON g.id = c.given_by_party_id
                                WHERE c.party_id = ANY(%s) ORDER BY c.at DESC LIMIT 50""", (ids,))
    merged = await ctx.all("SELECT id, display_name FROM parties.parties WHERE id = ANY(%s) AND id <> %s ORDER BY display_name", (ids, root))
    ended = await ctx.all(
        """SELECT r.kind, r.ended_on, r.ended_reason, a.display_name AS who, b.display_name AS whom FROM parties.relations r
           JOIN parties.parties a ON a.id = r.party_id JOIN parties.parties b ON b.id = r.related_party_id
           WHERE r.ended_on IS NOT NULL AND (r.party_id = ANY(%(i)s) OR r.related_party_id = ANY(%(i)s)) ORDER BY r.ended_on DESC LIMIT 20""", {"i": ids})
    s = await consent_states(ctx, root)
    blocks: list[dict[str, Any]] = []
    if s["born_on"]:
        blocks.append(ui.b_text(f"**Ngày sinh:** {s['born_on']:%d/%m/%Y} ({s['age']} tuổi)"
                                + (" · các định danh đã gộp ghi khác nhau, cần sửa" if s["birth_conflict"] else ""), ""))
    blocks.append(ui.b_list([{"title": f"{IDENTIFIER_KIND.get(c['kind'], c['kind'])}: {phone_display(c['value']) if c['kind'] == 'phone' else c['value']}",
                                  "subtitle": c["source"]} for c in contacts], "Liên hệ"))
    if rels:
        blocks.append(ui.b_list([{"title": f"{r['who']} — {RELATION_KIND[r['kind']].lower()} — {r['whom']}", "subtitle": r["note"]} for r in rels], "Quan hệ"))
    if ended:   # vết: quan hệ kết thúc vẫn đọc được, kể cả khi vòng quét đủ tuổi tự kết thúc
        blocks.append(ui.b_list([{"title": f"{r['who']} — {RELATION_KIND[r['kind']].lower()} — {r['whom']}", "subtitle": r["ended_reason"],
                                  "badge": f"{r['ended_on']:%d/%m/%Y}"} for r in ended], "Quan hệ đã kết thúc"))
    if consents:
        blocks.append(ui.b_list([{"title": f"{c['label']}: {c['state_label'].lower()}", "subtitle": _who_said(c)} for c in s["consents"] if c["recorded"]],
                                "Trạng thái đồng ý"))
        blocks.append(ui.b_text(_age_note(s), ""))
        blocks.append(ui.b_list([{"title": f"{CONSENT_PURPOSE.get(c['purpose'], c['purpose'])}: {'đồng ý' if c['granted'] else 'không đồng ý'}",
                                  "subtitle": " · ".join(x for x in (c["given_by"] and f"{c['given_by']} đồng ý thay", c["source"]) if x),
                                  "badge": f"{c['at'].astimezone(zone()):%d/%m/%Y}"} for c in consents], "Lịch sử đồng ý"))
    actions = [ui.act("parties.add_identifier", "Thêm liên hệ", {"party_id": str(root)}), ui.act("parties.relate", "Thêm quan hệ", {"party_id": str(root)}),
               ui.act("parties.set_birth_date", "Ghi ngày sinh", {"party_id": str(root)}),
               ui.act("parties.record_consent", "Ghi sự đồng ý", {"party_id": str(root)})]
    actions += [ui.act("parties.unmerge", f"Tách {x['display_name']}", {"party_id": str(x["id"])}, "destructive") for x in merged]
    return ui.card(p["display_name"], blocks, subtitle=PARTY_KIND[p["kind"]], icon="person.crop.circle", actions=actions)


# Tài chính bắt buộc danh mục (0105) nên màn nằm trong menu Kế toán; công ty không dùng Tài chính thì chưa cần danh mục này.
m.screens(
    "accounting",
    (
        Screen(
            "parties",
            "Cá nhân & tổ chức",
            "person.crop.rectangle.stack",
            "parties.list",
            tint="blue",
            min_scope="all",
            group="Thiết lập",   # danh mục đối tượng — nhóm Thiết lập của menu Kế toán (rà vai kế toán 18/11/2026)
            actions=(A("parties.create", style="primary"),),
            row_actions=(A("parties.update", "Sửa", bind={"party_id": "id"}, prefill={"display_name": "display_name", "active": "active", "listed": "listed"}),),
            row_click="open", open=Open("parties.person", {"party_id": "id"}, "Thông tin người"),
        ),
        Screen(
            "party_duplicates",
            "Người nghi trùng",
            "person.2.badge.gearshape",
            "parties.merge_candidates",
            tint="orange",
            min_scope="all",
            group="Thiết lập",   # danh mục đối tượng — nhóm Thiết lập của menu Kế toán (rà vai kế toán 18/11/2026)
            row_actions=(A("parties.merge", "Gộp", bind={"party_id": "party_id", "into_party_id": "other_party_id"}, style="primary"),
                         A("parties.dismiss_candidate", "Không trùng", bind={"candidate_id": "id"})),
        ),
    ),
    rank=90,
)
