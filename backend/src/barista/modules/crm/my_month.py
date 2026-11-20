"""Tổng kết tháng của CHÍNH người hỏi (0198 — công cụ hẹp cho kỹ năng "Tổng kết tháng của tôi"): đã liên hệ bao nhiêu khách, gọi được bao nhiêu, lịch
hẹn, lead đổi giai đoạn, lead mới nhận, khách quan tâm [[offering|l]] nào — tháng này so với tháng trước, MỘT lần gọi (trước đây trợ lý ghép từ ~18
lần gọi công cụ). Giao dịch / doanh số ở công cụ của gói ngành (vd "Giao dịch của tôi"), không ở đây.

"Của tôi" = việc CHÍNH người hỏi làm (chăm sóc / chuyển giai đoạn họ ghi, hẹn họ đặt) + lead họ đang giữ (hẹn đã gặp, lead thành giao dịch — bước tạo
giao dịch có thể do người khác ghi). Đọc bằng RLS của người hỏi (trưởng nhóm vẫn chỉ ra số của riêng mình — xem đội ở màn đội)."""

from datetime import date, datetime, timedelta
from typing import Any

from barista.api import Ctx, Input, Invalid, add_months, day_start, field, today, ui
from barista.modules.crm import m

ROWS = (("contacted", "Khách đã liên hệ"), ("touches", "Lượt chăm sóc"), ("talked", "Lượt nói chuyện được / gặp"),
        ("new_leads", "Lead mới nhận"), ("appts_made", "Lịch hẹn đã đặt"), ("appts_done", "Hẹn đã gặp"),
        ("to_prospect", "Lead chuyển sang tiềm năng"), ("to_deal", "Lead thành giao dịch"), ("to_lost", "Lead chuyển sang mất"))

FIGURES = """
WITH act AS (SELECT a.lead_id, a.kind, a.outcome FROM crm.activities a
             WHERE a.by_user = core.me() AND a.kind <> 'note' AND a.at >= %(lo)s AND a.at < %(hi)s),
     ev AS (SELECT e.kind, e.to_stage, e.by_user, e.data, l.owner_id FROM crm.stage_events e JOIN crm.leads l ON l.id = e.lead_id
            WHERE e.at >= %(lo)s AND e.at < %(hi)s AND e.kind IN ('stage', 'assigned'))
SELECT (SELECT count(DISTINCT lead_id) FROM act) AS contacted,
       (SELECT count(*) FROM act) AS touches,
       (SELECT count(*) FROM act WHERE outcome = 'connected' OR kind IN ('meet', 'visit')) AS talked,
       (SELECT count(*) FROM ev WHERE kind = 'assigned' AND data ->> 'to' = core.me()::text) AS new_leads,
       (SELECT count(*) FROM crm.appointments ap WHERE ap.created_by = core.me() AND ap.created_at >= %(lo)s AND ap.created_at < %(hi)s) AS appts_made,
       (SELECT count(*) FROM crm.appointments ap JOIN crm.leads l ON l.id = ap.lead_id
        WHERE ap.status = 'done' AND ap.closed_at >= %(lo)s AND ap.closed_at < %(hi)s AND (l.owner_id = core.me() OR ap.created_by = core.me())) AS appts_done,
       (SELECT count(*) FROM ev WHERE kind = 'stage' AND to_stage = 'prospect' AND by_user = core.me()) AS to_prospect,
       (SELECT count(*) FROM ev WHERE kind = 'stage' AND to_stage = 'deal' AND (owner_id = core.me() OR by_user = core.me())) AS to_deal,
       (SELECT count(*) FROM ev WHERE kind = 'stage' AND to_stage = 'lost' AND by_user = core.me()) AS to_lost
"""

OFFERINGS = """
SELECT coalesce(nullif(l.offering_code, ''), '—') AS offering,
       count(DISTINCT a.lead_id) FILTER (WHERE a.at >= %(lo)s) AS now_n,
       count(DISTINCT a.lead_id) FILTER (WHERE a.at < %(lo)s) AS before_n
FROM crm.activities a JOIN crm.leads l ON l.id = a.lead_id
WHERE a.by_user = core.me() AND a.kind <> 'note' AND a.at >= %(plo)s AND a.at < %(hi)s
GROUP BY 1 ORDER BY 2 DESC, 3 DESC, 1 LIMIT 10
"""


class MonthIn(Input):
    month: str = field("Tháng", "", max_length=7, example="2026-11", help="Dạng YYYY-MM; trống = tháng này (so với cùng kỳ tháng trước)")


def _range(month: str) -> tuple[date, date, bool]:
    """(ngày đầu tháng, ngày đầu tháng sau, có phải tháng đang chạy)."""
    t = today()
    if not month.strip():
        first = t.replace(day=1)
    else:
        try:
            first = date.fromisoformat(f"{month.strip()}-01")
        except ValueError as e:
            raise Invalid("Tháng dạng YYYY-MM, vd 2026-11") from e
        if first > t:
            raise Invalid("Tháng này chưa tới")
    return first, add_months(first, 1), first == t.replace(day=1)


@m.query("my_month", resource="crm", title="Tổng kết tháng của tôi")
async def my_month(ctx: Ctx, inp: MonthIn) -> dict[str, Any]:
    """Một tháng của người hỏi so với tháng trước: khách đã liên hệ, lượt chăm sóc / nói chuyện được, lead mới nhận, lịch hẹn đặt / đã gặp, lead chuyển
    tiềm năng / thành giao dịch / mất, và [[offering|l]] khách mình chăm quan tâm nhiều nhất. Tháng đang chạy ⇒ so với CÙNG KỲ tháng trước (tới cùng ngày)
    và cả tháng trước. Giao dịch / doanh số: công cụ giao dịch của gói ngành."""
    first, nxt, running = _range(inp.month)
    prev = add_months(first, -1)
    t = today()
    if running:   # tới hết hôm nay; cùng kỳ = cùng số ngày từ đầu tháng trước (tháng trước ngắn hơn ⇒ tới hết tháng trước)
        elapsed = t + timedelta(days=1) - first
        hi, same_hi = day_start(t + timedelta(days=1)), day_start(min(prev + elapsed, first))
    else:
        hi, same_hi = day_start(nxt), day_start(first)

    async def figures(lo: datetime, up: datetime) -> dict[str, int]:
        row = await ctx.one(FIGURES, {"lo": lo, "hi": up})
        return {k: int((row or {}).get(k) or 0) for k, _ in ROWS}

    cur, same = await figures(day_start(first), hi), await figures(day_start(prev), same_hi)
    full = await figures(day_start(prev), day_start(first)) if running else same
    rows = [{"id": k, "metric": label, "now": cur[k], "before": same[k], "delta": cur[k] - same[k], **({"full_before": full[k]} if running else {})}
            for k, label in ROWS]
    now_label = f"Tháng {first:%m/%Y}" + (f" (tới {t:%d/%m})" if running else "")
    before_label = f"Tháng {prev:%m/%Y}" + (" — cùng kỳ" if running else "")
    cols = [ui.col("metric", "Chỉ số", width=220), ui.col("now", now_label, "number"), ui.col("before", before_label, "number"),
            ui.col("delta", "Chênh lệch", "number")]
    if running:
        cols.append(ui.col("full_before", f"Cả tháng {prev:%m/%Y}", "number"))
    offers = await ctx.all(OFFERINGS, {"lo": day_start(first), "hi": hi, "plo": day_start(prev)})
    blocks = [ui.b_kpis([ui.kpi("Khách đã liên hệ", cur["contacted"], hint=f"{before_label}: {same['contacted']}"),
                         ui.kpi("Lịch hẹn đã đặt", cur["appts_made"], hint=f"{before_label}: {same['appts_made']}"),
                         ui.kpi("Chuyển tiềm năng", cur["to_prospect"], hint=f"{before_label}: {same['to_prospect']}"),
                         ui.kpi("Thành giao dịch", cur["to_deal"], hint=f"{before_label}: {same['to_deal']}")]),
              ui.b_table(ui.table(rows, cols, title="So với tháng trước", limit=20)),
              ui.b_table(ui.table([{"id": r["offering"], **r} for r in offers],
                                  [ui.col("offering", "[[offering]]"), ui.col("now_n", f"Khách đã liên hệ — {now_label}", "number"),
                                   ui.col("before_n", f"Tháng {prev:%m/%Y} (cả tháng)", "number")],
                                  title="Khách mình chăm quan tâm [[offering|l]] nào", limit=10, empty="Chưa liên hệ khách nào"))]
    return ui.card("Tổng kết tháng của tôi", blocks, subtitle=f"{now_label} so với {before_label}", icon="chart.bar")
