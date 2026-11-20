"""Ranh giới phát hành: không đổi DB khi rolling không phù hợp; không bật code cũ trên schema mới khi cutover lỗi."""
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("barista_deploy", Path(__file__).resolve().parents[2] / "deploy/ops/deploy.py")
assert SPEC is not None and SPEC.loader is not None
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)
SHA = "a" * 40
OLD = {"services": {s: {"image": f"legacy-{s}:old", "deploy": {"replicas": 1}} for s in ("api", "web")}}


class FakeDeployment(ops.Deployment):
    def __init__(self, root: Path, *, needs: bool = True, fail: str = ""):
        self.root = root
        self.config = {"postgres_container": "test-pg"}
        self.calls: list = []
        self.needs, self.fail = needs, fail
        self.requested_at = datetime.now(UTC)

    def current(self):
        return {"composeFile": json.dumps(OLD)}

    def migration_plan(self, image):
        self.calls.append("plan")
        return {"maintenance": ["179.sql"] if self.needs else []}

    def apply(self, stack, title):
        self.calls.append(("apply", json.loads(stack)))

    def wait(self, images):
        self.calls.append("wait")

    def wait_stopped(self, timeout=300):
        self.calls.append("wait_stopped")
        if self.fail == "stop":
            raise TimeoutError("still running")

    def backup_database(self, sha):
        self.calls.append("backup")
        if self.fail == "backup":
            raise RuntimeError("backup failed")
        return self.root / "before.dump"

    def migrate(self, image):
        self.calls.append("migrate")
        if self.fail == "migrate":
            raise RuntimeError("migrate failed")

    def smoke(self, sha):
        self.calls.append("smoke")
        if self.fail == "smoke":
            raise RuntimeError("smoke failed")

    def prune(self, keep, repos=frozenset()):
        self.calls.append(("prune", frozenset(keep), frozenset(repos)))

    def waited(self, images):
        self.calls.append(("wait", dict(images)))


def test_rolling_refuses_before_any_mutation(tmp_path):
    d = FakeDeployment(tmp_path)
    with pytest.raises(ValueError, match="maintenance"):
        d.release(SHA, 1, json.dumps(OLD))
    assert d.calls == ["plan"]


def test_a_release_after_the_image_rename_rolls_back_to_the_old_named_images(tmp_path):
    """0233 (16/11/2026): ảnh đổi tên thành ``barista-*``; bản đang chạy còn mang tên CŨ (``legacy-*`` ở đây — tên lấy từ Dokploy, không viết cứng).
    Bản mới lỗi ⇒ trả cấu hình cũ và chờ ĐÚNG ảnh tên cũ khoẻ; xong ⇒ sổ quay lui ghi ảnh tên cũ, dọn ảnh biết cả tên cũ lẫn tên mới."""
    d = FakeDeployment(tmp_path, needs=False, fail="smoke")
    d.wait = d.waited   # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="smoke"):
        d.release(SHA, 1, json.dumps(OLD))
    applied = [c[1] for c in d.calls if isinstance(c, tuple) and c[0] == "apply"]
    assert applied[0]["services"]["api"]["image"] == f"barista-api:{SHA}" and applied[-1] == OLD
    assert [c[1] for c in d.calls if isinstance(c, tuple) and c[0] == "wait"][-1] == {"api": "legacy-api:old", "web": "legacy-web:old"}
    ok = FakeDeployment(tmp_path, needs=False)
    ok.release(SHA, 1, json.dumps(OLD))
    assert json.loads((tmp_path / "rollback.json").read_text())["images"] == {"api": "legacy-api:old", "web": "legacy-web:old"}
    (prune,) = [c for c in ok.calls if isinstance(c, tuple) and c[0] == "prune"]
    assert {"old", SHA} <= prune[1] and {"barista-api", "barista-web", "legacy-api", "legacy-web"} == prune[2]


def test_first_release_of_a_new_install_may_roll_through_maintenance_migrations(tmp_path):
    """Bản cài mới (bootstrap: ảnh ":bootstrap", chưa có release.json): DB trống, không bản cũ nào chạy ⇒ không đòi bảo trì. Đã có
    release.json (từng phát hành) thì vẫn từ chối như trên."""
    boot = {"services": {s: {"image": f"barista-{s}:bootstrap", "deploy": {"replicas": 1}} for s in ("api", "web")}}
    d = FakeDeployment(tmp_path)
    d.current = lambda: {"composeFile": json.dumps(boot)}
    d.release(SHA, 0, json.dumps(boot))
    assert d.calls[:2] == ["plan", "migrate"] and "backup" not in d.calls
    assert json.loads((tmp_path / "release.json").read_text())["sha"] == SHA
    d2 = FakeDeployment(tmp_path)
    d2.current = lambda: {"composeFile": json.dumps(boot)}
    with pytest.raises(ValueError, match="maintenance"):
        d2.release(SHA, 1, json.dumps(boot))


def test_maintenance_stops_old_workers_then_backs_up_then_migrates(tmp_path):
    d = FakeDeployment(tmp_path)
    d.release(SHA, 1, json.dumps(OLD), maintenance=True)
    assert d.calls[:5] == ["plan", ("apply", {"services": {s: {"image": f"legacy-{s}:old", "deploy": {"replicas": 0}}
                                                        for s in ("api", "web")}}), "wait_stopped", "backup", "migrate"]
    assert json.loads((tmp_path / "release.json").read_text())["sha"] == SHA
    assert json.loads((tmp_path / "rollback.json").read_text())["restore_database_before_old_code"] is True


@pytest.mark.parametrize("failure", ["migrate", "smoke"])
def test_after_migration_failure_services_stay_stopped(tmp_path, failure):
    d = FakeDeployment(tmp_path, fail=failure)
    with pytest.raises(RuntimeError, match="giữ dừng"):
        d.release(SHA, 1, json.dumps(OLD), maintenance=True)
    final = [c[1] for c in d.calls if isinstance(c, tuple) and c[0] == "apply"][-1]
    assert all(final["services"][s]["deploy"]["replicas"] == 0 for s in ("api", "web"))
    assert not (tmp_path / "release.json").exists()


def test_backup_failure_restores_old_stack_without_migrating(tmp_path):
    d = FakeDeployment(tmp_path, fail="backup")
    with pytest.raises(RuntimeError, match="backup failed"):
        d.release(SHA, 1, json.dumps(OLD), maintenance=True)
    assert "migrate" not in d.calls
    assert [c[1] for c in d.calls if isinstance(c, tuple)][-1] == OLD


def test_maintenance_marker_and_manifest_agree():
    """``deployment.json`` của từng gói là NGUỒN CHUẨN (migration đã chạy không thêm dòng đánh dấu được — đổi file là lệch checksum). Migration ghi
    "-- deployment: maintenance" ở dòng đầu thì PHẢI có trong danh sách của gói nó — quên ⇒ bản phát hành chạy rolling trên migration đổi hợp đồng."""
    from barista.kernel.migrate import files, policy

    marked = {k for k, p in files().items() if "deployment: maintenance" in p.read_text().splitlines()[0]}
    listed = policy()
    assert marked <= listed, marked - listed
    assert listed <= set(files()), listed - set(files())


def test_migration_numbers_are_unique_per_pack_and_late_arrivals_block_the_release():
    """Trong MỘT gói: hai nhánh cùng đặt số / migration số nhỏ tới sau khi số lớn đã chạy ⇒ bước lập kế hoạch phát hành dừng (``pending_plan``).
    Hai gói khác nhau cùng số thì không sao (0130 — hết tranh số giữa các gói)."""

    from barista.kernel.migrate import chains, ordering_errors

    assert ordering_errors([(p, list(f)) for p, f in chains()], []) == []
    base = ["000_baseline/00_core.sql", "000_baseline/01_x.sql"]   # bản gốc là MỘT mục dù nhiều file
    assert ordering_errors([("base", [*base, "001_a.sql", "002_b.sql"])], [("base", n) for n in [*base, "001_a.sql"]]) == []
    late = ordering_errors([("base", [*base, "001_a.sql", "002_b.sql", "003_c.sql"])], [("base", n) for n in [*base, "001_a.sql", "003_c.sql"]])
    assert len(late) == 1 and "base/002_b.sql" in late[0]
    dup = ordering_errors([("base", [*base, "005_a.sql", "005_b.sql"])], [])
    assert len(dup) == 1 and "cùng số 005" in dup[0]
    assert ordering_errors([("crm", ["005_a.sql"]), ("talk", ["005_b.sql"])], []) == []           # hai gói cùng số: được
    old = ordering_errors([("base", [*base, "001_a.sql"])], [("base", "183_equity_research.sql")])   # file không còn trong gói ⇒ dựng lại
    assert len(old) == 1 and "trước bản gốc" in old[0]
    assert ordering_errors([("base", base)], [("gone", "001_x.sql")]) == []                        # gói đã gỡ: dòng sổ của nó bỏ qua


MODULE_FROM = 1   # sau bản gốc (0085) mọi migration khai "-- module: <khoá>" ở dòng đầu; file bản gốc cũng vậy
TARGETS = (r"CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\b",
           r"(?:CREATE(?:\s+OR\s+REPLACE)?|ALTER|DROP)\s+(?:UNIQUE\s+)?(?:TABLE|VIEW|MATERIALIZED\s+VIEW|FUNCTION|PROCEDURE|TYPE|SEQUENCE|TRIGGER)"
           r"\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?(?:ONLY\s+)?(\w+)\.", r"CREATE\s+POLICY\s+\w+\s+ON\s+(\w+)\.",
           r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+ON\s+(\w+)\.", r"INSERT\s+INTO\s+(\w+)\.(?!subject_kinds\b)",
           r"UPDATE\s+(\w+)\.", r"DELETE\s+FROM\s+(\w+)\.", r"CALL\s+core\.secure_\w+\(\s*'(\w+)\.")


def migration_schemas(sql: str) -> set[str]:
    """Schema mà migration TẠO / SỬA / GHI (không tính khoá ngoại tới core, không tính đăng ký đối tượng vào core.subject_kinds)."""
    import re

    body = re.sub(r"--[^\n]*", "", sql)
    return {m.lower() for pat in TARGETS for m in re.findall(pat, body, re.IGNORECASE)} - {"pg_temp"}   # bảng tạm của phiên (N11b) không phải module


def test_each_new_migration_belongs_to_the_modules_it_declares():
    """Chuẩn bị tách gói ra repo / dãy migration riêng (ROADMAP J): mỗi migration thuộc MỘT module — chỉ chạm schema của module đó. Việc thật
    sự xuyên module (chuyển dữ liệu giữa hai module) khai cả hai KÈM LÝ DO, ``-- module: a, b — lý do: …`` — hiện ra lúc review, lúc tách là việc
    phối hợp hai bên. Đây là hàng rào hỗ trợ review (quét SQL), không chứng minh gói chạy độc lập — việc đó là hai bài kiểm của ROADMAP J7."""
    import re

    from barista.kernel import registry
    from barista.kernel.migrate import BASELINE, chains, provenance

    assert migration_schemas("CREATE TABLE research.x (u UUID REFERENCES core.users(id)); CALL core.secure_table('research.x', 'r');"
                             "INSERT INTO core.subject_kinds VALUES (1);") == {"research"}
    assert migration_schemas("ALTER TABLE core.ai_usage ADD c INT; CREATE INDEX i ON acct.lines (a);") == {"core", "acct"}
    for pack, n, p in [(pack, n, p) for pack, found in chains() for n, p in found.items()]:
        if not n.startswith(BASELINE) and int(p.name.split("_", 1)[0]) < MODULE_FROM:
            continue
        first = p.read_text().splitlines()[0]
        head = re.match(r"--\s*module:\s*([\w ,]+)", first)
        assert head, f"{p.name}: dòng đầu phải là “-- module: <khoá>”"
        declared = [m.strip() for m in head.group(1).split(",") if m.strip()]
        assert len(declared) == 1 or "lý do:" in first, f"{p.name}: chạm nhiều module phải ghi “— lý do: …” ngay dòng đầu"
        assert migration_schemas(p.read_text()) <= set(declared), f"{p.name} chạm {migration_schemas(p.read_text()) - set(declared)} ngoài module đã khai"
        # 0130: migration nằm trong thư mục của gói sở hữu module đầu tiên nó khai (lõi / sổ cái thuộc gói nền)
        owner = registry.MODULE_PACK.get(declared[0]) or provenance(chains()).renamed.get(declared[0], "base")   # file đã chạy dưới khoá cũ của gói đổi tên (0159)
        # bản gốc cũng vậy: bảng của gói nằm ở bản gốc trong thư mục của gói (0130 ghi chú 08/11) — gỡ gói thì DB mới không có bảng của nó
        assert owner == pack, f"{pack}/{n}: module {declared[0]} thuộc gói {owner} — đặt file vào migrations/{owner}/"


def test_old_service_not_stopped_means_no_backup_no_migration_and_old_back(tmp_path):
    d = FakeDeployment(tmp_path, fail="stop")
    with pytest.raises(TimeoutError):
        d.release(SHA, 1, json.dumps(OLD), maintenance=True)
    assert "backup" not in d.calls and "migrate" not in d.calls
    final = [c[1] for c in d.calls if isinstance(c, tuple) and c[0] == "apply"][-1]
    assert all(final["services"][s]["deploy"]["replicas"] == 1 for s in ("api", "web"))


def _swarm(monkeypatch, replicas: list[int], running: list[str]):
    """Giả Swarm: mỗi lần inspect trả replicas kế tiếp; docker ps trả danh sách container đang chạy kế tiếp."""
    inspects, pss = iter(replicas), iter(running)

    def fake(*args, **kw):
        if args[:3] == ("docker", "service", "inspect"):
            return json.dumps([{"Spec": {"Mode": {"Replicated": {"Replicas": next(inspects)}}}}])
        if args[:2] == ("docker", "ps"):
            return next(pss)
        raise AssertionError(args)
    monkeypatch.setattr(ops, "command", fake)
    monkeypatch.setattr(ops.time, "sleep", lambda _s: None)


def test_wait_stopped_does_not_trust_a_spec_swarm_has_not_picked_up(monkeypatch, tmp_path):
    # 07/11/2026: ngay sau apply, Swarm vẫn báo replicas 1 + container cũ khoẻ ⇒ bản cũ trước đây được coi là "đã dừng".
    d = ops.Deployment.__new__(ops.Deployment)
    d.config = {"prefix": "demo"}
    _swarm(monkeypatch, replicas=[1, 0, 0, 0, 0], running=["abc\n", "", ""])
    assert d._stopped("demo_api") is False          # spec chưa đổi
    assert d._stopped("demo_api") is False          # spec 0 nhưng container cũ còn chạy
    d.wait_stopped(timeout=5)                         # spec 0, không container ở cả api lẫn web


def test_wait_counts_spec_zero_as_not_running(monkeypatch):
    d = ops.Deployment.__new__(ops.Deployment)
    d.requested_at = datetime.now(UTC)

    def fake(*args, **kw):
        if args[:3] == ("docker", "service", "inspect"):
            return json.dumps([{"Spec": {"Mode": {"Replicated": {"Replicas": 0}},
                                          "TaskTemplate": {"ContainerSpec": {"Image": "legacy-api:old"}}}}])
        if args[:2] == ("docker", "ps"):
            return ""
        raise AssertionError(args)
    monkeypatch.setattr(ops, "command", fake)
    assert d._healthy("demo_api", "legacy-api:old") is False


def test_every_sql_file_lives_in_the_folder_of_a_loaded_pack(tmp_path):
    """0130: ``.sql`` ở gốc ``migrations/``, trong thư mục không phải của gói đã nạp, hay thư mục con lạ của một gói ⇒ lỗi (runner sẽ bỏ qua
    nó). Cây thật sạch."""
    from barista.kernel.migrate import BASELINE, roots, stray_errors

    assert stray_errors(roots()) == []
    root = tmp_path / "migrations"
    for d in ("crm/000_baseline", "crm/old", "commerce", "__pycache__", "empty"):
        (root / d).mkdir(parents=True)
    for f in ("044_cart.sql", "crm/001_a.sql", f"crm/{BASELINE}/00_x.sql", "crm/old/002_b.sql", "commerce/001_c.sql", "__pycache__/x.sql"):
        (root / f).write_text("SELECT 1;")
    errors = stray_errors({root: {"crm"}})
    assert len(errors) == 3, errors
    assert "044_cart.sql nằm ở gốc" in errors[0] and "commerce/" in errors[1] and "crm/old/" in errors[2]
