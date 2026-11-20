"""Chia lead hơn hệ cũ (0152): ngày lễ trong đồng hồ hạn, bằng chứng liên hệ là luật, nhắc trước hạn / báo trưởng nhóm / ân hạn, trực ngoài giờ,
khách quay lại là sự kiện, nghỉ phép (điểm cắm — gói giả), thành viên có hạng + trọng số, công tắc chỉ thành viên (RLS), bảng phản hồi, bước nhận lead,
người lọc lead, giải thích + đề xuất chuyển tay cho trợ lý.

Mỗi test dựng MỘT công ty riêng (luật chia là cấu hình cả công ty)."""

import itertools
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from barista import seed
from barista.db import db
from barista.kernel import business_calendar, executor
from barista.kernel.clock import VN, today
from barista.kernel.principal import Principal, system_principal
from barista.kernel.workhours import Hours, add_working_minutes, next_open
from tests.conftest import fresh_phone, ok, owner_val

_seq = itertools.count(1)
PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("truong", "Trưởng nhóm A", "team_lead", "KD-A", True),
          ("a", "Sale A", "sale", "KD-A", False), ("b", "Sale B", "sale", "KD-A", False), ("c", "Sale C", "sale", "KD-B", False),
          ("truc", "Sale Trực", "sale", "KD-C", False), ("dig", "Điều phối Digital", "coordinator", "DIG", True)]
WITH_LEAVE = (*seed.sample_packs(), "timekeeping")


async def _company(packs: tuple[str, ...] | None = None) -> tuple[UUID, str, dict[str, Principal]]:
    domain = f"lfp{next(_seq)}.demo"
    tid = await seed.create_tenant(f"Công ty lead+ {domain}", domain, people=PEOPLE, packs=packs)
    return tid, domain, {k[0]: await seed.who(tid, k[0], domain) for k in PEOPLE}


async def _rule(admin: Principal, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"name": "Chia lead", "effective_from": (today() - timedelta(days=1)).isoformat(), "cap_uncalled": 0, "roles": ["sale"],
                            "first_call_min": 15, "max_recalls": 2, "days": "all", "open_at": "00:00", "close_at": "24:00", "remind_before_min": 0}
    out: dict[str, Any] = await ok(admin, "leadflow.set_rule", **{**base, **kw})
    return out


async def _intake(p: Principal, **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = await ok(p, "leadflow.intake", **{"name": "Khách mới", "phone": fresh_phone(), **kw})
    out["lead_id"] = UUID(out["lead_id"])
    out["assigned_to"] = UUID(out["assigned_to"]) if out["assigned_to"] else None
    return out


async def _sweep(tid: UUID) -> dict[str, int]:
    out: dict[str, int] = await ok(system_principal(tid), "leadflow.enforce_sla")
    return out


async def _exec(sql: str, params: Any = None) -> None:
    async with db.owner.connection() as conn:
        await conn.execute(sql, params)  # type: ignore[arg-type]


async def _state(lead: UUID, col: str) -> Any:
    return await owner_val(f"SELECT {col} FROM leadflow.lead_state WHERE lead_id = %s", (lead,))


async def _kinds(lead: UUID) -> list[str]:
    return list(await owner_val("SELECT coalesce(jsonb_agg(kind ORDER BY id), '[]') FROM leadflow.sla_events WHERE lead_id = %s", (lead,)))


def _holder(people: dict[str, Principal], uid: Any) -> Principal:
    return next(p for p in people.values() if p.user_id == uid)


def _vn(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=VN)


# ── Ngày lễ trong đồng hồ giờ làm việc (hàm thuần) ──

def test_holidays_stop_the_working_clock() -> None:
    tet = frozenset({date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18), date(2026, 2, 19), date(2026, 2, 20)})
    h = Hours((1, 2, 3, 4, 5, 6), 510, 1080, tet)                                   # T2–T7 08:30–18:00, nghỉ Tết T2–T6
    sat_before = _vn(2026, 2, 14, 17, 50)                                           # thứ Bảy trước Tết
    assert add_working_minutes(sat_before, 15, h) == _vn(2026, 2, 21, 8, 35)        # 10 phút thứ Bảy, bỏ CN + 5 ngày Tết, sáng T7 21/02
    assert next_open(_vn(2026, 2, 17, 10, 0), h) == _vn(2026, 2, 21, 8, 30)
    plain = Hours((1, 2, 3, 4, 5, 6), 510, 1080)
    assert add_working_minutes(sat_before, 15, plain) == _vn(2026, 2, 16, 8, 35)    # không có lịch lễ ⇒ "trễ" ngay mùng 1


def test_calendar_holidays_come_from_the_versioned_calendar() -> None:
    days = business_calendar.holidays("vn-public")
    assert date(2026, 9, 2) in days and date(2026, 4, 30) in days
    assert date(2026, 1, 10) not in days                                            # ngày làm bù không phải ngày nghỉ


# ── 1. Thế nào là đã liên hệ là LUẬT ──

async def test_self_logged_unanswered_call_is_not_contact_when_the_rule_wants_a_conversation() -> None:
    tid, _, pp = await _company()
    await _rule(pp["admin"], contact_proof=["connected"])
    out = await _intake(pp["admin"])
    sale = _holder(pp, out["assigned_to"])
    await ok(sale, "crm.log_activity", lead_id=out["lead_id"], kind="call", outcome="no_answer")   # tự ghi "đã gọi — không nghe"
    assert (await _sweep(tid))["met"] == 0 and await _state(out["lead_id"], "status") == "waiting"
    await ok(sale, "crm.log_activity", lead_id=out["lead_id"], kind="call", outcome="connected")
    assert (await _sweep(tid))["met"] == 1
    assert await owner_val("SELECT data->>'proof' FROM leadflow.sla_events WHERE lead_id = %s AND kind = 'first_call_met'", (out["lead_id"],)) == "connected"


async def test_pbx_proof_needs_the_telephony_pack() -> None:
    _, _, pp = await _company()
    r = await executor.run(pp["admin"], "leadflow.set_rule", {"name": "x", "effective_from": today().isoformat(), "contact_proof": ["pbx"]})
    assert r.status == "invalid" and "Tổng đài" in (r.error or "")


# ── 2. Nhắc trước hạn, báo trưởng nhóm, ân hạn rồi mới thu hồi ──

async def test_remind_then_escalate_to_the_team_lead_then_recall_after_grace() -> None:
    tid, _, pp = await _company()
    await _rule(pp["admin"], first_call_min=30, remind_before_min=10, escalate=True, grace_min=20, team_code="KD-A")
    out = await _intake(pp["admin"])
    lead, holder = out["lead_id"], out["assigned_to"]
    assert await _state(lead, "remind_at") is not None
    await _exec("UPDATE leadflow.lead_state SET remind_at = now() - interval '1 minute' WHERE lead_id = %s", (lead,))
    assert (await _sweep(tid))["reminded"] == 1 and (await _sweep(tid))["reminded"] == 0       # nhắc đúng một lần
    await _exec("UPDATE leadflow.lead_state SET first_call_due = now() - interval '1 minute' WHERE lead_id = %s", (lead,))
    got = await _sweep(tid)
    assert got["breached"] == 1 and got["escalated"] == 1 and got["recalled"] == 0              # trong ân hạn: chưa thu hồi
    lead_note = await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND kind = 'lead_escalated'", (pp["truong"].user_id,))
    assert lead_note == 1 and await owner_val("SELECT owner_id FROM crm.leads WHERE id = %s", (lead,)) == holder
    assert (await _sweep(tid))["breached"] == 0                                                 # không chấm vi phạm lặp
    await _exec("UPDATE leadflow.lead_state SET recall_at = now() - interval '1 minute' WHERE lead_id = %s", (lead,))
    assert (await _sweep(tid))["recalled"] == 1
    assert await owner_val("SELECT owner_id FROM crm.leads WHERE id = %s", (lead,)) not in (None, holder)
    kinds = await _kinds(lead)
    assert kinds.index("reminded") < kinds.index("first_call_breached") < kinds.index("escalated") < kinds.index("recalled")


# ── 3 + 4. Lịch nghỉ lễ, trực ngoài giờ ──

async def test_policy_card_reads_holidays_and_duty_and_warns_without_a_calendar() -> None:
    _, _, pp = await _company()
    made = await _rule(pp["admin"], calendar="vn-public", off_hours="duty", duty_team_code="KD-C", duty_open_at="18:00", duty_close_at="22:00")
    assert "trừ ngày lễ lịch vn-public" in made["sla"] and "đội trực" in made["sla"] and not made["notes"]
    plain = await _rule(pp["admin"], name="Không lịch", source="event")
    assert any("lịch nghỉ lễ" in n for n in plain["notes"])
    r = await executor.run(pp["admin"], "leadflow.set_rule", {"name": "x", "effective_from": today().isoformat(), "off_hours": "duty"})
    assert r.status == "invalid"                                                                # trực mà không chọn đội


@pytest.mark.parametrize(("h", "mi"), [(0, 30), (1, 50), (3, 0), (7, 0), (18, 1), (23, 30)], ids=lambda v: f"{v}")
async def test_lead_arriving_after_hours_goes_to_the_duty_team_with_a_duty_clock(freeze_clock: Callable[[datetime], None], h: int, mi: int) -> None:
    _tid, _, pp = await _company()
    t0 = _vn(today().year, today().month, today().day, h, mi)
    freeze_clock(t0)
    # giờ làm việc 13:00–14:00 KHÔNG chứa mốc nào ở trên; đội trực 24 giờ ⇒ hạn chạy theo giờ trực, không đợi giờ mở cửa
    await _rule(pp["admin"], open_at="13:00", close_at="14:00", off_hours="duty", duty_team_code="KD-C", duty_open_at="00:00",
                duty_close_at="24:00", duty_days="all", first_call_min=15, roles=["sale"])
    out = await _intake(pp["admin"])
    assert out["assigned_to"] == pp["truc"].user_id and await _state(out["lead_id"], "duty") is True
    due = await _state(out["lead_id"], "first_call_due")
    assert timedelta(minutes=14) <= due - t0 <= timedelta(minutes=16)                           # đồng hồ của giờ trực, không đợi giờ mở cửa


# ── 6. Nghỉ phép: gói chấm công cắm điểm leadflow.away ──

async def test_approved_leave_reroutes_waiting_leads_at_once_without_counting_a_recall() -> None:
    tid, _, pp = await _company(WITH_LEAVE)
    await _rule(pp["admin"], team_code="KD-A", contact_proof=["any"])
    out = await _intake(pp["admin"])
    holder = out["assigned_to"]
    await _exec("""INSERT INTO timekeeping.leave_types (tenant_id, code, name, paid_by) VALUES (%s, 'PN', 'Nghỉ phép năm', 'company')
                   ON CONFLICT DO NOTHING""", (tid,))
    await _exec("""INSERT INTO timekeeping.leave_requests (tenant_id, user_id, leave_type, from_day, to_day, days, status, requested_by, approved_by)
                   VALUES (%s, %s, 'PN', %s, %s, 2, 'approved', %s, %s)""",
                (tid, holder, today(), today() + timedelta(days=1), holder, [pp["truong"].user_id]))   # đơn đã duyệt (người khác duyệt)
    got = await _sweep(tid)
    assert got["handed_over"] == 1 and got["recalled"] == 0
    new = await owner_val("SELECT owner_id FROM crm.leads WHERE id = %s", (out["lead_id"],))
    assert new not in (None, holder) and await _state(out["lead_id"], "recalls") == 0           # không tính vào số lần thu hồi
    nxt = await _intake(pp["admin"])
    assert nxt["assigned_to"] not in (None, holder)                                             # người nghỉ không nhận lead mới


async def test_leave_hook_is_optional_and_a_plug_double_drives_it(monkeypatch: Any) -> None:
    """Không gói nào cắm ⇒ như chưa có; cắm (gói giả) ⇒ người trong danh sách nghỉ không nhận lead."""
    from barista.modules.leadflow import routing, sweep
    _tid, _, pp = await _company()
    await _rule(pp["admin"], team_code="KD-A")
    a = pp["a"].user_id

    async def fake_away(ctx: Any, ids: Any) -> dict[str, dict[str, Any]]:
        return {str(a): {"until": today().isoformat(), "label": "Nghỉ phép"}} if a in set(ids) else {}

    monkeypatch.setattr(routing, "away", fake_away)
    monkeypatch.setattr(sweep, "away", fake_away)
    got = [(await _intake(pp["admin"]))["assigned_to"] for _ in range(3)]
    assert set(got) == {pp["b"].user_id}


# ── 8. Bảng phản hồi theo người (chỉ đọc) ──

async def test_scorecard_measures_each_person_and_respects_scope() -> None:
    tid, _, pp = await _company()
    await _rule(pp["admin"], team_code="KD-A")
    out = await _intake(pp["admin"])
    sale = _holder(pp, out["assigned_to"])
    await ok(sale, "crm.log_activity", lead_id=out["lead_id"], kind="call", outcome="connected")
    await _sweep(tid)
    late = await _intake(pp["admin"])
    await _exec("UPDATE leadflow.lead_state SET first_call_due = now() - interval '1 minute' WHERE lead_id = %s", (late["lead_id"],))
    await _sweep(tid)
    rows = {r["name"]: r for r in (await executor.query(pp["admin"], "leadflow.scorecard", {})).output["rows"]}
    assert rows[sale.name]["on_time_pct"] == 100 and rows[sale.name]["median_min"] == 0
    late_holder = _holder(pp, late["assigned_to"])
    assert rows[late_holder.name]["recalled"] == 1 and rows[late_holder.name]["on_time_pct"] == 0
    mine = (await executor.query(sale, "leadflow.scorecard", {})).output["rows"]
    assert [r["name"] for r in mine] == [sale.name]                                              # sale chỉ thấy dòng của mình


# ── 9. Bước bấm nhận + người lọc lead ──

async def test_accept_step_and_missed_accept_moves_the_lead() -> None:
    tid, _, pp = await _company()
    await _rule(pp["admin"], accept_min=5, team_code="KD-A")
    one = await _intake(pp["admin"], qualified_by_email=pp["dig"].email)
    assert await _state(one["lead_id"], "status") == "offered" and await _state(one["lead_id"], "qualified_by") == pp["dig"].user_id
    holder = _holder(pp, one["assigned_to"])
    other = next(p for p in (pp["a"], pp["b"]) if p.user_id != holder.user_id)
    assert (await executor.run(other, "leadflow.accept", {"lead_id": str(one["lead_id"])})).status == "not_found"
    await ok(holder, "leadflow.accept", lead_id=str(one["lead_id"]))
    assert await _state(one["lead_id"], "status") == "waiting"
    two = await _intake(pp["admin"])
    await _exec("UPDATE leadflow.lead_state SET accept_due = now() - interval '1 minute' WHERE lead_id = %s", (two["lead_id"],))
    got = await _sweep(tid)
    assert got["accept_missed"] == 1 and got["recalled"] == 1
    assert await owner_val("SELECT owner_id FROM crm.leads WHERE id = %s", (two["lead_id"],)) != two["assigned_to"]


# ── Trợ lý: giải thích, đề xuất chuyển tay (người quản lý bấm) ──

async def test_explain_names_the_reason_and_managers_get_a_reassign_proposal() -> None:
    _tid, _, pp = await _company()
    await _rule(pp["admin"], team_code="KD-A", name="Chia đội A")
    out = await _intake(pp["admin"])
    holder = _holder(pp, out["assigned_to"])
    page = (await executor.query(pp["truong"], "leadflow.explain", {"lead_id": str(out["lead_id"])})).output
    history = next(b for b in page["blocks"] if b["type"] == "list")["items"]
    assert "Chia cho" in history[0]["title"] and "luật “Chia đội A”" in history[0]["title"] and "xoay vòng" in history[0]["title"]
    act = next(b for b in page["blocks"] if b["type"] == "callout")["actions"][0]
    assert act["command"] == "leadflow.reassign" and act["preset"]["owner_email"] != holder.email
    assert (await executor.run(holder, "leadflow.reassign", act["preset"])).status == "denied"   # sale không tự chuyển
    for target in (pp["c"].email, pp["truong"].email):                                           # ngoài đội mình quản lý / tự nhận ⇒ không
        assert (await executor.run(pp["truong"], "leadflow.reassign", {**act["preset"], "owner_email": target})).status == "invalid", target
    agent = await executor.run(pp["truong"].as_agent(), "leadflow.reassign", act["preset"])
    assert agent.status == "proposed"                                                            # trợ lý chỉ ra thẻ đề xuất
    await ok(pp["truong"], "leadflow.reassign", **act["preset"])
    assert await owner_val("SELECT owner_id FROM crm.leads WHERE id = %s", (out["lead_id"],)) != holder.user_id
    assert "handed_over" in await _kinds(out["lead_id"])
    late = await _intake(pp["admin"])
    await _exec("UPDATE leadflow.lead_state SET first_call_due = now() - interval '1 minute', breached_at = now() WHERE lead_id = %s", (late["lead_id"],))
    tips = (await executor.query(pp["truong"], "leadflow.suggestions", {})).output
    assert tips and tips[0]["command"] == "leadflow.reassign"
    assert (await executor.query(pp["a"], "leadflow.suggestions", {})).output == []


# ── Dữ liệu mẫu chia lead (seed_routing — dùng trong hồ sơ demo đậm) ──

async def test_routing_demo_seed_tells_each_story_once() -> None:
    from barista.seed_routing import seed_routing
    domain = f"lfseed{next(_seq)}.demo"
    tid = await seed.create_tenant(f"Công ty demo chia lead {domain}", domain, packs=seed.ALL_PACKS)   # chỉ gói của lõi: thứ bán là chữ tự do
    out = await seed_routing(tid, domain)
    assert out == {"members": 6, "leads": 4}
    kinds = set(await owner_val("SELECT jsonb_agg(DISTINCT kind) FROM leadflow.sla_events WHERE tenant_id = %s", (tid,)))
    assert {"assigned", "first_call_met", "first_call_breached", "recalled", "returned"} <= kinds
    assert (await seed_routing(tid, domain))["skipped"]                                         # chạy lại an toàn


async def test_reassign_is_not_a_tool_for_people_who_cannot_run_it() -> None:
    """Công cụ trợ lý = thao tác người chạy ĐƯỢC. Chuyển lead cần phạm vi đội trở lên (``min_scope``): sale thường không có nó trong bộ công cụ (trước đây
    được đưa cho model rồi ``denied`` trong handler — tốn 6–10 bước, eval 11/11), trưởng nhóm / quản lý thì có. Handler vẫn kiểm lần nữa."""
    from barista.agent import chat

    _tid, _, pp = await _company()
    sale_tools = {op.name for op, _ in await chat.catalog(pp["a"])}
    lead_tools = {op.name for op, _ in await chat.catalog(pp["truong"])}
    assert "leadflow.reassign" not in sale_tools and "leadflow.reassign" in lead_tools
    r = await executor.run(pp["a"].as_agent(), "leadflow.reassign", {"lead_id": str(uuid.uuid4()), "owner_email": pp["truong"].email})
    assert r.status == "denied" and "phạm vi đội" in r.error


async def test_late_lists_the_teams_overdue_leads_with_ids_for_explain_and_reassign() -> None:
    """Trưởng nhóm hỏi "lead nào của đội quá hạn gọi, chuyển giúp": ``leadflow.late`` trả mã lead + người giữ + trễ bao lâu (nguồn mã cho explain → reassign);
    sale thường không chạy được (phạm vi đội trở lên), lead còn xa hạn không có mặt."""
    from barista.agent import chat

    _tid, _, pp = await _company()
    await _rule(pp["admin"], team_code="KD-A", name="Chia đội A")
    late, fresh = await _intake(pp["admin"]), await _intake(pp["admin"])
    await _exec("UPDATE leadflow.lead_state SET first_call_due = now() - interval '3 hours' WHERE lead_id = %s", (late["lead_id"],))
    await _exec("UPDATE leadflow.lead_state SET first_call_due = now() + interval '5 hours' WHERE lead_id = %s", (fresh["lead_id"],))
    page = (await executor.query(pp["truong"], "leadflow.late", {})).output
    ids = {r["lead_id"]: r for r in page["rows"]}
    assert str(late["lead_id"]) in ids and str(fresh["lead_id"]) not in ids
    assert ids[str(late["lead_id"])]["state"] == "late" and ids[str(late["lead_id"])]["when"].startswith("quá hạn")
    assert (await executor.query(pp["a"], "leadflow.late", {})).status == "denied"
    assert "leadflow.late" in {op.name for op, _ in await chat.catalog(pp["truong"])}
    assert "leadflow.late" not in {op.name for op, _ in await chat.catalog(pp["a"])}


# ── Bản LÕI (ADR 0190) của ``barista-packs:real_estate/tests/test_leadflow_plus.py``: khách quay lại, thành viên có hạng + trọng số, công tắc chỉ thành viên —
# đều khoá theo MÃ thứ khách quan tâm (chữ người nhập khi không gói nào cung cấp danh mục). Công ty riêng chỉ bật gói của lõi.

async def test_returning_customer_notifies_the_owner_and_reopens_a_lost_lead() -> None:
    tid, _, pp = await _company(seed.ALL_PACKS)
    await _rule(pp["admin"])
    phone = fresh_phone()
    first = await _intake(pp["admin"], phone=phone, offering_code="RTN", source="ads")
    owner = _holder(pp, first["assigned_to"])
    again = await _intake(pp["admin"], phone=phone, offering_code="RTN", source="referral", campaign_code="")
    assert again["duplicate"] and again["lead_id"] == first["lead_id"]
    assert await owner_val("SELECT count(*) FROM crm.leads WHERE customer_id = (SELECT customer_id FROM crm.leads WHERE id = %s)", (first["lead_id"],)) == 1
    assert "returned" in await _kinds(first["lead_id"])
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND kind = 'lead_returned'", (owner.user_id,)) == 1
    assert await _state(first["lead_id"], "returns") == 1
    await ok(owner, "crm.log_activity", lead_id=first["lead_id"], kind="call", outcome="connected")
    await ok(owner, "crm.set_stage", lead_id=first["lead_id"], stage="lost", lost_reason="Chỉ tham khảo giá")
    await _sweep(tid)
    third = await _intake(pp["admin"], phone=phone, offering_code="RTN", source="ads", campaign_code="")
    assert third["lead_id"] == first["lead_id"]
    assert await owner_val("SELECT stage FROM crm.leads WHERE id = %s", (first["lead_id"],)) == "lead"   # mở lại
    assert await _state(first["lead_id"], "status") == "waiting" and await _state(first["lead_id"], "holder_id") == owner.user_id
    assert "reopened" in await _kinds(first["lead_id"])
    over = (await executor.query(pp["admin"], "leadflow.overview", {})).output
    assert next(k for k in over["blocks"][0]["items"] if k["label"] == "Khách quay lại (7 ngày)")["value"] == 1


async def test_members_tier_and_weight_drive_the_rotation() -> None:
    _tid, _, pp = await _company(seed.ALL_PACKS)
    for who, tier, weight in (("a", 1, 2), ("b", 1, 1), ("c", 2, 1)):
        await ok(pp["admin"], "leadflow.set_member", offering_code="MEM", user_email=pp[who].email, tier=tier, weight=weight)
    await _rule(pp["admin"], offering_code="MEM", receivers="members")
    got = [(await _intake(pp["admin"], offering_code="MEM"))["assigned_to"] for _ in range(6)]
    assert got.count(pp["a"].user_id) == 4 and got.count(pp["b"].user_id) == 2 and pp["c"].user_id not in got
    for who in "ab":
        await ok(pp[who], "leadflow.pause", until=(today() + timedelta(days=1)).isoformat())
    out = await _intake(pp["admin"], offering_code="MEM")
    assert out["assigned_to"] == pp["c"].user_id                                                 # hạng 1 hết người ⇒ hạng 2
    data = await owner_val("SELECT data FROM leadflow.sla_events WHERE lead_id = %s AND kind = 'assigned'", (out["lead_id"],))
    assert data["tier"] == 2 and data["weight"] == 1
    assert (await executor.run(pp["a"], "leadflow.set_member", {"offering_code": "MEM", "user_email": pp["a"].email})).status == "denied"


async def test_members_only_switch_hides_other_offerings_in_the_database() -> None:
    tid, _, pp = await _company(seed.ALL_PACKS)
    await _rule(pp["admin"])
    await ok(pp["admin"], "leadflow.set_member", offering_code="PJX", user_email=pp["a"].email)
    y = await _intake(pp["admin"], offering_code="PJY", owner_email=pp["a"].email)              # trước khi bật: ai cũng nhận được
    y_of_b = await _intake(pp["admin"], offering_code="PJY", owner_email=pp["b"].email)         # lead PJY của NGƯỜI KHÁC (B)
    await ok(pp["admin"], "admin.set_setting", key="leadflow.members_only", enabled=True)
    a = pp["a"]
    rows = (await executor.query(a, "crm.leads", {})).output["rows"]
    assert "PJY" in {r["offering_code"] for r in rows}                                           # lead ĐANG GIỮ vẫn thấy (vòng quét vẫn chấm hạn người này)
    assert str(y_of_b["lead_id"]) not in {str(r["id"]) for r in rows}                            # KHÔNG thấy lead PJY của người khác (A không tham gia PJY)
    assert str(y["lead_id"]) in {str(r["id"]) for r in rows}
    assert str(y_of_b["lead_id"]) in {str(r["id"]) for r in (await executor.query(pp["truong"], "crm.leads", {})).output["rows"]}   # trưởng nhóm không bị chặn
    assert (await _sweep(tid))["recalled"] == 0 and await _state(y["lead_id"], "holder_id") == a.user_id
    r = await executor.run(a, "crm.create_lead", {"name": "Tự kiếm", "phone": fresh_phone(), "offering_code": "PJY"})
    assert r.status != "ok"                                                                      # RLS: không tạo lead mới cho thứ mình không tham gia
    r = await executor.run(pp["admin"], "crm.assign", {"lead_id": str(y["lead_id"]), "owner_email": pp["b"].email})
    assert r.status == "invalid" and "thành viên" in (r.error or "")
    x = await _intake(pp["admin"], offering_code="PJX")
    assert x["assigned_to"] == a.user_id                                                         # chỉ A là thành viên PJX
    r = await executor.run(pp["admin"], "leadflow.intake", {"name": "Y", "phone": fresh_phone(), "offering_code": "PJY", "owner_email": a.email})
    assert r.status == "invalid"
