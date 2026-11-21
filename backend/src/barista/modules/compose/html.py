"""XUẤT bản soạn thành MỘT trang web một tệp (0224): chia sẻ cho khách qua link, hoặc mở / in ra PDF từ trình duyệt. Không script, không tải gì từ ngoài —
CSS nằm trong trang, ảnh nhúng dạng data URL (thu nhỏ nếu quá lớn), màu + phông + logo + chân trang từ bộ nhận diện công ty. Số trên trang CHỈ in từ
khối số liệu (ảnh chụp kết quả truy vấn, kèm dòng nguồn + lúc chụp); chữ do máy / người viết được thoát ký tự HTML.

Bố cục (bìa, cột, ô nổi bật, lưới ảnh, chân) do ``layout.sections`` quyết — cùng một hàm với màn xem trước của web và bộ xuất Word."""

import base64
import html as _html
import io
from datetime import datetime
from typing import Any

from barista.api import zone
from barista.modules.compose import check, layout, render, stock, vn

IMG_MAX_PX = 1600
IMG_SHRINK_ABOVE = 400 * 1024


def esc(s: Any) -> str:
    return _html.escape(str(s), quote=True)


def shrink(data: bytes, content_type: str) -> tuple[bytes, str]:
    """Ảnh lớn ⇒ thu nhỏ (cạnh dài ≤ 1600 px, JPEG) cho trang nhẹ. Không có Pillow / ảnh hỏng ⇒ giữ nguyên."""
    if len(data) <= IMG_SHRINK_ABOVE:
        return data, content_type
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(data))
        im.thumbnail((IMG_MAX_PX, IMG_MAX_PX))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=82, optimize=True)
        return buf.getvalue(), "image/jpeg"
    except Exception:   # ảnh lạ / hỏng: dùng bản gốc (trình duyệt tự xử lý), không làm hỏng việc xuất
        return data, content_type


def thumb(data: bytes, px: int = 480) -> str:
    """Bản thu nhỏ (JPEG, cạnh dài ≤ ``px``) dạng data URL cho màn soạn. Lỗi ⇒ chuỗi rỗng."""
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(data))
        im.thumbnail((px, px))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=75)
        return f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}"
    except Exception:
        return ""


def data_url(img: tuple[bytes, str]) -> str:
    raw, ctype = shrink(*img)
    return f"data:{ctype};base64,{base64.b64encode(raw).decode()}"


# Chú thích bắt buộc của ảnh AI (ADR 0252): in TRÊN ảnh, mọi bộ vẽ (trang thường, tạp chí, nhiều trang) dùng chung chữ + kiểu này
ILLUS_CSS = (".pw{position:relative}.illus{position:absolute;right:8px;bottom:8px;padding:2px 8px;background:rgba(0,0,0,.62);color:#fff;"
             "font:500 11px/1.4 sans-serif;letter-spacing:.04em;border-radius:4px}")


def badge(b: dict[str, Any]) -> str:
    """Nhãn "Ảnh minh hoạ" nếu khối ảnh là ảnh AI (``origin = ai``); ảnh thật ⇒ rỗng. Bộ vẽ chỉ cần đặt vào một khung ``position:relative``."""
    return f'<span class="illus">{esc(stock.LABEL)}</span>' if stock.is_ai(b) else ""


CSS = """
:root{--brand:#%(c)s;--on:#%(on)s;--tint:#%(t1)s;--tint2:#%(t2)s;--ink:#1d1d1f;--muted:#5c5c63;--line:#e3e3e8}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%%}
body{margin:0;background:#f4f4f6;color:var(--ink);font:16px/1.6 %(font)s,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
.sheet{max-width:880px;margin:0 auto;background:#fff}
@media(min-width:900px){body{padding:24px 0}.sheet{box-shadow:0 1px 3px rgba(0,0,0,.12)}}
.cover{position:relative;background:var(--brand);color:var(--on);padding:48px 40px}
.cover.hero{padding:0;min-height:340px;display:flex;align-items:flex-end;background:#222}
.cover.hero img.bg{position:absolute;inset:0;width:100%%;height:100%%;object-fit:cover}
.cover .veil{position:relative;width:100%%;padding:72px 40px 32px;background:linear-gradient(to top,rgba(0,0,0,.78),rgba(0,0,0,.05));color:#fff}
.cover h1{margin:0;font-size:clamp(28px,5vw,44px);line-height:1.15;font-weight:700;letter-spacing:-.01em}
.cover p{margin:10px 0 0;font-size:18px;max-width:640px}
.cover .logo{position:absolute;top:20px;right:24px;max-height:44px;max-width:160px;background:#fff;padding:6px 10px;border-radius:6px}
header.plain{display:flex;gap:16px;align-items:center;padding:28px 40px 8px;border-bottom:4px solid var(--brand)}
header.plain h1{margin:0;flex:1;font-size:28px;line-height:1.2;color:var(--brand)}
header.plain img{max-height:44px}
main{padding:8px 40px 24px}
@media(max-width:640px){main,header.plain{padding-left:20px;padding-right:20px}.cover{padding:32px 20px}.cover.hero .veil{padding:56px 20px 24px}}
h2{margin:28px 0 8px;font-size:22px;line-height:1.25;color:var(--brand);border-left:5px solid var(--brand);padding-left:12px}
h3{margin:20px 0 6px;font-size:18px;color:var(--brand)}
p{margin:8px 0}
mark{background:#ffe27a;color:#111;padding:0 2px;border-radius:3px}
blockquote{margin:14px 0;padding:10px 16px;border-left:4px solid var(--brand);background:var(--tint)}
blockquote cite{display:block;margin-top:4px;font-size:13px;color:var(--muted);font-style:normal}
figure{margin:14px 0}
figure img{display:block;width:100%%;height:auto;border-radius:8px}
figcaption{font-size:13px;color:var(--muted);margin-top:4px}
.data{margin:16px 0}
.data h4{margin:0 0 6px;font-size:16px}
.src{font-size:12px;color:var(--muted);margin:6px 0 0}
.tiles{display:grid;gap:12px;margin:16px 0;grid-template-columns:repeat(var(--n),minmax(0,1fr))}
@media(max-width:640px){.tiles{grid-template-columns:repeat(2,minmax(0,1fr))}}
.tile{background:var(--tint);border-radius:10px;padding:14px 16px;border-top:4px solid var(--brand)}
.tile b{display:block;font-size:24px;line-height:1.2;color:var(--brand);overflow-wrap:anywhere}
.tile b.long{font-size:16px}
.tile span{display:block;font-size:13px;color:var(--muted);margin-top:2px}
.cols{display:grid;gap:24px;margin:12px 0;grid-template-columns:repeat(var(--n),minmax(0,1fr))}
@media(max-width:640px){.cols{grid-template-columns:1fr}}
.cols>div>*:first-child{margin-top:0}
.grid{display:grid;gap:10px;margin:14px 0;grid-template-columns:repeat(var(--n),minmax(0,1fr))}
@media(max-width:640px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
.grid figure{margin:0}
.grid img{aspect-ratio:4/3;object-fit:cover}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%%;font-size:14px}
th{background:var(--brand);color:var(--on);text-align:left;font-weight:600}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tbody tr:nth-child(even) td{background:#fafafb}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
footer.contact{background:var(--brand);color:var(--on);padding:24px 40px}
footer.contact p{margin:4px 0}
footer.contact .brandline{font-size:13px;opacity:.85;margin-top:12px}
footer.plain{padding:16px 40px 28px;color:var(--muted);font-size:13px;text-align:center;border-top:1px solid var(--line)}
@page{size:A4;margin:14mm 0}
@media print{body{background:#fff;padding:0}.sheet{box-shadow:none;max-width:none}.cover,th,.tile,footer.contact{-webkit-print-color-adjust:exact;print-color-adjust:exact}.data,figure,blockquote,.tile{break-inside:avoid}h2,h3{break-after:avoid}}
"""


def _text(t: str) -> str:
    """Chữ ⇒ HTML: thoát ký tự, tô chỗ giữ "[cần số liệu: …]" (xuất bị chặn nếu còn cờ số; chỗ giữ thì chỉ cảnh báo)."""
    out, pos = [], 0
    for m in check.PLACEHOLDER.finditer(t):
        out.append(esc(t[pos:m.start()]))
        out.append(f"<mark>{esc(m.group(0))}</mark>")
        pos = m.end()
    out.append(esc(t[pos:]))
    return "".join(out).replace("\n", "<br>")


def _tile_value(v: str) -> str:
    return f'<b class="long">{esc(v)}</b>' if len(v) > 14 else f"<b>{esc(v)}</b>"


def _data(b: dict[str, Any], *, source: bool = True, style: str = "") -> str:
    d = b.get("data") or {}
    parts = ['<section class="data">']
    if d.get("title"):
        parts.append(f"<h4>{esc(d['title'])}</h4>")
    tiles = vn.tiles(d.get("kpis") or [], render.fmt, style)
    if tiles:
        parts.append(f'<div class="tiles" style="--n:{min(max(len(tiles), 1), 4)}">'
                     + "".join(f'<div class="tile">{_tile_value(k["value"])}<span>{esc(k["label"])}</span></div>' for k in tiles) + "</div>")
    cols, rows = vn.table(d, render.fmt, style)
    if cols and rows:
        head = "".join(f'<th scope="col"{" class=num" if c["num"] else ""}>{esc(c["label"])}</th>' for c in cols)
        body = "".join("<tr>" + "".join(f'<td{" class=num" if c["num"] else ""}>{esc(r[c["key"]])}</td>' for c in cols) + "</tr>" for r in rows)
        parts.append(f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')
    for t in d.get("texts") or []:
        if t.get("title"):
            parts.append(f"<h3>{esc(t['title'])}</h3>")
        parts.append(f"<p>{_text(t.get('text', ''))}</p>")
    note = (render.source_line(d) + (" · " + d["note"] if d.get("note") else "")) if source else str(d.get("note") or "")
    parts.append((f'<p class="src">{esc(note)}</p>' if note else "") + "</section>")
    return "".join(parts)


def _block(b: dict[str, Any], images: dict[str, tuple[bytes, str]], *, grid: bool = False, source: bool = True, style: str = "") -> str:
    t = b["type"]
    if t == "heading":
        return f"<h2>{_text(b['text'])}</h2>" if b.get("level", 1) == 1 else f"<h3>{_text(b['text'])}</h3>"
    if t == "paragraph":
        return f"<p>{_text(b['text'])}</p>"
    if t == "quote":
        q = b.get("quote") or {}
        return f"<blockquote>{esc(q.get('text', ''))}<cite>— {esc(q.get('cite', ''))}</cite></blockquote>"
    if t == "image":
        im = b.get("image") or {}
        alt = im.get("caption") or im.get("filename") or "Hình minh hoạ"
        img = images.get(b["id"])
        if img is None:
            return ""   # không còn quyền đọc ảnh lúc xuất ⇒ bỏ ảnh (chữ báo thiếu ảnh chỉ có ở bản Word)
        cap = f"<figcaption>{esc(im['caption'])}</figcaption>" if im.get("caption") and not grid else ""
        return f'<figure><div class="pw"><img src="{data_url(img)}" alt="{esc(alt)}" loading="lazy">{badge(b)}</div>{cap}</figure>'
    if t == "data":
        return _data(b, source=source, style=style)
    return ""


def page(title: str, blocks: list[dict[str, Any]], brand: dict[str, Any], images: dict[str, tuple[bytes, str]], lay: dict[str, Any] | None = None,
         *, when: datetime | None = None, only: int = 0) -> str:
    """Trang HTML hoàn chỉnh. ``images``: mã khối ảnh ⇒ (byte, loại). ``lay``: ``layout`` của ấn phẩm (rỗng ⇒ mẫu "Đơn giản"). ``only``: chỉ trang thứ n (mẫu nhiều trang)."""
    tpl = layout.template_of(lay) or layout.TEMPLATES["plain"]
    if tpl.get("look") == "paged":
        from barista.modules.compose import paged  # nhập muộn như editorial

        return paged.page(title, blocks, brand, images, tpl, only=only)
    if tpl.get("look") == "editorial":
        from barista.modules.compose import editorial  # nhập muộn: editorial dùng lại ``esc`` / ``data_url`` của tệp này

        return editorial.page(title, blocks, brand, images, tpl)
    customer = bool(tpl.get("customer"))   # trang gửi khách: không dòng nguồn nội bộ — một chú thích "cập nhật lúc …" ở cuối (nguồn chi tiết ở màn soạn)
    style = str(tpl.get("style", ""))
    by = {b["id"]: b for b in blocks}
    color = brand["primary_color"]
    css = CSS % {"c": color, "on": layout.on_color(color), "t1": layout.tint(color, 0.9), "t2": layout.tint(color, 0.75),
                 "font": f'"{brand["font"]}"' if brand.get("font") else "sans-serif"} + ILLUS_CSS
    logo_src = data_url((brand["logo"], brand.get("logo_type") or "image/png")) if brand.get("logo") else ""
    logo = f'<img class="logo" src="{logo_src}" alt="Logo công ty">' if logo_src else ""
    out: list[str] = []
    cover_html = ""
    foot: list[str] = []
    for s in layout.sections(blocks, tpl, title):
        k = s["kind"]
        if k == "cover":
            sub = "".join(f"<p>{_text(by[i]['text'])}</p>" for i in s["text"])
            if s["style"] == "hero" and s["hero"] and (hero := images.get(s["hero"])):
                cover_html = (f'<section class="cover hero" aria-label="Bìa"><img class="bg" src="{data_url(hero)}" alt="">{badge(by[s["hero"]])}{logo}'
                              f'<div class="veil"><h1>{esc(title)}</h1>{sub}</div></section>')
            else:
                cover_html = f'<section class="cover" aria-label="Bìa">{logo}<h1>{esc(title)}</h1>{sub}</section>'
        elif k == "flow":
            out += [_block(by[i], images, source=not customer, style=style) for i in s["ids"]]
        elif k == "columns":
            out.append(f'<div class="cols" style="--n:{s["cols"]}">' + "".join("<div>" + "".join(_block(by[i], images, source=not customer, style=style) for i in col) + "</div>"
                                                                                  for col in s["columns"]) + "</div>")
        elif k == "tiles":
            out.append(f'<div class="tiles" style="--n:{min(s["cols"], max(len(s["items"]), 1))}">'
                       + "".join(f'<div class="tile">{_tile_value(it["value"])}<span>{esc(it["label"])}</span></div>' for it in s["items"]) + "</div>")
            out += [] if customer else [f'<p class="src">{esc(render.source_line(by[i]["data"]))}</p>' for i in s["ids"]]
        elif k == "grid":
            out.append(f'<div class="grid" style="--n:{s["cols"]}">' + "".join(_block(by[i], images, grid=True) for i in s["ids"]) + "</div>")
        elif k == "footer":
            foot = [f"<p>{_text(x)}</p>" for i in s["ids"] for x in layout.footer_lines(by[i])]
    stamp = (when or datetime.now(zone())).strftime("%d/%m/%Y")
    plain_logo = f'<img src="{logo_src}" alt="Logo công ty">' if logo_src else ""
    head = "" if cover_html else f'<header class="plain"><h1>{esc(title)}</h1>{plain_logo}</header>'
    if foot or brand.get("footer"):
        tail = f'<footer class="contact">{"".join(foot)}<p class="brandline">{esc(brand.get("footer", ""))}</p></footer>'
    else:
        tail = ""
    fn = render.footnote(blocks)
    note = (f'<footer class="plain">{esc(fn)}</footer>' if fn else "") if customer else f'<footer class="plain">Lập ngày {stamp} · Số liệu in theo nguồn ghi dưới từng bảng</footer>'
    return ('<!DOCTYPE html><html lang="vi"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">'
            f'<title>{esc(title)}</title><style>{css}</style></head><body><div class="sheet">{cover_html}{head}'
            f'<main>{"".join(out)}</main>{tail}{note}</div></body></html>')
