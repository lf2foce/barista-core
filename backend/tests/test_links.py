"""Link gửi khách (0064): khách không đăng nhập, cầm link thì làm ĐÚNG một yêu cầu. Ranh giới: link chỉ chạy thao tác public của đúng loại link;
RLS chỉ mở đúng yêu cầu (không thấy kỳ / tài liệu / yêu cầu khác); nhân viên không chạy được thao tác của khách; mã link không nằm trong DB (chỉ mã
băm; khoá once_* không lưu khi chống lặp); tạo lại / đóng ⇒ link cũ chết ngay; hết hạn ⇒ chết; câu trả lời là giải trình chờ kế toán nhận."""

import base64
import json
import uuid
from typing import Any
from uuid import UUID

from httpx import ASGITransport, AsyncClient

from barista import seed
from barista.db import db
from barista.http_api import app
from barista.kernel import executor, links
from barista.kernel.principal import Principal
from tests.conftest import ok, owner_val, q

ITEMS = "- Sao kê Vietcombank tháng 10 [tệp]\n- Khoản 35tr ngày 14/10 là gì? [chọn: Trả nhà cung cấp; Tạm ứng; Chi cá nhân]\n- Ghi chú thêm"


async def _company() -> tuple[UUID, dict[str, Principal]]:
    d = f"{uuid.uuid4().hex[:6]}.link.test"
    tid = await seed.create_tenant("Khách có link (test)", d, packs=("books",),
                                   people=[("admin", "Quản trị", "admin", "QT", True), ("kt1", "Kế toán Một", "bookkeeper", "KT", False)])
    return tid, {k: await seed.who(tid, k, d) for k in ("admin", "kt1")}


async def _request(p: Principal, **kw: Any) -> tuple[UUID, str, dict[str, Any]]:
    out = await ok(p, "books.create_request", title="Chứng từ tháng 10/2026", items=ITEMS, note="Gửi giúp em trước ngày 5", **kw)
    rid, _, token = out["once_link"].partition(":")
    return UUID(rid), token, out


async def test_client_answers_and_uploads_through_the_link_only() -> None:
    _, people = await _company()
    rid, token, _ = await _request(people["kt1"])
    card = await q(people["kt1"], "asks.request", once_link=f"{rid}:{token}")
    callout = next(b for b in card["blocks"] if b["type"] == "callout")
    assert f"/c/{token}" in callout["draft"]                                               # ngay sau khi tạo: thẻ hiện tin nhắn kèm link để copy
    assert not any(b["type"] == "callout" for b in (await q(people["kt1"], "asks.request", once_link=str(rid)))["blocks"])   # mở lại: không còn
    assert not any(b["type"] == "callout" for b in (await q(people["kt1"], "asks.request", once_link=f"{rid}:{'x' * 43}"))["blocks"])

    p = await links.resolve(token)
    assert p is not None and p.via == "link" and p.user_id is None and not p.ai
    view = (await executor.query(p, "asks.link_view", {})).output
    assert view["title"] == "Chứng từ tháng 10/2026" and [i["kind"] for i in view["items"]] == ["file", "choice", "text"]
    file_item, choice_item, _ = view["items"]
    bad = await executor.run(p, "asks.link_answer", {"item_id": choice_item["id"], "answer": "Mua vàng"})
    assert bad.status == "invalid"                                                          # chỉ nhận đúng các lựa chọn
    assert (await executor.run(p, "asks.link_answer", {"item_id": choice_item["id"], "answer": "Tạm ứng"})).ok
    pdf = base64.b64encode(b"%PDF-1.4 sao ke (test)").decode()
    up = await executor.run(p, "asks.link_upload", {"item_id": file_item["id"], "filename": "sao ke VCB T10.pdf", "data_b64": pdf})
    assert up.ok, up.error
    assert (await executor.run(p, "asks.link_upload", {"item_id": file_item["id"], "filename": "virus.exe", "data_b64": pdf})).status == "invalid"
    doc = await owner_val("SELECT document_ids[1] FROM asks.items WHERE id = %s", (file_item["id"],))
    assert await owner_val("SELECT filename FROM kb.files WHERE document_id = %s", (doc,)) == "sao ke VCB T10.pdf"   # file gốc vào kho của khách
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND kind = 'asks'", (people["kt1"].user_id,)) == 2
    assert await owner_val("SELECT via FROM core.audit_log WHERE command = 'asks.link_answer' ORDER BY at DESC LIMIT 1") == "link"

    await ok(people["kt1"], "asks.accept_items", request_id=str(rid), seqs=[2])               # giải trình thành căn cứ khi kế toán nhận
    again = await executor.run(p, "asks.link_answer", {"item_id": choice_item["id"], "answer": "Chi cá nhân"})
    assert again.status == "conflict"                                                        # đã nhận ⇒ khách không đổi được nữa


async def test_a_link_opens_one_request_and_nothing_else() -> None:
    tid, people = await _company()
    await executor.run(__import__("barista.kernel.principal", fromlist=["system_principal"]).system_principal(tid), "books.configure",
                       {"owner_email": people["kt1"].email, "start_period": "2026-09-01"})
    rid, token, _ = await _request(people["kt1"])
    other, _, _ = await _request(people["kt1"])
    p = await links.resolve(token)
    assert p is not None
    async with db.tenant_tx(p, read_only=True) as conn:                                     # đọc thẳng bảng: chỉ đúng yêu cầu của link
        cur = await conn.execute("SELECT (SELECT count(*) FROM asks.requests) AS r, (SELECT count(*) FROM asks.items) AS i,"
                                 " (SELECT count(*) FROM books.setup) AS s, (SELECT count(*) FROM kb.documents) AS d,"
                                 " (SELECT count(*) FROM core.memberships) AS m")
        row = await cur.fetchone()
    assert row is not None and (row["r"], row["i"], row["d"], row["s"], row["m"]) == (1, 3, 0, 0, 0)   # cả cấu hình chung / thành viên cũng đóng
    for name, inp in (("books.periods", {}), ("asks.requests", {}), ("books.mark_filing", {"filing_id": str(uuid.uuid4()), "evidence": "x"}),
                      ("kb.search", {"q": "sao kê"}), ("books.create_request", {"title": "x", "items": "- y"})):
        r = await (executor.query(p, name, inp) if name in ("books.periods", "asks.requests", "kb.search") else executor.run(p, name, inp))
        assert r.status in ("denied", "not_found"), (name, r.status)                        # ngoài thao tác public của link: không gì chạy được
    other_item = await owner_val("SELECT id FROM asks.items WHERE request_id = %s ORDER BY seq LIMIT 1 OFFSET 1", (other,))
    r = await executor.run(p, "asks.link_answer", {"item_id": str(other_item), "answer": "Tạm ứng"})
    assert r.status == "not_found"                                                          # mục của yêu cầu khác: không thấy
    staff = await executor.run(people["kt1"], "asks.link_answer", {"item_id": str(other_item), "answer": "Tạm ứng"})
    assert staff.status == "denied"                                                         # nhân viên không trả lời thay khách
    assert rid != other


async def test_links_are_hashed_revocable_and_expire() -> None:
    _, people = await _company()
    rid, token, _ = await _request(people["kt1"])
    assert await owner_val("SELECT count(*) FROM core.public_links WHERE token_hash = %s", (token.encode(),)) == 0   # không lưu mã rõ
    relinked = await ok(people["kt1"], "asks.relink", request_id=str(rid))
    new = relinked["once_link"].partition(":")[2]
    assert await links.resolve(token) is None and await links.resolve(new) is not None   # tạo lại ⇒ link cũ chết ngay
    await owner_val("UPDATE core.public_links SET expires_at = now() - interval '1 second' WHERE ref_id = %s RETURNING 1", (rid,))
    assert await links.resolve(new) is None                                                 # hết hạn
    rid2, token2, _ = await _request(people["kt1"])
    await ok(people["kt1"], "asks.close", request_id=str(rid2))
    assert await links.resolve(token2) is None                                              # đóng ⇒ thu hồi

    key = f"idem-{uuid.uuid4().hex}"
    first = await executor.run(people["kt1"], "books.create_request", {"title": "Lần chống lặp", "items": "- a [tệp]"}, key=key)
    assert first.ok, first.error
    assert "once_link" in first.output
    stored = await owner_val("SELECT output FROM core.idempotency WHERE key LIKE %s", ("%" + key,))
    assert "once_link" not in stored and "request_id" in stored                            # khoá once_* không bao giờ vào DB
    replay = await executor.run(people["kt1"], "books.create_request", {"title": "Lần chống lặp", "items": "- a [tệp]"}, key=key)
    assert "once_link" not in replay.output


async def test_public_http_routes() -> None:
    _, people = await _company()
    _, token, _ = await _request(people["kt1"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get(f"/v1/public/links/{'x' * 43}")).status_code == 404
        view = (await c.get(f"/v1/public/links/{token}")).json()["output"]
        file_item = view["items"][0]["id"]
        r = await c.post(f"/v1/public/links/{token}/upload", params={"item_id": file_item}, content=b"%PDF-1.4 test",
                         headers={"x-filename": "sao%20k%C3%AA.pdf"})
        assert r.status_code == 200, r.text
        r = await c.post(f"/v1/public/links/{token}/answer", content=json.dumps({"item_id": view["items"][2]["id"], "answer": "Không có gì thêm"}),
                         headers={"content-type": "application/json"})
        assert r.status_code == 200, r.text
        done = (await c.get(f"/v1/public/links/{token}")).json()["output"]["items"]
    assert [i["done"] for i in done] == [True, False, True] and done[0]["files"] == 1


async def test_a_link_created_through_the_assistant_never_lands_in_chat_history_or_the_model() -> None:
    from barista.agent import chat
    from tests.test_assistant import Scripted, call
    from tests.test_resume import Log

    tid, people = await _company()
    model = Scripted([[call("books.create_request", title="Chứng từ tháng 11", items="- Sao kê tháng 11 [tệp]")], "Đã tạo yêu cầu ạ."])
    first = Log()
    await chat.chat_turn(people["kt1"], "Gửi khách yêu cầu nộp sao kê tháng 11", emit=first, model=model)
    card = first.proposals()[0]
    after = Log()
    await chat.decide(people["kt1"], card["id"], "confirm", card["input"], "k-req-1", emit=after, model=model)
    assert await owner_val("SELECT count(*) FROM core.public_links WHERE tenant_id = %s", (tid,)) == 1   # đã tạo thật (người bấm)
    turns = json.dumps(await owner_val("SELECT turns FROM core.chat_threads WHERE user_id = %s ORDER BY updated_at DESC LIMIT 1",
                                       (people["kt1"].user_id,)), ensure_ascii=False)
    assert "once_link" not in turns and "/c/" not in turns                                  # lịch sử chat không giữ mã link
    assert "once_link" not in json.dumps(model.returns, ensure_ascii=False, default=str)    # model không nhìn thấy mã link


async def test_public_link_is_rate_limited_per_link() -> None:
    from barista.kernel import ratelimit

    ratelimit.reset()
    assert all(ratelimit.allow("k", "upload", now=0) for _ in range(60)) and not ratelimit.allow("k", "upload", now=1)
    assert ratelimit.allow("k", "upload", now=3601)                                         # cửa sổ trượt qua ⇒ gửi tiếp được
    assert ratelimit.allow("other", "upload", now=1)                                        # link khác đếm riêng
    assert ratelimit.take_bytes("big", ratelimit.UPLOAD_BYTES_PER_DAY, now=0) and not ratelimit.take_bytes("big", 1, now=1)   # trần / ngày
    assert ratelimit.take_bytes("big", 1, now=86401)                                        # qua ngày ⇒ lại có hạn mức
    ratelimit.reset()
    _, people = await _company()
    _, token, _ = await _request(people["kt1"])
    old = ratelimit.LIMITS["view"]
    ratelimit.LIMITS["view"] = (2, 600)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            codes = [(await c.get(f"/v1/public/links/{token}")).status_code for _ in range(3)]
    finally:
        ratelimit.LIMITS["view"] = old
        ratelimit.reset()
    assert codes == [200, 200, 429]


async def test_accept_several_items_in_one_go() -> None:
    _, people = await _company()
    rid, token, _ = await _request(people["kt1"])
    p = await links.resolve(token)
    assert p is not None
    view = (await executor.query(p, "asks.link_view", {})).output
    f, ch, _ = view["items"]
    await executor.run(p, "asks.link_upload", {"item_id": f["id"], "filename": "sao ke.txt", "data_b64": base64.b64encode("Sao kê VCB".encode()).decode()})
    await executor.run(p, "asks.link_answer", {"item_id": ch["id"], "answer": "Tạm ứng"})
    card = await q(people["kt1"], "asks.request", once_link=str(rid))
    assert "Sao kê VCB" in json.dumps(card, ensure_ascii=False)                              # nội dung khách nộp hiện ngay trên thẻ
    bulk = next(a for a in card["actions"] if a["command"] == "asks.accept_items")
    out = await ok(people["kt1"], "asks.accept_items", **bulk["preset"], note="Đã đối chiếu")
    assert out["accepted"] == 2
    r = await executor.run(people["kt1"], "asks.accept_items", {"request_id": str(rid), "seqs": [3]})
    assert r.status == "invalid"                                                            # mục khách chưa gửi: không nhận được


async def test_upload_is_refused_before_reading_the_body() -> None:
    from barista.kernel import ratelimit

    ratelimit.reset()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(f"/v1/public/links/{'x' * 43}/upload", params={"item_id": str(uuid.uuid4())}, content=b"abc")
        assert r.status_code == 404                                                         # link sai: không đọc / lưu gì
        big = await c.post(f"/v1/public/links/{'x' * 43}/upload", params={"item_id": str(uuid.uuid4())}, content=b"0",
                           headers={"content-length": str(30 * 1024 * 1024)})
        assert big.status_code == 413                                                       # khai báo quá cỡ: chặn trước cả khi tra link


async def test_switching_off_a_group_hides_its_data_even_through_another_module() -> None:
    tid, people = await _company()
    from barista.kernel.principal import system_principal
    await executor.run(system_principal(tid), "books.configure", {"owner_email": people["kt1"].email, "start_period": "2026-09-01"})
    await executor.run(system_principal(tid), "books.open_due", {"as_of": "2026-10-03"})
    rid, token, _ = await _request(people["kt1"])
    p = await links.resolve(token)
    assert p is not None
    f = (await executor.query(p, "asks.link_view", {})).output["items"][0]
    await executor.run(p, "asks.link_upload", {"item_id": f["id"], "filename": "sao ke.txt", "data_b64": base64.b64encode("Số dư SODUBIMAT".encode()).decode()})
    await ok(people["admin"], "admin.set_agent_group", group="kb.*", mode="deny")
    agent = await executor.query(people["kt1"].as_agent(), "asks.request", {"once_link": str(rid)})
    assert agent.ok and "SODUBIMAT" not in json.dumps(agent.output, ensure_ascii=False)          # trợ lý: không tới nội dung tài liệu qua thẻ sổ sách
    assert "không được đọc tài liệu" in json.dumps(agent.output, ensure_ascii=False)
    human = await q(people["kt1"], "asks.request", once_link=str(rid))
    assert "SODUBIMAT" in json.dumps(human, ensure_ascii=False)                                   # người vẫn thấy (quyền người không đổi)
    await ok(people["admin"], "admin.set_agent_group", group="flow.*", mode="deny")
    periods = await executor.query(people["kt1"].as_agent(), "books.periods", {})
    assert periods.ok and periods.output["rows"]                                            # tắt Quy trình: bảng kỳ vẫn chạy cho trợ lý (không đọc hồ sơ)
