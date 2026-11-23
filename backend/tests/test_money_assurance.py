"""BẢO ĐẢM TIỀN (0228 §D) trên Postgres thật: hồ sơ khoản lệch dựng + đóng bằng đúng lệnh có sẵn (vòng quét, ghi phiếu thu, xoá phần thiếu qua duyệt, đóng có
lý do) ⇒ tập dữ liệu ``acct.ds_dossiers`` và màn ``acct.assurance`` ra đúng số hồ sơ / tiền phát hiện / tiền đã xử lý / phần thiếu đã xoá / số ngày; người phạm
vi hẹp chỉ thấy số của hồ sơ giao cho mình và không mở được màn toàn công ty.

Các hồ sơ của test được dời mốc mở / đóng về THÁNG CÁCH ĐÂY HAI THÁNG (hồ sơ của test khác cùng DB đều mở "bây giờ") ⇒ lọc đúng tháng đó chỉ còn hồ sơ của test này."""

import uuid
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any

from barista.db import db
from barista.kernel import executor
from barista.kernel.clock import add_months, today
from tests.conftest import Who, ok, q
from tests.test_discrepancy_dossier import _amt, _clean, _dossier, _line, _owner_is, _recv, _run_step, _scan, _source  # noqa: F401 — fixtures dùng lại

VN = timezone(timedelta(hours=7))


def _at(d: date) -> datetime:
    return datetime.combine(d, time(12), VN).astimezone(UTC)   # trưa giờ VN ⇒ cùng ngày ở mọi múi giờ công ty thường gặp


async def _move(line_id: str, opened: date, closed: date | None) -> str:
    d = await _dossier(line_id)
    async with db.owner_tx() as conn:
        await conn.execute("UPDATE acct.discrepancy_dossiers SET created_at = %s, closed_at = coalesce(%s, closed_at) WHERE id = %s",
                           (_at(opened), _at(closed) if closed else None, d["id"]))
    return d["code"]


def _k(page: dict[str, Any], key: str) -> Any:
    return next(k for k in page["kpis"] if k.get("key") == key)["value"]


MEASURES = ["opened", "caught_vnd", "resolved", "resolved_vnd", "written_off_vnd", "dismissed", "dismissed_vnd", "open", "open_vnd", "days_sum", "avg_days"]


async def test_money_caught_resolved_written_off_and_days_from_real_dossiers(who: Who) -> None:
    owner = await _owner_is(who, "ketoan")
    # A: khớp đủ, chưa ghi ⇒ kế toán ghi phiếu thu ⇒ resolved
    fa = await _recv(who, _amt())
    la = await _line(who, fa["fee"], f"TT {fa['code']}", fa["party"])
    # B: thiếu nhỏ (nghi phí) ⇒ ghi phần đã nhận + xoá phần thiếu qua phiếu duyệt ⇒ written_off
    fb = await _recv(who, _amt())
    lb = await _line(who, fb["fee"] - 20_000, f"TT {fb['code']}", fb["party"])
    # C: không rõ bên chuyển ⇒ đóng có lý do ⇒ dismissed;  D: không rõ, số lớn ⇒ còn mở
    amt_c, amt_d = _amt() + 12_345, 987_654_321
    lc = await _line(who, amt_c, "chuyen tien", "Nguoi La " + uuid.uuid4().hex[:5])
    ld = await _line(who, amt_d, "chuyen tien", "Nguoi Khac " + uuid.uuid4().hex[:5])
    await _scan(who)

    da = await _dossier(la)
    await _run_step(owner, next(s for a in da["facts"]["actions"] for s in a["steps"] if s["command"] == "acct.apply_line"))
    db_ = await _dossier(lb)
    for s in next(a for a in db_["facts"]["actions"] if a["key"] == "settle_deduction")["steps"]:
        await _run_step(owner, s)
    await ok(owner, "acct.dismiss_dossier", code=(await _dossier(lc))["code"], reason="Chuyển nội bộ giữa hai tài khoản công ty")
    assert [(await _dossier(x))["status"] for x in (la, lb, lc, ld)] == ["resolved", "written_off", "dismissed", "open"]

    m = add_months(today().replace(day=1), -2)
    await _move(la, m + timedelta(days=2), m + timedelta(days=4))      # 2 ngày
    await _move(lb, m + timedelta(days=2), m + timedelta(days=8))      # 6 ngày
    await _move(lc, m + timedelta(days=3), m + timedelta(days=3))      # không phải khoản lệch — không tính vào số ngày
    code_d = await _move(ld, m + timedelta(days=5), None)
    period = {"date_from": m.isoformat(), "date_before": add_months(m, 1).isoformat()}

    # theo NGÀY MỞ: mọi hồ sơ mở trong tháng
    page = await q(owner, "acct.ds_dossiers", measures=MEASURES, date="opened_on", **period)
    caught = fa["fee"] + (fb["fee"] - 20_000) + amt_c + amt_d
    assert {k: _k(page, k) for k in MEASURES} == {
        "opened": 4, "caught_vnd": caught, "resolved": 2, "resolved_vnd": fa["fee"] + fb["fee"] - 20_000, "written_off_vnd": 20_000,
        "dismissed": 1, "dismissed_vnd": amt_c, "open": 1, "open_vnd": amt_d, "days_sum": 8, "avg_days": 4.0}
    assert _k(page, "caught_vnd") == _k(page, "resolved_vnd") + _k(page, "dismissed_vnd") + _k(page, "open_vnd")   # phát hiện = xong + không phải + còn mở
    assert "cả công ty" in page["note"]
    # nhóm theo kết quả: tổng các nhóm = ô tổng, kết quả là nhãn
    by = await q(owner, "acct.ds_dossiers", measures=["caught_vnd"], by=["status"], date="opened_on", **period)
    assert {r["status"]: r["caught_vnd"] for r in by["rows"]} == {"resolved": fa["fee"], "written_off": fb["fee"] - 20_000, "dismissed": amt_c, "open": amt_d}
    # theo NGÀY ĐÓNG: chỉ hồ sơ đã đóng trong tháng (hồ sơ còn mở không có ngày đóng)
    closed = await q(owner, "acct.ds_dossiers", measures=["opened", "resolved", "resolved_vnd", "written_off_vnd", "open"], date="closed_on", **period)
    assert [_k(closed, k) for k in ("opened", "resolved", "resolved_vnd", "written_off_vnd", "open")] == [3, 2, fa["fee"] + fb["fee"] - 20_000, 20_000, 0]
    # chiều nguồn khoản phải thu = phần đầu nguồn của khoản hồ sơ nhắm tới; hồ sơ không nhắm khoản nào ⇒ trống
    src = await q(owner, "acct.ds_dossiers", measures=["opened"], by=["source"], date="opened_on", **period)
    assert {r["source"]: r["opened"] for r in src["rows"]} == {"testpay": 2, "": 2}

    # màn Bảo đảm tiền: tháng cách đây hai tháng nằm trong "3 tháng trước" ⇒ trung vị (2, 6) = 4 ngày; hồ sơ mở lớn nhất đứng đầu
    card = await q(owner, "acct.assurance")
    before = {k["label"].split(" T")[0]: k for k in card["blocks"][1]["items"]}
    assert before["Tiền lệch phát hiện"]["value"] == caught and before["Đã xử lý xong"]["value"] == fa["fee"] + fb["fee"] - 20_000
    assert "xoá phần thiếu 20.000 đ" in before["Đã xử lý xong"]["hint"]
    assert before["Trung vị ngày xử lý"]["value"] == "4 ngày"
    top = next(b for b in card["blocks"] if b["type"] == "list")
    assert top["items"][0]["open"] == {"query": "acct.dossier", "input": {"code": code_d}} and "987.654.321 đ" in top["items"][0]["title"]

    # người phạm vi hẹp: không mở được màn toàn công ty; tập dữ liệu chỉ ra số hồ sơ giao cho MÌNH (RLS), ghi rõ phạm vi
    sale = await who("sale1")
    assert (await executor.query(sale, "acct.assurance", {})).status == "denied"
    mine = await q(sale, "acct.ds_dossiers", measures=["opened", "caught_vnd"], date="opened_on", **period)
    assert (_k(mine, "opened"), _k(mine, "caught_vnd")) == (0, 0) and "cả công ty" not in mine["note"]
    async with db.owner_tx() as conn:
        await conn.execute("UPDATE acct.discrepancy_dossiers SET owner_id = %s WHERE code = %s", (sale.user_id, code_d))
    try:
        mine = await q(sale, "acct.ds_dossiers", measures=["opened", "caught_vnd", "open_vnd", "resolved"], date="opened_on", **period)
        assert [_k(mine, k) for k in ("opened", "caught_vnd", "open_vnd", "resolved")] == [1, amt_d, amt_d, 0]
    finally:   # dọn: test khác cùng DB kiểm "sale1 không thấy hồ sơ nào" và gợi ý buổi sáng (3 hồ sơ mở của kế toán) — trả người cũ rồi đóng hồ sơ còn mở
        async with db.owner_tx() as conn:
            await conn.execute("UPDATE acct.discrepancy_dossiers SET owner_id = %s WHERE code = %s", (owner.user_id, code_d))
        await ok(owner, "acct.dismiss_dossier", code=code_d, reason="Dọn dữ liệu test")


async def test_dataset_is_declared_for_the_dashboard_builder_and_assistant(who: Who) -> None:
    """Tập dữ liệu được mô tả như mọi tập (app tự ghép / trợ lý đọc chiều + chỉ số), trung bình ngày là tỷ lệ của hai chỉ số cộng được."""
    from barista.kernel import datasets

    d = datasets.describe(datasets.DATASETS["acct.dossiers"])
    assert {"opened_month", "closed_month", "status", "entity", "assignee", "source"} <= set(d["dims"])
    assert d["measures"]["avg_days"]["ratio"] == "days_sum / resolved" and set(d["dates"]) == {"opened_on", "closed_on"}
    page = await q(await who("ketoan"), "acct.ds_dossiers", measures=["opened"], by=["opened_month"])
    assert page["source"]["kind"] == "dataset"


async def test_unrecorded_money_on_the_bank_statement_is_never_reported_as_all_clear(who: Who) -> None:
    """Rà 23/11/2026: màn báo xanh "Không còn hồ sơ khoản lệch" trong khi còn dòng sao kê chưa khớp từ đầu tháng (dòng chưa thành hồ sơ). Nay dòng tiền về
    nằm quá STALE_DAYS ngày chưa ghi ⇒ ô đỏ + lời báo, và không còn câu "đã sạch"."""
    from barista.modules.acct.assurance import STALE_DAYS
    ketoan, giamdoc = await who("ketoan"), await who("giamdoc")
    await ok(ketoan, "acct.add_statement_line", received_on=(today() - timedelta(days=STALE_DAYS + 2)).isoformat(), amount_vnd=7_000_000,
             memo=f"KHONG RO NOI DUNG {uuid.uuid4().hex[:6]}")
    page = (await executor.query(giamdoc, "acct.assurance", {})).output
    tiles = [k for b in page["blocks"] if b["type"] == "kpis" for k in b["items"]]
    stale = next(k for k in tiles if k["label"].startswith("Tiền về chưa ghi"))
    assert stale["value"] >= 7_000_000 and stale["tone"] == "red"
    titles = [b.get("title", "") for b in page["blocks"] if b["type"] == "callout"]
    assert any("chưa ghi vào công nợ" in t for t in titles)
    assert not any(t.startswith("Không còn hồ sơ khoản lệch") for t in titles)
    assert next(k for k in tiles if k["label"] == "Còn mở")["tone"] != "green"
