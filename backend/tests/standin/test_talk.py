"""TRAO ĐỔI trên đối tượng của một gói ngành (0046 / 0089) — bản LÕI (ADR 0190) của ``barista-packs:commission/tests/test_talk.py``: quyền đọc luồng = quyền
thấy đối tượng (loại đối tượng gói khai — ``m.subject`` + hàm "thấy" ở DB), thẻ chia sẻ chỉ là tham chiếu (người mở chạy lại bằng quyền mình), trao đổi
về hồ sơ nằm ngay trên thẻ hồ sơ của gói, tắt gói Trao đổi / thiếu quyền Trao đổi thì thẻ của gói vẫn mở, phiếu duyệt chia sẻ là THẺ SỐNG."""

import uuid
from typing import Any

from barista import seed, workspaces
from barista.kernel import executor
from tests.conftest import Who, approved, edge, fresh_phone, ok, owner_val, q
from tests.standin import helpers, pack
from tests.test_journey import _blocks
from tests.test_packs_industry import PEOPLE
from tests.test_talk import _subj, _thread


def _order(o: dict[str, Any]) -> dict[str, str]:
    return {"subject_kind": pack.ORDER, "subject_id": o["order_id"]}


async def test_who_can_read_follows_who_can_see_the_order(who: Who) -> None:
    """Luồng bám một ĐƠN (đối tượng của gói): người phụ trách, trưởng đội của họ và người phạm vi cả công ty đọc / viết được; người đội khác thì như
    không có luồng — nhắc tên cũng không cấp quyền."""
    sale3, truongb, ketoan, sale1, truonga = await who("sale3"), await who("truongb"), await who("ketoan"), await who("sale1"), await who("truonga")
    o = await helpers.order(who, owner="sale3")
    await ok(sale3, "talk.post", **_order(o), body="Khách hẹn ký thứ Sáu")
    for insider in (truongb, ketoan):
        assert [m["body"] for m in _thread(await q(insider, "talk.thread", **_order(o)))["messages"]] == ["Khách hẹn ký thứ Sáu"]
    for outsider in (sale1, truonga):                                               # đội khác: không thấy đơn ⇒ không thấy luồng
        assert (await executor.query(outsider, "talk.thread", _order(o))).status == "not_found"
        assert (await executor.run(outsider, "talk.post", {**_order(o), "body": "chen vào"})).status == "not_found"
    r = await executor.run(sale3, "talk.post", {**_order(o), "body": "@An xem giúp", "mentions": [sale1.email]})
    assert r.status == "invalid" and "không xem được" in r.error                   # nhắc người không thấy đơn ⇒ từ chối, không lộ
    assert o["code"] in await owner_val("SELECT title FROM talk.threads WHERE subject_id = %s", (o["order_id"],))   # tên luồng = tên đối tượng gói khai


async def test_shared_cards_are_references_rerun_with_the_viewers_rights(who: Who) -> None:
    sale3, truongb = await who("sale3"), await who("truongb")
    o = await helpers.order(who, owner="sale3")
    mate = await helpers.fresh_member(who, "sale", "KD-B")
    await helpers.order(who, owner=mate)
    for bad in ({"query": "talk.block", "input": {}}, {"query": "orderdesk.create", "input": {}}, {"query": "khong.co", "input": {}},
                {"query": "orderdesk.health_report", "input": {}}):
        r = await executor.run(sale3, "talk.post", {**_order(o), "body": "xem thẻ", "card": bad})
        assert r.status == "invalid", bad                                           # chỉ truy vấn công khai — không nội bộ, không lệnh ghi
    body = f"Danh sách đơn của em {uuid.uuid4().hex[:6]}"
    await ok(sale3, "talk.post", **_order(o), body=body, card={"query": "orderdesk.list", "input": {}, "title": "Đơn của tôi",
                                                                "page": {"rows": [{"so": 999}]}})
    stored = await owner_val("SELECT card FROM talk.messages WHERE body = %s", (body,))
    assert stored == {"query": "orderdesk.list", "input": {}, "title": "Đơn của tôi"}   # không lưu dữ liệu chụp kèm theo
    card = _thread(await q(truongb, "talk.thread", **_order(o)))["messages"][-1]["card"]
    viewer = await executor.query(truongb, card["query"], card["input"])
    assert viewer.status == "ok" and viewer.output["title"]                         # mở thẻ = chạy lại bằng quyền NGƯỜI MỞ
    mine = await executor.query(sale3, card["query"], card["input"])
    assert {r["owner"] for r in mine.output["rows"]} == {sale3.name}                # cùng một thẻ, người gửi chỉ thấy đơn của mình…
    assert {sale3.name, mate.name} <= {r["owner"] for r in viewer.output["rows"]}   # …trưởng đội mở ra thấy đơn cả đội


async def test_case_discussion_sits_on_the_case_card(who: Who) -> None:
    sale3, ketoan = await who("sale3"), await who("ketoan")
    o = await helpers.signed(who, owner="sale3")
    case = str(await helpers.case_id(o["order_id"]))
    await ok(ketoan, "talk.post", subject_kind="flow.case", subject_id=case, body="Phiếu đặt hàng thiếu chữ ký", mentions=[sale3.email])
    page = await q(sale3, "orderdesk.case_file", code=o["code"])
    assert _thread(page)["messages"][0]["body"] == "Phiếu đặt hàng thiếu chữ ký" and _thread(page)["messages"][0]["mentions_me"]
    assert (await executor.query(await who("sale1"), "talk.thread", {"subject_kind": "flow.case", "subject_id": case})).status == "not_found"


async def test_without_the_talk_pack_cards_still_open() -> None:
    domain = f"k{uuid.uuid4().hex[:6]}.demo"
    tid = await seed.create_tenant(f"Công ty không trao đổi {domain}", domain, people=PEOPLE, packs=(pack.PACK, "crm"))
    sale, admin = await seed.who(tid, "sale1", domain), await seed.who(tid, "admin", domain)
    lead = str((await ok(sale, "crm.create_lead", name="Khách không trao đổi", phone=fresh_phone()))["lead_id"])
    page = await q(sale, "crm.journey", lead_id=lead)
    assert _blocks(page, "thread") == [] and _blocks(page, "steps")                # không có khối trao đổi, thẻ vẫn đủ
    assert (await executor.run(sale, "talk.post", {**_subj(lead), "body": "x"})).status == "not_found"
    o = await ok(admin, "orderdesk.create", title="Tư vấn", customer="Khách không trao đổi", owner_email=sale.email, amount_vnd=1_000_000_000)
    from barista.kernel import events
    await events.drain()
    case = await q(sale, "orderdesk.case_file", code=o["code"])
    assert _blocks(case, "stages") and _blocks(case, "thread") == []               # thẻ hồ sơ của gói cũng vậy
    queries = workspaces.manifest(sale, "")["queries"]
    assert "crm.journey" in queries and "orderdesk.case_file" in queries and "talk.inbox" not in queries   # app không gọi hộp tin khi gói tắt


async def test_people_without_talk_rights_still_open_the_case_card(who: Who) -> None:
    """Phần gắn thêm (khối trao đổi) không bao giờ làm vỡ thẻ chính: vai không có quyền "Trao đổi" vẫn mở hồ sơ, chỉ không thấy khối."""
    admin = await who("admin")
    o = await helpers.signed(who, owner="sale3")
    assert "talk.inbox" in workspaces.manifest(await who("sale3"), "")["queries"]
    assert _blocks(await q(await who("sale3"), "orderdesk.case_file", code=o["code"]), "thread")
    await approved(admin, "admin.set_grant", role="sale", resource="talk", scope="team", actions=[])
    try:
        sale3 = await who("sale3")
        assert "talk.inbox" not in workspaces.manifest(sale3, "")["queries"]       # không quyền ⇒ app không gọi hộp tin
        page = await q(sale3, "orderdesk.case_file", code=o["code"])
        assert _blocks(page, "stages") and _blocks(page, "thread") == []
        assert (await executor.query(sale3, "talk.mine", {})).status == "denied"
    finally:
        await approved(admin, "admin.set_grant", role="sale", resource="talk", scope="team", actions=["read", "write"])


async def test_shared_approval_card_is_live_and_only_the_current_approver_gets_the_buttons(who: Who) -> None:
    """THẺ SỐNG (0089): phiếu chia sẻ vào phòng tính lại bằng quyền NGƯỜI XEM — người duyệt bước hiện tại thấy Duyệt / Từ chối, người xin chỉ
    thấy trạng thái (không tự duyệt), người ngoài phiếu không thấy gì thêm; duyệt xong thì thẻ của mọi người đổi trạng thái."""
    sale3, giamdoc, sale1 = await who("sale3"), await who("giamdoc"), await who("sale1")
    o = await helpers.order(who, owner="sale3")
    r = await executor.run(sale3, "orderdesk.cancel", {"code": o["code"], "reason": "thẻ sống trong phòng"})   # luật duyệt mặc định của gói
    assert r.status == "pending_approval" and r.approval_id
    room = await ok(sale3, "talk.create_room", name=f"Duyệt nhanh {uuid.uuid4().hex[:4]}", members=[giamdoc.email, sale1.email])
    subj = {"subject_kind": "talk.room", "subject_id": str(room["room_id"])}
    await ok(sale3, "talk.post", **subj, body="Anh duyệt giúp em",
             card={"query": "core.approval", "input": {"approval_id": r.approval_id}, "title": "Phiếu huỷ đơn"})

    async def live(p: Any) -> dict[str, Any]:
        thread = _blocks(await q(p, "talk.thread", **subj), "thread")[0]
        return [m for m in thread["messages"] if m["card"]][-1]["card"]["live"]

    boss = await live(giamdoc)
    assert [a["title"] for a in boss["actions"]] == ["Duyệt", "Từ chối"] and boss["subtitle"].startswith("Chờ bạn duyệt")
    assert boss["actions"][0]["preset"] == {"approval_id": r.approval_id, "approve": True}
    mine = await live(sale3)
    assert mine["actions"] == [] and mine["subtitle"].startswith("Chờ duyệt bước 1/")      # người xin không tự duyệt
    assert (await live(sale1))["actions"] == []                                            # cùng phòng nhưng không phải người duyệt

    await ok(edge(giamdoc), "core.decide", approval_id=r.approval_id, approve=True)
    assert (await live(giamdoc))["actions"] == []                                          # đã duyệt bước mình ⇒ hết nút
    after = (await live(sale3))["subtitle"]
    assert after != mine["subtitle"] and (after.startswith("Chờ duyệt bước 2/") or after.startswith("Đã duyệt"))   # mọi người thấy trạng thái mới
    thread_id = _blocks(await q(sale3, "talk.thread", **subj), "thread")[0]["thread_id"]
    assert (await ok(giamdoc, "talk.cards_changed", thread_id=thread_id))["thread_id"]
    assert await owner_val("SELECT status FROM orderdesk.orders WHERE id = %s", (o["order_id"],)) == "cancelled"
