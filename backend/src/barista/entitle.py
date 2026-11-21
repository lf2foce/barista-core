"""Lệnh VẬN HÀNH của nhà cung cấp: cấp / xem quyền dùng gói của một công ty (quyết định 0063 / migration 156) — khách trả tiền gói nào thì mở gói
đó; quản trị công ty sau đó bật trong Quản trị › Gói & ứng dụng. Chạy bằng kết nối owner trên máy chủ của bản cài, KHÔNG có trong app.

  uv run python -m barista.entitle --tenant "Sàn mẫu" --list
  uv run python -m barista.entitle --tenant "Sàn mẫu" --grant findings --by "anh Bùi" --note "HĐ 12/2026"
  uv run python -m barista.entitle --tenant "Sàn mẫu" --ai-budget 50 --ai-warn 80     # trần chi AI 50 USD / tháng (0092); "none" = bỏ trần
"""

import argparse
import asyncio
from datetime import date
from decimal import Decimal

from barista.config import settings
from barista.db import db
from barista.kernel import packs, registry
from barista.kernel.clock import today, using


def _month(tz: str) -> date:
    """Tháng của hạn mức AI theo múi giờ CÔNG TY (0169) — như ``auth.load``."""
    with using(tz):
        return today().replace(day=1)


async def main(tenant: str, grant: str, by: str, note: str, ai_budget: str = "", ai_warn: int | None = None) -> None:
    await db.open(settings.database_url, settings.owner_database_url)
    try:
        async with db.owner_tx() as c:
            cur = await c.execute("SELECT id, name, timezone FROM core.tenants WHERE id::text = %s OR name ILIKE %s ORDER BY created_at", (tenant, f"%{tenant}%"))
            rows = await cur.fetchall()
            if len(rows) != 1:
                raise SystemExit(f"Cần đúng một công ty khớp “{tenant}”, đang có {len(rows)}: " + "; ".join(r["name"] for r in rows))
            t = rows[0]
            if grant:
                if grant not in registry.PACKS or grant in registry.always_on():
                    raise SystemExit(f"Gói lạ hoặc luôn bật: {grant}. Có: {', '.join(k for k in registry.PACKS if k not in registry.always_on())}")
                if not by.strip():
                    raise SystemExit("Cần --by (ai cấp)")
                for p in sorted(registry.pack_closure({grant}) - registry.always_on()):   # gói bắt buộc đi kèm cũng được quyền
                    await c.execute("""INSERT INTO core.tenant_entitlements (tenant_id, pack, granted_by, note) VALUES (%s, %s, %s, %s)
                                       ON CONFLICT (tenant_id, pack) DO NOTHING""", (t["id"], p, by.strip(), note))
            if ai_budget:   # hạn mức chi AI / tháng (0092): bên trả tiền model đặt — không có trong app
                cap = None if ai_budget.lower() == "none" else Decimal(ai_budget)
                await c.execute("UPDATE core.tenant_profile SET ai_monthly_usd = %s, ai_warn_pct = coalesce(%s, ai_warn_pct) WHERE tenant_id = %s",
                                (cap, ai_warn, t["id"]))
            cur = await c.execute("""SELECT p.ai_enabled, p.ai_monthly_usd, p.ai_warn_pct, coalesce(s.usd, 0) AS spent FROM core.tenant_profile p
                                     LEFT JOIN core.ai_spend s ON s.tenant_id = p.tenant_id AND s.month = %s WHERE p.tenant_id = %s""",
                                  (_month(t["timezone"]), t["id"]))
            ai = await cur.fetchone()
            have = await packs.entitled(c, t["id"])
            cur = await c.execute("SELECT pack FROM core.tenant_packs WHERE tenant_id = %s", (t["id"],))
            on = {r["pack"] for r in await cur.fetchall()} | registry.always_on()
        print(f"{t['name']} — gói mặc định của bản cài: {', '.join(sorted(packs.defaults() - registry.always_on()))}")
        if ai:
            cap = f"{ai['ai_monthly_usd']:.2f} USD / tháng, báo ở {ai['ai_warn_pct']}%" if ai["ai_monthly_usd"] is not None else "không trần"
            print(f"  Trợ lý AI: {'bật' if ai['ai_enabled'] else 'TẮT'} · hạn mức {cap} · tháng này đã dùng {ai['spent']:.2f} USD")
        for k, pk in registry.PACKS.items():
            label = pk.title
            if k != "base":
                print(f"  {'■' if k in on else '□'} {label:<28} "
                      f"{'luôn bật' if k in registry.always_on() else 'đang bật' if k in on else 'có quyền, chưa bật' if k in have else 'chưa có quyền'}")
    finally:
        await db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True, help="tên (một phần) hoặc id công ty")
    ap.add_argument("--grant", default="", help="mã gói cần mở (vd findings)")
    ap.add_argument("--by", default="", help="người vận hành cấp quyền")
    ap.add_argument("--note", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--ai-budget", default="", help="trần chi AI USD / tháng (vd 50); none = bỏ trần")
    ap.add_argument("--ai-warn", type=int, default=None, help="báo quản trị khi dùng tới % hạn mức (mặc định 80)")
    a = ap.parse_args()
    asyncio.run(main(a.tenant, a.grant, a.by, a.note, a.ai_budget, a.ai_warn))
