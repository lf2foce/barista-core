"""TRAO ĐỔI — luồng tin nhắn gắn vào đối tượng công việc (hồ sơ, khách…), phòng nhóm, tin riêng 1–1, @nhắc tên (quyết định 0046, 0048, 0115).

* **Ai đọc được** = ai thấy đối tượng, kiểm ở DB lúc truy cập (``core.subject_visible`` ⇒ hàm của module sở hữu đối tượng, chạy bằng
  RLS người hỏi). Không chép quyền sang đây ⇒ đổi người phụ trách là người cũ mất quyền ngay. Tệp, cảm xúc, lịch sử sửa là bảng con của tin
  ⇒ cùng một quyền, mất cùng lúc.
* **@nhắc tên** chỉ thêm người theo dõi + thông báo; chỉ nhắc được người VỐN thấy đối tượng — không thành cửa sau để xem hồ sơ.
* **Chia sẻ kết quả** (thẻ của trợ lý) chỉ lưu ``{query, input, title}``: ai bấm mở thì truy vấn chạy lại bằng quyền NGƯỜI MỞ.
  Phần chữ do người chia sẻ thấy trước khi gửi và chịu trách nhiệm.
* Trả lời của trợ lý KHÔNG tự vào luồng chung: hỏi trợ lý là hỏi riêng (quyền người hỏi), muốn cho cả luồng thấy thì người bấm chia sẻ.
* **Chữ tin không đi đâu ngoài luồng** (0115): không vào nhật ký thao tác (``audit=False``), không vào thông báo (chỉ tên người + tên hồ sơ),
  tín hiệu tức thì chỉ mang mã luồng — nội dung luôn đọc qua RLS lúc đọc.
* **Sửa / thu hồi không xoá:** bản trước khi đổi vào ``talk.message_versions``; tin thu hồi để lại dòng "đã thu hồi" cho mọi người, nội dung gốc
  chỉ người gửi + người có quyền "Xem lại tin đã thu hồi" (``talk.audit``) mà vẫn đọc được luồng.
"""

import asyncio
import base64
import binascii
import hashlib
import io
import json
import logging
import re
import unicodedata
import zipfile
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote, urlsplit
from uuid import UUID

from barista.api import (
    Ctx,
    Denied,
    Desk,
    Input,
    Invalid,
    Line,
    Module,
    NoInput,
    NotFound,
    Pack,
    Screen,
    can_run,
    company_settings,
    field,
    kpi,
    like,
    line,
    num,
    ui,
    web,
    zone,
)
from barista.api import files as filestore
from barista.kernel import registry

log = logging.getLogger("barista.talk")

# Gói 'talk' tự khai (0069) — kernel không biết tên gói; vai / đội / mẫu mặc định xem presets, màn & "cần chú ý" khai ngay dưới.
Pack("talk", "Trao đổi",
     "Trao đổi gắn vào hồ sơ / khách / đối tượng nghiệp vụ, phòng nhóm, tin riêng, @nhắc tên",
     grants=(("admin", "talk", "all", "read write"), ("director", "talk", "all", "read write"),
             ("office", "talk", "team", "read write"), ("staff", "talk", "team", "read write"),
             ("admin", "talk.audit", "all", "read"),),
     rules="""- TRAO ĐỔI: tóm tắt / hỏi lại một trao đổi (phòng, tin riêng, trao đổi trên hồ sơ) ⇒ talk__digest (subject_kind, subject_id lấy từ ngữ cảnh "đang mở
  trao đổi"; "tin chưa đọc" ⇒ only_unread=true): tóm tắt cho RIÊNG người hỏi — đã chốt gì, ai cần làm gì / hạn nào, còn treo gì; nêu tên người nói, không
  thêm điều tin nhắn không nói; KHÔNG tự gửi tóm tắt vào trao đổi (người muốn thì tự bấm "Chia sẻ vào trao đổi"). Tìm tin cũ, tệp / ảnh đã gửi ("file báo
  giá anh X gửi tuần trước") ⇒ core__find (theo chữ, tên tệp, chú thích): thẻ dẫn tới đúng tin, KHÔNG đọc / tóm tắt nội dung tệp (chỉ đọc được khi đã
  lưu vào kho tài liệu).
""",
     kind="capability")

m = Module("talk", "Trao đổi", pack="talk", resources={"talk": "Trao đổi trên hồ sơ / khách, phòng nhắn tin nhóm, tin riêng",
                                                      "talk.audit": "Xem lại tin đã thu hồi"})

ROOM, DIRECT = "talk.room", "talk.direct"
PAGE = 100            # số tin mỗi lần tải (mới nhất; "xem tin cũ hơn" tải tiếp)
AROUND = 40           # mở từ kết quả tìm: số tin trước / sau tin cần xem
RECALLED = "Tin đã được thu hồi"
# Cảm xúc: ít, đủ để "đồng ý / đã xong / đang xem / cảm ơn" mà không phải gửi một tin "ok" làm cả phòng có tin chưa đọc
REACTIONS = ("👍", "✅", "👀", "🙏")
FILE_MAX = 20 * 1024 * 1024
IMAGES = ("image/png", "image/jpeg", "image/gif", "image/webp")
OOXML = {"docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "word/"),
         "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xl/"),
         "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "ppt/")}
OLE = {"doc": "application/msword", "xls": "application/vnd.ms-excel", "ppt": "application/vnd.ms-powerpoint"}
FILE_KINDS = "ảnh (PNG, JPEG, GIF, WebP, HEIC), PDF, Word, Excel, PowerPoint, TXT, CSV"
MEDIA = (*IMAGES, "image/heic")   # tab "Ảnh" của tab Tệp (0128); xem trước trong tin vẫn chỉ IMAGES
# Link trong chữ tin (0128 §1): rút bằng code lúc gửi / sửa; KHÔNG tải trang đích (không SSRF). Migration 040 rút link tin cũ bằng đúng luật này.
LINK_RE = re.compile(r"https?://[^\s<>\"'`]+")
LINK_TRAIL = ".,;:!?)]}"
LINKS_MAX = 20
TEAM_ROOMS = m.setting("team_rooms", "Mỗi đội có sẵn một phòng trao đổi", default=False,
                       help="Đội có người ⇒ có phòng mang tên đội, thành viên theo đội (vào / ra đội là vào / ra phòng); người được thêm tay giữ nguyên. "
                            "Đổi tên đội ⇒ đổi tên phòng (nếu phòng chưa bị đặt tên khác). Giải thể đội không xoá phòng.")


async def _subject(ctx: Ctx, kind: str, sid: UUID, who: Any = None) -> dict[str, Any] | None:
    """Đối tượng như ``who`` (mặc định người gọi) thấy — None nếu không thấy / loại lạ / gói của đối tượng chưa bật."""
    if kind not in registry.SUBJECTS:
        return None
    try:
        return await ctx.call_as(registry.SUBJECTS[kind][1], who or ctx.p, subject_id=sid)
    except NotFound:
        return None


def _open(kind: str, sid: Any) -> dict[str, Any]:
    return {"query": "talk.thread", "input": {"subject_kind": kind, "subject_id": str(sid)}}


def _closed(kind: str, subj: dict[str, Any]) -> bool:
    """Tin riêng với người đã rời công ty: đọc lại được, không gửi thêm."""
    return kind == DIRECT and subj.get("state") == "inactive"


async def _signal(ctx: Ctx, tid: Any, *, inbox: bool = True, thread: bool = True) -> None:
    """Báo tức thì (0046, 0115) — chỉ MÃ, không chữ: ``thread:<luồng>`` cho máy đang mở luồng; ``inbox:<công ty>:<người>:<luồng>`` cho hộp tin của
    người theo dõi (kênh theo người kiểm lại quyền đọc luồng trước khi chuyển — người đã mất quyền không nhận gì). Gửi khi lần ghi commit;
    đề xuất chạy thử của trợ lý (hoàn lại) không báo. ``thread=False``: tin MỚI — trigger ``trg_talk_message`` đã báo ``thread:`` (một tin
    một tín hiệu luồng)."""
    if thread:
        await ctx.exec("SELECT pg_notify('barista_talk', 'thread:' || %s::text)", (tid,))
    if inbox:
        await ctx.exec("""SELECT pg_notify('barista_talk', 'inbox:' || mb.tenant_id || ':' || mb.user_id || ':' || mb.thread_id)
                          FROM talk.members mb WHERE mb.thread_id = %s AND mb.following""", (tid,))


async def _messages(ctx: Ctx, tid: Any, before: int | None, around: int | None) -> tuple[list[dict[str, Any]], bool, bool]:
    """Một cửa sổ tin của luồng (cũ → mới) + còn tin cũ hơn / mới hơn không."""
    cols = """SELECT msg.id, msg.author_id, u.name AS author, msg.body, msg.mentions, msg.card, msg.created_at, msg.edited_at, msg.recalled_at,
                     msg.pinned_at, msg.reply_to, msg.has_files,
                     msg.recalled_at IS NOT NULL AND talk.may_see_message(msg.id) AS original
              FROM talk.messages msg JOIN core.users u ON u.id = msg.author_id WHERE msg.thread_id = %s"""
    # "còn tin cũ hơn" = lấy THÊM một tin trong chính câu đọc (không câu thứ hai): mỗi câu chạm talk.messages dựng lại kiểm quyền luồng của RLS,
    # và planner có thể chọn quét mọi luồng của công ty cho câu hỏi một dòng (đo 10/11: ~72ms — perf doc §14).
    if around is not None:
        older = await ctx.all(cols + " AND msg.id <= %s ORDER BY msg.id DESC LIMIT %s", (tid, around, AROUND + 2))
        newer = await ctx.all(cols + " AND msg.id > %s ORDER BY msg.id LIMIT %s", (tid, around, AROUND + 1))
        has_more, has_newer = len(older) > AROUND + 1, len(newer) > AROUND
        rows = list(reversed(older[:AROUND + 1])) + newer[:AROUND]
    else:
        page = await ctx.all(cols + (" AND msg.id < %s" if before is not None else "") + " ORDER BY msg.id DESC LIMIT %s",
                             (tid, before, PAGE + 1) if before is not None else (tid, PAGE + 1))
        has_more, has_newer = len(page) > PAGE, False
        rows = list(reversed(page[:PAGE]))
    return rows, has_more, has_newer


def _excerpt(body: str, file: str = "") -> str:
    return re.sub(r"\s+", " ", body).strip()[:160] or (f"Tệp: {file}" if file else "Tệp đính kèm")


async def _block(ctx: Ctx, kind: str, sid: UUID, subj: dict[str, Any], *, before: int | None = None, around: int | None = None) -> dict[str, Any]:
    t = await ctx.one("SELECT * FROM talk.threads WHERE subject_kind = %s AND subject_id = %s", (kind, sid))
    msgs: list[dict[str, Any]] = []
    last_read, muted, following, has_more, has_newer = 0, False, False, False, False
    files: dict[int, list[dict[str, Any]]] = {}
    reacts: dict[int, list[dict[str, Any]]] = {}
    replies: dict[int, dict[str, Any]] = {}
    pinned: list[dict[str, Any]] = []
    if t is not None:
        msgs, has_more, has_newer = await _messages(ctx, t["id"], before, around)
        mb = await ctx.one("SELECT last_read_id, muted, following FROM talk.members WHERE thread_id = %s AND user_id = core.me()", (t["id"],))
        if mb:
            last_read, muted, following = int(mb["last_read_id"] or 0), bool(mb["muted"]), bool(mb["following"])
        ids = [x["id"] for x in msgs]
        # tệp của tin đã thu hồi: RLS (p_recalled) chỉ còn cho người gửi / talk.audit — và ở đây cũng không vẽ (xem qua "lịch sử")
        for f in await ctx.all("""SELECT id, message_id, filename, content_type, size_bytes FROM talk.attachments
                                  WHERE message_id = ANY(%s) ORDER BY created_at""", (ids,)):
            files.setdefault(f["message_id"], []).append(
                {"id": str(f["id"]), "filename": f["filename"], "content_type": f["content_type"], "size_bytes": f["size_bytes"],
                 "is_image": f["content_type"] in IMAGES})
        for r in await ctx.all("""SELECT r.message_id, r.emoji, count(*) AS n, bool_or(r.user_id = core.me()) AS mine,
                                         array_agg(u.name ORDER BY r.created_at) AS names
                                  FROM talk.reactions r JOIN core.users u ON u.id = r.user_id
                                  WHERE r.message_id = ANY(%s) GROUP BY r.message_id, r.emoji""", (ids,)):
            reacts.setdefault(r["message_id"], []).append({"emoji": r["emoji"], "count": r["n"], "mine": r["mine"], "names": r["names"]})
        for x in reacts.values():
            x.sort(key=lambda r: REACTIONS.index(r["emoji"]) if r["emoji"] in REACTIONS else 99)
        if refs := [x["reply_to"] for x in msgs if x["reply_to"]]:
            for r in await ctx.all("""SELECT msg.id, u.name AS author, msg.body, msg.recalled_at IS NOT NULL AS recalled,
                                             (SELECT a.filename FROM talk.attachments a WHERE a.message_id = msg.id LIMIT 1) AS file
                                      FROM talk.messages msg JOIN core.users u ON u.id = msg.author_id
                                      WHERE msg.id = ANY(%s) AND msg.thread_id = %s""", (refs, t["id"])):
                replies[r["id"]] = {"id": r["id"], "author": r["author"], "body": _excerpt(r["body"], r["file"] or ""), "recalled": r["recalled"]}
        pinned = [{"id": p["id"], "author": p["author"], "body": _excerpt(p["body"], p["file"] or ""), "pinned_by": p["pinned_by"] or "", "at": p["pinned_at"]}
                  for p in await ctx.all(
                      """SELECT msg.id, u.name AS author, msg.body, msg.pinned_at, pu.name AS pinned_by,
                                (SELECT a.filename FROM talk.attachments a WHERE a.message_id = msg.id LIMIT 1) AS file
                         FROM talk.messages msg JOIN core.users u ON u.id = msg.author_id LEFT JOIN core.users pu ON pu.id = msg.pinned_by
                         WHERE msg.thread_id = %s AND msg.pinned_at IS NOT NULL AND msg.recalled_at IS NULL
                         ORDER BY msg.pinned_at DESC LIMIT 20""", (t["id"],))]
    me = ctx.p.user_id
    if kind == ROOM:   # phòng: chỉ nhắc được người trong phòng (người ngoài không đọc được tin)
        people = await ctx.all("""SELECT u.email AS value, u.name AS label FROM talk.room_members rm JOIN core.users u ON u.id = rm.user_id
                                  WHERE rm.room_id = %s AND u.id <> core.me() ORDER BY u.name""", (sid,))
    elif kind == DIRECT:   # tin riêng: chỉ có hai người — không có ai để nhắc
        people = []
    else:
        people = await ctx.all("""SELECT u.email AS value, u.name AS label FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id
                                  AND mb.tenant_id = core.tenant() WHERE mb.active AND u.id <> core.me() ORDER BY u.name""")
    # thẻ TRÙNG (cùng truy vấn + tham số) chỉ tính MỘT lần mỗi lần mở luồng — chia sẻ một thẻ nặng nhiều lần không nhân tải (review 05/11)
    by_card: dict[str, dict[str, Any]] = {}
    live: dict[int, dict[str, Any]] = {}
    for x in [m for m in msgs if m["card"]][-LIVE_CARDS:]:
        k = json.dumps([x["card"]["query"], x["card"].get("input") or {}], sort_keys=True, default=str)
        if k not in by_card:
            by_card[k] = await _live(ctx, x["card"])
        live[x["id"]] = by_card[k]
    # điểm cắm theo TIN (0115, ghi chú 07/11): gói khác góp nút / dấu cho từng tin (vd "Tạo việc", "Đã tạo việc"), MỘT lần hỏi mỗi lần mở luồng, chạy
    # bằng quyền người xem — gói tắt / thiếu quyền ⇒ tin không có nút / dấu đó; dấu chỉ hiện khi người xem thấy thứ nó dẫn tới (module góp tự lọc).
    per_msg: dict[int, dict[str, list[dict[str, Any]]]] = {}
    if t is not None and msgs:
        briefs = [{"id": x["id"], "body": "" if x["recalled_at"] is not None else x["body"], "recalled": x["recalled_at"] is not None} for x in msgs]
        for got in await ctx.plugs("talk.message_actions", subject_kind=kind, subject_id=sid, thread_id=t["id"], members_only=kind in (ROOM, DIRECT),
                                   messages=briefs):
            for row in got:
                e = per_msg.setdefault(int(row["message_id"]), {"actions": [], "marks": []})
                e["actions"] += row.get("actions") or []
                e["marks"] += row.get("marks") or []
    writer = ctx.p.can("talk", "write")
    can_post = writer and not _closed(kind, subj)
    unread = sum(1 for x in msgs if x["id"] > last_read and x["author_id"] != me and x["recalled_at"] is None)
    title = subj["title"]

    def msg(x: dict[str, Any]) -> dict[str, Any]:
        gone = x["recalled_at"] is not None
        mine = x["author_id"] == me
        return {"id": x["id"], "author": x["author"], "mine": mine, "body": x["body"], "at": x["created_at"],
                "mentions_me": me in (x["mentions"] or []),
                "card": None if gone else ({**x["card"], "live": live[x["id"]]} if x["id"] in live else x["card"]),
                # 0115 — chỉ THÊM trường
                "edited": x["edited_at"] is not None and not gone, "recalled": gone, "pinned": x["pinned_at"] is not None and not gone,
                "reply": replies.get(x["reply_to"]) if x["reply_to"] else None,
                "attachments": [] if gone else files.get(x["id"], []), "reactions": [] if gone else reacts.get(x["id"], []),
                "can_edit": mine and not gone and writer,
                "history": {"query": "talk.message_history", "input": {"message_id": x["id"]}}
                if (x["edited_at"] is not None and not gone) or (gone and x["original"]) else None,
                # điểm cắm talk.message_actions — chỉ THÊM trường
                "actions": [] if gone else per_msg.get(x["id"], {}).get("actions", []),
                # tin đã thu hồi: dấu vẫn dẫn tới thứ nó trỏ, nhưng KHÔNG mang chữ chi tiết (có thể chép từ chữ gốc — vd tiêu đề việc)
                "marks": [{**k, "detail": ""} if gone else k for k in per_msg.get(x["id"], {}).get("marks", [])]}

    assist = [{"title": "Tóm tắt cho tôi", "prompt": f"Tóm tắt trao đổi “{title}” cho tôi: đã chốt gì, ai cần làm gì, còn treo gì."}]
    if unread:
        assist.insert(0, {"title": f"Tóm tắt {unread} tin chưa đọc", "prompt": f"Tóm tắt các tin chưa đọc trong trao đổi “{title}” cho tôi."})
    return {"type": "thread", "title": "Tin riêng" if kind == DIRECT else "Trao đổi", "subject_kind": kind, "subject_id": str(sid),
            "subject_title": title, "thread_id": str(t["id"]) if t else None, "last_read": last_read,
            "can_post": can_post, "people": ui.options(people),
            # điểm cắm (0110): gói khác góp nút lên khối trao đổi (vd "Gọi" — 0114), chạy bằng quyền người xem; gói tắt / thiếu quyền ⇒ không có nút
            "actions": [a for got in await ctx.plugs("talk.thread_actions", subject_kind=kind, subject_id=sid) for a in got],
            "messages": [msg(x) for x in msgs],
            # 0115 — chỉ THÊM trường
            "kind_label": registry.SUBJECTS.get(kind, ("Hồ sơ", ""))[0], "has_more": has_more, "has_newer": has_newer, "focus_id": around,
            "pinned": pinned, "muted": muted, "following": following, "reaction_choices": list(REACTIONS),
            "can_attach": can_post, "file_max": FILE_MAX, "file_kinds": FILE_KINDS, "assist": assist,
            "notice": f"{title} không còn làm trong công ty — chỉ đọc lại được tin cũ." if writer and _closed(kind, subj) else "",
            # 0128 — chỉ THÊM trường: tab "Tệp" (ảnh / tài liệu / link của luồng) = một trang thẻ có tab, app mở bằng bộ vẽ trang sẵn có
            "files": {"title": "Tệp", "query": "talk.files", "input": {"thread_id": str(t["id"])}} if t is not None else None}


LIVE_CARDS = 15   # chỉ tính lại các thẻ gần nhất của luồng (mỗi thẻ là một truy vấn)


async def _live(ctx: Ctx, card: dict[str, Any]) -> dict[str, Any]:
    """THẺ SỐNG (0089): chạy lại truy vấn của thẻ bằng QUYỀN NGƯỜI ĐANG XEM — trạng thái hiện tại (tiêu đề phụ) + nút người đó được bấm (vd
    Duyệt / Từ chối chỉ người duyệt bước hiện tại thấy). Không xem được / gói tắt / đã xoá ⇒ ``gone`` — không lộ gì thêm ngoài tên thẻ lúc chia sẻ."""
    try:
        page = await ctx.call_optional(card["query"], **(card.get("input") or {}))
    except (Denied, NotFound, Invalid):
        page = None
    if not isinstance(page, dict) or page.get("kind") not in ("card", "detail"):
        return {"gone": page is None, "subtitle": "", "headline": "", "actions": []}
    # ``headline``: tên thẻ theo người xem (trường thêm — thẻ không khai thì trống, app giữ tên lưu lúc chia sẻ)
    return {"gone": False, "subtitle": str(page.get("subtitle") or ""), "headline": str(page.get("headline") or ""),
            "actions": [a for a in page.get("actions") or [] if a.get("command")][:3]}


class SubjectIn(Input):
    subject_kind: str = field("Loại đối tượng", hidden=True)
    subject_id: UUID = field("Đối tượng", hidden=True)


class ThreadIn(SubjectIn):
    before: int | None = field("Tin cũ hơn mã", None, ge=1, hidden=True, help="Tải tiếp các tin cũ hơn tin này")
    around: int | None = field("Mở quanh tin", None, ge=1, hidden=True, help="Mở luồng quanh một tin (từ kết quả tìm)")


@m.query("thread", resource="talk", title="Trao đổi trên một hồ sơ / khách / phòng / tin riêng")
async def thread(ctx: Ctx, inp: ThreadIn) -> dict[str, Any]:
    """Trao đổi về một đối tượng (100 tin gần nhất; ``before`` ⇒ tin cũ hơn; ``around`` ⇒ quanh một tin) — người gọi phải thấy được đối tượng."""
    subj = await _subject(ctx, inp.subject_kind, inp.subject_id)
    if subj is None:
        raise NotFound("Không thấy hồ sơ / khách này (hoặc bạn không có quyền xem)")
    return ui.card(subj["title"], [await _block(ctx, inp.subject_kind, inp.subject_id, subj, before=inp.before, around=inp.around)],
                   subtitle=registry.SUBJECTS[inp.subject_kind][0], icon="bubble.left.and.bubble.right",
                   open=[{"title": f"Mở {registry.SUBJECTS[inp.subject_kind][0].lower()}", **subj["open"]}] if subj.get("open") else [])


@m.query("block", resource="talk", title="Khối trao đổi cho thẻ của module khác", internal=True)
async def block(ctx: Ctx, inp: SubjectIn) -> dict[str, Any] | None:
    """Module khác gọi bằng QUYỀN NGƯỜI XEM (``call_as_optional``) để gắn khối "Trao đổi" vào thẻ của mình."""
    subj = await _subject(ctx, inp.subject_kind, inp.subject_id)
    return None if subj is None else await _block(ctx, inp.subject_kind, inp.subject_id, subj)


class MessageBrief(Input):
    id: int
    body: str
    recalled: bool


class MessagesIn(SubjectIn):
    """Đầu vào điểm cắm ``talk.message_actions``: luồng + các tin đang hiện (chữ rỗng với tin đã thu hồi). Gói cắm vào trả
    ``[{message_id, actions: [nút], marks: [{title, detail, tone, open}]}]`` — chỉ cho tin có gì để góp. ``members_only``: người đọc là MỘT DANH
    SÁCH (phòng, tin riêng) — hẹp hơn mọi phạm vi quyền nghiệp vụ, nên gói cắm vào không được chép chữ tin sang chỗ người khác đọc."""
    thread_id: UUID
    members_only: bool
    messages: list[MessageBrief]


class Post(Input):
    subject_kind: str = field("Loại đối tượng", hidden=True)
    subject_id: UUID = field("Đối tượng", hidden=True)
    body: str = field("Nội dung", kind="textarea", max_length=4000, audit=False)
    mentions: list[str] = field("Nhắc tên (email)", [], hidden=True, help="Chỉ nhắc được người vốn xem được hồ sơ / khách này")
    card: dict[str, Any] | None = field("Thẻ chia sẻ", None, hidden=True)
    reply_to: int | None = field("Trả lời tin", None, ge=1, hidden=True)


@m.command("post", resource="talk", title="Gửi vào trao đổi")   # KHÔNG promotable: trợ lý chỉ soạn trong trao đổi (0115) — tin đã gửi người khác đã đọc
async def post(ctx: Ctx, inp: Post) -> dict[str, Any]:
    """Nhắn tin / gửi tin / thông báo / báo cho mọi người trong MỘT luồng trao đổi (hồ sơ, phòng nhắn tin nhóm, tin riêng đã mở). Trợ lý chỉ soạn (người bấm mới gửi). Gửi lại cùng khoá chống lặp ⇒ không thành hai tin. Chữ tin không vào nhật ký (0115)."""
    if not inp.body.strip():
        raise Invalid("Tin nhắn trống")
    return await _deliver(ctx, inp.subject_kind, inp.subject_id, inp.body, inp.mentions, inp.card, inp.reply_to)


async def _deliver(ctx: Ctx, kind: str, sid: UUID, body: str, mentions: list[str], card: dict[str, Any] | None, reply_to: int | None,
                   file: tuple[str, str, bytes | bytearray] | None = None) -> dict[str, Any]:
    subj = await _subject(ctx, kind, sid)
    if subj is None:
        raise NotFound("Không thấy hồ sơ / khách này (hoặc bạn không có quyền xem)")
    if _closed(kind, subj):
        raise Invalid(f"{subj['title']} không còn làm trong công ty — không gửi tin riêng được nữa")
    card = _check_card(card) if card else None
    mentioned: list[tuple[UUID, str]] = []
    for email in dict.fromkeys(e.strip().lower() for e in mentions if e.strip()):
        u = await ctx.one("""SELECT u.id, u.name FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()
                             WHERE u.email = %s AND mb.active""", (email,))
        if u is None:
            raise NotFound(f"Không có người {email} trong công ty")
        who = await ctx.principal_of(u["id"])
        if who is None or await _subject(ctx, kind, sid, who) is None:
            raise Invalid(f"{u['name']} không xem được “{subj['title']}” — chỉ nhắc được người vốn xem được (nhờ quản lý giao quyền nếu cần)")
        if u["id"] != ctx.p.user_id:
            mentioned.append((u["id"], u["name"]))
    tid = await ctx.val("SELECT id FROM talk.threads WHERE subject_kind = %s AND subject_id = %s", (kind, sid))
    if tid is None:
        tid = await ctx.val("""INSERT INTO talk.threads (subject_kind, subject_id, title, created_by) VALUES (%s, %s, %s, core.me())
                               ON CONFLICT (tenant_id, subject_kind, subject_id) DO UPDATE SET title = EXCLUDED.title RETURNING id""",
                            (kind, sid, subj["title"]))
    if reply_to is not None and not await ctx.val("SELECT 1 FROM talk.messages WHERE id = %s AND thread_id = %s", (reply_to, tid)):
        raise Invalid("Tin được trả lời không thuộc trao đổi này")
    mid = await ctx.val("""INSERT INTO talk.messages (thread_id, author_id, body, mentions, card, reply_to, has_files)
                           VALUES (%s, core.me(), %s, %s, %s, %s, %s) RETURNING id""",
                        (tid, body.strip(), [u for u, _ in mentioned], card and _json(card), reply_to, file is not None))
    if file is not None:
        name, ctype, data = file
        stored = await filestore.put(ctx, data, name, ctype, max_bytes=FILE_MAX)   # kho trước, dòng sau — cùng giao dịch (0122 §3)
        await ctx.exec("""INSERT INTO talk.attachments (message_id, filename, content_type, size_bytes, sha256, file_id, created_by)
                          VALUES (%s, %s, %s, %s, %s, %s, core.me())""", (mid, name, ctype, stored.size, stored.sha256, stored.id))
    await _save_links(ctx, mid, body)
    if kind != DIRECT:   # tin riêng: tên luồng là tên NGƯỜI KIA, tính lúc đọc theo người xem
        await ctx.exec("UPDATE talk.threads SET title = %s WHERE id = %s", (subj["title"], tid))
    await ctx.exec("UPDATE talk.threads SET last_message_at = now(), last_message_id = %s WHERE id = %s", (mid, tid))
    await ctx.exec("""INSERT INTO talk.members (thread_id, user_id, last_read_id, reason) VALUES (%s, core.me(), %s, 'posted')
                      ON CONFLICT (thread_id, user_id) DO UPDATE SET following = true, last_read_id = EXCLUDED.last_read_id""", (tid, mid))
    for uid, _ in mentioned:
        await ctx.exec("""INSERT INTO talk.members (thread_id, user_id, reason) VALUES (%s, %s, 'mentioned')
                          ON CONFLICT (thread_id, user_id) DO UPDATE SET following = true""", (tid, uid))
    # Thông báo KHÔNG chép chữ tin (0115): hộp thông báo đọc được cả sau khi người nhận mất quyền xem hồ sơ — chữ chỉ đọc trong luồng, qua RLS
    await ctx.notify([u for u, _ in mentioned], kind="mention", title=f"{ctx.p.name} nhắc anh/chị: {subj['title']}",
                     push="Anh/chị được nhắc trong một trao đổi", open=_open(kind, sid))
    owner = subj.get("owner_id")
    if owner and owner not in {u for u, _ in mentioned}:
        r = await ctx.one("SELECT muted, last_read_id FROM talk.members WHERE thread_id = %s AND user_id = %s", (tid, owner))
        if not (r and r["muted"]):
            # một thông báo cho mỗi đợt tin chưa đọc (đọc rồi thì lần tới báo lại) — nhắn mười tin liền không thành mười thông báo
            key = f"talk:{tid}:{r['last_read_id'] if r else 0}"
            if kind == DIRECT:
                await ctx.notify([owner], kind="direct", title=f"{ctx.p.name} nhắn riêng cho anh/chị", push="Có tin nhắn riêng mới",
                                 open=_open(kind, sid), key=key)
            else:
                await ctx.notify([owner], kind="talk", title=f"{ctx.p.name} trao đổi về {subj['title']}",
                                 push="Có trao đổi mới về việc anh/chị phụ trách", open=_open(kind, sid), key=key)
    await _signal(ctx, tid, thread=False)
    return {"thread_id": tid, "message_id": mid, "summary": ("Gửi tin riêng cho " if kind == DIRECT else "Gửi vào trao đổi ") + f"“{subj['title']}”"
            + (f", nhắc {', '.join(n for _, n in mentioned)}" if mentioned else "")}


def _check_card(card: dict[str, Any]) -> dict[str, Any]:
    """Thẻ chia sẻ = THAM CHIẾU truy vấn, không chụp dữ liệu: người mở chạy lại bằng quyền của họ (không lộ số vượt quyền)."""
    op = registry.get(str(card.get("query", "")))
    if op is None or op.kind != "query" or op.internal:
        raise Invalid("Thẻ chia sẻ chỉ được là một truy vấn công khai")
    inp = card.get("input") or {}
    if not isinstance(inp, dict):
        raise Invalid("Tham số thẻ không hợp lệ")
    op.input.model_validate(inp)
    return {"query": op.name, "input": inp, "title": str(card.get("title") or op.title)[:200]}


def _json(v: Any) -> Any:
    from psycopg.types.json import Jsonb
    return Jsonb(v)


def links(body: str) -> list[tuple[str, str]]:
    """``[(url, host)]`` trong chữ tin, theo thứ tự xuất hiện, không trùng, tối đa ``LINKS_MAX``. Chỉ đọc CHỮ — không mở link."""
    out: dict[str, str] = {}
    for raw in LINK_RE.findall(body):
        url = raw.rstrip(LINK_TRAIL)
        if len(url) > 2000 or len(out) >= LINKS_MAX or url in out:
            continue
        try:
            host = (urlsplit(url).hostname or "")[:255]
        except ValueError:
            continue
        if host:
            out[url] = host
    return list(out.items())


async def _save_links(ctx: Ctx, mid: int, body: str, *, replace: bool = False) -> None:
    if replace:
        await ctx.exec("DELETE FROM talk.links WHERE message_id = %s", (mid,))
    for url, host in links(body):
        await ctx.exec("INSERT INTO talk.links (message_id, url, host) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (mid, url, host))


# ── Tệp đính kèm (0115): thân tệp ở kho đối tượng (kernel/files.py — 0122), quyền theo LUỒNG; thu hồi tin KHÔNG release tệp ──

def sniff(filename: str, data: bytes | bytearray) -> str:
    """Loại tệp theo NỘI DUNG (không tin loại máy khách khai): chỉ nhận danh sách cố định; không HTML / SVG / tệp chạy được. Trả kiểu MIME."""
    visible = "".join(ch for ch in filename if unicodedata.category(ch) != "Cf")
    ext = visible.rsplit(".", 1)[-1].lower().strip() if "." in visible else ""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"heif"):
        return "image/heic"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"PK\x03\x04") and ext in OOXML:
        try:
            names = zipfile.ZipFile(io.BytesIO(memoryview(data))).namelist()
        except zipfile.BadZipFile:
            names = []
        if "[Content_Types].xml" in names and any(n.startswith(OOXML[ext][1]) for n in names):
            return OOXML[ext][0]
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") and ext in OLE:
        return OLE[ext]
    if ext in ("txt", "csv") and b"\x00" not in data:
        try:
            data.decode("utf-8")
            return "text/csv; charset=utf-8" if ext == "csv" else "text/plain; charset=utf-8"
        except UnicodeDecodeError:
            pass
    raise Invalid(f"Không nhận loại tệp này — chỉ nhận {FILE_KINDS}")


EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp", "image/heic": "heic", "application/pdf": "pdf",
       **{v[0]: k for k, v in OOXML.items()}, **{v: k for k, v in OLE.items()},
       "text/csv; charset=utf-8": "csv", "text/plain; charset=utf-8": "txt"}


def clean_name(filename: str, content_type: str) -> str:
    """Tên tệp an toàn, GẮN với loại server nhận diện (review 07/11): bỏ thư mục, ký tự điều khiển và ký tự định dạng vô hình (đảo chiều chữ
    U+202E, U+2066…, khoảng trắng không độ rộng — Unicode "Cf") vốn làm "ảnh.png" hiện / lưu thành ".html"; đuôi tệp luôn là đuôi của loại
    đã nhận diện (PNG mang tên ".html" ⇒ ".png")."""
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in base if ch >= " " and ch != "\x7f" and unicodedata.category(ch) != "Cf").strip(" .")
    stem = base.rsplit(".", 1)[0].strip(" .") if "." in base else base
    ext = EXT[content_type]
    return f"{(stem or 'tep')[:190]}.{ext}"


def ascii_name(name: str) -> str:
    """Tên dự phòng chỉ chữ ASCII cho ``Content-Disposition`` (trình duyệt / app cũ không đọc ``filename*``)."""
    folded = unicodedata.normalize("NFD", name.replace("đ", "d").replace("Đ", "D"))
    return re.sub(r"[^A-Za-z0-9._-]+", "_", "".join(c for c in folded if not unicodedata.combining(c))).strip("_") or "tep"


class PostFile(Input):
    subject_kind: str = field("Loại đối tượng", hidden=True)
    subject_id: UUID = field("Đối tượng", hidden=True)
    filename: str = field("Tên tệp", max_length=300)
    size: int = field("Cỡ (byte)", ge=1, le=FILE_MAX, hidden=True)
    sha256: str = field("Mã băm SHA-256", max_length=64, hidden=True, help="Của nội dung tệp gửi kèm — tệp không nằm trong đầu vào")
    body: str = field("Chú thích", "", kind="textarea", max_length=4000, audit=False)
    reply_to: int | None = field("Trả lời tin", None, ge=1, hidden=True)
    # Phân hệ khác gửi tệp CỦA NGƯỜI GỌI vào trao đổi bằng ``ctx.call`` (vd Văn bản Word "Gửi vào phòng" — 0227 §2.11): gọi lồng không mang được
    # ``ctx.files`` nên nội dung đi trong đầu vào — ẩn trên form, KHÔNG chép vào nhật ký (chỉ cỡ + sha256); mọi kiểm tra bên dưới giữ nguyên.
    content_b64: str = field("Nội dung tệp (base64)", "", hidden=True, audit=False, agent=False, max_length=FILE_MAX * 4 // 3 + 8)


@m.command("post_file", resource="talk", title="Gửi tệp vào trao đổi", agent="deny")
async def post_file(ctx: Ctx, inp: PostFile) -> dict[str, Any]:
    """MỘT tệp = MỘT tin (kèm chú thích nếu có). Nội dung tệp đi KÈM lệnh (``ctx.files["file"]`` — route ``POST /v1/talk/files`` đọc từ thân
    request, chặn cỡ ngay khi đọc), đầu vào chỉ mang cỡ + sha256 ⇒ nhật ký / chống lặp không giữ nội dung. Kiểm cỡ + loại theo nội dung ở server;
    ai đọc được luồng mới tải được tệp (RLS bảng con của tin). Trợ lý không gửi tệp thay người."""
    data = ctx.files.get("file")
    if not data and inp.content_b64:
        try:
            data = base64.b64decode(inp.content_b64, validate=True)
        except (ValueError, binascii.Error) as e:
            raise Invalid("Nội dung tệp không đọc được") from e
    if not data:
        raise Invalid("Thiếu nội dung tệp")
    if len(data) > FILE_MAX:
        raise Invalid(f"Tệp quá {FILE_MAX // (1024 * 1024)} MB")
    if len(data) != inp.size or hashlib.sha256(data).hexdigest() != inp.sha256.lower():
        raise Invalid("Nội dung tệp không khớp cỡ / mã băm đã khai")
    ctype = sniff(inp.filename, data)
    name = clean_name(inp.filename, ctype)
    out = await _deliver(ctx, inp.subject_kind, inp.subject_id, inp.body, [], None, inp.reply_to, (name, ctype, data))
    return {**out, "summary": f"Gửi tệp {name}"}


class FileRef(Input):
    attachment_id: UUID = field("Tệp", hidden=True)


@m.query("file", resource="talk", title="Tải tệp trong trao đổi", agent="deny")
async def file(ctx: Ctx, inp: FileRef) -> dict[str, Any]:
    """RLS: không đọc được luồng (mất quyền xem hồ sơ, rời phòng) hoặc tin đã thu hồi (trừ người gửi / talk.audit) ⇒ không thấy tệp."""
    f = await ctx.one("SELECT filename, content_type, file_id FROM talk.attachments WHERE id = %s", (inp.attachment_id,))
    if f is None:
        raise NotFound("Không thấy tệp (hoặc anh/chị không còn xem được trao đổi này)")
    data = await filestore.read(ctx, f["file_id"])
    return {"filename": f["filename"], "content_type": f["content_type"], "inline": f["content_type"] in IMAGES or f["content_type"] == "application/pdf",
            "data_b64": base64.b64encode(data).decode()}


# ── Sửa / thu hồi / cảm xúc / ghim (0115) ──

class MessageRef(Input):
    message_id: int = field("Tin", ge=1, hidden=True)


# không khai callers=: executor từ chối call_system tới thao tác nội bộ chưa có hợp đồng ⇒ chỉ gọi được bằng quyền MỘT NGƯỜI (call_as), RLS áp
@m.query("message_ref", resource="talk", title="Một tin nhắn, bằng quyền người hỏi", internal=True)
async def message_ref(ctx: Ctx, inp: MessageRef) -> dict[str, Any]:
    """Cho module khác hỏi bằng quyền MỘT NGƯỜI (``call_as`` / ``call_as_optional``): người đó đọc được tin này không (RLS của luồng = thấy đối
    tượng), tin thuộc luồng nào, đối tượng NGHIỆP VỤ của luồng (``about_*`` — phòng / tin riêng là của chính Trao đổi ⇒ không có), chữ tin (rỗng
    nếu đã thu hồi), lối mở về đúng tin. Không đọc được ⇒ NotFound."""
    r = await ctx.one("""SELECT msg.id, msg.thread_id, msg.body, msg.recalled_at, msg.created_at, u.name AS author, t.subject_kind, t.subject_id
                         FROM talk.messages msg JOIN talk.threads t ON t.id = msg.thread_id JOIN core.users u ON u.id = msg.author_id
                         WHERE msg.id = %s""", (inp.message_id,))
    if r is None:
        raise NotFound("Không thấy tin nhắn (hoặc không xem được trao đổi này)")
    own = r["subject_kind"] in (ROOM, DIRECT)
    return {"message_id": r["id"], "thread_id": r["thread_id"], "subject_kind": r["subject_kind"], "subject_id": r["subject_id"],
            "about_kind": None if own else r["subject_kind"], "about_id": None if own else r["subject_id"], "members_only": own,
            "body": "" if r["recalled_at"] is not None else r["body"], "recalled": r["recalled_at"] is not None, "author": r["author"],
            "at": r["created_at"],
            "open": {"query": "talk.thread", "input": {"subject_kind": r["subject_kind"], "subject_id": str(r["subject_id"]), "around": r["id"]}}}


async def _message(ctx: Ctx, mid: int) -> dict[str, Any]:
    r = await ctx.one("SELECT id, thread_id, author_id, body, card, mentions, recalled_at, has_files FROM talk.messages WHERE id = %s", (mid,))
    if r is None:   # RLS: không đọc được luồng ⇒ không thấy tin
        raise NotFound("Không thấy tin nhắn")
    return r


def _own(ctx: Ctx, r: dict[str, Any], what: str) -> None:
    if r["author_id"] != ctx.p.user_id:
        raise Denied(f"Chỉ {what} được tin của chính mình")
    if r["recalled_at"] is not None:
        raise Invalid("Tin đã thu hồi")


class EditIn(Input):
    message_id: int = field("Tin", ge=1, hidden=True)
    body: str = field("Nội dung mới", kind="textarea", max_length=4000, audit=False)


@m.command("edit_message", resource="talk", title="Sửa tin nhắn của mình", agent="deny")
async def edit_message(ctx: Ctx, inp: EditIn) -> dict[str, Any]:
    """Bản trước khi sửa giữ ở lịch sử (ai đọc được luồng đều xem được); tin hiện "đã sửa". Nhắc tên không đổi (nhắc thêm ⇒ gửi tin mới)."""
    r = await _message(ctx, inp.message_id)
    _own(ctx, r, "sửa")
    body = inp.body.strip()
    if not body and not r["has_files"]:
        raise Invalid("Tin nhắn trống — muốn bỏ tin thì thu hồi")
    if body == r["body"]:
        return {"message_id": r["id"], "summary": "Không có gì thay đổi"}
    await ctx.exec("""INSERT INTO talk.message_versions (message_id, kind, body, card, mentions, changed_by) VALUES (%s, 'edit', %s, %s, %s, core.me())""",
                   (r["id"], r["body"], r["card"] and _json(r["card"]), r["mentions"]))
    await ctx.exec("UPDATE talk.messages SET body = %s, edited_at = now() WHERE id = %s", (body, r["id"]))
    await _save_links(ctx, r["id"], body, replace=True)
    await _signal(ctx, r["thread_id"])
    return {"message_id": r["id"], "summary": "Đã sửa tin"}


@m.command("recall_message", resource="talk", title="Thu hồi tin nhắn của mình", agent="deny")
async def recall_message(ctx: Ctx, inp: MessageRef) -> dict[str, Any]:
    """Không xoá: mọi người thấy "Tin đã được thu hồi" (ai, lúc nào); nội dung, thẻ, tệp giữ lại — chỉ người gửi và người có quyền "Xem lại tin
    đã thu hồi" mà vẫn đọc được luồng xem lại được (``talk.message_versions`` + RLS ``talk.may_see_message``). Bỏ ghim, bỏ nhắc tên."""
    r = await _message(ctx, inp.message_id)
    _own(ctx, r, "thu hồi")
    await ctx.exec("""INSERT INTO talk.message_versions (message_id, kind, body, card, mentions, changed_by) VALUES (%s, 'recall', %s, %s, %s, core.me())""",
                   (r["id"], r["body"], r["card"] and _json(r["card"]), r["mentions"]))
    await ctx.exec("""UPDATE talk.messages SET body = %s, card = NULL, mentions = '{}', recalled_at = now(), pinned_at = NULL, pinned_by = NULL
                      WHERE id = %s""", (RECALLED, r["id"]))
    await _signal(ctx, r["thread_id"])
    return {"message_id": r["id"], "summary": "Đã thu hồi tin — nội dung gốc vẫn lưu trong lịch sử"}


@m.query("message_history", resource="talk", title="Lịch sử sửa / thu hồi một tin", agent="deny")
async def message_history(ctx: Ctx, inp: MessageRef) -> dict[str, Any]:
    """Các bản trước của một tin. Tin đã thu hồi: chỉ người gửi + người có quyền talk.audit (RLS ``p_recalled``) — người khác chỉ thấy là đã thu hồi."""
    r = await ctx.one("""SELECT msg.id, msg.body, msg.created_at, msg.recalled_at, u.name AS author,
                                (SELECT string_agg(a.filename, ', ') FROM talk.attachments a WHERE a.message_id = msg.id) AS files
                         FROM talk.messages msg JOIN core.users u ON u.id = msg.author_id WHERE msg.id = %s""", (inp.message_id,))
    if r is None:
        raise NotFound("Không thấy tin nhắn")
    rows = await ctx.all("""SELECT v.kind, v.body, v.changed_at, u.name AS who FROM talk.message_versions v JOIN core.users u ON u.id = v.changed_by
                            WHERE v.message_id = %s ORDER BY v.changed_at""", (inp.message_id,))
    if r["recalled_at"] is not None and not rows:
        raise NotFound("Tin đã được thu hồi — chỉ người gửi và người được giao quyền xem lại được nội dung gốc")
    items = [{"title": v["body"][:300] or "(chỉ có tệp)", "subtitle": f"{'Bản trước khi thu hồi' if v['kind'] == 'recall' else 'Bản trước khi sửa'} · "
              f"{v['who']} · {v['changed_at'].astimezone(zone()):%d/%m %H:%M}", "tone": "bad" if v["kind"] == "recall" else "muted"} for v in rows]
    now = {"title": r["body"][:300] or "(chỉ có tệp)", "subtitle": f"Hiện tại · {r['author']} · gửi {r['created_at'].astimezone(zone()):%d/%m %H:%M}"
           + (f" · tệp: {r['files']}" if r["files"] else ""), "tone": "accent"}
    return ui.card("Lịch sử tin nhắn", [ui.b_list([now, *reversed(items)], "Mới nhất trước")],
                   subtitle="Không bản nào bị xoá — sửa / thu hồi đều giữ bản trước", icon="clock.arrow.circlepath")


class ReactIn(Input):
    message_id: int = field("Tin", ge=1, hidden=True)
    emoji: str = field("Cảm xúc", options={e: e for e in REACTIONS})
    on: bool = field("Bật", True, hidden=True)   # nút thả / gỡ cảm xúc điền sẵn — không ai tick


@m.command("react", resource="talk", title="Thả / bỏ cảm xúc trên tin", agent="deny")
async def react(ctx: Ctx, inp: ReactIn) -> dict[str, Any]:
    """Không thành tin mới, không tăng số chưa đọc; máy đang mở luồng tự vẽ lại. Trợ lý không thả thay người."""
    if inp.emoji not in REACTIONS:
        raise Invalid("Cảm xúc không có trong bộ chọn")
    r = await _message(ctx, inp.message_id)
    if r["recalled_at"] is not None:
        raise Invalid("Tin đã thu hồi")
    if inp.on:
        await ctx.exec("INSERT INTO talk.reactions (message_id, user_id, emoji) VALUES (%s, core.me(), %s) ON CONFLICT DO NOTHING", (r["id"], inp.emoji))
    else:
        await ctx.exec("DELETE FROM talk.reactions WHERE message_id = %s AND user_id = core.me() AND emoji = %s", (r["id"], inp.emoji))
    await _signal(ctx, r["thread_id"], inbox=False)
    return {"message_id": r["id"], "on": inp.on}


class PinIn(Input):
    message_id: int = field("Tin", ge=1, hidden=True)
    pinned: bool = field("Ghim", True, hidden=True)


@m.command("pin", resource="talk", title="Ghim / bỏ ghim tin trong trao đổi", agent="deny")
async def pin(ctx: Ctx, inp: PinIn) -> dict[str, Any]:
    """Ai gửi được vào trao đổi thì ghim được (như nhóm chat); ai ghim, lúc nào hiện trên dải ghim, nhật ký giữ vết."""
    r = await _message(ctx, inp.message_id)
    if r["recalled_at"] is not None:
        raise Invalid("Tin đã thu hồi")
    if inp.pinned:
        await ctx.exec("UPDATE talk.messages SET pinned_at = now(), pinned_by = core.me() WHERE id = %s", (r["id"],))
    else:
        await ctx.exec("UPDATE talk.messages SET pinned_at = NULL, pinned_by = NULL WHERE id = %s", (r["id"],))
    await _signal(ctx, r["thread_id"], inbox=False)
    return {"message_id": r["id"], "pinned": inp.pinned, "summary": "Đã ghim tin" if inp.pinned else "Đã bỏ ghim"}


class ReadIn(Input):
    thread_id: UUID = field("Luồng", hidden=True)
    upto: int = field("Đã đọc tới tin", ge=0, hidden=True)


async def _me_changed(ctx: Ctx, tid: Any) -> None:
    """Báo các máy KHÁC của chính người này (số chưa đọc / tắt tiếng trên menu) — chỉ mã luồng."""
    await ctx.exec("SELECT pg_notify('barista_talk', 'inbox:' || core.tenant() || ':' || core.me() || ':' || %s::text)", (tid,))


@m.command("mark_read", resource="talk", title="Đánh dấu đã đọc trao đổi", agent="deny")
async def mark_read(ctx: Ctx, inp: ReadIn) -> dict[str, int]:
    """Đọc ≠ theo dõi: đọc một luồng không tự đăng ký theo dõi nó."""
    if not await ctx.val("SELECT 1 FROM talk.threads WHERE id = %s", (inp.thread_id,)):
        raise NotFound("Không thấy trao đổi")
    n = await ctx.exec("""INSERT INTO talk.members (thread_id, user_id, following, last_read_id) VALUES (%s, core.me(), false, %s)
                          ON CONFLICT (thread_id, user_id) DO UPDATE SET last_read_id = greatest(talk.members.last_read_id, EXCLUDED.last_read_id)""",
                       (inp.thread_id, inp.upto))
    await _me_changed(ctx, inp.thread_id)
    return {"updated": n}


class FollowIn(Input):
    subject_kind: str = field("Loại đối tượng", hidden=True)
    subject_id: UUID = field("Đối tượng", hidden=True)
    following: bool = field("Theo dõi", True, hidden=True)


@m.command("follow", resource="talk", title="Theo dõi / bỏ theo dõi trao đổi", agent="deny")
async def follow(ctx: Ctx, inp: FollowIn) -> dict[str, Any]:
    tid = await ctx.val("SELECT id FROM talk.threads WHERE subject_kind = %s AND subject_id = %s", (inp.subject_kind, inp.subject_id))
    if tid is None:
        raise NotFound("Chưa có trao đổi nào để theo dõi")
    await ctx.exec("""INSERT INTO talk.members (thread_id, user_id, following, reason) VALUES (%s, core.me(), %s, 'follow')
                      ON CONFLICT (thread_id, user_id) DO UPDATE SET following = EXCLUDED.following""", (tid, inp.following))
    await _me_changed(ctx, tid)
    return {"following": inp.following}


class MuteIn(Input):
    thread_id: UUID = field("Luồng", hidden=True)
    muted: bool = field("Tắt thông báo", True, hidden=True)


@m.command("mute", resource="talk", title="Tắt / bật thông báo một trao đổi", agent="deny")
async def mute(ctx: Ctx, inp: MuteIn) -> dict[str, Any]:
    """Tắt tiếng: không thông báo "có tin mới", không tính vào số trên menu; vẫn thấy luồng + số chưa đọc của luồng. Bị @nhắc vẫn báo."""
    if not await ctx.val("SELECT 1 FROM talk.threads WHERE id = %s", (inp.thread_id,)):
        raise NotFound("Không thấy trao đổi")
    await ctx.exec("""INSERT INTO talk.members (thread_id, user_id, following, muted) VALUES (%s, core.me(), false, %s)
                      ON CONFLICT (thread_id, user_id) DO UPDATE SET muted = EXCLUDED.muted""", (inp.thread_id, inp.muted))
    await _me_changed(ctx, inp.thread_id)
    return {"muted": inp.muted, "summary": "Đã tắt thông báo trao đổi này" if inp.muted else "Đã bật lại thông báo"}


async def _my_threads(ctx: Ctx) -> list[dict[str, Any]]:
    """Luồng tôi theo dõi / có người nhắc tôi, mới nhất trước — CHỈ những luồng tôi vẫn thấy được đối tượng (RLS): đổi người phụ trách là
    tự rơi khỏi đây. Tin riêng: tên là người kia. Tin đã thu hồi không tính chưa đọc."""
    # Đi từ dòng theo dõi của TÔI (vài chục, có chỉ mục) rồi dò luồng theo khoá chính: luật "thấy luồng ⇔ thấy đối tượng" vẫn do RLS của
    # talk.threads kiểm cho từng luồng dò tới. Trước đây planner quét tuần tự MỌI luồng của công ty qua chuỗi quyền rồi mới nối (đo 10/11:
    # 3.933 luồng bị loại / 26 giữ, 300ms ở bước quét) — xem ghi chú nội bộ (barista-docs) §11.
    # Mốc "đã đọc tới" tính sẵn trong mb (không coalesce trong điều kiện): "x.id > mb.last_read_id" thành điều kiện chỉ mục (thread_id, id) ⇒ chỉ chạm
    # tin MỚI; để coalesce trong điều kiện thì Postgres xét nó SAU kiểm quyền luồng của RLS ⇒ mọi tin cũ của luồng qua kiểm quyền (đo: 4.950 lần, §15).
    return await ctx.all(
        """WITH mb AS MATERIALIZED (SELECT thread_id, muted, coalesce(last_read_id, 0) AS last_read_id, following FROM talk.members
                                   WHERE user_id = core.me())
           SELECT t.id, t.subject_kind, t.subject_id, coalesce(dp.name, t.title) AS title, coalesce(t.last_message_at, t.created_at) AS last_message_at,
                  coalesce(mb.muted, false) AS muted, coalesce(dp.active, true) AS peer_active,
                  (SELECT count(*) FROM talk.messages x WHERE x.thread_id = t.id AND x.id > mb.last_read_id
                     AND x.author_id <> core.me() AND x.recalled_at IS NULL) AS unread,
                  (SELECT count(*) FROM talk.messages x WHERE x.thread_id = t.id AND x.id > mb.last_read_id
                     AND core.me() = ANY(x.mentions)) AS mentioned,
                  lm.author AS last_author, lm.body AS last_body, lm.mine AS last_mine, lm.file AS last_file
           FROM mb JOIN LATERAL (SELECT * FROM talk.threads th WHERE th.id = mb.thread_id OFFSET 0) t ON true   -- OFFSET 0: rào, planner dò theo khoá từng luồng thay vì quét hết
           LEFT JOIN LATERAL (SELECT u.name, coalesce(ms.active, false) AS active FROM talk.directs d
                              JOIN core.users u ON u.id = CASE WHEN d.user_a = core.me() THEN d.user_b ELSE d.user_a END
                              LEFT JOIN core.memberships ms ON ms.user_id = u.id AND ms.tenant_id = core.tenant()
                              WHERE t.subject_kind = 'talk.direct' AND d.id = t.subject_id) dp ON true
           LEFT JOIN LATERAL (SELECT u.name AS author, left(x.body, 160) AS body, x.author_id = core.me() AS mine,
                                     (SELECT a.filename FROM talk.attachments a WHERE a.message_id = x.id LIMIT 1) AS file
                              FROM talk.messages x JOIN core.users u ON u.id = x.author_id
                              WHERE x.thread_id = t.id ORDER BY x.id DESC LIMIT 1) lm ON true
           WHERE (t.last_message_id IS NOT NULL OR t.subject_kind IN ('talk.room', 'talk.direct'))
             -- bị @nhắc ⇒ luôn có dòng talk.members (post: reason 'mentioned'); chỉ quét tin tìm @nhắc ở luồng mình là thành viên mà đã bỏ theo dõi
             AND (mb.following OR (mb.thread_id IS NOT NULL
                                   AND EXISTS (SELECT 1 FROM talk.messages x WHERE x.thread_id = t.id AND core.me() = ANY(x.mentions))))
           ORDER BY coalesce(t.last_message_at, t.created_at) DESC, t.id LIMIT 100""")   # t.id: thứ tự ổn định khi cùng giờ (không tuỳ plan)


def _counts(rows: list[dict[str, Any]]) -> tuple[int, int]:
    """Số luồng có tin chưa đọc (luồng tắt tiếng chỉ tính khi bị nhắc) + số luồng nhắc tên tôi — số trên menu / thanh bên."""
    return sum(1 for r in rows if r["unread"] and (not r["muted"] or r["mentioned"])), sum(1 for r in rows if r["mentioned"])


@m.query("mine", resource="talk", title="Trao đổi của tôi")
async def mine(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Thẻ "Trao đổi của tôi": luồng có người nhắc tôi lên đầu, rồi luồng có tin chưa đọc."""
    rows = await _my_threads(ctx)
    rows.sort(key=lambda r: (-(r["mentioned"] > 0), -(r["unread"] > 0 and not r["muted"])))
    unread, mentioned = _counts(rows)
    now = datetime.now(zone())
    items = [{"title": r["title"], "subtitle": f"{r['last_author']}: {_excerpt(r['last_body'] or '', r['last_file'] or '')}" if r["last_author"] else "",
              "badge": ("Nhắc bạn · " if r["mentioned"] else "") + (f"{r['unread']} chưa đọc" if r["unread"] else _ago(now, r["last_message_at"])),
              "tone": "bad" if r["mentioned"] else ("accent" if r["unread"] and not r["muted"] else "muted"), "open": _open(r["subject_kind"], r["subject_id"])}
             for r in rows]
    return ui.card("Trao đổi của tôi", [
        ui.b_kpis([ui.kpi("Có tin chưa đọc", unread, tone="orange" if unread else None), ui.kpi("Nhắc tên bạn", mentioned, tone="red" if mentioned else None)]),
        ui.b_list(items, "") if items else ui.b_callout("muted", "Chưa có trao đổi nào", "Mở một hồ sơ / khách và gõ vào khối “Trao đổi”, hoặc được ai đó @nhắc."),
    ], subtitle="Luồng anh/chị theo dõi hoặc được nhắc tên", icon="bubble.left.and.bubble.right")


@m.query("inbox", resource="talk", title="Danh sách trao đổi cho hộp tin nhắn của app", agent="deny")
async def inbox(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Cùng dữ liệu với ``mine`` nhưng dạng dòng cho cột "Tin nhắn" của app (mới nhất trước, như danh sách chat). Trợ lý dùng ``mine``."""
    rows = await _my_threads(ctx)
    unread, mentioned = _counts(rows)
    return {"unread": unread, "mentioned": mentioned,
            "items": [{"thread_id": str(r["id"]), "subject_kind": r["subject_kind"], "subject_id": str(r["subject_id"]),
                       "kind_label": registry.SUBJECTS.get(r["subject_kind"], ("Hồ sơ", ""))[0], "title": r["title"],
                       "last_author": r["last_author"] or "",
                       "last_body": _excerpt(r["last_body"] or "", r["last_file"] or "") if r["last_author"] else "",
                       "last_mine": bool(r["last_mine"]), "at": r["last_message_at"], "unread": r["unread"], "mentioned": r["mentioned"],
                       "open": _open(r["subject_kind"], r["subject_id"]),
                       # 0115 — chỉ THÊM trường
                       "muted": r["muted"], "section": "direct" if r["subject_kind"] == DIRECT else "room" if r["subject_kind"] == ROOM else "record",
                       "inactive": not r["peer_active"]} for r in rows],
            "can_start_direct": ctx.p.can("talk", "write")}


def _ago(now: datetime, at: datetime | None) -> str:
    if at is None:
        return ""
    d = now - at
    return "vừa xong" if d.total_seconds() < 120 else f"{int(d.total_seconds() // 60)} phút" if d.total_seconds() < 3600 else f"{at.astimezone(zone()):%d/%m %H:%M}"


class ThreadRef(Input):
    thread_id: UUID


@m.query("can_read", resource="talk", title="Kiểm quyền đọc một luồng")
async def can_read(ctx: Ctx, inp: ThreadRef) -> dict[str, bool]:
    """Cho kênh báo tức thì (SSE) kiểm trước khi nghe VÀ trước mỗi tín hiệu — RLS: không thấy đối tượng ⇒ không thấy luồng."""
    if not await ctx.val("SELECT 1 FROM talk.threads WHERE id = %s", (inp.thread_id,)):
        raise NotFound("Không thấy trao đổi")
    return {"ok": True}


@m.command("cards_changed", resource="talk", title="Báo luồng: thẻ vừa đổi trạng thái", agent="deny")
async def cards_changed(ctx: Ctx, inp: ThreadRef) -> dict[str, Any]:
    """Người vừa bấm nút trên thẻ trong luồng ⇒ báo các máy đang mở luồng tải lại (cùng kênh "có tin mới", chỉ mang mã luồng) — thẻ của
    người khác tính lại bằng quyền của họ. Chỉ người xem được luồng mới báo được."""
    if not await ctx.val("SELECT 1 FROM talk.threads WHERE id = %s", (inp.thread_id,)):
        raise NotFound("Không thấy luồng trao đổi")
    await _signal(ctx, inp.thread_id, inbox=False)
    return {"thread_id": inp.thread_id}


# ── Trợ lý trong trao đổi (0115): đọc bằng quyền NGƯỜI HỎI, trả lời riêng người đó — không tự vào luồng chung (0046) ──

class DigestIn(SubjectIn):
    only_unread: bool = field("Chỉ tin chưa đọc", False)


DIGEST_CHARS = 30_000


@m.query("digest", resource="talk", title="Đọc một trao đổi để tóm tắt")
async def digest(ctx: Ctx, inp: DigestIn) -> dict[str, Any]:
    """Cho trợ lý tóm tắt: các tin mới nhất (người nói, giờ, chữ, tên tệp, trả lời tin nào, có ghim) trong giới hạn ~30.000 ký tự, KHÔNG gồm tin đã
    thu hồi. Chạy bằng quyền người hỏi (RLS) — không đánh dấu đã đọc, không ghi gì."""
    subj = await _subject(ctx, inp.subject_kind, inp.subject_id)
    if subj is None:
        raise NotFound("Không thấy trao đổi này (hoặc bạn không có quyền xem)")
    t = await ctx.one("""SELECT t.id, coalesce(mb.last_read_id, 0) AS last_read FROM talk.threads t
                         LEFT JOIN talk.members mb ON mb.thread_id = t.id AND mb.user_id = core.me()
                         WHERE t.subject_kind = %s AND t.subject_id = %s""", (inp.subject_kind, inp.subject_id))
    rows = [] if t is None else await ctx.all(
        """SELECT msg.id, u.name AS author, msg.body, msg.created_at, msg.reply_to, msg.pinned_at IS NOT NULL AS pinned,
                  (SELECT string_agg(a.filename, ', ') FROM talk.attachments a WHERE a.message_id = msg.id) AS files
           FROM talk.messages msg JOIN core.users u ON u.id = msg.author_id
           WHERE msg.thread_id = %s AND msg.recalled_at IS NULL AND (NOT %s OR (msg.id > %s AND msg.author_id <> core.me()))
           ORDER BY msg.id DESC LIMIT 501""", (t["id"], inp.only_unread, t["last_read"]))
    out: list[dict[str, Any]] = []
    used = 0
    for r in rows[:500]:
        used += len(r["body"]) + 60
        if used > DIGEST_CHARS and out:
            break
        out.append({"id": r["id"], "author": r["author"], "at": r["created_at"], "body": r["body"], "files": r["files"] or "",
                    "reply_to": r["reply_to"], "pinned": r["pinned"]})
    out.reverse()
    return {"title": subj["title"], "kind": registry.SUBJECTS[inp.subject_kind][0], "only_unread": inp.only_unread, "count": len(out),
            "truncated": len(out) < len(rows), "messages": out,
            "note": "Tóm tắt cho riêng người hỏi; không gửi vào trao đổi trừ khi người đó tự bấm chia sẻ."}


# ── Tìm trong tin nhắn (⌘K — 0087): chỉ luồng người tìm đọc được (RLS), không gồm tin đã thu hồi ──

@m.search("search_messages", "Tin nhắn", resource="talk", rank=80)
async def search_messages(ctx: Ctx, inp: registry.SearchIn) -> list[dict[str, Any]]:
    """Khớp chữ không phụ thuộc dấu (``talk.fold``), 8 tin mới nhất. Cách viết giữ tốc độ khi chữ HIẾM / không có (N11, đo:
    ghi chú nội bộ (barista-docs) §7):
    - ``OFFSET 0`` ngăn gộp lớp trong: lớp quét tin chỉ mang RLS (luồng đọc được), nên planner ước "mọi tin" và kiểm luồng bằng bảng băm các
      luồng đọc được (một lượt mỗi câu). Để LIKE chung lớp thì planner ước ít dòng khớp và kiểm luồng LẠI ở từng tin (~25 µs × 213.000 tin
      = 5–7 giây khi không tin nào khớp);
    - người viết kiểm bằng subquery vô hướng sau LIKE (không để planner kéo ``core.users`` — ước 1 dòng dưới RLS — thành vòng ngoài);
    - tên người / luồng nối SAU khi đã chọn 8 tin."""
    rows = await ctx.all(
        """WITH hit AS MATERIALIZED (
               SELECT m.id, m.thread_id, m.author_id, m.body, m.created_at
               FROM (SELECT msg.id, msg.thread_id, msg.author_id, msg.body, msg.body_fold, msg.created_at FROM talk.messages msg
                     WHERE msg.recalled_at IS NULL OFFSET 0) m
               WHERE m.body_fold LIKE talk.fold(%s) AND coalesce((SELECT true FROM core.users u WHERE u.id = m.author_id), false)
               ORDER BY m.id DESC LIMIT 8)
           SELECT h.id, h.body, h.created_at, u.name AS author, t.subject_kind, t.subject_id, coalesce(dp.name, t.title) AS title
           FROM hit h JOIN talk.threads t ON t.id = h.thread_id JOIN core.users u ON u.id = h.author_id
           LEFT JOIN LATERAL (SELECT pu.name FROM talk.directs d JOIN core.users pu ON pu.id = CASE WHEN d.user_a = core.me() THEN d.user_b ELSE d.user_a END
                              WHERE t.subject_kind = 'talk.direct' AND d.id = t.subject_id) dp ON true
           ORDER BY h.id DESC""", (like(inp.q),))
    return [{"title": _excerpt(r["body"]), "subtitle": f"{r['author']} · {r['title']} · {r['created_at'].astimezone(zone()):%d/%m %H:%M}",
             "badge": registry.SUBJECTS.get(r["subject_kind"], ("Hồ sơ", ""))[0], "tone": "muted",
             "open": {"query": "talk.thread", "input": {"subject_kind": r["subject_kind"], "subject_id": str(r["subject_id"]), "around": r["id"]}}}
            for r in rows]


# ── Tab "Tệp" của mỗi luồng (0128 §1, như "Ảnh, file, link" của Zalo): đọc dưới RLS người xem — luồng không đọc được ⇒ không thấy gì, tin thu hồi
# không hiện (kể cả với người gửi / talk.audit: xem lại tin thu hồi đi qua "lịch sử" của tin), bị mời khỏi phòng ⇒ mất cả danh sách ngay. ──

FILE_TABS = {"media": "Ảnh", "file": "Tài liệu", "link": "Link", "team": "Tài liệu của đội"}
FILES_PAGE = 200
DOC_LABELS = {"application/pdf": "PDF", "application/msword": "Word", OOXML["docx"][0]: "Word", "application/vnd.ms-excel": "Excel",
              OOXML["xlsx"][0]: "Excel", "application/vnd.ms-powerpoint": "PowerPoint", OOXML["pptx"][0]: "PowerPoint",
              "text/csv; charset=utf-8": "CSV", "text/plain; charset=utf-8": "TXT"}


def _kind_label(content_type: str) -> str:
    return "Ảnh" if content_type in MEDIA else DOC_LABELS.get(content_type, "Tệp")


def _size(n: int) -> str:
    return f"{n / 1048576:.1f} MB".replace(".", ",") if n >= 1048576 else f"{max(1, n // 1024)} KB"


def _at(r: dict[str, Any]) -> str:
    return f"{r['author']} · {r['created_at'].astimezone(zone()):%d/%m/%Y %H:%M}"


def _around(t: dict[str, Any], mid: int) -> dict[str, Any]:
    return {"query": "talk.thread", "input": {"subject_kind": t["subject_kind"], "subject_id": str(t["subject_id"]), "around": mid}}


class FilesIn(Input):
    thread_id: UUID = field("Luồng", hidden=True)
    kind: Literal["media", "file", "link", "team"] = field("Loại", "media", options=FILE_TABS)


class ThreadFiles(Input):
    """Đầu vào điểm cắm ``talk.thread_files``: luồng đang xem + đội gắn với phòng (``team_id``, None nếu không phải phòng đội). Gói cắm vào trả
    ``{title, items: [{title, subtitle?, badge?, tone?, open}], empty?}`` hoặc None — chạy bằng quyền NGƯỜI XEM, chỉ liệt kê thứ người đó đọc được."""
    subject_kind: str
    subject_id: UUID
    team_id: UUID | None = None


@m.query("files", resource="talk", title="Tệp, ảnh, link trong một trao đổi")
async def files(ctx: Ctx, inp: FilesIn) -> dict[str, Any]:
    """Ảnh / tài liệu / link đã gửi trong một luồng (mỗi nhóm một tab, như Zalo), mới nhất trước — mỗi dòng mở đúng tin (``talk.thread`` với
    ``around``); dòng tệp mang thêm ``file`` (mã tệp + loại) để app vẽ ảnh thu nhỏ qua đường tải tệp sẵn có. Không trả nội dung tệp.
    Phòng gắn một đội: thêm tab "Tài liệu của đội" do gói khác góp (điểm cắm ``talk.thread_files``), bằng quyền người xem."""
    t = await ctx.one("SELECT id, subject_kind, subject_id, title FROM talk.threads WHERE id = %s", (inp.thread_id,))
    if t is None:
        raise NotFound("Không thấy trao đổi (hoặc anh/chị không còn xem được)")
    subj = await _subject(ctx, t["subject_kind"], t["subject_id"])
    if subj is None:
        raise NotFound("Không thấy trao đổi (hoặc anh/chị không còn xem được)")
    # Đi từ TIN của đúng luồng (chỉ mục thread_id) rồi dò tệp / link theo message_id; "OFFSET 0" là rào để planner không quét toàn bộ bảng tệp / link
    # của công ty rồi kiểm quyền từng dòng (đo 10/11: 2.128 link quét, 300ms; sau: ~40 dòng dò) — xem ghi chú nội bộ (barista-docs) §11.
    # MỘT lượt qua tin của luồng cho cả ba số (mỗi câu lệnh trên talk.messages planner dựng một lần tập luồng nhìn thấy — đo 10/11: 3 câu = 3 lần
    # quét toàn bộ luồng qua chuỗi quyền, ~240ms); tệp / link đếm bằng truy vấn con có gộp theo từng tin (rào tự nhiên, dò theo chỉ mục message_id).
    counts = await ctx.one(
        """SELECT coalesce(sum(a.media), 0)::int AS media, coalesce(sum(a.file), 0)::int AS file, coalesce(sum(l.n), 0)::int AS link
           FROM talk.messages msg
           LEFT JOIN LATERAL (SELECT count(*) FILTER (WHERE x.content_type = ANY(%(m)s)) AS media, count(*) FILTER (WHERE NOT x.content_type = ANY(%(m)s)) AS file
                              FROM talk.attachments x WHERE x.message_id = msg.id) a ON true
           LEFT JOIN LATERAL (SELECT count(*) AS n FROM talk.links x WHERE x.message_id = msg.id) l ON true
           WHERE msg.thread_id = %(t)s AND msg.recalled_at IS NULL""", {"t": t["id"], "m": list(MEDIA)})
    assert counts is not None
    tabs = {k: f"{FILE_TABS[k]} ({counts[k]})" for k in ("media", "file", "link")}
    # phần góp của gói khác (vd tài liệu trên kệ của đội): chỉ phòng gắn đội; gói tắt / người xem không đọc được ⇒ không có tab
    team = await ctx.val("SELECT team_id FROM talk.rooms WHERE id = %s", (t["subject_id"],)) if t["subject_kind"] == ROOM else None
    extra = [] if team is None else await ctx.plugs("talk.thread_files", subject_kind=t["subject_kind"], subject_id=t["subject_id"], team_id=team)
    if extra:
        tabs["team"] = f"{FILE_TABS['team']} ({sum(len(g.get('items') or []) for g in extra)})"
    kind = inp.kind if inp.kind in tabs else "media"
    blocks: list[dict[str, Any]] = []
    if kind == "team":
        for got in extra:
            blocks.append(ui.b_list(got.get("items") or [], str(got.get("title") or "")) if got.get("items")
                          else ui.b_callout("muted", str(got.get("title") or ""), str(got.get("empty") or "")))
    else:
        if kind == "link":
            rows = await ctx.all("""SELECT l.url, l.host, msg.id AS message_id, msg.body, msg.created_at, u.name AS author
                                    FROM talk.messages msg JOIN LATERAL (SELECT l.url, l.host FROM talk.links l WHERE l.message_id = msg.id OFFSET 0) l ON true
                                    JOIN core.users u ON u.id = msg.author_id
                                    WHERE msg.thread_id = %s AND msg.recalled_at IS NULL ORDER BY msg.id DESC, l.url LIMIT %s""", (t["id"], FILES_PAGE))
            items = [{"title": r["url"], "subtitle": f"{_at(r)} · {_excerpt(r['body'])}", "badge": r["host"], "tone": "muted",
                      "open": _around(t, r["message_id"])} for r in rows]
            empty = "Chưa có link nào trong trao đổi này."
        else:
            rows = await ctx.all(
                f"""SELECT a.id, a.filename, a.content_type, a.size_bytes, msg.id AS message_id, msg.body, msg.created_at, u.name AS author
                    FROM talk.messages msg
                    JOIN LATERAL (SELECT a.id, a.filename, a.content_type, a.size_bytes, a.created_at FROM talk.attachments a WHERE a.message_id = msg.id OFFSET 0) a ON true
                    JOIN core.users u ON u.id = msg.author_id
                    WHERE msg.thread_id = %s AND msg.recalled_at IS NULL AND {"" if kind == "media" else "NOT "}a.content_type = ANY(%s)
                    ORDER BY msg.id DESC, a.created_at LIMIT %s""", (t["id"], list(MEDIA), FILES_PAGE))
            items = [{"title": r["filename"],
                      "subtitle": " · ".join(x for x in (_at(r), _size(r["size_bytes"]), _excerpt(r["body"]) if r["body"].strip() else "") if x),
                      "badge": _kind_label(r["content_type"]), "tone": "accent" if kind == "file" else "muted", "open": _around(t, r["message_id"]),
                      # 0128 — trường THÊM của dòng: cùng hình tệp đính kèm của tin (app tải qua /v1/talk/files/{id}, RLS như tin)
                      "file": {"id": str(r["id"]), "filename": r["filename"], "content_type": r["content_type"], "size_bytes": r["size_bytes"],
                               "is_image": r["content_type"] in IMAGES}} for r in rows]
            empty = "Chưa có ảnh nào trong trao đổi này." if kind == "media" else "Chưa có tài liệu nào trong trao đổi này."
        blocks.append(ui.b_list(items, f"{FILE_TABS[kind]} — mới nhất trước{' (200 gần nhất)' if len(items) >= FILES_PAGE else ''}") if items
                      else ui.b_callout("muted", empty, "Tệp và link gửi trong trao đổi hiện ở đây; bấm một dòng để mở đúng tin."))
    page = ui.card(f"Tệp · {subj['title']}", blocks, subtitle="Ảnh, tài liệu, link đã gửi — chỉ người đọc được trao đổi này thấy",
                   icon="paperclip")
    page["tabs"] = {"param": "kind", "items": [{"key": k, "title": v} for k, v in tabs.items()], "current": kind}
    return page


# ── Tìm tệp (⌘K — 0128 §2): tên tệp + chú thích, không phụ thuộc dấu; chỉ luồng người tìm đọc được; KHÔNG đọc nội dung tệp ──

@m.search("search_files", "Tệp trong trao đổi", resource="talk", rank=85)
async def search_files(ctx: Ctx, inp: registry.SearchIn) -> list[dict[str, Any]]:
    """Gấp chữ bằng ``talk.fold`` ngay trong truy vấn (chưa có chỉ mục — 0128: thêm khi số tệp lớn). Lọc luồng đọc được TRƯỚC (RLS), tin thu hồi
    không tính. Trợ lý dùng qua ``core.find`` bằng quyền người hỏi: trả thẻ dẫn tới tin, không bao giờ nội dung tệp."""
    rows = await ctx.all(
        """WITH t AS MATERIALIZED (SELECT id, subject_kind, subject_id, title FROM talk.threads WHERE last_message_id IS NOT NULL)
           SELECT a.filename, a.content_type, a.size_bytes, msg.id, msg.created_at, u.name AS author, t.subject_kind, t.subject_id,
                  coalesce(dp.name, t.title) AS title
           FROM talk.attachments a JOIN talk.messages msg ON msg.id = a.message_id JOIN t ON t.id = msg.thread_id
           JOIN core.users u ON u.id = msg.author_id
           LEFT JOIN LATERAL (SELECT pu.name FROM talk.directs d JOIN core.users pu ON pu.id = CASE WHEN d.user_a = core.me() THEN d.user_b ELSE d.user_a END
                              WHERE t.subject_kind = 'talk.direct' AND d.id = t.subject_id) dp ON true
           WHERE msg.recalled_at IS NULL AND (talk.fold(a.filename) LIKE talk.fold(%(q)s) OR msg.body_fold LIKE talk.fold(%(q)s))
           ORDER BY msg.id DESC LIMIT 8""", {"q": like(inp.q)})
    return [{"title": r["filename"], "subtitle": f"{r['author']} · {r['title']} · {r['created_at'].astimezone(zone()):%d/%m %H:%M} · {_size(r['size_bytes'])}",
             "badge": _kind_label(r["content_type"]), "tone": "muted", "open": _around(r, r["id"])} for r in rows]


# ── Phòng nhắn tin nhóm (quyết định 0048): một loại đối tượng của chính module này — quyền đọc = là thành viên ──

@m.subject("talk.room", "Phòng")
async def subject_room(ctx: Ctx, inp: registry.SubjectRef) -> dict[str, Any]:
    r = await ctx.one("SELECT id, name FROM talk.rooms WHERE id = %s AND talk.subject_visible_room(id)", (inp.subject_id,))
    if r is None:
        raise NotFound("Không thấy phòng (hoặc anh/chị không ở trong phòng)")
    return {"title": r["name"], "owner_id": None, "team_id": None, "state": "active",
            "open": {"query": "talk.room", "input": {"room_id": str(r["id"])}}}


async def _add_people(ctx: Ctx, room_id: UUID, emails: list[str]) -> list[UUID]:
    """Thêm người (theo email) vào phòng + cho theo dõi luồng của phòng. Trả người MỚI được thêm."""
    added: list[UUID] = []
    for email in dict.fromkeys(e.strip().lower() for e in emails if e.strip()):
        uid = await ctx.val("""SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()
                               WHERE u.email = %s AND mb.active""", (email,))
        if uid is None:
            raise NotFound(f"Không có người {email} trong công ty")
        if await ctx.val("""INSERT INTO talk.room_members (room_id, user_id, added_by) VALUES (%s, %s, core.me())
                            ON CONFLICT DO NOTHING RETURNING user_id""", (room_id, uid)):
            added.append(uid)
    await ctx.exec("""INSERT INTO talk.members (thread_id, user_id, reason)
                      SELECT t.id, rm.user_id, 'follow' FROM talk.threads t JOIN talk.room_members rm ON rm.room_id = t.subject_id
                      WHERE t.subject_kind = 'talk.room' AND t.subject_id = %s
                      ON CONFLICT (thread_id, user_id) DO UPDATE SET following = true""", (room_id,))
    if tid := await ctx.val("SELECT id FROM talk.threads WHERE subject_kind = 'talk.room' AND subject_id = %s", (room_id,)):
        await _signal(ctx, tid)
    return added


class RoomIn(Input):
    name: str = field("Tên phòng", max_length=80, example="Kinh doanh A")
    purpose: str = field("Mục đích", "", kind="textarea", max_length=300, example="Chia việc, báo nhanh khách, chốt lịch", group="Thêm chi tiết")
    members: list[str] = field("Thành viên", [], options_query="admin.member_options", help="Bạn tự là thành viên; thêm người sau cũng được")


@m.command("create_room", resource="talk", title="Tạo phòng nhắn tin")
async def create_room(ctx: Ctx, inp: RoomIn) -> dict[str, Any]:
    name = inp.name.strip()
    rid = await ctx.val("INSERT INTO talk.rooms (name, purpose, created_by) VALUES (%s, %s, core.me()) RETURNING id", (name, inp.purpose.strip()))
    await ctx.exec("INSERT INTO talk.room_members (room_id, user_id, is_owner, added_by) VALUES (%s, core.me(), true, core.me())", (rid,))
    await ctx.val("""INSERT INTO talk.threads (subject_kind, subject_id, title, created_by) VALUES ('talk.room', %s, %s, core.me())
                     RETURNING id""", (rid, name))
    added = await _add_people(ctx, rid, inp.members)
    await ctx.notify(added, kind="room", title=f"{ctx.p.name} thêm anh/chị vào phòng “{name}”", body=inp.purpose.strip(),
                     push="Anh/chị được thêm vào một phòng", open=_open("talk.room", rid))
    return {"room_id": rid, "subject_kind": ROOM, "subject_id": rid, "summary": f"Tạo phòng “{name}” · {len(added) + 1} người"}


class RoomPeople(Input):
    room_id: UUID = field("Phòng", hidden=True)
    members: list[str] = field("Thêm người", options_query="admin.member_options")


@m.command("add_members", resource="talk", title="Thêm người vào phòng")
async def add_members(ctx: Ctx, inp: RoomPeople) -> dict[str, Any]:
    """Người trong phòng thêm được người khác (như nhóm chat); người ngoài phòng không thấy phòng nên không thêm được."""
    room = await subject_room(ctx, registry.SubjectRef(subject_id=inp.room_id))
    added = await _add_people(ctx, inp.room_id, inp.members)
    await ctx.notify(added, kind="room", title=f"{ctx.p.name} thêm anh/chị vào phòng “{room['title']}”", body="",
                     push="Anh/chị được thêm vào một phòng", open=_open("talk.room", inp.room_id))
    return {"added": len(added), "summary": f"Thêm {len(added)} người vào phòng “{room['title']}”"}


class RoomRef(Input):
    room_id: UUID = field("Phòng", hidden=True)


async def _drop_member(ctx: Ctx, room_id: UUID, uid: UUID) -> None:
    """Bỏ một người khỏi phòng: thôi theo dõi luồng + xoá dòng thành viên ⇒ RLS chặn đọc tin / tệp của phòng ngay; người lập phòng rời đi ⇒
    người vào phòng sớm nhất còn lại nhận quản phòng (phòng không bao giờ mất người quản khi còn người)."""
    await ctx.exec("""UPDATE talk.members SET following = false WHERE user_id = %s AND thread_id IN
                      (SELECT id FROM talk.threads WHERE subject_kind = 'talk.room' AND subject_id = %s)""", (uid, room_id))
    await ctx.exec("DELETE FROM talk.room_members WHERE room_id = %s AND user_id = %s", (room_id, uid))
    if not await ctx.val("SELECT 1 FROM talk.room_members WHERE room_id = %s AND is_owner", (room_id,)):
        await ctx.exec("""UPDATE talk.room_members SET is_owner = true WHERE room_id = %s AND user_id =
                          (SELECT user_id FROM talk.room_members WHERE room_id = %s ORDER BY added_at, user_id LIMIT 1)""", (room_id, room_id))


@m.command("leave_room", resource="talk", title="Rời phòng", agent="deny")
async def leave_room(ctx: Ctx, inp: RoomRef) -> dict[str, Any]:
    room = await subject_room(ctx, registry.SubjectRef(subject_id=inp.room_id))
    assert ctx.p.user_id is not None
    await _drop_member(ctx, inp.room_id, ctx.p.user_id)
    return {"summary": f"Đã rời phòng “{room['title']}”"}


class RemoveIn(Input):
    room_id: UUID = field("Phòng", hidden=True)
    person: str = field("Người", options_query="admin.member_options")


@m.command("remove_member", resource="talk", title="Mời ra khỏi phòng", agent="deny")
async def remove_member(ctx: Ctx, inp: RemoveIn) -> dict[str, Any]:
    """Chỉ người quản phòng. Người bị mời ra mất quyền đọc tin, tệp của phòng NGAY (RLS theo thành viên) và thôi nhận tín hiệu tức thì."""
    room = await subject_room(ctx, registry.SubjectRef(subject_id=inp.room_id))
    if not await ctx.val("SELECT 1 FROM talk.room_members WHERE room_id = %s AND user_id = core.me() AND is_owner", (inp.room_id,)):
        raise Denied("Chỉ người quản phòng mời người khác ra được")
    u = await ctx.one("""SELECT u.id, u.name FROM core.users u JOIN talk.room_members rm ON rm.user_id = u.id AND rm.room_id = %s
                         WHERE u.email = %s""", (inp.room_id, inp.person.strip().lower()))
    if u is None:
        raise NotFound("Người này không ở trong phòng")
    if u["id"] == ctx.p.user_id:
        raise Invalid("Muốn ra khỏi phòng thì bấm “Rời phòng”")
    await _drop_member(ctx, inp.room_id, u["id"])
    return {"summary": f"Đã mời {u['name']} ra khỏi phòng “{room['title']}”"}


@m.query("room_people", resource="talk", title="Người trong phòng (không kể mình)")
async def room_people(ctx: Ctx, inp: RoomRef) -> list[dict[str, str]]:
    """Người còn làm trong công ty và đang ở trong phòng, trừ người hỏi: ``[{email, name}]`` — cho gói khác chọn người (vd nút Gọi của gói Gọi video,
    0114). Chỉ thành viên phòng hỏi được (RLS); không phải thành viên ⇒ không thấy phòng."""
    if await ctx.one("SELECT 1 FROM talk.rooms WHERE id = %s AND talk.subject_visible_room(id)", (inp.room_id,)) is None:
        raise NotFound("Không thấy phòng (hoặc anh/chị không ở trong phòng)")
    return [{"email": r["email"], "name": r["name"]} for r in await ctx.all(
        """SELECT u.email, u.name FROM talk.room_members rm JOIN core.users u ON u.id = rm.user_id
           JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant() AND mb.active
           WHERE rm.room_id = %s AND u.id <> core.me() ORDER BY u.name""", (inp.room_id,))]


@m.query("room", resource="talk", title="Thông tin phòng (thành viên, thẻ đã chia sẻ)")
async def room(ctx: Ctx, inp: RoomRef) -> dict[str, Any]:
    """Thẻ bên phải khi mở một phòng: ai trong phòng, thẻ số liệu đã chia sẻ (bấm mở = chạy lại bằng quyền NGƯỜI MỞ)."""
    r = await ctx.one("SELECT id, name, purpose, created_at FROM talk.rooms WHERE id = %s AND talk.subject_visible_room(id)", (inp.room_id,))
    if r is None:
        raise NotFound("Không thấy phòng (hoặc anh/chị không ở trong phòng)")
    people = await ctx.all(
        """SELECT u.name, u.id = core.me() AS me, rm.is_owner, rl.label AS role, tm.name AS team
           FROM talk.room_members rm JOIN core.users u ON u.id = rm.user_id
           LEFT JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()
           LEFT JOIN core.roles rl ON rl.tenant_id = mb.tenant_id AND rl.key = mb.role
           LEFT JOIN LATERAL (SELECT t.name FROM core.team_members x JOIN core.teams t ON t.id = x.team_id WHERE x.user_id = u.id ORDER BY x.is_home DESC LIMIT 1) tm ON true
           WHERE rm.room_id = %s ORDER BY rm.is_owner DESC, u.name""", (inp.room_id,))
    shared = await ctx.all(
        """SELECT msg.card, u.name AS author, msg.created_at FROM talk.messages msg JOIN talk.threads t ON t.id = msg.thread_id
           JOIN core.users u ON u.id = msg.author_id
           WHERE t.subject_kind = 'talk.room' AND t.subject_id = %s AND msg.card IS NOT NULL ORDER BY msg.id DESC LIMIT 10""", (inp.room_id,))
    week = await ctx.val("""SELECT count(*) FROM talk.messages msg JOIN talk.threads t ON t.id = msg.thread_id
                            WHERE t.subject_kind = 'talk.room' AND t.subject_id = %s AND msg.created_at > now() - interval '7 days'""", (inp.room_id,))
    blocks = [ui.b_kpis([ui.kpi("Thành viên", len(people)), ui.kpi("Tin 7 ngày qua", week)]),
              ui.b_list([{"title": p["name"] + (" (bạn)" if p["me"] else ""), "subtitle": " · ".join(x for x in (p["role"], p["team"]) if x),
                          "badge": "Lập phòng" if p["is_owner"] else "", "tone": "accent" if p["is_owner"] else "muted"} for p in people], "Thành viên")]
    if shared:
        blocks.append(ui.b_list([{"title": c["card"].get("title") or c["card"]["query"], "subtitle": f"{c['author']} chia sẻ · {c['created_at'].astimezone(zone()):%d/%m %H:%M}",
                                  "tone": "accent", "open": {"query": c["card"]["query"], "input": c["card"].get("input") or {}}} for c in shared],
                                "Thẻ đã chia sẻ — mở ra là số theo quyền của anh/chị"))
    actions = [ui.act("talk.add_members", "Thêm người", {"room_id": str(r["id"])})]
    if any(p["me"] and p["is_owner"] for p in people):
        actions.append(ui.act("talk.remove_member", "Mời ra khỏi phòng", {"room_id": str(r["id"])}))
    actions.append(ui.act("talk.leave_room", "Rời phòng", {"room_id": str(r["id"])}))
    return ui.card(r["name"], blocks, subtitle=r["purpose"] or "Phòng nhắn tin nhóm", icon="person.3.fill", actions=actions)


# ── Phòng của đội (0128 §5): công ty bật talk.team_rooms ⇒ đội có người là có phòng; vào / ra đội là vào / ra phòng (chỉ người do đồng bộ thêm —
# người được thêm tay giữ nguyên). Đổi tên đội ⇒ đổi tên phòng nếu phòng còn mang tên cũ. Giải thể / xoá đội không xoá phòng. ──

@m.on("admin.team_saved")
async def team_room_on_save(ctx: Ctx, event: registry.Event) -> None:
    await _sync_team_room(ctx, event.payload["team_id"], previous_name=event.payload.get("previous_name"))


@m.on("admin.team_members_changed")
async def team_room_on_members(ctx: Ctx, event: registry.Event) -> None:
    await _sync_team_room(ctx, event.payload["team_id"])


async def _sync_team_room(ctx: Ctx, team_id: Any, *, previous_name: str | None = None) -> None:
    """Chạy bằng quyền hệ thống (bên nghe). Phòng + thành viên phòng ghi ở đây; luồng của phòng và người theo dõi luồng ghi bằng quyền MỘT THÀNH VIÊN
    của phòng (``talk.seat_room``) — chính sách DB của luồng chỉ cho người trong phòng, hệ thống không có đường tắt.

    Cài đặt TẮT (review 08/11): không tạo phòng, không thêm người, không đổi tên — nhưng phòng đội đã có vẫn BỎ người do đồng bộ thêm khi họ rời đội /
    nghỉ việc (không ai giữ quyền đọc lịch sử + tệp của đội cũ chỉ vì công ty đã tắt công tắc). Người được thêm tay giữ nguyên trong mọi trường hợp.
    Người do đồng bộ thêm: quyền quản phòng theo trưởng đội HIỆN TẠI ở mỗi lần đồng bộ."""
    on = await company_settings.enabled(ctx.conn, ctx.p.tenant_id, TEAM_ROOMS)
    team = await ctx.one("SELECT id, name FROM core.teams WHERE id = %s", (team_id,))
    if team is None:
        return
    name = " ".join(team["name"].split())[:80]
    people = {r["user_id"]: r["is_lead"] for r in await ctx.all(
        """SELECT tm.user_id, tm.is_lead FROM core.team_members tm
           JOIN core.memberships mb ON mb.user_id = tm.user_id AND mb.tenant_id = core.tenant() AND mb.active WHERE tm.team_id = %s""", (team_id,))}
    room = await ctx.one("SELECT id, name FROM talk.rooms WHERE team_id = %s", (team_id,))
    if room is None:
        if not on or not people:   # tắt ⇒ không tạo; đội chưa có ai ⇒ chưa có phòng (người đầu tiên vào đội ⇒ có phòng)
            return
        rid = await ctx.val("INSERT INTO talk.rooms (name, purpose, team_id) VALUES (%s, %s, %s) RETURNING id", (name, f"Phòng của đội {name}"[:300], team_id))
    else:
        rid = room["id"]
        if on and previous_name is not None and room["name"] != name and room["name"] == " ".join(previous_name.split())[:80]:
            await ctx.exec("UPDATE talk.rooms SET name = %s WHERE id = %s", (name, rid))
    have = {r["user_id"]: r["added_by"] for r in await ctx.all("SELECT user_id, added_by FROM talk.room_members WHERE room_id = %s", (rid,))}
    added = [u for u in people if u not in have] if on else []
    for u in added:
        await ctx.exec("INSERT INTO talk.room_members (room_id, user_id, is_owner) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (rid, u, people[u]))
    gone = [u for u, by in have.items() if u not in people and by is None]
    if gone:
        await ctx.exec("DELETE FROM talk.room_members WHERE room_id = %s AND user_id = ANY(%s)", (rid, gone))
    # người do đồng bộ thêm: quản phòng = đang là trưởng đội (trưởng bị hạ ⇒ thôi quản; trưởng mới ⇒ quản)
    for u, lead in people.items():
        await ctx.exec("UPDATE talk.room_members SET is_owner = %s WHERE room_id = %s AND user_id = %s AND added_by IS NULL AND is_owner <> %s",
                       (lead, rid, u, lead))
    if not await ctx.val("SELECT 1 FROM talk.room_members WHERE room_id = %s AND is_owner", (rid,)):
        await ctx.exec("""UPDATE talk.room_members SET is_owner = true WHERE room_id = %s AND user_id =
                          (SELECT user_id FROM talk.room_members WHERE room_id = %s ORDER BY added_at, user_id LIMIT 1)""", (rid, rid))
    if added or gone:
        # luồng + người theo dõi: bằng quyền một người CÒN trong phòng (trưởng đội trước, rồi người khác trong phòng)
        stay = [r["user_id"] for r in await ctx.all("SELECT user_id FROM talk.room_members WHERE room_id = %s ORDER BY is_owner DESC, added_at", (rid,))]
        for u in sorted(stay, key=lambda x: (not people.get(x, False), x not in people)):
            who = await ctx.job_for(u)   # bên nghe làm LỆNH bằng quyền người trong phòng (0144)
            if who is not None and who.can("talk", "read"):
                await ctx.call_as("talk.seat_room", who, room_id=rid)
                break
    if added:
        await ctx.notify(added, kind="room", title=f"Anh/chị được thêm vào phòng của đội “{name}”", body="",
                         push="Anh/chị được thêm vào một phòng", open=_open(ROOM, rid))


# không khai callers=: chỉ gọi bằng quyền MỘT THÀNH VIÊN phòng (call_as) — RLS của luồng áp như người đó
@m.command("seat_room", resource="talk", title="Mở luồng phòng + người theo dõi theo thành viên phòng", internal=True, agent="deny")
async def seat_room(ctx: Ctx, inp: RoomRef) -> dict[str, Any]:
    """Luồng của phòng có sẵn (để phòng hiện trong hộp tin), mọi thành viên phòng theo dõi luồng, người không còn trong phòng thôi theo dõi."""
    r = await ctx.one("SELECT id, name FROM talk.rooms WHERE id = %s AND talk.subject_visible_room(id)", (inp.room_id,))
    if r is None:
        raise NotFound("Không thấy phòng (hoặc anh/chị không ở trong phòng)")
    tid = await ctx.val("""INSERT INTO talk.threads (subject_kind, subject_id, title) VALUES ('talk.room', %s, %s)
                           ON CONFLICT (tenant_id, subject_kind, subject_id) DO UPDATE SET title = EXCLUDED.title RETURNING id""", (r["id"], r["name"]))
    await ctx.exec("""INSERT INTO talk.members (thread_id, user_id, reason) SELECT %s, rm.user_id, 'follow' FROM talk.room_members rm WHERE rm.room_id = %s
                      ON CONFLICT (thread_id, user_id) DO UPDATE SET following = true""", (tid, r["id"]))
    await ctx.exec("""UPDATE talk.members SET following = false WHERE thread_id = %s AND following
                      AND user_id NOT IN (SELECT user_id FROM talk.room_members WHERE room_id = %s)""", (tid, r["id"]))
    await _signal(ctx, tid)
    return {"room_id": r["id"], "thread_id": tid}


# ── Tin riêng 1–1 (0115, bước 2 của 0046): đúng hai người, chỉ hai người đó đọc được (RLS ở cả cuộc trò chuyện lẫn tin) ──

@m.subject("talk.direct", "Tin riêng")
async def subject_direct(ctx: Ctx, inp: registry.SubjectRef) -> dict[str, Any]:
    """Tên = người KIA (theo người xem). Người kia rời công ty ⇒ ``inactive``: đọc lại được, không gửi thêm."""
    r = await ctx.one("""SELECT d.id, u.id AS peer, u.name, coalesce(mb.active, false) AS active FROM talk.directs d
                         JOIN core.users u ON u.id = CASE WHEN d.user_a = core.me() THEN d.user_b ELSE d.user_a END
                         LEFT JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()
                         WHERE d.id = %s AND talk.subject_visible_direct(d.id)""", (inp.subject_id,))
    if r is None:
        raise NotFound("Không thấy cuộc trò chuyện riêng này")
    return {"title": r["name"], "owner_id": r["peer"] if r["active"] else None, "team_id": None,
            "state": "active" if r["active"] else "inactive", "open": None}


class DirectIn(Input):
    person: str = field("Nhắn cho", options_query="talk.people", example="nhanvien@congty.vn")
    body: str = field("Tin đầu tiên", "", kind="textarea", max_length=4000, audit=False, help="Để trống ⇒ chỉ mở cuộc trò chuyện")


@m.command("start_direct", resource="talk", title="Nhắn riêng cho một người")
async def start_direct(ctx: Ctx, inp: DirectIn) -> dict[str, Any]:
    """NHẮN TIN / gửi tin / báo / nhắn nhủ / thông báo riêng cho MỘT người (đồng nghiệp, cấp trên, người ngoài công ty có tài khoản). Từ khoá: nhắn tin, nhắn, gửi tin, báo tin, thông báo, liên lạc. Mỗi cặp người một cuộc (mở lại cuộc cũ nếu đã có). Chỉ nhắn được người đang làm trong công ty VÀ có quyền dùng Trao đổi. Trợ lý chỉ soạn:
    người bấm mới mở / gửi."""
    peer = await ctx.one("""SELECT u.id, u.name FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()
                            WHERE u.email = %s AND mb.active""", (inp.person.strip().lower(),))
    if peer is None:
        raise NotFound(f"Không có người {inp.person} trong công ty")
    if peer["id"] == ctx.p.user_id:
        raise Invalid("Không nhắn riêng cho chính mình được — ghi chú riêng thì hỏi trợ lý")
    who = await ctx.principal_of(peer["id"])
    if who is None or not who.can("talk", "read"):
        raise Invalid(f"{peer['name']} chưa được dùng Trao đổi — nhờ quản trị giao quyền")
    a, b = sorted((ctx.p.user_id, peer["id"]), key=str)
    did = await ctx.val("""INSERT INTO talk.directs (user_a, user_b, created_by) VALUES (%s, %s, core.me())
                           ON CONFLICT (tenant_id, user_a, user_b) DO NOTHING RETURNING id""", (a, b)) \
        or await ctx.val("SELECT id FROM talk.directs WHERE user_a = %s AND user_b = %s", (a, b))
    tid = await ctx.val("""INSERT INTO talk.threads (subject_kind, subject_id, title, created_by) VALUES ('talk.direct', %s, 'Tin riêng', core.me())
                           ON CONFLICT (tenant_id, subject_kind, subject_id) DO UPDATE SET title = talk.threads.title RETURNING id""", (did,))
    await ctx.exec("""INSERT INTO talk.members (thread_id, user_id, reason) VALUES (%s, %s, 'follow'), (%s, %s, 'follow')
                      ON CONFLICT (thread_id, user_id) DO UPDATE SET following = true""", (tid, ctx.p.user_id, tid, peer["id"]))
    out: dict[str, Any] = {}
    if inp.body.strip():
        out = await _deliver(ctx, DIRECT, did, inp.body, [], None, None)
    else:
        await _signal(ctx, tid)
    return {"subject_kind": DIRECT, "subject_id": did, "thread_id": tid, "message_id": out.get("message_id"), "open": _open(DIRECT, did),
            "summary": f"Nhắn riêng cho {peer['name']}"}


@m.query("share_targets", resource="talk", title="Nơi gửi được: phòng của tôi và cuộc nhắn riêng đã có")
async def share_targets(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Chọn NƠI GỬI một tệp / một bản (vd "Gửi vào phòng" của Văn bản Word): phòng mình thấy (là thành viên) + cuộc nhắn riêng đã mở. Giá trị
    ``<subject_kind>:<subject_id>`` (tách ở dấu ``:`` CUỐI) — bên gửi không cần biết loại luồng; ``post_file`` kiểm lại quyền khi gửi. Chỉ để chọn."""
    rooms = await ctx.all("SELECT %s || ':' || id AS value, 'Phòng · ' || name AS label FROM talk.rooms WHERE talk.subject_visible_room(id) ORDER BY name",
                          (ROOM,))
    directs = await ctx.all(
        """SELECT %s || ':' || d.id AS value, 'Nhắn riêng · ' || pu.name AS label FROM talk.directs d
           JOIN core.users pu ON pu.id = CASE WHEN d.user_a = core.me() THEN d.user_b ELSE d.user_a END
           WHERE core.me() IN (d.user_a, d.user_b) ORDER BY pu.name""", (DIRECT,))
    return ui.options([*rooms, *directs])


@m.query("people", resource="talk", title="Người nhắn riêng được")
async def people(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Nhắn tin / nhắn cho ai: danh sách người nhắn được (tìm email của người cần nhắn tin, gửi tin, báo tin cho đồng nghiệp, cấp trên, người có tài khoản): người đang làm trong công ty có quyền xem Trao đổi (vai được giao) — chỉ để chọn; ``start_direct`` kiểm lại bằng quyền nạp từ DB."""
    return ui.options(await ctx.all(
        """SELECT u.email AS value, u.name AS label FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()
           WHERE mb.active AND u.id <> core.me() AND EXISTS (SELECT 1 FROM core.grants g WHERE g.tenant_id = core.tenant() AND g.role = mb.role
                                                            AND g.resource = 'talk' AND 'read' = ANY(g.actions))
           ORDER BY u.name"""))


# ---- Câu tín hiệu của bàn việc (0094): khai trên màn (desk=Desk(..., say=...)); agent/desk.py chạy bằng quyền người xem

def _attn_mine(o: Any) -> Line | None:
    return (line("warn", f"{num(kpi(o, 'Nhắc tên bạn'))} trao đổi nhắc tên anh/chị chưa đọc", "Mở để trả lời") if kpi(o, "Nhắc tên bạn") else None)


# ---- Màn hình (0069): module tự khai phân hệ / góp màn vào phân hệ khác; workspaces.py chỉ ráp và lọc theo quyền
m.screens("work", (
    Screen("talk", "Trao đổi của tôi", "bubble.left.and.bubble.right", "talk.mine", desk=Desk("do", say=_attn_mine, rank=90)),   # chỉ ở bàn việc: cột Tin nhắn của thanh bên đã là chính nó (rà menu 20/11/2026)
), rank=10)


# ── HTTP của Trao đổi (``Module.route`` — lõi HTTP gắn, không biết module nào): kênh tức thì + tệp lên / xuống ──

@m.route("GET", "/v1/talk/{thread_id}/stream")
async def http_thread_stream(thread_id: UUID, request: web.Request) -> web.StreamingResponse:
    """SSE cho MỘT luồng trao đổi đang mở (0046): ``event: changed`` khi có tin mới — không mang nội dung. Kiểm quyền đọc luồng trước
    khi nghe và TRƯỚC MỖI tín hiệu (0115): mất quyền ⇒ ``gone``; phiên hết hạn ⇒ ``auth``; lỗi tạm thời ⇒ ``retry`` — cả ba rồi đóng. Khi nối:
    404 / 401 / 503 tương ứng. Lúc rảnh kiểm lại ~5 phút một lần."""
    who = web.Listener(request)
    can = {"thread_id": str(thread_id)}
    first = await who.access("talk.can_read", can)
    if first != "ok":
        raise web.HTTPException({"gone": 404, "auth": 401}.get(first, 503), "Không nghe được luồng này")

    async def lines() -> AsyncIterator[bytes]:
        with web.subscribe(f"thread:{thread_id}") as q:
            idle = 0
            while not await request.is_disconnected():
                try:
                    await asyncio.wait_for(q.get(), 25)
                except TimeoutError:
                    idle += 1
                    if idle % 12 == 0 and (state := await who.access("talk.can_read", can)) != "ok":
                        yield web.EVENT[state]
                        return
                    yield web.PING
                    continue
                await web.settle(q)
                if (state := await who.access("talk.can_read", can)) != "ok":
                    yield web.EVENT[state]
                    return
                yield web.CHANGED

    return web.sse(lines())


@m.route("GET", "/v1/talk/stream")
async def http_inbox_stream(request: web.Request) -> web.StreamingResponse:
    """SSE cho HỘP TIN của một người (0115): ``event: changed`` + ``{"threads": [mã luồng…]}`` khi một luồng người đó theo dõi có tin / sửa /
    thu hồi, hoặc chính người đó đọc / tắt tiếng ở máy khác. KHÔNG mang chữ — app đọc lại ``talk.inbox`` qua RLS. Mỗi mã được kiểm lại quyền đọc
    trước khi báo: người đã mất quyền xem hồ sơ / đã rời phòng không nhận gì, kể cả mã. Phiên hết hạn ⇒ ``auth``; lỗi tạm thời ⇒ ``retry`` (app
    nối lại, đọc lại cả hộp). Một lần ngay khi nối."""
    who = web.Listener(request)
    p = await who.principal()
    if not can_run(p, "talk.inbox"):
        raise web.HTTPException(404, "Không dùng được Trao đổi")

    async def lines() -> AsyncIterator[bytes]:
        with web.inbox_feed(p) as q:
            yield web.CHANGED
            idle = 0
            while not await request.is_disconnected():
                try:
                    batch = {await asyncio.wait_for(q.get(), 25)}
                except TimeoutError:
                    idle += 1
                    if idle % 12 == 0:
                        try:
                            await who.principal()
                        except web.HTTPException as e:
                            yield web.AUTH if e.status_code == 401 else web.GONE
                            return
                        except Exception:   # lỗi tạm khi kiểm lại quyền ⇒ báo app nối lại; log để lỗi dai dẳng không im lặng
                            log.warning("hộp thư tức thì: kiểm quyền lỗi — báo nối lại", exc_info=True)
                            yield web.RETRY
                            return
                    yield web.PING
                    continue
                await web.settle(q, batch)   # một tin ⇒ vài tín hiệu; bấm liên tục ⇒ nhiều tín hiệu: gom lại, mỗi luồng kiểm một lần
                if web.RESYNC in batch:
                    yield web.CHANGED
                    continue
                ok: list[str] = []
                for t in sorted(batch):
                    state = await who.access("talk.can_read", {"thread_id": t})
                    if state == "ok":
                        ok.append(t)
                    elif state in ("auth", "retry"):   # cả kênh: đăng nhập lại / nối lại — không phải mất quyền luồng này
                        yield web.EVENT[state]
                        return
                if ok:
                    yield f"event: changed\ndata: {json.dumps({'threads': ok})}\n\n".encode()

    return web.sse(lines())


@m.route("POST", "/v1/talk/files")
async def http_upload(request: web.Request) -> web.JSONResponse:
    """Gửi MỘT tệp vào trao đổi (0115) — ``multipart/form-data``: ``subject_kind``, ``subject_id``, ``body`` (chú thích — trong THÂN, không lên
    URL / nhật ký truy cập), ``reply_to``, ``file``. Đọc theo khúc, chặn cỡ ngay khi vượt; nội dung tệp đi KÈM lệnh ``talk.post_file`` (không
    base64, không nằm trong đầu vào — đầu vào chỉ có cỡ + sha256). Loại tệp kiểm theo nội dung ở lệnh (một cửa: quyền, chống lặp, nhật ký)."""
    p = await web.caller(request)
    up = await web.multipart(request, FILE_MAX)
    if up.filename is None or not up.data:
        raise web.HTTPException(422, "Thiếu tệp")
    inp: dict[str, Any] = {k: v for k, v in up.fields.items() if k in ("subject_kind", "subject_id", "body", "reply_to") and v != ""}
    inp |= {"filename": up.filename, "size": len(up.data), "sha256": up.sha.hexdigest()}
    return web.result(await web.run(p, "talk.post_file", inp, key=request.headers.get("idempotency-key"), files={"file": up.data}))


@m.route("GET", "/v1/talk/files/{attachment_id}")
async def http_file(attachment_id: UUID, request: web.Request) -> web.Response:
    """Tệp trong trao đổi — quyền đọc = quyền đọc luồng (RLS bảng con của tin), kiểm MỖI lần tải; không cho trình duyệt / proxy giữ bản sao
    (mất quyền là hết tải được). Ảnh / PDF mở ngay, loại khác tải về; kiểu nội dung do server nhận diện lúc gửi, không đoán lại."""
    p = await web.caller(request)
    r = await web.query(p, "talk.file", {"attachment_id": str(attachment_id)})
    if not r.ok:
        return web.result(r)
    disposition = "inline" if r.output["inline"] else "attachment"
    name = r.output["filename"]
    return web.Response(base64.b64decode(r.output["data_b64"]), media_type=r.output["content_type"],
                        headers={"Content-Disposition": f"{disposition}; filename=\"{ascii_name(name)}\"; filename*=UTF-8''{quote(name, safe='')}",
                                 "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})
