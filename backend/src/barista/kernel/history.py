"""LỊCH SỬ THAY ĐỔI THEO TRƯỜNG (ADR 0246, api 1.69) — "ai đổi trường nào từ gì sang gì, lúc nào, qua lệnh nào (người / trợ lý / luật tự động)".

* KHAI: module sở hữu đối tượng khai trường có lịch sử bằng ``m.tracks(loại, trường, nhãn, fmt=…, needs=…)`` (``registry.TRACKED``).
* GHI: lệnh đổi trường gọi ``await ctx.track(loại, id, trường, cũ, mới)`` NGAY trong handler — cùng giao dịch với thay đổi (lệnh hoàn / chạy thử để soạn
  đề xuất ⇒ lịch sử cũng không còn). Chọn lời gọi TƯỜNG MINH thay vì executor tự chụp cột trước / sau: lõi không đọc bảng module (bất biến 3), giá trị nằm
  trong jsonb / bảng khác vẫn ghi được, và chỗ đổi nằm ngay trong mã người đọc thấy. Nạp lịch sử / chỉ ghi nhận (0185) ⇒ không ghi gì.
* AI THẤY: Postgres quyết (``core.field_changes``, migration base/102) — dòng hiện ra khi người xem THẤY ĐỐI TƯỢNG (``core.subject_visible`` ⇒ hàm của module
  sở hữu, như trao đổi / trí nhớ gắn đối tượng). Đổi người phụ trách là người cũ mất lịch sử ngay; không chép quyền sang đây. GIÁ TRỊ của trường nhạy cảm
  (khai ``needs=`` — 0239) chỉ hiện cho người đủ quyền; người khác thấy "đã đổi" — che ở máy chủ, giá trị không rời máy.
* HIỆN: ``block`` (khối "Lịch sử thay đổi" cho thẻ của module — ``barista.api.history.block``) và truy vấn ``core.history`` (trợ lý hỏi "ai đổi hạn việc
  này?" bằng quyền người hỏi). Mới nhất trước, nhãn người đọc, ngày "1 tháng 12, 2026", tiền đủ đồng.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic_core import to_jsonable_python

from barista.kernel import packs, registry, ui
from barista.kernel.clock import shown, zone
from barista.kernel.core_ops import m
from barista.kernel.errors import Denied, NotFound
from barista.kernel.executor import HISTORICAL, Ctx, current_command, current_trace, sealed
from barista.kernel.fields import Input, field
from barista.kernel.text import dong, fold, money

TITLE = "Lịch sử thay đổi"
BLOCK_ROWS = 8


async def record(ctx: Ctx, kind: str, subject_id: Any, field_key: str, old: Any, new: Any) -> bool:
    p, module, _ = sealed(ctx)
    t = registry.TRACKED.get((kind, field_key))
    if t is None:
        raise TypeError(f"ctx.track: {kind}.{field_key} chưa khai bằng m.tracks — lịch sử chỉ cho trường đã khai (nhãn, kiểu hiện, quyền xem giá trị)")
    if t.module != module:
        raise TypeError(f"ctx.track: {kind}.{field_key} thuộc module {t.module} — module {module or '(lõi)'} không ghi lịch sử hộ")
    if ctx.readonly:
        raise TypeError("Căn cứ chỉ đọc — không ghi lịch sử (0071)")
    if HISTORICAL.get():   # dữ liệu nạp từ hệ khác / chỉ ghi nhận là tham chiếu — không thành "vừa đổi" (0185)
        return False
    before, after = to_jsonable_python(old), to_jsonable_python(new)
    if before == after:
        return False
    await ctx.conn.execute(
        "INSERT INTO core.field_changes (subject_kind, subject_id, field, old_value, new_value, user_id, via, system, command, trace)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (kind, subject_id, field_key, Jsonb(before), Jsonb(after), p.user_id, p.via, p.system, current_command() or module, current_trace()))
    return True


def _day(d: date) -> str:
    return f"{d.day} tháng {d.month}, {d.year}"


def _when(at: datetime) -> str:
    t = shown(at).astimezone(zone())
    return f"{_day(t.date())} {t:%H:%M}"


def _value(t: registry.Tracked, v: Any, names: dict[str, str]) -> str:
    if v is None or v == "":
        return t.empty
    try:
        if t.fmt == "person":
            return names.get(str(v), "người đã rời công ty")
        if t.fmt == "date":
            return _day(date.fromisoformat(str(v)[:10]))
        if t.fmt == "money":
            return dong(int(v))
        if t.fmt == "number":
            return money(int(v)) if float(v).is_integer() else str(v).replace(".", ",")
        if t.fmt == "choice":
            return dict(t.choices).get(str(v), str(v))
    except (TypeError, ValueError):
        pass
    return str(v)[:200]


def _how(r: dict[str, Any]) -> str:
    """Qua đường nào — người làm trực tiếp thì không ghi gì thêm."""
    trace = r["trace"] or ""
    if trace.startswith("auto:"):
        return "luật tự động"
    if r["via"] == "agent":
        return "trợ lý"
    if r["via"] == "app":
        return "ứng dụng kết nối"
    if r["via"] == "link":
        return "qua link"
    if trace.startswith("job:") or (r["system"] and r["user_id"] is None):
        return "hệ thống tự động"
    return ""


def _matches(t: registry.Tracked, which: str) -> bool:
    w = fold(which.strip())
    return not w or w == t.field or w in fold(t.label)


async def entries(ctx: Ctx, kind: str, subject_id: Any, *, which: str = "", limit: int = 50) -> tuple[list[dict[str, Any]], bool]:
    """Thay đổi người xem (``ctx``) thấy được của một đối tượng, mới nhất trước — (mục danh sách, còn nữa?). RLS lọc dòng; giá trị nhạy cảm che ở đây."""
    keys = [f for (k, f), t in registry.TRACKED.items() if k == kind and _matches(t, which)]
    if not keys:
        return [], False
    rows = await ctx.all("""SELECT f.at, f.field, f.old_value, f.new_value, f.user_id, f.via, f.system, f.trace, u.name AS who
                            FROM core.field_changes f LEFT JOIN core.users u ON u.id = f.user_id
                            WHERE f.subject_kind = %s AND f.subject_id = %s AND f.field = ANY(%s)
                            ORDER BY f.at DESC, f.id DESC LIMIT %s""", (kind, subject_id, keys, limit + 1))
    more = len(rows) > limit
    rows = rows[:limit]
    people = {str(v) for r in rows if registry.TRACKED[(kind, r["field"])].fmt == "person"
              for v in (r["old_value"], r["new_value"]) if v}
    names: dict[str, str] = {}
    if people:
        names = {str(u["id"]): u["name"] or u["email"]
                 for u in await ctx.all("SELECT id, name, email FROM core.users WHERE id = ANY(%s::uuid[])", (sorted(people),))}
    items = []
    for r in rows:
        t = registry.TRACKED[(kind, r["field"])]
        seen = not t.needs or ctx.sees(list(t.needs))
        title = (f"{t.label}: {_value(t, r['old_value'], names)} → {_value(t, r['new_value'], names)}" if seen
                 else f"{t.label}: đã đổi (bạn không được xem giá trị)")
        who = r["who"] or ("Hệ thống" if r["user_id"] is None else "Người đã rời công ty")
        how = _how(r)
        items.append({"title": title, "subtitle": " · ".join(x for x in (who, _when(r["at"]), how) if x), "tone": "muted"})
    return items, more


def _open(kind: str, subject_id: Any) -> dict[str, Any]:
    return {"query": "core.history", "input": {"subject_kind": kind, "subject_id": str(subject_id)}}


async def block(ctx: Ctx, kind: str, subject_id: Any, *, limit: int = BLOCK_ROWS) -> dict[str, Any] | None:
    """Khối "Lịch sử thay đổi" cho thẻ của module (đặt cạnh khối trao đổi) — bằng quyền người xem; chưa có thay đổi nào người xem thấy ⇒ None."""
    items, more = await entries(ctx, kind, subject_id, limit=limit)
    if not items:
        return None
    if more:
        items.append({"title": "Xem toàn bộ lịch sử thay đổi", "tone": "accent", "open": _open(kind, subject_id)})
    return ui.b_list(items, TITLE)


class HistoryIn(Input):
    subject_kind: str = field("Loại đối tượng", hidden=True, max_length=60)
    subject_id: UUID = field("Đối tượng", hidden=True)
    which: str = field("Trường", "", max_length=60, example="hạn",
                       help="Để trống = mọi trường có lịch sử; gõ tên trường (vd “hạn”, “người phụ trách”) để chỉ xem trường đó")
    limit: int = field("Số dòng", 50, ge=1, le=200, hidden=True)


@m.query("history", resource=None, title=TITLE, category="core")
async def field_history(ctx: Ctx, inp: HistoryIn) -> dict[str, Any]:
    """Ai đổi trường nào của một hồ sơ / khách / việc, từ gì sang gì, lúc nào, qua người / trợ lý / luật tự động — mới nhất trước. Chỉ khi người hỏi
    THẤY đối tượng (cùng luật với trao đổi); giá trị trường nhạy cảm chỉ hiện cho người đủ quyền."""
    subject = registry.SUBJECTS.get(inp.subject_kind)
    title, opens = "", []
    if subject is not None:
        try:
            got = await ctx.call_as(subject[1], ctx.p, subject_id=inp.subject_id)
            title = str((got or {}).get("title") or "")
            opens = [{"title": f"Mở {subject[0].lower()}", **got["open"]}] if got and got.get("open") else []
        except (NotFound, Denied, packs.PackOff):   # không thấy / gói của đối tượng đang tắt
            got = None
        if got is None:
            subject = None
    if subject is None:
        raise NotFound("Không thấy hồ sơ này (hoặc bạn không có quyền xem)")
    items, more = await entries(ctx, inp.subject_kind, inp.subject_id, which=inp.which, limit=inp.limit)
    blocks = [ui.b_list(items, f"{len(items)} thay đổi gần nhất" if more else "") if items
              else ui.b_callout("muted", "Chưa có thay đổi nào được ghi", "Lịch sử bắt đầu từ lúc trường được theo dõi; dữ liệu nạp từ hệ cũ không có lịch sử.")]
    return ui.card(f"{TITLE} · {title}" if title else TITLE, blocks, subtitle=subject[0], icon="clock.arrow.circlepath",
                   open=opens)
