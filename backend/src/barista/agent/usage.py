"""Đo token của trợ lý (quyết định 0049): mỗi lượt chạy model ghi một dòng ``core.ai_usage``.

Loại việc = phân hệ của công cụ được gọi nhiều nhất trong lượt (khách hàng, giao dịch, hoa hồng…); không gọi công cụ nào ⇒ "chat"
(trò chuyện thuần); việc theo dõi ⇒ "work:<loại việc>". Chi phí ước tính theo bảng giá công khai của thư viện — model chưa có giá ⇒
NULL (không phải 0). Ghi hỏng thì chỉ ghi log: đo lường không được làm hỏng câu trả lời — dòng chưa ghi được xếp hàng thử lại (ADR 0200, review F10).
"""

import asyncio
import logging
import time
from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from pydantic_ai import ModelMessage, ModelResponse
from pydantic_ai.usage import RunUsage

from barista.agent import reliability
from barista.config import settings
from barista.db import after_release, db
from barista.kernel import alerts, notify, registry
from barista.kernel.clock import today
from barista.kernel.principal import Principal

log = logging.getLogger("barista.agent.usage")

SURFACES = {"chat": "Trò chuyện", "voice": "Giọng nói", "resume": "Chạy tiếp sau duyệt", "work": "Việc theo dõi", "kb": "Kho tài liệu", "docread": "Đọc tài liệu"}


def category_of(steps: Iterable[dict[str, Any]]) -> tuple[str, list[str]]:
    """(loại việc, các phân hệ đã chạm) từ các bước của lượt."""
    mods = Counter(str(s.get("tool", "")).split(".")[0] for s in steps if s.get("tool"))
    if not mods:
        return "chat", []
    return mods.most_common(1)[0][0], sorted(mods)


def label(category: str) -> str:
    if category == "chat":
        return "Trò chuyện (không dùng công cụ)"
    if category in ("voice:live", "voice:live_estimate"):   # lớp giọng nói Gemini Live (0193) — ước tính theo phút khi Live không báo số token
        return "Giọng nói (Gemini Live)" + (" · ước tính theo phút" if category.endswith("estimate") else "")
    if category.startswith("docread:"):   # đọc tài liệu / OCR theo mục đích (0232)
        from barista.docread.policy import PURPOSES

        return "Đọc tài liệu · " + PURPOSES.get(category[8:], category[8:])
    if category in ("kb:embed", "kb:embed_query"):   # nhúng tìm theo nghĩa (0141, 0232) — theo model ở báo cáo token
        return "Kho tài liệu · nhúng " + ("câu tìm" if category.endswith("query") else "đoạn tài liệu")
    if category.startswith("work:"):
        return "Việc theo dõi · " + {"fee_collection": "thu phí CĐT", "installment": "nhắc khách đóng tiền"}.get(category[5:], category[5:])
    return registry.MODULES.get(category, category)


def quota_tokens(u: RunUsage) -> int:
    """Token TÍNH HẠN MỨC theo người (ADR 0193): mọi token trừ token ĐỌC cache (rẻ ~50 lần — đếm vào thì hạn mức tiêu vào phần gần như không tốn tiền).
    Quy ước pydantic-ai: ``input_tokens`` ĐÃ gồm cả ``cache_read_tokens`` lẫn ``cache_write_tokens`` (và token âm thanh) ⇒ (vào − cache đọc) đã chứa cache
    ghi; cộng thêm cache ghi là đếm hai lần. = (vào − cache đọc) + ra."""
    return max(0, u.input_tokens - u.cache_read_tokens) + u.output_tokens


def cost_usd(messages: Iterable[ModelMessage]) -> Decimal | None:
    """Tổng chi phí ước tính các lần gọi model; một lần không tra được giá ⇒ cả lượt NULL (đừng báo thiếu mà tưởng đủ)."""
    total = Decimal(0)
    seen = False
    for m in messages:
        if not isinstance(m, ModelResponse):
            continue
        seen = True
        try:
            total += Decimal(str(m.cost().total_price))
        except Exception:   # model / nhà cung cấp chưa có trong bảng giá
            return None
    return total if seen else None


async def record(p: Principal, *, surface: str, category: str, modules: list[str], usage: RunUsage, model: str,
                 messages: Iterable[ModelMessage] = (), ok: bool = True, steps: Iterable[dict[str, Any]] = (),
                 started: float | None = None, priced: Decimal | None = None, error: BaseException | None = None,
                 skills: Iterable[str] = ()) -> None:
    """``priced``: chi phí bên gọi đã tự tính (lượt không có ``ModelResponse`` — embedding, 0141); trống ⇒ tính từ ``messages``.
    ``steps``: các bước công cụ của lượt (ToolRun.steps) — ghi tên công cụ đã gọi + công cụ lỗi (157) để thống kê theo công cụ.
    ``started``: ``time.monotonic()`` lúc bắt đầu lượt ⇒ ghi thời gian lượt (185); không truyền = nơi chạy chưa đo (NULL, khác 0).
    ``error``: ngoại lệ làm hỏng lượt ⇒ ghi LOẠI lỗi nhà cung cấp (``reliability.classify``, migration 070); không phải lỗi gọi model ⇒ NULL.
    ``model``: tên model bên gọi dựng; lượt có câu trả lời ⇒ ghi tên model ĐÃ TRẢ LỜI (chuỗi dự phòng — runbook 15).
    ``skills``: kỹ năng trợ lý đã mở trong lượt (``khoá@phiên bản`` — 0198); không mở ⇒ NULL."""
    messages = list(messages)
    model = next((m.model_name for m in reversed(messages) if isinstance(m, ModelResponse) and m.model_name), None) or model
    steps = list(steps)
    row = _Row(p, surface, category, (p.user_id, p.home_team, surface, category, modules, model, usage.requests, usage.tool_calls, usage.input_tokens,
                                      usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens,
                                      priced if priced is not None else cost_usd(messages), ok, [s["tool"] for s in steps],
                                      [s["tool"] for s in steps if s.get("status") == "error"],
                                      round((time.monotonic() - started) * 1000) if started is not None else None,
                                      reliability.classify(error) if error is not None else None, list(skills) or None),
               quota_tokens(usage))
    # Bên gọi đang GIỮ một kết nối (lệnh / truy vấn gọi model trong handler — F10): không mượn kết nối thứ hai từ pool có thể đã kín; ghi ngay sau
    # khi bên gọi trả kết nối (giao dịch commit hay hoàn đều ghi — token đã tốn). Không giữ ⇒ ghi bây giờ, chờ pool có giới hạn.
    if not after_release(lambda: _write_or_queue(row)):
        await _write_or_queue(row)


@dataclass
class _Row:
    p: Principal
    surface: str
    category: str
    values: tuple[Any, ...]
    tokens: int
    tries: int = 0

    @property
    def cost(self) -> Decimal | None:
        return self.values[12]


# Lượt dùng AI chưa ghi được (pool kín quá ``ai_usage_wait_s``, DB chập) — giữ trong bộ nhớ, thử lại nền tới khi được; ``flush`` lúc tắt máy chủ.
# Mất khi tiến trình chết đột ngột (đã ghi log từng dòng chờ). Trần để không phình bộ nhớ khi DB hỏng lâu.
_backlog: deque[_Row] = deque()
BACKLOG_MAX = 5000
MAX_TRIES = 30
_flusher: asyncio.Task[None] | None = None


def pending() -> int:
    """Số lượt dùng AI đang chờ ghi lại (đo / test)."""
    return len(_backlog)


# LỖI VĨNH VIỄN (19/11/2026): DB từ chối CHÍNH dòng này — vi phạm RLS / khoá ngoại / ràng buộc, dữ liệu sai kiểu. Ghi lại bao nhiêu lần cũng vậy ⇒ bỏ ngay
# (log lỗi), không xếp hàng: trước đây dòng hỏng nằm ĐẦU hàng chờ 30 lượt thử (lùi tới 30 s) và chặn dòng của mọi công ty khác phía sau. Lỗi tạm thời
# (hết kết nối pool, mất kết nối, DB khởi động lại) vẫn xếp hàng thử lại.
PERMANENT = (psycopg.errors.IntegrityError, psycopg.errors.ProgrammingError, psycopg.errors.DataError)
WRITTEN, RETRY, DROPPED = "written", "retry", "dropped"


async def _write_or_queue(row: _Row) -> None:
    try:
        outcome = await _write(row, wait=settings.ai_usage_wait_s)
    except BaseException:   # bị huỷ giữa chừng (tắt máy chủ, người bấm Dừng) ⇒ giữ để ghi sau, rồi để việc huỷ đi tiếp
        _queue(row)
        raise
    if outcome == RETRY:
        _queue(row)


def _queue(row: _Row) -> None:
    global _flusher
    if len(_backlog) >= BACKLOG_MAX:
        lost = _backlog.popleft()
        log.error("hàng chờ lượt dùng AI đầy — bỏ lượt cũ nhất %s/%s của công ty %s", lost.surface, lost.category, lost.p.tenant_id)
    _backlog.append(row)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _flusher is None or _flusher.done():
        _flusher = loop.create_task(_retry_forever())


async def _retry_forever() -> None:
    delay = 1.0
    while _backlog:
        await asyncio.sleep(delay)
        if await flush():
            return
        delay = min(delay * 2, 30.0)


_flush_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None   # khoá gắn vòng lặp sự kiện đang chạy (test đổi vòng lặp giữa các ca)


async def flush(*, wait: float | None = None) -> bool:
    """Ghi các lượt đang chờ theo thứ tự; True = đã hết hàng chờ. Gọi lúc tắt máy chủ (trước khi đóng pool) và từ vòng thử lại nền.
    MỘT lượt xả một lúc (19/11/2026): vòng nền và lượt tắt máy chủ cùng xả thì cả hai ghi chung dòng đầu (ghi TRÙNG token) rồi cùng ``popleft`` (lấy mất
    dòng chưa ghi / lỗi hàng rỗng)."""
    global _flush_lock
    loop = asyncio.get_running_loop()
    if _flush_lock is None or _flush_lock[0] is not loop:
        _flush_lock = (loop, asyncio.Lock())
    async with _flush_lock[1]:
        return await _flush(wait)


async def _flush(wait: float | None) -> bool:
    """Dòng đang ghi RỜI hàng chờ trước khi chờ ghi (rà 21/11/2026): ``_queue`` của lượt khác thấy hàng đầy bỏ dòng cũ nhất ĐANG CHỜ — không bao giờ
    bỏ đúng dòng đang ghi, rồi ``popleft`` sau khi ghi lại lấy mất một dòng chưa ghi. Ghi hỏng (thử lại) ⇒ dòng về đầu hàng; bị huỷ giữa chừng ⇒ cũng về."""
    while _backlog:
        row = _backlog.popleft()
        try:
            outcome = await _write(row, wait=wait)
        except BaseException:
            _backlog.appendleft(row)
            raise
        if outcome != RETRY:   # đã ghi, hoặc lỗi vĩnh viễn đã log
            continue
        row.tries += 1
        if row.tries >= MAX_TRIES:
            log.error("bỏ lượt dùng AI %s/%s của công ty %s sau %d lần ghi hỏng", row.surface, row.category, row.p.tenant_id, row.tries)
            continue
        _backlog.appendleft(row)
        return False
    return True


async def _write(row: _Row, *, wait: float | None) -> str:
    """``WRITTEN`` · ``RETRY`` (lỗi tạm thời — xếp hàng) · ``DROPPED`` (DB từ chối chính dòng này — log lỗi, không thử lại)."""
    p, out = row.p, ""
    try:
        async with db.tenant_tx(p, wait=wait) as conn:
            await conn.execute(
                """INSERT INTO core.ai_usage (user_id, team_id, surface, category, modules, model, requests, tool_calls, input_tokens,
                                              output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, ok, tools, tool_errors,
                                              duration_ms, error_class, skills)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", row.values)
            if row.cost:
                await _spend(conn, row.cost, p.tenant_id)
            if p.user_id is not None and not p.system and (row.tokens or row.cost):
                out = await _person_spend(conn, p, row.cost or Decimal(0), row.tokens)
    except PERMANENT:
        log.error("bỏ lượt dùng AI %s/%s của công ty %s: DB từ chối dòng (lỗi không tự hết)", row.surface, row.category, p.tenant_id, exc_info=True)
        return DROPPED
    except Exception:   # ghi số đo token không được làm hỏng lượt trả lời của người dùng — chỉ log, bên gọi xếp hàng thử lại
        log.warning("chưa ghi được token của lượt %s/%s — xếp hàng ghi lại", row.surface, row.category, exc_info=True)
        return RETRY
    if out:   # sau khi giao dịch ghi xong: báo kênh của nhà vận hành (không phải khách) — một lần / người / tháng (stopped_at)
        alerts.notice(p.tenant_id, f"ai_person_out:{p.tenant_id}:{p.user_id}:{today():%Y-%m}", out)
    return WRITTEN


async def _spend(conn: Any, cost: Decimal, tenant_id: UUID) -> None:
    """Chi AI của CÔNG TY trong tháng (giờ công ty) — cộng dồn cùng giao dịch ghi lượt (0092). Chạm ngưỡng cảnh báo / hết hạn mức ⇒ báo người quản trị hệ
    thống MỘT lần mỗi tháng (lượt đang chạy vẫn xong; lượt sau ``llm.model_for`` từ chối)."""
    cur = await conn.execute("""INSERT INTO core.ai_spend (month, usd) VALUES (%s, %s)
                                ON CONFLICT (tenant_id, month) DO UPDATE SET usd = core.ai_spend.usd + EXCLUDED.usd
                                RETURNING usd, warned_at, stopped_at""", (today().replace(day=1), cost))
    s = await cur.fetchone()
    cur = await conn.execute("SELECT ai_monthly_usd AS cap, ai_warn_pct AS pct FROM core.tenant_profile WHERE tenant_id = core.tenant()")
    t = await cur.fetchone()
    if s is None or t is None or t["cap"] is None:
        return
    stop = s["usd"] >= t["cap"] and s["stopped_at"] is None
    warn = not stop and s["usd"] >= t["cap"] * t["pct"] / 100 and s["warned_at"] is None
    if not (stop or warn):
        return
    await conn.execute(f"UPDATE core.ai_spend SET {'stopped_at' if stop else 'warned_at'} = now() WHERE month = %s", (today().replace(day=1),))
    cur = await conn.execute("""SELECT m.user_id FROM core.memberships m JOIN core.grants g ON g.tenant_id = m.tenant_id AND g.role = m.role
                                WHERE m.active AND g.resource = 'core.admin' AND 'manage' = ANY(g.actions)""")
    admins = [r["user_id"] for r in await cur.fetchall()]
    pct = min(100, int(s["usd"] * 100 / t["cap"]))
    title = "Trợ lý AI tạm dừng: hết hạn mức tháng" if stop else f"Trợ lý AI đã dùng {t['pct']}% hạn mức tháng"
    # khách thấy phần trăm, không số tiền (0193 — chi phí là của nhà vận hành); nhà vận hành nhận số USD qua kênh của mình
    body = f"Đã dùng {pct}% hạn mức trợ lý AI tháng này." + (" Các thao tác không cần trợ lý vẫn dùng bình thường." if stop else "")
    await notify.send(conn, admins, kind="ai_budget", title=title, body=body, open={"query": "core.ai_usage", "input": {}}, actor=None,
                      key=f"ai_budget:{today():%Y-%m}:{'stop' if stop else 'warn'}")
    alerts.notice(tenant_id, f"ai_company:{today():%Y-%m}:{'stop' if stop else 'warn'}",
                  f"Trần AI cả công ty: {title} — {s['usd']:.2f} / {t['cap']:.2f} USD")


async def _person_spend(conn: Any, p: Principal, cost: Decimal, tokens: int) -> str:
    """TOKEN (và chi phí nội bộ) của CHÍNH NGƯỜI NÀY trong tháng (ADR 0193) — cộng cùng giao dịch ghi lượt, cạnh chi của công ty. Hạn mức token lấy từ
    hạng hiện tại của vai (đọc lại mỗi lần — nhà vận hành đổi có hiệu lực ngay lượt sau). Chạm hạn mức ⇒ đánh dấu ``stopped_at`` MỘT lần / người / tháng
    và trả lời báo cho NHÀ VẬN HÀNH (bên gọi gửi sau khi ghi xong) + thông báo cho người giữ ``core.ai_policy`` nếu có; người dùng không nhận thông báo —
    lượt sau họ thấy câu báo trong khung chat (``auth.person_block`` → ``llm.model_for``). Lượt đang chạy vẫn xong."""
    month = today().replace(day=1)
    cur = await conn.execute("""INSERT INTO core.ai_person_spend (user_id, month, usd, tokens) VALUES (%s, %s, %s, %s)
                                ON CONFLICT (tenant_id, user_id, month) DO UPDATE SET usd = core.ai_person_spend.usd + EXCLUDED.usd,
                                    tokens = core.ai_person_spend.tokens + EXCLUDED.tokens
                                RETURNING tokens, stopped_at""", (p.user_id, month, cost, tokens))
    s = await cur.fetchone()
    cur = await conn.execute("""SELECT t.label, t.person_monthly_tokens AS cap FROM core.ai_tiers t
                                LEFT JOIN core.ai_role_tiers r ON r.tenant_id = t.tenant_id AND r.role = %s
                                WHERE t.tenant_id = core.tenant() AND (t.key = r.tier OR (r.tier IS NULL AND t.is_default))""", (p.role,))
    t = await cur.fetchone()
    if s is None or t is None or t["cap"] is None or s["tokens"] < t["cap"] or s["stopped_at"] is not None:
        return ""
    cur = await conn.execute("UPDATE core.ai_person_spend SET stopped_at = now() WHERE user_id = %s AND month = %s AND stopped_at IS NULL RETURNING 1",
                             (p.user_id, month))
    if await cur.fetchone() is None:   # lượt song song đã đánh dấu (và báo) trước
        return ""
    # kênh của nhà vận hành chỉ mang MÃ người (như báo lỗi — alerts không chở dữ liệu cá nhân); tên / email xem ở báo cáo token
    text = (f"Hết hạn mức token trợ lý AI tháng {today():%m/%Y}: người {p.user_id} (hạng “{t['label']}”) "
            f"{int(s['tokens']):,} / {int(t['cap']):,} token — trợ lý dừng với người này tới ngày 1 tháng sau.").replace(",", ".")
    cur = await conn.execute("""SELECT DISTINCT m.user_id FROM core.memberships m JOIN core.grants g ON g.tenant_id = m.tenant_id AND g.role = m.role
                                WHERE m.active AND g.resource = 'core.ai_policy'""")
    if ops := [r["user_id"] for r in await cur.fetchall()]:
        await notify.send(conn, ops, kind="ai_budget", title="Một người đã hết hạn mức trợ lý AI", body=text,
                          open={"query": "core.ai_token_report", "input": {}}, actor=None, key=f"ai_person_out:{today():%Y-%m}:{p.user_id}")
    return text
