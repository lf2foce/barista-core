"""Đối soát sao kê (họp khách 31/10 — docs/clients/<khách>, F02/F03 — quyết định 0055): dòng tiền vào nạp trước (chưa đụng sổ cái), CODE khớp với hoá đơn theo mã trong
nội dung chuyển khoản + số tiền, người bấm ghi qua đúng acct.record_payment ⇒ hoa hồng / việc theo dõi / hồ sơ tự chạy theo. Chênh lệch không
tự xoá nợ: ghi phần đã nhận + việc cho kế toán."""

import uuid
from datetime import date
from typing import Any

from barista.modules.acct.reconcile import match_lines
from tests.conftest import Who, ok, q

D = date(2026, 11, 5)


def _inv(no: str, amount: int, paid: int = 0, status: str = "open") -> dict[str, Any]:
    return {"id": no, "code": no, "party": no, "due_on": D, "match_hints": [no.rsplit("-", 1)[-1]], "match_keys": [no.removeprefix("HD-")], "amount_vnd": amount, "paid_vnd": paid, "status": status}


def _ln(memo: str, amount: int, ref: str = "", payer: str = "") -> dict[str, Any]:
    return {"id": uuid.uuid4(), "received_on": D, "amount_vnd": amount, "memo": memo, "payer": payer, "bank_ref": ref, "source": "excel"}


def test_matching_rules() -> None:
    invs = [_inv("HD-GD-HSR-10B06", 180_000_000), _inv("HD-GD-HSR-12A01", 90_000_000), _inv("HD-GD-DTP-03C01", 60_000_000),
            _inv("HD-GD-HSR-03C01", 70_000_000), _inv("HD-GD-HSR-09A09", 50_000_000, paid=50_000_000, status="paid")]
    got = {r["memo"]: r for r in match_lines([
        _ln("CDT HO SEN TT PHI MOI GIOI HD GD HSR 10B06", 162_000_000),        # mã hoá đơn viết rời, không gạch ⇒ vẫn khớp; thiếu đúng 10%
        _ln("Thanh toan phi can 12A01", 90_000_000),                              # chỉ mã căn, duy nhất ⇒ khớp đủ
        _ln("Phi moi gioi can 03C01", 60_000_000),                                # mã căn trùng 2 dự án ⇒ không khớp theo căn → theo số tiền
        _ln("chuyen tien", 77_000_000),                                           # không mã, không số nào bằng ⇒ chưa khớp + gợi ý gần nhất
        _ln("TT HD-GD-HSR-09A09", 50_000_000),                                    # hoá đơn đã thu đủ ⇒ có thể trả trùng
                     # mã đã có phiếu thu
    ], invs)}
    a = got["CDT HO SEN TT PHI MOI GIOI HD GD HSR 10B06"]
    assert (a["match"], a["code"], a["diff"], a["how"]) == ("short", "HD-GD-HSR-10B06", 18_000_000, "mã khoản") and "10%" in a["hint"]
    assert (got["Thanh toan phi can 12A01"]["match"], got["Thanh toan phi can 12A01"]["how"]) == ("full", "mã gợi ý")
    assert (got["Phi moi gioi can 03C01"]["match"], got["Phi moi gioi can 03C01"]["code"]) == ("amount_only", "HD-GD-DTP-03C01")
    assert got["chuyen tien"]["match"] == "unmatched" and "HD-GD-HSR-03C01" in got["chuyen tien"]["hint"]
    assert got["TT HD-GD-HSR-09A09"]["match"] == "settled"



def test_two_payments_for_one_invoice_are_counted_in_date_order() -> None:
    first, second = _ln("HD-GD-HSR-10B06 dot 1", 100_000_000), _ln("HD-GD-HSR-10B06 dot 2", 80_000_000)
    second["received_on"] = date(2026, 11, 9)
    out = match_lines([second, first], [_inv("HD-GD-HSR-10B06", 180_000_000)])
    assert [(r["match"], r["remaining"]) for r in out] == [("short", 180_000_000), ("full", 80_000_000)]
    assert "Hỏi" in out[0]["hint"] or "một nửa" in out[0]["hint"]


def test_money_text_keeps_sentence_commas() -> None:
    from barista.modules.acct.common import _vnd
    assert f"Ghi 2 phiếu thu, tổng {_vnd(325_200_000)}" == "Ghi 2 phiếu thu, tổng 325.200.000 đ"


def test_same_payer_and_unit_across_contracts_never_autoselects_one() -> None:
    a, b = _inv("HD-GD-HSR-12A01", 90_000_000), _inv("HD-GD-DTP-12A01", 70_000_000)
    a["party"] = b["party"] = "Chủ đầu tư chung"
    out = match_lines([_ln("Thanh toan phi can 12A01", 80_000_000)], [a, b])
    assert out[0]["match"] == "unmatched" and not out[0]["code"]


async def test_a_line_already_in_the_books_shows_as_waiting_for_allocation_not_unrecorded(who: Who) -> None:
    """Rà 23/11/2026: một dòng tiền đã ghi vào sổ (Tiền về chưa phân bổ) hiện ở Đối soát sao kê là "Chưa khớp — Số tiền chưa ghi" trong khi màn Tiền về
    chưa phân bổ nói nó đã ở sổ cái. Một dòng một trạng thái: "Đã vào sổ — chờ phân bổ"."""
    import uuid as _uuid

    from barista.kernel.clock import today as _today
    ketoan = await who("ketoan")
    ref = f"PARK{_uuid.uuid4().hex[:8].upper()}"
    out = await ok(ketoan, "acct.record_unapplied", amount_vnd=12_345_678, received_on=_today().isoformat(), memo=f"tien chua ro {ref}", bank_ref=ref)
    page = await q(ketoan, "acct.reconcile")
    row = next(r for r in page["rows"] if r["id"] == str(out["line_id"]) or r.get("bank_ref") == ref)
    assert row["state"] == "parked"
    tiles = {k["label"]: k for k in page["kpis"]}
    assert "Đã vào sổ, chờ phân bổ" in tiles and "Số tiền chưa ghi" not in str(page["columns"])
