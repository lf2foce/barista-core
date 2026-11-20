"""Hạng trợ lý AI theo vai (ADR 0193) — CHÍNH SÁCH CỦA NHÀ VẬN HÀNH: hạng là dữ liệu (mặc định đi kèm gói, cài một lần, nhà vận hành sửa qua quyền hệ
thống), khách (kể cả quản trị công ty) không đọc / sửa được hạng, model, hạn mức, báo cáo token và chỉ thấy số lượt; hạn mức TOKEN theo người (không
đếm token đọc cache) chặn đúng người đó, câu báo thân thiện trong khung chat, báo nhà vận hành một lần, mở lại sang tháng mới; model theo hạng; giọng nói
chỉ cho hạng cho phép và đi qua cùng cửa; báo cáo token theo người × tháng. Không gọi model / mạng thật: Gemini Live thay bằng đầu dò."""

import logging
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from google.genai import types
from openpyxl import load_workbook
from pydantic_ai import ModelResponse
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.usage import RequestUsage, RunUsage

from barista import ai_policy, seed, workspaces
from barista.agent import chat, llm, reliability, usage, voice
from barista.config import settings
from barista.db import db
from barista.kernel import alerts, auth, executor
from barista.kernel.principal import system_principal
from tests.conftest import owner_val
from tests.test_assistant import Scripted, call
from tests.test_voice import FakeLive, FakeWS, ask

PEOPLE = [("qt", "Quản trị", "admin", "QT", True), ("gd", "Giám đốc", "director", "HQ", True),
          ("sale1", "Sale Một", "sale", "KD-A", False), ("sale2", "Sale Hai", "sale", "KD-A", False)]
OP = {"by": "Nhà vận hành (test)", "why": "kiểm thử"}


async def _company() -> tuple[Any, str]:
    d = f"{uuid.uuid4().hex[:6]}.tier.test"
    tid = await seed.create_tenant("Công ty hạng AI (test)", d, people=PEOPLE, packs=("crm",))
    return tid, d


async def _use(p: Any, *, fresh: int = 0, cached: int = 0, written: int = 0, out: int = 0, usd: str | None = None) -> None:
    """Một lượt đã chạy của ``p``: token vào (``input_tokens`` GỒM cache đọc + ghi — quy ước pydantic-ai), ra; chi phí giả nếu cần."""
    with pytest.MonkeyPatch.context() as mp:   # chỉ hoàn lại chỗ vá của lượt này (không đụng các fixture đã vá)
        if usd is not None:
            mp.setattr(usage, "cost_usd", lambda _m: Decimal(usd))
        await usage.record(p, surface="chat", category="chat", modules=[], model="test-model",
                           usage=RunUsage(requests=1, input_tokens=fresh + cached + written, cache_read_tokens=cached, cache_write_tokens=written,
                                          output_tokens=out))


async def _spend(_mp: pytest.MonkeyPatch, p: Any, usd: str) -> None:
    await _use(p, usd=usd)


@pytest.fixture
def notices(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Kênh của nhà vận hành (alerts.notice) thay bằng danh sách — không gửi Telegram."""
    got: list[str] = []
    monkeypatch.setattr(alerts, "notice", lambda _t, _k, text: got.append(text))
    return got


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Bản cài: model chính Google (khoá chính), không khoá nhà nào khác (kể cả biến môi trường của máy chạy test)."""
    for var in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(settings, "llm_model", "google:gemini-3.8-flash")
    monkeypatch.setattr(settings, "llm_api_key", "khoa-chinh")
    monkeypatch.setattr(settings, "llm_fallback_models", [])
    monkeypatch.setattr(settings, "llm_keys", {})
    monkeypatch.setattr(settings, "llm_replay_dir", "")
    return monkeypatch


TIERS = "SELECT json_object_agg(key, json_build_array(model, person_monthly_tokens, person_monthly_usd, voice, is_default)) FROM core.ai_tiers WHERE tenant_id = %s"


async def test_defaults_are_installed_once_and_operator_edits_are_kept() -> None:
    tid, d = await _company()
    assert await owner_val(TIERS, (tid,)) == {"basic": ["deepseek:deepseek-flash", 5_000_000, None, False, True],
                                             "manager": [None, None, None, False, False], "executive": [None, None, None, True, False]}
    roles = await owner_val("SELECT json_object_agg(role, tier) FROM core.ai_role_tiers WHERE tenant_id = %s", (tid,))
    assert {k: roles[k] for k in ("admin", "director", "staff", "sale", "team_lead", "coordinator", "sales_admin")} == {
        "admin": "manager", "director": "executive", "staff": "basic", "sale": "basic", "team_lead": "manager", "coordinator": "basic",
        "sales_admin": "manager"}
    sale, gd = await seed.who(tid, "sale1", d), await seed.who(tid, "gd", d)
    assert (sale.ai_tier, sale.ai_model, sale.ai_voice) == ("basic", "deepseek:deepseek-flash", False)
    assert (gd.ai_tier, gd.ai_model, gd.ai_voice) == ("executive", "", True)
    # nhà vận hành sửa (quyền hệ thống, có người + lý do) ⇒ cài lại (nâng cấp bản cài) không ghi đè, không thêm dòng
    op = system_principal(tid)
    r = await executor.run(op, "core.save_ai_tier", {"key": "basic", "label": "Nhân viên", "model": "", "person_monthly_tokens": 9_000_000,
                                                     "is_default": True, **OP})
    assert r.status == "ok", r.error
    assert (await executor.run(op, "core.set_role_ai_tier", {"role": "sale", "tier": "manager", **OP})).status == "ok"
    before = await owner_val("SELECT count(*) FROM core.pack_installs WHERE tenant_id = %s", (tid,))
    r = await executor.run(op, "admin.install_defaults", {"packs": ["base", "crm"]})
    assert r.status == "ok", r.error
    assert await owner_val("SELECT count(*) FROM core.pack_installs WHERE tenant_id = %s", (tid,)) == before
    assert await owner_val("SELECT json_build_array(label, model, person_monthly_tokens) FROM core.ai_tiers WHERE tenant_id = %s AND key = 'basic'",
                           (tid,)) == ["Nhân viên", None, 9_000_000]
    assert await owner_val("SELECT tier FROM core.ai_role_tiers WHERE tenant_id = %s AND role = 'sale'", (tid,)) == "manager"
    # công ty CÓ TRƯỚC (chưa có hạng nào — như bản cài trước 081): cài lại thì nhận đủ bộ mặc định
    await owner_val("DELETE FROM core.ai_tiers WHERE tenant_id = %s RETURNING 1", (tid,))
    await owner_val("DELETE FROM core.pack_installs WHERE tenant_id = %s AND key LIKE 'core.%%ai_tier%%' RETURNING 1", (tid,))
    assert (await seed.who(tid, "sale1", d)).ai_voice is False                       # chưa có hạng ⇒ không giọng nói, model mặc định
    assert (await executor.run(op, "admin.install_defaults", {"packs": ["base", "crm"]})).status == "ok"
    assert await owner_val("SELECT count(*) FROM core.ai_tiers WHERE tenant_id = %s", (tid,)) == 3
    assert await owner_val("SELECT tier FROM core.ai_role_tiers WHERE tenant_id = %s AND role = 'director'", (tid,)) == "executive"


async def _as_081(tid: Any, *, usd: str = "2.00", model: str = "deepseek:deepseek-chat") -> None:
    """Đưa hạng "basic" của công ty về đúng trạng thái bản 081 (df66d796) đã cài: model + trần USD theo người, chưa có hạn mức token; nhật ký cài ghi
    khoá lệnh cũ (``core.save_ai_tier:…``) ⇒ lệnh cài mặc định mới chưa từng chạy ở công ty này."""
    await owner_val("""UPDATE core.ai_tiers SET model = %s, person_monthly_usd = %s, person_monthly_tokens = NULL
                       WHERE tenant_id = %s AND key = 'basic' RETURNING 1""", (model, Decimal(usd), tid))
    await owner_val("""UPDATE core.pack_installs SET key = replace(replace(key, 'core.default_role_ai_tier', 'core.set_role_ai_tier'),
                                                                   'core.default_ai_tier', 'core.save_ai_tier')
                       WHERE tenant_id = %s AND key LIKE 'core.default_%%' RETURNING 1""", (tid,))


async def test_upgrade_from_081_moves_the_shipped_basic_tier_to_tokens_and_keeps_operator_values() -> None:
    shipped, _ = await _company()
    edited, _ = await _company()
    await _as_081(shipped)
    await _as_081(edited, usd="5.00", model="google:gemini-3.8-flash")               # nhà vận hành / công ty đã đổi ở bản 081
    for tid in (shipped, edited):
        r = await executor.run(system_principal(tid), "admin.install_defaults", {"packs": ["base", "crm"]})   # seed --upgrade
        assert r.status == "ok", r.error
    assert (await owner_val(TIERS, (shipped,)))["basic"] == ["deepseek:deepseek-flash", 5_000_000, None, False, True]
    assert (await owner_val(TIERS, (edited,)))["basic"] == ["google:gemini-3.8-flash", None, 5.0, False, True]   # giữ nguyên
    # --upgrade chạy lại: không đổi gì, không thêm dòng nhật ký cài
    snap = [await owner_val(TIERS, (t,)) for t in (shipped, edited)]
    logs = await owner_val("SELECT count(*) FROM core.pack_installs WHERE tenant_id = ANY(%s)", ([shipped, edited],))
    for tid in (shipped, edited):
        assert (await executor.run(system_principal(tid), "admin.install_defaults", {"packs": ["base", "crm"]})).status == "ok"
    assert [await owner_val(TIERS, (t,)) for t in (shipped, edited)] == snap
    assert await owner_val("SELECT count(*) FROM core.pack_installs WHERE tenant_id = ANY(%s)", ([shipped, edited],)) == logs


async def test_an_admin_enabling_a_pack_still_gets_tier_defaults_without_seeing_them() -> None:
    """Quản trị công ty bật gói ⇒ mặc định hạng của vai mới vẫn cài (thao tác nội bộ, quyền hệ thống) — không lỗi, không hiện trong "bật sẽ thêm gì"."""
    tid, d = await _company()
    qt = await seed.who(tid, "qt", d)
    await owner_val("DELETE FROM core.ai_role_tiers WHERE tenant_id = %s AND role = 'sale' RETURNING 1", (tid,))
    await owner_val("DELETE FROM core.pack_installs WHERE tenant_id = %s AND key = 'core.default_role_ai_tier:sale' RETURNING 1", (tid,))
    info = (await executor.query(qt, "admin.pack_info", {"pack": "crm"})).output
    assert "ai_tier" not in str(info) and "Hạng trợ lý" not in str(info)
    r = await executor.run(qt, "admin.set_pack", {"pack": "crm", "enabled": True})   # gói đã bật ⇒ chỉ cài phần mặc định còn thiếu
    assert r.status == "ok" and not r.output["installed"]["skipped"], r.output
    assert await owner_val("SELECT tier FROM core.ai_role_tiers WHERE tenant_id = %s AND role = 'sale'", (tid,)) == "basic"


async def test_the_tier_picks_the_model_and_a_provider_without_a_key_falls_back(keys: pytest.MonkeyPatch,
                                                                              caplog: pytest.LogCaptureFixture) -> None:
    tid, d = await _company()
    sale, gd = await seed.who(tid, "sale1", d), await seed.who(tid, "gd", d)
    with caplog.at_level(logging.WARNING, "barista.agent.llm"):
        m = llm.model_for(sale)                                                       # deepseek chưa có khoá ⇒ model mặc định + một dòng log
    assert isinstance(m, reliability.Resilient) and m.spec == "google:gemini-3.8-flash"
    assert "deepseek:deepseek-flash" in caplog.text and "khoa-chinh" not in caplog.text   # log nêu model, không bao giờ nêu khoá
    keys.setattr(settings, "llm_keys", {"deepseek": "sk-deepseek-test"})
    m = llm.model_for(sale)
    assert isinstance(m, reliability.Resilient) and m.spec == "deepseek:deepseek-flash"
    assert llm.key_for("deepseek:deepseek-flash") == "sk-deepseek-test" and llm.key_for("openai:gpt-5.5") == ""   # khoá không đi sang nhà khác
    assert llm.model_for(sale, media=True).spec == "google:gemini-3.8-flash"        # type: ignore[attr-defined]  # ảnh / âm thanh: model mặc định
    assert llm.model_for(gd).spec == "google:gemini-3.8-flash"                       # type: ignore[attr-defined]  # hạng không đặt model ⇒ mặc định
    keys.setattr(settings, "llm_fallback_models", ["google:gemini-3.8-flash-lite"])
    chain = llm.model_for(llm_replace(sale, ai_fallback=True))
    assert isinstance(chain, FallbackModel) and [x.spec for x in chain.models] == [   # type: ignore[attr-defined]
        "deepseek:deepseek-flash", "google:gemini-3.8-flash", "google:gemini-3.8-flash-lite"]


def llm_replace(p: Any, **kw: Any) -> Any:
    from dataclasses import replace

    return replace(p, **kw)


def test_quota_counts_every_token_except_cache_reads() -> None:
    """Quy ước pydantic-ai: input_tokens GỒM cache đọc + cache ghi ⇒ token tính hạn mức = (vào − cache đọc) + ra (cache ghi đã nằm trong phần còn lại)."""
    u = RunUsage(input_tokens=1000, cache_read_tokens=600, cache_write_tokens=100, output_tokens=50)
    assert usage.quota_tokens(u) == 450                                               # 300 vào mới + 100 cache ghi + 50 ra


def test_the_default_basic_model_is_priced_at_deepseek_peak_rates() -> None:
    """Model mặc định của hạng "Cơ bản" phải có giá (không thì cost_usd = NULL ⇒ trần cả công ty mù): bảng giá của thư viện đang khoá (genai-prices
    0.2.0) có deepseek-flash 0,30 / 1,20 / 0,006 USD mỗi triệu token vào / ra / cache đọc — đúng giá CAO ĐIỂM trên trang giá DeepSeek.
    Giá DeepSeek đổi theo GIỜ (thấp điểm rẻ một nửa) và thư viện tính theo giờ của lượt ⇒ test ghim giờ, không dựa vào lúc chạy (từng đỏ khi chạy chiều tối)."""
    peak, off = datetime(2026, 11, 12, 2, tzinfo=UTC), datetime(2026, 11, 12, 20, tzinfo=UTC)

    def cost(tin: int = 0, tout: int = 0, cached: int = 0, at: datetime = peak) -> Decimal | None:
        return usage.cost_usd([ModelResponse(parts=[], model_name="deepseek-flash", provider_name="deepseek", timestamp=at,
                                             usage=RequestUsage(input_tokens=tin, output_tokens=tout, cache_read_tokens=cached))])

    assert cost(tin=1_000_000) == Decimal("0.3") and cost(tout=1_000_000) == Decimal("1.2")
    assert cost(tin=1_000_000, cached=1_000_000) == Decimal("0.006")
    assert cost(tin=1_000_000, at=off) == Decimal("0.15")   # thấp điểm: vẫn có giá (không NULL), rẻ hơn


async def test_the_person_token_quota_blocks_only_that_person_tells_them_kindly_alerts_the_operator_once_and_reopens(
        monkeypatch: pytest.MonkeyPatch, notices: list[str]) -> None:
    tid, d = await _company()
    s1 = await seed.who(tid, "sale1", d)
    await _use(s1, fresh=1_000_000, cached=3_000_000, out=50_000)                     # cache đọc không tính: 1,05 triệu
    assert await owner_val("SELECT tokens FROM core.ai_person_spend WHERE user_id = %s", (s1.user_id,)) == 1_050_000
    await _use(s1, fresh=3_900_000, written=40_000)                                   # 4.990.000 — còn dưới 5 triệu
    assert (await seed.who(tid, "sale1", d)).ai_block == "" and not notices
    await _use(s1, fresh=5_000, out=6_000)                                            # 5.001.000 ≥ 5 triệu
    s1, s2, gd = await seed.who(tid, "sale1", d), await seed.who(tid, "sale2", d), await seed.who(tid, "gd", d)
    assert s1.ai_block == auth.PERSON_OUT and "lượt trợ lý" in s1.ai_block
    assert not any(w in s1.ai_block.lower() for w in ("token", "usd", "deepseek", "gemini", "model", "nhà cung cấp", "hạng"))
    with pytest.raises(llm.AIDisabled, match="hết lượt trợ lý"):
        llm.model_for(s1, Scripted(["x"]))
    assert s2.ai_block == "" and gd.ai_block == "" and llm.model_for(s2, Scripted(["x"]))   # người khác vẫn dùng
    # trong khung chat: sự kiện lỗi web / app đã vẽ (không đổi hợp đồng), không gọi model
    events: list[dict[str, Any]] = []

    async def emit(e: dict[str, Any]) -> None:
        events.append(e)

    model = Scripted(["không được gọi"])
    await chat.chat_turn(s1, "Khách nào cần gọi hôm nay?", emit=emit, model=model)
    assert {"type": "error", "message": auth.PERSON_OUT} in events and model.turns == ["không được gọi"]
    # báo NHÀ VẬN HÀNH đúng một lần (kênh của nhà vận hành), khách không nhận thông báo nào
    assert len(notices) == 1 and str(s1.user_id) in notices[0] and "5.001.000" in notices[0] and "sale1@" not in notices[0]
    await _use(s1, fresh=10_000)                                                      # lượt đang chạy vẫn ghi; không báo lại
    assert len(notices) == 1
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE tenant_id = %s AND kind = 'ai_budget'", (tid,)) == 0
    assert await owner_val("SELECT stopped_at IS NOT NULL FROM core.ai_person_spend WHERE user_id = %s", (s1.user_id,)) is True
    # nhà vận hành nâng hạn mức ⇒ dùng lại ngay lượt sau
    r = await executor.run(system_principal(tid), "core.save_ai_tier", {"key": "basic", "label": "Cơ bản", "model": "deepseek:deepseek-flash",
                                                                        "person_monthly_tokens": 20_000_000, "is_default": True, **OP})
    assert r.status == "ok" and (await seed.who(tid, "sale1", d)).ai_block == ""
    assert (await executor.run(system_principal(tid), "core.save_ai_tier", {"key": "basic", "label": "Cơ bản", "person_monthly_tokens": 5_000_000,
                                                                            "is_default": True, **OP})).status == "ok"
    assert (await seed.who(tid, "sale1", d)).ai_block == auth.PERSON_OUT
    # sang tháng mới ⇒ đếm lại từ 0, mở lại
    nxt = datetime.now(UTC).date().replace(day=1)
    nxt = nxt.replace(year=nxt.year + 1, month=1) if nxt.month == 12 else nxt.replace(month=nxt.month + 1)
    monkeypatch.setattr(settings, "demo_today", nxt.isoformat())
    assert (await seed.who(tid, "sale1", d)).ai_block == ""
    # trần CẢ CÔNG TY (USD, nhà vận hành đặt) vẫn chặn mọi người, kể cả hạng không giới hạn token — câu báo không nêu số tiền
    monkeypatch.setattr(settings, "demo_today", "")
    await owner_val("UPDATE core.tenant_profile SET ai_monthly_usd = 1 WHERE tenant_id = %s RETURNING 1", (tid,))
    await _spend(monkeypatch, gd, "1.5")
    for who in ("gd", "qt", "sale2"):
        block = (await seed.who(tid, who, d)).ai_block
        assert "Công ty đã dùng hết" in block and "USD" not in block


async def test_person_spend_rows_are_private_to_the_person_and_company_wide_viewers() -> None:
    tid, d = await _company()
    s1, s2 = await seed.who(tid, "sale1", d), await seed.who(tid, "sale2", d)

    async with db.tenant_tx(s1) as c:
        await c.execute("INSERT INTO core.ai_person_spend (user_id, month, usd) VALUES (%s, date_trunc('month', now())::date, 1)", (s1.user_id,))
    async with db.tenant_tx(s2, read_only=True) as c:
        assert await (await c.execute("SELECT count(*) AS n FROM core.ai_person_spend")).fetchone() == {"n": 0}
    with pytest.raises(Exception, match="row-level security"):
        async with db.tenant_tx(s2) as c:   # ghi hộ người khác bị RLS chặn
            await c.execute("INSERT INTO core.ai_person_spend (user_id, month, usd) VALUES (%s, date_trunc('month', now())::date, 1)", (s1.user_id,))


POLICY_QUERIES = ("core.ai_tiers", "core.ai_role_tiers", "core.ai_tier_options", "core.ai_token_report")


async def test_the_customer_admin_cannot_see_or_edit_the_ai_policy_and_sees_only_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    tid, d = await _company()
    qt, sale, gd = await seed.who(tid, "qt", d), await seed.who(tid, "sale1", d), await seed.who(tid, "gd", d)
    tier = {"key": "basic", "label": "Cơ bản", "person_monthly_tokens": 50_000_000, "voice": True, "is_default": True, "sort": 10, **OP}
    for p in (qt, gd, sale, qt.as_agent()):
        assert (await executor.run(p, "core.save_ai_tier", tier)).status == "denied"
        assert (await executor.run(p, "core.set_role_ai_tier", {"role": "sale", "tier": "executive", **OP})).status == "denied"
        assert (await executor.run(p, "core.set_person_ai_model", {"email": "gd@" + d, "model": "", **OP})).status == "denied"
        for name in POLICY_QUERIES:
            assert (await executor.query(p, name, {})).status == "denied", name
    # tự cấp quyền: không có trong danh sách, lệnh đặt quyền từ chối, DB chặn cả khi ghi thẳng bằng phiên của quản trị
    assert "core.ai_policy" not in {o["value"] for o in (await executor.query(qt, "admin.resource_options", {})).output}
    assert "core.ai_policy" not in {r["code"] for r in (await executor.query(qt, "admin.permissions", {})).output["rows"]}
    assert (await executor.run(qt, "admin.set_grant", {"role": "admin", "resource": "core.ai_policy", "scope": "all",
                                                       "actions": ["read", "manage"]})).status == "not_found"
    with pytest.raises(Exception, match="nhà vận hành"):
        async with db.tenant_tx(qt) as c:
            await c.execute("INSERT INTO core.grants (role, resource, scope, actions) VALUES ('admin', 'core.ai_policy', 'all', '{read,manage}')")
    # menu: không màn chính sách nào; màn lượt dùng không có tiền / token / model / hạn mức theo người
    screens = {s["key"] for w in workspaces.manifest(qt, "x")["workspaces"] for s in w["screens"]}
    assert "ai_usage" in screens and not {"ai_tiers", "ai_role_tiers", "ai_tokens"} & screens
    await _use(sale, fresh=10_000, out=500, usd="0.12")
    page = str((await executor.query(gd, "core.ai_usage", {"period": "30d"})).output)
    assert "Lượt" in page and not any(w in page for w in ("USD", "Token", "token", "deepseek", "gemini", "0,12", "0.12"))
    # nhà vận hành đổi chính sách ⇒ có trong nhật ký (người + lý do), không hiện ở Nhật ký thao tác của khách
    assert (await executor.run(system_principal(tid), "core.save_ai_tier", tier)).status == "ok"
    logged = await owner_val("""SELECT input FROM core.audit_log WHERE tenant_id = %s AND command = 'core.save_ai_tier' AND status = 'ok'
                                ORDER BY id DESC LIMIT 1""", (tid,))
    assert logged["by"] == OP["by"] and logged["why"] == OP["why"]
    audit = (await executor.query(qt, "admin.audit", {})).output["rows"]
    assert not [r for r in audit if r["command"].startswith("core.") and "ai_" in r["command"]]
    nobody = await executor.run(system_principal(tid), "core.save_ai_tier", {**tier, "why": " "})
    assert nobody.status == "invalid" and "lý do" in nobody.error


async def test_the_operator_path_validates_and_sets_a_persons_model(keys: pytest.MonkeyPatch) -> None:
    tid, d = await _company()
    op = system_principal(tid)
    tier = {"key": "basic", "label": "Cơ bản", "person_monthly_tokens": 5_000_000, "is_default": True, "sort": 10, **OP}
    bad = await executor.run(op, "core.save_ai_tier", {**tier, "model": "khong-co-nha:abc"})
    assert bad.status == "invalid" and "nhà_cung_cấp:tên_model" in bad.error
    off = await executor.run(op, "core.save_ai_tier", {**tier, "is_default": False})
    assert off.status == "invalid" and "mặc định" in off.error                        # công ty luôn có đúng một hạng mặc định
    assert (await executor.run(op, "core.save_ai_tier", {**tier, "voice": True})).status == "ok"
    assert (await seed.who(tid, "sale1", d)).ai_voice is True
    page = (await executor.query(op, "core.ai_tiers", {})).output
    assert [row["key"] for row in page["rows"]] == ["basic", "manager", "executive"] and page["rows"][0]["people"] == 2
    gd = f"gd@{d}"
    assert (await executor.run(op, "core.set_person_ai_model", {"email": gd, "model": "anthropic:claude-sonnet-5", **OP})).status == "invalid"   # chưa có khoá
    keys.setattr(settings, "llm_keys", {"anthropic": "sk-ant-test"})
    assert (await executor.run(op, "core.set_person_ai_model", {"email": gd, "model": "anthropic:claude-sonnet-5", **OP})).status == "ok"
    p = await seed.who(tid, "gd", d)
    assert p.ai_model == "anthropic:claude-sonnet-5" and llm.model_for(p).spec == "anthropic:claude-sonnet-5"   # type: ignore[attr-defined]
    assert (await executor.run(op, "core.set_person_ai_model", {"email": f"sale1@{d}", "model": "anthropic:claude-sonnet-5", **OP})).status == "invalid"
    assert (await executor.run(op, "core.set_person_ai_model", {"email": gd, "model": "", **OP})).status == "ok"
    assert (await seed.who(tid, "gd", d)).ai_model == ""
    # lệnh vận hành (CLI): tham số không nêu ⇒ giữ giá trị đang có; ghi người + lý do; show / log đọc được
    args = ai_policy.parser().parse_args(["--tenant", "x", "set-tier", "basic", "--tokens", "7.000.000", "--by", "Anh", "--why", "HĐ 11/2026"])
    await ai_policy.set_tier(tid, args)
    assert (await owner_val(TIERS, (tid,)))["basic"][1:4] == [7_000_000, None, True]   # voice=True từ lần sửa trên vẫn giữ
    await ai_policy.show(tid, "x")
    await ai_policy.log(tid, 5)
    assert await owner_val("""SELECT input->>'why' FROM core.audit_log WHERE tenant_id = %s AND command = 'core.save_ai_tier' ORDER BY id DESC LIMIT 1""",
                           (tid,)) == "HĐ 11/2026"


async def test_the_token_report_counts_per_person_per_month_and_exports_xlsx(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    tid, d = await _company()
    s1, s2 = await seed.who(tid, "sale1", d), await seed.who(tid, "sale2", d)
    await _use(s1, fresh=1_000, cached=4_000, written=500, out=200, usd="0.01")
    await _use(s1, fresh=2_000, out=100)
    await _use(s2, fresh=300, cached=100, out=50)
    # lượt tháng trước không tính vào tháng này
    await owner_val("""INSERT INTO core.ai_usage (tenant_id, user_id, surface, category, modules, model, requests, tool_calls, input_tokens, output_tokens,
                                                 cache_read_tokens, cache_write_tokens, ok, at)
                       VALUES (%s, %s, 'chat', 'chat', '{}', 'x', 1, 0, 999999, 1, 0, 0, true, now() - interval '40 days') RETURNING 1""", (tid, s1.user_id))
    card = (await executor.query(system_principal(tid), "core.ai_token_report", {})).output
    rows = {r["email"]: r for r in card["blocks"][1]["page"]["rows"]}
    a = rows[s1.email]
    assert (a["runs"], a["tfresh"], a["tcache"], a["twrite"], a["tout"], a["quota"], a["total"]) == (2, 3_000, 4_000, 500, 300, 3_800, 7_800)
    assert a["tier"] == "Cơ bản" and a["cost"] == 0.01 and a["unpriced"] == 1
    assert (rows[s2.email]["quota"], rows[s2.email]["tcache"]) == (350, 100)
    assert a["quota"] == await owner_val("SELECT tokens FROM core.ai_person_spend WHERE user_id = %s", (s1.user_id,))   # cùng số với bộ đếm hạn mức
    path = tmp_path / "token.xlsx"
    await ai_policy.report(tid, "", str(path))
    wb = load_workbook(path)
    assert len(wb.sheetnames) == 4 and wb.sheetnames[0].startswith("Theo người")
    ws = wb[wb.sheetnames[0]]
    head = [c.value for c in ws[1]]
    first = {head[i]: c.value for i, c in enumerate(ws[2])}
    assert first["Email"] == s1.email and first["Token tính hạn mức"] == 3_800 and first["Token cache đọc"] == 4_000


# ── Giọng nói: cùng cửa, kiểm TRƯỚC khi mở Gemini Live ──

class SocketProbe(FakeWS):
    def __init__(self) -> None:
        super().__init__()
        self.closed: int | None = None

    async def send_json(self, data: Any) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = code


@pytest.fixture
def live_probe(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Thay ``Bridge.run`` (nơi mở Gemini Live) bằng đầu dò — không mở mạng, không tốn phí (như phép tái hiện của review)."""
    opened: list[Any] = []

    async def probe(self: voice.Bridge) -> None:
        opened.append(self)

    monkeypatch.setattr(voice.Bridge, "run", probe)
    monkeypatch.setattr(settings, "llm_api_key", "khoa-giong-noi")
    return opened


async def test_voice_opens_only_for_a_tier_that_allows_it_and_never_when_capped(live_probe: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
    tid, d = await _company()
    ws = SocketProbe()
    await voice.serve(ws, await seed.who(tid, "sale1", d))  # type: ignore[arg-type]  # hạng "Cơ bản": không giọng nói
    assert not live_probe and ws.closed == 4403 and "chưa được nói chuyện bằng giọng" in ws.sent[0]["detail"]
    ws = SocketProbe()
    await voice.serve(ws, await seed.who(tid, "qt", d))  # type: ignore[arg-type]  # hạng "Quản lý": không giọng nói
    assert not live_probe and ws.closed == 4403
    await voice.serve(SocketProbe(), await seed.who(tid, "gd", d))  # type: ignore[arg-type]  # hạng "Lãnh đạo": mở
    assert len(live_probe) == 1 and live_probe[0].key == "khoa-giong-noi"
    # hết hạn mức (công ty) ⇒ không mở, dù hạng cho phép
    await owner_val("UPDATE core.tenant_profile SET ai_monthly_usd = 1 WHERE tenant_id = %s RETURNING 1", (tid,))
    await _spend(monkeypatch, await seed.who(tid, "sale1", d), "1.5")
    ws = SocketProbe()
    await voice.serve(ws, await seed.who(tid, "gd", d))  # type: ignore[arg-type]
    assert len(live_probe) == 1 and "hết hạn mức" in ws.sent[0]["detail"]
    await owner_val("UPDATE core.tenant_profile SET ai_monthly_usd = NULL, ai_enabled = false WHERE tenant_id = %s RETURNING 1", (tid,))
    ws = SocketProbe()
    await voice.serve(ws, await seed.who(tid, "gd", d))  # type: ignore[arg-type]  # công ty tắt AI
    assert len(live_probe) == 1 and "tắt trợ lý AI" in ws.sent[0]["detail"]


async def test_an_open_voice_session_is_rechecked_each_turn_and_closes_when_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    tid, d = await _company()
    gd, op = await seed.who(tid, "gd", d), system_principal(tid)
    ws, live = SocketProbe(), FakeLive()
    model = Scripted([[call("crm.leads")], "Dạ có ạ."])
    bridge = voice.Bridge(ws, gd, "x", None, model=model)  # type: ignore[arg-type]
    assert await bridge.allowed() == ""
    await bridge.answer(live, ask("Có khách nào mới?"))
    assert len(model.turns) == 0 and ws.closed is None                                # lượt chạy bình thường
    # đang mở phiên: nhà vận hành bỏ giọng nói khỏi hạng ⇒ lượt kế tiếp bị từ chối, phiên đóng, không gọi model
    assert (await executor.run(op, "core.save_ai_tier", {"key": "executive", "label": "Lãnh đạo", "voice": False, "sort": 30, **OP})).status == "ok"
    model.turns.append("không được gọi")
    await bridge.answer(live, ask("Còn gì nữa?"))
    assert model.turns == ["không được gọi"] and ws.closed == 4403 and bridge.stopped
    assert "Trợ lý đã dừng" in live.responses[-1].response["answer"]
    # hạn mức token theo NGƯỜI chạm giữa phiên ⇒ kỳ kiểm kế tiếp đóng phiên (lượt đang chạy thì đã xong)
    assert (await executor.run(op, "core.save_ai_tier", {"key": "executive", "label": "Lãnh đạo", "voice": True, "person_monthly_tokens": 10_000,
                                                         "sort": 30, **OP})).status == "ok"
    bridge = voice.Bridge(SocketProbe(), await seed.who(tid, "gd", d), "x", None, model=model)  # type: ignore[arg-type]
    assert await bridge.allowed() == ""
    await _use(gd, fresh=12_000)
    assert await bridge.allowed() == auth.PERSON_OUT


async def test_live_usage_is_recorded_into_the_same_spend_exact_when_reported_estimated_when_not() -> None:
    tid, d = await _company()
    gd = await seed.who(tid, "gd", d)
    meter = voice.LiveMeter()
    meter.add(types.UsageMetadata(prompt_token_count=12_000, response_token_count=3_000, total_token_count=15_000,
                                  prompt_tokens_details=[types.ModalityTokenCount(modality=types.MediaModality.AUDIO, token_count=10_000)],
                                  response_tokens_details=[types.ModalityTokenCount(modality=types.MediaModality.AUDIO, token_count=3_000)]))
    await meter.flush(gd)
    row = await owner_val("""SELECT json_build_array(category, surface, model, input_tokens, output_tokens, cost_usd) FROM core.ai_usage
                             WHERE tenant_id = %s ORDER BY id DESC LIMIT 1""", (tid,))
    assert row[:5] == ["voice:live", "voice", settings.voice_model, 12_000, 3_000]
    # bảng giá gemini-3.8-live: âm thanh vào 3 / ra 12 USD, chữ vào 0,75 / ra 4,5 USD mỗi triệu token
    assert Decimal(str(row[5])) == Decimal("0.0675")   # 10k × 3 + 2k × 0,75 + 3k × 12 (phần triệu)
    meter.since = time.monotonic() - 120                                              # hai phút không có số token ⇒ ước tính theo phút
    await meter.flush(gd)
    est = await owner_val("SELECT json_build_array(category, cost_usd) FROM core.ai_usage WHERE tenant_id = %s ORDER BY id DESC LIMIT 1", (tid,))
    assert est[0] == "voice:live_estimate" and Decimal(str(est[1])) == pytest.approx(Decimal("0.10"), abs=Decimal("0.001"))
    # thời gian nói chuyện vào duration_ms (giây giọng nói của báo cáo token); token Live vào hạn mức theo người như chat
    assert await owner_val("SELECT duration_ms FROM core.ai_usage WHERE tenant_id = %s ORDER BY id DESC LIMIT 1", (tid,)) >= 119_000
    assert await owner_val("SELECT tokens FROM core.ai_person_spend WHERE tenant_id = %s AND user_id = %s", (tid, gd.user_id)) == 15_000
    spent = await owner_val("SELECT usd FROM core.ai_person_spend WHERE tenant_id = %s AND user_id = %s", (tid, gd.user_id))
    assert Decimal(str(spent)) == pytest.approx(Decimal("0.1675"), abs=Decimal("0.001"))
    assert Decimal(str(await owner_val("SELECT usd FROM core.ai_spend WHERE tenant_id = %s", (tid,)))) == pytest.approx(Decimal("0.1675"), abs=Decimal("0.001"))
    assert usage.label("voice:live_estimate") == "Giọng nói (Gemini Live) · ước tính theo phút"
