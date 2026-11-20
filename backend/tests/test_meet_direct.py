"""Gọi trực tiếp 1–1 (0114 bổ sung 07/11): đúng hai ghế, tín hiệu chỉ tới người kia và chỉ khi còn thấy đối tượng (kiểm ở DB mỗi lần đọc), có mặt theo
máy báo về (không đoán), người ngoài vào bằng link hẹp có hạn chỉ đúng cuộc gọi đó, trẻ em cần đủ sự đồng ý, TURN chỉ nhận mật khẩu tạm do server ký."""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import uvicorn

from barista import seed
from barista.config import settings
from barista.db import db
from barista.http_api import app
from barista.kernel import executor, links, live, ratelimit
from barista.kernel.principal import Principal, system_principal
from barista.modules import meet
from barista.modules.meet import presence_seconds, turn_credential
from tests.conftest import OWNER_URL, fresh_phone, ok, owner_val, q

PEOPLE = [("admin", "Quản trị thử", "admin", "QT", True), ("truong", "Trưởng nhóm thử", "team_lead", "KD-A", True),
          ("sale1", "Tư vấn một", "sale", "KD-A", False), ("sale2", "Tư vấn hai", "sale", "KD-A", False),
          ("ketoan", "Kế toán thử", "accountant", "QT", False)]
OFFER = {"sid": "s-a", "gen": 1, "tg": 0, "description": {"type": "offer", "sdp": "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\n"}}


@pytest.fixture(scope="module")
async def co() -> tuple[Any, str]:
    d = f"d{uuid.uuid4().hex[:6]}.demo"
    return await seed.create_tenant(f"Công ty gọi thẳng {d}", d, people=PEOPLE, packs=("crm", "talk", "meet")), d


async def _people(co: tuple[Any, str]) -> dict[str, Principal]:
    tid, d = co
    return {k: await seed.who(tid, k, d) for k, *_ in PEOPLE}


async def _lead(sale: Principal) -> dict[str, str]:
    out = await ok(sale, "crm.create_lead", name=f"Khách gọi {uuid.uuid4().hex[:4]}", phone=fresh_phone())
    return {"subject_kind": "crm.lead", "subject_id": str(out["lead_id"])}


async def _rows(p: Principal, sql: Any, params: Any = None) -> list[dict[str, Any]]:
    async with db.tenant_tx(p, read_only=True) as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()


async def _direct_call(p: dict[str, Principal], with_member: str = "", host: str = "sale1") -> tuple[dict[str, str], str]:
    subj = await _lead(p[host])
    started = await ok(p[host], "meet.start", **subj, provider="direct", with_member=with_member)
    return subj, str(started["call_id"])


async def test_two_seats_and_signals_reach_only_the_other_end_while_the_subject_is_visible(co: tuple[Any, str]) -> None:
    p = await _people(co)
    subj, cid = await _direct_call(p, with_member=p["truong"].email)
    assert (await q(p["sale1"], "meet.direct", call_id=cid))["seat"] == "a"
    assert (await executor.run(p["sale2"], "meet.join", {"call_id": cid})).status == "not_found"       # không thấy khách ⇒ không thấy cuộc gọi
    joined = await ok(p["truong"], "meet.join", call_id=cid)                                             # người được chọn lúc gọi
    assert joined["seat"] == "b" and joined["call_page"] == f"/goi/{cid}" and joined["once_open_url"].endswith(f"/goi/{cid}")
    third = await executor.run(p["admin"], "meet.join", {"call_id": cid})                               # thấy khách nhưng không phải người thứ hai
    assert third.status == "conflict" and third.error == "Cuộc gọi 1–1 đã đủ người"
    await ok(p["sale1"], "meet.signal", call_id=cid, kind="description", body=OFFER)
    got = await q(p["truong"], "meet.signals", call_id=cid, after=0)
    assert [s["kind"] for s in got["signals"]] == ["description"] and got["signals"][0]["body"] == OFFER and got["peer"]
    assert (await q(p["sale1"], "meet.signals", call_id=cid, after=0))["signals"] == []                  # không đọc lại thư mình gửi
    # quản trị thấy khách, không có ghế: không đọc được thư — kể cả đọc thẳng bảng
    assert (await executor.query(p["admin"], "meet.signals", {"call_id": cid})).status == "denied"
    assert (await _rows(p["admin"], "SELECT count(*) AS n FROM meet.signals WHERE call_id = %s", (cid,)))[0]["n"] == 0
    assert (await executor.run(p["admin"], "meet.signal", {"call_id": cid, "kind": "bye", "body": {}})).status == "denied"
    # ghi thẳng bảng giả làm ghế a, hoặc gửi tới người không ngồi ghế kia: DB chặn
    for seat, to in (("a", p["truong"].user_id), ("b", p["admin"].user_id)):
        with pytest.raises(Exception, match="row-level security"):
            async with db.tenant_tx(p["truong"]) as conn:
                await conn.execute("INSERT INTO meet.signals (call_id, from_seat, to_user, kind, body) VALUES (%s, %s, %s, 'bye', '{}')", (cid, seat, to))
    # tín hiệu không phải kênh nhắn tin: trường lạ / quá lớn bị từ chối bằng câu dễ hiểu
    bad = await executor.run(p["sale1"], "meet.signal", {"call_id": cid, "kind": "bye", "body": {"text": "chào anh"}})
    assert bad.status == "invalid" and "text" in bad.error
    big = await executor.run(p["sale1"], "meet.signal", {"call_id": cid, "kind": "description", "body": {"description": {"sdp": "x" * 20_000}}})
    assert big.status == "invalid" and big.error.startswith("Tín hiệu quá lớn")
    # đổi người phụ trách ⇒ người cũ mất cuộc gọi và thư NGAY, dù vẫn còn ghế; bên kia thấy người kia không còn (peer rỗng)
    await ok(p["truong"], "crm.assign", lead_id=subj["subject_id"], owner_email=p["sale2"].email)
    assert (await executor.query(p["sale1"], "meet.signals", {"call_id": cid})).status == "not_found"
    assert (await executor.run(p["sale1"], "meet.signal", {"call_id": cid, "kind": "bye", "body": {}})).status == "not_found"
    assert (await q(p["truong"], "meet.signals", call_id=cid))["peer"] == ""
    await ok(p["truong"], "meet.end", call_id=cid)


async def test_only_the_intended_second_person_sits_and_the_host_can_release_them(co: tuple[Any, str]) -> None:
    p = await _people(co)
    _, cid = await _direct_call(p)                                                       # khách của chính mình: chưa có người thứ hai
    r = await executor.run(p["truong"], "meet.join", {"call_id": cid})
    assert r.status == "conflict" and "chưa mời anh/chị" in r.error                     # thấy cuộc gọi nhưng không tự chiếm chỗ
    with pytest.raises(Exception, match="row-level security"):                          # kể cả ghi thẳng bảng
        async with db.tenant_tx(p["truong"]) as conn:
            await conn.execute("INSERT INTO meet.seats (call_id, seat, user_id) VALUES (%s, 'b', core.me())", (cid,))
    assert (await executor.run(p["truong"], "meet.seat_member", {"call_id": cid, "member": p["admin"].email})).status == "denied"   # không phải chủ
    assert (await executor.run(p["sale1"], "meet.seat_member", {"call_id": cid, "member": p["ketoan"].email})).status == "invalid"  # không thấy khách
    await ok(p["sale1"], "meet.seat_member", call_id=cid, member=p["truong"].email)
    assert (await ok(p["truong"], "meet.join", call_id=cid))["seat"] == "b"
    assert (await executor.run(p["sale1"], "meet.invite", {"call_id": cid, "name": "Khách", "adult_declared": True})).status == "conflict"
    await ok(p["sale1"], "meet.signal", call_id=cid, kind="hello", body={"sid": "a", "gen": 1})
    assert (await executor.run(p["truong"], "meet.release_seat", {"call_id": cid})).status == "denied"
    await ok(p["sale1"], "meet.release_seat", call_id=cid)
    assert (await executor.query(p["truong"], "meet.signals", {"call_id": cid})).status == "denied"   # mất kênh ngay
    assert (await q(p["sale1"], "meet.signals", call_id=cid))["peer"] == ""
    await ok(p["sale1"], "meet.invite", call_id=cid, name="Khách", adult_declared=True)     # chỗ trống lại
    await ok(p["sale1"], "meet.end", call_id=cid)


def test_minutes_come_from_client_reports_and_are_never_guessed() -> None:
    t0 = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
    at = lambda m: t0 + timedelta(minutes=m)   # noqa: E731
    rows = [{"who": "a", "event": "connected", "at": at(0)}, {"who": "b", "event": "connected", "at": at(1)},
            {"who": "a", "event": "connected", "at": at(2)},                                     # báo lặp khi đang nối: cùng một khoảng
            {"who": "a", "event": "disconnected", "at": at(10)}, {"who": "a", "event": "connected", "at": at(11)},
            {"who": "a", "event": "ended", "at": at(20)},
            # b khoá màn hình lúc phút 5, mở lại phút 15 mới báo được: giờ máy báo chỉ RÚT NGẮN khoảng, không kéo dài
            {"who": "b", "event": "disconnected", "at": at(15), "client_at": at(5)},
            {"who": "b", "event": "connected", "at": at(16)}]
    got = presence_seconds(rows)
    assert got["a"] == (at(0), at(20), 19 * 60, False)
    assert got["b"][2] is None and got["b"][3] is True                     # còn khoảng mở ⇒ không đo được, không phải 0
    late = presence_seconds([{"who": "c", "event": "connected", "at": at(0)}, {"who": "c", "event": "ended", "at": at(3), "client_at": at(-5)}])
    assert late["c"][2] == 3 * 60                                          # giờ máy lệch về trước lúc mở: bỏ, dùng giờ máy chủ


async def test_end_publishes_client_reported_attendance_and_outsiders(co: tuple[Any, str]) -> None:
    p = await _people(co)
    _, cid = await _direct_call(p, with_member=p["truong"].email)
    await ok(p["truong"], "meet.join", call_id=cid)
    for who in ("sale1", "truong"):
        await ok(p[who], "meet.presence", call_id=cid, event="connected")
    assert (await executor.run(p["admin"], "meet.presence", {"call_id": cid, "event": "connected"})).status == "denied"   # không có ghế
    with pytest.raises(Exception, match="row-level security"):            # báo thay người khác: DB chặn
        async with db.tenant_tx(p["truong"]) as conn:
            await conn.execute("INSERT INTO meet.presence (call_id, seat, user_id, event) VALUES (%s, 'a', %s, 'ended')", (cid, p["sale1"].user_id))
    await ok(p["sale1"], "meet.presence", call_id=cid, event="ended", detail="hangup")
    card = await q(p["truong"], "meet.call", call_id=cid)
    ends = next(b for b in card["blocks"] if b["type"] == "list")["items"]
    assert [i["subtitle"].split(" ")[0] for i in ends] == ["có", "đang"]
    await ok(p["truong"], "meet.end", call_id=cid)
    ev = await owner_val("SELECT payload FROM core.events WHERE type = 'meet.call_ended' AND payload->>'call_id' = %s", (cid,))
    att = ev["attendance"]
    assert att["source"] == "client" and att["covers_outsiders"] is True and ev["provider"] == "direct"
    secs = {x["user_id"]: x["seconds"] for x in att["people"]}
    assert secs[str(p["sale1"].user_id)] is not None and secs[str(p["truong"].user_id)] is None   # bên kia chưa báo kết thúc: không đoán
    assert (await executor.run(p["truong"], "meet.presence", {"call_id": cid, "event": "connected"})).status == "conflict"
    await ok(p["truong"], "meet.presence", call_id=cid, event="ended")     # báo muộn vẫn ghi (vết), số trong sự kiện đã chốt
    assert (await executor.run(p["sale1"], "meet.signal", {"call_id": cid, "kind": "bye", "body": {}})).status == "conflict"
    # vòng quét xoá tín hiệu (SDP / ICE có địa chỉ IP) của cuộc gọi đã xong
    await ok(p["sale1"], "meet.start", **(await _lead(p["sale1"])), provider="direct")
    assert (await executor.run(system_principal(co[0]), "meet.purge_signals", {}, quiet=True)).ok


async def test_an_outsider_link_opens_exactly_one_call_and_dies_on_revoke(co: tuple[Any, str]) -> None:
    p = await _people(co)
    _, cid = await _direct_call(p)
    other_subj, other = await _direct_call(p)
    inv = await ok(p["sale1"], "meet.invite", call_id=cid, name="Chị Lan", adult_declared=True, hours=2)
    token = inv["once_url"].rsplit("/", 1)[1]
    assert inv["once_url"].startswith(settings.web_url.rstrip("/") + "/c/goi/")
    card = await q(p["sale1"], "meet.invitation", once_invite=inv["once_invite"])
    assert token in str(card)                                              # hiện đúng lần ngay sau khi tạo
    assert token not in str(await q(p["sale1"], "meet.invitation", once_invite=str(inv["invite_id"])))
    assert token not in json.dumps(await owner_val("SELECT array_agg(input) FROM core.audit_log WHERE command = 'meet.invite'"), default=str)
    assert (await executor.run(p["truong"], "meet.join", {"call_id": cid})).error == "Cuộc gọi 1–1 đã đủ người"   # chỗ còn lại giữ cho người ngoài
    assert (await executor.run(p["sale1"], "meet.invite", {"call_id": cid, "name": "Người thứ ba", "adult_declared": True})).status == "conflict"

    guest = await links.resolve(token)
    assert guest is not None
    view = (await executor.query(guest, "meet.link_view", {})).output
    assert view["seat"] == "b" and view["polite"] is True and view["invitee"] == "Chị Lan" and view["once_ice"]["servers"]
    # chỉ đúng cuộc gọi đó: không đọc được cuộc gọi khác, thành viên, cấu hình; không chạy được lệnh của thành viên
    assert (await _rows(guest, "SELECT id FROM meet.calls")) == [{"id": uuid.UUID(cid)}]
    assert (await _rows(guest, "SELECT count(*) AS n FROM core.users"))[0]["n"] == 0
    assert (await _rows(guest, "SELECT count(*) AS n FROM meet.settings"))[0]["n"] == 0
    for name, inp in (("meet.signal", {"call_id": other, "kind": "bye", "body": {}}), ("meet.join", {"call_id": other}),
                      ("meet.end", {"call_id": cid})):
        assert (await executor.run(guest, name, inp)).status == "denied", name
    await ok(guest, "meet.link_signal", kind="hello", body={"sid": "g1", "gen": 1})
    await ok(guest, "meet.link_presence", event="connected")
    assert [s["kind"] for s in (await q(p["sale1"], "meet.signals", call_id=cid))["signals"]] == ["hello"]
    await ok(p["sale1"], "meet.signal", call_id=cid, kind="description", body=OFFER)
    assert [s["kind"] for s in (await q(guest, "meet.link_signals"))["signals"]] == ["description"]
    # nhân sự không dùng được thao tác của link
    assert (await executor.query(p["sale1"], "meet.link_view", {})).status == "denied"
    assert (await q(p["sale1"], "meet.signals", call_id=cid))["peer"]
    await ok(p["sale1"], "meet.revoke_invite", call_id=cid)
    assert await links.resolve(token) is None
    for r in (await executor.query(guest, "meet.link_signals", {}), await executor.run(guest, "meet.link_signal", {"kind": "bye", "body": {}}),
              await executor.run(guest, "meet.link_presence", {"event": "disconnected"})):
        assert r.status == "not_found"                                                   # phiên đang giữ cũng mất ngay (RLS đọc lời mời)
    assert (await q(p["sale1"], "meet.signals", call_id=cid))["peer"] == ""               # bên này được báo bỏ kết nối với họ
    # mời người khác vào chỗ đó: người mới KHÔNG đọc được thư đã gửi người cũ (thư theo người, không theo ghế)
    token2 = (await ok(p["sale1"], "meet.invite", call_id=cid, name="Người mới", adult_declared=True))["once_url"].rsplit("/", 1)[1]
    guest2 = await links.resolve(token2)
    assert guest2 is not None and (await q(guest2, "meet.link_signals"))["signals"] == []
    assert (await _rows(guest2, "SELECT count(*) AS n FROM meet.signals"))[0]["n"] == 0
    for c in (cid, other):
        await ok(p["sale1"], "meet.end", call_id=c)
    assert other_subj


async def test_a_child_needs_full_consent_before_and_during_the_call(co: tuple[Any, str]) -> None:
    p = await _people(co)
    admin = host = p["admin"]
    tag = uuid.uuid4().hex[:4]
    parent = (await ok(admin, "parties.create", display_name=f"Mẹ {tag}"))["party_id"]
    child = (await ok(admin, "parties.add_dependant", name=f"Bé {tag}", born_on=(datetime.now(UTC).date() - timedelta(days=365 * 9)).isoformat(),
                      guardian_party_id=parent))["party_id"]
    adult = (await ok(admin, "parties.create", display_name=f"Người lớn {tag}"))["party_id"]
    _, cid = await _direct_call(p, host="admin")   # chọn định danh cần quyền xem danh mục (0125 B): chủ cuộc gọi là người đọc được danh mục
    unknown = (await ok(admin, "parties.create", display_name=f"Chưa rõ tuổi {tag}"))["party_id"]
    await ok(admin, "parties.set_birth_date", party_id=adult, born_on="1990-05-01", source="CCCD")
    r = await executor.run(host, "meet.invite", {"call_id": cid, "name": "Bé", "party_id": child})
    assert r.status == "invalid" and "trẻ em" in r.error
    r = await executor.run(host, "meet.invite", {"call_id": cid, "name": "Ai đó"})          # không định danh, không xác nhận thành niên
    assert r.status == "invalid" and "có thể là trẻ em" in r.error
    r = await executor.run(host, "meet.invite", {"call_id": cid, "name": "Chưa rõ", "party_id": unknown})   # chưa biết tuổi, không giám hộ
    assert r.status == "invalid" and "ngày sinh" in r.error
    # đã chọn định danh thì xác nhận "thành niên" không thay được việc kiểm (định danh thắng)
    assert (await executor.run(host, "meet.invite", {"call_id": cid, "name": "Bé", "party_id": child, "adult_declared": True})).status == "invalid"
    assert (await executor.run(host, "meet.invite", {"call_id": cid, "name": "Người lớn", "party_id": adult})).ok   # người lớn: không chặn
    await ok(host, "meet.revoke_invite", call_id=cid)
    await ok(admin, "parties.record_consent", party_id=child, purpose="data_processing", granted=True, given_by_party_id=parent)
    await ok(admin, "parties.record_consent", party_id=child, purpose="data_processing", granted=True)   # 9 tuổi: cần cả trẻ (0112)
    inv = await ok(host, "meet.invite", call_id=cid, name="Bé", party_id=child)
    guest = await links.resolve(inv["once_url"].rsplit("/", 1)[1])
    assert guest is not None and (await executor.query(guest, "meet.link_view", {})).ok
    await ok(admin, "parties.record_consent", party_id=child, purpose="data_processing", granted=False, given_by_party_id=parent)   # rút giữa chừng
    for r in (await executor.query(guest, "meet.link_view", {}), await executor.query(guest, "meet.link_signals", {}),
              await executor.run(guest, "meet.link_presence", {"event": "connected"}), await executor.run(guest, "meet.link_presence", {"event": "ended"}),
              await executor.run(guest, "meet.link_signal", {"kind": "hello", "body": {}})):
        assert r.status == "denied" and "đồng ý" in r.error
    assert (await q(host, "meet.signals", call_id=cid))["peer"] == ""                 # bên này được báo bỏ kết nối với trẻ
    await ok(host, "meet.end", call_id=cid)


async def test_inviting_by_identity_needs_the_directory_right(co: tuple[Any, str]) -> None:
    """0125 §B: người mời không đọc được danh mục thì không dò được trạng thái đồng ý của một định danh — vẫn mời được người lớn bằng xác nhận thành niên."""
    p = await _people(co)
    stranger = (await ok(p["admin"], "parties.create", display_name=f"Người lạ {uuid.uuid4().hex[:4]}"))["party_id"]
    _, cid = await _direct_call(p)
    r = await executor.run(p["sale1"], "meet.invite", {"call_id": cid, "name": "Người lạ", "party_id": stranger})
    assert r.status == "invalid" and "không có quyền xem danh mục" in r.error
    assert await owner_val("SELECT count(*) FROM meet.invites WHERE party_id = %s", (stranger,)) == 0
    await ok(p["sale1"], "meet.invite", call_id=cid, name="Người lớn", adult_declared=True)
    await ok(p["sale1"], "meet.end", call_id=cid)


async def test_turn_credentials_are_short_tied_to_the_seat_and_rate_limited(co: tuple[Any, str], monkeypatch: pytest.MonkeyPatch) -> None:
    user, cred = turn_credential("bi-mat", ttl_s=300, tag="ab12", now=1_800_000_000)
    assert user == "1800000300:ab12"
    assert cred == base64.b64encode(hmac.new(b"bi-mat", b"1800000300:ab12", hashlib.sha1).digest()).decode()
    assert settings.turn_ttl_s <= 600                                                    # vài phút: người bị bỏ không chuyển tiếp lâu được
    monkeypatch.setattr(settings, "turn_urls", "turn:turn.vi-du.vn:3478?transport=udp, turns:turn.vi-du.vn:5349")
    monkeypatch.setattr(settings, "turn_secret", "khoa-chung-rat-dai")
    got = meet.ice_servers(tag="t", now=1_800_000_000)
    turn = got["servers"][-1]
    assert got["turn"] is True and turn["urls"] == ["turn:turn.vi-du.vn:3478?transport=udp", "turns:turn.vi-du.vn:5349"]
    assert turn["username"] == f"{1_800_000_000 + settings.turn_ttl_s}:t" and "khoa-chung" not in json.dumps(got)
    assert meet.ice_servers()["turn"] is False                                           # không có ghế ⇒ không TURN
    # tên TURN gắn cuộc gọi + ghế + người: người khác ở cùng ghế (sau khi thu hồi) có tên khác, không lộ mã người
    p = await _people(co)
    _, cid = await _direct_call(p)
    name = lambda o: o["once_ice"]["servers"][-1]["username"].split(":", 1)[1]   # noqa: E731
    mine = name(await q(p["sale1"], "meet.direct", call_id=cid))
    assert p["sale1"].user_id is not None and mine == meet.seat_tag(uuid.UUID(cid), "a", p["sale1"].user_id) and str(p["sale1"].user_id) not in mine and cid.replace("-", "")[:12] in mine
    # lấy mật khẩu quá nhiều lần ⇒ chỉ còn STUN (không lỗi trang)
    ratelimit.reset()
    seen = [(await q(p["sale1"], "meet.direct", call_id=cid))["once_ice"]["turn"] for _ in range(ratelimit.LIMITS["turn"][0] + 2)]
    assert seen[0] is True and seen[-1] is False
    ratelimit.reset()
    monkeypatch.setattr(settings, "turn_secret", "")
    assert meet.ice_servers(tag="t")["turn"] is False and all("username" not in s for s in meet.ice_servers(tag="t")["servers"])   # thiếu khoá ⇒ không TURN
    await ok(p["sale1"], "meet.end", call_id=cid)


async def test_cloudflare_turn_per_seat_drops_port_53_and_falls_back_to_stun(co: tuple[Any, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Khoá TURN Cloudflare (09/11): mỗi ghế xin một cặp tạm qua API, hạn = turn_ttl_s; cổng 53 bị lọc; token không bao giờ tới trình duyệt;
    API lỗi ⇒ chỉ STUN, trang gọi vẫn mở; không ghế ⇒ không gọi API."""
    calls: list[httpx.Request] = []
    fail = False

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if fail:
            return httpx.Response(503)
        return httpx.Response(201, json={"iceServers": [
            {"urls": ["stun:stun.cloudflare.com:3478", "stun:stun.cloudflare.com:53"]},
            {"urls": ["turn:turn.cloudflare.com:3478?transport=udp", "turn:turn.cloudflare.com:53?transport=udp",
                      "turns:turn.cloudflare.com:443?transport=tcp"], "username": "tam-user", "credential": "tam-pass"}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(meet.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(settings, "turn_cloudflare_key_id", "khoa-turn-1")
    monkeypatch.setattr(settings, "turn_cloudflare_token", "token-bi-mat")
    got = await meet.ice_for(tag="t")
    assert got["turn"] is True and got["ttl_s"] == settings.turn_ttl_s
    assert got["servers"] == [{"urls": ["stun:stun.cloudflare.com:3478"]},
                              {"urls": ["turn:turn.cloudflare.com:3478?transport=udp", "turns:turn.cloudflare.com:443?transport=tcp"],
                               "username": "tam-user", "credential": "tam-pass"}]
    req = calls[-1]
    assert req.url.path == "/v1/turn/keys/khoa-turn-1/credentials/generate-ice-servers" and req.headers["authorization"] == "Bearer token-bi-mat"
    assert json.loads(req.content) == {"ttl": settings.turn_ttl_s} and "token-bi-mat" not in json.dumps(got)
    n = len(calls)
    assert (await meet.ice_for())["turn"] is False and len(calls) == n                   # không ghế / vượt hạn mức ⇒ không xin
    # trang gọi thật dùng đường này
    p = await _people(co)
    _, cid = await _direct_call(p)
    ratelimit.reset()
    assert (await q(p["sale1"], "meet.direct", call_id=cid))["once_ice"]["servers"][-1]["username"] == "tam-user"
    fail = True
    down = (await q(p["sale1"], "meet.direct", call_id=cid))["once_ice"]
    assert down["turn"] is False and down["servers"] and all("username" not in s for s in down["servers"])   # Cloudflare lỗi ⇒ chỉ STUN
    ratelimit.reset()
    await ok(p["sale1"], "meet.end", call_id=cid)


async def test_the_assistant_cannot_signal_read_turn_or_invite_by_itself(co: tuple[Any, str]) -> None:
    p = await _people(co)
    _, cid = await _direct_call(p)
    bot = p["sale1"].as_agent()
    assert (await executor.query(bot, "meet.direct", {"call_id": cid})).status == "denied"
    assert (await executor.run(bot, "meet.signal", {"call_id": cid, "kind": "bye", "body": {}})).status == "denied"
    assert (await executor.run(bot, "meet.presence", {"call_id": cid, "event": "connected"})).status == "denied"
    assert (await executor.run(bot, "meet.invite", {"call_id": cid, "name": "Ai đó", "adult_declared": True})).status == "proposed"
    assert await owner_val("SELECT count(*) FROM meet.invites WHERE call_id = %s", (cid,)) == 0
    await ok(p["sale1"], "meet.end", call_id=cid)


# ── Qua HTTP thật: kênh tín hiệu SSE ──

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.asynccontextmanager
async def _server() -> AsyncIterator[str]:
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning"))
    task = asyncio.create_task(server.serve())
    listener = asyncio.create_task(live.listen_forever(OWNER_URL))
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.4)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        listener.cancel()
        await task


def _h(p: Principal) -> dict[str, str]:
    return {"x-dev-user": p.email or "", "x-tenant-id": str(p.tenant_id)}


_LINES: dict[int, Any] = {}   # một bộ đọc dòng cho mỗi phản hồi SSE (httpx chỉ cho đọc luồng một lần)


async def _events(resp: httpx.Response, want: int, wait_s: float = 5, *, peer: bool = False) -> list[tuple[str, dict[str, Any]]]:
    """Đọc tiếp ``want`` sự kiện của một kênh SSE (bỏ ``peer`` trừ khi hỏi), dừng ở sự kiện kết thúc hoặc hết giờ."""
    lines = _LINES.setdefault(id(resp), resp.aiter_lines())
    out: list[tuple[str, dict[str, Any]]] = []
    ev = ""

    async def read() -> None:
        nonlocal ev
        while True:
            line = await lines.__anext__()
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                if ev == "peer" and not peer:
                    continue
                out.append((ev, json.loads(line[5:])))
                if len(out) >= want or ev in ("gone", "ended", "auth", "retry"):
                    return

    with contextlib.suppress(TimeoutError, StopAsyncIteration):
        await asyncio.wait_for(read(), wait_s)
    return out


async def test_signals_flow_over_sse_and_stop_on_loss_of_access_or_end(co: tuple[Any, str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dev_auth", True)
    p = await _people(co)
    subj, cid = await _direct_call(p, with_member=p["truong"].email)
    await ok(p["truong"], "meet.join", call_id=cid)
    async with _server() as base, httpx.AsyncClient(base_url=base, timeout=10) as c:
        assert (await c.get(f"/v1/meet/calls/{cid}/stream", headers=_h(p["sale2"]))).status_code == 404
        async with c.stream("GET", f"/v1/meet/calls/{cid}/stream", headers=_h(p["truong"])) as s:
            r = await c.post(f"/v1/meet/calls/{cid}/signal", headers=_h(p["sale1"]), json={"kind": "description", "body": OFFER})
            assert r.status_code == 200, r.text
            assert (await c.post(f"/v1/meet/calls/{cid}/signal", headers=_h(p["sale1"]), json={"kind": "candidates", "body": {}})).status_code == 422
            big = await c.post(f"/v1/meet/calls/{cid}/signal", headers=_h(p["sale1"]), json={"kind": "description", "body": {"description": {"sdp": "x" * 20_000}}})
            assert big.status_code == 422 and big.json()["error"].startswith("Tín hiệu quá lớn")
            got = await _events(s, 1)
            assert got[0][0] == "signal" and got[0][1]["body"] == OFFER
            last = got[0][1]["id"]
        # tín hiệu không thành dòng nhật ký thao tác (vận chuyển); có mặt / vào / kết thúc thì có
        assert await owner_val("SELECT count(*) FROM core.audit_log WHERE command = 'meet.signal' AND status = 'ok' AND input->>'call_id' = %s", (cid,)) == 0
        # rớt mạng rồi nối lại với mốc cuối đã nhận: không mất thư gửi trong lúc rớt, không nhận lại thư cũ
        await c.post(f"/v1/meet/calls/{cid}/signal", headers=_h(p["sale1"]), json={"kind": "hello", "body": {"sid": "s-a", "gen": 2}})
        async with c.stream("GET", f"/v1/meet/calls/{cid}/stream?after={last}", headers=_h(p["truong"])) as s:
            got = await _events(s, 1)
            assert [(e, d["kind"]) for e, d in got] == [("signal", "hello")]
        # đang nghe mà người nghe mất quyền xem khách ⇒ "gone" rồi đóng; bên kia nhận "peer" rỗng ⇒ trang bỏ kết nối với người đó
        async with c.stream("GET", f"/v1/meet/calls/{cid}/stream?after={last + 1}", headers=_h(p["sale1"])) as s, \
                c.stream("GET", f"/v1/meet/calls/{cid}/stream?after=100000000", headers=_h(p["truong"])) as other:
            assert (await _events(other, 1, peer=True))[0] == ("peer", {"peer": hashlib.sha256(uuid.UUID(str(p["sale1"].user_id)).bytes).hexdigest()[:12]})
            await asyncio.sleep(0.2)
            await ok(p["truong"], "crm.assign", lead_id=subj["subject_id"], owner_email=p["sale2"].email)
            await c.post(f"/v1/meet/calls/{cid}/signal", headers=_h(p["truong"]), json={"kind": "bye", "body": {"sid": "s-b"}})
            assert [e for e, _ in await _events(s, 1)] == ["gone"]
            assert await _events(other, 1, peer=True) == [("peer", {"peer": ""})]
        # kết thúc ⇒ "ended" cho người còn nghe
        async with c.stream("GET", f"/v1/meet/calls/{cid}/stream?after=100000000", headers=_h(p["truong"])) as s:
            await asyncio.sleep(0.2)
            await ok(p["truong"], "meet.end", call_id=cid)
            assert [e for e, _ in await _events(s, 1)] == ["ended"]


async def test_an_outsider_signals_over_the_public_link_routes(co: tuple[Any, str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dev_auth", True)
    p = await _people(co)
    _, cid = await _direct_call(p)
    token = (await ok(p["sale1"], "meet.invite", call_id=cid, name="Học viên", adult_declared=True))["once_url"].rsplit("/", 1)[1]
    async with _server() as base, httpx.AsyncClient(base_url=base, timeout=10) as c:
        view = (await c.get(f"/v1/public/links/{token}")).json()["output"]
        assert view["seat"] == "b" and view["company"]
        async with c.stream("GET", f"/v1/public/links/{token}/stream?after={view['cursor']}") as s:
            await c.post(f"/v1/meet/calls/{cid}/signal", headers=_h(p["sale1"]), json={"kind": "description", "body": OFFER})
            got = await _events(s, 1)
            assert got[0][1]["body"] == OFFER
        assert (await c.post(f"/v1/public/links/{token}/signal", json={"kind": "hello", "body": {"sid": "g"}})).status_code == 200
        assert (await c.post(f"/v1/public/links/{token}/presence", json={"event": "connected"})).status_code == 200
        assert [s["kind"] for s in (await q(p["sale1"], "meet.signals", call_id=cid))["signals"]] == ["hello"]
        # thu hồi trong lúc cả hai đang nghe: người ngoài nhận "gone" ngay, bên mời nhận "peer" rỗng (trang đóng kết nối, không chờ hết hạn)
        async with c.stream("GET", f"/v1/public/links/{token}/stream?after=100000000") as g, \
                c.stream("GET", f"/v1/meet/calls/{cid}/stream?after=100000000", headers=_h(p["sale1"])) as h:
            assert (await _events(h, 1, peer=True))[0][1]["peer"]
            await asyncio.sleep(0.2)
            await ok(p["sale1"], "meet.revoke_invite", call_id=cid)
            assert [e for e, _ in await _events(g, 1)] == ["gone"]
            assert await _events(h, 1, peer=True) == [("peer", {"peer": ""})]
        assert (await c.get(f"/v1/public/links/{token}/stream")).status_code == 404
        assert (await c.post(f"/v1/public/links/{token}/signal", json={"kind": "bye", "body": {}})).status_code == 404
    await ok(p["sale1"], "meet.end", call_id=cid)
