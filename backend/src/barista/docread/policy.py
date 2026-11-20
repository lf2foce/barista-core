"""CẤU HÌNH ĐỌC TÀI LIỆU là DỮ LIỆU (ADR 0232, bất biến 5): hồ sơ đọc theo MỤC ĐÍCH (``kb``, ``kb_images``, ``brochure_images``, ``invoice``…), lớp chồng:

    mặc định trong mã  →  ``settings.docread["default"]``  →  ``settings.docread["purposes"][mục đích]``
    →  dòng công ty ``core.docread_profiles`` mục đích ``*``  →  dòng công ty đúng mục đích      (chỉ nhà vận hành ghi — như hạng trợ lý AI, 0193)

Hồ sơ trả lời bốn câu hỏi về TIỀN: (1) bộ đọc nào, theo thứ tự (``chain``); (2) model nào (``model`` — trống = model mặc định của bản cài); (3) việc nào được phép
tốn model (``scanned_pdf`` / ``tables`` / ``images_ocr`` / ``images_caption`` — mỗi cái một công tắc); (4) trần cứng (``max_model_pages`` mỗi tài liệu,
``monthly_tokens`` cả công ty mỗi tháng, đếm bằng chính ``core.ai_usage`` loại ``docread:*``). Vượt trần ⇒ bộ đọc miễn phí + thông báo rõ, không bao giờ im lặng.
"""

import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, replace
from typing import Any
from uuid import UUID

from barista.config import settings
from barista.db import db
from barista.kernel.errors import Invalid
from barista.kernel.principal import Principal

log = logging.getLogger("barista.docread")

# mục đích đã biết (nhãn cho màn dùng AI + bảng giá); mục đích khác vẫn dùng được (khoá chữ thường)
PURPOSES = {
    "kb": "Kho tài liệu", "kb_images": "Ảnh trong tài liệu", "brochure_images": "Ảnh cho tờ giới thiệu", "invoice": "Hoá đơn / chứng từ",
    "credential": "Giấy tờ nhân sự", "other": "Khác",
}
KEY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
TABLE_MODES = {"off": "không xử lý bảng", "text": "trích bảng bằng code (miễn phí)", "auto": "bằng code, bảng lệch cột thì nhờ model", "model": "mọi trang có bảng nhờ model"}
DEFAULT_CHAIN = ("office", "pypdf", "model")
MODEL_PAGES_MAX = 500


@dataclass(frozen=True)
class Policy:
    purpose: str = "other"
    chain: tuple[str, ...] = DEFAULT_CHAIN     # thứ tự bộ đọc; bỏ "model" khỏi chuỗi = không bao giờ tốn model
    model: str = ""                            # nhà_cung_cấp:tên_model; trống = model mặc định của bản cài (agent/llm)
    scanned_pdf: bool = True                   # model đọc trang PDF không có lớp chữ (scan / chụp)
    tables: str = "auto"                       # off | text | auto | model
    images_ocr: bool = True                    # model chép CHỮ trong ảnh (hoá đơn, biên lai, trang hợp đồng chụp)
    images_caption: bool = False               # model MÔ TẢ ảnh để tìm được (tốn thêm token; mặc định tắt)
    max_model_pages: int = 30                  # trần trang / ảnh gửi model MỖI tài liệu
    monthly_tokens: int | None = None          # trần token đọc tài liệu của cả công ty / tháng (None = không đặt)
    source: str = "mặc định"

    def uses_model(self) -> bool:
        return "model" in self.chain

    def fingerprint(self) -> str:
        """Phần cấu hình làm ĐỔI kết quả đọc (không gồm trần) — khoá bộ nhớ theo băm file: đổi model / công tắc ⇒ đọc lại, giữ nguyên ⇒ không tốn lần hai."""
        spec = self.model or settings.llm_model
        raw = json.dumps([list(self.chain), spec, self.scanned_pdf, self.tables, self.images_ocr, self.images_caption, "v1"])
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def describe(self) -> str:
        cap = f"; ≤ {self.monthly_tokens:,} token/tháng".replace(",", ".") if self.monthly_tokens else ""
        return (f"{'>'.join(self.chain)}; model {self.model or '(mặc định)'}; scan {'bật' if self.scanned_pdf else 'tắt'}; bảng {self.tables}; "
                f"ảnh: chữ {'bật' if self.images_ocr else 'tắt'}, mô tả {'bật' if self.images_caption else 'tắt'}; ≤ {self.max_model_pages} trang/tài liệu{cap}")


def validate(raw: dict[str, Any], *, readers: set[str] | None = None) -> dict[str, Any]:
    """Làm sạch một hồ sơ (một phần các khoá). Sai ⇒ ``Invalid`` nêu rõ khoá. Dùng cho cả ``settings.docread`` lẫn lệnh nhà vận hành."""
    from barista.docread.readers import names

    known = readers if readers is not None else set(names())
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k == "chain":
            chain = tuple(str(x).strip() for x in (v.split(",") if isinstance(v, str) else v) if str(x).strip())
            if not chain or any(c not in known for c in chain):
                raise Invalid(f"chain: bộ đọc phải thuộc {', '.join(sorted(known))}")
            out[k] = list(dict.fromkeys(chain))
        elif k == "model":
            spec = str(v or "").strip()
            if spec:
                from barista.agent import llm

                if not re.match(r"^[a-z][a-z0-9-]*:[^\s:]+$", spec) or not llm.known_provider(spec):
                    raise Invalid(f"model “{spec}” không hợp lệ — dạng nhà_cung_cấp:tên_model, vd google:gemini-3.8-flash")
            out[k] = spec
        elif k in ("scanned_pdf", "images_ocr", "images_caption"):
            out[k] = v if isinstance(v, bool) else str(v).lower() in ("1", "yes", "on", "true", "co", "có")
        elif k == "tables":
            if v not in TABLE_MODES:
                raise Invalid("tables: một trong " + ", ".join(TABLE_MODES))
            out[k] = v
        elif k == "max_model_pages":
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= MODEL_PAGES_MAX:
                raise Invalid(f"max_model_pages: số nguyên 0–{MODEL_PAGES_MAX} (0 = không trang nào tốn model)")
            out[k] = v
        elif k == "monthly_tokens":
            if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 1000):
                raise Invalid("monthly_tokens: số nguyên ≥ 1000 hoặc bỏ trống (không đặt trần)")
            out[k] = v
        else:
            raise Invalid(f"khoá “{k}” không thuộc hồ sơ đọc tài liệu")
    return out


def apply(p: Policy, upd: dict[str, Any], source: str) -> Policy:
    """Chồng một lớp (đã ``validate``) lên hồ sơ."""
    if not upd:
        return p
    d = dict(upd)
    if "chain" in d:
        d["chain"] = tuple(d["chain"])
    return replace(p, **d, source=source)


def instance_policy(purpose: str) -> Policy:
    cfg = settings.docread or {}
    p = Policy(purpose=purpose)
    for src, layer in (("bản cài", cfg.get("default") or {}), (f"bản cài/{purpose}", (cfg.get("purposes") or {}).get(purpose) or {})):
        p = apply(p, validate(layer), src)
    return p


# hồ sơ công ty: nhớ ngắn hạn theo công ty (mỗi lần đọc tài liệu không tốn thêm một lượt DB); lệnh ghi xoá bản nhớ của công ty
_CACHE: dict[UUID, tuple[float, dict[str, dict[str, Any]]]] = {}
TTL_S = 30.0


def invalidate(tenant_id: UUID | None = None) -> None:
    if tenant_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(tenant_id, None)


async def company_rows(p: Principal) -> dict[str, dict[str, Any]]:
    hit = _CACHE.get(p.tenant_id)
    if hit and time.monotonic() - hit[0] < TTL_S:
        return hit[1]
    async with db.tenant_tx(p, read_only=True, wait=5) as c:
        cur = await c.execute("SELECT purpose, profile FROM core.docread_profiles")
        rows = {r["purpose"]: dict(r["profile"]) for r in await cur.fetchall()}
    _CACHE[p.tenant_id] = (time.monotonic(), rows)
    return rows


def compose(purpose: str, rows: dict[str, dict[str, Any]]) -> Policy:
    """Hồ sơ đang áp: mặc định bản cài rồi các dòng công ty (``*`` rồi đúng mục đích)."""
    pol = instance_policy(purpose)
    for key in ("*", purpose):
        if key in rows:
            try:
                pol = apply(pol, validate(rows[key]), "công ty")
            except Invalid:   # dòng cũ không còn hợp lệ (bộ đọc đã gỡ…): bỏ dòng, giữ lớp dưới + để lại dấu cho quản trị
                log.warning("hồ sơ đọc tài liệu của công ty (%s) không hợp lệ — bỏ qua", key, exc_info=True)
    return pol


async def resolve(p: Principal, purpose: str) -> Policy:
    """Hồ sơ đang áp cho (công ty, mục đích)."""
    if not KEY.match(purpose):
        raise ValueError(f"mục đích “{purpose}” sai tên")
    return compose(purpose, await company_rows(p))


async def used_tokens(p: Principal) -> int:
    """Token đọc tài liệu công ty đã dùng THÁNG NÀY (mọi mục đích — ``core.ai_usage`` loại ``docread:*``; mọi token trừ token đọc cache, như hạn mức người)."""
    from barista.kernel.clock import today

    async with db.tenant_tx(p, read_only=True, wait=5) as c:
        cur = await c.execute("""SELECT coalesce(sum(greatest(input_tokens - cache_read_tokens, 0) + output_tokens), 0) AS n FROM core.ai_usage
                                 WHERE category LIKE 'docread:%%' AND at >= %s::date""", (today().replace(day=1),))
        row = await cur.fetchone()
    return int(row["n"]) if row else 0


def as_dict(p: Policy) -> dict[str, Any]:
    d = asdict(p)
    d["chain"] = list(p.chain)
    return d


@dataclass
class Budget:
    """Hạn mức token đọc tài liệu CÒN LẠI của công ty trong tháng cho một lượt đọc (``cap`` None = không đặt trần). ``spent`` cộng dồn ngay trong lượt."""
    cap: int | None = None
    used: int = 0
    spent: int = 0

    def exhausted(self) -> bool:
        return self.cap is not None and self.used + self.spent >= self.cap
