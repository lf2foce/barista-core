"""Soạn với AI là GÓI TUỲ CHỌN, mặc định TẮT (không phải gói nền luôn bật): công ty chưa bật ⇒ phân hệ, màn, thao tác và công cụ trợ lý của nó biến mất;
quản trị bật ở "Store" ⇒ có đủ như trước. Công ty chưa từng có dòng ``core.tenant_packs`` cho gói này ⇒ tắt (không cần migration)."""

from typing import Any

from httpx import ASGITransport, AsyncClient

from barista.agent import chat
from barista.http_api import app
from barista.kernel import executor, registry
from tests.conftest import Who, ok, owner_val, q


def _h(p) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"X-Dev-User": p.email, "X-Tenant-Id": str(p.tenant_id)}


async def _workspaces(p) -> set[str]:  # type: ignore[no-untyped-def]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return {w["key"] for w in (await c.get("/v1/manifest", headers=_h(p))).json()["workspaces"]}


def test_the_pack_is_optional_self_contained_and_not_always_on() -> None:
    pack = registry.PACKS["compose"]
    assert pack.kind == "capability" and not pack.always_on and pack.title == "Ấn phẩm (thử nghiệm)"
    assert registry.MODULE_PACK["compose"] == "compose" and "compose" not in registry.BASE_MODULES
    assert "compose" not in registry.always_on()


async def test_off_by_default_nothing_of_it_is_visible_or_runnable(who: Who) -> None:
    sale = await who("sale1")
    assert "compose" not in await _workspaces(sale)                                          # menu
    tools = {op.name for op, _ in await chat.catalog(sale)}
    assert not {n for n in tools if n.startswith("compose.")}                                # công cụ trợ lý
    for name, inp in (("compose.start", {"kind": "post", "brief": "x"}), ("compose.begin", {"kind": "post", "brief": "x"})):
        assert (await executor.run(sale, name, inp)).status != "ok", name                    # thao tác (ghi)
    assert (await executor.query(sale, "compose.studio", {})).status != "ok"                 # truy vấn (màn)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:   # luồng HTTP đi qua cùng cửa lệnh
        r = await c.post("/v1/compose/stream", headers=_h(sale), json={"kind": "post", "brief": "Báo cáo"})
    assert r.status_code == 200 and '"type": "error"' in r.text and '"session"' not in r.text


async def test_the_admin_switches_it_on_in_the_store_and_off_again(who: Who) -> None:
    admin, sale = await who("admin"), await who("sale1")
    store = await q(admin, "admin.pack_store")
    tiles = [t for b in store["blocks"] if b.get("type") == "tiles" for t in b["items"]]
    tile = next((t for t in tiles if t["title"] == "Ấn phẩm (thử nghiệm)"), None)
    assert tile is not None and tile["badge"] == "Có sẵn", "gói phải hiện ở Store (Có sẵn) để quản trị bật"
    await ok(admin, "admin.set_pack", pack="compose", enabled=True)
    try:
        sale = await who("sale1")   # nạp lại quyền theo gói đang bật
        assert "compose" in await _workspaces(sale)
        assert {"compose.studio", "compose.list"} <= {op.name for op, _ in await chat.catalog(sale)}
        assert (await executor.query(sale, "compose.studio", {})).status == "ok"
    finally:
        await ok(admin, "admin.set_pack", pack="compose", enabled=False)
    assert "compose" not in await _workspaces(await who("sale1"))


async def test_drafts_are_intact_after_the_pack_is_switched_off_and_on_again(who: Who, compose_on: None) -> None:
    """Tắt gói chỉ ẩn màn / công cụ — bản soạn + mọi phiên bản giữ nguyên trong DB, bật lại ra đúng như cũ và sửa tiếp được (như ghi ở nhãn tắt gói)."""
    from tests.test_compose_ai import _draft, _start

    admin, sale = await who("admin"), await who("sale1")
    out, _ = await _start(sale, [_draft("Tình hình như bảng dưới. [cần số liệu: mục tiêu tháng]")])
    sid = out["session_id"]

    async def stored() -> Any:
        return await owner_val(
            """SELECT jsonb_build_object('title', s.title, 'blocks', s.blocks, 'version', s.version, 'status', s.status, 'owner', s.owner_id,
                      'versions', (SELECT jsonb_agg(jsonb_build_object('v', v.version, 'title', v.title, 'blocks', v.blocks, 'via', v.via) ORDER BY v.version)
                                   FROM compose.versions v WHERE v.session_id = s.id))
               FROM compose.sessions s WHERE s.id = %s""", (sid,))

    before = await stored()
    assert before["status"] == "ready" and before["versions"], before
    page_before = (await q(sale, "compose.view", session_id=sid))["blocks"][0]

    await ok(admin, "admin.set_pack", pack="compose", enabled=False)
    sale = await who("sale1")   # nạp lại quyền theo gói đang tắt
    assert (await executor.query(sale, "compose.view", {"session_id": sid})).status != "ok"      # tắt: không xem / không sửa được
    assert (await executor.run(sale, "compose.remove_block", {"session_id": sid, "block": before["blocks"][1]["id"]})).status != "ok"
    assert await stored() == before                                                              # dữ liệu không bị đụng

    await ok(admin, "admin.set_pack", pack="compose", enabled=True)
    sale = await who("sale1")
    assert await stored() == before
    page_after = (await q(sale, "compose.view", session_id=sid))["blocks"][0]
    assert page_after["blocks"] == page_before["blocks"] and page_after["stats"] == page_before["stats"]
    assert sid in {str(r["id"]) for r in (await q(sale, "compose.list"))["rows"]}
    await ok(sale, "compose.remove_block", session_id=sid, block=before["blocks"][1]["id"])     # bật lại: sửa tiếp bình thường
    assert (await stored())["version"] == before["version"] + 1
