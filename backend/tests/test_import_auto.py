"""0226 — nhập Excel TỰ KHỚP: file lộn xộn (tên báo cáo, ô gộp, dòng tổng, SĐT / ngày / tiền đủ kiểu) vào đúng trường mà không bắt người dùng sửa theo mẫu.

Phần THUẦN (``kernel/import_match``): nhận dòng tiêu đề, bỏ dòng tổng / chân trang, khớp tên + nội dung cột, danh sách chọn. Phần CÓ DB: ``imports.analyze`` /
``run_plan`` — khớp bằng model chỉ cho cột còn lại (model giả, đo cái được gửi đi), ánh xạ nhớ theo công ty, thêm mới / cập nhật / trùng theo khoá tự nhiên,
chạy thử rồi nhập thật qua đúng đường lệnh."""

import dataclasses
import io
import itertools
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from barista import seed
from barista.http_api import app
from barista.kernel import executor, imports, registry
from barista.kernel import import_match as im
from barista.kernel.principal import Principal
from tests.conftest import ok, owner_val, q

_seq = itertools.count(1)


@pytest.fixture(autouse=True)
def _no_real_model() -> Any:
    """Test không gọi nhà cung cấp model thật: mặc định model giả không khớp được gì; test nào cần thì tự đặt ``imports.AI_MODEL``."""
    imports.AI_MODEL = FunctionModel(lambda messages, info: ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"pairs": []})]), model_name="fake-empty")
    yield
    imports.AI_MODEL = None
PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("a", "Sale A", "sale", "KD-A", False)]


async def _company() -> tuple[UUID, str, dict[str, Principal]]:
    domain = f"ia{next(_seq)}.demo"
    tid = await seed.create_tenant(f"Công ty nhập tự khớp {domain}", domain, people=PEOPLE)
    return tid, domain, {k: await seed.who(tid, k, domain) for k, *_ in PEOPLE}


# ── Bảng tính thật, lộn xộn như file khách gửi ──

def _xlsx(sheets: dict[str, list[list[Any]]], merges: dict[str, list[str]] | None = None) -> bytes:
    wb = Workbook()
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet()
        assert ws is not None
        ws.title = name
        first = False
        for r in rows:
            ws.append(r)
        for ref in (merges or {}).get(name, []):
            ws.merge_cells(ref)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _messy_customers() -> list[list[Any]]:
    """Tên báo cáo gộp ô + ngày lập + dòng trống + nhóm cột gộp ô + tiêu đề + dữ liệu đủ kiểu + dòng tổng + chân trang. SĐT ở CẢ ba dạng (chữ, số mất số 0, có dấu chấm)."""
    return [
        ["DANH SÁCH KHÁCH QUAN TÂM — THÁNG 10/2026", None, None, None, None, None, None, None],
        ["Người lập: Nguyễn Văn A", None, None, None, None, None, None, None],
        [None] * 8,
        [None, "Thông tin khách", None, None, "Nhu cầu", None, None, None],
        ["STT", "Họ và tên", "Số điện thoại", "Ngày sinh", "Ngân sách", "Khu vực", "Nguồn", "Ghi chú"],
        [1, "Nguyễn Thị Lan", "0912000001", "15/11/1988", "2,5 tỷ", "Quận 7", "Quảng cáo", "cần tư vấn cuối tuần"],
        [2, "Trần Văn Bình", 912000002, datetime(1990, 3, 2), "850tr", "Thủ Đức", "Giới thiệu", None],
        [3, "Lê Thu Hà", "0912.000.003", "01/02/1995", "4.500.000.000", "Quận 2", "Sự kiện", "gọi sau 6h"],
        [4, "Tổng công ty ABC", "0912000004", "03/04/1980", "1 tỷ", None, "Quảng cáo", None],
        [None] * 8,
        ["Tổng cộng", None, None, None, 8850000000, None, None, None],
        ["Người duyệt: ………"],
    ]


# ── Phần thuần ──

def test_header_row_found_under_title_rows_and_merged_group_cells_and_totals_skipped() -> None:
    grid = _messy_customers()
    h = im.detect_header(grid)
    assert h is not None and h.line == 5 and h.names[1:4] == ["Họ và tên", "Số điện thoại", "Ngày sinh"]
    assert h.groups[2] == "Thông tin khách" and h.groups[5] == "Nhu cầu"   # ô gộp nhóm ở dòng trên được ghép vào tiêu đề
    rows = im.data_rows(grid, h)
    assert [ln for ln, _ in rows.rows] == [6, 7, 8, 9]   # "Tổng công ty ABC" là KHÁCH, không phải dòng tổng
    assert dict(rows.skipped) == {11: "dòng tổng cộng", 12: "chân trang / dòng ghi chú"}


def test_total_row_needs_label_plus_numbers_only() -> None:
    assert im.is_total_row(["Tổng cộng", None, None, 8850000000, None])
    assert im.is_total_row([None, "TỔNG", None, 12, 3])
    assert not im.is_total_row(["Tổng công ty ABC", "0912000004", "HCM", None])
    assert not im.is_total_row(["Nguyễn Văn A", 1, 2])


def _fields(command: str, **kw: Any) -> list[im.Field]:
    op = registry.get(command)
    assert op is not None
    return imports._fields_of(op, None, **kw)


def test_vietnamese_headers_map_by_name_synonym_and_content_without_a_model() -> None:
    grid = _messy_customers()
    fields = _fields("crm.create_lead")
    h = im.detect_header(grid, imports._known_headers(fields))
    assert h is not None
    rows = im.data_rows(grid, h)
    cols = im.profile_columns(h, rows)
    ms, _ = im.match_columns(cols, fields)
    got = {m.field: (h.names[m.col], m) for m in ms}
    assert got["name"][0] == "Họ và tên" and got["phone"][0] == "Số điện thoại" and got["birthday"][0] == "Ngày sinh"
    assert got["budget_vnd"][0] == "Ngân sách" and got["note"][0] == "Ghi chú" and got["source"][0] == "Nguồn"
    assert all(m.conf >= im.SURE for k, (_, m) in got.items() if k in ("name", "phone", "birthday", "budget_vnd", "note"))
    assert 0 in {c.index for c in cols if c.ignore}   # STT bỏ qua
    assert "Khu vực" not in {name for name, _ in got.values()}   # cột lạ không bị ép vào trường nào


@pytest.mark.parametrize("header", ["SĐT", "Số điện thoại", "Điện thoại di động", "Tel", "Mobile", "SDT khách", "Số ĐT"])
def test_phone_header_variants(header: str) -> None:
    fields = _fields("crm.create_lead")
    grid = [["Họ tên", header], ["Lan", "0912000001"], ["Bình", "0912000002"]]
    h = im.detect_header(grid, imports._known_headers(fields))
    assert h is not None
    cols = im.profile_columns(h, im.data_rows(grid, h))
    ms, _ = im.match_columns(cols, fields)
    assert {m.field: m.col for m in ms}["phone"] == 1


def test_content_alone_recognises_phone_email_date_when_headers_say_nothing() -> None:
    fields = _fields("crm.create_lead")
    grid = [["Cột X", "Cột Y", "Cột Z"],
            ["0912000001", "lan@example.com", "15/11/1988"], ["0912000002", "binh@example.com", "02/03/1990"], ["0912000003", "ha@example.com", "01/02/1995"]]
    h = im.detect_header(grid)
    assert h is not None
    cols = im.profile_columns(h, im.data_rows(grid, h))
    ms, _ = im.match_columns(cols, fields)
    by = {m.field: m for m in ms}
    assert by["phone"].col == 0 and by["email"].col == 1
    assert all(m.source == "value" and m.conf < im.SURE for m in by.values())   # chỉ nhận ra bằng nội dung ⇒ "gợi ý", người xem lại


def test_option_index_exact_contained_fuzzy_and_never_a_blind_guess() -> None:
    idx = im.OptionIndex([("ads", "Quảng cáo"), ("referral", "Giới thiệu"), ("event", "Sự kiện"), ("KD-A", "KD-A — Kinh doanh A"), ("KD-B", "KD-B — Kinh doanh B")])
    assert idx.resolve("quang cao") == ("ads", None) and idx.resolve("Giới Thiệu") == ("referral", None) and idx.resolve("ads") == ("ads", None)
    assert idx.resolve("kinh doanh a")[0] == "KD-A" and idx.resolve("KD-B")[0] == "KD-B"
    assert idx.resolve("Quang cao ") [0] == "ads" and idx.resolve("Su kien")[0] == "event"
    assert idx.resolve("Quảng cáoo")[0] == "ads"   # sai chính tả nhẹ
    value, _hint = idx.resolve("Facebook")
    assert value is None   # không đoán bừa: chờ người chọn
    assert idx.resolve("") == (None, None)


def test_resolve_options_collects_each_distinct_unresolved_text_once_not_per_row() -> None:
    f = _fields("crm.create_lead")
    by = {x.key: x for x in f}
    rows = [{"_line": i, "source": s} for i, s in enumerate(["Quảng cáo", "Facebook", "facebook ", "Zalo", "Facebook"], 2)]
    out, pending, waiting = im.resolve_options(by, rows, {})
    assert [(u.raw, u.count) for u in pending if u.field == "source"] == [("Facebook", 3), ("Zalo", 1)]   # "Facebook" / "facebook " là MỘT chữ
    assert out[0]["source"] == "ads" and waiting == {3, 4, 5, 6}   # dòng 2 ("Quảng cáo") nhận ra; 4 dòng Facebook / Zalo chờ chọn
    picked, pending2, waiting2 = im.resolve_options(by, rows, {"source": {im.name_key("Facebook"): "ads", im.name_key("Zalo"): ""}})
    assert pending2 == [] and waiting2 == set() and picked[1]["source"] == "ads" and "source" not in picked[3]   # "" = bỏ trống ô


def test_signature_ignores_column_order_case_and_accents() -> None:
    a = im.Header(0, ["Họ tên", "SĐT", "Ngân sách"], ["", "", ""], 1)
    b = im.Header(3, ["NGAN SACH", "ho ten", "sđt"], ["", "", ""], 4)
    assert im.signature(a) == im.signature(b)
    assert im.signature(a) != im.signature(im.Header(0, ["Họ tên", "SĐT"], ["", ""], 1))


async def _none() -> list[dict[str, Any]]:
    return []


# ── Có DB: phân tích, ánh xạ nhớ, model chỉ cho cột còn lại, nhập thật ──

async def _analyze(p: Principal, command: str, data: bytes, **kw: Any) -> dict[str, Any]:
    r = await imports.analyze(p, command, imports.read_all(data), **kw)
    assert r.status == "ok", r.error
    return r.output


def _plan(a: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return {"sheet": a["sheet"], "header_line": a["header_line"], "mapping": {m["field"]: m["column"] for m in a["mapping"]},
            "resolutions": a["resolutions"], **kw}


async def test_messy_customer_sheet_maps_dry_runs_and_imports_through_the_command() -> None:
    tid, _d, w = await _company()
    admin = w["admin"]
    data = _xlsx({"Khách T10": _messy_customers()}, {"Khách T10": ["A1:H1"]})
    a = await _analyze(admin, "crm.create_lead", data, file_name="khach-t10.xlsx")
    assert a["header_line"] == 5 and a["rows"] == 4 and a["unmapped_required"] == []
    by = {m["field"]: m for m in a["mapping"]}
    assert by["phone"]["column"] == 2 and by["phone"]["confidence"] >= im.SURE and by["name"]["source"] == "name"
    assert a["skipped_count"] == 2 and a["layout"] is None and a["key"]["update"] is False and a["key"]["fields"][0] == "SĐT"
    plan = _plan(a)
    r = await imports.run_plan(admin, "crm.create_lead", imports.read_all(data), plan, dry_run=True, file_name="khach-t10.xlsx")
    assert r.status == "ok", r.error
    out = r.output
    assert out["dry_run"] and out["summary"]["create"] == 4 and out["summary"]["failed"] == 0 and out["summary"]["ignored"] == 2, out["rows"]
    head = {v["label"]: v["text"] for v in out["preview"][1]["values"]}
    assert head["SĐT"] == "912000002" and "Ngân sách" in head   # số mất số 0 đầu vẫn tới lệnh; lệnh chuẩn hoá thành E.164
    assert await owner_val("SELECT count(*) FROM crm.customers WHERE tenant_id = %s", (tid,)) == 0   # chạy thử không ghi
    real = await imports.run_plan(admin, "crm.create_lead", imports.read_all(data), plan, dry_run=False, file_name="khach-t10.xlsx", file_sha256="a" * 64)
    assert real.status == "ok" and real.output["summary"]["create"] == 4, real.output["rows"]
    rows = await owner_val("SELECT array_agg(phone ORDER BY phone) FROM crm.customers WHERE tenant_id = %s", (tid,))
    assert rows == ["+84912000001", "+84912000002", "+84912000003", "+84912000004"]
    budgets = await owner_val("""SELECT array_agg((l.interest->>'budget_vnd')::bigint ORDER BY c.phone) FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id
                                 WHERE l.tenant_id = %s""", (tid,))
    assert budgets == [2_500_000_000, 850_000_000, 4_500_000_000, 1_000_000_000]   # "2,5 tỷ" · "850tr" · "4.500.000.000" · "1 tỷ"
    logged = await q(admin, "core.import_runs")
    assert logged["rows"][0]["total"] == 4 and logged["rows"][0]["ok"] == 4 and logged["rows"][0]["file_name"] == "khach-t10.xlsx"   # nhật ký nhập như mọi lần nhập


async def test_same_file_again_is_matched_as_duplicate_not_run_and_a_new_row_is_added() -> None:
    _tid, _d, w = await _company()
    admin = w["admin"]
    data = _xlsx({"Khách": _messy_customers()})
    a = await _analyze(admin, "crm.create_lead", data)
    plan = _plan(a)
    assert (await imports.run_plan(admin, "crm.create_lead", imports.read_all(data), plan, dry_run=False)).output["summary"]["create"] == 4
    grid = _messy_customers()
    grid.insert(9, [5, "Phạm Mới", "0912000005", "05/05/1985", "700tr", "Quận 1", "Sự kiện", None])
    grid.insert(9, [6, "Lan trùng dòng trên", "0912000005", None, None, None, None, None])   # cùng SĐT trong chính file
    data2 = _xlsx({"Khách": grid})
    r = await imports.run_plan(admin, "crm.create_lead", imports.read_all(data2), plan | {"header_line": 5}, dry_run=True)
    s = r.output["summary"]
    assert s["create"] == 1 and s["skip"] == 5 and s["failed"] == 0, r.output["rows"]   # 4 khách đã có + 1 trùng trong file
    why = {sk["reason"] for sk in r.output["skips"]}
    assert "đã có trong hệ thống (trùng SĐT)" in why and any("lặp lại dòng đứng trước trong file" in x for x in why)
    assert all("+84" not in x and "/ )" not in x and "()" not in x for x in why)   # không in khoá nội bộ / phần khoá rỗng
    dup = next(g for g in r.output["skips"] if g["reason"] == "đã có trong hệ thống (trùng SĐT)")
    assert dup["count"] == 4 and dup["rows"][0] == {"line": 6, "who": "Nguyễn Thị Lan · 0912000001", "note": "đã có"}   # SĐT theo cách đọc của vùng
    again = await imports.run_plan(admin, "crm.create_lead", imports.read_all(data), plan, dry_run=True)
    assert again.output["summary"]["create"] == 0 and again.output["nothing"] == "Cả 4 dòng đã có trong hệ thống — không có gì mới để nhập."
    assert all(row["action"] in ("skip", "create") for row in r.output["rows"])


async def test_existing_team_is_updated_safely_never_resets_unlisted_fields_and_overwrite_is_opt_in() -> None:
    tid, _d, w = await _company()
    admin = w["admin"]
    await ok(admin, "admin.upsert_team", code="MKT-1", name="Marketing cũ", kind="marketing", parent_code="")
    data = _xlsx({"Đội": [["Mã đội", "Tên đội", "Thuộc đội"], ["MKT-1", "Marketing mới", "QT"], ["KD-Z", "Kinh doanh Z", "QT"], ["mkt-1", "lặp", None]]})
    a = await _analyze(admin, "admin.upsert_team", data)
    assert {m["field"] for m in a["mapping"]} == {"code", "name", "parent_code"} and a["key"]["update"] is True
    plan = _plan(a)
    safe = (await imports.run_plan(admin, "admin.upsert_team", imports.read_all(data), plan, dry_run=True)).output
    assert safe["summary"]["create"] == 1 and safe["summary"]["update"] == 1 and safe["summary"]["skip"] == 1, safe["rows"]   # parent_code đang trống ⇒ điền; tên khác ⇒ giữ
    upd = next(r for r in safe["rows"] if r["action"] == "update")
    assert {x["field"]: x["applied"] for x in upd["diff"]} == {"Thuộc đội": True, "Tên đội": False}
    real = (await imports.run_plan(admin, "admin.upsert_team", imports.read_all(data), plan, dry_run=False)).output
    assert real["summary"]["update"] == 1
    row = await owner_val("SELECT to_jsonb(x) FROM (SELECT t.name, t.kind, p.code AS parent FROM core.teams t "
                          "LEFT JOIN core.teams p ON p.id = t.parent_id WHERE t.tenant_id = %s AND t.code = 'MKT-1') x", (tid,))
    assert row == {"name": "Marketing cũ", "kind": "marketing", "parent": "QT"}   # tên giữ, loại KHÔNG bị đặt lại về mặc định "sales", đội cha được điền
    forced = (await imports.run_plan(admin, "admin.upsert_team", imports.read_all(data), plan | {"overwrite": True}, dry_run=False)).output
    assert forced["summary"]["update"] == 1
    again = await owner_val("SELECT name || '/' || kind FROM core.teams WHERE tenant_id = %s AND code = 'MKT-1'", (tid,))
    assert again == "Marketing mới/marketing"


async def test_options_resolve_from_options_query_labels_once_per_distinct_value_and_real_run_waits_for_the_pick() -> None:
    _tid, d, w = await _company()
    admin = w["admin"]
    roles = {o["label"]: o["value"] for o in await q(admin, "admin.role_options")}
    sale_label = next(lb for v, lb in ((v, lb) for lb, v in roles.items()) if v == "sale")
    rows = [["Email", "Họ tên", "Vai trò", "Đội", "Trưởng đội?"],
            [f"m1@{d}", "Mai Một", sale_label, "KD-A", "không"], [f"m2@{d}", "Mai Hai", sale_label.upper(), "KD-A", "có"], [f"m3@{d}", "Mai Ba", "Bán hàng online", "KD-A", "x"],
            [f"m4@{d}", "Mai Bốn", "Bán hàng online", "KD-A", "không"]]
    data = _xlsx({"Nhân sự": rows})
    a = await _analyze(admin, "admin.invite_user", data)
    assert {m["field"] for m in a["mapping"]} >= {"email", "name", "role", "team_code", "is_lead"}
    plan = _plan(a)
    dry = (await imports.run_plan(admin, "admin.invite_user", imports.read_all(data), plan, dry_run=True)).output
    assert dry["summary"]["waiting"] == 2 and dry["summary"]["failed"] == 0 and dry["reasons"] == []   # chờ chọn KHÔNG phải lỗi
    assert dry["nothing"] is None and {r["status"] for r in dry["preview"]} <= {"ok", "pending_approval", "waiting"} and "waiting" in {r["status"] for r in dry["preview"]}
    group = dry["unresolved"][0]
    assert group["field"] == "role" and [(v["raw"], v["count"]) for v in group["values"]] == [("Bán hàng online", 2)]   # MỘT chữ, 2 dòng — không phải mỗi dòng một lần hỏi
    assert any(o["value"] == "sale" for o in group["options"])
    blocked = await imports.run_plan(admin, "admin.invite_user", imports.read_all(data), plan, dry_run=False)
    assert blocked.status == "invalid" and "chưa chọn" in (blocked.error or "")
    picked = plan | {"resolutions": {"role": {group["values"][0]["key"]: "sale"}}}
    done = (await imports.run_plan(admin, "admin.invite_user", imports.read_all(data), picked, dry_run=True)).output
    assert done["unresolved"] == [] and done["summary"]["failed"] == 0, done["rows"]
    assert done["summary"]["create"] + done["summary"]["update"] == 4


async def test_remembered_mapping_applies_to_the_next_file_with_same_layout_and_can_be_listed_and_forgotten() -> None:
    _tid, _d, w = await _company()
    admin = w["admin"]
    first = _xlsx({"A": [["Tên bộ phận", "Mã đội", "Ghi chú linh tinh"], ["Đội một", "R-1", "x"]]})
    a1 = await _analyze(admin, "admin.upsert_team", first)
    assert a1["layout"] is None and {m["field"] for m in a1["mapping"]} == {"code"}   # "Tên bộ phận" máy không chắc ⇒ người chọn trên màn khớp cột
    plan = _plan(a1)
    plan["mapping"]["name"] = 0
    done = (await imports.run_plan(admin, "admin.upsert_team", imports.read_all(first), plan, dry_run=False, file_name="doi-1.xlsx")).output
    assert done["summary"]["create"] == 1
    # cùng bố cục (đảo thứ tự cột, đổi hoa / thường) ⇒ nhận ra, mọi cặp từ trí nhớ
    again = _xlsx({"B": [["GHI CHÚ LINH TINH", "tên bộ phận", "MÃ ĐỘI"], ["y", "Đội hai", "R-2"]]})
    a2 = await _analyze(admin, "admin.upsert_team", again)
    assert a2["layout"] == {"exact": True, "name": "doi-1.xlsx", "uses": 1}
    assert {m["field"]: (m["source"], m["column"]) for m in a2["mapping"]} == {"name": ("saved", 1), "code": ("saved", 2)}
    other = _xlsx({"C": [["Tên bộ phận", "Mã đội", "Cột lạ"], ["Đội ba", "R-3", "z"]]})   # bố cục KHÁC (thêm cột lạ) nhưng "Tên bộ phận" đã quen
    a3 = await _analyze(admin, "admin.upsert_team", other)
    assert a3["layout"] == {"exact": False} and {m["field"]: m["source"] for m in a3["mapping"]} == {"name": "saved", "code": "saved"}
    page = await q(admin, "core.import_mappings")
    assert len(page["rows"]) == 1 and "Tên bộ phận → Tên đội" in page["rows"][0]["columns"] and page["rows"][0]["uses"] == 1
    await ok(admin, "core.edit_import_mapping", id=str(page["rows"][0]["id"]), name="Đội — mẫu cũ", forget_values=False)
    assert (await q(admin, "core.import_mappings"))["rows"][0]["name"] == "Đội — mẫu cũ"
    assert (await executor.run(w["a"], "core.delete_import_mapping", {"id": str(page["rows"][0]["id"])})).status == "denied"   # chỉ quản trị
    await ok(admin, "core.delete_import_mapping", id=str(page["rows"][0]["id"]))
    assert (await q(admin, "core.import_mappings"))["rows"] == []
    a4 = await _analyze(admin, "admin.upsert_team", again)
    assert a4["layout"] is None and {m["field"] for m in a4["mapping"]} == {"code"}


async def test_the_model_only_sees_unmapped_columns_with_a_few_masked_samples_and_cannot_invent_fields() -> None:
    _tid, _d, w = await _company()
    admin = w["admin"]
    seen: list[str] = []

    def fake(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        text = json.dumps([getattr(part, "content", "") for m in messages for part in m.parts], ensure_ascii=False)
        seen.append(text)
        tool = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(tool, {"pairs": [
            {"column": 3, "field": "wishes", "confidence": 0.9, "why": "chữ dài mô tả điều khách muốn"},
            {"column": 3, "field": "bịa_khoá", "confidence": 0.9},
            {"column": 2, "field": "birthday", "confidence": 0.9}]})])   # cột 2 đã khớp sẵn bằng nội dung ⇒ không nằm trong phần gửi, bị bỏ

    rows = [["Họ tên", "Số điện thoại", "Ngày sinh", "Điều khách đang tìm kiếm"],
            ["Lan", "0912000001", "15/11/1988", "căn 2 phòng ngủ gần trường quốc tế, ngân sách mềm"],
            ["Bình", "0912000002", "02/03/1990", "nhà phố có chỗ đậu xe hơi"], ["Hà", "0912000003", "01/02/1995", "căn hộ view sông"]]
    imports.AI_MODEL = FunctionModel(fake, model_name="fake-map")
    try:
        a = await _analyze(admin, "crm.create_lead", _xlsx({"S": rows}))
    finally:
        imports.AI_MODEL = None
    assert a["ai"]["state"] == "used" and a["ai"]["columns"] == 1 and a["ai"]["matched"] == 1, a["ai"]
    got = {m["field"]: m for m in a["mapping"]}
    assert got["wishes"]["source"] == "ai" and got["wishes"]["column"] == 3 and got["wishes"]["confidence"] < im.SURE   # model đoán ⇒ luôn "gợi ý", không "chắc"
    assert got["birthday"]["source"] != "ai"
    prompt = seen[0]
    assert "Điều khách đang tìm kiếm" in prompt and "căn hộ view sông" in prompt
    assert "0912000001" not in prompt and "Số điện thoại" not in prompt and "Nguyễn" not in prompt   # cột đã khớp không rời máy; số dài bị che
    assert a["ai"]["input_chars"] < 2500   # gửi tiêu đề + ≤ 5 ô mẫu của cột còn lại, không phải cả bảng


async def test_ai_off_or_failing_never_blocks_the_import_and_fully_mapped_files_cost_no_tokens() -> None:
    tid, d, w = await _company()
    admin = w["admin"]
    rows = [["Họ tên", "SĐT", "Điều khách đang tìm"], ["Lan", "0912000001", "căn hộ"]]
    await owner_val("UPDATE core.tenant_profile SET ai_enabled = false WHERE tenant_id = %s RETURNING 1", (tid,))
    off = await _analyze(await seed.who(tid, "admin", d), "crm.create_lead", _xlsx({"S": rows}))   # công ty tắt AI ⇒ không gửi gì ra ngoài
    assert off["ai"]["state"] == "off" and {m["field"] for m in off["mapping"]} >= {"name", "phone"}

    def broken(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("nhà cung cấp sập")

    imports.AI_MODEL = FunctionModel(broken)
    try:
        err = await _analyze(admin, "crm.create_lead", _xlsx({"S": rows}))
    finally:
        imports.AI_MODEL = None
    assert err["ai"]["state"] == "error" and {m["field"] for m in err["mapping"]} >= {"name", "phone"}

    calls: list[int] = []

    def boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        return ModelResponse(parts=[TextPart("không dùng")])

    imports.AI_MODEL = FunctionModel(boom)
    try:
        full = await _analyze(admin, "crm.create_lead", _xlsx({"S": [["Họ tên", "SĐT"], ["Lan", "0912000001"]]}))
    finally:
        imports.AI_MODEL = None
    assert full["ai"]["state"] == "none" and calls == []   # không còn cột nào chưa khớp ⇒ không gọi model


async def test_multi_sheet_file_suggests_which_sheet_goes_to_which_command() -> None:
    _tid, _d, w = await _company()
    admin = w["admin"]
    data = _xlsx({"Đội nhóm": [["Mã đội", "Tên đội", "Loại", "Thuộc đội"], ["T-1", "Đội 1", "Kinh doanh", "QT"]],
                  "Khách": [["Họ tên", "SĐT", "Email", "Ngân sách"], ["Lan", "0912000001", "lan@example.com", "2 tỷ"]]})
    a = await _analyze(admin, "crm.create_lead", data)
    assert a["sheet"] == "Khách"   # trang hợp lệnh này nhất được chọn sẵn
    sug = {s["name"]: s["suggest"] for s in a["sheets"]}
    assert sug["Khách"]["command"] == "crm.create_lead" and sug["Khách"]["same"]
    assert sug["Đội nhóm"]["command"] == "admin.upsert_team" and not sug["Đội nhóm"]["same"]
    other = await _analyze(admin, "crm.create_lead", data, sheet="Đội nhóm")
    assert other["sheet"] == "Đội nhóm" and other["header_line"] == 1


async def test_commands_with_their_own_reader_and_non_importable_commands_are_not_auto_matched(monkeypatch: pytest.MonkeyPatch) -> None:
    _tid, _d, w = await _company()
    admin = w["admin"]
    data = _xlsx({"S": [["a", "b"], [1, 2]]})
    op = registry.OPS["admin.upsert_team"]
    monkeypatch.setitem(registry.OPS, "admin.upsert_team", dataclasses.replace(op, import_rows=lambda ctx, grid: _none()))   # công ty có bộ đọc theo mẫu riêng
    r = await imports.analyze(admin, "admin.upsert_team", imports.read_all(data))
    assert r.status == "ok" and r.output["auto"] is False   # bộ đọc theo mẫu riêng ⇒ đường nhập cũ
    assert (await imports.run_plan(admin, "admin.upsert_team", imports.read_all(data), {}, dry_run=True)).status == "invalid"
    monkeypatch.setitem(registry.OPS, "admin.upsert_team", op)
    assert (await imports.analyze(admin, "crm.leads", imports.read_all(data))).status == "not_found"
    sale_ok = await imports.analyze(w["a"], "admin.upsert_team", imports.read_all(data))
    assert sale_ok.status == "denied"


async def test_http_analyze_then_plan_end_to_end_and_the_old_xlsx_door_still_works() -> None:
    tid, _d, w = await _company()
    admin = w["admin"]
    h = {"X-Dev-User": admin.email, "X-Tenant-Id": str(admin.tenant_id)}
    data = _xlsx({"Đội": [["Mã đội", "Tên đội"], ["HTTP-1", "Đội http"]]})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/v1/imports/admin.upsert_team/analyze", files={"file": ("doi.xlsx", data)}, headers=h)
        assert r.status_code == 200, r.text
        a = r.json()["output"] if "output" in r.json() else r.json()
        plan = _plan(a)
        dry = await c.post("/v1/imports/admin.upsert_team/plan?dry_run=true", files={"file": ("doi.xlsx", data)}, data={"plan": json.dumps(plan)}, headers=h)
        assert dry.status_code == 200 and (dry.json().get("output") or dry.json())["summary"]["create"] == 1
        real = await c.post("/v1/imports/admin.upsert_team/plan?dry_run=false", files={"file": ("doi.xlsx", data)}, data={"plan": json.dumps(plan)}, headers=h)
        assert real.status_code == 200, real.text
        bad = await c.post("/v1/imports/admin.upsert_team/analyze", files={"file": ("x.xlsx", b"PK\x03\x04junk" * 10)}, headers=h)
        assert bad.status_code == 422 and "Không đọc được file" in bad.text
        assert (await c.post("/v1/imports/admin.upsert_team/analyze", data={"x": "y"}, headers=h)).status_code in (415, 422)
        legacy = await c.post("/v1/imports/admin.upsert_team/xlsx?dry_run=true", content=_xlsx({"S": [["Mã đội", "Tên đội"], ["OLD-1", "Đội cũ"]]}), headers=h)
        assert legacy.status_code == 200   # cửa cũ (app Mac) giữ nguyên
    assert await owner_val("SELECT count(*) FROM core.teams WHERE tenant_id = %s AND code = 'HTTP-1'", (tid,)) == 1


def test_every_declared_import_key_names_real_input_fields_and_update_commands() -> None:
    keyed = [op for op in registry.OPS.values() if op.import_key is not None]
    assert {op.name for op in keyed} >= {"crm.create_lead", "admin.upsert_team", "mkt.upsert_campaign"}
    for op in keyed:
        assert op.import_key is not None
        assert all(f in op.input.model_fields for f in op.import_key.fields), op.name
        if op.import_key.update not in (None, "self"):
            assert registry.get(op.import_key.update) is not None
    from barista.kernel.registry import ImportKey
    from barista.modules.crm import m as crm

    with pytest.raises(ValueError, match="import_key"):   # chỉ lệnh nhập hàng loạt mới khai khoá (kiểm ngay lúc khai, chưa đăng ký gì)
        crm.command("zz_probe", resource="crm", title="t", import_key=ImportKey(("a",), lambda *_: None))   # type: ignore[arg-type]
    assert "crm.zz_probe" not in registry.OPS



# ── Ngày theo vùng của công ty (14/11): quyết định theo CỘT, ô ngày thật của Excel không bao giờ đọc lại như chữ, giờ không nêu múi = giờ công ty ──

def test_a_text_date_column_is_decided_once_for_the_whole_column() -> None:
    amb = ["03/04/2026", "05/06/2026", "11/12/2025"]   # mọi ô ≤ 12 ⇒ mơ hồ ⇒ mặc định của vùng
    vn, us = im.decide_order(amb, "VN"), im.decide_order(amb, "US")
    assert (vn.order, vn.decided) == ("dmy", "country") and (us.order, us.decided) == ("mdy", "country")
    assert vn.examples["dmy"] == "03/04/2026 = 3 tháng 4 năm 2026" and vn.examples["mdy"] == "03/04/2026 = 4 tháng 3 năm 2026"   # cả hai cách đọc có ví dụ cụ thể để bấm đổi
    for country in ("VN", "US"):   # có một giá trị > 12 ⇒ dữ liệu quyết, bất kể vùng — và áp cho CẢ cột (ô 03/04 cũng đọc theo ngày trước)
        d = im.decide_order(["03/04/2026", "25/04/2026"], country)
        assert (d.order, d.decided) == ("dmy", "data")
        m = im.decide_order(["03/04/2026", "04/25/2026"], country)
        assert (m.order, m.decided) == ("mdy", "data")
    both = im.decide_order(["25/04/2026", "04/25/2026", "26/04/2026"], "US")
    assert both.decided == "mixed" and both.order == "dmy"   # cột lẫn hai kiểu: theo bên nhiều hơn, màn khớp cột báo
    assert im.decide_order(amb, "VN", saved="mdy").order == "mdy" and im.decide_order(amb, "VN", saved="mdy").decided == "saved"
    assert im.decide_order(amb, "VN", saved="mdy", user="dmy").decided == "user"
    assert im.decide_order(["25/04/2026"], "US", saved="mdy").order == "dmy"   # trí nhớ không thắng dữ liệu rõ ràng
    only_real = im.decide_order([datetime(2026, 4, 3)], "VN")
    assert only_real.text_dates == 0   # ô ngày thật không đếm — không có gì để hỏi


def test_real_excel_date_cells_and_serial_numbers_are_used_as_is() -> None:

    fields = {"d": im.Field("d", "Ngày", "date", False, [], {})}
    rows = [{"d": datetime(2026, 4, 3)}, {"d": 46115}, {"d": "03/04/2026"}]   # ô ngày thật · số ngày của Excel (03/04/2026 = 46115) · chữ
    for country, text_day in (("VN", "2026-04-03"), ("US", "2026-03-04")):
        out, info = im.convert_dates(fields, rows, country, UTC)
        assert out[0]["d"] == "2026-04-03" and out[1]["d"] == "2026-04-03"   # KHÔNG đổi theo vùng: ô thật không mơ hồ
        assert out[2]["d"] == text_day and info["d"].text_dates == 1


def test_datetimes_without_offset_are_company_local_time_and_explicit_offsets_are_kept() -> None:
    from zoneinfo import ZoneInfo

    vn, ny = ZoneInfo("Asia/Ho_Chi_Minh"), ZoneInfo("America/New_York")
    f = {"at": im.Field("at", "Lúc", "text", True, [], {})}
    rows = [{"at": "03/04/2026 14:30"}, {"at": datetime(2026, 4, 3, 14, 30)}, {"at": "03/04/2026 14:30 +09:00"}, {"at": "2026-04-03T07:30:00Z"}, {"at": "2026-04-03 14:30"},
            {"at": 46115.5}]
    out_vn, _ = im.convert_dates(f, rows, "VN", vn)
    assert [r["at"] for r in out_vn] == ["2026-04-03T14:30:00+07:00", "2026-04-03T14:30:00+07:00", "2026-04-03T14:30:00+09:00", "2026-04-03T07:30:00+00:00",
                                         "2026-04-03T14:30:00+07:00", "2026-04-03T12:00:00+07:00"]
    out_ny, info = im.convert_dates(f, rows[:2], "US", ny)
    assert [r["at"] for r in out_ny] == ["2026-03-04T14:30:00-05:00", "2026-04-03T14:30:00-04:00"] and info["at"].order == "mdy"   # chữ mơ hồ theo US; ô ngày thật giữ ngày; cùng giờ treo theo múi công ty
    assert im.is_stamp_field("starts_at", "text") and im.is_stamp_field("at", "text") and not im.is_stamp_field("note", "text") and not im.is_stamp_field("at", "date")


async def _birthdays(tid: UUID) -> list[str]:
    return await owner_val("SELECT array_agg(name || '=' || birthday::text ORDER BY name) FROM crm.customers WHERE tenant_id = %s", (tid,))


def _bday_sheet(rows: list[list[Any]]) -> bytes:
    return _xlsx({"S": [["Họ tên", "SĐT", "Ngày sinh"], *rows]})


async def test_same_ambiguous_birthday_file_reads_day_first_for_vn_and_month_first_for_us_and_shows_the_interpretation() -> None:
    rows = [["An", "0912000011", "03/04/1990"], ["Bình", "0912000012", "05/06/1991"]]
    for country, want in (("VN", ["An=1990-04-03", "Bình=1991-06-05"]), ("US", ["An=1990-03-04", "Bình=1991-05-06"])):
        tid, d, _w = await _company()
        await owner_val("UPDATE core.tenants SET country = %s WHERE id = %s RETURNING 1", (country, tid))
        admin = await seed.who(tid, "admin", d)
        data = _bday_sheet(rows)
        a = await _analyze(admin, "crm.create_lead", data)
        assert a["locale"]["country"] == country and a["locale"]["date_order"] == ("mdy" if country == "US" else "dmy")
        (info,) = a["dates"]
        assert info["field"] == "birthday" and info["decided"] == "country" and info["order"] == a["locale"]["date_order"]
        assert info["examples"]["dmy"] == "03/04/1990 = 3 tháng 4 năm 1990" and info["examples"]["mdy"] == "03/04/1990 = 4 tháng 3 năm 1990"
        dry = (await imports.run_plan(admin, "crm.create_lead", imports.read_all(data), _plan(a, date_orders={"birthday": info["order"]}), dry_run=True)).output
        shown = next(v["text"] for v in dry["preview"][0]["values"] if v["label"] == "Sinh nhật")
        assert shown == ("4 tháng 3 năm 1990" if country == "US" else "3 tháng 4 năm 1990")   # xem trước nói rõ máy đã hiểu ngày nào
        await imports.run_plan(admin, "crm.create_lead", imports.read_all(data), _plan(a, date_orders={"birthday": info["order"]}), dry_run=False)
        assert await _birthdays(tid) == want


async def test_a_column_with_a_day_above_12_decides_even_for_a_us_company_and_real_date_cells_ignore_the_region() -> None:
    tid, d, _w = await _company()
    await owner_val("UPDATE core.tenants SET country = 'US' WHERE id = %s RETURNING 1", (tid,))
    admin = await seed.who(tid, "admin", d)
    data = _bday_sheet([["An", "0912000021", "03/04/1990"], ["Bình", "0912000022", "25/12/1985"], ["Cúc", "0912000023", datetime(1992, 5, 6)]])
    a = await _analyze(admin, "crm.create_lead", data)
    assert a["dates"][0]["order"] == "dmy" and a["dates"][0]["decided"] == "data"   # 25/12 chỉ có thể là ngày trước — cả cột đọc ngày trước, dù công ty ở Mỹ
    await imports.run_plan(admin, "crm.create_lead", imports.read_all(data), _plan(a, date_orders={"birthday": a["dates"][0]["order"]}), dry_run=False)
    assert await _birthdays(tid) == ["An=1990-04-03", "Bình=1985-12-25", "Cúc=1992-05-06"]   # ô ngày thật (Cúc) giữ nguyên 6/5, không bị đọc lại theo vùng


async def test_the_toggle_is_remembered_in_the_saved_mapping_and_applied_to_the_next_file() -> None:
    tid, _d, w = await _company()
    admin = w["admin"]   # công ty Việt Nam: mặc định ngày trước
    data = _bday_sheet([["An", "0912000031", "03/04/1990"]])
    a = await _analyze(admin, "crm.create_lead", data)
    assert a["dates"][0]["order"] == "dmy"
    plan = _plan(a, date_orders={"birthday": "mdy"})   # người bấm đổi sang tháng/ngày
    real = (await imports.run_plan(admin, "crm.create_lead", imports.read_all(data), plan, dry_run=False, file_name="b.xlsx")).output
    assert real["summary"]["create"] == 1 and await _birthdays(tid) == ["An=1990-03-04"]
    nxt = await _analyze(admin, "crm.create_lead", _bday_sheet([["Bình", "0912000032", "05/06/1991"]]))
    assert nxt["dates"][0]["order"] == "mdy" and nxt["dates"][0]["decided"] == "saved"
    assert "__date_order" not in nxt["resolutions"]


async def test_timestamp_fields_take_company_local_time_into_timestamptz() -> None:
    results = {}
    for tz in ("Asia/Ho_Chi_Minh", "America/New_York"):
        tid, d, _w = await _company()
        await owner_val("UPDATE core.tenants SET timezone = %s WHERE id = %s RETURNING 1", (tz, tid))
        admin = await seed.who(tid, "admin", d)
        data = _xlsx({"S": [["Nguồn", "Mã", "Việc", "Bắt đầu"], ["he_cu", "x-1", "Họp khách", "02/11/2026 09:15"]]})
        plan = {"sheet": "S", "header_line": 1, "mapping": {"origin": 0, "ext_ref": 1, "title": 2, "starts_at": 3}}
        out = (await imports.run_plan(admin, "schedule.import_past", imports.read_all(data), plan, dry_run=False)).output
        assert out["summary"]["create"] == 1, out["rows"]
        results[tz] = await owner_val("SELECT (starts_at AT TIME ZONE 'UTC')::text FROM schedule.events WHERE tenant_id = %s", (tid,))
    assert results["Asia/Ho_Chi_Minh"].startswith("2026-11-02 02:15:00") and results["America/New_York"].startswith("2026-11-02 14:15:00")   # cùng "09:15" = hai thời điểm khác nhau


def test_option_errors_name_the_real_labels_in_vietnamese() -> None:
    from pydantic import ValidationError

    from barista.kernel.executor import explain

    op = registry.OPS["crm.create_lead"]
    with pytest.raises(ValidationError) as e:
        op.input.model_validate({"name": "A", "phone": "0912000001", "source": "facebook"})
    msg = explain(e.value, op.input)
    assert msg.startswith("Nguồn: phải là một trong: ") and " hoặc " in msg and " or " not in msg and "'" not in msg and "…" not in msg
    assert "Quảng cáo" in msg   # nhãn người đọc thấy, không phải mã `ads`
