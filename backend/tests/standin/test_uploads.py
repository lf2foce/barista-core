"""NÚT TẢI TỆP CHUNG (ADR 0262): lệnh khai ``upload=`` nhận MỘT tệp qua ``POST /v1/uploads/<lệnh>`` — thân = bytes, ``X-Filename``; lõi kiểm đuôi + cỡ rồi chạy
lệnh qua executor với nội dung ở ``ctx.files["file"]`` (đầu vào chỉ tên / cỡ / sha256 + ô thường từ query). Không còn route tệp riêng từng module."""

from typing import Any

import pytest
from pydantic import BaseModel

from barista.kernel import registry
from barista.kernel.screens import Action
from barista.workspaces import _action
from tests.conftest import Who
from tests.test_entry import _client, _h


async def test_a_file_goes_through_the_one_door_with_its_bytes_beside_the_input(who: Who) -> None:
    sale = await who("sale1")
    async with _client() as c:
        r = await c.post("/v1/uploads/orderdesk.attach_scan?code=DH-1", content=b"%PDF-1.4 scan", headers={**_h(sale), "x-filename": "b%E1%BA%A3n-scan.pdf"})
        assert r.status_code == 200, r.text
        out = r.json()["output"]
        assert out == {"filename": "bản-scan.pdf", "code": "DH-1", "bytes": 13, "same": True, "read_outside": 13, "summary": "Đã nhận bản-scan.pdf"}
        bad = await c.post("/v1/uploads/orderdesk.attach_scan", content=b"MZ", headers={**_h(sale), "x-filename": "virus.exe"})
        assert bad.status_code == 415 and ".pdf" in bad.json()["detail"]
        assert (await c.post("/v1/uploads/orderdesk.attach_scan", content=b"", headers={**_h(sale), "x-filename": "a.pdf"})).status_code == 422
        assert (await c.post("/v1/uploads/orderdesk.create", content=b"x", headers={**_h(sale), "x-filename": "a.pdf"})).status_code == 404   # lệnh không nhận tệp


async def test_the_button_carries_the_accepted_types_only_for_people_who_can_run_it(who: Who) -> None:
    out: Any = _action(await who("sale1"), Action("orderdesk.attach_scan", "Tải bản scan"))
    assert out["upload"] == "pdf,jpg" and out["title"] == "Tải bản scan"
    assert "upload" not in (_action(await who("sale1"), Action("orderdesk.create")) or {})


def test_an_upload_command_is_declared_on_purpose() -> None:
    class NoFile(BaseModel):
        code: str = ""

    class WithFile(BaseModel):
        filename: str
        size: int
        sha256: str

    # sổ đăng ký được fixture ``standin`` chụp / trả quanh file này ⇒ module thử không lọt sang file khác
    m = registry.Module("uptest", "Thử tải tệp", pack="orderdesk")
    with pytest.raises(ValueError, match="trợ lý không tải tệp"):
        m.command("a", resource="orderdesk", title="A", upload="pdf")             # agent mặc định "propose" ⇒ từ chối
    with pytest.raises(ValueError, match="đuôi tệp"):
        m.command("b", resource="orderdesk", title="B", agent="deny", upload="PDF, xml")
    with pytest.raises(ValueError, match="filename / size / sha256"):
        async def c(ctx: Any, inp: NoFile) -> None: ...
        m.command("c", resource="orderdesk", title="C", agent="deny", upload="pdf")(c)

    async def d(ctx: Any, inp: WithFile) -> None: ...
    m.command("d", resource="orderdesk", title="D", agent="deny", upload="pdf,xml")(d)
    assert registry.OPS["uptest.d"].upload == "pdf,xml"
