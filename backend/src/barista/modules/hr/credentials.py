"""CHỨNG CHỈ / GIẤY PHÉP HÀNH NGHỀ của nhân sự (0202) — chung cho mọi ngành (môi giới, điều dưỡng, lái xe, thợ điện, an toàn lao động…).

* LOẠI chứng chỉ là DỮ LIỆU của công ty (``hr.credential_kinds``): mã, tên, nơi cấp thường gặp, có hạn không, thời hạn mặc định, báo trước bao
  nhiêu ngày, vai / đội phải có. Lõi không có loại nào; gói ngành góp loại mặc định qua ``Pack(records=(("hr.save_credential_kind", …),))``.
* MỖI NGƯỜI nhiều chứng chỉ (``hr.credentials``), chỉ thêm — sai thì thu hồi rồi ghi dòng mới. TRẠNG THÁI tính lúc đọc: còn hạn / sắp hết hạn /
  đã hết hạn / chưa có ngày hết hạn / bị thay (cùng người cùng loại có bản mới hơn) / đã thu hồi.
* AI THẤY GÌ: chính người đó thấy chứng chỉ của mình (kể cả số + bản scan); trưởng đội thấy TRẠNG THÁI chứng chỉ của đội — số chứng chỉ và bản scan ở
  bảng riêng ``hr.credential_private`` (tài nguyên riêng) nên trưởng đội không đọc được; phụ trách nhân sự cả công ty ghi + xem đủ. Số chứng chỉ không
  vào nhật ký thao tác (``audit=False``), không vào kết quả công cụ của trợ lý (``credential_watch``), không vào thông báo.
* NHẮC: vòng quét ``remind_credentials`` báo người đó + quản lý trực tiếp (không có ⇒ trưởng đội gần nhất) MỘT lần mỗi mốc mỗi chứng chỉ (sổ
  ``credential_notices``), gộp theo người nhận. Không nhắc mốc đã qua lúc ghi (ghi một chứng chỉ đã hết hạn thì không báo "đã hết hạn"); dòng nạp từ
  hệ cũ (``historical=True`` — 0185) là tham chiếu: không nhắc gì tới khi người phụ trách XÁC NHẬN (``verify_credentials``), và chỉ nhắc mốc sau
  ngày xác nhận. KHÔNG chặn việc gì vì thiếu / hết hạn chứng chỉ — luật chặn là quyết định sau của chủ doanh nghiệp.
"""

import base64
import calendar
import hashlib
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date, timedelta
from typing import Any, Literal, LiteralString
from uuid import UUID

from barista.api import Action as A
from barista.api import (
    Ctx,
    Day,
    Denied,
    Desk,
    Event,
    Input,
    Invalid,
    Line,
    NoInput,
    NotFound,
    Screen,
    Workspace,
    field,
    files,
    fold,
    kpi,
    line,
    snake_from_name,
    today,
    ui,
)
from barista.modules.hr import _names, _seen, m

STATUS = {"valid": "Còn hạn", "expiring": "Sắp hết hạn", "expired": "Đã hết hạn", "unknown": "Chưa có ngày hết hạn", "replaced": "Đã có bản mới",
          "revoked": "Đã thu hồi", "missing": "Chưa có"}
TONES = {"valid": "green", "expiring": "orange", "expired": "red", "unknown": "gray", "replaced": "gray", "revoked": "gray", "missing": "red"}
LIVE = ["valid", "expiring", "expired", "unknown"]
SHOW = {"current": "Đang dùng", "expiring": "Sắp hết hạn", "expired": "Đã hết hạn", "missing": "Thiếu (vai / đội phải có)",
        "unverified": "Nạp từ hệ cũ — chưa xác nhận", "history": "Tất cả, kể cả bản cũ / đã thu hồi"}
KIND_KEY_HELP = "Chữ thường không dấu, số, gạch dưới — vd the_an_toan, bang_lai_c"
FILE_TYPES = {"application/pdf": b"%PDF", "image/png": b"\x89PNG", "image/jpeg": b"\xff\xd8\xff"}
FILE_MAX = 10 * 1024 * 1024
DIGEST_NAMES = 3


def plus_months(d: date, n: int) -> date:
    """Ngày CUỐI còn hiệu lực của chứng chỉ cấp ngày ``d`` có thời hạn ``n`` tháng (cấp 15/03/2024, 60 tháng ⇒ 14/03/2029)."""
    y, mo = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(y, mo + 1, min(d.day, calendar.monthrange(y, mo + 1)[1])) - timedelta(days=1)


# ── Trạng thái tính lúc đọc: dòng "hiện hành" của một người × một loại = dòng còn sống có ngày hết hạn xa nhất (trống = không hạn), mới ghi nhất ──

_RANKED: LiteralString = """WITH c AS (
  SELECT c.*, k.key AS kind_key, k.name AS kind_name, k.remind_days, k.expires AS kind_expires,
         row_number() OVER (PARTITION BY c.party_id, c.kind_id ORDER BY (c.revoked_at IS NULL) DESC, c.expires_on DESC NULLS FIRST, c.created_at DESC) AS rn
  FROM hr.credentials c JOIN hr.credential_kinds k ON k.tenant_id = c.tenant_id AND k.id = c.kind_id)
SELECT c.id, c.party_id, c.user_id, c.team_id, c.kind_key, c.kind_name, c.issued_on, c.expires_on, c.issuer, c.note, c.recorded_on,
       (c.loaded_at IS NOT NULL AND c.verified_on IS NULL) AS unverified,
       CASE WHEN c.revoked_at IS NOT NULL THEN 'revoked' WHEN c.rn > 1 THEN 'replaced'
            WHEN c.expires_on IS NULL THEN CASE WHEN c.kind_expires THEN 'unknown' ELSE 'valid' END
            WHEN c.expires_on < %(d)s::date THEN 'expired'
            WHEN c.expires_on <= %(d)s::date + c.remind_days THEN 'expiring' ELSE 'valid' END AS status,
       c.expires_on - %(d)s::date AS days_left"""
# số + có bản scan: chỉ người có dòng ở bảng riêng dưới RLS (chính người đó, nhân sự cả công ty) — trưởng đội nhận NULL
_WITH_PRIVATE: LiteralString = _RANKED + """, pv.number, (pv.file_id IS NOT NULL) AS has_file FROM c
  LEFT JOIN hr.credential_private pv ON pv.tenant_id = c.tenant_id AND pv.credential_id = c.id"""
_WITHOUT_PRIVATE: LiteralString = _RANKED + " FROM c"

# Mốc nhắc ĐANG tới hạn mà chưa xử lý — một định nghĩa cho câu chọn công ty của vòng quét và cho chính lượt quét.
_MILESTONE: LiteralString = "CASE WHEN c.expires_on < core.today_of(c.tenant_id) THEN 'expired' ELSE 'expiring' END"
_DUE: LiteralString = """ FROM hr.credentials c JOIN hr.credential_kinds k ON k.tenant_id = c.tenant_id AND k.id = c.kind_id
WHERE c.revoked_at IS NULL AND c.expires_on IS NOT NULL
  AND (c.loaded_at IS NULL OR c.verified_on IS NOT NULL)
  AND c.expires_on >= coalesce(c.verified_on, c.recorded_on)
  AND c.expires_on - k.remind_days <= core.today_of(c.tenant_id)
  AND NOT EXISTS (SELECT 1 FROM hr.credentials o WHERE o.tenant_id = c.tenant_id AND o.party_id = c.party_id AND o.kind_id = c.kind_id AND o.id <> c.id
                  AND o.revoked_at IS NULL AND (o.expires_on IS NULL OR o.expires_on > c.expires_on OR (o.expires_on = c.expires_on AND o.created_at > c.created_at)))
  AND NOT EXISTS (SELECT 1 FROM hr.credential_notices n WHERE n.tenant_id = c.tenant_id AND n.credential_id = c.id
                  AND n.milestone = CASE WHEN c.expires_on < core.today_of(c.tenant_id) THEN 'expired' ELSE 'expiring' END)"""


async def _team_of(ctx: Ctx, party_id: UUID, user_id: UUID | None) -> UUID | None:
    """Đội của người có chứng chỉ (cho phạm vi "đội tôi quản"): đội chính của tài khoản — một nguồn ở lõi (0256). Người không tài khoản: không đội."""
    return await ctx.home_team(user_id)


async def resync(ctx: Ctx, party_ids: list[UUID], *, logins: bool = False) -> int:
    """Đội (và với ``logins``: tài khoản) chủ dòng cho RLS theo hồ sơ HIỆN TẠI — gọi khi đổi phân công / thành viên đội. Không đổi gì khác của dòng."""
    n = 0
    for pid in dict.fromkeys(party_ids):
        user = await ctx.val("SELECT user_id FROM hr.credentials WHERE party_id = %s ORDER BY created_at DESC LIMIT 1", (pid,))
        if logins:
            user = (await ctx.call_system("parties.resolve_for_payment", party_id=pid))["user_id"]
            await ctx.exec("""UPDATE hr.credential_private SET user_id = %s WHERE user_id IS DISTINCT FROM %s
                              AND credential_id IN (SELECT id FROM hr.credentials WHERE party_id = %s)""", (user, user, pid))
        team = await _team_of(ctx, pid, user)
        n += await ctx.exec("UPDATE hr.credentials SET user_id = %s, team_id = %s WHERE party_id = %s AND (user_id IS DISTINCT FROM %s OR team_id IS DISTINCT FROM %s)",
                            (user, team, pid, user, team))
    return n


@m.on("admin.team_members_changed")
async def credentials_follow_team(ctx: Ctx, event: Event) -> None:
    """Người vào / rời đội ⇒ chứng chỉ của họ theo đội mới (trưởng đội mới thấy, trưởng đội cũ thôi thấy). Chỉ đồng bộ chủ dòng — không thông báo,
    nên nạp lịch sử cũng chạy như thường."""
    team = event.payload.get("team_id")
    if team is None:
        return
    rows = await ctx.all("""SELECT DISTINCT c.party_id FROM hr.credentials c WHERE c.team_id = %s
                            OR c.user_id IN (SELECT tm.user_id FROM core.team_members tm WHERE tm.team_id = %s) LIMIT 5000""", (team, team))
    await resync(ctx, [r["party_id"] for r in rows], logins=True)


# ── Loại chứng chỉ (dữ liệu của công ty) ──

class KindIn(Input):
    key: str = field("Mã loại", "", options_query="hr.credential_kind_options", max_length=40, example="the_an_toan", group="Thêm chi tiết",
                     placeholder="Loại mới — máy đặt mã từ tên", help="Chọn loại có sẵn để sửa; để trống khi thêm loại mới. " + KIND_KEY_HELP)
    name: str = field("Tên loại", max_length=120, example="Thẻ an toàn lao động")
    issuer_hint: str = field("Nơi cấp thường gặp", "", max_length=200, example="Trung tâm huấn luyện an toàn", group="Thêm chi tiết")
    expires: bool = field("Có thời hạn", True, help="Bỏ chọn: chứng chỉ vĩnh viễn (không nhắc)")
    validity_months: int | None = field("Thời hạn mặc định (tháng)", None, ge=1, le=600, example=24,
                                        help="Ghi chứng chỉ không có ngày hết hạn ⇒ ngày cấp + số tháng này")
    remind_days: int = field("Báo trước (ngày)", 30, ge=0, le=365, help="Báo người đó và quản lý trước ngày hết hạn chừng này ngày", group="Thêm chi tiết")
    required_roles: list[str] = field("Vai phải có", [], kind="multiselect", options_query="admin.role_options",
                                      help="Người giữ các vai này mà chưa có ⇒ hiện ở danh sách Thiếu (không chặn việc gì)", group="Thêm chi tiết")
    required_teams: list[str] = field("Đội phải có (cả đội con)", [], kind="multiselect", options_query="admin.team_options", group="Thêm chi tiết")
    active: bool = field("Đang dùng", True, help="Bỏ chọn: không chọn được khi ghi mới; chứng chỉ đã ghi giữ nguyên", group="Thêm chi tiết")
    only_new: bool = field("Chỉ thêm khi chưa có", False, hidden=True, agent=False, help="Bộ mặc định của gói: công ty đã đặt thì giữ của công ty")


@m.command("save_credential_kind", resource="hr.credentials", action="manage", title="Thêm / sửa loại chứng chỉ", category="setup", importable=True,
           agent="propose", min_scope="all")
async def save_credential_kind(ctx: Ctx, inp: KindIn) -> dict[str, Any]:
    """Loại chứng chỉ là danh mục của công ty: gói ngành góp bộ mặc định qua đúng lệnh này lúc cài (``only_new``); nhân sự đổi / tắt / thêm."""
    name = inp.name.strip()
    key = inp.key.strip().lower() or (snake_from_name(name, {r["key"] for r in await ctx.all("SELECT key FROM hr.credential_kinds")}) if name else "")   # 0225: máy đặt mã từ tên
    if not key or not name or not key[0].isalpha() or not all(ch.isascii() and (ch.isalnum() or ch == "_") for ch in key) or len(key) > 40:
        raise Invalid(f"Mã loại: {KIND_KEY_HELP}; cần tên loại")
    if not inp.expires and inp.validity_months is not None:
        raise Invalid("Loại vĩnh viễn không có thời hạn mặc định")
    roles = sorted({r.strip() for r in inp.required_roles if r.strip()})
    if roles and (bad := set(roles) - {r["key"] for r in await ctx.all("SELECT key FROM core.roles WHERE key = ANY(%s)", (roles,))}):
        raise NotFound(f"Không có vai {', '.join(sorted(bad))}")
    codes = sorted({c.strip() for c in inp.required_teams if c.strip()})
    teams = {r["code"]: r["id"] for r in await ctx.all("SELECT code, id FROM core.teams WHERE code = ANY(%s)", (codes,))} if codes else {}
    if bad := set(codes) - set(teams):
        raise NotFound(f"Không có đội {', '.join(sorted(bad))}")
    row = (key, name, inp.issuer_hint.strip(), inp.expires, inp.validity_months, inp.remind_days, roles, list(teams.values()), inp.active)
    if inp.only_new:
        n = await ctx.exec("""INSERT INTO hr.credential_kinds (key, name, issuer_hint, expires, validity_months, remind_days, required_roles, required_teams,
                              active, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, core.me()) ON CONFLICT (tenant_id, key) DO NOTHING""", row)
        return {"key": key, "added": bool(n)}
    await ctx.exec("""INSERT INTO hr.credential_kinds (key, name, issuer_hint, expires, validity_months, remind_days, required_roles, required_teams,
                      active, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, core.me())
                      ON CONFLICT (tenant_id, key) DO UPDATE SET name = excluded.name, issuer_hint = excluded.issuer_hint, expires = excluded.expires,
                      validity_months = excluded.validity_months, remind_days = excluded.remind_days, required_roles = excluded.required_roles,
                      required_teams = excluded.required_teams, active = excluded.active, updated_at = now()""", row)
    return {"key": key, "added": True, "summary": f"Loại chứng chỉ {name}"}


@m.query("credential_kinds", resource="hr.credentials", title="Loại chứng chỉ", min_scope="all")
async def credential_kinds(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("""SELECT k.id, k.key, k.name, k.issuer_hint, k.expires, k.validity_months, k.remind_days, k.required_roles, k.active,
                                   coalesce((SELECT array_agg(t.code ORDER BY t.code) FROM core.teams t WHERE t.id = ANY(k.required_teams)), '{}') AS required_teams,
                                   (SELECT count(*) FROM hr.credentials c WHERE c.kind_id = k.id AND c.revoked_at IS NULL) AS used
                            FROM hr.credential_kinds k ORDER BY k.active DESC, k.name LIMIT 500""")
    for r in rows:
        r["state"] = "active" if r["active"] else "inactive"
        r["expiry"] = "yes" if r["expires"] else "no"
        r["required"] = ", ".join([*r["required_roles"], *r["required_teams"]])
    return ui.table(rows, [ui.col("name", "Tên loại", width=220), ui.col("key", "Mã"), ui.col("expiry", "Có hạn", "badge", labels={"yes": "Có hạn", "no": "Vĩnh viễn"}),
                           ui.col("validity_months", "Thời hạn (tháng)", "number"), ui.col("remind_days", "Báo trước (ngày)", "number"),
                           ui.col("required", "Vai / đội phải có"), ui.col("used", "Đang ghi", "number"),
                           ui.col("state", "Trạng thái", "badge", labels={"active": "Đang dùng", "inactive": "Đã tắt"}, tones={"active": "green", "inactive": "gray"})],
                    title="Loại chứng chỉ", limit=500, empty="Chưa có loại chứng chỉ nào — bấm “Thêm / sửa loại chứng chỉ”.",
                    note="Danh mục của công ty: loại nào, có hạn không, báo trước bao lâu, vai / đội nào phải có. Không chặn việc gì khi thiếu.")


@m.query("credential_kind_options", resource="hr.credentials", title="Chọn loại chứng chỉ")
async def credential_kind_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all("SELECT key AS value, name AS label FROM hr.credential_kinds WHERE active ORDER BY name"))


async def _kind(ctx: Ctx, text: str) -> dict[str, Any]:
    """Loại theo mã, hoặc theo tên (không phân biệt dấu) — cột Excel ghi tên loại."""
    t = text.strip()
    rows = await ctx.all("SELECT id, key, name, expires, validity_months, active FROM hr.credential_kinds")
    hit = next((r for r in rows if r["key"] == t.lower()), None) or next((r for r in rows if fold(r["name"]).strip() == fold(t)), None)
    if hit is None:
        raise NotFound(f"Không có loại chứng chỉ “{t}” — thêm ở Nhân sự › Loại chứng chỉ")
    if not hit["active"]:
        raise Invalid(f"Loại {hit['name']} đã tắt")
    return hit


# ── Ghi / thu hồi / xác nhận chứng chỉ (phụ trách nhân sự cả công ty) ──

async def _person(ctx: Ctx, text: str) -> UUID:
    """Người do NGƯỜI GHI nêu (ô chọn / cột Excel): mã trong danh mục, email thành viên, mã nhân viên, hoặc tên đúng như danh mục — chỉ người họ
    thấy trong danh mục (0125 §B: kiểm bằng quyền người ghi)."""
    t = text.strip()
    if not t:
        raise Invalid("Cần người")
    pid: UUID | None = None
    try:
        pid = UUID(t)
    except ValueError:
        if "@" in t:
            uid = await ctx.val("""SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()
                                   WHERE lower(u.email) = lower(%s)""", (t,))
            if uid is None:
                raise NotFound(f"Không có thành viên {t}") from None
            pid = (await ctx.call_system("parties.ensure_member", user_id=uid))["party_id"]
        else:
            codes = [r["party_id"] for r in await ctx.all("SELECT DISTINCT party_id FROM hr.employments WHERE employee_code = %s", (t,))]
            if len(codes) == 1:
                pid = codes[0]
            elif not codes:
                hits = [o["value"] for o in await ctx.call("parties.options") if fold(o["label"].rsplit(" (", 1)[0]).strip() == fold(t)]
                if len(hits) != 1:
                    raise Invalid(f"“{t}”: {'không thấy' if not hits else 'trùng tên'} trong danh mục — ghi email thành viên hoặc mã nhân viên") from None
                pid = UUID(hits[0])
            else:
                raise Invalid(f"Mã nhân viên {t} thuộc nhiều người (nhiều pháp nhân) — ghi email thành viên") from None
    assert pid is not None
    if str(pid) not in await _seen(ctx, [pid]):
        raise NotFound("Không thấy người này trong danh mục bạn được xem")
    return pid


def _need_all(ctx: Ctx) -> None:
    if not ctx.p.has_scope("hr.credentials", "all"):
        raise Denied("Cần quyền nhân sự cả công ty")


class RecordCredential(Input):
    person: str = field("Người", kind="select", options_query="parties.options", max_length=200,
                        help="Nhập Excel: email thành viên, mã nhân viên hoặc tên đúng như danh mục")
    kind_key: str = field("Loại chứng chỉ", options_query="hr.credential_kind_options", max_length=120, example="the_an_toan", help="Mã hoặc tên loại")
    number: str = field("Số chứng chỉ", "", max_length=80, audit=False, example="AT-000123", help="Dữ liệu cá nhân: chỉ người đó và nhân sự xem được")
    issued_on: Day | None = field("Ngày cấp", None, example="15/03/2025")
    expires_on: Day | None = field("Ngày hết hạn", None, example="14/03/2027", help="Ngày cuối còn hiệu lực. Trống ⇒ ngày cấp + thời hạn mặc định của loại")
    issuer: str = field("Nơi cấp", "", max_length=200, group="Thêm chi tiết")
    note: str = field("Ghi chú", "", max_length=500, group="Thêm chi tiết")


@m.command("record_credential", resource="hr.credentials", title="Ghi chứng chỉ của nhân sự", importable=True, agent="propose", min_scope="all")
async def record_credential(ctx: Ctx, inp: RecordCredential) -> dict[str, Any]:
    """Một chứng chỉ của một người (form hoặc một dòng Excel — nhập cả bảng qua Nhập dữ liệu, kể cả lớp nạp hệ cũ ``historical=True``: dòng nạp là
    tham chiếu, không nhắc tới khi xác nhận). Nhập lại cùng dòng không ghi hai lần. Gia hạn = ghi bản mới; bản cũ tự thành "đã có bản mới"."""
    _need_all(ctx)
    ctx.require("hr.credential_private", "write")
    kind = await _kind(ctx, inp.kind_key)
    party = await _person(ctx, inp.person)
    who = await ctx.call_system("parties.resolve_for_payment", party_id=party)
    if who["kind"] != "person":
        raise Invalid("Chứng chỉ chỉ ghi cho cá nhân")
    expires = inp.expires_on
    if expires is None and kind["expires"] and kind["validity_months"] and inp.issued_on:
        expires = plus_months(inp.issued_on, kind["validity_months"])
    if inp.issued_on and expires and expires < inp.issued_on:
        raise Invalid("Ngày hết hạn trước ngày cấp")
    if inp.issued_on and inp.issued_on > today():
        raise Invalid("Ngày cấp ở tương lai")
    number = inp.number.strip()
    same = await ctx.val("""SELECT c.id FROM hr.credentials c JOIN hr.credential_private pv ON pv.tenant_id = c.tenant_id AND pv.credential_id = c.id
                            WHERE c.party_id = %s AND c.kind_id = %s AND c.revoked_at IS NULL AND c.issued_on IS NOT DISTINCT FROM %s
                            AND c.expires_on IS NOT DISTINCT FROM %s AND pv.number = %s LIMIT 1""", (party, kind["id"], inp.issued_on, expires, number))
    label = f"{who['display_name']}: {kind['name']}" + (f", hết hạn {expires:%d/%m/%Y}" if expires else "")
    if same:
        return {"credential_id": same, "replayed": True, "summary": f"{label} (đã có)"}
    team = await _team_of(ctx, party, who["user_id"])
    cid = await ctx.val("""INSERT INTO hr.credentials (party_id, user_id, team_id, kind_id, issued_on, expires_on, issuer, note, loaded_at, created_by)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() END, core.me()) RETURNING id""",
                        (party, who["user_id"], team, kind["id"], inp.issued_on, expires, inp.issuer.strip(), inp.note.strip(), ctx.historical))
    await ctx.exec("INSERT INTO hr.credential_private (credential_id, user_id, number) VALUES (%s, %s, %s)", (cid, who["user_id"], number))
    return {"credential_id": cid, "replayed": False, "summary": label + (" — nạp từ hệ cũ, chờ xác nhận" if ctx.historical else "")}


class CredentialRef(Input):
    credential_id: UUID = field("Chứng chỉ", hidden=True)


class Revoke(CredentialRef):
    reason: str = field("Lý do", max_length=300, example="Ghi nhầm loại / cơ quan cấp đã thu hồi")


@m.command("revoke_credential", resource="hr.credentials", title="Thu hồi chứng chỉ", agent="propose", min_scope="all")
async def revoke_credential(ctx: Ctx, inp: Revoke) -> dict[str, Any]:
    """Ghi nhầm hoặc chứng chỉ bị thu hồi: đánh dấu một lần (giữ dòng làm lịch sử). Sửa = thu hồi rồi ghi lại."""
    _need_all(ctx)
    if not inp.reason.strip():
        raise Invalid("Cần lý do thu hồi")
    n = await ctx.exec("""UPDATE hr.credentials SET revoked_at = now(), revoked_by = core.me(), revoke_reason = %s
                          WHERE id = %s AND revoked_at IS NULL""", (inp.reason.strip(), inp.credential_id))
    if not n:
        raise NotFound("Không thấy chứng chỉ còn hiệu lực")
    return {"credential_id": inp.credential_id, "summary": "Đã thu hồi chứng chỉ"}


class Verify(Input):
    credential_id: UUID | None = field("Chứng chỉ", None, hidden=True)
    kind_key: str = field("Loại chứng chỉ", "", options_query="hr.credential_kind_options", help="Trống = mọi loại")


@m.command("verify_credentials", resource="hr.credentials", action="manage", title="Xác nhận chứng chỉ nạp từ hệ cũ", agent="propose", min_scope="all")
async def verify_credentials(ctx: Ctx, inp: Verify) -> dict[str, Any]:
    """Dòng nạp từ hệ cũ là tham chiếu (0185): người phụ trách đã đối chiếu thì xác nhận — từ hôm nay chứng chỉ đó vào vòng nhắc; mốc đã qua (vd đã hết
    hạn theo hồ sơ cũ) vẫn không nhắc. Một dòng (nút trên dòng) hoặc mọi dòng chưa xác nhận của một loại / mọi loại."""
    _need_all(ctx)
    kind = (await _kind(ctx, inp.kind_key))["id"] if inp.kind_key.strip() else None
    n = await ctx.exec("""UPDATE hr.credentials SET verified_on = core.today(), verified_by = core.me()
                          WHERE loaded_at IS NOT NULL AND verified_on IS NULL AND revoked_at IS NULL
                          AND (%s::uuid IS NULL OR id = %s) AND (%s::uuid IS NULL OR kind_id = %s)""",
                       (inp.credential_id, inp.credential_id, kind, kind))
    if inp.credential_id and not n:
        raise NotFound("Không thấy chứng chỉ nạp từ hệ cũ chưa xác nhận")
    return {"verified": n, "summary": f"Đã xác nhận {n} chứng chỉ nạp từ hệ cũ"}


class Attach(CredentialRef):
    filename: str = field("Tên file", max_length=300, hidden=True)
    content_type: str = field("Loại file", max_length=120, hidden=True)
    data_b64: str = field("Nội dung (base64)", max_length=14_000_000, audit=False, hidden=True)   # app đọc file được chọn — không ai gõ


@m.command("attach_credential_file",
           purpose="Lưu bản scan hoặc ảnh chứng chỉ vào hồ sơ để có bằng chứng kèm theo.", reversible="Tải lại bản scan đúng (bản cũ chờ vòng dọn theo thời hạn lưu của công ty)",
           resource="hr.credential_private", title="Lưu bản scan chứng chỉ", agent="deny")
async def attach_credential_file(ctx: Ctx, inp: Attach) -> dict[str, Any]:
    """Bản scan / ảnh chụp (PDF, PNG, JPEG ≤ 10 MB) vào kho tệp (0122); thay bản thì bản cũ chờ vòng dọn theo thời hạn lưu của công ty."""
    _need_all(ctx)
    try:
        data = base64.b64decode(inp.data_b64, validate=True)
    except ValueError as e:
        raise Invalid("Nội dung file không đọc được") from e
    magic = FILE_TYPES.get(inp.content_type.strip().lower())
    if magic is None or not data.startswith(magic):
        raise Invalid("Chỉ nhận PDF, PNG hoặc JPEG")
    if len(data) > FILE_MAX:
        raise Invalid("File quá 10 MB")
    old = await ctx.one("""SELECT pv.file_id FROM hr.credential_private pv JOIN hr.credentials c ON c.tenant_id = pv.tenant_id AND c.id = pv.credential_id
                           WHERE pv.credential_id = %s AND c.revoked_at IS NULL""", (inp.credential_id,))
    if old is None:
        raise NotFound("Không thấy chứng chỉ còn hiệu lực")
    stored = await files.put(ctx, data, inp.filename, inp.content_type.strip().lower(), max_bytes=FILE_MAX)   # kho trước, dòng sau (0122 §3)
    await ctx.exec("UPDATE hr.credential_private SET file_id = %s, file_name = %s, file_type = %s WHERE credential_id = %s",
                   (stored.id, (inp.filename.strip() or "ban-scan")[:300], inp.content_type.strip().lower(), inp.credential_id))
    if old["file_id"] is not None:
        await files.release(ctx, old["file_id"])
    return {"credential_id": inp.credential_id, "bytes": len(data), "summary": "Đã lưu bản scan"}


@m.query("credential_file", resource="hr.credential_private", title="Bản scan chứng chỉ", agent="deny")
async def credential_file(ctx: Ctx, inp: CredentialRef) -> dict[str, Any]:
    """RLS bảng riêng: chính người đó và nhân sự cả công ty mở được; trưởng đội không thấy dòng ⇒ không mở được."""
    f = await ctx.one("SELECT file_id, file_name, file_type FROM hr.credential_private WHERE credential_id = %s AND file_id IS NOT NULL", (inp.credential_id,))
    if f is None:
        raise NotFound("Không có bản scan (hoặc anh/chị không được xem)")
    data = await files.read(ctx, f["file_id"])
    return {"filename": f["file_name"], "content_type": f["file_type"], "inline": True, "data_b64": base64.b64encode(data).decode()}


# ── Đọc: của tôi / của đội / cả công ty, và công cụ trợ lý (không số) ──

async def _rows(ctx: Ctx, *, private: bool, mine: bool = False) -> list[dict[str, Any]]:
    query = (_WITH_PRIVATE if private else _WITHOUT_PRIVATE) + (" WHERE c.user_id = core.me()" if mine else "") + " ORDER BY c.expires_on NULLS LAST LIMIT 5000"
    rows = await ctx.all(query, {"d": today()})
    names = await _names(ctx, list({r["party_id"] for r in rows}))
    teams = {str(r["id"]): r["name"] for r in await ctx.all("SELECT id, name FROM core.teams WHERE id = ANY(%s)", (list({r["team_id"] for r in rows if r["team_id"]}),))} if rows else {}
    for r in rows:
        r["name"] = names.get(str(r["party_id"]), {}).get("name", "")
        r["team"] = teams.get(str(r["team_id"]), "") if r["team_id"] else ""
        r["loaded"] = "yes" if r.pop("unverified") else "no"
        if private:
            r["file"] = "yes" if r.pop("has_file") else "no"
    return rows


async def _missing(ctx: Ctx, kind_key: str = "") -> list[dict[str, Any]]:
    """Người giữ vai / thuộc đội (cả cây con) mà loại chứng chỉ yêu cầu nhưng chưa có dòng nào còn sống — trong phạm vi người xem (cả công ty / đội tôi
    quản / chính tôi). Chỉ người có tài khoản: yêu cầu khai theo vai / đội, mà vai / đội là của tài khoản."""
    rows = await ctx.all(
        """WITH RECURSIVE kinds AS (SELECT id, key, name, required_roles, required_teams FROM hr.credential_kinds
                                    WHERE active AND (cardinality(required_roles) > 0 OR cardinality(required_teams) > 0) AND (%(k)s = '' OR key = %(k)s)),
           tree AS (SELECT k.id AS kind_id, t.id AS team_id FROM kinds k JOIN core.teams t ON t.id = ANY(k.required_teams)
                    UNION SELECT tree.kind_id, ch.id FROM core.teams ch JOIN tree ON ch.parent_id = tree.team_id),
           need AS (SELECT k.id AS kind_id, mb.user_id FROM kinds k JOIN core.memberships mb ON mb.active AND mb.role = ANY(k.required_roles)
                    UNION SELECT tree.kind_id, tm.user_id FROM tree JOIN core.team_members tm ON tm.team_id = tree.team_id
                          JOIN core.memberships mb ON mb.user_id = tm.user_id AND mb.active)
           SELECT n.kind_id, n.user_id, u.name, k.key AS kind_key, k.name AS kind_name FROM need n JOIN kinds k ON k.id = n.kind_id JOIN core.users u ON u.id = n.user_id
           WHERE NOT EXISTS (SELECT 1 FROM hr.credentials c WHERE c.kind_id = n.kind_id AND c.user_id = n.user_id AND c.revoked_at IS NULL)
             AND (core.scope('hr.credentials') = 'all' OR n.user_id = core.me()
                  OR EXISTS (SELECT 1 FROM core.team_members tm2 WHERE tm2.user_id = n.user_id AND tm2.team_id = ANY(
                             CASE core.scope('hr.credentials') WHEN 'team' THEN core.my_teams() WHEN 'unit' THEN core.my_units() ELSE '{}'::uuid[] END)))
           ORDER BY k.name, u.name LIMIT 5000""", {"k": kind_key.strip().lower()})
    return [{"id": None, "name": r["name"], "kind_key": r["kind_key"], "kind_name": r["kind_name"], "status": "missing", "team": "", "issued_on": None,
             "expires_on": None, "days_left": None, "issuer": "", "note": "", "loaded": "no", "user_id": r["user_id"]} for r in rows]


def _counts(rows: list[dict[str, Any]], missing: int) -> list[dict[str, Any]]:
    live = [r for r in rows if r["status"] in LIVE]
    return [ui.kpi("Sắp hết hạn", sum(1 for r in live if r["status"] == "expiring"), tone="orange"),
            ui.kpi("Đã hết hạn", sum(1 for r in live if r["status"] == "expired"), tone="red"),
            ui.kpi("Thiếu", missing, tone="red"), ui.kpi("Chưa xác nhận (nạp từ hệ cũ)", sum(1 for r in live if r["loaded"] == "yes"))]


def _pick(rows: list[dict[str, Any]], show: str, within: int) -> list[dict[str, Any]]:
    if show == "history":
        return rows
    live = [r for r in rows if r["status"] in LIVE]
    if show == "expiring":
        return [r for r in live if r["days_left"] is not None and 0 <= r["days_left"] <= within]
    if show == "expired":
        return [r for r in live if r["status"] == "expired"]
    if show == "unverified":
        return [r for r in live if r["loaded"] == "yes"]
    return live


COLUMNS = [ui.col("name", "Người", width=200), ui.col("kind_name", "Loại"), ui.col("team", "Đội"),
           ui.col("status", "Trạng thái", "badge", labels=STATUS, tones=TONES), ui.col("expires_on", "Hết hạn", "date"),
           ui.col("days_left", "Còn (ngày)", "number"), ui.col("issued_on", "Ngày cấp", "date"), ui.col("issuer", "Nơi cấp")]


class CredentialFilter(Input):
    show: Literal["current", "expiring", "expired", "missing", "unverified", "history"] = field("Xem", "current", options=SHOW)
    within_days: int = field("Sắp hết hạn trong (ngày)", 60, ge=1, le=730)
    kind_key: str = field("Loại", "", options_query="hr.credential_kind_options", placeholder="Mọi loại")
    team_code: str = field("Đội", "", options_query="admin.team_options", placeholder="Mọi đội", help="Gồm cả đội con")


async def _filter(ctx: Ctx, rows: list[dict[str, Any]], inp: CredentialFilter) -> list[dict[str, Any]]:
    if inp.kind_key.strip():
        rows = [r for r in rows if r["kind_key"] == inp.kind_key.strip().lower()]
    if inp.team_code.strip():
        tree = {r["id"] for r in await ctx.all("""WITH RECURSIVE t AS (SELECT id FROM core.teams WHERE code = %s
                                                  UNION SELECT ch.id FROM core.teams ch JOIN t ON ch.parent_id = t.id) SELECT id FROM t""", (inp.team_code.strip(),))}
        rows = [r for r in rows if r.get("team_id") in tree]
    return rows


@m.query("credentials", resource="hr.credentials", title="Chứng chỉ nhân sự", min_scope="team", agent="deny")
async def credentials(ctx: Ctx, inp: CredentialFilter) -> dict[str, Any]:
    """Cả công ty (nhân sự) hoặc đội tôi quản (trưởng đội — RLS): đang dùng / sắp hết hạn trong N ngày / đã hết hạn / thiếu theo vai-đội phải có /
    nạp từ hệ cũ chưa xác nhận / lịch sử. Số chứng chỉ chỉ hiện với người được xem dữ liệu cá nhân; trợ lý dùng ``credential_watch`` (không số)."""
    private = ctx.p.can("hr.credential_private", "read") and ctx.p.has_scope("hr.credential_private", "all")
    rows = await _rows(ctx, private=private)
    missing = await _missing(ctx, inp.kind_key)
    if inp.team_code.strip():
        missing = []   # thiếu theo người chưa gắn đội của chứng chỉ — lọc đội thì xem ở màn của đội
    shown = missing if inp.show == "missing" else await _filter(ctx, _pick(rows, inp.show, inp.within_days), inp)
    cols = COLUMNS + ([ui.col("number", "Số chứng chỉ"), ui.col("file", "Bản scan", "badge", labels={"yes": "Có", "no": "—"})] if private else []) + [
        ui.col("loaded", "Nạp từ hệ cũ", "badge", labels={"yes": "Chưa xác nhận", "no": "—"}, tones={"yes": "orange"})]
    return ui.table(shown, cols, title=f"Chứng chỉ nhân sự — {SHOW[inp.show].lower()}", kpis=_counts(rows, len(missing)), limit=5000,
                    empty="Không có chứng chỉ nào ở mục này",
                    note="Hết hạn theo hồ sơ không chặn việc gì. Dòng nạp từ hệ cũ không được nhắc tới khi xác nhận.")


@m.query("my_credentials", resource="hr.credentials", title="Chứng chỉ của tôi", agent="deny")
async def my_credentials(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await _rows(ctx, private=ctx.p.can("hr.credential_private", "read"), mine=True)
    missing = [r for r in await _missing(ctx) if r["user_id"] == ctx.p.user_id]
    shown = [r for r in rows if r["status"] not in ("replaced", "revoked")] + missing
    cols = [c for c in COLUMNS if c["key"] not in ("name", "team")] + [ui.col("number", "Số chứng chỉ"),
                                                                       ui.col("file", "Bản scan", "badge", labels={"yes": "Có", "no": "—"})]
    return ui.table(shown, cols, title="Chứng chỉ của tôi", kpis=_counts(rows, len(missing))[:3], limit=5000,
                    empty="Chưa có chứng chỉ nào được ghi cho anh/chị",
                    note="Gia hạn xong, gửi bản mới cho phụ trách nhân sự để cập nhật.")


class Watch(Input):
    show: Literal["expiring", "expired", "missing"] = field("Xem", "expiring", options={k: SHOW[k] for k in ("expiring", "expired", "missing")})
    kind_key: str = field("Loại", "", options_query="hr.credential_kind_options", placeholder="Mọi loại")
    within_days: int = field("Sắp hết hạn trong (ngày)", 60, ge=1, le=730)


@m.query("credential_watch", resource="hr.credentials", title="Ai thiếu / sắp hết hạn / đã hết hạn chứng chỉ", agent="allow")
async def credential_watch(ctx: Ctx, inp: Watch) -> dict[str, Any]:
    """Công cụ của trợ lý (và màn): KHÔNG có số chứng chỉ, không bản scan — tên, loại, đội, trạng thái, ngày hết hạn. Phạm vi = phạm vi người hỏi (RLS)."""
    rows = await _rows(ctx, private=False)
    if inp.show == "missing":
        shown = await _missing(ctx, inp.kind_key)
    else:
        shown = _pick(rows, inp.show, inp.within_days)
        if inp.kind_key.strip():
            shown = [r for r in shown if r["kind_key"] == inp.kind_key.strip().lower()]
    for r in shown:
        for k in ("user_id", "party_id", "team_id"):
            r.pop(k, None)
    return ui.table(shown, COLUMNS, title=f"Chứng chỉ — {SHOW[inp.show].lower()}", limit=5000, empty="Không có ai ở mục này")


# ── Nhắc hạn (vòng quét) ──

TITLE = {"expiring": "Chứng chỉ sắp hết hạn", "expired": "Chứng chỉ đã hết hạn"}
HINT = {"expiring": "Gia hạn rồi gửi bản mới cho phụ trách nhân sự để cập nhật hồ sơ.",
        "expired": "Hết hạn theo hồ sơ — nếu đã gia hạn, gửi bản mới cho phụ trách nhân sự."}


@dataclass
class Digest:
    """Người nhận × mốc → các chứng chỉ (mã, nhãn, của chính người nhận?). Gửi một tin mỗi nhóm cuối lượt quét (gộp như 0185)."""
    items: dict[tuple[UUID, str], list[tuple[str, str, bool]]] = dc_field(default_factory=dict)

    def add(self, to: list[UUID], milestone: str, cid: str, own: str, other: str, holder: UUID | None) -> None:
        for u in to:
            self.items.setdefault((u, milestone), []).append((cid, own if u == holder else other, u == holder))

    async def send(self, ctx: Ctx) -> int:
        n = 0
        for (user, ms), rows in self.items.items():
            labels = [r[1] for r in rows]
            more = f" +{len(labels) - DIGEST_NAMES}" if len(labels) > DIGEST_NAMES else ""
            title = f"{TITLE[ms]}: {labels[0]}" if len(rows) == 1 else f"{TITLE[ms]}: {len(rows)} chứng chỉ"
            body = (f"{', '.join(labels[:DIGEST_NAMES])}{more}. " if len(rows) > 1 else "") + HINT[ms]
            mine = all(r[2] for r in rows)
            key = hashlib.sha1(",".join(sorted(r[0] for r in rows)).encode()).hexdigest()[:16]
            n += await ctx.notify([user], kind="hr_credential", title=title, body=body, push=f"{TITLE[ms]}: {len(rows)} chứng chỉ",
                                  open={"query": "hr.my_credentials", "input": {}} if mine else {"query": "hr.credentials", "input": {"show": ms}},
                                  key=f"hr.credentials:{ms}:{key}")
        return n


async def _recipients(ctx: Ctx, rows: list[dict[str, Any]]) -> dict[str, list[UUID]]:
    """Mỗi chứng chỉ: chính người đó (tài khoản đang hoạt động) + quản lý trực tiếp ở hồ sơ làm việc, không có thì trưởng đội gần nhất của đội."""
    d = today()
    parties = list({r["party_id"] for r in rows})
    mgr = {str(r["party_id"]): r["manager_party_id"] for r in await ctx.all(
        """SELECT DISTINCT ON (party_id) party_id, manager_party_id FROM hr.assignments WHERE party_id = ANY(%s) AND manager_party_id IS NOT NULL
           AND valid_from <= %s AND (valid_to IS NULL OR valid_to >= %s) ORDER BY party_id, valid_from DESC""", (parties, d, d))}
    logins = await ctx.call_system("parties.logins", party_ids=list(set(mgr.values()))) if mgr else {}
    users = {r["user_id"] for r in rows if r["user_id"]} | {UUID(u) for u in logins.values()}
    active = {r["user_id"] for r in await ctx.all("SELECT user_id FROM core.memberships WHERE active AND user_id = ANY(%s)", (list(users),))} if users else set()
    leads: dict[str, list[UUID]] = {}
    for team in {r["team_id"] for r in rows if r["team_id"]}:
        found = await ctx.all(
            """WITH RECURSIVE up AS (SELECT id, parent_id, 0 AS depth FROM core.teams WHERE id = %s
                                     UNION ALL SELECT t.id, t.parent_id, up.depth + 1 FROM core.teams t JOIN up ON t.id = up.parent_id)
               SELECT tm.user_id, up.depth FROM up JOIN core.team_members tm ON tm.team_id = up.id AND tm.is_lead
               JOIN core.memberships mb ON mb.user_id = tm.user_id AND mb.active ORDER BY up.depth""", (team,))
        leads[str(team)] = [r["user_id"] for r in found]
    out: dict[str, list[UUID]] = {}
    for r in rows:
        to: list[UUID] = [r["user_id"]] if r["user_id"] in active else []
        boss = logins.get(str(mgr.get(str(r["party_id"])))) if mgr.get(str(r["party_id"])) else None
        if boss and UUID(boss) in active:
            to.append(UUID(boss))
        else:   # trưởng đội gần nhất KHÁC chính người đó (người đó là trưởng đội ⇒ lên tầng trên)
            cands = [u for u in leads.get(str(r["team_id"]), []) if u != r["user_id"]]
            to += cands[:1]
        out[str(r["id"])] = list(dict.fromkeys(to))
    return out


@m.command("remind_credentials", resource="hr.credentials", title="Nhắc chứng chỉ sắp / đã hết hạn", internal=True, agent="deny",
           sweep="SELECT DISTINCT c.tenant_id" + _DUE)
async def remind_credentials(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Một lượt của MỘT công ty: mốc tới hạn chưa xử lý (sắp hết hạn trong số ngày báo trước của loại / đã hết hạn) của chứng chỉ hiện hành ⇒ ghi sổ
    nhắc (khoá chính chặn lặp) rồi gửi bản gộp theo người nhận. Không có ai để báo vẫn ghi sổ (không quét lại mãi)."""
    if ctx.historical:
        return {"claimed": 0, "sent": 0}
    rows = await ctx.all("SELECT c.id, c.party_id, c.user_id, c.team_id, c.expires_on, k.name AS kind_name, " + _MILESTONE + " AS milestone"
                         + _DUE + " ORDER BY c.expires_on LIMIT 500")
    if not rows:
        return {"claimed": 0, "sent": 0}
    to = await _recipients(ctx, rows)
    claimed = {(str(r["credential_id"]), r["milestone"]) for r in await ctx.all(
        """INSERT INTO hr.credential_notices (credential_id, milestone, recipients)
           SELECT * FROM unnest(%s::uuid[], %s::text[], %s::int[]) ON CONFLICT DO NOTHING RETURNING credential_id, milestone""",
        ([r["id"] for r in rows], [r["milestone"] for r in rows], [len(to[str(r["id"])]) for r in rows]))}
    names = await _names(ctx, list({r["party_id"] for r in rows}))
    digest = Digest()
    for r in rows:
        if (str(r["id"]), r["milestone"]) not in claimed:
            continue
        when = f"{r['expires_on']:%d/%m/%Y}"
        own = f"{r['kind_name']} (hết hạn {when})"
        other = f"{names.get(str(r['party_id']), {}).get('name', 'Nhân sự')} — {r['kind_name']} ({when})"
        digest.add(to[str(r["id"])], r["milestone"], str(r["id"]), own, other, r["user_id"])
    return {"claimed": len(claimed), "sent": await digest.send(ctx)}


# ── Màn ──

def _say_mine(o: Any) -> Line | None:
    late, soon, miss = int(kpi(o, "Đã hết hạn")), int(kpi(o, "Sắp hết hạn")), int(kpi(o, "Thiếu"))
    if late or miss:
        return line("warn", f"{late + miss} chứng chỉ của anh/chị đã hết hạn / chưa có", "Gia hạn hoặc gửi bản mới cho phụ trách nhân sự")
    return line("accent", f"{soon} chứng chỉ của anh/chị sắp hết hạn", "Gia hạn trước ngày hết hạn") if soon else None


def _say_company(o: Any) -> Line | None:
    late, soon, miss = int(kpi(o, "Đã hết hạn")), int(kpi(o, "Sắp hết hạn")), int(kpi(o, "Thiếu"))
    if not (late or soon or miss):
        return None
    parts = [f"{x} {label}" for x, label in ((late, "đã hết hạn"), (soon, "sắp hết hạn"), (miss, "thiếu")) if x]
    return line("warn" if late or miss else "accent", f"Chứng chỉ nhân sự: {', '.join(parts)}", "Theo hồ sơ — không chặn việc gì",
                ask="Ai sắp hết hạn chứng chỉ trong 60 ngày tới?")


_ROW_ACTIONS = (A("hr.verify_credentials", "Xác nhận", bind={"credential_id": "id"}, when={"loaded": ["yes"], "status": LIVE}, style="primary"),
                A("hr.revoke_credential", "Thu hồi", bind={"credential_id": "id"}, when={"status": LIVE}, style="destructive"))

m.workspace(Workspace("hr", "Nhân sự", "person.text.rectangle.fill", (
    Screen("hr_my_credentials", "Chứng chỉ của tôi", "checkmark.seal", "hr.my_credentials", tint="blue",
           desk=Desk("watch", say=_say_mine, menu=True, rank=70)),
    Screen("hr_team_credentials", "Chứng chỉ của đội", "person.2.badge.gearshape", "hr.credentials", tint="orange", min_scope="team", max_scope="team"),
    Screen("hr_credentials", "Chứng chỉ nhân sự", "checkmark.seal.fill", "hr.credentials", tint="indigo", min_scope="all",
           actions=(A("hr.record_credential", style="primary"), A("hr.verify_credentials")), row_actions=_ROW_ACTIONS,
           desk=Desk("watch", say=_say_company, menu=True, rank=70)),
    Screen("hr_credential_kinds", "Loại chứng chỉ", "list.bullet.rectangle", "hr.credential_kinds", min_scope="all", pinnable=False,
           actions=(A("hr.save_credential_kind", style="primary"),),
           row_actions=(A("hr.save_credential_kind", "Sửa", prefill={"key": "key", "name": "name", "issuer_hint": "issuer_hint", "expires": "expires",
                                                                     "validity_months": "validity_months", "remind_days": "remind_days",
                                                                     "required_roles": "required_roles", "required_teams": "required_teams",
                                                                     "active": "active"}),)),
), order=46))
