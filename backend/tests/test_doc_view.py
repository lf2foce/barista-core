"""Xem tài liệu (0102): người XEM bản gốc (PDF; Word / Excel đổi sang PDF ở máy chủ), chữ bóc / OCR là bản phụ; giấy tờ tự soạn vẽ đúng cấu trúc
(bảng thật) và xem trước như bản in = PDF đổi từ CHÍNH file .docx; máy chưa cài LibreOffice ⇒ báo rõ, app cho tải file."""

import asyncio
import base64
import sys
from pathlib import Path

import httpx
import pytest

from barista.http_api import app
from barista.kernel import convert
from tests.conftest import Who, ok, q

FAKE_PDF = b"%PDF-1.4\n% fake\n"


@pytest.fixture
def fake_soffice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """LibreOffice giả: ghi in.pdf vào --outdir và đếm số lần chạy (kiểm nhớ kết quả)."""
    calls = tmp_path / "calls"
    exe = tmp_path / "soffice"
    exe.write_text(f"""#!{sys.executable}
import sys, pathlib
args = sys.argv[1:]
out = pathlib.Path(args[args.index("--outdir") + 1])
(out / "in.pdf").write_bytes({FAKE_PDF!r})
with open({str(calls)!r}, "a") as f:
    f.write("x")
""")
    exe.chmod(0o755)
    monkeypatch.setattr(convert, "binary", lambda: str(exe))
    convert._cache.clear()
    return calls


async def test_pdf_passes_through_and_office_files_convert_once(fake_soffice: Path) -> None:
    assert await convert.to_pdf("a.pdf", FAKE_PDF) == FAKE_PDF
    data = b"PK\x03\x04 not really a docx"
    assert await convert.to_pdf("hop-dong.docx", data) == FAKE_PDF
    assert await convert.to_pdf("hop-dong.docx", data) == FAKE_PDF
    assert await asyncio.to_thread(fake_soffice.read_text) == "x"                # lần hai lấy từ bộ nhớ, không đổi lại
    with pytest.raises(convert.Unavailable):
        await convert.to_pdf("anh.heic", b"...")


async def test_without_libreoffice_the_reason_is_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(convert, "binary", lambda: None)
    convert._cache.clear()
    with pytest.raises(convert.Unavailable, match="LibreOffice"):
        await convert.to_pdf("bang-tinh.xlsx", b"PK...")


async def test_kb_document_shows_the_original_first_and_text_as_extracted(who: Who, fake_soffice: Path) -> None:
    admin = await who("admin")
    doc = (await ok(admin, "kb.add_document", title="Hợp đồng mẫu xem trước", kind="other", body="Điều 1. Nội dung bóc chữ."))["document_id"]
    await ok(admin, "kb.attach_original", document_id=doc, filename="hop-dong-mau.docx",
             content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             data_b64=base64.b64encode(b"PK\x03\x04 docx").decode())
    page = await q(admin, "kb.document", document_id=doc)
    assert page["original"] == {"title": "Bản gốc", "path": f"/v1/kb/documents/{doc}/preview", "page": None, "kind": "pdf",
                                "filename": "hop-dong-mau.pdf"}
    assert page["text_role"] == "extracted"                                     # chữ bóc là bản phụ (tìm / trích cho trợ lý)
    h = {"X-Dev-User": admin.email, "X-Tenant-Id": str(admin.tenant_id)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
        r = await c.get(f"/v1/kb/documents/{doc}/preview", headers=h)
        assert r.status_code == 200 and r.content == FAKE_PDF
