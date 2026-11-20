"""Công cụ mỗi lượt — "tìm trước" (0095): phần cố định của vai nhỏ + đúng quyền; code tìm theo câu hỏi mang công cụ cần ngay lượt gọi model ĐẦU
(không thêm lượt); lưới an toàn ``core.open_tools(query)`` chỉ tìm trong danh mục người hỏi, tối đa 2 lần / lượt, mô tả cố định; thứ tự gửi giữ
bộ đệm prompt; "all" vẫn mang mọi thao tác như trước."""

import json
from typing import Any

import pytest

from barista.agent import chat, toolset
from barista.config import settings
from barista.kernel import registry
from tests.conftest import Who
from tests.test_assistant import _turn, call
from tests.toolset_bench import role_tools


@pytest.fixture(autouse=True)
def retrieve_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_tools", "retrieve")


async def _plan(p: Any, question: str = "", k: int = 0) -> tuple[toolset.Plan, set[str]]:
    ops = await chat.catalog(p.as_agent())
    have = {op.name for op, _ in ops}
    return toolset.plan(p.role, have, question=question, retrieve=k), have


async def _noop(_: dict[str, Any]) -> None:
    return None


@pytest.mark.parametrize("role", ["sale1", "truonga", "giamdoc", "ketoan", "mkt", "admin"])
async def test_fixed_part_is_small_and_within_the_persons_rights(who: Who, role: str) -> None:
    p = await who(role)
    plan, have = await _plan(p)
    core = {n for n in have if registry.tool_kind(registry.OPS[n]) == "core"}
    assert plan.visible <= have and core <= plan.visible
    assert len(plan.visible - core) <= settings.agent_tool_budget
    assert not any(registry.tool_kind(registry.OPS[n]) == "setup" for n in plan.visible)   # thiết lập vẫn sau core.open_setup
    assert plan.rest <= have and not plan.rest & plan.visible


async def test_retrieval_brings_the_tool_the_question_needs_on_the_first_model_call(who: Who) -> None:
    boss = await who("giamdoc")   # 19/11/2026: màn Luật tự động vào Quản trị ⇒ quản trị có sẵn công cụ này ở phần cố định; giám đốc thì không
    q = "Đặt luật: khi có lead mới dự án HSR thì báo trưởng team A."
    plan, have = await _plan(boss)
    assert "flow.save_automation" in have and "flow.save_automation" not in plan.visible   # được dùng nhưng phần cố định của giám đốc không có
    _, _, model = await _turn(boss, q, ["Vâng ạ."])
    assert "flow__save_automation" in model.tools_seen[0]                     # có ngay từ lượt gọi model ĐẦU — không cần open_tools
    assert len(model.tools_seen[0]) < len(have)


def test_multi_part_question_retrieves_for_each_part() -> None:
    names = [n for n, op in registry.OPS.items() if not op.internal and registry.tool_kind(op) != "setup"]
    got = toolset.retrieve_for("Tuần sau đào tạo sản phẩm cho team A, và từ giờ có lead mới thì báo trưởng team A.", names, 15)
    assert "flow.save_automation" in got and len(got) <= 15


async def test_safety_net_finds_within_the_catalog_only_and_at_most_twice(who: Who) -> None:
    mkt = await who("mkt")
    plan, have = await _plan(mkt)
    toolset.open_search(plan, "hoa hồng của sale")
    assert plan.visible <= have and "commission.wallets" not in plan.visible    # marketing không có quyền hoa hồng ⇒ tìm cũng không thấy
    toolset.open_search(plan, "tài liệu dự án")
    assert toolset.open_search(plan, "báo cáo")["status"] == "error"            # lần thứ ba trong lượt ⇒ từ chối, không đi vòng


async def test_tool_found_mid_turn_is_usable_on_the_next_step(who: Who) -> None:
    ketoan = await who("ketoan")
    plan, _ = await _plan(ketoan, "Chào em")
    target = next(n for n in sorted(plan.rest) if registry.OPS[n].kind == "query" and n in toolset.search(registry.OPS[n].title, plan.rest, 6))
    async with role_tools(await who("admin"), "accountant", None):   # vai tự tuyển (chưa đặt bộ công cụ — 0218): lưới an toàn là open_tools
        out, _, model = await _turn(ketoan, "Chào em", [[call("core.open_tools", query=registry.OPS[target].title)], [call(target)], "Xong ạ."])
    assert chat.tool_name(target) not in model.tools_seen[0] and "core__open_tools" in model.tools_seen[0]
    assert chat.tool_name(target) in model.tools_seen[1]
    assert [s["tool"] for s in out["steps"]][:2] == ["core.open_tools", target]


async def test_tools_are_sent_fixed_first_then_open_tools_then_question_specific(who: Who) -> None:
    sale1 = await who("sale1")
    seen = []
    for q in ("Xuất dữ liệu MISA tháng này", "Hồ Sen còn căn 2PN nào?"):
        run = chat.ToolRun(sale1, _noop, "t", question=q)
        tools = run.tools(await chat.catalog(sale1))
        assert run.plan is not None
        found = {chat.tool_name(n) for n in run.plan.retrieved}
        sent = {chat.tool_name(n) for n in run.plan.visible} | {"core__open_tools"}   # công cụ thật sự gửi đi; phần chưa gửi / thiết lập ẩn xếp sau cùng
        names = [t.name for t in tools if t.name in sent]
        flags = [n in found for n in names]
        assert flags == sorted(flags)                                         # mọi công cụ cố định đứng trước mọi công cụ tìm được
        assert names[flags.index(True) - 1 if True in flags else -1] == "core__open_tools"   # open_tools đứng cuối phần cố định
        t = next(x for x in tools if x.name == "core__open_tools")
        seen.append((t.description, json.dumps(t.function_schema.json_schema, sort_keys=True)))
    assert seen[0] == seen[1]                                                 # mô tả open_tools không đổi theo câu hỏi (bộ đệm prompt)


def test_recent_modules_put_the_confirmed_card_first_and_skip_core_tools() -> None:
    turns = [{"steps": [{"tool": "crm.leads"}, {"tool": "core.find"}]}, {"steps": [{"tool": "acct.receivables"}]}]
    assert chat.recent_modules(turns) == ("acct", "crm")
    assert chat.recent_modules(turns, first=["flow.save_automation"]) == ("flow", "acct")


async def test_all_mode_keeps_every_tool_as_before(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_tools", "all")
    sale1 = await who("sale1")
    _, _, model = await _turn(sale1, "Chào", ["Chào anh."])
    every = {chat.tool_name(op.name) for op, _ in await chat.catalog(sale1) if registry.tool_kind(op) not in ("setup", "builder")}   # builder: chỉ ở trình tạo app (0098)
    assert every <= model.tools_seen[0] and "core__open_tools" not in model.tools_seen[0]
