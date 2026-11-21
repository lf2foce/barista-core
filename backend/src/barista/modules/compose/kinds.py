"""LOẠI ẤN PHẨM — MỘT danh sách (ADR 0249 lát 2): loại quyết định trang mẫu được dùng, định dạng xuất và lời dặn hình dạng cho AI.

Trước đây bốn nơi tự giữ danh sách và lệch nhau (lời mô tả trường ``template`` của đầu ra model, lời dặn ``SHAPE``, ``DEFAULT_FOR_KIND`` của bố cục, bộ
``SLOTS`` / ``TEMPLATES`` của ``kernel/brochure.py`` — thiếu tờ rơi). Giờ:

* trang mẫu (chi tiết vẽ) chỉ ở ``layout.TEMPLATES``, chỗ đứng chỉ ở ``layout.SLOTS``;
* loại ⇒ ``templates`` (mẫu HỢP loại này, mẫu đầu là mặc định; rỗng = không bố cục, vd slide xuất PowerPoint) + ``formats`` (định dạng xuất, đầu là chính);
* AI không chọn trang mẫu: chọn loại là xong mẫu + định dạng; người đổi mẫu chỉ trong số mẫu của loại (``compose.set_layout`` kiểm).

``document`` (Văn bản) chỉ còn cho bản soạn cũ — không mời chọn khi làm mới (0231: văn bản hành chính ở phân hệ Giấy tờ).
"""

from dataclasses import dataclass

from barista.modules.compose import layout

FORMATS = {"pdf": "PDF", "docx": "Word (.docx)", "pptx": "PowerPoint (.pptx)", "html": "Trang web (.html)"}


@dataclass(frozen=True)
class Kind:
    label: str
    templates: tuple[str, ...]   # mẫu hợp loại này (khoá của ``layout.TEMPLATES``); mẫu đầu = mặc định; () = không bố cục
    formats: tuple[str, ...]     # định dạng xuất được (khoá của ``FORMATS``); đầu = định dạng chính
    shape: str                   # lời dặn hình dạng cho AI (chỉ nói khối + chỗ đứng; mẫu do loại chọn, AI không chọn)
    offered: bool = True         # mời chọn khi làm ấn phẩm mới

    @property
    def template(self) -> str:
        return self.templates[0] if self.templates else ""


def _paged_shape(template: str, what: str) -> str:
    """Lời dặn hình dạng của loại NHIỀU TRANG CỐ ĐỊNH — sinh từ danh sách trang của mẫu (``layout.TEMPLATES[..]["pages"]``), không viết hai lần."""
    pages = layout.TEMPLATES[template]["pages"]
    return (f"{what} A4 ngang, {len(pages)} trang cố định; mỗi khối nêu slot \"p<số trang>\" của trang nó đứng. Tiêu đề trang (heading 1 đầu trang) là MỘT CÂU KẾT LUẬN, không phải nhãn chung. "
            "Số chỉ nằm ở khối data (ô số hoặc bảng); đoạn chữ không có chữ số; số ước tính ghi rõ trong đề / tên khối. Ảnh chỉ khi có (không tự tạo). "
            + " · ".join(f"{p['key']} = {p['about']}" for p in pages) + ". Chân liên hệ slot \"footer\". Cần số mà không có ⇒ chỗ giữ \"[cần số liệu: …]\".")


KINDS: dict[str, Kind] = {
    "flyer": Kind("Tờ rơi", ("flyer",), ("pdf", "html", "docx"),
                  "Tờ rơi MỘT trang, đọc trong 10 giây: tiêu đề lớn gây chú ý (heading 1), một câu phụ, 3–4 lợi ích ngắn (mỗi ý một đoạn rất ngắn), MỘT ưu đãi / "
                  "con số nổi bật bằng khối data chỉ có ô số, lời kêu gọi + liên hệ cuối. 5–9 khối. Chỗ đứng: ảnh đầu tiên slot \"cover\" cùng câu phụ slot "
                  "\"cover\"; lợi ích xếp slot \"col1\" / \"col2\" / \"col3\"; ô số slot \"tiles\"; kêu gọi / liên hệ slot \"footer\"."),
    "brochure": Kind("Tờ giới thiệu", ("editorial", "brochure", "proposal"), ("pdf", "html", "docx"),
                     "Tờ giới thiệu một trang: câu mở hấp dẫn, 3–5 phần ngắn (heading 2), số liệu nổi bật bằng khối data, câu kêu gọi liên hệ cuối. 6–14 khối. "
                     "Chỗ đứng: ảnh đầu tiên (nếu có) slot \"cover\" cùng một câu mở ngắn slot \"cover\"; khối data chỉ có ô số (kpis, không dòng bảng) slot "
                     "\"tiles\"; các phần ngắn song song xếp slot \"col1\" / \"col2\"; nhiều ảnh slot \"grid\"; lời kêu gọi / liên hệ slot \"footer\". Khi SỬA "
                     "bản có sẵn, giữ nguyên slot của khối (outline có ghi slot)."),
    "slides": Kind("Slide trình bày", (), ("pptx", "pdf"),
                   "Slide: MỖI heading 1 là một slide; dưới mỗi slide 1–4 đoạn rất ngắn (gạch đầu dòng) hoặc MỘT khối data. 5–10 slide. Không dùng slot."),
    "post": Kind("Bài đăng", ("plain",), ("html", "docx"),
                 "Bài đăng mạng xã hội: câu mở 1 dòng gây chú ý, 2–4 đoạn rất ngắn (có thể dùng gạch đầu dòng), lời kêu gọi hành động, 3–6 hashtag ở đoạn cuối. "
                 "Tối đa một ảnh (khối image) và một khối data nếu cần dẫn số. 3–8 khối. Giọng gần gũi, không viết hoa cả câu, không hứa quá sự thật. Không dùng slot."),
    "memo": Kind("Hồ sơ chào bán 10 trang", ("memo10",), ("pdf", "html"), _paged_shape("memo10", "Hồ sơ chào bán tài sản")),
    "teaser": Kind("Tóm tắt 4 trang", ("teaser4",), ("pdf", "html"), _paged_shape("teaser4", "Tóm tắt chào bán tài sản")),
    "document": Kind("Văn bản (bản cũ)", ("plain", "proposal"), ("docx", "pdf", "pptx", "html"),
                     "Tiêu đề mục (heading 1) cho từng phần; mỗi phần 1–3 đoạn ngắn + khối số liệu khi cần. 6–20 khối.", offered=False),
}
OFFERED = tuple(k for k, v in KINDS.items() if v.offered)


def of(kind: str) -> Kind:
    """Loại của một ấn phẩm (loại lạ trong dữ liệu cũ ⇒ coi như bản cũ, không vỡ)."""
    return KINDS.get(kind) or KINDS["document"]


def template_for(kind: str, wanted: str = "") -> str:
    """Trang mẫu dùng được cho loại ``kind``: ``wanted`` nếu hợp loại, không thì mẫu mặc định của loại ("" = không bố cục)."""
    k = of(kind)
    return wanted if wanted in k.templates else k.template


def templates(kind: str) -> list[dict[str, str]]:
    """Mẫu mời chọn cho loại (chỉ mẫu hợp loại) — app vẽ ô chọn từ đây."""
    return [{"key": t, "label": layout.TEMPLATES[t]["label"], "about": layout.TEMPLATES[t]["about"]} for t in of(kind).templates]


def formats(kind: str) -> list[dict[str, str]]:
    return [{"key": f, "label": FORMATS[f]} for f in of(kind).formats]


def check() -> None:
    """Danh sách tự kiểm lúc nạp: mọi mẫu / định dạng nhắc tới đều có thật (lệch ⇒ lỗi ngay khi khởi động, không đợi người bấm)."""
    for key, k in KINDS.items():
        if bad := [t for t in k.templates if t not in layout.TEMPLATES]:
            raise ValueError(f"loại ấn phẩm {key}: trang mẫu không có {bad}")
        if not k.formats or (bad := [f for f in k.formats if f not in FORMATS]):
            raise ValueError(f"loại ấn phẩm {key}: định dạng lạ {bad}")


check()
