"""Gói nghiệp vụ (quyết định 0045): công ty chỉ bật gói Bán hàng ⇒ màn / nút / công cụ trợ lý / bên nghe sự kiện của Tài chính và
Marketing biến mất, phần còn lại chạy trọn vẹn (không lỗi). Bật / tắt kiểm phụ thuộc; trợ lý không bật / tắt thay."""

import uuid
from typing import Any

import pytest

from barista import seed
from barista.kernel import executor
from barista.workspaces import manifest

DOMAIN = "goi.demo"
PEOPLE = [("admin", "Quản trị gói", "admin", "QT", True), ("truonga", "Trưởng nhóm gói", "team_lead", "KD-A", True),
          ("sale1", "Sale gói", "sale", "KD-A", False), ("ketoan", "Kế toán gói", "accountant", "KT", True)]


@pytest.fixture(scope="module")
async def sales_only() -> Any:
    return await seed.create_tenant(f"Công ty chỉ bán hàng {uuid.uuid4().hex[:4]}", DOMAIN, people=PEOPLE, packs=("real_estate",))


async def _who(tid: Any, local: str) -> Any:
    return await seed.who(tid, local, DOMAIN)


async def test_every_visible_screen_works_with_only_sales(sales_only: Any) -> None:
    """Tắt gói không làm vỡ gói còn lại: mọi màn người dùng thấy đều mở được (không lỗi hệ thống)."""
    for local, *_ in PEOPLE:
        p = await _who(sales_only, local)
        for w in manifest(p, "x")["workspaces"]:
            for s in w["screens"]:
                r = await executor.query(p, s["query"], {})
                assert r.status == "ok", (local, s["query"], r.status, r.error)
