"""GIẤY TỜ — năng lực chung: lưu bản soạn, cấp số theo loại trong năm, ban hành (khoá nội dung), huỷ, xuất .docx theo thể thức văn bản
hành chính — Nghị định 30/2020/NĐ-CP, Phụ lục I (doanh nghiệp không bắt buộc nhưng dùng làm chuẩn trình bày) — hoặc tài liệu gửi khách.

Lõi chỉ có CÔNG VĂN. Loại giấy của ngành (đề nghị thanh toán phí môi giới, bản tư vấn căn, …) do GÓI soạn: gói lấy số liệu bằng quyền người
soạn, dựng nội dung theo khuôn trình bày, gọi ``papers.save_draft`` (quyết định 0076). Soạn = NHÁP (người + trợ lý); BAN HÀNH = chỉ người.
Mẫu nội dung là GIẢ ĐỊNH ban đầu — luật sư / kế toán của công ty duyệt lại trước khi dùng thật (ARCHITECTURE §8).
"""

import base64
import io
import re
from datetime import date
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.api import Action as A
from barista.api import (
    Ctx,
    Input,
    Invalid,
    Module,
    NoInput,
    NotFound,
    Open,
    Screen,
    SearchIn,
    SearchTerms,
    field,
    files,
    today,
    ui,
    web,
)
from barista.kernel import convert

m = Module("papers", "Giấy tờ", pack="base", resources={"papers": "Giấy tờ & công văn", "papers.templates": "Mẫu văn bản (soạn thảo)"},
           setup=("papers.templates",))

LETTER = "letter"   # công văn — loại duy nhất của lõi; loại khác do gói khai khi soạn (save_draft)
STATUS = {"draft": "Dự thảo", "issued": "Đã ban hành", "void": "Đã huỷ"}
CONTRACT = "contract"   # khuôn của văn bản soạn từ mẫu (0118) — Quốc hiệu, tên văn bản, các mục, bảng, khung ký nhiều bên

# ── Thông tin công ty ──

async def settings(ctx: Ctx) -> dict[str, Any]:
    s = await ctx.one("SELECT * FROM papers.settings")
    return s or {"company_name": "", "short_name": "", "address": "", "tax_code": "", "phone": "", "bank_account": "",
                 "bank_name": "", "city": "Hà Nội", "signer_title": "GIÁM ĐỐC", "signer_name": ""}


@m.query("company", resource="papers", title="Thông tin công ty in trên giấy tờ (cho gói)", internal=True, family="paper_draft",
         why="Lệnh soạn giấy của gói khai consumes(\"paper_draft\") (lệnh soạn khai resource=\"papers\" nên executor đã kiểm quyền soạn; số liệu đọc bằng "
             "quyền người soạn): đọc dòng papers.settings của công ty. Trả company_name, address, tax_code, bank_account, "
             "bank_name — đúng những ô giấy của gói in ra.")
async def company(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Hợp đồng HẸP cho gói soạn giấy: tên, địa chỉ, MST, tài khoản nhận tiền, người ký — đúng những gì in trên giấy."""
    s = await settings(ctx)
    return {k: s[k] for k in ("company_name", "address", "tax_code", "bank_account", "bank_name")}


@m.query("settings_view", resource="papers", title="Thông tin công ty trên giấy tờ")
async def settings_view(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    s = await settings(ctx)
    return ui.detail("Thông tin công ty trên giấy tờ", [ui.section("In trên mọi giấy tờ", [
        ui.field("Tên công ty", s["company_name"] or "(chưa nhập)"), ui.field("Viết tắt (số ký hiệu)", s["short_name"]),
        ui.field("Địa chỉ", s["address"]), ui.field("Mã số thuế", s["tax_code"]), ui.field("Điện thoại", s["phone"]),
        ui.field("Tài khoản nhận tiền", f"{s['bank_account']} — {s['bank_name']}"), ui.field("Địa danh", s["city"]),
        ui.field("Người ký", f"{s['signer_title']} {s['signer_name']}"),
    ])])


class SetSettings(Input):
    company_name: str = field("Tên công ty (in hoa)", example="CÔNG TY CỔ PHẦN SÀN MẪU", max_length=200)
    short_name: str = field("Viết tắt trong số ký hiệu", example="SM", max_length=10)
    address: str = field("Địa chỉ", "", max_length=300)
    tax_code: str = field("Mã số thuế", "", max_length=20)
    phone: str = field("Điện thoại", "", max_length=30)
    bank_account: str = field("Số tài khoản nhận tiền", "", max_length=40)
    bank_name: str = field("Tại ngân hàng", "", max_length=120)
    city: str = field("Địa danh ghi ngày tháng", "Hà Nội", max_length=60)
    signer_title: str = field("Chức vụ người ký", "GIÁM ĐỐC", max_length=60)
    signer_name: str = field("Họ tên người ký", "", max_length=120)


@m.command("set_settings", resource="core.admin", action="manage", title="Sửa thông tin công ty trên giấy tờ")
async def set_settings(ctx: Ctx, inp: SetSettings) -> dict[str, Any]:
    v = inp.model_dump()
    v["company_name"], v["signer_title"], v["short_name"] = v["company_name"].upper(), v["signer_title"].upper(), v["short_name"].upper()
    await ctx.exec(
        """INSERT INTO papers.settings (company_name, short_name, address, tax_code, phone, bank_account, bank_name, city,
                                        signer_title, signer_name)
           VALUES (%(company_name)s, %(short_name)s, %(address)s, %(tax_code)s, %(phone)s, %(bank_account)s, %(bank_name)s,
                   %(city)s, %(signer_title)s, %(signer_name)s)
           ON CONFLICT (tenant_id) DO UPDATE SET company_name = EXCLUDED.company_name, short_name = EXCLUDED.short_name,
               address = EXCLUDED.address, tax_code = EXCLUDED.tax_code, phone = EXCLUDED.phone,
               bank_account = EXCLUDED.bank_account, bank_name = EXCLUDED.bank_name, city = EXCLUDED.city,
               signer_title = EXCLUDED.signer_title, signer_name = EXCLUDED.signer_name""", v)
    return {"ok": True}


# ── Soạn nháp ──

async def _insert(ctx: Ctx, *, kind: str, kind_label: str, title: str, abbr: str, layout: str, summary: str, recipient: str,
                  content: dict[str, Any], ref: str) -> dict[str, Any]:
    team = await ctx.home_team(ctx.p.user_id)
    pid = await ctx.val(
        """INSERT INTO papers.documents (kind, kind_label, title, abbr, layout, year, summary, recipient, content, ref, created_by, team_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (kind, kind_label, title, abbr, layout, today().year, summary, recipient, Jsonb(content), ref, ctx.p.user_id, team))
    return {"paper_id": pid, "status": "draft", "summary": summary}


class Draft(Input):
    kind: str = field("Mã loại", max_length=40)
    kind_label: str = field("Tên loại (danh sách)", max_length=80)
    title: str = field("Tên loại in trên giấy", "", max_length=120, help="Trống = công văn (trích yếu dưới số ký hiệu)")
    abbr: str = field("Viết tắt trong số ký hiệu", "", max_length=12)
    layout: Literal["official", "handout"] = "official"
    summary: str = field("Trích yếu", max_length=300)
    recipient: str = field("Kính gửi", "", max_length=300)
    content: dict[str, Any]
    ref: str = field("Liên quan", "", max_length=200)


@m.command("save_draft", resource="papers", title="Lưu bản soạn (cho gói)", internal=True, agent="deny", family="paper_draft",
           why="Lệnh soạn của gói khai consumes(\"paper_draft\") (tờ giới thiệu một mặt hàng, đề nghị thanh toán / đối chiếu phí, đề nghị tạm ứng, "
               "hồ sơ nội bộ của gói, bản phát hành gửi khách sau duyệt…) khai "
               "resource=\"papers\" nên executor đã kiểm quyền soạn; căn / khách / phiếu ứng / khoản phí đọc bằng quyền người soạn, còn hợp "
               "đồng, dự án và khoản phí của đối chiếu lấy bằng quyền hệ thống. Chỉ GHI một dự thảo papers.documents (người tạo + đội nhà = "
               "người gọi), không đọc gì. "
               "Trả {paper_id, status, summary}.")
async def save_draft(ctx: Ctx, inp: Draft) -> dict[str, Any]:
    """Hợp đồng HẸP cho gói: gói đã kiểm quyền người soạn (lệnh soạn của gói khai ``resource="papers"``) và lấy số liệu bằng quyền người đó;
    ở đây chỉ lưu. Khuôn ``official``: {paragraphs, table?, after?, receivers?, signature: single|two_party|requester, requester?};
    ``handout`` (không cấp số; mặc định gửi khách, ``internal: true`` = hồ sơ nội bộ): {handout: {headline, for?, internal?, facts: [[nhãn, giá trị]], sections: [{title, lines?, table?, note?}]}}."""
    if not re.fullmatch(r"[a-z_]+", inp.kind) or inp.kind == LETTER or inp.kind.startswith(compose.KIND_PREFIX):
        raise Invalid("Mã loại giấy chỉ chữ thường / gạch dưới, khác công văn và khác dãy của mẫu văn bản (mau_…)")
    if inp.layout == "handout" and not isinstance(inp.content.get("handout"), dict):
        raise Invalid("Tài liệu gửi khách cần nội dung handout")
    return await _insert(ctx, kind=inp.kind, kind_label=inp.kind_label, title=inp.title.upper(), abbr=inp.abbr.upper(), layout=inp.layout,
                         summary=inp.summary, recipient=inp.recipient, content=inp.content, ref=inp.ref)


class Letter(Input):
    recipient: str = field("Kính gửi", example="Công ty CP Đầu tư Mẫu Một", max_length=300)
    subject: str = field("Trích yếu (V/v …)", example="đề nghị cung cấp hồ sơ năng lực", max_length=300)
    body: str = field("Nội dung", kind="textarea", help="Mỗi đoạn một dòng", max_length=20_000)
    receivers: str = field("Nơi nhận khác", "", help="Cách nhau dấu chấm phẩy; luôn có 'Như trên' và 'Lưu: VT'")


@m.command("draft_letter", resource="papers", title="Soạn công văn", agent="allow", shows=("papers.view", "paper_id"))
async def draft_letter(ctx: Ctx, inp: Letter) -> dict[str, Any]:
    subject = inp.subject.removeprefix("V/v").strip()
    content = {
        "paragraphs": [p.strip() for p in inp.body.split("\n") if p.strip()],
        "receivers": ["Như trên;", *[f"{x.strip()};" for x in inp.receivers.split(";") if x.strip()], "Lưu: VT."],
        "signature": "single",
    }
    return await _insert(ctx, kind=LETTER, kind_label="Công văn", title="", abbr="", layout="official", summary=f"V/v {subject}",
                         recipient=inp.recipient, content=content, ref="")


# ── Ban hành / huỷ ──

class PaperRef(Input):
    paper_id: UUID = field("Giấy tờ", hidden=True)


@m.command("issue", resource="papers", action="manage", title="Ban hành (cấp số)", agent="deny")
async def issue(ctx: Ctx, inp: PaperRef) -> dict[str, Any]:
    """Cấp số liên tục theo loại trong năm (từ 01 mỗi năm), khoá nội dung. Chỉ người bấm — trợ lý không ban hành."""
    d = await ctx.one("SELECT * FROM papers.documents WHERE id = %s FOR UPDATE", (inp.paper_id,))
    if d is None:
        raise NotFound("Không thấy giấy tờ")
    if d["layout"] == "handout":
        raise Invalid(f"{d['kind_label']} là tài liệu gửi khách — không cần ban hành / cấp số")
    if d["status"] != "draft":
        raise Invalid("Chỉ ban hành được bản dự thảo")
    if d["template_version_id"] is not None:   # soạn từ mẫu (0118): còn ô thiếu / hồ sơ đã đổi ⇒ chặn, đọc lại bằng quyền người ban hành
        await compose.check_issue(ctx, d["id"])
    await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"papers:{ctx.p.tenant_id}:{d['kind']}:{d['year']}",))
    n = int(await ctx.val("SELECT coalesce(max(number), 0) + 1 FROM papers.documents WHERE kind = %s AND year = %s",
                          (d["kind"], d["year"])))
    s = await settings(ctx)
    short = s["short_name"] or "CT"
    symbol = f"{n:02d}/{d['abbr']}-{short}" if d["abbr"] else f"{n:02d}/{short}-VP"
    await ctx.exec("UPDATE papers.documents SET status = 'issued', number = %s, symbol = %s, issued_by = %s, issued_on = core.today()"
                   " WHERE id = %s", (n, symbol, ctx.p.user_id, d["id"]))
    await _store_issued(ctx, d["id"])   # MỌI giấy ban hành (cả công văn tự do — ADR 0249 lát 4): tệp đóng băng lúc cấp số
    return {"paper_id": d["id"], "symbol": symbol}


@m.command("void", resource="papers", action="manage", title="Huỷ giấy tờ", agent="deny")
async def void(ctx: Ctx, inp: PaperRef) -> dict[str, Any]:
    if (d := await ctx.one("SELECT * FROM papers.documents WHERE id = %s", (inp.paper_id,))) is not None:
        await compose.require_seen(ctx, d)   # giấy theo mẫu: phải đọc được hồ sơ nguồn
    n = await ctx.exec("UPDATE papers.documents SET status = 'void' WHERE id = %s AND status <> 'void'", (inp.paper_id,))
    if not n:
        raise NotFound("Không thấy giấy tờ")
    return {"paper_id": inp.paper_id, "status": "void"}


# ── Xem / danh sách / xuất ──

class PaperFilter(Input):
    status: str = field("Trạng thái", "", options={"": "Tất cả", **STATUS})
    kind: str = field("Loại", "", options_query="papers.kind_options")


@m.query("kind_options", resource="papers", title="Loại giấy tờ đã có")
async def kind_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all("""SELECT DISTINCT ON (kind) kind AS value, kind_label AS label FROM papers.documents
                                       ORDER BY kind, created_at DESC"""))


@m.query("list", resource="papers", title="Giấy tờ & công văn")
async def list_papers(ctx: Ctx, inp: PaperFilter) -> dict[str, Any]:
    rows = await ctx.all(
        """SELECT d.id, d.kind, d.kind_label, d.status, d.layout,
                  CASE WHEN d.layout = 'handout' THEN (CASE WHEN (d.content->'handout'->>'internal')::boolean THEN 'Nội bộ' ELSE 'Gửi khách' END) ELSE coalesce(nullif(d.symbol, ''), 'Dự thảo') END AS symbol,
                  d.summary, d.recipient, u.name AS author, d.created_at, d.issued_on, d.template_version_id, d.subject_kind, d.subject_id,
                  CASE WHEN d.template_version_id IS NULL THEN 'no' ELSE 'yes' END AS templated, cardinality(r.missing) AS missing
           FROM papers.documents d LEFT JOIN core.users u ON u.id = d.created_by
           LEFT JOIN papers.revisions r ON r.document_id = d.id AND r.rev = d.rev AND d.template_version_id IS NOT NULL
           WHERE (%(s)s = '' OR d.status = %(s)s) AND (%(k)s = '' OR d.kind = %(k)s)
           ORDER BY d.created_at DESC LIMIT 1000""", {"s": inp.status, "k": inp.kind})
    rows = [{k: v for k, v in r.items() if k not in ("template_version_id", "subject_kind", "subject_id")} for r in await compose.visible(ctx, rows)]
    drafts = sum(1 for r in rows if r["status"] == "draft" and r["layout"] != "handout")
    gaps = sum(1 for r in rows if r["status"] == "draft" and r["missing"])   # bản soạn theo mẫu còn ô chưa có dữ kiện (19/11/2026: gộp màn "Soạn theo mẫu" vào đây)
    return ui.table(rows, [
        ui.col("symbol", "Số"), ui.col("kind_label", "Loại"),
        ui.col("summary", "Trích yếu", width=300), ui.col("recipient", "Kính gửi", width=200),
        ui.col("status", "Trạng thái", "badge", labels=STATUS, tones={"draft": "orange", "issued": "green", "void": "gray"}),
        ui.col("missing", "Ô thiếu", "number"),
        ui.col("author", "Người soạn"), ui.col("created_at", "Soạn lúc", "datetime"), ui.col("issued_on", "Ban hành", "date"),
    ], title="Giấy tờ & công văn", limit=1000, kpis=[ui.kpi("Dự thảo chờ ban hành", drafts, tone="orange" if drafts else None),
                                                      ui.kpi("Còn thiếu dữ kiện", gaps, tone="orange" if gaps else None)])


@m.query("view", resource="papers", title="Xem giấy tờ")
async def view(ctx: Ctx, inp: PaperRef) -> dict[str, Any]:
    d = await ctx.one("SELECT * FROM papers.documents WHERE id = %s", (inp.paper_id,))
    if d is None:
        raise NotFound("Không thấy giấy tờ")
    await compose.require_seen(ctx, d)
    c = d["content"]
    if d["layout"] == "handout":
        return _handout_page(d)
    if d["template_version_id"] is not None:
        return await compose.draft_page(ctx, d)
    # 0102: nội dung là dữ liệu có CẤU TRÚC (đoạn · bảng · phần kết) — vẽ đúng cấu trúc (bảng thật), và XEM TRƯỚC như bản in = PDF đổi từ CHÍNH
    # file .docx tải về (một mẫu cho cả hai — xem trước và file gửi đi không lệch nhau)
    body = [ui.section("Nội dung", text="\n\n".join(c.get("paragraphs", [])))]
    if c.get("table"):
        body.append(ui.section("", table={"columns": c["table"]["columns"], "rows": c["table"]["rows"], "total": c["table"].get("total") or []}))
    if c.get("after"):
        body.append(ui.section("", text="\n\n".join(c["after"])))
    name = f"{(d['symbol'] or 'Du-thao').replace('/', '-')} {d['kind_label']}"
    page = ui.detail(d["summary"] or d["kind_label"], [
        ui.section("Thông tin", [
            ui.field("Số", d["symbol"] or "(dự thảo — cấp số khi ban hành)"), ui.field("Loại", d["kind_label"]),
            ui.field("Kính gửi", d["recipient"]), ui.field("Trạng thái", STATUS[d["status"]]),
        ]),
        *body,
    ], subtitle=f"{d['kind_label']} · {STATUS[d['status']]}")
    page["original"] = {"title": "Bản in", "path": f"/v1/papers/{d['id']}/pdf", "filename": f"{name}.pdf", "page": None, "kind": "pdf"}
    page["links"] = [{"title": "Tải .docx", "path": f"/v1/papers/{d['id']}/docx", "filename": f"{name}.docx"},
                     {"title": "Tải .pdf", "path": f"/v1/papers/{d['id']}/pdf", "filename": f"{name}.pdf"}]
    if d["status"] == "draft":
        page["actions"] = [{"command": "papers.issue", "title": "Ban hành (cấp số)", "style": "primary", "bind": {},
                            "preset": {"paper_id": str(d["id"])}, "when": {}, "importable": False}]
    return page


@m.query("export", resource="papers", title="Xuất .docx", agent="deny")
async def export(ctx: Ctx, inp: PaperRef) -> dict[str, Any]:
    """Dựng file .docx — đọc bằng quyền người tải (RLS). Không dành cho trợ lý (dữ liệu nhị phân)."""
    d = await ctx.one("SELECT * FROM papers.documents WHERE id = %s", (inp.paper_id,))
    if d is None:
        raise NotFound("Không thấy giấy tờ")
    await compose.require_seen(ctx, d)
    if d["status"] == "issued" and (kept := await _issued_file(ctx, d["id"])):   # bản ban hành: đúng tệp đã cất lúc cấp số
        return {"filename": kept[0], "docx_b64": base64.b64encode(kept[1]).decode()}
    # dựng theo dữ liệu hiện tại: bản dự thảo / đã huỷ, và giấy ban hành TRƯỚC ADR 0249 lát 4 (công văn tự do chưa cất tệp lúc cấp số)
    data = render_docx(d, await settings(ctx))
    return {"filename": f"{(d['symbol'] or 'Du-thao').replace('/', '-')}.docx", "docx_b64": base64.b64encode(data).decode()}


def _d(v: Any) -> str:
    if isinstance(v, str):
        v = date.fromisoformat(v[:10])
    return f"{v:%d/%m/%Y}" if isinstance(v, date) else "…/…/……"


def _table_text(t: dict[str, Any]) -> list[str]:
    lines = [" | ".join(t["columns"]), *[" | ".join(r) for r in t["rows"]]]
    if t.get("total"):
        lines.append(" | ".join(t["total"]))
    return ["\n".join(lines)]


def _section_text(sec: dict[str, Any]) -> str:
    return "\n".join([*sec.get("lines", []), *(_table_text(sec["table"]) if sec.get("table") else [])])


def _handout_page(d: dict[str, Any]) -> dict[str, Any]:
    h = d["content"]["handout"]
    page = ui.detail(h["headline"], [ui.section(h.get("facts_title") or "Thông tin", [ui.field(k, v) for k, v in h.get("facts", [])]),
                                     *(ui.section(x["title"], text=_section_text(x)) for x in h.get("sections", []))],
                     subtitle=" · ".join(x for x in (h.get("for", ""), d["kind_label"]) if x))
    page["links"] = [{"title": "Tải .docx" if h.get("internal") else "Tải .docx gửi khách", "path": f"/v1/papers/{d['id']}/docx",
                      "filename": f"{d['kind_label']} {d['ref'].replace('/', ' ')}.docx".strip()}]
    return page


# ── .docx theo thể thức Nghị định 30/2020/NĐ-CP (Phụ lục I) ──

def render_docx(d: dict[str, Any], s: dict[str, Any]) -> bytes:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    if d["layout"] == "handout":
        return _render_handout(d, s)
    if d["layout"] == CONTRACT:
        return compose.render_contract(d, s)
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)  # A4
    sec.top_margin, sec.bottom_margin, sec.left_margin, sec.right_margin = Cm(2), Cm(2), Cm(3), Cm(1.5)
    from docx.styles.style import ParagraphStyle

    normal = doc.styles["Normal"]
    assert isinstance(normal, ParagraphStyle)
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(13)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")  # type: ignore[union-attr]
    normal.paragraph_format.space_after = Pt(6)

    def para(cell_or_doc: Any, text: str = "", *, bold: bool = False, italic: bool = False, size: float = 13,
             align: Any = None, underline: bool = False, indent: bool = False, upper: bool = False) -> Any:
        p = cell_or_doc.add_paragraph()
        if align is not None:
            p.alignment = align
        if indent:
            p.paragraph_format.first_line_indent = Cm(1)
        p.paragraph_format.space_after = Pt(3)
        r = p.add_run(text.upper() if upper else text)
        r.bold, r.italic, r.underline, r.font.size = bold, italic, underline, Pt(size)
        return p

    c = d["content"]
    draft = d["status"] == "draft"
    issued: date = d["issued_on"] or today()
    center, right, just = WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.RIGHT, WD_ALIGN_PARAGRAPH.JUSTIFY

    if draft:
        para(doc, "DỰ THẢO", bold=True, size=12, align=right)
    # Phần đầu: tên cơ quan + số ký hiệu (trái) | Quốc hiệu, tiêu ngữ, địa danh ngày tháng (phải)
    head = doc.add_table(rows=1, cols=2)
    head.alignment = WD_TABLE_ALIGNMENT.CENTER
    left, right_cell = head.rows[0].cells
    left.width, right_cell.width = Cm(6.5), Cm(10)
    left.paragraphs[0].text = ""
    para(left, s["company_name"] or "TÊN CÔNG TY", bold=True, size=12, align=center, upper=True)
    para(left, "———", size=10, align=center)
    number = d["symbol"] or (f"    /{d['abbr']}-{s['short_name'] or 'CT'}" if d["abbr"] else f"    /{s['short_name'] or 'CT'}-VP")
    para(left, f"Số: {number}", size=13, align=center)
    if not d["title"]:   # công văn: trích yếu dưới số ký hiệu
        para(left, d["summary"], size=12, align=center)
    right_cell.paragraphs[0].text = ""
    para(right_cell, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", bold=True, size=12, align=center)
    para(right_cell, "Độc lập - Tự do - Hạnh phúc", bold=True, size=13, align=center, underline=True)
    para(right_cell, f"{s['city'] or 'Hà Nội'}, ngày {issued.day:02d} tháng {issued.month:02d} năm {issued.year}", italic=True,
         size=13, align=center)

    # Tên loại + trích yếu (văn bản có tên loại); "Kính gửi"
    if d["title"]:
        para(doc, "")
        para(doc, d["title"], bold=True, size=14, align=center)
        if d["summary"]:
            para(doc, d["summary"], bold=True, size=14, align=center)
    if d["recipient"] and c.get("signature") != "two_party":   # biên bản hai bên: không "Kính gửi"
        para(doc, f"Kính gửi: {d['recipient']}", size=14, align=center)

    for t in c.get("paragraphs", []):
        para(doc, t, size=14, align=just, indent=True)
    if tb := c.get("table"):
        table = doc.add_table(rows=1, cols=len(tb["columns"]))
        table.style = "Table Grid"
        aligns = {"c": center, "r": right, "l": WD_ALIGN_PARAGRAPH.LEFT}
        for j, h in enumerate(tb["columns"]):
            cell = table.rows[0].cells[j]
            cell.paragraphs[0].text = ""
            para(cell, h, bold=True, size=12, align=center)
        for row in tb["rows"]:
            cells = table.add_row().cells
            for j, v in enumerate(row):
                cells[j].paragraphs[0].text = ""
                para(cells[j], v, size=12, align=aligns[tb.get("align", ["l"] * len(row))[j]])
        if tot := tb.get("total"):
            cells = table.add_row().cells
            label_span = len(tb["columns"]) - (len(tot) - 1)
            merged = cells[0].merge(cells[label_span - 1]) if label_span > 1 else cells[0]
            merged.paragraphs[0].text = ""
            para(merged, tot[0], bold=True, size=12, align=center)
            for j, v in enumerate(tot[1:]):
                cells[label_span + j].paragraphs[0].text = ""
                para(cells[label_span + j], v, bold=True, size=12, align=right)
    for t in c.get("after", []):
        para(doc, t, size=14, align=just, indent=True)

    # Phần ký
    para(doc, "")
    kind = c.get("signature", "single")
    if kind == "two_party":
        sig = doc.add_table(rows=1, cols=2).rows[0].cells
        for cell, who in zip(sig, ("ĐẠI DIỆN BÊN A", "ĐẠI DIỆN BÊN B"), strict=True):
            cell.paragraphs[0].text = ""
            para(cell, who, bold=True, size=13, align=center)
            para(cell, "(Ký, ghi rõ họ tên, đóng dấu)", italic=True, size=12, align=center)
        para(sig[0], "\n\n" + (s["signer_name"] or ""), bold=True, size=13, align=center)
    elif kind == "requester":
        sig = doc.add_table(rows=1, cols=3).rows[0].cells
        for cell, who in zip(sig, ("NGƯỜI ĐỀ NGHỊ", "KẾ TOÁN", "GIÁM ĐỐC DUYỆT"), strict=True):
            cell.paragraphs[0].text = ""
            para(cell, who, bold=True, size=12, align=center)
            para(cell, "(Ký, ghi rõ họ tên)", italic=True, size=11, align=center)
        para(sig[0], "\n\n" + c.get("requester", ""), bold=True, size=12, align=center)
    else:
        sig = doc.add_table(rows=1, cols=2).rows[0].cells
        sig[0].paragraphs[0].text = ""
        p = para(sig[0], "Nơi nhận:", bold=True, italic=True, size=12)
        p.paragraph_format.space_after = Pt(0)
        for rcv in c.get("receivers", ["Như trên;", "Lưu: VT."]):
            q = para(sig[0], f"- {rcv}", size=11)
            q.paragraph_format.space_after = Pt(0)
        sig[1].paragraphs[0].text = ""
        para(sig[1], s["signer_title"] or "GIÁM ĐỐC", bold=True, size=13, align=center, upper=True)
        para(sig[1], "(Ký, ghi rõ họ tên, đóng dấu)", italic=True, size=12, align=center)
        para(sig[1], "\n\n" + (s["signer_name"] or ""), bold=True, size=13, align=center)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _render_handout(d: dict[str, Any], s: dict[str, Any]) -> bytes:
    """Tài liệu gửi khách: trình bày như tài liệu bán hàng (không Quốc hiệu, không số ký hiệu, không chữ ký) — số y như lúc soạn."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor
    from docx.styles.style import ParagraphStyle

    h = d["content"]["handout"]
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.top_margin, sec.bottom_margin, sec.left_margin, sec.right_margin = Cm(1.8), Cm(1.8), Cm(2), Cm(2)
    normal = doc.styles["Normal"]
    assert isinstance(normal, ParagraphStyle)
    normal.font.name = "Arial"
    normal.font.size = Pt(11)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")  # type: ignore[union-attr]

    def para(text: str, *, bold: bool = False, italic: bool = False, size: float = 11, color: tuple[int, int, int] | None = None,
             align: Any = None, after: float = 4) -> None:
        p = doc.add_paragraph()
        if align is not None:
            p.alignment = align
        p.paragraph_format.space_after = Pt(after)
        r = p.add_run(text)
        r.bold, r.italic, r.font.size = bold, italic, Pt(size)
        if color:
            r.font.color.rgb = RGBColor(*color)

    blue = (0x1D, 0x4E, 0xD8)
    company = " · ".join(x for x in (s["company_name"], s["phone"], s["address"]) if x)
    if company:
        para(company, size=9, color=(0x55, 0x55, 0x55), after=10)
    if d["title"]:
        para(d["title"], bold=True, size=12, color=blue, after=2)
    para(h["headline"], bold=True, size=18, after=2)
    if h.get("for"):
        para(h["for"], italic=True, size=12, after=8)
    table = doc.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    for k, v in h.get("facts", []):
        cells = table.add_row().cells
        cells[0].text, cells[1].text = k, v
        cells[0].paragraphs[0].runs[0].bold = True
        cells[0].width, cells[1].width = Cm(5), Cm(12)
    for x in h.get("sections", []):
        para("", after=2)
        para(x["title"], bold=True, size=12, color=blue, after=3)
        if tb := x.get("table"):
            t = doc.add_table(rows=1, cols=len(tb["columns"]))
            t.style = "Table Grid"
            for j, col in enumerate(tb["columns"]):
                t.rows[0].cells[j].text = col
                t.rows[0].cells[j].paragraphs[0].runs[0].bold = True
            total = tb.get("total") or []
            for row in [*tb["rows"], *([[""] * (len(tb["columns"]) - len(total)) + total] if total else [])]:
                cells = t.add_row().cells
                for j, v in enumerate(row):
                    cells[j].text = v
                    if j == len(row) - 1:
                        cells[j].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
        for line in x.get("lines", []):
            para(line, italic=bool(x.get("note")), size=10 if x.get("note") else 11)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---- Màn hình (0069): module tự khai phân hệ / góp màn vào phân hệ khác; workspaces.py chỉ ráp và lọc theo quyền
m.screens("docs", (
    # MỘT màn cho mọi giấy tờ (19/11/2026): trước có thêm màn "Soạn theo mẫu" (``compose``) liệt kê riêng bản soạn từ mẫu — cùng bảng, cùng thẻ xem, hai
    # màn cạnh nhau làm người dùng không biết giấy tờ của mình ở đâu. Hai nút soạn (từ mẫu · công văn tự do) ở đây; khoá cũ ⇒ ``aliases`` giữ ghim.
    Screen("papers", "Giấy tờ & công văn", "doc.richtext", "papers.list", aliases=("compose",),
           actions=(A("papers.start_draft", style="primary"), A("papers.draft_letter")),
           row_actions=(A("papers.refresh_draft", bind={"paper_id": "id"}, when={"status": ["draft"], "templated": ["yes"]}),
                        A("papers.issue", bind={"paper_id": "id"}, when={"status": ["draft"]}, style="primary"),
                        A("papers.void", bind={"paper_id": "id"}, when={"status": ["draft", "issued"]}, style="destructive")),
           row_click="open", open=Open("papers.view", {"paper_id": "id"}, "Xem giấy tờ")),
), rank=-3)   # 0231: văn bản có số hiệu đứng ĐẦU phân hệ "Văn bản & tài liệu" (trước kho tài liệu — màn của chủ phân hệ, rank 0)
m.screens("admin", (
    Screen("paper_settings", "Thông tin công ty trên giấy tờ", "building.columns", "papers.settings_view", group="Hệ thống",
           needs_action=True, actions=(A("papers.set_settings", style="primary"),)),
), rank=10)


# ── Tìm nhanh ⌘K (0087) ──

@m.search("search_papers", "Giấy tờ", resource="papers", rank=70)
async def search_papers(ctx: Ctx, inp: SearchIn) -> list[dict[str, Any]]:
    """Tên / số / ký hiệu giấy tờ ("12/QĐ", "quyet dinh 12") — trong phạm vi người tìm xem được (RLS); có dấu hay không, từng từ."""
    t = SearchTerms(inp.q)
    rows = await compose.visible(ctx, await ctx.all(
        f"""SELECT id, title, kind_label, status, number, symbol, template_version_id, subject_kind, subject_id FROM papers.documents
            WHERE {t.match("title", "number::text", "symbol")} ORDER BY {t.rank("title", "symbol")}, created_at DESC LIMIT %(_n)s""", t.params))
    return [{"title": r["title"], "subtitle": " · ".join(x for x in (r["kind_label"], f"Số {r['number']}/{r['symbol']}" if r["number"] else "") if x),
             "badge": STATUS.get(r["status"], r["status"]), "tone": "muted", "open": {"query": "papers.view", "input": {"paper_id": str(r["id"])}}}
            for r in rows]


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def _store_issued(ctx: Ctx, paper_id: UUID) -> None:
    """Bản ban hành ⇒ dựng .docx MỘT lần (nội dung + cài đặt công ty lúc cấp số), cất ở kho tệp; tải về / xem bản in sau luôn là đúng tệp này — đổi tên
    công ty, người ký, mẫu… sau đó không đổi văn bản đã phát hành."""
    d = await ctx.one("SELECT * FROM papers.documents WHERE id = %s", (paper_id,))
    assert d is not None
    name = f"{d['symbol'].replace('/', '-')} {d['kind_label']}.docx"
    stored = await files.put(ctx, render_docx(d, await settings(ctx)), name, DOCX)
    await ctx.exec("INSERT INTO papers.files (document_id, rev, file_id, role, filename, created_by) VALUES (%s, %s, %s, 'issued', %s, %s)",
                   (d["id"], d["rev"], stored.id, name, ctx.p.user_id))


async def _issued_file(ctx: Ctx, paper_id: UUID) -> tuple[str, bytes] | None:
    f = await ctx.one("SELECT file_id, filename FROM papers.files WHERE document_id = %s AND role = 'issued'", (paper_id,))
    return None if f is None else (f["filename"], await files.read(ctx, f["file_id"]))


# ── HTTP của Giấy tờ (``Module.route``): tải .docx / xem như BẢN IN (PDF đổi từ chính file .docx — một mẫu cho xem trước lẫn file gửi đi) ──

@m.route("GET", "/v1/papers/{paper_id}/docx")
async def http_docx(paper_id: UUID, request: web.Request) -> web.Response:
    p = await web.caller(request)
    r = await web.query(p, "papers.export", {"paper_id": str(paper_id)})
    if not r.ok:
        return web.result(r)
    return web.Response(base64.b64decode(r.output["docx_b64"]),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(r.output['filename'])}"})


@m.route("GET", "/v1/papers/{paper_id}/pdf")
async def http_pdf(paper_id: UUID, request: web.Request) -> web.Response:
    """Giấy tờ như BẢN IN = PDF đổi từ chính file .docx (một mẫu cho xem trước lẫn file gửi đi)."""
    p = await web.caller(request)
    r = await web.query(p, "papers.export", {"paper_id": str(paper_id)})
    if not r.ok:
        return web.result(r)
    try:
        pdf = await convert.to_pdf(r.output["filename"], base64.b64decode(r.output["docx_b64"]))
    except convert.Unavailable as e:
        raise web.HTTPException(409, str(e)) from e
    name = r.output["filename"].rsplit(".", 1)[0] + ".pdf"
    return web.Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}"})


# Soạn thảo từ mẫu (0118) — cùng module, file riêng; nạp sau cùng vì dùng các hàm ở trên
from barista.modules.papers import compose  # noqa: E402
