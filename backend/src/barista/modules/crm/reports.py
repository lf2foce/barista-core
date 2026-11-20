"""Số đếm ra ngoài module — marketing, màn điều hành, sức khoẻ dữ liệu — và chỉ số mục tiêu. Chỉ con số / nhãn, không dòng khách."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from barista.api import Ctx, Dim, Input, Measure, MetricIn, NoInput, Ratio, metrics, zone
from barista.api.lead_labels import SOURCES
from barista.modules.crm import STAGES, m


class StatsInput(Input):
    campaign_ids: list[UUID]
    date_from: date | None = None
    date_to: date | None = None


@m.query("lead_stats", resource="crm", title="Số lead theo chiến dịch", internal=True, callers=("mkt",),
         why="Màn Chiến dịch / Hiệu quả quảng cáo (người xem có quyền đọc Marketing, không cần quyền xem khách): đếm lead của chiến dịch trên "
             "TOÀN công ty. Trả CHỈ SỐ ĐẾM theo chiến dịch {leads, qualified, deals, open_leads, outcome_count, outcome_value_vnd, with_ad} — không tên, "
             "không SĐT.")
async def lead_stats(ctx: Ctx, inp: StatsInput) -> dict[str, dict[str, int]]:
    """CHỈ số đếm theo chiến dịch — cho marketing (không tên, không SĐT). Có khoảng ngày ⇒ lead tính theo NGÀY KHÁCH ĐỂ LẠI THÔNG TIN
    (captured_at, trống thì ngày tạo), giờ công ty. Doanh thu / giao dịch ký là NHÓM LEAD của kỳ xem theo trạng thái hiện tại — không
    phải "ký trong kỳ" (nhóm chưa chín ⇒ thấp giả). ``with_ad``: số lead biết MẪU quảng cáo (0210) — độ phủ của số theo mẫu."""
    return await _stats(ctx, "campaign_id", inp.campaign_ids, inp.date_from, inp.date_to)


class AdStatsInput(Input):
    ad_ids: list[UUID]
    date_from: date | None = None
    date_to: date | None = None


@m.query("lead_stats_by_ad", resource="crm", title="Số lead theo mẫu quảng cáo", internal=True, callers=("mkt",),
         why="Màn Mẫu quảng cáo ra khách (0210, người xem có quyền đọc Marketing, không cần quyền xem khách): đếm lead theo MẪU quảng cáo trên TOÀN "
             "công ty — mẫu nào ra khách thật, không chỉ lead form. Trả CHỈ SỐ ĐẾM theo mẫu {leads, qualified, deals, open_leads, outcome_count, "
             "outcome_value_vnd, with_ad} — không tên, không SĐT.")
async def lead_stats_by_ad(ctx: Ctx, inp: AdStatsInput) -> dict[str, dict[str, int]]:
    return await _stats(ctx, "ad_id", inp.ad_ids, inp.date_from, inp.date_to)


async def _stats(ctx: Ctx, key: Literal["campaign_id", "ad_id"], ids: list[UUID], d_from: date | None, d_to: date | None) -> dict[str, dict[str, int]]:
    col = "ad_id" if key == "ad_id" else "campaign_id"   # tên cột vào SQL chỉ là một trong hai hằng chữ, không bao giờ từ người gọi
    rows = await ctx.all(
        f"""SELECT {col} AS k, array_agg(id) AS ids, count(*) AS leads, count(*) FILTER (WHERE stage IN ('prospect', 'deal')) AS qualified,
                  count(*) FILTER (WHERE stage = 'deal') AS deals, count(*) FILTER (WHERE stage IN ('lead', 'prospect')) AS open_leads,
                  count(*) FILTER (WHERE ad_id IS NOT NULL) AS with_ad
           FROM crm.leads
           WHERE {col} = ANY(%(c)s)
             AND (%(f)s::date IS NULL OR (coalesce(captured_at, created_at) AT TIME ZONE core.tz())::date >= %(f)s)
             AND (%(t)s::date IS NULL OR (coalesce(captured_at, created_at) AT TIME ZONE core.tz())::date <= %(t)s)
           GROUP BY {col}""", {"c": ids, "f": d_from, "t": d_to})
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        parts = await ctx.plugs("crm.lead_outcome", system=True, lead_ids=r["ids"])
        out[str(r["k"])] = {"leads": r["leads"], "qualified": r["qualified"], "deals": r["deals"], "open_leads": r["open_leads"],
                            "with_ad": r["with_ad"], "outcome_count": sum(p["count"] for p in parts),
                            "outcome_value_vnd": sum(p["value_vnd"] for p in parts)}
    return out


@m.query("outcome_info", resource=None, title="Kết quả bán đo được thế nào", internal=True, agent="deny", callers=("mkt",),
         why="Màn hiệu quả quảng cáo: gói nào ghi kết quả bán (giao dịch ký, phí…) thì nói TÊN GỌI hai con số đó (đếm / giá trị); không có gói nào thì None — số 0 khi đó "
             "là không đo được, không phải không bán được. Trả chỉ nhãn chữ, không số liệu.")
async def outcome_info(ctx: Ctx, inp: NoInput) -> dict[str, str] | None:
    parts = await ctx.plugs("crm.lead_outcome", system=True, lead_ids=[])
    return parts[0]["labels"] if parts else None


class FunnelIn(Input):
    since: date
    until: date


class SourceIn(Input):
    lead_ids: list[UUID]


@m.query("source_of_leads", resource="crm", title="Nguồn của các lead", internal=True, agent="deny", family="company_figures",
         why="Trang báo cáo quản trị của gói khai consumes(\"company_figures\") (thao tác gọi khai min_scope=\"all\"): cơ cấu thương vụ đã ký theo NGUỒN KHÁCH — "
             "bên gọi đã có mã lead của các thương vụ ký (số liệu toàn công ty của chính gói) và chỉ cần biết lead đó đến từ nguồn nào. Trả {nguồn: [mã lead]} gộp theo mã nguồn + nhãn nguồn — "
             "không tên khách, không SĐT, không chặng. Tối đa 20.000 mã. "
             "[id: module gọi tự có — mã lead lấy từ chính các thương vụ ký mà trang báo cáo của gói đã tổng hợp toàn công ty, không do người xem nhập]")
async def source_of_leads(ctx: Ctx, inp: SourceIn) -> dict[str, Any]:
    rows = await ctx.all("SELECT source, array_agg(id::text) AS ids FROM crm.leads WHERE id = ANY(%s) GROUP BY source", (inp.lead_ids[:20_000],))
    return {"by_source": {r["source"]: r["ids"] for r in rows}, "labels": dict(SOURCES)}


@m.query("funnel", resource="crm", title="Phễu lead theo kỳ", internal=True, agent="deny", family="company_figures",
         why="Màn điều hành của gói khai consumes(\"company_figures\") (người xem có quyền trên màn đó phạm vi CẢ công ty — thao tác gọi khai min_scope=\"all\"): đếm lead toàn công ty theo kỳ. "
             "Trả CHỈ SỐ ĐẾM {leads, contacted, prospect, deal} — không dòng lead nào.")
async def funnel(ctx: Ctx, inp: FunnelIn) -> dict[str, int]:
    """Cho màn điều hành: cohort lead VỀ trong kỳ (``captured_at`` nếu người nhập khai ngày lead về thật, không thì ngày tạo), trạng thái
    HIỆN TẠI — không phải "chuyển chặng trong kỳ"."""
    r = await ctx.one(
        """SELECT count(*) AS leads, count(*) FILTER (WHERE last_activity_at IS NOT NULL) AS contacted,
                  count(*) FILTER (WHERE stage IN ('prospect', 'deal')) AS prospect, count(*) FILTER (WHERE stage = 'deal') AS deal
           FROM crm.leads WHERE (coalesce(captured_at, created_at) AT TIME ZONE core.tz())::date >= %s AND (coalesce(captured_at, created_at) AT TIME ZONE core.tz())::date < %s""",
        (inp.since, inp.until))
    return {k: int(v) for k, v in (r or {}).items()}


# ── Sức khoẻ dữ liệu (module health gom — chỉ số đếm + mốc giờ, không xuất dữ liệu khách ra ngoài trừ vài tên nghi trùng) ──


class HealthIn(Input):
    since: date


def _phones_close(a: str, b: str) -> bool:
    """Cùng độ dài, khác nhau tối đa 2 chữ số — gõ nhầm / đảo số."""
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b, strict=True)) <= 2


LEAD_STALE_H = 48   # nguồn đang hoạt động (có lead trong 30 ngày) quá ngần này giờ không có lead mới ⇒ trễ


@m.query("campaign_leads", resource="crm", title="Số lead theo chiến dịch (cho marketing)", internal=True, agent="deny", callers=("mkt",),
         why="mkt.health_report (phần Sức khoẻ dữ liệu, module health gom bằng quyền hệ thống — bên gọi không kiểm thêm gì): tìm chiến dịch "
             "có lead mà chưa có chi phí. Trả CHỈ SỐ ĐẾM {campaign_id: số lead từ since} — không dòng lead nào.")
async def campaign_leads(ctx: Ctx, inp: HealthIn) -> dict[str, int]:
    """Hợp đồng hẹp cho Marketing (sức khoẻ dữ liệu): chiến dịch nào có lead từ ``since`` — để tìm chiến dịch có lead mà chưa có chi phí."""
    rows = await ctx.all("""SELECT campaign_id, count(*) AS leads FROM crm.leads
                            WHERE campaign_id IS NOT NULL AND (coalesce(captured_at, created_at) AT TIME ZONE core.tz())::date >= %s
                            GROUP BY campaign_id""", (inp.since,))
    return {str(r["campaign_id"]): r["leads"] for r in rows}


@m.query("health_report", resource="crm", title="Sức khoẻ dữ liệu khách / lead", internal=True, agent="deny", callers=("health",),
         why="health._collect (Sức khoẻ dữ liệu — health.overview, min_scope all): tình trạng dữ liệu của module này cả công ty. Trả {rank, issues, notes, fresh} — câu + số đếm, có thể kèm mã để sửa; không thân dữ liệu nghiệp vụ.")
async def health_report(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Phần của khách / lead trong "Sức khoẻ dữ liệu" (module health gom): độ mới từng nguồn lead + bản ghi cần sửa."""
    from barista.api import fold

    sources = await ctx.all(
        """SELECT source, max(created_at) AS last_created, max(captured_at) AS last_captured,
                  count(*) FILTER (WHERE created_at >= now() - interval '7 days') AS week,
                  count(*) FILTER (WHERE created_at >= now() - interval '30 days') AS month
           FROM crm.leads GROUP BY source ORDER BY source""")
    gaps = await ctx.one(
        """SELECT count(*) FILTER (WHERE l.offering_id IS NULL AND l.offering_code = '' AND l.stage IN ('lead', 'prospect')) AS no_offering,
                  count(*) FILTER (WHERE l.offering_id IS NULL AND l.offering_code <> '') AS bad_offering_text,
                  count(*) FILTER (WHERE l.stage IN ('lead', 'prospect') AND NOT EXISTS (
                      SELECT 1 FROM core.memberships mb WHERE mb.user_id = l.owner_id AND mb.tenant_id = (SELECT core.tenant()) AND mb.active)) AS inactive_owner
           FROM crm.leads l""")
    groups: dict[str, list[dict[str, Any]]] = {}
    for c in await ctx.all("SELECT name, phone FROM crm.customers WHERE phone IS NOT NULL"):
        groups.setdefault(" ".join(fold(c["name"]).split()), []).append(c)
    pairs: list[str] = []
    for rows in groups.values():
        for i, a in enumerate(rows):
            pairs += [a["name"] for b in rows[i + 1:] if _phones_close(a["phone"], b["phone"])]
    assert gaps is not None
    now = datetime.now(zone())
    fresh = []
    for r in sources:
        label = str(SOURCES.get(r["source"]) or r["source"])
        active = r["month"] > 0
        stale = active and (r["last_created"] is None or (now - r["last_created"]).total_seconds() > LEAD_STALE_H * 3600)
        fresh.append({"title": f"Lead · {label}", "subtitle": f"{r['week']} lead / 7 ngày", "at": r["last_created"], "stale": stale,
                      "tone": "ok" if active else "muted", "open": "crm.leads",
                      "why": f"Lead {label.lower()} quá {LEAD_STALE_H} giờ không có lead mới ⇒ số lead / chi phí trên mỗi lead mấy ngày gần đây có thể thiếu"})
    names = sorted(set(pairs))[:5]
    return {"rank": 10, "fresh": fresh, "notes": [f"Lead mỗi nguồn đang hoạt động trễ khi > {LEAD_STALE_H} giờ không có lead mới."], "issues": [
        {"key": "no_offering", "n": gaps["no_offering"], "title": "lead đang theo chưa gắn [[offering|l]] quan tâm", "open": "crm.leads",
         "why": "{n} lead chưa gắn [[offering|l]] quan tâm ⇒ không vào số theo [[offering|l]] / phễu [[offering|l]]"},
        {"key": "bad_offering_text", "n": gaps["bad_offering_text"], "title": "lead ghi [[offering|l]] không khớp danh mục", "open": "crm.leads",
         "why": "{n} lead có tên [[offering|l]] lạ ⇒ thêm bí danh trong danh mục để gom đúng"},
        {"key": "inactive_owner", "n": gaps["inactive_owner"], "title": "lead đang theo của người đã nghỉ / bị khoá", "open": "crm.leads",
         "why": "{n} lead của người đã nghỉ ⇒ không ai chăm, số chăm sóc của đội thấp giả"},
        {"key": "dup_customers", "n": len(pairs), "title": "cặp khách nghi trùng (cùng tên, SĐT gần giống)", "open": "crm.leads",
         "subtitle": ", ".join(names), "subtitle_resource": "crm",   # tên khách: chỉ người đọc được khách cả công ty (Sức khoẻ dữ liệu lọc)
         "why": "{n} cặp khách nghi trùng ⇒ số khách / lead có thể cao hơn thật"},
    ]}


@m.metric("metric_leads", "Lead mới", "lead", resource="crm")
async def metric_leads(ctx: Ctx, inp: MetricIn) -> dict[str, Any]:
    """Lead tạo trong kỳ — theo người đang phụ trách / đội của lead."""
    return await metrics.run(ctx, inp, frm="crm.leads l", where="%(ctx)s = '' OR l.offering_code = %(ctx)s",
                             at="l.created_at", person="l.owner_id", team="l.team_id")


@m.metric("metric_appointments", "Khách đặt lịch hẹn", "khách", resource="crm")
async def metric_appointments(ctx: Ctx, inp: MetricIn) -> dict[str, Any]:
    """Số KHÁCH (không phải số lượt) có lịch gặp / tham quan đặt trong kỳ, chưa huỷ — vd mục tiêu "50 khách tham dự sự kiện"."""
    return await metrics.run(ctx, inp, frm="crm.appointments a JOIN crm.leads l ON l.id = a.lead_id",
                             where="a.kind IN ('meet', 'visit') AND a.status <> 'cancelled' AND (%(ctx)s = '' OR l.offering_code = %(ctx)s)",
                             at="a.created_at", person="l.owner_id", team="l.team_id",
                             value="count(DISTINCT l.customer_id)")


@m.metric("metric_calls", "Cuộc gọi khách", "cuộc", resource="crm")
async def metric_calls(ctx: Ctx, inp: MetricIn) -> dict[str, Any]:
    """Cuộc gọi đã ghi trong kỳ (mọi kết quả) — theo người gọi; đội = đội của lead."""
    return await metrics.run(ctx, inp, frm="crm.activities a JOIN crm.leads l ON l.id = a.lead_id",
                             where="a.kind = 'call' AND (%(ctx)s = '' OR l.offering_code = %(ctx)s)",
                             at="a.at", person="a.by_user", team="l.team_id")


@m.metric("metric_connected", "Cuộc gọi được (khách nghe máy)", "cuộc", resource="crm")
async def metric_connected(ctx: Ctx, inp: MetricIn) -> dict[str, Any]:
    return await metrics.run(ctx, inp, frm="crm.activities a JOIN crm.leads l ON l.id = a.lead_id",
                             where="a.kind = 'call' AND a.outcome = 'connected' AND (%(ctx)s = '' OR l.offering_code = %(ctx)s)",
                             at="a.at", person="a.by_user", team="l.team_id")


# ── Tập dữ liệu (0100): lead theo nguồn × [[offering]] × chặng × đội × người giữ × tháng về — cho app tự ghép / trang báo cáo quản trị.
# Cohort theo ngày lead VỀ (captured_at nếu nguồn khai, không thì ngày tạo), trạng thái HIỆN TẠI — cùng nghĩa với phễu ``crm.funnel``.
m.dataset(
    "leads", "Lead", resource="crm",
    description=("Mỗi dòng gốc là một lead (một khách × một [[offering]]). Tháng / ngày = lúc lead VỀ; chặng là chặng HIỆN TẠI, không phải chặng "
                 "lúc đó. Không có tên, SĐT của khách."),
    base="""SELECT l.stage, coalesce(l.source, '') AS source, coalesce(l.offering_code, '') AS offering_code,
                   coalesce(s.label, l.pipeline_stage, '') AS pipeline, coalesce(tm.name, '') AS team, coalesce(u.name, u.email, '') AS owner,
                   (coalesce(l.captured_at, l.created_at) AT TIME ZONE core.tz())::date AS came_on,
                   (l.owner_id IS NOT NULL) AS assigned, (l.last_activity_at IS NOT NULL) AS contacted
            FROM crm.leads l LEFT JOIN crm.stages s ON s.key = l.pipeline_stage
            LEFT JOIN core.teams tm ON tm.id = l.team_id LEFT JOIN core.users u ON u.id = l.owner_id""",
    dims={"stage": Dim("Chặng", "t.stage", labels=STAGES), "pipeline": Dim("Bước bán hàng", "t.pipeline"),
          "source": Dim("Nguồn", "t.source", labels=dict(SOURCES)), "offering": Dim("[[offering]]", "t.offering_code"),
          "team": Dim("Đội", "t.team"), "owner": Dim("Người giữ", "t.owner"),
          "month": Dim("Tháng về", "to_char(t.came_on, 'YYYY-MM')", month=True)},
    measures={"count": Measure("Số lead", "count(*)"), "assigned": Measure("Đã có người giữ", "count(*) FILTER (WHERE t.assigned)"),
              "contacted": Measure("Đã liên hệ", "count(*) FILTER (WHERE t.contacted)"),
              "won": Measure("Thành giao dịch", "count(*) FILTER (WHERE t.stage = 'deal')"),
              "lost": Measure("Mất", "count(*) FILTER (WHERE t.stage = 'lost')"),
              "contact_rate": Ratio("Tỷ lệ đã liên hệ", "contacted", "count"), "win_rate": Ratio("Tỷ lệ thành giao dịch", "won", "count")},
    dates={"came_on": "Ngày lead về"},
)
