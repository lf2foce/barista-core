"""Kho tài liệu bản 2 (quyết định 0052): chỉ bản ĐANG hiệu lực được tìm / trích, bản mới thay bản cũ có dấu vết, câu trích kiểm nguyên
văn bằng code, đoạn nhớ trang, file gốc đọc theo quyền kệ."""

import base64
import json
import uuid
from datetime import timedelta
from typing import Any

from barista.kernel import executor
from barista.kernel.clock import today
from barista.modules import kb
from tests.conftest import Who, ok, owner_val, q


async def _doc(p: Any, title: str, body: str, **kw: Any) -> str:
    return str((await ok(p, "kb.add_document", title=title, kind="policy", body=body, **kw))["document_id"])


async def _hits(p: Any, word: str, mark: str = "", **kw: Any) -> list[dict[str, Any]]:
    """Đoạn tìm được thuộc tài liệu của test này (chứa mã ngẫu nhiên `mark`) — kho dùng chung với test khác."""
    rows = (await q(p, "kb.search", q=word, **kw))["rows"]
    return [r for r in rows if mark in r["text"]]


async def test_new_version_replaces_old_and_only_current_is_searched(who: Who) -> None:
    gd = await who("giamdoc")
    w = f"ph{uuid.uuid4().hex[:6]}"
    old = await _doc(gd, f"Chính sách phí {w} T10/2026", f"# Điều 1. Phí\nPhí môi giới {w} là 2% giá bán.",
                     effective_from=str(today() - timedelta(days=40)))
    # bản mới tên gần giống ⇒ thêm thường thì CẢNH BÁO (kẻo hai bản cùng được trích)
    out = await ok(gd, "kb.add_document", title=f"Chính sách phí {w} T11/2026", kind="policy", body=f"Phí môi giới {w} là 2,5%.")
    assert out["similar"] and "Thay bằng bản mới" in out["warning"]
    await ok(gd, "kb.delete_document", document_id=str(out["document_id"]))
    new = await _doc(gd, f"Chính sách phí {w} T11/2026", f"# Điều 1. Phí\nPhí môi giới {w} là 2,5% giá bán.",
                     effective_from=str(today()), replaces_document_id=old)
    rows = await _hits(gd, f"phí môi giới {w}", w)
    assert [r["document_id"] for r in rows] == [new] and rows[0]["state"] == "active"
    both = await _hits(gd, f"phí môi giới {w}", w, include_history=True)
    assert {r["state"] for r in both} == {"active", "superseded"}
    view = await q(gd, "kb.document", document_id=old)
    assert "Đã bị thay thế" in view["subtitle"] and "T11/2026" in view["subtitle"]
    listed = {str(r["id"]): r["state"] for r in (await q(gd, "kb.documents", state="superseded"))["rows"]}
    assert listed.get(old) == "superseded" and new not in listed


async def test_future_version_waits_and_expired_drops_out(who: Who) -> None:
    gd = await who("giamdoc")
    w = f"fu{uuid.uuid4().hex[:6]}"
    old = await _doc(gd, f"Quy trình cọc {w}", f"Cọc {w} trong 3 ngày.")
    await _doc(gd, f"Quy trình cọc {w} bản 2", f"Cọc {w} trong 5 ngày.", effective_from=str(today() + timedelta(days=10)),
               replaces_document_id=old)
    rows = await _hits(gd, f"cọc {w}", w)
    assert [r["document_id"] for r in rows] == [old]          # bản mới chưa tới ngày ⇒ bản cũ vẫn dùng
    gone = await _doc(gd, f"Thông báo {w}", f"Ưu đãi {w} tháng trước.", effective_to=str(today() - timedelta(days=1)))
    assert not await _hits(gd, f"ưu đãi {w}", w)
    assert (await _hits(gd, f"ưu đãi {w}", w, include_history=True))[0]["state"] == "expired"
    await ok(gd, "kb.set_validity", document_id=gone, effective_to=None)
    assert await _hits(gd, f"ưu đãi {w}", w)


async def test_quote_is_checked_against_the_text(who: Who) -> None:
    gd = await who("giamdoc")
    w = f"qt{uuid.uuid4().hex[:6]}"
    doc = await _doc(gd, f"Hợp đồng mẫu {w}", f"## Trang 3\n# Điều 7. Phạt\nBên mua chậm thanh toán {w} chịu phạt **0,05%** mỗi ngày.")
    hit = await q(gd, "kb.quote", document_id=doc, text=f"chậm thanh toán {w} chịu phạt 0,05% mỗi ngày")
    assert hit["verified"] and hit["page"] == 3 and "Điều 7" in hit["cite"] and "trang 3" in hit["cite"]
    r = await executor.query(gd, "kb.quote", {"document_id": doc, "text": f"chậm thanh toán {w} chịu phạt 0,5% mỗi ngày"})
    assert r.status == "not_found"                                  # model nhớ sai một chữ số ⇒ bị chặn


def test_chunks_remember_pages() -> None:
    parts = kb.chunks("## Trang 1\n# Điều 1. Chung\nNội dung một.\n## Trang 2\nNội dung hai.\n# Điều 2. Phí\nPhí ba.")
    assert [(h, p) for h, _, p in parts] == [("Điều 1. Chung", 1), ("Điều 1. Chung", 2), ("Điều 2. Phí", 2)]


async def test_search_ranks_the_exact_phrase_and_reads_by_page(who: Who) -> None:
    gd = await who("giamdoc")
    w = f"rk{uuid.uuid4().hex[:6]}"
    noisy = await _doc(gd, f"Ghi chú {w}", f"Giới thiệu {w}: phí gửi xe, môi trường xanh, quy định chung về phí.")
    exact = await _doc(gd, f"Biểu phí {w}", f"## Trang 4\nPhí môi giới {w} tính trên giá trước VAT.\n## Trang 5\nThanh toán trong 7 ngày.")
    rows = await _hits(gd, f"phí môi giới {w}", w, limit=30)
    assert rows[0]["document_id"] == exact and rows[0]["page"] == 4 and "trang 4" in rows[0]["source"]
    assert [r["document_id"] for r in rows[1:]] == [noisy]           # khớp lẻ tẻ "phí" / "giới" vẫn có nhưng xếp sau
    part = await q(gd, "kb.read", document_id=exact, page_from=5)
    assert "7 ngày" in part["text"] and "VAT" not in part["text"]


async def test_original_file_follows_shelf_permission(who: Who) -> None:
    gd, sale3 = await who("giamdoc"), await who("sale3")
    w = f"fl{uuid.uuid4().hex[:6]}"
    shelf = (await ok(gd, "kb.save_shelf", name=f"Kệ {w}", roles=["director"]))["key"]
    doc = await _doc(gd, f"Pháp lý {w}", f"Sổ hồng {w}.", shelf=shelf)
    await ok(gd, "kb.attach_original", document_id=doc, filename="phap-ly.pdf", content_type="application/pdf",
             data_b64=base64.b64encode(b"%PDF-1.4 fake").decode())
    got = await q(gd, "kb.original", document_id=doc)
    assert base64.b64decode(got["data_b64"]).startswith(b"%PDF")
    assert (await executor.query(sale3, "kb.original", {"document_id": doc})).status == "not_found"
    assert (await executor.query(gd.as_agent(), "kb.original", {"document_id": doc})).status == "denied"
    view = await q(gd, "kb.document", document_id=doc, page=2)
    assert view["links"][0]["path"].endswith(f"/{doc}/file") and view["links"][0]["page"] == 2


async def test_version_chain_keeps_exactly_one_current(who: Who) -> None:
    """Review 31/10: (1) A bắt đầu hôm nay, B thay A hôm nay ⇒ chỉ B; (2) C không thay A lần nữa được; (3) dời B sang tương lai ⇒ A dùng lại."""
    gd = await who("giamdoc")
    w = f"ch{uuid.uuid4().hex[:6]}"
    a = await _doc(gd, f"Quy định {w} A", f"Hạn nộp {w} là 3 ngày.", effective_from=str(today()))
    b = await _doc(gd, f"Quy định {w} B", f"Hạn nộp {w} là 5 ngày.", effective_from=str(today()), replaces_document_id=a)
    assert [r["document_id"] for r in await _hits(gd, f"hạn nộp {w}", w)] == [b]
    r = await executor.run(gd, "kb.add_document", {"title": f"Quy định {w} C", "kind": "policy", "body": f"Hạn nộp {w} 7 ngày.",
                                                  "replaces_document_id": a})
    assert r.status == "invalid" and "đã có bản thay" in r.error
    assert (await executor.run(gd, "kb.supersede", {"document_id": a, "old_document_id": b})).status == "invalid"   # không tạo vòng
    await ok(gd, "kb.set_validity", document_id=b, effective_from=str(today() + timedelta(days=5)))
    assert [r["document_id"] for r in await _hits(gd, f"hạn nộp {w}", w)] == [a]
    await ok(gd, "kb.set_validity", document_id=b, effective_from=str(today()))
    # trích từ bản đã bị thay ⇒ bị chặn, trừ khi hỏi đúng bản cũ
    old = await executor.query(gd, "kb.quote", {"document_id": a, "text": f"Hạn nộp {w} là 3 ngày"})
    assert old.status == "invalid" and "include_history" in old.error
    assert (await q(gd, "kb.quote", document_id=a, text=f"Hạn nộp {w} là 3 ngày", include_history=True))["state"] == "Đã bị thay thế"


async def test_file_content_is_not_copied_into_the_audit_log(who: Who) -> None:
    gd = await who("giamdoc")
    doc = await _doc(gd, f"Hợp đồng {uuid.uuid4().hex[:6]}", "Nội dung.")
    blob = base64.b64encode(b"%PDF-1.4 secret-contents").decode()
    await ok(gd, "kb.attach_original", document_id=doc, filename="hd.pdf", content_type="application/pdf", data_b64=blob)
    logged = await owner_val("""SELECT input FROM core.audit_log WHERE command = 'kb.attach_original' AND input->>'document_id' = %s
                                ORDER BY id DESC LIMIT 1""", (doc,))
    assert logged["data_b64"]["omitted"] is True and len(logged["data_b64"]["sha256"]) == 64 and blob not in str(logged)


def _guesser(args: dict[str, Any]) -> Any:
    from pydantic_ai import ModelMessage, ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    return FunctionModel(reply, model_name="validity-fake")


async def test_upload_suggests_validity_checked_by_code_and_applied_by_a_person(who: Who) -> None:
    """Trợ lý đọc văn bản đoán ngày hiệu lực + bản bị thay; code kiểm (câu làm bằng phải có nguyên văn, bản bị thay phải là bản đang dùng người
    tải đọc được); chỉ thành GỢI Ý + nút trên trang tài liệu, người bấm mới đổi hiệu lực."""
    gd = await who("giamdoc")
    w = f"vl{uuid.uuid4().hex[:6]}"
    old = await _doc(gd, f"Giấy ĐKDN {w} lần 5", f"Đăng ký thay đổi lần thứ 5 của công ty {w}.")
    body = f"GIẤY CHỨNG NHẬN ĐĂNG KÝ DOANH NGHIỆP {w}\\nĐăng ký thay đổi lần thứ 6: ngày 15 tháng 08 năm 2025\\nNội dung…"
    g = await kb.guess_validity(gd, f"Giấy ĐKDN {w} lần 6", body, model=_guesser(
        {"effective_from": "2025-08-15", "evidence": "Đăng ký thay đổi lần thứ 6: ngày 15 tháng 08 năm 2025", "replaces_id": old,
         "replaces_reason": "lần 6 thay lần 5"}))
    assert g == {"effective_from": "2025-08-15", "evidence": "Đăng ký thay đổi lần thứ 6: ngày 15 tháng 08 năm 2025", "replaces_id": old,
                 "replaces_title": f"Giấy ĐKDN {w} lần 5", "replaces_reason": "lần 6 thay lần 5"}
    new = await _doc(gd, f"Giấy ĐKDN {w} lần 6", body, suggestion_json=json.dumps(g))
    page = await q(gd, "kb.document", document_id=new)
    assert page["sections"][0]["title"].startswith("Trợ lý gợi ý") and "15 tháng 8, 2025" in page["sections"][0]["text"]
    acts = {a["command"]: a for a in page["actions"]}
    assert acts["kb.supersede"]["preset"]["old_document_id"] == old and acts["kb.set_validity"]["preset"]["effective_from"] == "2025-08-15"
    assert (await q(gd, "kb.document", document_id=old))["subtitle"].startswith("Đang dùng")          # gợi ý chưa đổi gì
    await ok(gd, "kb.set_validity", document_id=new, effective_from="2025-08-15")
    await ok(gd, "kb.supersede", document_id=new, old_document_id=old)
    assert (await q(gd, "kb.document", document_id=old))["subtitle"].startswith("Đã bị thay thế")
    assert not (await q(gd, "kb.document", document_id=new))["sections"][0]["title"].startswith("Trợ lý gợi ý")   # đã áp ⇒ thôi gợi ý


async def test_validity_guess_without_proof_or_outside_the_readers_shelves_is_dropped(who: Who) -> None:
    gd, sale1 = await who("giamdoc"), await who("sale1")
    w = f"vx{uuid.uuid4().hex[:6]}"
    shelf = (await ok(gd, "kb.save_shelf", name=f"Kệ {w}", roles=["director"]))["key"]
    hidden = await _doc(gd, f"Quy chế {w} cũ", "Nội dung cũ.", shelf=shelf)
    body = "Quy chế lương thưởng áp dụng cho toàn công ty."
    bogus = await kb.guess_validity(sale1, f"Quy chế {w}", body, model=_guesser(
        {"effective_from": "2026-01-01", "evidence": "áp dụng từ ngày 01/01/2026", "replaces_id": hidden}))
    assert bogus is None              # câu làm bằng không có trong văn bản ⇒ bỏ ngày; bản bị thay ngoài kệ sale đọc được ⇒ bỏ
    def broken(*_: Any) -> Any:
        raise RuntimeError("model down")
    from pydantic_ai.models.function import FunctionModel
    assert await kb.guess_validity(gd, "X", body, model=FunctionModel(broken, model_name="down")) is None   # lỗi không chặn tải lên
