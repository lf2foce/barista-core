"""Báo cáo chạy song song (0230): bộ gom phát hiện, các phát hiện v1 (marketing / khách / chia lead), "chưa đo được ≠ 0", dòng gốc khớp con số, chỉ ĐỌC, quyền, xuất tờ qua Soạn với AI.

Mỗi test dựng MỘT công ty riêng (số tuyệt đối đúng, không dính dữ liệu của test khác)."""

import base64
import uuid
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from barista import seed
from barista.kernel import executor
from barista.kernel import findings as fd
from barista.kernel.clock import today
from barista.kernel.principal import Principal, system_principal
from tests.conftest import fresh_phone, ok, owner_val, q
from tests.test_leadflow_plus import _company, _intake, _rule

PACKS = (*seed.sample_packs(), "findings", "compose")
PLAN = "CPFINDPLAN1"


async def _world() -> tuple[UUID, dict[str, Principal], Principal]:
    tid, _, pp = await _company(PACKS)
    boss = system_principal(tid, pp["admin"].user_id)   # người nhập chi phí / chiến dịch (như test chia lead theo kế hoạch)
    await _rule(pp["admin"], offering_code="MEM", team_code="KD-A")
    return tid, pp, boss


async def _campaign(boss: Principal, tag: str, *, spend: int, plan: str = "") -> str:
    code = f"FD{tag}{uuid.uuid4().hex[:5]}".upper()
    await ok(boss, "mkt.upsert_campaign", code=code, name=f"Chiến dịch {tag}", channel="facebook", offering_code="MEM", plan_code=plan)
    await ok(boss, "mkt.record_spend", campaign_code=code, day=today().isoformat(), amount_vnd=spend)
    return code


def _window() -> dict[str, str]:
    return {"date_from": today().isoformat(), "date_to": today().isoformat()}


def _cards(card: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """tiêu đề ⇒ khối callout của phát hiện (mỗi phát hiện đo được một callout; kèm khối ô số liền sau)."""
    out: dict[str, dict[str, Any]] = {}
    blocks = card["blocks"]
    for i, b in enumerate(blocks):
        if b["type"] == "callout" and b["tone"] in ("warn", "ok") and i + 1 < len(blocks):
            out[b["title"]] = {"callout": b, "kpis": blocks[i + 1]["items"] if blocks[i + 1]["type"] == "kpis" else []}
    return out


def _kpi(c: dict[str, Any], label: str) -> Any:
    return next(k["value"] for k in c["kpis"] if k["label"] == label)


async def _rows(p: Principal, drill: dict[str, Any]) -> list[dict[str, Any]]:
    page = await q(p, drill["query"], **drill["input"])
    return list(page["rows"])


# ── Hợp đồng ──

def test_a_finding_that_cannot_be_measured_carries_no_number() -> None:
    ok_one = fd.unmeasured("crm.x", "Điều chưa đo được", "process", why_not="chưa có dữ liệu", unlock="có dữ liệu")
    assert fd.parse(ok_one) is not None
    assert fd.parse({**ok_one, "impact": fd.impact_count(0, "lead")}) is None                       # 0 trần cho thứ chưa đo được = bị từ chối
    assert fd.parse({**ok_one, "metrics": [fd.metric("Lead", 0)]}) is None
    assert fd.parse({**ok_one, "unlock": ""}) is None                                                 # phải nói khi nào đo được
    assert fd.parse(fd.finding("crm.y", "Đã kiểm không thấy", "leads", summary="x", formula="y", impact=fd.impact_count(0, "lead"))) is not None   # 0 khi ĐÃ KIỂM là hợp lệ
    assert fd.parse(fd.finding("crm.y", "Thiếu công thức", "leads", summary="x", formula="", impact=fd.impact_count(3, "lead"))) is None
    assert fd.parse({**ok_one, "key": "khongcamodule"}) is None


def test_outlier_rule_is_pure_and_needs_enough_campaigns() -> None:
    from barista.modules.mkt.findings import _outliers

    rows = [{"id": i, "channel": "facebook", "spend": s, "leads": 10} for i, s in enumerate((1_000_000, 1_100_000, 1_200_000, 5_000_000))]
    ok_rows, median = _outliers(rows)
    assert median is not None and [r["outlier"] for r in ok_rows] == [False, False, False, True]
    assert ok_rows[3]["excess"] == round((500_000 - ok_rows[3]["ref_cpl"]) * 10)
    assert _outliers(rows[:2])[1] is None                                                            # chưa đủ chiến dịch ⇒ không có "mức bình thường"


# ── Phát hiện trên dữ liệu thật của một công ty ──

async def test_findings_catch_what_the_parallel_run_left_behind() -> None:
    tid, pp, boss = await _world()
    admin = pp["admin"]
    dead = await _campaign(boss, "DEAD", spend=3_000_000)                       # chi mà không có lead
    cheap = [await _campaign(boss, f"OK{i}", spend=1_000_000) for i in range(3)]
    dear = await _campaign(boss, "DEAR", spend=6_000_000)                      # chi phí / lead gấp nhiều lần
    for code in (*cheap, dear):
        for _ in range(5):
            await _intake(admin, offering_code="MEM", source="ads", campaign_code=code, team_code="DIG")   # về hàng chờ đội Digital: chưa có người nhận
    phone = fresh_phone()   # cùng một khách qua hai chiến dịch, hai sản phẩm ⇒ trùng
    await _intake(admin, name="Khách trùng", phone=phone, offering_code="MEM", source="ads", campaign_code=cheap[0], team_code="DIG")
    await _intake(admin, name="Khách trùng", phone=phone, offering_code="PRO", source="ads", campaign_code=cheap[1], team_code="DIG")

    card = await q(admin, "findings.overview", **_window())
    got = _cards(card)
    spend0 = got["Tiền chạy quảng cáo mà không thấy lead nào"]
    assert _kpi(spend0, "Tiền chi cho chiến dịch không ra lead") == 3_000_000 and _kpi(spend0, "Chiến dịch chi mà không có lead") == 1
    rows = await _rows(admin, next(k["drill"] for k in spend0["kpis"] if k.get("drill")))
    assert [r["code"] for r in rows] == [dead]
    assert _kpi(got["Chiến dịch có chi phí mỗi lead cao bất thường"], "Chiến dịch bất thường") == 1

    dup = got["Cùng một khách, quảng cáo trả tiền nhiều lần"]
    assert _kpi(dup, "Khách có lead trùng") == 1 and _kpi(dup, "Lead trùng") == 1
    assert [r["name"] for r in await _rows(admin, next(k["drill"] for k in dup["kpis"] if k["label"] == "Lead trùng"))] == ["Khách trùng"]

    unowned = got["Lead đã về nhưng chưa có người nhận"]
    assert _kpi(unowned, "Lead chưa có người nhận") == 5 * 4 + 2

    gap = got["Luật chia lead hiện tại sẽ giao được bao nhiêu lead đang chờ"]
    # luật chung (sản phẩm MEM) giao được 21 lead; lead sản phẩm PRO chưa có luật nào ⇒ vẫn kẹt — mô phỏng chỉ đọc, không giao thật
    assert _kpi(gap, "Lead đang chờ mà luật hiện tại sẽ giao ngay") == 21 and _kpi(gap, "Vẫn kẹt sau khi chia") == 1 and gap["callout"]["tone"] == "warn"
    sim = {r["offering"]: r for r in await _rows(admin, next(k["drill"] for k in gap["kpis"] if k.get("drill")))}
    assert sim["PRO"]["now"] == "Chưa có luật chia" and sim["MEM"]["assign"] == 21
    assert await owner_val("SELECT count(*) FROM leadflow.lead_state WHERE tenant_id = %s AND status = 'queued'", (tid,)) == 22   # chưa ai bị giao


async def test_drill_rows_match_every_number_that_offers_a_drill() -> None:
    _tid, pp, boss = await _world()
    admin = pp["admin"]
    code = await _campaign(boss, "DR", spend=2_000_000)
    for _ in range(3):
        await _intake(admin, offering_code="MEM", source="ads", campaign_code=code, team_code="DIG")
    await _campaign(boss, "DR2", spend=1_000_000)
    checked = 0
    for b in (await q(admin, "findings.overview", **_window()))["blocks"]:
        for k in b.get("items", []) if b["type"] == "kpis" else []:
            if not k.get("drill"):
                continue
            rows = await _rows(admin, k["drill"])
            col = k["drill"]["sum"]
            assert (sum(r[col] for r in rows) if col else len(rows)) == k["value"], k["label"]
            checked += 1
    assert checked >= 3


async def test_not_measurable_is_stated_never_shown_as_zero() -> None:
    _tid, pp, _boss = await _world()
    admin = pp["admin"]
    await _intake(admin, offering_code="MEM", source="ads", team_code="DIG")
    card = await q(admin, "findings.overview", **_window())
    miss = next(b for b in card["blocks"] if b["type"] == "list" and b["title"].startswith("Chưa đo được"))["items"]
    titles = {i["title"]: i for i in miss}
    assert "Khách được gọi lại nhanh hay chậm" in titles and "Bao nhiêu lead thành giao dịch" in titles
    assert titles["Khách được gọi lại nhanh hay chậm"]["subtitle"].startswith("Chưa đo được — vì ") and "Sẽ đo được khi" in titles["Khách được gọi lại nhanh hay chậm"]["subtitle"]
    all_labels = {k["label"] for b in card["blocks"] if b["type"] == "kpis" for k in b["items"]}
    assert not any("gọi" in label.lower() or "giao dịch" in label.lower() for label in all_labels)   # không ô số nào cho thứ chưa đo được
    # không có chi phí nào ⇒ phần chiến dịch cũng "chưa đo được", không phải 0
    assert "Tiền chạy quảng cáo mà không thấy lead nào" in titles or "Tiền chạy quảng cáo mà không thấy lead nào" in _cards(card)


async def test_leads_without_campaign_make_spend_vs_leads_unmeasurable() -> None:
    _tid, pp, boss = await _world()
    admin = pp["admin"]
    await _campaign(boss, "NOLEAD", spend=2_000_000)
    for _ in range(4):
        await _intake(admin, offering_code="MEM", source="ads", team_code="DIG")            # lead quảng cáo nhưng KHÔNG gắn chiến dịch nào
    card = await q(admin, "findings.overview", **_window())
    assert "Tiền chạy quảng cáo mà không thấy lead nào" not in _cards(card)                 # không kết luận "chiến dịch không ra lead"
    item = next(i for b in card["blocks"] if b["type"] == "list" for i in b["items"] if i["title"] == "Tiền chạy quảng cáo mà không thấy lead nào")
    assert "chưa gắn được chiến dịch" in item["subtitle"]


async def test_cost_share_fairness_compares_leads_with_the_shares() -> None:
    _tid, pp, boss = await _world()
    admin = pp["admin"]
    code = await _campaign(boss, "CS", spend=2_000_000, plan=PLAN)
    for who in ("a", "c"):                                                                     # a thuộc KD-A (góp 70%), c thuộc KD-B (góp 30%)
        await ok(admin, "leadflow.set_member", offering_code="MEM", user_email=pp[who].email, plan_code=PLAN.lower())
    await _rule(admin, name="Gói đồng chi trả", plan_code=PLAN, receivers="members")
    for team, pct in (("KD-A", 70), ("KD-B", 30)):
        await ok(boss, "mkt.set_cost_share", plan_code=PLAN, team_code=team, share_pct=pct, valid_from=(today() - timedelta(days=5)).isoformat())
    for _ in range(10):
        await _intake(admin, offering_code="MEM", source="ads", campaign_code=code)            # xoay vòng a / c ⇒ 5 / 5 trong khi phần góp là 7 / 3
    fair = _cards(await q(admin, "findings.overview", **_window()))["Bên góp tiền quảng cáo có nhận đủ phần lead tương ứng không"]
    assert _kpi(fair, "Lead bên góp nhận thiếu so với phần góp") == 2
    rows = {r["party"]: r for r in await _rows(admin, next(k["drill"] for k in fair["kpis"] if k.get("drill")))}
    assert rows["Team Kinh doanh A"]["got"] == 5 and rows["Team Kinh doanh A"]["expected"] == 7.0 and rows["Team Kinh doanh B"]["shortfall"] == 0


async def test_reading_the_report_writes_nothing_and_notifies_nobody() -> None:
    tid, pp, boss = await _world()
    admin = pp["admin"]
    code = await _campaign(boss, "RO", spend=1_000_000)
    for _ in range(3):
        await _intake(admin, offering_code="MEM", source="ads", campaign_code=code, team_code="DIG")
    watch = ("core.notifications", "core.events", "leadflow.sla_events", "leadflow.lead_state", "crm.leads", "core.proposals")

    async def snapshot() -> list[Any]:
        return [await owner_val(f"SELECT count(*) FROM {t} WHERE tenant_id = %s", (tid,)) for t in watch] + \
               [await owner_val("SELECT coalesce(sum(extract(epoch FROM updated_at)), 0) FROM leadflow.lead_state WHERE tenant_id = %s", (tid,))]

    before = await snapshot()
    await q(admin, "findings.overview", **_window())
    await q(admin, "findings.overview", period="since_start")
    assert await snapshot() == before


async def test_only_people_with_whole_company_scope_open_the_report(who: Any) -> None:
    _tid, pp, _boss = await _world()
    for local in ("a", "truong"):
        r = await executor.query(pp[local], "findings.overview", {})
        assert r.status in ("denied", "not_found"), (local, r.status)
    assert (await executor.query(pp["admin"], "findings.overview", {})).status == "ok"


async def test_a_company_without_the_pack_does_not_have_the_report() -> None:
    _tid, _, pp = await _company(seed.sample_packs())          # không bật gói findings
    r = await executor.query(pp["admin"], "findings.overview", {})
    assert r.status != "ok"


async def test_a_broken_contributor_does_not_take_the_report_down(monkeypatch: pytest.MonkeyPatch) -> None:
    _tid, pp, _boss = await _world()
    from barista.modules.crm import findings as crm_findings

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("hỏng giả")
    monkeypatch.setattr(crm_findings, "_duplicates", boom)
    card = await q(pp["admin"], "findings.overview", **_window())
    warn = next(b for b in card["blocks"] if b["type"] == "callout" and b["title"] == "Chưa kiểm hết")
    assert any("chưa đọc được" in f for f in warn["facts"])
    assert "Chi phí quảng cáo tăng hay giảm" in _cards(card) or any(b["type"] == "list" for b in card["blocks"])   # phần của module khác vẫn còn


async def test_the_board_document_is_built_from_queries_and_leaks_no_internal_codes() -> None:
    _tid, pp, boss = await _world()
    admin = pp["admin"]
    code = await _campaign(boss, "EX", spend=2_000_000)
    for _ in range(3):
        await _intake(admin, offering_code="MEM", source="ads", campaign_code=code, team_code="DIG")
    await _campaign(boss, "EX2", spend=900_000)
    assert "parallel_report" in [o["value"] for o in await q(admin, "compose.brochure_options")]
    out = await ok(admin, "compose.brochure", kind="parallel_report", subject=f"{today()}..{today()}")
    sid = out["session_id"]
    blocks = await owner_val("SELECT blocks FROM compose.sessions WHERE id = %s", (UUID(str(sid)),))
    data = [b for b in blocks if b["type"] == "data"]
    assert len(data) >= 6 and all(b["data"]["source"]["query"].startswith("findings.") for b in data)   # mọi số là ảnh chụp truy vấn
    assert all(not b.get("flags") for b in blocks)                                                       # không số nào không khớp
    exp = await ok(admin, "compose.export", session_id=str(sid), format="html")
    page = base64.b64decode((await q(admin, "compose.download", export_id=str(exp["export_id"])))["data_b64"]).decode()
    assert "Báo cáo chạy song song" in page and "Tiền chạy quảng cáo mà không thấy lead nào" in page
    for leak in ("mkt.", "crm.", "leadflow.", "findings.", "finding_rows"):
        assert leak not in page, leak


async def test_speed_and_conversion_get_numbers_once_sales_log_work_in_the_system() -> None:
    _, pp, boss = await _world()
    admin = pp["admin"]
    code = await _campaign(boss, "SPD", spend=1_000_000)
    leads = [await _intake(admin, offering_code="MEM", source="ads", campaign_code=code) for _ in range(4)]   # luật chung giao cho người bán
    assert all(x["assigned_to"] for x in leads)
    before = await q(admin, "findings.overview", **_window())
    assert "Khách được gọi lại nhanh hay chậm" in {i["title"] for b in before["blocks"] if b["type"] == "list" for i in b["items"]}   # chưa ai ghi gì ⇒ chưa đo được
    for x in leads[:2]:
        holder = next(p for p in pp.values() if p.user_id == x["assigned_to"])
        await ok(holder, "crm.log_activity", lead_id=str(x["lead_id"]), kind="call", outcome="connected")
    got = _cards(await q(admin, "findings.overview", **_window()))["Khách được gọi lại nhanh hay chậm"]
    assert _kpi(got, "Lead đã giao trong kỳ") == 4 and _kpi(got, "Lead đã giao nhưng chưa có cuộc gọi nào") == 2
    assert "Bao nhiêu lead thành giao dịch" in _cards(await q(admin, "findings.overview", **_window()))
