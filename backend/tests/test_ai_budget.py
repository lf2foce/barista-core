"""Hạn mức chi AI theo công ty (0092): chi tháng đếm cho CẢ CÔNG TY (kể cả lượt của người chỉ xem được lượt của mình), báo quản trị một lần ở
ngưỡng và một lần khi hết, hết hạn mức thì ``model_for`` từ chối như công ty tắt AI; không đặt trần thì không chặn."""

import uuid
from decimal import Decimal
from typing import Any

import pytest
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage

from barista import seed
from barista.agent import llm, usage
from barista.kernel.auth import ai_block
from tests.conftest import owner_val


def test_block_message_only_when_spent_reaches_the_cap() -> None:
    assert ai_block(Decimal("5"), None) == "" and ai_block(Decimal("4.99"), Decimal("5")) == ""
    assert "hết hạn mức" in ai_block(Decimal("5"), Decimal("5")) and "USD" not in ai_block(Decimal("5"), Decimal("5"))   # khách không thấy số tiền (0193)


async def _company(cap: str | None) -> tuple[Any, dict[str, Any]]:
    d = f"{uuid.uuid4().hex[:6]}.ai.test"
    tid = await seed.create_tenant("Công ty hạn mức AI (test)", d, packs=("real_estate",),
                                   people=[("qt", "Quản trị", "admin", "QT", True), ("sale", "Sale", "sale", "HQ", False)])
    if cap is not None:
        await owner_val("UPDATE core.tenant_profile SET ai_monthly_usd = %s, ai_warn_pct = 80 WHERE tenant_id = %s RETURNING 1", (cap, tid))
    return tid, {"d": d, "tid": tid}


async def _turn(monkeypatch: pytest.MonkeyPatch, p: Any, usd: str) -> None:
    monkeypatch.setattr(usage, "cost_usd", lambda _m: Decimal(usd))
    await usage.record(p, surface="chat", category="chat", modules=[], usage=RunUsage(requests=1), model="test-model")


def _fake() -> FunctionModel:
    return FunctionModel(lambda *_: None, model_name="never-called")   # type: ignore[arg-type, return-value]


async def test_company_spend_counts_every_turn_warns_once_then_stops_the_assistant(monkeypatch: pytest.MonkeyPatch) -> None:
    tid, c = await _company("1.00")
    qt, sale = await seed.who(tid, "qt", c["d"]), await seed.who(tid, "sale", c["d"])
    assert sale.ai_block == "" and llm.model_for(sale, _fake())
    await _turn(monkeypatch, sale, "0.85")                       # lượt của SALE (RLS chỉ thấy lượt của mình) vẫn cộng vào chi cả công ty
    notes = "SELECT coalesce(json_agg(title ORDER BY created_at), '[]') FROM core.notifications WHERE user_id = %s AND kind = 'ai_budget'"
    assert await owner_val(notes, (qt.user_id,)) == ["Trợ lý AI đã dùng 80% hạn mức tháng"]
    await _turn(monkeypatch, sale, "0.05")                       # vẫn dưới trần: không báo lại
    assert len(await owner_val(notes, (qt.user_id,))) == 1
    await _turn(monkeypatch, qt, "0.20")
    assert await owner_val(notes, (qt.user_id,)) == ["Trợ lý AI đã dùng 80% hạn mức tháng", "Trợ lý AI tạm dừng: hết hạn mức tháng"]
    assert await owner_val("SELECT usd::text FROM core.ai_spend WHERE tenant_id = %s", (tid,)) == "1.100000"
    sale = await seed.who(tid, "sale", c["d"])                    # nạp lại quyền (mỗi request / mỗi lần chạy việc nền)
    assert "hết hạn mức" in sale.ai_block
    with pytest.raises(llm.AIDisabled, match="hết hạn mức"):
        llm.model_for(sale, _fake())                             # kể cả khi bên gọi tiêm sẵn model
    with pytest.raises(llm.AIDisabled):
        llm.model_for(sale.as_agent(), _fake())


async def test_no_cap_means_no_block_but_spend_is_still_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    tid, c = await _company(None)
    qt = await seed.who(tid, "qt", c["d"])                        # hạng "Quản lý": không trần theo người (trần theo người — test_ai_tiers.py)
    await _turn(monkeypatch, qt, "50")
    assert (await seed.who(tid, "qt", c["d"])).ai_block == ""
    assert await owner_val("SELECT usd::int FROM core.ai_spend WHERE tenant_id = %s", (tid,)) == 50


def test_every_usage_surface_in_code_is_one_the_database_accepts() -> None:
    """Lượt dùng AI ghi với ``surface`` ngoài danh sách ràng buộc ⇒ DB từ chối, dòng bị bỏ im lặng, tiền không tính vào hạn mức (đào tạo, app —
    review 09/11). Mọi chữ ``surface="…"`` trong mã phải nằm trong ràng buộc ``ai_usage_surface_check``."""
    import re
    from pathlib import Path

    from tests.module_src import py_files

    src = Path(__file__).resolve().parents[1] / "src" / "barista"
    # ràng buộc hiện hành = lần định nghĩa SAU CÙNG trong các migration của gói nền (094 nới thêm "docread" — 0232)
    defs = [(f, re.search(r"ai_usage_surface_check CHECK \(\(?surface = ANY \(ARRAY\[(.*?)\]", f.read_text(), re.S))
            for f in sorted((src / "migrations" / "base").rglob("*.sql"), key=lambda f: (f.parent.name != "000_baseline", f.name))]
    last = [m for _, m in defs if m][-1]
    allowed = set(re.findall(r"'(\w+)'", last.group(1)))
    used = {m for f in py_files() for m in re.findall(r'surface="(\w+)"', f.read_text())}
    assert used and used <= allowed, used - allowed
