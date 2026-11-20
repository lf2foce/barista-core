"""HỒ SƠ NHÚNG THEO CÔNG TY (ADR 0232, mục "Nhúng theo công ty"): model embedding + số chiều (+ địa chỉ máy chủ tự dựng) là CHÍNH SÁCH CỦA NHÀ VẬN HÀNH
như hạng trợ lý AI (0193) và hồ sơ đọc tài liệu: khách không tự đổi nhà cung cấp nhận dữ liệu của họ, cũng không tự đẩy chi phí lên.

Lớp chồng (cùng cách ``docread.policy``):  ``settings.embed_model`` / ``embed_dims`` / ``embed_profile``  →  dòng công ty ``core.embed_profiles``.

Hồ sơ gồm:
  ``model``   ``nhà_cung_cấp:tên`` — ``google:gemini-embedding-001``, ``openai:text-embedding-3-small``… hoặc ``openai-compatible:<tên>`` (Ollama / vLLM / TEI
              phục vụ EmbeddingGemma, nomic, bge-m3… trên máy của mình; bắt buộc ``base_url``). Rỗng = TẮT tìm theo nghĩa cho công ty này.
  ``dims``    số chiều của vector LƯU (chuẩn 768 — chỉ mục HNSW có sẵn; số khác chỉ khi đã dựng chỉ mục cho số đó — ``kb.vectors add-index``).
  ``base_url`` máy chủ tương thích OpenAI (``http://host:11434/v1``); ``key_ref`` tên mục trong ``settings.llm_keys`` giữ khoá (không bao giờ ghi khoá vào DB).
  ``truncate`` model Matryoshka trả nhiều chiều hơn ``dims`` ⇒ cắt phía mình rồi chuẩn hoá (không gửi ``dimensions`` — vLLM từ chối model không hỗ trợ). Chỉ
              bật cho model ĐƯỢC HUẤN LUYỆN kiểu Matryoshka; cắt model khác làm vector hỏng mà không báo lỗi.
  ``query_prefix`` / ``document_prefix`` tiền tố chữ model đòi (EmbeddingGemma, nomic, e5…): câu hỏi và đoạn tài liệu nhúng khác nhau.
  ``usd_per_mtok`` giá cho máy chủ tự dựng (mặc định 0 USD — vẫn đếm lượt + token).
  ``min_sim`` ngưỡng độ giống cosine của nhánh nghĩa (mặc định ``kb_vector_min_sim`` = 0,65, đo cho Gemini): mỗi model một phân bố điểm — đổi model thì đo lại
              (``python -m barista.docread.bench --retrieval``) rồi đặt.

Vector của hai model không so được ⇒ mỗi đoạn ghi ``embed_model`` + ``embed_dims`` và tìm chỉ so với model ĐANG DÙNG của công ty (không bao giờ trộn).
"""

import hashlib
import json
import re
import time
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from barista.config import settings
from barista.db import db
from barista.kernel.errors import Invalid
from barista.kernel.principal import Principal

COMPAT = "openai-compatible"
STANDARD_DIMS = 768
MAX_DIMS = 4000          # chỉ mục halfvec tối đa 4.000 chiều (pgvector); vector ≤ 2.000
VECTOR_INDEX_MAX = 2000
KEYS = ("model", "dims", "base_url", "key_ref", "truncate", "query_prefix", "document_prefix", "usd_per_mtok", "min_sim")

# Gợi ý khi nhà vận hành không nêu: chỉ ĐIỀN SẴN lúc đặt hồ sơ (hồ sơ lưu là dữ liệu tường minh), không phải luật. (khớp chuỗi con trong tên model, chữ thường)
PRESETS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("embeddinggemma", {"native": 768, "truncate": True, "query_prefix": "task: search result | query: ", "document_prefix": "title: none | text: "}),
    ("nomic-embed-text", {"native": 768, "truncate": True, "query_prefix": "search_query: ", "document_prefix": "search_document: "}),
    ("bge-m3", {"native": 1024, "truncate": False}),
    ("multilingual-e5-large", {"native": 1024, "truncate": False, "query_prefix": "query: ", "document_prefix": "passage: "}),
    ("gemini-embedding-001", {"native": 3072, "truncate": False}),
    ("text-embedding-3-small", {"native": 1536, "truncate": False}),
    ("text-embedding-3-large", {"native": 3072, "truncate": False}),
)


def preset(model: str) -> dict[str, Any]:
    name = model.split(":", 1)[-1].lower()
    return next((dict(v) for k, v in PRESETS if k in name), {})


@dataclass(frozen=True)
class Profile:
    model: str = ""
    dims: int = STANDARD_DIMS
    base_url: str = ""
    key_ref: str = ""
    truncate: bool = False
    query_prefix: str = ""
    document_prefix: str = ""
    usd_per_mtok: float | None = None
    min_sim: float | None = None     # ngưỡng giống tối thiểu của nhánh nghĩa; trống = ``settings.kb_vector_min_sim`` (0,65 đo cho Gemini — model khác đo lại)
    source: str = "bản cài"

    @property
    def off(self) -> bool:
        return not self.model

    @property
    def provider(self) -> str:
        return self.model.split(":", 1)[0]

    @property
    def compat(self) -> bool:
        return self.provider == COMPAT

    @property
    def self_hosted(self) -> bool:
        return self.compat

    def fingerprint(self) -> str:
        """Phần cấu hình làm ĐỔI vector (khoá bộ nhớ câu tìm): model, số chiều, địa chỉ, cách cắt, tiền tố."""
        raw = json.dumps([self.model, self.dims, self.base_url, self.truncate, self.query_prefix, self.document_prefix])
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def same_vectors(self, other: Profile) -> bool:
        """Cùng model + cùng số chiều ⇒ vector đã lưu dùng chung được (địa chỉ máy chủ / giá không đổi vector)."""
        return self.model == other.model and self.dims == other.dims

    @property
    def threshold(self) -> float:
        return self.min_sim if self.min_sim is not None else settings.kb_vector_min_sim

    def describe(self) -> str:
        if self.off:
            return f"TẮT tìm theo nghĩa (nguồn: {self.source})"
        where = f" @ {self.base_url}" if self.base_url else ""
        extra = ", cắt Matryoshka" if self.truncate else ""
        price = (f", {self.usd_per_mtok:g} USD/triệu token" if self.usd_per_mtok else ", 0 USD (tự dựng)") if self.self_hosted else ""
        return f"{self.model}{where}, {self.dims} chiều{extra}{price} (nguồn: {self.source})"

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in KEYS}


def validate(raw: dict[str, Any]) -> dict[str, Any]:
    """Làm sạch MỘT PHẦN các khoá hồ sơ; sai ⇒ ``Invalid`` nêu rõ khoá. Kiểm chéo giữa các khoá (base_url bắt buộc với openai-compatible…) ở ``check``."""
    from barista.agent import llm

    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k == "model":
            spec = str(v or "").strip()
            if spec:
                if not re.match(r"^[a-z][a-z0-9-]*:[^\s:]+(:[^\s:]+)?$", spec):
                    raise Invalid(f"model “{spec}” không hợp lệ — dạng nhà_cung_cấp:tên_model, vd google:gemini-embedding-001 hoặc {COMPAT}:embeddinggemma")
                if spec.split(":", 1)[0] != COMPAT and not llm.known_provider(spec):
                    raise Invalid(f"nhà cung cấp “{spec.split(':', 1)[0]}” chưa biết — dùng google, openai, cohere, voyageai hoặc {COMPAT} (kèm base_url)")
            out[k] = spec
        elif k == "dims":
            if not isinstance(v, int) or isinstance(v, bool) or not 32 <= v <= MAX_DIMS:
                raise Invalid(f"dims: số nguyên 32–{MAX_DIMS} (chuẩn {STANDARD_DIMS})")
            out[k] = v
        elif k == "base_url":
            url = str(v or "").strip()
            if url and not re.match(r"^https?://[^\s]+$", url):
                raise Invalid("base_url: địa chỉ http:// hoặc https:// (vd http://127.0.0.1:11434/v1)")
            out[k] = url.rstrip("/")
        elif k == "key_ref":
            ref = str(v or "").strip()
            if ref and not re.match(r"^[A-Za-z0-9_.-]{1,60}$", ref):
                raise Invalid("key_ref: tên mục trong llm_keys của bản cài (chữ, số, gạch) — KHÔNG dán khoá vào đây")
            out[k] = ref
        elif k == "truncate":
            out[k] = v if isinstance(v, bool) else str(v).lower() in ("1", "yes", "on", "true", "co", "có")
        elif k in ("query_prefix", "document_prefix"):
            if not isinstance(v, str) or len(v) > 120:
                raise Invalid(f"{k}: chữ ≤ 120 ký tự")
            out[k] = v
        elif k == "usd_per_mtok":
            if v is not None and (isinstance(v, bool) or not isinstance(v, int | float) or not 0 <= v <= 1000):
                raise Invalid("usd_per_mtok: số 0–1000 (USD / triệu token) hoặc bỏ trống")
            out[k] = None if v is None else float(v)
        elif k == "min_sim":
            if v is not None and (isinstance(v, bool) or not isinstance(v, int | float) or not 0 < v < 1):
                raise Invalid("min_sim: số trong (0, 1) hoặc bỏ trống (dùng mặc định bản cài)")
            out[k] = None if v is None else float(v)
        else:
            raise Invalid(f"khoá “{k}” không thuộc hồ sơ nhúng")
    return out


def check(prof: Profile) -> Profile:
    """Kiểm chéo hồ sơ ĐÃ GỘP. Sai ⇒ ``Invalid`` với câu chỉ rõ cách sửa."""
    if prof.off:
        return prof
    if prof.compat and not prof.base_url:
        raise Invalid(f"{COMPAT}: cần base_url (máy chủ tự dựng — Ollama / vLLM / TEI)")
    if not prof.compat and prof.base_url:
        raise Invalid(f"base_url chỉ dùng với {COMPAT}:<model> — model {prof.model} đi thẳng tới nhà cung cấp của nó")
    if prof.truncate and not prof.compat:
        raise Invalid("truncate chỉ cho máy chủ tự dựng — google / openai tự trả đúng số chiều qua tham số dimensions")
    if (n := preset(prof.model).get("native")) and prof.dims > n:
        raise Invalid(f"{prof.model} chỉ có {n} chiều gốc — không thể nhúng {prof.dims} chiều")
    if (n := preset(prof.model).get("native")) and prof.dims < n and prof.compat and not prof.truncate:
        raise Invalid(f"{prof.model} trả {n} chiều gốc; muốn {prof.dims} chiều phải bật truncate (CHỈ khi model huấn luyện kiểu Matryoshka), "
                      f"nếu không hãy đặt dims={n} (cần chỉ mục cho {n} chiều: kb.vectors add-index {n})")
    return prof


def apply(prof: Profile, upd: dict[str, Any], source: str) -> Profile:
    return replace(prof, **upd, source=source) if upd else prof


def instance_profile() -> Profile:
    """Mặc định của bản cài: ``embed_model`` + ``embed_dims`` (+ ``embed_profile`` cho địa chỉ / khoá / tiền tố). Đọc MỖI lần (test đổi settings)."""
    base = Profile(model=settings.embed_model, dims=settings.embed_dims)
    return apply(base, validate(dict(settings.embed_profile or {})), "bản cài")


def compose(row: dict[str, Any] | None) -> Profile:
    """Hồ sơ đang áp: mặc định bản cài, rồi dòng riêng của công ty (đè NGUYÊN các khoá nó nêu; model đổi ⇒ không thừa hưởng địa chỉ / tiền tố của model cũ)."""
    inst = instance_profile()
    if not row:
        return inst
    if "model" in row and row["model"] != inst.model:   # model khác ⇒ các khoá đi kèm model của bản cài (địa chỉ, khoá, tiền tố, cắt) không còn đúng
        inst = Profile(model=inst.model, dims=inst.dims, source=inst.source)
    return apply(inst, validate(row), "công ty")


# ── Dòng riêng của công ty: nhớ ngắn hạn theo công ty (mỗi câu tìm không tốn thêm lượt DB); lệnh ghi xoá bản nhớ của công ty (tiến trình khác ≤ TTL_S).
# Chỉ nhớ DÒNG; mặc định bản cài ghép lại mỗi lần (đổi cấu hình bản cài có hiệu lực ngay). ──
_CACHE: dict[UUID, tuple[float, dict[str, Any] | None]] = {}
TTL_S = 30.0


def invalidate(tenant_id: UUID | None = None) -> None:
    if tenant_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(tenant_id, None)


def cached(tenant_id: UUID) -> Profile | None:
    """Chỉ đọc bản nhớ (không DB) — cho nơi đang giữ giao dịch (gọi lồng)."""
    hit = _CACHE.get(tenant_id)
    return compose(hit[1]) if hit and time.monotonic() - hit[0] < TTL_S else None


async def for_tenant(p: Principal) -> Profile:
    """Hồ sơ đang áp cho công ty của ``p`` (người đọc — quyền người đó; bản nhớ ngắn hạn)."""
    if (hit := cached(p.tenant_id)) is not None:
        return hit
    async with db.tenant_tx(p, read_only=True, wait=5) as c:
        cur = await c.execute("SELECT profile FROM core.embed_profiles")
        row = await cur.fetchone()
    data = dict(row["profile"]) if row else None
    _CACHE[p.tenant_id] = (time.monotonic(), data)
    return compose(data)


async def fresh(tenant_id: UUID) -> Profile:
    """Đọc LẠI từ DB (việc nền / vòng quét: mỗi lô kiểm lại, đổi model giữa chừng thì dừng đúng lúc) bằng quyền chủ DB — chỉ đọc đúng một dòng."""
    async with db.owner.connection() as conn:
        cur = await conn.execute("SELECT profile FROM core.embed_profiles WHERE tenant_id = %s", (tenant_id,))
        row = await cur.fetchone()
    data = dict(row["profile"]) if row else None
    _CACHE[tenant_id] = (time.monotonic(), data)
    return compose(data)
