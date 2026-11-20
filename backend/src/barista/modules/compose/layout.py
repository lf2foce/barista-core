"""BỐ CỤC bản soạn (0224) — trang mẫu là DỮ LIỆU, chỗ đứng của khối là một trường (``slot``) nằm trong khối.

* **Trang mẫu** (``TEMPLATES``): kiểu bìa, số ô nổi bật / số ảnh trên một hàng, lề. Màu, phông, logo, chân trang lấy từ bộ nhận diện công ty
  (``compose.brand``) — mẫu không mang màu. Bản soạn chọn mẫu bằng ``layout = {"template": "<khoá>"}``; ``{}`` = bố cục cũ (xuất như trước khi có
  bố cục — bản cũ không đổi diện mạo).
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

# Trang mẫu: ``style`` kiểu hiển thị số (``vn`` = "2,85 tỷ", "48 m²" — ``vn.py``; trống = in đủ); ``customer`` = trang gửi khách (không dòng nguồn nội bộ — một chú thích
# cập nhật lúc nào); ``labels`` nhãn mục do mẫu đặt (điểm nổi bật, lưới ảnh, chân); ``fonts`` tên phông (trang web NHÚNG phông; Word chỉ đặt tên, không nhúng), ``look`` bộ giao diện của trang web (``editorial`` = kiểu tạp chí / tờ giới thiệu cao cấp,
# trống = giao diện thường). ``cover`` none (không bìa riêng) · band (dải màu thương hiệu) · hero (ảnh lớn có dải chữ); ``tile_cols`` số ô nổi bật một hàng;
# ``grid_cols`` số ảnh một hàng; ``margin_cm`` lề giấy khi xuất Word / PDF.
TEMPLATES: dict[str, dict[str, Any]] = {
    "plain": {"label": "Văn bản", "about": "Tiêu đề + nội dung chảy, không bìa riêng", "cover": "none", "tile_cols": 3, "grid_cols": 2, "margin_cm": 2.2},
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
DEFAULT_FOR_KIND = {"brochure": "editorial", "flyer": "flyer"}   # bản soạn MỚI loại "Tờ giới thiệu" mặc định dùng mẫu này; loại khác: không bố cục (như cũ)


def clean(layout: Any) -> dict[str, Any]:
    """Bố cục lưu được: ``{}`` hoặc ``{"template": <khoá đã khai>}`` — khoá lạ bị bỏ (không tin đầu vào)."""
    if isinstance(layout, dict) and layout.get("template") in TEMPLATES:
        return {"template": layout["template"]}
    return {}


def template_of(layout: Any) -> dict[str, Any] | None:
    """Trang mẫu của bản soạn; ``None`` = bố cục cũ."""
    key = clean(layout).get("template")
    return TEMPLATES[key] if key else None


def slot_of(b: dict[str, Any]) -> str:
    s = b.get("slot") or ""
    return s if s in SLOTS else ""


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


def choices() -> list[dict[str, str]]:
    return [{"key": k, "label": t["label"], "about": t["about"]} for k, t in TEMPLATES.items()]


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
