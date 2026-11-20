"""PHÁT HIỆN CỦA BÁO CÁO CHẠY SONG SONG (ADR 0230) — hợp đồng giữa bộ gom (``modules/findings.py``) và các module / gói đóng góp.

Module (hoặc gói) nào có thứ đáng báo cho ban giám đốc thì khai MỘT truy vấn nội bộ ``<module>.findings_report`` (``internal=True``, ``agent="deny"``,
``callers=("findings",)``, ``why=…`` — cùng khuôn ``<module>.health_report`` của Sức khoẻ dữ liệu). Đầu vào ``{date_from, date_to}`` (ngày giờ công ty, đã chọn
sẵn bởi bộ gom). Trả về::

    {"findings": [<phát hiện>, …],          # dựng bằng ``finding(...)`` / ``unmeasured(...)`` dưới đây (gói ngoài trả dict cùng khuôn)
     "context": [{"label", "value", "format", "hint"?}, …],   # vài số nền (chi bao nhiêu, lead bao nhiêu) cho đầu trang — tuỳ chọn
     "notes": ["…"]}                        # lưu ý chung về dữ liệu của module (vd kỳ đã bị cắt cho khớp độ phủ dữ liệu) — tuỳ chọn

Luật cứng (bộ gom ép, không tin người đóng góp):

* Mọi con số đến từ TRUY VẤN (bất biến 8) — chữ tóm tắt do mã dựng từ chính các số đó, không phải do model viết.
* ``measurable=False`` ⇒ KHÔNG có số (impact / metrics bị bỏ): hiện "chưa đo được — vì …", kèm "sẽ đo được khi …". KHÔNG BAO GIỜ hiện 0 cho thứ chưa đo được:
  số 0 đọc là "không có vấn đề". ``measurable=True`` mà số bằng 0 = ĐÃ KIỂM và không thấy.
* Mỗi phát hiện đo được nêu CÔNG THỨC (``formula``) và độ tin cậy (``confidence``) — người đọc biết ước tính từ đâu.
* Bấm vào con số mở các dòng gốc: ``drill`` = ``{"query": "<module>.finding_rows", "input": {...}}`` — truy vấn thường (quyền người xem), số dòng khớp con số.
* Chỉ ĐỌC: không ghi, không thông báo, không chia lead, không gọi ra ngoài (chế độ chỉ ghi nhận / lịch sử — 0185, 0207).
"""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

REPORT = "findings_report"
GROUPS = {"money": "Tiền quảng cáo", "leads": "Lead và người nhận", "fairness": "Chia lead có công bằng không", "process": "Quy trình bán hàng"}
CONFIDENCE = {"high": "Cao", "medium": "Vừa", "low": "Thấp"}
FORMATS = ("money", "number", "percent", "text", "date")


class Metric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    value: int | float | str | None
    format: Literal["money", "number", "percent", "text", "date"] = "number"
    hint: str = ""
    drill: dict[str, Any] | None = None   # ``ui.drill``: bấm số ⇒ các dòng gốc, tổng / số dòng KHỚP số này (tests/test_findings.py kiểm)


class Impact(BaseModel):
    """Tác động ước tính: tiền (VND) hoặc số lượng. ``label`` nói rõ đếm cái gì ("tiền quảng cáo đã chi mà không ra lead")."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["money", "count"]
    value: int | float = Field(ge=0)
    label: str
    drill: dict[str, Any] | None = None   # mở các dòng tạo nên con số này (``ui.drill``); trống = lấy ``drill`` của cả phát hiện


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    title: str = Field(min_length=3, max_length=140)   # tiếng Việt thường cho người điều hành — không mã, không tên bảng
    group: Literal["money", "leads", "fairness", "process"]
    measurable: bool
    summary: str = ""
    why_not: str = ""
    unlock: str = ""
    impact: Impact | None = None
    metrics: list[Metric] = []
    formula: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"
    caveats: list[str] = []
    drill: dict[str, Any] | None = None
    attention: bool | None = None   # có cần ban giám đốc chú ý không; trống ⇒ suy từ tác động > 0

    @property
    def needs_attention(self) -> bool:
        if not self.measurable:
            return False
        return self.attention if self.attention is not None else bool(self.impact and self.impact.value > 0)

    @model_validator(mode="after")
    def _honest(self) -> Finding:
        if self.measurable:
            if self.why_not or self.unlock:
                raise ValueError("phát hiện đo được không có why_not / unlock")
            if not self.formula and self.impact is not None:
                raise ValueError("có ước tính tác động thì phải nêu công thức")
        else:
            if self.impact is not None or self.metrics:
                raise ValueError("chưa đo được thì KHÔNG có số (không bao giờ hiện 0 cho thứ chưa đo được)")
            if not self.why_not.strip() or not self.unlock.strip():
                raise ValueError("chưa đo được phải nói vì sao + khi nào đo được")
        return self


def finding(key: str, title: str, group: str, *, summary: str, formula: str, impact: Impact | dict[str, Any] | None = None,
            metrics: list[dict[str, Any]] | None = None, confidence: str = "medium", caveats: list[str] | None = None,
            drill: dict[str, Any] | None = None, attention: bool | None = None) -> dict[str, Any]:
    """Phát hiện ĐO ĐƯỢC. ``impact`` / ``metrics`` là số từ truy vấn của chính module."""
    imp = impact.model_dump() if isinstance(impact, Impact) else impact
    return {"key": key, "title": title, "group": group, "measurable": True, "summary": summary, "formula": formula, "impact": imp,
            "metrics": metrics or [], "confidence": confidence, "caveats": caveats or [], "drill": drill, "attention": attention}


def unmeasured(key: str, title: str, group: str, *, why_not: str, unlock: str) -> dict[str, Any]:
    """Phát hiện CHƯA ĐO ĐƯỢC: không số, không 0."""
    return {"key": key, "title": title, "group": group, "measurable": False, "why_not": why_not, "unlock": unlock}


def metric(label: str, value: Any, fmt: str = "number", hint: str = "", drill: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"label": label, "value": value, "format": fmt, "hint": hint, "drill": drill}


def impact_money(value: int | float, label: str, drill: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"kind": "money", "value": round(value), "label": label, "drill": drill}


def impact_count(value: int, label: str, drill: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"kind": "count", "value": int(value), "label": label, "drill": drill}


def contributors(only: str | None = None) -> list[tuple[str, str, str]]:
    """[(khoá module, tên thao tác, tên module cho người đọc)] — mọi module / gói khai ``<module>.findings_report`` (hiện có trong sổ đăng ký, kể cả gói đang tắt: bên gọi gặp ``PackOff`` ⇒ "chưa kiểm")."""
    from barista.kernel import registry

    return [(op.module, op.name, registry.MODULES.get(op.module) or op.module) for op in registry.OPS.values()
            if op.name == f"{op.module}.{REPORT}" and (only is None or op.module == only)]


def parse(raw: Any) -> Finding | None:
    """Dict của người đóng góp ⇒ ``Finding`` đã kiểm; sai khuôn ⇒ None (bộ gom ghi một dòng cảnh báo, không làm hỏng cả báo cáo)."""
    try:
        return Finding.model_validate(raw)
    except ValidationError:
        return None


# ── Chữ cho người đọc (tiếng Việt, số kiểu Việt) — mã dựng từ SỐ của truy vấn ──

def n_text(n: int | float) -> str:
    """12345 ⇒ "12.345"."""
    return f"{round(n):,}".replace(",", ".")


def money_text(n: int | float) -> str:
    """Tiền gọn, giữ một chữ số lẻ: 90,5 triệu · 1,25 tỷ · 450.000 đ."""
    v = round(n)
    if v >= 10**9:
        return f"{v / 10**9:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " tỷ đồng"
    if v >= 10**6:
        return f"{v / 10**6:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " triệu đồng"
    return n_text(v) + " đồng"


def dec_text(x: int | float, digits: int = 1) -> str:
    """Số thập phân kiểu Việt: 15,4 (không dấu chấm thập phân)."""
    return f"{x:.{digits}f}".rstrip("0").rstrip(".").replace(".", ",") if digits else n_text(x)


def pct_text(part: int | float, whole: int | float) -> str:
    """"37%" — không chia cho 0 (không có mẫu số ⇒ chuỗi rỗng, bên gọi tự quyết)."""
    return f"{round(100 * part / whole)}%" if whole else ""


def day_text(d: date) -> str:
    return f"{d.day}/{d.month}/{d.year}"


def median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
