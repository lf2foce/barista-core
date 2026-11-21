"""Hợp đồng giao diện: truy vấn trả TRANG (bảng hoặc chi tiết) do server mô tả — app chỉ vẽ, không tự tính số.

Bảng::
    {"kind": "table", "title", "kpis": [{label, value, format, tone}], "columns": [{key, label, format, labels?, tones?}],
     "rows": [{…, "id"}], "row_key": "id", "empty": "…"}
Chi tiết::
    {"kind": "detail", "title", "subtitle", "sections": [{title, fields: [{label, value, format}] | text}],
     "links"?: [{title, path, filename}], "actions"?: [hành động], "style"?: "document",
     "ask"?: {title, prompt}, "original"?: {title, path, filename, page, kind: "pdf"}, "text_role"?: "extracted"}
``original`` (0102): BẢN GỐC để XEM trước tiên (trình xem PDF nhúng; file Word / Excel đã đổi sang PDF ở máy chủ) — đường ``path`` lỗi
⇒ app cho tải file như ``links``. ``text_role="extracted"``: các mục là CHỮ BÓC / OCR từ bản gốc — app để ở tab phụ ("Văn bản trích"), không
thay bản gốc. Mục có ``table`` {columns, rows, total?}: vẽ BẢNG thật (không ghép chữ có dấu |).
``style="document"``: vẽ như văn bản liền mạch (mục = tiêu đề, chọn / chép cả bài). ``ask``: nút đưa câu mở đầu vào ô chat.

``format``: text · money · number · percent · date · datetime · badge · phone. Badge có ``labels`` (giá trị → chữ) và
``tones`` (giá trị → green / orange / red / blue / gray / purple).
Chỉ được THÊM trường; đổi tên / bỏ trường ⇒ sửa app Mac cùng commit.
"""

import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, timedelta
from io import BytesIO
from typing import Any
from uuid import UUID

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter

from barista.kernel.fields import phone_display
from barista.kernel.text import fold

FORMATS = ("text", "money", "number", "percent", "date", "datetime", "badge", "phone")

# ── QUYỀN THEO KHỐI (lớp 3, ADR 0239 bổ sung 18/11/2026 lát 2, api 1.59) ──
# Một màn, nhiều khối: khối / ô số / cột / chuỗi biểu đồ / mục danh sách TỰ KHAI cần nhóm thông tin nào (``needs=``) — cửa ra của executor
# (``executor.viewer_cut``) bỏ phần người xem không đủ quyền TRƯỚC khi trả (web, app, trợ lý, xuất Excel, bản chốt), kể cả GIÁ TRỊ của cột trong từng
# dòng. Một mục ``needs`` = ``"<khoá>"`` hoặc ``"<khoá>:<phạm vi tối thiểu>"``; khoá = khoá ``InfoGroup`` của một gói (vd nhóm phí môi giới) hoặc tên tài
# nguyên quyền; phạm vi mặc định ``own`` (có quyền xem ở bất kỳ phạm vi nào — DÒNG nào thấy vẫn do RLS); số GỘP cả công ty khai ``:all``. Nhiều mục ⇒
# cần đủ cả. Dấu (ô ``needs_info`` — ``needs.MARK``) ở lại trong đầu ra (bản chốt / thẻ lưu được lọc lại theo người mở sau này); app bỏ qua trường lạ.
Needs = str | tuple[str, ...] | list[str] | None


def needs_of(needs: Needs) -> list[str]:
    """Chuẩn hoá ``needs=`` về danh sách chuỗi (rỗng = không khai)."""
    if needs is None:
        return []
    items = [needs] if isinstance(needs, str) else list(needs)
    out = [x.strip() for x in items if isinstance(x, str) and x.strip()]
    assert len(out) == len([x for x in items if x]), needs
    return out


def gated(x: dict[str, Any], needs: Needs) -> dict[str, Any]:
    """Gắn ``needs`` cho một phần tử bất kỳ của trang (mục ``b_list``, ô ``b_tiles``, khối tự dựng…) — trống ⇒ trả nguyên."""
    n = needs_of(needs)
    return {**x, "needs_info": n} if n else x


def need(resource: str | None, scope: str = "") -> str | None:
    """``needs`` từ tài nguyên + phạm vi tối thiểu (``None`` khi không có tài nguyên — vd gói không khai tài nguyên của giá trị)."""
    return None if not resource else f"{resource}:{scope}" if scope else resource


def col(key: str, label: str, fmt: str = "text", *, labels: dict[str, str] | None = None,
        tones: dict[str, str] | None = None, width: int | None = None, needs: Needs = None) -> dict[str, Any]:
    """``needs``: cột của một nhóm thông tin — người xem thiếu quyền ⇒ cột VÀ giá trị ``key`` của mọi dòng bị bỏ ở cửa ra (lớp 3)."""
    assert fmt in FORMATS, fmt
    c: dict[str, Any] = {"key": key, "label": label, "format": fmt}
    if n := needs_of(needs):
        c["needs_info"] = n
    if labels:
        c["labels"] = labels
    if tones:
        c["tones"] = tones
    if width:
        c["width"] = width
    return c


def kpi(label: str, value: Any, fmt: str = "number", tone: str | None = None, hint: str = "",
        drill: dict[str, Any] | None = None, *, needs: Needs = None) -> dict[str, Any]:
    """``drill`` (``ui.drill``): NGUỒN của con số — bấm ô số ⇒ mở đúng các dòng tạo nên nó (quyền người xem). Có nguồn thì phải KHỚP: đếm (hoặc
    cộng cột ``sum``) các dòng của nguồn = con số — ``tests/test_drill.py`` kiểm mọi ô số khai nguồn trên dữ liệu mẫu (0097)."""
    out = {"label": label, "value": value, "format": fmt, "tone": tone, "hint": hint}
    if drill is not None:
        out["drill"] = drill
    return gated(out, needs)


def drill(query: str, input: dict[str, Any] | None = None, *, sum: str | None = None) -> dict[str, Any]:
    """Nguồn của một ô số (0097): truy vấn trả bảng + đầu vào ⇒ các dòng tạo nên con số; ``sum`` = cột được cộng (trống = đếm dòng)."""
    return {"query": query, "input": jsonable_input(input or {}), "sum": sum}


def jsonable_input(v: Any) -> Any:
    """Ngày / mã trong đầu vào của nguồn ⇒ chuỗi (app gửi lại nguyên văn khi mở)."""
    if isinstance(v, dict):
        return {k: jsonable_input(x) for k, x in v.items()}
    if isinstance(v, date | UUID):
        return v.isoformat() if isinstance(v, date) else str(v)
    return v


def table(rows: list[dict[str, Any]], columns: list[dict[str, Any]], *, title: str = "",
          kpis: list[dict[str, Any]] | None = None, empty: str = "Chưa có dữ liệu", row_key: str = "id",
          note: str = "", limit: int | None = None, views: dict[str, Any] | None = None,
          empty_actions: list[dict[str, Any]] | None = None, header: list[dict[str, Any]] | None = None,
          paged_total: int | None = None) -> dict[str, Any]:
    """``empty_actions`` (``act(...)``): nút hiện dưới câu ``empty`` khi bảng không có dòng — việc nên làm tiếp (vd "Thêm người / phòng"); chỉ THÊM
    trường, app cũ chỉ hiện câu. Module chỉ khai nút người xem chạy được. ``limit``: giới hạn LIMIT của truy vấn — chạm trần thì ghi rõ để không ai tưởng đó là toàn bộ (số KPI cũng chỉ
    tính trên các dòng đang hiện). ``views``: góc nhìn khác của CÙNG các dòng (``views(...)``). ``header``: khối thẻ (``b_picker``, ``b_callout``…) vẽ
    TRÊN bảng — vd chọn mức đặt sẵn trước bảng chi tiết; chỉ THÊM trường, app chưa biết thì bỏ qua."""
    if limit is not None and len(rows) >= limit:
        cut = f"Đang hiện {limit:,} dòng đầu — lọc hẹp hơn để xem đủ.".replace(",", ".")
        note = f"{note} {cut}".strip()
    out = {"kind": "table", "title": title, "kpis": kpis or [], "columns": columns, "rows": rows, "row_key": row_key,
           "empty": empty, "note": note}
    if empty_actions and not rows:
        out["empty_actions"] = empty_actions
    if header:
        out["header"] = header
    if limit is not None and len(rows) < limit:   # truy vấn chưa chạm trần ⇒ số dòng là TỔNG THẬT (thẻ gọn ghi "3/28"); không khai trần ⇒ không biết
        out["total"] = len(rows)
    if views:
        assert {c["key"] for c in columns} >= {v for spec in views.values() for k, v in spec.items() if k in ("by", "date")}, views
        if b := views.get("board"):   # cột Kanban THEO THỨ TỰ nhãn của cột trạng thái (app không phải đoán thứ tự / nhãn)
            col = next(c for c in columns if c["key"] == b["by"])
            keys = list(col.get("labels") or {}) or list(dict.fromkeys(str(r.get(b["by"]) or "") for r in rows))
            views = {**views, "board": {**b, "columns": [{"key": k, "title": (col.get("labels") or {}).get(k, k or "—"),
                                                          "tone": (col.get("tones") or {}).get(k)} for k in keys]}}
        out["views"] = views
    if paged_total is not None and (pg := paging()) is not None:   # truy vấn đã tự lấy đúng một trang ở SQL (``paging()``) — ``rows`` là trang đó
        out["paging"] = {"offset": pg.offset, "limit": pg.limit, "total": paged_total, "complete": True}
        out["total"] = paged_total
    return out


def views(*, board: dict[str, Any] | None = None, calendar: dict[str, Any] | None = None,
          timeline: dict[str, Any] | None = None) -> dict[str, Any]:
    """Góc nhìn khác của CÙNG các dòng bảng (0087) — app đổi Bảng ↔ Kanban ↔ Lịch không cần truy vấn mới; server khai bảng nào hợp góc nào.
    ``timeline`` (0222) = một khối ``b_timeline`` server dựng sẵn (không tham chiếu cột — dòng thời gian có hàng riêng: việc của từng kế hoạch);
    app chưa biết góc này thì không hiện tab, bảng vẫn như cũ."""
    return {k: v for k, v in (("board", board), ("calendar", calendar), ("timeline", timeline)) if v}


def board_view(by: str, title: str, subtitle: str = "", move: dict[str, Any] | None = None) -> dict[str, Any]:
    """Kanban theo cột ``by`` (cột badge có ``labels`` — thứ tự nhãn là thứ tự cột). ``move`` = ``{command, id_key, column_key,
    form_columns?}`` như khối board: kéo thẻ sang cột = CHẠY LỆNH đó (server kiểm quyền / luật như mọi lệnh); không có ``move`` ⇒ chỉ xem.
    Đừng khai ``move`` cho cột mà đổi trạng thái không phải một lệnh (vd "đã ký" là cả một bước có duyệt)."""
    return {"by": by, "title": title, "subtitle": subtitle, **({"move": move} if move else {})}


def calendar_view(date: str, title: str, subtitle: str = "", *, overdue: bool = True) -> dict[str, Any]:
    """Lịch theo cột ngày ``date`` (hạn, hẹn gọi, mốc) — các dòng xếp theo ngày. ``overdue=False``: ngày đã qua KHÔNG phải quá hạn (lịch sự kiện:
    việc hôm qua đã diễn ra, không phải trễ) — app hiện ngày đã qua như thường. Chỉ thêm trường khi tắt (trường mới — app cũ bỏ qua)."""
    return {"date": date, "title": title, "subtitle": subtitle, **({} if overdue else {"overdue": False})}


def field(label: str, value: Any, fmt: str = "text", *, needs: Needs = None, open: dict[str, Any] | None = None,
          action: dict[str, Any] | None = None, placeholder: str = "") -> dict[str, Any]:
    """Một dòng nhãn – giá trị của trang chi tiết. Ô BẤM ĐƯỢC (ADR 0251, chỉ THÊM trường): ``open`` = {query, input, title?} mở trang khác (vd "Thuộc: Giao
    dịch …" mở hồ sơ); ``action`` = nút (``act``) chạy khi chạm ô (vd ô thiếu ⇒ form điền đúng ô đó). ``placeholder``: chữ hiện khi giá trị trống — ô trống có
    ``action`` vẫn hiện (chỗ trống để điền). Cửa ra bỏ ``open`` / ``action`` người xem không chạy được (``executor.visible_actions``)."""
    out: dict[str, Any] = {"label": label, "value": value, "format": fmt}
    if open:
        out["open"] = open
    if action:
        out["action"] = action
    if placeholder:
        out["placeholder"] = placeholder
    return gated(out, needs)


def section(title: str, fields: list[dict[str, Any]] | None = None, text: str | None = None,
            table: dict[str, Any] | None = None, *, needs: Needs = None) -> dict[str, Any]:
    """``table``: {columns: [chữ], rows: [[chữ]], total?: [chữ]} — bảng trong văn bản (vd bảng phí của công văn), app vẽ bảng thật."""
    return gated({"title": title, "fields": fields or [], "text": text, **({"table": table} if table else {})}, needs)


def detail(title: str, sections: list[dict[str, Any]], subtitle: str = "") -> dict[str, Any]:
    return {"kind": "detail", "title": title, "subtitle": subtitle, "sections": sections}


# ── THẺ KHỐI (kind "card") — học từ CardSpec của barista_bds: một trang ghép từ khối có kiểu, server tính hết số, app chỉ vẽ.
#
#   {"kind": "card", "title", "subtitle", "icon", "blocks": [...], "actions": [hành động], "open": [{title, query, input}],
#    "ask"?: {title, prompt}}
#   Khối (mỗi khối có "type"; app gặp type lạ thì BỎ QUA, không vỡ thẻ):
#     kpis     {items: [kpi]}
#     steps    {title, steps: [{label, state: done|current|stopped|todo, at?, note?, owner?}]}      — hành trình
#     stages   {title, stages: [{key, name, owner, state: done|current|todo, items: [{label, status, required, hidden,
#               value?, note?, at?}]}]}                                                           — hồ sơ theo chặng
#     callout  {tone: ok|warn|bad|accent|muted, title, text, facts?: [str], draft?: str, actions?: [hành động]}
#     finding  {tone, title, badge?, text, facts?: [str], evidence: [bằng chứng], actions?: [hành động]}   — kết luận có BẰNG CHỨNG mở được:
#              bằng chứng {label, quote, value?, page?, document_id?, scan?, filename?} — file gốc .pdf ⇒ app mở đúng trang, tô câu nguyên văn;
#              tài liệu khác (Word, chữ dán) ⇒ mở trang tài liệu
#     list     {title?, items: [{title, subtitle?, tone?, badge?, open?: {query, input}, ask?}]}
#     text     {title?, markdown}
#     table    {page: <trang bảng>}
#     chart    {title, kind: bar|hbar|line|area|donut|funnel|combo, unit: vnd|count|pct|text, labels: [str], note?, unit_right?,
#               series: [{name, values: [số | null], tone?: blue|green|orange|red|purple|teal, as?: bar|line, axis?: left|right}]}   — null = không đo được
#               (không vẽ, KHÔNG thành 0). ``combo`` (0166): cột + đường trên một biểu đồ; mỗi chuỗi ``as`` (mặc định bar) và ``axis`` (mặc định left);
#               chuỗi ``axis: right`` đo bằng ``unit_right`` (vd tỉ lệ % trên trục phải). Client cũ không biết ``combo`` ⇒ vẽ tất cả thành cột.
#               ``scatter`` (0173): mỗi nhãn là MỘT điểm, ``series[0].x`` = hoành độ, ``values`` = tung độ; ``treemap``: diện tích ô = ``values`` (một chuỗi). Client chưa vẽ ⇒ cột của ``values``.
#   Biểu đồ có thể kèm ``pick: {param, values: [str]}`` (cùng độ dài ``labels``, 0173): bấm nhãn i ⇒ app đặt bộ lọc ``param`` của màn = ``values[i]`` (bấm lại: bỏ lọc) rồi chạy lại truy vấn.
#   MỌI khối / ô số / cột / chuỗi / mục có thể kèm ``needs_info`` (``needs=`` — quyền theo khối, 1.59): cửa ra đã bỏ phần người xem thiếu quyền; app không cần làm gì.
#   MỌI khối trong thẻ có thể kèm ``w`` (0166): bề rộng 1–12 trên lưới 12 cột. Các khối LIỀN NHAU có ``w`` mà tổng ≤ 12 nằm cùng một hàng (ô số hàng
#   trên, 2–3 biểu đồ cạnh nhau như báo cáo quản trị); không có ``w`` ⇒ chiếm cả hàng như cũ. Màn hẹp (iPhone) xếp dọc. Client cũ bỏ qua ``w`` ⇒ xếp dọc.
#     timeline {title, today, start, end, groups: [{key, title, subtitle?, start?, end?, collapsed?, milestones?: [mốc], items: [dòng]}], milestones?: [mốc],
#               off?: [{from, to, kind: weekend|holiday}], note?} — DÒNG THỜI GIAN (0222), mọi ngày là ISO yyyy-mm-dd, server tính hết; app chỉ vẽ thanh:
#               dòng {id, title, start, end|null, kind: bar|milestone, tone: ok|warn|bad|accent|muted, status?, who?, dates (chữ "12/11 – 20/11"), flag?, after?: [id],
#               open?: {query, input}} — ``end`` null = chưa có hạn (vẽ chấm tại ``start``); ``after`` = mã các dòng phải xong trước (mũi tên); mốc {on, title, tone}.
#               Client chưa vẽ thanh ⇒ danh sách theo nhóm: ``title`` + ``dates`` + ``who`` + ``status``. Cũng là ``views.timeline`` của bảng (màn Kế hoạch).
#     calendar {title, view: day|week|agenda, start, end, today, now, tz, hours: [từ giờ, đến giờ], days: [{on, label, off?}], lanes: [{key, title,
#               subtitle?, me?, open?}], kinds: [{key, label, tone}], items: [mục], nav: {param_view, param_on, prev, next, views: [{key, title}]},
#               create?: {creators: [cách tạo], understand?: truy vấn, lane_field?}, empty, note?} — LỊCH kiểu app lịch (0240): mọi ngày giờ ISO theo giờ
#               công ty, server tính hết (ai ở làn nào, màu, ai kéo được gì); app chỉ vẽ lưới giờ + xếp mục chồng giờ cạnh nhau. Mục {id, lane, title,
#               subtitle?, start, end, all_day, kind, tone, status, busy, repeats, open?: {query, input, title}, links: [{title, query, input}],
#               move?: {command, preset, fields: {day, start, end?}}, cancel?: {command, preset}, organizer?, people?: [{name, rsvp, label, organizer}],
#               respond?: {command, preset, param, current, options}} — có ``move`` ⇒ kéo được (thả ⇒ chạy lệnh với giờ mới, server kiểm lại); ``respond`` ⇒
#               nút trả lời lời mời. ``create.invite``: {options: truy vấn danh mục người, groups: [{title, emails}]} — ô "Mời" + gợi ý cả đội.
#               ``nav.unit``: {param, current, options} — chọn đơn vị (chi nhánh / phòng).
#               Cách tạo {key, label, kind, tone, command, preset, fields: {day, start, end?, minutes?, title?, attendee?}, subject?: {field, label,
#               search, required}, place?: tên trường địa điểm} — bấm / kéo ô giờ trống ⇒ hộp nhỏ ⇒ lệnh đó. ``nav``: bấm Hôm nay / ‹ › / chế độ ⇒ đặt
#               bộ lọc của màn rồi chạy lại truy vấn. Client chưa vẽ lưới ⇒ danh sách theo ngày (``title`` + giờ).
#     board    {title, columns: [...], move}   thread {…}   (xem flow.py / talk.py)
#     choices  {title, questions: [{key, question, options: [str], multi}], submit, prefix}   — câu hỏi TRƯỚC khi làm (0098): người chọn rồi bấm
#              gửi ⇒ app ghép thành MỘT tin nhắn "prefix\n- câu hỏi: lựa chọn" vào chat
#   Thẻ có thể kèm ``tabs: {param, items: [{key, title}], current}`` (app tự ghép nhiều trang — 0098): app vẽ thanh tab, bấm tab = chạy lại truy
#   vấn của màn với ``param`` = key. Bảng trong thẻ có thể kèm ``row_actions`` + ``open`` như ScreenSpec (nút của dòng, mở chi tiết). Cột có thể
#   kèm ``order: "asc"`` (trục thời gian — tháng).
#   Hành động: cùng dạng với ``actions`` của trang chi tiết ({command, title, style, bind, preset, when, importable}).

def card(title: str, blocks: list[dict[str, Any]], *, subtitle: str = "", icon: str = "", actions: list[dict[str, Any]] | None = None,
         open: list[dict[str, Any]] | None = None, ask: dict[str, Any] | None = None, headline: str = "") -> dict[str, Any]:
    """``headline``: tên thẻ DÀNH RIÊNG cho người xem (vd "A gọi cho bạn") — thẻ sống trong luồng trao đổi (0089) hiện nó thay cho tên lưu lúc chia sẻ,
    vì tên lưu là sự thật trung tính (không theo người xem). Trống = không khai, app giữ tên đã lưu."""
    page: dict[str, Any] = {"kind": "card", "title": title, "subtitle": subtitle, "icon": icon, "blocks": blocks,
                            "actions": actions or [], "open": open or []}
    if headline:
        page["headline"] = headline
    if ask:
        page["ask"] = ask
    return page


# ── Thẻ gọn (0094 đợt 2): CHÍNH trang đó, server cắt bớt — app vẽ bằng bộ vẽ trang sẵn có, nút trên dòng / khối giữ nguyên ──
COMPACT_ROWS = 3   # thẻ gọn để liếc + làm việc gấp nhất; cột hẹp / iPhone — nhiều hơn thì "Mở"
COMPACT_COLUMNS = 3
COMPACT_KPIS = 3
_ITEM_BLOCKS = ("list", "callout", "finding", "board", "table", "tiles", "steps")


def compact(page: Any, rows: int = COMPACT_ROWS) -> dict[str, Any] | None:
    """Bản xem nhanh của một trang (bảng / thẻ): ≤ ``rows`` dòng (theo đúng thứ tự module đã xếp — dòng gấp lên trước), ≤ 3 cột (cột nhãn đầu
    tiên + 3 cột thường đầu); ô số chỉ giữ (≤ 3) khi KHÔNG có dòng nào — có dòng thì câu tín hiệu / tiêu đề đã nói số; khối nặng (đoạn
    văn, biểu đồ) bỏ — riêng thẻ KHÔNG có dòng nào thì MỘT biểu đồ đầu ở lại (0220 lát 5). ``compact = {shown, total}``: ``total`` chỉ có khi BIẾT tổng thật
    (bảng chưa chạm trần / khối đã tính đủ) — không biết thì app ghi "N dòng đầu", không đoán. Trang chi tiết / lạ ⇒ None (chỉ có nút Mở)."""
    if not isinstance(page, dict):
        return None
    if page.get("kind") == "table":
        all_rows = page.get("rows") or []
        cols = page.get("columns") or []
        badge = [c for c in cols if c.get("format") == "badge"][:1]
        keep = {c["key"] for c in badge + [c for c in cols if c.get("format") != "badge"][:COMPACT_COLUMNS]}
        out = {**page, "kpis": [] if all_rows else (page.get("kpis") or [])[:COMPACT_KPIS], "columns": [c for c in cols if c["key"] in keep],
               "rows": all_rows[:rows], "compact": {"shown": min(rows, len(all_rows)), "total": page.get("total")}}
        out.pop("views", None)   # Kanban / lịch là góc nhìn của màn đầy đủ
        return out
    if page.get("kind") != "card":
        return None
    budget, total, blocks = rows, 0, []
    for b in page.get("blocks") or []:
        t = b.get("type")
        if t == "kpis":
            blocks.append({**b, "items": (b.get("items") or [])[:COMPACT_KPIS]})
        elif t == "choices":   # câu hỏi trước khi làm (0098): giữ nguyên — người phải trả lời được ngay trong chat
            blocks.append(b)
        elif t in ("callout", "finding"):
            total += 1
            if budget > 0:
                blocks.append(b)
                budget -= 1
        elif t == "board":
            cols = []
            for c in b.get("columns") or []:
                items = c.get("items") or []
                total += len(items)
                cols.append({**c, "items": items[:max(budget, 0)]})
                budget -= min(len(items), max(budget, 0))
            blocks.append({**b, "columns": [c for c in cols if c["items"]]})
        elif t == "table":
            inner = compact(b.get("page"), max(budget, 0))
            n = len((b.get("page") or {}).get("rows") or [])
            total += n
            if inner and budget > 0:
                blocks.append({**b, "page": inner})
            budget -= min(n, max(budget, 0))
        elif t == "chart" and not any(x.get("type") == "chart" for x in blocks):   # MỘT biểu đồ (đầu tiên) ở lại bản gọn — câu trả lời về xu hướng là cái hình (0220 lát 5)
            blocks.append(b)
        elif t in ("list", "tiles", "steps"):
            key = "steps" if t == "steps" else "items"
            items = b.get(key) or []
            total += len(items)
            if budget > 0:
                blocks.append({**b, key: items[:budget]})
            budget -= min(len(items), max(budget, 0))
    if total:   # có dòng ⇒ bỏ ô số + biểu đồ (gọn); không có dòng nào ⇒ ô số / MỘT biểu đồ là nội dung (thẻ trả lời bằng khối — 0220 lát 5)
        blocks = [b for b in blocks if b.get("type") not in ("kpis", "chart")]
    # tiêu đề / nút thanh công cụ / "hỏi trợ lý" của trang để ở bản đủ — thẻ gọn chỉ giữ nội dung + nút trong khối (app không phải sửa trang)
    out = {**page, "title": "", "subtitle": "", "actions": [], "open": [], "blocks": blocks,
           "compact": {"shown": rows - max(budget, 0) if total else 0, "total": total}}
    if not (page.get("ask") or {}).get("mode"):   # nút chuyển chế độ (vd "Mở trình tạo app" — 0098) là nội dung chính của thẻ ⇒ giữ
        out.pop("ask", None)
    return out


def act(command: str, title: str, preset: dict[str, Any] | None = None, style: str = "default", *,
        values: dict[str, Any] | None = None, import_only: bool = False, basis: str = "") -> dict[str, Any]:
    """Nút gọi một lệnh (form mở sẵn các trường ``preset`` — cố định, ẩn). ``values``: ô ĐIỀN SẴN mà người vẫn thấy + sửa được (vd tiêu đề việc lấy
    từ tin nhắn) — như phần trợ lý soạn sẵn trên thẻ đề xuất. Quyền / duyệt vẫn do executor — app chỉ vẽ."""
    out = {"command": command, "title": title, "style": style, "bind": {}, "preset": preset or {}, "when": {}, "importable": False}
    if import_only:
        out["import_only"] = True   # chỉ THÊM trường: nút mở thẳng màn "Nhập từ Excel" (lệnh nhập cả bảng — không có form nhập một)
    if values:
        out["values"] = values   # chỉ THÊM trường (bất biến 7): app cũ bỏ qua ⇒ form trống như trước
    if basis:
        # CĂN CỨ người xem đang thấy (0071 / 0251 — ``executor.basis_token``): app gửi lại nguyên chữ ở tiêu đề ``X-Expect-Basis`` khi bấm ⇒ số đã đổi
        # giữa lúc xem và lúc bấm thì lệnh trả ``conflict``, không chạy. App cũ không gửi ⇒ lệnh chạy như trước (không so)
        out["basis"] = basis
    return out


def b_kpis(items: list[dict[str, Any]], *, needs: Needs = None) -> dict[str, Any]:
    return gated({"type": "kpis", "items": items}, needs)


def b_steps(steps: list[dict[str, Any]], title: str = "", *, needs: Needs = None) -> dict[str, Any]:
    return gated({"type": "steps", "title": title, "steps": steps}, needs)


def b_stages(stages: list[dict[str, Any]], title: str = "", *, needs: Needs = None) -> dict[str, Any]:
    return gated({"type": "stages", "title": title, "stages": stages}, needs)


def b_callout(tone: str, title: str, text: str = "", *, facts: list[str] | None = None, draft: str = "",
              actions: list[dict[str, Any]] | None = None, needs: Needs = None) -> dict[str, Any]:
    return gated({"type": "callout", "tone": tone, "title": title, "text": text, "facts": facts or [], "draft": draft,
                  "actions": actions or []}, needs)


def b_finding(tone: str, title: str, text: str, evidence: list[dict[str, Any]], *, badge: str = "", facts: list[str] | None = None,
              actions: list[dict[str, Any]] | None = None, needs: Needs = None) -> dict[str, Any]:
    """Một phát hiện (sai biệt, điều khoản lệch…) + bằng chứng từng phía. Bằng chứng: ``evidence(...)``. App lạ khối này ⇒ bỏ qua — trang nên
    giữ thêm bảng tổng hợp cho client chưa vẽ được."""
    return gated({"type": "finding", "tone": tone, "title": title, "badge": badge, "text": text, "facts": facts or [], "evidence": evidence,
                  "actions": actions or []}, needs)


def evidence(label: str, quote: str, *, value: Any = None, page: int | None = None, document_id: Any = None, scan: bool = False,
             filename: str = "", note: str = "") -> dict[str, Any]:
    """``note``: chữ hiện thay câu nguyên văn khi không có câu (trống ⇒ app ghi "không có trên chứng từ"). ``scan``: chữ do model chép từ ảnh — app không tô được câu trên trang, chỉ mở đúng trang để người đối chiếu bằng mắt.
    ``filename``: file gốc của tài liệu — ``.pdf`` ⇒ app mở trình xem PDF đúng trang; khác / trống ⇒ mở trang tài liệu (chữ), không hứa đúng trang."""
    return {"label": label, "quote": quote, "value": None if value is None else str(value), "page": page,
            "document_id": None if document_id is None else str(document_id), "scan": scan, "filename": filename, "note": note}


def b_picker(title: str, items: list[dict[str, Any]], *, current: str, custom: str = "", note: str = "", pending: str = "",
             needs: Needs = None) -> dict[str, Any]:
    """Chọn MỘT trong vài mức đặt sẵn (vd mức tự chủ của trợ lý — 0144): mục ``{key, title, text, facts?: [str], action: act(...)}``; bấm = chạy
    lệnh của mục (quyền / duyệt do executor). ``current``: khoá mục đang khớp dữ liệu, "" = không khớp mục nào ⇒ app hiện ``custom`` (vd "Tuỳ chỉnh").
    ``pending``: câu đang chờ (vd phiếu chờ chủ tài khoản duyệt). Server tính mục nào đang áp — app không suy."""
    return gated({"type": "picker", "title": title, "items": items, "current": current, "custom": custom, "note": note, "pending": pending}, needs)


def b_image(src: str, *, caption: str = "", alt: str = "", note: str = "", needs: Needs = None) -> dict[str, Any]:
    """ẢNH trong thẻ (api 1.73): ``src`` = link ký ngắn hạn của ``files.signed_url(inline=True)`` (ảnh / PDF) hoặc đường /v1/… của core; bấm ⇒ xem to. ``note`` hiện thay
    ảnh khi chưa có ("Chưa có ảnh phiếu"). Mảnh ghép nền — ghép với ``b_split`` để bố cục "nguồn bên trái, dòng đọc bên phải"; app chưa biết khối ⇒ bỏ qua."""
    return gated({"type": "image", "src": src, "caption": caption, "alt": alt or caption, "note": note}, needs)


def b_split(left: list[dict[str, Any]], right: list[dict[str, Any]], *, ratio: str = "1:1", needs: Needs = None) -> dict[str, Any]:
    """CHIA HAI CỘT (api 1.73): mỗi cột là danh sách khối bất kỳ (kể cả ``b_split`` lồng nhau). ``ratio`` "1:1" · "2:3" · "1:2"… (trái:phải); màn hẹp ⇒ xếp dọc,
    cột trái trước. Mảnh ghép bố cục chung — gói tự ghép kiểu nhìn riêng của nghiệp vụ mình (ảnh + dòng đọc, hai bản so sánh, hồ sơ + lịch sử…) mà không chờ lõi thêm khối mới."""
    if not re.fullmatch(r"[1-9]:[1-9]", ratio):
        raise ValueError(f"ratio phải dạng 'a:b' (một chữ số), nhận {ratio!r}")
    return gated({"type": "split", "left": left, "right": right, "ratio": ratio}, needs)


def b_list(items: list[dict[str, Any]], title: str = "", *, needs: Needs = None) -> dict[str, Any]:
    """Mục của danh sách có thể tự khai ``needs`` (``gated(mục, …)``) — vd gợi ý dựa trên doanh thu chỉ cho người thấy doanh thu."""
    return gated({"type": "list", "title": title, "items": items}, needs)


def b_tiles(items: list[dict[str, Any]], title: str = "", *, needs: Needs = None) -> dict[str, Any]:
    """Lưới thẻ: ô {title, subtitle?, badge?, tone?, module? (biểu tượng + màu theo phân hệ), open?}."""
    return gated({"type": "tiles", "title": title, "items": items}, needs)


def b_text(markdown: str, title: str = "", *, needs: Needs = None) -> dict[str, Any]:
    return gated({"type": "text", "title": title, "markdown": markdown}, needs)


CHART_KINDS = ("bar", "hbar", "line", "area", "donut", "funnel", "combo", "scatter", "treemap")
SERIES_AS = ("bar", "line")
SERIES_AXIS = ("left", "right")
GRID_COLUMNS = 12


def b_chart(kind: str, labels: list[str], series: list[dict[str, Any]], *, title: str = "", unit: str = "count", note: str = "",
            unit_right: str = "", pick: tuple[str, list[str]] | None = None, needs: Needs = None) -> dict[str, Any]:
    """Biểu đồ do server tính số, app chỉ vẽ. ``series[i]["values"]`` cùng độ dài ``labels``; ``None`` = không đo được (app để trống).
    ``combo`` (0166): chuỗi khai ``as`` (bar | line) và ``axis`` (left | right); trục phải đo bằng ``unit_right`` (trống ⇒ như ``unit``)."""
    assert kind in CHART_KINDS, kind
    for sr in series:
        assert len(sr["values"]) == len(labels), (title, sr["name"])
        assert sr.get("as", "bar") in SERIES_AS and sr.get("axis", "left") in SERIES_AXIS, (title, sr["name"])
    assert kind == "combo" or not any(k in sr for sr in series for k in ("as", "axis")), f"{title}: as / axis chỉ cho combo"
    assert (kind == "scatter") == any("x" in sr for sr in series) and all(len(sr["x"]) == len(labels) for sr in series if "x" in sr), f"{title}: x chỉ cho scatter, cùng độ dài nhãn"
    out = {"type": "chart", "title": title, "kind": kind, "unit": unit, "labels": labels, "series": series, "note": note}
    if unit_right:
        assert kind in ("combo", "scatter"), f"{title}: unit_right chỉ cho combo (trục phải) / scatter (đơn vị hoành độ)"
        out["unit_right"] = unit_right
    if pick:   # LỌC CHÉO kiểu BI (0173): bấm một cột / miếng ⇒ app đặt bộ lọc ``param`` của MÀN = ``values[i]`` (bấm lại ⇒ bỏ), truy vấn chạy lại; app cũ bỏ qua
        param, values = pick
        assert len(values) == len(labels) and param, (title, param)
        out["pick"] = {"param": param, "values": values}
    return gated(out, needs)


def line(name: str, values: list[float | None], tone: str = "blue", *, as_: str = "", axis: str = "", x: list[float | None] | None = None,
         needs: Needs = None) -> dict[str, Any]:
    """``needs``: chuỗi của một nhóm thông tin (vd doanh thu cạnh số giao dịch) — người thiếu quyền nhận biểu đồ không có chuỗi này; biểu đồ hết chuỗi thì bỏ."""
    out: dict[str, Any] = {"name": name, "values": values, "tone": tone}
    if n := needs_of(needs):
        out["needs_info"] = n
    if x is not None:   # scatter (0173): hoành độ từng điểm, cùng độ dài ``labels`` — ``values`` là tung độ
        out["x"] = x
    if as_:
        out["as"] = as_
    if axis:
        out["axis"] = axis
    return out


def span(block: dict[str, Any], w: int) -> dict[str, Any]:
    """Đặt bề rộng ``w`` (1–12 trên lưới 12 cột, 0166) cho một khối của thẻ: khối liền nhau có ``w`` mà tổng ≤ 12 nằm cùng một hàng."""
    assert 1 <= w <= GRID_COLUMNS, w
    return {**block, "w": w}


def tl_item(id: str, title: str, start: date, end: date | None, *, kind: str = "bar", tone: str = "accent", status: str = "", who: str = "",
            dates: str = "", flag: str = "", open: dict[str, Any] | None = None, after: list[str] | None = None) -> dict[str, Any]:
    """Một dòng của ``b_timeline``. ``kind="milestone"``: hình thoi tại ``end`` (``start`` = ``end``). ``end=None``: việc chưa có hạn. ``flag``: câu cảnh báo
    code tính (vd "bắt đầu trước khi việc trước xong") — app chỉ hiện."""
    assert kind in ("bar", "milestone"), kind
    out: dict[str, Any] = {"id": id, "title": title, "start": start.isoformat(), "end": end.isoformat() if end else None, "kind": kind, "tone": tone,
                           "status": status, "who": who, "dates": dates, "after": after or []}
    if flag:
        out["flag"] = flag
    if open:
        out["open"] = open
    return out


def tl_group(key: str, title: str, items: list[dict[str, Any]], *, subtitle: str = "", start: date | None = None, end: date | None = None,
             milestones: list[dict[str, Any]] | None = None, collapsed: bool = False, open: dict[str, Any] | None = None) -> dict[str, Any]:
    """Một nhóm dòng (giai đoạn / người / kế hoạch). ``start`` / ``end``: thanh TÓM TẮT cả nhóm vẽ ở hàng tiêu đề (vd cả kế hoạch); ``collapsed``: gấp sẵn;
    ``open``: bấm tiêu đề nhóm ⇒ mở trang này (vd thẻ kế hoạch)."""
    out: dict[str, Any] = {"key": key, "title": title, "subtitle": subtitle, "items": items}
    if start and end:
        out["start"], out["end"] = start.isoformat(), end.isoformat()
    if milestones:
        out["milestones"] = milestones
    if collapsed:
        out["collapsed"] = True
    if open:
        out["open"] = open
    return out


def tl_milestone(on: date, title: str, tone: str = "accent") -> dict[str, Any]:
    return {"on": on.isoformat(), "title": title, "tone": tone}


def b_timeline(groups: list[dict[str, Any]], *, today: date, start: date, end: date, title: str = "", milestones: list[dict[str, Any]] | None = None,
               off: list[tuple[date, str]] | None = None, note: str = "", needs: Needs = None) -> dict[str, Any]:
    """Khối DÒNG THỜI GIAN (0222, mô tả ở đầu mục THẺ KHỐI). ``start`` / ``end`` = khoảng ngày cần vẽ (server chọn: phủ mọi thanh + hôm nay);
    ``off`` = ngày nghỉ (lịch làm việc của công ty — cuối tuần / lễ) gộp thành đoạn liên tục để app tô bóng."""
    spans: list[dict[str, str]] = []
    for day, kind in sorted(off or []):
        if spans and spans[-1]["kind"] == kind and date.fromisoformat(spans[-1]["to"]) == day - timedelta(days=1):
            spans[-1]["to"] = day.isoformat()
        else:
            spans.append({"from": day.isoformat(), "to": day.isoformat(), "kind": kind})
    return gated({"type": "timeline", "title": title, "today": today.isoformat(), "start": start.isoformat(), "end": end.isoformat(), "groups": groups,
                  "milestones": milestones or [], "off": spans, "note": note}, needs)


CAL_VIEWS = ("day", "week", "agenda")
CAL_TONES = ("blue", "green", "orange", "red", "purple", "teal", "pink", "indigo", "gray")


def cal_item(id: str, title: str, start: str, end: str, *, lane: str = "", subtitle: str = "", all_day: bool = False, kind: str = "",
             tone: str = "blue", status: str = "booked", busy: bool = False, repeats: bool = False, open: dict[str, Any] | None = None,
             links: list[dict[str, Any]] | None = None, move: dict[str, Any] | None = None, cancel: dict[str, Any] | None = None,
             organizer: str = "", people: list[dict[str, Any]] | None = None, respond: dict[str, Any] | None = None) -> dict[str, Any]:
    """Một mục của ``b_calendar`` (0240). ``start`` / ``end``: ISO có múi giờ công ty (cả ngày: ``start`` = ngày đầu, ``end`` = ngày cuối, yyyy-mm-dd).
    ``move``: {command, preset, fields} — server đã quyết người xem dời được mục này bằng lệnh nào (không có ⇒ không kéo được); ``cancel``: {command, preset}
    — huỷ được. ``organizer`` + ``people`` [{name, rsvp, label, organizer}]: người tổ chức và người được mời (câu trả lời); ``respond``: {command, preset,
    param, current, options: [{key, title}]} — người xem được mời ⇒ nút Đồng ý / Có thể / Từ chối."""
    assert tone in CAL_TONES, tone
    out: dict[str, Any] = {"id": id, "lane": lane, "title": title, "subtitle": subtitle, "start": start, "end": end, "all_day": all_day, "kind": kind,
                           "tone": tone, "status": status, "busy": busy, "repeats": repeats, "links": links or []}
    if open:
        out["open"] = open
    if move:
        out["move"] = move
    if cancel:
        out["cancel"] = cancel
    if organizer:
        out["organizer"] = organizer
    if people:
        out["people"] = people
    if respond:
        out["respond"] = respond
    return out


def b_calendar(view: str, *, start: date, end: date, today: date, now: str, tz: str, days: list[dict[str, Any]], items: list[dict[str, Any]],
               lanes: list[dict[str, Any]] | None = None, kinds: list[dict[str, Any]] | None = None, hours: tuple[int, int] = (7, 20),
               nav: dict[str, Any] | None = None, create: dict[str, Any] | None = None, title: str = "", empty: str = "", note: str = "") -> dict[str, Any]:
    """Khối LỊCH (0240, mô tả ở đầu mục THẺ KHỐI): ``start`` / ``end`` = khoảng ngày đang xem (cả hai đầu); ``hours`` = khung giờ lưới (server nới để
    phủ mọi mục trong ngày); ``lanes`` = người cạnh nhau (chế độ Ngày) theo phạm vi người xem được nhìn."""
    assert view in CAL_VIEWS, view
    lo, hi = hours
    assert 0 <= lo < hi <= 24, hours
    return {"type": "calendar", "title": title, "view": view, "start": start.isoformat(), "end": end.isoformat(), "today": today.isoformat(), "now": now,
            "tz": tz, "hours": [lo, hi], "days": days, "lanes": lanes or [], "kinds": kinds or [], "items": items, "nav": nav or {},
            "create": create, "empty": empty, "note": note}


def b_table(page: dict[str, Any], *, needs: Needs = None) -> dict[str, Any]:
    return gated({"type": "table", "page": page}, needs)


def b_later(query: str, input: dict[str, Any] | None = None, *, title: str = "", needs: Needs = None) -> dict[str, Any]:
    """Khối TẢI SAU (18/11/2026): chỗ của các khối do truy vấn ``query`` trả (thẻ ``card``) — để phần nặng không giữ cả màn. Thẻ hiện phần nhanh (ô số,
    biểu đồ chính) trước; app báo đỡ được (web: ``?later=1``) thì nhận khối này nguyên dạng và tự tải từng khối theo THỨ TỰ trong thẻ (khối đầu tải
    trước — thứ tự là ưu tiên do server khai); app khác / trợ lý / xuất Excel / thẻ gọn ⇒ executor chạy ``query`` ngay và thay khối này bằng các khối
    của nó (ra đúng thẻ như khi không tách). ``query`` là truy vấn bình thường, tự kiểm quyền; không có quyền / gói tắt ⇒ phần đó không có. Chỉ đặt
    phần NGƯỜI XEM cần, không phải số mà mã khác đọc lại qua ``ctx.call`` (gọi lồng nhận nguyên khối này, không tự mở)."""
    return gated({"type": "later", "query": query, "input": input or {}, "title": title}, needs)


# ── PHẦN TẢI SAU trong CÙNG một truy vấn (18/11/2026) — cơ chế chung cho màn nhiều biểu đồ / bảng: ô số trước, phần nặng tải dần ──
#
# Truy vấn viết như thường, bọc mỗi phần nặng (một hay vài biểu đồ / bảng cần đọc thêm dữ liệu) bằng ``await ui.part(khoá, hàm_dựng, title=)``:
#
#     blocks = [ui.b_kpis(...)]                                        # phần nhanh — luôn chạy
#     blocks += await ui.part("trend", lambda: _trend(ctx, t), title="Xu hướng 6 tháng")
#     blocks += await ui.part("funnel", lambda: _funnel(ctx, a, b), title="Phễu lead")
#
# * Bên gọi không tự tải (app macOS / iOS, trợ lý, thẻ gọn, Excel, gọi lồng): ``part`` chạy ``hàm_dựng`` ngay ⇒ thẻ y như khi không tách.
# * Web (``?later=1``): ``part`` KHÔNG chạy hàm, trả MỘT khối ``later`` {query, input, part, title}; web tải từng phần theo thứ tự khối (thứ tự =
#   ưu tiên do server khai) bằng ``?part=<khoá>``: truy vấn chạy lại phần nhanh, BỎ QUA các phần khác, dựng đúng phần đó rồi dừng (không dựng phần sau).
# Thêm biểu đồ mới = thêm một ``ui.part`` (hoặc thêm vào hàm dựng của phần có sẵn) — không đụng app. Phần nhanh nên là thứ rẻ (nó chạy lại mỗi phần).


@dataclass
class _Parts:
    query: str
    input: dict[str, Any]
    want: str | None = None          # None = lượt đầu (trả chỗ chờ); khoá = chỉ dựng phần này
    got: list[dict[str, Any]] | None = None


_PARTS: ContextVar[_Parts | None] = ContextVar("barista_ui_parts", default=None)


class PartDone(Exception):
    """Tín hiệu (không phải lỗi): phần được hỏi đã dựng — dừng truy vấn, phần sau không chạy; executor bắt."""


async def part(key: str, make: Callable[[], Awaitable[list[dict[str, Any]]]], *, title: str = "") -> list[dict[str, Any]]:
    """Một phần tải sau của thẻ — xem chú thích đầu mục. Trả danh sách khối để nối vào ``blocks``."""
    st = _PARTS.get()
    if st is None:
        return await make()
    if st.want is None:
        return [{"type": "later", "query": st.query, "input": st.input, "part": key, "title": title}]
    if st.want != key or st.got is not None:
        return []
    token = _PARTS.set(None)   # bên trong phần: gọi lồng / phần lồng chạy thẳng
    try:
        st.got = await make()
    finally:
        _PARTS.reset(token)
    raise PartDone(key)


# ── TRANG của bảng phía server (18/11/2026): app xin ``limit`` / ``offset`` ⇒ chỉ nhận một trang + ``paging`` {offset, limit, total, complete} ──
# ``total`` = số dòng của CẢ tập lọc (ô số vẫn tính trên cả tập như trước); ``complete`` = False khi chính truy vấn đã chạm trần LIMIT của nó.
# Mặc định executor cắt trang SAU khi truy vấn chạy (mọi bảng đều có trang, không sửa module). Truy vấn nặng tự đẩy trang xuống SQL: đọc
# ``ui.paging()`` (None = bên gọi không xin trang), ``LIMIT/OFFSET`` + ``count(*) OVER ()``, rồi ``ui.table(..., paged_total=N)``.
# App không xin trang (macOS / iOS, Excel, thẻ gọn, trợ lý) nhận đủ dòng như cũ. Góc nhìn Kanban / lịch / dòng thời gian cần đủ dòng ⇒ web không xin
# trang khi đang ở các góc nhìn đó (``views`` vẫn đi kèm trang để app biết có góc nhìn nào).

PAGE_MAX = 1000


@dataclass(frozen=True)
class Paging:
    offset: int = 0
    limit: int = 300


_PAGING: ContextVar[Paging | None] = ContextVar("barista_ui_paging", default=None)


def paging() -> Paging | None:
    return _PAGING.get()


def slice_page(out: Any, pg: Paging) -> Any:
    """Cắt một trang của trang bảng (sau khi truy vấn chạy) — trang thẻ / chi tiết hoặc bảng đã tự chia trang ở SQL thì giữ nguyên."""
    if not isinstance(out, dict) or out.get("kind") != "table" or "paging" in out:
        return out
    rows = out.get("rows") or []
    return {**out, "rows": rows[pg.offset:pg.offset + pg.limit],
            "paging": {"offset": pg.offset, "limit": pg.limit, "total": len(rows), "complete": "total" in out}}


def options(rows: list[dict[str, Any]], value: str = "value", label: str = "label", hint: str | None = None) -> list[dict[str, str]]:
    """Dòng của ô chọn ``[{value, label, hint?}]``. ``hint`` = tên cột của DÒNG PHỤ mờ dưới nhãn (vd SĐT, dự án, vai) — giúp phân biệt hai dòng cùng nhãn (1.61)."""
    out = [{"value": str(r[value]), "label": str(r[label])} for r in rows]
    if hint:
        for o, r in zip(out, rows, strict=True):
            if r.get(hint) not in (None, ""):
                o["hint"] = str(r[hint])
    return out


def narrow_options(rows: Any, q: str = "", limit: int = 0) -> Any:
    """Thu hẹp danh sách chọn theo chữ người gõ ``q`` (mọi từ có trong nhãn + dòng phụ + giá trị, bỏ dấu, không phân biệt hoa thường; nhãn bắt đầu bằng
    từ khoá xếp trước) và cắt ``limit`` dòng đầu (0 = không cắt). Cửa ``/v1/options`` dùng cho truy vấn chọn KHÔNG tự khai ``q`` (1.61)."""
    if not isinstance(rows, list):
        return rows
    words = fold(q).split()
    if words:
        first = fold(q.strip())
        ranked: list[tuple[int, int, Any]] = []
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                continue
            hay = fold(" ".join(str(r.get(k) or "") for k in ("label", "hint", "value")))
            if not all(w in hay for w in words):
                continue
            rank = 0 if fold(str(r.get("label") or "")).startswith(first) else 1 if all(
                hay.startswith(w) or f" {w}" in hay or f"-{w}" in hay for w in words) else 2   # cả cụm ở đầu nhãn → mọi từ khớp đầu từ → khớp giữa từ
            ranked.append((rank, i, r))
        rows = [r for _, _, r in sorted(ranked, key=lambda t: (t[0], t[1]))]
    return rows[:limit] if limit > 0 else rows


# ── Excel ──

_THIN = Side(style="thin", color="999999")
_BOX = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_XLSX_FMT = {"money": "#,##0", "number": "#,##0", "percent": "0.00%", "date": "dd/mm/yyyy", "datetime": "dd/mm/yyyy hh:mm"}


def _cell_value(c: dict[str, Any], v: Any, country: str | None = None) -> Any:
    if v is None:
        return None
    fmt = c["format"]
    if fmt == "phone":   # chữ như màn hình (số nội địa liền ``0898654936`` / số nước khác ``+…``); ô là CHỮ để Excel không nuốt số 0 đầu
        return phone_display(str(v), country)
    if fmt == "badge":
        return c.get("labels", {}).get(str(v), v)
    if fmt == "percent":
        return float(v) / 100
    if fmt in ("date", "datetime") and isinstance(v, str):
        from datetime import date, datetime
        try:
            return date.fromisoformat(v) if fmt == "date" else datetime.fromisoformat(v).replace(tzinfo=None)
        except ValueError:
            return v
    return v


def table_xlsx(page: dict[str, Any], country: str | None = None) -> bytes:
    """Bảng → .xlsx (không màu, kẻ ô, tiền là số thật để kế toán cộng trừ được; SĐT là chữ theo quốc gia công ty ``country``)."""
    return tables_xlsx([page], country)


def tables_xlsx(pages: list[dict[str, Any]], country: str | None = None) -> bytes:
    """Nhiều bảng → MỘT .xlsx, mỗi bảng một trang tính (tên trang = tiêu đề bảng) — vd báo cáo token của nhà vận hành (0193)."""
    wb = Workbook()
    first = wb.active
    assert first is not None
    for i, page in enumerate(pages):
        ws = first if i == 0 else wb.create_sheet()
        ws.title = re.sub(r"[\\/*?:\[\]]", "-", page.get("title") or f"Dữ liệu {i + 1}")[:31]
        _sheet(ws, page, country)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sheet(ws: Any, page: dict[str, Any], country: str | None = None) -> None:
    cols = page["columns"]
    for j, c in enumerate(cols, 1):
        cell = ws.cell(row=1, column=j, value=c["label"])
        cell.font = Font(bold=True)
        cell.border = _BOX
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(j)].width = max(12, min(40, len(c["label"]) + 4))
    for i, r in enumerate(page["rows"], 2):
        for j, c in enumerate(cols, 1):
            cell = ws.cell(row=i, column=j, value=_cell_value(c, r.get(c["key"]), country))
            cell.border = _BOX
            if f := _XLSX_FMT.get(c["format"]):
                cell.number_format = f
            if c["format"] == "phone":
                cell.number_format = "@"   # định dạng Văn bản: mở file rồi sửa ô cũng không mất số 0 đầu
    ws.freeze_panes = "A2"
