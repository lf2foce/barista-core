"""Khai / sửa một ỨNG DỤNG FIRST-PARTY đăng nhập bằng tài khoản Barista (0205) — việc của NHÀ VẬN HÀNH bản cài, chạy bằng kết nối owner.

Client công khai + PKCE (không có client secret ⇒ DB không giữ bí mật), phạm vi DUY NHẤT ``identity``, địa chỉ quay về khớp TUYỆT ĐỐI danh sách khai
ở đây (không theo allowlist tên miền của ứng dụng AI). Đăng ký tự do qua ``/register`` không bao giờ có phạm vi này.

    cd backend && uv run python scripts/first_party_client.py --client-id suite-demo --name "Marketing Suite" \\
        --redirect https://suite.example.com/api/auth/barista/callback --redirect http://localhost:3001/api/auth/barista/callback
    … --remove      ⇒ gỡ client (token đã cấp hết hiệu lực)
    … --new-secret  ⇒ cấp CLIENT SECRET mới (lần khai đầu tự cấp). Secret in ra MỘT lần — đặt vào env của server ứng dụng; DB chỉ giữ mã băm.
                      Secret chỉ dùng cho đường đăng nhập ngay trên trang ứng dụng (POST /v1/oauth/google, 0205 §5b); /authorize + PKCE không cần.
"""
import argparse
import asyncio
import re
import secrets

from mcp.shared.auth import OAuthClientInformationFull
from psycopg.types.json import Jsonb

from barista.config import settings
from barista.db import db
from barista.kernel import oauth


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--redirect", action="append", default=[])
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--new-secret", action="store_true")
    a = ap.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,62}", a.client_id):
        raise SystemExit("client-id: chữ thường, số, gạch ngang (3–63)")
    await db.open(settings.database_url, settings.owner_database_url)
    try:
        async with db.owner_tx() as c:
            if a.remove:
                await c.execute("UPDATE core.mcp_tokens SET revoked_at = now() WHERE client_id = %s AND revoked_at IS NULL", (a.client_id,))
                await c.execute("DELETE FROM core.mcp_clients WHERE client_id = %s", (a.client_id,))
                print(f"đã gỡ {a.client_id}")
                return
            if not a.name or not a.redirect:
                raise SystemExit("cần --name và ít nhất một --redirect")
            for r in a.redirect:
                if not (r.startswith("https://") or r.startswith("http://localhost:") or r.startswith("http://127.0.0.1:")):
                    raise SystemExit(f"địa chỉ quay về phải là https (hoặc localhost khi thử): {r}")
            info = OAuthClientInformationFull(client_id=a.client_id, client_name=a.name, redirect_uris=a.redirect,
                                              grant_types=["authorization_code", "refresh_token"], response_types=["code"],
                                              token_endpoint_auth_method="none", scope=oauth.IDENTITY)
            cur = await c.execute("SELECT info->>%s AS h FROM core.mcp_clients WHERE client_id = %s", (oauth.SECRET_KEY, a.client_id))
            row = await cur.fetchone()
            secret = secrets.token_urlsafe(32) if (a.new_secret or not (row and row["h"])) else None
            data = {**info.model_dump(mode="json"), oauth.SECRET_KEY: oauth.hash_secret(secret) if secret else row["h"]}   # type: ignore[index]
            await c.execute("""INSERT INTO core.mcp_clients (client_id, info) VALUES (%s, %s)
                               ON CONFLICT (client_id) DO UPDATE SET info = EXCLUDED.info""", (a.client_id, Jsonb(data)))
            print(f"đã khai {a.client_id} ({a.name}) · phạm vi {oauth.IDENTITY} · quay về: {', '.join(a.redirect)}")
            if secret:
                print(f"CLIENT SECRET (chỉ hiện lần này — đặt vào BARISTA_CLIENT_SECRET của server ứng dụng): {secret}")
    finally:
        await db.close()


asyncio.run(main())
