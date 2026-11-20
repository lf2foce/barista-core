"""Hành trình khách, thẻ buổi sáng, hồ sơ giao dịch theo chặng (học từ barista_bds): mốc từ dữ liệu thật, xếp hạng có lý do,
mục tiền công ty ẩn số với người không có quyền Kế toán, chặng chuyển do code."""

import re
import uuid
from datetime import timedelta
from typing import Any

from barista import seed
from barista.db import db
from barista.kernel import executor
from barista.kernel.clock import today
from tests.conftest import Who, fresh_phone, fresh_sale, ok, owner_val, q


def _blocks(page: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [b for b in page["blocks"] if b["type"] == kind]


async def _new_lead(sale: Any, name: str) -> Any:
    out = await ok(sale, "crm.create_lead", name=name, phone=fresh_phone(), offering_code="HSR", budget_vnd=4_000_000_000)
    return out["lead_id"]


async def test_stage_history_is_append_only_with_who_and_why(who: Who) -> None:
    sale1 = await who("sale1")
    lead = await _new_lead(sale1, "Khách lịch sử")
    await ok(sale1, "crm.set_stage", lead_id=lead, stage="prospect")
    await ok(sale1, "crm.set_stage", lead_id=lead, stage="lost", lost_reason="Mua chỗ khác")
    await ok(sale1, "crm.set_stage", lead_id=lead, stage="prospect")  # quay lại: vòng lặp KHÔNG bị mất (TMS ghi đè)
    rows = await owner_val("SELECT jsonb_agg(jsonb_build_array(kind, from_stage, to_stage, reason) ORDER BY id) FROM crm.stage_events WHERE lead_id = %s", (lead,))
    assert [r[2] for r in rows] == ["lead", "prospect", "lost", "prospect"]
    assert rows[2][3] == "Mua chỗ khác"
    assert await owner_val("SELECT count(*) FROM crm.stage_events WHERE lead_id = %s AND by_user IS NOT NULL", (lead,)) == 4


async def test_no_answer_sets_a_callback_and_outcome_only_for_calls(who: Who) -> None:
    sale1 = await who("sale1")
    lead = await _new_lead(sale1, "Khách không nghe máy")
    await ok(sale1, "crm.log_activity", lead_id=lead, kind="call", outcome="no_answer")
    assert await owner_val("SELECT next_follow_up FROM crm.leads WHERE id = %s", (lead,)) == today()
    r = await executor.run(sale1, "crm.log_activity", {"lead_id": str(lead), "kind": "zalo", "outcome": "connected"})
    assert r.status == "invalid"


async def test_brief_ranks_by_urgency_with_reason_and_a_draft_from_facts(who: Who) -> None:
    admin = await who("admin")
    email = f"sale-brief-{uuid.uuid4().hex[:6]}@{seed.DOMAIN}"  # sale mới tinh: không lẫn lead của dữ liệu mẫu
    await ok(admin, "admin.invite_user", email=email, name="Sale Bản Tin", role="sale")
    sale2 = await seed.who(await owner_val("SELECT tenant_id FROM core.memberships m JOIN core.users u ON u.id = m.user_id WHERE u.email = %s", (email,)),
                           email.split("@")[0], seed.DOMAIN)
    overdue = await _new_lead(sale2, "Khách quá hạn")
    await ok(sale2, "crm.log_activity", lead_id=overdue, kind="call", outcome="connected", next_follow_up=(today() - timedelta(days=3)).isoformat())
    today_lead = await _new_lead(sale2, "Khách hẹn hôm nay")
    await ok(sale2, "crm.log_activity", lead_id=today_lead, kind="zalo", next_follow_up=today().isoformat())
    page = await q(sale2, "crm.brief")
    callouts = _blocks(page, "callout")
    names = [c["title"] for c in callouts]
    i_overdue = next(i for i, t in enumerate(names) if "Khách quá hạn" in t)
    i_today = next(i for i, t in enumerate(names) if "Khách hẹn hôm nay" in t)
    assert i_overdue < i_today  # quá hạn gọi lại gấp hơn hẹn hôm nay
    c = callouts[i_overdue]
    assert c["title"].startswith("Quá hạn gọi lại") and any("trễ 3 ngày" in f for f in c["facts"])
    assert "Khách quá hạn" not in c["draft"] and "quá" not in c["draft"].lower()  # tin chỉ dùng dữ kiện, không lộ lý do nội bộ
    assert c["actions"][0]["command"] == "crm.log_activity"
    assert "Hôm trước mình có trao đổi" in c["draft"]  # đã từng nói chuyện ⇒ được nhắc lại
    fresh = await _new_lead(sale2, "Khách chưa ai gọi")
    async with db.owner_tx() as conn:  # lead "cũ 3 ngày, chưa hẹn": chỉnh bằng owner vì created_at do hệ thống đặt
        await conn.execute("UPDATE crm.leads SET created_at = now() - interval '3 days', next_follow_up = NULL WHERE id = %s", (fresh,))
    c2 = next(c for c in _blocks(await q(sale2, "crm.brief"), "callout") if "Khách chưa ai gọi" in c["title"])
    assert c2["title"].startswith("Chưa ai gọi") and "trao đổi" not in c2["draft"]  # chưa từng liên hệ ⇒ không bịa "hôm trước mình có trao đổi"
    # SĐT trên thẻ: số nội địa liền (0109000012), không +84, không nhóm; tin gửi KHÁCH không mang mã nội bộ của dự án (HSR) — có tên thì nói tên, không thì bỏ
    assert re.search(r"\(0109\d{6}\)$", c["title"]) and "+84" not in c["title"], c["title"]
    assert "HSR" not in c["draft"] and "HSR" not in c2["draft"], (c["draft"], c2["draft"])
    card = await q(sale2, "crm.journey", lead_id=str(overdue))
    assert re.match(r"0109\d{6} · ", card["subtitle"]), card["subtitle"]
    assert all("HSR" not in b.get("draft", "") for b in _blocks(card, "callout"))


async def test_brief_breaks_ties_by_age_then_id_not_by_heap_order(who: Who) -> None:
    """Hoà độ gấp + cùng ngày + CÙNG created_at (lead nhập hàng loạt): trước đây thứ tự là thứ tự đống của Postgres (SQL không ORDER BY, khoá sắp xếp chỉ có
    độ gấp và ngày) — đổi sau mỗi UPDATE, test xếp hạng / bản gọn chập chờn. Nay hoà ⇒ lead về trước, rồi mã; cố ý đặt created_at TRÙNG để mã quyết định."""
    sale = await fresh_sale(who)
    names = {}
    for i in range(4):
        lead = await _new_lead(sale, f"Khách hoà {i}")
        names[lead] = f"Khách hoà {i}"
    async with db.owner_tx() as conn:   # cùng một mốc thời gian cho cả bốn; hẹn gọi hôm nay (cùng loại "due_today" hoặc "uncalled", cùng ngày)
        await conn.execute("UPDATE crm.leads SET created_at = now() - interval '2 hours', next_follow_up = core.today() WHERE owner_id = %s", (sale.user_id,))
    # đảo thứ tự đống: UPDATE từng dòng theo thứ tự ngược mã ⇒ phiên bản dòng mới nằm cuối đống theo thứ tự đó
    async with db.owner_tx() as conn:
        for lead in sorted(names, reverse=True):
            await conn.execute("UPDATE crm.leads SET updated_at = now() WHERE id = %s", (lead,))
    want = [names[k] for k in sorted(names)]
    for _ in range(3):
        titles = [c["title"] for c in _blocks(await q(sale, "crm.brief"), "callout")]
        got = [n for t in titles for n in want if n in t]
        assert got == want
