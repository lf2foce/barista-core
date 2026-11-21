"""ĐỀ XUẤT BUỔI SÁNG (quyết định 0074 A) — trợ lý chủ động mà vẫn an toàn.

Module tự khai NGUỒN GỢI Ý (``@m.suggests``): truy vấn chạy bằng quyền người nhận, trả việc nên làm kèm lý do — code chọn, không model.
Hub này (không biết module nào) mỗi sáng, với từng người: gom gợi ý, CHẠY THỬ từng lệnh bằng quyền trợ lý của người đó
(``executor.propose`` — luôn chỉ là đề xuất), giữ tối đa ``MAX_CARDS`` thẻ chạy được vào ``core.proposals``. Người bấm Xác nhận ⇒ chạy lại
bằng quyền NGƯỜI, kiểm căn cứ như thẻ trong chat (0071). Nhịp: vòng nền mỗi công ty một lượt / ngày sau ``START_HOUR`` giờ CỦA CÔNG TY (0169); người mở app
mà hôm nay chưa có lượt nào thì soạn ngay cho riêng người đó.
"""

import asyncio
import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.db import Conn, db
from barista.kernel import attention, auth, clock, executor, registry
from barista.kernel import preview as previews
from barista.kernel.clock import today
from barista.kernel.forms import field_specs
from barista.kernel.principal import Principal

log = logging.getLogger(__name__)
MAX_CARDS = 3
START_HOUR = 6


async def suggestions(p: Principal) -> list[dict[str, Any]]:
    """Mọi gợi ý cho ``p`` từ các nguồn module khai — nguồn lỗi / gói tắt thì bỏ qua (không làm hỏng cả buổi sáng)."""
    out: list[tuple[int, int, dict[str, Any]]] = []
    for src in attention.SOURCES:
        r = await executor.query(p, src.query, {})
        if not r.ok or not isinstance(r.output, list):
            continue
        out += [(int(s.get("rank", 500)), src.rank, s) for s in r.output if isinstance(s, dict) and s.get("command") and s.get("key")]
    return [s for _, _, s in sorted(out, key=lambda x: (x[0], x[1]))]


async def prepare(p: Principal, day: date | None = None) -> int:
    """Soạn thẻ cho MỘT người (tối đa ``MAX_CARDS`` đang mở trong ngày); chạy lại cùng ngày không soạn trùng khoá. Trả số thẻ mới."""
    if not p.ai:
        return 0
    async with db.tenant_tx(p) as conn:
        day = day or today()   # trong giao dịch: "hôm nay" theo múi giờ công ty (0169)
        await conn.execute("UPDATE core.proposals SET status = 'expired' WHERE status = 'open' AND day < %s", (day,))
        # thẻ do ứng dụng AI ngoài soạn (0228) không chiếm chỗ của đề xuất buổi sáng
        cur = await conn.execute("SELECT key, status FROM core.proposals WHERE day = %s AND key NOT LIKE %s", (day, OUTSIDE + ":%"))
        rows = await cur.fetchall()
    have = {r["key"] for r in rows}
    room = MAX_CARDS - sum(1 for r in rows if r["status"] == "open")
    made, agent = 0, p.as_agent()
    for s in await suggestions(p):
        if made >= room:
            break
        if s["key"] in have:
            continue
        with executor.tracing(_trace(p.user_id, day, s["key"])):   # cùng vết với lần người bấm Xác nhận thẻ này
            r = await executor.propose(agent, s["command"], s.get("input") or {}, reason=str(s.get("reason") or ""))
        if r.status != "proposed" or not isinstance(r.output, dict):
            continue   # không chạy được lúc này (căn đã bán, việc đã xong…) ⇒ không đưa thẻ hỏng cho người
        o: dict[str, Any] = r.output
        async with db.tenant_tx(p) as conn:
            await conn.execute(
                """INSERT INTO core.proposals (user_id, day, key, command, title, reason, input, basis, rank, preview)
                   VALUES (core.me(), %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                (day, s["key"], o["command"], o["brief"]["title"], o["brief"]["reason"], Jsonb(o["input"]),
                 Jsonb(o.get("basis")) if o.get("basis") else None, int(s.get("rank", 500)), Jsonb(o["brief"])))
        have.add(s["key"])
        made += 1
    return made


async def file_cards(p: Principal, prefix: str, cards: list[dict[str, Any]], reason: str, *, rank: int = 50) -> int:
    """Thẻ ĐỀ XUẤT trợ lý soạn NGOÀI lượt chat (việc theo dõi — 0142) ⇒ ``core.proposals`` của ``p`` hôm nay: cùng bảng, cùng đường Xác nhận / Bỏ
    (``decide``: chạy bằng quyền NGƯỜI, kiểm căn cứ 0071). Khoá ``<prefix>:<thứ tự>`` ⇒ chạy lại trong ngày không nhân đôi (UNIQUE người, ngày, khoá)."""
    props = [c for c in cards if c.get("kind") == "proposal" and c.get("command")]
    if not props:
        return 0
    n = 0
    async with db.tenant_tx(p) as conn:
        day = today()   # trong giao dịch: múi giờ công ty (0169)
        for i, c in enumerate(props, 1):
            cur = await conn.execute(
                """INSERT INTO core.proposals (user_id, day, key, command, title, reason, input, basis, rank, preview)
                   VALUES (core.me(), %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id""",
                (day, f"{prefix}:{i}", c["command"], str(c.get("title") or c["command"])[:300], reason[:300], Jsonb(c.get("input") or {}),
                 Jsonb(c["basis"]) if c.get("basis") else None, rank, Jsonb(c["brief"]) if c.get("brief") else None))
            n += 1 if await cur.fetchone() else 0
    return n


OUTSIDE = "mcp"          # tiền tố khoá của thẻ do ứng dụng AI ngoài soạn (0228)
AUTO = "auto"            # tiền tố khoá của thẻ do LUẬT TỰ ĐỘNG soạn (ADR 0246 — hành động "đề xuất")
OUTSIDE_PER_DAY = 20     # trần thẻ một người nhận từ ứng dụng AI ngoài mỗi ngày — app bị dẫn dắt sai không làm ngập bàn việc


async def file_outside(p: Principal, source: str, source_name: str, command: str, inp: dict[str, Any], note: str = "") -> dict[str, Any]:
    """Ứng dụng AI của CHÍNH người này (MCP, phạm vi ``propose`` — 0228) soạn MỘT đề xuất: chạy thử bằng quyền trợ lý của họ (``executor.propose`` — luôn
    chỉ là đề xuất, lệnh trợ lý bị cấm vẫn bị cấm) rồi thành thẻ trong ``core.proposals`` hôm nay — cùng bảng, cùng đường Xác nhận / Bỏ (``decide``:
    chạy bằng quyền NGƯỜI, kiểm căn cứ 0071) với đề xuất buổi sáng. Không có đường nào từ đây tới lệnh chạy thật. ``note`` là chữ ứng dụng ngoài
    viết — chỉ hiện cho người đọc, không phải căn cứ."""
    if not p.ai:
        return {"status": "denied", "error": "Công ty đã tắt trợ lý AI"}
    if await count_today(p, OUTSIDE) >= OUTSIDE_PER_DAY:
        return {"status": "limit", "error": f"Hôm nay đã soạn {OUTSIDE_PER_DAY} đề xuất từ ứng dụng AI — người dùng xử lý bớt trong Barista rồi soạn tiếp."}
    async with db.tenant_tx(p, read_only=True):
        day = today()   # trong giao dịch: múi giờ công ty (0169)
    key = f"{OUTSIDE}:{source[:40]}:{uuid.uuid4().hex[:12]}"
    with executor.tracing(_trace(p.user_id, day, key)):   # cùng vết với lần người bấm Xác nhận thẻ này
        reason = f"Soạn từ {source_name[:60]}" + (f" — {' '.join(note.split())[:200]}" if note.strip() else "")
        r = await executor.propose(p.as_agent(), command, inp, reason=reason)
    if r.status != "proposed" or not isinstance(r.output, dict):
        return {"status": r.status, "error": r.error}
    o: dict[str, Any] = r.output
    title = str(o["brief"]["title"])[:300]
    async with db.tenant_tx(p) as conn:
        # trần là trần CỨNG: khoá theo (người, ngày) rồi đếm lại trong chính giao dịch ghi — nhiều lượt gọi song song không vượt được
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"outside-proposals:{p.tenant_id}:{p.user_id}:{day}",))
        cur = await conn.execute("SELECT count(*) AS n FROM core.proposals WHERE day = %s AND key LIKE %s", (day, OUTSIDE + ":%"))
        n = await cur.fetchone()
        if n and n["n"] >= OUTSIDE_PER_DAY:
            return {"status": "limit", "error": f"Hôm nay đã soạn {OUTSIDE_PER_DAY} đề xuất từ ứng dụng AI — người dùng xử lý bớt trong Barista rồi soạn tiếp."}
        cur = await conn.execute(
            """INSERT INTO core.proposals (user_id, day, key, command, title, reason, input, basis, rank, preview)
               VALUES (core.me(), %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (day, key, o["command"], title, reason, Jsonb(o["input"]), Jsonb(o.get("basis")) if o.get("basis") else None, 40, Jsonb(o["brief"])))
        row = await cur.fetchone()
    assert row is not None
    return {"status": "proposed", "id": str(row["id"]), "title": title}


async def outside_status(p: Principal, source: str, proposal_id: UUID) -> dict[str, Any] | None:
    """Trạng thái MỘT đề xuất do CHÍNH ứng dụng ``source`` soạn cho ``p`` (RLS: chỉ của người đó; khoá: chỉ của ứng dụng đó — app khác của cùng người
    không đọc được) — để ứng dụng biết người đã bấm chưa rồi làm tiếp."""
    mine = f"{OUTSIDE}:{source[:40]}:".replace("\\", "").replace("%", "").replace("_", "\\_")
    async with db.tenant_tx(p, read_only=True) as conn:
        cur = await conn.execute("SELECT status, title, decided_at, result FROM core.proposals WHERE id = %s AND key LIKE %s", (proposal_id, mine + "%"))
        row = await cur.fetchone()
    return {"status": _proposal_status(row), "title": row["title"], "decided_at": row["decided_at"]} if row else None


async def open_titles(p: Principal, prefix: str) -> list[dict[str, Any]]:
    """Đề xuất đang mở của ``p`` có khoá bắt đầu bằng ``prefix`` (vd một việc theo dõi) — chỉ tên + lệnh, để trang việc liệt kê."""
    async with db.tenant_tx(p, read_only=True) as conn:
        cur = await conn.execute("SELECT title, command FROM core.proposals WHERE key LIKE %s AND status = 'open' ORDER BY created_at",
                                 (prefix.replace("%", "") + ":%",))
        return list(await cur.fetchall())


async def count_today(p: Principal, prefix: str) -> int:
    """Số đề xuất soạn hôm nay cho ``p`` có khoá bắt đầu bằng ``prefix`` (mọi trạng thái) — "hôm nay trợ lý đã soạn N đề xuất"."""
    async with db.tenant_tx(p, read_only=True) as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM core.proposals WHERE day = %s AND key LIKE %s", (today(), prefix.replace("%", "") + ":%"))
        row = await cur.fetchone()
    return int(row["n"]) if row else 0


def _trace(user_id: Any, day: date, key: str) -> str:
    """Vết của MỘT thẻ (0086): suy từ đúng khoá của thẻ (người, ngày, khoá gợi ý) — lúc soạn chưa có mã thẻ, lúc bấm vẫn ra cùng chuỗi ⇒ nhật ký
    nối "trợ lý soạn lúc sáng → người xác nhận"."""
    return f"morning:{user_id}:{day.isoformat()}:{key}"


async def open_count(p: Principal) -> int:
    """Số thẻ đề xuất sáng đang mở hôm nay — CHỈ đọc (bàn việc đếm, không kích hoạt soạn đề xuất bằng model như ``cards``)."""
    if not p.ai:
        return 0
    async with db.tenant_tx(p, read_only=True) as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM core.proposals WHERE day = %s AND status = 'open'", (today(),))
        row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def cards(p: Principal) -> list[dict[str, Any]]:
    """Thẻ đề xuất đang mở hôm nay của ``p`` (cùng hình thẻ đề xuất trong chat + ``reason``). Hôm nay chưa soạn gì cho người này ⇒ soạn ngay."""
    if not p.ai:
        return []
    async with db.tenant_tx(p, read_only=True) as conn:
        day = today()   # trong giao dịch: múi giờ công ty (0169)
        # chỉ đếm thẻ của chính buổi sáng: ứng dụng ngoài soạn thẻ trước 6 giờ không được làm mất lượt soạn buổi sáng (0228)
        cur = await conn.execute("SELECT count(*) AS n FROM core.proposals WHERE day = %s AND key NOT LIKE %s AND key NOT LIKE %s",
                                 (day, OUTSIDE + ":%", AUTO + ":%"))   # thẻ luật tự động (0246) cũng không thay lượt soạn buổi sáng
        row = await cur.fetchone()
    if not row or not row["n"]:
        await prepare(p, day)
    async with db.tenant_tx(p, read_only=True) as conn:
        cur = await conn.execute("""SELECT id, command, title, reason, input, basis, preview, status FROM core.proposals
                                    WHERE day = %s AND status = 'open' ORDER BY rank, created_at""", (day,))
        rows = await cur.fetchall()
    out = []
    for r in rows:
        op = registry.get(r["command"])
        if op is None:
            continue
        out.append(card_of(r, p, op))
    return out


def card_of(r: dict[str, Any], p: Principal, op: registry.Op) -> dict[str, Any]:
    """MỘT hình thẻ đề xuất cho mọi nơi đọc ``core.proposals`` (Việc, danh sách "Chờ tôi duyệt", mở theo mã): ``brief`` = bản chạy thử chuẩn (ADR 0253) —
    dòng cũ chưa có ⇒ dựng tối thiểu từ tiêu đề + lý do; ``state`` đọc từ server (open / done / dismissed / expired)."""
    brief = r["preview"] or {"title": r["title"], "subject": None, "changes": [], "reason": r["reason"], "reversible": previews.reversibility(op)}
    return {"id": str(r["id"]), "kind": "proposal", "source": "morning", "command": r["command"], "title": brief["title"], "subtitle": r["reason"],
            "reason": r["reason"], "brief": brief, "input": r["input"], "fields": field_specs(op, p.vocab, p.fields), "basis": r["basis"],
            **previews.state_of(_proposal_status(r), (r.get("result") or {}).get("approval_id") if isinstance(r.get("result"), dict) else None)}


def _proposal_status(row: dict[str, Any]) -> str:
    if row["status"] == "running":
        return previews.running_state(row.get("decided_at"))
    if row["status"] == "failed" and (row.get("result") or {}).get("status") == previews.RECONCILIATION:
        return previews.RECONCILIATION
    return row["status"]


def _unavailable(row: dict[str, Any] | None) -> executor.Result | None:
    status = _proposal_status(row) if row else None
    if status == "open":
        return None
    if status == "running":
        return executor.Result("conflict", error=RUNNING)
    if status == previews.RECONCILIATION:
        return executor.Result("conflict", error=previews.RECONCILIATION_LABEL)
    return executor.Result("not_found", error=GONE)


async def decide(p: Principal, proposal_id: UUID, decision: str, data: dict[str, Any] | None, key: str) -> dict[str, Any]:
    """Xác nhận ⇒ chạy bằng quyền NGƯỜI bấm (không phải trợ lý), căn cứ đổi so với lúc soạn ⇒ không chạy (0071); Bỏ ⇒ ghi lại, không hỏi lại
    trong ngày (cùng khoá).

    MỖI ĐỀ XUẤT CHỈ CHẠY ĐÚNG MỘT LẦN (ADR 0253) và DÙNG ĐÚNG MỘT KẾT NỐI POOL (rà Codex 21/11):
    - Lệnh thường: khoá dòng ``FOR UPDATE`` + chạy lệnh + đóng thẻ trong CÙNG giao dịch / MỘT kết nối (``executor.run(..., claim=, on_done=)``).
    - Lệnh có ``op.prepare`` gọi hệ ngoài: ``on_prepare`` chuyển thẻ sang ``running`` và commit TRƯỚC khi nhả kết nối gọi mạng (chặn Bỏ và
      Xác nhận thứ hai chen vào lúc đang gọi ngoài); gọi xong thì Pass 2 chốt ``done``, lỗi thì ``on_fail`` chốt ``failed`` (bấm lại không gọi thêm)."""
    if decision != "confirm":
        async with db.tenant_tx(p) as conn:
            cur = await conn.execute("SELECT * FROM core.proposals WHERE id = %s FOR UPDATE", (proposal_id,))
            row = await cur.fetchone()
            if unavailable := _unavailable(row):
                return _result(unavailable.status, unavailable.error)
            assert row is not None
            with executor.tracing(_trace(row["user_id"], row["day"], row["key"])):   # cùng vết với lúc soạn: "trợ lý soạn → người quyết"
                await executor.record_dismissed(p, row["command"], row["input"], conn=conn)   # tín hiệu "bỏ" (0091) — dùng chung kết nối
                await _close(conn, proposal_id, "dismissed", None)
        return _result("dismissed")

    async with db.tenant_tx(p, read_only=True) as conn:
        cur = await conn.execute("SELECT * FROM core.proposals WHERE id = %s", (proposal_id,))
        row = await cur.fetchone()
    if unavailable := _unavailable(row):
        return _result(unavailable.status, unavailable.error)
    assert row is not None
    inp: dict[str, Any] = data if isinstance(data, dict) and data else row["input"]

    async def _claim(conn: Conn, prepared: bool) -> executor.Result | None:
        cur = await conn.execute("SELECT status, decided_at, result FROM core.proposals WHERE id = %s FOR UPDATE", (proposal_id,))
        got = await cur.fetchone()
        if prepared:
            return None if got is not None and got["status"] == "running" else executor.Result("not_found", error=GONE)
        return _unavailable(got)

    async def _on_prepare(conn: Conn) -> None:
        await conn.execute("UPDATE core.proposals SET status = 'running', decided_at = now() WHERE id = %s AND status = 'open'", (proposal_id,))

    async def _on_done(conn: Conn, r: executor.Result) -> None:
        await _close(conn, proposal_id, "done", executor.jsonable(r.output))

    async def _on_fail(conn: Conn, r: executor.Result) -> None:
        await conn.execute("UPDATE core.proposals SET status = 'failed', result = %s, decided_at = now() WHERE id = %s AND status = 'running'",
                           (Jsonb({"status": r.status, "error": r.error or ""}), proposal_id))

    with executor.tracing(_trace(row["user_id"], row["day"], row["key"])):
        r = await executor.run(p, row["command"], inp, key=f"proposal:{proposal_id}", expect_basis=row["basis"],
                               edited=inp != row["input"], claim=_claim, on_prepare=_on_prepare, on_done=_on_done, on_fail=_on_fail)
    return _result(r.status, r.error or "", executor.jsonable(r.output), r.approval_id)


GONE = "Đề xuất này đã được xử lý (hoặc hết hạn, hoặc không phải của bạn)"
RUNNING = "Đề xuất này đang được thực hiện — không thể bỏ hoặc chạy lại"


def _result(status: str, error: str = "", output: Any = None, approval_id: str | None = None) -> dict[str, Any]:
    """Cùng hình kết quả lệnh (``/v1/commands``) — app đọc một kiểu."""
    return {"status": status, "error": error, "output": output, "approval_id": approval_id, "replayed": False}


async def _close(conn: Conn, proposal_id: UUID, status: str, result: Any) -> None:
    await conn.execute("UPDATE core.proposals SET status = %s, result = %s, decided_at = now() WHERE id = %s AND status IN ('open', 'running')",
                       (status, Jsonb(result) if result is not None else None, proposal_id))


async def prepare_tenant(tenant_id: UUID, day: date) -> tuple[int, int]:
    """Soạn cho mọi người đang hoạt động của một công ty; ghi lượt chạy (một lượt / công ty / ngày)."""
    async with db.owner.connection() as conn:
        cur = await conn.execute("SELECT user_id FROM core.memberships WHERE tenant_id = %s AND active", (tenant_id,))
        users = [r["user_id"] for r in await cur.fetchall()]
    people = cards_n = 0
    for uid in users:
        async with db.owner.connection() as conn:
            p = await auth.load(conn, tenant_id, user_id=uid)
        if p is None:
            continue
        try:
            n = await prepare(p, day)
        except Exception:   # một người lỗi không làm hỏng buổi sáng của người khác
            log.exception("soạn đề xuất buổi sáng lỗi (%s)", uid)
            continue
        people += 1
        cards_n += n
    async with db.owner.connection() as conn:
        await conn.execute("""INSERT INTO core.proposal_runs (tenant_id, day, people, cards) VALUES (%s, %s, %s, %s)
                              ON CONFLICT (tenant_id, day) DO NOTHING""", (tenant_id, day, people, cards_n))
    return people, cards_n


async def run_once(now: datetime | None = None) -> int:
    """Mỗi công ty một lượt / ngày khi đã qua ``START_HOUR`` giờ CỦA CÔNG TY ĐÓ (0169): cùng một lúc, công ty ở Hà Nội đã sáng, công ty ở London chưa."""
    now = now or datetime.now(UTC)
    async with db.owner.connection() as conn:
        cur = await conn.execute("SELECT id, timezone FROM core.tenants ORDER BY created_at")
        due = [(r["id"], d) for r in await cur.fetchall()
               if (local := now.astimezone(clock.of(r["timezone"]))).hour >= START_HOUR and (d := local.date())]
        cur = await conn.execute("SELECT tenant_id, day FROM core.proposal_runs WHERE tenant_id = ANY(%s) AND day = ANY(%s)",
                                 ([t for t, _ in due], list({d for _, d in due})))
        done = {(r["tenant_id"], r["day"]) for r in await cur.fetchall()}
    tenants = [(t, d) for t, d in due if (t, d) not in done]
    for t, d in tenants:
        await prepare_tenant(t, d)
    return len(tenants)


async def run_forever(interval_s: float) -> None:
    while True:
        try:
            await run_once()
        except Exception:   # vòng nền không được chết vì một lượt lỗi — log.exception rồi chạy lượt sau
            log.exception("vòng đề xuất buổi sáng lỗi")
        await asyncio.sleep(interval_s)
