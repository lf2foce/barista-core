"""Dụng cụ dùng chung của test qua gói thế vai (``tests/standin``): dựng đơn, người mới, đọc dòng bằng kết nối owner."""

from datetime import date
from typing import Any
from uuid import uuid4

from barista.db import db
from barista.kernel import events
from barista.kernel.principal import Principal
from tests.conftest import Who, ok, owner_val
from tests.standin.conftest import DOMAIN


async def rows(sql: Any, params: Any = None) -> list[dict[str, Any]]:
    async with db.owner.connection() as conn:
        return await (await conn.execute(sql, params)).fetchall()


async def fresh_member(who: Who, role: str = "sale", team: str = "") -> Principal:
    """Thành viên mới tinh của công ty thế vai (không dính đơn / số của test khác)."""
    local = f"t{uuid4().hex[:10]}"
    await ok(await who("admin"), "admin.invite_user", email=f"{local}@{DOMAIN}", name=f"Người thử {local}", role=role, team_code=team, is_lead=False)
    return await who(local)


async def order(who: Who, *, owner: str | Principal = "sale3", amount: int = 4_000_000_000, customer: str = "Khách thử", **kw: Any) -> dict[str, Any]:
    """Đơn mới (admin nhập cho ``owner``): ``{code, order_id, fee, bonus}`` — hồ sơ tự mở theo quy trình mặc định của gói sau khi chạy sự kiện."""
    admin = await who("admin")
    p = owner if isinstance(owner, Principal) else await who(owner)
    out = await ok(admin, "orderdesk.create", title="Tư vấn triển khai", customer=customer, owner_email=p.email, amount_vnd=amount, **kw)
    await events.drain()
    return {"code": out["code"], "order_id": str(out["order_id"]), "fee": amount * 3 // 100, "bonus": amount // 100}


async def signed(who: Who, *, signed_on: date | None = None, **kw: Any) -> dict[str, Any]:
    """Đơn đã ký + mọi bên nghe đã chạy: thêm ``inv`` (mã khoản phí bên Tài chính, PD-<mã đơn>) và ``work`` (việc theo dõi thu phí)."""
    o = await order(who, **kw)
    await ok(await who("admin"), "orderdesk.sign", code=o["code"], **({"signed_on": signed_on.isoformat()} if signed_on else {}))
    await events.drain()
    inv = f"PD-{o['code']}"
    return {**o, "inv": inv, "work": await owner_val("SELECT id FROM work.items WHERE subject_ref = %s", (inv,))}


async def case_id(order_id: str) -> Any:
    return await owner_val("SELECT id FROM flow.cases WHERE subject_kind = 'orderdesk.order' AND subject_id = %s", (order_id,))
