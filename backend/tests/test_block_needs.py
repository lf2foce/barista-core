"""Quyền theo KHỐI — lớp 3 (ADR 0239 bổ sung 18/11/2026, api 1.59): cơ chế của lõi, không gói ngành (nhóm thông tin của lõi: sổ cái, lương).

Khối / ô số / cột / chuỗi / mục khai ``needs=``; cửa ra của executor (``localized`` — run / query / propose) bỏ phần người xem thiếu quyền, kể cả GIÁ TRỊ
cột trong từng dòng; hàng lưới mất ô thì ô còn lại giãn ra; khoá gõ sai ⇒ bỏ cho mọi người + ghi lại. Test quét mọi vai × mọi màn ở gói Bất động sản
(``real_estate/tests/test_block_permissions.py``)."""

from typing import Any
from uuid import UUID

from barista.kernel import executor, needs, ui
from barista.kernel.executor import Result
from barista.kernel.principal import Principal, system_principal
from tests.conftest import Who


def _page() -> dict[str, Any]:
    rows = [{"id": 1, "name": "A", "salary": 25_000_000, "balance": 7}, {"id": 2, "name": "B", "salary": 31_000_000, "balance": 9}]
    table = ui.table(rows, [ui.col("name", "Tên"), ui.col("salary", "Lương", "money", needs="payslips"), ui.col("balance", "Số dư", "money", needs="ledger:all")],
                     kpis=[ui.kpi("Người", 2), ui.kpi("Quỹ lương", 56_000_000, "money", needs="payslips")])
    return ui.card("Thử", [
        ui.span(ui.b_kpis([ui.kpi("Người", 2), ui.kpi("Quỹ lương", 56_000_000, "money", needs="payslips")]), 4),
        ui.span(ui.b_chart("bar", ["T1"], [ui.line("Lương", [56_000_000], needs="payslips")], title="Lương theo tháng", unit="vnd"), 4),
        ui.span(ui.b_text("Sổ cái tháng này", needs="ledger:all"), 4),
        ui.b_list([{"title": "Ai cũng thấy"}, ui.gated({"title": "Chỉ người xem lương"}, "payslips")], "Việc"),
        ui.b_table(table),
    ])


async def test_the_exit_cuts_blocks_columns_and_row_values_the_viewer_may_not_see(who: Who) -> None:
    mkt, gd = await who("mkt"), await who("giamdoc")
    assert not needs.meets(mkt, "payslips") and not needs.meets(mkt, "ledger:all")
    page = _page()
    out = executor.localized(mkt, Result("ok", page)).output
    types = [b["type"] for b in out["blocks"]]
    assert types == ["kpis", "list", "table"]                                       # biểu đồ hết chuỗi + đoạn chữ sổ cái ⇒ bỏ cả khối
    assert out["blocks"][0]["w"] == 12                                              # hàng lưới 4 + 4 + 4 mất hai ô ⇒ ô còn lại đủ hàng
    assert [k["label"] for k in out["blocks"][0]["items"]] == ["Người"]
    assert [i["title"] for i in out["blocks"][1]["items"]] == ["Ai cũng thấy"]
    t = out["blocks"][2]["page"]
    assert [c["key"] for c in t["columns"]] == ["name"] and [k["label"] for k in t["kpis"]] == ["Người"]
    assert all(set(r) == {"id", "name"} for r in t["rows"])                          # số không rời máy chủ — không chỉ ẩn tiêu đề
    # người đủ quyền (ở đây: quyền hệ thống) nhận đúng trang như module dựng — dấu needs giữ lại để bản chốt / thẻ lưu lọc lại theo người mở
    assert needs.cut(system_principal(mkt.tenant_id), page) == page
    # phạm vi: ``ledger:all`` cần cả công ty; ``payslips`` (không ghi phạm vi) = có quyền xem ở bất kỳ phạm vi nào — dòng nào thấy vẫn do RLS
    assert needs.meets(gd, "ledger") == gd.can("ledger", "read") and needs.meets(gd, "ledger:all") == (gd.can("ledger", "read") and gd.has_scope("ledger", "all"))
    assert needs.cut(mkt, {"x": 1}) == {"x": 1}                                      # không có dấu needs ⇒ trả nguyên


def test_wrong_needs_key_hides_for_everyone_and_is_recorded() -> None:
    needs.UNKNOWN.discard("khong_co_nhom_nay")
    p = system_principal(UUID(int=1))
    assert needs.resolve("ledger") == "ledger" and needs.resolve("payslips") == "pay.slips"
    assert not needs.meets(p, "khong_co_nhom_nay") and "khong_co_nhom_nay" in needs.UNKNOWN   # kể cả quyền hệ thống: khoá sai là lỗi khai
    assert not needs.meets(p, "ledger:everything")                                              # phạm vi lạ
    needs.UNKNOWN.discard("khong_co_nhom_nay")


def test_needs_marker_does_not_collide_with_existing_needs_fields() -> None:
    """Trang sẵn có mang ô tên "needs" (vd gói cần bật kèm ở Store) KHÔNG bị coi là dấu quyền theo khối."""
    p = Principal(tenant_id=UUID(int=1), user_id=None, grants={})
    page = ui.table([{"id": 1, "needs": "Khách hàng"}], [ui.col("needs", "Bật kèm gói")])
    assert needs.cut(p, page) == page
    assert ui.col("x", "X", needs="ledger")["needs_info"] == ["ledger"] and "needs_info" not in ui.col("x", "X")
