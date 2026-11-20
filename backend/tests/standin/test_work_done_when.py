"""VIỆC CÓ BỘ KIỂM ("xong khi" — 0117) qua gói thế vai: bản LÕI (ADR 0190) của ``barista-packs:real_estate/tests/test_work_done_when.py`` — cùng từng bất
biến, đối tượng là đơn của gói ``orderdesk`` thay cho giao dịch: việc đóng bằng bằng chứng chứ không bằng một cú bấm, DB chặn mọi đường giả, việc module
sinh mang bộ kiểm của chính module, quét không bị đói, mất căn cứ ⇒ mở lại, ngoại lệ / huỷ qua duyệt, gói tắt ⇒ chưa kiểm được ≠ chưa đạt."""

import asyncio
import re
import uuid
from datetime import timedelta
from typing import Any

import psycopg
import pytest

from barista.db import db
from barista.kernel import events, executor, packs
from barista.kernel.clock import today
from barista.kernel.errors import Denied
from barista.kernel.principal import Principal, system_principal
from tests.conftest import Who, edge, ok, owner_val, q
from tests.standin import helpers
from tests.test_work_done_when import _blocks

# ── 3. done_when: xong theo bằng chứng ──

PAID = {"done_check": "orderdesk.stage_paid", "done_args": "seq=1"}
MONEY = re.compile(r"\d đ")


async def _deal(who: Who) -> tuple[str, str]:
    """Đơn mới của sale3 (đội KD-B của truongb) — đối tượng của gói thế vai mà việc trỏ tới."""
    o = await helpers.order(who)
    return o["code"], o["order_id"]


async def _checked_task(who: Who, deal_id: str, title: str = "Thu đủ cọc đợt 1") -> str:
    truongb, sale3 = await who("truongb"), await who("sale3")
    out = await ok(truongb, "flow.create_task", title=title, assignee_email=sale3.email, subject_kind="orderdesk.order", subject_id=deal_id, **PAID)
    return str(out["task_id"])


async def _task(tid: str) -> dict[str, Any]:
    async with db.owner.connection() as conn:
        cur = await conn.execute("SELECT * FROM flow.tasks WHERE id = %s", (tid,))
        row = await cur.fetchone()
        assert row is not None
        return row


async def _n(sql: Any, params: Any = None) -> int:
    return int(await owner_val(sql, params) or 0)


async def _as_system(tenant: Any, sql: Any, params: Any) -> None:
    """Ghi cột kiểm bằng PHIÊN HỆ THỐNG: trigger 033 chỉ cho hệ thống ghi; kết nối owner không claims giờ là "không ai" (0146) nên bị chặn đúng."""
    async with db.tenant_tx(system_principal(tenant)) as conn:
        await conn.execute(sql, params)


async def _sweep(tenant: Any) -> dict[str, int]:
    await _as_system(tenant, "UPDATE flow.tasks SET checked_at = now() - interval '2 hours' WHERE done_check IS NOT NULL", ())
    r = await executor.run(system_principal(tenant), "flow.check_tasks", {})
    assert r.status == "ok", r.error
    return r.output


async def test_a_checked_task_closes_only_by_evidence_never_by_a_click(who: Who) -> None:
    admin, truongb, sale3 = await who("admin"), await who("truongb"), await who("sale3")
    code, deal = await _deal(who)
    for bad in ({"done_check": "orderdesk.stage_paid"},                                                    # bộ kiểm cần đối tượng
                {"subject_kind": "orderdesk.order", "subject_id": deal, "done_check": "books.filings_done"},       # sai loại đối tượng
                {"subject_kind": "orderdesk.order", "subject_id": deal, "done_check": "khong.co"},
                {"subject_kind": "orderdesk.order", "subject_id": deal, "done_check": "orderdesk.stage_paid", "done_args": "seq"},
                {"subject_kind": "orderdesk.order", "subject_id": deal, "done_check": "orderdesk.stage_paid", "done_args": "seq=abc"},   # sai kiểu
                {"subject_kind": "orderdesk.order", "subject_id": deal, "done_check": "orderdesk.stage_paid", "done_args": "dot=1"},     # tham số lạ
                {"subject_kind": "orderdesk.order", "subject_id": deal, "done_check": "orderdesk.signed", "done_args": "seq=1"}):   # không nhận tham số
        r = await executor.run(truongb, "flow.create_task", {"title": "Sai", "assignee_email": sale3.email, **bad})
        assert r.status == "invalid" and r.error and "hệ thống" not in r.error, (bad, r.status, r.error)
    tid = await _checked_task(who, deal)
    assert (await _task(tid))["status"] == "todo" and (await _task(tid))["check_status"] in ("missing", "received")
    # không ai bấm "Xong" được: người làm, trưởng nhóm, trợ lý; kéo thẻ trên bảng là cùng lệnh
    for p in (sale3, truongb, sale3.as_agent()):
        r = await executor.run(p, "flow.set_task", {"task_id": tid, "status": "done"})
        assert r.status == "invalid" and "xong khi" in (r.error or "").lower(), (p.email, r.status, r.error)
    assert (await _task(tid))["status"] == "todo"
    with pytest.raises(psycopg.Error, match="không tự đánh dấu xong"):                                       # DB chặn cả đường ghi thẳng
        async with db.tenant_tx(sale3) as conn:
            await conn.execute("UPDATE flow.tasks SET status = 'done', done_at = now() WHERE id = %s", (tid,))
    with pytest.raises(psycopg.Error, match="không đổi được"):                                               # điều kiện xong không đổi giữa chừng
        async with db.tenant_tx(truongb) as conn:
            await conn.execute("UPDATE flow.tasks SET done_args = '{\"seq\": 2}' WHERE id = %s", (tid,))
    page = await q(sale3, "flow.task", task_id=tid)
    commands = [a["command"] for a in page["actions"]]
    assert "flow.recheck_task" in commands and "flow.request_override" in commands
    assert not any(a["command"] == "flow.set_task" and (a.get("preset") or {}).get("status") == "done" for a in page["actions"])
    assert (await ok(sale3.as_agent(), "flow.recheck_task", task_id=tid))["status"] == "todo"                 # trợ lý kiểm lại được; chưa đạt thì vẫn mở
    await ok(admin, "orderdesk.pay_stage", code=code, seq=1)
    await events.drain()                                                                                      # sự kiện đánh thức, bộ kiểm quyết
    t = await _task(tid)
    assert (t["status"], t["done_by"], t["check_status"]) == ("done", "evidence", "verified")
    assert await _n("SELECT count(*) FROM core.events WHERE type = 'flow.task_done' AND payload->>'task_id' = %s", (tid,)) == 1
    assert await _n("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE 'Xong (có bằng chứng)%%' AND open->'input'->>'task_id' = %s",
                    (sale3.user_id, tid)) == 1
    assert _blocks(await q(sale3, "flow.task", task_id=tid), "callout")[0]["title"] == "Đóng bằng bằng chứng"


async def test_a_module_task_carries_its_own_checker_and_people_never_need_one(who: Who) -> None:
    """Ghi chú 07/11 của 0117: việc MODULE sinh ra gắn bộ kiểm khớp với nó qua ``flow.module_task`` (người bấm không chọn gì); việc người tạo không cần bộ
    kiểm. Module chỉ gắn bộ kiểm / nguồn của chính nó; một nguồn một việc đang mở."""
    admin, truongb, sale3 = await who("admin"), await who("truongb"), await who("sale3")
    code, deal = await _deal(who)
    out = await ok(truongb, "orderdesk.follow_stage", code=code, seq=2)
    t = await _task(str(out["task_id"]))
    assert (t["origin"], t["done_check"], t["done_args"], t["assignee_id"], t["subject_id"], t["source_kind"], t["source_ref"]) == (
        "module", "orderdesk.stage_paid", {"seq": 2}, sale3.user_id, uuid.UUID(deal), "orderdesk.stage", f"{code}#2")
    assert (await ok(truongb, "orderdesk.follow_stage", code=code, seq=2))["task_id"] == out["task_id"]      # bấm lại ⇒ việc cũ
    plain = await ok(sale3, "flow.create_task", title="Gọi khách hỏi lịch đóng", subject_kind="orderdesk.order", subject_id=deal)
    assert (await _task(str(plain["task_id"])))["done_check"] is None                                            # người tạo: không bộ kiểm
    async with db.tenant_tx(truongb, rollback=True) as conn:                                                      # bộ kiểm / nguồn của module khác
        base = {"title": "Thử", "assignee_id": sale3.user_id, "subject_kind": "orderdesk.order", "subject_id": deal, "source_ref": "x"}
        with pytest.raises(Denied, match="thuộc module books"):
            await executor.core_ctx(conn, truongb, module="orderdesk").call_system("flow.module_task", **base, done_check="books.filings_done",
                                                                          source_kind="orderdesk.stage")
        with pytest.raises(Denied, match="thuộc module acct"):
            await executor.core_ctx(conn, truongb, module="orderdesk").call_system("flow.module_task", **base, done_check="orderdesk.stage_paid",
                                                                          done_args="seq=2", source_kind="acct.receivable")
        with pytest.raises(Denied, match="họ thao tác"):   # 0186: hợp đồng mở theo họ follow_up — module chưa khai consumes thì không qua
            await executor.core_ctx(conn, truongb, module="mkt").call_system("flow.module_task", **base, done_check="orderdesk.stage_paid",
                                                                        done_args="seq=2", source_kind="orderdesk.stage")
        with pytest.raises(Denied, match="call_system"):                                                      # lách hợp đồng bằng call_as
            await executor.core_ctx(conn, truongb, module="mkt").call_as("flow.module_task", truongb, **base, done_check="orderdesk.stage_paid",
                                                                        done_args="seq=2", source_kind="orderdesk.stage")
    await ok(admin, "orderdesk.pay_stage", code=code, seq=2)
    await events.drain()
    t = await _task(str(out["task_id"]))
    assert (t["status"], t["done_by"]) == ("done", "evidence")                                                  # tự đóng khi tiền về


async def test_two_simultaneous_module_tasks_for_one_source_make_one_task(who: Who) -> None:
    """Review 07/11: "một nguồn một việc đang mở" chỉ kiểm bằng SELECT ⇒ hai lượt cùng lúc thành hai việc. Chỉ mục duy nhất (mig 036) ép ở DB, lượt
    sau nhận việc của lượt trước. Lượt đầu giữ giao dịch mở cho tới khi lượt sau đã chạm chỉ mục ⇒ cuộc đua chắc chắn xảy ra."""
    truongb = await who("truongb")
    code, _ = await _deal(who)
    inp = {"code": code, "seq": 2}
    async with db.tenant_tx(truongb) as conn:
        first = await executor.invoke(executor.core_ctx(conn, truongb), "orderdesk.follow_stage", inp)
        second = asyncio.create_task(executor.run(truongb, "orderdesk.follow_stage", inp))
        await asyncio.sleep(0.5)
        assert not second.done()                                     # lượt sau đang chờ chỉ mục duy nhất, không lặng lẽ tạo việc thứ hai
    r = await second
    assert r.status == "ok" and str(r.output["task_id"]) == str(first["task_id"]), (r.status, r.error)
    assert await _n("SELECT count(*) FROM flow.tasks WHERE source_kind = 'orderdesk.stage' AND source_ref = %s", (f"{code}#2",)) == 1


async def test_checker_numbers_follow_the_case_file_rule_not_who_sees_the_deal(who: Who) -> None:
    sale3, giamdoc, nhansu = await who("sale3"), await who("giamdoc"), await who("nhansu")
    _, deal = await _deal(who)
    for check in ("orderdesk.stage_paid",):   # sale tự gắn bộ kiểm vào đơn CỦA MÌNH (cùng luật cho mọi bộ kiểm — may_see_check_value)
        tid = str((await ok(sale3, "flow.create_task", title=f"Theo dõi {check}", subject_kind="orderdesk.order", subject_id=deal,
                            done_check=check, done_args="seq=1"))["task_id"])
        assert MONEY.search((await _task(tid))["check_value"]), check                                    # bộ kiểm có trả số tiền…
        facts = [f for b in _blocks(await q(sale3, "flow.task", task_id=tid), "callout") for f in b["facts"]]
        assert not any(MONEY.search(f) for f in facts), (check, facts)                                   # …nhưng sale không thấy trên thẻ
        assert not MONEY.search((await ok(sale3, "flow.recheck_task", task_id=tid))["summary"])         # …lẫn khi kiểm tra lại
        facts = [f for b in _blocks(await q(giamdoc, "flow.task", task_id=tid), "callout") for f in b["facts"]]
        assert any(MONEY.search(f) for f in facts), (check, facts)                                       # người xem cả công ty thì thấy
    usable = {o["value"] for o in await q(sale3, "flow.checker_options")}
    assert "orderdesk.stage_paid" in usable
    assert not {o["value"] for o in await q(nhansu, "flow.checker_options")} & {"orderdesk.stage_paid", "orderdesk.signed"}   # không quyền với đơn
    r = await executor.run(nhansu, "flow.create_task", {"title": "Thử", "subject_kind": "orderdesk.order", "subject_id": deal, "done_check": "orderdesk.signed"})
    assert r.status in ("invalid", "not_found")


async def _sql(p: Principal, sql: str, params: Any) -> None:
    async with db.tenant_tx(p) as conn:
        await conn.execute(sql, params)   # type: ignore[arg-type]


async def test_the_database_refuses_every_way_to_fake_a_closing(who: Who) -> None:
    truongb, sale3 = await who("truongb"), await who("sale3")
    _, deal = await _deal(who)
    tid = await _checked_task(who, deal, "Thu cọc (ghi thẳng)")
    sysp = system_principal(sale3.tenant_id)
    fakes: list[tuple[Principal, str, tuple[Any, ...], str]] = [
        (sale3, "UPDATE flow.tasks SET status = 'done', done_at = now(), done_by = 'evidence' WHERE id = %s", (tid,), "bằng chứng"),
        (sale3, "UPDATE flow.tasks SET status = 'done', done_at = now(), done_by = 'evidence', check_status = 'verified' WHERE id = %s", (tid,),
         "chỉ hệ thống"),                                                                               # tự ghi "đạt" trong cùng câu
        (sale3, "UPDATE flow.tasks SET status = 'done', done_at = now(), done_by = 'override', override_reason = 'x', override_asked_by = %s,"
                " override_by = ARRAY[%s]::uuid[] WHERE id = %s", (sale3.user_id, truongb.user_id, tid), "chỉ hệ thống"),
        (sale3, "UPDATE flow.tasks SET status = 'done', done_at = now(), done_by = 'xong_roi' WHERE id = %s", (tid,), "không hợp lệ"),
        (sysp, "UPDATE flow.tasks SET status = 'done', done_at = now(), done_by = 'evidence' WHERE id = %s", (tid,), "vừa báo đạt"),   # chưa đạt
        (sysp, "UPDATE flow.tasks SET status = 'done', done_at = now(), done_by = 'override', override_asked_by = %s, override_by = '{}' WHERE id = %s",
         (sale3.user_id, tid), "người duyệt"),                                                          # không ai duyệt
        (sysp, "UPDATE flow.tasks SET status = 'done', done_at = now(), done_by = 'override', override_asked_by = %s,"
               " override_by = ARRAY[%s]::uuid[] WHERE id = %s", (sale3.user_id, sale3.user_id, tid), "người duyệt"),   # tự duyệt
    ]
    for p, sql, params, msg in fakes:
        with pytest.raises(psycopg.Error, match=msg):
            await _sql(p, sql, params)
    t = await _task(tid)
    assert (t["status"], t["done_by"], t["check_status"]) == ("todo", None, t["check_status"]) and t["check_status"] != "verified"


async def test_the_sweep_is_not_starved_by_tasks_of_closed_plans(who: Who) -> None:
    sale3 = await who("sale3")
    _, deal = await _deal(who)
    sysp = system_principal(sale3.tenant_id)
    async with db.tenant_tx(sysp) as conn:   # 120 việc đã đóng bằng bằng chứng của một kế hoạch đã nghiệm thu, chưa từng được kiểm lại
        cur = await conn.execute("INSERT INTO flow.plans (title, status, closed_at) VALUES ('Kế hoạch đã nghiệm thu (quét)', 'done', now()) RETURNING id")
        closed = (await cur.fetchone() or {})["id"]
        await conn.execute("""INSERT INTO flow.tasks (title, assignee_id, plan_id, subject_kind, subject_id, done_check, done_args, status, done_by,
                                                      done_at, check_status)
                              SELECT 'Việc cũ ' || g, %s, %s, 'orderdesk.order', %s, 'orderdesk.stage_paid', '{"seq": 1}', 'done', 'evidence', now(), 'verified'
                              FROM generate_series(1, 120) g""", (sale3.user_id, closed, deal))
    tid = await _checked_task(who, deal, "Thu cọc (chờ quét)")
    await _as_system(sysp.tenant_id, "UPDATE flow.tasks SET checked_at = now() - interval '1 hour' WHERE id = %s", (tid,))
    r = await executor.run(sysp, "flow.check_tasks", {})
    assert r.status == "ok", r.error
    assert await _n("SELECT count(*) FROM flow.tasks WHERE id = %s AND checked_at > now() - interval '1 minute'", (tid,)) == 1   # việc mở được kiểm
    assert await _n("SELECT count(*) FROM flow.tasks WHERE plan_id = %s AND checked_at IS NOT NULL", (closed,)) == 0              # việc của kế hoạch đóng: không chọn


async def test_one_task_whose_check_fails_does_not_sink_the_whole_sweep(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Một việc kiểm lỗi (bộ kiểm của gói ném lỗi / dòng không ghi được) chỉ hỏng phần của nó: lượt quét vẫn ok, việc khác vẫn được kiểm, việc lỗi
    được đẩy mốc kiểm (không đứng đầu hàng mãi chặn cả công ty — từng làm bộ test chập chờn 14/11)."""
    import barista.modules.flow.evidence as ev
    sale3 = await who("sale3")
    _, deal = await _deal(who)
    sysp = system_principal(sale3.tenant_id)
    bad, good = await _checked_task(who, deal, "Thu cọc (bộ kiểm hỏng)"), await _checked_task(who, deal, "Thu cọc (bình thường)")
    for tid in (bad, good):
        await _as_system(sysp.tenant_id, "UPDATE flow.tasks SET checked_at = now() - interval '1 hour' WHERE id = %s", (tid,))
    real = ev._run_checker

    async def flaky(ctx: Any, t: Any) -> Any:
        if str(t["id"]) == str(bad):
            raise psycopg.errors.InsufficientPrivilege("không được ghi dòng này")
        return await real(ctx, t)

    monkeypatch.setattr(ev, "_run_checker", flaky)
    r = await executor.run(sysp, "flow.check_tasks", {})
    assert r.status == "ok", r.error
    assert r.output.get("failed", 0) >= 1
    assert await _n("SELECT count(*) FROM flow.tasks WHERE id = %s AND checked_at > now() - interval '1 minute'", (good,)) == 1
    assert await _n("SELECT count(*) FROM flow.tasks WHERE id = %s AND checked_at > now() - interval '1 minute' AND check_status = 'unknown'",
                    (bad,)) == 1


async def test_two_checks_at_once_close_the_task_once(who: Who) -> None:
    admin = await who("admin")
    code, deal = await _deal(who)
    tid = await _checked_task(who, deal, "Thu cọc (đua nhau)")
    await ok(admin, "orderdesk.pay_stage", code=code, seq=1)        # chưa chạy sự kiện: hai lượt kiểm tự tới cùng lúc
    sysp = system_principal(admin.tenant_id)
    async with db.owner_tx() as conn:                                # giữ khoá dòng để hai lượt chắc chắn chồng lên nhau
        await conn.execute("SELECT 1 FROM flow.tasks WHERE id = %s FOR UPDATE", (tid,))
        runs = [asyncio.create_task(executor.run(sysp, "flow.check_task", {"task_id": tid})) for _ in range(2)]
        await asyncio.sleep(0.3)
    results = await asyncio.gather(*runs)
    assert [r.status for r in results] == ["ok", "ok"] and sorted(r.output["closed"] for r in results) == [0, 1]
    await events.drain()                                             # sự kiện đến muộn cũng không đóng lại lần nữa
    assert await _n("SELECT count(*) FROM core.events WHERE type = 'flow.task_done' AND payload->>'task_id' = %s", (tid,)) == 1
    sent = "FROM core.notifications WHERE title LIKE 'Xong (có bằng chứng)%%' AND open->'input'->>'task_id' = %s"
    assert await _n("SELECT count(*) " + sent, (tid,)) == await _n("SELECT count(DISTINCT user_id) " + sent, (tid,)) == 2   # người làm + người giao, mỗi người một


async def test_evidence_that_disappears_reopens_the_task_once_and_plan_progress_follows(who: Who) -> None:
    admin, truongb, sale3 = await who("admin"), await who("truongb"), await who("sale3")
    code, deal = await _deal(who)
    plan = await ok(truongb, "flow.propose_plan", title=f"Thu cọc tuần {uuid.uuid4().hex[:4]}", due_on=(today() + timedelta(days=15)).isoformat(),
                    tasks=[{"title": "Thu đủ cọc", "assignee_email": sale3.email, "subject_kind": "orderdesk.order", "subject_id": deal, **PAID},
                           {"title": "Gọi cảm ơn khách", "assignee_email": sale3.email}])
    pid = str(plan["plan_id"])
    checked, plain = [str(await owner_val("SELECT id FROM flow.tasks WHERE plan_id = %s AND title = %s", (pid, x))) for x in ("Thu đủ cọc", "Gọi cảm ơn khách")]
    await ok(sale3, "flow.set_task", task_id=plain, status="done")                                           # việc thường: tự báo vẫn đơn giản
    await ok(admin, "orderdesk.pay_stage", code=code, seq=1)
    await events.drain()
    kpi = next(k for k in _blocks(await q(truongb, "flow.plan", plan_id=pid), "kpis")[0]["items"] if k["label"] == "Tiến độ")
    assert kpi["value"] == "2/2 việc" and kpi["hint"] == "1 có bằng chứng · 1 tự báo"
    row = next(r for r in (await q(truongb, "flow.plans"))["rows"] if str(r["id"]) == pid)
    assert row["risk"] == "review"

    reopened = "SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE 'Mở lại vì căn cứ%%' AND open->'input'->>'task_id' = %s"
    await owner_val("UPDATE orderdesk.stages SET paid_vnd = 0 WHERE order_id = %s AND seq = 1 RETURNING 1", (deal,))   # tiền bị trả lại / ghi nhầm
    assert (await _sweep(admin.tenant_id))["reopened"] >= 1
    t = await _task(checked)
    assert (t["status"], t["done_by"], t["done_at"]) == ("todo", None, None) and t["evidence_lost_at"] is not None   # tự mở lại, giữ mốc
    assert await _n(reopened, (truongb.user_id, checked)) == await _n(reopened, (sale3.user_id, checked)) == 1     # người giao + người làm
    g = await q(truongb, "flow.plan", plan_id=pid)
    assert next(k for k in _blocks(g, "kpis")[0]["items"] if k["label"] == "Tiến độ")["value"] == "1/2 việc"   # tiến độ phản ánh ngay
    assert (await executor.run(truongb, "flow.set_plan", {"plan_id": pid, "status": "done"})).status == "invalid"
    assert any("mở lại" in f for f in _blocks(await q(sale3, "flow.task", task_id=checked), "callout")[0]["facts"])
    await _sweep(admin.tenant_id)                                                                         # vẫn chưa đạt: không báo lại
    assert await _n(reopened, (truongb.user_id, checked)) == 1
    await owner_val("UPDATE orderdesk.stages SET paid_vnd = amount_vnd WHERE order_id = %s AND seq = 1 RETURNING 1", (deal,))
    assert (await _sweep(admin.tenant_id))["closed"] >= 1 and (await _task(checked))["done_by"] == "evidence"   # có lại căn cứ ⇒ đóng lại
    await owner_val("UPDATE orderdesk.stages SET paid_vnd = 0 WHERE order_id = %s AND seq = 1 RETURNING 1", (deal,))
    await _sweep(admin.tenant_id)
    assert await _n(reopened, (truongb.user_id, checked)) == 2                                          # lần mất MỚI ⇒ một thông báo mới


async def test_closing_without_evidence_is_an_approved_exception_with_who_and_why(who: Who) -> None:
    truongb, sale3 = await who("truongb"), await who("sale3")
    _, deal = await _deal(who)
    tid = await _checked_task(who, deal, "Thu cọc (ngoại lệ)")
    reason = "Khách đã chuyển khoản thẳng cho chủ đầu tư"
    assert (await executor.run(sale3.as_agent(), "flow.request_override", {"task_id": tid, "reason": reason})).status == "denied"   # trợ lý không xin thay
    assert (await executor.run(sale3, "flow.request_override", {"task_id": tid, "reason": "xong"})).status == "invalid"
    r = await executor.run(sale3, "flow.request_override", {"task_id": tid, "reason": reason})
    assert r.status == "pending_approval" and r.approval_id
    assert (await _task(tid))["status"] == "todo"                                                         # chưa duyệt thì chưa đóng
    assert (await executor.run(edge(sale3), "core.decide", {"approval_id": r.approval_id, "approve": True})).status != "ok"   # người xin không tự duyệt
    await ok(edge(truongb), "core.decide", approval_id=r.approval_id, approve=True)                              # trưởng nhóm của người xin
    t = await _task(tid)
    assert (t["status"], t["done_by"], t["override_asked_by"], list(t["override_by"])) == ("done", "override", sale3.user_id, [truongb.user_id])
    assert t["override_reason"] == reason
    callout = _blocks(await q(sale3, "flow.task", task_id=tid), "callout")[0]
    assert callout["title"] == "Đóng ngoại lệ (đã duyệt)" and any("Bùi Thanh Tùng duyệt" in f for f in callout["facts"])
    r = await executor.run(system_principal(truongb.tenant_id), "flow.close_by_override", {"task_id": tid, "reason": "chạy thẳng không qua duyệt"})
    assert r.status in ("denied", "invalid")                                                              # không có đường đóng ngoại lệ không duyệt


async def test_cancelling_a_checked_task_needs_the_same_approval_and_stays_visible_in_progress(who: Who) -> None:
    truongb, sale3 = await who("truongb"), await who("sale3")
    _, deal = await _deal(who)
    plan = await ok(truongb, "flow.propose_plan", title=f"Thu cọc huỷ {uuid.uuid4().hex[:4]}", due_on=(today() + timedelta(days=15)).isoformat(),
                    tasks=[{"title": "Thu đủ cọc", "assignee_email": sale3.email, "subject_kind": "orderdesk.order", "subject_id": deal, **PAID},
                           {"title": "Gửi lời cảm ơn", "assignee_email": sale3.email}])
    pid = str(plan["plan_id"])
    checked, plain = [str(await owner_val("SELECT id FROM flow.tasks WHERE plan_id = %s AND title = %s", (pid, x))) for x in ("Thu đủ cọc", "Gửi lời cảm ơn")]
    for p in (sale3, truongb):                                                                            # người làm lẫn trưởng nhóm: không huỷ thẳng
        r = await executor.run(p, "flow.set_task", {"task_id": checked, "status": "cancelled"})
        assert r.status == "invalid" and "duyệt" in (r.error or ""), (p.email, r.error)
    with pytest.raises(psycopg.Error, match="Huỷ việc có bộ kiểm"):
        await _sql(sale3, "UPDATE flow.tasks SET status = 'cancelled' WHERE id = %s", (checked,))
    r = await executor.run(sale3, "flow.request_override", {"task_id": checked, "outcome": "cancelled", "reason": "Khách chuyển sang mua căn khác của dự án"})
    assert r.status == "pending_approval"
    await ok(edge(truongb), "core.decide", approval_id=r.approval_id, approve=True)                              # trưởng nhóm duyệt huỷ
    t = await _task(checked)
    assert (t["status"], t["override_asked_by"], list(t["override_by"])) == ("cancelled", sale3.user_id, [truongb.user_id])
    assert _blocks(await q(sale3, "flow.task", task_id=checked), "callout")[0]["title"] == "Huỷ qua duyệt"
    await ok(sale3, "flow.set_task", task_id=plain, status="done")
    kpi = next(k for k in _blocks(await q(truongb, "flow.plan", plan_id=pid), "kpis")[0]["items"] if k["label"] == "Tiến độ")
    assert kpi["value"] == "1/1 việc" and kpi["hint"] == "1 tự báo · 1 huỷ (có bộ kiểm)"                     # không lặng lẽ rơi khỏi tiến độ


async def test_a_pack_turned_off_leaves_the_task_open_and_unchecked_not_failed(who: Who) -> None:
    sale3 = await who("sale3")
    _, deal = await _deal(who)
    tid = await _checked_task(who, deal, "Thu cọc (gói tắt)")
    tenant = sale3.tenant_id
    await owner_val("DELETE FROM core.tenant_packs WHERE tenant_id = %s AND pack = 'orderdesk' RETURNING 1", (tenant,))
    packs.invalidate(tenant)
    try:
        sale3 = await who("sale3")
        page = await q(sale3, "flow.task", task_id=tid)                                                  # việc KHÔNG ẩn
        assert any(b["title"] == "Gắn với một đơn dịch vụ" for b in _blocks(page, "callout"))
        await _sweep(tenant)
        t = await _task(tid)
        assert (t["status"], t["check_status"]) == ("todo", "unknown")                                   # chưa kiểm được ≠ chưa đạt
        assert (await executor.run(sale3, "flow.set_task", {"task_id": tid, "status": "done"})).status == "invalid"
    finally:
        await owner_val("INSERT INTO core.tenant_packs (tenant_id, pack) VALUES (%s, 'orderdesk') RETURNING 1", (tenant,))
        packs.invalidate(tenant)


async def test_done_when_probe_turns_only_parameter_errors_into_invalid() -> None:
    """Chạy thử bộ kiểm lúc gắn: lỗi do THAM SỐ (sai kiểu / thiếu / DB không nhận giá trị) ⇒ Invalid; lỗi khác (mất kết nối, lỗi lập trình) lộ ra."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from barista.kernel.errors import Invalid
    from barista.modules.flow.tasks import _done_when

    def ctx_raising(exc: Exception | None) -> Any:
        @asynccontextmanager
        async def transaction():
            yield

        async def call_system(*_: Any, **__: Any) -> None:
            if exc:
                raise exc

        return SimpleNamespace(conn=SimpleNamespace(transaction=transaction), call_system=call_system)

    sid = uuid.uuid4()
    probe = lambda exc: _done_when(ctx_raising(exc), "orderdesk.stage_paid", "seq=1", "orderdesk.order", sid, by_module=True)  # noqa: E731
    assert (await probe(None))[1] == {"seq": 1}
    for exc in (ValueError("x"), TypeError("x"), KeyError("seq"), ArithmeticError("x"), psycopg.errors.InvalidTextRepresentation("x")):
        with pytest.raises(Invalid, match="không hợp lệ cho bộ kiểm"):
            await probe(exc)
    for exc in (RuntimeError("lỗi lập trình"), psycopg.OperationalError("mất kết nối")):
        with pytest.raises(type(exc)):
            await probe(exc)
