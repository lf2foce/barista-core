"""NẠP GÓI BẰNG ENTRY POINT (0130 lát 2): lõi nạp mọi gói ĐÃ CÀI theo thứ tự tính được (tầng → phụ thuộc → khoá), không danh sách import cứng.
Gói mẫu ``tests/fixtures/barista-pack-sample`` là một bản phân phối RIÊNG (pyproject + package của nó, chỉ dùng ``barista.api``); test "cài"
nó vào một thư mục site tạm (dist-info dựng từ chính pyproject của nó) rồi chạy lõi trong tiến trình con — có gói và đã gỡ gói."""

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from psycopg import AsyncConnection

from barista.kernel import loader, registry
from tests.conftest import MAKE_UPGRADE_DB, UPGRADE_URL, ensure_db

BACKEND = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "barista-pack-sample"


def install(site: Path) -> Path:
    """Cài bản phân phối mẫu như pip cài một wheel thuần Python: package + ``<tên>-<bản>.dist-info`` (METADATA, entry_points.txt)."""
    project = tomllib.loads((FIXTURE / "pyproject.toml").read_text())["project"]
    shutil.copytree(FIXTURE / "src" / "barista_pack_sample", site / "barista_pack_sample")
    info = site / f"{project['name'].replace('-', '_')}-{project['version']}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {project['name']}\nVersion: {project['version']}\n")
    groups = project["entry-points"]
    (info / "entry_points.txt").write_text("".join(f"[{g}]\n" + "".join(f"{k} = {v}\n" for k, v in eps.items()) + "\n" for g, eps in groups.items()))
    return site


def run(code: str, site: Path | None = None, *args: str, **env: str) -> dict:
    """Chạy lõi trong tiến trình con (sổ đăng ký của tiến trình test không bị đụng); in JSON ở dòng cuối."""
    path = [str(site)] if site else []
    full = {**os.environ, **env, "PYTHONPATH": os.pathsep.join([*path, os.environ.get("PYTHONPATH", "")]).strip(os.pathsep)}
    out = subprocess.run([sys.executable, "-c", code, *args], cwd=BACKEND, env=full, capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-3000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


SNAPSHOT = """
import json
import barista.modules
from barista.kernel import loader, registry
print(json.dumps({"loaded": [(e.key, e.dist, e.kind) for e in loader.LOADED], "modules": list(registry.MODULES),
                  "ops": sorted(registry.OPS), "packs": list(registry.PACKS)}))
"""


def test_in_repo_packs_load_through_entry_points_in_tier_then_dependency_order() -> None:
    keys = [e.key for e in loader.LOADED]
    assert keys[0] == "base" and set(keys) == set(registry.PACKS)                       # mọi gói đều qua điểm vào, kể cả gói nền
    tiers = [loader.TIERS.index(e.kind) for e in loader.LOADED]
    assert tiers == sorted(tiers)                                                         # nền → năng lực → ngành
    for i, e in enumerate(loader.LOADED):
        assert set(e.requires) <= set(keys[:i]), e.key                                    # gói cần luôn nạp trước
        assert (e.kind, e.requires) == (registry.PACKS[e.key].kind, registry.PACKS[e.key].requires)   # đọc trước = khai thật
    # gói của repo này = đúng các điểm vào khai ở backend/pyproject.toml; gói của bản phân phối khác (đang cài, 0155) không lẫn vào
    declared = set(tomllib.loads((BACKEND / "pyproject.toml").read_text())["project"]["entry-points"]["barista.packs"]) | {"base"}
    assert {e.key for e in loader.LOADED if e.dist == "barista"} == declared
    assert next(iter(registry.MODULES)) == "core" and registry.pack_module("crm") == "crm"


def test_order_is_tier_then_requires_then_key_and_refuses_missing_or_circular_packs() -> None:
    E = loader.Entry
    got = loader.order([E("z_ind", "m", "d", "industry", ()), E("a_ind", "m", "d", "industry", ("y_cap",)),
                        E("y_cap", "m", "d", "capability", ("b_cap",)), E("b_cap", "m", "d", "capability", ()),
                        E("base", "m", "d", "core", ())])
    assert [e.key for e in got] == ["base", "b_cap", "y_cap", "a_ind", "z_ind"]
    with pytest.raises(RuntimeError, match="x cần nope"):
        loader.order([E("x", "m", "d", "industry", ("nope",))])
    with pytest.raises(RuntimeError, match="vòng phụ thuộc"):
        loader.order([E("p", "m", "d", "industry", ("q",)), E("q", "m", "d", "industry", ("p",))])


def test_entry_module_must_declare_its_pack_with_literal_tier_and_requires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "ext_ok.py").write_text('from barista.api import Pack\nPack("ext", "X", "", kind="industry", requires=("crm",))\n')
    (tmp_path / "ext_wrong.py").write_text('from barista.api import Pack\nPack("other", "X", "", kind="industry")\n')
    (tmp_path / "ext_expr.py").write_text('from barista.api import Pack\nK = "industry"\nPack("ext", "X", "", kind=K)\n')
    assert loader._declared("ext_ok", "ext") == ("industry", ("crm",))
    with pytest.raises(RuntimeError, match="không khai Pack"):
        loader._declared("ext_wrong", "ext")
    with pytest.raises(RuntimeError, match="giá trị chữ"):
        loader._declared("ext_expr", "ext")


def test_reading_a_pack_declaration_runs_no_code_not_even_the_parent_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "extpkg").mkdir()
    (tmp_path / "extpkg" / "__init__.py").write_text("raise RuntimeError('package cha bị chạy')\n")
    (tmp_path / "extpkg" / "entry.py").write_text('raise RuntimeError("điểm vào bị chạy")\nPack("ext", "X", "", kind="industry", requires=("crm",))\n')
    assert loader._declared("extpkg.entry", "ext") == ("industry", ("crm",))
    assert "extpkg" not in sys.modules


def test_a_pack_whose_runtime_declaration_differs_from_what_was_read_stops_the_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """Thứ tự nạp tính theo bản đọc bằng ``ast``; gói khai khác lúc chạy (vd ``kind`` tính bằng biểu thức bị che) ⇒ dừng, báo rõ."""
    from types import SimpleNamespace

    monkeypatch.setitem(registry.PACKS, "zz_ext", SimpleNamespace(kind="capability", requires=()))
    with pytest.raises(RuntimeError, match="khác bản đọc trước khi nạp"):
        loader._check_declared(loader.Entry("zz_ext", "m", "d", "industry", ()))
    loader._check_declared(loader.Entry("zz_ext", "m", "d", "capability", ()))
    with pytest.raises(RuntimeError, match="không khai Pack"):
        loader._check_declared(loader.Entry("zz_missing", "m", "d", "industry", ()))


def test_a_pack_from_a_separate_distribution_loads_and_removing_it_leaves_the_core_running(tmp_path: Path) -> None:
    site = install(tmp_path / "site")
    without = run(SNAPSHOT)
    with_pack = run(SNAPSHOT, site)
    assert ["sample", "barista-pack-sample", "industry"] in with_pack["loaded"]
    keys = [k for k, _, _ in with_pack["loaded"]]
    assert keys.index("crm") < keys.index("sample")                                       # gói cần (crm) nạp trước
    assert keys.index("sample") > max(i for i, (_, _, kind) in enumerate(with_pack["loaded"]) if kind == "capability")
    assert "sample.notes" in with_pack["ops"] and "sample" in with_pack["packs"]
    # gỡ gói ⇒ lõi + mọi gói khác nạp y như cũ (cùng thao tác, cùng thứ tự gói)
    assert "sample" not in without["packs"]
    assert set(with_pack["ops"]) - set(without["ops"]) == {"sample.notes"}
    assert [p for p in with_pack["packs"] if p != "sample"] == without["packs"]
    assert [m for m in with_pack["modules"] if m != "sample"] == without["modules"]


def test_the_sample_pack_uses_only_the_published_surface() -> None:
    from tests.test_pack_surface import _imports, _internal

    files = sorted((FIXTURE / "src").rglob("*.py"))
    assert files and not [f"{p.name}: {n}" for p in files for n in _imports(p) if _internal(n)]


WITHOUT = """
import importlib.metadata as md, json, sys
drop = set(sys.argv[1].split(","))
real = md.entry_points
def entry_points(**kw):
    eps = real(**kw)
    return md.EntryPoints([e for e in eps if e.name not in drop]) if kw.get("group") == "barista.packs" else eps
md.entry_points = entry_points
from starlette.testclient import TestClient
from barista.http_api import app
from barista.kernel import loader
c = TestClient(app)   # không vào lifespan: không cần DB — chỉ hỏi bảng route
print(json.dumps({"packs": [e.key for e in loader.LOADED], "routes": sorted({r.path for r in app.routes if getattr(r, "methods", None)}),
                  "config": c.get("/v1/auth/config").status_code, "talk": c.get("/v1/talk/stream").status_code}))
"""


def test_the_http_app_starts_without_the_talk_and_meet_packs_and_their_routes_are_gone() -> None:
    """Gói năng lực tự mang đường HTTP (``Module.route``): gỡ Trao đổi + Gọi video ⇒ lõi HTTP vẫn lên, đường của hai gói biến mất, đường của lõi
    và gói khác còn nguyên (lõi HTTP không import / không nhắc tên gói nào)."""
    full = run(WITHOUT, None, "")
    without = run(WITHOUT, None, "talk,meet")
    assert {"talk", "meet"} <= set(full["packs"]) and not {"talk", "meet"} & set(without["packs"])
    gone = set(full["routes"]) - set(without["routes"])
    assert gone == {"/v1/talk/{thread_id}/stream", "/v1/talk/stream", "/v1/talk/files", "/v1/talk/files/{attachment_id}",
                    "/v1/meet/calls/{call_id}/stream", "/v1/meet/calls/{call_id}/signal", "/v1/public/links/{token}/stream"}, gone
    assert {"/v1/kb/upload", "/v1/papers/{paper_id}/pdf", "/v1/commands/{name}", "/v1/public/links/{token}/signal"} <= set(without["routes"])
    assert without["config"] == 200 and without["talk"] == 404 and full["talk"] == 401   # có gói: đường có, chưa đăng nhập ⇒ 401


MIGRATE = """
import asyncio, json, sys
from barista.kernel.migrate import migrate, pending_plan
from barista.seed import reset_schemas
async def main():
    if sys.argv[2] == "reset":
        await reset_schemas(sys.argv[1])
    ran = await migrate(sys.argv[1])
    print(json.dumps({"ran": ran, "plan": await pending_plan(sys.argv[1])}))
asyncio.run(main())
"""
URL = UPGRADE_URL


async def test_a_packs_own_migrations_run_after_the_packs_it_needs_and_stay_put_when_it_is_removed(tmp_path: Path) -> None:
    """0130 lát 3: migration nằm TRONG bản phân phối của gói (``<package>/migrations/<gói>/``), chạy sau lõi và sau gói nó cần, sổ khoá
    ``(gói, tên)``. Gỡ gói ⇒ lõi chạy migrate / kế hoạch bình thường (dòng sổ của gói giữ, không kiểm); cài lại ⇒ không chạy lại."""
    if MAKE_UPGRADE_DB:
        await ensure_db(URL)
    try:
        await (await AsyncConnection.connect(URL)).close()
    except Exception:
        pytest.skip(f"Không nối được DB nâng cấp {URL.rpartition('/')[2]} — kiểm Postgres local + BARISTA_TEST_ADMIN_URL (DB tự tạo theo lần chạy)")
    site = install(tmp_path / "site")
    env = {"BARISTA_FILES_STORE": "local", "BARISTA_FILES_DIR": str(tmp_path / "files"), "BARISTA_FILES_SIGN_SECRET": "test"}
    first = run(MIGRATE, site, URL, "reset", **env)
    at = first["ran"].index("sample/001_notes.sql")   # sau lõi và gói nó cần (crm); gói ngành khác xếp theo khoá
    assert at > first["ran"].index("crm/038_offering_columns.sql") and not [k for k in first["ran"][at + 1:] if k.startswith(("base/", "crm/"))]
    assert first["plan"] == {"pending": [], "maintenance": []}
    removed = run(MIGRATE, None, URL, "keep", **env)
    assert removed == {"ran": [], "plan": {"pending": [], "maintenance": []}}
    again = run(MIGRATE, site, URL, "keep", **env)
    assert again == {"ran": [], "plan": {"pending": [], "maintenance": []}}
    async with await AsyncConnection.connect(URL) as c:
        row = await (await c.execute("SELECT count(*) FROM public.barista_pack_migrations WHERE pack = 'sample'")).fetchone()
        assert row == (1,)
        assert (await (await c.execute("SELECT to_regclass('sample.notes') IS NOT NULL")).fetchone()) == (True,)
        await c.execute("DROP SCHEMA sample CASCADE")
        await c.commit()


ABSENT = """
import asyncio, json, sys
from barista import seed, workspaces
from barista.config import settings
from barista.db import db
from barista.kernel import executor, loader, packs
from barista.kernel.migrate import migrate, pending_plan
url, phase = sys.argv[1], sys.argv[2]
async def main():
    if phase == "install":
        await seed.reset_schemas(url)
    ran = await migrate(url)
    out = {"loaded": [e.key for e in loader.LOADED], "ran": ran, "plan": await pending_plan(url)}
    await db.open(settings.database_url, settings.owner_database_url)
    try:
        if phase == "install":
            tid = await seed.create_tenant("Công ty có gói mẫu", "absent.demo", people=[("admin", "Quản trị", "admin", "QT", True)],
                                           packs=("sample",), about="thử gỡ gói")
            async with db.owner_tx() as c:
                await c.execute("INSERT INTO sample.notes (tenant_id, body) VALUES (%s, 'ghi chú trước khi gỡ gói')", (tid,))
        else:
            out["installed"] = await seed.install_defaults_everywhere()   # bước --upgrade của mỗi lần phát hành
            packs.check_config()
            async with db.owner.connection() as c:
                tid = (await (await c.execute("SELECT id FROM core.tenants WHERE name = 'Công ty có gói mẫu'")).fetchone())["id"]
                out["tenant_packs"] = sorted(r["pack"] for r in await (await c.execute(
                    "SELECT pack FROM core.tenant_packs WHERE tenant_id = %s", (tid,))).fetchall())
                out["notes"] = (await (await c.execute("SELECT count(*) AS n FROM sample.notes")).fetchone())["n"]
            admin = await seed.who(tid, "admin", "absent.demo")
            listed = (await executor.query(admin, "admin.packs", {})).output
            out["packs_query"] = bool(listed)
            out["notes_query"] = (await executor.query(admin, "sample.notes", {})).status
            out["screens"] = sorted(w["key"] for w in workspaces.manifest(admin, "Công ty có gói mẫu").get("workspaces", []))
    finally:
        await db.close()
    print(json.dumps(out, default=str))
asyncio.run(main())
"""


async def test_an_install_whose_db_has_an_absent_packs_migrations_and_tenants_starts_cleanly(tmp_path: Path) -> None:
    """0155: DB đã chạy migration của một gói ngoài lõi và có công ty BẬT gói đó; bản cài sau này KHÔNG cài gói (vd gói tài trợ thương mại tách
    khỏi lõi, bản cài đầu tiên không cài). Không bước tay, không lỗi, không mất dữ liệu: migrate / kế hoạch rỗng, ``seed --upgrade`` (cài mặc định cho mọi công
    ty) chạy, schema + dữ liệu + dòng sổ + dòng ``core.tenant_packs`` của gói GIỮ nguyên (cài lại gói là chạy tiếp), thao tác của gói không có
    (như gói tắt), màn của gói không hiện."""
    if MAKE_UPGRADE_DB:
        await ensure_db(URL)
    try:
        await (await AsyncConnection.connect(URL)).close()
    except Exception:
        pytest.skip(f"Không nối được DB nâng cấp {URL.rpartition('/')[2]} — kiểm Postgres local + BARISTA_TEST_ADMIN_URL (DB tự tạo theo lần chạy)")
    site = install(tmp_path / "site")
    dbs = {"BARISTA_DATABASE_URL": URL.replace("barista_owner:barista_owner", "barista_app:barista_app"), "BARISTA_OWNER_DATABASE_URL": URL}
    env = {"BARISTA_FILES_STORE": "local", "BARISTA_FILES_DIR": str(tmp_path / "files"), "BARISTA_FILES_SIGN_SECRET": "test", "BARISTA_PACKS": "", **dbs}
    try:
        first = run(ABSENT, site, URL, "install", **env)
        assert "sample" in first["loaded"] and "sample/001_notes.sql" in first["ran"]
        gone = run(ABSENT, None, URL, "absent", **env)
        assert "sample" not in gone["loaded"]
        assert gone["ran"] == [] and gone["plan"] == {"pending": [], "maintenance": []}
        assert gone["installed"] >= 1 and "sample" in gone["tenant_packs"] and gone["notes"] == 1
        assert gone["packs_query"] and gone["notes_query"] == "not_found" and "sample" not in gone["screens"]
        again = run(ABSENT, site, URL, "absent", **env)   # cài lại: không chạy lại gì, dữ liệu còn, thao tác của gói chạy lại được
        assert again["ran"] == [] and again["notes"] == 1 and again["notes_query"] == "ok"
    finally:
        from barista.seed import reset_schemas
        await reset_schemas(URL)
