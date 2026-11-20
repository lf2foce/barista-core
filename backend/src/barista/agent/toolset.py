"""Bộ công cụ MỖI LƯỢT — "tìm trước" (quyết định 0095). Chọn công cụ nào trợ lý THẤY, không quyết công cụ nào chạy được (quyền vẫn ở executor).

* PHẦN CỐ ĐỊNH: thao tác module khai ``category="core"`` + thao tác trên MÀN của phân hệ chính của vai (màn ghim trước) + module vừa dùng trong
  cuộc trò chuyện / của thẻ vừa xác nhận — chỉ trong danh mục người hỏi chạy được (``chat.catalog``), dưới trần số công cụ VÀ dung lượng.
* PHẦN TÌM: code tìm K công cụ khớp CÂU HỎI nhất (BM25 trên tên / tiêu đề / mô tả / nhãn trường, bỏ dấu; câu nhiều ý tìm theo từng vế) trong
  phần còn lại của danh mục — mang luôn từ đầu lượt, KHÔNG thêm lượt gọi model. Gửi SAU phần cố định (giữ bộ đệm prompt).
* LƯỚI AN TOÀN: ``core.open_tools(query)`` — khi vẫn thiếu, model tìm thêm theo từ khoá (tối đa ``OPEN_MAX`` lần / lượt); mô tả cố định.
* ``settings.agent_tools = "all"`` ⇒ không dùng file này: mang mọi thao tác (giữ để so bằng ``scripts/eval_matrix.py``).
* BỘ CỦA VAI (0218, ``role_plan``) — vai có dòng ``core.role_tools``: phần cố định = lõi luôn mang + MỌI công cụ của bộ (∩ danh mục người hỏi), giống hệt
  nhau mọi lượt của vai ⇒ đọc được từ bộ đệm prompt; nhóm vừa dùng / vừa mở mang sau; tìm theo câu hỏi chỉ ``agent_tool_k_toolset`` công cụ NGOÀI bộ.
  Model luôn có DANH MỤC NGẮN các nhóm khác người hỏi dùng được (một dòng / nhóm) trong ``core.open_group`` — mở cả nhóm cho phần còn lại của lượt
  (lượt sau còn mang nếu vừa dùng — ``chat.recent_modules``); không có ngõ cụt. Vai không có dòng ⇒ ``plan`` như trên.
* Không biết tên gói / module nào (0069): phân hệ, màn, vai đều đọc từ registry / workspaces.
"""

import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import pairwise

from barista import workspaces
from barista.config import settings
from barista.kernel import ai_tools, registry, vocab

OPEN_MAX = 2      # số lần core.open_tools mỗi lượt — đủ cho một lần tìm sót, không để model đi vòng (eval 05/11: gọi 4 lần)
OPEN_K = 6        # số công cụ mỗi lần tìm thêm
GROUP_OPEN_MAX = 2    # số lần core.open_group mỗi lượt (0218)
GROUP_PER_CALL = 2    # số nhóm tối đa một lần mở — câu xuyên hai nhóm mở MỘT lần (không thêm lượt gọi model)
INDEX_TITLES = 5      # số tên công cụ nêu trong một dòng danh mục nhóm
GROUP_TOOLS_MAX = 12  # nhóm lớn hơn thế chỉ mở phần khớp từ khoá nhất (``open_group``)


def _screen_ops(s: object) -> list[str]:
    names = [getattr(s, "query", "")]
    names += [a.command for a in (*getattr(s, "actions", ()), *getattr(s, "row_actions", ()))]
    if (o := getattr(s, "open", None)) is not None:
        names.append(o.query)
    return [n for n in names if n]


def _size(op: registry.Op, describe: dict[str, str], schema: dict[str, dict[str, object]]) -> int:
    return len(op.name) + len(describe.get(op.name, "")) + len(json.dumps(schema.get(op.name, {}), ensure_ascii=False))


@dataclass
class Plan:
    visible: set[str]                                        # công cụ thường (không phải thiết lập) đang mang
    rest: set[str] = field(default_factory=set)              # phần còn lại của danh mục người hỏi — chỗ ``open_tools`` tìm thêm
    retrieved: list[str] = field(default_factory=list)       # công cụ code tìm theo câu hỏi (gửi SAU phần cố định)
    opened: int = 0                                          # số lần ``open_tools`` đã dùng trong lượt
    terms: tuple[tuple[str, str], ...] = ()                  # từ vựng công ty (0124): tìm công cụ theo CHỮ công ty dùng ("dự án"), không theo chữ trung tính
    # bộ của vai (0218): phần cố định (gửi trước, như nhau mọi lượt của vai) + danh mục nhóm khác (khoá → dòng mô tả) + nhóm đã mở trong lượt
    stable: set[str] | None = None
    index: dict[str, str] = field(default_factory=dict)
    groups_opened: list[str] = field(default_factory=list)
    group_opens: int = 0


def _words(text: str) -> list[str]:
    plain = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    plain = "".join(ch for ch in plain if unicodedata.category(ch) != "Mn")
    uni = [w for w in re.findall(r"[a-z0-9]+", plain) if len(w) > 1]
    return uni + [f"{a}_{b}" for a, b in pairwise(uni)]   # cặp từ: "khu vuc", "dong tien" khớp chặt hơn từ đơn


@lru_cache(maxsize=4096)
def _doc(name: str, terms: tuple[tuple[str, str], ...] = ()) -> tuple[str, ...]:
    op = registry.OPS[name]
    labels = " ".join(str(f.title or "") for f in op.input.model_fields.values())
    text = f"{name.replace('.', ' ').replace('_', ' ')} {op.title} {registry.MODULES.get(op.module, '')} {op.description} {labels}"
    return tuple(_words(vocab.resolve(text, dict(terms))))


def terms_key(terms: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((terms or {}).items()))


def search(question: str, names: Iterable[str], k: int, terms: tuple[tuple[str, str], ...] = ()) -> list[str]:
    """BM25 (k1 = 1,2, b = 0,75) — K công cụ khớp câu hỏi nhất trong ``names``; điểm 0 thì không lấy."""
    return _bm25(question, {n: Counter(_doc(n, terms)) for n in names}, k)


def rank_texts(question: str, texts: Mapping[str, str], k: int) -> list[str]:
    """Như ``search`` nhưng trên chữ tuỳ ý (khoá → chữ) — vd tên + "khi dùng" của kỹ năng trợ lý (0198)."""
    return _bm25(question, {n: Counter(_words(t)) for n, t in texts.items()}, k)


def _bm25(question: str, docs: dict[str, Counter[str]], k: int) -> list[str]:
    q = set(_words(question))
    if not q or not docs:
        return []
    avg = sum(sum(c.values()) for c in docs.values()) / len(docs)
    df = Counter(w for c in docs.values() for w in c if w in q)
    scores = {}
    for n, c in docs.items():
        size, sc = sum(c.values()), 0.0
        for w in q & c.keys():
            idf = math.log(1 + (len(docs) - df[w] + 0.5) / (df[w] + 0.5))
            sc += idf * c[w] * 2.2 / (c[w] + 1.2 * (0.25 + 0.75 * size / avg))
        if sc > 0:
            scores[n] = sc
    return sorted(scores, key=lambda n: (-scores[n], n))[:k]


def retrieve_for(question: str, names: Iterable[str], k: int, terms: tuple[tuple[str, str], ...] = ()) -> list[str]:
    """Câu nhiều ý ("đào tạo team A, và có lead mới thì báo…") ⇒ tìm theo TỪNG VẾ rồi gộp xen kẽ (mỗi vế có phần), cộng cả câu; tối đa K."""
    pool = list(names)
    parts = [x for x in re.split(r"[,.;?!]|\s(?:và|rồi|sau đó|đồng thời)\s", question) if len(_words(x)) >= 2]
    lists = [search(x, pool, k, terms) for x in [question, *parts]] if len(parts) > 1 else [search(question, pool, k, terms)]
    out: list[str] = []
    for rank in range(k):
        for lst in lists:
            if rank < len(lst) and lst[rank] not in out:
                out.append(lst[rank])
    return out[:k]


def plan(role: str, catalog: Iterable[str], *, recent_modules: Iterable[str] = (), describe: dict[str, str] | None = None,
         schema: dict[str, dict[str, object]] | None = None, question: str = "", retrieve: int = 0,
         terms: Mapping[str, str] | None = None) -> Plan:
    """Phần cố định (lõi → màn ghim → màn của phân hệ chính → module vừa dùng; dừng ở trần) + K công cụ tìm theo câu hỏi."""
    have = set(catalog)
    describe, schema = describe or {}, schema or {}
    order: list[str] = [n for n in have if registry.tool_kind(registry.OPS[n]) == "core"]
    first, pins = workspaces.role_home(role)
    all_ws = {ws.key: ws for ws in workspaces.workspaces()}
    by_screen = {s.key: s for ws in all_ws.values() for s in ws.screens}
    for key in pins:
        if (s := by_screen.get(key)) is not None:
            order += _screen_ops(s)
    for wk in first:
        for s in all_ws[wk].screens if wk in all_ws else ():
            order += _screen_ops(s)
    # module vừa dùng / của thẻ vừa xác nhận: LUÔN mang (như lõi), không bị trần cắt — thiếu là không làm tiếp được việc đang dở
    # (test_meeting_note: màn mới góp vào phân hệ làm phần cố định đầy hơn ⇒ công cụ CRM của lượt chạy tiếp từng bị cắt)
    keep = {n for n, op in registry.OPS.items() if op.module in set(recent_modules)}
    visible: set[str] = set()
    used = 0
    for n in [*sorted(keep), *order]:
        if n not in have or n in visible or registry.tool_kind(registry.OPS[n]) == "setup":
            continue
        size = _size(registry.OPS[n], describe, schema)
        must = registry.tool_kind(registry.OPS[n]) == "core" or n in keep
        if not must and (len(visible) >= settings.agent_tool_budget or used + size > settings.agent_tool_chars):
            continue
        visible.add(n)
        used += size
    normal = {n for n in have if registry.tool_kind(registry.OPS[n]) != "setup"}
    tk = terms_key(terms)
    found = retrieve_for(question, normal - visible, retrieve, tk) if retrieve and question.strip() else []
    visible |= set(found)
    return Plan(visible, normal - visible, found, terms=tk)


def open_search(p: Plan, query: str) -> dict[str, object]:
    """Lưới an toàn: tìm theo từ khoá trong phần CHƯA mang (vẫn chỉ trong danh mục người hỏi) rồi mang luôn; tối đa ``OPEN_MAX`` lần / lượt."""
    if p.opened >= OPEN_MAX:
        return {"status": "error", "error": f"Đã tìm thêm {OPEN_MAX} lần trong lượt này — trả lời với công cụ đang có, hoặc hỏi lại người dùng."}
    p.opened += 1
    found = set(search(query, p.rest, OPEN_K, p.terms))
    if not found:
        return {"status": "ok", "opened": [], "message": "Không có công cụ nào khớp — có thể việc này nằm ngoài quyền của bạn hoặc chưa có trong hệ thống."}
    p.visible |= found
    p.rest -= found
    return {"status": "ok", "opened": sorted(n.replace(".", "__") for n in found),
            "message": f"Đã mang thêm {len(found)} công cụ khớp “{query}” — dùng ở bước tiếp theo."}


def _index_line(key: str, names: list[str], terms: Mapping[str, str] | None) -> str:
    """Một dòng danh mục: khoá — nhãn nhóm — vài tên công cụ (XEM trước, theo tên — xác định ⇒ mô tả ``open_group`` không đổi giữa các lượt)."""
    ops = sorted((registry.OPS[n] for n in names), key=lambda o: (o.kind != "query", o.name))
    titles = list(dict.fromkeys(o.title for o in ops))
    more = f" (+{len(titles) - INDEX_TITLES})" if len(titles) > INDEX_TITLES else ""
    return vocab.resolve(f"- {key}: {registry.MODULES[key]} — " + "; ".join(titles[:INDEX_TITLES]) + more, dict(terms or {}))


def role_plan(entries: Iterable[str], catalog: Iterable[str], *, recent_modules: Iterable[str] = (), question: str = "", retrieve: int = 0,
              terms: Mapping[str, str] | None = None) -> Plan:
    """Bộ của vai (0218): phần cố định = lõi luôn mang + bộ của vai (∩ danh mục người hỏi — bộ không thêm quyền); nhóm vừa dùng mang sau; tối đa
    ``retrieve`` công cụ NGOÀI phần đã mang khớp câu hỏi; danh mục nhóm khác (tính trên phần ngoài phần cố định ⇒ như nhau mọi lượt của vai)."""
    have = set(catalog)
    normal = {n for n in have if registry.tool_kind(registry.OPS[n]) != "setup"}
    stable = {n for n in normal if registry.tool_kind(registry.OPS[n]) == "core"} | (ai_tools.expand(tuple(entries)) & normal)
    recent = set(recent_modules)
    visible = stable | {n for n in normal if registry.OPS[n].module in recent}
    tk = terms_key(terms)
    found = retrieve_for(question, normal - visible, retrieve, tk) if retrieve and question.strip() else []
    visible |= set(found)
    outside: dict[str, list[str]] = {}
    for n in sorted(normal - stable):
        outside.setdefault(registry.OPS[n].module, []).append(n)
    index = {k: _index_line(k, outside[k], terms) for k in registry.MODULES if k in outside}
    return Plan(visible, normal - visible, found, terms=tk, stable=stable, index=index)


def open_group(p: Plan, keys: Iterable[str], allowed: Callable[[str], bool], query: str = "") -> dict[str, object]:
    """Mở nhóm (trong danh mục người hỏi; ``allowed`` kiểm lại quyền lúc mở) cho phần còn lại của lượt — tối đa ``GROUP_OPEN_MAX`` lần / lượt. Nhóm
    LỚN (quá ``GROUP_TOOLS_MAX`` công cụ chưa mang) chỉ mở phần khớp ``query`` nhất (BM25; không khớp gì ⇒ XEM trước, theo tên) — phần còn lại vẫn
    mở được bằng lần gọi sau với từ khoá khác (mở nguyên nhóm kế toán 51 công cụ ≈ 16k token mỗi lần gọi model — đo 14/11)."""
    want = list(dict.fromkeys(str(k).strip() for k in keys if str(k).strip()))[:GROUP_PER_CALL]
    if p.group_opens >= GROUP_OPEN_MAX:
        return {"status": "error", "error": f"Đã mở nhóm {GROUP_OPEN_MAX} lần trong lượt này — trả lời với công cụ đang có, hoặc hỏi lại người dùng."}
    if not want:
        return {"status": "error", "error": "Chọn khoá nhóm trong danh sách."}
    p.group_opens += 1
    if bad := [k for k in want if k not in p.index]:
        return {"status": "error", "error": f"Không có nhóm {', '.join(bad)} trong danh sách — chọn đúng khoá, hoặc việc này nằm ngoài quyền của bạn."}
    got: set[str] = set()
    left = 0
    for k in want:
        pool = sorted(n for n in p.rest if registry.OPS[n].module == k and allowed(n))
        if len(pool) > GROUP_TOOLS_MAX:
            best = search(query, pool, GROUP_TOOLS_MAX, p.terms) if query.strip() else []
            best += [n for n in sorted(pool, key=lambda n: (registry.OPS[n].kind != "query", n)) if n not in best]
            left += len(pool) - GROUP_TOOLS_MAX
            pool = best[:GROUP_TOOLS_MAX]
        got |= set(pool)
        if k not in p.groups_opened:
            p.groups_opened.append(k)
    p.visible |= got
    p.rest -= got
    more = f" Nhóm còn {left} công cụ chưa mở — thiếu thì gọi lại kèm từ khoá việc cần làm (query)." if left else ""
    return {"status": "ok", "opened": want, "tools": sorted(n.replace(".", "__") for n in got),
            "message": f"Đã mở nhóm {', '.join(registry.MODULES[k] for k in want)} — dùng các công cụ này ở bước tiếp theo.{more}"}
