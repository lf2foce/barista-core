"""QUYỀN THEO KHỐI — lớp 3 của quyền (ADR 0239, bổ sung 18/11/2026 lát 2; api 1.59).

Bốn lớp, mỗi lớp một chỗ: (1) MÀN hiện không = quyền đọc tài nguyên của truy vấn màn; (2) DÒNG nào thấy = RLS (own / unit / team / all); (3) KHỐI nào của
màn có mặt = ``needs=`` khai trên khối / ô số / cột / chuỗi biểu đồ / mục (``kernel/ui.py``) — module này; (4) làm được gì = quyền hành động + chuỗi duyệt.

Một màn cho mọi vai: lãnh đạo và nhân viên mở CÙNG màn, mỗi người nhận phần mình được thấy. Không có cơ chế "ẩn màn trùng" / "màn này thay màn kia".

``cut`` chạy ở CỬA RA của executor (``localized`` — ``run`` / ``query`` / ``propose``, kể cả khối tải sau) nên mọi đường ra cùng bị lọc: web, app, trợ lý
(kết quả công cụ), xuất Excel (``/xlsx`` đọc qua ``executor.query``), bản chốt (lưu NGUYÊN dấu ``needs`` — người mở bản chốt được lọc theo quyền của họ lúc
mở). Gọi lồng (``ctx.call``) nhận nguyên — mã của module khác cần số để tính, không phải người xem.

Hợp đồng thẻ chỉ BỚT phần tử theo người xem, không đổi tên trường (bất biến 7). Hàng lưới (``w``) mất ô thì các ô còn lại giãn ra lấp chỗ (không để lỗ).
"""

import logging
from typing import Any

from barista.kernel import registry
from barista.kernel.principal import SCOPES, Principal

log = logging.getLogger(__name__)

# khoá ``needs`` không khớp nhóm thông tin / tài nguyên nào (gõ sai, gói đổi khoá) ⇒ coi như KHÔNG có quyền (an toàn) + ghi lại để test quét báo đỏ
UNKNOWN: set[str] = set()

GRID = 12
MARK = "needs_info"   # khoá dấu trong đầu ra (``ui.*(needs=)`` ghi) — tên riêng của lõi, không trùng ô "needs" sẵn có của các trang (vd gói cần bật kèm)


def resolve(key: str) -> str | None:
    """Khoá nhóm thông tin (``InfoGroup.key`` của bất kỳ gói đã nạp) ⇒ tài nguyên của nhóm; tên tài nguyên ⇒ chính nó; lạ ⇒ None."""
    for _, g in registry.info_groups():
        if g.key == key:
            return g.resource
    return key if key in registry.RESOURCES else None


def parse(item: str) -> tuple[str | None, str]:
    """``"khoá[:phạm vi]"`` ⇒ (tài nguyên, phạm vi tối thiểu — mặc định own)."""
    key, _, scope = item.partition(":")
    scope = scope or "own"
    if scope not in SCOPES:
        return None, scope
    return resolve(key), scope


def meets(p: Principal, needs: Any) -> bool:
    """Người xem đủ quyền cho ``needs`` (chuỗi hoặc danh sách — cần đủ cả)? Quyền hệ thống: luôn. Khoá lạ ⇒ không (và ghi vào ``UNKNOWN``)."""
    if not needs:
        return True
    items = [needs] if isinstance(needs, str) else needs
    for item in items:
        res, scope = parse(str(item))
        if res is None:
            if item not in UNKNOWN:
                log.error("needs=%r: không phải nhóm thông tin / tài nguyên đã khai — khối bị bỏ cho mọi người", item)
                UNKNOWN.add(str(item))
            return False
        if p.system:
            continue
        if not (p.can(res, "read") and p.has_scope(res, scope)):
            return False
    return True


def _empty(x: dict[str, Any]) -> bool:
    """Khối chỉ còn vỏ sau khi bỏ phần tử (ô số / chuỗi / cột) ⇒ bỏ cả khối, không để khung trống."""
    t = x.get("type")
    if t == "kpis":
        return not x.get("items")
    if t == "chart":
        return not x.get("series")
    if t == "table":
        return not ((x.get("page") or {}).get("columns"))
    return False


def _rows_of(blocks: list[Any]) -> list[list[int]]:
    """Hàng lưới như app vẽ (``web/src/lib/layout.ts``): khối có ``w`` liền nhau tổng ≤ 12 cùng một hàng; khối không ``w`` chiếm cả hàng."""
    rows: list[list[int]] = []
    used = GRID
    for i, b in enumerate(blocks):
        w = b.get("w") if isinstance(b, dict) else None
        if not isinstance(w, int):
            rows.append([i])
            used = GRID
        elif rows and used + w <= GRID and all(isinstance(blocks[j], dict) and isinstance(blocks[j].get("w"), int) for j in rows[-1]):
            rows[-1].append(i)
            used += w
        else:
            rows.append([i])
            used = w
    return rows


def _refill(blocks: list[Any], keep: list[bool]) -> list[Any]:
    """Bỏ khối không giữ; hàng lưới mất ô ⇒ ô còn lại chia lại đúng bề rộng cũ của hàng (tỷ lệ theo ``w`` cũ, ô cuối nhận phần lẻ)."""
    out: list[Any] = []
    for row in _rows_of(blocks):
        kept = [i for i in row if keep[i]]
        if len(kept) < len(row) and kept and len(row) > 1:
            total = sum(blocks[i]["w"] for i in row)
            base = sum(blocks[i]["w"] for i in kept)
            ws = [max(1, round(blocks[i]["w"] * total / base)) for i in kept]
            ws[-1] = max(1, total - sum(ws[:-1]))
            out += [{**blocks[i], "w": w} for i, w in zip(kept, ws, strict=True)]
        else:
            out += [blocks[i] for i in kept]
    return out


def cut(p: Principal, x: Any) -> Any:
    """Đầu ra theo người xem: bỏ mọi phần tử (trong danh sách) khai ``needs`` mà ``p`` không đủ quyền; bảng (``columns`` + ``rows``) mất cột ⇒ bỏ luôn
    GIÁ TRỊ đó trong từng dòng (dữ liệu không rời máy chủ) và góc nhìn dựa trên cột đó. Không có dấu ``needs`` nào ⇒ trả đúng như cũ."""
    if p.system or not _has(x):
        return x
    return _cut(p, x)


def _has(x: Any) -> bool:
    if isinstance(x, dict):
        return isinstance(x.get(MARK), list) or any(_has(v) for v in x.values() if isinstance(v, dict | list))
    if isinstance(x, list):
        return any(_has(v) for v in x if isinstance(v, dict | list))
    return False


BLOCK_LISTS = ("blocks", "header")   # danh sách KHỐI của thẻ / khối vẽ trên bảng — nơi khối rỗng bị bỏ và hàng lưới được lấp


def _cut(p: Principal, x: Any, blocks: bool = False) -> Any:
    if isinstance(x, list):
        keep = [not (isinstance(v, dict) and isinstance(v.get(MARK), list) and not meets(p, v[MARK])) for v in x]
        vals = [_cut(p, v) if k and isinstance(v, dict | list) else v for v, k in zip(x, keep, strict=True)]
        if not blocks:
            return [v for v, k in zip(vals, keep, strict=True) if k]
        # khối CHỈ còn vỏ vì vừa bị bỏ phần tử ⇒ bỏ cả khối (khối vốn rỗng giữ nguyên như trước)
        keep = [k and not (isinstance(v, dict) and _empty(v) and not _empty(o)) for v, o, k in zip(vals, x, keep, strict=True)]
        return _refill(vals, keep)
    if not isinstance(x, dict):
        return x
    out = {k: _cut(p, v, k in BLOCK_LISTS) if isinstance(v, dict | list) else v for k, v in x.items()}
    cols = x.get("columns")
    if isinstance(cols, list) and isinstance(x.get("rows"), list):
        gone = {c["key"] for c in cols if isinstance(c, dict) and isinstance(c.get(MARK), list) and not meets(p, c[MARK])}
        if gone:
            out["rows"] = [{k: v for k, v in r.items() if k not in gone} if isinstance(r, dict) else r for r in out["rows"]]
            if isinstance(views := out.get("views"), dict):
                out["views"] = {k: v for k, v in views.items() if not (isinstance(v, dict) and {v.get("by"), v.get("date")} & gone)}
    return out


def collect(x: Any, acc: list[tuple[list[str], dict[str, Any]]] | None = None) -> list[tuple[list[str], dict[str, Any]]]:
    """Mọi (needs, phần tử) trong một đầu ra — cho test quét và màn "Ai thấy thông tin gì"."""
    acc = [] if acc is None else acc
    if isinstance(x, dict):
        if isinstance(n := x.get(MARK), list):
            acc.append(([str(i) for i in n], x))
        for v in x.values():
            if isinstance(v, dict | list):
                collect(v, acc)
    elif isinstance(x, list):
        for v in x:
            if isinstance(v, dict | list):
                collect(v, acc)
    return acc
