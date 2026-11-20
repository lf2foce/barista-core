"""Phần dùng chung của gói Chấm công: người (theo đăng nhập), đăng ký chấm công tại một ngày, ngày lễ theo bộ quy định ``vn-labour``."""

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from barista.api import Ctx, NotFound, regulations
from barista.modules.timekeeping import LABOUR


async def member(ctx: Ctx, email: str) -> dict[str, Any]:
    """Thành viên ĐANG HOẠT ĐỘNG của công ty theo email (người do người nhập nêu — bảng lõi ``core.users`` / ``memberships`` được phép đọc)."""
    row = await ctx.one("""SELECT u.id,u.name,u.email FROM core.users u JOIN core.memberships mb ON mb.user_id=u.id AND mb.tenant_id=core.tenant()
                           WHERE mb.active AND lower(u.email)=lower(%s)""", (email.strip(),))
    if not row:
        raise NotFound(f"Không có thành viên {email.strip()}")
    return row


async def names(ctx: Ctx, ids: list[UUID]) -> dict[str, str]:
    if not ids:
        return {}
    rows = await ctx.all("SELECT id,name FROM core.users WHERE id=ANY(%s)", (ids,))
    return {str(r["id"]): r["name"] for r in rows}


ENROLMENT_ON = """SELECT DISTINCT ON (e.user_id) e.user_id,e.team_id,e.mode,e.valid_from,e.site_id,
                         p.code AS pattern_code,p.name AS pattern_name,p.weekdays,to_char(p.starts,'HH24:MI') AS starts,to_char(p.ends,'HH24:MI') AS ends,
                         p.break_minutes,p.grace_minutes,s.code AS site_code,s.name AS site_name
                  FROM timekeeping.enrolments e JOIN timekeeping.patterns p ON p.tenant_id=e.tenant_id AND p.id=e.pattern_id
                  LEFT JOIN timekeeping.sites s ON s.tenant_id=e.tenant_id AND s.id=e.site_id
                  WHERE e.valid_from<=%(d)s AND (%(u)s::uuid[] IS NULL OR e.user_id=ANY(%(u)s::uuid[]))
                  ORDER BY e.user_id,e.valid_from DESC"""


async def enrolments(ctx: Ctx, on: date, users: list[UUID] | None = None) -> dict[str, dict[str, Any]]:
    """Cách chấm công hiệu lực ngày ``on`` của từng người (RLS: người xem thấy tới đâu thì được tới đó)."""
    return {str(r["user_id"]): r for r in await ctx.all(ENROLMENT_ON, {"d": on, "u": users})}


SEGMENTS = """SELECT e.user_id,e.team_id,e.mode,e.valid_from,p.weekdays,to_char(p.starts,'HH24:MI') AS starts,to_char(p.ends,'HH24:MI') AS ends,
                     p.break_minutes,p.grace_minutes
              FROM timekeeping.enrolments e JOIN timekeeping.patterns p ON p.tenant_id=e.tenant_id AND p.id=e.pattern_id
              WHERE e.valid_from<=%(last)s AND (%(u)s::uuid[] IS NULL OR e.user_id=ANY(%(u)s::uuid[]))
                AND e.valid_from >= coalesce((SELECT max(x.valid_from) FROM timekeeping.enrolments x
                                              WHERE x.tenant_id=e.tenant_id AND x.user_id=e.user_id AND x.valid_from<=%(first)s), '-infinity'::date)
              ORDER BY e.user_id,e.valid_from"""


async def segments(ctx: Ctx, first: date, last: date, users: list[UUID] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Mọi dòng cách chấm có hiệu lực TRONG tháng của từng người (dòng hiệu lực đầu tháng + các dòng bắt đầu trong tháng) — RLS như ``enrolments``."""
    out: dict[str, list[dict[str, Any]]] = {}
    for r in await ctx.all(SEGMENTS, {"first": first, "last": last, "u": users}):
        out.setdefault(str(r["user_id"]), []).append({"from": r["valid_from"], "mode": r["mode"], "team_id": r["team_id"], "user_id": r["user_id"],
                                                     "pattern": pattern_of(r)})
    return out


def pattern_of(e: dict[str, Any]) -> dict[str, Any]:
    return {"weekdays": list(e["weekdays"]), "starts": e["starts"], "ends": e["ends"], "break_minutes": e["break_minutes"],
            "grace_minutes": e["grace_minutes"]}


def holidays(start: date, end: date) -> dict[str, str]:
    """Ngày lễ hưởng lương trong khoảng — mỗi ngày đọc theo phiên bản ``vn-labour`` hiệu lực đúng ngày đó (ngày lễ mới chỉ áp từ ngày hiệu lực)."""
    out: dict[str, str] = {}
    d = start
    while d <= end:
        v = regulations.version_on(LABOUR, d)
        if v is not None and "PUBLIC_HOLIDAYS" in v.params:
            hit = next((h for h in v.params["PUBLIC_HOLIDAYS"]["value"]["dates"] if h["date"] == d.isoformat()), None)
            if hit:
                out[hit["date"]] = hit["label"]
        d += timedelta(days=1)
    return out


def labour_rule(code: str, on: date) -> dict[str, Any] | None:
    """Một quy tắc của ``vn-labour`` hiệu lực ngày ``on`` (giá trị + mã + trạng thái xác minh) — None nếu chưa có."""
    v = regulations.version_on(LABOUR, on)
    if v is None or code not in v.params:
        return None
    p = v.params[code]
    return {**p["value"], "ref": f"{v.ref}:{code}", "status": p["status"], "label": p["label"]}


def month_end(day: date) -> date:
    return regulations.period_end(day.replace(day=1))
