"""ẤN PHẨM GẮN HỒ SƠ (ADR 0249 lát 2), bản LÕI qua gói thế vai: công thức nói về MỘT hồ sơ (đơn) — người soạn CHỌN hồ sơ trong danh sách theo quyền của họ (0225),
ấn phẩm ghi lại hồ sơ + khách, số lấy từ hồ sơ (không model), loại ấn phẩm của công thức quyết trang mẫu (mẫu gói nêu sai loại bị bỏ); nút "Soạn ấn phẩm" do module
CHỦ hồ sơ đặt trên thẻ của mình; "làm bản cho khách khác" dựng lại bằng đúng công thức; khách mở link lần đầu ⇒ sự kiện outbox ⇒ người phụ trách khách được báo."""

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from barista.http_api import app
from barista.kernel import events, executor
from barista.modules import compose
from tests.conftest import Who, fresh_phone, ok, owner_val, q
from tests.standin import helpers


@pytest_asyncio.fixture(autouse=True)
async def compose_on(who: Who) -> AsyncIterator[None]:
    admin = await who("admin")
    assert (await executor.run(admin, "admin.set_pack", {"pack": "compose", "enabled": True})).status == "ok"
    try:
        yield
    finally:
        await executor.run(admin, "admin.set_pack", {"pack": "compose", "enabled": False})


async def _composer(p: Any, sid: Any) -> dict[str, Any]:
    return (await q(p, "compose.view", session_id=str(sid)))["blocks"][0]


async def test_a_piece_from_a_record_is_about_that_record_and_pulls_numbers_with_no_model(who: Who) -> None:
    o = await helpers.order(who, owner="sale1")
    sale1, sale2 = await who("sale1"), await who("sale2")
    picks = await q(sale1, "compose.subject_options", kind="demo_order")                                           # chọn hồ sơ trong danh sách, không gõ mã
    assert {"value": o["order_id"], "label": f"{o['code']} · Tư vấn triển khai"} in [{k: x[k] for k in ("value", "label")} for x in picks]
    assert all(x["value"] != o["order_id"] for x in await q(sale2, "compose.subject_options", kind="demo_order"))   # đơn của sale1: sale2 không thấy
    assert (await executor.run(sale2, "compose.brochure", {"kind": "demo_order", "subject": o["order_id"]})).status == "not_found"
    r = await executor.run(sale1, "compose.brochure", {"kind": "demo_order"})                                       # công thức có hồ sơ ⇒ phải chọn
    assert r.status == "invalid" and "Chọn hồ sơ" in r.error
    assert compose.MODEL is None                                                                                     # đường này không gọi model nào
    out = await ok(sale1, "compose.brochure", kind="demo_order", subject=o["order_id"])
    comp = await _composer(sale1, out["session_id"])
    assert comp["subject"] == {"kind": "orderdesk.order", "id": o["order_id"], "label": f"{o['code']} · Tư vấn triển khai"} and comp["from_record"]
    assert comp["kind"] == "flyer" and comp["layout"]["template"] == "flyer"                                        # gói nêu mẫu "editorial" — không hợp tờ rơi ⇒ mẫu của loại
    assert [f["key"] for f in comp["formats"]] == ["pdf", "html", "docx"] and [t["key"] for t in comp["layout"]["templates"]] == ["flyer"]
    data = next(b for b in comp["blocks"] if b["type"] == "data")
    assert data["source"]["query"] == "orderdesk.list" and data["page"]["rows"] and comp["stats"]["flagged"] == 0  # số = ảnh chụp truy vấn bằng quyền người soạn
    assert any(r["id"] == str(out["session_id"]) and r["subject_label"] == comp["subject"]["label"] for r in (await q(sale1, "compose.list"))["rows"])
    recipes = {r["key"]: r for r in comp["recipes"]}
    assert recipes["demo_order"]["needs_subject"] and recipes["demo_order"]["piece"] == "flyer" and not recipes["demo_orders"]["needs_subject"]


async def test_the_record_owner_puts_the_action_on_its_own_card_and_it_hides_when_pieces_are_off(who: Who) -> None:
    o = await helpers.order(who, owner="sale1")
    sale, admin = await who("sale1"), await who("admin")
    card = await q(sale, "orderdesk.case_file", code=o["code"])
    act = next(a for a in card["actions"] if a["command"] == "compose.brochure")
    assert act["preset"] == {"kind": "demo_order", "subject": o["order_id"]} and act["title"] == "Soạn ấn phẩm"
    lead = await ok(sale, "crm.create_lead", name="Khách Nút", phone=fresh_phone())
    journey = await q(sale, "crm.journey", lead_id=str(lead["lead_id"]))
    assert any(a["command"] == "compose.brochure" and a["preset"] == {"lead_id": str(lead["lead_id"])} for a in journey["actions"])
    await ok(admin, "admin.set_pack", pack="compose", enabled=False)
    sale = await who("sale1")
    assert all(a["command"] != "compose.brochure" for a in (await q(sale, "orderdesk.case_file", code=o["code"]))["actions"])
    assert all(a["command"] != "compose.brochure" for a in (await q(sale, "crm.journey", lead_id=str(lead["lead_id"])))["actions"])


async def test_make_one_for_another_customer_rebuilds_with_the_recipe_and_checks_the_customer(who: Who) -> None:
    o = await helpers.order(who, owner="sale1")
    sale1, sale2 = await who("sale1"), await who("sale2")
    a = await ok(sale1, "crm.create_lead", name="Khách Một", phone=fresh_phone())
    b = await ok(sale1, "crm.create_lead", name="Khách Hai", phone=fresh_phone())
    hidden = await ok(sale2, "crm.create_lead", name="Khách Của Người Khác", phone=fresh_phone())
    first = await ok(sale1, "compose.brochure", kind="demo_order", subject=o["order_id"], lead_id=str(a["lead_id"]))
    one = await _composer(sale1, first["session_id"])
    assert one["customer"] == {"kind": "crm.lead", "id": str(a["lead_id"]), "label": "Khách Một"}
    assert any(x.get("text") == "Dành riêng cho anh/chị Khách Một" for x in one["blocks"])
    r = await executor.run(sale1, "compose.duplicate", {"session_id": str(first["session_id"]), "lead_id": str(hidden["lead_id"])})
    assert r.status == "not_found"                                                                                   # khách người soạn không thấy ⇒ không có bản nào
    two = await _composer(sale1, (await ok(sale1, "compose.duplicate", session_id=str(first["session_id"]), lead_id=str(b["lead_id"])))["session_id"])
    assert two["session_id"] != one["session_id"] and two["customer"]["label"] == "Khách Hai" and two["subject"] == one["subject"]
    texts = [x.get("text") for x in two["blocks"]]
    assert "Dành riêng cho anh/chị Khách Hai" in texts and "Dành riêng cho anh/chị Khách Một" not in texts          # dựng LẠI cho khách mới, không chép lời chào cũ
    assert (await _composer(sale1, first["session_id"]))["customer"]["label"] == "Khách Một"                        # bản gốc giữ nguyên
    assert str(await owner_val("SELECT source_id FROM compose.sessions WHERE id = %s", (two["session_id"],))) == str(first["session_id"])
    again = await executor.run(sale1, "compose.duplicate", {"session_id": two["session_id"], "lead_id": str(b["lead_id"])})
    assert again.status == "invalid"                                                                                 # đã là của khách đó


async def _get(path: str) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.get(path)


async def test_a_customer_opening_the_link_is_an_event_and_their_owner_is_told_once(who: Who) -> None:
    o = await helpers.order(who, owner="sale1")
    sale = await who("sale1")
    lead = await ok(sale, "crm.create_lead", name="Khách Mở Link", phone=fresh_phone())
    sid = str((await ok(sale, "compose.brochure", kind="demo_order", subject=o["order_id"], lead_id=str(lead["lead_id"])))["session_id"])
    sh = await ok(sale, "compose.share", session_id=sid, days=7)
    assert (await _composer(sale, sid))["shares"][0]["recipient"] == "Khách Mở Link"                                  # người nhận mặc định = khách của ấn phẩm
    token = sh["url"].rsplit("/", 1)[1]
    for _ in range(2):
        assert (await _get(f"/v1/compose/shared/{token}")).status_code == 200
    got = await owner_val("""SELECT array_agg(payload ORDER BY id) FROM core.events WHERE type = 'compose.piece_opened' AND payload->>'share_id' = %s""",
                          (str(sh["share_id"]),))
    assert [(e["first"], e["views"], e["customer_kind"], e["customer_id"], e["subject_kind"]) for e in got] == [
        (True, 1, "crm.lead", str(lead["lead_id"]), "orderdesk.order"), (False, 2, "crm.lead", str(lead["lead_id"]), "orderdesk.order")]
    await events.drain()
    n = await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND kind = 'piece_opened' AND title LIKE %s", (sale.user_id, "Khách Mở Link%"))
    assert n == 1                                                                                                    # báo một lần — lượt sau chỉ đếm


async def test_an_extra_idea_lets_the_ai_write_only_prose_on_a_piece_built_from_the_record(who: Who) -> None:
    """"Thêm ý" (tuỳ chọn) trên ấn phẩm làm từ hồ sơ: AI chạy SAU khi dựng xong, được dặn giữ nguyên mọi khối số liệu — số vẫn chỉ từ hồ sơ."""
    import json

    from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    seen: list[str] = []

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        req = next(m for m in messages if isinstance(m, ModelRequest))
        seen.append(str(req.instructions or ""))
        prompt = json.loads(next(str(p.content) for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart)))
        keep = [{"type": b["type"], "ref": b["ref"], **({"text": b["text"]} if b["type"] in ("heading", "paragraph") else {})} for b in prompt["ban_hien_tai"]]
        out = {"title": "Ưu đãi riêng", "blocks": [{"type": "heading", "text": "Ưu đãi riêng tháng này"}, *keep]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, out, tool_call_id="o1")])

    o = await helpers.order(who, owner="sale1")
    sale = await who("sale1")
    compose.MODEL = FunctionModel(reply, model_name="compose-fake")
    try:
        out = await ok(sale, "compose.brochure", kind="demo_order", subject=o["order_id"], extra="Nhấn mạnh ưu đãi tháng này")
    finally:
        compose.MODEL = None
    assert out["status"] == "drafting"
    comp = await _composer(sale, out["session_id"])
    assert comp["status"] == "ready", comp["error"]
    assert seen and "dựng TỪ HỒ SƠ" in seen[0] and "GIỮ NGUYÊN mọi khối data" in seen[0]
    assert comp["blocks"][0]["text"] == "Ưu đãi riêng tháng này" and comp["version"] == 2
    data = [b for b in comp["blocks"] if b["type"] == "data"]
    assert len(data) == 1 and data[0]["source"]["query"] == "orderdesk.list" and data[0]["origin"] == "data"
    assert [t["role"] for t in comp["turns"]][-2:] == ["user", "assistant"] and comp["turns"][-2]["text"] == "Nhấn mạnh ưu đãi tháng này"
