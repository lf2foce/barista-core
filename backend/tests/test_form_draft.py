"""Form theo ý định + "Soạn giúp" (ADR 0250).

- Lệnh khai ``intent=`` một lần ⇒ form công bố thêm ``intent`` + ``draft`` (chỉ THÊM trường); lệnh không khai giữ form y như cũ.
- ``core.draft_input``: model (giả trong test — không gọi model thật) soạn đầu vào của ĐÚNG lệnh đó; CODE kiểm: ô lạ / ô ẩn bị bỏ, giá trị chọn phải nằm trong danh
  sách tải bằng QUYỀN NGƯỜI GỌI (người ngoài phạm vi giao việc không được gợi ý), kiểu từng ô theo model của lệnh (ô sai bị bỏ, không vỡ cả bản soạn), ô người đã
  điền giữ nguyên. Không bao giờ chạy lệnh; trợ lý chat không gọi được; người gửi bản soạn qua executor như mọi lần.
"""

from datetime import timedelta
from typing import Any

import pytest
from pydantic_ai import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from barista.kernel import executor, form_draft, registry
from barista.kernel.clock import today
from barista.kernel.fields import Input, field
from barista.kernel.forms import field_specs, form_spec
from tests.conftest import Who, ok, owner_val, q


class _Fake:
    """Model giả qua đúng cửa ``ai.run`` (công tắc AI + hạn mức vẫn áp): trả ``values`` cho sẵn, ghi lại lời nhắn đã nhận."""

    def __init__(self, values: dict[str, Any], notes: list[str] | None = None) -> None:
        self.values, self.notes, self.prompts = values, notes or [], []

    def __enter__(self) -> _Fake:
        def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.prompts.append(str(messages[-1]))
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"values": self.values, "notes": self.notes})])

        form_draft.DRAFT_MODEL = FunctionModel(reply, model_name="draft-fake")
        return self

    def __exit__(self, *_: object) -> None:
        form_draft.DRAFT_MODEL = None


def test_intent_is_one_declaration_and_forms_without_it_are_unchanged() -> None:
    plan = registry.get("flow.propose_plan")
    assert plan is not None and plan.intent == ("title", "due_on", "owner_email")
    spec = form_spec(plan)
    assert spec["intent"] == ["title", "due_on", "owner_email"] and spec["draft"] == "core.draft_input"
    assert {f["key"] for f in spec["fields"]} >= {"title", "goal", "tasks"}                         # form đủ ô vẫn còn (app chưa biết ⇒ vẽ như cũ)
    task = registry.get("flow.create_task")
    assert task is not None and not task.intent
    assert set(form_spec(task)) == {"command", "title", "description", "importable", "fields", "confirm"}   # không khai ⇒ y như trước, chỉ THÊM luật xác nhận (0253)

    class In(Input):
        title: str = field("Tên")
        secret: str = field("Ẩn", "", hidden=True)
        rare: str = field("Hiếm", "", agent=False)

    registry.validate_intent("x.y", "command", False, ("title",), In)
    for bad in (("nope",), ("secret",), ("rare",), ("title", "title")):
        with pytest.raises(ValueError):
            registry.validate_intent("x.y", "command", False, bad, In)
    with pytest.raises(ValueError):
        registry.validate_intent("x.y", "query", False, ("title",), In)                              # chỉ lệnh, không truy vấn / lệnh nội bộ
    with pytest.raises(ValueError):
        registry.validate_intent("x.y", "command", True, ("title",), In)


def test_cleaning_keeps_only_valid_values_in_the_lists_and_never_overrides_the_person() -> None:
    op = registry.get("flow.propose_plan")
    assert op is not None
    fields = form_draft._draftable(op, field_specs(op))
    keys = {f["key"] for f in fields}
    assert "tasks" in keys and "subject_kind" not in {c["key"] for f in fields if f["key"] == "tasks" for c in f["fields"]}   # ô ẩn / agent=False không soạn
    opts = {"tasks.assignee_email": [{"value": "a@x.demo", "label": "Chị A · Marketing · 2 việc mở"}],
            "owner_email": [{"value": "a@x.demo", "label": "Chị A · Marketing · 2 việc mở"}]}
    raw = {"title": "Máy đổi tên", "goal": "Ra mắt gọn", "due_on": "không phải ngày", "owner_email": "b@x.demo",
           "tasks": [{"title": "Việc 1", "assignee_email": "Chị A", "due_on": "2030-01-05"},
                     {"title": "Việc 2", "assignee_email": "ngoai@x.demo", "due_on": "05/13/2030"},
                     {"assignee_email": "a@x.demo"},                          # thiếu tên việc ⇒ bỏ cả dòng
                     {"title": "Việc 1", "due_on": "2030-01-06"}]}            # tên trùng ⇒ bỏ
    values, guessed, notes = form_draft.clean(op, fields, opts, raw, {"title": "Ra mắt sản phẩm mới"})
    assert "title" not in values                                              # người đã điền ⇒ máy không sửa
    assert values["goal"] == "Ra mắt gọn" and "due_on" not in values and "owner_email" not in values
    assert values["tasks"] == [{"title": "Việc 1", "assignee_email": "a@x.demo", "due_on": "2030-01-05"},   # gọi tên ⇒ đúng người trong danh sách
                               {"title": "Việc 2"}]                                                       # người ngoài danh sách + ngày sai ⇒ bỏ ô
    assert set(guessed) == {"goal", "tasks"} and len(notes) >= 3


async def test_draft_a_plan_from_intent_with_the_callers_rights_then_the_person_submits(who: Who) -> None:
    lead, a, b = await who("mkt"), await who("mkt2"), await who("mkt3")
    milestone = today() + timedelta(days=30)
    title = "Ra mắt dòng sản phẩm mới (test soạn giúp)"
    fake = {"goal": "Đủ ấn phẩm và quảng cáo trước ngày ra mắt",
            "tasks": [{"title": "Chốt thông điệp", "assignee_email": a.name, "due_on": (milestone - timedelta(days=20)).isoformat()},
                      {"title": "Thiết kế ấn phẩm", "assignee_email": b.email, "due_on": (milestone - timedelta(days=10)).isoformat(),
                       "after": "Chốt thông điệp"},
                      {"title": "Chạy quảng cáo", "assignee_email": "sale1@sanmau.demo", "due_on": milestone.isoformat(),   # ngoài phạm vi giao việc của trưởng MKT
                       "after": "Thiết kế ấn phẩm"}]}
    with _Fake(fake, ["Đoán người làm theo phòng"]) as m:
        out = await q(lead, "core.draft_input", command="flow.propose_plan", text="Ra mắt cuối tháng, cần thông điệp, ấn phẩm, quảng cáo",
                       values={"title": title, "due_on": milestone.isoformat()})
        assert len(m.prompts) == 1
        prompt = m.prompts[0]
        assert f"Hôm nay: {today():%Y-%m-%d}" in prompt and title in prompt and "ấn phẩm, quảng cáo" in prompt
        assert a.email in prompt and "sale1@sanmau.demo" not in prompt                                # danh sách chọn tải bằng quyền người gọi
        r = await executor.query(lead.as_agent(), "core.draft_input", {"command": "flow.propose_plan", "text": "x"})
        assert r.status == "denied" and len(m.prompts) == 1                                           # trợ lý chat không gọi; không gửi gì cho model
        r = await executor.query(lead, "core.draft_input", {"command": "flow.create_task", "text": "x"})
        assert r.status == "not_found"                                                                # lệnh không khai ý định ⇒ không có soạn giúp
    v = out["values"]
    assert v["title"] == title and v["due_on"] == milestone.isoformat()                               # ô người điền giữ nguyên, không tính là máy đoán
    assert set(out["guessed"]) == {"goal", "tasks"} and "Đoán người làm theo phòng" in out["notes"]
    assert [t.get("assignee_email") for t in v["tasks"]] == [a.email, b.email, None]                   # gọi tên ⇒ đúng người; người ngoài phạm vi ⇒ bỏ
    assert any("không có trong danh sách" in n for n in out["notes"])
    assert await owner_val("SELECT count(*) FROM flow.plans WHERE title = %s", (title,)) == 0         # soạn giúp không chạy lệnh
    assert await owner_val("SELECT count(*) FROM core.ai_usage WHERE category = 'flow:draft' AND user_id = %s", (lead.user_id,)) >= 1   # lượt model đo như mọi lượt

    v["tasks"][2]["assignee_email"] = lead.email                                                       # người sửa chip còn thiếu rồi gửi qua executor
    made = await ok(lead, "flow.propose_plan", **v)
    assert made["tasks"] == 3
    deps = await owner_val("""SELECT count(*) FROM flow.tasks WHERE plan_id = %s AND cardinality(depends_on) = 1""", (made["plan_id"],))
    assert deps == 2                                                                                  # thứ tự việc máy đặt đi vào kế hoạch


async def test_a_draft_needs_something_to_work_from_and_a_broken_model_is_a_readable_error(who: Who) -> None:
    lead = await who("mkt")
    r = await executor.query(lead, "core.draft_input", {"command": "flow.propose_plan"})
    assert r.status == "invalid"

    def boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("nhà cung cấp lỗi")

    form_draft.DRAFT_MODEL = FunctionModel(boom, model_name="draft-boom")
    try:
        r = await executor.query(lead, "core.draft_input", {"command": "flow.propose_plan", "text": "Kế hoạch tết"})
    finally:
        form_draft.DRAFT_MODEL = None
    assert r.status == "invalid" and r.error and "Điền tay toàn bộ" in r.error
