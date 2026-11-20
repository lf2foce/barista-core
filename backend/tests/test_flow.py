"""Quy trình & việc (quyết định 0043): mẫu là DỮ LIỆU có phiên bản (hồ sơ đang mở giữ bản cũ), mục kiểm bằng bộ kiểm module ngành đăng ký,
chặng do code (trả lại một mục ⇒ lùi đúng chặng + việc bổ sung cho người phụ trách), một bảng việc chung theo đúng phạm vi quyền."""

import uuid
from datetime import timedelta
from typing import Any

from barista.db import db
from barista.kernel import executor
from barista.kernel.clock import today
from tests.conftest import Who, ok, owner_val, q
from tests.test_journey import _blocks

FREE = """# Chuẩn bị | Trưởng nhóm
- Có danh sách khách mời
- Đặt phòng họp (tuỳ chọn)
> Gửi lời mời @KD-A | 2 ngày
# Chốt | Giám đốc
- Giám đốc duyệt ngân sách
> Báo cáo kết quả | 1 ngày"""


def _key() -> str:
    return f"qt_{uuid.uuid4().hex[:8]}"


def _items(page: dict[str, Any]) -> list[str]:
    return [i["label"] for st in _blocks(page, "stages")[0]["stages"] for i in st["items"]]


def _board(page: dict[str, Any]) -> dict[str, list[str]]:
    return {c["key"]: [i["title"] for i in c["items"]] for c in _blocks(page, "board")[0]["columns"]}


async def test_assistant_drafts_a_template_as_a_proposal_with_a_readable_summary(who: Who) -> None:
    admin = await who("admin")
    key = _key()
    r = await executor.run(admin.as_agent(), "flow.set_template", {"key": key, "name": "Sự kiện", "source": FREE})
    assert r.status == "proposed" and "Chuẩn bị → Chốt" in r.output["preview"]["summary"]   # thẻ đề xuất hiện tóm tắt này
    assert await owner_val("SELECT count(*) FROM flow.templates WHERE key = %s", (key,)) == 0   # chưa ai bấm ⇒ chưa có gì


async def test_editing_a_template_adds_a_version_and_open_cases_keep_theirs(who: Who) -> None:
    admin, truonga = await who("admin"), await who("truonga")
    key = _key()
    await ok(admin, "flow.set_template", key=key, name="Sự kiện", source=FREE)
    c1 = (await ok(truonga, "flow.open_case", key=key, title="Mở bán đợt 1"))["case_id"]
    v2 = await ok(admin, "flow.set_template", key=key, name="Sự kiện", source=FREE.replace("- Giám đốc duyệt ngân sách",
                                                                                         "- Giám đốc duyệt ngân sách\n- Kế toán tạm ứng"))
    assert v2["version"] == 2
    c2 = (await ok(truonga, "flow.open_case", key=key, title="Mở bán đợt 2"))["case_id"]
    assert "Kế toán tạm ứng" not in _items(await q(truonga, "flow.case", case_id=c1))
    assert "Kế toán tạm ứng" in _items(await q(truonga, "flow.case", case_id=c2))
    assert await owner_val("SELECT count(*) FROM flow.templates WHERE key = %s AND is_active", (key,)) == 1
    page = await q(admin, "flow.template", key=key)
    assert page["title"].endswith("v2") and page["actions"][0]["preset"]["source"].count("#") == 2   # sửa = mở form có sẵn bản chữ
    assert (await q(truonga, "flow.template", key=key))["actions"] == []           # xem được, không sửa được ⇒ không hiện nút
    board = await q(await who("mkt"), "flow.board", key=key)
    assert board["open"] == []                                                      # không có quyền xem mẫu ⇒ không có nút mở mẫu


async def test_tasks_are_scoped_by_rights_and_status_rules_hold(who: Who) -> None:
    sale1, sale3, truonga = await who("sale1"), await who("sale3"), await who("truonga")
    mine = await ok(sale3, "flow.create_task", title="Việc riêng của Khánh", due_on=today().isoformat())
    await owner_val("UPDATE flow.tasks SET due_on = %s WHERE id = %s RETURNING 1", (today() - timedelta(days=1), mine["task_id"]))   # lệnh không nhận hạn đã qua
    r = await executor.run(sale1, "flow.set_task", {"task_id": str(mine["task_id"]), "status": "done"})
    assert r.status == "not_found"                                                  # RLS: việc người khác không thấy, không đổi được
    r = await executor.run(sale3, "flow.create_task", {"title": "Giao cho An", "assignee_email": sale1.email})
    assert r.status == "denied"                                                     # phạm vi "của mình": không giao việc cho người khác
    r = await executor.run(sale3, "flow.set_task", {"task_id": str(mine["task_id"]), "status": "blocked"})
    assert r.status == "invalid"                                                    # kẹt phải có lý do
    page = await q(sale3, "flow.tasks")
    kpis = {k["label"]: k["value"] for k in _blocks(page, "kpis")[0]["items"]}
    assert kpis["Quá hạn"] >= 1 and "Việc riêng của Khánh" in _board(page)["todo"]
    move = _blocks(page, "board")[0]["move"]
    assert move["command"] == "flow.set_task" and move["form_columns"] == ["blocked"]   # kéo sang "Kẹt" ⇒ app mở form hỏi lý do
    await ok(sale3, "flow.set_task", task_id=mine["task_id"], status="done")
    assert await owner_val("SELECT done_at IS NOT NULL FROM flow.tasks WHERE id = %s", (mine["task_id"],))
    await ok(sale3, "flow.set_task", task_id=mine["task_id"], status="doing")      # mở lại: bỏ dấu xong
    assert await owner_val("SELECT done_at FROM flow.tasks WHERE id = %s", (mine["task_id"],)) is None

    team = await ok(truonga, "flow.create_task", title="Trực quầy cuối tuần", team_code="KD-A")
    assert await owner_val("SELECT assignee_id FROM flow.tasks WHERE id = %s", (team["task_id"],)) is None
    assert "Trực quầy cuối tuần" in _board(await q(truonga, "flow.tasks"))["todo"]   # trưởng nhóm thấy việc đội chưa ai nhận
    await ok(truonga, "flow.set_task", task_id=team["task_id"], status="doing", take=True)
    assert await owner_val("SELECT assignee_id FROM flow.tasks WHERE id = %s", (team["task_id"],)) == truonga.user_id
    p = await executor.run(sale3.as_agent(), "flow.create_task", {"title": "Trợ lý giao"})
    assert p.status == "proposed"                                                   # trợ lý soạn việc, người bấm mới thành


# ── Review 31/10 (Codex): tái hiện trước, sửa sau ──

APPROVAL = """# Chuẩn bị | Trưởng nhóm
- Có danh sách khách mời
# Chốt | Giám đốc [xác nhận: director]
- Giám đốc duyệt ngân sách
- Kế toán tạm ứng [xác nhận: @KT]"""


async def test_confirmers_declared_in_the_template_are_enforced_by_the_server(who: Who) -> None:
    admin, truonga, giamdoc, ketoan = await who("admin"), await who("truonga"), await who("giamdoc"), await who("ketoan")
    key = _key()
    r = await executor.run(admin, "flow.set_template", {"key": key, "name": "X", "source": APPROVAL.replace("director", "khong_co_vai")})
    assert r.status == "invalid" and "khong_co_vai" in r.error
    await ok(admin, "flow.set_template", key=key, name="Sự kiện có duyệt", source=APPROVAL)
    cid = (await ok(truonga, "flow.open_case", key=key, title="Sự kiện cần duyệt"))["case_id"]
    await ok(truonga, "flow.mark_item", case_id=cid, item_key="co_danh_sach_khach_moi", status="verified")
    r = await executor.run(truonga, "flow.mark_item", {"case_id": str(cid), "item_key": "giam_doc_duyet_ngan_sach", "status": "verified"})
    assert r.status == "denied" and "Giám đốc" in r.error                         # người mở hồ sơ không tự duyệt thay giám đốc
    page = await q(truonga, "flow.case", case_id=cid)
    assert not any("Giám đốc duyệt" in a["title"] for a in page["actions"])          # không hiện nút người xem không bấm được
    r = await executor.run(truonga, "flow.mark_item", {"case_id": str(cid), "item_key": "ke_toan_tam_ung", "status": "verified"})
    assert r.status == "denied"                                                     # mục của đội KT: trưởng nhóm kinh doanh không thay
    await ok(giamdoc, "flow.mark_item", case_id=cid, item_key="giam_doc_duyet_ngan_sach", status="verified")
    await ok(ketoan, "flow.mark_item", case_id=cid, item_key="ke_toan_tam_ung", status="verified")
    assert await owner_val("SELECT status FROM flow.cases WHERE id = %s", (cid,)) == "done"


async def test_concurrent_confirm_and_reject_cannot_leave_a_done_case_with_a_rejected_item(who: Who) -> None:
    import asyncio

    admin, truonga = await who("admin"), await who("truonga")
    key = _key()
    await ok(admin, "flow.set_template", key=key, name="Hai chặng", source="# Một\n- Mục đầu\n# Hai\n- Mục cuối")
    cid = (await ok(truonga, "flow.open_case", key=key, title="Đua nhau"))["case_id"]
    await ok(truonga, "flow.mark_item", case_id=cid, item_key="muc_dau", status="verified")
    async with db.owner_tx() as conn:   # giữ khoá hồ sơ để hai lệnh chắc chắn chồng lên nhau (lệnh xác nhận vào hàng trước)
        await conn.execute("SELECT 1 FROM flow.cases WHERE id = %s FOR UPDATE", (cid,))
        last = asyncio.create_task(executor.run(truonga, "flow.mark_item", {"case_id": str(cid), "item_key": "muc_cuoi", "status": "verified"}))
        await asyncio.sleep(0.3)
        back = asyncio.create_task(executor.run(admin, "flow.mark_item", {"case_id": str(cid), "item_key": "muc_dau", "status": "rejected",
                                                                          "note": "Sai"}))
        await asyncio.sleep(0.3)
    results = await asyncio.gather(last, back)
    assert all(r.status in ("ok", "invalid") for r in results), [(r.status, r.error) for r in results]   # không khoá chéo / lỗi hệ thống
    status = await owner_val("SELECT status FROM flow.cases WHERE id = %s", (cid,))
    rejected = await owner_val("SELECT count(*) FROM flow.case_items WHERE case_id = %s AND status = 'rejected'", (cid,))
    assert not (status == "done" and rejected), "hồ sơ 'xong' mà vẫn có mục bị trả lại"


async def test_team_members_see_and_take_unassigned_team_tasks(who: Who) -> None:
    truonga, sale1, sale3 = await who("truonga"), await who("sale1"), await who("sale3")
    t = await ok(truonga, "flow.create_task", title=f"Trực quầy {uuid.uuid4().hex[:4]}", team_code="KD-A")
    title = await owner_val("SELECT title FROM flow.tasks WHERE id = %s", (t["task_id"],))
    assert title in _board(await q(sale1, "flow.tasks"))["todo"]                   # sale đội KD-A thấy việc chung của đội
    assert title not in _board(await q(sale3, "flow.tasks"))["todo"]               # đội khác không thấy
    r = await executor.run(sale3, "flow.set_task", {"task_id": str(t["task_id"]), "status": "doing", "take": True})
    assert r.status == "not_found"
    r = await executor.run(sale1, "flow.set_task", {"task_id": str(t["task_id"]), "status": "blocked", "blocked_reason": "x"})
    assert r.status == "ok" and await owner_val("SELECT assignee_id FROM flow.tasks WHERE id = %s", (t["task_id"],)) == sale1.user_id
    t2 = await ok(truonga, "flow.create_task", title="Việc chung khác", team_code="KD-A")
    r = await executor.run(sale1, "flow.set_task", {"task_id": str(t2["task_id"]), "status": "cancelled"})
    assert r.status == "denied"                                                     # chưa nhận thì không huỷ / sửa việc chung được


async def test_board_keeps_cases_of_an_older_version_after_a_stage_is_renamed(who: Who) -> None:
    admin, truonga = await who("admin"), await who("truonga")
    key = _key()
    await ok(admin, "flow.set_template", key=key, name="Đổi tên", source=FREE)
    await ok(truonga, "flow.open_case", key=key, title="Hồ sơ bản cũ")
    await ok(admin, "flow.set_template", key=key, name="Đổi tên", source=FREE.replace("# Chuẩn bị |", "# Chuẩn bị kỹ |"))
    board = _board(await q(truonga, "flow.board", key=key))
    assert any("Hồ sơ bản cũ" in t for col in board.values() for t in col)


async def _free_rows(sql: Any, params: Any = None) -> list[dict[str, Any]]:
    async with db.owner.connection() as conn:
        return await (await conn.execute(sql, params)).fetchall()


async def test_free_case_moves_by_items_spawns_stage_tasks_and_closes(who: Who) -> None:
    admin, truonga, giamdoc = await who("admin"), await who("truonga"), await who("giamdoc")
    key = _key()
    await ok(admin, "flow.set_template", key=key, name="Sự kiện", source=FREE)
    cid = (await ok(truonga, "flow.open_case", key=key, title="Mở bán Hồ Sen"))["case_id"]
    tasks = await _free_rows("SELECT title, assignee_id, team_id, due_on FROM flow.tasks WHERE case_id = %s", (cid,))
    kd_a = await owner_val("SELECT id FROM core.teams WHERE code = 'KD-A' AND tenant_id = %s", (truonga.tenant_id,))
    assert [(t["title"], t["assignee_id"], t["team_id"]) for t in tasks] == [("Gửi lời mời", None, kd_a)]   # việc của đội, chưa ai nhận
    assert tasks[0]["due_on"] == today() + timedelta(days=2)

    await ok(truonga, "flow.mark_item", case_id=cid, item_key="co_danh_sach_khach_moi", status="verified")   # mục tuỳ chọn không chặn
    assert await owner_val("SELECT stage_key FROM flow.cases WHERE id = %s", (cid,)) == "chot"
    spawned = await _free_rows("SELECT assignee_id FROM flow.tasks WHERE case_id = %s AND title = 'Báo cáo kết quả'", (cid,))
    assert [t["assignee_id"] for t in spawned] == [truonga.user_id]                 # không ghi đội ⇒ người phụ trách hồ sơ

    await ok(giamdoc, "flow.mark_item", case_id=cid, item_key="giam_doc_duyet_ngan_sach", status="verified")
    assert await owner_val("SELECT status FROM flow.cases WHERE id = %s", (cid,)) == "done"
    r = await executor.run(giamdoc, "flow.mark_item", {"case_id": str(cid), "item_key": "giam_doc_duyet_ngan_sach", "status": "rejected", "note": "x"})
    assert r.status == "invalid"                                                    # hồ sơ đã đóng
    kinds = [r["kind"] for r in await _free_rows("SELECT kind FROM flow.case_events WHERE case_id = %s ORDER BY at, id", (cid,))]
    assert kinds == ["opened", "stage", "item", "stage", "item", "closed"]          # nhật ký chỉ thêm
