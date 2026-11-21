"""SỐ TÓM TẮT cho màn tổng hợp (21/11/2026): module sở hữu công bố đúng con số màn của nó hiện (``health.summary``, ``leadflow.summary``,
``acct.cashflow_projection``, ``core.approvals_summary``) để bảng điều hành của gói không dựng cả màn rồi dò ô số theo nhãn. Kiểm: cùng số với màn,
gọi bằng quyền NGƯỜI XEM (người không xem được màn ⇒ None), số đếm trên CẢ tập (không theo trần của danh sách)."""

from typing import Any

from barista.db import db
from barista.kernel.executor import core_ctx
from barista.kernel.principal import Principal
from barista.modules.acct.integrity import _first
from tests.conftest import Who, owner_val, q


async def _summary(p: Principal, name: str, **inp: Any) -> Any:
    """Như bảng điều hành của gói gọi: ``call_as_optional(name, ctx.p)`` trong giao dịch chỉ đọc của người xem."""
    async with db.tenant_tx(p, read_only=True) as conn:
        return await core_ctx(conn, p, module="kpi").call_as_optional(name, p, **inp)


def _kpis(card: dict[str, Any]) -> dict[str, Any]:
    items = list(card.get("kpis") or [])
    for b in card.get("blocks") or []:
        if b.get("type") == "kpis":
            items += b.get("items") or []
    return {k["label"]: k["value"] for k in items}


async def test_health_summary_is_the_screen_figures_and_needs_the_screen_permission(who: Who) -> None:
    gd, sale = await who("giamdoc"), await who("sale1")
    k = _kpis(await q(gd, "health.overview"))
    assert await _summary(gd, "health.summary") == {"issue_kinds": k["Loại vấn đề cần sửa"], "records": k["Bản ghi cần sửa"],
                                                     "stale_sources": k["Nguồn cập nhật trễ"]}
    assert await _summary(sale, "health.summary") is None   # sale không xem Sức khoẻ dữ liệu cả công ty ⇒ không có số


async def test_leadflow_summary_is_the_screen_figures_and_counts_the_whole_set(who: Who, demo: dict[str, Any]) -> None:
    gd, sale = await who("giamdoc"), await who("sale1")
    k = _kpis(await q(gd, "leadflow.overview"))
    got = await _summary(gd, "leadflow.summary")
    assert (got["stuck"], got["planned"], got["waiting"], got["recalled"], got["returned"]) == (
        k["Kẹt — không ai nhận"], k["Chờ điều phối chia"], k["Đang chờ gọi lần đầu"], k["Thu hồi (7 ngày)"], k["Khách quay lại (7 ngày)"])
    # ô số = cả tập (danh sách của màn dừng ở 50 / 60 dòng)
    assert got["waiting"] == await owner_val("SELECT count(*) FROM leadflow.lead_state WHERE tenant_id = %s AND status IN ('offered', 'waiting')"
                                             " AND imported_at IS NULL", (demo["tenant"],))
    assert await _summary(sale, "leadflow.summary") is None


async def test_cashflow_projection_is_the_screen_projection(who: Who) -> None:
    kt, sale = await who("ketoan"), await who("sale1")
    card = await q(kt, "acct.cashflow", months=4)
    got = await _summary(kt, "acct.cashflow_projection", months=4)
    assert got["rows"] == [{"month": r["month"], "closing": r["closing"]} for r in card["rows"]] and got["note"] == card["note"]
    assert await _summary(sale, "acct.cashflow_projection", months=4) is None


async def test_approvals_summary_counts_what_waits_for_the_viewer(who: Who) -> None:
    gd = await who("giamdoc")
    assert await _summary(gd, "core.approvals_summary") == {"waiting": _kpis(await q(gd, "core.approvals"))["Chờ bạn duyệt"]}


async def test_money_drift_counts_every_row_and_lists_the_first_codes() -> None:
    """Bất biến tiền lệch: số đếm là cả tập (trước đây tối đa 20 + 20 vì đếm danh sách mã đã cắt); mã kèm theo là 20 mã đầu theo thứ tự."""
    sql = _first("SELECT 'R' || lpad(x::text, 3, '0') AS code FROM generate_series(1, 57) x", "code", "code")
    async with db.owner.connection() as conn:
        r = await (await conn.execute(sql)).fetchone()
    assert r is not None and r["n"] == 57 and r["first"] == [f"R{i:03d}" for i in range(1, 21)]
    empty = _first("SELECT 'x' AS code WHERE false", "code", "code")
    async with db.owner.connection() as conn:
        r = await (await conn.execute(empty)).fetchone()
    assert r is not None and r["n"] == 0 and r["first"] == []
