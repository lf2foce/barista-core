"""Khoản phải thu dùng chung — phần "sales-to-cash" mọi ngành (quyết định 0057): ghi sổ theo BẢN CHẤT tiền (doanh thu / thu hộ / cọc), mã thanh
toán, đối soát nhiều nguồn, chi trả tiền thu hộ qua duyệt, nhắc nợ quá hạn là việc theo dõi chung."""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest

from barista.kernel import events, executor
from barista.kernel.clock import today
from barista.kernel.principal import elevate
from barista.modules import work
from barista.modules.acct.reconcile import match_lines
from tests.conftest import Who, edge, ok, owner_val, q


async def _issue(p: Any, **inp: Any) -> dict[str, Any]:
    """Gói ngành gọi hợp đồng nội bộ bằng quyền hệ thống (giữ tên người gọi)."""
    r = await executor.run(elevate(p), "acct.issue_receivable", {"source": "test.thu", "label": "Khoản thử", "party": "Khách thử",
                                                                     "due_on": today().isoformat(), **inp})
    assert r.status == "ok", r.error
    return r.output


async def _bal(code: str) -> int:
    return int(await owner_val("SELECT coalesce(sum(p.amount_vnd), 0) FROM ledger.postings p JOIN ledger.accounts a ON a.id = p.account_id"
                               " WHERE a.code = %s", (code,)) or 0)


def _ar(code: str, amount: int, due: date, party: str = "A", hints: tuple[str, ...] = (), paid: int = 0) -> dict[str, Any]:
    return {"id": uuid.uuid4(), "code": code, "source": "test.rent", "party": party, "amount_vnd": amount, "paid_vnd": paid, "due_on": due,
            "status": "open", "match_hints": list(hints)}


def _line(memo: str, amount: int, ref: str = "") -> dict[str, Any]:
    return {"id": uuid.uuid4(), "received_on": today(), "amount_vnd": amount, "memo": memo, "payer": "", "bank_ref": ref, "source": "excel"}


def test_match_by_payment_code_and_by_unit_picks_the_oldest_open_period() -> None:
    t = today()
    ars = [_ar("TT2222AA", 10_000_000, t - timedelta(days=30), hints=("HSR-A1203",), paid=10_000_000),
           _ar("TT3333BB", 10_000_000, t - timedelta(days=2), hints=("HSR-A1203",)),
           _ar("TT4444CC", 10_000_000, t + timedelta(days=28), hints=("HSR-A1203",)),
           _ar("TT5555DD", 7_000_000, t + timedelta(days=60), party="B", hints=("DTP-C0910",)),
           _ar("TT6666EE", 8_000_000, t, party="C", hints=("HSR-B0705",)),
           _ar("TT7777FF", 8_000_000, t, party="D", hints=("HSR-B0705",))]
    got = {r["memo"]: r for r in match_lines([
        _line("ck tien nha TT4444CC", 10_000_000),                 # mã thanh toán: nhận kể cả kỳ chưa tới
        _line("TIEN NHA HSR A1203 T10", 10_000_000),               # mã căn: kỳ tới hạn sớm nhất CÒN NỢ (bỏ kỳ đã thu, bỏ kỳ chưa tới)
        _line("tien nha DTP C0910", 7_000_000),                    # mã căn nhưng kỳ còn xa ⇒ không ăn tiền kỳ chưa tới
        _line("tien nha HSR-B0705", 8_000_000),                    # hai khách khác nhau cùng mã căn ⇒ không đoán
        _line("chuyen khoan", 5_000_000),
    ], ars, t + timedelta(days=10))}
    a = got["ck tien nha TT4444CC"]
    assert (a["match"], a["code"], a["how"]) == ("full", "TT4444CC", "mã khoản")
    b = got["TIEN NHA HSR A1203 T10"]
    assert (b["match"], b["code"], b["how"]) == ("full", "TT3333BB", "mã gợi ý")
    assert got["tien nha DTP C0910"]["match"] == "unmatched"
    assert got["tien nha HSR-B0705"]["match"] == "unmatched"
    short = match_lines([_line("TT3333BB", 9_000_000)], ars, t)[0]
    assert short["match"] == "short" and "A" in short["hint"] and "CĐT" not in short["hint"]


async def test_money_nature_decides_the_books(who: Who) -> None:
    """Doanh thu ghi nhận lúc lập; thu hộ: chỉ phần phí là doanh thu (theo tỷ lệ đã thu), phần còn lại là nợ phải trả người nhận; cọc là nợ
    phải trả. Thu vượt bị chặn; lập lại cùng nguồn không nhân đôi."""
    ketoan = await who("ketoan")
    tag = uuid.uuid4().hex[:8]
    rev0, fee0 = await _bal("revenue:services"), await _bal("revenue:other_income")
    sv = await _issue(ketoan, source_ref=f"{tag}#dv", nature="revenue", amount_vnd=5_000_000)
    again = await _issue(ketoan, source_ref=f"{tag}#dv", nature="revenue", amount_vnd=5_000_000)
    assert again["code"] == sv["code"] and again["created"] is False
    assert await _bal("revenue:services") == rev0 - 5_000_000                          # doanh thu: ghi nhận lúc lập
    payee = f"p{tag}"
    th = await _issue(ketoan, source_ref=f"{tag}#01", nature="pass_through", amount_vnd=10_000_000, fee_vnd=1_000_000,
                      revenue_account="revenue:other_income", payee_key=payee, payee_name="Chủ nhà thử")
    assert await _bal(f"held:{payee}") == 0                                             # chưa thu ⇒ chưa nợ ai
    p1 = await ok(ketoan, "acct.record_payment", code=th["code"], amount_vnd=4_000_000)
    p2 = await ok(ketoan, "acct.record_payment", code=th["code"], amount_vnd=6_000_000)
    assert (p1["fee_vnd"], p2["fee_vnd"], p2["status"]) == (400_000, 600_000, "paid")
    assert await _bal(f"held:{payee}") == -9_000_000                                    # nợ chủ nhà 9 triệu
    assert await _bal("revenue:other_income") == fee0 - 1_000_000                     # doanh thu chỉ là phí
    r = await executor.run(ketoan, "acct.record_payment", {"code": th["code"], "amount_vnd": 1})
    assert r.status == "invalid"
    dep = await _issue(ketoan, source_ref=f"{tag}#coc", nature="deposit", amount_vnd=3_000_000, payee_key=f"d{tag}", payee_name="Khách thử")
    await ok(ketoan, "acct.record_payment", code=dep["code"], amount_vnd=3_000_000)
    assert await _bal(f"deposit:d{tag}") == -3_000_000                                  # cọc: nợ phải hoàn của ĐÚNG khoản này
    r = await executor.run(ketoan, "acct.record_payment", {"code": sv["code"], "amount_vnd": 6_000_000})
    assert r.status == "invalid" and "vượt" in r.error


async def test_remittance_needs_approval_and_cannot_exceed_held(who: Who) -> None:
    ketoan, gd = await who("ketoan"), await who("giamdoc")
    tag = uuid.uuid4().hex[:8]
    payee = f"r{tag}"
    th = await _issue(ketoan, source_ref=f"{tag}#01", nature="pass_through", amount_vnd=20_000_000, fee_vnd=2_000_000,
                      revenue_account="revenue:other_income", payee_key=payee, payee_name="Chủ nhà R")
    await ok(ketoan, "acct.record_payment", code=th["code"], amount_vnd=20_000_000)
    req = {"account": f"held:{payee}", "payee_name": "Chủ nhà R", "amount_vnd": 18_000_001, "reason": "thử"}
    r = await executor.run(elevate(ketoan), "acct.request_remittance", req)
    assert r.status == "invalid" and "18.000.000" in r.error
    r = await executor.run(elevate(ketoan), "acct.request_remittance", {**req, "amount_vnd": 10_000_000})
    assert r.status == "pending_approval", r.error
    r2 = await executor.run(elevate(ketoan), "acct.request_remittance", {**req, "amount_vnd": 9_000_000})
    assert r2.status == "invalid" and "đang chờ duyệt" in r2.error                       # tính cả phần đang xin
    cash0 = await _bal("cash:bank")
    await ok(edge(gd), "core.decide", approval_id=r.output["approval_id"], approve=True, note="")
    assert await _bal(f"held:{payee}") == -8_000_000
    rid = r.output["remittance_id"]
    assert await owner_val("SELECT status FROM acct.remittances WHERE id = %s", (rid,)) == "pending_transfer"
    await ok(ketoan, "acct.confirm_remittance", remittance_id=rid, bank_ref=f"FT{tag}")
    assert await _bal("cash:bank") == cash0 - 10_000_000
    page = await q(ketoan, "acct.remittances")
    assert next(x for x in page["rows"] if x["id"] == rid)["status"] == "transferred"


async def test_statement_line_with_payment_code_becomes_a_payment(who: Who) -> None:
    ketoan = await who("ketoan")
    tag = uuid.uuid4().hex[:8]
    ar = await _issue(ketoan, source_ref=f"{tag}#01", nature="revenue", amount_vnd=7_350_000)
    await ok(ketoan, "acct.add_statement_line", received_on=today().isoformat(), amount_vnd=7_350_000,
             memo=f"NGUYEN VAN A CK {ar['code']} HOC PHI", bank_ref=f"FT{tag}")
    row = next(r for r in (await q(ketoan, "acct.reconcile"))["rows"] if r["bank_ref"] == f"FT{tag}")
    assert (row["state"], row["code"], row["how"]) == ("full", ar["code"], "mã khoản")
    await ok(ketoan, "acct.apply_line", line_id=row["id"])
    assert await owner_val("SELECT status FROM acct.receivables WHERE code = %s", (ar["code"],)) == "paid"
    assert await owner_val("SELECT status FROM acct.statement_lines WHERE bank_ref = %s", (f"FT{tag}",)) == "applied"
    assert await owner_val("SELECT count(*) FROM acct.payments p JOIN acct.statement_lines l ON l.id = p.line_id WHERE l.bank_ref = %s",
                           (f"FT{tag}",)) == 1
    opts = {o["value"] for o in await q(ketoan, "acct.collect_options")}
    assert ar["code"] not in opts                                                       # đã thu ⇒ không còn trong ô chọn


async def test_overdue_receivable_opens_one_generic_follow_up_that_closes_on_payment(who: Who, demo: dict[str, Any]) -> None:
    ketoan, sale = await who("ketoan"), await who("sale1")
    tag = uuid.uuid4().hex[:8]
    ar = await _issue(ketoan, source_ref=f"{tag}#01", nature="revenue", amount_vnd=2_000_000, due_on=(today() - timedelta(days=9)).isoformat(),
                      owner_id=str(ketoan.user_id))
    sys = elevate(ketoan)
    out = await executor.run(sys, "acct.follow_overdue", {})
    assert out.status == "ok" and out.output["opened"] >= 1
    await events.drain()
    again = await executor.run(sys, "acct.follow_overdue", {})
    assert ar["code"] not in str(again.output)                                          # mỗi khoản mở một lần
    item = await owner_val("SELECT id FROM work.items WHERE kind = 'collection' AND subject_ref = %s", (ar["code"],))
    assert item is not None
    res = await work.step(demo["tenant"], item, use_model=False)
    assert res["status"] == "waiting"                                                   # quá hạn > ân hạn ⇒ báo người
    note = await owner_val("SELECT last_note FROM work.items WHERE id = %s", (item,))
    assert ar["code"] in note and "2.000.000" in note
    r = await executor.query(sale, "acct.receivable_detail", {"code": ar["code"]})
    assert r.status in ("not_found", "denied")                                          # sale không thấy khoản của người khác
    await ok(ketoan, "acct.record_payment", code=ar["code"], amount_vnd=2_000_000)
    await events.drain()
    assert (await work.step(demo["tenant"], item, use_model=False))["status"] == "done"


# ── Ba lỗi tiền từ review 01/11: mỗi lỗi một test giữ bất biến ──

async def test_one_bank_transaction_is_recorded_once_across_flows(who: Who, demo: dict[str, Any]) -> None:
    """Mã giao dịch ngân hàng chỉ nằm ở dòng tiền vào: ghi phiếu thu hoá đơn CĐT rồi ghi khoản phải thu cùng mã ⇒ lần hai bị chặn, tiền vào sổ
    đúng một lần."""
    ketoan = await who("ketoan")
    tag = uuid.uuid4().hex[:8]
    inv = await owner_val("SELECT code FROM acct.receivables WHERE tenant_id = %s AND status = 'open' ORDER BY due_on LIMIT 1", (demo["tenant"],))
    ar = await _issue(ketoan, source_ref=f"{tag}#01", nature="revenue", amount_vnd=5_000_000)
    cash0 = await _bal("cash:bank")
    await ok(ketoan, "acct.record_payment", code=inv, amount_vnd=1_000_000, bank_ref=f"FT{tag}")
    r = await executor.run(ketoan, "acct.record_payment", {"code": ar["code"], "amount_vnd": 1_000_000, "bank_ref": f"FT{tag}"})
    assert r.status == "conflict" and f"FT{tag}" in r.error
    assert await _bal("cash:bank") == cash0 + 1_000_000
    await ok(ketoan, "acct.add_statement_line", received_on=today().isoformat(), amount_vnd=1_000_000, memo="nap lai", bank_ref=f"FT{tag}")
    assert await owner_val("SELECT count(*) FROM acct.statement_lines WHERE bank_ref = %s", (f"FT{tag}",)) == 1   # nạp sao kê sau cũng không nhân đôi


async def test_overpayment_stays_in_the_books_as_unapplied_until_allocated(who: Who) -> None:
    """Sao kê 12 triệu cho khoản 10 triệu ⇒ sổ tiền +12, 10 vào khoản, 2 nằm ở "chưa phân bổ"; dòng CHƯA xong, không bỏ qua được; phân bổ phần
    còn lại vào khoản khác ⇒ dòng xong."""
    ketoan = await who("ketoan")
    tag = uuid.uuid4().hex[:8]
    a = await _issue(ketoan, source_ref=f"{tag}#a", nature="revenue", amount_vnd=10_000_000)
    b = await _issue(ketoan, source_ref=f"{tag}#b", nature="revenue", amount_vnd=2_000_000)
    cash0, un0 = await _bal("cash:bank"), await _bal("unapplied:bank")
    await ok(ketoan, "acct.add_statement_line", received_on=today().isoformat(), amount_vnd=12_000_000, memo=f"CK {a['code']}", bank_ref=f"FT{tag}")
    line = await owner_val("SELECT id FROM acct.statement_lines WHERE bank_ref = %s", (f"FT{tag}",))
    res = await ok(ketoan, "acct.apply_line", line_id=line)
    assert (res["amount_vnd"], res["diff"], res["line_left"]) == (10_000_000, -2_000_000, 2_000_000) and res["task_id"]
    assert await _bal("cash:bank") == cash0 + 12_000_000 and await _bal("unapplied:bank") == un0 - 2_000_000
    assert await owner_val("SELECT status FROM acct.statement_lines WHERE id = %s", (line,)) == "open"
    row = next(r for r in (await q(ketoan, "acct.reconcile"))["rows"] if str(r["id"]) == str(line))
    assert (row["amount_vnd"], row["allocated_vnd"]) == (2_000_000, 10_000_000)            # bảng đối soát còn hiện phần chưa ghi
    r = await executor.run(ketoan, "acct.ignore_line", {"line_id": str(line), "note": "x"})
    assert r.status == "invalid" and "chưa phân bổ" in r.error
    await ok(ketoan, "acct.apply_line", line_id=line, code=b["code"])
    assert await owner_val("SELECT status FROM acct.statement_lines WHERE id = %s", (line,)) == "applied"
    assert await _bal("unapplied:bank") == un0


async def test_concurrent_refund_requests_cannot_exceed_one_deposit(who: Who) -> None:
    """Hai yêu cầu hoàn cọc cùng lúc cho MỘT khoản cọc 10 triệu (trong khi khoản cọc khác cũng có tiền) ⇒ chỉ một lọt; mỗi khoản cọc một tài
    khoản nên không hoàn bằng tiền cọc của người khác."""
    import asyncio

    ketoan = await who("ketoan")
    tag = uuid.uuid4().hex[:8]
    for k in ("x", "y"):
        dep = await _issue(ketoan, source_ref=f"{tag}#{k}", nature="deposit", amount_vnd=10_000_000, payee_key=f"{k}{tag}", payee_name=f"Khách {k}")
        await ok(ketoan, "acct.record_payment", code=dep["code"], amount_vnd=10_000_000)
    req = {"account": f"deposit:x{tag}", "payee_name": "Khách x", "amount_vnd": 10_000_000, "reason": "hoàn cọc"}
    got = await asyncio.gather(*(executor.run(elevate(ketoan), "acct.request_remittance", req) for _ in range(2)))
    assert sorted(r.status for r in got) == ["invalid", "pending_approval"], [(r.status, r.error) for r in got]
    r = await executor.run(elevate(ketoan), "acct.request_remittance", {**req, "amount_vnd": 1})
    assert r.status == "invalid"                                                            # còn 0 dù khoản cọc y vẫn 10 triệu


async def test_receivables_filter_by_text_in_code_label_party_or_source(who: Who) -> None:
    """Câu "trong đó của dự án X" lọc trên thẻ bằng chữ (mã chứng từ gốc mang mã dự án) — lõi tiền không cần biết khái niệm dự án."""
    ketoan = await who("ketoan")
    tag = uuid.uuid4().hex[:6].upper()
    await _issue(ketoan, source_ref=f"GD-ZQ{tag}-01#1", nature="revenue", amount_vnd=7_000_000, label="Phí môi giới đợt 1")
    await _issue(ketoan, source_ref=f"GD-ZW{tag}-02#1", nature="revenue", amount_vnd=9_000_000, label="Phí môi giới đợt 1",
                 party=f"Chủ đầu tư {tag}")
    only = await q(ketoan, "acct.receivables", status="", q=f"ZQ{tag}")
    assert [r["remaining_vnd"] for r in only["rows"]] == [7_000_000]
    by_party = await q(ketoan, "acct.receivables", status="", q=f"chủ đầu tư {tag.lower()}")
    assert [r["remaining_vnd"] for r in by_party["rows"]] == [9_000_000]
    assert len((await q(ketoan, "acct.receivables", status="", q=tag))["rows"]) == 2


async def test_remittance_and_receivable_families_reach_only_the_callers_own_source(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 11/11: họ ``remittance`` / ``receivable`` mở cho gói ngoài lõi — một gói khai ``consumes`` không được xin chi / xem tiền giữ hộ / khoản thu
    của NGUỒN KHÁC (tiền giữ hộ thuộc nguồn lập khoản thu hộ / cọc mang khoá đó). ``test`` = module của gói ngoài; ``other`` = gói ngoài khác."""
    from barista.db import db
    from barista.kernel import registry
    from barista.kernel.errors import Denied
    from barista.kernel.executor import core_ctx

    monkeypatch.setattr(registry, "FAMILY_CALLERS", {**registry.FAMILY_CALLERS, "remittance": {"test", "other"}, "receivable": {"test", "other"}})
    ketoan = await who("ketoan")
    tag = uuid.uuid4().hex[:8]
    payee = f"o{tag}"
    th = await _issue(ketoan, source_ref=f"{tag}#01", nature="pass_through", amount_vnd=20_000_000, fee_vnd=2_000_000,
                      revenue_account="revenue:other_income", payee_key=payee, payee_name="Chủ nhà O")
    await ok(ketoan, "acct.record_payment", code=th["code"], amount_vnd=20_000_000)
    account = f"held:{payee}"
    req = {"account": account, "payee_name": "Chủ nhà O", "amount_vnd": 1_000_000, "reason": "thử"}

    async def call(module: str, op: str, **inp: Any) -> Any:
        async with db.tenant_tx(ketoan) as conn:
            return await core_ctx(conn, ketoan, module=module).call_system(op, **inp)

    for op, inp in (("acct.balances_for", {"accounts": [account]}), ("acct.request_remittance", req),
                    ("acct.receivables_for", {"source_prefix": "test.", "ref_prefixes": [f"{tag}#"]})):
        with pytest.raises(Denied, match="thuộc module test"):   # gói khác: không xem / không xin chi tiền giữ hộ, không đọc khoản thu của nguồn "test"
            await call("other", op, **inp)
    with pytest.raises(Denied):
        await call("other", "acct.receivables_for", source_prefix="", ref_prefixes=[""])           # tiền tố rỗng không phải lối đọc mọi nguồn
    assert (await call("test", "acct.balances_for", accounts=[account]))[account]["held"] == 18_000_000   # nguồn đúng: như cũ
    assert [r["code"] for r in await call("test", "acct.receivables_for", source_prefix="test.", ref_prefixes=[f"{tag}#"])] == [th["code"]]
    unknown = f"held:z{tag}"   # chưa ai lập khoản thu hộ cho khoá này ⇒ không số để đọc, không tiền để xin chi
    assert (await call("test", "acct.balances_for", accounts=[unknown]))[unknown] == {"held": 0, "requested": 0, "pending": 0, "paid": 0}
    with pytest.raises(Denied, match="chưa có khoản thu hộ"):
        await call("test", "acct.request_remittance", **{**req, "account": unknown})
    got = await call("test", "acct.request_remittance", **req)
    assert got["approval_id"] and await owner_val("SELECT requested_by IS NOT NULL FROM acct.remittances WHERE id = %s", (got["remittance_id"],))
