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

import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from barista.kernel import registry
from barista.kernel.agent_policy import self_serve
from barista.kernel.forms import field_specs
from barista.kernel.text import vnd_text

if TYPE_CHECKING:
    from barista.kernel.executor import Ctx

UNKNOWN = {"ok": None, "how": ""}
EXTERNAL_DECISION_S = 300
# Mốc "đang chạy quá lâu ⇒ cần đối soát" = hạn gọi ngoài + khoảng đệm: mốc giờ ghi lúc chiếm thẻ (trước khi commit, trước khi đồng hồ hạn bắt đầu,
# cộng lệch giờ app/DB) nên phải QUA HẲN hạn thì lượt kia mới chắc đã kết thúc (xong, lỗi hoặc tiến trình chết) — người đối soát không chen vào lượt còn sống.
STALE_RUNNING_S = EXTERNAL_DECISION_S + 60
RECONCILIATION = "needs_reconciliation"
RECONCILIATION_LABEL = "Cần đối soát với hệ ngoài trước khi thực hiện lại"
# ĐỐI SOÁT (rà 22/11): thẻ cần đối soát có đường đóng — người xem hệ ngoài rồi chọn một trong hai. Server gửi nút (app chỉ vẽ), cùng cho thẻ chat lẫn thẻ sáng.
RECONCILED_DONE, RECONCILED_NOT_DONE = "reconcile_done", "reconcile_not_done"
RESOLVE = [{"decision": RECONCILED_DONE, "label": "Đã thực hiện ở hệ ngoài"}, {"decision": RECONCILED_NOT_DONE, "label": "Chưa thực hiện"}]
RECONCILED_MESSAGE = {RECONCILED_DONE: "Đã đối soát: việc đã thực hiện ở hệ ngoài",
                      RECONCILED_NOT_DONE: "Đã đối soát: việc chưa thực hiện — muốn làm thì soạn đề xuất mới"}


def running_state(started_at: datetime | float | None) -> str:
    """Lượt quá hạn (kể cả khi tiến trình chết) cần đối soát, vẫn giữ chặn chạy lại."""
    start = started_at.timestamp() if isinstance(started_at, datetime) else started_at
    return "running" if start is not None and time.time() - start < STALE_RUNNING_S else RECONCILIATION


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


def action_rule(op: registry.Op, fixed: set[str]) -> dict[str, Any]:
    """Luật xác nhận áp cho MỘT NÚT (``fixed`` = ô nút đã cố định bằng ``bind`` / ``preset``): còn ô để điền ⇒ {} (form như thường); không còn ô nào ⇒
    ``{"instant": True}`` (đảo được: chạm là chạy, báo kết quả kèm Hoàn tác nếu có) hoặc ``{"confirm": hệ quả}`` (hộp nói hệ quả). Một chỗ cho nút của màn
    (``workspaces._action``) lẫn nút của thẻ (``ui.act``) — app chỉ vẽ."""
    if [f for f in field_specs(op) if not f["hidden"] and f["key"] not in fixed]:
        return {}
    kind, text = confirm_rule(op)
    return {"instant": True} if kind == "none" else {"confirm": text}


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
    ``running`` · ``needs_reconciliation`` (kèm ``resolve``: nút đối soát) · ``executed`` (đã làm) · ``sent`` (đã gửi duyệt — kèm ``approval_id``) · ``dismissed`` · ``expired`` · ``failed``."""
    if status == "done" and approval_id:
        return {"state": "sent", "approval_id": str(approval_id)}
    if status == RECONCILIATION:
        return {"state": RECONCILIATION, "resolve": RESOLVE}   # nút đối soát do server nói — app chỉ vẽ
    return {"state": {"open": "proposed", "done": "executed"}.get(status, status)}
