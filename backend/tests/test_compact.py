"""Thẻ gọn (0094 đợt 2): CHÍNH trang đó, server cắt bớt — ≤ 3 dòng theo thứ tự module đã xếp, ≤ 3 cột, ô số chỉ khi không có dòng; "3/28" chỉ khi biết tổng thật;
nút trên dòng / khối giữ nguyên (đã lọc quyền); trong chat cắt từ kết quả vừa chạy, không truy vấn lại."""

from typing import Any

from httpx import ASGITransport, AsyncClient

from barista.agent import chat
from barista.http_api import app
from barista.kernel import ui
from tests.conftest import Who, q
from tests.test_assistant import Scripted, call
from tests.test_resume import Log


def _rows(n: int) -> list[dict[str, Any]]:
    return [{"id": i, "name": f"Khách {i}", "phone": "010", "project": "HSR", "stage": "lead", "note": "x"} for i in range(n)]


COLS = [ui.col("name", "Khách"), ui.col("phone", "SĐT"), ui.col("project", "Dự án"), ui.col("note", "Ghi chú"),
        ui.col("stage", "Chặng", "badge")]


def test_table_keeps_order_few_columns_and_knows_the_total_only_when_it_is_real() -> None:
    full = ui.table(_rows(28), COLS, kpis=[ui.kpi(str(i), i) for i in range(5)], limit=400,
                    views=ui.views(board=ui.board_view("stage", "Theo chặng")))
    small = ui.compact(full)
    assert small is not None
    assert [r["id"] for r in small["rows"]] == [0, 1, 2]                             # thứ tự module đã xếp, không đảo
    assert [c["key"] for c in small["columns"]] == ["name", "phone", "project", "stage"] and small["kpis"] == []   # có dòng ⇒ bỏ ô số
    assert small["compact"] == {"shown": 3, "total": 28} and "views" not in small
    empty = ui.compact(ui.table([], COLS, kpis=[ui.kpi(str(i), i) for i in range(5)], limit=400))
    assert empty is not None and len(empty["kpis"]) == 3                               # không có dòng ⇒ ô số là nội dung
    capped = ui.compact(ui.table(_rows(400), COLS, limit=400))                         # chạm trần ⇒ không biết tổng ⇒ "5 dòng đầu"
    assert capped is not None and capped["compact"] == {"shown": 3, "total": None}
    unknown = ui.compact(ui.table(_rows(12), COLS))                                    # không khai trần ⇒ không dám nói tổng
    assert unknown is not None and unknown["compact"]["total"] is None


def test_card_spends_one_row_budget_across_blocks_and_drops_heavy_ones() -> None:
    a = ui.act("crm.log_activity", "Ghi đã gọi")
    page = ui.card("Hôm nay", [
        ui.b_kpis([ui.kpi("a", 1), ui.kpi("b", 2), ui.kpi("c", 3), ui.kpi("d", 4)]),
        ui.b_list([{"title": f"Hẹn {i}"} for i in range(3)]),
        *[{**ui.b_callout("warn", f"Gọi {i}"), "actions": [a]} for i in range(4)],
        ui.b_chart("bar", ["x"], [ui.line("y", [1])]),
        ui.b_text("Đoạn dài"),
    ])
    small = ui.compact(page, 5)
    assert small is not None
    kinds = [b["type"] for b in small["blocks"]]
    assert kinds == ["list", "callout", "callout"]                                     # có dòng ⇒ bỏ ô số; biểu đồ / đoạn văn bỏ
    assert small["blocks"][1]["actions"] == [a]                                        # nút của khối giữ nguyên
    assert small["compact"] == {"shown": 5, "total": 7}
    assert (small["title"], small["actions"], "ask" in small) == ("", [], False)       # thanh công cụ / hỏi trợ lý ở bản đủ
    assert ui.compact({"kind": "detail", "title": "x", "sections": []}) is None        # trang chi tiết: chỉ có nút Mở


async def test_compact_over_the_api_is_a_subset_of_the_full_page_with_the_same_buttons(who: Who) -> None:
    sale1 = await who("sale1")
    full = await q(sale1, "crm.brief")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/v1/queries/crm.brief?compact=1", json={}, headers={"X-Dev-User": sale1.email, "X-Tenant-Id": str(sale1.tenant_id)})
    small = r.json()["output"]
    assert small["compact"]["shown"] <= 3
    full_blocks = [b for b in full["blocks"] if b["type"] != "kpis"]
    for b in (x for x in small["blocks"] if x["type"] != "kpis"):
        assert any(b["title"] == f["title"] and b.get("actions") == f.get("actions") for f in full_blocks)   # bản gọn ⊆ bản đủ, cùng nút


async def test_chat_card_carries_a_preview_cut_from_the_result_it_just_ran(who: Who) -> None:
    sale1 = await who("sale1")
    log = Log()
    await chat.chat_turn(sale1, "Hôm nay tôi gọi ai?", emit=log, model=Scripted([[call("crm.brief")], "Anh xem thẻ bên dưới ạ."]))
    card = next(e["card"] for e in log.of("card") if e["card"]["kind"] == "page")
    assert card["preview"]["compact"]["shown"] <= 3 and card["page"]["blocks"]          # bản đủ vẫn còn để "Mở"


async def test_later_blocks_are_filled_in_for_callers_that_cannot_load_them(who: Who, monkeypatch: Any) -> None:
    """Khối tải sau (``ui.b_later``, 18/11/2026): bên gọi không tự tải (app khác, trợ lý, thẻ gọn) nhận thẻ đã ghép — các khối của truy vấn được trỏ
    thay đúng chỗ, theo thứ tự; truy vấn không có quyền / không có ⇒ bỏ phần đó; lỗi ⇒ một dòng báo; thẻ trỏ vòng vào nhau không lặp mãi. Web
    (``?later=1``) nhận nguyên khối để tự tải."""
    from barista.kernel import executor

    boss = await who("giamdoc")
    real = executor._query
    cards = {
        "t.main": ui.card("Chính", [ui.b_kpis([ui.kpi("A", 1)]), ui.b_later("t.slow", {"x": 1}, title="Chậm"), ui.b_later("t.denied"),
                                   ui.b_later("t.broken", title="Hỏng"), ui.b_later("t.loop"), ui.b_text("cuối")]),
        "t.slow": ui.card("Chậm", [ui.b_text("chậm 1"), ui.b_text("chậm 2")]),
        "t.loop": ui.card("Vòng", [ui.b_text("vòng"), ui.b_later("t.loop")]),
    }

    async def fake(p: Any, name: str, inp: dict[str, Any] | None = None, *, quiet: bool = False) -> executor.Result:
        if name in cards:
            return executor.Result("ok", cards[name])
        if name == "t.denied":
            return executor.Result("denied", error="không")
        if name == "t.broken":
            return executor.Result("error", error="hỏng")
        return await real(p, name, inp, quiet=quiet)
    monkeypatch.setattr(executor, "_query", fake)
    full = (await executor.query(boss, "t.main", {})).output["blocks"]
    assert [b.get("markdown") or b.get("title") or b["type"] for b in full] == ["kpis", "chậm 1", "chậm 2", "Hỏng", "vòng", "vòng", "cuối"]
    fast = (await executor.query(boss, "t.main", {}, later=True)).output["blocks"]
    assert [b["type"] for b in fast] == ["kpis", "later", "later", "later", "later", "text"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        h = {"X-Dev-User": boss.email, "X-Tenant-Id": str(boss.tenant_id)}
        web = (await c.post("/v1/queries/t.main?later=1", json={}, headers=h)).json()["output"]["blocks"]
        mac = (await c.post("/v1/queries/t.main", json={}, headers=h)).json()["output"]["blocks"]
    assert [b["type"] for b in web] == [b["type"] for b in fast] and mac == full


async def test_parts_load_one_at_a_time_on_the_web_and_whole_for_everyone_else(who: Who, monkeypatch: Any) -> None:
    """Phần tải sau trong CÙNG truy vấn (``ui.part``, 18/11/2026): web (``?later=1``) nhận ô số + chỗ chờ, mỗi ``?part=`` chạy lại phần nhanh và dựng
    ĐÚNG phần được hỏi (phần khác không chạy, phần sau không chạy); bên gọi khác nhận thẻ đủ, cùng thứ tự; gọi lồng luôn nhận thẻ đủ."""
    from barista.kernel import executor

    boss = await who("giamdoc")
    built: list[str] = []
    real = executor._query

    def maker(key: str, n: int):
        async def make() -> list[dict[str, Any]]:
            built.append(key)
            return [ui.b_text(f"{key} {i}") for i in range(n)]
        return make

    async def handler() -> dict[str, Any]:
        blocks = [ui.b_kpis([ui.kpi("A", 1)])]
        blocks += await ui.part("one", maker("one", 2), title="Một")
        blocks.append(ui.b_text("giữa"))
        blocks += await ui.part("two", maker("two", 1), title="Hai")
        blocks += await ui.part("empty", maker("empty", 0), title="Rỗng")
        return ui.card("Thử", blocks)

    async def fake(p: Any, name: str, inp: dict[str, Any] | None = None, *, quiet: bool = False) -> executor.Result:
        if name != "t.parts":
            return await real(p, name, inp, quiet=quiet)
        out: Any = None
        try:
            out = await handler()
        except ui.PartDone:
            pass
        if (st := ui._PARTS.get()) is not None and st.want is not None:
            out = ui.card("", st.got or [])
        return executor.Result("ok", out)
    monkeypatch.setattr(executor, "_query", fake)
    text = lambda bl: [b.get("markdown") or b["type"] for b in bl]   # noqa: E731
    full = (await executor.query(boss, "t.parts", {"x": 1})).output["blocks"]
    assert text(full) == ["kpis", "one 0", "one 1", "giữa", "two 0"] and built == ["one", "two", "empty"]
    built.clear()
    fast = (await executor.query(boss, "t.parts", {"x": 1}, later=True)).output["blocks"]
    assert [(b["type"], b.get("part")) for b in fast] == [("kpis", None), ("later", "one"), ("text", None), ("later", "two"), ("later", "empty")]
    assert fast[1] == {"type": "later", "query": "t.parts", "input": {"x": 1}, "part": "one", "title": "Một"} and built == []
    two = (await executor.query(boss, "t.parts", {"x": 1}, part="two")).output["blocks"]
    assert text(two) == ["two 0"] and built == ["two"]                                 # chỉ dựng phần được hỏi
    assert (await executor.query(boss, "t.parts", {"x": 1}, part="nope")).output["blocks"] == []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        h = {"X-Dev-User": boss.email, "X-Tenant-Id": str(boss.tenant_id)}
        web = (await c.post("/v1/queries/t.parts?part=one", json={"x": 1}, headers=h)).json()["output"]["blocks"]
    assert text(web) == ["one 0", "one 1"]


async def test_server_pages_of_a_table_count_the_whole_set_and_old_apps_get_every_row(who: Who) -> None:
    """Trang phía server (``?limit=&offset=``): một trang + ``paging.total`` = cả tập lọc; ô số giữ nguyên (tính trên cả tập); không xin trang (app cũ,
    Excel) ⇒ đủ dòng như cũ; bảng có Kanban / lịch không chia trang; trang tự làm ở SQL (``ui.paging`` + ``paged_total``) không bị cắt lần hai."""
    rows = [{"id": i, "n": i} for i in range(25)]
    page = ui.table(rows, [ui.col("n", "N", "number")], kpis=[ui.kpi("Tổng", 300)], limit=100)
    got = ui.slice_page(page, ui.Paging(10, 10))
    assert [r["id"] for r in got["rows"]] == list(range(10, 20)) and got["kpis"] == page["kpis"]
    assert got["paging"] == {"offset": 10, "limit": 10, "total": 25, "complete": True} and got["total"] == 25
    capped = ui.slice_page(ui.table(rows, [ui.col("n", "N", "number")], limit=25), ui.Paging(0, 10))
    assert capped["paging"]["complete"] is False                                       # truy vấn chạm trần của nó — app nói "N dòng đầu"
    board = ui.slice_page(ui.table(rows, [ui.col("n", "N", "number")], views={"board": {"by": "n"}}), ui.Paging(0, 10))
    assert len(board["rows"]) == 10 and "board" in board["views"]                    # góc nhìn vẫn khai; web xin đủ dòng khi mở Kanban / lịch
    token = ui._PAGING.set(ui.Paging(300, 300))
    try:
        sql_paged = ui.table(rows[:5], [ui.col("n", "N", "number")], limit=2000, paged_total=2305)
    finally:
        ui._PAGING.reset(token)
    assert sql_paged["paging"] == {"offset": 300, "limit": 300, "total": 2305, "complete": True}
    assert ui.slice_page(sql_paged, ui.Paging(300, 300)) is sql_paged
    assert "paging" not in ui.table(rows, [ui.col("n", "N", "number")], paged_total=25)  # không ai xin trang ⇒ bảng như cũ

    boss = await who("giamdoc")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        h = {"X-Dev-User": boss.email, "X-Tenant-Id": str(boss.tenant_id)}
        whole = (await c.post("/v1/queries/crm.leads", json={}, headers=h)).json()["output"]
        one = (await c.post("/v1/queries/crm.leads?limit=2&offset=1", json={}, headers=h)).json()["output"]
        bad = await c.post("/v1/queries/crm.leads?limit=x", json={}, headers=h)
    assert len(whole["rows"]) > 3 and "paging" not in whole
    # cùng tập (thứ tự trong các dòng bằng nhau về khoá sắp xếp có thể khác giữa hai lượt — module cần khoá phụ ổn định nếu muốn trang khít tuyệt đối)
    assert len(one["rows"]) == 2 and {r["id"] for r in one["rows"]} <= {r["id"] for r in whole["rows"]}
    assert one["paging"] == {"offset": 1, "limit": 2, "total": len(whole["rows"]), "complete": "total" in whole} and one["kpis"] == whole["kpis"]
    assert bad.status_code == 422
