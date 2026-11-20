"""Tự động hoá "Nếu… thì…" (0090): luật là dữ liệu trên CÒ module khai; chạy bằng quyền người đặt luật, mỗi sự kiện một lần, có trần / ngày,
có nhật ký + mã vết; cò chỉ hiện theo gói đang bật; chỉ người quản lý luật mới đặt được."""

import uuid
from decimal import Decimal
from typing import Any

import pytest

from barista import seed
from barista.kernel import events, executor
from barista.kernel.errors import Invalid
from barista.modules.flow import automations as auto
from tests.conftest import Who, fresh_phone, ok, owner_val, q

FIELDS = {"source": "Nguồn", "project_code": "Dự án", "price_vnd": "Giá bán (đồng)", "was_signed": "Đã ký trước đó"}


def test_conditions_and_messages_are_parsed_and_checked_by_code() -> None:
    conds = auto.parse_conditions("Dự án = HSR\nGiá bán (đồng) >= 5.000.000.000\nsource chứa ads", FIELDS)
    assert conds == [{"field": "project_code", "op": "eq", "value": "HSR"}, {"field": "price_vnd", "op": "ge", "value": "5.000.000.000"},
                     {"field": "source", "op": "contains", "value": "ads"}]
    assert auto.matches(conds, {"project_code": "hsr", "price_vnd": 6_000_000_000, "source": "ads_meta"})
    assert not auto.matches(conds, {"project_code": "HSR", "price_vnd": 4_000_000_000, "source": "ads"})
    assert not auto.matches(conds, {"project_code": "HSR", "source": "ads"})                      # thiếu trường ⇒ sai, không đoán
    assert auto.matches(auto.parse_conditions("Đã ký trước đó = true", FIELDS), {"was_signed": True})
    assert auto.matches(auto.parse_conditions("Dự án ≠ DTP", FIELDS), {"project_code": "HSR"})
    for bad, msg in (("Khách = Lan", "không có trường"), ("Dự án HSR", "thiếu phép so")):
        with pytest.raises(Invalid, match=msg):
            auto.parse_conditions(bad, FIELDS)
    assert auto.render("Ký {Dự án} giá {price_vnd} đ, {lạ}", {"project_code": "HSR", "price_vnd": Decimal("5200000000")}, FIELDS) \
        == "Ký HSR giá 5.200.000.000 đ, {lạ}"


async def _notes(user_id: Any, title: str) -> list[dict[str, Any]]:
    return await owner_val("SELECT coalesce(json_agg(json_build_object('body', body, 'open', open)), '[]') FROM core.notifications "
                           "WHERE user_id = %s AND title = %s", (user_id, title))


async def test_only_rule_managers_set_rules_and_triggers_follow_enabled_packs(who: Who) -> None:
    sale1 = await who("sale1")
    r = await executor.run(sale1, "flow.save_automation", {"name": "x", "trigger": "crm.lead_created", "action": "notify", "team_code": "KD-A",
                                                          "message": "x"})
    assert r.status == "denied"
    admin = await who("admin")
    r = await executor.run(admin, "flow.save_automation", {"name": "x", "trigger": "crm.lead_created", "action": "notify", "team_code": "KD-A",
                                                          "message": "Khách {Tên khách}"})
    assert r.status == "invalid" and "Tên khách" in r.error                                          # chỉ trường cò khai (không tên khách)
    assert (await executor.run(admin.as_agent(), "flow.save_automation", {"name": "x", "trigger": "crm.lead_created", "action": "notify",
                                                                         "team_code": "KD-A", "message": "x"})).status == "proposed"
    d = f"{uuid.uuid4().hex[:6]}.auto.test"
    tid = await seed.create_tenant("Công ty chỉ gói nền (test)", d, packs=(), people=[("qt", "Quản trị", "admin", "QT", True)])
    opts = {o["value"] for o in await q(await seed.who(tid, "qt", d), "flow.trigger_options")}
    assert "flow.plan_closed" in opts and "crm.lead_created" not in opts                            # gói Bán hàng tắt ⇒ không có cò của nó


async def test_new_rules_run_in_trial_first_recording_what_they_would_do(who: Who) -> None:
    """Luật mới CHẠY THỬ: khớp ⇒ chỉ ghi “sẽ báo ai: nội dung”, không gửi; bấm “Chạy thật” mới gửi (review 05/11: bật lên không bắn ngay)."""
    admin, sale1, truonga = await who("admin"), await who("sale1"), await who("truonga")
    name = f"Thử luật {uuid.uuid4().hex[:4]}"
    await events.drain()
    rule = await ok(admin, "flow.save_automation", name=name, trigger="crm.lead_created", conditions="Sản phẩm quan tâm = DTP", action="notify",
                    team_code="KD-A", leads_only=True, message="Lead {Sản phẩm quan tâm} mới")
    assert "CHẠY THỬ" in rule["summary"]
    await ok(sale1, "crm.create_lead", name="Khách chạy thử", phone=fresh_phone(), offering_code="DTP")
    await events.drain()
    assert await _notes(truonga.user_id, name) == []                                                # chưa gửi gì
    note = await owner_val("SELECT note FROM flow.automation_runs WHERE automation_id = %s AND status = 'trial'", (rule["automation_id"],))
    assert note == "Sẽ báo trưởng Team Kinh doanh A: “Lead DTP mới”"
    page = await q(admin, "flow.automation", automation_id=str(rule["automation_id"]))
    assert page["subtitle"] == "Chạy thử" and page["actions"][0]["title"] == "Chạy thật"
    await ok(admin, "flow.set_automation", automation_id=str(rule["automation_id"]), trial=False)
    await ok(sale1, "crm.create_lead", name="Khách chạy thật", phone=fresh_phone(), offering_code="DTP")
    await events.drain()
    assert [n["body"] for n in await _notes(truonga.user_id, name)] == ["Lead DTP mới"]


async def test_unknown_trigger_names_the_ones_that_exist(who: Who) -> None:
    """Đoán sai tên cò ⇒ lỗi kể luôn cò dùng được (mã + nhãn + trường) — sửa trong một bước, không đoán lại (eval 0095)."""
    admin = await who("admin")
    r = await executor.run(admin, "flow.save_automation", {"name": "Thử cò sai", "trigger": "lead.new", "action": "notify",
                                                          "message": "Có lead mới", "user_email": admin.email})
    assert r.status == "invalid" and "lead.new" in (r.error or "") and "crm.lead_created" in (r.error or "")
