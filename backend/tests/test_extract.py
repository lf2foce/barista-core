"""Trích trường có nguồn (quyết định 0070): model điền ô, CODE kiểm từng ô — câu nguyên văn trên trang + đọc lại ra đúng giá trị; người chốt ô chưa
kiểm, ghi thêm không sửa đè; quyền = quyền đọc tài liệu; gói tắt / công ty tắt AI ⇒ không trích. Chứng từ thử: hoá đơn chép từ hồ sơ L/C mẫu
của gói tài trợ thương mại (tests/fixtures/extract) — dữ liệu giả."""

import base64
import io
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from barista.db import db
from barista.kernel import executor, extract, packs, registry
from barista.kernel.extract import DocType, F
from barista.kernel.principal import issued, system_principal
from barista.modules import kb
from tests.conftest import Who, ok, q

INVOICE = "## Trang 1\n" + (Path(__file__).parent / "fixtures" / "extract" / "hoa-don-mau.md").read_text(encoding="utf-8")
DT = "kb.test_invoice"
FIELDS = (F("number", "Số hoá đơn"), F("issued_on", "Ngày lập", "date"), F("amount", "Tổng tiền", "amount"),
          F("quantity", "Số lượng (MT)", "number"), F("discharge", "Cảng dỡ"), F("lc_number", "Số L/C"), F("buyer_tax", "MST người mua"),
          F("ports", "Các cảng", "list"))


# ── Đọc lại bằng code (thuần) — câu thật của hồ sơ mẫu ──

def test_read_back_catches_a_misread_number_and_accepts_the_usual_ways_documents_write_things() -> None:
    rb = extract.read_back
    assert rb("amount", "USD 459000.00", "TOTAL: USD 459,000.00") == ""
    assert rb("amount", "USD 495000.00", "TOTAL: USD 459,000.00") == "câu gốc không có số tiền này"     # đúng câu, chép sai số
    assert rb("amount", "EUR 459000", "TOTAL: USD 459,000.00") == "câu gốc không có đồng tiền EUR"
    assert rb("amount", "USD 450000", ":32B: CURRENCY CODE, AMOUNT     USD450000,00") == ""             # điện SWIFT: dính mã, phẩy thập phân
    assert rb("number", "1700", "TOTAL: 1,710 BAGS — NET WEIGHT 102,000 KGS") != ""                      # lỗi đánh máy 1,710 không thành 1,700
    assert rb("number", "102", "102.000 MT") == "" and rb("number", "1710", "TOTAL: 1,710 BAGS") == ""
    assert rb("date", "2026-11-21", "SHIPPED ON BOARD: 21 NOV 2026") == ""
    assert rb("date", "2026-11-12", "SHIPPED ON BOARD: 21 NOV 2026") == "câu gốc không có ngày này"
    assert rb("date", "2026-09-15", ":31C: DATE OF ISSUE             260915") == ""
    assert rb("date", "2026-10-30", "Buon Ma Thuot, 30 October 2026") == ""
    assert rb("date", "2026-11-21", "Hà Nội, ngày 21 tháng 11 năm 2026") == ""
    assert rb("text", "BREMERHAVEN PORT, GERMANY", "| Port of Discharge | BREMERHAVEN PORT, GERMANY |") == ""
    assert rb("text", "HAMBURG PORT, GERMANY", "| Port of Discharge | BREMERHAVEN PORT, GERMANY |") != ""
    assert extract.canonical("amount", "usd 459,000.00") == "USD 459000" and extract.canonical("date", "21/11/2026") is None
    assert extract.parse_number("459,000.00") == Decimal("459000") and extract.dates_in("21/11/2026") == {date(2026, 11, 21)}


# ── Qua kb: model giả, Postgres thật ──

@pytest.fixture
def invoice_type() -> Iterator[DocType]:
    registry.DOC_TYPES[DT] = DocType(DT, "Hoá đơn (thử)", "kb", FIELDS)
    yield registry.DOC_TYPES[DT]
    registry.DOC_TYPES.pop(DT, None)


def _model(cells: list[dict[str, Any]]) -> FunctionModel:
    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"cells": cells})])
    return FunctionModel(reply, model_name="extract-fake")


READ = [   # model đọc: phần lớn đúng, cố tình sai ba kiểu
    {"field": "number", "value": "TNSC-EXP-2611-07", "page": 1, "quote": "Invoice No. | TNSC-EXP-2611-07"},
    {"field": "issued_on", "value": "2026-11-20", "page": 1, "quote": "| Date | 20 NOV 2026 |"},
    {"field": "amount", "value": "USD 495000.00", "page": 1, "quote": "TOTAL: USD 459,000.00"},                 # chép sai số
    {"field": "quantity", "value": "102", "page": 1, "quote": "102.000 MT"},
    {"field": "discharge", "value": "BREMERHAVEN PORT, GERMANY", "page": 2, "quote": "Port of Discharge | BREMERHAVEN PORT, GERMANY"},  # sai trang
    {"field": "lc_number", "value": "MKB-LC-26-9999", "page": 1, "quote": "Documentary Credit No. | MKB-LC-26-9999"},     # câu bịa
    {"field": "buyer_tax", "value": None, "page": None, "quote": None},                                                  # chứng từ không có
    {"field": "ports", "value": "HO CHI MINH CITY PORT, VIETNAM", "page": 1, "quote": "Port of Loading | HO CHI MINH CITY PORT, VIETNAM"},
    {"field": "ports", "value": "BREMERHAVEN PORT, GERMANY", "page": 1, "quote": "Port of Discharge | BREMERHAVEN PORT, GERMANY"},
]


async def _invoice_doc(p: Any, shelf: str = "common") -> str:
    return str((await ok(p, "kb.add_document", title=f"Hoá đơn thử {uuid.uuid4().hex[:6]}", shelf=shelf, kind="other", body=INVOICE))["document_id"])


async def test_each_cell_is_checked_by_code_and_people_confirm_what_the_model_got_wrong(who: Who, invoice_type: DocType) -> None:
    gd = await who("giamdoc")
    doc = await _invoice_doc(gd)
    kb.EXTRACT_MODEL = _model(READ)
    try:
        out = await ok(gd, "kb.extract", document_id=doc, doc_type=DT)
    finally:
        kb.EXTRACT_MODEL = None
    assert (out["verified"], out["unverified"], out["missing"]) == (5, 2, 1)

    sys = system_principal(gd.tenant_id, gd.user_id)
    got = (await executor.query(sys, "kb.extracted", {"document_id": doc, "doc_type": DT})).output
    f = got["fields"]
    assert f["amount"] == {**f["amount"], "value": "USD 495000", "status": "unverified", "usable": False}   # số sai KHÔNG dùng được
    assert f["discharge"]["status"] == "verified" and f["discharge"]["page"] == 1                         # sai trang ⇒ code sửa trang
    assert f["lc_number"]["status"] == "unverified" and f["buyer_tax"]["status"] == "missing"
    assert f["ports"]["value"] == ["HO CHI MINH CITY PORT, VIETNAM", "BREMERHAVEN PORT, GERMANY"] and f["ports"]["usable"]
    assert f["issued_on"]["value"] == "2026-11-20" and f["quantity"]["value"] == "102" and not got["from_scan"]

    page = await q(gd, "kb.extraction", extraction_id=got["extraction_id"])
    asks = [b for b in page["blocks"] if b["type"] == "callout" and b.get("actions")]
    assert {b["title"] for b in asks} == {"Tổng tiền: chưa kiểm được", "Số L/C: chưa kiểm được"}
    assert asks[0]["actions"][0]["command"] == "kb.correct_field"

    eid = got["extraction_id"]
    bad = await executor.run(gd, "kb.correct_field", {"extraction_id": eid, "key": "amount", "value": "bốn trăm", "reason": "đối chiếu bản gốc"})
    assert bad.status == "invalid"
    assert (await executor.run(gd, "kb.correct_field", {"extraction_id": eid, "key": "amount", "value": "USD 459000", "reason": " "})).status == "invalid"
    await ok(gd, "kb.correct_field", extraction_id=eid, key="amount", value="USD 459,000.00", reason="Đối chiếu dòng TOTAL trang 1")
    after = (await executor.query(sys, "kb.extracted", {"document_id": doc, "doc_type": DT})).output
    assert after["fields"]["amount"]["value"] == "USD 459000" and after["fields"]["amount"]["status"] == "confirmed"
    assert after["fields"]["amount"]["usable"] and after["mark"] != got["mark"]                           # người sửa ⇒ dấu căn cứ đổi
    async with db.owner.connection() as conn:                                                             # lần trích gốc giữ nguyên
        cur = await conn.execute("SELECT fields->'amount'->0->>'value' AS v FROM kb.extractions WHERE id = %s", (eid,))
        assert (await cur.fetchone())["v"] == "USD 495000"                                                # type: ignore[index]

    kb.EXTRACT_MODEL = _model(READ[:1])
    try:
        again = await ok(gd, "kb.extract", document_id=doc, doc_type=DT)                                  # trích lại = lần mới
    finally:
        kb.EXTRACT_MODEL = None
    newest = (await executor.query(sys, "kb.extracted", {"document_id": doc, "doc_type": DT})).output
    assert newest["extraction_id"] == again["extraction_id"] != eid

    await ok(gd, "kb.delete_document", document_id=doc)                                                   # xoá chứng từ ⇒ lần trích đi theo
    async with db.owner.connection() as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM kb.extractions WHERE document_id = %s", (doc,))
        assert (await cur.fetchone())["n"] == 0                                                           # type: ignore[index]


async def test_rights_pack_and_ai_switch_gate_extraction(who: Who, invoice_type: DocType) -> None:
    gd, kt, sale1 = await who("giamdoc"), await who("ketoan"), await who("sale1")
    shelf = (await ok(gd, "kb.save_shelf", name=f"Kệ chứng từ {uuid.uuid4().hex[:4]}", roles=["director"]))["key"]
    doc = await _invoice_doc(gd, shelf)
    kb.EXTRACT_MODEL = _model(READ)
    try:
        assert (await executor.run(sale1, "kb.extract", {"document_id": doc, "doc_type": DT})).status == "denied"   # chỉ đọc kho ⇒ không ghi
        r = await executor.run(kt, "kb.extract", {"document_id": doc, "doc_type": DT})
        assert r.status == "not_found"                                                       # ghi được kho nhưng không đọc được kệ ⇒ không trích
        mine = await ok(gd, "kb.extract", document_id=doc, doc_type=DT)
        assert (await executor.query(kt, "kb.extraction", {"extraction_id": str(mine["extraction_id"])})).status == "not_found"
        assert (await executor.run(issued(replace(gd, ai=False)), "kb.extract", {"document_id": doc, "doc_type": DT})).status == "denied"

        async with db.owner.connection() as conn:
            on = await packs.enabled(conn, gd.tenant_id)
        off = next((mod for mod, pk in registry.MODULE_PACK.items() if pk not in on), None)
        if off is not None:                                                                               # loại chứng từ của gói chưa bật
            registry.DOC_TYPES["x.off"] = DocType("x.off", "Chứng từ gói tắt", off, FIELDS[:1])
            try:
                assert (await executor.run(gd, "kb.extract", {"document_id": doc, "doc_type": "x.off"})).status == "denied"
                assert "x.off" not in {o["value"] for o in await q(gd, "kb.doc_type_options")}
            finally:
                registry.DOC_TYPES.pop("x.off", None)
    finally:
        kb.EXTRACT_MODEL = None


async def test_a_scanned_original_is_flagged_so_people_check_the_page_image(who: Who, invoice_type: DocType) -> None:
    from pypdf import PdfWriter

    gd = await who("giamdoc")
    doc = await _invoice_doc(gd)
    buf = io.BytesIO()
    w = PdfWriter()
    w.add_blank_page(595, 842)   # trang ảnh, không lớp chữ ⇒ bản scan
    w.write(buf)
    await ok(gd, "kb.attach_original", document_id=doc, filename="hoa-don-scan.pdf", content_type="application/pdf",
             data_b64=base64.b64encode(buf.getvalue()).decode())
    kb.EXTRACT_MODEL = _model(READ[:2])
    try:
        out = await ok(gd, "kb.extract", document_id=doc, doc_type=DT)
    finally:
        kb.EXTRACT_MODEL = None
    page = await q(gd, "kb.extraction", extraction_id=out["extraction_id"])
    assert any(b["type"] == "callout" and b["title"] == "Chứng từ là bản scan" for b in page["blocks"])


def test_doc_type_declaration_rejects_bad_shapes() -> None:
    m = registry.Module.__new__(registry.Module)
    m.key = "kb"
    with pytest.raises(ValueError, match="kiểu ô lạ"):
        m.doc_type("bad_kind", "Sai", (F("a", "A", "money"),))   # type: ignore[arg-type]
    with pytest.raises(ValueError, match="không trùng khoá"):
        m.doc_type("dup", "Trùng", (F("a", "A"), F("a", "B")))
    assert "kb.bad_kind" not in registry.DOC_TYPES and "kb.dup" not in registry.DOC_TYPES


async def test_upload_shelves_lists_only_shelves_you_can_add_to_narrowest_first(who: Who) -> None:
    """Hộp tải lên khi thả chứng từ vào chat (review 04/11): không tự chọn kệ "Chung" — liệt kê kệ người đó THÊM được, kèm ai đọc, kệ hẹp trước."""
    gd, kt = await who("giamdoc"), await who("ketoan")
    key = (await ok(gd, "kb.save_shelf", name=f"Kệ hẹp {uuid.uuid4().hex[:4]}", roles=["director"]))["key"]
    mine = await q(gd, "kb.upload_shelves")
    keys = [r["value"] for r in mine]
    assert key in keys and keys.index(key) < keys.index("common") and keys[-1] == "common"   # kệ cả công ty sau cùng
    assert all(r["audience"] for r in mine) and next(r for r in mine if r["value"] == "common")["everyone"] is True
    assert key not in [r["value"] for r in await q(kt, "kb.upload_shelves")]                 # không đọc được kệ ⇒ không thêm vào được
