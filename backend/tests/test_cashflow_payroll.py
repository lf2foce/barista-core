"""0211 — lương trong Dòng tiền dự phóng: nghĩa vụ phải trả xếp theo NGÀY ĐẾN HẠN (``due_on``) thay vì dồn tháng này; lương kỳ chưa chốt ước bằng kỳ đang
tính / kỳ đã chốt gần nhất (``pay.cashflow_lines``); không đếm hai lần khi kỳ chuyển sang chốt; người xem không có quyền phiếu lương (giám đốc) vẫn thấy
đủ khoản chi — dưới dạng tổng, không người."""

import json
import shutil
import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from barista import seed_pay
from barista.kernel import regulations
from barista.kernel.clock import add_months, month_of, today
from barista.modules.pay.cashflow import NET_KEY, STATUTORY_KEY, _shift
from tests.conftest import edge, ok, owner_val, q


@pytest.fixture
def all_verified(tmp_path: Path) -> Iterator[None]:
    """Bản sao bộ quy định ``vn-payroll`` với MỌI quy tắc đã xác minh (giả) — để chốt được kỳ thật có bảo hiểm và thuế (như test_pay_statutory)."""
    root = tmp_path / "regulations"
    shutil.copytree(regulations.ROOT, root)
    shipped = regulations.ROOT
    for f in (root / "vn-payroll").glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        for p in d["params"].values():
            p |= {"status": "verified", "verified_by": "Kế toán thử (test), 14/11/2026"}
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    regulations.reload(root)
    yield
    regulations.reload(shipped)


def _mk(d: date) -> str:
    return f"{d.month:02d}/{d.year}"


def _rows(card: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["month"]: r for r in card["rows"]}


def test_pay_date_shifts_by_month_and_clamps_to_the_month_end() -> None:
    assert _shift(date(2026, 10, 5), 1) == date(2026, 11, 5)
    assert _shift(date(2026, 10, 31), 1) == date(2026, 11, 30)
    assert _shift(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert _shift(date(2026, 12, 25), 2) == date(2027, 2, 25)


async def test_payroll_moves_from_estimate_to_obligation_on_its_pay_month_without_counting_twice(all_verified: None) -> None:
    co = await seed_pay.office(f"cf{uuid.uuid4().hex[:6]}.demo", "Văn phòng dòng tiền lương (test)", runs=False)
    w = co["who"]
    this = month_of(today())
    pay_on = add_months(this, 1).replace(day=5)            # lương tháng trước chi ngày 05 tháng sau ⇒ không phải tháng này
    m_pay, m_next = _mk(pay_on), _mk(add_months(this, 2))
    run = await ok(w["luong"], "pay.draft_run", period=co["period"].isoformat(), pay_date=pay_on.isoformat())
    net = int(await owner_val("SELECT coalesce(sum(net_vnd), 0) FROM pay.slips WHERE run_id = %s AND held_reason = ''", (run["run_id"],)))
    statutory = int(await owner_val("""SELECT coalesce(sum(l.amount_vnd), 0) FROM pay.slip_lines l JOIN pay.slips s ON s.id = l.slip_id
                                       WHERE s.run_id = %s AND s.held_reason = '' AND l.kind IN ('withholding', 'employer')""", (run["run_id"],)))
    assert net > 0 and statutory > 0

    # kỳ NHÁP: ước ở tháng chi, chưa có nghĩa vụ; chưa từng chốt kỳ nào ⇒ không suy các tháng sau
    rows = _rows(await q(w["ketoan"], "acct.cashflow", months=4))
    assert rows[m_pay][NET_KEY] == net and rows[m_pay][STATUTORY_KEY] == statutory and rows[m_pay]["out_payout"] == 0
    assert all(r[NET_KEY] == 0 for k, r in rows.items() if k != m_pay)

    sub = await ok(w["luong"], "pay.submit_run", run_id=run["run_id"])
    assert (await ok(edge(w["ketoan"]), "core.decide", approval_id=str(sub["approval_id"]), approve=True))["status"] == "executed"
    due = await owner_val("SELECT jsonb_agg(DISTINCT due_on) FROM acct.payables WHERE source = 'pay.slip' AND tenant_id = %s", (co["tenant_id"],))
    assert due == [pay_on.isoformat()]

    # đã CHỐT: thực nhận chuyển sang nghĩa vụ phải trả đúng tháng chi (không dồn tháng này), phần ước của kỳ đó về 0 — không đếm hai lần;
    # tháng sau ước bằng kỳ vừa chốt (chi ngày 05 tháng sau nữa)
    for viewer in ("ketoan", "giamdoc"):   # giám đốc không có quyền phiếu lương: vẫn thấy đủ, dạng tổng (acct.confidential_open)
        card = await q(w[viewer], "acct.cashflow", months=4)
        rows = _rows(card)
        assert rows[m_pay]["out_payout"] == net and rows[m_pay][NET_KEY] == 0, viewer
        assert rows[m_pay][STATUTORY_KEY] == statutory, viewer               # nộp nhà nước: chưa có lệnh nộp ⇒ ước cùng tháng chi
        assert rows[_mk(this)]["out_payout"] == 0, viewer
        assert rows[m_next][NET_KEY] == net and rows[m_next][STATUTORY_KEY] == statutory, viewer
        assert "kỳ đã chốt gần nhất" in card["note"], viewer
    # chi tiết từng người vẫn mật: giám đốc không thấy dòng nghĩa vụ lương nào (chỉ tổng), kế toán thấy đủ dòng
    boss = (await q(w["giamdoc"], "acct.payables"))["rows"]
    assert not [r for r in boss if r["label"].startswith("Lương & thù lao")]
    mine = (await q(w["ketoan"], "acct.payables"))["rows"]
    assert sum(r["left_vnd"] for r in mine if r["label"].startswith("Lương & thù lao")) == net

    # đề nghị chi cả kỳ, giám đốc duyệt ⇒ lệnh chi (mật) chờ chuyển khoản: tiền rời khỏi tháng chi, về THÁNG NÀY — với cả người không thấy lệnh chi
    req = await ok(w["ketoan"], "pay.request_payment", run_id=run["run_id"], recipient_instruction="Theo bảng kê tài khoản (test)",
                   reason="Chi lương (test)")
    assert (await ok(edge(w["giamdoc"]), "core.decide", approval_id=str(req["approval_id"]), approve=True))["status"] == "executed"
    for viewer in ("ketoan", "giamdoc"):
        rows = _rows(await q(w[viewer], "acct.cashflow", months=4))
        assert rows[_mk(this)]["out_payout"] == net and rows[m_pay]["out_payout"] == 0, viewer


async def test_without_payroll_the_forecast_has_no_payroll_columns() -> None:
    co = await seed_pay.office(f"cn{uuid.uuid4().hex[:6]}.demo", "Văn phòng chưa có kỳ lương (test)", runs=False, close=False)
    card = await q(co["who"]["ketoan"], "acct.cashflow", months=3)
    assert NET_KEY not in {c["key"] for c in card["columns"]} and len(card["rows"]) == 3


async def test_a_negative_month_is_flagged_on_the_desk_and_announced_once_a_week(all_verified: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """0211 lát 2: tháng đầu tiên cuối kỳ âm tiền ⇒ trường ``alert`` của Dòng tiền, câu tín hiệu ở bàn việc, thông báo buổi sáng cho người đọc Kế toán
    cả công ty (tin đẩy không mang số tiền, cùng tháng âm không nhắc lại trong tuần). Chưa nạp sao kê gần đây ⇒ không báo (tiền trên sổ chưa đối chiếu)."""
    from barista.kernel import executor
    from barista.kernel.principal import system_principal
    from barista.modules.acct import _say_cashflow, bank

    co = await seed_pay.office(f"cl{uuid.uuid4().hex[:6]}.demo", "Văn phòng báo âm tiền (test)", runs=False)
    w, tid = co["who"], co["tenant_id"]
    pay_on = add_months(month_of(today()), 1).replace(day=5)
    await ok(w["luong"], "pay.draft_run", period=co["period"].isoformat(), pay_date=pay_on.isoformat())

    card = await q(w["ketoan"], "acct.cashflow", months=4)
    assert _rows(card)[_mk(pay_on)]["closing"] < 0 and card["alert"] is None and "Chưa nạp sao kê" in card["note"]
    assert _say_cashflow(card) is None

    await ok(w["ketoan"], "acct.add_statement_line", received_on=today().isoformat(), amount_vnd=1_000_000, memo="TIEN VE THU (test)")
    card = await q(w["ketoan"], "acct.cashflow", months=4)
    assert card["alert"] == {"month": _mk(pay_on), "closing": _rows(card)[_mk(pay_on)]["closing"]}
    said = _say_cashflow(card)
    assert said["tone"] == "bad" and _mk(pay_on) in said["title"]

    monkeypatch.setattr(bank, "DIGEST_HOUR", 0)
    monkeypatch.setattr(bank, "_low_checked", {})
    r = await executor.run(system_principal(tid), "acct.cash_low_alert", {}, background=True)
    assert r.ok and r.output["sent"] >= 2 and r.output["month"] == _mk(pay_on), r.error or r.output
    got = await owner_val("""SELECT jsonb_agg(jsonb_build_object('u', user_id, 'push', push) ORDER BY user_id) FROM core.notifications
                             WHERE tenant_id = %s AND dedupe_key LIKE 'acct.cash_low:%%'""", (tid,))
    users = {x["u"] for x in got}
    assert {str(w["ketoan"].user_id), str(w["giamdoc"].user_id)} <= users and str(w["luong"].user_id) not in users
    assert all(not any(ch.isdigit() for ch in x["push"]) for x in got)   # tin đẩy đi qua máy chủ ngoài: không số tiền, không tháng
    monkeypatch.setattr(bank, "_low_checked", {})
    await executor.run(system_principal(tid), "acct.cash_low_alert", {}, background=True)
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE tenant_id = %s AND dedupe_key LIKE 'acct.cash_low:%%'", (tid,)) == len(got)
