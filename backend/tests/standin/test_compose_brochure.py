"""SOẠN VỚI AI — tờ giới thiệu soạn TỪ DỮ LIỆU + bố cục + chia sẻ qua link (ADR 0224), bản LÕI chạy qua gói thế vai (``tests/standin/pack.py`` cắm
``compose.brochure``): công thức của gói chỉ mô tả khối, lõi chạy truy vấn bằng quyền người soạn; ảnh / trích dẫn chỉ từ kệ người soạn đọc được; khách người soạn
không được xem thì không soạn được; xuất HTML / Word / PowerPoint không vỡ; link chia sẻ đếm lượt xem, thu hồi được, không mở thêm đường nào khác."""

import base64
import io
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from barista.http_api import app
from barista.kernel import convert, executor
from tests.conftest import Who, fresh_phone, ok, q
from tests.standin import helpers

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
LEGAL = "Dự án đã được cấp phép xây dựng đầy đủ và đủ điều kiện mở bán theo quy định hiện hành của pháp luật."


@pytest_asyncio.fixture(autouse=True)
async def compose_on(who: Who) -> AsyncIterator[None]:
    admin = await who("admin")
    assert (await executor.run(admin, "admin.set_pack", {"pack": "compose", "enabled": True})).status == "ok"
    try:
        yield
    finally:
        await executor.run(admin, "admin.set_pack", {"pack": "compose", "enabled": False})


async def _doc(admin: Any, title: str, *, shelf: str = "common", kind: str = "other", context: str = "DEMO", image: bool = False, body: str = "Nội dung") -> str:
    d = await ok(admin, "kb.add_document", title=title, shelf=shelf, kind=kind, context_code=context, body=body)
    if image:
        await ok(admin, "kb.attach_original", document_id=str(d["document_id"]), filename=f"{title}.png", content_type="image/png", data_b64=base64.b64encode(PNG).decode())
    return str(d["document_id"])


async def _composer(p: Any, sid: Any) -> dict[str, Any]:
    return (await q(p, "compose.view", session_id=str(sid)))["blocks"][0]


async def _brochure(p: Any, kind: str = "demo_orders", **kw: Any) -> dict[str, Any]:
    return await ok(p, "compose.brochure", kind=kind, **kw)


async def test_a_pack_recipe_builds_a_layout_draft_from_queries_with_no_model(who: Who) -> None:
    await helpers.order(who, owner="sale1", amount=2_000_000_000)
    sale = await who("sale1")
    assert [o["value"] for o in await q(sale, "compose.brochure_options")] == ["demo_orders", "demo_lead"]            # loại do gói góp, người soạn chọn
    out = await _brochure(sale)
    comp = await _composer(sale, out["session_id"])
    assert comp["status"] == "ready" and comp["kind"] == "brochure" and comp["layout"]["template"] == "editorial" and comp["version"] == 1
    types = [(b["type"], b["origin"], b["slot"]) for b in comp["blocks"]]
    assert ("paragraph", "template", "cover") in types and ("data", "data", "") in types and ("paragraph", "template", "footer") in types
    data = next(b for b in comp["blocks"] if b["type"] == "data")
    assert data["source"]["query"] == "orderdesk.list" and data["page"]["rows"]                                       # ảnh chụp truy vấn, kèm nguồn
    assert comp["stats"]["flagged"] == 0 and comp["layout"]["sections"][0]["kind"] == "cover"
    assert {t["key"] for t in comp["layout"]["templates"]} == {"plain", "brochure", "proposal", "editorial", "flyer"} and "col1" in {s["key"] for s in comp["layout"]["slots"]}
    assert "Đã dựng bản 1 từ dữ liệu" in comp["turns"][-1]["text"]
    # bản của riêng người soạn
    other = await who("sale2")
    assert (await executor.query(other, "compose.view", {"session_id": str(out["session_id"])})).status == "not_found"
    # công thức chỉ nêu truy vấn THƯỜNG: không nhờ công thức chạy lệnh ghi / truy vấn nội bộ
    assert (await executor.run(sale, "compose.brochure", {"kind": "khong_co"})).status == "invalid"


async def test_images_and_excerpts_come_only_from_shelves_the_composer_reads(who: Who) -> None:
    admin, sale = await who("admin"), await who("sale1")
    ok_img = await _doc(admin, "Phối cảnh", image=True)                                                              # kệ chung: sale đọc được
    await _doc(admin, "Ảnh nội bộ ban giám đốc", shelf="management", image=True)                                      # kệ sale KHÔNG đọc được
    await _doc(admin, "Ảnh dự án khác", context="KHAC", image=True)                                                   # phạm vi khác
    await _doc(admin, "Tệp không phải ảnh", body="chữ")                                                               # không có tệp gốc
    legal = await _doc(admin, "Pháp lý dự án mẫu", kind="legal", body=LEGAL + "\nĐiều khoản khác của tài liệu pháp lý dài hơn.")
    await _doc(admin, "Pháp lý nội bộ", shelf="management", kind="legal", body="Nội dung pháp lý chỉ ban giám đốc được đọc trong kệ nội bộ của công ty.")
    comp = await _composer(sale, (await _brochure(sale))["session_id"])
    imgs = [b for b in comp["blocks"] if b["type"] == "image"]
    assert [b["document_id"] for b in imgs] == [ok_img] and imgs[0]["slot"] == "cover"                                 # đúng một ảnh, đứng ở bìa
    quotes = [b for b in comp["blocks"] if b["type"] == "quote"]
    assert [b["document_id"] for b in quotes] == [legal] and quotes[0]["text"] == LEGAL and "Pháp lý dự án mẫu" in quotes[0]["cite"]   # nguyên văn, có nguồn
    # quản trị (đọc được mọi kệ) thấy nhiều ảnh hơn ⇒ lọc là THEO QUYỀN người soạn, không phải chặn cứng
    comp_admin = await _composer(admin, (await _brochure(admin))["session_id"])
    assert len([b for b in comp_admin["blocks"] if b["type"] == "image"]) == 2


async def test_a_customer_the_composer_cannot_see_cannot_be_briefed(who: Who) -> None:
    sale1, sale2 = await who("sale1"), await who("sale2")
    lead = await ok(sale1, "crm.create_lead", name="Khách Riêng Tư", phone=fresh_phone())
    lid = str(lead["lead_id"])
    r = await executor.run(sale2, "compose.brochure", {"kind": "demo_lead", "lead_id": lid})                           # khách của sale1: RLS ⇒ sale2 không thấy
    assert r.status == "not_found"
    assert not (await q(sale2, "compose.list"))["rows"] or all(x["title"] != "Tờ dành cho Khách Riêng Tư" for x in (await q(sale2, "compose.list"))["rows"])
    mine = await ok(sale1, "compose.brochure", kind="demo_lead", lead_id=lid)
    comp = await _composer(sale1, mine["session_id"])
    assert comp["title"] == "Tờ dành cho Khách Riêng Tư" and any(b["text"] == "Dành riêng cho anh/chị Khách Riêng Tư" for b in comp["blocks"] if b["type"] == "paragraph")


async def test_exports_open_html_word_and_the_fallback_without_crashing(who: Who) -> None:
    admin, sale = await who("admin"), await who("sale1")
    await helpers.order(who, owner="sale1")
    await _doc(admin, "Phối cảnh xuất", image=True)
    out = await _brochure(sale)
    sid = str(out["session_id"])
    from docx import Document
    from pptx import Presentation

    got: dict[str, bytes] = {}
    for fmt in ("html", "docx", "pptx"):
        e = await ok(sale, "compose.export", session_id=sid, format=fmt)
        got[fmt] = base64.b64decode((await q(sale, "compose.download", export_id=e["export_id"]))["data_b64"])
    html = got["html"].decode()
    assert html.startswith("<!DOCTYPE html>") and 'aria-label="Bìa"' in html and "data:image/png;base64," in html and "<script" not in html
    assert "Nguồn:" not in html and "theo quyền xem" not in html and "Thông tin cập nhật lúc" in html
    doc = Document(io.BytesIO(got["docx"]))
    assert len(doc.tables) >= 2 and len(doc.inline_shapes) >= 1                                                       # bìa + ảnh nhúng theo bố cục
    assert len(Presentation(io.BytesIO(got["pptx"])).slides) >= 2                                                     # PowerPoint: xếp theo mục, chỗ đứng bị bỏ qua
    if convert.binary():                                                                                              # PDF cần LibreOffice (máy dev có thể chưa cài)
        e = await ok(sale, "compose.export", session_id=sid, format="pdf")
        assert base64.b64decode((await q(sale, "compose.download", export_id=e["export_id"]))["data_b64"])[:4] == b"%PDF"
    else:
        r = await executor.run(sale, "compose.export", {"session_id": sid, "format": "pdf"})
        assert r.status == "invalid"                                                                                  # báo rõ "chưa cài", không vỡ


async def test_layout_changes_are_versioned_and_slots_move_blocks(who: Who) -> None:
    await helpers.order(who, owner="sale1")
    sale = await who("sale1")
    sid = str((await _brochure(sale))["session_id"])
    comp = await _composer(sale, sid)
    para = next(b for b in comp["blocks"] if b["type"] == "heading")
    await ok(sale, "compose.set_slot", session_id=sid, block=para["id"], slot="col2")
    await ok(sale, "compose.set_layout", session_id=sid, template="proposal")
    comp = await _composer(sale, sid)
    assert comp["version"] == 3 and comp["layout"]["template"] == "proposal" and next(b for b in comp["blocks"] if b["id"] == para["id"])["slot"] == "col2"
    assert next(s for s in comp["layout"]["sections"] if s["kind"] == "cover")["style"] == "band"
    await ok(sale, "compose.restore", session_id=sid, version=1)                                                       # quay lại cả bố cục
    comp = await _composer(sale, sid)
    assert comp["layout"]["template"] == "editorial" and next(b for b in comp["blocks"] if b["id"] == para["id"])["slot"] == ""
    assert (await executor.run(sale, "compose.set_layout", {"session_id": sid, "template": "không có"})).status == "invalid"
    assert (await executor.run(sale, "compose.set_slot", {"session_id": sid, "block": para["id"], "slot": "xyz"})).status == "invalid"
    await ok(sale, "compose.set_layout", session_id=sid, template="")                                                  # bỏ trang mẫu ⇒ bố cục cũ
    assert (await _composer(sale, sid))["layout"] == {**(await _composer(sale, sid))["layout"], "template": "", "sections": []}


async def _get(path: str) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.get(path)


async def test_share_link_counts_views_and_is_revocable_and_opens_nothing_else(who: Who) -> None:
    await helpers.order(who, owner="sale1")
    sale, other = await who("sale1"), await who("sale2")
    sid = str((await _brochure(sale))["session_id"])
    # trợ lý không gửi gì ra ngoài; chỉ người soạn bấm
    assert (await executor.run(sale.as_agent(), "compose.share", {"session_id": sid})).status != "ok"
    sh = await ok(sale, "compose.share", session_id=sid, recipient="Chị Lan", days=7)
    token = sh["url"].rsplit("/", 1)[1]
    assert sh["url"].startswith("http") and "/v1/compose/shared/" in sh["url"] and len(token) > 30
    r = await _get(f"/v1/compose/shared/{token}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html") and "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-robots-tag"].startswith("noindex") and r.headers["cache-control"] == "no-store" and "Giới thiệu dịch vụ" in r.text
    assert (await _get(f"/v1/compose/shared/{token}")).status_code == 200
    shares = (await _composer(sale, sid))["shares"]
    assert len(shares) == 1 and shares[0]["views"] == 2 and shares[0]["recipient"] == "Chị Lan" and shares[0]["active"] and shares[0]["last_viewed_at"]
    # trang là ẢNH CHỤP: sửa bản soạn sau đó không đổi trang đã gửi
    comp = await _composer(sale, sid)
    await ok(sale, "compose.edit_block", session_id=sid, block=next(b for b in comp["blocks"] if b["type"] == "heading")["id"], text="Tiêu đề đã sửa sau khi gửi")
    assert "Tiêu đề đã sửa sau khi gửi" not in (await _get(f"/v1/compose/shared/{token}")).text
    # người cầm link chỉ mở được ĐÚNG trang đó: không đọc JSON chung, không tìm thấy bản soạn của người khác
    assert (await _get(f"/v1/public/links/{token}")).status_code != 200
    assert (await _get("/v1/compose/shared/khong-phai-link-that-0123456789abcdefghij")).status_code == 404
    # người khác không thu hồi được link của bạn; chủ thu hồi ⇒ khách mở thấy hết hiệu lực, số lượt vẫn giữ
    assert (await executor.run(other, "compose.unshare", {"share_id": sh["share_id"]})).status == "not_found"
    await ok(sale, "compose.unshare", share_id=str(sh["share_id"]))
    assert (await _get(f"/v1/compose/shared/{token}")).status_code == 404
    shares = (await _composer(sale, sid))["shares"]
    assert shares[0]["views"] == 3 and not shares[0]["active"]
    # xoá bản soạn ⇒ link đã gửi cũng hết hiệu lực
    sh2 = await ok(sale, "compose.share", session_id=sid, recipient="", days=7)
    token2 = sh2["url"].rsplit("/", 1)[1]
    assert (await _get(f"/v1/compose/shared/{token2}")).status_code == 200
    await ok(sale, "compose.delete", session_id=sid)
    assert (await _get(f"/v1/compose/shared/{token2}")).status_code == 404


async def test_sharing_is_refused_while_numbers_do_not_match_or_holes_remain(who: Who) -> None:
    await helpers.order(who, owner="sale1")
    sale = await who("sale1")
    sid = str((await _brochure(sale))["session_id"])
    comp = await _composer(sale, sid)
    head = next(b for b in comp["blocks"] if b["type"] == "heading")
    await ok(sale, "compose.edit_block", session_id=sid, block=head["id"], text="Còn [cần số liệu: giá bàn giao] chờ điền")
    r = await executor.run(sale, "compose.share", {"session_id": sid})
    assert r.status == "invalid" and "cần số liệu" in r.error                                                         # ra ngoài công ty: không còn chỗ trống
    await ok(sale, "compose.edit_block", session_id=sid, block=head["id"], text="Đơn gần đây")
    assert (await ok(sale, "compose.share", session_id=sid))["url"]


# ── Ảnh: kho → ảnh trong PDF → chỗ giữ ở màn soạn; ghim ảnh bìa (0224 §8) ──

def _pdf_with_images(n: int = 2) -> bytes:
    """PDF thật có n ảnh nhúng (gradient đủ lớn, đủ khác nhau để qua bộ lọc ảnh rác) — Pillow ghi mỗi ảnh một trang."""
    from PIL import Image

    pages = []
    for k in range(n):
        im = Image.new("RGB", (900, 600))
        px = im.load()
        assert px is not None
        for x in range(900):
            for y in range(0, 600, 3):
                px[x, y] = ((x * (k + 1)) % 256, (y * 2 + k * 60) % 256, (x + y) % 256)
        pages.append(im)
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:])
    return buf.getvalue()


async def _pdf_doc(admin: Any, title: str, *, shelf: str = "common", context: str = "DEMO", n: int = 2) -> str:
    d = await ok(admin, "kb.add_document", title=title, shelf=shelf, kind="other", context_code=context, body="Tờ giới thiệu của chủ đầu tư")
    await ok(admin, "kb.attach_original", document_id=str(d["document_id"]), filename=f"{title}.pdf", content_type="application/pdf",
             data_b64=base64.b64encode(_pdf_with_images(n)).decode())
    return str(d["document_id"])


async def _clean(admin: Any) -> None:
    """Xoá tài liệu phạm vi DEMO do test trước để lại (cùng một DB cho cả file) — để đếm ảnh chính xác."""
    for r in (await q(admin, "kb.documents"))["rows"]:
        if r["context_code"] == "DEMO":
            await ok(admin, "kb.delete_document", document_id=str(r["id"]))


def _images_of(comp: dict[str, Any]) -> list[dict[str, Any]]:
    return [b for b in comp["blocks"] if b["type"] == "image"]


async def test_image_order_is_library_first_then_images_inside_pdfs_and_pdf_images_are_reused(who: Who) -> None:
    admin, sale = await who("admin"), await who("sale1")
    await _clean(admin)
    lib = await _doc(admin, "1. Phối cảnh tổng thể", image=True)
    pdf = await _pdf_doc(admin, "Tờ giới thiệu chủ đầu tư", n=2)
    first = await _composer(sale, (await _brochure(sale))["session_id"])
    imgs = _images_of(first)
    assert [(b["slot"], b["derived"]) for b in imgs] == [("cover", False), ("grid", True), ("grid", True)]      # thư viện làm bìa, ảnh trong PDF lấp lưới
    assert imgs[0]["document_id"] == lib and imgs[0]["caption"] == "Phối cảnh tổng thể"                         # số đầu tên chỉ để sắp thứ tự, không in ra
    assert all(b["document_id"] == pdf and b["thumb"].startswith("data:image/jpeg;base64,") for b in imgs[1:])  # ảnh trích liền với tài liệu nguồn; màn soạn thấy bản thu nhỏ
    again = await _composer(sale, (await _brochure(sale))["session_id"])
    assert [b["id"] for b in _images_of(again)] == [b["id"] for b in imgs]                                       # lần dựng sau dùng lại ảnh đã trích (không trích lặp)
    e = await ok(sale, "compose.export", session_id=str(again["session_id"]), format="html")
    html = base64.b64decode((await q(sale, "compose.download", export_id=e["export_id"]))["data_b64"]).decode()
    assert html.count("data:image/jpeg;base64,") >= 2 and "data:image/png;base64," in html                       # ảnh trích vào trang như ảnh kho


async def test_images_inside_pdfs_follow_the_composers_rights_and_no_image_means_a_studio_only_placeholder(who: Who) -> None:
    admin, sale = await who("admin"), await who("sale1")
    await _clean(admin)
    await _pdf_doc(admin, "Tờ nội bộ ban giám đốc", shelf="management")                                          # kệ sale không đọc được
    boss = await _composer(admin, (await _brochure(admin))["session_id"])
    assert [b["derived"] for b in _images_of(boss)] == [True, True]                                              # quản trị đọc được ⇒ ảnh được trích (và cất đệm)
    mine = await _composer(sale, (await _brochure(sale))["session_id"])                                          # cùng ảnh đã cất đệm nhưng sale không đọc được tài liệu nguồn
    assert _images_of(mine) == []
    ph = [b for b in mine["blocks"] if b["type"] == "placeholder"]
    assert len(ph) == 1 and ph[0]["slot"] == "cover" and ph[0]["text"] == "Cần ảnh thử" and "DEMO" in ph[0]["caption"]   # chỗ giữ có ở màn soạn
    sid = str(mine["session_id"])
    from docx import Document
    from pptx import Presentation

    for fmt in ("html", "docx", "pptx"):
        e = await ok(sale, "compose.export", session_id=sid, format=fmt)
        raw = base64.b64decode((await q(sale, "compose.download", export_id=e["export_id"]))["data_b64"])
        if fmt == "html":
            assert "Cần ảnh" not in raw.decode()                         # KHÔNG bao giờ ra tệp / trang gửi khách
        elif fmt == "docx":
            assert "Cần ảnh" not in "\n".join(p.text for p in Document(io.BytesIO(raw)).paragraphs) + "\n".join(
                c.text for t in Document(io.BytesIO(raw)).tables for r in t.rows for c in r.cells)
        else:
            assert all("Cần ảnh" not in sh.text_frame.text for sl in Presentation(io.BytesIO(raw)).slides for sh in sl.shapes if sh.has_text_frame)   # pyright: ignore[reportAttributeAccessIssue]
    r = await executor.run(sale, "compose.share", {"session_id": sid, "recipient": "x"})                          # chia sẻ không bị chặn vì chỗ giữ, và trang không mang chữ đó
    assert r.status == "ok"


async def test_the_composer_can_pin_a_cover_image_and_the_old_cover_takes_its_place_in_the_grid(who: Who) -> None:
    admin, sale = await who("admin"), await who("sale1")
    await _clean(admin)
    await _doc(admin, "1. Ảnh một", image=True)
    await _doc(admin, "2. Ảnh hai", image=True)
    await _doc(admin, "3. Ảnh ba", image=True)
    sid = str((await _brochure(sale))["session_id"])
    comp = await _composer(sale, sid)
    caps = [(b["slot"], b["caption"]) for b in _images_of(comp)]
    assert caps == [("cover", "Ảnh một"), ("grid", "Ảnh hai"), ("grid", "Ảnh ba")]
    third = _images_of(comp)[2]["id"]
    await ok(sale, "compose.set_cover", session_id=sid, block=third)
    comp = await _composer(sale, sid)
    assert [(b["slot"], b["caption"]) for b in _images_of(comp)] == [("cover", "Ảnh ba"), ("grid", "Ảnh hai"), ("grid", "Ảnh một")]
    assert comp["layout"]["sections"][0]["hero"] == third and comp["stats"]["flagged"] == 0
    assert (await executor.run(sale, "compose.set_cover", {"session_id": sid, "block": third})).status == "invalid"   # đã là bìa
    heading = next(b for b in comp["blocks"] if b["type"] == "heading")["id"]
    assert (await executor.run(sale, "compose.set_cover", {"session_id": sid, "block": heading})).status == "not_found"   # không phải ảnh
