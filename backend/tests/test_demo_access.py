"""Bản demo trên VPS (dữ liệu giả): đăng nhập dev từ máy KHÁC chỉ khi gửi đúng X-Demo-Key; không đặt khoá ⇒ chỉ máy chạy core.
/health báo commit đang chạy và 503 khi đang rút (Swarm / Traefik thôi gửi request mới)."""

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from barista import http_api as api
from barista.config import settings
from barista.http_api import app


def remote() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=("10.0.0.7", 51000)), base_url="http://test")


@pytest.fixture
def demo_key() -> Iterator[str]:
    old = settings.demo_key
    settings.demo_key = "khoa-demo-thu-nghiem-0123456789"
    yield settings.demo_key
    settings.demo_key = old


async def test_remote_dev_login_needs_the_demo_key(demo: dict[str, Any], demo_key: str) -> None:
    h = {"x-dev-user": "giamdoc@sanmau.demo", "x-tenant-id": str(demo["tenant"])}
    async with remote() as c:
        assert (await c.get("/v1/manifest", headers=h)).status_code == 401
        assert (await c.get("/v1/manifest", headers={**h, "x-demo-key": "sai"})).status_code == 401
        assert (await c.get("/v1/dev/logins")).status_code == 404
        assert (await c.get("/v1/manifest", headers={**h, "x-demo-key": demo_key})).status_code == 200
        assert (await c.get("/v1/dev/logins", headers={"x-demo-key": demo_key})).status_code == 200
        assert (await c.get("/v1/auth/config", headers={"x-demo-key": demo_key})).json()["dev"] is True


async def test_without_a_demo_key_remote_dev_login_stays_closed(demo: dict[str, Any]) -> None:
    assert settings.demo_key == ""
    async with remote() as c:
        r = await c.get("/v1/manifest", headers={"x-dev-user": "giamdoc@sanmau.demo", "x-tenant-id": str(demo["tenant"]), "x-demo-key": ""})
        assert r.status_code == 401


async def test_a_public_demo_lets_apps_pick_a_role_without_the_key(demo: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Bản demo công khai (``demo_open`` — bản cài ``"gate": false``, toàn dữ liệu giả): app gọi thẳng API (không qua BFF giữ khoá) vẫn thấy trang chọn vai."""
    monkeypatch.setattr(settings, "demo_open", True)
    async with remote() as c:
        assert (await c.get("/v1/dev/logins")).status_code == 200
        r = await c.get("/v1/manifest", headers={"x-dev-user": "giamdoc@sanmau.demo", "x-tenant-id": str(demo["tenant"])})
        assert r.status_code == 200
    monkeypatch.setattr(settings, "dev_auth", False)   # đăng nhập dev tắt hẳn thì cờ demo công khai cũng không mở gì
    async with remote() as c:
        assert (await c.get("/v1/dev/logins")).status_code == 404


async def test_health_reports_release_and_drains(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "release", "abc123")
    monkeypatch.setattr(api, "DRAIN_FLAG", tmp_path / "draining")
    async with remote() as c:
        r = await c.get("/health")
        assert r.status_code == 200 and r.json() == {"status": "ok", "release": "abc123"}
        (tmp_path / "draining").touch()
        assert (await c.get("/health")).status_code == 503
