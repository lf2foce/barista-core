"""SOẠN VỚI AI dạng LUỒNG (``/v1/compose/stream``) trên nguồn của một gói ngành, qua gói thế vai: bản LÕI (ADR 0190) của
``barista-packs:real_estate/tests/test_compose_stream.py`` — web thấy tiến trình ngay; truy vấn / tập dữ liệu của gói chạy bằng quyền người soạn; lỗi lấy số không
chạy lại; chạm lưới chặn / hết giờ / bấm Dừng ⇒ giữ phần đã lấy."""

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from pydantic_ai import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from barista.kernel import executor, vocab
from barista.modules import compose
from tests.conftest import Who, q
from tests.test_compose_stream import _events, _model


@pytest_asyncio.fixture(autouse=True)
async def compose_on(who: Who) -> AsyncIterator[None]:
    """"Soạn với AI" là gói TUỲ CHỌN, mặc định tắt: bật cho CÔNG TY THẾ VAI (như quản trị bấm ở "Store") rồi tắt lại sau test — bản của fixture
    ``compose_on`` trong ``tests/conftest.py`` (fixture đó bật cho công ty mẫu)."""
    admin = await who("admin")
    r = await executor.run(admin, "admin.set_pack", {"pack": "compose", "enabled": True})
    assert r.status == "ok", (r.status, r.error)
    try:
        yield
    finally:
        await executor.run(admin, "admin.set_pack", {"pack": "compose", "enabled": False})


def _fetching_model(text: str) -> FunctionModel:
    """Lần 1 gọi fetch_data (truy vấn chạy bằng quyền người soạn), lần 2 trả bản soạn dùng khối số liệu d1."""

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("fetch_data", {"query": "orderdesk.list", "title": "Đơn", "input": {}}, tool_call_id="f1")])
        out = {"title": "Báo cáo đơn dịch vụ", "blocks": [{"type": "heading", "text": "Tình hình"}, {"type": "paragraph", "text": text},
                                                         {"type": "data", "ref": "d1"}]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, out, tool_call_id="o1")])

    return FunctionModel(reply, model_name="compose-stream-fetch")


async def test_chat_turns_follow_up_streams_through_the_same_door_and_persists(who: Who) -> None:
    """Soạn mới rồi nói tiếp ("rút gọn đoạn 2") — cả hai lượt đi luồng, bảng vừa lấy hiện trước bản đầy đủ, mở lại thấy đủ cuộc trò chuyện."""
    sale = await who("sale1")
    compose.MODEL = _fetching_model("Như bảng dưới.")
    try:
        first = await _events(sale, {"kind": "document", "brief": "Báo cáo đơn dịch vụ cho giám đốc"})
        sid = first[0]["session_id"]
        steps = [e for e in first if e["type"] == "step"]
        assert [s["status"] for s in steps] == ["running", "ok"] and steps[0]["title"]          # nhãn truy vấn đang chạy, rồi xong
        data = next(e for e in first if e["type"] == "data")
        assert data["block"]["type"] == "data" and data["block"]["page"]["kind"] == "table"   # bảng hiện trước khi AI viết xong
        assert first.index(data) < next(i for i, e in enumerate(first) if e["type"] == "blocks")
        assert first[-1]["status"] == "ready" and first[-1]["turn"]["role"] == "assistant"
        second = await _events(sale, {"session_id": sid, "instruction": "Rút gọn đoạn 2"})
    finally:
        compose.MODEL = None
    assert second[0] == {"type": "session", "session_id": sid} and second[-1]["status"] == "ready"
    comp = (await q(sale, "compose.view", session_id=sid))["blocks"][0]
    assert comp["version"] == 2 and comp["status"] == "ready"
    assert [(t["role"], t["text"][:14]) for t in comp["turns"]][::2] == [("user", "Báo cáo đơn dị"), ("user", "Rút gọn đoạn 2")]
    assert [t["status"] for t in comp["turns"] if t["role"] == "assistant"] == ["ready", "ready"]
    # bước lưu trong lượt trò chuyện = bước đã phát trong luồng; tên truy vấn của gói viết bằng dấu nhãn từ vựng (0124): bản mở lại qua executor đã đổi
    # thành chữ của công ty, còn sự kiện của luồng hiện mang nguyên dấu nhãn ⇒ so sau khi đổi nhãn
    assert comp["turns"][1]["steps"] == [{"title": vocab.resolve(steps[0]["title"], sale.vocab), "status": "ok"}]
    assert comp["history"][0]["id"] == sid
    other = await who("sale2")
    studio = (await q(other, "compose.studio"))["blocks"][0]
    assert studio["session_id"] == "" and sid not in [h["id"] for h in studio["history"]]   # lịch sử chỉ bản của mình


def _tool(name: str, args: dict[str, Any], cid: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(name, args, tool_call_id=cid)])


async def test_the_same_failed_fetch_is_not_run_twice_and_the_step_says_why(who: Who) -> None:
    """Mô hình gọi lại ĐÚNG truy vấn + đầu vào vừa lỗi (đốt hết lượt như bản chụp màn hình): lần 2, 3 bị từ chối ngay không chạy lại — chỉ MỘT cặp
    running → error trong luồng; dòng lỗi có lý do tiếng Việt ngắn (không tên trường), và lý do được lưu vào lượt trò chuyện."""
    sale = await who("sale1")
    bad = {"query": "orderdesk.ds_orders", "title": "Đơn theo tháng", "input": {"by": ["khong_co_chieu_nay"]}}
    told: list[str] = []

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if sum(1 for m in messages if m.kind == "response") == 0:
            return ModelResponse(parts=[ToolCallPart("fetch_data", bad, tool_call_id=f"c{i}") for i in range(3)])   # ba lần liền, cùng đầu vào
        told.extend(str(p.content) for m in messages if m.kind == "request" for p in m.parts if p.part_kind == "tool-return")
        out = {"title": "Báo cáo", "blocks": [{"type": "paragraph", "text": "Chưa có số theo tháng. [cần số liệu: đơn theo tháng]"}]}
        return _tool(info.output_tools[0].name, out, "o1")

    compose.MODEL = FunctionModel(reply, model_name="repeat-fetch")
    try:
        events = await _events(sale, {"kind": "document", "brief": "Báo cáo đơn theo tháng"})
    finally:
        compose.MODEL = None
    steps = [e for e in events if e["type"] == "step" and e["tool"] == "orderdesk.ds_orders"]
    assert [s["status"] for s in steps] == ["running", "error"]                      # chạy đúng MỘT lần
    assert steps[1]["reason"] and "khong_co_chieu_nay" not in steps[1]["reason"]     # lý do dễ hiểu, không lộ tên trường
    assert sum("ĐỪNG gọi lại" in t for t in told) == 2                              # hai lần gọi lặp được báo "đã thử, lỗi"
    assert events[-1]["status"] == "ready"
    turn = events[-1]["turn"]
    assert turn["steps"][0]["reason"] == steps[1]["reason"]


async def test_a_runaway_guard_delivers_the_partial_draft_instead_of_failing(who: Who, monkeypatch: Any) -> None:
    """Chạm lưới chặn chạy vô hạn (số lần gọi model) khi model còn loay hoay: KHÔNG báo lỗi — bản soạn giữ mọi khối số liệu đã lấy + chỗ giữ cho số
    liệu thiếu, lượt AI đánh dấu ``partial`` để web hiện "Tiếp tục"."""
    sale = await who("sale1")
    monkeypatch.setattr(compose, "REQUESTS", 4)

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        n = sum(1 for m in messages if m.kind == "response")
        if n == 0:
            return _tool("fetch_data", {"query": "orderdesk.ds_orders", "title": "Đơn theo tháng", "input": {"by": ["khong_co"]}}, "c0")   # lỗi ⇒ chỗ thiếu
        if n == 1:
            return _tool("fetch_data", {"query": "orderdesk.list", "title": "Đơn", "input": {}}, "c1")                                       # lấy được
        return _tool("find_data", {"need": "doanh số"}, f"f{n}")                                                                                # rồi loay hoay mãi

    compose.MODEL = FunctionModel(reply, model_name="runaway")
    try:
        events = await _events(sale, {"kind": "document", "brief": "Báo cáo đơn theo tháng"})
    finally:
        compose.MODEL = None
    sid = events[0]["session_id"]
    assert events[-1]["status"] == "ready" and events[-1]["error"] == ""
    blocks = next(e for e in events if e["type"] == "blocks")["blocks"]
    assert [b["type"] for b in blocks] == ["data", "paragraph"]
    assert "[cần số liệu: Đơn theo tháng]" in blocks[1]["text"]
    turn = events[-1]["turn"]
    assert turn["partial"] is True and "Đơn theo tháng" in turn["text"] and "Tiếp tục" in turn["text"]
    comp = (await q(sale, "compose.view", session_id=sid))["blocks"][0]
    assert comp["status"] == "ready" and comp["version"] == 1 and comp["turns"][-1]["partial"] is True


async def test_timeout_after_some_data_delivers_the_partial_draft(who: Who, monkeypatch: Any) -> None:
    """Hết giờ giữa chừng: số liệu đã lấy không bị bỏ — giao bản dở (khi chưa lấy được gì thì vẫn báo "Hết giờ", test_slow_model_times_out…)."""
    import asyncio

    sale = await who("sale1")
    monkeypatch.setattr(compose, "TIMEOUT_S", 1.5)

    async def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if sum(1 for m in messages if m.kind == "response") == 0:
            return _tool("fetch_data", {"query": "orderdesk.list", "title": "Đơn", "input": {}}, "c1")
        await asyncio.sleep(10)
        raise AssertionError("không tới đây")

    compose.MODEL = FunctionModel(reply, model_name="slow-after-data")
    try:
        events = await _events(sale, {"kind": "document", "brief": "Báo cáo đơn dịch vụ"})
    finally:
        compose.MODEL = None
    assert events[-1]["status"] == "ready" and events[-1]["turn"]["partial"] is True
    assert [b["type"] for b in next(e for e in events if e["type"] == "blocks")["blocks"]] == ["data"]


async def test_stop_cancels_the_running_turn_keeps_what_was_fetched_and_releases_the_slot(who: Who, monkeypatch: Any) -> None:
    """"Dừng" thật (không chỉ đóng kết nối): lệnh ``compose.stop`` huỷ lượt model đang chạy ở server. Khối số liệu đã lấy được GIỮ thành phiên bản, lượt AI
    ghi "đã dừng" (không phải lỗi), chỗ giữ của công ty được trả; người khác không dừng được lượt của tôi; "Tiếp tục" làm nốt từ bản dở."""
    import asyncio

    sale, other = await who("sale1"), await who("sale2")
    never = asyncio.Event()

    async def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if sum(1 for m in messages if m.kind == "response") == 0:
            return _tool("fetch_data", {"query": "orderdesk.list", "title": "Đơn", "input": {}}, "c1")
        await never.wait()   # model "đang nghĩ" mãi — chỉ Dừng mới kết thúc được
        raise AssertionError("không tới đây")

    monkeypatch.setattr(compose, "MODEL", FunctionModel(reply, model_name="hang"))
    run = asyncio.create_task(_events(sale, {"kind": "document", "brief": "Báo cáo đơn dịch vụ"}))
    for _ in range(200):   # chờ tới khi lượt chạy thật sự (đã đăng ký việc), rồi cho bảng đầu tiên lấy xong
        if compose._tasks:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(1.0)
    sid = next(iter(compose._tasks))
    assert compose._running.get(sale.tenant_id)

    stolen = await executor.run(other, "compose.stop", {"session_id": str(sid)})
    assert stolen.status == "not_found" and not compose._tasks[sid][1].done()              # người khác không dừng được
    assert (await executor.run(sale, "compose.stop", {"session_id": str(sid)})).output == {"stopped": True}
    events = await asyncio.wait_for(run, 30)

    assert events[-1]["type"] == "done" and events[-1]["status"] == "stopped" and events[-1]["turn"]["status"] == "stopped"
    kept = next(e for e in events if e["type"] == "blocks")["blocks"]
    assert [b["type"] for b in kept] == ["data"]                                           # khối đã lấy được giữ lại
    comp = (await q(sale, "compose.view", session_id=str(sid)))["blocks"][0]
    assert comp["status"] == "stopped" and comp["status_label"] == "Đã dừng" and comp["version"] == 1
    assert comp["turns"][-1]["status"] == "stopped" and comp["turns"][-1]["partial"] is True
    assert [b["type"] for b in comp["blocks"]] == ["data"]
    assert not compose._running.get(sale.tenant_id) and sid not in compose._tasks           # chỗ giữ được trả
    assert (await executor.run(sale, "compose.stop", {"session_id": str(sid)})).output == {"stopped": False, "reason": "done"}   # hết gì để dừng

    monkeypatch.setattr(compose, "MODEL", _model())
    again = await _events(sale, {"session_id": str(sid), "instruction": "Tiếp tục: làm nốt phần còn thiếu"})   # "Tiếp tục" như một lượt sửa thường
    assert again[-1]["status"] == "ready"
    assert (await q(sale, "compose.view", session_id=str(sid)))["blocks"][0]["status"] == "ready"
