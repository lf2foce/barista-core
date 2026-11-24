"""Kiểm cấu trúc: không bảng nào lọt RLS, role app không vượt RLS, ma trận quyền / menu không trỏ vào thứ không tồn tại,
module không đọc / ghi bảng của module khác."""

import ast
import re
from pathlib import Path

import pytest

from barista import presets
from barista.kernel import registry
from barista.workspaces import INBOX, workspaces
from tests.conftest import owner_val
from tests.module_src import module_files, py_files, rel, sql_files

SRC = Path(__file__).resolve().parent.parent / "src" / "barista"
# Schema nghiệp vụ = mọi schema migration tạo ra (tự nhận gói / module mới — không danh sách viết tay để quên thêm)
# Schema ĐỔI TÊN (``ALTER SCHEMA a RENAME TO b`` — vd 0159: bản gốc đã chạy tạo tên cũ, migration sau đổi tên) ⇒ tính tên CUỐI.
_SQL = [f.read_text() for f in sql_files()]   # bản gốc 000_baseline/ + migration sau đó
_RENAMED = dict(re.findall(r"ALTER SCHEMA ([a-z_]+) RENAME TO ([a-z_]+)", "\n".join(_SQL)))
BUSINESS_SCHEMAS = tuple(sorted({_RENAMED.get(m, m) for sql in _SQL
                                 for m in re.findall(r"CREATE SCHEMA (?:IF NOT EXISTS )?([a-z_]+)", sql)} - {"dbos"}))


async def test_every_table_has_forced_rls_and_a_policy() -> None:
    missing = await owner_val(
        """SELECT coalesce(string_agg(n.nspname || '.' || c.relname, ', '), '') FROM pg_class c
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relkind = 'r' AND n.nspname = ANY(%s)
             AND NOT (c.relrowsecurity AND c.relforcerowsecurity AND EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid))""",
        (list(BUSINESS_SCHEMAS),))
    assert missing == "", f"bảng thiếu RLS: {missing}"


async def test_app_role_cannot_bypass_rls() -> None:
    assert await owner_val("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = 'barista_app'") is False


async def test_table_resources_are_registered() -> None:
    comments = await owner_val(
        """SELECT array_agg(DISTINCT substr(obj_description(c.oid, 'pg_class'), 10)) FROM pg_class c
           JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relkind = 'r' AND obj_description(c.oid, 'pg_class') LIKE 'resource=%%'""")
    assert set(comments) <= set(registry.RESOURCES), set(comments) - set(registry.RESOURCES)


def test_seed_grants_use_known_resources() -> None:
    for role, res, scope, actions in presets.grants():
        assert res in registry.RESOURCES, (role, res)
        assert scope in ("own", "unit", "team", "all")   # unit: đội mình THUỘC (0125 §A) — gói khai được ở Pack.grants
        assert set(actions.split()) <= {"read", "write", "approve", "manage"}


def test_menu_points_at_real_ops() -> None:
    for w in workspaces():
        for s in (*w.screens, INBOX):
            assert registry.OPS[s.query].kind == "query", s.query
            for a in (*s.actions, *s.row_actions):
                op = registry.OPS[a.command]
                assert op.kind == "command" and not op.internal, a.command
                assert set(a.bind) | set(a.preset) <= set(op.input.model_fields), (a.command, a.bind, a.preset)
            if s.open:
                assert set(s.open.bind) <= set(registry.OPS[s.open.query].input.model_fields)


def test_ops_have_title_and_known_resource() -> None:
    for op in registry.OPS.values():
        assert op.title, op.name
        assert op.resource is None or op.resource in registry.RESOURCES


# Ranh giới module: SQL trong module X chỉ chạm schema của X (+ vài bảng core để tra tên người / đội). MỌI file module đều bị kiểm (module mới tự
# vào); module không có schema riêng (điều hành, sức khoẻ…) chỉ được đọc bảng core cho phép. Ngoại lệ có chủ đích, ghi lý do:
CORE_WRITERS = {"admin": "quản trị cấu hình lõi (người, đội, vai, quyền, chuỗi duyệt, gói, sự kiện lỗi)",
                "connect": "app từ xa (0140): sổ app / quyền / token / webhook ở core vì xác thực (kernel) đọc trước khi có công ty"}
MODULE_FILES = module_files()   # module ↔ file (một file, hoặc package — 0088)
OWN_SCHEMA = {mod: (mod if mod in BUSINESS_SCHEMAS else None) for mod in MODULE_FILES}
CORE_READABLE = {"users", "team_members", "team_terms", "team_parent_log", "teams", "memberships", "roles", "grants", "tenant", "me", "today", "ensure_user",
                 "legal_entities", "default_entity",   # pháp nhân (0099): chiều dùng chung trên chứng từ tiền
                 "embed_profiles"}   # hồ sơ nhúng theo công ty (0232): CHỈ vòng quét ``kb.embed_sweep`` đọc để biết model đang dùng của từng công ty; nhà vận hành ghi, kb không ghi
SQL_REF = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-z_]+)\.([a-z_]+)", re.IGNORECASE)


def test_modules_only_touch_their_own_schema() -> None:
    bad = []
    for mod, schema in OWN_SCHEMA.items():
        for node in (n for f in MODULE_FILES[mod] for n in ast.walk(ast.parse(f.read_text()))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for s, table in SQL_REF.findall(node.value):
                    if s == schema or (s == "core" and (table in CORE_READABLE or mod in CORE_WRITERS)):
                        continue
                    if s in BUSINESS_SCHEMAS:
                        bad.append(f"{mod}: {s}.{table}")
    assert not bad, bad


def test_sweeps_are_declared_by_modules_not_hardcoded() -> None:
    """Vòng quét định kỳ (0066): bộ điều phối không đọc bảng module nào — mỗi lệnh quét là lệnh nội bộ tự khai ``sweep=`` (SQL đó nằm trong file
    module nên đã qua kiểm ranh giới schema ở trên)."""
    src = (SRC / "sweeps.py").read_text()
    leaked = [f"{s}.{t}" for s, t in SQL_REF.findall(src) if s in BUSINESS_SCHEMAS]
    assert not leaked, leaked
    for name in registry.SWEEPS:
        op = registry.get(name)
        assert op is not None and op.kind == "command" and op.internal, name
        assert "tenant_id" in registry.SWEEPS[name], name
    assert {"leadflow.enforce_sla", "books.open_due"} <= set(registry.SWEEPS)


def _pack_calls() -> list[tuple[str, str, str, int, str]]:
    """(module, hàm, đích, dòng, cách gọi) cho mọi lời gọi chéo module bằng tên chữ (``ctx.call*("mod.op")``)."""
    out = []
    for mod, path in ((m, f) for m, files in MODULE_FILES.items() for f in files):
        tree = ast.parse(path.read_text())
        fns = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)]
        # hàm lồng (vd phần tải sau ``ui.part`` viết thành hàm con trong handler) là của handler bao nó: lời gọi tính cho hàm ngoài cùng
        # (``ast.walk`` của hàm ngoài đã gồm thân hàm con) — không xét hàm con như một hàm riêng không khai ``needs``
        nested = {id(n) for f in fns for n in ast.walk(f) if n is not f and isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)}
        for fn in [f for f in fns if id(f) not in nested]:
            for node in ast.walk(fn):
                # ctx.call*("mod.op") và cả executor.invoke / run / query(…, "mod.op", …) — bản đầu chỉ bắt ctx.call*, sót work → deals (0056)
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and (node.func.attr.startswith("call") or node.func.attr in ("invoke", "run", "query"))):
                    continue
                for arg in node.args[:2]:
                    if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                            and re.fullmatch(r"[a-z_]+\.[a-z_]+", arg.value) and arg.value.split(".")[0] in registry.MODULES):
                        target = arg.value.split(".")[0]
                        if target != mod:
                            out.append((mod, fn.name, target, node.lineno, node.func.attr))
                        break
    return out


def test_cross_pack_calls_are_required_or_optional() -> None:
    """Quyết định 0045: module gọi sang module khác gói ⇒ gói đó phải nằm trong gói bắt buộc của mình (``Pack(requires=...)``), hoặc
    thao tác gọi khai ``needs=`` gói đó, hoặc gọi bằng ``*_optional`` (gói tắt ⇒ None). Không thì tắt gói kia làm vỡ gói này."""
    needs_of = {(op.module, op.handler.__name__): set(op.needs) for op in registry.OPS.values()}
    needs_of |= {(sub.name.split(".")[0], sub.handler.__name__): set(sub.needs) for sub in registry.SUBSCRIBERS.values()}
    bad = []
    for mod, fn, target, line, how in _pack_calls():
        own, other = registry.MODULE_PACK[mod], registry.MODULE_PACK[target]
        have = registry.pack_closure({own} | needs_of.get((mod, fn), set())) | {"base"}   # gói kéo theo (bắc cầu) cũng tính
        ok = other in have or how.endswith("_optional")
        if not ok:
            bad.append(f"{mod}.py:{line} {fn}() gọi {target} (gói {other}) bằng {how}")
    assert not bad, "\n".join(bad)


def test_every_module_declares_a_known_pack() -> None:
    assert set(registry.MODULES) == set(registry.MODULE_PACK)
    assert set(registry.MODULE_PACK.values()) <= set(registry.PACKS)
    for pack, pk in registry.PACKS.items():
        assert set(pk.requires) <= set(registry.PACKS) - {"base"}, pack
    registry.validate()
    # ``needs`` được nhắc gói không cài (validate cho qua) — ở bản cài đủ gói của repo thì phải là gói thật (bắt gõ sai khoá),
    # trừ bên nghe sự kiện của gói ngoài chưa nạp (``n == s.event_type.split(".")[0]``: giữ ràng buộc 0056 khi gói đó được cài).
    assert not [(op.name, n) for op in registry.OPS.values() for n in op.needs if n not in registry.PACKS]
    assert not [(s.name, n) for s in registry.SUBSCRIBERS.values() for n in s.needs
                if n not in registry.PACKS and not (n == s.event_type.split(".")[0] and n not in registry.MODULE_PACK)]


async def test_every_subject_kind_has_a_db_visibility_function() -> None:
    """Quyết định 0046: quyền đọc trao đổi = quyền thấy đối tượng, kiểm ở DB qua hàm của module sở hữu — loại nào khai ở registry
    mà thiếu hàm thì trao đổi trên loại đó KHÔNG ai đọc được (an toàn) nhưng là lỗi."""
    kinds = set(await owner_val("SELECT array_agg(kind) FROM core.subject_kinds") or [])
    assert set(registry.SUBJECTS) == kinds, set(registry.SUBJECTS) ^ kinds


def test_listeners_of_another_packs_events_declare_that_pack() -> None:
    """0056: bên nghe sự kiện của module gói khác (vd kế toán nghe "ký giao dịch") phải khai gói đó (``m.on(..., needs=)``) hoặc gói của
    mình kéo theo nó — không thì công ty tắt gói ngành vẫn chạy phản ứng ngành (lập hoá đơn phí CĐT cho công ty không môi giới)."""
    bad = []
    for sub in registry.SUBSCRIBERS.values():
        mod, src = sub.name.split(".")[0], sub.event_type.split(".")[0]
        if src not in registry.MODULE_PACK:
            continue   # sự kiện lõi (subject.*)
        other = registry.MODULE_PACK[src]
        if other not in registry.pack_closure({registry.MODULE_PACK[mod], *sub.needs}) | {"base"}:
            bad.append(f"{sub.name} nghe {sub.event_type} (gói {other}) mà không khai needs")
    assert not bad, "\n".join(bad)


def test_core_and_capability_packs_do_not_depend_on_industry_packs() -> None:
    """0056: lõi (kernel / agent) không import module nào; gói năng lực dùng chung không BẮT BUỘC gói ngành (thiếu ⇒ công ty ngành khác
    không bật được kế toán / trao đổi)."""
    bad = [f"{p.relative_to(SRC)}: {line.strip()}" for d in ("kernel", "agent") for p in (SRC / d).rglob("*.py")
           for line in p.read_text().splitlines() if re.match(r"\s*(from barista\.modules|import barista\.modules)", line)]
    assert not bad, bad
    for pack, pk in registry.PACKS.items():
        if pk.kind in ("core", "capability"):
            assert not [r for r in pk.requires if registry.pack_kind(r) == "industry"], (pack, pk.requires)



def test_structure_checks_see_every_module_and_schema() -> None:
    """Kiểm cấu trúc tự nhận module / schema mới (review 01/11): mọi module đã đăng ký đều có file được quét; mọi schema migration tạo đều nằm trong
    danh sách kiểm RLS; module có bảng riêng thì schema trùng tên module."""
    every = set(registry.MODULES)   # kể cả module của gói ngoài repo đang cài (0191): mã + migration của chúng được quét như mã trong repo
    assert set(OWN_SCHEMA) == every - {"core"}, set(OWN_SCHEMA) ^ (every - {"core"})
    assert {"core", "ledger", "crm", "acct"} <= set(BUSINESS_SCHEMAS)
    assert set(BUSINESS_SCHEMAS) - {"core", "ledger"} <= every, set(BUSINESS_SCHEMAS) - every


def test_account_codes_used_in_code_are_declared_by_a_module() -> None:
    """0059: kernel không khai tài khoản ngành; mọi mã tài khoản cố định viết trong module phải được một module khai (ledger.declare)."""
    from barista.kernel import ledger

    assert not re.search(r'"(revenue|receivable|expense):', (SRC / "kernel" / "ledger.py").read_text().split('"""', 2)[2])
    used = {c for files in MODULE_FILES.values() for p in files
            for c in re.findall(r'"((?:cash|receivable|revenue|expense|payout|unapplied):[a-z_]+)"', p.read_text())}
    assert used and used <= set(ledger.ACCOUNTS), used - set(ledger.ACCOUNTS)
    # 0189: tài khoản cơ chế do module KHAI nó tự đánh dấu — mọi mã đánh dấu là tài khoản đã khai, và Tài chính không còn danh sách tay
    assert ledger.MECHANISM <= set(ledger.ACCOUNTS) and {"revenue:opening_adjustment", "revenue:advance_charge"} <= ledger.MECHANISM
    assert "CATEGORY_SKIP" not in (SRC / "modules" / "acct" / "corrections.py").read_text()


def test_team_of_a_person_has_one_definition() -> None:
    """0062: đội chính / đội tham gia / đội quản lý của MỘT người chỉ định nghĩa ở ``kernel/teams.py`` — module / lõi khác gọi
    ``ctx.home_team`` hoặc đọc ``ctx.p`` (chép truy vấn ra nhiều nơi ⇒ mở nhiều đội sẽ lặng lẽ lấy nhầm đội ở chỗ sót). Liệt kê NHIỀU
    người kèm đội (JOIN để hiển thị / lọc theo cây) vẫn viết trong SQL của mình."""
    own = SRC / "kernel" / "teams.py"
    bad = [f"{rel(p)}: {m.group(0)}" for p in py_files() if p != own
           for m in re.finditer(r"SELECT\s+(?:tm\.)?team_id\s+FROM\s+core\.team_members(?:\s+tm)?\s+WHERE\s+(?:tm\.)?(?:tenant_id\s*=\s*\S+\s+AND\s+)?(?:tm\.)?user_id",
                                p.read_text())]
    assert not bad, bad


def test_every_model_goes_through_the_ai_switch() -> None:
    """0063 / 151: công ty tắt trợ lý AI ⇒ không model nào được dựng. Chỉ ``agent/llm.py`` gọi ``build_model``; mọi nơi khác lấy model qua
    ``llm.model_for(principal, …)`` (kiểm công tắc trước, kể cả khi tiêm sẵn model)."""
    own = SRC / "agent" / "llm.py"
    bad = [rel(p) for p in py_files() if p != own and re.search(r"\bbuild_model\(", p.read_text())]
    assert not bad, bad


LINK_READABLE = {"core.tenants", "core.tenant_packs", "core.events", "core.deliveries"}   # cửa executor cần khi người qua link chạy thao tác


async def test_link_sessions_cannot_read_company_configuration() -> None:
    """0064: bảng "theo công ty" (thành viên, vai, quyền, đội, link, mẫu…) có chính sách hạn chế p_no_link — phiên của link công khai không đọc được,
    kể cả khi một thao tác public lỡ viết SQL rộng. Bảng tạo sau đi qua core.secure_tenant là tự có."""
    rows = await owner_val("""SELECT array_agg(p.schemaname || '.' || p.tablename) FROM pg_policies p WHERE p.policyname = 'p_tenant'
                              AND NOT EXISTS (SELECT 1 FROM pg_policies x WHERE x.schemaname = p.schemaname AND x.tablename = p.tablename
                                              AND x.policyname = 'p_no_link')""")
    assert set(rows or []) == LINK_READABLE, set(rows or []) ^ LINK_READABLE
    assert await owner_val("SELECT 1 FROM pg_policies WHERE schemaname = 'core' AND tablename = 'users' AND policyname = 'p_no_link'") == 1


# File RÁP (hub) — thêm gói KHÔNG được phải sửa (0069): kernel, menu, mặc định, trợ lý chỉ đọc registry. Chuỗi hằng trong các file này không
# được nhắc tên module / gói ngoài gói nền (chỉ docstring được miễn). Câu gợi ý của trợ lý nằm ở gói sở hữu thao tác (``Role.suggest`` / ``Pack.hints``).
HUB_FILES = (*sorted((SRC / "kernel").glob("*.py")), SRC / "workspaces.py", SRC / "presets.py", SRC / "agent" / "desk.py",
             SRC / "agent" / "chat.py", SRC / "http_api.py", SRC / "mcp_server.py")   # HTTP của lõi: route của gói do module tự khai (Module.route)


# Chữ trùng tên gói nhưng là NGÔN NGỮ TỰ NHIÊN, không trỏ tới gói: (file, chữ) ⇒ lý do. Giữ nhỏ.
NATURAL_WORDS: dict[tuple[str, str], str] = {
    ("import_match.py", "budget"): "từ tiếng Anh trong bảng đồng nghĩa tiêu đề cột Excel (cột \"Budget\" = ngân sách của khách) — không gọi gói Ngân sách",
}


def test_hub_files_do_not_name_modules_or_packs_outside_base() -> None:
    import ast

    foreign_mods = {m for m, p in registry.MODULE_PACK.items() if p != "base"}
    foreign_packs = set(registry.PACKS) - {"base"}
    for path in HUB_FILES:
        tree = ast.parse(path.read_text())
        skip: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and node.body \
                    and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                skip.add(id(node.body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
                v = node.value
                if (path.name, v) in NATURAL_WORDS:
                    continue
                assert v not in foreign_packs and v.split(".")[0] not in foreign_mods, (path.name, node.lineno, v)


def test_the_http_layer_imports_no_module_and_mounts_module_routes_from_the_registry() -> None:
    """HTTP của lõi không import module nào (chỉ ``barista.modules`` để nạp gói đã cài) và không viết tay đường của gói: route của gói do module
    tự khai (``Module.route``) — mọi route đã khai đều được gắn, đúng phương thức + đường."""
    from barista.http_api import app

    tree = ast.parse((SRC / "http_api.py").read_text())
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {f"{n.module}.{a.name}" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module for a in n.names}
    assert not [n for n in names if n.startswith("barista.modules.")], names
    mounted = {(m, getattr(r, "path", "")) for r in app.routes for m in getattr(r, "methods", None) or ()}
    declared = {(r.method, r.path) for r in registry.ROUTES}
    assert declared and declared <= mounted, declared - mounted
    assert all(r.path.startswith("/v1/") for r in registry.ROUTES)


# Route do module khai KHÔNG cần đăng nhập — mỗi route một lý do; thêm route công khai = sửa danh sách này có chủ đích (gói ngoài khai qua core_guards.json → public_routes)
PUBLIC_ROUTES = {("GET", "/v1/public/links/{token}/stream")}


async def test_every_module_route_needs_a_login_unless_declared_public() -> None:
    """Gọi MỌI route module khai mà không xác thực: route thường ⇒ 401 (lõi chặn trước handler); route công khai (``public="<lý do>"``) với mã
    link bịa ⇒ 404. Danh sách route công khai chốt ở ``PUBLIC_ROUTES``."""
    import re
    import uuid

    from httpx import ASGITransport, AsyncClient

    from barista.http_api import app
    from tests.module_src import declared

    pack_public = set()
    for k in declared("public_routes"):
        parts = k.split(" ", 1)
        assert len(parts) == 2 and parts[0] in ("GET", "POST", "PUT", "PATCH", "DELETE") and parts[1].startswith("/v1/"), f"public_routes sai dạng: {k!r}"
        pack_public.add((parts[0], parts[1]))
    assert {(r.method, r.path) for r in registry.ROUTES if r.public} == (PUBLIC_ROUTES | pack_public)
    assert all(len(r.public) >= 10 for r in registry.ROUTES if r.public)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        for r in registry.ROUTES:
            url = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), r.path)
            got = (await c.request(r.method, url, json={} if r.method != "GET" else None, timeout=10)).status_code
            assert got == (404 if r.public else 401), (r.method, r.path, got)


def test_module_sources_are_one_file_or_one_package(tmp_path: Path) -> None:
    """0088: module là file ``modules/<khoá>.py`` HOẶC package ``modules/<khoá>/`` — mọi test đọc mã đi qua ``module_files`` nên tách module lớn
    thành package không làm hụt kiểm (ranh giới schema, gọi chéo, từ ngữ ngành, hợp đồng quyền hệ thống)."""
    from tests.module_src import module_of

    root = tmp_path / "modules"
    (root / "big" / "parts").mkdir(parents=True)
    for f in ("__init__.py", "small.py", "big/__init__.py", "big/plans.py", "big/parts/deep.py"):
        (root / f).write_text("")
    assert {m: [str(p.relative_to(root)) for p in fs] for m, fs in module_files(root).items()} == {
        "big": ["big/__init__.py", "big/parts/deep.py", "big/plans.py"], "small": ["small.py"]}
    assert (module_of(root / "big" / "plans.py", root), module_of(root / "small.py", root), module_of(tmp_path / "x.py", root)) == ("big", "small", None)
    (root / "big.py").write_text("")
    with pytest.raises(AssertionError, match="chọn một dạng"):
        module_files(root)


def test_every_operation_lives_in_its_own_modules_source() -> None:
    """Thao tác / bên nghe khai bằng ``m`` của module nào thì nằm trong file của đúng module đó — file con của một package không lỡ đăng ký
    thành module khác (kiểm ranh giới đọc mã theo module mới đúng)."""
    import inspect

    from tests.module_src import module_of

    bad = []
    for name, fn, owner in [(op.name, op.handler, op.module) for op in registry.OPS.values()] + \
                           [(s.name, s.handler, s.name.split(".")[0]) for s in registry.SUBSCRIBERS.values()]:
        src = inspect.getsourcefile(fn)
        mod = module_of(Path(src)) if src else None
        if mod is not None and mod != owner:
            bad.append(f"{name}: khai bằng module {owner} nhưng nằm trong mã của {mod}")
    assert not bad, bad


async def test_no_table_keeps_file_bodies_in_bytea() -> None:
    """0122: thân tệp ở kho đối tượng, bảng chỉ giữ ``file_id`` trỏ ``core.files``. Cột bytea chỉ được là mã băm (token_hash, sha…) — không thân tệp."""
    found = await owner_val(
        """SELECT coalesce(string_agg(table_schema || '.' || table_name || '.' || column_name, ', '), '') FROM information_schema.columns
           WHERE data_type = 'bytea' AND table_schema = ANY(%s) AND column_name !~ '(hash|sha|digest)'
             AND NOT (table_schema = 'core' AND table_name = 'session_key')
             AND NOT (table_schema = 'connectors' AND table_name = 'secrets' AND column_name = 'sealed')""", (list(BUSINESS_SCHEMAS),))
    # ngoại lệ: khoá ký claims phiên (N11b); khoá kết nối ngoài đã mã hoá AES-GCM, vài trăm byte (0244) — không phải thân tệp
    assert found == "", f"cột bytea giữ thân tệp: {found} — dùng kernel/files.py (0122)"


async def test_foreign_keys_between_company_tables_carry_the_company() -> None:
    """ADR 0258: khoá ngoại giữa hai bảng CÙNG mang ``tenant_id`` phải mang cả ``tenant_id`` (dòng con và dòng cha luôn cùng công ty — DB tự giữ, kể cả
    khi mã chạy bằng kết nối owner / quyền hệ thống, nơi RLS không chặn). Bảng mới khai thẳng ``FOREIGN KEY (tenant_id, x) REFERENCES đích (tenant_id, id)``;
    hoặc migration của gói gọi ``CALL core.tenant_fks(ARRAY['<schema>'])`` sau khi tạo bảng. Quét MỌI schema đang có (cả gói ngoài đang cài)."""
    bad = await owner_val(
        """SELECT coalesce(string_agg(c.conrelid::regclass || '.' || c.conname || ' → ' || c.confrelid::regclass, ', ' ORDER BY c.conrelid::regclass::text, c.conname), '')
           FROM pg_constraint c JOIN pg_class r ON r.oid = c.conrelid JOIN pg_namespace n ON n.oid = r.relnamespace
           WHERE c.contype = 'f' AND n.nspname NOT IN ('public', 'dbos') AND n.nspname !~ '^pg_' AND c.confrelid <> 'core.tenants'::regclass
             AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.conrelid AND a.attname = 'tenant_id' AND NOT a.attisdropped)
             AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.confrelid AND a.attname = 'tenant_id' AND NOT a.attisdropped)
             AND NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) AND a.attname = 'tenant_id')""")
    assert bad == "", f"khoá ngoại thiếu tenant_id (ADR 0258): {bad}"


def test_pack_guards_list_names_every_source_scanning_test() -> None:
    """0191: test đọc mã qua ``tests/module_src.py`` cũng quét gói ngoài đang cài — phát hành chạy lại đúng các file ghi ở ``tests/pack_guards.txt`` sau
    khi cài gói của bản cài. File test mới dùng ``module_src`` mà quên ghi vào danh sách ⇒ gói ngoài lọt luật đó."""
    here = Path(__file__).resolve().parent
    listed = {ln.strip() for ln in (here / "pack_guards.txt").read_text().splitlines() if ln.strip() and not ln.startswith("#")}
    scanning = {f"tests/{p.relative_to(here).as_posix()}" for p in sorted(here.rglob("test_*.py"))
                if re.search(r"from tests\.module_src import [^\n]*\b(py_files|sql_files|module_files|module_of|declared)\b", p.read_text())
                or re.search(r"from tests\.module_src import \(", p.read_text())}
    assert scanning == listed, scanning ^ listed


# Màn chưa có câu "để làm gì" (cơ chế lõi G1 — ``Screen.about``) của các gói TRONG repo này, đo 24/11/2026. Chỉ được GIẢM: màn mới phải có ``about``; viết
# thêm cho màn cũ thì hạ số này xuống đúng số mới. Gói ngoài repo (barista-packs) tự giữ con số của mình.
SCREENS_WITHOUT_ABOUT = 157


def test_new_screens_say_what_they_are_for() -> None:
    from barista.kernel import loader
    from barista.workspaces import INBOX, workspaces

    ours = {e.key for e in loader.LOADED if e.dist == "barista"}
    screens = {(s.query, s.key): s for w in workspaces() for s in w.screens} | {(INBOX.query, INBOX.key): INBOX}
    mine = [s for s in screens.values() if registry.MODULE_PACK.get(s.query.split(".")[0]) in ours]
    missing = sorted(s.key for s in mine if not s.about.strip())
    assert len(missing) <= SCREENS_WITHOUT_ABOUT, f"màn mới thiếu câu about (G1): {len(missing)} > {SCREENS_WITHOUT_ABOUT}"
    assert len(missing) == SCREENS_WITHOUT_ABOUT, f"đã viết thêm about ⇒ hạ SCREENS_WITHOUT_ABOUT xuống {len(missing)}"   # con số chỉ đi xuống, luôn đúng
    assert all(len(s.about) <= 200 and "\n" not in s.about for s in mine if s.about), "about là MỘT câu ngắn"
