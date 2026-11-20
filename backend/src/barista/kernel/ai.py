"""MỘT LƯỢT MODEL cho gói (bề mặt ``barista.api.ai`` 1.2.0 — 0149): MỘT lượt model qua công tắc AI + hạn mức tháng của công ty, ghi lượt dùng vào ``core.ai_usage`` — gói không tự dựng
model, không tự ghi hạn mức. Công ty tắt AI / hết hạn mức ⇒ ``Denied`` (lý do người đọc được), không gửi gì ra nhà cung cấp model."""

from typing import Any

from barista.kernel.errors import Denied
from barista.kernel.executor import Ctx, Prep


async def run[T](ctx: Ctx | Prep, prompt: Any, *, instructions: str, output_type: type[T], category: str, modules: list[str],
                 model: Any = None, usage: Any = None) -> T:
    """``ctx``: ngữ cảnh handler, hoặc — tốt hơn cho lệnh ghi — bước chuẩn bị ``Prep`` của ``Module.command(prepare=…)`` (ADR 0200): model chạy khi KHÔNG
    giữ giao dịch / khoá dòng nào. Gọi trong handler vẫn được (lượt dùng ghi sau khi handler trả kết nối, không mượn thêm kết nối).
    ``usage``: ``RunUsage`` do bên gọi đưa vào để ĐỌC số token của lượt (vd đo chi phí của một bước) — không đưa thì lõi tự dựng.
    ``prompt``: chữ hoặc danh sách phần nội dung của Pydantic AI (chữ + ``BinaryContent`` ảnh / âm thanh). ``model``: chỉ cho test / chạy thử
    (vẫn qua công tắc AI). ``category``: loại việc trên màn lượt dùng AI (vd ``<module>:summary``).

    Chỉ trong lượt có NGƯỜI (review 09/11): quyền hệ thống (vòng quét, bên nghe sự kiện, ``call_system``) mang ``ai=True`` mặc định, không có hạn
    mức của công ty và không ghi được lượt dùng theo người ⇒ từ chối, không để gói đi vòng công tắc AI / hạn mức. Chạy thử (thẻ đề xuất) ⇒ từ chối:
    không gửi gì cho model trước khi người bấm."""
    p = ctx.p
    if p.system or p.user_id is None:
        raise Denied("Lượt AI của gói chỉ chạy trong thao tác của một người (không chạy bằng quyền hệ thống)")
    if ctx.preview:
        raise Denied("Chạy thử không gửi gì cho model")
    from pydantic_ai import Agent
    from pydantic_ai.usage import RunUsage

    from barista.agent import usage as metering
    from barista.agent.llm import AIDisabled, model_for

    try:
        m = model_for(p, model, media=not isinstance(prompt, str))   # ảnh / âm thanh ⇒ model mặc định của bản cài (0193)
    except AIDisabled as e:
        raise Denied(str(e)) from None
    used, ok, msgs = usage if usage is not None else RunUsage(), False, []
    failure: BaseException | None = None
    try:
        result = await Agent(m, instructions=instructions, output_type=output_type).run(prompt, usage=used)
        ok, msgs = True, result.new_messages()
        return result.output
    except Exception as e:   # loại lỗi nhà cung cấp vào lượt đo (agent/reliability); ngoại lệ vẫn đi ra như cũ cho gói xử lý
        failure = e
        raise
    finally:
        # mặt "work" = lượt nền / lệnh của gói, không phải chat (ràng buộc core.ai_usage.surface); loại việc ở ``category``
        await metering.record(p, surface="work", category=category, modules=modules, usage=used,
                              model=getattr(m, "model_name", ""), messages=msgs, ok=ok, error=failure)

