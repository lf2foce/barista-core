"""Lỗi đã tái hiện sau bản đầu 0104/0105 — mỗi test giữ đúng một lỗi, trượt trên bản trước khi sửa. Dữ liệu giả, Postgres thật."""

from typing import Any

import psycopg
import pytest

from barista import seed
from barista.db import db
from barista.kernel import executor, forms, registry
from barista.kernel.clock import today as clock_today  # xuất MISA mặc định = tháng đang kê khai (18/11/2026) ⇒ test nêu tháng này
from barista.kernel.errors import Invalid
from barista.workspaces import manifest
from tests import money_source
from tests.conftest import Who, fresh_sale, ok, owner_val, q
from tests.money_source import account, internal, party, payable, prepared


@pytest.fixture(autouse=True)
def _source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nguồn tiền giả của lõi (``tests/money_source.py``); test của gói Hoa hồng chạy lại các test này với nguồn thật."""
    money_source.use(monkeypatch)


# ── 1. Lệnh chi "cần tra soát": ngân hàng xác nhận chưa chuyển thì huỷ được, có căn cứ; huỷ tự động vẫn dừng ──

async def test_unknown_payout_can_be_cancelled_with_investigation_result_and_releases_reservation(who: Who):
    pid, _, _ = await payable(who)
    payout = await prepared(who, pid, 4_000_000)
    ketoan = await who("ketoan")
    await ok(ketoan, "acct.mark_payout_unknown", payout_id=payout["payout_id"], reason="Ngân hàng chưa phản hồi")
    bank = await account(who, "cash:bank")
    no_result = await executor.run(ketoan, "acct.cancel_payout", {"payout_id": str(payout["payout_id"]), "reason": "Huỷ"})
    assert no_result.status == "invalid"
    await ok(ketoan, "acct.cancel_payout", payout_id=payout["payout_id"], reason="Huỷ theo tra soát",
             investigation="Ngân hàng xác nhận lệnh chưa được gửi, phiếu tra soát giả 01")
    assert await owner_val("SELECT status FROM acct.payouts WHERE id=%s", (payout["payout_id"],)) == "cancelled"
    assert "phiếu tra soát" in await owner_val("SELECT status_note FROM acct.payouts WHERE id=%s", (payout["payout_id"],))
    assert await account(who, f"payable:{pid}") == -10_000_000
    assert await account(who, "cash:bank") == bank
    assert (await internal(await who("admin"), "acct.position", party_id=pid))["owed"] == 10_000_000


async def test_automatic_void_stops_on_unknown_payout_instead_of_cancelling(who: Who):
    pid, _, data = await payable(who)
    payout = await prepared(who, pid, 4_000_000)
    await ok(await who("ketoan"), "acct.mark_payout_unknown", payout_id=payout["payout_id"], reason="Đang tra soát")
    with pytest.raises(Invalid):
        await internal(await who("admin"), "acct.void_payables", source=data["source"], refs=[data["source_ref"]],
                       action_key="cancel", reason="Huỷ giao dịch fixture")
    assert await owner_val("SELECT status FROM acct.payouts WHERE id=%s", (payout["payout_id"],)) == "unknown"


async def test_trigger_requires_a_note_to_leave_unknown_as_cancelled(who: Who):
    pid, _, _ = await payable(who)
    payout = await prepared(who, pid, 1_000_000)
    await ok(await who("ketoan"), "acct.mark_payout_unknown", payout_id=payout["payout_id"], reason="Đang tra soát")
    with pytest.raises(psycopg.errors.RaiseException, match="tra soát"):
        async with db.owner_tx() as conn:
            await conn.execute("UPDATE acct.payouts SET status='cancelled' WHERE id=%s", (payout["payout_id"],))


# ── 3. Xuất MISA: mọi tiền tố 0104 có ánh xạ mẫu; bản cài đang chạy được thêm phần thiếu, không đè phần kế toán đã sửa ──

async def test_sample_account_map_covers_settlement_prefixes_and_upgrade_only_adds_missing(who: Who, demo: dict[str, Any]):
    ketoan = await who("ketoan")
    tid = ketoan.tenant_id
    mapped = {r["ledger_prefix"] for r in (await q(ketoan, "acct.account_map"))["rows"]}
    assert {"payable", "advance", "recovery", "tax:withheld", "revenue:advance_charge"} <= mapped
    await ok(ketoan, "acct.set_account_map", ledger_prefix="payable", misa_account="331", label="Kế toán đã chốt")
    async with db.owner_tx() as conn:
        await conn.execute("DELETE FROM acct.account_map WHERE tenant_id=%s AND ledger_prefix='recovery'", (tid,))
    added = await seed.ensure_account_map(tid)
    assert added == ["recovery"]
    rows = {r["ledger_prefix"]: r["misa_account"] for r in (await q(ketoan, "acct.account_map"))["rows"]}
    assert rows["payable"] == "331" and rows["recovery"]


def test_sample_account_map_covers_every_account_the_finance_money_path_posts_to():
    mapped = {p for p, _, _ in seed.ACCOUNT_MAP}
    for code in ("cash:bank", "unapplied:bank", "payout:clearing", "payable:x", "advance:x", "recovery:x", "tax:withheld", "revenue:advance_charge",
                 "receivable:customers", "revenue:services", "expense:marketing", "tax:vat_input", "tax:vat_output"):
        assert code in mapped or code.split(":")[0] in mapped, code


async def test_misa_export_has_no_unmapped_settlement_lines_and_still_reports_a_missing_prefix(who: Who):
    pid, _, _ = await payable(who, amount=20_000_000, tax=2_000_000)
    await prepared(who, pid, 1_000_000)
    ketoan = await who("ketoan")
    page = await q(ketoan, "acct.misa_export", month=clock_today().strftime("%m/%Y"))
    mine = [r for r in page["rows"] if r["memo"] in ("Quyền fixture", "Chi fixture")]
    assert mine and all("????" not in (r["debit"], r["credit"]) for r in mine)
    async with db.owner_tx() as conn:
        await conn.execute("DELETE FROM acct.account_map WHERE tenant_id=%s AND ledger_prefix='payable'", (ketoan.tenant_id,))
    try:
        page = await q(ketoan, "acct.misa_export", month=clock_today().strftime("%m/%Y"))
        kpi = next(k for k in page["kpis"] if k["label"] == "Tài khoản chưa ánh xạ")
        assert kpi["value"] > 0 and kpi["tone"] == "red"
    finally:
        await ok(ketoan, "acct.set_account_map", ledger_prefix="payable", misa_account="3388", label="Khôi phục")


# ── 5. Màn Phải trả & ứng + mọi form mới dùng nhãn tiếng Việt ──

async def test_payables_screen_names_recipient_entity_remaining_and_vietnamese_kind(who: Who):
    pid, p, _ = await payable(who, amount=10_000_000)
    await prepared(who, pid, 4_000_000)
    ketoan = await who("ketoan")
    name = await owner_val("SELECT display_name FROM parties.parties WHERE id=%s", (pid,))
    page = await q(ketoan, "acct.payables")
    row = next(r for r in page["rows"] if str(r["id"]) == str(p["payable_id"]))
    assert row["party_name"] == name and row["entity_name"] and row["left_vnd"] == 6_000_000
    cols = {c["key"]: c for c in page["columns"]}
    assert {"party_name", "entity_name", "left_vnd"} <= set(cols)
    assert cols["kind"].get("labels", {}).get("payable") == "Phải trả"
    lead = await who("truonga")
    keys = {s["key"] for ws in manifest(lead, "Mẫu")["workspaces"] for s in ws["screens"]}
    assert "payables" not in keys


NEW_COMMANDS = ("acct.request_payment", "acct.mark_payout_unknown", "acct.cancel_payout", "acct.recover_payment", "parties.create",
                "parties.update")


@pytest.mark.parametrize("name", NEW_COMMANDS)
def test_new_forms_use_vietnamese_labels_and_hide_ids(name: str):
    def walk(specs: list[dict[str, Any]]) -> None:
        for f in specs:
            assert f["label"] != f["key"], f"{name}.{f['key']} chưa có nhãn tiếng Việt"
            assert f["kind"] != "id" or f["hidden"], f"{name}.{f['key']}: id phải ẩn"
            assert not (f["key"].endswith("_id") and not f["hidden"] and f["kind"] != "select"), f"{name}.{f['key']}: id lộ ra form"
            if f["kind"] == "rows":
                walk(f["fields"])
    walk(forms.field_specs(registry.OPS[name]))


# ── 6. Chuỗi duyệt mặc định có sẵn sau phát hành: xem test_upgrade.py; ở đây giữ quyền danh mục cho giám đốc ──

async def test_director_can_read_party_directory(who: Who):
    gd = await who("giamdoc")
    assert (await executor.query(gd, "parties.list", {})).status == "ok"


async def test_locked_member_counts_as_inactive_without_a_second_flag(who: Who):
    admin, sale = await who("admin"), await fresh_sale(who)
    pid = (await internal(admin, "parties.ensure_member", user_id=sale.user_id))["party_id"]
    assert (await internal(admin, "parties.resolve_for_payment", party_id=pid))["active"] is True
    async with db.owner_tx() as conn:
        await conn.execute("UPDATE core.memberships SET active=false WHERE tenant_id=%s AND user_id=%s", (sale.tenant_id, sale.user_id))
    try:
        assert (await internal(admin, "parties.resolve_for_payment", party_id=pid))["active"] is False
    finally:
        async with db.owner_tx() as conn:
            await conn.execute("UPDATE core.memberships SET active=true WHERE tenant_id=%s AND user_id=%s", (sale.tenant_id, sale.user_id))


async def test_party_directory_screen_and_member_cannot_be_stopped_here(who: Who):
    admin, ketoan, sale = await who("admin"), await who("ketoan"), await fresh_sale(who)
    screens = {s["key"]: s for ws in manifest(ketoan, "Mẫu")["workspaces"] for s in ws["screens"]}
    assert {a["command"] for a in screens["parties"]["actions"]} == {"parties.create"}
    assert {a["command"] for a in screens["payouts"]["row_actions"]} >= {"acct.mark_payout_unknown", "acct.cancel_payout"}
    own = (await internal(admin, "parties.ensure_member", user_id=sale.user_id))["party_id"]
    refused = await executor.run(ketoan, "parties.update", {"party_id": str(own), "display_name": sale.name, "active": False})
    assert refused.status == "invalid" and "Quản trị" in (refused.error or "")
    outside = await party(who)
    pid, _, _ = await payable(who, pid=outside, amount=1_000_000)
    stopped = await ok(ketoan, "parties.update", party_id=pid, display_name="Tên mới giả", active=False)
    assert "1.000.000" in stopped["warning"]
    assert "Tên mới giả" not in {o["label"] for o in await q(ketoan, "parties.options")}
