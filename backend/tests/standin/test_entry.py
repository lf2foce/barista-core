"""NHẬP LIỆU / API / MANIFEST với thao tác của một gói ngành — bản LÕI (ADR 0190) của ``barista-packs:commission/tests/test_entry.py``: màn của gói góp vào phân
hệ lọc theo vai (nút chỉ hiện với người chạy được lệnh), lệnh / truy vấn / xuất Excel / mẫu nhập / form của gói qua đúng HTTP API của lõi. Phần không
cần gói (mẫu Excel của mọi lệnh nhập được, màn nhà theo vai) nằm ở ``tests/test_entry.py``."""

import uuid

from barista.kernel import imports, registry
from tests.conftest import Who, owner_val
from tests.standin import helpers
from tests.test_entry import _client, _h


async def test_manifest_is_filtered_by_role(who: Who) -> None:
    async with _client() as c:
        spaces, orders = {}, {}
        for local in ("sale1", "mkt", "ketoan", "admin", "giamdoc"):
            m = (await c.get("/v1/manifest", headers=_h(await who(local)))).json()
            spaces[local] = {w["key"]: [s["key"] for s in w["screens"]] for w in m["workspaces"]}
            orders[local] = next((s for w in m["workspaces"] for s in w["screens"] if s["key"] == "orders"), None)
    # việc của chính mình ở BÀN VIỆC (0094), không thành phân hệ riêng; "App của tôi" (0098) và "Kết nối & kỹ năng" (0261) ai cũng có; vai bán hàng có sẵn quyền Lịch (0158)
    assert set(spaces["sale1"]) == {"sales", "kpi", "docs", "work", "apps", "schedule", "hr", "plugins"}   # KPI có phân hệ riêng (0124)
    assert spaces["sale1"]["hr"] == ["hr_my_credentials"]   # chứng chỉ của chính mình (0202) — không màn của đội / cả công ty
    assert set(spaces["sale1"]["schedule"]) == {"schedule_week"}   # không có công cụ tìm / thiết lập (cần quyền đặt lịch / quản lý)
    assert spaces["sale1"]["sales"] == ["leads", "orders"]                                # màn của gói góp vào phân hệ Kinh doanh
    assert set(spaces["mkt"]) == {"marketing", "docs", "work", "apps", "schedule", "plugins"}   # không quyền với tài nguyên của gói ⇒ không có màn của gói; Lịch của mình (0240)
    assert "accounting" in spaces["ketoan"] and spaces["ketoan"]["sales"] == ["orders"]   # kế toán đọc đơn cả công ty, không đọc khách
    assert spaces["ketoan"].get("admin") == ["hr_staff", "hr_org"]                        # không thấy màn hệ thống
    assert {"users", "permissions", "chains", "leads_entry"} <= set(spaces["admin"]["admin"]) and "orders" in spaces["admin"]["sales"]
    assert "accounting" in spaces["giamdoc"] and "users" not in spaces["giamdoc"].get("admin", [])
    # nút trên màn theo QUYỀN LỆNH: người ghi được thấy Mở / Ký / Huỷ, người chỉ đọc thấy màn + mở hồ sơ, không nút nào
    for local in ("sale1", "admin"):
        assert [a["command"] for a in orders[local]["actions"]] == ["orderdesk.create"], local
        assert [a["command"] for a in orders[local]["row_actions"]] == ["orderdesk.sign", "orderdesk.cancel"], local
        assert orders[local]["actions"][0]["title"] == "Mở đơn dịch vụ" and orders[local]["actions"][0]["importable"] is True
    for local in ("ketoan", "giamdoc"):
        assert orders[local]["actions"] == [] and orders[local]["row_actions"] == [], local
        assert orders[local]["open"]["query"] == "orderdesk.case_file", local
    assert orders["mkt"] is None


async def test_api_command_query_and_exports(who: Who) -> None:
    sale1, admin = await who("sale1"), await who("admin")
    await helpers.order(who, owner="sale1", line="LD")
    code = f"DH-API-{uuid.uuid4().hex[:6].upper()}"
    async with _client() as c:
        r = await c.post("/v1/commands/admin.invite_user", json={"email": "a@b.vn", "name": "A", "role": "sale"}, headers=_h(sale1))
        assert r.status_code == 403
        r = await c.post("/v1/queries/orderdesk.list", json={"line": "LD"}, headers=_h(sale1))
        assert r.status_code == 200 and r.json()["output"]["kind"] == "table" and r.json()["output"]["title"] == "Đơn dịch vụ"
        assert r.json()["output"]["rows"] and all(x["line"] == "LD" for x in r.json()["output"]["rows"])
        r = await c.post("/v1/queries/orderdesk.report_year", json={}, headers=_h(sale1))
        assert r.status_code == 403                                                        # số cả công ty: API cũng từ chối
        r = await c.post("/v1/queries/orderdesk.list/xlsx", json={}, headers=_h(admin))
        assert r.status_code == 200 and r.content[:2] == b"PK"
        r = await c.get("/v1/imports/orderdesk.create/template", headers=_h(admin))
        assert r.status_code == 200 and r.content[:2] == b"PK"
        mapped, unknown = imports.map_rows(registry.OPS["orderdesk.create"], [{"Nội dung đơn": "Tư vấn", "Khách hàng": "Khách file", "Giá trị đơn": 5}],
                                           admin.vocab)
        assert unknown == [] and mapped[0][1] == {"title": "Tư vấn", "customer": "Khách file", "amount_vnd": 5}   # tiêu đề cột của mẫu = nhãn ô form
        r = await c.get("/v1/forms/orderdesk.create", headers=_h(admin))
        assert {f["key"] for f in r.json()["fields"]} >= {"title", "customer", "amount_vnd", "owner_email"} and r.json()["title"] == "Mở đơn dịch vụ"
        r = await c.get("/v1/manifest", headers={"X-Dev-User": "khong-co@x.vn", "X-Tenant-Id": str(sale1.tenant_id)})
        assert r.status_code == 403
        body = {"title": "Tư vấn", "customer": "Khách API", "amount_vnd": 1_000_000_000, "code": code}
        key = {**_h(sale1), "Idempotency-Key": f"api-{code}"}
        r = await c.post("/v1/commands/orderdesk.create", json=body, headers=key)
        again = await c.post("/v1/commands/orderdesk.create", json=body, headers=key)
        r2 = await c.post("/v1/commands/orderdesk.create", json={**body, "customer": "Khách khác"}, headers=key)
        assert r.status_code == 200 and again.status_code == 200 and r2.status_code == 409  # cùng khoá, khác dữ liệu ⇒ từ chối
    assert await owner_val("SELECT count(*) FROM orderdesk.orders WHERE code = %s AND tenant_id = %s", (code, sale1.tenant_id)) == 1
