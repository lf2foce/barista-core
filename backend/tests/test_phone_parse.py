"""Chuẩn hoá SĐT (0197, 0199): MỌI số lưu E.164 (số Việt Nam ``+84…``), đầu số di động cũ đổi sang mới, số nước ngoài chỉ khi nói rõ là quốc tế; hiện
theo quốc gia công ty. SĐT là khoá gộp khách nên luật này là hợp đồng — đổi nó phải kèm migration chuẩn hoá số đã lưu (directory/081–082, crm/081, crm/083)."""

import uuid

import pytest

from barista.kernel import executor
from barista.kernel.fields import OLD_MOBILE_PREFIX, parse_phone, phone_display, phone_search
from tests.conftest import Who, fresh_phone, ok, owner_val, q


@pytest.mark.parametrize(("raw", "want"), [
    ("0912345678", "+84912345678"),
    ("0100000123", "+84100000123"),        # số mẫu đầu 010 (bất biến 9): chỉ kiểm hình dạng, không kiểm đầu số nhà mạng
    ("+84 912 345 678", "+84912345678"),
    ("84912345678", "+84912345678"),
    ("0084912345678", "+84912345678"),
    ("912345678", "+84912345678"),          # Excel / Sheets nuốt số 0 đầu
    ("01623456789", "+84323456789"),        # Viettel cũ → mới
    ("01203456789", "+84703456789"),        # MobiFone
    ("0129 345 6789", "+84823456789"),      # VinaPhone, có khoảng trắng
    ("01993456789", "+84593456789"),        # Gmobile
    ("02412345678", "+842412345678"),       # số bàn 11 chữ số: chỉ đổi sang E.164
    ("+1 781 967 8196", "+17819678196"),  # quốc tế nói rõ bằng +
    ("0081 90 1234 5678", "+819012345678"),  # quốc tế nói rõ bằng 00
    ("", None),
    (None, None),
])
def test_phone_is_normalized_to_one_key(raw: str | None, want: str | None) -> None:
    assert parse_phone(raw) == want


@pytest.mark.parametrize("raw", [
    "17819678196",    # dãy trần đầu 1: số Mỹ hay số Việt Nam gõ sai? — không đoán mã nước
    "0359598",        # thiếu số
    "+12",            # quốc tế quá ngắn
    "+0912345678",    # + rồi 0: không phải mã nước
    "abc",
])
def test_ambiguous_or_broken_numbers_are_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_phone(raw)


def test_old_prefix_table_is_one_to_one() -> None:
    """Hai đầu số cũ đổi về cùng một đầu số mới thì hai khách khác nhau thành một khoá."""
    assert len(set(OLD_MOBILE_PREFIX.values())) == len(OLD_MOBILE_PREFIX)
    assert all(len(k) == 4 and len(v) == 3 for k, v in OLD_MOBILE_PREFIX.items())



async def test_foreign_number_is_one_customer_key(who: Who) -> None:
    """Số quốc tế đi trọn đường: chuẩn hoá ⇒ ghi khách (ràng buộc DB crm/082) ⇒ lần sau cùng số (gõ kiểu khác) là CÙNG khách."""
    sale, tag = await who("sale1"), uuid.uuid4().hex[:6]
    local = f"{int(tag, 16) % 10_000_000:07d}"
    first = await ok(sale, "crm.create_lead", name=f"Việt kiều {tag}", phone=f"+1 781 {local}", offering_code="HSR")
    assert await owner_val("SELECT phone FROM crm.customers WHERE id = %s", (first["customer_id"],)) == f"+1781{local}"
    r = await executor.run(sale, "crm.create_lead", {"name": "Trùng", "phone": f"001781{local}", "offering_code": "HSR"})
    assert r.status == "conflict"   # cùng khách × cùng dự án — không tạo khách thứ hai


@pytest.mark.parametrize(("stored", "country", "shown"), [
    ("+84912345678", "VN", "0912345678"),
    ("+842412345678", "VN", "02412345678"),
    ("+17819678196", "VN", "+17819678196"),   # số nước khác: giữ dạng quốc tế
    ("+84912345678", "US", "+84912345678"),   # nước lõi chưa có luật nội địa: mọi số dạng quốc tế
    ("", "VN", ""),
])
def test_phone_shown_by_company_country(stored: str, country: str, shown: str) -> None:
    assert phone_display(stored, country) == shown


@pytest.mark.parametrize(("q", "digits"), [("0912", "912"), ("+84 912", "84912"), ("0084912", "84912"), ("345 678", "345678"), ("09", ""), ("Lan", "")])
def test_typed_domestic_number_finds_stored_e164(q: str, digits: str) -> None:
    assert phone_search(q) == digits


async def test_domestic_typing_reaches_the_e164_key_on_every_path(who: Who) -> None:
    """Gõ ``0…`` khi thêm lead ⇒ lưu ``+84…``; gõ lại ``0…`` / một đoạn số khi tìm ⇒ ra đúng khách; danh sách hiện dạng nội địa (lớp hiện)."""
    sale = await who("sale1")
    stored = fresh_phone()
    domestic = "0" + stored[3:]
    made = await ok(sale, "crm.create_lead", name=f"Khách gõ nội địa {stored[-4:]}", phone=domestic, offering_code="HSR")
    assert await owner_val("SELECT phone FROM crm.customers WHERE id = %s", (made["customer_id"],)) == stored
    assert (await executor.run(sale, "crm.create_lead", {"name": "Trùng", "phone": stored, "offering_code": "HSR"})).status == "conflict"
    for typed in (domestic, domestic[:7], stored):
        rows = (await q(sale, "crm.leads", q=typed))["rows"]
        assert any(r["id"] == made["lead_id"] for r in rows), typed
    assert phone_display(stored) == domestic


async def test_company_country_changes_display_not_the_key(who: Who) -> None:
    admin = await who("admin")
    try:
        out = await ok(admin, "admin.set_country", country="us")
        assert out["country"] == "US"
        assert "quốc tế" in (await q(admin, "admin.country"))["rows"][0]["rule"]
        assert (await executor.run(admin, "admin.set_country", {"country": "Việt Nam"})).status == "invalid"
    finally:
        await ok(admin, "admin.set_country", country="VN")


def test_manifest_tells_apps_how_to_show_phones() -> None:
    from barista.kernel.principal import system_principal
    from barista.workspaces import manifest
    p = system_principal(uuid.uuid4())
    assert manifest(p, "Mẫu")["tenant"]["phone"] == {"code": "84", "trunk": "0"}
    assert manifest(p, "Mẫu", country="US")["tenant"] == {**manifest(p, "Mẫu", country="US")["tenant"], "country": "US", "phone": None}
