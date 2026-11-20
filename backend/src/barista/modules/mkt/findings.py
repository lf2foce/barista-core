"""PHÁT HIỆN của marketing cho Báo cáo chạy song song (0230): tiền chạy quảng cáo mà không ra lead, chi phí / lead bất thường theo kênh và chiến dịch, xu hướng chi.
Chỉ ĐỌC. So chi phí với lead CHỈ trong khoảng lead đã vào hệ thống (kỳ bị cắt, ghi rõ) và chỉ khi đa số lead quảng cáo gắn được chiến dịch — không thì "chưa đo được",
không bao giờ hiện 0. Hợp đồng: ``kernel/findings.py``."""

from datetime import date, timedelta
from typing import Any

from barista.api import Ctx, Input, ui
from barista.api import findings as fd
from barista.modules.mkt import CHANNELS, _period_stats, _reliability, m

ATTRIBUTED_MIN = 0.5       # < 50% lead quảng cáo gắn được chiến dịch ⇒ "chiến dịch không ra lead" không đo được (lead không biết về chiến dịch nào)
OUTLIER_MIN_LEADS = 5      # chiến dịch phải có ngần này lead mới so chi phí / lead
OUTLIER_FACTOR = 2.0       # chi phí / lead > 2 lần mức trung vị ⇒ bất thường
OUTLIER_MIN_CAMPAIGNS = 3  # cần ngần này chiến dịch đủ lead để có "mức bình thường"
TREND_NOTABLE = 30         # tăng / giảm ≥ 30% so với kỳ trước ⇒ nói rõ


class WindowIn(Input):
    date_from: date
    date_to: date


class RowsIn(WindowIn):
    key: str


async def _window(ctx: Ctx, inp: WindowIn) -> tuple[date, date, dict[str, Any], list[str]]:
    """Kỳ dùng để SO chi phí với lead: cắt đầu kỳ theo ngày lead đầu tiên vào hệ thống, cắt cuối kỳ theo ngày có chi phí cuối cùng."""
    span = await ctx.call_system("crm.lead_span", date_from=inp.date_from, date_to=inp.date_to)
    last_spend = await ctx.val("SELECT max(day) FROM mkt.spend")
    f, t, notes = inp.date_from, inp.date_to, []
    if span["first_on"] and span["first_on"] > f:
        f = span["first_on"]
        notes.append(f"Lead chỉ vào hệ thống từ {fd.day_text(f)} — chi phí và lead chỉ được so từ ngày đó (chi phí trước đó chưa có lead để đối chiếu).")
    cov = await ctx.one("SELECT spend_reliable_from, lead_reliable_from FROM mkt.coverage")
    reliable = max((x for x in (cov or {}).values() if x), default=None)
    if reliable and reliable > f:
        f = reliable
        notes.append(f"Mốc dữ liệu chi phí / lead đáng tin của công ty là {fd.day_text(f)} — chi phí và lead chỉ được so từ ngày đó.")
    if last_spend and last_spend < t:
        t = last_spend
        notes.append(f"Chi phí mới có tới hết {fd.day_text(t)} — kỳ so được cắt tới ngày đó.")
    return f, t, span, notes


@m.query("campaign_cpl", resource="mkt", title="Chi phí và lead theo chiến dịch (cho khách / lead)", internal=True, agent="deny", callers=("crm",),
         why="crm.findings_report (Báo cáo chạy song song, min_scope all): quy lead chưa có người nhận / lead trùng ra tiền bằng chi phí trung bình mỗi lead của CHIẾN DỊCH. "
             "Trả CHỈ SỐ GỘP {campaign_id: {spend, leads}} cho chiến dịch có lead trong kỳ; None khi kỳ chưa đáng tin (mốc dữ liệu công ty khai) — không tên, không SĐT.")
async def campaign_cpl(ctx: Ctx, inp: WindowIn) -> dict[str, dict[str, int]] | None:
    f, t, span, _ = await _window(ctx, inp)
    if f > t or span["first_on"] is None:
        return None
    unsafe, _ = await _reliability(ctx, f, t)
    if unsafe:
        return None
    rows, _ = await _period_stats(ctx, f, t)
    return {str(r["id"]): {"spend": int(r["spend"]), "leads": int(r["leads"])} for r in rows if r["leads"] and r["spend"]}


@m.query("cost_share_terms", resource="mkt", title="Phần góp đồng chi trả theo kế hoạch (cho chia lead)", internal=True, agent="deny", callers=("leadflow",),
         why="leadflow.findings_report (Báo cáo chạy song song, min_scope all): so lead mỗi bên nhận với phần chi phí quảng cáo bên đó góp. Trả CHỈ danh sách "
             "[{plan_code, team_id, user_id, name, share_bps}] của thoả thuận có hiệu lực trong kỳ — mã đội / người là mã nội bộ đã có trong công ty, không SĐT, không tiền.")
async def cost_share_terms(ctx: Ctx, inp: WindowIn) -> list[dict[str, Any]]:
    return await ctx.all(
        """SELECT s.plan_code, s.team_id, s.user_id, coalesce(t.name, u.name, '') AS name, s.share_bps
           FROM mkt.cost_shares s LEFT JOIN core.teams t ON t.id = s.team_id LEFT JOIN core.users u ON u.id = s.user_id
           WHERE s.plan_code <> '' AND s.valid_from <= %(t)s AND coalesce(s.valid_to, %(t)s) >= %(f)s ORDER BY s.plan_code, s.share_bps DESC""",
        {"f": inp.date_from, "t": inp.date_to})


def _drill(key: str, f: date, t: date, sum_col: str | None = None) -> dict[str, Any]:
    return ui.drill("mkt.finding_rows", {"key": key, "date_from": f, "date_to": t}, sum=sum_col)


async def _campaign_rows(ctx: Ctx, f: date, t: date) -> list[dict[str, Any]]:
    rows, _ = await _period_stats(ctx, f, t)
    return rows


def _outliers(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float | None]:
    """Chiến dịch đủ lead có chi phí / lead > ``OUTLIER_FACTOR`` × trung vị của CẢ kỳ (trung vị theo kênh khi kênh đó có ≥ 3 chiến dịch đủ lead). Hàm thuần."""
    ok = [{**r, "cpl": r["spend"] / r["leads"]} for r in rows if r["leads"] >= OUTLIER_MIN_LEADS and r["spend"] > 0]
    if len(ok) < OUTLIER_MIN_CAMPAIGNS:
        return ok, None
    overall = fd.median([r["cpl"] for r in ok])
    by_channel: dict[str, list[float]] = {}
    for r in ok:
        by_channel.setdefault(r["channel"], []).append(r["cpl"])
    for r in ok:
        peers = by_channel[r["channel"]]
        r["ref_cpl"] = fd.median(peers) if len(peers) >= OUTLIER_MIN_CAMPAIGNS else overall
        r["outlier"] = r["cpl"] > OUTLIER_FACTOR * r["ref_cpl"]
        r["excess"] = round((r["cpl"] - r["ref_cpl"]) * r["leads"]) if r["outlier"] else 0
    return ok, overall


@m.query("findings_report", resource="mkt", title="Phát hiện của marketing (Báo cáo chạy song song)", internal=True, agent="deny", callers=("findings",),
         why="findings._collect (Báo cáo chạy song song — findings.overview, min_scope all): tiền quảng cáo không ra lead, chi phí / lead bất thường, xu hướng chi cho một kỳ, cả công ty. "
             "Trả {findings, context, notes} — số gộp + công thức; không tên khách, không SĐT.")
async def findings_report(ctx: Ctx, inp: WindowIn) -> dict[str, Any]:
    f, t, span, notes = await _window(ctx, inp)
    out: list[dict[str, Any]] = []
    unsafe, rel_notes = await _reliability(ctx, f, t)
    notes += rel_notes
    context: list[dict[str, Any]] = []
    attributed = span["ads_attributed"] / span["ads"] if span["ads"] else None
    can_compare = span["first_on"] is not None and f <= t and attributed is not None and attributed >= ATTRIBUTED_MIN and not unsafe
    if can_compare:
        rows = await _campaign_rows(ctx, f, t)
        tot_spend, tot_leads = sum(r["spend"] for r in rows), sum(r["leads"] for r in rows)
        context += [fd.metric("Chi phí quảng cáo trong kỳ so được", tot_spend, "money"), fd.metric("Lead gắn được chiến dịch", tot_leads)]
        # 1 — chi mà không ra lead
        dead = sorted((r for r in rows if r["spend"] > 0 and r["leads"] == 0), key=lambda r: -r["spend"])
        wasted = sum(r["spend"] for r in dead)
        out.append(fd.finding(
            "mkt.spend_no_leads", "Tiền chạy quảng cáo mà không thấy lead nào", "money",
            summary=(f"{fd.n_text(len(dead))} chiến dịch đã chi {fd.money_text(wasted)} ({fd.pct_text(wasted, tot_spend) or '0%'} tổng chi phí so được) mà không có lead nào vào hệ thống "
                     f"từ {fd.day_text(f)} đến {fd.day_text(t)}." if dead else
                     f"Mọi chiến dịch có chi phí từ {fd.day_text(f)} đến {fd.day_text(t)} đều có lead vào hệ thống."),
            formula="Tiền ước tính = tổng chi phí trong kỳ của các chiến dịch có chi phí > 0 và 0 lead vào hệ thống (lead tính theo ngày khách để lại thông tin, gắn đúng chiến dịch).",
            impact=fd.impact_money(wasted, "tiền chi cho chiến dịch không ra lead", _drill("spend_no_leads", f, t, "spend")),
            metrics=[fd.metric("Chiến dịch chi mà không có lead", len(dead)), fd.metric("Tổng chi phí của các chiến dịch đó", wasted, "money"),
                     fd.metric("Tổng chi phí so được", tot_spend, "money"), fd.metric("Chiến dịch có chi phí", sum(1 for r in rows if r["spend"] > 0))],
            confidence="medium",
            caveats=["Chiến dịch mới chạy vài ngày có thể chưa kịp có lead; khách nhắn tin trực tiếp mà chưa được nhập thành lead cũng không được đếm.",
                     "Chỉ so trong khoảng đã có lead vào hệ thống."],
            drill=_drill("spend_no_leads", f, t, "spend")))
        # 2 — chi phí / lead bất thường
        ok, median = _outliers(rows)
        if median is None:
            out.append(fd.unmeasured("mkt.cpl_outliers", "Chiến dịch có chi phí mỗi lead cao bất thường", "money",
                                     why_not=f"chưa đủ chiến dịch có từ {OUTLIER_MIN_LEADS} lead trở lên để biết mức bình thường (hiện có {len(ok)}, cần ít nhất {OUTLIER_MIN_CAMPAIGNS})",
                                     unlock="có thêm lead gắn đúng chiến dịch, hoặc kỳ dài hơn"))
        else:
            bad = sorted((r for r in ok if r["outlier"]), key=lambda r: -r["excess"])
            excess = sum(r["excess"] for r in bad)
            out.append(fd.finding(
                "mkt.cpl_outliers", "Chiến dịch có chi phí mỗi lead cao bất thường", "money",
                summary=(f"{fd.n_text(len(bad))} chiến dịch có chi phí mỗi lead cao hơn {fd.dec_text(OUTLIER_FACTOR)} lần mức thông thường của kênh; "
                         f"phần chi vượt ước tính {fd.money_text(excess)}." if bad else
                         f"Không chiến dịch nào có chi phí mỗi lead vượt {fd.dec_text(OUTLIER_FACTOR)} lần mức thông thường (mức trung vị {fd.money_text(median)} mỗi lead)."),
                formula=(f"Mức thông thường = trung vị chi phí mỗi lead của các chiến dịch cùng kênh (từ {OUTLIER_MIN_CAMPAIGNS} chiến dịch đủ lead; không thì trung vị cả kỳ). "
                         f"Bất thường = chi phí mỗi lead > {fd.dec_text(OUTLIER_FACTOR)} × mức thông thường. Phần chi vượt = (chi phí mỗi lead − mức thông thường) × số lead của chiến dịch."),
                impact=fd.impact_money(excess, "phần chi vượt so với mức thông thường của kênh", _drill("cpl_outliers", f, t, "excess")),
                metrics=[fd.metric("Chiến dịch bất thường", len(bad)), fd.metric("Chiến dịch đủ lead để so", len(ok)),
                         fd.metric("Chi phí mỗi lead trung vị", round(median), "money"),
                         fd.metric("Chi phí mỗi lead cao nhất", round(max(r["cpl"] for r in ok)), "money")],
                confidence="medium", caveats=[f"Chỉ so chiến dịch có từ {OUTLIER_MIN_LEADS} lead; chi phí mỗi lead chưa tính chất lượng lead."],
                drill=_drill("cpl_outliers", f, t, "excess")))
    else:
        reason = ("lead chưa vào hệ thống trong kỳ" if span["first_on"] is None else
                  "kỳ chọn không còn ngày nào có cả chi phí lẫn lead" if f > t else
                  "phần lớn lead quảng cáo chưa gắn được chiến dịch nên không biết chiến dịch nào ra lead" if attributed is not None and attributed < ATTRIBUTED_MIN else
                  "kỳ này chưa nằm trong khoảng dữ liệu chi phí / lead đáng tin (mốc công ty đã khai)" if unsafe else "kỳ này chưa có lead quảng cáo")
        if attributed is not None and attributed < ATTRIBUTED_MIN:
            reason += f" (chỉ {fd.pct_text(span['ads_attributed'], span['ads'])} lead quảng cáo gắn được chiến dịch)"
        unlock = "lead quảng cáo vào hệ thống kèm mã chiến dịch (cấu hình nguồn lead gửi mã chiến dịch), và mốc dữ liệu đáng tin được khai đúng"
        out.append(fd.unmeasured("mkt.spend_no_leads", "Tiền chạy quảng cáo mà không thấy lead nào", "money", why_not=reason, unlock=unlock))
        out.append(fd.unmeasured("mkt.cpl_outliers", "Chiến dịch có chi phí mỗi lead cao bất thường", "money", why_not=reason, unlock=unlock))

    # 3 — xu hướng chi (không cần lead): kỳ gốc so kỳ liền trước cùng độ dài
    t2 = min(inp.date_to, await ctx.val("SELECT max(day) FROM mkt.spend") or inp.date_to)
    days = (t2 - inp.date_from).days + 1
    if days < 1:
        out.append(fd.unmeasured("mkt.spend_trend", "Chi phí quảng cáo tăng hay giảm", "money", why_not="kỳ chọn chưa có chi phí nào được nhập", unlock="chi phí quảng cáo được nhập / đồng bộ"))
    else:
        p_to = inp.date_from - timedelta(days=1)
        p_from = p_to - timedelta(days=days - 1)
        cur = int(await ctx.val("SELECT coalesce(sum(amount_vnd), 0) FROM mkt.spend WHERE day BETWEEN %s AND %s", (inp.date_from, t2)))
        prev = int(await ctx.val("SELECT coalesce(sum(amount_vnd), 0) FROM mkt.spend WHERE day BETWEEN %s AND %s", (p_from, p_to)))
        prev_days = int(await ctx.val("SELECT count(DISTINCT day) FROM mkt.spend WHERE day BETWEEN %s AND %s", (p_from, p_to)))
        if prev_days == 0:
            out.append(fd.unmeasured("mkt.spend_trend", "Chi phí quảng cáo tăng hay giảm", "money", why_not="kỳ liền trước chưa có chi phí nào để so",
                                     unlock="chi phí của kỳ liền trước được nhập / đồng bộ"))
        else:
            ch = round(100 * (cur - prev) / prev, 1) if prev else None
            notable = ch is not None and abs(ch) >= TREND_NOTABLE
            lead_note = ("Chưa so được chi phí mỗi lead với kỳ trước vì lead chỉ vào hệ thống từ " + fd.day_text(span["first_on"]) + "." if span["first_on"] and span["first_on"] > p_to else "")
            out.append(fd.finding(
                "mkt.spend_trend", "Chi phí quảng cáo tăng hay giảm", "money",
                summary=(f"Chi {fd.money_text(cur)} trong {fd.n_text(days)} ngày, " + ("so với kỳ liền trước không có chi phí." if not prev else
                         (f"{'tăng' if cur >= prev else 'giảm'} {fd.dec_text(abs(ch or 0))}% so với {fd.money_text(prev)} của kỳ liền trước" if abs(ch or 0) >= 0.5 else
                         f"gần như không đổi so với {fd.money_text(prev)} của kỳ liền trước") + (" — thay đổi đáng chú ý." if notable else "."))),
                formula="So sánh tổng chi phí quảng cáo của kỳ với kỳ liền trước cùng số ngày. Chỉ là xu hướng chi, không phải lãng phí.",
                metrics=[fd.metric("Chi phí kỳ này", cur, "money"), fd.metric("Chi phí kỳ liền trước", prev, "money"),
                         *([fd.metric("Thay đổi", ch, "percent")] if ch is not None else []), fd.metric("Số ngày", days),
                         fd.metric("Chi phí trung bình mỗi ngày", round(cur / days), "money")],
                confidence="high", attention=notable, caveats=[c for c in (lead_note,) if c], drill=_drill("spend_trend", inp.date_from, t2)))
    return {"findings": out, "context": context, "notes": notes}


@m.query("finding_rows", resource="mkt", title="Dòng gốc của phát hiện (quảng cáo)", min_scope="all", agent="deny")
async def finding_rows(ctx: Ctx, inp: RowsIn) -> dict[str, Any]:
    """Các dòng tạo nên con số của một phát hiện marketing — theo chiến dịch (hoặc theo ngày với xu hướng chi)."""
    f, t = inp.date_from, inp.date_to
    if inp.key == "spend_trend":
        rows = await ctx.all("SELECT day::text AS id, day, sum(amount_vnd)::bigint AS spend, count(*) AS campaigns FROM mkt.spend WHERE day BETWEEN %s AND %s GROUP BY day ORDER BY day DESC",
                             (f, t))
        return ui.table(rows, [ui.col("day", "Ngày", "date"), ui.col("spend", "Chi phí", "money"), ui.col("campaigns", "Số chiến dịch có chi", "number")],
                        title="Chi phí theo ngày", empty="Chưa có chi phí")
    rows = await _campaign_rows(ctx, f, t)
    if inp.key == "spend_no_leads":
        sel = [r for r in rows if r["spend"] > 0 and r["leads"] == 0]
        sel.sort(key=lambda r: -r["spend"])
        return ui.table([{"id": str(r["id"]), "code": r["code"], "name": r["name"], "channel": r["channel"], "spend": r["spend"], "days": r["spend_days"]} for r in sel], [
            ui.col("code", "Mã"), ui.col("name", "Chiến dịch", width=220), ui.col("channel", "Kênh", "badge", labels=CHANNELS),
            ui.col("spend", "Đã chi", "money"), ui.col("days", "Số ngày có chi", "number")], title="Chiến dịch chi mà không có lead", empty="Không có chiến dịch nào như vậy")
    if inp.key == "cpl_outliers":
        ok, _ = _outliers(rows)
        ok.sort(key=lambda r: (-(r.get("excess") or 0), -r["cpl"]))
        return ui.table([{"id": str(r["id"]), "code": r["code"], "name": r["name"], "channel": r["channel"], "spend": r["spend"], "leads": r["leads"], "cpl": round(r["cpl"]),
                          "ref_cpl": round(r.get("ref_cpl") or 0), "excess": r.get("excess", 0), "verdict": "Bất thường" if r.get("outlier") else "Bình thường"} for r in ok], [
            ui.col("code", "Mã"), ui.col("name", "Chiến dịch", width=200), ui.col("channel", "Kênh", "badge", labels=CHANNELS), ui.col("spend", "Đã chi", "money"),
            ui.col("leads", "Lead", "number"), ui.col("cpl", "Chi phí mỗi lead", "money"), ui.col("ref_cpl", "Mức thông thường của kênh", "money"),
            ui.col("excess", "Chi vượt", "money"), ui.col("verdict", "Đánh giá", "badge", tones={"Bất thường": "red", "Bình thường": "green"})],
            title="Chi phí mỗi lead theo chiến dịch", empty="Chưa chiến dịch nào đủ lead để so")
    return ui.table([], [ui.col("code", "Mã")], title="", empty="Không có dòng nào cho mục này")
