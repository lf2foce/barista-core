"""Gói nghiệp vụ (quyết định 0045): công ty bật gói nào thì thấy / chạy được thao tác của gói đó — tắt ⇒ coi như không có.

Một gói = vài module gọi nhau chặt (Bán hàng: khách, chia lead, bảng hàng, giao dịch). Gói "base" và gói khai ``always_on`` luôn bật (0157). Gói cần gói khác
(``Pack(requires=...)``) — bật / tắt kiểm ở ``admin.set_pack``. Mọi bảng của mọi gói luôn có (một dòng migration chung); tắt
gói chỉ chặn ở cửa: executor (lệnh / truy vấn / gọi lồng), menu, công cụ trợ lý, bên nghe sự kiện, vòng quét.
Gọi sang gói không bắt buộc ⇒ ``ctx.call_optional`` (gói tắt ⇒ None, không lỗi); ``tests/test_meta.py`` chặn gọi thẳng.
"""

import time
from uuid import UUID

from barista.db import Conn
from barista.kernel import registry
from barista.kernel.errors import NotFound

TTL_S = 30.0   # bật / tắt gói hiệu lực trên mọi tiến trình trong ≤ 30 giây (trong tiến trình đổi: ngay)
_cache: dict[UUID, tuple[float, frozenset[str]]] = {}


class PackOff(NotFound):
    """Thao tác thuộc gói công ty chưa bật — với người dùng / trợ lý: như không có thao tác đó."""


def defaults() -> frozenset[str]:
    """Gói MẶC ĐỊNH của bản cài (``BARISTA_PACKS``; trống = mọi gói) — mọi công ty có quyền; gồm "base", gói luôn bật và gói mà chúng bắt buộc."""
    from barista.config import settings

    names = {x.strip() for x in settings.packs.split(",") if x.strip()}
    private = registry.company_packs()   # gói riêng công ty KHÔNG BAO GIỜ là mặc định của bản cài (ADR 0233 D): chỉ công ty được cấp quyền mới có
    if not names:
        return frozenset(registry.PACKS) - private
    unknown = names - set(registry.PACKS)
    if unknown:
        raise ValueError(f"BARISTA_PACKS có gói lạ: {', '.join(sorted(unknown))}")
    if named := names & private:
        raise ValueError(f"BARISTA_PACKS nêu gói riêng công ty: {', '.join(sorted(named))} — gói riêng cấp theo TỪNG công ty (python -m barista.entitle), "
                         "không đặt làm mặc định của bản cài")
    return frozenset(registry.pack_closure({*registry.always_on(), *names}))


async def hidden(conn: Conn, tenant_id: UUID) -> frozenset[str]:
    """Gói riêng công ty mà công ty NÀY không có quyền — với họ như không tồn tại: không hiện ở chợ gói / danh sách gói, không xem, không bật."""
    private = registry.company_packs()
    return private - await entitled(conn, tenant_id) if private else frozenset()


def check_config() -> None:
    """Kiểm TRƯỚC khi khởi động (``http_api`` lúc lên, ``seed``): ``BARISTA_PACKS`` nêu khoá gói không cài ⇒ DỪNG với lời rõ — không để bản cài
    lên rồi mới vỡ ở lần bật gói đầu tiên. Khoá cũ của một gói đã đổi tên (``provenance.json`` — ``renamed_from``) ⇒ chỉ đúng khoá mới."""
    from barista.kernel import loader, migrate

    loader.load()
    try:
        defaults()
    except ValueError:
        from barista.config import settings

        names = {x.strip() for x in settings.packs.split(",") if x.strip()}
        renamed = migrate.provenance(migrate.chains()).renamed
        bad = [f"{n} (nay là {renamed[n]})" if n in renamed else n for n in sorted(names - set(registry.PACKS))]
        raise RuntimeError(f"BARISTA_PACKS có khoá gói không cài: {', '.join(bad)} — sửa biến môi trường của bản cài; gói đang cài: "
                           f"{', '.join(sorted(registry.PACKS))}") from None


async def entitled(conn: Conn, tenant_id: UUID) -> frozenset[str]:
    """Gói công ty này CÓ QUYỀN bật: mặc định của bản cài ∪ gói nhà cung cấp đã cấp ∪ gói đang bật (đã dùng thì giữ quyền)."""
    cur = await conn.execute("""SELECT pack FROM core.tenant_entitlements WHERE tenant_id = %(t)s
                                UNION SELECT pack FROM core.tenant_packs WHERE tenant_id = %(t)s""", {"t": tenant_id})
    return defaults() | {r["pack"] for r in await cur.fetchall()}


def invalidate(tenant_id: UUID) -> None:
    _cache.pop(tenant_id, None)


async def enabled(conn: Conn, tenant_id: UUID) -> frozenset[str]:
    hit = _cache.get(tenant_id)
    if hit and time.monotonic() - hit[0] < TTL_S:
        return hit[1]
    cur = await conn.execute("SELECT pack FROM core.tenant_packs WHERE tenant_id = %s", (tenant_id,))
    out = registry.always_on() | {r["pack"] for r in await cur.fetchall()}
    _cache[tenant_id] = (time.monotonic(), out)
    return out


def module_on(packs: frozenset[str], module: str) -> bool:
    pack = registry.MODULE_PACK.get(module)   # module lạ ⇒ không bật (đóng khi không rõ — trước đây mặc định "base")
    return pack is not None and pack in packs


def subscriber_on(packs: frozenset[str], sub: registry.Subscriber) -> bool:
    """Gói của bên nghe GHI RÕ lúc khai (``Subscriber.pack``) — không suy từ tên chuỗi."""
    return bool(sub.pack) and sub.pack in packs and all(p in packs for p in sub.needs)


def missing(packs: frozenset[str], op: registry.Op) -> str | None:
    """Gói còn thiếu để chạy ``op`` (None = chạy được)."""
    for pack in (registry.MODULE_PACK.get(op.module, "base"), *op.needs):
        if pack not in packs:
            return pack
    return None


def off_message(pack: str) -> str:
    return f"Công ty chưa bật gói “{registry.pack_title(pack)}”"


async def require(conn: Conn, tenant_id: UUID, op: registry.Op) -> None:
    if (pack := missing(await enabled(conn, tenant_id), op)) is not None:
        raise PackOff(off_message(pack))


SWITCHES = frozenset({"admin.set_pack"})   # lệnh ĐỔI gói — giữ khoá độc quyền trạng thái gói


def lock_key(tenant_id: UUID) -> str:
    return f"packs:{tenant_id}"


async def lock_shared(conn: Conn, tenant_id: UUID) -> frozenset[str]:
    """Khoá CHIA SẺ trạng thái gói tới hết giao dịch + đọc gói THẬT (làm mới bộ nhớ đệm). Lệnh ghi và worker phát sự kiện đều giữ ⇒ đổi gói (khoá
    độc quyền) chờ chúng xong, và chúng không chạy giữa lúc đang đổi gói."""
    await conn.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended(%s, 0))", (lock_key(tenant_id),))
    return await _fresh(conn, tenant_id)


async def _fresh(conn: Conn, tenant_id: UUID) -> frozenset[str]:
    cur = await conn.execute("SELECT pack FROM core.tenant_packs WHERE tenant_id = %s", (tenant_id,))
    fresh = registry.always_on() | {r["pack"] for r in await cur.fetchall()}
    _cache[tenant_id] = (time.monotonic(), fresh)
    return fresh


async def require_for_write(conn: Conn, tenant_id: UUID, op: registry.Op) -> None:
    """Cho LỆNH ghi: giữ khoá CHIA SẺ trên trạng thái gói của công ty tới hết giao dịch rồi đọc gói THẬT từ DB (không qua bộ nhớ đệm). Đổi gói
    (``admin.set_pack``) giữ khoá ĐỘC QUYỀN cùng khoá ⇒ "tắt gói" và "lệnh của gói đó" xếp hàng: không có chuyện gói vừa tắt (đã kiểm không còn
    hợp đồng đang chạy) mà một lệnh ký hợp đồng đồng thời vẫn lọt. Làm mới bộ nhớ đệm bằng số vừa đọc ⇒ lời gọi lồng trong giao dịch dùng đúng số."""
    if op.name in SWITCHES:   # đổi gói: độc quyền ngay từ đầu (không nâng khoá chia sẻ ⇒ không khoá chéo)
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_key(tenant_id),))
        fresh = await _fresh(conn, tenant_id)
    else:
        fresh = await lock_shared(conn, tenant_id)
    if (pack := missing(fresh, op)) is not None:
        raise PackOff(off_message(pack))
