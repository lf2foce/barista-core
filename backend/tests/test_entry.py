"""Nhập liệu: form sinh từ lệnh, nhập Excel (chạy thử không ghi, nhập thật chống trùng), mẫu Excel, API + manifest."""

from typing import Any

from httpx import ASGITransport, AsyncClient

from barista.http_api import app
from barista.kernel import imports, registry
from barista.kernel.fields import parse_money
from barista.kernel.forms import form_spec
from tests.conftest import Who, fresh_phone, owner_val


def test_money_parsing_vietnamese_styles() -> None:
    assert parse_money("4.500.000.000") == 4_500_000_000
    assert parse_money("4,5 tỷ") == 4_500_000_000
    assert parse_money("90tr") == 90_000_000
    assert parse_money("10 triệu") == 10_000_000
    assert parse_money(1.5e9) == 1_500_000_000


def test_form_spec_from_command_model() -> None:
    spec = form_spec(registry.OPS["crm.create_lead"])
    by = {f["key"]: f for f in spec["fields"]}
    assert by["phone"]["kind"] == "phone" and not by["phone"]["required"]   # 0111: có thể chỉ có email / kênh khác
    assert by["budget_vnd"]["kind"] == "money"
    assert by["source"]["kind"] == "select" and {"value": "ads", "label": "Quảng cáo"} in by["source"]["options"]
    assert by["offering_code"]["options_query"] == "crm.offering_options"
    assert form_spec(registry.OPS["core.decide"])["fields"][0]["hidden"]


async def _count_leads(tenant) -> int:
    return await owner_val("SELECT count(*) FROM crm.leads WHERE tenant_id = %s", (tenant,))


async def test_import_dry_run_reports_real_errors_and_writes_nothing(who: Who) -> None:
    sale1 = await who("sale1")
    dup = fresh_phone()
    rows: list[dict[str, Any]] = [
        {"Họ tên khách": "Khách nhập 1", "SĐT": fresh_phone(), "Nguồn": "Quảng cáo", "Ngân sách": "4,5 tỷ", "Cột lạ": "x"},
        {"Họ tên khách": "Khách nhập 2", "SĐT": "123"},
        {"Họ tên khách": "Khách nhập 3", "SĐT": dup},
        {"Họ tên khách": "Khách nhập 3 trùng", "SĐT": dup},
    ]
    before = await _count_leads(sale1.tenant_id)
    r = await imports.run_import(sale1, "crm.create_lead", rows, dry_run=True)
    out = r.output
    assert [x["status"] for x in out["rows"]] == ["ok", "invalid", "ok", "ok"]  # chạy thử mỗi dòng độc lập (đã rollback)
    assert "SĐT" in out["rows"][1]["error"]
    assert out["unknown_columns"] == ["Cột lạ"]
    assert await _count_leads(sale1.tenant_id) == before

    r = await imports.run_import(sale1, "crm.create_lead", rows, dry_run=False)
    assert [x["status"] for x in r.output["rows"]] == ["ok", "invalid", "ok", "conflict"]
    assert await _count_leads(sale1.tenant_id) == before + 2
    src = await owner_val("SELECT l.source FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id WHERE c.name = 'Khách nhập 1'")
    assert src == "ads"  # nhãn "Quảng cáo" → giá trị

    again = await imports.run_import(sale1, "crm.create_lead", rows, dry_run=False)
    assert again.output["rows"][0]["replayed"] is True  # nhập lại cùng file: không nhân đôi
    assert await _count_leads(sale1.tenant_id) == before + 2


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _h(p) -> dict[str, str]:
    return {"X-Dev-User": p.email, "X-Tenant-Id": str(p.tenant_id)}


async def test_xlsx_import_answers_422_for_unreadable_files_but_does_not_mask_other_errors(who: Who, monkeypatch: Any) -> None:
    """Chỉ tập lỗi "file hỏng" của imports.UNREADABLE thành 422; lỗi lạ (không phải do file) vẫn lộ ra, không bị che thành "file hỏng"."""
    import io
    import zipfile

    import pytest

    admin = await who("admin")
    zip_no_workbook = io.BytesIO()
    with zipfile.ZipFile(zip_no_workbook, "w") as z:
        z.writestr("hello.txt", "x")
    from openpyxl import Workbook

    ok_xlsx = io.BytesIO()
    Workbook().save(ok_xlsx)
    broken_sheet = io.BytesIO()   # xlsx thật nhưng trang tính cắt cụt giữa thẻ
    with zipfile.ZipFile(ok_xlsx) as zin, zipfile.ZipFile(broken_sheet, "w") as zout:
        for item in zin.namelist():
            body = zin.read(item)
            zout.writestr(item, body[: len(body) // 2] if item == "xl/worksheets/sheet1.xml" else body)
    bad = {"xlsx hỏng giữa trang tính": broken_sheet.getvalue(), "CSV có ô quá 128 KB": b"a,b\n" + b"x" * 200_000 + b",1\n",
           "zip giả": b"PK\x03\x04junk" * 10, "zip không phải xlsx": zip_no_workbook.getvalue(), "CSV không phải UTF-8": b"\xff\xfe\xfa,b\n1,2"}
    async with _client() as c:
        for what, data in bad.items():
            r = await c.post("/v1/imports/crm.create_lead/xlsx", content=data, headers=_h(admin))
            assert r.status_code == 422 and "Không đọc được file" in r.text, (what, r.status_code, r.text)

        def boom(_: bytes) -> Any:
            raise RuntimeError("lỗi lập trình, không phải file hỏng")

        monkeypatch.setattr(imports, "read_grid", boom)
        with pytest.raises(RuntimeError):
            await c.post("/v1/imports/crm.create_lead/xlsx", content=b"a,b\n1,2", headers=_h(admin))


# ── Bản LÕI (ADR 0190) của phần trung tính trong ``barista-packs:commission/tests/test_entry.py`` — không cần đối tượng của gói ngành nào ──

def test_template_round_trip_for_every_importable_command() -> None:
    """Mẫu Excel của MỌI lệnh nhập được (đang nạp): đọc lại rồi ánh xạ ⇒ không cột lạ; dòng ví dụ ra đúng khoá ô + đúng chữ ví dụ đã khai (số tiền
    viết kiểu Việt giữ nguyên chuỗi)."""
    from barista.kernel.forms import field_specs

    ops = [op for op in registry.OPS.values() if op.kind == "command" and op.importable]
    assert len(ops) >= 5 and registry.OPS["crm.create_lead"] in ops
    with_example = 0
    for op in ops:
        mapped, unknown = imports.map_rows(op, imports.read_xlsx(imports.template_xlsx(op)))
        assert unknown == [], (op.name, unknown)
        specs = [s for s in field_specs(op) if not s["hidden"] and isinstance(s.get("example"), str) and s["example"]]
        if not specs:
            continue
        with_example += 1
        assert len(mapped) == 1 and {s["key"] for s in specs} <= set(mapped[0][1]), op.name
        free = {s["key"]: s["example"] for s in specs if not s.get("options")}     # ô có danh sách chọn: nhãn trên mẫu đổi về MÃ lúc ánh xạ
        assert {k: str(mapped[0][1][k]) for k in free} == free, op.name
    assert with_example >= 3


async def test_home_follows_role(who: Who) -> None:
    async with _client() as c:
        acc = (await c.get("/v1/manifest", headers=_h(await who("ketoan")))).json()
        gd = (await c.get("/v1/manifest", headers=_h(await who("giamdoc")))).json()
        sale = (await c.get("/v1/manifest", headers=_h(await who("sale1")))).json()
    assert acc["workspaces"][0]["key"] == "accounting"
    assert [p["key"] for p in acc["pins"]][:3] == ["receivables", "cashflow", "reconcile"]  # việc trợ lý cần mình xem: ở bàn việc (0094)
    # màn ghim của vai có mặt; không so vị trí đầu — test khác ghim app tự ghép cho sale1 trong cùng công ty (tuỳ thứ tự file trong tiến trình)
    assert sale["workspaces"][0]["key"] == "sales" and "leads" in [p["key"] for p in sale["pins"]]

    def keys(m: dict[str, Any]) -> set[str]:
        return {s["key"] for w in m["workspaces"] for s in w["screens"]} | {s["key"] for s in m["desk"]}

    assert "lead_mine" not in keys(gd)                    # màn cá nhân (``max_scope``): phạm vi cả công ty không cần
    assert "lead_mine" in keys(sale)
