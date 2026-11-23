"""THANH BÊN THEO VAI (ADR 0255 lát 1) — thanh bên trái KHÔNG vẽ hết mọi phân hệ: vài phân hệ MẶC ĐỊNH theo vai + phân hệ người dùng ĐÃ GHIM + "Tất cả mục".

* Mặc định theo vai do GÓI khai (``Pack(rail={vai: (phân hệ…)})``, đọc thẳng từ mã — không cài vào dữ liệu ⇒ nâng gói không ghi đè chỗ công ty đã đổi).
  Lõi không biết vai hay phân hệ nào: chỉ đọc khai báo. Vai không có khai báo (và công ty chưa đặt) ⇒ thanh bên vẽ hết phân hệ như trước.
* Công ty đổi mặc định của MỘT vai ở Quản trị › Thanh bên theo vai (``core.role_rail``, một dòng / vai = danh sách thay thế hoàn toàn; xoá dòng = về mặc định của gói).
* Cá nhân ghim thêm / bỏ ghim CẢ PHÂN HỆ (``core.pin`` / ``core.unpin`` với khoá màn trống — bảng ``core.user_pins``); ghim từng MÀN vẫn như cũ.
* Giấu menu KHÔNG thay quyền: phân hệ người đó không thấy (không quyền / gói tắt) tự bị lọc khỏi mọi nhóm — executor + RLS vẫn là nguồn quyền duy nhất.
* Một phân hệ chỉ hiện MỘT lần: đã ở "Theo vai" thì không lặp ở "Đã ghim" (ghim vẫn ghi nhận, hiện khi phân hệ rời nhóm "Theo vai").
* Trợ lý đề xuất ghim ("anh mở màn này 5 lần, ghim nhé?") là lát sau — chưa có ở đây.
"""

from collections.abc import Collection, Iterable, Sequence
from typing import Any

from barista.kernel import registry, screens, ui
from barista.kernel.core_ops import m
from barista.kernel.errors import Invalid, NotFound
from barista.kernel.executor import Ctx
from barista.kernel.fields import Input, NoInput, field

RESOURCE = "core.admin"   # như từ vựng công ty: quản trị / công nghệ chỉnh dữ liệu cấu hình theo vai


def pack_default(role: str, on: Collection[str] | None = None) -> tuple[str, ...]:
    """Phân hệ mặc định của vai do các gói khai (gói của vai và gói khác góp — như ``Pack.pins``), theo thứ tự nạp gói, bỏ trùng. ``on`` = gói công ty ĐANG BẬT
    (``Principal.packs``): gói tắt không góp gì — khai của một gói ngành không đổi thanh bên của công ty không dùng ngành đó. None = mọi gói đã nạp."""
    out: list[str] = []
    for p in registry.PACKS.values():
        if on is None or p.key in on:
            out += [k for k in p.rail.get(role, ()) if k not in out]
    return tuple(out)


def build(role_keys: Iterable[str], pinned_keys: Iterable[str], visible: Sequence[str]) -> dict[str, list[str]] | None:
    """Hai nhóm của thanh bên cho MỘT người, từ khoá phân hệ họ THẤY (``visible``, đã lọc quyền + gói bật):
    ``role`` = mặc định của vai (theo thứ tự khai, tối đa ``RAIL_MAX``), ``pinned`` = phân hệ họ ghim mà chưa nằm ở ``role`` (một mục một lần).
    Không có mặc định nào người đó thấy được ⇒ None (thanh bên như trước — vẽ hết). Khoá lạ / không thấy bị bỏ qua, không báo lỗi."""
    seen = set(visible)
    role = [k for k in dict.fromkeys(role_keys) if k in seen][:registry.RAIL_MAX]
    if not role:
        return None
    return {"role": role, "pinned": [k for k in dict.fromkeys(pinned_keys) if k in seen and k not in role]}


def _known(ctx: Ctx) -> dict[str, str]:
    """Phân hệ công ty này có thể đặt lên thanh bên: khoá → tên (gói của phân hệ đang bật; không có phân hệ cá nhân)."""
    from barista import workspaces  # phân hệ giả ``account`` (màn cá nhân) không bao giờ lên thanh bên

    return {w.key: w.title for w in screens.assemble()
            if w.key != workspaces.ACCOUNT and not (ctx.p.packs is not None and w.pack and w.pack not in ctx.p.packs)}


class SetRoleRail(Input):
    role: str = field("Vai", options_query="admin.role_options")
    workspaces: list[str] = field("Phân hệ hiện sẵn trên thanh bên", options_query="core.workspace_options",
                                  help=f"Tối đa {registry.RAIL_MAX}, theo thứ tự chọn. Người giữ vai chỉ thấy phân hệ họ có quyền; ai cũng ghim thêm được")


class RoleKey(Input):
    role: str = field("Vai", options_query="admin.role_options")


async def _role(ctx: Ctx, role: str) -> str:
    if not await ctx.val("SELECT 1 FROM core.roles WHERE key = %s", (role,)):
        raise NotFound(f"Không có vai {role}")
    return role


@m.command("set_role_rail", resource=RESOURCE, title="Đặt phân hệ hiện sẵn trên thanh bên của vai", agent="deny", category="setup", risk="low")
async def set_role_rail(ctx: Ctx, inp: SetRoleRail) -> dict[str, Any]:
    """Thay TOÀN BỘ danh sách phân hệ mặc định của một vai (thay cho khai báo của gói; gói nâng cấp không ghi đè). Không cấp quyền — chỉ là cái hiện sẵn:
    người giữ vai không có quyền một phân hệ thì không bao giờ thấy nó ở đây. Có hiệu lực ở lần tải menu sau của họ."""
    role = await _role(ctx, inp.role.strip())
    keys = list(dict.fromkeys(k.strip() for k in inp.workspaces if k.strip()))
    known = _known(ctx)
    if bad := [k for k in keys if k not in known]:
        raise Invalid(f"Không có phân hệ: {', '.join(bad)}")
    if not 1 <= len(keys) <= registry.RAIL_MAX:
        raise Invalid(f"Chọn từ 1 đến {registry.RAIL_MAX} phân hệ (muốn theo mặc định của gói thì bấm “Về mặc định của gói”)")
    await ctx.exec("""INSERT INTO core.role_rail (role, workspaces, updated_by) VALUES (%s, %s, core.me())
                      ON CONFLICT (tenant_id, role) DO UPDATE SET workspaces = EXCLUDED.workspaces, updated_by = core.me(), updated_at = now()""", (role, keys))
    return {"role": role, "workspaces": keys, "summary": f"Vai {role}: thanh bên hiện sẵn {', '.join(known[k] for k in keys)}"}


@m.command("clear_role_rail", resource=RESOURCE, title="Đưa thanh bên của vai về mặc định của gói", agent="deny", category="setup", risk="low")
async def clear_role_rail(ctx: Ctx, inp: RoleKey) -> dict[str, Any]:
    """Xoá phần công ty đã đặt ⇒ vai theo khai báo của gói (không khai ⇒ thanh bên vẽ hết phân hệ như trước)."""
    role = await _role(ctx, inp.role.strip())
    n = await ctx.exec("DELETE FROM core.role_rail WHERE role = %s", (role,))
    return {"role": role, "summary": f"Vai {role}: thanh bên theo mặc định của gói" if n else f"Vai {role} vốn đang theo mặc định của gói"}


@m.query("workspace_options", resource=RESOURCE, title="Phân hệ có thể đặt lên thanh bên")
async def workspace_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options([{"value": k, "label": t} for k, t in _known(ctx).items()])


@m.query("my_rail", resource=None, title="Thanh bên của vai tôi", agent="deny")
async def my_rail(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Phần công ty đã đặt cho VAI của người đang xem (menu / manifest ráp thanh bên từ đây; None = vai theo mặc định của gói). Mỗi người đọc đúng dòng của vai mình
    (RLS cách ly công ty) — không cần quyền quản lý để thanh bên của chính mình đúng."""
    row = await ctx.val("SELECT workspaces FROM core.role_rail WHERE role = %s", (ctx.p.role,))
    return {"workspaces": list(row) if row is not None else None}


@m.query("role_rail", resource=RESOURCE, title="Thanh bên theo vai")
async def role_rail(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Mỗi vai một dòng: đang theo mặc định của gói hay công ty đã đặt, danh sách hiện sẵn, mặc định của gói (trước khi lọc quyền từng người)."""
    known = _known(ctx)

    def names(keys: Iterable[str]) -> str:
        return ", ".join(known.get(k) or k for k in keys)

    rows = []
    for r in await ctx.all("""SELECT r.key, r.label, t.workspaces, t.updated_at, u.name AS updated_by FROM core.roles r
                               LEFT JOIN core.role_rail t ON t.role = r.key LEFT JOIN core.users u ON u.id = t.updated_by ORDER BY r.key"""):
        mine, base = r["workspaces"], pack_default(r["key"], ctx.p.packs)
        shown = list(mine) if mine is not None else list(base)
        rows.append({"id": r["key"], "role": r["key"], "label": r["label"], "state": "set" if mine is not None else "pack" if base else "all",
                     "shown": names(shown) or "Hiện hết phân hệ", "base": names(base), "workspaces_in": [k for k in shown if k in known],
                     "updated_at": r["updated_at"], "updated_by": r["updated_by"]})
    return ui.table(rows, [ui.col("label", "Vai", width=160),
                           ui.col("state", "Thanh bên", "badge", labels={"set": "Công ty đã đổi", "pack": "Theo gói", "all": "Chưa khai — hiện hết"},
                                  tones={"set": "green", "pack": "gray", "all": "gray"}),
                           ui.col("shown", "Hiện sẵn", width=320), ui.col("base", "Mặc định của gói", width=260),
                           ui.col("updated_by", "Người sửa"), ui.col("updated_at", "Sửa lúc", "datetime")],
                    title="Thanh bên theo vai", limit=500,
                    note="Phân hệ hiện sẵn ở thanh bên trái của người giữ vai; phần còn lại nằm ở “Tất cả mục”, ai cũng ghim thêm được. Chỉ là cái hiện sẵn, không cấp quyền: "
                         "người không có quyền một phân hệ thì không thấy nó ở đâu cả. Gói nâng cấp không ghi đè chỗ đã đổi.")
