"""Bộ công cụ trợ lý theo VAI (ADR 0218): gói mang bộ mặc định, công ty sửa trong app; bộ không bao giờ thêm quyền; phần cố định của vai gửi trước
và như nhau mọi lượt (bộ đệm prompt); nhóm ngoài bộ luôn có trong danh mục ngắn để mở (không ngõ cụt), mở lại kiểm quyền; cuộc trò chuyện giữ nhóm
vừa mở. Model giả theo kịch bản; số token đo bằng ``tests/toolset_bench.py`` (đặt ``BARISTA_BENCH_OUT`` để ghi bảng đủ bộ đề)."""

import json
import os
from pathlib import Path
from typing import Any

import pytest

from barista import presets, seed
from barista.agent import chat, toolset
from barista.config import settings
from barista.kernel import ai_tools, executor, registry
from barista.kernel.principal import system_principal
from tests import toolset_bench as tb
from tests.conftest import Who
from tests.test_assistant import _turn, call
from tests.toolset_bench import role_tools


@pytest.fixture(autouse=True)
def retrieve_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agent_tools", "retrieve")


async def _run(p: Any, q: str = "Chào em") -> chat.ToolRun:
    async def emit(_: dict[str, Any]) -> None:
        return None

    run = chat.ToolRun(p, emit, "t", question=q)
    await run.load_skills(p)
    run.tools(await chat.catalog(p))
    return run


async def test_pack_defaults_are_installed_per_role(who: Who) -> None:
    admin = await who("admin")
    rows = {r["role"]: r for r in (await executor.query(admin, "core.role_tools", {})).output["rows"]}
    sale = set(rows["sale"]["tools_in"])
    assert {"crm.leads", "leadflow.mine", "kpi.mine", "training.my"} <= sale                      # vai của crm + phần gói kpi / đào tạo góp
    director = set(rows["director"]["tools_in"])
    assert {"health"} <= set(rows["director"]["groups_in"]) and {"acct.cashflow", "mkt.overview", "leadflow.overview", "kpi.ranking"} <= director
    assert rows["coordinator"]["groups_in"] == ["leadflow"]
    items = presets.plan({"crm", "kpi"}, {"base", "crm", "kpi", "directory", "schedule"}, {"sale", "director"})
    keys = {i.key for i in items if i.kind == "record" and i.data["command"] == presets.ROLE_TOOLS}
    assert {"core.default_role_tools:sale:crm", "core.default_role_tools:sale:kpi", "core.default_role_tools:director:crm"} <= keys


async def test_install_adds_only_once_and_keeps_the_companys_edit(who: Who) -> None:
    admin = await who("admin")
    async with role_tools(admin, "sale", ["crm.leads"]):
        await seed.run(system_principal(admin.tenant_id), "admin.install_defaults", packs=["base", "crm", "kpi", "training"])   # nâng cấp: đã cài ⇒ không thêm lại
        rows = {r["role"]: r for r in (await executor.query(admin, "core.role_tools", {})).output["rows"]}
        assert rows["sale"]["tools_in"] == ["crm.leads"]


async def test_a_toolset_never_adds_rights(who: Who) -> None:
    admin, mkt = await who("admin"), await who("mkt")
    have = {chat.tool_name(op.name) for op, _ in await chat.catalog(mkt)}
    async with role_tools(admin, "marketer", ["acct", "crm.leads", "mkt.overview"]):
        _, _, model = await _turn(mkt, "Khoản phải thu nào quá hạn?", ["Dạ."])
        assert model.tools_seen[0] - {"core__open_group", "core__use_skill", "core__open_setup"} <= have
        assert not any(t.startswith(("acct__", "crm__")) for t in model.tools_seen[0])
        run = await _run(mkt)
        assert run.plan is not None and "acct" not in run.plan.index and "crm" not in run.plan.index   # danh mục chỉ có nhóm người hỏi dùng được
        res = toolset.open_group(run.plan, ["acct"], lambda n: True)
        assert res["status"] == "error" and not any(registry.OPS[n].module == "acct" for n in run.plan.visible)


async def test_stable_part_first_and_identical_across_questions(who: Who) -> None:
    sale = await who("sale1")
    seen = []
    for q in ("Xuất dữ liệu MISA tháng này", "Hôm nay em nên gọi lead nào trước?", "Có ai nhắn gì cho em không?"):
        run = await _run(sale, q)
        assert run.plan is not None and run.plan.stable is not None
        tools = run.tools(await chat.catalog(sale))
        names = [t.name for t in tools if t.name != "core__open_setup"]
        fixed = {chat.tool_name(n) for n in run.plan.stable} | {"core__use_skill"}
        cut = names.index("core__open_group")
        assert set(names[:cut]) <= fixed and not set(names[cut + 1:]) & fixed        # cố định · open_group · phần theo câu hỏi
        g = next(t for t in tools if t.name == "core__open_group")
        seen.append((names[:cut + 1], g.description, json.dumps(g.function_schema.json_schema, sort_keys=True)))
        assert len(run.plan.retrieved) <= settings.agent_tool_k_toolset
    assert seen[0] == seen[1] == seen[2]                                           # phần đầu giống hệt ⇒ đọc bộ đệm


async def test_open_group_brings_the_group_next_step_and_the_thread_keeps_it(who: Who) -> None:
    sale = await who("sale1")
    run = await _run(sale, "Chào em")
    assert run.plan is not None and "talk.mine" not in run.plan.visible and "talk" in run.plan.index
    out, _, model = await _turn(sale, "Chào em", [[call("core.open_group", groups=["talk"])], [call("talk.mine")], "Dạ."])
    assert "talk__mine" not in model.tools_seen[0] and "talk__mine" in model.tools_seen[1]
    assert out["steps"][0]["tool"] == "core.open_group" and out["steps"][0]["groups"] == ["talk"]
    _, _, again = await _turn(sale, "Còn gì nữa không?", ["Dạ hết rồi ạ."], thread_id=out["thread_id"])
    assert "talk__mine" in again.tools_seen[0]                                     # nhóm vừa mở / vừa dùng còn ở lượt sau


async def test_open_group_is_capped_per_turn_and_big_groups_open_by_keyword(who: Who) -> None:
    ketoan = await who("ketoan")
    run = await _run(ketoan, "Chào em")
    assert run.plan is not None
    big = sum(1 for n in run.plan.rest if registry.OPS[n].module == "acct")
    assert big > toolset.GROUP_TOOLS_MAX
    res = toolset.open_group(run.plan, ["acct"], lambda n: True, query="hoá đơn GTGT")
    assert res["status"] == "ok" and "acct.vat_invoices" in run.plan.visible and "Nhóm còn" in str(res["message"])
    stable = run.plan.stable or set()
    assert sum(1 for n in run.plan.visible if registry.OPS[n].module == "acct" and n not in stable) <= toolset.GROUP_TOOLS_MAX
    toolset.open_group(run.plan, ["talk"], lambda n: True)
    assert toolset.open_group(run.plan, ["kb"], lambda n: True)["status"] == "error"   # lần thứ ba trong lượt ⇒ từ chối


async def test_open_group_rechecks_rights_at_open(who: Who) -> None:
    sale = await who("sale1")
    run = await _run(sale)
    assert run.plan is not None
    toolset.open_group(run.plan, ["talk"], lambda n: n != "talk.mine")              # quyền đổi giữa lượt ⇒ công cụ không còn quyền không được mở
    assert "talk.mine" not in run.plan.visible and "talk.room" in run.plan.visible


async def test_role_without_toolset_keeps_the_automatic_choice(who: Who) -> None:
    admin, sale = await who("admin"), await who("sale1")
    async with role_tools(admin, "sale", None):
        run = await _run(sale, "Hồ Sen còn căn nào?")
        assert run.plan is not None and run.plan.stable is None
        _, _, model = await _turn(sale, "Chào", ["Chào anh."])
        assert "core__open_tools" in model.tools_seen[0] and "core__open_group" not in model.tools_seen[0]


async def test_only_toolset_admins_change_it_and_never_the_assistant(who: Who) -> None:
    admin, sale, giamdoc = await who("admin"), await who("sale1"), await who("giamdoc")
    assert (await executor.run(sale, "core.set_role_tools", {"role": "sale", "groups": ["crm"]})).status == "denied"
    assert (await executor.run(admin.as_agent(), "core.set_role_tools", {"role": "sale", "groups": ["crm"]})).status == "denied"
    assert (await executor.run(admin, "core.set_role_tools", {"role": "sale", "groups": ["khong_co"]})).status == "invalid"
    assert (await executor.run(admin, "core.set_role_tools", {"role": "sale", "tools": ["core.install_skill"]})).status == "invalid"   # nội bộ
    async with role_tools(giamdoc, "sale", ["crm", "kpi.mine"]):
        rows = {r["role"]: r for r in (await executor.query(giamdoc, "core.role_tools", {})).output["rows"]}
        assert rows["sale"]["groups_in"] == ["crm"] and rows["sale"]["count"] == len(ai_tools.expand(["crm", "kpi.mine"]))
    assert "core.set_role_tools" not in {op.name for op, _ in await chat.catalog(admin)}  # trợ lý không có công cụ tự sửa bộ của mình


def test_pack_declarations_name_only_their_own_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    assert registry._tool_entries_bad() == []
    monkeypatch.setitem(registry.PACKS["kpi"].tools, "sale", ("acct.cashflow", "khong_co"))
    bad = registry._tool_entries_bad()
    assert any("acct.cashflow thuộc gói finance" in b for b in bad) and any("khong_co không có" in b for b in bad)


async def test_bench_every_question_is_reachable_and_the_stable_part_is_small(who: Who) -> None:
    """Cổng đo gọn (một câu trong bộ + một câu ngoài bộ mỗi vai): công cụ đúng có ngay hoặc mở được bằng MỘT lần ``open_group``; phần công cụ gửi mỗi
    lần gọi model dưới trần. Đủ bộ đề + bảng số: ``BARISTA_BENCH_OUT=<file.json>``."""
    full = bool(os.environ.get("BARISTA_BENCH_OUT"))
    tb.SEEN.clear()
    picks = tb.CASES + tb.OUTSIDE if full else [c for r in tb.ROLES for c in ([x for x in tb.CASES if x.role == r][:1] + [x for x in tb.OUTSIDE if x.role == r][:1])]
    rows = [await tb.run_case(await who(c.role), c) for c in picks]
    assert all(r["ok"] for r in rows), [r["ask"] for r in rows if not r["ok"]]
    # trần 12.500 (15/11, anh Anh: "vượt tí không sao"): thêm công cụ lõi apps.answer — trả lời bằng khối, 0220 lát 5 (~450 token, đã làm phẳng lược đồ)
    assert all(r["tools_tokens_first"] <= 12_500 for r in rows), [(r["role"], r["tools_tokens_first"]) for r in rows]
    if full:
        inside = rows[: len(tb.CASES)]
        out = {"rows": inside, "outside": rows[len(tb.CASES):], "summary": tb.summarize(inside), "summary_outside": tb.summarize(rows[len(tb.CASES):])}
        Path(os.environ["BARISTA_BENCH_OUT"]).write_text(json.dumps(out, ensure_ascii=False, indent=1))  # noqa: ASYNC240 — chỉ khi đo tay
