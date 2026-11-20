#!/usr/bin/env python3
"""GÓI NGOÀI LÕI CỦA MỘT BẢN PHÁT HÀNH (0155) — phần chạy trên MÁY PHÁT HÀNH (Mac, deploy/release.sh). Chỉ thư viện chuẩn (Python ≥ 3.11).

Bản cài khai gói nó dùng trong file bản cài: ``"packs": {"<khoá gói>": "v<X.Y.Z>"}`` hoặc, ghim cả commit của tag,
``{"<khoá gói>": {"version": "v<X.Y.Z>", "commit": "<sha 40 ký tự>"}}`` — GHIM commit BẮT BUỘC ở bản cài thật; chỉ bản cài khai ``"env": "demo"``
(dữ liệu giả, không ai trả tiền) mới được để dạng chuỗi ngắn (``barista-ops/instances/<tên>.json`` — nguồn DUY NHẤT của
file bản cài; lõi chia sẻ đối tác không mang file này; không khai ⇒ không gói nào). Mỗi gói là một thư mục ``<khoá>/`` của repo ``barista-packs`` (bản
phân phối Python riêng: pyproject + package + migration + test), phiên bản = TAG ``<khoá>/v<X.Y.Z>`` (repo nhiều gói, mỗi gói đánh số riêng).

Gói của ĐỐI TÁC (ADR 0242 phần 3): mục thêm ``"repo": "<thư mục>"`` ⇒ gói đó lấy từ repo ``<thư mục>`` cạnh repo lõi (``BARISTA_PACK_REPOS_DIR`` nếu
đặt) thay cho ``barista-packs`` — cùng bố cục, cùng mọi bước kiểm; LUÔN phải ghim commit (commit đã review), kể cả bản demo; không nhận URL (máy phát
hành không tự tải mã).

``stage`` lấy đúng tag bằng ``git archive`` (chỉ file đã commit ở tag — sửa dở trong thư mục làm việc không lọt) vào ``deploy/packs/<khoá>/``
của mã gửi lên, kèm ``deploy/packs/manifest.json`` (khoá ⇒ phiên bản, commit, tên bản phân phối). DỪNG khi: thiếu tag, tag trỏ commit khác commit
đã ghim (tag bị dời — tag xoá / tạo lại được, kể cả tag có chú thích; commit ghim mới là chốt), tag KHÔNG có chú thích, tag chưa có
(hoặc khác) trên kho chung ``origin`` (``published`` — máy phát hành khác phải lấy được đúng bản này), version trong pyproject
khác tag, gói không khai đúng điểm vào ``barista.packs``, ``[tool.barista] requires-core`` không khớp phiên bản bề mặt ``barista.api`` của lõi
đang phát hành. Máy chủ (deploy/ops/deploy.py) chỉ đối chiếu manifest với file bản cài CỦA NÓ rồi cài — không cần quyền đọc GitHub.

  python3 deploy/packs.py instance <tên>               # in đường dẫn file bản cài (barista-ops/instances)
  python3 deploy/packs.py bundle <file bản cài> <lõi.tar> <ra.tar.gz>  # tar của `git archive <sha>` + gói ⇒ ĐÚNG tar.gz sẽ gửi; in khoá gói
  python3 deploy/packs.py bundle <file bản cài> <lõi.tar> <ra.tar.gz> origin/main  # CHỈ KIỂM (scripts/premerge.sh --packs-main): gói ở ref, không ở tag
  python3 deploy/packs.py catalog                      # in danh mục gói (JSON) của MỌI gói trong repo gói — bundle ghi vào tar (xem dưới)

``bundle`` còn ghi ``backend/src/barista/pack_catalog.json`` vào tar: danh mục CHỮ (khoá, tên, mô tả, loại, phiên bản mới nhất, ``requires-core``,
tóm tắt, tên module / vai) của MỌI gói trong repo gói — kể cả gói bản cài này không cài — để chợ gói hiện "Chưa có trên bản cài" (0163). Đọc từ
tag mới nhất ``<khoá>/vX.Y.Z`` của từng gói (chưa có tag ⇒ HEAD), bằng ``tomllib`` + ``ast`` — KHÔNG import mã gói. Không thấy repo gói ⇒ không ghi file
(chợ gói như cũ); gói không đọc được ⇒ bỏ gói đó, báo ở stderr.

Vị trí repo ngang hàng: ``BARISTA_OPS_DIR`` / ``BARISTA_PACKS_DIR`` nếu đặt, không thì ``../barista-ops`` / ``../barista-packs`` cạnh repo
lõi (worktree: cạnh repo CHÍNH, theo ``git rev-parse --git-common-dir``).
"""

from __future__ import annotations

import ast
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

KEY_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
VERSION_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
ENVS = ("production", "demo")   # khoá "env" của file bản cài; vắng ⇒ production (nghiêm nhất). deploy/ops/deploy.py trên VPS đọc cùng hai giá trị.
NAME_RE = re.compile(r"[a-z][a-z0-9]{0,15}\Z")
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")   # tên THƯ MỤC repo gói của đối tác, nằm cạnh repo lõi (không phải URL — máy phát hành không tự tải mã)
STAGED = Path("deploy") / "packs"   # trong mã gửi lên (khớp deploy/ops/deploy.py + deploy/api.Dockerfile)
CORE = Path(__file__).resolve().parents[1]


def siblings_root(core: Path = CORE) -> Path:
    """Thư mục chứa repo lõi CHÍNH (worktree của agent nằm sâu trong .claude/worktrees ⇒ lấy theo thư mục .git chung)."""
    try:
        common = subprocess.check_output(["git", "-C", str(core), "rev-parse", "--path-format=absolute", "--git-common-dir"], text=True).strip()
        return Path(common).parent.parent
    except (OSError, subprocess.CalledProcessError):
        return core.parent


def ops_dir(core: Path = CORE) -> Path:
    return Path(os.environ.get("BARISTA_OPS_DIR") or siblings_root(core) / "barista-ops")


def packs_dir(core: Path = CORE) -> Path:
    return Path(os.environ.get("BARISTA_PACKS_DIR") or siblings_root(core) / "barista-packs")


def pack_repo(name: str, core: Path = CORE) -> Path:
    """Repo gói của ĐỐI TÁC (ADR 0242 phần 3): thư mục ``<name>`` cạnh repo lõi (hoặc trong ``BARISTA_PACK_REPOS_DIR``) — người phát hành clone về sau khi
    đã review mã của commit sẽ ghim. Cùng bố cục với repo gói chính thức: mỗi gói một thư mục ``<khoá>/``, tag ``<khoá>/v<X.Y.Z>``."""
    return Path(os.environ.get("BARISTA_PACK_REPOS_DIR") or siblings_root(core)) / name


def instance_file(name: str, ops: Path) -> Path:
    """``<ops>/instances/<tên>.json`` (repo ``barista-ops``); không có ⇒ dừng."""
    if not NAME_RE.fullmatch(name):
        raise SystemExit(f"Tên bản cài không hợp lệ: {name!r}")
    f = ops / "instances" / f"{name}.json"
    if not f.is_file():
        raise SystemExit(f"Không có file bản cài {name}.json ở {ops / 'instances'} — repo barista-ops cạnh repo lõi (hoặc BARISTA_OPS_DIR)")
    return f


def checked_instance_file(name: str, ops: Path) -> Path:
    """``instance_file`` + đúng phép kiểm file bản cài của VPS (``deploy/ops/deploy.py`` ``load_instance`` — MỘT nguồn luật, kể cả chế độ production
    ADR 0194: thiếu đăng nhập thật ⇒ ``release.sh`` dừng ở bước đầu, trước khi test / gửi)."""
    import importlib.util

    f = instance_file(name, ops)
    spec = importlib.util.spec_from_file_location("barista_deploy_ops", CORE / "deploy" / "ops" / "deploy.py")
    assert spec is not None and spec.loader is not None
    deploy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deploy)
    try:
        deploy.load_instance(name, f.parent)
    except (ValueError, KeyError) as error:
        raise SystemExit(f"File bản cài {f}: {error}") from None
    return f


API = "backend/src/barista/api/__init__.py"


def api_version(source: str) -> str:
    """``barista.api.__version__`` từ mã nguồn của ``barista/api/__init__.py`` (không import lõi; python của máy có thể cũ hơn lõi)."""
    m = re.search(r'^__version__ = "([0-9.]+)"[ \t]*(?:#.*)?$', source, re.M)
    if m is None:
        raise SystemExit("Không đọc được barista.api.__version__")
    return m.group(1)


def _ver(text: str) -> tuple[int, ...]:
    parts = text.strip().split(".")
    if not (1 <= len(parts) <= 3 and all(p.isdigit() for p in parts)):
        raise ValueError(f"phiên bản lạ: {text!r}")
    return tuple(int(p) for p in parts) + (0,) * (3 - len(parts))


def satisfies(version: str, spec: str) -> bool:
    """``spec`` = các điều kiện cách nhau dấu phẩy, mỗi điều kiện ``>=`` / ``>`` / ``<=`` / ``<`` / ``==`` / ``!=`` + số (vd ``">=1.3,<2"``)."""
    have = _ver(version)
    ops = {">=": have.__ge__, ">": have.__gt__, "<=": have.__le__, "<": have.__lt__, "==": have.__eq__, "!=": have.__ne__}
    conds = [c.strip() for c in spec.split(",") if c.strip()]
    if not conds:
        raise ValueError("requires-core trống")
    for c in conds:
        m = re.fullmatch(r"(>=|<=|==|!=|>|<)\s*([0-9.]+)", c)
        if m is None:
            raise ValueError(f"requires-core: điều kiện lạ {c!r}")
        if not ops[m.group(1)](_ver(m.group(2))):
            return False
    return True


def pinned(spec: object) -> tuple[str, str] | None:
    """Một mục ``"packs"``: ``"v1.2.3"`` ⇒ (phiên bản, ""); ``{"version": "v1.2.3", "commit": "<sha>"}`` ⇒ (phiên bản, sha) — commit tuỳ chọn.
    Thêm ``"repo": "<thư mục>"`` (``source_of``): gói lấy từ repo của đối tác thay cho repo gói chính thức. Sai dạng ⇒ None.
    (deploy/ops/deploy.py trên VPS đọc ``version`` / ``commit`` của cùng hai dạng — giữ hai bản khớp nhau; khoá ``repo`` chỉ máy phát hành dùng.)"""
    if isinstance(spec, str):
        version, commit = spec, ""
    elif isinstance(spec, dict) and set(spec) <= {"version", "commit", "repo"} and isinstance(spec.get("version"), str):
        version, commit = spec["version"], spec.get("commit", "")
        if "repo" in spec and not (isinstance(spec["repo"], str) and REPO_RE.fullmatch(spec["repo"])):
            return None
    else:
        return None
    if not isinstance(commit, str) or not VERSION_RE.fullmatch(version) or (commit and not COMMIT_RE.fullmatch(commit)):
        return None
    return version, commit


def source_of(spec: object) -> str:
    """Tên repo gói của đối tác mà mục ``"packs"`` nêu (``"repo"``), "" = repo gói chính thức."""
    return str(spec.get("repo") or "") if isinstance(spec, dict) else ""


def env_of(instance: dict) -> str:
    env = instance.get("env", "production")
    if env not in ENVS:
        raise SystemExit(f"instances/{instance.get('name')}.json: \"env\" = {env!r} — chỉ nhận {', '.join(ENVS)} (vắng = production)")
    return env


def declared(instance: dict) -> dict[str, tuple[str, str]]:
    """``"packs"`` của file bản cài ⇒ {khoá: (phiên bản, commit ghim hoặc "")}. Bản cài KHÔNG phải ``"env": "demo"`` mà có gói không ghim commit ⇒ dừng
    (tag xoá / tạo lại được — chỉ commit ghim mới chốt được mã gói đi vào bản cài thật)."""
    packs = instance.get("packs", {})
    if not isinstance(packs, dict):
        raise SystemExit(f"instances/{instance.get('name')}.json: \"packs\" phải là {{\"<khoá gói>\": \"v<X.Y.Z>\" | {{\"version\", \"commit\"}}}}")
    out: dict[str, tuple[str, str]] = {}
    for key, spec in packs.items():
        got = pinned(spec)
        if not isinstance(key, str) or not KEY_RE.fullmatch(key) or got is None:
            raise SystemExit(f"instances/{instance.get('name')}.json: gói {key!r} = {spec!r} — khoá chữ thường, phiên bản dạng v1.2.3, "
                             "commit (nếu ghim) là sha đủ 40 ký tự hex")
        out[key] = got
    # gói từ repo của đối tác: LUÔN ghim commit, kể cả bản demo — commit ghim là commit đã được review (0242); tag của repo ngoài không phải thứ để tin
    if outside := sorted(k for k, spec in packs.items() if source_of(spec) and not out[k][1]):
        raise SystemExit(f"instances/{instance.get('name')}.json: gói {', '.join(outside)} lấy từ repo của đối tác (\"repo\") phải ghim commit đã review "
                         "— {\"version\": \"v…\", \"commit\": \"<sha 40 ký tự>\", \"repo\": \"<thư mục>\"}")
    if loose := sorted(k for k, (_, pin) in out.items() if not pin):
        if env_of(instance) != "demo":
            raise SystemExit(f"instances/{instance.get('name')}.json: gói {', '.join(loose)} chưa ghim commit — bản cài thật phải khai "
                             "{\"version\": \"v…\", \"commit\": \"<sha 40 ký tự>\"} (git rev-parse <khoá>/v…^{commit}); chỉ \"env\": \"demo\" được bỏ qua")
    else:
        env_of(instance)
    return dict(sorted(out.items()))


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


REMOTE = "origin"   # kho chung của repo gói (GitHub) — mọi máy phát hành lấy tag từ đây


def published(repo: Path, key: str, tag: str) -> None:
    """Tag gói bản cài ghim phải (1) CÓ CHÚ THÍCH (``git tag -a`` — ai tạo, lúc nào, vì sao; DIEU-PHOI luật 5) và (2) đã có ĐÚNG như vậy trên
    ``origin``: tag chỉ có ở một máy thì máy / phiên khác không dựng lại được bản đang chạy; tag ở máy khác tag trên GitHub thì hai máy phát hành ra
    hai thứ (14/11/2026: 7 tag gói chỉ có ở một máy, một tag lệch dạng). Repo không có ``origin`` (repo tạm của test) ⇒ chỉ kiểm (1)."""
    ref = f"refs/tags/{tag}"
    if _git(repo, "cat-file", "-t", ref).decode().strip() != "tag":
        raise SystemExit(f"Gói {key}: tag {tag} không có chú thích — tạo lại `git tag -f -a {tag} {tag}^{{commit}} -m \"<vì sao>\"`, "
                         f"đẩy `git push --force {REMOTE} {ref}` rồi phát hành")
    if REMOTE not in _git(repo, "remote").decode().split():
        return
    local = _git(repo, "rev-parse", ref).decode().strip()
    try:
        got = _git(repo, "ls-remote", REMOTE, ref).decode().split()
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"Gói {key}: không hỏi được tag {tag} trên {REMOTE} ({e.stderr.decode().strip()[:200]}) — kiểm mạng / quyền GitHub "
                         f"rồi phát hành lại") from None
    if not got:
        raise SystemExit(f"Gói {key}: tag {tag} chưa có trên {REMOTE} — đẩy trước `git push {REMOTE} {ref}` rồi phát hành")
    if got[0] != local:
        raise SystemExit(f"Gói {key}: tag {tag} ở máy ({local[:12]}) khác trên {REMOTE} ({got[0][:12]}) — đồng bộ tag (tải lại hoặc đẩy đè có "
                         f"chủ đích) rồi phát hành")


CATALOG = "backend/src/barista/pack_catalog.json"   # trong mã gửi lên; lõi đọc qua kernel/pack_catalog.py


def _latest_tag(repo: Path, key: str) -> str:
    tags = [t for t in _git(repo, "tag", "--list", f"{key}/v*").decode().split() if VERSION_RE.fullmatch(t.split("/", 1)[1])]
    return max(tags, key=lambda t: tuple(int(x) for x in VERSION_RE.fullmatch(t.split("/", 1)[1]).groups()), default="") if tags else ""  # type: ignore[union-attr]


def _const(node: ast.AST | None) -> str:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _kw(call: ast.Call, name: str) -> ast.AST | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _try_parse(chunk: str) -> ast.Module | None:
    try:
        return ast.parse(chunk)
    except SyntaxError:
        return None


def _calls(source: str) -> list[str]:
    """Văn bản từng lời gọi ``Pack(…)`` / ``Module(…)`` đứng đầu dòng (cắt theo ngoặc cân, bỏ qua chuỗi + chú thích) — dùng khi cả file không parse nổi."""
    out = []
    for m in re.finditer(r"^(?:\w+ = )?(?:Pack|Module)\(", source, re.M):
        i, depth, quote = m.end() - 1, 0, ""
        while i < len(source):
            c = source[i]
            if quote:
                if c == "\\":
                    i += 1
                elif source.startswith(quote, i):
                    i += len(quote) - 1
                    quote = ""
            elif c in "\"'":
                quote = c * 3 if source.startswith(c * 3, i) else c
                i += len(quote) - 1
            elif c == "#":
                i = source.find("\n", i) - 1 if "\n" in source[i:] else len(source)
            elif c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
                if depth == 0:
                    out.append(source[m.start():i + 1])
                    break
            i += 1
    return out


def _scan(source: str, key: str, found: dict) -> None:
    """Một file mã gói: ``Pack("<khoá>", tên, mô tả, kind=, requires=, roles={…: Role(nhãn…)})`` và ``Module(khoá, tên, pack="<khoá>")`` — chỉ đọc cây ``ast``."""
    try:
        trees = [ast.parse(source)]
    except SyntaxError:   # máy phát hành có thể chạy Python cũ hơn 3.14 (f-string lồng dấu nháy…) ⇒ chỉ phân tích riêng từng lời gọi Pack( / Module(
        trees = [t for chunk in _calls(source) if (t := _try_parse(chunk)) is not None]
    for n in (n for tree in trees for n in ast.walk(tree)):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)):
            continue
        if n.func.id == "Pack" and n.args and _const(n.args[0]) == key and "title" not in found:
            args = [_const(a) for a in n.args[1:3]] + ["", ""]
            roles = _kw(n, "roles")
            requires = _kw(n, "requires")
            found.update(title=args[0], description=args[1], kind=_const(_kw(n, "kind")) or "industry",
                         requires=[_const(e) for e in requires.elts if _const(e)] if isinstance(requires, ast.Tuple) else [],
                         roles=[_const(v.args[0]) for v in roles.values if isinstance(v, ast.Call) and v.args and _const(v.args[0])]
                         if isinstance(roles, ast.Dict) else [])
        elif n.func.id == "Module" and len(n.args) >= 2 and _const(_kw(n, "pack")) == key and _const(n.args[1]):
            found.setdefault("modules", []).append(_const(n.args[1]))


def catalog(repo: Path) -> dict:
    """Danh mục chữ của MỌI gói trong repo gói (thư mục con có ``pyproject.toml``), theo tag mới nhất của từng gói (chưa tag ⇒ HEAD)."""
    keys = [k for k in _git(repo, "ls-tree", "--name-only", "HEAD").decode().split() if KEY_RE.fullmatch(k)]
    out: list[dict] = []
    for key in sorted(keys):
        ref = _latest_tag(repo, key) or "HEAD"
        try:
            with tarfile.open(fileobj=io.BytesIO(_git(repo, "archive", "--format=tar", ref, key))) as t:
                files = {m.name: t.extractfile(m).read() for m in t if m.isfile() and m.name.endswith((".py", "pyproject.toml"))  # type: ignore[union-attr]
                         and "/tests/" not in m.name and "/migrations/" not in m.name}
            meta = tomllib.loads(files[f"{key}/pyproject.toml"].decode())
        except (subprocess.CalledProcessError, KeyError, tarfile.TarError, tomllib.TOMLDecodeError):
            continue   # thư mục không phải gói (không pyproject ở ref này)
        project = meta.get("project", {})
        found: dict = {}
        for name, data in sorted(files.items()):
            if name.endswith(".py"):
                _scan(data.decode(errors="replace"), key, found)
        if found.get("kind") == "company":   # gói riêng công ty không lên danh mục / chợ gói (ADR 0233 D)
            continue
        if not found.get("title") or not project.get("version"):
            print(f"Danh mục gói: bỏ {key} ({ref}) — không đọc được Pack(...) / version", file=sys.stderr)
            continue
        summary = str(project.get("description", ""))
        out.append({"key": key, "title": found["title"], "description": found["description"] or summary, "kind": found["kind"],
                    "version": f"v{project['version']}", "requires_core": str(meta.get("tool", {}).get("barista", {}).get("requires-core", "")),
                    "requires": found["requires"], "summary": summary, "modules": found.get("modules", []), "roles": found["roles"]})
    return {"format": 1, "packs": out}


def stage(instance: dict, repo: Path, out: Path, core_api: str, ref: str = "") -> dict[str, dict[str, str]]:
    """Đặt từng gói của bản cài ở đúng tag vào ``<out>/deploy/packs/<khoá>/`` + ``manifest.json``; trả manifest (không gói ⇒ {} và không ghi gì).
    ``core_api``: ``barista.api.__version__`` của ĐÚNG commit lõi đang phát hành. ``ref`` (chỉ để KIỂM trước khi gộp — ``scripts/premerge.sh
    --packs-main``, không bao giờ để phát hành): lấy mọi gói của bản cài ở một ref của repo gói (vd ``origin/main``) thay cho tag đã ghim — bắt gói
    vỡ trước khi gói được tag; bỏ kiểm tag / ghim / ``published`` / version-khớp-tag, vẫn kiểm điểm vào, ``requires-core``, ``test-requires``."""
    want = declared(instance)
    if not want:
        return {}
    official = repo
    outside = {k: source_of(spec) for k, spec in (instance.get("packs") or {}).items() if source_of(spec)}
    if set(want) - set(outside) and not (official / ".git").exists():
        raise SystemExit(f"Bản cài {instance.get('name')} cần gói {', '.join(sorted(set(want) - set(outside)))} nhưng không thấy repo gói ở {official} (BARISTA_PACKS_DIR)")
    for k, name in outside.items():
        if not (pack_repo(name) / ".git").exists():
            raise SystemExit(f"Gói {k}: không thấy repo của đối tác ở {pack_repo(name)} — clone repo đó cạnh repo lõi (hoặc đặt BARISTA_PACK_REPOS_DIR) "
                             "sau khi đã review commit sẽ ghim")
    target = out / STAGED
    target.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, str]] = {}
    needs: dict[str, list[str]] = {}
    for key, (version, pin) in want.items():
        repo = pack_repo(outside[key]) if key in outside else official   # mỗi gói một nguồn: repo gói chính thức hoặc repo của đối tác (0242 phần 3)
        tag = ref or f"{key}/{version}"
        try:
            commit = _git(repo, "rev-parse", "-q", "--verify", f"{tag}^{{commit}}" if ref else f"refs/tags/{tag}^{{commit}}").decode().strip()
        except subprocess.CalledProcessError:
            if ref:
                raise SystemExit(f"Repo gói {repo.name} không có ref {ref}") from None
            raise SystemExit(f"Gói {key}: repo {repo.name} không có tag {tag} — tag bản gói trước (git tag -a {tag}) rồi phát hành") from None
        if not ref and pin and commit != pin:
            raise SystemExit(f"Gói {key}: tag {tag} trỏ {commit[:12]} nhưng bản cài ghim {pin[:12]} — tag đã bị dời; kiểm repo gói, sửa tag hoặc ghim")
        if not ref:
            published(repo, key, tag)
        try:
            body = _git(repo, "archive", "--format=tar", tag, key)
        except subprocess.CalledProcessError:
            raise SystemExit(f"Gói {key}: không có thư mục {key}/ ở {tag} của repo gói") from None
        with tarfile.open(fileobj=io.BytesIO(body)) as t:
            t.extractall(target, filter="data")
        meta = tomllib.loads((target / key / "pyproject.toml").read_text())
        project = meta.get("project", {})
        if ref:
            version = f"v{project.get('version')}"
        elif f"v{project.get('version')}" != version:
            raise SystemExit(f"Gói {key}: tag {tag} nhưng pyproject ghi version {project.get('version')!r}")
        entry = project.get("entry-points", {}).get("barista.packs", {})
        if key not in entry:
            raise SystemExit(f"Gói {key}: pyproject không khai điểm vào barista.packs \"{key}\"")
        spec = meta.get("tool", {}).get("barista", {}).get("requires-core", "")
        try:
            ok = satisfies(core_api, spec)
        except ValueError as e:
            raise SystemExit(f"Gói {key}: [tool.barista] requires-core {spec!r} — {e}") from None
        if not ok:
            raise SystemExit(f"Gói {key} {version} cần lõi {spec}, lõi đang phát hành có barista.api {core_api} — sửa gói / chọn bản gói khác")
        manifest[key] = {"version": version, "commit": commit, "dist": str(project.get("name", "")), **({"repo": outside[key]} if key in outside else {})}
        needs[key] = [str(r) for r in meta.get("tool", {}).get("barista", {}).get("test-requires", [])]
    # [tool.barista] test-requires: bộ TEST của gói là kịch bản xuyên gói — cần các gói đó đã cài (không phải Pack(requires=…)). Phát hành test gói
    # trên đúng bộ gói của bản cài ⇒ bản cài phải ghim cả chúng; thiếu thì dừng ở đây thay vì để test đỏ khó hiểu (0191).
    for key, others in needs.items():
        if lacking := sorted(set(others) - set(want)):
            raise SystemExit(f"Gói {key}: bộ test của gói cần gói {', '.join(lacking)} (test-requires) nhưng bản cài {instance.get('name')} không "
                             f"khai — thêm vào \"packs\" của file bản cài")
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def bundle(instance: dict, repo: Path, core_tar: Path, out: Path, ref: str = "") -> dict[str, dict[str, str]]:
    """Mã lõi (tar của ``git archive``) + gói của bản cài (``stage``, ``requires-core`` so với ``barista.api`` TRONG tar đó) ⇒ MỘT tar.gz — đúng
    thứ máy phát hành test và gửi lên VPS. Viết bằng ``tarfile`` (không qua tar của macOS: không kèm thuộc tính mở rộng / tệp ``._*``)."""
    with tarfile.open(core_tar) as src:
        member = src.extractfile(API)
        if member is None:
            raise SystemExit(f"Mã lõi thiếu {API}")
        core_api = api_version(member.read().decode())
    with tempfile.TemporaryDirectory() as d:
        manifest = stage(instance, repo, Path(d), core_api, ref)
        listing = catalog(repo) if (repo / ".git").exists() else {"packs": []}
        with tarfile.open(out, "w:gz") as dst, tarfile.open(core_tar) as src:
            for m in src:
                dst.addfile(m, src.extractfile(m) if m.isfile() else None)
            if listing["packs"]:   # danh mục gói chưa cài (0163): file CHỮ cạnh mã lõi, tách khỏi mã gói; không có repo gói ⇒ không ghi
                body = (json.dumps(listing, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode()
                info = tarfile.TarInfo(CATALOG)
                info.size, info.mode = len(body), 0o644
                dst.addfile(info, io.BytesIO(body))
            if manifest:
                dst.add(Path(d) / STAGED, arcname=str(STAGED), filter=lambda t: t.replace(uid=0, gid=0, uname="", gname="", deep=False))
    return manifest


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "instance":
        print(checked_instance_file(args[1], ops_dir()))
    elif len(args) == 1 and args[0] == "catalog":
        print(json.dumps(catalog(packs_dir()), ensure_ascii=False, indent=1, sort_keys=True))
    elif len(args) in (4, 5) and args[0] == "bundle":
        print(" ".join(bundle(json.loads(Path(args[1]).read_text()), packs_dir(), Path(args[2]), Path(args[3]), *args[4:])))
    else:
        raise SystemExit("Dùng: packs.py instance <tên> | packs.py catalog | packs.py bundle <file bản cài> <lõi.tar> <ra.tar.gz> [<ref repo gói>]")
