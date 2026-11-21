"""VỪA TRANG (ADR 0252) — chữ dài hơn khung trang của mẫu nhiều trang cố định thì tự xử lý MỘT vòng, không bắt người dùng đoán.

Phép đo (``paged.measure``) trả mỗi trang tràn: phần vượt (px), khối gây tràn, chiều cao từng khối. Ở đây là phần THUẦN (không model, không DB):

* ``report``: số đo + khối của bản ⇒ báo cáo từng trang (khối nào, dài bao nhiêu từ, nên rút còn bao nhiêu, hành động bấm được = ``compose.revise`` đúng khối đó). Cùng báo cáo
  này nuôi lời báo lỗi lúc xuất / gửi link, truy vấn ``compose.overflow`` và vòng tự rút gọn.
* ``plan``: danh sách (khối, số từ đích) để vòng tự rút gọn gọi đường sửa khối ``revise`` đã có (quyền người soạn, qua kiểm số ``check.py``). Chỉ đoạn chữ — số trong bảng / ô số là
  số liệu, không cắt bằng tay hay bằng model.
* ``describe``: câu tiếng Việt gọn nói đúng việc — trang nào, khối nào, bấm gì.
"""

import math
from typing import Any

from barista.modules.compose.paged import Over

LINE_PX = 19.8      # một dòng chữ thân ở khổ in (9,6 pt × 1,55)
MIN_WORDS = 25      # đoạn ngắn hơn không đáng rút
MAX_PER_PAGE = 3    # tối đa số đoạn rút / trang trong một vòng
MAX_TOTAL = 5       # tối đa số đoạn rút / một vòng
KEEP_MIN = 0.35     # không rút còn dưới tỉ lệ này (rút hơn nữa là mất ý — để người quyết)
KEEP_MAX = 0.9
HINT = "Rút gọn khối này"


def lines(px: int) -> int:
    return max(1, math.ceil(px / LINE_PX))


def words(b: dict[str, Any]) -> int:
    return len(str(b.get("text", "")).split())


def label(b: dict[str, Any]) -> str:
    """Tên ngắn của khối để người đọc nhận ra: đầu đoạn chữ / tên bảng / số ô."""
    t = b["type"]
    d = b.get("data") or {}
    if t in ("paragraph", "heading"):
        s = " ".join(str(b.get("text", "")).split())
        return (s[:50] + "…") if len(s) > 50 else s
    if t == "data":
        return str(d.get("title") or "Số liệu")
    if t == "image":
        return str((b.get("image") or {}).get("caption") or "Ảnh")
    if t == "quote":
        return "Trích dẫn"
    return ""


def _kind(b: dict[str, Any]) -> str:
    t = b["type"]
    if t == "paragraph":
        return "đoạn"
    if t == "data":
        return "bảng" if (b.get("data") or {}).get("rows") else "ô số"
    return {"image": "ảnh", "quote": "trích dẫn", "heading": "tiêu đề"}.get(t, "khối")


def _action(sid: str, b: dict[str, Any], page: int, px: int, h: int) -> dict[str, Any] | None:
    """Hành động bấm được cho MỘT khối: gọi ``compose.revise`` (viết lại đúng khối đó, quyền người soạn). Ảnh / ô số: không có (bớt ảnh = xoá khối)."""
    if b["type"] == "paragraph":
        n = words(b)
        keep = max(KEEP_MIN, min(KEEP_MAX, (h - 1.25 * px) / h)) if h > 0 else 0.7
        to = max(12, int(n * keep))
        return {"command": "compose.revise", "label": HINT, "input": {
            "session_id": sid, "block": b["id"], "mode": "rewrite",
            "instruction": f"Rút gọn đoạn này còn khoảng {to} từ (hiện {n} từ) để vừa khung trang {page}; giữ ý chính, không thêm con số mới."}}
    rows = (b.get("data") or {}).get("rows") or []
    if b["type"] == "data" and len(rows) > 3 and h > 0:
        drop = math.ceil(1.25 * px / max(h / (len(rows) + 2), 1))
        to = max(3, len(rows) - drop)
        return {"command": "compose.revise", "label": "Bớt dòng của bảng này", "input": {
            "session_id": sid, "block": b["id"], "mode": "rewrite",
            "instruction": f"Rút gọn bảng này còn khoảng {to} dòng quan trọng nhất (hiện {len(rows)} dòng) để vừa khung trang {page}."}}
    return None


def report(sid: str, overs: list[Over], blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mỗi trang tràn ⇒ ``{page, px, lines, axis, blocks: [...]}`` — khối của trang theo thứ tự trên trang, khối gây tràn (``culprit``) đứng đầu."""
    by = {b["id"]: b for b in blocks}
    out = []
    for o in overs:
        items = []
        for bid, h in o.heights.items():
            b = by.get(bid)
            if b is None:
                continue
            items.append({"id": bid, "type": b["type"], "kind": _kind(b), "label": label(b), "height": h, "culprit": bid in o.blocks,
                          "words": words(b) if b["type"] == "paragraph" else 0, "action": _action(sid, b, o.page, o.px, h)})
        items.sort(key=lambda x: (not x["culprit"], -x["height"]))
        out.append({"page": o.page, "px": o.px, "lines": lines(o.px), "axis": o.axis, "blocks": items})
    return out


def plan(pages: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """Vòng tự rút gọn: mỗi trang tràn DỌC có đoạn chữ ⇒ rút các đoạn dài nhất (tối đa ``MAX_PER_PAGE``) về cùng một tỉ lệ đủ bù phần vượt (+25%).
    Trang chỉ có bảng / ảnh (không đoạn nào) hoặc tràn ngang ⇒ không có gì để tự rút — để người quyết (``describe`` nói rõ)."""
    out: list[tuple[str, int]] = []
    for pg in pages:
        if pg["axis"] != "h":
            continue
        paras = sorted((b for b in pg["blocks"] if b["type"] == "paragraph" and b["words"] >= MIN_WORDS), key=lambda b: -b["height"])
        total = sum(b["height"] for b in paras)
        if not paras or total <= 0:
            continue
        keep = max(KEEP_MIN, min(KEEP_MAX, (total - 1.25 * pg["px"] - 4) / total))
        for b in paras[:MAX_PER_PAGE]:
            to = int(b["words"] * keep)
            if to < b["words"] - 4 and len(out) < MAX_TOTAL:
                out.append((b["id"], max(12, to)))
    return out


def describe(pages: list[dict[str, Any]]) -> str:
    """Câu báo cho người: trang nào tràn bao nhiêu, do khối nào, bấm gì. Chỉ nêu trang ĐO RA tràn."""
    parts = []
    for pg in pages:
        head = f"Trang {pg['page']} tràn khung" + (f" (khoảng {pg['lines']} dòng)" if pg["axis"] == "h" else " (rộng quá khung)")
        cul = [b for b in pg["blocks"] if b["culprit"] and b["type"] in ("paragraph", "data")] or [b for b in pg["blocks"] if b["type"] == "paragraph"][:1] \
            or [b for b in pg["blocks"] if b["type"] == "data" and b["action"] is not None][:1]
        if not cul:
            parts.append(f"{head} — bớt ảnh / khối trên trang này hoặc chuyển bớt sang trang khác")
            continue
        top = cul[0]
        what = f"{top['kind']} “{top['label']}”" if top["label"] else top["kind"]
        if top["action"] is None:
            parts.append(f"{head} — {what}: bớt chữ / dòng hoặc chuyển bớt khối sang trang khác")
        elif top["type"] == "paragraph":
            parts.append(f"{head} — {what}: bấm “{top['action']['label']}” (AI viết lại ngắn hơn) rồi xuất lại")
        else:
            parts.append(f"{head} — {what}: bấm “{top['action']['label']}” hoặc chuyển bảng sang trang khác rồi xuất lại")
    return "; ".join(parts)
