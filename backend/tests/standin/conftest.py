"""Test của LÕI chạy qua GÓI THẾ VAI NGÀNH (``tests/standin/pack.py`` — ADR 0190): bản lõi của các test cơ chế trước đây chỉ được phủ khi có gói Bất động
sản / Hoa hồng (repo ``barista-packs``). Chạy được cả khi môi trường không cài gói ngành nào lẫn khi đang cài.

Cách ly: gói thế vai chỉ có trong sổ đăng ký TRONG LÚC một file test ở thư mục này chạy (fixture ``standin`` — phạm vi module; xdist ``--dist loadfile`` giữ
trọn file trong một tiến trình) và mọi dữ liệu của nó nằm ở một CÔNG TY RIÊNG ("Công ty thế vai") — công ty mẫu và công ty khác của bộ test không có dòng
nào mang loại đối tượng / loại việc / bộ kiểm của gói đã gỡ. Bảng của gói (``schema.sql``) tạo một lần mỗi DB test; dòng ``core.subject_kinds``, dòng bật gói
và quyền dùng gói của công ty thế vai thêm / gỡ quanh từng file (các sổ đó phải khớp registry — ``tests/test_meta.py``, ``tests/test_presets.py``)."""

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from psycopg import AsyncConnection

from barista import seed
from barista.db import db
from barista.kernel import events, packs, registry, vocab
from barista.kernel.principal import Principal
from tests import conftest as core
from tests.conftest import Who
from tests.standin import pack

DOMAIN = "thevai.demo"
_tenant: UUID | None = None   # công ty thế vai của tiến trình test này (một DB mỗi tiến trình)


def _containers() -> list[tuple[str, str, Any]]:
    """Mọi sổ cấp module (dict / list / set tên VIẾT HOA) của lõi mà một gói khai vào: kernel + ``workspaces`` / ``presets``. Không liệt kê tay — sổ mới
    của lõi tự được chụp / trả."""
    out = []
    for name, mod in list(sys.modules.items()):
        if name.startswith("barista.kernel.") or name in ("barista.workspaces", "barista.presets"):
            out += [(name, k, v) for k, v in vars(mod).items() if k.isupper() and isinstance(v, (dict, list, set))]
    return out


def _copy(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: (type(x)(x) if isinstance(x, (dict, list, set)) else x) for k, x in v.items()}
    return type(v)(v)


def _restore(snap: list[tuple[Any, Any]]) -> None:
    for live, old in snap:
        live.clear()
        live.extend(old) if isinstance(live, list) else live.update(old)


async def _owner(sql: Any, params: Any = None) -> None:
    async with db.owner_tx() as conn:
        await conn.execute(sql, params)


@pytest_asyncio.fixture(scope="module", autouse=True, loop_scope="session")
async def standin(demo: dict[str, Any]) -> AsyncIterator[UUID]:
    """Khai gói thế vai + bật cho công ty thế vai suốt một file test; xong file: chạy hết sự kiện, gỡ gói, trả sổ đăng ký như cũ."""
    global _tenant
    snap = [(v, _copy(v)) for _, _, v in _containers()]
    pack.register()
    registry.validate()
    try:
        async with await AsyncConnection.connect(core.OWNER_URL, autocommit=True) as conn:
            if not (await (await conn.execute("SELECT to_regnamespace('orderdesk') IS NOT NULL")).fetchone() or (False,))[0]:
                await conn.execute((Path(__file__).parent / "schema.sql").read_bytes())
        await _owner("INSERT INTO core.subject_kinds (kind, visible_fn, pack) VALUES (%s, 'orderdesk.subject_visible_order', %s) ON CONFLICT DO NOTHING",
                     (pack.ORDER, pack.PACK))
        if _tenant is None:   # công ty riêng: người / đội của công ty mẫu, gói của lõi + gói thế vai — cài mặc định qua đúng admin.install_defaults
            _tenant = await seed.create_tenant("Công ty thế vai (test)", DOMAIN, people=list(seed.PEOPLE), packs=(*seed.ALL_PACKS, pack.PACK))
        else:
            await _owner("INSERT INTO core.tenant_packs (tenant_id, pack) VALUES (%s, %s) ON CONFLICT DO NOTHING", (_tenant, pack.PACK))
            await _owner("""INSERT INTO core.tenant_entitlements (tenant_id, pack, granted_by, note) VALUES (%s, %s, 'seed', 'gói thế vai của test')
                            ON CONFLICT DO NOTHING""", (_tenant, pack.PACK))
        packs.invalidate(_tenant)
        vocab.invalidate(_tenant)
        yield _tenant
        await events.drain()
    finally:
        # mọi công ty (công ty thế vai + công ty dùng một lần mà test tạo): không công ty nào còn "bật" một gói đã gỡ khỏi sổ đăng ký
        async with db.owner.connection() as conn:
            on = [r["tenant_id"] for r in await (await conn.execute("SELECT tenant_id FROM core.tenant_packs WHERE pack = %s", (pack.PACK,))).fetchall()]
        await _owner("DELETE FROM core.tenant_packs WHERE pack = %s", (pack.PACK,))
        await _owner("DELETE FROM core.tenant_entitlements WHERE pack = %s", (pack.PACK,))
        for t in on:
            packs.invalidate(t)
            vocab.invalidate(t)
        await _owner("DELETE FROM core.subject_kinds WHERE kind = %s", (pack.ORDER,))
        _restore(snap)


@pytest.fixture
def company(standin: UUID) -> UUID:
    """Mã công ty thế vai (thay ``demo["tenant"]`` của test gốc)."""
    return standin


@pytest.fixture
def who(demo: dict[str, Any], standin: UUID) -> Who:
    """Người của CÔNG TY THẾ VAI (cùng tên đăng nhập với công ty mẫu: admin, ketoan, giamdoc, truongb, sale3…); ``other=True`` = công ty khác của bộ test."""
    async def f(local: str, *, other: bool = False) -> Principal:
        return await seed.who(demo["other"] if other else standin, local, core.OTHER_DOMAIN if other else DOMAIN)

    return f
