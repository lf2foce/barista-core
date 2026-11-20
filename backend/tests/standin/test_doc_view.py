"""XEM TÀI LIỆU (0102) với giấy tờ một gói ngành tự soạn, qua gói thế vai: bản LÕI (ADR 0190) của ``barista-packs:commission/tests/test_doc_view.py`` — giấy gói
soạn qua họ ``paper_draft`` (``papers.save_draft``) vẽ đúng cấu trúc (bảng thật, không ghép chữ) và xem trước như bản in = PDF đổi từ CHÍNH file .docx."""

from pathlib import Path

import httpx
import pytest

from barista.http_api import app
from barista.kernel import convert
from tests.conftest import Who, ok, q
from tests.standin import helpers
from tests.test_doc_view import FAKE_PDF, fake_soffice  # noqa: F401


async def _paper_with_table(who: Who) -> str:
    """Đề nghị thanh toán phí của một đơn vừa ký — giấy tờ có BẢNG (đơn · giá trị · phí)."""
    o = await helpers.signed(who)
    return str((await ok(await who("ketoan"), "orderdesk.draft_fee_request", code=o["inv"]))["paper_id"])


async def test_a_generated_paper_shows_a_real_table_and_a_print_preview(who: Who) -> None:
    ketoan = await who("ketoan")
    page = await q(ketoan, "papers.view", paper_id=await _paper_with_table(who))
    tables = [s["table"] for s in page["sections"] if s.get("table")]
    assert tables and tables[0]["columns"] and tables[0]["rows"]                 # bảng thật, không ghép chữ có dấu |
    assert not any("|" in (s.get("text") or "") for s in page["sections"])
    assert page["original"]["kind"] == "pdf" and page["original"]["path"].endswith("/pdf")
    assert {link["path"].rsplit("/", 1)[-1] for link in page["links"]} == {"docx", "pdf"}


async def test_paper_pdf_route_converts_the_same_docx(who: Who, fake_soffice: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811 — fixture nhập từ file test của lõi
    ketoan = await who("ketoan")
    pid = await _paper_with_table(who)
    h = {"X-Dev-User": ketoan.email, "X-Tenant-Id": str(ketoan.tenant_id)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
        r = await c.get(f"/v1/papers/{pid}/pdf", headers=h)
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content == FAKE_PDF
        monkeypatch.setattr(convert, "binary", lambda: None)
        convert._cache.clear()
        r = await c.get(f"/v1/papers/{pid}/pdf", headers=h)
        assert r.status_code == 409 and "LibreOffice" in r.json()["detail"]      # dev chưa cài ⇒ lý do rõ, app cho tải .docx
