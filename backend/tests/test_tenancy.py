"""``barista.api.tenancy`` (công ty khách của công ty dịch vụ — 0063, 1.8) — review 11/11: công ty dịch vụ KHÔNG do gói truyền vào mà lấy từ ``ctx``
(Ctx do lõi dựng cho module đang chạy), module phải khai ``consumes("tenancy")``, gói cấp cho khách chỉ là gói công ty dịch vụ đang có, ``run_in`` /
``query_in`` không chạy thao tác nội bộ / quản trị. Test chạy chỉ với lõi: module ``mkt`` đóng vai module của một gói ngoài khai ``consumes``."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from barista import seed
from barista.api import tenancy as api_tenancy
from barista.db import db
from barista.kernel import registry, tenancy
from barista.kernel.errors import Denied
from barista.kernel.executor import core_ctx
from barista.kernel.principal import Principal
from tests.conftest import owner_val

PACKS = ("finance", "books", "talk")
MODULE = "mkt"   # đóng vai module của gói ngoài lõi


@pytest.fixture(autouse=True)
def _consumers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "FAMILY_CALLERS", {**registry.FAMILY_CALLERS, "tenancy": {MODULE}})


async def _service(packs: tuple[str, ...] = PACKS) -> Principal:
    d = f"dv{uuid.uuid4().hex[:6]}.test"
    tid = await seed.create_tenant(f"Công ty dịch vụ {d}", d, people=[("admin", "Quản trị", "admin", "QT", True), ("kt", "Kế toán", "bookkeeper", "KT", False)],
                                   packs=packs)
    return await seed.who(tid, "admin", d)


@asynccontextmanager
async def _ctx(who: Principal, module: str = MODULE) -> AsyncIterator[Any]:
    async with db.tenant_tx(who) as conn:
        yield core_ctx(conn, who, module=module)


async def test_the_api_surface_is_the_kernels_own_functions() -> None:
    assert api_tenancy.run_in is tenancy.run_in and api_tenancy.ensure_company is tenancy.ensure_company
    import inspect
    for name in api_tenancy.__all__:
        params = inspect.signature(getattr(api_tenancy, name)).parameters
        assert "provider" not in params and next(iter(params)) == "ctx", name   # công ty dịch vụ không bao giờ do bên gọi nêu


async def test_a_company_can_provision_and_manage_only_its_own_customers() -> None:
    a, b = await _service(), await _service()
    client = uuid.uuid4()
    async with _ctx(a) as ctx:
        assert await tenancy.ensure_company(ctx, client, "Khách của A", packs=("books",)) is True
        assert await tenancy.ensure_company(ctx, client, "Khách của A", packs=("books",)) is False        # chạy lại ⇒ cùng một công ty
        assert (await tenancy.query_in(ctx, client, "kb.shelf_options")) is not None
    assert await owner_val("SELECT provisioned_by FROM core.tenants WHERE id = %s", (client,)) == a.tenant_id
    async with _ctx(b) as ctx:   # công ty dịch vụ KHÁC: mọi đường chạm khách của A đều bị từ chối
        for call in (tenancy.ensure_company(ctx, client, "Chiếm khách", packs=("books",)),
                     tenancy.sync_members(ctx, client, members=[(uuid.uuid4(), "admin")]),
                     tenancy.active_members(ctx, client),
                     tenancy.set_ai(ctx, client, enabled=True),
                     tenancy.run_in(ctx, client, "kb.save_shelf", {"name": "Lạ", "everyone": True}),
                     tenancy.query_in(ctx, client, "kb.shelf_options")):
            with pytest.raises(Denied, match="không do công ty của bạn tạo"):
                await call
    assert await owner_val("SELECT count(*) FROM kb.shelves WHERE tenant_id = %s", (client,)) == 0
    assert await owner_val("SELECT provisioned_by FROM core.tenants WHERE id = %s", (client,)) == a.tenant_id


async def test_the_provider_cannot_be_named_by_the_caller() -> None:
    a = await _service()
    client = uuid.uuid4()
    async with _ctx(a) as ctx:
        with pytest.raises(TypeError):   # không còn tham số provider
            await tenancy.ensure_company(ctx, client, "X", provider=uuid.uuid4(), packs=("books",))   # type: ignore[call-arg]
        with pytest.raises(TypeError, match="Ctx không do lõi dựng"):   # Ctx tự dựng / giả ⇒ lõi từ chối
            await tenancy.ensure_company(type("FakeCtx", (), {"p": a, "module": MODULE})(), client, "X", packs=("books",))   # type: ignore[arg-type]


async def test_a_module_that_did_not_declare_tenancy_is_refused() -> None:
    a = await _service()
    client = uuid.uuid4()
    async with _ctx(a, module="crm") as ctx:
        with pytest.raises(Denied, match="consumes"):
            await tenancy.ensure_company(ctx, client, "Không khai", packs=("books",))
    async with _ctx(a, module="") as ctx:
        with pytest.raises(Denied, match="consumes"):
            await tenancy.gather(ctx, [(a.tenant_id, "chính nó")], "books.summary")
    assert await owner_val("SELECT count(*) FROM core.tenants WHERE id = %s", (client,)) == 0


async def test_a_customer_gets_only_packs_the_service_company_has() -> None:
    a = await _service(("finance", "books", "talk"))
    client = uuid.uuid4()
    async with _ctx(a) as ctx:
        with pytest.raises(Denied, match="không có gói commission"):   # gói trả tiền mà công ty dịch vụ không có / chưa được cấp
            await tenancy.ensure_company(ctx, client, "Khách đòi gói lạ", packs=("books", "commission"))
        assert await owner_val("SELECT count(*) FROM core.tenants WHERE id = %s", (client,)) == 0
        assert await owner_val("SELECT count(*) FROM core.tenant_entitlements WHERE tenant_id = %s", (client,)) == 0
        await tenancy.ensure_company(ctx, client, "Khách đúng gói", packs=("books", "talk"))
        with pytest.raises(Denied, match="không có gói commission"):   # công ty đã có rồi vẫn không cài thêm mặc định của gói lạ
            await tenancy.ensure_company(ctx, client, "Khách đúng gói", packs=("commission",))
    assert sorted(r for r in await owner_val("SELECT array_agg(pack) FROM core.tenant_packs WHERE tenant_id = %s", (client,))) == ["books", "talk"]


async def test_run_in_refuses_internal_admin_and_unknown_operations() -> None:
    a = await _service()
    client = uuid.uuid4()
    async with _ctx(a) as ctx:
        await tenancy.ensure_company(ctx, client, "Khách thử", packs=("books",))
        for name, inp in (("admin.set_pack", {"pack": "commission", "enabled": True}), ("admin.invite_user", {"email": "x@y.test", "name": "X", "role": "admin"}),
                          ("acct.issue_receivable", {}), ("work.open", {}), ("không.có", {})):
            with pytest.raises(Denied):
                await tenancy.run_in(ctx, client, name, inp)
            with pytest.raises(Denied):
                await tenancy.query_in(ctx, client, name, inp)
    assert await owner_val("SELECT count(*) FROM core.tenant_packs WHERE tenant_id = %s AND pack = 'commission'", (client,)) == 0


def test_the_namespace_guard_matches_the_prefix_with_its_dot(monkeypatch: pytest.MonkeyPatch) -> None:
    """``admin.*`` / ``core.*`` bị chặn theo TIỀN TỐ CÓ DẤU CHẤM (và theo khoá module); tên chỉ giống chữ đầu (``admin2.x``, ``acct2.x``, ``coreX.x``) không bị chặn nhầm."""
    from types import SimpleNamespace
    ops = {"admin.x": SimpleNamespace(internal=False, module="khac"), "core.x": SimpleNamespace(internal=False, module="khac"),
           "x.y": SimpleNamespace(internal=False, module="admin"),
           "admin2.x": SimpleNamespace(internal=False, module="admin2"), "acct2.x": SimpleNamespace(internal=False, module="acct2"),
           "coreX.x": SimpleNamespace(internal=False, module="coreX"), "acct.ok": SimpleNamespace(internal=False, module="acct"),
           "acct.private": SimpleNamespace(internal=True, module="acct")}
    monkeypatch.setattr(registry, "get", ops.get)
    for name in ("admin.x", "core.x", "x.y", "acct.private"):
        with pytest.raises(Denied):
            tenancy._allowed_op(name)
    for name in ("admin2.x", "acct2.x", "coreX.x", "acct.ok"):
        tenancy._allowed_op(name)   # không ném


async def test_every_tenancy_entry_refuses_a_module_without_consumes() -> None:
    """Gói KHÔNG khai ``consumes("tenancy")`` bị từ chối ở MỌI cửa (không chỉ ``ensure_company`` / ``gather``) — và không có dòng nào bị ghi."""
    a = await _service()
    client = uuid.uuid4()
    async with _ctx(a, module="crm") as ctx:
        calls = (tenancy.ensure_company(ctx, client, "Không khai", packs=("books",)), tenancy.sync_members(ctx, client, members=[(uuid.uuid4(), "admin")]),
                 tenancy.active_members(ctx, client), tenancy.set_ai(ctx, client, enabled=True), tenancy.deactivate_everywhere(ctx, uuid.uuid4()),
                 tenancy.run_in(ctx, client, "kb.save_shelf", {"name": "Lạ", "everyone": True}), tenancy.query_in(ctx, client, "kb.shelf_options"),
                 tenancy.gather(ctx, [(a.tenant_id, "chính nó")], "books.summary"))
        for call in calls:
            with pytest.raises(Denied, match="consumes"):
                await call
    assert await owner_val("SELECT count(*) FROM core.tenants WHERE id = %s", (client,)) == 0
