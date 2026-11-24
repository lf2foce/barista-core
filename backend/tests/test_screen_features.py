"""NGHIỆP VỤ CÔNG TY CÓ DÙNG (api 1.63): màn khai ``Screen(feature=<cài đặt công ty>)`` chỉ lên menu khi công ty BẬT công tắc đó — một ô tick cho cả công
ty, khác quyền theo vai. Mặc định bật (công ty chưa đổi gì thấy như trước); tắt ⇒ không vai nào có màn, quyền + dữ liệu giữ nguyên; bật lại ⇒ có lại."""

import argparse
import uuid
from typing import Any
from uuid import UUID

import pytest

from barista import operator, seed, workspaces
from barista.agent import chat
from barista.kernel import company_settings, executor, screens
from barista.kernel.principal import Principal, system_principal
from tests.conftest import owner_val

PEOPLE = [("giamdoc", "Giám đốc thử", "director", "HQ", True), ("admin", "Quản trị thử", "admin", "QT", True),
          ("ketoan", "Kế toán thử", "accountant", "HQ", False), ("mkt", "Marketing thử", "marketer", "HQ", False)]
OP = {"by": "Người vận hành thử", "why": "công ty không làm nghiệp vụ này (test)"}
FEATURES = {"acct.use_remittances": {"remittances"}, "acct.use_cost_objects": {"cost_objects"},
            "mkt.use_cost_share": {"cost_shares", "cost_share_statement"},
            "mkt.use_ads": {"ads", "creatives", "mgmt_creatives", "ad_changes", "ad_conversions"}}


@pytest.fixture
async def co() -> dict[str, Any]:
    d = f"ft{uuid.uuid4().hex[:8]}.demo"
    tid = await seed.create_tenant(f"Công ty công tắc nghiệp vụ {d}", d, people=PEOPLE, packs=("finance", "marketing", "mgmt"), about="công ty thử (test)")
    return {"tid": tid, "d": d}


async def _menu(co: dict[str, Any], local: str) -> set[str]:
    p: Principal = await seed.who(co["tid"], local, co["d"])   # nạp lại: cài đặt công ty đi cùng quyền ở mỗi request
    man = workspaces.manifest(p, "Công ty thử")
    return {s["key"] for w in man["workspaces"] for s in w["screens"]} | {s["key"] for s in man["desk"]}


def test_every_feature_names_a_declared_setting_and_the_expected_screens_carry_it() -> None:
    tagged: dict[str, set[str]] = {}
    for w in workspaces.workspaces():
        for s in w.screens:
            if s.feature:
                assert s.feature in company_settings.SETTINGS, f"màn {s.key}: công tắc {s.feature} chưa khai (Module.setting)"
                tagged.setdefault(s.feature, set()).add(s.key)
    for key, keys in FEATURES.items():
        assert company_settings.SETTINGS[key].default is True, f"{key}: mặc định phải BẬT — công ty có sẵn không mất màn khi nâng bản"
        assert tagged.get(key) == keys, key


async def test_switching_a_feature_off_hides_its_screens_from_every_role_and_on_brings_them_back(co: dict[str, Any]) -> None:
    tid: UUID = co["tid"]
    every = set().union(*FEATURES.values())
    seen = await _menu(co, "giamdoc") | await _menu(co, "ketoan") | await _menu(co, "mkt")
    assert every <= seen, f"mặc định bật — thiếu {sorted(every - seen)}"
    kept = {who: await _menu(co, who) - every for who in ("giamdoc", "ketoan", "mkt")}
    grants = await owner_val("SELECT count(*) FROM core.grants WHERE tenant_id = %s", (tid,))
    for key in FEATURES:
        r = await executor.run(system_principal(tid), "admin.operator_set_setting", {**OP, "key": key, "enabled": False})
        assert r.status == "ok", r.error
    for who in ("giamdoc", "ketoan", "mkt", "admin"):
        menu = await _menu(co, who)
        assert not (menu & every), f"{who} còn thấy {sorted(menu & every)}"
        if who in kept:
            assert menu == kept[who], f"{who}: tắt nghiệp vụ không được làm mất màn khác"
    assert await owner_val("SELECT count(*) FROM core.grants WHERE tenant_id = %s", (tid,)) == grants   # quyền không đổi — đây không phải thu hồi quyền
    kt = await seed.who(tid, "ketoan", co["d"])
    assert (await executor.query(kt, "acct.remittances", {})).status != "ok" and not executor.can_run(kt, "acct.remittances")   # tắt là tắt: gọi thẳng cũng không
    assert (await executor.query(kt, "acct.payouts", {})).status == "ok"                                                          # việc khác không ảnh hưởng
    assert (await executor.query(system_principal(tid), "acct.remittances", {})).status == "ok"                                   # hệ thống (gọi lồng) không vướng
    await executor.run(system_principal(tid), "admin.operator_set_setting", {**OP, "key": "acct.use_remittances", "enabled": True})
    assert "remittances" in await _menu(co, "ketoan") and "cost_objects" not in await _menu(co, "ketoan")


async def test_the_admin_ticks_the_same_switch_in_company_settings(co: dict[str, Any]) -> None:
    admin = await seed.who(co["tid"], "admin", co["d"])
    rows = {r["id"]: r for r in (await executor.query(admin, "admin.settings", {})).output["rows"]}
    assert set(FEATURES) <= set(rows) and all(rows[k]["state"] == "on" for k in FEATURES)
    assert (await executor.run(admin, "admin.set_setting", {"key": "mkt.use_cost_share", "enabled": False})).status == "ok"
    assert not ({"cost_shares", "cost_share_statement"} & await _menu(co, "mkt")) and "campaigns" in await _menu(co, "mkt")


async def test_the_operator_door_needs_a_name_and_a_reason_and_knows_only_declared_settings(co: dict[str, Any]) -> None:
    tid: UUID = co["tid"]
    assert (await executor.run(system_principal(tid), "admin.operator_set_setting", {"key": "acct.use_remittances", "enabled": False})).status != "ok"
    assert (await executor.run(system_principal(tid), "admin.operator_set_setting", {**OP, "key": "acct.khong_co", "enabled": False})).status == "not_found"
    a: argparse.Namespace = operator.parser().parse_args(["--tenant", "x", "set-setting", "acct.use_remittances", "off", "--by", "Anh", "--why", "thử"])
    assert (a.cmd, a.key, a.state) == ("set-setting", "acct.use_remittances", "off")
    with pytest.raises(SystemExit):
        await operator.main(operator.parser().parse_args(["--tenant", "x", "set-setting", "acct.use_remittances", "off", "--why", "x"]))


def test_a_principal_without_loaded_settings_falls_back_to_the_declared_default() -> None:
    assert company_settings.on(None, "acct.use_remittances") is True
    assert company_settings.on({"acct.use_remittances": False}, "acct.use_remittances") is False
    with pytest.raises(KeyError):
        company_settings.on({}, "acct.chua_khai")


async def test_a_switched_off_feature_is_off_for_the_assistant_too(co: dict[str, Any]) -> None:
    """Tắt nghiệp vụ ⇒ thao tác CHỈ thuộc màn của nó không còn là công cụ của trợ lý / MCP (cùng ``chat.catalog``); thao tác dùng chung vẫn còn."""
    ops = screens.feature_ops()
    assert {"acct.remittances", "acct.confirm_remittance", "mkt.cost_shares", "mkt.set_cost_share", "mkt.cost_share_statement"} <= set(ops)
    assert ops["mkt.set_cost_share"] == "mkt.use_cost_share" and "mkt.campaigns" not in ops and "mkt.record_spend" not in ops
    tid: UUID = co["tid"]

    async def tools(local: str) -> set[str]:
        return {op.name for op, _ in await chat.catalog(await seed.who(tid, local, co["d"]))}
    before = await tools("ketoan") | await tools("mkt")
    assert {"acct.remittances", "mkt.cost_shares", "mkt.cost_share_statement"} <= before
    for key in ("acct.use_remittances", "mkt.use_cost_share"):
        assert (await executor.run(system_principal(tid), "admin.operator_set_setting", {**OP, "key": key, "enabled": False})).status == "ok"
    after = await tools("ketoan") | await tools("mkt")
    gone = {n for n, k in ops.items() if k in ("acct.use_remittances", "mkt.use_cost_share")}
    assert not (after & gone), sorted(after & gone)
    assert before - after <= gone and {"mkt.campaigns", "acct.payouts"} <= after


async def test_personal_screens_live_in_the_account_menu_and_rules_live_in_admin(co: dict[str, Any]) -> None:
    """19/11/2026: Trí nhớ của trợ lý là màn CÁ NHÂN — manifest gửi ở ``account`` (menu tài khoản), không ở menu phân hệ; "Luật tự động" (khoá
    ``automations``) nằm ở Quản trị, không ở Quy trình & kế hoạch."""
    for who in ("giamdoc", "ketoan", "admin"):
        man = workspaces.manifest(await seed.who(co["tid"], who, co["d"]), "Công ty thử")
        # menu tài khoản: Kết nối & kỹ năng (ADR 0261) + Trí nhớ của trợ lý — đều là màn CÁ NHÂN
        assert [s["key"] for s in man["account"]] == ["plugins", "memory"] and man["account"][1]["title"] == "Trí nhớ của trợ lý"
        assert "account" not in {w["key"] for w in man["workspaces"]}
        assert "memory" not in {s["key"] for w in man["workspaces"] for s in w["screens"]}
    man = workspaces.manifest(await seed.who(co["tid"], "admin", co["d"]), "Công ty thử")
    place = {s["key"]: (w["key"], s["title"]) for w in man["workspaces"] for s in w["screens"]}
    assert place["automations"] == ("admin", "Luật tự động")
