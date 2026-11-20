"""Rà vai marketing 18/11/2026: chi phí dừng nhập (hoặc người xem chưa phụ trách chiến dịch nào) thì màn marketing không trống im lặng — kỳ mặc định
lùi về 30 ngày tới ngày có chi phí gần nhất (ngày ghi trên tiêu đề phụ), lưu ý nói chi phí dừng ở ngày nào; người phạm vi hẹp được nói vì sao không thấy."""

import uuid
from datetime import timedelta
from typing import Any

from barista import seed
from barista.kernel.clock import today
from tests.conftest import Who, approved, ok, owner_val, q


async def _fresh_marketer(who: Who) -> Any:
    """Marketing mới, không làm trưởng đội nào ⇒ phạm vi ``team`` của vai chỉ là chiến dịch chính mình chạy."""
    admin = await who("admin")
    email = f"mkt-latest-{uuid.uuid4().hex[:6]}@{seed.DOMAIN}"
    await approved(admin, "admin.invite_user", email=email, name="Marketing Thử Kỳ", role="marketer")   # vai không phải sale ⇒ qua chủ tài khoản
    return await seed.who(await owner_val("SELECT m.tenant_id FROM core.memberships m JOIN core.users u ON u.id = m.user_id WHERE u.email = %s", (email,)),
                          email.split("@")[0], seed.DOMAIN)


def _facts(page: dict[str, Any], title: str) -> list[str]:
    return [f for b in page["blocks"] if b["type"] == "callout" and b["title"] == title for f in b["facts"]]


def _rows(page: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for b in page["blocks"] if b["type"] == "table" for r in b["page"]["rows"]]


async def test_no_campaign_in_scope_says_why_instead_of_a_silent_zero(who: Who) -> None:
    mkt = await _fresh_marketer(who)
    page = await q(mkt, "mkt.overview")
    assert any("Bạn chưa thấy chiến dịch nào" in f for f in _facts(page, "Cần biết trước khi đọc số"))
    assert "Bạn chưa thấy chiến dịch nào" in (await q(mkt, "mkt.campaigns"))["empty"]
    assert "Chưa có chi phí nào" in (await q(mkt, "mkt.spend"))["empty"]


async def test_stale_spend_moves_the_default_period_and_names_the_last_day(who: Who) -> None:
    mkt = await _fresh_marketer(who)
    code = f"T-OLD-{uuid.uuid4().hex[:5]}".upper()
    last = today() - timedelta(days=40)
    await ok(mkt, "mkt.upsert_campaign", code=code, name="Chiến dịch dừng nhập", channel="facebook", offering_code="HSR",
             start_on=(last - timedelta(days=5)).isoformat())
    await ok(mkt, "mkt.record_spend", campaign_code=code, day=last.isoformat(), amount_vnd="3tr")

    page = await q(mkt, "mkt.overview")   # kỳ mặc định: 30 ngày tới ngày có chi phí gần nhất
    assert f"{last:%d/%m/%Y}" in page["subtitle"]
    assert [r["code"] for r in _rows(page)] == [code]
    assert any(f"mới nhập tới ngày {last:%d/%m/%Y}" in f for f in _facts(page, "Cần biết trước khi đọc số"))
    # định nghĩa cố định nằm SAU bảng, không chắn giữa ô số và bảng
    kinds = [b["type"] for b in page["blocks"]]
    assert kinds.index("table") < max(i for i, b in enumerate(page["blocks"]) if b["type"] == "callout" and b["title"] == "Cách tính số")

    old = await q(mkt, "mkt.overview", period="last_30")   # kỳ theo lịch vẫn chọn được — trống nhưng nói vì sao
    assert not _rows(old) and any("kỳ đang xem chưa có chi phí nào" in f for f in _facts(old, "Cần biết trước khi đọc số"))
    spend = await q(mkt, "mkt.spend")   # chi phí dừng nhập: màn mở ra 30 ngày TỚI ngày nhập gần nhất (không trống), KPI nói rõ ngày
    assert [r["code"] for r in spend["rows"]] == [code] and f"tới {last:%d/%m/%Y}" in spend["kpis"][0]["hint"]
