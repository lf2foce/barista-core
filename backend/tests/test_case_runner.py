"""Trợ lý cầm trọn một hồ sơ nhiều ngày (quyết định 0142): mẫu quy trình có ``follow`` ⇒ hồ sơ mở là một việc theo dõi loại ``case`` của người phụ
trách; tình hình do code tính bằng quyền người đó; kẹt chặng / việc quá hạn ⇒ ngoại lệ (lời báo mẫu hoặc trợ lý với bộ công cụ hẹp); thẻ đề xuất
trợ lý soạn vào ``core.proposals`` — người bấm mới chạy; đổi người phụ trách ⇒ bàn giao; hồ sơ đủ chặng ⇒ việc tự đóng."""

import uuid
from datetime import timedelta
from typing import Any
from uuid import UUID

from pydantic_ai import ToolCallPart

from barista.agent import morning
from barista.kernel import executor
from barista.kernel.clock import today
from barista.kernel.executor import core_ctx
from barista.modules import work
from tests.conftest import Who, ok, owner_val, q
from tests.test_assistant import Scripted

GUIDED = """# Chuẩn bị | Trưởng nhóm | tối đa 3 ngày, báo @KT
- Có danh sách khách mời
- Đặt phòng họp (tuỳ chọn)
> Gửi lời mời | 1 ngày
~ Thiếu danh sách khách mời thì nhắc người phụ trách; quá 5 ngày thì đề xuất báo trưởng nhóm.
# Chốt | Giám đốc
- Giám đốc duyệt ngân sách"""


def _key() -> str:
    return f"cr_{uuid.uuid4().hex[:8]}"


async def _item(cid: Any) -> dict[str, Any] | None:
    async def one() -> Any:
        return await owner_val("SELECT to_jsonb(i) FROM work.items i WHERE kind = 'case' AND subject_ref = %s AND status IN ('active', 'waiting')", (str(cid),))
    return await one()


async def test_guide_lines_are_part_of_the_template_text(who: Who) -> None:
    admin = await who("admin")
    key = _key()
    out = await ok(admin, "flow.set_template", key=key, name="Có lời dặn", source=GUIDED, follow=True)
    assert "lời dặn trợ lý ở 1 chặng" in out["summary"]
    for src, msg in [("~ dặn trước chặng\n# A | X\n- Y", "cần một chặng"), ("# A | X\n- Y\n~", "cần nội dung"), ("# A | X\n- Y\n? gì đây", "~ (lời dặn")]:
        r = await executor.run(admin, "flow.set_template", {"key": _key(), "name": "Thử", "source": src})
        assert r.status == "invalid" and msg in r.error, (src, r.error)
    page = await q(admin, "flow.template", key=key)
    assert any(b.get("title") == "Lời dặn trợ lý theo chặng" and "nhắc người phụ trách" in b["markdown"] for b in page["blocks"])
    assert "Trợ lý của người phụ trách cầm hồ sơ" in page["blocks"][0]["markdown"]


async def test_follow_template_opens_a_work_item_for_the_case_owner_and_plain_template_does_not(who: Who) -> None:
    admin, truonga = await who("admin"), await who("truonga")
    key_f, key_p = _key(), _key()
    await ok(admin, "flow.set_template", key=key_f, name="Theo dõi", source=GUIDED, follow=True)
    await ok(admin, "flow.set_template", key=key_p, name="Không theo dõi", source=GUIDED)
    c_f = (await ok(truonga, "flow.open_case", key=key_f, title=f"Mở bán có trợ lý {uuid.uuid4().hex[:4]}"))["case_id"]
    c_p = (await ok(truonga, "flow.open_case", key=key_p, title=f"Mở bán thường {uuid.uuid4().hex[:4]}"))["case_id"]
    item = await _item(c_f)
    assert item is not None and item["owner_id"] == str(truonga.user_id) and item["title"].startswith("Hồ sơ: Mở bán có trợ lý")
    assert await _item(c_p) is None
    assert (await owner_val("SELECT text FROM work.events WHERE item_id = %s AND kind = 'created'", (item["id"],))).startswith("Hồ sơ mở theo quy trình")
    # người phụ trách thấy việc trong hộp của mình; sale khác không thấy (work phạm vi own)
    assert any(r["id"] == item["id"] for r in (await q(truonga, "work.inbox"))["rows"])
    assert all(r["id"] != item["id"] for r in (await q(await who("sale3"), "work.inbox", status="all"))["rows"])


async def test_step_tracks_the_case_by_code_and_closes_when_the_case_is_done(who: Who, demo: dict[str, Any]) -> None:
    admin, truonga, giamdoc = await who("admin"), await who("truonga"), await who("giamdoc")
    key = _key()
    await ok(admin, "flow.set_template", key=key, name="Theo dõi", source=GUIDED, follow=True)
    cid = (await ok(truonga, "flow.open_case", key=key, title=f"Hồ sơ đi hết {uuid.uuid4().hex[:4]}"))["case_id"]
    item = await _item(cid)
    assert item is not None
    r = await work.step(demo["tenant"], UUID(item["id"]), use_model=False)
    assert r["status"] == "active" and not r["final"]
    facts = await owner_val("SELECT facts FROM work.items WHERE id = %s", (item["id"],))
    assert facts["stage_name"] == "Chuẩn bị" and facts["missing"] == ["Có danh sách khách mời"] and facts["late_days"] == 0 and facts["tasks_open"] == 1
    assert facts["due"] == (today() + timedelta(days=3)).isoformat()   # hạn chặng ⇒ hạn của việc theo dõi

    await ok(truonga, "flow.mark_item", case_id=cid, item_key="co_danh_sach_khach_moi", status="verified", note="đã có")
    assert await owner_val("SELECT count(*) FROM work.events WHERE item_id = %s AND kind = 'woke'", (item["id"],)) == 1   # sang chặng ⇒ đánh thức
    r = await work.step(demo["tenant"], UUID(item["id"]), use_model=False)
    assert (await owner_val("SELECT facts->>'stage_name' FROM work.items WHERE id = %s", (item["id"],))) == "Chốt"
    await ok(giamdoc, "flow.mark_item", case_id=cid, item_key="giam_doc_duyet_ngan_sach", status="verified", note="duyệt")
    assert await owner_val("SELECT status FROM flow.cases WHERE id = %s", (cid,)) == "done"
    r = await work.step(demo["tenant"], UUID(item["id"]), use_model=False)
    assert r == {"final": True, "status": "done", "sleep_s": 0}
    assert "đủ mọi chặng" in await owner_val("SELECT last_note FROM work.items WHERE id = %s", (item["id"],))


async def test_stuck_case_raises_an_exception_with_a_template_note_and_the_owner_sees_it(who: Who, demo: dict[str, Any]) -> None:
    admin, truonga = await who("admin"), await who("truonga")
    key = _key()
    await ok(admin, "flow.set_template", key=key, name="Theo dõi", source=GUIDED, follow=True)
    cid = (await ok(truonga, "flow.open_case", key=key, title=f"Hồ sơ kẹt {uuid.uuid4().hex[:4]}"))["case_id"]
    item = await _item(cid)
    assert item is not None
    await owner_val("UPDATE flow.cases SET stage_since = now() - interval '10 days' WHERE id = %s RETURNING 1", (cid,))
    r = await work.step(demo["tenant"], UUID(item["id"]), use_model=False)   # không model ⇒ lời báo mẫu
    assert r["status"] == "waiting"
    note = await owner_val("SELECT last_note FROM work.items WHERE id = %s", (item["id"],))
    assert "Có danh sách khách mời" in note and "ngày thứ 10" in note
    facts = await owner_val("SELECT facts FROM work.items WHERE id = %s", (item["id"],))
    assert facts["late_days"] == 7
    page = await q(truonga, "work.view", item_id=item["id"])
    assert {a["command"] for a in page["actions"]} == {"work.ack", "work.cancel"}
    # màn "Cần xử lý" của người xem gom luôn việc này
    card = await q(truonga, "flow.needs_action")
    items = next((b["items"] for b in card["blocks"] if b.get("type") == "list" and b["title"].startswith("Trợ lý đang chờ")), [])
    assert any(i["title"] == item["title"] for i in items)


async def test_assistant_proposals_from_the_item_land_in_the_owners_proposals_and_run_as_the_person(who: Who, demo: dict[str, Any]) -> None:
    admin, truonga = await who("admin"), await who("truonga")
    key = _key()
    await ok(admin, "flow.set_template", key=key, name="Theo dõi", source=GUIDED, follow=True)
    cid = (await ok(truonga, "flow.open_case", key=key, title=f"Hồ sơ trợ lý {uuid.uuid4().hex[:4]}"))["case_id"]
    item = await _item(cid)
    assert item is not None
    task_id = await owner_val("SELECT id FROM flow.tasks WHERE case_id = %s AND origin = 'stage'", (cid,))
    await owner_val("UPDATE flow.cases SET stage_since = now() - interval '10 days' WHERE id = %s RETURNING 1", (cid,))
    await owner_val("DELETE FROM core.proposals WHERE user_id = %s RETURNING 1", (truonga.user_id,))
    new_due = (today() + timedelta(days=2)).isoformat()
    model = Scripted([
        [ToolCallPart("flow__case", {"case_id": str(cid)})],
        [ToolCallPart("flow__adjust_task", {"task_id": str(task_id), "due_on": new_due, "note": "dời hạn vì chờ danh sách"})],
        "Hồ sơ kẹt vì chưa có danh sách khách mời. Em đã soạn đề xuất dời hạn việc gửi lời mời.",
    ])
    r = await work.step(demo["tenant"], UUID(item["id"]), model=model)
    assert r["status"] == "waiting"
    assert model.tools_seen[0] <= {t.replace(".", "__") for t in ("flow.case", "flow.tasks", "flow.adjust_task", "flow.create_task", "talk.post",
                                                                   "talk.digest", "asks.create", "kb.search", "core.open_tools")}   # bộ công cụ hẹp
    # lệnh ghi của trợ lý KHÔNG chạy: hạn việc còn nguyên; thẻ đề xuất nằm ở core.proposals của người phụ trách
    assert str(await owner_val("SELECT due_on FROM flow.tasks WHERE id = %s", (task_id,))) != new_due
    data = await owner_val("SELECT data FROM work.events WHERE item_id = %s AND kind = 'exception'", (item["id"],))
    assert data["proposals"] == 1 and data["agent"] is True
    cards = await morning.cards(truonga)
    card = next(c for c in cards if c["command"] == "flow.adjust_task" and c["input"]["task_id"] == str(task_id))
    assert card["reason"].startswith("Việc theo dõi: Hồ sơ:") and card["fields"]
    page = await q(truonga, "work.view", item_id=item["id"])
    assert any("Đề xuất trợ lý đã soạn" in s["title"] for s in page["sections"])
    assert (await morning.decide(await who("sale3"), UUID(card["id"]), "confirm", None, "k1"))["status"] == "not_found"   # thẻ của người khác
    assert (await morning.decide(truonga, UUID(card["id"]), "confirm", None, "k1"))["status"] == "ok"
    assert str(await owner_val("SELECT due_on FROM flow.tasks WHERE id = %s", (task_id,))) == new_due
    assert await owner_val("SELECT via FROM core.audit_log WHERE command = 'flow.adjust_task' AND status = 'ok' ORDER BY id DESC LIMIT 1") == "human"
    inbox = await q(truonga, "work.inbox")
    assert {k["label"]: k["value"] for k in inbox["kpis"]}["Đề xuất đã soạn hôm nay"] >= 1


async def test_changing_the_case_owner_hands_the_item_over(who: Who, demo: dict[str, Any]) -> None:
    """Hồ sơ bám đối tượng (giao dịch): đổi sale phụ trách ⇒ việc của người cũ dừng, việc mới cho người mới — kiểm qua flow.sync."""
    admin, sale3, sale1 = await who("admin"), await who("sale3"), await who("sale1")
    key = _key()
    await ok(admin, "flow.set_template", key=key, name="Theo dõi bàn giao", source=GUIDED, follow=True)
    cid = (await ok(sale3, "flow.open_case", key=key, title=f"Hồ sơ bàn giao {uuid.uuid4().hex[:4]}"))["case_id"]
    old = await _item(cid)
    assert old is not None and old["owner_id"] == str(sale3.user_id)
    # đổi chủ hồ sơ thẳng ở bảng (như đối tượng đổi sale) rồi gọi đúng đường flow.sync ⇒ bàn giao
    from barista.db import db
    from barista.kernel.principal import system_principal
    sysp = system_principal(demo["tenant"])
    await owner_val("UPDATE flow.cases SET owner_id = %s WHERE id = %s RETURNING 1", (sale1.user_id, cid))
    async with db.tenant_tx(sysp) as conn:
        ctx = core_ctx(conn, sysp, module="flow")
        case = await ctx.one("SELECT * FROM flow.cases WHERE id = %s", (cid,))
        assert case is not None
        tpl = await ctx.one("SELECT * FROM flow.templates WHERE id = %s", (case["template_id"],))
        assert tpl is not None
        from barista.modules.flow import cases
        await cases._follow_case(ctx, {**case, "owner_id": sale1.user_id}, tpl, handoff=True)
    assert await owner_val("SELECT status FROM work.items WHERE id = %s", (old["id"],)) == "cancelled"
    new = await _item(cid)
    assert new is not None and new["owner_id"] == str(sale1.user_id) and new["id"] != old["id"]
    r = await work.step(demo["tenant"], UUID(new["id"]), use_model=False)   # người mới chạy bằng quyền của mình
    assert r["status"] in ("active", "waiting")


async def test_base_only_company_can_follow_a_case(who: Who) -> None:
    """Loại việc ``case`` thuộc gói nền ⇒ công ty không bật gói ngành nào vẫn giao được cho trợ lý."""
    opts = await q(await who("admin"), "work.kind_options")
    assert any(o["value"] == "case" for o in opts)


async def test_case_warns_early_before_the_stage_deadline_then_again_when_late(who: Who, demo: dict[str, Any]) -> None:
    """0142 lát 2: hạn chặng hôm nay / ngày mai mà còn thiếu ⇒ nhắc SỚM (lý do "attention"); nhắc sớm không chặn lời báo quá hạn mấy ngày sau."""
    admin, truonga = await who("admin"), await who("truonga")
    key = _key()
    await ok(admin, "flow.set_template", key=key, name="Theo dõi sớm", source=GUIDED, follow=True)
    cid = (await ok(truonga, "flow.open_case", key=key, title=f"Hồ sơ sắp hạn {uuid.uuid4().hex[:4]}"))["case_id"]
    item = await _item(cid)
    assert item is not None
    r = await work.step(demo["tenant"], UUID(item["id"]), use_model=False)
    assert r["status"] == "active"   # hạn chặng còn 3 ngày: chưa nhắc
    await owner_val("UPDATE flow.cases SET stage_since = now() - interval '2 days' WHERE id = %s RETURNING 1", (cid,))   # hạn: ngày mai
    r = await work.step(demo["tenant"], UUID(item["id"]), use_model=False)
    assert r["status"] == "waiting"
    ev = await owner_val("SELECT jsonb_build_object('text', text, 'reason', data->>'reason') FROM work.events WHERE item_id = %s AND kind = 'exception'",
                         (item["id"],))
    assert ev["reason"] == "attention" and ev["text"].startswith("Hồ sơ cần bạn xem sớm") and "Hạn chặng sắp tới" in ev["text"]
    await work.step(demo["tenant"], UUID(item["id"]), use_model=False)   # cùng ngày: không nhắc lại
    assert await owner_val("SELECT count(*) FROM work.events WHERE item_id = %s AND kind = 'exception'", (item["id"],)) == 1
    await owner_val("UPDATE flow.cases SET stage_since = now() - interval '8 days' WHERE id = %s RETURNING 1", (cid,))   # quá hạn 5 ngày
    await work.step(demo["tenant"], UUID(item["id"]), use_model=False)
    reasons = await owner_val("SELECT array_agg(data->>'reason' ORDER BY id) FROM work.events WHERE item_id = %s AND kind = 'exception'", (item["id"],))
    assert reasons == ["attention", "late"]


async def test_external_requests_of_the_case_count_toward_late_and_attention(who: Who, demo: dict[str, Any]) -> None:
    """Yêu cầu gửi bên ngoài (link) gắn vào hồ sơ: quá hạn nộp ⇒ tính vào quá hạn của hồ sơ (người phụ trách có quyền xem yêu cầu — giám đốc)."""
    admin, owner = await who("admin"), await who("giamdoc")
    key = _key()
    await ok(admin, "flow.set_template", key=key, name="Theo dõi có link", source="# Hồ sơ | Trưởng nhóm\n- Giấy tờ bên ngoài đủ", follow=True)
    cid = (await ok(owner, "flow.open_case", key=key, title=f"Hồ sơ có link {uuid.uuid4().hex[:4]}"))["case_id"]
    item = await _item(cid)
    assert item is not None
    out = await ok(owner, "asks.create", subject_kind="flow.case", subject_id=str(cid), title="Giấy tờ bổ sung", recipient="Khách thử",
                   items="- Bản chụp CCCD [tệp]\n- Địa chỉ nhận thư", due_on=(today() + timedelta(days=5)).isoformat())
    rid = out.get("request_id") or await owner_val("SELECT id FROM asks.requests WHERE subject_id = %s", (cid,))
    await owner_val("UPDATE asks.requests SET due_on = %s WHERE id = %s RETURNING 1", (today() - timedelta(days=6), rid))
    r = await work.step(demo["tenant"], UUID(item["id"]), use_model=False)
    facts = await owner_val("SELECT facts FROM work.items WHERE id = %s", (item["id"],))
    assert facts["asks_open"] == 1 and facts["asks_late"] == 1 and facts["late_days"] == 6 and r["status"] == "waiting"
    note = await owner_val("SELECT last_note FROM work.items WHERE id = %s", (item["id"],))
    assert "1 yêu cầu bên ngoài quá hạn nộp (lâu nhất 6 ngày)" in note and "Giấy tờ bổ sung" not in note and "Khách thử" not in note   # không lộ tên


async def test_early_warning_window_is_part_of_the_template(who: Who) -> None:
    """0142 lát 2 (review 09/11): “nhắc trước N ngày” nằm trong mẫu (dữ liệu), không phải hằng số; sai thì báo ngay khi lưu."""
    admin = await who("admin")
    src = "# Chuẩn bị | Trưởng nhóm | tối đa 10 ngày, báo @KT, nhắc trước 3 ngày\n- Có danh sách khách mời"
    out = await ok(admin, "flow.set_template", key=_key(), name="Nhắc trước", source=src)
    soon = await owner_val("SELECT stages->0->>'soon_days' FROM flow.templates WHERE id = %s", (out["template_id"],))
    assert soon == "3"
    r = await executor.run(admin, "flow.set_template", {"key": _key(), "name": "Sai", "source": "# A | X | tối đa 3 ngày, nhắc trước 3 ngày\n- Y"})
    assert r.status == "invalid" and "nhắc trước" in r.error
