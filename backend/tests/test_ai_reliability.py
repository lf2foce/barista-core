"""Độ bền khi gọi model (agent/reliability.py, runbook 15): xếp loại lỗi nhà cung cấp, thử lại CHỈ lỗi tạm ở mức một lần gọi model (không chạy lại
công cụ), theo Retry-After, có trần; chuỗi dự phòng chỉ chuyển nhà khi lỗi tạm / sập / khoá hỏng, không khi nội dung bị chặn hay yêu cầu sai; cầu dao;
câu cho người theo loại + loại lỗi ghi vào ``core.ai_usage``. Không gọi model thật."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic_ai import Agent, ModelMessage, ModelResponse, TextPart, Tool, ToolCallPart
from pydantic_ai.exceptions import (
    ContentFilterError,
    ModelAuthenticationError,
    ModelContextWindowExceededError,
    ModelHTTPError,
    ModelOverloadedError,
    ModelQuotaExceededError,
    ModelRateLimitError,
    ModelRequestTimeout,
    ModelTimeoutError,
    UserError,
)
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import AgentInfo, FunctionModel

from barista.agent import chat, llm, reliability
from barista.agent.reliability import Policy, Resilient, backoff, classify
from barista.config import settings
from tests.conftest import Who, owner_val
from tests.test_assistant import call
from tests.test_autonomy_chat import Slow


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    got: list[float] = []

    async def fake(s: float) -> None:
        got.append(s)

    monkeypatch.setattr(reliability, "_sleep", fake)
    reliability.reset_breakers()
    return got


def flaky(*errors: BaseException, text: str = "xong") -> tuple[FunctionModel, dict[str, int]]:
    """Model giả: lần gọi thứ i ném ``errors[i]`` (None = trả lời), hết danh sách thì trả lời ``text``."""
    n = {"calls": 0}
    todo = list(errors)

    def fn(_m: list[ModelMessage], _i: AgentInfo) -> ModelResponse:
        n["calls"] += 1
        if todo:
            raise todo.pop(0)
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(fn, model_name=f"flaky-{text}"), n


# ── Xếp loại ──

@pytest.mark.parametrize(("err", "cls"), [
    (ModelRateLimitError("m", "429"), "rate_limit"),
    (ModelHTTPError(429, "m"), "rate_limit"),
    (ModelHTTPError(503, "m"), "unavailable"),
    (ModelOverloadedError("m", "quá tải"), "unavailable"),
    (ModelHTTPError(500, "m"), "unavailable"),
    (ModelTimeoutError("m", "im lặng"), "timeout"),
    (ModelRequestTimeout("m", 120), "timeout"),
    (reliability.tag(TimeoutError()), "timeout"),                  # TimeoutError trần chỉ là lỗi nhà cung cấp khi ra từ đường gọi model
    (httpx.ReadTimeout("chậm"), "timeout"),
    (httpx.ConnectError("rớt"), "unavailable"),
    (ModelAuthenticationError("m", "khoá sai"), "config"),
    (ModelHTTPError(401, "m"), "config"),
    (UserError("thiếu khoá"), "config"),
    (ModelQuotaExceededError("m", "hết tín dụng"), "quota"),
    (ContentFilterError("bị chặn"), "safety"),
    (ModelContextWindowExceededError("m", "quá dài"), "bad_request"),
    (ModelHTTPError(400, "m"), "bad_request"),
    (ValueError("lỗi code"), None),
    (TimeoutError("asyncio.timeout của bên gọi / công cụ"), None),   # không dấu ⇒ không phải lỗi nhà cung cấp
    (ConnectionError("công cụ rớt kết nối"), None),
])
def test_provider_errors_get_one_class(err: BaseException, cls: str | None) -> None:
    assert classify(err) == cls
    if cls is not None:
        assert reliability.message(cls) and "Lỗi hệ thống" not in reliability.message(cls)


def _wrapped(inner: BaseException) -> RuntimeError:
    try:
        try:
            raise inner
        except BaseException as e:
            raise RuntimeError("bọc ngoài") from e
    except RuntimeError as outer:
        return outer


def test_cause_chain_is_followed_only_for_errors_from_the_model_path() -> None:
    # lỗi của CÔNG CỤ do timeout / lỗi nhà cung cấp gây ra: không nhận nhầm là lỗi nhà cung cấp
    assert classify(_wrapped(TimeoutError("công cụ quá hạn"))) is None
    assert classify(_wrapped(ModelRateLimitError("m", "429"))) is None
    assert not chat.model_timed_out(_wrapped(httpx.ReadTimeout("công cụ gọi HTTP chậm")))
    # cùng lỗi nhưng ra từ đường gọi model (có dấu): lần theo nguyên nhân, nhận cả kiểu lỏng
    assert classify(reliability.tag(_wrapped(TimeoutError("lần gọi model quá hạn")))) == "timeout"
    assert classify(reliability.tag(_wrapped(ModelRateLimitError("m", "429")))) == "rate_limit"
    assert classify(ExceptionGroup("nhiều", [ValueError("x"), ModelHTTPError(503, "m")])) == "unavailable"
    assert classify(ExceptionGroup("công cụ", [TimeoutError("x"), ValueError("y")])) is None
    assert chat.SLOW_MODEL == reliability.message("timeout") and chat.model_timed_out(ModelTimeoutError("m", "x"))


async def test_a_tool_that_times_out_is_not_labelled_a_provider_error(sleeps: list[float]) -> None:
    async def slow_tool() -> str:
        raise TimeoutError("công cụ quá hạn")

    n = {"calls": 0}

    def fn(_m: list[ModelMessage], _i: AgentInfo) -> ModelResponse:
        n["calls"] += 1
        return ModelResponse(parts=[ToolCallPart("slow_tool", {}, tool_call_id="t1")])

    with pytest.raises(TimeoutError) as info:
        await Agent(Resilient(FunctionModel(fn)), tools=[Tool(slow_tool)]).run("làm")
    assert classify(info.value) is None and not chat.model_timed_out(info.value) and sleeps == [] and n["calls"] == 1


def test_backoff_grows_with_jitter_respects_retry_after_and_stops() -> None:
    pol = Policy(attempts=4, base_s=0.5, max_delay_s=8.0, retry_after_max_s=20.0, budget_s=30.0)
    e = ModelHTTPError(503, "m")
    lo = [backoff(pol, a, e, rng=lambda: 0.0) for a in range(3)]
    hi = [backoff(pol, a, e, rng=lambda: 1.0) for a in range(3)]
    assert lo == [0.375, 0.75, 1.5] and hi == [0.625, 1.25, 2.5]          # gấp đôi mỗi lần, nhiễu ±25%
    assert backoff(pol, 3, e) is None                                     # hết số lần
    assert backoff(pol, 0, ModelRateLimitError("m", "x", retry_after=5)) == 5    # chờ đúng lời nhà cung cấp
    assert backoff(pol, 0, ModelRateLimitError("m", "x", retry_after=60)) is None   # bảo chờ quá lâu ⇒ không chờ
    assert backoff(pol, 0, e, elapsed=29.9) is None                       # hết trần thời gian
    for bad in (ModelHTTPError(400, "m"), ContentFilterError("x"), ModelAuthenticationError("m", "x"), ModelQuotaExceededError("m", "x")):
        assert backoff(pol, 0, bad) is None                               # không thử lại lỗi không tạm


# ── Thử lại ở mức một lần gọi model ──

async def test_transient_errors_are_retried_then_answer(sleeps: list[float]) -> None:
    m, n = flaky(ModelHTTPError(503, "m"), ModelRateLimitError("m", "x", retry_after=2))
    r = await Agent(Resilient(m)).run("chào")
    assert r.output == "xong" and n["calls"] == 3 and len(sleeps) == 2 and sleeps[1] >= 2


@pytest.mark.parametrize("err", [ModelHTTPError(400, "m"), ContentFilterError("bị chặn"), ModelAuthenticationError("m", "khoá sai")])
async def test_non_transient_errors_are_not_retried(sleeps: list[float], err: BaseException) -> None:
    m, n = flaky(err)
    with pytest.raises(type(err)):
        await Agent(Resilient(m)).run("chào")
    assert n["calls"] == 1 and sleeps == []


async def test_attempts_are_bounded(sleeps: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_retry_attempts", 2)
    m, n = flaky(*[ModelHTTPError(503, "m")] * 5)
    with pytest.raises(ModelHTTPError):
        await Agent(Resilient(m)).run("chào")
    assert n["calls"] == 2 and len(sleeps) == 1


async def test_retry_never_reruns_a_tool_that_already_ran(sleeps: list[float]) -> None:
    """Lệnh ghi chạy GIỮA hai lần gọi model ⇒ lần gọi model sau hỏng rồi được thử lại không chạy lại lệnh."""
    effects: list[int] = []
    n = {"calls": 0}

    def write(x: int) -> str:
        effects.append(x)
        return "đã ghi"

    def fn(_m: list[ModelMessage], _i: AgentInfo) -> ModelResponse:
        n["calls"] += 1
        if n["calls"] == 1:
            return ModelResponse(parts=[ToolCallPart("write", {"x": 1}, tool_call_id="t1")])
        if n["calls"] == 2:
            raise ModelHTTPError(503, "m")
        return ModelResponse(parts=[TextPart("xong")])

    r = await Agent(Resilient(FunctionModel(fn)), tools=[Tool(write)]).run("ghi")
    assert r.output == "xong" and effects == [1] and n["calls"] == 3 and len(sleeps) == 1


async def test_stream_is_retried_only_before_its_first_piece(sleeps: list[float]) -> None:
    n = {"calls": 0}
    mid_stream = {"on": False}

    async def stream(_m: list[ModelMessage], _i: AgentInfo) -> AsyncIterator[str]:
        n["calls"] += 1
        if n["calls"] == 1 and not mid_stream["on"]:
            raise ModelOverloadedError("m", "quá tải trước mẩu đầu")
        yield "Chào "
        if mid_stream["on"]:
            raise ModelOverloadedError("m", "đứt giữa dòng")
        yield "anh."

    async def sink(_ctx: Any, events: Any) -> None:
        async for _ in events:
            pass

    model = Resilient(FunctionModel(stream_function=stream))
    r = await Agent(model).run("chào", event_stream_handler=sink)
    assert r.output == "Chào anh." and n["calls"] == 2 and len(sleeps) == 1
    n["calls"], mid_stream["on"] = 0, True
    with pytest.raises(ModelOverloadedError):   # chữ đã tới người dùng ⇒ không thử lại (không lặp chữ)
        await Agent(model).run("chào", event_stream_handler=sink)
    assert n["calls"] == 1


# ── Dự phòng + cầu dao ──

def chain(primary: FunctionModel, backup: FunctionModel, breaker: reliability.Breaker | None = None) -> FallbackModel:
    return FallbackModel(Resilient(primary, spec="a:p", breaker=breaker), Resilient(backup, spec="b:q"), fallback_on=reliability.falls_back)


async def test_fallback_only_for_provider_down_not_for_refusals(sleeps: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_retry_attempts", 1)
    for err in (ModelHTTPError(503, "m"), ModelRateLimitError("m", "x"), ModelAuthenticationError("m", "khoá hỏng"), ModelQuotaExceededError("m", "x")):
        p, pn = flaky(err, text="chính")
        b, bn = flaky(text="dự phòng")
        assert (await Agent(chain(p, b)).run("chào")).output == "dự phòng" and pn["calls"] == 1 and bn["calls"] == 1
    for err in (ModelHTTPError(400, "m"), ContentFilterError("bị chặn")):
        p, pn = flaky(err, text="chính")
        b, bn = flaky(text="dự phòng")
        with pytest.raises(type(err)):
            await Agent(chain(p, b)).run("chào")
        assert bn["calls"] == 0   # nhà khác không được hỏi: nội dung bị chặn / yêu cầu sai không phải "nhà cung cấp sập"


async def test_breaker_skips_a_failing_provider_for_a_while(sleeps: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_retry_attempts", 1)
    now = [1000.0]
    monkeypatch.setattr(reliability, "_clock", lambda: now[0])
    br = reliability.Breaker(failures=2, window_s=60, cooldown_s=30)
    p, pn = flaky(*[ModelHTTPError(503, "m")] * 10, text="chính")
    b, bn = flaky(text="dự phòng")
    model = chain(p, b, br)
    for _ in range(2):
        assert (await Agent(model).run("chào")).output == "dự phòng"
    assert pn["calls"] == 2
    await Agent(model).run("chào")
    assert pn["calls"] == 2 and bn["calls"] == 3      # cầu dao mở: không gửi gì cho nhà đang hỏng
    now[0] += 31
    await Agent(model).run("chào")
    assert pn["calls"] == 3                            # hết giờ nghỉ ⇒ thử lại một lần, hỏng ⇒ mở lại ngay
    await Agent(model).run("chào")
    assert pn["calls"] == 3
    now[0] += 31
    p2, _ = flaky(text="chính")                        # nhà chính hồi ⇒ dùng lại, cầu dao đóng
    model = chain(p2, b, br)
    assert (await Agent(model).run("chào")).output == "chính" and not br.open_until and not br.recent


def test_only_provider_errors_count_for_the_breaker() -> None:
    for err in (ModelHTTPError(503, "m"), ModelRateLimitError("m", "x"), ModelTimeoutError("m", "x"), ModelRequestTimeout("m", 60),
                httpx.ReadTimeout("mạng"), ModelAuthenticationError("m", "x"), ModelQuotaExceededError("m", "x")):
        assert reliability.provider_failed(err), err
    for err in (UserError("thiếu khoá phía code"), TimeoutError("asyncio.timeout cục bộ"), reliability.tag(TimeoutError("lần gọi bị huỷ cục bộ")),
                ConnectionError("x"), ValueError("x"), ContentFilterError("x"), ModelHTTPError(400, "m"), reliability.ProviderSkipped("m", "x")):
        assert not reliability.provider_failed(err), err


async def test_non_provider_failures_never_open_the_breaker(sleeps: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_retry_attempts", 1)
    br = reliability.Breaker(failures=2, window_s=60, cooldown_s=30)
    backup, _ = flaky(text="dự phòng")
    # lỗi cấu hình phía code, timeout cục bộ của lần gọi (asyncio), lỗi từ phía công cụ / bên tiêu thụ ném vào luồng: không cái nào là "nhà chính hỏng"
    for err in (UserError("thiếu khoá"), TimeoutError("asyncio.timeout cục bộ")):
        primary, _ = flaky(*[err] * 5, text="chính")
        for _ in range(4):
            try:
                await Agent(FallbackModel(Resilient(primary, spec="a:p", breaker=br), Resilient(backup, spec="b:q"), fallback_on=reliability.falls_back)).run("chào")
            except Exception:   # UserError không chuyển dự phòng ⇒ lỗi đi ra; TimeoutError trần ra từ model ⇒ chuyển dự phòng
                pass
        assert br.allow() and not br.recent, err

    async def tool_side_failure(_ctx: Any, events: Any) -> None:
        async for _ in events:
            raise TimeoutError("công cụ / bên tiêu thụ quá hạn khi luồng đang mở")

    async def stream(_m: list[ModelMessage], _i: AgentInfo) -> AsyncIterator[str]:
        yield "Chào "
        yield "anh."

    model = Resilient(FunctionModel(stream_function=stream), spec="a:p", breaker=br)
    for _ in range(4):
        with pytest.raises(TimeoutError):
            await Agent(model).run("chào", event_stream_handler=tool_side_failure)
    assert br.allow() and not br.recent
    # nhà cung cấp THẬT SỰ trả lỗi ⇒ vẫn mở cầu dao
    down, _ = flaky(*[ModelHTTPError(503, "m")] * 5)
    for _ in range(2):
        assert (await Agent(FallbackModel(Resilient(down, spec="a:p", breaker=br), Resilient(backup, spec="b:q"),
                                          fallback_on=reliability.falls_back)).run("chào")).output == "dự phòng"
    assert not br.allow()


def test_chain_is_built_from_settings_with_own_keys(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    keyfile = tmp_path / "other.env"
    keyfile.write_text("ANTHROPIC_API_KEY=sk-ant-khoa-rieng\n")
    monkeypatch.setattr(settings, "llm_model", "google:gemini-3.8-flash")
    monkeypatch.setattr(settings, "llm_api_key", "khoa-chinh")
    monkeypatch.setattr(settings, "llm_fallback_models", [])
    one = llm.build_model(fallback=True)
    assert isinstance(one, Resilient) and one.breaker is None and one.extra == {"max_retries": 0}   # không dự phòng ⇒ như trước + thử lại
    monkeypatch.setattr(settings, "llm_fallback_models", [
        "google:gemini-3.8-flash-lite",
        {"model": "anthropic:claude-sonnet-5", "api_key_file": str(keyfile), "api_key_var": "ANTHROPIC_API_KEY"},
        "openai:gpt-5", "google:gemini-3.8-flash"])
    assert llm.chain_specs() == [("google:gemini-3.8-flash", "khoa-chinh"), ("google:gemini-3.8-flash-lite", "khoa-chinh"),
                                 ("anthropic:claude-sonnet-5", "sk-ant-khoa-rieng"), ("openai:gpt-5", "")]   # khác nhà, không khai khoá ⇒ không mượn khoá chính
    monkeypatch.setattr(settings, "llm_fallback_models", settings.llm_fallback_models[:2])
    assert isinstance(llm.build_model(), Resilient)   # công ty chưa cho nhà thứ hai ⇒ chỉ nhà chính, dù bản cài khai chuỗi
    built = llm.build_model(fallback=True)
    assert isinstance(built, FallbackModel) and len(built.models) == 3
    members = [m for m in built.models if isinstance(m, Resilient)]
    assert [m.spec for m in members] == [s for s, _ in llm.chain_specs()]
    assert [m.breaker is not None for m in members] == [True, True, False]   # mục cuối luôn được thử
    # dự phòng không dựng được (khác nhà, không khoá, không biến môi trường) ⇒ bỏ mục đó, model chính vẫn chạy như trước
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(settings, "llm_fallback_models", ["openai:gpt-5"])
    alone = llm.build_model(fallback=True)
    assert isinstance(alone, Resilient) and alone.spec == "google:gemini-3.8-flash"


# ── Bên gọi: câu cho người + loại lỗi vào lượt đo ──

async def test_chat_says_what_went_wrong_and_meters_the_class(who: Who, sleeps: list[float]) -> None:
    sale1 = await who("sale1")
    events: list[dict[str, Any]] = []

    async def emit(ev: dict[str, Any]) -> None:
        events.append(ev)

    out = await chat.chat_turn(sale1, "Khách của tôi", emit=emit, model=Slow([[call("crm.leads")], ModelQuotaExceededError("m", "hết tín dụng")]))
    assert out["text"] == reliability.message("quota") and not any(e["type"] == "error" for e in events)
    row = await owner_val("SELECT row_to_json(u) FROM core.ai_usage u WHERE user_id = %s ORDER BY id DESC LIMIT 1", (sale1.user_id,))
    assert row["error_class"] == "quota" and not row["ok"] and row["tools"] == ["crm.leads"]


async def test_chat_retry_keeps_the_tool_step_single(who: Who, sleeps: list[float]) -> None:
    sale1 = await who("sale1")
    events: list[dict[str, Any]] = []

    async def emit(ev: dict[str, Any]) -> None:
        events.append(ev)

    model = Resilient(Slow([[call("crm.leads")], ModelHTTPError(503, "m"), "Anh có mấy khách."]))
    out = await chat.chat_turn(sale1, "Khách của tôi", emit=emit, model=model)
    assert out["text"].startswith("Anh có mấy khách") and [s["tool"] for s in out["steps"]] == ["crm.leads"] and len(sleeps) == 1
    row = await owner_val("SELECT row_to_json(u) FROM core.ai_usage u WHERE user_id = %s ORDER BY id DESC LIMIT 1", (sale1.user_id,))
    assert row["ok"] and row["error_class"] is None


# ── Công tắc công ty cho nhà cung cấp THỨ HAI (core.ai_allow_fallback_vendor, mặc định tắt) ──

KEY = "core.ai_allow_fallback_vendor"


def streaming(name: str, *, error: BaseException | None = None, text: str = "Chào anh.") -> tuple[FunctionModel, dict[str, int]]:
    n = {"calls": 0}

    async def stream(_m: list[ModelMessage], _i: AgentInfo) -> AsyncIterator[str]:
        n["calls"] += 1
        if error is not None:
            raise error
        yield text

    return FunctionModel(stream_function=stream, model_name=name), n


@pytest.fixture
def vendors(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> dict[str, tuple[FunctionModel, dict[str, int]]]:
    """Chuỗi cấu hình: chính (luôn 503) rồi dự phòng (trả lời). Không gọi mạng — ``_provider_model`` trả model giả theo chuỗi."""
    made = {"google:main": streaming("main-down", error=ModelHTTPError(503, "m")), "openai:backup": streaming("backup-ok")}
    monkeypatch.setattr(settings, "llm_model", "google:main")
    monkeypatch.setattr(settings, "llm_fallback_models", ["openai:backup"])
    monkeypatch.setattr(settings, "llm_retry_attempts", 1)
    monkeypatch.setattr(llm, "_provider_model", lambda spec, _key: made[spec][0])
    return made


def test_the_company_switch_is_declared_off_by_default_and_the_chain_obeys_it(vendors: dict[str, Any]) -> None:
    from barista.kernel import company_settings

    s = company_settings.SETTINGS[KEY]
    assert s.default is False and s.module == "core"
    assert isinstance(llm.build_model(), Resilient)                                  # công ty chưa bật ⇒ chỉ nhà chính
    assert isinstance(llm.build_model(fallback=True), FallbackModel)


async def test_company_switch_off_stops_the_chain_at_the_primary_and_on_reaches_the_backup(who: Who, vendors: dict[str, Any]) -> None:
    from barista.kernel import company_settings
    from tests.conftest import ok

    admin, sale1 = await who("admin"), await who("sale1")
    assert sale1.ai_fallback is False

    async def ask(p: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        events: list[dict[str, Any]] = []

        async def emit(ev: dict[str, Any]) -> None:
            events.append(ev)

        return await chat.chat_turn(p, "Chào", emit=emit), events

    # TẮT (mặc định): người dùng thấy câu lỗi thường, nhà dự phòng không nhận gì
    out, _ = await ask(sale1)
    assert out["text"] == reliability.message("unavailable") and vendors["openai:backup"][1]["calls"] == 0 and vendors["google:main"][1]["calls"] == 1
    row = await owner_val("SELECT row_to_json(u) FROM core.ai_usage u WHERE user_id = %s ORDER BY id DESC LIMIT 1", (sale1.user_id,))
    assert row["error_class"] == "unavailable" and not row["ok"]
    # quản trị BẬT (cài đặt công ty — dữ liệu, có nhật ký): lượt sau tới nhà dự phòng
    r = await ok(admin, "admin.set_setting", key=KEY, enabled=True)
    assert r["enabled"] is True and company_settings.SETTINGS[KEY].label in r["summary"]
    on = await who("sale1")
    assert on.ai_fallback is True
    out, _ = await ask(on)
    assert out["text"].startswith("Chào anh") and vendors["openai:backup"][1]["calls"] == 1
    row = await owner_val("SELECT row_to_json(u) FROM core.ai_usage u WHERE user_id = %s ORDER BY id DESC LIMIT 1", (sale1.user_id,))
    assert row["ok"] and row["model"] == "backup-ok"      # ghi model đã trả lời (để biết dữ liệu đã tới nhà nào)
    # TẮT lại: có hiệu lực từ lượt kế
    await ok(admin, "admin.set_setting", key=KEY, enabled=False)
    assert (await who("sale1")).ai_fallback is False


async def test_company_switch_is_per_company(who: Who, vendors: dict[str, Any]) -> None:
    from tests.conftest import ok

    admin, other = await who("admin"), await who("admin", other=True)
    await ok(admin, "admin.set_setting", key=KEY, enabled=True)
    try:
        assert (await who("sale1")).ai_fallback is True
        assert (await who("admin", other=True)).ai_fallback is False and other.ai_fallback is False   # công ty khác chưa bật
    finally:
        await ok(admin, "admin.set_setting", key=KEY, enabled=False)
