"""ĐỌC và SỬA TẠI CHỖ file Word (0227) — phần THUẦN, không DB, không mạng.

Ý chính: KHÔNG đổi Word → HTML → Word (mất trình bày). ``read`` dựng một MÔ HÌNH để app vẽ (đoạn, lượt chữ kèm định dạng đã tính, bảng, ảnh nhỏ);
``apply`` nhận danh sách thay đổi của người sửa (đặt lại chữ một đoạn / thêm đoạn / xoá đoạn) và áp ĐÚNG các chỗ đó vào XML của file gốc — mọi thứ
không đụng tới (đầu / chân trang, kiểu chữ, lề, ảnh, bảng, mục lục) giữ nguyên từng byte XML.

* MÃ ĐOẠN = đường đi theo vị trí trong file (``b12``, ``b3.r1.c2.b0``): ổn định trong MỘT phiên bản file; thay đổi gửi kèm phiên bản gốc, lệch ⇒ từ chối
  ở tầng module. Cùng một hàm duyệt (``_blocks``) cho đọc lẫn áp ⇒ mã luôn trỏ đúng phần tử.
* ĐOẠN SỬA ĐƯỢC = đoạn chỉ gồm lượt chữ đơn giản (chữ, tab, xuống dòng). Đoạn có liên kết, trường (mục lục, số trang), ảnh, ghi chú, theo dõi thay đổi
  ⇒ HIỆN nhưng KHOÁ (``why`` nói lý do) — không đoán cách sửa thứ mình không hiểu.
* Định dạng chữ khi sửa: mỗi lượt chữ phía app mang ``src`` = lượt gốc nó kế thừa ⇒ áp lại chép nguyên ``rPr`` của lượt gốc, chỉ ghi thêm đậm / nghiêng /
  gạch chân khi người sửa đổi khác giá trị đang có hiệu lực.
* File của người dùng là dữ liệu không tin được: chặn bom nén (tổng cỡ giải nén), bộ đọc XML của python-docx không giải thực thể ngoài, không chạy macro
  (chỉ nhận .docx).
"""

import base64
import copy
import io
import re
import zipfile
from typing import Any

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

MAX_BYTES = 20 * 1024 * 1024          # file .docx tối đa
MAX_UNZIPPED = 300 * 1024 * 1024      # tổng cỡ giải nén (chặn bom nén)
MAX_BLOCKS = 6000                     # số đoạn + ô bảng tối đa dựng mô hình
IMG_MAX = 1024 * 1024                 # mỗi ảnh nhúng vào mô hình
IMG_TOTAL = 6 * 1024 * 1024           # tổng ảnh nhúng vào mô hình
TEXT_MAX = 20000                      # chữ tối đa của một đoạn khi sửa

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
A_BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
R_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
WP_EXTENT = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent"

P, R, T, TAB, BR, TBL, TR, TC, SDT, SDT_CONTENT = (qn(f"w:{x}") for x in ("p", "r", "t", "tab", "br", "tbl", "tr", "tc", "sdt", "sdtContent"))
PPR, RPR, SECT = qn("w:pPr"), qn("w:rPr"), qn("w:sectPr")
VAL = qn("w:val")
# con của ĐOẠN không làm đoạn bị khoá (giữ nguyên chỗ khi sửa)
P_PASSIVE = {qn(f"w:{x}") for x in ("pPr", "bookmarkStart", "bookmarkEnd", "proofErr", "permStart", "permEnd")}
# con của LƯỢT CHỮ đơn giản
R_SIMPLE = {RPR, T, TAB, BR, qn("w:lastRenderedPageBreak"), qn("w:noBreakHyphen"), qn("w:softHyphen")}
LOCK_WHY = {"hyperlink": "có liên kết", "fldSimple": "có trường tự động", "fldChar": "có trường tự động (mục lục, số trang…)",
            "instrText": "có trường tự động", "drawing": "có hình", "pict": "có hình", "object": "có đối tượng nhúng", "ins": "có theo dõi thay đổi",
            "del": "có theo dõi thay đổi", "footnoteReference": "có chú thích", "endnoteReference": "có chú thích", "commentReference": "có ghi chú",
            "sdt": "có ô nhập liệu", "smartTag": "có thẻ thông minh", "sym": "có ký hiệu đặc biệt", "AlternateContent": "có hình", "br_page": "có ngắt trang"}


class BadDocx(Exception):
    """File không đọc được / vượt giới hạn / thay đổi không áp được — chữ cho người dùng."""


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _pct(el: Any) -> float | None:
    """Độ rộng kiểu phần trăm của ``w:tblW`` / ``w:tcW``: ``"25%"`` hoặc phần năm mươi của phần trăm (5000 = 100%)."""
    if el is None or el.get(qn("w:type")) != "pct":
        return None
    v = (el.get(qn("w:w")) or "").strip()
    try:
        n = float(v[:-1]) if v.endswith("%") else float(v) / 50
    except ValueError:
        return None
    return round(n, 2) if 0 < n <= 100 else None


def _columns(tbl: Any) -> tuple[list[float], float | None]:
    """Độ rộng cột: lưới cột (pt) khi file ghi thật. File do công cụ sinh hay để lưới giả (mỗi cột vài pt) và ghi độ rộng ở từng ô — khi đó lấy theo dòng
    đầu tiên không gộp ô. Bảng / ô rộng theo % ⇒ trả thêm % bề ngang của bảng, cột là tỷ lệ."""
    grid = [w for w in (_twips(g.get(qn("w:w"))) for g in tbl.iterfind(f"{qn('w:tblGrid')}/{qn('w:gridCol')}")) if w]
    pr = tbl.find(qn("w:tblPr"))
    table_pct = _pct(pr.find(qn("w:tblW"))) if pr is not None else None
    if table_pct is None and sum(grid) >= 72:   # lưới thật (từ một inch trở lên)
        return grid, None
    for tr in tbl.iterchildren(TR):
        ws = [tc.find(f"{qn('w:tcPr')}/{qn('w:tcW')}") for tc in tr.iterchildren(TC)]
        if any(tc.find(f"{qn('w:tcPr')}/{qn('w:gridSpan')}") is not None for tc in tr.iterchildren(TC)) or not ws:
            continue
        pcts = [_pct(w) for w in ws]
        if all(pcts):
            return [float(x or 0) for x in pcts], table_pct or min(100.0, round(sum(float(x or 0) for x in pcts), 2))
        pts = [_twips(w.get(qn("w:w"))) if w is not None and w.get(qn("w:type")) in (None, "dxa") else None for w in ws]
        if all(pts) and table_pct is None:
            return [float(x or 0) for x in pts], None
        break
    return (grid, table_pct) if grid else ([], table_pct)


def _twips(v: str | None) -> float | None:
    try:
        return round(int(float(v)) / 20, 2) if v is not None else None   # twip → pt
    except ValueError:
        return None


def _on(el: Any) -> bool:
    """Thuộc tính bật / tắt của Word (``<w:b/>`` = bật; ``w:val="0"`` / ``false`` = tắt)."""
    return el.get(VAL) not in ("0", "false", "off")


def load(data: bytes) -> Any:
    if len(data) > MAX_BYTES:
        raise BadDocx(f"File quá {MAX_BYTES // (1024 * 1024)} MB")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
                raise BadDocx("File giải nén quá lớn — không mở")
            if "word/vbaProject.bin" in z.namelist():
                raise BadDocx("File có macro — chỉ nhận .docx không macro")
        return Document(io.BytesIO(data))
    except BadDocx:
        raise
    except Exception as e:   # GIỮ rộng: byte người ngoài đưa vào làm zip / lxml / python-docx ném đủ loại lỗi
        raise BadDocx("Không đọc được file Word (file hỏng hoặc không phải .docx)") from e


# ── Duyệt cấu trúc: MỘT hàm cho đọc lẫn áp ──

def _blocks(container: Any) -> list[Any]:
    """Đoạn và bảng trực tiếp của thân / ô bảng, theo thứ tự; ô nhập liệu cấp khối (``w:sdt``) được mở phẳng."""
    out: list[Any] = []
    for ch in container.iterchildren():
        if ch.tag in (P, TBL):
            out.append(ch)
        elif ch.tag == SDT:
            content = ch.find(SDT_CONTENT)
            if content is not None:
                out += _blocks(content)
    return out


def _index(container: Any, prefix: str, into: dict[str, Any]) -> None:
    for i, el in enumerate(_blocks(container)):
        bid = f"{prefix}b{i}"
        into[bid] = el
        if el.tag == TBL:
            for r, tr in enumerate(el.iterchildren(TR)):
                for c, tc in enumerate(tr.iterchildren(TC)):
                    _index(tc, f"{bid}.r{r}.c{c}.", into)


def _simple_runs(p: Any) -> list[Any]:
    return [ch for ch in p.iterchildren(R)]


def _lock_reason(p: Any) -> str:
    """Lý do đoạn bị khoá ("" = sửa được)."""
    for ch in p.iterchildren():
        if ch.tag in P_PASSIVE:
            continue
        if ch.tag != R:
            return LOCK_WHY.get(_local(ch.tag), "có nội dung đặc biệt")
        for g in ch.iterchildren():
            if g.tag not in R_SIMPLE:
                return LOCK_WHY.get(_local(g.tag), "có nội dung đặc biệt")
            if g.tag == BR and g.get(qn("w:type")) in ("page", "column"):
                return LOCK_WHY["br_page"]
    ppr = p.find(PPR)
    if ppr is not None and ppr.find(qn("w:rPr")) is not None and ppr.find(qn("w:rPr")).find(qn("w:ins")) is not None:
        return LOCK_WHY["ins"]
    return ""


def _run_text(r: Any) -> str:
    out: list[str] = []
    for g in r.iterchildren():
        if g.tag == T:
            out.append(g.text or "")
        elif g.tag == TAB:
            out.append("\t")
        elif g.tag == BR:
            out.append("\n")
        elif g.tag == qn("w:noBreakHyphen"):
            out.append("‑")
    return "".join(out)


# ── Định dạng có hiệu lực (trực tiếp → kiểu ký tự → kiểu đoạn → mặc định văn bản) ──

class _Styles:
    def __init__(self, doc: Any) -> None:
        root = doc.styles.element
        self.by_id = {s.get(qn("w:styleId")): s for s in root.iterchildren(qn("w:style"))}
        self.default_p = next((s for s in self.by_id.values() if s.get(qn("w:type")) == "paragraph" and s.get(qn("w:default")) in ("1", "true")), None)
        dd = root.find(qn("w:docDefaults"))
        self.dd_rpr = dd.find(f"{qn('w:rPrDefault')}/{RPR}") if dd is not None else None
        self.dd_ppr = dd.find(f"{qn('w:pPrDefault')}/{PPR}") if dd is not None else None

    def chain(self, style_id: str | None) -> list[Any]:
        out, seen = [], set()
        while style_id and style_id not in seen and style_id in self.by_id:
            seen.add(style_id)
            s = self.by_id[style_id]
            out.append(s)
            based = s.find(qn("w:basedOn"))
            style_id = based.get(VAL) if based is not None else None
        return out

    def p_chain(self, p: Any) -> list[Any]:
        ppr = p.find(PPR)
        sid = None
        if ppr is not None and ppr.find(qn("w:pStyle")) is not None:
            sid = ppr.find(qn("w:pStyle")).get(VAL)
        return self.chain(sid) if sid else ([self.default_p] if self.default_p is not None else [])

    def rprs(self, r: Any | None, p: Any) -> list[Any]:
        """Các ``rPr`` theo thứ tự ưu tiên cho một lượt chữ của đoạn ``p``."""
        out: list[Any] = []
        rpr = r.find(RPR) if r is not None else None
        if rpr is not None:
            out.append(rpr)
            rs = rpr.find(qn("w:rStyle"))
            if rs is not None:
                out += [x.find(RPR) for x in self.chain(rs.get(VAL))]
        out += [s.find(RPR) for s in self.p_chain(p)]
        out.append(self.dd_rpr)
        return [x for x in out if x is not None]

    def pprs(self, p: Any) -> list[Any]:
        out = [p.find(PPR)] + [s.find(PPR) for s in self.p_chain(p)] + [self.dd_ppr]
        return [x for x in out if x is not None]

    def name(self, p: Any) -> str:
        ch = self.p_chain(p)
        if not ch:
            return ""
        n = ch[0].find(qn("w:name"))
        return (n.get(VAL) or "") if n is not None else ""


def _first(props: list[Any], tag: str) -> Any | None:
    for pr in props:
        el = pr.find(qn(tag))
        if el is not None:
            return el
    return None


def run_format(st: _Styles, r: Any | None, p: Any) -> dict[str, Any]:
    props = st.rprs(r, p)
    out: dict[str, Any] = {}
    for key, tag in (("b", "w:b"), ("i", "w:i"), ("s", "w:strike"), ("caps", "w:caps")):
        el = _first(props, tag)
        if el is not None and _on(el):
            out[key] = True
    u = _first(props, "w:u")
    if u is not None and u.get(VAL) not in (None, "none"):
        out["u"] = True
    sz = _first(props, "w:sz")
    if sz is not None and (sz.get(VAL) or "").isdigit():
        out["sz"] = int(sz.get(VAL)) / 2
    color = _first(props, "w:color")
    if color is not None and (color.get(VAL) or "auto") != "auto":
        out["color"] = color.get(VAL)
    fonts = _first(props, "w:rFonts")
    if fonts is not None:
        f = fonts.get(qn("w:ascii")) or fonts.get(qn("w:hAnsi")) or fonts.get(qn("w:cs"))
        if f:
            out["font"] = f
    hl = _first(props, "w:highlight")
    if hl is not None and hl.get(VAL) not in (None, "none"):
        out["hl"] = hl.get(VAL)
    va = _first(props, "w:vertAlign")
    if va is not None and va.get(VAL) in ("superscript", "subscript"):
        out["va"] = va.get(VAL)[:3]
    return out


def _para_format(st: _Styles, p: Any) -> dict[str, Any]:
    props = st.pprs(p)
    out: dict[str, Any] = {}
    jc = _first(props, "w:jc")
    if jc is not None:
        out["align"] = {"both": "justify", "distribute": "justify", "start": "left", "end": "right"}.get(jc.get(VAL), jc.get(VAL))
    ind = _first(props, "w:ind")
    if ind is not None:
        left = _twips(ind.get(qn("w:left")) or ind.get(qn("w:start")))
        right = _twips(ind.get(qn("w:right")) or ind.get(qn("w:end")))
        first, hang = _twips(ind.get(qn("w:firstLine"))), _twips(ind.get(qn("w:hanging")))
        if left:
            out["ind_l"] = left
        if right:
            out["ind_r"] = right
        if hang:
            out["ind_first"] = -hang
        elif first:
            out["ind_first"] = first
    sp = _first(props, "w:spacing")
    if sp is not None:
        for key, attr in (("sp_before", "w:before"), ("sp_after", "w:after")):
            v = _twips(sp.get(qn(attr)))
            if v:
                out[key] = v
        line = sp.get(qn("w:line"))
        if line and line.lstrip("-").isdigit():
            if sp.get(qn("w:lineRule")) in ("exact", "atLeast"):
                out["line_pt"] = int(line) / 20
            else:
                out["line"] = round(int(line) / 240, 2)
    name = st.name(p).lower()
    if name.startswith("heading ") and name[8:].isdigit():
        out["heading"] = min(6, int(name[8:]))
    elif name == "title":
        out["heading"] = 1
    return out


# ── Đánh số / gạch đầu dòng (bản gọn: đủ cho danh sách thường gặp) ──

def _fmt_number(n: int, fmt: str) -> str:
    if fmt in ("lowerLetter", "upperLetter"):
        s, n0 = "", n
        while n0 > 0:
            n0, rem = divmod(n0 - 1, 26)
            s = chr(97 + rem) + s
        return s.upper() if fmt == "upperLetter" else s
    if fmt in ("lowerRoman", "upperRoman"):
        vals = ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"))
        s, n0 = "", n
        for v, sym in vals:
            while n0 >= v:
                s, n0 = s + sym, n0 - v
        return s.upper() if fmt == "upperRoman" else s
    if fmt == "decimalZero":
        return f"{n:02d}"
    return str(n)


class _Numbering:
    def __init__(self, doc: Any) -> None:
        self.abstract: dict[str, dict[int, dict[str, Any]]] = {}
        self.num: dict[str, str] = {}
        self.counters: dict[str, dict[int, int]] = {}
        try:
            root = doc.part.numbering_part.element
        except Exception:   # không có phần đánh số (file không có danh sách) — python-docx ném KeyError / NotImplementedError tuỳ bản
            return
        for a in root.iterchildren(qn("w:abstractNum")):
            lv: dict[int, dict[str, Any]] = {}
            for lvl in a.iterchildren(qn("w:lvl")):
                def val(tag: str, lvl: Any = lvl) -> str:
                    el = lvl.find(qn(tag))
                    return (el.get(VAL) or "") if el is not None else ""
                ind = lvl.find(f"{PPR}/{qn('w:ind')}")
                lv[int(lvl.get(qn("w:ilvl")) or 0)] = {
                    "fmt": val("w:numFmt") or "decimal", "text": val("w:lvlText"), "start": int(val("w:start") or 1),
                    "ind_l": _twips(ind.get(qn("w:left")) or ind.get(qn("w:start"))) if ind is not None else None,
                    "hang": _twips(ind.get(qn("w:hanging"))) if ind is not None else None}
            self.abstract[a.get(qn("w:abstractNumId"))] = lv
        for n in root.iterchildren(qn("w:num")):
            a = n.find(qn("w:abstractNumId"))
            if a is not None:
                self.num[n.get(qn("w:numId"))] = a.get(VAL)

    def label(self, num_id: str, ilvl: int) -> dict[str, Any] | None:
        aid = self.num.get(num_id)
        levels = self.abstract.get(aid or "")
        if not levels or ilvl not in levels:
            return None
        lv = levels[ilvl]
        state = self.counters.setdefault(aid or "", {})
        state[ilvl] = (state[ilvl] if ilvl in state else int(lv["start"]) - 1) + 1
        for deeper in [k for k in state if k > ilvl]:
            del state[deeper]
        if lv["fmt"] == "bullet":
            text = {"o": "◦", "-": "–", "–": "–", "+": "+", "*": "•"}.get(lv["text"], "•")
        elif lv["fmt"] == "none":
            text = ""
        else:
            text = lv["text"]
            for k in range(9):
                if f"%{k + 1}" in text:
                    other = levels.get(k, lv)
                    text = text.replace(f"%{k + 1}", _fmt_number(state[k] if k in state else int(other["start"]), other["fmt"]))
        out: dict[str, Any] = {"label": text, "level": ilvl}
        if lv["ind_l"]:
            out["ind_l"] = lv["ind_l"]
        if lv["hang"]:
            out["hang"] = lv["hang"]
        return out


# ── Đọc thành mô hình ──

class _Reader:
    def __init__(self, doc: Any) -> None:
        self.doc, self.st, self.num = doc, _Styles(doc), _Numbering(doc)
        self.count, self.img_bytes = 0, 0
        self.locked, self.images_skipped = 0, 0

    def _image(self, r: Any) -> dict[str, Any] | None:
        blip = next(iter(r.iter(A_BLIP)), None)
        if blip is None or not blip.get(R_EMBED):
            return None
        ext = next(iter(r.iter(WP_EXTENT)), None)
        size = {"w": round(int(ext.get("cx")) / 12700, 1), "h": round(int(ext.get("cy")) / 12700, 1)} if ext is not None and ext.get("cx") else {}
        try:
            part = self.doc.part.related_parts[blip.get(R_EMBED)]
            blob, kind = part.blob, part.content_type
        except Exception:   # liên kết ảnh hỏng / ảnh ngoài file
            return {"img": "", **size}
        if not kind.startswith("image/") or kind in ("image/x-emf", "image/x-wmf") or len(blob) > IMG_MAX or self.img_bytes + len(blob) > IMG_TOTAL:
            self.images_skipped += 1
            return {"img": "", **size}
        self.img_bytes += len(blob)
        return {"img": f"data:{kind};base64,{base64.b64encode(blob).decode()}", **size}

    def paragraph(self, p: Any, bid: str) -> dict[str, Any]:
        why = _lock_reason(p)
        out: dict[str, Any] = {"id": bid, "t": "p", "style": _para_format(self.st, p), "runs": []}
        num = _first(self.st.pprs(p), "w:numPr")
        if num is not None:
            nid, lvl = num.find(qn("w:numId")), num.find(qn("w:ilvl"))
            if nid is not None and nid.get(VAL) not in (None, "0"):
                got = self.num.label(nid.get(VAL), int(lvl.get(VAL)) if lvl is not None and (lvl.get(VAL) or "").isdigit() else 0)
                if got:
                    out["list"] = got
        if why:
            self.locked += 1
            out["locked"] = why
            for r in p.iter(R):   # hiện gần đúng: chữ của mọi lượt (kể cả trong liên kết) + ảnh theo thứ tự
                img = self._image(r)
                if img is not None:
                    out["runs"].append(img)
                text = _run_text(r)
                if text:
                    out["runs"].append({"text": text, **run_format(self.st, r, p)})
        else:
            for i, r in enumerate(_simple_runs(p)):
                text = _run_text(r)
                if text:
                    out["runs"].append({"src": i, "text": text, **run_format(self.st, r, p)})
        if not out["runs"]:
            out["base"] = run_format(self.st, None, p)   # đoạn trống: cỡ / phông để ô gõ chữ đúng chiều cao
        return out

    def table(self, tbl: Any, bid: str) -> dict[str, Any]:
        grid, w_pct = _columns(tbl)
        rows: list[dict[str, Any]] = []
        open_span: dict[int, dict[str, Any]] = {}   # cột lưới → ô đang gộp dọc
        for r, tr in enumerate(tbl.iterchildren(TR)):
            cells, col = [], 0
            for c, tc in enumerate(tr.iterchildren(TC)):
                pr = tc.find(qn("w:tcPr"))
                span_el = pr.find(qn("w:gridSpan")) if pr is not None else None
                span = int(span_el.get(VAL)) if span_el is not None and (span_el.get(VAL) or "").isdigit() else 1
                vm = pr.find(qn("w:vMerge")) if pr is not None else None
                if vm is not None and vm.get(VAL) != "restart" and col in open_span:
                    open_span[col]["rowspan"] += 1   # ô nối tiếp của một ô gộp dọc: không vẽ, ô đầu cao thêm một dòng
                    col += span
                    continue
                cell: dict[str, Any] = {"blocks": self.container(tc, f"{bid}.r{r}.c{c}.")}
                if span > 1:
                    cell["span"] = span
                shd = pr.find(qn("w:shd")) if pr is not None else None
                if shd is not None and shd.get(qn("w:fill")) not in (None, "auto"):
                    cell["fill"] = shd.get(qn("w:fill"))
                tcb = pr.find(qn("w:tcBorders")) if pr is not None else None
                if tcb is not None and len(tcb):   # viền đặt riêng ở ô thắng viền của bảng (vd khối quốc hiệu: bảng có kiểu kẻ ô nhưng ô đặt "không viền")
                    cell["borders"] = any(b.get(VAL) not in (None, "nil", "none") for b in tcb.iterchildren())
                va = pr.find(qn("w:vAlign")) if pr is not None else None
                if va is not None and va.get(VAL) in ("center", "bottom"):
                    cell["valign"] = va.get(VAL)
                if vm is not None:
                    cell["rowspan"] = 1
                    open_span[col] = cell
                else:
                    open_span.pop(col, None)
                cells.append(cell)
                col += span
            rows.append({"cells": cells})
        out = {"id": bid, "t": "table", "cols": grid, "rows": rows, "borders": self._borders(tbl)}
        if w_pct:
            out["w_pct"] = w_pct   # bảng rộng theo % bề ngang trang; `cols` khi đó là tỷ lệ giữa các cột, không phải pt
        return out

    def _borders(self, tbl: Any) -> bool:
        def has(borders: Any | None) -> bool | None:
            if borders is None:
                return None
            return any(b.get(VAL) not in (None, "nil", "none") for b in borders.iterchildren())
        pr = tbl.find(qn("w:tblPr"))
        if pr is None:
            return False
        direct = has(pr.find(qn("w:tblBorders")))
        if direct is not None:
            return direct
        style = pr.find(qn("w:tblStyle"))
        for s in self.st.chain(style.get(VAL) if style is not None else None):
            got = has(s.find(f"{qn('w:tblPr')}/{qn('w:tblBorders')}"))
            if got is not None:
                return got
        return False

    def container(self, container: Any, prefix: str) -> list[dict[str, Any]]:
        out = []
        for i, el in enumerate(_blocks(container)):
            self.count += 1
            if self.count > MAX_BLOCKS:
                raise BadDocx(f"Văn bản quá dài (hơn {MAX_BLOCKS} đoạn) — chưa mở để sửa được")
            out.append(self.paragraph(el, f"{prefix}b{i}") if el.tag == P else self.table(el, f"{prefix}b{i}"))
        return out


def read(data: bytes) -> dict[str, Any]:
    """Mô hình để vẽ: ``{page, blocks, stats}``. ``page``: khổ giấy + lề (pt). Đầu / chân trang không nằm trong mô hình (giữ nguyên trong file)."""
    doc = load(data)
    rd = _Reader(doc)
    blocks = rd.container(doc.element.body, "")
    sect = doc.element.body.find(SECT)
    page = {"w": 595.3, "h": 841.9, "ml": 72.0, "mr": 72.0, "mt": 72.0, "mb": 72.0}
    if sect is not None:
        sz, mar = sect.find(qn("w:pgSz")), sect.find(qn("w:pgMar"))
        for key, el, attr in (("w", sz, "w:w"), ("h", sz, "w:h"), ("ml", mar, "w:left"), ("mr", mar, "w:right"), ("mt", mar, "w:top"), ("mb", mar, "w:bottom")):
            v = _twips(el.get(qn(attr))) if el is not None else None
            if v:
                page[key] = v
    has_hf = bool(doc.element.body.findall(f".//{qn('w:headerReference')}") or doc.element.body.findall(f".//{qn('w:footerReference')}"))
    return {"page": page, "blocks": blocks,
            "stats": {"blocks": rd.count, "locked": rd.locked, "images_skipped": rd.images_skipped, "header_footer": has_hf}}


def plain_text(model: dict[str, Any], limit: int = 4000) -> str:
    """Bản chữ của văn bản (cho app chưa vẽ được trình sửa + cho trợ lý): mỗi đoạn một dòng, ô bảng cách nhau bằng " | "."""
    lines: list[str] = []

    def walk(blocks: list[dict[str, Any]]) -> None:
        for b in blocks:
            if b["t"] == "p":
                text = "".join(r.get("text", "") for r in b["runs"])
                label = b.get("list", {}).get("label", "")
                lines.append(f"{label} {text}".strip() if label else text)
            else:
                for row in b["rows"]:
                    lines.append(" | ".join(" ".join("".join(r.get("text", "") for r in p.get("runs", [])) for p in c["blocks"] if p["t"] == "p")
                                            for c in row["cells"]))
    walk(model["blocks"])
    text = "\n".join(x for x in lines if x.strip())
    return text if len(text) <= limit else text[:limit] + "…"


# ── Áp thay đổi vào file gốc ──

# Thứ tự phần tử con của ``w:rPr`` theo lược đồ (phần hay gặp) — chèn đúng chỗ, Word bản khó tính mới không báo file lỗi
RPR_ORDER = ("rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline", "shadow", "emboss", "imprint", "noProof",
             "snapToGrid", "vanish", "webHidden", "color", "spacing", "w", "kern", "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd",
             "fitText", "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath")


def _rpr_child(rpr: Any, name: str) -> Any:
    """Phần tử con ``w:<name>`` của ``w:rPr``: có thì trả, chưa có thì tạo và chèn ĐÚNG thứ tự lược đồ."""
    el = rpr.find(qn(f"w:{name}"))
    if el is not None:
        return el
    el = OxmlElement(f"w:{name}")
    later = {qn(f"w:{n}") for n in RPR_ORDER[RPR_ORDER.index(name) + 1:]} | {qn("w:rPrChange")}
    for k, ch in enumerate(rpr):
        if ch.tag in later:
            rpr.insert(k, el)
            return el
    rpr.append(el)
    return el


def _set_flag(rpr: Any, name: str, on: bool) -> None:
    for n in (name, f"{name}Cs"):
        el = _rpr_child(rpr, n)
        if on:
            el.attrib.pop(VAL, None)
        else:
            el.set(VAL, "0")


def _new_run(st: _Styles, p: Any, base: Any | None, spec: dict[str, Any]) -> Any:
    """Lượt chữ mới: chép nguyên thuộc tính của lượt gốc ``base``, chỉ ghi thêm thứ KHÁC định dạng hiệu lực (đậm / nghiêng / gạch chân, cỡ, màu, phông)."""
    r = OxmlElement("w:r")
    rpr = copy.deepcopy(base.find(RPR)) if base is not None and base.find(RPR) is not None else None
    have = run_format(st, base, p)
    flags = {k: bool(spec.get(k)) for k in ("b", "i", "u") if bool(spec.get(k)) != bool(have.get(k))}
    sz = spec.get("sz") if spec.get("sz") is not None and spec.get("sz") != have.get("sz") else None
    color = spec.get("color") if spec.get("color") and spec["color"].upper() != str(have.get("color") or "").upper() else None
    font = spec.get("font") if spec.get("font") and spec["font"] != have.get("font") else None
    if flags or sz is not None or color or font:
        if rpr is None:
            rpr = OxmlElement("w:rPr")
        for k in ("b", "i"):
            if k in flags:
                _set_flag(rpr, k, flags[k])
        if "u" in flags:
            _rpr_child(rpr, "u").set(VAL, "single" if flags["u"] else "none")
        if sz is not None:
            for n in ("sz", "szCs"):
                _rpr_child(rpr, n).set(VAL, str(round(sz * 2)))   # nửa điểm
        if color:
            el = _rpr_child(rpr, "color")
            for a in list(el.attrib):
                del el.attrib[a]   # bỏ màu theo chủ đề (themeColor / themeTint) — màu người chọn là màu cố định
            el.set(VAL, color.upper())
        if font:
            el = _rpr_child(rpr, "rFonts")
            for a in list(el.attrib):
                del el.attrib[a]   # bỏ phông theo chủ đề / gợi ý
            for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
                el.set(qn(a), font)
    if rpr is not None:
        r.append(rpr)
    buf = ""

    def flush() -> None:
        nonlocal buf
        if buf:
            t = OxmlElement("w:t")
            t.text = buf
            t.set(XML_SPACE, "preserve")
            r.append(t)
            buf = ""
    for ch in spec["text"]:
        if ch == "\t":
            flush()
            r.append(OxmlElement("w:tab"))
        elif ch == "\n":
            flush()
            r.append(OxmlElement("w:br"))
        elif ch >= " " or ch == "‑":
            buf += ch   # ký tự điều khiển khác bị bỏ (XML không nhận)
    flush()
    return r


HEX6 = re.compile(r"[0-9a-fA-F]{6}")
FONT_NAME = re.compile(r"[\w .\-]{1,60}")


def _clean_runs(runs: Any) -> list[dict[str, Any]]:
    if not isinstance(runs, list) or len(runs) > 2000:
        raise BadDocx("Thay đổi không hợp lệ")
    out: list[dict[str, Any]] = []
    total = 0
    for x in runs:
        if not isinstance(x, dict) or not isinstance(x.get("text"), str):
            raise BadDocx("Thay đổi không hợp lệ")
        if not x["text"]:
            continue
        total += len(x["text"])
        sz, color, font = x.get("sz"), x.get("color"), x.get("font")
        spec = {"src": x.get("src") if isinstance(x.get("src"), int) else -1, "text": x["text"], **{k: bool(x.get(k)) for k in ("b", "i", "u")},
                # cỡ / màu / phông: None = giữ như lượt gốc; giá trị lạ bị bỏ (không đưa chuỗi tuỳ ý vào XML)
                "sz": round(float(sz) * 2) / 2 if isinstance(sz, (int, float)) and not isinstance(sz, bool) and 1 <= sz <= 400 else None,
                "color": color.upper() if isinstance(color, str) and HEX6.fullmatch(color) else None,
                "font": font if isinstance(font, str) and FONT_NAME.fullmatch(font) else None}
        if out and all(out[-1][k] == spec[k] for k in ("src", "b", "i", "u", "sz", "color", "font")):
            out[-1]["text"] += spec["text"]   # gộp các mảnh liền nhau cùng nguồn, cùng định dạng (trình duyệt hay cắt vụn)
        else:
            out.append(spec)
    if total > TEXT_MAX:
        raise BadDocx(f"Một đoạn tối đa {TEXT_MAX} ký tự")
    return out


def _fill(st: _Styles, p: Any, source: Any, runs: list[dict[str, Any]]) -> None:
    """Đặt lại chữ của đoạn ``p`` theo ``runs``; định dạng kế thừa từ lượt chữ ``src`` của đoạn ``source`` (chính ``p`` khi sửa, đoạn mẫu khi thêm)."""
    originals = _simple_runs(source)
    fallback = originals[0] if originals else None
    new = [_new_run(st, source, originals[s["src"]] if 0 <= s["src"] < len(originals) else fallback, s) for s in runs]
    for ch in list(p):
        if ch.tag == R or ch.tag == qn("w:proofErr"):
            p.remove(ch)
    for r in new:
        p.append(r)


def apply(data: bytes, ops: list[dict[str, Any]]) -> bytes:
    """Áp ``ops`` vào file gốc, trả file mới. ``ops`` (theo thứ tự người sửa): ``{"op": "set", "id", "runs"}`` · ``{"op": "insert", "after" | "before": id,
    "runs"}`` (đoạn mới chép định dạng đoạn của đoạn mốc) · ``{"op": "delete", "id"}`` ·
    ``{"op": "para", "id", "align"?: left | center | right | justify, "line"?: bội số giãn dòng, "indent"?: thụt lề trái (pt)}`` (``insert`` cũng nhận ba khoá này).
    Lượt chữ nhận thêm ``sz`` (pt) / ``color`` (hex) / ``font`` — thiếu = giữ như lượt gốc. Mã trỏ theo file GỐC; sai / trỏ đoạn khoá ⇒ ``BadDocx``."""
    if not isinstance(ops, list) or not ops or len(ops) > 5000:
        raise BadDocx("Không có thay đổi nào để lưu" if not ops else "Thay đổi không hợp lệ")
    doc = load(data)
    st = _Styles(doc)
    index: dict[str, Any] = {}
    _index(doc.element.body, "", index)

    def para(bid: Any) -> Any:
        el = index.get(bid) if isinstance(bid, str) else None
        if el is None or el.tag != P:
            raise BadDocx("Văn bản đã đổi so với lúc mở — tải lại rồi sửa tiếp")
        return el

    plan: list[tuple[str, Any, Any]] = []
    for op in ops:   # kiểm và phân giải HẾT trước khi đụng XML ⇒ lỗi giữa chừng không để lại file nửa vời
        if not isinstance(op, dict):
            raise BadDocx("Thay đổi không hợp lệ")
        kind = op.get("op")
        if kind == "set":
            p = para(op.get("id"))
            if _lock_reason(p):
                raise BadDocx("Đoạn này không sửa trực tiếp được")
            plan.append(("set", p, _clean_runs(op.get("runs"))))
        elif kind == "insert":
            ref, where = (para(op["after"]), "after") if "after" in op else (para(op.get("before")), "before")
            plan.append((where, ref, (_clean_runs(op.get("runs")), _para_props(op))))
        elif kind == "para":
            p = para(op.get("id"))
            if _lock_reason(p):
                raise BadDocx("Đoạn này không sửa trực tiếp được")
            props = _para_props(op)
            if not props:
                raise BadDocx("Thay đổi không hợp lệ")
            plan.append(("para", p, props))
        elif kind == "delete":
            p = para(op.get("id"))
            if _lock_reason(p):
                raise BadDocx("Đoạn này không xoá trực tiếp được")
            plan.append(("delete", p, None))
        else:
            raise BadDocx("Thay đổi không hợp lệ")

    tail: dict[Any, Any] = {}   # đoạn mốc → đoạn mới thêm sau cùng (nhiều đoạn thêm sau cùng một mốc giữ đúng thứ tự)
    for kind, el, runs in plan:
        if kind == "set":
            _fill(st, el, el, runs)
        elif kind == "para":
            _set_para(el, runs)
        elif kind in ("after", "before"):
            runs, props = runs
            new = OxmlElement("w:p")
            ppr = el.find(PPR)
            if ppr is not None:
                ppr = copy.deepcopy(ppr)
                for drop in (SECT, qn("w:pPrChange")):
                    for x in ppr.findall(drop):
                        ppr.remove(x)   # ngắt phần / dấu theo dõi thay đổi thuộc về đoạn mốc, không chép
                new.append(ppr)
            _fill(st, new, el, runs)
            if props:
                _set_para(new, props)
            if kind == "after":
                tail.get(el, el).addnext(new)
                tail[el] = new
            else:
                el.addprevious(new)
        else:
            ppr = el.find(PPR)
            parent = el.getparent()
            last_in_cell = parent.tag == TC and len([x for x in parent.iterchildren(P)]) <= 1
            if last_in_cell or (ppr is not None and ppr.find(SECT) is not None):
                _fill(st, el, el, [])   # ô bảng phải còn một đoạn; đoạn mang ngắt phần giữ lại — chỉ bỏ chữ
            else:
                parent.remove(el)
    return _repack(data, doc.part.partname.lstrip("/"), doc.part.blob)


ALIGN = {"left": "left", "center": "center", "right": "right", "justify": "both"}


def _para_props(op: dict[str, Any]) -> dict[str, Any]:
    """Thuộc tính ĐOẠN người sửa đổi: ``align`` (căn lề) · ``line`` (giãn dòng, bội số 0,5–5) · ``indent`` (thụt lề trái, pt 0–400). Giá trị lạ ⇒ từ chối."""
    out: dict[str, Any] = {}
    if "align" in op:
        if op["align"] not in ALIGN:
            raise BadDocx("Căn lề không hợp lệ")
        out["align"] = ALIGN[op["align"]]
    for key, low, high, what in (("line", 0.5, 5, "Giãn dòng"), ("indent", 0, 400, "Thụt lề")):
        if key in op:
            v = op[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not low <= v <= high:
                raise BadDocx(f"{what} không hợp lệ")
            out[key] = float(v)
    return out


def _set_para(p: Any, props: dict[str, Any]) -> None:
    """Ghi thuộc tính đoạn vào ``w:pPr`` (thư viện lo thứ tự phần tử con theo lược đồ)."""
    ppr = p.get_or_add_pPr()
    if "align" in props:
        ppr.get_or_add_jc().set(VAL, props["align"])
    if "line" in props:
        sp = ppr.get_or_add_spacing()
        sp.set(qn("w:line"), str(round(props["line"] * 240)))
        sp.set(qn("w:lineRule"), "auto")
    if "indent" in props:
        ind = ppr.get_or_add_ind()
        ind.attrib.pop(qn("w:start"), None)
        ind.set(qn("w:left"), str(round(props["indent"] * 20)))


def blank() -> bytes:
    """Văn bản trắng để soạn mới: khổ A4 dọc, lề theo thể thức văn bản hành chính (trên / dưới 20 mm, trái 30 mm, phải 20 mm), chữ Times New Roman 13,
    giãn dòng 1,15, một đoạn trống để bắt đầu gõ (hoặc để trợ lý viết vào)."""
    from docx.shared import Mm, Pt

    d = Document()
    s = d.sections[0]
    s.page_width, s.page_height = Mm(210), Mm(297)
    s.top_margin, s.bottom_margin, s.left_margin, s.right_margin = Mm(20), Mm(20), Mm(30), Mm(20)
    normal: Any = d.styles["Normal"]   # kiểu đoạn (ParagraphStyle) — thư viện khai kiểu chung BaseStyle
    normal.font.name, normal.font.size = "Times New Roman", Pt(13)
    fonts = normal.element.get_or_add_rPr().get_or_add_rFonts()
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), "Times New Roman")
    normal.paragraph_format.line_spacing = 1.15
    normal.paragraph_format.space_after = Pt(6)
    d.add_paragraph("")
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def _repack(data: bytes, name: str, body: bytes) -> bytes:
    """Gói lại file gốc, CHỈ thay phần thân văn bản — mọi phần khác (kiểu, đầu / chân trang, hình, ghi chú, thuộc tính) chép nguyên byte, đúng thứ tự cũ.
    Không để thư viện ghi lại cả gói: nó chuẩn hoá từng phần và bỏ phần nó không hiểu."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            dst.writestr(info, body if info.filename == name else src.read(info), compress_type=info.compress_type)
    return out.getvalue()
