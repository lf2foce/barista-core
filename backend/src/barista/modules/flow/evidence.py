"""BẰNG CHỨNG XONG (0117) — việc có BỘ KIỂM (``done_when``) chỉ đóng khi bộ kiểm báo đạt; sự kiện chỉ đánh thức, bộ kiểm quyết. Module ``flow``
(0088: package — ``m`` khai ở ``__init__``).

* Bộ kiểm cùng họ với mục hồ sơ (``registry.CHECKERS``, ``Module.checker``), đọc ĐỐI TƯỢNG của việc, chạy bằng quyền hệ thống (``consumes("check")``)
  như hồ sơ. Kết quả: đạt / mới một phần / chưa đạt / CHƯA KIỂM ĐƯỢC (gói tắt, bộ kiểm không còn) — chỉ "đạt" đóng việc.
* Đánh thức: ``subject.changed`` của module chủ đối tượng + vòng quét đối soát (sự kiện có thể lỡ). Mỗi lần kiểm khoá kế hoạch rồi khoá dòng việc
  (cùng thứ tự với sửa việc) và đọc lại trạng thái ⇒ hai lượt kiểm đồng thời chỉ đóng một lần, phát một sự kiện.
* Người không bấm "Xong" được (``set_task`` + DB chặn); kiểm lại ngay bằng ``recheck_task``; đóng không cần bằng chứng = NGOẠI LỆ có lý do, qua chuỗi
  duyệt ``task_override`` (dữ liệu), ghi người xin + người duyệt + lý do.
* Căn cứ MẤT sau khi đã đóng (trong ``RECHECK_DAYS``): việc TỰ MỞ LẠI, ghi mốc ``evidence_lost_at`` (đếm số lần đóng sai), báo người làm + người
  giao một lần cho lần mở lại đó. Có lại căn cứ ⇒ lượt kiểm sau đóng lại như thường. Không cờ / màn riêng cho "mất căn cứ".
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Literal, LiteralString
from uuid import UUID

from barista.api import Ctx, Denied, Event, Input, Invalid, NoInput, NotFound, field, zone
from barista.kernel import packs, registry
from barista.modules.flow import _task_open, m
from barista.modules.flow.tasks import CHECK_STATUS, TaskRef, _lock_plan, _task_for_change, may_see_check_value

log = logging.getLogger("barista.flow.evidence")
RECHECK_DAYS = 30           # việc đã đóng bằng bằng chứng còn được kiểm lại 30 ngày — sau đó là lịch sử, không báo mất căn cứ nữa
OPEN = ("todo", "doing", "blocked")
# việc cần kiểm: đang mở, hoặc vừa đóng bằng bằng chứng (còn trong cửa sổ kiểm lại), và KHÔNG thuộc kế hoạch đã nghiệm thu / huỷ (việc con của kế
# hoạch đóng không đổi nữa — để chúng trong danh sách thì vòng quét chọn lại mãi mà không làm gì). Dùng chung cho vòng quét và bên nghe sự kiện.
WATCHED: LiteralString = """done_check IS NOT NULL AND (status IN ('todo', 'doing', 'blocked')
                             OR (status = 'done' AND done_by = 'evidence' AND done_at > now() - interval '30 days'))
                         AND (plan_id IS NULL OR plan_id IN (SELECT p.id FROM flow.plans p WHERE p.status = 'open'))"""
BATCH = 50   # việc mỗi lượt quét — một giao dịch giữ tối đa chừng ấy khoá (kế hoạch + dòng việc), lấy theo một thứ tự cố định


def _label(check: str) -> str:
    return registry.CHECKERS.get(check, (check,))[0]


async def _run_checker(ctx: Ctx, t: dict[str, Any]) -> tuple[str, str]:
    """Hỏi bộ kiểm (quyền hệ thống, hợp đồng họ "check"). Gói của bộ kiểm tắt / bộ kiểm không còn ⇒ "chưa kiểm được", KHÔNG phải "chưa đạt"."""
    if registry.get(t["done_check"]) is None:
        return "unknown", ""
    try:
        async with ctx.conn.transaction():   # điểm lưu: một bộ kiểm lỗi không làm hỏng cả lượt quét
            got = await ctx.call_system(t["done_check"], subject_id=t["subject_id"], args=t["done_args"])
    except (packs.PackOff, Denied):   # PackOff là một NotFound — bắt TRƯỚC: gói tắt là "chưa kiểm được", không phải "chưa đạt"
        return "unknown", ""
    except NotFound:                  # đối tượng không còn ⇒ căn cứ không còn
        return "missing", ""
    except Exception:                 # bộ kiểm lỗi (dữ liệu / tham số cũ): chưa kiểm được, không đóng / mở việc
        log.exception("bộ kiểm %s của việc %s lỗi", t["done_check"], t["id"])
        return "unknown", ""
    status = got.get("status") if isinstance(got, dict) else None
    return (status if status in ("missing", "received", "verified") else "unknown"), str(got.get("value") or "")[:300] if isinstance(got, dict) else ""


async def _check(ctx: Ctx, task_id: UUID) -> dict[str, int]:
    ref = await ctx.one("SELECT plan_id FROM flow.tasks WHERE id = %s", (task_id,))
    if ref is None:
        return {}
    if ref["plan_id"]:
        await _lock_plan(ctx, ref["plan_id"])   # cùng thứ tự khoá với sửa việc / đóng kế hoạch
    t = await ctx.one("""SELECT t.*, p.status AS plan_status FROM flow.tasks t LEFT JOIN flow.plans p ON p.id = t.plan_id
                         WHERE t.id = %s FOR UPDATE OF t""", (task_id,))
    if t is None or not t["done_check"]:
        return {}
    is_open = t["status"] in OPEN
    watched = t["status"] == "done" and t["done_by"] == "evidence" and t["done_at"] > datetime.now(zone()) - timedelta(days=RECHECK_DAYS)
    if t["plan_status"] not in (None, "open") or not (is_open or watched):
        # kế hoạch đã đóng / huỷ (việc con không đổi — 0073) hoặc việc không còn cần theo dõi: vẫn đẩy mốc kiểm để không bị chọn lại mãi
        await ctx.exec("UPDATE flow.tasks SET checked_at = now() WHERE id = %s", (task_id,))
        return {}
    status, value = await _run_checker(ctx, t)
    await ctx.exec("UPDATE flow.tasks SET check_status = %s, check_value = %s, checked_at = now() WHERE id = %s", (status, value, task_id))
    label, see = _label(t["done_check"]), _task_open(task_id)
    if is_open and status == "verified":
        await ctx.exec("""UPDATE flow.tasks SET status = 'done', done_by = 'evidence', done_at = now(), blocked_reason = '', updated_at = now()
                          WHERE id = %s""", (task_id,))
        await ctx.notify({t["assignee_id"], t["created_by"]}, kind="task", title=f"Xong (có bằng chứng): {t['title']}", body=label, open=see)
        ctx.emit("flow.task_done", task_id=task_id, plan_id=t["plan_id"], done_by="evidence")
        return {"closed": 1}
    if watched and status in ("missing", "received"):
        # căn cứ mất ⇒ việc TỰ MỞ LẠI (tiến độ kế hoạch phản ánh ngay), ghi mốc mất căn cứ; MỘT thông báo cho người làm + người giao cho lần
        # mở lại này, không im lặng (0094). Có lại căn cứ ⇒ lượt kiểm sau đóng lại như thường.
        await ctx.exec("""UPDATE flow.tasks SET status = 'todo', done_at = NULL, evidence_lost_at = now(), updated_at = now() WHERE id = %s""",
                       (task_id,))
        await ctx.notify({t["assignee_id"], t["created_by"]}, kind="task", title=f"Mở lại vì căn cứ không còn đạt: {t['title']}",
                         body=f"“{label}” không còn đạt", push="Một việc đã đóng được mở lại vì căn cứ không còn đạt", open=see)
        ctx.emit("flow.task_reopened", task_id=task_id, plan_id=t["plan_id"], reason="evidence_lost")
        return {"reopened": 1}
    return {}


class CheckTaskIn(Input):
    task_id: UUID = field("Việc", hidden=True)


@m.command("check_task", resource=None, title="Kiểm bằng chứng của một việc", internal=True, agent="deny", callers=("flow",),
           why="Tạo việc, kiểm lại, bên nghe subject.changed của flow: chạy bộ kiểm của MỘT việc (quyền hệ thống — người kiểm lại có thể không thấy "
               "đối tượng) và đổi trạng thái theo kết quả. Trả số việc đã đóng / mất căn cứ / có lại căn cứ — không trả số đọc được của bộ kiểm.")
async def check_task(ctx: Ctx, inp: CheckTaskIn) -> dict[str, int]:
    return {"closed": 0, "reopened": 0, **await _check(ctx, inp.task_id)}


@m.command("check_tasks", resource=None, title="Kiểm lại việc chờ bằng chứng", internal=True, agent="deny",
           sweep="SELECT DISTINCT tenant_id FROM flow.tasks WHERE " + WATCHED
                 + " AND (checked_at IS NULL OR checked_at < now() - interval '30 minutes')")
async def check_tasks(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Vòng quét đối soát (quyền hệ thống): sự kiện có thể lỡ / gói không phát sự kiện ⇒ mỗi 30 phút kiểm lại việc chờ bằng chứng. Chọn việc ĐANG MỞ
    trước, rồi việc đã đóng bằng bằng chứng, cũ nhất trước, tối đa ``BATCH``; xử lý theo thứ tự khoá cố định (kế hoạch, rồi việc) như bên nghe sự
    kiện ⇒ hai lượt chồng nhau không khoá chéo. Lượt không đổi gì trả toàn 0 ⇒ không ghi nhật ký."""
    out = {"closed": 0, "reopened": 0}
    for r in await ctx.all("SELECT id FROM (SELECT id, plan_id FROM flow.tasks WHERE " + WATCHED
                           + " AND (checked_at IS NULL OR checked_at < now() - interval '30 minutes')"
                           + " ORDER BY (status = 'done'), checked_at NULLS FIRST LIMIT %s) x ORDER BY plan_id NULLS FIRST, id", (BATCH,)):
        # MỖI việc trong điểm lưu riêng: một việc kiểm lỗi (bộ kiểm của gói ném lỗi, dòng không ghi được…) chỉ hoàn phần của nó — không làm hỏng cả
        # lượt. Việc lỗi được đẩy mốc kiểm (xuống cuối hàng, 'unknown') ⇒ không đứng đầu hàng mãi chặn mọi việc khác của công ty (đã thấy 14/11).
        try:
            async with ctx.conn.transaction():
                got = await _check(ctx, r["id"])
        except Exception:
            log.exception("kiểm việc %s lỗi — bỏ qua lượt này, quét tiếp việc khác", r["id"])
            out["failed"] = out.get("failed", 0) + 1
            try:   # chính dòng đó có thể không ghi được (cùng lỗi) — vẫn quét tiếp; mỗi lượt xét mỗi việc một lần, BATCH việc khác vẫn được kiểm
                async with ctx.conn.transaction():
                    await ctx.exec("UPDATE flow.tasks SET check_status = 'unknown', checked_at = now() WHERE id = %s", (r["id"],))
            except Exception:
                log.warning("không đẩy được mốc kiểm của việc %s", r["id"])
            continue
        for k, v in got.items():
            out[k] += v
    return out


@m.on("subject.changed")
async def wake_checked_tasks(ctx: Ctx, event: Event) -> None:
    """Dữ kiện của đối tượng đổi ⇒ kiểm lại các việc chờ bằng chứng về ĐÚNG đối tượng đó (không quét cả công ty). Sự kiện không phải điều kiện xong.
    Cùng thứ tự khoá với vòng quét (kế hoạch, rồi việc)."""
    for r in await ctx.all("SELECT id FROM flow.tasks WHERE subject_kind = %s AND subject_id = %s AND " + WATCHED
                           + " ORDER BY plan_id NULLS FIRST, id LIMIT %s", (event.payload["kind"], event.payload["id"], BATCH)):
        await ctx.call_system("flow.check_task", task_id=r["id"])


@m.command("recheck_task", resource="flow", title="Kiểm tra lại bằng chứng", agent="allow",
           description="Chạy bộ kiểm của việc ngay bây giờ — đạt thì việc tự đóng. Không ghi gì khác; trợ lý được làm thẳng.")
async def recheck_task(ctx: Ctx, inp: TaskRef) -> dict[str, Any]:
    """Thay cho nút "Xong" của việc có bộ kiểm: người (hoặc trợ lý) nói "đã làm xong" ⇒ kiểm dữ kiện thật; chưa đạt thì nói thiếu gì."""
    t = await ctx.one("SELECT id, title, done_check, subject_kind, subject_id FROM flow.tasks WHERE id = %s", (inp.task_id,))
    if t is None:
        raise NotFound("Không thấy việc (hoặc bạn không được xem)")
    if not t["done_check"]:
        raise Invalid("Việc này không có bộ kiểm — người làm tự báo xong")
    got = await ctx.call_system("flow.check_task", task_id=t["id"])
    r = await ctx.one("SELECT status, check_status, check_value FROM flow.tasks WHERE id = %s", (t["id"],))
    assert r is not None
    sees = may_see_check_value(ctx, t["done_check"])   # cùng một luật với thẻ việc
    label = _label(t["done_check"])
    if got.get("closed"):
        state = "đạt — việc đã đóng"
    elif r["status"] == "done":
        state = "đạt (việc đã xong)"
    else:
        state = CHECK_STATUS.get(r["check_status"] or "unknown", "Chưa kiểm được").lower()
    return {"task_id": t["id"], "status": r["status"], "check_status": r["check_status"],
            "summary": f"“{t['title']}” — {label}: {state}" + (f" ({r['check_value']})" if sees and r["check_value"] else "")}


OUTCOME = {"done": "Đóng không cần bằng chứng", "cancelled": "Huỷ việc"}


class OverrideIn(Input):
    task_id: UUID = field("Việc", hidden=True)
    outcome: Literal["done", "cancelled"] = field("Xin", "done", options=OUTCOME)
    reason: str = field("Vì sao", kind="textarea", max_length=1000,
                        help="Người duyệt đọc lý do này; nó nằm lại trên việc. Vd “Khách ký giấy tay, hệ thống chưa có bản ghi — bản chụp ở trao đổi”")


def _overridable(t: dict[str, Any]) -> None:
    if not t["done_check"]:
        raise Invalid("Việc không có bộ kiểm — người làm tự báo xong / tự huỷ, không cần ngoại lệ")
    if t["status"] not in OPEN:
        raise Invalid("Việc đã đóng / đã huỷ — không cần ngoại lệ")


@m.command("request_override", resource="flow", title="Xin đóng / huỷ việc không cần bằng chứng", agent="deny",
           description="Việc có bộ kiểm mà dữ kiện không thể vào hệ thống, hoặc không cần làm nữa: ghi lý do, trưởng nhóm (chuỗi duyệt “Đóng việc "
                       "không cần bằng chứng”) duyệt thì việc đóng / huỷ, ghi rõ ai xin, ai duyệt, vì sao. Trợ lý không xin thay.")
async def request_override(ctx: Ctx, inp: OverrideIn) -> dict[str, Any]:
    t = await _task_for_change(ctx, inp.task_id)   # khoá + kế hoạch còn mở + RLS: chỉ người sửa được việc mới xin
    _overridable(t)
    reason = inp.reason.strip()
    if len(reason) < 10:
        raise Invalid("Ghi rõ vì sao (ít nhất một câu) — người duyệt đọc lý do này")
    aid = await ctx.request_approval(chain="task_override", command="flow.close_by_override",
                                     input={"task_id": str(t["id"]), "outcome": inp.outcome, "reason": reason},
                                     summary=f"{OUTCOME[inp.outcome]}: “{t['title']}” ({_label(t['done_check'])}) — {reason}")
    return {"task_id": t["id"], "approval_id": aid, "summary": f"Đã gửi xin duyệt — {OUTCOME[inp.outcome].lower()} “{t['title']}”"}


@m.command("close_by_override", resource=None, title="Đóng / huỷ việc ngoại lệ (sau duyệt)", internal=True, agent="deny")
async def close_by_override(ctx: Ctx, inp: OverrideIn) -> dict[str, Any]:
    """Lõi chạy sau khi phiếu ``task_override`` duyệt đủ (quyền hệ thống, ghi tên người duyệt). Kiểm lại việc còn cần ngoại lệ (có thể đã đóng bằng
    bằng chứng trong lúc chờ duyệt ⇒ phiếu thành lỗi kèm lý do, không ghi đè). DB kiểm lại: người duyệt khác người xin (mig 033)."""
    if not ctx.p.approved_by:
        raise Denied("Đóng / huỷ ngoại lệ chỉ chạy sau khi phiếu được duyệt")
    t = await _task_for_change(ctx, inp.task_id)
    _overridable(t)
    done = inp.outcome == "done"
    await ctx.exec("""UPDATE flow.tasks SET status = %s, done_by = CASE WHEN %s THEN 'override' END, done_at = CASE WHEN %s THEN now() END,
                          override_reason = %s, override_asked_by = %s, override_by = %s, blocked_reason = '', updated_at = now() WHERE id = %s""",
                   ("done" if done else "cancelled", done, done, inp.reason.strip(), ctx.p.user_id, list(ctx.p.approved_by), t["id"]))
    word = "Đóng" if done else "Huỷ"
    await ctx.notify({t["assignee_id"], t["created_by"], ctx.p.user_id}, kind="task", title=f"{word} ngoại lệ đã duyệt: {t['title']}",
                     body=inp.reason.strip(), open=_task_open(t["id"]))
    ctx.emit("flow.task_done" if done else "flow.task_cancelled", task_id=t["id"], plan_id=t["plan_id"], done_by="override")
    return {"task_id": t["id"], "summary": f"“{t['title']}” {word.lower()} ngoại lệ"}
