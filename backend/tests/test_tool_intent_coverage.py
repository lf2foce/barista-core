"""Công cụ có mặt đúng LƯỢT ĐẦU theo Ý ĐỊNH của câu hỏi (eval DeepSeek 11/11 §4.3, mục 5): "nhắn tin" ⇒ công cụ Trao đổi; xin nghỉ ⇒ tra loại nghỉ trước
khi soạn đơn; hỏi hạn trong hợp đồng ⇒ kho tài liệu có trong bộ công cụ + lời dặn bắt đọc hợp đồng. Phủ ý định là DỮ LIỆU (mô tả, từ khoá của công cụ)
— test này khoá lại để mô tả bị tỉa thì thấy ngay."""

import uuid
from typing import Any

import pytest

from barista import seed_pay
from barista.agent import chat, toolset
from barista.config import settings
from barista.kernel import registry
from barista.kernel.principal import Principal


@pytest.fixture(autouse=True)
def retrieve_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_tools", "retrieve")


async def _offered(p: Principal, question: str) -> set[str]:
    """Đúng cách ``ToolRun.tools`` dựng: danh mục của người (agent) ∩ phần cố định + K công cụ tìm theo câu."""
    ops = await chat.catalog(p.as_agent())
    plan = toolset.plan(p.role, (op.name for op, _ in ops), question=question, retrieve=settings.agent_tool_k,
                        describe={op.name: chat._describe(op, mode) for op, mode in ops}, schema={op.name: chat.tool_schema(op) for op, _ in ops})
    return plan.visible


@pytest.fixture(scope="module")
async def office() -> dict[str, Any]:
    return await seed_pay.office(f"ti{uuid.uuid4().hex[:6]}.demo", "Văn phòng ý định thử (test)")


async def test_leave_request_offers_the_leave_type_lookup_next_to_the_command(office: dict[str, Any]) -> None:
    got = await _offered(office["who"]["nvchi"], "Cho tôi xin nghỉ ốm thứ Sáu tuần này.")
    assert {"timekeeping.leave_types", "timekeeping.request_leave", "timekeeping.my_month"} <= got
    desc = chat._describe(registry.OPS["timekeeping.request_leave"], "propose")
    assert "leave_types" in desc and desc.index("leave_types") < 200   # lời "tra loại nghỉ trước" nằm ĐẦU mô tả (mô tả bị cắt ở cuối)


async def test_contract_deadline_questions_have_the_document_store_and_the_rule_to_read_it(office: dict[str, Any]) -> None:
    assert registry.tool_kind(registry.OPS["kb.search"]) == "core"                       # luôn mang: không phụ thuộc BM25
    assert "hợp đồng" in chat.RULES and "kb__search" in chat.RULES.split("hợp đồng", 1)[1][:400]
    assert "HỢP ĐỒNG" in registry.OPS["kb.search"].description
