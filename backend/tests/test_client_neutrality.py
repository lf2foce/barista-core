"""APP CHỈ VẼ (bất biến 7) — ngoại lệ CÓ GIỚI HẠN (0014, ghi chú 08/11/2026): web / Mac / iPhone có bộ vẽ native cho vài khối của gói năng lực
(trao đổi — khối ``thread``; gọi trực tiếp — ``call_page``; xem tài liệu — trang ``style="document"``) và gọi thao tác của CHÍNH các gói đó. Gói
ngành phải được server mô tả trọn: mã app không nhắc thao tác nào của gói ngành, không rẽ nhánh theo tên gói ngành.

Kiểm bằng cách quét chuỗi trong mã app (bỏ test và dòng chú thích): mọi chuỗi dạng ``"<module>.<tên>"`` của một module đã đăng ký phải thuộc gói
nền hoặc gói có bộ vẽ native; khoá module ngành chỉ được xuất hiện trong bảng biểu tượng / màu dự phòng (ngoại lệ còn nợ — kế hoạch bên dưới)."""

import re
from pathlib import Path

import pytest

from barista.kernel import registry

ROOT = Path(__file__).resolve().parents[2]
CLIENT_DIRS = (ROOT / "web" / "src", ROOT / "macos" / "Sources", ROOT / "ios")
SUFFIXES = {".ts", ".tsx", ".swift"}
# Gói NĂNG LỰC có bộ vẽ native (ngoại lệ 0014): thêm gói vào đây = quyết định có chủ đích, ghi vào ADR 0014 cùng commit
NATIVE_PACKS = {"talk", "meet"}
# Chuỗi trùng dạng "<module>.<tên>" nhưng không phải thao tác
NOT_OPS = {"books.vertical": "tên biểu tượng SF Symbols (kệ sách), không phải thao tác của gói Sổ sách"}
# Bảng biểu tượng / màu DỰ PHÒNG theo khoá module khi màn chưa khai icon / tint — còn nợ: server gửi biểu tượng + màu của module (trường THÊM
# trong manifest) rồi xoá hai bảng này (chủ: lõi giao diện; khi làm hợp đồng manifest kế tiếp)
STYLE_FALLBACK = {"web/src/components/icons.tsx", "macos/Sources/BaristaDesk/Theme.swift"}
OP = re.compile(r"[\"'`]([a-z][a-z_]*)\.([a-z_]+)")
COMMENT = re.compile(r"^\s*(//|/\*|\*|#)")


def _sources() -> list[Path]:
    return [p for d in CLIENT_DIRS if d.exists() for p in sorted(d.rglob("*"))
            if p.suffix in SUFFIXES and "__tests__" not in p.parts and ".test." not in p.name and "node_modules" not in p.parts]


def _lines(p: Path) -> list[tuple[int, str]]:
    return [(i, line) for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1) if not COMMENT.match(line)]


def test_clients_call_only_base_and_native_renderer_packs() -> None:
    files = _sources()
    if not files:
        pytest.skip("không có mã app trong bản kiểm này")
    allowed = {"base", *NATIVE_PACKS}
    bad = []
    for p in files:
        for i, line in _lines(p):
            for mod, name in OP.findall(line):
                if mod in registry.MODULE_PACK and f"{mod}.{name}" not in NOT_OPS and registry.MODULE_PACK[mod] not in allowed:
                    bad.append(f"{p.relative_to(ROOT)}:{i}: {mod}.{name} (gói {registry.MODULE_PACK[mod]})")
    assert not bad, "app nhắc thao tác của gói không có bộ vẽ native — để server mô tả:\n" + "\n".join(bad)


def test_industry_module_keys_appear_only_in_the_style_fallback() -> None:
    files = _sources()
    if not files:
        pytest.skip("không có mã app trong bản kiểm này")
    industry = sorted(m for m, pk in registry.MODULE_PACK.items() if registry.pack_kind(pk) == "industry")
    if not industry:
        pytest.skip("không gói ngành nào đang nạp (chạy lõi-không-gói) — không có khoá nào để tìm")
    key = re.compile(r"[\"'`](" + "|".join(industry) + r")[\"'`]|^\s*(" + "|".join(industry) + r"):")
    seen = {str(p.relative_to(ROOT)) for p in files for _, line in _lines(p) if key.search(line)}
    assert seen <= STYLE_FALLBACK, f"khoá module ngành trong mã app ngoài bảng dự phòng: {sorted(seen - STYLE_FALLBACK)}"
    assert seen == STYLE_FALLBACK, f"bảng dự phòng đã hết khoá ngành — xoá ngoại lệ: {sorted(STYLE_FALLBACK - seen)}"


# Khối ``composer`` (màn Ấn phẩm của một gói ngoài — ADR 0254): web chỉ vẽ theo ``kinds[].paper`` / ``kinds[].example`` / ``default_kind`` máy chủ gửi,
# không biết khoá loại ấn phẩm nào (rà 22/11: bảng câu gợi ý, bảng đoán loại theo chữ gõ, ``kind === "slides"`` từng nằm ở web)
COMPOSER_KIND_KEYS = ("slides", "flyer", "post", "memo", "teaser", "brochure", "document")
COMPOSER_FILES = ("web/src/components/blocks/composer*.tsx", "web/src/lib/composer.ts", "web/src/lib/decode.ts")


def test_the_composer_renderer_names_no_publication_kind() -> None:
    files = sorted({p for g in COMPOSER_FILES for p in ROOT.glob(g)})
    if not files:
        pytest.skip("không có mã web trong bản kiểm này")
    key = re.compile(r"[\"'`](" + "|".join(COMPOSER_KIND_KEYS) + r")[\"'`]")
    bad = [f"{p.relative_to(ROOT)}:{i}: {m.group(0)}" for p in files for i, line in _lines(p) if (m := key.search(line))]
    assert not bad, "web viết cứng khoá loại ấn phẩm — để máy chủ gửi trong khối composer:\n" + "\n".join(bad)
