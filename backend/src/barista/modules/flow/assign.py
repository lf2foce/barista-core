"""GIAO VIỆC TỪ MỘT CÂU NÓI (0222) — "giao chị Lan làm báo giá trước thứ 6 (trong kế hoạch …)".

Trợ lý KHÔNG tự đoán email / ngày: ``flow.resolve_assignment`` do CODE hiểu ba mảnh của câu rồi trả đúng một kết quả (hoặc nói rõ vì sao chưa chắc):

* người nhận — khớp tên (bỏ dấu, bỏ "anh / chị / em / bạn…") TRONG số người người hỏi giao được (``_candidates``: phạm vi ``flow`` của chính họ). Tên khớp nhiều
  người ⇒ ``ambiguous`` kèm danh sách (trợ lý hỏi lại); không ai khớp ⇒ ``not_found`` (không có trong phạm vi giao việc — không gợi ý người ngoài phạm vi).
* hạn — ``due_phrase.parse_due_phrase`` theo NGÀY CÔNG TY (múi giờ công ty), rồi qua đúng luật hạn của việc (``_due``: rơi ngày nghỉ ⇒ lùi về ngày làm việc trước).
* kế hoạch — tên kế hoạch đang chạy người hỏi THẤY (RLS), khớp không dấu; trống = việc lẻ.

Sau đó trợ lý soạn MỘT ``flow.create_task`` (thẻ đề xuất, người bấm một lần) với ``assignee_email`` / ``due_on`` / ``plan_id`` đã giải — quyền không đổi: người giao
không giao được cho ai ngoài phạm vi, RLS vẫn chặn ghi ngoài phạm vi, trợ lý chỉ đề xuất.
"""

import re
from typing import Any

from barista.api import Ctx, Input, Invalid, field, fold, today
from barista.modules.flow import m
from barista.modules.flow.due_phrase import parse_due_phrase
from barista.modules.flow.tasks import _candidates, _due

HONORIFICS = {"anh", "chi", "em", "ban", "ong", "ba", "co", "chu", "bac", "thay", "sep", "mr", "ms", "mrs"}
WEEKDAY = ("thứ Hai", "thứ Ba", "thứ Tư", "thứ Năm", "thứ Sáu", "thứ Bảy", "Chủ nhật")


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", fold(text))


def match_people(query: str, people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Người khớp nhất với lời nói (hàm thuần): email đúng ⇒ người đó; không thì tên — tầng 1: tên KẾT THÚC bằng đúng các chữ gõ (tên gọi tiếng Việt là chữ
    cuối: "Lan" ⇒ "Nguyễn Thị Lan", không phải "Lan Anh"); tầng 2: có đủ các chữ ở bất kỳ đâu. Trả MỌI người ở tầng tốt nhất có khớp."""
    q = query.strip()
    if "@" in q:
        return [p for p in people if p["email"].lower() == q.lower()]
    t = _tokens(q)
    if len(t) > 1 and t[0] in HONORIFICS:
        t = t[1:]
    if not t:
        return []
    names = [(p, _tokens(p["name"])) for p in people]
    tier1 = [p for p, n in names if n[-len(t):] == t]
    if tier1:
        return tier1
    return [p for p, n in names if all(x in n for x in t)]


def _label(d: Any) -> str:
    return f"{WEEKDAY[d.weekday()]} {d:%d/%m/%Y}"


class ResolveIn(Input):
    who: str = field("Người nhận (đúng lời người dùng nói)", "", max_length=120, example="chị Lan", help="Tên hoặc email — để trống nếu người dùng không nêu (việc cho chính mình)")
    due: str = field("Hạn (đúng lời người dùng nói)", "", max_length=80, example="trước thứ 6",
                     help="Giữ NGUYÊN lời ("
                          "“mai”, “thứ 6”, “cuối tuần”, “20/11”, “tuần sau thứ 3”) — code đổi ra ngày theo múi giờ công ty, đừng tự tính")
    plan: str = field("Kế hoạch (tên)", "", max_length=200, help="Tên hoặc một phần tên kế hoạch đang chạy nếu người dùng nêu — trống = việc lẻ")


@m.query("resolve_assignment", resource="flow", title="Hiểu câu giao việc (người nhận · hạn · kế hoạch)", agent="allow")
async def resolve_assignment(ctx: Ctx, inp: ResolveIn) -> dict[str, Any]:
    """Bước TRƯỚC ``flow.create_task`` khi người dùng giao việc bằng lời ("giao chị Lan làm báo giá trước thứ 6"): đổi tên → email (chỉ trong phạm vi giao
    việc của người hỏi), lời về hạn → ngày (múi giờ công ty, luật ngày nghỉ của đội người nhận), tên kế hoạch → mã (chỉ kế hoạch người hỏi thấy).
    ``status``: ``ok`` ⇒ dùng ``assignee.email`` / ``due_on`` / ``plan.id`` vào ``flow.create_task`` rồi DỪNG; ``ambiguous`` ⇒ hỏi lại người dùng chọn trong
    ``candidates``; ``not_found`` / ``invalid`` ⇒ nói đúng ``issues``, không tự chọn người / ngày khác."""
    issues: list[str] = []
    status = "ok"
    out: dict[str, Any] = {"assignee": None, "due_on": None, "due_label": "", "plan": None, "candidates": [], "issues": issues, "status": status}

    def bad(kind: str, msg: str) -> None:
        nonlocal status
        issues.append(msg)
        if status == "ok" or kind == "not_found":
            status = kind

    assignee_team = None
    if inp.who.strip():
        people = await _candidates(ctx, "")
        hit = match_people(inp.who, people)
        if not hit:
            bad("not_found", f"Không có “{inp.who.strip()}” trong số người anh/chị giao việc được (ngoài phạm vi giao việc hoặc không còn hoạt động) — "
                             "không giao được; có thể nhờ trưởng phòng của người đó")
        elif len(hit) > 1:
            out["candidates"] = [{"name": p["name"], "email": p["email"], "team": p["team"] or "", "role": p["role"] or ""} for p in hit[:8]]
            bad("ambiguous", f"Có {len(hit)} người khớp “{inp.who.strip()}” — hỏi lại người dùng chọn ai")
        else:
            p = hit[0]
            out["assignee"] = {"name": p["name"], "email": p["email"], "team": p["team"] or "", "role": p["role"] or ""}
            assignee_team = await ctx.home_team(p["id"])
    if inp.due.strip():
        d = parse_due_phrase(inp.due, today())
        if d is None:
            bad("invalid", f"Chưa hiểu hạn “{inp.due.strip()}” — hỏi lại người dùng một ngày cụ thể (vd 20/11)")
        else:
            try:
                due, moved, notes = await _due(ctx, d, assignee_team or await ctx.home_team(ctx.p.user_id), None, "")
            except Invalid as e:
                bad("invalid", str(e))
            else:
                out["due_on"] = due.isoformat() if due else None
                out["due_label"] = _label(due) + (f" (người nói {_label(d)} là ngày nghỉ ⇒ lùi về ngày làm việc trước)" if moved else "")
                if notes:
                    out["calendar_notes"] = list(notes)
    if inp.plan.strip():
        want = _tokens(inp.plan)
        rows = await ctx.all("SELECT id, title FROM flow.plans WHERE status = 'open' ORDER BY created_at DESC LIMIT 200")
        exact = [r for r in rows if str(r["id"]) == inp.plan.strip()]
        hits = exact or [r for r in rows if fold(r["title"]).strip() == fold(inp.plan).strip()] or [r for r in rows if all(x in _tokens(r["title"]) for x in want)]
        if not hits:
            bad("not_found", f"Không thấy kế hoạch đang chạy nào tên “{inp.plan.strip()}” trong phạm vi anh/chị xem — tạo việc lẻ hoặc hỏi lại tên")
        elif len(hits) > 1:
            out["candidates"] = [*out["candidates"], *({"plan": r["title"], "plan_id": str(r["id"])} for r in hits[:8])]
            bad("ambiguous", f"Có {len(hits)} kế hoạch khớp “{inp.plan.strip()}” — hỏi lại người dùng chọn kế hoạch nào")
        else:
            out["plan"] = {"id": str(hits[0]["id"]), "title": hits[0]["title"]}
    out["status"] = status
    out["next"] = ("Soạn MỘT flow.create_task: assignee_email, due_on, plan_id lấy đúng từ kết quả này; title là việc người dùng nêu (ngắn). Rồi DỪNG."
                   if status == "ok" else "Chưa soạn thẻ: nói rõ ``issues`` cho người dùng / hỏi lại.")
    return out

