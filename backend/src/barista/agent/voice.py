"""Nói chuyện bằng giọng (Gemini Live qua google-genai) — app chỉ gửi / nhận âm thanh, khoá model và công cụ ở lại backend (quyết định 0042).

Một WebSocket ``/v1/assistant/voice`` cho mỗi cuộc nói chuyện. Gemini Live chỉ là lớp GIỌNG: mọi câu hỏi về công việc đi qua công cụ
``ask_assistant`` ⇒ chạy MỘT lượt chat thật (``chat.chat_turn``: cùng bộ công cụ theo quyền người hỏi, cùng cuộc trò chuyện) ⇒ bước / thẻ
stream về app đúng như chat gõ chữ, còn Gemini chỉ đọc tóm tắt. Giọng nói không có quyền gì hơn gõ chữ.

Giao thức (khung JSON text):
  app → server  {"type":"audio","data":b64 PCM16 16 kHz} · {"type":"text","text"} · {"type":"stop"}
  server → app  {"type":"ready","thread_id"} · {"type":"audio","data":b64 PCM16 24 kHz} · {"type":"user_caption"|"bot_caption","text"}
                {"type":"interrupted"} · {"type":"turn_complete"} · {"type":"chat_start","question"}
                {"type":"chat","event":<sự kiện như /assistant/chat/stream>} · {"type":"reconnecting"} · {"type":"error","detail"}
"""

import asyncio
import base64
import contextlib
import json
import logging
import re
import time
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types
from pydantic_ai import ModelResponse
from pydantic_ai.models import Model
from pydantic_ai.usage import RequestUsage, RunUsage

from barista.agent import chat, llm
from barista.agent import usage as metering
from barista.config import settings
from barista.db import db
from barista.kernel.principal import Principal
from barista.kernel.standing import Revoked, Standing

log = logging.getLogger("barista.voice")

VOICE = "Achernar"
_TAGS = re.compile(r"<[^>]*>|\{[^}]*\}")  # phiên âm có nhãn kỹ thuật kiểu <no speech>, {pause}

# Hỏi bằng giọng: người dùng chờ trong im lặng ⇒ trả lời chữ ngắn (nhanh hơn), chi tiết để thẻ gánh.
VOICE_STYLE = (
    "Câu này hỏi BẰNG GIỌNG NÓI, người dùng đang chờ nghe — trả lời TỐI ĐA 3 câu ngắn (dưới 50 từ), không bảng, không tiêu đề, không markdown, "
    "không liệt kê quá 2 tên / số; điều quan trọng nhất trước. Số liệu chi tiết đã hiện trên thẻ ở màn hình. Thẻ chỉ hiện khi bạn GỌI CÔNG CỤ: "
    "hỏi về số liệu, danh sách, một người hay hồ sơ ⇒ LUÔN gọi công cụ lấy số mới nhất, kể cả khi câu trả lời có vẻ đã nằm trong hội thoại trước."
)

ASK_TOOL = types.FunctionDeclaration(
    name="ask_assistant",
    description=("Hỏi trợ lý vận hành — nơi DUY NHẤT có dữ liệu thật của công ty (khách, việc, hồ sơ, tiền… theo các phân hệ công ty "
                 "đang dùng) và làm được việc (soạn đề xuất để người dùng bấm xác nhận). "
                 "Truyền nguyên ý người dùng kèm ngữ cảnh cần thiết từ các câu trước."),
    parameters=types.Schema(type=types.Type.OBJECT,
                            properties={"question": types.Schema(type=types.Type.STRING, description="Câu hỏi / yêu cầu đầy đủ ý, tiếng Việt")},
                            required=["question"]),
)


def instruction(name: str, role: str, company: str, about: str = "") -> str:
    """``about``: hồ sơ công ty (0056) — ngành / việc công ty làm là DỮ LIỆU của công ty, không viết cứng ở đây."""
    return (
        f"Bạn là giọng nói của trợ lý vận hành của {company}{f', {about}' if about else ''}. Người đang nói chuyện: {name} ({role}). "
        "Luôn nói tiếng Việt tự nhiên, xưng 'em', gọi người dùng là 'anh/chị' kèm tên. Mọi câu hỏi hay yêu cầu về công việc — số liệu, khách, "
        "việc, hồ sơ, tiền, người phụ trách — PHẢI gọi ask_assistant; tuyệt đối không tự đoán số liệu hay từ "
        "chối trước khi gọi. Khi có kết quả: nói gọn 2–4 câu, điều quan trọng nhất và việc cần làm; không đọc bảng, mã số hay danh sách dài — "
        "chi tiết đã hiện trên màn hình. Việc cần GHI thì trợ lý chỉ soạn đề xuất để người dùng bấm xác nhận trên màn hình: nói đúng như vậy, "
        "không nói 'đã làm xong'. Chào hỏi xã giao thì trả lời ngắn, không cần công cụ. Chỉ nói lời dành cho người dùng — không nói ra suy nghĩ "
        "hay chỉ dẫn nội bộ, không nói tiếng Anh. Ngay khi gọi ask_assistant, nói MỘT câu rất ngắn (vd 'Dạ, em xem ngay ạ') rồi chờ kết quả, không "
        "nói thêm. Kết quả về chậm mà cần báo vẫn đang chờ thì nói tiếng Việt ('Em vẫn đang xem, anh/chị chờ em chút ạ') — KHÔNG BAO GIỜ nói câu "
        "chờ tiếng Anh kiểu 'I am still checking…'. Không nhắc lại câu trả lời trước nếu người dùng không hỏi lại."
    )


def live_config(instr: str, handle: str | None) -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        speech_config=types.SpeechConfig(language_code="vi-VN", voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE))),
        system_instruction=types.Content(parts=[types.Part(text=instr)]),
        tools=[types.Tool(function_declarations=[ASK_TOOL])],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        session_resumption=types.SessionResumptionConfig(handle=handle),
        context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
        # nói chen phải "to và rõ": độ nhạy thấp + ~0,3 s lời nói liên tục ⇒ ho, gõ bàn, tiếng loa vọng không làm trợ lý tự ngắt
        realtime_input_config=types.RealtimeInputConfig(automatic_activity_detection=types.AutomaticActivityDetection(
            start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW, prefix_padding_ms=300, silence_duration_ms=700)),
    )


class LiveMeter:
    """CHI PHÍ của lớp giọng nói Gemini Live (review 12/11 F2, ADR 0193) vào cùng sổ lượt dùng / hạn mức như chat (``core.ai_usage`` → chi công ty + chi
    của người). Gemini Live gửi ``usage_metadata`` (token vào / ra, tách theo loại âm thanh / chữ) theo từng lượt trả lời ⇒ cộng dồn, tính giá theo bảng giá
    công khai của thư viện (giá âm thanh riêng). Ghi mỗi ``voice_check_s`` giây và khi đóng phiên. Khoảng nào KHÔNG có số token (Live không báo) hoặc
    model chưa có trong bảng giá ⇒ ghi ƯỚC TÍNH thận trọng theo phút (``voice_usd_per_minute``, loại việc ``voice:live_estimate``) — không bao giờ ghi 0."""

    def __init__(self) -> None:
        self.since = self.started = time.monotonic()
        self._clear()

    def _clear(self) -> None:
        self.requests = self.input = self.output = self.cached = self.audio_in = self.audio_out = 0

    def add(self, u: types.UsageMetadata) -> None:
        self.requests += 1
        self.input += (u.prompt_token_count or 0) + (u.tool_use_prompt_token_count or 0)
        self.output += (u.response_token_count or 0) + (u.thoughts_token_count or 0)
        self.cached += u.cached_content_token_count or 0
        self.audio_in += sum(d.token_count or 0 for d in u.prompt_tokens_details or [] if d.modality == types.MediaModality.AUDIO)
        self.audio_out += sum(d.token_count or 0 for d in u.response_tokens_details or [] if d.modality == types.MediaModality.AUDIO)

    def take(self) -> tuple[str, RunUsage, list[ModelResponse], Decimal]:
        """(loại việc, lượt dùng, câu trả lời mang số token để ghi tên model, chi phí) của khoảng từ lần ghi trước — rồi xoá bộ đếm. ``started`` giữ đầu
        khoảng (thời gian nói chuyện vào ``duration_ms`` — giây giọng nói của báo cáo token, 0193)."""
        now = time.monotonic()
        self.started = self.since
        minutes = Decimal(str(round((now - self.since) / 60, 4)))
        self.since = now
        estimate = (minutes * Decimal(str(settings.voice_usd_per_minute))).quantize(Decimal("0.000001"))
        used = RunUsage(requests=self.requests, input_tokens=self.input, output_tokens=self.output, cache_read_tokens=self.cached)
        resp = ModelResponse(parts=[], model_name=settings.voice_model, provider_name="google",
                             usage=RequestUsage(input_tokens=self.input, output_tokens=self.output, cache_read_tokens=self.cached,
                                                input_audio_tokens=self.audio_in, output_audio_tokens=self.audio_out))
        exact: Decimal | None = None
        if self.requests:
            try:
                exact = Decimal(str(resp.cost().total_price))
            except Exception:   # model Live chưa có trong bảng giá ⇒ ước tính (không ghi 0)
                exact = None
        self._clear()
        if exact is not None:
            return "voice:live", used, [resp], exact
        return "voice:live_estimate", used, [resp], estimate

    async def flush(self, p: Principal) -> None:
        category, used, msgs, cost = self.take()
        if cost <= 0 and not used.requests:
            return
        await metering.record(p, surface="voice", category=category, modules=[], usage=used, model=settings.voice_model, messages=msgs, priced=cost,
                              started=self.started)


_ACTIVE: dict[tuple[str, str], Bridge] = {}  # (công ty:người) → cuộc nói chuyện đang giữ phiên (mỗi người một phiên giọng nói)


class Bridge:
    """Một cuộc nói chuyện: nối WebSocket của app với phiên Gemini Live (tự nối lại khi rớt / goAway)."""

    def __init__(self, ws: WebSocket, p: Principal, instr: str, thread_id: UUID | None = None, *, model: Model | None = None,
                 standing: Standing | None = None, key: str = "") -> None:
        self.ws, self.p, self.instr, self.thread_id, self.model = ws, p, instr, thread_id, model
        # cả phiên một người gọi ⇒ kiểm lại tư cách (F1) + cửa giọng nói (hạng / công tắc / hạn mức — 0193) mỗi lượt và mỗi ``voice_check_s`` giây
        self.standing = standing or Standing(p)
        self.key = key
        self.meter = LiveMeter()
        self.stopped = ""
        self.handle: str | None = None
        self.live: Any = None
        self.send_lock = asyncio.Lock()
        self.turn_lock = asyncio.Lock()      # một lượt chat mỗi lần: Gemini đôi khi gọi công cụ lần hai do tiếng vọng
        self.pending: set[str] = set()
        self.tasks: set[asyncio.Task[Any]] = set()
        self.listening: asyncio.Task[Any] | None = None
        self.preempted = False
        self.finished = asyncio.Event()

    async def send(self, msg: dict[str, Any]) -> None:
        async with self.send_lock:
            await self.ws.send_text(json.dumps(msg, ensure_ascii=False, default=str))

    async def preempt(self) -> None:
        """Người này mở giọng nói ở nơi khác: đóng phiên cũ trước (một mã tiếp tục chỉ thuộc một kết nối)."""
        self.preempted = True
        if self.listening is not None:
            self.listening.cancel()
        with contextlib.suppress(Exception):
            await self.ws.close(code=4409, reason="đã mở giọng nói ở nơi khác")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.finished.wait(), 3)

    async def allowed(self) -> str:
        """"" = người này còn được nói chuyện bằng giọng; khác ⇒ lý do dừng (bị khoá / đổi quyền / đăng xuất, công ty tắt AI, hết hạn mức, hạng không cho
        giọng nói). Nạp lại người gọi từ DB (một lần ``auth.load``) — hạn mức vừa chạm trần do lượt trước cũng thấy ngay."""
        try:
            llm.voice_for(await self.standing.check(force=True))
        except (Revoked, llm.AIDisabled) as e:
            return str(e)
        return ""

    async def stop(self, reason: str) -> None:
        """Đóng phiên vì người gọi không còn được dùng: báo app, đóng WebSocket (``from_app`` kết thúc ⇒ ``run`` đóng phiên Live)."""
        if self.stopped:
            return
        self.stopped = reason
        log.info("voice: đóng phiên — người gọi không còn được dùng giọng nói")
        with contextlib.suppress(Exception):
            await self.send({"type": "error", "detail": reason})
        if self.listening is not None:
            self.listening.cancel()
        with contextlib.suppress(Exception):
            await self.ws.close(code=4403, reason="trợ lý giọng nói đã dừng")

    async def watch(self) -> None:
        """Mỗi ``voice_check_s`` giây: ghi chi phí Live của khoảng vừa qua, rồi kiểm lại người gọi + cửa giọng nói; không còn được ⇒ đóng phiên."""
        while True:
            await asyncio.sleep(settings.voice_check_s)
            try:
                await self.meter.flush(self.standing.current)
            except Exception:   # đo lỗi không đóng phiên (metering tự log) — kiểm quyền bên dưới vẫn chạy
                log.warning("voice: không ghi được chi phí Live", exc_info=True)
            if reason := await self.allowed():
                await self.stop(reason)
                return

    async def run(self) -> None:
        key = self.key
        if not key:
            await self.send({"type": "error", "detail": "Backend chưa cấu hình khoá model cho giọng nói"})
            return
        slot = (str(self.p.tenant_id), str(self.p.user_id))
        if (old := _ACTIVE.get(slot)) is not None and old is not self:
            await old.preempt()
        _ACTIVE[slot] = self
        client = genai.Client(api_key=key)
        pump = asyncio.create_task(self.from_app())
        watcher = asyncio.create_task(self.watch())
        opened = time.monotonic()
        self.meter.since = opened
        try:
            for attempt in range(4):
                entered = False
                try:
                    async with client.aio.live.connect(model=settings.voice_model, config=live_config(self.instr, self.handle)) as live:
                        entered = True
                        self.live = live
                        await self.send({"type": "ready", "thread_id": self.thread_id})
                        self.listening = asyncio.create_task(self.from_gemini(live))
                        await asyncio.wait({self.listening, pump, watcher}, return_when=asyncio.FIRST_COMPLETED)
                        if self.stopped or not self.listening.done():  # app rời / người gọi hết quyền ⇒ đóng NGAY phiên Gemini
                            self.listening.cancel()
                            await asyncio.gather(self.listening, return_exceptions=True)
                            return
                        if self.preempted:
                            return
                        if self.listening.result():    # True ⇒ goAway: nối lại ngay
                            await self.send({"type": "reconnecting"})
                            continue
                        return
                except WebSocketDisconnect:
                    return
                except Exception as exc:  # rớt mạng / lỗi phía Gemini: nối lại vài lần rồi mới báo
                    if pump.done() or self.stopped:
                        return
                    if self.handle and (not entered or getattr(exc, "code", None) in {400, 404, 409, 410, 1008}):
                        self.handle = None   # mã tiếp tục chỉ là tối ưu: bị từ chối ⇒ mở phiên Live mới (ngữ cảnh chat vẫn ở thread)
                        await self.send({"type": "reconnecting"})
                        continue
                    log.warning("voice: phiên Live lỗi (%s), lần %s", exc, attempt + 1)
                    if attempt == 3:
                        await self.send({"type": "error", "detail": "Mất kết nối giọng nói — thử lại sau"})
                        return
                    await self.send({"type": "reconnecting"})
                    await asyncio.sleep(0.5 * (attempt + 1))
                finally:
                    self.live = None
        finally:
            log.info("voice: đóng phiên sau %.0fs (app %s)", time.monotonic() - opened, "đã ngắt" if pump.done() else "còn nối")
            if _ACTIVE.get(slot) is self:
                del _ACTIVE[slot]
            self.finished.set()
            for task in [pump, watcher, *self.tasks]:
                task.cancel()
            await asyncio.gather(pump, watcher, *self.tasks, return_exceptions=True)
            try:   # phần chi phí Live còn lại của phiên (kể cả phiên ngắn hơn một kỳ kiểm)
                await self.meter.flush(self.standing.current)
            except Exception:
                log.warning("voice: không ghi được chi phí Live lúc đóng phiên", exc_info=True)
            await client.aio.aclose()

    async def from_app(self) -> None:
        """App → Gemini: âm thanh micro / câu gõ. App ngắt ⇒ đóng cả phiên."""
        try:
            while True:
                try:
                    msg = json.loads(await self.ws.receive_text())
                except json.JSONDecodeError:
                    continue
                live = self.live
                if live is None:
                    continue
                match msg.get("type"):
                    case "audio":
                        await live.send_realtime_input(audio=types.Blob(data=base64.b64decode(msg["data"]), mime_type="audio/pcm;rate=16000"))
                    case "text":
                        await live.send_realtime_input(text=str(msg.get("text", "")))
                    case "stop":
                        await self.ws.close()
                        return
        except (WebSocketDisconnect, RuntimeError):
            return

    async def from_gemini(self, live: Any) -> bool:
        """Gemini → app. ``receive()`` dừng sau mỗi lượt ⇒ lặp tới khi phiên đóng. True nếu cần nối lại."""
        while True:
            got = False
            async for m in live.receive():
                got = True
                if m.usage_metadata is not None:   # số token Live của lượt vừa trả lời ⇒ sổ chi phí (LiveMeter)
                    self.meter.add(m.usage_metadata)
                if (update := m.session_resumption_update) is not None:
                    self.handle = None if update.resumable is False else (update.new_handle or self.handle)
                if m.go_away is not None:
                    return True
                if m.tool_call and m.tool_call.function_calls:
                    for call in m.tool_call.function_calls:
                        t = asyncio.create_task(self.answer(live, call))
                        self.tasks.add(t)
                        t.add_done_callback(self.tasks.discard)
                if (sc := m.server_content) is None:
                    continue
                if sc.input_transcription and sc.input_transcription.text and (text := _TAGS.sub("", sc.input_transcription.text)):
                    await self.send({"type": "user_caption", "text": text})
                if sc.output_transcription and sc.output_transcription.text and (text := _TAGS.sub("", sc.output_transcription.text)):
                    await self.send({"type": "bot_caption", "text": text})
                if sc.model_turn and sc.model_turn.parts:
                    for part in sc.model_turn.parts:
                        if part.inline_data and part.inline_data.data:
                            await self.send({"type": "audio", "data": base64.b64encode(part.inline_data.data).decode()})
                if sc.interrupted:
                    await self.send({"type": "interrupted"})
                if sc.turn_complete:
                    await self.send({"type": "turn_complete"})
            if not got:   # phiên đã đóng phía Gemini
                return False

    async def answer(self, live: Any, call: types.FunctionCall) -> None:
        """ask_assistant ⇒ một lượt chat thật (bước / thẻ stream về app) ⇒ trả kết quả cho Gemini đọc tóm tắt."""
        q = str((call.args or {}).get("question", "")).strip()
        if q and q in self.pending:   # Gemini gọi lặp cùng câu do tiếng vọng
            await self._respond(live, call, "Câu này đang được xử lý — chờ kết quả của lượt đang chạy.")
            return
        self.pending.add(q)
        try:
            async with self.turn_lock:
                await self._turn(live, call, q)
        finally:
            self.pending.discard(q)

    async def _turn(self, live: Any, call: types.FunctionCall, q: str) -> None:
        if reason := await self.allowed():   # MỖI lượt: còn là thành viên, quyền chưa đổi, AI còn bật, chưa hết hạn mức, hạng còn cho giọng nói
            await self._respond(live, call, f"Trợ lý đã dừng: {reason} Nói với người dùng đúng như vậy rồi thôi, không gọi công cụ nữa.")
            await self.stop(reason)
            return
        await self.send({"type": "chat_start", "question": q})

        async def emit(ev: dict[str, Any]) -> None:
            if ev.get("type") == "done" and ev.get("thread_id"):
                self.thread_id = UUID(str(ev["thread_id"]))
            await self.send({"type": "chat", "event": ev})

        try:
            # người gọi VỪA kiểm (cùng quyền với lúc mở phiên; hạn mức / model của hạng mới nhất); công cụ của lượt vẫn kiểm từng lần (standing)
            out = await chat.chat_turn(self.standing.current, q, emit=emit, thread_id=self.thread_id, model=self.model, style=VOICE_STYLE, fast=True,
                                       surface="voice", standing=self.standing)
            self.thread_id = out["thread_id"] or self.thread_id
            answer = str(out["text"])
            await self.send({"type": "chat", "event": {"type": "reply", "text": answer, "thread_id": self.thread_id}})
        except Exception as exc:   # Gemini vẫn phải nhận phản hồi, nếu không sẽ treo chờ công cụ
            log.exception("voice: ask_assistant lỗi")
            await self.send({"type": "chat", "event": {"type": "error", "detail": "Trợ lý gặp lỗi khi trả lời — thử lại"}})
            answer = f"Lỗi khi hỏi trợ lý: {exc}"
        await self._respond(live, call, answer)

    async def _respond(self, live: Any, call: types.FunctionCall, answer: str) -> None:
        with contextlib.suppress(Exception):
            await live.send_tool_response(function_responses=[types.FunctionResponse(
                id=call.id, name=call.name,
                response={"answer": answer[:4000],
                          "cach_noi": "Nói 2–4 câu: điều quan trọng nhất + việc cần làm. Không đọc bảng, mã, danh sách dài — chi tiết đã hiện "
                                      "trên màn hình. Việc cần ghi mới chỉ là ĐỀ XUẤT chờ người dùng bấm xác nhận. Nếu câu trả lời đang hỏi "
                                      "lại thì hỏi lại đúng ý đó."})])


async def serve(ws: WebSocket, p: Principal, thread_id: UUID | None = None, *, model: Model | None = None,
                standing: Standing | None = None) -> None:
    """Cửa giọng nói (``llm.voice_for`` — ADR 0193) TRƯỚC khi mở Gemini Live: công ty tắt AI / hết hạn mức (công ty hoặc người) / hạng không cho giọng nói
    ⇒ báo lý do, đóng, không kết nối nhà cung cấp nào."""
    try:
        key = llm.voice_for(p)
    except llm.AIDisabled as e:
        await ws.send_json({"type": "error", "detail": str(e), "message": str(e)})
        await ws.close(code=4403)
        return
    async with db.tenant_tx(p, read_only=True) as conn:
        cur = await conn.execute("SELECT t.name, coalesce(pr.about, '') AS about FROM core.tenants t"
                                 " LEFT JOIN core.tenant_profile pr ON pr.tenant_id = t.id")
        row = await cur.fetchone()
    await Bridge(ws, p, instruction(p.name or p.email or "bạn", p.role_label, row["name"] if row else "công ty", row["about"] if row else ""),
                 thread_id, model=model, standing=standing, key=key).run()
