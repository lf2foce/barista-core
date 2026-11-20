"""App tự ghép (0098): app là DỮ LIỆU (tham số + trang + khối = nguồn có sẵn / tập dữ liệu + cách hiện), mở app = chạy lại nguồn bằng quyền NGƯỜI
XEM; ghép từng khối, kiểm ngay; nguồn bị cắt không được đếm / cộng; app riêng từng người, ghim lên sidebar của người đó; hỏi trước bằng thẻ chọn."""

from typing import Any

from barista import workspaces
from barista.kernel import executor, ui
from barista.modules.apps import BlockError, _aggregate_guard
from tests.conftest import Who, ok, q


def _blocks(page: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [b for b in page["blocks"] if b["type"] == kind]


def test_a_source_that_may_be_cut_is_not_counted() -> None:
    try:
        _aggregate_guard({"kind": "table", "rows": [{"id": 1}], "columns": []})
    except BlockError:
        pass
    else:
        raise AssertionError("đếm trên nguồn có thể đã cắt phải bị từ chối")
    _aggregate_guard({"kind": "table", "rows": [], "columns": [], "total": 0})


async def test_brief_asks_before_building_and_survives_the_chat_preview(who: Who) -> None:
    page = await q(await who("giamdoc"), "apps.brief", title="Kế toán của giám đốc", summary="Xem dòng tiền và công nợ theo kiểu anh muốn",
                   outline=["Trang Bán hàng", "Trang Công nợ"],
                   questions=[{"key": "ky", "question": "Mặc định xem kỳ nào?", "options": ["Tháng này", "Quý này", "Năm nay"]},
                              {"key": "chieu", "question": "Chia số theo gì?", "options": ["Dự án", "Đội", "Sale"], "multi": True}])
    choices = _blocks(page, "choices")[0]
    assert [x["key"] for x in choices["questions"]] == ["ky", "chieu"] and choices["questions"][1]["multi"]
    small = ui.compact(page)
    assert small and any(b["type"] == "choices" for b in small["blocks"])   # thẻ gọn trong chat vẫn trả lời được


async def test_user_pins_come_first_and_only_for_that_person(who: Who) -> None:
    sale1, sale2 = await who("sale1"), await who("sale2")
    m = workspaces.manifest(sale1, "")
    ws, key = m["workspaces"][-1]["key"], m["workspaces"][-1]["screens"][0]["key"]
    await ok(sale1, "core.pin", workspace=ws, key=key)
    m1 = workspaces.manifest(sale1, "", mine=await workspaces.personal(sale1))
    assert {"workspace": ws, "key": key, "mine": True} in m1["pins"]
    flags = [bool(x.get("mine")) for x in m1["pins"]]
    assert flags == sorted(flags, reverse=True)
    m2 = workspaces.manifest(sale2, "", mine=await workspaces.personal(sale2))
    assert {"workspace": ws, "key": key, "mine": True} not in m2["pins"]
    await ok(sale1, "core.unpin", workspace=ws, key=key)


async def test_one_chat_turn_cannot_create_apps_in_bulk(who: Who) -> None:
    sale2 = await who("sale2")
    with executor.tracing("chat:bulk-turn"):
        ids = [(await ok(sale2, "apps.create", title=f"Hàng loạt {i}"))["app_id"] for i in range(2)]
        r = await executor.run(sale2, "apps.create", {"title": "Hàng loạt 3"})
    assert r.status == "invalid" and "tối đa" in (r.error or "")
    for i in ids:
        await ok(sale2, "apps.delete", app_id=i)


async def test_the_personal_assistant_never_carries_builder_tools_and_the_builder_carries_only_them(who: Who) -> None:
    """Trợ lý cá nhân và trình tạo app là HAI chế độ (0098): trợ lý không mang công cụ ghép app (chỉ "mở trình tạo app" — không tự dựng, không
    bị kéo sang dựng giao diện); trình tạo mang đúng bộ của nó (+ mở / danh sách app), lời dặn riêng, cuộc trò chuyện không lẫn vào lịch sử."""
    from barista.agent import chat
    from barista.kernel import registry
    from tests.test_assistant import Scripted
    from tests.test_resume import Log

    gd = await who("giamdoc")
    builder = {n.replace(".", "__") for n, op in registry.OPS.items() if registry.tool_kind(op) == "builder"} | {"apps__list"}
    assert {"apps__create", "apps__add_block", "apps__brief"} <= builder and "apps__revise_block" not in builder
    for q_ in ("tạo app theo dõi khách quá hạn", "theo dõi khách quá hạn của đội", "tổng giá trị giao dịch theo dự án"):
        model = Scripted(["Dạ."])
        await chat.chat_turn(gd, q_, emit=Log(), model=model)
        seen = model.tools_seen[0]
        assert not (seen & (builder - {"apps__list"})), (q_, seen & builder)
        assert "apps__handoff" in seen                                           # luôn có lối chuyển sang trình tạo
    model = Scripted(["Em hỏi anh vài câu nhé."])
    out = await chat.chat_turn(gd, "tạo app dòng tiền tháng sau", emit=Log(), model=model, mode="builder")
    assert model.tools_seen[0] == builder
    assert str(out["thread_id"]) not in {str(t["id"]) for t in await chat.list_threads(gd)}   # không lẫn vào lịch sử trợ lý
    handoff = await q(gd, "apps.handoff", request="tạo app dòng tiền tháng sau")
    small = ui.compact(handoff)
    assert handoff["ask"]["mode"] == "builder" and small and small["ask"]["mode"] == "builder"   # nút vẫn còn trên thẻ gọn trong chat


def test_extra_series_are_grouped_not_dropped() -> None:
    from barista.modules.apps import Block, _chart

    rows = [{"m": "2026-01", "s": f"S{i}", "v": 10 + i} for i in range(8)]
    page = {"kind": "table", "rows": rows, "total": 8, "columns": [
        {"key": "m", "label": "Tháng", "format": "text", "order": "asc"}, {"key": "s", "label": "Sale", "format": "text"},
        {"key": "v", "label": "Giá trị", "format": "money"}]}
    b = Block.model_validate({"kind": "chart", "source": {"query": "deals.list"}, "by": "m", "split": "s", "measure": "v"})
    chart = _chart(b, page, rows)
    assert len(chart["series"]) == 6 and chart["series"][-1]["name"] == "Khác"
    assert sum(s["values"][0] for s in chart["series"]) == sum(r["v"] for r in rows)   # không mất đồng nào


async def test_stop_cancels_the_running_turn_and_keeps_what_was_done(who: Who) -> None:
    """"Dừng": huỷ lượt đang chạy của CHÍNH người đó (mã lượt app gửi); phần đã làm được lưu, có mã cuộc trò chuyện để hỏi tiếp đúng mạch."""
    import asyncio

    from barista.agent import chat

    gd = await who("giamdoc")
    events: list[dict[str, Any]] = []
    started = asyncio.Event()

    async def emit(ev: dict[str, Any]) -> None:
        events.append(ev)

    class Slow:   # model giả chậm: lượt kẹt ở đây tới khi bị huỷ
        async def __call__(self, *a: Any, **k: Any) -> None:
            started.set()
            await asyncio.sleep(30)

    async def run() -> None:
        from tests.test_assistant import Scripted
        model = Scripted(["chưa tới đây"])
        orig = chat._run_agent

        async def slow_agent(*a: Any, **k: Any) -> Any:
            await Slow()()
            return await orig(*a, **k)
        chat._run_agent = slow_agent   # type: ignore[assignment]
        try:
            await chat.chat_turn(gd, "tổng hợp giúp tôi", emit=emit, model=model)
        finally:
            chat._run_agent = orig   # type: ignore[assignment]
    task = asyncio.create_task(run())
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    await task   # lượt tự kết thúc gọn (không đẩy huỷ ra ngoài)
    done = next(e for e in events if e["type"] == "done")
    assert done["stopped"] and done["thread_id"]
    t = await chat.load_thread(gd, __import__("uuid").UUID(done["thread_id"]))
    assert t and t["turns"][0]["text"] == "tổng hợp giúp tôi" and "dừng" in t["turns"][1]["text"]


async def test_a_workspace_of_a_pack_the_company_has_not_enabled_stays_hidden(who: Who) -> None:
    """Phân hệ thuộc gói của module sở hữu: công ty chưa bật gói ⇒ không có phân hệ, kể cả khi bên trong có màn dùng truy vấn gói nền (vd "Sổ
    sách" của gói dịch vụ kế toán từng lọt lên menu giám đốc sàn BĐS chỉ vì màn "Yêu cầu gửi khách")."""
    from barista.kernel import screens

    gd = await who("giamdoc")
    owned = {w.key: w.pack for w in screens.assemble()}
    assert owned["apps"] == "base" and owned["books"] == "books"
    m = workspaces.manifest(gd, "")
    keys = {w["key"] for w in m["workspaces"]}
    assert gd.packs is not None and "books" not in gd.packs and "books" not in keys


async def test_dictation_returns_text_only_and_starts_no_turn(who: Who, monkeypatch: Any) -> None:
    """Mic trong ô nhập: chép lời ⇒ CHỈ trả chữ (người xem / sửa rồi tự gửi) — không mở lượt trợ lý, không thêm cuộc trò chuyện."""
    from barista.agent import chat, field_note
    from tests.test_entry import _client, _h

    async def fake(p: Any, audio: bytes, media_type: str) -> str:
        return "khách Mai hẹn gọi lại chiều mai"
    monkeypatch.setattr(field_note, "transcribe", fake)
    gd = await who("giamdoc")
    before = len(await chat.list_threads(gd))
    async with _client() as c:
        r = await c.post("/v1/assistant/transcribe", content=b"RIFF....WAVE", headers={**_h(gd), "content-type": "audio/wav"})
        assert r.status_code == 200 and r.json() == {"text": "khách Mai hẹn gọi lại chiều mai"}
        bad = await c.post("/v1/assistant/transcribe", content=b"x", headers={**_h(gd), "content-type": "text/plain"})
        assert bad.status_code == 422
    assert len(await chat.list_threads(gd)) == before


async def test_builder_ideas_follow_what_each_role_can_read(who: Who) -> None:
    """Màn chào của trình tạo app: ý tưởng dựng từ tập dữ liệu NGƯỜI ĐÓ đọc được — vai khác ⇒ ý tưởng khác; không gợi ý nguồn không có quyền;
    sửa app ⇒ gợi ý sửa (không phải ý tưởng app mới)."""
    from barista.kernel import datasets
    from barista.kernel.executor import can_run

    seen: dict[str, list[str]] = {}
    for role in ("ketoan", "mkt", "giamdoc"):
        p = await who(role)
        out = await q(p, "apps.builder_ideas")
        assert out["mode"] == "new" and 3 <= len(out["ideas"]) <= 5, (role, out)
        used = [i["dataset"] for i in out["ideas"] if i["dataset"]]
        assert all(can_run(p, datasets.DATASETS[d].query) for d in used), (role, used)   # không gợi ý nguồn người đó không xem được
        assert all(i["text"].strip() for i in out["ideas"])
        seen[role] = used
    assert seen["ketoan"] != seen["mkt"]                                                 # vai khác ⇒ ý tưởng khác
    gd = await who("giamdoc")
    app = await ok(gd, "apps.create", title="Ý tưởng sửa")
    try:
        edit = await q(gd, "apps.builder_ideas", app_id=app["app_id"])
        assert edit["mode"] == "edit" and edit["ideas"] and all(i["text"].startswith("Thêm") for i in edit["ideas"])
    finally:
        await ok(gd, "apps.delete", app_id=app["app_id"])
