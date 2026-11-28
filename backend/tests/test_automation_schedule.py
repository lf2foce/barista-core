"""Luật tự động THEO LỊCH (ADR 0265 mục 1, 4): người đặt CHỌN nhịp + giờ (không gõ chuỗi quy tắc), tới hạn đúng giờ CÔNG TY, mỗi lượt chạy một lần
(không chạy đôi, không dội bù sau khi máy chủ nghỉ), hành động "nhờ trợ lý soạn" chỉ ra THẺ ĐỀ XUẤT (không tự ghi, model giả — không gọi model thật) và
"gửi tóm tắt phát hiện" thành thông báo trong app bằng quyền người nhận.

Mỗi test một công ty riêng (luật theo lịch quét theo công ty — không đụng luật của test khác)."""

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from barista import seed
from barista.api import Invalid
from barista.kernel import clock, events, executor, registry
from barista.kernel.forms import field_specs
from barista.kernel.principal import Principal, system_principal
from barista.modules.flow import automations as auto
from barista.modules.flow import timetable
from tests.conftest import ok, owner_val, q

PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("gd", "Giám đốc", "director", "QT", False), ("nv", "Nhân viên", "staff", "QT", False)]


async def _company(packs: tuple[str, ...] = ("findings",)) -> tuple[UUID, dict[str, Principal]]:
    d = f"lich{uuid.uuid4().hex[:6]}.test"
    tid = await seed.create_tenant(f"Công ty lịch {d}", d, people=PEOPLE, packs=packs, about="thử luật theo lịch")
    return tid, {k[0]: await seed.who(tid, k[0], d) for k in PEOPLE}


async def _exec(sql: Any, params: Any = None) -> None:
    from barista.db import db

    async with db.owner.connection() as conn:
        await conn.execute(sql, params)


async def _runs(rid: Any) -> list[dict[str, Any]]:
    return await owner_val("""SELECT coalesce(json_agg(json_build_object('status', status, 'note', note, 'slot', slot_at, 'job', job) ORDER BY id), '[]')
                              FROM flow.automation_runs WHERE automation_id = %s""", (rid,))


async def _due_now(rid: Any, ago: timedelta = timedelta(minutes=1)) -> None:
    await _exec("UPDATE flow.automations SET next_run_at = now() - %s WHERE id = %s", (ago, rid))


async def _sweep(tid: UUID) -> dict[str, int]:
    r = await executor.run(system_principal(tid), "flow.sweep_scheduled_automations", {})
    assert r.status == "ok", r.error
    await events.drain()
    return r.output or {}


def _rule(**kw: Any) -> dict[str, Any]:
    return {"name": f"Lịch {uuid.uuid4().hex[:5]}", "repeat": "daily", "at": "08:00", "action": "notify", "message": "Xem số đầu ngày",
            "user_email": "", "trial": False} | kw


# ── Lịch: hàm thuần ──

def test_choices_become_a_rule_and_back_without_typing_rrule() -> None:
    assert timetable.build("daily") == "FREQ=DAILY;INTERVAL=1"
    assert timetable.build("weekdays") == "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,TU,WE,TH,FR"
    assert timetable.build("weekly", weekday="TH") == "FREQ=WEEKLY;INTERVAL=1;BYDAY=TH"
    assert timetable.build("monthly", month_day="last") == "FREQ=MONTHLY;BYMONTHDAY=-1"
    for args in (("daily",), ("weekdays",), ("weekly", "FR"), ("monthly", "MO", "5"), ("monthly", "MO", "last")):
        rule = timetable.build(*args)
        c = timetable.choices(rule)
        assert timetable.build(c["repeat"], c.get("weekday", "MO"), c.get("month_day", "1")) == rule
    assert timetable.describe("FREQ=WEEKLY;INTERVAL=1;BYDAY=MO", time(8)) == "mỗi tuần vào thứ hai lúc 08:00"
    with pytest.raises(Invalid, match="29–31"):
        timetable.build("monthly", month_day="31")   # ngày không phải tháng nào cũng có ⇒ không cho chọn (không lượt nào bị bỏ âm thầm)
    with pytest.raises(Invalid):
        timetable.build("hourly")
    with pytest.raises(Invalid):
        timetable.clock_of("08:15")   # giờ chọn theo nửa tiếng


def test_next_run_is_on_the_company_clock() -> None:
    after = datetime(2026, 12, 1, 0, 30, tzinfo=UTC)   # thứ Ba 07:30 giờ Việt Nam
    with clock.using("Asia/Ho_Chi_Minh"):
        assert timetable.next_after("FREQ=DAILY;INTERVAL=1", time(8), after) == datetime(2026, 12, 1, 1, 0, tzinfo=UTC)
        assert timetable.next_after("FREQ=WEEKLY;INTERVAL=1;BYDAY=MO", time(8), after) == datetime(2026, 12, 7, 1, 0, tzinfo=UTC)
        friday_late = datetime(2026, 12, 4, 3, 0, tzinfo=UTC)   # thứ Sáu 10:00 ⇒ ngày làm việc kế tiếp là thứ Hai
        assert timetable.next_after("FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,TU,WE,TH,FR", time(8), friday_late) == datetime(2026, 12, 7, 1, 0, tzinfo=UTC)
        assert timetable.next_after("FREQ=MONTHLY;BYMONTHDAY=-1", time(17, 30), after) == datetime(2026, 12, 31, 10, 30, tzinfo=UTC)
        assert timetable.next_after("FREQ=MONTHLY;BYMONTHDAY=1", time(8), after) == datetime(2026, 12, 1, 1, 0, tzinfo=UTC)   # hôm nay, chưa tới 8 giờ
        assert timetable.next_after("FREQ=MONTHLY;BYMONTHDAY=1", time(7), after) == datetime(2027, 1, 1, 0, 0, tzinfo=UTC)   # đã qua ⇒ tháng sau
        exact = datetime(2026, 12, 1, 1, 0, tzinfo=UTC)
        assert timetable.next_after("FREQ=DAILY;INTERVAL=1", time(8), exact) == exact + timedelta(days=1)   # đúng mốc vừa chạy ⇒ lượt SAU
    with clock.using("Asia/Tokyo"):   # công ty ở múi khác: cùng "08:00" là mốc tuyệt đối khác
        assert timetable.next_after("FREQ=DAILY;INTERVAL=1", time(8), after) == datetime(2026, 12, 1, 23, 0, tzinfo=UTC)


# ── Đặt luật ──

async def test_rule_form_offers_choices_and_saves_the_next_run() -> None:
    form = {f["key"]: f for f in field_specs(registry.OPS["flow.save_automation"])}
    assert {o["value"] for o in form["repeat"]["options"]} == {"", "daily", "weekdays", "weekly", "monthly"}   # chọn, không gõ quy tắc
    assert "08:00" in {o["value"] for o in form["at"]["options"]} and form["tools"]["options_query"] == "flow.draft_tool_options"
    _tid, pp = await _company()
    admin = pp["admin"]
    out = await ok(admin, "flow.save_automation", **_rule(repeat="weekly", weekday="MO", user_email=admin.email))
    row = await owner_val("SELECT row_to_json(a) FROM flow.automations a WHERE id = %s", (out["automation_id"],))
    assert row["schedule"] == "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO" and row["trigger"] is None and row["date_source"] is None
    nxt = datetime.fromisoformat(row["next_run_at"])
    assert nxt > datetime.now(UTC) and nxt.astimezone(clock.of("Asia/Ho_Chi_Minh")).isoweekday() == 1
    assert "lần đầu" in out["summary"]
    card = await q(admin, "flow.automation", automation_id=str(out["automation_id"]))
    edit = next(a for a in card["actions"] if a["command"] == "flow.save_automation")["preset"]
    assert edit["repeat"] == "weekly" and edit["weekday"] == "MO" and edit["at"] == "08:00"   # mở lại để sửa: đúng lựa chọn cũ
    r = await executor.run(admin, "flow.save_automation", _rule(trigger="flow.plan_closed", user_email=admin.email))
    assert r.status == "invalid" and "MỘT" in r.error
    r = await executor.run(admin, "flow.save_automation", _rule(conditions="Trạng thái = done", user_email=admin.email))
    assert r.status == "invalid" and "điều kiện" in r.error
    r = await executor.run(admin, "flow.save_automation", _rule(trigger="flow.plan_closed", repeat="", action="digest", user_email=admin.email))
    assert r.status == "invalid" and "theo lịch" in r.error


# ── Chạy ──

async def test_due_rule_runs_once_per_slot_and_never_twice() -> None:
    tid, pp = await _company()
    admin = pp["admin"]
    rid = (await ok(admin, "flow.save_automation", **_rule(user_email=pp["gd"].email, message="Đầu ngày: xem việc trễ")))["automation_id"]
    assert (await _sweep(tid))["queued"] == 0            # chưa tới giờ
    await _due_now(rid)
    first = await executor.run(system_principal(tid), "flow.sweep_scheduled_automations", {})
    second = await executor.run(system_principal(tid), "flow.sweep_scheduled_automations", {})   # quét lại ngay: lượt đã đẩy sang lần sau
    assert first.output == {"queued": 1} and second.output == {"queued": 0}
    await events.drain()
    runs = await _runs(rid)
    assert [x["status"] for x in runs] == ["done"]
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND body = 'Đầu ngày: xem việc trễ'", (pp["gd"].user_id,)) == 1
    assert await owner_val("SELECT next_run_at > now() FROM flow.automations WHERE id = %s", (rid,))
    # hai tiến trình quét cùng lúc cùng một lượt: khoá dòng + chống lặp theo (luật, lượt) ⇒ vẫn MỘT lần chạy
    await _due_now(rid)
    outs = await asyncio.gather(*(executor.run(system_principal(tid), "flow.sweep_scheduled_automations", {}) for _ in range(3)))
    assert sum((o.output or {}).get("queued", 0) for o in outs) == 1
    await events.drain()
    assert len(await _runs(rid)) == 2


async def test_missed_slots_after_downtime_are_skipped_not_flooded() -> None:
    tid, pp = await _company()
    admin = pp["admin"]
    rid = (await ok(admin, "flow.save_automation", **_rule(user_email=admin.email, message="Không được dội")))["automation_id"]
    await _due_now(rid, timedelta(days=3))   # máy chủ nghỉ ba ngày
    assert (await _sweep(tid))["queued"] == 0
    runs = await _runs(rid)
    assert len(runs) == 1 and runs[0]["status"] == "skipped" and "không chạy bù" in runs[0]["note"]
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE body = 'Không được dội'") == 0
    assert await owner_val("SELECT next_run_at > now() FROM flow.automations WHERE id = %s", (rid,))
    await ok(admin, "flow.set_automation", automation_id=str(rid), active=False)
    await _exec("UPDATE flow.automations SET next_run_at = now() - interval '2 days' WHERE id = %s", (rid,))
    await ok(admin, "flow.set_automation", automation_id=str(rid), active=True)   # bật lại ⇒ lượt kế tính từ bây giờ
    assert await owner_val("SELECT next_run_at > now() FROM flow.automations WHERE id = %s", (rid,))


async def test_trial_and_daily_cap_apply_to_scheduled_rules() -> None:
    tid, pp = await _company()
    admin = pp["admin"]
    rid = (await ok(admin, "flow.save_automation", **_rule(user_email=admin.email, trial=True, daily_cap=1)))["automation_id"]
    await _due_now(rid)
    await _sweep(tid)
    assert [x["status"] for x in await _runs(rid)] == ["trial"]
    await _due_now(rid, timedelta(minutes=2))
    await _sweep(tid)
    runs = await _runs(rid)
    assert runs[-1]["status"] == "skipped" and "trần" in runs[-1]["note"]


# ── Nhờ trợ lý soạn ──

@pytest.fixture
def fake_model() -> Iterator[dict[str, Any]]:
    """Model giả: lần đầu gọi lệnh GHI (thêm việc), lần sau trả lời. Ghi lại tên công cụ được mang — bộ hẹp của luật."""
    seen: dict[str, Any] = {"tools": [], "calls": 0}

    def fn(_m: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen["calls"] += 1
        seen["tools"] = sorted(t.name for t in info.function_tools)
        if seen["calls"] == 1 and "flow__create_task" in seen["tools"]:
            return ModelResponse(parts=[ToolCallPart("flow__create_task", {"title": seen["title"]}, tool_call_id="t1")])
        return ModelResponse(parts=[TextPart("Đã soạn sẵn việc đầu tuần — chờ anh/chị xác nhận.")])

    old = auto.DRAFT_MODEL
    auto.DRAFT_MODEL = FunctionModel(fn, model_name="gia-lap")
    yield seen
    auto.DRAFT_MODEL = old


async def test_draft_rule_only_files_proposals_never_writes(fake_model: dict[str, Any]) -> None:
    tid, pp = await _company()
    admin, gd = pp["admin"], pp["gd"]
    title = f"Việc soạn sẵn {uuid.uuid4().hex[:6]}"
    fake_model["title"] = title
    opts = {o["value"] for o in await q(admin, "flow.draft_tool_options")}
    assert {"flow.tasks", "flow.create_task"} <= opts
    assert not any(registry.OPS[o].kind == "command" and registry.OPS[o].agent != "propose" for o in opts)   # lệnh trợ lý tự làm không vào bộ
    r = await executor.run(admin, "flow.save_automation", _rule(action="draft", user_email=gd.email, tools=["flow.tasks", "flow.zz_khong_co"]))
    assert r.status == "invalid" and "zz_khong_co" in r.error
    r = await executor.run(admin, "flow.save_automation", _rule(action="draft", user_email=gd.email, tools=[]))
    assert r.status == "invalid" and "công cụ" in r.error
    r = await executor.run(admin, "flow.save_automation", _rule(action="draft", user_email=gd.email, tools=["flow.tasks"], daily_cap=50))
    assert r.status == "invalid" and "tối đa" in r.error
    rid = (await ok(admin, "flow.save_automation", **_rule(action="draft", user_email=gd.email, tools=["flow.tasks", "flow.create_task"],
                                                              message="Soạn việc đầu tuần cho đội")))["automation_id"]
    await _due_now(rid)
    await _sweep(tid)
    runs = await _runs(rid)
    assert runs[0]["status"] == "queued" and runs[0]["job"]["users"] == [str(gd.user_id)]   # trong giao dịch chỉ chốt việc — chưa gọi model
    assert fake_model["calls"] == 0
    out = await executor.run(system_principal(tid), "flow.run_automation_drafts", {})
    assert out.status == "ok" and out.output == {"drafted": 1}, out.error
    assert {t for t in fake_model["tools"] if not t.startswith("core__")} == {"flow__tasks", "flow__create_task"}   # đúng bộ hẹp của luật
    runs = await _runs(rid)
    assert runs[0]["status"] == "done" and "1 thẻ đề xuất" in runs[0]["note"]
    assert await owner_val("SELECT count(*) FROM flow.tasks WHERE title = %s", (title,)) == 0   # KHÔNG tự ghi
    card = await owner_val("SELECT row_to_json(p) FROM core.proposals p WHERE user_id = %s AND command = 'flow.create_task' AND key LIKE 'auto:%%'",
                           (gd.user_id,))
    assert card is not None and card["input"]["title"] == title and card["status"] == "open"   # thẻ chờ NGƯỜI NHẬN bấm
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE '%%trợ lý đã soạn'", (gd.user_id,)) == 1
    assert await owner_val("SELECT count(*) FROM core.ai_usage WHERE user_id = %s AND category = 'auto:draft'", (gd.user_id,)) >= 1   # token đã đo
    again = await executor.run(system_principal(tid), "flow.run_automation_drafts", {})
    assert again.output == {"drafted": 0} and fake_model["calls"] == 2           # việc đã xong không soạn lại


async def test_draft_rule_in_trial_and_too_many_people_do_not_call_the_model(fake_model: dict[str, Any]) -> None:
    tid, pp = await _company()
    admin = pp["admin"]
    rid = (await ok(admin, "flow.save_automation", **_rule(action="draft", user_email=admin.email, tools=["flow.tasks"], trial=True)))["automation_id"]
    await _due_now(rid)
    await _sweep(tid)
    runs = await _runs(rid)
    assert runs[0]["status"] == "trial" and "Sẽ nhờ trợ lý" in runs[0]["note"] and runs[0]["job"] is None
    old = auto.DRAFT_MAX_PEOPLE
    auto.DRAFT_MAX_PEOPLE = 1
    try:
        rid2 = (await ok(admin, "flow.save_automation", **_rule(action="draft", team_code="QT", tools=["flow.tasks"])))["automation_id"]
        await _due_now(rid2)
        await _sweep(tid)
        runs = await _runs(rid2)
        assert runs[0]["status"] == "failed" and "tối đa 1 người" in runs[0]["note"]
    finally:
        auto.DRAFT_MAX_PEOPLE = old
    await executor.run(system_principal(tid), "flow.run_automation_drafts", {})
    assert fake_model["calls"] == 0


# ── Gửi tóm tắt phát hiện ──

async def test_digest_rule_pushes_findings_to_people_who_can_read_them() -> None:
    tid, pp = await _company()
    admin = pp["admin"]
    rid = (await ok(admin, "flow.save_automation", **_rule(action="digest", user_email=pp["gd"].email, message="")))["automation_id"]
    await _due_now(rid)
    await _sweep(tid)
    runs = await _runs(rid)
    assert runs[0]["status"] == "done" and "1 người" in runs[0]["note"], runs
    n = await owner_val("SELECT row_to_json(n) FROM core.notifications n WHERE user_id = %s AND kind = 'automation' ORDER BY created_at DESC LIMIT 1",
                        (pp["gd"].user_id,))
    assert "7 ngày qua" in n["body"] and n["open"]["query"] == "findings.overview"
    rid2 = (await ok(admin, "flow.save_automation", **_rule(action="digest", user_email=pp["nv"].email)))["automation_id"]   # nhân viên: không xem cả công ty
    await _due_now(rid2)
    await _sweep(tid)
    runs = await _runs(rid2)
    assert runs[0]["status"] == "failed" and "không xem được" in runs[0]["note"]
