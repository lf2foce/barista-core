"""Nghỉ việc ⇒ credential của người đó chết HẲN, không chỉ "hết quyền".

Quyền đã hết hiệu lực ngay khi membership bị khoá (``auth.load`` lọc ``active``, MCP nạp lại mỗi request) — nên đây không phải lỗ hổng.
Nhưng bản ghi còn sống thì: mở lại tài khoản là token cũ dùng lại được mà không ai duyệt lại; màn "ứng dụng đã kết nối" báo sai; kiểm toán
thấy credential trông như đang cấp. Vì vậy ``admin.member_deactivated`` phải thu hồi một chiều cả phiên đăng nhập lẫn token MCP.
"""

import hashlib
import secrets
import uuid
from typing import Any

from barista.kernel import events
from barista.kernel.principal import Principal
from tests.conftest import Who, ok, owner_val


async def _credential(user: Principal, tenant_id: uuid.UUID | None = None) -> str:
    """Một access token MCP (của ``tenant_id``, mặc định công ty của người đó) + một phiên đăng nhập — như thể người đó đã đồng ý cấp trên web (0093)."""
    client = f"test-{uuid.uuid4().hex[:8]}"
    await owner_val("INSERT INTO core.mcp_clients (client_id, info) VALUES (%s, '{}') RETURNING client_id", (client,))
    token = secrets.token_urlsafe(32)
    await owner_val("""INSERT INTO core.mcp_tokens (kind, token_hash, client_id, user_id, tenant_id, expires_at)
                       VALUES ('access', %s, %s, %s, %s, now() + interval '1 hour') RETURNING id""",
                    (hashlib.sha256(token.encode()).hexdigest(), client, user.user_id, tenant_id or user.tenant_id))
    await owner_val("""INSERT INTO core.sessions (token_hash, user_id, provider, expires_at)
                       VALUES (%s, %s, 'google', now() + interval '1 hour') RETURNING id""",
                    (hashlib.sha256(secrets.token_urlsafe(16).encode()).hexdigest(), user.user_id))
    return token


async def _alive(user: Principal) -> tuple[int, int]:
    """(token MCP còn hiệu lực, phiên còn hiệu lực) — đếm bằng kết nối owner, không phụ thuộc RLS của chính người đó."""
    tok = await owner_val("SELECT count(*) FROM core.mcp_tokens WHERE user_id = %s AND revoked_at IS NULL", (user.user_id,))
    ses = await owner_val("SELECT count(*) FROM core.sessions WHERE user_id = %s AND revoked_at IS NULL", (user.user_id,))
    return int(tok or 0), int(ses or 0)


async def _usable(token: str) -> bool:
    """Token còn dùng được không — đúng điều kiện ``oauth._row`` dùng khi nạp token."""
    got: Any = await owner_val("""SELECT count(*) FROM core.mcp_tokens WHERE kind = 'access' AND token_hash = %s
                                  AND revoked_at IS NULL AND expires_at > now()""", (hashlib.sha256(token.encode()).hexdigest(),))
    return int(got or 0) == 1


async def test_offboarding_revokes_sessions_and_ai_app_tokens_for_good(who: Who) -> None:
    admin, sale = await who("admin"), await who("sale1")
    token = await _credential(sale)
    tok, ses = await _alive(sale)
    assert tok >= 1 and ses >= 1 and await _usable(token)             # ≥ vì các test khác có thể để lại credential của cùng người trên DB dùng chung

    await ok(admin, "admin.update_member", email=sale.email, active=False)      # nghỉ việc: chạy thẳng (không phải phiếu đổi quyền)
    await events.drain()                                                        # bên nghe chạy sau commit (production: vòng nền)

    assert await _alive(sale) == (0, 0)                                        # phiên + token đều bị thu hồi
    assert not await _usable(token)                                            # token cũ không còn dùng được

    await ok(admin, "admin.update_member", email=sale.email, active=True)       # mở lại tài khoản…
    await events.drain()
    assert not await _usable(token)                                            # …KHÔNG làm token cũ sống lại: phải đồng ý lại trên web


async def _sessions_alive(user: Principal) -> int:
    return int(await owner_val("SELECT count(*) FROM core.sessions WHERE user_id = %s AND revoked_at IS NULL", (user.user_id,)) or 0)


async def test_offboarding_in_one_company_does_not_log_the_person_out_of_another(who: Who) -> None:
    """Một người thuộc hai công ty: khoá ở A thu hồi token MCP của A, KHÔNG đụng token của B và KHÔNG đăng xuất phiên (phiên gắn người, không gắn công ty —
    ``auth.load`` đã từ chối thành viên bị khoá ở A từng request). Admin công ty này không được làm hỏng đăng nhập của người đó ở công ty khác."""
    admin, sale = await who("admin"), await who("sale1")
    other = uuid.uuid4()
    await owner_val("INSERT INTO core.tenants (id, name) VALUES (%s, 'Công ty khác của cùng một người') RETURNING id", (other,))
    await owner_val("INSERT INTO core.roles (tenant_id, key, label) VALUES (%s, 'staff', 'Nhân viên') RETURNING key", (other,))
    await owner_val("INSERT INTO core.memberships (tenant_id, user_id, role) VALUES (%s, %s, 'staff') RETURNING 1", (other, sale.user_id))
    try:
        here, there = await _credential(sale), await _credential(sale, other)
        sessions = await _sessions_alive(sale)            # so theo mức trước đó, không theo số tuyệt đối (DB dùng chung với test khác)
        await ok(admin, "admin.update_member", email=sale.email, active=False)
        await events.drain()
        assert not await _usable(here)                    # token của công ty bị khoá: chết
        assert await _usable(there)                       # token của công ty kia: còn nguyên
        assert await _sessions_alive(sale) == sessions    # phiên đăng nhập: không bị đụng (người đó vẫn là thành viên ở công ty kia)
    finally:
        await owner_val("DELETE FROM core.tenants WHERE id = %s RETURNING 1", (other,))   # kéo theo vai, thành viên, token của công ty đó
        await owner_val("DELETE FROM core.sessions WHERE user_id = %s RETURNING 1", (sale.user_id,))
        await ok(admin, "admin.update_member", email=sale.email, active=True)
        await events.drain()

