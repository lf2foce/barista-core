"""BÀN VIỆC (quyết định 0094) — MỘT điểm vào "tôi phải làm gì": ba nhóm cần tôi quyết · cần tôi làm · đang theo dõi.

Ráp từ màn module tự khai ``desk=`` (``kernel/screens.py``) + phiếu duyệt của lõi; mỗi mục = một màn đúng quyền người xem (mở ra đủ nút như
cũ), kèm câu tín hiệu do CODE tính từ chính truy vấn của màn (chạy bằng quyền người xem ⇒ số khớp 100% với màn mở ra). Model không tham gia.
Lời chào "em thấy có việc này" của trợ lý (``attention``) là góc nhìn gọn của cùng các câu đó — không có khai báo thứ hai.
File này không biết module / gói nào.
"""

import asyncio
import json
import logging
from typing import Any

from barista import workspaces
from barista.agent import morning
from barista.kernel import executor, registry
from barista.kernel.principal import Principal
from barista.kernel.screens import DESK_GROUPS, Desk, Line, Screen

log = logging.getLogger("barista.desk")

DESK_DEADLINE_S = 12.0   # cả bàn: quá hạn ⇒ mục còn lại "chưa kiểm được", bàn vẫn trả (app không tự hết giờ trước server)
SOURCE_TIMEOUT_S = 3.0   # một nguồn chậm không làm treo cả bàn: quá hạn ⇒ "chưa kiểm được"
SLOW_TIMEOUT_S = 10.0    # nguồn khai ``slow`` (quét nhiều công ty) được chờ lâu hơn — KHÔNG nhớ kết quả: quyền ở từng công ty được đọc có thể vừa đổi
PARALLEL = 4             # truy vấn nguồn chạy cùng lúc trong CẢ tiến trình (pool DB 10 kết nối dùng chung với mọi request khác)
TONES = {"bad": 0, "warn": 1}
_gates: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}


def _gate() -> asyncio.Semaphore:
    """Cổng chung của tiến trình (theo vòng lặp sự kiện — test mỗi lượt một vòng lặp). Thời hạn tính từ lúc QUA cổng: chờ lượt không bị coi là
    nguồn chậm (nhiều người cùng mở bàn ⇒ chậm hơn, không ra "chưa kiểm được" giả)."""
    loop = asyncio.get_running_loop()
    if (g := _gates.get(loop)) is None:
        g = _gates[loop] = asyncio.Semaphore(PARALLEL)
    return g


# Lượt đọc nguồn ĐANG CHẠY, dùng chung cho người hỏi CÙNG LÚC (mở app gọi bàn việc + lời chào + gợi ý câu hỏi — ba lối cùng chạy mọi nguồn): khoá
# = vòng lặp + nguyên người gọi (mọi trường quyền) + truy vấn + đầu vào + số lệnh đã commit. Xong là bỏ — KHÔNG nhớ kết quả qua lượt sau; có lệnh
# commit sau lúc lượt bắt đầu ⇒ người hỏi sau đó chạy lượt mới (số trên bàn việc sau khi bấm luôn tính cả lệnh vừa chạy).
_flights: dict[tuple[Any, ...], tuple[asyncio.Task[executor.Result], asyncio.Event]] = {}


async def _source(p: Principal, name: str, inp: dict[str, Any], passed: asyncio.Event) -> executor.Result:
    async with _gate():
        passed.set()
        return await executor.query(p, name, inp)


def _flight(p: Principal, name: str, inp: dict[str, Any]) -> tuple[asyncio.Task[executor.Result], asyncio.Event]:
    loop = asyncio.get_running_loop()
    key = (loop, repr(p), name, json.dumps(inp, sort_keys=True, default=str), executor.writes(p.tenant_id))
    if (got := _flights.get(key)) is not None:
        return got
    passed = asyncio.Event()
    task = loop.create_task(_source(p, name, inp, passed))
    _flights[key] = (task, passed)

    def done(t: asyncio.Task[executor.Result]) -> None:
        if _flights.get(key, (None,))[0] is t:
            del _flights[key]
        if not t.cancelled():
            t.exception()   # đánh dấu đã đọc lỗi (không cảnh báo "never retrieved") — người chờ đã log riêng
    task.add_done_callback(done)
    return task, passed


async def _output(p: Principal, name: str, inp: dict[str, Any], slow: bool, deadline: float) -> Any:
    """Kết quả truy vấn bằng quyền ``p`` — None nếu lỗi / quá hạn nguồn / hết hạn cả bàn / lỗi bất ngờ (mục đó "chưa kiểm được", bàn vẫn ra).
    Quá hạn chỉ thôi CHỜ, không huỷ truy vấn đang chạy dở (``shield``): huỷ giữa lúc psycopg đang gửi / nhận làm hỏng kết nối trả về pool (lỗi
    "another command is already in progress" ở request kế — đo trên bản cài 18/11/2026); lượt chưa qua cổng thì huỷ được (chưa chạm DB)."""
    task, passed = _flight(p, name, inp)
    try:
        async with asyncio.timeout_at(deadline):
            await passed.wait()
            r = await asyncio.wait_for(asyncio.shield(task), SLOW_TIMEOUT_S if slow else SOURCE_TIMEOUT_S)
    except TimeoutError:
        if not passed.is_set():
            task.cancel()   # còn chờ cổng — chưa mượn kết nối nào
        log.warning("nguồn bàn việc %s quá hạn", name)
        return None
    except Exception:   # một nguồn hỏng không làm mất cả bàn việc — đã log.exception
        log.exception("nguồn bàn việc %s lỗi", name)
        return None
    return r.output if r.status == "ok" else None


def _lines(s: Screen, d: Desk, out: Any) -> list[Line] | None:
    """Câu tín hiệu của màn — None nếu hàm ``say`` lỗi (bàn việc báo "chưa kiểm được", không im lặng như hết việc)."""
    if d.say is None:
        return []
    try:
        said = d.say(out)
    except Exception:   # câu tín hiệu hỏng ⇒ bỏ riêng tín hiệu đó — đã log.exception
        log.exception("câu tín hiệu của màn %s lỗi", s.key)
        return None
    return [{**x, "query": s.query} for x in (said if isinstance(said, list) else [said]) if x]


async def _section(p: Principal, ws: str, s: Screen, deadline: float) -> tuple[Desk, dict[str, Any]] | None:
    try:
        return await _section_or_raise(p, ws, s, deadline)
    except Exception:   # một mục hỏng không làm mất cả bàn việc
        log.exception("mục bàn việc %s lỗi", s.key)
        return None


async def _section_or_raise(p: Principal, ws: str, s: Screen, deadline: float) -> tuple[Desk, dict[str, Any]] | None:
    d = s.desk
    assert d is not None
    spec = workspaces.screen_spec(p, s)
    if spec is None:
        return None
    op = registry.OPS[s.query]
    quiet = d.team_only and op.resource is not None and p.has_scope(op.resource, "all")   # có tín hiệu toàn công ty rồi — mục vẫn mở được
    # việc của người xử lý (``Desk.doers_only``): người chỉ xem nhận mục ở "Đang theo dõi", câu không màu, không đếm, không vào lời chào trợ lý
    watching = d.doers_only and op.resource is not None and not p.can(op.resource, "write")
    lines: list[Line] | None = []
    if d.say is not None and not quiet:
        out = await _output(p, s.query, d.input, d.slow, deadline)
        lines = _lines(s, d, out) if out is not None else None
    if watching and lines:
        lines = [{**x, "tone": "accent", "ask": None} for x in lines]
    return d, {"workspace": ws, "screen": spec, "lines": lines or [], "unchecked": lines is None,
               **({"oversight": True} if watching else {}), "_group": "watch" if watching else d.group,
               "_rank": (min((TONES.get(x["tone"], 2) for x in lines or []), default=3), d.rank)}


async def desk(p: Principal) -> dict[str, Any]:
    """``{groups: [{key, title, sections: [{workspace, screen, lines, unchecked}]}], morning, count}`` — mục nào người không mở được thì không có; truy vấn lỗi /
    quá hạn hoặc câu tín hiệu lỗi ⇒ ``unchecked`` (KHÔNG coi như hết việc); mục hỏng bất ngờ thì bỏ, bàn vẫn ra. Nguồn chạy song song qua cổng
    chung của tiến trình. Trong nhóm: mục có tín hiệu đỏ →
    vàng → còn lại, rồi thứ tự module khai."""
    deadline = asyncio.get_running_loop().time() + DESK_DEADLINE_S
    found, waiting = await asyncio.gather(asyncio.gather(*(_section(p, ws, s, deadline) for ws, s in workspaces.desk_screens())),
                                          _morning(p, deadline))
    groups: dict[str, list[dict[str, Any]]] = {g: [] for g in DESK_GROUPS}
    for got in found:
        if got is not None:
            groups[got[1].pop("_group")].append(got[1])
    for secs in groups.values():
        secs.sort(key=lambda x: x.pop("_rank"))
    hot = sum(1 for secs in groups.values() for sec in secs if any(x["tone"] in TONES for x in sec["lines"]))
    unchecked = sum(1 for secs in groups.values() for sec in secs if sec["unchecked"]) + (waiting is None)
    # ``count``: số MỤC cần để ý trên nút "Việc" (mục có tín hiệu đỏ / vàng + thẻ đề xuất sáng đang mở — không phải số việc); ``unchecked``: số mục
    # chưa kiểm được (app hiện dấu trung tính, không để 0 trông như hết việc). App vẽ, không tự đếm chữ.
    return {"groups": [{"key": g, "title": DESK_GROUPS[g], "sections": secs} for g, secs in groups.items() if secs],
            "morning": waiting or 0, "morning_unchecked": waiting is None, "count": hot + (waiting or 0), "unchecked": unchecked}


async def _morning(p: Principal, deadline: float) -> int | None:
    """Thẻ đề xuất sáng đang mở — thuộc nhóm "cần tôi quyết" (app vẽ thẻ từ ``/v1/assistant/morning``). Cùng hạn chót của bàn; lỗi / quá hạn ⇒
    None ("chưa đếm được" — khác với "không có đề xuất")."""
    try:
        async with asyncio.timeout_at(deadline):
            return await morning.open_count(p)
    except Exception:   # con số phụ trên bàn việc: lỗi / quá hạn ⇒ bỏ số đó, bàn việc vẫn mở — đã log.exception
        log.exception("đếm đề xuất sáng lỗi / quá hạn")
        return None


async def attention(p: Principal) -> list[Line]:
    """Lời chào của trợ lý: tối đa 5 câu tín hiệu đáng nói nhất trên bàn việc (đỏ → vàng → còn lại) — cùng nguồn với bàn việc."""
    page = await desk(p)
    lines = [x for g in page["groups"] for sec in g["sections"] if not sec.get("oversight") for x in sec["lines"]]   # việc người khác xử lý: không chào
    return sorted(lines, key=lambda x: TONES.get(x["tone"], 2))[:5]
