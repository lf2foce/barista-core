"""Bảng sửa khối kiểu BI (0220 §bảng sửa): người CHỌN trường thay vì nói — danh mục trường theo quyền người sửa, xem trước KHÔNG lưu, lưu bằng đúng
lệnh update_block / add_block như trợ lý; chỉ chủ app mở được."""

from barista.kernel import executor
from tests.conftest import Who, ok, q

CHART = {"kind": "chart", "title": "Lead theo chặng", "chart": "bar", "measure": "count", "by": "stage",
         "source": {"dataset": "crm.leads", "measures": ["count"], "by": ["stage"]}}


async def test_design_lists_what_can_be_picked_and_only_for_the_owner(who: Who) -> None:
    gd, sale1 = await who("giamdoc"), await who("sale1")
    app = await ok(gd, "apps.create", title="Bảng sửa khối")
    await ok(gd, "apps.add_block", app_id=app["app_id"], page="main", block=CHART)
    d = await q(gd, "apps.block_design", app_id=app["app_id"], page="main", index=1)
    assert d["block"]["source"]["dataset"] == "crm.leads" and d["block"]["chart"] == "bar"
    leads = next(x for x in d["datasets"] if x["dataset"] == "crm.leads")
    assert {"stage", "month"} <= set(leads["dims"]) and leads["measures"]["win_rate"].get("ratio")
    assert "[[" not in str(leads)                                            # nhãn công ty đã đổi ở cửa ra
    assert d["charts"]["hbar"] and d["index"] == 1 and d["blocks"] == 1
    new = await q(gd, "apps.block_design", app_id=app["app_id"], page="main")
    assert new["block"] is None and new["index"] is None
    r = await executor.query(sale1, "apps.block_design", {"app_id": app["app_id"], "page": "main", "index": 1})
    assert r.status == "not_found"                                           # app người khác: không thấy
    await ok(gd, "apps.delete", app_id=app["app_id"])


async def test_preview_runs_the_edited_block_without_saving(who: Who) -> None:
    gd = await who("giamdoc")
    app = await ok(gd, "apps.create", title="Xem trước khối")
    await ok(gd, "apps.add_block", app_id=app["app_id"], page="main", block=CHART)
    before = await q(gd, "apps.describe", app_id=app["app_id"])
    edited = {**CHART, "chart": "hbar", "measure": "win_rate", "source": {**CHART["source"], "measures": ["win_rate"]}, "w": 4}
    page = await q(gd, "apps.preview_block", app_id=app["app_id"], page="main", index=1, block=edited)
    [chart] = [b for b in page["blocks"] if b["type"] == "chart"]
    assert chart["kind"] == "hbar" and "w" not in chart and "edit" not in chart   # hết bề ngang khung xem trước, không nút sửa
    assert chart["unit"] == "pct"                                            # tỷ lệ ⇒ trục in “%”, không phải số đếm
    assert all(v is None or 0 <= v <= 100 for v in chart["series"][0]["values"])
    assert await q(gd, "apps.describe", app_id=app["app_id"]) == before       # chưa lưu gì
    bad = {**CHART, "by": "khong_co"}
    page = await q(gd, "apps.preview_block", app_id=app["app_id"], page="main", index=1, block=bad)
    assert page["blocks"][0]["type"] == "callout" and "Chưa mở được" in page["blocks"][0]["text"]   # lỗi hiện trong khối, không vỡ bảng sửa
    added = await q(gd, "apps.preview_block", app_id=app["app_id"], page="main",
                    block={"kind": "kpi", "title": "Số lead", "measure": "count", "source": {"dataset": "crm.leads", "measures": ["count"]}})
    assert [b["type"] for b in added["blocks"]] == ["kpis"]                  # khối mới: chỉ khối đó
    await ok(gd, "apps.update_block", app_id=app["app_id"], page="main", index=1, block=edited)   # lưu = cùng lệnh trợ lý dùng
    assert (await q(gd, "apps.describe", app_id=app["app_id"])) != before
    await ok(gd, "apps.delete", app_id=app["app_id"])


async def test_dimension_values_come_from_rows_the_editor_can_see(who: Who) -> None:
    gd = await who("giamdoc")
    stages = await q(gd, "apps.dim_values", dataset="crm.leads", dim="stage")
    assert stages and all(set(x) == {"value", "label"} for x in stages)      # chiều có nhãn cố định ⇒ danh sách nhãn
    teams = await q(gd, "apps.dim_values", dataset="crm.leads", dim="team")
    assert all(x["value"] for x in teams)                                     # giá trị trống không thành lựa chọn
    months = await q(gd, "apps.dim_values", dataset="crm.leads", dim="month")
    assert all(x["label"].startswith("T") for x in months)
    r = await executor.query(gd, "apps.dim_values", {"dataset": "crm.leads", "dim": "khong_co"})
    assert r.status == "invalid"


async def test_compose_adds_one_block_from_words_and_retries_on_a_bad_block(who: Who) -> None:
    """Ô "nói muốn xem gì" (0220 lát 4): model chỉ trả MỘT khối ghép từ tập dữ liệu người dùng xem được; khối lỗi ⇒ model nhận lỗi và sửa; thêm cuối trang."""
    import json

    from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from barista.modules import apps

    gd, sale1 = await who("giamdoc"), await who("sale1")
    app = await ok(gd, "apps.create", title="Dựng khối bằng lời")
    await ok(gd, "apps.add_block", app_id=app["app_id"], page="main", block=CHART)
    seen: list[str] = []

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(str(getattr(messages[0].parts[-1], "content", "")) if len(seen) == 0 else "retry")
        bad = {**CHART, "title": "Tỷ lệ theo đội", "by": "khong_co"}
        good = {"kind": "chart", "chart": "hbar", "title": "Tỷ lệ thành giao dịch theo đội", "measure": "win_rate", "by": "team",
                "source": {"dataset": "crm.leads", "measures": ["win_rate"], "by": ["team"]}, "w": 6}
        return ModelResponse(parts=[TextPart(json.dumps(bad if len(seen) == 1 else good))])

    apps.REVISE_MODEL = FunctionModel(reply, model_name="compose-fake")
    try:
        out = await ok(gd, "apps.compose_block", app_id=app["app_id"], page="main", instruction="tỷ lệ thành giao dịch theo đội")
        r = await executor.run(sale1, "apps.compose_block", {"app_id": app["app_id"], "page": "main", "instruction": "x"})
    finally:
        apps.REVISE_MODEL = None
    assert out["index"] == 2 and out["blocks"] == 2 and len(seen) == 2          # lần đầu lỗi chiều ⇒ sửa lại; thêm CUỐI trang
    assert "crm.leads" in seen[0] and "win_rate" in seen[0]                      # model thấy danh mục số chuẩn, không phải bảng thô
    assert r.status == "not_found"                                               # app người khác
    page = await q(gd, "apps.open", app_id=app["app_id"])
    assert [b["kind"] for b in page["blocks"] if b["type"] == "chart"] == ["bar", "hbar"]
    await ok(gd, "apps.delete", app_id=app["app_id"])


async def test_answer_blocks_show_a_chart_in_chat_and_can_be_kept_on_a_dashboard(who: Who) -> None:
    """Trả lời bằng khối (0220 lát 5): cùng định nghĩa khối với dashboard; thẻ gọn trong chat giữ MỘT biểu đồ; "Ghim vào dashboard" thêm đúng
    các khối đó; khối lỗi ⇒ từ chối kèm lý do (trợ lý sửa), không ra thẻ rỗng."""
    from barista.kernel import ui

    gd, sale1 = await who("giamdoc"), await who("sale1")
    cat = await q(gd, "apps.catalog")
    assert any(d["dataset"] == "crm.leads" for d in cat["datasets"]) and "last_3m" in cat["periods"]
    blocks = [{"kind": "kpi", "title": "Số lead", "dataset": "crm.leads", "measures": ["count"], "period": "last_3m"},
              {"kind": "chart", "chart": "line", "title": "Lead theo tháng", "dataset": "crm.leads", "measures": ["count"], "by": ["month"], "period": "last_3m"}]
    page = await q(gd, "apps.answer", title="Lead 3 tháng gần nhất", blocks=blocks)
    assert [b["type"] for b in page["blocks"]] == ["kpis", "chart"] and all("edit" not in b for b in page["blocks"])
    assert page["blocks"][1]["source"]["label"]                                   # nguồn gốc của số đi theo khối
    small = ui.compact(page)
    assert small and [b["type"] for b in small["blocks"]] == ["kpis", "chart"]    # chat: ô số + MỘT biểu đồ
    keep = page["actions"][0]
    assert keep["command"] == "apps.keep" and len(keep["preset"]["blocks"]) == 2
    r = await executor.query(gd, "apps.answer", {"title": "x", "blocks": [{**blocks[1], "by": ["khong_co"]}]})
    assert r.status == "invalid" and "khong_co" in (r.error or "")                # lỗi nói rõ để trợ lý sửa
    assert (await executor.query(gd, "apps.answer", {"title": "x", "blocks": []})).status == "invalid"

    out = await ok(gd, "apps.keep", blocks=keep["preset"]["blocks"], title="Bảng theo dõi lead")   # không chọn app ⇒ tạo mới
    opened = await q(gd, "apps.open", app_id=out["app_id"])
    assert [b["type"] for b in opened["blocks"]] == ["kpis", "chart"] and out["blocks"] == 2
    again = await ok(gd, "apps.keep", blocks=keep["preset"]["blocks"][:1], app_id=out["app_id"])
    assert again["blocks"] == 3
    assert {"value": str(out["app_id"]), "label": "Bảng theo dõi lead"} in await q(gd, "apps.mine_options")
    denied = await executor.run(sale1, "apps.keep", {"blocks": keep["preset"]["blocks"], "app_id": str(out["app_id"])})
    assert denied.status == "not_found"                                            # không ghim vào app người khác
    await ok(gd, "apps.delete", app_id=out["app_id"])


def test_last_n_months_are_whole_calendar_months() -> None:
    from datetime import date

    from barista.modules.apps import period_range

    assert period_range("last_3m", date(2026, 11, 15)) == (date(2026, 9, 1), date(2026, 12, 1))
    assert period_range("last_12m", date(2026, 1, 31)) == (date(2025, 2, 1), date(2026, 2, 1))


async def test_why_skill_ships_with_the_base_pack_and_only_uses_standard_numbers(who: Who) -> None:
    """Kỹ năng "tìm vì sao" (0220 lát 6): gói nền mang bản mặc định — cách làm là dữ liệu của công ty; chỉ gọi công cụ trả lời bằng khối (số chuẩn)."""
    from barista.kernel import registry, skill_specs

    spec = skill_specs.DECLARED["tim_vi_sao"]
    assert spec.tools == ("apps.answer",) and "director" in spec.roles
    assert any(r[0] == skill_specs.INSTALL and r[1] == "tim_vi_sao" for r in registry.PACKS["base"].records)
    assert skill_specs.lint(spec.title, spec.when_to_use, spec.instructions, spec.roles, spec.tools) == []
    gd = await who("giamdoc")
    rows = (await q(gd, "core.skills"))["rows"]
    assert any(r.get("key") == "tim_vi_sao" for r in rows)                       # công ty mẫu đã cài bộ mặc định


async def test_why_button_carries_a_question_built_from_the_block(who: Who) -> None:
    """Nút "Vì sao?" (0220 lát 7): ô số / biểu đồ trên tập dữ liệu mang câu hỏi điền sẵn (chỉ số, chiều, kỳ đang chọn, lọc); khối nguồn danh sách thì không."""
    gd = await who("giamdoc")
    app = await ok(gd, "apps.create", title="Nút vì sao", params=[{"key": "ky", "label": "Kỳ", "kind": "period", "default": "this_month"}])
    await ok(gd, "apps.add_block", app_id=app["app_id"], page="main",
             block={**CHART, "source": {**CHART["source"], "period": "$ky", "where": {"stage": ["new"]}}})
    await ok(gd, "apps.add_block", app_id=app["app_id"], page="main",
             block={"kind": "kpi", "title": "Số lead", "measure": "count", "source": {"dataset": "crm.leads", "measures": ["count"]}})
    page = await q(gd, "apps.open", app_id=app["app_id"], ky="last_month")
    chart = next(b for b in page["blocks"] if b["type"] == "chart")
    assert chart["why"].startswith("Vì sao Số lead theo chặng") and "kỳ tháng trước" in chart["why"] and "chặng:" in chart["why"]
    kpi = next(b for b in page["blocks"] if b["type"] == "kpis")
    assert all(k["why"].startswith("Vì sao Số lead") for k in kpi["items"])
    await ok(gd, "apps.delete", app_id=app["app_id"])
