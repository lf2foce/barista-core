"""Khuôn chung cho CHỈ SỐ module khai (``Module.metric`` — 0074 B, 0101): một nơi quyết khoảng ngày [since, until) và cách nhóm theo người / đội,
module chỉ đưa bảng + điều kiện + cột ngày + cột người / đội + biểu thức giá trị (chuỗi trong code, không từ người dùng).

    @m.metric("metric_leads", "Lead mới", "lead", resource="crm")
    async def metric_leads(ctx: Ctx, inp: MetricIn) -> dict[str, Any]:
        return await metrics.run(ctx, inp, frm="crm.leads l", where="%(ctx)s = '' OR l.offering_code = %(ctx)s",
                                 at="l.created_at", person="l.owner_id", team="l.team_id")

Cột mốc là ``timestamptz`` ⇒ đưa ``at="a.at"`` thay cho ``day="(a.at AT TIME ZONE core.tz())::date"``: cùng nghĩa (ngày theo múi giờ công ty, nửa mở
[since, until)) nhưng điều kiện thành khoảng trên chính cột (``a.at >= (since::timestamp AT TIME ZONE core.tz())``) ⇒ dùng được chỉ mục; biểu thức ngày
bọc cột thì mọi dòng của bảng phải qua RLS rồi mới lọc kỳ (đo quy mô khách đầu tiên 11/11: 90.000 hoạt động, 130–800 ms ⇒ vài ms).
"""

from typing import Any, LiteralString

from psycopg import sql

from barista.kernel.executor import Ctx
from barista.kernel.fields import MetricIn


async def run(ctx: Ctx, inp: MetricIn, *, frm: LiteralString, where: LiteralString, person: LiteralString, team: LiteralString,
              day: LiteralString = "", at: LiteralString = "", value: LiteralString = "count(*)") -> dict[str, Any]:
    """``{"value": tổng}``; nhóm theo người / đội thì thêm ``{"rows": [{"key", "value"}]}`` (dòng không có người / đội chỉ tính vào tổng).
    Tổng các dòng ≤ tổng chung CHỈ khi mỗi dòng gốc thuộc một người / đội; ``frm`` nhân dòng theo người đứng tên với ``value`` đếm phân biệt
    (một thương vụ chia cho hai người) thì mỗi người đếm một lần còn tổng chung đếm một lần — đừng cộng các dòng để ra tổng.
    Tham số dùng được trong ``where``: ``%(ctx)s`` (phạm vi, viết hoa), ``%(since)s``, ``%(until)s``. Mốc: ``day`` (cột / biểu thức kiểu date) HOẶC ``at``
    (cột timestamptz — khoảng theo ngày của múi giờ công ty)."""
    if bool(day) == bool(at):
        raise ValueError("metrics.run: khai đúng một trong day= / at=")
    args = {"ctx": inp.context_code.upper(), "since": inp.since, "until": inp.until}
    if at:
        rng = sql.SQL("{at} >= (%(since)s::date::timestamp AT TIME ZONE core.tz()) "
                      "AND (%(until)s::date IS NULL OR {at} < (%(until)s::date::timestamp AT TIME ZONE core.tz()))").format(at=sql.SQL(at))
    else:
        rng = sql.SQL("{day} >= %(since)s AND (%(until)s::date IS NULL OR {day} < %(until)s)").format(day=sql.SQL(day))
    base = sql.SQL("FROM {frm} WHERE ({where}) AND {rng}").format(frm=sql.SQL(frm), where=sql.SQL(where), rng=rng)
    total = await ctx.val(sql.SQL("SELECT coalesce({value}, 0) {base}").format(value=sql.SQL(value), base=base), args)
    out: dict[str, Any] = {"value": int(total or 0)}
    if inp.group_by:
        key = sql.SQL(person if inp.group_by == "person" else team)
        rows = await ctx.all(sql.SQL("SELECT {key}::text AS key, coalesce({value}, 0) AS value {base} AND {key} IS NOT NULL GROUP BY {key}").format(
            key=key, value=sql.SQL(value), base=base), args)
        out["rows"] = [{"key": r["key"], "value": int(r["value"])} for r in rows]
    return out
