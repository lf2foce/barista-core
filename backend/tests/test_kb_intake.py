"""Nhập tài liệu bằng THẢ FILE (0225): không điền gì trước khi thả; máy đọc + đoán, người xác nhận. Mọi khẳng định đi qua cửa HTTP thật
(``POST /v1/kb/upload``) rồi kb.search / kb.document như người dùng."""

import io
import uuid
import zipfile
from typing import Any
from urllib.parse import quote

import pytest
from docx import Document
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from pptx import Presentation
from pydantic_ai import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from barista.config import settings
from barista.docread import office, titles
from barista.http_api import app
from barista.kernel.errors import Invalid
from barista.modules.kb import intake, readers
from tests.conftest import Who, ok, owner_val, q


async def _post(p: Any, name: str, data: bytes, **params: str) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.post("/v1/kb/upload", params=params, content=data,
                            headers={"X-Dev-User": p.email, "X-Tenant-Id": str(p.tenant_id), "X-Filename": quote(name)})


def _docx(title: str, text: str, heading: bool = True) -> bytes:
    d = Document()
    if heading:
        d.add_heading(title, level=1)
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Bảng giá"
    ws.append(["Căn", "Diện tích", "Giá bán"])
    ws.append(["A-0101", 52.5, 2850000000])
    ws.append(["A-0102", 61, 3320000000])
    ws.append([f"M-{uuid.uuid4().hex[:8]}", 0, 0])   # mỗi lần dựng một dấu SHA khác — kho dùng chung giữa các test
    hidden = wb.create_sheet("Nháp")
    hidden.sheet_state = "hidden"
    hidden.append(["không được đọc"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _pptx() -> bytes:
    prs = Presentation()
    for title, text in (("Chính sách chiết khấu quý IV", "Chiết khấu 2% cho khách thanh toán sớm"),
                        ("Điều kiện áp dụng", "Chỉ áp dụng căn thuộc giỏ hàng ưu tiên")):
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        title_box, body_box = slide.placeholders[0], slide.placeholders[1]
        title_box.text_frame.text = title   # type: ignore[attr-defined]
        body_box.text_frame.text = text     # type: ignore[attr-defined]
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _png() -> bytes:
    import random

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (random.randrange(256), random.randrange(256), random.randrange(256))).save(buf, "PNG")   # mỗi ảnh một dấu SHA khác
    return buf.getvalue()


def _model(args: dict[str, Any]) -> Any:
    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    return FunctionModel(reply, model_name="intake-fake")


# ── đọc từng loại file (thuần) ──

def test_spreadsheets_become_text_tables_one_section_per_visible_sheet() -> None:
    text = office.sheets_text("gia.xlsx", _xlsx())
    assert text.startswith("# Bảng giá\nCăn | Diện tích | Giá bán\nA-0101 | 52.5 | 2850000000")
    assert "không được đọc" not in text                       # sheet ẩn bị bỏ
    csv_text = office.sheets_text("gia.csv", "Mã;Tên\nHSR;Hồ Sen\n".encode("utf-8-sig"))
    assert "Mã | Tên" in csv_text and "HSR | Hồ Sen" in csv_text
    cp = office.sheets_text("gia.csv", b"M\xe3,T\xean\r\nHSR,H\xf4\xcc Sen\r\n")     # Excel cũ xuất theo bảng mã Windows
    assert "Hồ Sen" in cp
    long = office.sheets_text("dai.csv", ("Mã,Giá\n" + "".join(f"M{i},{i}\n" for i in range(60))).encode())
    assert long.count("Mã | Giá") >= 4                         # tiêu đề cột nhắc lại để đoạn cắt nào cũng mang tên cột


def test_old_xls_without_libreoffice_says_what_to_do(monkeypatch: pytest.MonkeyPatch) -> None:
    from barista.kernel import convert

    monkeypatch.setattr(convert, "binary", lambda: None)
    with pytest.raises(Invalid, match=r"Lưu thành.*\.xlsx"):
        office.sheets_text("cu.xls", b"\xd0\xcf\x11\xe0 khong phai")


def test_slides_become_pages_with_titles_as_headings() -> None:
    from barista.modules.kb import chunks

    text = office.slides_text(_pptx())
    assert "## Trang 1\n# Chính sách chiết khấu quý IV" in text and "## Trang 2\n# Điều kiện áp dụng" in text
    parts = chunks(text)
    assert [(h, pg) for h, _, pg in parts] == [("Chính sách chiết khấu quý IV", 1), ("Điều kiện áp dụng", 2)]   # trích nguồn "trang N" = slide N


def test_zip_expansion_skips_junk_nested_zips_and_unsupported_and_blocks_bombs() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hop-dong/a.docx", _docx("A", "a"))
        z.writestr("__MACOSX/hop-dong/._a.docx", b"junk")
        z.writestr(".DS_Store", b"x")
        z.writestr("~$tam.docx", b"x")
        z.writestr("hop-dong/lồng.zip", b"PK")
        z.writestr("hop-dong/video.mp4", b"x")
        z.writestr("hop-dong/b.csv", b"a,b\n1,2\n")
    entries, skipped = readers.expand_zip(buf.getvalue())
    assert [n for n, _ in entries] == ["a.docx", "b.csv"]
    assert {s["filename"] for s in skipped} == {"lồng.zip", "video.mp4"}      # rác hệ điều hành bỏ im lặng, còn lại nói rõ lý do
    with pytest.raises(Invalid, match=r"không phải ZIP|hỏng"):
        readers.expand_zip(b"khong phai zip")
    big = io.BytesIO()
    with zipfile.ZipFile(big, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(3):
            z.writestr(f"x{i}.txt", b"0" * (45 * 1024 * 1024))
    with pytest.raises(Invalid, match="quá"):
        readers.expand_zip(big.getvalue())


def test_title_comes_from_the_document_not_the_filename() -> None:
    assert readers.hint_title("scan_0001.docx", _docx("Quy chế thưởng quý IV", "nội dung"), "# Quy chế thưởng quý IV\nnội dung") == "Quy chế thưởng quý IV"
    assert readers.hint_title("quy_che-thuong_q4.pdf", b"khong phai pdf", "") == "Quy che thuong q4"      # không có gì trong file ⇒ tên file đã làm sạch + viết hoa chữ đầu
    assert readers.title_from_filename("Chinh_sach-ban_hang T11.docx") == "Chinh sach ban hang T11"
    assert titles.sane_title("Microsoft Word - ban nhap.docx") == "" and titles.sane_title("Document1") == ""


# ── thả file qua HTTP: không tham số nào ──

async def test_dropping_a_word_file_needs_nothing_typed(who: Who) -> None:
    ketoan = await who("ketoan")
    w = "DRP" + uuid.uuid4().hex[:6].upper()
    r = await _post(ketoan, "quy-che-rac-ten-file.docx", _docx(f"Quy chế hoàn ứng {w}", f"Hoàn ứng trong 7 ngày làm việc, mã {w}."))
    assert r.status_code == 200, r.text
    out = r.json()["output"]
    (d,) = out["documents"]
    assert d["status"] == "added" and d["title"] == f"Quy chế hoàn ứng {w}" and d["kind"] == "other"      # tên từ đầu mục, không từ tên file
    assert d["document_id"] == out["document_id"] and d["shelf"] and d["context_code"] == ""
    assert "title" in d["guessed"] and d["filename"] == "quy-che-rac-ten-file.docx"
    page = await q(ketoan, "kb.document", document_id=d["document_id"])
    assert page["original"]["kind"] == "pdf" and "nguồn: quy-che-rac-ten-file.docx" in page["subtitle"]       # nguồn = tên file; file gốc lưu kèm
    assert (await q(ketoan, "kb.search", q=w))["rows"]                                                          # đã tìm thấy được ngay
    assert await owner_val("SELECT sha256 IS NOT NULL FROM kb.files WHERE document_id = %s", (d["document_id"],))


async def test_same_file_twice_is_skipped_and_says_which_document(who: Who) -> None:
    ketoan = await who("ketoan")
    data = _docx(f"Biểu phí {uuid.uuid4().hex[:6]}", "Phí 1%.")
    first = (await _post(ketoan, "bieu-phi.docx", data)).json()["output"]
    again = (await _post(ketoan, "bieu-phi-copy.docx", data)).json()["output"]
    (d,) = again["documents"]
    assert d["status"] == "duplicate" and d["duplicate_of"]["id"] == first["document_id"] and "Trùng với" in d["note"]
    assert again["added"] == 0 and again["document_id"] == first["document_id"] and again["duplicates"] == 1


async def test_shelf_defaults_to_the_common_shelf_never_silently_a_restricted_one(who: Who) -> None:
    gd = await who("giamdoc")
    opts = await q(gd, "kb.intake_options")
    restricted = [s for s in opts["shelves"] if not s["everyone"]]
    assert restricted and opts["shelf"] == "common"
    await ok(gd, "kb.add_document", title=f"Lần trước {uuid.uuid4().hex[:6]}", shelf=restricted[0]["value"], body="x")
    assert (await q(gd, "kb.intake_options"))["shelf"] == "common"                        # dùng kệ hạn chế lần trước KHÔNG kéo theo lần sau
    d = (await _post(gd, "moi.docx", _docx(f"Mới {uuid.uuid4().hex[:6]}", "nội dung"))).json()["output"]["documents"][0]
    assert d["shelf"] == "common" and d["shelf_everyone"] is True
    # người CHỌN kệ khác (web gửi shelf cho các file sau của lần thả) ⇒ tài liệu nằm đó và trả về ai đọc được để hiện chip "hạn chế"
    r = (await _post(gd, "rieng.docx", _docx(f"Riêng {uuid.uuid4().hex[:6]}", "nội dung riêng"), shelf=restricted[0]["value"])).json()["output"]["documents"][0]
    assert r["shelf"] == restricted[0]["value"] and r["shelf_everyone"] is False and r["shelf_audience"] == restricted[0]["audience"]


async def test_title_of_a_bare_filename_is_prettified_by_the_model_or_capitalised(who: Who) -> None:
    gd = await who("giamdoc")
    w = uuid.uuid4().hex[:6]
    plain = (await _post(gd, f"bang_gia_hso_{w}.xlsx", _xlsx())).json()["output"]["documents"][0]
    assert plain["title"] == f"Bang gia hso {w}" and "title" not in plain["guessed"]       # không có model: viết hoa chữ đầu, giữ nguyên
    seen: list[str] = []

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(repr(messages))
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"title": f"Bảng giá HSO {w}", "kind": "other"})])

    intake.INTAKE_MODEL = FunctionModel(reply, model_name="pretty-fake")
    try:
        out = (await _post(gd, f"bang_gia_hso_{w}b.xlsx", _xlsx())).json()["output"]["documents"][0]
    finally:
        intake.INTAKE_MODEL = None
    assert out["title"] == f"Bảng giá HSO {w}" and "title" in out["guessed"]
    assert "ĐỦ DẤU" in seen[0] and f"bang_gia_hso_{w}b.xlsx" in seen[0]                      # model được nhờ viết lại có dấu và thấy tên file


async def test_dropping_a_spreadsheet_a_deck_and_a_csv_keeps_the_originals(who: Who) -> None:
    gd = await who("giamdoc")
    w = uuid.uuid4().hex[:6]
    x = (await _post(gd, f"gia-{w}.xlsx", _xlsx())).json()["output"]["documents"][0]
    p = (await _post(gd, f"ck-{w}.pptx", _pptx())).json()["output"]["documents"][0]
    c = (await _post(gd, f"bang-{w}.csv", f"Mã,Mô tả\nZQ{w},Đặt cọc 50 triệu\n".encode())).json()["output"]["documents"][0]
    assert x["title"] == f"Gia {w}" and p["title"] == "Chính sách chiết khấu quý IV" and c["status"] == "added"
    hit = (await q(gd, "kb.search", q="3320000000"))["rows"]
    assert any(h["document_id"] == x["document_id"] for h in hit)
    slide = (await q(gd, "kb.search", q="giỏ hàng ưu tiên"))["rows"]
    assert any(h["document_id"] == p["document_id"] and h["page"] == 2 for h in slide)          # slide 2 ⇒ trang 2
    for d in (x, p, c):
        got = await q(gd, "kb.original", document_id=d["document_id"])
        assert got["filename"].endswith((".xlsx", ".pptx", ".csv"))


async def test_zip_becomes_many_documents_and_one_bad_file_does_not_sink_the_rest(who: Who) -> None:
    gd = await who("giamdoc")
    w = uuid.uuid4().hex[:6]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"a-{w}.docx", _docx(f"Quy trình A {w}", "bước 1"))
        z.writestr(f"b-{w}.docx", _docx(f"Quy trình B {w}", "bước 2"))
        z.writestr(f"hong-{w}.pdf", b"khong phai pdf")
        z.writestr("__MACOSX/._a.docx", b"x")
    out = (await _post(gd, f"goi-{w}.zip", buf.getvalue())).json()["output"]
    assert out["added"] == 2 and [d["title"] for d in out["documents"]] == [f"Quy trình A {w}", f"Quy trình B {w}"]
    assert [s["filename"] for s in out["skipped"]] == [f"hong-{w}.pdf"] and out["skipped"][0]["reason"]


async def test_unsupported_and_empty_files_get_a_reason(who: Who) -> None:
    gd = await who("giamdoc")
    r = await _post(gd, "phim.mp4", b"x" * 100)
    assert r.status_code == 422 and "mp4" in r.text and "ZIP" in r.text
    assert (await _post(gd, "trong.docx", b"")).status_code == 422


# ── máy đoán, người xác nhận ──

async def test_model_guesses_title_kind_and_scope_but_only_from_existing_options_and_validity_stays_a_proposal(who: Who) -> None:
    gd = await who("giamdoc")
    w = "MG" + uuid.uuid4().hex[:5].upper()
    await ok(gd, "kb.add_document", title=f"Quy chế thưởng {w} cũ", kind="policy", context_code=w, body="Thưởng cũ.")
    old_id = (await q(gd, "kb.current_options"))
    old_id = next(o["value"] for o in old_id if o["label"] == f"Quy chế thưởng {w} cũ")
    body_line = "Áp dụng từ ngày 01/01/2027 cho toàn bộ nhân viên"
    intake.INTAKE_MODEL = _model({"title": f"Quy chế thưởng {w} (bản 2027)", "kind": "policy", "context_code": w.lower(),
                                  "effective_from": "2027-01-01", "evidence": body_line, "replaces_id": old_id, "replaces_reason": "bản mới"})
    try:
        out = (await _post(gd, "q.docx", _docx("Quy chế", f"{body_line}. Mã {w}."))).json()["output"]
    finally:
        intake.INTAKE_MODEL = None
    (d,) = out["documents"]
    assert d["title"] == f"Quy chế thưởng {w} (bản 2027)" and d["kind"] == "policy" and d["context_code"] == w     # mã chọn trong danh sách có sẵn
    assert set(d["guessed"]) == {"title", "kind", "context_code"}
    assert d["effective_from"] is None and d["suggestion"]["effective_from"] == "2027-01-01" and d["suggestion"]["replaces_id"] == old_id
    assert (await q(gd, "kb.document", document_id=d["document_id"]))["subtitle"].startswith("Đang dùng")       # đề xuất chưa đổi gì
    # mã không có trong danh sách ⇒ bỏ, không bao giờ tạo mã mới
    intake.INTAKE_MODEL = _model({"title": "Tài liệu khác", "kind": "faq", "context_code": "BIA-RA-MOI"})
    try:
        d2 = (await _post(gd, "r.docx", _docx("R", f"nội dung khác {w}"))).json()["output"]["documents"][0]
    finally:
        intake.INTAKE_MODEL = None
    assert d2["context_code"] == "" and d2["kind"] == "faq"


async def test_a_similar_title_becomes_a_replace_proposal_without_a_model(who: Who) -> None:
    gd = await who("giamdoc")
    w = uuid.uuid4().hex[:6]
    old = await ok(gd, "kb.add_document", title=f"Chính sách bán hàng Hồ Sen {w} tháng 10", kind="policy", body="Cũ.")
    d = (await _post(gd, "csbh.docx", _docx(f"Chính sách bán hàng Hồ Sen {w} tháng 11", "Mới."))).json()["output"]["documents"][0]
    assert d["similar"] and d["similar"][0]["id"] == str(old["document_id"])
    assert d["suggestion"]["replaces_id"] == str(old["document_id"])
    page = await q(gd, "kb.document", document_id=d["document_id"])
    assert any(a["command"] == "kb.supersede" for a in page["actions"])                                          # trang tài liệu cũng có nút


async def test_replace_mode_keeps_the_old_title_shelf_and_kind(who: Who) -> None:
    gd = await who("giamdoc")
    w = uuid.uuid4().hex[:6]
    old = await ok(gd, "kb.add_document", title=f"Biểu phí {w}", kind="process", context_code="HSR", body="Cũ.")
    d = (await _post(gd, "moi.docx", _docx("Tên khác hẳn", "Bản mới."), replaces_document_id=str(old["document_id"]))).json()["output"]["documents"][0]
    assert d["title"] == f"Biểu phí {w}" and d["kind"] == "process" and d["context_code"] == "HSR" and d["replaced"] == f"Biểu phí {w}"


async def test_images_are_read_by_the_model_or_stored_with_a_caption(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    gd = await who("giamdoc")
    plain = (await _post(gd, f"anh-{uuid.uuid4().hex[:6]}.png", _png())).json()["output"]["documents"][0]
    assert plain["status"] == "added" and "trợ lý AI" in plain["note"]
    w = "IMG" + uuid.uuid4().hex[:5].upper()
    monkeypatch.setattr(settings, "docread", {"default": {"images_caption": True}})   # mô tả ảnh MẶC ĐỊNH TẮT (tốn token — 0232): công ty bật thì ảnh không chữ vẫn tìm được
    intake.INTAKE_MODEL = _model({"text": "", "caption": f"Phối cảnh toà nhà {w} nhìn từ sông", "title": f"Phối cảnh {w}"})
    try:
        cap = (await _post(gd, f"p-{uuid.uuid4().hex[:4]}.png", _png())).json()["output"]["documents"][0]
    finally:
        intake.INTAKE_MODEL = None
    assert cap["title"] == f"Phối cảnh {w}" and "không có chữ" in cap["note"]
    assert (await q(gd, "kb.search", q=f"phối cảnh toà nhà {w}"))["rows"]                                       # mô tả tìm được; ảnh gốc giữ
    assert (await q(gd, "kb.original", document_id=cap["document_id"]))["content_type"] == "image/png"
    w2 = "OCR" + uuid.uuid4().hex[:5].upper()
    intake.INTAKE_MODEL = _model({"text": f"BẢNG GIÁ {w2}\nCăn A1 | 2,5 tỷ\nCăn A2 | 3 tỷ", "caption": "Ảnh chụp bảng giá", "title": f"Bảng giá {w2}"})
    try:
        t = (await _post(gd, f"b-{uuid.uuid4().hex[:4]}.png", _png())).json()["output"]["documents"][0]
    finally:
        intake.INTAKE_MODEL = None
    assert (await q(gd, "kb.search", q=w2))["rows"] and t["note"] == ""


# ── xác nhận / sửa sau khi thả ──

async def test_update_document_edits_title_scope_validity_and_replaces_with_the_right_guards(who: Who) -> None:
    gd, sale1 = await who("giamdoc"), await who("sale1")
    w = uuid.uuid4().hex[:6]
    old = str((await ok(gd, "kb.add_document", title=f"Mẫu hợp đồng {w}", kind="template", body="Cũ.", shelf="common"))["document_id"])
    d = (await _post(gd, "hd.docx", _docx(f"Hợp đồng mới {w}", f"Nội dung riêng {w}.", heading=True))).json()["output"]["documents"][0]
    did = d["document_id"]
    out = await ok(gd, "kb.update_document", document_id=did, title=f"Mẫu hợp đồng {w} v2", kind="template", effective_from="2026-01-02",
                   replaces_document_id=old)
    assert set(out["changed"]) >= {"tên", "loại", "hiệu lực"}
    page = await q(gd, "kb.document", document_id=did)
    assert page["title"] == f"Mẫu hợp đồng {w} v2" and "thay cho" in page["subtitle"]
    assert (await q(gd, "kb.search", q=f"mẫu hợp đồng {w} v2"))["rows"]                                          # đổi tên ⇒ cắt lại chữ tìm
    r = await ok(gd, "kb.update_document", document_id=did, no_context=True, context_code="")
    assert "phạm vi" in r["changed"]
    from barista.kernel import executor

    bad = await executor.run(gd, "kb.update_document", {"document_id": did, "shelf": "management"})
    assert bad.status != "ok" and "bản thay" in bad.error                                                      # đã dính chuỗi thay bản ⇒ không chuyển kệ
    other = await executor.run(sale1, "kb.update_document", {"document_id": did, "title": "Sửa trộm"})
    assert other.status != "ok"
    assert (await ok(gd, "kb.update_document", document_id=did))["summary"] == "Không có gì đổi"


def test_paste_form_no_longer_asks_for_source_or_a_free_text_code() -> None:
    from barista.kernel import forms, registry

    spec = forms.form_spec(registry.OPS["kb.add_document"])
    byk = {f["key"]: f for f in spec["fields"]}
    assert byk["source"].get("hidden") and byk["context_code"].get("options_query") == "kb.context_options"


def test_intake_contract_is_documented_in_the_module_docstring() -> None:
    doc = intake.__doc__ or ""
    assert "POST /v1/kb/upload" in doc and "một file" in doc.lower() and "kb.update_document" in doc


# ── ngày theo lệ nước của công ty (0169) ──

def test_validity_dates_follow_the_company_country_for_ambiguous_numbers() -> None:
    from barista.modules.kb import ValidityGuess, long_date, verified_validity

    body = "Văn bản này có hiệu lực từ 01/12/2027 và hết hạn 03/04/2028."
    ev = "có hiệu lực từ 01/12/2027 và hết hạn 03/04/2028"
    from datetime import date
    now = date(2026, 11, 14)
    for country, want_from, want_to in (("VN", "2027-12-01", "2028-04-03"), ("US", "2027-01-12", "2028-03-04")):
        for model_from, model_to in ((date(2027, 12, 1), date(2028, 4, 3)), (date(2027, 1, 12), date(2028, 3, 4))):    # model đọc theo lệ nào cũng ra lệ công ty
            got = verified_validity(ValidityGuess(effective_from=model_from, effective_to=model_to, evidence=ev), body, {}, country=country, now=now)
            assert got and (got["effective_from"], got["effective_to"]) == (want_from, want_to), (country, model_from)
    unambiguous = verified_validity(ValidityGuess(effective_from=date(2027, 1, 12), evidence="áp dụng từ ngày 13/12/2027"), "áp dụng từ ngày 13/12/2027", {},
                                    country="VN", now=now)
    assert unambiguous == {"effective_from": "2027-12-13", "evidence": "áp dụng từ ngày 13/12/2027"}               # chỉ một cách đọc hợp lệ ⇒ lấy theo câu
    english = verified_validity(ValidityGuess(effective_from=date(2027, 1, 12), evidence="effective December 1, 2027"), "Effective December 1, 2027.", {},
                                country="VN", now=now)
    assert english and english["effective_from"] == "2027-12-01"                                                    # ngày viết bằng chữ đọc đúng chữ, bất kể nước
    assert long_date(date(2027, 12, 1)) == "1 tháng 12, 2027"


async def test_upload_reads_ambiguous_dates_by_company_country_and_shows_them_unambiguously(who: Who) -> None:
    admin, gd = await who("admin"), await who("giamdoc")
    line = "Chính sách này có hiệu lực từ 01/12/2027"
    seen: list[str] = []

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(repr(messages[-1]))
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"effective_from": "2027-01-12", "evidence": line})])

    async def drop(tag: str) -> dict[str, Any]:
        intake.INTAKE_MODEL = FunctionModel(reply, model_name="date-fake")
        try:
            return (await _post(gd, f"ngay-{tag}.docx", _docx(f"Chính sách ngày {tag}", f"{line}. Mã {tag}."))).json()["output"]["documents"][0]
        finally:
            intake.INTAKE_MODEL = None

    vn = await drop(uuid.uuid4().hex[:6])
    assert vn["suggestion"]["effective_from"] == "2027-12-01" and any("NGÀY trước THÁNG" in s for s in seen)
    page = await q(gd, "kb.document", document_id=vn["document_id"])
    assert "1 tháng 12, 2027" in page["sections"][0]["text"]                                                         # chip / gợi ý hiện ngày không nhập nhằng
    try:
        await ok(admin, "admin.set_country", country="us")
        us = await drop(uuid.uuid4().hex[:6])
        assert us["suggestion"]["effective_from"] == "2027-01-12" and any("THÁNG trước NGÀY" in s for s in seen)
        assert (await q(gd, "kb.intake_options"))["country"] == "US"
    finally:
        await ok(admin, "admin.set_country", country="VN")
