"""BÁO CÁO CHẠY SONG SONG (ADR 0230) — trong lúc công ty chạy Barista ở chế độ chỉ ghi nhận (0185, 0207) cạnh hệ đang dùng, cho ban giám đốc thấy Barista BẮT ĐƯỢC GÌ từ
chính dữ liệu của họ, kèm tác động bằng tiền / số lượng, công thức, độ tin cậy và dòng gốc — trước ngày chuyển hẳn.

Chỉ GOM + HIỂN THỊ (như ``health``): mỗi module / gói tự khai phần của mình bằng truy vấn nội bộ ``<module>.findings_report`` (hợp đồng và luật cứng ở ``kernel/findings.py``);
module này không biết ngành nào, không đọc bảng của ai. Thứ chưa đo được thì nói "chưa đo được — vì …", KHÔNG BAO GIỜ hiện 0. Chỉ ĐỌC: không thông báo, không chia lead, không
webhook. Xuất cho hội đồng quản trị: cắm vào ``compose.brochure`` (0224) loại ``parallel_report`` — trang tạp chí dựng từ cùng các truy vấn, số chỉ đến từ truy vấn.
"""

import logging
from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID

from barista.api import Ctx, Day, Input, Invalid, Module, Pack, Screen, Workspace, can_run, field, today, ui
from barista.api import findings as fd
from barista.api.brochure import data as bro_data
from barista.api.brochure import heading as bro_heading
from barista.api.brochure import kind as bro_kind
from barista.api.brochure import paragraph as bro_paragraph
from barista.api.brochure import recipe as bro_recipe

log = logging.getLogger("barista.findings")

Pack("findings", "Báo cáo chạy song song",
     "Khi công ty chạy hệ thống mới song song với hệ đang dùng: thấy ngay hệ thống BẮT ĐƯỢC gì (chi phí không ra kết quả, việc chưa ai nhận, dữ liệu trùng, phân chia lệch…) "
     "kèm tác động ước tính, công thức, độ tin cậy và dòng gốc; thứ chưa đo được nói rõ vì sao. Xuất thành tờ gửi hội đồng quản trị.",
     kind="capability",
     grants=(("admin", "findings", "all", "read"), ("director", "findings", "all", "read")),
     tools={"director": ("findings.overview",)})
m = Module("findings", "Báo cáo chạy song song", pack="findings", resources={"findings": "Báo cáo chạy song song (số cả công ty)"})

BRIEF_KIND = "parallel_report"
PERIODS = {"since_start": "Từ khi bắt đầu chạy song song", "last_7": "7 ngày qua", "last_30": "30 ngày qua", "this_month": "Tháng này", "last_month": "Tháng trước"}
GROUP_ORDER = ("money", "leads", "fairness", "process")
MAX_SPAN_DAYS = 400
ROWS_IN_EXPORT = 12


class PeriodIn(Input):
    period: Literal["since_start", "last_7", "last_30", "this_month", "last_month"] = field("Kỳ", "since_start", options=PERIODS)
    date_from: Day | None = field("Từ ngày", None, help="Chọn khoảng ngày riêng — thay cho ô Kỳ")
    date_to: Day | None = field("Đến ngày", None, help="Trống = hôm nay")


class WindowIn(Input):
    date_from: date
    date_to: date


class ItemIn(WindowIn):
    key: str = field("Phát hiện", hidden=True)


async def _first_day(ctx: Ctx) -> date | None:
    """Ngày lead đầu tiên vào hệ thống — mốc "bắt đầu chạy song song". Không có gói khách hàng / chưa có lead ⇒ None."""
    got = await ctx.call_system_optional("crm.lead_span", date_from=date(2000, 1, 1), date_to=today())
    return (got or {}).get("first_on")


async def resolve(ctx: Ctx, period: str, d_from: date | None, d_to: date | None) -> tuple[date, date]:
    t = today()
    if d_from or d_to:
        f, e = d_from or (d_to or t) - timedelta(days=29), d_to or t
    elif period == "last_7":
        f, e = t - timedelta(days=6), t
    elif period == "last_30":
        f, e = t - timedelta(days=29), t
    elif period == "this_month":
        f, e = t.replace(day=1), t
    elif period == "last_month":
        e = t.replace(day=1) - timedelta(days=1)
        f = e.replace(day=1)
    else:
        f, e = (await _first_day(ctx)) or t - timedelta(days=29), t
    if e < f:
        raise Invalid("Đến ngày phải sau Từ ngày")
    if (e - f).days > MAX_SPAN_DAYS:
        raise Invalid(f"Kỳ tối đa {MAX_SPAN_DAYS} ngày")
    return f, e


async def _one(ctx: Ctx, name: str, f: date, t: date) -> dict[str, Any] | None:
    """Một module đóng góp, trong điểm lưu riêng: lỗi của một module không làm mất cả báo cáo (ghi log, báo "chưa đọc được")."""
    try:
        async with ctx.conn.transaction():
            return await ctx.call_system_optional(name, date_from=f, date_to=t)
    except Exception:
        log.exception("findings: %s lỗi", name)
        return {"_error": True}


async def collect(ctx: Ctx, f: date, t: date, *, only: str | None = None) -> dict[str, Any]:
    """{window, findings: [Finding…] đã xếp, context, notes, unchecked (module chưa bật gói), failed (module lỗi), invalid (phát hiện sai khuôn)}."""
    found: list[tuple[str, fd.Finding]] = []
    context: list[dict[str, Any]] = []
    notes: list[str] = []
    unchecked: list[str] = []
    failed: list[str] = []
    invalid: list[str] = []
    for module, name, label in fd.contributors(only):
        r = await _one(ctx, name, f, t)
        if r is None:
            unchecked.append(label)
            continue
        if r.get("_error"):
            failed.append(label)
            continue
        for raw in r.get("findings") or []:
            p = fd.parse(raw)
            if p is None or not p.key.startswith(module + "."):
                invalid.append(str((raw or {}).get("key", "?")) if isinstance(raw, dict) else "?")
                continue
            found.append((module, p))
        context += r.get("context") or []
        notes += r.get("notes") or []
    gi = {g: i for i, g in enumerate(GROUP_ORDER)}
    found.sort(key=lambda mf: (not mf[1].measurable, gi[mf[1].group], -(mf[1].impact.value if mf[1].impact and mf[1].impact.kind == "money" else 0),
                               -(mf[1].impact.value if mf[1].impact else 0), mf[1].key))
    return {"window": (f, t), "findings": [x for _, x in found], "context": context, "notes": list(dict.fromkeys(notes)), "unchecked": unchecked,
            "failed": failed, "invalid": invalid}


def _kpi(ctx: Ctx, mt: fd.Metric, tone: str | None = None) -> dict[str, Any]:
    """Ô số; bấm mở dòng gốc CHỈ khi người xem mở được truy vấn đó (RLS vẫn áp)."""
    drill = mt.drill
    if drill and not _can(ctx, drill["query"]):
        drill = None
    return ui.kpi(mt.label, mt.value, mt.format, tone=tone, hint=mt.hint, drill=drill)


def _can(ctx: Ctx, name: str) -> bool:
    return can_run(ctx.p, name)


def _open(ctx: Ctx, drill: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not drill or not _can(ctx, drill["query"]):
        return []
    return [{"title": "Xem các dòng gốc", "query": drill["query"], "input": drill["input"]}]


def _blocks_for(ctx: Ctx, x: fd.Finding) -> list[dict[str, Any]]:
    facts = [f"Cách tính: {x.formula}", f"Độ tin cậy: {fd.CONFIDENCE[x.confidence]}", *x.caveats]
    hit = x.needs_attention
    tone = "warn" if hit else "ok"
    out = [ui.b_callout(tone, x.title, x.summary, facts=facts)]
    tiles = []
    if x.impact:
        imp = fd.Metric(label=x.impact.label[:1].upper() + x.impact.label[1:], value=x.impact.value, format="money" if x.impact.kind == "money" else "number", drill=x.impact.drill)
        tiles.append(_kpi(ctx, imp, tone="orange" if hit else "green"))
    tiles += [_kpi(ctx, mt) for mt in x.metrics[:5]]
    if tiles:
        out.append(ui.b_kpis(tiles))
    return out


async def _overview(ctx: Ctx, f: date, t: date) -> dict[str, Any]:
    r = await collect(ctx, f, t)
    got = [x for x in r["findings"] if x.measurable]
    miss = [x for x in r["findings"] if not x.measurable]
    hits = [x for x in got if x.needs_attention]
    blocks: list[dict[str, Any]] = [ui.b_kpis([
        *[ui.kpi(c["label"], c["value"], c.get("format", "number"), hint=c.get("hint", "")) for c in r["context"][:3]],
        ui.kpi("Phát hiện cần chú ý", len(hits), tone="orange" if hits else "green"),
        ui.kpi("Đã kiểm, không thấy vấn đề", len(got) - len(hits), tone="green"),
        ui.kpi("Chưa đo được", len(miss), tone=None),
    ])]
    blocks.append(ui.b_callout("accent", "Cách đọc báo cáo", facts=[
        "Hệ thống đang chạy song song ở chế độ chỉ ghi nhận: dữ liệu vào đủ nhưng chưa giao việc cho ai — báo cáo này chỉ ĐỌC, không gửi thông báo, không giao việc cho ai.",
        "Mỗi con số có công thức và dòng gốc (bấm vào ô số). Các ước tính có thể chồng lên nhau (một lead vừa chưa có người nhận vừa là lead trùng) nên KHÔNG cộng lại thành một tổng.",
        "“Chưa đo được” khác với 0: 0 nghĩa là đã kiểm và không thấy; chưa đo được nghĩa là hệ thống chưa có dữ liệu để kiểm."]))
    if r["notes"]:
        blocks.append(ui.b_callout("muted", "Lưu ý về dữ liệu của kỳ này", facts=r["notes"]))
    problems = [*[f"Phần của {x} chưa kiểm (gói chưa bật)." for x in r["unchecked"]], *[f"Phần của {x} chưa đọc được do lỗi hệ thống — đã ghi lại." for x in r["failed"]],
                *[f"Một phát hiện của module nêu mã {k} sai khuôn nên bị bỏ." for k in r["invalid"]]]
    if problems:
        blocks.append(ui.b_callout("warn", "Chưa kiểm hết", facts=problems))
    for x in got:
        blocks += _blocks_for(ctx, x)
    if miss:
        blocks.append(ui.b_list([{"title": x.title, "subtitle": f"Chưa đo được — vì {x.why_not}. Sẽ đo được khi {x.unlock}.", "badge": "Chưa đo được", "tone": "muted"}
                                 for x in miss], "Chưa đo được (không phải bằng 0)"))
    actions = [ui.act("compose.brochure", "Xuất tờ cho hội đồng quản trị", {"kind": BRIEF_KIND, "subject": f"{f.isoformat()}..{t.isoformat()}"}, "primary")] \
        if _can(ctx, "compose.brochure") else []
    return ui.card("Báo cáo chạy song song", blocks, subtitle=f"{f:%d/%m/%Y} – {t:%d/%m/%Y} · Barista bắt được gì từ dữ liệu thật của công ty", icon="arrow.triangle.branch",
                   actions=actions, ask={"title": "Hỏi trợ lý về báo cáo này", "prompt": "Về báo cáo chạy song song: "})


@m.query("overview", resource="findings", title="Báo cáo chạy song song", min_scope="all")
async def overview(ctx: Ctx, inp: PeriodIn) -> dict[str, Any]:
    """Hệ thống Barista bắt được gì trong lúc chạy song song với hệ đang dùng — do các module khai (chi phí không ra kết quả, việc chưa có người nhận, dữ liệu trùng, phân chia lệch…), kèm tác
    động ước tính, công thức, độ tin cậy, dòng gốc; mục chưa đo được nói rõ vì sao. Số cả công ty; chỉ đọc."""
    f, t = await resolve(ctx, inp.period, inp.date_from, inp.date_to)
    return await _overview(ctx, f, t)


m.workspace(Workspace("parallel", "Chạy song song", "arrow.triangle.branch", (
    Screen("overview", "Báo cáo chạy song song", "chart.bar.doc.horizontal", "findings.overview", tint="orange", min_scope="all"),), order=26))


# ── Xuất cho hội đồng quản trị: công thức ``compose.brochure`` (0224) — mọi con số là ảnh chụp các truy vấn dưới đây, chạy bằng quyền người soạn ──

async def _pick(ctx: Ctx, inp: ItemIn) -> fd.Finding:
    mod = inp.key.split(".")[0]
    r = await collect(ctx, inp.date_from, inp.date_to, only=mod)
    for x in r["findings"]:
        if x.key == inp.key:
            return x
    raise Invalid("Không có phát hiện này trong kỳ")


@m.query("item", resource="findings", title="Một phát hiện: số, bảng dòng gốc, công thức", min_scope="all")
async def item(ctx: Ctx, inp: ItemIn) -> dict[str, Any]:
    """Số + vài dòng gốc đầu của MỘT phát hiện (cho tờ xuất; mở được cả riêng lẻ)."""
    x = await _pick(ctx, inp)
    kpis = []
    if x.impact:
        kpis.append(ui.kpi(x.impact.label[:1].upper() + x.impact.label[1:], x.impact.value, "money" if x.impact.kind == "money" else "number"))
    kpis += [ui.kpi(mt.label, mt.value, mt.format) for mt in x.metrics[:3]]
    rows: list[dict[str, Any]] = []
    cols: list[dict[str, Any]] = []
    if x.drill and _can(ctx, x.drill["query"]):
        try:
            async with ctx.conn.transaction():
                page = await ctx.call(x.drill["query"], **x.drill["input"])
            cols, rows = page.get("columns") or [], (page.get("rows") or [])[:ROWS_IN_EXPORT]
            dates = [c["key"] for c in cols if c.get("format") == "date"]
            if dates:   # tờ gửi hội đồng: ngày viết d/m/yyyy, không dạng 2026-11-15
                rows = [{**r, **{k: fd.day_text(date.fromisoformat(str(r[k])[:10])) for k in dates if r.get(k)}} for r in rows]
                cols = [{**c, "format": "text"} if c["key"] in dates else c for c in cols]
        except Exception:
            log.exception("findings: dòng gốc của %s", x.key)
    return ui.table(rows, cols, title="", kpis=kpis, empty="", note=f"Độ tin cậy: {fd.CONFIDENCE[x.confidence]}") if rows else \
        ui.table([], [ui.col("k", "—")], title="", kpis=kpis, empty="", note=f"Độ tin cậy: {fd.CONFIDENCE[x.confidence]}")


@m.query("item_text", resource="findings", title="Một phát hiện: điều bắt được và cách tính", min_scope="all")
async def item_text(ctx: Ctx, inp: ItemIn) -> dict[str, Any]:
    x = await _pick(ctx, inp)
    secs = [ui.section("", text=x.summary)]
    secs.append(ui.section("Cách tính", text=x.formula))
    if x.caveats:
        secs.append(ui.section("Lưu ý", text="\n".join(x.caveats)))
    return ui.detail("", secs)


@m.query("cover_figures", resource="findings", title="Số nền của báo cáo chạy song song", min_scope="all")
async def cover_figures(ctx: Ctx, inp: WindowIn) -> dict[str, Any]:
    r = await collect(ctx, inp.date_from, inp.date_to)
    got = [x for x in r["findings"] if x.measurable]
    hits = [x for x in got if x.needs_attention]
    k = [ui.kpi("Từ ngày", fd.day_text(inp.date_from), "text"), ui.kpi("Đến ngày", fd.day_text(inp.date_to), "text"),
         *[ui.kpi(c["label"], c["value"], c.get("format", "number")) for c in r["context"][:2]],
         ui.kpi("Phát hiện cần chú ý", len(hits)), ui.kpi("Chưa đo được", len(r["findings"]) - len(got))]
    return ui.table([], [ui.col("k", "—")], title="", kpis=k, empty="")


@m.query("unmeasured", resource="findings", title="Những mục chưa đo được và khi nào đo được", min_scope="all")
async def unmeasured(ctx: Ctx, inp: WindowIn) -> dict[str, Any]:
    r = await collect(ctx, inp.date_from, inp.date_to)
    rows = [{"id": x.key, "title": x.title, "why": f"Chưa đo được — vì {x.why_not}", "unlock": f"Sẽ đo được khi {x.unlock}"} for x in r["findings"] if not x.measurable]
    return ui.table(rows, [ui.col("title", "Hạng mục", width=220), ui.col("why", "Vì sao chưa đo được", width=300), ui.col("unlock", "Khi nào đo được", width=300)],
                    title="", empty="Mọi hạng mục đều đã đo được",
                    note="Chưa đo được khác với bằng 0: hệ thống chưa có dữ liệu để kiểm.")


@m.query("guide", resource="findings", title="Cách đọc báo cáo chạy song song", min_scope="all")
async def guide(ctx: Ctx, inp: WindowIn) -> dict[str, Any]:
    return ui.detail("", [ui.section("", text=(
        "Trong giai đoạn chạy song song, hệ thống mới nhận dữ liệu thật của công ty nhưng chưa giao việc cho ai và chưa gửi thông báo nào; hệ đang dùng vẫn là nơi làm việc hằng ngày. "
        "Báo cáo này chỉ đọc dữ liệu đã có và cho biết hệ thống mới bắt được gì."),),
        ui.section("Về các con số", text=(
            "Mỗi phát hiện nêu số đo, công thức và độ tin cậy. Các ước tính tiền có thể chồng lên nhau nên không cộng lại. "
            "Mục ghi “chưa đo được” nghĩa là hệ thống chưa có dữ liệu để kiểm, không có nghĩa là không có vấn đề."))])


class BrochureAsk(Input):
    """Hỏi của ``compose.brochure`` (lõi): ``op`` = kinds | build; ``subject`` = kỳ ``YYYY-MM-DD..YYYY-MM-DD``."""
    op: str = field("Việc", "kinds", hidden=True)
    kind: str = field("Loại", "", hidden=True)
    subject: str = field("Kỳ", "", hidden=True)
    lead_id: UUID | None = field("Khách", None, hidden=True)


@m.plug("compose.brochure", "Báo cáo chạy song song cho hội đồng quản trị", resource="findings")
async def plug_brochure(ctx: Ctx, inp: BrochureAsk) -> dict[str, Any] | None:
    """Loại tờ ``parallel_report``: bìa + số nền + từng phát hiện đo được (điều bắt được, cách tính, số, dòng gốc đầu) + bảng mục chưa đo được. Chạy bằng quyền người soạn."""
    if inp.op == "kinds":
        return {"kinds": [bro_kind(BRIEF_KIND, "Báo cáo chạy song song (cho hội đồng quản trị)", "Hệ thống bắt được gì từ dữ liệu thật của công ty, kèm tác động, công thức và mục chưa đo được")]}
    if inp.kind != BRIEF_KIND:
        return None
    sub = inp.subject.strip()
    try:
        a, b = (sub.split("..", 1) if ".." in sub else ("", ""))
        f, t = await resolve(ctx, "since_start", date.fromisoformat(a) if a else None, date.fromisoformat(b) if b else None)
    except ValueError:
        raise Invalid("Kỳ báo cáo dạng 2026-11-12..2026-11-15 (hoặc mở từ màn Báo cáo chạy song song)") from None
    win = {"date_from": f.isoformat(), "date_to": t.isoformat()}
    r = await collect(ctx, f, t)
    blocks: list[dict[str, Any]] = [bro_paragraph("Kết quả giai đoạn vận hành song song hai hệ thống, tính từ dữ liệu thật của công ty", slot="cover"),
                                    bro_data("findings.cover_figures", "", win, slot="tiles"),
                                    bro_heading("Cách đọc báo cáo", level=1), bro_data("findings.guide", "", win),
                                    bro_heading("Điều hệ thống đã bắt được", level=1),
                                    bro_paragraph("Mỗi mục dưới đây nêu điều bắt được, cách tính, độ tin cậy và các dòng gốc đầu tiên; số đều lấy trực tiếp từ dữ liệu của công ty.")]
    for x in r["findings"]:
        if x.measurable:
            call = {**win, "key": x.key}
            blocks += [bro_heading(x.title, level=2), bro_data("findings.item_text", "", call), bro_data("findings.item", "", call, required=False)]
    if any(not x.measurable for x in r["findings"]):
        blocks += [bro_heading("Những mục chưa đo được", level=1), bro_data("findings.unmeasured", "", win)]
    return {"recipe": bro_recipe("Báo cáo chạy song song", blocks, brief="Báo cáo chạy song song cho hội đồng quản trị, dựng từ dữ liệu của công ty")}
