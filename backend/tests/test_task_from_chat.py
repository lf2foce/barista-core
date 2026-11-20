"""Tạo việc từ một tin nhắn (0115 + 0117, ghi chú 07/11): ``talk`` mở điểm cắm theo tin (``talk.message_actions``), ``flow`` góp nút "Tạo việc" (chỉ
người tạo được việc) và dấu "Đã tạo việc" (chỉ người thấy việc). Việc mang đối tượng nghiệp vụ của luồng (tin riêng ⇒ không có), dẫn về tin gốc,
không gắn bộ kiểm; người nhận phải đọc được trao đổi; trợ lý chỉ đề xuất trong chat riêng, không tạo, không gửi vào luồng chung."""

import dataclasses
import json
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from barista.agent import chat
from barista.http_api import app
from barista.kernel import executor, registry
from barista.kernel.forms import field_specs
from barista.kernel.principal import Grant, issued
from tests.conftest import Who, ok, owner_val, q
from tests.test_assistant import Scripted, call
from tests.test_resume import Log
from tests.test_talk import _lead, _subj, _thread


def _msg(page: dict[str, Any], mid: int) -> dict[str, Any]:
    return next(m for m in _thread(page)["messages"] if m["id"] == mid)


async def _task(source_ref: Any) -> dict[str, Any] | None:
    return await owner_val("SELECT row_to_json(t) FROM flow.tasks t WHERE source_kind = 'talk.message' AND source_ref = %s", (str(source_ref),))


async def test_a_message_becomes_a_task_about_the_threads_subject_that_links_back(who: Who) -> None:
    sale1, sale2, truonga = await who("sale1"), await who("sale2"), await who("truonga")
    lead = await _lead(sale1)
    mid = (await ok(sale1, "talk.post", **_subj(lead), body="Anh Nam   gọi lại khách\nchốt lịch gặp thứ Sáu nhé"))["message_id"]
    act = _msg(await q(truonga, "talk.thread", **_subj(lead)), mid)["actions"][0]
    assert (act["command"], act["preset"], act["values"]) == ("flow.task_from_message", {"message_id": mid},
                                                              {"title": "Anh Nam gọi lại khách chốt lịch gặp thứ Sáu nhé"})
    specs = {f["key"]: f for f in field_specs(registry.OPS["flow.task_from_message"])}
    assert specs["assignee"]["options_with"] == ["message_id"] and not {"done_check", "done_args"} & set(specs)   # không ô bộ kiểm
    # danh sách "Giao cho": trong phạm vi giao việc của người giao VÀ đọc được trao đổi (sale2 cùng đội nhưng không thấy khách này)
    people = {o["value"] for o in await q(truonga, "flow.message_assignee_options", message_id=mid)}
    assert sale1.email in people and truonga.email in people and sale2.email not in people
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:   # app gửi kèm mã tin qua query string
        r = await c.get(f"/v1/options/flow.message_assignee_options?message_id={mid}",
                        headers={"X-Dev-User": truonga.email, "X-Tenant-Id": str(truonga.tenant_id)})
    assert {o["value"] for o in r.json()["output"]} == people
    r = await executor.run(truonga, "flow.task_from_message", {"message_id": mid, "title": "Gọi lại khách", "assignee": sale2.email})
    assert r.status == "invalid" and "không xem được trao đổi" in (r.error or "")               # lệnh kiểm lại, không tin danh sách
    out = await ok(truonga, "flow.task_from_message", message_id=mid, title="Gọi lại khách chốt lịch", assignee=sale1.email)
    t = await _task(mid)
    assert t is not None and (t["id"], t["subject_kind"], t["subject_id"], t["assignee_id"], t["done_check"], t["origin"]) == (
        str(out["task_id"]), "crm.lead", lead, str(sale1.user_id), None, "manual")
    mark = _msg(await q(sale1, "talk.thread", **_subj(lead)), mid)["marks"][0]
    assert mark["title"] == "Đã tạo việc" and mark["open"] == {"query": "flow.task", "input": {"task_id": str(out["task_id"])}}
    card = await q(sale1, "flow.task", task_id=out["task_id"])
    src = next(o for o in card["open"] if o["title"].startswith("Tin nhắn gốc"))
    assert src["input"] == {**_subj(lead), "around": mid}                                         # việc dẫn về đúng tin
    assert any(o["title"].startswith("Khách / lead") for o in card["open"])


async def test_the_marker_only_shows_to_people_who_can_see_the_task(who: Who) -> None:
    sale1, truonga, giamdoc = await who("sale1"), await who("truonga"), await who("giamdoc")
    lead = await _lead(sale1)
    mid = (await ok(sale1, "talk.post", **_subj(lead), body="Cần chuẩn bị bảng tính cho buổi gặp"))["message_id"]
    await ok(truonga, "flow.task_from_message", message_id=mid, title="Chuẩn bị bảng tính")    # trưởng nhóm tự nhận (trống = chính mình)
    assert _msg(await q(truonga, "talk.thread", **_subj(lead)), mid)["marks"]
    assert _msg(await q(giamdoc, "talk.thread", **_subj(lead)), mid)["marks"]
    hidden = _msg(await q(sale1, "talk.thread", **_subj(lead)), mid)                              # sale1 đọc luồng nhưng không thấy việc của trưởng nhóm
    assert hidden["marks"] == [] and hidden["actions"]                                            # không lộ là có việc; vẫn tự tạo việc được


async def test_only_people_who_can_create_tasks_get_the_button(who: Who) -> None:
    sale1 = await who("sale1")
    lead = await _lead(sale1)
    mid = (await ok(sale1, "talk.post", **_subj(lead), body="Nhớ gửi tài liệu cho khách"))["message_id"]
    reader = issued(dataclasses.replace(sale1, grants={**sale1.grants, "flow": Grant("own", frozenset({"read"}))}))   # như auth.load cấp
    m = _msg(await q(reader, "talk.thread", **_subj(lead)), mid)
    assert m["actions"] == [] and m["body"] == "Nhớ gửi tài liệu cho khách"
    assert (await executor.run(reader, "flow.task_from_message", {"message_id": mid, "title": "x"})).status == "denied"
    await ok(sale1, "talk.recall_message", message_id=mid)
    assert _msg(await q(sale1, "talk.thread", **_subj(lead)), mid)["actions"] == []               # tin đã thu hồi: không có nút
    r = await executor.run(sale1, "flow.task_from_message", {"message_id": mid, "title": "x"})
    assert r.status == "invalid" and "thu hồi" in (r.error or "")


async def test_a_direct_message_task_has_no_subject_and_the_assignee_must_be_in_the_conversation(who: Who) -> None:
    sale1, sale2, truonga = await who("sale1"), await who("sale2"), await who("truonga")
    d = await ok(sale1, "talk.start_direct", person=truonga.email, body="Anh gửi em báo giá trước 5 giờ nhé")
    subj = {"subject_kind": "talk.direct", "subject_id": str(d["subject_id"])}
    mid = d["message_id"]
    act = _msg(await q(truonga, "talk.thread", **subj), mid)["actions"][0]
    assert "values" not in act                                                                     # tin riêng: không điền sẵn chữ tin
    r = await executor.run(truonga, "flow.task_from_message", {"message_id": mid, "title": "Gửi báo giá", "assignee": sale2.email})
    assert r.status == "invalid" and "không xem được" in (r.error or "")                         # người ngoài cuộc trò chuyện riêng
    await ok(truonga, "flow.task_from_message", message_id=mid, title="Gửi báo giá cho Hà")
    t = await _task(mid)
    assert t is not None and (t["subject_kind"], t["subject_id"], t["case_id"], t["plan_id"], t["done_check"]) == (None, None, None, None, None)
    assert _msg(await q(truonga, "talk.thread", **subj), mid)["marks"][0]["title"] == "Đã tạo việc"


async def test_the_assistant_only_proposes_in_the_askers_own_chat(who: Who) -> None:
    sale1, truonga = await who("sale1"), await who("truonga")
    lead = await _lead(sale1)
    mid = (await ok(sale1, "talk.post", **_subj(lead), body="Ai đặt phòng họp thứ Hai giúp em"))["message_id"]
    before = await owner_val("SELECT count(*) FROM talk.messages m JOIN talk.threads t ON t.id = m.thread_id WHERE t.subject_id = %s", (lead,))
    r = await executor.run(truonga.as_agent(), "flow.task_from_message", {"message_id": mid, "title": "Đặt phòng họp thứ Hai"})
    assert r.status == "proposed" and r.output["input"]["message_id"] == mid                   # thẻ đề xuất đã chạy thử, chưa tạo gì
    log = Log()
    await chat.chat_turn(truonga, f"Tạo việc từ tin nhắn {mid} trong trao đổi này", emit=log,
                         model=Scripted([[call("flow.task_from_message", message_id=mid, title="Đặt phòng họp thứ Hai")], "Anh xem thẻ đề xuất."]))
    assert [p["command"] for p in log.proposals()] == ["flow.task_from_message"]                 # chỉ người hỏi thấy, trong chat riêng
    assert await _task(mid) is None                                                               # không tự tạo
    assert await owner_val("SELECT count(*) FROM talk.messages m JOIN talk.threads t ON t.id = m.thread_id WHERE t.subject_id = %s",
                           (lead,)) == before                                                     # không gửi gì vào luồng chung
    assert registry.OPS["flow.task_from_message"].agent == "propose"


async def test_without_a_plugged_pack_the_thread_has_no_message_actions(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """``flow`` là gói nền (luôn bật) ⇒ mô phỏng không có gói nào cắm vào điểm: tin không có nút / dấu, luồng vẫn mở như cũ."""
    sale1 = await who("sale1")
    lead = await _lead(sale1)
    mid = (await ok(sale1, "talk.post", **_subj(lead), body=f"Tin thử {uuid.uuid4().hex[:4]}"))["message_id"]
    await ok(sale1, "flow.task_from_message", message_id=mid, title="Việc thử")
    monkeypatch.setitem(registry.PLUGS, "talk.message_actions", [])
    m = _msg(await q(sale1, "talk.thread", **_subj(lead)), mid)
    assert (m["actions"], m["marks"]) == ([], [])


async def test_direct_message_text_never_reaches_people_who_see_the_task_but_not_the_conversation(who: Who) -> None:
    """Review 07/11: tiêu đề việc ai thấy việc cũng đọc (đội của người nhận, giám đốc) ⇒ chữ tin riêng không được thành tiêu đề việc."""
    sale1, sale2, truonga, giamdoc = await who("sale1"), await who("sale2"), await who("truonga"), await who("giamdoc")
    secret = f"Chuyện riêng {uuid.uuid4().hex[:6]}: em xin nghỉ chiều nay"
    d = await ok(sale1, "talk.start_direct", person=sale2.email, body=secret)
    subj, mid = {"subject_kind": "talk.direct", "subject_id": str(d["subject_id"])}, d["message_id"]
    assert "values" not in _msg(await q(sale2, "talk.thread", **subj), mid)["actions"][0]
    r = await executor.run(sale2, "flow.task_from_message", {"message_id": mid, "title": secret})          # chép nguyên tin ⇒ từ chối
    assert r.status == "invalid" and "tiêu đề riêng" in (r.error or "")
    r = await executor.run(sale2.as_agent(), "flow.task_from_message", {"message_id": mid, "title": secret})   # trợ lý chép hộ cũng vậy
    assert r.status == "invalid"
    out = await ok(sale2, "flow.task_from_message", message_id=mid, title="Sắp lịch trực thay chiều nay")
    for viewer in (truonga, giamdoc):
        assert (await executor.query(viewer, "talk.thread", subj)).status == "not_found"                 # không đọc được cuộc trò chuyện
        card = await q(viewer, "flow.task", task_id=out["task_id"])                                        # thấy việc …
        assert "nghỉ chiều nay" not in json.dumps(card, ensure_ascii=False, default=str)                  # … mà không thấy chữ tin
    assert not await owner_val("SELECT count(*) FROM flow.tasks WHERE title LIKE %s OR notes LIKE %s", (f"%{secret[:20]}%", f"%{secret[:20]}%"))


async def test_reassigning_a_task_from_a_message_rechecks_the_new_assignee_can_read_the_thread(who: Who) -> None:
    """Review 07/11: người nhận chỉ được kiểm lúc tạo ⇒ giao lại cho người không đọc được trao đổi là cửa sau. Giao lại kiểm cùng luật."""
    sale1, sale2, truonga = await who("sale1"), await who("sale2"), await who("truonga")
    lead = await _lead(sale1)
    mid = (await ok(sale1, "talk.post", **_subj(lead), body="Chuẩn bị hồ sơ vay cho khách"))["message_id"]
    out = await ok(truonga, "flow.task_from_message", message_id=mid, title="Chuẩn bị hồ sơ vay", assignee=sale1.email)
    r = await executor.run(truonga, "flow.adjust_task", {"task_id": str(out["task_id"]), "assignee_email": sale2.email})
    assert r.status == "invalid" and "không xem được" in (r.error or "")
    assert (await _task(mid) or {})["assignee_id"] == str(sale1.user_id)
    await ok(truonga, "flow.adjust_task", task_id=out["task_id"], assignee_email=truonga.email)   # người đọc được ⇒ giao lại bình thường
    plain = await ok(truonga, "flow.create_task", title="Việc không từ tin", assignee_email=sale1.email)
    await ok(truonga, "flow.adjust_task", task_id=plain["task_id"], assignee_email=sale2.email)  # việc thường: không ràng buộc thêm


async def test_a_recalled_message_keeps_a_neutral_marker_without_the_task_title(who: Who) -> None:
    """Review 07/11: tiêu đề việc điền sẵn từ chữ tin ⇒ thu hồi tin rồi mà dấu vẫn hiện tiêu đề là lộ lại chữ gốc."""
    sale1, truonga = await who("sale1"), await who("truonga")
    lead = await _lead(sale1)
    body = f"Gửi lại bảng giá cũ {uuid.uuid4().hex[:4]}"
    mid = (await ok(sale1, "talk.post", **_subj(lead), body=body))["message_id"]
    out = await ok(sale1, "flow.task_from_message", message_id=mid, title=body)
    await ok(sale1, "talk.recall_message", message_id=mid)
    for viewer in (sale1, truonga):
        m = _msg(await q(viewer, "talk.thread", **_subj(lead)), mid)
        assert m["recalled"] and m["marks"] and body not in json.dumps(m, ensure_ascii=False)
        assert m["marks"][0]["open"] == {"query": "flow.task", "input": {"task_id": str(out["task_id"])}}   # vẫn dẫn tới việc
