"""Trí nhớ doanh nghiệp (0091): mục nhớ là dữ liệu có nguồn, đúng quyền (của tôi · gắn đối tượng theo quyền nghiệp vụ · cả công ty), trợ lý ghi
= thẻ đề xuất, quên được; tín hiệu "đề xuất → xác nhận / sửa / bỏ" nằm trong nhật ký cùng vết."""

import uuid
from typing import Any

from barista import seed
from barista.agent import chat, morning
from barista.kernel import events, executor
from barista.kernel.clock import today
from tests.conftest import Who, ok, owner_val, q
from tests.test_assistant import Scripted, call
from tests.test_resume import Log


def _texts(page: dict[str, Any]) -> list[str]:
    return [r["text"] for r in page["rows"]]


async def _lead_of(who: Who, sale: str) -> str:
    s = await who(sale)
    return str(await owner_val("SELECT id FROM crm.leads WHERE owner_id = %s ORDER BY created_at LIMIT 1", (s.user_id,)))


async def test_personal_memory_is_private_even_from_admins(who: Who) -> None:
    sale1, sale2, admin = await who("sale1"), await who("sale2"), await who("admin")
    await ok(sale1, "memory.remember", text="Tôi thích báo cáo ngắn, số trước chữ sau (riêng thử)", kind="preference")
    assert "Tôi thích báo cáo ngắn, số trước chữ sau (riêng thử)" in _texts(await q(sale1, "memory.recall", query="bao cao ngan"))  # tìm bỏ dấu
    for other in (sale2, admin):
        assert "Tôi thích báo cáo ngắn, số trước chữ sau (riêng thử)" not in _texts(await q(other, "memory.items"))
        assert "Tôi thích báo cáo ngắn, số trước chữ sau (riêng thử)" not in _texts(await q(other, "memory.recall", query="bao cao ngan"))
    logged = await owner_val("""SELECT input::text FROM core.audit_log WHERE command = 'memory.remember' AND user_id = %s
                                ORDER BY at DESC LIMIT 1""", (sale1.user_id,))
    assert "riêng thử" not in logged and "sha256" in logged    # nhật ký thao tác (quản trị đọc được) không giữ bản sao nội dung
    key = f"mem-{uuid.uuid4().hex[:8]}"                        # bảng chống lặp cũng không giữ câu tóm tắt chép nội dung (quên không với tới)
    first = await executor.run(sale1, "memory.remember", {"text": "Khách hay đổi lịch (riêng thử 2)", "kind": "fact"}, key=key)
    again = await executor.run(sale1, "memory.remember", {"text": "Khách hay đổi lịch (riêng thử 2)", "kind": "fact"}, key=key)
    assert "riêng thử 2" in first.output["summary"] and again.replayed and again.output["memory_id"] == first.output["memory_id"]
    assert "riêng thử 2" not in await owner_val("SELECT output::text FROM core.idempotency WHERE key LIKE %s", (f"%{key}",))


async def test_subject_memory_follows_the_business_permission_of_the_subject(who: Who) -> None:
    """Ai thấy khách (quyền crm) thì thấy điều nhớ về khách: sale khác (phạm vi của mình) không; trưởng nhóm (đội) và giám đốc (cả công ty) có."""
    sale1, sale2, lead_kd, gd = await who("sale1"), await who("sale2"), await who("truonga"), await who("giamdoc")
    lead = await _lead_of(who, "sale1")
    note = "Khách chỉ nghe máy sau 18h, ưu tiên hướng Đông Nam (thử)"
    out = await ok(sale1, "memory.remember", text=note, scope="subject", subject_kind="lead", subject_id=lead)   # tên ngắn như trợ lý hay gõ
    assert out["created"] and await owner_val("SELECT subject_kind FROM memory.items WHERE id = %s", (out["memory_id"],)) == "crm.lead"
    for p in (sale1, lead_kd, gd):
        assert note in _texts(await q(p, "memory.recall", subject_kind="crm.lead", subject_id=lead))
    assert (await executor.query(sale2, "memory.recall", {"subject_kind": "crm.lead", "subject_id": lead})).status == "not_found"
    assert note not in _texts(await q(sale2, "memory.items"))
    # không ghi nhớ về thứ mình không thấy
    r = await executor.run(sale2, "memory.remember", {"text": "x", "scope": "subject", "subject_kind": "crm.lead", "subject_id": lead})
    assert r.status == "not_found"
    # khách đổi người phụ trách ⇒ điều nhớ đi theo: người mới thấy, người cũ (phạm vi của mình) hết thấy
    # Postgres hỏi thẳng module chủ đối tượng (core.subject_visible) — không chép người phụ trách, người ghi không có ngoại lệ
    await ok(lead_kd, "crm.assign", lead_id=lead, owner_email=sale2.email)
    await events.drain()
    assert note in _texts(await q(sale2, "memory.recall", subject_kind="crm.lead", subject_id=lead))
    assert (await executor.query(sale1, "memory.recall", {"subject_kind": "crm.lead", "subject_id": lead})).status == "not_found"
    assert note not in _texts(await q(sale1, "memory.items"))           # chính người ghi cũng hết thấy khi hết quyền xem khách
    await ok(lead_kd, "crm.assign", lead_id=lead, owner_email=sale1.email)   # trả lại cho dữ liệu mẫu
    await events.drain()


async def test_who_may_forget_and_personal_memory_is_erased(who: Who) -> None:
    """Thấy chưa đủ để bỏ: mục gắn khách — người ghi / người quản lý tri thức; trưởng nhóm thấy nhưng không bỏ được. Mục của tôi bỏ = xoá thật."""
    sale1, lead_kd, gd = await who("sale1"), await who("truonga"), await who("giamdoc")
    lead = await _lead_of(who, "sale1")
    by_gd = (await ok(gd, "memory.remember", text="Khách cần hợp đồng song ngữ (thử quyền bỏ)", scope="subject", subject_kind="crm.lead",
                      subject_id=lead))["memory_id"]
    by_sale = (await ok(sale1, "memory.remember", text="Khách hay hỏi chỗ đỗ xe (thử quyền bỏ)", scope="subject", subject_kind="crm.lead",
                        subject_id=lead))["memory_id"]
    assert (await executor.run(sale1, "memory.forget", {"memory_id": str(by_gd)})).status == "denied"
    assert (await executor.run(lead_kd, "memory.forget", {"memory_id": str(by_sale)})).status == "denied"
    await ok(sale1, "memory.forget", memory_id=by_sale)
    await ok(gd, "memory.forget", memory_id=by_gd)
    assert await owner_val("SELECT count(*) FROM memory.items WHERE id = ANY(%s) AND status = 'retired'", ([by_gd, by_sale],)) == 2   # giữ để tra
    mine = (await ok(sale1, "memory.remember", text="Tôi muốn được nhắc việc lúc 7h (thử xoá)", kind="preference"))["memory_id"]
    await ok(sale1, "memory.forget", memory_id=mine)
    assert await owner_val("SELECT count(*) FROM memory.items WHERE id = %s", (mine,)) == 0


async def test_new_memory_replaces_or_flags_similar_ones(who: Who) -> None:
    sale1 = await who("sale1")
    lead = await _lead_of(who, "sale1")
    sub = {"scope": "subject", "subject_kind": "crm.lead", "subject_id": lead}
    old = (await ok(sale1, "memory.remember", text="Khách thích căn tầng thấp gần thang máy (thử thay)", **sub))["memory_id"]
    again = await ok(sale1, "memory.remember", text="Khách thích căn tầng cao xa thang máy (thử thay)", **sub)
    assert again["similar"] and "đã có điều gần giống" in again["summary"]          # thẻ cho người bấm thấy đang ghi chồng
    await ok(sale1, "memory.forget", memory_id=again["memory_id"])
    new = await ok(sale1, "memory.remember", text="Khách đổi ý: muốn tầng cao, view hồ (thử thay)", replaces=str(old), **sub)
    assert "thay cho" in new["summary"]
    texts = _texts(await q(sale1, "memory.recall", subject_kind="crm.lead", subject_id=lead))
    assert "Khách đổi ý: muốn tầng cao, view hồ (thử thay)" in texts and "Khách thích căn tầng thấp gần thang máy (thử thay)" not in texts
    await ok(sale1, "memory.forget", memory_id=new["memory_id"])


async def test_recall_marks_use_and_unused_or_expired_memory_comes_back_for_review(who: Who) -> None:
    """Chống rác: mỗi lần được đưa ra ghi một dấu dùng (cùng vết lượt); hết hạn / lâu không dùng ⇒ hàng "cần xem lại"; giữ ⇒ ra khỏi hàng."""
    sale1 = await who("sale1")
    used = (await ok(sale1, "memory.remember", text="Tôi hay quên gửi bảng giá kèm (thử dấu dùng)", kind="lesson"))["memory_id"]
    with executor.tracing("chat:thu-dau-dung"):
        assert "Tôi hay quên gửi bảng giá kèm (thử dấu dùng)" in _texts(await q(sale1, "memory.recall", query="quen gui bang gia"))
    assert await owner_val("SELECT trace FROM memory.uses WHERE item_id = %s", (used,)) == "chat:thu-dau-dung"
    assert await owner_val("SELECT count(*) FROM core.audit_log WHERE command = 'memory.used' AND trace <> ''") == 0   # không lẫn vào chuỗi của lượt
    stale = (await ok(sale1, "memory.remember", text="Tôi thích gọi khách bằng Zalo (thử lâu không dùng)", kind="preference"))["memory_id"]
    expired = (await ok(sale1, "memory.remember", text="Đang nghỉ phép tới hết tuần (thử hết hạn)", valid_until=str(today())))["memory_id"]
    await owner_val("UPDATE memory.items SET created_at = now() - interval '100 days' WHERE id = ANY(%s) RETURNING 1", ([stale, used],))
    await owner_val("UPDATE memory.items SET valid_until = current_date - 1 WHERE id = %s RETURNING 1", (expired,))
    page = await q(sale1, "memory.review")
    why = {r["text"]: r["why"] for r in page["rows"]}
    assert why.get("Tôi thích gọi khách bằng Zalo (thử lâu không dùng)") == "90 ngày chưa được dùng"
    assert why.get("Đang nghỉ phép tới hết tuần (thử hết hạn)") == "Hết hạn còn đúng"
    assert "Tôi hay quên gửi bảng giá kèm (thử dấu dùng)" not in why                       # cũ nhưng vừa được dùng
    assert "Đang nghỉ phép tới hết tuần (thử hết hạn)" not in _texts(await q(sale1, "memory.recall", query="nghi phep"))
    await ok(sale1, "memory.keep", memory_id=stale)
    assert "Tôi thích gọi khách bằng Zalo (thử lâu không dùng)" not in _texts(await q(sale1, "memory.review"))
    for mid in (used, stale, expired):
        await ok(sale1, "memory.forget", memory_id=mid)


async def test_company_memory_is_read_by_all_written_by_knowledge_managers(who: Who) -> None:
    sale1, gd = await who("sale1"), await who("giamdoc")
    text = "Chủ đầu tư dự án mẫu thường trả phí chậm khoảng 10 ngày — gọi kế toán của họ (thử)"
    assert (await executor.run(sale1, "memory.remember", {"text": text, "scope": "company", "kind": "lesson"})).status == "denied"
    mid = (await ok(gd, "memory.remember", text=text, scope="company", kind="lesson"))["memory_id"]
    assert text in _texts(await q(sale1, "memory.recall", query="chu dau tu tra phi cham"))
    assert (await executor.run(sale1, "memory.forget", {"memory_id": str(mid)})).status == "denied"
    await ok(gd, "memory.forget", memory_id=mid)
    assert text not in _texts(await q(sale1, "memory.recall", query="chu dau tu tra phi cham"))     # đã bỏ ⇒ không gợi lại


async def test_assistant_writes_memory_as_a_proposal_with_its_turn_as_source(who: Who) -> None:
    """Trợ lý ghi nhớ = thẻ đề xuất (người biết hệ thống nhớ gì); người bấm ⇒ mục nhớ mang NGUỒN là lượt đã soạn."""
    sale1 = await who("sale1")
    text = "Anh An thích nhận danh sách khách theo dự án (thử nguồn)"
    with executor.tracing("chat:thu-de-xuat"):
        assert (await executor.run(sale1.as_agent(), "memory.remember", {"text": text, "kind": "preference"})).status == "proposed"
    # trợ lý soạn mà không có vết ⇒ DB không nhận (mục do trợ lý soạn luôn truy được lượt / việc sinh ra nó)
    assert (await executor.run(sale1.as_agent(), "memory.remember", {"text": text, "kind": "preference"})).status == "invalid"
    model = Scripted([[call("memory.remember", text=text, kind="preference")], "Em đã ghi nhớ ạ."])
    first = Log()
    out = await chat.chat_turn(sale1, "Nhớ giúp: tôi thích danh sách khách theo dự án", emit=first, model=model)
    card = first.proposals()[0]
    assert await owner_val("SELECT count(*) FROM memory.items WHERE text = %s", (text,)) == 0                # chưa ghi gì
    await chat.decide(sale1, card["id"], "confirm", card["input"], "k-mem-1", emit=Log(), model=model)
    turn = await owner_val("""SELECT t->>'trace' FROM core.chat_threads c, jsonb_array_elements(c.turns) t WHERE c.id = %s
                              AND t->>'role' = 'assistant' LIMIT 1""", (out["thread_id"],))
    assert await owner_val("SELECT sources->0->>'trace' FROM memory.items WHERE text = %s", (text,)) == f"chat:{turn}"


async def test_dismissed_proposals_are_signals_in_the_audit_log(who: Who) -> None:
    """Tín hiệu cho trí nhớ: đề xuất (trợ lý) → bỏ (người) cùng một vết — ở thẻ trong trò chuyện và thẻ buổi sáng."""
    sale1, lead = await who("sale1"), await who("mkt")
    model = Scripted([[call("memory.remember", text="Điều sẽ bị bỏ (thử)", kind="fact")], "Vâng ạ."])
    first = Log()
    out = await chat.chat_turn(sale1, "Nhớ giúp điều này", emit=first, model=model)
    await chat.decide(sale1, first.proposals()[0]["id"], "dismiss", {}, "k-dis-1", emit=Log(), model=model)
    turn = await owner_val("""SELECT t->>'trace' FROM core.chat_threads c, jsonb_array_elements(c.turns) t WHERE c.id = %s
                              AND t->>'role' = 'assistant' LIMIT 1""", (out["thread_id"],))
    trail = await owner_val("SELECT jsonb_agg(jsonb_build_array(via, status) ORDER BY id) FROM core.audit_log WHERE trace = %s",
                            (f"chat:{turn}",))
    assert trail == [["agent", "proposed"], ["human", "dismissed"]]
    cards = await morning.cards(lead)
    if cards:   # thẻ sáng (nếu hôm nay có): bỏ ⇒ dòng "dismissed" cùng vết thẻ
        await morning.decide(lead, cards[0]["id"], "dismiss", None, "k-dis-2")
        assert await owner_val("""SELECT count(*) FROM core.audit_log WHERE status = 'dismissed' AND trace = (
                                  SELECT 'morning:' || user_id || ':' || day || ':' || key FROM core.proposals WHERE id = %s)""",
                               (cards[0]["id"],)) == 1


async def test_memory_suggestion_does_not_hold_the_reply(who: Who) -> None:
    """Gợi ý ghi nhớ là thẻ BÊN LỀ: trợ lý trả lời tiếp như bình thường, lượt không chờ; người bấm lúc nào cũng được, bấm xong không chạy tiếp."""
    sale1 = await who("sale1")
    text = "Tôi muốn bản tin sáng chỉ 3 dòng (thử bên lề)"
    model = Scripted([[call("memory.remember", text=text, kind="preference")], "Dạ, hôm nay anh có 3 khách cần gọi lại."])
    first = Log()
    out = await chat.chat_turn(sale1, "Hôm nay tôi cần gọi ai? Nhân tiện bản tin sáng chỉ cần 3 dòng thôi", emit=first, model=model)
    card = first.proposals()[0]
    assert card.get("aside") and not out["pending"] and out["text"] == "Dạ, hôm nay anh có 3 khách cần gọi lại."
    later = Log()
    await chat.decide(sale1, card["id"], "confirm", card["input"], "k-aside-1", emit=later, model=model)
    assert [e.get("resuming") for e in later.of("decided")] == [False]
    assert await owner_val("SELECT count(*) FROM memory.items WHERE text = %s", (text,)) == 1
    again = Log()   # bấm lại (mạng chập) ⇒ không ghi hai lần
    await chat.decide(sale1, card["id"], "confirm", card["input"], "k-aside-2", emit=again, model=model)
    assert again.of("decided")[0]["replayed"] and await owner_val("SELECT count(*) FROM memory.items WHERE text = %s", (text,)) == 1
    await ok(sale1, "memory.forget", memory_id=await owner_val("SELECT id FROM memory.items WHERE text = %s", (text,)))


async def test_departed_members_memory_is_company_work_data(who: Who) -> None:
    """Trí nhớ "của tôi" là dữ liệu công việc: còn làm thì chỉ chính chủ; nghỉ ⇒ người giữ quyền memory.legacy (mặc định giám đốc) thấy trong hàng
    xem lại, chuyển thành trí nhớ chung (giữ nguồn) hoặc xoá. Quản trị hệ thống không có quyền này ⇒ vẫn không thấy."""
    admin, gd = await who("admin"), await who("giamdoc")
    local = f"nghi-{uuid.uuid4().hex[:6]}"
    await ok(admin, "admin.invite_user", email=f"{local}@{seed.DOMAIN}", name="Người sắp nghỉ", role="sale", team_code="KD-A")
    leaver = await who(local)
    text = "Chủ đầu tư mẫu chỉ ký phụ lục vào thứ Năm, gửi trước hai ngày (thử người nghỉ)"
    with executor.tracing("chat:thu-nguoi-nghi"):
        mid = (await ok(leaver, "memory.remember", text=text, kind="procedure"))["memory_id"]
    assert text not in _texts(await q(gd, "memory.review"))                       # còn làm ⇒ của riêng người đó
    await ok(admin, "admin.update_member", email=f"{local}@{seed.DOMAIN}", active=False)
    rows = {r["text"]: r for r in (await q(gd, "memory.review"))["rows"]}
    assert rows[text]["why"] == "Của người đã nghỉ" and rows[text]["about"] == "Của Người sắp nghỉ (đã nghỉ)"
    assert text not in _texts(await q(admin, "memory.items"))
    shared = await ok(gd, "memory.remember", text=text, kind="procedure", scope="company", replaces=str(mid))
    assert await owner_val("SELECT count(*) FROM memory.items WHERE id = %s", (mid,)) == 0                # mục cá nhân đã chuyển ⇒ xoá
    assert await owner_val("SELECT sources->0->>'trace' FROM memory.items WHERE id = %s", (shared["memory_id"],)) == "chat:thu-nguoi-nghi"
    assert text in _texts(await q(await who("sale1"), "memory.recall", query="phu luc thu nam"))
    await ok(gd, "memory.forget", memory_id=shared["memory_id"])


async def test_memory_list_can_be_searched(who: Who) -> None:
    sale1 = await who("sale1")
    mid = (await ok(sale1, "memory.remember", text="Tôi hay họp giao ban lúc 8h30 (thử tìm)"))["memory_id"]
    assert _texts(await q(sale1, "memory.items", query="giao ban")) == ["Tôi hay họp giao ban lúc 8h30 (thử tìm)"]
    await ok(sale1, "memory.forget", memory_id=mid)
