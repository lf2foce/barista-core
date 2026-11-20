"""KHÁCH HÀNG VÀ CHIA LEAD LÀ NĂNG LỰC DÙNG CHUNG (0110) — bản LÕI (ADR 0190) của ``barista-packs:real_estate/tests/test_crm_capability.py``: công ty chỉ bật
Khách hàng chạy đủ (mã thứ khách quan tâm là chữ người nhập); phần của gói ngành chỉ hiện khi gói CẮM vào điểm của Khách hàng (``crm.offering`` /
``crm.offering_options`` / ``crm.lead_story``) và theo quyền người xem; chỉ module chủ được hỏi điểm cắm của mình.

Danh mục của gói thế vai (module ``orderlines``) chỉ được khai TRONG file này (fixture ``lines``) — các file khác chạy với công ty thế vai không có danh
mục; sổ đăng ký trả về như cũ sau file (fixture ``standin``)."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from barista import seed
from barista.agent import desk
from barista.db import db
from barista.kernel import executor, registry
from barista.workspaces import manifest
from tests.conftest import Who, approved, fresh_phone, ok, owner_val, q
from tests.standin import pack

PEOPLE = [("admin", "QT", "admin", "QT", True), ("giamdoc", "GĐ", "director", "QT", True), ("sale1", "Tư vấn", "sale", "QT", False)]
CORE_STEPS = ["Lead về", "Gọi lần đầu", "Nói chuyện được", "Quan tâm"]


@pytest_asyncio.fixture(scope="module", autouse=True, loop_scope="session")
async def lines(standin: Any) -> AsyncIterator[None]:
    """Khai thêm module danh mục của gói thế vai cho riêng file này (gỡ cùng lúc với gói khi fixture ``standin`` trả sổ đăng ký)."""
    pack.register_lines()
    registry.validate()
    yield


@pytest.fixture
async def crm_only() -> tuple[Any, str]:
    d = f"c{uuid.uuid4().hex[:6]}.demo"
    return await seed.create_tenant(f"Trung tâm {d}", d, people=PEOPLE, packs=("crm",)), d


async def test_a_company_with_only_crm_runs_leads_without_any_industry_part(crm_only: tuple[Any, str]) -> None:
    tid, d = crm_only
    sale, gd = await seed.who(tid, "sale1", d), await seed.who(tid, "giamdoc", d)
    industry = {m for m, pk in registry.MODULE_PACK.items() if registry.pack_kind(pk) == "industry"}
    assert {pack.MODULE, pack.LINES_MODULE} <= industry
    assert not industry & {op.split(".")[0] for op in registry.OPS if executor.can_run(sale, op)}   # không thao tác nào của gói ngành chạy được
    for who_ in (sale, gd):
        for w in manifest(who_, "x")["workspaces"]:
            for sc in w["screens"]:
                r = await executor.query(who_, sc["query"], {})
                assert r.ok, (sc["query"], r.status, r.error)
        await desk.attention(who_)
    # không có danh mục (gói cắm ``crm.offering`` không bật ở công ty này) ⇒ giữ chữ người nhập, không đòi mã trong danh mục
    lead = await ok(sale, "crm.create_lead", name="Phụ huynh Lan", phone=fresh_phone(), offering_code="ielts", wishes="Cho con học cuối tuần",
                    budget_vnd=20_000_000)
    assert await owner_val("SELECT offering_code FROM crm.leads WHERE id = %s", (lead["lead_id"],)) == "IELTS"
    assert await q(sale, "crm.offering_options") == []
    page = await q(sale, "crm.journey", lead_id=lead["lead_id"])
    labels = [s["label"] for b in page["blocks"] if b["type"] == "steps" for s in b["steps"]]
    assert labels == CORE_STEPS                                                    # không mốc nào của gói ngành
    assert page["open"] == []
    text = str(page)
    assert "Cho con học cuối tuần" in text and "Đơn dịch vụ" not in text
    # chưa có danh mục: mỗi thứ ghi bằng chữ vẫn là một lead riêng, kể cả qua cửa nạp lead
    phone = fresh_phone()
    first = await ok(sale, "crm.create_lead", name="Phụ huynh Mai", phone=phone, offering_code="IELTS")
    second = await ok(sale, "crm.create_lead", name="Phụ huynh Mai", phone=phone, offering_code="TOAN")
    assert first["lead_id"] != second["lead_id"] and first["customer_id"] == second["customer_id"]
    assert (await executor.run(sale, "crm.create_lead", {"name": "Phụ huynh Mai", "phone": phone, "offering_code": "TOAN"})).status == "conflict"
    admin = await seed.who(tid, "admin", d)
    third = await ok(admin, "leadflow.intake", name="Phụ huynh Mai", phone=phone, offering_code="GIA-SU", source="event")
    assert third["duplicate"] is False
    await q(sale, "crm.brief")
    await q(sale, "crm.reminders")
    await q(sale, "crm.agenda", days=7)


async def test_an_industry_pack_plugs_in_by_viewer_rights_and_turning_it_off_leaves_crm(who: Who) -> None:
    sale, admin = await who("sale1"), await who("admin")
    lead = await ok(sale, "crm.create_lead", name=f"Khách cắm {uuid.uuid4().hex[:4]}", phone=fresh_phone(), offering_code="tư vấn")
    row = (await _lead_row(lead["lead_id"]))
    assert row["offering_code"] == "TV" and row["offering_id"] is not None        # tra qua gói: tên / bí danh ra đúng MÃ + mã định danh của danh mục
    assert {"value": "LD", "label": "Lắp đặt"} in await q(sale, "crm.offering_options")
    page = await q(sale, "crm.journey", lead_id=lead["lead_id"])
    steps = [s["label"] for b in page["blocks"] if b["type"] == "steps" for s in b["steps"]]
    assert steps == [*CORE_STEPS, "Mở đơn dịch vụ"]                                # mốc gói ngành cắm vào nối sau mốc chung
    assert any(o["query"] == "orderdesk.list" for o in page["open"])
    assert any(a["command"] == "orderdesk.create" for a in page["actions"])
    # mã không có trong danh mục ⇒ báo, không ghi chữ lạ — ở mọi đường nhập (tạo lead, nạp lead, luật chia)
    r = await executor.run(sale, "crm.create_lead", {"name": "Khách sai mã", "phone": fresh_phone(), "offering_code": "KHONGCO"})
    assert r.status == "not_found" and "KHONGCO" in (r.error or "")
    r = await executor.run(admin, "leadflow.intake", {"name": "Khách sai mã", "phone": fresh_phone(), "offering_code": "KHONGCO"})
    assert r.status == "not_found" and "KHONGCO" in (r.error or "")
    # vai không có quyền với tài nguyên của gói: mất phần gói góp (mốc, lối mở, nút, ô chọn), thẻ vẫn mở
    await approved(admin, "admin.set_grant", role="sale", resource="orderdesk", scope="own", actions=[])
    try:
        sale = await who("sale1")
        page = await q(sale, "crm.journey", lead_id=lead["lead_id"])
        assert [s["label"] for b in page["blocks"] if b["type"] == "steps" for s in b["steps"]] == CORE_STEPS
        assert not any(o["query"] == "orderdesk.list" for o in page["open"])
        assert not any(a["command"] == "orderdesk.create" for a in page["actions"])
        assert await q(sale, "crm.offering_options") == []
        again = await ok(sale, "crm.create_lead", name=f"Khách cắm {uuid.uuid4().hex[:4]}", phone=fresh_phone(), offering_code="LD")
        assert (await _lead_row(again["lead_id"]))["offering_code"] == "LD"        # tra MÃ là tra cứu hẹp bằng quyền hệ thống — không theo người xem
    finally:
        await approved(admin, "admin.set_grant", role="sale", resource="orderdesk", scope="own", actions=["read", "write"])
    # tắt gói ở một công ty khác: Khách hàng chạy tiếp như chưa từng có danh mục
    d = f"o{uuid.uuid4().hex[:6]}.demo"
    tid = await seed.create_tenant(f"Công ty bật rồi tắt {d}", d, people=PEOPLE, packs=(pack.PACK, "crm"))
    boss, rep = await seed.who(tid, "admin", d), await seed.who(tid, "sale1", d)
    assert (await executor.run(rep, "crm.create_lead", {"name": "Khách", "phone": fresh_phone(), "offering_code": "KHONGCO"})).status == "not_found"
    await ok(boss, "admin.set_pack", pack=pack.PACK, enabled=False)
    rep = await seed.who(tid, "sale1", d)
    free = await ok(rep, "crm.create_lead", name="Khách sau khi tắt gói", phone=fresh_phone(), offering_code="khongco")
    assert (await _lead_row(free["lead_id"]))["offering_code"] == "KHONGCO"
    page = await q(rep, "crm.journey", lead_id=free["lead_id"])
    assert [s["label"] for b in page["blocks"] if b["type"] == "steps" for s in b["steps"]] == CORE_STEPS and page["open"] == []


async def _lead_row(lead_id: Any) -> dict[str, Any]:
    async with db.owner.connection() as conn:
        row = await (await conn.execute("SELECT offering_code, offering_id FROM crm.leads WHERE id = %s", (lead_id,))).fetchone()
    assert row is not None
    return row


async def test_only_the_owning_module_may_ask_its_plug_point(who: Who) -> None:
    gd = await who("giamdoc")
    lead = await ok(await who("sale1"), "crm.create_lead", name=f"Khách điểm cắm {uuid.uuid4().hex[:4]}", phone=fresh_phone())
    async with db.tenant_tx(gd, rollback=True) as conn:
        with pytest.raises(Exception, match="module chủ"):
            await executor.core_ctx(conn, gd, module="mkt").plugs("crm.lead_story", lead_id=lead["lead_id"])
        with pytest.raises(Exception, match="module chủ"):
            await executor.core_ctx(conn, gd, module=pack.MODULE).plugs("crm.offering", system=True, code="TV")
        parts = await executor.core_ctx(conn, gd, module="crm").plugs("crm.lead_story", lead_id=lead["lead_id"])
        assert [p["open"][0]["query"] for p in parts] == ["orderdesk.list"]
        hits = await executor.core_ctx(conn, gd, module="crm").plugs("crm.offering", system=True, code="ld")
        assert [h["code"] for h in hits] == ["LD"]
    r = await executor.query(gd, f"{pack.LINES_MODULE}.plug_offering", {"code": "TV"})
    assert r.status == "not_found"                                                 # thao tác cắm là nội bộ — không gọi thẳng được


def test_crm_is_a_capability_and_packs_that_need_it_say_so() -> None:
    assert registry.pack_kind("crm") == "capability" and registry.pack_kind(pack.PACK) == "industry"
    assert "crm" in registry.PACKS["marketing"].requires
    assert registry.MODULE_PACK["crm"] == registry.MODULE_PACK["leadflow"] == "crm"
    assert registry.MODULE_PACK[pack.LINES_MODULE] == registry.MODULE_PACK[pack.MODULE] == pack.PACK
    assert {f"{pack.LINES_MODULE}.plug_offering"} <= set(registry.PLUGS["crm.offering"])
    assert pack.LINES_MODULE in registry.FAMILY_CALLERS["plug"] or registry.OPS[f"{pack.LINES_MODULE}.plug_offering"].family == "plug"
