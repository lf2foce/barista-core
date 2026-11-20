"""BẢN ĐỒ MODULE sinh từ code (ROADMAP N4): đọc sổ đăng ký sau khi nạp mọi module + quét mã (AST) để viết ``docs/MODULE-MAP.md``.

    cd backend && uv run python scripts/module_map.py            # viết lại docs/MODULE-MAP.md
    cd backend && uv run python scripts/module_map.py --check    # chỉ so, lệch ⇒ mã thoát 1

Nội dung: gói (loại, gói bắt buộc, module), module (gói, schema, file mã, số thao tác), rồi từng module: tài nguyên quyền, hợp đồng nội bộ
(``callers=``), họ thao tác được gọi bằng quyền hệ thống (``consumes``), điểm cắm hỏi / cắm vào, sự kiện phát / nghe, cò, chỉ số, tìm nhanh,
đối tượng, bộ kiểm, việc theo dõi, vòng quét. Mọi danh sách sắp theo tên, không ghi giờ ⇒ cùng code ra cùng file
(``tests/test_module_map.py`` đỏ khi file đã commit lệch code).
"""

import argparse
import ast
import inspect
import re
import sys
from collections.abc import Iterable
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import barista.modules  # noqa: E402, F401 — nạp mọi module để sổ đăng ký đủ
from barista.kernel import loader, registry  # noqa: E402
from tests.module_src import SRC, module_files  # noqa: E402 — chỗ DUY NHẤT biết module ↔ file (0088)

OUT = BACKEND.parent / "docs" / "MODULE-MAP.md"
REGEN = "cd backend && uv run python scripts/module_map.py"
KIND_LABEL = {"core": "lõi", "capability": "năng lực", "industry": "ngành"}


def _schemas() -> set[str]:
    """Schema Postgres khai trong migration (bản gốc + các file sau)."""
    out: set[str] = set()
    for f in (SRC / "migrations").rglob("*.sql"):
        out |= set(re.findall(r"CREATE SCHEMA (?:IF NOT EXISTS )?([a-z_]+)", f.read_text()))
    return out


def _strings(node: ast.expr) -> list[str]:
    """Tên cố định ở tham số đầu (chuỗi, hoặc ``a if … else b`` hai chuỗi); tên tính lúc chạy ⇒ bỏ qua."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _strings(node.body) + _strings(node.orelse)
    return []


def _scan(files: list[Path]) -> tuple[set[str], set[str]]:
    """(sự kiện phát bằng ``.emit("…")``, điểm cắm hỏi bằng ``.plugs("…")`` / ``.plugged("…")``) trong mã của một module."""
    emits: set[str] = set()
    asks: set[str] = set()
    for f in files:
        for n in ast.walk(ast.parse(f.read_text())):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.args:
                if n.func.attr == "emit":
                    emits |= {s for s in _strings(n.args[0]) if "." in s}
                elif n.func.attr in ("plugs", "plugged"):
                    asks |= set(_strings(n.args[0]))
    return emits, asks


def _sources(m: str, files: dict[str, list[Path]], ops: list[registry.Op]) -> list[Path]:
    """File mã của module: ``modules/<khoá>.py`` hoặc package (0088); module lõi ngoài ``modules/`` (``core``) ⇒ file chứa handler."""
    if m in files:
        return files[m]
    reg = Path(registry.__file__).resolve()   # handler sinh trong registry (``Module.dataset``) không phải mã của module
    return sorted({Path(inspect.getfile(o.handler)).resolve() for o in ops} - {reg})


def _code(items: Iterable[str]) -> str:
    return ", ".join(f"`{x}`" for x in sorted(items)) or "—"


def external_packs() -> set[str]:
    """Khoá gói của bản phân phối NGOÀI lõi đang cài (``barista.packs`` mà ``dist`` khác ``barista``). Bản đồ này mô tả repo lõi: cài hay không cài gói
    ngoài thì file sinh ra vẫn y hệt (``tests/test_module_map.py`` chạy được cả hai cách) — phần gói ngoài do tài liệu của chính gói mô tả."""
    return {e.key for e in loader.LOADED if e.dist != "barista"}


def render() -> str:
    files = module_files()
    schemas = _schemas()
    outside = external_packs()
    mods = sorted(m for m in registry.MODULES if registry.MODULE_PACK[m] not in outside)
    ops_of: dict[str, list[registry.Op]] = {m: [] for m in mods}
    for op in registry.OPS.values():
        if op.module in ops_of:
            ops_of[op.module].append(op)
    all_ops = [op for ops in ops_of.values() for op in ops]
    subscribers = [s for s in registry.SUBSCRIBERS.values() if (s.module or s.name.split(".")[0]) in ops_of]
    plugs = {pt: [o for o in cmds if o.split(".")[0] in ops_of] for pt, cmds in registry.PLUGS.items()}
    srcs = {m: _sources(m, files, ops_of[m]) for m in mods}
    scanned = {m: _scan(srcs[m]) for m in mods}

    lines = ["# Bản đồ module", "",
             f"> Sinh từ code — KHÔNG sửa tay. Sinh lại: `{REGEN}`; `tests/test_module_map.py` đỏ khi file này lệch code.",
             "> Vì sao chia như vậy: [OVERVIEW.md](OVERVIEW.md), ADR 0045, "
             "0056, 0088.", "",
             f"{len(registry.PACKS) - len(outside)} gói · {len(mods)} module · {len(all_ops)} thao tác "
             f"({sum(1 for o in all_ops if o.internal)} nội bộ) · {len(subscribers)} bên nghe sự kiện.", "",
             "## Gói", "", "| Gói | Tên | Loại | Bắt buộc | Module |", "|---|---|---|---|---|"]
    for key in sorted(k for k in registry.PACKS if k not in outside):
        p = registry.PACKS[key]
        members = [m for m in mods if registry.MODULE_PACK[m] == key]
        lines.append(f"| `{key}` | {p.title} | {KIND_LABEL.get(p.kind, p.kind)}{" (luôn bật)" if p.always_on else ""} | {_code(p.requires)} | {_code(members)} |")

    lines += ["", "## Module", "",
              "| Module | Tên | Gói | Schema | Mã | Lệnh | Truy vấn | Nội bộ |", "|---|---|---|---|---|---|---|---|"]
    for m in mods:
        ops = ops_of[m]
        src = srcs[m]
        where = (f"`modules/{m}/` ({len(src)} file)" if m in files and src[0].name == "__init__.py"
                 else ", ".join(f"`{f.relative_to(SRC)}`" for f in src))
        lines.append(f"| `{m}` | {registry.MODULES[m]} | `{registry.MODULE_PACK[m]}` | {f'`{m}`' if m in schemas else '—'} | {where} "
                     f"| {sum(1 for o in ops if o.kind == 'command')} | {sum(1 for o in ops if o.kind == 'query')} | {sum(1 for o in ops if o.internal)} |")

    lines += ["", "## Từng module", ""]
    for m in mods:
        ops = sorted(ops_of[m], key=lambda o: o.name)
        emits, asks = scanned[m]
        asks |= {pt for pt in plugs if pt.split(".")[0] == m}
        src = srcs[m]
        lines += [f"### `{m}` — {registry.MODULES[m]}", ""]
        if m in files and src[0].name == "__init__.py":
            lines.append("- **Mã:** " + ", ".join(f"`{f.relative_to(SRC / 'modules')}`" for f in src))
        lines.append("- **Tài nguyên quyền:** " + _code([r for r, mod in registry.RESOURCE_MODULE.items() if mod == m]))
        if contracts := [o for o in ops if o.callers]:
            lines.append("- **Hợp đồng quyền hệ thống** (thao tác ← module được gọi):")
            lines += [f"  - `{o.name}` ← {_code(o.callers)}" for o in contracts]
        if opened := [o for o in ops if o.internal and o.family in registry.OPEN_FAMILIES]:
            lines.append("- **Mở cho gói ngoài** (thao tác → họ; gói khai `consumes`):")
            lines += [f"  - `{o.name}` → `{o.family}`" for o in opened]
        if fams := [f for f in registry.FAMILIES if m in registry.FAMILY_CALLERS[f]]:
            lines.append("- **Gọi họ thao tác bằng quyền hệ thống:** " + _code(fams))
        if asks:
            lines.append("- **Điểm cắm của module này** (điểm ← thao tác cắm vào):")
            lines += [f"  - `{pt}` ← {_code(plugs.get(pt, []))}" for pt in sorted(asks)]
        plugs_into = sorted(pt for pt, ops_ in plugs.items() for o in ops_ if o.split(".")[0] == m)
        if plugs_into:
            lines.append("- **Cắm vào:** " + _code(plugs_into))
        if slots := sorted(pt for pt, cmds in registry.ACTION_PLUGS.items() for c in cmds if c.split(".")[0] == m):
            lines.append("- **Thay nút:** " + _code(slots))
        if emits:
            lines.append("- **Sự kiện phát:** " + _code(emits))
        if subs := sorted((s for s in subscribers if s.name.split(".")[0] == m), key=lambda s: s.name):
            lines.append("- **Nghe sự kiện:** " + ", ".join(
                f"`{'(mọi cò)' if s.event_type == registry.ANY_TRIGGER else s.event_type}` → `{s.name}`" for s in subs))
        extra = [("Cò tự động hoá", [t for t, tr in registry.TRIGGERS.items() if tr.module == m]),
                 ("Chỉ số", [k for k in registry.METRICS if k.split(".")[0] == m]),
                 ("Tìm nhanh", [k for k in registry.SEARCHES if k.split(".")[0] == m]),
                 ("Đối tượng", [k for k, (_, op) in registry.SUBJECTS.items() if op.split(".")[0] == m]),
                 ("Bộ kiểm", [k for k in registry.CHECKERS if k.split(".")[0] == m]),
                 ("Việc theo dõi", [k for k, w in registry.WORK_KINDS.items() if w.module == m]),
                 ("Vòng quét", [k for k in registry.SWEEPS if k.split(".")[0] == m])]
        for label, items in extra:
            if items:
                lines.append(f"- **{label}:** " + _code(items))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--check", action="store_true", help="chỉ so với file đã có, lệch ⇒ mã thoát 1")
    args = ap.parse_args()
    text = render()
    if args.check:
        same = OUT.exists() and OUT.read_text() == text
        print("docs/MODULE-MAP.md khớp code" if same else f"docs/MODULE-MAP.md lệch code — chạy: {REGEN}")
        return 0 if same else 1
    OUT.write_text(text)
    print(f"đã viết {OUT.relative_to(BACKEND.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
