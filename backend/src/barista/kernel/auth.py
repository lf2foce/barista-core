"""Nạp ``Principal`` từ DB: thành viên đang hoạt động + vai trò + ma trận quyền của vai trò + cây đội mình quản.

Dùng được với kết nối owner (lúc đăng nhập, chưa có biến phiên) lẫn kết nối trong phiên công ty (chạy lệnh sau duyệt
bằng quyền người xin — nạp LẠI để quyền bị thu hồi trong lúc chờ duyệt có hiệu lực).
"""

from typing import Any
from uuid import UUID

from psycopg import AsyncClientCursor

from barista.db import Conn
from barista.kernel import company_settings, extra_fields, packs, teams, vocab
from barista.kernel.clock import today, using
from barista.kernel.principal import Grant, Principal, issued

GRANTS_SQL = "SELECT resource, scope, actions FROM core.grants WHERE tenant_id = %(t)s AND role = %(role)s"
BUDGET_SQL = """SELECT p.ai_enabled, p.ai_monthly_usd, coalesce(s.usd, 0) AS spent FROM core.tenant_profile p
                LEFT JOIN core.ai_spend s ON s.tenant_id = p.tenant_id AND s.month = %(m)s WHERE p.tenant_id = %(t)s"""
SETTINGS_SQL = "SELECT key, enabled FROM core.settings WHERE tenant_id = %(t)s"   # mọi cài đặt công ty đã có dòng (0128) — vài chục dòng là cùng
# Hạng trợ lý AI của người (ADR 0193): hạng gán cho vai, vai chưa gán ⇒ hạng mặc định của công ty; kèm TOKEN tháng này của CHÍNH người đó và model nhà vận
# hành đặt riêng. Công ty chưa có hạng nào (chưa cài mặc định mới) ⇒ không dòng: model mặc định của bản cài, không hạn mức theo người, KHÔNG giọng nói.
TIER_SQL = """SELECT t.key, t.label, t.model, t.choices, t.person_monthly_tokens AS cap, t.voice,
                     coalesce((SELECT s.tokens FROM core.ai_person_spend s WHERE s.tenant_id = t.tenant_id AND s.user_id = %(u)s AND s.month = %(m)s), 0) AS used,
                     (SELECT c.model FROM core.ai_choices c WHERE c.tenant_id = t.tenant_id AND c.user_id = %(u)s) AS chosen
              FROM core.ai_tiers t LEFT JOIN core.ai_role_tiers r ON r.tenant_id = t.tenant_id AND r.role = %(role)s
              WHERE t.tenant_id = %(t)s AND (t.key = r.tier OR (r.tier IS NULL AND t.is_default))"""


async def load(conn: Conn, tenant_id: UUID, *, email: str | None = None, user_id: UUID | None = None) -> Principal | None:
    cur = await conn.execute(
        """SELECT u.id, u.email, u.name, m.role, r.label AS role_label, t.timezone
           FROM core.memberships m
           JOIN core.tenants t ON t.id = m.tenant_id
           JOIN core.users u ON u.id = m.user_id
           JOIN core.roles r ON r.tenant_id = m.tenant_id AND r.key = m.role
           WHERE m.tenant_id = %(t)s AND m.active AND (u.email = %(email)s OR u.id = %(uid)s)""",
        {"t": tenant_id, "email": (email or "").strip().lower() or None, "uid": user_id})
    u = await cur.fetchone()
    if u is None:
        return None
    with using(u["timezone"]):   # tháng của hạn mức AI theo múi giờ công ty (0169) — nạp được cả bằng kết nối owner (ngoài phiên công ty)
        month = today().replace(day=1)
    # Sáu câu sau độc lập nhau ⇒ MỘT lượt mạng (một thông điệp nhiều câu, Postgres chạy lần lượt) thay vì sáu lượt nối tiếp — nạp quyền chạy ở MỖI
    # request (đo 12/11/2026). Không dùng pipeline: lượt bị huỷ giữa chừng (luồng SSE đóng) để lại máy phát dở của psycopg.
    cur = AsyncClientCursor(conn)
    await cur.execute("; ".join((GRANTS_SQL, teams.MEMBER_OF_SQL, teams.MANAGED_SQL, BUDGET_SQL, SETTINGS_SQL, TIER_SQL)),
                      {"t": tenant_id, "u": u["id"], "role": u["role"], "m": month})
    grants = {g["resource"]: Grant(g["scope"], frozenset(g["actions"])) for g in await cur.fetchall()}
    cur.nextset()
    member = tuple(r["team_id"] for r in await cur.fetchall())
    cur.nextset()
    managed = tuple(r["id"] for r in await cur.fetchall())
    cur.nextset()
    prof = await cur.fetchone()
    cur.nextset()
    settings = {r["key"]: bool(r["enabled"]) for r in await cur.fetchall()}
    cur.nextset()
    tier = await cur.fetchone()
    block = ai_block(prof["spent"], prof["ai_monthly_usd"]) if prof else ""
    return issued(Principal(tenant_id=tenant_id, user_id=u["id"], email=u["email"], name=u["name"], role=u["role"],
                     role_label=u["role_label"], grants=grants, home_team=teams.home_of(member),
                     member_teams=member, managed_teams=managed,
                     ai=prof["ai_enabled"] if prof else True,
                     ai_block=block or (person_block(tier["used"], tier["cap"]) if tier else ""),
                     ai_tier=tier["key"] if tier else "", ai_model=effective_model(tier), ai_voice=bool(tier and tier["voice"]),
                     ai_fallback=settings.get(company_settings.AI_FALLBACK_VENDOR, False),   # chưa có dòng ⇒ tắt (mặc định khai ở core_ops)
                     settings=settings,
                     packs=await packs.enabled(conn, tenant_id), vocab=await vocab.terms(conn, tenant_id),
                     fields=await extra_fields.load(conn, tenant_id)))


def effective_model(tier: dict[str, Any] | None) -> str:
    """Model người này dùng: model nhà vận hành đặt riêng (chỉ khi còn nằm trong danh sách của hạng) · model của hạng · "" (mặc định của bản cài)."""
    if tier is None:
        return ""
    if tier["chosen"] and tier["chosen"] in (tier["choices"] or []):
        return str(tier["chosen"])
    return str(tier["model"] or "")


PERSON_OUT = ("Bạn đã dùng hết lượt trợ lý AI của tháng này. Lượt mới mở lại vào ngày 1 tháng sau; cần thêm thì báo quản lý. "
              "Các thao tác không cần trợ lý vẫn dùng bình thường.")


def person_block(used: Any, cap: Any) -> str:
    """Hạn mức TOKEN tháng của MỘT NGƯỜI theo hạng (ADR 0193): đã dùng ≥ hạn mức ⇒ lời báo cho chính người đó (nói "lượt", không nhắc token / tiền /
    model / nhà cung cấp); chỉ chặn người đó, người khác trong công ty vẫn dùng. Sang tháng mới đếm lại từ 0."""
    if cap is None or used < cap:
        return ""
    return PERSON_OUT


def ai_block(spent: Any, cap: Any) -> str:
    """Hạn mức AI tháng (0092): đã chi ≥ trần ⇒ lời báo (trợ lý tạm dừng); không trần / còn hạn ⇒ ""."""
    if cap is None or spent < cap:
        return ""
    return ("Công ty đã dùng hết hạn mức trợ lý AI tháng này — trợ lý tạm dừng tới ngày 1 tháng sau hoặc khi nhà cung cấp dịch vụ mở thêm. "
            "Các thao tác không cần trợ lý vẫn dùng bình thường.")   # không nêu số tiền (0193): khách không thấy chi phí


async def dev_logins(conn: Conn) -> list[dict[str, Any]]:
    """Danh sách tài khoản để chọn vai khi dev (owner — xuyên công ty). Tắt khi ``dev_auth`` tắt. Thô: nhãn vai, đội nhà (không tính gốc công ty) + có là
    trưởng không — nhãn hiện trên nút do ``workspaces.picker_card`` ráp (kèm chức danh gói khai, nếu có)."""
    cur = await conn.execute(
        """SELECT t.id AS tenant_id, t.name AS tenant, t.created_at AS tenant_since, u.id AS user_id, u.email, u.name, r.label AS role, m.role AS role_key,
                  home.name AS team, home.is_lead
           FROM core.memberships m JOIN core.users u ON u.id = m.user_id JOIN core.tenants t ON t.id = m.tenant_id
           JOIN core.roles r ON r.tenant_id = m.tenant_id AND r.key = m.role
           LEFT JOIN LATERAL (SELECT tm.name, x.is_lead FROM core.team_members x JOIN core.teams tm ON tm.id = x.team_id
                              WHERE x.tenant_id = m.tenant_id AND x.user_id = m.user_id AND tm.parent_id IS NOT NULL
                              ORDER BY x.is_home DESC, x.is_lead DESC, tm.name LIMIT 1) home ON true
           WHERE m.active ORDER BY t.created_at, r.label, u.name""")
    return await cur.fetchall()
