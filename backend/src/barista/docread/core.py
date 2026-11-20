"""``read`` — MỘT cửa đọc tài liệu cho cả hệ thống (ADR 0232).

    doc = await read(principal, filename, data, purpose="kb")

Việc của hàm này: lấy hồ sơ đọc của (công ty, mục đích) → xem BỘ NHỚ theo (băm file, cấu hình) → chạy các bộ đọc theo thứ tự của chuỗi (bộ miễn phí trước, bộ tốn tiền
chỉ nhận trang đã bị gắn cờ, trong trần) → trả ``Doc`` có chữ + bảng + ảnh + phương pháp từng trang + thông báo + số đo. Không bao giờ im lặng khi một trang cần model mà không
được đọc (công tắc tắt / trần / hết hạn mức / AI tắt / lỗi): ``Doc.notices`` nói rõ vì sao, ``Doc.unread()`` liệt kê trang.
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from barista.docread import policy as pol_mod
from barista.docread import readers
from barista.docread.policy import Budget, Policy
from barista.docread.titles import hint_title
from barista.docread.types import (
    AI_OFF,
    MODEL_ERROR,
    NO_MODEL,
    OVER_BUDGET,
    PAGE_CAP,
    Doc,
    ImageRef,
    Notice,
    Page,
    Table,
    Usage,
    kind_of,
)
from barista.kernel.errors import Invalid
from barista.kernel.principal import Principal

log = logging.getLogger("barista.docread")

CACHE_MAX_BYTES = 1_500_000
INCOMPLETE = {AI_OFF, NO_MODEL, MODEL_ERROR, PAGE_CAP, OVER_BUDGET}   # kết quả thiếu phần ⇒ không nhớ (đọc lại khi hết nghẽn)
ACCEPT = "PDF, Word, Excel / CSV, PowerPoint, ảnh (jpg, png, webp, heic) hoặc chữ (.txt .md)"


@dataclass
class Hints:
    """Gợi ý của nơi gọi (chỉ THU HẸP, không mở rộng quyền tốn tiền của hồ sơ): ``pages`` chỉ đọc các trang này; ``extra`` câu dặn thêm cho model
    (vd "đây là hoá đơn bán hàng, cần số hoá đơn, ngày, tổng tiền")."""
    pages: tuple[int, ...] | None = None
    extra: str = ""


async def read(p: Principal | None, filename: str, data: bytes, *, purpose: str = "kb", hints: Hints | None = None, policy: Policy | None = None,
               model: Any = None, module: str = "", cache: bool = True, refresh: bool = False, meter: bool = True) -> Doc:
    """Đọc ``data`` thành ``Doc``. ``purpose``: ``kb`` / ``kb_images`` / ``brochure_images`` / ``invoice`` / ``credential`` / khác (khoá chữ thường) — chọn hồ sơ đọc + loại lượt dùng AI
    ``docread:<mục đích>``. ``policy`` truyền tay (benchmark) ⇒ bỏ qua hồ sơ công ty; ``model`` tiêm model (test / benchmark); ``refresh`` bỏ bộ nhớ (đọc lại, trả tiền lại)."""
    kind = kind_of(filename)
    if not kind:
        raise Invalid(f"Chưa đọc được loại file này — nhận {ACCEPT}")
    pol = policy or (await pol_mod.resolve(p, purpose) if p is not None else pol_mod.instance_policy(purpose))
    h = hints or Hints()
    doc = Doc(filename, kind, policy=pol.describe())
    paid_possible = pol.uses_model() and kind in ("pdf", "image") and any((rd := readers.get(n)) and rd.paid and rd.handles(kind) for n in pol.chain)
    sha = doc.sha256 = hashlib.sha256(data).hexdigest()
    cache = cache and model is None   # model tiêm (test / benchmark) không đi qua bộ nhớ — kết quả của nó không thuộc cấu hình thật
    if paid_possible and cache and p is not None and not refresh and (hit := await _cache_get(p, sha, pol.fingerprint(), filename)) is not None:
        return hit
    budget = Budget(pol.monthly_tokens, await pol_mod.used_tokens(p) if (paid_possible and pol.monthly_tokens and p is not None) else 0)
    req = readers.Request(p, filename, data, pol, purpose, pages=h.pages, extra=h.extra, model=model, module=module, budget=budget, meter=meter)
    if kind == "image":
        doc.pages = [Page(1, flags=["image"])]
    for name in pol.chain:
        rd = readers.get(name)
        if rd is not None and rd.handles(kind):
            await rd.read(req, doc)
    if kind != "image" and not doc.title_hint:
        doc.title_hint = await asyncio.to_thread(hint_title, filename, data, doc.text())
    if paid_possible and cache and p is not None and doc.usage.model_pages and not any(n.code in INCOMPLETE for n in doc.notices):
        await _cache_put(p, sha, pol.fingerprint(), doc)
    return doc


def read_free(filename: str, data: bytes, *, tables: str = "text") -> Doc:
    """Đọc MIỄN PHÍ, đồng bộ, không DB / model — chỉ bộ ``office`` + ``pypdf``. Cho nơi đang giữ kết nối DB (``kb.receive``) hoặc chỉ cần chữ có sẵn. Trang scan ⇒ ``Doc.unread()``."""
    kind = kind_of(filename)
    if not kind or kind == "image":
        raise Invalid(f"Chưa đọc được loại file này — nhận {ACCEPT}")
    pol = Policy(chain=("office", "pypdf"), tables=tables)
    doc = Doc(filename, kind, sha256=hashlib.sha256(data).hexdigest())
    req = readers.Request(None, filename, data, pol, "other")
    for name in pol.chain:
        rd = readers.get(name)
        if rd is not None and rd.handles(kind):
            rd.read_sync(req, doc)   # type: ignore[attr-defined]
    return doc


# ── bộ nhớ theo (băm file, cấu hình): MỘT file + MỘT cấu hình không trả tiền hai lần ──

def doc_to_json(doc: Doc) -> dict[str, Any]:
    def img(i: ImageRef) -> dict[str, Any]:
        return {"page": i.page, "idx": i.idx, "w": i.width, "h": i.height, "sha": i.sha256, "text": i.text, "caption": i.caption}

    return {"paged": doc.paged, "title": doc.title_hint, "policy": doc.policy,
            "notices": [{"code": n.code, "message": n.message, "pages": n.pages} for n in doc.notices],
            "usage": {"models": doc.usage.models, "pages": doc.usage.model_pages},
            "pages": [{"n": p.n, "text": p.text, "method": p.method, "confidence": p.confidence, "flags": p.flags,
                       "tables": [{"header": t.header, "rows": t.rows} for t in p.tables], "images": [img(i) for i in p.images]} for p in doc.pages]}


def doc_from_json(filename: str, kind: str, j: dict[str, Any]) -> Doc:
    pages = [Page(p["n"], p["text"], [Table(t["header"], t["rows"], p["n"]) for t in p["tables"]],
                  [ImageRef(i["page"], i["idx"], i["w"], i["h"], i["sha"], b"", i["text"], i["caption"]) for i in p["images"]], p["method"], p["confidence"], p["flags"])
             for p in j["pages"]]
    return Doc(filename, kind, pages, j["paged"], j["title"], [Notice(n["code"], n["message"], n["pages"]) for n in j["notices"]],
               Usage(models=j["usage"]["models"], cost_usd=Decimal(0)), cached=True, policy=j["policy"])


async def _cache_get(p: Principal, sha: str, fp: str, filename: str) -> Doc | None:
    from barista.db import db

    try:
        async with db.tenant_tx(p, wait=5) as c:
            cur = await c.execute("""UPDATE core.docread_cache SET hits = hits + 1, last_hit_at = now() WHERE sha256 = %s AND fingerprint = %s RETURNING doc""", (sha, fp))
            row = await cur.fetchone()
    except Exception:   # bộ nhớ chỉ là tối ưu: lỗi DB / kết nối ⇒ đọc như chưa có (log để thấy)
        log.warning("docread: không tra được bộ nhớ", exc_info=True)
        return None
    if not row:
        return None
    doc = doc_from_json(filename, kind_of(filename), row["doc"])
    doc.sha256 = sha
    return doc


async def _cache_put(p: Principal, sha: str, fp: str, doc: Doc) -> None:
    from barista.db import db

    payload = json.dumps(doc_to_json(doc), ensure_ascii=False)
    if len(payload) > CACHE_MAX_BYTES:
        return
    try:
        async with db.tenant_tx(p, wait=5) as c:
            await c.execute("""INSERT INTO core.docread_cache (sha256, fingerprint, doc) VALUES (%s, %s, %s::jsonb)
                               ON CONFLICT (tenant_id, sha256, fingerprint) DO UPDATE SET doc = EXCLUDED.doc, last_hit_at = now()""", (sha, fp, payload))
    except Exception:   # tối ưu thôi — không làm hỏng việc đọc đã xong
        log.warning("docread: không ghi được bộ nhớ", exc_info=True)
