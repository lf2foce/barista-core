"""MÃ NGUỒN CỦA MỘT MODULE (quyết định 0088) — một module là MỘT trong hai dạng: file ``modules/<khoá>.py``, hoặc package ``modules/<khoá>/``
(``__init__.py`` khai ``m = Module(...)``, các file con dùng chung ``m``). Mọi test đọc mã theo module (ranh giới schema, gọi chéo, từ ngữ ngành,
hợp đồng quyền hệ thống) đi qua đây — không test nào tự quét ``modules/*.py`` nữa, nên tách một module lớn thành package không làm hụt kiểm.

GÓI NGOÀI REPO ĐANG CÀI CŨNG ĐƯỢC QUÉT (0191): ``py_files`` / ``sql_files`` / ``module_files`` / ``module_of`` trả cả mã của mọi bản phân phối gói đang
cài trong môi trường (``barista.packs`` có ``dist`` khác ``barista``) — cùng luật ranh giới schema, gọi chéo gói, mã người, tên thao tác, múi giờ, hợp
đồng quyền hệ thống như mã trong repo. Không cài gói nào ⇒ đúng phần lõi. Dòng cho phép (ngoại lệ có lý do) liên quan tới MỘT GÓI do gói đó khai trong
``<package của gói>/core_guards.json`` (``declared``) — lõi không liệt kê tên module / thao tác / file của gói ngoài.
"""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "barista"
GUARDS_FILE = "core_guards.json"
# Danh sách cho phép một gói ngoài được khai (khoá ⇒ test đọc + dạng): thêm khoá mới ở đây khi một test cấu trúc mở danh sách cho gói.
GUARD_LISTS = {
    "resolved_first": "tests/test_internal_person_ids.py — {'<module>|<hàm>|<thao tác>': lý do}: call_system truyền inp.<mã người> đã kiểm bằng quyền người gọi",
    "write_verb_queries": "tests/test_guards.py — {'<thao tác>': lý do}: truy vấn mở đầu bằng động từ ghi",
    "read_verb_commands": "tests/test_guards.py — {'<thao tác>': lý do}: lệnh mở đầu bằng động từ đọc",
    "vn_allowed": "tests/test_timezone.py — {'<file, tính từ package của gói>': lý do}: file được dùng VN (múi của luật Việt Nam / dữ liệu mẫu Việt Nam)",
    "core_imports": "tests/test_pack_surface.py — {'<module>|<barista.internal>': 'lý do | người gỡ | kế hoạch'}: ngoại lệ tạm của gói nội bộ nhập phần lõi chưa có trên barista.api (gói đối tác pack.sh check vẫn bị chặn)",
    "public_routes": "tests/test_meta.py — {'<METHOD> <path>': lý do}: route công khai (không cần đăng nhập) do gói ngoài khai qua Module.route(public=…)",
}


@dataclass(frozen=True)
class Outside:
    """Một gói ngoài repo lõi đang cài: khoá gói, thư mục package của nó, {module: file} và các file .py không thuộc module nào (dụng cụ nạp mẫu…)."""
    key: str
    root: Path
    modules: dict[str, list[Path]]
    other: list[Path]


def _dev_tool(rel: Path) -> bool:
    """Dụng cụ nạp mẫu của gói (``sample.py`` / ``sample/`` / ``seed_*.py``): công cụ dev như ``seed*.py`` của lõi — không phải mã của module nào."""
    return rel.parts[0] in ("sample", "sample.py") or rel.parts[0].startswith("seed_")


@cache
def outside() -> tuple[Outside, ...]:
    """Mọi gói ngoài repo đang cài. Module ↔ file: ``<package>/<khoá module>.py`` hoặc ``<package>/<khoá module>/``; gói MỘT module có thể để mã ngay
    ở gốc package (mọi file trừ dụng cụ nạp mẫu). Gói nhiều module mà có module không tìm ra file ⇒ dừng (không quét sót lặng lẽ)."""
    import barista.modules  # noqa: F401 — nạp gói đã cài
    from barista.kernel import loader, registry

    out = []
    for e in loader.LOADED:
        if e.dist == "barista":
            continue
        root = e.migrations.parent.parent
        every = [f for f in sorted(root.rglob("*.py")) if "__pycache__" not in f.parts]
        mods: dict[str, list[Path]] = {}
        unplaced = []
        for k in sorted(m for m, pk in registry.MODULE_PACK.items() if pk == e.key):
            if (root / f"{k}.py").is_file():
                mods[k] = [root / f"{k}.py"]
            elif (root / k / "__init__.py").is_file():
                mods[k] = sorted((root / k).rglob("*.py"), key=lambda f: (f.name != "__init__.py", str(f)))
            else:
                unplaced.append(k)
        taken = {f for fs in mods.values() for f in fs}
        if len(unplaced) == 1:
            mods[unplaced[0]] = sorted((f for f in every if f not in taken and not _dev_tool(f.relative_to(root))),
                                       key=lambda f: (f.relative_to(root).as_posix() != "__init__.py", str(f)))
            taken |= set(mods[unplaced[0]])
        else:
            assert not unplaced, f"gói {e.key}: không tìm ra file của module {unplaced} trong {root} — đặt <module>.py hoặc <module>/ ở gốc package"
        out.append(Outside(e.key, root, mods, [f for f in every if f not in taken]))
    return tuple(out)


def declared(name: str) -> dict[str, str]:
    """Dòng cho phép do các gói ngoài đang cài khai cho danh sách ``name`` (``GUARD_LISTS``) trong ``core_guards.json`` ở gốc package: {khoá: lý do}.
    Khoá danh sách lạ / lý do trống ⇒ dừng. Hai gói khai cùng khoá ⇒ dừng."""
    assert name in GUARD_LISTS, name
    out: dict[str, str] = {}
    for o in outside():
        path = o.root / GUARDS_FILE
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        assert set(data) <= set(GUARD_LISTS), f"{path}: danh sách lạ {sorted(set(data) - set(GUARD_LISTS))} — chỉ nhận {sorted(GUARD_LISTS)}"
        for k, why in data.get(name, {}).items():
            assert isinstance(why, str) and why.strip(), f"{path}: {name}[{k!r}] thiếu lý do"
            assert k not in out, f"{path}: {name}[{k!r}] đã có gói khác khai"
            out[k] = why
    return out


def rel(path: Path) -> str:
    """Tên file để báo lỗi / so danh sách cho phép: tính từ ``src/barista`` (lõi) hoặc ``<package của gói>/…`` (gói ngoài)."""
    p = path.resolve()
    try:
        return p.relative_to(SRC.resolve()).as_posix()
    except ValueError:
        pass
    for o in outside():
        try:
            return f"{o.root.name}/{p.relative_to(o.root.resolve()).as_posix()}"
        except ValueError:
            continue
    return str(path)


def in_pack(path: Path) -> tuple[str, str] | None:
    """(khoá gói, đường dẫn tính từ package của gói) nếu file thuộc một gói ngoài đang cài; file của lõi ⇒ None."""
    p = path.resolve()
    for o in outside():
        try:
            return o.key, p.relative_to(o.root.resolve()).as_posix()
        except ValueError:
            continue
    return None


def py_files() -> list[Path]:
    """Mọi file ``.py`` của repo lõi + của các gói ngoài đang cài."""
    return [f for f in sorted(SRC.rglob("*.py"))] + [f for o in outside() for f in sorted({*o.other, *(x for fs in o.modules.values() for x in fs)})]


def sql_files() -> list[Path]:
    """Mọi migration ``.sql`` của repo lõi + của các gói ngoài đang cài."""
    return sorted((SRC / "migrations").rglob("*.sql")) + [f for o in outside() for f in sorted((o.root / "migrations").rglob("*.sql"))]


def module_files(root: Path | None = None) -> dict[str, list[Path]]:
    """{khoá module: các file .py của nó} — package: mọi file trong thư mục (kể cả thư mục con), ``__init__.py`` đứng đầu. Không nêu ``root``: module
    của lõi + module của các gói ngoài đang cài."""
    if root is not None:
        return _module_files(root)
    out = _module_files(SRC / "modules")
    for o in outside():
        for k, files in o.modules.items():
            assert k not in out, f"module {k}: vừa ở lõi vừa ở gói {o.key}"
            out[k] = files
    return out


def _module_files(root: Path) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    for p in sorted(root.iterdir()):
        if p.suffix == ".py" and p.stem != "__init__":
            key, files = p.stem, [p]
        elif p.is_dir() and (p / "__init__.py").exists():
            key, files = p.name, sorted(p.rglob("*.py"), key=lambda f: (f.name != "__init__.py", str(f)))
        else:
            continue
        assert key not in out, f"module {key}: vừa có {key}.py vừa có package {key}/ — chọn một dạng"
        out[key] = files
    return out


def module_of(path: Path, root: Path | None = None) -> str | None:
    """File này thuộc module nào (``modules/flow.py`` và ``modules/flow/plans.py`` đều là ``flow``; file của gói ngoài đang cài: module của gói chứa
    nó); ngoài mã module ⇒ None."""
    try:
        rel_ = path.resolve().relative_to((root or SRC / "modules").resolve())
    except ValueError:
        if root is None:
            p = path.resolve()
            return next((k for o in outside() for k, files in o.modules.items() if any(p == f.resolve() for f in files)), None)
        return None
    return rel_.parts[0].removesuffix(".py") if rel_.parts and rel_.parts[0] != "__init__.py" else None


def outside_packs() -> set[str]:
    """Khoá gói của bản phân phối NGOÀI repo lõi đang cài trong môi trường này (``barista.packs`` có ``dist`` khác ``barista``; ADR 0155). Bộ test lõi
    chạy được cả khi không cài gói nào lẫn khi cài (``BARISTA_PACKS`` trống ⇒ nạp mọi gói) — test MÔ TẢ REPO LÕI (bản đồ module, lịch sử migration của
    lõi) dùng hai hàm này để chỉ tính phần lõi; test CẤU TRÚC thì quét cả gói ngoài (đầu file)."""
    from barista.kernel import loader

    return {e.key for e in loader.LOADED if e.dist != "barista"}


def core_modules() -> set[str]:
    """Module (kể cả ``core``) thuộc gói của repo lõi — loại module của gói ngoài đang cài."""
    from barista.kernel import registry

    out = outside_packs()
    return {m for m, pk in registry.MODULE_PACK.items() if pk not in out}
