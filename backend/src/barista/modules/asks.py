"""YÊU CẦU GỬI BÊN NGOÀI QUA LINK (quyết định 0075) — năng lực lõi: gửi người NGOÀI công ty (khách, người thụ hưởng, nhà cung cấp, người
làm thuê ngoài…) một link nộp giấy tờ / trả lời câu hỏi, có hạn; hệ thống nhắc người gửi khi gần / quá hạn; bên kia nộp ⇒ sự kiện để gói sở hữu
đối tượng tự phản ứng.

* Yêu cầu GẮN VÀO MỘT ĐỐI TƯỢNG (``registry.SUBJECTS`` — như Trao đổi, 0046): ai thấy đối tượng thì thấy yêu cầu (kiểm ở DB lúc truy cập).
  Gói ngành không có bảng yêu cầu riêng: khai đối tượng + nghe ``asks.item_answered`` (lọc theo ``subject_kind``, đọc ``ref`` của mục).
* Link: 0064 (mã 256 bit, chỉ lưu mã băm, có hạn, thu hồi được; người qua link chỉ thấy / làm đúng yêu cầu này, không trợ lý AI). Link trả
  MỘT lần (khoá ``once_link``) — không lưu ở đâu cả, nên công văn / nhắc hạn không chứa link; mất ⇒ "Tạo lại link".
* Câu trả lời / file bên kia gửi là của BÊN KIA: chờ người gửi nhận (``accept_items``) mới thành căn cứ — trừ khi gói tự đưa vào quy trình
  có cửa kiểm của người phía sau (vd bản sửa chứng từ ⇒ kiểm lại ⇒ người quyết từng sai biệt).
"""

import re
from datetime import date
from typing import Any, Literal
from uuid import UUID

from barista.api import (
    Conflict,
    Ctx,
    Day,
    Denied,
    Desk,
    Input,
    Invalid,
    Line,
    Module,
    NoInput,
    NotFound,
    Open,
    Screen,
    field,
    kpi,
    line,
    links,
    num,
    today,
    ui,
)
from barista.config import settings
from barista.kernel import registry

m = Module("asks", "Yêu cầu gửi bên ngoài", pack="base", resources={"asks": "Yêu cầu gửi bên ngoài qua link (nộp giấy tờ / trả lời, có hạn)"})

LINK = "asks.request"
ITEM_KIND = {"file": "Nộp tệp", "choice": "Chọn một", "text": "Trả lời"}
ITEM_STATUS = {"open": "Chờ bên nhận", "answered": "Đã gửi — chờ nhận", "accepted": "Đã nhận"}
ITEM_TONES = {"open": "orange", "answered": "blue", "accepted": "green"}
MAX_ITEMS = 30
MAX_FILES_PER_ITEM = 20
UPLOAD_TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".heic": "image/heic",
                ".webp": "image/webp", ".xml": "application/xml", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".xls": "application/vnd.ms-excel", ".csv": "text/csv", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".zip": "application/zip", ".txt": "text/plain"}
REMIND_BEFORE = 1   # nhắc người gửi từ 1 ngày trước hạn, mỗi ngày một lần cho tới khi đủ / đóng


def parse_items(text: str) -> list[dict[str, Any]]:
    """Mỗi dòng một mục. Cuối dòng ``[tệp]`` ⇒ nộp file; ``[chọn: A; B; C]`` ⇒ bấm chọn; không ghi ⇒ gõ trả lời."""
    out: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("-•*").strip()
        if not line:
            continue
        if mf := re.search(r"\[\s*(t[ệe]p|file)\s*\]\s*$", line, flags=re.I):
            out.append({"kind": "file", "prompt": line[:mf.start()].strip(), "options": []})
        elif mc := re.search(r"\[\s*ch[ọo]n\s*:\s*([^\]]+)\]\s*$", line, flags=re.I):
            out.append({"kind": "choice", "prompt": line[:mc.start()].strip(), "options": [o.strip() for o in mc.group(1).split(";") if o.strip()]})
        else:
            out.append({"kind": "text", "prompt": line, "options": []})
    return out


def _check_items(items: list[dict[str, Any]]) -> None:
    if not items:
        raise Invalid("Cần ít nhất một mục")
    if len(items) > MAX_ITEMS:
        raise Invalid(f"Tối đa {MAX_ITEMS} mục một yêu cầu")
    for it in items:
        if not it["prompt"].strip():
            raise Invalid("Có mục thiếu nội dung câu hỏi / giấy tờ")
        if it["kind"] == "choice" and not 2 <= len(it["options"]) <= 8:
            raise Invalid(f"“{it['prompt']}”: cần 2–8 lựa chọn, cách nhau bằng dấu ;")


def link_url(token: str) -> str:
    return f"{settings.web_url.rstrip('/')}/c/{token}"


async def _subject(ctx: Ctx, kind: str, sid: UUID) -> dict[str, Any]:
    """Đối tượng như NGƯỜI GỌI thấy — không thấy / loại lạ / gói của đối tượng tắt ⇒ NotFound (không gửi yêu cầu cho thứ mình không thấy)."""
    if kind not in registry.SUBJECTS:
        raise NotFound("Loại đối tượng không gửi yêu cầu được")
    try:
        return await ctx.call_as(registry.SUBJECTS[kind][1], ctx.p, subject_id=sid)
    except (NotFound, Denied) as e:
        raise NotFound("Không thấy đối tượng (hoặc bạn không có quyền xem)") from e


async def _shelf(ctx: Ctx, key: str) -> str:
    opts = [o["value"] for o in await ctx.call("kb.upload_shelves")]
    if not opts:
        raise Invalid("Bạn chưa thêm được tài liệu vào kệ nào — không có chỗ nhận file bên kia nộp")
    if key and key not in opts:
        raise NotFound(f"Không có kệ {key} (hoặc bạn không thêm tài liệu vào kệ đó được)")
    return key or opts[0]


async def _open(ctx: Ctx, *, subject_kind: str, subject_id: UUID, subject_title: str, title: str, recipient: str, note: str, shelf: str,
                due_on: date | None, days: int, items: list[dict[str, Any]]) -> dict[str, Any]:
    _check_items(items)
    if due_on is not None and due_on < today():
        raise Invalid("Hạn nộp đã qua")
    rid = await ctx.val("""INSERT INTO asks.requests (subject_kind, subject_id, subject_title, title, recipient, note, shelf, due_on, owner_id, team_id)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (subject_kind, subject_id, subject_title, title.strip(), recipient.strip(), note.strip(), shelf, due_on, ctx.p.user_id,
                         await ctx.home_team(ctx.p.user_id)))
    for i, it in enumerate(items, 1):
        await ctx.exec("INSERT INTO asks.items (request_id, seq, kind, prompt, options, ref) VALUES (%s, %s, %s, %s, %s, %s)",
                       (rid, i, it["kind"], it["prompt"].strip(), it.get("options") or [], it.get("ref") or ""))
    # link sống tới hạn nộp (+3 ngày cho bản nộp sát giờ), không quá số ngày người gửi chọn
    life = min(days, (due_on - today()).days + 3) if due_on else days
    token = await links.issue(ctx, LINK, rid, days=max(life, 1))
    return {"request_id": rid, "once_link": f"{rid}:{token}",
            "summary": f"Yêu cầu “{title.strip()}” gửi {recipient.strip() or 'bên ngoài'} ({len(items)} mục"
                       + (f", hạn {due_on:%d/%m/%Y})" if due_on else ")")}


class CreateRequest(Input):
    subject_kind: str = field("Loại đối tượng", hidden=True)
    subject_id: UUID = field("Đối tượng", hidden=True)
    title: str = field("Tiêu đề", max_length=200, example="Chứng từ bổ sung hồ sơ vay")
    recipient: str = field("Gửi ai", "", max_length=200, help="Tên bên nhận (người ngoài công ty) — in trên trang link và lời nhắn")
    items: str = field("Cần gửi / trả lời", kind="textarea", max_length=6000,
                       example="- Sao kê tài khoản 3 tháng gần nhất [tệp]\n- Mục đích vay? [chọn: Mua nhà; Kinh doanh; Khác]",
                       help="Mỗi dòng một mục. Cuối dòng: [tệp] = nộp file; [chọn: A; B; C] = bấm chọn; không ghi = gõ trả lời")
    note: str = field("Lời nhắn", "", kind="textarea", max_length=1000)
    due_on: Day | None = field("Hạn nộp", None, help="Có hạn ⇒ hệ thống nhắc bạn từ 1 ngày trước hạn nếu chưa đủ")
    shelf: str = field("Kệ lưu file nhận về", "", options_query="kb.upload_shelves", help="Trống = kệ đầu tiên bạn thêm được")
    days: int = field("Link dùng được tối đa (ngày)", 14, ge=1, le=60)


@m.command("create", resource="asks", title="Gửi yêu cầu ra ngoài (link)", shows=("asks.request", "once_link"))
async def create(ctx: Ctx, inp: CreateRequest) -> dict[str, Any]:
    """Yêu cầu + link gửi bên ngoài, gắn vào một đối tượng người gửi thấy được. Link chỉ trả MỘT lần."""
    subj = await _subject(ctx, inp.subject_kind, inp.subject_id)
    return await _open(ctx, subject_kind=inp.subject_kind, subject_id=inp.subject_id, subject_title=subj["title"], title=inp.title,
                       recipient=inp.recipient, note=inp.note, shelf=await _shelf(ctx, inp.shelf), due_on=inp.due_on, days=inp.days,
                       items=parse_items(inp.items))


class ItemIn(Input):
    kind: Literal["file", "choice", "text"]
    prompt: str = field("Nội dung", max_length=1000)
    options: list[str] = field("Lựa chọn", [])
    ref: str = field("Khoá của gói", "", max_length=100)


class OpenRequest(Input):
    subject_kind: str
    subject_id: UUID
    subject_title: str = field("Tên đối tượng", max_length=300)
    title: str = field("Tiêu đề", max_length=200)
    recipient: str = field("Gửi ai", "", max_length=200)
    note: str = field("Lời nhắn", "", max_length=4000)
    items: list[ItemIn]
    due_on: Day | None = None
    shelf: str = field("Kệ", "", max_length=100)
    days: int = field("Link dùng được tối đa (ngày)", 30, ge=1, le=60)


@m.command("open", resource="asks", title="Mở yêu cầu gửi ra ngoài (cho gói khác)", internal=True, agent="deny", callers=("flow",), family="outreach",
           why="Gói đã kiểm người gọi thấy đối tượng (flow: người xin việc nhờ đã duyệt; gói ngoài khai consumes(\"outreach\"): đối tượng của gói) và tự soạn mục: tạo "
               "yêu cầu + link một lần, người gửi = người gọi, kệ nhận file = kệ người gọi thêm được (kiểm bằng quyền người gọi). Trả mã yêu cầu + "
               "link một lần.")
async def open_request(ctx: Ctx, inp: OpenRequest) -> dict[str, Any]:
    """Hợp đồng HẸP cho gói khác: gói đã kiểm người gọi thấy đối tượng, tự soạn mục (kèm ``ref`` của gói để nhận lại trong sự kiện).
    Người gửi = người gọi (nhận thông báo, nhắc hạn); kệ trống = kệ đầu tiên người gọi thêm được."""
    if inp.subject_kind not in registry.SUBJECTS:
        raise Invalid("Loại đối tượng chưa khai")
    shelf = inp.shelf
    if not shelf:
        who = await ctx.principal_of(ctx.p.user_id) if ctx.p.user_id else None
        opts = await ctx.call_as("kb.upload_shelves", who) if who else []
        if not opts:
            raise Invalid("Người gửi chưa thêm được tài liệu vào kệ nào — không có chỗ nhận file")
        shelf = opts[0]["value"]
    return await _open(ctx, subject_kind=inp.subject_kind, subject_id=inp.subject_id, subject_title=inp.subject_title, title=inp.title,
                       recipient=inp.recipient, note=inp.note, shelf=shelf, due_on=inp.due_on, days=inp.days,
                       items=[i.model_dump() for i in inp.items])


class RequestRef(Input):
    request_id: UUID = field("Yêu cầu", hidden=True)


class RequestDays(RequestRef):
    days: int = field("Link dùng được (ngày)", 14, ge=1, le=60)


@m.command("relink", resource="asks", title="Tạo lại link", agent="deny", shows=("asks.request", "once_link"))
async def relink(ctx: Ctx, inp: RequestDays) -> dict[str, Any]:
    """Link mới; link cũ mất hiệu lực NGAY (gửi nhầm người / hết hạn / quên copy)."""
    r = await ctx.one("SELECT id, status, title FROM asks.requests WHERE id = %s FOR UPDATE", (inp.request_id,))
    if r is None:
        raise NotFound("Không thấy yêu cầu")
    if r["status"] != "open":
        raise Conflict("Yêu cầu đã đóng")
    token = await links.issue(ctx, LINK, r["id"], days=inp.days)
    return {"request_id": r["id"], "once_link": f"{r['id']}:{token}", "summary": f"Link mới cho “{r['title']}” — link cũ đã mất hiệu lực"}


async def _close(ctx: Ctx, rid: UUID) -> str | None:
    title = await ctx.val("UPDATE asks.requests SET status = 'closed', closed_at = now() WHERE id = %s AND status = 'open' RETURNING title", (rid,))
    if title is not None:
        await links.revoke(ctx, LINK, rid)
    return title


@m.command("close",
           purpose="Yêu cầu đã xong việc hoặc không cần nữa — đóng và thu hồi link.", irreversible=True, effect="Đóng yêu cầu và thu hồi link: người nhận bấm link cũ sẽ báo hết hiệu lực, không mở lại được. Câu trả lời và tệp đã nhận giữ nguyên.",
           resource="asks", title="Đóng yêu cầu", agent="deny")
async def close(ctx: Ctx, inp: RequestRef) -> dict[str, Any]:
    """Đóng + thu hồi link (bấm link cũ ⇒ báo hết hiệu lực). Câu trả lời / file đã nhận giữ nguyên."""
    title = await _close(ctx, inp.request_id)
    if title is None:
        raise NotFound("Không thấy yêu cầu đang mở")
    return {"request_id": inp.request_id, "summary": f"Đã đóng “{title}” và thu hồi link", "subject": {"label": f"Yêu cầu “{title}”", "open": None}}


class SubjectRef(Input):
    subject_kind: str
    subject_id: UUID


@m.command("close_subject", resource="asks", title="Đóng mọi yêu cầu của một đối tượng", internal=True, agent="deny", family="outreach",
           why="Gói (khai consumes(\"outreach\")) kết luận đối tượng của nó (đã khoá bằng RLS của gói): đóng MỌI yêu cầu còn mở của đúng đối tượng đó + thu hồi "
               "link, kể cả yêu cầu người khác gửi. Trả số yêu cầu đã đóng.")
async def close_subject(ctx: Ctx, inp: SubjectRef) -> dict[str, Any]:
    """Hợp đồng cho gói: đối tượng xong việc (kết luận, huỷ…) ⇒ đóng yêu cầu còn mở + thu hồi link — không để link sống quá việc."""
    rids = [r["id"] for r in await ctx.all("SELECT id FROM asks.requests WHERE subject_kind = %s AND subject_id = %s AND status = 'open'",
                                           (inp.subject_kind, inp.subject_id))]
    for rid in rids:
        await _close(ctx, rid)
    return {"closed": len(rids)}


class AcceptItems(RequestRef):
    seqs: list[int] = field("Các mục nhận (số thứ tự)", help="Số thứ tự mục trong yêu cầu, vd [1, 2]")
    note: str = field("Đã đối chiếu", "", max_length=500, help="Ghi ngắn đã đối chiếu gì")


@m.command("accept_items", resource="asks", title="Nhận các mục bên ngoài đã gửi")
async def accept_items(ctx: Ctx, inp: AcceptItems) -> dict[str, Any]:
    """Người gửi đã xem và nhận — từ đây mới dùng làm căn cứ. Trợ lý chỉ soạn đề xuất sau khi đối chiếu; người bấm."""
    rows = await ctx.all("""UPDATE asks.items SET status = 'accepted', accepted_by = core.me(), accepted_at = now()
                            WHERE request_id = %s AND seq = ANY(%s) AND status = 'answered' RETURNING seq, prompt""", (inp.request_id, inp.seqs))
    if not rows:
        raise Invalid("Không có mục nào bên kia đã gửi mà chưa nhận trong danh sách này")
    rows.sort(key=lambda r: r["seq"])
    return {"accepted": len(rows), "summary": f"Nhận {len(rows)} mục: " + "; ".join(f"{r['seq']}. {r['prompt']}" for r in rows)
                                              + (f" — {inp.note.strip()}" if inp.note.strip() else "")}


# ── Xem ──

def _due_label(due: date | None, left: int) -> str:
    if due is None or not left:
        return ""
    d = (due - today()).days
    return f"Quá hạn {-d} ngày" if d < 0 else "Hạn hôm nay" if d == 0 else "Hạn ngày mai" if d == 1 else ""


class ListIn(Input):
    subject_kind: str = field("Loại đối tượng", "", hidden=True)
    subject_id: UUID | None = field("Đối tượng", None, hidden=True)


@m.query("requests", resource="asks", title="Yêu cầu gửi bên ngoài")
async def requests(ctx: Ctx, inp: ListIn) -> dict[str, Any]:
    rows = await ctx.all(
        """SELECT r.id, r.title, r.recipient, r.subject_kind, r.subject_title, r.status, r.due_on, r.created_at, u.name AS owner,
                  count(i.id) AS items, count(i.id) FILTER (WHERE i.status <> 'open') AS answered,
                  count(i.id) FILTER (WHERE i.status = 'answered') AS to_review
           FROM asks.requests r LEFT JOIN core.users u ON u.id = r.owner_id LEFT JOIN asks.items i ON i.request_id = r.id
           WHERE (%(k)s = '' OR r.subject_kind = %(k)s) AND (%(s)s::uuid IS NULL OR r.subject_id = %(s)s::uuid)
           GROUP BY r.id, u.name ORDER BY r.status, r.due_on NULLS LAST, r.created_at DESC LIMIT 300""",
        {"k": inp.subject_kind, "s": inp.subject_id})
    for r in rows:
        r["progress"] = f"{r['answered']}/{r['items']}"
        r["subject"] = f"{registry.SUBJECTS.get(r['subject_kind'], ('',))[0]} · {r['subject_title']}".strip(" ·")
        due = _due_label(r["due_on"], r["items"] - r["answered"]) if r["status"] == "open" else ""
        r["flag"] = "Cần xem" if r["to_review"] else due
    late = sum(r["flag"].startswith("Quá hạn") for r in rows)
    return ui.table(rows, [
        ui.col("flag", "", "badge", tones={"Cần xem": "blue", "Hạn hôm nay": "orange", "Hạn ngày mai": "orange"}),
        ui.col("title", "Yêu cầu"), ui.col("recipient", "Gửi"), ui.col("subject", "Thuộc"), ui.col("progress", "Đã gửi"),
        ui.col("due_on", "Hạn", "date"), ui.col("owner", "Người gửi"),
        ui.col("status", "", "badge", labels={"open": "Đang mở", "closed": "Đã đóng"}, tones={"open": "green", "closed": "gray"}),
    ], kpis=[ui.kpi("Đang mở", sum(r["status"] == "open" for r in rows)), ui.kpi("Cần xem", sum(bool(r["to_review"]) for r in rows)),
             ui.kpi("Quá hạn", late, tone="red" if late else None)],
        title="Yêu cầu gửi bên ngoài", empty="Chưa gửi yêu cầu nào ra ngoài", limit=300)


class RequestView(Input):
    once_link: str = field("Yêu cầu", hidden=True, max_length=200)   # "<id>" hoặc "<id>:<mã link>" ngay sau khi tạo (mã kiểm lại, không tin app)


@m.query("request", resource="asks", title="Yêu cầu gửi bên ngoài")
async def request(ctx: Ctx, inp: RequestView) -> dict[str, Any]:
    rid_s, _, token = inp.once_link.partition(":")
    try:
        rid = UUID(rid_s)
    except ValueError as e:
        raise NotFound("Không thấy yêu cầu") from e
    r = await ctx.one("SELECT * FROM asks.requests WHERE id = %s", (rid,))
    if r is None:
        raise NotFound("Không thấy yêu cầu (hoặc bạn không có quyền xem)")
    company = await ctx.company()
    items = await ctx.all("SELECT * FROM asks.items WHERE request_id = %s ORDER BY seq", (rid,))
    left = sum(it["status"] == "open" for it in items)
    blocks: list[dict[str, Any]] = [ui.b_kpis([
        ui.kpi("Đã gửi", f"{len(items) - left}/{len(items)}", "text", tone="green" if not left else None),
        ui.kpi("Hạn nộp", r["due_on"], "date", tone="red" if r["due_on"] and left and r["due_on"] < today() else None),
        ui.kpi("Chờ nhận", sum(it["status"] == "answered" for it in items))])]
    if token and r["status"] == "open" and await links.matches(ctx, LINK, rid, token):
        url = link_url(token)
        hello = f"Kính gửi {r['recipient']}," if r["recipient"] else "Chào anh/chị,"
        due = f" trước ngày {r['due_on']:%d/%m/%Y}" if r["due_on"] else ""
        blocks.append(ui.b_callout("accent", "Link gửi bên nhận — copy gửi qua Zalo / email", "Link chỉ hiện lần này (hệ thống không lưu). Mất ⇒ bấm Tạo lại link.",
                                   draft=f"{hello} {company} cần {r['title'][:1].lower() + r['title'][1:]}{due}. Anh/chị bấm link này để gửi "
                                         f"(không cần đăng nhập, chỉ dùng cho yêu cầu này): {url}"))
    if label := _due_label(r["due_on"], left) if r["status"] == "open" else "":
        blocks.append(ui.b_callout("warn", label, f"Còn {left} mục bên nhận chưa gửi — nhắc bên nhận (link cũ mất ⇒ Tạo lại link)."))
    lines = []
    for it in items:
        ans = it["answer"] or (f"{len(it['document_ids'])} file" if it["document_ids"] else "")
        lines.append({"title": f"{it['seq']}. {it['prompt']}", "subtitle": f"{ITEM_KIND[it['kind']]} · {ITEM_STATUS[it['status']]}" + (f" · {ans}" if ans else ""),
                      "tone": ITEM_TONES[it["status"]],
                      **({"open": {"query": "kb.document", "input": {"document_id": str(it["document_ids"][-1])}}} if it["document_ids"] else {})})
    blocks.append(ui.b_list(lines, "Các mục"))
    # nội dung bên kia nộp (đọc bằng quyền NGƯỜI XEM qua kho tài liệu) — người / trợ lý đối chiếu ngay trên thẻ, không phải đi tìm
    seen = []
    for it in items:
        for doc in it["document_ids"][-3:]:
            try:
                got = await ctx.call("kb.read", document_id=doc)
            except Denied:   # trợ lý hỏi, nhóm Tài liệu tắt ở công ty này ⇒ không đưa nội dung file cho trợ lý
                seen.append(f"**{it['seq']}. {it['prompt']}** — (trợ lý không được đọc tài liệu ở công ty này — mở file để xem)")
                continue
            except NotFound:
                continue
            seen.append(f"**{it['seq']}. {it['prompt']}** — {got['title']}\n\n> " + " ".join(got["text"].split())[:500])
    if seen:
        blocks.append(ui.b_text("\n\n".join(seen), "Nội dung bên kia nộp (phần đầu)"))
    if r["note"]:
        blocks.append(ui.b_text(r["note"], "Lời nhắn đã gửi"))
    acts = [ui.act("asks.relink", "Tạo lại link", {"request_id": str(rid)}), ui.act("asks.close", "Đóng yêu cầu", {"request_id": str(rid)})] \
        if r["status"] == "open" else []
    answered = [it["seq"] for it in items if it["status"] == "answered"]
    if answered:
        acts.append(ui.act("asks.accept_items", f"Nhận {len(answered)} mục đã gửi", {"request_id": str(rid), "seqs": answered}, "primary"))
    try:
        target = (await _subject(ctx, r["subject_kind"], r["subject_id"])).get("open")
    except NotFound:
        target = None
    subject = registry.SUBJECTS[r["subject_kind"]][0]
    return ui.card(r["title"], blocks, icon="paperplane", actions=acts,
                   subtitle=" · ".join(x for x in (f"Gửi {r['recipient']}" if r["recipient"] else "", r["subject_title"],
                                                   "đang mở" if r["status"] == "open" else "đã đóng") if x),
                   open=[{"title": f"Mở {subject[:1].lower()}{subject[1:]}", **target}] if target else [])


# ── Thông báo + nhắc hạn ──

class NotifyIn(RequestRef):
    title: str = field("Tiêu đề", max_length=300)
    body: str = field("Nội dung", max_length=2000)


@m.command("notify_owner", resource="asks", title="Báo người gửi yêu cầu", internal=True, agent="deny", callers=("asks", "flow"), family="outreach",
           why="Sau khi bên ngoài nộp qua link (phiên link không đọc được thành viên) hoặc vòng quét nhắc hạn của asks, hay gói nghe sự kiện bên "
               "kia nộp (flow, gói khai consumes(\"outreach\")): gửi MỘT thông báo cho người gửi yêu cầu, tiêu đề / nội dung do bên gọi soạn. Trả số thông báo đã gửi.")
async def notify_owner(ctx: Ctx, inp: NotifyIn) -> dict[str, Any]:
    """Cho thao tác qua link (phiên của link không đọc danh sách thành viên nên không tự gửi được) và cho gói phản ứng sau khi bên kia nộp."""
    owner = await ctx.val("SELECT owner_id FROM asks.requests WHERE id = %s", (inp.request_id,))
    n = await ctx.notify([owner], kind="asks", title=inp.title, body=inp.body, open={"query": "asks.request", "input": {"once_link": str(inp.request_id)}})
    return {"sent": n}


@m.command("remind", resource="asks", title="Nhắc hạn yêu cầu gửi bên ngoài", internal=True, agent="deny",
           sweep="""SELECT DISTINCT tenant_id FROM asks.requests WHERE status = 'open' AND due_on IS NOT NULL
                    AND due_on <= core.today_of(tenant_id) + 1 AND (reminded_on IS NULL OR reminded_on < core.today_of(tenant_id))""")
async def remind(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Vòng quét: yêu cầu còn mục chưa gửi, hạn từ ngày mai trở về trước ⇒ báo người gửi (mỗi ngày một lần). Bên ngoài không nhận nhắc tự động:
    link không lưu ở đâu cả — người gửi nhắc lại qua kênh đã gửi (tạo lại link nếu cần)."""
    rows = await ctx.all("""SELECT r.id, r.title, r.recipient, r.due_on, r.subject_title,
                                   (SELECT count(*) FROM asks.items i WHERE i.request_id = r.id AND i.status = 'open') AS left
                            FROM asks.requests r WHERE r.status = 'open' AND r.due_on <= core.today() + %s
                            AND (r.reminded_on IS NULL OR r.reminded_on < core.today()) FOR UPDATE SKIP LOCKED""", (REMIND_BEFORE,))
    sent = 0
    for r in rows:
        await ctx.exec("UPDATE asks.requests SET reminded_on = core.today() WHERE id = %s", (r["id"],))
        if not r["left"]:
            continue
        label = _due_label(r["due_on"], r["left"]) or f"Hạn {r['due_on']:%d/%m}"
        await ctx.call_system("asks.notify_owner", request_id=r["id"], title=f"{label}: {r['title']}",
                              body=f"{r['recipient'] or 'Bên nhận'} còn {r['left']} mục chưa gửi" + (f" — {r['subject_title']}" if r["subject_title"] else ""))
        sent += 1
    return {"reminded": sent}


# ── Thao tác của BÊN NGOÀI qua link (public — chỉ người cầm link loại asks.request; RLS chỉ mở đúng yêu cầu đó) ──

@m.query("link_view", resource="asks", title="Yêu cầu (bên ngoài xem qua link)", public=LINK, agent="deny")
async def link_view(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Chỉ tên công ty, tiêu đề, lời nhắn, hạn, các mục + câu đã trả lời / số file đã nộp của CHÍNH yêu cầu này."""
    r = await ctx.one("SELECT * FROM asks.requests WHERE id = core.link_ref()")
    if r is None:
        raise NotFound("Link không còn hiệu lực")
    items = await ctx.all("SELECT id, seq, kind, prompt, options, answer, status, cardinality(document_ids) AS files FROM asks.items"
                          " WHERE request_id = %s ORDER BY seq", (r["id"],))
    return {"company": await ctx.company(), "title": r["title"], "note": r["note"], "recipient": r["recipient"],
            "due_on": r["due_on"].isoformat() if r["due_on"] else None, "open": r["status"] == "open",
            "items": [{"id": i["id"], "kind": i["kind"], "prompt": i["prompt"], "options": i["options"], "answer": i["answer"], "files": i["files"],
                       "done": i["status"] != "open"} for i in items]}


async def _link_item(ctx: Ctx, item_id: UUID, kind: tuple[str, ...]) -> dict[str, Any]:
    it = await ctx.one("""SELECT i.*, r.status AS req_status, r.title, r.shelf, r.subject_kind, r.subject_id, r.owner_id
                          FROM asks.items i JOIN asks.requests r ON r.id = i.request_id
                          WHERE i.id = %s AND r.id = core.link_ref() FOR UPDATE OF i""", (item_id,))
    if it is None:
        raise NotFound("Không thấy mục này trong yêu cầu")
    if it["req_status"] != "open":
        raise Conflict("Yêu cầu đã đóng — liên hệ người gửi")
    if it["status"] == "accepted":
        raise Conflict("Bên gửi yêu cầu đã nhận mục này rồi")
    if it["kind"] not in kind:
        raise Invalid("Mục này không nhận kiểu trả lời đó")
    return it


def _answered(ctx: Ctx, it: dict[str, Any], *, answer: str = "", document_id: UUID | None = None) -> None:
    """Sự kiện cho gói sở hữu đối tượng (lọc theo subject_kind, đọc ``ref``) — chạy SAU khi bên kia gửi xong, bằng quyền hệ thống."""
    ctx.emit("asks.item_answered", request_id=it["request_id"], item_id=it["id"], seq=it["seq"], kind=it["kind"], ref=it["ref"],
             subject_kind=it["subject_kind"], subject_id=it["subject_id"], owner_id=it["owner_id"], answer=answer, document_id=document_id)


class LinkAnswer(Input):
    item_id: UUID
    answer: str = field("Trả lời", max_length=2000)


@m.command("link_answer", resource="asks", title="Bên ngoài trả lời qua link", public=LINK, agent="deny")
async def link_answer(ctx: Ctx, inp: LinkAnswer) -> dict[str, Any]:
    it = await _link_item(ctx, inp.item_id, ("choice", "text"))
    ans = inp.answer.strip()
    if not ans or (it["kind"] == "choice" and ans not in it["options"]):
        raise Invalid("Chọn một trong các lựa chọn" if it["kind"] == "choice" else "Chưa có câu trả lời")
    await ctx.exec("UPDATE asks.items SET answer = %s, status = 'answered', answered_at = now() WHERE id = %s", (ans, it["id"]))
    await ctx.call_system("asks.notify_owner", request_id=it["request_id"], title=f"Bên ngoài trả lời: {it['prompt'][:80]}",
                          body=f"{it['title']} — {ans[:200]}")
    _answered(ctx, it, answer=ans)
    return {"item_id": it["id"], "answer": ans}


class LinkUpload(Input):
    item_id: UUID
    filename: str = field("Tên file", max_length=200)
    data_b64: str = field("Nội dung", audit=False)


@m.command("link_upload", resource="asks", title="Bên ngoài nộp file qua link", public=LINK, agent="deny")
async def link_upload(ctx: Ctx, inp: LinkUpload) -> dict[str, Any]:
    it = await _link_item(ctx, inp.item_id, ("file",))
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", inp.filename.strip())[:200] or "tep"
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in UPLOAD_TYPES:
        raise Invalid(f"Chỉ nhận: {', '.join(sorted(UPLOAD_TYPES))}")
    if len(it["document_ids"]) >= MAX_FILES_PER_ITEM:
        raise Invalid(f"Tối đa {MAX_FILES_PER_ITEM} file một mục")
    doc = await ctx.call_system("kb.receive", shelf=it["shelf"], title=f"{it['prompt'][:150]} — {name}", source=f"Gửi qua link: {it['title']}",
                                filename=name, content_type=UPLOAD_TYPES[ext], data_b64=inp.data_b64)
    await ctx.exec("""UPDATE asks.items SET document_ids = array_append(document_ids, %s), status = 'answered', answered_at = now()
                      WHERE id = %s""", (doc["document_id"], it["id"]))
    await ctx.call_system("asks.notify_owner", request_id=it["request_id"], title=f"Bên ngoài nộp file: {it['prompt'][:80]}",
                          body=f"{it['title']} — {name}")
    _answered(ctx, it, document_id=doc["document_id"])
    return {"item_id": it["id"], "filename": name, "bytes": doc["bytes"]}


# ── Cần chú ý + màn ──

def _attn(o: Any) -> Line | None:
    if late := kpi(o, "Quá hạn"):
        return line("bad", f"{num(late)} yêu cầu gửi bên ngoài đã quá hạn", "Bên nhận chưa gửi đủ — nhắc lại hoặc đóng")
    return (line("warn", f"{num(kpi(o, 'Cần xem'))} yêu cầu bên ngoài đã gửi — chờ nhận", "Đối chiếu rồi nhận các mục đã đủ",
                 "Bên ngoài đã gửi gì, nhận những mục đã đủ giúp tôi") if kpi(o, "Cần xem") else None)


m.screens("docs", (Screen("asks", "Yêu cầu gửi bên ngoài", "paperplane", "asks.requests", tint="teal", desk=Desk("watch", say=_attn, menu=True, rank=178),
                          open=Open("asks.request", {"once_link": "id"}, "Yêu cầu gửi bên ngoài")),), rank=20)

