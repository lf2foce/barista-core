"""NẠP LỊCH TỪ HỆ KHÁC (quyết định 0179): cuộc hẹn / họp đã diễn ra ở hệ cũ vào ``schedule.events`` GIỮ GIỜ GỐC và người làm.

Lịch là MỘT đối tượng (0158) nên dòng nạp vào cùng bảng với lịch đặt trong Barista, đánh dấu ``source = 'import.<hệ nguồn>'`` + ``source_ref`` = mã dòng nguồn
(nạp lại không nhân đôi). Khác ``schedule.create``:
* giờ là giờ GỐC (bắt buộc có múi giờ; chỉ nạp lịch ĐÃ qua — lịch sắp tới đặt bằng ``schedule.create``), trạng thái do hệ nguồn nói (đã xong / đã huỷ / chưa chốt);
* không giữ chỗ người / phòng (không ràng buộc không trùng, không "đủ lượt"), không mời, không báo, không phát sự kiện, không sinh chuỗi lặp; trùng giờ của người
  tham dự chỉ trả cảnh báo (số dòng), không chặn;
* người làm / người tham dự khớp thành viên theo email, không khớp thì giữ mã hệ nguồn (``author_ref`` / ``attendee_refs``) — không bỏ dòng.
Chỉ người quản lý Lịch cả công ty nạp; trợ lý không chạy. Nội dung ghi chú không chép vào nhật ký thao tác."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime

from barista.api import Ctx, Denied, Input, Invalid, field
from barista.modules.schedule import _parties_seen, _put_parties, _soft_clash, m

ORIGIN = r"^[a-z][a-z0-9_]{1,39}$"
STATUS = {"done": "Đã diễn ra", "cancelled": "Đã huỷ", "booked": "Chưa chốt kết quả"}


class ImportPastIn(Input):
    origin: str = field("Hệ nguồn", max_length=40, example="he_cu", help="Mã ngắn của hệ cũ (chữ thường, số, _) — cùng mã cho mọi dòng")
    ext_ref: str = field("Mã dòng ở hệ nguồn", max_length=120, example="hen-1042", help="Duy nhất trong hệ nguồn — nạp lại cùng mã không nhân đôi")
    title: str = field("Việc", max_length=200, example="Hẹn xem căn")
    starts_at: AwareDatetime = field("Bắt đầu", kind="text", example="2026-05-04T09:15:00+07:00", help="ISO 8601 CÓ múi giờ — giờ gốc ở hệ nguồn")
    ends_at: AwareDatetime | None = field("Kết thúc", None, kind="text", help="Trống = Bắt đầu + Thời lượng")
    duration_min: int = field("Thời lượng (phút)", 30, ge=1, le=1440, help="Dùng khi hệ nguồn không có giờ kết thúc")
    status: Literal["done", "cancelled", "booked"] = field("Trạng thái", "done", options=STATUS)
    cancel_reason: str = field("Lý do huỷ", "", max_length=200, help="Chỉ khi trạng thái là Đã huỷ")
    author_email: str = field("Người làm (email)", "", max_length=200, help="Người tạo lịch ở hệ nguồn; người đã nghỉ vẫn nhận nếu từng là thành viên")
    author_ref: str = field("Mã người làm ở hệ nguồn", "", max_length=120, help="Khi không có email khớp thành viên: giữ mã cũ, không bỏ dòng")
    attendee_emails: list[str] = field("Người tham dự (email)", [], kind="multiselect", help="Khớp thành viên thì thành người tham dự; không khớp thì giữ trong mã hệ nguồn")
    attendee_refs: list[str] = field("Mã người tham dự ở hệ nguồn", [], kind="multiselect", help="Người không có email")
    party_ids: list[UUID] = field("Bên ngoài (khách…)", [], kind="multiselect", options_query="parties.options", help="Khách trong danh mục — kiểm bằng quyền người nạp")
    team_code: str = field("Ở đội / cơ sở", "", options_query="admin.team_options", help="Trống = đội chính của người làm")
    note: str = field("Nội dung / kết quả", "", kind="textarea", max_length=4000, audit=False,
                      help="Có thể có dữ liệu cá nhân của khách — không chép vào nhật ký thao tác")


@m.command("import_past", resource="schedule", action="manage", title="Nạp lịch từ hệ khác (giữ giờ gốc)", category="setup", importable=True,
           agent="deny", min_scope="all")
async def import_past(ctx: Ctx, inp: ImportPastIn) -> dict[str, Any]:
    """MỘT cuộc hẹn đã xảy ra ở hệ khác ⇒ ``schedule.events`` (``source = 'import.<hệ nguồn>'``) giữ giờ gốc + người làm. Không giữ chỗ, không mời, không báo.
    Nạp lại cùng (hệ nguồn, mã dòng) ⇒ bỏ qua."""
    if not ctx.p.has_scope("schedule", "all"):
        raise Denied("Nạp lịch từ hệ khác là việc của người quản lý Lịch cả công ty")
    if not re.fullmatch(ORIGIN, inp.origin):
        raise Invalid("Hệ nguồn: chữ thường không dấu, số, _ (2–40 ký tự), bắt đầu bằng chữ")
    ref = inp.ext_ref.strip()
    if not ref:
        raise Invalid("Cần Mã dòng ở hệ nguồn")
    title = " ".join(inp.title.split())
    if not title:
        raise Invalid("Cần tên việc")
    s = inp.starts_at
    e = inp.ends_at or s + timedelta(minutes=inp.duration_min)
    if e <= s:
        raise Invalid("Giờ kết thúc phải sau giờ bắt đầu")
    if s > datetime.now(UTC):
        raise Invalid("Bắt đầu ở tương lai — chỉ nạp lịch đã diễn ra; lịch sắp tới đặt bằng Đặt lịch")
    if inp.status == "cancelled" and not inp.cancel_reason.strip():
        inp = inp.model_copy(update={"cancel_reason": "Đã huỷ ở hệ nguồn"})
    if inp.status != "cancelled" and inp.cancel_reason.strip():
        raise Invalid("Lý do huỷ chỉ ghi cho lịch đã huỷ")
    source = f"import.{inp.origin}"
    if old := await ctx.val("SELECT id FROM schedule.events WHERE source = %s AND source_ref = %s", (source, ref)):   # mọi trạng thái, kể cả đã huỷ
        return {"event_id": old, "inserted": False, "matched_author": None, "matched_attendees": 0, "warnings": []}
    await _parties_seen(ctx, inp.party_ids)

    async def by_email(emails: list[str]) -> tuple[dict[str, UUID], list[str]]:
        want = sorted({x.strip().lower() for x in emails if x.strip()})
        rows = await ctx.all("""SELECT u.id, lower(u.email) AS email FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()
                                WHERE lower(u.email) = ANY(%s)""", (want,)) if want else []
        got = {r["email"]: r["id"] for r in rows}
        return got, [x for x in want if x not in got]

    author_map, _ = await by_email([inp.author_email] if inp.author_email.strip() else [])
    author = next(iter(author_map.values()), None)
    people_map, unmatched = await by_email(inp.attendee_emails)
    people = list(dict.fromkeys(people_map.values()))
    refs = list(dict.fromkeys([*unmatched, *(x.strip() for x in inp.attendee_refs if x.strip())]))
    team = (await ctx.val("SELECT id FROM core.teams WHERE code = %s", (inp.team_code,)) if inp.team_code else await ctx.home_team(author or ctx.p.user_id))
    if inp.team_code and team is None:
        raise Invalid(f"Không có đội {inp.team_code}")
    row = await ctx.one(
        """INSERT INTO schedule.events (title, starts_at, ends_at, team_id, status, source, source_ref, note, created_by, closed_at, cancel_reason,
                                        author_ref, attendee_refs, imported_by, imported_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()) RETURNING id""",
        (title, s, e, team, inp.status, source, ref, inp.note, author or ctx.p.user_id, None if inp.status == "booked" else e, inp.cancel_reason.strip(),
         "" if author else inp.author_ref.strip(), refs, ctx.p.user_id))
    assert row
    eid = row["id"]
    for u in people:   # đã diễn ra ⇒ coi là đã nhận (giờ trả lời = giờ gốc); chưa chốt / huỷ ⇒ chỉ ghi tên, chưa trả lời
        yes = inp.status == "done"
        await ctx.exec("INSERT INTO schedule.attendees (event_id, user_id, rsvp, responded_at) VALUES (%s, %s, %s, %s)",
                       (eid, u, "accepted" if yes else "invited", s if yes else None))
    await _put_parties(ctx, eid, inp.party_ids)
    warnings = await _soft_clash(ctx, people, s, e, eid) if inp.status != "cancelled" else []
    return {"event_id": eid, "inserted": True, "matched_author": author is not None, "matched_attendees": len(people), "warnings": warnings}
