"""VIỆC — giao / nhận / đổi việc, bảng việc, nhắc hạn, lịch làm việc theo đội, xếp người nhận theo khối lượng. Module ``flow`` (0088: package — ``m`` khai ở ``__init__``)."""

import re
from datetime import date, datetime, timedelta
from typing import Any, Literal, LiteralString
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from barista.api import (
    Conflict,
    Ctx,
    DateIn,
    Day,
    Denied,
    Input,
    Invalid,
    Line,
    NoInput,
    NotFound,
    business_calendar,
    field,
    fold,
    history,
    kpi,
    line,
    notify,
    num,
    record_files,
    today,
    ui,
    zone,
)
from barista.kernel import packs, registry
from barista.modules.flow import (
    TASK_STATUS,
    TASK_SUBJECT,
    TASK_TONE,
    TASKS_OPEN,
    _case_open,
    _plan_open,
    _task_item,
    _task_open,
    _team_id,
    m,
)
from barista.modules.flow.cases import _active_template, check_args, check_fits
from barista.modules.flow.schedule_rules import check_schedule, fit_start

REMIND_FROM_HOUR = 8   # nhắc việc đến hạn từ 8 giờ sáng (giờ công ty)


class TaskRef(Input):
    task_id: UUID = field("Việc", hidden=True)


# Lịch sử thay đổi theo trường (ADR 0246): "ai đổi hạn / giao lại việc này"
m.tracks(TASK_SUBJECT, "assignee", "Người làm", fmt="person", empty="Đội (chưa ai nhận)")
m.tracks(TASK_SUBJECT, "due_on", "Hạn", fmt="date", empty="không hạn")
m.record_files(TASK_SUBJECT, labels=("Kết quả", "Bằng chứng", "Tài liệu tham khảo"))   # tệp của việc (ADR 0249)


@m.subject(TASK_SUBJECT, "Việc")
async def subject_task(ctx: Ctx, inp: registry.SubjectRef) -> dict[str, Any]:
    """Việc như một ĐỐI TƯỢNG (trao đổi / cuộc gọi gắn vào — 0117). Gọi bằng quyền người hỏi ⇒ không thấy việc thì NotFound. Người phụ trách
    đối tượng = người làm (chưa ai nhận ⇒ đội)."""
    t = await ctx.one("SELECT id, title, assignee_id, team_id, status FROM flow.tasks WHERE id = %s", (inp.subject_id,))
    if t is None:
        raise NotFound("Không thấy việc")
    return {"title": t["title"], "owner_id": t["assignee_id"], "team_id": t["team_id"],
            "state": {"done": "done", "cancelled": "cancelled"}.get(t["status"], "active"), "open": _task_open(t["id"])}


# ── Việc trỏ tới đối tượng nghiệp vụ (0117) ──
# Đối tượng của gói bất kỳ đã khai ``@m.subject`` (khách, giao dịch, kỳ sổ…). Lưu kiểu source / source_ref, không chép tên / quyền: người giao phải
# THẤY đối tượng lúc giao; người mở việc thấy đối tượng qua MODULE CHỦ bằng quyền của chính mình (không thấy ⇒ chỉ biết loại, không biết là gì).
# Hồ sơ / kế hoạch đã có cột riêng (case_id / plan_id); việc không trỏ việc.
OWN_KINDS = ("flow.case", "flow.plan", "flow.task")


async def _subject_as(ctx: Ctx, kind: str, sid: UUID) -> dict[str, Any] | None:
    """Đối tượng qua module chủ, bằng QUYỀN NGƯỜI ĐANG GỌI. Không thấy / gói tắt / không còn ⇒ None."""
    if kind not in registry.SUBJECTS:
        return None
    try:
        out: dict[str, Any] | None = await ctx.call_as_optional(registry.SUBJECTS[kind][1], ctx.p, subject_id=sid)
    except NotFound:
        return None
    return out


async def _check_subject(ctx: Ctx, kind: str, sid: UUID | None) -> dict[str, Any] | None:
    """Kiểm đối tượng gắn vào việc lúc GIAO: loại đã đăng ký, không phải hồ sơ / kế hoạch / việc (đã có cột riêng), người giao thấy được."""
    if not kind and sid is None:
        return None
    if not kind or sid is None:
        raise Invalid("Gắn đối tượng cần cả loại lẫn mã")
    if kind in OWN_KINDS:
        raise Invalid("Hồ sơ / kế hoạch gắn bằng mục riêng (case_id / plan_id); việc không gắn vào việc")
    if kind not in registry.SUBJECTS:
        raise Invalid(f"Không có loại đối tượng “{kind}”")
    subj = await _subject_as(ctx, kind, sid)
    if subj is None:
        raise NotFound(f"Không thấy {registry.SUBJECTS[kind][0].lower()} này (hoặc bạn không được xem, hoặc gói đang tắt)")
    return subj


@m.query("task", resource="flow", title="Việc")
async def task(ctx: Ctx, inp: TaskRef) -> dict[str, Any]:
    """Một việc: trạng thái, hạn, người làm, ghi chú; nút đổi trạng thái / giao lại theo quyền người xem; dẫn về hồ sơ / kế hoạch chứa nó."""
    t = await ctx.one("""SELECT t.*, u.name AS who, tm.name AS team, c.title AS case_title, pl.title AS plan_title, cb.name AS by_name
                         FROM flow.tasks t LEFT JOIN core.users u ON u.id = t.assignee_id LEFT JOIN core.teams tm ON tm.id = t.team_id
                         LEFT JOIN flow.cases c ON c.id = t.case_id LEFT JOIN flow.plans pl ON pl.id = t.plan_id
                         LEFT JOIN core.users cb ON cb.id = t.created_by WHERE t.id = %s""", (inp.task_id,))
    if t is None:
        raise NotFound("Không thấy việc (hoặc bạn không được xem)")
    late = t["due_on"] is not None and t["due_on"] < today() and t["status"] not in ("done", "cancelled")
    blocks: list[dict[str, Any]] = [ui.b_kpis([
        ui.kpi("Trạng thái", TASK_STATUS[t["status"]], "text", tone="red" if late or t["status"] == "blocked" else None),
        ui.kpi("Hạn", f"{t['due_on']:%d/%m/%Y}" if t["due_on"] else "—", "text", tone="red" if late else None),
        ui.kpi("Người làm", t["who"] or "Chưa ai nhận", "text"), ui.kpi("Đội", t["team"] or "—", "text")])]
    subj = await _subject_as(ctx, t["subject_kind"], t["subject_id"]) if t["subject_kind"] else None
    if t["done_check"]:
        blocks.append(await _done_when_block(ctx, t))
    if t["status"] == "blocked" and t["blocked_reason"]:
        blocks.append(ui.b_callout("bad", "Đang kẹt", t["blocked_reason"]))
    if t["notes"]:
        blocks.append(ui.b_text(t["notes"], "Ghi chú"))
    ref = {"task_id": str(t["id"])}
    acts: list[dict[str, Any]] = []
    if ctx.p.can("flow", "write"):
        if t["status"] in ("done", "cancelled"):
            acts.append(ui.act("flow.set_task", "Mở lại", {**ref, "status": "todo"}))
        elif t["assignee_id"] is None:
            acts.append(ui.act("flow.set_task", "Nhận việc", {**ref, "status": "doing", "take": True}, "primary"))
        else:
            if t["status"] == "todo":
                acts.append(ui.act("flow.set_task", "Bắt đầu làm", {**ref, "status": "doing"}))
            # việc có bộ kiểm: KHÔNG có nút "Xong" — kiểm lại bằng dữ kiện, hoặc xin ngoại lệ có lý do qua duyệt (0117)
            done = (ui.act("flow.recheck_task", "Kiểm tra lại", ref, "primary") if t["done_check"]
                    else ui.act("flow.set_task", "Xong", {**ref, "status": "done"}, "primary"))
            acts += [done, *([ui.act("flow.set_task", "Báo kẹt", {**ref, "status": "blocked"})] if t["status"] != "blocked" else []),
                     *([ui.act("flow.request_override", "Xin đóng / huỷ ngoại lệ", ref)] if t["done_check"] else []),
                     ui.act("flow.adjust_task", "Giao lại / dời hạn", ref)]
    opens = ([{"title": f"Hồ sơ: {t['case_title']}", **_case_open(t["case_id"])}] if t["case_id"] else []) + \
            ([{"title": f"Kế hoạch: {t['plan_title']}", **_plan_open(t["plan_id"])}] if t["plan_id"] else [])
    if t["subject_kind"]:   # đối tượng nghiệp vụ: qua module chủ, bằng quyền NGƯỜI XEM — không thấy thì chỉ biết loại
        label = registry.SUBJECTS.get(t["subject_kind"], ("Đối tượng",))[0]
        if subj is None:
            blocks.append(ui.b_callout("muted", f"Gắn với một {label.lower()}", "Bạn chưa được xem đối tượng này, hoặc gói của nó đang tắt."))
        elif subj.get("open"):
            opens.insert(0, {"title": f"{label}: {subj['title']}", **subj["open"]})
    if t["source_kind"] == "talk.message":   # việc tạo từ một tin: dẫn về đúng tin — chỉ khi người xem đọc được trao đổi đó (quyền của họ)
        try:
            src = await ctx.call_as_optional("talk.message_ref", ctx.p, message_id=int(t["source_ref"]))
        except NotFound:
            src = None
        if src:
            opens.append({"title": f"Tin nhắn gốc ({src['author']})", **src["open"]})
    if talk := await ctx.call_as_optional("talk.block", ctx.p, subject_kind=TASK_SUBJECT, subject_id=t["id"]):   # gói Trao đổi (0046, 0117)
        blocks.append(talk)
    if kept := await record_files.block(ctx, TASK_SUBJECT, t["id"]):   # tệp của việc (ADR 0249)
        blocks.append(kept)
    if changes := await history.block(ctx, TASK_SUBJECT, t["id"]):   # lịch sử thay đổi theo trường (ADR 0246)
        blocks.append(changes)
    by = f"Giao bởi {t['by_name']} · " if t["by_name"] else ""
    return ui.card(t["title"], blocks, subtitle=f"{by}tạo {t['created_at'].astimezone(zone()):%d/%m/%Y}", icon="checklist", actions=acts, open=opens,
                   ask={"title": "Hỏi trợ lý về việc này", "prompt": f"Việc “{t['title']}”: "})


CHECK_STATUS = {"verified": "Đạt", "received": "Mới có một phần", "missing": "Chưa đạt", "unknown": "Chưa kiểm được"}
DONE_BY = {"self": "Người làm tự báo xong", "evidence": "Đóng bằng bằng chứng", "override": "Đóng ngoại lệ (đã duyệt)"}


def _checker_resources(check: str) -> list[str]:
    op = registry.get(check)
    return [r for r, mod in registry.RESOURCE_MODULE.items() if op is not None and mod == op.module]


def may_see_check_value(ctx: Ctx, check: str) -> bool:
    """MỘT luật cho mọi chỗ hiện số đọc từ bộ kiểm (thẻ việc, kiểm tra lại): bộ kiểm chạy bằng quyền hệ thống nên con số (tiền khách đã đóng, phí đã thu…)
    là số của MODULE CHỦ bộ kiểm ⇒ chỉ người có phạm vi CẢ CÔNG TY trên một tài nguyên của module đó thấy — như mục ``[riêng: …]`` của hồ sơ.
    Thấy đối tượng (của chính mình) chưa đủ: ai không xem được số tiền của công ty trên hồ sơ thì cũng không xem được ở đây."""
    return any(ctx.p.has_scope(r, "all") for r in _checker_resources(check))


def checker_usable(ctx: Ctx, check: str) -> bool:
    """Người này gắn được bộ kiểm: gói của nó đang bật VÀ có quyền đọc một tài nguyên của module chủ bộ kiểm."""
    op = registry.get(check)
    if op is None or (ctx.p.packs is not None and packs.missing(ctx.p.packs, op) is not None):
        return False
    return any(ctx.p.can(r, "read") for r in _checker_resources(check))


async def _done_when_block(ctx: Ctx, t: dict[str, Any]) -> dict[str, Any]:
    """Ô "Xong khi" của thẻ việc: bộ kiểm, kết quả lần kiểm gần nhất, ai / cái gì đã đóng. Con số đọc từ bộ kiểm theo ``may_see_check_value``."""
    label = registry.CHECKERS.get(t["done_check"] or "", (t["done_check"] or "",))[0]
    facts: list[str] = []
    if t["check_value"] and may_see_check_value(ctx, t["done_check"]):
        facts.append(t["check_value"])
    if t["checked_at"]:
        facts.append(f"Kiểm lần cuối {t['checked_at'].astimezone(zone()):%H:%M %d/%m}")
    if t["override_asked_by"]:   # đóng HOẶC huỷ qua phiếu duyệt
        names = [r["name"] for r in await ctx.all("SELECT name FROM core.users WHERE id = ANY(%s) ORDER BY name", (list(t["override_by"]),))]
        asker = await ctx.val("SELECT name FROM core.users WHERE id = %s", (t["override_asked_by"],)) if t["override_asked_by"] else None
        facts.append(f"Ngoại lệ: {t['override_reason']}" + (f" — {asker} xin" if asker else "") + (f", {', '.join(names)} duyệt" if names else ""))
    if t["evidence_lost_at"] is not None and t["status"] != "done":
        facts.append(f"Đã mở lại {t['evidence_lost_at'].astimezone(zone()):%d/%m} vì căn cứ không còn đạt")
    if t["status"] == "done":
        return ui.b_callout("ok", DONE_BY[t["done_by"]], f"Xong khi: {label}" if label else "", facts=facts)
    if t["status"] == "cancelled":
        return ui.b_callout("muted", "Huỷ qua duyệt" if t["override_asked_by"] else "Đã huỷ", f"Xong khi: {label}" if label else "", facts=facts)
    state = CHECK_STATUS.get(t["check_status"] or "unknown", "Chưa kiểm được")
    return ui.b_callout("warn" if t["check_status"] in ("missing", "received") else "muted", f"Xong khi: {label}",
                        f"{state} — việc tự đóng khi dữ kiện đạt, không bấm “Xong” tay.", facts=facts)


class BoardIn(Input):
    key: str = field("Quy trình", "", options_query="flow.template_options_all", placeholder="Quy trình đầu tiên")


@m.query("template_options_all", resource=None, title="Quy trình")
async def template_options_all(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all("SELECT key AS value, name AS label FROM flow.templates WHERE is_active ORDER BY name"))


@m.query("board", resource="flow", title="Bảng hồ sơ theo chặng")
async def board(ctx: Ctx, inp: BoardIn) -> dict[str, Any]:
    """Kanban: mỗi cột một chặng, mỗi thẻ một hồ sơ (RLS: đúng phạm vi người xem). Không kéo thả — hồ sơ chuyển chặng khi đủ mục (code quyết)."""
    key = inp.key or await ctx.val("SELECT key FROM flow.templates WHERE is_active ORDER BY name LIMIT 1")
    if key is None:
        return ui.card("Hồ sơ theo chặng", [ui.b_callout("muted", "Chưa có quy trình nào", "Quản trị thêm ở Mẫu quy trình, hoặc nhờ trợ lý soạn.")],
                       icon="rectangle.split.3x1")
    tpl = await _active_template(ctx, key)
    cases = await ctx.all(
        """SELECT c.*, u.name AS owner, t.stages, t.version FROM flow.cases c JOIN flow.templates t ON t.id = c.template_id
           LEFT JOIN core.users u ON u.id = c.owner_id
           WHERE t.key = %s AND (c.status = 'open' OR (c.status = 'done' AND c.closed_at > now() - interval '14 days'))
           ORDER BY c.stage_since LIMIT 500""", (key,))
    now = datetime.now(zone())
    cols: list[dict[str, Any]] = []
    for st in tpl["stages"]:
        items = [c for c in cases if c["status"] == "open" and c["stage_key"] == st["key"]]
        cols.append({"key": st["key"], "title": st["name"], "subtitle": st["owner"], "tone": "accent",
                     "items": [_case_item(c, now) for c in items]})
    # hồ sơ ghim phiên bản cũ đang ở chặng mà bản mới đã đổi tên / bỏ: vẫn hiện (cột riêng ghi rõ phiên bản), không biến khỏi bảng
    known = {st["key"] for st in tpl["stages"]}
    old: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for c in cases:
        if c["status"] == "open" and c["stage_key"] not in known:
            old.setdefault((c["version"], c["stage_key"]), []).append(c)
    for (ver, key_), group in sorted(old.items(), key=lambda kv: kv[0]):
        name = next((st["name"] for st in group[0]["stages"] if st["key"] == key_), key_)
        cols.append({"key": f"v{ver}:{key_}", "title": f"{name} (phiên bản {ver})", "subtitle": "phiên bản cũ của quy trình", "tone": "muted",
                     "items": [_case_item(c, now) for c in group]})
    cols.append({"key": "_done", "title": "Xong (14 ngày)", "subtitle": "", "tone": "ok",
                 "items": [_case_item(c, now) for c in cases if c["status"] == "done"]})
    # Ô đếm trên CẢ tập hồ sơ (cùng RLS), không phải 500 thẻ hiển thị — bảng chỉ vẽ 500 hồ sơ cũ nhất theo thời gian ở chặng
    n = await ctx.one("""SELECT count(*) FILTER (WHERE c.status = 'open') AS open_n,
                                count(*) FILTER (WHERE c.status = 'open' AND c.stage_since <= now() - interval '7 days') AS stuck,
                                count(*) FILTER (WHERE c.status = 'done' AND c.closed_at > now() - interval '14 days') AS done_n
                         FROM flow.cases c JOIN flow.templates t ON t.id = c.template_id WHERE t.key = %s""", (key,))
    open_n, stuck, done_n = int((n or {}).get("open_n") or 0), int((n or {}).get("stuck") or 0), int((n or {}).get("done_n") or 0)
    return ui.card(tpl["name"], [
        ui.b_kpis([ui.kpi("Đang mở", open_n), ui.kpi("Đứng một chặng ≥ 7 ngày", stuck, tone="orange" if stuck else None),
                   ui.kpi("Xong 14 ngày", done_n)]),
        {"type": "board", "title": "", "columns": cols, "move": None},
    ], subtitle=f"Phiên bản {tpl['version']} · thẻ chuyển cột khi đủ mục của chặng", icon="rectangle.split.3x1",
        actions=[ui.act("flow.open_case", "Mở hồ sơ", {"key": tpl["key"]}, "primary")] if not tpl["subject_kind"] and ctx.p.can("flow", "write") else [],
        open=[{"title": "Xem mẫu quy trình", "query": "flow.template", "input": {"key": tpl["key"]}}] if ctx.p.can("flow.templates", "read") else [])


def _case_item(c: dict[str, Any], now: datetime) -> dict[str, Any]:
    days = (now - c["stage_since"]).days
    return {"id": str(c["id"]), "title": c["title"], "subtitle": " · ".join(x for x in [c["owner"] or "", f"{days} ngày ở chặng" if days else "mới vào chặng"] if x),
            "tone": "bad" if days >= 7 and c["status"] == "open" else ("ok" if c["status"] == "done" else "accent"),
            "open": {"query": "flow.case", "input": {"case_id": str(c["id"])}}}


# ── Việc ──

async def _lock_plan(ctx: Ctx, plan_id: UUID) -> None:
    """Đóng / mở kế hoạch và sửa việc con dùng cùng khoá, lấy trước khoá dòng việc. Không thay quyền RLS của người gọi."""
    await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"flow.plan:{plan_id}",))


def _unchanged_since(t: dict[str, Any], since: datetime | None) -> None:
    """Khoá của Hoàn tác (0144): người bấm hoàn tác một việc đã được sửa sau lần trợ lý làm ⇒ từ chối, không đè lên thay đổi mới hơn."""
    if since is not None and t["updated_at"] > since:
        raise Conflict(f"“{t['title']}” đã được sửa sau đó ({t['updated_at'].astimezone(zone()):%H:%M %d/%m}) — không hoàn tác đè lên thay đổi mới. Mở việc để xem.")


async def _task_for_change(ctx: Ctx, task_id: UUID) -> dict[str, Any]:
    ref = await ctx.one("SELECT plan_id FROM flow.tasks WHERE id = %s", (task_id,))
    if ref is None:
        raise NotFound("Không thấy việc")
    if ref["plan_id"]:
        await _lock_plan(ctx, ref["plan_id"])
        plan = await ctx.one("SELECT status FROM flow.plans WHERE id = %s", (ref["plan_id"],))
        if plan is None or plan["status"] != "open":
            raise Invalid("Kế hoạch đã đóng / huỷ — người phụ trách cần mở lại trước khi sửa việc")
    task = await ctx.one("""SELECT t.*, p.due_on AS plan_due FROM flow.tasks t LEFT JOIN flow.plans p ON p.id = t.plan_id
                            WHERE t.id = %s FOR UPDATE OF t""", (task_id,))
    if task is None:
        raise NotFound("Không thấy việc")
    return task


class CreateTask(Input):
    title: str = field("Việc", max_length=300, example="Gọi lại chị Lan chốt lịch gặp")
    notes: str = field("Ghi chú", "", kind="textarea", max_length=4000)
    assignee_email: str = field("Giao cho", "", options_query="admin.member_options", placeholder="Chính bạn (hoặc đội nếu chọn đội)")
    team_code: str = field("Đội", "", options_query="admin.team_options", help="Giao cho cả đội (chưa ai nhận) — chọn đội, để trống người", group="Giao cho cả đội")
    due_on: Day | None = field("Hạn", None)
    case_id: UUID | None = field("Hồ sơ", None, hidden=True)
    plan_id: UUID | None = field("Kế hoạch", None, hidden=True)
    start_on: Day | None = field("Bắt đầu", None, placeholder="Hôm nay", help="Dòng thời gian của kế hoạch vẽ thanh từ ngày này tới hạn", group="Lịch trong kế hoạch")
    phase: str = field("Giai đoạn", "", max_length=60, help="Nhãn nhóm trên dòng thời gian của kế hoạch (vd “Chuẩn bị”)", group="Lịch trong kế hoạch")
    subject_kind: str = field("Loại đối tượng", "", hidden=True)
    subject_id: UUID | None = field("Đối tượng", None, hidden=True)
    # người gắn trên form; trợ lý không thấy (schema nhỏ — 0095; bộ kiểm hiếm, gắn là việc của người giao)
    done_check: str = field("Xong khi", "", options_query="flow.checker_options", help="Việc tự đóng khi điều kiện này đạt", agent=False, group="Tự đóng việc khi đạt điều kiện")
    done_args: str = field("Điều kiện kèm theo", "", max_length=200, help="Vd seq=1", agent=False, group="Tự đóng việc khi đạt điều kiện")


async def _done_when(ctx: Ctx, check: str, args: str, kind: str, sid: UUID | None, *, by_module: bool = False) -> tuple[str | None, dict[str, Any]]:
    """``done_when`` của một việc (0117): bộ kiểm đã đăng ký (cùng họ với mục hồ sơ), đúng loại đối tượng việc đang gắn, người này dùng được;
    tham số cùng cú pháp mẫu quy trình, chỉ tên tham số bộ kiểm khai, và bộ kiểm phải chạy được với tham số đó trên đúng đối tượng (chạy thử trong
    điểm lưu — sai kiểu ⇒ báo rõ lúc gắn, không thành lỗi hệ thống ở vòng quét). Không SQL, không tên sự kiện, không biểu thức tự do.
    ``by_module``: module sinh việc gắn bộ kiểm CỦA CHÍNH NÓ (``flow.module_task``, đã kiểm chủ bộ kiểm) — không hỏi quyền dùng bộ kiểm của người bấm."""
    check = check.strip()
    if not check:
        if args.strip():
            raise Invalid("Tham số bộ kiểm cần chọn bộ kiểm (“Xong khi”)")
        return None, {}
    if not kind:
        raise Invalid("“Xong khi” cần gắn việc vào đối tượng mà bộ kiểm đọc (vd hồ sơ, kỳ sổ)")
    check_fits(check, kind)
    label, _, hint = registry.CHECKERS[check]
    if not by_module and not checker_usable(ctx, check):
        raise Invalid(f"Bạn không dùng được bộ kiểm “{label}” (gói chưa bật, hoặc không có quyền với dữ liệu nó đọc)")
    parsed = check_args(args.split())
    allowed = set(re.findall(r"(\w+)=", hint))
    if extra := sorted(set(parsed) - allowed):
        raise Invalid(f"Bộ kiểm “{label}” không có tham số {', '.join(extra)}" + (f" — dùng {hint}" if hint else " — bộ kiểm này không nhận tham số"))
    try:
        async with ctx.conn.transaction():   # điểm lưu: lỗi trong bộ kiểm không làm hỏng giao dịch của lệnh
            await ctx.call_system(check, subject_id=sid, args=parsed)
    except (packs.PackOff, NotFound):
        pass   # gói tắt / đối tượng chưa có dữ kiện: không phải lỗi tham số
    except (Invalid, ValueError, TypeError, KeyError, ArithmeticError, psycopg.errors.DataError) as e:
        # tham số sai kiểu / thiếu / không đổi được số (int("abc"), Decimal, uuid cho DB) ⇒ Invalid chỉ rõ bộ kiểm + gợi ý; lỗi gốc nối ``from e``. Lỗi KHÁC (mất kết nối
        # DB, thiếu quyền, lỗi lập trình) KHÔNG phải do tham số ⇒ để nổi lên, không che thành "tham số không hợp lệ"
        raise Invalid(f"Tham số “{args.strip()}” không hợp lệ cho bộ kiểm “{label}”" + (f" — dùng {hint}" if hint else "")) from e
    return check, parsed


@m.query("checker_options", resource=None, title="Bộ kiểm")
async def checker_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Ô "Xong khi": bộ kiểm người này gắn được (``checker_usable``) — nhãn kèm loại đối tượng nó đọc và gợi ý tham số."""
    out = []
    for key, (label, kind, hint) in sorted(registry.CHECKERS.items()):
        if not checker_usable(ctx, key):
            continue
        what = registry.SUBJECTS.get(kind, (kind,))[0].lower()
        out.append({"value": key, "label": f"{label} ({what}{', ' + hint if hint else ''})"})
    return out


async def _assignee(ctx: Ctx, ref: str, team: UUID | None) -> UUID | None:
    """Người nhận theo THAM CHIẾU form gửi lên, giải ở server — MỘT chỗ cho mọi form giao việc (thêm việc, tạo việc từ tin nhắn). Hôm nay tham chiếu
    là email của một thành viên đang làm; trống = chính người giao, trừ khi giao cho cả đội (``None`` = hàng chờ đội). Thêm loại người nhận khác
    (vd trợ lý của chính người giao — ghi chú 0117 ngày 07/11) = thêm một nhánh ở đây + một dòng trong danh sách chọn, không đổi form."""
    if ref.strip():
        uid: UUID | None = await ctx.val("""SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()
                                            WHERE u.email = %s AND mb.active""", (ref.strip().lower(),))
        if uid is None:
            raise NotFound(f"Không có người {ref}")
        return uid
    return ctx.p.user_id if team is None else None


async def _new_task(ctx: Ctx, *, title: str, notes: str, assignee: UUID | None, team: UUID | None, due_on: date | None, case_id: UUID | None = None,
                    plan_id: UUID | None = None, subject_kind: str = "", subject_id: UUID | None = None, check: str | None = None,
                    args: dict[str, Any] | None = None, origin: str = "", source: tuple[str, str] | None = None, start_on: date | None = None,
                    phase: str = "") -> dict[str, Any]:
    """MỘT đường ghi việc mới cho mọi lối vào (form, tin nhắn, module sinh việc): đội mặc định của người nhận, hồ sơ / kế hoạch còn chạy, luật hạn
    (``_due``), kiểm bằng chứng ngay nếu có bộ kiểm, báo người nhận / đội. Bên gọi đã kiểm đối tượng + bộ kiểm. RLS của người gọi chặn ghi ngoài
    phạm vi. Trả mã việc, hạn đã dời, cảnh báo lịch, tên người nhận."""
    if assignee is not None and team is None:
        team = await ctx.home_team(assignee)
    if case_id and not await ctx.val("SELECT 1 FROM flow.cases WHERE id = %s", (case_id,)):
        raise NotFound("Không thấy hồ sơ")
    milestone = None
    if plan_id:
        await _lock_plan(ctx, plan_id)
        plan = await ctx.one("SELECT due_on FROM flow.plans WHERE id = %s AND status = 'open'", (plan_id,))
        if plan is None:
            raise NotFound("Không thấy kế hoạch đang chạy")
        milestone = plan["due_on"]
    due, _, warnings = await _due(ctx, due_on, team, milestone, "")
    start_on = fit_start(start_on, due_on, due)
    check_schedule(start_on, due, f"“{title}”: ")
    tid = await ctx.val(
        """INSERT INTO flow.tasks (title, notes, assignee_id, team_id, due_on, case_id, plan_id, stage_key, origin, created_by, subject_kind, subject_id,
                                   done_check, done_args, source_kind, source_ref, start_on, phase)
           VALUES (%s, %s, %s, %s, %s, %s, %s, (SELECT stage_key FROM flow.cases WHERE id = %s), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (title, notes, assignee, team, due, case_id, plan_id, case_id, origin or ("assistant" if ctx.p.via == "agent" else "manual"),
         ctx.p.user_id, subject_kind or None, subject_id, check, Jsonb(args or {}), source[0] if source else None, source[1] if source else None,
         start_on, phase.strip()))
    # kiểm ngay một lần: dữ kiện đã đạt sẵn ⇒ việc đóng bằng bằng chứng, tiến độ kế hoạch đúng ngay
    checked = await ctx.call_system("flow.check_task", task_id=tid) if check else {}
    who = await ctx.val("SELECT name FROM core.users WHERE id = %s", (assignee,)) if assignee else "đội"
    when = f", hạn {due:%d/%m}" if due else ""
    await ctx.notify([assignee] if assignee else await notify.team_members(ctx.conn, team), kind="task",
                     title=f"Việc mới: {title}" if assignee else f"Việc chung của đội: {title}", body=f"{ctx.p.name} giao{when}",
                     push=f"Anh/chị có việc mới{when}" if assignee else f"Đội có việc mới chưa ai nhận{when}",
                     open=_case_open(case_id) if case_id else (_plan_open(plan_id) if plan_id else TASKS_OPEN))
    return {"task_id": tid, "due": due, "when": when, "who": who, "warnings": warnings, "closed": bool(checked.get("closed"))}


# Thêm việc là thao tác LÕI của trợ lý (0095 tầng 1): mọi vai đều giao / tự ghi việc, câu nào cũng có thể kết thúc bằng "rồi tạo việc …" — không để
# nó phụ thuộc vào việc tìm theo câu hỏi hay trần dung lượng.
@m.command("create_task", resource="flow", title="Thêm việc", category="core", promotable=True, risk="low", target="task_id",   # 0143
           undone_by=("flow.set_task:status=cancelled",))
async def create_task(ctx: Ctx, inp: CreateTask) -> dict[str, Any]:
    """Giao cho người khác / đội cần quyền với phạm vi đó (RLS chặn ghi ngoài phạm vi). Trợ lý chỉ soạn đề xuất. Bộ kiểm KHÔNG bắt buộc: người
    tạo việc tuỳ chọn gắn trên form (trợ lý không thấy ô này); việc do module sinh ra gắn bộ kiểm của module qua ``flow.module_task``."""
    team: UUID | None = await _team_id(ctx, inp.team_code)
    if inp.team_code and team is None:
        raise NotFound(f"Không có đội {inp.team_code}")
    assignee = await _assignee(ctx, inp.assignee_email, team)
    subj = await _check_subject(ctx, inp.subject_kind, inp.subject_id)
    check, args = await _done_when(ctx, inp.done_check, inp.done_args, inp.subject_kind, inp.subject_id)
    made = await _new_task(ctx, title=inp.title, notes=inp.notes, assignee=assignee, team=team, due_on=inp.due_on, case_id=inp.case_id,
                           plan_id=inp.plan_id, subject_kind=inp.subject_kind, subject_id=inp.subject_id, check=check, args=args,
                           start_on=inp.start_on, phase=inp.phase)
    who = made["who"] if assignee else f"đội {inp.team_code}"
    # tên đối tượng chỉ vào câu tóm tắt cho NGƯỜI GIAO (đã thấy nó); thông báo cho người nhận không mang tên — họ mở việc, đối tượng hiện theo quyền họ
    about = f" — {registry.SUBJECTS[inp.subject_kind][0].lower()} {subj['title']}" if subj else ""
    stamp = await ctx.val("SELECT updated_at FROM flow.tasks WHERE id = %s", (made["task_id"],))
    return {"task_id": made["task_id"], "calendar_warnings": list(made["warnings"]),
            # HOÀN TÁC (0144): huỷ chính việc vừa tạo, chỉ khi chưa ai đụng vào nó
            "undo": {"command": "flow.set_task", "title": f"Hoàn tác: huỷ việc “{inp.title}”",
                     "input": {"task_id": str(made["task_id"]), "status": "cancelled", "if_unchanged_since": stamp.isoformat()}},
            "summary": f"Việc “{inp.title}”{about} → {who}{made['when']}"
                       + (f" · xong khi: {registry.CHECKERS[check][0]}" if check else "")
                       + (" — dữ kiện đã đạt, việc tự đóng" if made["closed"] else "") + _calendar_note(made["warnings"])}


class ModuleTask(Input):
    title: str = field("Việc", max_length=300)
    notes: str = field("Ghi chú", "", max_length=4000)
    assignee_id: UUID = field("Người làm")
    due_on: Day | None = field("Hạn", None)
    subject_kind: str = field("Loại đối tượng")
    subject_id: UUID = field("Đối tượng")
    done_check: str = field("Bộ kiểm", help="Bộ kiểm của CHÍNH module gọi, đọc đúng loại đối tượng")
    done_args: str = field("Tham số bộ kiểm", "", max_length=200, help="Cú pháp mẫu quy trình, vd seq=2")
    source_kind: str = field("Nguồn", help="<module gọi>.<loại> — một việc đang mở cho mỗi nguồn")
    source_ref: str = field("Mã nguồn", max_length=200)


OPEN_FROM_SOURCE: LiteralString = """SELECT id FROM flow.tasks WHERE origin = 'module' AND source_kind = %s AND source_ref = %s
                                     AND status IN ('todo', 'doing', 'blocked') ORDER BY created_at LIMIT 1"""


@m.command("module_task", resource=None, title="Việc do module sinh ra (gắn bộ kiểm của module)", internal=True, agent="deny", family="follow_up",
           why="Gói khai consumes(\"follow_up\"): module sinh việc cho người (vd việc thu một đợt tiền) gắn SẴN bộ kiểm của chính nó (0117, ghi chú 07/11): chỉ bộ kiểm + nguồn thuộc module "
               "gọi (owned_by), đúng loại đối tượng, tham số hợp lệ; người nhận là thành viên đang làm. Bên gọi đã kiểm người bấm thấy đối tượng "
               "(RLS của module đó). Trả mã việc + đã có sẵn chưa — không trả số đọc được của bộ kiểm."
               " [id: module gọi tự có — assignee_id là người phụ trách đọc từ dòng của chính module gọi]")
async def module_task(ctx: Ctx, inp: ModuleTask) -> dict[str, Any]:
    """Hợp đồng cho module sinh việc: việc người TẠO không bao giờ cần bộ kiểm (tuỳ chọn trên form); việc MODULE sinh ra mang bộ kiểm khớp với nó —
    người không phải chọn. Một nguồn một việc đang mở (bấm lại ⇒ trả việc cũ). Origin ``module``: không tính là việc người / trợ lý tạo."""
    if not ctx.caller:   # không qua call_system (vd call_as) ⇒ không biết module nào gắn bộ kiểm — owned_by sẽ để lọt
        raise Denied("flow.module_task chỉ nhận lời gọi của module qua hợp đồng hệ thống (call_system)")
    op = registry.get(inp.done_check)
    if op is None or inp.done_check not in registry.CHECKERS:
        raise Invalid(f"Không có bộ kiểm “{inp.done_check}”")
    ctx.owned_by(op.module, f"Bộ kiểm “{registry.CHECKERS[inp.done_check][0]}”")
    ctx.owned_by(inp.source_kind.split(".")[0], f"Nguồn {inp.source_kind}")
    if inp.subject_kind in OWN_KINDS or inp.subject_kind not in registry.SUBJECTS:
        raise Invalid(f"Không gắn được việc vào loại đối tượng “{inp.subject_kind}”")
    if old := await ctx.val(OPEN_FROM_SOURCE, (inp.source_kind, inp.source_ref)):
        return {"task_id": old, "existed": True}
    if not await ctx.val("SELECT 1 FROM core.memberships WHERE user_id = %s AND tenant_id = core.tenant() AND active", (inp.assignee_id,)):
        raise Invalid("Người nhận không còn làm trong công ty")
    check, args = await _done_when(ctx, inp.done_check, inp.done_args, inp.subject_kind, inp.subject_id, by_module=True)
    try:
        async with ctx.conn.transaction():   # điểm lưu: lượt song song đã tạo trước (chỉ mục duy nhất của mig 036) ⇒ trả việc của lượt đó
            made = await _new_task(ctx, title=inp.title, notes=inp.notes, assignee=inp.assignee_id, team=None, due_on=inp.due_on,
                                   subject_kind=inp.subject_kind, subject_id=inp.subject_id, check=check, args=args, origin="module",
                                   source=(inp.source_kind, inp.source_ref))
    except psycopg.errors.UniqueViolation as e:
        if e.diag.constraint_name != "uq_flow_tasks_source_open":
            raise
        return {"task_id": await ctx.val(OPEN_FROM_SOURCE, (inp.source_kind, inp.source_ref)), "existed": True}
    return {"task_id": made["task_id"], "existed": False, "who": made["who"], "when": made["when"], "closed": made["closed"]}


class SetTask(Input):
    task_id: UUID = field("Việc", hidden=True)
    status: Literal["todo", "doing", "blocked", "done", "cancelled"] = field("Trạng thái", "done", options=TASK_STATUS)
    blocked_reason: str = field("Kẹt vì", "", max_length=300, help="Bắt buộc khi chuyển sang Kẹt")
    take: bool = field("Nhận việc về mình", False, help="Việc của đội chưa ai nhận", group="Thêm chi tiết")
    if_unchanged_since: datetime | None = field("Chỉ khi chưa ai sửa sau", None, hidden=True, agent=False,
                                                help="Hoàn tác (0144): việc đã được sửa sau mốc này ⇒ từ chối, không đè lên thay đổi mới")


# auto=True (ADR 0246): luật tự động được đổi trạng thái việc (vd quá hạn 14 ngày ⇒ Kẹt / Huỷ) — rủi ro thấp, người đảo được bằng chính lệnh này;
# việc có bộ kiểm vẫn không đóng tay được (chặn ở dưới + DB)
@m.command("set_task", resource="flow", title="Đổi trạng thái việc", risk="low", auto=True)
async def set_task(ctx: Ctx, inp: SetTask) -> dict[str, Any]:
    """Kéo thẻ trên bảng = lệnh này. Chỉ thấy (nên chỉ đổi được) việc trong phạm vi của mình — RLS."""
    t = await _task_for_change(ctx, inp.task_id)
    _unchanged_since(t, inp.if_unchanged_since)
    if t["done_check"] and inp.status == "done":   # mọi đường (nút, kéo thẻ, trợ lý, tự động hoá) qua đây; DB chặn thêm (mig 033)
        raise Invalid(f"“{t['title']}” xong khi “{registry.CHECKERS.get(t['done_check'], (t['done_check'],))[0]}” đạt — việc tự đóng khi có dữ kiện. "
                      "Bấm “Kiểm tra lại”, báo kẹt nếu vướng, hoặc xin đóng ngoại lệ (có lý do, qua duyệt)")
    if t["done_check"] and inp.status == "cancelled" and t["status"] != "cancelled":   # huỷ = đóng mà không làm ⇒ cùng đường duyệt ngoại lệ
        raise Invalid(f"“{t['title']}” có bộ kiểm — huỷ cần lý do và người duyệt: dùng “Xin đóng / huỷ ngoại lệ”, chọn “Huỷ việc”")
    if inp.status == "blocked" and not inp.blocked_reason.strip():
        raise Invalid("Kẹt vì gì? Ghi lý do để người khác gỡ được")
    # việc chung của đội: bắt tay vào (đang làm / kẹt / xong) = nhận về mình; thành viên đội chỉ sửa được sau khi nhận (RLS, migration 125)
    assignee = ctx.p.user_id if (inp.take or (t["assignee_id"] is None and inp.status in ("doing", "blocked", "done"))) else t["assignee_id"]
    await ctx.exec(
        """UPDATE flow.tasks SET status = %s, blocked_reason = %s, assignee_id = %s, updated_at = now(),
               done_at = CASE WHEN %s = 'done' THEN coalesce(done_at, now()) ELSE NULL END WHERE id = %s""",
        (inp.status, inp.blocked_reason.strip() if inp.status == "blocked" else "", assignee, inp.status, inp.task_id))
    await ctx.track(TASK_SUBJECT, inp.task_id, "assignee", t["assignee_id"], assignee)
    return {"task_id": inp.task_id, "status": inp.status, "summary": f"{t['title']} → {TASK_STATUS[inp.status]}"}


class TasksIn(Input):
    scope: Literal["", "team"] = field("Xem", "", options={"": "Việc của tôi", "team": "Việc của đội (mọi việc tôi được xem)"})


@m.query("tasks", resource="flow", title="Việc")
async def tasks(ctx: Ctx, inp: TasksIn) -> dict[str, Any]:
    """Bảng việc kéo thả theo trạng thái. "Của tôi" = giao cho tôi + việc của đội tôi chưa ai nhận; "của đội" = mọi việc trong phạm vi xem."""
    mine = inp.scope != "team"
    rows = await ctx.all(
        """SELECT t.*, u.name AS who, c.title AS case_title, pl.title AS plan_title FROM flow.tasks t
           LEFT JOIN core.users u ON u.id = t.assignee_id LEFT JOIN flow.cases c ON c.id = t.case_id LEFT JOIN flow.plans pl ON pl.id = t.plan_id
           WHERE (t.status IN ('todo', 'doing', 'blocked') OR (t.status = 'done' AND t.done_at > now() - interval '7 days'))
             AND (NOT %(mine)s OR t.assignee_id = core.me()
                  OR (t.assignee_id IS NULL AND t.team_id = ANY(%(member)s)))
           ORDER BY t.due_on NULLS LAST, t.created_at LIMIT 400""", {"mine": mine, "member": list(ctx.p.member_teams)})
    t0 = today()
    cols = []
    for key in ("todo", "doing", "blocked", "done"):
        items = []
        for r in (x for x in rows if x["status"] == key):
            it = _task_item(r)
            if r["case_title"] or r["plan_title"]:
                it["subtitle"] = f"{r['case_title'] or 'Kế hoạch ' + r['plan_title']} · {it['subtitle']}"
            if r["assignee_id"] is None:
                it["badge"] = "Chưa ai nhận"
            items.append(it)
        cols.append({"key": key, "title": TASK_STATUS[key] + (" (7 ngày)" if key == "done" else ""), "subtitle": "", "tone": TASK_TONE[key], "items": items})
    late = sum(1 for r in rows if r["due_on"] and r["due_on"] < t0 and r["status"] not in ("done",))
    today_n = sum(1 for r in rows if r["due_on"] == t0 and r["status"] != "done")
    blocked = sum(1 for r in rows if r["status"] == "blocked")
    return ui.card("Việc của tôi" if mine else "Việc của đội", [
        ui.b_kpis([ui.kpi("Cần làm", sum(1 for r in rows if r["status"] in ("todo", "doing")), hint=f"{today_n} việc hạn hôm nay" if today_n else ""),
                   ui.kpi("Quá hạn", late, tone="red" if late else None), ui.kpi("Kẹt", blocked, tone="red" if blocked else None)]),
        {"type": "board", "title": "", "columns": cols,
         "move": {"command": "flow.set_task", "id_key": "task_id", "column_key": "status", "form_columns": ["blocked"]}},
    ], subtitle="Kéo thẻ sang cột để đổi trạng thái", icon="checklist",
        actions=[ui.act("flow.create_task", "Thêm việc", style="primary")],
        ask={"title": "Nhờ trợ lý sắp việc", "prompt": "Sắp xếp việc hôm nay của tôi: "})


@m.command("remind_due", resource="flow", title="Nhắc việc đến hạn", internal=True,
           sweep="""SELECT DISTINCT tenant_id FROM flow.tasks WHERE status IN ('todo', 'doing', 'blocked')
                    AND assignee_id IS NOT NULL AND due_on <= core.today_of(tenant_id)""")
async def remind_due(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Vòng quét (mỗi phút, quyền hệ thống): từ 8 giờ sáng, MỖI NGƯỜI MỘT thông báo / ngày gộp việc hạn hôm nay + quá hạn — không báo
    từng việc (đỡ phiền), khoá theo ngày nên chạy lại không báo lặp."""
    if datetime.now(zone()).hour < REMIND_FROM_HOUR:
        return {"sent": 0}
    rows = await ctx.all(
        """SELECT assignee_id, title, due_on FROM flow.tasks WHERE status IN ('todo', 'doing', 'blocked') AND assignee_id IS NOT NULL
             AND due_on <= core.today() ORDER BY assignee_id, due_on, created_at""")
    by: dict[UUID, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["assignee_id"], []).append(r)
    t0, sent = today(), 0
    for uid, items in by.items():
        late = sum(1 for r in items if r["due_on"] < t0)
        due_today = len(items) - late
        head = ", ".join(x for x in [f"{due_today} việc đến hạn hôm nay" if due_today else "", f"{late} việc quá hạn" if late else ""] if x)
        sent += await ctx.notify([uid], kind="due", title=head[:1].upper() + head[1:], push=f"Anh/chị có {head}",
                                 body="\n".join(f"• {r['title']}" + (f" (hạn {r['due_on']:%d/%m})" if r["due_on"] < t0 else "") for r in items[:6]),
                                 open=TASKS_OPEN, key=f"due:{t0}")
    return {"sent": sent}


class DueBetween(Input):
    since: str
    until: str
    mine: bool = True


@m.query("due_between", resource="flow", title="Việc đến hạn trong khoảng ngày", agent="allow")
async def due_between(ctx: Ctx, inp: DueBetween) -> list[dict[str, Any]]:
    """Cho lịch gộp (crm.agenda): việc chưa xong có hạn trong [since, until) — của tôi, hoặc mọi việc tôi được xem."""
    return await ctx.all(
        """SELECT t.id, t.title, t.due_on, t.case_id, u.name AS who FROM flow.tasks t LEFT JOIN core.users u ON u.id = t.assignee_id
           WHERE t.status IN ('todo', 'doing', 'blocked') AND t.due_on >= %s::date AND t.due_on < %s::date
             AND (NOT %s OR t.assignee_id = core.me()) ORDER BY t.due_on LIMIT 300""", (inp.since, inp.until, inp.mine))


# ---- Câu tín hiệu của bàn việc (0094): khai trên màn (desk=Desk(..., say=...)); agent/desk.py chạy bằng quyền người xem

def _attn_needs_action(o: Any) -> Line | None:
    return (line("bad", f"{num(kpi(o, 'Hồ sơ kẹt quá hạn chặng'))} hồ sơ kẹt quá hạn chặng",
            "Thiếu gì, đang chờ ai — mở để gỡ", "Hồ sơ nào đang kẹt, thiếu gì, chờ ai?")
            if kpi(o, "Hồ sơ kẹt quá hạn chặng") else None)


def _attn_tasks(o: Any) -> Line | None:
    return (line("bad", f"{num(kpi(o, 'Quá hạn'))} việc của anh/chị đã quá hạn", "Làm, dời hạn hoặc ghi lý do kẹt", "Sắp xếp giúp tôi các việc quá hạn") if kpi(o, "Quá hạn") else None)


def _attn_tasks_2(o: Any) -> Line | None:
    return (line("warn", f"{num(kpi(o, 'Kẹt'))} việc đang kẹt", "Xem ai cần gỡ giúp")
            if kpi(o, "Kẹt") else None)
DEFAULT_CALENDAR = "vn-public"   # công ty chưa đặt lịch làm việc (flow.work_calendars) ⇒ lịch hành chính 5 ngày (0068)


async def _team_tree(ctx: Ctx, team: UUID) -> list[UUID]:
    rows = await ctx.all("""WITH RECURSIVE t AS (SELECT id FROM core.teams WHERE id = %s
                            UNION SELECT c.id FROM core.teams c JOIN t ON c.parent_id = t.id) SELECT id FROM t""", (team,))
    return [r["id"] for r in rows]


async def _candidates(ctx: Ctx, team_code: str) -> list[dict[str, Any]]:
    """Người mà người giao ĐƯỢC giao việc — theo phạm vi ``flow`` của chính người đó: cả công ty (``all``; chọn đội ⇒ cây đội đó), đội mình làm
    trưởng + cây con (``team``), hay chỉ bản thân (``own``). Chỉ thành viên đang hoạt động."""
    team = await _team_id(ctx, team_code)
    if team_code and team is None:
        raise NotFound(f"Không có đội {team_code}")
    scope = ctx.p.scope("flow")
    if scope == "all":
        teams: list[UUID] | None = await _team_tree(ctx, team) if team else None
    elif scope == "team":
        managed = set(ctx.p.managed_teams)
        teams = [t for t in await _team_tree(ctx, team) if t in managed] if team else list(managed)
    else:
        teams = []
    return await ctx.all(
        """SELECT DISTINCT ON (u.id) u.id, u.email, u.name, r.label AS role, t.name AS team FROM core.memberships mb
           JOIN core.users u ON u.id = mb.user_id JOIN core.roles r ON r.tenant_id = mb.tenant_id AND r.key = mb.role
           LEFT JOIN core.team_members tm ON tm.tenant_id = mb.tenant_id AND tm.user_id = u.id LEFT JOIN core.teams t ON t.id = tm.team_id
           WHERE mb.active AND (%(any)s OR tm.team_id = ANY(%(teams)s) OR u.id = core.me())   -- luôn giao được cho chính mình
           ORDER BY u.id, tm.is_lead DESC""", {"any": teams is None, "teams": teams or []})


async def _calendar_of(ctx: Ctx, team: UUID | None) -> str:
    """Lịch làm việc của một đội: lịch riêng của đội > mặc định của công ty > lịch hành chính. Dữ liệu công ty (migration 169)."""
    rows = await ctx.all("SELECT team_id, calendar FROM flow.work_calendars WHERE team_id IS NULL OR team_id = %s", (team,))
    by = {r["team_id"]: r["calendar"] for r in rows}
    return by.get(team) or by.get(None) or DEFAULT_CALENDAR


def _calendar_note(warnings: tuple[str, ...] | list[str]) -> str:
    """Trên thẻ / câu tóm tắt chỉ nói điều làm HẠN NÀY có thể sai: năm chưa có lịch lễ. "Lịch … chưa được xác nhận" là trạng thái của dữ liệu
    lịch (đúng cho mọi ngày) — để ở ``calendar_warnings`` và màn "Lịch làm việc", không lặp trên từng việc."""
    missing = [w for w in warnings if w.startswith("Chưa có lịch")]
    return (" — Lưu ý: " + "; ".join(missing)) if missing else ""


async def _due(ctx: Ctx, d: date | None, team: UUID | None, milestone: date | None, label: str) -> tuple[date | None, bool, tuple[str, ...]]:
    """MỘT luật hạn cho mọi đường giao việc (lập kế hoạch, thêm việc, giao lại / dời hạn): không trong quá khứ; rơi ngày nghỉ theo lịch của
    ĐỘI người nhận ⇒ lùi về ngày làm việc LIỀN TRƯỚC (việc nội bộ phải xong trước — chuẩn bị cho sự kiện thứ Bảy thì hạn thứ Sáu; khác hạn
    nộp theo luật là sang ngày kế tiếp); không còn ngày làm việc nào từ hôm nay tới đó ⇒ giữ ngày người giao chọn; không trễ hơn mốc kế hoạch.
    Giữ cảnh báo lịch để người xác nhận biết dữ liệu còn thiếu."""
    if d is None:
        return None, False, ()
    if d < today():
        raise Invalid(f"{label}hạn {d:%d/%m/%Y} đã qua")
    adjusted = business_calendar.previous_workday(await _calendar_of(ctx, team), d)
    moved = adjusted.day if adjusted.day >= today() else d
    if milestone and moved > milestone:
        raise Invalid(f"{label}hạn {moved:%d/%m/%Y} trễ hơn mốc kế hoạch {milestone:%d/%m/%Y} — dời mốc kế hoạch hoặc chia nhỏ việc")
    return moved, moved != d, adjusted.warnings


class WorkCalendarIn(Input):
    team_code: str = field("Đội", "", options_query="admin.team_options", placeholder="Lịch mặc định của cả công ty")
    calendar: str = field("Lịch", options_query="flow.calendar_options", help="Ngày nghỉ trong tuần + nghỉ lễ của đội — dùng dời hạn việc rơi ngày nghỉ")


WEEKDAYS = ("thứ Hai", "thứ Ba", "thứ Tư", "thứ Năm", "thứ Sáu", "thứ Bảy", "Chủ nhật")


def calendar_label(cid: str) -> str:
    """Tên đọc được của một lịch (thay mã ``vn-public`` …): ngày nghỉ trong tuần của bản mới nhất + lễ."""
    if cid not in business_calendar.calendars():
        return cid
    t = today()
    wk = sorted({d.weekday() for d, why in business_calendar.non_working(cid, t, t + timedelta(days=13)) if why == "weekend"})
    return "Làm cả tuần, chỉ nghỉ lễ" if not wk else f"Nghỉ {' và '.join(WEEKDAYS[d] for d in wk)} và ngày lễ"


@m.query("calendar_options", resource=None, title="Ngày làm việc & nghỉ lễ")
async def calendar_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [{"value": c, "label": calendar_label(c)} for c in business_calendar.calendars()]


@m.command("set_work_calendar", resource="flow.templates", action="manage", title="Đặt lịch làm việc")
async def set_work_calendar(ctx: Ctx, inp: WorkCalendarIn) -> dict[str, Any]:
    """Lịch dùng để dời hạn việc rơi ngày nghỉ — mặc định của công ty hoặc riêng một đội (vd đội bán hàng làm cả cuối tuần)."""
    if inp.calendar not in business_calendar.calendars():
        raise NotFound(f"Không có lịch {inp.calendar}")
    team = await _team_id(ctx, inp.team_code)
    if inp.team_code and team is None:
        raise NotFound(f"Không có đội {inp.team_code}")
    await ctx.exec("""INSERT INTO flow.work_calendars (team_id, calendar) VALUES (%s, %s)
                      ON CONFLICT (tenant_id, team_id) DO UPDATE SET calendar = EXCLUDED.calendar""", (team, inp.calendar))
    return {"summary": f"Ngày làm việc {'đội ' + inp.team_code if inp.team_code else 'mặc định'} → {calendar_label(inp.calendar)}"}


@m.query("work_calendars", resource="flow.templates", title="Ngày làm việc & nghỉ lễ")
async def work_calendars(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Lịch của từng đội (đội không đặt riêng ⇒ theo mặc định của công ty) — dùng dời hạn việc rơi ngày nghỉ."""
    rows = await ctx.all("""SELECT t.code, t.name, w.calendar FROM core.teams t LEFT JOIN flow.work_calendars w ON w.team_id = t.id ORDER BY t.code""")
    default = await ctx.val("SELECT calendar FROM flow.work_calendars WHERE team_id IS NULL") or DEFAULT_CALENDAR
    out = [{"id": "", "code": "", "name": "Mặc định cả công ty", "calendar": default, "own": "—"}]
    out += [{"id": r["code"], "code": r["code"], "name": r["name"], "calendar": r["calendar"] or default, "own": "Riêng" if r["calendar"] else "Theo mặc định"}
            for r in rows]
    for r in out:
        r["calendar_label"] = calendar_label(r["calendar"])   # chỉ THÊM trường; mã lịch giữ ở ``calendar`` cho nút Đổi lịch
    return ui.table(out, [ui.col("name", "Đội"), ui.col("code", "Mã"), ui.col("calendar_label", "Ngày nghỉ"), ui.col("own", "")],
                    title="Ngày làm việc & nghỉ lễ",
                    note="Dùng để dời hạn việc rơi vào ngày nghỉ. Ngày lễ lấy từ dữ liệu lịch có sẵn — "
                         "lịch đang ở trạng thái giả định cần người xác nhận theo thông báo nghỉ lễ chính thức.", row_key="id")


class CalendarOfIn(Input):
    team_id: UUID | None = None


@m.query("calendar_of", resource=None, title="Lịch làm việc của đội", internal=True, agent="deny", callers=("schedule",),
         why="schedule (màn Lịch — 0240) tô bóng ngày nghỉ theo lịch làm việc công ty / đội đã đặt ở Ngày làm việc & nghỉ lễ, cùng nguồn với dời hạn "
             "việc. Trả MÃ lịch (vd vn-t2-t7), không gì khác. [id: không nhận mã người — team_id là đội người xem đang xem, chỉ để chọn lịch riêng của đội]")
async def calendar_of(ctx: Ctx, inp: CalendarOfIn) -> dict[str, str]:
    return {"calendar": await _calendar_of(ctx, inp.team_id)}


class WorkloadIn(Input):
    user_ids: list[UUID] = field("Người", hidden=True)


@m.query("workload", resource=None, title="Khối lượng việc theo người", internal=True, agent="deny", callers=("flow",),
         why="Xếp người nhận (_ranked) trong phạm vi giao việc người gọi đã có: người giao không thấy hết việc của người nhận. Trả CHỈ SỐ ĐẾM theo "
             "người được chọn — không tên việc, không nội dung."
             " [id: người gọi thấy — user_ids là người trong phạm vi giao việc của chính người gọi (_candidates)]")
async def workload(ctx: Ctx, inp: WorkloadIn) -> dict[str, dict[str, int]]:
    """CHỈ SỐ ĐẾM (không nội dung việc) của những người được chọn — để xếp hạng người nhận cho đúng dù người giao không thấy hết việc của họ."""
    rows = await ctx.all(
        """SELECT assignee_id::text AS uid,
                  count(*) FILTER (WHERE status IN ('todo', 'doing', 'blocked')) AS open,
                  count(*) FILTER (WHERE status IN ('todo', 'doing', 'blocked') AND due_on < core.today()) AS overdue,
                  count(*) FILTER (WHERE status IN ('todo', 'doing', 'blocked') AND due_on BETWEEN core.today() AND core.today() + 7) AS due_week,
                  -- đúng hạn chỉ tính việc ĐÓNG BẰNG BẰNG CHỨNG (0117): lần tự bấm "Xong" không phải dữ kiện về tốc độ
                  count(*) FILTER (WHERE status = 'done' AND done_by = 'evidence' AND due_on IS NOT NULL AND done_at > now() - interval '28 days') AS done28,
                  count(*) FILTER (WHERE status = 'done' AND done_by = 'evidence' AND due_on IS NOT NULL AND done_at > now() - interval '28 days'
                                   AND (done_at AT TIME ZONE core.tz())::date <= due_on) AS ontime28
           FROM flow.tasks WHERE assignee_id = ANY(%s) GROUP BY assignee_id""", (inp.user_ids,))
    return {r["uid"]: {k: int(r[k]) for k in ("open", "overdue", "due_week", "done28", "ontime28")} for r in rows}


async def _ranked(ctx: Ctx, team_code: str) -> list[dict[str, Any]]:
    people = await _candidates(ctx, team_code)
    load: dict[str, dict[str, int]] = await ctx.call_system("flow.workload", user_ids=[p["id"] for p in people]) if people else {}
    out = []
    for p in people:
        w = load.get(str(p["id"]), {})
        open_, overdue, week, done28, ontime28 = (w.get(k, 0) for k in ("open", "overdue", "due_week", "done28", "ontime28"))
        rate = round(ontime28 * 100 / done28) if done28 >= 3 else None   # ít hơn 3 việc đóng bằng bằng chứng ⇒ chưa đủ để nói
        reason = f"{open_} việc đang mở · {overdue} quá hạn · {week} đến hạn trong 7 ngày" + (f" · đúng hạn {rate}% (có bằng chứng, 28 ngày)" if rate is not None else "")
        # Điểm CHỈ theo khối lượng (0117): bỏ tỉ lệ đúng hạn khỏi điểm — trước đây tự bấm "Xong" sớm thì được giao ít việc hơn; tỉ lệ chỉ để hiện
        out.append({**p, "open": open_, "overdue": overdue, "due_week": week, "ontime": rate, "reason": reason,
                    "score": overdue * 3 + open_ + week * 0.5})
    out.sort(key=lambda r: (r["score"], r["name"]))
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


class AssigneeIn(Input):
    team_code: str = field("Đội", "", options_query="admin.team_options", placeholder="Mọi người trong phạm vi của bạn")
    q: str = field("Tên", "", max_length=80, help="Lọc theo tên (không dấu cũng được), vd “Hạnh”")


@m.query("assignees", resource="flow", title="Ai nên nhận việc")
async def assignees(ctx: Ctx, inp: AssigneeIn) -> dict[str, Any]:
    """Người bạn giao việc được, XẾP HẠNG bằng code theo khối lượng: quá hạn (nặng nhất), việc đang mở, việc đến hạn 7 ngày — kèm lý do từng người.
    Tỉ lệ đúng hạn (chỉ việc đóng bằng bằng chứng, 28 ngày) để xem, không vào điểm. Trợ lý giao việc CHỈ chọn trong danh sách này."""
    rows = await _ranked(ctx, inp.team_code)
    if inp.q.strip():
        want = fold(inp.q).strip()
        rows = [r for r in rows if want in fold(r["name"])]
    for r in rows:
        r["ontime_text"] = f"{r['ontime']}%" if r["ontime"] is not None else "chưa đủ dữ liệu"
    return ui.table(rows, [
        ui.col("rank", "#", "number"), ui.col("name", "Người"), ui.col("email", "Email"), ui.col("role", "Vai"), ui.col("team", "Đội"),
        ui.col("open", "Đang mở", "number"), ui.col("overdue", "Quá hạn", "number"), ui.col("due_week", "Hạn 7 ngày", "number"),
        ui.col("ontime_text", "Đúng hạn (có bằng chứng, 28 ngày)"),
    ], note="Xếp theo khối lượng hiện tại (quá hạn tính nặng gấp 3). Chỉ gồm người trong phạm vi giao việc của bạn.",
        empty="Bạn chưa giao việc được cho ai ngoài bản thân (cần quyền việc phạm vi đội hoặc cả công ty)", limit=200)


@m.query("assignee_options", resource="flow", title="Người nhận việc")
async def assignee_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Ô thả xuống "Giao cho" của form kế hoạch: người giao được, XẾP SẴN theo khối lượng, nhãn kèm số việc — người duyệt sửa nhanh mà vẫn
    chọn người ít việc. (Trợ lý dùng ``flow.assignees`` — bảng có lý do; đuôi ``_options`` là danh sách của form, trợ lý không thấy.)"""
    out = []
    for r in await _ranked(ctx, ""):
        load = f"{r['open']} việc mở" + (f", {r['overdue']} quá hạn" if r["overdue"] else "")
        out.append({"value": r["email"], "label": " · ".join(x for x in (r["name"], r["team"], load) if x)})
    return out


async def source_readable(ctx: Ctx, kind: str | None, ref: str | None, user_id: UUID) -> bool:
    """Việc nhớ nguồn là một TIN NHẮN ⇒ người làm phải đọc được trao đổi chứa tin (tiêu đề / dấu dẫn về tin) — kiểm bằng quyền của chính người đó,
    nạp lại từ DB, lúc tạo VÀ mỗi lần giao lại. Gói Trao đổi tắt / không đọc được ⇒ không. Nguồn khác: không ràng buộc."""
    if kind != "talk.message" or not ref:
        return True
    who = await ctx.principal_of(user_id)
    if who is None:
        return False
    try:
        return await ctx.call_as_optional("talk.message_ref", who, message_id=int(ref)) is not None
    except NotFound:
        return False


class AdjustTask(Input):
    task_id: UUID = field("Việc", hidden=True)
    assignee_email: str = field("Giao lại cho", "", options_query="flow.assignee_options", placeholder="Giữ người đang làm", help="Xếp theo khối lượng")
    due_on: Day | None = field("Hạn mới", None, placeholder="Giữ hạn cũ")
    note: str = field("Lời nhắn", "", kind="textarea", max_length=500)
    if_unchanged_since: datetime | None = field("Chỉ khi chưa ai sửa sau", None, hidden=True, agent=False,
                                                help="Hoàn tác (0144): việc đã được sửa sau mốc này ⇒ từ chối, không đè lên thay đổi mới")


@m.command("adjust_task", resource="flow", title="Giao lại / dời hạn", promotable=True, risk="low", target="task_id",   # 0143: rủi ro thấp, đảo được
           undone_by=("flow.adjust_task", "flow.set_task:status=cancelled"), subject=(TASK_SUBJECT, "task_id"),
           purpose="Việc cần người khác nhận hoặc cần dời hạn để kịp thực tế — người cũ và người mới đều được báo")
async def adjust_task(ctx: Ctx, inp: AdjustTask) -> dict[str, Any]:
    """Đổi người làm và / hoặc hạn của một việc — cùng luật như lập kế hoạch: người mới trong phạm vi giao việc của người đổi, hạn không qua,
    không trễ hơn mốc kế hoạch, rơi ngày nghỉ thì dời. Báo người mới, người cũ (nếu đổi người) kèm lời nhắn."""
    t = await _task_for_change(ctx, inp.task_id)
    _unchanged_since(t, inp.if_unchanged_since)
    if t["status"] in ("done", "cancelled"):
        raise Invalid("Việc đã xong / đã huỷ — không cần đổi")
    email = inp.assignee_email.strip().lower()
    if not email and inp.due_on is None:
        raise Invalid("Chọn người nhận mới hoặc hạn mới")
    who, due = t["assignee_id"], t["due_on"]
    if email:
        allowed = {r["email"]: r["id"] for r in await _candidates(ctx, "")}
        if email not in allowed:
            raise Invalid(f"Không giao được cho {email} — ngoài phạm vi giao việc của bạn. Chọn trong “Ai nên nhận việc”")
        who = allowed[email]
    moved = who != t["assignee_id"]
    if moved and who is not None and not await source_readable(ctx, t["source_kind"], t["source_ref"], who):
        name = await ctx.val("SELECT name FROM core.users WHERE id = %s", (who,))
        raise Invalid(f"Việc này tạo từ một tin nhắn mà {name} không xem được — chỉ giao lại cho người đọc được trao đổi đó")
    team = await ctx.home_team(who) if moved else t["team_id"]
    warnings: tuple[str, ...] = ()
    if inp.due_on is not None:
        due, _, warnings = await _due(ctx, inp.due_on, team, t["plan_due"], "")
        if due and t["start_on"] and t["start_on"] > due:
            raise Invalid(f"Hạn mới {due:%d/%m/%Y} trước ngày bắt đầu {t['start_on']:%d/%m/%Y} — đổi ngày bắt đầu trước (Lịch của việc)")
    stamp = await ctx.val("UPDATE flow.tasks SET assignee_id = %s, team_id = %s, due_on = %s, updated_at = now() WHERE id = %s RETURNING updated_at",
                          (who, team, due, inp.task_id))
    await ctx.track(TASK_SUBJECT, inp.task_id, "assignee", t["assignee_id"], who)
    await ctx.track(TASK_SUBJECT, inp.task_id, "due_on", t["due_on"], due)
    see = _plan_open(t["plan_id"]) if t["plan_id"] else TASKS_OPEN
    when = f", hạn {due:%d/%m}" if due else ""
    body = ctx.p.name + (f": {inp.note.strip()}" if inp.note.strip() else "")
    if moved:
        await ctx.notify([who], kind="task", title=f"Việc được giao cho anh/chị: {t['title']}{when}", body=body, push=f"Anh/chị có việc mới{when}", open=see)
        if t["assignee_id"]:
            await ctx.notify([t["assignee_id"]], kind="task", title=f"Việc “{t['title']}” đã chuyển cho người khác", body=body, open=see)
    elif due != t["due_on"]:
        await ctx.notify([who], kind="task", title=f"Việc “{t['title']}” dời hạn sang {due:%d/%m}", body=body, open=see)
    name = await ctx.val("SELECT name FROM core.users WHERE id = %s", (who,)) if who else "đội"
    # HOÀN TÁC (0144): chính lệnh này với giá trị TRƯỚC đó, chỉ khi chưa ai sửa sau lần này; việc của đội (không người, không hạn) thì huỷ-không-được ⇒ bỏ
    old_email = await ctx.val("SELECT email FROM core.users WHERE id = %s", (t["assignee_id"],)) if t["assignee_id"] else ""
    undo = ({"command": "flow.adjust_task", "title": f"Hoàn tác: trả “{t['title']}” về như trước",
             "input": {"task_id": str(inp.task_id), "assignee_email": old_email or "", "due_on": t["due_on"].isoformat() if t["due_on"] else None,
                       "note": "hoàn tác", "if_unchanged_since": stamp.isoformat()}} if (old_email or t["due_on"]) else None)
    return {"task_id": inp.task_id, "calendar_warnings": list(warnings),
            "summary": f"“{t['title']}” → {name}{when}" + _calendar_note(warnings), **({"undo": undo} if undo else {})}


# ── Mốc hạn cho lịch chung (0158): hạn việc — KHÔNG là lịch (không chiếm giờ của ai), màn lịch vẽ chồng ──

class MarkersIn(Input):
    since: date
    until: date
    mine: bool = True


@m.plug("schedule.markers", "Hạn việc", resource="flow")
async def plug_markers(ctx: Ctx, inp: MarkersIn) -> list[dict[str, Any]]:
    """Bằng quyền người xem (RLS của việc): việc chưa xong có hạn trong [since, until) — của tôi, hoặc mọi việc tôi được xem."""
    return [{"on": r["due_on"], "kind": "task", "label": "Hạn việc", "title": r["title"], "ref": str(r["id"]), "detail": r["who"] or "đội",
             "open": {"query": "flow.case", "input": {"case_id": str(r["case_id"])}} if r["case_id"] else {"query": "flow.tasks", "input": {}}}
            for r in await ctx.all("""SELECT t.id, t.title, t.due_on, t.case_id, u.name AS who FROM flow.tasks t LEFT JOIN core.users u ON u.id = t.assignee_id
                                      WHERE t.status IN ('todo', 'doing', 'blocked') AND t.due_on >= %s AND t.due_on < %s
                                        AND (NOT %s OR t.assignee_id = core.me()) ORDER BY t.due_on LIMIT 1000""", (inp.since, inp.until, inp.mine))]


# ── Nguồn ngày cho luật tự động theo thời gian (ADR 0246) ──
@m.date_source("task_due", "Việc — hạn việc", date_label="hạn việc", resource="flow",
               fields={"status": "Trạng thái", "origin": "Nguồn việc", "team": "Đội", "owner": "Người làm"}, ids=("task_id",), open=("flow.task", {"task_id": "task_id"}),
               why="Động cơ luật tự động (flow, ADR 0246) xét luật theo ngày bằng quyền hệ thống: việc CHƯA XONG (cần làm / đang làm / kẹt) có hạn trong "
                   "khoảng được hỏi — trả mã việc, hạn, người làm (mã + tên), trạng thái, nguồn việc, tên đội; không tiêu đề / ghi chú việc. Hành động của luật "
                   "chạy bằng quyền người đặt luật (nạp lại mỗi lần)")
async def task_due(ctx: Ctx, inp: DateIn) -> list[dict[str, Any]]:
    return await ctx.all(
        """SELECT t.id::text AS ref, t.id AS task_id, t.due_on AS "on", t.assignee_id AS owner_id, u.name AS owner, t.status, t.origin, tm.name AS team
           FROM flow.tasks t LEFT JOIN core.teams tm ON tm.id = t.team_id LEFT JOIN core.users u ON u.id = t.assignee_id
           WHERE t.status IN ('todo', 'doing', 'blocked') AND t.due_on BETWEEN %s AND %s ORDER BY t.due_on, t.id LIMIT 2000""", (inp.since, inp.until))
