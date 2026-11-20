"""BỘ ĐỌC cắm được (ADR 0232): một giao diện, nhiều cách đọc; cấu hình chọn bộ nào chạy theo thứ tự (``Policy.chain``).

``Reader.read(req, doc)`` ĐỌC TIẾP vào ``doc`` (điền / thay trang, gắn cờ, cộng số đo). Bộ miễn phí (``paid = False``: ``office``, ``pypdf``) chạy trước và gắn cờ cho trang
cần giúp (``scanned`` — không có lớp chữ; ``table_ragged`` — bảng trích bằng code chưa chắc đúng); bộ tốn tiền (``model``) chỉ nhận đúng các trang đó, trong
trần của hồ sơ. Bộ NGOÀI (dịch vụ tài liệu của nhà cung cấp — Document AI, Textract, OCR riêng…) cài bằng ``register(reader)``: chỉ cần ``name`` / ``paid`` /
``handles`` / ``read``; hiện CHƯA có bộ ngoài nào đi kèm (``ExternalReader`` là khung để cắm).
"""

from dataclasses import dataclass
from typing import Any, Protocol

from barista.docread.policy import Policy
from barista.docread.types import Doc, Notice
from barista.kernel.principal import Principal


@dataclass
class Request:
    """Một lượt đọc: file + hồ sơ + bối cảnh (người gọi cho công tắc AI / đo dùng; model tiêm cho test / benchmark)."""
    p: Principal | None
    filename: str
    data: bytes
    policy: Policy
    purpose: str
    pages: tuple[int, ...] | None = None    # gợi ý: chỉ đọc các trang này (số trang 1…)
    extra: str = ""                         # gợi ý thêm cho model (vd "đây là hoá đơn VAT")
    model: Any = None
    module: str = ""                        # module gọi — ghi vào lượt dùng AI
    budget: Any = None                      # ``Budget`` của lượt này (trần token tháng còn lại)
    meter: bool = True                      # False ⇒ không ghi ``core.ai_usage`` (benchmark / thử trên máy không có DB)


class Reader(Protocol):
    name: str
    paid: bool

    def handles(self, kind: str) -> bool: ...

    async def read(self, req: Request, doc: Doc) -> None: ...


class ExternalReader:
    """Khung cho bộ đọc dịch vụ ngoài: lớp con cài ``read`` (gọi dịch vụ, điền ``doc.pages``, cộng ``doc.usage``), rồi ``register(MyReader())`` và thêm tên vào chuỗi.
    ``paid = True`` ⇒ chịu trần trang / token như ``model``. Dữ liệu công ty đi ra ngoài ⇒ chỉ cắm khi công ty đã đồng ý (như nhà cung cấp AI thứ hai, 0193)."""
    name = "external"
    paid = True
    kinds: frozenset[str] = frozenset({"pdf", "image"})

    def handles(self, kind: str) -> bool:
        return kind in self.kinds

    async def read(self, req: Request, doc: Doc) -> None:   # pragma: no cover — khung
        raise NotImplementedError


_REG: dict[str, Reader] = {}


def register(reader: Reader) -> None:
    _REG[reader.name] = reader


def get(name: str) -> Reader | None:
    return _REG.get(name)


def names() -> list[str]:
    return sorted(_REG)


def notice(doc: Doc, code: str, message: str, pages: list[int] | None = None) -> None:
    """Một thông báo mỗi mã (gộp trang vào thông báo đã có)."""
    if n := doc.notice(code):
        n.pages = sorted({*n.pages, *(pages or [])})
    else:
        doc.notices.append(Notice(code, message, sorted(pages or [])))


def _load() -> None:
    from barista.docread.readers import model, office, pdf

    for r in (office.OfficeReader(), pdf.PdfTextReader(), model.ModelReader()):
        register(r)


_load()
