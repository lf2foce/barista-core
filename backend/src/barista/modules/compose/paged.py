"""TRANG WEB NHIỀU TRANG CỐ ĐỊNH, A4 NGANG (ADR 0252) — Hồ sơ chào bán 10 trang, Tóm tắt 4 trang: mỗi trang một khuôn (``layout`` ``pages``: cover · contents · body · close),
tiêu đề trang là một câu kết luận, số CHỈ in từ khối số liệu (kèm nhãn nguồn / ước tính), màu + logo + chân trang từ bộ nhận diện công ty, phông nhúng (``editorial.font_css``).

Cùng MỘT trang HTML cho xem trước, bản gửi và PDF: ``@page`` quyết khổ giấy, trình duyệt không giao diện in ra PDF (``kernel/convert.html_to_pdf``). Một tệp, không script, không tải gì
từ ngoài. Trên màn hình các trang xếp dọc như tờ giấy (thu nhỏ theo bề rộng); khi in thì mỗi trang đúng một tờ."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from barista.api import convert, zone
from barista.modules.compose import editorial, html, layout, render, vn
from barista.modules.compose.html import data_url, esc

CSS = """
@page{size:297mm 210mm;margin:0}
:root{--deep:#__DEEP__;--accent:#__ACCENT__;--tint:#__TINT__;--paper:#F7F4EE;--ink:#1D1B18;--mute:#6B665D;--line:#D9D3C7;--serif:__SERIF__;--sans:__SANS__}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{background:#E9E6DF;color:var(--ink);font:400 9.6pt/1.55 var(--sans);font-synthesis:none;-webkit-font-smoothing:antialiased;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.pages{padding:20px 0}
.page{position:relative;width:297mm;height:210mm;margin:0 auto 18px;background:#fff;overflow:hidden;box-shadow:0 2px 18px rgba(40,30,10,.16)}
@media screen and (max-width:1200px){.page{zoom:.8}}
@media screen and (max-width:980px){.page{zoom:.62}}
@media screen and (max-width:780px){.page{zoom:.45}}
@media screen and (max-width:560px){.page{zoom:.32}}
@media print{body{background:#fff}.pages{padding:0}.page{margin:0;box-shadow:none;break-after:page;zoom:1}.page:last-child{break-after:auto}}
.kicker,.tag,.lbl,th{font:600 7pt var(--sans);letter-spacing:.16em;text-transform:uppercase;color:var(--accent)}
.pad{position:absolute;left:16mm;right:16mm;top:13mm;bottom:15mm;display:flex;flex-direction:column;min-height:0}
.hd{flex:none;padding-bottom:5mm;margin-bottom:5mm;border-bottom:.3mm solid var(--line)}
.hd h2{margin-top:2mm;font:500 24pt/1.08 var(--serif);color:var(--deep);letter-spacing:-.01em;text-wrap:balance;max-width:230mm}
.ft{position:absolute;left:16mm;right:16mm;bottom:6mm;display:flex;justify-content:space-between;gap:10mm;font:400 6.8pt var(--sans);color:var(--mute);letter-spacing:.03em}
.ft b{color:var(--deep);font-weight:600}
.main{flex:1;min-height:0;display:flex;flex-direction:column;gap:5mm}
.split{flex:0 1 auto;min-height:0;display:grid;gap:9mm;grid-template-columns:1fr 1fr}
.split.right,.split.left{flex:1}
.split.right{grid-template-columns:1.08fr 1fr}
.split.left{grid-template-columns:1fr 1.08fr}
.split.left .media{order:-1}
.split.solo{grid-template-columns:1fr}
.split.solo .media{flex-direction:row;height:62mm}
.split.solo .media figure,.split.solo .media figure+figure{flex:1 1 0}
.text{min-width:0}
.text p{margin-bottom:2.6mm}
.text h3{margin:3mm 0 1.6mm;font:600 7.6pt var(--sans);letter-spacing:.16em;text-transform:uppercase;color:var(--accent)}
.text .lede{font:500 14.5pt/1.45 var(--serif);color:var(--deep)}
.text.two{column-count:2;column-gap:9mm}
.text.two p,.text.two h3{break-inside:avoid}
.media{min-height:0;display:flex;flex-direction:column;gap:3mm}
.media figure{flex:1 1 0;min-height:0}
.media figure+figure{flex:0 0 34mm}
.banner{flex:none;display:flex;gap:3mm;height:36mm}
.banner figure{flex:1;min-width:0}
.ph{position:relative;overflow:hidden;background:var(--paper)}
.ph img{display:block;width:100%;height:100%;object-fit:cover}
.ph figcaption{position:absolute;left:0;bottom:0;max-width:80%;padding:1mm 2.4mm;background:rgba(255,255,255,.88);font:400 6.6pt var(--sans);color:var(--mute)}
__ILLUS__
.strip{flex:none;display:grid;grid-template-columns:repeat(var(--n),minmax(0,1fr));border-top:.5mm solid var(--deep);border-bottom:.3mm solid var(--line)}
.strip>div{padding:3.4mm 4mm 3.4mm 0;margin-right:4mm;border-right:.3mm solid var(--line);min-width:0}
.strip>div:last-child{border-right:0;margin-right:0}
.strip dt{font:600 6.6pt var(--sans);letter-spacing:.14em;text-transform:uppercase;color:var(--mute);margin-bottom:1.6mm}
.strip dd{font:500 21pt/1.08 var(--serif);color:var(--deep);overflow-wrap:anywhere}
.strip dd.txt{font-size:13pt;line-height:1.25}
.strip.many dd{font-size:17pt}
table{width:100%;border-collapse:collapse;font-size:8.4pt}
th{text-align:left;color:var(--mute);padding:0 3mm 2mm 0;border-bottom:.4mm solid var(--deep);white-space:nowrap}
td{padding:1.9mm 3mm 1.9mm 0;border-bottom:.25mm solid var(--line);vertical-align:baseline;color:#2c2925}
td:first-child{font:500 10.4pt/1.25 var(--serif);color:var(--deep)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;padding-left:3mm}
th:last-child,td:last-child{padding-right:0}
tr{break-inside:avoid}
.data{flex:none}
.data h3{margin-bottom:2mm;font:600 7.6pt var(--sans);letter-spacing:.16em;text-transform:uppercase;color:var(--accent)}
.src{margin-top:1.8mm;display:flex;gap:2mm;align-items:center;font:400 6.6pt var(--sans);color:var(--mute)}
.tag{display:inline-block;padding:.5mm 1.8mm;border-radius:1mm;background:var(--tint);color:var(--deep);letter-spacing:.08em;font-size:6.2pt}
.tag.est{background:#F6EEDF;color:#8A6A35}
.prose p,.prose li{font-size:8.6pt;line-height:1.55}
.prose ul{margin:0 0 2mm 4.5mm}
blockquote{margin:0 0 3mm;padding-left:4mm;border-left:.4mm solid var(--accent);font:400 9pt/1.6 var(--sans)}
blockquote cite{display:block;margin-top:1mm;font:600 6.4pt var(--sans);letter-spacing:.12em;text-transform:uppercase;color:var(--mute);font-style:normal}
mark{background:#ffe27a;color:#111;padding:0 .6mm}
/* bìa */
.cover{background:var(--deep);color:#fff}
.cover img.bg{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.cover .shade{position:absolute;inset:0;background:linear-gradient(90deg,rgba(6,10,14,.84) 0%,rgba(6,10,14,.5) 42%,rgba(6,10,14,0) 72%),linear-gradient(0deg,rgba(6,10,14,.55) 0%,rgba(6,10,14,0) 38%)}
.cover.plainbg .shade{background:radial-gradient(ellipse at 20% 0%,rgba(255,255,255,.10),rgba(0,0,0,0) 60%)}
.cover .logo{position:absolute;top:13mm;left:18mm;max-height:13mm;max-width:46mm;background:#fff;padding:2mm 3mm}
.cover .t{position:absolute;left:18mm;top:50mm;width:150mm}
.cover .t .kicker{color:rgba(255,255,255,.82)}
.cover .t .rule{display:block;width:16mm;height:.3mm;background:rgba(255,255,255,.8);margin:0 0 6mm}
.cover h1{margin:3mm 0 5mm;font:500 40pt/1.03 var(--serif);letter-spacing:-.01em;text-wrap:balance}
.cover .sub{font:300 12pt/1.55 var(--sans);opacity:.94;max-width:125mm}
.cover .band{position:absolute;left:0;right:0;bottom:0;display:grid;grid-template-columns:repeat(var(--n),minmax(0,1fr));gap:8mm;padding:6mm 18mm 8mm;background:rgba(6,10,14,.78)}
.cover .band dt{font:600 6.4pt var(--sans);letter-spacing:.14em;text-transform:uppercase;color:rgba(255,255,255,.66);margin-bottom:1.2mm}
.cover .band dd{font:500 17pt/1.1 var(--serif);overflow-wrap:anywhere}
.cover .illus{right:6mm;bottom:auto;top:6mm}
/* mục lục */
.toc{list-style:none;border-top:.5mm solid var(--deep)}
.toc li{display:flex;align-items:baseline;gap:4mm;padding:3.2mm 0;border-bottom:.25mm solid var(--line);font:500 12pt/1.3 var(--serif);color:var(--deep)}
.toc li .no{width:8mm;font:600 8pt var(--sans);letter-spacing:.14em;color:var(--accent)}
.toc li .pg{margin-left:auto;font:400 8pt var(--sans);color:var(--mute);font-variant-numeric:tabular-nums}
/* trang cuối: thẻ liên hệ */
.close{flex:1;min-height:0;display:grid;grid-template-columns:1fr 78mm;gap:10mm}
.close .main{min-width:0}
.contact{align-self:stretch;background:var(--deep);color:#fff;padding:8mm 7mm;display:flex;flex-direction:column}
.contact .eyebrow{font:600 6.6pt var(--sans);letter-spacing:.2em;text-transform:uppercase;color:rgba(255,255,255,.7)}
.contact .invite{margin-top:3mm;font:500 15pt/1.25 var(--serif)}
.contact .who{margin-top:auto;padding-top:5mm;border-top:.3mm solid rgba(255,255,255,.3);display:grid;gap:3mm}
.contact .who dt{font:600 6.2pt var(--sans);letter-spacing:.16em;text-transform:uppercase;color:rgba(255,255,255,.62);margin-bottom:.8mm}
.contact .who dd{margin:0;font:500 12pt/1.3 var(--serif);overflow-wrap:anywhere}
.contact .who dd.name{font-size:15pt}
.contact .who dd.num{font:400 9pt/1.5 var(--sans)}
.contact a{color:inherit;text-decoration:none}
"""


def _date(d: dict[str, Any]) -> str:
    at = (d.get("source") or {}).get("at") or ""
    try:
        return datetime.fromisoformat(at).astimezone(zone()).strftime("%d/%m/%Y")
    except ValueError:
        return ""


def _src(b: dict[str, Any]) -> str:
    """Nhãn dưới mỗi khối số liệu: ``Ước tính`` (khối đánh dấu ước tính) hoặc ``Nguồn: số liệu công ty`` + ngày chụp. Trang là bản gửi ngoài ⇒ không tên truy vấn nội bộ."""
    d = b.get("data") or {}
    tag = '<span class="tag est">Ước tính</span>' if d.get("estimate") else '<span class="tag">Nguồn: số liệu công ty</span>'
    when = _date(d)
    return f'<p class="src">{tag}{f"cập nhật {esc(when)}" if when else ""}</p>'


def _tiles_only(b: dict[str, Any]) -> bool:
    d = b.get("data") or {}
    return b["type"] == "data" and bool(d.get("kpis")) and not d.get("rows") and not d.get("texts")


def _strip(items: list[dict[str, str]], bid: str = "") -> str:
    n = max(len(items), 1)
    cells = "".join(f'<div><dt>{esc(i["label"])}</dt><dd class="{"num" if any(ch.isdigit() for ch in i["value"]) else "txt"}">{esc(i["value"])}</dd></div>' for i in items)
    mark = f' data-b="{esc(bid)}"' if bid else ""
    return f'<dl class="strip{" many" if n > 4 else ""}"{mark} style="--n:{min(n, 6)}">{cells}</dl>'


def _figure(b: dict[str, Any], images: dict[str, tuple[bytes, str]]) -> str:
    img = images.get(b["id"])
    if img is None:
        return ""
    im = b.get("image") or {}
    cap = f"<figcaption>{esc(im['caption'])}</figcaption>" if im.get("caption") else ""
    return f'<figure class="ph" data-b="{esc(b["id"])}"><img src="{data_url(img)}" alt="{esc(im.get("caption") or "Hình minh hoạ")}">{html.badge(b)}{cap}</figure>'


def _txt(t: str) -> str:
    return editorial._txt(t)


def _data(b: dict[str, Any], style: str, *, with_tiles: bool) -> str:
    d = b.get("data") or {}
    parts: list[str] = [f'<section class="data" data-b="{esc(b["id"])}">']
    if d.get("title"):
        parts.append(f"<h3>{esc(d['title'])}</h3>")
    if with_tiles and d.get("kpis"):
        parts.append(_strip(vn.tiles(d["kpis"], render.fmt, style)))
    parts.append(editorial._table(d))
    for t in d.get("texts") or []:
        if t.get("title"):
            parts.append(f"<h3>{esc(t['title'])}</h3>")
        parts.append(f'<div class="prose">{editorial._paras(t.get("text", ""))}</div>')
    if d.get("note"):
        parts.append(f'<p class="src">{esc(d["note"])}</p>')
    parts.append(_src(b))
    parts.append("</section>")
    return "".join(parts)


def _headline(ids: list[str], by: dict[str, dict[str, Any]]) -> tuple[str, list[str]]:
    """Tiêu đề trang = tiêu đề đầu tiên của trang (một câu kết luận); phần còn lại của trang không gồm nó."""
    for n, i in enumerate(ids):
        if by[i]["type"] == "heading":
            return by[i]["text"], ids[:n] + ids[n + 1:]
    return "", ids


def _body(ids: list[str], by: dict[str, dict[str, Any]], images: dict[str, tuple[bytes, str]], media: str, style: str, tpl: dict[str, Any]) -> str:
    text: list[tuple[str, str, str]] = []   # (loại, html, mã khối) — mã khối để phép đo tràn chỉ ra ĐÚNG khối gây tràn
    figs, strips, datas = [], [], []
    for i in ids:
        b = by[i]
        t = b["type"]
        if t == "paragraph":
            text.append(("p" if any(k in ("lede", "p") for k, _, _ in text) else "lede", _txt(b["text"]), i))
        elif t == "heading":
            text.append(("h", _txt(b["text"]), i))
        elif t == "quote":
            q = b.get("quote") or {}
            text.append(("q", f'<blockquote data-b="{esc(i)}">{esc(q.get("text", ""))}<cite>{esc(q.get("cite", ""))}</cite></blockquote>', i))
        elif t == "image" and i in images:
            figs.append(b)
        elif _tiles_only(b):
            strips.append(b)
        elif t == "data":
            datas.append(b)
    paras = sum(1 for k, _, _ in text if k in ("p", "lede"))
    inner = "".join(f'<h3 data-b="{esc(i)}">{x}</h3>' if k == "h" else x if k == "q" else f'<p class="lede" data-b="{esc(i)}">{x}</p>' if k == "lede"
                    else f'<p data-b="{esc(i)}">{x}</p>' for k, x, i in text)
    split_media = media in ("right", "left") and figs
    parts: list[str] = []
    if figs and not split_media:   # trang không dành chỗ ảnh cạnh chữ ⇒ ảnh thành dải ngang phía trên
        parts.append('<div class="banner">' + "".join(_figure(b, images) for b in figs[:3]) + "</div>")
    if split_media or inner:
        cls = f"split {media}" if split_media and inner else "split solo"
        two = " two" if not split_media and paras > 2 else ""
        media_html = f'<div class="media">{"".join(_figure(b, images) for b in figs[:3])}</div>' if split_media else ""
        parts.append(f'<div class="{cls}"><div class="text{two}">{inner}</div>{media_html}</div>' if inner or split_media else "")
    for b in strips:
        parts.append(_strip(vn.tiles((b.get("data") or {}).get("kpis") or [], render.fmt, style), b["id"]) + _src(b))
    parts += [_data(b, style, with_tiles=True) for b in datas]
    return "".join(parts)


def _cover(title: str, ids: list[str], by: dict[str, dict[str, Any]], images: dict[str, tuple[bytes, str]], brand: dict[str, Any], tpl: dict[str, Any], style: str) -> str:
    hero = next((by[i] for i in ids if by[i]["type"] == "image" and i in images), None)
    sub = "".join(f'<p class="sub">{_txt(by[i]["text"])}</p>' for i in ids if by[i]["type"] in ("paragraph", "heading"))
    items = [it for i in ids if _tiles_only(by[i]) for it in vn.tiles(by[i]["data"]["kpis"], render.fmt, style, skip_values=(title,))][:4]
    logo = f'<img class="logo" src="{data_url((brand["logo"], brand.get("logo_type") or "image/png"))}" alt="Logo công ty">' if brand.get("logo") else ""
    bg = f'<img class="bg" src="{data_url(images[hero["id"]])}" alt="">{html.badge(hero)}' if hero else ""
    cells = "".join(f'<div><dt>{esc(i["label"])}</dt><dd>{esc(i["value"])}</dd></div>' for i in items)
    band = f'<dl class="band" style="--n:{len(items)}">{cells}</dl>' if items else ""
    return (f'<section class="page cover{"" if hero else " plainbg"}" data-n="1" aria-label="Bìa">{bg}<div class="shade"></div>{logo}'
            f'<div class="t"><span class="kicker">{esc(tpl.get("kicker", ""))}</span><h1>{esc(title)}</h1>{sub}</div>{band}</section>')


def page(title: str, blocks: list[dict[str, Any]], brand: dict[str, Any], images: dict[str, tuple[bytes, str]], tpl: dict[str, Any], *, only: int = 0) -> str:
    """Trang HTML nhiều trang. ``only`` (≥ 1) ⇒ chỉ trang thứ ``only`` — để chụp ảnh từng trang cho bước tự soát (``compose.page_image``)."""
    primary = brand["primary_color"]
    css = (CSS.replace("__DEEP__", layout.shade(primary, 0.45)).replace("__ACCENT__", layout.readable_on_white(primary)).replace("__TINT__", layout.tint(primary, 0.9))
           .replace("__SERIF__", editorial.SERIF).replace("__SANS__", editorial.SANS).replace("__ILLUS__", html.ILLUS_CSS))
    by = {b["id"]: b for b in blocks}
    style = str(tpl.get("style", ""))
    secs = layout.sections(blocks, tpl, title)
    heads = {s["n"]: _headline(s["ids"], by)[0] for s in secs}
    out: list[str] = []
    for s in secs:
        if only and s["n"] != only:
            continue
        form, ids = s["form"], s["ids"]
        if form == "cover":
            out.append(_cover(title, ids, by, images, brand, tpl, style))
            continue
        foots = [i for i in ids if layout.slot_of(by[i]) == "footer"]
        head, rest = _headline([i for i in ids if i not in foots], by)
        h = f'<header class="hd"><span class="kicker">{esc(title)}</span>' + (f"<h2>{_txt(head)}</h2>" if head else "") + "</header>"
        if form == "contents":
            toc = "".join(f'<li><span class="no">{n:02d}</span>{_txt(t)}<span class="pg">{n}</span></li>' for n, t in heads.items() if n > s["n"] and t)
            body = (f'<div class="split solo" style="grid-template-columns:1fr 1fr"><div class="text">{_body_text(rest, by)}</div><ol class="toc">{toc}</ol></div>')
        elif form == "close" and foots:
            body = f'<div class="close"><div class="main">{_body(rest, by, images, s["media"], style, tpl)}</div>{editorial._contact(foots, by, str((tpl.get("labels") or {}).get("footer", "")))}</div>'
        else:
            body = f'<div class="main">{_body(rest, by, images, s["media"], style, tpl)}</div>'
        foot = esc(brand.get("footer", ""))
        out.append(f'<section class="page" data-n="{s["n"]}" aria-label="Trang {s["n"]}"><div class="pad">{h}{body}</div>'
                   f'<footer class="ft"><span>{foot or "&nbsp;"}</span><span><b>{s["n"]}</b> / {s["of"]}</span></footer></section>')
    return ('<!DOCTYPE html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">'
            f'<title>{esc(title)}</title><style>{editorial.font_css()}{css}</style></head><body><div class="pages">{"".join(out)}</div></body></html>')


def _body_text(ids: list[str], by: dict[str, dict[str, Any]]) -> str:
    return "".join(f'<p data-b="{esc(i)}">{_txt(by[i]["text"])}</p>' for i in ids if by[i]["type"] == "paragraph")


# Đo TRÀN bằng chính trình duyệt sẽ in PDF: khung mỗi trang cao cố định (210 mm) nên nội dung dài hơn sẽ đè lên khối bên dưới hoặc rơi khỏi trang — mất chữ mà không báo.
# Phép đo phải ĐÚNG hình học lúc in (nếu không sẽ báo tràn giả — người dùng không hiểu vì sao, xem trước vẫn đẹp):
#   * cửa sổ đo đủ rộng (``MEASURE_SIZE``) và ép ``zoom:1`` cho từng trang — trang web thu nhỏ theo bề rộng màn hình (``.page{zoom:.45}`` ở cửa sổ mặc định 800 px) làm số đo
#     lệch vì làm tròn; lúc in không có zoom;
#   * đo SAU khi phông + ảnh đã tải (ép bố cục một lần rồi mới chờ ``document.fonts.ready`` / đợi ảnh ``complete``);
#   * chỉ so với hộp nội dung ``.pad`` của TỪNG trang; bỏ phần tử trang trí / ``position:absolute`` có chủ đích (nhãn "Ảnh minh hoạ", chú thích ảnh) và mọi thứ
#     nằm trong khung ảnh (``.ph`` tự cắt ảnh theo khung — không phải tràn);
#   * tràn = (a) một khung có nội dung vượt hộp của nó (``scrollHeight`` / ``scrollWidth``) hơn dung sai — dọc ``BOX_TOL`` (cỡ lề cuối đoạn + một phần khoảng cách giữa khối 5 mm: vượt ít hơn
#     thế mắt không thấy đè), ngang ``TOL``; hoặc (b) một phần tử chạm tới CHÂN TRANG (``.ft``) — đáy ``.pad`` còn 6 mm đệm trước chân trang, lấn vào đệm chưa phải mất / đè chữ.
# Ra: mỗi trang tràn một bản ghi ``trang|px|trục|khối gây tràn|cao từng khối`` (mã khối từ ``data-b`` — để tự rút gọn / báo ĐÚNG khối). Cũng là chỗ cắm cho bước tự soát bằng agent.
TOL = 2
BOX_TOL = 10
MEASURE_SIZE = (1400, 1000)
MEASURE_JS = """(async()=>{
const TOL=__TOL__, BOX_TOL=__BOX_TOL__, BOXES='.main,.split,.text,.media,.close,.toc,.contact,.data,.banner';
try{
 document.querySelectorAll('.page').forEach(p=>{p.style.zoom='1'});
 void document.body.offsetHeight;
 try{await document.fonts.ready}catch(e){}
 try{await Promise.race([Promise.all([...document.images].map(i=>i.complete?0:new Promise(r=>{i.onload=i.onerror=r}))),new Promise(r=>setTimeout(r,2000))])}catch(e){}
 void document.body.offsetHeight;
}catch(e){}
const recs=[];
document.querySelectorAll('.page').forEach(pg=>{
 const pad=pg.querySelector('.pad'); if(!pad)return;
 const bottom=pad.getBoundingClientRect().bottom, ft=pg.querySelector('.ft'), limit=ft?ft.getBoundingClientRect().top-1:bottom+TOL;
 const boxes=[];
 for(const el of [pad,...pad.querySelectorAll(BOXES)]){
  const h=el.scrollHeight-el.clientHeight, w=el.scrollWidth-el.clientWidth;
  if(h>BOX_TOL||w>TOL)boxes.push({el,ex:Math.max(h>BOX_TOL?h:0,w>TOL?w:0),axis:h>BOX_TOL?'h':'w'});
 }
 const low=[];
 for(const el of pad.querySelectorAll('*')){
  if(el.closest('.ph'))continue;
  const pos=getComputedStyle(el).position; if(pos==='absolute'||pos==='fixed')continue;
  const r=el.getBoundingClientRect(); if(!r.width&&!r.height)continue;
  if(r.bottom>limit)low.push({el,ex:r.bottom-bottom,axis:'h',box:el.closest(BOXES)});
 }
 const all=[...boxes,...low]; if(!all.length)return;
 const inner=all.filter(a=>!all.some(b=>b!==a&&a.el!==b.el&&a.el.contains(b.el)));
 const cul=new Set();
 for(const a of inner){const root=a.box||a.el;root.querySelectorAll('[data-b]').forEach(x=>cul.add(x.getAttribute('data-b')));const c=a.el.closest('[data-b]');if(c)cul.add(c.getAttribute('data-b'))}
 const best=all.reduce((m,a)=>a.ex>m.ex?a:m);
 const hs=[...pg.querySelectorAll('[data-b]')].map(x=>x.getAttribute('data-b')+':'+Math.round(x.getBoundingClientRect().height));
 recs.push([pg.getAttribute('data-n'),Math.round(best.ex),best.axis,[...cul].join(','),hs.join(',')].join('|'));
});
document.documentElement.setAttribute('data-overflow',recs.join(';'));
})();""".replace("__TOL__", str(TOL)).replace("__BOX_TOL__", str(BOX_TOL))


@dataclass
class Over:
    """Một trang TRÀN: ``px`` = phần vượt (px CSS ở khổ in), ``axis`` ``h`` (dọc) / ``w`` (ngang), ``blocks`` = mã khối gây tràn, ``heights`` = chiều cao từng khối trên trang."""

    page: int
    px: int
    axis: str = "h"
    blocks: tuple[str, ...] = ()
    heights: dict[str, int] = field(default_factory=dict)


def parse(got: str) -> list[Over]:
    out: list[Over] = []
    for rec in got.split(";"):
        f = rec.split("|")
        if len(f) < 5 or not f[0].isdigit():
            continue
        hs = {k: int(v) for k, _, v in (x.partition(":") for x in f[4].split(",") if x) if v.isdigit()}
        out.append(Over(int(f[0]), int(f[1]) if f[1].isdigit() else 0, f[2] or "h", tuple(x for x in f[3].split(",") if x), hs))
    return out


async def measure(page_html: str) -> list[Over]:
    """Các trang bị TRÀN (rỗng = vừa hết) kèm phần vượt và khối gây tràn. Thiếu trình duyệt / đo lỗi ⇒ ``convert.Unavailable`` — không coi là "không tràn"."""
    return parse(await convert.html_measure(page_html, MEASURE_JS, "data-overflow", size=MEASURE_SIZE))


async def overflow(page_html: str) -> list[int]:
    """Số thứ tự các trang bị TRÀN trong trang HTML nhiều trang (rỗng = vừa hết)."""
    return [o.page for o in await measure(page_html)]
