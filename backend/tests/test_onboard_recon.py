"""ĐỐI CHIẾU + KÝ + NGÀY CHUYỂN (ADR 0263 mục B, D) trên dữ liệu GIẢ: lệch vượt ngưỡng ⇒ ``lech``; ký qua chuỗi duyệt; ``go_live`` từ chối khi còn việc bắt
buộc chưa xong / chưa có lượt cuối / lượt cuối còn lệch chưa ký, chỉ chủ tài khoản bấm, trợ lý không làm; đạt thì chạy bước ngày chuyển của gói (khoá kỳ)
rồi ghi ngày chuyển — không bấm lần hai."""

from datetime import timedelta
from typing import Any
from uuid import UUID

from barista import seed
from barista.api import onboard as ob
from barista.kernel import executor, principal
from barista.kernel.clock import today
from barista.modules import onboard as mod
from tests.conftest import approved, ok, owner_val, q
from tests.test_onboard_load import company, people_entity

ROWS = [{"source_id": "1", "name": "Người Giả Một", "paid": 1_000_000, "month": "2026-09"},
        {"source_id": "2", "name": "Người Giả Hai", "paid": 2_000_000, "month": "2026-10"}]


def test_compare_uses_the_tolerance_of_the_source_value() -> None:
    got = ob.compare("money", "Tiền", {"2026-09": 1000.0, "2026-10": 2000.0}, {"2026-09": 1004.0, "2026-10": 2000.0, "2026-11": 1.0}, 0.5)
    assert [(x["period"], x["status"]) for x in got] == [("2026-09", "khop"), ("2026-10", "khop"), ("2026-11", "lech")]
    assert ob.compare("n", "Đếm", {"": 0.0}, {"": 0.0}, 0)[0]["status"] == "khop"
    assert ob.compare("n", "Đếm", {"": 10.0}, {"": 9.0}, 0)[0]["status"] == "lech"


def _money(target: dict[str, float]) -> ob.Metric:
    async def tgt(reader: ob.Reader) -> dict[str, float]:
        return target

    def src() -> dict[str, float]:
        out: dict[str, float] = {}
        for r in ROWS:
            out[r["month"]] = out.get(r["month"], 0) + r["paid"]
        return out

    return ob.Metric("money.paid", "Tiền đã thu", src, tgt, by="month", tolerance_pct=0.5)


async def _run(tid: UUID, domain: str, kind: str, target: dict[str, float]) -> ob.Report:
    conv = ob.Converter("hecu", (people_entity(ROWS),), metrics=(_money(target),), mapping_version="v1")
    return await ob.load(tid, conv, by_email=f"admin@{domain}", kind=kind)   # type: ignore[arg-type]


async def test_a_mismatch_over_the_threshold_is_lech_and_signing_goes_through_the_chain() -> None:
    tid, domain = await company()
    r = await _run(tid, domain, "mock", {"2026-09": 1_000_000, "2026-10": 1_900_000})
    assert [(x["period"], x["status"]) for x in r.recon if x["metric"] == "money.paid"] == [("2026-09", "khop"), ("2026-10", "lech")]
    admin, gd = await seed.who(tid, "admin", domain), await seed.who(tid, "gd", domain)
    page = await q(admin, "onboard.run", run_id=str(r.run_id))
    assert any(b.get("type") == "callout" and "chưa ký" in b["title"] for b in page["blocks"])
    # admin xin ký ⇒ phiếu chờ chủ tài khoản (giám đốc) duyệt; trợ lý không ký thay
    pending = await executor.run(admin, "onboard.sign", {"run_id": str(r.run_id), "note": "Chấp nhận lệch tháng 10"})
    assert pending.status == "pending_approval"
    assert (await executor.run(admin.as_agent(), "onboard.sign", {"run_id": str(r.run_id)})).status == "denied"
    aid = UUID(str(pending.approval_id))
    approver = await owner_val("SELECT approver_ids[1] FROM core.approvals WHERE id = %s", (aid,))
    assert approver == gd.user_id
    await approved(admin, "onboard.sign", run_id=str(r.run_id), note="Chấp nhận lệch tháng 10")
    row = await owner_val("SELECT signed_by::text || '|' || array_to_string(approved_by, ',') || '|' || mismatches FROM onboard.signoffs WHERE run_id = %s",
                          (r.run_id,))
    assert row == f"{admin.user_id}|{gd.user_id}|1"
    runs = await q(admin, "onboard.runs")
    assert [x["signed"] for x in runs["rows"]] == ["signed"]


async def _tick_required(tid: UUID, domain: str) -> None:
    admin = await seed.who(tid, "admin", domain)
    page = await q(admin, "onboard.checklist")
    for s in page["rows"]:
        if s["required"] == "yes" and not s["auto"]:
            await ok(admin, "onboard.mark_step", key=s["id"], status="done", evidence=f"Biên bản giả cho {s['id']}")


async def test_go_live_refuses_until_steps_runs_and_signatures_are_done_then_runs_cutover_steps_once() -> None:
    tid, domain = await company(("directory", "finance"))
    admin, gd = await seed.who(tid, "admin", domain), await seed.who(tid, "gd", domain)
    cut = today().isoformat()
    # không phải chủ tài khoản ⇒ không bấm được, kể cả khi đủ điều kiện
    r = await executor.run(admin, "onboard.go_live", {"cut_date": cut})
    assert r.status == "denied"
    r = await executor.run(gd, "onboard.go_live", {"cut_date": cut})
    assert r.status == "invalid" and "Việc bắt buộc chưa xong" in (r.error or "") and "lượt chuyển cuối" in (r.error or "")
    keys = [s["id"] for s in (await q(admin, "onboard.checklist"))["rows"]]
    assert "finance.opening_balances" in keys and "base.go_no_go" in keys and not any(k.startswith("crm.") for k in keys)   # việc của gói chưa bật không hiện
    r = await executor.run(admin, "onboard.mark_step", {"key": "base.fit_gap", "status": "done", "evidence": ""})
    assert r.status == "invalid"   # thiếu bằng chứng
    r = await executor.run(admin, "onboard.mark_step", {"key": "base.go_no_go", "status": "skipped"})
    assert r.status == "invalid"   # việc bắt buộc không bỏ qua
    await _tick_required(tid, domain)
    matching: dict[str, float] = {"2026-09": 1_000_000, "2026-10": 2_000_000}
    await _run(tid, domain, "mock", matching)
    r = await executor.run(gd, "onboard.go_live", {"cut_date": cut})
    assert r.status == "invalid" and "lượt chuyển thử" in (r.error or "")   # chặng 3: cần đủ lượt thử đạt
    await _run(tid, domain, "mock", matching)
    final = await _run(tid, domain, "final", {"2026-09": 1_000_000, "2026-10": 1_000_000})
    r = await executor.run(gd, "onboard.go_live", {"cut_date": cut})
    assert r.status == "invalid" and "chưa ký" in (r.error or "")
    r = await executor.run(admin, "onboard.sign", {"run_id": str(final.run_id)})
    assert r.status == "invalid" and "lý do" in (r.error or "")   # còn lệch ⇒ ký phải ghi lý do chấp nhận
    await approved(admin, "onboard.sign", run_id=str(final.run_id), note="Chấp nhận lệch tháng 10 (dữ liệu giả)")
    assert await _blockers(gd) == []
    r = await executor.run(gd, "onboard.go_live", {"cut_date": cut, "note": "Theo biên bản go / no-go giả"})
    assert r.status == "ok", r.error   # chủ tài khoản duy nhất ⇒ chạy ngay (nhật ký ghi tự duyệt)
    steps = {s["key"]: s["status"] for s in r.output["steps"]}
    assert steps == {"acct.lock_before_cut": "done"}
    through = await owner_val("""SELECT max(l.locked_through) FROM ledger.period_locks l JOIN core.legal_entities e ON e.id = l.entity_id
                                 WHERE e.tenant_id = %s""", (tid,))
    assert through == today() - timedelta(days=1)
    assert await owner_val("SELECT cut_date FROM onboard.live WHERE tenant_id = %s", (tid,)) == today()
    again = await executor.run(gd, "onboard.go_live", {"cut_date": cut})
    assert again.status == "conflict"
    page = await q(admin, "onboard.checklist")
    assert page["header"][0]["title"].startswith("Đã chuyển sang Barista")


async def test_cutover_steps_are_declared_by_modules_and_rerun_safely() -> None:
    from barista.kernel import registry

    c = registry.CUTOVERS["acct.lock_before_cut"]
    op = registry.OPS[c.command]
    assert op.internal and op.agent == "deny" and op.callers == ("onboard",)
    assert [x.key for x in mod.cutovers_for(frozenset({"base"}))] == []   # gói Tài chính tắt ⇒ không bước khoá kỳ
    tid, _ = await company(("directory", "finance"))
    sysp = principal.system_principal(tid)
    first = await executor.run(sysp, c.command, {"cut_date": today().isoformat()})
    again = await executor.run(sysp, c.command, {"cut_date": today().isoformat()})
    assert first.status == "ok" and not first.output.get("skipped") and again.output.get("skipped")


async def _blockers(who: Any) -> list[str]:
    """Hàm ``blockers`` (một nơi tính cho màn và lệnh) đọc bằng quyền người xem."""
    from barista.db import db

    async with db.tenant_tx(who, rollback=True) as conn:
        return await mod.blockers(executor.core_ctx(conn, who, module="onboard"))


async def test_exceptions_alone_block_go_live_until_signed_with_a_reason_and_every_source_needs_its_own_final() -> None:
    """Số khớp nhưng còn dòng ngoại lệ ⇒ lượt CHƯA đạt; ký phải ghi lý do. Hai nguồn dữ liệu ⇒ mỗi nguồn cần lượt cuối đạt của chính nó."""
    tid, domain = await company()
    admin, gd = await seed.who(tid, "admin", domain), await seed.who(tid, "gd", domain)
    await _tick_required(tid, domain)
    rows = [{"source_id": "1", "name": "Người Giả Một"}]
    conv = ob.Converter("hecu", (people_entity(rows),))
    for _ in range(2):
        await ob.load(tid, conv, by_email=f"admin@{domain}", kind="mock")   # hai lượt thử sạch
    rows.append({"source_id": "2", "name": "  "})   # lượt cuối: dòng 2 thành ngoại lệ "dữ liệu nguồn sai" — số đếm vẫn khớp phần đã nạp
    final = await ob.load(tid, conv, by_email=f"admin@{domain}", kind="final")
    assert final.exceptions == 1
    blockers = await _blockers(gd)
    assert any("ngoại lệ chưa ký" in b for b in blockers), blockers
    r = await executor.run(admin, "onboard.sign", {"run_id": str(final.run_id), "note": "  "})
    assert r.status == "invalid"
    await approved(admin, "onboard.sign", run_id=str(final.run_id), note="Dòng 2 không có tên ở hệ cũ — bỏ (dữ liệu giả)")
    assert await owner_val("SELECT exceptions FROM onboard.signoffs WHERE run_id = %s", (final.run_id,)) == 1
    assert await _blockers(gd) == []
    # nguồn thứ hai mới chạy thử ⇒ ngày chuyển lại bị chặn vì nguồn đó chưa có lượt cuối
    other = ob.Converter("hecu_ketoan", (people_entity([{"source_id": "k1", "name": "Người Giả Kế Toán"}]),))
    await ob.load(tid, other, by_email=f"admin@{domain}")
    assert any("hecu_ketoan" in b and "lượt chuyển cuối" in b for b in await _blockers(gd))
