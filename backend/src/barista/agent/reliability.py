"""ĐỘ BỀN khi gọi model — MỘT chính sách cho chat, soạn tài liệu, embedding và mọi lượt đi qua ``llm.model_for`` (runbook 15).

* ``classify``: lỗi nhà cung cấp ⇒ MỘT loại (``rate_limit`` 429 · ``unavailable`` 5xx / quá tải / rớt kết nối · ``timeout`` · ``config`` khoá sai /
  không có quyền / sai tên model · ``quota`` tài khoản hết tín dụng · ``safety`` bộ lọc an toàn chặn · ``bad_request`` yêu cầu sai / quá dài);
  không phải lỗi nhà cung cấp ⇒ None (bên gọi xử lý như lỗi thường). ``message`` = câu tiếng Việt cho người dùng theo loại; metering ghi loại.
* THỬ LẠI chỉ loại tạm (``RETRYABLE``), lùi cấp số nhân + nhiễu, theo Retry-After, có trần số lần + trần thời gian (``settings.llm_retry_*``).
  Thử lại ở MỨC MỘT LẦN GỌI MODEL (``Resilient.request`` / ``request_stream``) — không bao giờ chạy lại cả lượt: công cụ (lệnh ghi) chạy GIỮA hai lần
  gọi model, nên lần gọi được thử lại không chạy lại công cụ nào. Lượt stream chỉ thử lại khi lỗi xảy ra TRƯỚC mẩu đầu tiên (chưa có chữ nào tới
  người dùng); lỗi giữa dòng đi ra như cũ. SDK của nhà cung cấp tắt thử lại riêng (``max_retries=0``) để không nhân đôi số lần.
* DỰ PHÒNG (``settings.llm_fallback_models``): ``FallbackModel`` của Pydantic AI chuyển sang mục kế khi lỗi thuộc ``FALLBACK`` (tạm / sập / khoá
  hỏng / hết tín dụng) — không chuyển khi nội dung bị chặn hay yêu cầu sai (nhà khác cũng sẽ từ chối, hoặc chính sách nội dung khác nhau).
  CẦU DAO: N lần gọi hỏng liên tiếp trong cửa sổ ⇒ bỏ qua nhà đó M giây (mục cuối chuỗi không bao giờ bị bỏ qua). Trạng thái cầu dao theo tiến trình.
* Embedding KHÔNG có dự phòng: vector của model khác không so được với vector đã lưu (0141) — chỉ thử lại.
"""

import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

# cùng cách FallbackModel chờ mẩu stream đầu tiên rồi mới giao (hàm nội bộ của thư viện — bản ghim <2.67; nâng bản thì test_ai_reliability bắt)
from pydantic_ai._fallback import request_stream_until_first_event
from pydantic_ai.exceptions import (
    ContentFilterError,
    ModelAPIError,
    ModelAuthenticationError,
    ModelConnectionError,
    ModelHTTPError,
    ModelNotFoundError,
    ModelQuotaExceededError,
    ModelRateLimitError,
    ModelRequestRejectedError,
    ModelRequestTimeout,
    ModelServerError,
    ModelTimeoutError,
    ModelUnavailableError,
    UserError,
)
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models._request_timeout import RequestDeadline, start_request_deadline
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings, merge_model_settings

from barista.config import settings

log = logging.getLogger("barista.agent.reliability")

type ErrorClass = Literal["rate_limit", "unavailable", "timeout", "config", "quota", "safety", "bad_request"]
RETRYABLE: frozenset[str] = frozenset({"rate_limit", "unavailable", "timeout"})
FALLBACK: frozenset[str] = RETRYABLE | {"config", "quota"}   # nhà này không dùng được lúc này; nhà khác có khoá riêng thì dùng được

MESSAGES: dict[str, str] = {
    "rate_limit": "Nhà cung cấp mô hình đang giới hạn số lượt gọi — anh/chị đợi khoảng một phút rồi hỏi lại giúp em.",
    "unavailable": "Nhà cung cấp mô hình đang quá tải hoặc gián đoạn — anh/chị thử lại sau ít phút.",
    "timeout": "Mô hình trả lời chậm, anh/chị hỏi lại giúp.",
    "config": "Khoá hoặc cấu hình mô hình AI của bản cài chưa đúng — báo quản trị kiểm tra khoá AI.",
    "quota": "Tài khoản nhà cung cấp mô hình đã hết hạn mức / tín dụng — báo quản trị nạp thêm hoặc đổi khoá.",
    "safety": "Nhà cung cấp mô hình từ chối nội dung này theo bộ lọc an toàn của họ — anh/chị diễn đạt lại hoặc bỏ phần nhạy cảm.",
    "bad_request": "Yêu cầu gửi mô hình không hợp lệ (thường do cuộc trò chuyện quá dài hoặc tệp đính kèm không đọc được) — anh/chị mở "
                   "cuộc trò chuyện mới rồi hỏi lại.",
}


def message(cls: str) -> str:
    return MESSAGES.get(cls, MESSAGES["unavailable"])


_TAG = "_barista_model_path"


def tag(e: BaseException) -> BaseException:
    """Đánh dấu ngoại lệ đã đi ra từ ĐƯỜNG GỌI MODEL (``Resilient`` / ``call_with_retry``) — chỉ loại này mới được xếp loại theo nguyên nhân
    (``__cause__``) và theo kiểu lỏng (``TimeoutError`` trần, ``ConnectionError``, tên có "Timeout"). Lỗi của công cụ / của lượt (lệnh ghi quá hạn,
    ``asyncio.timeout`` của bên gọi) KHÔNG mang dấu ⇒ không bị nhận nhầm là lỗi nhà cung cấp."""
    try:
        setattr(e, _TAG, True)
    except Exception:   # đối tượng không cho gắn thuộc tính — hiếm; coi như chưa đánh dấu
        pass
    return e


def _strict(x: BaseException) -> str | None:
    """Loại của MỘT ngoại lệ theo KIỂU mà lớp nhà cung cấp / đường mạng ném (Pydantic AI, httpx) — không lần theo nguyên nhân, không đoán theo tên.
    Thứ tự quan trọng: lớp con trước lớp cha."""
    if isinstance(x, ContentFilterError):
        return "safety"
    if isinstance(x, ModelRequestTimeout | ModelTimeoutError | httpx.TimeoutException):
        return "timeout"
    if isinstance(x, ModelRateLimitError):
        return "rate_limit"
    if isinstance(x, ModelQuotaExceededError):
        return "quota"
    if isinstance(x, ModelAuthenticationError | ModelNotFoundError):
        return "config"
    if isinstance(x, ModelRequestRejectedError):   # gồm cả vượt cửa sổ ngữ cảnh
        return "bad_request"
    if isinstance(x, ModelUnavailableError | ModelServerError | ModelConnectionError | httpx.TransportError):
        return "unavailable"
    if isinstance(x, ModelHTTPError):   # mã HTTP chưa được thư viện xếp loại
        s = x.status_code
        return ("rate_limit" if s == 429 else "quota" if s == 402 else "config" if s in (401, 403, 404) else "timeout" if s == 408
                else "unavailable" if s >= 500 else "bad_request")
    if isinstance(x, ModelAPIError):
        return "unavailable"
    if isinstance(x, UserError):   # Pydantic AI: thiếu khoá / sai chuỗi model khi dựng (lỗi cấu hình của bản cài)
        return "config"
    return None


def _loose(x: BaseException) -> str | None:
    """Như ``_strict`` + kiểu lỏng, CHỈ cho ngoại lệ đã qua đường gọi model (có dấu ``tag``): SDK nhà cung cấp tự đặt tên, ``TimeoutError`` /
    ``ConnectionError`` trần của lần gọi model."""
    if (c := _strict(x)) is not None:
        return c
    if isinstance(x, TimeoutError):
        return "timeout"
    if isinstance(x, ConnectionError):
        return "unavailable"
    name = type(x).__name__
    if "Timeout" in name or "DeadlineExceeded" in name:
        return "timeout"
    return None


def classify(e: BaseException) -> str | None:
    """Loại lỗi nhà cung cấp, hoặc None = không phải lỗi gọi model. Ngoại lệ KHÔNG mang dấu ``tag`` chỉ được xếp theo kiểu của chính nó (và của
    thành viên nhóm lỗi) — một lỗi công cụ do ``TimeoutError`` gây ra không thành "nhà cung cấp chậm". Ngoại lệ có dấu: lần theo
    ``__cause__`` / ``__context__`` và nhận cả kiểu lỏng (``_loose``)."""
    seen: set[int] = set()
    todo: list[tuple[BaseException, bool]] = [(e, False)]
    while todo:
        x, tagged = todo.pop(0)
        if id(x) in seen:
            continue
        seen.add(id(x))
        tagged = tagged or bool(getattr(x, _TAG, False))
        if (c := (_loose(x) if tagged else _strict(x))) is not None:
            return c
        if isinstance(x, BaseExceptionGroup):
            todo += [(m, tagged) for m in x.exceptions]
        if tagged:
            todo += [(c, True) for c in (x.__cause__, x.__context__) if c is not None]
    return None


def falls_back(e: BaseException) -> bool:
    """Điều kiện chuyển sang nhà cung cấp dự phòng (``FallbackModel.fallback_on``). Lỗi cấu hình phía CODE (``UserError``) không chuyển."""
    return not isinstance(e, UserError) and classify(e) in FALLBACK


def provider_failed(e: BaseException) -> bool:
    """Lỗi do NHÀ CUNG CẤP / đường mạng TRẢ VỀ (kiểu ngoại lệ của lớp nhà cung cấp, loại đáng chuyển dự phòng) — chỉ loại này được tính cho cầu dao.
    Không tính: lỗi cấu hình phía code (``UserError``), ``TimeoutError`` của ``asyncio`` do bên gọi đặt, lỗi công cụ, cầu dao tự bỏ qua."""
    return not isinstance(e, ProviderSkipped | UserError) and _strict(e) in FALLBACK


def retry_after(e: BaseException) -> float | None:
    x: BaseException | None = e
    while x is not None:
        if isinstance(x, ModelAPIError) and x.retry_after is not None:
            return float(x.retry_after)
        x = x.__cause__
    return None


@dataclass(frozen=True)
class Policy:
    attempts: int = 3            # tổng số lần (kể cả lần đầu)
    base_s: float = 0.5
    max_delay_s: float = 8.0
    retry_after_max_s: float = 20.0
    budget_s: float = 30.0


def policy(attempts: int | None = None) -> Policy:
    return Policy(attempts=max(1, attempts if attempts is not None else settings.llm_retry_attempts), base_s=settings.llm_retry_base_s,
                  max_delay_s=settings.llm_retry_max_delay_s, retry_after_max_s=settings.llm_retry_after_max_s,
                  budget_s=settings.llm_retry_budget_s)


def backoff(pol: Policy, attempt: int, e: BaseException, *, elapsed: float = 0.0, rng: Callable[[], float] = random.random) -> float | None:
    """Số giây chờ trước lần thử kế (``attempt`` = số thứ tự từ 0 của lần VỪA hỏng); None = không thử lại. Nhiễu ±25% để nhiều tiến trình không
    đập lại cùng lúc; Retry-After của nhà cung cấp là mức tối thiểu (quá ``retry_after_max_s`` ⇒ không chờ)."""
    if classify(e) not in RETRYABLE or attempt + 1 >= pol.attempts:
        return None
    delay = min(pol.max_delay_s, pol.base_s * 2 ** attempt) * (0.75 + rng() / 2)
    if (ra := retry_after(e)) is not None:
        if ra > pol.retry_after_max_s:
            return None
        delay = max(delay, ra)
    if elapsed + delay > pol.budget_s:
        return None
    return delay


_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep   # test thay bằng hàm ghi lại số giây
_clock: Callable[[], float] = time.monotonic


async def call_with_retry[T](fn: Callable[[], Awaitable[T]], *, pol: Policy | None = None, label: str = "") -> T:
    """Gọi ``fn`` (MỘT lần gọi model không có tác dụng phụ — vd một lô embedding), thử lại lỗi tạm theo ``pol``."""
    pol = pol or policy()
    started, attempt = _clock(), 0
    while True:
        try:
            return await fn()
        except Exception as e:
            tag(e)
            delay = backoff(pol, attempt, e, elapsed=_clock() - started)
            if delay is None:
                raise
            log.info("%s: lỗi tạm (%s, lần %d) — thử lại sau %.1f giây", label or "model", classify(e), attempt + 1, delay)
            await _sleep(delay)
            attempt += 1


@dataclass
class Breaker:
    """Cầu dao của MỘT nhà cung cấp trong tiến trình: ``failures`` lần hỏng liên tiếp trong ``window_s`` ⇒ mở ``cooldown_s`` giây. Hết giờ ⇒ cho một
    lần thử; hỏng tiếp ⇒ mở lại ngay."""
    failures: int
    window_s: float
    cooldown_s: float
    recent: list[float] = field(default_factory=list)
    open_until: float = 0.0
    half_open: bool = False

    def allow(self) -> bool:
        now = _clock()
        if self.open_until and now >= self.open_until:   # hết thời gian nghỉ ⇒ cho thử, hỏng một lần là mở lại
            self.open_until, self.half_open = 0.0, True
        return now >= self.open_until

    def success(self) -> None:
        self.recent.clear()
        self.open_until, self.half_open = 0.0, False

    def failure(self) -> None:
        now = _clock()
        self.recent = [t for t in self.recent if now - t <= self.window_s] + [now]
        if self.half_open or len(self.recent) >= self.failures:
            self.open_until, self.half_open = now + self.cooldown_s, False
            self.recent.clear()
            log.warning("cầu dao mở: bỏ qua nhà cung cấp %.0f giây", self.cooldown_s)


_breakers: dict[str, Breaker] = {}


def breaker_for(spec: str) -> Breaker:
    b = _breakers.get(spec)
    if b is None:
        b = _breakers[spec] = Breaker(max(1, settings.llm_breaker_failures), settings.llm_breaker_window_s, settings.llm_breaker_cooldown_s)
    return b


def reset_breakers() -> None:
    _breakers.clear()


class ProviderSkipped(ModelUnavailableError):
    """Cầu dao đang mở — không gửi gì cho nhà cung cấp này; ``FallbackModel`` chuyển ngay sang mục kế."""


class Resilient(WrapperModel):
    """MỘT nhà cung cấp: thử lại lỗi tạm theo chính sách chung + cầu dao (khi còn dự phòng phía sau). Thời hạn ``request_timeout`` của Pydantic AI vẫn
    bọc ngoài mọi lần thử của một lần gọi (không kéo dài lượt quá trần cũ)."""

    def __init__(self, wrapped: Model, *, spec: str = "", breaker: Breaker | None = None, extra: ModelSettings | None = None) -> None:
        super().__init__(wrapped)
        self.spec = spec or wrapped.model_name
        self.breaker = breaker
        self.extra = extra   # vd {"max_retries": 0}: SDK không tự thử lại — một chính sách, không nhân đôi

    def _with_extra(self, s: ModelSettings | None) -> ModelSettings | None:
        return merge_model_settings(s, self.extra) if self.extra else s

    def _start_request_deadline(self, model_settings: ModelSettings | None, *, stream: bool) -> RequestDeadline | None:
        """Lần gọi STREAM: hạn giờ do chính ``Resilient`` đặt (mỗi lần thử một hạn, ngay lúc mở luồng) — nếu để bên ngoài (``FallbackModel`` / lượt chạy)
        đặt thì luồng đã được "nhìn trước" mẩu đầu (``request_stream_until_first_event``) khi hạn mới tới ⇒ thư viện từ chối. Lần gọi thường: hạn của
        thư viện bọc ngoài MỌI lần thử như cũ."""
        return None if stream else super()._start_request_deadline(model_settings, stream=stream)

    def _gate(self) -> None:
        if self.breaker is not None and not self.breaker.allow():
            raise ProviderSkipped(self.model_name, f"bỏ qua {self.spec}: cầu dao đang mở")

    def _done(self, e: BaseException | None) -> None:
        if self.breaker is None:
            return
        if e is None:
            self.breaker.success()
        elif provider_failed(e):   # chỉ lỗi nhà cung cấp TRẢ VỀ — không tính lỗi cấu hình phía code, timeout cục bộ, lỗi công cụ
            self.breaker.failure()

    def _wait(self, pol: Policy, attempt: int, e: BaseException, started: float) -> float | None:
        delay = backoff(pol, attempt, e, elapsed=_clock() - started)
        cls = classify(e)
        if delay is None:
            log.warning("model %s lỗi (%s) sau %d lần thử", self.spec, cls, attempt + 1)
        else:
            log.info("model %s lỗi tạm (%s, lần %d) — thử lại sau %.1f giây", self.spec, cls, attempt + 1, delay)
        return delay

    async def request(self, messages: list[ModelMessage], model_settings: ModelSettings | None,
                      model_request_parameters: ModelRequestParameters) -> ModelResponse:
        self._gate()
        pol, started, attempt = policy(), _clock(), 0
        while True:
            try:
                r = await self.wrapped.request(messages, self._with_extra(model_settings), model_request_parameters)
            except Exception as e:   # mọi thứ ra từ ĐÂY là của đường gọi model ⇒ đánh dấu (classify nhận cả kiểu lỏng + nguyên nhân)
                tag(e)
                if (delay := self._wait(pol, attempt, e, started)) is None:
                    self._done(e)
                    raise
                await _sleep(delay)
                attempt += 1
                continue
            self._done(None)
            return r

    @asynccontextmanager
    async def request_stream(self, messages: list[ModelMessage], model_settings: ModelSettings | None,
                             model_request_parameters: ModelRequestParameters, run_context: Any = None) -> AsyncIterator[StreamedResponse]:
        self._gate()
        pol, started, attempt = policy(), _clock(), 0
        while True:
            stack = AsyncExitStack()
            try:
                merged = self._with_extra(model_settings)
                response = await stack.enter_async_context(request_stream_until_first_event(
                    self.wrapped, start_request_deadline(self.wrapped, merged, stream=True), messages, merged, model_request_parameters, run_context))
            except Exception as e:   # chưa có mẩu nào tới người dùng ⇒ thử lại an toàn
                await stack.aclose()
                tag(e)
                if (delay := self._wait(pol, attempt, e, started)) is None:
                    self._done(e)
                    raise
                await _sleep(delay)
                attempt += 1
                continue
            break
        async with stack:
            try:
                yield response
            except Exception as e:   # lỗi GIỮA dòng: chữ đã tới người dùng ⇒ không thử lại. Ngoại lệ ném vào đây có thể là của bên tiêu thụ (công cụ,
                self._done(e)    # asyncio.timeout của lượt) — KHÔNG đánh dấu, và cầu dao chỉ tính lỗi mang kiểu nhà cung cấp (``provider_failed``)
                raise
        self._done(None)
