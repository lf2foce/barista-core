"""PHÁT HIỆN của khách / lead cho Báo cáo chạy song song (0230): lead chưa có người nhận (nằm bao lâu, tiền quảng cáo đang nằm trong đó), khách để lại thông tin
nhiều lần (trả tiền quảng cáo hai lần), tốc độ gọi lại + chuyển đổi (CHƯA ĐO ĐƯỢC khi người bán chưa ghi việc trong hệ thống). Chỉ ĐỌC. Hợp đồng: ``kernel/findings.py``."""

from datetime import date
from typing import Any

from barista.api import Ctx, Input, ui
from barista.api import findings as fd
from barista.api.lead_labels import SOURCES
from barista.modules.crm import m

DUP_DAYS = 30            # cùng một khách để lại thông tin lần nữa trong ngần này ngày kể từ lần đầu trong kỳ ⇒ lead trùng
AGE_BUCKETS = (("Dưới 1 ngày", 0, 1), ("1 – 3 ngày", 1, 3), ("3 – 7 ngày", 3, 7), ("7 – 30 ngày", 7, 30), ("Trên 30 ngày", 30, 100_000))
ACTIVITY_COVERAGE_MIN = 0.2   # ≥ 20% lead đã có người nhận phải có ghi chăm sóc / cuộc gọi thì mới kết luận về tốc độ gọi và chuyển đổi
ARRIVED = "(coalesce(l.captured_at, l.created_at) AT TIME ZONE core.tz())::date"


class WindowIn(Input):
    date_from: date
    date_to: date


class RowsIn(WindowIn):
    key: str


@m.query("lead_span", resource="crm", title="Lead vào hệ thống từ ngày nào", internal=True, agent="deny", callers=("findings", "mkt"),
         why="Báo cáo chạy song song (findings, min_scope all) và phần phát hiện của marketing: lead đầu tiên vào hệ thống từ ngày nào, bao nhiêu lead quảng cáo trong kỳ "
             "và bao nhiêu trong đó gắn được chiến dịch — để cắt kỳ so chi phí cho khớp độ phủ dữ liệu. Trả CHỈ SỐ ĐẾM {first_on, total, ads, ads_attributed}.")
async def lead_span(ctx: Ctx, inp: WindowIn) -> dict[str, Any]:
    r = await ctx.one(
        f"""SELECT min((l.created_at AT TIME ZONE core.tz())::date) AS first_on,
                   count(*) FILTER (WHERE {ARRIVED} BETWEEN %(f)s AND %(t)s) AS total,
                   count(*) FILTER (WHERE l.source = 'ads' AND {ARRIVED} BETWEEN %(f)s AND %(t)s) AS ads,
                   count(*) FILTER (WHERE l.source = 'ads' AND l.campaign_id IS NOT NULL AND {ARRIVED} BETWEEN %(f)s AND %(t)s) AS ads_attributed
            FROM crm.leads l""", {"f": inp.date_from, "t": inp.date_to})
    assert r is not None
    return {"first_on": r["first_on"], "total": int(r["total"]), "ads": int(r["ads"]), "ads_attributed": int(r["ads_attributed"])}


def _drill(key: str, inp: WindowIn, sum_col: str | None = None) -> dict[str, Any]:
    """``sum_col`` có ⇒ bấm số ra bảng GỘP mà tổng cột đó = con số (khớp đến từng đơn vị); không ⇒ danh sách dòng lẻ (có thể bị cắt ở trần)."""
    return ui.drill("crm.finding_rows", {"key": key, "date_from": inp.date_from, "date_to": inp.date_to}, sum=sum_col)


async def _cpl(ctx: Ctx, inp: WindowIn) -> dict[str, dict[str, int]] | None:
    """Chi phí / lead của từng chiến dịch (None = không có gói marketing hoặc dữ liệu chi phí chưa đáng tin)."""
    return await ctx.call_system_optional("mkt.campaign_cpl", date_from=inp.date_from, date_to=inp.date_to)


async def _unowned(ctx: Ctx, inp: WindowIn) -> dict[str, Any]:
    p = {"f": inp.date_from, "t": inp.date_to}
    r = await ctx.one(
        f"""SELECT count(*) AS n, count(*) FILTER (WHERE source = 'ads') AS ads, coalesce(avg(age), 0) AS avg_days,
                   count(*) FILTER (WHERE age < 1) AS b0, count(*) FILTER (WHERE age >= 1 AND age < 3) AS b1, count(*) FILTER (WHERE age >= 3 AND age < 7) AS b2,
                   count(*) FILTER (WHERE age >= 7 AND age < 30) AS b3, count(*) FILTER (WHERE age >= 30) AS b4
            FROM (SELECT l.source, extract(epoch FROM now() - coalesce(l.captured_at, l.created_at)) / 86400 AS age FROM crm.leads l
                  WHERE l.owner_id IS NULL AND l.stage IN ('lead', 'prospect') AND {ARRIVED} BETWEEN %(f)s AND %(t)s) x""", p)
    assert r is not None
    buckets = {label: int(r[f"b{i}"]) for i, (label, _, _) in enumerate(AGE_BUCKETS)}
    per_campaign = {str(x["campaign_id"]): int(x["n"]) for x in await ctx.all(
        f"""SELECT l.campaign_id, count(*) AS n FROM crm.leads l WHERE l.owner_id IS NULL AND l.stage IN ('lead', 'prospect') AND l.campaign_id IS NOT NULL
              AND {ARRIVED} BETWEEN %(f)s AND %(t)s GROUP BY l.campaign_id""", p)}
    total = int(await ctx.val(f"SELECT count(*) FROM crm.leads l WHERE l.stage IN ('lead', 'prospect') AND {ARRIVED} BETWEEN %(f)s AND %(t)s", p))
    return {"n": int(r["n"]), "ads": int(r["ads"]), "avg_days": float(r["avg_days"]), "buckets": buckets, "per_campaign": per_campaign, "open_total": total}


async def _duplicates(ctx: Ctx, inp: WindowIn) -> list[dict[str, Any]]:
    """Mỗi khách có ≥ 2 lead quảng cáo trong ``DUP_DAYS`` ngày kể từ lần đầu trong kỳ: số lead THỪA (lead thứ 2 trở đi) + chiến dịch của chúng."""
    return await ctx.all(
        f"""WITH x AS (SELECT l.id, l.customer_id, l.campaign_id, {ARRIVED} AS day,
                            row_number() OVER (PARTITION BY l.customer_id ORDER BY coalesce(l.captured_at, l.created_at), l.id) AS rn,
                            min({ARRIVED}) OVER (PARTITION BY l.customer_id) AS first_day
                     FROM crm.leads l WHERE l.source = 'ads' AND {ARRIVED} BETWEEN %(f)s AND %(t)s)
            SELECT x.customer_id, c.name, count(*) FILTER (WHERE x.rn > 1 AND x.day - x.first_day <= %(d)s) AS extra, count(*) AS leads,
                   count(DISTINCT x.campaign_id) AS campaigns, min(x.day) AS first_day, max(x.day) AS last_day,
                   array_agg(x.campaign_id) FILTER (WHERE x.rn > 1 AND x.day - x.first_day <= %(d)s AND x.campaign_id IS NOT NULL) AS extra_campaigns
            FROM x JOIN crm.customers c ON c.id = x.customer_id
            GROUP BY x.customer_id, c.name HAVING count(*) FILTER (WHERE x.rn > 1 AND x.day - x.first_day <= %(d)s) > 0
            ORDER BY extra DESC, c.name""", {"f": inp.date_from, "t": inp.date_to, "d": DUP_DAYS})


@m.query("findings_report", resource="crm", title="Phát hiện của khách / lead (Báo cáo chạy song song)", internal=True, agent="deny", callers=("findings",),
         why="findings._collect (Báo cáo chạy song song — findings.overview, min_scope all): các phát hiện về lead chưa có người nhận, khách trùng, tốc độ gọi, chuyển đổi cho "
             "một kỳ, cả công ty. Trả {findings, context, notes} — số đếm + công thức; không tên khách, không SĐT.")
async def findings_report(ctx: Ctx, inp: WindowIn) -> dict[str, Any]:
    """Chỉ đọc. Số tiền quy từ chi phí / lead của chiến dịch (nếu có gói marketing và dữ liệu chi phí đáng tin) — không thì chỉ có số đếm."""
    out: list[dict[str, Any]] = []
    cpl = await _cpl(ctx, inp)
    un = await _unowned(ctx, inp)
    total_leads = int(await ctx.val(f"SELECT count(*) FROM crm.leads l WHERE {ARRIVED} BETWEEN %(f)s AND %(t)s", {"f": inp.date_from, "t": inp.date_to}))
    ctxk = [fd.metric("Lead vào hệ thống trong kỳ", total_leads), fd.metric("Trong đó chưa có người nhận", un["n"])]

    # 1 — lead chưa có người nhận
    if un["open_total"] == 0:
        out.append(fd.unmeasured("crm.unowned_leads", "Lead chưa có người nhận", "leads", why_not="trong kỳ chưa có lead nào vào hệ thống",
                                 unlock="có lead vào hệ thống trong kỳ này"))
    else:
        money = None
        if cpl:
            money = sum(n * cpl[c]["spend"] / cpl[c]["leads"] for c, n in un["per_campaign"].items() if c in cpl and cpl[c]["leads"])
        old = un["buckets"]["7 – 30 ngày"] + un["buckets"]["Trên 30 ngày"]
        metrics = [*([fd.metric("Lead chưa có người nhận", un["n"], drill=_drill("unowned_groups", inp, "n"))] if money is not None else []),
                   fd.metric("Trên tổng lead đang chăm", un["open_total"]),
                   fd.metric("Tuổi trung bình (ngày)", round(un["avg_days"], 1)), fd.metric("Nằm quá 7 ngày", old),
                   *(fd.metric(f"Tuổi: {k.lower()}", v) for k, v in un["buckets"].items())]
        impact = fd.impact_money(money, "tiền quảng cáo nằm trong lead chưa ai nhận") if money is not None else \
            fd.impact_count(un["n"], "lead chưa có người nhận", _drill("unowned_groups", inp, "n"))
        share = fd.pct_text(un["n"], un["open_total"])
        out.append(fd.finding(
            "crm.unowned_leads", "Lead đã về nhưng chưa có người nhận", "leads",
            summary=(f"{fd.n_text(un['n'])} trong {fd.n_text(un['open_total'])} lead đang chăm ({share}) chưa giao cho ai; {fd.n_text(old)} lead đã nằm quá 7 ngày"
                     + (f". Tiền quảng cáo đã chi cho các lead này ước tính {fd.money_text(money)}." if money is not None else ".")),
            formula=("Tiền ước tính = Σ theo chiến dịch (số lead chưa có người nhận × chi phí trung bình mỗi lead của chiến dịch trong kỳ); "
                     "chi phí trung bình mỗi lead = chi phí chiến dịch ÷ lead của chiến dịch trong kỳ. Lead không gắn chiến dịch không quy ra tiền."),
            impact=impact, metrics=metrics, confidence="high" if money is None else "medium",
            caveats=[*(["Chưa quy ra tiền: không có số chi phí đáng tin cho kỳ này — chỉ có số lead."] if money is None else []),
                     "Lead chưa có người nhận có thể đang được xử lý ở hệ đang dùng song song; con số cho biết hệ thống mới CHƯA thấy ai nhận."],
            drill=_drill("unowned_leads", inp)))

    # 1b — lead nằm im quá ngưỡng của chặng (ADR 0246): một định nghĩa ở crm/idle.py
    out.append(await _idle_finding(ctx, inp))

    # 2 — khách để lại thông tin nhiều lần
    dups = await _duplicates(ctx, inp)
    ad_leads = int(await ctx.val(f"SELECT count(*) FROM crm.leads l WHERE l.source = 'ads' AND {ARRIVED} BETWEEN %(f)s AND %(t)s", {"f": inp.date_from, "t": inp.date_to}))
    if ad_leads == 0:
        out.append(fd.unmeasured("crm.duplicate_leads", "Khách để lại thông tin nhiều lần", "money", why_not="trong kỳ chưa có lead từ quảng cáo",
                                 unlock="có lead quảng cáo vào hệ thống"))
    else:
        extra = sum(int(d["extra"]) for d in dups)
        money = None
        if cpl:
            money = sum(cpl[str(c)]["spend"] / cpl[str(c)]["leads"] for d in dups for c in (d["extra_campaigns"] or []) if str(c) in cpl and cpl[str(c)]["leads"])
        impact = fd.impact_money(money, "tiền quảng cáo trả cho lead trùng") if money is not None else \
            fd.impact_count(extra, "lead quảng cáo trùng", _drill("duplicate_leads", inp, "extra"))
        out.append(fd.finding(
            "crm.duplicate_leads", "Cùng một khách, quảng cáo trả tiền nhiều lần", "money",
            summary=(f"{fd.n_text(len(dups))} khách để lại thông tin từ quảng cáo nhiều lần trong {DUP_DAYS} ngày, tạo thêm {fd.n_text(extra)} lead trùng"
                     + (f" — ước tính {fd.money_text(money)} tiền quảng cáo trả cho lần thứ hai trở đi." if money is not None else ".")),
            formula=(f"Lead trùng = lead quảng cáo thứ hai trở đi của cùng một khách (cùng số điện thoại) trong {DUP_DAYS} ngày kể từ lần đầu trong kỳ. "
                     "Tiền ước tính = Σ lead trùng × chi phí trung bình mỗi lead của chiến dịch mà lead trùng đó đến từ."),
            impact=impact, metrics=[fd.metric("Khách có lead trùng", len(dups)), *([fd.metric("Lead trùng", extra, drill=_drill("duplicate_leads", inp, "extra"))] if money is not None else []), fd.metric("Lead quảng cáo trong kỳ", ad_leads),
                                    fd.metric("Tỷ lệ lead trùng", round(100 * extra / ad_leads, 1), "percent")],
            confidence="medium",
            caveats=["Một khách quan tâm hai sản phẩm khác nhau có thể là nhu cầu thật chứ không phải trùng — danh sách bên dưới để người xem phân biệt.",
                     "Chưa tính khách gửi lại đúng biểu mẫu cho cùng một sản phẩm (hệ thống gộp vào lead cũ nên không để lại dòng riêng)."],
            drill=_drill("duplicate_leads", inp)))

    # 3 / 4 — tốc độ gọi, chuyển đổi: chỉ kết luận khi người bán đã ghi việc trong hệ thống
    p = {"f": inp.date_from, "t": inp.date_to}
    owned = int(await ctx.val(f"SELECT count(*) FROM crm.leads l WHERE l.owner_id IS NOT NULL AND {ARRIVED} BETWEEN %(f)s AND %(t)s", p))
    touched = int(await ctx.val(f"""SELECT count(*) FROM crm.leads l WHERE l.owner_id IS NOT NULL AND {ARRIVED} BETWEEN %(f)s AND %(t)s
                                    AND EXISTS (SELECT 1 FROM crm.activities a WHERE a.lead_id = l.id)""", p))
    calls_total = int(await ctx.val("SELECT count(*) FROM crm.activities WHERE kind = 'call'"))
    covered = owned > 0 and touched / owned >= ACTIVITY_COVERAGE_MIN
    if not covered:
        why = ("người bán chưa ghi cuộc gọi hay chăm sóc nào trong hệ thống này (việc gọi khách vẫn làm ở hệ đang dùng)" if calls_total == 0 and touched == 0
               else f"mới {fd.pct_text(touched, owned) or '0%'} lead đã có người nhận được ghi chăm sóc — chưa đủ để kết luận")
        out.append(fd.unmeasured("crm.speed_to_lead", "Khách được gọi lại nhanh hay chậm", "process", why_not=why,
                                 unlock="người bán ghi cuộc gọi trong hệ thống (hoặc nối tổng đài vào), tối thiểu 20% lead đã giao có ghi chăm sóc"))
        out.append(fd.unmeasured("crm.conversion", "Bao nhiêu lead thành giao dịch", "process",
                                 why_not="chưa có lead nào đi qua các bước bán hàng trong hệ thống này — giao dịch vẫn ghi ở hệ đang dùng",
                                 unlock="người bán cập nhật bước bán hàng của lead (hoặc giao dịch được ghi vào hệ thống) trong kỳ"))
    else:
        r = await ctx.one(
            f"""WITH f AS (SELECT l.id, {ARRIVED}, coalesce(l.captured_at, l.created_at) AS at0,
                                 (SELECT min(a.at) FROM crm.activities a WHERE a.lead_id = l.id AND a.kind = 'call') AS first_call
                          FROM crm.leads l WHERE l.owner_id IS NOT NULL AND {ARRIVED} BETWEEN %(f)s AND %(t)s)
                SELECT count(*) AS n, count(first_call) AS called,
                       count(*) FILTER (WHERE first_call IS NOT NULL AND first_call - at0 <= interval '1 hour') AS within_hour,
                       coalesce(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM first_call - at0) / 60) FILTER (WHERE first_call IS NOT NULL AND first_call >= at0), 0) AS median_min
                FROM f""", p)
        assert r is not None
        never = int(r["n"]) - int(r["called"])
        out.append(fd.finding(
            "crm.speed_to_lead", "Khách được gọi lại nhanh hay chậm", "process",
            summary=f"Trong {fd.n_text(r['n'])} lead đã giao, {fd.n_text(r['within_hour'])} lead được gọi trong 1 giờ; {fd.n_text(never)} lead chưa có cuộc gọi nào.",
            formula="Tốc độ = thời gian từ lúc lead về đến cuộc gọi đầu tiên được ghi; tính trên lead đã có người nhận trong kỳ. Số lead chưa gọi = lead đã giao mà chưa có cuộc gọi nào được ghi.",
            impact=fd.impact_count(never, "lead đã giao nhưng chưa có cuộc gọi nào", _drill("uncalled_groups", inp, "n")),
            metrics=[fd.metric("Lead đã giao trong kỳ", int(r["n"])), fd.metric("Được gọi trong 1 giờ", int(r["within_hour"])),
                     fd.metric("Thời gian gọi lần đầu (trung vị, phút)", round(float(r["median_min"]))),
                     fd.metric("Tỷ lệ lead đã giao có ghi chăm sóc", round(100 * touched / owned, 1), "percent")],
            confidence="medium", caveats=["Chỉ tính cuộc gọi đã được ghi trong hệ thống này; cuộc gọi ở nơi khác không có mặt ở đây."],
            drill=_drill("uncalled_leads", inp)))
        d = await ctx.one(f"""SELECT count(*) AS n, count(*) FILTER (WHERE l.stage IN ('prospect', 'deal')) AS qualified, count(*) FILTER (WHERE l.stage = 'deal') AS deals
                              FROM crm.leads l WHERE {ARRIVED} BETWEEN %(f)s AND %(t)s""", p)
        assert d is not None
        out.append(fd.finding(
            "crm.conversion", "Bao nhiêu lead thành giao dịch", "process",
            summary=f"Trong {fd.n_text(d['n'])} lead của kỳ, {fd.n_text(d['qualified'])} lead là khách quan tâm trở lên và {fd.n_text(d['deals'])} lead đã thành giao dịch (theo trạng thái hiện tại).",
            formula="Tỷ lệ = số lead đã thành giao dịch ÷ số lead về trong kỳ, theo trạng thái hiện tại — nhóm lead còn đang chăm sẽ còn tăng.",
            metrics=[fd.metric("Lead trong kỳ", int(d["n"])), fd.metric("Khách quan tâm trở lên", int(d["qualified"])), fd.metric("Thành giao dịch", int(d["deals"])),
                     fd.metric("Tỷ lệ thành giao dịch", round(100 * d["deals"] / d["n"], 1) if d["n"] else 0, "percent")],
            confidence="low", caveats=["Chỉ đúng khi người bán cập nhật bước bán hàng trong hệ thống này."]))
    return {"findings": out, "context": ctxk, "notes": []}


async def _idle_finding(ctx: Ctx, inp: WindowIn) -> dict[str, Any]:
    from barista.modules.crm import idle  # nạp muộn: giữ thứ tự thao tác trong sổ đăng ký

    if not await ctx.val("SELECT 1 FROM crm.stages WHERE idle_days IS NOT NULL AND active AND phase IN ('lead', 'prospect') LIMIT 1"):
        return fd.unmeasured("crm.idle_leads", "Lead nằm im quá lâu ở một chặng", "process",
                             why_not="công ty chưa đặt ngưỡng nằm im cho chặng nào của lead",
                             unlock="quản trị đặt số ngày “Nằm im tối đa” cho các chặng (Quản trị › Chặng của lead)")
    rows = await idle.idle_rows(ctx, over_only=False, arrived=(inp.date_from, inp.date_to))
    over = [r for r in rows if r["over"]]
    by_stage: dict[str, int] = {}
    for r in over:
        by_stage[r["stage_label"]] = by_stage.get(r["stage_label"], 0) + 1
    worst = max(by_stage.items(), key=lambda kv: kv[1]) if by_stage else None
    groups = _drill("idle_groups", inp, "n")
    return fd.finding(
        "crm.idle_leads", "Lead nằm im quá lâu ở một chặng", "process",
        summary=(f"{fd.n_text(len(over))} trong {fd.n_text(len(rows))} lead đang theo ở chặng có ngưỡng ({fd.pct_text(len(over), len(rows)) or '0%'}) đã nằm im quá ngưỡng"
                 + (f"; nhiều nhất ở chặng “{worst[0]}” ({fd.n_text(worst[1])} lead)." if worst else ".")),
        formula=("Nằm im = số ngày từ lần gần nhất lead có chuyển động (lead về, vào chặng hiện tại, hoặc được chăm sóc — ghi chú nội bộ không tính) tới hôm nay; "
                 "quá ngưỡng khi lớn hơn số ngày công ty đặt cho chặng. Chỉ tính lead đang theo, về trong kỳ, ở chặng có đặt ngưỡng."),
        impact=fd.impact_count(len(over), "lead nằm im quá ngưỡng", groups),
        metrics=[fd.metric("Lead nằm im quá ngưỡng", len(over), drill=groups), fd.metric("Lead đang theo ở chặng có ngưỡng", len(rows)),
                 *([fd.metric("Nằm im lâu nhất (ngày)", max(int(r["days"]) for r in over))] if over else [])],
        confidence="medium",
        caveats=["Chỉ thấy chuyển động được ghi trong hệ thống này — lead đang được chăm ở hệ dùng song song vẫn hiện là nằm im.",
                 "Chặng chưa đặt ngưỡng không được tính."],
        drill=_drill("idle_leads", inp))


@m.query("finding_rows", resource="crm", title="Dòng gốc của phát hiện (khách / lead)", min_scope="all", agent="deny")
async def finding_rows(ctx: Ctx, inp: RowsIn) -> dict[str, Any]:
    """Các dòng tạo nên con số của một phát hiện (lead chưa có người nhận, khách trùng, lead chưa gọi) — bằng quyền người xem."""
    p = {"f": inp.date_from, "t": inp.date_to}
    if inp.key == "unowned_groups":
        rows = await ctx.all(
            f"""SELECT coalesce(l.source, '') AS source, l.offering_code,
                       CASE WHEN age < 1 THEN 'Dưới 1 ngày' WHEN age < 3 THEN '1 – 3 ngày' WHEN age < 7 THEN '3 – 7 ngày' WHEN age < 30 THEN '7 – 30 ngày' ELSE 'Trên 30 ngày' END AS age_band,
                       count(*) AS n
                FROM (SELECT l.*, extract(epoch FROM now() - coalesce(l.captured_at, l.created_at)) / 86400 AS age FROM crm.leads l) l
                WHERE l.owner_id IS NULL AND l.stage IN ('lead', 'prospect') AND {ARRIVED} BETWEEN %(f)s AND %(t)s
                GROUP BY 1, 2, 3 ORDER BY min(age) DESC, n DESC""", p)
        return ui.table([{**r, "id": f"{r['source']}|{r['offering_code']}|{r['age_band']}"} for r in rows], [
            ui.col("source", "Nguồn", "badge", labels=dict(SOURCES)), ui.col("offering_code", "Sản phẩm quan tâm"), ui.col("age_band", "Đã nằm"),
            ui.col("n", "Số lead", "number")], title="Lead chưa có người nhận (gộp theo nguồn, sản phẩm, tuổi)", empty="Không có lead nào chưa có người nhận")
    if inp.key == "uncalled_groups":
        rows = await ctx.all(
            f"""SELECT l.offering_code, count(*) AS n FROM crm.leads l WHERE l.owner_id IS NOT NULL AND {ARRIVED} BETWEEN %(f)s AND %(t)s
                  AND NOT EXISTS (SELECT 1 FROM crm.activities a WHERE a.lead_id = l.id AND a.kind = 'call') GROUP BY 1 ORDER BY n DESC""", p)
        return ui.table([{**r, "id": r["offering_code"]} for r in rows], [ui.col("offering_code", "Sản phẩm quan tâm"), ui.col("n", "Lead đã giao, chưa có cuộc gọi", "number")],
                        title="Lead đã giao mà chưa có cuộc gọi (gộp theo sản phẩm)", empty="Mọi lead đã giao đều có cuộc gọi")
    if inp.key == "unowned_leads":
        rows = await ctx.all(
            f"""SELECT l.id, c.name, l.offering_code, l.source, {ARRIVED} AS arrived,
                       floor(extract(epoch FROM now() - coalesce(l.captured_at, l.created_at)) / 86400)::int AS age_days
                FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id
                WHERE l.owner_id IS NULL AND l.stage IN ('lead', 'prospect') AND {ARRIVED} BETWEEN %(f)s AND %(t)s ORDER BY age_days DESC, c.name LIMIT 500""", p)
        return ui.table([{**r, "id": str(r["id"])} for r in rows], [
            ui.col("name", "Khách", width=200), ui.col("offering_code", "Sản phẩm quan tâm"), ui.col("source", "Nguồn", "badge", labels=dict(SOURCES)),
            ui.col("arrived", "Ngày về", "date"), ui.col("age_days", "Đã nằm (ngày)", "number")],
            title="Lead chưa có người nhận", empty="Không có lead nào chưa có người nhận", limit=500)
    if inp.key in ("idle_groups", "idle_leads"):
        from barista.modules.crm import idle  # nạp muộn: giữ thứ tự thao tác trong sổ đăng ký

        got = [r for r in await idle.idle_rows(ctx, arrived=(inp.date_from, inp.date_to)) if r["over"]]
        if inp.key == "idle_groups":
            groups: dict[str, dict[str, Any]] = {}
            for r in got:
                g = groups.setdefault(r["stage_key"], {"id": r["stage_key"], "stage": r["stage_label"], "limit_days": r["limit_days"], "n": 0, "max_days": 0})
                g["n"] += 1
                g["max_days"] = max(g["max_days"], int(r["days"]))
            return ui.table(sorted(groups.values(), key=lambda g: -g["n"]), [
                ui.col("stage", "Chặng"), ui.col("limit_days", "Ngưỡng (ngày)", "number"), ui.col("n", "Lead nằm im quá ngưỡng", "number"),
                ui.col("max_days", "Lâu nhất (ngày)", "number")], title="Lead nằm im quá ngưỡng (gộp theo chặng)", empty="Không có lead nào nằm im quá ngưỡng")
        names = {r["id"]: r["name"] for r in await ctx.all("SELECT l.id, c.name FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id WHERE l.id = ANY(%s)",
                                                         ([r["lead_id"] for r in got[:500]],))} if got else {}
        return ui.table([{"id": str(r["lead_id"]), "name": names.get(r["lead_id"], ""), "stage": r["stage_label"], "days": r["days"], "limit_days": r["limit_days"]}
                         for r in got[:500]], [
            ui.col("name", "Khách", width=200), ui.col("stage", "Chặng"), ui.col("days", "Nằm im (ngày)", "number"), ui.col("limit_days", "Ngưỡng (ngày)", "number")],
            title="Lead nằm im quá ngưỡng", empty="Không có lead nào nằm im quá ngưỡng", limit=500)
    if inp.key == "duplicate_leads":
        rows = await _duplicates(ctx, inp)
        return ui.table([{"id": str(d["customer_id"]), "name": d["name"], "leads": d["leads"], "extra": d["extra"], "campaigns": d["campaigns"],
                          "first_day": d["first_day"], "last_day": d["last_day"]} for d in rows[:2000]], [
            ui.col("name", "Khách", width=200), ui.col("leads", "Số lead quảng cáo", "number"), ui.col("extra", "Lead trùng", "number"),
            ui.col("campaigns", "Số chiến dịch", "number"), ui.col("first_day", "Lần đầu", "date"), ui.col("last_day", "Lần cuối", "date")],
            title="Khách có lead quảng cáo trùng", empty="Không có khách nào trùng", limit=2000)
    if inp.key == "uncalled_leads":
        rows = await ctx.all(
            f"""SELECT l.id, c.name, l.offering_code, {ARRIVED} AS arrived FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id
                WHERE l.owner_id IS NOT NULL AND {ARRIVED} BETWEEN %(f)s AND %(t)s
                  AND NOT EXISTS (SELECT 1 FROM crm.activities a WHERE a.lead_id = l.id AND a.kind = 'call') ORDER BY arrived, c.name LIMIT 500""", p)
        return ui.table([{**r, "id": str(r["id"])} for r in rows], [ui.col("name", "Khách", width=200), ui.col("offering_code", "Sản phẩm quan tâm"),
                                                                   ui.col("arrived", "Ngày về", "date")],
                        title="Lead đã giao mà chưa có cuộc gọi", empty="Mọi lead đã giao đều có cuộc gọi", limit=500)
    return ui.table([], [ui.col("name", "Khách")], title="", empty="Không có dòng nào cho mục này")
