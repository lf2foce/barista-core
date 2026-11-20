"""APP TỰ GHÉP (0098) trên nguồn của một gói ngành, qua gói thế vai: bản LÕI (ADR 0190) của ``barista-packs:real_estate/tests/test_apps.py`` — app là DỮ LIỆU
(tham số + trang + khối = nguồn có sẵn / tập dữ liệu + cách hiện), mở app = chạy lại nguồn bằng quyền NGƯỜI XEM; ghép từng khối, kiểm ngay; một lượt trò
chuyện = một phiên bản; khối mất quyền hỏng riêng; sửa một khối bằng lời chỉ đụng khối đó. Công ty thế vai không có dữ liệu nạp sẵn ⇒ test tự dựng đơn."""

from datetime import date
from typing import Any

from psycopg.types.json import Jsonb

from barista import workspaces
from barista.kernel import executor
from barista.kernel.clock import today
from barista.modules.apps import period_range
from tests.conftest import Who, ok, owner_val, q
from tests.standin import helpers
from tests.test_apps import _blocks


def _kpi(page: dict[str, Any], label: str) -> dict[str, Any]:
    return next(k for b in _blocks(page, "kpis") for k in b["items"] if k["label"] == label)


async def _app(p: Any, title: str, blocks: list[dict[str, Any]], **frame: Any) -> str:
    a = await ok(p, "apps.create", title=title, **frame)
    page = (frame.get("pages") or [{"key": "main"}])[0]["key"]
    for b in blocks:
        await ok(p, "apps.add_block", app_id=a["app_id"], page=page, block=b)
    return a["app_id"]


async def _orders(who: Who) -> None:
    """Đơn mở + đã ký của hai người (sale1, sale3), hai dòng dịch vụ; một đơn ký NĂM TRƯỚC (ngoài kỳ "năm nay")."""
    await helpers.order(who, owner="sale1", amount=1_200_000_000)
    await helpers.signed(who, owner="sale1", amount=2_500_000_000)
    await helpers.signed(who, owner="sale3", amount=4_000_000_000, line="LD")
    await helpers.signed(who, owner="sale3", amount=900_000_000, signed_on=date(today().year - 1, 6, 15))
    await helpers.order(who, owner="sale3", amount=700_000_000, line="LD")


ORDERS = [
    {"kind": "source_kpis", "source": {"query": "orderdesk.list"}},
    {"kind": "kpi", "title": "Đơn của tôi", "source": {"query": "orderdesk.list"}},
    {"kind": "chart", "title": "Theo trạng thái", "source": {"query": "orderdesk.list"}, "by": "status"},
    {"kind": "table", "title": "Mới nhất", "source": {"query": "orderdesk.list"}, "columns": ["code", "customer", "amount_vnd"], "rows": 5,
     "labels": {"customer": "Khách"}, "sort": "-amount_vnd"},
]


async def test_inspect_shows_real_columns_and_the_buttons_the_module_already_declared(who: Who) -> None:
    d = await q(await who("sale1"), "apps.inspect", query="orderdesk.list")
    assert d["complete"] is True and d["returns"] == "table" and "kind" not in d   # không giả làm một trang (thẻ gọn trong chat)
    assert {"code", "status", "amount_vnd"} <= {c["key"] for c in d["columns"]}
    assert d["open"] and d["open"]["query"]
    ds = await q(await who("giamdoc"), "apps.datasets")
    assert {"orderdesk.orders", "acct.receivables", "core.ledger"} <= {x["dataset"] for x in ds}


async def test_build_block_by_block_and_open_with_the_viewers_rights(who: Who) -> None:
    await _orders(who)
    sale1, gd = await who("sale1"), await who("giamdoc")
    mine = await _app(sale1, "Đơn của tôi", ORDERS)
    theirs = await _app(gd, "Đơn công ty", ORDERS)
    page_s = await q(sale1, "apps.open", app_id=mine)
    page_g = await q(gd, "apps.open", app_id=theirs)
    assert 0 < _kpi(page_s, "Đơn của tôi")["value"] < _kpi(page_g, "Đơn của tôi")["value"]   # cùng định nghĩa, RLS người xem
    assert _kpi(page_s, "Đơn của tôi")["value"] == len((await q(sale1, "orderdesk.list"))["rows"])
    table = _blocks(page_s, "table")[0]["page"]
    assert [c["key"] for c in table["columns"]] == ["code", "customer", "amount_vnd"] and table["columns"][1]["label"] == "Khách"
    prices = [r["amount_vnd"] for r in table["rows"]]
    assert prices == sorted(prices, reverse=True) and 2 <= len(prices) <= 5
    assert _blocks(page_s, "chart") and not _blocks(page_s, "callout")
    assert _kpi(page_s, "Đơn của tôi")["drill"]["query"] == "orderdesk.list"
    # không trong lượt trò chuyện ⇒ mỗi bước một phiên bản; app riêng: người khác không mở được, không thấy trong menu
    assert await owner_val("SELECT count(*) FROM apps.versions WHERE app_id = %s", (mine,)) == 1 + len(ORDERS)
    assert (await executor.query(gd, "apps.open", {"app_id": mine})).status != "ok"
    m = workspaces.manifest(sale1, "", mine=await workspaces.personal(sale1))
    screen = next(s for w in m["workspaces"] if w["key"] == "apps" for s in w["screens"] if s["key"] == f"app-{mine}")
    assert screen["query"] == "apps.open" and screen["input"] == {"app_id": str(mine)} and screen["filters"] == []
    assert {"workspace": "apps", "key": screen["key"], "mine": True} in m["pins"]
    m_gd = workspaces.manifest(gd, "", mine=await workspaces.personal(gd))
    assert screen["key"] not in {s["key"] for w in m_gd["workspaces"] for s in w["screens"]}


async def test_params_pages_and_datasets_make_a_real_app(who: Who) -> None:
    await _orders(who)
    gd = await who("giamdoc")
    app = await _app(gd, "Kế toán của giám đốc", [
        {"kind": "kpi", "title": "Giá trị đã ký", "source": {"dataset": "orderdesk.orders", "measures": ["value"], "where": {"status": ["signed"]},
                                                              "date": "signed_on", "period": "$ky"}},
        {"kind": "chart", "title": "Giá trị theo tháng ký × dòng dịch vụ", "chart": "line",
         "source": {"dataset": "orderdesk.orders", "measures": ["value"], "by": ["signed_month", "line"], "where": {"line": "$dong"},
                    "date": "signed_on", "period": "$ky"}},
    ], params=[{"key": "ky", "label": "Kỳ", "kind": "period", "default": "this_year"},
               {"key": "dong", "label": "Dòng dịch vụ", "options_query": "orderdesk.line_options"}],
        pages=[{"key": "ban_hang", "title": "Bán hàng"},
               {"key": "cong_no", "title": "Công nợ", "actions": [{"command": "flow.create_task", "title": "Giao việc thu nợ"}]}])
    await ok(gd, "apps.add_block", app_id=app, page="cong_no", block={
        "kind": "chart", "title": "Còn phải thu theo tình trạng", "source": {"dataset": "acct.receivables", "measures": ["remaining"], "by": ["collect"]}})
    page = await q(gd, "apps.open", app_id=app)
    assert page["tabs"]["current"] == "ban_hang" and [t["key"] for t in page["tabs"]["items"]] == ["ban_hang", "cong_no"]
    signed = _kpi(page, "Giá trị đã ký")
    lo, hi = period_range("this_year")
    assert lo and hi
    assert signed["drill"]["input"]["signed_from"] == lo.isoformat() and signed["drill"]["input"]["signed_before"] == hi.isoformat()
    chart = _blocks(page, "chart")[0]
    assert chart["kind"] == "line" and len(chart["series"]) >= 1
    assert chart["labels"] == sorted(chart["labels"], key=lambda s: (s.split("/")[1], int(s.split("/")[0][1:])))   # tháng tăng dần
    # tham số ⇒ bộ lọc của màn; đổi kỳ ⇒ số theo kỳ mới; trang khác ⇒ khối + nút thanh công cụ của trang đó
    m = workspaces.manifest(gd, "", mine=await workspaces.personal(gd))
    screen = next(s for w in m["workspaces"] for s in w["screens"] if s["key"] == f"app-{app}")
    assert [f["key"] for f in screen["filters"]] == ["ky", "dong"] and screen["filters"][0]["default"] == "this_year"
    assert screen["filters"][1]["options_query"] == "orderdesk.line_options"
    all_time = await q(gd, "apps.open", app_id=app, ky="all")
    assert _kpi(all_time, "Giá trị đã ký")["value"] > signed["value"] > 0 and "drill" in _kpi(all_time, "Giá trị đã ký")   # đơn ký năm trước chỉ vào "all"
    one_line = await q(gd, "apps.open", app_id=app, ky="all", dong="LD")
    assert len(_blocks(one_line, "chart")[0]["series"]) == 1 < len(_blocks(all_time, "chart")[0]["series"])              # tham số chọn vào bộ lọc của khối
    debt = await q(gd, "apps.open", app_id=app, page="cong_no")
    assert _blocks(debt, "chart") and [a["command"] for a in debt["actions"]] == ["flow.create_task"]


async def test_each_step_is_checked_and_a_chat_turn_is_one_version(who: Who) -> None:
    sale1 = await who("sale1")
    app = await _app(sale1, "Thử kiểm", [])
    for block, says in (
        ({"kind": "table", "source": {"query": "orderdesk.list"}, "columns": ["khong_co"]}, "khong_co"),
        ({"kind": "table", "source": {"query": "orderdesk.list"}, "row_actions": [{"command": "orderdesk.sign", "bind": {"code": "ma_sai"}}]}, "ma_sai"),
        ({"kind": "kpi", "source": {"query": "orderdesk.list"}, "measure": "sum:khong_co"}, "khong_co"),
        ({"kind": "table", "source": {"query": "apps.list"}}, "không phải truy vấn"),
        ({"kind": "table", "source": {"query": "admin.users"}}, "quyền"),
        ({"kind": "kpi", "source": {"dataset": "orderdesk.orders", "period": "$ky"}}, "tham số"),
        ({"kind": "kpi", "source": {"dataset": "khong.co"}}, "tập dữ liệu"),
    ):
        r = await executor.run(sale1, "apps.add_block", {"app_id": app, "block": block})
        assert r.status == "invalid" and says in (r.error or ""), (block, r.status, r.error)
    with executor.tracing("chat:test-turn"):
        await ok(sale1, "apps.add_block", app_id=app, block=ORDERS[1])
        await ok(sale1, "apps.add_block", app_id=app, block=ORDERS[2])
        await ok(sale1, "apps.remove_block", app_id=app, index=1)
    versions = await owner_val("SELECT array_agg(version ORDER BY version) FROM apps.versions WHERE app_id = %s", (app,))
    assert versions == [1, 2]                                  # khung + MỘT phiên bản cho cả lượt trò chuyện
    page = await q(sale1, "apps.open", app_id=app)
    assert _blocks(page, "chart") and not _blocks(page, "kpis")
    await ok(sale1, "apps.delete", app_id=app)
    assert f"app-{app}" not in {p["key"] for p in await q(sale1, "core.pins")}


async def test_a_block_the_viewer_can_no_longer_open_degrades_alone(who: Who) -> None:
    sale1 = await who("sale1")
    app = await _app(sale1, "Mất quyền", [ORDERS[1]])
    bad = {"pages": [{"key": "main", "title": "Tổng quan", "blocks": [ORDERS[1], {"kind": "table", "title": "Người dùng", "source": {"query": "admin.users"}}]}]}
    await owner_val("UPDATE apps.items SET spec = %s WHERE id = %s RETURNING 1", (Jsonb(bad), app))   # như quyền vừa bị thu
    page = await q(sale1, "apps.open", app_id=app)
    assert _blocks(page, "kpis") and "quyền" in _blocks(page, "callout")[0]["text"]
    assert "admin.users" not in str(page["blocks"])


async def test_a_block_filter_on_a_dataset_reaches_the_number(who: Who) -> None:
    """Lọc đặt ở KHỐI (không ở nguồn) trên tập dữ liệu vẫn vào con số — không lấy ô tổng chưa lọc của nguồn."""
    await _orders(who)
    gd = await who("giamdoc")
    app = await _app(gd, "Lọc khối", [
        {"kind": "kpi", "title": "Đã ký (lọc ở khối)", "source": {"dataset": "orderdesk.orders", "measures": ["value"]}, "where": {"status": ["signed"]}},
        {"kind": "kpi", "title": "Tất cả", "source": {"dataset": "orderdesk.orders", "measures": ["value"]}},
    ])
    page = await q(gd, "apps.open", app_id=app)
    signed = (await q(gd, "orderdesk.ds_orders", measures=["value"], where={"status": ["signed"]}))["kpis"][0]["value"]
    assert 0 < _kpi(page, "Đã ký (lọc ở khối)")["value"] == signed < _kpi(page, "Tất cả")["value"]
    await ok(gd, "apps.delete", app_id=app)


async def test_revise_one_block_by_words_touches_only_that_block(who: Who) -> None:
    """"Sửa khối này": model chỉ trả MỘT khối (đầu ra có kiểu) thay cho đúng khối đó; khối trả về lỗi ⇒ model nhận lỗi và sửa lại; khối khác,
    trang khác, tham số giữ nguyên. App mở ra có nút sửa trên từng khối (ô số: từng ô)."""
    import json

    from pydantic_ai import ModelMessage, ModelResponse, TextPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from barista.modules import apps

    gd = await who("giamdoc")
    app = await _app(gd, "Sửa một khối", [ORDERS[1], ORDERS[2], ORDERS[3]])
    page = await q(gd, "apps.open", app_id=app)
    assert _kpi(page, "Đơn của tôi")["edit"]["preset"] == {"app_id": str(app), "page": "main", "index": 1}
    assert _blocks(page, "chart")[0]["edit"]["command"] == "apps.revise_block"
    before = await owner_val("SELECT spec FROM apps.items WHERE id = %s", (app,))
    calls: list[int] = []

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        bad = {"kind": "chart", "title": "Theo dòng dịch vụ", "source": {"query": "orderdesk.list"}, "by": "khong_co"}
        good = {"kind": "chart", "title": "Theo dòng dịch vụ", "source": {"query": "orderdesk.list"}, "by": "line"}
        return ModelResponse(parts=[TextPart(json.dumps(bad if len(calls) == 1 else good))])   # trả JSON theo lược đồ (PromptedOutput)

    apps.REVISE_MODEL = FunctionModel(reply, model_name="revise-fake")
    try:
        await ok(gd, "apps.revise_block", app_id=app, page="main", index=2, instruction="Chia theo dòng dịch vụ thay vì trạng thái")
    finally:
        apps.REVISE_MODEL = None
    assert len(calls) == 2                                     # lần đầu lỗi cột ⇒ model nhận lỗi, sửa lại
    after = await owner_val("SELECT spec FROM apps.items WHERE id = %s", (app,))
    b0, b1 = before["pages"][0]["blocks"], after["pages"][0]["blocks"]
    assert b1[1]["by"] == "line" and b1[1]["title"] == "Theo dòng dịch vụ"
    assert b1[0] == b0[0] and b1[2] == b0[2] and after.get("params", []) == before.get("params", [])   # khối khác không đổi
    assert (await executor.run(gd, "apps.revise_block", {"app_id": app, "page": "main", "index": 9, "instruction": "x"})).status == "invalid"
