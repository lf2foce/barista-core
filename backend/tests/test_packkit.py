"""Bộ công cụ gói cho đối tác (ADR 0242, ``scripts/pack.sh``): khuôn sinh ra một gói ĐÚNG hợp đồng — đọc tĩnh đạt, mã chạy chỉ dùng ``barista.api``,
migration đúng chỗ — và khuôn từ chối khoá trùng lõi. Phần kiểm đầy đủ (cài gói vào lõi sạch, ruff, pyright, test của gói, test cấu trúc) chạy bằng
``bash scripts/pack.sh check <gói>`` — quá nặng cho bộ test này (dựng môi trường riêng); lần đo 19/11/2026: gói khuôn ĐẠT 4 bước trong ~75 giây,
bản cố ý sai (import ``barista.kernel``, truy vấn ``crm.leads``, ``requires-core`` ngoài lõi) KHÔNG ĐẠT đúng ba chỗ đó."""

import ast
import importlib.util
import tomllib
from pathlib import Path

import pytest

import barista.api
from tests.test_pack_surface import _imports, _internal

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("packkit", ROOT / "scripts" / "packkit" / "packkit.py")
assert _spec and _spec.loader
packkit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(packkit)


@pytest.fixture
def made(tmp_path: Path) -> Path:
    return packkit.new("kho_lanh", tmp_path / "kho-lanh", "Kho lạnh")


def test_the_template_makes_a_pack_that_passes_the_static_contract(made: Path) -> None:
    r = packkit.Report()
    info = packkit.static_check(made, barista.api.__version__, r)
    assert not r.fails, r.fails
    assert info == {"key": "kho_lanh", "pkg": "barista_pack_kho_lanh", "dist": "barista-pack-kho-lanh", "version": "0.1.0"}
    meta = tomllib.loads((made / "pyproject.toml").read_text(encoding="utf-8"))
    major, minor = barista.api.__version__.split(".")[:2]
    assert meta["tool"]["barista"]["requires-core"] == f">={major}.{minor},<2"
    assert (made / "src/barista_pack_kho_lanh/migrations/kho_lanh/001_kho_lanh.sql").read_text().startswith("-- module: kho_lanh\n")
    assert (made / ".gitignore").is_file() and (made / "tests/test_kho_lanh.py").is_file()


def test_the_generated_code_parses_and_its_runtime_imports_only_the_published_surface(made: Path) -> None:
    files = sorted(made.rglob("*.py"))
    for f in files:
        ast.parse(f.read_text(encoding="utf-8"))
    runtime = [f for f in files if "/tests/" not in f.as_posix()]
    assert runtime and not [f"{p.name}: {n}" for p in runtime for n in _imports(p) if _internal(n)]
    assert not [str(f) for f in made.rglob("*") if f.is_file() and "@@" in f.read_text(encoding="utf-8")]


@pytest.mark.parametrize("key", ["crm", "core", "acct", "public", "Kho", "1kho", "k"])
def test_a_key_that_clashes_with_the_core_or_is_malformed_is_refused(tmp_path: Path, key: str) -> None:
    with pytest.raises(SystemExit):
        packkit.new(key, tmp_path / "x", "X")
    assert not (tmp_path / "x").exists()


def test_the_static_check_names_what_is_wrong(made: Path) -> None:
    py = made / "pyproject.toml"
    py.write_text(py.read_text(encoding="utf-8").replace('requires-core = ">=', 'requires-core = ">=9.').replace("-p tests.conftest ", ""), encoding="utf-8")
    sql = made / "src/barista_pack_kho_lanh/migrations/kho_lanh/001_kho_lanh.sql"
    sql.rename(made / "src/barista_pack_kho_lanh/migrations/002_lac_cho.sql")
    r = packkit.Report()
    packkit.static_check(made, barista.api.__version__, r)
    joined = " | ".join(r.fails)
    assert "KHÔNG chứa barista.api" in joined and "tests.conftest" in joined and "migrations/kho_lanh/" in joined, r.fails


def test_refuses_to_write_into_a_folder_that_has_content(tmp_path: Path) -> None:
    (tmp_path / "co-roi").mkdir()
    (tmp_path / "co-roi" / "x.txt").write_text("giữ nguyên")
    with pytest.raises(SystemExit):
        packkit.new("kho_lanh", tmp_path / "co-roi", "Kho lạnh")
    assert [p.name for p in (tmp_path / "co-roi").iterdir()] == ["x.txt"]


@pytest.fixture
def made_connector(tmp_path: Path) -> Path:
    return packkit.new("noi_cms", tmp_path / "noi-cms", "Nối CMS", "connector")


def test_the_connector_template_makes_a_connector_pack_on_the_core_framework(made_connector: Path) -> None:
    """Khuôn gói kết nối (0244): đọc tĩnh đạt như khuôn thường, không sót ``@@…@@``, mã chạy chỉ dùng bề mặt, và DÙNG khung kết nối của lõi (bộ nối +
    lệnh nhận + lưu kết nối qua ``connectors.save``) thay vì tự làm bảng kết nối / khoá."""
    r = packkit.Report()
    packkit.static_check(made_connector, barista.api.__version__, r)
    assert not r.fails, r.fails
    files = [p for p in made_connector.rglob("*") if p.is_file()]
    assert files and not [p.name for p in files if "@@" in p.read_text(encoding="utf-8")]
    src = (made_connector / "src" / "barista_pack_noi_cms" / "__init__.py").read_text(encoding="utf-8")
    ast.parse(src)
    assert "connectors.Connector" in src and "@m.connector(Api)" in src and "connectors.save(" in src and 'm.consumes("connector")' in src
    sql = (made_connector / "src" / "barista_pack_noi_cms" / "migrations" / "noi_cms" / "001_noi_cms.sql").read_text(encoding="utf-8")
    assert "secret" not in sql.lower() and "token" not in sql.lower() and "cursor" not in sql.lower()   # khoá / con trỏ ở lõi, không ở gói
    runtime = sorted((made_connector / "src").rglob("*.py"))
    assert runtime and not [f"{p.name}: {n}" for p in runtime for n in _imports(p) if _internal(n)]
    assert "use_transport" in (made_connector / "tests" / "test_noi_cms.py").read_text(encoding="utf-8")   # test không gọi mạng thật


def test_a_pack_that_opens_its_own_webhook_door_is_told_to_use_the_connector_framework(made: Path) -> None:
    init = made / "src/barista_pack_kho_lanh/__init__.py"
    init.write_text(init.read_text(encoding="utf-8") + '\nm.inbound("kho_lanh.receive_hook")\n', encoding="utf-8")
    r = packkit.Report()
    packkit.static_check(made, barista.api.__version__, r)
    assert any("Module.inbound" in f and "runbook 22" in f for f in r.fails), r.fails
