#!/usr/bin/env python3
"""Bộ công cụ gói cho đối tác / bên ngoài (0130 lát 4, ADR 0242): sinh khuôn gói và tự kiểm gói ở repo RIÊNG của họ. Chỉ thư viện chuẩn (Python ≥ 3.11).

  bash scripts/pack.sh new <khoá> <thư mục> [--title "Tên gói"]      # sinh gói mới đúng hợp đồng (chạy được ngay, có test)
  bash scripts/pack.sh check <thư mục gói> [--core-ref HEAD] [--worktree] [--with <thư mục gói khác>] [--keep]

``check`` không sửa gì của người gọi: dựng bản lõi SẠCH (``git archive`` của ``--core-ref`` — mặc định HEAD của repo lõi này), chép gói vào thư mục tạm
(mặc định chỉ file đã commit nếu gói nằm trong repo git; ``--worktree`` lấy cả thay đổi chưa commit), rồi lần lượt:

  1. đọc tĩnh ``pyproject.toml`` + mã (không import): tên bản phân phối, phiên bản, đúng MỘT điểm vào ``barista.packs``, ``requires-core`` chứa
     ``barista.api`` của lõi, quyền khai (``permissions``) khớp quyền mã dùng và mã chạy chỉ dùng thư viện cho phép (ADR 0247), tên ``barista.api``
     sắp bỏ (chú ý), ``Pack("<khoá>", tên, mô tả, kind=…)`` bằng chữ, migration nằm ở ``migrations/<khoá>/`` và mở đầu ``-- module:``, có test;
  2. ``uv sync --frozen`` môi trường lõi, cài gói editable, nạp bằng loader của lõi + import mọi module con của gói (tên ngoài ``barista.api`` ⇒ lỗi);
  3. ruff, pyright, pytest của gói (từ ``backend/`` của lõi, ``-p tests.conftest``);
  4. test CẤU TRÚC của lõi (``backend/tests/pack_guards.txt``) chạy lại với gói đã cài — gói chịu cùng luật ranh giới như mã lõi.

Cần Postgres local như bộ test của lõi (``BARISTA_TEST_ADMIN_URL``); DB test mang mã lần chạy, xoá khi xong. Thoát 0 = ĐẠT, 1 = KHÔNG ĐẠT, 2 = không chạy được.
``barista-packs/scripts/check_packs.sh`` (repo gói chính thức) làm cùng các bước cho mọi gói của repo đó + kiểm ghim của bản cài.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE.parents[1]                      # repo lõi chứa script này
TEMPLATE = HERE / "template"
TEMPLATES = {"plain": TEMPLATE, "connector": HERE / "template-connector"}   # khuôn gói thường · gói kết nối (khung kết nối chung, ADR 0244)
sys.path.insert(0, str(CORE / "deploy"))
import packs as release_packs  # noqa: E402  # pyright: ignore[reportMissingImports] — deploy/packs.py: satisfies / _scan / api_version dùng chung với máy phát hành

KEY_RE = re.compile(r"[a-z][a-z0-9_]{1,30}\Z")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+\Z")
# Khoá không được dùng: schema / module của lõi đọc lúc chạy ``new`` (``_core_keys``) + tên Postgres hay dùng.
RESERVED = {"public", "pg_catalog", "information_schema", "core", "tests", "barista", "base", "sample"}


# ───────────────────────────────────────────────────────────── quyền gói khai (ADR 0247)

# Quyền = phần NHẠY CẢM của ``barista.api`` mà mã chạy của gói (``src/``) dùng: lộ dữ liệu ra ngoài, tốn tiền, gửi cho người, chạy trong công ty khác.
# Gói khai ``[tool.barista] permissions = [...]``; ``pack.sh check`` đọc mã (không import) suy ra quyền đang dùng: dùng mà không khai ⇒ LỖI, khai mà
# không dùng ⇒ CHÚ Ý. Phần còn lại của ``barista.api`` (màn, lệnh, quy định, pháp nhân…) không cần khai. Họ thao tác hệ thống (``m.consumes``) đã
# khai trong mã — chỉ in ra cho người review, không khai lại. Đây là kiểm TĨNH (bắt dùng vô ý, chỉ chỗ cho người review soi), không phải hộp cát:
# gói vẫn chạy chung tiến trình với lõi (runbook 21 "Vì sao có bước review mã"); lớp cách ly sau này chặn theo đúng danh sách này.
PERMISSIONS: dict[str, tuple[str, frozenset[str]]] = {
    "ai": ("gọi model / đọc tài liệu bằng AI — tốn hạn mức AI của công ty, gửi dữ liệu cho nhà cung cấp model", frozenset({"ai", "docread"})),
    "network": ("nói chuyện với hệ ngoài qua khung kết nối — gửi dữ liệu ra ngoài, giữ khoá của hệ ngoài", frozenset({"connectors", "inbound"})),
    "files": ("lưu / đọc / đổi tệp ở sổ tệp của lõi", frozenset({"files", "convert"})),
    "notify": ("gửi thông báo tới người dùng", frozenset({"notify"})),
    "public": ("mở dữ liệu ra ngoài: link công khai, cửa HTTP riêng của module (Module.route)", frozenset({"links", "web"})),
    "client_companies": ("chạy lệnh / truy vấn TRONG công ty khách do công ty dịch vụ tạo", frozenset({"tenancy"})),
}
API_TO_PERMISSION = {sub: perm for perm, (_, subs) in PERMISSIONS.items() for sub in subs}
# Thư viện mã chạy của gói (``src/``) được import: thư viện chuẩn cho chữ, số, ngày, cấu trúc dữ liệu + vài phần thư viện lõi gói cần thật (khuôn
# ``Input`` của pydantic, ``Jsonb`` / mã lỗi của psycopg cho tham số SQL). Mục ``a.b`` cho phép ``a.b`` và con của nó, không cho ``a`` (``urllib.parse``
# được, ``urllib.request`` không). Ngoài danh sách (os, sys, subprocess, socket, ssl, urllib.request, http, ctypes, importlib, threading, pickle,
# shutil, httpx, psycopg.connect…) ⇒ LỖI, không quyền nào mở: mạng đi qua khung kết nối, tệp qua sổ tệp, DB qua executor. Cần thêm một thư viện vô hại
# ⇒ thêm vào đây có lý do. Gói khác (``barista_pack_*``) theo luật của ``tests/test_pack_surface.py``. Test của gói (``tests/``) không bị giới hạn.
IMPORTS_OK = frozenset({
    "__future__", "abc", "asyncio", "base64", "bisect", "calendar", "collections", "contextlib", "copy", "csv", "dataclasses", "datetime", "decimal",
    "difflib", "enum", "fractions", "functools", "hashlib", "heapq", "hmac", "html", "io", "itertools", "json", "logging", "math", "numbers", "operator",
    "pathlib", "random", "re", "secrets", "statistics", "string", "struct", "textwrap", "time", "tomllib", "types", "typing", "unicodedata",
    "urllib.parse", "uuid", "warnings", "zlib", "zoneinfo",
    "pydantic", "pydantic_core", "psycopg.types.json", "psycopg.errors",
})
BUILTINS_NO = frozenset({"eval", "exec", "compile", "__import__", "breakpoint"})
ATTRS_NO = frozenset({"__builtins__", "__globals__", "__subclasses__", "__code__", "__closure__",   # lách vào nội bộ trình thông dịch
                      "create_subprocess_exec", "create_subprocess_shell", "open_connection", "open_unix_connection"})   # asyncio mở tiến trình / socket


def _import_ok(name: str, pkg: str) -> bool:
    top = name.split(".")[0]
    return top in (pkg, "barista") or top.startswith("barista_pack_") or any(name == ok or name.startswith(ok + ".") for ok in IMPORTS_OK)


def code_permissions(src: Path, pkg: str) -> tuple[dict[str, set[str]], list[str], list[str]]:
    """Đọc mã chạy (``src/``): ``({quyền: {chỗ dùng}}, [vượt luật], [họ thao tác m.consumes])``. Chỗ dùng = ``file:dòng api.<phần>``."""
    used: dict[str, set[str]] = {}
    bad: list[str] = []
    families: list[str] = []

    def use(sub: str, where: str) -> None:
        if (perm := API_TO_PERMISSION.get(sub)) is not None:
            used.setdefault(perm, set()).add(f"{where} api.{sub}")

    for f in sorted(src.rglob("*.py")):
        rel = f.relative_to(src.parent).as_posix()
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError as e:
            bad.append(f"{rel}: không đọc được ({e.msg}, dòng {e.lineno})")
            continue
        modules: set[str] = set()            # tên Module(...) trong file — ``m.route(`` là cửa HTTP riêng
        api_alias: set[str] = set()          # ``import barista.api as api`` ⇒ ``api.ai`` là dùng phần ``ai``
        for node in ast.walk(tree):
            where = f"{rel}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == "barista.api" or a.name.startswith("barista.api."):
                        parts = a.name.split(".")
                        if len(parts) > 2:
                            use(parts[2], where)
                        if a.asname:
                            api_alias.add(a.asname)
                    elif not _import_ok(a.name, pkg):
                        bad.append(f"{where}: import {a.name} — mã chạy của gói chỉ dùng barista.api + thư viện chuẩn cho phép (runbook 21)")
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module == "barista.api":
                    for a in node.names:
                        use(a.name, where)
                elif node.module.startswith("barista.api."):
                    use(node.module.split(".")[2], where)
                elif not _import_ok(node.module, pkg) and not all(_import_ok(f"{node.module}.{a.name}", pkg) for a in node.names):
                    bad.append(f"{where}: from {node.module} import … — mã chạy của gói chỉ dùng barista.api + thư viện chuẩn cho phép (runbook 21)")
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and getattr(node.value.func, "id", "") == "Module":
                modules.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BUILTINS_NO:
                bad.append(f"{where}: {node.func.id}(…) — không chạy mã động trong gói")
            elif isinstance(node, ast.Attribute) and node.attr in ATTRS_NO:
                bad.append(f"{where}: .{node.attr} — gói không lách vào nội bộ trình thông dịch, không mở tiến trình / socket")
        for node in ast.walk(tree):   # vòng hai: cần đủ tên Module / bí danh của cả file
            where = f"{rel}:{getattr(node, 'lineno', 0)}"
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) and node.value.attr == "api"
                    and isinstance(node.value.value, ast.Name) and node.value.value.id == "barista"):   # ``import barista.api`` rồi ``barista.api.ai``
                use(node.attr, where)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id in api_alias:
                    use(node.attr, where)
                if node.value.id in modules and node.attr == "route":
                    used.setdefault("public", set()).add(f"{where} Module.route")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "consumes"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id in modules):
                families += [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    return used, bad, sorted(set(families))


def permission_check(pack: Path, pkg: str, meta: dict, r: Report) -> None:
    """So quyền khai ở ``[tool.barista] permissions`` với quyền mã dùng (``code_permissions``)."""
    raw = meta.get("tool", {}).get("barista", {}).get("permissions", [])
    if not isinstance(raw, list) or not all(isinstance(p, str) for p in raw):
        r.fail('[tool.barista] permissions phải là danh sách chuỗi, vd permissions = ["network"] (để [] nếu không cần quyền nào)')
        return
    declared = set(raw)
    for p in sorted(declared - set(PERMISSIONS)):
        r.fail(f"quyền '{p}' không có — chọn trong: {', '.join(PERMISSIONS)}")
    used, bad, families = code_permissions(pack / "src" / pkg, pkg)
    for b in bad:
        r.fail(b)
    for perm in sorted(set(used) - declared):
        r.fail(f"mã dùng quyền '{perm}' ({PERMISSIONS[perm][0]}) mà pyproject chưa khai: {', '.join(sorted(used[perm])[:3])} — "
               f'thêm "{perm}" vào [tool.barista] permissions')
    for perm in sorted((declared & set(PERMISSIONS)) - set(used)):
        r.warn(f"khai quyền '{perm}' mà mã không dùng — bỏ khỏi permissions cho người review khỏi soi thừa")
    shown = ", ".join(sorted(declared & set(PERMISSIONS))) or "không"
    r.ok(f"quyền khai: {shown}; họ thao tác hệ thống (m.consumes): {', '.join(families) or 'không'}")


def deprecated_names(src: Path, surface: dict) -> list[str]:
    """Tên của ``barista.api`` gói đang dùng mà lõi đã đánh dấu sắp bỏ (``api_surface.json`` → ``deprecated``) — kèm thứ thay và ngày sớm nhất bị bỏ."""
    gone = surface.get("deprecated", {})
    out: list[str] = []
    for f in sorted(src.rglob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.ImportFrom) and node.module == "barista.api" else []
            names += [node.attr] if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "api" else []
            for n in names:
                if n in gone:
                    d = gone[n]
                    out.append(f"{f.name}:{getattr(node, 'lineno', 0)}: barista.api.{n} sắp bỏ (từ {d['since']}) — dùng {d['use']}; bỏ sớm nhất {d['remove_after']}, ở bản {d['remove_in']}")
    return out


# ───────────────────────────────────────────────────────────── new

def _core_keys() -> set[str]:
    """Khoá gói + module + schema của lõi (đọc mã lõi bằng ``ast``, không import) — gói mới không được trùng."""
    src = CORE / "backend" / "src" / "barista"
    keys: set[str] = set()
    for f in [*src.glob("modules/*.py"), *src.glob("modules/*/__init__.py")]:
        keys.add(f.parent.name if f.name == "__init__.py" else f.stem)
        for m in re.finditer(r'^(?:\w+ = )?(?:Pack|Module)\("([a-z0-9_]+)"', f.read_text(encoding="utf-8"), re.M):
            keys.add(m.group(1))
    for d in (src / "migrations").iterdir() if (src / "migrations").is_dir() else ():
        if d.is_dir():
            keys.add(d.name)
    return {k for k in keys if not k.startswith("_")}


def _render(text: str, vals: dict[str, str]) -> str:
    for k, v in vals.items():
        text = text.replace(f"@@{k}@@", v)
    left = re.findall(r"@@[A-Z_]+@@", text)
    if left:
        raise SystemExit(f"khuôn còn chỗ chưa thay: {sorted(set(left))}")
    return text


def new(key: str, dest: Path, title: str, kind: str = "plain") -> Path:
    if not KEY_RE.fullmatch(key):
        raise SystemExit(f"Khoá gói '{key}' không hợp lệ: chữ thường, số, gạch dưới; bắt đầu bằng chữ; 2–31 ký tự (khoá = tên module = tên schema)")
    if key in RESERVED or key in _core_keys():
        raise SystemExit(f"Khoá '{key}' trùng khoá / schema của lõi — chọn khoá theo NGHIỆP VỤ của gói (vd 'quan_ly_kho_lanh')")
    dest = dest.resolve()
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"{dest} đã có nội dung — chọn thư mục mới (khuôn không ghi đè)")
    api = release_packs.api_version((CORE / "backend/src/barista/api/__init__.py").read_text(encoding="utf-8"))
    major, minor = api.split(".")[:2]
    pkg = f"barista_pack_{key}"
    vals = {"KEY": key, "TITLE": title, "PKG": pkg, "DIST": f"barista-pack-{key.replace('_', '-')}", "API": api,
            "API_MINOR": f"{major}.{minor}", "CORE_BACKEND": os.path.relpath(CORE / "backend", dest)}
    tmpl = TEMPLATES[kind]
    for f in sorted(tmpl.rglob("*.tmpl")):
        rel = f.relative_to(tmpl).as_posix()[: -len(".tmpl")]
        rel = rel.replace("src/PKG", f"src/{pkg}").replace("migrations/KEY", f"migrations/{key}").replace("KEY", key)
        rel = ".gitignore" if rel == "gitignore" else rel
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_render(f.read_text(encoding="utf-8"), vals), encoding="utf-8")
    print(f"Đã tạo gói '{key}' ({vals['DIST']}) ở {dest}")
    print(f"  cài vào môi trường lõi: uv pip install --python {CORE / 'backend/.venv/bin/python'} --no-deps -e {dest}")
    print(f"  tự kiểm:               bash {CORE / 'scripts/pack.sh'} check {dest}")
    return dest


# ───────────────────────────────────────────────────────────── check

class Report:
    def __init__(self) -> None:
        self.fails: list[str] = []
        self.warns: list[str] = []

    def ok(self, msg: str) -> None:
        print(f"  ĐẠT   {msg}")

    def warn(self, msg: str) -> None:
        print(f"  CHÚ Ý {msg}")
        self.warns.append(msg)

    def fail(self, msg: str, detail: str = "") -> None:
        print(f"  LỖI   {msg}")
        if detail:
            print("\n".join("        " + line for line in detail.rstrip().splitlines()[-30:]))
        self.fails.append(msg)


def step(title: str) -> None:
    print(f"\n== {title} ==")


def die(msg: str) -> None:
    print(f"DỪNG: {msg}", file=sys.stderr)
    raise SystemExit(2)


def run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr)


def static_check(pack: Path, api: str, r: Report) -> dict[str, str] | None:
    """Bước 1 — chỉ đọc file. Trả {key, pkg, dist, version} khi đủ để đi tiếp."""
    try:
        meta = tomllib.loads((pack / "pyproject.toml").read_text(encoding="utf-8"))
    except FileNotFoundError:
        r.fail(f"không có {pack}/pyproject.toml — đưa đường dẫn tới THƯ MỤC GÓI (chỗ có pyproject.toml)")
        return None
    except tomllib.TOMLDecodeError as e:
        r.fail(f"pyproject.toml đọc không được: {e}")
        return None
    project = meta.get("project", {})
    dist, version = str(project.get("name", "")), str(project.get("version", ""))
    if not dist.startswith("barista-pack-"):
        r.fail(f"tên bản phân phối '{dist}' phải bắt đầu bằng 'barista-pack-'")
    if not VERSION_RE.fullmatch(version):
        r.fail(f"version '{version}' phải dạng X.Y.Z (tag của gói: <khoá>/vX.Y.Z)")
    if not str(project.get("description", "")).strip():
        r.fail("pyproject thiếu description (chợ gói hiện câu này cho khách)")
    if not any(re.match(r"barista\b", str(d)) for d in project.get("dependencies", [])):
        r.fail('dependencies phải có "barista" (lõi)')
    eps = project.get("entry-points", {}).get("barista.packs", {})
    if len(eps) != 1:
        r.fail(f'[project.entry-points."barista.packs"] phải có đúng MỘT dòng <khoá> = "<package>" (đang có {len(eps)})')
        return None
    key, target = next(iter(eps.items()))
    pkg = target.split(":")[0].split(".")[0]
    if not KEY_RE.fullmatch(key) or key in RESERVED:
        r.fail(f"khoá gói '{key}' không hợp lệ hoặc trùng tên dành riêng")
    src = pack / "src" / pkg
    if not src.is_dir():
        r.fail(f"không có package src/{pkg}/ mà điểm vào '{target}' trỏ tới")
        return None
    spec = str(meta.get("tool", {}).get("barista", {}).get("requires-core", ""))
    try:
        if not spec:
            r.fail("thiếu [tool.barista] requires-core (vd \">=1.63,<2\")")
        elif release_packs.satisfies(api, spec):
            r.ok(f"requires-core '{spec}' chứa barista.api {api} của lõi")
        else:
            r.fail(f"requires-core '{spec}' KHÔNG chứa barista.api {api} của lõi — nâng gói hoặc kiểm với lõi khác (--core-ref)")
    except ValueError as e:
        r.fail(f"requires-core '{spec}': {e}")
    permission_check(pack, pkg, meta, r)
    found: dict = {}
    for f in sorted(src.rglob("*.py")):
        release_packs._scan(f.read_text(encoding="utf-8", errors="replace"), key, found)
    if not found.get("title"):
        r.fail(f'không thấy Pack("{key}", "<tên>", "<mô tả>", kind=…) với tên + mô tả là CHUỖI CHỮ trong src/{pkg}/')
    else:
        r.ok(f"Pack '{key}': {found['title']} ({found['kind']}" + (f", cần {', '.join(found['requires'])}" if found["requires"] else "") + ")")
        if found["kind"] not in ("industry", "capability", "company"):
            r.fail(f"kind='{found['kind']}' — gói ngoài lõi chỉ được 'industry', 'capability' hoặc 'company'")
    sqls = sorted((src / "migrations").rglob("*.sql")) if (src / "migrations").is_dir() else []
    for f in sqls:
        rel = f.relative_to(src / "migrations").as_posix()
        if not rel.startswith(f"{key}/"):
            r.fail(f"migration {rel} phải nằm trong migrations/{key}/ (thư mục trùng khoá gói)")
        first = f.read_text(encoding="utf-8").split("\n", 1)[0]
        if not re.fullmatch(r"-- module: [a-z][a-z0-9_]*\s*", first):
            r.fail(f"migration {rel}: dòng đầu phải là '-- module: <khoá module>'")
    if sqls:
        r.ok(f"{len(sqls)} migration trong migrations/{key}/")
    for f in sorted(src.rglob("*.py")):   # cửa webhook VÀO chỉ có ở khung kết nối của lõi (ADR 0244) — báo sớm, có chỉ đường, thay vì đỏ ở test cấu trúc
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if re.search(r"\bm\.inbound\(|\.inbound\(\s*[\"']", line) and not line.lstrip().startswith("#"):
                r.fail(f"{f.name}:{n}: gói tự mở cửa webhook (Module.inbound) — nhận webhook qua bộ nối của khung kết nối (runbook 22)")
    tests = sorted((pack / "tests").glob("test_*.py")) if (pack / "tests").is_dir() else []
    if not tests:
        r.fail("không có tests/test_*.py — gói phải có test kịch bản (quyền, công ty khác, đường thuận)")
    addopts = str(meta.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("addopts", ""))
    if "-p tests.conftest" not in addopts:
        r.fail('[tool.pytest.ini_options] addopts phải có "-p tests.conftest" (DB test + fixture của lõi)')
    # gói kết nối (0244): địa chỉ lấy từ CÀI ĐẶT kết nối (người nhập) mà gọi bằng connectors.client(…) ⇒ không qua kiểm địa chỉ nội bộ (client_for)
    for f in sorted((pack / "src").rglob("*.py")) if (pack / "src").is_dir() else ():
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"connectors\.client\(", line) and "settings" in line:
                r.warn(f"{f.name}:{n}: địa chỉ từ cài đặt kết nối gọi bằng connectors.client — dùng await connectors.client_for(...) + khai url_settings (runbook 22)")
    return {"key": key, "pkg": pkg, "dist": dist, "version": version}


IMPORT_CHECK = """
import importlib, importlib.metadata as md, pkgutil, sys, traceback
key = sys.argv[1]
try:
    from barista.kernel import loader
    import barista.modules  # noqa: F401 — loader.load()
except Exception:
    print("loader.load():\\n" + traceback.format_exc(limit=-3)); sys.exit(1)
eps = [e for e in md.entry_points(group="barista.packs") if e.name == key]
if not eps:
    print(f"điểm vào barista.packs '{key}' chưa đăng ký"); sys.exit(1)
if key not in {e.key for e in loader.LOADED}:
    print(f"loader không nạp gói {key}"); sys.exit(1)
top = importlib.import_module(eps[0].value.split(":")[0].split(".")[0])
mods = [top.__name__] + [m.name for m in pkgutil.walk_packages(getattr(top, "__path__", []), top.__name__ + ".")]
bad = 0
for m in mods:
    try:
        importlib.import_module(m)
    except Exception:
        bad += 1; print(f"import {m}:\\n{traceback.format_exc(limit=-3)}")
print(f"loader + {len(mods)} module" if not bad else f"{bad} lỗi"); sys.exit(1 if bad else 0)
"""


def _copy_pack(src: Path, dest: Path, worktree: bool, r: Report) -> None:
    """Chép gói vào chỗ tạm: file đã commit (git archive) nếu gói nằm trong repo git, không thì cả thư mục."""
    code, top = run(["git", "-C", str(src), "rev-parse", "--show-toplevel"])
    if worktree or code != 0:
        if code != 0:
            r.warn("gói không nằm trong repo git — kiểm cả thư mục (nên để gói trong repo git và gửi bằng tag)")
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "*.egg-info"))
        return
    top_dir = Path(top.strip())
    sub = src.resolve().relative_to(top_dir.resolve()).as_posix()
    dirty = run(["git", "-C", str(top_dir), "status", "--porcelain", "--", sub or "."])[1].strip()
    if dirty:
        r.warn("gói có thay đổi chưa commit — KHÔNG được kiểm (chỉ HEAD); thêm --worktree để kiểm cả chúng")
    data = subprocess.run(["git", "-C", str(top_dir), "archive", "--format=tar", "HEAD", sub or "."], check=True, capture_output=True).stdout
    tmp = dest.parent / "_archive"
    tmp.mkdir()
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        t.extractall(tmp, filter="data")
    shutil.move(str(tmp / sub) if sub else str(tmp), dest)
    shutil.rmtree(tmp, ignore_errors=True)


def _pyright_config(pack: Path, core_be: Path) -> None:
    """``pyrightconfig.json`` trong BẢN CHÉP (đè ``[tool.pyright]``): giữ cấu hình của gói, chỉ trỏ lại đường tới ``backend/`` của lõi sạch."""
    import json

    meta = tomllib.loads((pack / "pyproject.toml").read_text(encoding="utf-8"))
    cfg = dict(meta.get("tool", {}).get("pyright", {}))
    extra = [p for p in cfg.get("extraPaths", []) if not str(p).rstrip("/").endswith("backend")]
    cfg["extraPaths"] = [*extra, str(core_be)]
    cfg.setdefault("include", ["src", "tests"])
    cfg.setdefault("pythonVersion", "3.14")
    (pack / "pyrightconfig.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")


def check(pack: Path, core_ref: str, worktree: bool, extra: list[Path], keep: bool) -> int:
    pack = pack.resolve()
    if not (pack / "pyproject.toml").is_file():
        die(f"{pack} không phải thư mục gói (thiếu pyproject.toml)")
    for tool in ("uv", "git"):
        if shutil.which(tool) is None:
            die(f"thiếu {tool}")
    admin_url = os.environ.get("BARISTA_TEST_ADMIN_URL", "postgresql://localhost:5432/postgres")
    if shutil.which("psql") and run(["psql", admin_url, "-Atc", "select 1"])[0] != 0:
        die(f"không nối được Postgres local ({admin_url}) — test cần nó (BARISTA_TEST_ADMIN_URL)")
    r = Report()
    work = Path(tempfile.mkdtemp(prefix="pack-check.")).resolve()
    run_id = f"pc{os.getpid()}"
    env = {k: v for k, v in os.environ.items()
           if k not in ("BARISTA_TEST_DATABASE_URL", "BARISTA_TEST_OWNER_DATABASE_URL", "BARISTA_TEST_UPGRADE_DATABASE_URL", "VIRTUAL_ENV")}
    env["BARISTA_TEST_RUN_ID"] = run_id
    try:
        step(f"0. Lõi sạch ({core_ref} của {CORE})")
        sha = run(["git", "-C", str(CORE), "rev-parse", "--verify", "-q", f"{core_ref}^{{commit}}"])[1].strip()
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            die(f"lõi không có ref {core_ref}")
        (work / "barista").mkdir()
        data = subprocess.run(["git", "-C", str(CORE), "archive", "--format=tar", sha], check=True, capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            t.extractall(work / "barista", filter="data")
        core_be = work / "barista" / "backend"
        api = release_packs.api_version((core_be / "src/barista/api/__init__.py").read_text(encoding="utf-8"))
        print(f"  lõi {sha[:12]}, barista.api {api}")
        if run(["git", "-C", str(CORE), "status", "--porcelain", "--", "backend"])[1].strip():
            r.warn("repo lõi có thay đổi chưa commit ở backend/ — kiểm với lõi ĐÃ COMMIT, không phải cây làm việc")

        step("1. Đọc tĩnh pyproject + mã gói")
        mine = work / "packs" / pack.name
        mine.parent.mkdir()
        _copy_pack(pack, mine, worktree, r)
        info = static_check(mine, api, r)
        if info is None:
            return _summary(r, "?")
        surface = json.loads((core_be / "tests/api_surface.json").read_text(encoding="utf-8"))
        for w in deprecated_names(mine / "src" / info["pkg"], surface):
            r.warn(w)

        step("2. Môi trường lõi + cài gói + nạp")
        code, out = run(["uv", "sync", "--frozen", "-q"], cwd=core_be, env=env)
        if code:
            die(f"uv sync --frozen của lõi lỗi:\n{out[-2000:]}")
        vpy = core_be / ".venv" / "bin" / "python"
        for other in extra:   # gói khác mà test của gói này cần (vd gói ngành nó cắm vào)
            dst = work / "packs" / other.resolve().name
            _copy_pack(other.resolve(), dst, True, r)
            code, out = run(["uv", "pip", "install", "-q", "--python", str(vpy), "--no-deps", "-e", str(dst)], env=env)
            if code:
                r.fail(f"không cài được gói kèm {other}", out)
        code, out = run(["uv", "pip", "install", "-q", "--python", str(vpy), "--no-deps", "-e", str(mine)], env=env)
        if code:
            r.fail("uv pip install -e gói lỗi", out)
            return _summary(r, info["key"])
        code, out = run([str(vpy), "-c", IMPORT_CHECK, info["key"]], cwd=core_be, env=env)
        if code == 0:
            r.ok(f"nạp bằng loader của lõi ({out.strip().splitlines()[-1]})")
        else:
            r.fail("nạp gói / import module lỗi (tên ngoài barista.api? khai Pack / Module sai?)", out)

        step("3. ruff, pyright, test của gói")
        code, out = run([str(vpy), "-m", "ruff", "check", "."], cwd=mine, env=env)
        r.ok("ruff") if code == 0 else r.fail("ruff", out)
        _pyright_config(mine, core_be)
        code, out = run([str(vpy), "-m", "pyright", "--pythonpath", str(vpy)], cwd=mine, env=env)
        r.ok(f"pyright ({out.strip().splitlines()[-1] if out.strip() else ''})") if code == 0 else r.fail("pyright", out)
        code, out = run([str(vpy), "-m", "pytest", "-q", "-p", "no:cacheprovider", str(mine / "tests")], cwd=core_be, env=env)
        last = out.strip().splitlines()[-1] if out.strip() else ""
        r.ok(f"pytest của gói ({last})") if code == 0 else r.fail(f"pytest của gói ({last})", out)

        step("4. Test cấu trúc của lõi trên mã gói")
        guards = [g for g in (core_be / "tests/pack_guards.txt").read_text(encoding="utf-8").splitlines() if g.strip() and not g.startswith("#")]
        code, out = run([str(vpy), "-m", "pytest", "-q", "-p", "no:cacheprovider", *guards], cwd=core_be, env=env)
        last = out.strip().splitlines()[-1] if out.strip() else ""
        r.ok(f"{len(guards)} file test cấu trúc ({last})") if code == 0 else r.fail(f"test cấu trúc của lõi ({last})", out)
        return _summary(r, info["key"])
    finally:
        if keep:
            print(f"Giữ lại {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
        if shutil.which("psql"):
            dbs = run(["psql", admin_url, "-Atc", f"SELECT datname FROM pg_database WHERE datname LIKE 'barista_erp_%_{run_id}%'"])[1].split()
            for d in dbs:
                run(["psql", admin_url, "-qc", f'DROP DATABASE IF EXISTS "{d}" WITH (FORCE)'])


def _summary(r: Report, key: str) -> int:
    print(f"\n== Tóm tắt gói {key} ==")
    for w in r.warns:
        print(f"  CHÚ Ý {w}")
    for f in r.fails:
        print(f"  LỖI   {f}")
    if r.fails:
        print(f"KẾT QUẢ: KHÔNG ĐẠT — {len(r.fails)} lỗi, {len(r.warns)} chú ý.")
        return 1
    print(f"KẾT QUẢ: ĐẠT — 0 lỗi, {len(r.warns)} chú ý. Gửi tag / commit này cho bên vận hành để đọc mã trước khi ghim.")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pack.sh", description="Khuôn gói + tự kiểm gói của đối tác (ADR 0242)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("new", help="sinh gói mới từ khuôn")
    a.add_argument("key")
    a.add_argument("dest", type=Path)
    a.add_argument("--title", default="")
    a.add_argument("--connector", action="store_true", help="gói KẾT NỐI ra hệ ngoài (khung kết nối chung của lõi, ADR 0244): bộ nối + lệnh nhận + form kết nối")
    b = sub.add_parser("check", help="tự kiểm một gói trên lõi sạch")
    b.add_argument("pack", type=Path)
    b.add_argument("--core-ref", default="HEAD")
    b.add_argument("--worktree", action="store_true", help="kiểm cả thay đổi chưa commit của gói")
    b.add_argument("--with", dest="extra", action="append", type=Path, default=[], help="gói khác cần cài kèm cho test (lặp được)")
    b.add_argument("--keep", action="store_true", help="giữ thư mục tạm để xem")
    args = ap.parse_args(argv)
    if args.cmd == "new":
        new(args.key, args.dest, args.title.strip() or args.key.replace("_", " ").capitalize(), "connector" if args.connector else "plain")
        return 0
    return check(args.pack, args.core_ref, args.worktree, args.extra, args.keep)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
