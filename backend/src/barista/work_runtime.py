"""Cầu nối DBOS ↔ việc theo dõi (quyết định 0021).

Mỗi việc một workflow bền, ID ``work:<id việc>``: chạy ``work.step`` (một step DBOS — đã xong thì khởi động lại không chạy
lại) → ngủ bằng ``recv`` tới lần kiểm sau HOẶC tới khi có tin "wake" (phiếu thu mới, người bấm "đã xử lý") → chạy lại, tới
khi việc xong / dừng. Tiến trình chết ⇒ ``DBOS.launch()`` lần sau chạy tiếp từ chỗ dở.

DBOS chỉ giữ NHỊP (ngủ / thức / chạy lại). Trạng thái việc nằm ở ``work.items`` — xoá sạch dữ liệu DBOS thì vòng đối soát
dựng lại workflow từ bảng. Hệ thống DBOS dùng schema ``dbos`` trong cùng database (một bản sao lưu cho cả hai).

PHIÊN BẢN (quyết định 0026): DBOS chỉ chạy tiếp workflow cùng "application version"; mặc định version = băm mã nguồn
workflow ⇒ sửa một dòng trong ``follow`` là mọi việc đang ngủ thành mồ côi sau deploy. Nên version CỐ ĐỊNH (``WORKFLOW_VERSION``)
và nằm trong ID workflow. Sửa ``work.step`` (nội dung một bước) KHÔNG cần đổi. Đổi THỨ TỰ / SỐ bước, ``recv`` trong
``follow`` ⇒ tăng ``WORKFLOW_VERSION``: bản cũ không được chạy tiếp (đúng — lịch sử cũ không khớp mã mới), vòng đối soát thấy
việc mở chưa có workflow ID mới ⇒ khởi động lại từ bảng ``work.items``. Không mất việc vì trạng thái không nằm trong DBOS.
"""

import asyncio
import contextvars
import logging
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID

from dbos import DBOS, SetWorkflowID

from barista.config import settings
from barista.modules import work

log = logging.getLogger("barista.work")

MAX_SLEEP_S = 6 * 3600   # ngủ tối đa một lèo — dù hẹn 1 ngày vẫn tự dậy kiểm 4 lần/ngày (sửa tay dữ liệu, lệch giờ…)
ERROR_SLEEP_S = 300      # bước lỗi (DB chập chờn…) ⇒ ngủ ngần này rồi thử lại, không bỏ việc
WORKFLOW_VERSION = "1"  # xem đầu file: chỉ tăng khi đổi thứ tự / số bước của ``follow``
_running = False


def workflow_id(item_id: UUID | str) -> str:
    return f"work:v{WORKFLOW_VERSION}:{item_id}"


@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=2.0)
async def run_step(tenant_id: str, item_id: str) -> dict[str, Any]:
    return await work.step(UUID(tenant_id), UUID(item_id))


# Mỗi lần khởi động lại tiến trình (deploy) tính một lần "khôi phục"; việc sống vài tháng qua hàng trăm lần deploy ⇒ trần cao.
@DBOS.workflow(max_recovery_attempts=10_000)
async def follow(tenant_id: str, item_id: str) -> str:
    while True:
        try:
            r = await run_step(tenant_id, item_id)
        except Exception as e:   # bước lỗi sau khi thử lại ⇒ log, trả trạng thái error để nghỉ rồi chạy lại (việc không mất)
            log.warning("việc %s: bước lỗi sau khi thử lại — %r", item_id, e)
            r = {"final": False, "status": "error", "sleep_s": ERROR_SLEEP_S}
        if r["final"]:
            return str(r["status"])
        await DBOS.recv_async("wake", timeout_seconds=max(1, min(int(r["sleep_s"]), MAX_SLEEP_S)))


async def _outside_dbos[T](coro: Coroutine[Any, Any, T]) -> T:
    """Chạy trong ngữ cảnh SẠCH. Tín hiệu sau commit có thể phát từ một lệnh chạy BÊN TRONG một DBOS step (trợ lý xử lý ngoại
    lệ gọi công cụ ghi) — kế thừa ngữ cảnh step thì ``start_workflow`` lỗi AssertionError (DBOS cấm mở workflow từ step;
    phenau_v3 đã dính 21/10/2026: mất thông báo mỗi giờ mà test vẫn xanh)."""
    return await asyncio.get_running_loop().create_task(coro, context=contextvars.Context())


async def start(tenant_id: UUID, item_id: UUID) -> None:
    """Bắt đầu theo dõi một việc. Cùng ID ⇒ đã có workflow thì không tạo thêm (gọi bao nhiêu lần cũng được)."""
    if not _running:
        return

    async def go() -> None:
        with SetWorkflowID(workflow_id(item_id)):
            await DBOS.start_workflow_async(follow, str(tenant_id), str(item_id))

    await _outside_dbos(go())


async def wake(item_id: UUID) -> None:
    """Đánh thức ngay (thay vì đợi lần kiểm sau). Workflow đã kết thúc ⇒ tin bị bỏ qua."""
    if not _running:
        return

    async def go() -> None:
        status = await DBOS.get_workflow_status_async(workflow_id(item_id))
        if status is not None and status.status == "PENDING":
            await DBOS.send_async(workflow_id(item_id), "wake", "wake")

    await _outside_dbos(go())


async def reconcile_once() -> int:
    """Lưới an toàn: mọi việc đang mở phải có workflow sống. Lỡ tín hiệu, workflow lỗi, dữ liệu DBOS bị xoá ⇒ dựng lại."""
    n = 0
    for tenant_id, item_id, overdue in await work.open_items():
        status = await DBOS.get_workflow_status_async(workflow_id(item_id))
        if status is None:
            await start(tenant_id, item_id)
            n += 1
        elif status.status in ("ERROR", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"):
            log.warning("việc %s: workflow %s — chạy tiếp", item_id, status.status)
            await DBOS.resume_workflow_async(workflow_id(item_id))
            n += 1
        elif overdue:
            await wake(item_id)
    return n


async def _reconcile_forever(interval_s: float) -> None:
    while True:
        try:
            await reconcile_once()
        except Exception:   # vòng nền không được chết vì một lượt lỗi — log.exception rồi chạy lượt sau
            log.exception("đối soát việc theo dõi lỗi")
        await asyncio.sleep(interval_s)


def init() -> None:
    """Gọi trước ``launch``: cấu hình DBOS (một lần mỗi tiến trình)."""
    DBOS(config={"name": "barista", "system_database_url": settings.dbos_database_url or settings.owner_database_url,
                 "application_version": f"barista-work-v{WORKFLOW_VERSION}", "log_level": "WARNING"})


async def launch(*, reconcile: bool = True) -> asyncio.Task[None] | None:
    """Khởi động DBOS (chạy tiếp workflow dở), gắn móc vào module việc, mở vòng đối soát."""
    global _running
    DBOS.launch()
    _running = True
    work.on_start, work.on_wake = start, wake
    return asyncio.create_task(_reconcile_forever(settings.work_reconcile_s)) if reconcile else None


def shutdown() -> None:
    global _running
    _running = False
    work.on_start = work.on_wake = None
    DBOS.destroy()
    # DBOS gắn executor của nó làm executor MẶC ĐỊNH của vòng lặp, destroy tắt executor đó ⇒ vòng lặp còn chạy (test cùng phiên, khởi động lại
    # trong tiến trình) không tra được DNS / to_thread nữa ("cannot schedule new futures after shutdown") — trả lại một executor mới
    try:
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor())
    except RuntimeError:   # không có vòng lặp đang chạy (gọi từ code đồng bộ) — không có gì để trả
        pass
