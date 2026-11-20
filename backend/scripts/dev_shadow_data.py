"""DỮ LIỆU GIẢ "ĐANG CHẠY SONG SONG" cho máy dev (ADR 0230) — dựng lại những gì một bản cài ở chế độ CHỈ GHI NHẬN (0207) để lại, rồi bấm thử Báo cáo chạy song song:
chi phí quảng cáo nhiều tuần nhưng lead chỉ vào từ vài ngày gần đây; phần lớn lead KHÔNG có người nhận; vài chiến dịch tiêu tiền mà không ra lead; một chiến dịch chi phí / lead
cao gấp mấy lần; khách để lại thông tin nhiều lần; hai kế hoạch đồng chi trả 7:3 nhưng lead về nghiêng một phía. Lead vào bằng đúng lệnh ``leadflow.intake`` ở chế độ nạp lịch sử
(``historical=True``) — y như app chỉ ghi nhận.

Dữ liệu GIẢ (tên ghép, SĐT dải 010). Chỉ chạy trên DB dev (từ chối ``BARISTA_ENV=production``). Chạy một lần sau ``dev_reset.sh``:

    PYTHONPATH=… BARISTA_DATABASE_URL=… BARISTA_OWNER_DATABASE_URL=… python backend/scripts/dev_shadow_data.py [--days 4]
"""

import asyncio
import random
import sys
from datetime import timedelta
from uuid import UUID

from barista.config import settings
from barista.db import db
from barista.kernel import executor
from barista.kernel.clock import today
from barista.seed import DOMAIN, _owner_rows, run, who

SURNAME = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Vũ", "Đặng", "Bùi", "Đỗ", "Hồ", "Ngô", "Dương"]
GIVEN = ["An", "Bình", "Chi", "Dũng", "Giang", "Hà", "Hải", "Hạnh", "Hiếu", "Hoa", "Huy", "Khang", "Lan", "Linh", "Long", "Mai", "Nam", "Nga", "Phong", "Quân"]
# (mã, kênh, kế hoạch, lead mỗi ngày, chi phí mỗi ngày) — hai chiến dịch không ra lead; một chiến dịch chi phí / lead rất cao; hai chiến dịch chung kế hoạch đồng chi trả
CAMPAIGNS = [("SH-FB-A", "facebook", "SH-PLAN-73", 22, 2_600_000), ("SH-FB-B", "facebook", "SH-PLAN-73", 18, 2_100_000), ("SH-GG-A", "google", "", 9, 1_900_000),
             ("SH-TT-A", "tiktok", "", 14, 1_300_000), ("SH-FB-C", "facebook", "", 3, 2_400_000), ("SH-GG-B", "google", "", 0, 1_700_000), ("SH-TT-B", "tiktok", "", 0, 900_000)]


async def main() -> None:
    if settings.env == "production":
        raise SystemExit("Dừng: BARISTA_ENV=production — không dựng dữ liệu giả")
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 4
    rnd = random.Random(11)
    await db.open(settings.database_url, settings.owner_database_url)
    try:
        rows = await _owner_rows("SELECT id FROM core.tenants ORDER BY created_at LIMIT 2", ())
        if len(rows) != 1:
            raise SystemExit(f"Dừng: cần đúng một công ty mẫu trong DB, thấy {len(rows)}")
        tid: UUID = rows[0]["id"]
        admin, mkt, digital, gd = [await who(tid, x, DOMAIN) for x in ("admin", "mkt", "digital", "giamdoc")]
        await run(admin, "admin.set_pack", pack="findings", enabled=True)
        offerings = [o["value"] for o in (await executor.query(gd, "crm.offering_options", {})).output][:2] or [""]
        t = today()
        first = t - timedelta(days=days - 1)
        for code, channel, plan, _, _ in CAMPAIGNS:
            await run(mkt, "mkt.upsert_campaign", code=code, name=f"Chạy song song {code}", channel=channel, offering_code=offerings[0], plan_code=plan, status="running")
        for d in range(45, -1, -1):   # chi phí có từ nhiều tuần trước; lead chỉ từ ``first``
            day = t - timedelta(days=d)
            for code, _, _, _, spend in CAMPAIGNS:
                await run(mkt, "mkt.record_spend", campaign_code=code, day=day.isoformat(), amount_vnd=int(spend * rnd.uniform(0.85, 1.15)))
        await run(mkt, "mkt.set_coverage", spend_reliable_from=(t - timedelta(days=45)).isoformat(), lead_reliable_from=first.isoformat(), spend_complete_through=t.isoformat())
        for team, pct in (("KD-A", 70), ("KD-B", 30)):
            await run(gd, "mkt.set_cost_share", plan_code="SH-PLAN-73", team_code=team, share_pct=pct, valid_from=(t - timedelta(days=45)).isoformat())
        made: list[tuple[str, str, int]] = []   # (lead_id, plan, ngày lùi)
        n = 0
        for back in range(days - 1, -1, -1):
            for code, _, plan, per_day, _ in CAMPAIGNS:
                for _ in range(per_day):
                    n += 1
                    name = f"{rnd.choice(SURNAME)} {rnd.choice(['Văn', 'Thị', 'Minh', 'Thu'])} {rnd.choice(GIVEN)}"
                    phone = f"010{300000 + n:07d}"
                    res = await executor.run(digital, "leadflow.intake", {"name": name, "phone": phone, "source": "ads", "campaign_code": code, "offering_code": offerings[0],
                                                                          "provider": "shadow", "external_ref": f"sh-{n}"}, historical=True)
                    if res.status != "ok":
                        raise SystemExit(f"intake lỗi: {res.status} {res.error}")
                    made.append((str(res.output["lead_id"]), plan, back))
                    if rnd.random() < 0.10:   # khách để lại thông tin lần nữa qua chiến dịch / dự án khác ⇒ lead trùng bị trả tiền hai lần
                        other = rnd.choice([c for c in CAMPAIGNS if c[0] != code and c[3] > 0])
                        r2 = await executor.run(digital, "leadflow.intake", {"name": name, "phone": phone, "source": "ads", "campaign_code": other[0],
                                                                             "offering_code": offerings[-1], "provider": "shadow", "external_ref": f"sh-{n}-b"}, historical=True)
                        if r2.status == "ok" and r2.output.get("lead_id"):
                            made.append((str(r2.output["lead_id"]), other[2], back))
        # trải ngày về của lead cho khớp (``back`` ngày trước), và cho khoảng 28% lead có người nhận — nghiêng về đội A với kế hoạch 7:3 (đội B nhận ít hơn phần góp)
        for lead_id, _, back in made:
            await _owner_rows("UPDATE crm.leads SET created_at = now() - make_interval(days => %s, hours => %s), captured_at = NULL WHERE id = %s RETURNING id", (back, rnd.randint(0, 20), lead_id))
            await _owner_rows("UPDATE leadflow.lead_state SET created_at = (SELECT created_at FROM crm.leads WHERE id = %s) WHERE lead_id = %s RETURNING lead_id", (lead_id, lead_id))
        team_a = [r["email"].split("@")[0] for r in await _owner_rows("SELECT u.email FROM core.users u JOIN core.team_members tm ON tm.user_id = u.id JOIN core.teams t ON t.id = tm.team_id WHERE t.code = 'KD-A' AND u.email LIKE 'sale%%'", ())] or ["sale1"]
        team_b = [r["email"].split("@")[0] for r in await _owner_rows("SELECT u.email FROM core.users u JOIN core.team_members tm ON tm.user_id = u.id JOIN core.teams t ON t.id = tm.team_id WHERE t.code = 'KD-B' AND u.email LIKE 'sale%%'", ())] or ["sale2"]
        given = 0
        for lead_id, plan, _ in made:
            if rnd.random() > 0.28:
                continue
            pick = rnd.choice(team_a) if (plan and rnd.random() < 0.8) or not plan else rnd.choice(team_b)
            res = await executor.run(digital, "leadflow.assign", {"lead_id": lead_id, "owner_email": f"{pick}@{DOMAIN}"}, historical=True)
            given += res.status == "ok"
        print(f"{len(made)} lead về trong {days} ngày (chế độ chỉ ghi nhận), {given} lead có người nhận; {len(CAMPAIGNS)} chiến dịch, chi phí 45 ngày, đồng chi trả 70/30 kế hoạch SH-PLAN-73")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
