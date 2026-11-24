"""Rà form nhóm A (quản trị, kế toán, nhân sự, KPI, quy trình, trao đổi — 18/11/2026, ADR 0225): không bắt gõ mã / khoá có sẵn trong hệ thống,
không hỏi thứ máy đoán được; danh sách chọn bằng chữ người đọc. Tên trường cũ vẫn nhận (bất biến 7)."""

import re
import uuid
from datetime import timedelta

from barista.kernel import executor, forms, registry
from barista.kernel.clock import today
from tests.conftest import Who, approved, fresh_sale, ok, owner_val, q

GROUP_A = {"admin", "acct", "hr", "kpi", "flow", "work", "memory", "apps", "talk", "connect"}


def _tag() -> str:
    return uuid.uuid4().hex[:6]


# ── Cấu trúc: không còn ô gõ mã trần ở form người dùng nhóm A ──

def test_no_bare_code_box_left_in_group_a_forms() -> None:
    """Ô tên ``code`` / ``key`` / ``batch`` / ``ref`` kiểu chữ, hiện, không chọn từ danh sách, không gấp ⇒ người dùng bị bắt gõ khoá hệ thống. Lệnh nội bộ / của
    nhà vận hành không phải form người dùng. Thêm lệnh mới mà vướng ⇒ chọn từ danh sách, hoặc để trống cho máy đặt từ tên và gấp vào ``group=``."""
    bare = []
    for name, op in sorted(registry.OPS.items()):
        if op.module not in GROUP_A or op.kind != "command" or op.internal or registry.operator_command(name):
            continue
        for f in forms.form_spec(op)["fields"]:
            if f["key"] in ("code", "key", "batch", "ref") and f["kind"] == "text" and not f["hidden"] and not f.get("group") \
                    and not f.get("options") and not f.get("options_query"):
                bare.append(f"{name}.{f['key']}")
    assert bare == [], bare


def test_machine_made_codes_are_folded_and_optional() -> None:
    for name, key in (("admin.upsert_entity", "code"), ("admin.upsert_team", "code"), ("admin.upsert_role", "key"), ("admin.set_chain", "key"),
                      ("acct.set_category", "code"), ("acct.set_tax_category", "code"), ("acct.set_bank_account", "code"),
                      ("acct.set_cost_object", "code"), ("flow.set_template", "key"), ("hr.save_credential_kind", "key")):
        spec = {f["key"]: f for f in forms.form_spec(registry.OPS[name])["fields"]}[key]
        assert not spec["required"] and spec.get("group"), (name, key)


def test_dates_the_machine_can_guess_are_not_required() -> None:
    for name, key in (("acct.lock_period", "locked_through"), ("hr.set_employment", "started_on"), ("hr.set_assignment", "valid_from"),
                      ("admin.set_team_lead", "valid_from"), ("hr.end_employment", "ended_on")):
        spec = {f["key"]: f for f in forms.form_spec(registry.OPS[name])["fields"]}[key]
        assert not spec["required"] and spec["placeholder"], (name, key)


def test_no_decision_numbers_in_visible_wording_of_group_a() -> None:
    pat = re.compile(r"\((?:ADR )?0\d{3}\b|\b0\d{3}\)")
    for name, op in registry.OPS.items():
        if op.module in GROUP_A and op.kind == "command" and not op.internal:
            for f in forms.form_spec(op)["fields"]:
                if not f["hidden"]:
                    assert not pat.search(f"{f['label']} {f['help']}"), (name, f["key"])


def test_heavy_forms_keep_only_the_main_fields_outside_the_fold() -> None:
    spec = forms.form_spec(registry.OPS["acct.record_expense"])["fields"]
    top = [f["key"] for f in spec if not f["hidden"] and not f.get("group")]
    assert len(top) <= 7 and {"bill_on", "category", "amount_vnd", "supplier"} <= set(top), top
    trust = [f["key"] for f in forms.form_spec(registry.OPS["admin.set_agent_trust"])["fields"] if not f.get("group")]
    assert len(trust) <= 3, trust


# ── Hành vi: máy đặt mã, mặc định đúng ──

async def test_entity_code_is_made_from_the_name(who: Who) -> None:
    admin, tag = await who("admin"), _tag()
    a = await ok(admin, "admin.upsert_entity", name=f"Chi nhánh Thử {tag}")
    b = await ok(admin, "admin.upsert_entity", name=f"Chi nhánh Thử {tag}")
    codes = [await owner_val("SELECT code FROM core.legal_entities WHERE id = %s", (x["entity_id"],)) for x in (a, b)]
    assert codes[0].startswith("CHI-NHANH-THU") and re.fullmatch(r"[A-Z0-9_-]{1,20}", codes[0]) and codes[0] != codes[1]
    assert (await ok(admin, "admin.upsert_entity", code=codes[0], name=f"Chi nhánh Thử {tag} (đổi tên)"))["summary"].startswith(f"Pháp nhân {codes[0]}")   # mã cũ vẫn nhận


async def test_role_and_chain_keys_are_made_from_the_label(who: Who) -> None:
    admin, tag = await who("admin"), _tag()
    role = (await ok(admin, "admin.upsert_role", label=f"Vai thử {tag}"))["role"]
    assert role.startswith("vai_thu") and await owner_val("SELECT label FROM core.roles WHERE key = %s AND tenant_id = %s", (role, admin.tenant_id)) == f"Vai thử {tag}"
    out = await approved(admin, "admin.set_chain", label=f"Chuỗi thử {tag}", approvers=[{"who": "team_lead"}, {"who": "role:director"}])
    assert out["key"].startswith("chuoi_thu") and [s["kind"] for s in out["steps"]] == ["team_lead", "role"]
    old = await approved(admin, "admin.set_chain", key=out["key"], label=f"Chuỗi thử {tag}", steps="owner")   # ô viết tay cũ vẫn nhận
    assert old["key"] == out["key"] and old["steps"] == [{"kind": "owner"}]


async def test_chain_step_and_country_choices_use_reader_words(who: Who) -> None:
    admin = await who("admin")
    steps = {o["value"]: o["label"] for o in await q(admin, "admin.chain_step_options")}
    assert steps["team_lead"] == "Trưởng nhóm của người xin" and steps["owner"] == "Chủ tài khoản" and "role:director" in steps
    assert {o["value"]: o["label"] for o in await q(admin, "admin.country_options")}["VN"] == "Việt Nam"
    teams = [o["label"] for o in await q(admin, "admin.team_options")]
    assert teams and not any(" — " in t for t in teams)                                      # tên đội, không "KD-A — Kinh doanh A"


async def test_category_account_is_chosen_not_typed(who: Who) -> None:
    kt, tag = await who("ketoan"), _tag()
    income = {o["value"] for o in await q(kt, "acct.category_account_options", direction="in")}
    assert income and all(v.startswith("revenue:") for v in income)
    spend = await ok(kt, "acct.set_category", direction="out", label=f"Chi thử {tag}")                # không mã, không tài khoản
    assert spend["code"].startswith("chi_thu") and spend["summary"].endswith("(tài khoản riêng của công ty)")
    bad = await executor.run(kt, "acct.set_category", {"direction": "in", "label": f"Thu thử {tag}"})
    assert bad.status == "invalid"                                                            # khoản thu phải chọn tài khoản doanh thu
    good = await ok(kt, "acct.set_category", direction="in", label=f"Thu thử {tag}", account=sorted(income)[0])
    assert good["code"].startswith("thu_thu")


async def test_tax_bank_and_cost_object_codes_are_made_from_names(who: Who) -> None:
    kt, tag = await who("ketoan"), _tag()
    tax = await ok(kt, "acct.set_tax_category", label=f"Phí thử {tag}", treatment="standard", applies_to=["revenue:thu_nghiem_a", "revenue:thu_nghiem_b"])
    assert tax["code"].startswith("phi_thu")
    assert await owner_val("SELECT applies_to FROM acct.tax_categories WHERE code = %s AND tenant_id = %s", (tax["code"], kt.tenant_id)) == ["revenue:thu_nghiem_a", "revenue:thu_nghiem_b"]
    bank = await ok(kt, "acct.set_bank_account", label=f"VCB thử {tag}")
    assert bank["code"].startswith("VCB-THU") and {o["label"] for o in await q(kt, "acct.bank_account_options")} >= {f"VCB thử {tag}"}
    obj = await ok(kt, "acct.set_cost_object", label=f"Công trình thử {tag}")
    assert obj["code"].startswith("CONG-TRINH-THU")


async def test_period_choices_replace_typed_month_formats(who: Who) -> None:
    kt = await who("ketoan")
    months = await q(kt, "acct.month_options")
    now = today()
    assert months[0] == {"value": f"{now.month:02d}/{now.year}", "label": f"Tháng {now.month}/{now.year}"} and len(months) == 24
    vat = [o["value"] for o in await q(kt, "acct.vat_period_options")]
    assert re.fullmatch(r"Q[1-4]/\d{4}", vat[0]) and f"{now.month:02d}/{now.year}" in vat
    page = await q(kt, "acct.vat_summary", period=vat[0])                                    # giá trị chọn đúng định dạng máy đọc
    assert page["title"]


async def test_opening_batch_default_is_the_only_draft_or_today(who: Who) -> None:
    kt, batch = await who("ketoan"), f"DK-{_tag()}"
    await ok(kt, "acct.add_opening_line", batch=batch, kind="account", account="cash:bank", label="Tiền gửi", debit_vnd=1_000_000)
    assert batch in {o["value"] for o in await q(kt, "acct.opening_draft_options")}
    await ok(kt, "acct.discard_opening_lines", batch=batch)
    assert batch not in {o["value"] for o in await q(kt, "acct.opening_draft_options")}


async def test_hr_defaults_code_and_dates(who: Who) -> None:
    admin, sale = await who("admin"), await fresh_sale(who)
    party = await owner_val("SELECT id FROM parties.parties WHERE user_id = %s AND tenant_id = %s", (sale.user_id, sale.tenant_id))
    first = await ok(admin, "hr.set_employment", party_id=party, title="Nhân viên", started_on=(today() - timedelta(days=30)).isoformat())
    code = await owner_val("SELECT employee_code FROM hr.employments WHERE id = %s", (first["employment_id"],))
    assert code                                                                               # máy đặt mã kế tiếp
    again = await ok(admin, "hr.set_employment", party_id=party, title="Trưởng nhóm")        # không mã, không ngày: giữ mã cũ, bắt đầu hôm nay
    assert await owner_val("SELECT employee_code FROM hr.employments WHERE id = %s", (again["employment_id"],)) == code
    assert await owner_val("SELECT started_on FROM hr.employments WHERE id = %s", (again["employment_id"],)) == today()
    boss = (await ok(admin, "parties.create", display_name=f"Quản lý thử {str(party)[:6]}", kind="person"))["party_id"]
    await ok(admin, "hr.set_assignment", party_id=party, manager_party_id=boss)             # không ngày: hôm nay
    await ok(admin, "hr.end_employment", employment_id=again["employment_id"])               # không ngày nghỉ: hôm nay
    assert await owner_val("SELECT ended_on FROM hr.employments WHERE id = %s", (again["employment_id"],)) == today()


async def test_connect_access_rows_become_the_grant_text() -> None:
    from barista.modules import connect

    inp = connect.Install.model_validate({"name": "App thử", "events": ["lead.assigned", "lead.stage_changed"],
                                          "access": [{"resource": "crm", "scope": "all", "actions": ["read", "write"]},
                                                     {"resource": "ledger", "scope": "team", "actions": ["read"]}]})
    assert connect._grants_text(inp.access, inp.grants) == "crm:all:read,write; ledger:team:read"
    assert inp.events == "lead.assigned, lead.stage_changed"                                 # ô chọn nhiều gửi danh sách, lưu như chữ cũ
    old = connect.Install.model_validate({"name": "App cũ", "grants": "crm:all:read", "events": "lead.assigned"})      # ô viết tay + chữ cũ vẫn nhận
    assert connect._grants_text(old.access, old.grants) == "crm:all:read" and old.events == "lead.assigned"


async def test_old_field_names_still_import_after_relabel(who: Who) -> None:
    """Đổi nhãn "Mã thanh toán" → "Khoản phải thu", "Kỳ (năm-tháng)" → "Tháng": nhãn cũ giữ làm bí danh cho cột Excel."""
    def aliases(name: str, key: str) -> list[str]:
        return {f["key"]: f for f in forms.import_specs(registry.OPS[name])}[key].get("aliases") or []

    assert "Mã thanh toán" in aliases("acct.record_payment", "code") and "Mã khoản phải thu" in aliases("acct.reverse_receipt", "code")
    assert "Kỳ (năm-tháng)" in aliases("kpi.set_target", "period") and "Người (email)" in aliases("kpi.set_target", "user_email")
    assert "Tài khoản sổ cái" in aliases("acct.set_category", "account") and "Tài khoản MISA" in aliases("acct.set_account_map", "misa_account")
    assert "Mã" in aliases("admin.upsert_entity", "code") and "Áp mặc định cho" in aliases("acct.set_tax_category", "applies_to")

