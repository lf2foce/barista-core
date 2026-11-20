"""Lý do mất khách là DANH MỤC của công ty (ADR 0246): đánh dấu lead mất thì CHỌN một lý do (ADR 0225: chọn, không gõ) — ghi chú ngắn vẫn tuỳ chọn. Lý do
thành chiều đếm được ("vì sao mình mất khách" — tập dữ liệu ``crm.ds_leads``), không còn là chữ mỗi người viết một kiểu. Bộ mặc định trung tính đi kèm gói
(``Pack(records=…)``), công ty đổi tên / thêm / tắt ở Quản trị › Lý do mất khách; không xoá (lead cũ còn trỏ tới).

Lý do của lần mất gần nhất ở ``crm.leads.lost_reason_key`` (giữ khi lead mở lại); mỗi lần mất ghi vào lịch sử chặng (``crm.stage_events.data``). Chữ tự do
cũ (``crm.leads.lost_reason``) giữ nguyên — lead mất trước khi có danh mục vẫn đọc được lý do người đã gõ."""

import re
from typing import Any

from barista.api import Ctx, Denied, Input, Invalid, NoInput, NotFound, field, fold, snake_from_name, ui
from barista.modules.crm import m

KEY = re.compile(r"[a-z][a-z0-9_]{1,39}")
MATCH_MIN_WORDS = 2   # ghi chú NẰM TRONG tên lý do chỉ tính khi có từ ngần này từ trở lên (một chữ "khác" không đủ để đoán)


def _words(text: str) -> str:
    """Chữ để so: bỏ dấu, chữ thường, chỉ chữ / số, cách hai đầu — so khớp theo NGUYÊN TỪ (" mua cho khac " nằm trong " da mua cho khac ")."""
    return " " + " ".join(re.findall(r"[a-z0-9]+", fold(text))) + " "


class SaveLostReason(Input):
    label: str = field("Lý do", example="Đã mua chỗ khác", max_length=80)
    key: str = field("Mã lý do", "", example="bought_elsewhere", max_length=40, placeholder="Máy đặt từ tên lý do", group="Thêm chi tiết",
                     help="Trống = máy đặt từ tên (lý do trùng tên đã có ⇒ sửa lý do đó). Không đổi được sau khi tạo")
    sort: int = field("Thứ tự", 100, ge=0, le=999, help="Thứ tự trong danh sách chọn khi đánh dấu mất khách")
    needs_note: bool = field("Cần ghi chú", False, help="Chọn lý do này thì phải ghi thêm vài chữ (vd \"Lý do khác\")")
    active: bool = field("Đang dùng", True, help="Tắt = không chọn được nữa; lead đã mất vì lý do này giữ nguyên")
    only_new: bool = field("Chỉ thêm nếu chưa có", False, hidden=True)


@m.command("save_lost_reason", resource="crm", action="manage", title="Thêm / sửa lý do mất khách", category="setup", importable=True, agent="propose")
async def save_lost_reason(ctx: Ctx, inp: SaveLostReason) -> dict[str, Any]:
    """Danh mục lý do là cấu hình cả công ty (báo cáo "vì sao mất khách" dùng chung) ⇒ cần quyền quản lý Khách hàng phạm vi cả công ty — như chặng của lead."""
    if not ctx.p.has_scope("crm", "all"):
        raise Denied("Lý do mất khách là cấu hình cả công ty — cần quyền quản lý Khách hàng phạm vi cả công ty")
    label = inp.label.strip()
    if not inp.key.strip():   # máy đặt mã từ tên (0225): lý do trùng tên đã có ⇒ sửa lý do đó
        inp.key = await ctx.val("SELECT key FROM crm.lost_reasons WHERE core.vn_fold(btrim(label)) = core.vn_fold(btrim(%s)) LIMIT 1", (label,)) or \
            snake_from_name(label, {r["key"] for r in await ctx.all("SELECT key FROM crm.lost_reasons")})
    if not KEY.fullmatch(inp.key):
        raise Invalid("Mã lý do: chữ thường không dấu, số, gạch dưới; bắt đầu bằng chữ (vd bought_elsewhere)")
    have = await ctx.val("SELECT 1 FROM crm.lost_reasons WHERE key = %s", (inp.key,))
    if have and inp.only_new:
        return {"key": inp.key, "added": False}
    await ctx.exec(
        """INSERT INTO crm.lost_reasons (key, label, sort, needs_note, active, updated_by) VALUES (%s, %s, %s, %s, %s, core.me())
           ON CONFLICT (tenant_id, key) DO UPDATE SET label = EXCLUDED.label, sort = EXCLUDED.sort, needs_note = EXCLUDED.needs_note,
               active = EXCLUDED.active, updated_by = EXCLUDED.updated_by, updated_at = now()""",
        (inp.key, label, inp.sort, inp.needs_note, inp.active))
    return {"key": inp.key, "added": not have, "summary": f"Lý do mất khách “{label}”" + ("" if inp.active else " (đã tắt)")}


@m.query("lost_reasons", resource="crm", title="Lý do mất khách")
async def lost_reasons(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("""SELECT r.key AS id, r.key, r.label, r.sort, r.needs_note, r.active, CASE WHEN r.active THEN 'on' ELSE 'off' END AS state,
                                   (SELECT count(*) FROM crm.leads l WHERE l.lost_reason_key = r.key AND l.stage = 'lost') AS leads
                            FROM crm.lost_reasons r ORDER BY r.active DESC, r.sort, r.label""")
    return ui.table(rows, [
        ui.col("label", "Lý do"), ui.col("sort", "Thứ tự", "number"),
        ui.col("state", "", "badge", labels={"on": "Đang dùng", "off": "Đã tắt"}, tones={"on": "green", "off": "gray"}),
        ui.col("leads", "Lead đang mất vì lý do này", "number"),
    ], title="Lý do mất khách", empty="Chưa có lý do nào — đánh dấu mất khách sẽ phải gõ lý do",
        empty_actions=[ui.act("crm.save_lost_reason", "Thêm lý do", style="primary")])


@m.query("lost_reason_options", resource="crm", title="Lý do mất khách chọn được")
async def lost_reason_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all("SELECT key AS value, label FROM crm.lost_reasons WHERE active ORDER BY sort, label"))


async def pick(ctx: Ctx, key: str, note: str) -> tuple[str | None, str]:
    """Lý do người chọn (mã) + ghi chú ⇒ (mã, tên lý do). Không chọn mà có ghi chú (nhập Excel, trợ lý, gói ngành gửi chữ) ⇒ máy đoán lý do từ chữ
    (tên lý do nằm trong ghi chú hoặc ngược lại — chữ gốc vẫn lưu làm ghi chú); không khớp ⇒ lý do "cần ghi chú" của công ty (vd "Lý do khác"). Công ty
    chưa có lý do nào đang dùng ⇒ (None, "") — ghi chú là lý do (như trước khi có danh mục)."""
    rows = await ctx.all("SELECT key, label, needs_note FROM crm.lost_reasons WHERE active ORDER BY sort, label")
    note = note.strip()
    if key.strip():
        want = _words(key)   # mã trong danh sách chọn; trợ lý / người nhập có thể gửi đúng TÊN lý do
        hit = next((r for r in rows if r["key"] == key.strip()), None) or next((r for r in rows if _words(r["label"]) == want), None)
        if hit is None:
            raise NotFound("Không có lý do mất khách này (hoặc đã tắt) — chọn trong danh sách")
        if hit["needs_note"] and not note:
            raise Invalid(f"Lý do “{hit['label']}” cần ghi chú thêm vài chữ")
        return hit["key"], hit["label"]
    if not rows:
        if not note:
            raise Invalid("Cần lý do mất khách")
        return None, ""
    if not note:
        raise Invalid("Chọn lý do mất khách trong danh sách")
    said = _words(note)
    found = [r for r in rows if not r["needs_note"] and (lab := _words(r["label"])).strip()
             and (lab in said or (len(said.split()) >= MATCH_MIN_WORDS and said in lab))]
    if found:
        best = max(found, key=lambda r: len(r["label"]))
        return best["key"], best["label"]
    other = next((r for r in rows if r["needs_note"]), None)
    if other is None:
        raise Invalid("Chọn lý do mất khách trong danh sách")
    return other["key"], other["label"]


async def label_of(ctx: Ctx, key: str | None) -> str:
    return (await ctx.val("SELECT label FROM crm.lost_reasons WHERE key = %s", (key,)) or "") if key else ""
