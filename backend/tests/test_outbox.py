"""HỘP THƯ ĐI (ADR 0265 mục 3): thư nháp không bao giờ tự đi — NGƯỜI bấm gửi (trợ lý chỉ soạn thẻ); chống lặp; vòng gửi thử lại lùi dần rồi ``dead``;
thiếu sự đồng ý thì chặn tin chăm sóc / tiếp thị; giấy tờ đã ban hành "Gửi cho …" kèm tệp ban hành; email có HTML + đính kèm + Reply-To + tên người
gửi theo công ty (bộ gửi giả — không máy chủ thư thật)."""

import uuid
from collections.abc import Iterator
from email.message import EmailMessage
from typing import Any
from uuid import UUID

import pytest

from barista import seed
from barista.kernel import executor, mailer
from barista.kernel.principal import Principal, system_principal
from barista.modules import outbound
from tests.conftest import ok, owner_val, q

PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("nv", "Nhân viên", "staff", "QT", False)]


async def _company() -> tuple[UUID, dict[str, Principal]]:
    d = f"thu{uuid.uuid4().hex[:6]}.test"
    tid = await seed.create_tenant(f"Công ty thư {d}", d, people=PEOPLE, packs=("directory",), about="thử hộp thư đi")
    return tid, {k[0]: await seed.who(tid, k[0], d) for k in PEOPLE}


async def _party(admin: Principal, email: str = "") -> str:
    pid = str((await ok(admin, "parties.create", display_name=f"Bên nhận {uuid.uuid4().hex[:4]}", kind="organization"))["party_id"])
    await ok(admin, "parties.add_identifier", party_id=pid, kind="email", value=email or f"nhan-{uuid.uuid4().hex[:6]}@vi-du.test")
    return pid


@pytest.fixture
def outbox() -> Iterator[list[outbound.Envelope]]:
    sent: list[outbound.Envelope] = []

    async def fake(e: outbound.Envelope) -> None:
        sent.append(e)

    outbound.use_sender("email", fake)
    yield sent
    outbound.use_sender("email", None)


async def _deliver(tid: UUID) -> dict[str, int]:
    r = await executor.run(system_principal(tid), "outbound.deliver", {})
    assert r.status == "ok", r.error
    return r.output or {"sent": 0, "failed": 0}


async def _msg(mid: Any) -> dict[str, Any]:
    return await owner_val("SELECT row_to_json(m) FROM outbound.messages m WHERE id = %s", (mid,))


async def _log(mid: Any) -> list[str]:
    return await owner_val("SELECT coalesce(json_agg(kind ORDER BY id), '[]') FROM outbound.log WHERE message_id = %s", (mid,))


async def test_a_draft_never_leaves_until_a_person_presses_send(outbox: list[outbound.Envelope]) -> None:
    tid, pp = await _company()
    admin = pp["admin"]
    pid = await _party(admin, "ke-toan@vi-du.test")
    out = await ok(admin, "outbound.draft", party_id=pid, subject="Xác nhận lịch hẹn", body="Kính gửi {ten_nguoi_nhan},\n\nHẹn gặp thứ Hai.")
    mid = out["message_id"]
    m = await _msg(mid)
    assert m["status"] == "draft" and m["to_address"] == "ke-toan@vi-du.test" and m["body"].startswith("Kính gửi Bên nhận")   # địa chỉ chốt từ liên hệ
    assert await _deliver(tid) == {"sent": 0, "failed": 0} and outbox == []                   # nháp: vòng gửi không đụng
    proposed = await executor.run(admin.as_agent(), "outbound.send", {"message_id": str(mid)})
    assert proposed.status == "proposed" and (await _msg(mid))["status"] == "draft"          # trợ lý chỉ soạn thẻ — không gửi
    with pytest.raises(Exception, match="check"):                                             # DB: không tin nào rời nháp mà không có người bấm
        await owner_val("UPDATE outbound.messages SET status = 'queued' WHERE id = %s RETURNING id", (mid,))
    await ok(admin, "outbound.send", message_id=str(mid))
    m = await _msg(mid)
    assert m["status"] == "queued" and m["released_by"] == str(admin.user_id)
    assert (await _deliver(tid))["sent"] == 1
    assert len(outbox) == 1 and outbox[0].to_address == "ke-toan@vi-du.test" and outbox[0].subject == "Xác nhận lịch hẹn"
    assert "<p>Kính gửi Bên nhận" in outbox[0].html and "<br>" not in outbox[0].html.split("</p>")[0]
    assert (await _msg(mid))["status"] == "sent" and await _log(mid) == ["drafted", "released", "sent"]
    assert (await _deliver(tid))["sent"] == 0 and len(outbox) == 1                           # đã gửi ⇒ không gửi lại
    r = await executor.run(admin, "outbound.send", {"message_id": str(mid)})
    assert r.status == "invalid" and "chỉ gửi được thư nháp" in r.error


async def test_failures_back_off_then_stop_and_tell_the_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    tid, pp = await _company()
    admin = pp["admin"]
    tries: list[str] = []

    async def down(e: outbound.Envelope) -> None:
        tries.append(e.subject)
        raise mailer.MailError("Máy chủ thư mail.vi-du.test không nhận thư: SMTPDataError")

    outbound.use_sender("email", down)
    monkeypatch.setattr(outbound, "MAX_ATTEMPTS", 2)
    try:
        mid = (await ok(admin, "outbound.draft", party_id=await _party(admin), subject="Thư lỗi", body="Nội dung"))["message_id"]
        await ok(admin, "outbound.send", message_id=str(mid))
        assert (await _deliver(tid))["failed"] == 1
        m = await _msg(mid)
        assert m["status"] == "queued" and m["attempts"] == 1 and "SMTPDataError" in m["last_error"]
        assert await owner_val("SELECT next_at > now() + interval '30 seconds' FROM outbound.messages WHERE id = %s", (mid,))   # lùi dần
        assert (await _deliver(tid))["failed"] == 0                                            # chưa tới giờ thử lại
        await owner_val("UPDATE outbound.messages SET next_at = now() WHERE id = %s RETURNING id", (mid,))
        await _deliver(tid)
        assert (await _msg(mid))["status"] == "dead" and len(tries) == 2
        assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND kind = 'outbound'", (admin.user_id,)) == 1
        card = await q(admin, "outbound.message", message_id=str(mid))
        assert [a["command"] for a in card["actions"]] == ["outbound.retry", "outbound.cancel"]
        sent: list[outbound.Envelope] = []

        async def up(e: outbound.Envelope) -> None:
            sent.append(e)

        outbound.use_sender("email", up)
        await ok(admin, "outbound.retry", message_id=str(mid))
        assert (await _deliver(tid))["sent"] == 1 and len(sent) == 1
        assert await _log(mid) == ["drafted", "released", "attempt", "attempt", "dead", "retried", "sent"]
    finally:
        outbound.use_sender("email", None)


async def test_care_and_marketing_need_consent_checked_at_draft_and_at_send(outbox: list[outbound.Envelope]) -> None:
    _tid, pp = await _company()
    admin = pp["admin"]
    pid = await _party(admin)
    r = await executor.run(admin, "outbound.draft", {"party_id": pid, "purpose": "marketing", "subject": "Ưu đãi", "body": "Giảm giá"})
    assert r.status == "invalid" and "chưa đồng ý" in r.error
    await ok(admin, "parties.record_consent", party_id=pid, purpose="marketing", granted=True, source="Phiếu đăng ký")
    mid = (await ok(admin, "outbound.draft", party_id=pid, purpose="marketing", subject="Ưu đãi", body="Giảm giá"))["message_id"]
    await ok(admin, "parties.record_consent", party_id=pid, purpose="marketing", granted=False, source="Khách rút")   # rút trước khi bấm gửi
    r = await executor.run(admin, "outbound.send", {"message_id": str(mid)})
    assert r.status == "invalid" and "chưa đồng ý" in r.error and (await _msg(mid))["status"] == "draft"
    assert (await ok(admin, "outbound.draft", party_id=pid, subject="Xác nhận đơn", body="Đơn đã giao"))["message_id"]   # tin giao dịch: không cần
    no_mail = str((await ok(admin, "parties.create", display_name="Không email", kind="person"))["party_id"])
    r = await executor.run(admin, "outbound.draft", {"party_id": no_mail, "subject": "x", "body": "y"})
    assert r.status == "invalid" and "chưa có email" in r.error


async def test_issued_paper_is_sent_with_its_frozen_file_once(outbox: list[outbound.Envelope]) -> None:
    tid, pp = await _company()
    admin = pp["admin"]
    pid = await _party(admin, "doi-tac@vi-du.test")
    paper = str((await ok(admin, "papers.draft_letter", recipient="Đối tác mẫu", subject="đề nghị xác nhận", body="Kính đề nghị xác nhận."))["paper_id"])
    r = await executor.run(admin, "papers.send_to", {"paper_id": paper, "party_id": pid})
    assert r.status == "invalid" and "ban hành" in r.error                                    # dự thảo không gửi ra ngoài
    await ok(admin, "papers.issue", paper_id=paper)
    view = await q(admin, "papers.view", paper_id=paper)
    assert "papers.send_to" in [a["command"] for a in view["actions"]]
    first = await ok(admin, "papers.send_to", paper_id=paper, party_id=pid, note="Gửi anh/chị bản đã ký.")
    again = await ok(admin, "papers.send_to", paper_id=paper, party_id=pid)
    assert first["message_id"] == again["message_id"]                                         # khoá chống lặp: một giấy một người nhận một thư
    m = await _msg(first["message_id"])
    assert m["source"] == "papers" and m["source_ref"] == paper and m["subject"].startswith("Công văn số ")
    assert await owner_val("SELECT count(*) FROM outbound.attachments WHERE message_id = %s", (first["message_id"],)) == 1
    await ok(admin, "outbound.send", message_id=str(first["message_id"]))
    await _deliver(tid)
    (env,) = outbox
    (att,) = env.attachments
    assert att.filename.endswith(".docx") and att.data.startswith(b"PK") and "wordprocessingml" in att.content_type
    assert env.text.startswith("Gửi anh/chị bản đã ký.") and "Tệp đính kèm" in env.text


async def test_sender_name_and_reply_to_are_company_settings_and_need_email_on(monkeypatch: pytest.MonkeyPatch) -> None:
    tid, pp = await _company()
    admin, nv = pp["admin"], pp["nv"]
    r = await executor.run(nv, "outbound.set_sender", {"from_name": "Ai đó", "reply_to": ""})
    assert r.status == "denied"                                                               # quản lý người gửi = quyền manage
    r = await executor.run(admin, "outbound.set_sender", {"from_name": "Phòng HC", "reply_to": "a@b.test, c@d.test"})
    assert r.status == "invalid"
    await ok(admin, "outbound.set_sender", from_name="Phòng Hành chính", reply_to="hanh-chinh@vi-du.test")
    sent: list[outbound.Envelope] = []

    async def fake(e: outbound.Envelope) -> None:
        sent.append(e)

    mid = (await ok(admin, "outbound.draft", party_id=await _party(admin), subject="Chào", body="Nội dung"))["message_id"]
    monkeypatch.setattr(mailer, "configured", lambda: False)
    r = await executor.run(admin, "outbound.send", {"message_id": str(mid)})
    assert r.status == "invalid" and "chưa bật gửi email" in r.error                          # bản cài chưa bật email ⇒ không cho bấm gửi
    outbound.use_sender("email", fake)
    try:
        await ok(admin, "outbound.send", message_id=str(mid))
        await _deliver(tid)
    finally:
        outbound.use_sender("email", None)
    assert sent[0].from_name == "Phòng Hành chính" and sent[0].reply_to == "hanh-chinh@vi-du.test"
    assert (await q(nv, "outbound.messages"))["rows"] == []                                   # nhân viên chỉ thấy thư của mình (phạm vi own)


def test_mailer_builds_html_attachments_reply_to_and_company_sender_name(monkeypatch: pytest.MonkeyPatch) -> None:
    from barista.config import settings

    monkeypatch.setattr(settings, "mail_from", "Barista <no-reply@vi-du.test>")
    msg = mailer.build("nhan@vi-du.test", "Tiêu đề", "Chữ thuần", html="<p>HTML</p>", reply_to="tra-loi@vi-du.test", from_name="Công ty Mẫu",
                       to_name="Bên nhận", attachments=[mailer.Attachment("hop-dong.pdf", b"%PDF-1.4 x", "application/pdf"),
                                                        mailer.Attachment("ghi-chu.txt", b"abc", "text/plain; charset=utf-8")])
    assert "no-reply@vi-du.test" in msg["From"] and "Công ty Mẫu" in str(msg["From"]) and msg["Reply-To"] == "tra-loi@vi-du.test"
    parts = list(msg.iter_parts())
    assert parts[0].get_content_type() == "multipart/alternative"
    assert [p.get_content_type() for p in parts[0].iter_parts()] == ["text/plain", "text/html"]
    assert [(p.get_filename(), p.get_content_type()) for p in parts[1:]] == [("hop-dong.pdf", "application/pdf"), ("ghi-chu.txt", "text/plain")]
    with pytest.raises(mailer.MailError):
        mailer.build("a@b.test, c@d.test", "x", "y")                                         # một ô, một người — không gửi nhiều người lén
    with pytest.raises(mailer.MailError):
        mailer.build("a@b.test", "x", "y", reply_to="x\nBcc: z@y.test")
    plain = mailer.build("nhan@vi-du.test", "Mã đăng nhập", "123456")                         # đường mã đăng nhập: chữ thuần như cũ
    assert isinstance(plain, EmailMessage) and plain.get_content_type() == "text/plain" and plain["Reply-To"] is None
    assert "&lt;b&gt;" in outbound.html_of("<b>x</b>") and outbound.render("Chào {ten}, {la}", {"ten": "An"}) == "Chào An, {la}"
