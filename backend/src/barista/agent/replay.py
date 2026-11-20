"""LƯU VÀ PHÁT LẠI lượt model ĐỌC TÀI LIỆU cho máy demo (``BARISTA_LLM_REPLAY_DIR``) — không dùng ở bản cài thật.

Chép chữ bản scan, phân loại, trích ô: cùng đầu vào thì trả lại đúng câu trả lời đã lưu, không cần mạng và không tốn lượt. Chưa có thì gọi model thật rồi lưu.
Trò chuyện của trợ lý (stream) không đi qua đây: lượt chat luôn gọi model thật. Người trình diễn phải nói rõ khi đang dùng kết quả đã chạy sẵn.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic_ai import ModelMessagesTypeAdapter, ModelResponse
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings


def _strip(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _strip(x) for k, x in v.items() if k not in ("timestamp", "tool_call_id", "run_id", "conversation_id", "metadata")}
    if isinstance(v, list):
        return [_strip(x) for x in v]
    return v


class ReplayModel(WrapperModel):
    def __init__(self, wrapped: Any, folder: str) -> None:
        super().__init__(wrapped)
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def _key(self, messages: list[ModelMessage], params: ModelRequestParameters) -> Path:
        body = _strip(json.loads(ModelMessagesTypeAdapter.dump_json(messages)))
        tools = [t.name for t in (*params.function_tools, *params.output_tools)]
        return self.folder / (hashlib.sha256(json.dumps([body, tools], sort_keys=True, ensure_ascii=False).encode()).hexdigest() + ".json")

    async def request(self, messages: list[ModelMessage], model_settings: ModelSettings | None, model_request_parameters: ModelRequestParameters) -> ModelResponse:
        path = self._key(messages, model_request_parameters)
        if path.exists():
            got = ModelMessagesTypeAdapter.validate_json(path.read_bytes())[0]
            assert isinstance(got, ModelResponse)
            return got
        response = await self.wrapped.request(messages, model_settings, model_request_parameters)
        path.write_bytes(ModelMessagesTypeAdapter.dump_json([response]))
        return response
