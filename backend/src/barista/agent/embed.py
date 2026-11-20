"""Gọi model EMBEDDING (tìm theo nghĩa trong kho tài liệu — quyết định 0141).

* Cùng cửa với trợ lý: ``llm.embedding_model_for`` kiểm công tắc AI + hạn mức của công ty TRƯỚC khi dựng model, cùng khoá model.
* Chia lô (``embed_batch``), mỗi lô có hạn giờ ngắn, lỗi tạm (mạng, 429, 5xx, quá hạn) thử lại tổng ``embed_attempts`` lần theo chính sách chung
  (``reliability`` — lùi cấp số nhân + nhiễu, theo Retry-After); không có nhà cung cấp dự phòng (vector khác model không so được); lỗi hẳn ⇒ ``Unavailable`` (bên gọi rơi về
  tìm từ khoá / để vòng quét thử lại sau).
* KHÔNG gọi khi đang giữ giao dịch DB: câu tìm nhúng ở bước ``prepare`` của truy vấn (ngoài giao dịch), việc nền nhúng giữa hai
  giao dịch ngắn. Đo như mọi lượt model (0049): một dòng ``core.ai_usage`` mỗi lần nhúng (surface "kb"), chi phí cộng vào hạn mức tháng. Gemini API không trả số
  token của embedding ⇒ ƯỚC TÍNH từ độ dài chữ (``estimated_tokens``) rồi tính giá theo bảng giá công khai — ghi rõ là ước tính.
* MODEL THEO CÔNG TY (ADR 0232, ``embed_profile``): mỗi hàm nhận hồ sơ nhúng của công ty (không nêu ⇒ nạp theo ``p``). Nhà cung cấp ngoài (google / openai…) nhận
  ``dimensions`` qua API; máy chủ tự dựng (``openai-compatible:``) KHÔNG nhận ``dimensions`` — model Matryoshka được CẮT phía mình (``truncate``) rồi chuẩn hoá, model
  khác phải trả đúng số chiều cấu hình. Tiền tố câu hỏi / đoạn (EmbeddingGemma, nomic, e5) do hồ sơ khai. Đo theo (công ty, model): ``core.ai_usage.model``; máy tự dựng 0 USD.
* Vector chuẩn hoá độ dài 1 ⇒ tích vô hướng = cosine (nhánh mảng ``real[]`` không có pgvector tính bằng SQL thuần).
"""

import asyncio
import logging
import math
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic_ai.embeddings import EmbeddingModel, EmbeddingResult
from pydantic_ai.usage import RequestUsage, RunUsage

from barista.agent import embed_profile, reliability
from barista.agent import usage as metering
from barista.agent.embed_profile import Profile
from barista.agent.llm import AIDisabled, embedding_key, embedding_model_for
from barista.config import settings
from barista.kernel.principal import Principal

log = logging.getLogger("barista.agent.embed")

Kind = Literal["document", "query"]
MAX_CHARS = 6000   # gemini-embedding-001 nhận ~2048 token / đoạn; đoạn kho tài liệu ~900 ký tự nên chỉ chặn trường hợp lạ


class Unavailable(Exception):
    """Không nhúng được lúc này — ``reason`` là câu cho người đọc (vì sao chỉ tìm từ khoá)."""

    def __init__(self, reason: str, *, transient: bool = False, ai_off: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.transient = transient   # lỗi tạm (mạng, quá hạn, 429, 5xx) — cả lô hỏng như nhau; khác: lỗi riêng của đoạn (thử từng đoạn)
        self.ai_off = ai_off         # công ty tắt AI / hết hạn mức — không gửi gì ra nhà cung cấp


@dataclass
class Vectors:
    model: str      # đúng chuỗi cấu hình (``google:gemini-embedding-001``) — lưu cạnh mỗi vector; đổi ⇒ nhúng lại
    dims: int
    vectors: list[list[float]]
    cost_usd: Decimal | None = None
    tokens: int = 0


ModelSource = EmbeddingModel | Callable[[Profile], EmbeddingModel]
_override: ModelSource | None = None
_cache: OrderedDict[tuple[str, str], list[float]] = OrderedDict()   # (dấu vân hồ sơ, câu) ⇒ vector
CACHE_SIZE = 512


def use_model(model: ModelSource | None) -> None:
    """Test / chạy thử: tiêm model embedding — một model, hoặc hàm hồ sơ → model (vẫn qua công tắc AI của công ty). None = model thật theo cấu hình."""
    global _override
    _override = model
    _cache.clear()


def spec() -> tuple[str, int]:
    """(model, số chiều) MẶC ĐỊNH của bản cài — công ty có hồ sơ riêng thì dùng ``embed_profile.for_tenant``."""
    prof = embed_profile.instance_profile()
    return prof.model, prof.dims


def unconfigured(profile: Profile) -> str:
    """"" = có thể nhúng; khác rỗng = lý do không (tắt / chưa chọn model / chưa có khoá) — không gọi mạng."""
    if profile.off:
        return "bản cài chưa bật tìm theo nghĩa" if profile.source == "bản cài" else "công ty này đã tắt tìm theo nghĩa"
    if _override is not None:
        return ""
    if not profile.compat and not embedding_key(profile):
        return "chưa cấu hình khoá model"
    return ""


def _model(p: Principal, profile: Profile) -> EmbeddingModel:
    ov = _override(profile) if callable(_override) else _override
    return embedding_model_for(p, ov, profile)


def estimated_tokens(texts: Sequence[str]) -> int:
    """Gemini API không trả token của embedding: ~3 ký tự / token với chữ tiếng Việt có dấu (đo trên bộ đếm của Gemini, làm tròn lên)."""
    return sum(math.ceil(len(t) / 3) for t in texts)


def _normalize(v: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _transient(e: BaseException) -> bool:
    """Lỗi tạm của nhà cung cấp (cả lô hỏng như nhau) — cùng cách xếp loại với chat (``reliability``)."""
    return reliability.classify(e) in reliability.RETRYABLE


def price(profile: Profile, model_name: str, provider: str, tokens: int) -> Decimal | None:
    if profile.self_hosted:   # máy chủ tự dựng: 0 USD (vẫn đếm lượt + token) trừ khi nhà vận hành khai giá (nhà thứ ba tương thích OpenAI)
        return Decimal(str(profile.usd_per_mtok or 0)) * tokens / 1_000_000
    try:
        from genai_prices import calc_price

        return Decimal(str(calc_price(RequestUsage(input_tokens=tokens), model_name, provider_id=provider).total_price))
    except Exception:   # model chưa có trong bảng giá ⇒ NULL (không phải 0)
        return None


def _shape(profile: Profile, v: Sequence[float]) -> list[float]:
    """Đưa vector model trả về đúng ``profile.dims`` chiều rồi chuẩn hoá. Nhiều chiều hơn ⇒ chỉ cắt khi hồ sơ cho phép (Matryoshka); còn lại ⇒ lỗi rõ ràng
    (không âm thầm lưu vector sai chiều)."""
    n = len(v)
    if n == profile.dims:
        return _normalize(v)
    if n > profile.dims and profile.truncate:
        return _normalize(v[:profile.dims])
    hint = " — model Matryoshka thì bật truncate, nếu không đặt dims đúng số chiều gốc" if n > profile.dims else ""
    raise Unavailable(f"model embedding trả {n} chiều, hồ sơ cần {profile.dims}{hint}")


def _texts(profile: Profile, texts: Sequence[str], kind: Kind) -> list[str]:
    prefix = profile.query_prefix if kind == "query" else profile.document_prefix
    return [prefix + t[:MAX_CHARS] for t in texts]


def _settings(profile: Profile) -> dict[str, Any]:
    return {} if profile.compat else {"dimensions": profile.dims}   # máy tự dựng: không gửi dimensions (vLLM từ chối model không hỗ trợ) — cắt phía mình


async def _call(model: EmbeddingModel, profile: Profile, batch: list[str], kind: Kind, limit_s: float, attempts: int) -> EmbeddingResult:
    async def once() -> EmbeddingResult:
        return await asyncio.wait_for(model.embed(batch, input_type=kind, settings=_settings(profile)), limit_s)  # type: ignore[arg-type]

    try:
        return await reliability.call_with_retry(once, pol=reliability.policy(attempts), label="embedding")
    except Exception as e:
        raise Unavailable("quá hạn khi gọi model embedding" if isinstance(e, TimeoutError)
                          else f"model embedding lỗi ({type(e).__name__})", transient=_transient(e)) from e


async def _batches(model: EmbeddingModel, prof: Profile, texts: list[str], kind: Kind, limit_s: float, attempts: int,
                   used: RunUsage) -> tuple[list[list[float]], Decimal | None]:
    """Gọi model theo lô (``embed_batch``): (vector đã đưa về đúng số chiều + chuẩn hoá, chi phí hoặc None nếu có lô chưa có giá). Cộng lượt / token vào ``used``
    NGAY SAU MỖI LÔ (bên gọi vẫn ghi đo cho phần đã tốn khi một lô sau hỏng)."""
    out: list[list[float]] = []
    cost, priced = Decimal(0), True
    for i in range(0, len(texts), max(1, settings.embed_batch)):
        batch = texts[i:i + max(1, settings.embed_batch)]
        r = await _call(model, prof, batch, kind, limit_s, attempts)
        if len(r.embeddings) != len(batch):
            raise Unavailable("model embedding trả sai số vector")
        out += [_shape(prof, v) for v in r.embeddings]
        tokens = r.usage.input_tokens or estimated_tokens(batch)
        used.requests += 1
        used.input_tokens += tokens
        c = price(prof, r.model_name, r.provider_name, tokens)
        if c is None:
            priced = False
        else:
            cost += c
    return out, (cost if priced else None)


async def embed(p: Principal, texts: Sequence[str], *, kind: Kind, meter: Principal | None = None,
                category: str = "kb:embed", profile: Profile | None = None) -> Vectors:
    """Nhúng ``texts`` (giữ thứ tự). ``p``: người / công ty mà dữ liệu thuộc về — công tắc AI kiểm theo ``p``. ``meter``: người ghi lượt đo
    (mặc định ``p``; việc nền không người bấm truyền người thêm tài liệu). ``profile``: hồ sơ nhúng của công ty (không nêu ⇒ nạp theo ``p``).
    Hỏng ⇒ ``Unavailable``."""
    prof = profile or await embed_profile.for_tenant(p)
    if reason := unconfigured(prof):
        raise Unavailable(reason)
    try:
        model = _model(p, prof)
    except AIDisabled as e:
        raise Unavailable(str(e), ai_off=True) from None
    limit_s = settings.embed_query_timeout_s if kind == "query" else settings.embed_timeout_s
    attempts = 1 if kind == "query" else max(1, settings.embed_attempts)
    used, ok = RunUsage(), False
    cost: Decimal | None = None
    failure: BaseException | None = None
    started = time.monotonic()
    try:
        vecs, cost = await _batches(model, prof, _texts(prof, texts, kind), kind, limit_s, attempts, used)
        ok = True
    except Exception as e:
        failure = e.__cause__ if isinstance(e, Unavailable) and e.__cause__ is not None else e
        raise
    finally:
        if used.requests:   # lô hỏng giữa chừng: lượt đã tốn vẫn ghi (chi phí chưa biết ⇒ NULL)
            await metering.record(meter or p, surface="kb", category=category, modules=["kb"], usage=used, model=prof.model, ok=ok,
                                  started=started, priced=cost, error=failure)
    return Vectors(prof.model, prof.dims, vecs, cost, used.input_tokens)


async def embed_texts(profile: Profile, texts: Sequence[str], *, kind: Kind) -> Vectors:
    """Nhúng KHÔNG qua công ty / DB / lượt đo — cho đo đạc (``docread.bench --retrieval``) và nhà vận hành thử một hồ sơ. Cùng đường gọi, cắt chiều, tiền tố, giá như ``embed``."""
    if reason := unconfigured(profile):
        raise Unavailable(reason)
    model = _model(_AllowAll, profile)  # type: ignore[arg-type]
    used = RunUsage()
    vecs, cost = await _batches(model, profile, _texts(profile, texts, kind), kind, max(settings.embed_timeout_s, 30.0), 1, used)
    return Vectors(profile.model, profile.dims, vecs, cost, used.input_tokens)


@dataclass
class Probe:
    ok: bool
    native_dims: int = 0
    seconds: float = 0.0
    message: str = ""


async def probe(profile: Profile, *, sample: Sequence[str] = ("Khách được hoàn lại tiền giữ chỗ trong 7 ngày.",)) -> Probe:
    """Thử MỘT lần gọi thật với câu mẫu cố định (không dữ liệu công ty ⇒ không qua công tắc AI, không ghi lượt đo) — nhà vận hành kiểm máy chủ / khoá /
    số chiều TRƯỚC khi đổi hồ sơ. Báo số chiều gốc model trả và thời gian một lô."""
    if reason := unconfigured(profile):
        return Probe(False, message=reason)
    try:
        ov = _override(profile) if callable(_override) else _override
        model = ov or embedding_model_for(_AllowAll, None, profile)  # type: ignore[arg-type]
    except Exception as e:
        return Probe(False, message=f"không dựng được model ({type(e).__name__}: {e})")
    started = time.monotonic()
    try:
        texts = _texts(profile, sample, "document")
        r = await asyncio.wait_for(model.embed(texts, input_type="document", settings=_settings(profile)), max(settings.embed_timeout_s, 30.0))  # type: ignore[arg-type]
    except Exception as e:
        return Probe(False, seconds=time.monotonic() - started, message=f"gọi model lỗi ({type(e).__name__}: {str(e)[:160]})")
    n = len(r.embeddings[0]) if r.embeddings else 0
    secs = time.monotonic() - started
    if n == profile.dims or (n > profile.dims and profile.truncate):
        return Probe(True, n, secs, "")
    if n > profile.dims:
        return Probe(False, n, secs, f"model trả {n} chiều gốc, hồ sơ muốn {profile.dims}: bật truncate nếu là model Matryoshka, hoặc đặt dims={n} (cần chỉ mục {n} chiều)")
    return Probe(False, n, secs, f"model chỉ trả {n} chiều, ít hơn {profile.dims}")


class _Gate:
    """Người gọi giả cho ``probe`` (câu mẫu cố định, không dữ liệu công ty): công tắc AI không áp."""
    ai = True
    ai_block = ""


_AllowAll = _Gate()


def cached_query_vector(p: Principal, text: str, profile: Profile) -> list[float] | None:
    """Chỉ đọc bộ nhớ (không gọi mạng) — cho nơi đang giữ giao dịch DB (gọi lồng)."""
    if unconfigured(profile) or not p.ai or p.ai_block:
        return None
    return _cache.get((profile.fingerprint(), text.strip().lower()))


async def query_vector(p: Principal, text: str, profile: Profile | None = None) -> list[float]:
    """Vector của câu tìm — nhớ 512 câu gần nhất trong tiến trình (trợ lý hay tìm lại đúng câu cũ; không tốn lượt gọi)."""
    prof = profile or await embed_profile.for_tenant(p)
    key = (prof.fingerprint(), text.strip().lower())
    if (hit := _cache.get(key)) is not None and not unconfigured(prof) and p.ai and not p.ai_block:
        _cache.move_to_end(key)
        return hit
    v = (await embed(p, [text], kind="query", category="kb:embed_query", profile=prof)).vectors[0]
    _cache[key] = v
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return v
