"""TRANG WEB kiểu TẠP CHÍ / tờ giới thiệu bất động sản cao cấp (0224 §8): bìa ảnh tràn trang, tên dự án bằng chữ có chân, nhãn nhỏ giãn chữ, đường kẻ mảnh, nhiều khoảng trắng.
Dải "Điểm nổi bật" thay ô số kiểu bảng điều khiển; bảng căn thoáng; lịch thanh toán là THANH MỐC nằm ngang; lưới ảnh có chú thích; thẻ liên hệ của chuyên viên.

Học PHONG CÁCH của tờ giới thiệu hãng môi giới lớn — không chép tên, logo, màu hay chữ của bất kỳ hãng nào. Bảng màu rút từ bộ nhận diện công ty (``compose.brand``): màu SÂU = màu chính
pha đen, màu NHẤN = màu chính (đủ tương phản trên nền sáng), nền giấy ấm + đường kẻ trung tính — công ty chưa đặt bộ nhận diện thì dùng màu mặc định (xanh navy). Phông NHÚNG, giống nhau trên mọi máy / trình duyệt / PDF in: Cormorant Garamond (tiêu đề, số lớn) + Be Vietnam Pro (nhãn, bảng, chữ nhỏ — thiết kế riêng cho tiếng Việt), cả hai SIL OFL 1.1,
cắt còn Latin + tiếng Việt, woff2, nhúng dạng data URL (``fonts/``: file + giấy phép + cách dựng lại — không tải từ Google Fonts). Phông của bộ nhận diện (Arial…) chỉ dùng cho Word / PowerPoint;
trang này lấy MÀU từ bộ nhận diện, không lấy phông. CSP trang chia sẻ có ``font-src data:``.

Một tệp, không script, không tải gì từ ngoài. In ra PDF từ trình duyệt: A4 dọc, bìa tràn trang, mỗi mục lớn một trang, bảng không bị cắt giữa dòng. Điện thoại: một cột, dải điểm nổi bật hai cột."""

import base64
from functools import lru_cache
from pathlib import Path
from typing import Any

from barista.modules.compose import check, layout, render, vn
from barista.modules.compose.html import ILLUS_CSS, badge, data_url, esc

SERIF = '"Cormorant Garamond","Iowan Old Style","Palatino Linotype",Palatino,Georgia,"Times New Roman",serif'
SANS = '"Be Vietnam Pro",-apple-system,"Segoe UI","Helvetica Neue",Arial,sans-serif'
FONT_DIR = Path(__file__).parent / "fonts"
# phông NHÚNG (SIL OFL 1.1, giấy phép nằm cạnh ở ``fonts/OFL-*.txt``), đã cắt còn Latin + tiếng Việt: (họ, file, độ đậm)
FONTS = (("Cormorant Garamond", "CormorantGaramond-Medium-vi.woff2", 500), ("Be Vietnam Pro", "BeVietnamPro-Regular-vi.woff2", 400),
         ("Be Vietnam Pro", "BeVietnamPro-SemiBold-vi.woff2", 600))


@lru_cache(maxsize=1)
def font_css() -> str:
    """``@font-face`` với phông nhúng dạng data URL — người xem nào (Android, Windows, iOS, PDF in từ trình duyệt, tệp lưu offline) cũng thấy đúng một bộ chữ, không tải gì từ ngoài."""
    return "".join(f'@font-face{{font-family:"{fam}";font-weight:{w};font-style:normal;font-display:block;'
                   f'src:url(data:font/woff2;base64,{base64.b64encode((FONT_DIR / f).read_bytes()).decode()}) format("woff2")}}' for fam, f, w in FONTS)

CSS = """
:root{--deep:#__DEEP__;--accent:#__ACCENT__;--on-deep:#FFFFFF;--paper:#F7F4EE;--ink:#1D1B18;--mute:#6B665D;--line:#D9D3C7;--serif:__SERIF__;--sans:__SANS__}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:400 15px/1.7 var(--sans);font-synthesis:none;-webkit-font-smoothing:antialiased}
.sheet{background:#fff;max-width:1100px;margin:0 auto}
@media(min-width:1140px){body{padding:32px 0}.sheet{box-shadow:0 2px 24px rgba(40,30,10,.10)}}
.wrap{padding:0 clamp(20px,6vw,72px)}
.eyebrow,.cap,th,dt,.lbl{font-family:var(--sans);font-size:10.5px;font-weight:600;letter-spacing:.2em;text-transform:uppercase;color:var(--accent)}
/* bìa */
.cover{position:relative;display:flex;align-items:flex-end;min-height:min(92vh,820px);color:#fff;background:var(--deep);overflow:hidden}
.cover img.bg{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.cover .shade{position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.28) 0%,rgba(0,0,0,0) 30%,rgba(8,10,14,.82) 100%)}
.cover.plainbg .shade{background:radial-gradient(ellipse at 20% 0%,rgba(255,255,255,.10),rgba(0,0,0,0) 60%)}
.cover .logo{position:absolute;top:clamp(20px,4vw,40px);left:clamp(20px,6vw,72px);max-height:44px;max-width:170px;background:#fff;padding:8px 12px}
.cover .in{position:relative;width:100%;padding:0 clamp(20px,6vw,72px) clamp(36px,7vw,84px)}
.cover .rule{display:block;width:56px;height:1px;background:rgba(255,255,255,.75);margin:0 0 22px}
.cover h1{margin:0;font:500 clamp(42px,8vw,84px)/1.02 var(--serif);letter-spacing:-.01em;max-width:16ch;text-wrap:balance}
.cover .loc{margin:22px 0 0;font:400 12px/1.6 var(--sans);letter-spacing:.2em;text-transform:uppercase;color:rgba(255,255,255,.88)}
.cover .loc+.loc{margin-top:6px;letter-spacing:.06em;text-transform:none;font-size:20px;font-family:var(--serif);font-weight:500}
/* mục */
.chap{padding:clamp(40px,7vw,80px) 0 0}
.chap:last-of-type{padding-bottom:0}
h2.sec{display:flex;align-items:baseline;gap:18px;margin:0 0 clamp(20px,3vw,34px);font:500 clamp(30px,4.6vw,44px)/1.12 var(--serif);color:var(--deep)}
h2.sec .n{font:600 11px var(--sans);letter-spacing:.2em;color:var(--accent)}
h2.sec::after{content:"";flex:1;height:1px;background:var(--line);align-self:center;margin-left:6px}
h3.sub{margin:0 0 14px;font:600 10.5px var(--sans);letter-spacing:.2em;text-transform:uppercase;color:var(--accent)}
p{margin:0 0 12px}
.lede{font:500 clamp(20px,2.6vw,25px)/1.5 var(--serif);color:var(--deep);max-width:60ch}
/* dải điểm nổi bật */
.strip{display:grid;grid-template-columns:repeat(var(--n),minmax(0,1fr));margin:0;border-top:1px solid var(--deep);border-bottom:1px solid var(--line)}
.strip>div{padding:22px 20px 22px 0;margin-right:20px;border-right:1px solid var(--line)}
.strip>div:last-child{border-right:0;margin-right:0}
.strip dt{margin:0 0 10px;color:var(--mute)}
.strip dd{margin:0;font:500 clamp(28px,3.8vw,40px)/1.1 var(--serif);color:var(--deep);overflow-wrap:anywhere}
.strip dd.txt{font-size:clamp(20px,2.4vw,26px);line-height:1.25}
/* bảng căn */
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:15px}
th{text-align:left;color:var(--mute);padding:0 14px 12px 0;border-bottom:1px solid var(--deep);white-space:nowrap}
td{padding:18px 14px 18px 0;border-bottom:1px solid var(--line);vertical-align:baseline;font:400 14.5px/1.4 var(--sans);color:#2c2925}
td:first-child{font:500 22px/1.25 var(--serif);color:var(--deep)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;padding-left:14px}
th:last-child,td:last-child{padding-right:0}
tr{break-inside:avoid}
.note{margin:14px 0 0;font:400 13px/1.5 var(--sans);color:var(--mute)}
/* tiến độ thanh toán */
.plan .bar{display:flex;gap:3px;height:56px}
.plan .bar>span{display:flex;align-items:center;justify-content:center;min-width:44px;font:500 22px var(--serif);color:var(--c)}
.plan ol{list-style:none;margin:18px 0 0;padding:0;display:grid;grid-template-columns:repeat(var(--n),minmax(0,1fr));gap:18px}
.plan li{border-top:1px solid var(--line);padding-top:12px;font-size:14px;line-height:1.45}
.plan li b{display:block;font:500 28px/1.15 var(--serif);color:var(--deep)}
.plan li i{display:block;font-style:normal;color:var(--mute);font-size:13px;margin-top:2px}
.plan li::before{content:"";display:block;width:8px;height:8px;border-radius:50%;background:var(--c);margin-bottom:10px}
/* văn bản chính sách */
.prose p,.prose li{font:400 15px/1.8 var(--sans);color:#2c2925}
.prose ul{margin:0 0 12px;padding-left:20px}
.cols{display:grid;gap:clamp(28px,5vw,64px);grid-template-columns:repeat(var(--n),minmax(0,1fr))}
.cols.stack{grid-template-columns:1fr;gap:clamp(28px,4vw,44px)}
.cols>div>*:last-child{margin-bottom:0}
/* ảnh */
.mosaic{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:clamp(10px,1.6vw,18px)}
.mosaic figure{margin:0;grid-column:span var(--s)}
.mosaic img{display:block;width:100%;height:auto;aspect-ratio:var(--r,4/3);object-fit:cover;background:var(--paper)}
.mosaic figure:first-child img{--r:16/9}
.mosaic figcaption{margin-top:8px;font:400 12.5px/1.45 var(--sans);color:var(--mute)}
figure.one img{display:block;width:100%;height:auto}
blockquote{margin:0 0 16px;padding:0 0 0 20px;border-left:1px solid var(--accent);font:400 14px/1.75 var(--sans);color:#2c2925}
blockquote cite{display:block;margin-top:6px;font:600 10.5px var(--sans);letter-spacing:.14em;text-transform:uppercase;color:var(--mute)}
mark{background:#ffe27a;color:#111;padding:0 2px}
/* thẻ liên hệ */
.contact{margin-top:clamp(48px,8vw,96px);background:var(--deep);color:#fff;padding:clamp(32px,5vw,56px) clamp(20px,6vw,72px)}
.contact .eyebrow{color:rgba(255,255,255,.7)}
.contact .invite{margin:14px 0 0;max-width:30ch;font:500 clamp(26px,3.4vw,38px)/1.2 var(--serif)}
.contact .who{margin-top:28px;padding-top:24px;border-top:1px solid rgba(255,255,255,.28);display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:20px 32px}
.contact .who div{min-width:0}
.contact .who dt{color:rgba(255,255,255,.62);margin-bottom:6px}
.contact .who dd{margin:0;font:500 24px/1.3 var(--serif);overflow-wrap:anywhere}
.contact .who dd.name{font-size:32px}
.contact .who dd.num{font:400 17px/1.5 var(--sans);font-variant-numeric:tabular-nums}
.contact a{color:inherit;text-decoration:none;border-bottom:1px solid rgba(255,255,255,.4)}
.fine{padding:20px clamp(20px,6vw,72px) 32px;font:400 12px/1.6 var(--sans);color:var(--mute)}
.fine p{margin:0 0 4px}
@media(max-width:640px){
 .strip{grid-template-columns:repeat(2,minmax(0,1fr))}
 .strip>div{padding:18px 12px 18px 0;margin-right:12px}
 .strip>div:nth-child(2n){border-right:0;margin-right:0}
 .strip>div:nth-child(n+3){border-top:1px solid var(--line)}
 .cols{grid-template-columns:1fr}
 .plan ol{grid-template-columns:1fr 1fr}
 .plan .bar{height:44px}.plan .bar>span{font-size:19px;min-width:34px}
 .mosaic figure{grid-column:span 6!important}
 th{letter-spacing:.06em;font-size:9px;padding-right:6px;white-space:normal;vertical-align:bottom}td{padding:14px 6px 14px 0;font-size:13.5px}td:first-child{font-size:19px}td.num{white-space:nowrap}td.num,th.num{padding-left:6px}th:last-child,td:last-child{padding-right:0}
 h2.sec::after{display:none}
}
/* số lining (Cormorant mặc định là số kiểu cổ — giá / số căn phải đều chiều cao); đặt SAU mọi `font:` rút gọn vì `font:` xoá font-variant */
h1,h2,.lede,.strip dd,.plan li b,.plan .bar>span,td:first-child,.invite,.who dd.name,.loc{font-variant-numeric:lining-nums}
td.num{font-variant-numeric:tabular-nums}
@page{size:A4;margin:16mm 0}
@page:first{margin:0}
@media print{
 body{background:#fff;padding:0;font-size:12px}
 .sheet{box-shadow:none;max-width:none}
 .cover{min-height:297mm;height:297mm;break-after:page}
 .cover,.cover *,.contact,.plan .bar>span,.plan li::before,.strip{-webkit-print-color-adjust:exact;print-color-adjust:exact}
 .chap{padding-top:10mm;break-inside:avoid}
 .chap.pb{break-before:page;padding-top:0}
 .contact{break-inside:avoid;margin-top:8mm;padding-top:8mm;padding-bottom:8mm}
 .contact .who{margin-top:14px;padding-top:14px}
 .strip dd{font-size:25px}.strip dd.txt{font-size:19px}
 .mosaic figure:first-child img{--r:21/9}
 .mosaic figure img{max-height:68mm}
 .mosaic figure,.plan,table,blockquote{break-inside:avoid}
 h2.sec{break-after:avoid}
 .fine{padding-bottom:0}
}
"""


def _paras(text: str) -> str:
    """Đoạn chữ nguyên văn (chính sách…): dòng bắt đầu ``-`` / ``•`` / ``*`` thành gạch đầu dòng, còn lại là đoạn; thoát ký tự, tô chỗ giữ."""
    out: list[str] = []
    items: list[str] = []

    def flush() -> None:
        if items:
            out.append("<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>")
            items.clear()

    for line in (ln.strip() for ln in text.split("\n")):
        if not line:
            continue
        body = _txt(line.lstrip("-•* ").strip()) if line[0] in "-•*" else None
        if body is not None:
            items.append(body)
        else:
            flush()
            out.append(f"<p>{_txt(line)}</p>")
    flush()
    return "".join(out)


def _txt(t: str) -> str:
    out, pos = [], 0
    for m in check.PLACEHOLDER.finditer(t):
        out.append(esc(t[pos:m.start()]))
        out.append(f"<mark>{esc(m.group(0))}</mark>")
        pos = m.end()
    out.append(esc(t[pos:]))
    return "".join(out)


def _strip(items: list[dict[str, str]], cols: int) -> str:
    n = min(max(len(items), 1), cols)
    cells = "".join(f'<div><dt>{esc(i["label"])}</dt><dd class="{"num" if any(ch.isdigit() for ch in i["value"]) else "txt"}">{esc(i["value"])}</dd></div>' for i in items)
    return f'<dl class="strip" style="--n:{n}">{cells}</dl>'


def _bar(rows: list[dict[str, Any]], accent: str, deep: str) -> str:
    """Lịch thanh toán ⇒ thanh mốc nằm ngang (độ rộng mỗi đợt theo tỷ lệ) + chú thích từng đợt; màu đậm dần từ nhạt tới sâu."""
    n = len(rows)
    cols = [layout.mix(layout.tint(accent, 0.62), deep, i / max(n - 1, 1)) for i in range(n)]
    segs = "".join(f'<span style="flex:{r["pct"]:g} 1 0;background:#{c};--c:#{layout.on_color(c)}">{esc(r["text"])}</span>' for r, c in zip(rows, cols, strict=True))
    lis = "".join(f'<li style="--c:#{c}"><b>{esc(r["text"])}</b>{esc(r["label"])}' + (f'<i>{esc(r["after"])}</i>' if r["after"] else "") + "</li>"
                  for r, c in zip(rows, cols, strict=True))
    return f'<div class="plan"><div class="bar" role="img" aria-label="Tiến độ thanh toán theo đợt">{segs}</div><ol style="--n:{min(n, 6)}">{lis}</ol></div>'


def _table(data: dict[str, Any]) -> str:
    cols, rows = vn.table(data, render.fmt, vn.STYLE_VN)
    if not (cols and rows):
        return ""
    head = "".join(f'<th scope="col"{" class=num" if c["num"] else ""}>{esc(c["label"])}</th>' for c in cols)
    body = "".join("<tr>" + "".join(f'<td{" class=num" if c["num"] else ""}>{esc(r[c["key"]])}</td>' for c in cols) + "</tr>" for r in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _data(b: dict[str, Any], tpl: dict[str, Any], accent: str, deep: str) -> str:
    """Khối số liệu ⇒ HTML. Không có dòng "Nguồn: …" — chú thích cập nhật là một dòng ở cuối trang."""
    d = b.get("data") or {}
    parts: list[str] = []
    if d.get("title"):
        parts.append(f'<h3 class="sub">{esc(d["title"])}</h3>')
    if d.get("kpis"):
        parts.append(_strip(vn.tiles(d["kpis"], render.fmt, vn.STYLE_VN), int(tpl["tile_cols"])))
    parts.append(_bar(plan, accent, deep) if (plan := vn.plan(d)) else _table(d))
    for t in d.get("texts") or []:
        if t.get("title"):
            parts.append(f'<h3 class="sub">{esc(t["title"])}</h3>')
        parts.append(f'<div class="prose">{_paras(t.get("text", ""))}</div>')
    if d.get("note"):
        parts.append(f'<p class="note">{esc(d["note"])}</p>')
    return "".join(parts)


def _mosaic(figs: list[tuple[str, str, str, str]]) -> str:
    """Lưới ảnh: ảnh đầu rộng cả hàng, các ảnh sau chia hàng 3 (hoặc 2) — ``figs``: (data url, chữ thay thế, chú thích, nhãn ảnh AI hoặc rỗng)."""
    spans, rest = [6], len(figs) - 1
    while rest > 0:
        take = 2 if rest in (2, 4) else 1 if rest == 1 else 3
        spans += [6 // take] * take
        rest -= take
    cells = []
    for (src, alt, cap, tag), sp in zip(figs, spans, strict=False):
        c = f"<figcaption>{esc(cap)}</figcaption>" if cap else ""
        cells.append(f'<figure style="--s:{sp}"><div class="pw"><img src="{src}" alt="{esc(alt)}" loading="lazy">{tag}</div>{c}</figure>')
    return f'<div class="mosaic">{"".join(cells)}</div>'


def _flow(ids: list[str], by: dict[str, dict[str, Any]], images: dict[str, tuple[bytes, str]], tpl: dict[str, Any], accent: str, deep: str,
          num: list[int]) -> list[str]:
    """Khối chảy ⇒ các mục: tiêu đề mức 1 mở mục mới (có số thứ tự); khối trước tiêu đề đầu tiên thuộc mục đang mở."""
    chaps: list[tuple[str, list[str]]] = []   # (tiêu đề mục, các phần)
    for i in ids:
        b = by[i]
        if b["type"] == "heading" and b.get("level", 1) == 1:
            num[0] += 1
            chaps.append((f'<h2 class="sec"><span class="n">{num[0]:02d}</span>{_txt(b["text"])}</h2>', []))
            continue
        if not chaps:
            chaps.append(("", []))
        t = b["type"]
        if t == "heading":
            chaps[-1][1].append(f'<h3 class="sub">{_txt(b["text"])}</h3>')
        elif t == "paragraph":
            chaps[-1][1].append(f'<p class="lede">{_txt(b["text"])}</p>')
        elif t == "quote":
            q = b.get("quote") or {}
            chaps[-1][1].append(f'<blockquote>{esc(q.get("text", ""))}<cite>{esc(q.get("cite", ""))}</cite></blockquote>')
        elif t == "data":
            chaps[-1][1].append(_data(b, tpl, accent, deep))
        elif t == "image" and (img := images.get(b["id"])):
            cap = (b.get("image") or {}).get("caption", "")
            chaps[-1][1].append(f'<figure class="one"><div class="pw"><img src="{data_url(img)}" alt="{esc(cap or "Hình minh hoạ")}" loading="lazy">{badge(b)}</div></figure>')
    return [f'<section class="chap">{h}{"".join(p)}</section>' for h, p in chaps if h or p]


def _contact(ids: list[str], by: dict[str, dict[str, Any]], label: str) -> str:
    """Thẻ liên hệ: lời mời (đoạn chữ) + người phụ trách (khối số liệu: ô đầu = tên, còn lại = điện thoại, email…)."""
    invite, who = "", []
    for i in ids:
        b = by[i]
        if b["type"] in ("paragraph", "heading"):
            invite += f'<p class="invite">{_txt(b["text"])}</p>'
        elif b["type"] == "data":
            for n, k in enumerate(vn.tiles((b.get("data") or {}).get("kpis") or [], render.fmt, vn.STYLE_VN)):
                v = esc(k["value"])
                if "@" in k["value"]:
                    v = f'<a href="mailto:{esc(k["value"])}">{v}</a>'
                elif sum(ch.isdigit() for ch in k["value"]) >= 8:
                    v = f'<a href="tel:{esc("".join(ch for ch in k["value"] if ch.isdigit() or ch == "+"))}">{v}</a>'
                who.append(f'<div><dt>{esc(k["label"])}</dt><dd class="{"name" if n == 0 else "num" if "@" in k["value"] or any(ch.isdigit() for ch in k["value"]) else ""}">{v}</dd></div>')
    return (f'<footer class="contact"><p class="eyebrow">{esc(label)}</p>{invite}'
            + (f'<dl class="who">{"".join(who)}</dl>' if who else "") + "</footer>")


def page(title: str, blocks: list[dict[str, Any]], brand: dict[str, Any], images: dict[str, tuple[bytes, str]], tpl: dict[str, Any]) -> str:
    primary = brand["primary_color"]
    deep = layout.shade(primary, 0.45)
    accent = layout.readable_on_white(primary)
    css = (CSS.replace("__DEEP__", deep).replace("__ACCENT__", accent).replace("__SERIF__", SERIF)
           .replace("__SANS__", SANS)) + ILLUS_CSS
    by = {b["id"]: b for b in blocks}
    out: list[str] = []
    cover = contact = ""
    num = [0]
    first_pb = True
    for s in layout.sections(blocks, tpl, title):
        k = s["kind"]
        if k == "cover":
            hero = images.get(s["hero"]) if s["hero"] else None
            logo = f'<img class="logo" src="{data_url((brand["logo"], brand.get("logo_type") or "image/png"))}" alt="Logo công ty">' if brand.get("logo") else ""
            sub = "".join(f'<p class="loc">{_txt(by[i]["text"])}</p>' for i in s["text"])
            bg = f'<img class="bg" src="{data_url(hero)}" alt="">{badge(by[s["hero"]])}' if hero else ""
            cover = (f'<header class="cover{"" if hero else " plainbg"}" aria-label="Bìa">{bg}<div class="shade"></div>{logo}'
                     f'<div class="in"><span class="rule"></span><h1>{esc(title)}</h1>{sub}</div></header>')
        elif k == "tiles":
            num[0] += 1
            out.append(f'<section class="chap pb"><h2 class="sec"><span class="n">{num[0]:02d}</span>{esc(s.get("title") or "Điểm nổi bật")}</h2>'
                       f'{_strip(s["items"], int(s["cols"]))}</section>')
            first_pb = False
        elif k == "flow":
            chaps = _flow(s["ids"], by, images, tpl, accent, deep, num)
            if first_pb and chaps:
                chaps[0] = chaps[0].replace('class="chap"', 'class="chap pb"', 1)
                first_pb = False
            out += chaps
        elif k == "columns":
            stack = any(by[i]["type"] == "data" and vn.plan(by[i].get("data") or {}) for col in s["columns"] for i in col)   # có thanh mốc ⇒ xếp dọc, tiêu đề thành mục có số
            cols_html = []
            for col in s["columns"]:
                inner = []
                for i in col:
                    b = by[i]
                    if b["type"] == "heading" and stack:
                        num[0] += 1
                        inner.append(f'<h2 class="sec"><span class="n">{num[0]:02d}</span>{_txt(b["text"])}</h2>')
                    elif b["type"] == "heading":
                        inner.append(f'<h3 class="sub">{_txt(b["text"])}</h3>')
                    elif b["type"] == "paragraph":
                        inner.append(f'<p class="lede">{_txt(b["text"])}</p>')
                    elif b["type"] == "data":
                        inner.append(_data(b, tpl, accent, deep))
                    elif b["type"] == "quote":
                        q = b.get("quote") or {}
                        inner.append(f'<blockquote>{esc(q.get("text", ""))}<cite>{esc(q.get("cite", ""))}</cite></blockquote>')
                cols_html.append("<div>" + "".join(inner) + "</div>")
            out.append(f'<section class="chap"><div class="cols{" stack" if stack else ""}" style="--n:{s["cols"]}">{"".join(cols_html)}</div></section>')
        elif k == "grid":
            figs = []
            for i in s["ids"]:
                if img := images.get(i):
                    im = by[i].get("image") or {}
                    figs.append((data_url(img), im.get("caption") or "Hình minh hoạ", im.get("caption") or "", badge(by[i])))
            if figs:
                num[0] += 1
                out.append(f'<section class="chap pb"><h2 class="sec"><span class="n">{num[0]:02d}</span>{esc(s.get("title") or "Hình ảnh")}</h2>{_mosaic(figs)}</section>')
        elif k == "footer":
            contact = _contact(s["ids"], by, str(s.get("title") or "Liên hệ"))
    fine = [render.footnote(blocks), brand.get("footer", "")]
    fine_html = "".join(f"<p>{esc(x)}</p>" for x in fine if x)
    return ('<!DOCTYPE html><html lang="vi"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">'
            f'<title>{esc(title)}</title><style>{font_css()}{css}</style></head><body><div class="sheet">{cover}'
            f'<main class="wrap">{"".join(out)}</main>{contact}<div class="fine">{fine_html}</div></div></body></html>')
