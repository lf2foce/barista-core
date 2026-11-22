"""``seed --add``: bản demo nhiều công ty — thêm công ty mẫu của một hồ sơ gói vào DB đang có dữ liệu; đã có thì bỏ qua; production thì từ chối. Không DB thật."""

from types import SimpleNamespace

import pytest

from barista import seed
from barista.config import settings


class _Db:
    """DB giả: đã có công ty (``core.tenants`` không trống); ``names`` = tên công ty đang có."""

    def __init__(self, names: set[str]):
        self.names = names
        self.owner = self

    async def open(self, *_):
        pass

    async def close(self):
        pass

    def connection(self):
        names = self.names

        class Conn:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def execute(self, sql: str, params: tuple = ()):
                class Cur:
                    async def fetchone(self):
                        if "WHERE name" in sql:
                            return (1,) if params[0] in names else None
                        return (1,)   # SELECT 1 FROM core.tenants LIMIT 1 — DB đã có công ty
                return Cur()
        return Conn()


def _profile(seeded: list[str]) -> SimpleNamespace:
    async def run(name: str) -> str:
        seeded.append(name)
        return "t-new"
    return SimpleNamespace(NAME="Cửa hàng mẫu", seed=run, DONE="OK: cửa hàng mẫu", AUTONOMY=False)


@pytest.fixture
def quiet(monkeypatch):
    async def nothing(*_a, **_k):
        return None
    monkeypatch.setattr(seed, "migrate", nothing)
    monkeypatch.setattr(seed.packs, "check_config", lambda: None)


async def test_add_seeds_a_second_company_once(monkeypatch, capsys, quiet):
    monkeypatch.setattr(settings, "env", "demo")
    seeded: list[str] = []
    monkeypatch.setattr(seed, "pack_profile", lambda _name: _profile(seeded))
    monkeypatch.setattr(seed, "db", _Db({"Sàn mẫu"}))
    await seed.main(False, "cua-hang", add=True)
    assert seeded == ["Cửa hàng mẫu"] and "OK: cửa hàng mẫu" in capsys.readouterr().out
    monkeypatch.setattr(seed, "db", _Db({"Sàn mẫu", "Cửa hàng mẫu"}))
    await seed.main(False, "cua-hang", add=True)
    assert seeded == ["Cửa hàng mẫu"] and "bỏ qua" in capsys.readouterr().out   # chạy lại an toàn


async def test_without_add_a_running_demo_is_left_alone(monkeypatch, capsys, quiet):
    monkeypatch.setattr(settings, "env", "demo")
    monkeypatch.setattr(seed, "pack_profile", lambda _name: pytest.fail("không --add thì không nạp thêm"))
    monkeypatch.setattr(seed, "db", _Db({"Sàn mẫu"}))
    await seed.main(False, "cua-hang")
    assert "Đã có dữ liệu mẫu" in capsys.readouterr().out


async def test_add_needs_a_pack_profile(monkeypatch, quiet):
    monkeypatch.setattr(settings, "env", "demo")
    monkeypatch.setattr(seed, "pack_profile", lambda _name: None)
    monkeypatch.setattr(seed, "db", _Db({"Sàn mẫu"}))
    with pytest.raises(SystemExit, match="hồ sơ của gói"):
        await seed.main(False, "sample", add=True)


async def test_add_is_refused_in_production_before_touching_the_database(monkeypatch, quiet):
    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(seed, "db", SimpleNamespace(open=lambda *_: pytest.fail("không được mở DB")))
    with pytest.raises(SystemExit, match="production"):
        await seed.main(False, "cua-hang", add=True)
