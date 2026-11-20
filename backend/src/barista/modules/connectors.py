"""KẾT NỐI NGOÀI (ADR 0244) — module lõi của khung kết nối chung: mọi nối ra hệ ngoài đi qua đây. Hợp đồng bộ nối + mã hoá: ``kernel/connectors.py``;
bề mặt cho gói: ``barista.api.connectors``.

* Một KẾT NỐI = công ty × bộ nối (gói khai bằng ``@m.connector``) × khoá (mã hoá, quản trị dán trong app) × cài đặt × con trỏ.
* VÒNG KÉO (``sync``, vòng quét mỗi công ty): bước chuẩn bị NGOÀI giao dịch nạp kết nối tới hạn, gọi bộ nối kéo một lô; handler giao TỪNG MỤC cho lệnh
  nhận của gói trong điểm lưu riêng — mục lỗi vào hàng thử lại (``connectors.pending``), con trỏ vẫn tiến; lỗi của hệ ngoài ⇒ con trỏ đứng, lùi dần.
* WEBHOOK VÀO ``/v1/hooks/connectors/<công ty>/<mã kết nối>``: khoá ``webhook`` của kết nối, chống lặp theo mã sự kiện, giao mục hoặc đánh thức lượt kéo.
* "Chỉ ghi nhận" (0207) là thuộc tính của KẾT NỐI: lõi giao mục bằng ``call_record_only``.
* Gói của bộ nối đang tắt / bản cài thiếu bộ nối ⇒ không giao gì, con trỏ không tiến, kết nối báo lỗi trên màn + Sức khoẻ dữ liệu.
"""

import asyncio
import base64
import binascii
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, LiteralString
from uuid import UUID, uuid4

import psycopg

from barista.api import Action as A
from barista.api import Conflict, Ctx, Denied, Input, Invalid, Module, NoInput, NotFound, Open, Screen, field, ui
from barista.config import settings
from barista.db import db
from barista.kernel import connectors as kc
from barista.kernel import packs, registry
from barista.kernel.executor import Prep

log = logging.getLogger("barista.connectors")

m = Module("connectors", "Kết nối ngoài", pack="base", resources={"connectors": "Kết nối ra hệ ngoài (khoá, đồng bộ, webhook)"})

STATUS = {"active": "Đang chạy", "paused": "Tạm dừng"}
MODE = {"live": "Chạy thật", "record_only": "Chỉ ghi nhận"}
PULL_S = 60.0               # một lần kéo của một kết nối tối đa ngần này giây
SWEEP_S = 150.0             # cả lượt kéo của một công ty tối đa ngần này giây
PARALLEL = 4                # số kết nối kéo cùng lúc trong một lượt
PER_SWEEP = 20              # một lượt quét của một công ty kéo tối đa ngần này kết nối
PENDING_PER_SWEEP = 200     # mục thử lại mỗi lượt
PENDING_TRIES = 5
KEEP_DAYS = 30
DUE: LiteralString = ("SELECT tenant_id FROM connectors.connections WHERE status = 'active' AND next_try_at <= now()"
                      " UNION SELECT p.tenant_id FROM connectors.pending p JOIN connectors.connections c ON c.id = p.connection_id"
                      " WHERE p.state = 'retry' AND p.next_try_at <= now() AND c.status = 'active'")


def hook_url(tenant_id: UUID, connection_id: UUID) -> str:
    return f"{settings.public_api_url.rstrip('/')}/v1/hooks/connectors/{tenant_id}/{connection_id}"


def _backoff(fails: int, kind: str, retry_after_s: int, every_min: int) -> timedelta:
    if kind == "rate_limit" and retry_after_s:
        return timedelta(seconds=min(retry_after_s, 3600))
    if kind in ("auth", "secrets", "missing"):
        return timedelta(hours=1)   # cần người sửa (khoá, gói) — thử lại thưa, sửa xong bấm "Đồng bộ ngay"
    return timedelta(minutes=min(60, max(1, every_min) * 2 ** min(fails, 6) if every_min else 2 ** min(fails, 6)))


def every_of(row: dict[str, Any]) -> int:
    """Nhịp kéo (phút) của MỘT kết nối: cài đặt ``every_min`` của kết nối (gói cho quản trị chọn trong form) nếu có, không thì nhịp mặc định của bộ nối.
    Bộ nối không kéo (``every_min = 0``) thì cài đặt không bật được việc kéo."""
    k = kc.KINDS.get(row["kind"])
    base = k.cls.every_min if k else 15
    try:
        own = int((row.get("settings") or {}).get("every_min") or 0)
    except (TypeError, ValueError):
        own = 0
    return min(max(own, 1), 24 * 60) if base and own else base


def _kind_label(kind: str) -> str:
    k = kc.KINDS.get(kind)
    return k.cls.label if k else f"{kind} (chưa cài)"


async def _run(ctx: Ctx, cid: UUID, via: str, ok: bool, items: int = 0, kind: str = "", error: str = "") -> None:
    await ctx.exec("INSERT INTO connectors.runs (connection_id, via, ok, items, error_kind, error) VALUES (%s, %s, %s, %s, %s, %s)",
                   (cid, via, ok, items, kind, error[:500]))


async def _fail(ctx: Ctx, row: dict[str, Any], kind: str, message: str, retry_after_s: int = 0, *, via: str = "pull") -> None:
    every = every_of(row)
    wait = _backoff(row["fails"] + 1, kind, retry_after_s, every)
    await ctx.exec("""UPDATE connectors.connections SET last_sync_at = now(), last_error = %s, error_kind = %s, fails = fails + 1,
                      next_try_at = now() + %s, updated_at = now() WHERE id = %s""", (message[:500], kind, wait, row["id"]))
    await _run(ctx, row["id"], via, False, 0, kind, message)


def _why(e: BaseException) -> str:
    """Câu lỗi của MỘT mục giao hỏng: lỗi nghiệp vụ của gói (``Invalid``…) giữ câu của gói — gói KHÔNG đưa dữ liệu khách vào câu (runbook 22); lỗi lạ chỉ
    giữ tên lớp (câu của nó có thể mang giá trị của mục), chi tiết ở log máy chủ."""
    if isinstance(e, (Invalid, NotFound, Conflict, Denied)):
        return str(e)[:300]
    log.warning("connectors: lệnh nhận lỗi lạ %s", type(e).__name__, exc_info=True)
    return f"Lệnh nhận lỗi ({type(e).__name__})"


async def _deliver(ctx: Ctx, row: dict[str, Any], items: list[dict[str, Any]], via: str) -> tuple[int, int]:
    """Giao từng mục cho lệnh nhận của gói, mỗi mục một điểm lưu. (đã nhận, đưa vào hàng thử lại)."""
    receive = kc.KINDS[row["kind"]].receive
    call = ctx.call_record_only if row["mode"] == "record_only" else ctx.call_system
    done = parked = 0
    for item in items:
        try:
            async with ctx.conn.transaction():
                await call(receive, connection_id=row["id"], item=item, via=via)
            done += 1
        except Exception as e:   # mục làm lệnh nhận lỗi — kể cả lỗi lạ (thiếu trường, ràng buộc DB): mục chờ thử lại, mục khác vẫn vào, lượt không chết
            await ctx.exec("INSERT INTO connectors.pending (connection_id, item, via, error, next_try_at) VALUES (%s, %s, %s, %s, now() + interval '5 minutes')",
                           (row["id"], json.dumps(item, default=str), via, _why(e)))
            parked += 1
    return done, parked


# ── Bước chuẩn bị (NGOÀI giao dịch): nạp kết nối + khoá bằng một giao dịch ĐỌC ngắn, rồi gọi hệ ngoài ──

async def _load(prep: Prep, ids: list[UUID] | None) -> list[tuple[dict[str, Any], kc.Connector | None, str]]:
    """[(dòng, bộ nối | None, lý do không dùng được)] — kết nối tới hạn (``ids`` None) hoặc đúng các kết nối nêu."""
    out: list[tuple[dict[str, Any], kc.Connector | None, str]] = []
    async with db.tenant_tx(prep.p, read_only=True) as conn:
        on = await packs.enabled(conn, prep.p.tenant_id)
        if ids is None:
            cur = await conn.execute("""SELECT id FROM connectors.connections WHERE status = 'active' AND next_try_at <= now()
                                        ORDER BY next_try_at LIMIT %s""", (PER_SWEEP,))
            ids = [r["id"] for r in await cur.fetchall()]
        for cid in ids:
            try:
                row, inst = await kc.build(conn, cid)
            except LookupError:
                continue
            except (kc.ConnectorError, kc.SecretsOff) as e:
                cur = await conn.execute("SELECT * FROM connectors.connections WHERE id = %s", (cid,))
                r = await cur.fetchone()
                if r is not None:
                    out.append((dict(r), None, kc.error_of(e)[1]))
                continue
            pack = registry.MODULE_PACK.get(kc.KINDS[row["kind"]].owner, "base")
            if pack != "base" and pack not in on:
                out.append((row, None, f"Gói “{registry.pack_title(pack)}” đang tắt — bật lại gói thì kết nối chạy tiếp"))
            elif miss := kc.missing_secrets(type(inst), set(inst.secret)):
                out.append((row, None, "Chưa có khoá: " + ", ".join(miss)))
            else:
                out.append((row, inst, ""))
    return out


async def _pull_one(row: dict[str, Any], inst: kc.Connector, deadline: float) -> dict[str, Any]:
    left = deadline - asyncio.get_running_loop().time()
    if left <= 1:
        return {"id": row["id"], "cursor": row["cursor"], "skip": True}   # hết giờ của lượt: kết nối này kéo ở lượt sau, không tính lỗi
    try:
        batch = await asyncio.wait_for(inst.pull(row["cursor"]), min(PULL_S, left))
        if len(batch.items) > kc.MAX_ITEMS:
            raise kc.ConnectorError("other", f"Bộ nối trả {len(batch.items)} mục một lô (tối đa {kc.MAX_ITEMS})")
        return {"id": row["id"], "cursor": row["cursor"], "batch": batch}
    except Exception as e:   # lỗi của hệ ngoài / bộ nối không làm chết lượt của kết nối khác
        if not isinstance(e, kc.ConnectorError):
            log.warning("connectors: bộ nối %s lỗi khi kéo (%s)", row["kind"], type(e).__name__)
        return {"id": row["id"], "cursor": row["cursor"], "error": kc.error_of(e)}


async def _prepare_sync(prep: Prep, inp: NoInput) -> list[dict[str, Any]]:
    """Kéo các kết nối tới hạn, tối đa ``PARALLEL`` kết nối cùng lúc, cả lượt không quá ``SWEEP_S`` giây (kết nối chưa tới lượt kéo ở lượt sau)."""
    deadline = asyncio.get_running_loop().time() + SWEEP_S
    gate = asyncio.Semaphore(PARALLEL)
    results: list[dict[str, Any]] = []
    jobs = []
    for row, inst, why in await _load(prep, None):
        if inst is None:
            results.append({"id": row["id"], "cursor": row["cursor"], "error": ("missing", why, 0)})
        elif not type(inst).every_min:   # bộ nối chỉ webhook / chỉ đẩy ra: không kéo
            results.append({"id": row["id"], "cursor": row["cursor"], "idle": True})
        else:
            async def one(row: dict[str, Any] = row, inst: kc.Connector = inst) -> dict[str, Any]:
                async with gate:
                    return await _pull_one(row, inst, deadline)
            jobs.append(one())
    return results + list(await asyncio.gather(*jobs))


@m.command("sync", resource="connectors", title="Đồng bộ kết nối ngoài", internal=True, agent="deny", sweep=DUE, prepare=_prepare_sync)
async def sync(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Vòng quét của MỘT công ty. ``ctx.prepared`` None (gọi lồng / chạy thử) ⇒ chỉ thử lại mục chờ."""
    out = {"connections": 0, "items": 0, "parked": 0, "errors": 0, "retried": 0}
    for res in ctx.prepared or []:
        row = await ctx.one("SELECT * FROM connectors.connections WHERE id = %s FOR UPDATE", (res["id"],))
        if row is None or row["status"] != "active" or row["cursor"] != res["cursor"]:
            continue   # tắt / gỡ / lượt khác đã tiến con trỏ giữa bước chuẩn bị và lúc này
        out["connections"] += 1
        if res.get("skip"):
            out["connections"] -= 1
            continue
        if res.get("idle"):
            await ctx.exec("UPDATE connectors.connections SET next_try_at = now() + interval '1 day' WHERE id = %s", (row["id"],))
            continue
        if err := res.get("error"):
            await _fail(ctx, row, *err)
            out["errors"] += 1
            continue
        batch: kc.Batch = res["batch"]
        try:   # mỗi kết nối một điểm lưu: lỗi ngoài từng mục (hiếm — vd khoá dòng) không kéo phần đã giao của kết nối khác trong cùng lượt
            async with ctx.conn.transaction():
                done, parked = await _deliver(ctx, row, batch.items, "pull")
                every = every_of(row)
                await ctx.exec("""UPDATE connectors.connections SET cursor = %s, last_sync_at = now(), last_ok_at = now(), last_error = '', error_kind = '',
                                  fails = 0, next_try_at = now() + %s, updated_at = now() WHERE id = %s""",
                               (batch.cursor[:2000], timedelta(0) if batch.more else timedelta(minutes=every), row["id"]))
        except Exception as e:
            log.warning("connectors: giao lô của kết nối %s lỗi (%s)", row["id"], type(e).__name__, exc_info=True)
            await _fail(ctx, row, "deliver", f"Barista không nhận được lô ({type(e).__name__})")
            out["errors"] += 1
            continue
        note = await _verify_routes(ctx, row, batch.claimed) if batch.claimed else ""
        await _run(ctx, row["id"], "pull", True, done, "deliver" if parked else "",
                   " · ".join(x for x in (f"{parked} mục chờ thử lại" if parked else "", note) if x))
        out["items"] += done
        out["parked"] += parked
    out["retried"] = await _retry_pending(ctx)
    await ctx.exec("DELETE FROM connectors.events WHERE received_at < now() - make_interval(days => %s)", (KEEP_DAYS,))
    await ctx.exec("DELETE FROM connectors.runs WHERE at < now() - make_interval(days => %s)", (KEEP_DAYS,))
    await ctx.exec("DELETE FROM connectors.pending WHERE state = 'failed' AND next_try_at < now() - make_interval(days => %s)", (KEEP_DAYS,))   # mục bỏ mang dữ liệu khách: không giữ mãi
    return out


async def _verify_routes(ctx: Ctx, row: dict[str, Any], claimed: tuple[str, ...]) -> str:
    """Khoá định tuyến mà lượt kéo vừa CHỨNG MINH token của kết nối đọc được ⇒ có hiệu lực cho webhook chung. Khoá đã thuộc kết nối khác (đã xác minh
    trước) ⇒ giữ nguyên chủ cũ, ghi chú trên nhật ký đồng bộ (nhà vận hành gỡ kết nối cũ nếu đổi chủ thật)."""
    taken = []
    for k in kc.clean_keys(list(claimed)):
        try:
            async with ctx.conn.transaction():
                await ctx.exec("""UPDATE connectors.routes SET verified_at = now() WHERE connection_id = %s AND route_key = %s AND verified_at IS NULL""",
                               (row["id"], k))
        except psycopg.errors.UniqueViolation:
            taken.append(k)
    return f"{len(taken)} khoá định tuyến đã thuộc kết nối khác: {', '.join(taken[:5])}" if taken else ""


class WakeIn(Input):
    kind: str = field("Bộ nối", max_length=40)
    keys: list[str] = field("Khoá định tuyến", [])


@m.command("wake_routes", resource="connectors", title="Đánh thức kết nối theo webhook chung", internal=True, agent="deny")
async def wake_routes(ctx: Ctx, inp: WakeIn) -> dict[str, int]:
    """Cửa webhook chung (1.66) đã kiểm chữ ký bằng khoá bản cài: đặt lượt kéo NGAY cho kết nối đang chạy của công ty này sở hữu (đã xác minh) các khoá."""
    n = await ctx.val("""WITH w AS (UPDATE connectors.connections c SET next_try_at = now() WHERE c.status = 'active' AND c.kind = %s
                                    AND EXISTS (SELECT 1 FROM connectors.routes r WHERE r.connection_id = c.id AND r.route_key = ANY(%s)
                                                AND r.verified_at IS NOT NULL) RETURNING 1)
                         SELECT count(*) FROM w""", (inp.kind, kc.clean_keys(inp.keys)))
    return {"woken": int(n or 0)}


async def _retry_pending(ctx: Ctx) -> int:
    rows = await ctx.all("""SELECT p.id, p.item, p.via, p.attempts, c.id AS connection_id, c.kind, c.mode, c.status
                            FROM connectors.pending p JOIN connectors.connections c ON c.id = p.connection_id
                            WHERE p.state = 'retry' AND p.next_try_at <= now() AND c.status = 'active'   -- kết nối tạm dừng: mục nằm chờ, không chiếm chỗ mục khác
                            ORDER BY p.id LIMIT %s FOR UPDATE OF p SKIP LOCKED""", (PENDING_PER_SWEEP,))
    done = 0
    for r in rows:
        if r["status"] != "active" or r["kind"] not in kc.KINDS:
            continue
        receive = kc.KINDS[r["kind"]].receive
        call = ctx.call_record_only if r["mode"] == "record_only" else ctx.call_system
        try:
            async with ctx.conn.transaction():
                await call(receive, connection_id=r["connection_id"], item=r["item"], via=r["via"])
            await ctx.exec("DELETE FROM connectors.pending WHERE id = %s", (r["id"],))
            done += 1
        except Exception as e:
            await ctx.exec("""UPDATE connectors.pending SET attempts = attempts + 1, error = %s,
                                  state = CASE WHEN attempts + 1 >= %s THEN 'failed' ELSE 'retry' END,
                                  next_try_at = now() + make_interval(mins => 5 * (attempts + 1)) WHERE id = %s""", (_why(e), PENDING_TRIES, r["id"]))
    return done


# ── Webhook VÀO: một cửa cho mọi bộ nối ──

class HookIn(Input):
    ref: str = field("Mã kết nối", max_length=100)
    headers: dict[str, str] = field("Header", {}, audit=False)   # mang khoá / chữ ký — nhật ký chỉ giữ TÊN tiêu đề
    body_b64: str = field("Thân (base64)", "", max_length=400_000, audit=False)   # dữ liệu khách — nhật ký giữ cỡ + sha256


@m.command("receive_hook", resource="connectors", title="Nhận webhook kết nối ngoài", internal=True, agent="deny")
async def receive_hook(ctx: Ctx, inp: HookIn) -> dict[str, Any]:
    try:
        cid = UUID(inp.ref)
    except ValueError:
        raise NotFound("Không có kết nối này") from None
    try:
        row, inst = await kc.build(ctx.conn, cid)
    except LookupError:
        raise NotFound("Không có kết nối này") from None
    except (kc.ConnectorError, kc.SecretsOff) as e:
        log.warning("connectors: webhook tới kết nối %s không dùng được: %s", cid, kc.error_of(e)[1])
        raise Denied("Kết nối chưa nhận webhook") from None
    cls = type(inst)
    if not cls.webhook or not inst.secret.get("webhook"):
        raise Denied("Kết nối chưa bật nhận webhook (chưa có khoá webhook)")
    if row["status"] != "active":
        raise Denied("Kết nối đang tạm dừng")
    try:
        body = base64.b64decode(inp.body_b64, validate=True)
    except (binascii.Error, ValueError):
        raise Invalid("Thân webhook không đọc được") from None
    if why := inst.hook_check(inp.headers, body):
        log.warning("connectors: webhook tới kết nối %s bị từ chối: %s", cid, why)
        raise Denied(f"Webhook bị từ chối: {kc.inbound.REASONS.get(why, why)}")
    try:
        payload = json.loads(body.decode("utf-8")) if body else None
        hook = inst.hook_parse(payload)
    except (UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError):
        hook = None
    if hook is None or not hook.event_id.strip():
        log.warning("connectors: webhook tới kết nối %s thiếu mã sự kiện / dạng lạ — bỏ qua", cid)
        raise Invalid("Webhook thiếu mã sự kiện")
    fresh = await ctx.val("INSERT INTO connectors.events (connection_id, event_id) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING true",
                          (cid, hook.event_id[:200]))
    if not fresh:
        return {"accepted": True, "duplicate": True}
    done = parked = 0
    if hook.items:
        pack = registry.MODULE_PACK.get(kc.KINDS[row["kind"]].owner, "base")
        if pack != "base" and pack not in await packs.enabled(ctx.conn, row["tenant_id"]):
            raise Denied("Gói của kết nối đang tắt")
        done, parked = await _deliver(ctx, row, hook.items[:kc.MAX_ITEMS], "hook")
        await _run(ctx, cid, "hook", True, done, "deliver" if parked else "", f"{parked} mục chờ thử lại" if parked else "")
    if hook.wake:
        await ctx.exec("UPDATE connectors.connections SET next_try_at = now() WHERE id = %s", (cid,))
    return {"accepted": True, "duplicate": False, "items": done, "parked": parked}


m.inbound("connectors.receive_hook")


# ── Họ ``connector`` (gói gọi qua ``barista.api.connectors``): lưu / liệt kê kết nối của CHÍNH gói ──

class SaveIn(Input):
    kind: str = field("Bộ nối", max_length=40)
    label: str = field("Tên kết nối", max_length=120)
    settings: dict[str, Any] = field("Cài đặt", {})
    secrets: dict[str, str] = field("Khoá", {}, audit=False)
    connection_id: UUID | None = field("Kết nối (sửa)", None)
    mode: Literal["live", "record_only"] | None = field("Chế độ", None)


@m.command("save", resource=None, title="Lưu kết nối ngoài", internal=True, agent="deny", family="connector",
           why="barista.api.connectors.save: gói kết nối tạo / sửa kết nối của bộ nối CHÍNH NÓ khai (owned_by theo module khai bộ nối) từ lệnh có form "
               "của gói; khoá mã hoá ở đây, không trả ra")
async def save(ctx: Ctx, inp: SaveIn) -> dict[str, Any]:
    kind = kc.KINDS.get(inp.kind)
    if kind is None:
        raise NotFound(f"Không có bộ nối {inp.kind}")
    ctx.owned_by(kind.owner, f"bộ nối {inp.kind}")
    allowed = {s.name for s in kind.cls.secrets}
    if bad := sorted(set(inp.secrets) - allowed):
        raise Invalid(f"Bộ nối {kind.cls.label} không có khoá: {', '.join(bad)}")
    for key in kind.cls.url_settings:   # địa chỉ người nhập: báo sớm khi trỏ vào máy nội bộ (kiểm đủ ở connectors.client_for mỗi lần gọi)
        if (v := str(inp.settings.get(key) or "")) and (why := kc.check_url_setting(v)):
            raise Invalid(f"Địa chỉ “{key}” {why}")
    if inp.connection_id is None:
        cid = uuid4()
        await ctx.exec("""INSERT INTO connectors.connections (id, owner, kind, label, settings, mode) VALUES (%s, %s, %s, %s, %s, %s)""",
                       (cid, kind.owner, inp.kind, inp.label.strip(), json.dumps(inp.settings, default=str), inp.mode or "live"))
    else:
        cid = inp.connection_id
        n = await ctx.val("""UPDATE connectors.connections SET label = %s, settings = %s, mode = coalesce(%s, mode), next_try_at = now(), updated_at = now()
                             WHERE id = %s AND owner = %s AND kind = %s RETURNING 1""",
                          (inp.label.strip(), json.dumps(inp.settings, default=str), inp.mode, cid, kind.owner, inp.kind))
        if not n:
            raise NotFound("Không có kết nối này")
    for name, value in inp.secrets.items():
        if value.strip():
            await _put_secret(ctx, cid, name, value.strip())
    if kind.cls.shared_hook:   # webhook chung (1.66): khoá định tuyến kết nối KHAI — chưa hiệu lực tới khi lượt kéo bằng token của nó xác minh
        keys = kc.clean_keys(kind.cls.claims(inp.settings))
        await ctx.exec("DELETE FROM connectors.routes WHERE connection_id = %s AND NOT (route_key = ANY(%s))", (cid, keys))
        for k in keys:
            await ctx.exec("""INSERT INTO connectors.routes (connection_id, kind, route_key) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                           (cid, inp.kind, k))
    return {"connection_id": str(cid), "webhook_url": hook_url(ctx.p.tenant_id, cid) if kind.cls.webhook else ""}


async def _put_secret(ctx: Ctx, cid: UUID, name: str, value: str) -> None:
    try:
        sealed = kc.seal(ctx.p.tenant_id, cid, name, value)
    except kc.SecretsOff as e:
        raise Invalid(str(e)) from None
    await ctx.exec("""INSERT INTO connectors.secrets (connection_id, name, sealed, hint, updated_by) VALUES (%s, %s, %s, %s, %s)
                      ON CONFLICT (tenant_id, connection_id, name) DO UPDATE SET sealed = EXCLUDED.sealed, hint = EXCLUDED.hint,
                          updated_by = EXCLUDED.updated_by, updated_at = now()""", (cid, name, sealed, kc.hint(value), ctx.p.user_id))
    await ctx.exec("UPDATE connectors.connections SET next_try_at = now(), updated_at = now() WHERE id = %s", (cid,))


class OwnedIn(Input):
    kind: str = field("Bộ nối", max_length=40)


@m.query("of_kind", resource=None, title="Kết nối của một bộ nối", internal=True, agent="deny", family="connector",
         why="barista.api.connectors.connections: gói liệt kê kết nối của bộ nối CHÍNH NÓ khai (owned_by) để hiện trên màn của gói / chọn kết nối trong form "
             "— không có khoá, chỉ mã, tên, trạng thái, cài đặt, lần đồng bộ")
async def of_kind(ctx: Ctx, inp: OwnedIn) -> list[dict[str, Any]]:
    kind = kc.KINDS.get(inp.kind)
    if kind is None:
        return []
    ctx.owned_by(kind.owner, f"bộ nối {inp.kind}")
    return await ctx.all("""SELECT id, label, status, mode, settings, last_ok_at, last_error FROM connectors.connections
                            WHERE kind = %s ORDER BY label""", (inp.kind,))


# ── Màn quản trị "Kết nối ngoài" ──

@m.query("list", resource="connectors", title="Kết nối ngoài")
async def connections(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("""SELECT c.*, (SELECT count(*) FROM connectors.pending p WHERE p.connection_id = c.id AND p.state = 'retry') AS retrying,
                                   (SELECT count(*) FROM connectors.pending p WHERE p.connection_id = c.id AND p.state = 'failed') AS failed,
                                   (SELECT string_agg(s.name || ' ' || s.hint, ' · ' ORDER BY s.name) FROM connectors.secrets s WHERE s.connection_id = c.id) AS keys
                            FROM connectors.connections c ORDER BY c.label""")
    out = []
    for r in rows:
        k = kc.KINDS.get(r["kind"])
        out.append({"id": str(r["id"]), "label": r["label"], "kind": _kind_label(r["kind"]),
                    "pack": registry.pack_title(registry.MODULE_PACK.get(k.owner, "base")) if k else "",
                    "status": r["status"], "mode": r["mode"], "health": "error" if r["fails"] else ("ok" if r["last_ok_at"] else "new"),
                    "last_ok_at": r["last_ok_at"], "last_error": r["last_error"], "error_kind": kc.ERROR_KINDS.get(r["error_kind"], ""),
                    "pending": int(r["retrying"]) + int(r["failed"]), "keys": r["keys"] or "",
                    "webhook": hook_url(ctx.p.tenant_id, r["id"]) if k and k.cls.webhook else ""})
    bad = sum(1 for r in out if r["health"] == "error")
    return ui.table(out, [
        ui.col("label", "Kết nối", width=200), ui.col("kind", "Hệ ngoài"), ui.col("pack", "Gói"),
        ui.col("status", "Trạng thái", "badge", labels=STATUS, tones={"active": "green", "paused": "gray"}),
        ui.col("mode", "Chế độ", "badge", labels=MODE, tones={"live": "blue", "record_only": "orange"}),
        ui.col("health", "Đồng bộ", "badge", labels={"ok": "Ổn", "error": "Lỗi", "new": "Chưa chạy"}, tones={"ok": "green", "error": "red", "new": "gray"}),
        ui.col("last_ok_at", "Lần đạt gần nhất", "datetime"), ui.col("last_error", "Lỗi", width=260), ui.col("pending", "Mục chờ", "number"),
        ui.col("keys", "Khoá đã đặt"), ui.col("webhook", "Địa chỉ webhook", width=260),
    ], title="Kết nối ngoài", kpis=[ui.kpi("Kết nối", len(out)), ui.kpi("Đang lỗi", bad, tone="red" if bad else None)],
        empty="Chưa có kết nối — thêm ở màn của gói kết nối (vd tổng đài, kênh bán, quảng cáo). Gói của đối tác dùng chung màn này.",
        note="Khoá mã hoá khi lưu, chỉ hiện bốn ký tự cuối. Lỗi khoá ⇒ đặt lại khoá rồi bấm “Đồng bộ ngay”.")


class ConnRef(Input):
    connection_id: UUID = field("Kết nối", options_query="connectors.options")


@m.query("options", resource="connectors", title="Chọn kết nối")
async def options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options([{"value": str(r["id"]), "label": f"{r['label']} · {_kind_label(r['kind'])}"}
                       for r in await ctx.all("SELECT id, label, kind FROM connectors.connections ORDER BY label")])


class SecretRef(Input):
    connection_id: UUID = field("Kết nối", hidden=True)


@m.query("secret_options", resource="connectors", title="Khoá của kết nối")
async def secret_options(ctx: Ctx, inp: SecretRef) -> list[dict[str, str]]:
    kind = await ctx.val("SELECT kind FROM connectors.connections WHERE id = %s", (inp.connection_id,))
    k = kc.KINDS.get(kind or "")
    return [{"value": s.name, "label": s.label, **({"hint": s.help} if s.help else {})} for s in (k.cls.secrets if k else ())]


class SetSecretIn(Input):
    connection_id: UUID = field("Kết nối", hidden=True)
    name: str = field("Khoá", options_query="connectors.secret_options", options_args={"connection_id": "connection_id"})
    value: str = field("Giá trị", max_length=4000, audit=False, help="Dán khoá từ trang quản trị của hệ ngoài — lưu mã hoá, chỉ hiện bốn ký tự cuối")


@m.command("set_secret", resource="connectors", action="manage", title="Đặt khoá kết nối", agent="deny", category="setup")
async def set_secret(ctx: Ctx, inp: SetSecretIn) -> dict[str, Any]:
    kind = await ctx.val("SELECT kind FROM connectors.connections WHERE id = %s", (inp.connection_id,))
    if kind is None:
        raise NotFound("Không có kết nối này")
    k = kc.KINDS.get(kind)
    if k is None or inp.name not in {s.name for s in k.cls.secrets}:
        raise Invalid("Bộ nối không có khoá này")
    if not inp.value.strip():
        raise Invalid("Khoá trống")
    await _put_secret(ctx, inp.connection_id, inp.name, inp.value.strip())
    return {"connection_id": str(inp.connection_id), "summary": f"Đã đặt khoá {inp.name} ({kc.hint(inp.value)})"}


class StatusIn(Input):
    connection_id: UUID = field("Kết nối", hidden=True)
    status: Literal["active", "paused"] = field("Trạng thái", options=STATUS)


@m.command("set_status", resource="connectors", action="manage", title="Tạm dừng / chạy lại kết nối", agent="propose", category="setup")
async def set_status(ctx: Ctx, inp: StatusIn) -> dict[str, Any]:
    n = await ctx.val("""UPDATE connectors.connections SET status = %s, next_try_at = now(), fails = CASE WHEN %s = 'active' THEN 0 ELSE fails END,
                         updated_at = now() WHERE id = %s RETURNING label""", (inp.status, inp.status, inp.connection_id))
    if n is None:
        raise NotFound("Không có kết nối này")
    return {"summary": f"{n}: {STATUS[inp.status].lower()}"}


class ModeIn(Input):
    connection_id: UUID = field("Kết nối", hidden=True)
    mode: Literal["live", "record_only"] = field("Chế độ", options=MODE,
                                                 help="Chỉ ghi nhận: dữ liệu vào đủ nhưng không chia việc, không báo — khi công ty còn chạy song song hệ khác")


@m.command("set_mode", resource="connectors", action="manage", title="Đổi chế độ kết nối", agent="propose", risk="authority", category="setup")   # sang "chạy thật" = bắt đầu chia việc / báo người — luôn có người bấm
async def set_mode(ctx: Ctx, inp: ModeIn) -> dict[str, Any]:
    kind = await ctx.val("SELECT kind FROM connectors.connections WHERE id = %s", (inp.connection_id,))
    k = kc.KINDS.get(kind or "")
    if k and k.cls.mode_command:   # gói có luật riêng khi chuyển sang chạy thật (vd không để hai đường cùng chia lead) — đổi ở lệnh của gói
        raise Invalid(f"Kết nối {k.cls.label} đổi chế độ ở lệnh của gói ({k.cls.mode_command}) — gói kiểm luật riêng trước khi chuyển")
    n = await ctx.val("UPDATE connectors.connections SET mode = %s, updated_at = now() WHERE id = %s RETURNING label", (inp.mode, inp.connection_id))
    if n is None:
        raise NotFound("Không có kết nối này")
    return {"summary": f"{n}: {MODE[inp.mode].lower()}"}


class RefIn(Input):
    connection_id: UUID = field("Kết nối", hidden=True)


@m.command("sync_now", resource="connectors", title="Đồng bộ ngay", agent="propose", risk="low", category="setup")
async def sync_now(ctx: Ctx, inp: RefIn) -> dict[str, Any]:
    n = await ctx.val("""UPDATE connectors.connections SET next_try_at = now(), updated_at = now() WHERE id = %s AND status = 'active' RETURNING label""",
                      (inp.connection_id,))
    if n is None:
        raise Invalid("Kết nối không có hoặc đang tạm dừng")
    # mục bị bỏ được thử lại TỪ ĐẦU (đủ lượt) — người bấm "Đồng bộ ngay" sau khi sửa nguyên nhân
    await ctx.exec("UPDATE connectors.pending SET state = 'retry', attempts = 0, next_try_at = now() WHERE connection_id = %s", (inp.connection_id,))
    return {"summary": f"{n}: sẽ đồng bộ trong vòng một phút"}


@m.command("remove", resource="connectors", action="manage", title="Gỡ kết nối", agent="deny", category="setup")
async def remove(ctx: Ctx, inp: RefIn) -> dict[str, Any]:
    """Xoá kết nối + khoá + hàng chờ + nhật ký đồng bộ. Dữ liệu đã nhận vào Barista (lead, đơn…) GIỮ NGUYÊN — thuộc module sở hữu."""
    n = await ctx.val("DELETE FROM connectors.connections WHERE id = %s RETURNING label", (inp.connection_id,))
    if n is None:
        raise NotFound("Không có kết nối này")
    return {"summary": f"Đã gỡ kết nối {n} (dữ liệu đã nhận vào Barista giữ nguyên)"}


async def _prepare_check(prep: Prep, inp: RefIn) -> dict[str, Any]:
    got = await _load(prep, [inp.connection_id])
    if not got:
        return {"ok": False, "kind": "missing", "message": "Không có kết nối này"}
    _, inst, why = got[0]
    if inst is None:
        return {"ok": False, "kind": "missing", "message": why}
    try:
        return {"ok": True, "message": (await asyncio.wait_for(inst.check(), PULL_S))[:300]}
    except Exception as e:
        kind, message, _ = kc.error_of(e)
        return {"ok": False, "kind": kind, "message": message}


@m.command("check", resource="connectors", title="Thử kết nối", agent="propose", risk="low", category="setup", prepare=_prepare_check)
async def check(ctx: Ctx, inp: RefIn) -> dict[str, Any]:
    got = ctx.prepared
    if got is None:
        raise Invalid("Thử kết nối cần gọi hệ ngoài — bấm từ màn Kết nối ngoài")
    row = await ctx.one("SELECT * FROM connectors.connections WHERE id = %s", (inp.connection_id,))
    if row is None:
        raise NotFound("Không có kết nối này")
    await _run(ctx, row["id"], "check", got["ok"], 0, got.get("kind", ""), "" if got["ok"] else got["message"])
    if not got["ok"]:
        raise Invalid(f"Thử kết nối không đạt: {got['message']}")
    return {"summary": f"{row['label']}: đạt — {got['message']}"}


class RunsIn(Input):
    connection_id: UUID = field("Kết nối", hidden=True)


@m.query("runs", resource="connectors", title="Nhật ký đồng bộ")
async def runs(ctx: Ctx, inp: RunsIn) -> dict[str, Any]:
    rows = await ctx.all("""SELECT id, at, via, ok, items, error_kind, error FROM connectors.runs WHERE connection_id = %s ORDER BY at DESC LIMIT 200""",
                         (inp.connection_id,))
    pend = await ctx.all("""SELECT id, created_at AS at, via, false AS ok, 0 AS items, 'deliver' AS error_kind, error, state, attempts
                            FROM connectors.pending WHERE connection_id = %s ORDER BY id DESC LIMIT 100""", (inp.connection_id,))
    out = [{**r, "id": f"p{r['id']}", "what": "Mục chờ thử lại" if r["state"] == "retry" else f"Mục bỏ sau {r['attempts']} lần thử"} for r in pend] + \
          [{**r, "id": f"r{r['id']}", "what": {"pull": "Kéo", "hook": "Webhook", "check": "Thử kết nối"}[r["via"]]} for r in rows]
    for r in out:
        r["ok"] = "ok" if r["ok"] else "error"
    for r in out:
        r["error_kind"] = kc.ERROR_KINDS.get(r["error_kind"], r["error_kind"])
    return ui.table(out, [ui.col("at", "Lúc", "datetime"), ui.col("what", "Việc"), ui.col("ok", "Kết quả", "badge", labels={"ok": "Đạt", "error": "Lỗi"}, tones={"ok": "green", "error": "red"}),
                          ui.col("items", "Số mục", "number"), ui.col("error_kind", "Loại lỗi"), ui.col("error", "Lỗi", width=320)],
                    title="Nhật ký đồng bộ", empty="Chưa có lượt nào", limit=300,
                    note="Nhật ký ghi số mục và loại lỗi, không chép khoá. Mục chờ thử lại giữ dữ liệu của mục để giao lại; mục bị bỏ tự xoá sau 30 ngày.")


@m.query("health_report", resource="connectors", title="Sức khoẻ kết nối ngoài", internal=True, agent="deny", callers=("health",),
         why="health._collect (Sức khoẻ dữ liệu): kết nối ngoài nào đang lỗi / lâu không đồng bộ, mục nào bị bỏ — số đếm + tên kết nối, không dữ liệu khách")
async def health_report(ctx: Ctx, inp: NoInput) -> dict[str, Any] | None:
    rows = await ctx.all("""SELECT c.id, c.label, c.kind, c.status, c.fails, c.last_ok_at, c.last_error, c.settings,
                                   (SELECT count(*) FROM connectors.pending p WHERE p.connection_id = c.id AND p.state = 'failed') AS dropped
                            FROM connectors.connections c ORDER BY c.label""")
    if not rows:
        return None
    now = datetime.now(UTC)
    fresh = []
    for r in rows:
        every = every_of(r)
        stale = r["status"] == "active" and bool(every) and (r["last_ok_at"] is None or now - r["last_ok_at"] > timedelta(minutes=max(60, 3 * every)))
        fresh.append({"title": r["label"], "subtitle": _kind_label(r["kind"]), "at": r["last_ok_at"], "stale": stale,
                      "tone": "muted" if r["status"] == "paused" else None, "badge": "Tạm dừng" if r["status"] == "paused" else None,
                      "why": r["last_error"] or "", "open": "connectors.list"})
    failing = [r for r in rows if r["status"] == "active" and r["fails"]]
    dropped = sum(int(r["dropped"]) for r in rows)
    return {"rank": 20, "notes": ["Kết nối ngoài: đồng bộ theo lịch của từng bộ nối; lỗi khoá cần quản trị đặt lại khoá."], "fresh": fresh,
            "issues": [{"key": "connector_failing", "n": len(failing), "title": "kết nối ngoài đang lỗi", "open": "connectors.list",
                        "subtitle": ", ".join(r["label"] for r in failing[:5]), "why": "{n} kết nối đang lỗi ⇒ dữ liệu từ hệ ngoài chưa vào Barista"},
                       {"key": "connector_dropped", "n": dropped, "title": "mục từ hệ ngoài bị bỏ sau nhiều lần thử", "open": "connectors.list",
                        "why": "{n} mục Barista không nhận được ⇒ xem Nhật ký đồng bộ của kết nối"}]}


m.screens("admin", (
    Screen("connectors", "Kết nối ngoài", "link", "connectors.list", group="Hệ thống", tint="teal",
           open=Open("connectors.runs", {"connection_id": "id"}, "Nhật ký đồng bộ"),
           row_actions=(A("connectors.check", "Thử kết nối", bind={"connection_id": "id"}, style="primary"),
                        A("connectors.sync_now", bind={"connection_id": "id"}, when={"status": ["active"]}),
                        A("connectors.set_secret", bind={"connection_id": "id"}),
                        A("connectors.set_status", "Tạm dừng", bind={"connection_id": "id"}, preset={"status": "paused"}, when={"status": ["active"]}),
                        A("connectors.set_status", "Chạy lại", bind={"connection_id": "id"}, preset={"status": "active"}, when={"status": ["paused"]}),
                        A("connectors.set_mode", bind={"connection_id": "id"}, prefill={"mode": "mode"}),
                        A("connectors.remove", bind={"connection_id": "id"}, style="destructive"))),
), rank=14)
