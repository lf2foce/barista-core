"""Soạn với AI dạng LUỒNG (``/v1/compose/stream``): web thấy tiến trình ngay thay cho "đang gửi" rồi "mở lại sau ít giây".

Bất biến cần giữ: luồng chỉ nói **đang làm gì** và **khối đã dựng** — quyền + RLS + số liệu vẫn do server quyết qua executor như mọi lệnh;
sự kiện cuối ``done`` luôn tới, kể cả khi lỗi, nên app không bao giờ treo ở trạng thái "đang soạn".
"""

import json
from typing import Any

import httpx
import pytest
from pydantic_ai import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from barista.config import settings
from barista.http_api import app
from barista.modules import compose
from tests.conftest import Who

pytestmark = pytest.mark.usefixtures("compose_on")   # gói tuỳ chọn, mặc định tắt — bật cho công ty mẫu


def _model(text: str = "Tình hình tháng này ổn định.") -> FunctionModel:
    """Một lượt model: trả thẳng bản soạn (không gọi công cụ) để luồng chỉ có status → blocks → done."""

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        out = {"title": "Báo cáo giao dịch", "blocks": [{"type": "heading", "text": "Tình hình giao dịch"},
                                                         {"type": "paragraph", "text": text}]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, out, tool_call_id="o1")])

    return FunctionModel(reply, model_name="compose-stream-fake")


async def _events(sale: Any, body: dict[str, Any]) -> list[dict[str, Any]]:
    """Đọc trọn luồng NDJSON của một lượt soạn (header dev = phiên + công ty đang chọn, như app thật)."""
    base = settings.public_api_url.rstrip("/")
    headers = {"X-Dev-User": sale.email, "X-Tenant-Id": str(sale.tenant_id)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base) as c:
        async with c.stream("POST", "/v1/compose/stream", headers=headers, json=body) as r:
            assert r.status_code == 200, await r.aread()
            raw = await r.aread()
    return [json.loads(line) for line in raw.decode().splitlines() if line.strip()]


async def test_compose_stream_emits_progress_then_blocks_then_done(who: Who) -> None:
    sale = await who("sale1")
    compose.MODEL = _model()
    try:
        events = await _events(sale, {"kind": "document", "brief": "Báo cáo giao dịch tháng này cho giám đốc"})
    finally:
        compose.MODEL = None
    types = [e["type"] for e in events]
    assert types[0] == "session" and types[-1] == "done"          # mở bằng mã bản nháp, đóng bằng done
    assert "status" in types and "blocks" in types               # có tiến trình, không chỉ im lặng chờ
    assert events[0]["session_id"] and events[-1]["status"] == "ready"
    payload = next(e for e in events if e["type"] == "blocks")
    assert [b["type"] for b in payload["blocks"]] == ["heading", "paragraph"]
    assert payload["title"] == "Báo cáo giao dịch" and payload["blocks"][1]["origin"] == "ai"


async def test_compose_stream_rejects_empty_brief_without_creating_a_draft(who: Who) -> None:
    sale = await who("sale1")
    base = settings.public_api_url.rstrip("/")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base) as c:
        r = await c.post("/v1/compose/stream", headers={"X-Dev-User": sale.email, "X-Tenant-Id": str(sale.tenant_id)},
                         json={"kind": "document", "brief": "   "})
    assert r.status_code == 422


async def test_unconfigured_model_says_so_in_plain_words_and_retry_reuses_the_turn(who: Who, monkeypatch: Any) -> None:
    """Máy chưa có khoá model: lượt báo "Chưa cấu hình mô hình AI — báo quản trị" (không phải "Lỗi" trơn), chi tiết ở log; "Thử lại" chạy lại
    đúng lượt đó trên CÙNG bản soạn, không thêm lượt người nói."""
    from pydantic_ai.exceptions import UserError

    from barista.agent import llm
    from tests.conftest import q

    def no_key(spec: str | None = None) -> Any:
        raise UserError("Set the `GOOGLE_API_KEY` environment variable")

    sale = await who("sale1")
    monkeypatch.setattr(llm, "build_model", no_key)
    failed = await _events(sale, {"kind": "document", "brief": "Báo cáo giao dịch"})
    sid = failed[0]["session_id"]
    assert failed[-1]["status"] == "failed" and failed[-1]["error"] == compose.NO_MODEL
    monkeypatch.undo()
    seen: list[str] = []

    def spy(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(str(messages[0]))
        return _model().function(messages, info)   # type: ignore[misc]

    compose.MODEL = FunctionModel(spy, model_name="compose-spy")
    try:
        # chữ máy khách gửi kèm "thử lại" KHÔNG được tin: server chạy lại đúng lượt người nói đã lưu
        again = await _events(sale, {"session_id": sid, "instruction": "QZX-do-may-khach-tu-be", "retry": True})
    finally:
        compose.MODEL = None
    assert "Báo cáo giao dịch" in seen[0] and "QZX" not in seen[0]
    assert again[-1]["status"] == "ready"
    comp = (await q(sale, "compose.view", session_id=sid))["blocks"][0]
    assert [(t["role"], t.get("status")) for t in comp["turns"]] == [("user", None), ("assistant", "failed"), ("assistant", "ready")]
    assert comp["turns"][1]["text"] == compose.NO_MODEL and comp["status_label"] == "Xong"


async def test_retry_is_refused_on_a_draft_that_is_not_failed(who: Who) -> None:
    sale = await who("sale1")
    compose.MODEL = _model()
    try:
        sid = (await _events(sale, {"kind": "document", "brief": "Báo cáo giao dịch"}))[0]["session_id"]
        again = await _events(sale, {"session_id": sid, "instruction": "x", "retry": True})
    finally:
        compose.MODEL = None
    assert [e["type"] for e in again] == ["error"] and "thử lại" in again[0]["message"]


async def test_another_user_cannot_run_a_turn_on_my_draft_through_the_stream(who: Who) -> None:
    """Luồng không phải đường vòng qua RLS: người khác POST session_id của tôi ⇒ bị từ chối, bản của tôi không đổi (không thêm lượt, không đổi trạng thái)."""
    from tests.conftest import q

    sale, other = await who("sale1"), await who("sale2")
    compose.MODEL = _model()
    try:
        sid = (await _events(sale, {"kind": "document", "brief": "Báo cáo giao dịch"}))[0]["session_id"]
        stolen = await _events(other, {"session_id": sid, "instruction": "Đổi hết thành quảng cáo"})
    finally:
        compose.MODEL = None
    assert [e["type"] for e in stolen] == ["error"] and "Không thấy" in stolen[0]["message"]
    comp = (await q(sale, "compose.view", session_id=sid))["blocks"][0]
    assert comp["version"] == 1 and comp["status"] == "ready" and len(comp["turns"]) == 2


async def test_a_number_the_model_invents_in_a_streamed_revise_is_flagged(who: Who) -> None:
    """Kiểm số vẫn chạy trên đường luồng: đoạn AI viết có số không khớp khối số liệu nào ⇒ khối mang cờ ngay trong sự kiện ``blocks``."""
    sale = await who("sale1")
    compose.MODEL = _model()
    try:
        sid = (await _events(sale, {"kind": "document", "brief": "Báo cáo giao dịch"}))[0]["session_id"]
        compose.MODEL = _model("Doanh số đạt 4.512 triệu đồng với 37 giao dịch.")   # số bịa, không có khối số liệu nào
        events = await _events(sale, {"session_id": sid, "instruction": "Thêm doanh số"})
    finally:
        compose.MODEL = None
    blocks = next(e for e in events if e["type"] == "blocks")["blocks"]
    assert any(b["flags"] for b in blocks if b["type"] == "paragraph")


async def test_two_requests_at_once_on_one_draft_run_one_turn_and_refuse_the_other(who: Who, monkeypatch: Any) -> None:
    """Bấm đúp / hai tab: lệnh bắt đầu lượt KHOÁ dòng bản soạn. Dựng đúng thế tranh chấp: một kết nối khác (lượt đầu) đang giữ khoá dòng; lượt thứ hai
    phải ĐỨNG CHỜ (không đọc bản cũ "xong" rồi chạy chồng), và khi lượt đầu ghi "đang soạn" thì lượt hai bị từ chối. Bản không chuyển "chưa soạn được"."""
    import asyncio

    from barista.db import db
    from barista.kernel import executor
    from tests.conftest import q

    sale = await who("sale1")
    compose.MODEL = _model()
    sid = str((await _events(sale, {"kind": "document", "brief": "Báo cáo giao dịch"}))[0]["session_id"])
    monkeypatch.setattr(compose, "MODEL", _model("Bản sửa."))
    async with db.owner.connection() as holder, holder.transaction():   # kết nối riêng + giao dịch mở: khoá giữ tới hết khối này
        await holder.execute("SELECT 1 FROM compose.sessions WHERE id = %s FOR UPDATE", (sid,))   # lượt đầu đang ở giữa lệnh của nó
        second = asyncio.create_task(executor.run(sale, "compose.revise", {"session_id": sid, "instruction": "Sửa lần hai"}))
        await asyncio.sleep(0.5)
        assert not second.done()   # không khoá ⇒ đã đọc "xong" và chạy xong lượt trong 0.5 giây
        await holder.execute("UPDATE compose.sessions SET status = 'drafting', updated_at = now() WHERE id = %s", (sid,))
    refused = await second
    assert refused.status == "invalid" and "đang được soạn" in refused.error
    comp = (await q(sale, "compose.view", session_id=sid))["blocks"][0]
    assert comp["version"] == 1 and [t["role"] for t in comp["turns"]] == ["user", "assistant"]   # lượt bị từ chối không để lại dấu vết
    assert not compose._running.get(sale.tenant_id)


async def test_turns_keep_only_the_most_recent_on_write(who: Who, monkeypatch: Any) -> None:
    from tests.conftest import owner_val, q

    sale = await who("sale1")
    compose.MODEL = _model()
    try:
        sid = str((await _events(sale, {"kind": "document", "brief": "Báo cáo giao dịch"}))[0]["session_id"])
        monkeypatch.setattr(compose, "KEEP_TURNS", 5)
        await owner_val("""UPDATE compose.sessions SET turns = (SELECT jsonb_agg(jsonb_build_object('role', 'user', 'text', 'cũ ' || g, 'at', ''))
                                                                  FROM generate_series(1, 20) g) WHERE id = %s RETURNING 1""", (sid,))
        await _events(sale, {"session_id": sid, "instruction": "Sửa mới"})
    finally:
        compose.MODEL = None
    turns = (await q(sale, "compose.view", session_id=sid))["blocks"][0]["turns"]
    assert len(turns) == 5 and turns[-2]["text"] == "Sửa mới" and turns[-1]["role"] == "assistant" and turns[0]["text"] == "cũ 18"


async def test_a_failed_save_does_not_leave_the_draft_stuck_drafting(who: Who, monkeypatch: Any) -> None:
    """``compose.finish`` ném lỗi lần đầu (DB chập chờn): bản vẫn được đánh dấu "chưa soạn được" (không treo "đang soạn" tới khi hết hạn), luồng vẫn
    kết thúc bằng ``done`` và chỗ giữ của công ty được trả."""
    from barista.kernel import executor
    from tests.conftest import q

    sale = await who("sale1")
    real = executor.run
    calls: list[int] = []

    async def flaky(p: Any, name: str, inp: Any, **kw: Any) -> Any:
        if name == "compose.finish" and not calls:
            calls.append(1)
            raise RuntimeError("DB chập chờn")
        return await real(p, name, inp, **kw)

    monkeypatch.setattr(executor, "run", flaky)
    compose.MODEL = _model()
    try:
        events = await _events(sale, {"kind": "document", "brief": "Báo cáo giao dịch"})
    finally:
        compose.MODEL = None
    assert events[-1]["type"] == "done" and events[-1]["status"] == "failed"
    comp = (await q(sale, "compose.view", session_id=events[0]["session_id"]))["blocks"][0]
    assert comp["status"] == "failed" and comp["turns"][-1]["status"] == "failed"
    assert not compose._running.get(sale.tenant_id)


async def test_stop_before_anything_was_fetched_says_stopped_and_retry_runs_the_same_turn(who: Who, monkeypatch: Any) -> None:
    import asyncio

    from barista.kernel import executor
    from tests.conftest import q

    sale = await who("sale1")
    never = asyncio.Event()

    async def hang(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await never.wait()
        raise AssertionError("không tới đây")

    monkeypatch.setattr(compose, "MODEL", FunctionModel(hang, model_name="hang"))
    run = asyncio.create_task(_events(sale, {"kind": "document", "brief": "Báo cáo giao dịch"}))
    for _ in range(200):
        if compose._tasks:
            break
        await asyncio.sleep(0.05)
    sid = next(iter(compose._tasks))
    await asyncio.sleep(0.3)
    assert (await executor.run(sale, "compose.stop", {"session_id": str(sid)})).output == {"stopped": True}
    events = await asyncio.wait_for(run, 30)
    assert events[-1]["status"] == "stopped" and not any(e["type"] == "blocks" for e in events)
    comp = (await q(sale, "compose.view", session_id=str(sid)))["blocks"][0]
    assert comp["status"] == "stopped" and comp["version"] == 0 and comp["blocks"] == []
    monkeypatch.setattr(compose, "MODEL", _model())
    again = await _events(sale, {"session_id": str(sid), "instruction": "bỏ qua", "retry": True})   # "Thử lại": đúng lượt người nói đã lưu
    assert again[-1]["status"] == "ready"
    comp = (await q(sale, "compose.view", session_id=str(sid)))["blocks"][0]
    assert [t["role"] for t in comp["turns"]] == ["user", "assistant", "assistant"] and not compose._running.get(sale.tenant_id)


async def test_stop_reports_a_turn_running_in_another_process_instead_of_pretending(who: Who) -> None:
    """Lượt chỉ huỷ được trong cùng tiến trình: bản đang soạn mà không có việc ở đây (tiến trình máy chủ khác) ⇒ ``stopped: false, reason: elsewhere``."""
    from barista.kernel import executor
    from tests.conftest import owner_val

    sale = await who("sale1")
    compose.MODEL = _model()
    try:
        sid = str((await _events(sale, {"kind": "document", "brief": "Báo cáo giao dịch"}))[0]["session_id"])
    finally:
        compose.MODEL = None
    await owner_val("UPDATE compose.sessions SET status = 'drafting', updated_at = now() WHERE id = %s RETURNING 1", (sid,))
    assert (await executor.run(sale, "compose.stop", {"session_id": sid})).output == {"stopped": False, "reason": "elsewhere"}
