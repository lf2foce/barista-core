"""ADR 0246 — lý do mất khách là danh mục của công ty (chọn, không gõ; giữ qua lần mở lại; đếm được trong tập dữ liệu lead) và lead nằm im quá ngưỡng của
chặng (một công thức: danh sách lead, thẻ khách, truy vấn ``crm.idle_leads``, Báo cáo chạy song song).

Mỗi test dựng MỘT công ty riêng (số tuyệt đối đúng, không dính dữ liệu của test khác)."""

from datetime import timedelta
from typing import Any
from uuid import UUID

from barista.db import db
from barista.kernel import executor
from barista.kernel.clock import today
from barista.kernel.principal import Principal
from tests.conftest import fresh_phone, ok, owner_val, q
from tests.test_findings import PACKS, _cards, _kpi, _rows
from tests.test_leadflow_plus import _company, _exec


async def _lead(sale: Principal, name: str) -> UUID:
    out = await ok(sale, "crm.create_lead", name=name, phone=fresh_phone())
    return UUID(str(out["lead_id"]))


async def _lost(lead: UUID) -> dict[str, Any]:
    async with db.owner.connection() as conn:
        cur = await conn.execute("SELECT stage, lost_reason, lost_reason_key FROM crm.leads WHERE id = %s", (lead,))
        row = await cur.fetchone()
    assert row is not None
    return dict(row)


async def _age(lead: UUID, days: int, *, activity_days: int | None = None) -> None:
    """Lùi đồng hồ của MỘT lead: lead về + mọi mốc chặng ``days`` ngày trước; chăm sóc (nếu có) ``activity_days`` ngày trước."""
    await _exec("UPDATE crm.leads SET created_at = now() - make_interval(days => %s), captured_at = NULL WHERE id = %s", (days, lead))
    await _exec("UPDATE crm.stage_events SET at = now() - make_interval(days => %s) WHERE lead_id = %s", (days, lead))
    if activity_days is not None:
        await _exec("UPDATE crm.leads SET last_activity_at = now() - make_interval(days => %s) WHERE id = %s", (activity_days, lead))
        await _exec("UPDATE crm.activities SET at = now() - make_interval(days => %s) WHERE lead_id = %s", (activity_days, lead))


def _row(page: dict[str, Any], lead: UUID) -> dict[str, Any]:
    return next(r for r in page["rows"] if str(r["id"]) == str(lead))


# ── Lý do mất khách ──

async def test_lost_reasons_ship_as_company_data_that_only_company_wide_managers_edit() -> None:
    _tid, _, pp = await _company()
    options = {o["label"]: o["value"] for o in await q(pp["a"], "crm.lost_reason_options")}
    assert {"Đã mua chỗ khác", "Không liên lạc được", "Chỉ tham khảo", "Lý do khác"} <= options.keys()     # bộ mặc định đi kèm gói
    denied = await executor.run(pp["truong"], "crm.save_lost_reason", {"label": "Gia đình chưa thống nhất"})
    assert denied.status == "denied"                                                                     # trưởng nhóm: phạm vi đội, không sửa cấu hình cả công ty
    added = await ok(pp["admin"], "crm.save_lost_reason", label="Gia đình chưa thống nhất", sort=25)
    assert added["added"] and added["key"] == "gia_dinh_chua_thong_nhat"                                # mã do máy đặt từ tên
    again = await ok(pp["admin"], "crm.save_lost_reason", label="gia dinh chua thong nhat", sort=26, active=False)
    assert again["key"] == added["key"] and not again["added"]                                           # trùng tên ⇒ sửa lý do đó
    labels = [o["label"] for o in await q(pp["a"], "crm.lost_reason_options")]
    assert "Gia đình chưa thống nhất" not in labels                                                      # đã tắt ⇒ không chọn được
    table = await q(pp["admin"], "crm.lost_reasons")
    assert any(r["key"] == added["key"] and r["state"] == "off" for r in table["rows"])


async def test_marking_a_lead_lost_picks_a_reason_from_the_list_and_keeps_it_in_history() -> None:
    _tid, _, pp = await _company()
    sale = pp["a"]
    lead = await _lead(sale, "Khách chọn lý do")
    r = await executor.run(sale, "crm.set_stage", {"lead_id": str(lead), "stage": "lost"})
    assert r.status == "invalid"                                                                         # mất khách cần lý do
    r = await executor.run(sale, "crm.set_stage", {"lead_id": str(lead), "stage": "lost", "lost_reason_key": "other"})
    assert r.status == "invalid"                                                                         # "Lý do khác" cần ghi chú
    r = await executor.run(sale, "crm.set_stage", {"lead_id": str(lead), "stage": "lost", "lost_reason_key": "khong_co"})
    assert r.status == "not_found"
    got = await ok(sale, "crm.set_stage", lead_id=str(lead), stage="lost", lost_reason_key="price", lost_reason="Chờ đợt giảm giá")
    assert got["lost_reason_key"] == "price"
    assert await _lost(lead) == {"stage": "lost", "lost_reason": "Chờ đợt giảm giá", "lost_reason_key": "price"}
    ev = await owner_val("SELECT jsonb_build_array(reason, data) FROM crm.stage_events WHERE lead_id = %s AND to_stage = 'lost'", (lead,))
    assert ev[0] == "Chờ đợt giảm giá" and ev[1]["lost_reason_key"] == "price" and ev[1]["lost_reason_label"] == "Giá / tài chính chưa phù hợp"
    card = await q(sale, "crm.journey", lead_id=str(lead))
    steps = next(b for b in card["blocks"] if b["type"] == "steps")
    assert any(s["state"] == "stopped" and s["note"] == "Giá / tài chính chưa phù hợp — Chờ đợt giảm giá" for s in steps["steps"])

    # khách quay lại theo: lý do của lần mất gần nhất ở lại trên lead, lịch sử giữ nguyên
    await ok(sale, "crm.set_stage", lead_id=str(lead), stage="prospect")
    assert await _lost(lead) == {"stage": "prospect", "lost_reason": "Chờ đợt giảm giá", "lost_reason_key": "price"}
    assert await owner_val("SELECT count(*) FROM crm.stage_events WHERE lead_id = %s AND data->>'lost_reason_key' = 'price'", (lead,)) == 1


async def test_typed_reasons_are_matched_to_the_list_and_the_rest_go_to_the_note_reason() -> None:
    _tid, _, pp = await _company()
    sale = pp["a"]
    cases = (("Mua chỗ khác", "bought_elsewhere"), ("Khách đã mua chỗ khác rồi", "bought_elsewhere"), ("Chỉ tham khảo giá", "just_browsing"), ("không còn nhu cầu (hệ cũ)", "no_longer_needed"),
             ("Khác", "other"), ("Khách bận việc gia đình", "other"), ("Đã mua chỗ khác", "bought_elsewhere"))
    for i, (text, key) in enumerate(cases):
        lead = await _lead(sale, f"Khách gõ lý do {i}")
        await ok(sale, "crm.set_stage", lead_id=str(lead), stage="lost", lost_reason=text)
        assert await _lost(lead) == {"stage": "lost", "lost_reason": text, "lost_reason_key": key}, text   # chữ gốc giữ làm ghi chú
    lead = await _lead(sale, "Khách chọn theo tên")
    await ok(sale, "crm.set_stage", lead_id=str(lead), stage="lost", lost_reason_key="Không liên lạc được")   # trợ lý gửi đúng TÊN lý do
    assert (await _lost(lead))["lost_reason_key"] == "unreachable"


async def test_a_company_with_no_active_reason_still_takes_a_typed_reason() -> None:
    tid, _, pp = await _company()
    await _exec("UPDATE crm.lost_reasons SET active = false WHERE tenant_id = %s", (tid,))
    lead = await _lead(pp["a"], "Khách không danh mục")
    r = await executor.run(pp["a"], "crm.set_stage", {"lead_id": str(lead), "stage": "lost"})
    assert r.status == "invalid"
    await ok(pp["a"], "crm.set_stage", lead_id=str(lead), stage="lost", lost_reason="Hết ngân sách")
    assert await _lost(lead) == {"stage": "lost", "lost_reason": "Hết ngân sách", "lost_reason_key": None}


async def test_the_leads_dataset_answers_why_we_lose_leads() -> None:
    tid, _, pp = await _company()
    sale, boss = pp["a"], pp["admin"]
    for i, key in enumerate(("price", "price", "bought_elsewhere")):
        await ok(sale, "crm.set_stage", lead_id=str(await _lead(sale, f"Khách mất {i}")), stage="lost", lost_reason_key=key)
    old = await _lead(sale, "Khách mất trước danh mục")   # lead mất trước khi có danh mục: chỉ có chữ người đã gõ
    await _exec("UPDATE crm.leads SET stage = 'lost', lost_reason = 'Ghi tay kiểu cũ', lost_reason_key = NULL WHERE id = %s", (old,))
    revived = await _lead(sale, "Khách quay lại")
    await ok(sale, "crm.set_stage", lead_id=str(revived), stage="lost", lost_reason_key="unreachable")
    await ok(sale, "crm.set_stage", lead_id=str(revived), stage="lead")
    page = await q(boss, "crm.ds_leads", measures=["lost"], by=["lost_reason"], where={"stage": ["lost"]})
    by = {r["lost_reason"]: r["lost"] for r in page["rows"]}
    assert by == {"Giá / tài chính chưa phù hợp": 2, "Đã mua chỗ khác": 1, "Ghi tay kiểu cũ": 1}   # lead đã mở lại không còn là "đang mất"
    assert await owner_val("SELECT count(*) FROM crm.leads WHERE tenant_id = %s AND stage = 'lost'", (tid,)) == 4


# ── Lead nằm im ──

async def test_stage_idle_threshold_is_company_data_kept_unless_changed() -> None:
    tid, _, pp = await _company()
    await ok(pp["admin"], "crm.save_stage", key="new", label="Mới", phase="lead", sort=10, idle_days=3)
    await ok(pp["admin"], "crm.save_stage", key="new", label="Mới", phase="lead", sort=10)          # không nêu ngưỡng ⇒ giữ nguyên
    assert await owner_val("SELECT idle_days FROM crm.stages WHERE tenant_id = %s AND key = 'new'", (tid,)) == 3
    await ok(pp["admin"], "crm.save_stage", key="new", label="Mới", phase="lead", sort=10, idle_days=0)   # 0 = bỏ ngưỡng
    assert await owner_val("SELECT idle_days FROM crm.stages WHERE tenant_id = %s AND key = 'new'", (tid,)) is None
    stages = await q(pp["admin"], "crm.stages")
    assert "idle_days" in {c["key"] for c in stages["columns"]}


async def test_a_lead_sitting_idle_past_its_stage_threshold_is_flagged_for_owner_and_team_lead() -> None:
    _tid, _, pp = await _company()
    await ok(pp["admin"], "crm.save_stage", key="new", label="Mới", phase="lead", sort=10, idle_days=7)
    sale = pp["a"]
    idle = await _lead(sale, "Khách nằm im")
    fresh = await _lead(sale, "Khách mới về")
    await ok(sale, "crm.log_activity", lead_id=str(idle), kind="call", outcome="connected")
    await _age(idle, 12, activity_days=9)                       # vào chặng 12 ngày trước, chăm sóc lần cuối 9 ngày trước ⇒ nằm im 9 ngày > 7

    mine = await q(sale, "crm.leads")
    assert _row(mine, idle)["idle_flag"] == "Nằm im 9 ngày" and _row(mine, idle)["idle_days"] == 9
    assert _row(mine, fresh)["idle_flag"] == "" and _row(mine, fresh)["idle_days"] is None
    assert next(k["value"] for k in mine["kpis"] if k["label"] == "Nằm im quá lâu") == 1
    for viewer in (sale, pp["truong"]):                         # người phụ trách và trưởng nhóm của đội
        listed = await q(viewer, "crm.idle_leads")
        assert [r["id"] for r in listed["rows"]] == [str(idle)] and listed["rows"][0]["days"] == 9
    assert (await q(pp["c"], "crm.idle_leads"))["rows"] == []   # sale đội khác không thấy

    card = await q(sale, "crm.journey", lead_id=str(idle))
    kpis = next(b for b in card["blocks"] if b["type"] == "kpis")["items"]
    assert next(k for k in kpis if k["label"] == "Nằm im")["value"] == "9 ngày"
    assert any(b["type"] == "callout" and b["title"] == "Đang kẹt: Nằm im 9 ngày" for b in card["blocks"])

    await ok(sale, "crm.log_activity", lead_id=str(idle), kind="zalo", note="Đã nhắn lại")   # chăm sóc ⇒ hết nằm im
    assert _row(await q(sale, "crm.leads"), idle)["idle_flag"] == ""
    await ok(sale, "crm.log_activity", lead_id=str(fresh), kind="note", note="Ghi chú nội bộ")   # ghi chú nội bộ không phải chuyển động
    await _age(fresh, 8)
    assert _row(await q(sale, "crm.leads"), fresh)["idle_flag"] == "Nằm im 8 ngày"


async def test_idle_leads_finding_is_unmeasured_without_thresholds_and_its_drill_matches() -> None:
    _tid, _, pp = await _company(PACKS)
    admin, sale = pp["admin"], pp["a"]
    lead = await _lead(sale, "Khách nằm im báo cáo")
    window = {"date_from": (today() - timedelta(days=30)).isoformat(), "date_to": today().isoformat()}
    card = await q(admin, "findings.overview", **window)
    miss = next(b for b in card["blocks"] if b["type"] == "list" and b["title"].startswith("Chưa đo được"))["items"]
    assert any(i["title"] == "Lead nằm im quá lâu ở một chặng" for i in miss)                     # chưa đặt ngưỡng ⇒ chưa đo được, không phải 0

    await ok(admin, "crm.save_stage", key="new", label="Mới", phase="lead", sort=10, idle_days=5)
    await _age(lead, 6)
    await _lead(sale, "Khách vừa về")
    got = _cards(await q(admin, "findings.overview", **window))["Lead nằm im quá lâu ở một chặng"]
    assert _kpi(got, "Lead nằm im quá ngưỡng") == 1 and _kpi(got, "Lead đang theo ở chặng có ngưỡng") == 2
    drill = next(k["drill"] for k in got["kpis"] if k["label"] == "Lead nằm im quá ngưỡng")
    assert sum(r["n"] for r in await _rows(admin, drill)) == 1
    rows = await q(admin, "crm.finding_rows", key="idle_leads", **window)
    assert [r["name"] for r in rows["rows"]] == ["Khách nằm im báo cáo"]
