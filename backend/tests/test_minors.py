"""0112: dữ liệu trẻ em trong định danh — ngày sinh theo quyền liên hệ, đồng ý của ai, trạng thái hiệu lực khi trẻ và người giám hộ khác ý, kiểm người
đồng ý thay, giám hộ tự hết lúc đủ tuổi (vòng quét, có vết), ngưỡng tuổi từ bộ quy định có phiên bản. Không khẳng định tuân thủ."""

import asyncio
import json
import shutil
import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from barista.db import db
from barista.kernel import executor, registry, regulations
from barista.kernel.clock import today
from barista.kernel.principal import system_principal
from barista.modules import books, parties
from barista.modules.parties import AgeRule, age_on, comes_of_age, effective_consent, required_consents
from tests.conftest import Who, fresh_phone, ok, owner_val, q

RULE = AgeRule(7, 18, "x@1", True)
SHIPPED = regulations.ROOT


def _tag() -> str:
    return uuid.uuid4().hex[:6]


def born(years: int, days: int = 0) -> str:
    """Ngày sinh để hôm nay vừa đủ ``years`` tuổi (``days`` > 0: còn thiếu từng ấy ngày)."""
    t = today()
    d = date(t.year - years, t.month, 28 if (t.month, t.day) == (2, 29) else t.day)
    return (d + timedelta(days=days)).isoformat()


async def _person(admin: Any, name: str, birth: str | None = None) -> str:
    pid = (await ok(admin, "parties.create", display_name=f"{name} {_tag()}"))["party_id"]
    if birth:
        await ok(admin, "parties.set_birth_date", party_id=pid, born_on=birth, source="Giấy khai sinh")
    return pid


async def _state(admin: Any, pid: Any, purpose: str = "care") -> str:
    rows = (await q(admin, "parties.consent_state", party_id=pid))["rows"]
    return next(r["state"] for r in rows if r["id"] == purpose)


def test_effective_state_says_who_is_missing_and_a_no_beats_a_yes() -> None:
    both, guardian_only, self_only = ("subject", "guardian"), ("guardian",), ("subject",)
    assert effective_consent(both, None, []) == "not_recorded"
    assert effective_consent(both, True, []) == "missing_guardian"
    assert effective_consent(both, None, [True]) == "missing_child"
    assert effective_consent(both, True, [True]) == "granted"
    assert effective_consent(both, True, [True, False]) == "withdrawn"          # hai người giám hộ khác ý: chưa được phép
    assert effective_consent(both, False, [True]) == "withdrawn"                # trẻ từ chối, cha mẹ đồng ý: chưa được phép
    assert effective_consent(guardian_only, None, [True]) == "granted"
    assert effective_consent(guardian_only, True, []) == "not_recorded"         # dưới tuổi đồng ý kép: lời của trẻ chỉ để biết
    assert effective_consent(guardian_only, False, [True, True]) == "granted"   # …kể cả lời "không" (review 07/11: trước đây không gỡ được)
    assert effective_consent(self_only, None, [True]) == "not_recorded"         # đủ tuổi: lời người giám hộ cũ không còn tính
    assert effective_consent(self_only, True, [False]) == "granted"


def test_required_consents_follow_the_rule_and_unknown_age_is_protective() -> None:
    assert required_consents(5, RULE, True) == ("guardian",)
    assert required_consents(7, RULE, True) == ("subject", "guardian")
    assert required_consents(17, RULE, False) == ("subject", "guardian")        # chưa có ai giám hộ: vẫn thiếu, không tự thành "đủ"
    assert required_consents(18, RULE, True) == ("subject",)
    assert required_consents(None, RULE, True) == ("subject", "guardian")       # chưa biết tuổi mà có người giám hộ ⇒ như trẻ
    assert required_consents(None, RULE, False) == ("subject",)
    assert required_consents(10, None, False) == ("subject",)                   # trước phiên bản quy định đầu tiên: không có luật để áp


def test_age_edges_including_the_29th_of_february() -> None:
    assert age_on(date(2008, 11, 7), date(2026, 11, 7)) == 18 and age_on(date(2008, 11, 8), date(2026, 11, 7)) == 17
    assert age_on(date(2008, 2, 29), date(2026, 2, 28)) == 17 and age_on(date(2008, 2, 29), date(2026, 3, 1)) == 18
    assert comes_of_age(date(2008, 2, 29), 18) == date(2026, 3, 1) and comes_of_age(date(2008, 2, 29), 16) == date(2024, 2, 29)


def test_thresholds_are_a_versioned_rulebook_awaiting_a_lawyer() -> None:
    regulations.reload(SHIPPED)
    v = regulations.packs()[parties.LAW][0]
    assert v.status == "assumption" and not v.verified_by and "LUẬT SƯ" in v.summary.upper()
    assert all(p["basis"] for p in v.params.values()) and len(v.sources) >= 2
    assert parties.age_rule(today()) == AgeRule(7, 18, f"{parties.LAW}@2026-01-01", True)
    assert parties.age_rule(date(2025, 12, 31)) is None
    assert parties.LAW not in books.reg_options()                                # không phải bộ nghĩa vụ của sổ sách


@pytest.fixture
def law_root(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "regulations"
    shutil.copytree(regulations.ROOT, root)
    regulations.reload(root)
    yield root
    regulations.reload(SHIPPED)


async def test_a_new_rule_file_changes_the_threshold_without_code(who: Who, law_root: Path) -> None:
    admin = await who("admin")
    child = await _person(admin, "Trẻ bảy tuổi", born(7))
    parent = await _person(admin, "Mẹ")
    await ok(admin, "parties.relate", party_id=parent, kind="guardian_of", related_party_id=child)
    await ok(admin, "parties.record_consent", party_id=child, purpose="care", granted=True, given_by_party_id=parent)
    assert await _state(admin, child) == "missing_child"                         # 7 tuổi: cần cả trẻ
    newer = json.loads((law_root / parties.LAW / "2026-01-01.json").read_text(encoding="utf-8"))
    day = (today() - timedelta(days=1)).isoformat()
    newer.update(version=day, effective_from=day)
    newer["params"]["dual_consent_from_age"]["value"] = 8
    (law_root / parties.LAW / f"{day}.json").write_text(json.dumps(newer, ensure_ascii=False), encoding="utf-8")
    regulations.reload()
    assert await _state(admin, child) == "granted"                               # văn bản mới: dưới 8 chỉ người giám hộ
    assert f"{parties.LAW}@{day}" in (await q(admin, "parties.consent_state", party_id=child))["note"]


async def test_child_and_guardian_can_disagree_and_the_card_says_so(who: Who) -> None:
    admin = await who("admin")
    child = await _person(admin, "Con", born(10))
    mother, father = await _person(admin, "Mẹ"), await _person(admin, "Bố")
    for g in (mother, father):
        await ok(admin, "parties.relate", party_id=g, kind="guardian_of", related_party_id=child)
    assert await _state(admin, child) == "not_recorded"
    await ok(admin, "parties.record_consent", party_id=child, purpose="care", granted=True, source="Trẻ ký phiếu")
    assert await _state(admin, child) == "missing_guardian"
    await ok(admin, "parties.record_consent", party_id=child, purpose="care", granted=True, given_by_party_id=mother)
    assert await _state(admin, child) == "granted"
    await ok(admin, "parties.record_consent", party_id=child, purpose="care", granted=False, given_by_party_id=father)
    assert await _state(admin, child) == "withdrawn"
    rows = (await q(admin, "parties.consent_state", party_id=child))["rows"]
    who_said = next(r["who"] for r in rows if r["id"] == "care")
    assert "Chính người này: đồng ý" in who_said and ": không" in who_said
    assert await owner_val("SELECT array_agg(given_as ORDER BY at) FROM parties.consents WHERE party_id = %s", (child,)) == ["subject", "guardian", "guardian"]
    card = await q(admin, "parties.person", party_id=child)
    assert "Chăm sóc & nhắc lịch: đã rút / từ chối" in str(card) and "Ngày sinh" in str(card)


async def test_only_an_active_guardian_may_consent_for_someone_and_pays_for_is_not_enough(who: Who) -> None:
    admin = await who("admin")
    child = await _person(admin, "Con", born(9))
    stranger, payer, guardian = await _person(admin, "Người lạ"), await _person(admin, "Người trả tiền"), await _person(admin, "Người giám hộ")
    await ok(admin, "parties.relate", party_id=payer, kind="pays_for", related_party_id=child)
    for giver in (stranger, payer):
        r = await executor.run(admin, "parties.record_consent", {"party_id": child, "purpose": "care", "granted": True, "given_by_party_id": giver})
        assert r.status == "invalid" and "người giám hộ" in r.error, r.error
    rel = await ok(admin, "parties.relate", party_id=guardian, kind="guardian_of", related_party_id=child)
    await ok(admin, "parties.record_consent", party_id=child, purpose="care", granted=True, given_by_party_id=guardian)
    await ok(admin, "parties.record_consent", party_id=child, purpose="care", granted=True)
    assert await _state(admin, child) == "granted"
    assert set(await q(system_principal(admin.tenant_id), "parties.guardians", party_id=child)) == {guardian}   # pays_for không có ở đây
    await ok(admin, "parties.end_relation", relation_id=rel["relation_id"])
    assert await _state(admin, child) == "missing_guardian"                      # dòng cũ còn làm vết nhưng không còn tính
    assert (await executor.run(admin, "parties.record_consent", {"party_id": child, "purpose": "care", "granted": True,
                                                                 "given_by_party_id": guardian})).status == "invalid"


async def test_under_the_dual_age_only_the_guardian_consents(who: Who) -> None:
    admin, sale = await who("admin"), await who("sale1")
    child, parent = await _person(admin, "Bé", born(5)), await _person(admin, "Cha")
    for granted in (True, False):   # lời của trẻ dưới 7 tuổi không có hiệu lực: không ghi, để khỏi tưởng là đã mở / đã chặn
        r = await executor.run(admin, "parties.record_consent", {"party_id": child, "purpose": "marketing", "granted": granted})
        assert r.status == "invalid" and "7 tuổi" in r.error and "người giám hộ" in r.error
    await ok(admin, "parties.relate", party_id=parent, kind="guardian_of", related_party_id=child)
    await ok(admin, "parties.record_consent", party_id=child, purpose="care", granted=True, given_by_party_id=parent)
    assert await _state(admin, child) == "granted" and await _state(admin, child, "marketing") == "not_recorded"
    # CRM luôn ghi như chính khách: khách là trẻ dưới 7 tuổi ⇒ cùng một luật, báo cần người giám hộ
    made = await ok(sale, "crm.create_lead", name=f"Khách nhỏ {_tag()}", phone=fresh_phone(), offering_code="HSR")
    pid = await owner_val("SELECT party_id FROM crm.customers WHERE id = %s", (made["customer_id"],))
    await ok(admin, "parties.set_birth_date", party_id=pid, born_on=born(6))
    r = await executor.run(sale, "crm.record_consent", {"lead_id": made["lead_id"], "purpose": "care", "granted": False})
    assert r.status == "invalid" and "người giám hộ" in r.error


async def test_a_young_childs_earlier_no_does_not_block_the_guardians_yes(who: Who) -> None:
    """Probe của review 07/11: trẻ 5 tuổi nói "không" (ghi khi chưa biết tuổi), hai người giám hộ cùng "đồng ý" ⇒ đủ đồng ý; lời của trẻ vẫn hiện để biết."""
    admin = await who("admin")
    child = await _person(admin, "Bé ghi trước")
    await ok(admin, "parties.record_consent", party_id=child, purpose="care", granted=False, source="Bé nói không")
    mother, father = await _person(admin, "Mẹ"), await _person(admin, "Bố")
    for g in (mother, father):
        await ok(admin, "parties.relate", party_id=g, kind="guardian_of", related_party_id=child)
        await ok(admin, "parties.record_consent", party_id=child, purpose="care", granted=True, given_by_party_id=g)
    assert await _state(admin, child) == "withdrawn"                             # chưa biết tuổi + có người giám hộ: xét như trẻ cần cả hai
    await ok(admin, "parties.set_birth_date", party_id=child, born_on=born(5))
    assert await _state(admin, child) == "granted"
    row = next(r for r in (await q(admin, "parties.consent_state", party_id=child))["rows"] if r["id"] == "care")
    assert "Chính người này: không" in row["who"]


async def test_reopening_a_guardianship_does_not_revive_earlier_consents(who: Who) -> None:
    admin = await who("admin")
    child, parent = await _person(admin, "Con", born(10)), await _person(admin, "Mẹ")
    first = await ok(admin, "parties.relate", party_id=parent, kind="guardian_of", related_party_id=child)
    await ok(admin, "parties.record_consent", party_id=child, purpose="care", granted=True, given_by_party_id=parent)
    await ok(admin, "parties.record_consent", party_id=child, purpose="care", granted=True)
    assert await _state(admin, child) == "granted"
    await ok(admin, "parties.end_relation", relation_id=first["relation_id"], reason="Chuyển giám hộ")
    await ok(admin, "parties.relate", party_id=parent, kind="guardian_of", related_party_id=child)
    assert await _state(admin, child) == "missing_guardian"                      # lời cũ cho trong quan hệ trước: không sống lại
    await ok(admin, "parties.record_consent", party_id=child, purpose="care", granted=True, given_by_party_id=parent)
    assert await _state(admin, child) == "granted"


async def test_unknown_age_with_a_guardian_is_treated_as_a_child(who: Who) -> None:
    admin = await who("admin")
    person, parent = await _person(admin, "Chưa rõ tuổi"), await _person(admin, "Phụ huynh")
    await ok(admin, "parties.record_consent", party_id=person, purpose="care", granted=True)
    assert await _state(admin, person) == "granted"
    assert "Chưa có ngày sinh" in (await q(admin, "parties.consent_state", party_id=person))["note"]
    await ok(admin, "parties.relate", party_id=parent, kind="guardian_of", related_party_id=person)
    assert await _state(admin, person) == "missing_guardian"


async def test_birth_date_is_contacts_data_not_directory_data(who: Who) -> None:
    admin, kt, sale = await who("admin"), await who("ketoan"), await who("sale1")
    made = await ok(sale, "crm.create_lead", name=f"Khách nhỏ {_tag()}", phone=fresh_phone(), offering_code="HSR")
    pid = await owner_val("SELECT party_id FROM crm.customers WHERE id = %s", (made["customer_id"],))
    await ok(admin, "parties.set_birth_date", party_id=pid, born_on=born(12))
    async with db.tenant_tx(kt, read_only=True) as conn:                          # kế toán có quyền danh mục: RLS vẫn không cho đọc ngày sinh
        assert (await (await conn.execute("SELECT count(*) AS n FROM parties.birth_dates")).fetchone() or {})["n"] == 0
    assert (await executor.run(kt, "parties.set_birth_date", {"party_id": pid, "born_on": born(12)})).status == "denied"
    assert (await executor.query(kt, "parties.consent_state", {"party_id": pid})).status == "denied"
    await ok(sale, "crm.record_consent", lead_id=made["lead_id"], purpose="care", granted=True)
    page = str(await q(sale, "crm.journey", lead_id=made["lead_id"]))
    assert "Chăm sóc & nhắc lịch: thiếu đồng ý của người giám hộ" in page and born(12)[:4] not in page   # sale thấy trạng thái, không thấy ngày sinh
    logged = await owner_val("SELECT input->>'born_on' FROM core.audit_log WHERE command = 'parties.set_birth_date' AND input->>'party_id' = %s",
                             (str(pid),))
    assert logged is None or born(12) not in logged                               # nhật ký không chép ngày sinh
    assert (await executor.run(admin, "parties.set_birth_date", {"party_id": pid, "born_on": (today() + timedelta(days=1)).isoformat()})).status == "invalid"
    org = (await ok(admin, "parties.create", display_name=f"Tổ chức {_tag()}", kind="organization"))["party_id"]
    assert (await executor.run(admin, "parties.set_birth_date", {"party_id": org, "born_on": born(30)})).status == "invalid"
    await ok(admin, "parties.set_birth_date", party_id=pid, born_on=None)
    assert await owner_val("SELECT count(*) FROM parties.birth_dates WHERE party_id = %s", (pid,)) == 0
    assert (await executor.run(admin.as_agent(), "parties.set_birth_date", {"party_id": pid, "born_on": born(12)})).status == "proposed"


async def _guardian_case(admin: Any, years: int, days: int = 0) -> dict[str, Any]:
    """Quan hệ ghi khi còn nhỏ (chưa có ngày sinh), ngày sinh ghi sau — như hồ sơ thật qua nhiều năm."""
    child, parent = await _person(admin, "Học viên"), await _person(admin, "Cha mẹ")
    g = await ok(admin, "parties.relate", party_id=parent, kind="guardian_of", related_party_id=child)
    p = await ok(admin, "parties.relate", party_id=parent, kind="pays_for", related_party_id=child)
    await ok(admin, "parties.set_birth_date", party_id=child, born_on=born(years, days))
    return {"child": child, "parent": parent, "guardian": g["relation_id"], "pays": p["relation_id"]}


async def test_guardianship_ends_at_majority_with_a_trail_and_pays_for_survives(who: Who) -> None:
    admin = await who("admin")
    sweep = system_principal(admin.tenant_id)
    adult, young = await _guardian_case(admin, 18), await _guardian_case(admin, 18, days=1)
    r = await executor.run(admin, "parties.record_consent", {"party_id": adult["child"], "purpose": "care", "granted": True, "given_by_party_id": adult["parent"]})
    assert r.status == "invalid" and "đủ 18 tuổi" in r.error
    await owner_val("""INSERT INTO parties.consents (tenant_id, party_id, purpose, granted, given_by_party_id, at)
                       VALUES (%s, %s, 'care', true, %s, now() - interval '1 year') RETURNING 1""", (admin.tenant_id, adult["child"], adult["parent"]))   # ghi hồi còn nhỏ
    assert await q(sweep, "parties.guardians", party_id=adult["child"]) == []       # đủ tuổi: ngay cả trước vòng quét
    assert await _state(admin, adult["child"]) == "not_recorded"                   # lời người giám hộ cũ không còn tính
    assert (await executor.run(sweep, "parties.end_guardians_of_age", {})).ok
    rel = await owner_val("SELECT jsonb_build_object('on', ended_on, 'reason', ended_reason, 'rule', ended_rule, 'by', ended_by) FROM parties.relations WHERE id = %s",
                          (adult["guardian"],))
    assert rel["on"] == today().isoformat() and "Đủ 18 tuổi" in rel["reason"] and rel["rule"] == f"{parties.LAW}@2026-01-01:majority_age" and rel["by"] is None
    assert await owner_val("SELECT ended_on FROM parties.relations WHERE id = %s", (adult["pays"],)) is None     # trả tiền giữ nguyên
    assert await owner_val("SELECT ended_on FROM parties.relations WHERE id = %s", (young["guardian"],)) is None  # còn một ngày: chưa
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND kind = 'guardian_ended' AND dedupe_key = %s",
                           (admin.user_id, f"guardian_ended:{adult['guardian']}")) == 1
    assert await owner_val("SELECT count(*) FROM core.events WHERE tenant_id = %s AND type = 'parties.guardian_ended' AND payload->>'relation_id' = %s",
                           (admin.tenant_id, adult["guardian"])) == 1
    assert "Đủ 18 tuổi" in str(await q(admin, "parties.person", party_id=adult["child"]))
    again = await executor.run(sweep, "parties.end_guardians_of_age", {})
    assert again.ok and again.output == {"ended": 0}
    r = await executor.run(admin, "parties.relate", {"party_id": adult["parent"], "kind": "guardian_of", "related_party_id": adult["child"]})
    assert r.status == "invalid" and "đủ 18 tuổi" in r.error                     # đủ tuổi: không ghi lại giám hộ để vòng quét kết thúc lần nữa


async def test_two_sweeps_at_once_end_and_notify_once(who: Who) -> None:
    admin = await who("admin")
    case = await _guardian_case(admin, 19)
    sweep = system_principal(admin.tenant_id)
    a, b = await asyncio.gather(*(executor.run(sweep, "parties.end_guardians_of_age", {}) for _ in range(2)))
    assert a.ok and b.ok
    assert await owner_val("SELECT count(*) FROM core.events WHERE type = 'parties.guardian_ended' AND payload->>'relation_id' = %s", (case["guardian"],)) == 1
    await _guardian_case(admin, 20)
    due = registry.SWEEPS["parties.end_guardians_of_age"]                          # bộ điều phối tự thấy lệnh khai sweep= (không chạy mọi vòng quét ở đây)
    async with db.owner.connection() as conn:
        assert admin.tenant_id in {r["tenant_id"] for r in await (await conn.execute(due)).fetchall()}


async def test_merged_identities_are_one_person_for_age_and_guardianship_and_unmerge_is_clean(who: Who) -> None:
    admin = await who("admin")
    child_a, child_b = await _person(admin, "Con định danh A", born(10)), await _person(admin, "Con định danh B")
    parent_a, parent_b = await _person(admin, "Mẹ định danh A"), await _person(admin, "Mẹ định danh B")
    await ok(admin, "parties.relate", party_id=parent_a, kind="guardian_of", related_party_id=child_b)   # quan hệ ghi ở định danh B
    await ok(admin, "parties.merge", party_id=child_b, into_party_id=child_a)
    await ok(admin, "parties.merge", party_id=parent_b, into_party_id=parent_a)
    await ok(admin, "parties.record_consent", party_id=child_a, purpose="care", granted=True, given_by_party_id=parent_b)   # giám hộ qua định danh khác
    await ok(admin, "parties.record_consent", party_id=child_b, purpose="care", granted=True)
    assert await _state(admin, child_a) == "granted" and await _state(admin, child_b) == "granted"
    assert await owner_val("SELECT given_by_party_id FROM parties.consents WHERE party_id = %s AND given_as = 'guardian'", (child_a,)) == uuid.UUID(parent_b)
    await ok(admin, "parties.unmerge", party_id=parent_b)
    assert await _state(admin, child_a) == "missing_guardian"                    # tách mẹ: dòng ghi bằng định danh B không còn là lời người giám hộ
    await ok(admin, "parties.unmerge", party_id=child_b)
    assert await _state(admin, child_b) == "missing_guardian"                    # quan hệ và lời của trẻ ở lại B; B chưa có ngày sinh mà có người giám hộ
    assert await _state(admin, child_a) == "not_recorded"                        # A giữ ngày sinh, không còn dòng nào tính được
    # hai định danh ghi khác ngày sinh: gộp có cảnh báo, tính theo người trẻ hơn
    older = await _person(admin, "Cùng người ghi tuổi khác", born(30))
    out = await ok(admin, "parties.merge", party_id=older, into_party_id=child_a)
    assert "ngày sinh khác nhau" in out["warning"]
    assert (await q(admin, "parties.consent_state", party_id=older))["note"].count("ngày trẻ hơn") == 1


async def test_a_child_without_contacts_is_added_with_their_guardian_in_one_step(who: Who) -> None:
    """Trẻ không có SĐT / email: ``ensure_person`` từ chối; ``add_dependant`` tạo định danh ngoài danh mục chọn + ngày sinh + giám hộ cùng lúc."""
    admin, kt = await who("admin"), await who("ketoan")
    parent = await _person(admin, "Phụ huynh")
    made = await ok(admin, "parties.add_dependant", name=f"Bé không liên hệ {_tag()}", born_on=born(10), guardian_party_id=parent, source="Giấy khai sinh")
    child = made["party_id"]
    assert await owner_val("SELECT count(*) FROM parties.identifiers WHERE party_id = %s", (child,)) == 0
    assert await owner_val("SELECT listed FROM parties.parties WHERE id = %s", (child,)) is False
    assert await q(system_principal(admin.tenant_id), "parties.guardians", party_id=child) == [parent]
    assert await _state(admin, child) == "not_recorded"                          # 10 tuổi: cần cả trẻ và người giám hộ
    await ok(admin, "parties.record_consent", party_id=child, purpose="care", granted=True, given_by_party_id=parent)
    assert await _state(admin, child) == "missing_child"
    await ok(admin, "parties.record_consent", party_id=child, purpose="care", granted=True)
    assert await _state(admin, child) == "granted"
    small = (await ok(admin, "parties.add_dependant", name=f"Bé nhỏ {_tag()}", born_on=born(4), guardian_party_id=parent))["party_id"]
    await ok(admin, "parties.record_consent", party_id=small, purpose="care", granted=True, given_by_party_id=parent)
    assert await _state(admin, small) == "granted"                               # dưới 7 tuổi: người giám hộ đồng ý là đủ
    r = await executor.run(admin, "parties.add_dependant", {"name": "Đã lớn", "born_on": born(18), "guardian_party_id": parent})
    assert r.status == "invalid" and "đủ 18 tuổi" in r.error
    assert await owner_val("SELECT count(*) FROM parties.parties WHERE tenant_id = %s AND display_name = 'Đã lớn'", (admin.tenant_id,)) == 0
    assert (await executor.run(kt, "parties.add_dependant", {"name": "X", "born_on": born(9), "guardian_party_id": parent})).status == "denied"
    agent = await executor.run(admin.as_agent(), "parties.add_dependant", {"name": "Y", "born_on": born(9), "guardian_party_id": parent})
    assert agent.status == "proposed"
