"""Canh hạn theo quy trình + "Cần xử lý" (họp khách 31/10 — docs/clients/<khách>, H12, quyết định 0054): hạn chặng khai trong MẪU (dữ liệu, có phiên bản), hồ sơ
kẹt quá hạn ⇒ đúng một việc "hồ sơ kẹt" cho đội được báo; màn "Cần xử lý" gom hồ sơ kẹt + việc quá hạn / kẹt + việc trợ lý chờ người, theo
quyền người xem. Cuối file: kịch bản demo thu phí CĐT xuyên module (giao dịch → kế toán → giấy tờ → việc theo dõi → hoa hồng)."""

import uuid
from typing import Any

from barista.db import db
from barista.kernel import executor
from barista.kernel.principal import system_principal
from tests.conftest import Who, ok, owner_val, q


async def _rows(sql: Any, params: Any = None) -> list[dict[str, Any]]:
    async with db.owner.connection() as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()


TIMED = """# Chuẩn bị | Trưởng nhóm | tối đa 3 ngày, báo @KT
- Có danh sách khách mời
- Đặt phòng họp (tuỳ chọn)
# Chốt | Giám đốc
- Giám đốc duyệt ngân sách"""


def _key() -> str:
    return f"hn_{uuid.uuid4().hex[:8]}"


def _list(card: dict[str, Any], title: str) -> list[dict[str, Any]]:
    return next((b["items"] for b in card["blocks"] if b.get("type") == "list" and b["title"].startswith(title)), [])


def _kpi(card: dict[str, Any], label: str) -> Any:
    return next(k["value"] for b in card["blocks"] if b["type"] == "kpis" for k in b["items"] if k["label"] == label)


async def test_stage_deadline_is_part_of_the_template_text(who: Who) -> None:
    admin = await who("admin")
    out = await ok(admin, "flow.set_template", key=_key(), name="Có hạn", source=TIMED)
    assert "1 chặng có hạn" in out["summary"]
    for src, msg in [("# A | X | nhanh lên\n- Y", "tối đa 30 ngày"), ("# A | X | tối đa 0 ngày\n- Y", "ít nhất 1 ngày"),
                     ("# A | X | tối đa 3 ngày, báo @KHONGCO\n- Y", "Không có đội")]:
        r = await executor.run(admin, "flow.set_template", {"key": _key(), "name": "Thử", "source": src})
        assert r.status == "invalid" and msg in r.error, (src, r.error)


async def test_stuck_case_is_escalated_once_and_listed_for_those_who_see_it(who: Who, demo: dict[str, Any]) -> None:
    admin, truonga, giamdoc, sale3 = await who("admin"), await who("truonga"), await who("giamdoc"), await who("sale3")
    key = _key()
    await ok(admin, "flow.set_template", key=key, name="Sự kiện có hạn", source=TIMED)
    title = f"Mở bán {uuid.uuid4().hex[:5]}"
    cid = (await ok(truonga, "flow.open_case", key=key, title=title))["case_id"]
    sysp = system_principal(demo["tenant"])
    await ok(sysp, "flow.escalate_stuck")
    assert not await _rows("SELECT 1 FROM flow.tasks WHERE case_id = %s AND origin = 'overdue'", (cid,))   # mới mở: chưa kẹt
    await owner_val("UPDATE flow.cases SET stage_since = now() - interval '5 days' WHERE id = %s RETURNING 1", (cid,))

    await ok(sysp, "flow.escalate_stuck")
    await ok(sysp, "flow.escalate_stuck")                                              # chạy lại: không sinh trùng
    tasks = await _rows("SELECT title, notes, assignee_id, team_id FROM flow.tasks WHERE case_id = %s AND origin = 'overdue'", (cid,))
    kt = await owner_val("SELECT id FROM core.teams WHERE code = 'KT' AND tenant_id = %s", (demo["tenant"],))
    assert len(tasks) == 1 and tasks[0]["assignee_id"] is None and tasks[0]["team_id"] == kt   # việc của đội được báo
    assert "5 ngày (tối đa 3)" in tasks[0]["title"] and "Có danh sách khách mời" in tasks[0]["notes"]
    assert "Đặt phòng họp" not in tasks[0]["notes"]                                    # mục tuỳ chọn không phải "thiếu"
    assert await owner_val("SELECT count(*) FROM flow.case_events WHERE case_id = %s AND kind = 'overdue'", (cid,)) == 1

    card = await q(giamdoc, "flow.needs_action")
    assert _kpi(card, "Hồ sơ kẹt quá hạn chặng") >= 1
    row = next(i for i in _list(card, "Hồ sơ kẹt") if i["title"].startswith(title))
    assert row["badge"] == "5/3 ngày" and "Thiếu: Có danh sách khách mời" in row["subtitle"] and "Trưởng nhóm" in row["subtitle"]
    assert row["open"] == {"query": "flow.case", "input": {"case_id": str(cid)}}
    assert not any(i["title"].startswith(title) for i in _list(await q(sale3, "flow.needs_action"), "Hồ sơ kẹt"))   # ngoài phạm vi

    await ok(truonga, "flow.mark_item", case_id=cid, item_key="co_danh_sach_khach_moi", status="verified")   # sang chặng mới
    assert not any(i["title"].startswith(title) for i in _list(await q(giamdoc, "flow.needs_action"), "Hồ sơ kẹt"))
