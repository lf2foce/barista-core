"""Kiểm "im lặng sau khi nạp" (0185, runbook 16): chạy MỌI vòng quét đã đăng ký + giao hết sự kiện vài lượt trên một BẢN SAO của DB vừa nạp dữ liệu hệ cũ,
rồi so trước / sau. Dữ liệu nạp là tham chiếu ⇒ không được có thông báo mới, việc theo dõi mới, sự kiện chia lead mới, bút toán mới, đổi người giữ lead.

    cd backend && BARISTA_DATABASE_URL=… BARISTA_OWNER_DATABASE_URL=… uv run python scripts/check_quiet_after_load.py [số lượt, mặc định 3]

CHỈ chạy trên bản sao: nếu dữ liệu chưa im thì chính lần kiểm này ghi thông báo / đổi người giữ vào DB đang trỏ. Không đặt khoá model khi chạy (việc theo dõi
mở ra sẽ gọi trợ lý). Mã thoát 0 = im; 1 = có thay đổi (in ra cột nào đổi)."""
import asyncio
import sys

import barista.kernel.loader as loader
from barista import sweeps
from barista.config import settings
from barista.db import db
from barista.kernel import events, registry

COUNTS = """SELECT (SELECT count(*) FROM core.notifications) AS notifications,
 (SELECT count(*) FROM leadflow.sla_events) AS sla_events,
 (SELECT count(*) FROM work.items) AS work_items,
 (SELECT count(*) FROM work.items WHERE status IN ('active', 'waiting')) AS open_work_items,
 (SELECT count(*) FROM core.ai_usage) AS ai_requests,
 (SELECT count(*) FROM core.events) AS events,
 (SELECT count(*) FROM ledger.journals) AS journals,
 (SELECT md5(string_agg(lead_id::text || coalesce(holder_id::text, '-') || status, ',' ORDER BY lead_id)) FROM leadflow.lead_state) AS lead_holders,
 (SELECT md5(string_agg(id::text || coalesce(owner_id::text, '-'), ',' ORDER BY id)) FROM crm.leads) AS lead_owners"""


async def _counts() -> dict:
    async with db.owner.connection() as conn:
        row = await (await conn.execute(COUNTS)).fetchone()
        return dict(row or {})


async def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    loader.load()
    await db.open(settings.database_url, settings.owner_database_url)
    try:
        before = await _counts()
        for i in range(rounds):
            ran = await sweeps.sweep_once()
            delivered = await events.drain()
            print(f"lượt {i + 1}: vòng quét trả {ran}, {delivered} sự kiện giao ({len(registry.SWEEPS)} vòng quét đã đăng ký)")
        after = await _counts()
    finally:
        await db.close()
    changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    print("trước:", before)
    print("sau:  ", after)
    if changed:
        print("KHÔNG IM — đổi:", changed)
        return 1
    print("IM: không có gì đổi sau", rounds, "lượt quét")
    if after["open_work_items"]:   # vòng quét im KHÔNG có nghĩa việc theo dõi im: bộ giữ nhịp kiểm từng việc đang mở khi máy chủ chạy (runbook 16)
        print(f"CHÚ Ý: còn {after['open_work_items']} việc theo dõi đang mở — nếu là của dữ liệu nạp thì đóng trước khi cho máy chủ trỏ vào")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
