"""Tự chủ của trợ lý NHÌN TỪ KHUNG CHAT (0144): thẻ đề xuất mang ``why`` (vì sao chờ người); lần trợ lý TỰ LÀM có thẻ kết quả mang Hoàn tác
(``output.undo`` của module) — Hoàn tác chạy bằng quyền NGƯỜI bấm qua cửa lệnh thường, model không thấy nút đó; sau khi người đã xác nhận, câu
trả lời không mời "bấm Xác nhận" nữa; model quá hạn giữa lượt ⇒ "Mô hình trả lời chậm", không "Lỗi hệ thống"."""

import uuid
from typing import Any

import httpx
from pydantic_ai import ModelMessage
from pydantic_ai.models.function import AgentInfo

from barista import seed
from barista.agent import chat
from barista.kernel import executor
from barista.kernel.clock import today
from tests.conftest import Who, approved, owner_val
from tests.test_assistant import Scripted, _turn, call

PEOPLE = [("giamdoc", "Giám đốc thử", "director", "HQ", True), ("admin", "Quản trị thử", "admin", "QT", True),
          ("nv", "Nhân viên thử", "staff", "HCNS", False), ("nv2", "Nhân viên khác", "staff", "HCNS", False)]


async def test_proposal_card_says_why_it_waits(who: Who) -> None:
    sale1 = await who("sale1")
    _, events, _ = await _turn(sale1, "Thêm khách", [
        [call("crm.create_lead", name="Khách lý do", phone=f"010{uuid.uuid4().int % 10**7:07d}", offering_code="HSR")], "Em đã soạn đề xuất."])
    card = next(e["card"] for e in events if e["type"] == "card")
    assert card["kind"] == "proposal" and card["why"] and any("cần người xác nhận" in w for w in card["why"])
    assert not any("trần code" in w or "propose" in w for w in card["why"])   # chữ người đọc, không tên kỹ thuật


async def test_autorun_shows_a_result_card_with_undo_that_runs_as_the_clicker() -> None:
    d = f"u{uuid.uuid4().hex[:8]}.demo"
    tid = await seed.create_tenant(f"Công ty hoàn tác {d}", d, people=PEOPLE, packs=(), about="công ty thử (test)")
    admin, nv, nv2 = [await seed.who(tid, x, d) for x in ("admin", "nv", "nv2")]
    await approved(admin, "admin.set_agent_preset", preset="balanced")
    title = f"Việc trợ lý tự tạo {uuid.uuid4().hex[:4]}"
    _, events, model = await _turn(nv, "Tạo việc", [
        [call("flow.create_task", title=title, assignee_email=nv.email, due_on=today().isoformat())], "Em đã tạo việc."])
    card = next(e["card"] for e in events if e["type"] == "card")
    assert card["kind"] == "result" and card["subtitle"] == "Trợ lý đã tự làm"
    undo = card["undo"]
    assert undo["command"] == "flow.set_task" and undo["title"].startswith("Hoàn tác")
    assert "undo" not in model.returns[0]["output"]   # nút của người, model không thấy (không đường tự đảo)
    task = undo["input"]["task_id"]
    assert await owner_val("SELECT status FROM flow.tasks WHERE id = %s", (uuid.UUID(task),)) != "cancelled"
    # người khác không có quyền trên việc này ⇒ Hoàn tác không chạy được bằng quyền của họ
    r = await executor.run(nv2, undo["command"], undo["input"])
    assert r.status != "ok", r
    r = await executor.run(nv, undo["command"], undo["input"])
    assert r.status == "ok", r
    assert await owner_val("SELECT status FROM flow.tasks WHERE id = %s", (uuid.UUID(task),)) == "cancelled"
    via = await owner_val("SELECT via FROM core.audit_log WHERE tenant_id = %s AND command = 'flow.set_task' ORDER BY id DESC LIMIT 1", (tid,))
    assert via == "human"   # nhật ký thường, tên người bấm


async def test_after_confirming_the_reply_does_not_ask_to_confirm_again(who: Who) -> None:
    sale1 = await who("sale1")
    phone = f"010{uuid.uuid4().int % 10**7:07d}"
    _, events, _ = await _turn(sale1, "Thêm khách", [[call("crm.create_lead", name="Khách xác nhận", phone=phone, offering_code="HSR")]])
    card = next(e["card"] for e in events if e["type"] == "card")
    after: list[dict[str, Any]] = []

    async def emit(ev: dict[str, Any]) -> None:
        after.append(ev)

    model = Scripted(["Đã thêm khách Khách xác nhận. Anh/chị kiểm tra rồi bấm Xác nhận nhé."])
    await chat.decide(sale1, card["id"], "confirm", card["input"], f"k-nag-{phone}", emit=emit, model=model)
    done = next(e for e in after if e["type"] == "done")
    assert "Đã thêm khách" in done["text"] and "Xác nhận" not in done["text"]


class Slow(Scripted):
    """Lượt gọi model thứ hai quá hạn (giữa lượt, sau một bước công cụ)."""

    def _next(self, messages: list[ModelMessage], info: AgentInfo) -> Any:
        t = super()._next(messages, info)
        if isinstance(t, BaseException):
            raise t
        return t


def test_confirm_nag_is_dropped_only_when_present() -> None:
    assert chat.drop_confirm_nag("Đã xong. Anh/chị bấm **Xác nhận** giúp em.") == "Đã xong."
    assert chat.drop_confirm_nag("Đã tạo việc cho An.") == "Đã tạo việc cho An."
    assert chat.drop_confirm_nag("Bấm Xác nhận nhé.") == ""


def test_timeouts_are_recognised_only_when_they_come_from_the_model() -> None:
    from barista.agent import reliability

    try:
        try:
            raise httpx.ReadTimeout("chậm")
        except httpx.ReadTimeout as e:
            raise RuntimeError("bọc ngoài") from e
    except RuntimeError as outer:
        assert not chat.model_timed_out(outer)                       # công cụ gọi HTTP chậm không phải "mô hình trả lời chậm"
        assert chat.model_timed_out(reliability.tag(outer))          # cùng lỗi ra từ đường gọi model thì đúng
    assert chat.model_timed_out(httpx.ReadTimeout("model im lặng"))   # model tiêm thẳng ném lỗi nhà cung cấp
    assert not chat.model_timed_out(TimeoutError())
    assert not chat.model_timed_out(ValueError("khác"))


async def test_model_timeout_mid_turn_says_the_model_is_slow(who: Who) -> None:
    sale1 = await who("sale1")
    events: list[dict[str, Any]] = []

    async def emit(ev: dict[str, Any]) -> None:
        events.append(ev)

    out = await chat.chat_turn(sale1, "Khách của tôi", emit=emit, model=Slow([[call("crm.leads")], httpx.ReadTimeout("model im lặng quá lâu")]))
    assert out["text"] == chat.SLOW_MODEL
    assert not any(e["type"] == "error" for e in events)
