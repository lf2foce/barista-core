"""Nhập liệu tối thiểu (0225): mã do máy đặt, ô chọn thay ô gõ mã — các sửa nhỏ ở ngoài kho tài liệu."""

import uuid

from barista.kernel import forms, registry
from barista.kernel.text import code_from_name
from tests.conftest import Who, ok, q


def test_code_from_name_is_upper_ascii_unique_and_fits_the_limit() -> None:
    assert code_from_name("Kinh doanh Đông") == "KINH-DOANH-DONG"
    assert code_from_name("Kinh doanh Đông", {"KINH-DOANH-DONG"}) == "KINH-DOANH-DONG-2"
    assert code_from_name("Kinh doanh Đông", {"KINH-DOANH-DONG", "KINH-DOANH-DONG-2"}) == "KINH-DOANH-DONG-3"
    long = code_from_name("Chính sách bán hàng Hồ Sen Residence tháng mười một năm 2026", limit=30)
    assert long == "CHINH-SACH-BAN-HANG-HO-SEN" and len(long) <= 30
    assert code_from_name("???") == "MA" and code_from_name("x" * 80, limit=30) == "X" * 27


async def test_a_new_team_needs_no_typed_code(who: Who) -> None:
    admin = await who("admin")
    name = f"Đội Thử {uuid.uuid4().hex[:6]}"
    t1 = await ok(admin, "admin.upsert_team", name=name, kind="sales", parent_code="HQ")
    assert t1["code"].startswith("DOI-THU-")
    t2 = await ok(admin, "admin.upsert_team", name=name, kind="sales", parent_code="HQ")
    assert t2["code"] == f"{t1['code']}-2" and t2["team_id"] != t1["team_id"]                                  # trùng tên ⇒ mã khác, không ghi đè đội cũ
    again = await ok(admin, "admin.upsert_team", code=t1["code"], name=name + " đổi tên", kind="sales", parent_code="HQ")
    assert again["team_id"] == t1["team_id"]                                                                    # có mã ⇒ vẫn là sửa như cũ


async def test_receivable_pickers_replace_typed_codes_and_include_paid_ones(who: Who) -> None:
    for name in ("acct.cancel_vat_invoice", "acct.adjust_vat_invoice", "acct.reverse_receipt", "acct.credit_receivable"):
        spec = {f["key"]: f for f in forms.form_spec(registry.OPS[name])["fields"]}
        assert spec["code"].get("options_query") == "acct.receivable_any_options", name
    kt = await who("ketoan")
    opts = await q(kt, "acct.receivable_any_options")
    assert isinstance(opts, list) and all({"value", "label"} <= set(o) for o in opts)
