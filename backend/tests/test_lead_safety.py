"""Lead an toàn (0153) — những lỗi đã đo được ở hệ cũ của khách đầu tiên KHÔNG có đường lặp lại ở đây:

1. không trạng thái ngõ cụt: hàng chờ (kể cả hết số lần thu hồi) có tuổi + có chủ, báo lặp lại tới khi có người nhận; quét xoay vòng;
2. người giữ nghỉ việc / rời đội / rời [[offering|l]] ⇒ lead chuyển NGAY qua luật chia, không tính thu hồi, có trong sổ sự kiện;
3. MỘT nguồn "ai giữ lead" (crm.leads.owner_id) — trạng thái chia đi theo trong cùng giao dịch, lưu lại không đặt lại đồng hồ;
4. webhook tổng đài: chữ ký thời gian hằng, chống lặp ở DB, thân lạ ⇒ 4xx, không xử lý nặng theo sự kiện — kiểm ở gói tổng đài (repo gói: ``telephony/tests/test_telephony_hooks.py``);
5. chặng lead là dữ liệu công ty, DB ép chặng thuộc đúng giai đoạn;
6. bấm nhận / thu hồi: chỉ người giữ, trước hạn, khoá dòng;
+ đêm / ngoài giờ / đội trực: không ai nhận lead "đã trễ", hạn không tràn sang ca trực hôm sau.

Mỗi test dựng MỘT công ty riêng."""

import itertools
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

import barista.modules.leadflow as lf
from barista import seed
from barista.db import db
from barista.kernel import events, executor
from barista.kernel.clock import VN, today
from barista.kernel.principal import Principal, system_principal
from barista.kernel.workhours import Hours, add_working_minutes
from tests.conftest import Who, fresh_phone, ok, owner_val
from tests.module_src import module_files, rel

_seq = itertools.count(1)
PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("truong", "Trưởng nhóm A", "team_lead", "KD-A", True),
          ("a", "Sale A", "sale", "KD-A", False), ("b", "Sale B", "sale", "KD-A", False), ("c", "Sale C", "sale", "KD-B", False),
          ("truc", "Sale Trực", "sale", "KD-C", False)]
SRC = Path(__file__).resolve().parents[1] / "src" / "barista"


async def _company(packs: tuple[str, ...] | None = None) -> tuple[UUID, dict[str, Principal]]:
    domain = f"ls{next(_seq)}.demo"
    tid = await seed.create_tenant(f"Công ty an toàn lead {domain}", domain, people=PEOPLE, packs=packs)
    return tid, {k[0]: await seed.who(tid, k[0], domain) for k in PEOPLE}


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


async def _owner(lead: UUID) -> Any:
    return await owner_val("SELECT owner_id FROM crm.leads WHERE id = %s", (lead,))


async def _events(lead: UUID, kind: str) -> list[dict[str, Any]]:
    return list(await owner_val("SELECT coalesce(jsonb_agg(data ORDER BY id), '[]') FROM leadflow.sla_events WHERE lead_id = %s AND kind = %s",
                                (lead, kind)))


async def _lead_held_by(pp: dict[str, Principal], user: str, **kw: Any) -> dict[str, Any]:
    """Nạp lead tới khi luật xoay vòng giao cho đúng người ``user`` (đội nhận có hai sale)."""
    for _ in range(4):
        out = await _intake(pp["admin"], **kw)
        if out["assigned_to"] == pp[user].user_id:
            return out
    raise AssertionError(f"không chia được lead cho {user}")


async def _holders_match_owners(tid: UUID) -> int:
    """Bất biến 0153: mọi lead đang có người giữ — người giữ bên chia lead = người phụ trách bên Khách. Trả số dòng LỆCH."""
    n: int = await owner_val("""SELECT count(*) FROM leadflow.lead_state s JOIN crm.leads l ON l.id = s.lead_id
                                WHERE s.tenant_id = %s AND s.status IN ('offered', 'waiting', 'contacted', 'queued')
                                  AND s.holder_id IS DISTINCT FROM l.owner_id""", (tid,))
    return n


def _vn(d: date, h: int, mi: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, mi, tzinfo=VN)


def _next_monday() -> date:
    d = today()
    return d + timedelta(days=(7 - d.weekday()) or 7)


# ── 3. MỘT nguồn "ai giữ lead" ──

def test_only_one_place_writes_the_lead_owner_and_only_leadflow_writes_the_holder() -> None:
    """Chỗ GHI người phụ trách lead chỉ có ``crm._set_owner``; người giữ bên chia lead chỉ ghi trong ``leadflow/routing.py`` (đi theo người phụ trách)."""
    owner_writes, holder_writes = [], []
    for f in (f for files in module_files().values() for f in files):   # module của lõi + của gói ngoài đang cài (0191)
        text = f.read_text(encoding="utf-8")
        for sql in re.findall(r"UPDATE crm\.leads SET[^\"]*", text):
            if "owner_id" in sql:
                owner_writes.append(rel(f))
        writes = [*re.findall(r"UPDATE leadflow\.lead_state SET[^\"]*", text), *re.findall(r"INSERT INTO leadflow\.lead_state[^\"]*", text),
                  *re.findall(r"DO UPDATE SET[^\"]*", text)]
        if any("holder_id" in w for w in writes):
            holder_writes.append(rel(f))
    assert owner_writes == ["modules/crm/__init__.py"], owner_writes
    assert sorted(set(holder_writes)) == ["modules/leadflow/routing.py"], holder_writes


async def test_customer_card_reassignment_moves_the_routing_state_in_the_same_transaction() -> None:
    tid, pp = await _company()
    await _rule(pp["admin"], team_code="KD-A")
    out = await _lead_held_by(pp, "a")
    lead = out["lead_id"]
    await _exec("UPDATE leadflow.lead_state SET first_call_due = now() - interval '1 minute' WHERE lead_id = %s", (lead,))
    await ok(pp["truong"], "crm.assign", lead_id=lead, owner_email=pp["b"].email)
    assert await _state(lead, "holder_id") == pp["b"].user_id and await _state(lead, "first_call_due") > datetime.now(VN)   # không đợi vòng quét
    assert await _events(lead, "handed_over") == [{"to": str(pp["b"].user_id), "why": "manager", "by": str(pp["truong"].user_id)}]
    assert await _state(lead, "recalls") == 0
    res = await _sweep(tid)
    assert res["breached"] == 0 and res["resynced"] == 0 and await _holders_match_owners(tid) == 0
    # đã liên hệ rồi mới chuyển: người giữ (nhắc / báo chăm sóc lại) cũng đi theo — hệ trước 0153 để người cũ nhận nhắc mãi
    await ok(pp["b"], "crm.log_activity", lead_id=lead, kind="call", outcome="connected")
    assert (await _sweep(tid))["met"] == 1
    await ok(pp["truong"], "crm.assign", lead_id=lead, owner_email=pp["a"].email)
    assert await _state(lead, "status") == "contacted" and await _state(lead, "holder_id") == pp["a"].user_id
    assert await _holders_match_owners(tid) == 0


async def test_saving_the_same_holder_again_never_resets_the_clock() -> None:
    """Hệ cũ: mỗi lần bấm lưu (kể cả đúng người cũ) ghi đè giờ phân công và xếp lại hẹn giờ 30' ⇒ lead sống mãi, không bao giờ thu hồi."""
    tid, pp = await _company()
    await _rule(pp["admin"], team_code="KD-A")
    out = await _lead_held_by(pp, "a")
    lead = out["lead_id"]
    due, assigned = await _state(lead, "first_call_due"), await _state(lead, "assigned_at")
    await ok(pp["a"], "crm.log_activity", lead_id=lead, kind="note", note="ghi chú")
    await ok(pp["a"], "crm.update_interest", lead_id=lead, wishes="cần tư vấn thêm")
    again = await ok(pp["truong"], "crm.assign", lead_id=lead, owner_email=pp["a"].email)
    assert again["unchanged"] is True
    assert await _state(lead, "first_call_due") == due and await _state(lead, "assigned_at") == assigned
    await _exec("UPDATE leadflow.lead_state SET first_call_due = now() - interval '1 minute' WHERE lead_id = %s", (lead,))
    assert (await _sweep(tid))["recalled"] == 1                                                  # đồng hồ vẫn chạy ⇒ thu hồi đúng hạn


async def test_queued_lead_assigned_on_the_customer_card_leaves_the_queue_for_good() -> None:
    tid, pp = await _company()
    out = await _intake(pp["admin"])                                                             # chưa có luật ⇒ hàng chờ
    assert out["assigned_to"] is None and await _state(out["lead_id"], "status") == "queued"
    await ok(pp["admin"], "crm.assign", lead_id=out["lead_id"], owner_email=pp["c"].email)
    assert await _state(out["lead_id"], "status") != "queued" and await _state(out["lead_id"], "holder_id") == pp["c"].user_id
    await _rule(pp["admin"])                                                                     # có luật rồi: vòng chia lại KHÔNG cướp lead đã giao tay
    await _sweep(tid)
    assert await _owner(out["lead_id"]) == pp["c"].user_id and await _holders_match_owners(tid) == 0


# ── 1. Không trạng thái ngõ cụt ──

async def test_recall_limit_queue_ages_escalates_and_the_team_lead_assigns() -> None:
    """Thu hồi × tối đa ⇒ hàng chờ 'hết số lần' ⇒ sau N phút làm việc báo trưởng đội sở hữu hàng chờ ⇒ lần sau báo rộng lên quản lý ⇒ trưởng đội giao.
    Hệ cũ: 78 lead 'đang thu hồi' nằm từ 2022–2024, không job nào xét lại, không ai được báo."""
    tid, pp = await _company()
    await _rule(pp["admin"], team_code="KD-A", max_recalls=1, queue_escalate_min=30)
    out = await _intake(pp["admin"])
    lead = out["lead_id"]
    for _ in range(2):
        await _exec("UPDATE leadflow.lead_state SET first_call_due = now() - interval '1 minute' WHERE lead_id = %s", (lead,))
        await _sweep(tid)
    assert await _state(lead, "status") == "queued" and await _state(lead, "queue_reason") == "recall_limit"
    team_a = await owner_val("SELECT id FROM core.teams WHERE tenant_id = %s AND code = 'KD-A'", (tid,))
    assert await _state(lead, "queue_team_id") == team_a
    assert (await _sweep(tid))["queue_escalated"] == 0                                            # chưa tới tuổi
    await _exec("UPDATE leadflow.lead_state SET queued_at = now() - interval '31 minutes' WHERE lead_id = %s", (lead,))
    assert (await _sweep(tid))["queue_escalated"] == 1
    first = (await _events(lead, "escalated"))[-1]
    assert first["queue"] is True and first["n"] == 1 and first["to"] == [str(pp["truong"].user_id)]
    assert await owner_val("""SELECT count(*) FROM core.notifications WHERE user_id = %s AND kind = 'lead_escalated' AND tenant_id = %s
                              AND title LIKE 'Lead chờ chia%%'""", (pp["truong"].user_id, tid)) == 1
    assert (await _sweep(tid))["queue_escalated"] == 0                                            # vừa báo: đợi thêm N phút
    await _exec("UPDATE leadflow.lead_state SET queue_escalated_at = now() - interval '31 minutes' WHERE lead_id = %s", (lead,))
    assert (await _sweep(tid))["queue_escalated"] == 1
    second = (await _events(lead, "escalated"))[-1]
    assert second["n"] == 2 and str(pp["admin"].user_id) in second["to"]                         # lần hai: rộng lên quản lý cả công ty
    blocked = await executor.run(pp["a"], "leadflow.assign", {"lead_id": str(lead), "owner_email": pp["a"].email})
    assert blocked.status == "not_found"                                                          # sale không thấy / không tự giao hàng chờ
    await ok(pp["truong"], "leadflow.assign", lead_id=str(lead), owner_email=pp["a"].email)
    assert await _state(lead, "status") == "waiting" and await _state(lead, "holder_id") == pp["a"].user_id
    assert await _state(lead, "queue_escalations") == 0 and await _holders_match_owners(tid) == 0


async def test_unruled_queue_escalates_to_company_managers() -> None:
    tid, pp = await _company()
    out = await _intake(pp["admin"])                                                             # chưa có luật ⇒ hàng chờ cả công ty
    await _exec("UPDATE leadflow.lead_state SET queued_at = now() - interval '5 hours' WHERE lead_id = %s", (out["lead_id"],))
    assert (await _sweep(tid))["queue_escalated"] == 1
    to = (await _events(out["lead_id"], "escalated"))[-1]["to"]
    assert str(pp["admin"].user_id) in to and str(pp["a"].user_id) not in to


async def test_rule_has_no_way_to_turn_queue_escalation_off() -> None:
    _, pp = await _company()
    r = await executor.run(pp["admin"], "leadflow.set_rule", {"name": "x", "effective_from": today().isoformat(), "queue_escalate_min": 0})
    assert r.status == "invalid"


async def test_the_sweep_rotates_so_no_open_lead_is_starved(monkeypatch: pytest.MonkeyPatch) -> None:
    import barista.modules.leadflow.sweep as sweep

    tid, pp = await _company()
    await _rule(pp["admin"])
    leads = [(await _intake(pp["admin"]))["lead_id"] for _ in range(3)]
    monkeypatch.setattr(sweep, "SWEEP_LIMIT", 2)
    await _sweep(tid)
    await _sweep(tid)
    assert all([await _state(lead, "checked_at") is not None for lead in leads])


# ── 2. Người giữ rời đi ⇒ chuyển ngay, không tính thu hồi ──

async def test_deactivated_holder_hands_over_every_open_lead_at_once() -> None:
    tid, pp = await _company()
    await _rule(pp["admin"], team_code="KD-A")
    fresh = await _lead_held_by(pp, "a")
    talked = await _lead_held_by(pp, "a")
    await ok(pp["a"], "crm.log_activity", lead_id=talked["lead_id"], kind="call", outcome="connected")
    await _sweep(tid)
    assert await _state(talked["lead_id"], "status") == "contacted"
    await ok(pp["admin"], "admin.update_member", email=pp["a"].email, active=False)
    await events.drain()
    for lead in (fresh["lead_id"], talked["lead_id"]):
        assert await _owner(lead) == pp["b"].user_id and await _state(lead, "holder_id") == pp["b"].user_id
        assert await _state(lead, "recalls") == 0 and await _events(lead, "recalled") == []
        assert (await _events(lead, "handed_over"))[-1]["why"] == "deactivated"
    assert await _holders_match_owners(tid) == 0


async def test_holder_moved_out_of_the_rule_team_hands_over() -> None:
    _tid, pp = await _company()
    await _rule(pp["admin"], team_code="KD-A")
    out = await _lead_held_by(pp, "a")
    kept = await _intake(pp["admin"], owner_email=pp["c"].email)                                 # giao tay cho người NGOÀI đội nhận: không chuyển
    await ok(pp["admin"], "admin.update_member", email=pp["a"].email, team_code="KD-B")
    await events.drain()
    assert await _owner(out["lead_id"]) == pp["b"].user_id and (await _events(out["lead_id"], "handed_over"))[-1]["why"] == "left_team"
    await ok(pp["admin"], "admin.update_member", email=pp["c"].email, team_code="KD-C")
    await events.drain()
    assert await _owner(kept["lead_id"]) == pp["c"].user_id                                       # c không giữ lead VÌ thuộc đội


async def test_a_self_made_lead_of_someone_who_left_is_rerouted_even_if_the_event_was_missed() -> None:
    tid, pp = await _company()
    await _rule(pp["admin"], team_code="KD-A")
    made = await ok(pp["a"], "crm.create_lead", name="Khách tự kiếm", phone=fresh_phone())     # lead tự tạo: chưa từng qua chia lead
    await _exec("UPDATE core.memberships SET active = false WHERE tenant_id = %s AND user_id = %s", (tid, pp["a"].user_id))   # không có sự kiện
    res = await _sweep(tid)
    lead = UUID(str(made["lead_id"]))
    assert res["handed_over"] >= 1 and await _owner(lead) == pp["b"].user_id
    assert (await _events(lead, "handed_over"))[-1]["why"] == "deactivated"


# ── 6. Bấm nhận + thu hồi: chỉ người giữ, trước hạn, khoá dòng ──

async def test_accept_only_by_the_holder_before_the_deadline_and_never_after_recall() -> None:
    tid, pp = await _company()
    await _rule(pp["admin"], team_code="KD-A", accept_min=5)
    out = await _lead_held_by(pp, "a")
    lead = str(out["lead_id"])
    for intruder in ("b", "truong", "c"):                                                        # hệ cũ: ai đăng nhập cũng nhận được lead bất kỳ
        r = await executor.run(pp[intruder], "leadflow.accept", {"lead_id": lead})
        assert r.status == "not_found", intruder
    assert await _state(out["lead_id"], "status") == "offered"
    await _exec("UPDATE leadflow.lead_state SET accept_due = now() - interval '1 minute' WHERE lead_id = %s", (out["lead_id"],))
    late = await executor.run(pp["a"], "leadflow.accept", {"lead_id": lead})
    assert late.status == "conflict"                                                             # quá hạn: không "nhận vớt" trước vòng thu hồi
    assert (await _sweep(tid))["accept_missed"] == 1 and await _state(out["lead_id"], "holder_id") == pp["b"].user_id
    after = await executor.run(pp["a"], "leadflow.accept", {"lead_id": lead})
    assert after.status == "not_found"
    assert "Đã nhận" in (await ok(pp["b"], "leadflow.accept", lead_id=lead))["summary"]


async def test_recall_skips_a_lead_someone_is_working_on_then_takes_it() -> None:
    """Thu hồi khoá dòng (FOR UPDATE SKIP LOCKED): dòng đang bị giữ (vd người nhận đang bấm nhận) ⇒ lượt này bỏ qua, không xoá nửa chừng."""
    tid, pp = await _company()
    await _rule(pp["admin"], team_code="KD-A")
    out = await _lead_held_by(pp, "a")
    await _exec("UPDATE leadflow.lead_state SET first_call_due = now() - interval '1 minute' WHERE lead_id = %s", (out["lead_id"],))
    async with db.owner.connection() as conn, conn.transaction():
        await conn.execute("SELECT 1 FROM leadflow.lead_state WHERE lead_id = %s FOR UPDATE", (out["lead_id"],))
        assert (await _sweep(tid))["recalled"] == 0
    assert await _owner(out["lead_id"]) == pp["a"].user_id
    assert (await _sweep(tid))["recalled"] == 1 and await _owner(out["lead_id"]) == pp["b"].user_id
    assert await _holders_match_owners(tid) == 0


# ── Đêm, giờ đóng cửa, đội trực ──

def test_duty_clock_runs_into_the_next_regular_morning_not_the_next_duty_shift() -> None:
    mon = _next_monday()
    work = Hours((1, 2, 3, 4, 5, 6), 510, 1080)
    duty = Hours((1, 2, 3, 4, 5, 6, 7), 1080, 1320)
    assert lf.due_in(_vn(mon, 17, 50), 120, (work,)) == _vn(mon + timedelta(days=1), 10, 20)
    assert lf.due_in(_vn(mon, 21, 0), 120, (duty, work)) == _vn(mon + timedelta(days=1), 9, 30)   # 60' ca trực + 60' sáng mai
    assert add_working_minutes(_vn(mon, 21, 0), 120, duty) == _vn(mon + timedelta(days=1), 19, 0)   # lỗi cũ: đợi tới ca trực tối hôm sau
    assert lf.due_in(_vn(mon, 17, 30), 60, (duty, work)) == _vn(mon, 18, 30)                     # 18:00 hết giờ làm, 18:00 vào ca trực: chạy liền


async def test_lead_at_1750_is_due_next_morning_and_is_not_recalled_overnight(monkeypatch: pytest.MonkeyPatch) -> None:
    mon = _next_monday()
    tue = mon + timedelta(days=1)
    tid, pp = await _company()
    await _rule(pp["admin"], team_code="KD-A", first_call_min=120, days="mon_sat", open_at="08:30", close_at="18:00")
    monkeypatch.setattr(lf, "clock", lambda: _vn(mon, 17, 50))
    out = await _intake(pp["admin"])
    assert await _state(out["lead_id"], "first_call_due") == _vn(tue, 10, 20)
    for at in (_vn(mon, 23, 0), _vn(tue, 3, 0), _vn(tue, 10, 19)):
        monkeypatch.setattr(lf, "clock", lambda at=at: at)
        res = await _sweep(tid)
        assert res["breached"] == 0 and res["recalled"] == 0, at
    monkeypatch.setattr(lf, "clock", lambda: _vn(tue, 10, 21))
    assert (await _sweep(tid))["recalled"] == 1
    assert await _state(out["lead_id"], "first_call_due") == _vn(tue, 12, 21)                     # người mới: đủ 120 phút làm việc


async def test_recall_at_closing_gives_the_new_holder_a_full_clock_and_no_night_alarm(monkeypatch: pytest.MonkeyPatch) -> None:
    mon = _next_monday()
    tue = mon + timedelta(days=1)
    tid, pp = await _company()
    await _rule(pp["admin"], team_code="KD-A", first_call_min=120, remind_before_min=30, days="mon_sat", open_at="08:30", close_at="18:00")
    monkeypatch.setattr(lf, "clock", lambda: _vn(mon, 15, 0))
    out = await _lead_held_by(pp, "a")
    await _exec("UPDATE leadflow.lead_state SET first_call_due = %s WHERE tenant_id = %s AND lead_id <> %s", (_vn(tue, 17, 0), tid, out["lead_id"]))
    await _exec("UPDATE leadflow.lead_state SET first_call_due = %s WHERE lead_id = %s", (_vn(mon, 17, 59), out["lead_id"]))
    monkeypatch.setattr(lf, "clock", lambda: _vn(mon, 18, 0))
    assert (await _sweep(tid))["recalled"] == 1
    lead = out["lead_id"]
    assert await _state(lead, "holder_id") == pp["b"].user_id
    assert await _state(lead, "first_call_due") == _vn(tue, 10, 30)                               # không ai nhận lead "đã trễ"
    assert await _state(lead, "remind_at") == _vn(tue, 10, 0)
    assert await _assign_push(tid, pp["b"].user_id, lead) == ""                                   # báo trong app, không rung máy lúc ngoài giờ
    for at in (_vn(mon, 23, 30), _vn(tue, 7, 0)):
        monkeypatch.setattr(lf, "clock", lambda at=at: at)
        res = await _sweep(tid)
        assert res["reminded"] == 0 and res["breached"] == 0 and res["recalled"] == 0, at


async def _assign_push(tid: UUID, user: UUID | None, lead: UUID) -> str:
    """Tin đẩy của thông báo "Lead mới" CỦA ĐÚNG lead này (không lấy "thông báo mới nhất" — lead khác của cùng người, nhắc hạn, đều cùng lúc giao dịch)."""
    got: str = await owner_val("""SELECT push FROM core.notifications WHERE tenant_id = %s AND user_id = %s AND kind = 'lead' AND title LIKE 'Lead mới%%'
                                  AND open->'input'->>'lead_id' = %s ORDER BY id DESC LIMIT 1""", (tid, user, str(lead)))
    return got


WORK = Hours((1, 2, 3, 4, 5, 6), 510, 1080)                       # T2–T7 08:30–18:00 — cùng luật dùng ở các test dưới
# (ngày lệch so với thứ Hai tuần sau, giờ, phút): đêm, sát giờ mở / đóng, giữa ngày, cuối tuần
CLOCKS = [(0, 0, 30), (0, 1, 50), (0, 8, 29), (0, 12, 0), (0, 17, 59), (0, 18, 1), (0, 23, 59), (5, 17, 59), (6, 12, 0), (6, 0, 30)]


@pytest.mark.parametrize(("day", "h", "mi"), CLOCKS, ids=[f"+{d}d-{h:02d}:{m:02d}" for d, h, m in CLOCKS])
async def test_any_hour_the_holder_gets_a_full_clock_and_is_never_woken_outside_hours(freeze_clock: Callable[[datetime], None],
                                                                                      day: int, h: int, mi: int) -> None:
    """Dù lead về / bị thu hồi lúc nào (đêm, sát giờ đóng, Chủ nhật): hạn = đúng 120 phút LÀM VIỆC từ lúc nhận, không ai nhận lead "đã trễ", không
    thu hồi trước hạn, tin đẩy chỉ rung trong giờ làm việc. Test không đọc giờ máy."""
    t0 = _vn(_next_monday() + timedelta(days=day), h, mi)
    tid, pp = await _company()
    await _rule(pp["admin"], team_code="KD-A", first_call_min=120, days="mon_sat", open_at="08:30", close_at="18:00")
    freeze_clock(t0)
    out = await _intake(pp["admin"])
    lead, first = out["lead_id"], out["assigned_to"]
    assert first is not None
    due = add_working_minutes(t0, 120, WORK)
    assert await _state(lead, "first_call_due") == due and add_working_minutes(due, 0, WORK) == due        # hạn rơi trong giờ làm việc
    assert due >= t0 + timedelta(minutes=120)                                                               # không bao giờ ngắn hơn 120 phút
    assert (await _assign_push(tid, first, lead) != "") == (add_working_minutes(t0, 0, WORK) == t0)          # rung máy chỉ khi đang giờ làm việc
    for at in (t0, due - timedelta(minutes=1)):
        freeze_clock(at)
        res = await _sweep(tid)
        assert res["breached"] == 0 and res["recalled"] == 0, at
    late = due + timedelta(minutes=1)
    freeze_clock(late)
    assert (await _sweep(tid))["recalled"] == 1
    second = await _state(lead, "holder_id")
    assert second not in (None, first)
    new_due = await _state(lead, "first_call_due")
    assert new_due == add_working_minutes(late, 120, WORK) and new_due > late                               # người mới: đủ 120 phút làm việc, chưa trễ
    assert (await _assign_push(tid, second, lead) != "") == (add_working_minutes(late, 0, WORK) == late)


async def test_holiday_does_not_run_the_clock(freeze_clock: Callable[[datetime], None]) -> None:
    """Lịch lễ trong đồng hồ hạn: 31/08 – 02/09/2026 là ngày nghỉ (lịch vn-public) ⇒ lead về trưa 31/08 có hạn từ sáng 03/09."""
    tid, pp = await _company()
    await _rule(pp["admin"], team_code="KD-A", first_call_min=120, days="mon_sat", open_at="08:30", close_at="18:00", calendar="vn-public")
    freeze_clock(_vn(date(2026, 8, 31), 12, 0))
    out = await _intake(pp["admin"])
    assert await _state(out["lead_id"], "first_call_due") == _vn(date(2026, 9, 3), 10, 30)
    freeze_clock(_vn(date(2026, 9, 3), 10, 29))
    assert (await _sweep(tid))["recalled"] == 0


async def test_duty_team_lead_at_21h_is_due_next_morning_not_tomorrow_night(monkeypatch: pytest.MonkeyPatch) -> None:
    mon = _next_monday()
    _tid, pp = await _company()
    await _rule(pp["admin"], team_code="KD-A", first_call_min=120, days="mon_sat", open_at="08:30", close_at="18:00",
                off_hours="duty", duty_team_code="KD-C", duty_days="all", duty_open_at="18:00", duty_close_at="22:00")
    monkeypatch.setattr(lf, "clock", lambda: _vn(mon, 21, 0))
    out = await _intake(pp["admin"])
    assert out["assigned_to"] == pp["truc"].user_id and await _state(out["lead_id"], "duty") is True
    assert await _state(out["lead_id"], "first_call_due") == _vn(mon + timedelta(days=1), 9, 30)





async def test_the_first_real_contact_moves_a_new_lead_on_and_nothing_else_does(who: Who) -> None:
    """Rà 23/11/2026: lead đã được chăm sóc vẫn nằm ở "Mới" ⇒ phễu vô nghĩa. Lần đầu nói chuyện được (gọi nghe máy / gặp / dẫn xem) chuyển lead từ chặng
    ĐẦU TIÊN sang chặng kế; gọi không nghe, ghi chú, hay lead đã ở chặng khác thì không đổi."""
    sale = await who("sale1")
    lead = (await ok(sale, "crm.create_lead", name="Khách lần đầu", phone=fresh_phone()))["lead_id"]
    stage = lambda: owner_val("SELECT pipeline_stage FROM crm.leads WHERE id = %s", (lead,))   # noqa: E731
    first = await stage()
    await ok(sale, "crm.log_activity", lead_id=str(lead), kind="call", outcome="no_answer")
    await ok(sale, "crm.log_activity", lead_id=str(lead), kind="note", note="ghi chú nội bộ")
    assert await stage() == first
    out = await ok(sale, "crm.log_activity", lead_id=str(lead), kind="call", outcome="connected", note="khách nghe, hẹn gửi thông tin")
    moved = await stage()
    assert moved != first and out["stage_moved_to"]
    assert await owner_val("SELECT count(*) FROM crm.stage_events WHERE lead_id = %s AND kind = 'pipeline'", (lead,)) >= 1   # đi qua lệnh chuyển chặng
    await ok(sale, "crm.log_activity", lead_id=str(lead), kind="meet")
    assert await stage() == moved                                                    # chỉ lần đầu, chỉ từ chặng đầu
