"""HỒ SƠ ĐIỀU TRA KHOẢN LỆCH (ADR 0223) trên Postgres thật: code dựng hồ sơ từ sao kê + công nợ, lời giải thích theo mẫu (thiếu thuế GTGT, nhiều khoản một lần
chuyển, trả một phần, bên trả giữ phí, tiền của pháp nhân khác, chuyển trùng, chưa rõ bên chuyển) kèm bằng chứng + độ chắc; hành động là thẻ đề xuất — KHÔNG đụng
tiền tới khi người xác nhận, mọi tiền đi qua lệnh Tài chính có sẵn; việc theo dõi mở cho người phụ trách theo luật dữ liệu và tự đóng khi code thấy xong; RLS."""

import uuid
from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Any

import pytest
import pytest_asyncio

from barista.db import db
from barista.kernel import events, executor
from barista.kernel.clock import today
from barista.kernel.principal import Principal, system_principal
from barista.modules import work
from barista.modules.acct import dossier_logic as L
from barista.modules.acct.vat import split
from tests import money_source
from tests.conftest import Who, approved, ok, owner_val, q
from tests.test_vat import _category, _num, _sym, vat_on, verified_law  # noqa: F401 — fixtures dùng lại

D = date(2026, 11, 5)


# ── Hàm thuần ──

def _recs(*specs: tuple[str, int, int, str]) -> list[dict[str, Any]]:
    return L.prepare([{"id": c, "code": c, "source": "x.fee", "party": party, "amount_vnd": amt, "paid_vnd": paid, "due_on": D, "status": "open" if paid < amt else "paid",
                       "nature": "revenue", "match_hints": [], "match_keys": [], "entity_id": "E1", "invoice_no": "", "owner_id": None} for c, amt, paid, party in specs])


def _ln(memo: str, amount: int, payer: str = "") -> dict[str, Any]:
    return {"id": uuid.uuid4(), "received_on": D, "amount_vnd": amount, "left": amount, "memo": memo, "payer": payer, "bank_ref": "B1", "entity_id": "E1", "status": "open"}


def _explain(ln: dict[str, Any], recs: list[dict[str, Any]], vat: dict[str, Any] | None = None, others: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    cands = L.score_candidates(ln, recs, vat or {}, D)
    return L.explain(ln, recs, cands, others or [], vat or {}, {}, 50_000)


def test_arithmetic_alone_is_never_called_certain() -> None:
    recs = _recs(("TT100", 100_000_000, 0, "Cong ty An Binh"), ("TT200", 40_000_000, 0, "Cong ty Khac"))
    # nội dung có mã khoản ⇒ trả một phần là chắc; không mã, chỉ tên + số tiền ⇒ chưa chắc
    sure = _explain(_ln("thanh toan tt100", 60_000_000), recs)[0]
    assert (sure["pattern"], sure["confidence"]) == ("partial", "high") and "chưa rõ lý do" in sure["summary"]
    weak = _explain(_ln("chuyen tien", 60_000_000, "Cong ty An Binh"), recs)[0]
    assert weak["pattern"] == "partial" and weak["confidence"] != "high" and not weak["sure"]
    # không có gì khớp ⇒ unknown, độ chắc thấp
    none = _explain(_ln("abc", 123_456), recs)[0]
    assert (none["pattern"], none["confidence"]) == ("unknown", "low")


def test_vat_pattern_needs_company_tax_data() -> None:
    recs = _recs(("TT100", 110_000_000, 0, "Cong ty An Binh"))
    ln = _ln("tt100", 100_000_000)
    assert _explain(ln, recs)[0]["pattern"] == "partial"                                   # không có dữ liệu thuế ⇒ không bịa mức VAT
    inv = _explain(ln, recs, {"TT100": {"kind": "invoice", "rate_bps": 1000, "tax_vnd": 10_000_000}})[0]
    assert (inv["pattern"], inv["confidence"], inv["evidence"]["tax_vnd"]) == ("vat_short", "high", 10_000_000)
    guess = _explain(ln, recs, {"TT100": {"kind": "category", "rate_bps": 1000, "tax_vnd": 10_000_000}})[0]
    assert guess["pattern"] == "vat_short" and guess["confidence"] == "medium" and "giả định" in guess["summary"]


def test_many_receivables_one_transfer_unique_and_ambiguous() -> None:
    recs = _recs(("TT1", 30_000_000, 0, "Cong ty An Binh"), ("TT2", 20_000_000, 0, "Cong ty An Binh"), ("TT3", 50_000_000, 0, "Cong ty An Binh"))
    unique = [e for e in _explain(_ln("chuyen khoan", 50_000_000, "An Binh"), recs) if e["pattern"] == "multi"]
    assert unique and unique[0]["confidence"] == "medium" and not unique[0]["sure"]      # không mã nào trong nội dung ⇒ chỉ trùng khớp số học
    one = [e for e in _explain(_ln("tt1 tt2", 50_000_000, "An Binh"), recs) if e["pattern"] == "multi"]
    assert one and one[0]["evidence"]["receivables"] == ["TT1", "TT2"] and one[0]["confidence"] == "high"
    recs += _recs(("TT4", 20_000_000, 0, "Cong ty An Binh"))
    amb = [e for e in _explain(_ln("chuyen khoan", 50_000_000, "An Binh"), recs) if e["pattern"] == "multi"]
    assert amb and amb[0]["confidence"] == "low" and amb[0]["evidence"]["alternatives"]   # TT1+TT2 và TT1+TT4 cùng tổng ⇒ nhiều tổ hợp ⇒ chưa chắc


def test_small_gap_is_fee_suspect_not_fee_fact() -> None:
    recs = _recs(("TT100", 100_000_000, 0, "Cong ty An Binh"))
    e = _explain(_ln("tt100", 99_980_000), recs)[0]
    assert e["pattern"] == "fee" and e["confidence"] == "medium" and "chưa có chứng cứ" in e["summary"]


def test_actions_are_single_existing_commands_and_draft_never_sends() -> None:
    recs = _recs(("TT100", 100_000_000, 0, "Cong ty An Binh"))
    ln = _ln("tt100", 99_980_000, "Cong ty An Binh")
    primary = _explain(ln, recs)[0]
    acts = L.actions_for("KL-AAAA1111", ln, primary, set(), "Cong ty An Binh")
    cmds = {s["command"] for a in acts for s in a["steps"]}
    assert cmds <= {"acct.apply_line", "acct.write_off_dossier", "acct.draft_dossier_ask", "acct.mark_dossier", "acct.dismiss_dossier"}
    assert not any(c in cmds for c in ("acct.post_entry", "acct.record_payment", "acct.reverse_receipt"))   # không đường ghi sổ riêng
    assert not any(a["key"] == "settle_deduction" for a in L.actions_for("KL-AAAA1111", ln, primary, {"TT100"}, "x"))   # đã có hoá đơn GTGT ⇒ không giảm thẳng
    assert "Kính gửi Cong ty An Binh" in L.draft_ask(ln, primary, None)


# ── Postgres thật ──

@pytest_asyncio.fixture(autouse=True)
async def _clean(who: Who) -> AsyncIterator[None]:
    money_source.MODULE = "testpay"
    async with db.owner_tx() as conn:
        await conn.execute("DELETE FROM acct.discrepancy_rules")
    yield
    async with db.owner_tx() as conn:
        await conn.execute("DELETE FROM acct.discrepancy_rules")


@pytest.fixture(autouse=True)
def _source(monkeypatch: pytest.MonkeyPatch) -> None:
    money_source.use(monkeypatch)


def _amt() -> int:
    return 61_000_000 + uuid.uuid4().int % 9_000_000 * 7


async def _recv(who: Who, amount: int, party: str = "", entity_code: str = "", overdue: bool = False) -> dict[str, Any]:
    f = await money_source.revenue(who, amount=amount, entity_code=entity_code)
    async with db.owner_tx() as conn:
        if party:
            await conn.execute("UPDATE acct.receivables SET party = %s WHERE code = %s", (party, f["code"]))
        if overdue:
            await conn.execute("UPDATE acct.receivables SET due_on = %s WHERE code = %s", (today() - timedelta(days=10), f["code"]))
    f["party"] = party or await owner_val("SELECT party FROM acct.receivables WHERE code = %s", (f["code"],))
    return f


async def _line(who: Who, amount: int, memo: str, payer: str = "", age: int = 6) -> str:
    ref = f"FT{uuid.uuid4().hex[:10]}"
    await ok(await who("ketoan"), "acct.add_statement_line", received_on=(today() - timedelta(days=age)).isoformat(), amount_vnd=amount, memo=memo, payer=payer, bank_ref=ref)
    return str(await owner_val("SELECT id FROM acct.statement_lines WHERE bank_ref = %s", (ref,)))


async def _scan(who: Who, *, enable: bool = True) -> dict[str, Any]:
    """``enable``: công ty BẬT tính năng (có luật còn hiệu lực, đặt từ trước các dòng thử) — mặc định tắt cho tới khi công ty đặt luật (soát 14/11)."""
    tid = (await who("ketoan")).tenant_id
    if enable:
        if not await owner_val("SELECT count(*) FROM acct.discrepancy_rules WHERE tenant_id = %s AND active", (tid,)):
            await ok(await who("ketoan"), "acct.set_discrepancy_rule")
        async with db.owner_tx() as conn:
            await conn.execute("UPDATE acct.discrepancy_rules SET created_at = now() - interval '400 days' WHERE tenant_id = %s", (tid,))
    r = await executor.run(system_principal(tid), "acct.scan_discrepancies", {})
    assert r.status == "ok", (r.status, r.error)
    return r.output


async def test_scan_is_off_until_the_company_sets_a_rule_and_skips_lines_older_than_that(who: Who) -> None:
    """Bật theo công ty: không luật ⇒ không dựng hồ sơ nào; có luật ⇒ chỉ dòng nhận từ ngày đặt luật — dòng tồn cũ không sinh hàng loạt hồ sơ / việc."""
    await _recv(who, _amt(), overdue=True)
    old = await _line(who, 7_777_000, "chuyen tien khong ro", age=10)
    out = await _scan(who, enable=False)
    assert out["opened"] == 0 and await owner_val("SELECT count(*) FROM acct.discrepancy_dossiers WHERE line_id = %s", (old,)) == 0
    await ok(await who("ketoan"), "acct.set_discrepancy_rule")          # bật hôm nay ⇒ dòng 10 ngày trước KHÔNG tự dựng
    await executor.run(system_principal((await who("ketoan")).tenant_id), "acct.scan_discrepancies", {})
    assert await owner_val("SELECT count(*) FROM acct.discrepancy_dossiers WHERE line_id = %s", (old,)) == 0


async def _dossier(line_id: str) -> dict[str, Any]:
    row = await owner_val("""SELECT to_jsonb(d) FROM acct.discrepancy_dossiers d WHERE line_id = %s""", (line_id,))
    assert row is not None, "chưa có hồ sơ"
    return row


async def _ar(code: str) -> dict[str, Any]:
    return await owner_val("SELECT json_build_object('paid', paid_vnd, 'amount', amount_vnd, 'status', status, 'credited', credited_vnd) FROM acct.receivables WHERE code = %s", (code,))


async def _run_step(p: Principal, step: dict[str, Any]) -> Any:
    out = await approved(p, step["command"], **step["input"])
    await events.drain()
    return out


async def _owner_is(who: Who, local: str) -> Principal:
    p = await who(local)
    await ok(await who("ketoan"), "acct.set_discrepancy_rule", owner_email=p.email)
    return p


async def test_exact_match_builds_dossier_assigns_owner_and_closes_itself_after_confirmed_apply(who: Who) -> None:
    owner = await _owner_is(who, "ketoan")
    f = await _recv(who, _amt())
    lid = await _line(who, f["fee"], f"TT {f['code']}", f["party"])
    out = await _scan(who)
    assert out["opened"] >= 1
    d = await _dossier(lid)
    assert (d["pattern"], d["confidence"], d["status"], d["trigger"]) == ("exact", "high", "open", "stale_line")
    assert d["owner_id"] == str(owner.user_id) and d["target_codes"] == [f["code"]]
    # việc theo dõi mở cho đúng người phụ trách, subject = mã hồ sơ
    item = await owner_val("SELECT to_jsonb(i) FROM work.items i WHERE kind = 'discrepancy' AND subject_ref = %s", (d["code"],))
    assert item["owner_id"] == str(owner.user_id) and item["status"] == "active"
    # đề xuất không động tiền: truy vấn gợi ý + chạy thử bằng quyền trợ lý ⇒ chỉ là thẻ đề xuất
    sug = [s for s in await q(owner, "acct.discrepancy_suggestions") if s["key"] == f"dossier:{d['code']}"]
    assert sug and sug[0]["command"] == "acct.apply_line"
    r = await executor.propose(owner.as_agent(), sug[0]["command"], sug[0]["input"])
    assert r.status == "proposed"
    assert (await _ar(f["code"]))["paid"] == 0 and (await _dossier(lid))["status"] == "open"
    # agent gọi thẳng ⇒ cũng không ghi (đề xuất), tiền vẫn 0
    r = await executor.run(owner.as_agent(), "acct.apply_line", {"line_id": lid, "code": f["code"]})
    assert r.status == "proposed" and (await _ar(f["code"]))["paid"] == 0
    # người xác nhận (chạy lệnh Tài chính có sẵn) ⇒ tiền vào, hồ sơ tự đóng, việc theo dõi đóng ở lần kiểm sau
    step = next(s for a in d["facts"]["actions"] for s in a["steps"] if s["command"] == "acct.apply_line")
    await _run_step(owner, step)
    assert (await _ar(f["code"]))["status"] == "paid"
    closed = await _dossier(lid)
    assert closed["status"] == "resolved" and closed["closed_at"] is not None
    res = await work.step(owner.tenant_id, uuid_of(item["id"]), use_model=False)
    assert res["final"] and res["status"] == "done"


def uuid_of(v: Any) -> Any:
    return uuid.UUID(str(v))


async def test_young_line_gets_no_dossier_unless_a_strong_overdue_receivable_matches(who: Who) -> None:
    quiet = await _line(who, _amt(), "chuyen tien linh tinh", age=1)
    f = await _recv(who, _amt(), overdue=True)
    hit = await _line(who, f["fee"], f"TT {f['code']}", f["party"], age=1)
    await _scan(who)
    assert await owner_val("SELECT count(*) FROM acct.discrepancy_dossiers WHERE line_id = %s", (quiet,)) == 0
    d = await _dossier(hit)
    assert d["trigger"] == "overdue_receivable" and d["pattern"] == "exact"
    # chạy lại không dựng trùng (một dòng một hồ sơ)
    await _scan(who)
    assert await owner_val("SELECT count(*) FROM acct.discrepancy_dossiers WHERE line_id = %s", (hit,)) == 1


async def test_vat_short_uses_company_invoice_data_and_vat_gap_is_followed_up(who: Who, verified_law: None, vat_on: None) -> None:  # noqa: F811
    owner = await _owner_is(who, "ketoan")
    f = await _recv(who, _amt())
    await _category(who, "thu_dossier", treatment="standard", reducible=False, price_mode="exclusive")
    await ok(owner, "acct.issue_vat_invoice", code=f["code"], category="thu_dossier", symbol=_sym(), number=_num())
    ar = await _ar(f["code"])
    tax = ar["amount"] - f["fee"]
    assert tax == split(f["fee"], 1000, "exclusive")[1]
    lid = await _line(who, f["fee"], f"TT {f['code']}", f["party"])    # bên trả chuyển số CHƯA gồm thuế
    await _scan(who)
    d = await _dossier(lid)
    e = d["facts"]["explanations"][0]
    assert (d["pattern"], d["confidence"]) == ("vat_short", "high")
    assert e["evidence"]["tax_vnd"] == tax and e["evidence"]["expected_vnd"] == f["fee"] and e["evidence"]["tax_source"] == "invoice"
    assert "settle_deduction" not in {a["key"] for a in d["facts"]["actions"]}          # khoản có hoá đơn GTGT: không xoá thẳng phần thuế
    follow = next(a for a in d["facts"]["actions"] if a["key"] == "vat_follow_up")
    for s in follow["steps"]:
        await _run_step(owner, s)
    after = await _dossier(lid)
    assert after["status"] == "open" and after["decision"] == "vat_follow_up"           # tiền đã ghi hết nhưng còn thiếu thuế ⇒ KHÔNG đóng
    assert (await _ar(f["code"]))["paid"] == f["fee"]
    await ok(owner, "acct.record_payment", code=f["code"], amount_vnd=tax, bank_ref=f"VT{uuid.uuid4().hex[:8]}")   # bên trả bù thuế
    await events.drain()
    assert (await _dossier(lid))["status"] == "resolved"


async def test_one_transfer_for_several_receivables(who: Who) -> None:
    owner = await _owner_is(who, "ketoan")
    party = f"Cong ty Da Khoan {uuid.uuid4().hex[:5]}"
    a, b = await _recv(who, _amt(), party), await _recv(who, _amt() + 3_000_000, party)
    lid = await _line(who, a["fee"] + b["fee"], f"TT {a['code']} {b['code']}", party)
    await _scan(who)
    d = await _dossier(lid)
    assert (d["pattern"], d["confidence"]) == ("multi", "high") and sorted(d["target_codes"]) == sorted([a["code"], b["code"]])
    act = next(x for x in d["facts"]["actions"] if x["key"] == "allocate_many")
    assert len(act["steps"]) == 2 and (await _ar(a["code"]))["paid"] == 0
    for s in act["steps"]:
        await _run_step(owner, s)
    assert (await _ar(a["code"]))["status"] == "paid" and (await _ar(b["code"]))["status"] == "paid"
    assert (await _dossier(lid))["status"] == "resolved"


async def test_partial_payment_stays_open_until_the_rest_is_collected(who: Who) -> None:
    owner = await _owner_is(who, "ketoan")
    f = await _recv(who, _amt() + 40_000_000)
    lid = await _line(who, 50_000_000, f"TT {f['code']}", f["party"])
    await _scan(who)
    d = await _dossier(lid)
    assert (d["pattern"], d["confidence"]) == ("partial", "high")
    await _run_step(owner, d["facts"]["actions"][0]["steps"][0])
    assert (await _ar(f["code"]))["paid"] == 50_000_000
    assert (await _dossier(lid))["status"] == "open"                                   # dòng đã ghi hết nhưng khoản còn nợ ⇒ vẫn theo dõi
    await ok(owner, "acct.record_payment", code=f["code"], amount_vnd=f["fee"] - 50_000_000, bank_ref=f"PP{uuid.uuid4().hex[:8]}")
    await events.drain()
    assert (await _dossier(lid))["status"] == "resolved"


async def test_fee_deducted_is_a_suspicion_and_write_off_needs_someone_elses_approval(who: Who) -> None:
    owner = await _owner_is(who, "ketoan")
    f = await _recv(who, _amt())
    lid = await _line(who, f["fee"] - 20_000, f"TT {f['code']}", f["party"])
    await _scan(who)
    d = await _dossier(lid)
    assert d["pattern"] == "fee" and d["confidence"] == "medium"
    act = next(a for a in d["facts"]["actions"] if a["key"] == "settle_deduction")
    assert act["approval"] and [s["command"] for s in act["steps"]] == ["acct.apply_line", "acct.write_off_dossier"]
    await _run_step(owner, act["steps"][0])
    wo = act["steps"][1]
    r = await executor.run(owner, wo["command"], wo["input"])                           # người xin không tự duyệt: thành phiếu chờ, chưa động tiền
    assert r.status == "pending_approval" and (await _ar(f["code"]))["credited"] == 0
    assert (await _dossier(lid))["status"] == "open"
    await _run_step(owner, wo)                                                          # sau duyệt bởi người khác
    ar = await _ar(f["code"])
    assert (ar["credited"], ar["status"]) == (20_000, "paid")
    closed = await _dossier(lid)
    assert closed["status"] == "written_off" and closed["written_off_vnd"] == 20_000
    # ghi sổ vẫn cân và là bút toán giấy báo có của lệnh Tài chính (không có đường ghi riêng của hồ sơ)
    assert await owner_val("SELECT count(*) FROM ledger.journals WHERE source = 'acct.credit_note' AND source_ref = %s", (f["code"],)) == 1


async def test_money_of_another_entity_is_flagged_not_allocated(who: Who) -> None:
    owner = await _owner_is(who, "ketoan")
    f = await _recv(who, _amt(), entity_code="MG")
    lid = await _line(who, f["fee"], f"TT {f['code']}", f["party"])
    await _scan(who)
    d = await _dossier(lid)
    assert d["pattern"] == "other_entity" and d["confidence"] == "high" and d["target_codes"] == [f["code"]]
    assert f["entity"] in d["facts"]["explanations"][0]["summary"]
    assert not any(s["command"] == "acct.apply_line" for a in d["facts"]["actions"] for s in a["steps"])
    mark = next(a for a in d["facts"]["actions"] if a["key"] == "other_entity")["steps"][0]
    await _run_step(owner, mark)
    assert (await _dossier(lid))["decision"] == "other_entity" and (await _ar(f["code"]))["paid"] == 0
    # đóng có lý do ⇒ việc theo dõi dừng
    item = await owner_val("SELECT id FROM work.items WHERE kind = 'discrepancy' AND subject_ref = %s", (d["code"],))
    await ok(owner, "acct.dismiss_dossier", code=d["code"], reason="Kế toán chuyển nội bộ ngoài hệ thống")
    await events.drain()
    assert (await _dossier(lid))["status"] == "dismissed"
    assert (await work.step(owner.tenant_id, uuid_of(item), use_model=False))["status"] == "cancelled"


async def test_duplicate_transfer_is_flagged_with_the_twin_as_evidence(who: Who) -> None:
    owner = await _owner_is(who, "ketoan")
    party = f"Cong ty Tra Trung {uuid.uuid4().hex[:5]}"
    f = await _recv(who, _amt(), party)
    first = await _line(who, f["fee"], f"TT {f['code']}", party, age=7)
    await ok(owner, "acct.apply_line", line_id=first, code=f["code"])
    second = await _line(who, f["fee"], f"TT {f['code']}", party, age=6)
    await _scan(who)
    d = await _dossier(second)
    e = d["facts"]["explanations"][0]
    assert d["pattern"] == "duplicate" and e["evidence"]["twin_line"] == first and e["evidence"]["settled_receivable"] == f["code"]
    assert (await _ar(f["code"]))["status"] == "paid"                                    # không tự ghi gì thêm


async def test_unknown_payer_gets_a_draft_that_is_never_sent(who: Who) -> None:
    owner = await _owner_is(who, "ketoan")
    lid = await _line(who, _amt() + 12_345, "chuyen tien", "Nguoi La " + uuid.uuid4().hex[:5])
    await _scan(who)
    d = await _dossier(lid)
    assert (d["pattern"], d["confidence"]) == ("unknown", "low") and "ask_payer" in {a["key"] for a in d["facts"]["actions"]}
    out = await ok(owner.as_agent() if False else owner, "acct.draft_dossier_ask", code=d["code"])
    assert "Kính gửi" in out["draft"] and "chưa xác định được khoản phải thu" in out["draft"]
    assert (await _dossier(lid))["draft"] == out["draft"] and (await _dossier(lid))["status"] == "open"
    # agent soạn nháp được (rủi ro thấp) nhưng không có lệnh gửi nào trong hồ sơ
    assert (await executor.run(owner.as_agent(), "acct.draft_dossier_ask", {"code": d["code"]})).status == "ok"
    page = await q(owner, "acct.dossier", code=d["code"])
    assert any(b.get("draft") for b in page["blocks"] if b["type"] == "callout")
    assert not any("gửi" in a["command"] for a in page["actions"])


async def test_assignment_policy_is_data_source_rule_beats_general_rule(who: Who) -> None:
    ketoan, admin = await who("ketoan"), await who("admin")
    await ok(ketoan, "acct.set_discrepancy_rule", owner_email=ketoan.email)
    await ok(ketoan, "acct.set_discrepancy_rule", source="testpay.", owner_email=admin.email)
    f = await _recv(who, _amt())
    lid = await _line(who, f["fee"] - 1_000_000, f"TT {f['code']}", f["party"])
    await _scan(who)
    assert (await _dossier(lid))["owner_id"] == str(admin.user_id)
    # đổi ngưỡng ngày bằng luật dữ liệu: 30 ngày ⇒ dòng 6 ngày tuổi chưa dựng hồ sơ
    await ok(ketoan, "acct.set_discrepancy_rule", stale_days=30)
    lid2 = await _line(who, _amt() + 777, "chuyen tien", "Ai do " + uuid.uuid4().hex[:5])
    await _scan(who)
    assert await owner_val("SELECT count(*) FROM acct.discrepancy_dossiers WHERE line_id = %s", (lid2,)) == 0


async def test_dossier_is_row_level_secured(who: Who) -> None:
    owner = await _owner_is(who, "ketoan")
    f = await _recv(who, _amt())
    lid = await _line(who, f["fee"], f"TT {f['code']}", f["party"])
    await _scan(who)
    d = await _dossier(lid)
    page = await q(owner, "acct.dossiers", status="open")
    assert any(r["code"] == d["code"] for r in page["rows"]) and page["kpis"][0]["value"] >= 1
    # người không có quyền Kế toán: lệnh / truy vấn bị từ chối
    sale = await who("sale1")
    assert (await executor.query(sale, "acct.dossiers", {})).status == "denied"
    assert (await executor.query(sale, "acct.dossier", {"code": d["code"]})).status in ("denied", "not_found")   # hồ sơ của người khác: RLS ẩn
    assert (await executor.run(sale, "acct.dismiss_dossier", {"code": d["code"], "reason": "x"})).status == "denied"
    # công ty khác không thấy dòng nào (RLS), kể cả đọc thẳng bảng bằng quyền ứng dụng
    other = await who("admin", other=True)
    async with db.tenant_tx(other, read_only=True) as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM acct.discrepancy_dossiers")
        assert (await cur.fetchone())["n"] == 0   # type: ignore[index]
    # người phạm vi hẹp thấy hồ sơ giao cho MÌNH (đọc chi tiết), không thấy hồ sơ của người khác; danh sách toàn công ty vẫn cần phạm vi cả công ty
    async with db.tenant_tx(sale, read_only=True) as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM acct.discrepancy_dossiers")
        assert (await cur.fetchone())["n"] == 0   # type: ignore[index]
    async with db.owner_tx() as conn:
        await conn.execute("UPDATE acct.discrepancy_dossiers SET owner_id = %s WHERE code = %s", (sale.user_id, d["code"]))
    assert (await executor.query(sale, "acct.dossier", {"code": d["code"]})).status == "ok"
    assert (await executor.query(sale, "acct.dossiers", {})).status == "denied"


async def test_reassigning_hands_the_follow_up_to_the_new_person(who: Who) -> None:
    owner = await _owner_is(who, "ketoan")
    admin = await who("admin")
    f = await _recv(who, _amt())
    lid = await _line(who, f["fee"] - 5_000_000, f"TT {f['code']}", f["party"])
    await _scan(who)
    d = await _dossier(lid)
    assert d["owner_id"] == str(owner.user_id)
    await ok(owner, "acct.assign_dossier", code=d["code"], owner_email=admin.email)
    assert (await _dossier(lid))["owner_id"] == str(admin.user_id)
    rows = await owner_val("SELECT jsonb_agg(jsonb_build_object('owner', owner_id, 'status', status)) FROM work.items WHERE kind = 'discrepancy' AND subject_ref = %s", (d["code"],))
    assert {(r["owner"], r["status"]) for r in rows} == {(str(owner.user_id), "cancelled"), (str(admin.user_id), "active")}
