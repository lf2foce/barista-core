"""KPI & XẾP HẠNG (quyết định 0101).

* Chỉ tiêu là DỮ LIỆU (``kpi.targets``): kỳ = tháng × chỉ số × người HOẶC đội. Nhập tay / Excel (``set_target`` nhập được) / trợ lý đề xuất.
* Thực tế KHÔNG lưu: tính lúc xem từ CHỈ SỐ module khai (``Module.metric`` — cùng định nghĩa với mục tiêu kế hoạch, 0074 B), nhóm theo người / đội.
  Chỉ số trả số cả công ty (quyền hệ thống, họ ``metric``) ⇒ module này CHỈ hiện dòng người xem được thấy: chỉ tiêu đọc qua RLS (của mình / đội
  mình quản lý / cả công ty); xếp hạng hiện tên người khác chỉ khi chính sách công ty cho phép (``kpi.policy``).
* Chốt kỳ (``close_period``) = ảnh chụp chỉ tiêu + thực tế (chỉ thêm) — số trả thưởng không đổi khi dữ liệu cũ được sửa sau.
* Nhịp: tháng đang chạy so với đường thẳng theo ngày; chậm hơn 80% đường ⇒ "Chậm" (cùng ngưỡng với mục tiêu kế hoạch).
"""

import calendar
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from barista.api import Action as A
from barista.api import (
    Ctx,
    Denied,
    Desk,
    Input,
    Invalid,
    Line,
    Module,
    Money,
    NotFound,
    Pack,
    Screen,
    Workspace,
    field,
    line,
    today,
    ui,
)
from barista.kernel import packs, registry

# Gói 'kpi' tự khai (0124): năng lực dùng chung — chỉ tiêu theo tháng cho người / đội, thực tế tính từ CHỈ SỐ các gói khác khai (``Module.metric``). Không bắt buộc
# gói nào: công ty có bao nhiêu chỉ số thì chọn bấy nhiêu. Quyền mặc định của các vai làm việc với khách khai ở đây (vai thuộc gói Khách hàng chỉ được cài khi gói đó bật).
Pack("kpi", "KPI & xếp hạng",
     "Chỉ tiêu tháng cho từng người / đội, thực tế tính từ số liệu của các gói đang bật, nhịp so với đường cần đạt, bảng xếp hạng, chốt kỳ — dùng được cho mọi ngành",
     kind="capability",
     grants=(("admin", "kpi", "all", "read write manage"), ("director", "kpi", "all", "read write manage"),
             ("team_lead", "kpi", "team", "read write"), ("sale", "kpi", "own", "read"), ("sales_admin", "kpi", "all", "read write")),
     tools={"sale": ("kpi.mine",), "team_lead": ("kpi.board", "kpi.ranking"), "director": ("kpi.ranking",)})   # bộ công cụ trợ lý (0218)

m = Module("kpi", "KPI & xếp hạng", pack="kpi", resources={"kpi": "KPI & chỉ tiêu"})
m.consumes("metric")

BEHIND = 0.8   # đạt < 80% đường cần tới hôm nay ⇒ chậm (như mục tiêu kế hoạch — flow.plans.GOAL_BEHIND)
STATUS = {"met": "Đạt", "ok": "Đúng nhịp", "behind": "Chậm", "missed": "Không đạt"}
TONES = {"Đạt": "green", "Đúng nhịp": "blue", "Chậm": "orange", "Không đạt": "red"}


# ── Kỳ ──

def _period(text: str) -> date:
    """"2026-11" / "11/2026" / trống (tháng này) ⇒ ngày đầu tháng."""
    t = (text or "").strip()
    if not t:
        return today().replace(day=1)
    try:
        if "/" in t:
            mo, yr = t.split("/", 1)
            return date(int(yr), int(mo), 1)
        yr, mo = t.split("-", 2)[:2]
        return date(int(yr), int(mo), 1)
    except ValueError as e:
        raise Invalid("Kỳ dạng 2026-11 (năm-tháng)") from e


def _next(p: date) -> date:
    return date(p.year + (p.month == 12), p.month % 12 + 1, 1)


def _elapsed(p: date) -> float:
    """Phần tháng đã qua (tháng sau = 0, tháng trước = 1)."""
    t = today()
    if t < p:
        return 0.0
    if t >= _next(p):
        return 1.0
    return t.day / calendar.monthrange(p.year, p.month)[1]


def _status(actual: float, target: float, elapsed: float) -> str:
    if actual >= target:
        return STATUS["met"]
    if elapsed >= 1:
        return STATUS["missed"]
    return STATUS["behind"] if elapsed > 0.1 and actual < target * elapsed * BEHIND else STATUS["ok"]


def _metric(key: str) -> registry.Metric:
    mt = registry.METRICS.get(key)
    if mt is None:
        raise NotFound(f"Không có chỉ số “{key}” — chọn trong danh sách chỉ số KPI")
    return mt


def _readable(ctx: Ctx, mt: registry.Metric) -> bool:
    """Chỉ số trả số CẢ CÔNG TY (quyền hệ thống) ⇒ chỉ người ĐỌC được tài nguyên của chỉ số mới chọn / thấy chỉ số đó (review 0101: thêm chỉ số
    tiền — kế toán, thù lao — không được thành đường lộ số qua KPI). Phạm vi dòng (người / đội nào) vẫn do RLS của kpi.targets."""
    return ctx.p.can(mt.resource, "read")


def _usable(ctx: Ctx, key: str) -> registry.Metric:
    mt = _metric(key)
    if not _readable(ctx, mt):
        raise Denied(f"Bạn không được xem số “{mt.label}” — không đặt / xem KPI theo chỉ số này được")
    return mt


async def _by(ctx: Ctx, metric: str, p: date, group_by: str) -> dict[str, int]:
    """Thực tế cả công ty theo người / đội trong kỳ — số từ chỉ số module khai (quyền hệ thống); bên gọi chỉ hiện dòng người xem được thấy."""
    out = await ctx.call_system(metric, since=p.isoformat(), until=_next(p).isoformat(), group_by=group_by)
    return {r["key"]: int(r["value"]) for r in out.get("rows", [])}


async def _subtrees(ctx: Ctx, team_ids: set[str]) -> dict[str, set[str]]:
    """Đội → chính nó + mọi đội con (chỉ tiêu đội cộng cả cây: khu vực = các khối = các team)."""
    if not team_ids:
        return {}
    rows = await ctx.all("""WITH RECURSIVE t(root, id) AS (SELECT id, id FROM core.teams WHERE id = ANY(%s::uuid[])
                                UNION SELECT t.root, c.id FROM core.teams c JOIN t ON c.parent_id = t.id)
                            SELECT root::text AS root, id::text AS id FROM t""", (list(team_ids),))
    out: dict[str, set[str]] = {}
    for r in rows:
        out.setdefault(r["root"], set()).add(r["id"])
    return out


def _fmt(value: float, unit: str) -> str:
    if unit == "đ":
        return f"{value / 1e9:.2f} tỷ".replace(".", ",") if value >= 1e9 else f"{value / 1e6:.0f} triệu"
    return f"{value:,.0f} {unit}".replace(",", ".")


# ── Chỉ tiêu ──

class PeriodIn(Input):
    period: str = field("Tháng", "", kind="month", example="2026-11", placeholder="Tháng này", aliases=("Kỳ (năm-tháng)", "Kỳ"), primary=True)


@m.query("metric_options", resource=None, title="Chỉ số KPI", agent="deny")
async def metric_options(ctx: Ctx, inp: PeriodIn) -> list[dict[str, str]]:
    return ui.options([{"value": k, "label": f"{mt.label} ({mt.unit})"} for k, mt in sorted(registry.METRICS.items(), key=lambda kv: kv[1].label)
                       if _readable(ctx, mt)])


class SetTarget(Input):
    period: str = field("Tháng", "", kind="month", example="2026-11", placeholder="Tháng này", aliases=("Kỳ (năm-tháng)", "Kỳ"), primary=True)
    metric: str = field("Chỉ số", options_query="kpi.metric_options", example="crm.metric_calls")
    user_email: str = field("Người", "", options_query="admin.member_options", example="sale1@sanmau.demo", aliases=("Người (email)", "Email"),
                            help="Chỉ tiêu của một người — hoặc chọn đội bên dưới")
    team_code: str = field("Hoặc đội / khu vực", "", options_query="admin.team_options", example="KD-A",
                           help="Chỉ tiêu của cả đội, tính cả các đội con")
    target: Money = field("Chỉ tiêu", example=3, help="Số theo đơn vị của chỉ số (hợp đồng, cuộc gọi, đồng…)")


@m.command("set_target", resource="kpi", title="Đặt chỉ tiêu KPI", importable=True, agent="propose")
async def set_target(ctx: Ctx, inp: SetTarget) -> dict[str, Any]:
    """Đặt / sửa chỉ tiêu tháng cho MỘT người hoặc MỘT đội trên một chỉ số. Trưởng đội đặt được cho người / đội trong cây mình quản lý (phạm vi
    đội); cả công ty cần phạm vi cả công ty. Trợ lý chỉ đề xuất — người có quyền bấm áp dụng."""
    p, mt = _period(inp.period), _usable(ctx, inp.metric)
    if bool(inp.user_email.strip()) == bool(inp.team_code.strip()):
        raise Invalid("Điền MỘT trong hai: người (email) hoặc đội")
    if inp.target <= 0:
        raise Invalid("Chỉ tiêu phải lớn hơn 0")
    if inp.user_email.strip():
        uid = await ctx.val("""SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()
                               WHERE lower(u.email) = lower(%s) AND mb.active""", (inp.user_email.strip(),))
        if uid is None:
            raise NotFound(f"Không có người {inp.user_email} trong công ty")
        row = await ctx.one("""INSERT INTO kpi.targets (period, metric, user_id, team_id, target, set_by)
                               VALUES (%s, %s, %s, %s, %s, core.me())
                               ON CONFLICT (tenant_id, period, metric, user_id) WHERE user_id IS NOT NULL
                               DO UPDATE SET target = EXCLUDED.target, team_id = EXCLUDED.team_id, set_by = core.me(), updated_at = now()
                               RETURNING id""", (p, mt.key, uid, await ctx.home_team(uid), inp.target))
        who = inp.user_email.strip()
    else:
        tid = await ctx.val("SELECT id FROM core.teams WHERE upper(code) = upper(%s)", (inp.team_code.strip(),))
        if tid is None:
            raise NotFound(f"Không có đội {inp.team_code}")
        row = await ctx.one("""INSERT INTO kpi.targets (period, metric, team_id, target, set_by) VALUES (%s, %s, %s, %s, core.me())
                               ON CONFLICT (tenant_id, period, metric, team_id) WHERE user_id IS NULL
                               DO UPDATE SET target = EXCLUDED.target, set_by = core.me(), updated_at = now() RETURNING id""",
                            (p, mt.key, tid, inp.target))
        who = inp.team_code.strip().upper()
    assert row is not None
    return {"target_id": row["id"], "summary": f"Chỉ tiêu {p:%m/%Y} · {who} · {mt.label}: {_fmt(float(inp.target), mt.unit)}"}


class CopyTargets(Input):
    from_period: str = field("Chép từ tháng", "", kind="month", example="2026-10", placeholder="Tháng trước", aliases=("Chép từ kỳ",))
    to_period: str = field("Sang tháng", "", kind="month", example="2026-11", placeholder="Tháng này", aliases=("Sang kỳ",))
    team_code: str = field("Chỉ đội / khu vực", "", options_query="admin.team_options", placeholder="Mọi chỉ tiêu bạn thấy", help="Gồm cả đội con")


@m.command("copy_targets", resource="kpi", title="Chép chỉ tiêu sang kỳ mới", agent="propose")
async def copy_targets(ctx: Ctx, inp: CopyTargets) -> dict[str, Any]:
    """Chép các chỉ tiêu người xem thấy (RLS — trưởng đội: cây mình quản lý) từ kỳ cũ sang kỳ mới; chỉ tiêu ĐÃ CÓ ở kỳ mới giữ nguyên (không ghi
    đè). Chỉ số người chép không được xem thì bỏ qua."""
    to = _period(inp.to_period)
    frm = _period(inp.from_period) if inp.from_period.strip() else (to - timedelta(days=1)).replace(day=1)
    if frm == to:
        raise Invalid("Kỳ chép và kỳ nhận trùng nhau")
    rows = [r for r in await _rows(ctx, frm, team_code=inp.team_code)]
    done = 0
    for r in rows:
        added = await ctx.exec("""INSERT INTO kpi.targets (period, metric, user_id, team_id, target, set_by)
                                  SELECT %s, metric, user_id, team_id, target, core.me() FROM kpi.targets WHERE id = %s
                                  ON CONFLICT DO NOTHING""", (to, r["id"]))
        done += added
    return {"copied": done, "skipped": len(rows) - done, "summary": f"Chép {done} chỉ tiêu {frm:%m/%Y} → {to:%m/%Y}"
            + (f" (giữ nguyên {len(rows) - done} chỉ tiêu đã có)" if len(rows) > done else "")}


# ── Thực tế ──

async def _rows(ctx: Ctx, p: date, *, team_code: str = "", metric: str = "", mine: bool = False) -> list[dict[str, Any]]:
    """Chỉ tiêu người xem thấy được (RLS) + thực tế + % + nhịp."""
    rows = await ctx.all(
        """SELECT t.id, t.metric, t.user_id::text AS user_id, t.team_id::text AS team_id, t.target, u.name AS person, tm.name AS team
           FROM kpi.targets t LEFT JOIN core.users u ON u.id = t.user_id LEFT JOIN core.teams tm ON tm.id = t.team_id
           WHERE t.period = %(p)s AND (%(m)s = '' OR t.metric = %(m)s) AND (NOT %(mine)s OR t.user_id = core.me())
             AND (%(team)s = '' OR t.team_id IN (
                 WITH RECURSIVE s AS (SELECT id FROM core.teams WHERE upper(code) = upper(%(team)s)
                                      UNION SELECT c.id FROM core.teams c JOIN s ON c.parent_id = s.id) SELECT id FROM s))
           ORDER BY t.metric, t.user_id IS NULL DESC, tm.name, u.name""", {"p": p, "m": metric, "mine": mine, "team": team_code.strip()})
    elapsed = _elapsed(p)
    cache: dict[tuple[str, str], dict[str, int]] = {}
    subtrees = await _subtrees(ctx, {r["team_id"] for r in rows if r["user_id"] is None and r["team_id"]})
    out = []
    for r in rows:
        mt = registry.METRICS.get(r["metric"])
        if mt is None or not _readable(ctx, mt):   # chỉ số người xem không được đọc ⇒ không hiện dòng (kể cả chỉ tiêu của chính mình)
            continue
        g = "person" if r["user_id"] else "team"
        if (r["metric"], g) not in cache:
            cache[(r["metric"], g)] = await _by(ctx, r["metric"], p, g)
        vals = cache[(r["metric"], g)]
        actual = vals.get(r["user_id"], 0) if r["user_id"] else sum(vals.get(t, 0) for t in subtrees.get(r["team_id"], {r["team_id"]}))
        target = float(r["target"])
        expected = target * elapsed
        out.append({"id": r["id"], "metric": r["metric"], "metric_label": mt.label, "unit": mt.unit, "user_id": r["user_id"],
                    "subject": r["person"] if r["user_id"] else f"Đội: {r['team']}", "team": r["team"] or "", "team_id": r["team_id"],
                    "target": target, "actual": actual, "pct": round(100 * actual / target, 1),
                    "expected": round(expected, 1), "gap": max(0.0, target - actual),
                    "status": _status(actual, target, elapsed), "target_text": _fmt(target, mt.unit), "actual_text": _fmt(actual, mt.unit)})
    return out


def _attn_board(o: Any) -> Line | None:
    """Câu bàn việc của người quản lý đội: bao nhiêu người đang chậm chỉ tiêu tháng, chậm nhất là ai — câu hỏi "ai cần kèm" của trưởng nhóm."""
    rows = [r for r in (o.get("rows") or []) if r.get("user_id") and r.get("status") in (STATUS["behind"], STATUS["missed"])] \
        if isinstance(o, dict) else []
    if not rows:
        return None
    r = min(rows, key=lambda x: x["pct"])
    return line("warn", f"{len({x['user_id'] for x in rows})} người trong đội đang chậm chỉ tiêu tháng",
                f"Chậm nhất: {r['subject']} — {r['pct']:.0f}% {r['metric_label'].lower()}",
                "Ai trong đội đang chậm chỉ tiêu, tôi nên kèm ai trước?")


def _attn_mine(o: Any) -> Line | None:
    """Câu bàn việc: chỉ số đang chậm nhất của tôi tháng này."""
    rows = [r for r in (o.get("rows") or []) if r.get("status") == STATUS["behind"]] if isinstance(o, dict) else []
    if not rows:
        return None
    r = min(rows, key=lambda x: x["pct"])
    return line("warn", f"KPI {r['metric_label'].lower()}: mới {r['pct']:.0f}% chỉ tiêu tháng",
                f"Đạt {r['actual_text']} / {r['target_text']} — cần thêm {_fmt(r['gap'], r['unit'])}")


@m.query("mine", resource="kpi", title="KPI của tôi")
async def mine(ctx: Ctx, inp: PeriodIn) -> dict[str, Any]:
    """Chỉ tiêu tháng của tôi: thực tế, % đạt, nhịp, còn thiếu bao nhiêu, và HẠNG của tôi giữa những người cùng chỉ tiêu (tên người khác hiện theo
    chính sách công ty)."""
    p = _period(inp.period)
    rows = await _rows(ctx, p, mine=True)
    for r in rows:
        peers = await _peer_rank(ctx, p, r["metric"])
        rank = next((i for i, x in enumerate(peers, 1) if x["user_id"] == r["user_id"]), None)
        r["rank"] = f"{rank}/{len(peers)}" if rank else "—"
    met = sum(1 for r in rows if r["status"] == STATUS["met"])
    return ui.table(rows, [
        ui.col("metric_label", "Chỉ số", width=220), ui.col("target_text", "Chỉ tiêu"), ui.col("actual_text", "Thực tế"),
        ui.col("pct", "% đạt", "percent"), ui.col("status", "Nhịp", "badge", tones=TONES), ui.col("rank", "Hạng"),
    ], title=f"KPI của tôi · {p:%m/%Y}", kpis=[ui.kpi("Chỉ tiêu", len(rows)), ui.kpi("Đã đạt", met),
                                               ui.kpi("Đang chậm", sum(1 for r in rows if r["status"] == STATUS["behind"]),
                                                      tone="orange" if any(r["status"] == STATUS["behind"] for r in rows) else None)],
        note="Thực tế tính từ dữ liệu hệ thống (cùng định nghĩa với báo cáo). Nhịp: so với đường cần đạt tới hôm nay.",
        empty="Tháng này chưa có chỉ tiêu nào của bạn.")


class BoardIn(Input):
    period: str = field("Tháng", "", kind="month", example="2026-11", placeholder="Tháng này", aliases=("Kỳ (năm-tháng)", "Kỳ"), primary=True)
    team_code: str = field("Đội / khu vực", "", options_query="admin.team_options", placeholder="Mọi đội bạn thấy", primary=True, help="Gồm cả các đội con")
    metric: str = field("Chỉ số", "", options_query="kpi.metric_options")


@m.query("board", resource="kpi", title="KPI đội")
async def board(ctx: Ctx, inp: BoardIn) -> dict[str, Any]:
    """Chỉ tiêu của người / đội trong phạm vi tôi quản lý (RLS): % đạt, nhịp, ai đang chậm; kỳ đã chốt thì so số chốt với số hiện tại."""
    p = _period(inp.period)
    rows = await _rows(ctx, p, team_code=inp.team_code, metric=inp.metric)
    # số chốt mới nhất của từng chỉ tiêu: dòng người khớp theo NGƯỜI (đội lúc chốt có thể khác), dòng đội khớp theo ĐỘI
    closed = {(c["metric"], c["subject"]): float(c["actual"]) for c in await ctx.all(
        """SELECT DISTINCT ON (metric, subject) metric, subject, actual FROM (
               SELECT metric, coalesce(user_id::text, 'team:' || team_id::text) AS subject, actual, closed_at FROM kpi.closings WHERE period = %s) c
           ORDER BY metric, subject, closed_at DESC""", (p,))}
    # chỉ tiêu ĐỘI so với TỔNG chỉ tiêu người trong cây (review 0101): không sai dữ liệu, nhưng lệch là chỗ cãi nhau khi trả thưởng ⇒ hiện ra
    people = await ctx.all("SELECT metric, team_id::text AS team_id, target FROM kpi.targets WHERE period = %s AND user_id IS NOT NULL", (p,))
    subtrees = await _subtrees(ctx, {r["team_id"] for r in rows if not r["user_id"] and r["team_id"]})
    for r in rows:
        if not r["user_id"] and r["team_id"]:
            tree = subtrees.get(r["team_id"], {r["team_id"]})
            total = sum(float(x["target"]) for x in people if x["metric"] == r["metric"] and x["team_id"] in tree)
            r["people_sum"] = _fmt(total, r["unit"]) if total else ""
            r["mismatch"] = "Lệch tổng người" if total and abs(total - r["target"]) > 1e-6 else ""
        else:
            r["people_sum"], r["mismatch"] = "", ""
    for r in rows:
        snap = closed.get((r["metric"], r["user_id"] or f"team:{r['team_id']}"))
        r["closed"] = "" if snap is None else _fmt(snap, r["unit"]) + ("" if snap == r["actual"] else " (đã đổi sau chốt)")
    behind = [r for r in rows if r["status"] in (STATUS["behind"], STATUS["missed"])]
    # thứ nhìn thấy đầu tiên (rà vai trưởng nhóm 18/11/2026): người cần kèm — chậm / không đạt, % thấp nhất — lên đầu bảng
    # người xem CẢ công ty (giám đốc — rà vai 18/11/2026): cấp ĐỘI trước rồi mới tới người; trưởng nhóm: người cần kèm lên đầu như trên
    company = ctx.p.has_scope("kpi", "all")
    rows.sort(key=lambda r: (company and bool(r["user_id"]), r["status"] not in (STATUS["behind"], STATUS["missed"]), r["pct"]))
    mismatched = sum(1 for r in rows if r["mismatch"])
    cols = [ui.col("subject", "Người / đội", width=200), ui.col("team", "Đội"), ui.col("metric_label", "Chỉ số", width=200),
            ui.col("target_text", "Chỉ tiêu"), ui.col("actual_text", "Thực tế"), ui.col("pct", "% đạt", "percent"),
            ui.col("status", "Nhịp", "badge", tones=TONES), ui.col("people_sum", "Tổng chỉ tiêu người"),
            ui.col("mismatch", "", "badge", tones={"Lệch tổng người": "orange"})]
    if closed:
        cols.append(ui.col("closed", "Số đã chốt"))
    return ui.table(rows, cols, title=f"KPI đội · {p:%m/%Y}", kpis=[
        ui.kpi("Chỉ tiêu", len(rows)), ui.kpi("Đã đạt", sum(1 for r in rows if r["status"] == STATUS["met"])),
        ui.kpi("Chậm / không đạt", len(behind), tone="orange" if behind else None),
        ui.kpi("% đạt trung bình", f"{sum(r['pct'] for r in rows) / len(rows):.0f}%" if rows else "—", "text"),
        *([ui.kpi("Đội lệch tổng người", mismatched, tone="orange",
                  hint="Chỉ tiêu đội khác tổng chỉ tiêu của người trong đội — không sai, nhưng nên thống nhất trước khi tính thưởng")] if mismatched else []),
    ], note="Chỉ hiện người / đội trong phạm vi bạn quản lý. Chỉ tiêu đội cộng cả các đội con.", empty="Kỳ này chưa có chỉ tiêu trong phạm vi của bạn.")


# ── Xếp hạng ──

class PeerIn(Input):
    period: date = field("Kỳ")
    metric: str = field("Chỉ số")


@m.query("peer_targets", resource=None, title="Chỉ tiêu cùng chỉ số (xếp hạng)", internal=True, agent="deny", callers=("kpi",),
         why="Xếp hạng KPI: mọi chỉ tiêu CÁ NHÂN cùng kỳ + chỉ số trong công ty — chỉ trả mã người + mã đội + chỉ tiêu; bên gọi tự quyết hiện tên "
             "theo phạm vi người xem và chính sách công ty (kpi.policy).")
async def peer_targets(ctx: Ctx, inp: PeerIn) -> list[dict[str, Any]]:
    return await ctx.all("""SELECT user_id::text AS user_id, team_id::text AS team_id, target FROM kpi.targets
                            WHERE period = %s AND metric = %s AND user_id IS NOT NULL""", (inp.period, inp.metric))


class CompanyTargetsIn(Input):
    metric: str = field("Chỉ số")
    since: date = field("Từ tháng")
    until: date = field("Đến tháng", help="Nửa mở: trước ngày này")


@m.query("company_targets", resource=None, title="Chỉ tiêu cả công ty theo tháng", internal=True, agent="deny", family="company_figures",
         why="Trang báo cáo quản trị của gói khai consumes(\"company_figures\") (thao tác gọi khai min_scope all): vẽ thực tế so với chỉ tiêu CẢ CÔNG TY theo tháng. Trả [{period: 'YYYY-MM', target}] — tổng chỉ tiêu ĐỘI GỐC "
             "(đội loại công ty không có đội cha) của chỉ số + khoảng tháng được hỏi; không chỉ tiêu cá nhân, không tên người. [id: module gọi tự có — chỉ số + khoảng tháng do "
             "trang báo cáo tự đặt, không có mã người / khách do người xem nhập]")
async def company_targets(ctx: Ctx, inp: CompanyTargetsIn) -> list[dict[str, Any]]:
    rows = await ctx.all("""SELECT to_char(t.period, 'YYYY-MM') AS period, sum(t.target)::bigint AS target
                            FROM kpi.targets t JOIN core.teams c ON c.id = t.team_id AND c.parent_id IS NULL AND c.kind = 'company'
                            WHERE t.user_id IS NULL AND t.metric = %s AND t.period >= %s AND t.period < %s GROUP BY 1 ORDER BY 1""", (inp.metric, inp.since, inp.until))
    return [{"period": r["period"], "target": int(r["target"])} for r in rows]


async def _peer_rank(ctx: Ctx, p: date, metric: str) -> list[dict[str, Any]]:
    """Mọi người cùng chỉ tiêu (kỳ, chỉ số), xếp theo % đạt rồi theo số thực tế — chưa lọc tên."""
    peers = await ctx.call_system("kpi.peer_targets", period=p.isoformat(), metric=metric)
    vals = await _by(ctx, metric, p, "person")
    rows = [{"user_id": x["user_id"], "team_id": x["team_id"], "target": float(x["target"]), "actual": vals.get(x["user_id"], 0)} for x in peers]
    for r in rows:
        r["pct"] = round(100 * r["actual"] / r["target"], 1)
    return sorted(rows, key=lambda r: (-r["pct"], -r["actual"], r["user_id"]))


class RankIn(Input):
    period: str = field("Tháng", "", kind="month", example="2026-11", placeholder="Tháng này", aliases=("Kỳ (năm-tháng)", "Kỳ"), primary=True)
    metric: str = field("Chỉ số", "", options_query="kpi.metric_options", placeholder="Chỉ số mặc định", help="Chỉ số gói đang bật chọn sẵn; không có thì chỉ số có nhiều chỉ tiêu nhất trong tháng")
    team_code: str = field("Trong đội / khu vực", "", options_query="admin.team_options", placeholder="Cả công ty", primary=True, help="Gồm cả các đội con")


async def _default_metric(ctx: Ctx, p: date) -> str:
    """Bảng xếp hạng mở không chọn chỉ số (mã chỉ số không viết cứng ở đây — công ty không có gói nào khai chỉ số đó vẫn mở được màn). Thứ tự quyết định,
    luôn xác định (không theo nhãn chữ):
    1. chỉ số GÓI ĐANG BẬT khai làm mặc định (``Pack(rank_metric=…)``), gói theo thứ tự khai, miễn người xem đọc được VÀ kỳ đó có chỉ tiêu cá nhân
       của chỉ số đó (hoặc kỳ chưa có chỉ tiêu nào) — công ty đặt chỉ tiêu trên chỉ số khác thì bảng xếp hạng mở theo chỉ số công ty đã đặt, không mở
       một bảng trống;
    2. không gói nào khai (hoặc chỉ số của gói không có chỉ tiêu trong kỳ) ⇒ chỉ số có NHIỀU chỉ tiêu cá nhân nhất trong kỳ, hòa thì theo thứ tự KHAI;
    3. kỳ không có chỉ tiêu nào ⇒ chỉ số gói khai, không có thì chỉ số đọc được đầu tiên theo thứ tự khai (không chọn ngẫu nhiên theo nhãn)."""
    usable = [mt for mt in registry.METRICS.values() if _readable(ctx, mt)]   # dict giữ thứ tự khai
    if not usable:
        raise NotFound("Công ty chưa có chỉ số nào để xếp hạng")
    on = await packs.enabled(ctx.conn, ctx.p.tenant_id)
    keys = {mt.key for mt in usable}
    counts = {r["metric"]: r["n"] for r in await ctx.all(
        "SELECT metric, count(*) AS n FROM kpi.targets WHERE period = %s AND user_id IS NOT NULL GROUP BY metric", (p,))}
    for pk in registry.PACKS.values():
        if pk.rank_metric and pk.key in on and pk.rank_metric in keys and (counts.get(pk.rank_metric) or not counts):
            return pk.rank_metric
    return max(usable, key=lambda mt: counts.get(mt.key, 0)).key   # max giữ phần tử ĐẦU khi hòa ⇒ thứ tự khai; không chỉ tiêu nào ⇒ chỉ số khai đầu


@m.query("ranking", resource="kpi", title="Xếp hạng KPI")
async def ranking(ctx: Ctx, inp: RankIn) -> dict[str, Any]:
    """Xếp hạng theo % chỉ tiêu tháng. Tên + số của người khác chỉ hiện khi: người đó thuộc phạm vi tôi được xem (của mình / đội tôi quản lý /
    cả công ty), HOẶC công ty bật "hiện tên trên bảng xếp hạng". Không thì chỉ thấy hạng của chính mình."""
    p = _period(inp.period)
    mt = _usable(ctx, inp.metric.strip() or await _default_metric(ctx, p))
    ranked = await _peer_rank(ctx, p, mt.key)
    if inp.team_code.strip():
        sub = await _subtrees(ctx, {str(t) for t in [await ctx.val("SELECT id FROM core.teams WHERE upper(code) = upper(%s)", (inp.team_code.strip(),))] if t})
        allowed = set().union(*sub.values()) if sub else set()
        ranked = [r for r in ranked if r["team_id"] in allowed]
    visible = {str(u) for u in [r["user_id"] for r in await ctx.all(
        "SELECT user_id FROM kpi.targets WHERE period = %s AND metric = %s AND user_id IS NOT NULL", (p, mt.key))]}
    show_all = bool(await ctx.val("SELECT show_names FROM kpi.policy"))
    names = {r["id"]: r for r in await ctx.all("""SELECT u.id::text AS id, u.name, tm.name AS team FROM core.users u
                                                   LEFT JOIN core.team_members m ON m.user_id = u.id AND m.tenant_id = core.tenant() AND m.is_home
                                                   LEFT JOIN core.teams tm ON tm.id = m.team_id
                                                   WHERE u.id = ANY(%s::uuid[])""", ([r["user_id"] for r in ranked],))}
    me = str(ctx.p.user_id)
    rows = []
    for i, r in enumerate(ranked, 1):
        if not (show_all or r["user_id"] in visible):
            continue
        n = names.get(r["user_id"], {})
        rows.append({"id": r["user_id"], "rank": i, "person": (n.get("name") or "—") + (" (bạn)" if r["user_id"] == me else ""),
                     "team": n.get("team") or "", "target_text": _fmt(r["target"], mt.unit), "actual_text": _fmt(r["actual"], mt.unit),
                     "pct": r["pct"]})
    mine_rank = next((i for i, r in enumerate(ranked, 1) if r["user_id"] == me), None)
    return ui.table(rows, [ui.col("rank", "Hạng", "number"), ui.col("person", "Người", width=200), ui.col("team", "Đội"),
                           ui.col("target_text", "Chỉ tiêu"), ui.col("actual_text", "Thực tế"), ui.col("pct", "% đạt", "percent")],
                    title=f"Xếp hạng · {mt.label} · {p:%m/%Y}", kpis=[
                        ui.kpi("Người có chỉ tiêu", len(ranked)), ui.kpi("Hạng của bạn", f"{mine_rank}/{len(ranked)}" if mine_rank else "—", "text")],
                    note=("" if show_all else "Chỉ hiện người trong phạm vi bạn được xem (công ty chưa bật hiện tên trên bảng xếp hạng). ")
                    + "Xếp theo % chỉ tiêu, bằng nhau thì theo số thực tế.", empty="Chưa ai có chỉ tiêu cho chỉ số này trong kỳ.")


# ── Chốt kỳ · chính sách ──

def _need_all(ctx: Ctx, why: str) -> None:
    if not ctx.p.has_scope("kpi", "all"):
        raise Denied(f"{why} — cần quyền quản lý KPI phạm vi cả công ty")


@m.command("close_period", resource="kpi", action="manage", title="Chốt KPI tháng", agent="propose")
async def close_period(ctx: Ctx, inp: PeriodIn) -> dict[str, Any]:
    """Chụp chỉ tiêu + thực tế của MỌI chỉ tiêu trong kỳ (chỉ thêm) — số dùng để trả thưởng, không đổi khi dữ liệu cũ được sửa sau. Chốt lại
    được (thêm ảnh chụp mới; màn KPI đội hiện số chốt mới nhất)."""
    _need_all(ctx, "Chốt KPI là việc của cả công ty")
    p = _period(inp.period)
    rows = await _rows(ctx, p)
    for r in rows:
        await ctx.exec("""INSERT INTO kpi.closings (period, metric, user_id, team_id, target, actual, closed_by)
                          SELECT period, metric, user_id, team_id, target, %s, core.me() FROM kpi.targets WHERE id = %s""",
                       (Decimal(str(r["actual"])), r["id"]))
    return {"closed": len(rows), "summary": f"Đã chốt KPI {p:%m/%Y}: {len(rows)} chỉ tiêu"}


class PolicyIn(Input):
    show_names: bool = field("Hiện tên mọi người trên bảng xếp hạng", False,
                             help="Tắt: mỗi người chỉ thấy hạng của mình (và người trong đội mình quản lý)")


@m.command("set_policy", resource="kpi", action="manage", title="Chính sách bảng xếp hạng", agent="deny")
async def set_policy(ctx: Ctx, inp: PolicyIn) -> dict[str, Any]:
    _need_all(ctx, "Chính sách bảng xếp hạng áp cho cả công ty")
    await ctx.exec("""INSERT INTO kpi.policy (show_names, updated_by) VALUES (%s, core.me())
                      ON CONFLICT (tenant_id) DO UPDATE SET show_names = EXCLUDED.show_names, updated_by = core.me(), updated_at = now()""",
                   (inp.show_names,))
    return {"show_names": inp.show_names}


# ── Màn ──
# Phân hệ RIÊNG (0124): KPI không còn là màn góp vào "Kinh doanh" (phân hệ của gói Khách hàng) — công ty không dùng Khách hàng vẫn có KPI.
m.workspace(Workspace("kpi", "KPI & xếp hạng", "target", (
    Screen("kpi_mine", "KPI của tôi", "target", "kpi.mine", tint="indigo",
           desk=Desk("watch", say=_attn_mine, menu=True, rank=40)),
    Screen("kpi_board", "KPI đội", "chart.bar.xaxis", "kpi.board", tint="indigo", min_scope="team",
           desk=Desk("watch", say=_attn_board, menu=True, team_only=True, rank=41),
           actions=(A("kpi.set_target", style="primary"), A("kpi.copy_targets"), A("kpi.close_period"), A("kpi.set_policy"))),
    Screen("kpi_ranking", "Xếp hạng KPI", "trophy.fill", "kpi.ranking", tint="yellow"),
), order=25))


# Báo cáo quản trị (0166): KPI đội và xếp hạng trong menu của ban giám đốc — cùng truy vấn
m.screens("mgmt", (Screen("mgmt_kpi", "KPI đội", "target", "kpi.board", group="Nhân sự & KPI", tint="orange", min_scope="all"),
                   Screen("mgmt_rank", "Xếp hạng KPI", "trophy.fill", "kpi.ranking", group="Nhân sự & KPI", tint="orange", min_scope="all")), rank=40)
