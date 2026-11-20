"""TỪ VỰNG CÔNG TY (quyết định 0124): code nói bằng KHOÁ KHÁI NIỆM trung tính (``offering``, ``deal``…), mỗi công ty có bảng khoá → nhãn riêng.

* Module khai khái niệm một lần (``Module.term(khoá, nhãn trung tính, nghĩa)``) và viết chuỗi người đọc thấy bằng DẤU NHÃN ``[[khoá]]`` (viết hoa
  chữ đầu — "Dự án") hoặc ``[[khoá|l]]`` (chữ thường giữa câu — "dự án"). Không module nào tự thay chữ.
* Bảng nhãn là DỮ LIỆU của công ty (``core.vocabulary``, quản trị sửa trong app). Gói mang theo nhãn mặc định của ngành mình
  (``Pack(vocab=...)``): bật gói ⇒ công ty có sẵn nhãn đó (``admin.install_defaults``, chỉ thêm phần chưa có).
* Dấu nhãn được đổi thành nhãn THẬT ở MỘT chỗ cho mỗi cửa ra: kết quả thao tác (executor), form / menu (``forms``, ``workspaces``), công cụ và lời dặn
  của trợ lý, mẫu nhập Excel. Ô nào quên ⇒ ra nhãn TRUNG TÍNH (không bao giờ lộ dấu ``[[…]]``). Dấu nhãn KHÔNG được ghi vào dữ liệu (thông báo, ghi chú,
  tài liệu gửi khách): nơi đó ghi nhãn đã đổi (``resolve``) hoặc không dùng dấu. Hai điểm chặn bảo đảm điều đó: kết quả GỌI LỒNG (``executor.invoke`` —
  tên đối tượng chảy sang luồng trao đổi, yêu cầu, hồ sơ) và ghi thông báo (``notify.send``). Chữ đã lưu là ẢNH CHỤP lúc ghi theo nhãn công ty lúc đó — đổi nhãn sau
  không sửa chữ đã lưu (tên luồng / thông báo cũ giữ chữ cũ).
* Chỉ đổi CHỮ HIỆN RA, không đổi khoá / tên cột / tên lệnh — trợ lý và API vẫn gọi bằng khoá cũ.
"""

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from barista.db import Conn

TOKEN = re.compile(r"\[\[([a-z][a-z0-9_]*)(\|l)?\]\]")
KEY = re.compile(r"^[a-z][a-z0-9_]*$")
MAX_LABEL = 40
TTL_S = 30.0   # sửa nhãn hiệu lực trên mọi tiến trình trong ≤ 30 giây (trong tiến trình đổi: ngay)


@dataclass(frozen=True)
class Term:
    """Một khái niệm có nhãn: ``label`` = chữ TRUNG TÍNH dùng khi công ty chưa đặt nhãn riêng; ``meaning`` = nghĩa cho quản trị / trợ lý (không chứa nhãn)."""
    key: str
    label: str
    meaning: str


TERMS: dict[str, Term] = {}
_cache: dict[UUID, tuple[float, dict[str, str]]] = {}


def declare(key: str, label: str, meaning: str) -> Term:
    if not KEY.match(key) or key in TERMS:
        raise ValueError(f"khái niệm {key} sai tên hoặc khai hai lần")
    if not clean(label) or len(label) > MAX_LABEL or "[[" in label:
        raise ValueError(f"khái niệm {key}: nhãn không hợp lệ")
    TERMS[key] = Term(key, label, meaning)
    return TERMS[key]


def clean(label: str) -> str:
    return " ".join(label.split())


FORBIDDEN = re.compile(r"[\x00-\x1f\x7f\"'`“”‘’]|\[\[|\]\]")


def problem(label: str) -> str:
    """Lý do một nhãn công ty KHÔNG hợp lệ ("" = được). Nhãn đi vào lời dặn của trợ lý và mọi màn: không ký tự điều khiển (NUL, xuống dòng), không dấu nháy
    (thoát khỏi câu dẫn trong lời dặn), không dấu nhãn; một cụm ngắn."""
    if FORBIDDEN.search(label):
        return "Nhãn không được có xuống dòng / ký tự điều khiển, dấu nháy hoặc dấu [[ ]] — chỉ chữ, số, khoảng trắng và dấu câu thường"
    if len(clean(label)) > MAX_LABEL:
        return f"Nhãn tối đa {MAX_LABEL} ký tự"
    return ""


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _low(s: str) -> str:
    return s if s.isupper() else s.lower()   # "VIP" giữ nguyên, "Dự Án" ⇒ "dự án"


def label(key: str, terms: Mapping[str, str] | None = None, *, lower: bool = False) -> str:
    """Nhãn của một khái niệm cho công ty có ``terms`` (None / thiếu khoá ⇒ nhãn trung tính)."""
    t = TERMS.get(key)
    if t is None:
        return key
    text = clean((terms or {}).get(key) or t.label)
    return _low(text) if lower else _cap(text)


def resolve(text: str, terms: Mapping[str, str] | None = None) -> str:
    """Đổi dấu nhãn trong MỘT chuỗi. Khoá lạ giữ nguyên (không đoán)."""
    if "[[" not in text:
        return text

    def sub(m: re.Match[str]) -> str:
        return label(m.group(1), terms, lower=bool(m.group(2))) if m.group(1) in TERMS else m.group(0)
    return TOKEN.sub(sub, text)


def apply(obj: Any, terms: Mapping[str, str] | None = None) -> Any:
    """Đổi dấu nhãn trong mọi chuỗi GIÁ TRỊ của một cấu trúc JSON (khoá của từ điển giữ nguyên — khoá là hợp đồng, không phải chữ hiện ra)."""
    if isinstance(obj, str):
        return resolve(obj, terms)
    if isinstance(obj, dict):
        return {k: apply(v, terms) for k, v in obj.items()}
    if isinstance(obj, list):
        return [apply(v, terms) for v in obj]
    if isinstance(obj, tuple):
        return tuple(apply(v, terms) for v in obj)
    return obj


def has_token(text: str) -> bool:
    return "[[" in text


async def terms(conn: Conn, tenant_id: UUID) -> dict[str, str]:
    """Nhãn RIÊNG của công ty (chỉ những khoá đã đặt và còn được khai) — bộ nhớ đệm ngắn như trạng thái gói."""
    hit = _cache.get(tenant_id)
    if hit and time.monotonic() - hit[0] < TTL_S:
        return hit[1]
    cur = await conn.execute("SELECT term, label FROM core.vocabulary WHERE tenant_id = %s", (tenant_id,))
    out = {r["term"]: r["label"] for r in await cur.fetchall() if r["term"] in TERMS}
    _cache[tenant_id] = (time.monotonic(), out)
    return out


def invalidate(tenant_id: UUID) -> None:
    _cache.pop(tenant_id, None)


def neutral_words() -> dict[str, str]:
    """Khoá → nhãn trung tính (cho test: công ty có nhãn riêng không được thấy các chữ này)."""
    return {k: t.label for k, t in TERMS.items()}
