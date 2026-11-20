"""Tạo công ty KHÁCH từ một công ty dịch vụ (quyết định 0063) — đường ghi xuyên công ty DUY NHẤT, hẹp có chủ đích:

* ``ensure_company``: tạo công ty theo ``tenant_id`` CHO TRƯỚC (= id dòng khách ở công ty dịch vụ) trong MỘT giao dịch owner — chạy lại bao nhiêu lần
  cũng một công ty (không công ty mồ côi khi bên gọi lỗi giữa chừng). Đã có ⇒ không đụng gì, chỉ cài lại cấu hình mặc định còn thiếu.
* ``set_ai``: bật / tắt trợ lý AI của công ty khách theo cờ "khách đã đồng ý" (mặc định TẮT khi tạo).
* ``deactivate_everywhere``: người nghỉ việc ở công ty dịch vụ ⇒ khoá ở MỌI công ty khách do công ty đó tạo (chỉ chiều khoá; mở lại là việc có chủ đích).
* ``sync_members``: đưa phần thành viên DO HỢP ĐỒNG CẤP (``memberships.provided_by``, 0066) về đúng danh sách hiện tại — thêm, sửa vai, gỡ người
  không còn phụ trách. Không đụng người khách tự mời; KHÔNG mở lại người đã khoá — đồng bộ chạy lại mỗi lần sửa khách (thử lại khởi tạo) không
  được thành cấp lại quyền; cấp lại người đã khoá là việc có chủ đích của quản trị.

Cả hai CHỈ làm với công ty có ``provisioned_by`` = công ty gọi — công ty tự đăng ký hoặc của công ty dịch vụ khác ⇒ từ chối. Phần nghiệp vụ trong
công ty khách (thiết lập sổ sách, kệ tài liệu) đi qua executor bằng quyền hệ thống CỦA CÔNG TY ĐÓ (``run_in`` / ``query_in`` — cũng chỉ với công ty
mình tạo), không ghi thẳng schema module. Đọc nhiều công ty khách bằng quyền người xem: ``gather`` (``kernel/portfolio``).

Gói dùng qua ``barista.api.tenancy`` (1.8) — gói ngoài lõi không import ``kernel``. MỌI hàm nhận ``ctx`` (không nhận ``provider``): công ty dịch vụ
= ``ctx.p.tenant_id`` của Ctx do lõi dựng cho module đang chạy (``executor.sealed`` — Ctx giả ⇒ TypeError), nên một gói không thể đặt tên công
ty dịch vụ khác. Module phải khai ``m.consumes("tenancy")`` (không thì Denied). ``ensure_company`` chỉ bật cho khách những gói CHÍNH công ty dịch vụ
đang có (``core.tenant_packs`` + gói luôn bật); ``run_in`` / ``query_in`` không chạy thao tác nội bộ và không chạm quản trị / quyền (``admin.*``, ``core.*``).
"""

from typing import Any
from uuid import UUID

from barista import presets
from barista.db import Conn, db
from barista.kernel import clock, executor, portfolio, registry
from barista.kernel.errors import Denied
from barista.kernel.principal import system_principal

__all__ = ["active_members", "deactivate_everywhere", "ensure_company", "gather", "query_in", "run_in", "set_ai", "sync_members"]

NOT_FOR_TENANCY = ("admin", "core")   # run_in / query_in không đụng quản trị công ty khách (gói bật / tắt, quyền, thành viên) — đã có hàm hẹp riêng ở trên


def provider_of(ctx: executor.Ctx) -> UUID:
    """Công ty dịch vụ = công ty của Ctx đang chạy. Ctx phải do lõi dựng (``sealed``); module đang chạy phải khai ``consumes("tenancy")``."""
    p, module, _ = executor.sealed(ctx)
    if not module or module not in registry.FAMILY_CALLERS["tenancy"]:
        raise Denied(f"Module {module or '(không rõ)'} không khai dùng công ty khách (Module.consumes(\"tenancy\") — barista.api.tenancy)")
    return p.tenant_id


async def _check_owner(c: Conn, tenant_id: UUID, provider: UUID) -> bool:
    """True = công ty đã có và thuộc ``provider``; False = chưa có; công ty của người khác ⇒ Denied."""
    cur = await c.execute("SELECT provisioned_by FROM core.tenants WHERE id = %s FOR UPDATE", (tenant_id,))
    row = await cur.fetchone()
    if row is None:
        return False
    if row["provisioned_by"] != provider:
        raise Denied("Công ty này không do công ty của bạn tạo — không thêm thành viên / gói được")
    return True


async def ensure_company(ctx: executor.Ctx, tenant_id: UUID, name: str, *, packs: tuple[str, ...], about: str = "", ai: bool = False) -> bool:
    """Trả True nếu vừa tạo. Gói bật + hồ sơ công ty chỉ ghi lúc tạo; cấu hình mặc định của gói cài lại mỗi lần (chỉ thêm phần thiếu).
    ``packs`` ⊆ gói công ty dịch vụ ĐANG CÓ (kèm gói luôn bật): công ty dịch vụ không cấp cho khách gói nó chưa có quyền dùng."""
    provider = provider_of(ctx)
    async with db.owner_tx() as c:
        cur = await c.execute("SELECT pack FROM core.tenant_packs WHERE tenant_id = %s", (provider,))
        own = {r["pack"] for r in await cur.fetchall()} | registry.always_on() | {"base"}
        if over := sorted(set(packs) - own):
            raise Denied(f"Công ty dịch vụ không có gói {', '.join(over)} — không cấp cho công ty khách được")
        created = not await _check_owner(c, tenant_id, provider)
        if created:
            # múi giờ: mặc định của BẢN CÀI (0169) — quản trị công ty khách đổi trong Cài đặt nếu khác
            await c.execute("INSERT INTO core.tenants (id, name, provisioned_by, timezone) VALUES (%s, %s, %s, %s)",
                            (tenant_id, name, provider, clock.default_zone().key))
            for pack in (p for p in packs if p not in registry.always_on()):   # gói luôn bật (0157) không có dòng tenant_packs; kèm quyền dùng gói (156): gói công ty dịch vụ mở cho khách là gói khách đã có quyền
                await c.execute("INSERT INTO core.tenant_packs (tenant_id, pack) VALUES (%s, %s)", (tenant_id, pack))
                await c.execute("""INSERT INTO core.tenant_entitlements (tenant_id, pack, granted_by, note) VALUES (%s, %s, 'provisioning', %s)
                                   ON CONFLICT DO NOTHING""", (tenant_id, pack, f"tạo bởi công ty {provider}"))
            await c.execute("INSERT INTO core.tenant_profile (tenant_id, about, ai_enabled) VALUES (%s, %s, %s)", (tenant_id, about, ai))
    executor.raise_for(await executor.run(system_principal(tenant_id), "admin.install_defaults", {"packs": ["base", *packs]}))
    return created


async def sync_members(ctx: executor.Ctx, tenant_id: UUID, *, members: list[tuple[UUID, str]]) -> dict[str, int]:
    """Đưa thành viên DO HỢP ĐỒNG CẤP (``provided_by`` = ``provider``) về đúng ``members`` (người trùng ⇒ vai đầu tiên):
    thêm người chưa có, sửa vai cho đúng, GỠ người đang hoạt động không còn trong danh sách (đổi người phụ trách, ngừng dịch vụ, thôi quản trị).
    Chỉ cấp cho người còn hoạt động ở công ty nguồn; khoá dòng nguồn tới lúc ghi xong để nghỉ việc không chen giữa kiểm và cấp quyền.
    Không đụng người công ty khách tự mời, và không đụng người đã bị KHOÁ (không mở lại, không gỡ — khoá là việc có chủ đích của quản trị)."""
    provider = provider_of(ctx)
    want = dict(reversed(members))   # người trùng: vai đầu tiên thắng
    async with db.owner_tx() as c:
        if not await _check_owner(c, tenant_id, provider):
            raise Denied("Chưa có công ty khách — tạo công ty trước")
        # Không tin danh sách đã đọc ở module: người có thể nghỉ giữa lúc module kiểm và giao dịch này bắt đầu.
        # FOR SHARE chờ UPDATE đang chạy và giữ trạng thái nguồn tới commit: hoặc nghỉ trước ⇒ không cấp, hoặc cấp trước ⇒ sự kiện nghỉ thu hồi.
        cur = await c.execute("""SELECT user_id FROM core.memberships WHERE tenant_id = %s AND user_id = ANY(%s) AND active
                                 ORDER BY user_id FOR SHARE""", (provider, list(want)))
        active = {r["user_id"] for r in await cur.fetchall()}
        want = {u: role for u, role in want.items() if u in active}
        for role in sorted(set(want.values())):
            await c.execute("INSERT INTO core.roles (tenant_id, key, label) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                            (tenant_id, role, presets.role_label(role)))
        added = changed = 0
        for user_id, role in want.items():
            cur = await c.execute("""INSERT INTO core.memberships (tenant_id, user_id, role, provided_by) VALUES (%s, %s, %s, %s)
                                     ON CONFLICT (tenant_id, user_id) DO UPDATE SET role = EXCLUDED.role
                                     WHERE core.memberships.provided_by = EXCLUDED.provided_by AND core.memberships.active
                                       AND core.memberships.role <> EXCLUDED.role
                                     RETURNING (xmax = 0) AS inserted""", (tenant_id, user_id, role, provider))
            row = await cur.fetchone()
            if row is not None:
                added, changed = added + row["inserted"], changed + (not row["inserted"])
        cur = await c.execute("""DELETE FROM core.memberships WHERE tenant_id = %s AND provided_by = %s AND active AND NOT (user_id = ANY(%s))
                                 RETURNING user_id""", (tenant_id, provider, list(want)))
        removed = [r["user_id"] for r in await cur.fetchall()]
        if removed:
            await c.execute("DELETE FROM core.team_members WHERE tenant_id = %s AND user_id = ANY(%s)", (tenant_id, removed))
    # thành viên tới sau lần cài mặc định / chủ tài khoản vừa bị gỡ ⇒ công ty khách luôn có chủ tài khoản theo quy tắc của 0029
    executor.raise_for(await executor.run(system_principal(tenant_id), "admin.ensure_owner", {}))
    return {"added": added, "changed": changed, "removed": len(removed)}


async def active_members(ctx: executor.Ctx, tenant_id: UUID) -> set[UUID]:
    """Ai đang hoạt động ở công ty khách (công ty mình tạo) — để đồng bộ không gán việc cho người quản trị khách đã khoá."""
    provider = provider_of(ctx)
    async with db.owner.connection() as c:
        if not await _check_owner(c, tenant_id, provider):
            return set()
        cur = await c.execute("SELECT user_id FROM core.memberships WHERE tenant_id = %s AND active", (tenant_id,))
        return {r["user_id"] for r in await cur.fetchall()}


async def set_ai(ctx: executor.Ctx, tenant_id: UUID, *, enabled: bool) -> None:
    provider = provider_of(ctx)
    async with db.owner_tx() as c:
        if await _check_owner(c, tenant_id, provider):
            await c.execute("UPDATE core.tenant_profile SET ai_enabled = %s WHERE tenant_id = %s", (enabled, tenant_id))


async def deactivate_everywhere(ctx: executor.Ctx, user_id: UUID) -> int:
    """Khoá ``user_id`` ở mọi công ty do công ty của ``ctx`` tạo — phần quyền DO HỢP ĐỒNG CẤP (khách tự mời người đó là quan hệ riêng của khách).
    Trả số công ty vừa khoá."""
    provider = provider_of(ctx)
    async with db.owner_tx() as c:
        cur = await c.execute(
            """UPDATE core.memberships m SET active = false FROM core.tenants t
               WHERE t.id = m.tenant_id AND t.provisioned_by = %s AND m.provided_by = %s AND m.user_id = %s AND m.active""",
            (provider, provider, user_id))
        return cur.rowcount


async def _provided(tenant_id: UUID, provider: UUID) -> None:
    async with db.owner.connection() as c:
        if not await _check_owner(c, tenant_id, provider):
            raise Denied("Chưa có công ty khách — tạo công ty trước")


def _allowed_op(name: str) -> None:
    op = registry.get(name)
    if op is None:
        raise Denied(f"Không có thao tác {name}")
    # khớp CẢ khoá module LẪN tiền tố tên có dấu chấm ("admin." / "core.") — không khớp theo chuỗi đầu trần: "admin2.x" không bị chặn nhầm, "admin.x" không lọt
    if op.internal or op.module in NOT_FOR_TENANCY or name.startswith(tuple(f"{m}." for m in NOT_FOR_TENANCY)):
        raise Denied(f"{name}: không chạy được trong công ty khách bằng quyền hệ thống (thao tác nội bộ / quản trị công ty / quyền)")


async def run_in(ctx: executor.Ctx, tenant_id: UUID, name: str, inp: dict[str, Any]) -> Any:
    """Chạy lệnh ``name`` trong công ty KHÁCH bằng quyền hệ thống của CHÍNH công ty đó (thiết lập sổ sách, kệ tài liệu…) — chỉ công ty do công ty của
    ``ctx`` tạo (không thì Denied), không thao tác nội bộ, không ``admin.*`` / ``core.*``; lỗi của lệnh ⇒ ném như ``executor.raise_for``."""
    provider = provider_of(ctx)
    _allowed_op(name)
    await _provided(tenant_id, provider)
    return executor.raise_for(await executor.run(system_principal(tenant_id), name, inp))


async def query_in(ctx: executor.Ctx, tenant_id: UUID, name: str, inp: dict[str, Any] | None = None) -> Any | None:
    """Truy vấn ``name`` trong công ty KHÁCH bằng quyền hệ thống của công ty đó — cùng ràng buộc như ``run_in``. Không chạy được (gói tắt…) ⇒ None."""
    provider = provider_of(ctx)
    _allowed_op(name)
    await _provided(tenant_id, provider)
    r = await executor.query(system_principal(tenant_id), name, inp or {})
    return r.output if r.ok else None


async def gather(ctx: executor.Ctx, tenants: list[tuple[UUID, str]], query: str, inp: dict[str, Any] | None = None,
                 *, concurrency: int = portfolio.CONCURRENCY) -> list[dict[str, Any]]:
    """Đọc nhiều công ty bằng quyền CỦA NGƯỜI ĐANG GỌI (``ctx.p``) tại từng công ty (``kernel/portfolio``): không phải thành viên ⇒ "no_access"."""
    provider_of(ctx)
    return await portfolio.gather(ctx.p, tenants, query, inp, concurrency=concurrency)
