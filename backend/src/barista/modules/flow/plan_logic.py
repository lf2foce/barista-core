"""NHẬP KẾ HOẠCH TỪ EXCEL (0222 §15/11) — phần THUẦN: hiểu cả bảng việc của một file kế hoạch. Không DB, không module khác ⇒ test độc lập.

Dòng của file tham chiếu nhau nên phải nhìn CẢ file mới hiểu được:

* **Cấp bậc**: mã WBS ("1", "1.1", "1.2.3"), cột cấp (Outline Level), STT La Mã / chữ cái ("I", "II", "A"), hoặc dòng tiêu đề nhóm (có tên, không ngày / số ngày / người /
  việc trước, ngay sau là dòng có dữ liệu). Dòng CÓ con là dòng nhóm → thành GIAI ĐOẠN của các việc con (không thành việc).
* **Việc trước**: "3", "1.2", "3FS+2d", "5SS-1", "Chốt thiết kế; 4" — tách theo ; hoặc , ; tham chiếu giải theo mã WBS / STT, cột ID, số thứ tự dòng, hoặc tên việc.
  Kiểu FS / SS tính ngày; FF / SF chỉ ghi "chờ" (có báo). Độ trễ (lag) tính theo ngày LÀM VIỆC (d / ngày, w / tuần = 5 ngày, h / giờ = 8 giờ).
* **Ngày**: ngày trong file giữ NGUYÊN; thiếu thì tính từ số ngày + việc trước theo lịch ngày làm việc của công ty (``is_off``). Việc nào có ngày do máy tính đều được đánh dấu.
* **Vòng phụ thuộc** ⇒ lỗi rõ, liệt kê dòng.
"""

import math
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from barista.modules.flow.schedule_rules import find_cycle

MAX_DEPS = 20          # khớp CHECK tasks_depends_len của migration 089
PHASE_LEN = 60         # khớp CHECK tasks_phase_len


def fold(s: str) -> str:
    t = unicodedata.normalize("NFD", str(s)).replace("đ", "d").replace("Đ", "D")
    return "".join(ch for ch in t if not unicodedata.combining(ch)).lower()


def key_of(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", fold(s))


# ── dữ liệu vào / ra ──

@dataclass
class Row:
    line: int
    title: str
    code: str = ""             # STT / WBS như trong file
    row_id: str = ""           # cột ID (MS Project)
    level: str = ""            # cột cấp (Outline Level)
    start: str = ""            # ISO hoặc chữ không đọc được
    due: str = ""
    duration: str = ""
    assignee: str = ""         # email đã chọn (hoặc trống)
    after: str = ""
    percent: str = ""
    phase: str = ""            # cột giai đoạn tường minh
    notes: str = ""


@dataclass
class Dep:
    target: Item
    kind: str = "FS"
    lag: int = 0
    raw: str = ""


@dataclass
class Item:
    row: Row
    pos: int
    parent: Item | None = None
    children: list[Item] = field(default_factory=list)
    is_group: bool = False
    depth: int = 0
    phase: str = ""
    start: date | None = None
    due: date | None = None
    duration: int | None = None     # ngày làm việc; 0 = mốc
    computed: set[str] = field(default_factory=set)   # {"start", "due"} do máy tính
    deps: list[Dep] = field(default_factory=list)
    status: str | None = None       # todo / doing / done theo % hoàn thành; None = file không nói
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def line(self) -> int:
        return self.row.line

    @property
    def title(self) -> str:
        return self.row.title.strip()

    def label(self) -> str:
        return f"dòng {self.line} “{self.title[:40]}”"


@dataclass
class Built:
    items: list[Item]                  # mọi dòng có tên, theo thứ tự file (cả nhóm)
    tasks: list[Item]                  # các dòng là VIỆC (không phải nhóm)
    groups: list[Item]
    notes: list[str]                   # lưu ý cả file (không gắn một dòng)
    blocking: str = ""                 # lỗi cả file (vòng phụ thuộc) — không nhập gì
    ignored: list[tuple[int, str]] = field(default_factory=list)


# ── ngày làm việc ──

class WorkDays:
    """Phép tính trên lịch ngày làm việc. ``is_off(d)``: ngày nghỉ (cuối tuần / lễ của lịch công ty)."""

    def __init__(self, is_off: Callable[[date], bool]) -> None:
        self.off = is_off

    def fwd(self, d: date) -> date:   # ngày làm việc đầu tiên ≥ d
        for _ in range(60):
            if not self.off(d):
                return d
            d += timedelta(days=1)
        return d

    def back(self, d: date) -> date:   # ngày làm việc cuối cùng ≤ d
        for _ in range(60):
            if not self.off(d):
                return d
            d -= timedelta(days=1)
        return d

    def add(self, d: date, n: int) -> date:
        """Dịch ``n`` ngày LÀM VIỆC (âm = lùi) từ ``d`` (d nên là ngày làm việc)."""
        step = 1 if n >= 0 else -1
        left = abs(n)
        while left:
            d += timedelta(days=step)
            if not self.off(d):
                left -= 1
        return d

    def end_of(self, start: date, days: int) -> date:
        """Ngày xong của việc bắt đầu ``start``, dài ``days`` ngày làm việc (ngày bắt đầu là ngày thứ nhất; 0 = mốc, xong ngay ngày đó)."""
        s = self.fwd(start)
        return s if days <= 1 else self.add(s, days - 1)

    def start_of(self, end: date, days: int) -> date:
        e = self.back(end)
        return e if days <= 1 else self.add(e, -(days - 1))


# ── đọc ô ──

def parse_iso(v: str) -> date | None:
    try:
        return date.fromisoformat(v[:10]) if v and re.match(r"\d{4}-\d{2}-\d{2}", v) else None
    except ValueError:
        return None


_UNIT_DAYS = {"d": 1, "day": 1, "days": 1, "ngay": 1, "edays": 1, "eday": 1, "w": 5, "wk": 5, "wks": 5, "week": 5, "weeks": 5, "tuan": 5,
              "mo": 20, "mon": 20, "mons": 20, "month": 20, "months": 20, "thang": 20}
_UNIT_HOURS = {"h", "hr", "hrs", "hour", "hours", "gio"}
_DUR = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([a-zA-ZÀ-ỹ]*)\s*\??\s*$")


def parse_duration(v: str) -> tuple[int | None, str]:
    """Số ngày LÀM VIỆC từ ô "5", "5 ngày", "5d", "2 tuần", "2 wks", "12h", "0" (mốc). Trả (số ngày | None, lưu ý nếu không đọc được). Số lẻ làm tròn lên."""
    t = v.strip()
    if not t:
        return None, ""
    m = _DUR.match(t)
    if not m:
        return None, f"Số ngày “{t[:30]}” chưa hiểu — bỏ qua ô này"
    n = float(m.group(1).replace(",", "."))
    unit = fold(m.group(2))
    if unit in _UNIT_HOURS:
        return (0 if n == 0 else max(1, math.ceil(n / 8))), ""
    if unit and unit not in _UNIT_DAYS:
        return None, f"Đơn vị “{m.group(2)}” của số ngày chưa hiểu — bỏ qua ô này"
    days = n * _UNIT_DAYS.get(unit, 1)
    return (0 if days == 0 else max(1, math.ceil(days))), ""


def parse_percent(values: Iterable[str]) -> list[int | None]:
    """% hoàn thành cả cột: ô "50%" / 50 → 50; cột toàn số ≤ 1 (ô định dạng % của Excel lưu 0,5) → ×100. Không đọc được ⇒ None."""
    raw = [v.strip() for v in values]
    nums: list[float | None] = []
    for v in raw:
        t = v.replace("%", "").replace(",", ".").strip()
        try:
            nums.append(float(t) if t else None)
        except ValueError:
            nums.append(None)
    explicit = [("%" in v) for v in raw]
    plain = [n for n, e in zip(nums, explicit, strict=True) if n is not None and not e]
    fraction = bool(plain) and all(0 <= n <= 1 for n in plain) and any(n != int(n) or n == 1 for n in plain) and max(plain) <= 1
    out: list[int | None] = []
    for n, e in zip(nums, explicit, strict=True):
        if n is None:
            out.append(None)
        else:
            val = n * 100 if (fraction and not e) else n
            out.append(max(0, min(100, round(val))))
    return out


def status_of(percent: int | None) -> str | None:
    if percent is None:
        return None
    return "done" if percent >= 100 else "doing" if percent > 0 else "todo"


# ── tham chiếu việc trước ──

_DEP = re.compile(r"^(?P<ref>.+?)\s*(?P<kind>FS|SS|FF|SF)?\s*(?P<lag>[+-]\s*\d+(?:[.,]\d+)?)?\s*(?P<unit>[a-zA-ZÀ-ỹ%]*)\s*$", re.I)


def parse_lag(num: str, unit: str) -> tuple[int, str]:
    n = float(num.replace(" ", "").replace(",", "."))
    u = fold(unit)
    if u == "%":
        return 0, "độ trễ theo % chưa hỗ trợ — coi như 0"
    if u in _UNIT_HOURS:
        return math.ceil(abs(n) / 8) * (1 if n >= 0 else -1), ""
    if u and u not in _UNIT_DAYS:
        return 0, f"đơn vị độ trễ “{unit}” chưa hiểu — coi như 0"
    return round(n * _UNIT_DAYS.get(u, 1)), ""


def split_deps(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"[;,\n]", text) if p.strip()]


# ── cấp bậc ──

_ROMAN = re.compile(r"^[IVXLC]+[.)]?$")
_LETTER = re.compile(r"^[A-Z][.)]?$")
_WBS = re.compile(r"^\d+(?:\.\d+)*\.?$")


def _wbs_parts(code: str) -> list[str] | None:
    c = code.strip()
    if not _WBS.match(c):
        return None
    return c.rstrip(".").split(".")


def _has_data(r: Row) -> bool:
    return any(x.strip() for x in (r.start, r.due, r.duration, r.assignee, r.after, r.percent))


def link_hierarchy(items: list[Item]) -> None:
    """Gắn cha / con. Ưu tiên: cột cấp → mã WBS có dấu chấm → STT La Mã / chữ cái → dòng tiêu đề nhóm (dòng trống dữ liệu đứng trước dòng có dữ liệu)."""
    levels = [i.row.level.strip() for i in items]
    if sum(1 for x in levels if x.isdigit()) >= max(2, len(items) // 2):
        stack: list[tuple[int, Item]] = []
        for it, lv in zip(items, levels, strict=True):
            n = int(lv) if lv.isdigit() else (stack[-1][0] if stack else 1)
            while stack and stack[-1][0] >= n:
                stack.pop()
            if stack:
                it.parent = stack[-1][1]
            stack.append((n, it))
    else:
        by_code = {c: it for it in items if (c := it.row.code.strip().rstrip(".")) and _wbs_parts(c)}
        dotted = sum(1 for c in by_code if "." in c)
        if dotted:
            for it in items:
                parts = _wbs_parts(it.row.code)
                while parts and len(parts) > 1:
                    parts = parts[:-1]
                    if (p := by_code.get(".".join(parts))) is not None and p is not it:
                        it.parent = p
                        break
        else:
            heads = [it for it in items if _ROMAN.match(it.row.code.strip()) or (_LETTER.match(it.row.code.strip()) and not it.row.code.strip().isdigit())]
            if heads and len(heads) < len(items):
                cur: Item | None = None
                for it in items:
                    if it in heads:
                        cur = it
                    elif cur is not None:
                        it.parent = cur
            else:   # dòng tiêu đề nhóm: không có dữ liệu, dòng kế tiếp có dữ liệu
                cur = None
                for idx, it in enumerate(items):
                    nxt = items[idx + 1] if idx + 1 < len(items) else None
                    if not _has_data(it.row) and nxt is not None and _has_data(nxt.row):
                        cur = it
                        continue
                    if not _has_data(it.row) and cur is not None and nxt is not None and not _has_data(nxt.row):
                        pass   # chuỗi dòng chỉ có tên: là việc không ngày dưới nhóm hiện tại
                    if cur is not None:
                        it.parent = cur
    for it in items:
        if it.parent is not None:
            it.parent.children.append(it)
    for it in items:
        it.is_group = bool(it.children)
        d, p = 0, it.parent
        while p is not None:
            d, p = d + 1, p.parent
        it.depth = d


def phase_of(it: Item) -> str:
    chain: list[str] = []
    p = it.parent
    while p is not None:
        chain.append(p.title)
        p = p.parent
    chain.reverse()
    if not chain:
        return ""
    text = chain[0] if len(chain) == 1 else f"{chain[0]} › {chain[-1]}"
    return text if len(text) <= PHASE_LEN else text[:PHASE_LEN - 1] + "…"


def descendants(it: Item) -> list[Item]:
    out: list[Item] = []
    for c in it.children:
        out.extend(descendants(c) if c.is_group else [c])
    return out


# ── bản dựng cả kế hoạch ──

def build(rows: list[Row], work: WorkDays) -> Built:
    ignored: list[tuple[int, str]] = []
    items: list[Item] = []
    for r in rows:
        if not r.title.strip():
            ignored.append((r.line, "không có tên việc"))
            continue
        items.append(Item(r, len(items) + 1))
    notes: list[str] = []
    link_hierarchy(items)
    pcts = parse_percent([i.row.percent for i in items])
    for it, pc in zip(items, pcts, strict=True):
        it.status = status_of(pc)
        it.phase = it.row.phase.strip()[:PHASE_LEN] or phase_of(it)
        for attr, raw, name in (("start", it.row.start, "Bắt đầu"), ("due", it.row.due, "Hạn")):
            if not raw.strip():
                continue
            d = parse_iso(raw)
            if d is None:
                it.warnings.append(f"{name} “{raw.strip()[:30]}” không đọc được — bỏ qua ô này")
            else:
                setattr(it, attr, d)
        it.duration, w = parse_duration(it.row.duration)
        if w:
            it.warnings.append(w)
        if it.start and it.due and it.start > it.due:
            it.error = f"bắt đầu {it.start:%d/%m/%Y} sau hạn {it.due:%d/%m/%Y}"
    groups = [i for i in items if i.is_group]
    tasks = [i for i in items if not i.is_group]
    _resolve_deps(items, notes)
    _inherit_group_deps(items)
    graph = {id(t): [id(d.target) for d in t.deps] for t in tasks}
    names = {id(t): t for t in tasks}
    cyc = find_cycle(graph)
    blocking = ""
    if cyc:
        blocking = ("Các việc chờ nhau thành vòng: " + " → ".join(names[x].label() for x in cyc) +
                    ". Sửa cột “Việc trước” ở các dòng này rồi chọn lại file.")
        return Built(items, tasks, groups, notes, blocking, ignored)
    _schedule(tasks, work)
    return Built(items, tasks, groups, notes, "", ignored)


def _lookup(items: list[Item]) -> tuple[dict[str, Item], dict[str, Item], dict[str, Item], dict[str, list[Item]], bool]:
    by_code = {c: i for i in items if (c := i.row.code.strip().rstrip(".").lower())}
    by_id = {c: i for i in items if (c := i.row.row_id.strip().lower())}
    by_pos = {str(i.pos): i for i in items}
    by_title: dict[str, list[Item]] = {}
    for i in items:
        by_title.setdefault(key_of(i.title), []).append(i)
    return by_code, by_id, by_pos, by_title, bool(by_id)


def _resolve_deps(items: list[Item], notes: list[str]) -> None:
    by_code, by_id, by_pos, by_title, has_id = _lookup(items)
    # số dòng Excel cũng là một cách người ta gọi ("dòng 7"): chỉ dùng khi viết rõ chữ "dòng"
    by_line = {str(i.line): i for i in items}
    for it in items:
        text = it.row.after.strip()
        if not text:
            continue
        whole = by_title.get(key_of(text))
        parts = [text] if whole and len(whole) == 1 and not re.fullmatch(r"[\d.]+", text) else split_deps(text)
        seen: set[int] = set()
        for part in parts:
            m = _DEP.match(part)
            ref, kind = (m.group("ref").strip(), (m.group("kind") or "FS").upper()) if m else (part, "FS")
            lag = 0
            if m and m.group("lag"):
                lag, w = parse_lag(m.group("lag"), m.group("unit") or "")
                if w:
                    it.warnings.append(f"Việc trước “{part}”: {w}")
            elif m and m.group("unit") and not m.group("kind") and fold(m.group("unit")):
                ref = part.strip()   # chữ thừa không phải đơn vị độ trễ ⇒ cả cụm là tên việc
                kind, lag = "FS", 0
            low = ref.lower().rstrip(".")
            target: Item | None = None
            if (lm := re.fullmatch(r"(?:dòng|dong|row|line)\s*(\d+)", low)):
                target = by_line.get(lm.group(1))
            elif "." in low or not low.isdigit():
                target = by_code.get(low) or (by_id.get(low) if has_id else None)
            else:
                order = (by_id, by_code, by_pos) if has_id else (by_code, by_pos)
                target = next((hit for table in order if (hit := table.get(low)) is not None), None)
            if target is None:
                hits = by_title.get(key_of(ref), [])
                if len(hits) == 1:
                    target = hits[0]
                elif len(hits) > 1:
                    it.warnings.append(f"Việc trước “{ref}” trùng tên với {len(hits)} việc — không biết là việc nào, bỏ qua")
                    continue
            if target is None:
                it.warnings.append(f"Việc trước “{ref}” không tìm thấy trong file — bỏ qua")
                continue
            if target is it or id(target) in seen:
                if target is it:
                    it.warnings.append("Việc không thể chờ chính nó — bỏ qua")
                continue
            seen.add(id(target))
            if kind in ("FF", "SF"):
                it.warnings.append(f"Kiểu phụ thuộc {kind} (“{part}”) chỉ ghi là “chờ”, không tính ngày theo kiểu này")
            it.deps.append(Dep(target, kind, lag, part))


def _inherit_group_deps(items: list[Item]) -> None:
    """Quy mọi phụ thuộc về VIỆC LÁ: dòng nhóm có việc trước ⇒ các việc đầu nhóm chờ nó; chờ một nhóm ⇒ chờ mọi việc lá của nhóm. Tối đa ``MAX_DEPS`` mỗi việc."""
    for it in (i for i in items if i.is_group):
        leaves = descendants(it)
        inner = {id(x) for x in leaves}
        heads = [x for x in leaves if not any(id(d.target) in inner for d in x.deps)]
        for d in it.deps:
            for h in heads:
                h.deps.append(Dep(d.target, d.kind, d.lag, d.raw))
        it.deps = []
    for it in (i for i in items if not i.is_group):
        uniq: dict[int, Dep] = {}
        for d in it.deps:
            for leaf in (descendants(d.target) if d.target.is_group else [d.target]):
                if leaf is not it:
                    uniq.setdefault(id(leaf), Dep(leaf, d.kind, d.lag, d.raw))
        it.deps = list(uniq.values())
        if len(it.deps) > MAX_DEPS:
            it.warnings.append(f"Chờ {len(it.deps)} việc — chỉ ghi {MAX_DEPS} việc đầu")
            it.deps = it.deps[:MAX_DEPS]


def _order(tasks: list[Item]) -> list[Item]:
    """Thứ tự tô-pô: việc trước đứng trước (giữ thứ tự file khi không ràng buộc). Gọi khi đã chắc không có vòng."""
    done: set[int] = set()
    out: list[Item] = []

    def visit(t: Item) -> None:
        if id(t) in done:
            return
        done.add(id(t))
        for d in t.deps:
            if not d.target.is_group:
                visit(d.target)
        out.append(t)

    for t in tasks:
        visit(t)
    return out


def _schedule(tasks: list[Item], work: WorkDays) -> None:
    """Điền ngày còn thiếu theo thứ tự tô-pô. Ngày CÓ trong file giữ nguyên; chỉ tính phần thiếu từ số ngày + việc trước (FS: ngày làm việc sau khi việc trước xong + lag;
    SS: cùng ngày bắt đầu + lag). Dùng lịch ngày làm việc ``work``."""
    for t in _order(tasks):
        if t.error:
            continue
        floor: date | None = None   # sớm nhất được bắt đầu theo việc trước
        for d in t.deps:
            p = d.target
            if p.error:
                continue
            if d.kind == "SS" and p.start:
                cand = work.fwd(work.add(work.fwd(p.start), d.lag) if d.lag else p.start)
            elif d.kind in ("FS", "FF", "SF") and p.due:
                cand = work.add(work.back(p.due), 1 + d.lag) if d.lag >= -1 else work.add(work.back(p.due), d.lag)
                cand = work.fwd(cand)
            else:
                continue
            if d.kind in ("FF", "SF"):
                continue
            floor = cand if floor is None or cand > floor else floor
        if t.start and floor and t.start < floor:
            before = next((d.target for d in t.deps if d.target.due and d.target.due >= t.start and d.kind == "FS"), None)
            if before is not None:
                t.warnings.append(f"Bắt đầu {t.start:%d/%m} nhưng {before.label()} mới xong {before.due:%d/%m} — giữ ngày trong file")
        dur = t.duration
        if t.start is None and t.due is None:
            if floor is not None and dur is not None:
                t.start, t.computed = floor, t.computed | {"start"}
                t.due, t.computed = work.end_of(t.start, dur), t.computed | {"due"}
            elif floor is not None:
                t.start, t.computed = floor, t.computed | {"start"}
        elif t.start is not None and t.due is None:
            if dur is not None:
                t.due, t.computed = work.end_of(t.start, dur), t.computed | {"due"}
        elif t.start is None and t.due is not None:
            if dur is not None:
                t.start, t.computed = work.start_of(t.due, dur), t.computed | {"start"}
            elif floor is not None and floor <= t.due:
                t.start, t.computed = floor, t.computed | {"start"}
        if t.start and t.due and t.start > t.due:
            t.error = f"bắt đầu {t.start:%d/%m/%Y} sau hạn {t.due:%d/%m/%Y}"


# ── khớp với việc đã có (nhập lại) ──

def match_existing(tasks: list[Item], existing: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    """Việc trong file ↔ việc đã có của kế hoạch. ``existing``: {id, title, plan_ref, ...}. Ba lượt, mỗi việc đã có chỉ nhận một dòng:
    (1) cùng mã + cùng tên; (2) cùng tên (duy nhất ở cả hai phía) — mã đổi / dòng dịch chỗ; (3) cùng mã dạng WBS có dấu chấm hoặc mã chữ (không phải STT 1, 2, 3 chạy số
    — chèn dòng làm lệch) nhưng tên đổi ⇒ đổi tên. Trả ({id(Item): việc đã có}, việc đã có không còn trong file)."""
    taken: set[Any] = set()
    out: dict[int, dict[str, Any]] = {}

    def code_of(it: Item) -> str:
        return it.row.code.strip().rstrip(".").lower()

    pool = list(existing)
    for t in tasks:   # lượt 1
        c = code_of(t)
        if not c:
            continue
        hit = next((e for e in pool if e["id"] not in taken and (e.get("plan_ref") or "").lower() == c and key_of(e["title"]) == key_of(t.title)), None)
        if hit:
            out[id(t)] = hit
            taken.add(hit["id"])
    rest = [t for t in tasks if id(t) not in out]
    free = [e for e in pool if e["id"] not in taken]
    for t in rest:   # lượt 2
        ks = key_of(t.title)
        a = [e for e in free if key_of(e["title"]) == ks and e["id"] not in taken]
        b = [x for x in rest if key_of(x.title) == ks and id(x) not in out]
        if len(a) == 1 and len(b) == 1:
            out[id(t)] = a[0]
            taken.add(a[0]["id"])
    for t in [x for x in rest if id(x) not in out]:   # lượt 3
        c = code_of(t)
        if not c or (c.isdigit()):
            continue
        a = [e for e in pool if e["id"] not in taken and (e.get("plan_ref") or "").lower() == c]
        if len(a) == 1:
            out[id(t)] = a[0]
            taken.add(a[0]["id"])
    return out, [e for e in pool if e["id"] not in taken]
