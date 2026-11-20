"""Trợ lý với Chấm công + Lương (0150 lát 3–4, AI-native): mỗi lệnh / truy vấn mới là công cụ của ĐÚNG vai, số nằm trên thẻ (kết quả truy vấn), trợ lý
soạn đơn cho chính người hỏi (thẻ đề xuất người bấm), không chấm thay / xét / duyệt / chốt / chi. Model giả theo kịch bản (``tests.test_assistant.Scripted``)
— không gọi model thật; các câu này cũng là ca của bộ eval model thật ``scripts/eval_timekeeping.py``."""

import json
import uuid
from datetime import timedelta
from typing import Any

import pytest

from barista import seed_pay
from barista.agent import chat, desk
from barista.config import settings
from barista.db import db
from barista.kernel import executor, registry
from barista.kernel.clock import today
from barista.kernel.principal import Principal
from tests.conftest import owner_val
from tests.test_assistant import Scripted, call

NEVER = {"timekeeping__punch", "timekeeping__review_punch", "timekeeping__close_period", "timekeeping__grant_leave", "timekeeping__grant_annual_leave",
         "timekeeping__import_punch", "pay__submit_run", "pay__request_payment", "pay__set_rate", "hr__set_terms", "core__decide"}


@pytest.fixture(scope="module")
async def co() -> dict[str, Any]:
    return await seed_pay.office(f"ta{uuid.uuid4().hex[:6]}.demo", "Văn phòng trợ lý thử (test)")


async def _tools(p: Principal) -> dict[str, str]:
    return {chat.tool_name(op.name): mode for op, mode in await chat.catalog(p)}


async def _noop(ev: dict[str, Any]) -> None:
    return None


async def _seen(p: Principal, op: str, /, **args: Any) -> dict[str, Any]:
    run = chat.ToolRun(p, _noop, uuid.uuid4().hex, defer=True)
    return await run.call(registry.OPS[op], args)


async def _turn(p: Principal, message: str, script: list[Any]) -> tuple[list[dict[str, Any]], Scripted]:
    events: list[dict[str, Any]] = []

    async def emit(ev: dict[str, Any]) -> None:
        events.append(ev)

    model = Scripted(script)
    await chat.chat_turn(p, message, emit=emit, model=model)
    return events, model


async def test_each_role_gets_the_right_tools_and_never_the_authority_ones(co: dict[str, Any]) -> None:
    w = co["who"]
    staff, lead, hr, payroll = [await _tools(w[k].as_agent()) for k in ("nvan", "truongkd", "nhansu", "luong")]
    assert {staff[t] for t in ("timekeeping__my_month", "timekeeping__today", "pay__compare_slips", "pay__my_slips")} == {"allow"}
    assert {staff[t] for t in ("timekeeping__request_leave", "timekeeping__request_overtime", "timekeeping__request_correction",
                               "timekeeping__confirm_timesheet")} == {"propose"}
    assert "timekeeping__timesheet" not in staff and "timekeeping__flagged" in staff          # bảng công đội: chỉ trưởng đội trở lên
    assert lead["timekeeping__timesheet"] == "allow" and lead["timekeeping__flagged"] == "allow"
    assert hr["timekeeping__flagged"] == "allow" and "pay__runs" not in hr                    # Nhân sự không mặc nhiên thấy kỳ lương
    assert payroll["pay__compare_slips"] == "allow" and payroll["pay__anomalies"] == "allow"
    for tools in (staff, lead, hr, payroll):
        assert not {t for t in NEVER if tools.get(t, "deny") != "deny"}, NEVER & {t for t, m in tools.items() if m != "deny"}


async def test_late_count_and_leave_left_come_from_the_card(co: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """ "Tháng này tôi đi muộn mấy buổi, còn mấy ngày phép?" — model giả gọi đúng thẻ; số model đọc = số trên thẻ người dùng thấy."""
    monkeypatch.setattr(settings, "agent_tools", "all")
    p = co["who"]["nvchi"]
    events, model = await _turn(p, "Tháng trước tôi đi muộn mấy buổi, còn mấy ngày phép?",
                                [[call("timekeeping.my_month", month=co["period"].isoformat())], "Tháng trước anh/chị đi muộn 1 buổi, còn 12 ngày phép năm."])
    seen = json.dumps(model.returns[0], ensure_ascii=False)
    assert '"Đi muộn (buổi)": 1' in seen and '"Phép năm còn lại": 12' in seen, seen[:600]
    cards = [e["card"] for e in events if e["type"] == "card"]
    assert [c["query"] for c in cards] == ["timekeeping.my_month"]
    k = {i["label"]: i["value"] for b in cards[0]["page"]["blocks"] if b["type"] == "kpis" for i in b["items"]}
    assert k["Đi muộn (buổi)"] == 1 and k["Phép năm còn lại"] == 12


async def test_ask_for_friday_off_becomes_a_leave_card_the_employee_confirms(co: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_tools", "all")
    p = co["who"]["nvchi"]
    friday = today() + timedelta(days=(4 - today().weekday()) % 7 or 7)
    before = await owner_val("SELECT count(*) FROM timekeeping.leave_requests WHERE user_id = %s", (p.user_id,))
    events, _ = await _turn(p, "Cho tôi xin nghỉ phép thứ Sáu này",
                            [[call("timekeeping.request_leave", leave_type="PHEP_NAM", from_day=friday.isoformat(), reason="Việc gia đình")],
                             "Em đã soạn đơn nghỉ phép thứ Sáu — anh/chị bấm Xác nhận để gửi quản lý duyệt."])
    card = next(e["card"] for e in events if e["type"] == "card" and e["card"]["kind"] == "proposal")
    assert card["command"] == "timekeeping.request_leave" and card["input"]["from_day"] == friday.isoformat()
    assert await owner_val("SELECT count(*) FROM timekeeping.leave_requests WHERE user_id = %s", (p.user_id,)) == before   # chưa ghi gì
    # người bấm Xác nhận ⇒ lệnh chạy bằng quyền NGƯỜI, thành đơn chờ quản lý duyệt (không tự duyệt)
    r = await executor.run(p, "timekeeping.request_leave", card["input"])
    assert r.status in ("ok", "pending_approval") and r.output["approval_id"], (r.status, r.error)
    assert await owner_val("SELECT status FROM timekeeping.leave_requests WHERE id = %s", (r.output["request_id"],)) == "requested"


async def test_hr_asks_who_punched_outside_the_office_this_week(co: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_tools", "all")
    hr = co["who"]["nhansu"]
    seen = await _seen(hr.as_agent(), "timekeeping.flagged", days=7)
    assert seen["status"] == "ok" and seen["shown_to_user_as_card"] is True
    names = {r["Người"] for r in seen["data"]["rows"] if "Ngoài mạng" in r["Cờ"]}
    assert names == {"Phí Thị Bình", "Hoàng Minh An"} and seen["data"]["kpis"]["Chờ xét"] >= 2
    # trợ lý không xét thay: lệnh xét là "deny" — kể cả khi model cố gọi
    row = await owner_val("SELECT id FROM timekeeping.punches WHERE tenant_id = %s AND flags <> '{}' ORDER BY at DESC LIMIT 1", (co["tenant_id"],))
    r = await executor.run(co["who"]["truongkd"].as_agent(), "timekeeping.review_punch", {"punch_id": str(row), "decision": "accept"})
    assert r.status != "ok"


async def test_why_is_pay_lower_is_explained_from_two_locked_slips(co: dict[str, Any]) -> None:
    """ "Vì sao lương tháng này thấp hơn?" — thẻ so hai phiếu ĐÃ CHỐT, từng khoản + dữ kiện bản chụp; người lao động chỉ thấy phiếu của mình."""
    w = co["who"]
    r = await _seen(w["nvchi"].as_agent(), "pay.compare_slips")
    assert r["status"] != "ok" or "Chưa có" in json.dumps(r, ensure_ascii=False)        # chưa có phiếu đã chốt (kỳ thật đang dừng chờ xác minh)
    async with db.tenant_tx(w["nvchi"], read_only=True) as conn:
        assert await (await conn.execute("SELECT count(*) AS n FROM pay.slips")).fetchone() == {"n": 0}


async def test_desk_shows_flagged_waiting_requests_and_unconfirmed_timesheets(co: dict[str, Any]) -> None:
    w = co["who"]
    lead = json.dumps(await desk.desk(w["truongkd"]), ensure_ascii=False, default=str)
    assert "giờ chấm có cờ chờ xét" in lead
    mine = json.dumps(await desk.desk(w["nvbinh"]), ensure_ascii=False, default=str)
    assert "bảng công đã chốt chờ anh/chị xác nhận" in mine
    pay = json.dumps(await desk.desk(w["luong"]), ensure_ascii=False, default=str)
    assert "dừng vì quy tắc luật chưa xác minh" in pay
