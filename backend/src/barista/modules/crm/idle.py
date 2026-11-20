"""LEAD NẰM IM (ADR 0246): lead đang theo nằm ở chặng hiện tại quá số ngày công ty đặt cho chặng đó (``crm.stages.idle_days``) mà không chuyển chặng, không
được chăm sóc. MỘT định nghĩa cho mọi chỗ dùng (danh sách lead, thẻ khách, Báo cáo chạy song song, module khác sau này gọi ``crm.idle_leads`` bằng quyền người
gọi) — không ai tự viết lại công thức.

"Nằm im từ" = lúc gần nhất trong: lead về (``captured_at`` / tạo), vào chặng hiện tại (lịch sử chặng: tạo / đổi giai đoạn / đổi chặng), chăm sóc gần nhất
(``last_activity_at`` — mọi hình thức trừ ghi chú nội bộ, cùng nghĩa "đã liên hệ" của chia lead). Đổi người phụ trách KHÔNG tính (giao lại không làm lead
đi tiếp). Số ngày tính theo ngày lịch của công ty; quá ngưỡng = số ngày nằm im LỚN HƠN ngưỡng. Chặng không đặt ngưỡng ⇒ không bao giờ nằm im.

Chỉ ĐỌC: không nhắc ai, không chuyển lead — luật tự động theo thời gian là việc của phần khác."""

from datetime import date
from typing import Any, LiteralString
from uuid import UUID

from barista.api import Ctx, Input, field, ui
from barista.modules.crm import m

# biểu thức trên một dòng lead ``l`` (cột id, captured_at, created_at, last_activity_at) — dùng được cả trên crm.leads lẫn tập con đã chọn sẵn cột đó
IDLE_SINCE: LiteralString = """greatest(coalesce(l.captured_at, l.created_at), l.last_activity_at,
                                (SELECT max(e.at) FROM crm.stage_events e WHERE e.lead_id = l.id AND e.kind IN ('created', 'stage', 'pipeline')))"""
IDLE_FOR: LiteralString = f"(core.today() - (({IDLE_SINCE}) AT TIME ZONE core.tz())::date)"
OPEN: LiteralString = "l.stage IN ('lead', 'prospect')"
ARRIVED: LiteralString = "(coalesce(l.captured_at, l.created_at) AT TIME ZONE core.tz())::date"


async def idle_rows(ctx: Ctx, *, lead_ids: list[UUID] | None = None, over_only: bool = True,
                    arrived: tuple[date, date] | None = None, limit: int = 5000) -> list[dict[str, Any]]:
    """Lead đang theo ở chặng CÓ ngưỡng, bằng quyền người gọi (RLS): ``{lead_id, stage_key, stage_label, limit_days, since, days, over}``.
    ``lead_ids``: chỉ các lead này · ``over_only``: chỉ lead đã quá ngưỡng · ``arrived``: chỉ lead VỀ trong khoảng ngày (cohort của báo cáo)."""
    return await ctx.all(
        f"""SELECT l.id AS lead_id, s.key AS stage_key, s.label AS stage_label, s.idle_days AS limit_days, x.since, x.days, x.days > s.idle_days AS over
            FROM crm.leads l JOIN crm.stages s ON s.tenant_id = l.tenant_id AND s.key = l.pipeline_stage AND s.idle_days IS NOT NULL
            CROSS JOIN LATERAL (SELECT {IDLE_SINCE} AS since) y
            CROSS JOIN LATERAL (SELECT (core.today() - (y.since AT TIME ZONE core.tz())::date) AS days, y.since) x
            WHERE {OPEN} AND (%(ids)s::uuid[] IS NULL OR l.id = ANY(%(ids)s::uuid[]))
              AND (%(f)s::date IS NULL OR {ARRIVED} BETWEEN %(f)s AND %(t)s)
              AND (NOT %(over)s OR x.days > s.idle_days)
            ORDER BY x.days - s.idle_days DESC, l.id LIMIT %(n)s""",
        {"ids": lead_ids, "over": over_only, "f": arrived[0] if arrived else None, "t": arrived[1] if arrived else None, "n": limit})


def flag(days: int | None, limit_days: int | None) -> str:
    """Chữ trên danh sách / thẻ khi lead đã quá ngưỡng; chưa quá ⇒ trống."""
    return f"Nằm im {days} ngày" if days is not None and limit_days is not None and days > limit_days else ""


class IdleIn(Input):
    lead_ids: list[UUID] | None = field("Chỉ các lead này", None, hidden=True)


@m.query("idle_leads", resource="crm", title="Lead nằm im quá lâu ở một chặng")
async def idle_leads(ctx: Ctx, inp: IdleIn) -> dict[str, Any]:
    """Lead đang theo đã nằm ở chặng hiện tại lâu hơn ngưỡng công ty đặt cho chặng (không chuyển chặng, không chăm sóc) — người phụ trách thấy lead của
    mình, trưởng nhóm thấy của đội (quyền người xem). Chặng chưa đặt ngưỡng thì không tính."""
    got = await idle_rows(ctx, lead_ids=inp.lead_ids, limit=1000)
    names = {r["id"]: r for r in await ctx.all(
        """SELECT l.id, c.name AS customer, l.offering_code, coalesce(u.name, 'Chưa chia') AS owner FROM crm.leads l
           JOIN crm.customers c ON c.id = l.customer_id LEFT JOIN core.users u ON u.id = l.owner_id WHERE l.id = ANY(%s)""",
        ([r["lead_id"] for r in got],))} if got else {}
    rows = [{"id": str(r["lead_id"]), **{k: v for k, v in names.get(r["lead_id"], {}).items() if k != "id"}, "stage": r["stage_label"],
             "days": r["days"], "limit_days": r["limit_days"], "since": r["since"], "idle_flag": flag(r["days"], r["limit_days"])}
            for r in got if r["lead_id"] in names]
    has_limits = bool(rows) or bool(await ctx.val("SELECT 1 FROM crm.stages WHERE idle_days IS NOT NULL AND active LIMIT 1"))
    return ui.table(rows, [
        ui.col("customer", "Khách"), ui.col("offering_code", "[[offering]]"), ui.col("stage", "Chặng"),
        ui.col("days", "Nằm im (ngày)", "number"), ui.col("limit_days", "Ngưỡng của chặng (ngày)", "number"),
        ui.col("since", "Lần cuối có chuyển động", "datetime"), ui.col("owner", "Phụ trách"),
    ], title="Lead nằm im quá lâu", limit=1000, kpis=[ui.kpi("Lead nằm im", len(rows), tone="orange" if rows else None)],
        empty="Không có lead nào nằm im quá ngưỡng" if has_limits else "Chưa chặng nào đặt ngưỡng nằm im — đặt số ngày ở Chặng của lead")
