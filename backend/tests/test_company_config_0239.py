"""Câu hỏi rà vai 18/11/2026 thành CẤU HÌNH công ty (anh Anh: "các câu hỏi này để làm config, hiện tại chọn best practice") — ADR 0239 phần 3–4.

* Ai thấy thông tin gì: nhóm thông tin nhạy cảm gói khai × vai, đọc / ghi CHÍNH ma trận quyền (không bảng thứ hai), giữ hành động khác, qua chuỗi đổi quyền.
* Vai Công nghệ của lõi: hệ thống, không khách / tiền / lương; Sức khoẻ dữ liệu không lộ tên khách cho vai không đọc được khách.
* Câu gợi ý trợ lý theo vai là dữ liệu: tắt / sửa chữ / câu riêng; câu không cấp quyền; luôn chừa ≥ 2 chỗ cho câu của vai.
* Bàn việc: mục ``doers_only`` chỉ là việc của người có quyền ghi — người chỉ xem thấy ở "Đang theo dõi", không màu, không đếm.
* Câu hỏi cấu hình khi đưa công ty vào dùng: mỗi câu trỏ đúng khoá + lệnh có thật.
"""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from barista import presets, seed
from barista.agent import chat, desk
from barista.http_api import compose_suggestions
from barista.kernel import ai_hints, executor, registry
from barista.kernel.principal import Principal
from barista.modules import health
from tests.conftest import approved, ok, owner_val, q

PEOPLE = [("giamdoc", "Giám đốc thử", "director", "HQ", True), ("admin", "Quản trị thử", "admin", "QT", True),
          ("cn", "Công nghệ thử", "tech", "QT", False), ("ketoan", "Kế toán thử", "accountant", "KT", True),
          ("mkt", "Marketing thử", "marketer", "MKT", False), ("mkt2", "Marketing hai", "marketer", "MKT", False)]

NOT_FOR_TECH = {"crm", "leadflow", "parties", "parties.contacts", "acct", "ledger", "pay", "pay.slips", "pay.policy", "hr.terms", "hr.credential_private",
                "mkt", "mkt.ad_control", "exec", "mgmt", "findings"}


@pytest.fixture(scope="module")
async def co() -> dict[str, Any]:
    d = f"c{uuid.uuid4().hex[:8]}.demo"
    tid = await seed.create_tenant(f"Công ty cấu hình {d}", d, people=PEOPLE, packs=("finance", "crm", "marketing", "hr"), about="test")
    return {"tid": tid, "d": d}


async def _who(co: dict[str, Any], local: str) -> Principal:
    return await seed.who(co["tid"], local, co["d"])


# ── Ai thấy thông tin gì (0239 phần 3) ──

def test_every_information_group_names_a_grantable_resource_and_says_where_it_applies() -> None:
    groups = registry.info_groups()
    assert {"ledger", "customers", "contacts", "payslips", "contract_terms"} <= {g.key for _, g in groups}
    for _, g in groups:
        assert g.resource in registry.grantable() and g.applies.strip() and g.label.strip(), g
    assert next(g for _, g in groups if g.key == "ledger").scopes == ("all",)   # sổ cái: chưa có nghĩa "của mình"


async def test_who_sees_what_writes_the_grant_matrix_keeps_other_actions_and_goes_through_the_owner(co: dict[str, Any]) -> None:
    gd, admin, cn = await _who(co, "giamdoc"), await _who(co, "admin"), await _who(co, "cn")
    page = await q(gd, "admin.info_access")   # chủ tài khoản đọc được màn để duyệt; quản trị xin đổi
    row = next(r for r in page["rows"] if r["id"] == "customers")
    assert row["tech"] == "" and row["resource"] == "crm"                                     # vai Công nghệ không thấy khách
    # phạm vi nhóm không hiểu ⇒ từ chối; trợ lý không đổi quyền
    assert (await executor.run(gd, "admin.set_info_access", {"group": "customers", "role": "tech", "level": "all"})).status == "denied"   # chỉ xem
    bad = await executor.run(admin, "admin.set_info_access", {"group": "ledger", "role": "tech", "level": "own"})
    assert bad.status == "invalid" and "Cả công ty" in str(bad.error)
    agent = await executor.run(admin.as_agent(), "admin.set_info_access", {"group": "customers", "role": "tech", "level": "all"})
    assert agent.status != "ok" and not await owner_val("SELECT 1 FROM core.grants WHERE tenant_id = %s AND role = 'tech' AND resource = 'crm'", (co["tid"],))
    # ô của màn = ô ma trận quyền: giữ hành động ghi đang có, chỉ đổi phạm vi xem
    await approved(admin, "admin.set_grant", role="tech", resource="crm", scope="team", actions=["read", "write"])
    await approved(admin, "admin.set_info_access", group="customers", role="tech", level="all")
    assert await owner_val("SELECT scope || ':' || array_to_string(actions, '/') FROM core.grants WHERE tenant_id = %s AND role = 'tech' AND resource = 'crm'",
                           (co["tid"],)) == "all:read/write"
    assert next(r for r in (await q(gd, "admin.info_access"))["rows"] if r["id"] == "customers")["tech"] == "Cả công ty"
    # bỏ quyền xem khi vai còn quyền ghi ⇒ từ chối (sửa ở Vai trò & quyền)
    assert (await executor.run(admin, "admin.set_info_access", {"group": "customers", "role": "tech", "level": "none"})).status == "invalid"
    await approved(admin, "admin.set_grant", role="tech", resource="crm", scope="all", actions=["read"])
    await approved(admin, "admin.set_info_access", group="customers", role="tech", level="none")
    assert not await owner_val("SELECT 1 FROM core.grants WHERE tenant_id = %s AND role = 'tech' AND resource = 'crm'", (co["tid"],))
    assert (await executor.query(cn, "admin.info_access", {})).status == "ok"   # vai Công nghệ đọc được màn (core.admin) — đổi vẫn qua chủ tài khoản


# ── Vai Công nghệ ──

def test_tech_role_holds_the_system_but_no_customer_money_or_pay_resource() -> None:
    mine = {res: acts for role, res, _, acts in presets.grants() if role == "tech"}
    assert {"core.admin", "core.audit", "health", "flow.templates", "flow.automations", "hr"} <= set(mine)
    assert not set(mine) & NOT_FOR_TECH, set(mine) & NOT_FOR_TECH
    assert "hr.terms" not in mine and "manage" in mine["core.admin"].split()


async def test_data_health_names_customers_only_for_people_who_read_customers_company_wide(co: dict[str, Any]) -> None:
    cn, gd = await _who(co, "cn"), await _who(co, "giamdoc")
    issue = {"key": "dup_customers", "n": 2, "subtitle": "Nguyễn Văn A, Trần Thị B", "subtitle_resource": "crm"}
    assert health._subtitle(SimpleNamespace(p=cn), issue) == ""                                # type: ignore[arg-type]
    assert health._subtitle(SimpleNamespace(p=cn), {**issue, "subtitle_resource": None}) == issue["subtitle"]   # type: ignore[arg-type]
    assert gd.can("crm", "read") and gd.has_scope("crm", "all")                                   # giám đốc mặc định đọc khách cả công ty
    assert health._subtitle(SimpleNamespace(p=gd), issue) == issue["subtitle"]                # type: ignore[arg-type]
    assert (await executor.query(cn, "health.overview", {})).status == "ok"
    assert (await executor.query(cn, "crm.leads", {})).status in ("denied", "not_found")


# ── Marketing thấy chiến dịch cả phòng ──

async def test_marketers_see_the_campaigns_of_the_teams_they_belong_to(co: dict[str, Any]) -> None:
    m1, m2 = await _who(co, "mkt"), await _who(co, "mkt2")
    assert m1.scope("mkt") == "unit" and m2.scope("mkt") == "unit"
    code = f"C{uuid.uuid4().hex[:6].upper()}"
    await ok(m1, "mkt.upsert_campaign", code=code, name="Chiến dịch phòng", channel="facebook")
    assert await owner_val("""SELECT t.code FROM mkt.campaigns c JOIN core.teams t ON t.id = c.team_id WHERE c.tenant_id = %s AND c.code = %s""",
                           (co["tid"], code)) == "MKT"
    assert code in {r["code"] for r in (await q(m2, "mkt.campaigns"))["rows"]}                  # đồng nghiệp cùng phòng thấy


# ── Câu gợi ý trợ lý theo vai là dữ liệu (0239 phần 4) ──

async def test_role_hints_can_be_turned_off_reworded_added_and_reset_without_granting_anything(co: dict[str, Any]) -> None:
    gd, admin, kt = await _who(co, "giamdoc"), await _who(co, "admin"), await _who(co, "ketoan")
    base = chat.suggestions(admin, limit=50, rows=await chat.hint_rows(admin))
    assert "Ai đang bị khoá tài khoản?" in base
    await ok(gd, "core.set_role_hint", role="admin", key="admin.users", enabled=False)
    assert "Ai đang bị khoá tài khoản?" not in chat.suggestions(admin, limit=50, rows=await chat.hint_rows(admin))
    await ok(gd, "core.set_role_hint", role="admin", key="admin.users", text="Tài khoản nào đang bị khoá và vì sao?", enabled=True)
    assert "Tài khoản nào đang bị khoá và vì sao?" in chat.suggestions(admin, limit=50, rows=await chat.hint_rows(admin))
    # câu riêng gắn công cụ: chỉ hiện cho người chạy được công cụ đó — câu chữ không cấp quyền
    await ok(gd, "core.set_role_hint", role="admin", text="Nguồn dữ liệu nào trễ tuần này?", tool="health.overview")
    await ok(gd, "core.set_role_hint", role="accountant", text="Ai đang có quyền quản trị?", tool="admin.permissions")
    assert chat.suggestions(admin, limit=50, rows=await chat.hint_rows(admin))[0] == "Nguồn dữ liệu nào trễ tuần này?"
    assert "Ai đang có quyền quản trị?" not in chat.suggestions(kt, limit=50, rows=await chat.hint_rows(kt))
    assert (await executor.run(gd, "core.set_role_hint", {"role": "admin", "text": "Thiếu công cụ"})).status == "invalid"
    page = await q(gd, "core.role_hints", role="admin")
    assert {"custom", "edited"} <= {r["origin"] for r in page["rows"]}
    await ok(gd, "core.reset_role_hint", role="admin", key="admin.users")
    assert "Ai đang bị khoá tài khoản?" in chat.suggestions(admin, limit=50, rows=await chat.hint_rows(admin))
    # trợ lý chỉ đề xuất, không tự đổi câu gợi ý
    card = await executor.run(gd.as_agent(), "core.set_role_hint", {"role": "admin", "key": "admin.audit", "enabled": False})
    assert card.status != "ok"


def test_default_hint_keys_are_stable_per_tool_and_unique_per_role() -> None:
    on = frozenset(registry.PACKS)
    for role in {r for pk in registry.PACKS.values() for r in pk.roles}:
        keys = [h.key for h in ai_hints.defaults(role, on)]
        assert len(keys) == len(set(keys)), role


async def test_suggestions_keep_two_places_for_the_role_after_today_s_work(co: dict[str, Any]) -> None:
    admin = await _who(co, "admin")
    rows = await chat.hint_rows(admin)
    own = chat.suggestions(admin, rows=rows)
    out = compose_suggestions(["Việc hôm nay 1?", "Việc hôm nay 2?", "Việc hôm nay 3?"], admin, rows)
    assert out[:2] == ["Việc hôm nay 1?", "Việc hôm nay 2?"] and len(out) <= 6
    assert len([x for x in out if x in own]) >= min(2, len(own))


# ── Bàn việc: việc của người xử lý ──

async def test_a_doers_only_desk_item_is_watched_not_worked_by_someone_who_cannot_write(co: dict[str, Any]) -> None:
    gd, kt = await _who(co, "giamdoc"), await _who(co, "ketoan")

    def where(page: dict[str, Any], key: str) -> tuple[str, dict[str, Any]] | None:
        return next(((g["key"], s) for g in page["groups"] for s in g["sections"] if s["screen"]["key"] == key), None)

    mine = where(await desk.desk(kt), "payout_checks")
    assert mine is not None and mine[0] == "decide" and not mine[1].get("oversight")            # kế toán: việc cần mình quyết
    assert gd.has_scope("acct", "all") and not gd.can("acct", "write")                            # giám đốc mặc định chỉ xem kế toán
    theirs = where(await desk.desk(gd), "payout_checks")
    assert theirs is not None and theirs[0] == "watch" and theirs[1]["oversight"] is True
    assert all(x["tone"] == "accent" and not x.get("ask") for x in theirs[1]["lines"])
    assert not [x for x in await desk.attention(gd) if x.get("query") == "acct.payout_checks"]      # không vào lời chào / câu việc hôm nay


# ── Câu hỏi cấu hình khi đưa công ty vào dùng ──

def test_every_config_question_points_at_a_real_key_and_command() -> None:
    roles = {r for pk in registry.PACKS.values() for r in pk.roles}
    for pack, qn in registry.questions():
        assert qn.ask.strip() and qn.why.strip() and qn.decider.strip(), qn
        assert not qn.command or qn.command in registry.OPS, (pack, qn.key, qn.command)
        kind, _, rest = qn.key.partition(":")
        if kind == "grant":
            role, _, res = rest.partition(":")
            assert role in roles and res in registry.RESOURCES, qn.key


async def test_config_questions_page_lists_pack_questions_and_company_settings_with_current_values(co: dict[str, Any]) -> None:
    gd = await _who(co, "giamdoc")
    rows = {r["key"]: r for r in (await q(gd, "admin.config_questions"))["rows"]}
    assert rows["grant:marketer:mkt"]["current"] == "unit:read/write" and rows["grant:marketer:mkt"]["default"] == "unit"
    assert rows["grant:marketer:mkt"]["command"] == "admin.set_grant"
    settings = [r for k, r in rows.items() if k.startswith("setting:")]
    assert settings and all(r["command"] == "admin.set_setting" and r["current"] in ("on", "off") for r in settings)
