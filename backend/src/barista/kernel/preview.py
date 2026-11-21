"""BẢN CHẠY THỬ CHUẨN (ADR 0253) — MỘT hợp đồng cho mọi thứ người phải DUYỆT: thẻ đề xuất ở chat, thẻ buổi sáng, phiếu duyệt, thông báo.

    {"title": "Đảo phiếu thu PT-0412 · 30 triệu",                         # đúng việc + hồ sơ + số tiền (nếu có)
     "subject": {"label": "Nguyễn Văn A · căn A-1203", "open": {query, input}} | None,   # hồ sơ nào (bấm mở)
     "changes": [{"label": "Hạn", "before": "30 tháng 11, 2026", "after": "5 tháng 12, 2026"}],   # đổi gì, từ gì thành gì
     "reason":  "Phiếu thu ghi nhầm…",                                   # vì sao (ngôn ngữ nghiệp vụ)
     "reversible": {"ok": True | False | None, "how": "…"}}             # đảo được không + cách

Nguồn của từng phần (một chỗ, không lệnh nào tự dựng thẻ):
  * title    — ``summary`` handler trả khi chạy thử (nếu có) hoặc tên lệnh, thêm hồ sơ và số tiền khi chưa có trong câu.
  * subject  — lệnh khai ``subject=(loại, ô)`` ⇒ lõi tra bằng ``m.subject`` của module sở hữu, QUYỀN NGƯỜI XEM (không thấy ⇒ không có nhãn); hoặc handler trả ``subject``.
  * changes  — mọi ``ctx.track`` lệnh gọi khi chạy thử (module khai lịch sử là có ngay) + ``changes`` handler trả thêm (trường chưa theo dõi).
  * reason   — nguồn gợi ý nói (lý do code tính của thẻ sáng / luật) thắng; không thì ``purpose=`` của lệnh.
  * reversible — khai trên lệnh (``irreversible`` / ``reversible`` / ``undone_by``) hoặc ``agent="allow"``; KHÔNG suy từ ``risk`` (máy từng suy sai).
Luật xác nhận của nút không ô nào (``confirm_rule``) cũng ở đây — ``forms.form_spec`` chỉ đọc, app chỉ vẽ.
"""

from typing import TYPE_CHECKING, Any

from barista.kernel import registry
from barista.kernel.agent_policy import self_serve
from barista.kernel.forms import field_specs
from barista.kernel.text import vnd_text

if TYPE_CHECKING:
    from barista.kernel.executor import Ctx

UNKNOWN = {"ok": None, "how": ""}


def reversibility(op: registry.Op) -> dict[str, Any]:
    """Đảo được không + cách — theo khai của lệnh, KHÔNG theo ``risk``."""
    if op.irreversible:
        return {"ok": False, "how": op.effect}
    if op.reversible:
        return {"ok": True, "how": op.reversible}
    if op.undone_by:
        return {"ok": True, "how": "Bấm Hoàn tác ở thẻ kết quả (trong 7 ngày), hoặc sửa lại bằng chính thao tác này."}
    if self_serve(op):
        return {"ok": True, "how": "Sửa lại được bằng chính thao tác này."}
    return dict(UNKNOWN)


def confirm_rule(op: registry.Op) -> tuple[str, str]:
    """LUẬT XÁC NHẬN MỘT CHỖ cho nút không còn ô nào để điền: ("none", "") = đảo được ⇒ chạy ngay khi chạm, báo kết quả (kèm Hoàn tác nếu có);
    ("consequence", chữ) = phá huỷ / không đảo được / chưa khai ⇒ hộp nói rõ HỆ QUẢ. Thẻ đề xuất không đi qua đây — luôn vẽ thẻ chuẩn."""
    r = reversibility(op)
    if r["ok"] is True:
        return "none", ""
    return "consequence", op.effect or f"Làm “{op.title}” ngay bây giờ."


async def build(ctx: Ctx, op: registry.Op, payload: dict[str, Any], out: Any, *, reason: str = "") -> dict[str, Any]:
    """Bản chạy thử chuẩn của MỘT lần chạy thử (``ctx`` = ngữ cảnh đã chạy handler trong điểm lưu, còn thấy dữ liệu chưa hoàn)."""
    from barista.kernel import history  # nạp muộn: history cần executor

    given: dict[str, Any] = out if isinstance(out, dict) else {}
    subject = await _subject(ctx, op, payload, given)
    changes = _given(given.get("changes")) + await history.describe(ctx)
    return {"title": _title(op, payload, given, subject), "subject": subject, "changes": changes,
            "reason": reason.strip() or op.purpose, "reversible": reversibility(op)}


async def _subject(ctx: Ctx, op: registry.Op, payload: dict[str, Any], given: dict[str, Any]) -> dict[str, Any] | None:
    if op.subject is not None and (kind := op.subject[0]) in registry.SUBJECTS and (sid := payload.get(op.subject[1])):
        try:
            got = await ctx.call_as(registry.SUBJECTS[kind][1], ctx.p, subject_id=sid)
        except Exception:   # không thấy / gói của đối tượng tắt ⇒ thẻ không có nhãn hồ sơ (không làm hỏng việc soạn thẻ)
            got = None
        if isinstance(got, dict) and got.get("title"):
            return {"label": str(got["title"]), "open": got.get("open")}
    s = given.get("subject")
    if isinstance(s, dict) and s.get("label"):
        return {"label": str(s["label"]), "open": s.get("open")}
    return None


def _given(raw: Any) -> list[dict[str, str]]:
    """``changes`` do handler trả: [{label, before, after}] — giá trị đã là chữ người đọc; thiếu ⇒ bỏ dòng."""
    out = []
    for c in raw if isinstance(raw, list) else []:
        if isinstance(c, dict) and c.get("label") and ("after" in c):
            out.append({"label": str(c["label"]), "before": "" if c.get("before") is None else str(c["before"]), "after": str(c["after"])})
    return out


def _title(op: registry.Op, payload: dict[str, Any], given: dict[str, Any], subject: dict[str, Any] | None) -> str:
    base = given["summary"].strip() if isinstance(given.get("summary"), str) and given["summary"].strip() else op.title
    if subject and subject["label"] not in base:
        base = f"{base} — {subject['label']}"
    amount = next((int(payload[f["key"]]) for f in field_specs(op) if f["kind"] == "money" and isinstance(payload.get(f["key"]), int | float)
                   and payload[f["key"]] > 0), None)
    if amount and vnd_text(amount) not in base and f"{amount:,}".replace(",", ".") not in base:
        base = f"{base} · {vnd_text(amount)}"
    return base


def state_of(status: str, approval_id: Any = None) -> dict[str, Any]:
    """TRẠNG THÁI "đã quyết" của một thẻ đề xuất, đọc từ SERVER (không giữ ở trình duyệt) — một từ vựng cho thẻ chat lẫn thẻ sáng: ``proposed`` (chờ) ·
    ``executed`` (đã làm) · ``sent`` (đã gửi duyệt — kèm ``approval_id`` để mở ĐÚNG phiếu) · ``dismissed`` · ``expired``."""
    if status == "done" and approval_id:
        return {"state": "sent", "approval_id": str(approval_id)}
    return {"state": {"open": "proposed", "done": "executed", "dismissed": "dismissed", "expired": "expired"}.get(status, status)}
