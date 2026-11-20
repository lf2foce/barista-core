"""Gói LUÔN BẬT (0157): ``Pack(always_on=True)`` — mọi công ty đều có, như gói nền: không có dòng ``core.tenant_packs``, không tắt được, mặc định cài cùng
phần nền (công ty mới lẫn công ty có trước), gói khác ``requires`` nó thì bật thẳng. Lõi không biết gói nào luôn bật — chỉ đọc cờ trên khai báo (gói giả
ở đây chứng minh; gói thật dùng cờ này là ``schedule``)."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from barista import seed
from barista.config import settings
from barista.db import db
from barista.kernel import executor, packs, registry
from barista.kernel.registry import Module, Pack
from tests.conftest import ok, owner_val, q
from tests.test_dummy_pack import LIVE

ADMIN = [("admin", "Quản trị", "admin", "QT", True)]


async def _company(*pack_keys: str) -> Any:
    d = f"{uuid.uuid4().hex[:6]}.luonbat.test"
    tid = await seed.create_tenant(f"Công ty luôn bật {d}", d, packs=pack_keys, people=ADMIN, about="thử gói luôn bật")
    return tid, await seed.who(tid, "admin", d)


def test_schedule_declares_always_on_and_the_kernel_only_reads_the_flag() -> None:
    assert registry.always_on() == {"base", "schedule"} and registry.PACKS["schedule"].always_on
    assert registry.pack_closure({"crm"}) >= {"schedule"}                                       # crm vẫn khai requires — chỉ là luôn sẵn
    with pytest.raises(ValueError, match="luôn bật chỉ dành cho gói năng lực"):
        Pack("always_industry", "Gói ngành", "x", kind="industry", always_on=True)
    registry.PACKS.pop("always_industry", None)


@pytest.fixture
def always_pack() -> Iterator[None]:
    snap = [type(s)(s) for s in LIVE]
    Pack("tmp_always", "Gói luôn bật thử", "Gói giả luôn bật — thước đo lõi không nhắc tên gói", kind="capability", always_on=True,
         grants=(("admin", "tmp_always", "all", "read write manage"),))
    Module("tmp_always", "Thử luôn bật", pack="tmp_always", resources={"tmp_always": "Thử"})
    registry.validate()
    yield
    for live, old in zip(LIVE, snap, strict=True):
        live.clear()
        live.extend(old) if isinstance(live, list) else live.update(old)


def test_an_always_on_pack_must_only_require_always_on_packs(always_pack: None) -> None:
    Pack("tmp_always2", "Luôn bật cần gói thường", "x", kind="capability", always_on=True, requires=("finance",))
    with pytest.raises(RuntimeError, match="luôn bật nhưng requires gói không luôn bật"):
        registry.validate()


def test_an_always_on_pack_may_not_carry_money_or_authority_commands_or_own_subject_kinds(always_pack: None) -> None:
    from barista.kernel.executor import Ctx
    from barista.kernel.fields import Input, NoInput, field

    class Pay(Input):
        amount_vnd: int = field("Số tiền", kind="money")

    m = registry.Module("tmp_always_money", "Tiền luôn bật", pack="tmp_always")

    @m.command("pay", resource="tmp_always", title="Chi tiền")
    async def _pay(ctx: Ctx, inp: Pay) -> dict[str, Any]:
        return {}

    with pytest.raises(RuntimeError, match="luôn bật nhưng có lệnh rủi ro money"):
        registry.validate()
    registry.OPS.pop("tmp_always_money.pay")
    registry.validate()                                                                     # bỏ lệnh tiền ⇒ hợp lệ lại

    @m.subject("tmp_always_money.thing", "Đồ thử")
    async def _thing(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
        return {}

    with pytest.raises(RuntimeError, match=r"luôn bật nhưng sở hữu loại đối tượng tmp_always_money\.thing"):
        registry.validate()
    registry.SUBJECTS.pop("tmp_always_money.thing")
    registry.OPS.pop("tmp_always_money.subject_thing")


async def test_a_declared_always_on_pack_is_on_for_a_new_company_and_cannot_be_turned_off(always_pack: None) -> None:
    tid, admin = await _company("finance")
    assert await owner_val("SELECT count(*) FROM core.tenant_packs WHERE tenant_id = %s AND pack IN ('tmp_always', 'schedule')", (tid,)) == 0
    assert {"tmp_always", "schedule"} <= set(admin.packs or ())
    r = await executor.run(admin, "admin.set_pack", {"pack": "tmp_always", "enabled": False})
    assert r.status == "invalid" and "luôn bật cho mọi công ty — không tắt được" in r.error
    assert await owner_val("SELECT count(*) FROM core.grants WHERE tenant_id = %s AND resource = 'tmp_always'", (tid,)) == 1   # mặc định đã cài


async def test_a_new_company_has_schedule_on_with_its_defaults_and_no_pack_row() -> None:
    tid, admin = await _company("finance")                                                  # KHÔNG nêu schedule
    assert "schedule" in (admin.packs or ()) and registry.always_on() <= set(admin.packs or ())
    assert await owner_val("SELECT count(*) FROM core.tenant_packs WHERE tenant_id = %s AND pack = 'schedule'", (tid,)) == 0
    assert await owner_val("SELECT scope FROM core.grants WHERE tenant_id = %s AND role = 'admin' AND resource = 'schedule'", (tid,)) == "all"
    assert await owner_val("SELECT count(*) FROM core.pack_installs WHERE tenant_id = %s AND kind = 'grant' AND key = 'admin|schedule'", (tid,)) == 1
    assert (await executor.query(admin, "schedule.week", {})).status == "ok"                 # màn lịch chạy được ngay
    rows = {r["id"]: r for r in (await q(admin, "admin.packs"))["rows"]}
    assert rows["schedule"]["status"] == "always" and rows["finance"]["status"] == "on" and rows["training"]["status"] == "off"
    cols = (await q(admin, "admin.packs"))["columns"]
    assert next(c for c in cols if c["key"] == "status")["labels"]["always"] == "Luôn bật"
    store = await q(admin, "admin.pack_store")
    cards = {i["title"]: i for b in store["blocks"] if b["type"] == "tiles" for i in b["items"]}
    assert cards["Lịch & nguồn lực"]["badge"] == "Luôn bật"
    info = await q(admin, "admin.pack_info", pack="schedule")
    assert info["blocks"][0]["title"].startswith("Luôn bật")
    assert not any(a["command"] == "admin.set_pack" and not a["preset"]["enabled"] for a in info["actions"])   # không có nút Tắt


async def test_turning_an_always_on_pack_off_is_refused_with_a_clear_message_and_nothing_changes() -> None:
    tid, admin = await _company("finance")
    r = await executor.run(admin, "admin.set_pack", {"pack": "schedule", "enabled": False})
    assert r.status == "invalid" and r.error == "Gói “Lịch & nguồn lực” luôn bật cho mọi công ty — không tắt được", r.error
    assert "schedule" in ((await seed.who(tid, "admin", admin.email.split("@")[1])).packs or ())
    assert (await executor.query(admin, "schedule.week", {})).status == "ok"
    out = await ok(admin, "admin.set_pack", pack="schedule", enabled=True)                    # "bật" = chỉ cài phần mặc định còn thiếu, không thêm dòng
    assert out["enabled"] is True and "luôn bật" in out["summary"]
    assert await owner_val("SELECT count(*) FROM core.tenant_packs WHERE tenant_id = %s AND pack = 'schedule'", (tid,)) == 0


async def test_a_pack_that_requires_it_can_be_enabled_directly_and_never_says_need_schedule() -> None:
    tid, admin = await _company("directory")                                                 # có directory, chưa có crm, không nêu schedule
    store = await q(admin, "admin.pack_store")
    cards = {i["title"]: i for b in store["blocks"] if b["type"] == "tiles" for i in b["items"]}
    assert cards["Khách hàng"]["badge"] == "Có sẵn", cards["Khách hàng"]
    info = await q(admin, "admin.pack_info", pack="crm")
    assert "Cần bật trước" not in info["blocks"][0]["text"] or "Lịch" not in info["blocks"][0]["text"], info["blocks"][0]["text"]
    await ok(admin, "admin.set_pack", pack="crm", enabled=True)
    assert "crm" in ((await seed.who(tid, "admin", admin.email.split("@")[1])).packs or ())
    r = await executor.run(admin, "admin.set_pack", {"pack": "schedule", "enabled": False})   # crm cần nó, nhưng chặn đã ở "luôn bật"
    assert r.status == "invalid" and "luôn bật" in r.error


async def test_barista_packs_filter_never_hides_an_always_on_pack() -> None:
    tid, admin = await _company("finance")
    old = settings.packs
    settings.packs = "finance"                                                              # bản cài chỉ mặc định Tài chính
    try:
        assert registry.always_on() <= packs.defaults() and "schedule" in packs.defaults() and "training" not in packs.defaults()
        packs.invalidate(tid)
        async with db.owner.connection() as conn:
            assert registry.always_on() <= await packs.entitled(conn, tid)                   # có quyền dùng dù BARISTA_PACKS không nêu
        again = await seed.who(tid, "admin", admin.email.split("@")[1])
        assert "schedule" in (again.packs or ())
        store = await q(again, "admin.pack_store")
        cards = {i["title"]: i for b in store["blocks"] if b["type"] == "tiles" for i in b["items"]}
        assert cards["Lịch & nguồn lực"]["badge"] == "Luôn bật" and cards["Đào tạo nội bộ"]["badge"] == "Liên hệ để mở"
    finally:
        settings.packs = old


async def test_the_core_files_name_no_always_on_pack() -> None:
    from pathlib import Path

    src = Path(registry.__file__).parent
    hits = [f.name for f in [*src.glob("*.py"), src.parent / "presets.py", src.parent / "workspaces.py"] if "schedule" in f.read_text().lower()]
    assert hits == [], hits                                                                  # lõi chỉ đọc cờ, không nhắc tên gói
