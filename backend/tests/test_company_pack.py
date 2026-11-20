"""Gói RIÊNG công ty (``Pack(kind="company")`` — ADR 0233 D): không là mặc định của bản cài, công ty không được cấp quyền thì không thấy ở danh sách /
chợ gói, không xem, không bật được; công ty được nhà vận hành cấp quyền thì thấy, bật, và thao tác của gói chạy như mọi gói. Gói chung không được
bắt buộc gói riêng; gói riêng không luôn bật."""

from collections.abc import Iterator
from typing import Any

import pytest

from barista import seed
from barista.config import settings
from barista.kernel import executor, packs, registry, screens
from barista.kernel.executor import Ctx
from barista.kernel.fields import NoInput
from barista.kernel.registry import Module, Pack
from tests.conftest import Who, ok, owner_val, q

LIVE: tuple[Any, ...] = (registry.PACKS, registry.MODULES, registry.MODULE_PACK, registry.RESOURCES, registry.RESOURCE_MODULE, registry.OPS,
                         registry.SETUP_RESOURCES, registry.PACK_MODULE, registry.ROUTES, screens.WORKSPACES, screens.CONTRIB)


@pytest.fixture
def own_pack() -> Iterator[None]:
    snap = [type(s)(s) for s in LIVE]
    Pack("acme_own", "Nghiệp vụ riêng Acme", "Gói giả — riêng một công ty", kind="company", grants=(("admin", "acme_own", "all", "read write"),))
    m = Module("acme_own", "Riêng Acme", pack="acme_own", resources={"acme_own": "Dữ liệu riêng Acme"})

    @m.query("things", resource="acme_own", title="Đồ riêng")
    async def _things(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
        return {"rows": [], "kpis": []}

    try:
        yield
    finally:
        for live, old in zip(LIVE, snap, strict=True):
            live.clear()
            live.extend(old) if isinstance(live, list) else live.update(old)
        packs._cache.clear()


def test_a_company_pack_is_never_an_install_default_and_shared_packs_cannot_require_it(own_pack: None, monkeypatch: pytest.MonkeyPatch) -> None:
    assert registry.company_packs() == {"acme_own"} and "acme_own" not in packs.defaults()
    monkeypatch.setattr(settings, "packs", "crm,acme_own")
    with pytest.raises(ValueError, match="gói riêng công ty"):
        packs.defaults()
    monkeypatch.setattr(settings, "packs", "")
    with pytest.raises(ValueError, match="luôn bật"):
        Pack("acme_always", "x", "x", kind="company", always_on=True)
    Pack("needs_private", "x", "x", kind="capability", requires=("acme_own",))
    with pytest.raises(RuntimeError, match="gói chung không được bắt buộc gói riêng công ty acme_own"):
        registry.validate()


async def test_only_the_entitled_company_sees_and_enables_it(own_pack: None, who: Who) -> None:
    admin = await who("admin")
    other = await seed.create_tenant("Công ty khác (gói riêng)", "khac-goi-rieng.demo", people=[("admin", "Quản trị khác", "admin", "QT", True)], packs=("crm",))
    stranger = await seed.who(other, "admin", "khac-goi-rieng.demo")

    for p in (admin, stranger):                                             # chưa ai được cấp: với mọi công ty, gói như không tồn tại
        assert "acme_own" not in {r["id"] for r in (await q(p, "admin.packs"))["rows"]}
        assert "Nghiệp vụ riêng Acme" not in str(await q(p, "admin.pack_store"))
        assert (await executor.query(p, "admin.pack_info", {"pack": "acme_own"})).status == "not_found"
        assert (await executor.run(p, "admin.set_pack", {"pack": "acme_own", "enabled": True})).status == "invalid"

    await owner_val("INSERT INTO core.tenant_entitlements (tenant_id, pack, granted_by, note) VALUES (%s, 'acme_own', 'test', '') RETURNING 1",
                    (admin.tenant_id,))                                     # nhà vận hành cấp cho ĐÚNG một công ty (barista.entitle)
    assert "acme_own" in {r["id"] for r in (await q(admin, "admin.packs"))["rows"]}
    assert "Nghiệp vụ riêng Acme" in str(await q(admin, "admin.pack_store")) and "Riêng của công ty bạn" in str(await q(admin, "admin.pack_store"))
    assert (await executor.query(admin, "acme_own.things", {})).status == "not_found"            # có quyền nhưng chưa bật
    await ok(admin, "admin.set_pack", pack="acme_own", enabled=True)
    admin = await who("admin")
    assert (await executor.query(admin, "acme_own.things", {})).status == "ok"

    assert "acme_own" not in {r["id"] for r in (await q(stranger, "admin.packs"))["rows"]}        # công ty kia vẫn không thấy gì
    assert (await executor.run(stranger, "admin.set_pack", {"pack": "acme_own", "enabled": True})).status == "invalid"
    assert (await executor.query(stranger, "acme_own.things", {})).status == "not_found"

    await ok(admin, "admin.set_pack", pack="acme_own", enabled=False)       # dọn: công ty mẫu dùng chung với test khác
    await owner_val("DELETE FROM core.tenant_entitlements WHERE tenant_id = %s AND pack = 'acme_own' RETURNING 1", (admin.tenant_id,))
