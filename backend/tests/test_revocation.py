"""Thu hồi quyền tới được luồng dài (review 12/11 F1, ADR 0193): lượt chat đang chạy / phiên giọng nói đang mở mà người hỏi bị khoá, đổi vai hay đăng
xuất từ một request khác ⇒ công cụ KẾ TIẾP không chạy (cả xem lẫn ghi), lượt dừng với lời báo rõ. Đúng executor + Postgres thật; tín hiệu "quyền đã
đổi" đi đường thật (NOTIFY ``barista_auth`` → ``live.hub``)."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from barista import seed
from barista.db import db
from barista.kernel import executor, live, sessions, standing
from barista.kernel.principal import system_principal
from tests.conftest import OWNER_URL, owner_val
from tests.test_assistant import Scripted, call
from tests.test_voice import FakeLive, FakeWS, ask

PEOPLE = [("qt", "Quản trị", "admin", "QT", True), ("gd", "Giám đốc", "director", "HQ", True), ("sale", "Sale", "sale", "KD-A", False)]


@pytest_asyncio.fixture
async def listening() -> AsyncIterator[None]:
    ready = asyncio.Event()
    task = asyncio.create_task(live.listen_forever(OWNER_URL, ready))
    await asyncio.wait_for(ready.wait(), 30)
    yield
    task.cancel()


async def _company() -> tuple[Any, str]:
    d = f"{uuid.uuid4().hex[:6]}.revoke.test"
    return await seed.create_tenant("Công ty thu hồi (test)", d, people=PEOPLE, packs=("crm",)), d


async def _bumped(tid: Any, before: int) -> None:
    for _ in range(250):   # tín hiệu NOTIFY tới hub (kết nối LISTEN riêng) — chờ tín hiệu, không đoán bằng giờ
        if live.hub.auth_epoch(str(tid)) != before:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("không nhận được tín hiệu barista_auth")


class Midway(Scripted):
    """Model kịch bản: trước câu trả lời thứ ``at`` chạy ``hook`` (request KHÁC — quản trị khoá / đổi vai người đang hỏi)."""

    def __init__(self, turns: list[Any], hook: Any, at: int = 0) -> None:
        super().__init__(turns)
        self.hook, self.at, self.n = hook, at, 0

    async def _stream(self, messages: Any, info: Any) -> AsyncIterator[Any]:
        if self.n == self.at:
            await self.hook()
        self.n += 1
        async for x in super()._stream(messages, info):
            yield x


async def _turn(p: Any, model: Scripted, held: standing.Standing | None = None) -> list[dict[str, Any]]:
    from barista.agent import chat

    events: list[dict[str, Any]] = []

    async def emit(ev: dict[str, Any]) -> None:
        events.append(ev)

    await chat.chat_turn(p, "Khách nào cần gọi?", emit=emit, model=model, standing=held)
    return events


async def test_deactivated_mid_turn_the_next_read_tool_does_not_run(listening: None) -> None:
    tid, d = await _company()
    qt, sale = await seed.who(tid, "qt", d), await seed.who(tid, "sale", d)
    held = standing.Standing(sale)

    async def deactivate() -> None:
        before = live.hub.auth_epoch(str(tid))
        assert (await executor.run(qt, "admin.update_member", {"email": f"sale@{d}", "active": False})).status == "ok"
        await _bumped(tid, before)

    model = Midway([[call("crm.leads", stage="open")], "Không được tới đây."], deactivate)
    events = await _turn(sale, model, held)
    assert not [e for e in events if e["type"] in ("step", "card")]                  # công cụ xem KHÔNG chạy (cả bước "đang chạy")
    err = next(e for e in events if e["type"] == "error")
    assert err["revoked"] is True and "không còn hoạt động" in err["message"]
    assert model.returns == [] and model.turns == ["Không được tới đây."]           # model không nhận kết quả nào, lượt dừng
    assert held.reloads == 1                                                         # MỘT lần nạp lại — do tín hiệu, không phải mỗi công cụ
    assert await owner_val("SELECT count(*) FROM core.chat_threads WHERE tenant_id = %s", (tid,)) == 0   # không lưu lượt bằng quyền cũ
    assert await owner_val("SELECT count(*) FROM core.ai_usage WHERE tenant_id = %s AND NOT ok", (tid,)) == 1   # token đã tốn vẫn ghi


async def test_role_changed_mid_turn_the_next_write_tool_does_not_run(listening: None) -> None:
    tid, d = await _company()
    sale = await seed.who(tid, "sale", d)

    async def demote() -> None:   # đổi vai qua đúng lệnh quản trị (quyền hệ thống — như lệnh đã được chủ tài khoản duyệt chạy lại)
        before = live.hub.auth_epoch(str(tid))
        r = await executor.run(system_principal(tid), "admin.update_member", {"email": f"sale@{d}", "role": "staff"})
        assert r.status == "ok", (r.status, r.error)
        await _bumped(tid, before)

    phone = f"010{uuid.uuid4().int % 10**7:07d}"
    model = Midway([[call("crm.leads", stage="open")], [call("crm.create_lead", name="Khách sau đổi vai", phone=phone)], "x"], demote, at=1)
    events = await _turn(sale, model)
    steps = [e for e in events if e["type"] == "step"]
    assert {s["tool"] for s in steps} == {"crm.leads"}                               # bước xem TRƯỚC khi đổi vai chạy bình thường
    assert not [e for e in events if e["type"] == "card" and e["card"]["kind"] == "proposal"]   # lệnh ghi sau khi đổi vai: không đề xuất, không chạy
    assert "Quyền của bạn vừa thay đổi" in next(e for e in events if e["type"] == "error")["message"]
    assert await owner_val("SELECT count(*) FROM crm.customers WHERE tenant_id = %s AND phone = %s", (tid, phone)) == 0


async def test_logout_reaches_a_turn_opened_with_that_session(listening: None) -> None:
    tid, d = await _company()
    sale = await seed.who(tid, "sale", d)
    async with db.owner_tx() as c:
        token, _ = await sessions.open_session(c, sale.user_id, provider="google")   # type: ignore[arg-type]
    held = standing.Standing(sale, session=token)
    assert (await held.check(force=True)).user_id == sale.user_id
    before = live.hub.auth_epoch(str(tid))
    async with db.owner_tx() as c:
        await sessions.revoke(c, token)                                               # đăng xuất ở thiết bị khác — tín hiệu "*"
    await _bumped(tid, before)
    with pytest.raises(standing.Revoked, match="thu hồi"):
        await held.check()
    with pytest.raises(standing.Revoked):                                              # đã dừng là dừng
        await held.check()


async def test_without_a_signal_the_ttl_still_bounds_how_long_old_authority_lives(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mất kết nối LISTEN (không có tín hiệu): quá TTL thì nạp lại — giới hạn trên của độ trễ. Trong TTL và không tín hiệu: không SQL nào."""
    tid, d = await _company()
    sale = await seed.who(tid, "sale", d)
    held = standing.Standing(sale)
    for _ in range(20):
        await held.check()
    assert held.reloads == 0                                                          # chi phí mỗi công cụ khi không có gì đổi: 0 truy vấn
    await owner_val("UPDATE core.memberships SET active = false WHERE tenant_id = %s AND user_id = %s RETURNING 1", (tid, sale.user_id))
    await held.check()                                                                 # trong TTL: chưa thấy (đã biết, có giới hạn)
    monkeypatch.setattr(standing, "TTL", 0.0)
    with pytest.raises(standing.Revoked):
        await held.check()


async def test_voice_session_stops_when_the_member_is_deactivated(listening: None) -> None:
    tid, d = await _company()
    gd = await seed.who(tid, "gd", d)
    from barista.agent import voice

    ws, live_ = FakeWS(), FakeLive()
    closed: list[int] = []

    async def close(code: int = 1000, reason: str = "") -> None:
        closed.append(code)

    ws.close = close  # type: ignore[method-assign]
    model = Scripted(["Dạ."])
    bridge = voice.Bridge(ws, gd, "x", None, model=model)  # type: ignore[arg-type]
    await bridge.answer(live_, ask("Chào em"))
    assert not model.turns and not closed
    # giám đốc là chủ tài khoản duy nhất (lệnh khoá chặn khoá người chủ cuối) ⇒ khoá thẳng ở DB, như quản trị hệ thống làm từ bản cài
    await owner_val("UPDATE core.memberships SET active = false WHERE tenant_id = %s AND user_id = %s RETURNING 1", (tid, gd.user_id))
    model.turns.append("không được gọi")
    await bridge.answer(live_, ask("Doanh thu tháng này?"))
    assert model.turns == ["không được gọi"] and closed == [4403]                     # không chạy lượt chat nào, phiên đóng
    assert "Trợ lý đã dừng" in live_.responses[-1].response["answer"]
    assert any(m.get("type") == "error" and "không còn hoạt động" in m.get("detail", "") for m in ws.sent)
