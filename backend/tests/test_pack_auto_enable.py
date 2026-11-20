"""Bật gói thì bật KÈM gói nó ``requires`` (0157): bắc cầu, gói được cần trước, mỗi gói cài mặc định của riêng nó trong một giao dịch; gói (đích hay kèm)
công ty chưa có quyền dùng ⇒ từ chối cả đợt, không bật gì; chợ gói nói "Có sẵn" (không "Cần …") khi chỉ là chưa bật; tắt vẫn bị chặn khi gói đang bật cần nó."""

import uuid
from typing import Any

from barista import seed
from barista.config import settings
from barista.db import db
from barista.kernel import executor, registry
from tests.conftest import ok, owner_val, q

ADMIN = [("admin", "Quản trị", "admin", "QT", True)]


async def _bare() -> tuple[Any, Any]:
    d = f"{uuid.uuid4().hex[:6]}.kem.test"
    tid = await seed.create_tenant(f"Công ty bật kèm {d}", d, packs=(), people=ADMIN, about="thử bật kèm")
    return tid, await seed.who(tid, "admin", d)


async def _rows(tid: Any) -> set[str]:
    async with db.owner.connection() as c:
        cur = await c.execute("SELECT pack FROM core.tenant_packs WHERE tenant_id = %s", (tid,))
        return {r["pack"] for r in await cur.fetchall()}


def test_pack_order_puts_required_packs_first_and_the_target_last() -> None:
    order = registry.pack_order("pay")
    assert order[-1] == "pay" and set(order) == registry.pack_closure({"pay"})
    assert all(order.index(r) < order.index(p) for p in order for r in registry.pack_requires(p))   # mọi gói đứng sau gói nó cần (bắc cầu)
    assert registry.pack_order("talk") == ["talk"]


async def test_enabling_a_pack_pulls_its_requirements_in_dependency_order_with_each_ones_defaults() -> None:
    tid, admin = await _bare()
    assert await _rows(tid) == set()
    out = await ok(admin, "admin.set_pack", pack="pay", enabled=True)                       # pay cần directory, finance, hr (finance và hr cần directory)
    assert out["also_enabled"] == ["directory", "finance", "hr"], out
    assert out["menu"] is True                                                               # app tải lại thanh bên ngay — phân hệ của gói hiện luôn
    assert "bật kèm: Danh mục cá nhân & tổ chức, Tài chính" in out["summary"] or "bật kèm:" in out["summary"]
    assert await _rows(tid) == {"directory", "finance", "hr", "pay"}
    # mặc định của TỪNG gói đã cài: chuỗi duyệt chi của Tài chính, quyền danh mục, vai của Lương
    assert await owner_val("SELECT count(*) FROM core.approval_chains WHERE tenant_id = %s AND key = 'settlement_payment'", (tid,)) == 1
    assert await owner_val("SELECT count(*) FROM core.pack_installs WHERE tenant_id = %s AND pack = 'finance'", (tid,)) > 0
    assert await owner_val("SELECT count(*) FROM core.pack_installs WHERE tenant_id = %s AND pack = 'directory'", (tid,)) > 0
    assert await owner_val("SELECT count(*) FROM core.roles WHERE tenant_id = %s AND key = 'payroll'", (tid,)) == 1
    again = await ok(admin, "admin.set_pack", pack="pay", enabled=True)                       # bật lại: không bật thêm gì, không lỗi
    assert again["also_enabled"] == [] and await _rows(tid) == {"directory", "finance", "hr", "pay"}


async def test_the_whole_enable_is_refused_when_a_required_pack_is_not_entitled_and_nothing_is_enabled() -> None:
    tid, admin = await _bare()
    old = settings.packs
    settings.packs = "talk"                                                                 # bản cài chỉ mặc định Trao đổi (+ nền, gói luôn bật)
    try:
        await owner_val("INSERT INTO core.tenant_entitlements (tenant_id, pack, granted_by) VALUES (%s, 'pay', 'nhà cung cấp') RETURNING 1", (tid,))
        store = await q(admin, "admin.pack_store")
        cards = {i["title"]: i for b in store["blocks"] if b["type"] == "tiles" for i in b["items"]}
        assert cards["Lương & thù lao"]["badge"].startswith("Cần ") and "Tài chính" in cards["Lương & thù lao"]["badge"], cards["Lương & thù lao"]
        info = await q(admin, "admin.pack_info", pack="pay")
        assert not any(a["command"] == "admin.set_pack" for a in info["actions"])           # không có nút Bật khi thiếu quyền gói kèm
        r = await executor.run(admin, "admin.set_pack", {"pack": "pay", "enabled": True})
        assert r.status == "invalid" and "cần bật kèm" in r.error and "Tài chính" in r.error and "Chưa bật gói nào" in r.error, r.error
        assert await _rows(tid) == set()                                                    # không gói nào được bật, kể cả gói được phép
        assert await owner_val("SELECT count(*) FROM core.pack_installs WHERE tenant_id = %s AND pack IN ('pay', 'finance', 'directory', 'hr')", (tid,)) == 0
        for pk in ("directory", "finance", "hr"):                                                 # nhà cung cấp mở đủ gói kèm ⇒ bật được
            await owner_val("INSERT INTO core.tenant_entitlements (tenant_id, pack, granted_by) VALUES (%s, %s, 'nhà cung cấp') RETURNING 1", (tid, pk))
        await ok(admin, "admin.set_pack", pack="pay", enabled=True)
        assert await _rows(tid) == {"directory", "finance", "hr", "pay"}
    finally:
        settings.packs = old


async def test_the_store_says_available_and_the_preview_says_what_will_be_enabled_along() -> None:
    tid, admin = await _bare()
    store = await q(admin, "admin.pack_store")
    cards = {i["title"]: i for b in store["blocks"] if b["type"] == "tiles" for i in b["items"]}
    assert cards["Lương & thù lao"]["badge"] == "Có sẵn" and cards["Tài chính"]["badge"] == "Có sẵn"   # chưa bật ≠ "Cần …"
    info = await q(admin, "admin.pack_info", pack="pay")
    head = info["blocks"][0]
    assert "Sẽ bật kèm: " in head["text"] and "Tài chính" in head["text"] and "Cần bật trước" not in head["text"]
    titles = [b["title"] for b in info["blocks"] if b["type"] == "list"]
    assert any(t.startswith("Bật kèm “Tài chính” sẽ thêm") for t in titles), titles
    assert any(a["command"] == "admin.set_pack" and a["preset"] == {"pack": "pay", "enabled": True} for a in info["actions"])
    finance_items = next(b["items"] for b in info["blocks"] if b["type"] == "list" and b["title"].startswith("Bật kèm “Tài chính”"))
    assert any(i["badge"] == "Chuỗi duyệt" for i in finance_items)
    assert await _rows(tid) == set()                                                        # xem trước không đổi gì


async def test_disabling_stays_blocked_while_an_enabled_pack_requires_it() -> None:
    tid, admin = await _bare()
    await ok(admin, "admin.set_pack", pack="pay", enabled=True)
    r = await executor.run(admin, "admin.set_pack", {"pack": "finance", "enabled": False})
    assert r.status == "invalid" and "gói đang bật cần nó" in r.error and "Lương & thù lao" in r.error, r.error
    r = await executor.run(admin, "admin.set_pack", {"pack": "directory", "enabled": False})
    assert r.status == "invalid" and "gói đang bật cần nó" in r.error, r.error
    assert await _rows(tid) == {"directory", "finance", "hr", "pay"}
    await ok(admin, "admin.set_pack", pack="pay", enabled=False)                            # gỡ từ ngọn: pay → hr, finance → directory
    await ok(admin, "admin.set_pack", pack="hr", enabled=False)
    await ok(admin, "admin.set_pack", pack="finance", enabled=False)
    await ok(admin, "admin.set_pack", pack="directory", enabled=False)
    assert await _rows(tid) == set()
