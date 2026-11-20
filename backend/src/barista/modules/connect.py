"""ỨNG DỤNG ĐÃ KẾT NỐI (0140, 0130 tầng 3) — quản trị cài / đổi quyền / xoay khoá / thu hồi app từ xa. Cơ chế (token, người gọi, webhook) ở
``kernel/remote_apps.py`` + ``kernel/webhooks.py``; ở đây chỉ là lệnh quản trị qua executor.

* Cài, đổi quyền / sự kiện / địa chỉ, xoay khoá: luật duyệt mặc định ``access_change`` → chủ tài khoản (0029, như đổi quyền người). Lệnh chạy sau
  duyệt bằng quyền NGƯỜI XIN (nạp lại) ⇒ quyền app phải nằm gọn trong quyền người xin; người xin thành NGƯỜI BẢO LÃNH (quyền app lúc chạy luôn
  giao quyền hiện tại của người này).
* Khoá app KHÔNG đi qua phiếu duyệt (kết quả phiếu lưu trong DB, người duyệt thấy): duyệt xong, người xin tự bấm "Nhận khoá" — khoá sinh lúc đó,
  hiện MỘT lần (khoá ``once_*`` không lưu ở bảng chống lặp / nhật ký), DB chỉ giữ bản băm. Xoay khoá = khoá cũ + token đang có hết hiệu lực ngay.
* Thu hồi: chạy ngay, không chờ duyệt (bớt quyền không phải nâng quyền) — token bị từ chối từ request kế tiếp, thư webhook chưa gửi bị huỷ.
"""

import unicodedata
from collections.abc import Mapping
from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from uuid import UUID, uuid4

from pydantic import BeforeValidator, field_validator

from barista.api import Action as A
from barista.api import Ctx, Denied, Input, Invalid, Module, NoInput, NotFound, Screen, field, ui
from barista.config import settings
from barista.kernel import operator_door, registry, remote_apps, webhooks
from barista.kernel.principal import Grant

m = Module("connect", "Ứng dụng kết nối", pack="base")

GRANTS_HELP = ("Dùng thay bảng quyền (nhập hàng loạt, trợ lý): mỗi quyền một dòng / cách nhau “;”: <tài nguyên>:<own|unit|team|all>:<read,write> — "
               "vd “leadflow:team:write; crm:all:read”. Không vượt quyền của chính bạn; app không bao giờ duyệt / quản lý.")
ACCESS_HELP = "Mỗi dòng một loại dữ liệu: app thấy tới đâu, được xem hay ghi. Không vượt quyền của chính bạn; app không bao giờ duyệt / quản lý"
SCOPES = {"own": "Của người bảo lãnh", "unit": "Đội của người bảo lãnh", "team": "Đội người bảo lãnh quản", "all": "Cả công ty"}


def _join_events(v: Any) -> Any:
    """Ô chọn nhiều sự kiện gửi danh sách; trợ lý / nhập tay gửi chữ cách nhau dấu phẩy — cùng một giá trị."""
    return ", ".join(str(x) for x in v) if isinstance(v, list | tuple) else v


class AppAccess(Input):
    resource: str = field("Dữ liệu", options_query="admin.resource_options", example="crm")
    scope: Literal["own", "unit", "team", "all"] = field("Thấy tới đâu", "all", options=SCOPES)
    actions: list[Literal["read", "write"]] = field("Được", ["read"], options={"read": "Xem", "write": "Ghi"})


def _grants_text(access: list[AppAccess], grants: str) -> str:
    """Quyền dạng chữ cho bộ phân tích chung (``remote_apps.parse_grants``): bảng chọn nếu có, không thì ô viết tay."""
    return "; ".join(f"{a.resource}:{a.scope}:{','.join(a.actions)}" for a in access) if access else grants


NAME_MAX = 60


def clean_name(v: str) -> str:
    """Tên app hiện trên phiếu duyệt / danh sách: một dòng, không ký tự điều khiển / định dạng (xuống dòng, chữ vô hình, đổi chiều chữ), ≤ 60 ký tự
    (review 08/11: tên dài + 40 dòng trống từng che phần quyền thật trên phiếu)."""
    v = v.strip()
    if not v or len(v) > NAME_MAX or any(unicodedata.category(c).startswith("C") for c in v):
        raise ValueError(f"tên một dòng, 1–{NAME_MAX} ký tự, không ký tự điều khiển")
    return v


async def _rule_required(ctx: Ctx, command: str) -> None:
    """FAIL CLOSED (review 08/11): cài / đổi quyền / xoay khoá app chỉ chạy khi công ty CÓ luật duyệt cho lệnh đó (executor đã bắt qua chủ tài khoản,
    hoặc ngoại lệ một chủ tài khoản). Công ty chưa cài luật (chưa chạy seed --upgrade, hoặc ai đó đã bỏ) ⇒ từ chối, không chạy thẳng."""
    if not await ctx.val("SELECT 1 FROM core.approval_rules WHERE command = %s", (command,)):
        raise Denied(f"Công ty chưa có luật duyệt cho “{command}” — cài cấu hình mặc định (luật “Thay đổi quyền truy cập” → chủ tài khoản) "
                     "trước khi cài / đổi quyền / xoay khoá ứng dụng")


def _describe(grants_text: str, events_text: str, url: str) -> str:
    """Mô tả trên PHIẾU DUYỆT dựng từ cấu trúc đã phân tích — không chép chữ tự do người xin gõ (review 08/11): mỗi quyền [tài nguyên] phạm vi ·
    hành động, sự kiện chỉ tên có thật, webhook chỉ tên máy."""
    try:
        grants = remote_apps.parse_grants(grants_text)
        rows = " | ".join(f"[{r}] {g.scope} · {'/'.join(a for a in ('read', 'write') if a in g.actions)}" for r, g in sorted(grants.items()))
    except ValueError:
        rows = "(không hợp lệ — lệnh sẽ bị từ chối)"
    events = _split(events_text)
    ev = ", ".join(e if e in registry.WEBHOOKS else "[lạ]" for e in events) or "(không)"
    host = (urlparse(url.strip()).hostname or "(không hợp lệ)") if url.strip() else "(không)"
    return f"quyền: {rows or '(không có)'} · sự kiện: {ev} · webhook tới máy: {host}"


def _split(text: str) -> list[str]:
    return sorted({e.strip() for e in text.replace(";", ",").replace("\n", ",").split(",") if e.strip()})


async def _check(ctx: Ctx, grants_text: str, events_text: str, url: str, *, holder: Mapping[str, Grant] | None = None,
                 holder_label: str = "người cài") -> tuple[dict[str, Grant], list[str]]:
    """Kiểm quyền / sự kiện / địa chỉ của app bằng quyền NGƯỜI ĐANG CHẠY lệnh (người xin — lệnh sau duyệt chạy lại bằng quyền họ, nạp lại), hoặc
    bằng quyền của VAI bảo lãnh (``holder`` — cửa nhà vận hành, 0206: vai đứng thay người cài trong luật "app ≤ người cài")."""
    try:
        grants = remote_apps.parse_grants(grants_text)
    except ValueError as e:
        raise Invalid(str(e)) from e
    if not grants:
        raise Invalid("App cần ít nhất một quyền")
    if unknown := [r for r in grants if r not in registry.grantable()]:   # tài nguyên của nhà vận hành (0193 / 0206) không bao giờ là quyền app
        raise Invalid(f"Tài nguyên lạ: {', '.join(unknown)}")
    if over := remote_apps.exceeding(grants, ctx.p.grants if holder is None else holder):
        raise Denied(f"Quyền app vượt quyền của {holder_label}: {'; '.join(over)}")
    events = _split(events_text)
    for e in events:
        hook = registry.WEBHOOKS.get(e)
        if hook is None or (ctx.p.packs is not None and registry.MODULE_PACK.get(hook.module) not in ctx.p.packs | {"base"}):
            raise Invalid(f"Không có sự kiện {e} (đang có: {', '.join(sorted(_available(ctx)))})")
        g = grants.get(hook.resource)
        if g is None or "read" not in g.actions or g.scope != "all":
            raise Invalid(f"Nhận “{e}” cần quyền {hook.resource}:all:read — sự kiện không lọc theo dòng nên app phải xem được cả công ty")
    if events and not url:
        raise Invalid("Nhận sự kiện cần địa chỉ webhook")
    if url and (why := webhooks.url_allowed(url)):
        raise Invalid(why)
    return grants, events


def _available(ctx: Ctx) -> list[str]:
    return [w.name for w in registry.WEBHOOKS.values()
            if ctx.p.packs is None or registry.MODULE_PACK.get(w.module) in ctx.p.packs | {"base"}]


async def _write_access(ctx: Ctx, app_id: UUID, grants: dict[str, Grant], events: list[str]) -> None:
    await ctx.exec("DELETE FROM core.remote_app_grants WHERE app_id = %s", (app_id,))
    await ctx.exec("DELETE FROM core.remote_app_events WHERE app_id = %s", (app_id,))
    for res, g in grants.items():
        await ctx.exec("INSERT INTO core.remote_app_grants (app_id, resource, scope, actions) VALUES (%s, %s, %s, %s)",
                       (app_id, res, g.scope, [a for a in ("read", "write") if a in g.actions]))
    for e in events:
        await ctx.exec("INSERT INTO core.remote_app_events (app_id, event) VALUES (%s, %s)", (app_id, e))


# ── Cài ──

class Install(Input):
    name: str = field("Tên ứng dụng", example="Kênh chat & nguồn khách", max_length=NAME_MAX)
    access: list[AppAccess] = field("Quyền của app", [], help=ACCESS_HELP)
    events: Annotated[str, BeforeValidator(_join_events)] = field(
        "Sự kiện app muốn nhận", "", kind="multiselect", options_query="connect.event_options", max_length=500, group="Nhận sự kiện qua webhook",
        help="Chọn các sự kiện Barista sẽ báo cho app; nhận sự kiện cần địa chỉ webhook")
    webhook_url: str = field("Địa chỉ webhook", "", help="https://… của app — Barista gửi sự kiện đã ký tới đây", max_length=500, group="Nhận sự kiện qua webhook")
    grants: str = field("Viết tay các quyền", "", kind="textarea", help=GRANTS_HELP, example="leadflow:team:write; crm:all:read", max_length=2000,
                        group="Thêm chi tiết")

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return clean_name(v)


async def _install_access(ctx: Ctx, inp: Install) -> str:
    return f"cài app “{inp.name}” · {_describe(_grants_text(inp.access, inp.grants), inp.events, inp.webhook_url)}"


@m.command("install", resource="core.admin", action="manage", title="Cài ứng dụng kết nối", agent="deny", sensitive=_install_access)
async def install(ctx: Ctx, inp: Install) -> dict[str, Any]:
    """Tạo app (client OAuth của công ty này). Chạy sau khi chủ tài khoản duyệt; người xin là người bảo lãnh và là người DUY NHẤT nhận khoá lần đầu."""
    await _rule_required(ctx, "connect.install")
    grants, events = await _check(ctx, _grants_text(inp.access, inp.grants), inp.events, inp.webhook_url.strip())
    app_id = uuid4()
    user_id = await ctx.val("SELECT core.ensure_user(%s, %s)", (remote_apps.service_email(app_id), f"Ứng dụng {inp.name.strip()}"))
    client_id = remote_apps.new_client_id()
    await ctx.exec(
        """INSERT INTO core.remote_apps (id, client_id, name, service_user_id, installed_by, approved_by, webhook_url, secret_claim_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (app_id, client_id, inp.name.strip(), user_id, ctx.p.user_id, list(ctx.p.approved_by), inp.webhook_url.strip(), ctx.p.user_id))
    await _write_access(ctx, app_id, grants, events)
    return {"app_id": app_id, "client_id": client_id}


# ── Đổi quyền / sự kiện / địa chỉ ──

class SetAccess(Input):
    app_id: UUID = field("Mã app", hidden=True)
    access: list[AppAccess] = field("Quyền của app", [], help=ACCESS_HELP)
    events: Annotated[str, BeforeValidator(_join_events)] = field(
        "Sự kiện app muốn nhận", "", kind="multiselect", options_query="connect.event_options", max_length=500, group="Nhận sự kiện qua webhook")
    webhook_url: str = field("Địa chỉ webhook", "", max_length=500, group="Nhận sự kiện qua webhook")
    grants: str = field("Viết tay các quyền", "", kind="textarea", help=GRANTS_HELP, max_length=2000, group="Thêm chi tiết")


@m.query("event_options", resource="core.admin", title="Sự kiện app nhận được")
async def event_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Sự kiện công bố của các gói đang bật (``registry.WEBHOOKS``), nhãn người đọc — không bắt gõ tên sự kiện."""
    return [{"value": w.name, "label": w.label or w.name} for w in sorted(registry.WEBHOOKS.values(), key=lambda w: w.label or w.name)
            if ctx.p.packs is None or registry.MODULE_PACK.get(w.module) in ctx.p.packs | {"base"}]


async def _app(ctx: Ctx, app_id: UUID, *, lock: bool = False) -> dict[str, Any]:
    a = await ctx.one("SELECT * FROM core.remote_apps WHERE id = %s FOR UPDATE" if lock else "SELECT * FROM core.remote_apps WHERE id = %s", (app_id,))
    if a is None:
        raise NotFound("Không thấy ứng dụng")
    if a["status"] != "active":
        raise Invalid("Ứng dụng đã bị thu hồi — cài lại nếu cần")
    return a


async def _access_change(ctx: Ctx, inp: SetAccess) -> str:
    a = await ctx.one("SELECT name FROM core.remote_apps WHERE id = %s", (inp.app_id,))
    before = remote_apps.format_grants(await remote_apps.app_grants(ctx.conn, inp.app_id)) if a else ""
    return (f"app “{a['name'] if a else inp.app_id}” — {_describe(_grants_text(inp.access, inp.grants), inp.events, inp.webhook_url)} (quyền đang có: {before or 'không có'})"
            " · người xin thành người bảo lãnh")


@m.command("set_access", resource="core.admin", action="manage", title="Đổi quyền ứng dụng kết nối", agent="deny", sensitive=_access_change)
async def set_access(ctx: Ctx, inp: SetAccess) -> dict[str, Any]:
    """Thay toàn bộ quyền + sự kiện + địa chỉ webhook. Người xin (đã được duyệt) thành người bảo lãnh — quản trị rời công ty thì quản trị khác nhận
    bảo lãnh bằng lệnh này."""
    await _rule_required(ctx, "connect.set_access")
    await _app(ctx, inp.app_id, lock=True)
    grants, events = await _check(ctx, _grants_text(inp.access, inp.grants), inp.events, inp.webhook_url.strip())
    # người xin (đã được duyệt) nhận bảo lãnh — kể cả app nhà vận hành cài bảo lãnh bằng vai (0206): từ đây quyền app ≤ quyền người này
    await ctx.exec("UPDATE core.remote_apps SET webhook_url = %s, installed_by = %s, sponsor_role = NULL, approved_by = %s WHERE id = %s",
                   (inp.webhook_url.strip(), ctx.p.user_id, list(ctx.p.approved_by), inp.app_id))
    await _write_access(ctx, inp.app_id, grants, events)
    return {"app_id": inp.app_id}


# ── Khoá ──

class AppRef(Input):
    app_id: UUID = field("Mã app", hidden=True)


async def _rotate_access(ctx: Ctx, inp: AppRef) -> str:
    name = await ctx.val("SELECT name FROM core.remote_apps WHERE id = %s", (inp.app_id,))
    return f"cấp khoá mới cho app “{name or inp.app_id}” (khoá cũ hết hiệu lực khi người xin nhận khoá mới)"


@m.command("rotate_secret", resource="core.admin", action="manage", title="Xoay khoá ứng dụng", agent="deny", sensitive=_rotate_access)
async def rotate_secret(ctx: Ctx, inp: AppRef) -> dict[str, Any]:
    """Sau duyệt: người xin được nhận MỘT khoá mới ("Nhận khoá"). Khoá cũ vẫn chạy tới lúc đó (đổi không gián đoạn); lộ khoá ⇒ thu hồi app."""
    await _rule_required(ctx, "connect.rotate_secret")
    await _app(ctx, inp.app_id, lock=True)
    await ctx.exec("UPDATE core.remote_apps SET secret_claim_by = %s WHERE id = %s", (ctx.p.user_id, inp.app_id))
    return {"app_id": inp.app_id}


@m.command("claim_secret", resource="core.admin", action="manage", title="Nhận khoá ứng dụng", agent="deny")
async def claim_secret(ctx: Ctx, inp: AppRef) -> dict[str, Any]:
    """Chỉ người được duyệt cài / xoay khoá, một lần. Trả client_id + khoá app + khoá ký webhook — hiện MỘT lần, Barista không giữ bản rõ."""
    a = await _app(ctx, inp.app_id, lock=True)
    if a["secret_claim_by"] != ctx.p.user_id or ctx.p.via != "human":
        raise Denied("Chỉ người đã được duyệt cài / xoay khoá ứng dụng này nhận khoá, một lần")
    if not settings.apps_signing_secret:
        raise Invalid("Bản cài chưa đặt BARISTA_APPS_SIGNING_SECRET — nhờ nhà vận hành đặt rồi nhận khoá")
    secret, version = await remote_apps.set_secret(ctx.conn, inp.app_id)
    return {"app_id": inp.app_id, "client_id": a["client_id"], "token_url": f"{settings.public_api_url.rstrip('/')}/v1/apps/token",
            "once_client_secret": secret, "once_webhook_secret": remote_apps.signing_key(inp.app_id, version),
            "message": "Chép khoá vào cấu hình của app NGAY — Barista không hiện lại. Mất khoá ⇒ xoay khoá."}


# ── Thu hồi ──

class Revoke(Input):
    app_id: UUID = field("Mã app", hidden=True)
    reason: str = field("Lý do", "", max_length=300)


@m.command("revoke", resource="core.admin", action="manage", title="Thu hồi ứng dụng kết nối")
async def revoke(ctx: Ctx, inp: Revoke) -> dict[str, Any]:
    """Có hiệu lực NGAY: token bị từ chối từ request kế tiếp, thư webhook chưa gửi bị huỷ. Không mở lại được — cài app mới nếu cần."""
    return await _revoke(ctx, inp.app_id, inp.reason)


async def _revoke(ctx: Ctx, app_id: UUID, reason: str) -> dict[str, Any]:
    await _app(ctx, app_id, lock=True)
    await ctx.exec("UPDATE core.remote_apps SET status = 'revoked', revoked_at = now(), revoked_by = core.me(), secret_claim_by = NULL WHERE id = %s",
                   (app_id,))
    tokens = await ctx.exec("UPDATE core.remote_app_tokens SET revoked_at = now() WHERE app_id = %s AND revoked_at IS NULL", (app_id,))
    dropped = await ctx.exec("""UPDATE core.webhook_deliveries SET status = 'cancelled', done_at = now(), last_error = %s
                                WHERE app_id = %s AND status = 'pending'""", (f"thu hồi: {reason}"[:300], app_id))
    return {"app_id": app_id, "tokens_revoked": tokens, "webhooks_cancelled": dropped}


# ── Cửa nhà vận hành (0206): cài / xoay khoá / thu hồi KHÔNG qua phiếu, bảo lãnh bằng một VAI — chỉ ``python -m barista.operator`` ──

class OperatorInstall(Install):
    like_role: str = field("Vai bảo lãnh", max_length=30, help="Quyền app ≤ quyền HIỆN TẠI của vai này — lúc cài và mỗi lần app gọi")
    by: str = operator_door.by_field()
    why: str = operator_door.why_field()


def _secrets(app_id: UUID, client_id: str, secret: str, version: int) -> dict[str, Any]:
    """Khoá trả MỘT lần (khoá ``once_*``: không vào bảng chống lặp / nhật ký / lịch sử — ``executor.storable``)."""
    return {"app_id": app_id, "client_id": client_id, "token_url": f"{settings.public_api_url.rstrip('/')}/v1/apps/token",
            "once_client_secret": secret, "once_webhook_secret": remote_apps.signing_key(app_id, version)}


def _signing_ready() -> None:
    if not settings.apps_signing_secret:
        raise Invalid("Bản cài chưa đặt BARISTA_APPS_SIGNING_SECRET — đặt trước khi cài / xoay khoá ứng dụng (runbook 11)")


@m.command("operator_install", resource=operator_door.RESOURCE, action="manage", title="Cài ứng dụng kết nối (nhà vận hành)", internal=True,
           agent="deny", category="setup")
async def operator_install(ctx: Ctx, inp: OperatorInstall) -> dict[str, Any]:
    """App bảo lãnh bằng VAI ``like_role`` (không có người cài): quyền app phải nằm gọn trong quyền hiện tại của vai — đúng luật "app ≤ người cài" của
    0140, vai đứng thay người. Vai không thuộc đội nào ⇒ chỉ phạm vi own / all. Khoá sinh NGAY, trả một lần cho người vận hành (không phiếu, không
    "Nhận khoá"). Quản trị công ty sau này "Đổi quyền" app ⇒ thành người bảo lãnh (qua duyệt như mọi app)."""
    operator_door.require_who(inp)
    _signing_ready()
    role = await ctx.one("SELECT key, label FROM core.roles WHERE key = %s", (inp.like_role,))
    if role is None:
        raise NotFound(f"Không có vai trò {inp.like_role}")
    held = await remote_apps.role_grants(ctx.conn, ctx.p.tenant_id, role["key"]) or {}
    grants, events = await _check(ctx, _grants_text(inp.access, inp.grants), inp.events, inp.webhook_url.strip(), holder=held,
                                  holder_label=f"vai {role['label']} [{role['key']}]")
    if narrow := [r for r, g in grants.items() if g.scope not in ("own", "all")]:
        raise Invalid(f"App bảo lãnh bằng vai không thuộc đội nào — chỉ phạm vi own hoặc all: {', '.join(sorted(narrow))}")
    app_id = uuid4()
    user_id = await ctx.val("SELECT core.ensure_user(%s, %s)", (remote_apps.service_email(app_id), f"Ứng dụng {inp.name.strip()}"))
    client_id = remote_apps.new_client_id()
    await ctx.exec("""INSERT INTO core.remote_apps (id, client_id, name, service_user_id, installed_by, sponsor_role, webhook_url)
                      VALUES (%s, %s, %s, %s, NULL, %s, %s)""", (app_id, client_id, inp.name.strip(), user_id, role["key"], inp.webhook_url.strip()))
    await _write_access(ctx, app_id, grants, events)
    secret, version = await remote_apps.set_secret(ctx.conn, app_id)
    return {**_secrets(app_id, client_id, secret, version), "grants": remote_apps.format_grants(grants), "events": events,
            "summary": f"Cài app “{inp.name.strip()}” bảo lãnh bằng vai [{role['key']}] · {_describe(_grants_text(inp.access, inp.grants), inp.events, inp.webhook_url)}"}


class OperatorApp(Input):
    app_id: UUID = field("Mã app", hidden=True)
    by: str = operator_door.by_field()
    why: str = operator_door.why_field()


@m.command("operator_rotate", resource=operator_door.RESOURCE, action="manage", title="Xoay khoá ứng dụng (nhà vận hành)", internal=True,
           agent="deny", category="setup")
async def operator_rotate(ctx: Ctx, inp: OperatorApp) -> dict[str, Any]:
    """Khoá mới NGAY, trả một lần: khoá cũ + mọi token đang có hết hiệu lực từ request kế tiếp."""
    operator_door.require_who(inp)
    _signing_ready()
    a = await _app(ctx, inp.app_id, lock=True)
    secret, version = await remote_apps.set_secret(ctx.conn, inp.app_id)
    return {**_secrets(inp.app_id, a["client_id"], secret, version), "summary": f"Khoá mới cho app “{a['name']}” — khoá cũ hết hiệu lực"}


@m.command("operator_revoke", resource=operator_door.RESOURCE, action="manage", title="Thu hồi ứng dụng kết nối (nhà vận hành)", internal=True,
           agent="deny", category="setup")
async def operator_revoke(ctx: Ctx, inp: OperatorApp) -> dict[str, Any]:
    operator_door.require_who(inp)
    return await _revoke(ctx, inp.app_id, operator_door.note(inp.by, inp.why))


class OperatorShadow(OperatorApp):
    on: bool = field("Chỉ ghi nhận", help="Bật: lệnh của app chạy như nạp lịch sử — dữ liệu vào đủ nhưng không báo ai, không tính hạn, không webhook")


@m.command("operator_shadow", resource=operator_door.RESOURCE, action="manage", title="Bật / tắt chế độ chỉ ghi nhận của ứng dụng (nhà vận hành)", internal=True,
           agent="deny", category="setup")
async def operator_shadow(ctx: Ctx, inp: OperatorShadow) -> dict[str, Any]:
    """0207: app chạy song song hệ đang dùng — dữ liệu vào đủ, không làm phiền người. Tắt = app chạy THẬT từ request kế tiếp (lead được chia, báo,
    tính hạn). Chỉ nhà vận hành đổi (app không tự bật / tắt được)."""
    operator_door.require_who(inp)
    a = await _app(ctx, inp.app_id, lock=True)
    await ctx.exec("UPDATE core.remote_apps SET shadow = %s WHERE id = %s", (inp.on, inp.app_id))
    state = "CHỈ GHI NHẬN (dữ liệu vào đủ, chưa giao cho ai — không báo, không tính hạn, không webhook)" if inp.on else "CHẠY THẬT"
    return {"app_id": inp.app_id, "shadow": inp.on, "summary": f"App “{a['name']}”: {state}"}


# ── Xem ──

STATUS = {"active": "Đang chạy", "revoked": "Đã thu hồi"}


@m.query("apps", resource="core.admin", title="Ứng dụng đã kết nối")
async def apps(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all(
        """SELECT a.id, a.name, a.client_id, a.status, a.webhook_url, a.secret_set_at, a.last_token_at, a.created_at, a.secret_claim_by,
                  coalesce(u.name, 'Vai ' || r.label || ' (nhà vận hành cài)') AS sponsor, c.name AS claimer,
                  (SELECT string_agg(e.event, ', ' ORDER BY e.event) FROM core.remote_app_events e WHERE e.app_id = a.id) AS events,
                  (SELECT count(*) FROM core.webhook_deliveries w WHERE w.app_id = a.id AND w.status = 'pending') AS pending,
                  (SELECT count(*) FROM core.webhook_deliveries w WHERE w.app_id = a.id AND w.status = 'dead') AS dead
           FROM core.remote_apps a LEFT JOIN core.users u ON u.id = a.installed_by LEFT JOIN core.users c ON c.id = a.secret_claim_by
           LEFT JOIN core.roles r ON r.key = a.sponsor_role
           ORDER BY a.status, a.created_at DESC""")
    for r in rows:
        r["grants"] = remote_apps.format_grants(await remote_apps.app_grants(ctx.conn, r["id"]))
        r["claim"] = "me" if r.pop("secret_claim_by") == ctx.p.user_id and r["status"] == "active" else ""
        r["secret"] = (f"Chờ {r['claimer']} nhận" if r.pop("claimer") else "Đã nhận" if r["secret_set_at"] else "Chưa có")
    return ui.table(rows, [
        ui.col("name", "Ứng dụng"), ui.col("status", "Trạng thái", "badge", labels=STATUS, tones={"active": "green", "revoked": "gray"}),
        ui.col("client_id", "client_id", width=220), ui.col("grants", "Quyền", width=260), ui.col("events", "Sự kiện", width=200),
        ui.col("webhook_url", "Webhook", width=220), ui.col("sponsor", "Người bảo lãnh"), ui.col("secret", "Khoá"),
        ui.col("last_token_at", "Gọi gần nhất", "datetime"), ui.col("pending", "Thư chờ gửi", "number"), ui.col("dead", "Thư dừng thử", "number"),
    ], title="Ứng dụng đã kết nối", empty="Chưa có ứng dụng nào kết nối",
        note="Ứng dụng từ xa gọi API bằng quyền ghi ở đây, không bao giờ hơn quyền người bảo lãnh. Cài / đổi quyền / xoay khoá cần chủ tài khoản "
             f"duyệt; thu hồi có hiệu lực ngay. Sự kiện có: {', '.join(sorted(_available(ctx))) or '(không)'}.")


class DeliveriesIn(Input):
    app_id: UUID = field("Mã app", hidden=True)


@m.query("deliveries", resource="core.admin", title="Webhook đã gửi của ứng dụng")
async def deliveries(ctx: Ctx, inp: DeliveriesIn) -> dict[str, Any]:
    rows = await ctx.all("""SELECT id, event, data, status, attempts, last_status, last_error, occurred_at, done_at FROM core.webhook_deliveries
                            WHERE app_id = %s ORDER BY created_at DESC LIMIT 200""", (inp.app_id,))
    return ui.table(rows, [
        ui.col("occurred_at", "Lúc xảy ra", "datetime"), ui.col("event", "Sự kiện"),
        ui.col("status", "Trạng thái", "badge", labels={"pending": "Chờ gửi", "done": "Đã nhận", "dead": "Dừng thử", "cancelled": "Đã huỷ"},
               tones={"pending": "orange", "done": "green", "dead": "red", "cancelled": "gray"}),
        ui.col("attempts", "Số lần", "number"), ui.col("last_status", "Mã HTTP", "number"), ui.col("last_error", "Lỗi", width=280),
    ], title="Webhook đã gửi", limit=200, empty="Chưa có sự kiện nào")


m.screens("admin", (
    Screen("connected_apps", "Ứng dụng đã kết nối", "app.connected.to.app.below.fill", "connect.apps", group="Hệ thống",
           actions=(A("connect.install", style="primary"),),
           row_actions=(A("connect.claim_secret", "Nhận khoá", bind={"app_id": "id"}, when={"claim": ["me"]}, style="primary"),
                        A("connect.set_access", "Đổi quyền", bind={"app_id": "id"}, prefill={"grants": "grants", "events": "events",
                                                                                            "webhook_url": "webhook_url"}, when={"status": ["active"]}),
                        A("connect.rotate_secret", "Xoay khoá", bind={"app_id": "id"}, when={"status": ["active"]}),
                        A("connect.revoke", "Thu hồi", bind={"app_id": "id"}, when={"status": ["active"]}, style="destructive"))),
), rank=40)
