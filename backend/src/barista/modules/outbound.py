"""HỘP THƯ ĐI (ADR 0265 mục 3) — năng lực lõi: MỘT hàng chờ cho mọi tin công ty gửi RA NGOÀI (email trước; Zalo OA / SMS là bộ nối sau — thêm kênh
không đổi bảng: ``channel`` là chữ, bộ gửi khai ở ``_SENDERS``).

* Tin sinh ra ở trạng thái NHÁP — người soạn tay (``outbound.draft``), module khác soạn hộ bằng quyền người bấm (``outbound.compose``, vd giấy tờ đã ban
  hành "Gửi cho …"), hoặc trợ lý soạn thẻ đề xuất. Người nhận là một người / tổ chức trong danh mục (``parties``): địa chỉ CHỐT lúc soạn từ liên hệ của
  họ, đọc bằng quyền người soạn (không thấy liên hệ ⇒ không soạn được). Tiêu đề + thân từ mẫu (``{khoá}`` lấy từ ``data``), đính kèm = tệp trong sổ tệp
  (``file_id`` — 0122: module đang giữ tệp ``grant``, hộp thư đi ``adopt``, không chép thân tệp).
* GỬI luôn do NGƯỜI bấm (``outbound.send`` — trợ lý chỉ soạn thẻ đề xuất, ``agent="propose"``; công ty muốn hai người thì đặt luật duyệt cho lệnh này ở
  ``core.approval_rules`` — chuỗi duyệt sẵn có, người xin không tự duyệt). Không tin nào rời nháp mà không có người bấm (ràng buộc DB ``released_by``).
* Tin chăm sóc / tiếp thị cần sự ĐỒNG Ý hiệu lực của người nhận (họ ``consent`` của danh mục — 0112): kiểm lúc soạn VÀ lúc bấm gửi.
* Vòng gửi theo mẫu ``kernel/webhooks.py``: lượt quét mỗi phút (``deliver`` — ``sweep=``) thuê một lô (``lease_until``) trong giao dịch ngắn, GỬI NGOÀI
  giao dịch (bước ``prepare`` — 0200), ghi kết quả chỉ khi còn đúng lượt thuê của mình. Lỗi ⇒ thử lại lùi dần (1 phút × 2^n, tối đa 1 giờ); quá
  ``MAX_ATTEMPTS`` ⇒ ``dead`` + báo người đã bấm gửi; "Gửi lại" đưa về hàng chờ. Mỗi bước một dòng nhật ký (ai, lúc nào, lỗi gì).
* Khoá chống lặp ``dedupe_key`` (một khoá một tin trong công ty): soạn lại cùng việc ⇒ trả đúng tin đã có, không thành hai thư.
"""

import html
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.api import Action as A
from barista.api import Ctx, Input, Invalid, Module, NoInput, NotFound, Open, Prep, Screen, field, files, ui, zone
from barista.db import db
from barista.kernel import mailer
from barista.kernel.executor import core_ctx

log = logging.getLogger("barista.outbound")

m = Module("outbound", "Hộp thư đi", pack="base",
           resources={"outbound": "Hộp thư đi — tin gửi ra ngoài (email) chờ bấm gửi / đã gửi; quản lý = tên người gửi của công ty"})

CHANNELS = {"email": "Email"}
PURPOSE = {"transactional": "Nghiệp vụ (giấy tờ, xác nhận, nhắc hạn)", "care": "Chăm sóc", "marketing": "Tiếp thị"}
NEEDS_CONSENT = ("care", "marketing")   # mục đích cần người nhận đồng ý (khoá mục đích của danh mục cá nhân — 0112)
STATUS = {"draft": "Nháp — chờ bấm gửi", "queued": "Chờ gửi", "sent": "Đã gửi", "dead": "Không gửi được", "cancelled": "Đã huỷ"}
TONES = {"draft": "orange", "queued": "blue", "sent": "green", "dead": "red", "cancelled": "gray"}
LOG = {"drafted": "Soạn", "released": "Bấm gửi", "attempt": "Gửi lỗi", "sent": "Đã gửi", "dead": "Dừng thử", "cancelled": "Huỷ", "retried": "Gửi lại"}
MAX_ATTEMPTS = 8         # ~ 1 + 2 + 4 + … phút, tối đa 1 giờ một lần ⇒ dừng sau khoảng nửa ngày
LEASE_S = 300            # ≥ một lô xấu nhất: BATCH × (15 giây SMTP + đọc tệp)
BATCH = 10               # tin mỗi lượt quét của một công ty — vòng quét dùng chung, không giữ lâu
MAX_FILES = 10
MAX_BYTES = 20 * 1024 * 1024   # tổng đính kèm một thư (nhà cung cấp thư thường chặn ~25 MB sau mã hoá)


@dataclass(frozen=True)
class Envelope:
    """Một tin đã sẵn để gửi — bộ gửi của kênh chỉ thấy chừng này."""
    channel: str
    to_address: str
    to_name: str
    subject: str
    text: str
    html: str
    attachments: tuple[mailer.Attachment, ...]
    from_name: str
    reply_to: str


type Sender = Callable[[Envelope], Awaitable[None]]


async def _email(e: Envelope) -> None:
    await mailer.send_message(e.to_address, e.subject, e.text, html=e.html, attachments=e.attachments, reply_to=e.reply_to,
                              from_name=e.from_name, to_name=e.to_name)


_SENDERS: dict[str, Sender] = {"email": _email}
_FAKE: dict[str, Sender] = {}


def use_sender(channel: str, s: Sender | None) -> None:
    """Test: bộ gửi giả cho một kênh (không cần máy chủ thư). None = bộ gửi thật."""
    if s is None:
        _FAKE.pop(channel, None)
    else:
        _FAKE[channel] = s


def not_ready(channel: str) -> str | None:
    """Lý do kênh chưa gửi được (None = gửi được)."""
    if channel in _FAKE:
        return None
    if channel == "email":
        return None if mailer.configured() else "Bản cài chưa bật gửi email (BARISTA_SMTP_URL + BARISTA_MAIL_FROM) — nhờ người vận hành bật rồi bấm gửi lại"
    return f"Kênh {channel} chưa có bộ nối"


def render(template: str, data: dict[str, Any]) -> str:
    """``{khoá}`` ⇒ giá trị trong ``data`` (chữ). Khoá không có giữ nguyên — không đánh giá gì như mã."""
    return re.sub(r"\{([a-z_][a-z0-9_]*)\}", lambda mt: str(data[mt.group(1)]) if mt.group(1) in data else mt.group(0), template)


def html_of(text: str) -> str:
    """Thân chữ ⇒ HTML đơn giản (đoạn + xuống dòng), mọi ký tự đã thoát — không nhận HTML do người / trợ lý gõ."""
    paras = [p.strip() for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()]
    body = "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paras)
    return ('<!DOCTYPE html><html><body style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.55;color:#1f2937">'
            f"{body}</body></html>")


async def _log(ctx: Ctx, message_id: UUID, kind: str, note: str = "") -> None:
    await ctx.exec("INSERT INTO outbound.log (message_id, kind, note, actor) VALUES (%s, %s, %s, %s)", (message_id, kind, note[:500], ctx.p.user_id))


async def recipient(ctx: Ctx, party_id: UUID, purpose: str, to_email: str = "") -> dict[str, str]:
    """Tên + địa chỉ của người nhận, đọc bằng QUYỀN NGƯỜI GỌI (không thấy liên hệ ⇒ lỗi), và kiểm đồng ý khi tin là chăm sóc / tiếp thị."""
    c = await ctx.call_as_optional("parties.mail_contact", ctx.p, party_id=party_id)
    if c is None:
        raise Invalid("Không đọc được liên hệ của người nhận — cần gói Cá nhân & tổ chức và quyền xem liên hệ của người này")
    emails: list[str] = c["emails"]
    if not emails:
        raise Invalid(f"{c['name']} chưa có email — thêm liên hệ ở Cá nhân & tổ chức trước")
    want = to_email.strip().lower()
    if want and want not in emails:
        raise Invalid(f"{want} không phải email của {c['name']} — chọn một trong: {', '.join(emails)}")
    if purpose in NEEDS_CONSENT and c["consent"].get(purpose) != "granted":
        raise Invalid(f"{c['name']} chưa đồng ý nhận tin {PURPOSE[purpose].lower()} ({c['consent_labels'].get(purpose, 'chưa ghi nhận')}) — ghi sự đồng ý ở "
                      "Cá nhân & tổ chức trước, hoặc chọn mục đích “Nghiệp vụ” nếu đây là thư xác nhận / giấy tờ")
    return {"name": c["name"], "address": want or emails[0]}


# ── Soạn ──

class DraftIn(Input):
    party_id: UUID = field("Gửi cho", options_query="parties.options")
    purpose: Literal["transactional", "care", "marketing"] = field("Mục đích", "transactional", options=PURPOSE,
                                                                   help="Chăm sóc / tiếp thị cần người nhận đã đồng ý (Cá nhân & tổ chức)")
    subject: str = field("Tiêu đề", max_length=200, example="Gửi anh/chị hợp đồng đã ký")
    body: str = field("Nội dung", kind="textarea", max_length=20000)
    to_email: str = field("Email nhận", "", max_length=320, help="Trống = email đầu tiên của người nhận", group="Thêm chi tiết")


class AttachIn(Input):
    file_id: UUID
    grant_id: UUID
    filename: str = field("Tên tệp", max_length=300)


class ComposeIn(DraftIn):
    source: str = field("Nguồn", "", max_length=60)
    source_ref: str = field("Mã ở nguồn", "", max_length=100)
    dedupe_key: str = field("Khoá chống lặp", "", max_length=200)
    template: str = field("Mẫu", "", max_length=60)
    data: dict[str, Any] = field("Dữ liệu mẫu", {})
    attachments: list[AttachIn] = field("Đính kèm", [])
    drafted_by_ai: bool = False


async def _create(ctx: Ctx, inp: ComposeIn) -> dict[str, Any]:
    if inp.dedupe_key.strip():
        if (got := await ctx.val("SELECT id FROM outbound.messages WHERE dedupe_key = %s", (inp.dedupe_key.strip(),))) is not None:
            return {"message_id": got, "created": False, "summary": "Tin này đã có trong hộp thư đi"}
    if len(inp.attachments) > MAX_FILES:
        raise Invalid(f"Tối đa {MAX_FILES} tệp đính kèm một thư")
    who = await recipient(ctx, inp.party_id, inp.purpose, inp.to_email)
    data = {"ten_nguoi_nhan": who["name"], **{k: v for k, v in inp.data.items() if isinstance(v, str | int | float)}}
    subject, body = render(inp.subject, data).strip(), render(inp.body, data).strip()
    if not subject or not body:
        raise Invalid("Cần tiêu đề và nội dung")
    mid = await ctx.val(
        """INSERT INTO outbound.messages (channel, purpose, party_id, to_name, to_address, subject, body, template, data, source, source_ref, dedupe_key,
                                          created_by, drafted_by_ai)
           VALUES ('email', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id""",
        (inp.purpose, inp.party_id, who["name"][:200], who["address"], subject[:200], body, inp.template, Jsonb(inp.data), inp.source, inp.source_ref,
         inp.dedupe_key.strip() or None, ctx.p.user_id, inp.drafted_by_ai))
    if mid is None:   # cùng khoá vừa được người khác soạn (hai lần bấm cùng lúc)
        raise Invalid("Tin này vừa được soạn trong hộp thư đi — mở Hộp thư đi để xem")
    for a in inp.attachments:
        await files.adopt(ctx, a.file_id, a.grant_id)   # giữ CÙNG tệp (0249): module nguồn đã kiểm người bấm thấy tệp rồi mới cấp quyền lấy
        await ctx.exec("INSERT INTO outbound.attachments (message_id, file_id, filename) VALUES (%s, %s, %s)", (mid, a.file_id, a.filename[:300]))
    await _log(ctx, mid, "drafted", f"{who['name']} <{who['address']}>" + (f" · {len(inp.attachments)} tệp" if inp.attachments else ""))
    return {"message_id": mid, "created": True, "summary": f"Đã soạn thư “{subject[:80]}” tới {who['name']} — chờ bấm gửi"}


@m.command("draft", resource="outbound", title="Soạn thư gửi ra ngoài", agent="propose", shows=("outbound.message", "message_id"))
async def draft(ctx: Ctx, inp: DraftIn) -> dict[str, Any]:
    """Soạn MỘT thư (nháp) cho một người / tổ chức trong danh mục — địa chỉ lấy từ liên hệ của họ. Chưa gửi gì: người bấm “Gửi” ở thư mới đi.
    Chăm sóc / tiếp thị cần người nhận đã đồng ý."""
    return await _create(ctx, ComposeIn(**inp.model_dump()))


@m.command("compose", resource="outbound", title="Soạn thư gửi ra ngoài (module khác gọi)", internal=True, agent="deny")
async def compose(ctx: Ctx, inp: ComposeIn) -> dict[str, Any]:
    """Module nguồn (giấy tờ đã ban hành — nút "Gửi cho …") soạn sẵn một thư NHÁP kèm tệp của nó. CHỈ gọi bằng QUYỀN NGƯỜI BẤM (``call_as`` ctx.p —
    không hợp đồng quyền hệ thống): người nhận đọc bằng RLS của người đó, tệp đính kèm qua quyền lấy tệp module nguồn vừa cấp (``files.grant`` — đã kiểm
    người bấm thấy tệp). Không gửi gì: thư nằm ở hộp thư đi chờ người bấm Gửi. Trả mã thư + đã tạo mới hay trùng khoá chống lặp."""
    return await _create(ctx, inp)


# ── Gửi / huỷ / gửi lại (người bấm) ──

class MessageRef(Input):
    message_id: UUID = field("Thư", hidden=True)


async def _row(ctx: Ctx, message_id: UUID) -> dict[str, Any]:
    r = await ctx.one("SELECT * FROM outbound.messages WHERE id = %s FOR UPDATE", (message_id,))
    if r is None:
        raise NotFound("Không thấy thư (hoặc bạn không xem được thư này)")
    return r


@m.command("send", resource="outbound", title="Gửi thư ra ngoài", agent="propose", irreversible=True,
           purpose="Gửi một thư đã soạn trong hộp thư đi tới người nhận ngoài công ty",
           effect="Thư đi ra ngoài tới người nhận — đã gửi thì không thu hồi được")
async def send(ctx: Ctx, inp: MessageRef) -> dict[str, Any]:
    """NGƯỜI bấm gửi một thư nháp: kiểm lại kênh đã bật và sự đồng ý (chăm sóc / tiếp thị) bằng quyền người bấm, rồi xếp vào hàng gửi (vòng gửi nền gửi
    trong vòng một phút, lỗi thì tự thử lại). Trợ lý chỉ soạn thẻ đề xuất — không tự gửi."""
    r = await _row(ctx, inp.message_id)
    if r["status"] != "draft":
        raise Invalid(f"Thư đang ở trạng thái “{STATUS[r['status']]}” — chỉ gửi được thư nháp")
    if why := not_ready(r["channel"]):
        raise Invalid(why)
    if r["party_id"] is not None and r["purpose"] in NEEDS_CONSENT:
        await recipient(ctx, r["party_id"], r["purpose"], r["to_address"])   # đồng ý có thể đã rút từ lúc soạn
    await ctx.exec("""UPDATE outbound.messages SET status = 'queued', released_by = %s, released_at = now(), next_at = now(), updated_at = now()
                      WHERE id = %s""", (ctx.p.user_id, r["id"]))
    await _log(ctx, r["id"], "released")
    return {"message_id": r["id"], "summary": f"Đã xếp gửi “{r['subject'][:80]}” tới {r['to_name'] or r['to_address']}"}


@m.command("cancel", resource="outbound", title="Huỷ thư chưa gửi", agent="propose", irreversible=True,
           effect="Thư không gửi nữa — muốn gửi phải soạn lại")
async def cancel(ctx: Ctx, inp: MessageRef) -> dict[str, Any]:
    r = await _row(ctx, inp.message_id)
    if r["status"] not in ("draft", "queued", "dead"):
        raise Invalid(f"Thư đã {STATUS[r['status']].lower()} — không huỷ được")
    n = await ctx.exec("""UPDATE outbound.messages SET status = 'cancelled', updated_at = now(), lease_until = NULL
                          WHERE id = %s AND (lease_until IS NULL OR lease_until < now())""", (r["id"],))
    if not n:
        raise Invalid("Thư đang được gửi ngay lúc này — chờ một phút rồi xem lại")
    await _log(ctx, r["id"], "cancelled")
    return {"message_id": r["id"], "summary": "Đã huỷ thư"}


@m.command("retry", resource="outbound", title="Gửi lại thư lỗi", agent="propose", irreversible=True,
           effect="Thư được xếp gửi lại ra ngoài — đã gửi thì không thu hồi được")
async def retry(ctx: Ctx, inp: MessageRef) -> dict[str, Any]:
    r = await _row(ctx, inp.message_id)
    if r["status"] != "dead":
        raise Invalid("Chỉ gửi lại thư đã dừng vì lỗi")
    if why := not_ready(r["channel"]):
        raise Invalid(why)
    await ctx.exec("""UPDATE outbound.messages SET status = 'queued', attempts = 0, last_error = '', next_at = now(), lease_until = NULL,
                          released_by = %s, released_at = now(), updated_at = now() WHERE id = %s""", (ctx.p.user_id, r["id"]))
    await _log(ctx, r["id"], "retried")
    return {"message_id": r["id"], "summary": "Đã xếp gửi lại"}


class SenderIn(Input):
    from_name: str = field("Tên người gửi", "", max_length=80, example="Phòng Hành chính — Công ty Mẫu",
                           help="Tên hiện ở hộp thư người nhận. Địa chỉ gửi là địa chỉ chung của bản cài")
    reply_to: str = field("Nhận trả lời ở", "", max_length=320, kind="email", help="Người nhận bấm Trả lời ⇒ thư về địa chỉ này. Trống = không đặt")


@m.command("set_sender", resource="outbound", action="manage", title="Tên người gửi của công ty", agent="propose")
async def set_sender(ctx: Ctx, inp: SenderIn) -> dict[str, Any]:
    name, reply = " ".join(inp.from_name.split()), inp.reply_to.strip().lower()
    if re.search(r"[<>\"]", name):
        raise Invalid("Tên người gửi không chứa < > \"")
    if reply and not mailer.valid_address(reply):
        raise Invalid("Địa chỉ nhận trả lời không hợp lệ")
    await ctx.exec("""INSERT INTO outbound.sender (from_name, reply_to, updated_by) VALUES (%s, %s, %s)
                      ON CONFLICT (tenant_id) DO UPDATE SET from_name = EXCLUDED.from_name, reply_to = EXCLUDED.reply_to,
                                                            updated_by = EXCLUDED.updated_by, updated_at = now()""", (name, reply, ctx.p.user_id))
    return {"summary": f"Người gửi: {name or 'mặc định của bản cài'}" + (f" · trả lời về {reply}" if reply else "")}


# ── Xem ──

class ListIn(Input):
    status: str = field("Trạng thái", "", options={"": "Tất cả", **STATUS})


@m.query("messages", resource="outbound", title="Hộp thư đi")
async def messages(ctx: Ctx, inp: ListIn) -> dict[str, Any]:
    rows = await ctx.all("""SELECT id, status, subject, to_name, to_address, purpose, source, attempts, last_error, created_at, sent_at
                            FROM outbound.messages WHERE (%s = '' OR status = %s) ORDER BY created_at DESC LIMIT 300""", (inp.status, inp.status))
    out = [{**r, "to": f"{r['to_name']} <{r['to_address']}>" if r["to_name"] else r["to_address"]} for r in rows]
    s = await ctx.one("SELECT from_name, reply_to FROM outbound.sender")
    ready = not_ready("email")
    note = ready or ("Thư nháp chỉ đi khi có người bấm Gửi; lỗi thì tự thử lại, quá " + str(MAX_ATTEMPTS) + " lần thì dừng và báo người đã bấm gửi.")
    return ui.table(out, [ui.col("status", "", "badge", labels=STATUS, tones=TONES), ui.col("subject", "Tiêu đề", width=280), ui.col("to", "Gửi cho", width=240),
                          ui.col("purpose", "Mục đích", "badge", labels=PURPOSE), ui.col("created_at", "Soạn lúc", "datetime"),
                          ui.col("sent_at", "Gửi lúc", "datetime")],
                    title="Hộp thư đi", limit=300, empty="Chưa có thư nào — soạn thư, hoặc mở một giấy tờ đã ban hành rồi bấm “Gửi cho …”",
                    kpis=[ui.kpi("Chờ bấm gửi", sum(1 for r in rows if r["status"] == "draft"), tone="orange"),
                          ui.kpi("Không gửi được", sum(1 for r in rows if r["status"] == "dead"), tone="red")],
                    note=note + (f" Người gửi: {s['from_name']}" + (f", trả lời về {s['reply_to']}" if s["reply_to"] else "") + "." if s and s["from_name"] else ""))


@m.query("message", resource="outbound", title="Thư gửi ra ngoài")
async def message(ctx: Ctx, inp: MessageRef) -> dict[str, Any]:
    r = await ctx.one("SELECT * FROM outbound.messages WHERE id = %s", (inp.message_id,))
    if r is None:
        raise NotFound("Không thấy thư (hoặc bạn không xem được thư này)")
    att = await ctx.all("SELECT filename FROM outbound.attachments WHERE message_id = %s ORDER BY created_at", (r["id"],))
    log_rows = await ctx.all("""SELECT l.kind, l.note, l.at, u.name FROM outbound.log l LEFT JOIN core.users u ON u.id = l.actor
                                WHERE l.message_id = %s ORDER BY l.id""", (r["id"],))
    facts = [f"Gửi cho: {r['to_name']} <{r['to_address']}>", f"Mục đích: {PURPOSE[r['purpose']]}", f"Kênh: {CHANNELS.get(r['channel'], r['channel'])}",
             *([f"Đính kèm: {', '.join(a['filename'] for a in att)}"] if att else []),
             *([f"Đã thử {r['attempts']} lần — lỗi gần nhất: {r['last_error']}"] if r["last_error"] else []),
             *(["Trợ lý soạn"] if r["drafted_by_ai"] else [])]
    blocks = [ui.b_callout({"dead": "bad", "draft": "warn"}.get(r["status"], "muted"), r["subject"], r["body"], facts=facts),
              ui.b_list([{"title": LOG[x["kind"]] + (f" — {x['name']}" if x["name"] else ""),
                          "subtitle": f"{x['at'].astimezone(zone()):%d/%m %H:%M}" + (f" · {x['note']}" if x["note"] else ""),
                          "tone": "bad" if x["kind"] in ("attempt", "dead") else "muted"} for x in log_rows], "Nhật ký")]
    ref = {"message_id": str(r["id"])}
    actions = []
    if r["status"] == "draft":
        actions.append(ui.act("outbound.send", "Gửi", ref, "primary"))
    if r["status"] == "dead":
        actions.append(ui.act("outbound.retry", "Gửi lại", ref, "primary"))
    if r["status"] in ("draft", "queued", "dead"):
        actions.append(ui.act("outbound.cancel", "Huỷ thư", ref, "destructive"))
    return ui.card(r["subject"], blocks, subtitle=STATUS[r["status"]], icon="paperplane.fill", actions=actions)


m.screens("work", (
    Screen("outbox", "Hộp thư đi", "paperplane.fill", "outbound.messages", tint="blue", row_click="open",
           about="Thư công ty gửi ra ngoài: soạn sẵn, người bấm Gửi mới đi, xem đã gửi / lỗi và gửi lại",
           open=Open("outbound.message", {"message_id": "id"}, "Thư"),
           actions=(A("outbound.draft", "Soạn thư", style="primary"), A("outbound.set_sender", "Tên người gửi")),
           row_actions=(A("outbound.send", "Gửi", bind={"message_id": "id"}, when={"status": ["draft"]}, style="primary"),)),
), rank=30)


# ── Vòng gửi (mẫu kernel/webhooks.py — thuê lô, gửi ngoài giao dịch, ghi khi còn đúng lượt thuê) ──

async def _prepare_delivery(prep: Prep, inp: NoInput) -> list[dict[str, Any]]:
    """Bước chuẩn bị của lệnh (ngoài phiên ghi của lệnh): thuê một lô tin tới hạn (phiên ghi ngắn), đọc tệp đính kèm, rồi gửi từng tin — không giữ kết nối DB
    lúc chờ máy chủ thư."""
    async with db.tenant_tx(prep.p) as conn:
        cur = await conn.execute(
            """UPDATE outbound.messages SET lease_until = now() + make_interval(secs => %s)
               WHERE id IN (SELECT id FROM outbound.messages WHERE status = 'queued' AND next_at <= now() AND (lease_until IS NULL OR lease_until < now())
                            ORDER BY next_at LIMIT %s FOR UPDATE SKIP LOCKED)
               RETURNING id, channel, to_address, to_name, subject, body, lease_until""", (LEASE_S, BATCH))
        due = await cur.fetchall()
        if not due:
            return []
        cur = await conn.execute("SELECT from_name, reply_to FROM outbound.sender")
        s = await cur.fetchone() or {"from_name": "", "reply_to": ""}
        ctx = core_ctx(conn, prep.p, module=m.key)
        attach: dict[UUID, list[mailer.Attachment]] = {}
        broken: dict[UUID, str] = {}
        for a in await ctx.all("SELECT message_id, file_id, filename FROM outbound.attachments WHERE message_id = ANY(%s) ORDER BY created_at",
                               ([d["id"] for d in due],)):
            try:
                data = await files.read(ctx, a["file_id"])   # kiểm quyền như mở tệp: hộp thư đi giữ tệp (adopt) + còn dòng đính kèm trỏ tới
                attach.setdefault(a["message_id"], []).append(mailer.Attachment(a["filename"], data, files.sniff(a["filename"], data)))
            except Exception as e:   # tệp mất / hỏng ⇒ không gửi thư thiếu tệp; lỗi ghi vào nhật ký thư, thử lại theo nhịp thường
                broken[a["message_id"]] = f"Không đọc được tệp {a['filename']}: {e}"
    out = []
    for d in due:
        res = {"id": d["id"], "lease": d["lease_until"], "error": broken.get(d["id"], "")}
        parts = tuple(attach.get(d["id"], ()))
        if not res["error"] and sum(len(p.data) for p in parts) > MAX_BYTES:
            res["error"] = f"Đính kèm quá {MAX_BYTES // (1024 * 1024)} MB — bỏ bớt tệp rồi soạn lại"
        if not res["error"]:
            env = Envelope(d["channel"], d["to_address"], d["to_name"], d["subject"], d["body"], html_of(d["body"]), parts, s["from_name"], s["reply_to"])
            try:
                await (_FAKE.get(d["channel"]) or _SENDERS[d["channel"]])(env)
            except Exception as e:   # máy chủ thư từ chối / mạng / chưa cấu hình ⇒ thử lại lùi dần; chữ lỗi không chứa mật khẩu (MailError)
                log.warning("thư %s không gửi được: %r", d["id"], e)
                res["error"] = f"{type(e).__name__}: {e}"[:500]
        out.append(res)
    return out


@m.command("deliver", resource="outbound", title="Gửi thư trong hộp thư đi", internal=True, agent="deny", prepare=_prepare_delivery,
           sweep="""SELECT DISTINCT tenant_id FROM outbound.messages WHERE status = 'queued' AND next_at <= now()
                    AND (lease_until IS NULL OR lease_until < now())""")
async def deliver(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Vòng quét (quyền hệ thống): ghi kết quả lượt gửi — chỉ khi thư còn đúng lượt thuê của lượt này (hai tiến trình không ghi đè nhau)."""
    sent = failed = 0
    for x in ctx.prepared or []:
        if not x["error"]:
            n = await ctx.exec("""UPDATE outbound.messages SET status = 'sent', sent_at = now(), attempts = attempts + 1, last_error = '', lease_until = NULL,
                                      updated_at = now() WHERE id = %s AND status = 'queued' AND lease_until = %s""", (x["id"], x["lease"]))
            if n:
                sent += 1
                await _log(ctx, x["id"], "sent")
            continue
        r = await ctx.one("""UPDATE outbound.messages SET attempts = attempts + 1, last_error = %s, lease_until = NULL, updated_at = now(),
                                 status = CASE WHEN attempts + 1 >= %s THEN 'dead' ELSE 'queued' END,
                                 next_at = now() + make_interval(secs => least(3600, 60 * 2 ^ attempts))
                             WHERE id = %s AND status = 'queued' AND lease_until = %s RETURNING status, attempts, subject, released_by""",
                          (x["error"][:500], MAX_ATTEMPTS, x["id"], x["lease"]))
        if r is None:
            continue
        failed += 1
        await _log(ctx, x["id"], "attempt", f"Lần {r['attempts']}: {x['error']}")
        if r["status"] == "dead":
            await _log(ctx, x["id"], "dead", f"Dừng sau {r['attempts']} lần")
            await ctx.notify([r["released_by"]], kind="outbound", title=f"Thư không gửi được: {r['subject'][:120]}",
                             body=f"Đã thử {r['attempts']} lần — lỗi gần nhất: {x['error']}. Mở thư để gửi lại hoặc huỷ.",
                             push="Một thư gửi ra ngoài không gửi được", open={"query": "outbound.message", "input": {"message_id": str(x["id"])}},
                             key=f"outbound:dead:{x['id']}:{r['attempts']}")
    return {"sent": sent, "failed": failed}
