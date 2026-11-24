"""Năng lực ĐỌC TÀI LIỆU (ADR 0232): bộ đọc cắm được, bảng có tiêu đề, chuyển đúng trang cho model, trần + công tắc + hạn mức, bộ nhớ theo băm file, ảnh, cấu hình theo công ty, benchmark.
Model giả (FunctionModel) — không gọi mạng. Tài liệu giả ở ``barista/docread/fixtures`` (đáp án ở truth.json)."""

import io
import re
import uuid
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic_ai import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from barista import docread, seed
from barista.agent import llm, usage
from barista.config import settings
from barista.docread import bench, policy, types
from barista.docread.readers import model as model_reader
from barista.kernel import executor
from barista.kernel.principal import Principal, system_principal
from barista.modules import kb
from tests.conftest import owner_val

FIX = Path(docread.__file__).parent / "fixtures"
PEOPLE = [("qt", "Quản trị", "admin", "QT", True), ("sale1", "Sale Một", "sale", "KD-A", False)]
OP = {"by": "Nhà vận hành (test)", "why": "kiểm thử"}


def fx(name: str) -> bytes:
    return (FIX / name).read_bytes()


class Spy:
    """Model giả đọc theo đáp án và GHI những trang / ảnh đã được gửi (để kiểm "chỉ trang cần mới tốn tiền")."""

    def __init__(self, caption: str = "Phối cảnh có hồ bơi") -> None:
        self.calls: list[list[int]] = []
        self.rules: list[str] = []
        self.caption = caption
        self.truth = bench.json.loads((FIX / "truth.json").read_text(encoding="utf-8"))

    def model(self, fixture: str) -> FunctionModel:
        def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.rules.append(str(getattr(messages[0], "instructions", "")))
            if info.output_tools:
                self.calls.append([1])
                t = self.truth[fixture]
                return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"text": bench.render_blocks(t["pages"][0]), "caption": self.caption, "title": "Ảnh"})])
            m = re.search(r"các trang gốc số ([\d, ]+)\.", repr(messages[-1]))
            pages = [int(x) for x in m.group(1).replace(" ", "").split(",")] if m else [1]
            self.calls.append(pages)
            blocks = self.truth[fixture]["pages"]
            return ModelResponse(parts=[TextPart("\n".join(f"## Trang {n}\n" + bench.render_blocks(blocks[n - 1]) for n in pages))])

        return FunctionModel(reply, model_name="spy")


def pol(**kw: Any) -> policy.Policy:
    return policy.Policy(purpose="test", **{"max_model_pages": 50, **kw})


async def read(name: str, p: Any = None, *, model: Any = None, **kw: Any) -> types.Doc:
    return await docread.read(p or system_principal(uuid4()), name, fx(name), purpose="kb", model=model, cache=False, meter=False, **kw)


# ── bộ đọc miễn phí: bảng ──

async def test_a_text_pdf_keeps_its_tables_with_header_and_cells_for_free() -> None:
    doc = await read("bang-gia.pdf", policy=pol(chain=("office", "pypdf"), tables="auto"))
    assert [t.header for t in doc.tables] == [["STT", "Mã căn", "Diện tích (m²)", "Giá bán (VND)", "Ghi chú"], ["Đợt", "Thời điểm thanh toán", "Tỷ lệ", "Số tiền (VND)"]]
    assert doc.tables[0].rows[2] == ["3", "A-0610", "75,2", "3.120.000.000", "Góc, view sông"] and len(doc.tables[1].rows) == 6
    assert "| 3 | A-0610 | 75,2 | 3.120.000.000 | Góc, view sông |" in doc.text() and "| --- |" in doc.text()
    assert doc.methods == {1: "pdftable", 2: "pdftable"} and doc.usage.model_pages == 0 and doc.usage.tokens == 0   # miễn phí


async def test_tables_off_keeps_the_plain_pypdf_text_and_flattens_columns() -> None:
    doc = await read("bang-gia.pdf", policy=pol(chain=("office", "pypdf"), tables="off"))
    assert not doc.tables and "|" not in doc.text() and doc.methods == {1: "pypdf", 2: "pypdf"}


async def test_word_tables_stay_in_reading_order_and_sheets_expose_one_table_per_sheet() -> None:
    doc = await read("hop-dong.docx", policy=pol())
    text = doc.text()
    assert text.index("# Điều 1. Các bên") < text.index("| Hạng mục | Nội dung |") < text.index("# Điều 2. Giá trị")   # bảng không rơi mất, đúng chỗ
    assert doc.tables[0].rows[2] == ["Tiền đặt cọc", "122.500.000 đồng"] and not doc.paged
    sheets = await read("bang-tinh.xlsx", policy=pol())
    assert [t.header for t in sheets.tables] == [["Mã căn", "Diện tích", "Giá bán"], ["Chương trình", "Ưu đãi"]] and sheets.methods == {1: "office"}


def test_markdown_tables_round_trip_and_ragged_columns_are_padded() -> None:
    t = types.Table(["a", "b|x"], [["1"], ["2", "3", "4"]])
    md = t.markdown()
    assert md.splitlines()[0] == "| a | b/x |  |" and md.splitlines()[1].startswith("| ---")   # "|" trong ô không phá bảng; dòng ngắn được đệm
    back = types.parse_md_tables("chữ\n" + md + "\nchữ")
    assert back[0].header[0] == "a" and len(back[0].rows) == 2


def test_chunks_repeat_the_table_header_in_every_part_even_across_pages() -> None:
    head = "| Mã | Giá |\n| --- | --- |"
    rows = [f"| A-{i:03d} | {i * 1000}.000.000 |" for i in range(120)]
    body = "## Trang 1\n# Bảng giá\n" + head + "\n" + "\n".join(rows[:60]) + "\n## Trang 2\n" + "\n".join(rows[60:]) + "\nHết bảng. Điều khoản chung áp dụng."
    parts = kb.chunks(body)
    table_parts = [t for _, t, _ in parts if t.startswith("| Mã | Giá |") or "| A-" in t]
    assert len(table_parts) >= 4 and all(t.startswith(head) for t in table_parts)         # mỗi đoạn có tên cột
    assert {p for _, t, p in parts if "| A-0" in t and "A-100" in t} == {2} or any(p == 2 for _, _, p in parts)
    assert any(p == 2 and t.startswith(head) for _, t, p in parts)                       # bảng tiếp sang trang 2 vẫn mang tiêu đề
    assert not any(t.count(head) > 1 for _, t, _ in parts)                               # không nhắc hai lần trong một đoạn


# ── chuyển đúng trang cho model ──

async def test_only_the_scanned_page_goes_to_the_model_and_text_pages_stay_free() -> None:
    spy = Spy()
    doc = await read("hon-hop.pdf", model=spy.model("hon-hop.pdf"), policy=pol(chain=("office", "pypdf", "model")))
    assert spy.calls == [[2]]                                                           # trang 1 có chữ ⇒ miễn phí; chỉ trang 2 (scan) gửi model
    assert doc.methods == {1: "pdftable", 2: "model"} and doc.usage.model_pages == 1 and doc.usage.requests == 1 and doc.usage.tokens > 0
    assert "## Trang 2\nĐIỀU KHOẢN ĐẶT CỌC" in doc.text() and doc.unread() == [] and not doc.notices


async def test_page_numbers_survive_a_model_that_renumbers_pages_from_one() -> None:
    texts, sure = model_reader.split_pages("## Trang 1\nA\n## Trang 2\nB", [4, 9])
    assert texts == {4: "A", 9: "B"} and sure is False                                  # ghép theo thứ tự, đánh dấu không chắc
    texts, sure = model_reader.split_pages("## Trang 9\nB\n## Trang 4\nA", [4, 9])
    assert texts == {9: "B", 4: "A"} and sure is True


async def test_model_first_chain_reads_every_pdf_page_and_a_ragged_table_goes_to_the_model_in_auto() -> None:
    spy = Spy()
    doc = await read("bang-gia.pdf", model=spy.model("bang-gia.pdf"), policy=pol(chain=("office", "model"), tables="model"))
    assert spy.calls == [[1, 2]] and doc.methods == {1: "model", 2: "model"} and doc.tables[0].header[0] == "STT"
    spy2 = Spy()
    ok = await read("bang-gia.pdf", model=spy2.model("bang-gia.pdf"), policy=pol(chain=("office", "pypdf", "model"), tables="auto"))
    assert spy2.calls == [] and ok.methods == {1: "pdftable", 2: "pdftable"}            # bảng trích đủ cột ⇒ không tốn model


# ── trần, công tắc, AI tắt, lỗi: không bao giờ im lặng ──

async def test_switches_caps_and_failures_fall_back_to_free_text_with_a_clear_notice() -> None:
    spy = Spy()
    off = await read("phieu-scan.pdf", model=spy.model("phieu-scan.pdf"), policy=pol(scanned_pdf=False))
    assert spy.calls == [] and off.unread() == [1] and off.notice(types.SWITCHED_OFF)
    nomodel = await read("phieu-scan.pdf", model=spy.model("phieu-scan.pdf"), policy=pol(chain=("office", "pypdf")))
    assert spy.calls == [] and nomodel.unread() == [1] and not nomodel.usage.requests          # chuỗi không có model ⇒ không bao giờ tốn model
    capped = await read("hon-hop.pdf", model=spy.model("hon-hop.pdf"), policy=pol(max_model_pages=0))
    assert spy.calls == [] and (n := capped.notice(types.PAGE_CAP)) and n.pages == [2] and capped.unread() == [2]
    assert "Trang 1" in capped.text() and "BẢNG GIÁ" in capped.text()                  # trang chữ vẫn đọc được

    def boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("nhà cung cấp sập")

    bad = await read("hon-hop.pdf", model=FunctionModel(boom, model_name="boom"), policy=pol())
    assert (m := bad.notice(types.MODEL_ERROR)) and "nhà cung cấp sập" in m.message and bad.unread() == [2] and "BẢNG GIÁ" in bad.text()


async def test_company_ai_off_never_builds_a_model() -> None:
    p = Principal(tenant_id=uuid4(), user_id=None, ai=False)
    doc = await read("phieu-scan.pdf", p, policy=pol())
    assert doc.notice(types.AI_OFF) and doc.unread() == [1] and doc.usage.requests == 0


async def test_mixed_scan_larger_than_the_cap_reads_the_first_pages_and_reports_the_rest() -> None:
    spy = Spy()
    cap = await read("hon-hop.pdf", model=spy.model("hon-hop.pdf"), policy=pol(chain=("office", "model"), tables="model", max_model_pages=1))
    assert spy.calls == [[1]] and (n := cap.notice(types.PAGE_CAP)) and n.pages == [2] and cap.methods == {1: "model", 2: ""}


# ── ảnh: bốn chế độ ──

def _noisy(w: int = 700, h: int = 500, seed_: int = 1) -> Any:
    """Ảnh nhiều khối màu ngẫu nhiên (không phải một màu — bộ lọc ảnh rác sẽ bỏ ảnh phẳng)."""
    import random

    from PIL import Image, ImageDraw

    r = random.Random(seed_)
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for x in range(0, w, 50):
        for y in range(0, h, 50):
            d.rectangle([x, y, x + 49, y + 49], fill=(r.randrange(256), r.randrange(256), r.randrange(256)))
    return im


def test_images_are_extracted_for_free_from_pdf_word_and_slides_and_deduplicated() -> None:
    from docx import Document
    from docx.shared import Inches
    from pptx import Presentation
    from pptx.util import Length

    im = _noisy()
    png = io.BytesIO()
    im.save(png, "PNG")
    tiny = io.BytesIO()
    _noisy(40, 40, 2).save(tiny, "PNG")
    pdf = io.BytesIO()
    im.save(pdf, "PDF")
    got = docread.extract_images("a.pdf", pdf.getvalue())
    assert len(got) == 1 and got[0].page == 1 and got[0].width == 700 and len(got[0].sha256) == 64 and got[0].data[:2] == b"\xff\xd8"
    doc = Document()
    for blob in (png, png, tiny):                                      # cùng ảnh hai lần + một logo nhỏ
        blob.seek(0)
        doc.add_picture(blob, width=Inches(2))
    buf = io.BytesIO()
    doc.save(buf)
    assert len(docread.extract_images("a.docx", buf.getvalue())) == 1  # trùng bị bỏ, logo nhỏ bị lọc
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    png.seek(0)
    slide.shapes.add_picture(png, Length(0), Length(0))
    out = io.BytesIO()
    prs.save(out)
    s = docread.extract_images("a.pptx", out.getvalue())
    assert len(s) == 1 and s[0].page == 1
    assert docread.extract_images("a.txt", b"xin chao") == [] and docread.extract_images("a.pdf", b"khong phai pdf") == []   # lỗi ⇒ rỗng, không ném


async def test_image_ocr_and_caption_are_separate_switches_and_unused_output_is_not_requested() -> None:
    spy = Spy()
    ocr = await read("hoa-don.png", model=spy.model("hoa-don.png"), policy=pol())    # mặc định: chép chữ BẬT, mô tả TẮT
    ref = ocr.images[0]
    assert "HD-004217" in ocr.text() and ref.caption == "" and "caption: để TRỐNG" in spy.rules[-1] and ocr.usage.model_pages == 1
    assert ocr.tables and ocr.tables[0].header == ["Mặt hàng", "SL", "Đơn giá", "Thành tiền"]   # hoá đơn: bảng dòng hàng có cấu trúc
    cap = await read("phoi-canh.jpg", model=spy.model("phoi-canh.jpg"), policy=pol(images_caption=True, images_ocr=False))
    assert cap.images[0].caption == "Phối cảnh có hồ bơi" and cap.text() == "" and "text: để TRỐNG" in spy.rules[-1]
    n = len(spy.calls)
    none = await read("hoa-don.png", model=spy.model("hoa-don.png"), policy=pol(images_ocr=False))
    assert len(spy.calls) == n and none.notice(types.SWITCHED_OFF) and none.usage.requests == 0   # cả hai tắt ⇒ không gọi model


async def test_extracted_images_are_read_once_per_hash() -> None:
    spy = Spy()
    a, b = docread.extract_images("a.pdf", _pdf_of(_noisy())), docread.extract_images("b.pdf", _pdf_of(_noisy()))
    assert a[0].sha256 == b[0].sha256
    refs, usage_, notices = await docread.read_images(system_principal(uuid4()), [a[0], b[0]], purpose="kb_images", model=spy.model("hoa-don.png"))
    assert len(spy.calls) == 1 and refs[0].text == refs[1].text and "HD-004217" in refs[0].text and usage_.model_pages == 1 and not notices


def _pdf_of(im: Any) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PDF")
    return buf.getvalue()


# ── đơn vị nhúng (chuẩn bị embedding đa phương thức) ──

async def test_a_doc_exposes_stable_embeddable_units_with_page_header_and_media_reference() -> None:
    p = system_principal(uuid4())
    doc = await docread.read(p, "bang-gia.pdf", fx("bang-gia.pdf"), purpose="kb", policy=pol(chain=("office", "pypdf")), cache=False, meter=False)
    units = doc.units()
    assert [(u.kind, u.page) for u in units] == [("text", 1), ("table", 1), ("text", 2), ("table", 2)]
    table = units[1]
    assert table.header[:2] == ("STT", "Mã căn") and table.rows[2][1] == "A-0610" and table.text.startswith("| STT |")
    assert "|" not in units[0].text and "BẢNG GIÁ" in units[0].text                       # chữ trang không lẫn bảng
    again = await docread.read(p, "ban-sao.pdf", fx("bang-gia.pdf"), purpose="kb", policy=pol(chain=("office", "pypdf")), cache=False, meter=False)
    assert [u.key for u in again.units()] == [u.key for u in units] and len({u.key for u in units}) == 4   # khoá theo băm file: ổn định, không trùng
    spy = Spy()
    img = await docread.read(p, "hoa-don.png", fx("hoa-don.png"), purpose="invoice", model=spy.model("hoa-don.png"), policy=pol(images_caption=True), cache=False, meter=False)
    img.pages[0].images[0].file_id = "file-123"                                           # nơi gọi lưu JPEG vào sổ tệp rồi điền mã
    im = next(u for u in img.units() if u.kind == "image")
    ref = im.ref or {}
    assert ref == {"sha256": img.images[0].sha256, "file_id": "file-123", "media_type": "image/jpeg", "width": ref["width"], "height": ref["height"]} and im.alt
    img.audio.append(types.AudioRef(sha256="a" * 64, seconds=3.5, transcript="xin chào", file_id="aud-1"))
    assert next(u for u in img.units() if u.kind == "audio").text == "xin chào"           # chỗ sẵn cho âm thanh: bản chép + tham chiếu tệp


# ── cấu hình là dữ liệu ──

def test_profiles_validate_and_layer_instance_defaults_over_code_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from barista.kernel.errors import Invalid

    for bad in ({"chain": ["office", "khong-co"]}, {"model": "khong-co-nha"}, {"tables": "nua"}, {"max_model_pages": -1}, {"monthly_tokens": 5}, {"la": 1}):
        with pytest.raises(Invalid):
            policy.validate(bad)
    monkeypatch.setattr(settings, "docread", {"default": {"images_caption": True, "max_model_pages": 5}, "purposes": {"invoice": {"images_caption": False, "monthly_tokens": 2_000_000}}})
    kb_p, inv = policy.instance_policy("kb"), policy.instance_policy("invoice")
    assert kb_p.images_caption and kb_p.max_model_pages == 5 and kb_p.source == "bản cài" and kb_p.scanned_pdf
    assert not inv.images_caption and inv.monthly_tokens == 2_000_000 and inv.source == "bản cài/invoice"
    assert policy.Policy().images_caption is False and policy.Policy().fingerprint() != policy.Policy(tables="off").fingerprint()


class _FakeExternal(docread.ExternalReader):
    """Điểm cắm cho dịch vụ tài liệu ngoài (chỉ giao diện): đọc xong điền trang như một bộ đọc."""
    name = "fake_external"
    kinds = frozenset({"pdf"})

    async def read(self, req: docread.Request, doc: types.Doc) -> None:
        for p in doc.pages:
            if "scanned" in p.flags:
                p.text, p.method = "chữ do dịch vụ ngoài đọc", self.name


async def test_an_external_reader_plugs_into_the_chain_by_name() -> None:
    docread.register(_FakeExternal())
    try:
        assert "fake_external" in policy.validate({"chain": ["office", "pypdf", "fake_external"]})["chain"]
        doc = await read("phieu-scan.pdf", policy=pol(chain=("office", "pypdf", "fake_external")))
        assert doc.methods == {1: "fake_external"} and "dịch vụ ngoài" in doc.text()
    finally:
        from barista.docread import readers

        readers._REG.pop("fake_external", None)


# ── benchmark ──

async def test_the_benchmark_runs_with_a_fake_model_and_shows_routing_saves_pages() -> None:
    truth = bench.json.loads((FIX / "truth.json").read_text(encoding="utf-8"))
    cfgs = dict(bench.configs([""]))
    cells = []
    for name in ("free", "free+tables", "auto@(mặc định)", "model-all@(mặc định)"):
        pol_ = cfgs[name]
        for fixture in truth:
            cells.append(await bench.run_one(name, pol_, fixture, truth, bench.fake_model(truth) if pol_.uses_model() else None))
    rows = {r["config"]: r for r in bench.summarise(cells, truth)}
    assert rows["free+tables"]["tables"] > rows["free"]["tables"] and rows["free"]["usd"] == 0 and rows["free"]["model_pages"] == 0
    assert rows["auto@(mặc định)"]["wer"] < 0.01 and rows["auto@(mặc định)"]["pages_ok"] == 1.0 and rows["auto@(mặc định)"]["tables"] == 1.0
    assert rows["auto@(mặc định)"]["model_pages"] < rows["model-all@(mặc định)"]["model_pages"]      # định tuyến: ít trang tốn tiền hơn đọc hết bằng model
    assert "auto@(mặc định)" in bench.render(list(rows.values()), 25_500)
    assert bench.error_rates("a b c", "a b d")[0] == pytest.approx(1 / 3)


# ── cần DB: cấu hình theo công ty, đo theo mục đích, hạn mức tháng, bộ nhớ ──

async def _company(packs: tuple[str, ...] = ("crm",)) -> Any:
    tid = await seed.create_tenant("Công ty đọc tài liệu (test)", f"{uuid.uuid4().hex[:6]}.docread.test", people=PEOPLE, packs=packs)
    return tid


async def _set(tid: Any, purpose: str, **kw: Any) -> dict[str, Any]:
    r = await executor.run(system_principal(tid), "core.set_docread_profile", {"purpose": purpose, **OP, **kw})
    assert r.status == "ok", r.error
    return r.output or {}


async def _principal(tid: Any, email: str = "qt@" ) -> Principal:
    from barista.db import db
    from barista.kernel import auth

    async with db.owner_tx() as c:
        cur = await c.execute("SELECT u.email FROM core.memberships m JOIN core.users u ON u.id = m.user_id WHERE m.tenant_id = %s AND m.role = 'admin' LIMIT 1", (tid,))
        row = await cur.fetchone()
        assert row is not None
        mail = row["email"]
        p = await auth.load(c, tid, email=mail)
    assert p is not None
    return p


async def test_the_company_profile_overrides_the_instance_default_and_only_the_operator_can_set_it() -> None:
    tid = await _company()
    qt = await _principal(tid)
    assert (await policy.resolve(qt, "invoice")).source == "mặc định"
    out = await _set(tid, "invoice", model="google:gemini-3.8-flash", images_caption=True, max_model_pages=3, monthly_tokens=500_000)
    assert "invoice" in out["summary"]
    got = await policy.resolve(qt, "invoice")
    assert got.model == "google:gemini-3.8-flash" and got.images_caption and got.max_model_pages == 3 and got.monthly_tokens == 500_000 and got.source == "công ty"
    assert (await policy.resolve(qt, "kb")).max_model_pages == policy.Policy().max_model_pages        # mục đích khác không bị ảnh hưởng
    await _set(tid, "*", tables="text")
    assert (await policy.resolve(qt, "kb")).tables == "text" and (await policy.resolve(qt, "invoice")).tables == "text"
    await _set(tid, "invoice", clear=["monthly_tokens"])
    assert (await policy.resolve(qt, "invoice")).monthly_tokens is None
    denied = await executor.run(qt, "core.set_docread_profile", {"purpose": "kb", "tables": "off", **OP})
    assert denied.status == "denied"                                                           # quản trị công ty không tự nới trần / đổi model
    bad = await executor.run(system_principal(tid), "core.set_docread_profile", {"purpose": "kb", "chain": ["office", "khong-co"], **OP})
    assert bad.status != "ok" and "bộ đọc" in (bad.error or "")
    shown = (await executor.query(system_principal(tid), "core.docread_profiles", {})).output or {}
    row = {r["purpose"]: r for r in shown["rows"]}["invoice"]
    assert row["effective"]["model"] == "google:gemini-3.8-flash" and row["company_row"]["images_caption"] is True
    cleared = await executor.run(system_principal(tid), "core.clear_docread_profile", {"purpose": "invoice", **OP})
    assert cleared.status == "ok" and (await policy.resolve(qt, "invoice")).source == "công ty"   # còn dòng chung "*"


async def test_a_read_is_metered_per_purpose_and_stops_at_the_monthly_token_budget() -> None:
    tid = await _company()
    qt = await _principal(tid)
    spy = Spy()
    await _set(tid, "invoice", max_model_pages=5, monthly_tokens=1000)
    first = await docread.read(qt, "hoa-don.png", fx("hoa-don.png"), purpose="invoice", model=spy.model("hoa-don.png"), module="kb")
    assert first.usage.model_pages == 1 and "HD-004217" in first.text()
    row = await owner_val("SELECT surface || '/' || category || '/' || requests FROM core.ai_usage WHERE tenant_id = %s ORDER BY id DESC LIMIT 1", (tid,))
    assert row == "docread/docread:invoice/1"                                                     # một dòng dùng AI mỗi lượt gọi, loại theo mục đích
    assert await policy.used_tokens(qt) > 1000
    second = await docread.read(qt, "hoa-don.png", fx("hoa-don.png"), purpose="invoice", model=spy.model("hoa-don.png"))
    assert second.notice(types.OVER_BUDGET) and second.usage.requests == 0 and len(spy.calls) == 1   # hết hạn mức ⇒ không gọi model + nói rõ
    other = await docread.read(qt, "hoa-don.png", fx("hoa-don.png"), purpose="kb", model=spy.model("hoa-don.png"))
    assert other.notice(types.OVER_BUDGET) is None and len(spy.calls) == 2                       # hồ sơ riêng mục đích kb không đặt trần
    shown = (await executor.query(system_principal(tid), "core.docread_profiles", {})).output or {}
    inv = {r["purpose"]: r for r in shown["rows"]}["invoice"]
    assert inv["runs"] == 1 and inv["tokens"] > 1000 and usage.label("docread:invoice") == "Đọc tài liệu · Hoá đơn / chứng từ"


async def test_the_result_is_remembered_by_file_hash_and_configuration_so_it_is_never_paid_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    tid = await _company()
    qt = await _principal(tid)
    spy = Spy()
    monkeypatch.setattr(llm, "model_for", lambda p, model=None, *, media=False, spec=None: spy.model("phieu-scan.pdf"))
    first = await docread.read(qt, "phieu-scan.pdf", fx("phieu-scan.pdf"), purpose="kb", module="kb")
    again = await docread.read(qt, "ban-sao.pdf", fx("phieu-scan.pdf"), purpose="kb")           # cùng nội dung, tên khác
    assert len(spy.calls) == 1 and not first.cached and again.cached and again.usage.requests == 0 and again.text() == first.text()
    assert again.methods == {1: "model"} and "ĐẶT CỌC" in again.text()
    await docread.read(qt, "phieu-scan.pdf", fx("phieu-scan.pdf"), purpose="kb", refresh=True)    # người yêu cầu đọc lại ⇒ trả tiền lại
    assert len(spy.calls) == 2
    await _set(tid, "kb", model="google:gemini-3.8-pro")                                           # đổi model = cấu hình khác ⇒ đọc lại
    await docread.read(qt, "phieu-scan.pdf", fx("phieu-scan.pdf"), purpose="kb")
    assert len(spy.calls) == 3
    n = await owner_val("SELECT count(*) FROM core.docread_cache WHERE tenant_id = %s", (tid,))
    assert n == 2 and await owner_val("SELECT max(hits) FROM core.docread_cache WHERE tenant_id = %s", (tid,)) >= 1


async def test_kb_upload_reads_a_scan_through_the_company_profile_and_says_why_when_it_cannot(monkeypatch: pytest.MonkeyPatch) -> None:
    from barista.kernel.errors import Invalid

    tid = await _company()
    qt = await _principal(tid)
    await _set(tid, "kb", scanned_pdf=False)
    with pytest.raises(Invalid, match="tắt cho AI đọc trang scan"):
        await kb.read_upload(qt, "phieu-scan.pdf", fx("phieu-scan.pdf"), model=Spy().model("phieu-scan.pdf"))
    await _set(tid, "kb", scanned_pdf=True)
    text = await kb.read_upload(qt, "phieu-scan.pdf", fx("phieu-scan.pdf"), model=Spy().model("phieu-scan.pdf"))
    assert "ĐIỀU KHOẢN ĐẶT CỌC" in text and text.startswith("## Trang 1")


def test_a_pack_reader_registers_once_and_never_replaces_another() -> None:
    """Gói bộ đọc (api 1.85, ADR 0262): khai qua ``barista.api.docread.register`` ⇒ hồ sơ đọc chọn được bằng tên; tên trùng bộ đã có (của lõi hay gói khác) ⇒
    từ chối — thay im lặng là đổi cách đọc của mọi công ty. Tên phải dạng khoá."""
    from barista.api import docread as api_docread
    from barista.docread import readers

    class LocalOcr(api_docread.ExternalReader):
        name = "ocr_thu_nghiem"
        paid = False

    r = LocalOcr()
    api_docread.register(r)
    try:
        api_docread.register(r)                                     # cùng bộ ⇒ không lỗi (nạp lại gói)
        assert "ocr_thu_nghiem" in readers.names()
        with pytest.raises(ValueError, match="đã có"):
            api_docread.register(LocalOcr())                        # bộ khác cùng tên
        with pytest.raises(ValueError, match="đã có"):
            api_docread.register(type("Fake", (api_docread.ExternalReader,), {"name": "model"})())   # không đè bộ của lõi
        with pytest.raises(ValueError, match="dạng khoá"):
            api_docread.register(type("Bad", (api_docread.ExternalReader,), {"name": "OCR Mới"})())
    finally:
        readers._REG.pop("ocr_thu_nghiem", None)
