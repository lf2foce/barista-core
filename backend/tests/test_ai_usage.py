"""Đo token của trợ lý theo loại việc (quyết định 0049)."""

from typing import Any

from barista.agent import chat
from barista.kernel import executor
from tests.conftest import Who, owner_val, q
from tests.test_assistant import Scripted, call
from tests.test_journey import _blocks


async def _noop(_: dict[str, Any]) -> None:
    return None


async def _last(user_email: str) -> dict[str, Any]:
    return await owner_val(
        """SELECT row_to_json(x) FROM (SELECT surface, category, modules, model, requests, tool_calls, input_tokens, output_tokens, cost_usd, ok
           FROM core.ai_usage a JOIN core.users u ON u.id = a.user_id WHERE u.email = %s ORDER BY a.id DESC LIMIT 1) x""", (user_email,))


async def test_runs_that_hit_the_limit_still_count_and_offer_to_continue(who: Who) -> None:
    from tests.test_resume import Log

    sale1 = await who("sale1")
    endless = Scripted([[call("crm.leads", stage="open")]] * 40)
    log = Log()
    out = await chat.chat_turn(sale1, "Tra mãi", emit=log, model=endless)
    row = await _last(sale1.email)
    assert "Tiếp tục" in out["text"] and row["ok"] is False and row["requests"] > 5          # đã tốn token dù không ra câu trả lời
    done = log.of("done")[-1]
    assert done["limit"] == {"steps": 20, "continue": chat.CONTINUE}                        # trợ lý thường: trần chung 20, app vẽ nút Tiếp tục
    saved = await owner_val("SELECT turns->-1 FROM core.chat_threads WHERE id = %s", (out["thread_id"],))
    assert saved["limit"]["steps"] == 20                                                     # mở lại cuộc trò chuyện vẫn thấy lượt dừng vì chạm trần
    calm = Log()
    await chat.chat_turn(sale1, "Chào em", emit=calm, model=Scripted(["Chào anh."]))
    assert "limit" not in calm.of("done")[-1]                                                # lượt xong bình thường: không có nút


async def test_a_mode_declares_its_own_step_cap_the_app_builder_gets_fifty(who: Who) -> None:
    from barista.kernel import registry
    from tests.test_resume import Log

    assert registry.MODE_STEPS["builder"] == 50 and "assistant" not in registry.MODE_STEPS
    gd = await who("giamdoc")
    log = Log()
    out = await chat.chat_turn(gd, "dựng app thật nhiều phần", emit=log, model=Scripted([[call("apps.list")]] * 60 + ["Xong phần đầu."]), mode="builder")
    ran = [s for s in out["steps"] if s.get("status") == "ok"]
    assert len(ran) == 50                                                                    # chạy đủ 50 bước rồi mới dừng (không phải 20)
    assert log.of("done")[-1]["limit"] == {"steps": 50, "continue": chat.CONTINUE}


async def test_the_cost_report_follows_who_may_see_whose_usage(who: Who) -> None:
    sale1, sale3, truonga, giamdoc = await who("sale1"), await who("sale3"), await who("truonga"), await who("giamdoc")
    for p in (sale1, sale3):
        await chat.chat_turn(p, "Chào em", emit=_noop, model=Scripted(["Chào anh."]))
    page = await q(giamdoc, "core.ai_usage", period="7d")
    kpis = {k["label"]: k["value"] for k in _blocks(page, "kpis")[0]["items"]}
    assert kpis["Lượt chạy"] >= 2 and "Token vào" not in kpis and "Chi phí ước tính" not in kpis   # khách thấy LƯỢT, không token / tiền (0193)
    names = {lbl for c in _blocks(page, "chart") if c["title"] == "Lượt theo người" for lbl in c["labels"]}
    assert {"Nguyễn Văn An", "Đặng Gia Khánh"} <= names                                      # giám đốc: cả công ty
    team = {lbl for c in _blocks(await q(truonga, "core.ai_usage", period="7d"), "chart") if c["title"] == "Lượt theo người"
            for lbl in c["labels"]}
    assert "Nguyễn Văn An" in team and "Đặng Gia Khánh" not in team                         # trưởng nhóm A: chỉ đội mình
    assert (await executor.query(sale1, "core.ai_usage", {"period": "7d"})).status == "denied"


async def test_system_errors_reach_the_audit_log_with_detail_and_turns_are_timed(who: Who, monkeypatch) -> None:
    """ROADMAP A4: lỗi BẤT NGỜ của truy vấn ghi vào nhật ký kèm loại + nội dung lỗi (quản trị sửa được), người dùng chỉ thấy câu chung; lỗi
    nghiệp vụ không ghi. Lượt trợ lý có thời gian (``started``)."""
    import dataclasses
    import time

    from pydantic_ai.usage import RunUsage

    from barista.agent import usage
    from barista.kernel import executor, registry

    admin = await who("admin")
    op = registry.OPS["admin.audit"]

    async def boom(ctx, inp):
        raise RuntimeError("hỏng thử cho nhật ký")

    monkeypatch.setitem(registry.OPS, op.name, dataclasses.replace(op, handler=boom))
    r = await executor.query(admin, "admin.audit", {})
    assert r.status == "error" and "hỏng thử" not in r.error                                     # người dùng: câu chung
    assert await owner_val("""SELECT count(*) FROM core.audit_log WHERE command = 'admin.audit' AND status = 'error'
                              AND error LIKE '%%RuntimeError: hỏng thử cho nhật ký%%'""") == 1    # quản trị: chi tiết để sửa
    monkeypatch.setitem(registry.OPS, op.name, op)
    assert (await executor.query(admin, "admin.audit", {"limit": 0})).status == "invalid"
    assert await owner_val("SELECT count(*) FROM core.audit_log WHERE command = 'admin.audit' AND status = 'invalid'") == 0

    await usage.record(admin, surface="chat", category="thử", modules=[], usage=RunUsage(), model="", started=time.monotonic() - 1.5)
    assert await owner_val("SELECT duration_ms FROM core.ai_usage WHERE category = 'thử' ORDER BY id DESC LIMIT 1") >= 1500


async def test_system_errors_notify_company_admins_once_a_day_and_the_operator_without_a_storm(who: Who, monkeypatch) -> None:
    """Lỗi hệ thống ⇒ (1) quản trị công ty (quyền đọc Nhật ký cả công ty) nhận MỘT thông báo / ngày, mở sẵn Nhật ký lọc lỗi; (2) nhà vận hành
    nhận tin Telegram — lỗi lặp cùng thao tác + loại chỉ một tin / khoảng yên lặng; chưa cấu hình kênh ⇒ không gửi."""
    import dataclasses

    from barista.config import settings
    from barista.kernel import alerts, events, registry

    admin = await who("admin")
    op = registry.OPS["admin.audit"]

    async def boom(ctx, inp):
        raise RuntimeError("hỏng thử báo quản trị")

    sent: list[str] = []

    async def fake_send(text: str) -> None:
        sent.append(text)

    monkeypatch.setitem(registry.OPS, op.name, dataclasses.replace(op, handler=boom))
    monkeypatch.setattr(alerts, "_send", fake_send)
    monkeypatch.setattr(alerts, "_last", {})
    monkeypatch.setattr(settings, "alert_telegram_token", "")
    await executor.query(admin, "admin.audit", {})
    monkeypatch.setattr(settings, "alert_telegram_token", "t")
    monkeypatch.setattr(settings, "alert_telegram_chat", "c")
    for _ in range(3):
        await executor.query(admin, "admin.audit", {})
    await events.drain()
    for t in list(alerts._tasks):
        await t
    assert len(sent) == 1 and "admin.audit: RuntimeError" in sent[0]                  # chưa cấu hình: 0 tin; 3 lỗi lặp: 1 tin
    gd = await who("giamdoc")   # người gây lỗi đã thấy lỗi — không tự nhận (quy ước thông báo); người khác có quyền Nhật ký cả công ty nhận
    n = await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND kind = 'system' AND title = 'Có lỗi hệ thống hôm nay'",
                        (gd.user_id,))
    assert n == 1                                                                       # 4 lỗi trong ngày: một thông báo
