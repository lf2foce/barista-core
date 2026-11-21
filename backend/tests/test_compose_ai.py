"""Soạn với AI (thử nghiệm — ghi chú nội bộ (barista-docs)): số chỉ từ khối số liệu (truy vấn chạy bằng quyền người soạn), code kiểm số
trong chữ AI, bản soạn của riêng người tạo, xuất tệp qua kho tệp, chế độ trợ lý."""

import base64
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic_ai import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from barista.kernel import executor
from barista.modules import compose
from barista.modules.compose import check
from tests.conftest import Who, ok, q

pytestmark = pytest.mark.usefixtures("compose_on")   # gói tuỳ chọn, mặc định tắt — bật cho công ty mẫu


def test_number_check_is_lenient_on_years_ordinals_and_rounding() -> None:
    values = {Decimal(4_512_000_000), Decimal(12), Decimal("37.5")}
    t = date(2026, 11, 8)
    assert check.unmatched("Năm 2026, quý 3 doanh số khoảng 4,5 tỷ với 12 giao dịch, tỷ lệ 37,5%", values, set(), t) == []
    assert check.unmatched("Dự án có 120 căn và giá 3,2 tỷ", values, set(), t) == ["120", "3,2 tỷ"]
    assert check.unmatched("Còn [cần số liệu: số căn trống 99] căn", values, set(), t) == []
    assert check.unmatched("Bàn giao 15/12/2026", values, {date(2026, 12, 15)}, t) == []
    assert check.unmatched("Bàn giao 16/12/2026", values, {date(2026, 12, 15)}, t) == ["16/12/2026"]
    assert check.values_of({"rows": [{"code": "12A01", "price": 5_000_000_000}], "kpis": [{"value": 7}]}) >= {Decimal(5_000_000_000), Decimal(7)}


def _model(outputs: list[dict[str, Any]], fetch: dict[str, Any] | None = None) -> tuple[FunctionModel, list[int]]:
    """Lần 1: gọi fetch_data (nếu có); sau đó mỗi lần trả một bản soạn trong ``outputs`` (lần sau = sau lời kiểm số của code)."""
    calls: list[int] = []

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        if fetch is not None and len(calls) == 1:
            # hai lời gọi SONG SONG (Gemini hay làm vậy) — cùng một kết nối DB, điểm lưu phải lần lượt
            return ModelResponse(parts=[ToolCallPart("fetch_data", fetch, tool_call_id="c1"), ToolCallPart("fetch_data", fetch, tool_call_id="c2")])
        out = outputs.pop(0)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, out, tool_call_id=f"o{len(calls)}")])
    return FunctionModel(reply, model_name="compose-fake"), calls


def _draft(text: str) -> dict[str, Any]:
    return {"title": "Báo cáo giao dịch", "blocks": [{"type": "heading", "text": "Tình hình giao dịch"}, {"type": "paragraph", "text": text},
                                                   {"type": "data", "ref": "d1"}, {"type": "data", "ref": "d99"}]}


async def _start(p: Any, outputs: list[dict[str, Any]]) -> tuple[dict[str, Any], list[int]]:
    model, calls = _model(outputs, {"query": "deals.list", "title": "Giao dịch của tôi", "input": {}})
    compose.MODEL = model
    try:
        out = await ok(p, "compose.start", kind="post", brief="Báo cáo giao dịch tháng này cho giám đốc")
    finally:
        compose.MODEL = None
    assert out["status"] == "drafting"   # lệnh chỉ ghi "đang soạn"; lượt model chạy SAU commit (executor chờ xong mới trả lời)
    comp = (await q(p, "compose.view", session_id=out["session_id"]))["blocks"][0]
    assert comp["status"] == "ready", comp["error"]
    return {**out, **comp["stats"]}, calls


async def test_a_bad_query_input_gets_the_schema_back_and_a_retried_step_is_not_shown_as_failed(who: Who) -> None:
    from pydantic_ai.messages import ToolReturnPart

    sale = await who("sale1")
    seen: list[Any] = []

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        n = len(seen)
        back = [part.content for m in messages for part in getattr(m, "parts", []) if isinstance(part, ToolReturnPart)]
        seen.append(back[-1] if back else None)
        if n == 0:   # đầu vào sai (trường không có)
            return ModelResponse(parts=[ToolCallPart("fetch_data", {"query": "crm.leads", "title": "Khách", "input": {"stage": {"sai": "kiểu"}}}, tool_call_id="c1")])
        if n == 1:   # sửa theo lược đồ vừa nhận
            return ModelResponse(parts=[ToolCallPart("fetch_data", {"query": "crm.leads", "title": "Khách", "input": {}}, tool_call_id="c2")])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, _draft("Như bảng dưới."), tool_call_id="o")])

    compose.MODEL = FunctionModel(reply, model_name="compose-fake")
    try:
        out = await ok(sale, "compose.start", kind="post", brief="Báo cáo khách đang theo")
    finally:
        compose.MODEL = None
    bad = seen[1]
    assert isinstance(bad, dict) and bad.get("error") and isinstance(bad.get("input_schema"), dict) and bad.get("hint")   # model nhận lược đồ đúng
    comp = (await q(sale, "compose.view", session_id=out["session_id"]))["blocks"][0]
    assert comp["status"] == "ready", comp["error"]
    steps = [s for turn in comp["turns"] if turn.get("role") == "assistant" for s in turn.get("steps") or []]
    assert steps and all(s["status"] == "ok" for s in steps), steps                       # bước đã tự sửa được không còn hiện là lỗi
    assert compose.settle_steps([{"title": "A", "status": "error"}, {"title": "B", "status": "error"}, {"title": "A", "status": "ok"}]) == [
        {"title": "B", "status": "error"}, {"title": "A", "status": "ok"}]                 # lỗi chưa gỡ được vẫn giữ


async def test_asking_the_same_thing_twice_in_one_turn_runs_it_once_and_shows_one_step(who: Who, monkeypatch: Any) -> None:
    """Gốc của "Đã chạy 11 bước" lặp tên: model hay gọi lại ĐÚNG truy vấn + đầu vào đã có kết quả (song song hoặc ở vòng sau). Lần lặp không chạy lại
    truy vấn, không thêm một bước vào nhật ký, không thêm khối số liệu trùng — trả lại mã cũ. Đầu vào KHÁC vẫn chạy bình thường."""
    from pydantic_ai.messages import ToolReturnPart

    sale = await who("sale1")
    ran: list[str] = []
    real = executor.query

    async def counting(p: Any, name: str, inp: Any, **kw: Any) -> Any:
        ran.append(name)
        return await real(p, name, inp, **kw)

    monkeypatch.setattr(executor, "query", counting)
    fetch = {"query": "crm.leads", "title": "Khách", "input": {}}
    returned: list[Any] = []

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        back = [part.content for m in messages for part in getattr(m, "parts", []) if isinstance(part, ToolReturnPart)]
        n = len([m for m in messages if isinstance(m, ModelResponse)])
        returned.append(back)
        if n == 0:   # hai lời gọi song song cùng đầu vào + hai lần tìm tài liệu cùng câu hỏi
            return ModelResponse(parts=[ToolCallPart("fetch_data", fetch, tool_call_id="c1"), ToolCallPart("fetch_data", dict(fetch), tool_call_id="c2"),
                                        ToolCallPart("search_docs", {"q": "chính sách bán hàng"}, tool_call_id="s1"),
                                        ToolCallPart("search_docs", {"q": "chính sách bán hàng"}, tool_call_id="s2")])
        if n == 1:   # vòng sau: lại đúng truy vấn đó (đầu vào thêm trường null = vẫn là cùng đầu vào) + một câu tìm KHÁC
            return ModelResponse(parts=[ToolCallPart("fetch_data", {**fetch, "input": {"stage": None}}, tool_call_id="c3"),
                                        ToolCallPart("search_docs", {"q": "bảng giá"}, tool_call_id="s3")])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, _draft("Như bảng dưới."), tool_call_id="o")])

    compose.MODEL = FunctionModel(reply, model_name="compose-fake")
    try:
        out = await ok(sale, "compose.start", kind="post", brief="Báo cáo khách đang theo")
    finally:
        compose.MODEL = None
    assert ran.count("crm.leads") == 1 and ran.count("kb.search") == 2, ran            # chạy một lần mỗi (truy vấn, đầu vào) khác nhau
    refs = [r["ref"] for back in returned for r in back if isinstance(r, dict) and "ref" in r]
    assert refs and len(set(refs)) == 1, refs                                            # cả ba lời gọi fetch_data nhận CÙNG một mã
    comp = (await q(sale, "compose.view", session_id=out["session_id"]))["blocks"][0]
    assert comp["status"] == "ready", comp["error"]
    steps = [s["title"] for turn in comp["turns"] if turn.get("role") == "assistant" for s in turn.get("steps") or []]
    assert len(steps) == 3 and steps.count("Tìm trong tài liệu công ty") == 2, steps     # 1 truy vấn + 2 câu tìm khác nhau — không còn dòng lặp
    assert comp["stats"]["data"] == 1                                                    # một khối số liệu, không phải ba


async def test_agent_modes(who: Who) -> None:
    sale = await who("sale1")
    out, _ = await _start(sale.as_agent(), [_draft("Như bảng dưới.")])
    sid = str(out["session_id"])
    agent = sale.as_agent()
    assert (await executor.run(agent, "compose.export", {"session_id": sid, "format": "docx"})).status == "ok"
    assert (await executor.run(agent, "compose.edit_block", {"session_id": sid, "block": "b2", "text": "x"})).status != "ok"
    assert (await executor.run(agent, "compose.delete", {"session_id": sid})).status != "ok"
    assert (await executor.query(agent, "compose.download", {"export_id": sid})).status != "ok"


async def test_brand_is_company_settings_and_the_piece_reads_it_from_there(who: Who) -> None:
    """ADR 0249 lát 2: nhận diện thương hiệu là CÀI ĐẶT CÔNG TY (Quản trị › Nhận diện thương hiệu, ``admin.brand``) — phân hệ Ấn phẩm không còn bộ riêng; ấn phẩm
    vẽ (xem trước + tệp xuất) bằng đúng bộ đó, kể cả logo (tệp logo do module Quản trị giữ)."""
    from barista import workspaces
    from barista.kernel import registry

    admin, sale = await who("admin"), await who("sale1")
    assert "compose.set_brand" not in registry.OPS and "compose.brand_view" not in registry.OPS
    assert (await executor.run(sale, "admin.set_brand", {"primary_color": "AA0000"})).status == "denied"
    png = base64.b64encode(bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                                         "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")).decode()
    await ok(admin, "admin.set_brand", primary_color="#aa0000", font="Calibri", footer="Công ty mẫu", logo_b64=png, logo_filename="logo.png")
    row = (await q(admin, "admin.brand_view"))["rows"][0]
    assert (row["primary_color"], row["font"], row["footer"], row["logo"]) == ("AA0000", "Calibri", "Công ty mẫu", "logo.png")
    out, _ = await _start(sale, [_draft("Như bảng dưới.")])
    comp = (await q(sale, "compose.view", session_id=str(out["session_id"])))["blocks"][0]
    assert comp["brand"]["color"] == "AA0000" and comp["brand"]["font"] == "Calibri" and comp["brand"]["logo"].startswith("data:image/png;base64,")
    exp = await ok(sale, "compose.export", session_id=str(out["session_id"]), format="html")
    page = base64.b64decode((await q(sale, "compose.download", export_id=str(exp["export_id"])))["data_b64"]).decode()
    assert "#AA0000" in page.upper() and "Công ty mẫu" in page and "data:image/png;base64," in page
    await ok(admin, "admin.set_brand", primary_color="1F4E79", font="Arial", footer="")   # không gửi logo ⇒ giữ logo cũ
    assert (await q(admin, "admin.brand_view"))["rows"][0]["logo"] == "logo.png"
    ws = next(w for w in workspaces.workspaces() if w.key == "compose")
    spec = workspaces.screen_spec(sale, ws.screens[0])
    assert ws.title == "Ấn phẩm" and spec is not None and spec["badge"] == "Thử nghiệm" and spec["query"] == "compose.studio" and not spec["actions"]


async def test_revise_one_block_keeps_the_others(who: Who) -> None:
    sale = await who("sale1")
    out, _ = await _start(sale, [_draft("Như bảng dưới.")])
    sid = str(out["session_id"])
    before = (await q(sale, "compose.view", session_id=sid))["blocks"][0]["blocks"]
    model, _ = _model([{"title": "x", "blocks": [{"type": "paragraph", "text": "Lời dẫn mới, ngắn hơn."}]}])
    compose.MODEL = model
    try:
        await ok(sale, "compose.revise", session_id=sid, block=before[1]["id"], instruction="ngắn hơn")
    finally:
        compose.MODEL = None
    comp = (await q(sale, "compose.view", session_id=sid))["blocks"][0]
    after = comp["blocks"]
    assert comp["status"] == "ready" and comp["version"] == 2 and after[1]["text"] == "Lời dẫn mới, ngắn hơn."
    assert [b["id"] for b in after if b["id"] != after[1]["id"]] == [b["id"] for b in before if b["id"] != before[1]["id"]]


async def test_slow_model_times_out_without_holding_a_transaction(who: Who, monkeypatch: Any) -> None:
    """Lượt model chạy NGOÀI giao dịch: trong lúc model "nghĩ", không kết nối nào của DB test đứng ở idle in transaction; quá trần ⇒ bản soạn
    "Lỗi: hết giờ", không treo ở "đang soạn", chỗ của công ty được trả."""
    import asyncio

    from tests.conftest import owner_val

    sale = await who("sale1")
    seen: list[int] = []

    async def slow(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(int(await owner_val("""SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()
                                              AND state LIKE 'idle in transaction%%' AND usename = 'barista_app'""")))
        await asyncio.sleep(5)
        raise AssertionError("không tới đây")

    monkeypatch.setattr(compose, "TIMEOUT_S", 0.5)
    monkeypatch.setattr(compose, "MODEL", FunctionModel(slow, model_name="slow"))
    out = await ok(sale, "compose.start", kind="slides", brief="Slide chậm")
    comp = (await q(sale, "compose.view", session_id=out["session_id"]))["blocks"][0]
    assert seen == [0]
    assert comp["status"] == "failed" and "Hết giờ" in comp["error"]
    assert not compose._running.get(sale.tenant_id)


async def test_company_cap_refuses_extra_concurrent_drafts(who: Who, monkeypatch: Any) -> None:
    import asyncio

    sale, other = await who("sale1"), await who("sale2")
    gate = asyncio.Event()

    async def wait(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await gate.wait()
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, _draft("Như bảng dưới."), tool_call_id="o")])

    monkeypatch.setattr(compose, "MODEL", FunctionModel(wait, model_name="wait"))
    runs = [asyncio.create_task(executor.run(p, "compose.start", {"kind": "post", "brief": f"Bản {i}"})) for i, p in enumerate((sale, other))]
    for _ in range(100):
        if len(compose._running.get(sale.tenant_id, {})) >= compose.CAP:
            break
        await asyncio.sleep(0.05)
    third = await executor.run(sale, "compose.start", {"kind": "post", "brief": "Bản thứ ba"})
    assert third.status == "invalid" and "đang soạn" in third.error
    gate.set()
    assert [r.status for r in await asyncio.gather(*runs)] == ["ok", "ok"]
    assert (await executor.run(sale, "compose.start", {"kind": "post", "brief": "Sau khi xong"})).status == "ok"


async def test_new_marketing_kinds_are_stored_and_a_flyer_gets_its_one_page_layout(who: Who) -> None:
    """0231: tờ rơi / bài đăng lưu được (ràng buộc loại đã nới — compose/072); tờ rơi mới mặc định bố cục "flyer"; màn soạn chỉ mời chọn loại marketing."""
    sale = await who("sale1")
    for kind in ("flyer", "post"):
        model, _ = _model([{"title": "Ưu đãi tháng này", "blocks": [{"type": "heading", "text": "Ưu đãi lớn"}, {"type": "paragraph", "text": "Liên hệ ngay"}]}])
        compose.MODEL = model
        try:
            out = await ok(sale, "compose.start", kind=kind, brief="Ưu đãi tháng này cho khách mới")
        finally:
            compose.MODEL = None
        comp = (await q(sale, "compose.view", session_id=out["session_id"]))["blocks"][0]
        assert comp["status"] == "ready", comp["error"]
        assert comp["kind"] == kind and [k["key"] for k in comp["kinds"]] == ["flyer", "brochure", "slides", "post", "memo", "teaser"]
        assert comp["layout"]["template"] == ("flyer" if kind == "flyer" else "plain")   # loại quyết mẫu (kinds.py)
        assert [t["key"] for t in comp["layout"]["templates"]] == [comp["layout"]["template"]]   # chỉ mẫu hợp loại được mời chọn
