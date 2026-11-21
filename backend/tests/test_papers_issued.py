"""Giấy tờ giữ TỆP BAN HÀNH cho mọi văn bản (ADR 0249 lát 4): công văn tự do cũng cất .docx lúc cấp số; tải về sau khi đổi thông tin công ty vẫn là
đúng tệp đã phát hành, còn bản dự thảo dựng theo dữ liệu hiện tại."""

import base64
import io
import uuid
import zipfile
from typing import Any

from barista.kernel.principal import Principal
from tests.conftest import Who, ok, owner_val, q


def _text(docx_b64: str) -> str:
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(docx_b64))) as z:
        return z.read("word/document.xml").decode()


async def _settings(admin: Principal, **changes: str) -> dict[str, Any]:
    keep = await owner_val("SELECT row_to_json(s) FROM papers.settings s WHERE tenant_id = %s", (admin.tenant_id,)) or {}
    base = {k: keep.get(k) or "" for k in ("company_name", "short_name", "address", "tax_code", "phone", "bank_account", "bank_name", "city",
                                           "signer_title", "signer_name")}
    base |= {"company_name": base["company_name"] or "CÔNG TY MẪU", "short_name": base["short_name"] or "CTM", "city": base["city"] or "Hà Nội",
             "signer_title": base["signer_title"] or "GIÁM ĐỐC"}
    await ok(admin, "papers.set_settings", **(base | changes))
    return base


async def test_an_issued_free_form_letter_downloads_the_frozen_file_after_company_settings_change(who: Who) -> None:
    admin, gd = await who("admin"), await who("giamdoc")
    tag = uuid.uuid4().hex[:6].upper()
    before = await _settings(admin, signer_name=f"NGƯỜI KÝ CŨ {tag}")
    try:
        paper = str((await ok(gd, "papers.draft_letter", recipient="Công ty Mẫu Một", subject=f"đề nghị {tag}", body="Kính đề nghị…"))["paper_id"])
        draft = await ok(gd, "papers.draft_letter", recipient="Công ty Mẫu Hai", subject=f"dự thảo {tag}", body="Nội dung.")
        await ok(gd, "papers.issue", paper_id=paper)
        kept = await owner_val("SELECT file_id FROM papers.files WHERE document_id = %s AND role = 'issued'", (uuid.UUID(paper),))
        assert kept is not None                                                              # cất tệp lúc cấp số, cả công văn tự do
        first = await q(gd, "papers.export", paper_id=paper)
        await _settings(admin, signer_name=f"NGƯỜI KÝ MỚI {tag}")
        again = await q(gd, "papers.export", paper_id=paper)
        assert again["docx_b64"] == first["docx_b64"]                                        # đúng tệp đã phát hành, byte không đổi
        assert f"NGƯỜI KÝ CŨ {tag}" in _text(again["docx_b64"]) and f"NGƯỜI KÝ MỚI {tag}" not in _text(again["docx_b64"])
        assert f"NGƯỜI KÝ MỚI {tag}" in _text((await q(gd, "papers.export", paper_id=str(draft["paper_id"])))["docx_b64"])   # dự thảo: dữ liệu hiện tại
    finally:
        await ok(admin, "papers.set_settings", **before)
