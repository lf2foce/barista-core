"""Che liên hệ tới khi bấm nhận + báo người phụ trách chiến dịch khi lead bị thu hồi (0214).

· luật chia có bước bấm nhận bật thêm "che": lead đang chờ nhận ⇒ MỌI người xem (người được chia, quản lý) thấy số đã che ở danh sách, thẻ lead,
  thẻ buổi sáng, ⌘K; nút trên thẻ là "Nhận lead"; bấm nhận ⇒ thấy đủ số;
· che mà không có bước bấm nhận ⇒ từ chối (không có lúc nào để mở số);
· lead của chiến dịch bị thu hồi ⇒ người phụ trách chiến dịch nhận MỘT tin gộp có tên chiến dịch, không tên / SĐT khách;
· CANH CẤU TRÚC: câu SQL mới của crm đọc SĐT / email khách phải đi qua ``crm/reveal.py`` hoặc khai lý do ở ``ALLOW``.

Mỗi test dựng MỘT công ty riêng (luật chia là cấu hình cả công ty)."""

import ast
import itertools
import re
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from barista import seed
from barista.db import db
from barista.kernel import executor
from barista.kernel.clock import today
from barista.kernel.fields import phone_display
from barista.kernel.principal import Principal, system_principal
from barista.modules.crm.reveal import mask_phone
from tests.conftest import fresh_phone, ok, owner_val, q

_seq = itertools.count(1)
PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("truong", "Trưởng nhóm A", "team_lead", "KD-A", True),
          ("a", "Sale A", "sale", "KD-A", False), ("b", "Sale B", "sale", "KD-A", False), ("mk", "Người chạy quảng cáo", "marketer", "MKT", False)]


async def _company() -> tuple[UUID, dict[str, Principal]]:
    domain = f"gate{next(_seq)}.demo"
    tid = await seed.create_tenant(f"Công ty che số {domain}", domain, people=PEOPLE)
    return tid, {k[0]: await seed.who(tid, k[0], domain) for k in PEOPLE}


async def _rule(admin: Principal, **kw: Any) -> None:
    await ok(admin, "leadflow.set_rule", **{"name": "Chia lead", "effective_from": (today() - timedelta(days=1)).isoformat(), "cap_uncalled": 0,
                                             "roles": ["sale"], "first_call_min": 15, "max_recalls": 2, "days": "all", "open_at": "00:00",
                                             "close_at": "24:00", "remind_before_min": 0, "team_code": "KD-A", **kw})


def _row(page: dict[str, Any], lead_id: str) -> dict[str, Any]:
    rows = page["rows"] if "rows" in page else page["page"]["rows"]
    return next(r for r in rows if str(r["id"]) == lead_id)


def _callouts(page: dict[str, Any]) -> list[dict[str, Any]]:
    return [b for b in page["blocks"] if b["type"] == "callout"]


async def test_contact_stays_hidden_until_the_receiver_accepts() -> None:
    _tid, pp = await _company()
    bad = await executor.run(pp["admin"], "leadflow.set_rule", {"name": "Che mà không nhận", "effective_from": today().isoformat(), "roles": ["sale"],
                                                                "team_code": "KD-A", "hide_contact": True})
    assert bad.status == "invalid" and "bước bấm nhận" in (bad.error or "")
    await _rule(pp["admin"], accept_min=5, hide_contact=True)
    phone = fresh_phone()
    got = await ok(pp["admin"], "leadflow.intake", name="Khách che số", phone=phone)
    lid, holder = got["lead_id"], next(p for p in (pp["a"], pp["b"]) if str(p.user_id) == got["assigned_to"])
    stored = await owner_val("SELECT phone FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id WHERE l.id = %s", (lid,))
    full, masked = phone_display(stored), mask_phone(stored)   # so CHUỖI (không "4 số cuối không xuất hiện": số thử 0100… lặp chữ số ⇒ đỏ oan)
    assert masked and masked.count("•") == 4 and masked != full

    for viewer in (holder, pp["truong"]):                                    # người được chia lẫn quản lý: số đã che
        row = _row(await q(viewer, "crm.leads"), lid)
        assert row["phone"] == masked and row["contact_hidden"] is True
    card = await q(holder, "crm.journey", lead_id=lid)
    assert masked in card["subtitle"] and full not in card["subtitle"]
    gate = next(c for c in _callouts(card) if c["title"].startswith("Chưa nhận lead"))
    assert gate["actions"][0]["command"] == "leadflow.accept"
    assert not next(c for c in _callouts(await q(pp["truong"], "crm.journey", lead_id=lid)) if c["title"].startswith("Chưa nhận lead"))["actions"]
    brief = await q(holder, "crm.brief")
    mine = next(c for c in _callouts(brief) if "Khách che số" in c["title"])
    assert masked in mine["title"] and full not in mine["title"] and mine["actions"][0]["command"] == "leadflow.accept"
    for term in ("Khách che số", "khach che so", phone[-6:]):                # ⌘K: có dấu, không dấu, theo số — số vẫn che
        found = [i for b in (await q(holder, "core.find", q=term))["blocks"] if b["type"] == "list" for i in b["items"]]
        assert found and all(full not in i.get("subtitle", "") for i in found) and any(masked in i.get("subtitle", "") for i in found), term

    await ok(holder, "leadflow.accept", lead_id=lid)                         # cam kết ⇒ thấy đủ số
    for viewer in (holder, pp["truong"]):
        row = _row(await q(viewer, "crm.leads"), lid)
        assert "•" not in (row["phone"] or "") and "contact_hidden" not in row
    assert not [c for c in _callouts(await q(holder, "crm.journey", lead_id=lid)) if c["title"].startswith("Chưa nhận lead")]


async def test_without_the_switch_an_offered_lead_shows_its_number() -> None:
    _tid, pp = await _company()
    await _rule(pp["admin"], accept_min=5)
    got = await ok(pp["admin"], "leadflow.intake", name="Khách không che", phone=fresh_phone())
    holder = next(p for p in (pp["a"], pp["b"]) if str(p.user_id) == got["assigned_to"])
    assert await owner_val("SELECT status FROM leadflow.lead_state WHERE lead_id = %s", (got["lead_id"],)) == "offered"
    assert "•" not in _row(await q(holder, "crm.leads"), got["lead_id"])["phone"]


async def test_campaign_owner_hears_when_its_leads_are_recalled() -> None:
    tid, pp = await _company()
    await _rule(pp["admin"], accept_min=5)
    await ok(pp["mk"], "mkt.upsert_campaign", code="FB-GATE", name="Chiến dịch che số", channel="facebook", budget_vnd="10tr",
             start_on=today().isoformat())
    got = await ok(pp["admin"], "leadflow.intake", name="Khách bị thu hồi", phone=fresh_phone(), campaign_code="FB-GATE")
    async with db.owner.connection() as conn:
        await conn.execute("UPDATE leadflow.lead_state SET accept_due = now() - interval '1 minute' WHERE lead_id = %s", (got["lead_id"],))
    out = await ok(system_principal(tid), "leadflow.enforce_sla")
    assert out["accept_missed"] == 1
    note = await owner_val("SELECT row_to_json(n) FROM core.notifications n WHERE user_id = %s AND kind = 'lead_escalated'", (pp["mk"].user_id,))
    assert note and "Lead của chiến dịch bị thu hồi: 1 lead" in note["title"] and "Chiến dịch che số" in note["body"]
    assert "Khách bị thu hồi" not in note["body"]                             # không tên khách cho marketing


# ── Canh cấu trúc: SQL đọc liên hệ khách trong crm phải qua reveal ──

CRM = Path(__file__).resolve().parents[1] / "src" / "barista" / "modules" / "crm"
ALIASED = re.compile(r"\bc\.(phone|email)\b")                       # khách gắn bí danh c (mọi chỗ crm nối crm.customers c)
BARE = re.compile(r"\bcrm\.customers\b", re.I)
COLUMN = re.compile(r"\b(phone|email)\b")


def _reads_contact(sql: str) -> bool:
    """Câu SQL chạm SĐT / email của KHÁCH (không phải email nhân viên ``u.email`` hay loại chăm sóc 'email')."""
    return bool(ALIASED.search(sql)) or bool(BARE.search(sql) and COLUMN.search(re.sub(r"\b\w+\.(phone|email)\b", "", sql)))
# Đọc liên hệ nhưng KHÔNG đưa ra người xem (hoặc đưa ra trước khi lead tới tay ai) — lý do từng chỗ:
ALLOW = {
    ("identity.py", "_customer_of"): "khớp khách khi nạp lead (so số, không trả số ra)",
    ("identity.py", "_party_of_lead"): "dùng nội bộ để thêm liên hệ / nối giao dịch, không trả số",
    ("calls.py", "leads_for_phones"): "tổng đài: bên gọi đã có số (nhật ký cuộc gọi), chỉ trả mã lead",
    ("deal_link.py", "lead_match_keys"): "chỉ dấu băm SHA-256 cho kênh quảng cáo",
    ("reports.py", "health_report"): "chỉ trả TÊN khách nghi trùng số, không trả số",
    ("routing.py", "intake_lead"): "nạp lead mới (chưa ai giữ), không trả số",
    ("leads.py", "_leads_existing"): "nhập Excel nhận ra lead đã có theo SĐT người nhập gõ (0226): so số, chỉ trả TÊN khách để báo trùng, không trả số",
    ("leads.py", "create_lead"): "tạo lead (người nhập tự gõ số), không trả số",
    ("deal_link.py", "link_deal"): "giao dịch từ hợp đồng: khớp khách theo số người nhập gõ, trả mã khách / lead",
    ("deal_link.py", "_link_deal_from_lead"): "bổ sung số còn thiếu cho khách khi nối giao dịch, trả mã",
    ("journey.py", "add_contact"): "thêm liên hệ (người nhập tự gõ), chỉ kiểm khách đã có số chưa — trả câu xác nhận, không số",
}


def _functions() -> list[tuple[str, str, str]]:
    out = []
    for f in sorted(CRM.glob("*.py")):
        src = f.read_text()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = ast.get_source_segment(src, node) or ""
                sql = [c.value for c in ast.walk(node) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
                if any(_reads_contact(s) for s in sql):
                    out.append((f.name, node.name, body))
    return out


def test_every_crm_read_of_customer_contacts_goes_through_reveal() -> None:
    found = _functions()
    leaks = [(f, n) for f, n, body in found if "reveal." not in body and (f, n) not in ALLOW]
    assert not leaks, f"SQL đọc SĐT / email khách mà không che theo 0214 (dùng crm/reveal.py hoặc khai lý do ở ALLOW): {leaks}"
    stale = set(ALLOW) - {(f, n) for f, n, _ in found}
    assert not stale, f"ALLOW còn chỗ không còn đọc liên hệ — xoá: {stale}"
