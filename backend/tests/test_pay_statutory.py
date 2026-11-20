"""0150 lát 3 "Luật VN" — hàm tính THUẦN (không DB): bảo hiểm có trần theo vùng, đoàn phí, thuế TNCN luỹ tiến luỹ kế theo tháng chi với giảm trừ gia
cảnh, lương theo ngày công từ bảng công đã chốt, làm thêm giờ; quy tắc chưa xác minh giữ phiếu thật, kỳ chạy bóng tính và đánh dấu. Bộ quy định
phát hành phải nạp được và mọi quy tắc lát 3 ở trạng thái chờ xác minh, có căn cứ."""

import json
import shutil
import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest

from barista import seed_pay
from barista.db import db
from barista.kernel import executor, regulations
from barista.kernel.clock import today
from barista.modules.pay import engine
from barista.modules.pay.runs import law_on
from tests.conftest import edge, ok, owner_val, q

MONTHLY = {"code": "LUONG", "version": 1, "label": "Lương cơ bản", "kind": "earning", "calculator": "prorata", "taxable": True,
           "expense_account": "expense:payroll", "source": ""}
OT = {**MONTHLY, "code": "LAM_THEM", "label": "Làm thêm giờ", "calculator": "overtime"}


def _verified(law: dict[str, Any]) -> dict[str, Any]:
    return {k: {**v, "status": "verified", "verified_by": "Kế toán thử (test)"} for k, v in law.items()}


def _inputs(*, law: dict[str, Any], terms: dict[str, Any] | None = None, entries: list[dict[str, Any]] | None = None, kind: str = "regular",
            prior: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"version": 2, "run_kind": kind, "period": "2026-10-01", "period_end": "2026-10-31", "pay_date": "2026-11-05",
            "party": {"id": "p1", "name": "A"},
            "terms": terms or {"contract_kind": "indefinite", "term_months": None, "insured_vnd": 20_000_000, "work_region": "I", "union_member": False,
                               "dependants": 1},
            "entries": entries if entries is not None else [
                {"id": "f", "work_date": "2026-10-01", "kind": "amount", "unit": "", "quantity": "0", "amount_vnd": 30_000_000, "role_key": "",
                 "component": "LUONG", "source": "pay.fixed"},
                {"id": "d1", "work_date": "2026-10-31", "kind": "qty", "unit": "day", "quantity": "22", "amount_vnd": None, "role_key": "days.paid",
                 "component": "", "source": "timekeeping.close"}],
            "rates": [], "components": {"LUONG": MONTHLY, "LAM_THEM": OT}, "advances_vnd": 0, "law": law, "prior": prior or {}}


def test_shipped_rules_load_with_sources_and_all_wait_for_the_accountant() -> None:
    regulations.reload()
    for pack in ("vn-payroll", "vn-labour"):
        vs = regulations.packs()[pack]
        assert [v.version for v in vs] == ["2026-01-01", "2026-07-01"], pack
        for v in vs:
            assert v.sources and v.status == "assumption"
            for code, p in v.params.items():
                assert p["status"] == "assumption" and not p["verified_by"] and len(p["basis"]) > 40, (pack, v.version, code)
    law = law_on(date(2026, 11, 5), date(2026, 10, 31))
    assert law["INSURANCE_BASE"]["base_salary_vnd"] == 2_530_000 and law["PIT_FLAT_10"]["min_payment_vnd"] == 5_000_000
    assert law_on(date(2026, 6, 30))["INSURANCE_BASE"]["base_salary_vnd"] == 2_340_000
    # thuế theo NGÀY CHI, bảo hiểm theo THÁNG CÔNG: lương tháng 6 chi ngày 05/07 ⇒ ngưỡng 10% mới, trần bảo hiểm cũ
    mixed = law_on(date(2026, 7, 5), date(2026, 6, 30))
    assert mixed["PIT_FLAT_10"]["ref"].startswith("vn-payroll@2026-07-01") and mixed["INSURANCE_BASE"]["ref"].startswith("vn-payroll@2026-01-01")
    holidays = {d["date"] for d in regulations.version_on("vn-labour", date(2026, 11, 1)).params["PUBLIC_HOLIDAYS"]["value"]["dates"]}  # type: ignore[union-attr]
    assert "2026-11-24" in holidays and "2026-09-02" in holidays


def test_progressive_brackets() -> None:
    b = law_on(date(2026, 11, 5))["PIT_PROGRESSIVE"]["brackets"]
    assert engine.progressive(0, b) == 0 and engine.progressive(10_000_000, b) == 500_000
    assert engine.progressive(30_000_000, b) == 500_000 + 2_000_000
    assert engine.progressive(120_000_000, b) == 500_000 + 2_000_000 + 6_000_000 + 12_000_000 + 7_000_000


def test_labour_contract_insurance_union_and_progressive_tax() -> None:
    law = _verified(law_on(date(2026, 11, 5), date(2026, 10, 31)))
    out = engine.compute(_inputs(law=law))
    assert not out["held"], out["held"]
    by = {x["component"]: x for x in out["lines"]}
    assert by["LUONG"]["amount_vnd"] == 30_000_000 and "22/22 ngày công" in by["LUONG"]["basis"]
    assert (by["SI_EMPLOYEE"]["amount_vnd"], by["HI_EMPLOYEE"]["amount_vnd"], by["UI_EMPLOYEE"]["amount_vnd"]) == (1_600_000, 300_000, 200_000)
    assert by["SI_EMPLOYEE"]["account"] == "payable:insurance" and by["SI_EMPLOYEE"]["kind"] == "withholding"
    # 30tr − bảo hiểm 2,1tr − bản thân 15,5tr − 1 người phụ thuộc 6,2tr = 6,2tr ⇒ 5%
    assert by["PIT_PROGRESSIVE"]["amount_vnd"] == 310_000 and "1 người phụ thuộc" in by["PIT_PROGRESSIVE"]["basis"]
    assert "PIT_DEDUCTIONS" in by["PIT_PROGRESSIVE"]["rule_ref"]
    assert out["net_vnd"] == 30_000_000 - 2_100_000 - 310_000
    employer = [x for x in out["lines"] if x["kind"] == "employer"]
    assert sum(x["amount_vnd"] for x in employer) == 3_500_000 + 600_000 + 200_000 + 400_000 and "UNION_MEMBER_FEE" not in by
    assert out["figures"] == {"taxable_vnd": 30_000_000, "insurance_vnd": 2_100_000, "pit_vnd": 310_000, "employer_vnd": 4_700_000, "insured": True}
    # trần: BHXH / BHYT 20 × lương cơ sở (50,6tr), BHTN 20 × lương tối thiểu vùng I (106,2tr); đoàn viên: 1%, tối đa 10% lương cơ sở
    rich = engine.compute(_inputs(law=law, terms={"contract_kind": "indefinite", "term_months": None, "insured_vnd": 120_000_000, "work_region": "I",
                                                  "union_member": True, "dependants": 0}))
    by = {x["component"]: x for x in rich["lines"]}
    assert by["SI_EMPLOYEE"]["amount_vnd"] == 50_600_000 * 8 // 100 and "trần" in by["SI_EMPLOYEE"]["basis"]
    assert by["UI_EMPLOYEE"]["amount_vnd"] == 106_200_000 // 100 and by["UNION_MEMBER_FEE"]["amount_vnd"] == 253_000


def test_unverified_rules_hold_a_real_slip_and_shadow_marks_every_number() -> None:
    law = law_on(date(2026, 11, 5), date(2026, 10, 31))
    real = engine.compute(_inputs(law=law))
    assert any("chưa xác minh" in h and "PIT_PROGRESSIVE" in h for h in real["held"])
    assert [x["kind"] for x in real["lines"]] == ["earning"]                                # không dòng luật nào ở kỳ thật
    shadow = engine.compute(_inputs(law=law, kind="shadow"))
    assert not shadow["held"] and all(x["unverified"] for x in shadow["lines"] if x["kind"] in ("withholding", "employer"))
    assert {r.split(":")[-1] for r in shadow["unverified"]} >= {"PIT_PROGRESSIVE", "PIT_DEDUCTIONS", "SI_EMPLOYEE", "INSURANCE_BASE"}


def test_cumulative_tax_in_the_pay_month_matches_one_slip() -> None:
    """Hai kỳ phụ chi cùng tháng ra cùng tổng thuế như một phiếu (thuế luỹ kế theo tháng chi); bảo hiểm chỉ một lần mỗi tháng công."""
    law = _verified(law_on(date(2026, 11, 5), date(2026, 10, 31)))
    one = engine.compute(_inputs(law=law))
    bonus = [{"id": "m", "work_date": "2026-10-20", "kind": "amount", "unit": "", "quantity": "0", "amount_vnd": 20_000_000, "role_key": "",
              "component": "THUONG", "source": "pay.manual"}]
    comps = {"THUONG": {**MONTHLY, "code": "THUONG", "label": "Thưởng", "calculator": "manual"}}
    second = _inputs(law=law, entries=bonus, prior={**one["figures"], "insured_period": True})
    second["components"] = comps
    two = engine.compute(second)
    by = {x["component"]: x for x in two["lines"]}
    assert by["SI_EMPLOYEE"]["amount_vnd"] == 0 and "đã đóng" in by["SI_EMPLOYEE"]["basis"]
    together = engine.compute({**_inputs(law=law, entries=[*_inputs(law=law)["entries"], *bonus]), "components": {**_inputs(law=law)["components"], **comps}})
    assert one["figures"]["pit_vnd"] + two["figures"]["pit_vnd"] == together["figures"]["pit_vnd"]


def test_days_and_overtime_from_the_closed_timesheet() -> None:
    law = _verified(law_on(date(2026, 11, 5), date(2026, 10, 31)))
    entries = [{"id": "f", "work_date": "2026-10-01", "kind": "amount", "unit": "", "quantity": "0", "amount_vnd": 22_000_000, "role_key": "",
                "component": "LUONG", "source": "pay.fixed"},
               {"id": "p", "work_date": "2026-10-31", "kind": "qty", "unit": "day", "quantity": "20", "amount_vnd": None, "role_key": "days.paid",
                "component": "", "source": "timekeeping.close"},
               {"id": "u", "work_date": "2026-10-31", "kind": "qty", "unit": "day", "quantity": "2", "amount_vnd": None, "role_key": "days.unpaid",
                "component": "", "source": "timekeeping.close"},
               {"id": "o", "work_date": "2026-10-31", "kind": "qty", "unit": "hour", "quantity": "4", "amount_vnd": None, "role_key": "ot.150",
                "component": "", "source": "timekeeping.close"}]
    out = engine.compute(_inputs(law=law, entries=entries))
    assert not out["held"], out["held"]
    by = {x["component"]: x for x in out["lines"]}
    assert by["LUONG"]["amount_vnd"] == 20_000_000 and "20/22 ngày công" in by["LUONG"]["basis"]
    # đơn giá giờ = 22tr / 22 ngày / 8 giờ = 125.000; 4 giờ × 150% = 750.000, phần chênh 250.000 miễn thuế
    assert by["LAM_THEM"]["amount_vnd"] == 750_000 and "OT_PAY" in by["LAM_THEM"]["rule_ref"]
    assert out["figures"]["taxable_vnd"] == 20_750_000 - 250_000 and "PIT_OT_EXEMPT" in by["PIT_PROGRESSIVE"]["rule_ref"]
    # chưa chốt bảng công ⇒ khoản theo ngày công không đoán
    held = engine.compute(_inputs(law=law, entries=entries[:1]))
    assert any("bảng công" in h for h in held["held"])
    # sàn lương: lương đóng bảo hiểm dưới lương tối thiểu vùng ⇒ dừng
    low = engine.compute(_inputs(law=law, terms={"contract_kind": "indefinite", "term_months": None, "insured_vnd": 4_000_000, "work_region": "I",
                                                 "union_member": False, "dependants": 0}))
    assert any("tối thiểu vùng" in h for h in low["held"])


def test_collaborator_path_is_unchanged_and_probation_waits_for_the_accountant() -> None:
    law = _verified(law_on(date(2026, 11, 5), date(2026, 10, 31)))
    comp = {**MONTHLY, "code": "TRA", "calculator": "manual"}
    entries = [{"id": "m", "work_date": "2026-10-20", "kind": "amount", "unit": "", "quantity": "0", "amount_vnd": 6_000_000, "role_key": "",
                "component": "TRA", "source": "pay.manual"}]
    svc = engine.compute({**_inputs(law=law, entries=entries, terms={"contract_kind": "service", "term_months": None}), "components": {"TRA": comp}})
    assert [x["component"] for x in svc["lines"]] == ["TRA", "PIT_FLAT_10"] and svc["net_vnd"] == 5_400_000
    prob = engine.compute({**_inputs(law=law, entries=entries, terms={"contract_kind": "probation", "term_months": None}), "components": {"TRA": comp}})
    assert any("thử việc" in h for h in prob["held"])


# ── Đầu cuối (DB): kế toán đã xác minh mọi quy tắc ⇒ kỳ thật của văn phòng chốt được, khấu trừ theo luật ghi sổ qua Tài chính ──

@pytest.fixture
def all_verified(tmp_path: Path) -> Iterator[None]:
    root = tmp_path / "regulations"
    shutil.copytree(regulations.ROOT, root)
    shipped = regulations.ROOT
    for f in (root / "vn-payroll").glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        for p in d["params"].values():
            p |= {"status": "verified", "verified_by": "Kế toán thử (test), 10/11/2026"}
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    regulations.reload(root)
    yield
    regulations.reload(shipped)


async def test_verified_rules_lock_an_office_run_with_insurance_and_pit_posted(all_verified: None) -> None:
    co = await seed_pay.office(f"ps{uuid.uuid4().hex[:6]}.demo", "Văn phòng chốt lương thử (test)", runs=False)
    w, tid = co["who"], co["tenant_id"]
    run = await ok(w["luong"], "pay.draft_run", period=co["period"].isoformat(), pay_date=today().isoformat())
    slips = {s["name"]: s for s in await owner_val("SELECT jsonb_agg(to_jsonb(s)) FROM pay.slips s WHERE run_id = %s", (run["run_id"],))}
    assert not slips["Hoàng Minh An"]["held_reason"] and "bảng công" in slips["Trịnh Thu Trang"]["held_reason"]
    sub = await ok(w["luong"], "pay.submit_run", run_id=run["run_id"])
    done = await ok(edge(w["ketoan"]), "core.decide", approval_id=str(sub["approval_id"]), approve=True)
    assert done["status"] == "executed", done
    an = await owner_val("SELECT to_jsonb(s) FROM pay.slips s WHERE run_id = %s AND name = 'Hoàng Minh An'", (run["run_id"],))
    ins = an["figures"]["insurance_vnd"]
    assert ins == 15_000_000 * 105 // 1000 and an["payable_id"]                           # 10,5% trên lương đóng bảo hiểm 15tr
    # khấu trừ theo luật thành dòng nợ phải trả ở sổ cái (thuế, bảo hiểm, đoàn phí); sổ cân
    posted = await owner_val("""SELECT jsonb_object_agg(a.code, s.v) FROM (SELECT p.account_id, sum(p.amount_vnd) AS v FROM ledger.postings p
                                JOIN ledger.journals j ON j.id = p.journal_id WHERE j.tenant_id = %s GROUP BY p.account_id) s
                                JOIN ledger.accounts a ON a.id = s.account_id""", (tid,))
    withheld = await owner_val("""SELECT sum(amount_vnd) FROM pay.slip_lines l JOIN pay.slips s ON s.id = l.slip_id
                                  WHERE s.run_id = %s AND l.account = 'payable:insurance'""", (run["run_id"],))
    assert -posted["payable:insurance"] == withheld > 0
    assert "payable:union" in posted and "tax:withheld" in posted and sum(posted.values()) == 0
    assert "expense:insurance" not in posted                                                # phần doanh nghiệp đóng: chưa ghi sổ (cần lệnh của Tài chính)
    # phát lại vàng từ bản chụp
    lines = await owner_val("SELECT jsonb_agg(to_jsonb(l) ORDER BY l.seq) FROM pay.slip_lines l WHERE l.slip_id = %s", (an["id"],))
    out = engine.compute(an["inputs"])
    keys = ("seq", "component", "label", "kind", "amount_vnd", "basis", "rule_ref", "account", "unverified")
    assert [{k: x[k] for k in keys} for x in out["lines"]] == [{k: x[k] for k in keys} for x in lines]
    # người lao động thấy phiếu của mình + thẻ "vì sao khác kỳ trước" (chưa có kỳ trước ⇒ nói rõ)
    card = await q(w["nvan"], "pay.compare_slips")
    assert "chưa có kỳ trước" in card["subtitle"] and card["blocks"][0]["items"][0]["value"] == an["net_vnd"]
    assert await owner_val("SELECT count(*) FROM pay.slips WHERE run_id = %s AND owner_id IS NOT NULL", (run["run_id"],)) == 4


def test_review_fixes_short_contracts_insure_standard_days_divide_and_untaxed_overtime_is_not_exempted_twice() -> None:
    """Review 10/11: (3) hợp đồng 1–<3 tháng vẫn đóng bảo hiểm dù thuế là 10% theo lần chi; (2) mẫu số lương theo ngày công là ngày công CHUẨN của
    tháng (người vào giữa tháng không được đủ tháng); làm thêm không chịu thuế cả khoản thì không trừ phần chênh lần nữa."""
    law = _verified(law_on(date(2026, 11, 5), date(2026, 10, 31)))
    short = engine.compute(_inputs(law=law, terms={"contract_kind": "fixed_term", "term_months": 2, "insured_vnd": 20_000_000, "work_region": "I",
                                                   "union_member": False, "dependants": 0}))
    by = {x["component"]: x for x in short["lines"]}
    assert not short["held"] and by["SI_EMPLOYEE"]["amount_vnd"] == 1_600_000 and "PIT_FLAT_10" in by and "PIT_PROGRESSIVE" not in by
    assert by["PIT_FLAT_10"]["amount_vnd"] == 3_000_000 and short["net_vnd"] == 30_000_000 - 2_100_000 - 3_000_000
    joiner = [{"id": "f", "work_date": "2026-10-01", "kind": "amount", "unit": "", "quantity": "0", "amount_vnd": 22_000_000, "role_key": "",
               "component": "LUONG", "source": "pay.fixed"},
              *({"id": k, "work_date": "2026-10-31", "kind": "qty", "unit": "day", "quantity": q, "amount_vnd": None, "role_key": f"days.{k}",
                 "component": "", "source": "timekeeping.close"} for k, q in (("paid", "9"), ("std", "22")))]
    got = engine.compute(_inputs(law=law, entries=joiner))
    assert {x["component"]: x["amount_vnd"] for x in got["lines"]}["LUONG"] == 9_000_000
    ot = [*joiner, {"id": "o", "work_date": "2026-10-31", "kind": "qty", "unit": "hour", "quantity": "4", "amount_vnd": None, "role_key": "ot.150",
                    "component": "", "source": "timekeeping.close"}]
    untaxed = _inputs(law=law, entries=ot)
    untaxed["components"] = {**untaxed["components"], "LAM_THEM": {**OT, "taxable": False}}
    out = engine.compute(untaxed)
    assert out["figures"]["taxable_vnd"] == 9_000_000 and "PIT_OT_EXEMPT" not in {x["component"] for x in out["lines"]}
    assert all("PIT_OT_EXEMPT" not in x["rule_ref"] for x in out["lines"])


async def test_a_dependant_cannot_be_registered_twice() -> None:
    co = await seed_pay.office(f"pd{uuid.uuid4().hex[:6]}.demo", "Văn phòng người phụ thuộc (test)", runs=False, close=False)
    party = await owner_val("""SELECT p.id FROM parties.parties p JOIN core.users u ON u.id = p.user_id WHERE p.tenant_id = %s AND u.email = %s""",
                            (co["tenant_id"], f"nvan@{co['domain']}"))
    await ok(co["who"]["luong"], "hr.add_dependant", party_id=str(party), name="Hoàng Minh Khang", relation="child", registered_from="2026-01-01")
    r = await executor.run(co["who"]["luong"], "hr.add_dependant", {"party_id": str(party), "name": " hoàng minh khang ", "relation": "child",
                                                                    "registered_from": "2026-03-01"})
    assert r.status == "invalid" and "hai lần" in r.error
    with pytest.raises(psycopg.errors.UniqueViolation):
        async with db.owner_tx() as conn:
            await conn.execute("""INSERT INTO hr.dependants (tenant_id, party_id, name, relation, registered_from)
                                  VALUES (%s, %s, 'Hoàng Minh Khang', 'child', '2026-01-01')""", (co["tenant_id"], party))


# ── Luật hồi tố không âm thầm đổi số (0154) ──

def test_law_changes_flags_same_rule_new_version_only() -> None:
    from barista.modules.pay.runs import law_changes
    before = ["vn-payroll@2026-07-01:OT_PAY", "vn-payroll@2026-07-01:INSURANCE_BASE + company:LUONG", "vn-payroll@2026-01-01:SI_EMPLOYEE"]
    after = ["vn-payroll@2026-09-01:OT_PAY", "vn-payroll@2026-07-01:INSURANCE_BASE", "vn-payroll@2026-01-01:SI_EMPLOYEE", "vn-payroll@2026-01-01:HI_EMPLOYEE"]
    assert law_changes(before, after) == [{"rule": "vn-payroll:OT_PAY", "from": ["2026-07-01"], "to": ["2026-09-01"]}]   # thêm mã mới không phải đổi phiên bản
    assert law_changes(after, after) == [] and law_changes([], after) == []


def _backdate(tmp_path: Path) -> Path:
    """Bộ quy định thật + trạng thái đã xác minh + MỘT phiên bản vn-payroll hiệu lực từ 01/09/2026 nhưng phát hành hôm nay (hồi tố, có lý do)."""
    root = tmp_path / "regulations"
    shutil.copytree(regulations.ROOT, root)
    for f in (root / "vn-payroll").glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        for p in d["params"].values():
            p |= {"status": "verified", "verified_by": "Kế toán thử (test), 10/11/2026"}
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return root


def _publish_backdated(root: Path) -> None:
    newest = sorted((root / "vn-payroll").glob("*.json"))[-1]
    d = json.loads(newest.read_text(encoding="utf-8"))
    d |= {"version": "2026-09-01", "effective_from": "2026-09-01", "published_on": today().isoformat(),
          "retroactive_reason": "Giả lập (test): nghị định ban hành muộn, kế toán trưởng chấp thuận"}
    (root / "vn-payroll" / "2026-09-01.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    regulations.reload(root)


async def test_recomputing_a_draft_run_flags_the_rules_whose_version_changed(tmp_path: Path) -> None:
    shipped = regulations.ROOT
    root = _backdate(tmp_path)
    regulations.reload(root)
    try:
        co = await seed_pay.office(f"ps{uuid.uuid4().hex[:6]}.demo", "Văn phòng luật hồi tố thử (test)", runs=False)
        w = co["who"]
        run = await ok(w["luong"], "pay.draft_run", period=co["period"].isoformat(), pay_date=today().isoformat())
        assert run["law_changes"] == []                                                       # lần tính đầu: chưa có gì để so
        card = await q(w["luong"], "pay.run", run_id=str(run["run_id"]))
        assert not any(b.get("title") == "Luật đã đổi phiên bản từ lúc tính" for b in card["blocks"])
        again = await ok(w["luong"], "pay.compute_run", run_id=str(run["run_id"]))
        assert again["law_changes"] == [] and "CHÚ Ý" not in again["summary"]                 # luật không đổi ⇒ không cờ

        _publish_backdated(root)                                                              # văn bản hồi tố ra giữa lúc kỳ còn nháp
        card = await q(w["luong"], "pay.run", run_id=str(run["run_id"]))                      # màn kỳ BÁO TRƯỚC khi bấm Tính lại
        warn = next(b for b in card["blocks"] if b.get("title") == "Luật đã đổi phiên bản từ lúc tính")
        assert any("vn-payroll:INSURANCE_BASE: 2026-07-01 → 2026-09-01" in f for f in warn["facts"])
        out = await ok(w["luong"], "pay.compute_run", run_id=str(run["run_id"]))              # lệnh tính lại NÊU danh sách quy tắc + tổng trước / sau
        rules = {c["rule"]: c for c in out["law_changes"]}
        assert rules["vn-payroll:INSURANCE_BASE"] == {"rule": "vn-payroll:INSURANCE_BASE", "from": ["2026-07-01"], "to": ["2026-09-01"]}
        assert "CHÚ Ý" in out["summary"] and out["net_before_vnd"] == out["net_after_vnd"] > 0   # bản sao giống hệt ⇒ số không đổi, cờ vẫn báo
        card = await q(w["luong"], "pay.run", run_id=str(run["run_id"]))
        assert not any(b.get("title") == "Luật đã đổi phiên bản từ lúc tính" for b in card["blocks"])   # tính lại xong ⇒ phiếu khớp luật hiện hành
    finally:
        regulations.reload(shipped)


async def test_a_locked_run_keeps_its_rule_versions_when_a_backdated_law_is_published(tmp_path: Path) -> None:
    shipped = regulations.ROOT
    root = _backdate(tmp_path)
    regulations.reload(root)
    try:
        co = await seed_pay.office(f"ps{uuid.uuid4().hex[:6]}.demo", "Văn phòng chốt rồi luật hồi tố (test)", runs=False)
        w = co["who"]
        run = await ok(w["luong"], "pay.draft_run", period=co["period"].isoformat(), pay_date=today().isoformat())
        sub = await ok(w["luong"], "pay.submit_run", run_id=run["run_id"])
        assert (await ok(edge(w["ketoan"]), "core.decide", approval_id=str(sub["approval_id"]), approve=True))["status"] == "executed"
        refs_sql = """SELECT jsonb_agg(DISTINCT l.rule_ref ORDER BY l.rule_ref) FROM pay.slip_lines l JOIN pay.slips s ON s.id = l.slip_id WHERE s.run_id = %s"""
        before = await owner_val(refs_sql, (run["run_id"],))
        net = await owner_val("SELECT sum(net_vnd) FROM pay.slips WHERE run_id = %s", (run["run_id"],))
        assert any("2026-07-01" in r for r in before)
        _publish_backdated(root)
        assert await owner_val(refs_sql, (run["run_id"],)) == before                            # phiếu đã chốt giữ nguyên phiên bản đã tính
        assert await owner_val("SELECT sum(net_vnd) FROM pay.slips WHERE run_id = %s", (run["run_id"],)) == net
        r = await executor.run(w["luong"], "pay.compute_run", {"run_id": str(run["run_id"])})
        assert r.status == "invalid" and "kỳ nháp" in r.error                                   # không có đường tính lại kỳ đã chốt
        card = await q(w["luong"], "pay.run", run_id=str(run["run_id"]))
        assert not any(b.get("title") == "Luật đã đổi phiên bản từ lúc tính" for b in card["blocks"])
    finally:
        regulations.reload(shipped)
