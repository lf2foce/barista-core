"""HỒ SƠ THEO CHẶNG — mẫu quy trình (cú pháp chữ, phiên bản), hồ sơ của một đối tượng, mục kiểm, đồng bộ chặng, canh hạn chặng / Cần xử lý. Module ``flow`` (0088: package — ``m`` khai ở ``__init__``)."""

import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.api import Conflict, Ctx, Denied, Event, Input, Invalid, NoInput, NotFound, field, notify, today, ui, zone
from barista.kernel import registry
from barista.kernel.text import snake_from_name
from barista.modules.flow import ITEM_STATUS, TASKS_OPEN, _case_open, _task_item, _team_id, m

# ── Mẫu quy trình: cú pháp chữ ──
#
#   # Giữ chỗ & cọc | Kế toán                  ← chặng: tên | ai giữ (nhãn hiển thị)
#   - Phiếu giữ chỗ đã ký                       ← mục do người xác nhận (bắt buộc)
#   - Tiền cọc đã nhận = <module>.<bộ kiểm> seq=1          ← mục có BỘ KIỂM bằng dữ kiện thật (+ tham số) — bộ kiểm do gói sở hữu đối tượng khai
#   - Phí đã thu = <module>.<bộ kiểm> [riêng: acct]              ← chỉ người có quyền "acct" cả công ty thấy số
#   - Ảnh bàn giao (tuỳ chọn)                   ← không bắt buộc
#   - Giám đốc duyệt ngân sách [xác nhận: director]   ← CHỈ người vai đó / đội đó (@KT) xác nhận được — server kiểm, không phải nhãn
#   # Chốt | Giám đốc [xác nhận: director, @KT]  ← đặt ở chặng = mặc định cho mọi mục người xác nhận của chặng
#   > Gửi phiếu cọc cho khách ký | 1 ngày       ← việc tự sinh khi vào chặng — giao người phụ trách hồ sơ
#   > Đối chiếu phiếu cọc @KT | 2 ngày          ← … hoặc giao cả đội (mã đội), ai trong đội nhận cũng được
#   # Thanh toán & phí | Kế toán | tối đa 30 ngày, báo @KT   ← HẠN CHẶNG: nằm quá 30 ngày ⇒ một việc "hồ sơ kẹt" cho đội KT
#                                                              (không "báo @…" ⇒ giao người phụ trách hồ sơ) — H12
#   ~ Khách im quá 5 ngày thì soạn tin nhắc; thiếu CCCD thì gửi link nộp   ← LỜI DẶN TRỢ LÝ khi hồ sơ kẹt ở chặng này (0142) — dữ liệu, có phiên bản

def _slug(s: str) -> str:
    s = unicodedata.normalize("NFD", s.replace("đ", "d").replace("Đ", "D"))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:40] or "muc"


def _confirm_of(body: str) -> tuple[list[str], str]:
    """``[xác nhận: director, @KT]`` ⇒ (["director", "@KT"], phần còn lại)."""
    if (mc := re.search(r"\[x[áa]c nh[ậa]n\s*:\s*([^\]]+)\]", body)) is None:
        return [], body
    who = [w.strip() for w in mc.group(1).split(",") if w.strip()]
    return who, body.replace(mc.group(0), "").strip()


async def _check_confirmers(ctx: Ctx, stages: list[dict[str, Any]]) -> None:
    for w in {w for st in stages for w in st["confirm"] + [x for i in st["items"] for x in i["confirm"]]} | {
            "@" + st["escalate"] for st in stages if st.get("escalate")}:
        if w.startswith("@"):
            if not await ctx.val("SELECT 1 FROM core.teams WHERE code = %s", (w[1:],)):
                raise Invalid(f"Không có đội {w}")
        elif not await ctx.val("SELECT 1 FROM core.roles WHERE key = %s", (w,)):
            raise Invalid(f"Không có vai trò “{w}” (người xác nhận ghi mã vai, vd director, hoặc @MÃ_ĐỘI)")


async def _may_confirm(ctx: Ctx, confirm: list[str]) -> bool:
    """Người xác nhận khai trong mẫu: đúng VAI, hoặc thuộc / quản ĐỘI đó (cả cây con — như phạm vi "đội"). Không khai ⇒ ai ghi được hồ sơ."""
    if not confirm or ctx.p.system:
        return True
    if ctx.p.role in confirm:
        return True
    codes = [w[1:] for w in confirm if w.startswith("@")]
    if not codes:
        return False
    ids = {r["id"] for r in await ctx.all("SELECT id FROM core.teams WHERE code = ANY(%s)", (codes,))}
    return bool(ids & {*ctx.p.member_teams, *ctx.p.managed_teams})   # thành viên đội đó hoặc quản lý đội đó (cây cha)


async def _confirm_label(ctx: Ctx, confirm: list[str]) -> str:
    roles = {r["key"]: r["label"] for r in await ctx.all("SELECT key, label FROM core.roles WHERE key = ANY(%s)", ([w for w in confirm if w[0] != "@"],))}
    teams = {r["code"]: r["name"] for r in await ctx.all("SELECT code, name FROM core.teams WHERE code = ANY(%s)", ([w[1:] for w in confirm if w[0] == "@"],))}
    return " hoặc ".join(roles.get(w, teams.get(w[1:], w)) for w in confirm)


def check_args(parts: list[str], where: str = "") -> dict[str, Any]:
    """Tham số bộ kiểm dạng ``ten=giatri`` (số nguyên thì thành số) — MỘT cú pháp cho mục hồ sơ lẫn ``done_when`` của việc (0117)."""
    args: dict[str, Any] = {}
    for kv in parts:
        k, _, v = kv.partition("=")
        if not k or not v:
            raise Invalid(f"{where}tham số bộ kiểm dạng ten=giatri (nhận “{kv}”)")
        args[k] = int(v) if v.isdigit() else v
    return args


def check_fits(source: str, subject_kind: str, where: str = "", ours: str = "") -> None:
    """Bộ kiểm phải đã đăng ký và đúng loại đối tượng nó đọc."""
    if source not in registry.CHECKERS:
        raise Invalid(f"{where}không có bộ kiểm “{source}”. Có: {', '.join(sorted(registry.CHECKERS)) or '—'}")
    if registry.CHECKERS[source][1] != subject_kind:
        raise Invalid(f"{where}bộ kiểm “{source}” dành cho {registry.CHECKERS[source][1]}, {ours or f'việc gắn “{subject_kind or 'không đối tượng'}”'}")


def parse_template(text: str, subject_kind: str) -> list[dict[str, Any]]:
    stages: list[dict[str, Any]] = []
    keys: set[str] = set()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line[0] not in "#->~":
            raise Invalid(f"Dòng {n}: dòng phải bắt đầu bằng # (chặng), - (mục), > (việc tự sinh) hoặc ~ (lời dặn trợ lý)")
        if line.startswith("#"):
            confirm, body = _confirm_of(line.lstrip("#").strip())
            name, _, rest = (p.strip() for p in body.partition("|"))
            owner, _, rules = (p.strip() for p in rest.partition("|"))
            if not name:
                raise Invalid(f"Dòng {n}: chặng cần tên")
            if any(st["key"] == _slug(name) for st in stages):
                raise Invalid(f"Dòng {n}: trùng chặng “{name}”")
            max_days = int(md.group(1)) if (md := re.search(r"t[ốo]i đa\s*(\d+)\s*ngày", rules)) else None
            escalate = me.group(1) if (me := re.search(r"báo\s*@([A-Za-z0-9_-]+)", rules)) else ""
            soon = int(ms.group(1)) if (ms := re.search(r"nhắc trước\s*(\d+)\s*ngày", rules)) else SOON_DAYS   # trợ lý nhắc sớm (0142 lát 2)
            if rules and max_days is None:
                raise Invalid(f"Dòng {n}: phần hạn chặng ghi dạng “tối đa 30 ngày” (tuỳ chọn thêm “, báo @MÃ_ĐỘI”, “, nhắc trước 2 ngày”)")
            if max_days is not None and max_days < 1:
                raise Invalid(f"Dòng {n}: hạn chặng phải ít nhất 1 ngày")
            if max_days is not None and not 0 <= soon < max_days:
                raise Invalid(f"Dòng {n}: “nhắc trước” phải từ 0 tới dưới hạn chặng ({max_days} ngày)")
            stages.append({"key": _slug(name), "name": name, "owner": owner, "confirm": confirm, "items": [], "tasks": [],
                           "max_days": max_days, "escalate": escalate, "soon_days": soon, "guide": []})
            continue
        if not stages:
            raise Invalid(f"Dòng {n}: cần một chặng (# Tên chặng) trước khi khai mục")
        st = stages[-1]
        if line.startswith("~"):   # lời dặn trợ lý của chặng (0142): chữ tự do, không lệnh, không id — trợ lý đọc khi hồ sơ kẹt ở chặng này
            guide = line[1:].strip()
            if not guide:
                raise Invalid(f"Dòng {n}: lời dặn trợ lý (~) cần nội dung")
            if len(guide) > 500:
                raise Invalid(f"Dòng {n}: lời dặn trợ lý tối đa 500 ký tự một dòng")
            st["guide"].append(guide)
            continue
        if line.startswith("-"):
            confirm, body = _confirm_of(line[1:].strip())
            private = ""
            if (mp := re.search(r"\[riêng\s*:\s*([a-z_.]+)\]", body)) is not None:
                private, body = mp.group(1), body.replace(mp.group(0), "").strip()
            required = True
            if re.search(r"\(tuỳ chọn\)|\(tùy chọn\)", body):
                required, body = False, re.sub(r"\s*\(t[uù][yỳ] chọn\)", "", body).strip()
            label, _, check = (p.strip() for p in body.partition("="))
            source, args = "manual", {}
            if check:
                parts = check.split()
                source, args = parts[0], check_args(parts[1:], f"Dòng {n}: ")
                check_fits(source, subject_kind, f"Dòng {n}: ", f"mẫu này bám “{subject_kind or 'tự do'}”")
            if not label:
                raise Invalid(f"Dòng {n}: mục cần tên")
            key = _slug(label)
            if key in keys:
                raise Invalid(f"Dòng {n}: trùng mục “{label}”")
            keys.add(key)
            if confirm and source != "manual":
                raise Invalid(f"Dòng {n}: “{label}” kiểm bằng dữ kiện thật — không cần người xác nhận")
            st["items"].append({"key": key, "label": label, "required": required, "source": source, "args": args, "private": private,
                                "confirm": (confirm or st["confirm"]) if source == "manual" else []})
        else:
            title, _, rest = (p.strip() for p in line[1:].partition("|"))
            team = ""
            if (mt := re.search(r"@([A-Za-z0-9_-]+)", title)) is not None:
                team, title = mt.group(1), title.replace(mt.group(0), "").strip()
            days = int(md.group(1)) if (md := re.search(r"(\d+)\s*ngày", rest)) else None
            if not title:
                raise Invalid(f"Dòng {n}: việc cần tên")
            st["tasks"].append({"title": title, "due_days": days, "team_code": team})
    if not stages:
        raise Invalid("Mẫu cần ít nhất một chặng")
    for st in stages:
        if not st["items"]:
            raise Invalid(f"Chặng “{st['name']}” chưa có mục nào")
    return stages


def summary(stages: list[dict[str, Any]]) -> str:
    items = [i for st in stages for i in st["items"]]
    auto = sum(1 for i in items if i["source"] != "manual")
    tasks = sum(len(st["tasks"]) for st in stages)
    timed = sum(1 for st in stages if st.get("max_days"))
    guided = sum(1 for st in stages if st.get("guide"))
    return (f"{len(stages)} chặng ({' → '.join(st['name'] for st in stages)}) · {len(items)} mục: {len(items) - auto} người xác nhận, "
            f"{auto} kiểm tự động" + (f" · {tasks} việc tự sinh" if tasks else "") + (f" · {timed} chặng có hạn" if timed else "")
            + (f" · lời dặn trợ lý ở {guided} chặng" if guided else ""))


class SetTemplate(Input):
    key: str = field("Sửa quy trình có sẵn", "", options_query="flow.template_options_all", example="giao_dich", max_length=40, group="Thêm chi tiết",
                     placeholder="Quy trình mới — máy đặt mã từ tên", help="Chọn quy trình để lưu bản mới của nó; để trống khi thêm quy trình mới")
    name: str = field("Tên", example="Hồ sơ hợp đồng", max_length=120)
    subject_kind: str = field("Bám theo", "", options_query="flow.subject_options", placeholder="Hồ sơ tự do (tự mở bằng tay)")
    auto_open: bool = field("Tự mở hồ sơ khi có đối tượng mới", False, group="Thêm chi tiết")
    follow: bool = field("Trợ lý theo dõi hồ sơ", False, group="Thêm chi tiết",
                         help="Mở hồ sơ theo mẫu này ⇒ trợ lý của người phụ trách cầm hồ sơ nhiều ngày: kiểm mỗi ngày, kẹt chặng / việc quá hạn thì "
                              "soạn đề xuất (người bấm mới chạy). Lời dặn cho trợ lý ghi bằng dòng ~ trong chặng")
    source: str = field("Các chặng", kind="textarea", max_length=20000,
                        example="# Nộp & bàn giao | Kế toán\n- Đã gửi khách báo cáo kỳ\n- Nghĩa vụ của kỳ đã nộp = books.filings_done",
                        help="Mỗi chặng một dòng bắt đầu bằng # ; dưới mỗi chặng, mỗi việc cần xong một dòng bắt đầu bằng - . Chưa quen? Đóng form, chọn một mẫu có sẵn ở màn "
                             "Mẫu quy trình rồi sửa. Nâng cao: # Chặng | người giữ · mục = bộ_kiểm_tự_động · > việc @ĐỘI | số ngày · ~ lời dặn trợ lý")


@m.command("set_template", resource="flow.templates", action="manage", title="Lưu mẫu quy trình")
async def set_template(ctx: Ctx, inp: SetTemplate) -> dict[str, Any]:
    """Thêm PHIÊN BẢN mới (bản cũ thôi áp cho hồ sơ mới; hồ sơ đang mở giữ phiên bản của mình). Trợ lý được soạn — thẻ đề xuất hiện tóm tắt."""
    key = inp.key or snake_from_name(inp.name, {r["key"] for r in await ctx.all("SELECT DISTINCT key FROM flow.templates")})   # 0225: máy đặt mã từ tên
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", key):
        raise Invalid("Mã quy trình: chữ thường không dấu, số, gạch dưới (vd giao_dich)")
    if inp.subject_kind and inp.subject_kind not in registry.SUBJECTS:
        raise Invalid(f"Không có loại đối tượng “{inp.subject_kind}”")
    if inp.auto_open and not inp.subject_kind:
        raise Invalid("Tự mở hồ sơ cần chọn đối tượng bám theo")
    stages = parse_template(inp.source, inp.subject_kind)
    for code in {t["team_code"] for st in stages for t in st["tasks"]} - {""}:
        if not await ctx.val("SELECT 1 FROM core.teams WHERE code = %s", (code,)):
            raise Invalid(f"Không có đội @{code}")
    await _check_confirmers(ctx, stages)
    version = int(await ctx.val("SELECT coalesce(max(version), 0) + 1 FROM flow.templates WHERE key = %s", (key,)))
    await ctx.exec("UPDATE flow.templates SET is_active = false WHERE key = %s AND is_active", (key,))
    if inp.auto_open and await ctx.val("SELECT 1 FROM flow.templates WHERE subject_kind = %s AND auto_open AND is_active", (inp.subject_kind,)):
        raise Conflict("Đã có một quy trình khác tự mở theo loại đối tượng này — tắt tự mở ở quy trình kia trước")
    tid = await ctx.val(
        """INSERT INTO flow.templates (key, version, name, subject_kind, auto_open, follow, source, stages, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (key, version, inp.name, inp.subject_kind, inp.auto_open, inp.follow, inp.source.strip(), Jsonb(stages), ctx.p.user_id))
    return {"template_id": tid, "version": version, "summary": f"{inp.name} v{version}: {summary(stages)}"}


@m.query("subject_options", resource=None, title="Loại đối tượng")
async def subject_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [{"value": "", "label": "Tự do (mở bằng tay)"},
            *({"value": k, "label": v[0]} for k, v in registry.SUBJECTS.items() if not k.startswith("flow."))]


def _starter_block(has_any: bool) -> dict[str, Any]:
    """Khối "Bắt đầu từ một mẫu có sẵn": mỗi nút mở form Thêm quy trình đã điền sẵn tên + các chặng (người sửa được). Chưa có quy trình nào ⇒ nói thẳng việc nên làm."""
    from barista.modules.flow.starters import STARTERS

    return ui.b_callout("blue", "Bắt đầu từ một mẫu có sẵn" if not has_any else "Thêm quy trình từ mẫu có sẵn",
                        "Quy trình là các chặng việc một hồ sơ đi qua. Chọn mẫu gần việc của bạn rồi sửa chặng cho khớp." if not has_any else "",
                        facts=[f"{name} — {note}" for name, note, _ in STARTERS],
                        actions=[ui.act("flow.set_template", f"Mẫu: {name}", values={"name": name, "source": text}) for name, _, text in STARTERS])


@m.query("templates", resource="flow.templates", title="Mẫu quy trình")
async def templates(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all(
        """SELECT t.id, t.key, t.version, t.name, t.subject_kind, t.auto_open, t.follow, t.stages, t.created_at, u.name AS by_name,
                  (SELECT count(*) FROM flow.cases c WHERE c.template_id = t.id AND c.status = 'open') AS open_cases
           FROM flow.templates t LEFT JOIN core.users u ON u.id = t.created_by WHERE t.is_active ORDER BY t.name""")
    for r in rows:
        r["summary"] = summary(r.pop("stages"))
        r["subject"] = (registry.SUBJECTS.get(r["subject_kind"], ("Tự do",))[0] + (" · tự mở" if r["auto_open"] else "")
                        + (" · trợ lý theo dõi" if r["follow"] else ""))
    return ui.table(rows, [
        ui.col("name", "Quy trình"), ui.col("version", "Phiên bản", "number"), ui.col("subject", "Bám theo"), ui.col("summary", "Chặng & mục", width=420),
        ui.col("open_cases", "Hồ sơ đang mở", "number"), ui.col("by_name", "Người sửa"), ui.col("created_at", "Lúc", "datetime"),
    ], title="Mẫu quy trình", empty="Chưa có quy trình — bấm một mẫu có sẵn ở trên, hoặc nhờ trợ lý soạn", row_key="key",
        header=[_starter_block(bool(rows))])


class TemplateRef(Input):
    key: str = field("Mã quy trình", hidden=True)


@m.query("template", resource="flow.templates", title="Một mẫu quy trình")
async def template(ctx: Ctx, inp: TemplateRef) -> dict[str, Any]:
    t = await _active_template(ctx, inp.key)
    stages = [{"key": st["key"], "name": st["name"], "owner": st["owner"], "state": "todo",
               "items": [{"label": i["label"] + ("" if i["required"] else " (tuỳ chọn)"), "status": "missing" if i["source"] == "manual" else "received",
                          "required": i["required"], "hidden": False,
                          "value": "người xác nhận" if i["source"] == "manual" else f"tự kiểm: {registry.CHECKERS.get(i['source'], ('?',))[0]}",
                          "note": "; ".join(x for x in [f"chỉ người có quyền {i['private']} thấy số" if i["private"] else "",
                                                        f"chỉ {', '.join(i.get('confirm', []))} xác nhận" if i.get("confirm") else ""] if x)}
                         for i in st["items"]]} for st in t["stages"]]
    guides = [f"**{st['name']}**: " + " · ".join(st["guide"]) for st in t["stages"] if st.get("guide")]
    return ui.card(f"{t['name']} · v{t['version']}", [
        ui.b_text(summary(t["stages"]) + ("\n\nTrợ lý của người phụ trách cầm hồ sơ mở theo mẫu này (0142)." if t["follow"] else "")),
        ui.b_stages(stages),
        *([ui.b_text("\n".join(guides), "Lời dặn trợ lý theo chặng")] if guides else []),
        ui.b_text(t["source"], "Bản chữ (sửa = phiên bản mới)"),
    ], subtitle=registry.SUBJECTS.get(t["subject_kind"], ("Hồ sơ tự do",))[0], icon="list.bullet.rectangle",
        actions=[ui.act("flow.set_template", "Sửa (thành phiên bản mới)",
                        {"key": t["key"], "name": t["name"], "subject_kind": t["subject_kind"], "auto_open": t["auto_open"], "follow": t["follow"],
                         "source": t["source"]}, "primary")]
        if ctx.p.can("flow.templates", "manage") else [],
        open=[{"title": "Bảng hồ sơ", "query": "flow.board", "input": {"key": t["key"]}}],
        ask={"title": "Nhờ trợ lý sửa quy trình", "prompt": f"Sửa quy trình “{t['name']}”: "})


async def _active_template(ctx: Ctx, key: str) -> dict[str, Any]:
    t = await ctx.one("SELECT * FROM flow.templates WHERE key = %s AND is_active", (key,))
    if t is None:
        raise NotFound(f"Không có quy trình “{key}”")
    return t


# ── Hồ sơ: đánh giá + đồng bộ ──

async def _subject(ctx: Ctx, kind: str, sid: UUID) -> dict[str, Any] | None:
    if kind not in registry.SUBJECTS:
        return None
    try:
        out: dict[str, Any] = await ctx.call_system(registry.SUBJECTS[kind][1], subject_id=sid)
    except NotFound:
        return None
    return out


async def _evaluate(ctx: Ctx, case: dict[str, Any], tpl: dict[str, Any], *, upto_current: bool = False) -> dict[str, Any]:
    """Trạng thái từng mục (người xác nhận: bảng case_items; có bộ kiểm: hỏi module ngành bằng quyền hệ thống), chặng hiện tại theo luật,
    và phần riêng tư ẩn theo quyền NGƯỜI XEM. ``upto_current``: dừng ở chặng hiện tại (danh sách hồ sơ kẹt chỉ cần "thiếu gì, chờ ai" của chặng
    đó — chấm cả các chặng sau là gọi bộ kiểm sang module khác vô ích: đo 10/11 ~5 lần gọi / hồ sơ × 50 hồ sơ mỗi lượt màn Việc cần xử lý, §16)."""
    manual = {r["item_key"]: r for r in await ctx.all(
        "SELECT ci.*, u.name AS by_name FROM flow.case_items ci LEFT JOIN core.users u ON u.id = ci.by_user WHERE ci.case_id = %s", (case["id"],))}
    stages: list[dict[str, Any]] = []
    current: str | None = None
    for st in tpl["stages"]:
        items = []
        for it in st["items"]:
            status, at, note, value = "missing", None, "", None
            if it["source"] == "manual":
                if (r := manual.get(it["key"])) is not None:
                    status, at, note = r["status"], r["at"], r["note"] + (f" · {r['by_name']}" if r["by_name"] else "")
            elif case["subject_id"] is not None:
                try:
                    got = await ctx.call_system(it["source"], subject_id=case["subject_id"], args=it["args"])
                except NotFound:
                    got = {"status": "missing"}
                status, value, at = got.get("status", "missing"), got.get("value"), got.get("at")
            hidden = bool(it["private"]) and not ctx.p.has_scope(it["private"], "all")
            items.append({"key": it["key"], "label": it["label"], "status": status, "required": it["required"], "hidden": hidden,
                          "value": None if hidden else value, "note": "" if hidden else note, "at": at, "editable": it["source"] == "manual",
                          "confirm": it.get("confirm", [])})
        done = all(i["status"] == "verified" for i in items if i["required"])
        if not done and current is None:
            current = st["key"]
        stages.append({"key": st["key"], "name": st["name"], "owner": st["owner"], "items": items,
                       "state": "done" if done else ("current" if current == st["key"] else "todo")})
        if upto_current and current is not None:
            break
    if case["status"] == "cancelled":
        for st in stages:
            st["state"] = "done" if st["state"] == "done" else "stopped"
    cur = next((s for s in stages if s["key"] == current), None)
    return {"stages": stages, "current": current,
            "missing": [i["label"] for i in (cur["items"] if cur else []) if i["required"] and i["status"] != "verified"],
            "rejected": [(st["key"], i) for st in stages for i in st["items"] if i["status"] == "rejected"]}


async def _event(ctx: Ctx, case_id: UUID, kind: str, data: dict[str, Any] | None = None) -> None:
    await ctx.exec("INSERT INTO flow.case_events (case_id, kind, by_user, data) VALUES (%s, %s, %s, %s)",
                   (case_id, kind, ctx.p.user_id, Jsonb(data or {})))


class SyncIn(Input):
    case_id: UUID
    rejected: str = ""   # khoá mục vừa bị trả lại ⇒ sinh việc bổ sung cho người phụ trách


@m.command("sync", resource="flow", title="Đồng bộ hồ sơ", internal=True, callers=("flow",),
           why="Bên nghe sự kiện / cập nhật mục hồ sơ của flow (người cập nhật đã qua RLS của mục đó): tính lại chặng, sinh việc cho người KHÁC "
               "người bấm, đóng / huỷ hồ sơ — ghi trên flow.cases / tasks / case_items của đúng một hồ sơ. Trả {changed, status}.")
async def sync(ctx: Ctx, inp: SyncIn) -> dict[str, Any]:
    """Tính lại chặng bằng code và GHI lại: tiến / lùi chặng (kèm nhật ký), sinh việc khi vào chặng mới, việc bổ sung khi một mục bị
    trả lại, đóng hồ sơ khi qua hết chặng, huỷ khi đối tượng bị huỷ. Chạy bằng quyền hệ thống (việc sinh ra giao cho người khác người bấm)."""
    case = await ctx.one("SELECT * FROM flow.cases WHERE id = %s FOR UPDATE", (inp.case_id,))
    if case is None or case["status"] == "cancelled":
        return {"changed": False}
    tpl = await ctx.one("SELECT * FROM flow.templates WHERE id = %s", (case["template_id"],))
    assert tpl is not None
    if case["subject_id"] is not None:
        subj = await _subject(ctx, case["subject_kind"], case["subject_id"])
        if subj is None or subj.get("state") == "cancelled":   # cả hồ sơ đã đủ cũng huỷ theo (giao dịch huỷ sau khi ký)
            await ctx.exec("UPDATE flow.cases SET status = 'cancelled', closed_at = now() WHERE id = %s", (case["id"],))
            await ctx.exec("UPDATE flow.tasks SET status = 'cancelled', updated_at = now() WHERE case_id = %s AND status NOT IN ('done', 'cancelled')",
                           (case["id"],))
            await _event(ctx, case["id"], "cancelled")
            await _wake_follow(ctx, case["id"], "Hồ sơ đã huỷ.")
            return {"changed": True, "status": "cancelled"}
        if subj.get("owner_id") and (subj["owner_id"] != case["owner_id"] or subj.get("team_id") != case["team_id"]):
            await ctx.exec("UPDATE flow.cases SET owner_id = %s, team_id = %s, title = %s WHERE id = %s",
                           (subj["owner_id"], subj.get("team_id"), subj.get("title") or case["title"], case["id"]))
            await ctx.exec("UPDATE flow.tasks SET assignee_id = %s, team_id = %s, updated_at = now() WHERE case_id = %s AND assignee_id = %s"
                           " AND status NOT IN ('done', 'cancelled')", (subj["owner_id"], subj.get("team_id"), case["id"], case["owner_id"]))
            was = case["owner_id"]
            case = {**case, "owner_id": subj["owner_id"], "team_id": subj.get("team_id")}
            if case["status"] == "open" and case["stage_key"]:   # hồ sơ đang chờ người: giờ mới sinh việc của chặng hiện tại
                await _stage_tasks(ctx, case, next(st for st in tpl["stages"] if st["key"] == case["stage_key"]))
            if case["status"] == "open" and was != subj["owner_id"]:   # người phụ trách đổi ⇒ trợ lý của người MỚI cầm hồ sơ (0142)
                await _follow_case(ctx, case, tpl, handoff=True)
    if case["status"] != "open":
        return {"changed": False}
    ev = await _evaluate(ctx, case, tpl)
    new = ev["current"]
    if inp.rejected:
        it = next((i for st in ev["stages"] for i in st["items"] if i["key"] == inp.rejected), None)
        title = f"Bổ sung: {it['label']}" if it else ""
        if it and (case["owner_id"] or case["team_id"]) and not await ctx.val("SELECT 1 FROM flow.tasks WHERE case_id = %s AND origin = 'returned' AND title = %s"
                                    " AND status NOT IN ('done', 'cancelled')", (case["id"], title)):
            stage_of = next(st["key"] for st in ev["stages"] if any(i["key"] == inp.rejected for i in st["items"]))
            await ctx.exec(
                """INSERT INTO flow.tasks (title, notes, assignee_id, team_id, due_on, case_id, stage_key, origin, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'returned', %s)""",
                (title, f"{case['title']} — bị trả lại: {it['note']}", case["owner_id"], case["team_id"], today() + timedelta(days=1),
                 case["id"], stage_of, ctx.p.user_id))
            await ctx.notify([case["owner_id"]], kind="returned", title=f"Hồ sơ bị trả lại: {it['label']}",
                             body=f"{case['title']} — {it['note']}", push="Một mục hồ sơ bị trả lại — cần bổ sung", open=_case_open(case["id"]))
    if new == case["stage_key"]:
        return {"changed": False}
    order = [st["key"] for st in tpl["stages"]]
    if new is None:
        await ctx.exec("UPDATE flow.cases SET stage_key = NULL, status = 'done', closed_at = now(), stage_since = now() WHERE id = %s", (case["id"],))
        await _event(ctx, case["id"], "closed", {"from": case["stage_key"]})
        await _wake_follow(ctx, case["id"], "Hồ sơ đã đủ mọi chặng.")
        return {"changed": True, "status": "done"}
    await ctx.exec("UPDATE flow.cases SET stage_key = %s, stage_since = now() WHERE id = %s", (new, case["id"]))
    name = next(st["name"] for st in tpl["stages"] if st["key"] == new)
    if case["stage_key"] is not None and order.index(new) < order.index(case["stage_key"]):
        await _event(ctx, case["id"], "returned", {"from": case["stage_key"], "to": new})
        await _wake_follow(ctx, case["id"], f"Hồ sơ lùi về chặng “{name}”.")
        return {"changed": True, "stage": new, "returned": True}
    await _event(ctx, case["id"], "stage", {"from": case["stage_key"], "to": new})
    await _stage_tasks(ctx, case, next(st for st in tpl["stages"] if st["key"] == new))
    await _wake_follow(ctx, case["id"], f"Hồ sơ sang chặng “{name}”.")
    return {"changed": True, "stage": new}


async def _stage_tasks(ctx: Ctx, case: dict[str, Any], stage: dict[str, Any]) -> None:
    """Việc tự sinh của một chặng — không trùng khi vào lại chặng. Việc giao "người phụ trách hồ sơ" mà hồ sơ CHƯA có người lẫn đội (người phụ trách
    nghỉ…) ⇒ CHỜ, không sinh việc vô chủ; ``sync`` gọi lại khi đối tượng có người phụ trách."""
    for t in stage["tasks"]:
        if await ctx.val("SELECT 1 FROM flow.tasks WHERE case_id = %s AND stage_key = %s AND title = %s", (case["id"], stage["key"], t["title"])):
            continue   # vào lại chặng (sau khi bị trả về rồi đạt): không sinh việc trùng
        team = await _team_id(ctx, t["team_code"])
        if team is None and case["owner_id"] is None and case["team_id"] is None:
            continue
        await ctx.exec(
            """INSERT INTO flow.tasks (title, notes, assignee_id, team_id, due_on, case_id, stage_key, origin, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'stage', %s)""",
            (t["title"], case["title"], None if team else case["owner_id"], team or case["team_id"],
             today() + timedelta(days=t["due_days"]) if t["due_days"] else None, case["id"], stage["key"], ctx.p.user_id))
        await ctx.notify(await notify.team_members(ctx.conn, team) if team else [case["owner_id"]], kind="task",
                         title=f"Việc mới từ hồ sơ: {t['title']}", body=case["title"], open=_case_open(case["id"]),
                         push="Đội có việc mới từ một hồ sơ, chưa ai nhận" if team else "Anh/chị có việc mới từ một hồ sơ")


@m.subject("flow.case", "Hồ sơ")
async def subject_case(ctx: Ctx, inp: registry.SubjectRef) -> dict[str, Any]:
    """Hồ sơ như một ĐỐI TƯỢNG (trao đổi gắn vào hồ sơ — 0046). Gọi bằng quyền người hỏi ⇒ không thấy hồ sơ thì NotFound."""
    c = await ctx.one("SELECT id, title, owner_id, team_id, status FROM flow.cases WHERE id = %s", (inp.subject_id,))
    if c is None:
        raise NotFound("Không thấy hồ sơ")
    return {"title": c["title"], "owner_id": c["owner_id"], "team_id": c["team_id"],
            "state": "cancelled" if c["status"] == "cancelled" else "active", "open": _case_open(c["id"])}


class OpenFor(Input):
    subject_kind: str
    subject_id: UUID


@m.command("open_for_subject", resource="flow", title="Mở hồ sơ theo đối tượng", internal=True, callers=("flow",),
           why="Bên nghe sự kiện tạo đối tượng (module chủ đối tượng đã ghi bằng quyền người làm): mở hồ sơ theo mẫu tự mở của loại đó — "
               "ghi flow.cases cho đúng đối tượng. Trả case_id.")
async def open_for_subject(ctx: Ctx, inp: OpenFor) -> dict[str, Any]:
    tpl = await ctx.one("SELECT * FROM flow.templates WHERE subject_kind = %s AND auto_open AND is_active", (inp.subject_kind,))
    if tpl is None:
        return {"case_id": None}
    if existing := await ctx.val("SELECT id FROM flow.cases WHERE template_id IN (SELECT id FROM flow.templates WHERE key = %s)"
                                 " AND subject_kind = %s AND subject_id = %s", (tpl["key"], inp.subject_kind, inp.subject_id)):
        return {"case_id": existing}
    subj = await _subject(ctx, inp.subject_kind, inp.subject_id)
    if subj is None:
        return {"case_id": None}
    cid = await ctx.val(
        """INSERT INTO flow.cases (template_id, subject_kind, subject_id, title, owner_id, team_id, historical) VALUES (%s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",   # historical (0185): hồ sơ của đối tượng nạp từ hệ khác là tham chiếu — vòng quét "kẹt chặng" không xét
        (tpl["id"], inp.subject_kind, inp.subject_id, subj["title"], subj.get("owner_id"), subj.get("team_id"), ctx.historical))
    await _event(ctx, cid, "opened", {"template": tpl["key"], "version": tpl["version"]})
    await sync(ctx, SyncIn(case_id=cid))
    await _follow_case(ctx, {"id": cid, "title": subj["title"], "owner_id": subj.get("owner_id"), "status": "open"}, tpl)
    return {"case_id": cid}


@m.on("subject.created")
async def on_subject_created(ctx: Ctx, event: Event) -> None:
    await ctx.call_system("flow.open_for_subject", subject_kind=event.payload["kind"], subject_id=event.payload["id"])


@m.on("subject.changed")
async def on_subject_changed(ctx: Ctx, event: Event) -> None:
    for r in await ctx.all("SELECT id FROM flow.cases WHERE subject_kind = %s AND subject_id = %s AND status IN ('open', 'done')",
                           (event.payload["kind"], event.payload["id"])):
        await ctx.call_system("flow.sync", case_id=r["id"])


class OpenCase(Input):
    key: str = field("Quy trình", options_query="flow.template_options")
    title: str = field("Tên hồ sơ", max_length=200, example="Tiếp nhận khách mới — chị Hạnh")
    owner_email: str = field("Người phụ trách", "", options_query="admin.member_options", placeholder="Chính bạn")


@m.command("open_case", resource="flow", title="Mở hồ sơ")
async def open_case(ctx: Ctx, inp: OpenCase) -> dict[str, Any]:
    """Hồ sơ tự do (quy trình không bám đối tượng) — người hoặc trợ lý mở; quy trình bám đối tượng thì tự mở khi có đối tượng."""
    tpl = await _active_template(ctx, inp.key)
    if tpl["subject_kind"]:
        raise Invalid(f"Quy trình “{tpl['name']}” tự mở theo {registry.SUBJECTS[tpl['subject_kind']][0].lower()} — không mở tay")
    owner = ctx.p.user_id
    if inp.owner_email:
        owner = await ctx.val("SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()"
                              " WHERE u.email = %s AND mb.active", (inp.owner_email.lower(),))
        if owner is None:
            raise NotFound(f"Không có người {inp.owner_email}")
    team = await ctx.home_team(owner)
    cid = await ctx.val("INSERT INTO flow.cases (template_id, title, owner_id, team_id) VALUES (%s, %s, %s, %s) RETURNING id",
                        (tpl["id"], inp.title, owner, team))
    await _event(ctx, cid, "opened", {"template": tpl["key"], "version": tpl["version"]})
    await ctx.call_system("flow.sync", case_id=cid)
    await _follow_case(ctx, {"id": cid, "title": inp.title, "owner_id": owner, "status": "open"}, tpl)
    return {"case_id": cid, "summary": f"Mở hồ sơ “{inp.title}” theo {tpl['name']}"}


@m.query("template_options", resource=None, title="Quy trình")
async def template_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all("SELECT key AS value, name AS label FROM flow.templates WHERE is_active AND subject_kind = '' ORDER BY name"))


class MarkItem(Input):
    case_id: UUID = field("Hồ sơ", hidden=True)
    item_key: str = field("Mục", hidden=True)
    status: Literal["received", "verified", "rejected"] = field("Kết quả", "verified",
                                                                 options={k: ITEM_STATUS[k] for k in ("received", "verified", "rejected")})
    note: str = field("Ghi chú / lý do", "", max_length=500, help="Bắt buộc khi trả lại; khi xác nhận đạt nên ghi đã đối chiếu với gì")


@m.command("mark_item", resource="flow", title="Xác nhận mục hồ sơ",
           description="Người xác nhận một mục chưa có nguồn dữ liệu khác (bản chụp CCCD, phiếu có chữ ký). Trả lại ⇒ hồ sơ lùi về đúng chặng "
                       "của mục đó và người phụ trách nhận việc bổ sung. Trợ lý chỉ soạn đề xuất.")
async def mark_item(ctx: Ctx, inp: MarkItem) -> dict[str, Any]:
    # KHOÁ hồ sơ trước khi đọc trạng thái và ghi mục: hai người cùng bấm (xác nhận mục cuối / trả lại mục trước) chạy lần lượt, không
    # khoá chéo nhau và không để hồ sơ "xong" mà còn mục bị trả lại (review 31/10).
    case = await ctx.one("SELECT * FROM flow.cases WHERE id = %s FOR UPDATE", (inp.case_id,))
    if case is None:
        raise NotFound("Không thấy hồ sơ")
    if case["status"] != "open":
        raise Invalid("Hồ sơ đã đóng / đã huỷ")
    tpl = await ctx.one("SELECT stages FROM flow.templates WHERE id = %s", (case["template_id"],))
    assert tpl is not None
    item = next((i for st in tpl["stages"] for i in st["items"] if i["key"] == inp.item_key), None)
    if item is None:
        raise NotFound("Không có mục này trong quy trình của hồ sơ")
    if item["source"] != "manual":
        raise Invalid(f"“{item['label']}” kiểm bằng dữ kiện thật ({registry.CHECKERS.get(item['source'], ('?',))[0]}) — không xác nhận tay")
    if inp.status == "rejected" and not inp.note.strip():
        raise Invalid("Trả lại một mục cần ghi lý do")
    if not await _may_confirm(ctx, item.get("confirm", [])):
        raise Denied(f"“{item['label']}” do {await _confirm_label(ctx, item['confirm'])} xác nhận")
    await ctx.exec(
        """INSERT INTO flow.case_items (case_id, item_key, status, note, by_user) VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (case_id, item_key) DO UPDATE SET status = EXCLUDED.status, note = EXCLUDED.note, by_user = EXCLUDED.by_user, at = now()""",
        (case["id"], inp.item_key, inp.status, inp.note.strip(), ctx.p.user_id))
    await _event(ctx, case["id"], "item", {"item": inp.item_key, "label": item["label"], "status": inp.status, "note": inp.note.strip()})
    moved = await ctx.call_system("flow.sync", case_id=case["id"], rejected=inp.item_key if inp.status == "rejected" else "")
    return {"case_id": case["id"], "item": inp.item_key, "status": inp.status,
            "summary": f"{ITEM_STATUS[inp.status]}: {item['label']} — {case['title']}", "stage_changed": moved.get("changed", False)}


class CaseView(Input):
    case_id: UUID | None = field("Hồ sơ", None, hidden=True)
    subject_kind: str = field("Loại đối tượng", "", hidden=True)
    subject_id: UUID | None = field("Đối tượng", None, hidden=True)


@m.query("case", resource="flow", title="Hồ sơ theo chặng")
async def case_view(ctx: Ctx, inp: CaseView) -> dict[str, Any]:
    """Hồ sơ: đang ở chặng nào, ai giữ, còn thiếu gì, bị trả lại gì, việc đang mở. Mục riêng tư chỉ hiện số cho người có quyền."""
    if inp.case_id:
        case = await ctx.one("SELECT * FROM flow.cases WHERE id = %s", (inp.case_id,))
    else:
        case = await ctx.one("SELECT * FROM flow.cases WHERE subject_kind = %s AND subject_id = %s ORDER BY opened_at DESC LIMIT 1",
                             (inp.subject_kind, inp.subject_id))
    if case is None:
        raise NotFound("Chưa có hồ sơ (chưa có quy trình tự mở cho loại này, hoặc bạn không có quyền xem)")
    tpl = await ctx.one("SELECT * FROM flow.templates WHERE id = %s", (case["template_id"],))
    assert tpl is not None
    ev = await _evaluate(ctx, case, tpl)
    cur = next((s for s in ev["stages"] if s["key"] == ev["current"]), None)
    blocks: list[dict[str, Any]] = []
    if case["status"] == "cancelled":
        blocks.append(ui.b_callout("bad", "Hồ sơ đã huỷ", "Đối tượng của hồ sơ đã bị huỷ."))
    elif cur is None:
        blocks.append(ui.b_callout("ok", "Hồ sơ đã đủ", "Mọi chặng đã đạt."))
    else:
        days = (datetime.now(zone()) - case["stage_since"]).days if case["stage_since"] else 0
        blocks.append(ui.b_callout("warn" if ev["missing"] else "ok", f"Đang ở chặng “{cur['name']}”" + (f" — {cur['owner']} giữ" if cur["owner"] else ""),
                                   ("Còn thiếu: " + "; ".join(ev["missing"])) if ev["missing"] else "",
                                   facts=[*(f"Bị trả lại — {i['label']}: {i['note']}" for _, i in ev["rejected"] if i["note"]),
                                          *([f"Ở chặng này {days} ngày"] if days else [])]))
    blocks.append(ui.b_stages(ev["stages"]))
    tasks = await ctx.all("SELECT t.*, u.name AS who FROM flow.tasks t LEFT JOIN core.users u ON u.id = t.assignee_id"
                          " WHERE t.case_id = %s AND t.status NOT IN ('cancelled') ORDER BY t.status = 'done', t.due_on NULLS LAST", (case["id"],))
    if tasks:
        blocks.append(ui.b_list([_task_item(t) for t in tasks], "Việc của hồ sơ"))
    if talk := await ctx.call_as_optional("talk.block", ctx.p, subject_kind="flow.case", subject_id=case["id"]):   # gói Trao đổi (0046)
        blocks.append(talk)
    done = sum(1 for st in ev["stages"] if st["state"] == "done")
    actions = []
    if case["status"] == "open":
        actions = [ui.act("flow.mark_item", f"Xác nhận: {i['label']}", {"case_id": str(case["id"]), "item_key": i["key"]})
                   for st in ev["stages"] if st["state"] == "current" for i in st["items"]
                   if i["editable"] and i["status"] != "verified" and await _may_confirm(ctx, i["confirm"])][:3]
        actions.append(ui.act("flow.create_task", "Thêm việc", {"case_id": str(case["id"])}))
    return ui.card(case["title"], blocks, subtitle=f"{tpl['name']} (phiên bản {tpl['version']}) · chặng {done}/{len(ev['stages'])} đạt", icon="list.bullet.rectangle",
                   actions=actions, ask={"title": "Hỏi trợ lý về hồ sơ này", "prompt": f"Về hồ sơ “{case['title']}”: "})


# ── Canh hạn chặng (H12): hồ sơ nằm một chặng quá hạn mẫu khai ⇒ một việc "hồ sơ kẹt", và màn "Cần xử lý" xuyên phòng ban ──

# Hồ sơ đang mở nằm chặng hiện tại lâu hơn hạn mẫu khai (ngày theo giờ công ty). Dùng chung cho vòng quét và màn "Cần xử lý".
STUCK_SQL = """SELECT c.id, c.title, c.stage_key, c.owner_id, c.team_id, c.stage_since, st.value AS stage,
                      (core.today() - (c.stage_since AT TIME ZONE core.tz())::date) AS days
               FROM flow.cases c JOIN flow.templates t ON t.id = c.template_id
               CROSS JOIN LATERAL jsonb_array_elements(t.stages) st
               WHERE c.status = 'open' AND NOT c.historical AND st.value->>'key' = c.stage_key AND (st.value->>'max_days') IS NOT NULL
                 AND (core.today() - (c.stage_since AT TIME ZONE core.tz())::date) > (st.value->>'max_days')::int"""


# hồ sơ kẹt chặng quá hạn mẫu khai mà chưa có việc "hồ sơ kẹt" đang mở (H12) — chỉ công ty có hồ sơ như vậy mới chạy lệnh
@m.command("escalate_stuck", resource="flow", title="Báo hồ sơ kẹt chặng", internal=True,
           sweep="""SELECT DISTINCT c.tenant_id FROM flow.cases c JOIN flow.templates t ON t.id = c.template_id
                    CROSS JOIN LATERAL jsonb_array_elements(t.stages) st
                    WHERE c.status = 'open' AND NOT c.historical AND st.value->>'key' = c.stage_key AND (st.value->>'max_days') IS NOT NULL
                      AND c.stage_since < now() - make_interval(days => (st.value->>'max_days')::int)
                      AND NOT EXISTS (SELECT 1 FROM flow.tasks x WHERE x.case_id = c.id AND x.stage_key = c.stage_key
                                      AND x.origin = 'overdue' AND x.status IN ('todo', 'doing', 'blocked'))""")
async def escalate_stuck(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Vòng quét (quyền hệ thống): mỗi hồ sơ kẹt chặng quá hạn ⇒ MỘT việc "hồ sơ kẹt" (đội được báo, không khai ⇒ người phụ trách), ghi
    còn thiếu gì; đã có việc đang mở cho đúng (hồ sơ, chặng) ⇒ thôi (chỉ mục unique ở mig 140 giữ). Lời báo mẫu, không gọi model."""
    made = 0
    for c in await ctx.all(STUCK_SQL + """ AND NOT EXISTS (SELECT 1 FROM flow.tasks x WHERE x.case_id = c.id AND x.stage_key = c.stage_key
                                              AND x.origin = 'overdue' AND x.status IN ('todo', 'doing', 'blocked'))"""):
        case = await ctx.one("SELECT * FROM flow.cases WHERE id = %s", (c["id"],))
        tpl = await ctx.one("SELECT t.* FROM flow.templates t JOIN flow.cases c ON c.template_id = t.id WHERE c.id = %s", (c["id"],))
        assert case is not None and tpl is not None
        ev = await _evaluate(ctx, case, tpl, upto_current=True)
        st = c["stage"]
        team = await _team_id(ctx, st.get("escalate") or "")
        missing = ", ".join(ev["missing"]) or "—"
        title = f"Hồ sơ kẹt: {c['title']} — {st['name']} {c['days']} ngày (tối đa {st['max_days']})"
        await ctx.exec(
            """INSERT INTO flow.tasks (title, notes, assignee_id, team_id, due_on, case_id, stage_key, origin, created_by)
               VALUES (%s, %s, %s, %s, core.today(), %s, %s, 'overdue', %s)""",
            (title[:300], f"Còn thiếu: {missing}. Gỡ giúp hoặc ghi lý do kẹt (kéo thẻ sang “Kẹt”).", None if team else c["owner_id"],
             team or c["team_id"], c["id"], c["stage_key"], ctx.p.user_id))
        await _event(ctx, c["id"], "overdue", {"stage": c["stage_key"], "days": c["days"], "max_days": st["max_days"], "missing": ev["missing"]})
        who = await notify.team_members(ctx.conn, team) if team else [c["owner_id"]]
        await ctx.notify([u for u in who if u], kind="task", title=title[:200], body=f"Còn thiếu: {missing}", open=_case_open(c["id"]),
                         push="Có hồ sơ kẹt quá hạn chặng — cần gỡ")
        made += 1
    return {"escalated": made}


def _days(n: int) -> str:
    return f"{n} ngày" if n else "hôm nay"


@m.query("needs_action", resource="flow", title="Cần xử lý", category="core")
async def needs_action(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Việc đang VƯỚNG trong phạm vi bạn xem (của mình / đội / cả công ty theo quyền): hồ sơ kẹt chặng quá hạn (thiếu gì, chờ ai), việc quá
    hạn / đang kẹt (ai giữ, chưa ai nhận), và việc trợ lý theo dõi đang chờ người (thu tiền, nhắc khách…). Mỗi dòng mở đúng chỗ xử lý."""
    stuck = []
    for c in await ctx.all(STUCK_SQL + " ORDER BY days DESC LIMIT 50"):
        case = await ctx.one("SELECT * FROM flow.cases WHERE id = %s", (c["id"],))
        tpl = await ctx.one("SELECT t.* FROM flow.templates t JOIN flow.cases c ON c.template_id = t.id WHERE c.id = %s", (c["id"],))
        assert case is not None and tpl is not None
        ev = await _evaluate(ctx, case, tpl, upto_current=True)
        cur = next((x for x in ev["stages"] if x["key"] == ev["current"]), None)
        who = [await _confirm_label(ctx, i["confirm"]) for i in (cur["items"] if cur else [])
               if i["required"] and i["status"] != "verified" and i["confirm"]] + [c["stage"].get("owner") or ""]
        waiting_on = ", ".join(dict.fromkeys(w for w in who if w)) or "người phụ trách hồ sơ"
        stuck.append({"title": f"{c['title']} — kẹt ở {c['stage']['name']}", "badge": f"{c['days']}/{c['stage']['max_days']} ngày", "tone": "bad",
                      "subtitle": f"Thiếu: {', '.join(ev['missing']) or '—'} · chờ: {waiting_on}", "open": _case_open(c["id"])})
    t0 = today()
    tasks = await ctx.all(
        """SELECT t.id, t.title, t.status, t.due_on, t.notes, t.case_id, t.origin, u.name AS who, tm.name AS team
           FROM flow.tasks t LEFT JOIN core.users u ON u.id = t.assignee_id LEFT JOIN core.teams tm ON tm.id = t.team_id
           WHERE t.status IN ('todo', 'doing', 'blocked') AND (t.status = 'blocked' OR t.due_on < core.today())
           ORDER BY t.due_on NULLS LAST LIMIT 100""")
    task_items = [{"title": r["title"],
                   "subtitle": " · ".join(x for x in [
                       f"Kẹt: {r['notes']}" if r["status"] == "blocked" and r["notes"] else ("Kẹt" if r["status"] == "blocked" else ""),
                       f"quá hạn {_days((t0 - r['due_on']).days)}" if r["due_on"] and r["due_on"] < t0 else "",
                       f"giữ: {r['who']}" if r["who"] else f"chưa ai nhận ({r['team'] or 'đội'})"] if x),
                   "badge": "Kẹt" if r["status"] == "blocked" else "Quá hạn", "tone": "bad" if r["status"] == "blocked" else "warn",
                   "open": _case_open(r["case_id"]) if r["case_id"] else TASKS_OPEN} for r in tasks]
    follow = await ctx.call_optional("work.blockers")   # gói Tài chính tắt / không có quyền xem ⇒ không có phần này
    work_items = [{"title": w["title"], "subtitle": f"{w['note']} · phụ trách: {w['owner']}", "badge": "Cần người",
                   "tone": "warn", "open": {"query": "work.view", "input": {"item_id": str(w["id"])}}} for w in (follow or [])]
    unclaimed = sum(1 for r in tasks if not r["who"])
    blocks: list[dict[str, Any]] = [ui.b_kpis([
        ui.kpi("Hồ sơ kẹt quá hạn chặng", len(stuck), tone="red" if stuck else "green"),
        ui.kpi("Việc quá hạn / kẹt", len(tasks), tone="orange" if tasks else None, hint=f"{unclaimed} chưa ai nhận" if unclaimed else ""),
        ui.kpi("Trợ lý chờ người", len(work_items), tone="orange" if work_items else None),
    ])]
    if not (stuck or tasks or work_items):
        blocks.append(ui.b_callout("ok", "Không có gì đang vướng", "Trong phạm vi bạn xem: không hồ sơ nào quá hạn chặng, không việc quá hạn / kẹt."))
    if stuck:
        blocks.append(ui.b_list(stuck, "Hồ sơ kẹt quá hạn chặng — thiếu gì, chờ ai"))
    if work_items:
        blocks.append(ui.b_list(work_items, "Trợ lý đang chờ người quyết"))
    if task_items:
        blocks.append(ui.b_list(task_items, "Việc quá hạn / đang kẹt"))
    return ui.card("Cần xử lý", blocks, subtitle="Việc đang vướng trong phạm vi bạn xem — bấm từng dòng để xử lý", icon="exclamationmark.triangle",
                   ask={"title": "Nhờ trợ lý gỡ", "prompt": "Trong các việc đang vướng, việc nào cần làm trước và tôi nên làm gì? "})


class SubjectIn(Input):
    subject_kind: str
    subject_id: UUID


class KindIn(Input):
    subject_kind: str


@m.query("subject_states", resource="flow", title="Trạng thái hồ sơ theo loại đối tượng", internal=True)
async def subject_states(ctx: Ctx, inp: KindIn) -> dict[str, str]:
    """Cho module khác đếm / lọc nhanh: {id đối tượng: open|done|cancelled} của hồ sơ mới nhất mỗi đối tượng (quyền người gọi) — một lượt SQL,
    không tính lại chặng (cần chặng / còn thiếu ⇒ ``subject_status`` cho từng đối tượng)."""
    rows = await ctx.all("""SELECT DISTINCT ON (subject_id) subject_id, status FROM flow.cases WHERE subject_kind = %s AND subject_id IS NOT NULL
                            ORDER BY subject_id, opened_at DESC""", (inp.subject_kind,))
    return {str(r["subject_id"]): r["status"] for r in rows}


@m.query("subject_status", resource="flow", title="Tóm tắt hồ sơ của một đối tượng", internal=True, family="follow_up",
         why="Gói khai consumes(\"follow_up\") hiện hồ sơ trên hành trình của đối tượng CỦA NÓ (loại đối tượng do chính module gọi khai — ctx.owned_by; vd "
             "điểm cắm hành trình khách, crm chỉ gọi khi người xem đã thấy lead): hồ sơ mới nhất của đối tượng. Trả chặng, "
             "trạng thái, ai giữ, mục còn thiếu, nhật ký mục xác nhận / trả lại — chỉ nhãn, không số của mục riêng tư.")
async def subject_status(ctx: Ctx, inp: SubjectIn) -> dict[str, Any] | None:
    """Cho module khác (hành trình khách): chặng hiện tại, ai giữ, còn thiếu gì, và nhật ký mục được xác nhận / trả lại.
    Chỉ nhãn — không số của mục riêng tư."""
    ctx.owned_by(inp.subject_kind.split(".")[0], f"Loại đối tượng {inp.subject_kind}")   # 0186: họ mở ⇒ gói chỉ đọc hồ sơ của đối tượng nó khai
    case = await ctx.one("SELECT * FROM flow.cases WHERE subject_kind = %s AND subject_id = %s ORDER BY opened_at DESC LIMIT 1",
                         (inp.subject_kind, inp.subject_id))
    if case is None:
        return None
    tpl = await ctx.one("SELECT * FROM flow.templates WHERE id = %s", (case["template_id"],))
    assert tpl is not None
    ev = await _evaluate(ctx, case, tpl)
    cur = next((s for s in ev["stages"] if s["key"] == ev["current"]), None)
    events = await ctx.all("SELECT kind, at, data FROM flow.case_events WHERE case_id = %s AND kind = 'item' ORDER BY at, id", (case["id"],))
    return {"case_id": case["id"], "status": case["status"], "stage": ev["current"], "stage_name": cur["name"] if cur else "",
            "owner": cur["owner"] if cur else "", "missing": ev["missing"], "events": events}


# ── Trợ lý cầm hồ sơ nhiều ngày (0142): loại việc theo dõi "case" của gói nền — bộ máy ở module ``work``, điều kiện / tình hình do code ở đây ──

WORK_KIND = "case"
# bộ công cụ hẹp khi hồ sơ kẹt: xem hồ sơ + việc, giao lại / dời hạn / thêm việc, nhắc trên hồ sơ, gửi link nộp giấy tờ, tra quy trình
CASE_TOOLS = ("flow.case", "flow.tasks", "flow.adjust_task", "flow.create_task", "talk.post", "talk.digest", "asks.create", "kb.search")
CASE_RULES = ("Hãy: (1) xem hồ sơ (flow.case) và việc đang mở; (2) soạn ĐỀ XUẤT phù hợp — giao lại / dời hạn việc quá hạn (flow.adjust_task), thêm việc "
              "bổ sung (flow.create_task), nhắc trên hồ sơ (talk.post), gửi link nộp giấy tờ cho bên ngoài (asks.create) khi mục thiếu do bên ngoài nộp; "
              "(3) viết 2–3 câu báo người phụ trách: kẹt vì gì, đã soạn đề xuất gì. KHÔNG tự xác nhận mục hồ sơ, không hứa với khách, không bịa số.")


async def _follow_case(ctx: Ctx, case: dict[str, Any], tpl: dict[str, Any], *, handoff: bool = False) -> None:
    """Mẫu có ``follow`` và hồ sơ đang mở có người phụ trách ⇒ trợ lý của người đó cầm hồ sơ: một việc theo dõi loại ``case`` (``work.open``); đổi
    người phụ trách ⇒ ``work.handoff`` (việc cũ dừng, việc mới cho người mới — không việc nào chạy bằng quyền người không còn giữ hồ sơ)."""
    if not tpl.get("follow") or not case.get("owner_id") or case.get("status") != "open":
        return
    title = f"Hồ sơ: {case['title']}"[:300]
    if handoff:
        await ctx.call_system("work.handoff", kind=WORK_KIND, owner_id=case["owner_id"], subject_ref=str(case["id"]), title=title,
                              note="Người phụ trách hồ sơ đổi — trợ lý của người mới tiếp tục theo dõi.")
    else:
        await ctx.call_system("work.open", kind=WORK_KIND, owner_id=case["owner_id"], subject_ref=str(case["id"]), title=title,
                              note=f"Hồ sơ mở theo quy trình “{tpl['name']}” — trợ lý theo dõi tới khi đủ mọi chặng.")


async def _wake_follow(ctx: Ctx, case_id: UUID, text: str) -> None:
    """Hồ sơ đổi chặng / đóng / huỷ ⇒ việc theo dõi (nếu có) kiểm ngay, không đợi lần kiểm hằng ngày."""
    await ctx.call_system("work.wake", kind=WORK_KIND, subject_ref=str(case_id), text=text)


SOON_DAYS = 1   # MẶC ĐỊNH khi mẫu không ghi “nhắc trước N ngày”: hạn chặng còn ≤ N ngày mà còn thiếu mục ⇒ trợ lý nhắc SỚM (0142 lát 2)


async def _case_asks(ctx: Ctx, case_id: UUID) -> list[dict[str, Any]]:
    """Yêu cầu gửi bên ngoài (link — 0075) gắn vào hồ sơ, bằng quyền NGƯỜI PHỤ TRÁCH (ctx đang chạy bằng quyền đó). Người phụ trách không xem được
    yêu cầu ⇒ coi như không có (phần thêm không làm vỡ việc theo dõi)."""
    out = await ctx.call_optional("asks.requests", subject_kind="flow.case", subject_id=str(case_id))
    return [r for r in (out or {}).get("rows", []) if r.get("status") == "open"]


def _case_facts(stage: dict[str, Any], cur: dict[str, Any], case: dict[str, Any], tasks: list[dict[str, Any]], ev: dict[str, Any],
                asks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    t0 = today()
    days = (t0 - case["stage_since"].astimezone(zone()).date()).days
    max_days = stage.get("max_days")
    late_tasks = [t for t in tasks if t["due_on"] and t["due_on"] < t0]
    asks = asks or []
    # yêu cầu bên ngoài QUÁ HẠN = còn mục chưa gửi mà qua hạn nộp; CHỜ NHẬN = bên ngoài đã gửi, người phụ trách chưa nhận làm căn cứ
    asks_late = [a for a in asks if a.get("due_on") and a["due_on"] < t0 and a["items"] > a["answered"]]
    f: dict[str, Any] = {"stage": cur["key"], "stage_name": cur["name"], "days_in_stage": days, "max_days": max_days, "missing": ev["missing"],
                         "rejected": [i["label"] for _, i in ev["rejected"]], "tasks_open": len(tasks), "tasks_late": len(late_tasks),
                         "tasks_blocked": sum(1 for t in tasks if t["status"] == "blocked"),
                         "asks_open": len(asks), "asks_late": len(asks_late), "asks_to_review": sum(1 for a in asks if a.get("to_review")),
                         "late_days": max(max(0, days - max_days) if max_days else 0, max(((t0 - t["due_on"]).days for t in late_tasks), default=0),
                                          max(((t0 - a["due_on"]).days for a in asks_late), default=0))}
    if max_days:
        due = case["stage_since"].astimezone(zone()).date() + timedelta(days=max_days)
        f["due"] = due.isoformat()
        f["stage_soon"] = bool(ev["missing"]) and 0 <= (due - t0).days <= int(stage.get("soon_days", SOON_DAYS))
    return f


@m.work_kind(WORK_KIND, "Hồ sơ theo quy trình", subject_help="Mã hồ sơ — hồ sơ mở theo mẫu có “Trợ lý theo dõi” thì tự giao, không cần ghi", tools=CASE_TOOLS)
async def work_case(ctx: Ctx, inp: registry.WorkIn) -> dict[str, Any]:
    """Bằng quyền người phụ trách: tình hình một hồ sơ theo chặng do CODE tính — xong khi hồ sơ đủ mọi chặng, dừng khi huỷ; ngoại lệ khi quá hạn
    chặng hoặc việc của hồ sơ quá hạn. ``task`` = tình hình + lời dặn (dòng ``~``) của chặng hiện tại trong mẫu, phiên bản hồ sơ đang giữ."""
    try:
        cid = UUID(inp.subject.strip())
    except ValueError as e:
        raise Invalid("Theo dõi hồ sơ: ghi mã hồ sơ (uuid) — lấy từ trang hồ sơ") from e
    case = await ctx.one("SELECT * FROM flow.cases WHERE id = %s", (cid,))
    if case is None:
        raise NotFound("Không thấy hồ sơ (trong phạm vi bạn được xem)")
    tpl = await ctx.one("SELECT * FROM flow.templates WHERE id = %s", (case["template_id"],))
    assert tpl is not None
    ref, title = str(case["id"]), f"Hồ sơ: {case['title']}"[:300]
    if case["status"] != "open":
        done = case["status"] == "done"
        return {"ref": ref, "title": title, "state": "done" if done else "cancelled", "facts": {"status": case["status"]},
                "summary": "Hồ sơ đã đủ mọi chặng." if done else "Hồ sơ đã huỷ.",
                "closing": "Hồ sơ đã đủ mọi chặng — đóng việc." if done else "Hồ sơ đã huỷ — dừng theo dõi.", "task": "", "fallback": ""}
    ev = await _evaluate(ctx, case, tpl)
    cur = next((s for s in ev["stages"] if s["key"] == ev["current"]), None)
    stage = next((st for st in tpl["stages"] if st["key"] == ev["current"]), None)
    if cur is None or stage is None:   # mọi mục đã đạt mà sync chưa chạy tới — lần kiểm sau sẽ thấy hồ sơ đóng
        return {"ref": ref, "title": title, "state": "wait", "facts": {"status": "closing"}, "summary": "Mọi chặng đã đạt — chờ đóng hồ sơ.",
                "task": "", "fallback": ""}
    tasks = await ctx.all("""SELECT t.title, t.status, t.due_on, t.assignee_id, u.name AS who FROM flow.tasks t LEFT JOIN core.users u ON u.id = t.assignee_id
                             WHERE t.case_id = %s AND t.status IN ('todo', 'doing', 'blocked') ORDER BY t.due_on NULLS LAST""", (case["id"],))
    asks = await _case_asks(ctx, case["id"])
    f = _case_facts(stage, cur, case, tasks, ev, asks)
    t0 = today()
    # KHÔNG mang tên yêu cầu / người nhận vào lời báo: lời báo nằm ở work.items (ai thấy việc là đọc được), còn asks.requests có RLS hẹp hơn (review 09/11)
    asks_late = [a for a in asks if a.get("due_on") and a["due_on"] < t0 and a["items"] > a["answered"]]
    to_review = sum(int(a.get("to_review") or 0) for a in asks)
    asks_line = "; ".join(
        ([f"{len(asks_late)} yêu cầu bên ngoài quá hạn nộp (lâu nhất {max((t0 - a['due_on']).days for a in asks_late)} ngày)"] if asks_late else [])
        + ([f"bên ngoài đã gửi {to_review} mục chờ bạn nhận"] if to_review else []))
    late = [f"“{t['title']}” ({t['who'] or 'chưa ai nhận'}, quá hạn {(t0 - t['due_on']).days} ngày)" for t in tasks if t["due_on"] and t["due_on"] < t0]
    waiting = ", ".join(dict.fromkeys([await _confirm_label(ctx, i["confirm"]) for i in cur["items"]
                                       if i["required"] and i["status"] != "verified" and i["confirm"]] + ([stage["owner"]] if stage.get("owner") else [])))
    where = f"chặng “{cur['name']}” ngày thứ {f['days_in_stage']}" + (f" (hạn {f['max_days']} ngày)" if f["max_days"] else "")
    missing = "; ".join(f["missing"]) or "không"
    summary = (f"Ở {where} — còn thiếu: {missing}" + (f" · chờ: {waiting}" if waiting else "")
               + (f" · {len(late)} việc quá hạn" if late else "") + (f" · bị trả lại: {'; '.join(f['rejected'])}" if f["rejected"] else "")
               + (f" · yêu cầu bên ngoài: {asks_line}" if asks_line else "") + (" · sắp tới hạn chặng" if f.get("stage_soon") else "") + ".")
    guide = stage.get("guide") or []
    task = (f"Hồ sơ “{case['title']}” (quy trình {tpl['name']}) đang ở {where}.\nCòn thiếu: {missing}." + (f" Đang chờ: {waiting}." if waiting else "")
            + (f"\nViệc của hồ sơ quá hạn: {'; '.join(late)}." if late else "") + (f"\nBị trả lại: {'; '.join(f['rejected'])}." if f["rejected"] else "")
            + (f"\nYêu cầu gửi bên ngoài: {asks_line} — xem chi tiết ở màn Yêu cầu gửi bên ngoài của hồ sơ." if asks_line else "")
            + ("\nHạn chặng là hôm nay / ngày mai mà còn thiếu mục — nhắc SỚM, trước khi kẹt." if f.get("stage_soon") and not f["late_days"] else "")
            + (f"\nLời dặn của quy trình cho chặng này: {' '.join(guide)}" if guide else "") + f"\n{CASE_RULES}")
    head = "Hồ sơ kẹt ở" if f["late_days"] else "Hồ sơ cần bạn xem sớm —"
    fallback = (f"{head} {where}: còn thiếu {missing}" + (f"; chờ {waiting}" if waiting else "")
                + (f"; {len(late)} việc quá hạn ({'; '.join(late)})" if late else "") + (f"; yêu cầu bên ngoài: {asks_line}" if asks_line else "")
                + (". Hạn chặng sắp tới" if f.get("stage_soon") and not f["late_days"] else "") + ". Bạn gỡ giúp hoặc giao lại.")
    # NHẮC SỚM (0142 lát 2): chưa quá hạn mà sắp tới hạn chặng còn thiếu mục, hoặc bên ngoài đã gửi chờ người nhận ⇒ cần người, trước khi kẹt
    attention = bool(f.get("stage_soon") or f["asks_to_review"])
    return {"ref": ref, "title": title, "state": "open", "facts": f, "summary": summary, "task": task, "fallback": fallback, "attention": attention}
