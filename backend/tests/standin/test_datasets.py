"""TẬP DỮ LIỆU CỦA MỘT GÓI NGÀNH (0100) qua gói thế vai: bản LÕI (ADR 0190) của ``barista-packs:real_estate/tests/test_datasets.py`` — nhóm / cộng trên TOÀN BỘ
dòng bằng quyền người xem; tổng các nhóm = ô tổng; ô tổng bấm ra đúng các dòng gốc (khớp từng đồng); tên chiều / chỉ số lạ bị từ chối (không có đường
chèn SQL). Công ty thế vai không có dữ liệu nạp sẵn ⇒ mỗi test tự dựng đơn (mở + đã ký, của hai người)."""

from barista.kernel import executor
from tests.conftest import Who, q
from tests.standin import helpers
from tests.test_datasets import _kpi

DS = "orderdesk.ds_orders"


async def _orders(who: Who) -> None:
    """Đơn mở + đơn đã ký của hai người phụ trách khác nhau (sale1, sale3) — đủ hai trạng thái, hai phạm vi nhìn."""
    await helpers.order(who, owner="sale1", amount=1_200_000_000)
    await helpers.signed(who, owner="sale1", amount=2_500_000_000)
    await helpers.signed(who, owner="sale3", amount=4_000_000_000, line="LD")
    await helpers.order(who, owner="sale3", amount=700_000_000)


async def test_groups_add_up_and_the_total_opens_the_exact_rows(who: Who) -> None:
    await _orders(who)
    gd = await who("giamdoc")
    page = await q(gd, DS, measures=["count", "value"], by=["status"])
    assert page["total"] == len(page["rows"]) >= 2 and "cả công ty" in page["note"]
    total = _kpi(page, "value")
    assert total["value"] == sum(r["value"] for r in page["rows"]) > 0
    src = await q(gd, total["drill"]["query"], **total["drill"]["input"])
    assert sum(r["amount_vnd"] for r in src["rows"]) == total["value"]         # bấm ô tổng ⇒ đúng các đơn tạo nên nó
    signed = await q(gd, DS, measures=["value"], where={"status": ["signed"]}, date="signed_on", date_from="2020-01-01", date_before="2100-01-01")
    d = _kpi(signed, "value")["drill"]
    assert d["input"]["status"] == "signed" and d["input"]["signed_from"] == "2020-01-01"
    rows = (await q(gd, d["query"], **d["input"]))["rows"]
    assert sum(r["amount_vnd"] for r in rows) == _kpi(signed, "value")["value"] > 0


async def test_viewer_rights_apply_and_many_values_mean_no_drill(who: Who) -> None:
    await _orders(who)
    sale1, gd = await who("sale1"), await who("giamdoc")
    mine = _kpi(await q(sale1, DS), "count")["value"]
    all_ = _kpi(await q(gd, DS), "count")["value"]
    assert 0 < mine < all_
    two = await q(gd, DS, where={"status": ["signed", "open"]})
    assert "drill" not in _kpi(two, "count")                                   # danh sách gốc không lọc được hai trạng thái ⇒ không đoán


async def test_unknown_names_are_refused(who: Who) -> None:
    gd = await who("giamdoc")
    for bad in ({"by": ["status; DROP TABLE x"]}, {"measures": ["amount_vnd"]}, {"where": {"nope": ["1"]}}, {"by": ["a", "b", "c"]}):
        r = await executor.query(gd, DS, bad)
        assert r.status == "invalid", (bad, r.status)


async def test_a_bare_value_is_taken_as_a_one_item_list(who: Who) -> None:
    """Mô hình AI hay gửi "by": "status" thay vì ["status"] (Soạn với AI từng lặp lại đúng lệnh đó 5 lần, lần nào cũng lỗi): nhận như danh sách một
    phần tử, kết quả y hệt; giá trị lạ vẫn bị từ chối."""
    await _orders(who)
    gd = await who("giamdoc")
    bare = await q(gd, DS, measures="value", by="status", where={"status": "signed"})
    full = await q(gd, DS, measures=["value"], by=["status"], where={"status": ["signed"]})
    assert bare["rows"] == full["rows"] and bare["rows"]
    assert (await executor.query(gd, DS, {"by": "status; DROP TABLE x"})).status == "invalid"
