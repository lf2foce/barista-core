"""MA TRẬN BẤT BIẾN quyền trợ lý (quyết định 0144 mục 5) + bảng chân lý ``kernel/agent_policy.decide`` chạy SONG SONG với executor (lát 1 — chưa nối).

Hai loại kiểm, cố ý tách:
* BẤT BIẾN — câu KHÔNG phụ thuộc hàm quyết định (nếu lấy kết quả mong đợi từ chính hàm thì test tự chứng minh chính nó): sinh tự động mọi thao tác ×
  loại người gọi × chính sách mẫu, khẳng định điều phải đúng ở mọi ô.
* ĐỐI CHIẾU — ``decide`` cho cùng chế độ với các mảnh đang rải trong executor (``effective_mode``, ``company_mode``, nhánh attended / trần của ``_run``)
  trên cùng lưới; khi nối executor sang ``decide`` (lát 2) hành vi không đổi.
Cộng: mức rủi ro suy diễn chọi với trần (danh sách phải rỗng hoặc có lý do), registry chặn khai sai, trigger DB tách bạch nhiệm vụ."""

import itertools
import pathlib
import uuid
from typing import Any

import psycopg
import pytest

import barista.main  # noqa: F401 — nạp mọi gói
from barista.kernel import agent_policy as ap
from barista.kernel import executor, registry
from barista.kernel.agent_policy import Caller, Company, Run, decide, risk_of
from tests.conftest import owner_val
from tests.module_src import py_files
from tests.module_src import rel as rel_of

OPS = sorted(registry.OPS.values(), key=lambda o: o.name)
CMDS = [o for o in OPS if o.kind == "command"]
PROMOTABLE = [o for o in CMDS if o.promotable]
# Chính sách công ty MẪU: không đặt · siết nhóm đề xuất · tắt nhóm · siết lệnh · nới lệnh (chỉ có nghĩa với lệnh promotable)
POLICIES: dict[str, dict[str, str]] = {"none": {}, "grp_propose": {"*GRP*": "propose"}, "grp_deny": {"*GRP*": "deny"},
                                       "cmd_propose": {"*CMD*": "propose"}, "cmd_deny": {"*CMD*": "deny"}, "cmd_allow": {"*CMD*": "allow"},
                                       "grp_deny_cmd_allow": {"*GRP*": "deny", "*CMD*": "allow"}}
VIAS = ("human", "agent", "app", "link", "system")


def _overrides(op: registry.Op, pol: dict[str, str]) -> dict[str, str]:
    return {k.replace("*GRP*", f"{op.module}.*").replace("*CMD*", op.name): v for k, v in pol.items()}


def _grid():
    for op, (pname, pol), via, attended, kind in itertools.product(OPS, POLICIES.items(), VIAS, (False, True), ap.KINDS):
        yield op, pname, _overrides(op, pol), Caller(via=via, attended=attended, kind=kind)


def _legacy_effective_mode(op: registry.Op, ov: dict[str, str]) -> str:
    """Thuật toán TRƯỚC 0144 (executor.effective_mode cũ, 0143 lát 1) — chuẩn đối chiếu độc lập; executor hiện gọi decide nên không dùng nó làm chuẩn."""
    modes = registry.AGENT_MODES
    ceiling = "allow" if op.promotable and ov.get(op.name) == "allow" else op.agent
    picks = [ceiling] + [m for m in (ov.get(f"{op.module}.*"), ov.get(op.name)) if m is not None and not (op.kind == "query" and m == "propose")]
    return modes[min(modes.index(m) for m in picks)]


# ── Bất biến (không phụ thuộc decide — khẳng định bằng điều kiện của ô) ──

def test_invariants_hold_on_every_cell() -> None:
    bad: list[str] = []
    cells = 0
    for op, pname, ov, caller in _grid():
        for within, switch in itertools.product((None, False, True), (False, True)):
            d = decide(op, caller, Company(ov, within, switch))
            cells += 1
            risk = risk_of(op)
            where = f"{op.name} · {pname} · {caller.via}/{'attended' if caller.attended else 'nền'}/{caller.kind} · caps={within} · switch={switch}"
            if not d.why:
                bad.append(f"{where}: không có lý do")
            if caller.kind == "delegate" and op.kind == "command" and d.mode != "deny":
                bad.append(f"{where}: tay cầm uỷ quyền chạy được lệnh")
            if caller.via != "agent":
                continue
            if risk in ap.NEVER_ALLOW and op.kind == "command" and d.allow and op.agent != "allow":
                bad.append(f"{where}: rủi ro {risk} mà trợ lý được tự làm qua nới")
            if op.kind == "command" and op.agent != "allow" and d.allow and not (op.promotable and risk == "low"
                                                                                  and (ov.get(op.name) == "allow" or (switch and ov.get(op.name) is None))):
                bad.append(f"{where}: vượt trần code mà không phải lệnh nới được + (công ty nới lệnh, hoặc công tắc bật và lệnh chưa bị siết riêng)")
            if switch and op.kind == "command" and op.promotable and risk == "low" and ov.get(op.name) == "propose" and d.allow:
                bad.append(f"{where}: công ty siết riêng lệnh này mà công tắc vẫn mở")
            if op.kind == "command" and op.agent != "allow" and d.allow and not caller.attended:
                bad.append(f"{where}: lượt không người được tự làm lệnh trần {op.agent}")
            if op.kind == "command" and op.agent != "allow" and d.allow and not within:
                bad.append(f"{where}: tự làm ngoài trần ngày / tiền")
            if ov.get(f"{op.module}.*") == "deny" and d.mode != "deny":
                bad.append(f"{where}: công ty tắt nhóm mà trợ lý vẫn có chế độ {d.mode}")
            if op.agent == "deny" and d.mode != "deny":
                bad.append(f"{where}: trần deny mà ra {d.mode}")
    assert cells > 200_000, cells   # lưới phải thật sự phủ (~790 thao tác × 7 chính sách × 5 via × 2 × 3 × 3 × 2 công tắc)
    assert not bad, "\n".join(bad[:40]) + (f"\n… +{len(bad) - 40}" if len(bad) > 40 else "")


def test_system_and_link_rules_stay_in_allowed_not_in_decide() -> None:
    """Người thật / hệ thống / app / link KHÔNG bị rào chế độ trợ lý (decide = allow) — quyền, gói, link, app vẫn do executor.allowed kiểm như cũ."""
    for op in OPS:
        for via in ("human", "system", "app", "link"):
            assert decide(op, Caller(via=via), Company({})).allow, (op.name, via)


# ── Đối chiếu với executor hiện tại (hành vi không đổi khi nối — 0144 lát 2) ──

def _current_top_level(op: registry.Op, ov: dict[str, str], attended: bool, within: bool | None) -> str:
    """Nhánh via="agent" của executor._run TRƯỚC khi nối (0143 lát 1): effective_mode cũ, rồi lệnh được nới cần lượt có người + trong trần."""
    mode = _legacy_effective_mode(op, ov)
    if mode == "allow" and op.kind == "command" and op.agent != "allow" and (not attended or not within):
        return "propose"
    return mode


def _current_nested(op: registry.Op, ov: dict[str, str], preview: bool) -> str:
    """Nhánh via="agent" của executor.invoke hôm nay: chỉ chính sách công ty (company_mode); đề xuất + lệnh + không xem trước ⇒ NeedsConfirmation."""
    modes = [ov[k] for k in (op.name, f"{op.module}.*") if k in ov]
    if not modes:
        return "allow"
    m = registry.AGENT_MODES[min(registry.AGENT_MODES.index(x) for x in modes)]
    if m == "deny":
        return "deny"
    if m == "propose" and op.kind == "command" and not preview:
        return "propose"
    return "allow"


def test_decide_matches_the_executor_before_wiring_on_the_whole_grid() -> None:
    """Công tắc TẮT ⇒ decide = thuật toán cũ trên mọi ô (lát nối không đổi hành vi); công tắc bật là ô mới, kiểm ở bất biến."""
    diff: list[str] = []
    for op, pname, ov, caller in _grid():
        if caller.via != "agent" or caller.kind == "delegate":
            continue
        for within in (None, False, True):
            got = decide(op, caller, Company(ov, within)).mode
            want = _current_top_level(op, ov, caller.attended, within)
            if got != want:
                diff.append(f"{op.name} · {pname} · attended={caller.attended} · caps={within}: decide={got} executor={want}")
        for preview in (False, True):
            got = decide(op, caller, Company(ov), Run(nested=True, preview=preview)).mode
            want = _current_nested(op, ov, preview)
            if got != want:
                diff.append(f"{op.name} · {pname} · lồng preview={preview}: decide={got} executor={want}")
    assert not diff, "\n".join(diff[:30]) + (f"\n… +{len(diff) - 30}" if len(diff) > 30 else "")


def test_the_executor_wrapper_agrees_with_decide() -> None:
    """effective_mode (danh mục công cụ / màn quản trị) chỉ là bọc của decide — không được lệch."""
    for op, _, ov, _ in itertools.islice(_grid(), 0, None, 97):
        for switch in (False, True):
            assert executor.effective_mode(op, ov, switch) == decide(op, Caller("agent", attended=True), Company(ov, True, switch)).mode


# ── Mức rủi ro ──

# Lệnh mà suy diễn ra money / authority nhưng trần là "allow" — MỖI dòng một lý do; đích là danh sách rỗng (chủ gói khai tay risk= hoặc hạ trần)
RISK_EXCEPTIONS: dict[str, str] = {}   # acct.add_statement_line đã hạ về propose (0151, review 10/11: nay ghi cả dòng tiền ra)


def test_inferred_risk_does_not_contradict_the_ceiling() -> None:
    bad = [f"{o.name}: rủi ro {risk_of(o)} nhưng agent=allow" for o in CMDS if not o.internal and o.agent == "allow" and risk_of(o) in ap.NEVER_ALLOW
           and o.name not in RISK_EXCEPTIONS]
    assert not bad, bad
    assert set(RISK_EXCEPTIONS) <= {o.name for o in CMDS if o.agent == "allow" and risk_of(o) in ap.NEVER_ALLOW}, "RISK_EXCEPTIONS thừa — xoá dòng"
    for o in PROMOTABLE:
        assert o.risk == "low" and risk_of(o) == "low", (o.name, o.risk, risk_of(o))   # khai tường minh, không suy diễn
    assert all(risk_of(o) == "read" for o in OPS if o.kind == "query")
    assert all(risk_of(o) == "authority" for o in CMDS if o.sensitive is not None), [o.name for o in CMDS if o.sensitive and risk_of(o) != "authority"]


def test_registry_refuses_money_or_authority_commands_that_the_assistant_may_run() -> None:
    m = registry.Module("zz_risk_probe", "Thử rủi ro", pack="crm")   # gói nền chỉ gồm module lõi (ctx-issued) ⇒ mượn một gói có thật
    try:
        with pytest.raises(ValueError, match="không bao giờ"):
            m.command("a", resource=None, title="x", agent="allow", risk="money")(_noop)
        with pytest.raises(ValueError, match="không bao giờ"):
            m.command("b", resource="flow", title="x", agent="propose", promotable=True, risk="authority", target="x", undone_by=("flow.set_task",))(_noop)
        with pytest.raises(ValueError, match="lạ"):
            m.command("c", resource=None, title="x", risk="high")(_noop)
        with pytest.raises(ValueError, match=r"risk=.*tường minh"):   # promotable không được dựa vào risk_of suy ra
            m.command("d", resource="flow", title="x", agent="propose", promotable=True, target="x", undone_by=("flow.set_task",))(_noop)
        with pytest.raises(ValueError, match="chỉ lệnh rủi ro low"):
            m.command("e", resource="flow", title="x", agent="propose", promotable=True, risk="read", target="x", undone_by=("flow.set_task",))(_noop)
    finally:
        registry.MODULES.pop("zz_risk_probe", None)
        registry.MODULE_PACK.pop("zz_risk_probe", None)
        mods = registry.PACK_MODULE.get("crm")
        if isinstance(mods, list) and "zz_risk_probe" in mods:
            mods.remove("zz_risk_probe")
        for k in [k for k in registry.OPS if k.startswith("zz_risk_probe.")]:
            registry.OPS.pop(k)


async def _noop(ctx: Any, inp: Any) -> dict[str, Any]:
    return {}


# ── Trigger DB: tách bạch nhiệm vụ không phụ thuộc Python ──

async def test_db_refuses_requester_self_approval_and_double_approval(demo: dict[str, Any]) -> None:
    tid = demo["tenant"]
    assert await owner_val("SELECT count(*) FROM pg_trigger WHERE tgname = 'approvals_separation_of_duties'") == 1   # migration 053 đã chạy
    requester, other, third = (await owner_val("SELECT array_agg(user_id) FROM (SELECT user_id FROM core.memberships WHERE tenant_id = %s AND active LIMIT 3) x", (tid,)))
    aid = await owner_val("""INSERT INTO core.approvals (tenant_id, chain, command, input, run_as, summary, requested_by, steps, approver_ids)
                             VALUES (%s, 'test', 'flow.create_task', '{}', 'requester', 'thử trigger', %s, '[{"kind":"owner"},{"kind":"owner"}]', %s)
                             RETURNING id""", (tid, requester, [other]))

    async def add(who: Any) -> None:
        await owner_val("""UPDATE core.approvals SET decisions = decisions || jsonb_build_array(jsonb_build_object('step', jsonb_array_length(decisions),
                           'user_id', %s::text, 'approve', true)) WHERE id = %s RETURNING 1""", (who, aid))

    with pytest.raises(psycopg.errors.CheckViolation, match="không tự duyệt"):
        await add(requester)
    await add(other)
    with pytest.raises(psycopg.errors.CheckViolation, match="hai bước"):
        await add(other)
    await add(third)
    # sửa người đã quyết ở bước cũ cũng bị chặn
    with pytest.raises(psycopg.errors.CheckViolation, match="không được đổi"):
        await owner_val("UPDATE core.approvals SET decisions = jsonb_set(decisions, '{0,user_id}', to_jsonb(%s::text)) WHERE id = %s RETURNING 1",
                        (str(uuid.uuid4()), aid))
    await owner_val("DELETE FROM core.approvals WHERE id = %s RETURNING 1", (aid,))


async def test_db_refuses_prefilled_decisions_and_identity_changes(demo: dict[str, Any]) -> None:
    """Hàng rào bất kể đường vào (review 09/11): INSERT mang sẵn quyết định bị chặn; người xin / công ty / cách chạy bất biến sau khi tạo — SQL thẳng bằng
    role chủ (BYPASSRLS) để chứng minh đây là rào ở DB, không phải ở Python. Lệnh / đầu vào / chuỗi KHÔNG khoá: migration đổi tên (049, 0110) phải sửa được."""
    tid = demo["tenant"]
    a, b = (await owner_val("SELECT array_agg(user_id) FROM (SELECT user_id FROM core.memberships WHERE tenant_id = %s AND active LIMIT 2) x", (tid,)))
    with pytest.raises(psycopg.errors.CheckViolation, match="không được mang sẵn quyết định"):
        await owner_val("""INSERT INTO core.approvals (tenant_id, chain, command, input, run_as, summary, requested_by, steps, approver_ids, decisions)
                           VALUES (%s, 'test', 'flow.create_task', '{}', 'requester', 'thử', %s, '[{"kind":"owner"}]', %s,
                                   jsonb_build_array(jsonb_build_object('step', 0, 'user_id', %s::text, 'approve', true))) RETURNING id""",
                        (tid, a, [b], a))
    aid = await owner_val("""INSERT INTO core.approvals (tenant_id, chain, command, input, run_as, summary, requested_by, steps, approver_ids)
                             VALUES (%s, 'test', 'flow.create_task', '{}', 'requester', 'thử', %s, '[{"kind":"owner"}]', %s) RETURNING id""", (tid, a, [b]))
    for col, val in (("requested_by", b), ("run_as", "system")):
        with pytest.raises(psycopg.errors.CheckViolation, match="không đổi được"):
            await owner_val(f"UPDATE core.approvals SET {col} = %s WHERE id = %s RETURNING 1", (val, aid))
    # cột khác vẫn đổi được: trạng thái / tóm tắt (nghiệp vụ), lệnh / đầu vào (migration đổi tên khoá)
    assert await owner_val("UPDATE core.approvals SET status = 'stale', summary = 'đổi tóm tắt', command = 'flow.adjust_task', input = '{\"x\": 1}'"
                           " WHERE id = %s RETURNING 1", (aid,)) == 1
    await owner_val("DELETE FROM core.approvals WHERE id = %s RETURNING 1", (aid,))


# ── Cấu trúc: chỉ ``agent_policy.decide`` quyết chế độ trợ lý (0144 mục 2) ──

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "barista"
# (file, mẫu) ⇒ lý do được đọc trần / chính sách ngoài decide — giữ NHỎ, mỗi dòng một lý do
DECISION_ALLOWED: dict[tuple[str, str], str] = {
    ("kernel/agent_policy.py", "*"): "nơi quyết",
    ("kernel/executor.py", "core.agent_policies"): "policy_overrides nạp chính sách cho decide; _autorun_ok đọc trần ngày / tiền — chỗ đọc bảng duy nhất ngoài quản trị",
    ("kernel/executor.py", "op.agent"): "allowed(): luật app từ xa (0140) — app không chạy lệnh trợ lý bị cấm trừ apps=True; và cờ autorun = trần code ≠ allow",
    ("kernel/registry.py", "*"): "khai trần / kiểm lúc khai (promotable, risk)",
    ("modules/admin.py", "op.agent"): "màn quản trị hiện TRẦN và set_agent_policy kiểm nới / siết so với trần — hiển thị + ghi chính sách, không quyết lúc chạy",
    ("modules/admin.py", "core.agent_policies"): "quản trị GHI chính sách (CORE_WRITERS)",
    ("modules/admin.py", "AGENT_MODES.index"): "set_agent_policy so mức xin với trần để biết là nới hay siết — kiểm lúc GHI chính sách, không quyết lúc chạy",
    ("agent/chat.py", "op.agent"): "ToolRun: thẻ đề xuất bên lề (op.aside) và mô tả công cụ — không quyết chế độ",
    ("seed.py", "core.agent_policies"): "demo_autonomy chỉ hỏi công ty đã từng cấu hình tự chủ chưa (có dòng nào) — ghi qua admin.set_agent_preset, không quyết",
}
DECISION_PATTERNS = ("op.agent", "effective_mode(", "agent_mode(", "company_mode(", "core.agent_policies", "AGENT_MODES.index")


def test_only_decide_reads_ceilings_and_policies() -> None:
    bad: list[str] = []
    for path in py_files():   # lõi + gói ngoài đang cài (0191)
        rel = rel_of(path)
        if (rel, "*") in DECISION_ALLOWED:
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            for pat in DECISION_PATTERNS:
                if pat in code and (rel, pat) not in DECISION_ALLOWED and not (pat == "effective_mode(" and rel in ("kernel/executor.py", "agent/chat.py", "modules/admin.py")):
                    bad.append(f"{rel}:{n}: {pat} — {code.strip()[:90]}")
    assert not bad, "quyết định chế độ trợ lý phải ở agent_policy.decide (0144):\n" + "\n".join(bad)
    assert all((SRC / f).exists() for f, _ in DECISION_ALLOWED), "DECISION_ALLOWED trỏ file không còn"


def test_decide_reports_why_on_proposals() -> None:
    op = registry.get("flow.adjust_task")
    assert op is not None
    d = decide(op, Caller(via="agent", attended=False), Company({op.name: "allow"}, within_caps=True))
    assert d.mode == "propose" and any("không người" in w for w in d.why)
    d = decide(op, Caller(via="agent", attended=True), Company({f"{op.module}.*": "deny"}))
    assert d.mode == "deny" and any("siết nhóm" in w for w in d.why)
