"""QUY TRÌNH & VIỆC — mẫu quy trình là dữ liệu có phiên bản, hồ sơ theo chặng, một bảng việc chung (quyết định 0043).

* **Mẫu** viết bằng chữ (người hay trợ lý viết được, đọc lại được), lưu kèm bản đã phân tích; sửa = thêm PHIÊN BẢN mới, hồ sơ đang mở giữ
  phiên bản lúc mở. Mỗi mục là "người xác nhận" hoặc một BỘ KIỂM do module ngành đăng ký (``registry.CHECKERS``) — lõi không biết ngành nào.
* **Chặng do code quyết**: chặng hiện tại = chặng đầu tiên còn mục BẮT BUỘC chưa đạt. Trả lại một mục ⇒ hồ sơ tự lùi về đúng chặng đó và sinh
  việc "bổ sung" cho người phụ trách. Vào chặng mới ⇒ sinh các việc mẫu khai cho chặng.
* **Hồ sơ bám đối tượng** (giao dịch, …) qua ``registry.SUBJECTS``: module phát ``subject.created`` / ``subject.changed``, flow tự mở / đồng bộ.
* **Một bảng việc** cho mọi việc của người: tự tạo, của đội (chưa người nhận), sinh từ chặng, bổ sung khi bị trả về. Xem dạng bảng kéo thả.
"""

import re
from typing import Any
from uuid import UUID

from barista.api import Ctx, Module, name_key, today

m = Module("flow", "Quy trình & việc", pack="base", resources={"flow": "Hồ sơ theo quy trình & việc", "flow.templates": "Mẫu quy trình",
                                                                  "flow.automations": "Luật tự động"}, setup=('flow.templates',))
m.consumes("subject", "check", "metric")   # hồ sơ đọc đối tượng + bộ kiểm của gói ngành, mục tiêu đọc chỉ số — bằng quyền hệ thống

ITEM_STATUS = {"missing": "Chưa có", "received": "Đã nhận", "verified": "Đạt", "rejected": "Bị trả lại"}
TASK_STATUS = {"todo": "Cần làm", "doing": "Đang làm", "blocked": "Kẹt", "done": "Xong", "cancelled": "Đã huỷ"}
TASK_TONE = {"todo": "muted", "doing": "accent", "blocked": "bad", "done": "ok", "cancelled": "muted"}
STAGE_TONE = {"done": "ok", "current": "accent", "todo": "muted", "stopped": "bad"}
TASKS_OPEN = {"query": "flow.tasks", "input": {}}
# Kế hoạch và việc là ĐỐI TƯỢNG (0117): trao đổi / cuộc gọi gắn vào, quyền thấy = quyền thấy kế hoạch / việc (hàm thấy-được ở DB, mig 033)
PLAN_SUBJECT, TASK_SUBJECT = "flow.plan", "flow.task"


def _plan_open(plan_id: Any) -> dict[str, Any]:
    return {"query": "flow.plan", "input": {"plan_id": str(plan_id)}}


def _case_open(case_id: Any) -> dict[str, Any]:
    return {"query": "flow.case", "input": {"case_id": str(case_id)}}


async def _team_id(ctx: Ctx, code: str) -> UUID | None:
    """Mã đội; gõ TÊN đội cũng nhận (bảng cho người / trợ lý đọc hiện tên — “Marketing”, “kinh doanh A”, “kda”): khớp mã trước, rồi khoá tên
    (không dấu, bỏ dấu cách / gạch — ``name_key``), bỏ chữ loại đội ở đầu CẢ chữ gõ lẫn tên đội (“phòng / đội / ban / team / nhóm / khối”) —
    “phòng kinh doanh A”, “kinh doanh A”, “Team Kinh doanh A” cùng trỏ một đội."""
    if not code.strip():
        return None
    rows = await ctx.all("SELECT id, code, name FROM core.teams")
    want = _bare(code)
    return next((r["id"] for r in rows if r["code"] == code), None) or next((r["id"] for r in rows if want in (name_key(r["code"]), _bare(r["name"]))), None)


def _bare(text: str) -> str:
    return name_key(re.sub(r"^\s*(phòng|phong|đội|doi|ban|team|nhóm|nhom|khối|khoi)\s+", "", text.strip(), flags=re.I))


def _task_item(t: dict[str, Any]) -> dict[str, Any]:
    late = t["due_on"] is not None and t["due_on"] < today() and t["status"] not in ("done", "cancelled")
    sub = " · ".join(x for x in [t.get("who") or "đội", f"hạn {t['due_on']:%d/%m}" if t["due_on"] else "",
                                  t["blocked_reason"] if t["status"] == "blocked" else "",
                                  "chờ bằng chứng" if t.get("done_check") and t["status"] in ("todo", "doing", "blocked") else ""] if x)
    # bấm một việc ⇒ thẻ CỦA VIỆC ĐÓ (đổi trạng thái, giao lại…); thẻ việc dẫn tiếp về hồ sơ / kế hoạch chứa nó
    return {"id": str(t["id"]), "title": t["title"], "subtitle": sub, "badge": TASK_STATUS[t["status"]],
            "tone": "bad" if late or t["status"] == "blocked" else TASK_TONE[t["status"]], "open": _task_open(t["id"])}


def _task_open(task_id: Any) -> dict[str, Any]:
    return {"query": "flow.task", "input": {"task_id": str(task_id)}}


async def _team_leads(ctx: Ctx, team: UUID) -> list[UUID]:
    rows = await ctx.all("""SELECT tm.user_id FROM core.team_members tm JOIN core.memberships mb ON mb.tenant_id = tm.tenant_id
                            AND mb.user_id = tm.user_id WHERE tm.team_id = %s AND tm.is_lead AND mb.active ORDER BY tm.user_id""", (team,))
    return [r["user_id"] for r in rows]


REQUESTS_OPEN = {"query": "flow.requests", "input": {}}


# Nạp các phần — đăng ký thao tác / bên nghe / màn hình vào registry. Cuối file vì các phần dùng ``m`` + hàm dùng chung ở trên.
from barista.modules.flow import (  # noqa: E402, F401
    assign,
    automations,
    cases,
    evidence,
    messages,
    plan_import,
    plans,
    requests,
    screens,
    tasks,
    timeline,
)
