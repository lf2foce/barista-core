"""Nạp lịch sử chia lead + trạng thái chia + nhật ký nhập từ hệ khác (0168): không thông báo, không đụng sổ sự kiện Barista tự ghi, chống lặp,
chỉ quản lý cả công ty, trợ lý không chạy; người giữ luôn là người phụ trách bên Khách; lần nhập file thật vào nhật ký nhập."""

import itertools
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from barista import seed
from barista.db import db
from barista.kernel import executor, imports
from barista.kernel.clock import VN, today
from barista.kernel.principal import Principal, system_principal
from tests.conftest import fresh_phone, ok, owner_val, q

_seq = itertools.count(1)
PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("giamdoc", "Giám đốc", "director", "HQ", True),
          ("a", "Sale A", "sale", "KD-A", False), ("b", "Sale B", "sale", "KD-A", False)]


async def _company() -> tuple[UUID, str, dict[str, Principal]]:
    domain = f"lh{next(_seq)}.demo"
    tid = await seed.create_tenant(f"Công ty lịch sử {domain}", domain, people=PEOPLE)
    return tid, domain, {k: await seed.who(tid, k, domain) for k, *_ in PEOPLE}


async def _lead(p: Principal, owner: str) -> UUID:
    out = await ok(p, "crm.create_lead", name="Khách cũ", phone=fresh_phone(), owner_email=owner, source="import")
    return UUID(str(out["lead_id"]))


def _at(days_ago: int, h: int = 9) -> str:
    return (datetime.now(VN).replace(hour=h, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)).isoformat()


async def _row(sql: str, *params: Any) -> dict[str, Any]:
    async with db.owner.connection() as conn:
        got = await (await conn.execute(sql, params)).fetchone()   # type: ignore[arg-type]
    assert got is not None
    return dict(got)


async def _count(sql: str, *params: Any) -> int:
    return int(await owner_val(sql, params) or 0)


async def test_history_rows_are_kept_apart_quiet_and_idempotent() -> None:
    tid, d, w = await _company()
    lid = await _lead(w["admin"], f"a@{d}")
    notes = await _count("SELECT count(*) FROM core.notifications WHERE tenant_id = %s", tid)
    events = await _count("SELECT count(*) FROM leadflow.sla_events WHERE tenant_id = %s", tid)
    row = {"lead_id": str(lid), "origin": "he_cu", "lead_ref": "D-1", "ext_ref": "a-1", "kind": "held", "user_email": f"a@{d}",
           "at": _at(3), "until": _at(3, 11), "started": "rule", "ended": "recalled", "why": "hết hạn gọi"}
    r = await imports.run_import(w["admin"], "leadflow.import_history", [row, {**row, "ext_ref": "a-2", "lead_id": "", "user_email": "",
                                                                                 "user_ref": "u-77", "at": _at(2), "until": "", "ended": "",
                                                                                 "started": "manual"}], dry_run=False)
    assert r.status == "ok" and r.output["ok"] == 2, (r.output["rows"], r.output["unknown_columns"])
    again = await executor.run(w["admin"], "leadflow.import_history", row)
    assert again.status == "ok" and again.output["inserted"] is False
    assert await _count("SELECT count(*) FROM leadflow.history WHERE tenant_id = %s", tid) == 2
    assert await _count("SELECT count(*) FROM leadflow.history WHERE tenant_id = %s AND user_id IS NULL AND user_ref = 'u-77'", tid) == 1
    assert await _count("SELECT count(*) FROM leadflow.external_refs WHERE tenant_id = %s AND provider = 'he_cu' AND ref = 'D-1'", tid) == 1
    # không thông báo, không chạm sổ sự kiện Barista, không tạo trạng thái chia
    assert await _count("SELECT count(*) FROM core.notifications WHERE tenant_id = %s", tid) == notes
    assert await _count("SELECT count(*) FROM leadflow.sla_events WHERE tenant_id = %s", tid) == events
    assert await _count("SELECT count(*) FROM leadflow.lead_state WHERE tenant_id = %s", tid) == 0
    card = await q(w["admin"], "leadflow.lead_history", lead_id=str(lid))
    assert len(card["rows"]) == 2
    # nhật ký nhập ghi lần nhập thật
    runs = await q(w["admin"], "core.import_runs", source="live")
    assert any(x["command"] == "leadflow.import_history" and x["ok"] == 2 for x in runs["rows"])


async def test_history_needs_a_zoned_past_time_a_company_manager_and_never_the_agent() -> None:
    _, d, w = await _company()
    lid = await _lead(w["admin"], f"a@{d}")
    base = {"lead_id": str(lid), "origin": "he_cu", "ext_ref": "x-1", "kind": "held", "user_email": f"a@{d}", "at": _at(1)}
    naive = await executor.run(w["admin"], "leadflow.import_history", {**base, "at": "2026-05-04T09:00:00"})
    assert naive.status == "invalid"
    future = await executor.run(w["admin"], "leadflow.import_history", {**base, "at": (datetime.now(VN) + timedelta(days=1)).isoformat()})
    assert future.status == "invalid"
    bad_origin = await executor.run(w["admin"], "leadflow.import_history", {**base, "origin": "Hệ cũ"})
    assert bad_origin.status == "invalid"
    sale = await executor.run(w["a"], "leadflow.import_history", base)
    assert sale.status == "denied"
    agent = await executor.run(w["admin"].as_agent(), "leadflow.import_history", base)
    assert agent.status in ("denied", "proposed") and agent.status != "ok"
    other = await _lead(w["admin"], f"b@{d}")
    await ok(w["admin"], "leadflow.import_history", **{**base, "lead_ref": "R-9"})
    clash = await executor.run(w["admin"], "leadflow.import_history", {**base, "lead_id": str(other), "lead_ref": "R-9", "ext_ref": "x-2"})
    assert clash.status == "conflict"


async def _members_rule(admin: Principal) -> None:
    await ok(admin, "leadflow.set_rule", name="Chia lead", effective_from=(today() - timedelta(days=1)).isoformat(), first_call_min=120,
             max_recalls=3, days="all", open_at="08:00", close_at="22:00")


async def test_state_follows_the_crm_owner_queues_clear_the_owner_and_live_state_is_never_overwritten() -> None:
    tid, d, w = await _company()
    boss = w["admin"]
    await _members_rule(boss)
    waiting = await _lead(boss, f"a@{d}")
    out = await ok(boss, "leadflow.import_state", lead_id=str(waiting), origin="he_cu", lead_ref="W-1", status="waiting",
                   assigned_at=_at(1), recalls=2, holders=[f"b@{d}"], holder_email=f"b@{d}")
    assert out["status"] == "waiting" and out["holder_matches"] is False and out["past_due"] is True
    st = await _row("SELECT holder_id, holders, recalls, imported_at IS NOT NULL AS imp FROM leadflow.lead_state WHERE lead_id = %s", waiting)
    assert st["holder_id"] == w["a"].user_id and set(st["holders"]) == {w["a"].user_id, w["b"].user_id} and st["recalls"] == 2 and st["imp"]
    queued = await _lead(boss, f"b@{d}")
    await ok(boss, "leadflow.import_state", lead_id=str(queued), origin="he_cu", status="queued", recalls=3, queue_reason="recall_limit")
    assert await owner_val("SELECT owner_id FROM crm.leads WHERE id = %s", (queued,)) is None
    assert await owner_val("SELECT status FROM leadflow.lead_state WHERE lead_id = %s", (queued,)) == "queued"
    lost = await _lead(boss, f"a@{d}")
    await ok(boss, "crm.set_stage", lead_id=str(lost), stage="lost", lost_reason="Không còn nhu cầu (hệ cũ)")
    await ok(boss, "leadflow.import_state", lead_id=str(lost), origin="he_cu", status="waiting", assigned_at=_at(5), recalls=7)
    assert await owner_val("SELECT status FROM leadflow.lead_state WHERE lead_id = %s", (lost,)) == "closed"
    # trạng thái nạp lại ⇒ ghi đè được; trạng thái Barista tự chia ⇒ không
    await ok(boss, "leadflow.import_state", lead_id=str(waiting), origin="he_cu", status="contacted", assigned_at=_at(1), first_call_at=_at(1, 10))
    assert await owner_val("SELECT status FROM leadflow.lead_state WHERE lead_id = %s", (waiting,)) == "contacted"
    live = await ok(boss, "leadflow.intake", name="Khách mới", phone=fresh_phone())
    clash = await executor.run(boss, "leadflow.import_state", {"lead_id": live["lead_id"], "origin": "he_cu", "status": "waiting",
                                                               "assigned_at": _at(1)})
    assert clash.status == "conflict"
    # lead nạp là lead ĐỖ (0185): quá hạn gọi, đã hết số lần thu hồi — vòng quét vẫn KHÔNG chấm / thu hồi / đưa vào hàng chờ (dữ liệu hệ cũ là tham chiếu)
    redo = await _lead(boss, f"a@{d}")
    await ok(boss, "leadflow.import_state", lead_id=str(redo), origin="he_cu", status="waiting", assigned_at=_at(2), recalls=3)
    swept = await ok(system_principal(tid), "leadflow.enforce_sla")
    assert swept["queued"] == 0 and swept["breached"] == 0 and swept["recalled"] == 0, swept
    assert await owner_val("SELECT status FROM leadflow.lead_state WHERE lead_id = %s", (redo,)) == "waiting"
    assert await _count("SELECT count(*) FROM leadflow.sla_events WHERE lead_id = %s", redo) == 0


async def test_past_import_runs_land_in_the_import_log_once() -> None:
    tid, d, w = await _company()
    row = {"origin": "he_cu", "run_ref": "415", "run_kind": "lead", "file_name": "lead-thang-9.xlsx", "run_by_email": f"a@{d}",
           "started_at": _at(30), "declared": '{"total": 2, "success": 1, "failed": 1}', "line": 1, "status": "ok",
           "data": '{"phone": "0100000001"}', "ref": "D-1"}
    rows = [row, {**row, "line": 2, "status": "failed", "error": "dự án không tồn tại", "ref": ""}]
    r = await imports.run_import(w["admin"], "core.import_past_run", rows, dry_run=False)
    assert r.status == "ok" and r.output["ok"] == 2, (r.output["rows"], r.output["unknown_columns"])
    again = await executor.run(w["admin"], "core.import_past_run", rows[1])
    assert again.status == "ok" and again.output["inserted"] is False
    got = await _row("SELECT total, ok, failed, source, by_user FROM core.import_runs WHERE tenant_id = %s AND source = 'import'", tid)
    assert (got["total"], got["ok"], got["failed"], got["source"], got["by_user"]) == (2, 1, 1, "import", w["a"].user_id)
    # nội dung dòng (có thể có dữ liệu cá nhân) không chép vào nhật ký thao tác
    logged = await owner_val("SELECT input FROM core.audit_log WHERE tenant_id = %s AND command = 'core.import_past_run' ORDER BY id DESC LIMIT 1", (tid,))
    assert "0100000001" not in str(logged)
    runs = await q(w["admin"], "core.import_runs", source="import")
    assert runs["rows"][0]["failed"] == 1
    bad = await q(w["admin"], "core.import_run", run_id=str(runs["rows"][0]["id"]))
    assert [x["line"] for x in bad["rows"]] == [2] and "data" not in bad["rows"][0]
    sale = await executor.run(w["a"], "core.import_past_run", row)
    assert sale.status == "denied"
