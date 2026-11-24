"""QUẢN TRỊ — người dùng, cây tổ chức, vai trò & ma trận quyền, chuỗi duyệt, luật duyệt, nhật ký, sự kiện lỗi.

Mọi thứ ở đây là DỮ LIỆU công ty tự sửa (không cần lập trình viên): thêm vai trò "trưởng chi nhánh", cho sale xem
khách của cả đội, bắt huỷ giao dịch phải qua giám đốc… Bất biến giữ bằng code: luôn còn ít nhất một người quản trị và một CHỦ TÀI KHOẢN
đang hoạt động (0029). Quản trị không tự nâng quyền: đổi quyền / vai / luật / chuỗi duyệt / chủ tài khoản / trưởng đội đi qua chuỗi
"access_change" → chủ tài khoản (luật mặc định của gói nền; ``sensitive`` của từng lệnh nói lần gọi nào thuộc luật + mô tả cho người duyệt).
"""

import base64
import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.api import Action as A
from barista.api import (
    Ctx,
    Day,
    Denied,
    Event,
    ImportKey,
    Input,
    Invalid,
    Module,
    NoInput,
    NotFound,
    Open,
    Screen,
    Workspace,
    field,
    files,
    today,
    ui,
    zone,
)
from barista.db import db
from barista.kernel import (
    alerts,
    clock,
    company_settings,
    executor,
    extra_fields,
    operator_door,
    pack_catalog,
    packs,
    registry,
    screens,
    skill_specs,
    vocab,
)
from barista.kernel import teams as team_tree
from barista.kernel.fields import DEFAULT_COUNTRY, NATIONAL, Money
from barista.kernel.forms import field_specs
from barista.kernel.principal import ACTIONS, system_principal
from barista.kernel.text import code_from_name, snake_from_name

m = Module("admin", "Quản trị", pack="base")

SCOPE_LABEL = {"own": "Của mình", "unit": "Đội mình thuộc", "team": "Đội mình quản", "all": "Cả công ty"}
ACTION_LABEL = {"read": "xem", "write": "ghi", "approve": "duyệt", "manage": "quản lý"}
TEAM_KINDS = {"company": "Công ty", "branch": "Chi nhánh", "sales": "Kinh doanh", "marketing": "Marketing",
              "accounting": "Kế toán", "admin": "Hành chính / quản trị"}


async def _ensure_admin_remains(ctx: Ctx) -> None:
    """Phải còn ít nhất một quản trị. Khoá chung theo công ty TRƯỚC khi đếm: hai quản trị cùng tự hạ quyền một lúc ⇒ người sau
    chờ người trước commit, lần đếm của người sau (READ COMMITTED, câu lệnh mới) thấy thay đổi đó ⇒ bị chặn."""
    await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended('admin-remains:' || core.tenant()::text, 0))")
    n = await ctx.val(
        """SELECT count(*) FROM core.memberships mb JOIN core.grants g ON g.tenant_id = mb.tenant_id AND g.role = mb.role
           WHERE mb.active AND g.resource = 'core.admin' AND g.scope = 'all' AND 'manage' = ANY(g.actions)""")
    if not n:
        raise Invalid("Phải còn ít nhất một người đang hoạt động có quyền quản trị hệ thống")


async def _ensure_owner_remains(ctx: Ctx) -> None:
    """Phải còn ít nhất một chủ tài khoản đang hoạt động (0029) — không còn thì không ai duyệt được thay đổi quyền. Khoá như ``_ensure_admin_remains``."""
    await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended('owner-remains:' || core.tenant()::text, 0))")
    if not await ctx.val("SELECT count(*) FROM core.memberships WHERE owner AND active"):
        raise Invalid("Phải còn ít nhất một chủ tài khoản đang hoạt động — giao chủ tài khoản cho người khác trước")


async def _first_owner(ctx: Ctx) -> list[UUID]:
    """Công ty CHƯA có chủ tài khoản đang hoạt động (công ty mới, công ty có trước 0029) ⇒ gán theo quy tắc cố định (0029): người đang hoạt
    động giữ vai gói khai ``account_owner`` (gói nền: giám đốc); không có ⇒ mọi người đang hoạt động có quyền quản trị hệ thống (core.admin ·
    cả công ty · quản lý). Đã có chủ tài khoản ⇒ không đổi gì. Trả người vừa được gán."""
    from barista import presets

    await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended('owner-remains:' || core.tenant()::text, 0))")
    if await ctx.val("SELECT 1 FROM core.memberships WHERE owner AND active LIMIT 1"):
        return []
    ids = [r["user_id"] for r in await ctx.all("SELECT user_id FROM core.memberships WHERE active AND role = ANY(%s) ORDER BY created_at",
                                               (presets.owner_roles(),))]
    if not ids:
        ids = [r["user_id"] for r in await ctx.all(
            """SELECT mb.user_id FROM core.memberships mb JOIN core.grants g ON g.tenant_id = mb.tenant_id AND g.role = mb.role
               WHERE mb.active AND g.resource = 'core.admin' AND g.scope = 'all' AND 'manage' = ANY(g.actions) ORDER BY mb.created_at""")]
    if ids:
        await ctx.exec("UPDATE core.memberships SET owner = true WHERE user_id = ANY(%s)", (ids,))
    return ids


async def _role_ref(ctx: Ctx, key: str) -> str:
    """Vai trên phiếu duyệt: nhãn LÚC XIN + MÃ vai (review 08/11) — nhãn là chữ quản trị sửa được, mã thì không; người duyệt thấy đúng vai thật."""
    label = await ctx.val("SELECT label FROM core.roles WHERE key = %s", (key,))
    return f"{label} [{key}]" if label and label != key else f"[{key}]"


async def _member_state(ctx: Ctx, email: str) -> dict[str, Any] | None:
    """Trạng thái quyền của một thành viên: vai, đang làm, chủ tài khoản, có quyền quản trị, đội chính, trưởng đội nào."""
    return await ctx.one(
        """SELECT u.id, u.name, mb.role, mb.active, mb.owner,
                  EXISTS (SELECT 1 FROM core.grants g WHERE g.role = mb.role AND g.resource = 'core.admin' AND g.scope = 'all'
                          AND 'manage' = ANY(g.actions)) AS admin,
                  EXISTS (SELECT 1 FROM core.team_members tm WHERE tm.user_id = mb.user_id AND tm.is_lead) AS leads,
                  (SELECT t.code FROM core.team_members tm JOIN core.teams t ON t.id = tm.team_id WHERE tm.user_id = mb.user_id AND tm.is_home) AS home_code,
                  coalesce((SELECT tm.is_lead FROM core.team_members tm WHERE tm.user_id = mb.user_id AND tm.is_home), false) AS home_lead
           FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant() WHERE u.email = %s""",
        (email.lower(),))


async def _role_is_admin(ctx: Ctx, role: str) -> bool:
    return bool(await ctx.val("SELECT 1 FROM core.grants WHERE role = %s AND resource = 'core.admin' AND scope = 'all' AND 'manage' = ANY(actions)",
                              (role,)))


def _who(m: dict[str, Any], email: str) -> str:
    return f"{m['name'] or email}: "


async def _team_id(ctx: Ctx, code: str | None) -> Any:
    if not code:
        return None
    tid = await ctx.val("SELECT id FROM core.teams WHERE code = %s", (code,))
    if tid is None:
        raise NotFound(f"Không có đội mã {code}")
    return tid


# ── Người dùng ──

@m.query("users", resource="core.admin", title="Người dùng")
async def users(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all(
        """SELECT u.id, u.email, u.name, u.phone, mb.role, r.label AS role_label, mb.owner, t.code AS team_code, t.name AS team,
                  coalesce(tm.is_lead, false) AS is_lead, mb.active, mb.created_at,
                  (SELECT string_agg(ot.name, ', ' ORDER BY ot.name) FROM core.team_members o JOIN core.teams ot ON ot.id = o.team_id
                   WHERE o.tenant_id = mb.tenant_id AND o.user_id = mb.user_id AND NOT o.is_home) AS other_teams,
                  idn.last_login_at, (idn.id IS NOT NULL) AS google_linked
           FROM core.memberships mb JOIN core.users u ON u.id = mb.user_id
           LEFT JOIN core.identities idn ON idn.user_id = u.id AND idn.provider = 'google'
           JOIN core.roles r ON r.tenant_id = mb.tenant_id AND r.key = mb.role
           LEFT JOIN core.team_members tm ON tm.tenant_id = mb.tenant_id AND tm.user_id = mb.user_id AND tm.is_home
           LEFT JOIN core.teams t ON t.id = tm.team_id
           ORDER BY mb.active DESC, t.code NULLS LAST, tm.is_lead DESC, u.name""")
    for r in rows:
        r["lead"] = "Trưởng" if r["is_lead"] else ""
        r["state"] = "active" if r["active"] else "inactive"
        r["google"] = "linked" if r.pop("google_linked") else "none"
        r["owner_badge"] = "Chủ tài khoản" if r["owner"] else ""
    active = sum(1 for r in rows if r["active"])
    owners = sum(1 for r in rows if r["active"] and r["owner"])
    return ui.table(rows, [
        ui.col("name", "Họ tên"), ui.col("email", "Email"), ui.col("role_label", "Vai trò"),
        ui.col("owner_badge", "", "badge", tones={"Chủ tài khoản": "purple"}),
        ui.col("team", "Đội chính"), ui.col("other_teams", "Đội khác"),
        ui.col("lead", "", "badge", tones={"Trưởng": "blue"}),
        ui.col("state", "Trạng thái", "badge", labels={"active": "Đang làm", "inactive": "Đã khoá"},
               tones={"active": "green", "inactive": "gray"}),
        ui.col("google", "Google", "badge", labels={"linked": "Đã liên kết", "none": "Chưa đăng nhập"}, tones={"linked": "blue", "none": "gray"}),
        ui.col("last_login_at", "Đăng nhập gần nhất", "datetime"),
    ], title="Người dùng", kpis=[ui.kpi("Đang hoạt động", active), ui.kpi("Đã khoá", len(rows) - active),
                                 ui.kpi("Chủ tài khoản", owners, tone="orange" if owners < 2 else None,
                                        hint="Chỉ có một chủ tài khoản: thay đổi quyền của người đó chạy không qua người duyệt thứ hai — "
                                             "giao thêm một người (Sửa người dùng › Chủ tài khoản)" if owners < 2 else "")],
        note="Đổi vai, quyền, luật duyệt, chủ tài khoản hoặc trưởng đội phải qua chủ tài khoản duyệt (người xin không tự duyệt)."
             + (" Công ty mới có MỘT chủ tài khoản — nên giao thêm người thứ hai." if owners < 2 else ""))


class Invite(Input):
    email: str = field("Email", kind="email", example="sale4@congty.vn", max_length=200)
    name: str = field("Họ tên", example="Trần Văn A", max_length=120)
    role: str = field("Vai trò", options_query="admin.role_options", example="sale")
    team_code: str = field("Đội", "", options_query="admin.team_options", example="KD-A")
    is_lead: bool = field("Trưởng đội", False)


async def _invite_access(ctx: Ctx, inp: Invite) -> str | None:
    """Mời người MỚI với vai NHẬP MÔN (``core.roles.entry`` — mặc định gói khai, công ty sửa), không làm trưởng ⇒ vận hành thường, chạy thẳng.
    Phải duyệt (0029) khi: vai không phải vai nhập môn hoặc có quyền quản trị (tài khoản mới với vai rộng / làm trưởng đội = thêm một người duyệt
    — review 08/11), làm trưởng đội; email ĐÃ là thành viên mà vai đổi / đang bị khoá / cờ trưởng đổi (kể cả mời lại vào đội khác làm mất cờ
    trưởng) — như sửa người dùng."""
    out: list[str] = []
    m = await _member_state(ctx, inp.email)
    if m is not None:
        if m["role"] != inp.role or not m["active"]:
            out.append(f"{'mở lại tài khoản, ' if not m['active'] else ''}vai {await _role_ref(ctx, m['role'])} → {await _role_ref(ctx, inp.role)}")
        if inp.team_code and ((inp.team_code != m["home_code"] and (m["home_lead"] or inp.is_lead))
                              or (inp.team_code == m["home_code"] and inp.is_lead != m["home_lead"])):
            out.append(f"trưởng đội {m['home_code'] or '—'}{' (bỏ)' if m['home_lead'] else ''} → đội {inp.team_code}{' (trưởng)' if inp.is_lead else ''}")
    else:
        entry = await ctx.val("SELECT entry FROM core.roles WHERE key = %s", (inp.role,))
        if not entry or await _role_is_admin(ctx, inp.role):
            out.append(f"tài khoản mới với vai {await _role_ref(ctx, inp.role)}"
                       + (" (quản trị hệ thống)" if await _role_is_admin(ctx, inp.role) else " (không phải vai nhập môn)"))
        if inp.is_lead and inp.team_code:
            out.append(f"làm trưởng đội {inp.team_code}")
    return f"{inp.name or inp.email} <{inp.email.lower()}>: " + "; ".join(out) if out else None


@m.command("invite_user",
           purpose="Cho người mới vào công ty với đúng vai trò và đội để bắt đầu làm việc.", reversible="Khoá tài khoản ở Sửa người dùng (bỏ chọn Đang làm việc)",
           resource="core.admin", action="manage", title="Thêm người dùng", importable=True, sensitive=_invite_access)
async def invite_user(ctx: Ctx, inp: Invite) -> dict[str, Any]:
    if not await ctx.val("SELECT 1 FROM core.roles WHERE key = %s", (inp.role,)):
        raise NotFound(f"Không có vai trò {inp.role}")
    uid = await ctx.val("SELECT core.ensure_user(%s, %s)", (inp.email, inp.name))
    before = await ctx.val("SELECT role FROM core.memberships WHERE user_id = %s", (uid,))
    await ctx.exec("UPDATE core.users SET name = %s WHERE id = %s AND name = ''", (inp.name, uid))
    await ctx.exec(
        """INSERT INTO core.memberships (user_id, role) VALUES (%s, %s)
           ON CONFLICT (tenant_id, user_id) DO UPDATE SET role = EXCLUDED.role, active = true""", (uid, inp.role))
    if tid := await _team_id(ctx, inp.team_code):
        await _set_home_team(ctx, uid, tid, inp.is_lead)
    return {"user_id": uid, "email": inp.email.lower(), "subject": await _person(ctx, uid, inp.email),
            "changes": [{"label": "Vai trò", "before": await _role_ref(ctx, before) if before else "Chưa có trong công ty", "after": await _role_ref(ctx, inp.role)}]}


async def _step_down(ctx: Ctx, tid: UUID, keep: UUID | None = None) -> UUID | None:
    """Một đội một trưởng tại một thời điểm (0256): trưởng đang giữ (khác ``keep``) thành thành viên thường. Trả người vừa thôi."""
    return await ctx.val("UPDATE core.team_members SET is_lead = false WHERE team_id = %s AND is_lead AND user_id IS DISTINCT FROM %s RETURNING user_id",
                         (tid, keep))


def _since(inp_from: date | None, reason: str) -> tuple[date | None, str]:
    """Ngày hiệu lực người dùng nêu ⇒ (ngày lùi hoặc None nếu là hôm nay / bỏ trống, lý do lưu). Tương lai và lùi quá hạn mức thiếu lý do ⇒ từ chối."""
    if inp_from is None or inp_from == today():
        return None, ""
    if inp_from > today():
        raise Invalid("Chưa ghi trước cho tương lai được: thay đổi cơ cấu có hiệu lực từ lúc ghi")
    why = team_tree.backdate_reason(inp_from, reason, today())
    if why is None:
        raise Invalid(f"Lùi ngày hiệu lực quá {team_tree.BACKDATE_DAYS} ngày (từ {inp_from:%d/%m/%Y}) cần ghi lý do — lịch sử cơ cấu ảnh hưởng người hưởng "
                      "hoa hồng đã dựng từ nó")
    return inp_from, why


async def _set_home_team(ctx: Ctx, uid: UUID, tid: UUID, lead: bool) -> None:
    """Đội chính: đơn vị mặc định của hồ sơ người đó tạo. Đổi đội chính thay dòng cũ; các đội tham gia khác giữ nguyên (0109)."""
    left = await ctx.all("DELETE FROM core.team_members WHERE user_id = %s AND is_home AND team_id <> %s RETURNING team_id", (uid, tid))
    if lead:
        await _step_down(ctx, tid, keep=uid)
    await ctx.exec(
        """INSERT INTO core.team_members (user_id, team_id, is_lead, is_home) VALUES (%s, %s, %s, true)
           ON CONFLICT (tenant_id, user_id, team_id) DO UPDATE SET is_lead = EXCLUDED.is_lead, is_home = true""",
        (uid, tid, lead))
    for t in dict.fromkeys([*(r["team_id"] for r in left), tid]):
        ctx.emit("admin.team_members_changed", team_id=t)


class TeamMemberRef(Input):
    email: str = field("Email", hidden=True)
    team_code: str = field("Đội / cơ sở", options_query="admin.team_options")


class AddTeam(TeamMemberRef):
    is_lead: bool = field("Trưởng đội này", False)


async def _person(ctx: Ctx, uid: UUID, email: str) -> dict[str, Any]:
    """Hồ sơ người cho thẻ duyệt (ADR 0253): tên + email, chưa có màn riêng để mở."""
    name = await ctx.val("SELECT name FROM core.users WHERE id = %s", (uid,))
    return {"label": f"{name} · {email.lower()}" if name else email.lower(), "open": None}


async def _member_id(ctx: Ctx, email: str) -> UUID:
    uid = await ctx.val("SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant() WHERE u.email = %s", (email.lower(),))
    if uid is None:
        raise NotFound(f"Không có thành viên {email}")
    return uid


async def _lead_row(ctx: Ctx, email: str, code: str) -> dict[str, Any] | None:
    return await ctx.one("""SELECT u.name, tm.is_lead FROM core.users u JOIN core.team_members tm ON tm.user_id = u.id
                            JOIN core.teams t ON t.id = tm.team_id WHERE u.email = %s AND t.code = %s""", (email.lower(), code))


async def _replaces(ctx: Ctx, code: str, newcomer: str) -> str:
    """MỘT đội MỘT trưởng (0256): đặt trưởng mới là thay người đang giữ — phiếu duyệt nói rõ ai bị thay (người duyệt quyết cả hai việc, không chỉ một).
    Dùng chung cho mọi đường đặt trưởng (ghi trưởng đội, sửa người dùng, thêm vào đội)."""
    row = await ctx.one("""SELECT u.name, u.email FROM core.team_members tm JOIN core.teams t ON t.id = tm.team_id JOIN core.users u ON u.id = tm.user_id
                           WHERE t.code = %s AND tm.is_lead""", (code,))
    if row is None or row["email"].lower() == newcomer.strip().lower():
        return ""
    return f" (thay {row['name'] or row['email']}, người này thành thành viên thường)"


async def _add_team_access(ctx: Ctx, inp: AddTeam) -> str | None:
    """Trưởng đội có phạm vi "đội mình quản" trên cả cây con và là người duyệt bước "trưởng nhóm" ⇒ đặt / bỏ trưởng là đổi quyền (0029);
    thêm thành viên thường thì không."""
    row = await _lead_row(ctx, inp.email, inp.team_code)
    if inp.is_lead == bool(row and row["is_lead"]):
        return None
    return f"{(row or {}).get('name') or inp.email}: {'làm' if inp.is_lead else 'thôi'} trưởng đội {inp.team_code}" + (
        await _replaces(ctx, inp.team_code, inp.email) if inp.is_lead else "")


async def _remove_team_access(ctx: Ctx, inp: TeamMemberRef) -> str | None:
    """Bỏ một TRƯỞNG khỏi đội ⇒ người duyệt bước "trưởng nhóm" của đội đó đổi (lên đội cha) ⇒ phải duyệt (0029); bỏ thành viên thường thì không."""
    row = await _lead_row(ctx, inp.email, inp.team_code)
    return f"{row['name'] or inp.email}: bỏ khỏi đội {inp.team_code} (đang là trưởng)" if row and row["is_lead"] else None


@m.command("add_team",
           purpose="Một người làm ở nhiều đội / cơ sở cần thấy việc chung và nhận thông báo của đội đó.", reversible="Bấm Bỏ khỏi đội / cơ sở",
           resource="core.admin", action="manage", title="Thêm vào đội / cơ sở khác", agent="propose", sensitive=_add_team_access)
async def add_team(ctx: Ctx, inp: AddTeam) -> dict[str, Any]:
    """Một người làm ở nhiều đội (vd một người làm ở hai chi nhánh, quản lý vùng): thấy việc chung và nhận thông báo của đội đó; là trưởng thì có phạm vi "team" trên đội đó.
    Hồ sơ người đó tạo vẫn mặc định thuộc đội chính. Chưa có đội nào thì đây thành đội chính."""
    uid = await _member_id(ctx, inp.email)
    tid = await _team_id(ctx, inp.team_code)
    if tid is None:
        raise Invalid("Cần chọn đội")
    home = not await ctx.val("SELECT 1 FROM core.team_members WHERE user_id = %s AND is_home", (uid,))
    if inp.is_lead:
        await _step_down(ctx, tid, keep=uid)
    await ctx.exec("""INSERT INTO core.team_members (user_id, team_id, is_lead, is_home) VALUES (%s, %s, %s, %s)
                      ON CONFLICT (tenant_id, user_id, team_id) DO UPDATE SET is_lead = EXCLUDED.is_lead""", (uid, tid, inp.is_lead, home))
    ctx.emit("admin.team_members_changed", team_id=tid)
    return {"user_id": uid, "team_id": tid, "is_home": home, "subject": await _person(ctx, uid, inp.email),
            "changes": [{"label": "Đội / cơ sở", "before": "", "after": inp.team_code + (" (trưởng)" if inp.is_lead else "")}]}


@m.command("remove_team",
           purpose="Người này không còn làm ở đội / cơ sở đó — thu hẹp việc và thông báo họ thấy.", reversible="Thêm lại bằng Thêm vào đội / cơ sở khác",
           resource="core.admin", action="manage", title="Bỏ khỏi đội / cơ sở", agent="propose", sensitive=_remove_team_access)
async def remove_team(ctx: Ctx, inp: TeamMemberRef) -> dict[str, Any]:
    uid = await _member_id(ctx, inp.email)
    tid = await _team_id(ctx, inp.team_code)
    row = await ctx.one("SELECT is_home FROM core.team_members WHERE user_id = %s AND team_id = %s", (uid, tid))
    if row is None:
        raise NotFound("Người này không ở đội đó")
    if row["is_home"] and await ctx.val("SELECT 1 FROM core.team_members WHERE user_id = %s AND NOT is_home", (uid,)):
        raise Invalid("Đây là đội chính — đổi đội chính sang đội khác trước")
    await ctx.exec("DELETE FROM core.team_members WHERE user_id = %s AND team_id = %s", (uid, tid))
    ctx.emit("admin.team_members_changed", team_id=tid)
    return {"user_id": uid, "team_id": tid, "subject": await _person(ctx, uid, inp.email),
            "changes": [{"label": "Đội / cơ sở", "before": inp.team_code, "after": "Đã bỏ"}]}


class TeamLead(Input):
    team_code: str = field("Đội", options_query="admin.team_options")
    email: str = field("Trưởng đội", "", options_query="admin.member_options", placeholder="Bỏ trống chức trưởng đội", help="Để trống = đội khuyết trưởng từ ngày này")
    valid_from: Day | None = field("Từ ngày", None, example="01/11/2026", placeholder="Hôm nay", help="Trưởng trước đó thôi từ ngày này")
    reason: str = field("Lý do lùi ngày", "", max_length=300, help=f"Bắt buộc khi lùi quá {team_tree.BACKDATE_DAYS} ngày so với hôm nay (ghi vào nhật ký)",
                        group="Thêm chi tiết")


async def _team_lead_access(ctx: Ctx, inp: TeamLead) -> str | None:
    """Đặt / bỏ trưởng đội là đổi quyền (phạm vi "đội mình quản", người duyệt bước trưởng nhóm — 0029) và đổi người hưởng theo cơ cấu (0160)."""
    return f"đội {inp.team_code}: {'trưởng mới ' + inp.email.lower() if inp.email.strip() else 'bỏ trống chức trưởng'}" + (
        f" từ {inp.valid_from:%d/%m/%Y}" if inp.valid_from else "") + (await _replaces(ctx, inp.team_code, inp.email) if inp.email.strip() else "")


@m.command("set_team_lead",
           purpose="Ghi ai đứng đầu một đội và từ ngày nào — quyền xem cả đội và người hưởng theo cơ cấu cùng đọc dòng này.",
           reversible="Chạy lại lệnh với trưởng trước đó",
           resource="core.admin", action="manage", title="Ghi trưởng đội từ ngày", agent="propose", sensitive=_team_lead_access)
async def set_team_lead(ctx: Ctx, inp: TeamLead) -> dict[str, Any]:
    """MỘT nơi ghi trưởng đội (ADR 0256): hiện tại ở ``core.team_members`` (quyền đọc), lịch sử có ngày do DB tự chép sang ``core.team_terms`` (hoa hồng
    đọc theo ngày ký). Một đội một trưởng: trưởng cũ thành thành viên thường. Người chưa ở đội được thêm vào đội (đội chính nếu chưa có đội nào)."""
    tid = await _team_id(ctx, inp.team_code)
    if tid is None:
        raise Invalid("Cần chọn đội")
    day, why = _since(inp.valid_from, inp.reason)
    current = await ctx.val("SELECT user_id FROM core.team_members WHERE team_id = %s AND is_lead", (tid,))
    if not inp.email.strip():
        if current is None:
            raise Invalid("Đội đang không có trưởng")
        await _step_down(ctx, tid)
        if day:
            await team_tree.until(ctx.conn, current, tid, day, why)
        uid = None
    else:
        uid = await _member_id(ctx, inp.email)
        if current == uid and day is None:
            raise Invalid("Người này đang là trưởng đội")
        if current != uid:
            await _step_down(ctx, tid, keep=uid)
            home = not await ctx.val("SELECT 1 FROM core.team_members WHERE user_id = %s AND is_home", (uid,))
            await ctx.exec("""INSERT INTO core.team_members (user_id, team_id, is_lead, is_home) VALUES (%s, %s, true, %s)
                              ON CONFLICT (tenant_id, user_id, team_id) DO UPDATE SET is_lead = true""", (uid, tid, home))
        if day:
            await team_tree.since(ctx.conn, uid, tid, day, why)
    ctx.emit("admin.team_members_changed", team_id=tid)
    when = f"{(inp.valid_from or today()):%d/%m/%Y}"
    return {"team_id": tid, "user_id": uid, "summary": f"{inp.team_code}: {'khuyết trưởng' if uid is None else 'trưởng mới'} từ {when}",
            "changes": [{"label": "Trưởng đội", "before": "", "after": (inp.email.lower() or "Khuyết") + f" (từ {when})"}]}


class TeamSince(TeamMemberRef):
    valid_from: Day = field("Ở đội này từ ngày", example="01/11/2026")
    reason: str = field("Lý do lùi ngày", "", max_length=300, help=f"Bắt buộc khi lùi quá {team_tree.BACKDATE_DAYS} ngày so với hôm nay (ghi vào nhật ký)")


@m.command("set_team_since",
           purpose="Sửa ngày một người bắt đầu ở đội khi ghi muộn — người hưởng theo cơ cấu tính theo ngày ký, không theo ngày nhập.",
           reversible="Chạy lại với ngày đúng (chỉ lùi được về trước)",
           resource="core.admin", action="manage", title="Sửa ngày vào đội", agent="propose")
async def set_team_since(ctx: Ctx, inp: TeamSince) -> dict[str, Any]:
    """Chỉ sửa LỊCH SỬ (``core.team_terms``): quyền hôm nay không đổi. Dòng liền trước mà dòng này thay chỗ (đội chính cũ) rút ngắn theo."""
    uid = await _member_id(ctx, inp.email)
    tid = await _team_id(ctx, inp.team_code)
    if tid is None or not await ctx.val("SELECT 1 FROM core.team_members WHERE user_id = %s AND team_id = %s", (uid, tid)):
        raise NotFound("Người này không ở đội đó")
    day, why = _since(inp.valid_from, inp.reason)
    if day:
        await team_tree.since(ctx.conn, uid, tid, day, why)
    return {"user_id": uid, "team_id": tid, "subject": await _person(ctx, uid, inp.email),
            "changes": [{"label": "Ở đội từ", "before": "", "after": f"{inp.valid_from:%d/%m/%Y}"}]}


class UpdateMember(Input):
    email: str = field("Email", hidden=True)
    role: str | None = field("Vai trò", None, options_query="admin.role_options")
    team_code: str | None = field("Đội", None, options_query="admin.team_options")
    is_lead: bool | None = field("Trưởng đội", None)
    active: bool | None = field("Đang làm việc", None, help="Bỏ chọn = khoá tài khoản (giữ lịch sử)")
    owner: bool | None = field("Chủ tài khoản", None, help="Duyệt thay đổi quyền, vai, luật duyệt của công ty — kiêm cùng vai đang có")


async def _member_access(ctx: Ctx, inp: UpdateMember) -> str | None:
    """Lần sửa nào là đổi QUYỀN (0029) ⇒ phải qua chủ tài khoản: đổi vai; giao / bỏ chủ tài khoản; đặt / bỏ trưởng đội, hoặc chuyển đội chính
    của một trưởng đội; khoá / mở lại tài khoản của chủ tài khoản, quản trị hoặc trưởng đội. Đổi đội của nhân viên thường, khoá nhân viên nghỉ việc
    ⇒ chạy thẳng (thu hẹp quyền / vận hành thường)."""
    m = await _member_state(ctx, inp.email)
    if m is None:
        return None   # handler báo "không có thành viên"
    out: list[str] = []
    if inp.role is not None and inp.role != m["role"]:
        out.append(f"vai {await _role_ref(ctx, m['role'])} → {await _role_ref(ctx, inp.role)}")
    if inp.owner is not None and inp.owner != m["owner"]:
        out.append("giao chủ tài khoản" if inp.owner else "bỏ chủ tài khoản")
    if inp.active is not None and inp.active != m["active"] and (m["owner"] or m["admin"] or m["leads"]):
        out.append(("mở lại" if inp.active else "khoá") + " tài khoản (" + ", ".join(
            x for x, on in (("chủ tài khoản", m["owner"]), ("quản trị", m["admin"]), ("trưởng đội", m["leads"])) if on) + ")")
    lead_changed = inp.is_lead is not None and inp.is_lead != m["home_lead"]
    team_changed = inp.team_code is not None and inp.team_code != m["home_code"]
    if lead_changed:
        code = inp.team_code or m["home_code"] or ""
        out.append(f"{'làm' if inp.is_lead else 'thôi'} trưởng đội {code}".rstrip() + (await _replaces(ctx, code, inp.email) if inp.is_lead and code else ""))
    elif team_changed and m["home_lead"]:
        out.append(f"trưởng đội chuyển đội chính {m['home_code'] or '—'} → {inp.team_code}")
    return _who(m, inp.email) + "; ".join(out) if out else None


@m.command("update_member",
           purpose="Đổi vai trò, đội hoặc trạng thái làm việc của một người cho khớp công việc hiện tại.", reversible="Chạy lại lệnh với vai trò, đội, trạng thái cũ",
           resource="core.admin", action="manage", title="Sửa người dùng", sensitive=_member_access)
async def update_member(ctx: Ctx, inp: UpdateMember) -> dict[str, Any]:
    uid = await ctx.val(
        "SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()"
        " WHERE u.email = %s", (inp.email.lower(),))
    if uid is None:
        raise NotFound(f"Không có thành viên {inp.email}")
    was = await ctx.one("SELECT role, active, owner FROM core.memberships WHERE user_id = %s", (uid,))
    if inp.role is not None:
        if not await ctx.val("SELECT 1 FROM core.roles WHERE key = %s", (inp.role,)):
            raise NotFound(f"Không có vai trò {inp.role}")
        await ctx.exec("UPDATE core.memberships SET role = %s WHERE user_id = %s", (inp.role, uid))
    if inp.active is not None:
        await ctx.exec("UPDATE core.memberships SET active = %s WHERE user_id = %s", (inp.active, uid))
        if not inp.active:   # nghỉ việc: gói khác thu hồi phần của mình (vd công ty khách của dịch vụ kế toán — 0063)
            ctx.emit("admin.member_deactivated", user_id=uid)
        # khoá / mở lại tài khoản đổi danh sách người ĐANG LÀM của mọi đội người đó thuộc (0128: phòng của đội đồng bộ theo đó)
        for team_id in await team_tree.member_of(ctx.conn, ctx.p.tenant_id, uid):   # một định nghĩa "đội của một người" (0062)
            ctx.emit("admin.team_members_changed", team_id=team_id)
    if inp.team_code is not None or inp.is_lead is not None:
        current = await ctx.one("SELECT team_id, is_lead FROM core.team_members WHERE user_id = %s AND is_home", (uid,))
        tid = await _team_id(ctx, inp.team_code) if inp.team_code is not None else (current or {}).get("team_id")
        if tid is None:
            raise Invalid("Cần chọn đội trước khi đặt trưởng đội")
        lead = inp.is_lead if inp.is_lead is not None else bool((current or {}).get("is_lead"))
        await _set_home_team(ctx, uid, tid, lead)
    if inp.owner is not None:
        await ctx.exec("UPDATE core.memberships SET owner = %s WHERE user_id = %s", (inp.owner, uid))
    await _ensure_admin_remains(ctx)
    await _ensure_owner_remains(ctx)
    changes = []
    if was and inp.role is not None and inp.role != was["role"]:
        changes.append({"label": "Vai trò", "before": await _role_ref(ctx, was["role"]), "after": await _role_ref(ctx, inp.role)})
    if was and inp.active is not None and inp.active != was["active"]:
        changes.append({"label": "Trạng thái", "before": "Đang làm việc" if was["active"] else "Đã khoá", "after": "Đang làm việc" if inp.active else "Đã khoá"})
    if was and inp.owner is not None and inp.owner != bool(was["owner"]):
        changes.append({"label": "Chủ tài khoản", "before": "Có" if was["owner"] else "Không", "after": "Có" if inp.owner else "Không"})
    return {"user_id": uid, "subject": await _person(ctx, uid, inp.email), "changes": changes}


@m.on("admin.member_deactivated")
async def revoke_credentials(ctx: Ctx, event: Event) -> None:
    """Nghỉ việc ⇒ thu hồi phiên đăng nhập và token ứng dụng AI (MCP) mà người đó đã đồng ý cấp.

    Quyền hết hiệu lực NGAY khi ``memberships.active = false`` (mỗi request nạp lại quyền), nhưng bản ghi còn sống thì: (a) mở lại tài khoản
    là token cũ dùng lại được mà không ai duyệt lại; (b) màn "ứng dụng đã kết nối" báo sai; (c) sổ kiểm toán thấy credential trông như đang cấp.
    Thu hồi MỘT CHIỀU — dùng lại phải đồng ý lại trên web (0093). Kết nối owner vì RLS của hai bảng chỉ cho thấy credential của CHÍNH mình ⇒ RLS không
    giới hạn hộ, phạm vi công ty phải ghi RÕ trong SQL: một người có thể thuộc nhiều công ty (``core.users`` dùng chung, ``memberships`` theo công ty).
    - Token MCP gắn công ty (``tenant_id``): chỉ thu hồi token của CÔNG TY NÀY.
    - Phiên đăng nhập gắn người, không gắn công ty: chỉ thu hồi khi người đó KHÔNG còn thành viên hoạt động ở công ty khác; còn thì giữ — ``auth.load``
      đã từ chối thành viên bị khoá ở công ty này từng request, và khoá ở công ty A không được đăng xuất người đó khỏi công ty B."""
    uid, tid = event.payload["user_id"], ctx.p.tenant_id
    async with db.owner_tx() as c:
        await c.execute("UPDATE core.mcp_tokens SET revoked_at = now() WHERE user_id = %s AND tenant_id = %s AND revoked_at IS NULL", (uid, tid))
        cur = await c.execute("SELECT 1 FROM core.memberships WHERE user_id = %s AND active AND tenant_id <> %s LIMIT 1", (uid, tid))
        if await cur.fetchone() is None:
            await c.execute("UPDATE core.sessions SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL", (uid,))


# ── Cây tổ chức ──

@m.query("teams", resource="core.admin", title="Cây tổ chức")
async def teams(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all(
        """WITH RECURSIVE tree AS (
               SELECT t.id, t.code, t.name, t.kind, t.parent_id, 0 AS depth, t.code::text AS path FROM core.teams t
               WHERE t.parent_id IS NULL
               UNION ALL
               SELECT c.id, c.code, c.name, c.kind, c.parent_id, tree.depth + 1, tree.path || '/' || c.code
               FROM core.teams c JOIN tree ON c.parent_id = tree.id)
           SELECT tree.*, p.code AS parent_code,
                  (SELECT string_agg(u.name, ', ') FROM core.team_members tm JOIN core.users u ON u.id = tm.user_id
                   WHERE tm.team_id = tree.id AND tm.is_lead) AS leads,
                  (SELECT count(*) FROM core.team_members tm WHERE tm.team_id = tree.id) AS members
           FROM tree LEFT JOIN core.teams p ON p.id = tree.parent_id ORDER BY tree.path""")
    for r in rows:
        r["tree_name"] = "    " * r["depth"] + ("└ " if r["depth"] else "") + r["name"]
    return ui.table(rows, [
        ui.col("tree_name", "Đội"), ui.col("code", "Mã"), ui.col("kind", "Loại", "badge", labels=TEAM_KINDS),
        ui.col("leads", "Trưởng"), ui.col("members", "Số người", "number"),
    ], title="Cây tổ chức", note="Trưởng đội thấy dữ liệu phạm vi “Đội mình quản” của đội mình và mọi đội con.")


class UpsertTeam(Input):
    code: str = field("Mã đội", "", example="KD-C", max_length=30, group="Thêm chi tiết", placeholder="Máy đặt từ tên đội",
                      help="Để trống khi thêm đội mới. Sửa đội có sẵn thì mã đã điền sẵn.")
    name: str = field("Tên đội", example="Kinh doanh C", max_length=120)
    kind: Literal["company", "branch", "sales", "marketing", "accounting", "admin"] = field("Loại", "sales", options=TEAM_KINDS)
    parent_code: str = field("Thuộc đội", "", options_query="admin.team_options", example="HQ")


class UnlinkGoogle(Input):
    email: str = field("Email", hidden=True)


@m.command("unlink_google",
           purpose="Email công ty được cấp lại cho người khác hoặc nhân viên đổi tài khoản Google — gỡ liên kết cũ.", irreversible=True, effect="Gỡ liên kết Google và đăng xuất người này khỏi mọi thiết bị ngay; họ phải đăng nhập lại.",
           resource="core.admin", action="manage", title="Gỡ liên kết Google & đăng xuất mọi thiết bị", agent="deny")
async def unlink_google(ctx: Ctx, inp: UnlinkGoogle) -> dict[str, Any]:
    """Dùng khi email công ty được cấp lại cho người mới, hoặc nhân viên đổi tài khoản Google: gỡ liên kết cũ (lần đăng nhập Google tới
    gắn lại theo email đã mời) và thu hồi mọi phiên đang mở. RLS: chỉ thành viên công ty mình."""
    uid = await ctx.val(
        "SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant() WHERE u.email = %s",
        (inp.email.lower(),))
    if uid is None:
        raise NotFound(f"Không có thành viên {inp.email}")
    removed = await ctx.exec("DELETE FROM core.identities WHERE user_id = %s AND provider = 'google'", (uid,))
    revoked = await ctx.exec("UPDATE core.sessions SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL", (uid,))
    return {"unlinked": removed, "sessions_revoked": revoked}


async def _team_access(ctx: Ctx, inp: UpsertTeam) -> str | None:
    """Chuyển một đội ĐANG CÓ sang đội cha khác ⇒ trưởng đội cha mới có phạm vi "đội mình quản" trên cả nhánh và thành người duyệt bước
    "trưởng nhóm" của nhánh đó (review 08/11) ⇒ phải duyệt. Tạo đội mới (chưa ai), đổi tên / loại ⇒ chạy thẳng."""
    row = await ctx.one("""SELECT t.parent_id, p.code AS parent_code FROM core.teams t LEFT JOIN core.teams p ON p.id = t.parent_id
                           WHERE t.code = %s""", (inp.code,))
    if row is None or (row["parent_code"] or "") == inp.parent_code:
        return None
    return f"đội {inp.code}: chuyển từ dưới {row['parent_code'] or '(gốc)'} sang dưới {inp.parent_code or '(gốc)'}"


async def _teams_existing(ctx: Ctx, keys: list[tuple[str, ...]]) -> dict[tuple[str, ...], dict[str, Any]]:
    """Nhập Excel nhận ra đội đã có theo mã (0226): tên / loại / đội cha hiện tại — để dòng file chỉ điền ô đang trống, không ghi đè mặc định."""
    rows = await ctx.all("""SELECT t.code, t.name, t.kind, coalesce(p.code, '') AS parent_code FROM core.teams t LEFT JOIN core.teams p ON p.id = t.parent_id
                            WHERE lower(t.code) = ANY(%s)""", ([k[0] for k in keys],))
    return {(r["code"].lower(),): r for r in rows}


@m.command("upsert_team",
           purpose="Lập hoặc sửa đội / cơ sở để phân việc và phạm vi dữ liệu theo đúng cơ cấu công ty.", reversible="Chạy lại lệnh với tên, loại, đội cha cũ",
           resource="core.admin", action="manage", title="Thêm / sửa đội", importable=True, sensitive=_team_access,
           import_key=ImportKey(("code",), _teams_existing, update="self"))
async def upsert_team(ctx: Ctx, inp: UpsertTeam) -> dict[str, Any]:
    parent = await _team_id(ctx, inp.parent_code)
    code = inp.code.strip()
    if not code:   # 0225: không bắt người dùng nghĩ ra mã — máy đặt theo tên, không trùng đội có sẵn
        code = code_from_name(inp.name, {r["code"] for r in await ctx.all("SELECT code FROM core.teams")})
    before = await ctx.one("SELECT id, name FROM core.teams WHERE code = %s", (code,))
    tid = await ctx.val(
        """INSERT INTO core.teams (code, name, kind, parent_id) VALUES (%s, %s, %s, %s)
           ON CONFLICT (tenant_id, code) DO UPDATE SET name = EXCLUDED.name, kind = EXCLUDED.kind, parent_id = EXCLUDED.parent_id
           RETURNING id""", (code, inp.name, inp.kind, parent))
    cyclic = await ctx.val(
        """WITH RECURSIVE up AS (SELECT parent_id FROM core.teams WHERE id = %(t)s
                                 UNION SELECT t.parent_id FROM core.teams t JOIN up ON t.id = up.parent_id)
           SELECT EXISTS (SELECT 1 FROM up WHERE parent_id = %(t)s)""", {"t": tid})
    if cyclic:
        raise Invalid("Không thể đặt đội làm con của chính nó / đội con của nó")
    # 0128: gói khác phản ứng theo cài đặt của công ty (kệ tài liệu của đội, phòng của đội) — quản trị không biết gói nào nghe
    ctx.emit("admin.team_saved", team_id=tid, code=code, name=inp.name, created=before is None,
             previous_name=before["name"] if before else None)
    return {"team_id": tid, "code": code, "subject": {"label": f"{inp.name} ({code})", "open": None},
            "changes": [{"label": "Tên đội", "before": before["name"], "after": inp.name}] if before and before["name"] != inp.name else []}


# ── Pháp nhân (0099): chiều trên bút toán và chứng từ tiền — không phải công ty riêng ──

@m.query("entity_options", resource=None, title="Pháp nhân")
async def entity_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all("""SELECT code AS value, code || ' — ' || name || CASE WHEN is_default THEN ' (mặc định)' ELSE '' END AS label
                                       FROM core.legal_entities WHERE active ORDER BY is_default DESC, code"""))


@m.query("legal_entities", resource="core.admin", title="Pháp nhân")
async def legal_entities(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Các pháp nhân của công ty (mã số thuế, địa chỉ) — mỗi bút toán / chứng từ tiền thuộc đúng một pháp nhân; báo cáo lọc hoặc gộp."""
    rows = await ctx.all("""SELECT id, code, name, tax_code, address, CASE WHEN is_default THEN 'Mặc định' ELSE '' END AS is_default,
                                   CASE WHEN active THEN 'Đang dùng' ELSE 'Ngừng' END AS active
                            FROM core.legal_entities ORDER BY is_default DESC, code""")
    return ui.table(rows, [ui.col("code", "Mã"), ui.col("name", "Tên pháp nhân", width=260), ui.col("tax_code", "Mã số thuế"),
                           ui.col("address", "Địa chỉ", width=240), ui.col("is_default", "", "badge", tones={"Mặc định": "blue"}),
                           ui.col("active", "Trạng thái", "badge", tones={"Đang dùng": "green", "Ngừng": "gray"})],
                    title="Pháp nhân", note="Mục ghi doanh thu chọn pháp nhân ghi sổ; dòng sao kê chọn pháp nhân nhận tiền. Chứng từ không "
                                            "chọn ⇒ pháp nhân mặc định.")


class UpsertEntity(Input):
    code: str = field("Mã in trên chứng từ", "", example="RBS", max_length=20, aliases=("Mã", "Mã pháp nhân"), group="Thêm chi tiết", placeholder="Máy đặt từ tên",
                      help="Chữ in hoa / số. Để trống khi thêm pháp nhân mới; sửa pháp nhân có sẵn thì mã đã điền sẵn")
    name: str = field("Tên pháp nhân", example="Công ty CP Mẫu Một", max_length=200)
    tax_code: str = field("Mã số thuế", "", max_length=20, aliases=("MST",))
    address: str = field("Địa chỉ", "", max_length=300)
    is_default: bool = field("Pháp nhân mặc định", False, group="Thêm chi tiết",
                             help="Chứng từ không chọn pháp nhân ⇒ ghi vào pháp nhân này (chỉ một)")
    active: bool = field("Đang dùng", True, group="Thêm chi tiết", help="Ngừng ⇒ không nhận chứng từ mới; sổ cũ giữ nguyên")


@m.command("upsert_entity",
           purpose="Khai pháp nhân (tên, mã số thuế, địa chỉ) để chứng từ và bút toán tiền ghi đúng đơn vị.", reversible="Chạy lại lệnh với thông tin cũ (pháp nhân đã ghi sổ chỉ ngừng dùng, không xoá)",
           resource="core.admin", action="manage", title="Thêm / sửa pháp nhân", importable=True, category="setup")
async def upsert_entity(ctx: Ctx, inp: UpsertEntity) -> dict[str, Any]:
    code = inp.code.strip().upper()
    if not code:   # 0225: không bắt người dùng nghĩ ra mã — máy đặt theo tên, không trùng pháp nhân có sẵn
        code = code_from_name(inp.name, {r["code"] for r in await ctx.all("SELECT code FROM core.legal_entities")}, limit=20)
    if not re.fullmatch(r"[A-Z0-9_-]{1,20}", code):
        raise Invalid("Mã pháp nhân: chữ in hoa / số / gạch, tối đa 20 ký tự")
    if inp.is_default and not inp.active:
        raise Invalid("Pháp nhân mặc định phải đang dùng")
    was = await ctx.one("SELECT name, tax_code, address FROM core.legal_entities WHERE code = %s", (code,))
    if inp.is_default:   # chỉ một mặc định — đổi mặc định là quyết định có nhật ký (lệnh này), không phải sửa tay
        await ctx.exec("UPDATE core.legal_entities SET is_default = false WHERE is_default AND code <> %s", (code,))
    eid = await ctx.val(
        """INSERT INTO core.legal_entities (code, name, tax_code, address, is_default, active) VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (tenant_id, code) DO UPDATE SET name = EXCLUDED.name, tax_code = EXCLUDED.tax_code, address = EXCLUDED.address,
               is_default = EXCLUDED.is_default, active = EXCLUDED.active RETURNING id""",
        (code, inp.name.strip(), inp.tax_code.strip(), inp.address.strip(), inp.is_default, inp.active))
    if not await ctx.val("SELECT 1 FROM core.legal_entities WHERE is_default"):
        raise Invalid("Công ty phải có một pháp nhân mặc định")
    changes = [{"label": label, "before": was[key], "after": now} for label, key, now in
               (("Tên pháp nhân", "name", inp.name.strip()), ("Mã số thuế", "tax_code", inp.tax_code.strip()), ("Địa chỉ", "address", inp.address.strip()))
               if was and was[key] != now]
    return {"entity_id": eid, "summary": f"Pháp nhân {code} — {inp.name.strip()}", "subject": {"label": f"{inp.name.strip()} ({code})", "open": None}, "changes": changes}


# ── Vai trò & quyền ──

@m.query("permissions", resource="core.admin", title="Vai trò & quyền")
async def permissions(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    roles = await ctx.all(
        """SELECT r.key, r.label, (SELECT count(*) FROM core.memberships mb WHERE mb.role = r.key AND mb.active) AS n
           FROM core.roles r ORDER BY r.label""")
    grants = await ctx.all("SELECT role, resource, scope, actions FROM core.grants")
    cell = {(g["role"], g["resource"]): g for g in grants}
    rows = []
    for res, label in sorted(registry.grantable().items()):   # tài nguyên của nhà vận hành (0193) không thuộc ma trận của công ty
        row: dict[str, Any] = {"id": res, "resource": label, "code": res}
        for r in roles:
            g = cell.get((r["key"], res))
            row[r["key"]] = (f"{SCOPE_LABEL[g['scope']]} · " + ", ".join(ACTION_LABEL[a] for a in ACTIONS if a in g["actions"])
                             if g else "")
        rows.append(row)
    cols = [ui.col("resource", "Tài nguyên", width=260)] + [ui.col(r["key"], f"{r['label']} ({r['n']})", width=170) for r in roles]
    return ui.table(rows, cols, title="Vai trò & quyền",
                    note="Ô trống = không có quyền. Phạm vi quyết định thấy DÒNG nào (DB lọc); hành động quyết định chạy LỆNH nào.")


class UpsertRole(Input):
    key: str = field("Sửa vai có sẵn", "", options_query="admin.role_options", example="branch_manager", max_length=30, group="Thêm chi tiết",
                     placeholder="Vai mới — máy đặt mã từ tên", help="Chọn vai để đổi tên / quyền; để trống khi thêm vai mới")
    label: str = field("Tên hiển thị", example="Trưởng chi nhánh", max_length=60)
    copy_from: str = field("Sao quyền từ vai trò", "", options_query="admin.role_options", placeholder="Không sao quyền")
    entry: bool | None = field("Vai nhập môn", None, group="Thêm chi tiết", help="Mời người mới với vai này không cần chủ tài khoản duyệt")


async def _role_key(ctx: Ctx, inp: UpsertRole) -> str:
    """Mã vai: người chọn vai có sẵn thì dùng; thêm vai mới thì máy đặt từ tên (0225), không trùng vai nào."""
    if inp.key:
        return inp.key
    return snake_from_name(inp.label, {r["key"] for r in await ctx.all("SELECT key FROM core.roles")}, limit=30, letters_only=True)   # khoá vai chỉ [a-z_]


async def _role_access(ctx: Ctx, inp: UpsertRole) -> str | None:
    """Phải duyệt (0029): chép quyền sang vai (= cấp quyền); ĐỔI TÊN vai đang có (review 08/11 — nhãn là thứ người duyệt đọc trên phiếu: đổi
    "Giám đốc" thành "Nhân viên" rồi xin chuyển vai sẽ thành phiếu giả); đổi cờ vai nhập môn. Thêm vai mới chưa có quyền ⇒ chạy thẳng."""
    key = await _role_key(ctx, inp)
    cur = await ctx.one("SELECT label, entry FROM core.roles WHERE key = %s", (key,))
    out: list[str] = []
    if cur is not None and cur["label"] != inp.label:
        out.append(f"đổi tên vai [{key}] “{cur['label']}” → “{inp.label}”")
    if inp.entry is not None and inp.entry != bool(cur and cur["entry"]):
        out.append(f"vai [{key}] {'thành' if inp.entry else 'thôi là'} vai nhập môn")
    if inp.copy_from:
        rows = await ctx.all("SELECT resource, scope, actions FROM core.grants WHERE role = %s ORDER BY resource", (inp.copy_from,))
        out.append(f"vai [{key}] nhận quyền chép từ {await _role_ref(ctx, inp.copy_from)}: "
                   + (", ".join(f"{r['resource']} {r['scope']}:{'/'.join(r['actions'])}" for r in rows) or "(không có quyền nào)"))
    return "; ".join(out) or None


@m.command("upsert_role",
           purpose="Lập hoặc đổi tên vai trò để gán quyền theo đúng chức danh trong công ty.", reversible="Chạy lại lệnh với tên cũ (quyền đã chép sửa ở Đặt quyền)",
           resource="core.admin", action="manage", title="Thêm / sửa vai trò", sensitive=_role_access)
async def upsert_role(ctx: Ctx, inp: UpsertRole) -> dict[str, Any]:
    key = await _role_key(ctx, inp)
    was = await ctx.val("SELECT label FROM core.roles WHERE key = %s", (key,))
    await ctx.exec("INSERT INTO core.roles (key, label) VALUES (%s, %s) ON CONFLICT (tenant_id, key) DO UPDATE SET label = EXCLUDED.label",
                   (key, inp.label))
    if inp.entry is not None:
        await ctx.exec("UPDATE core.roles SET entry = %s WHERE key = %s", (inp.entry, key))
    if inp.copy_from:
        await ctx.exec(
            """INSERT INTO core.grants (role, resource, scope, actions)
               SELECT %s, resource, scope, actions FROM core.grants WHERE role = %s
               ON CONFLICT (tenant_id, role, resource) DO NOTHING""", (key, inp.copy_from))
    return {"role": key, "subject": {"label": f"{inp.label} [{key}]", "open": None},
            "changes": [{"label": "Tên vai trò", "before": was, "after": inp.label}] if was and was != inp.label else []}


class SetGrant(Input):
    role: str = field("Vai trò", options_query="admin.role_options")
    resource: str = field("Tài nguyên", options_query="admin.resource_options")
    scope: Literal["own", "unit", "team", "all"] = field("Phạm vi", "own", options=SCOPE_LABEL)
    actions: list[Literal["read", "write", "approve", "manage"]] = field(
        "Hành động", [], options=ACTION_LABEL, help="Bỏ chọn hết = thu hồi quyền")


async def _grant_access(ctx: Ctx, inp: SetGrant) -> str | None:
    if inp.resource not in registry.grantable():   # tài nguyên lạ / của nhà vận hành (0193): từ chối trước khi thành phiếu duyệt
        raise NotFound(f"Không có tài nguyên {inp.resource}")
    res = f"“{registry.RESOURCES.get(inp.resource, inp.resource)}” [{inp.resource}]"
    cur = await ctx.one("SELECT scope, actions FROM core.grants WHERE role = %s AND resource = %s", (inp.role, inp.resource))
    before = f"{cur['scope']}:{'/'.join(cur['actions'])}" if cur else "không có"
    if not inp.actions:
        return f"thu hồi quyền của vai {await _role_ref(ctx, inp.role)} trên {res} (đang: {before})"
    acts = [a for a in ACTIONS if a in inp.actions]
    return (f"vai {await _role_ref(ctx, inp.role)} · {res}: {SCOPE_LABEL[inp.scope]} [{inp.scope}] · "
            f"{', '.join(ACTION_LABEL[a] for a in acts)} [{'/'.join(acts)}] (đang: {before})")


async def _apply_grant(ctx: Ctx, inp: SetGrant) -> None:
    """Ghi một ô của ma trận quyền — MỘT chỗ kiểm cho lệnh của quản trị (sau duyệt) lẫn cửa nhà vận hành (0206)."""
    if inp.resource not in registry.grantable():   # tài nguyên của nhà vận hành (0193): công ty không cấp được — như không có
        raise NotFound(f"Không có tài nguyên {inp.resource}")
    if not await ctx.val("SELECT 1 FROM core.roles WHERE key = %s", (inp.role,)):
        raise NotFound(f"Không có vai trò {inp.role}")
    if inp.actions:
        await ctx.exec(
            """INSERT INTO core.grants (role, resource, scope, actions) VALUES (%s, %s, %s, %s)
               ON CONFLICT (tenant_id, role, resource) DO UPDATE SET scope = EXCLUDED.scope, actions = EXCLUDED.actions""",
            (inp.role, inp.resource, inp.scope, sorted(set(inp.actions), key=ACTIONS.index)))
    else:
        await ctx.exec("DELETE FROM core.grants WHERE role = %s AND resource = %s", (inp.role, inp.resource))
    await _ensure_admin_remains(ctx)


@m.command("set_grant",
           purpose="Cấp hoặc thu quyền của một vai trò trên một loại dữ liệu để đúng người làm đúng việc.", reversible="Chạy lại lệnh với phạm vi và hành động cũ",
           resource="core.admin", action="manage", title="Đặt quyền", sensitive=_grant_access)
async def set_grant(ctx: Ctx, inp: SetGrant) -> dict[str, Any]:
    cur = await ctx.one("SELECT scope, actions FROM core.grants WHERE role = %s AND resource = %s", (inp.role, inp.resource))
    await _apply_grant(ctx, inp)

    def show(scope: str, acts: Sequence[str]) -> str:
        return f"{SCOPE_LABEL[scope]} · " + ", ".join(ACTION_LABEL[a] for a in ACTIONS if a in acts)

    return {"role": inp.role, "resource": inp.resource,
            "subject": {"label": f"Vai {await _role_ref(ctx, inp.role)} · {registry.RESOURCES.get(inp.resource, inp.resource)}", "open": None},
            "changes": [{"label": "Quyền", "before": show(cur["scope"], cur["actions"]) if cur else "Không có",
                         "after": show(inp.scope, inp.actions) if inp.actions else "Không có"}]}


# ── Ai thấy thông tin gì (ADR 0239 phần 3): nhóm thông tin nhạy cảm gói khai (``Pack(info_groups=…)``) × vai — đọc / ghi CHÍNH ma trận quyền ──

INFO_LEVEL = {"none": "Không thấy", **SCOPE_LABEL}


def _info_group(key: str, on: set[str]) -> registry.InfoGroup:
    for _, g in registry.info_groups(on):
        if g.key == key:
            return g
    raise NotFound(f"Không có nhóm thông tin {key} (hoặc gói của nó chưa bật)")


def _screens_showing(g: registry.InfoGroup, on: set[str]) -> str:
    """Màn nào chứa nhóm thông tin này — tự sinh từ khai báo, không gõ tay (1.59): "(cả màn)" = truy vấn của màn đọc chính tài nguyên của nhóm (lớp 1:
    không có quyền ⇒ không có màn); "(khối)" = truy vấn khai ``info=`` gồm nhóm — phần khai ``needs=`` trên màn đó (ô số, cột, biểu đồ) bỏ / hiện theo
    quyền nhóm (lớp 3), cùng luật cho trợ lý và xuất Excel từ màn."""
    out: dict[str, str] = {}
    for w in screens.assemble():
        if w.pack and w.pack not in on:
            continue
        for s in w.screens:
            op = registry.OPS.get(s.query)
            if op is None or registry.MODULE_PACK.get(op.module, "base") not in on:
                continue
            how = "cả màn" if op.resource == g.resource else "khối" if {g.key, g.resource} & set(op.info) or s.query in g.shown_in else ""
            if how:
                out.setdefault(f"{w.title} › {s.title}", how)
    return "; ".join(f"{t} ({h})" for t, h in out.items())


# Hai màn CHỈ ĐỌC dưới đây dùng quyền xem Nhật ký (``core.audit``): chủ tài khoản / giám đốc DUYỆT mọi đổi quyền nên phải đọc được "ai thấy gì" và câu
# hỏi cấu hình mà không cần quyền quản trị người dùng; đổi vẫn là lệnh của quản trị (``core.admin`` manage) qua chuỗi duyệt đổi quyền.
@m.query("info_access", resource="core.audit", title="Ai thấy thông tin gì")
async def info_access(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Hàng = nhóm thông tin nhạy cảm của các gói đang bật; cột = vai của công ty; ô = phạm vi quyền ĐỌC tài nguyên của nhóm (đọc thẳng
    ``core.grants`` — không có bảng thứ hai). Cột "Áp ở đâu" nói đúng chỗ quyền đó được thực thi (rà Codex 0239 F2)."""
    on = await _enabled(ctx)
    roles = await ctx.all("SELECT r.key, r.label, (SELECT count(*) FROM core.memberships mb WHERE mb.role = r.key AND mb.active) AS n FROM core.roles r ORDER BY r.label")
    cell = {(g["role"], g["resource"]): g for g in await ctx.all("SELECT role, resource, scope, actions FROM core.grants")}
    rows = []
    for pack, g in registry.info_groups(on):
        row: dict[str, Any] = {"id": g.key, "group": g.label, "applies": g.applies, "pack": registry.pack_title(pack), "resource": g.resource,
                               "scopes": ", ".join(SCOPE_LABEL[x] for x in g.scopes), "screens": _screens_showing(g, on)}
        for r in roles:
            gr = cell.get((r["key"], g.resource))
            row[r["key"]] = SCOPE_LABEL[gr["scope"]] if gr and "read" in gr["actions"] else ""
        rows.append(row)
    cols = [ui.col("group", "Thông tin", width=240), ui.col("applies", "Áp ở đâu", width=300), ui.col("screens", "Màn có thông tin này", width=320)] + \
        [ui.col(r["key"], f"{r['label']} ({r['n']})", width=130) for r in roles] + [ui.col("scopes", "Phạm vi nhóm này hiểu", width=200)]
    return ui.table(rows, cols, title="Ai thấy thông tin gì", limit=500, empty="Các gói đang bật chưa khai nhóm thông tin nhạy cảm nào",
                    note="Ô trống = vai đó không thấy. “Màn có thông tin này” tự sinh từ khai báo của màn: (cả màn) = không có quyền thì không có màn; (khối) = màn "
                         "vẫn mở, ô số / cột / biểu đồ của nhóm tự bỏ (kể cả số trong dòng, trợ lý, xuất Excel). Mỗi ô là quyền XEM của vai trên tài nguyên của nhóm, cùng ma trận ở “Vai trò & quyền” — đổi ở đây là "
                         "đổi ở đó, qua cùng chuỗi duyệt đổi quyền. Phạm vi áp cho mọi hành động của vai trên tài nguyên đó; quyền ghi / duyệt không đổi ở màn này.")


class SetInfoAccess(Input):
    group: str = field("Thông tin", options_query="admin.info_group_options")
    role: str = field("Vai trò", options_query="admin.role_options")
    level: Literal["none", "own", "unit", "team", "all"] = field("Vai này thấy", "none", options=INFO_LEVEL,
                                                                 help="Chỉ phạm vi nhóm thông tin này hiểu (cột cuối) — sổ cái chỉ có “Cả công ty”")


@m.query("info_group_options", resource="core.admin", title="Nhóm thông tin nhạy cảm")
async def info_group_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [{"value": g.key, "label": g.label} for _, g in registry.info_groups(await _enabled(ctx))]


async def _info_grant(ctx: Ctx, inp: SetInfoAccess) -> SetGrant:
    """Ô "ai thấy" ⇒ đúng một lần ghi ô ma trận quyền: GIỮ hành động khác (ghi / duyệt / quản lý) của vai trên tài nguyên đó (rà Codex: màn thông tin
    chỉ chỉnh quyền xem, không ghi đè hành động khác); bỏ quyền xem khi vai còn hành động khác ⇒ từ chối (sửa ở Vai trò & quyền — tránh vai ghi mà không xem)."""
    g = _info_group(inp.group, await _enabled(ctx))
    if inp.level != "none" and inp.level not in g.scopes:
        raise Invalid(f"“{g.label}” chỉ có phạm vi: {', '.join(SCOPE_LABEL[x] for x in g.scopes)}")
    cur = await ctx.one("SELECT scope, actions FROM core.grants WHERE role = %s AND resource = %s", (inp.role, g.resource))
    others = [a for a in (cur["actions"] if cur else []) if a != "read"]
    if inp.level == "none":
        if others:
            raise Invalid(f"Vai này còn quyền {', '.join(ACTION_LABEL[a] for a in others)} trên “{g.label}” — bỏ ở “Vai trò & quyền” (không để vai ghi mà không xem)")
        return SetGrant(role=inp.role, resource=g.resource, scope=cur["scope"] if cur else "own", actions=[])
    return SetGrant(role=inp.role, resource=g.resource, scope=inp.level, actions=["read", *others])


async def _info_access_change(ctx: Ctx, inp: SetInfoAccess) -> str | None:
    return await _grant_access(ctx, await _info_grant(ctx, inp))


@m.command("set_info_access",
           purpose="Quyết định vai nào được xem nhóm thông tin nhạy cảm và ở phạm vi nào.", reversible="Chạy lại lệnh với mức xem cũ",
           resource="core.admin", action="manage", title="Đổi ai thấy thông tin", sensitive=_info_access_change, agent="deny",
           category="setup")
async def set_info_access(ctx: Ctx, inp: SetInfoAccess) -> dict[str, Any]:
    """Một ô của màn "Ai thấy thông tin gì" = một ô ma trận quyền (``_apply_grant`` — cùng kiểm, cùng chuỗi duyệt đổi quyền như ``set_grant``)."""
    grant = await _info_grant(ctx, inp)
    await _apply_grant(ctx, grant)
    g = _info_group(inp.group, await _enabled(ctx))
    return {"group": g.key, "role": inp.role, "level": inp.level,
            "summary": f"{await _role_ref(ctx, inp.role)} · {g.label}: {INFO_LEVEL[inp.level].lower()}"}


# ── Câu hỏi cấu hình khi đưa công ty vào dùng (``Pack(questions=…)`` + cài đặt công ty 0128) — người và agent cấu hình cùng đọc ──

async def _current(ctx: Ctx, key: str, settings: dict[str, bool], grants: dict[tuple[str, str], dict[str, Any]], hints: dict[str, int]) -> str:
    kind, _, rest = key.partition(":")
    if kind == "grant":
        role, _, res = rest.partition(":")
        g = grants.get((role, res))
        return f"{g['scope']}:{'/'.join(g['actions'])}" if g else "không có"
    if kind == "setting":
        s = company_settings.SETTINGS.get(rest)
        return ("on" if settings.get(rest, s.default) else "off") if s else ""
    if kind == "hints":
        n = sum(hints.values()) if rest == "*" else hints.get(rest, 0)
        return f"{n} câu đã sửa / thêm" if n else "mặc định"
    return ""


@m.query("config_questions", resource="core.audit", title="Câu hỏi cấu hình công ty")
async def config_questions(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Câu hỏi khi đưa công ty vào dùng — gói đang bật khai (``Pack(questions=…)``) + mỗi cài đặt công ty (0128) một câu: câu nghiệp vụ, lựa chọn
    + ý nghĩa, mặc định + lý do, ai quyết, giá trị hiện tại, lệnh ghi. Chỉ ĐỌC: ghi bằng đúng lệnh ở cột cuối (qua executor, quyền người bấm)."""
    on = await _enabled(ctx)
    settings = await company_settings.values(ctx.conn, ctx.p.tenant_id)
    grants = {(g["role"], g["resource"]): g for g in await ctx.all("SELECT role, resource, scope, actions FROM core.grants")}
    hints = {r["role"]: r["n"] for r in await ctx.all("SELECT role, count(*) AS n FROM core.role_hints GROUP BY role")}
    rows = []
    for pack, q in registry.questions(on):
        rows.append({"id": q.key, "topic": q.topic or registry.pack_title(pack), "ask": q.ask,
                     "options": " · ".join(f"{v}: {m_}" for v, m_ in q.options), "default": q.default, "why": q.why, "decider": q.decider,
                     "current": await _current(ctx, q.key, settings, grants, hints), "command": q.command, "key": q.key})
    for k, st in company_settings.SETTINGS.items():
        if registry.MODULE_PACK.get(st.module, "base") in on:
            rows.append({"id": f"setting:{k}", "topic": "Cài đặt công ty", "ask": st.label + "?", "options": f"on: {st.help} · off: không",
                         "default": "on" if st.default else "off", "why": "Mặc định của gói " + registry.pack_title(registry.MODULE_PACK.get(st.module, "base")),
                         "decider": "quản trị", "current": "on" if settings.get(k, st.default) else "off", "command": "admin.set_setting",
                         "key": f"setting:{k}"})
    return ui.table(rows, [ui.col("topic", "Nhóm", width=130), ui.col("ask", "Câu hỏi", width=320), ui.col("options", "Lựa chọn", width=360),
                           ui.col("default", "Mặc định"), ui.col("why", "Vì sao mặc định", width=300), ui.col("decider", "Ai quyết"),
                           ui.col("current", "Đang"), ui.col("command", "Lệnh ghi"), ui.col("key", "Khoá cấu hình", width=220)],
                    title="Câu hỏi cấu hình công ty", limit=1000,
                    note="Mỗi câu một cấu hình đã có: trả lời ⇒ ghi bằng lệnh ở cột “Lệnh ghi” (trợ lý chỉ đề xuất, đổi quyền qua chủ tài khoản duyệt). "
                         "Câu công ty cần mà không có ở đây là việc xây thêm năng lực, không phải cấu hình.")


# ── Cửa nhà vận hành (0206): đổi quyền / tạo vai KHÔNG qua phiếu — chỉ ``python -m barista.operator`` (quyền hệ thống, có tên + lý do) ──

class OperatorGrant(SetGrant):
    by: str = operator_door.by_field()
    why: str = operator_door.why_field()


@m.command("operator_set_grant", resource=operator_door.RESOURCE, action="manage", title="Đặt quyền (nhà vận hành)", internal=True, agent="deny",
           category="setup")
async def operator_set_grant(ctx: Ctx, inp: OperatorGrant) -> dict[str, Any]:
    """Cùng kiểm như ``set_grant`` (tài nguyên cấp được, vai có thật, còn người quản trị), không thành phiếu duyệt: nhà vận hành thiết lập / vận hành
    công ty. Nhật ký ghi người vận hành + lý do + quyền trước / sau."""
    operator_door.require_who(inp)
    before = await _grant_access(ctx, inp)   # mô tả trước / sau như trên phiếu duyệt (kiểm tài nguyên trước khi ghi)
    await _apply_grant(ctx, inp)
    return {"role": inp.role, "resource": inp.resource, "summary": before}


class OperatorRoleTools(Input):
    by: str = operator_door.by_field()
    why: str = operator_door.why_field()


@m.command("operator_role_tools", resource=operator_door.RESOURCE, action="manage", title="Cài bộ công cụ trợ lý mặc định (nhà vận hành)", internal=True,
           agent="deny", category="setup")
async def operator_role_tools(ctx: Ctx, inp: OperatorRoleTools) -> dict[str, Any]:
    """Cài bộ công cụ trợ lý mặc định (0218 — ``Role.tools`` / ``Pack.tools`` của các gói đang bật) cho những vai CHƯA có bộ — công ty cài trước 0218
    không có dòng nào nên trợ lý chọn công cụ kiểu cũ. CHỈ phần này: không chạy lại ``install_defaults`` (sẽ thêm lại vai / quyền / đội mặc định mà
    công ty đã chỉnh). Vai đã có bộ (quản trị đã chọn) giữ nguyên — không thêm lại mục họ đã bỏ."""
    operator_door.require_who(inp)
    on = await _enabled(ctx)
    roles = {r["key"] for r in await ctx.all("SELECT key FROM core.roles")}
    have = {r["role"] for r in await ctx.all("SELECT role FROM core.role_tools")}
    want: dict[str, list[str]] = {}
    for key, pk in registry.PACKS.items():
        if key not in on:
            continue
        for k, r in pk.roles.items():
            if r.tools:
                want.setdefault(k, []).extend(r.tools)
        for k, t in pk.tools.items():
            want.setdefault(k, []).extend(t)
    done: dict[str, int] = {}
    for k in sorted(want):
        if k in roles and k not in have:
            done[k] = (await ctx.call_system("core.default_role_tools", role=k, tools=list(dict.fromkeys(want[k]))))["added"]
    kept = sorted(have & set(want))
    return {"installed": done, "kept": kept,
            "summary": (f"Cài bộ công cụ cho {len(done)} vai: " + ", ".join(f"{k} ({n})" for k, n in done.items()) if done
                        else "Không vai nào cần cài") + (f"; giữ nguyên vai đã có bộ: {', '.join(kept)}" if kept else "")}


class OperatorSkills(Input):
    by: str = operator_door.by_field()
    why: str = operator_door.why_field()


@m.command("operator_skills", resource=operator_door.RESOURCE, action="manage", title="Cài kỹ năng trợ lý mặc định (nhà vận hành)", internal=True,
           agent="deny", category="setup")
async def operator_skills(ctx: Ctx, inp: OperatorSkills) -> dict[str, Any]:
    """Cài kỹ năng trợ lý mặc định (0198 — ``barista.api.skill`` trong ``Pack(records=…)`` của các gói đang bật) mà công ty CHƯA có — vd kỹ năng mới
    của gói sau khi phát hành cho công ty đã cài từ trước. CHỈ phần này: không chạy lại ``install_defaults`` (sẽ thêm lại vai / quyền / đội mặc định
    mà công ty đã chỉnh). Khoá công ty đã có — đã sửa, đang chờ duyệt hay đã NGỪNG — giữ nguyên, không ghi đè, không bật lại (``core.install_skill``)."""
    operator_door.require_who(inp)
    on = await _enabled(ctx)
    keys = [rec[1] for key, pk in sorted(registry.PACKS.items()) if key in on for rec in pk.records if rec[0] == skill_specs.INSTALL]
    added: list[str] = []
    kept: list[str] = []
    for k in keys:
        out = await ctx.call_system("core.install_skill", key=k)
        (added if out["added"] else kept).append(k)
    return {"installed": added, "kept": kept,
            "summary": (f"Cài {len(added)} kỹ năng: " + ", ".join(added) if added else "Không kỹ năng nào cần cài")
                       + (f"; giữ nguyên kỹ năng công ty đã có: {', '.join(kept)}" if kept else "")}


ROLE_KEY = re.compile(r"^[a-z_]{2,30}$")


class OperatorRole(Input):
    key: str = field("Mã vai trò", example="integration_feed", help="chữ thường không dấu, gạch dưới (2–30 ký tự)", max_length=30)
    label: str = field("Tên hiển thị", max_length=60)
    copy_from: str = field("Sao quyền từ vai trò", "", max_length=30)
    by: str = operator_door.by_field()
    why: str = operator_door.why_field()


@m.command("operator_create_role", resource=operator_door.RESOURCE, action="manage", title="Tạo vai trò (nhà vận hành)", internal=True, agent="deny",
           category="setup")
async def operator_create_role(ctx: Ctx, inp: OperatorRole) -> dict[str, Any]:
    """Vai MỚI (vai đã có ⇒ từ chối — đổi tên vai đang có là thay đổi người duyệt đọc trên phiếu, không làm ở đây). Không phải vai nhập môn. Vai tích
    hợp chỉ dùng làm khuôn cho app (``install-app --like-role``) vẫn là vai bình thường: gán cho người là đổi vai — qua chủ tài khoản như mọi lần (0029)."""
    operator_door.require_who(inp)
    key, label = inp.key.strip(), inp.label.strip()
    if not ROLE_KEY.match(key) or not label:
        raise Invalid("Mã vai: chữ thường không dấu và gạch dưới, 2–30 ký tự; cần tên hiển thị")
    if await ctx.val("SELECT 1 FROM core.roles WHERE key = %s", (key,)):
        raise Invalid(f"Đã có vai [{key}] — đặt quyền bằng set-grant")
    if inp.copy_from and not await ctx.val("SELECT 1 FROM core.roles WHERE key = %s", (inp.copy_from,)):
        raise NotFound(f"Không có vai trò {inp.copy_from}")
    await ctx.exec("INSERT INTO core.roles (key, label) VALUES (%s, %s)", (key, label))
    copied = 0
    if inp.copy_from:
        copied = await ctx.exec("""INSERT INTO core.grants (role, resource, scope, actions)
                                   SELECT %s, resource, scope, actions FROM core.grants WHERE role = %s""", (key, inp.copy_from))
    return {"role": key, "copied": copied, "summary": f"Vai [{key}] “{label}”" + (f", chép {copied} quyền từ [{inp.copy_from}]" if inp.copy_from else "")}


# ── Chuỗi duyệt & luật duyệt ──

def parse_steps(text: str) -> list[dict[str, Any]]:
    """'team_lead > role:accountant > user:gd@cty.vn' → các bước."""
    steps: list[dict[str, Any]] = []
    for raw in text.split(">"):
        t = raw.strip()
        if t == "team_lead":
            steps.append({"kind": "team_lead"})
        elif t.startswith("role:") and t[5:].strip():
            steps.append({"kind": "role", "role": t[5:].strip()})
        elif t.startswith("user:") and "@" in t:
            steps.append({"kind": "user", "email": t[5:].strip().lower()})
        elif t == "owner":
            steps.append({"kind": "owner"})
        elif t:
            raise Invalid(f"Bước “{t}” không hiểu — dùng team_lead, role:<mã vai trò>, user:<email>, owner")
    if not steps:
        raise Invalid("Chuỗi duyệt cần ít nhất một bước")
    return steps


def format_steps(steps: list[dict[str, Any]], role_labels: dict[str, str]) -> str:
    out = []
    for s in steps:
        if s["kind"] == "team_lead":
            out.append("Trưởng nhóm của người xin")
        elif s["kind"] == "role":
            out.append(role_labels.get(s["role"], s["role"]))
        elif s["kind"] == "owner":
            out.append("Chủ tài khoản")
        else:
            out.append(s.get("email", ""))
    return " → ".join(out)


@m.query("chains", resource="core.admin", title="Chuỗi duyệt")
async def chains(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    roles = {r["key"]: r["label"] for r in await ctx.all("SELECT key, label FROM core.roles")}
    rules = await ctx.all("SELECT command, chain FROM core.approval_rules")
    rows = []
    for c in await ctx.all("SELECT key, label, steps FROM core.approval_chains ORDER BY label"):
        used = [registry.OPS[r["command"]].title for r in rules if r["chain"] == c["key"] and r["command"] in registry.OPS]
        rows.append({"id": c["key"], "key": c["key"], "label": c["label"], "steps": format_steps(c["steps"], roles),
                     "rules": ", ".join(used)})
    return ui.table(rows, [
        ui.col("label", "Chuỗi"), ui.col("key", "Mã"), ui.col("steps", "Các bước", width=320),
        ui.col("rules", "Bắt buộc cho lệnh", width=240),
    ], title="Chuỗi duyệt", note="Chuỗi mặc định đi kèm các gói đang bật (gói tự dùng chuỗi của mình cho lệnh của gói). "
                                 "“Bắt buộc cho lệnh” = luật duyệt: lệnh đó luôn tạo phiếu thay vì chạy thẳng.")


class ChainStep(Input):
    who: str = field("Ai duyệt", options_query="admin.chain_step_options", example="team_lead")


class SetChain(Input):
    key: str = field("Sửa chuỗi có sẵn", "", options_query="admin.chain_options", example="deal_cancel", max_length=40, group="Thêm chi tiết",
                     placeholder="Chuỗi mới — máy đặt mã từ tên", help="Chọn chuỗi để đổi; để trống khi thêm chuỗi mới")
    label: str = field("Tên", example="Huỷ hợp đồng", max_length=80)
    approvers: list[ChainStep] = field("Các bước duyệt, theo thứ tự", [], help="Mỗi dòng một bước: người duyệt bước đầu ở trên cùng")
    steps: str = field("Viết tay các bước", "", example="team_lead > role:director", group="Thêm chi tiết",
                       help="Dùng thay bảng trên (nhập Excel, trợ lý): cách nhau bằng “>”: team_lead · role:<mã vai trò> · user:<email> · owner (chủ tài khoản)")


def _chain_text(inp: SetChain) -> str:
    """Các bước dạng chữ ``team_lead > role:x``: bảng chọn (``approvers``) nếu có, không thì ô viết tay (``steps``) — nhập Excel / trợ lý."""
    return " > ".join(a.who for a in inp.approvers) if inp.approvers else inp.steps


async def _chain_key(ctx: Ctx, inp: SetChain) -> str:
    if inp.key:
        return inp.key
    return snake_from_name(inp.label, {r["key"] for r in await ctx.all("SELECT key FROM core.approval_chains")}, limit=40, letters_only=True)   # khoá chuỗi chỉ [a-z_]


async def _chain_access(ctx: Ctx, inp: SetChain) -> str | None:
    return f"chuỗi “{inp.label}” ({await _chain_key(ctx, inp)}): {_chain_text(inp)}"


@m.command("set_chain",
           purpose="Quy định ai duyệt, theo thứ tự nào, cho các việc nhạy cảm như tiền và quyền.", reversible="Chạy lại lệnh với các bước duyệt cũ",
           resource="core.admin", action="manage", title="Thêm / sửa chuỗi duyệt", sensitive=_chain_access)
async def set_chain(ctx: Ctx, inp: SetChain) -> dict[str, Any]:
    steps = parse_steps(_chain_text(inp))
    key = await _chain_key(ctx, inp)
    for s in steps:
        if s["kind"] == "role" and not await ctx.val("SELECT 1 FROM core.roles WHERE key = %s", (s["role"],)):
            raise NotFound(f"Không có vai trò {s['role']}")
    was = await ctx.val("SELECT steps FROM core.approval_chains WHERE key = %s", (key,))
    await ctx.exec(
        """INSERT INTO core.approval_chains (key, label, steps) VALUES (%s, %s, %s)
           ON CONFLICT (tenant_id, key) DO UPDATE SET label = EXCLUDED.label, steps = EXCLUDED.steps""",
        (key, inp.label, Jsonb(steps)))
    roles = {r["key"]: r["label"] for r in await ctx.all("SELECT key, label FROM core.roles")}
    return {"key": key, "steps": steps, "subject": {"label": f"Chuỗi duyệt “{inp.label}” ({key})", "open": None},
            "changes": [{"label": "Các bước duyệt", "before": format_steps(was, roles) if was else "Chuỗi mới", "after": format_steps(steps, roles)}]}


class SetRule(Input):
    command: str = field("Lệnh", options_query="admin.command_options")
    chain: str = field("Chuỗi duyệt", "", options_query="admin.chain_options", help="Để trống = bỏ luật, lệnh chạy thẳng")


async def _rule_access(ctx: Ctx, inp: SetRule) -> str | None:
    op = registry.get(inp.command)
    title = op.title if op else inp.command
    return f"“{title}” phải qua chuỗi {inp.chain}" if inp.chain else f"BỎ luật duyệt của “{title}”"


def _ruleable(op: registry.Op | None) -> bool:
    """Lệnh đặt luật duyệt được: lệnh nghiệp vụ + lệnh quản trị (0029: đổi quyền / vai / luật qua chủ tài khoản). Lệnh lõi (duyệt phiếu, thông báo…)
    và lệnh nội bộ thì không."""
    return op is not None and op.kind == "command" and not op.internal and op.module != "core"


@m.command("set_rule",
           purpose="Quyết định lệnh nào luôn phải có người duyệt trước khi chạy.", reversible="Chạy lại lệnh với chuỗi duyệt cũ (hoặc để trống chuỗi để bỏ luật)",
           resource="core.admin", action="manage", title="Đặt luật duyệt cho lệnh", sensitive=_rule_access)
async def set_rule(ctx: Ctx, inp: SetRule) -> dict[str, Any]:
    """Luật của chính lệnh này mặc định qua chủ tài khoản (0029) ⇒ bỏ bảo vệ của lệnh quản trị cũng phải duyệt."""
    if not _ruleable(registry.get(inp.command)):
        raise Invalid("Chỉ đặt luật duyệt cho lệnh nghiệp vụ / quản trị")
    was = await ctx.val("SELECT chain FROM core.approval_rules WHERE command = %s", (inp.command,))
    if inp.chain:
        await ctx.exec(
            """INSERT INTO core.approval_rules (command, chain) VALUES (%s, %s)
               ON CONFLICT (tenant_id, command) DO UPDATE SET chain = EXCLUDED.chain""", (inp.command, inp.chain))
    else:
        await ctx.exec("DELETE FROM core.approval_rules WHERE command = %s", (inp.command,))
    return {"command": inp.command, "chain": inp.chain or None, "subject": {"label": f"Luật duyệt của “{registry.OPS[inp.command].title}”", "open": None},
            "changes": [{"label": "Chuỗi duyệt", "before": was or "Chạy thẳng, không cần duyệt", "after": inp.chain or "Chạy thẳng, không cần duyệt"}]}


# ── Nhật ký & sự kiện ──

AUDIT_STATUS = {"ok": "Thành công", "pending_approval": "Chờ duyệt", "denied": "Bị chặn quyền", "invalid": "Dữ liệu sai",
                "not_found": "Không thấy", "conflict": "Trùng", "error": "Lỗi hệ thống"}
AUDIT_TONES = {"ok": "green", "pending_approval": "orange", "denied": "red", "invalid": "orange", "not_found": "gray",
               "conflict": "orange", "error": "red"}


class AuditFilter(Input):
    status: str = field("Trạng thái", "", options={"": "Tất cả", **AUDIT_STATUS}, primary=True)
    limit: int = field("Số dòng", 300, ge=1, le=2000, group="Thêm điều kiện")


def _operator_commands() -> list[str]:
    """Lệnh của nhà vận hành (chính sách trợ lý AI — 0193): nhật ký có ghi (người vận hành + lý do), nhưng không hiện ở Nhật ký thao tác của khách."""
    return [k for k in registry.OPS if registry.operator_command(k)]


@m.query("audit", resource="core.audit", title="Nhật ký thao tác")
async def audit(ctx: Ctx, inp: AuditFilter) -> dict[str, Any]:
    rows = await ctx.all(
        """SELECT a.id, a.at, coalesce(u.name, CASE WHEN a.system THEN 'Hệ thống' END) AS who, a.command, a.status, a.error,
                  cardinality(a.approved_by) > 0 AS approved
           FROM core.audit_log a LEFT JOIN core.users u ON u.id = a.user_id
           WHERE (%(s)s = '' OR a.status = %(s)s) AND NOT a.command = ANY(%(op)s) ORDER BY a.at DESC LIMIT %(n)s""",
        {"s": inp.status, "n": inp.limit, "op": _operator_commands()})
    for r in rows:
        op = registry.OPS.get(r["command"])
        r["title"] = (op.title if op else r["command"]) + (" (sau duyệt)" if r["approved"] else "")
    denied = sum(1 for r in rows if r["status"] == "denied")
    broken = await ctx.val("SELECT count(*) FROM core.audit_log WHERE status = 'error' AND at > now() - interval '7 days'")
    return ui.table(rows, [
        ui.col("at", "Lúc", "datetime"), ui.col("who", "Người"), ui.col("title", "Thao tác"),
        ui.col("status", "Kết quả", "badge", labels=AUDIT_STATUS, tones=AUDIT_TONES), ui.col("error", "Chi tiết", width=320),
    ], title="Nhật ký thao tác", limit=inp.limit, kpis=[ui.kpi("Bị chặn quyền", denied, tone="red" if denied else None),
                                                        ui.kpi("Lỗi hệ thống 7 ngày", broken, tone="red" if broken else None,
                                                               hint="Lọc “Lỗi hệ thống” để xem chi tiết kỹ thuật")])


TRACE_SOURCE = {"chat": "Trò chuyện", "morning": "Đề xuất buổi sáng", "work": "Việc theo dõi", "auto": "Luật tự động", "mcp": "Ứng dụng AI (MCP)"}


def _step(r: dict[str, Any]) -> str:
    if r["via"] == "agent":
        return "Trợ lý soạn đề xuất" if r["status"] == "proposed" else "Trợ lý làm"
    return "Người xác nhận" if r["trace"] else "Người làm"


class TrailIn(Input):
    days: int = field("Số ngày", 7, ge=1, le=90)


@m.query("agent_actions", resource="core.audit", title="Hành động của trợ lý")
async def agent_actions(ctx: Ctx, inp: TrailIn) -> dict[str, Any]:
    """MỌI thao tác dính tới trợ lý (migration 001): trợ lý soạn / làm, và lần NGƯỜI xác nhận đề xuất đó — cùng mã vết với lượt / đề xuất /
    việc đã sinh ra nó. Bấm một dòng ⇒ cả chuỗi của vết đó. Trong phạm vi người xem được Nhật ký (RLS ``core.audit``)."""
    rows = await ctx.all(
        """SELECT a.id, a.at, a.trace, a.via, a.command, a.status, a.error, coalesce(u.name, 'Hệ thống') AS who
           FROM core.audit_log a LEFT JOIN core.users u ON u.id = a.user_id
           WHERE (a.via = 'agent' OR a.trace <> '') AND a.at > now() - make_interval(days => %s)
           ORDER BY a.at DESC LIMIT 500""", (inp.days,))
    for r in rows:
        op = registry.OPS.get(r["command"])
        r["title"] = op.title if op else r["command"]
        r["source"] = TRACE_SOURCE.get(r["trace"].split(":")[0], "Khác") if r["trace"] else "—"
        r["step"] = _step(r)
    confirmed = sum(1 for r in rows if r["step"] == "Người xác nhận" and r["status"] == "ok")
    return ui.table(rows, [
        ui.col("at", "Lúc", "datetime"), ui.col("source", "Nguồn"), ui.col("step", "Bước"), ui.col("who", "Người"),
        ui.col("title", "Thao tác", width=240), ui.col("status", "Kết quả", "badge", labels={**AUDIT_STATUS, "proposed": "Đề xuất"},
                                                     tones={**AUDIT_TONES, "proposed": "blue"}),
    ], title="Hành động của trợ lý", limit=500, kpis=[
        ui.kpi("Trợ lý soạn đề xuất", sum(1 for r in rows if r["step"] == "Trợ lý soạn đề xuất")),
        ui.kpi("Người xác nhận", confirmed), ui.kpi("Trợ lý tự làm", sum(1 for r in rows if r["step"] == "Trợ lý làm")),
    ], note="Trợ lý chạy bằng quyền người dùng; lệnh ghi phần lớn chỉ thành đề xuất — người xác nhận mới ghi. Bấm một dòng để xem cả chuỗi.",
        empty="Chưa có hành động nào của trợ lý trong kỳ.")


class TraceRef(Input):
    audit_id: int = field("Dòng nhật ký", hidden=True)


@m.query("trace", resource="core.audit", title="Chuỗi hành động")
async def trace(ctx: Ctx, inp: TraceRef) -> dict[str, Any]:
    """Cả chuỗi của một vết: lượt / đề xuất / việc ⇒ từng bước trợ lý soạn / làm ⇒ ai xác nhận, kết quả."""
    t = await ctx.val("SELECT trace FROM core.audit_log WHERE id = %s", (inp.audit_id,))
    if t is None:
        raise NotFound("Không thấy dòng nhật ký (hoặc ngoài phạm vi bạn xem)")
    rows = await ctx.all(
        """SELECT a.id, a.at, a.trace, a.via, a.command, a.status, a.error, a.input, coalesce(u.name, 'Hệ thống') AS who
           FROM core.audit_log a LEFT JOIN core.users u ON u.id = a.user_id
           WHERE a.trace = %s AND (%s <> '' OR a.id = %s) ORDER BY a.at, a.id LIMIT 200""", (t, t, inp.audit_id))
    items = []
    for r in rows:
        op = registry.OPS.get(r["command"])
        items.append({"title": f"{_step(r)}: {op.title if op else r['command']}",
                      "subtitle": " · ".join(x for x in (r["who"], AUDIT_STATUS.get(r["status"], "Đề xuất" if r["status"] == "proposed" else r["status"]),
                                                          r["error"] or "") if x),
                      "badge": r["at"].astimezone(zone()).strftime("%d/%m %H:%M"),
                      "tone": "bad" if r["status"] in ("error", "denied", "conflict") else ("accent" if r["via"] == "agent" else "ok")})
    return ui.card("Chuỗi hành động", [ui.b_list(items, f"{TRACE_SOURCE.get(t.split(':')[0], 'Thao tác') if t else 'Thao tác'} — {len(items)} bước")],
                   subtitle="Trợ lý soạn / làm bằng quyền người dùng; người xác nhận mới ghi", icon="point.3.connected.trianglepath.dotted")


@m.query("deliveries", resource="core.admin", title="Sự kiện lỗi")
async def deliveries(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all(
        """SELECT d.id, e.type, d.subscriber, d.status, d.attempts, d.last_error, d.next_at, e.created_at
           FROM core.deliveries d JOIN core.events e ON e.id = d.event_id
           WHERE d.status = 'dead' OR (d.status = 'pending' AND d.attempts > 0) ORDER BY e.created_at DESC LIMIT 200""")
    return ui.table(rows, [
        ui.col("created_at", "Lúc", "datetime"), ui.col("type", "Sự kiện"), ui.col("subscriber", "Bên xử lý"),
        ui.col("status", "Trạng thái", "badge", labels={"dead": "Dừng thử", "pending": "Đang thử lại"},
               tones={"dead": "red", "pending": "orange"}),
        ui.col("attempts", "Số lần", "number"), ui.col("last_error", "Lỗi", width=320),
    ], title="Sự kiện lỗi", empty="Không có sự kiện nào lỗi")


class Retry(Input):
    delivery_id: int = field("Mã", hidden=True)


@m.command("retry_delivery",
           purpose="Một phản ứng tự động của hệ thống bị lỗi — chạy lại để nghiệp vụ đi tiếp.", irreversible=True, effect="Chạy lại phản ứng tự động ngay. Nếu phản ứng gửi tin hoặc ghi sổ thì kết quả đó không thu lại được.",
           resource="core.admin", action="manage", title="Chạy lại sự kiện")
async def retry_delivery(ctx: Ctx, inp: Retry) -> dict[str, Any]:
    n = await ctx.exec("UPDATE core.deliveries SET status = 'pending', next_at = now() WHERE id = %s AND status <> 'done'",
                       (inp.delivery_id,))
    if not n:
        raise NotFound("Không thấy sự kiện cần chạy lại")
    return {"delivery_id": inp.delivery_id}


class Dismiss(Input):
    delivery_id: int = field("Mã", hidden=True)
    reason: str = field("Lý do bỏ qua", kind="textarea", max_length=500, example="Đã lập hoá đơn tay ngày 02/11; phản ứng tự động không cần nữa")


@m.command("dismiss_delivery",
           purpose="Phản ứng lỗi mà nghiệp vụ đã xử lý cách khác — bỏ qua có lý do để không chặn việc tắt gói.", reversible="Bấm Chạy lại sự kiện (sự kiện bỏ qua vẫn chạy lại được)",
           resource="core.admin", action="manage", title="Bỏ qua sự kiện lỗi (có lý do)", agent="deny")
async def dismiss_delivery(ctx: Ctx, inp: Dismiss) -> dict[str, Any]:
    """Phản ứng hết lượt thử / đang lỗi mà nghiệp vụ đã xử lý cách khác ⇒ quản trị BỎ QUA có lý do (ghi ai, lúc nào). Không phải "đã xong": trạng
    thái riêng để truy lại; từ đây nó không còn chặn tắt gói."""
    if not inp.reason.strip():
        raise Invalid("Cần lý do bỏ qua")
    n = await ctx.exec("""UPDATE core.deliveries SET status = 'dismissed', dismissed_by = core.me(), dismiss_reason = %s, done_at = now()
                          WHERE id = %s AND status IN ('dead', 'pending')""", (inp.reason.strip(), inp.delivery_id))
    if not n:
        raise NotFound("Không thấy sự kiện lỗi cần bỏ qua")
    return {"delivery_id": inp.delivery_id, "summary": "Đã bỏ qua sự kiện lỗi (có lý do)"}


# ── Lựa chọn cho form ──

@m.query("role_options", resource=None, title="Vai trò")
async def role_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all("SELECT key AS value, label FROM core.roles ORDER BY label"))


@m.query("team_options", resource=None, title="Đội", creates="admin.upsert_team")
async def team_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    # tên đội cho người đọc; mã chỉ hiện khi hai đội trùng tên (mã là khoá hệ thống, không phải thứ người dùng cần nhớ)
    return ui.options(await ctx.all(
        """SELECT code AS value, CASE WHEN count(*) OVER (PARTITION BY lower(name)) > 1 THEN name || ' (' || code || ')' ELSE name END AS label
           FROM core.teams ORDER BY name, code"""))


@m.query("member_options", resource=None, title="Người trong công ty")
async def member_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all(
        """SELECT u.email AS value, u.name || ' (' || r.label || ')' AS label FROM core.memberships mb
           JOIN core.users u ON u.id = mb.user_id JOIN core.roles r ON r.tenant_id = mb.tenant_id AND r.key = mb.role
           WHERE mb.active ORDER BY u.name"""))


@m.query("resource_options", resource="core.admin", title="Tài nguyên")
async def resource_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    names = registry.grantable()
    dup = {v for v in names.values() if list(names.values()).count(v) > 1}   # khoá kỹ thuật chỉ hiện khi hai tài nguyên trùng tên
    return [{"value": k, "label": f"{v} ({k})" if v in dup else v} for k, v in sorted(names.items(), key=lambda kv: (kv[1], kv[0]))]


@m.query("chain_options", resource="core.admin", title="Chuỗi duyệt")
async def chain_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all("SELECT key AS value, label FROM core.approval_chains ORDER BY label"))


@m.query("chain_step_options", resource="core.admin", title="Người duyệt của một bước")
async def chain_step_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Mọi kiểu bước ``parse_steps`` hiểu, bằng chữ người đọc: trưởng nhóm, chủ tài khoản, một vai, một người cụ thể."""
    roles = await ctx.all("SELECT key, label FROM core.roles ORDER BY label")
    members = await ctx.all(
        """SELECT u.email, u.name FROM core.memberships mb JOIN core.users u ON u.id = mb.user_id WHERE mb.active ORDER BY u.name""")
    return [{"value": "team_lead", "label": "Trưởng nhóm của người xin"}, {"value": "owner", "label": "Chủ tài khoản"},
            *({"value": f"role:{r['key']}", "label": f"Vai: {r['label']}"} for r in roles),
            *({"value": f"user:{u['email']}", "label": f"Người: {u['name']}"} for u in members)]


@m.query("command_options", resource="core.admin", title="Lệnh nghiệp vụ")
async def command_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [{"value": op.name, "label": f"{registry.MODULES[op.module]} › {op.title}"}
            for op in sorted(registry.OPS.values(), key=lambda o: o.name)
            if _ruleable(op)]


# ── Trợ lý (agent) được làm gì ──

AGENT_MODE_LABEL = {"allow": "Tự làm", "propose": "Soạn đề xuất — người bấm", "deny": "Không được"}


@m.query("agent_policies", resource="core.admin", title="Trợ lý được làm gì")
async def agent_policies(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Trợ lý luôn chạy bằng quyền của chính người hỏi (không bao giờ hơn). Bảng này là rào THÊM: trần khai trong code,
    công ty siết thêm được (vd không cho trợ lý soạn phiếu rút tiền), không nới quá trần."""
    pol = {r["command"]: r for r in await ctx.all("SELECT command, mode, daily_cap, money_cap_vnd, basis FROM core.agent_policies")}
    overrides = {c: r["mode"] for c, r in pol.items()}
    trust = await _trust(ctx)
    rec = await _track_record(ctx, trust["window_days"])
    rows = []
    for op in sorted(registry.OPS.values(), key=lambda o: (o.module, o.kind, o.name)):
        if op.internal:
            continue
        r, p = rec.get(op.name, _ZERO), pol.get(op.name)
        promoted = p is not None and p["basis"] is not None
        rows.append({"id": op.name, "module": registry.MODULES[op.module], "title": op.title,
                     "kind": "write" if op.kind == "command" else "read", "ceiling": op.agent,
                     "mode": executor.effective_mode(op, overrides, bool(trust["autorun_low_risk"])), "mode_set": p["mode"] if p else "",
                     "custom": "Công ty nới" if promoted else "Công ty siết" if op.name in overrides
                     else ("Siết cả nhóm" if executor.group_key(op) in overrides
                           else "Công tắc công ty" if trust["autorun_low_risk"] and op.promotable else ""),
                     "proposed": r["proposed"], "approved_pct": f"{r['approved_pct']}%" if r["proposed"] else "", "autoruns": r["autoruns"],
                     "reversed": r["reversed_7d"],
                     "daily_cap": p["daily_cap"] if p else None, "money_cap_vnd": p["money_cap_vnd"] if p else None,
                     "can_promote": "yes" if op.promotable and not promoted and r["proposed"] >= trust["min_proposals"]
                     and r["approved_pct"] >= trust["min_approved_pct"] else "",
                     "promotable": "Nới được" if op.promotable else ""})
    modes = {"allow": "green", "propose": "orange", "deny": "red"}
    ready = sum(1 for x in rows if x["can_promote"])
    return ui.table(rows, header=[await _preset_picker(ctx, overrides, bool(trust["autorun_low_risk"]))], columns=[
        ui.col("module", "Phân hệ"), ui.col("title", "Thao tác", width=240),
        ui.col("kind", "Loại", "badge", labels={"write": "Ghi", "read": "Xem"}, tones={"write": "purple", "read": "blue"}),
        ui.col("mode", "Trợ lý", "badge", labels=AGENT_MODE_LABEL, tones=modes),
        ui.col("ceiling", "Trần (code)", "badge", labels=AGENT_MODE_LABEL), ui.col("custom", ""),
        ui.col("proposed", f"Đề xuất / {trust['window_days']} ngày", "number"), ui.col("approved_pct", "Duyệt nguyên văn"),
        ui.col("autoruns", "Tự làm", "number"), ui.col("reversed", "Bị sửa / huỷ (7 ngày)", "number"), ui.col("daily_cap", "Trần / ngày", "number"), ui.col("money_cap_vnd", "Trần tiền", "money"),
        ui.col("promotable", ""),
    ], title="Trợ lý được làm gì",
        kpis=[ui.kpi("Đủ thành tích để nới", ready, tone="green" if ready else None,
                     hint=f"≥ {trust['min_proposals']} đề xuất, ≥ {trust['min_approved_pct']}% duyệt nguyên văn / {trust['window_days']} ngày"),
              ui.kpi("Đang được tự làm", sum(1 for x in rows if x["custom"] == "Công ty nới"))],
        note="Trợ lý chạy bằng quyền của người hỏi — người không làm được thì trợ lý cũng không. Duyệt phiếu luôn do người bấm. "
             "Lệnh “Nới được” đủ thành tích thì chủ tài khoản nới lên Tự làm (có trần / ngày, trần tiền; lỗi nhiều tự hạ — 0143).")


async def _preset_picker(ctx: Ctx, overrides: dict[str, str], switch: bool) -> dict[str, Any]:
    """Khối chọn mức tự chủ trên đầu màn: mức đang KHỚP dữ liệu (server suy), phiếu nới đang chờ chủ tài khoản (nếu có)."""
    current = preset_of(overrides, switch)
    waiting = await ctx.val("""SELECT input->>'preset' FROM core.approvals WHERE command = 'admin.set_agent_preset' AND status = 'pending'
                               ORDER BY created_at DESC LIMIT 1""")
    auto = f"Tự làm: {', '.join(o.title.lower() for o in _promotable())}"
    facts = {"careful": [], "balanced": [auto, f"Trần {PRESET_CAP['balanced']} lần / ngày / lệnh"],
             "full": [auto, f"Trần {PRESET_CAP['full']} lần / ngày / lệnh"]}
    items = [{"key": k, "title": t, "text": text, "facts": facts[k],
              "action": ui.act("admin.set_agent_preset", t, preset={"preset": k}, style="primary" if k == "balanced" else "default")}
             for k, (t, text) in PRESETS.items()]
    return ui.b_picker("Mức tự chủ của trợ lý", items, current=current if current in PRESETS else "",
                       custom="Chưa chọn" if current == "default" else "Tuỳ chỉnh",
                       note="Nới thêm cần chủ tài khoản duyệt. Siết lại có hiệu lực ngay.",
                       pending=f"{PRESETS[waiting][0]}: chờ chủ tài khoản duyệt" if waiting in PRESETS else "")


@m.query("agent_groups", resource="core.admin", title="Nhóm công cụ của trợ lý")
async def agent_groups(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Một trợ lý, nhiều NHÓM công cụ (mỗi phân hệ một nhóm): tắt một nhóm ⇒ trợ lý không thấy công cụ nào của phân hệ đó ở công ty này
    (vd không cho trợ lý đụng Tài chính); "Chỉ đề xuất" ⇒ mọi lệnh ghi của nhóm chỉ thành thẻ người bấm. Quyền người hỏi vẫn là trần."""
    overrides = {r["command"]: r["mode"] for r in await ctx.all("SELECT command, mode FROM core.agent_policies")}
    on = await packs.enabled(ctx.conn, ctx.p.tenant_id)
    rows = []
    for key, label in registry.MODULES.items():
        ops = [o for o in registry.OPS.values() if o.module == key and not o.internal and o.public is None]
        if not ops or registry.MODULE_PACK.get(key, "base") not in on:
            continue
        g = f"{key}.*"
        rows.append({"id": g, "group": label, "tools": len(ops), "mode": overrides.get(g, ""),
                     "state": {"deny": "off", "propose": "propose"}.get(overrides.get(g, ""), "on")})
    return ui.table(rows, [
        ui.col("group", "Nhóm công cụ"), ui.col("tools", "Số thao tác", "number"),
        ui.col("state", "Trợ lý", "badge", labels={"on": "Theo quyền người hỏi", "propose": "Chỉ đề xuất", "off": "Tắt"},
               tones={"on": "green", "propose": "orange", "off": "red"}),
    ], title="Nhóm công cụ của trợ lý", note="Một trợ lý cầm công cụ theo quyền người hỏi; công ty tắt / siết theo nhóm ở đây, từng thao tác ở "
                                            "“Trợ lý được làm gì”. Chỉ siết được, không nới quá trần khai trong code.")


class SetAgentGroup(Input):
    group: str = field("Nhóm", hidden=True)
    mode: str = field("Trợ lý", "", options={"": "Theo quyền người hỏi", "propose": "Chỉ đề xuất", "deny": "Tắt"})


@m.command("set_agent_group",
           purpose="Quyết định trợ lý được dùng cả nhóm công cụ nào: theo quyền người hỏi, chỉ đề xuất hay tắt.", reversible="Chọn lại chế độ cũ cho nhóm này",
           resource="core.admin", action="manage", title="Bật / tắt nhóm công cụ của trợ lý", agent="deny")
async def set_agent_group(ctx: Ctx, inp: SetAgentGroup) -> dict[str, Any]:
    key = inp.group.removesuffix(".*")
    if key not in registry.MODULES or not inp.group.endswith(".*"):
        raise NotFound(f"Không có nhóm {inp.group}")
    if inp.mode and inp.mode not in ("propose", "deny"):
        raise Invalid("Chế độ không hợp lệ")
    was = await ctx.val("SELECT mode FROM core.agent_policies WHERE command = %s", (inp.group,)) or ""
    if not inp.mode:
        await ctx.exec("DELETE FROM core.agent_policies WHERE command = %s", (inp.group,))
    else:
        await ctx.exec("""INSERT INTO core.agent_policies (command, mode) VALUES (%s, %s)
                          ON CONFLICT (tenant_id, command) DO UPDATE SET mode = EXCLUDED.mode""", (inp.group, inp.mode))
    mode_text = {"": "Theo quyền người hỏi", "propose": "Chỉ đề xuất", "deny": "Tắt"}
    return {"group": inp.group, "mode": inp.mode or "on", "subject": {"label": f"Nhóm công cụ {registry.MODULES[key]}", "open": None},
            "changes": [{"label": "Trợ lý", "before": mode_text.get(was, was), "after": mode_text[inp.mode]}] if was != inp.mode else [],
            "summary": f"Trợ lý — nhóm {registry.MODULES[key]}: " + {"": "theo quyền người hỏi", "propose": "chỉ đề xuất", "deny": "tắt"}[inp.mode]}


# ── Thang tin cậy (0143): nới trợ lý từ "đề xuất" lên "tự làm" theo thành tích, có trần, thu hồi được ──

TRUST_DEFAULTS = {"min_proposals": 20, "min_approved_pct": 95, "window_days": 30, "max_errors_7d": 3, "reverse_days": 7,
                  "autorun_low_risk": 0, "autorun_daily_cap": 20}
_ZERO = {"proposed": 0, "approved": 0, "edited": 0, "dismissed": 0, "approved_pct": 0, "errors_7d": 0, "reversed_7d": 0, "autoruns": 0,
         "auto_money_vnd": 0}
async def _reversals(ctx: Ctx, reverse_days: int) -> dict[str, int]:
    """Số lần trợ lý TỰ LÀM (nhật ký ``ref = autorun:<đối tượng>``) trong 7 ngày qua mà sau đó NGƯỜI chạy một lệnh "đảo" (``Op.undone_by``) trên
    cùng đối tượng trong ``reverse_days`` ngày (cửa sổ là DỮ LIỆU công ty — ``core.agent_trust.reverse_days``) — tín hiệu "làm sai nhưng hợp lệ".
    Lệnh của người lọc ngay trong SQL theo đối tượng của các lần tự làm (review 09/11: không kéo cả 14 ngày lệnh về Python). Mỗi lần tự làm tính tối đa một."""
    rules: dict[str, tuple[str, list[tuple[str, str, str]]]] = {}
    for op in registry.OPS.values():
        if op.promotable and op.target and op.undone_by:
            parsed = []
            for u in op.undone_by:
                cmd, _, cond = u.partition(":")
                k, _, v = cond.partition("=")
                parsed.append((cmd, k, v))
            rules[op.name] = (op.target, parsed)
    if not rules:
        return {}
    autos = await ctx.all("""SELECT command, substr(ref, 9) AS obj, at FROM core.audit_log WHERE via = 'agent' AND status = 'ok' AND ref LIKE 'autorun:%%'
                             AND command = ANY(%s) AND at >= now() - interval '7 days'""", (list(rules),))
    if not autos:
        return {}
    undo_cmds = sorted({c for _, us in rules.values() for c, _, _ in us})
    targets, objs = sorted({t for t, _ in rules.values()}), sorted({a["obj"] for a in autos})
    humans = await ctx.all("""SELECT command, input, at FROM core.audit_log WHERE via = 'human' AND status = 'ok' AND command = ANY(%s)
                              AND at >= now() - interval '7 days'
                              AND EXISTS (SELECT 1 FROM jsonb_each_text(input) kv WHERE kv.key = ANY(%s) AND kv.value = ANY(%s))""",
                           (undo_cmds, targets, objs))
    out: dict[str, int] = {}
    for a in autos:
        target, undos = rules[a["command"]]
        for h in humans:
            inp = h["input"] or {}
            if (a["at"] < h["at"] <= a["at"] + timedelta(days=reverse_days) and str(inp.get(target)) == a["obj"]
                    and any(h["command"] == c and (not k or str(inp.get(k)) == v) for c, k, v in undos)):
                out[a["command"]] = out.get(a["command"], 0) + 1
                break
    return out


async def _rule_required(ctx: Ctx, command: str) -> None:
    """FAIL CLOSED (review an ninh 08/11): nới tự chủ / hạ ngưỡng chỉ chạy khi công ty CÓ luật duyệt cho lệnh đó (executor đã bắt qua chủ tài khoản,
    hoặc ngoại lệ một chủ tài khoản). Công ty chưa có luật (cài trước bản này mà chưa chạy nâng cấp, hoặc ai đó đã bỏ) ⇒ từ chối, không chạy thẳng."""
    if not await ctx.val("SELECT 1 FROM core.approval_rules WHERE command = %s", (command,)):
        raise Denied(f"Công ty chưa có luật duyệt cho “{command}” — cài cấu hình mặc định (luật “Thay đổi quyền truy cập” → chủ tài khoản) trước khi "
                     "nới tự chủ cho trợ lý")


async def _trust(ctx: Ctx) -> dict[str, int]:
    """Ngưỡng tin cậy CÔNG TY đặt (core.agent_trust) — chưa đặt ⇒ mặc định."""
    row = await ctx.one("""SELECT min_proposals, min_approved_pct, window_days, max_errors_7d, reverse_days, autorun_low_risk::int AS autorun_low_risk,
                                  autorun_daily_cap FROM core.agent_trust""")
    return {k: int(row[k]) for k in TRUST_DEFAULTS} if row else dict(TRUST_DEFAULTS)


async def _track_record(ctx: Ctx, window_days: int) -> dict[str, dict[str, int]]:
    """THÀNH TÍCH đề xuất theo lệnh trong ``window_days`` ngày — đếm từ nhật ký thao tác theo cùng VẾT (trợ lý soạn → người xác nhận / sửa / bỏ — 0086),
    không model. ``approved_pct`` = duyệt NGUYÊN VĂN (input lúc bấm = lúc soạn) / đề xuất. Kèm lỗi khi trợ lý chạy thẳng 7 ngày và số lần tự làm."""
    rows = await ctx.all(
        """WITH w AS (SELECT id, command, trace, input, status, via FROM core.audit_log
                     WHERE at >= now() - make_interval(days => %(d)s) AND trace <> ''),
                p AS (SELECT DISTINCT ON (command, trace) command, trace, input FROM w WHERE via = 'agent' AND status = 'proposed'
                      ORDER BY command, trace, id DESC),
                h AS (SELECT DISTINCT ON (command, trace) command, trace, input, status FROM w WHERE via = 'human' AND status IN ('ok', 'dismissed')
                      ORDER BY command, trace, (status = 'ok') DESC, id DESC)
           SELECT p.command, count(*) AS proposed,
                  count(h.trace) FILTER (WHERE h.status = 'ok' AND h.input = p.input) AS approved,
                  count(h.trace) FILTER (WHERE h.status = 'ok' AND h.input <> p.input) AS edited,
                  count(h.trace) FILTER (WHERE h.status = 'dismissed') AS dismissed
           FROM p LEFT JOIN h ON h.command = p.command AND h.trace = p.trace GROUP BY p.command""", {"d": window_days})
    errors = {r["command"]: int(r["n"]) for r in await ctx.all(
        "SELECT command, count(*) AS n FROM core.audit_log WHERE via = 'agent' AND status = 'error' AND at >= now() - interval '7 days' GROUP BY command")}
    auto = {r["command"]: (int(r["runs"]), int(r["money"])) for r in await ctx.all(
        "SELECT command, sum(runs) AS runs, sum(money_vnd) AS money FROM core.agent_autoruns WHERE day >= core.today() - %s GROUP BY command",
        (window_days,))}
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        n, ok_ = int(r["proposed"]), int(r["approved"])
        out[r["command"]] = {**_ZERO, "proposed": n, "approved": ok_, "edited": int(r["edited"]), "dismissed": int(r["dismissed"]),
                             "approved_pct": ok_ * 100 // n if n else 0}
    reversed_ = await _reversals(ctx, (await _trust(ctx))["reverse_days"])
    for c in set(errors) | set(auto) | set(reversed_):
        out.setdefault(c, dict(_ZERO))
    for c, v in out.items():
        v["errors_7d"] = errors.get(c, 0)
        v["reversed_7d"] = reversed_.get(c, 0)
        v["autoruns"], v["auto_money_vnd"] = auto.get(c, (0, 0))
    return out


class SetAgentPolicy(Input):
    command: str = field("Thao tác", options_query="admin.op_options")
    mode: str = field("Trợ lý", "", options={"": "Theo trần (bỏ siết / bỏ nới)", **AGENT_MODE_LABEL})
    daily_cap: int | None = field("Tối đa lần tự làm / ngày", None, ge=1, le=10000, help="Bắt buộc khi nới lên Tự làm; vượt mức ⇒ thành đề xuất")
    money_cap_vnd: Money | None = field("Trần tiền mỗi lần", None, help="Bắt buộc khi nới lệnh có ô tiền; vượt ⇒ thành đề xuất")


def _is_promotion(op: registry.Op | None, inp: SetAgentPolicy) -> bool:
    return op is not None and inp.mode == "allow" and registry.AGENT_MODES.index("allow") > registry.AGENT_MODES.index(op.agent)


async def _promotion_check(ctx: Ctx, op: registry.Op, inp: SetAgentPolicy) -> dict[str, Any]:
    """Điều kiện NỚI (0143), kiểm cả lúc xin (trước khi lập phiếu — sai thì báo ngay) lẫn lúc chạy sau duyệt (thành tích có thể đã đổi): lệnh khai
    ``promotable``, có trần ngày (và tiền nếu lệnh có ô tiền), thành tích đủ ngưỡng công ty. Trả căn cứ lưu kèm chính sách."""
    if not op.promotable:
        raise Invalid(f"Trần của “{op.title}” là “{AGENT_MODE_LABEL[op.agent]}” — chỉ siết thêm được, không nới (lệnh duyệt / quyền / tiền ra "
                      "không bao giờ để trợ lý tự làm)")
    if inp.daily_cap is None:
        raise Invalid("Nới lên Tự làm cần trần số lần / ngày")
    if inp.money_cap_vnd is None and any(s.get("kind") == "money" for s in field_specs(op)):
        raise Invalid(f"“{op.title}” có ô tiền — cần trần tiền mỗi lần")
    trust = await _trust(ctx)
    rec = (await _track_record(ctx, trust["window_days"])).get(inp.command, dict(_ZERO))
    if rec["proposed"] < trust["min_proposals"] or rec["approved_pct"] < trust["min_approved_pct"]:
        raise Invalid(f"Chưa đủ thành tích để nới “{op.title}”: {rec['proposed']} đề xuất, {rec['approved_pct']}% duyệt nguyên văn trong "
                      f"{trust['window_days']} ngày — công ty yêu cầu ≥ {trust['min_proposals']} đề xuất và ≥ {trust['min_approved_pct']}%")
    return {**rec, **trust, "promoted_on": today().isoformat()}


async def _policy_access(ctx: Ctx, inp: SetAgentPolicy) -> str | None:
    """NỚI trên trần (0143) = cấp quyền cho trợ lý ⇒ qua chuỗi đổi quyền (chủ tài khoản — 0029); siết / bỏ siết thì không. Điều kiện nới kiểm ở đây
    TRƯỚC khi lập phiếu — không bắt chủ tài khoản duyệt một yêu cầu sẽ bị từ chối."""
    op = registry.get(inp.command)
    if not _is_promotion(op, inp):
        return None
    assert op is not None
    await _promotion_check(ctx, op, inp)
    cap = f"tối đa {inp.daily_cap} lần / ngày" + (f", mỗi lần ≤ {inp.money_cap_vnd:,} đ".replace(",", ".") if inp.money_cap_vnd is not None else "")
    return f"NỚI trợ lý TỰ LÀM “{op.title}” ({cap})"


@m.command("set_agent_policy",
           purpose="Siết hoặc nới quyền của trợ lý với một thao tác (đề xuất, tự làm, tắt) theo độ tin cậy công ty muốn.", reversible="Đặt lại chế độ cũ (siết bất kỳ lúc nào; nới lại cần chủ tài khoản duyệt)",
           resource="core.admin", action="manage", title="Đặt quyền cho trợ lý", agent="deny", sensitive=_policy_access)
async def set_agent_policy(ctx: Ctx, inp: SetAgentPolicy) -> dict[str, Any]:
    """Siết: bất kỳ lúc nào. NỚI lên "Tự làm" (0143): chỉ lệnh khai ``promotable``, thành tích đề xuất đủ ngưỡng công ty, bắt buộc trần số lần / ngày
    (và trần tiền nếu lệnh có ô tiền); căn cứ (thành tích lúc đặt) lưu kèm; qua chủ tài khoản duyệt."""
    op = registry.get(inp.command)
    if op is None or op.internal:
        raise NotFound(f"Không có thao tác {inp.command}")
    was = await ctx.val("SELECT mode FROM core.agent_policies WHERE command = %s", (inp.command,)) or op.agent
    subject = {"label": f"Quyền của trợ lý với “{op.title}”", "open": None}
    if not inp.mode:
        await ctx.exec("DELETE FROM core.agent_policies WHERE command = %s", (inp.command,))
        return {"command": inp.command, "mode": op.agent, "summary": f"“{op.title}”: trợ lý theo trần — {AGENT_MODE_LABEL[op.agent].lower()}", "subject": subject,
                "changes": [{"label": "Trợ lý được", "before": AGENT_MODE_LABEL[was], "after": AGENT_MODE_LABEL[op.agent]}] if was != op.agent else []}
    modes = registry.AGENT_MODES
    if inp.mode not in modes:
        raise Invalid("Chế độ không hợp lệ")
    basis: dict[str, Any] | None = None
    if modes.index(inp.mode) > modes.index(op.agent):
        if inp.mode != "allow":
            raise Invalid(f"Trần của “{op.title}” là “{AGENT_MODE_LABEL[op.agent]}” — chỉ siết thêm được, không nới")
        await _rule_required(ctx, "admin.set_agent_policy")   # không có luật ⇒ không chạy thẳng (fail closed)
        basis = await _promotion_check(ctx, op, inp)   # kiểm lại sau duyệt: thành tích lúc chạy mới là căn cứ
    await ctx.exec("""INSERT INTO core.agent_policies (command, mode, daily_cap, money_cap_vnd, basis, set_by, set_at) VALUES (%s, %s, %s, %s, %s, %s, now())
                      ON CONFLICT (tenant_id, command) DO UPDATE SET mode = EXCLUDED.mode, daily_cap = EXCLUDED.daily_cap,
                          money_cap_vnd = EXCLUDED.money_cap_vnd, basis = EXCLUDED.basis, set_by = EXCLUDED.set_by, set_at = now()""",
                   (inp.command, inp.mode, inp.daily_cap if basis else None, inp.money_cap_vnd if basis else None, Jsonb(basis) if basis else None,
                    ctx.p.user_id))
    what = "NỚI — trợ lý tự làm" if basis else f"siết — {AGENT_MODE_LABEL[inp.mode].lower()}"
    return {"command": inp.command, "mode": inp.mode, "promoted": basis is not None, "summary": f"“{op.title}”: {what}", "subject": subject,
            "changes": [{"label": "Trợ lý được", "before": AGENT_MODE_LABEL[was], "after": AGENT_MODE_LABEL[inp.mode]}] if was != inp.mode else []}


class SetAgentTrust(Input):
    min_proposals: int = field("Trợ lý phải có ít nhất bao nhiêu đề xuất đã duyệt", 20, ge=1, le=10000, help="Tính trong khoảng ngày bên dưới")
    min_approved_pct: int = field("Tỉ lệ đề xuất được duyệt nguyên văn tối thiểu (%)", 95, ge=0, le=100)
    window_days: int = field("Tính thành tích trong bao nhiêu ngày gần nhất", 30, ge=7, le=365, group="Điều chỉnh sâu hơn")
    max_errors_7d: int = field("Quá bao nhiêu lỗi trong 7 ngày thì trợ lý bị hạ về chỉ đề xuất", 3, ge=1, le=100, group="Điều chỉnh sâu hơn",
                               help="Lỗi gồm việc bị người sửa hoặc huỷ")
    reverse_days: int = field("Người sửa / huỷ trong bao nhiêu ngày sau lần trợ lý tự làm thì tính là sai", 7, ge=1, le=30, group="Điều chỉnh sâu hơn")
    autorun_low_risk: bool = field("Cho trợ lý làm ngay các việc rủi ro thấp, đảo lại được (có nút Hoàn tác)", False, group="Điều chỉnh sâu hơn",
                                   help="Tắt = trợ lý chỉ soạn đề xuất, trừ lệnh đã nới riêng")
    autorun_daily_cap: int = field("Mỗi lệnh được tự làm tối đa bao nhiêu lần mỗi ngày", 20, ge=1, le=10000, group="Điều chỉnh sâu hơn")


def _loosens(cur: dict[str, int], inp: SetAgentTrust) -> bool:
    """Dễ nới hơn: hạ số đề xuất / tỉ lệ, nâng số lỗi chịu được, hoặc ĐỔI cửa sổ (dài ra gom thêm đề xuất cũ, ngắn lại bỏ lần bị bỏ — review 08/11)."""
    return (inp.min_proposals < cur["min_proposals"] or inp.min_approved_pct < cur["min_approved_pct"] or inp.max_errors_7d > cur["max_errors_7d"]
            or inp.window_days != cur["window_days"] or inp.reverse_days < cur["reverse_days"]   # cửa sổ đảo ngắn hơn = bỏ sót đảo = nới
            or (inp.autorun_low_risk and not cur["autorun_low_risk"]) or inp.autorun_daily_cap > cur["autorun_daily_cap"])   # bật công tắc / nâng trần = nới


async def _trust_access(ctx: Ctx, inp: SetAgentTrust) -> str | None:
    """Nới ngưỡng ⇒ chủ tài khoản duyệt; siết thì không."""
    if _loosens(await _trust(ctx), inp):
        return (f"NỚI ngưỡng tin cậy trợ lý: ≥ {inp.min_proposals} đề xuất, ≥ {inp.min_approved_pct}% duyệt nguyên văn / {inp.window_days} ngày; "
                f"tự hạ khi ≥ {inp.max_errors_7d} lỗi / 7 ngày")
    return None


@m.command("set_agent_trust",
           purpose="Đặt ngưỡng thành tích để một thao tác của trợ lý được nới lên Tự làm.", reversible="Chạy lại lệnh với ngưỡng cũ",
           resource="core.admin", action="manage", title="Đặt ngưỡng tin cậy của trợ lý", agent="deny", sensitive=_trust_access)
async def set_agent_trust(ctx: Ctx, inp: SetAgentTrust) -> dict[str, Any]:
    """Ngưỡng để một lệnh được nới lên Tự làm là DỮ LIỆU công ty (0143): công ty cẩn thận giữ 95%, công ty tin trợ lý đặt 90% — code chỉ kiểm."""
    cur = await _trust(ctx)
    if _loosens(cur, inp):
        await _rule_required(ctx, "admin.set_agent_trust")   # nới ngưỡng mà không có luật duyệt ⇒ từ chối (fail closed)
    await ctx.exec("""INSERT INTO core.agent_trust (min_proposals, min_approved_pct, window_days, max_errors_7d, reverse_days, autorun_low_risk,
                                                    autorun_daily_cap, updated_by, updated_at)
                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                      ON CONFLICT (tenant_id) DO UPDATE SET min_proposals = EXCLUDED.min_proposals, min_approved_pct = EXCLUDED.min_approved_pct,
                          window_days = EXCLUDED.window_days, max_errors_7d = EXCLUDED.max_errors_7d, reverse_days = EXCLUDED.reverse_days,
                          autorun_low_risk = EXCLUDED.autorun_low_risk, autorun_daily_cap = EXCLUDED.autorun_daily_cap,
                          updated_by = EXCLUDED.updated_by, updated_at = now()""",
                   (inp.min_proposals, inp.min_approved_pct, inp.window_days, inp.max_errors_7d, inp.reverse_days, inp.autorun_low_risk,
                    inp.autorun_daily_cap, ctx.p.user_id))
    changes = [{"label": lbl, "before": str(cur[k]), "after": str(getattr(inp, k))} for lbl, k in
               (("Số đề xuất tối thiểu", "min_proposals"), ("Tỉ lệ duyệt nguyên văn tối thiểu (%)", "min_approved_pct"), ("Cửa sổ tính (ngày)", "window_days"),
                ("Lỗi tối đa trong 7 ngày", "max_errors_7d"), ("Ngày tính là sửa sai", "reverse_days"), ("Tự làm tối đa mỗi ngày", "autorun_daily_cap"))
               if cur.get(k) != getattr(inp, k)]
    return {"subject": {"label": "Ngưỡng tin cậy của trợ lý", "open": None}, "changes": changes,
            "summary": f"Ngưỡng tin cậy: ≥ {inp.min_proposals} đề xuất, ≥ {inp.min_approved_pct}% duyệt nguyên văn trong {inp.window_days} ngày; "
                       f"tự hạ khi ≥ {inp.max_errors_7d} lỗi / 7 ngày; lệnh rủi ro thấp đảo được: "
                       + (f"trợ lý làm ngay (≤ {inp.autorun_daily_cap} lần / ngày / lệnh)" if inp.autorun_low_risk else "chỉ đề xuất")}


# ── Mức tự chủ đặt sẵn (0144 "preset chỉ là GIAO DIỆN"): ba gói tên gọi GHI vào dữ liệu đã có (công tắc ``core.agent_trust`` + ``core.agent_policies``),
# không máy móc mới. Mức đang áp SUY từ dữ liệu (không lưu tên preset) ⇒ ai sửa một lệnh / một nhóm là màn ghi "Tuỳ chỉnh". Áp một mức = đặt LẠI cấu
# hình tự chủ cho khớp mức đó (dòng chính sách từng lệnh bị thay; nhóm "Tắt" ở màn Nhóm công cụ giữ nguyên — đó là chuyện trợ lý có công cụ hay không).

PRESETS: dict[str, tuple[str, str]] = {
    "careful": ("Thận trọng", "Trợ lý chỉ soạn đề xuất. Mọi việc ghi chờ người bấm."),
    "balanced": ("Cân bằng", "Việc nhỏ đảo được trợ lý làm ngay, có nút Hoàn tác. Còn lại chờ người bấm."),
    "full": ("Toàn quyền", "Như Cân bằng, trần cao hơn. Tiền, duyệt phiếu, đổi quyền vẫn luôn do người."),
}
PRESET_CAP = {"careful": 20, "balanced": 20, "full": 50}   # trần lần tự làm / ngày / lệnh của mức


def _write_groups() -> list[str]:
    """Nhóm công cụ có lệnh GHI trợ lý gọi được (mỗi phân hệ một nhóm) — "Thận trọng" đặt cả nhóm "chỉ đề xuất"."""
    return sorted({executor.group_key(o) for o in registry.OPS.values() if o.kind == "command" and not o.internal and o.public is None})


def _promotable() -> list[registry.Op]:
    return sorted((o for o in registry.OPS.values() if o.promotable and not o.internal), key=lambda o: o.name)


async def _autonomy(ctx: Ctx) -> tuple[dict[str, str], bool, int]:
    """(chính sách {lệnh | nhóm: mode}, công tắc, trần công tắc) — cấu hình tự chủ hiện tại của công ty."""
    on, cap = await executor.autorun_switch(ctx.conn)
    return await executor.policy_overrides(ctx.conn), on, cap


def preset_of(overrides: dict[str, str], switch: bool) -> str:
    """Mức đặt sẵn KHỚP dữ liệu: careful / balanced / full; "default" = chưa chọn (công ty mới: tắt, không dòng nào); "" = tuỳ chỉnh."""
    per_op = {c: v for c, v in overrides.items() if not c.endswith(".*")}
    groups = {c: v for c, v in overrides.items() if c.endswith(".*")}
    soft = any(v == "propose" for v in groups.values())
    if not switch and not per_op and all(groups.get(g) in ("propose", "deny") for g in _write_groups()):
        return "careful"
    if not switch and not per_op and not soft:
        return "default"
    if switch and not soft and not per_op:
        return "balanced"
    if switch and not soft and per_op == {o.name: "allow" for o in _promotable()}:
        return "full"
    return ""


def _preset_target(preset: str, overrides: dict[str, str]) -> tuple[dict[str, str], bool]:
    """Chính sách + công tắc SAU khi áp mức: nhóm đang "Tắt" giữ nguyên, mọi dòng khác theo mức."""
    off = {g: v for g, v in overrides.items() if g.endswith(".*") and v == "deny"}
    if preset == "careful":
        return {**{g: "propose" for g in _write_groups()}, **off}, False
    if preset == "full":
        return {**off, **{o.name: "allow" for o in _promotable()}}, True
    return off, True


def _loosened(cur: dict[str, str], cur_on: bool, new: dict[str, str], new_on: bool) -> list[registry.Op]:
    """Lệnh trợ lý được làm NHIỀU hơn sau thay đổi (so bằng chính ``decide`` qua ``effective_mode`` — không tự tính)."""
    rank = registry.AGENT_MODES.index
    return [o for o in sorted(registry.OPS.values(), key=lambda o: o.name) if not o.internal and o.public is None
            and rank(executor.effective_mode(o, new, new_on)) > rank(executor.effective_mode(o, cur, cur_on))]


class SetAgentPreset(Input):
    preset: Literal["careful", "balanced", "full"] = field("Mức tự chủ", options={k: v[0] for k, v in PRESETS.items()})


async def _preset_change(ctx: Ctx, inp: SetAgentPreset) -> tuple[dict[str, str], bool, list[registry.Op], bool]:
    cur, on, cap = await _autonomy(ctx)
    new, new_on = _preset_target(inp.preset, cur)
    return new, new_on, _loosened(cur, on, new, new_on), new_on and PRESET_CAP[inp.preset] > cap


async def _preset_access(ctx: Ctx, inp: SetAgentPreset) -> str | None:
    """NỚI (thêm lệnh trợ lý tự làm, nâng trần) ⇒ chủ tài khoản duyệt cả mức một lần, phiếu nói rõ nới những gì; siết ⇒ áp ngay. Duyệt mức thay cho
    ngưỡng thành tích từng lệnh (0143) — vì vậy phiếu phải liệt kê đủ."""
    _, _, loose, cap_up = await _preset_change(ctx, inp)
    if not loose and not cap_up:
        return None
    names = ", ".join(o.title for o in loose)
    what = f"Bật {PRESETS[inp.preset][0]}: trợ lý tự làm {len(loose)} lệnh — {names}" if loose else f"Bật {PRESETS[inp.preset][0]}"
    if cap_up:
        what += f"; trần {PRESET_CAP[inp.preset]} lần / ngày / lệnh"
    return what + " (vẫn có Hoàn tác; tiền, duyệt phiếu, đổi quyền luôn do người)"


@m.command("set_agent_preset",
           purpose="Chọn mức tự chủ đặt sẵn cho trợ lý thay vì chỉnh từng lệnh.", reversible="Chọn lại mức trước (chỉnh riêng từng lệnh đã bị mức mới thay thì không tự quay lại)",
           resource="core.admin", action="manage", title="Chọn mức tự chủ của trợ lý", agent="deny", sensitive=_preset_access)
async def set_agent_preset(ctx: Ctx, inp: SetAgentPreset) -> dict[str, Any]:
    """Ghi mức đặt sẵn vào dữ liệu đã có: công tắc + trần (``core.agent_trust``) và dòng chính sách (``core.agent_policies``). "Toàn quyền" ghi dòng
    nới từng lệnh ``promotable`` có căn cứ "mức Toàn quyền, chủ tài khoản duyệt" — vòng quét tự hạ vẫn áp như lệnh nới tay."""
    cur, _, _ = await _autonomy(ctx)
    new, new_on, loose, cap_up = await _preset_change(ctx, inp)
    if loose or cap_up:
        await _rule_required(ctx, "admin.set_agent_preset")   # nới mà công ty không có luật duyệt ⇒ từ chối (fail closed)
    cap = PRESET_CAP[inp.preset]
    for command in set(cur) - set(new):
        await ctx.exec("DELETE FROM core.agent_policies WHERE command = %s", (command,))
    basis = Jsonb({"preset": inp.preset, "promoted_on": today().isoformat()})
    for command, mode in new.items():
        if cur.get(command) == mode and mode != "allow":
            continue
        await ctx.exec("""INSERT INTO core.agent_policies (command, mode, daily_cap, basis, set_by, set_at) VALUES (%s, %s, %s, %s, %s, now())
                          ON CONFLICT (tenant_id, command) DO UPDATE SET mode = EXCLUDED.mode, daily_cap = EXCLUDED.daily_cap, money_cap_vnd = NULL,
                              basis = EXCLUDED.basis, set_by = EXCLUDED.set_by, set_at = now()""",
                       (command, mode, cap if mode == "allow" else None, basis if mode == "allow" else None, ctx.p.user_id))
    t = TRUST_DEFAULTS
    await ctx.exec("""INSERT INTO core.agent_trust (min_proposals, min_approved_pct, window_days, max_errors_7d, reverse_days, autorun_low_risk,
                                                    autorun_daily_cap, updated_by, updated_at)
                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                      ON CONFLICT (tenant_id) DO UPDATE SET autorun_low_risk = EXCLUDED.autorun_low_risk, autorun_daily_cap = EXCLUDED.autorun_daily_cap,
                          updated_by = EXCLUDED.updated_by, updated_at = now()""",
                   (t["min_proposals"], t["min_approved_pct"], t["window_days"], t["max_errors_7d"], t["reverse_days"], new_on, cap, ctx.p.user_id))
    title = PRESETS[inp.preset][0]
    return {"preset": inp.preset, "loosened": [o.name for o in loose],
            "summary": f"Trợ lý: {title}" + (f" — tự làm {', '.join(o.title for o in loose)}" if loose else "")}


@m.command("review_agent_policies", resource="core.admin", title="Rà lệnh trợ lý được tự làm", internal=True, agent="deny",
           sweep="SELECT DISTINCT tenant_id FROM core.agent_policies WHERE mode = 'allow' AND basis IS NOT NULL")
async def review_agent_policies(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Vòng quét (quyền hệ thống): lệnh được nới mà trong 7 ngày trợ lý tự làm LỖI + bị người ĐẢO (sửa / huỷ — ``_reversals``) đạt ngưỡng công ty ⇒
    tự hạ về đề xuất (gỡ dòng nới), báo chủ tài khoản — nới lại phải qua duyệt như lần đầu. Lời báo mẫu, không gọi model."""
    trust = await _trust(ctx)
    rec = await _track_record(ctx, 7)
    owners = [r["user_id"] for r in await ctx.all("SELECT user_id FROM core.memberships WHERE owner AND active")]
    demoted = 0
    promoted = {r["command"] for r in await ctx.all("SELECT command FROM core.agent_policies WHERE mode = 'allow' AND basis IS NOT NULL")}
    # lệnh chạy dưới CÔNG TẮC (0144): promotable, công ty bật công tắc, chưa có dòng chính sách riêng
    overrides = set(await executor.policy_overrides(ctx.conn))
    under_switch = {o.name for o in registry.OPS.values() if o.promotable and o.name not in overrides} if trust["autorun_low_risk"] else set()
    for command in sorted(promoted | under_switch):
        rc = rec.get(command, {})
        errs, back = rc.get("errors_7d", 0), rc.get("reversed_7d", 0)
        if errs + back < trust["max_errors_7d"]:
            continue
        r = {"command": command}
        if command in promoted:
            await ctx.exec("DELETE FROM core.agent_policies WHERE command = %s", (command,))
        else:   # dưới công tắc: GHI một dòng siết "propose" cho đúng lệnh — công tắc vẫn bật cho lệnh khác, người thấy trên màn vì sao
            await ctx.exec("""INSERT INTO core.agent_policies (command, mode, set_by, set_at) VALUES (%s, 'propose', NULL, now())
                              ON CONFLICT (tenant_id, command) DO UPDATE SET mode = 'propose', basis = NULL, set_at = now()""", (command,))
        op = registry.get(r["command"])
        title = op.title if op else r["command"]
        await ctx.notify(owners, kind="agent_trust", title=f"Trợ lý bị hạ về đề xuất: {title}",
                         body=f"Trong 7 ngày: {errs} lỗi khi tự làm, {back} lần người sửa / huỷ việc trợ lý đã làm (ngưỡng {trust['max_errors_7d']}). "
                              "Rõ nguyên nhân rồi nới lại ở “Trợ lý được làm gì”.",
                         open={"query": "admin.agent_policies", "input": {}}, key=f"agent_trust:{r['command']}:{today()}")
        demoted += 1
    return {"demoted": demoted}


@m.query("op_options", resource="core.admin", title="Mọi thao tác")
async def op_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [{"value": op.name, "label": f"{registry.MODULES[op.module]} › {op.title}"}
            for op in sorted(registry.OPS.values(), key=lambda o: o.name) if not op.internal]


# ── Gói nghiệp vụ (quyết định 0045) — công ty dùng gói nào; tắt ⇒ menu, nút, công cụ trợ lý của gói đó biến mất, dữ liệu vẫn giữ ──

@m.query("packs", resource="core.admin", title="Gói nghiệp vụ")
async def packs_view(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    on = await packs.enabled(ctx.conn, ctx.p.tenant_id)
    fixed = registry.always_on()
    hidden = await packs.hidden(ctx.conn, ctx.p.tenant_id)   # gói riêng của công ty khác: như không có
    rows = []
    for key, pk in registry.PACKS.items():
        if key in hidden:
            continue
        label, desc = pk.title, pk.description
        mods = [registry.MODULES[m_] for m_, pk in registry.MODULE_PACK.items() if pk == key]
        needs = ", ".join(registry.pack_title(r) for r in pk.requires)
        rows.append({"id": key, "label": label, "status": "always" if key in fixed else ("on" if key in on else "off"),
                     "kind": pk.kind, "description": desc, "modules": ", ".join(mods), "needs": needs})
    about = await ctx.val("SELECT about FROM core.tenant_profile") or ""
    return ui.table(rows, [
        ui.col("label", "Gói"), ui.col("status", "Trạng thái", "badge", labels={"always": "Luôn bật", "on": "Đang dùng", "off": "Chưa bật"},
                                       tones={"always": "gray", "on": "green", "off": "orange"}),
        ui.col("kind", "Loại", "badge", labels={"core": "Lõi", "capability": "Năng lực dùng chung", "industry": "Nghiệp vụ ngành", "company": "Riêng công ty"},
               tones={"core": "gray", "capability": "blue", "industry": "purple", "company": "orange"}),
        ui.col("description", "Gồm", width=420), ui.col("needs", "Bật kèm gói"), ui.col("modules", "Module"),
    ], title="Gói nghiệp vụ",
        note="Tắt một gói: dữ liệu vẫn giữ nguyên, chỉ ẩn màn / nút / công cụ của trợ lý và dừng việc tự động của gói đó. "
             f"Giới thiệu công ty cho trợ lý: “{about or 'chưa đặt'}”.")


@m.query("profile", resource="core.admin", title="Hồ sơ công ty cho trợ lý")
async def profile(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Một dòng: câu "công ty làm gì" trợ lý mở đầu bằng (nút Sửa điền sẵn câu hiện tại)."""
    r = await ctx.one("SELECT about, updated_at FROM core.tenant_profile") or {"about": "", "updated_at": None}
    return ui.table([{"id": "profile", "about": r["about"], "updated_at": r["updated_at"]}],
                    [ui.col("about", "Công ty làm gì (trợ lý mở đầu bằng câu này)", width=520), ui.col("updated_at", "Cập nhật", "datetime")],
                    title="Hồ sơ công ty cho trợ lý",
                    note="Phần nghiệp vụ trợ lý tự theo các gói đang bật — ở đây chỉ ghi công ty làm gì, không ghi chỉ dẫn cho trợ lý.")


class Profile(Input):
    about: str = field("Công ty làm gì (một câu cho trợ lý)", "", kind="textarea", max_length=600,
                       example="công ty dịch vụ — phân phối [[offering|l]] cho khách doanh nghiệp",
                       help="Trợ lý mở đầu bằng câu này; phần nghiệp vụ tự theo các gói đang bật. Không ghi chỉ dẫn cho trợ lý ở đây.")


@m.command("set_profile",
           purpose="Cho trợ lý biết công ty làm gì để trả lời đúng ngành nghề.", reversible="Chạy lại lệnh với câu giới thiệu cũ",
           resource="core.admin", action="manage", title="Giới thiệu công ty cho trợ lý", agent="deny")
async def set_profile(ctx: Ctx, inp: Profile) -> dict[str, Any]:
    """Cấu hình RIÊNG của công ty (0056) — không viết cứng ngành trong code. Trợ lý không tự sửa lời dặn của chính nó."""
    was = await ctx.val("SELECT about FROM core.tenant_profile") or ""
    await ctx.exec("""INSERT INTO core.tenant_profile (about, updated_by) VALUES (%s, core.me())
                      ON CONFLICT (tenant_id) DO UPDATE SET about = EXCLUDED.about, updated_by = EXCLUDED.updated_by, updated_at = now()""",
                   (inp.about.strip(),))
    return {"summary": "Đã lưu giới thiệu công ty cho trợ lý", "subject": {"label": "Giới thiệu công ty cho trợ lý", "open": None},
            "changes": [{"label": "Giới thiệu", "before": was[:200], "after": inp.about.strip()[:200]}] if was != inp.about.strip() else []}


# ── Nhận diện thương hiệu công ty (ADR 0249 lát 2): MỘT bộ cho mọi thứ công ty phát ra (ấn phẩm, giấy tờ, Word) — trước nằm riêng ở Soạn với AI ──

LOGO_MAX = 500 * 1024


async def _brand_row(ctx: Ctx) -> dict[str, Any]:
    r = await ctx.one("SELECT primary_color, font, footer, logo_file_id, logo_filename, updated_at FROM admin.brand")
    return {**company_settings.BRAND_DEFAULT, "logo_file_id": None, "logo_filename": "", "updated_at": None, **(r or {})}


@m.query("brand_view", resource="core.admin", action="manage", title="Nhận diện thương hiệu")
async def brand_view(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Một dòng: màu chính, phông, chân trang, logo của công ty — ấn phẩm, giấy tờ, Word xuất ra theo bộ này (nút Sửa điền sẵn giá trị hiện tại)."""
    b = await _brand_row(ctx)
    return ui.table([{"id": "brand", "primary_color": b["primary_color"], "color": f"#{b['primary_color']}", "font": b["font"], "footer": b["footer"],
                      "logo": b["logo_filename"] or "(chưa có)", "updated_at": b["updated_at"]}],
                    [ui.col("color", "Màu chính"), ui.col("font", "Phông chữ"), ui.col("footer", "Chân trang", width=320), ui.col("logo", "Logo"),
                     ui.col("updated_at", "Cập nhật", "datetime")],
                    title="Nhận diện thương hiệu",
                    note="Dùng chung cho mọi thứ công ty xuất ra: ấn phẩm (tờ rơi, tờ giới thiệu, slide), giấy tờ, file Word.")


class SetBrand(Input):
    primary_color: str = field("Màu chính (hex)", "1F4E79", max_length=7, example="1F4E79")
    font: Literal["Arial", "Calibri", "Times New Roman", "Roboto", "Be Vietnam Pro"] = field(
        "Phông chữ", "Arial", options={f: f for f in company_settings.FONTS})
    footer: str = field("Chân trang", "", max_length=200, example="Công ty CP Mẫu · 010 000 0000 · mau.example")
    logo_b64: str = field("Logo (PNG / JPEG, base64)", "", hidden=True, max_length=700_000)
    logo_filename: str = field("Tên tệp logo", "", hidden=True, max_length=120)


@m.command("set_brand",
           purpose="Để ấn phẩm, giấy tờ và tệp Word công ty phát ra mang đúng màu, phông, chân trang và logo.", reversible="Chạy lại lệnh với màu, phông, chân trang cũ (logo cũ phải gửi lại)",
           resource="core.admin", action="manage", title="Sửa nhận diện thương hiệu", agent="propose")
async def set_brand(ctx: Ctx, inp: SetBrand) -> dict[str, Any]:
    """Màu / phông / chân trang / logo của công ty. Logo mới thay logo cũ (logo cũ được thu hồi ở kho tệp); không gửi logo ⇒ giữ logo đang có."""
    color = inp.primary_color.removeprefix("#").upper()
    if not re.fullmatch(r"[0-9A-F]{6}", color):
        raise Invalid("Màu viết dạng hex 6 ký tự, vd 1F4E79")
    logo_id = None
    if inp.logo_b64:
        if not inp.logo_filename.lower().endswith((".png", ".jpg", ".jpeg")):
            raise Invalid("Logo nhận PNG / JPEG")
        try:
            raw = base64.b64decode(inp.logo_b64, validate=True)
        except ValueError:
            raise Invalid("Logo hỏng (base64)") from None
        stored = await files.put(ctx, raw, inp.logo_filename, "image/png" if inp.logo_filename.lower().endswith(".png") else "image/jpeg", max_bytes=LOGO_MAX)
        logo_id = stored.id
    was = await _brand_row(ctx)
    old = was["logo_file_id"]
    await ctx.exec("""INSERT INTO admin.brand (primary_color, font, footer, logo_file_id, logo_filename, updated_by) VALUES (%s, %s, %s, %s, %s, core.me())
                      ON CONFLICT (tenant_id) DO UPDATE SET primary_color = EXCLUDED.primary_color, font = EXCLUDED.font, footer = EXCLUDED.footer,
                          logo_file_id = coalesce(EXCLUDED.logo_file_id, admin.brand.logo_file_id),
                          logo_filename = CASE WHEN EXCLUDED.logo_file_id IS NULL THEN admin.brand.logo_filename ELSE EXCLUDED.logo_filename END,
                          updated_by = EXCLUDED.updated_by, updated_at = now()""",
                   (color, inp.font, inp.footer.strip(), logo_id, inp.logo_filename if logo_id else ""))
    if logo_id and old:
        await files.release(ctx, old)
    changes = [{"label": lbl, "before": str(was[k]), "after": str(now)} for lbl, k, now in
               (("Màu chính", "primary_color", color), ("Phông", "font", inp.font), ("Chân trang", "footer", inp.footer.strip())) if str(was[k]).removeprefix("#").upper() != str(now).removeprefix("#").upper()]
    if logo_id:
        changes.append({"label": "Logo", "before": was["logo_filename"] or "Chưa có", "after": inp.logo_filename})
    return {"summary": "Đã lưu nhận diện thương hiệu", "subject": {"label": "Nhận diện thương hiệu công ty", "open": None}, "changes": changes}


@m.query("brand", resource=None, title="Nhận diện thương hiệu (cho tệp xuất)", internal=True, agent="deny", family="brand",
         why="Họ brand (gói xuất tệp / ấn phẩm — xem trước, xuất Word / PDF / PowerPoint / trang web, trang chia sẻ — ADR 0249): bộ nhận diện CỦA CÔNG TY người gọi để vẽ tệp. "
             "Trả {primary_color, font, footer, logo_filename, logo_type, logo_b64} — dữ liệu công ty ai trong công ty cũng thấy trên tệp xuất, không dữ liệu người.")
async def brand(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Bộ nhận diện để vẽ tệp. ``logo_b64`` rỗng khi chưa có logo / logo không đọc được (tệp vẫn xuất, không logo)."""
    b = await _brand_row(ctx)
    logo, ctype = b"", ""
    if b["logo_file_id"]:
        try:
            logo = await files.read(ctx, b["logo_file_id"])
            ctype = "image/png" if b["logo_filename"].lower().endswith(".png") else "image/jpeg"
        except (Denied, NotFound):
            logo = b""
    return {"primary_color": b["primary_color"], "font": b["font"], "footer": b["footer"], "logo_filename": b["logo_filename"],
            "logo_type": ctype, "logo_b64": base64.b64encode(logo).decode() if logo else ""}


@m.query("term_options", resource="core.admin", title="Khái niệm có nhãn riêng")
async def term_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [{"value": k, "label": t.meaning[:1].upper() + t.meaning[1:]} for k, t in vocab.TERMS.items()]


@m.query("vocabulary", resource="core.admin", title="Từ vựng công ty")
async def vocabulary(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Mỗi khái niệm một dòng: công ty đang gọi là gì (0124). Chữ này hiện ở form, cột, thẻ, tên màn và lời trợ lý nói."""
    mine = {r["term"]: r["label"] for r in await ctx.all("SELECT term, label FROM core.vocabulary")}
    rows = [{"id": k, "meaning": t.meaning[:1].upper() + t.meaning[1:], "label": vocab.label(k, mine), "set": "Công ty đặt" if k in mine else "Mặc định"}
            for k, t in vocab.TERMS.items()]
    return ui.table(rows, [ui.col("meaning", "Khái niệm", width=320), ui.col("label", "Công ty gọi là", width=200),
                           ui.col("set", "", "badge", tones={"Công ty đặt": "blue", "Mặc định": "gray"})],
                    title="Từ vựng công ty",
                    note="Đổi chữ hiện ra cho người dùng và trợ lý (form, cột, thẻ, tên màn); tên lệnh, khoá nhập Excel và dữ liệu giữ nguyên. "
                         "Gói ngành mang theo chữ mặc định của ngành khi bật.")


class SetTerm(Input):
    term: str = field("Khái niệm", options_query="admin.term_options")
    label: str = field("Công ty gọi là", "", max_length=vocab.MAX_LABEL, example="Gói dịch vụ", help="Một cụm ngắn, viết như giữa câu (chữ đầu tự viết hoa khi cần). "
                       "Để trống = bỏ chữ riêng, dùng chữ mặc định.")


@m.command("set_term",
           purpose="Đặt cách gọi riêng của công ty cho một khái niệm để màn hình và trợ lý dùng đúng từ.", reversible="Chạy lại lệnh với từ cũ, hoặc để trống để về chữ mặc định",
           resource="core.admin", action="manage", title="Đổi từ vựng công ty", agent="deny")
async def set_term(ctx: Ctx, inp: SetTerm) -> dict[str, Any]:
    """Đặt nhãn riêng của công ty cho một khái niệm (0124). Trợ lý không tự đổi từ vựng — nó định hình chính lời dặn của trợ lý."""
    if inp.term not in vocab.TERMS:
        raise Invalid("Khái niệm không có trong danh sách")
    if bad := vocab.problem(inp.label):
        raise Invalid(bad)
    label = vocab.clean(inp.label)
    was = await ctx.val("SELECT label FROM core.vocabulary WHERE term = %s", (inp.term,))
    if label:
        await ctx.exec("""INSERT INTO core.vocabulary (term, label, updated_by) VALUES (%s, %s, core.me())
                          ON CONFLICT (tenant_id, term) DO UPDATE SET label = EXCLUDED.label, updated_by = EXCLUDED.updated_by, updated_at = now()""",
                       (inp.term, label))
    else:
        await ctx.exec("DELETE FROM core.vocabulary WHERE term = %s", (inp.term,))
    ctx.after_commit(_forget_vocab(ctx.p.tenant_id))
    return {"term": inp.term, "label": label, "summary": f"Từ nay gọi {vocab.TERMS[inp.term].meaning} là “{vocab.label(inp.term, {inp.term: label})}”",
            "subject": {"label": f"Từ vựng: {vocab.TERMS[inp.term].meaning}", "open": None},
            "changes": [{"label": "Cách gọi", "before": vocab.label(inp.term, {inp.term: was or ""}), "after": vocab.label(inp.term, {inp.term: label})}]}


# ── Cài đặt công ty (0128): công tắc bật / tắt do gói khai (Module.setting), giá trị là dữ liệu công ty ──

def _setting_label(key: str) -> str:
    s = company_settings.SETTINGS.get(key)
    return s.label if s else key


@m.query("setting_options", resource="core.admin", title="Cài đặt công ty có thể đổi")
async def setting_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    on = await _enabled(ctx)
    return [{"value": k, "label": s.label} for k, s in company_settings.SETTINGS.items() if registry.MODULE_PACK.get(s.module, "base") in on]


@m.query("settings", resource="core.admin", title="Cài đặt công ty")
async def settings_page(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Mỗi công tắc của gói ĐANG BẬT một dòng: đang bật / tắt, do công ty đặt hay theo mặc định của gói."""
    on = await _enabled(ctx)
    mine = await company_settings.values(ctx.conn, ctx.p.tenant_id)
    rows = [{"id": k, "label": s.label, "help": s.help, "pack": registry.pack_title(registry.MODULE_PACK.get(s.module, "base")),
             "state": "on" if mine.get(k, s.default) else "off", "enabled": mine.get(k, s.default), "set": "Công ty đặt" if k in mine else "Mặc định"}
            for k, s in company_settings.SETTINGS.items() if registry.MODULE_PACK.get(s.module, "base") in on]
    return ui.table(rows, [ui.col("label", "Cài đặt", width=300), ui.col("state", "Đang", "badge", labels={"on": "Bật", "off": "Tắt"},
                                                                         tones={"on": "green", "off": "gray"}),
                           ui.col("help", "Khi bật", width=380), ui.col("pack", "Gói"),
                           ui.col("set", "", "badge", tones={"Công ty đặt": "blue", "Mặc định": "gray"})],
                    title="Cài đặt công ty", note="Công tắc của các gói đang bật. Đổi có hiệu lực từ lần tới việc đó xảy ra; không sửa thứ đã tạo.")


class SetSetting(Input):
    key: str = field("Cài đặt", options_query="admin.setting_options")
    enabled: bool = field("Bật", True)


@m.command("set_setting",
           purpose="Bật hoặc tắt một công tắc nghiệp vụ của công ty cho khớp cách công ty làm việc.", reversible="Bật / tắt lại công tắc này",
           resource="core.admin", action="manage", title="Đổi cài đặt công ty", agent="propose", category="setup")
async def set_setting(ctx: Ctx, inp: SetSetting) -> dict[str, Any]:
    """Bật / tắt một công tắc của gói đang bật (0128). Trợ lý chỉ đề xuất — người quản trị bấm mới đổi."""
    return await _apply_setting(ctx, inp)


async def _apply_setting(ctx: Ctx, inp: SetSetting) -> dict[str, Any]:
    """Ghi một công tắc — MỘT chỗ kiểm cho lệnh của quản trị lẫn cửa nhà vận hành (0206)."""
    s = company_settings.SETTINGS.get(inp.key)
    if s is None or registry.MODULE_PACK.get(s.module, "base") not in await _enabled(ctx):
        raise NotFound("Không có cài đặt này (hoặc gói của nó chưa bật)")
    was = await ctx.val("SELECT enabled FROM core.settings WHERE key = %s", (inp.key,))
    was = s.default if was is None else was
    await ctx.exec("""INSERT INTO core.settings (key, enabled, updated_by) VALUES (%s, %s, core.me())
                      ON CONFLICT (tenant_id, key) DO UPDATE SET enabled = EXCLUDED.enabled, updated_by = EXCLUDED.updated_by, updated_at = now()""",
                   (inp.key, inp.enabled))
    return {"key": inp.key, "enabled": inp.enabled, "summary": f"{s.label}: {'bật' if inp.enabled else 'tắt'}", "subject": {"label": s.label, "open": None},
            "changes": [{"label": "Công tắc", "before": "Bật" if was else "Tắt", "after": "Bật" if inp.enabled else "Tắt"}]}


class OperatorSetting(SetSetting):
    by: str = operator_door.by_field()
    why: str = operator_door.why_field()


@m.command("operator_set_setting", resource=operator_door.RESOURCE, action="manage", title="Đổi cài đặt công ty (nhà vận hành)", internal=True,
           agent="deny", category="setup")
async def operator_set_setting(ctx: Ctx, inp: OperatorSetting) -> dict[str, Any]:
    """Cùng kiểm như ``set_setting`` (công tắc đã khai, gói của nó đang bật): nhà vận hành thiết lập công ty lúc đưa vào dùng — vd tắt nghiệp vụ công ty
    không làm (``Screen.feature``). Nhật ký ghi người vận hành + lý do."""
    operator_door.require_who(inp)
    return await _apply_setting(ctx, inp)


# ── Trường riêng của công ty (0060 lát 1): quản trị tự thêm trường cho đối tượng module mở (``Module.extensible``) — không sửa mã gói ──

@m.query("field_subject_options", resource="core.admin", title="Đối tượng thêm được trường riêng")
async def field_subject_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    on = await _enabled(ctx)
    return [{"value": s.key, "label": s.label} for s in extra_fields.SUBJECTS.values() if registry.MODULE_PACK.get(s.module, "base") in on]


@m.query("field_defs", resource="core.admin", title="Trường riêng của công ty")
async def field_defs_page(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Mỗi trường công ty tự thêm một dòng: của đối tượng nào, kiểu gì, đang dùng hay đã ngừng. Giá trị nằm ở chính đối tượng (khách, …)."""
    rows = await ctx.all("SELECT id, subject, key, label, kind, options, help, active FROM core.field_defs ORDER BY subject, position, created_at")
    out = [{**r, "subject_label": extra_fields.SUBJECTS[r["subject"]].label if r["subject"] in extra_fields.SUBJECTS else r["subject"],
            "kind_label": extra_fields.KINDS.get(r["kind"], r["kind"]), "choices": ", ".join(str(o) for o in r["options"] or ()),
            "state": "on" if r["active"] else "off"} for r in rows]
    return ui.table(out, [ui.col("subject_label", "Của", width=160), ui.col("label", "Tên trường", width=220), ui.col("kind_label", "Kiểu"),
                          ui.col("choices", "Lựa chọn", width=260),
                          ui.col("state", "Đang", "badge", labels={"on": "Dùng", "off": "Ngừng"}, tones={"on": "green", "off": "gray"})],
                    title="Trường riêng của công ty",
                    note="Trường công ty tự thêm cho khách và các đối tượng khác — hiện trên form, thẻ và trợ lý. Ngừng dùng thì ẩn đi, giá trị đã ghi vẫn còn. "
                         "Tiền, quyền và trạng thái nghiệp vụ không thêm ở đây.")


class SaveField(Input):
    subject: str = field("Thêm cho", options_query="admin.field_subject_options")
    label: str = field("Tên trường", max_length=80, example="Mã công tơ")
    kind: Literal["text", "number", "date", "choice"] = field("Kiểu", "text", options=extra_fields.KINDS)
    options: str = field("Các lựa chọn", "", max_length=1000, example="Vàng, Bạc, Đồng", help="Chỉ cho kiểu Chọn một — cách nhau bằng dấu phẩy")
    help: str = field("Gợi ý khi điền", "", max_length=300)
    active: bool = field("Đang dùng", True)
    key: str = field("Mã trường", "", hidden=True)


@m.command("save_field",
           purpose="Hồ sơ cần ghi thêm một thông tin riêng của công ty — thêm, sửa hoặc ngừng dùng trường đó.", reversible="Sửa lại tên, lựa chọn hoặc bật dùng lại trường (giá trị đã nhập vẫn giữ)",
           resource="core.admin", action="manage", title="Thêm / sửa trường riêng", agent="propose", category="setup")
async def save_field(ctx: Ctx, inp: SaveField) -> dict[str, Any]:
    """Thêm một trường riêng cho đối tượng (hoặc sửa tên / lựa chọn / ngừng dùng trường đã có — ``key`` của dòng). Mã trường do máy đặt từ tên và không
    đổi sau đó; KIỂU không đổi được sau khi tạo (giá trị đã ghi theo kiểu cũ). Trợ lý chỉ đề xuất — quản trị bấm mới đổi."""
    s = extra_fields.SUBJECTS.get(inp.subject)
    if s is None or registry.MODULE_PACK.get(s.module, "base") not in await _enabled(ctx):
        raise NotFound("Đối tượng này không thêm được trường riêng (hoặc gói của nó chưa bật)")
    options = list(dict.fromkeys(o.strip() for o in inp.options.replace("\n", ",").split(",") if o.strip()))
    if inp.kind == "choice" and len(options) < 2:
        raise Invalid("Kiểu Chọn một cần ít nhất hai lựa chọn")
    if any(len(o) > 80 for o in options) or len(options) > 50:
        raise Invalid("Tối đa 50 lựa chọn, mỗi lựa chọn tối đa 80 ký tự")
    have = await ctx.all("SELECT key, kind, active, label FROM core.field_defs WHERE subject = %s", (inp.subject,))
    old = next((r for r in have if r["key"] == inp.key), None) if inp.key else None
    if inp.key and old is None:
        raise NotFound("Không thấy trường này")
    if old is not None and old["kind"] != inp.kind:
        raise Invalid("Không đổi được kiểu của trường đã tạo — ngừng trường này và thêm trường mới")
    if old is None:
        if sum(1 for r in have if r["active"]) >= extra_fields.MAX_PER_SUBJECT:
            raise Invalid(f"Mỗi đối tượng tối đa {extra_fields.MAX_PER_SUBJECT} trường riêng đang dùng")
        key = code_from_name(inp.label, frozenset(r["key"].upper().replace("_", "-") for r in have)).lower().replace("-", "_")
        if not extra_fields.KEY.fullmatch(key):
            key = "f_" + key[:28]
        await ctx.exec("""INSERT INTO core.field_defs (subject, key, label, kind, options, help, active, position, updated_by)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, core.me())""",
                       (inp.subject, key, inp.label.strip(), inp.kind, Jsonb(options if inp.kind == "choice" else []), inp.help.strip(), inp.active, len(have)))
    else:
        key = inp.key
        await ctx.exec("""UPDATE core.field_defs SET label = %s, options = %s, help = %s, active = %s, updated_by = core.me(), updated_at = now()
                          WHERE subject = %s AND key = %s""",
                       (inp.label.strip(), Jsonb(options if inp.kind == "choice" else []), inp.help.strip(), inp.active, inp.subject, key))
    extra_fields.invalidate(ctx.p.tenant_id)
    changes = [] if old is None else [{"label": lbl, "before": was, "after": now} for lbl, was, now in
                                       (("Tên trường", old["label"], inp.label.strip()), ("Đang dùng", "Có" if old["active"] else "Không", "Có" if inp.active else "Không"))
                                       if was != now]
    return {"subject": inp.subject, "key": key, "summary": f"{s.label}: trường “{inp.label.strip()}” ({extra_fields.KINDS[inp.kind]})"
                                                              + ("" if inp.active else " — ngừng dùng"),
            "changes": changes}


# ── Múi giờ công ty (0169): lưu mốc là timestamptz; ngày / giờ nghiệp vụ và hiển thị theo múi của công ty — dữ liệu, mặc định của bản cài ──

@m.query("timezone_options", resource="core.admin", title="Múi giờ")
async def timezone_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [{"value": n, "label": n} for n in clock.choices()]


@m.query("timezone", resource="core.admin", title="Múi giờ công ty")
async def timezone_page(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Một dòng: múi giờ đang dùng + giờ hiện tại theo múi đó (nút Đổi điền sẵn)."""
    tz = await ctx.val("SELECT timezone FROM core.tenants")
    now = clock.now()
    off = f"{now:%z}"
    return ui.table([{"id": "timezone", "timezone": tz, "now": f"{now:%H:%M %d/%m/%Y} (UTC{off[:3]}:{off[3:]})",
                      "default": "Mặc định" if tz == clock.default_zone().key else "Công ty đặt"}],
                    [ui.col("timezone", "Múi giờ", width=240), ui.col("now", "Bây giờ theo múi này", width=220),
                     ui.col("default", "", "badge", tones={"Công ty đặt": "blue", "Mặc định": "gray"})],
                    title="Múi giờ công ty",
                    note="Ngày “hôm nay”, giờ làm việc, hạn xử lý, lịch, kỳ báo cáo và giờ hiện trên màn tính theo múi này. Dữ liệu đã lưu không đổi "
                         "(mốc thời gian lưu tuyệt đối); hạn theo LUẬT Việt Nam (thuế, lao động) vẫn theo giờ Việt Nam.")


class SetTimezone(Input):
    timezone: str = field("Múi giờ", options_query="admin.timezone_options", example="Asia/Singapore",
                          help="Tên IANA (Khu vực/Thành phố), vd Asia/Singapore, Europe/London")


@m.command("set_timezone",
           purpose="Đặt múi giờ của công ty để ngày, giờ, hạn và lịch tính đúng giờ địa phương.", reversible="Chạy lại lệnh với múi giờ cũ (dữ liệu đã lưu không bị sửa)",
           resource="core.admin", action="manage", title="Đổi múi giờ công ty", agent="deny", category="setup")
async def set_timezone(ctx: Ctx, inp: SetTimezone) -> dict[str, Any]:
    """Đổi múi giờ của CHÍNH công ty (RLS: một dòng core.tenants của phiên). Có hiệu lực từ lần đọc ghi kế tiếp; không sửa dữ liệu đã lưu (mốc tuyệt đối),
    chỉ đổi cách tính ngày / giờ từ nay. Trợ lý không đổi thay (đổi múi dời mọi hạn / lịch theo giờ đồng hồ — việc của người quản trị)."""
    try:
        z = clock.named(inp.timezone)
    except ValueError as e:
        raise Invalid(str(e)) from e
    if not await ctx.val("SELECT core.valid_tz(%s)", (z.key,)):
        raise Invalid(f"Múi giờ “{z.key}” chưa có trong cơ sở dữ liệu của bản cài — chọn múi khác hoặc nhờ nhà vận hành cập nhật dữ liệu múi giờ")
    old = await ctx.val("SELECT timezone FROM core.tenants")
    await ctx.exec("UPDATE core.tenants SET timezone = %s WHERE id = core.tenant()", (z.key,))
    return {"timezone": z.key, "previous": old, "summary": f"Múi giờ công ty: {z.key}" + (f" (trước: {old})" if old != z.key else " (không đổi)"),
            "changes": [{"label": "Múi giờ", "before": old, "after": z.key}] if old != z.key else []}


# ── Quốc gia công ty (0199): SĐT lưu E.164 ở mọi nơi; HIỂN THỊ theo nước của công ty (số cùng nước dạng nội địa, số nước khác dạng +…) ──

@m.query("country", resource="core.admin", title="Quốc gia công ty")
async def country_page(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Một dòng: quốc gia đang dùng + một SĐT mẫu hiện theo nước đó (nút Đổi điền sẵn)."""
    c = await ctx.val("SELECT country FROM core.tenants")
    rule = "Số trong nước hiện dạng nội địa (0912345678)" if c in NATIONAL else "Mọi số hiện dạng quốc tế (+…) — lõi chưa có luật nội địa của nước này"
    return ui.table([{"id": "country", "country": c, "rule": rule, "default": "Mặc định" if c == DEFAULT_COUNTRY else "Công ty đặt"}],
                    [ui.col("country", "Quốc gia", width=120), ui.col("rule", "Cách hiện SĐT", width=380),
                     ui.col("default", "", "badge", tones={"Công ty đặt": "blue", "Mặc định": "gray"})],
                    title="Quốc gia công ty",
                    note="SĐT luôn lưu dạng quốc tế E.164 (+84…) — là khoá gộp khách nên không đổi theo nước. Quốc gia chỉ đổi cách HIỆN số trên màn; "
                         "số không có “+” nhập vào vẫn đọc theo luật số Việt Nam, số nước ngoài ghi “+<mã nước>”.")


# Nước hay gặp làm trụ sở công ty (tên người đọc → mã ISO 3166-1); nước ngoài danh sách vẫn nhập được qua lệnh / Excel bằng mã hai chữ cái.
COUNTRIES = {"VN": "Việt Nam", "SG": "Singapore", "TH": "Thái Lan", "MY": "Malaysia", "ID": "Indonesia", "PH": "Philippines", "KH": "Campuchia",
             "LA": "Lào", "MM": "Myanmar", "CN": "Trung Quốc", "HK": "Hồng Kông", "TW": "Đài Loan", "JP": "Nhật Bản", "KR": "Hàn Quốc",
             "IN": "Ấn Độ", "AU": "Úc", "NZ": "New Zealand", "US": "Hoa Kỳ", "CA": "Canada", "GB": "Anh", "FR": "Pháp", "DE": "Đức",
             "NL": "Hà Lan", "CH": "Thuỵ Sĩ", "AE": "Các Tiểu vương quốc Ả Rập Thống nhất"}


@m.query("country_options", resource="core.admin", title="Quốc gia")
async def country_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    cur = await ctx.val("SELECT country FROM core.tenants")
    return [{"value": c, "label": n} for c, n in COUNTRIES.items()] + ([{"value": cur, "label": cur}] if cur and cur not in COUNTRIES else [])


class SetCountry(Input):
    country: str = field("Quốc gia", options_query="admin.country_options", example="VN", help="Chọn nước đặt trụ sở công ty")


@m.command("set_country",
           purpose="Đặt quốc gia của công ty để số điện thoại hiện đúng dạng nội địa.", reversible="Chạy lại lệnh với quốc gia cũ (số đã lưu không đổi)",
           resource="core.admin", action="manage", title="Đổi quốc gia công ty", agent="deny", category="setup")
async def set_country(ctx: Ctx, inp: SetCountry) -> dict[str, Any]:
    """Đổi quốc gia của CHÍNH công ty (RLS: một dòng core.tenants của phiên). Chỉ đổi cách hiện SĐT; số đã lưu (E.164) không đổi."""
    c = inp.country.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", c):
        raise Invalid(f"Mã quốc gia “{inp.country}” không đúng dạng ISO hai chữ cái (vd VN, SG)")
    old = await ctx.val("SELECT country FROM core.tenants")
    await ctx.exec("UPDATE core.tenants SET country = %s WHERE id = core.tenant()", (c,))
    return {"country": c, "previous": old, "summary": f"Quốc gia công ty: {c}" + (f" (trước: {old})" if old != c else " (không đổi)"),
            "changes": [{"label": "Quốc gia", "before": old, "after": c}] if old != c else []}


@m.query("pack_options", resource="core.admin", title="Gói nghiệp vụ")
async def pack_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Gói bật / tắt được, nhãn người đọc. Dựng lúc hỏi (không lúc nạp module): gói ngoài nạp SAU admin nên danh sách dựng ở lúc nạp bị rỗng.
    Gói riêng công ty: bật từ thẻ gói, không lộ tên ở danh sách chọn chung."""
    hidden = await packs.hidden(ctx.conn, ctx.p.tenant_id)
    return [{"value": k, "label": v.title} for k, v in sorted(registry.PACKS.items(), key=lambda kv: kv[1].title)
            if k != "base" and v.kind != "company" and k not in hidden]


class SetPack(Input):
    pack: str = field("Gói", options_query="admin.pack_options")
    enabled: bool = field("Bật", True)


@m.command("set_pack",
           purpose="Bật hoặc tắt một gói nghiệp vụ cho công ty cho khớp phần việc công ty đang làm.", reversible="Bật / tắt lại gói (dữ liệu giữ nguyên; tắt chỉ được khi không còn việc dở dang)",
           resource="core.admin", action="manage", title="Bật / tắt gói nghiệp vụ", agent="deny")
async def set_pack(ctx: Ctx, inp: SetPack) -> dict[str, Any]:
    """Bật: bật KÈM các gói nó dựa vào mà công ty chưa bật (bắc cầu, gói được cần trước), mỗi gói cài phần mặc định còn thiếu của riêng nó, cùng một
    lần ghi; gói nào công ty chưa có quyền dùng (đích hay gói kèm) ⇒ từ chối cả đợt, không bật gì (0157). Tắt: chỉ khi không gói nào đang bật cần nó
    VÀ gói không còn việc / tiền dở dang (hợp đồng đang chạy, tiền chưa phân bổ, phiếu chờ…); dữ liệu giữ nguyên, bật lại là thấy lại. Trợ lý không
    bật / tắt thay. Gói luôn bật (``Pack(always_on=True)``): không tắt được; "bật" chỉ cài phần mặc định còn thiếu."""
    if inp.pack not in registry.PACKS or inp.pack == "base" or inp.pack in await packs.hidden(ctx.conn, ctx.p.tenant_id):
        raise Invalid("Gói không hợp lệ (gói Nền luôn bật)")   # gói riêng của công ty khác: cùng câu với gói không có — không lộ là nó tồn tại
    if inp.pack in registry.always_on():   # luôn bật (0157): không có dòng tenant_packs để thêm / xoá — bật = chỉ cài phần mặc định còn thiếu
        label = registry.pack_title(inp.pack)
        if not inp.enabled:
            raise Invalid(f"Gói “{label}” luôn bật cho mọi công ty — không tắt được")
        installed = await install_defaults(ctx, InstallDefaults(packs=[inp.pack]))
        return {"pack": inp.pack, "enabled": True, "installed": installed, "menu": True,
                "summary": f"Gói “{label}” luôn bật" + (f" — {installed['summary']}" if installed["summary"] else "")}
    # khoá theo công ty TRƯỚC khi đọc: hai quản trị cùng lúc tắt "Bán hàng" và bật "Tài chính" ⇒ người sau thấy thay đổi của người trước
    # khoá ĐỘC QUYỀN trạng thái gói đã lấy ở cửa executor (packs.SWITCHES): chờ mọi lệnh ghi đang chạy xong, chặn lệnh mới tới khi đổi gói xong
    # ⇒ điều kiện chặn tắt (hợp đồng đang chạy…) kiểm trên dữ liệu không thể đổi giữa chừng
    on = await _enabled(ctx)   # đọc thẳng, không qua bộ nhớ đệm
    label = registry.pack_title(inp.pack)
    if inp.enabled:
        allowed = await packs.entitled(ctx.conn, ctx.p.tenant_id)
        if inp.pack not in allowed:
            raise Invalid(f"Công ty chưa có quyền dùng gói “{label}” — liên hệ nhà cung cấp để mở gói")
        order = [p for p in registry.pack_order(inp.pack) if p not in on]   # gói được cần trước, gói đích cuối; đã bật hết ⇒ rỗng (chỉ cài phần thiếu)
        if no_right := [registry.pack_title(p) for p in order if p not in allowed]:
            raise Invalid(f"Gói “{label}” cần bật kèm {', '.join(no_right)} nhưng công ty chưa có quyền dùng — liên hệ nhà cung cấp để mở gói. Chưa bật gói nào")
        done: dict[str, dict[str, Any]] = {}
        for p in order or [inp.pack]:
            await ctx.exec("INSERT INTO core.tenant_packs (pack, enabled_by) VALUES (%s, core.me()) ON CONFLICT DO NOTHING", (p,))
            # mỗi gói mang theo cấu hình mặc định (vai, quyền, đội, chuỗi duyệt, mẫu quy trình) — chỉ thêm phần còn thiếu (0059)
            done[p] = await install_defaults(ctx, InstallDefaults(packs=[p]))
        ctx.after_commit(_forget(ctx.p.tenant_id))
        also = [p for p in done if p != inp.pack]
        extra = "; ".join(f"{registry.pack_title(p)}: {done[p]['summary']}" for p in also if done[p]["summary"])
        installed = done[inp.pack]
        return {"pack": inp.pack, "enabled": True, "installed": installed, "also_enabled": also, "menu": True,
                "summary": f"Bật gói “{label}”" + (f" (bật kèm: {', '.join(registry.pack_title(p) for p in also)})" if also else "")
                + (f" — {installed['summary']}" if installed["summary"] else "") + (f". Gói kèm — {extra}" if extra else "")}
    else:
        if users := [p.title for k, p in registry.PACKS.items() if inp.pack in p.requires and k in on]:
            raise Invalid(f"Không tắt được “{label}”: gói đang bật cần nó — {', '.join(users)}")
        if blockers := await _blockers(ctx, inp.pack):
            raise Invalid(f"Không tắt được “{label}” — còn việc / tiền chưa xử lý: {'; '.join(blockers)}")
        await ctx.exec("DELETE FROM core.tenant_packs WHERE pack = %s", (inp.pack,))
    ctx.after_commit(_forget(ctx.p.tenant_id))
    # ``menu``: phân hệ / màn của gói vừa hiện ra hoặc ẩn đi ⇒ app tải lại thanh bên ngay (không bắt người dùng tải lại trang)
    return {"pack": inp.pack, "enabled": inp.enabled, "menu": True, "summary": f"{'Bật' if inp.enabled else 'Tắt'} gói “{label}”"}


class InstallDefaults(Input):
    packs: list[str]


async def _blockers(ctx: Ctx, pack: str) -> list[str]:
    """Điều kiện chặn tắt gói (như khoá ngoại RESTRICT): gom ``<module>.pack_blockers`` của mọi module thuộc gói — còn hợp đồng đang chạy, tiền
    chưa xử lý, phiếu đang chờ… Module tự khai (``Module.pack_blockers``), lõi không biết ngành."""
    out: list[str] = []
    # lõi: phản ứng của gói với sự kiện ĐÃ xảy ra mà chưa chạy (vd lập hoá đơn phí sau khi ký) — tắt lúc này là mất nghiệp vụ
    mine = [name for name, sub in registry.SUBSCRIBERS.items() if sub.pack == pack or pack in sub.needs]
    mine += [old for old, new in registry.REPLACED.items() if new in mine]  # lượt cũ còn chờ cũng thuộc gói xử lý mới
    # "dead" (hết lượt thử) = CHƯA xử lý, không phải xong ⇒ cũng chặn; chỉ hết chặn khi chạy lại thành công hoặc quản trị bỏ qua có lý do
    if mine and (r := await ctx.one("""SELECT count(*) AS n, count(*) FILTER (WHERE status = 'dead' OR attempts > 0) AS failing FROM core.deliveries
                                         WHERE status IN ('pending', 'dead') AND subscriber = ANY(%s)""", (mine,))) and r["n"]:
        out.append(f"{r['n']} phản ứng của gói với sự kiện đã xảy ra chưa chạy xong"
                   + (f" ({r['failing']} đang lỗi / hết lượt thử — Quản trị › Sự kiện lỗi: chạy lại, hoặc bỏ qua có lý do)" if r["failing"]
                      else " (đợi vài giây rồi thử lại)"))
    for mod, pk in registry.MODULE_PACK.items():
        if pk == pack and registry.get(f"{mod}.pack_blockers") is not None:
            # quyền hệ thống KHÔNG mang ảnh chụp gói của phiên ⇒ gói kiểm theo DB (phiên nạp trước khi bật gói vẫn không bỏ sót điều kiện)
            # gói nền gọi bằng quyền hệ thống lõi dựng (``trusted`` — chỉ lõi / gói nền, 0130): điều kiện tắt gói là hợp đồng của lõi với mọi gói
            out += await executor.invoke(ctx, f"{mod}.pack_blockers", {}, as_principal=system_principal(ctx.p.tenant_id, ctx.p.user_id),
                                         allow_internal=True, trusted=True)
    return out


async def _enabled(ctx: Ctx) -> set[str]:
    return {*registry.always_on(), *(r["pack"] for r in await ctx.all("SELECT pack FROM core.tenant_packs"))}


async def _plan(ctx: Ctx, packs_: list[str], enabled: set[str]) -> list[Any]:
    from barista import presets

    new = registry.pack_closure(set(packs_)) | ({"base"} if "base" in packs_ else set())
    return presets.plan(new & enabled, enabled, {r["key"] for r in await ctx.all("SELECT key FROM core.roles")})


async def _flow_exists(ctx: Ctx, key: str) -> bool:
    try:
        await ctx.call("flow.template", key=key)
        return True
    except (NotFound, Denied):
        return False


@m.command("install_defaults", resource="core.admin", action="manage", title="Cài cấu hình mặc định của gói", internal=True, agent="deny")
async def install_defaults(ctx: Ctx, inp: InstallDefaults) -> dict[str, Any]:
    """Cài phần mặc định đi kèm các gói (``barista/presets.py``) theo NHẬT KÝ cài (``core.pack_installs``): mục chưa từng cài ⇒ thêm (công ty đã có
    sẵn cùng khoá ⇒ giữ của công ty) và ghi nhật ký; mục ĐÃ từng cài ⇒ bỏ qua dù công ty đã xoá / sửa — bật lại gói không khôi phục quyết định của
    công ty. Mẫu quy trình không cài được (vd công ty đổi tên đội) ⇒ bỏ qua, nói lý do, lần sau thử lại."""
    from barista import presets

    # mỗi công ty có ít nhất MỘT pháp nhân mặc định (0099) — tên công ty; công ty nhiều pháp nhân thêm ở Quản trị › Pháp nhân
    await ctx.exec("""INSERT INTO core.legal_entities (code, name, is_default) SELECT 'CHINH', name, true FROM core.tenants WHERE id = core.tenant()
                      AND NOT EXISTS (SELECT 1 FROM core.legal_entities WHERE is_default)""")
    # cài phần nền ("base") kéo theo mặc định của gói luôn bật (0157): công ty mới và công ty có trước (seed --upgrade) đều nhận
    wanted = [*inp.packs, *sorted(registry.always_on() - set(inp.packs))] if "base" in inp.packs else inp.packs
    n, skipped, held, _ = await _install_items(ctx, await _plan(ctx, wanted, await _enabled(ctx)))
    # chủ tài khoản (0029): công ty chưa có ai đang hoạt động ⇒ gán theo quy tắc cố định — công ty mới lẫn công ty có trước (seed --upgrade)
    owners = await _first_owner(ctx)
    # vai nhập môn (0029): công ty chưa có vai nào được đánh dấu (công ty có trước 043, hoặc vai đã có trước khi gói cài) ⇒ đánh dấu theo gói
    await ctx.exec("UPDATE core.roles SET entry = true WHERE key = ANY(%s) AND NOT EXISTS (SELECT 1 FROM core.roles WHERE entry)",
                   (presets.entry_roles(),))
    return {**{k + "s": v for k, v in n.items()}, "owners": [str(u) for u in owners], "skipped": skipped, "held": held,
            "summary": _install_summary(n, skipped, held, [f"{len(owners)} chủ tài khoản"] if owners else [])}


INSTALL_LABELS = {"role": "vai", "team": "đội", "grant": "quyền", "chain": "chuỗi duyệt", "rule": "luật duyệt", "flow": "mẫu quy trình", "vocab": "từ vựng",
                  "setting": "cài đặt", "record": "dòng cấu hình"}


def _install_summary(n: dict[str, int], skipped: list[str], held: list[str], extra: list[str]) -> str:
    summary = ", ".join([*(f"{v} {INSTALL_LABELS[k]}" for k, v in n.items() if v), *extra])
    return (("cài thêm " + summary if summary else "") + ("; chưa cài " + "; ".join(skipped) if skipped else "")
            + (f"; {len(held)} quyền mới mở rộng thẩm quyền tiền / quyền CHƯA cài — chủ tài khoản cấp nếu muốn (Quản trị › Vai trò & quyền): "
               + "; ".join(held) if held else ""))


async def _install_items(ctx: Ctx, items: list[Any]) -> tuple[dict[str, int], list[str], list[str], list[str]]:
    """Vòng cài DUY NHẤT của phần mặc định (``install_defaults`` — nút Cài phần còn thiếu — và lệnh vận hành ``install-defaults`` cài có chọn lọc dùng
    chung): mục chưa có nhật ký ⇒ thêm (chỉ thêm, giữ của công ty), ghi nhật ký; quyền mở rộng thẩm quyền của gói đã cài ⇒ giữ lại chờ chủ tài khoản.
    Trả (số đã thêm theo loại, mục bỏ qua kèm lý do, quyền bị giữ, "loại khoá" của mục đã thêm)."""
    from barista import presets

    installs = await ctx.all("SELECT kind, key, pack FROM core.pack_installs")
    logged = {(r["kind"], r["key"]) for r in installs}
    # gói ĐÃ cài ở công ty này từ trước (nâng cấp / chạy lại): quyền MỚI mở rộng thẩm quyền tiền / quyền KHÔNG cài lặng lẽ — để chủ tài khoản cấp (0059)
    known = {r["pack"] for r in installs}
    n: dict[str, int] = dict.fromkeys(presets.KIND_ORDER, 0)
    skipped: list[str] = []
    held: list[str] = []
    done: list[str] = []
    root = await ctx.val("SELECT id FROM core.teams WHERE parent_id IS NULL ORDER BY created_at LIMIT 1")
    for it in items:
        if (it.kind, it.key) in logged:
            continue
        d = it.data
        if it.kind == "role":
            added = await ctx.exec("INSERT INTO core.roles (key, label, entry) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                                   (it.key, d["label"], bool(d.get("entry"))))
        elif it.kind == "team":
            added = await ctx.exec("INSERT INTO core.teams (code, name, kind, parent_id) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                                   (it.key, d["name"], d["kind"], root))
        elif it.kind == "grant" and it.pack in known and presets.widens_authority(d["resource"], d["actions"]):
            held.append(f"{d['role']} → {d['resource']} ({'/'.join(d['actions'])})")
            continue                               # không ghi nhật ký ⇒ còn hiện ở đây tới khi chủ tài khoản quyết (cấp tay hoặc bỏ qua)
        elif it.kind == "grant":
            added = await ctx.exec("INSERT INTO core.grants (role, resource, scope, actions) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                                   (d["role"], d["resource"], d["scope"], d["actions"]))
        elif it.kind == "chain":
            added = await ctx.exec("INSERT INTO core.approval_chains (key, label, steps) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                                   (it.key, d["label"], Jsonb(d["steps"])))
        elif it.kind == "rule":
            added = await ctx.exec("INSERT INTO core.approval_rules (command, chain) VALUES (%s, %s) ON CONFLICT DO NOTHING", (it.key, d["chain"]))
        elif it.kind == "vocab":   # nhãn mặc định của ngành (0124): công ty đã đặt nhãn cho khái niệm đó thì giữ của công ty
            added = await ctx.exec("INSERT INTO core.vocabulary (term, label, updated_by) VALUES (%s, %s, core.me()) ON CONFLICT DO NOTHING", (it.key, d["label"]))
            ctx.after_commit(_forget_vocab(ctx.p.tenant_id))
        elif it.kind == "setting":   # cài đặt công ty (0128): công ty đã đặt thì giữ của công ty
            added = await ctx.exec("INSERT INTO core.settings (key, enabled, updated_by) VALUES (%s, %s, core.me()) ON CONFLICT DO NOTHING",
                                   (it.key, d["enabled"]))
        elif it.kind == "record":   # cấu hình mặc định qua lệnh của module sở hữu (0153) — lệnh tự giữ dòng công ty đã có
            try:
                # lệnh cài mặc định là hợp đồng nội bộ khai ``callers=("admin", …)`` (vd hạng trợ lý AI — chính sách của nhà vận hành, 0193) ⇒ chạy bằng
                # quyền hệ thống: dữ liệu do gói khai, quản trị công ty bật gói vẫn nhận mặc định mà không cần (và không có) quyền sửa chính sách đó
                rec = registry.get(d["command"])
                async with ctx.conn.transaction():
                    got = await (ctx.call_system(d["command"], **d["input"]) if rec is not None and rec.internal and "admin" in rec.callers
                                 else ctx.call(d["command"], **d["input"]))
            except (Invalid, Denied, NotFound) as e:
                skipped.append(f"{it.key}: {e}")
                continue                           # không ghi nhật ký ⇒ lần bật / cài sau thử lại
            added = 1 if not isinstance(got, dict) or got.get("added", True) else 0
        else:
            added = 0
            if not await _flow_exists(ctx, it.key):
                try:
                    async with ctx.conn.transaction():
                        await ctx.call("flow.set_template", key=it.key, **d)
                    added = 1
                except (Invalid, Denied, NotFound) as e:
                    skipped.append(f"mẫu “{d['name']}”: {e}")
                    continue                       # không ghi nhật ký ⇒ lần bật / cài sau thử lại
        n[it.kind] += added
        if added:
            done.append(f"{it.kind} {it.key}")
        await ctx.exec("INSERT INTO core.pack_installs (kind, key, pack, version) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                       (it.kind, it.key, it.pack, it.version if added else None))
    if held:   # quyền mới chưa cài vì mở rộng thẩm quyền ⇒ CHỦ TÀI KHOẢN thấy rõ việc phải quyết (một thông báo / bộ quyền), không lặng im
        keep = [r["user_id"] for r in await ctx.all("SELECT user_id FROM core.memberships WHERE owner AND active")]
        await ctx.notify(keep, kind="system", title=f"{len(held)} quyền mới chờ chủ tài khoản cấp",
                         body="Bản cập nhật có quyền mới mở rộng thẩm quyền tiền / quyền nên KHÔNG tự cài: " + "; ".join(held)
                              + ". Chưa cấp thì việc cần quyền đó chưa ai làm được — cấp ở Quản trị › Vai trò & quyền.",
                         push="Có quyền mới chờ chủ tài khoản cấp", open={"query": "admin.permissions", "input": {}},
                         key="held_grants:" + ",".join(sorted(held)))
    return n, skipped, held, done


async def _defaults_current(ctx: Ctx, items: list[Any]) -> Any:
    """Ảnh chụp cấu hình công ty cho ``presets.drift`` (chỉ đọc). Mẫu quy trình nằm ở module quy trình ⇒ hỏi qua ``flow.template`` (chỉ mẫu chưa có nhật ký)."""
    from barista import presets

    installs = await ctx.all("SELECT kind, key, pack FROM core.pack_installs")
    logged = {(r["kind"], r["key"]) for r in installs}
    flows = {i.key for i in items if i.kind == "flow" and (i.kind, i.key) not in logged and await _flow_exists(ctx, i.key)}
    return presets.Current(
        logged=logged, known_packs={r["pack"] for r in installs},
        roles={r["key"] for r in await ctx.all("SELECT key FROM core.roles")},
        teams={r["code"] for r in await ctx.all("SELECT code FROM core.teams")},
        grants={(r["role"], r["resource"]): (r["scope"], set(r["actions"]))
                for r in await ctx.all("SELECT role, resource, scope, actions FROM core.grants")},
        chains={r["key"]: r["steps"] for r in await ctx.all("SELECT key, steps FROM core.approval_chains")},
        rules={r["command"]: r["chain"] for r in await ctx.all("SELECT command, chain FROM core.approval_rules")},
        settings={r["key"]: r["enabled"] for r in await ctx.all("SELECT key, enabled FROM core.settings")},
        vocab={r["term"] for r in await ctx.all("SELECT term FROM core.vocabulary")}, flows=flows)


DRIFT_STATUS = {"missing": "Chưa cài", "held": "Chờ chủ tài khoản cấp", "changed": "Công ty đổi khác"}


@m.query("defaults_drift", resource="core.admin", title="Phần mặc định chưa cài")
async def defaults_drift(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """CHỈ ĐỌC: phần mặc định của các gói đang bật (vai, quyền, đội, chuỗi / luật duyệt, cài đặt, mẫu quy trình…) đối chiếu với cấu hình công ty —
    "chưa cài" (nút Cài thêm an toàn — ``admin.install_defaults`` chỉ thêm phần thiếu), "chờ chủ tài khoản" (quyền mở rộng thẩm quyền tiền / quyền: không tự
    cài), "công ty đổi khác" (đã gỡ / hẹp hơn / khác mặc định — quyết định của công ty, chỉ báo). Bước kiểm của phát hành (``barista.defaults_report``)
    đọc đúng truy vấn này."""
    from barista import presets

    on = await _enabled(ctx)
    items = await _plan(ctx, ["base", *sorted(on - {"base"})], on)
    gaps = presets.drift(items, await _defaults_current(ctx, items))
    if not ctx.p.system:   # dòng cấu hình của nhà vận hành (hạng trợ lý AI — 0193) không hiện cho khách
        gaps = [g for g in gaps if not (g.kind == "record" and registry.operator_command(g.data.get("command", "")))]
    order = {"held": 0, "missing": 1, "changed": 2}
    rows = [{"id": f"{g.kind}|{g.key}", "kind": g.kind, "key": g.key, "item": _default_line(g)["title"], "type": ITEM_LABEL[g.kind],
             "pack": registry.pack_title(g.pack), "pack_key": g.pack, "status": g.status, "detail": g.detail, "important": g.important}
            for g in sorted(gaps, key=lambda g: (order[g.status], presets.KIND_ORDER.index(g.kind), g.pack, g.key))]
    n = {k: sum(1 for r in rows if r["status"] == k) for k in order}
    actions = [ui.act("admin.install_missing_defaults", "Cài phần còn thiếu", style="primary")] if n["missing"] else []
    head = (ui.b_callout("warn", f"{n['missing']} mục mặc định chưa cài", "Thường do bản cập nhật thêm vai / quyền / chuỗi duyệt mới sau khi công ty đã bật "
                         "gói. Cài thêm chỉ THÊM phần thiếu — không sửa chỗ công ty đã đổi.", actions=actions) if n["missing"]
            else ui.b_callout("ok", "Mặc định của các gói đang bật đã cài đủ", ""))
    return ui.table(rows, [
        ui.col("item", "Mục", width=360), ui.col("type", "Loại"), ui.col("pack", "Gói"),
        ui.col("status", "Tình trạng", "badge", labels=DRIFT_STATUS, tones={"missing": "orange", "held": "red", "changed": "gray"}),
        ui.col("detail", "Chi tiết", width=320),
    ], title="Phần mặc định chưa cài", header=[head], empty="Không có khác biệt — cấu hình công ty khớp mặc định của các gói đang bật",
        kpis=[ui.kpi("Chưa cài", n["missing"], tone="orange" if n["missing"] else "green"), ui.kpi("Chờ chủ tài khoản", n["held"], tone="red" if n["held"] else None),
              ui.kpi("Công ty đổi khác", n["changed"])],
        note="“Chờ chủ tài khoản”: quyền mới mở rộng thẩm quyền tiền / quyền — không tự cài, cấp ở Vai trò & quyền. “Công ty đổi khác”: chỉ để biết, "
             "nút cài không ghi đè.")


@m.command("install_missing_defaults",
           purpose="Cài phần mặc định còn thiếu (vai, quyền, đội, chuỗi duyệt, mẫu quy trình) của các gói đang bật.", irreversible=True, effect="Thêm mọi phần mặc định còn thiếu của các gói đang bật; chỗ công ty đã đổi giữ nguyên. Phần đã thêm muốn bỏ phải gỡ từng mục.",
           resource="core.admin", action="manage", title="Cài phần mặc định còn thiếu", agent="deny")
async def install_missing_defaults(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Người bấm (trợ lý không làm thay): ``admin.install_defaults`` cho MỌI gói đang bật — chỉ thêm mục chưa từng cài; quyền mở rộng thẩm quyền của gói
    đã cài vẫn chờ chủ tài khoản; chỗ công ty đã đổi giữ nguyên."""
    on = await _enabled(ctx)
    out = await install_defaults(ctx, InstallDefaults(packs=["base", *sorted(on - {"base"})]))
    ctx.after_commit(_forget(ctx.p.tenant_id))
    return {**out, "summary": out["summary"] or "Mặc định của các gói đang bật đã cài đủ — không thêm gì"}


class OperatorInstallDefaults(Input):
    kinds: list[str] = field("Loại", [], help="role / team / grant / chain / rule / flow / vocab / setting / record — trống = không lọc theo loại")
    only: list[str] = field("Chỉ các mục", [], help="khoá như báo cáo phần mặc định in ra (vd <lệnh>:<khoá> của dòng cấu hình, "
                                                   "<vai>|<tài nguyên> của quyền) hoặc “loại khoá”")
    everything: bool = field("Cài mọi mục còn thiếu", False, help="như nút Cài phần còn thiếu — phải nói rõ, không mặc định")
    by: str = operator_door.by_field()
    why: str = operator_door.why_field()


@m.command("operator_install_defaults", resource=operator_door.RESOURCE, action="manage", title="Cài phần mặc định chưa cài (nhà vận hành)", internal=True,
           agent="deny", category="setup")
async def operator_install_defaults(ctx: Ctx, inp: OperatorInstallDefaults) -> dict[str, Any]:
    """Cài CÓ CHỌN LỌC phần mặc định chưa cài (cùng danh sách màn Quản trị › Phần mặc định chưa cài / ``barista.defaults_report``): chỉ mục thuộc loại
    ``kinds`` và / hoặc khoá ``only`` — không cấp gì ngoài phần được chọn; cùng vòng cài với nút Cài phần còn thiếu (``_install_items``: chỉ thêm, giữ
    chỗ công ty đã đổi, quyền mở rộng thẩm quyền của gói đã cài vẫn chờ chủ tài khoản — nhà vận hành cấp những quyền đó bằng ``set-grant``). Không
    chạy phần đi kèm của ``install_defaults`` (pháp nhân, chủ tài khoản, vai nhập môn). Khoá ``only`` không có trong mặc định của các gói đang bật ⇒
    từ chối (gõ sai không được lặng im); khoá đã cài / công ty đã đổi ⇒ báo, không đụng."""
    from barista import presets

    operator_door.require_who(inp)
    if bad := sorted(set(inp.kinds) - set(presets.KIND_ORDER)):
        raise Invalid(f"Loại lạ: {', '.join(bad)} — chọn trong {', '.join(presets.KIND_ORDER)}")
    if not (inp.kinds or inp.only or inp.everything):
        raise Invalid("Chọn phần cần cài: --kind và / hoặc --only (cài hết mọi mục còn thiếu: --all)")
    on = await _enabled(ctx)
    items = await _plan(ctx, ["base", *sorted(on - {"base"})], on)
    def names(it: Any) -> set[str]:
        return {it.key, f"{it.kind}|{it.key}", f"{it.kind} {it.key}"}

    wanted = {x.strip() for x in inp.only if x.strip()}
    if unknown := sorted(wanted - {x for it in items for x in names(it)}):
        raise Invalid("Không có trong phần mặc định của các gói đang bật: " + "; ".join(unknown))
    open_ = {(g.kind, g.key) for g in presets.drift(items, await _defaults_current(ctx, items)) if g.status in ("missing", "held")}
    picked = [it for it in items if (not inp.kinds or it.kind in inp.kinds) and (not wanted or names(it) & wanted)]
    todo = [it for it in picked if (it.kind, it.key) in open_]
    not_open = [f"{it.kind} {it.key}" for it in picked if (it.kind, it.key) not in open_ and wanted and names(it) & wanted]
    n, skipped, held, done = await _install_items(ctx, todo)
    ctx.after_commit(_forget(ctx.p.tenant_id))
    summary = _install_summary(n, skipped, held, []) or "Không có mục nào được chọn còn thiếu — không thêm gì"
    return {"installed": done, "skipped": skipped, "held": held, "not_missing": not_open,
            "summary": summary + (f"; đã cài / công ty đã đổi (không đụng): {'; '.join(not_open)}" if not_open else "")}


@m.command("ensure_owner", resource="core.admin", action="manage", title="Gán chủ tài khoản đầu tiên", internal=True, agent="deny")
async def ensure_owner(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Công ty chưa có chủ tài khoản đang hoạt động ⇒ gán theo quy tắc của ``_first_owner`` (0029). Cho đường cấp thành viên ngoài executor
    (công ty khách do công ty dịch vụ tạo: thành viên tới SAU lần cài mặc định — ``kernel/tenancy.sync_members``)."""
    return {"owners": [str(u) for u in await _first_owner(ctx)]}


# ── Chợ gói (0059): thẻ từng gói → xem gói gồm gì, bật sẽ thêm gì, rồi bật. App chỉ vẽ thẻ do server mô tả. ──

KIND_LABEL = {"core": "Lõi", "capability": "Năng lực dùng chung", "industry": "Nghiệp vụ ngành", "company": "Riêng công ty"}
ITEM_LABEL = {"role": "Vai", "team": "Đội", "grant": "Quyền", "chain": "Chuỗi duyệt", "rule": "Luật duyệt", "flow": "Mẫu quy trình", "vocab": "Từ vựng",
              "setting": "Cài đặt", "record": "Cấu hình"}


def _pack_screens(pack: str) -> list[str]:
    """Màn của gói = màn có truy vấn thuộc module của gói (đọc menu do server mô tả — không khai lại ở đây)."""
    from barista.workspaces import workspaces

    return [f"{w.title} › {sc.title}" for w in workspaces() for sc in w.screens
            if registry.MODULE_PACK.get(sc.query.split(".")[0]) == pack and registry.OPS[sc.query].kind == "query"
            and not registry.operator_command(sc.query)]   # màn của nhà vận hành (0193) không giới thiệu cho khách


def _default_line(i: Any) -> dict[str, Any]:
    """Một mục mặc định (``presets.Item`` / ``presets.Gap``) thành dòng đọc được: tên + loại."""
    from barista import presets

    d = i.data
    what = {"role": d.get("label"), "team": f"@{i.key} {d.get('name', '')}", "grant": f"{presets.role_label(d.get('role', ''))} — "
            f"{registry.RESOURCES.get(d.get('resource', ''), d.get('resource'))} ({d.get('scope')}: {' '.join(d.get('actions', []))})",
            "chain": d.get("label"), "rule": f"{i.key} qua chuỗi {d.get('chain')}", "flow": d.get("name"),
            "vocab": f"{vocab.TERMS[i.key].meaning[:1].upper()}{vocab.TERMS[i.key].meaning[1:]} gọi là “{d.get('label')}”" if i.key in vocab.TERMS else "",
            "setting": f"{_setting_label(i.key)}: {'bật' if d.get('enabled') else 'tắt'}",
            "record": (f"Công cụ trợ lý của vai {presets.role_label(d['input']['role'])}: " + ", ".join(d["input"]["tools"])
                       if d.get("command") == presets.ROLE_TOOLS else str((d.get("input") or {}).get("label") or i.key))}[i.kind]
    return {"title": str(what), "badge": ITEM_LABEL[i.kind]}


@m.query("pack_store", resource="core.admin", title="Gói & ứng dụng")
async def pack_store(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Mỗi gói một thẻ: đang bật / có sẵn / cần bật gói khác trước. Bấm thẻ ⇒ xem gói gồm gì và bật sẽ thêm gì (``admin.pack_info``)."""
    on = await _enabled(ctx)
    tiles: dict[str, list[dict[str, Any]]] = {"core": [], "capability": [], "industry": [], "company": []}
    allowed_ = await packs.entitled(ctx.conn, ctx.p.tenant_id)
    hidden = await packs.hidden(ctx.conn, ctx.p.tenant_id)   # gói riêng của công ty khác không lên chợ (ADR 0233 D)
    for key, pk in registry.PACKS.items():
        if key in hidden:
            continue
        label, desc = pk.title, pk.description
        # gói cần mà công ty chưa bật thì bật KÈM (0157) — chỉ khi công ty chưa có quyền dùng gói cần đó thì mới "Cần …"
        no_right = [registry.pack_title(r) for r in registry.pack_order(key)[:-1] if r not in on and r not in allowed_]
        badge, tone = (("Luôn bật", "muted") if key in registry.always_on() else ("Đang bật", "ok") if key in on
                       else ("Liên hệ để mở", "muted") if key not in allowed_      # gói trả tiền: nhà cung cấp cấp quyền
                       else (f"Cần {', '.join(no_right)}", "muted") if no_right else ("Có sẵn", "accent"))
        tiles[pk.kind].append(
            {"title": label, "subtitle": desc, "badge": badge, "tone": tone, "module": registry.pack_module(key),   # biểu tượng + màu thẻ = của module khai gói (ModuleStyle)
             "open": {"query": "admin.pack_info", "input": {"pack": key}}})
    titles = {"company": "Riêng của công ty bạn", "industry": "Nghiệp vụ theo ngành", "capability": "Năng lực dùng chung — ngành nào cũng dùng", "core": "Lõi"}
    # gói bán được nhưng CHƯA có mã trên bản cài (0163): chỉ thẻ chữ — không thao tác, không màn; bấm ⇒ trang mô tả (``admin.catalog_pack``)
    absent = [{"title": c.title, "subtitle": c.description, "badge": "Chưa có trên bản cài · Liên hệ", "tone": "warn",
               "open": {"query": "admin.catalog_pack", "input": {"pack": c.key}}} for c in pack_catalog.not_installed(set(registry.PACKS))]
    return ui.card("Gói & ứng dụng", [
        ui.b_callout("muted", "Bấm một gói để xem gồm gì và bật sẽ thêm gì",
                     "Lõi + gói là phần mềm chung; cấu hình là của công ty bạn. Bật gói ⇒ bật kèm gói nó cần và cài vai, quyền, đội, quy trình mặc định (chỉ thêm phần chưa có). "
                     "Tắt gói chỉ được khi không còn việc / tiền dở dang; dữ liệu giữ nguyên."),
        *(ui.b_tiles(tiles[k], titles[k]) for k in ("company", "industry", "capability", "core") if tiles[k]),
        *([ui.b_tiles(absent, "Gói khác của nhà cung cấp — chưa có trên bản cài này")] if absent else []),
        *([ui.b_tiles([{"title": "Ứng dụng đã kết nối", "subtitle": "Ứng dụng ngoài nối vào công ty bằng khoá riêng: quyền, webhook, thu hồi",
                        "badge": "Kết nối", "tone": "accent", "module": "connect", "open": {"query": "connect.apps", "input": {}}}],
                      "Ứng dụng kết nối từ xa")] if "connect.apps" in registry.OPS and executor.allowed(ctx.p, registry.OPS["connect.apps"]) else []),
        *(_usage_block(await _usage(ctx, 2)) if ctx.p.can("core.audit", "read") and ctx.p.has_scope("core.audit", "all") else [])],
        icon="square.stack.3d.up")


# ── Mức dùng theo gói (0163): người hoạt động + số lệnh + số lượt đọc, theo tháng — CHỈ số đếm, không nội dung ──

USAGE_MONTHS = 12   # trần số tháng trả về


def _months_back(n: int) -> list[date]:
    """``n`` tháng gần nhất (tháng hiện tại ở cuối), mỗi tháng là ngày mùng 1 (giờ công ty)."""
    t = clock.today()
    out = []
    for i in range(n - 1, -1, -1):
        idx = t.year * 12 + (t.month - 1) - i
        out.append(date(idx // 12, idx % 12 + 1, 1))
    return out


async def _usage(ctx: Ctx, months: int) -> dict[str, Any]:
    """Lệnh (``core.audit_log``, status ok, người thật) + lượt đọc (``core.usage_reads``) gộp theo (tháng, gói, kênh). Gói = gói của module trong tên lệnh
    (``registry.MODULE_PACK``); module không thuộc gói nào ⇒ nhóm "Lõi" (khoá ``""``). Kênh ``human`` = người bấm; ``agent`` = trợ lý làm thay (số riêng —
    không cộng vào người)."""
    months = max(1, min(months, USAGE_MONTHS))
    span = _months_back(months)
    mods, packs_ = list(registry.MODULE_PACK), list(registry.MODULE_PACK.values())
    rows = await ctx.all(
        """WITH mp AS (SELECT * FROM unnest(%(mods)s::text[], %(packs)s::text[]) AS t(module, pack)),
                ev AS (SELECT (date_trunc('month', a.at AT TIME ZONE core.tz()))::date AS month, coalesce(mp.pack, '') AS pack,
                              a.user_id, a.via, 1 AS commands, 0 AS reads
                       FROM core.audit_log a LEFT JOIN mp ON mp.module = split_part(a.command, '.', 1)
                       WHERE a.at >= (%(first)s::timestamp AT TIME ZONE core.tz()) AND a.status = 'ok' AND NOT a.system
                         AND a.user_id IS NOT NULL AND a.via IN ('human', 'agent')
                       UNION ALL
                       SELECT date_trunc('month', r.day)::date, r.pack, r.user_id, r.via, 0, r.queries
                       FROM core.usage_reads r WHERE r.day >= %(first)s)
           SELECT month, pack, via, count(DISTINCT user_id)::int AS users, sum(commands)::int AS commands, sum(reads)::int AS reads
           FROM ev GROUP BY month, pack, via""", {"mods": mods, "packs": packs_, "first": span[0]})
    table: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        key = r["pack"] if r["pack"] in registry.PACKS else ""   # gói đã gỡ / module lạ ⇒ Lõi
        cell = table.setdefault(key, {}).setdefault(r["month"].isoformat()[:7], {"humans": 0, "commands": 0, "reads": 0, "assistant_users": 0,
                                                                                    "assistant_commands": 0, "assistant_reads": 0})
        if r["via"] == "human":
            cell["humans"] += r["users"]
            cell["commands"] += r["commands"]
            cell["reads"] += r["reads"]
        else:
            cell["assistant_users"] += r["users"]
            cell["assistant_commands"] += r["commands"]
            cell["assistant_reads"] += r["reads"]
    labels = [d.isoformat()[:7] for d in span]
    out = []
    for key in [*registry.PACKS, ""]:
        if key not in table:
            continue
        out.append({"pack": key or "core", "title": registry.pack_title(key) if key else "Lõi", "kind": registry.pack_kind(key) if key else "core",
                    "months": [{"month": mth, **table[key].get(mth, {"humans": 0, "commands": 0, "reads": 0, "assistant_users": 0,
                                                                       "assistant_commands": 0, "assistant_reads": 0})} for mth in labels]})
    return {"months": labels, "packs": out}


class UsageRange(Input):
    months: int = field("Số tháng", 6, ge=1, le=USAGE_MONTHS)


@m.query("pack_usage", resource="core.audit", min_scope="all", title="Mức dùng theo gói",
         description="Mỗi gói và mỗi tháng: số người hoạt động, số lệnh đã chạy, số lượt xem — tách người bấm và trợ lý. Chỉ số đếm của cả công ty, không nội dung.")
async def pack_usage(ctx: Ctx, inp: UsageRange) -> dict[str, Any]:
    """Quản trị xem gói nào đang được dùng thật. ``humans`` / ``commands`` / ``reads`` = người bấm; ``assistant_*`` = trợ lý làm thay (không cộng vào
    người). Người hoạt động = người có ít nhất một lệnh thành công HOẶC một lượt xem trong tháng ở gói đó. Cần quyền xem nhật ký cả công ty (RLS nhật ký theo quyền đó)."""
    return await _usage(ctx, inp.months)


def _usage_block(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Mục "Mức dùng" của Store: mỗi gói một dòng — tháng gần nhất + tháng trước; chữ do server dựng (app chỉ vẽ)."""
    cur, prev = (data["months"][-1], data["months"][-2] if len(data["months"]) > 1 else None)
    items = []
    for p in data["packs"]:
        by = {c["month"]: c for c in p["months"]}
        now, was = by[cur], by.get(prev) if prev else None
        sub = f"Tháng {cur[5:]}/{cur[:4]}: {now['humans']} người · {now['commands']} lệnh · {now['reads']} lượt xem"
        if now["assistant_commands"] or now["assistant_reads"]:
            sub += f" — trợ lý: {now['assistant_commands']} lệnh · {now['assistant_reads']} lượt xem"
        if was:
            sub += f" (tháng trước: {was['humans']} người · {was['commands']} lệnh)"
        items.append({"title": p["title"], "subtitle": sub, "badge": f"{now['humans']} người", "tone": "ok" if now["humans"] else "muted"})
    return [ui.b_list(items or [{"title": "Chưa có hoạt động nào trong các tháng gần đây", "tone": "muted"}], "Mức dùng (người bấm; trợ lý ghi riêng)")]


class CatalogRef(Input):
    pack: str = field("Gói", max_length=41)


class RequestPack(Input):
    pack: str = field("Gói", max_length=41, hidden=True)   # thẻ gói chưa cài điền sẵn — không ai gõ mã gói
    note: str = field("Ghi chú cho nhà cung cấp", "", max_length=500)


@m.command("request_pack",
           purpose="Xin nhà cung cấp mở một gói mà bản cài chưa có.", irreversible=True, effect="Gửi yêu cầu tới các quản trị và nhà cung cấp ngay; tin đã gửi không thu lại được.",
           resource="core.admin", action="manage", title="Yêu cầu mở gói", agent="deny")
async def request_pack(ctx: Ctx, inp: RequestPack) -> dict[str, Any]:
    """Quản trị xin nhà cung cấp mở một gói trong danh mục mà bản cài CHƯA có (0163): chỉ ghi yêu cầu (``core.pack_requests``, không giá, không bật gì),
    báo các quản trị khác của công ty + kênh nhà vận hành. Xin lại cùng gói ⇒ không thêm dòng. Gói đã cài thì không xin được (dùng Bật gói)."""
    c = next((e for e in pack_catalog.not_installed(set(registry.PACKS)) if e.key == inp.pack), None)
    if c is None:
        raise NotFound("Gói này không có trong danh mục chưa cài của bản cài")
    added = await ctx.exec("INSERT INTO core.pack_requests (pack, requested_by, note) VALUES (%s, core.me(), %s) ON CONFLICT DO NOTHING", (c.key, inp.note.strip()))
    if not added:
        return {"pack": c.key, "added": False, "summary": f"Đã gửi yêu cầu mở gói “{c.title}” trước đó — nhà cung cấp sẽ liên hệ"}
    admins = await members_with_grant(ctx, GrantQuery(resource="core.admin", action="manage", scope="all"))
    await ctx.notify([UUID(u) for u in admins], kind="system", title=f"Đã xin nhà cung cấp mở gói “{c.title}”",
                     body="Yêu cầu đã ghi nhận; nhà cung cấp sẽ liên hệ để thống nhất và cài gói vào bản cài.", key=f"pack_request:{c.key}")
    tenant = ctx.p.tenant_id
    ctx.after_commit(_tell_operator(tenant, c.key, c.title))
    return {"pack": c.key, "added": True, "summary": f"Đã gửi yêu cầu mở gói “{c.title}” — nhà cung cấp sẽ liên hệ"}


def _tell_operator(tenant: Any, key: str, title: str) -> Any:
    async def run() -> None:
        alerts.notice(tenant, f"pack_request:{tenant}:{key}", f"Công ty xin mở gói {key} ({title})")
    return run


@m.query("catalog_pack", resource="core.admin", title="Một gói chưa có trên bản cài")
async def catalog_pack(ctx: Ctx, inp: CatalogRef) -> dict[str, Any]:
    """Trang mô tả một gói trong danh mục mà bản cài CHƯA có mã (0163): chữ từ danh mục tĩnh, không nút bật / không màn / không thao tác."""
    c = next((e for e in pack_catalog.not_installed(set(registry.PACKS)) if e.key == inp.pack), None)
    if c is None:
        raise NotFound("Không có gói này trong danh mục")
    blocks: list[dict[str, Any]] = [
        ui.b_callout("warn", f"Chưa có trên bản cài · {KIND_LABEL.get(c.kind, '')}", c.description + ". Mã của gói không nằm trên máy chủ này — bật được sau khi nhà cung cấp cài vào bản cài.")]
    if c.summary:
        blocks.append(ui.b_text(c.summary, "Gói thêm gì"))
    facts = [f for f in (f"Phiên bản mới nhất: {c.version}" if c.version else "", f"Cần lõi: {c.requires_core}" if c.requires_core else "",
                         f"Cần gói: {', '.join(registry.pack_title(r) if r in registry.PACKS else r for r in c.requires)}" if c.requires else "") if f]
    if facts:
        blocks.append(ui.b_list([{"title": f} for f in facts], "Thông tin gói"))
    if c.modules:
        blocks.append(ui.b_list([{"title": t} for t in c.modules], "Phần của gói"))
    if c.roles:
        blocks.append(ui.b_list([{"title": t} for t in c.roles], "Vai đi kèm"))
    asked = await ctx.one("SELECT requested_at FROM core.pack_requests WHERE pack = %s", (c.key,))
    blocks.append(ui.b_callout("ok", "Đã gửi yêu cầu", "Nhà cung cấp đã nhận yêu cầu mở gói này và sẽ liên hệ.") if asked else
                  ui.b_callout("accent", "Liên hệ", "Bấm Yêu cầu mở gói để báo nhà cung cấp; phí và việc cài thống nhất sau."))
    return ui.card(c.title, blocks, subtitle="Chưa có trên bản cài này", icon="square.stack.3d.up",
                   actions=[] if asked else [ui.act("admin.request_pack", "Yêu cầu mở gói", {"pack": c.key}, "primary")])


class PackRef(Input):
    pack: str = field("Gói", options_query="admin.pack_options")


@m.query("pack_info", resource="core.admin", title="Một gói")
async def pack_info(ctx: Ctx, inp: PackRef) -> dict[str, Any]:
    """Xem TRƯỚC khi bật: màn hình của gói, các mục mặc định sẽ thêm (chưa từng cài), mục công ty đã gỡ (giữ nguyên), mẫu có bản mới (chỉ đề xuất)."""
    if inp.pack not in registry.PACKS or inp.pack in await packs.hidden(ctx.conn, ctx.p.tenant_id):
        raise NotFound("Không có gói này")
    from barista import presets

    label, desc = registry.pack_title(inp.pack), registry.PACKS[inp.pack].description
    on = await _enabled(ctx)
    logged = {(r["kind"], r["key"]): r["version"] for r in await ctx.all("SELECT kind, key, version FROM core.pack_installs")}
    # bật gói ⇒ bật KÈM gói nó cần mà chưa bật (0157): xem trước đúng như lúc bật — từng gói theo thứ tự, mục đã nằm ở gói trước thì không lặp ở gói sau
    extras = [p for p in registry.pack_order(inp.pack)[:-1] if p not in on]
    enabled_now, seen = set(on), set()
    per_pack: dict[str, list[presets.Item]] = {}
    plan: list[presets.Item] = []
    for p in ([*extras, inp.pack] if inp.pack not in on else [inp.pack]):
        enabled_now.add(p)
        items = await _plan(ctx, [p], enabled_now)
        # dòng cấu hình của nhà vận hành (hạng trợ lý AI — 0193) vẫn cài, nhưng không hiện cho khách
        per_pack[p] = [i for i in items if (i.kind, i.key) not in logged and (i.kind, i.key) not in seen
                       and not (i.kind == "record" and registry.operator_command(i.data.get("command", "")))]
        seen |= {(i.kind, i.key) for i in per_pack[p]}
        if p == inp.pack:
            plan = items
    present = {("role", r["key"]) for r in await ctx.all("SELECT key FROM core.roles")} | {
        ("grant", f"{r['role']}|{r['resource']}") for r in await ctx.all("SELECT role, resource FROM core.grants")}
    todo = per_pack[inp.pack]
    removed = [i for i in plan if (i.kind, i.key) in logged and i.kind in ("role", "grant") and (i.kind, i.key) not in present]
    newer = [i for i in plan if i.kind == "flow" and logged.get((i.kind, i.key)) not in (None, i.version) and (i.kind, i.key) in logged]

    allowed = await packs.entitled(ctx.conn, ctx.p.tenant_id)
    no_right = [registry.pack_title(p) for p in extras if p not in allowed]
    fixed = inp.pack in registry.always_on()
    state = "Luôn bật" if fixed else "Đang bật" if inp.pack in on else "Chưa bật"
    blocks: list[dict[str, Any]] = [ui.b_callout("ok" if inp.pack in on else "muted", f"{state} · {KIND_LABEL.get(registry.pack_kind(inp.pack), '')}",
                                                 desc + (f". Sẽ bật kèm: {', '.join(registry.pack_title(p) for p in extras)}" if extras and inp.pack not in on else ""))]
    screens = _pack_screens(inp.pack)
    if screens:
        blocks.append(ui.b_list([{"title": t} for t in screens], "Màn hình của gói"))
    blocks.append(ui.b_list([_default_line(i) for i in todo] or [{"title": "Không có gì mới — mặc định của gói đã cài đủ", "tone": "ok"}],
                            ("Khi bật sẽ thêm" if inp.pack not in on else "Mặc định mới chưa cài") + f" ({len(todo)})"))
    blocks += [ui.b_list([_default_line(i) for i in per_pack[p]], f"Bật kèm “{registry.pack_title(p)}” sẽ thêm ({len(per_pack[p])})") for p in extras if per_pack[p]]
    if removed:
        blocks.append(ui.b_list([{**_default_line(i), "tone": "muted"} for i in removed], "Công ty đã gỡ — giữ nguyên, không cài lại"))
    if newer:
        blocks.append(ui.b_list([{**_default_line(i), "subtitle": "Có bản mới của mẫu — bấm Cập nhật để thêm phiên bản mới (hồ sơ đang mở giữ bản cũ)",
                                  "tone": "accent"} for i in newer], "Mẫu có bản mới"))
    blockers = await _blockers(ctx, inp.pack) if inp.pack in on and not fixed else []
    users = [p.title for k, p in registry.PACKS.items() if inp.pack in p.requires and k in on]
    if inp.pack in on and not fixed:
        why = ([f"Gói đang bật cần nó: {', '.join(users)}"] if users else []) + blockers
        blocks.append(ui.b_callout("warn" if why else "muted", "Chưa tắt được" if why else "Khi tắt",
                                   "Còn việc / tiền chưa xử lý — xử lý xong mới tắt được (dữ liệu không bị bỏ dở)." if why else
                                   "Màn hình, nút và công cụ trợ lý của gói ẩn đi; hồ sơ quy trình bám đối tượng của gói ẩn theo; dữ liệu giữ "
                                   "nguyên, bật lại là thấy lại.", facts=why))
    actions = []
    if not fixed and inp.pack not in on and inp.pack not in allowed:
        blocks.insert(1, ui.b_callout("accent", "Liên hệ để mở gói", "Công ty chưa có quyền dùng gói này. Nhà cung cấp mở gói sau khi thống nhất "
                                                                     "phí — mở xong, bấm Bật gói ở đây."))
    elif not fixed and inp.pack not in on and no_right:
        blocks.insert(1, ui.b_callout("accent", "Liên hệ để mở gói kèm", f"Gói này cần bật kèm {', '.join(no_right)} nhưng công ty chưa có quyền dùng. "
                                                                         "Nhà cung cấp mở gói rồi bấm Bật gói ở đây — chưa bật gì cả."))
    elif not fixed and inp.pack not in on:
        actions.append(ui.act("admin.set_pack", "Bật gói", {"pack": inp.pack, "enabled": True}, "primary"))
    elif fixed and todo and inp.pack != "base":   # luôn bật: không bật / tắt, chỉ còn việc cài phần mặc định thiếu
        actions.append(ui.act("admin.set_pack", "Cài phần mặc định còn thiếu", {"pack": inp.pack, "enabled": True}, "primary"))
    elif inp.pack in on and not fixed:
        if todo:
            actions.append(ui.act("admin.set_pack", "Cài phần mặc định còn thiếu", {"pack": inp.pack, "enabled": True}, "primary"))
        if not (blockers or users):
            actions.append(ui.act("admin.set_pack", "Tắt gói", {"pack": inp.pack, "enabled": False}, "destructive"))
    actions += [ui.act("admin.upgrade_flow", f"Cập nhật mẫu “{i.data['name']}”", {"key": i.key}) for i in newer]
    return ui.card(label, blocks, subtitle="Gói đang bật" if inp.pack in on else "Xem trước khi bật — chưa thay đổi gì",
                   icon="square.stack.3d.up", actions=actions)


class FlowKey(Input):
    key: str = field("Mẫu quy trình", hidden=True)


@m.command("upgrade_flow",
           purpose="Mẫu quy trình của gói có bản mới — cập nhật để hồ sơ mới chạy theo bản mới.", irreversible=True, effect="Thêm phiên bản mới của mẫu quy trình; hồ sơ đang mở giữ bản cũ, phiên bản đã thêm không gỡ được.",
           resource="core.admin", action="manage", title="Cập nhật mẫu quy trình lên bản mới của gói", agent="deny")
async def upgrade_flow(ctx: Ctx, inp: FlowKey) -> dict[str, Any]:
    """Mẫu quy trình gói vừa có bản mới ⇒ quản trị CHỌN cập nhật: thêm phiên bản mới (hồ sơ đang mở giữ bản cũ — luật phiên bản của flow)."""
    from barista import presets

    every = set(registry.PACKS)
    item = next((i for i in presets.plan(every, every, set(presets.roles())) if i.kind == "flow" and i.key == inp.key), None)
    if item is None:
        raise NotFound("Gói không có mẫu này")
    await ctx.call("flow.set_template", key=item.key, **item.data)
    await ctx.exec("""INSERT INTO core.pack_installs (kind, key, pack, version) VALUES ('flow', %s, %s, %s)
                      ON CONFLICT (tenant_id, kind, key) DO UPDATE SET version = EXCLUDED.version, installed_at = now()""",
                   (item.key, item.pack, item.version))
    return {"key": item.key, "summary": f"Đã cập nhật mẫu “{item.data['name']}” lên bản mới"}


def _forget(tenant_id: Any) -> Any:
    async def run() -> None:
        packs.invalidate(tenant_id)
    return run


def _forget_vocab(tenant_id: Any) -> Any:
    async def run() -> None:
        vocab.invalidate(tenant_id)
    return run


# ── Danh tính nguồn ngoài (Google Groups…) — bộ kết nối chép thành viên nhóm; kho tài liệu chỉ ĐỌC để so quyền ──

class SyncGroups(Input):
    groups: dict[str, list[str]]  # email nhóm → email thành viên (ghi đè từng nhóm)


@m.command("sync_groups", resource="core.admin", title="Đồng bộ thành viên nhóm nguồn ngoài", internal=True, agent="deny")
async def sync_groups(ctx: Ctx, inp: SyncGroups) -> dict[str, Any]:
    for group, members in inp.groups.items():
        g = group.lower()
        await ctx.exec("DELETE FROM core.identity_groups WHERE group_email = %s", (g,))
        if members:
            async with ctx.conn.cursor() as cur:
                await cur.executemany("INSERT INTO core.identity_groups (group_email, user_email) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                                      [(g, u.lower()) for u in members])
    return {"groups": len(inp.groups)}


# ── Ai giữ quyền gì — để module khác chọn người phụ trách theo MA TRẬN QUYỀN (dữ liệu), không viết cứng tên vai trò ──

class GrantQuery(Input):
    resource: str
    action: str
    scope: Literal["own", "unit", "team", "all"] = "all"


@m.query("members_with_grant", resource="core.admin", title="Thành viên có quyền", internal=True, agent="deny", callers=("acct",), family="follow_up",
         why="Bên nghe sự kiện chọn người phụ trách việc theo dõi theo ma trận quyền (acct.open_collection khi khoản quá hạn không có người phụ "
             "trách; acct.locked_task khi việc chạy nền không ghi được sổ vì kỳ đã khoá — 0119; gói khai consumes(\"follow_up\") khi đối tượng "
             "của gói tới mốc cần theo, vd thương vụ được ký ⇒ việc thu phí) — không người bấm, bên gọi không kiểm thêm. Đọc core.memberships × core.grants. "
             "Trả danh sách user_id (chuỗi) — không tên, không vai.")
async def members_with_grant(ctx: Ctx, inp: GrantQuery) -> list[str]:
    """Thành viên đang hoạt động có ``action`` trên ``resource`` với phạm vi ≥ ``scope`` — người vào sớm trước."""
    rank = {"own": 1, "unit": 2, "team": 3, "all": 4}
    rows = await ctx.all(
        """SELECT m.user_id FROM core.memberships m
           JOIN core.grants g ON g.tenant_id = m.tenant_id AND g.role = m.role
           WHERE m.active AND g.resource = %s AND %s = ANY(g.actions)
             AND (CASE g.scope WHEN 'all' THEN 4 WHEN 'team' THEN 3 WHEN 'unit' THEN 2 ELSE 1 END) >= %s
           ORDER BY m.created_at, m.user_id""", (inp.resource, inp.action, rank[inp.scope]))
    return [str(r["user_id"]) for r in rows]


# ── Lỗi hệ thống của công ty (ROADMAP A4): lõi phát core.system_error, module quản trị báo người xem được Nhật ký cả công ty ──

@m.on("core.system_error")
async def tell_admins_about_errors(ctx: Ctx, event: Event) -> None:
    """Một thông báo / người / ngày (khoá theo ngày) cho người có quyền đọc Nhật ký thao tác phạm vi cả công ty — theo ma trận quyền, không
    viết cứng vai. Bấm ⇒ Nhật ký lọc sẵn "Lỗi hệ thống" (chi tiết kỹ thuật ở đó)."""
    people = await members_with_grant(ctx, GrantQuery(resource="core.audit", action="read", scope="all"))
    await ctx.notify([UUID(x) for x in people], kind="system", title="Có lỗi hệ thống hôm nay",
                     body=f"Thao tác “{_title(event.payload.get('command', ''))}” gặp lỗi. Xem Nhật ký thao tác, lọc “Lỗi hệ thống”.",
                     open={"query": "admin.audit", "input": {"status": "error"}}, key=f"system_error:{today().isoformat()}")


def _title(command: str) -> str:
    op = registry.OPS.get(command)
    return op.title if op else command


# ── Sức khoẻ dữ liệu (module health gom) ──

@m.query("delivery_health", resource="core.admin", title="Sự kiện lỗi (đếm)", internal=True, agent="deny", callers=("health",),
         why="health.overview (khai min_scope=\"all\") gom sức khoẻ dữ liệu: đếm core.deliveries của cả công ty, mọi module. Trả CHỈ "
             "{dead, retrying, oldest} + errors (số lỗi hệ thống 7 ngày trong nhật ký thao tác) — chỉ số đếm, không nội dung sự kiện, "
             "không nội dung lỗi.")
async def delivery_health(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Phản ứng tự động (lập khoản phải thu, việc theo dõi, thông báo…) đã dừng thử / đang thử lại — số trên màn khác có thể thiếu vì đây."""
    r = await ctx.one(
        """SELECT count(*) FILTER (WHERE d.status = 'dead') AS dead, count(*) FILTER (WHERE d.status = 'pending' AND d.attempts > 0) AS retrying,
                  min(e.created_at) FILTER (WHERE d.status = 'dead') AS oldest
           FROM core.deliveries d JOIN core.events e ON e.id = d.event_id""")
    assert r is not None
    errors = await ctx.val("SELECT count(*) FROM core.audit_log WHERE status = 'error' AND at > now() - interval '7 days'")
    return {**r, "errors": int(errors)}


# ---- Màn hình (0069): module tự khai phân hệ / góp màn vào phân hệ khác; workspaces.py chỉ ráp và lọc theo quyền
_STORE_SCREENS = (
    # Store (chợ gói) ẨN khỏi menu 24/11/2026 (anh Anh, ADR 0261): tầng hệ thống — nhà vận hành bật gói khi cài; người dùng thấy "Kết nối & kỹ năng".
    Screen("packs", "Gói đang dùng", "square.stack.3d.up", "admin.packs", open=Open("admin.pack_info", {"pack": "id"}, "Trang gói")),
    Screen("profile", "Hồ sơ công ty cho trợ lý", "building.columns", "admin.profile",
           row_actions=(A("admin.set_profile", "Sửa", prefill={"about": "about"}, style="primary"),)),
    Screen("brand", "Nhận diện thương hiệu", "paintpalette", "admin.brand_view",
           row_actions=(A("admin.set_brand", "Sửa", prefill={"primary_color": "primary_color", "font": "font", "footer": "footer"}, style="primary"),)),
    Screen("vocabulary", "Từ vựng công ty", "textformat", "admin.vocabulary",
           row_actions=(A("admin.set_term", "Đổi chữ", bind={"term": "id"}, prefill={"label": "label"}, style="primary"),)),
    Screen("settings", "Cài đặt công ty", "switch.2", "admin.settings",
           row_actions=(A("admin.set_setting", "Đổi", bind={"key": "id"}, prefill={"enabled": "enabled"}, style="primary"),)),
    Screen("field_defs", "Trường riêng của công ty", "rectangle.and.pencil.and.ellipsis", "admin.field_defs",
           actions=(A("admin.save_field", "Thêm trường", style="primary"),),
           row_actions=(A("admin.save_field", "Sửa", bind={"key": "key", "subject": "subject", "kind": "kind"},
                          prefill={"label": "label", "options": "choices", "help": "help", "active": "active"}),)),
    Screen("timezone", "Múi giờ công ty", "globe", "admin.timezone",
           row_actions=(A("admin.set_timezone", "Đổi", prefill={"timezone": "timezone"}, style="primary"),)),
    Screen("country", "Quốc gia công ty", "flag", "admin.country",
           row_actions=(A("admin.set_country", "Đổi", prefill={"country": "country"}, style="primary"),)),
    Screen("import_retention", "Giữ nội dung dòng nhập file", "clock.arrow.circlepath", "core.import_retention",
           row_actions=(A("core.set_import_retention", "Đổi", style="primary"),)),
    Screen("import_mappings", "Cách khớp cột nhập Excel", "tablecells", "core.import_mappings",
           row_actions=(A("core.edit_import_mapping", "Sửa", bind={"id": "id"}, prefill={"name": "name"}, style="primary"),
                        A("core.delete_import_mapping", "Quên", bind={"id": "id"}, style="destructive"))),
    Screen("idempotency_retention", "Chống ghi trùng khi gửi lại", "arrow.triangle.2.circlepath", "core.idempotency_retention",
           row_actions=(A("core.set_idempotency_retention", "Đổi", style="primary"),)),
)
_ADMIN_SCREENS = (
    Screen("users", "Người dùng", "person.badge.key.fill", "admin.users", tint="gray", group="Hệ thống",
           actions=(A("admin.invite_user", style="primary"),),
           row_actions=(A("admin.update_member", bind={"email": "email"}), A("admin.add_team", bind={"email": "email"}), A("admin.remove_team", bind={"email": "email"}),
                        A("admin.unlink_google", "Gỡ liên kết Google", bind={"email": "email"}, when={"google": ["linked"]},
                          style="destructive"))),
    Screen("teams", "Cây tổ chức", "point.3.connected.trianglepath.dotted", "admin.teams", group="Hệ thống",
           actions=(A("admin.upsert_team", style="primary"),)),
    Screen("legal_entities", "Pháp nhân", "building.columns.fill", "admin.legal_entities", tint="gray", group="Hệ thống",
           actions=(A("admin.upsert_entity", style="primary"),), row_actions=(A("admin.upsert_entity", "Sửa", bind={"code": "code"}),)),
    Screen("permissions", "Vai trò & quyền", "lock.shield.fill", "admin.permissions", tint="gray", group="Hệ thống",
           actions=(A("admin.set_grant", style="primary"), A("admin.upsert_role"))),
    # 0239 phần 3: ma trận đọc được — nhóm thông tin nhạy cảm × vai, ghi vào chính ma trận quyền
    Screen("info_access", "Ai thấy thông tin gì", "eye.trianglebadge.exclamationmark", "admin.info_access", tint="red", group="Hệ thống",
           actions=(A("admin.set_info_access", style="primary"),), row_actions=(A("admin.set_info_access", "Đổi", bind={"group": "id"}),)),
    Screen("config_questions", "Câu hỏi cấu hình công ty", "questionmark.bubble", "admin.config_questions", group="Hệ thống", pinnable=False),
    Screen("chains", "Chuỗi & luật duyệt", "arrow.triangle.branch", "admin.chains", group="Hệ thống",
           actions=(A("admin.set_chain", style="primary"), A("admin.set_rule"))),
    # phần mặc định của gói còn thiếu sau bản cập nhật (vai / quyền / chuỗi duyệt mới) — xem + cài thêm, không ghi đè chỗ công ty đã đổi
    Screen("defaults_drift", "Phần mặc định chưa cài", "square.stack.3d.up", "admin.defaults_drift", group="Hệ thống"),   # nút cài nằm trong khối cảnh báo, chỉ khi có mục thiếu
    Screen("agent_groups", "Nhóm công cụ của trợ lý", "square.stack.3d.up", "admin.agent_groups", group="Hệ thống",
           row_actions=(A("admin.set_agent_group", "Đổi", bind={"group": "id"}, prefill={"mode": "mode"}),)),
    # bộ công cụ trợ lý theo vai (0218): vai mang sẵn nhóm nào mỗi lượt — gọn ⇒ ít token; không cấp quyền
    # 0239 phần 4: câu gợi ý của trợ lý theo vai là dữ liệu công ty — bật / tắt / sửa chữ / thêm câu riêng
    Screen("role_hints", "Câu gợi ý trợ lý theo vai", "text.bubble", "core.role_hints", group="Hệ thống", pinnable=False,
           actions=(A("core.set_role_hint", "Thêm câu riêng", style="primary"),),
           row_actions=(A("core.set_role_hint", "Sửa", bind={"role": "role", "key": "key"}, prefill={"text": "text", "enabled": "enabled"}, style="primary"),
                        A("core.set_role_hint", "Tắt", bind={"role": "role", "key": "key"}, preset={"enabled": False}, when={"enabled": [True]}),
                        A("core.reset_role_hint", "Về mặc định", bind={"role": "role", "key": "key"}, when={"changed": [True]}))),
    Screen("role_tools", "Công cụ trợ lý theo vai", "person.crop.rectangle.stack", "core.role_tools", group="Hệ thống",
           row_actions=(A("core.set_role_tools", "Đổi", bind={"role": "role"}, prefill={"groups": "groups_in", "tools": "tools_in"}, style="primary"),
                        A("core.clear_role_tools", "Để trợ lý tự tuyển", bind={"role": "role"}, when={"state": ["set"]}))),
    # thanh bên trái theo vai (ADR 0255): phân hệ hiện sẵn của vai là dữ liệu công ty (gói khai mặc định, công ty đổi, nâng gói không ghi đè); không cấp quyền
    Screen("role_rail", "Thanh bên theo vai", "sidebar.left", "core.role_rail", group="Hệ thống", pinnable=False,
           row_actions=(A("core.set_role_rail", "Đổi", bind={"role": "role"}, prefill={"workspaces": "workspaces_in"}, style="primary"),
                        A("core.clear_role_rail", "Về mặc định của gói", bind={"role": "role"}, when={"state": ["set"]}))),
    Screen("agent", "Trợ lý được làm gì", "sparkles", "admin.agent_policies", group="Hệ thống",
           actions=(A("admin.set_agent_policy", style="primary"), A("admin.set_agent_trust", "Ngưỡng tin cậy")),
           row_actions=(A("admin.set_agent_policy", "Đổi", bind={"command": "id"},
                          prefill={"mode": "mode_set", "daily_cap": "daily_cap", "money_cap_vnd": "money_cap_vnd"}),
                        A("admin.set_agent_policy", "Nới: tự làm", bind={"command": "id"}, preset={"mode": "allow"}, when={"can_promote": ["yes"]},
                          style="primary"))),
    Screen("audit", "Nhật ký thao tác", "list.bullet.rectangle.fill", "admin.audit", tint="gray", group="Hệ thống"),
    Screen("screen_usage", "Màn nào đang được dùng", "chart.bar.xaxis", "core.screen_usage", tint="gray", group="Hệ thống", pinnable=False,
           about="Xem màn nào người trong công ty đang mở và màn nào chưa ai mở trong 30 ngày qua, để biết nên giữ, gộp hay bỏ màn nào."),
    Screen("agent_actions", "Hành động của trợ lý", "point.3.connected.trianglepath.dotted", "admin.agent_actions", tint="purple", group="Hệ thống",
           open=Open("admin.trace", {"audit_id": "id"}, "Chuỗi hành động")),
    Screen("ai_usage", "Lượt dùng trợ lý AI", "gauge.with.dots.needle.bottom.50percent", "core.ai_usage", group="Hệ thống"),
    # CHÍNH SÁCH TRỢ LÝ AI (0193) — của NHÀ VẬN HÀNH (``core.ai_policy``, không vai nào của khách cấp được ⇒ khách không thấy các màn này):
    # hạng, model, hạn mức token mỗi người / tháng, giọng nói; vai → hạng; báo cáo token theo người × tháng. Trợ lý không làm thay.
    Screen("ai_tiers", "Hạng trợ lý AI", "brain.head.profile", "core.ai_tiers", group="Nhà vận hành",
           actions=(A("core.save_ai_tier", style="primary"),),
           row_actions=(A("core.save_ai_tier", "Sửa", bind={"key": "key"},
                          prefill={"label": "label", "model": "model_in", "choices": "choices_in", "person_monthly_tokens": "cap_in", "voice": "voice",
                                   "is_default": "is_default", "sort": "sort"}),)),
    Screen("ai_role_tiers", "Vai → hạng trợ lý AI", "person.2.badge.gearshape", "core.ai_role_tiers", group="Nhà vận hành",
           row_actions=(A("core.set_role_ai_tier", "Đổi hạng", bind={"role": "role"}, prefill={"tier": "tier"}, style="primary"),)),
    Screen("ai_tokens", "Token trợ lý AI theo người", "tablecells", "core.ai_token_report", group="Nhà vận hành"),
)

# MỘT phân hệ "Quản trị" (rà menu 20/11/2026: hai phân hệ cài đặt chồng nhau, 32 màn phẳng): màn xếp theo NHÓM — việc hay dùng ở trên, kỹ thuật vào "Nâng cao"
# (web gấp lại). Khoá màn giữ nguyên ⇒ ghim cũ vẫn trỏ đúng (``manifest`` tìm màn theo khoá khi phân hệ đổi). Màn góp từ module khác (``m.screens("admin", …)``) giữ nhóm
# "Hệ thống" của chúng và đứng cuối.
_GROUP_OF = {
    **dict.fromkeys(("profile", "brand", "settings", "legal_entities"), "Công ty"),
    **dict.fromkeys(("users", "teams", "permissions", "chains", "info_access"), "Người & quyền"),
    **dict.fromkeys(("packs",), "Gói & ứng dụng"),
    **dict.fromkeys(("agent", "agent_actions", "ai_usage"), "Trợ lý AI"),
    **dict.fromkeys(("vocabulary", "timezone", "country", "field_defs", "import_retention", "import_mappings", "idempotency_retention", "agent_groups",
                     "role_hints", "role_tools", "role_rail", "config_questions"), "Nâng cao"),
    **dict.fromkeys(("audit", "defaults_drift"), "Hệ thống"),
}
_GROUP_ORDER = ("Công ty", "Người & quyền", "Gói & ứng dụng", "Trợ lý AI", "Nâng cao", "Nhà vận hành", "Hệ thống")


def _settings_screens() -> tuple[Screen, ...]:
    placed = [replace(x, group=_GROUP_OF.get(x.key, x.group)) for x in (*_STORE_SCREENS, *_ADMIN_SCREENS)]
    return tuple(sorted(placed, key=lambda x: _GROUP_ORDER.index(x.group) if x.group in _GROUP_ORDER else len(_GROUP_ORDER)))


m.workspace(Workspace("admin", "Quản trị", "gearshape.2.fill", _settings_screens(), order=100))
m.screens("admin", (
    Screen("deliveries", "Sự kiện lỗi", "exclamationmark.triangle.fill", "admin.deliveries", tint="red", group="Hệ thống",
           row_actions=(A("admin.retry_delivery", bind={"delivery_id": "id"}, style="primary"),
                        A("admin.dismiss_delivery", "Bỏ qua (có lý do)", bind={"delivery_id": "id"}, style="destructive"))),
), rank=30)
