"""Kệ tài liệu là dữ liệu (quyết định 0051): ai đọc = cả công ty · đội (gồm đội con) · vai · người. Mọi khẳng định đi qua kb.search như
người / trợ lý thật — trợ lý tìm bằng quyền người hỏi."""

import io
import uuid
from typing import Any

from pydantic_ai import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pypdf import PdfWriter

from barista import docread
from barista.kernel import executor
from barista.modules import kb
from tests.conftest import Who, ok, owner_val, q


async def _finds(p: Any, word: str) -> bool:
    return bool((await q(p, "kb.search", q=word))["rows"])


async def _shelf(gd: Any, word: str, **audience: Any) -> str:
    out = await ok(gd, "kb.save_shelf", name=f"Kệ thử {word}", **audience)
    await ok(gd, "kb.add_document", title=f"Tài liệu {word}", shelf=out["key"], kind="process", body=f"# Mục\nNội dung riêng {word}.")
    return out["key"]


async def test_team_shelf_is_read_by_that_team_and_its_subteams_only(who: Who) -> None:
    gd, sale1, sale3, mkt, digital = (await who("giamdoc"), await who("sale1"), await who("sale3"), await who("mkt"), await who("digital"))
    word = f"kda{uuid.uuid4().hex[:6]}"
    await _shelf(gd, word, teams=["KD-A"], roles=["director"])
    assert await _finds(sale1, word) and await _finds(gd, word)
    assert not await _finds(sale3, word)                                                      # đội B không đọc được
    assert not await _finds(sale3.as_agent(), word)                                           # trợ lý của người đó cũng không
    mword = f"mkt{uuid.uuid4().hex[:6]}"
    await _shelf(gd, mword, teams=["MKT"], roles=["director"])
    assert await _finds(mkt, mword) and await _finds(digital, mword)                           # Digital là đội con của Marketing
    assert not await _finds(sale1, mword)


async def test_people_and_role_shelves_and_changes_apply_at_once(who: Who) -> None:
    gd, sale1, sale2, ketoan = await who("giamdoc"), await who("sale1"), await who("sale2"), await who("ketoan")
    word = f"ppl{uuid.uuid4().hex[:6]}"
    key = await _shelf(gd, word, people=[sale2.email], roles=["director"])
    assert await _finds(sale2, word) and not await _finds(sale1, word)
    await ok(gd, "kb.save_shelf", key=key, name=f"Kệ thử {word}", roles=["director", "accountant"])
    assert not await _finds(sale2, word) and await _finds(ketoan, word)                       # bỏ người / thêm vai: có hiệu lực ngay


async def test_who_may_manage_shelves(who: Who) -> None:
    gd, sale1 = await who("giamdoc"), await who("sale1")
    assert (await executor.run(sale1, "kb.save_shelf", {"name": "Kệ của tôi", "everyone": True})).status == "denied"
    assert (await executor.run(gd, "kb.save_shelf", {"name": "Kệ rỗng"})).status == "invalid"        # phải chọn ai đọc
    assert (await executor.run(gd, "kb.save_shelf", {"key": "common", "name": "Chung", "roles": ["director"]})).status == "invalid"
    word = f"sec{uuid.uuid4().hex[:6]}"
    key = await _shelf(gd, word, roles=["director"])
    r = await executor.run(sale1, "kb.add_document", {"title": "X", "shelf": key, "body": "y"})
    assert r.status == "denied"                                                               # không thả tài liệu vào kệ mình không đọc được
    rows = {r_["key"]: r_ for r_ in (await q(gd, "kb.shelves"))["rows"]}
    assert rows[key]["audience"] == "Vai: Giám đốc" and rows[key]["state"] == "mine"
    assert key not in {r_["key"] for r_ in (await q(sale1, "kb.shelves"))["rows"]}           # người thường chỉ thấy kệ mình đọc
    assert await owner_val("SELECT count(*) FROM core.grants WHERE resource IN ('kb.finance', 'kb.management')") == 0


def test_scanned_pdf_detection() -> None:
    blank = io.BytesIO()
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    w.add_blank_page(width=595, height=842)
    w.write(blank)
    assert docread.read_free("scan.pdf", blank.getvalue()).unread() == [1, 2]    # không có chữ ⇒ cả hai trang cần model đọc
    assert kb.extract_text("scan.pdf", blank.getvalue()).startswith("## Trang 1")


async def test_a_scanned_pdf_is_read_by_the_model_and_metered(who: Who) -> None:
    ketoan = await who("ketoan")
    buf = io.BytesIO()
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    w.write(buf)
    seen: list[Any] = []

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(getattr(messages[-1], "parts", []))
        return ModelResponse(parts=[TextPart("## Trang 1\n# Điều 1. Đặt cọc\nTiền cọc tối đa 5% giá bán, chuyển khoản vào tài khoản CĐT.")])

    text = await kb.read_upload(ketoan, "phieu-scan.pdf", buf.getvalue(), model=FunctionModel(reply, model_name="ocr-fake"))
    assert "Đặt cọc" in text and "## Trang 1" in text
    assert any("application/pdf" in repr(p_) for p_ in seen)                                   # model nhận đúng file PDF
    assert await owner_val("""SELECT surface || '/' || category FROM core.ai_usage a JOIN core.users u ON u.id = a.user_id
                              WHERE u.email = %s ORDER BY a.id DESC LIMIT 1""", (ketoan.email,)) == "docread/docread:kb"


async def test_unreadable_uploads_are_logged_not_only_reported(who: Who, caplog: Any) -> None:
    """Hai chỗ bắt rộng ở đường tải lên GIỮ (SDK model / trình đọc file ném đủ loại) nhưng phải để lại dấu trong log — lỗi lập trình không chỉ nằm trong câu báo."""
    import logging

    import pytest
    from httpx import ASGITransport, AsyncClient

    from barista.http_api import app
    from barista.kernel.errors import Invalid

    ketoan = await who("ketoan")
    buf = io.BytesIO()
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    w.write(buf)

    def broken(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("model sập")

    with caplog.at_level(logging.WARNING, logger="barista.docread"), pytest.raises(Invalid, match="Không đọc được PDF scan: model sập"):
        await kb.read_upload(ketoan, "scan.pdf", buf.getvalue(), model=FunctionModel(broken, model_name="ocr-broken"))
    assert any("đọc PDF bằng model lỗi" in r.getMessage() and r.exc_info for r in caplog.records)

    caplog.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with caplog.at_level(logging.WARNING, logger="barista.kb"):
            r = await c.post("/v1/kb/upload", params={"title": "Rác"}, content=b"khong phai pdf",
                             headers={"X-Dev-User": ketoan.email, "X-Tenant-Id": str(ketoan.tenant_id), "X-Filename": "rac.pdf"})
    assert r.status_code == 422 and "Không đọc được file" in r.text
    assert any("đọc file tải lên 'rac.pdf' lỗi" in rec.getMessage() for rec in caplog.records)
