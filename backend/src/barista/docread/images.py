"""ẢNH trong năng lực đọc tài liệu (ADR 0232) — bốn chế độ, mỗi chế độ ghi rõ TỐN TIỀN hay không:

(a) ``extract`` — LẤY ảnh nằm trong PDF / Word / PowerPoint ra: MIỄN PHÍ, không model (tờ giới thiệu, thư viện ảnh của kho). Lọc ảnh rác (logo, nền nhỏ, dải dài, một màu), bỏ ảnh
    trùng nội dung (cùng băm), chuẩn hoá JPEG ≤ 1600 px. Mọi lỗi đọc ⇒ bỏ ảnh / tài liệu đó, không làm hỏng việc dựng tờ.
(b) CHÉP CHỮ trong ảnh (hoá đơn, biên lai, ảnh chụp trang hợp đồng, ảnh màn hình) — ``core.read`` với file ảnh, model, công tắc ``images_ocr``.
(c) MÔ TẢ ảnh để tìm được ("phối cảnh hồ bơi") — model, công tắc ``images_caption`` MẶC ĐỊNH TẮT.
(d) TẠO ảnh: KHÔNG thuộc đây (tách riêng, mặc định tắt, người duyệt — ADR 0224 §8.4).

``read_images`` đọc một loạt ảnh đã lấy ra bằng (b)/(c) nhưng MỖI ẢNH (theo băm) chỉ trả tiền MỘT lần: trùng trong cùng lượt dùng chung kết quả, trùng với lần trước lấy từ bộ nhớ theo
(băm file, cấu hình đọc) của công ty (``core.docread_cache``).
"""

import hashlib
import io
import logging
import zipfile
from typing import Any

from barista.docread.types import ImageRef, Notice, Usage, ext_of, kind_of

log = logging.getLogger("barista.docread")

MIN_SIDE = 400
MIN_AREA = 200_000
MAX_RATIO = 3.0
MAX_PAGES = 40            # chỉ quét đầu tài liệu — tờ giới thiệu dài hàng trăm trang không đáng quét hết
MAX_PER_DOC = 8
MAX_PX = 1600
FLAT_STDDEV = 6.0         # độ lệch chuẩn độ sáng dưới mức này = ảnh một màu


def _useful(im: Any) -> bool:
    w, h = im.size
    if min(w, h) < MIN_SIDE or w * h < MIN_AREA or max(w, h) / min(w, h) > MAX_RATIO:
        return False
    from PIL import ImageStat

    return bool(ImageStat.Stat(im.convert("L").resize((64, 64))).stddev[0] >= FLAT_STDDEV)


def _norm(blob: bytes, page: int, idx: int, seen: set[str]) -> ImageRef | None:
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(blob))
        im.load()
    except Exception:
        log.debug("docread: ảnh trong tài liệu không đọc được", exc_info=True)
        return None
    if not _useful(im):
        return None
    im = im.convert("RGB")
    im.thumbnail((MAX_PX, MAX_PX))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85, optimize=True)
    data = buf.getvalue()
    sha = hashlib.sha256(data).hexdigest()
    if sha in seen:
        return None
    seen.add(sha)
    return ImageRef(page, idx, im.width, im.height, sha, data)


def _pdf_blobs(data: bytes) -> list[tuple[int, bytes]]:
    from pypdf import PdfReader

    out: list[tuple[int, bytes]] = []
    for pno, page in enumerate(PdfReader(io.BytesIO(data)).pages[:MAX_PAGES], 1):
        try:
            images = list(page.images)
        except Exception:   # trang lạ (bộ lọc không hỗ trợ…): bỏ trang, đi tiếp
            continue
        out += [(pno, img.data) for img in images]
    return out


def _slides_blobs(data: bytes) -> list[tuple[int, bytes]]:
    from pptx import Presentation

    def walk(shapes: Any, n: int) -> list[tuple[int, bytes]]:
        got: list[tuple[int, bytes]] = []
        for sh in shapes:
            if getattr(sh, "shape_type", None) == 6:
                got += walk(sh.shapes, n)
            elif getattr(sh, "shape_type", None) == 13:
                try:
                    got.append((n, sh.image.blob))
                except Exception:   # hình liên kết ngoài / hỏng: bỏ
                    continue
        return got

    out: list[tuple[int, bytes]] = []
    for n, slide in enumerate(Presentation(io.BytesIO(data)).slides, 1):
        out += walk(slide.shapes, n)
    return out


def _word_blobs(data: bytes) -> list[tuple[int, bytes]]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = sorted(n for n in zf.namelist() if n.startswith("word/media/") and not n.endswith("/"))
        return [(1, zf.read(n)) for n in names]


def extract(filename: str, data: bytes, *, limit: int = MAX_PER_DOC) -> list[ImageRef]:
    """Ảnh dùng được của PDF / Word / PowerPoint theo thứ tự (trang / slide). Rỗng khi không có / lỗi. MIỄN PHÍ."""
    kind = kind_of(filename)
    try:
        blobs = {"pdf": _pdf_blobs, "slides": _slides_blobs, "word": _word_blobs}.get(kind)
        if blobs is None:
            return []
        out: list[ImageRef] = []
        seen: set[str] = set()
        idx: dict[int, int] = {}
        for page, blob in blobs(data):
            ref = _norm(blob, page, idx.get(page, 0), seen)
            if ref is None:
                continue
            idx[page] = idx.get(page, 0) + 1
            out.append(ref)
            if len(out) >= limit:
                break
        return out
    except Exception:   # GIỮ rộng: pypdf / python-pptx / Pillow ném đủ loại lỗi trên byte người ngoài đưa vào
        log.warning("docread: không trích được ảnh từ %s", ext_of(filename) or "file", exc_info=True)
        return []


async def read_images(p: Any, refs: list[ImageRef], *, purpose: str = "kb_images", model: Any = None, module: str = "") -> tuple[list[ImageRef], Usage, list[Notice]]:
    """Chép chữ / mô tả (theo hồ sơ) cho các ảnh đã lấy ra. Trả (ảnh đã điền ``text`` / ``caption``, số đo cộng dồn, thông báo). Ảnh trùng băm đọc MỘT lần."""
    from barista.docread import core

    usage, notices, done = Usage(), [], {}
    for ref in refs:
        if ref.sha256 not in done:
            doc = await core.read(p, f"anh-{ref.sha256[:12]}.jpg", ref.data, purpose=purpose, model=model, module=module)
            usage.add(doc.usage)
            for n in doc.notices:
                if not any(x.code == n.code for x in notices):
                    notices.append(n)
            done[ref.sha256] = doc.images[0] if doc.images else None
        got = done[ref.sha256]
        if got is not None:
            ref.text, ref.caption = got.text, got.caption
    return refs, usage, notices
