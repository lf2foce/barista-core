"""Sức khoẻ dữ liệu (họp khách 31/10 — docs/clients/<khách>): mỗi loại lỗi được BẮT (đếm tăng khi gây lỗi đó), độ mới lấy từ mốc giờ thật, chỉ người phạm vi
cả công ty xem. Lỗi dựng bằng lệnh thật khi có lệnh; còn lại ghi thẳng bằng quyền chủ DB rồi dọn lại để không lệch test khác."""

import uuid
from datetime import timedelta
from typing import Any

from barista.kernel.clock import today
from barista.modules import mkt
from tests.conftest import Who, fresh_phone, ok, own_team, owner_val, q


async def _card(p: Any) -> dict[str, Any]:
    return await q(p, "health.overview")


def _list(card: dict[str, Any], title: str) -> list[dict[str, Any]]:
    return next(b for b in card["blocks"] if b["type"] == "list" and b["title"] == title)["items"]


async def _issues(p: Any) -> dict[str, int]:
    """{"lead đang theo chưa gắn dự án quan tâm": 3, …} — tiêu đề dòng là "<số> <loại lỗi>"."""
    out = {}
    for it in _list(await _card(p), "Bản ghi cần sửa"):
        n, label = it["title"].split(" ", 1)
        out[label] = int(n)
    return out


async def test_campaign_without_spend_is_counted(who: Who) -> None:
    gd, mkt, sale1 = await who("giamdoc"), await who("mkt"), await who("sale1")
    label = "chiến dịch có lead nhưng chưa có chi phí (30 ngày)"
    before = await _issues(gd)
    code = f"T-NOSPEND-{uuid.uuid4().hex[:5]}".upper()
    await ok(mkt, "mkt.upsert_campaign", code=code, name="Chiến dịch quên nhập chi phí", channel="facebook", offering_code="HSR")
    await ok(sale1, "crm.create_lead", name=f"Khách QC {code}", phone=fresh_phone(), offering_code="HSR", source="ads", campaign_code=code)
    assert (await _issues(gd))[label] == before[label] + 1
    item = next(i for i in _list(await _card(gd), "Bản ghi cần sửa") if i["title"].endswith(label))
    assert code in item["subtitle"] and item["open"]["query"] == "mkt.campaigns"
    await ok(mkt, "mkt.record_spend", campaign_code=code, day=today().isoformat(), amount_vnd="2tr")
    assert (await _issues(gd))[label] == before[label]                                       # có chi phí ⇒ hết lỗi


async def test_freshness_comes_from_real_timestamps(who: Who, demo: dict[str, Any]) -> None:
    from barista.kernel.clock import VN
    gd, sale1 = await who("giamdoc"), await who("sale1")
    await ok(sale1, "crm.create_lead", name=f"Khách mới tinh {uuid.uuid4().hex[:4]}", phone=fresh_phone(), offering_code="HSR", source="referral")
    last = await owner_val("SELECT max(created_at) FROM crm.leads WHERE tenant_id = %s AND source = 'referral'", (demo["tenant"],))
    row = next(i for i in _list(await _card(gd), "Độ mới theo nguồn") if i["title"] == "Lead · Giới thiệu")
    assert f"{last.astimezone(VN):%d/%m %H:%M}" in row["subtitle"] and row["badge"] == "vừa xong" and row["tone"] == "ok"


def test_one_fresh_campaign_does_not_hide_a_late_one() -> None:
    """Review 31/10: chiến dịch A có chi phí hôm nay, B trễ 10 ngày ⇒ vẫn báo trễ, nêu đúng B."""
    camp = {"name": "x", "status": "running", "spend": 1}
    row = mkt.spend_health([{**camp, "id": "a", "code": "FB-A", "last_day": today()},
                            {**camp, "id": "b", "code": "FB-B", "last_day": today() - timedelta(days=10)}], {})["fresh"][0]
    assert row["stale"] and "1/2" in row["subtitle"] and "FB-B" in row["subtitle"] and "FB-A" not in row["subtitle"]


# ── Bản LÕI (ADR 0190) của phần trung tính trong ``barista-packs:commission/tests/test_health.py`` — không cần đối tượng của gói ngành nào ──

async def test_only_company_wide_roles_see_it(who: Who) -> None:
    from barista.kernel import executor

    for local in ("giamdoc", "admin"):
        card = await _card(await who(local))
        assert card["title"] == "Sức khoẻ dữ liệu"
        assert any(b["type"] == "callout" and b["title"] == "Cách đọc" and "48 giờ" in b["facts"][0] for b in card["blocks"])
    for local in ("sale1", "truonga", "ketoan", "mkt"):
        r = await executor.query(await who(local), "health.overview", {})
        assert r.status == "denied", local


async def test_lead_gaps_are_counted(who: Who) -> None:
    """Từng loại thiếu của lead được BẮT: gây đúng một lỗi ⇒ đúng dòng đó tăng 1. Chữ theo từ vựng công ty (0124) — không viết cứng "dự án" / "sản phẩm"."""
    from barista.db import db
    from barista.kernel import vocab
    from tests.conftest import fresh_sale

    gd, sale1, admin = await who("giamdoc"), await who("sale1"), await who("admin")
    word = vocab.label("offering", gd.vocab, lower=True)
    no_offering, bad_text = f"lead đang theo chưa gắn {word} quan tâm", f"lead ghi {word} không khớp danh mục"
    inactive, dup = "lead đang theo của người đã nghỉ / bị khoá", "cặp khách nghi trùng (cùng tên, SĐT gần giống)"
    before = await _issues(gd)
    await ok(sale1, "crm.create_lead", name=f"Khách chưa chọn {uuid.uuid4().hex[:4]}", phone=fresh_phone())
    step = await _issues(gd)
    assert step[no_offering] == before[no_offering] + 1 and step[bad_text] == before[bad_text]
    lead = await ok(sale1, "crm.create_lead", name=f"Khách gõ tên lạ {uuid.uuid4().hex[:4]}", phone=fresh_phone())
    async with db.owner.connection() as conn:
        await conn.execute("UPDATE crm.leads SET offering_id = NULL, offering_code = 'Tên gõ sai' WHERE id = %s", (lead["lead_id"],))
    after = await _issues(gd)
    assert after[bad_text] == step[bad_text] + 1 and after[no_offering] == step[no_offering]
    # người đã nghỉ: lead của họ vẫn mở
    leaver = await fresh_sale(who, await own_team(who))                                # đội riêng: không test nào đếm lead
    await ok(leaver, "crm.create_lead", name=f"Khách của người nghỉ {uuid.uuid4().hex[:4]}", phone=fresh_phone())
    step = await _issues(gd)
    await ok(admin, "admin.update_member", email=leaver.email, active=False)
    after = await _issues(gd)
    assert after[inactive] == step[inactive] + 1
    # lead CHƯA CHIA (không người phụ trách) là loại riêng — không còn tính vào "người đã nghỉ" (rà 23/11/2026)
    orphan = await ok(sale1, "crm.create_lead", name=f"Khách chưa chia {uuid.uuid4().hex[:4]}", phone=fresh_phone())
    async with db.owner.connection() as conn:
        await conn.execute("UPDATE crm.leads SET owner_id = NULL WHERE id = %s", (orphan["lead_id"],))
    later = await _issues(gd)
    assert later[inactive] == after[inactive] and later["lead đang theo chưa chia cho ai"] >= 1
    after = later
    # khách nghi trùng: cùng tên (khác dấu / hoa thường), SĐT lệch 1 số (…109… ↔ …107…, dạng nào cũng được — 0199 lưu E.164)
    tag, p = uuid.uuid4().hex[:5], fresh_phone()
    await ok(sale1, "crm.create_lead", name=f"Nguyễn Văn Trùng {tag}", phone=p)
    await ok(sale1, "crm.create_lead", name=f"nguyen van trung {tag}", phone=p.replace("109", "107", 1))
    assert (await _issues(gd))[dup] == after[dup] + 1
    why = next(b for b in (await _card(gd))["blocks"] if b["type"] == "callout" and b["title"].startswith("Vì sao"))
    assert any(f"chưa gắn {word} quan tâm ⇒ không vào số theo {word}" in f for f in why["facts"])


async def test_dead_deliveries_are_counted(who: Who, demo: dict[str, Any]) -> None:
    from barista.db import db

    gd, tid, label = await who("giamdoc"), demo["tenant"], "sự kiện tự động đã dừng thử (lỗi)"
    before = await _issues(gd)
    event = await owner_val("INSERT INTO core.events (tenant_id, type) VALUES (%s, 'test.health') RETURNING id", (tid,))
    try:
        async with db.owner.connection() as conn:
            await conn.execute("""INSERT INTO core.deliveries (tenant_id, event_id, subscriber, status, attempts, last_error)
                                  VALUES (%s, %s, 'test.health', 'dead', 5, 'x')""", (tid, event))
        assert (await _issues(gd))[label] == before[label] + 1
    finally:
        async with db.owner.connection() as conn:
            await conn.execute("DELETE FROM core.events WHERE id = %s", (event,))
