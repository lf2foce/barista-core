"""TỰ ĐỘNG HOÁ "Nếu… thì…" do quản trị đặt (quyết định 0090, ROADMAP K4).

Luật là DỮ LIỆU: khi một CÒ module khai (``Module.trigger`` — sự kiện + trường an toàn) xảy ra và điều kiện trên các trường đó đúng ⇒ BÁO người /
đội, hoặc TẠO VIỆC. Giới hạn có chủ đích: chỉ cò module khai, chỉ hai hành động, chạy bằng QUYỀN NGƯỜI ĐẶT LUẬT (nạp lại mỗi lần — thôi việc / mất
quyền quản lý luật ⇒ không chạy), mỗi sự kiện một lần, trần số lần / ngày, mỗi lần có nhật ký + mã vết ``auto:<luật>`` (0086).
"""

import re
from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.api import Ctx, Denied, Event, Input, Invalid, NoInput, NotFound, field, money, notify, today, ui, zone
from barista.kernel import auth, executor, packs, registry, vocab
from barista.modules.flow import _team_id, m

OPS = {"eq": "=", "ne": "≠", "ge": "≥", "le": "≤", "contains": "chứa"}
_OP_WORDS = (("!=", "ne"), ("≠", "ne"), (">=", "ge"), ("≥", "ge"), ("<=", "le"), ("≤", "le"), ("=", "eq"), ("chứa", "contains"), ("contains", "contains"))
ACTIONS = {"notify": "Báo", "task": "Tạo việc"}
RUN_STATUS = {"done": "Đã chạy", "skipped": "Bỏ qua", "failed": "Lỗi", "trial": "Chạy thử (chưa gửi)"}
STATE = {"trial": "Chạy thử", "on": "Bật", "off": "Tắt"}

m.trigger("flow.plan_closed", "Kế hoạch đóng (xong / huỷ)", {"status": "Trạng thái"}, open=("flow.plan", {"plan_id": "plan_id"}))


# ── Hàm thuần (test thẳng) ──

def _fields(t: registry.Trigger, terms: Mapping[str, str] | None) -> dict[str, str]:
    """Trường của cò với nhãn của CÔNG TY (từ vựng, 0124): điều kiện / lời báo viết theo nhãn công ty người đặt luật."""
    return vocab.apply(t.fields, terms)


def _neutral(t: registry.Trigger) -> dict[str, str]:
    """Nhãn TRUNG TÍNH của trường — luật đặt trước khi công ty đổi nhãn (hoặc viết theo tài liệu chung) vẫn khớp."""
    return vocab.apply(t.fields)


def parse_conditions(text: str, fields: dict[str, str], also: dict[str, str] | None = None) -> list[dict[str, str]]:
    """Mỗi dòng "trường phép giá trị" — trường là MÃ hoặc NHÃN trong cò (vd ``Nguồn = ads``, ``Giá bán (đồng) >= 5000000000``, ``Nguồn chứa quảng``).
    ``also``: nhãn phụ (trung tính) cũng nhận. Trường lạ / phép lạ ⇒ ``Invalid`` nêu đúng dòng."""
    by_label = {v.lower(): k for k, v in (also or {}).items()} | {v.lower(): k for k, v in fields.items()}
    out = []
    for n, line in enumerate((x.strip() for x in text.splitlines()), 1):
        if not line:
            continue
        hit = next(((i, w, op) for w, op in _OP_WORDS if (i := line.find(w)) > 0), None)
        if hit is None:
            raise Invalid(f"Điều kiện dòng {n} “{line}”: thiếu phép so (=, ≠, ≥, ≤, chứa)")
        i, w, op = hit
        name, value = line[:i].strip(), line[i + len(w):].strip()
        key = name if name in fields else by_label.get(name.lower())
        if key is None:
            raise Invalid(f"Điều kiện dòng {n}: không có trường “{name}” — dùng: {', '.join(fields.values())}")
        out.append({"field": key, "op": op, "value": value})
    return out


def _num(v: Any) -> Decimal | None:
    try:
        return Decimal(str(v).replace(".", "").replace(",", ".")) if isinstance(v, str) else Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def matches(conditions: list[dict[str, Any]], payload: dict[str, Any]) -> bool:
    """Mọi điều kiện đều đúng (VÀ). So số khi cả hai vế là số; chữ không phân biệt hoa thường; trường thiếu ⇒ sai."""
    for c in conditions:
        got = payload.get(c["field"])
        if got is None:
            return False
        want = c["value"]
        a, b = (_num(got), _num(want)) if not isinstance(got, bool) else (None, None)
        if c["op"] in ("ge", "le"):
            if a is None or b is None or not (a >= b if c["op"] == "ge" else a <= b):
                return False
        elif c["op"] == "contains":
            if str(want).lower() not in str(got).lower():
                return False
        else:
            same = (a == b) if a is not None and b is not None else str(got).strip().lower() == str(want).strip().lower()
            if same != (c["op"] == "eq"):
                return False
    return True


def render(template: str, payload: dict[str, Any], fields: dict[str, str], also: dict[str, str] | None = None) -> str:
    """{mã trường} / {nhãn trường} ⇒ giá trị trong sự kiện (tiền: có dấu chấm). Chỉ trường cò khai — chỗ khác giữ nguyên. ``also``: nhãn phụ (trung tính)."""
    names = {**{k: k for k in fields}, **{v: k for k, v in (also or {}).items()}, **{v: k for k, v in fields.items()}}

    def sub(mt: re.Match[str]) -> str:
        key = names.get(mt.group(1).strip())
        if key is None:
            return mt.group(0)
        v = payload.get(key)
        if isinstance(v, bool):
            return "có" if v else "không"
        return money(int(v)) if isinstance(v, int | float | Decimal) and "(đồng)" in fields[key] else ("" if v is None else str(v))
    return re.sub(r"\{([^{}]+)\}", sub, template)


def describe(conditions: list[dict[str, Any]], fields: dict[str, str]) -> str:
    return " và ".join(f"{fields.get(c['field'], c['field'])} {OPS[c['op']]} {c['value']}" for c in conditions) or "luôn"


# ── Đặt luật ──

@m.query("trigger_options", resource="flow.automations", title="Cò tự động hoá dùng được")
async def trigger_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Cò của các gói công ty đang bật — nhãn kèm trường đặt điều kiện được."""
    on = await packs.enabled(ctx.conn, ctx.p.tenant_id)
    return [{"value": t.event_type, "label": f"{t.label} — trường: {', '.join(t.fields.values())}"}
            for t in sorted(registry.TRIGGERS.values(), key=lambda t: t.label) if packs.module_on(on, t.module)]


class SaveAutomation(Input):
    automation_id: UUID | None = field("Luật", None, hidden=True)
    name: str = field("Tên luật", max_length=120, example="Lead mới từ nguồn ngoài ⇒ báo trưởng team A")
    trigger: str = field("Khi", options_query="flow.trigger_options")
    conditions: str = field("Và điều kiện", "", kind="textarea", max_length=1000, example="[[offering]] quan tâm = HSR\nNguồn = ads",
                            help="Mỗi dòng: trường phép giá trị (=, ≠, ≥, ≤, chứa). Trống = mọi lần cò xảy ra.")
    action: Literal["notify", "task"] = field("Thì", "notify", options=ACTIONS)
    team_code: str = field("Cho đội", "", options_query="admin.team_options")
    leads_only: bool = field("Chỉ trưởng đội", False, group="Thêm chi tiết")
    user_email: str = field("Hoặc cho một người", "", options_query="admin.member_options", group="Thêm chi tiết")
    message: str = field("Nội dung", kind="textarea", max_length=500, example="Lead mới {[[offering]] quan tâm} (nguồn {Nguồn}) — gọi trong 15 phút",
                         help="{tên trường} lấy giá trị từ sự kiện")
    due_days: int | None = field("Hạn việc (sau … ngày)", None, ge=0, le=60, group="Thêm chi tiết")
    daily_cap: int = field("Tối đa mỗi ngày", 20, ge=1, le=500, help="Chặn bão thông báo / việc khi dữ liệu nhập ồ ạt", group="Thêm chi tiết")
    trial: bool = field("Chạy thử trước (chỉ ghi, chưa gửi)", True,
                        help="Khớp sự kiện thì chỉ ghi “sẽ báo ai / sẽ tạo việc gì” — xem các lần khớp rồi bấm “Chạy thật”", group="Thêm chi tiết")


@m.command("save_automation", resource="flow.automations", action="manage", title="Đặt luật tự động", agent="propose")
async def save_automation(ctx: Ctx, inp: SaveAutomation) -> dict[str, Any]:
    """Luật "NẾU … THÌ …": khi một việc xảy ra (lead mới, ký hợp đồng, khoản thu quá hạn, có tiền về…) thì BÁO / nhắc ai đó hoặc TẠO VIỆC —
    lấy cò + trường dùng được bằng flow__trigger_options trước. KHÔNG phải luật giao lead (lead mới vào tay ai ⇒ leadflow__set_rule). Luật mới chạy
    thử trước; chạy bằng QUYỀN NGƯỜI ĐẶT (người lưu sau cùng). Trợ lý chỉ đề xuất — người bấm mới lưu."""
    t = registry.TRIGGERS.get(inp.trigger)
    if t is None or not packs.module_on(await packs.enabled(ctx.conn, ctx.p.tenant_id), t.module):
        # nói luôn cò nào dùng được (mã — nhãn — trường): người / trợ lý sửa trong MỘT bước, không đoán lại (eval 0095: đoán 3 lần rồi bỏ cuộc)
        valid = "; ".join(f"{o['value']} = {o['label']}" for o in await trigger_options(ctx, NoInput()))
        raise Invalid(f"Cò “{inp.trigger}” không có hoặc gói của nó đang tắt — dùng một trong: {valid}")
    fields, also = _fields(t, ctx.p.vocab), _neutral(t)
    conds = parse_conditions(inp.conditions, fields, also)
    known = {*fields, *fields.values(), *also.values()}
    if bad := [x for x in re.findall(r"\{([^{}]+)\}", inp.message) if x.strip() not in known]:
        raise Invalid(f"Nội dung dùng trường không có trong cò: {', '.join(bad)} — dùng: {', '.join(fields.values())}")
    team = await _team_id(ctx, inp.team_code) if inp.team_code.strip() else None
    if inp.team_code.strip() and team is None:
        raise NotFound(f"Không có đội {inp.team_code}")
    user = None
    if inp.user_email.strip():
        user = await ctx.val("""SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()
                                WHERE u.email = %s AND mb.active""", (inp.user_email.strip().lower(),))
        if user is None:
            raise NotFound(f"Không có người {inp.user_email} trong công ty")
    if (team is None) == (user is None):
        raise Invalid("Chọn MỘT đích: một đội hoặc một người")
    row = (inp.name.strip(), inp.trigger, Jsonb(conds), inp.action, team, inp.leads_only and team is not None, user, inp.message.strip(),
           inp.due_days if inp.action == "task" else None, inp.daily_cap, inp.trial)
    if inp.automation_id:
        aid = await ctx.val("""UPDATE flow.automations SET name = %s, trigger = %s, conditions = %s, action = %s, team_id = %s, leads_only = %s,
                                   user_id = %s, message = %s, due_days = %s, daily_cap = %s, trial = %s, owner_id = core.me(), updated_at = now()
                               WHERE id = %s RETURNING id""", (*row, inp.automation_id))
        if aid is None:
            raise NotFound("Không thấy luật")
    else:
        aid = await ctx.val("""INSERT INTO flow.automations (name, trigger, conditions, action, team_id, leads_only, user_id, message, due_days,
                                                             daily_cap, trial, owner_id)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, core.me()) RETURNING id""", row)
    return {"automation_id": aid, "summary": f"Luật “{inp.name.strip()}”: khi {t.label.lower()} ({describe(conds, fields)}) ⇒ "
                                            f"{ACTIONS[inp.action].lower()}" + (" — đang CHẠY THỬ (chỉ ghi, chưa gửi)" if inp.trial else "")}


class AutomationRef(Input):
    automation_id: UUID = field("Luật", hidden=True)


class SetActive(Input):
    automation_id: UUID = field("Luật", hidden=True)
    active: bool | None = field("Đang bật", None)
    trial: bool | None = field("Chạy thử", None, help="Tắt chạy thử = luật bắt đầu gửi / tạo việc thật")


@m.command("set_automation", resource="flow.automations", action="manage", title="Bật / tắt / chạy thật luật tự động", agent="propose")
async def set_automation(ctx: Ctx, inp: SetActive) -> dict[str, Any]:
    """Chạy thật cần người quản lý luật bấm (trợ lý chỉ đề xuất); người bấm thành người đặt luật — luật chạy bằng quyền người đó."""
    r = await ctx.one("""UPDATE flow.automations SET active = coalesce(%s, active), trial = coalesce(%s, trial), owner_id = core.me(), updated_at = now()
                         WHERE id = %s RETURNING name, active, trial""", (inp.active, inp.trial, inp.automation_id))
    if r is None:
        raise NotFound("Không thấy luật")
    return {"summary": f"Luật “{r['name']}”: {STATE['off' if not r['active'] else 'trial' if r['trial'] else 'on'].lower()}"}


# ── Xem ──

async def _target(ctx: Ctx, r: dict[str, Any]) -> str:
    if r["user_id"]:
        return str(await ctx.val("SELECT name FROM core.users WHERE id = %s", (r["user_id"],)) or "—")
    team = await ctx.val("SELECT name FROM core.teams WHERE id = %s", (r["team_id"],))
    return f"{'trưởng ' if r['leads_only'] else ''}{team or '—'}"


@m.query("automations", resource="flow.automations", title="Luật tự động")
async def automations(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("""SELECT a.*, u.name AS owner,
                                   (SELECT count(*) FROM flow.automation_runs r WHERE r.automation_id = a.id AND r.status = 'done'
                                     AND r.created_at >= core.today()::timestamptz) AS today,
                                   (SELECT r.status FROM flow.automation_runs r WHERE r.automation_id = a.id ORDER BY r.id DESC LIMIT 1) AS last
                            FROM flow.automations a JOIN core.users u ON u.id = a.owner_id ORDER BY a.active DESC, a.name""")
    out = []
    for r in rows:
        t = registry.TRIGGERS.get(r["trigger"])
        fields = _fields(t, ctx.p.vocab) if t else {}
        out.append({"id": r["id"], "name": r["name"], "when": t.label if t else f"{r['trigger']} (gói tắt / cò bỏ)",
                    "if": describe(r["conditions"], fields), "then": f"{ACTIONS[r['action']]} {await _target(ctx, r)}",
                    "owner": r["owner"], "today": f"{r['today']}/{r['daily_cap']}", "last": r["last"] or "",
                    "active": "off" if not r["active"] else "trial" if r["trial"] else "on"})
    return ui.table(out, [ui.col("active", "", "badge", labels=STATE, tones={"on": "green", "off": "gray", "trial": "orange"}),
                          ui.col("name", "Luật"), ui.col("when", "Khi"), ui.col("if", "Và"), ui.col("then", "Thì"), ui.col("owner", "Chạy bằng quyền"),
                          ui.col("today", "Hôm nay"), ui.col("last", "Lần gần nhất", "badge", labels=RUN_STATUS,
                                                            tones={"done": "green", "skipped": "gray", "failed": "red", "trial": "orange"})],
                    title="Luật tự động", empty="Chưa có luật — “Đặt luật”: khi có lead mới / ký hợp đồng / tiền về… thì báo người hoặc tạo việc",
                    note="Luật mới CHẠY THỬ (chỉ ghi, chưa gửi) tới khi bấm “Chạy thật”; chạy bằng quyền người đặt; mỗi sự kiện một lần; quá trần mỗi ngày "
                         "thì bỏ qua (có ghi).")


@m.query("automation", resource="flow.automations", title="Luật tự động hoá")
async def automation(ctx: Ctx, inp: AutomationRef) -> dict[str, Any]:
    r = await ctx.one("SELECT a.*, u.name AS owner FROM flow.automations a JOIN core.users u ON u.id = a.owner_id WHERE a.id = %s", (inp.automation_id,))
    if r is None:
        raise NotFound("Không thấy luật")
    t = registry.TRIGGERS.get(r["trigger"])
    fields = _fields(t, ctx.p.vocab) if t else {}
    runs = await ctx.all("SELECT status, note, created_at FROM flow.automation_runs WHERE automation_id = %s ORDER BY id DESC LIMIT 20", (r["id"],))
    blocks = [ui.b_callout("muted", f"Khi {t.label.lower() if t else r['trigger']} và {describe(r['conditions'], fields)}",
                           f"Thì {ACTIONS[r['action']].lower()} {await _target(ctx, r)}: “{r['message']}”"
                           + (f" — hạn sau {r['due_days']} ngày" if r["due_days"] is not None else ""),
                           facts=[f"Chạy bằng quyền {r['owner']}", f"Tối đa {r['daily_cap']} lần / ngày"]),
              ui.b_list([{"title": RUN_STATUS[x["status"]], "subtitle": f"{x['created_at'].astimezone(zone()):%d/%m %H:%M}" + (f" · {x['note']}" if x["note"] else ""),
                          "tone": {"done": "ok", "skipped": "muted", "failed": "bad", "trial": "warn"}[x["status"]]} for x in runs], "Các lần chạy gần đây")]
    if r["trial"] and r["active"]:
        n = sum(1 for x in runs if x["status"] == "trial")
        blocks.insert(0, ui.b_callout("warn", "Đang chạy thử — chưa gửi gì, chưa tạo việc nào",
                                      f"{n} lần khớp gần đây (xem bên dưới: sẽ báo ai, nội dung gì). Đúng ý thì bấm “Chạy thật”."))
    edit = {"automation_id": str(r["id"]), "name": r["name"], "trigger": r["trigger"], "action": r["action"], "message": r["message"],
            "conditions": "\n".join(f"{fields.get(c['field'], c['field'])} {OPS[c['op']]} {c['value']}" for c in r["conditions"]),
            "leads_only": r["leads_only"], "daily_cap": r["daily_cap"]}
    state = "off" if not r["active"] else "trial" if r["trial"] else "on"
    actions = [ui.act("flow.set_automation", "Chạy thật", {"automation_id": str(r["id"]), "trial": False}, "primary")] if state == "trial" else []
    actions += [ui.act("flow.save_automation", "Sửa luật", {**edit, "trial": r["trial"]}),
                ui.act("flow.set_automation", "Tắt luật" if r["active"] else "Bật luật", {"automation_id": str(r["id"]), "active": not r["active"]},
                       "destructive" if r["active"] else "default")]
    return ui.card(r["name"], blocks, subtitle=STATE[state], icon="bolt.fill", actions=actions)


# ── Chạy ──

async def _record(ctx: Ctx, rule: UUID, event_id: int, status: str, note: str = "") -> None:
    await ctx.exec("INSERT INTO flow.automation_runs (automation_id, event_id, status, note) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                   (rule, event_id, status, note[:500]))


@m.on_trigger()
async def run_automations(ctx: Ctx, event: Event) -> None:
    """Mỗi cò ⇒ các luật đang bật của cò đó: điều kiện đúng ⇒ (chưa chạy với sự kiện này, chưa chạm trần, người đặt luật còn quyền) hành động
    bằng quyền NGƯỜI ĐẶT LUẬT. Lỗi nghiệp vụ (mất quyền với đội, đội đã bỏ…) ⇒ ghi "Lỗi", không thử lại vô hạn."""
    if ctx.historical:   # sự kiện của một lần nạp lịch sử (0185) không kích luật tự động hoá (luật gửi thông báo / tạo việc ngoài ``ctx.notify``)
        return
    t = registry.TRIGGERS[event.type]
    payload = {k: event.payload.get(k) for k in t.fields}
    terms = await vocab.terms(ctx.conn, ctx.p.tenant_id)   # worker chạy bằng quyền hệ thống (không mang nhãn công ty) ⇒ nạp nhãn của công ty này
    rules = await ctx.all("SELECT * FROM flow.automations WHERE active AND trigger = %s ORDER BY created_at", (event.type,))
    for r in rules:
        if event.at is not None and event.at < r["created_at"]:
            continue   # luật chỉ áp cho sự kiện xảy ra SAU khi đặt (sự kiện tồn / giao muộn không kích luật mới)
        if not matches(r["conditions"], payload):
            continue
        if await ctx.val("SELECT 1 FROM flow.automation_runs WHERE automation_id = %s AND event_id = %s", (r["id"], event.id)):
            continue   # sự kiện giao lại ⇒ không chạy lần hai
        done_today = await ctx.val("""SELECT count(*) FROM flow.automation_runs WHERE automation_id = %s AND status IN ('done', 'trial')
                                      AND created_at >= core.today()::timestamptz""", (r["id"],))
        if (done_today or 0) >= r["daily_cap"]:
            await _record(ctx, r["id"], event.id, "skipped", f"Đã chạm trần {r['daily_cap']} lần hôm nay")
            continue
        # động cơ tự động hoá (gói nền) CHẠY LỆNH bằng quyền người đặt luật ⇒ nạp như lõi (CORE), không qua principal_of (chỉ để hỏi hộ — 0130)
        owner = await auth.load(ctx.conn, ctx.p.tenant_id, user_id=r["owner_id"])
        if owner is None or not owner.can("flow.automations", "manage"):
            await _record(ctx, r["id"], event.id, "skipped", "Người đặt luật không còn hoạt động / không còn quyền quản lý luật — đặt lại luật")
            continue
        text = render(r["message"], payload, _fields(t, terms), _neutral(t))
        if r["trial"]:   # chạy thử: ghi đúng thứ SẼ làm (người nhận / đích việc + nội dung), không gửi, không tạo
            who = await _target(ctx, r)
            await _record(ctx, r["id"], event.id, "trial", f"Sẽ {'báo' if r['action'] == 'notify' else 'tạo việc cho'} {who}: “{text}”")
            continue
        open_ = {"query": t.open[0], "input": {k: event.payload.get(v) for k, v in t.open[1].items()}} if t.open else None
        try:
            with executor.tracing(f"auto:{r['id']}"):
                if r["action"] == "notify":
                    users = [r["user_id"]] if r["user_id"] else await notify.team_members(ctx.conn, r["team_id"])
                    if r["leads_only"] and r["team_id"]:
                        leads = {x["user_id"] for x in await ctx.all("SELECT user_id FROM core.team_members WHERE team_id = %s AND is_lead",
                                                                    (r["team_id"],))}
                        users = [u for u in users if u in leads]
                    await notify.send(ctx.conn, users, kind="automation", title=r["name"], body=text, push=text[:120], open=open_,
                                      key=f"auto:{r['id']}:{event.id}", actor=None)
                else:
                    # lệnh CẤP NGOÀI bằng quyền người đặt luật: có nhật ký + mã vết; khoá chống lặp theo (luật, sự kiện) ⇒ giao lại không thành hai việc
                    team_code = await ctx.val("SELECT code FROM core.teams WHERE id = %s", (r["team_id"],)) if r["team_id"] else ""
                    email = await ctx.val("SELECT email FROM core.users WHERE id = %s", (r["user_id"],)) if r["user_id"] else ""
                    res = await executor.run(owner, "flow.create_task", {
                        "title": text[:300], "notes": f"Tạo bởi luật tự động hoá “{r['name']}”", "team_code": team_code or "", "assignee_email": email or "",
                        "due_on": (today() + timedelta(days=r["due_days"])).isoformat() if r["due_days"] is not None else None},
                        key=f"auto:{r['id']}:{event.id}")
                    if not res.ok:
                        raise Invalid(res.error or res.status)
        except (Invalid, Denied, NotFound) as e:
            await _record(ctx, r["id"], event.id, "failed", str(e))
            continue
        await _record(ctx, r["id"], event.id, "done")
