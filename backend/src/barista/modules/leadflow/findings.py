"""PHÁT HIỆN của chia lead cho Báo cáo chạy song song (0230): vì sao lead nằm hàng chờ + luật chia HIỆN TẠI sẽ giao bao nhiêu (mô phỏng — chỉ đọc luật, người nhận, tải;
không giao, không ghi, không báo ai), phần lead mỗi bên đồng chi trả nhận so với phần họ góp, độ đều giữa các người nhận. Hợp đồng: ``kernel/findings.py``."""

from collections import defaultdict
from datetime import date
from typing import Any
from uuid import UUID

from barista.api import Ctx, Input, today, ui
from barista.api import findings as fd
from barista.api.lead_labels import SOURCES
from barista.modules.leadflow import QUEUE_REASON, _routing_on, _rule_for, m
from barista.modules.leadflow.routing import _eligible, _lead_capable

SPREAD_MIN_LEADS = 10    # ít hơn ngần này lead đã giao trong kỳ thì chưa đủ để nói về độ đều
BORN = "(s.created_at AT TIME ZONE core.tz())::date"
NOW_LABEL = {"assign": "Sẽ có người nhận", "no_rule": "Chưa có luật chia", "paused": "Sản phẩm tạm ngừng chia", "coordinator": "Luật để đội điều phối chia tay",
             "no_eligible": "Không ai đủ điều kiện nhận", "cap": "Người nhận đã chạm trần"}


class WindowIn(Input):
    date_from: date
    date_to: date


class RowsIn(WindowIn):
    key: str


def _drill(key: str, inp: WindowIn, sum_col: str | None = None) -> dict[str, Any]:
    return ui.drill("leadflow.finding_rows", {"key": key, "date_from": inp.date_from, "date_to": inp.date_to}, sum=sum_col)


async def _simulate(ctx: Ctx, inp: WindowIn) -> list[dict[str, Any]]:
    """Mỗi nhóm lead đang nằm hàng chờ (theo [[offering]] × nguồn × kế hoạch): lý do THẬT lúc đó + luật chia hiện tại sẽ làm gì. KHÔNG GHI gì: chỉ đọc luật, người nhận, tải."""
    groups = await ctx.all(
        f"""SELECT s.offering_code, s.source, s.plan_code, s.queue_reason, count(*) AS n FROM leadflow.lead_state s
            WHERE s.status = 'queued' AND {BORN} BETWEEN %(f)s AND %(t)s GROUP BY 1, 2, 3, 4 ORDER BY n DESC""", {"f": inp.date_from, "t": inp.date_to})
    load: dict[str, int] = dict(await ctx.call_system("crm.workload"))
    used: dict[str, int] = defaultdict(int)   # người nhận đã "nhận" trong mô phỏng — trần dùng chung giữa các nhóm
    out = []
    for g in groups:
        rule = await _rule_for(ctx, g["offering_code"], g["source"], today(), g["plan_code"])
        outcome, assign = "assign", int(g["n"])
        if not await _routing_on(ctx, g["offering_code"]):
            outcome, assign = "paused", 0
        elif rule is None:
            outcome, assign = "no_rule", 0
        elif rule["mode"] == "coordinator":
            outcome, assign = "coordinator", 0
        else:
            people = await _eligible(ctx, rule, g["offering_code"])
            if not people:
                outcome, assign = "no_eligible", 0
            elif rule["cap_uncalled"]:
                room = sum(max(0, rule["cap_uncalled"] - load.get(str(u), 0) - used[str(u)]) for u in people)
                assign = min(int(g["n"]), room)
                if assign < int(g["n"]):
                    outcome = "cap" if assign == 0 else "assign"
                left = assign
                for u in people:   # trải đều cho người còn chỗ (chỉ để dùng chung trần giữa các nhóm)
                    take = min(left, max(0, rule["cap_uncalled"] - load.get(str(u), 0) - used[str(u)]))
                    used[str(u)] += take
                    left -= take
        out.append({**g, "n": int(g["n"]), "outcome": outcome, "assign": assign, "unassigned": int(g["n"]) - assign})
    return out


async def _team_members(ctx: Ctx, team_ids: list[UUID]) -> dict[str, set[str]]:
    """{team_id: {user_id…}} gồm cả đội con."""
    rows = await ctx.all(
        """WITH RECURSIVE tree AS (SELECT id AS root, id FROM core.teams WHERE id = ANY(%s)
                                   UNION ALL SELECT tree.root, t.id FROM core.teams t JOIN tree ON t.parent_id = tree.id)
           SELECT tree.root, tm.user_id FROM tree JOIN core.team_members tm ON tm.team_id = tree.id""", (team_ids,))
    out: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        out[str(r["root"])].add(str(r["user_id"]))
    return out


async def _fairness(ctx: Ctx, inp: WindowIn) -> tuple[list[dict[str, Any]], int, int] | None:
    """Mỗi kế hoạch có đồng chi trả: bên góp, phần góp, số lead thực nhận. None = không có gói marketing. Trả (dòng, số kế hoạch có thoả thuận, số lead đã giao)."""
    terms = await ctx.call_system_optional("mkt.cost_share_terms", date_from=inp.date_from, date_to=inp.date_to)
    if terms is None:
        return None
    by_plan: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in terms:
        by_plan[t["plan_code"]].append(t)
    team_ids = [t["team_id"] for t in terms if t["team_id"]]
    members = await _team_members(ctx, team_ids) if team_ids else {}
    leads = await ctx.all(
        f"""SELECT s.plan_code, s.holder_id, count(*) AS n FROM leadflow.lead_state s
            WHERE s.plan_code = ANY(%(p)s) AND s.holder_id IS NOT NULL AND {BORN} BETWEEN %(f)s AND %(t)s GROUP BY 1, 2""",
        {"p": list(by_plan), "f": inp.date_from, "t": inp.date_to})
    rows: list[dict[str, Any]] = []
    assigned_total = 0
    for plan, ts in by_plan.items():
        mine = [(str(r["holder_id"]), int(r["n"])) for r in leads if r["plan_code"] == plan]
        n_total = sum(n for _, n in mine)
        assigned_total += n_total
        if not n_total:
            continue
        got: dict[int, int] = defaultdict(int)
        other = 0
        for holder, n in mine:
            idx = next((i for i, t in enumerate(ts) if t["user_id"] and str(t["user_id"]) == holder), None)   # người đứng tên thắng đội
            if idx is None:
                idx = next((i for i, t in enumerate(ts) if t["team_id"] and holder in members.get(str(t["team_id"]), ())), None)
            if idx is None:
                other += n
            else:
                got[idx] += n
        paid = sum(t["share_bps"] for t in ts)
        for i, t in enumerate(ts):
            expected = n_total * t["share_bps"] / paid
            rows.append({"id": f"{plan}:{i}", "plan": plan, "party": t["name"], "paid_pct": round(100 * t["share_bps"] / paid, 1), "got": got[i],
                         "got_pct": round(100 * got[i] / n_total, 1), "expected": round(expected, 1), "shortfall": max(0, round(expected - got[i]))})
        if other:
            rows.append({"id": f"{plan}:other", "plan": plan, "party": "Người ngoài các bên góp", "paid_pct": 0.0, "got": other,
                         "got_pct": round(100 * other / n_total, 1), "expected": 0.0, "shortfall": 0})
    return rows, len(by_plan), assigned_total


async def _spread(ctx: Ctx, inp: WindowIn) -> list[dict[str, Any]]:
    got = {str(r["holder_id"]): int(r["n"]) for r in await ctx.all(
        f"SELECT s.holder_id, count(*) AS n FROM leadflow.lead_state s WHERE s.holder_id IS NOT NULL AND {BORN} BETWEEN %(f)s AND %(t)s GROUP BY 1",
        {"f": inp.date_from, "t": inp.date_to})}
    capable = {str(u) for u in await _lead_capable(ctx, "")}
    ids = list(set(got) | capable)
    names = {str(r["id"]): r["name"] for r in await ctx.all("SELECT id, coalesce(name, email) AS name FROM core.users WHERE id = ANY(%s)", (ids,))} if ids else {}
    return sorted(({"id": u, "name": names.get(u, ""), "n": got.get(u, 0)} for u in capable | set(got)), key=lambda r: (-r["n"], r["name"]))


@m.query("findings_report", resource="leadflow", title="Phát hiện của chia lead (Báo cáo chạy song song)", internal=True, agent="deny", callers=("findings",),
         why="findings._collect (Báo cáo chạy song song — findings.overview, min_scope all): vì sao lead nằm hàng chờ + luật chia hiện tại sẽ giao được bao nhiêu (chỉ đọc), "
             "độ công bằng khi chia (kể cả theo phần đồng chi trả), cho một kỳ, cả công ty. Trả {findings, context, notes} — số đếm + công thức; không tên khách, không SĐT.")
async def findings_report(ctx: Ctx, inp: WindowIn) -> dict[str, Any]:
    out: list[dict[str, Any]] = []
    sim = await _simulate(ctx, inp)
    queued = sum(g["n"] for g in sim)
    if not queued:
        out.append(fd.unmeasured("leadflow.routing_gap", "Luật chia lead hiện tại sẽ giao được bao nhiêu lead đang chờ", "leads",
                                 why_not="trong kỳ không có lead nào nằm hàng chờ chia", unlock="có lead nằm hàng chờ trong kỳ"))
    else:
        would, still = sum(g["assign"] for g in sim), sum(g["unassigned"] for g in sim)
        reasons: dict[str, int] = defaultdict(int)
        for g in sim:
            reasons[QUEUE_REASON.get(g["queue_reason"], g["queue_reason"] or "không rõ")] += g["n"]
        top = max(reasons.items(), key=lambda kv: kv[1])
        out.append(fd.finding(
            "leadflow.routing_gap", "Luật chia lead hiện tại sẽ giao được bao nhiêu lead đang chờ", "leads",
            summary=(f"{fd.n_text(queued)} lead đang nằm hàng chờ chia. Nếu bật chia thật theo luật đang có, {fd.n_text(would)} lead sẽ có người nhận ngay; "
                     f"{fd.n_text(still)} lead vẫn kẹt (lý do thường gặp lúc lead về: {top[0]})."),
            formula=("Mô phỏng, không giao lead thật: với mỗi nhóm lead (sản phẩm × nguồn × kế hoạch) tìm luật chia đang hiệu lực hôm nay, lọc người đủ điều kiện nhận "
                     "(đang hoạt động, đúng đội và vai, không tạm ngừng / nghỉ phép), rồi cho nhận tới trần lead chưa gọi của luật (trừ lead họ đang giữ). "
                     "Số lead sẽ có người nhận = tổng suất còn lại, tối đa bằng số lead của nhóm."),
            impact=fd.impact_count(would, "lead đang chờ mà luật hiện tại sẽ giao ngay", _drill("routing_gap", inp, "assign")),
            metrics=[fd.metric("Lead nằm hàng chờ", queued), fd.metric("Vẫn kẹt sau khi chia", still),
                     *(fd.metric(f"Lý do lúc về: {k}", v) for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))],
            confidence="medium", attention=still > 0,
            caveats=["Đây là điều SẼ xảy ra nếu bật chia thật, không phải lỗi của đội: ở chế độ chỉ ghi nhận, lead nằm đỗ và hệ thống không tự chia thêm.",
                     "Mô phỏng theo luật và danh sách người nhận hôm nay, không theo lúc lead về."],
            drill=_drill("routing_gap", inp, "assign")))

    fair = await _fairness(ctx, inp)
    if fair is None or not fair[1]:
        out.append(fd.unmeasured("leadflow.cost_share_fairness", "Bên góp tiền quảng cáo có nhận đủ phần lead tương ứng không", "fairness",
                                 why_not="chưa có thoả thuận đồng chi trả nào trong kỳ để so" if fair is not None else "công ty chưa dùng phần marketing nên không có thoả thuận đồng chi trả",
                                 unlock="khai thoả thuận đồng chi trả (phần góp của từng đội / người theo kế hoạch quảng cáo)"))
    elif not fair[0]:
        out.append(fd.unmeasured("leadflow.cost_share_fairness", "Bên góp tiền quảng cáo có nhận đủ phần lead tương ứng không", "fairness",
                                 why_not=f"có {fair[1]} kế hoạch đồng chi trả nhưng chưa lead nào của các kế hoạch đó được giao cho người (đang nằm hàng chờ)",
                                 unlock="lead của kế hoạch đồng chi trả được giao cho người nhận (bật chia thật hoặc giao tay)"))
    else:
        rows, plans, assigned = fair
        short = sum(r["shortfall"] for r in rows)
        worst = max(rows, key=lambda r: r["shortfall"])
        out.append(fd.finding(
            "leadflow.cost_share_fairness", "Bên góp tiền quảng cáo có nhận đủ phần lead tương ứng không", "fairness",
            summary=(f"{fd.n_text(plans)} kế hoạch đồng chi trả, {fd.n_text(assigned)} lead đã giao. Các bên góp đang nhận thiếu tổng cộng {fd.n_text(short)} lead so với phần góp"
                     + (f"; lệch nhiều nhất: {worst['party']} (kế hoạch {worst['plan']}) góp {fd.dec_text(worst['paid_pct'])}% nhưng nhận {fd.dec_text(worst['got_pct'])}% lead." if short else ".")),
            formula=("Phần lead lẽ ra = số lead đã giao của kế hoạch × phần góp của bên đó ÷ tổng phần góp của các bên (giả định: lead của kế hoạch đồng chi trả chia theo tỷ lệ góp). "
                     "Nhận thiếu = max(0, phần lẽ ra − số lead thực nhận). Người nhận thuộc đội góp tính cho đội đó; người đứng tên góp thì tính riêng."),
            impact=fd.impact_count(short, "lead bên góp nhận thiếu so với phần góp", _drill("cost_share_fairness", inp, "shortfall")),
            metrics=[fd.metric("Kế hoạch đồng chi trả", plans), fd.metric("Lead đã giao trong các kế hoạch đó", assigned)],
            confidence="low",
            caveats=["Giả định lead chia đúng theo tỷ lệ góp — nếu thoả thuận quy định khác (vd người góp được ưu tiên nhưng không độc quyền) thì cần chỉnh công thức.",
                     "Chỉ tính lead đã giao cho người; lead còn nằm hàng chờ chưa vào phép so."],
            drill=_drill("cost_share_fairness", inp, "shortfall")))

    people = await _spread(ctx, inp)
    n_assigned = sum(p["n"] for p in people)
    if n_assigned < SPREAD_MIN_LEADS:
        out.append(fd.unmeasured("leadflow.distribution_spread", "Lead có được chia đều giữa những người nhận được không", "fairness",
                                 why_not=f"trong kỳ mới có {n_assigned} lead được giao cho người, chưa đủ ({SPREAD_MIN_LEADS}) để nói về độ đều",
                                 unlock="có thêm lead được giao cho người nhận trong kỳ"))
    else:
        idle = [p for p in people if p["n"] == 0]
        got = [p["n"] for p in people]
        avg = n_assigned / len(people)
        out.append(fd.finding(
            "leadflow.distribution_spread", "Lead có được chia đều giữa những người nhận được không", "fairness",
            summary=(f"{fd.n_text(n_assigned)} lead đã giao cho {fd.n_text(len(people))} người đủ điều kiện nhận: người nhiều nhất {fd.n_text(max(got))} lead, "
                     f"ít nhất {fd.n_text(min(got))} lead; {fd.n_text(len(idle))} người chưa nhận lead nào."),
            formula="So số lead mỗi người đang được giao trong kỳ với mức trung bình (tổng lead đã giao ÷ số người đủ điều kiện nhận). Luật chia theo trọng số hay hạng thì chênh lệch có thể là đúng ý.",
            impact=fd.impact_count(len(idle), "người đủ điều kiện nhận lead nhưng chưa nhận lead nào", _drill("idle_receivers", inp)),
            metrics=[fd.metric("Người đủ điều kiện nhận lead", len(people)), fd.metric("Lead nhiều nhất một người", max(got)), fd.metric("Lead ít nhất một người", min(got)),
                     fd.metric("Trung bình mỗi người", round(avg, 1))],
            confidence="low", caveats=["Chưa tính trọng số / hạng của từng người nên chênh lệch không đồng nghĩa với chia sai."],
            drill=_drill("distribution_spread", inp)))
    return {"findings": out, "context": [], "notes": []}


@m.query("finding_rows", resource="leadflow", title="Dòng gốc của phát hiện (chia lead)", min_scope="all", agent="deny")
async def finding_rows(ctx: Ctx, inp: RowsIn) -> dict[str, Any]:
    if inp.key == "routing_gap":
        sim = await _simulate(ctx, inp)
        rows = [{"id": f"{i}", "offering": g["offering_code"] or "—", "source": g["source"], "plan": g["plan_code"] or "—",
                 "reason": QUEUE_REASON.get(g["queue_reason"], g["queue_reason"] or "không rõ"), "n": g["n"], "now": NOW_LABEL[g["outcome"]], "assign": g["assign"]}
                for i, g in enumerate(sim)]
        return ui.table(rows, [ui.col("offering", "Sản phẩm"), ui.col("source", "Nguồn", "badge", labels=dict(SOURCES)), ui.col("plan", "Kế hoạch"),
                               ui.col("n", "Lead đang chờ", "number"), ui.col("reason", "Vì sao chờ lúc lead về", width=240),
                               ui.col("now", "Luật hiện tại sẽ", width=200), ui.col("assign", "Sẽ giao ngay", "number")],
                        title="Lead đang chờ theo nhóm", empty="Không có lead nào đang chờ")
    if inp.key == "cost_share_fairness":
        fair = await _fairness(ctx, inp)
        return ui.table((fair or ([], 0, 0))[0], [ui.col("plan", "Kế hoạch"), ui.col("party", "Bên góp", width=200), ui.col("paid_pct", "Phần góp %", "number"),
                                                  ui.col("got", "Lead thực nhận", "number"), ui.col("got_pct", "Phần nhận %", "number"),
                                                  ui.col("expected", "Lẽ ra nhận", "number"), ui.col("shortfall", "Nhận thiếu", "number")],
                        title="Phần góp so với phần lead nhận", empty="Chưa có lead nào của kế hoạch đồng chi trả được giao")
    if inp.key == "idle_receivers":
        return ui.table([r for r in await _spread(ctx, inp) if r["n"] == 0], [ui.col("name", "Người nhận", width=220), ui.col("n", "Lead được giao trong kỳ", "number")],
                        title="Người đủ điều kiện nhận lead nhưng chưa nhận lead nào", empty="Ai đủ điều kiện cũng đã nhận lead")
    if inp.key == "distribution_spread":
        rows = [r for r in await _spread(ctx, inp)]
        return ui.table(rows, [ui.col("name", "Người nhận", width=220), ui.col("n", "Lead được giao trong kỳ", "number")],
                        title="Lead mỗi người nhận", empty="Chưa có người nhận lead")
    return ui.table([], [ui.col("name", "Tên")], title="", empty="Không có dòng nào cho mục này")
