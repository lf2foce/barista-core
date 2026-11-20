"""NẠP GÓI ĐÃ CÀI (0130 lát 2). Mỗi gói là một điểm vào (entry point) nhóm ``barista.packs`` trong bản phân phối Python cài gói:

    [project.entry-points."barista.packs"]
    <khoá gói> = "<module điểm vào>"

Import module điểm vào phải đăng ký TRỌN gói đó (``Pack(...)`` + mọi ``Module`` của gói; module thứ hai trở đi import ở cuối module đầu, như
package — 0088). Gói trong repo khai ở ``backend/pyproject.toml``; gói ngoài repo cài thêm bản phân phối của nó — kernel không biết tên gói nào.

THỨ TỰ NẠP (xác định, không theo thứ tự cài / thư mục): lõi (``core_ops``) trước; rồi gói theo tầng ``core`` → ``capability`` → ``industry``,
trong tầng theo phụ thuộc ``Pack(requires=...)`` (gói cần đứng trước), hoà thì theo khoá gói. Phải biết tầng + phụ thuộc TRƯỚC khi chạy mã
gói ⇒ đọc từ lời khai ``Pack("<khoá>", ..., kind="…", requires=(…))`` trong mã nguồn module điểm vào bằng ``ast`` (giá trị chữ, không biểu
thức); gói do lõi khai sẵn (gói nền) lấy từ sổ đăng ký. ``needs=`` của thao tác / bên nghe không phải cạnh nạp (gói năng lực ``needs`` gói
ngành là hợp lệ — chỉ quyết định lúc chạy).

Kiểm khi nạp: hai bản phân phối cùng khoá gói, gói cần một gói chưa cài, vòng phụ thuộc, điểm vào không khai đúng gói, module nạp ra thuộc gói
khác, module của gói không có điểm vào ⇒ DỪNG (lõi không chạy nửa vời). Gỡ một gói = gỡ bản phân phối của nó: lõi và gói còn lại vẫn chạy,
migration của gói đó không chạy nữa (``kernel/migrate.py``).
"""

import ast
import importlib
import importlib.metadata
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.machinery import PathFinder
from pathlib import Path

from barista.kernel import registry, regulations

GROUP = "barista.packs"
TIERS = ("core", "capability", "industry", "company")   # gói riêng công ty nạp SAU gói chung nó dựa vào


@dataclass(frozen=True)
class Entry:
    """Một gói đã cài: khoá, module điểm vào, bản phân phối, tầng, gói cần — biết TRƯỚC khi chạy mã gói."""
    key: str
    module: str
    dist: str
    kind: str
    requires: tuple[str, ...]

    @property
    def migrations(self) -> Path:
        """Thư mục migration của gói: ``<package gốc của module điểm vào>/migrations/<khoá gói>/`` (gói trong repo:
        ``barista/migrations/<gói>/``; gói ngoài: ``<package của nó>/migrations/<gói>/``)."""
        spec = PathFinder.find_spec(self.module.split(".")[0])
        if spec is None or not spec.submodule_search_locations:
            raise RuntimeError(f"gói {self.key}: không tìm thấy package của {self.module}")
        return Path(next(iter(spec.submodule_search_locations))) / "migrations" / self.key


LOADED: list[Entry] = []   # gói đã nạp, theo thứ tự nạp (migration chạy theo đúng thứ tự này)
_state = {"phase": "idle"}


def _declared(module: str, key: str) -> tuple[str, tuple[str, ...]]:
    """(kind, requires) từ lời khai ``Pack("<key>", …)`` ở cấp module của file điểm vào — đọc mã nguồn, KHÔNG chạy (kể cả package cha)."""
    origin = _origin(module)
    if origin is None or origin.suffix != ".py":
        raise RuntimeError(f"gói {key}: không thấy mã nguồn của điểm vào {module}")
    for node in ast.parse(origin.read_text()).body:
        call = node.value if isinstance(node, ast.Expr | ast.Assign) else None
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "Pack" and call.args
                and isinstance(call.args[0], ast.Constant) and call.args[0].value == key):
            continue
        kw = {k.arg: k.value for k in call.keywords}
        try:
            kind = ast.literal_eval(kw["kind"])
            requires = tuple(ast.literal_eval(kw["requires"])) if "requires" in kw else ()
        except (KeyError, ValueError) as e:
            raise RuntimeError(f"gói {key} ({module}): Pack(...) phải khai kind= và requires= bằng giá trị chữ") from e
        return kind, requires
    raise RuntimeError(f"gói {key}: module điểm vào {module} không khai Pack({key!r}, …) ở cấp module")


def _origin(module: str) -> Path | None:
    """File nguồn của một module, tìm theo từng tầng package bằng ``PathFinder`` — KHÔNG import package cha (``importlib.util.find_spec``
    của ``a.b`` chạy ``a/__init__.py`` trước): mã của gói chưa chạy dòng nào trước khi thứ tự nạp được kiểm."""
    parts = module.split(".")
    spec = PathFinder.find_spec(parts[0])
    for i in range(2, len(parts) + 1):
        if spec is None or not spec.submodule_search_locations:
            return None
        spec = PathFinder.find_spec(".".join(parts[:i]), list(spec.submodule_search_locations))
    return Path(spec.origin) if spec is not None and spec.origin else None


def discover() -> list[Entry]:
    """Mọi gói đã cài (chưa nạp mã). Gói đã có trong sổ đăng ký (gói nền — lõi khai) lấy tầng / phụ thuộc từ đó."""
    found: dict[str, Entry] = {}
    for ep in importlib.metadata.entry_points(group=GROUP):
        dist = ep.dist.name if ep.dist else "?"
        if ep.name in found:
            raise RuntimeError(f"gói {ep.name} được cài hai lần ({found[ep.name].dist}, {dist})")
        if ep.attr:
            raise RuntimeError(f"gói {ep.name}: điểm vào phải là một module, không phải {ep.value}")
        pk = registry.PACKS.get(ep.name)
        kind, requires = (pk.kind, pk.requires) if pk else _declared(ep.module, ep.name)
        if kind not in TIERS:
            raise RuntimeError(f"gói {ep.name}: tầng {kind} lạ")
        found[ep.name] = Entry(ep.name, ep.module, dist, kind, requires)
    return list(found.values())


def order(entries: Iterable[Entry]) -> list[Entry]:
    """Tầng → phụ thuộc → khoá. Thiếu gói cần / vòng phụ thuộc ⇒ lỗi (kèm tên)."""
    todo = {e.key: e for e in entries}
    if missing := sorted(f"{e.key} cần {r}" for e in todo.values() for r in e.requires if r not in todo):
        raise RuntimeError("gói cần gói chưa cài: " + "; ".join(missing))
    out: list[Entry] = []
    done: set[str] = set()
    while todo:
        ready = [e for e in todo.values() if set(e.requires) <= done]
        if not ready:
            raise RuntimeError("vòng phụ thuộc giữa các gói: " + ", ".join(sorted(todo)))
        nxt = min(ready, key=lambda e: (TIERS.index(e.kind), e.key))
        out.append(nxt)
        done.add(nxt.key)
        del todo[nxt.key]
    return out


def _check_declared(e: Entry) -> None:
    """Sau khi import điểm vào: gói phải được khai, và tầng / gói cần khai thật phải đúng bản đọc trước (thứ tự nạp đã tính theo bản đó)."""
    if (pk := registry.PACKS.get(e.key)) is None:
        raise RuntimeError(f"gói {e.key}: import {e.module} không khai Pack({e.key!r})")
    if (pk.kind, tuple(pk.requires)) != (e.kind, e.requires):
        raise RuntimeError(f"gói {e.key}: Pack khai kind={pk.kind!r} requires={tuple(pk.requires)!r} khác bản đọc trước khi nạp "
                           f"(kind={e.kind!r} requires={e.requires!r}) — kind= / requires= phải là giá trị chữ, không đổi lúc chạy")


def load() -> None:
    """Nạp lõi + mọi gói đã cài theo ``order`` rồi ``registry.validate()``. Gọi nhiều lần / lồng nhau (import module gói trong lúc nạp) an toàn."""
    if _state["phase"] != "idle":
        return
    _state["phase"] = "loading"
    try:
        from barista.kernel import core_ops  # noqa: F401 — lõi: module ``core`` + gói nền

        entries = discover()
        if not entries:
            raise RuntimeError(f"Không thấy gói nào đã cài (entry point {GROUP}) — chạy `uv sync` trong backend/")
        for e in order(entries):
            before = set(registry.MODULES)
            importlib.import_module(e.module)
            _check_declared(e)
            if stray := sorted(m for m in set(registry.MODULES) - before if registry.MODULE_PACK[m] != e.key):
                raise RuntimeError(f"gói {e.key}: điểm vào {e.module} nạp module của gói khác: {', '.join(stray)}")
            LOADED.append(e)
        keys = {e.key for e in LOADED}
        if orphan := sorted(m for m, p in registry.MODULE_PACK.items() if p not in keys):
            raise RuntimeError(f"module không thuộc gói nào đã cài: {', '.join(orphan)}")
        registry.validate()
        regulations.check_contracts()   # bộ quy định phát hành khớp hợp đồng mã của module đã nạp — mã lạ / thiếu mã bắt buộc ⇒ DỪNG, không im lặng bỏ qua (0154)
        _state["phase"] = "done"
    except BaseException:
        _state["phase"] = "idle"
        LOADED.clear()
        raise
