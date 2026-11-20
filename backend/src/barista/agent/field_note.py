"""NÓI GHI CHÚ HIỆN TRƯỜNG (quyết định 0074 D) — sale gọi / gặp khách xong, nói một câu vào điện thoại thay vì gõ.

Hai bước, cùng đường với chat: (1) model CHÉP NGUYÊN VĂN đoạn ghi âm (người xem lại được — chữ hiện ngay trong khung chat, lưu như một câu
hỏi); (2) một lượt trợ lý bình thường với câu đó + lời dặn "ghi chú hiện trường" ⇒ các ĐỀ XUẤT (ghi chăm sóc, hẹn lại, đặt lịch, tạo việc) để
người bấm — cùng quyền, cùng hàng rào như gõ. Âm thanh không lưu lâu dài: server chỉ giữ trong bộ nhớ lúc chép lời; app ghi
ra file tạm trên máy và xoá ngay khi gửi.
"""

import time
from typing import Any
from uuid import UUID

from pydantic_ai import Agent, BinaryContent

from barista.agent import chat
from barista.agent import usage as metering
from barista.agent.llm import AIDisabled, model_for
from barista.kernel.principal import Principal
from barista.kernel.standing import Standing

AUDIO_TYPES = {"audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3", "audio/aac", "audio/mp4", "audio/m4a", "audio/x-m4a", "audio/ogg",
               "audio/flac", "audio/webm"}
AUDIO_BYTES = 12 * 1024 * 1024   # ~6 phút WAV 16 kHz mono

TRANSCRIBE = ("Chép NGUYÊN VĂN lời nói tiếng Việt trong đoạn ghi âm thành chữ, có dấu câu. Không tóm tắt, không thêm ý, không trả lời câu hỏi "
              "trong đó. Tên người, số tiền, ngày giờ, mã căn chép đúng như nghe. Không nghe được gì ⇒ trả về chuỗi rỗng.")
FIELD_STYLE = ("Đây là GHI CHÚ HIỆN TRƯỜNG người dùng vừa NÓI sau cuộc gọi / buổi gặp (chữ do máy chép từ giọng nói — có thể sai chính tả tên "
               "riêng). Tìm đúng khách / việc được nhắc, rồi soạn ĐỀ XUẤT ghi lại vào hệ thống: ghi chăm sóc + kết quả, cập nhật nhu cầu, hẹn gọi / "
               "gặp lại, việc cần làm tiếp. Không hỏi lại trừ khi không xác định được khách. Trả lời 1–2 câu.")


async def transcribe(p: Principal, audio: bytes, media_type: str) -> str:
    """Model chép lời (qua công tắc AI của công ty — tắt ⇒ ``AIDisabled``); tốn token ghi như mọi lượt (mục "voice")."""
    m = model_for(p, media=True)   # âm thanh: model mặc định của bản cài (model của hạng có thể chỉ đọc chữ)
    started = time.monotonic()
    run = await Agent(m, instructions=TRANSCRIBE, output_type=str).run(["Chép lời trong đoạn ghi âm này.",
                                                                        BinaryContent(data=audio, media_type=media_type)])
    await metering.record(p, surface="voice", category="transcribe", modules=[], usage=run.usage, model=getattr(m, "model_name", ""),
                          messages=run.new_messages(), started=started)
    return str(run.output).strip()


async def turn(p: Principal, audio: bytes, media_type: str, *, emit: chat.Emit, thread_id: UUID | None = None,
               standing: Standing | None = None) -> dict[str, Any]:
    """Chép lời ⇒ phát ``transcript`` (app hiện như câu người nói) ⇒ một lượt trợ lý kiểu ghi chú hiện trường (đề xuất để bấm)."""
    try:
        text = await transcribe(p, audio, media_type)
    except AIDisabled as e:
        await emit({"type": "error", "message": str(e)})
        return {}
    if not text:
        await emit({"type": "error", "message": "Em không nghe rõ — anh/chị nói lại giúp em nhé"})
        return {}
    await emit({"type": "transcript", "text": text})
    return await chat.chat_turn(p, text, emit=emit, thread_id=thread_id, style=FIELD_STYLE, surface="voice", standing=standing)
