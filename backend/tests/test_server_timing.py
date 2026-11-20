"""Server-Timing (12/11/2026, đo "mở dòng chậm"): mọi phản hồi của core mang thời lượng từng chặng — nạp quyền, executor, dựng JSON, tổng — để đo trên
bản cài thật bằng DevTools mà không cần vào máy chủ. Chỉ thời lượng, không dữ liệu."""

import re
from typing import Any

from httpx import ASGITransport, AsyncClient

from barista.http_api import app
from barista.kernel import timing
from tests.conftest import Who


def spans(header: str) -> dict[str, float]:
    return {m.group(1): float(m.group(2)) for m in re.finditer(r"([\w-]+);dur=([\d.]+)", header)}


async def test_query_reports_each_stage(who: Who) -> None:
    admin = await who("admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/v1/queries/hr.staff", json={}, headers={"X-Dev-User": admin.email, "X-Tenant-Id": str(admin.tenant_id)})
    assert r.status_code == 200, r.text
    got = spans(r.headers["server-timing"])
    assert {"owner-wait", "principal", "app-wait", "claims", "handler", "executor", "json", "app"} <= set(got)
    assert got["app"] >= got["executor"] >= got["handler"] and got["app"] >= got["principal"]
    assert re.fullmatch(r"[\w-]+;dur=[\d.]+(, [\w-]+;dur=[\d.]+)*", r.headers["server-timing"])   # chỉ tên chặng + số


async def test_refused_and_public_responses_still_carry_the_total(demo: dict[str, Any]) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert "app;dur=" in (await c.get("/health")).headers["server-timing"]
        r = await c.post("/v1/queries/hr.staff", json={}, headers={"Authorization": "Bearer khong-hop-le", "X-Tenant-Id": str(demo["tenant"])})
    assert r.status_code == 401 and "session;dur=" in r.headers["server-timing"]


def test_outside_a_request_spans_are_ignored() -> None:
    with timing.span("executor"):
        timing.add("claims", 1.0)   # không có request HTTP đang chạy ⇒ không làm gì, không lỗi
    assert timing.header({"executor": 0.0125}, 0.02) == b"executor;dur=12.5, app;dur=20.0"
