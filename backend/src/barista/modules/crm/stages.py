"""Chặng của lead là DỮ LIỆU công ty (0153): "quan tâm", "đã gặp", "cọc thiện chí"… là chặng thật (lọc, đếm, kéo trên bảng), không phải chữ trong
ghi chú. Mỗi chặng thuộc MỘT giai đoạn cố định của máy trạng thái (Lead · Tiềm năng · Giao dịch · Mất) — code chỉ ép giai đoạn (ai được đổi, đổi
sang đâu: Giao dịch chỉ do tạo giao dịch thật, Mất cần lý do), công ty đặt tên / thứ tự / bật tắt chặng. DB ép chặng thuộc đúng giai đoạn của
lead (khoá ngoại ``(tenant, pipeline_stage, stage)``). Bộ mặc định đi kèm gói (``Pack(records=…)``), cài qua ``admin.install_defaults``.

"Đang thu hồi" KHÔNG là chặng: đó là trạng thái chia lead (``leadflow.lead_state``) do vòng quét giữ — chặng người tự đặt tay chính là cách hệ cũ
biến thu hồi thành ngõ cụt."""

import re
from typing import Any, Literal

from barista.api import Ctx, Denied, Input, Invalid, NoInput, NotFound, field, fold, ui
from barista.modules.crm import STAGE_TONES, STAGES, m

PHASES = {k: STAGES[k] for k in ("lead", "prospect", "deal", "lost")}
TONES = {"": "Theo giai đoạn", "blue": "Xanh dương", "purple": "Tím", "green": "Xanh lá", "gray": "Xám", "orange": "Cam", "red": "Đỏ", "teal": "Xanh ngọc"}
SETTABLE = ("lead", "prospect", "lost")
KEY = re.compile(r"[a-z][a-z0-9_]{1,39}")   # giai đoạn người đặt tay được — Giao dịch chỉ đến từ việc tạo giao dịch thật


def code_from_name(name: str, taken: set[str] | frozenset[str] = frozenset(), limit: int = 30) -> str:
    """Mã do MÁY đặt từ tên (ADR 0225): chữ HOA không dấu, số, gạch nối; trùng ``taken`` ⇒ -2, -3… (cùng luật ``kernel/text.code_from_name`` — module không
    import ``barista.kernel``; bỏ bản này khi ``barista.api`` xuất hàm đó)."""
    words = re.findall(r"[a-z0-9]+", fold(name))
    base = ""
    for w in words:
        nxt = f"{base}-{w}" if base else w
        if len(nxt) > limit - 3:   # chừa chỗ cho hậu tố -NN
            break
        base = nxt
    base = (base or (words[0][: limit - 3] if words else "ma")).upper()
    code, i = base, 2
    while code in taken:
        code, i = f"{base}-{i}", i + 1
    return code


def _stage_key(label: str, taken: set[str]) -> str:
    base = code_from_name(label, limit=40).lower().replace("-", "_")
    base = base if base[:1].isalpha() else f"stage_{base}"
    key, i = base, 2
    while key in taken:
        key, i = f"{base}_{i}", i + 1
    return key


class SaveStage(Input):
    label: str = field("Tên chặng", example="Quan tâm", max_length=60)
    key: str = field("Mã chặng", "", example="interested", max_length=40, placeholder="Máy đặt từ tên chặng", group="Thêm chi tiết",
                     help="Trống = máy đặt từ tên chặng (chặng trùng tên đã có ⇒ sửa chặng đó). Chữ thường không dấu, gạch dưới — không đổi được sau khi tạo")
    phase: Literal["lead", "prospect", "deal", "lost"] = field("Thuộc giai đoạn", "prospect", options=PHASES,
                                                             help="Giai đoạn quyết lead còn đang theo, đã thành giao dịch hay đã mất — không đổi được sau khi tạo")
    sort: int = field("Thứ tự", 100, ge=0, le=999, help="Chặng thứ tự nhỏ nhất của giai đoạn là chặng mặc định khi lead vào giai đoạn đó")
    tone: Literal["", "blue", "purple", "green", "gray", "orange", "red", "teal"] = field("Màu", "", options=TONES)
    active: bool = field("Đang dùng", True, help="Tắt = không chọn được nữa; lead đang ở chặng này giữ nguyên")
    # ADR 0246: lead nằm ở chặng quá ngần này ngày (không chuyển chặng, không chăm sóc) ⇒ "nằm im" trên danh sách / thẻ / Báo cáo chạy song song
    idle_days: int | None = field("Nằm im tối đa (ngày)", None, ge=0, le=365, placeholder="Giữ nguyên",
                                  help="Lead ở chặng này quá ngần này ngày mà không chuyển chặng, không được chăm sóc ⇒ báo \"nằm im\". 0 = bỏ ngưỡng; trống = giữ nguyên")
    only_new: bool = field("Chỉ thêm nếu chưa có", False, hidden=True)


@m.command("save_stage", resource="crm", action="manage", title="Thêm / sửa chặng của lead", category="setup", importable=True, agent="propose")
async def save_stage(ctx: Ctx, inp: SaveStage) -> dict[str, Any]:
    """Chặng là cấu hình cả công ty (đếm phễu, báo cáo dùng chung) ⇒ cần quyền quản lý Khách hàng phạm vi cả công ty. Giai đoạn của một chặng cố
    định (lead đang ở chặng đó không được đổi nghĩa ngầm)."""
    if not ctx.p.has_scope("crm", "all"):
        raise Denied("Chặng của lead là cấu hình cả công ty — cần quyền quản lý Khách hàng phạm vi cả công ty")
    if not inp.key.strip():   # máy đặt mã từ tên (0225): chặng trùng tên đã có ⇒ sửa chặng đó
        inp.key = await ctx.val("SELECT key FROM crm.stages WHERE core.vn_fold(btrim(label)) = core.vn_fold(btrim(%s)) LIMIT 1", (inp.label,)) or \
            _stage_key(inp.label, {r["key"] for r in await ctx.all("SELECT key FROM crm.stages")})
    if not KEY.fullmatch(inp.key):
        raise Invalid("Mã chặng: chữ thường không dấu, số, gạch dưới; bắt đầu bằng chữ (vd interested)")
    have = await ctx.one("SELECT phase FROM crm.stages WHERE key = %s", (inp.key,))
    if have is not None and inp.only_new:
        return {"key": inp.key, "added": False}
    if have is not None and have["phase"] != inp.phase:
        raise Invalid(f"Chặng {inp.key} thuộc giai đoạn {PHASES[have['phase']]} — không đổi giai đoạn được; tạo chặng mới")
    if not inp.active and have is not None and have["phase"] in SETTABLE and not await ctx.val(
            "SELECT 1 FROM crm.stages WHERE phase = %s AND active AND key <> %s", (have["phase"], inp.key)):
        raise Invalid(f"Giai đoạn {PHASES[have['phase']]} cần ít nhất một chặng đang dùng")
    await ctx.exec(
        """INSERT INTO crm.stages (key, label, phase, sort, tone, active, idle_days, updated_by) VALUES (%(k)s, %(l)s, %(p)s, %(s)s, %(t)s, %(a)s, nullif(%(i)s::int, 0), core.me())
           ON CONFLICT (tenant_id, key) DO UPDATE SET label = EXCLUDED.label, sort = EXCLUDED.sort, tone = EXCLUDED.tone, active = EXCLUDED.active,
               idle_days = CASE WHEN %(i)s::int IS NULL THEN crm.stages.idle_days ELSE EXCLUDED.idle_days END,
               updated_by = EXCLUDED.updated_by, updated_at = now()""",
        {"k": inp.key, "l": inp.label.strip(), "p": inp.phase, "s": inp.sort, "t": inp.tone, "a": inp.active, "i": inp.idle_days})
    idle = f" · nằm im tối đa {inp.idle_days} ngày" if inp.idle_days else (" · bỏ ngưỡng nằm im" if inp.idle_days == 0 else "")
    return {"key": inp.key, "added": have is None, "summary": f"Chặng “{inp.label.strip()}” · giai đoạn {PHASES[inp.phase]}{idle}"}


@m.query("stages", resource="crm", title="Chặng của lead")
async def stages(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("""SELECT s.key AS id, s.key, s.label, s.phase, s.sort, s.tone, s.active, s.idle_days, CASE WHEN s.active THEN 'on' ELSE 'off' END AS state,
                                   (SELECT count(*) FROM crm.leads l WHERE l.pipeline_stage = s.key) AS leads
                            FROM crm.stages s ORDER BY array_position(ARRAY['lead', 'prospect', 'deal', 'lost'], s.phase), s.sort, s.key""")
    return ui.table(rows, [
        ui.col("label", "Chặng"), ui.col("phase", "Giai đoạn", "badge", labels=PHASES, tones=STAGE_TONES), ui.col("sort", "Thứ tự", "number"),
        ui.col("state", "", "badge", labels={"on": "Đang dùng", "off": "Đã tắt"}, tones={"on": "green", "off": "gray"}), ui.col("leads", "Lead đang ở", "number"),
        ui.col("idle_days", "Nằm im tối đa (ngày)", "number"), ui.col("key", "Mã"),
    ], title="Chặng của lead", empty="Chưa có chặng — lead hiện theo giai đoạn (Lead / Tiềm năng / Giao dịch / Mất)",
        empty_actions=[ui.act("crm.save_stage", "Thêm chặng", style="primary")])


async def stage_rows(ctx: Ctx) -> list[dict[str, Any]]:
    return await ctx.all("SELECT key, label, phase, sort, tone, active, idle_days FROM crm.stages ORDER BY array_position(ARRAY['lead', 'prospect', 'deal', 'lost'], phase), sort, key")


@m.query("stage_options", resource="crm", title="Chặng chuyển được")
async def stage_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Chặng người chọn được khi chuyển chặng: chặng đang dùng của Lead / Tiềm năng / Mất; công ty chưa có chặng ⇒ ba giai đoạn."""
    rows = [r for r in await stage_rows(ctx) if r["active"] and r["phase"] in SETTABLE]
    if not rows:
        return [{"value": k, "label": STAGES[k]} for k in SETTABLE]
    return [{"value": r["key"], "label": f"{r['label']} ({STAGES[r['phase']]})"} for r in rows]


async def resolve(ctx: Ctx, key: str) -> tuple[str, str | None]:
    """Chặng người chọn ⇒ (giai đoạn, chặng). Mã giai đoạn ⇒ chặng đầu đang dùng của giai đoạn đó (công ty chưa có chặng ⇒ trống)."""
    if key in SETTABLE:
        first = await ctx.val("SELECT key FROM crm.stages WHERE phase = %s AND active ORDER BY sort, key LIMIT 1", (key,))
        return key, first
    row = await ctx.one("SELECT phase, active FROM crm.stages WHERE key = %s", (key,))
    if row is None or not row["active"]:
        raise NotFound(f"Không có chặng {key} (hoặc đã tắt)")
    if row["phase"] not in SETTABLE:
        raise Invalid("Chặng thuộc giai đoạn Giao dịch chỉ đến từ việc tạo giao dịch thật — không đặt tay")
    return row["phase"], key


async def labels(ctx: Ctx) -> dict[str, str]:
    """Nhãn cho cột chặng: chặng của công ty + tên giai đoạn (lead chưa có chặng hiện theo giai đoạn)."""
    return {**STAGES, **{r["key"]: r["label"] for r in await stage_rows(ctx)}}


async def tones(ctx: Ctx) -> dict[str, str]:
    return {**STAGE_TONES, **{r["key"]: r["tone"] or STAGE_TONES[r["phase"]] for r in await stage_rows(ctx)}}


# ── Chuyển chặng được phép (ADR 0264): công ty khai cặp (từ → sang) + vai; không khai cặp nào ⇒ như cũ, mọi chặng chuyển được ──

class SetStageMove(Input):
    from_stage: str = field("Từ chặng", options_query="crm.stage_options")
    to_stage: str = field("Sang chặng", options_query="crm.stage_options")
    roles: list[str] = field("Vai được chuyển", [], options_query="admin.role_options", help="Trống = mọi vai được chuyển cặp chặng này")
    active: bool = field("Đang dùng", True, help="Tắt = bỏ cặp này (giữ dòng để tra cứu). Công ty không còn cặp nào đang dùng ⇒ chuyển chặng tự do như cũ")


@m.command("set_stage_move",
           purpose="Khai cặp chặng được phép chuyển (và vai nào được chuyển), để lead không bị chuyển chặng bừa làm sai phễu.",
           reversible="Chạy lại lệnh với giá trị cũ hoặc tắt cặp chặng",
           resource="crm", action="manage", title="Thêm / sửa chuyển chặng được phép", category="setup", importable=True, agent="propose", risk="authority")
async def set_stage_move(ctx: Ctx, inp: SetStageMove) -> dict[str, Any]:
    """Ma trận chuyển chặng là cấu hình cả công ty (ADR 0264). Khai ít nhất một cặp ⇒ ``crm.set_stage`` (kể cả luật tự động và bước tự chuyển chặng khi
    liên hệ lần đầu) chỉ cho các cặp đã khai, đúng vai. Chặng ghi bằng mã chặng của công ty hoặc mã giai đoạn (lead / prospect / lost)."""
    if not ctx.p.has_scope("crm", "all"):
        raise Denied("Chuyển chặng được phép là cấu hình cả công ty — cần quyền quản lý Khách hàng phạm vi cả công ty")
    known = {r["key"] for r in await stage_rows(ctx)} | set(PHASES)
    for k in (inp.from_stage, inp.to_stage):
        if k not in known:
            raise NotFound(f"Không có chặng {k}")
    if inp.from_stage == inp.to_stage:
        raise Invalid("Chặng đi và chặng đến phải khác nhau")
    roles = sorted(set(inp.roles))
    if bad := sorted(set(roles) - {r["key"] for r in await ctx.all("SELECT key FROM core.roles")}):
        raise Invalid(f"Không có vai: {', '.join(bad)}")
    await ctx.exec(
        """INSERT INTO crm.stage_moves (from_stage, to_stage, roles, active, updated_by) VALUES (%s, %s, %s, %s, core.me())
           ON CONFLICT (tenant_id, from_stage, to_stage) DO UPDATE SET roles = EXCLUDED.roles, active = EXCLUDED.active, updated_by = EXCLUDED.updated_by,
               updated_at = now()""", (inp.from_stage, inp.to_stage, roles, inp.active))
    names = await labels(ctx)
    who = ", ".join(roles) if roles else "mọi vai"
    return {"from_stage": inp.from_stage, "to_stage": inp.to_stage,
            "summary": f"{names.get(inp.from_stage, inp.from_stage)} → {names.get(inp.to_stage, inp.to_stage)}: "
                       + (f"được chuyển ({who})" if inp.active else "đã bỏ")}


@m.query("stage_moves", resource="crm", title="Chuyển chặng được phép")
async def stage_moves(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    names = await labels(ctx)
    rows = await ctx.all("SELECT from_stage, to_stage, roles, active, updated_at FROM crm.stage_moves ORDER BY from_stage, to_stage")
    for r in rows:
        r["id"] = f"{r['from_stage']}:{r['to_stage']}"
        r["from_label"], r["to_label"] = names.get(r["from_stage"], r["from_stage"]), names.get(r["to_stage"], r["to_stage"])
        r["who"] = ", ".join(r["roles"]) or "Mọi vai"
        r["state"] = "on" if r["active"] else "off"
    return ui.table(rows, [
        ui.col("from_label", "Từ chặng"), ui.col("to_label", "Sang chặng"), ui.col("who", "Vai được chuyển"),
        ui.col("state", "", "badge", labels={"on": "Đang dùng", "off": "Đã bỏ"}, tones={"on": "green", "off": "gray"}),
        ui.col("updated_at", "Cập nhật", "datetime"),
    ], title="Chuyển chặng được phép", empty="Chưa khai cặp nào — mọi chặng chuyển được (như cũ)",
        empty_actions=[ui.act("crm.set_stage_move", "Thêm cặp chặng", style="primary")])


async def move_block(ctx: Ctx, frm: str, to: str) -> str:
    """Câu từ chối nếu công ty đã khai chuyển chặng và cặp (``frm`` → ``to``) chưa được phép cho vai người gọi; "" = được. Công ty chưa khai cặp nào đang
    dùng ⇒ luôn được (hành vi cũ). Quyền hệ thống không mang vai ⇒ chỉ cần cặp có khai."""
    moves = await ctx.all("SELECT from_stage, to_stage, roles FROM crm.stage_moves WHERE active")
    if not moves:
        return ""
    role = ctx.p.role
    fit = [r for r in moves if r["from_stage"] == frm and (not r["roles"] or not role or role in r["roles"])]
    if any(r["to_stage"] == to for r in fit):
        return ""
    names = await labels(ctx)
    ok = sorted({str(names.get(r["to_stage"], r["to_stage"])) for r in fit})
    return (f"Chưa được chuyển từ “{names.get(frm, frm)}” sang “{names.get(to, to)}”"
            + (f" — từ chặng này chuyển được sang: {', '.join(ok)}" if ok else " — từ chặng này chưa có chặng nào được chuyển cho vai của anh/chị"))
