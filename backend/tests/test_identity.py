"""0111: một người một định danh — nhiều kênh liên hệ, quan hệ, sự đồng ý chỉ thêm, gộp bằng con trỏ và tách lại được, không tự gộp."""

import uuid
from typing import Any

import pytest

from barista.kernel import executor
from tests.conftest import fresh_phone, ok, owner_val, q


def _tag() -> str:
    return uuid.uuid4().hex[:6]


async def _party(customer_id: Any) -> Any:
    return await owner_val("SELECT party_id FROM crm.customers WHERE id = %s", (customer_id,))


async def test_every_customer_has_one_party_on_every_insert_path(who: Any) -> None:
    sale, admin = await who("sale1"), await who("admin")
    phone = fresh_phone()
    made = await ok(sale, "crm.create_lead", name=f"Khách {_tag()}", phone=phone, offering_code="HSR")
    pid = await _party(made["customer_id"])
    assert await owner_val("SELECT count(*) FROM parties.identifiers WHERE party_id = %s AND kind = 'phone' AND value = %s", (pid, phone)) == 1
    assert await owner_val("SELECT listed FROM parties.parties WHERE id = %s", (pid,)) is False       # không ngập danh mục chọn
    other = fresh_phone()
    got = await ok(admin, "leadflow.intake", name=f"Khách nạp {_tag()}", phone=other, offering_code="HSR", source="event")
    cid = await owner_val("SELECT customer_id FROM crm.leads WHERE id = %s", (got["lead_id"],))
    assert await _party(cid) is not None
    # đường chèn thẳng (bản cũ đang chạy, dữ liệu ngoài): trigger vẫn tạo định danh
    third = fresh_phone()
    cid = await owner_val("INSERT INTO crm.customers (tenant_id, name, phone) VALUES (%s, 'Chèn thẳng', %s) RETURNING id", (sale.tenant_id, third))
    assert await owner_val("SELECT i.value FROM crm.customers c JOIN parties.identifiers i ON i.party_id = c.party_id AND i.kind = 'phone' WHERE c.id = %s", (cid,)) == third


async def test_a_lead_without_phone_is_accepted_and_needs_some_contact(who: Any) -> None:
    sale = await who("sale1")
    r = await executor.run(sale, "crm.create_lead", {"name": "Không liên hệ", "offering_code": "HSR"})
    assert r.status == "invalid" and "liên hệ" in (r.error or "")
    zalo = f"zalo-{_tag()}"
    made = await ok(sale, "crm.create_lead", name=f"Chỉ có Zalo {_tag()}", contact_kind="zalo", contact_value=zalo, offering_code="HSR")
    assert await owner_val("SELECT phone FROM crm.customers WHERE id = %s", (made["customer_id"],)) is None
    page = await q(sale, "crm.journey", lead_id=made["lead_id"])
    assert zalo in str(page)
    await q(sale, "crm.leads")
    await q(sale, "crm.brief")
    # cùng kênh lần sau ⇒ đúng khách đó, không tạo người mới
    again = await executor.run(sale, "crm.create_lead", {"name": "Tên khác", "contact_kind": "zalo", "contact_value": zalo, "offering_code": "HSR"})
    assert again.status == "conflict"                                                                 # đã có lead cho sản phẩm này
    # có số sau: thành số chính
    phone = fresh_phone()
    await ok(sale, "crm.add_contact", lead_id=made["lead_id"], kind="phone", value=phone)
    assert await owner_val("SELECT phone FROM crm.customers WHERE id = %s", (made["customer_id"],)) == phone


async def test_people_can_be_found_only_by_who_may_see_them(who: Any) -> None:
    sale1, sale3 = await who("sale1"), await who("sale3")
    zalo = f"zalo-{_tag()}"
    await ok(sale1, "crm.create_lead", name=f"Khách của sale1 {_tag()}", contact_kind="zalo", contact_value=zalo, offering_code="HSR")
    r = await executor.run(sale3, "crm.create_lead", {"name": "Thử chen", "contact_kind": "zalo", "contact_value": zalo, "offering_code": "HSR"})
    assert r.status == "conflict" and "khách của người khác" in (r.error or "")                       # báo có chủ, không lộ là của ai
    assert (await executor.query(sale3, "parties.list", {})).status != "ok"


async def test_contact_of_another_person_is_not_moved_and_becomes_a_candidate(who: Any) -> None:
    sale, admin = await who("sale1"), await who("admin")
    a = await ok(sale, "crm.create_lead", name=f"Người A {_tag()}", phone=fresh_phone(), offering_code="HSR")
    phone_b = fresh_phone()
    b = await ok(sale, "crm.create_lead", name=f"Người B {_tag()}", phone=phone_b, offering_code="HSR")
    r = await ok(sale, "crm.add_contact", lead_id=a["lead_id"], kind="phone", value=phone_b)
    assert r["conflict"] and "nghi trùng" in r["summary"]
    assert await owner_val("SELECT phone FROM crm.customers WHERE id = %s", (a["customer_id"],)) != phone_b
    pa, pb = await _party(a["customer_id"]), await _party(b["customer_id"])
    assert any({x["party_id"], x["other_party_id"]} == {str(pa), str(pb)} for x in (await q(admin, "parties.merge_candidates"))["rows"])   # đã lưu thật
    await owner_val("DELETE FROM parties.merge_candidates WHERE party_id = %s AND other_party_id = %s RETURNING 1", (min(pa, pb), max(pa, pb)))
    assert await owner_val("SELECT party_id FROM parties.identifiers WHERE kind = 'phone' AND value = %s AND tenant_id = %s", (phone_b, sale.tenant_id)) == pb
    # một lần nhập mang liên hệ của hai người
    zalo = f"zalo-{_tag()}"
    await ok(admin, "parties.add_identifier", party_id=pa, kind="zalo", value=zalo)
    mixed = await ok(admin, "leadflow.intake", name="Một lần nhập hai người", phone=phone_b, contact_kind="zalo", contact_value=zalo, offering_code="DTP", source="event")
    assert mixed["lead_id"]
    table = await q(admin, "parties.merge_candidates")
    assert any({r["party_id"], r["other_party_id"]} == {str(pa), str(pb)} for r in table["rows"])


async def test_merge_is_a_pointer_and_can_be_undone_money_rows_untouched(who: Any) -> None:
    sale, admin = await who("sale1"), await who("admin")
    a = await ok(sale, "crm.create_lead", name=f"Một người {_tag()}", phone=fresh_phone(), offering_code="HSR")
    b = await ok(sale, "crm.create_lead", name=f"Một người hai số {_tag()}", phone=fresh_phone(), offering_code="HSR")
    pa, pb = await _party(a["customer_id"]), await _party(b["customer_id"])
    out = await ok(admin, "parties.merge", party_id=pa, into_party_id=pb)
    assert out["party_id"] == str(pb)
    assert await _party(a["customer_id"]) == pa                                                       # không chuyển dòng ở module khác
    assert await owner_val("SELECT merged_into FROM parties.parties WHERE id = %s", (pa,)) == pb
    card = await q(admin, "parties.person", party_id=pa)                                              # mở bên bị gộp ⇒ ra người giữ lại, đủ liên hệ hai bên
    assert card["title"].startswith("Một người hai số") and str(card).count("SĐT:") == 2
    r = await executor.run(admin, "parties.merge", {"party_id": pa, "into_party_id": pb})
    assert r.status == "invalid"
    await ok(admin, "parties.unmerge", party_id=pa)
    assert await owner_val("SELECT merged_into FROM parties.parties WHERE id = %s", (pa,)) is None
    assert (await executor.run(sale.as_agent(), "parties.merge", {"party_id": pa, "into_party_id": pb})).status in ("proposed", "denied", "not_found")


async def test_login_party_survives_a_merge_and_two_logins_are_refused(who: Any) -> None:
    sale, admin = await who("sale1"), await who("admin")
    me = await owner_val("SELECT id FROM parties.parties WHERE user_id = %s AND tenant_id = %s", (sale.user_id, sale.tenant_id))
    other = await owner_val("SELECT id FROM parties.parties WHERE user_id = %s AND tenant_id = %s", (admin.user_id, admin.tenant_id))
    made = await ok(sale, "crm.create_lead", name=f"Nhân viên cũng là khách {_tag()}", phone=fresh_phone(), offering_code="HSR")
    p = await _party(made["customer_id"])
    out = await ok(admin, "parties.merge", party_id=me, into_party_id=p)
    assert out["party_id"] == str(me) and await owner_val("SELECT merged_into FROM parties.parties WHERE id = %s", (p,)) == me
    assert (await executor.run(admin, "parties.merge", {"party_id": me, "into_party_id": other})).status == "invalid"


async def test_consent_is_append_only_latest_wins(who: Any) -> None:
    sale, admin = await who("sale1"), await who("admin")
    made = await ok(sale, "crm.create_lead", name=f"Khách đồng ý {_tag()}", phone=fresh_phone(), offering_code="HSR")
    await ok(sale, "crm.record_consent", lead_id=made["lead_id"], purpose="marketing", granted=True, source="Phiếu đăng ký")
    await ok(sale, "crm.record_consent", lead_id=made["lead_id"], purpose="marketing", granted=False, source="Cuộc gọi")
    pid = await _party(made["customer_id"])
    assert await owner_val("SELECT count(*) FROM parties.consents WHERE party_id = %s", (pid,)) == 2
    page = await q(sale, "crm.journey", lead_id=made["lead_id"])
    assert "Nhận tiếp thị: đã rút / từ chối" in str(page)                                          # trạng thái hiệu lực (0112), không phải dòng thô
    assert "Phiếu đăng ký" in str(await q(admin, "parties.person", party_id=pid))
    other = await who("sale3")
    assert (await executor.run(other, "crm.record_consent", {"lead_id": made["lead_id"], "purpose": "marketing", "granted": True})).status == "not_found"


async def test_relations_link_people_without_login_or_phone(who: Any) -> None:
    admin = await who("admin")
    parent = await ok(admin, "parties.create", display_name=f"Phụ huynh {_tag()}")
    child = await ok(admin, "parties.create", display_name=f"Học sinh {_tag()}")
    rel = await ok(admin, "parties.relate", party_id=parent["party_id"], kind="guardian_of", related_party_id=child["party_id"])
    assert (await executor.run(admin, "parties.relate", {"party_id": parent["party_id"], "kind": "guardian_of", "related_party_id": child["party_id"]})).status == "conflict"
    assert (await executor.run(admin, "parties.relate", {"party_id": parent["party_id"], "kind": "pays_for", "related_party_id": parent["party_id"]})).status == "invalid"
    def lists(card: dict[str, Any]) -> dict[str, str]:
        return {b["title"]: str(b["items"]) for b in card["blocks"] if b["type"] == "list"}

    assert "giám hộ" in lists(await q(admin, "parties.person", party_id=child["party_id"]))["Quan hệ"]
    await ok(admin, "parties.end_relation", relation_id=rel["relation_id"], reason="Chuyển giám hộ")
    after = lists(await q(admin, "parties.person", party_id=child["party_id"]))
    assert "Quan hệ" not in after and "Chuyển giám hộ" in after["Quan hệ đã kết thúc"]                  # hết hiệu lực, vết còn (0112)


async def test_directory_lists_chosen_people_and_search_reaches_customers(who: Any) -> None:
    sale, admin = await who("sale1"), await who("admin")
    name = f"Khách tìm được {_tag()}"
    phone = fresh_phone()
    made = await ok(sale, "crm.create_lead", name=name, phone=phone, offering_code="HSR")
    pid = await _party(made["customer_id"])
    assert not any(r["id"] == str(pid) for r in (await q(admin, "parties.list"))["rows"])
    assert not any(o["value"] == str(pid) for o in await q(admin, "parties.options"))
    assert any(r["id"] == str(pid) for r in (await q(admin, "parties.list", q=phone))["rows"])
    await ok(admin, "parties.update", party_id=pid, display_name=name + " sửa", active=True, listed=True)
    assert any(o["value"] == str(pid) for o in await q(admin, "parties.options"))


async def test_a_phone_attached_later_finds_the_same_customer_on_every_path(who: Any) -> None:
    sale, admin = await who("sale1"), await who("admin")
    made = await ok(sale, "crm.create_lead", name=f"Có số sau {_tag()}", contact_kind="zalo", contact_value=f"z-{_tag()}", offering_code="HSR")
    pid = await _party(made["customer_id"])
    phone = fresh_phone()
    await ok(admin, "parties.add_identifier", party_id=pid, kind="phone", value=phone)                # thêm ở danh mục, CRM chưa biết số
    got = await ok(admin, "leadflow.intake", name="Tên trong file", phone=phone, offering_code="DTP", source="event")
    assert str(await owner_val("SELECT customer_id FROM crm.leads WHERE id = %s", (got["lead_id"],))) == str(made["customer_id"])
    assert await owner_val("SELECT phone FROM crm.customers WHERE id = %s", (made["customer_id"],)) == phone


async def test_accountant_picks_payees_but_cannot_read_customer_contacts(who: Any) -> None:
    sale, kt = await who("sale1"), await who("ketoan")
    name, phone = f"Khách riêng tư {_tag()}", fresh_phone()
    made = await ok(sale, "crm.create_lead", name=name, phone=phone, offering_code="HSR")
    pid = await _party(made["customer_id"])
    assert (await executor.query(kt, "crm.leads", {})).status != "ok"
    assert not (await q(kt, "parties.list", q=phone))["rows"] and not (await q(kt, "parties.list", q=name))["rows"]
    for op, inp in (("parties.person", {"party_id": str(pid)}), ("parties.merge_candidates", {})):
        assert (await executor.query(kt, op, inp)).status != "ok"
    assert (await executor.run(kt, "parties.merge", {"party_id": pid, "into_party_id": pid})).status != "ok"
    assert (await executor.run(kt, "parties.add_identifier", {"party_id": pid, "kind": "zalo", "value": "x"})).status != "ok"
    assert (await q(kt, "parties.options")) is not None                                                 # chọn bên nhận tiền vẫn dùng được


async def test_contact_matching_does_not_fuse_people(who: Any, demo: Any) -> None:
    sale = await who("sale1")
    email = f"chung-{_tag()}@mau.test"
    a = await ok(sale, "crm.create_lead", name=f"Mẹ {_tag()}", phone=fresh_phone(), email=email, offering_code="HSR")
    b = await ok(sale, "crm.create_lead", name=f"Bố {_tag()}", phone=fresh_phone(), email=email, offering_code="HSR")   # cùng email, khác số: hai người
    pa, pb = await _party(a["customer_id"]), await _party(b["customer_id"])
    assert a["customer_id"] != b["customer_id"] and pa != pb
    assert await owner_val("SELECT count(*) FROM parties.merge_candidates WHERE party_id = %s AND other_party_id = %s", (min(pa, pb), max(pa, pb))) == 1
    # kênh "khác" là chữ tự do: không dùng để nhận ra người
    text = f"Anh Minh {_tag()}"
    c1 = await ok(sale, "crm.create_lead", name="Người thứ nhất", contact_kind="other", contact_value=text, offering_code="HSR")
    c2 = await ok(sale, "crm.create_lead", name="Người thứ hai", contact_kind="other", contact_value=text, offering_code="HSR")
    assert c1["customer_id"] != c2["customer_id"]


async def test_bad_phone_is_a_clear_error_and_blank_phone_means_none(who: Any) -> None:
    sale = await who("sale1")
    made = await ok(sale, "crm.create_lead", name=f"Khách {_tag()}", phone="", email=f"k-{_tag()}@mau.test", offering_code="HSR")
    r = await executor.run(sale, "crm.add_contact", {"lead_id": made["lead_id"], "kind": "phone", "value": "abc"})
    assert r.status == "invalid" and "SĐT" in (r.error or "")


async def test_consent_and_relation_stay_with_the_right_person_after_unmerge(who: Any) -> None:
    sale, admin = await who("sale1"), await who("admin")
    a = await ok(sale, "crm.create_lead", name=f"A {_tag()}", phone=fresh_phone(), offering_code="HSR")
    b = await ok(sale, "crm.create_lead", name=f"B {_tag()}", phone=fresh_phone(), offering_code="HSR")
    pa, pb = await _party(a["customer_id"]), await _party(b["customer_id"])
    await ok(admin, "parties.merge", party_id=pa, into_party_id=pb)
    await ok(sale, "crm.record_consent", lead_id=a["lead_id"], purpose="marketing", granted=False)
    assert "Nhận tiếp thị: đã rút / từ chối" in str(await q(sale, "crm.journey", lead_id=b["lead_id"]))   # đang gộp: một người
    await ok(admin, "parties.unmerge", party_id=pa)
    assert "Nhận tiếp thị" in str(await q(sale, "crm.journey", lead_id=a["lead_id"]))
    assert "Nhận tiếp thị" not in str(await q(sale, "crm.journey", lead_id=b["lead_id"]))              # tách lại: không dính sang B


async def test_merge_keeps_the_listed_payee_and_the_database_refuses_a_cycle(who: Any) -> None:
    admin = await who("admin")
    payee = await ok(admin, "parties.create", display_name=f"Bên nhận {_tag()}")
    other = await ok(admin, "parties.create", display_name=f"Người khác {_tag()}")
    await owner_val("UPDATE parties.parties SET listed = false WHERE id = %s RETURNING 1", (other["party_id"],))
    out = await ok(admin, "parties.merge", party_id=payee["party_id"], into_party_id=other["party_id"])
    assert out["party_id"] == str(payee["party_id"])
    assert any(o["value"] == str(payee["party_id"]) for o in await q(admin, "parties.options"))
    with pytest.raises(Exception, match="vòng"):
        await owner_val("UPDATE parties.parties SET merged_into = %s WHERE id = %s RETURNING 1", (other["party_id"], payee["party_id"]))
