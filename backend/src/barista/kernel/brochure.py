"""ẤN PHẨM TỪ HỒ SƠ (0224, api 1.45; hồ sơ + loại ấn phẩm 0249, api 1.72): gói / module chủ hồ sơ góp "công thức" qua điểm cắm ``compose.brochure`` của
phân hệ Ấn phẩm; lõi dựng khối + chụp số liệu.

Gói khai ``@m.plug("compose.brochure", …)`` (thao tác nội bộ, chạy bằng QUYỀN NGƯỜI SOẠN) nhận ``op`` + ``kind`` + ``subject`` + ``lead_id`` (xem lõi) và:

* ``op == "kinds"`` ⇒ ``{"kinds": [kind(...), ...]}`` — các công thức gói này có (không có ⇒ ``None``). ``kind(…, subject="<module>.<loại>")`` = công thức
  nói về MỘT hồ sơ loại đó (ấn phẩm ghi lại hồ sơ nào); ``piece`` = loại ấn phẩm làm ra (mặc định tờ giới thiệu — loại quyết trang mẫu + định dạng, danh sách ở
  ``modules/compose/kinds.py``); ``customer=True`` = nhận một khách (``lead_id``) để làm bản riêng.
* ``op == "subjects"`` và ``kind`` là của gói ⇒ ``{"subjects": [{"value": <mã hồ sơ>, "label": <tên>}, …]}`` — hồ sơ người soạn ĐƯỢC XEM (RLS), lọc theo
  ``subject`` (chữ người gõ vào ô chọn); ``value`` nên là id (uuid) của hồ sơ. Ô "Hồ sơ" của màn soạn hỏi câu này — người chọn, không gõ mã (0225).
* ``op == "build"`` và ``kind`` là của gói ⇒ ``recipe(...)`` cho hồ sơ ``subject`` (giá trị đã chọn); không phải của gói ⇒ ``None``.

Module chủ hồ sơ đặt nút "Soạn ấn phẩm" trên thẻ của mình: ``ui.act("compose.brochure", "Soạn ấn phẩm", {"kind": <công thức>, "subject": <id>})`` (gói Ấn phẩm
tắt ⇒ nút tự ẩn). Chỗ đứng hợp lệ của khối: ``layout.SLOTS`` của phân hệ Ấn phẩm; mẫu gói nêu trong ``recipe(template=…)`` chỉ được nhận nếu hợp loại ấn phẩm.

Công thức chỉ MÔ TẢ khối (không chứa số): chữ do gói viết (tiêu đề, lời chào — KHÔNG chữ số, code kiểm số vẫn áp), khối ``data`` = TÊN truy vấn + đầu vào (lõi chạy
bằng quyền người soạn rồi chụp — số trên tờ chỉ đến từ đây), ảnh / trích dẫn lấy từ kho tài liệu theo "phạm vi tài liệu" (``context``; ảnh chỉ từ kệ người soạn đọc được).
Truy vấn trong công thức phải là truy vấn thường (không nội bộ, không ghi): RLS / quyền của người soạn quyết dòng nào hiện — người soạn không thấy khách thì
truy vấn báo không thấy và cả lệnh dừng, không có đường nào vòng qua quyền."""

from typing import Any


def kind(key: str, label: str, about: str = "", *, subject: str = "", piece: str = "brochure", customer: bool = False) -> dict[str, Any]:
    """Một công thức. ``subject``: loại hồ sơ công thức nói về (``<module>.<loại>``, trống = không gắn hồ sơ); ``piece``: loại ấn phẩm làm ra; ``customer``:
    nhận khách để làm bản riêng. Ba khoá sau THÊM ở 1.72 — lõi cũ bỏ qua."""
    return {"key": key, "label": label, "about": about, "subject": subject, "piece": piece, "customer": customer}


def heading(text: str, *, level: int = 1, slot: str = "") -> dict[str, Any]:
    return {"type": "heading", "text": text, "level": level, "slot": slot}


def paragraph(text: str, *, slot: str = "") -> dict[str, Any]:
    return {"type": "paragraph", "text": text, "slot": slot}


def data(query: str, title: str = "", input: dict[str, Any] | None = None, *, slot: str = "", required: bool = True, empty: str = "") -> dict[str, Any]:
    """Khối số liệu: chạy ``query`` với ``input`` bằng quyền người soạn. ``required`` ⇒ lỗi (không thấy / không quyền) làm dừng cả lệnh; không ⇒ bỏ khối +
    chỗ giữ. ``empty``: câu thay thế khi truy vấn chạy được nhưng không ra dòng nào (không chữ số)."""
    return {"type": "data", "query": query, "title": title, "input": input or {}, "slot": slot, "required": required, "empty": empty}


def images(context: str, *, limit: int = 6, skip: int = 0, slot: str = "grid") -> dict[str, Any]:
    """Ảnh có phạm vi = ``context`` (vd mã dự án) mà người soạn đọc được, theo thứ tự ưu tiên: (a) thư viện ảnh trong kho tài liệu, (b) ảnh nằm trong tài liệu PDF của
    phạm vi đó (trích một lần); bỏ ``skip`` ảnh đầu (vd ảnh bìa đã lấy bằng một ``images(limit=1, slot="cover")`` đặt trước).

    Khoá TUỲ CHỌN ``need`` (gói thêm vào dict trả về, vd ``{**images(...), "need": "Cần ảnh dự án"}`` — lõi cũ bỏ qua): nhãn chỗ giữ ở MÀN SOẠN khi không có ảnh nào;
    chỉ nên đặt ở ảnh bìa (``skip=0``); chỗ giữ không bao giờ ra tệp / trang gửi khách (0224 §8.4)."""
    return {"type": "images", "context": context, "limit": limit, "skip": skip, "slot": slot}


def excerpts(doc_kind: str, context: str, *, limit: int = 2, chars: int = 480, slot: str = "", title: str = "") -> dict[str, Any]:
    """Đoạn mở đầu NGUYÊN VĂN của tài liệu đang hiệu lực loại ``doc_kind`` (vd ``legal``) có phạm vi ``context`` — kiểm bằng ``kb.quote`` trước khi vào khối."""
    return {"type": "excerpts", "doc_kind": doc_kind, "context": context, "limit": limit, "chars": chars, "slot": slot, "title": title}


def recipe(title: str, blocks: list[dict[str, Any]], *, brief: str, template: str = "", subject_label: str = "") -> dict[str, Any]:
    """``template`` trống = mẫu mặc định của loại ấn phẩm (mẫu không hợp loại cũng về mặc định); ``subject_label``: tên hồ sơ hiện trên ấn phẩm / danh sách (1.72)."""
    return {"title": title, "brief": brief, "template": template, "subject_label": subject_label, "blocks": blocks}
