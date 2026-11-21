"""BỐ CỤC ấn phẩm (0224) — trang mẫu là DỮ LIỆU, chỗ đứng của khối là một trường (``slot``) nằm trong khối.

* **Trang mẫu** (``TEMPLATES``): kiểu bìa, số ô nổi bật / số ảnh trên một hàng, lề. Màu, phông, logo, chân trang lấy từ nhận diện thương hiệu của CÔNG TY
  (cài đặt công ty — ``admin.brand``, ADR 0249) — mẫu không mang màu. Ấn phẩm chọn mẫu bằng ``layout = {"template": "<khoá>"}``; ``{}`` = bố cục cũ (xuất như
  trước khi có bố cục — bản cũ không đổi diện mạo). Mẫu nào hợp loại ấn phẩm nào: ``kinds.py`` (MỘT danh sách — loại quyết mẫu + định dạng).
* **Chỗ đứng** (``slot``): trường TUỲ CHỌN của khối (chỉ THÊM — khối cũ không có ⇒ chảy như thường): ``cover`` (bìa: ảnh đầu tiên = ảnh lớn, đoạn chữ =
  dòng phụ) · ``col1`` / ``col2`` / ``col3`` (các khối liên tiếp xếp thành hàng nhiều cột) · ``tiles`` (khối SỐ LIỆU chỉ có ô số ⇒ ô nổi bật) · ``grid``
  (ảnh liên tiếp ⇒ lưới ảnh) · ``footer`` (chân liên hệ cuối tờ).
* ``sections`` đổi danh sách khối thành các PHẦN bố cục (bìa, chảy, cột, ô nổi bật, lưới ảnh, chân). Một hàm thuần: máy chủ tính, web chỉ vẽ theo
  kết quả, bộ xuất HTML / Word dùng chung — cùng một bố cục ở mọi nơi. Số in ra vẫn chỉ đến từ khối số liệu (ô nổi bật là ô số của khối đó).
"""

from typing import Any

from barista.modules.compose import render, vn

SLOTS = {"": "Dòng chảy", "cover": "Bìa", "col1": "Cột 1", "col2": "Cột 2", "col3": "Cột 3", "tiles": "Ô nổi bật", "grid": "Lưới ảnh",
         "footer": "Chân liên hệ"}
COLS = ("col1", "col2", "col3")
PAGE_SLOTS = {f"p{n}": f"Trang {n}" for n in range(1, 11)}   # mẫu nhiều trang cố định (``pages``): khối đứng ở trang nào

# Trang mẫu: ``style`` kiểu hiển thị số (``vn`` = "2,85 tỷ", "48 m²" — ``vn.py``; trống = in đủ); ``customer`` = trang gửi khách (không dòng nguồn nội bộ — một chú thích
# cập nhật lúc nào); ``labels`` nhãn mục do mẫu đặt (điểm nổi bật, lưới ảnh, chân); ``fonts`` tên phông (trang web NHÚNG phông; Word chỉ đặt tên, không nhúng), ``look`` bộ giao diện của trang web (``editorial`` = kiểu tạp chí / tờ giới thiệu cao cấp,
# trống = giao diện thường). ``cover`` none (không bìa riêng) · band (dải màu thương hiệu) · hero (ảnh lớn có dải chữ); ``tile_cols`` số ô nổi bật một hàng;
# ``grid_cols`` số ảnh một hàng; ``margin_cm`` lề giấy khi xuất Word / PDF.
TEMPLATES: dict[str, dict[str, Any]] = {
    "plain": {"label": "Đơn giản", "about": "Tiêu đề + nội dung chảy, không bìa riêng", "cover": "none", "tile_cols": 3, "grid_cols": 2, "margin_cm": 2.2},
    "brochure": {"label": "Tờ giới thiệu", "about": "Bìa ảnh lớn, ô số nổi bật, lưới ảnh, chân liên hệ", "cover": "hero", "tile_cols": 4,
                 "grid_cols": 3, "margin_cm": 1.6},
    "proposal": {"label": "Đề xuất", "about": "Bìa dải màu, nội dung hai cột, bảng giá", "cover": "band", "tile_cols": 3, "grid_cols": 2, "margin_cm": 2.0,
                 "customer": True},
    "editorial": {"label": "Tạp chí (cao cấp)", "about": "Bìa ảnh tràn trang, chữ có chân, dải điểm nổi bật, tiến độ dạng thanh mốc, lưới ảnh có chú thích, thẻ liên hệ",
                  "cover": "hero", "tile_cols": 4, "grid_cols": 3, "margin_cm": 1.8, "style": vn.STYLE_VN, "customer": True, "look": "editorial", "fonts": {"display": "Cormorant Garamond", "text": "Be Vietnam Pro"},
                  "labels": {"tiles": "Điểm nổi bật", "grid": "Hình ảnh", "footer": "Liên hệ"}},
}
# 0231: tờ rơi — một trang, bìa ảnh lớn, ba lợi ích một hàng, ô ưu đãi nổi bật, chân liên hệ; lề hẹp để chữ to
TEMPLATES["flyer"] = {"label": "Tờ rơi", "about": "Một trang: bìa ảnh lớn, ba lợi ích một hàng, ô ưu đãi nổi bật, chân liên hệ", "cover": "hero", "tile_cols": 3,
                      "grid_cols": 2, "margin_cm": 1.2, "style": vn.STYLE_VN, "customer": True, "labels": {"tiles": "Ưu đãi", "footer": "Liên hệ"}}
TEMPLATES["brochure"]["customer"] = True


def _page(key: str, form: str, about: str, media: str = "none") -> dict[str, str]:
    return {"key": key, "form": form, "about": about, "media": media}


# Mẫu NHIỀU TRANG CỐ ĐỊNH (0252): mỗi trang một khuôn (``form``: cover · contents · body · close) + chỗ ảnh (``media``: right · left · none), là dữ liệu như mọi trang mẫu —
# không code riêng từng mẫu. Khối đứng vào trang bằng ``slot = "p<n>"`` (khối không nêu thì theo trang của khối liền trước); trong trang chỗ nào do LOẠI KHỐI quyết: tiêu đề đầu
# trang = tiêu đề trang (một câu kết luận), đoạn chữ = chữ, khối số liệu chỉ ô số = dải ô, khối số liệu có dòng = bảng, ảnh = ảnh, chân liên hệ (``footer``) = thẻ liên hệ ở trang ``close``.
# Khổ A4 NGANG. Trang trống bị bỏ khi vẽ (số trang tính trên trang có nội dung).
_PAGED = {"cover": "hero", "tile_cols": 4, "grid_cols": 2, "margin_cm": 0, "style": vn.STYLE_VN, "customer": True, "look": "paged", "landscape": True,
          "fonts": {"display": "Cormorant Garamond", "text": "Be Vietnam Pro"}, "labels": {"tiles": "Điểm nổi bật", "grid": "Hình ảnh", "footer": "Liên hệ"}}
TEMPLATES["memo10"] = {**_PAGED, "label": "Hồ sơ chào bán 10 trang", "kicker": "Hồ sơ chào bán", "about": "A4 ngang, 10 trang cố định: bìa · bảo mật + mục lục · tóm tắt · điểm nổi bật · vị trí · tài sản · thị trường · so sánh · tài chính chỉ báo · quy trình + liên hệ",
                       "pages": (_page("p1", "cover", "Bìa: ảnh lớn, tên tài sản (tiêu đề), một câu phụ, tối đa 4 ô số nổi bật"),
                                 _page("p2", "contents", "Bảo mật + mục lục: một đoạn lưu ý bảo mật và nguồn số liệu (mục lục tự dựng từ tiêu đề các trang)", "right"),
                                 _page("p3", "body", "Tóm tắt: 1 câu kết luận làm tiêu đề, 2 đoạn ngắn, 3–4 ô số"),
                                 _page("p4", "body", "Điểm nổi bật: tiêu đề kết luận, một dải 6 ô số / điểm, một ảnh dải ngang"),
                                 _page("p5", "body", "Vị trí: 1 ảnh, 2 đoạn, bảng khoảng cách / kết nối", "right"),
                                 _page("p6", "body", "Mô tả tài sản: ảnh + bảng thông số", "left"),
                                 _page("p7", "body", "Thị trường: 3 ô số, 1–2 đoạn, một ảnh", "right"),
                                 _page("p8", "body", "Tài sản so sánh: bảng"),
                                 _page("p9", "body", "Tài chính chỉ báo: ô số + bảng (số ước tính ghi rõ)"),
                                 _page("p10", "close", "Quy trình + liên hệ: các bước tiếp theo, chân liên hệ"))}
TEMPLATES["teaser4"] = {**_PAGED, "label": "Tóm tắt 4 trang", "kicker": "Tóm tắt chào bán", "about": "A4 ngang, 4 trang cố định: bìa · tổng quan + điểm nổi bật · vị trí + thị trường · tài chính chỉ báo + bước tiếp theo",
                        "pages": (_page("p1", "cover", "Bìa: ảnh lớn, tên tài sản (tiêu đề), một câu phụ, tối đa 4 ô số nổi bật"),
                                  _page("p2", "body", "Tổng quan: tiêu đề kết luận, 2 đoạn, 4 ô điểm nổi bật, một ảnh", "right"),
                                  _page("p3", "body", "Vị trí và thị trường: ảnh, đoạn, bảng / ô số", "left"),
                                  _page("p4", "close", "Tài chính chỉ báo + bước tiếp theo: ô số, bảng, chân liên hệ"))}


def clean(layout: Any) -> dict[str, Any]:
    """Bố cục lưu được: ``{}`` hoặc ``{"template": <khoá đã khai>}`` — khoá lạ bị bỏ (không tin đầu vào)."""
    if isinstance(layout, dict) and layout.get("template") in TEMPLATES:
        return {"template": layout["template"]}
    return {}


def template_of(layout: Any) -> dict[str, Any] | None:
    """Trang mẫu của bản soạn; ``None`` = bố cục cũ."""
    key = clean(layout).get("template")
    return TEMPLATES[key] if key else None


def valid_slot(s: str) -> bool:
    return s in SLOTS or s in PAGE_SLOTS


def slot_of(b: dict[str, Any]) -> str:
    s = b.get("slot") or ""
    return s if valid_slot(s) else ""


def slot_options(tpl: dict[str, Any] | None) -> dict[str, str]:
    """Chỗ đứng chọn được của một trang mẫu: mẫu nhiều trang ⇒ các trang của nó + chân liên hệ; mẫu thường ⇒ ``SLOTS``."""
    if tpl and tpl.get("pages"):
        return {**{p["key"]: PAGE_SLOTS[p["key"]] for p in tpl["pages"]}, "footer": SLOTS["footer"]}
    return dict(SLOTS)


def _tiles_ok(b: dict[str, Any]) -> bool:
    d = b.get("data") or {}
    return b["type"] == "data" and bool(d.get("kpis")) and not d.get("rows")


def _region(b: dict[str, Any]) -> str:
    s = slot_of(b)
    if s in COLS:
        return "columns"
    if s == "tiles" and _tiles_ok(b):
        return "tiles"
    if s == "grid" and b["type"] == "image":
        return "grid"
    return "flow"


def _rows(run: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Các khối cột liên tiếp ⇒ hàng. Nhiều khối cùng cột liền nhau là CÙNG một ô (tiêu đề + đoạn chữ); sang hàng mới khi số cột LÙI (col2 rồi col1)."""
    out: list[list[dict[str, Any]]] = []
    last = 0
    for b in run:
        n = COLS.index(slot_of(b)) + 1
        if not out or n < last:
            out.append([])
        out[-1].append(b)
        last = n
    secs = []
    for row in out:
        cols = max(COLS.index(slot_of(b)) + 1 for b in row)
        secs.append({"kind": "columns", "cols": cols,
                     "columns": [[b["id"] for b in row if slot_of(b) == COLS[i]] for i in range(cols)]})
    return secs


def sections(blocks: list[dict[str, Any]], tpl: dict[str, Any], title: str = "") -> list[dict[str, Any]]:
    """Khối ⇒ các phần bố cục theo thứ tự đọc: bìa (nếu có) · phần thân · chân. Mọi khối xuất hiện đúng một lần (kiểm bằng test). ``title``: tiêu đề bản (đã
    in lớn ở bìa) — ô nổi bật có đúng giá trị đó bị bỏ, khỏi lặp tên dự án."""
    if tpl.get("pages"):
        return paged_sections(blocks, tpl)
    cover = [b for b in blocks if slot_of(b) == "cover"]
    foot = [b for b in blocks if slot_of(b) == "footer"]
    body = [b for b in blocks if slot_of(b) not in ("cover", "footer")]
    out: list[dict[str, Any]] = []
    if tpl["cover"] != "none" or cover:
        hero = next((b for b in cover if b["type"] == "image"), None)
        sub = [b for b in cover if b["type"] in ("paragraph", "heading")]
        body = [b for b in cover if b is not hero and b not in sub] + body   # khối số liệu / trích dẫn đặt nhầm ở bìa ⇒ vào đầu thân
        style = tpl["cover"] if tpl["cover"] != "none" else "band"
        out.append({"kind": "cover", "style": style if hero or style == "band" else "band", "hero": hero["id"] if hero else "",
                    "text": [b["id"] for b in sub]})
    i = 0
    while i < len(body):
        region = _region(body[i])
        j = i
        while j < len(body) and _region(body[j]) == region:
            j += 1
        run = body[i:j]
        if region == "columns":
            out += _rows(run)
        elif region == "tiles":
            style = str(tpl.get("style", ""))
            items = [{"block": b["id"], **t} for b in run for t in vn.tiles(b["data"]["kpis"], render.fmt, style, skip_values=(title,) if title else ())]
            out.append({"kind": "tiles", "cols": tpl["tile_cols"], "items": items, "ids": [b["id"] for b in run],
                        "title": str((tpl.get("labels") or {}).get("tiles", ""))})
        elif region == "grid":
            out.append({"kind": "grid", "cols": tpl["grid_cols"], "ids": [b["id"] for b in run], "title": str((tpl.get("labels") or {}).get("grid", ""))})
        else:
            out.append({"kind": "flow", "ids": [b["id"] for b in run]})
        i = j
    if foot:
        out.append({"kind": "footer", "ids": [b["id"] for b in foot], "lines": {b["id"]: footer_lines(b) for b in foot},
                    "title": str((tpl.get("labels") or {}).get("footer", ""))})   # dòng chữ dựng ở máy chủ — web chỉ vẽ
    return out


def paged_sections(blocks: list[dict[str, Any]], tpl: dict[str, Any]) -> list[dict[str, Any]]:
    """Mẫu nhiều trang: mỗi trang của mẫu ⇒ một phần ``page`` (``ids`` = khối của trang theo thứ tự). Khối nêu ``slot`` trang nào thì vào trang đó; khối không nêu (hoặc nêu chỗ
    không thuộc mẫu) theo trang của khối liền trước (đầu bản ⇒ trang 1); chân liên hệ vào trang ``close`` cuối (không có ⇒ trang cuối). Trang không có khối bị bỏ. Mọi khối xuất hiện đúng một lần."""
    pages = tpl["pages"]
    keys = [p["key"] for p in pages]
    by_page: dict[str, list[str]] = {k: [] for k in keys}
    foot: list[str] = []
    cur = keys[0]
    for b in blocks:
        s = slot_of(b)
        if s == "footer":
            foot.append(b["id"])
            continue
        if s in by_page:
            cur = s
        by_page[cur].append(b["id"])
    close = next((p["key"] for p in reversed(pages) if p["form"] == "close"), keys[-1])
    by_page[close] += foot
    live = [p for p in pages if by_page[p["key"]]]
    return [{"kind": "page", "key": p["key"], "n": i + 1, "of": len(live), "form": p["form"], "media": p["media"], "about": p["about"], "ids": by_page[p["key"]]}
            for i, p in enumerate(live)]


def block_ids(secs: list[dict[str, Any]]) -> list[str]:
    """Mọi mã khối mà các phần bố cục nhắc tới (kiểm: đủ khối, không thừa, không lặp)."""
    ids: list[str] = []
    for s in secs:
        if s["kind"] == "cover":
            ids += ([s["hero"]] if s["hero"] else []) + s["text"]
        elif s["kind"] == "columns":
            ids += [i for col in s["columns"] for i in col]
        else:
            ids += s["ids"]
    return ids


def footer_lines(b: dict[str, Any]) -> list[str]:
    """Khối đứng ở chân liên hệ ⇒ các dòng chữ: đoạn / tiêu đề / trích dẫn nguyên văn; khối số liệu (vd thông tin chuyên viên) ⇒ tên + ``nhãn: giá trị`` + đoạn chữ."""
    t = b["type"]
    if t in ("paragraph", "heading"):
        return [b["text"]]
    if t == "quote":
        return [(b.get("quote") or {}).get("text", "")]
    if t == "data":
        d = b.get("data") or {}
        out = [str(d["title"])] if d.get("title") else []
        out += [f"{k.get('label', '')}: {render.fmt(k.get('value'), k.get('format', 'text'))}" for k in d.get("kpis") or []]
        out += [str(x.get("text", "")) for x in d.get("texts") or []]
        return [x for x in out if x]
    return []


def tint(hex6: str, amount: float) -> str:
    """Pha màu với trắng (``amount`` 0..1) — màu nền nhạt của ô / dải từ màu chính, không cần khai thêm màu."""
    r, g, b = int(hex6[0:2], 16), int(hex6[2:4], 16), int(hex6[4:6], 16)
    mix = [round(c + (255 - c) * amount) for c in (r, g, b)]
    return "".join(f"{c:02X}" for c in mix)


def on_color(hex6: str) -> str:
    """Chữ đen hay trắng đọc rõ hơn trên nền ``hex6`` (tỷ lệ tương phản WCAG: chọn bên cao hơn)."""
    def lin(c: int) -> float:
        x = c / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    lum = 0.2126 * lin(int(hex6[0:2], 16)) + 0.7152 * lin(int(hex6[2:4], 16)) + 0.0722 * lin(int(hex6[4:6], 16))
    return "111111" if (lum + 0.05) / 0.05 > 1.05 / (lum + 0.05) else "FFFFFF"


def shade(hex6: str, amount: float) -> str:
    """Pha màu với đen (``amount`` 0..1) — màu sâu của trang cao cấp từ màu chính của bộ nhận diện."""
    r, g, b = int(hex6[0:2], 16), int(hex6[2:4], 16), int(hex6[4:6], 16)
    return "".join(f"{round(c * (1 - amount)):02X}" for c in (r, g, b))


def mix(a: str, b: str, t: float) -> str:
    """Pha hai màu hex (t = 0 ⇒ a, 1 ⇒ b)."""
    ca = [int(a[i:i + 2], 16) for i in (0, 2, 4)]
    cb = [int(b[i:i + 2], 16) for i in (0, 2, 4)]
    return "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(ca, cb, strict=True))


def _lum(hex6: str) -> float:
    def lin(c: int) -> float:
        x = c / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(int(hex6[0:2], 16)) + 0.7152 * lin(int(hex6[2:4], 16)) + 0.0722 * lin(int(hex6[4:6], 16))


def readable_on_white(hex6: str, ratio: float = 4.5) -> str:
    """Màu chính đủ tương phản (≥ ``ratio``) trên nền trắng: quá nhạt ⇒ đậm dần cho tới khi đủ — chữ nhãn / số theo màu thương hiệu luôn đọc được."""
    out = hex6
    for _ in range(12):
        if 1.05 / (_lum(out) + 0.05) >= ratio:
            break
        out = shade(out, 0.12)
    return out
