"""0174 — nạp chăm sóc từ hệ khác giữ giờ gốc (crm.import_activity) + bằng chứng liên hệ của vòng quét; người phụ trách = người đang giữ ở hệ nguồn khi
nạp trạng thái chia; đổi sale / đội của giao dịch có vết; hạn giữ nội dung dòng nhập file là dữ liệu công ty, vòng quét xoá nội dung, giữ số đếm."""

from typing import Any
from uuid import UUID

from barista.db import db
from barista.kernel import executor, imports
from barista.kernel.principal import system_principal
from tests.conftest import ok, owner_val, q
from tests.test_leadflow_history import _at, _company, _count, _lead, _members_rule, _row


def _act(lead: UUID, ref: str, **kw: Any) -> dict[str, Any]:
    return {"lead_id": str(lead), "origin": "he_cu", "ext_ref": ref, "kind": "call", "outcome": "connected", "at": _at(2, 10), **kw}


async def test_imported_care_keeps_original_time_and_author_quietly_once() -> None:
    tid, d, w = await _company()
    lid = await _lead(w["admin"], f"a@{d}")
    notes = await _count("SELECT count(*) FROM core.notifications WHERE tenant_id = %s", tid)
    follow = await owner_val("SELECT next_follow_up FROM crm.leads WHERE id = %s", (lid,))
    rows = [_act(lid, "hi-1", author_email=f"a@{d}", note="Khách hẹn xem nhà 0100000009"),
            _act(lid, "hi-2", author_email="", author_ref="u-77", kind="meet", outcome="", at=_at(1, 15), source_label="Trực tiếp"),
            _act(lid, "hi-3", kind="note", outcome="", at=_at(1, 16), source_label="Khách để lại lời nhắn")]   # hôm qua 16h: mới hơn buổi gặp, luôn ở quá khứ (mốc "hôm nay 8h" đỏ khi chạy trước 8h sáng)
    r = await imports.run_import(w["admin"], "crm.import_activity", rows, dry_run=False)
    assert r.status == "ok" and r.output["ok"] == 3, r.output["rows"]
    again = await executor.run(w["admin"], "crm.import_activity", rows[0])
    assert again.status == "ok" and again.output["inserted"] is False
    got = await _row("""SELECT count(*) AS n, count(*) FILTER (WHERE by_user IS NULL AND author_ref = 'u-77') AS unmatched,
                               min(at) AS first, bool_and(source = 'import' AND imported_by IS NOT NULL) AS marked
                        FROM crm.activities WHERE lead_id = %s""", lid)
    assert got["n"] == 3 and got["unmatched"] == 1 and got["marked"] and got["first"].isoformat()[:13] == _at(2, 10)[:13]
    lead = await _row("SELECT last_activity_at, next_follow_up, stage, owner_id FROM crm.leads WHERE id = %s", lid)
    assert lead["last_activity_at"].isoformat()[:13] == _at(1, 15)[:13]   # giờ GỐC mới nhất không phải ghi chú — không phải lúc nạp
    assert lead["next_follow_up"] == follow and lead["stage"] == "lead" and lead["owner_id"] == w["a"].user_id
    assert await _count("SELECT count(*) FROM core.notifications WHERE tenant_id = %s", tid) == notes
    logged = await owner_val("SELECT input FROM core.audit_log WHERE tenant_id = %s AND command = 'crm.import_activity' ORDER BY id DESC LIMIT 1", (tid,))
    assert "0100000009" not in str(logged)   # nội dung chăm sóc không chép vào nhật ký thao tác


async def test_import_activity_refuses_naive_future_time_non_managers_and_the_agent() -> None:
    _, d, w = await _company()
    lid = await _lead(w["admin"], f"a@{d}")
    base = _act(lid, "x-1", author_email=f"a@{d}")
    assert (await executor.run(w["admin"], "crm.import_activity", {**base, "at": "2026-05-04T09:00:00"})).status == "invalid"
    assert (await executor.run(w["admin"], "crm.import_activity", {**base, "at": "2099-01-01T09:00:00+07:00"})).status == "invalid"
    assert (await executor.run(w["admin"], "crm.import_activity", {**base, "origin": "Hệ cũ"})).status == "invalid"
    assert (await executor.run(w["admin"], "crm.import_activity", {**base, "kind": "meet"})).status == "invalid"   # kết quả gọi chỉ cho cuộc gọi
    assert (await executor.run(w["admin"], "crm.import_activity", {**base, "author_email": "khong-co@x.y"})).status == "not_found"
    assert (await executor.run(w["a"], "crm.import_activity", base)).status == "denied"
    agent = await executor.run(w["admin"].as_agent(), "crm.import_activity", base)
    assert agent.status != "ok"


async def test_sweep_counts_imported_care_only_from_the_current_holder_since_they_got_the_lead() -> None:
    tid, d, w = await _company()
    boss = w["admin"]
    await _members_rule(boss)
    leads = [await _lead(boss, f"a@{d}") for _ in range(3)]
    for lid in leads:
        await ok(boss, "leadflow.import_state", lead_id=str(lid), origin="he_cu", status="waiting", assigned_at=_at(2, 9), recalls=0)
    # trạng thái nạp là lead ĐỖ (0185) — vòng quét bỏ qua. Phép thử này đo cách chấm bằng chứng liên hệ của lead SỐNG ⇒ bỏ dấu đỗ (như sau bước chuyển giao)
    async with db.owner.connection() as conn:
        await conn.execute("UPDATE leadflow.lead_state SET imported_at = NULL WHERE lead_id = ANY(%s)", (leads,))
    await ok(boss, "crm.import_activity", **_act(leads[0], "c-1", author_email=f"a@{d}", at=_at(2, 10)))   # người giữ, đúng hạn (hạn 11h)
    await ok(boss, "crm.import_activity", **_act(leads[1], "c-2", author_email=f"b@{d}", at=_at(2, 10)))   # người khác ⇒ không phải bằng chứng
    await ok(boss, "crm.import_activity", **_act(leads[2], "c-3", author_email=f"a@{d}", at=_at(3, 10)))   # trước lúc tới tay ⇒ không tính
    await ok(system_principal(tid), "leadflow.enforce_sla")
    st = {r["lead_id"]: r for r in [await _row("SELECT lead_id, status, first_call_ok, first_call_at FROM leadflow.lead_state WHERE lead_id = %s", x)
                                    for x in leads]}
    assert st[leads[0]]["status"] == "contacted" and st[leads[0]]["first_call_ok"] is True
    assert st[leads[0]]["first_call_at"].isoformat()[:13] == _at(2, 10)[:13]   # giờ gốc, không phải lúc quét
    assert st[leads[1]]["status"] != "contacted" and st[leads[2]]["status"] != "contacted"


async def test_import_state_can_hand_the_lead_to_the_source_holder_with_history_and_no_notice() -> None:
    tid, d, w = await _company()
    boss = w["admin"]
    await _members_rule(boss)
    lid = await _lead(boss, f"a@{d}")
    notes = await _count("SELECT count(*) FROM core.notifications WHERE tenant_id = %s", tid)
    out = await ok(boss, "leadflow.import_state", lead_id=str(lid), origin="he_cu", status="waiting", assigned_at=_at(1), holder_email=f"b@{d}",
                   take_holder=True)
    assert out["holder_taken"] is True and str(out["holder_id"]) == str(w["b"].user_id) and out["holder_matches"] is True
    assert await owner_val("SELECT owner_id FROM crm.leads WHERE id = %s", (lid,)) == w["b"].user_id
    assert await _count("SELECT count(*) FROM crm.stage_events WHERE lead_id = %s AND kind = 'assigned' AND reason LIKE 'Nạp từ hệ cũ%%'", lid) == 1
    assert await _count("SELECT count(*) FROM core.notifications WHERE tenant_id = %s", tid) == notes
    # người giữ ở hệ nguồn không còn làm được lead ⇒ giữ người phụ trách bên Khách, báo lệch
    await ok(boss, "admin.update_member", email=f"a@{d}", active=False)
    other = await _lead(boss, f"b@{d}")
    out = await ok(boss, "leadflow.import_state", lead_id=str(other), origin="he_cu", status="waiting", assigned_at=_at(1), holder_email=f"a@{d}",
                   take_holder=True)
    assert out["holder_taken"] is False and str(out["holder_id"]) == str(w["b"].user_id) and out["holder_matches"] is False


async def test_import_row_content_is_purged_after_the_company_retention_and_counts_stay() -> None:
    tid, _, w = await _company()
    old = {"origin": "he_cu", "run_ref": "9", "run_kind": "lead", "started_at": _at(200), "line": 1, "status": "failed", "error": "sai số",
           "data": '{"phone": "0100000002"}'}
    await ok(w["admin"], "core.import_past_run", **old)
    await ok(w["admin"], "core.import_past_run", **{**old, "run_ref": "10", "started_at": _at(10)})
    page = await q(w["admin"], "core.import_retention")
    assert page["rows"][0]["keep"] == "90 ngày" and page["rows"][0]["due_runs"] == 1
    audit = await _count("SELECT count(*) FROM core.audit_log WHERE tenant_id = %s", tid)
    out = await ok(system_principal(tid), "core.purge_import_rows")
    assert out == {"runs": 1, "rows": 1}
    got = await _row("""SELECT count(*) FILTER (WHERE rr.data = '{}'::jsonb) AS emptied, count(*) FILTER (WHERE rr.error = 'sai số') AS errors,
                               sum(r.failed) AS failed, count(r.rows_purged_at) AS purged
                        FROM core.import_runs r JOIN core.import_run_rows rr ON rr.run_id = r.id WHERE r.tenant_id = %s""", tid)
    assert (got["emptied"], got["errors"], got["failed"], got["purged"]) == (1, 2, 2, 1)
    # nhật ký thao tác không bị xoá (chỉ thêm dòng của chính lượt quét, nếu có)
    assert await _count("SELECT count(*) FROM core.audit_log WHERE tenant_id = %s", tid) >= audit
    assert (await ok(system_principal(tid), "core.purge_import_rows")) == {"runs": 0, "rows": 0}
    # rút ngắn ⇒ lượt sau xoá tiếp; giữ mãi ⇒ không xoá
    await ok(w["admin"], "core.set_import_retention", keep_days=5)
    assert (await ok(system_principal(tid), "core.purge_import_rows"))["runs"] == 1
    await ok(w["admin"], "core.set_import_retention", keep_forever=True)
    assert (await q(w["admin"], "core.import_retention"))["rows"][0]["keep"] == "Giữ mãi"
    assert (await executor.run(w["a"], "core.set_import_retention", {"keep_days": 1})).status == "denied"
    assert (await executor.run(w["admin"].as_agent(), "core.set_import_retention", {"keep_days": 1})).status != "ok"
