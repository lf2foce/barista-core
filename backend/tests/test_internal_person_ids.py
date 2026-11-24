"""QUY TẮC "ID NGƯỜI DO NGƯỜI GỌI NÊU" (0125 §B). Thao tác nội bộ chạy quyền hệ thống mà nhận mã người / đối tượng do người gọi nêu ⇒ người bán chỉ
tên được người mình không thấy (hai lỗi cao của gói đào tạo, một lỗi đọc ở việc từ tin nhắn). Rào ở ba chỗ:

1. Hợp đồng (``why=``) của mọi thao tác nội bộ có tham số kiểu "người" phải khai ``[id: <kiểu> — <cách kiểm>]`` (registry — thêm thao tác thiếu ⇒ đỏ).
2. Mọi lời gọi ``call_system`` truyền thẳng ``inp.<…>`` vào tham số kiểu "người" phải được duyệt trong ``RESOLVED_FIRST`` kèm lý do (đọc mã — AST).
3. Bốn lỗ thật tìm ra khi rà (người đặt quyền hưởng, hồ sơ làm việc, khai nguồn lực lịch, mời người ngoài) đã chuyển sang quyền người gọi: test hành vi (mời người ngoài: tests/test_meet_direct.py)."""

import ast
import pathlib
import re
import typing
import uuid
from collections.abc import AsyncIterator

import pytest
from pydantic import BaseModel

import barista.main  # noqa: F401 — nạp mọi module để registry đủ
from barista import seed
from barista.db import db
from barista.kernel import executor, registry
from barista.kernel.principal import Principal
from tests.conftest import Who, approved, ok, owner_val
from tests.module_src import declared, module_of, py_files

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "barista"
CALLS = ("call_system", "call_system_optional")
# Tham số kiểu "người" (và đối tượng gắn với một người: khách, lead): *_id / *_ids của các loại sau, cùng `by`.
PERSON = re.compile(r"(?:^|_)(?:party|user|customer|lead|learner|payer|guardian|payee|assignee|owner|sale|seller|student|member)(?:_party)?_ids?$|_party_ids?$|^by$")
KINDS = ("chính người gọi", "người gọi thấy", "module gọi tự có", "RLS ghi sau")
MARK = re.compile(r"\[id: (" + "|".join(KINDS) + r") — ([^\]]{20,})\]")


def person_fields(model: type[BaseModel], seen: frozenset[type] = frozenset()) -> list[str]:
    """Tên tham số kiểu "người" của một đầu vào, kể cả trong mô hình lồng (``payees.party_id``)."""
    out: list[str] = []
    for name, f in model.model_fields.items():
        if PERSON.search(name):
            out.append(name)
        for t in (f.annotation, *typing.get_args(f.annotation)):
            if isinstance(t, type) and issubclass(t, BaseModel) and t not in seen:
                out += [f"{name}.{x}" for x in person_fields(t, seen | {model})]
    return out


def test_every_internal_contract_with_a_person_id_says_how_it_is_verified() -> None:
    bad: list[str] = []
    checked: set[str] = set()
    for op in sorted(registry.OPS.values(), key=lambda o: o.name):
        # hợp đồng quyền hệ thống = khai ``callers=`` HOẶC thuộc một họ mở cho gói ngoài lõi (``family=`` — 0155: không còn tên gói trong callers)
        if not (op.internal and (op.callers or op.family in registry.OPEN_FAMILIES)) or not (ids := person_fields(op.input)):
            continue
        checked.add(op.name)
        if not MARK.search(op.why):
            bad.append(f"{op.name} ({', '.join(ids)}): why= chưa khai “[id: <{' | '.join(KINDS)}> — cách kiểm]” — id này do ai nêu, đã kiểm bằng quyền ai?")
    assert not bad, "\n".join(bad)
    assert {"parties.add_dependant_for", "parties.record_contact_consent", "crm.link_deal"} <= checked   # test không rỗng


def test_the_person_pattern_catches_the_names_that_caused_the_bugs() -> None:
    for name in ("party_id", "user_id", "customer_id", "learner_party_id", "payer_party_id", "lead_id", "guardian_party_id", "given_by_party_id",
                 "party_ids", "lead_ids", "owner_id", "assignee_id", "sale_id", "by"):
        assert PERSON.search(name), name
    for name in ("unit_id", "deal_id", "team_id", "project_id", "plan_id", "document_id"):
        assert not PERSON.search(name), name


# Lời gọi call_system truyền thẳng đầu vào của người dùng (``inp.…``) vào tham số kiểu "người": (module, hàm bao quanh, thao tác đích) → vì sao an toàn.
# Thêm một dòng ở đây = đã đọc từng bên gọi, KHÔNG phải tắt test.
RESOLVED_FIRST = {
    ("acct", "release_recovery", "parties.resolve_for_payment"): "thao tác nội bộ họ settlement (gói ngành gọi): party_id lấy từ dòng quyền hưởng của gói gọi, gói đã kiểm bằng call_as trước khi ghi dòng đó",
    ("acct", "recognize_recovery", "parties.resolve_for_payment"): "thao tác nội bộ họ settlement (gói ngành gọi): party_id lấy từ dòng quyền hưởng của gói gọi, gói đã kiểm bằng call_as trước khi ghi dòng đó",
    ("acct", "recognize_payable", "parties.resolve_for_payment"): "thao tác nội bộ họ settlement (gói ngành gọi): party_id do gói gọi dựng từ bộ phân bổ của nó, đã kiểm bằng call_as lúc đặt",
    ("acct", "prepare_payout", "parties.resolve_for_payment"): "thao tác nội bộ họ settlement (gói ngành gọi): party_id từ phiếu đã duyệt / bộ phân bổ của gói gọi",
    ("leadflow", "assign", "crm.lead_cards"): "lead_id đã đọc trong hàng chờ bằng RLS của người gọi ở dòng trước",
    ("leadflow", "assign", "leadflow.give"): "như trên, người nhận đã lọc qua _lead_capable",
    ("pay", "set_rate", "parties.resolve_for_payment"): "party_id đã có trong ctx.call parties.options (quyền người đặt mức) ngay dòng trước; chỉ để chặn tự đặt mức cho mình",
    ("pay", "record", "parties.ensure_member"): "thân của khe nối pay.record_work (thao tác nội bộ, hợp đồng [id: module gọi tự có]): user_id do NGUỒN nêu "
                                                 "(người dạy của buổi đã xác nhận); đường người nhập (import_work / request_entry) truyền party_id đã qua "
                                                 "ctx.call parties.options bằng quyền người nhập",
    ("parties", "update", "acct.party_open_vnd"): "dòng định danh đã đọc FOR UPDATE bằng RLS của người gọi, và đã đòi phạm vi cả công ty",
    ("crm", "assign", "leadflow.follow_owner"): "0153: lead_id đã đọc bằng RLS của người gọi (crm.leads) ngay trên; owner_id tra theo email thành viên đang "
                                                "hoạt động; người phụ trách ghi lại bằng quyền + RLS người gọi (WITH CHECK) trong cùng giao dịch ⇒ sai thì huỷ cả",
    ("crm", "_lead_into_deal", "leadflow.follow_owner"): "0153: thân của crm.link_deal (nội bộ, họ lead_conversion — gói gọi tự khai dòng của mình): lead khớp chỉ nhận khi "
                                                         "người nhập thấy (lead_seen bằng quyền họ), owner_id = sale của giao dịch, by = người nhập",
}


# Dòng của gói ngoài repo đang cài: gói tự khai trong core_guards.json của nó ("<module>|<hàm>|<thao tác>": lý do) — 0191
RESOLVED_FIRST |= {(m, f, o): why for (m, f, o), why in ((tuple(k.split("|")), why) for k, why in declared("resolved_first").items())}


def _sites() -> list[tuple[str, str, str, int]]:
    out: list[tuple[str, str, str, int]] = []
    for f in py_files():
        for fn in ast.walk(ast.parse(f.read_text())):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for n in ast.walk(fn):
                if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in CALLS and n.args
                        and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                    continue
                op = registry.get(n.args[0].value)
                callee_ids = person_fields(op.input) if op else []
                for k in n.keywords:
                    uses_inp = any(isinstance(x, ast.Name) and x.id == "inp" for x in ast.walk(k.value))
                    if uses_inp and ((k.arg is None and callee_ids) or (k.arg is not None and PERSON.search(k.arg))):
                        out.append((module_of(f) or f.stem, fn.name, n.args[0].value, n.lineno))
    return list(dict.fromkeys(out))


def test_system_calls_never_pass_caller_input_as_a_person_id_unreviewed() -> None:
    sites = _sites()
    unreviewed = [f"{mod}:{line} {fn} → {op}: truyền inp.… (mã người do người gọi nêu) vào quyền hệ thống — kiểm bằng call_as trước, hoặc duyệt trong RESOLVED_FIRST"
                  for mod, fn, op, line in sites if (mod, fn, op) not in RESOLVED_FIRST]
    assert not unreviewed, "\n".join(unreviewed)
    listed = set(RESOLVED_FIRST)   # kể cả dòng gói ngoài đang cài khai: dòng không còn khớp lời gọi nào ⇒ thừa
    assert listed <= {(m, f, o) for m, f, o, _ in sites}, f"RESOLVED_FIRST thừa: {sorted(listed - {(m, f, o) for m, f, o, _ in sites})}"


# ── Bốn lỗ thật: người gọi nêu mã người họ KHÔNG thấy trong danh mục ──

async def _blind_role(admin: Principal) -> tuple[Principal, str]:
    """Một vai như quản trị nhưng KHÔNG đọc danh mục cá nhân & tổ chức (parties): đủ quyền hoa hồng / hồ sơ làm việc / lịch / gọi."""
    tag = uuid.uuid4().hex[:8]
    role, local = "blind_" + "".join(chr(ord("a") + int(c, 16)) for c in tag), f"blind{tag}"   # khoá vai chỉ nhận chữ thường và gạch dưới
    domain = admin.email.rpartition("@")[2]
    await approved(admin, "admin.upsert_role", key=role, label="Vai thử không đọc danh mục", copy_from="admin")
    for res in ("parties", "parties.contacts"):
        await approved(admin, "admin.set_grant", role=role, resource=res, scope="all", actions=[])
    await approved(admin, "admin.invite_user", email=f"{local}@{domain}", name="Người thử không thấy danh mục", role=role)   # vai có quyền quản trị
    p = await seed.who(admin.tenant_id, local, domain)
    assert not p.can("parties", "read")
    return p, role


async def _drop_role(p: Principal, role: str) -> None:
    async with db.owner_tx() as conn:
        await conn.execute("DELETE FROM core.team_members WHERE user_id = %s", (p.user_id,))
        await conn.execute("DELETE FROM core.memberships WHERE user_id = %s", (p.user_id,))
        await conn.execute("DELETE FROM core.grants WHERE tenant_id = %s AND role = %s", (p.tenant_id, role))
        await conn.execute("DELETE FROM core.roles WHERE tenant_id = %s AND key = %s", (p.tenant_id, role))


@pytest.fixture
async def blind(who: Who) -> AsyncIterator[Principal]:
    p, role = await _blind_role(await who("admin"))
    yield p
    await _drop_role(p, role)


async def _party(admin: Principal) -> uuid.UUID:
    return (await ok(admin, "parties.create", display_name=f"Người ngoài tầm nhìn {uuid.uuid4().hex[:6]}"))["party_id"]


async def test_work_assignment_only_names_people_the_caller_can_see(who: Who, blind: Principal) -> None:
    admin = await who("admin")
    pid = await _party(admin)
    body = {"party_id": str(pid), "manager_party_id": str(await _party(admin)), "valid_from": "2026-11-01", "reason": "Dữ liệu thử"}
    r = await executor.run(blind, "hr.set_assignment", body)
    assert r.status == "denied", (r.status, r.error)
    assert await owner_val("SELECT count(*) FROM hr.assignments WHERE party_id = %s", (pid,)) == 0
    assert (await executor.run(admin, "hr.set_assignment", body)).status == "ok"


@pytest.fixture(scope="module")
async def sched_co() -> tuple[Principal, uuid.UUID]:
    domain = f"n{uuid.uuid4().hex[:6]}.demo"
    tid = await seed.create_tenant(f"Công ty thử mã người {domain}", domain, people=[("admin", "Quản trị", "admin", "QT", True)], packs=("schedule", "directory"))
    return await seed.who(tid, "admin", domain), tid


async def test_a_schedule_resource_only_links_to_people_the_caller_can_see(sched_co: tuple[Principal, uuid.UUID]) -> None:
    admin, _ = sched_co
    pid = await _party(admin)
    p, role = await _blind_role(admin)
    try:
        body = {"name": f"Người lịch {uuid.uuid4().hex[:4]}", "nature": "person", "kind": "giáo viên", "team_code": "QT", "party_id": str(pid)}
        r = await executor.run(p, "schedule.add_resource", body)
        assert r.status == "invalid" and "không có quyền xem danh mục" in (r.error or ""), (r.status, r.error)
        assert await owner_val("SELECT count(*) FROM schedule.resources WHERE party_id = %s", (pid,)) == 0
        assert (await executor.run(admin, "schedule.add_resource", body)).status == "ok"
    finally:
        await _drop_role(p, role)
