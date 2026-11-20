"""Trợ lý: bộ công cụ theo quyền người hỏi, thẻ dữ liệu, thẻ đề xuất, lưu cuộc trò chuyện. Model giả theo kịch bản."""

import json
from collections.abc import AsyncIterator
from typing import Any

from pydantic_ai import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from barista.agent import chat
from barista.kernel import executor
from tests.conftest import Who, fresh_phone, owner_val, q


class Scripted(FunctionModel):
    """Mỗi lượt gọi model lấy một mục: list[ToolCallPart] (gọi công cụ) hoặc str (trả lời)."""

    def __init__(self, turns: list[Any]) -> None:
        self.turns = list(turns)
        self.tools_seen: list[set[str]] = []
        self.returns: list[Any] = []
        self.history_len: list[int] = []
        super().__init__(self._respond, stream_function=self._stream, model_name="scripted")

    def _next(self, messages: list[ModelMessage], info: AgentInfo) -> Any:
        self.tools_seen.append({t.name for t in info.function_tools})
        self.history_len.append(len(messages))
        for part in getattr(messages[-1], "parts", []):
            if isinstance(part, ToolReturnPart):
                self.returns.append(part.content)
        return self.turns.pop(0)

    def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        t = self._next(messages, info)
        return ModelResponse(parts=[TextPart(t)] if isinstance(t, str) else t)

    async def _stream(self, messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        t = self._next(messages, info)
        if isinstance(t, str):
            for piece in t.split(" "):
                yield piece + " "
        else:
            yield {i: DeltaToolCall(name=c.tool_name, json_args=json.dumps(c.args), tool_call_id=c.tool_call_id)
                   for i, c in enumerate(t)}


def call(name: str, /, **args: Any) -> ToolCallPart:
    return ToolCallPart(tool_name=chat.tool_name(name), args=args, tool_call_id=f"c-{name}")


async def _turn(p, message: str, script: list[Any], thread_id=None) -> tuple[dict[str, Any], list[dict[str, Any]], Scripted]:
    events: list[dict[str, Any]] = []

    async def emit(ev: dict[str, Any]) -> None:
        events.append(ev)

    model = Scripted(script)
    out = await chat.chat_turn(p, message, emit=emit, thread_id=thread_id, model=model)
    return out, events, model


async def test_write_becomes_proposal_card_and_nothing_is_written(who: Who) -> None:
    sale1 = await who("sale1")
    phone = fresh_phone()
    _, events, model = await _turn(sale1, "Thêm khách Lan 0109..., quan tâm Hồ Sen", [
        [call("crm.create_lead", name="Khách Lan đề xuất", phone=phone, offering_code="HSR")],
        "Em đã soạn đề xuất, anh/chị kiểm tra rồi bấm Xác nhận.",
    ])
    card = next(e["card"] for e in events if e["type"] == "card")
    assert card["kind"] == "proposal" and card["command"] == "crm.create_lead" and card["input"]["phone"] == phone
    assert {f["key"] for f in card["fields"]} >= {"name", "phone"} and card["resumable"] is True
    assert model.returns == [] and model.turns                                       # lượt DỪNG chờ người (0049) — chưa đọc kết quả gì
    assert next(e for e in events if e["type"] == "done")["pending"] is True
    assert await owner_val("SELECT count(*) FROM crm.customers WHERE phone = %s", (phone,)) == 0


async def test_allowed_write_shows_its_result_card(who: Who) -> None:
    """Bản nháp trợ lý soạn (lệnh được tự làm) hiện thành thẻ để người xem / tải / ban hành."""
    _, events, model = await _turn(await who("giamdoc"), "Soạn công văn", [
        [call("papers.draft_letter", recipient="CĐT Mẫu Một", subject="đề nghị cung cấp bảng hàng", body="Kính đề nghị…")],
        "Em đã soạn dự thảo.",
    ])
    card = next(e["card"] for e in events if e["type"] == "card")
    assert card["query"] == "papers.view" and card["page"]["kind"] == "detail"
    assert card["page"]["links"][0]["path"].endswith("/docx") and card["page"]["actions"][0]["command"] == "papers.issue"
    assert model.returns[0]["shown_to_user_as_card"] is True


def test_select_filters_expose_valid_values_to_the_model() -> None:
    from barista.kernel import registry

    prop = chat.tool_schema(registry.OPS["acct.receivables"])["properties"]["status"]
    assert set(prop["enum"]) >= {"open", "", "partial", "paid"} and "Chưa thu" in prop["description"]


async def test_coordinator_assistant_reads_the_queue_buttons_and_proposes_named_assignments(who: Who) -> None:
    """Trợ lý đọc thẻ hàng chờ (kèm tham số nút), soạn ĐỀ XUẤT giao từng lead; mỗi thẻ đề xuất có câu riêng (không ba thẻ giống hệt)."""
    digital = await who("digital")
    queue = await executor.query(digital, "leadflow.queue", {})
    callouts = [b for b in queue.output["blocks"] if b["type"] == "callout" and b.get("actions")]
    assert callouts, "dữ liệu mẫu phải có lead quảng cáo chờ team Digital chia"
    preset = callouts[0]["actions"][0]["preset"]
    _, events, model = await _turn(digital, "Chia giúp tôi các lead đang chờ theo gợi ý", [
        [call("leadflow.queue")],
        [call("leadflow.assign", **preset)],
        "Em đã soạn đề xuất giao lead, chị bấm Xác nhận nhé.",
    ])
    seen = json.dumps(model.returns[0], ensure_ascii=False)
    assert "leadflow.assign" in seen and preset["owner_email"] in seen                 # model thấy đúng lệnh + tham số của nút
    card = next(e["card"] for e in events if e["type"] == "card" and e["card"]["kind"] == "proposal")
    assert card["title"].startswith("Giao ") and " cho " in card["title"]
    assert await owner_val("SELECT owner_id FROM crm.leads WHERE id = %s", (preset["lead_id"],)) is None   # chưa ghi gì


async def test_follow_up_sees_previous_tool_results_so_it_can_reuse_ids(who: Who) -> None:
    """Hỏi tiếp trong cùng cuộc trò chuyện: model nhận lại NGUYÊN VĂN lời gọi + kết quả công cụ lượt trước (dùng lại mã, hiểu "lead đó"),
    không chỉ câu trả lời dạng chữ. App không nhận phần nguyên văn này."""
    sale1 = await who("sale1")
    out, _, _ = await _turn(sale1, "Khách nào đến hạn gọi lại?", [[call("crm.leads", stage="open")], "Có vài khách đến hạn."])
    seen: list[ModelMessage] = []

    class Capture(Scripted):
        def _next(self, messages: list[ModelMessage], info: AgentInfo) -> Any:
            seen.extend(messages)
            return super()._next(messages, info)

    model = Capture(["Lead đó là của anh/chị."])
    await chat.chat_turn(sale1, "Lead đầu tiên là ai?", emit=_noop, thread_id=out["thread_id"], model=model)
    returns = [p_ for m in seen for p_ in getattr(m, "parts", []) if isinstance(p_, ToolReturnPart)]
    assert returns and returns[0].tool_name == chat.tool_name("crm.leads")          # kết quả công cụ lượt trước còn nguyên
    assert "rows" in json.dumps(returns[0].content, ensure_ascii=False) or "Khách" in json.dumps(returns[0].content, ensure_ascii=False)
    stored = await chat.load_thread(sale1, out["thread_id"])
    assert stored is not None and stored["turns"][1].get("messages")                   # lưu ở DB cho lượt sau
    assert all(m.get("instructions") is None for m in stored["turns"][1]["messages"] if m.get("kind") == "request")


async def _noop(ev: dict[str, Any]) -> None:
    return None


async def test_a_claimed_write_without_a_write_step_gets_a_correction(who: Who) -> None:
    """Review web 31/10: trợ lý nói "Em đã ghi nhận…" sau hai bước ĐỌC, không có lệnh ghi nào — lời không có việc đứng sau."""
    sale1 = await who("sale1")
    out, events, _ = await _turn(sale1, "Ghi chăm sóc cho khách Lan", [[call("crm.leads")], "Em đã ghi nhận cuộc gọi cho chị Lan rồi ạ."])
    assert "CHƯA có gì được lưu" in out["text"]
    assert any(e["type"] == "text" and "CHƯA có gì được lưu" in e["text"] for e in events)
    # có đề xuất (lệnh ghi được soạn) ⇒ câu "em đã soạn" không bị coi là lời nói dối; và người nói "đã soạn" cũng không khớp mẫu
    lead = (await q(sale1, "crm.leads"))["rows"][0]["id"]
    out2, _, _ = await _turn(sale1, "Ghi chăm sóc", [[call("crm.log_activity", lead_id=str(lead), kind="call")], "Em đã ghi nhận, anh/chị bấm Xác nhận nhé."])
    assert "CHƯA có gì được lưu" not in out2["text"]
    out3, _, _ = await _turn(sale1, "Chào", ["Chào anh, em đã sẵn sàng hỗ trợ ạ."])
    assert "CHƯA có gì được lưu" not in out3["text"]


def test_claim_detector_only_flags_completion_wording() -> None:
    assert chat.unbacked_write_claim("Em đã tạo lead mới cho chị Lan.", [])
    assert chat.unbacked_write_claim("Đã cập nhật xong ạ", [])
    assert not chat.unbacked_write_claim("Em đã soạn đề xuất, anh bấm Xác nhận.", [])      # "đã soạn" = chưa ghi, đúng sự thật
    assert not chat.unbacked_write_claim("Hôm nay có 3 khách quá hạn.", [])
    # câu tả dữ liệu ngôi thứ ba không phải lời nhận đã làm (31/10: câu hỏi đọc của kế toán bị gắn nhầm cảnh báo)
    assert not chat.unbacked_write_claim("Lệnh rút ví gần nhất của Nguyễn Văn An đã chuyển khoản thành công hôm nay.", [])
    assert not chat.unbacked_write_claim("- Khách Lan đã đặt cọc 200 triệu.", [])
    assert chat.unbacked_write_claim("Xong ạ. Đã giao lead cho sale2.", [])
    assert chat.unbacked_write_claim("Mình đã lưu lịch hẹn.", [])



def test_latex_in_replies_becomes_plain_text() -> None:
    raw = "Phí:\n$$\\text{Phí} = \\text{Giá tính phí} \\times \\text{Tỉ lệ}$$\nGiá $5 và $10 giữ nguyên; $a \\le b$."
    assert chat.plain_math(raw) == "Phí:\nPhí = Giá tính phí × Tỉ lệ\nGiá $5 và $10 giữ nguyên; a ≤ b."
