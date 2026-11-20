"""SỨC KHOẺ DỮ LIỆU gom phần của MỘT GÓI NGÀNH (0078) — bản LÕI (ADR 0190) của ``barista-packs:commission/tests/test_health.py``: module ``health`` không biết ngành
nào, chỉ gom truy vấn nội bộ ``<module>.health_report`` mỗi module tự khai (ở đây: gói thế vai kiểm xuyên đơn × Tài chính qua họ ``receivable``); mỗi loại
lỗi của gói được BẮT (đếm tăng khi gây lỗi đó), gói tắt ⇒ "chưa kiểm" chứ không thành dòng 0. Phần không cần gói (ai được xem, lỗ hổng lead, sự kiện lỗi)
nằm ở ``tests/test_health.py``."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, LiteralString
from uuid import UUID

from barista import seed
from barista.db import db
from barista.kernel import executor, registry
from barista.kernel.clock import today
from barista.modules import health, mkt
from tests.conftest import Who, owner_val
from tests.standin import helpers, pack
from tests.test_health import _card, _issues, _list
from tests.test_packs_industry import PEOPLE

NO_FEE, OVERDUE = "đơn đã ký chưa có khoản phí", "khoản phí đơn quá hạn mà khách chưa trả đồng nào"


async def _exec(sql: LiteralString, params: Any = None) -> None:
    async with db.owner.connection() as conn:
        await conn.execute(sql, params)


async def test_the_packs_part_is_gathered_only_for_company_wide_roles_and_only_where_the_pack_is_on(who: Who) -> None:
    """Thẻ của công ty bật gói có phần của gói (dòng lỗi + nguồn + ghi chú); người phạm vi hẹp bị từ chối; công ty không bật gói: phần đó ghi "chưa kiểm"."""
    gd = await who("giamdoc")
    card = await _card(gd)
    assert {NO_FEE, OVERDUE} <= set(await _issues(gd))
    assert any(i["title"] == "Phí đơn ghi nhận" for i in _list(card, "Độ mới theo nguồn"))
    notes = next(b for b in card["blocks"] if b["type"] == "callout" and b["title"] == "Cách đọc")["facts"]
    assert any(n.startswith("Phí đơn ghi nhận chỉ để biết") for n in notes) and not any("đơn dịch vụ" in n for n in notes if n.startswith("Chưa kiểm"))
    for local in ("sale1", "truonga", "ketoan"):
        assert (await executor.query(await who(local), "health.overview", {})).status == "denied", local
    domain = f"h{uuid.uuid4().hex[:6]}.demo"
    tid = await seed.create_tenant(f"Công ty thử {domain}", domain, people=PEOPLE, packs=("crm", "finance"))
    off = await _card(await seed.who(tid, "giamdoc", domain))
    assert not {NO_FEE, OVERDUE} & {i["title"].split(" ", 1)[1] for i in _list(off, "Bản ghi cần sửa")}        # gói tắt ⇒ không có dòng 0
    off_notes = next(b for b in off["blocks"] if b["type"] == "callout" and b["title"] == "Cách đọc")["facts"]
    assert any(n.startswith("Chưa kiểm (gói chưa bật)") and "đơn dịch vụ" in n for n in off_notes)


async def test_money_gaps_of_the_pack_are_counted(who: Who, company: UUID) -> None:
    """Đơn đã ký mà khoản phí bên Tài chính bị huỷ ⇒ "đơn đã ký chưa có khoản phí" tăng 1; một khoản phí quá hạn chưa thu đồng nào ⇒ dòng quá hạn tăng 1."""
    gd = await who("giamdoc")
    o = await helpers.signed(who)
    before = await _issues(gd)
    inv = await owner_val("SELECT id FROM acct.receivables WHERE tenant_id = %s AND code = %s", (company, o["inv"]))
    assert inv is not None
    status = await owner_val("SELECT status FROM acct.receivables WHERE id = %s", (inv,))
    fake = uuid.uuid4()
    try:
        await _exec("UPDATE acct.receivables SET status = 'void' WHERE id = %s", (inv,))
        await _exec("""INSERT INTO acct.receivables (id, tenant_id, code, source, source_ref, label, party, nature, amount_vnd, fee_vnd,
                        revenue_account, issued_on, due_on) VALUES (%s, %s, %s, %s, %s, 'Phí thử', 'Khách thử', 'revenue',
                        50000000, 50000000, %s, %s, %s)""",
                    (fake, company, f"PD-THU-{fake.hex[:6].upper()}", pack.FEE, str(fake), pack.REVENUE, today() - timedelta(days=40),
                     today() - timedelta(days=10)))
        after = await _issues(gd)
        assert after[NO_FEE] == before[NO_FEE] + 1
        assert after[OVERDUE] == before[OVERDUE] + 1
        item = next(i for i in _list(await _card(gd), "Bản ghi cần sửa") if i["title"].endswith(NO_FEE))
        assert o["code"] in item["subtitle"] or after[NO_FEE] > 5                       # dòng nêu mã đơn để sửa (tối đa 5 mã)
        why = next(b for b in (await _card(gd))["blocks"] if b["type"] == "callout" and b["title"].startswith("Vì sao"))
        assert any("đơn ký chưa có khoản phí ⇒ doanh thu ghi nhận" in f for f in why["facts"])
    finally:
        await _exec("UPDATE acct.receivables SET status = %s WHERE id = %s", (status, inv))
        await _exec("DELETE FROM acct.receivables WHERE id = %s", (fake,))
    assert (await _issues(gd))[NO_FEE] == before[NO_FEE]


def test_assess_only_gathers_what_modules_report() -> None:
    """0078: health chỉ gom phần của từng module (khuôn chung) — nguồn trễ ⇒ cảnh báo + lý do của chính module; gói tắt ⇒ "chưa kiểm", không
    thành dòng 0; ghi chú ngưỡng do module khai; sự kiện lỗi (lõi) luôn có."""
    now = datetime.now(UTC)
    d: dict[str, Any] = {"admin": {"dead": 0, "retrying": 0, "oldest": None}, "reports": {
        "crm": {"rank": 10, "notes": ["Lead mỗi nguồn đang hoạt động trễ khi > 48 giờ không có lead mới."], "issues": [], "fresh": [
            {"title": "Lead · Quảng cáo", "at": now - timedelta(hours=47), "stale": False, "why": "x", "open": "crm.leads"},
            {"title": "Lead · Sự kiện", "at": now - timedelta(hours=49), "stale": True, "why": "Lead sự kiện trễ", "open": "crm.leads"},
            {"title": "Lead · Nhập file", "at": now - timedelta(days=90), "stale": False, "tone": "muted", "open": "crm.leads"}]},
        "mkt": mkt.spend_health([{"id": "c1", "code": "FB-1", "name": "x", "status": "running", "last_day": today() - timedelta(days=3), "spend": 0}],
                                {"c1": 2}),
        pack.MODULE: None, "khong_co_module": None}}
    r = health.assess(d, now)
    assert {f["title"]: f["tone"] for f in r["fresh"]} == {"Lead · Quảng cáo": "ok", "Lead · Sự kiện": "warn", "Lead · Nhập file": "muted",
                                                           "Chi phí quảng cáo (chiến dịch đang chạy)": "warn"}
    assert {i["key"]: i["n"] for i in r["issues"]} == {"campaign_no_spend": 1, "dead_deliveries": 0, "system_errors": 0}
    assert sorted(r["unchecked"]) == sorted([registry.MODULES[pack.MODULE].lower(), "khong_co_module"])   # gói tắt ⇒ chưa kiểm, không có dòng 0
    assert len(r["why"]) == 3 and r["notes"][0].startswith("Lead mỗi nguồn")                   # 2 nguồn trễ + 1 chiến dịch thiếu chi phí
