"""Kho tài liệu (RAG theo quyền) + giấy tờ (soạn nháp → ban hành, .docx đúng thể thức)."""

import io
from datetime import timedelta

from docx import Document
from httpx import ASGITransport, AsyncClient

from barista.http_api import app
from barista.kernel import executor
from barista.kernel.clock import today
from barista.kernel.text import vnd_words
from tests.conftest import Who, ok, q


def _titles(page) -> set[str]:
    return {r["title"] for r in page["rows"]}


async def test_search_respects_shelves(who: Who) -> None:
    sale, acc, gd = await who("sale1"), await who("ketoan"), await who("giamdoc")
    assert not any("đối chiếu" in t for t in _titles(await q(sale, "kb.search", q="đối chiếu công nợ chủ đầu tư")))
    assert any("đối chiếu" in t for t in _titles(await q(acc, "kb.search", q="đối chiếu công nợ chủ đầu tư")))
    assert not any("quý I/2027" in t for t in _titles(await q(acc, "kb.search", q="định hướng kinh doanh ngân sách")))
    assert any("quý I/2027" in t for t in _titles(await q(gd, "kb.search", q="định hướng kinh doanh ngân sách")))
    # danh sách tài liệu cũng lọc theo kệ
    assert len((await q(sale, "kb.documents"))["rows"]) < len((await q(gd, "kb.documents"))["rows"])


async def test_who_can_add_to_which_shelf(who: Who) -> None:
    sale, acc = await who("sale1"), await who("ketoan")
    r = await executor.run(sale, "kb.add_document", {"title": "X", "body": "nội dung"})
    assert r.status == "denied"
    await ok(acc, "kb.add_document", title="Hướng dẫn hạch toán phí tạm ứng", shelf="finance", kind="process",
             body="# Hạch toán\nPhí tạm ứng ghi doanh thu tài chính.")
    r = await executor.run(acc, "kb.add_document", {"title": "Y", "shelf": "management", "body": "bí mật"})
    assert r.status == "denied"


async def test_expired_documents_are_not_cited(who: Who) -> None:
    gd = await who("giamdoc")
    await ok(gd, "kb.add_document", title="Chính sách chiết khấu cũ (hết hạn)", kind="policy",
             effective_to=(today() - timedelta(days=1)).isoformat(), body="Chiết khấu đặc biệt mã KMCU99 giảm 9%.")
    assert (await q(gd, "kb.search", q="KMCU99"))["rows"] == []


def test_vnd_words() -> None:
    assert vnd_words(168_000_000) == "Một trăm sáu mươi tám triệu đồng"
    assert vnd_words(1_222_500_000) == "Một tỷ hai trăm hai mươi hai triệu năm trăm nghìn đồng"
    assert vnd_words(1_000_005) == "Một triệu không trăm lẻ năm đồng"
    assert vnd_words(21_000) == "Hai mươi mốt nghìn đồng"
    assert vnd_words(15_024) == "Mười lăm nghìn không trăm hai mươi tư đồng"


async def test_upload_docx_to_kb_via_api(who: Who) -> None:
    acc = await who("ketoan")
    d = Document()
    d.add_heading("Thời hạn thanh toán của chủ đầu tư", level=1)
    d.add_paragraph("Chủ đầu tư Mẫu Một thanh toán phí môi giới trong 30 ngày kể từ khi nhận đề nghị XKQ77.")
    buf = io.BytesIO()
    d.save(buf)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/v1/kb/upload", params={"title": "Điều khoản thanh toán CĐT Mẫu Một", "shelf": "finance", "kind": "process"},
                         content=buf.getvalue(), headers={"X-Dev-User": acc.email, "X-Tenant-Id": str(acc.tenant_id),
                                                          "X-Filename": "dieu-khoan.docx"})
        assert r.status_code == 200, r.text
    page = await q(acc, "kb.search", q="XKQ77")
    assert page["rows"] and page["rows"][0]["heading"] == "Thời hạn thanh toán của chủ đầu tư"


async def test_document_scope_filters_without_industry_and_never_grants_access(who: Who) -> None:
    import uuid

    gd, sale = await who("giamdoc"), await who("sale1")
    word = "SCOPE" + uuid.uuid4().hex[:8].upper()
    for scope in ("SHOP-A", "SHOP-B", ""):
        await ok(gd, "kb.add_document", title=f"Tài liệu {word} {scope}", body=f"Nội dung {word}", context_code=scope)
    await ok(gd, "kb.add_document", title=f"Riêng {word}", body=f"Bí mật {word}", shelf="management", context_code=word)
    rows = (await q(sale, "kb.search", q=word, context_code="SHOP-A"))["rows"]
    assert len(rows) == 2 and {r["context_code"] for r in rows} == {"", "SHOP-A"}
    choices = {r["value"] for r in await q(sale, "kb.context_options")}
    assert {"SHOP-A", "SHOP-B"} <= choices and word not in choices
