"""Phát sự kiện outbox tới bên nghe — mỗi dòng ``core.deliveries`` xử lý đúng một lần trong DB.

Bên nghe chạy trong giao dịch của công ty đó, quyền HỆ THỐNG (người gây sự kiện ghi ở ``actor``). Xử lý + đánh dấu
``done`` cùng một giao dịch ⇒ chết giữa chừng thì chạy lại, không bao giờ nửa vời. Lỗi ⇒ thử lại lùi dần; quá 8 lần ⇒
``dead`` (hiện ở Quản trị để người xem).
"""

import asyncio
import logging
from uuid import UUID

from barista.db import db, set_session
from barista.kernel import packs, registry
from barista.kernel.executor import core_ctx, flush_events, historical, map_error, run_after
from barista.kernel.principal import Lease, system_principal

log = logging.getLogger("barista.events")
MAX_ATTEMPTS = 8


async def _deliver(tenant_id: UUID, delivery_id: int) -> bool:
    sysp = system_principal(tenant_id)
    lease = Lease()   # lượt VIỆC NỀN của đúng lần giao này: ``ctx.job_for`` chỉ có ở đây (và con của nó), hết khi giao xong (0144)
    try:
        async with db.tenant_tx(sysp) as conn:
            cur = await conn.execute(
                """SELECT d.id, d.subscriber, e.id AS event_id, e.type, e.payload, e.actor, e.created_at AS event_at, e.historical
                   FROM core.deliveries d JOIN core.events e ON e.id = d.event_id
                   WHERE d.id = %s AND d.status = 'pending' FOR UPDATE OF d SKIP LOCKED""", (delivery_id,))
            d = await cur.fetchone()
            if d is None:
                return False
            # khoá chia sẻ trạng thái gói: không tắt được gói giữa lúc phản ứng của nó đang chạy (sự kiện con phát ra cũng theo gói thật)
            on = await packs.lock_shared(conn, tenant_id)
            sub = registry.SUBSCRIBERS.get(d["subscriber"]) or registry.SUBSCRIBERS.get(registry.REPLACED.get(d["subscriber"], ""))
            if sub is None:
                await conn.execute("UPDATE core.deliveries SET status = 'dead', last_error = 'không còn bên nghe' WHERE id = %s",
                                   (delivery_id,))
                return False
            if not packs.subscriber_on(on, sub):
                # chỉ còn ở việc tạo TRƯỚC 0059 (từ nay việc chỉ tạo cho bên nghe của gói đang bật, và còn việc chờ thì không tắt được gói)
                await conn.execute("UPDATE core.deliveries SET status = 'done', done_at = now(), last_error = 'bỏ qua: gói chưa bật'"
                                   " WHERE id = %s", (delivery_id,))
                return False
            p = system_principal(tenant_id, d["actor"])
            if p.session_key() != sysp.session_key():
                await set_session(conn, p)
            ctx = core_ctx(conn, p, module=sub.module, background=lease)
            # sự kiện của một lần NẠP LỊCH SỬ (0185) ⇒ bên nghe chạy cùng chế độ (không thông báo / việc theo dõi), sự kiện nó phát tiếp cũng mang dấu
            with historical(d["historical"]):
                await sub.handler(ctx, registry.Event(d["event_id"], d["type"], d["payload"], d["actor"], d["event_at"]))
                await flush_events(conn, p, ctx.events)  # bên nghe phát tiếp ⇒ chuỗi sự kiện
            await conn.execute("UPDATE core.deliveries SET status = 'done', attempts = attempts + 1, done_at = now() WHERE id = %s",
                               (delivery_id,))
        await run_after(ctx)
        return True
    except Exception as e:   # giao lỗi ⇒ ghi last_error + đếm lần thử, tới ngưỡng thành 'dead' — sự kiện không mất, lỗi hệ thống đã log
        err = map_error(e).error if not isinstance(e, AssertionError) else repr(e)
        log.warning("giao sự kiện %s lỗi: %r", delivery_id, e)
        async with db.tenant_tx(sysp) as conn:
            await conn.execute(
                """UPDATE core.deliveries SET attempts = attempts + 1, last_error = %s,
                       status = CASE WHEN attempts + 1 >= %s THEN 'dead' ELSE 'pending' END,
                       next_at = now() + make_interval(secs => least(3600, 2 ^ (attempts + 1)))
                   WHERE id = %s""", (f"{type(e).__name__}: {err}"[:1000], MAX_ATTEMPTS, delivery_id))
        return False
    finally:
        lease.active = False


LEASE_S = 120


async def dispatch_once(limit: int = 50) -> int:
    """Nhận một lô việc đến hạn bằng cách THUÊ (đẩy next_at ra sau LEASE_S giây) trong một giao dịch ngắn: nhiều worker
    chạy song song lấy các lô khác nhau; worker chết giữa chừng ⇒ hết hạn thuê, việc tự quay lại hàng đợi."""
    async with db.owner_tx() as conn:
        cur = await conn.execute(
            """UPDATE core.deliveries SET next_at = now() + make_interval(secs => %s)
               WHERE id IN (SELECT id FROM core.deliveries WHERE status = 'pending' AND next_at <= now()
                            ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED)
               RETURNING id, tenant_id""", (LEASE_S, limit))
        due = sorted(await cur.fetchall(), key=lambda r: r["id"])
    done = 0
    for d in due:
        done += await _deliver(d["tenant_id"], d["id"])
    return done


async def drain(max_rounds: int = 50) -> int:
    """Chạy tới khi hết việc đến hạn (seed, test, dev)."""
    total = 0
    for _ in range(max_rounds):
        n = await dispatch_once()
        total += n
        if n == 0:
            break
    return total


async def run_forever(interval_s: float) -> None:
    while True:
        try:
            await drain()
        except asyncio.CancelledError:
            raise
        except Exception:   # vòng nền không được chết vì một lượt lỗi — log.exception rồi chạy lượt sau
            log.exception("vòng phát sự kiện lỗi")
        await asyncio.sleep(interval_s)
