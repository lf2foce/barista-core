"""Lõi lead: đồng hồ giờ làm việc, chia công bằng có trần, tạm ngừng, hàng chờ, hạn chụp theo luật, thu hồi, chống lặp.

Mỗi test dựng MỘT công ty riêng (luật chia là cấu hình cả công ty — dùng chung sẽ chọi nhau)."""

import itertools
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from barista import seed
from barista.db import db
from barista.kernel import executor
from barista.kernel.clock import VN, today
from barista.kernel.principal import Principal, system_principal
from barista.kernel.workhours import Hours, add_working_minutes, next_open
from tests.conftest import approved, fresh_phone, ok, owner_val

_seq = itertools.count(1)
PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("giamdoc", "Giám đốc", "director", "HQ", True),
          ("a", "Sale A", "sale", "KD-A", False), ("b", "Sale B", "sale", "KD-A", False), ("c", "Sale C", "sale", "KD-B", False),
          ("dig", "Điều phối Digital", "coordinator", "DIG", True), ("mk", "Marketing", "marketer", "MKT", True)]


async def _company() -> tuple[UUID, str]:
    domain = f"lf{next(_seq)}.demo"
    tid = await seed.create_tenant(f"Công ty lead {domain}", domain, people=PEOPLE)
    return tid, domain


async def _p(tid: UUID, domain: str, local: str) -> Principal:
    return await seed.who(tid, local, domain)


async def _rule(admin: Principal, **kw: Any) -> None:
    base: dict[str, Any] = {"name": "Chia lead", "effective_from": (today() - timedelta(days=1)).isoformat(), "cap_uncalled": 0,
                            "first_call_min": 15, "max_recalls": 2, "days": "all", "open_at": "00:00", "close_at": "24:00"}
    await ok(admin, "leadflow.set_rule", **{**base, **kw})


async def _intake(admin: Principal, **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = await ok(admin, "leadflow.intake", **{"name": "Khách mới", "phone": fresh_phone(), **kw})
    out["lead_id"] = UUID(out["lead_id"])
    out["assigned_to"] = UUID(out["assigned_to"]) if out["assigned_to"] else None
    return out


async def _sweep(tid: UUID) -> dict[str, int]:
    out: dict[str, int] = await ok(system_principal(tid), "leadflow.enforce_sla")
    return out


async def _exec(sql: str, params: Any = None) -> None:
    async with db.owner.connection() as conn:
        await conn.execute(sql, params)  # type: ignore[arg-type]


async def _owner(lead_id: Any) -> Any:
    return await owner_val("SELECT owner_id FROM crm.leads WHERE id = %s", (lead_id,))


def _vn(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=VN)


# ── Đồng hồ giờ làm việc (hàm thuần) ──

def test_working_clock_skips_nights_and_sundays() -> None:
    h = Hours((1, 2, 3, 4, 5, 6), 510, 1080)   # T2–T7, 08:30–18:00
    fri_evening = _vn(2026, 11, 6, 17, 50)       # 06/11/2026 là thứ Sáu
    assert add_working_minutes(fri_evening, 5, h) == _vn(2026, 11, 6, 17, 55)
    assert add_working_minutes(fri_evening, 20, h) == _vn(2026, 11, 7, 8, 40)    # 10 phút còn lại + 10 phút sáng T7
    sat_evening = _vn(2026, 11, 7, 19, 0)
    assert next_open(sat_evening, h) == _vn(2026, 11, 9, 8, 30)                  # bỏ Chủ nhật
    assert add_working_minutes(sat_evening, 15, h) == _vn(2026, 11, 9, 8, 45)
    assert next_open(_vn(2026, 11, 9, 7, 0), h) == _vn(2026, 11, 9, 8, 30)
    assert add_working_minutes(_vn(2026, 11, 9, 9, 0), 0, h) == _vn(2026, 11, 9, 9, 0)


def test_working_hours_must_be_sane() -> None:
    with pytest.raises(ValueError):
        Hours((1,), 600, 600)
    with pytest.raises(ValueError):
        Hours((), 0, 1440)
    assert add_working_minutes(_vn(2026, 11, 8, 3, 0), 30, Hours((1, 2, 3, 4, 5, 6, 7), 0, 1440)) == _vn(2026, 11, 8, 3, 30)


# ── Chia ──

async def test_rotation_is_fair_and_respects_cap_pause_and_queue() -> None:
    tid, dom = await _company()
    admin = await _p(tid, dom, "admin")
    a, b, c = [await _p(tid, dom, x) for x in "abc"]
    await _rule(admin, cap_uncalled=2)
    first = [(await _intake(admin))["assigned_to"] for _ in range(3)]
    assert sorted(map(str, first)) == sorted(str(p.user_id) for p in (a, b, c))      # xoay vòng: mỗi người một lead
    await ok(c, "leadflow.pause", until=(today() + timedelta(days=2)).isoformat())      # C nghỉ phép
    more = [(await _intake(admin))["assigned_to"] for _ in range(2)]
    assert set(more) == {a.user_id, b.user_id}                                          # A, B mỗi người thêm một
    capped = await _intake(admin)                                                       # A, B chạm trần 2, C nghỉ ⇒ hàng chờ
    assert capped["assigned_to"] is None and await _owner(capped["lead_id"]) is None
    assert await owner_val("SELECT status FROM leadflow.lead_state WHERE lead_id = %s", (capped["lead_id"],)) == "queued"
    # người chưa có quyền "cả công ty" không thấy lead chờ chia; quản trị thấy
    assert (await executor.query(a, "crm.journey", {"lead_id": str(capped["lead_id"])})).status != "ok"
    assert (await executor.query(admin, "crm.journey", {"lead_id": str(capped["lead_id"])})).status == "ok"
    # A gọi một lead ⇒ hết chạm trần ⇒ chia lại hàng chờ thì A nhận
    mine = await owner_val("SELECT id FROM crm.leads WHERE owner_id = %s LIMIT 1", (a.user_id,))
    await ok(a, "crm.log_activity", lead_id=mine, kind="call", outcome="connected")
    assert (await ok(admin, "leadflow.drain"))["assigned"] == 1
    assert await _owner(capped["lead_id"]) == a.user_id


async def test_least_loaded_prefers_the_lightest_queue() -> None:
    tid, dom = await _company()
    admin = await _p(tid, dom, "admin")
    a, b = await _p(tid, dom, "a"), await _p(tid, dom, "b")
    await _rule(admin, strategy="least_loaded", team_code="KD-A")   # chỉ KD-A ⇒ C không nhận
    for _ in range(3):   # nạp sẵn cho A ba lead chưa gọi
        await ok(a, "crm.create_lead", name="Tải sẵn", phone=fresh_phone())
    got = [(await _intake(admin))["assigned_to"] for _ in range(3)]
    assert got == [b.user_id] * 3
    assert (await _p(tid, dom, "c")).user_id not in got


async def test_external_ref_makes_a_lead_enter_only_once() -> None:
    tid, dom = await _company()
    admin = await _p(tid, dom, "admin")
    await _rule(admin)
    one = await _intake(admin, provider="meta", external_ref="lg-1")
    two = await _intake(admin, provider="meta", external_ref="lg-1", phone=fresh_phone())     # webhook gửi lại, SĐT khác cũng không tạo mới
    assert two["duplicate"] and two["lead_id"] == one["lead_id"]
    assert await owner_val("SELECT count(*) FROM crm.leads WHERE tenant_id = %s", (tid,)) == 1
    r = await executor.run(admin, "leadflow.intake", {"name": "X", "phone": fresh_phone(), "provider": "meta"})
    assert r.status == "invalid"                                                                 # nhà cung cấp thiếu mã


async def test_only_managers_set_rules_and_feed_leads() -> None:
    tid, dom = await _company()
    a, giamdoc = await _p(tid, dom, "a"), await _p(tid, dom, "giamdoc")
    for who, cmd, inp in [(a, "leadflow.set_rule", {"name": "x", "effective_from": today().isoformat()}),
                          (a, "leadflow.intake", {"name": "x", "phone": fresh_phone()}),
                          (a, "leadflow.drain", {})]:
        assert (await executor.run(who, cmd, inp)).status == "denied", cmd
    assert (await executor.run(giamdoc, "leadflow.drain", {})).status == "ok"
    assert (await executor.query(a, "leadflow.overview", {})).status == "denied"          # số liệu toàn công ty
    assert (await executor.run(a, "leadflow.pause", {})).status == "ok"                   # tự nhận lại lead thì được
    assert (await executor.run(a, "leadflow.pause", {"user_email": f"b@{dom}"})).status == "denied"   # đặt cho người khác thì không


# ── Hạn xử lý + thu hồi ──

async def test_first_call_is_judged_against_the_snapshotted_deadline() -> None:
    tid, dom = await _company()
    admin = await _p(tid, dom, "admin")
    await _rule(admin)
    out = await _intake(admin)
    assert out["assigned_to"] is not None
    sale = next(p for p in [await _p(tid, dom, x) for x in "abc"] if p.user_id == out["assigned_to"])
    due = await owner_val("SELECT first_call_due FROM leadflow.lead_state WHERE lead_id = %s", (out["lead_id"],))
    assert timedelta(minutes=14) < due - datetime.now(VN) <= timedelta(minutes=16)                   # ca 24/7: 15 phút (hạn làm tròn tới phút khi qua nửa đêm)
    assert (await _sweep(tid))["met"] == 0                                                           # chưa gọi, chưa tới hạn: không làm gì
    await ok(sale, "crm.log_activity", lead_id=out["lead_id"], kind="call", outcome="connected")
    assert (await _sweep(tid))["met"] == 1
    assert await owner_val("SELECT first_call_ok FROM leadflow.lead_state WHERE lead_id = %s", (out["lead_id"],)) is True
    assert (await _sweep(tid))["met"] == 0                                                           # không ghi lặp
    assert await owner_val("SELECT count(*) FROM leadflow.sla_events WHERE lead_id = %s AND kind = 'first_call_met'", (out["lead_id"],)) == 1


async def test_breach_recalls_to_someone_new_then_queues_after_the_limit() -> None:
    tid, dom = await _company()
    admin = await _p(tid, dom, "admin")
    sales = [await _p(tid, dom, x) for x in "abc"]
    await _rule(admin, max_recalls=2)
    out = await _intake(admin)
    lead = out["lead_id"]
    seen = [out["assigned_to"]]
    for _ in range(2):   # quá hạn ⇒ thu hồi, mỗi lần sang NGƯỜI MỚI
        await _exec("UPDATE leadflow.lead_state SET first_call_due = now() - interval '1 minute' WHERE lead_id = %s", (lead,))
        res = await _sweep(tid)
        assert res["breached"] == 1 and res["recalled"] == 1
        seen.append(await _owner(lead))
    assert len(set(seen)) == 3 and set(seen) == {p.user_id for p in sales}                        # đủ ba người, không ai nhận hai lần
    await _exec("UPDATE leadflow.lead_state SET first_call_due = now() - interval '1 minute' WHERE lead_id = %s", (lead,))
    res = await _sweep(tid)                                                                       # hết số lần thu hồi ⇒ hàng chờ
    assert res["queued"] == 1 and await _owner(lead) is None
    kinds = await owner_val("SELECT jsonb_agg(kind ORDER BY id) FROM leadflow.sla_events WHERE lead_id = %s", (lead,))
    assert kinds.count("assigned") == 3 and kinds.count("first_call_breached") == 3 and kinds.count("recalled") == 3 and kinds[-1] == "recalled"
    assert "queued" in kinds
    # lịch sử chặng của lead ghi rõ vì sao đổi người
    why = await owner_val("SELECT jsonb_agg(reason ORDER BY id) FROM crm.stage_events WHERE lead_id = %s AND kind = 'assigned'", (lead,))
    assert any("Thu hồi" in r for r in why) and any("hàng chờ" in r for r in why)
    assert (await _sweep(tid))["queued"] == 0                                                     # hàng chờ không báo lặp mỗi vòng quét
    kpis = {k["label"]: k for k in (await executor.query(admin, "leadflow.overview", {})).output["blocks"][0]["items"]}
    assert kpis["Thu hồi (7 ngày)"]["value"] == 1                                                 # MỘT lead bị thu hồi 3 lần vẫn là một lead
    assert kpis["Gọi đúng hạn (7 ngày)"]["value"] == 0 and "0 đúng hạn / 1 lead" in kpis["Gọi đúng hạn (7 ngày)"]["hint"]


async def test_breached_lead_called_in_time_is_not_recalled() -> None:
    tid, dom = await _company()
    admin = await _p(tid, dom, "admin")
    await _rule(admin)
    out = await _intake(admin)
    sale = next(p for p in [await _p(tid, dom, x) for x in "abc"] if p.user_id == out["assigned_to"])
    await _exec("UPDATE leadflow.lead_state SET first_call_due = now() - interval '1 minute' WHERE lead_id = %s", (out["lead_id"],))
    await ok(sale, "crm.log_activity", lead_id=out["lead_id"], kind="zalo")   # có liên hệ (dù trễ) ⇒ không thu hồi, ghi nhận "trễ"
    res = await _sweep(tid)
    assert res["recalled"] == 0 and res["met"] == 1 and await _owner(out["lead_id"]) == sale.user_id
    assert await owner_val("SELECT (data->>'on_time')::bool FROM leadflow.sla_events WHERE lead_id = %s AND kind = 'first_call_met'", (out["lead_id"],)) is False


async def test_manager_reassignment_restarts_the_clock_and_notes_are_not_contact() -> None:
    tid, dom = await _company()
    admin = await _p(tid, dom, "admin")
    await _rule(admin)
    out = await _intake(admin)
    lead = out["lead_id"]
    sale = next(p for p in [await _p(tid, dom, x) for x in "abc"] if p.user_id == out["assigned_to"])
    await ok(sale, "crm.log_activity", lead_id=lead, kind="note", note="Chỉ ghi chú")            # ghi chú không phải liên hệ
    other = next(p for p in [await _p(tid, dom, x) for x in "abc"] if p.user_id != sale.user_id)
    await _exec("UPDATE leadflow.lead_state SET first_call_due = now() - interval '1 minute' WHERE lead_id = %s", (lead,))   # người cũ đã trễ
    await ok(admin, "crm.assign", lead_id=lead, owner_email=other.email)
    # 0153: trạng thái chia đi theo NGAY trong cùng giao dịch (không đợi vòng quét) — người mới có đủ hạn từ đầu
    due = await owner_val("SELECT first_call_due FROM leadflow.lead_state WHERE lead_id = %s", (lead,))
    assert due > datetime.now(VN) and await owner_val("SELECT holder_id FROM leadflow.lead_state WHERE lead_id = %s", (lead,)) == other.user_id
    res = await _sweep(tid)
    assert res["breached"] == 0 and res["met"] == 0 and res["resynced"] == 0


async def test_followup_overdue_is_flagged_once_and_closed_leads_leave_the_sweep() -> None:
    tid, dom = await _company()
    admin = await _p(tid, dom, "admin")
    await _rule(admin, followup_hours=24)
    out = await _intake(admin)
    lead = out["lead_id"]
    sale = next(p for p in [await _p(tid, dom, x) for x in "abc"] if p.user_id == out["assigned_to"])
    await ok(sale, "crm.log_activity", lead_id=lead, kind="call", outcome="connected")
    await _sweep(tid)
    await _exec("UPDATE crm.activities SET at = now() - interval '3 days' WHERE lead_id = %s", (lead,))   # lần chăm sóc cuối đã 3 ngày
    assert (await _sweep(tid))["followup"] == 1
    assert (await _sweep(tid))["followup"] == 0                                                              # báo một lần cho mỗi hạn
    await ok(sale, "crm.set_stage", lead_id=lead, stage="lost", lost_reason="Không còn nhu cầu")
    assert (await _sweep(tid))["closed"] == 1
    assert await owner_val("SELECT status FROM leadflow.lead_state WHERE lead_id = %s", (lead,)) == "closed"


async def test_overview_card_reports_queue_and_breaches() -> None:
    tid, dom = await _company()
    admin = await _p(tid, dom, "admin")
    r = await executor.query(admin, "leadflow.overview", {})
    assert r.status == "ok" and r.output["kind"] == "card"
    await _intake(admin)                                                    # chưa có luật ⇒ hàng chờ
    page = (await executor.query(admin, "leadflow.overview", {})).output
    kpis = page["blocks"][0]["items"]
    assert next(k for k in kpis if k["label"] == "Kẹt — không ai nhận")["value"] == 1
    assert any(b["type"] == "callout" for b in page["blocks"])
    rules = (await executor.query(admin, "leadflow.rules", {})).output
    assert rules["rows"] == []


CLOCKS = [(0, 0, 30), (0, 1, 50), (0, 8, 29), (0, 12, 0), (0, 17, 59), (0, 18, 1), (0, 23, 59), (4, 17, 59), (5, 12, 0), (6, 12, 0)]   # ngày lệch so với thứ Hai


@pytest.mark.parametrize(("day", "h", "mi"), CLOCKS, ids=[f"+{d}d-{h:02d}:{m:02d}" for d, h, m in CLOCKS])
async def test_deadline_always_lands_inside_working_hours(freeze_clock: Callable[[datetime], None], day: int, h: int, mi: int) -> None:
    """Dù lead về lúc nào (đêm, Chủ nhật…), hạn gọi phải rơi trong giờ làm việc của luật — không "trễ" từ lúc chưa ai đi làm. Đồng hồ đứng yên
    (``freeze_clock``): kết quả không phụ thuộc giờ chạy test."""
    tid, dom = await _company()
    admin = await _p(tid, dom, "admin")
    await _rule(admin, days="mon_fri", open_at="08:30", close_at="18:00", first_call_min=15)
    d0 = today() + timedelta(days=(7 - today().weekday()) % 7 or 7) + timedelta(days=day)   # thứ Hai kế tiếp + lệch
    t0 = _vn(d0.year, d0.month, d0.day, h, mi)
    freeze_clock(t0)
    out = await _intake(admin)
    due = (await owner_val("SELECT first_call_due FROM leadflow.lead_state WHERE lead_id = %s", (out["lead_id"],))).astimezone(VN)
    assert due.isoweekday() <= 5 and 8 * 60 + 30 < due.hour * 60 + due.minute <= 18 * 60
    assert due >= t0 + timedelta(minutes=15)


@pytest.mark.parametrize(("h", "mi"), [(0, 5), (1, 50), (12, 0), (23, 50), (23, 59)], ids=lambda v: f"{v}")
async def test_round_the_clock_deadline_is_fifteen_minutes_at_any_hour(freeze_clock: Callable[[datetime], None], h: int, mi: int) -> None:
    tid, dom = await _company()
    admin = await _p(tid, dom, "admin")
    await _rule(admin)   # ca 24/7
    t0 = _vn(today().year, today().month, today().day, h, mi)
    freeze_clock(t0)
    out = await _intake(admin)
    due = await owner_val("SELECT first_call_due FROM leadflow.lead_state WHERE lead_id = %s", (out["lead_id"],))
    assert timedelta(minutes=14) < due - t0 <= timedelta(minutes=16)   # hạn làm tròn tới phút khi qua nửa đêm


async def test_new_assigned_lead_tops_the_sales_morning_brief() -> None:
    tid, dom = await _company()
    admin = await _p(tid, dom, "admin")
    await _rule(admin)
    out = await _intake(admin, name="Khách vừa về")
    sale = next(p for p in [await _p(tid, dom, x) for x in "abc"] if p.user_id == out["assigned_to"])
    page = (await executor.query(sale, "crm.brief", {})).output
    first = next(b for b in page["blocks"] if b["type"] == "callout")
    assert first["title"].startswith("Lead mới — gọi ngay: Khách vừa về") and "Hạn gọi lần đầu" in first["facts"][0]
    assert "hôm trước" not in first["draft"]                                       # chưa từng liên hệ ⇒ không bịa lịch sử
    await ok(sale, "crm.log_activity", lead_id=out["lead_id"], kind="call", outcome="no_answer")
    page = (await executor.query(sale, "crm.brief", {})).output
    assert not any(b["type"] == "callout" and b["title"].startswith("Lead mới") for b in page["blocks"])   # đã gọi ⇒ hết "gọi ngay"



# ── Chia linh hoạt: theo nguồn, điều phối, chỉ định khi nạp (quyết định 0038) ──

async def test_ads_leads_wait_for_the_digital_team_who_assign_with_a_suggestion() -> None:
    tid, dom = await _company()
    admin, dig = await _p(tid, dom, "admin"), await _p(tid, dom, "dig")
    sales = {p.user_id: p for p in [await _p(tid, dom, x) for x in "abc"]}
    await _rule(admin)                                                                            # chung: tự chia cho sale
    await _rule(admin, name="Quảng cáo qua Digital", source="ads", mode="coordinator", coord_team_code="DIG")
    event = await _intake(admin, source="event")
    assert event["assigned_to"] in sales                                                          # nguồn khác: tự chia như cũ
    ad = await _intake(admin, name="Khách Facebook", source="ads")
    assert ad["assigned_to"] is None and await _owner(ad["lead_id"]) is None                      # quảng cáo: về hàng chờ Digital
    assert await owner_val("SELECT queue_reason FROM leadflow.lead_state WHERE lead_id = %s", (ad["lead_id"],)) == "coordinator"
    assert (await _sweep(tid))["met"] == 0 and await _owner(ad["lead_id"]) is None              # vòng quét KHÔNG tự chia lead chờ điều phối
    page = (await executor.query(dig, "leadflow.queue", {})).output                               # người điều phối thấy hàng chờ đội mình
    card = next(b for b in page["blocks"] if b["type"] == "callout" and "Khách Facebook" in b["title"])
    suggest = card["actions"][0]
    assert suggest["command"] == "leadflow.assign" and suggest["preset"]["owner_email"].split("@")[0] in "abc"
    assert any(f.startswith("Gợi ý:") for f in card["facts"])
    found = (await executor.query(dig, "crm.leads", {"q": "Khách Facebook"})).output["rows"]
    assert len(found) == 1 and found[0]["owner"] == "Chưa chia"                                   # người điều phối thấy cả lead lẫn tên khách
    assert (await executor.query(dig, "crm.journey", {"lead_id": str(ad["lead_id"])})).status == "ok"
    a = sales[next(u for u, p_ in sales.items() if p_.email == suggest["preset"]["owner_email"])]
    assert (await executor.run(a, "leadflow.assign", suggest["preset"])).status == "not_found"   # sale không thấy hàng chờ, không tự bốc
    await ok(dig, "leadflow.assign", **suggest["preset"])
    assert await _owner(ad["lead_id"]) == a.user_id
    assert await owner_val("SELECT status FROM leadflow.lead_state WHERE lead_id = %s", (ad["lead_id"],)) == "waiting"   # có hạn gọi từ đây
    assert (await executor.query(dig, "leadflow.queue", {})).output["blocks"][0]["type"] == "callout"   # hết hàng chờ


async def test_rule_receivers_must_be_able_to_see_leads() -> None:
    tid, dom = await _company()
    admin = await _p(tid, dom, "admin")
    r = await executor.run(admin, "leadflow.set_rule", {"name": "Cho marketing", "effective_from": today().isoformat(), "roles": ["marketer"]})
    assert r.status == "invalid" and "quyền xem + ghi Khách hàng" in (r.error or "")                 # chia cho người không thấy lead = mất lead
    r = await executor.run(admin, "leadflow.set_rule", {"name": "Điều phối thiếu đội", "effective_from": today().isoformat(), "mode": "coordinator"})
    assert r.status == "invalid"
    out = await ok(admin, "leadflow.set_rule", name="Chỉ trưởng nhóm", effective_from=today().isoformat(), roles=["team_lead"])
    assert out["receivers_now"] == 0 and out["warnings"]                                        # thẻ đề xuất báo ngay: không ai nhận


async def test_the_importer_can_route_to_a_team_queue_or_straight_to_a_person() -> None:
    tid, dom = await _company()
    admin, dig, mk = await _p(tid, dom, "admin"), await _p(tid, dom, "dig"), await _p(tid, dom, "mk")
    b = await _p(tid, dom, "b")
    await _rule(admin)
    to_team = await _intake(dig, name="Lead tự kiếm", team_code="DIG")                         # Digital nạp lead tự kiếm về hàng chờ đội mình
    assert to_team["assigned_to"] is None
    assert await owner_val("SELECT queue_reason FROM leadflow.lead_state WHERE lead_id = %s", (to_team["lead_id"],)) == "team"
    await _sweep(tid)
    assert await _owner(to_team["lead_id"]) is None                                              # vòng quét không tự chia lead đã giao cho đội
    direct = await _intake(dig, name="Lead giao thẳng", owner_email=b.email)
    assert direct["assigned_to"] == b.user_id
    r = await executor.run(dig, "leadflow.intake", {"name": "X", "phone": fresh_phone(), "owner_email": mk.email})
    assert r.status == "invalid"                                                                 # marketing không thấy khách ⇒ không giao được
    assert (await executor.run(b, "leadflow.intake", {"name": "X", "phone": fresh_phone()})).status == "denied"   # sale không nạp lead vào kho chung


async def test_policy_card_reads_like_sentences_and_flags_a_blind_coordinator_team() -> None:
    tid, dom = await _company()
    admin = await _p(tid, dom, "admin")
    await _rule(admin)
    await _rule(admin, name="Qua đội Kế toán", source="ads", mode="coordinator", coord_team_code="KT")   # đội không ai thấy lead
    page = (await executor.query(admin, "leadflow.policy", {})).output
    callouts = [b for b in page["blocks"] if b["type"] == "callout"]
    assert callouts[0]["title"].startswith("Quảng cáo")                                           # luật cụ thể hơn lên trước
    assert "hàng chờ của đội Kế toán" in callouts[0]["text"] and callouts[0]["tone"] == "warn"
    assert any("chưa có trưởng đội" in f for f in callouts[0]["facts"])
    assert "tự chia xoay vòng" in callouts[1]["text"] and callouts[1]["tone"] == "ok"


async def test_queue_suggestions_spread_like_real_assignment() -> None:
    """Ba lead chờ điều phối, ba sale như nhau ⇒ gợi ý ba người KHÁC nhau (không dồn một người vì mỗi lead tính riêng)."""
    tid, dom = await _company()
    admin, dig = await _p(tid, dom, "admin"), await _p(tid, dom, "dig")
    await _rule(admin, source="ads", mode="coordinator", coord_team_code="DIG")
    for i in range(3):
        await _intake(admin, name=f"Khách FB {i}", source="ads")
    page = (await executor.query(dig, "leadflow.queue", {})).output
    picks = [b["actions"][0]["preset"].get("owner_email") for b in page["blocks"] if b["type"] == "callout"]
    assert len(picks) == 3 and len(set(picks)) == 3


async def test_an_internal_note_is_not_a_call_for_the_cap() -> None:
    """Trần lead chưa gọi = 1. Sale chỉ GHI CHÚ lead đầu (chưa liên hệ) ⇒ vẫn chạm trần, không nhận thêm."""
    tid, dom = await _company()
    admin, a = await _p(tid, dom, "admin"), await _p(tid, dom, "a")
    await _rule(admin, cap_uncalled=1)
    for x in "bc":
        await ok(await _p(tid, dom, x), "leadflow.pause", until=(today() + timedelta(days=1)).isoformat())
    first = await _intake(admin)
    assert first["assigned_to"] == a.user_id
    await ok(a, "crm.log_activity", lead_id=first["lead_id"], kind="note", note="Ghi chú nội bộ")
    second = await _intake(admin)
    assert second["assigned_to"] is None                                                         # ghi chú không phải liên hệ


async def test_receivers_need_read_as_well_as_write_on_customers() -> None:
    tid, dom = await _company()
    admin = await _p(tid, dom, "admin")
    await approved(admin, "admin.set_grant", role="sale", resource="crm", scope="own", actions=["write"])   # ghi được mà không xem được
    r = await executor.run(admin, "leadflow.set_rule", {"name": "x", "effective_from": today().isoformat(), "roles": ["sale"]})
    assert r.status == "invalid"
    await approved(admin, "admin.set_grant", role="sale", resource="crm", scope="own", actions=["read", "write"])
    await _rule(admin)
    await approved(admin, "admin.set_grant", role="sale", resource="crm", scope="own", actions=["write"])   # đổi quyền SAU khi đặt luật
    assert (await _intake(admin))["assigned_to"] is None                                         # không chia cho người không xem được lead


async def test_drain_reads_load_under_the_lock() -> None:
    """Chọn người khi chia hàng chờ phải đọc tải SAU khi giữ khoá: lượt nạp khác chen vào giữa không được làm vượt trần."""
    import asyncio
    tid, dom = await _company()
    admin, a = await _p(tid, dom, "admin"), await _p(tid, dom, "a")
    await _rule(admin, cap_uncalled=1)
    for x in "abc":
        await ok(await _p(tid, dom, x), "leadflow.pause", until=(today() + timedelta(days=1)).isoformat())
    assert (await _intake(admin))["assigned_to"] is None                                         # mọi người nghỉ ⇒ kẹt
    await ok(a, "leadflow.pause")                                                                 # A nhận lại
    from barista.modules import leadflow
    original, ready, release = leadflow.routing._pick, asyncio.Event(), asyncio.Event()

    async def slow_pick(*args: Any, **kwargs: Any) -> Any:
        out = await original(*args, **kwargs)
        ready.set()
        await release.wait()
        return out

    leadflow.routing._pick = slow_pick  # type: ignore[assignment]
    task = asyncio.create_task(executor.run(admin, "leadflow.drain", {}))
    try:
        await asyncio.wait_for(ready.wait(), 5)
        leadflow.routing._pick = original  # type: ignore[assignment]
        racer = asyncio.create_task(executor.run(admin, "leadflow.intake", {"name": "Chen ngang", "phone": fresh_phone()}))
        await asyncio.sleep(0.3)
        release.set()
        await asyncio.wait_for(asyncio.gather(task, racer), 10)
    finally:
        leadflow.routing._pick = original  # type: ignore[assignment]
        release.set()
    n = await owner_val("SELECT count(*) FROM leadflow.lead_state WHERE tenant_id = %s AND holder_id = %s AND status = 'waiting'", (tid, a.user_id))
    assert n == 1                                                                                # trần 1 ⇒ đúng 1 lead chưa gọi


# ── Bản LÕI (ADR 0190) của ``barista-packs:real_estate/tests/test_leadflow.py``: chia lead THEO MÃ thứ khách quan tâm là của lõi — công ty không có gói nào
# cung cấp danh mục (điểm cắm ``crm.offering`` trống) thì mã là chữ người nhập. Công ty riêng chỉ bật gói của lõi ⇒ chạy như nhau dù có nạp gói ngành hay không.

async def _core_company() -> tuple[UUID, str]:
    domain = f"lfc{next(_seq)}.demo"
    return await seed.create_tenant(f"Công ty lead lõi {domain}", domain, people=PEOPLE, packs=seed.ALL_PACKS), domain


async def test_existing_customer_keeps_their_owner_but_gets_a_deadline() -> None:
    tid, dom = await _core_company()
    admin, a = await _p(tid, dom, "admin"), await _p(tid, dom, "a")
    await _rule(admin)
    phone = fresh_phone()
    await ok(a, "crm.create_lead", name="Khách cũ", phone=phone)                               # khách của A, chưa chọn thứ quan tâm
    out = await _intake(admin, name="Khách cũ", phone=phone, offering_code="AAA")
    assert out["assigned_to"] == a.user_id and not out["duplicate"]
    assert await owner_val("SELECT status FROM leadflow.lead_state WHERE lead_id = %s", (out["lead_id"],)) == "waiting"
    again = await _intake(admin, name="Khách cũ", phone=phone, offering_code="aaa")           # cùng khách + cùng mã (hoa / thường) ⇒ không tạo thêm
    assert again["duplicate"] and again["lead_id"] == out["lead_id"]


async def test_rules_are_dated_offering_specific_and_never_change_issued_deadlines() -> None:
    tid, dom = await _core_company()
    admin = await _p(tid, dom, "admin")
    await _rule(admin, first_call_min=60)
    await _rule(admin, name="Riêng BBB", offering_code="BBB", first_call_min=5)
    await ok(admin, "leadflow.set_rule", name="Tương lai", effective_from=(today() + timedelta(days=30)).isoformat(), first_call_min=1,
             days="all", open_at="00:00", close_at="24:00")
    general = await _intake(admin)
    special = await _intake(admin, offering_code="BBB")
    rule_of = lambda lid: owner_val("SELECT rule->>'first_call_min' FROM leadflow.lead_state WHERE lead_id = %s", (lid,))  # noqa: E731
    assert await rule_of(general["lead_id"]) == "60" and await rule_of(special["lead_id"]) == "5"   # luật theo mã thắng; tương lai chưa áp
    await _rule(admin, name="Chung mới", first_call_min=10, effective_from=today().isoformat())
    assert await rule_of(general["lead_id"]) == "60"                                                   # lead đã chia giữ hạn đã chụp
    assert await rule_of((await _intake(admin))["lead_id"]) == "10"
    r = await executor.run(admin, "leadflow.set_rule", {"name": "Trùng", "effective_from": today().isoformat(), "days": "all"})
    assert r.status == "conflict"                                                                      # không sửa chồng luật cùng ngày


async def test_periodic_sweep_runs_every_tenant_quietly() -> None:
    """Vòng quét của tiến trình: quá hạn thì thu hồi; lượt rỗng không để rác trong nhật ký thao tác."""
    from barista import sweeps
    tid, dom = await _core_company()
    admin = await _p(tid, dom, "admin")
    await _rule(admin)
    out = await _intake(admin)
    await _exec("UPDATE leadflow.lead_state SET first_call_due = now() - interval '1 minute' WHERE lead_id = %s", (out["lead_id"],))
    await sweeps.sweep_once()
    assert await _owner(out["lead_id"]) != out["assigned_to"]
    before = await owner_val("SELECT count(*) FROM core.audit_log WHERE tenant_id = %s AND command = 'leadflow.enforce_sla'", (tid,))
    await sweeps.sweep_once()   # lượt này chỉ ghi nhận không có gì: giữ im lặng
    assert await owner_val("SELECT count(*) FROM core.audit_log WHERE tenant_id = %s AND command = 'leadflow.enforce_sla'", (tid,)) == before


async def test_most_specific_rule_wins_source_over_offering() -> None:
    tid, dom = await _core_company()
    admin = await _p(tid, dom, "admin")
    await _rule(admin, name="Chung", first_call_min=60)
    await _rule(admin, name="Riêng mã", offering_code="SRC", first_call_min=30)
    await _rule(admin, name="Riêng mã hai", offering_code="SRC2", first_call_min=30)
    await _rule(admin, name="Riêng giới thiệu", source="referral", first_call_min=10)
    await _rule(admin, name="Giới thiệu × mã", source="referral", offering_code="SRC", first_call_min=5)
    names = {}
    for src, code in [("referral", "SRC"), ("referral", ""), ("event", "SRC"), ("event", ""), ("referral", "SRC2")]:
        out = await _intake(admin, source=src, offering_code=code)
        names[(src, code)] = await owner_val("SELECT rule->>'name' FROM leadflow.lead_state WHERE lead_id = %s", (out["lead_id"],))
    assert names == {("referral", "SRC"): "Giới thiệu × mã", ("referral", ""): "Riêng giới thiệu",
                     ("event", "SRC"): "Riêng mã", ("event", ""): "Chung",
                     ("referral", "SRC2"): "Riêng giới thiệu"}   # luật theo NGUỒN thắng luật theo mã khi cả hai cùng khớp


async def test_recalling_one_offering_lead_does_not_hide_the_customers_other_leads() -> None:
    """Cùng khách, hai thứ quan tâm, cùng sale A. Lead thứ nhất bị thu hồi sang B ⇒ A vẫn phải thấy (và làm được) lead thứ hai của mình."""
    tid, dom = await _core_company()
    admin, a = await _p(tid, dom, "admin"), await _p(tid, dom, "a")
    await _rule(admin)
    phone = fresh_phone()
    one = await _intake(admin, phone=phone, offering_code="PJA", owner_email=a.email)
    two = await _intake(admin, phone=phone, offering_code="PJB")
    assert two["assigned_to"] == a.user_id                                                       # khách cũ ⇒ giữ người cũ
    before = {r["offering_code"] for r in (await executor.query(a, "crm.leads", {})).output["rows"]}
    await _exec("UPDATE leadflow.lead_state SET first_call_due = now() - interval '1 minute' WHERE lead_id = %s", (one["lead_id"],))
    await _sweep(tid)
    assert await _owner(one["lead_id"]) != a.user_id
    after = {r["offering_code"] for r in (await executor.query(a, "crm.leads", {})).output["rows"]}
    assert before >= {"PJA", "PJB"} and "PJB" in after                                           # lead của A vẫn hiện trong danh sách của A
    assert (await executor.query(a, "crm.journey", {"lead_id": str(two["lead_id"])})).status == "ok"


async def test_an_old_customer_whose_owner_left_goes_through_the_rule_again() -> None:
    tid, dom = await _core_company()
    admin, a = await _p(tid, dom, "admin"), await _p(tid, dom, "a")
    await _rule(admin)
    phone = fresh_phone()
    await _intake(admin, phone=phone, offering_code="PJC", owner_email=a.email)
    await approved(admin, "admin.update_member", email=a.email, active=False)                         # A nghỉ việc
    again = await _intake(admin, phone=phone, offering_code="PJD")
    assert again["assigned_to"] not in (None, a.user_id)                                         # không giao cho người đã khoá
