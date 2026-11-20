"""Kiểm id_token của Google (OIDC) tại core — không qua dịch vụ trung gian (quyết định 0040).

Kiểm: chữ ký (khoá công khai của Google, có cache), ``aud`` thuộc danh sách client ID của hệ thống (web / iOS / macOS), ``iss`` là Google,
``exp`` / ``iat`` (lệch đồng hồ tối đa 60 giây), ``email_verified``, ``nonce`` khớp (chống dùng lại token lấy được ở chỗ khác), và nếu
cấu hình ``google_hosted_domains`` thì ``hd`` phải thuộc danh sách đó (chỉ áp được khi công ty dùng Google Workspace).
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jwt

from barista.config import settings

GOOGLE_JWKS = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

_jwks = jwt.PyJWKClient(GOOGLE_JWKS, cache_keys=True, lifespan=3600)


def _google_key(token: str) -> Any:
    return _jwks.get_signing_key_from_jwt(token).key


# Test thay hàm này bằng khoá tự sinh — đường kiểm còn lại giữ nguyên.
key_resolver: Callable[[str], Any] = _google_key


class OIDCError(Exception):
    """Token không hợp lệ — báo chung chung cho người dùng, chi tiết vào log."""


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    name: str
    hosted_domain: str


async def verify_google(id_token: str, nonce: str) -> GoogleIdentity:
    audiences = [*settings.google_client_ids, *([settings.google_web_client_id] if settings.google_web_client_id else [])]
    if not audiences:
        raise OIDCError("Chưa cấu hình đăng nhập Google (BARISTA_GOOGLE_CLIENT_IDS)")
    if not nonce:
        raise OIDCError("Thiếu nonce")
    try:
        key = await asyncio.to_thread(key_resolver, id_token)
        claims: dict[str, Any] = jwt.decode(id_token, key, algorithms=["RS256"], audience=audiences, leeway=60,
                                            options={"require": ["exp", "iat", "iss", "aud", "sub"]})
    except jwt.PyJWTError as e:
        raise OIDCError(f"id_token không hợp lệ: {e}") from e
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise OIDCError("id_token không do Google cấp")
    if claims.get("nonce") != nonce:
        raise OIDCError("nonce không khớp")
    if claims.get("email_verified") is not True or not claims.get("email"):
        raise OIDCError("Email Google chưa được xác minh")
    hd = str(claims.get("hd") or "")
    if settings.google_hosted_domains and hd not in settings.google_hosted_domains:
        raise OIDCError("Tài khoản không thuộc tên miền công ty")
    return GoogleIdentity(subject=str(claims["sub"]), email=str(claims["email"]).strip().lower(), name=str(claims.get("name") or ""),
                          hosted_domain=hd)
