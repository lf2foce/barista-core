"""Việc theo dõi nhiều ngày (quyết định 0021): mở khi ký, kiểm điều kiện bằng code, ngoại lệ ⇒ trợ lý CỦA người phụ trách
(quyền người đó, bộ công cụ hẹp), đóng khi thu đủ; quyền nạp lại mỗi lần chạy. Phần lớn gọi thẳng ``work.step``; test cuối
chạy thật qua DBOS (ngủ → bị đánh thức bởi phiếu thu → xong)."""



from barista import work_runtime


async def test_signals_never_inherit_a_dbos_step_context() -> None:
    """Tín hiệu mở / đánh thức việc phát từ trong một DBOS step (trợ lý gọi công cụ ghi lúc xử lý ngoại lệ) phải chạy ở ngữ
    cảnh sạch — kế thừa ngữ cảnh step thì DBOS từ chối mở workflow (AssertionError)."""
    from dbos import _context as dbos_context

    async def seen() -> object:
        return dbos_context._dbos_context_var.get()

    token = dbos_context._dbos_context_var.set(object())  # type: ignore[arg-type]  # giả "đang trong step"
    try:
        assert await seen() is not None
        assert await work_runtime._outside_dbos(seen()) is None
    finally:
        dbos_context._dbos_context_var.reset(token)
