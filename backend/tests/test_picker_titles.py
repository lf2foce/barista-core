"""Màn chọn vai (``GET /v1/dev/logins``): nhãn người = CHỨC DANH do gói cung cấp (``Module.titles`` — lõi không đọc bảng của gói), vai quyền nêu ở mô tả;
gói tắt / chưa ghi chức danh ⇒ nhãn suy từ vai + đội như trước. Thước đo lõi không nêu tên gói vẫn là ``tests/test_dummy_pack.py``."""

import uuid
from typing import Any

from httpx import ASGITransport, AsyncClient

from barista import seed
from barista.http_api import app
from barista.kernel import registry
from barista.kernel.clock import today
from barista.workspaces import picker_hint, picker_label
from tests.conftest import ok, owner_val

PEOPLE = [("admin", "Quản trị thử", "admin", "QT", True), ("gdkhoi", "Giám đốc khối thử", "team_lead", "KHOI-1", True),
          ("sale", "Sale thử", "sale", "KD-A", False)]


async def _logins(tid: Any) -> dict[str, dict[str, Any]]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        res = await c.get("/v1/dev/logins")
    assert res.status_code == 200
    return {r["email"].split("@")[0]: r for r in res.json() if r["tenant_id"] == str(tid)}


async def _party(tid: Any, local: str, domain: str) -> str:
    return str(await owner_val("SELECT p.id FROM parties.parties p JOIN core.users u ON u.id = p.user_id WHERE p.tenant_id = %s AND u.email = %s",
                               (tid, f"{local}@{domain}")))


async def test_the_picker_shows_the_pack_title_and_the_role_and_falls_back_when_the_pack_is_off() -> None:
    d = f"{uuid.uuid4().hex[:6]}.chucdanh.test"
    tid = await seed.create_tenant(f"Công ty chức danh {d}", d, packs=("hr",), people=PEOPLE, about="thử chức danh")
    admin = await seed.who(tid, "admin", d)
    assert registry.OPS["hr.current_titles"].internal and "hr.current_titles" in registry.TITLES

    before = await _logins(tid)   # chưa ghi chức danh ⇒ suy từ vai + đội; tên đội chỉ lặp lại vai thì bỏ ("· trưởng Quản trị")
    assert before["gdkhoi"]["role"] == "Trưởng nhóm · Khối kinh doanh 1" and before["gdkhoi"]["title"] == ""
    assert before["admin"]["role"] == "Quản trị hệ thống" and before["sale"]["role"] == "Sale · Team Kinh doanh A"
    assert "Vai " not in before["gdkhoi"]["hint"]   # nhiều công ty ⇒ mô tả mở bằng tên công ty

    start = today().isoformat()
    await ok(admin, "hr.set_employment", party_id=await _party(tid, "gdkhoi", d), employee_code="T1", title="Giám đốc Khối kinh doanh 1", started_on=start)
    await ok(admin, "hr.set_employment", party_id=await _party(tid, "sale", d), employee_code="T2", title="Chuyên viên kinh doanh", started_on=start)
    await ok(admin, "hr.set_employment", party_id=await _party(tid, "admin", d), employee_code="T3", title="Trưởng phòng Quản trị hệ thống", started_on=start)
    got = await _logins(tid)
    lead = got["gdkhoi"]
    assert lead["role"] == "Giám đốc Khối kinh doanh 1" and lead["title"] == "Giám đốc Khối kinh doanh 1" and lead["role_label"] == "Trưởng nhóm"
    assert "· Vai Trưởng nhóm — " in lead["hint"] and lead["group"] == before["gdkhoi"]["group"] and lead["ask"]
    assert got["sale"]["role"] == "Chuyên viên kinh doanh · Team Kinh doanh A"   # người không phải trưởng: chức danh kèm đội
    assert "· Vai Quản trị hệ thống — Người dùng" in got["admin"]["hint"]   # mô tả mở bằng tên vai ⇒ không lặp "Quản trị hệ thống: "
    assert {"team", "is_lead", "user_id"}.isdisjoint(lead)

    await ok(admin, "admin.set_pack", pack="hr", enabled=False)
    off = await _logins(tid)
    assert off["gdkhoi"]["role"] == "Trưởng nhóm · Khối kinh doanh 1" and off["gdkhoi"]["title"] == "" and off["gdkhoi"]["hint"] == before["gdkhoi"]["hint"]


def test_label_rules() -> None:
    assert picker_label("Trưởng nhóm", "Team Kinh doanh A", True, "Trưởng team Kinh doanh A") == "Trưởng team Kinh doanh A"
    assert picker_label("Marketing", "Marketing", False, "Chuyên viên Marketing nội dung") == "Chuyên viên Marketing nội dung"   # chức danh đã nêu đội
    assert picker_label("Kế toán", "Kế toán", True) == "Trưởng Kế toán" and picker_label("Sale", None, False) == "Sale"
    assert picker_hint("Giáo vụ", "Giáo vụ", "Giáo vụ: người học.") == "Giáo vụ: người học."   # chức danh trùng tên vai ⇒ mô tả giữ nguyên
    assert picker_hint("Giáo viên", "Giáo viên IELTS", "Giáo viên: buổi dạy.") == "Vai Giáo viên — Buổi dạy."
    assert picker_hint("Khác", "Cố vấn", "") == "Vai Khác"
