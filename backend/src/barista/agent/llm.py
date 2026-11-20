"""Lớp model: Pydantic AI — đổi nhà cung cấp bằng MỘT chuỗi cấu hình (``google:gemini-3.8-flash``,
``anthropic:claude-sonnet-5``, ``openai:…``). Model dựng ra luôn bọc ``reliability.Resilient`` (thử lại lỗi tạm) và, khi bản cài khai
``llm_fallback_models``, là CHUỖI dự phòng ``FallbackModel`` có cầu dao — vẫn chỉ một cửa ``model_for`` (runbook 15)."""

import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic_ai import ModelSettings
from pydantic_ai.embeddings import EmbeddingModel, infer_embedding_model
from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.providers import Provider, infer_provider, infer_provider_class

from barista.agent import reliability
from barista.config import settings
from barista.kernel.principal import Principal

if TYPE_CHECKING:
    from barista.agent.embed_profile import Profile as EmbedProfile

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
log = logging.getLogger("barista.agent.llm")

# Mọi lần gọi model có hạn: nhà cung cấp mở kết nối rồi im thì lượt chat kết thúc, không treo app.
# thinking="low": việc của trợ lý là chọn công cụ + tóm tắt số đã có — nghĩ sâu chỉ thêm giây chờ (đo 30/10: 3 lượt gọi
# model chiếm gần hết ~15 giây, công cụ ~0). timeout = số giây IM LẶNG tối đa giữa hai mẩu trả về: Gemini không gửi dần lệnh gọi công cụ
# mà gửi cả khối khi viết xong ⇒ lệnh dài (ghép app — 0098) có lúc im > 30 giây; 60 giây, trần cả lượt gọi vẫn 120.
CHAT_SETTINGS = ModelSettings(temperature=0.2, thinking=settings.agent_thinking, timeout=60, request_timeout=120)  # type: ignore[typeddict-item]
# Lượt giọng nói: người dùng chờ trong im lặng ⇒ không "nghĩ" thêm (như barista_bds STEP_SETTINGS).
VOICE_SETTINGS = ModelSettings(temperature=0.2, timeout=30, request_timeout=90)


def read_key(api_key: str = "", key_file: str = "", key_var: str = "") -> str:
    """Khoá model: giá trị trực tiếp, hoặc MỘT biến đọc từ một file .env khác (không chép khoá vào cấu hình bản cài)."""
    if api_key:
        return api_key
    if key_file and key_var:
        try:
            with open(key_file, encoding="utf-8") as f:
                for line in f:
                    if line.startswith(f"{key_var}="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            return ""
    return ""


def resolve_api_key() -> str:
    return read_key(settings.llm_api_key, settings.llm_api_key_file, settings.llm_api_key_var)


def chain_specs(*, fallback: bool = True) -> list[tuple[str, str]]:
    """(chuỗi model, khoá) theo thứ tự thử: model chính rồi các dự phòng (``fallback=False`` ⇒ chỉ model chính). Dự phòng không khai khoá: CÙNG nhà cung cấp với model chính ⇒ khoá chính;
    khác ⇒ "" (biến môi trường chuẩn của nhà cung cấp đó — không bao giờ gửi khoá của nhà này sang nhà khác)."""
    primary = settings.llm_model
    key = resolve_api_key()
    out = [(primary, key)]
    for item in settings.llm_fallback_models if fallback else []:
        spec = item if isinstance(item, str) else item.get("model", "")
        if not spec or spec in (s for s, _ in out):
            continue
        own = "" if isinstance(item, str) else read_key(item.get("api_key", ""), item.get("api_key_file", ""), item.get("api_key_var", ""))
        same = spec.split(":", 1)[0] == primary.split(":", 1)[0]
        out.append((spec, own or (key if same else "")))
    return out


def provider_of(spec: str) -> str:
    return spec.split(":", 1)[0]


def key_for(spec: str) -> str:
    """Khoá cho model ``spec`` theo NHÀ CUNG CẤP của nó (ADR 0193 — model của hạng trợ lý AI): khoá khai riêng cho nhà đó (``llm_keys``) · cùng nhà với
    model chính ⇒ khoá chính · mục dự phòng cùng nhà có khai khoá ⇒ khoá đó · không ⇒ "" (biến môi trường chuẩn của nhà đó). Không bao giờ trả khoá
    của nhà này cho nhà khác; không ghi giá trị khoá vào log."""
    prov = provider_of(spec)
    own = settings.llm_keys.get(prov)
    if own:
        return own if isinstance(own, str) else read_key(own.get("api_key", ""), own.get("api_key_file", ""), own.get("api_key_var", ""))
    if prov == provider_of(settings.llm_model):
        return resolve_api_key()
    for item in settings.llm_fallback_models:
        if isinstance(item, dict) and provider_of(item.get("model", "")) == prov:
            if k := read_key(item.get("api_key", ""), item.get("api_key_file", ""), item.get("api_key_var", "")):
                return k
    return ""


def known_provider(spec: str) -> bool:
    """Chuỗi model có nhà cung cấp thư viện biết (kiểm khi quản trị nhập — chưa cần khoá)."""
    try:
        infer_provider_class(provider_of(spec))
    except Exception:
        return False
    return True


def usable(spec: str) -> bool:
    """Bản cài DỰNG ĐƯỢC model này (nhà cung cấp có khoá) — danh sách chọn chỉ hiện model dùng được; không gọi mạng."""
    try:
        _provider_model(spec, key_for(spec))
    except Exception:
        return False
    return True


class AIDisabled(Exception):
    """Công ty đã tắt trợ lý AI — không dựng model, dữ liệu công ty không gửi ra nhà cung cấp model."""


AI_OFF = "Công ty này đã tắt trợ lý AI (dữ liệu không gửi ra nhà cung cấp model). Quản trị bật lại khi khách đã đồng ý."
VOICE_OFF = "Tài khoản của bạn chưa được nói chuyện bằng giọng với trợ lý — dùng ô chat (gõ hoặc đọc chính tả)."   # không nêu hạng / chính sách (0193)


def _gate(p: Principal) -> None:
    """Công tắc AI của công ty + hạn mức tháng của công ty VÀ của chính người này (0092 / 0193) — chung cho mọi cửa dưới đây."""
    if not p.ai:
        raise AIDisabled(AI_OFF)
    if p.ai_block:   # hết hạn mức AI tháng này (công ty hoặc người — auth.ai_block) — cùng đường xử lý như công ty tắt AI
        raise AIDisabled(p.ai_block)


def model_for(p: Principal, model: Model | None = None, *, media: bool = False, spec: str | None = None) -> Model:
    """CỬA DUY NHẤT để có model (chat, tài liệu, đào tạo, việc theo dõi): kiểm công tắc AI + hạn mức tháng của công ty và của người TRƯỚC, kể cả khi
    bên gọi tiêm sẵn model (test / chạy thử) — tắt / hết hạn mức ⇒ ``AIDisabled``. Model = model của HẠNG trợ lý AI của người (``p.ai_model`` — ADR 0193),
    trống / nhà cung cấp chưa có khoá ⇒ model mặc định của bản cài. ``media``: lượt gửi ảnh / âm thanh / PDF ⇒ model mặc định của bản cài (model của hạng
    có thể chỉ đọc chữ — vd deepseek). ``spec``: bên gọi đã CHỌN model (hồ sơ đọc tài liệu theo công ty — ADR 0232) ⇒ đúng model đó, khoá theo nhà cung cấp của nó,
    không chuỗi dự phòng (dữ liệu chỉ tới nhà đã chọn); chưa có khoá / sai chuỗi ⇒ lỗi (bên gọi báo, không âm thầm đổi model). ``tests/test_meta.py`` chặn gọi
    ``build_model`` ngoài file này."""
    _gate(p)
    if model is not None:
        return model
    if spec:
        built_spec: Model = reliability.Resilient(_provider_model(spec, key_for(spec)), spec=spec, extra=NO_SDK_RETRIES)
        if settings.llm_replay_dir:
            from barista.agent.replay import ReplayModel

            return ReplayModel(built_spec, settings.llm_replay_dir)
        return built_spec
    # nhà cung cấp thứ hai chỉ khi CÔNG TY này bật (core.ai_allow_fallback_vendor) — chính sách là dữ liệu
    built = (None if media else _tier_model(p)) or build_model(fallback=p.ai_fallback)
    if settings.llm_replay_dir:
        from barista.agent.replay import ReplayModel

        return ReplayModel(built, settings.llm_replay_dir)
    return built


def voice_for(p: Principal) -> str:
    """Cửa của GIỌNG NÓI (Gemini Live — ADR 0193): cùng công tắc + hạn mức như ``model_for``, thêm hạng của người phải cho phép giọng nói. Kiểm TRƯỚC
    khi mở phiên Live và kiểm lại định kỳ / mỗi lượt trong phiên (agent/voice.py). Trả khoá của nhà cung cấp giọng nói ("" = chưa cấu hình)."""
    _gate(p)
    if not p.ai_voice:
        raise AIDisabled(VOICE_OFF)
    return key_for(f"google:{settings.voice_model}")


def _tier_model(p: Principal) -> Model | None:
    """Model của hạng (``p.ai_model``) khi khác model chính của bản cài và dựng được; nhà cung cấp chưa có khoá / chuỗi sai ⇒ None (bên gọi dùng
    model mặc định) + MỘT dòng log — không làm hỏng lượt, không ghi khoá. Công ty bật nhà thứ hai ⇒ chuỗi dự phòng của bản cài nối sau."""
    spec = p.ai_model
    if not spec or spec == settings.llm_model:
        return None
    try:
        first = reliability.Resilient(_provider_model(spec, key_for(spec)), spec=spec, extra=NO_SDK_RETRIES,
                                      breaker=reliability.breaker_for(spec) if p.ai_fallback else None)
    except Exception as e:
        log.warning("hạng trợ lý AI %r: không dựng được model %s (%s — chưa có khoá / sai chuỗi) — dùng model mặc định của bản cài",
                    p.ai_tier, spec, type(e).__name__)
        return None
    if not p.ai_fallback:
        return first
    rest = build_model(fallback=True)
    return FallbackModel(first, *(rest.models if isinstance(rest, FallbackModel) else [rest]), fallback_on=reliability.falls_back)


def key_named(ref: str) -> str:
    """Khoá theo TÊN mục trong ``settings.llm_keys`` (hồ sơ nhúng của công ty trỏ tới tên, không bao giờ chứa khoá). Không có ⇒ ""."""
    own = settings.llm_keys.get(ref) if ref else None
    if not own:
        return ""
    return own if isinstance(own, str) else read_key(own.get("api_key", ""), own.get("api_key_file", ""), own.get("api_key_var", ""))


def embedding_key(profile: EmbedProfile) -> str:
    """Khoá cho hồ sơ nhúng: máy chủ tự dựng ⇒ khoá ở mục ``key_ref`` (có thể không cần khoá); nhà cung cấp ngoài ⇒ khoá của ĐÚNG nhà đó (``key_for``), riêng nhà
    mặc định của bản cài (``embed_model``) còn đọc khoá chung với trợ lý như trước (0141). Không bao giờ đưa khoá của nhà này cho nhà khác."""
    if profile.compat:
        return key_named(profile.key_ref)
    if k := key_for(profile.model):
        return k
    same_as_default = settings.embed_model and profile.provider == provider_of(settings.embed_model)
    return resolve_api_key() if same_as_default else ""


_EMBEDDERS: dict[tuple[str, str, str], EmbeddingModel] = {}   # dựng một lần cho mỗi (model, địa chỉ, khoá) — giữ kết nối HTTP, không mở máy khách mới mỗi lô


def embedding_model_for(p: Principal, model: EmbeddingModel | None = None, profile: EmbedProfile | None = None) -> EmbeddingModel:
    """Model EMBEDDING (tìm theo nghĩa — 0141): cùng công tắc AI + hạn mức như ``model_for`` (đoạn tài liệu cũng là dữ liệu công ty gửi ra nhà
    cung cấp). Model theo HỒ SƠ NHÚNG của công ty (``profile``; không nêu ⇒ mặc định bản cài), KHÔNG theo hạng trợ lý AI: vector của model khác không so được
    với vector đã lưu. ``openai-compatible:<model>`` ⇒ máy chủ tự dựng ở ``profile.base_url`` (dữ liệu chỉ tới máy đó)."""
    _gate(p)
    if model is not None:
        return model
    from barista.agent.embed_profile import instance_profile

    prof = profile or instance_profile()
    key = embedding_key(prof)
    ck = (prof.model, prof.base_url, key)
    if (hit := _EMBEDDERS.get(ck)) is not None:
        return hit
    if prof.compat:
        from openai import AsyncOpenAI
        from pydantic_ai.embeddings.openai import OpenAIEmbeddingModel
        from pydantic_ai.providers.openai import OpenAIProvider

        # máy chủ cục bộ thường không đòi khoá; thử lại do ``agent/embed.py`` lo (một chính sách) ⇒ máy khách SDK không tự thử (mặc định 2 lần, lùi dần)
        client = AsyncOpenAI(base_url=prof.base_url, api_key=key or "none", max_retries=0)
        built: EmbeddingModel = OpenAIEmbeddingModel(prof.model.split(":", 1)[1], provider=OpenAIProvider(openai_client=client))
    else:
        # embedding KHÔNG có dự phòng: vector của model khác không so được với vector đã lưu (0141) — chỉ thử lại (agent/embed.py)
        factory: Callable[[str], Provider[Any]] = (
            (lambda name: infer_provider_class(name)(api_key=key)) if key else infer_provider  # type: ignore[call-arg]
        )
        built = infer_embedding_model(prof.model, provider_factory=factory)
    if len(_EMBEDDERS) >= 32:
        _EMBEDDERS.clear()
    _EMBEDDERS[ck] = built
    return built


def _provider_model(spec: str, key: str) -> Model:
    factory: Callable[[str], Provider[Any]] = (
        (lambda name: infer_provider_class(name)(api_key=key)) if key else infer_provider  # type: ignore[call-arg]
    )
    return infer_model(spec, provider_factory=factory)


NO_SDK_RETRIES = ModelSettings(max_retries=0)   # thử lại do reliability lo (một chính sách) — SDK OpenAI / Anthropic mặc định tự thử 2 lần


def build_model(spec: str | None = None, *, fallback: bool = False) -> Model:
    """``spec`` cho trước ⇒ đúng model đó (khoá chính), không dự phòng. Không ⇒ chuỗi theo cấu hình: một mục ⇒ ``Resilient`` (như trước + thử lại);
    nhiều mục ⇒ ``FallbackModel`` chỉ chuyển nhà khi ``reliability.falls_back``; cầu dao gắn mọi mục trừ mục cuối (luôn còn chỗ để thử).
    ``fallback``: công ty cho phép nhà cung cấp thứ hai (``Principal.ai_fallback``) — mặc định KHÔNG: dữ liệu công ty chỉ tới nhà chính."""
    if spec:
        return reliability.Resilient(_provider_model(spec, resolve_api_key()), spec=spec, extra=NO_SDK_RETRIES)
    built: list[tuple[str, Model]] = []
    for i, (s, k) in enumerate(chain_specs(fallback=fallback)):
        try:
            built.append((s, _provider_model(s, k)))
        except Exception:
            if i == 0:   # model CHÍNH hỏng cấu hình ⇒ báo như trước
                raise
            log.warning("bỏ nhà cung cấp dự phòng %s: không dựng được (thiếu khoá / sai chuỗi model)", s, exc_info=True)
    models = [reliability.Resilient(m, spec=s, extra=NO_SDK_RETRIES, breaker=reliability.breaker_for(s) if i < len(built) - 1 else None)
              for i, (s, m) in enumerate(built)]
    if len(models) == 1:
        return models[0]
    return FallbackModel(*models, fallback_on=reliability.falls_back)
