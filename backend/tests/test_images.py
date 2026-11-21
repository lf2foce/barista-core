"""Dán ảnh vào chat (ảnh chat với khách, phiếu cọc…): model đọc trong lượt đó, ảnh KHÔNG lưu lại (dữ liệu cá nhân)."""

import base64
import json
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic_ai import BinaryContent, ModelMessage, UserPromptPart
from pydantic_ai.models.function import AgentInfo

from barista.agent import chat
from barista.http_api import IMAGE_BYTES, parse_documents, parse_images
from tests.conftest import Who, owner_val
from tests.test_assistant import Scripted

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64   # đủ để kiểm đường đi; model giả không giải mã ảnh


async def _noop(_: dict[str, Any]) -> None:
    return None


async def test_the_model_sees_the_image_but_the_conversation_never_stores_it(who: Who) -> None:
    sale1 = await who("sale1")
    seen: list[Any] = []

    class Capture(Scripted):
        def _next(self, messages: list[ModelMessage], info: AgentInfo) -> Any:
            for part in getattr(messages[-1], "parts", []):
                if isinstance(part, UserPromptPart) and isinstance(part.content, list):
                    seen.extend(part.content)
            return super()._next(messages, info)

    out = await chat.chat_turn(sale1, "Cập nhật khách từ ảnh này", emit=_noop, images=[(PNG, "image/png")],
                               model=Capture(["Ảnh không rõ SĐT, anh/chị gửi lại giúp em."]))
    assert any(isinstance(c, BinaryContent) and c.data == PNG and c.media_type == "image/png" for c in seen)   # model đọc được ảnh
    raw = json.dumps(await owner_val("SELECT turns FROM core.chat_threads WHERE id = %s", (out["thread_id"],)), ensure_ascii=False)
    assert base64.b64encode(PNG).decode() not in raw and chat.IMAGE_GONE in raw                                  # DB không giữ ảnh
    turn = (await chat.load_thread(sale1, out["thread_id"]))["turns"][0]                                          # type: ignore[index]
    assert turn["attachments"] == [{"kind": "image", "media_type": "image/png", "bytes": len(PNG)}]


def test_image_limits() -> None:
    ok = parse_images([{"media_type": "image/png", "data": base64.b64encode(PNG).decode()}])
    assert ok == [(PNG, "image/png")] and parse_images(None) == []
    bad: list[Any] = [
        [{"media_type": "image/png", "data": base64.b64encode(PNG).decode()}] * 4,                # quá 3 ảnh
        [{"media_type": "application/pdf", "data": base64.b64encode(PNG).decode()}],             # không phải ảnh
        [{"media_type": "image/png", "data": "không phải base64!!"}],
        [{"media_type": "image/jpeg", "data": base64.b64encode(b"x" * (IMAGE_BYTES + 1)).decode()}],
    ]
    for raw in bad:
        with pytest.raises(HTTPException) as e:
            parse_images(raw)
        assert e.value.status_code == 422


async def test_a_document_dropped_in_chat_stays_in_the_conversation_and_makes_no_knowledge(who: Who) -> None:
    """ADR 0249: thả tài liệu vào chat trợ lý, chọn "chỉ dùng trong cuộc trò chuyện này" ⇒ model đọc chữ, KHÔNG thành tài liệu kho tri thức, không
    thành tệp trong sổ tệp; lượt ghi tên + cỡ."""
    sale1 = await who("sale1")
    seen: list[Any] = []
    docx = _docx("Báo giá căn mẫu: 3.200.000.000 đồng, cọc 10%.")

    class Capture(Scripted):
        def _next(self, messages: list[ModelMessage], info: AgentInfo) -> Any:
            for part in getattr(messages[-1], "parts", []):
                if isinstance(part, UserPromptPart) and isinstance(part.content, list):
                    seen.extend(part.content)
            return super()._next(messages, info)

    kb_before = await owner_val("SELECT count(*) FROM kb.documents")
    files_before = await owner_val("SELECT count(*) FROM core.files")
    out = await chat.chat_turn(sale1, "Tóm tắt báo giá này", emit=_noop, documents=[("bao-gia.docx", docx)],
                               model=Capture(["Báo giá 3,2 tỷ, cọc 10%."]))
    assert any(isinstance(c, str) and "3.200.000.000" in c and "bao-gia.docx" in c for c in seen)                   # model đọc được chữ
    assert await owner_val("SELECT count(*) FROM kb.documents") == kb_before                                           # không vào kho tri thức
    assert await owner_val("SELECT count(*) FROM core.files") == files_before                                          # thân tệp không lưu
    turn = (await chat.load_thread(sale1, out["thread_id"]))["turns"][0]                                              # type: ignore[index]
    assert turn["attachments"] == [{"kind": "document", "filename": "bao-gia.docx", "bytes": len(docx)}]


def _docx(text: str) -> bytes:
    import io

    from docx import Document

    d = Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_document_limits() -> None:
    pdf = b"%PDF-1.4 x"
    assert parse_documents([{"filename": "a.pdf", "data": base64.b64encode(pdf).decode()}]) == [("a.pdf", pdf)] and parse_documents(None) == []
    for raw in ([{"filename": "a.pdf", "data": base64.b64encode(pdf).decode()}] * 4,              # quá 3 tài liệu
                [{"filename": "a.exe", "data": base64.b64encode(pdf).decode()}],                 # loại không đọc được
                [{"filename": "a.pdf", "data": "không phải base64!!"}]):
        with pytest.raises(HTTPException) as e:
            parse_documents(raw)
        assert e.value.status_code == 422
