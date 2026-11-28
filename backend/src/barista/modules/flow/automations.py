"""TỰ ĐỘNG HOÁ "Nếu… thì…" do quản trị đặt (quyết định 0090, ROADMAP K4; mở rộng ADR 0246).

Luật là DỮ LIỆU, có ba kiểu KHI:
* **Cò sự kiện** module khai (``Module.trigger`` — sự kiện + trường an toàn): lead mới, lead đổi chặng, có tiền về…
* **Theo ngày** (ADR 0246): một NGUỒN NGÀY module khai (``Module.date_source`` — hạn việc, hẹn chăm sóc lại, hạn khoản phải thu…) + độ lệch
  ("3 ngày sau", "1 ngày trước"). Vòng quét của flow xét luật mỗi giờ; mỗi bản ghi kích MỘT lần cho mỗi giá trị ngày (đổi ngày ⇒ kích lại một lần).
* **Theo lịch** (ADR 0265): người đặt CHỌN nhịp (hằng ngày / ngày làm việc / hằng tuần thứ … / hằng tháng ngày …) + giờ theo múi giờ CÔNG TY
  (``timetable``). Vòng quét mỗi phút chọn luật tới hạn (``next_run_at``), mỗi LƯỢT chạy một lần (khoá dòng + chống lặp theo (luật, lượt)); lượt lỡ
  quá ``STALE`` (máy chủ nghỉ, luật vừa bật lại) ⇒ bỏ, có ghi — không dội bù hàng loạt.

Điều kiện có cấu trúc (trường, phép so, giá trị — không biểu thức, không mã do quản trị viết). THÌ: BÁO người / đội / người phụ trách bản ghi, TẠO
VIỆC, ĐỀ XUẤT (thẻ đề xuất cho một người — người bấm mới chạy, cùng kho đề xuất của trợ lý), CHẠY LỆNH — chỉ lệnh module khai an toàn cho luật
(``Module.command(auto=True)``: ghi rủi ro thấp, đảo được), NHỜ TRỢ LÝ SOẠN (ADR 0265: trợ lý của TỪNG người nhận chạy không người với lời dặn + bộ
công cụ hẹp khai cho luật; chỉ đọc và soạn — mọi lệnh ghi thành thẻ đề xuất chờ người đó xác nhận; chạy NGOÀI giao dịch ở vòng ``run_drafts``, đo
token), hoặc GỬI TÓM TẮT PHÁT HIỆN (bộ kiểm của công ty — số do truy vấn tính bằng quyền người nhận).

Giới hạn có chủ đích: chạy bằng QUYỀN NGƯỜI ĐẶT LUẬT (nạp lại mỗi lần — thôi việc / mất quyền ⇒ không chạy), chạy thử trước, mỗi sự kiện / (bản
ghi, ngày) một lần, trần số lần / ngày, im với dữ liệu nạp lịch sử và với thứ xảy ra / tới hạn TRƯỚC khi đặt luật, chạy SAU commit qua outbox (lỗi
thử lại lùi dần, quá ngưỡng thành "hỏng" ở Quản trị), chuỗi luật kích nhau tối đa ``MAX_CHAIN`` tầng, mỗi lần có nhật ký + mã vết ``auto:<luật>`` (0086).
"""

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, NamedTuple
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.api import Ctx, Denied, Event, Input, Invalid, NoInput, NotFound, Prep, field, money, notify, today, ui, zone
from barista.db import db
from barista.kernel import agent_policy, auth, executor, packs, registry, vocab
from barista.kernel.principal import Principal
from barista.modules.flow import _team_id, m, timetable

log = logging.getLogger("barista.automations")

OPS = {"eq": "=", "ne": "≠", "ge": "≥", "le": "≤", "contains": "chứa"}
_OP_WORDS = (("!=", "ne"), ("≠", "ne"), (">=", "ge"), ("≥", "ge"), ("<=", "le"), ("≤", "le"), ("=", "eq"), ("chứa", "contains"), ("contains", "contains"))
ACTIONS = {"notify": "Báo", "task": "Tạo việc", "propose": "Đề xuất cho người bấm", "command": "Chạy lệnh", "draft": "Nhờ trợ lý soạn",
           "digest": "Gửi tóm tắt phát hiện"}
RUN_STATUS = {"done": "Đã chạy", "skipped": "Bỏ qua", "failed": "Lỗi", "trial": "Chạy thử (chưa gửi)", "queued": "Đang chờ chạy"}
RUN_TONE = {"done": "ok", "skipped": "muted", "failed": "bad", "trial": "warn", "queued": "muted"}
STATE = {"trial": "Chạy thử", "on": "Bật", "off": "Tắt"}
DIRECTION = {"after": "Sau ngày đó", "before": "Trước ngày đó", "on": "Đúng ngày đó"}
MAX_CHAIN = 3          # luật kích luật (lệnh của luật A phát cò của luật B…): quá số tầng này thì dừng, có ghi — chặn vòng lặp
FROM_HOUR = 8          # luật theo ngày chỉ chạy từ giờ này (giờ công ty) — không báo / tạo việc lúc nửa đêm
LOOKBACK_DAYS = 7      # ngày tới hạn bị lỡ (máy chủ nghỉ) vẫn chạy bù trong chừng này ngày; cũ hơn ⇒ im
DUE_EVENT = "flow.automation_due"
SCHEDULE_EVENT = "flow.automation_scheduled"
SCHEDULE = "schedule"  # khoá vế KHI "theo lịch" (không bản ghi, không trường)
STALE = timedelta(hours=6)   # lượt theo lịch lỡ quá chừng này (máy chủ nghỉ, luật vừa bật lại) ⇒ bỏ, có ghi — không dội bù
# Nhờ trợ lý soạn (ADR 0265): trần cứng — mỗi lượt gọi model cho TỪNG người nhận nên số người và số lượt / ngày đều có trần, không theo ý người đặt
DRAFT_MAX_PEOPLE = 5      # quá số này ⇒ lượt lỗi, có ghi (chọn một người hoặc "chỉ trưởng đội")
DRAFT_DAILY_CAP = 20      # trần "Tối đa mỗi ngày" của luật có hành động này
DRAFT_MAX_TOOLS = 12      # bộ công cụ hẹp: nhiều hơn là không còn hẹp
DRAFT_REQUESTS = 6        # số lượt gọi model tối đa của MỘT lần soạn cho một người
DRAFT_TOKENS = 60_000     # trần token (vào + ra) của MỘT lần soạn cho một người — quá ⇒ dừng, phần đã soạn bỏ, có ghi; hạn mức tháng của công ty vẫn áp
DRAFT_LEASE_S = 900       # lượt soạn giữ việc chừng này giây; tiến trình chết ⇒ hết thuê, vòng sau làm lại (thẻ / thông báo có khoá chống lặp)
DRAFTS_PER_SWEEP = 3      # mỗi vòng quét của một công ty soạn tối đa chừng này việc — gọi model chậm, không giữ cả vòng
DRAFT_STYLE = ("Chỉ ĐỌC và SOẠN: việc cần ghi thì gọi công cụ ghi để thành thẻ đề xuất — người nhận bấm Xác nhận mới chạy. Trả lời ngắn bằng tiếng "
               "Việt, đi thẳng vào kết quả; số liệu lấy từ công cụ, không tự nghĩ ra số.")
DIGEST_PERIOD = "last_7"
DRAFT_MODEL: Any = None   # test tiêm model giả; None ⇒ model của hạng trợ lý người nhận (agent.llm.model_for)
clock: Callable[[], datetime] = lambda: datetime.now(UTC)   # noqa: E731 — đồng hồ của vòng quét theo lịch (test ghim)

class Outcome(NamedTuple):
    """Kết quả MỘT lần luật khớp cho nhật ký lần chạy. ``job``: việc chờ trợ lý soạn (hành động "nhờ trợ lý soạn") — vòng ``run_automation_drafts`` làm."""
    status: str
    note: str = ""
    job: dict[str, Any] | None = None


m.trigger("flow.plan_closed", "Kế hoạch đóng (xong / huỷ)", {"status": "Trạng thái"}, open=("flow.plan", {"plan_id": "plan_id"}))


@dataclass(frozen=True)
class When:
    """Vế KHI của một luật — cò sự kiện hoặc nguồn ngày, cùng một hình cho phần chạy."""
    key: str
    label: str
    module: str
    fields: dict[str, str]
    ids: tuple[str, ...]
    owner: str            # khoá người phụ trách bản ghi trong dữ liệu ("" = không có)
    open: tuple[str, dict[str, str]] | None
    date_label: str = ""  # nguồn ngày: tên ngày ("hạn việc") — "" = cò sự kiện


def when_of(trigger: str | None, date_source: str | None, schedule: str | None = None) -> When | None:
    if schedule:
        return When(SCHEDULE, "Theo lịch", m.key, {}, (), "", None)
    if trigger:
        t = registry.TRIGGERS.get(trigger)
        return When(t.event_type, t.label, t.module, t.fields, t.ids, t.owner, t.open) if t else None
    s = registry.DATE_SOURCES.get(date_source or "")
    return When(s.key, s.label, s.module, s.fields, s.ids, "owner_id", s.open, s.date_label) if s else None


# ── Hàm thuần (test thẳng) ──

def _fields(t: registry.Trigger | When, terms: Mapping[str, str] | None) -> dict[str, str]:
    """Trường của cò với nhãn của CÔNG TY (từ vựng, 0124): điều kiện / lời báo viết theo nhãn công ty người đặt luật."""
    return vocab.apply(t.fields, terms)


def _neutral(t: registry.Trigger | When) -> dict[str, str]:
    """Nhãn TRUNG TÍNH của trường — luật đặt trước khi công ty đổi nhãn (hoặc viết theo tài liệu chung) vẫn khớp."""
    return vocab.apply(t.fields)


def parse_conditions(text: str, fields: dict[str, str], also: dict[str, str] | None = None) -> list[dict[str, str]]:
    """Mỗi dòng "trường phép giá trị" — trường là MÃ hoặc NHÃN trong cò (vd ``Nguồn = ads``, ``Giá bán (đồng) >= 5000000000``, ``Nguồn chứa quảng``).
    ``also``: nhãn phụ (trung tính) cũng nhận. Trường lạ / phép lạ ⇒ ``Invalid`` nêu đúng dòng. Kết quả là CẤU TRÚC (trường, phép, giá trị) — không
    bao giờ đánh giá như mã."""
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
        if isinstance(v, date):
            return f"{v:%d/%m/%Y}"
        return money(int(v)) if isinstance(v, int | float | Decimal) and "(đồng)" in fields[key] else ("" if v is None else str(v))
    return re.sub(r"\{([^{}]+)\}", sub, template)


def describe(conditions: list[dict[str, Any]], fields: dict[str, str]) -> str:
    return " và ".join(f"{fields.get(c['field'], c['field'])} {OPS[c['op']]} {c['value']}" for c in conditions) or "luôn"


def offset_text(offset: int, date_label: str) -> str:
    """Độ lệch thành câu: ``3`` ⇒ "3 ngày sau hạn việc", ``-1`` ⇒ "1 ngày trước hạn việc", ``0`` ⇒ "đúng hạn việc"."""
    return f"{offset} ngày sau {date_label}" if offset > 0 else f"{-offset} ngày trước {date_label}" if offset < 0 else f"đúng {date_label}"


def when_text(w: When | None, r: Mapping[str, Any]) -> str:
    if w is None:
        return f"{r.get('trigger') or r.get('date_source')} (gói tắt / cò bỏ)"
    if w.key == SCHEDULE:
        return f"Theo lịch: {timetable.describe(r['schedule'], r['run_at'])}"
    return w.label if not w.date_label else f"{w.label}: {offset_text(r['offset_days'], w.date_label)}"


def _labels(op: registry.Op) -> dict[str, str]:
    return {k: (f.title or k) for k, f in op.input.model_fields.items()}


def parse_params(text: str, op: registry.Op) -> dict[str, str]:
    """Giá trị CỐ ĐỊNH cho ô của lệnh — mỗi dòng "Ô = giá trị" (ô là nhãn hoặc mã ô của lệnh). Giá trị là chữ, ``{trường}`` lấy từ bản ghi; executor
    kiểm kiểu như mọi lần gọi lệnh. Ô lạ ⇒ ``Invalid`` nêu đúng dòng + ô dùng được."""
    labels = _labels(op)
    by_label = {v.lower(): k for k, v in labels.items()}
    out: dict[str, str] = {}
    for n, line in enumerate((x.strip() for x in text.splitlines()), 1):
        if not line:
            continue
        name, eq, value = line.partition("=")
        if not eq or not name.strip():
            raise Invalid(f"Giá trị cho lệnh dòng {n} “{line}”: viết “Ô = giá trị”")
        key = name.strip() if name.strip() in labels else by_label.get(name.strip().lower())
        if key is None:
            raise Invalid(f"Giá trị cho lệnh dòng {n}: lệnh “{op.title}” không có ô “{name.strip()}” — dùng: {', '.join(labels.values())}")
        out[key] = value.strip()
    return out


def missing_inputs(op: registry.Op, ids: tuple[str, ...], params: Mapping[str, Any]) -> list[str]:
    """Ô BẮT BUỘC của lệnh mà luật không có cách điền (không phải mã bản ghi cùng tên, không có giá trị cố định)."""
    return [f.title or k for k, f in op.input.model_fields.items() if f.is_required() and k not in ids and k not in params]


def command_input(op: registry.Op, w: When, data: Mapping[str, Any], params: Mapping[str, str], terms: Mapping[str, str] | None) -> dict[str, Any]:
    """Đầu vào lệnh: ô trùng tên một mã của bản ghi (``lead_id``, ``task_id``…) lấy từ bản ghi; ô có giá trị cố định lấy giá trị đó (``{trường}`` thay)."""
    inp: dict[str, Any] = {k: str(data[k]) for k in op.input.model_fields if k in w.ids and data.get(k) is not None}
    return inp | {k: render(v, dict(data), _fields(w, terms), _neutral(w)) for k, v in params.items()}


# ── Đặt luật ──

@m.query("trigger_options", resource="flow.automations", title="Cò tự động hoá dùng được")
async def trigger_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Cò của các gói công ty đang bật — nhãn kèm trường đặt điều kiện được."""
    on = await packs.enabled(ctx.conn, ctx.p.tenant_id)
    return [{"value": t.event_type, "label": f"{t.label} — trường: {', '.join(t.fields.values())}"}
            for t in sorted(registry.TRIGGERS.values(), key=lambda t: t.label) if packs.module_on(on, t.module)]


@m.query("date_source_options", resource="flow.automations", title="Ngày dùng được cho luật theo ngày")
async def date_source_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Nguồn ngày (ADR 0246) của các gói công ty đang bật — hạn việc, hẹn chăm sóc lại, hạn khoản phải thu… kèm trường đặt điều kiện được."""
    on = await packs.enabled(ctx.conn, ctx.p.tenant_id)
    return [{"value": s.key, "label": f"{s.label} — trường: {', '.join(s.fields.values()) or 'không'}"}
            for s in sorted(registry.DATE_SOURCES.values(), key=lambda s: s.label) if packs.module_on(on, s.module)]


class CommandOptionsIn(Input):
    action: str = ""
    trigger: str = ""
    date_source: str = ""


def _fits(op: registry.Op, action: str, w: When | None) -> bool:
    """Lệnh dùng được cho hành động ``action`` của luật có vế KHI ``w``: ô mã bắt buộc (``*_id``) điền được từ bản ghi; "chạy lệnh" chỉ lệnh khai
    ``auto=True``; "đề xuất" mọi lệnh trợ lý không bị cấm, về CHÍNH bản ghi (nhận ít nhất một mã của nó)."""
    if op.kind != "command" or op.internal or op.public is not None or op.name in executor.DECISIONS:
        return False
    ids = w.ids if w else ()
    if any(f.is_required() and k.endswith("_id") and k not in ids for k, f in op.input.model_fields.items()):
        return False
    if action == "command":
        return op.auto
    # trợ lý bị cấm lệnh này (trần code) ⇒ không có thẻ đề xuất — một chỗ quyết chế độ trợ lý (agent_policy.decide, 0144); chính sách CÔNG TY áp lúc soạn thẻ
    return (action == "propose" and agent_policy.decide(op, agent_policy.Caller(via="agent"), agent_policy.Company()).mode != "deny"
            and bool(set(op.input.model_fields) & set(ids)))


@m.query("command_options", resource="flow.automations", title="Lệnh luật tự động dùng được")
async def command_options(ctx: Ctx, inp: CommandOptionsIn) -> list[dict[str, str]]:
    """Ô "Lệnh" của luật — tuỳ hành động + vế KHI đã chọn. "Chạy lệnh": chỉ lệnh module khai an toàn cho luật và NGƯỜI ĐẶT chạy được (luật chạy bằng
    quyền người đặt); "Đề xuất": lệnh về chính bản ghi (người nhận thẻ bấm mới chạy, bằng quyền của họ)."""
    w = when_of(inp.trigger or None, inp.date_source or None)
    on = await packs.enabled(ctx.conn, ctx.p.tenant_id)
    ops = [op for op in registry.OPS.values() if packs.module_on(on, op.module) and _fits(op, inp.action, w)
           and (inp.action != "command" or executor.allowed(ctx.p, op))]
    return [{"value": op.name, "label": op.title} for op in sorted(ops, key=lambda o: o.title)]


def draft_tool_ok(op: registry.Op) -> bool:
    """Công cụ khai được cho "nhờ trợ lý soạn" (ADR 0265): truy vấn trợ lý dùng được, hoặc lệnh ghi mà trợ lý CHỈ SOẠN THẺ (``agent_policy.decide`` ra
    "propose"). Lệnh trợ lý được làm thẳng ("allow") không vào bộ — lượt không người không bao giờ tự ghi (lệnh công ty nới thì ``decide`` hạ về đề xuất
    vì không có người; lệnh vốn được làm thẳng thì loại ở đây)."""
    if op.internal or op.public is not None or op.name.endswith("_options") or op.name in executor.DECISIONS or registry.tool_kind(op) == "builder":
        return False
    mode = agent_policy.decide(op, agent_policy.Caller(via="agent"), agent_policy.Company()).mode   # một nơi quyết chế độ trợ lý (0144)
    return mode != "deny" and (op.kind == "query" or mode == "propose")


@m.query("draft_tool_options", resource="flow.automations", title="Công cụ trợ lý dùng được cho luật")
async def draft_tool_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Ô "Trợ lý được dùng" của hành động "nhờ trợ lý soạn": truy vấn + lệnh soạn đề xuất của các gói đang bật mà NGƯỜI ĐẶT chạy được. Lúc chạy, trợ
    lý của từng người nhận chỉ mang phần người đó cũng chạy được."""
    on = await packs.enabled(ctx.conn, ctx.p.tenant_id)
    ops = [op for op in registry.OPS.values() if packs.module_on(on, op.module) and draft_tool_ok(op) and executor.allowed(ctx.p, op)]
    name = lambda o: registry.MODULES.get(o.module, o.module)   # noqa: E731
    return [{"value": op.name, "label": f"{name(op)} · {op.title}" + (" (soạn đề xuất)" if op.kind == "command" else "")}
            for op in sorted(ops, key=lambda o: (name(o), o.title))]


class SaveAutomation(Input):
    automation_id: UUID | None = field("Luật", None, hidden=True)
    name: str = field("Tên luật", max_length=120, example="Lead mới từ nguồn ngoài ⇒ báo trưởng team A")
    trigger: str = field("Khi (một việc xảy ra)", "", options_query="flow.trigger_options",
                         help="Chọn MỘT: việc xảy ra (ô này), theo ngày, hoặc theo lịch (các ô bên dưới)")
    date_source: str = field("Hoặc theo ngày", "", options_query="flow.date_source_options",
                             help="Hạn việc, hẹn chăm sóc lại, hạn khoản phải thu… — luật chạy N ngày trước / sau ngày đó, mỗi bản ghi một lần cho mỗi ngày")
    direction: Literal["after", "before", "on"] = field("Lúc nào", "after", options=DIRECTION, help="Chỉ cho luật theo ngày")
    days: int = field("Số ngày", 1, ge=0, le=365, help="Chỉ cho luật theo ngày: bao nhiêu ngày trước / sau")
    repeat: str = field("Hoặc theo lịch", "", options={"": "Không", **timetable.REPEAT},
                        help="Chạy theo nhịp cố định (giờ của công ty): báo cáo đầu tuần, nhắc cuối tháng, tóm tắt mỗi sáng…")
    weekday: str = field("Vào thứ", "MO", options=timetable.WEEKDAY, help="Chỉ cho lịch hằng tuần")
    month_day: str = field("Ngày trong tháng", "1", options=timetable.MONTH_DAY, help="Chỉ cho lịch hằng tháng")
    at: str = field("Lúc", "08:00", options=timetable.AT, help="Chỉ cho luật theo lịch — giờ của công ty")
    conditions: str = field("Và điều kiện", "", kind="textarea", max_length=1000, example="[[offering]] quan tâm = HSR\nNguồn = ads",
                            help="Mỗi dòng: trường phép giá trị (=, ≠, ≥, ≤, chứa). Trống = mọi lần cò xảy ra.")
    action: Literal["notify", "task", "propose", "command", "draft", "digest"] = field("Thì", "notify", options=ACTIONS)
    command: str = field("Lệnh", "", options_query="flow.command_options", options_with=("action", "trigger", "date_source"),
                         help="Chỉ cho “Đề xuất” / “Chạy lệnh”: lệnh về chính bản ghi (ô mã điền tự động)")
    params: str = field("Giá trị cho lệnh", "", kind="textarea", max_length=1000, example="Chặng = unreachable",
                        help="Mỗi dòng: ô = giá trị cho ô của lệnh mà bản ghi không tự điền. {tên trường} lấy giá trị từ bản ghi")
    tools: list[str] = field("Trợ lý được dùng", [], kind="multiselect", options_query="flow.draft_tool_options",
                             help=f"Chỉ cho “Nhờ trợ lý soạn”: bộ công cụ HẸP (tối đa {DRAFT_MAX_TOOLS}) — trợ lý chỉ đọc và soạn thẻ đề xuất, không tự ghi")
    to_owner: bool = field("Cho người phụ trách bản ghi", False, help="Sale giữ lead, người làm việc, người phụ trách khoản thu…")
    team_code: str = field("Hoặc cho đội", "", options_query="admin.team_options")
    leads_only: bool = field("Chỉ trưởng đội", False, group="Thêm chi tiết")
    user_email: str = field("Hoặc cho một người", "", options_query="admin.member_options", group="Thêm chi tiết")
    message: str = field("Nội dung", "", kind="textarea", max_length=500, example="Lead mới {[[offering]] quan tâm} (nguồn {Nguồn}) — gọi trong 15 phút",
                         help="{tên trường} lấy giá trị từ sự kiện. Đề xuất: lý do hiện trên thẻ; chạy lệnh: ghi chú; nhờ trợ lý soạn: lời dặn trợ lý")
    due_days: int | None = field("Hạn việc (sau … ngày)", None, ge=0, le=60, group="Thêm chi tiết")
    daily_cap: int = field("Tối đa mỗi ngày", 20, ge=1, le=500, help="Chặn bão thông báo / việc khi dữ liệu nhập ồ ạt", group="Thêm chi tiết")
    trial: bool = field("Chạy thử trước (chỉ ghi, chưa gửi)", True,
                        help="Khớp sự kiện thì chỉ ghi “sẽ báo ai / sẽ tạo việc gì” — xem các lần khớp rồi bấm “Chạy thật”", group="Thêm chi tiết")


async def _when_for_save(ctx: Ctx, inp: SaveAutomation) -> When:
    trig, src, rep = inp.trigger.strip(), inp.date_source.strip(), inp.repeat.strip()
    if bool(trig) + bool(src) + bool(rep) != 1:
        raise Invalid("Chọn MỘT vế “Khi”: một việc xảy ra, theo ngày, hoặc theo lịch")
    if rep:
        timetable.build(rep, inp.weekday, inp.month_day)   # lựa chọn lạ ⇒ Invalid nêu lựa chọn dùng được
        sw = when_of(None, None, rep)
        assert sw is not None
        return sw
    w = when_of(trig or None, src or None)
    if w is None or not packs.module_on(await packs.enabled(ctx.conn, ctx.p.tenant_id), w.module):
        # nói luôn cò / ngày nào dùng được (mã — nhãn — trường): người / trợ lý sửa trong MỘT bước, không đoán lại (eval 0095: đoán 3 lần rồi bỏ cuộc)
        opts = await (trigger_options(ctx, NoInput()) if trig else date_source_options(ctx, NoInput()))
        valid = "; ".join(f"{o['value']} = {o['label']}" for o in opts)
        raise Invalid(f"{'Cò' if trig else 'Ngày'} “{trig or src}” không có hoặc gói của nó đang tắt — dùng một trong: {valid}")
    return w


@m.command("save_automation", resource="flow.automations", action="manage", title="Đặt luật tự động", agent="propose")
async def save_automation(ctx: Ctx, inp: SaveAutomation) -> dict[str, Any]:
    """Luật "NẾU … THÌ …": khi một việc xảy ra (lead mới, lead đổi chặng, ký hợp đồng, có tiền về…) HOẶC theo ngày (N ngày sau hạn việc / hẹn chăm sóc
    lại / hạn khoản phải thu…) thì BÁO / nhắc ai đó, TẠO VIỆC, ĐỀ XUẤT một lệnh cho người bấm, hoặc CHẠY một lệnh module khai an toàn cho luật — lấy
    cò bằng flow__trigger_options, ngày bằng flow__date_source_options, lệnh bằng flow__command_options trước. KHÔNG phải luật giao lead (lead mới vào
    tay ai ⇒ leadflow__set_rule). Luật mới chạy thử trước; chạy bằng QUYỀN NGƯỜI ĐẶT (người lưu sau cùng). Trợ lý chỉ đề xuất — người bấm mới lưu."""
    w = await _when_for_save(ctx, inp)
    fields, also = _fields(w, ctx.p.vocab), _neutral(w)
    if w.key == SCHEDULE and inp.conditions.strip():
        raise Invalid("Luật theo lịch không có điều kiện trên bản ghi — chạy đúng giờ đã chọn; cần lọc ⇒ dặn trong lời dặn trợ lý")
    if inp.action == "digest" and w.key != SCHEDULE:
        raise Invalid("“Gửi tóm tắt phát hiện” chỉ cho luật theo lịch (vd 8 giờ mỗi sáng thứ Hai)")
    tools: list[str] = []
    if inp.action == "draft":
        allowed = {o["value"] for o in await draft_tool_options(ctx, NoInput())}
        tools = list(dict.fromkeys(t.strip() for t in inp.tools if t.strip()))
        if not tools or len(tools) > DRAFT_MAX_TOOLS:
            raise Invalid(f"“Nhờ trợ lý soạn” cần chọn 1–{DRAFT_MAX_TOOLS} công cụ trợ lý được dùng (bộ hẹp cho đúng việc này)")
        if bad_tools := [t for t in tools if t not in allowed]:
            raise Invalid(f"Công cụ không dùng được cho trợ lý soạn (hoặc anh/chị không chạy được): {', '.join(bad_tools)} — chọn trong danh sách")
        if not inp.message.strip():
            raise Invalid("Cần lời dặn trợ lý (ô Nội dung): soạn gì, cho ai, dựa vào số nào")
        if inp.daily_cap > DRAFT_DAILY_CAP:
            raise Invalid(f"Nhờ trợ lý soạn: tối đa {DRAFT_DAILY_CAP} lần mỗi ngày (mỗi lần gọi model cho từng người nhận)")
    elif inp.tools:
        raise Invalid("“Trợ lý được dùng” chỉ cho hành động Nhờ trợ lý soạn")
    conds = parse_conditions(inp.conditions, fields, also)
    known = {*fields, *fields.values(), *also.values()}
    if bad := [x for x in re.findall(r"\{([^{}]+)\}", inp.message + inp.params) if x.strip() not in known]:
        raise Invalid(f"Nội dung dùng trường không có trong cò: {', '.join(bad)} — dùng: {', '.join(fields.values())}")
    command, params = None, {}
    if inp.action in ("command", "propose"):
        op = registry.get(inp.command.strip())
        if op is None or not _fits(op, inp.action, w) or not packs.module_on(await packs.enabled(ctx.conn, ctx.p.tenant_id), op.module):
            valid = "; ".join(f"{o['value']} = {o['label']}" for o in await command_options(
                ctx, CommandOptionsIn(action=inp.action, trigger=inp.trigger, date_source=inp.date_source)))
            raise Invalid(f"Lệnh “{inp.command}” không dùng được cho {ACTIONS[inp.action].lower()} với vế “Khi” này — dùng một trong: {valid or 'không có lệnh nào'}")
        if inp.action == "command" and not executor.allowed(ctx.p, op):
            raise Denied(f"Anh/chị không chạy được “{op.title}” — luật chạy bằng quyền người đặt luật")
        params = parse_params(inp.params, op)
        if miss := missing_inputs(op, w.ids, params):
            raise Invalid(f"Lệnh “{op.title}” còn thiếu ô: {', '.join(miss)} — điền ở “Giá trị cho lệnh”")
        command = op.name
    elif inp.params.strip():
        raise Invalid("“Giá trị cho lệnh” chỉ cho hành động Đề xuất / Chạy lệnh")
    team = user = None
    if inp.action == "command":
        if inp.team_code.strip() or inp.user_email.strip() or inp.to_owner:
            raise Invalid("Chạy lệnh không gửi cho ai — lệnh chạy bằng quyền người đặt luật; bỏ chọn người / đội")
    else:
        team = await _team_id(ctx, inp.team_code) if inp.team_code.strip() else None
        if inp.team_code.strip() and team is None:
            raise NotFound(f"Không có đội {inp.team_code}")
        if inp.user_email.strip():
            user = await ctx.val("""SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()
                                    WHERE u.email = %s AND mb.active""", (inp.user_email.strip().lower(),))
            if user is None:
                raise NotFound(f"Không có người {inp.user_email} trong công ty")
        if inp.to_owner and not w.owner:
            raise Invalid(f"“{w.label}” không mang người phụ trách — chọn một đội hoặc một người")
        if (team is not None) + (user is not None) + inp.to_owner != 1:
            raise Invalid("Chọn MỘT đích: người phụ trách bản ghi, một đội hoặc một người")
    if inp.action in ("notify", "task") and not inp.message.strip():
        raise Invalid("Cần nội dung báo / tên việc")
    offset = 0 if not w.date_label or inp.direction == "on" else inp.days * (1 if inp.direction == "after" else -1)
    schedule = run_at = next_run = None
    if w.key == SCHEDULE:
        schedule, run_at = timetable.build(inp.repeat, inp.weekday, inp.month_day), timetable.clock_of(inp.at)
        next_run = timetable.next_after(schedule, run_at, clock())
    row = (inp.name.strip(), w.key if not w.date_label and w.key != SCHEDULE else None, w.key if w.date_label else None, offset, Jsonb(conds),
           inp.action, command, Jsonb(params), team, inp.leads_only and team is not None, user, inp.to_owner and inp.action != "command",
           inp.message.strip() or inp.name.strip(), inp.due_days if inp.action == "task" else None, inp.daily_cap, inp.trial,
           schedule, run_at, next_run, Jsonb(tools))
    if inp.automation_id:
        aid = await ctx.val("""UPDATE flow.automations SET name = %s, trigger = %s, date_source = %s, offset_days = %s, conditions = %s, action = %s,
                                   command = %s, params = %s, team_id = %s, leads_only = %s, user_id = %s, to_owner = %s, message = %s, due_days = %s,
                                   daily_cap = %s, trial = %s, schedule = %s, run_at = %s, next_run_at = %s, tools = %s, owner_id = core.me(),
                                   swept_at = NULL, updated_at = now()
                               WHERE id = %s RETURNING id""", (*row, inp.automation_id))
        if aid is None:
            raise NotFound("Không thấy luật")
    else:
        aid = await ctx.val("""INSERT INTO flow.automations (name, trigger, date_source, offset_days, conditions, action, command, params, team_id,
                                                             leads_only, user_id, to_owner, message, due_days, daily_cap, trial, schedule, run_at,
                                                             next_run_at, tools, owner_id)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, core.me()) RETURNING id""", row)
    then = ACTIONS[inp.action].lower() + (f" “{registry.OPS[command].title}”" if command else "")
    when = when_text(w, {"offset_days": offset, "schedule": schedule, "run_at": run_at}).lower()
    first = f" — lần đầu {next_run.astimezone(zone()):%H:%M %d/%m/%Y}" if next_run else ""
    return {"automation_id": aid, "summary": f"Luật “{inp.name.strip()}”: khi {when} ({describe(conds, fields)}) ⇒ {then}{first}"
                                            + (" — đang CHẠY THỬ (chỉ ghi, chưa gửi)" if inp.trial else "")}


class AutomationRef(Input):
    automation_id: UUID = field("Luật", hidden=True)


class SetActive(Input):
    automation_id: UUID = field("Luật", hidden=True)
    active: bool | None = field("Đang bật", None)
    trial: bool | None = field("Chạy thử", None, help="Tắt chạy thử = luật bắt đầu gửi / tạo việc thật")


@m.command("set_automation", resource="flow.automations", action="manage", title="Bật / tắt / chạy thật luật tự động", agent="propose")
async def set_automation(ctx: Ctx, inp: SetActive) -> dict[str, Any]:
    """Chạy thật cần người quản lý luật bấm (trợ lý chỉ đề xuất); người bấm thành người đặt luật — luật chạy bằng quyền người đó. Bật lại luật theo lịch
    ⇒ lượt kế tiếp tính từ BÂY GIỜ (các lượt lúc tắt không chạy bù)."""
    r = await ctx.one("""UPDATE flow.automations SET active = coalesce(%s, active), trial = coalesce(%s, trial), owner_id = core.me(), updated_at = now()
                         WHERE id = %s RETURNING name, active, trial, schedule, run_at""", (inp.active, inp.trial, inp.automation_id))
    if r is None:
        raise NotFound("Không thấy luật")
    if inp.active and r["schedule"]:
        await ctx.exec("UPDATE flow.automations SET next_run_at = %s WHERE id = %s",
                       (timetable.next_after(r["schedule"], r["run_at"], clock()), inp.automation_id))
    return {"summary": f"Luật “{r['name']}”: {STATE['off' if not r['active'] else 'trial' if r['trial'] else 'on'].lower()}"}


# ── Xem ──

async def _target(ctx: Ctx, r: Mapping[str, Any]) -> str:
    if r["action"] == "command":
        return "bằng quyền người đặt luật"
    if r.get("to_owner"):
        return "người phụ trách"
    if r["user_id"]:
        return str(await ctx.val("SELECT name FROM core.users WHERE id = %s", (r["user_id"],)) or "—")
    team = await ctx.val("SELECT name FROM core.teams WHERE id = %s", (r["team_id"],))
    return f"{'trưởng ' if r['leads_only'] else ''}{team or '—'}"


def _then(r: Mapping[str, Any]) -> str:
    op = registry.get(r["command"]) if r.get("command") else None
    return ACTIONS[r["action"]] + (f" “{op.title if op else r['command']}”" if r.get("command") else "")


@m.query("automations", resource="flow.automations", title="Luật tự động")
async def automations(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("""SELECT a.*, u.name AS owner,
                                   (SELECT count(*) FROM flow.automation_runs r WHERE r.automation_id = a.id AND r.status = 'done'
                                     AND r.created_at >= core.today()::timestamptz) AS today,
                                   (SELECT r.status FROM flow.automation_runs r WHERE r.automation_id = a.id ORDER BY r.id DESC LIMIT 1) AS last
                            FROM flow.automations a JOIN core.users u ON u.id = a.owner_id ORDER BY a.active DESC, a.name""")
    out = []
    for r in rows:
        w = when_of(r["trigger"], r["date_source"], r["schedule"])
        fields = _fields(w, ctx.p.vocab) if w else {}
        out.append({"id": r["id"], "name": r["name"], "when": when_text(w, r),
                    "if": describe(r["conditions"], fields), "then": f"{_then(r)} — {await _target(ctx, r)}",
                    "owner": r["owner"], "today": f"{r['today']}/{r['daily_cap']}", "last": r["last"] or "",
                    "active": "off" if not r["active"] else "trial" if r["trial"] else "on"})
    return ui.table(out, [ui.col("active", "", "badge", labels=STATE, tones={"on": "green", "off": "gray", "trial": "orange"}),
                          ui.col("name", "Luật"), ui.col("when", "Khi"), ui.col("if", "Và"), ui.col("then", "Thì"), ui.col("owner", "Chạy bằng quyền"),
                          ui.col("today", "Hôm nay"), ui.col("last", "Lần gần nhất", "badge", labels=RUN_STATUS,
                                                            tones={"done": "green", "skipped": "gray", "failed": "red", "trial": "orange", "queued": "gray"})],
                    title="Luật tự động", empty="Chưa có luật — “Đặt luật”: khi có lead mới / ký hợp đồng / tiền về, hoặc N ngày sau hạn việc / hẹn chăm "
                                                "sóc lại… thì báo người, tạo việc, đề xuất hoặc chạy lệnh",
                    note="Luật mới CHẠY THỬ (chỉ ghi, chưa gửi) tới khi bấm “Chạy thật”; chạy bằng quyền người đặt; mỗi sự kiện / mỗi bản ghi theo ngày "
                         "một lần; quá trần mỗi ngày thì bỏ qua (có ghi).")


@m.query("automation", resource="flow.automations", title="Luật tự động hoá")
async def automation(ctx: Ctx, inp: AutomationRef) -> dict[str, Any]:
    r = await ctx.one("SELECT a.*, u.name AS owner FROM flow.automations a JOIN core.users u ON u.id = a.owner_id WHERE a.id = %s", (inp.automation_id,))
    if r is None:
        raise NotFound("Không thấy luật")
    w = when_of(r["trigger"], r["date_source"], r["schedule"])
    fields = _fields(w, ctx.p.vocab) if w else {}
    runs = await ctx.all("SELECT status, note, created_at FROM flow.automation_runs WHERE automation_id = %s ORDER BY id DESC LIMIT 20", (r["id"],))
    op = registry.get(r["command"]) if r["command"] else None
    labels = _labels(op) if op else {}
    blocks = [ui.b_callout("muted", f"Khi {when_text(w, r).lower()} và {describe(r['conditions'], fields)}",
                           f"Thì {_then(r).lower()} — {await _target(ctx, r)}: “{r['message']}”"
                           + (f" — hạn sau {r['due_days']} ngày" if r["due_days"] is not None else ""),
                           facts=[f"Chạy bằng quyền {r['owner']}", f"Tối đa {r['daily_cap']} lần / ngày",
                                  *([f"Lần tới: {r['next_run_at'].astimezone(zone()):%H:%M %d/%m/%Y}"] if r["next_run_at"] and r["active"] else []),
                                  *([f"Trợ lý được dùng: {', '.join(registry.OPS[t].title if t in registry.OPS else t for t in r['tools'])}"] if r["tools"] else []),
                                  *[f"{labels.get(k, k)} = {v}" for k, v in r["params"].items()]]),
              ui.b_list([{"title": RUN_STATUS[x["status"]], "subtitle": f"{x['created_at'].astimezone(zone()):%d/%m %H:%M}" + (f" · {x['note']}" if x["note"] else ""),
                          "tone": RUN_TONE[x["status"]]} for x in runs], "Các lần chạy gần đây")]
    if r["trial"] and r["active"]:
        n = sum(1 for x in runs if x["status"] == "trial")
        blocks.insert(0, ui.b_callout("warn", "Đang chạy thử — chưa gửi gì, chưa tạo việc nào",
                                      f"{n} lần khớp gần đây (xem bên dưới: sẽ báo ai, nội dung gì). Đúng ý thì bấm “Chạy thật”."))
    off = r["offset_days"]
    edit = {"automation_id": str(r["id"]), "name": r["name"], "trigger": r["trigger"] or "", "date_source": r["date_source"] or "",
            "direction": "after" if off > 0 else "before" if off < 0 else "on", "days": abs(off), "action": r["action"], "command": r["command"] or "",
            "params": "\n".join(f"{labels.get(k, k)} = {v}" for k, v in r["params"].items()), "to_owner": r["to_owner"], "message": r["message"],
            "conditions": "\n".join(f"{fields.get(c['field'], c['field'])} {OPS[c['op']]} {c['value']}" for c in r["conditions"]),
            "leads_only": r["leads_only"], "daily_cap": r["daily_cap"], "tools": list(r["tools"]),
            **({**timetable.choices(r["schedule"]), "at": f"{r['run_at']:%H:%M}"} if r["schedule"] else {})}
    state = "off" if not r["active"] else "trial" if r["trial"] else "on"
    actions = [ui.act("flow.set_automation", "Chạy thật", {"automation_id": str(r["id"]), "trial": False}, "primary")] if state == "trial" else []
    actions += [ui.act("flow.save_automation", "Sửa luật", {**edit, "trial": r["trial"]}),
                ui.act("flow.set_automation", "Tắt luật" if r["active"] else "Bật luật", {"automation_id": str(r["id"]), "active": not r["active"]},
                       "destructive" if r["active"] else "default")]
    return ui.card(r["name"], blocks, subtitle=STATE[state], icon="bolt.fill", actions=actions)


# ── Chạy ──

async def _record(ctx: Ctx, rule: UUID, event_id: int, status: str, note: str = "", job: dict[str, Any] | None = None) -> None:
    await ctx.exec("INSERT INTO flow.automation_runs (automation_id, event_id, status, note, job) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                   (rule, event_id, status, note[:500], Jsonb(job) if job is not None else None))


async def _used_today(ctx: Ctx, rule: UUID) -> int:
    return int(await ctx.val("""SELECT count(*) FROM flow.automation_runs WHERE automation_id = %s AND status IN ('done', 'trial', 'queued')
                                AND created_at >= core.today()::timestamptz""", (rule,)) or 0)


async def _recipients(ctx: Ctx, r: Mapping[str, Any], data: Mapping[str, Any]) -> list[UUID]:
    if r["to_owner"]:
        return [UUID(str(data["owner_id"]))] if data.get("owner_id") else []
    if r["user_id"]:
        return [r["user_id"]]
    users = await notify.team_members(ctx.conn, r["team_id"])
    if r["leads_only"]:
        leads = {x["user_id"] for x in await ctx.all("SELECT user_id FROM core.team_members WHERE team_id = %s AND is_lead", (r["team_id"],))}
        users = [u for u in users if u in leads]
    return users


async def _execute(ctx: Ctx, r: Mapping[str, Any], w: When, data: dict[str, Any], key: str, depth: int,
                   terms: Mapping[str, str] | None) -> Outcome:
    """MỘT lần luật khớp ⇒ kết quả cho nhật ký lần chạy. Bằng quyền NGƯỜI ĐẶT LUẬT nạp lại ngay lúc này; mọi lệnh qua executor (một cửa)
    với khoá chống lặp ``key`` ⇒ giao lại không thành hai việc. Lệnh của luật chạy trong ``chained(depth + 1)`` ⇒ cò nó phát mang độ sâu chuỗi.
    "Nhờ trợ lý soạn" không gọi model ở đây (đang giữ phiên ghi DB): trả việc ``job`` đã chốt (người nhận, lời dặn) — vòng ``run_automation_drafts`` soạn."""
    # động cơ tự động hoá (gói nền) CHẠY LỆNH bằng quyền người đặt luật ⇒ nạp như lõi (CORE), không qua principal_of (chỉ để hỏi hộ — 0130)
    owner = await auth.load(ctx.conn, ctx.p.tenant_id, user_id=r["owner_id"])
    if owner is None or not owner.can("flow.automations", "manage"):
        return Outcome("skipped", "Người đặt luật không còn hoạt động / không còn quyền quản lý luật — đặt lại luật")
    text = render(r["message"], data, _fields(w, terms), _neutral(w))
    op = registry.get(r["command"]) if r["command"] else None
    if r["action"] in ("command", "propose") and (op is None or not _fits(op, r["action"], w)):
        return Outcome("failed", f"Lệnh {r['command']} không còn dùng được cho luật — sửa luật")
    users = await _recipients(ctx, r, data) if r["action"] != "command" else []
    if r["action"] != "command" and not users:
        return Outcome("failed", "Không có người nhận (bản ghi chưa có người phụ trách / đội không còn ai)")
    if r["action"] == "draft" and len(users) > DRAFT_MAX_PEOPLE:
        return Outcome("failed", f"Nhờ trợ lý soạn tối đa {DRAFT_MAX_PEOPLE} người một lần (đích đang có {len(users)} người) — chọn một người hoặc “chỉ trưởng đội”")
    who = await _target(ctx, r)
    inp = command_input(op, w, data, r["params"], terms) if op else {}
    if r["trial"]:   # chạy thử: ghi đúng thứ SẼ làm (người nhận / đích việc + nội dung), không gửi, không tạo
        if r["action"] == "command" and op:   # chạy thử THẬT bằng quyền người đặt (rồi bỏ) — sai quyền / sai dữ liệu hiện ngay ở đây
            with executor.tracing(f"auto:{r['id']}"):
                pre = await executor.propose(owner, op.name, inp)
            if pre.status != "proposed":
                return Outcome("failed", f"Chạy thử “{op.title}” không được: {pre.error or pre.status}")
            summary = pre.output["brief"]["title"] if isinstance(pre.output, dict) and pre.output.get("brief") else ""
            return Outcome("trial", f"Sẽ chạy “{op.title}”" + (f": {summary}" if summary else ""))
        if r["action"] == "propose" and op:
            return Outcome("trial", f"Sẽ đề xuất “{op.title}” cho {who}: “{text}”")
        if r["action"] == "draft":
            return Outcome("trial", f"Sẽ nhờ trợ lý của {who} soạn: “{text}”")
        if r["action"] == "digest":
            return Outcome("trial", f"Sẽ gửi tóm tắt phát hiện cho {who}")
        return Outcome("trial", f"Sẽ {'báo' if r['action'] == 'notify' else 'tạo việc cho'} {who}: “{text}”")
    if r["action"] == "draft":
        return Outcome("queued", f"Chờ trợ lý soạn cho {who}", {"users": [str(u) for u in users], "text": text, "key": key})
    open_ = {"query": w.open[0], "input": {k: str(data.get(v)) if data.get(v) is not None else None for k, v in w.open[1].items()}} if w.open else None
    try:
        with executor.tracing(f"auto:{r['id']}"), executor.chained(depth + 1):
            if r["action"] == "notify":
                await notify.send(ctx.conn, users, kind="automation", title=r["name"], body=text, push=text[:120], open=open_, key=key, actor=None)
            elif r["action"] == "digest":
                sent, errors = await _digest(ctx, r, users, key)
                if not sent:
                    raise Invalid("; ".join(errors) or "không gửi được tóm tắt cho ai")
                return Outcome("done", f"Đã gửi tóm tắt cho {sent} người" + (f" (bỏ qua: {'; '.join(errors)})" if errors else ""))
            elif r["action"] == "task":
                # lệnh CẤP NGOÀI bằng quyền người đặt luật: có nhật ký + mã vết; khoá chống lặp theo (luật, sự kiện) ⇒ giao lại không thành hai việc
                team_code = await ctx.val("SELECT code FROM core.teams WHERE id = %s", (r["team_id"],)) if r["team_id"] else ""
                person = r["user_id"] or (users[0] if r["to_owner"] else None)
                email = await ctx.val("SELECT email FROM core.users WHERE id = %s", (person,)) if person else ""
                res = await executor.run(owner, "flow.create_task", {
                    "title": text[:300], "notes": f"Tạo bởi luật tự động hoá “{r['name']}”", "team_code": team_code or "", "assignee_email": email or "",
                    "due_on": (today() + timedelta(days=r["due_days"])).isoformat() if r["due_days"] is not None else None}, key=key)
                if not res.ok:
                    raise Invalid(res.error or res.status)
            elif r["action"] == "command" and op:
                res = await executor.run(owner, op.name, inp, key=key)
                if not res.ok:
                    raise Invalid(res.error or res.status)
                if res.status == "pending_approval":
                    return Outcome("done", f"“{op.title}” đã gửi duyệt")
            elif op:   # đề xuất: thẻ trong kho đề xuất của người nhận — cùng đường Xác nhận / Bỏ với đề xuất của trợ lý (chạy bằng quyền NGƯỜI bấm)
                filed, errors = await _propose(ctx, r, op, inp, users, key, text)
                if not filed:
                    raise Invalid("; ".join(errors) or "không soạn được đề xuất")
                return Outcome("done", f"Đã đề xuất “{op.title}” cho {filed} người" + (f" (bỏ qua: {'; '.join(errors)})" if errors else ""))
    except (Invalid, Denied, NotFound) as e:
        return Outcome("failed", str(e))
    return Outcome("done")


async def _digest(ctx: Ctx, r: Mapping[str, Any], users: list[UUID], key: str) -> tuple[int, list[str]]:
    """Tóm tắt bộ kiểm (báo cáo phát hiện) cho từng người nhận — số do truy vấn của gói tính bằng QUYỀN NGƯỜI NHẬN (hỏi hộ, ``principal_of``): người
    không xem được báo cáo cả công ty / công ty chưa bật gói ⇒ không có bản tóm tắt (ghi lý do). Thông báo trong app, mở thẳng báo cáo."""
    sent, errors = 0, []
    for uid in users:
        who = await ctx.principal_of(uid)
        if who is None:
            continue
        got = await ctx.call_as_optional("findings.digest", who, period=DIGEST_PERIOD)
        if got is None:
            errors.append(f"{who.name}: không xem được báo cáo phát hiện (gói chưa bật hoặc không đủ quyền xem cả công ty)")
            continue
        await notify.send(ctx.conn, [uid], kind="automation", title=f"{r['name']}: {got['headline']}"[:300], body=got["text"],
                          push="Tóm tắt phát hiện mới", open={"query": "findings.overview", "input": {"period": DIGEST_PERIOD}}, key=f"{key}:{uid}", actor=None)
        sent += 1
    return sent, errors


async def _propose(ctx: Ctx, r: Mapping[str, Any], op: registry.Op, inp: dict[str, Any], users: list[UUID], key: str, text: str) -> tuple[int, list[str]]:
    """Thẻ ĐỀ XUẤT cho từng người nhận: soạn bằng QUYỀN TRỢ LÝ của chính người đó (``executor.propose`` — chạy thử rồi bỏ; lệnh trợ lý bị cấm vẫn bị
    cấm; người không có quyền thì không có thẻ), rồi vào ``core.proposals`` của họ (``morning.file_cards``). Người bấm Xác nhận mới chạy, bằng quyền
    của họ, kiểm căn cứ (0071)."""
    from barista.agent import morning

    filed, errors = 0, []
    for uid in users:
        rp = await auth.load(ctx.conn, ctx.p.tenant_id, user_id=uid)
        if rp is None:
            continue
        if not rp.ai:
            errors.append(f"{rp.name}: công ty tắt trợ lý — không có kho đề xuất")
            continue
        why = f"Luật tự động “{r['name']}”: {text}"
        pre = await executor.propose(rp.as_agent(), op.name, inp, reason=why)
        if pre.status != "proposed" or not isinstance(pre.output, dict):
            errors.append(f"{rp.name}: {pre.error or pre.status}")
            continue
        o = pre.output
        filed += await morning.file_cards(rp, f"{morning.AUTO}:{key}:{uid}", [{"kind": "proposal", "command": o["command"], "title": o["brief"]["title"],
                                                                              "input": o["input"], "basis": o.get("basis"), "brief": o["brief"]}], why)
    return filed, errors


@m.on_trigger()
async def run_automations(ctx: Ctx, event: Event) -> None:
    """Mỗi cò ⇒ các luật đang bật của cò đó: điều kiện đúng ⇒ (chưa chạy với sự kiện này, chưa chạm trần, người đặt luật còn quyền) hành động
    bằng quyền NGƯỜI ĐẶT LUẬT. Lỗi nghiệp vụ (mất quyền với đội, đội đã bỏ…) ⇒ ghi "Lỗi", không thử lại vô hạn."""
    if ctx.historical:   # sự kiện của một lần nạp lịch sử (0185) không kích luật tự động hoá (luật gửi thông báo / tạo việc ngoài ``ctx.notify``)
        return
    t = registry.TRIGGERS[event.type]
    w = when_of(t.event_type, None)
    assert w is not None
    data = {k: event.payload.get(k) for k in (*t.fields, *t.ids, *(t.open[1].values() if t.open else ()))}
    data["owner_id"] = event.payload.get(t.owner) if t.owner else None
    payload = {k: event.payload.get(k) for k in t.fields}
    depth = int(event.payload.get(executor.CHAIN_KEY) or 0)
    terms = await vocab.terms(ctx.conn, ctx.p.tenant_id)   # worker chạy bằng quyền hệ thống (không mang nhãn công ty) ⇒ nạp nhãn của công ty này
    rules = await ctx.all("SELECT * FROM flow.automations WHERE active AND trigger = %s ORDER BY created_at", (event.type,))
    for r in rules:
        if event.at is not None and event.at < r["created_at"]:
            continue   # luật chỉ áp cho sự kiện xảy ra SAU khi đặt (sự kiện tồn / giao muộn không kích luật mới)
        if not matches(r["conditions"], payload):
            continue
        if await ctx.val("SELECT 1 FROM flow.automation_runs WHERE automation_id = %s AND event_id = %s", (r["id"], event.id)):
            continue   # sự kiện giao lại ⇒ không chạy lần hai
        if depth >= MAX_CHAIN:
            await _record(ctx, r["id"], event.id, "skipped", f"Dừng: chuỗi luật kích nhau đã {depth} tầng (tối đa {MAX_CHAIN}) — xem lại các luật nối nhau")
            continue
        if await _used_today(ctx, r["id"]) >= r["daily_cap"]:
            await _record(ctx, r["id"], event.id, "skipped", f"Đã chạm trần {r['daily_cap']} lần hôm nay")
            continue
        out = await _execute(ctx, r, w, {**data, **payload}, f"auto:{r['id']}:{event.id}", depth, terms)
        await _record(ctx, r["id"], event.id, out.status, out.note, out.job)


# ── Luật theo ngày (ADR 0246) ──

async def _dated_rows(ctx: Ctx, source: str, since: date, until: date) -> list[dict[str, Any]]:
    """Bản ghi của MỘT nguồn ngày có ngày trong [since, until] — quyền hệ thống theo hợp đồng nguồn khai (``Module.date_source``: callers=flow + why)."""
    return await ctx.call_system(source, since=since, until=until)


@m.command("sweep_dated_automations", resource="flow.automations", title="Xét luật tự động theo ngày", internal=True, agent="deny",
           sweep="""SELECT DISTINCT tenant_id FROM flow.automations WHERE active AND date_source IS NOT NULL
                    AND (swept_at IS NULL OR swept_at < now() - interval '1 hour')""")
async def sweep_dated_automations(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Vòng quét (quyền hệ thống, mỗi luật ~mỗi giờ, từ ``FROM_HOUR`` giờ công ty): luật theo ngày đang bật ⇒ bản ghi có NGÀY + độ lệch tới hạn trong
    [max(ngày đặt luật, hôm nay − LOOKBACK_DAYS), hôm nay] khớp điều kiện ⇒ giữ chỗ (luật, bản ghi, ngày) rồi phát ``flow.automation_due`` — phần chạy
    nằm ở bên nghe (sau commit, thử lại lùi dần, quá ngưỡng thành hỏng như mọi sự kiện). Tới hạn TRƯỚC ngày đặt luật ⇒ im (dữ liệu cũ, nạp lịch sử)."""
    if ctx.historical or datetime.now(zone()).hour < FROM_HOUR:
        return {"queued": 0}
    on = await packs.enabled(ctx.conn, ctx.p.tenant_id)
    rules = await ctx.all("""SELECT * FROM flow.automations WHERE active AND date_source IS NOT NULL
                             AND (swept_at IS NULL OR swept_at < now() - interval '1 hour') ORDER BY created_at FOR UPDATE SKIP LOCKED""")
    t0, queued = today(), 0
    for r in rules:
        await ctx.exec("UPDATE flow.automations SET swept_at = now() WHERE id = %s", (r["id"],))
        w = when_of(None, r["date_source"])
        if w is None or not packs.module_on(on, w.module):
            continue
        low = max(r["created_at"].astimezone(zone()).date(), t0 - timedelta(days=LOOKBACK_DAYS))
        off = timedelta(days=r["offset_days"])
        used = await _used_today(ctx, r["id"])
        for row in await _dated_rows(ctx, w.key, low - off, t0 - off):
            if not matches(r["conditions"], {k: row.get(k) for k in w.fields}):
                continue
            ref, on_date = str(row["ref"])[:100], row["on"]
            if isinstance(on_date, str):
                on_date = date.fromisoformat(on_date)
            full = used >= r["daily_cap"]
            run_id = await ctx.val("""INSERT INTO flow.automation_runs (automation_id, ref, on_date, status, note) VALUES (%s, %s, %s, %s, %s)
                                      ON CONFLICT DO NOTHING RETURNING id""",
                                   (r["id"], ref, on_date, "skipped" if full else "queued", f"Đã chạm trần {r['daily_cap']} lần hôm nay" if full else ""))
            if run_id is None or full:
                continue   # (luật, bản ghi, ngày) đã có lần chạy ⇒ không lần hai
            used += 1
            queued += 1
            data = {k: row.get(k) for k in (*w.fields, *w.ids, *(w.open[1].values() if w.open else ()), "owner_id")}
            ctx.emit(DUE_EVENT, run_id=run_id, automation_id=r["id"], ref=ref, on=on_date, data=data)
    return {"queued": queued}


@m.on(DUE_EVENT)
async def run_dated_automation(ctx: Ctx, event: Event) -> None:
    """MỘT lần luật theo ngày tới hạn với một bản ghi: chạy hành động bằng quyền người đặt luật (nạp lại lúc này), ghi kết quả vào đúng hàng đã giữ chỗ.
    Lỗi hệ thống ⇒ ném ra: outbox thử lại, hàng vẫn "Đang chờ chạy" (khoá chống lặp ⇒ thử lại không nhân đôi)."""
    if ctx.historical:
        return
    run = await ctx.one("SELECT * FROM flow.automation_runs WHERE id = %s AND status = 'queued' AND job IS NULL FOR UPDATE", (event.payload["run_id"],))
    if run is None:
        return   # đã chạy (sự kiện giao lại), hoặc đã thành việc chờ trợ lý soạn
    r = await ctx.one("SELECT * FROM flow.automations WHERE id = %s", (run["automation_id"],))
    w = when_of(None, r["date_source"]) if r else None
    if r is None or not r["active"] or w is None:
        out = Outcome("skipped", "Luật đã tắt / nguồn ngày không còn")
    else:
        terms = await vocab.terms(ctx.conn, ctx.p.tenant_id)
        data = dict(event.payload.get("data") or {})
        out = await _execute(ctx, r, w, data, f"auto:{r['id']}:{run['ref']}:{run['on_date']}", int(event.payload.get(executor.CHAIN_KEY) or 0), terms)
    await _finish_run(ctx, run["id"], out)


async def _finish_run(ctx: Ctx, run_id: int, out: Outcome) -> None:
    await ctx.exec("UPDATE flow.automation_runs SET status = %s, note = %s, job = %s WHERE id = %s",
                   (out.status, out.note[:500], Jsonb(out.job) if out.job is not None else None, run_id))


# ── Luật theo lịch (ADR 0265 mục 1) ──

@m.command("sweep_scheduled_automations", resource="flow.automations", title="Xét luật tự động theo lịch", internal=True, agent="deny",
           sweep="SELECT DISTINCT tenant_id FROM flow.automations WHERE active AND schedule IS NOT NULL AND next_run_at <= now()")
async def sweep_scheduled_automations(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Vòng quét mỗi phút (quyền hệ thống): luật theo lịch tới hạn ⇒ khoá dòng luật (``SKIP LOCKED`` — hai tiến trình không lấy trùng), đẩy
    ``next_run_at`` sang lượt kế SAU BÂY GIỜ trong cùng phiên ghi, giữ chỗ (luật, lượt) — chỉ mục duy nhất ⇒ một lượt chạy một lần — rồi phát
    ``flow.automation_scheduled``: phần chạy ở bên nghe (sau commit, thử lại lùi dần như mọi sự kiện). Lượt lỡ quá ``STALE`` (máy chủ nghỉ) ⇒ ghi
    "bỏ qua", không chạy; nhiều lượt lỡ ⇒ chỉ lượt đến hạn sớm nhất được xét, các lượt giữa bị nhảy qua — không dội bù hàng loạt."""
    if ctx.historical:
        return {"queued": 0}
    now = clock()
    rules = await ctx.all("""SELECT * FROM flow.automations WHERE active AND schedule IS NOT NULL AND next_run_at <= %s
                             ORDER BY next_run_at FOR UPDATE SKIP LOCKED""", (now,))
    queued = 0
    for r in rules:
        slot = r["next_run_at"]
        await ctx.exec("UPDATE flow.automations SET next_run_at = %s WHERE id = %s", (timetable.next_after(r["schedule"], r["run_at"], now), r["id"]))
        if now - slot > STALE:
            await ctx.exec("""INSERT INTO flow.automation_runs (automation_id, slot_at, status, note) VALUES (%s, %s, 'skipped', %s)
                              ON CONFLICT DO NOTHING""",
                           (r["id"], slot, f"Bỏ lượt {slot.astimezone(zone()):%H:%M %d/%m} — máy chủ nghỉ / luật tắt quá {int(STALE.total_seconds() // 3600)} giờ, "
                                           "không chạy bù"))
            continue
        full = await _used_today(ctx, r["id"]) >= r["daily_cap"]
        run_id = await ctx.val("""INSERT INTO flow.automation_runs (automation_id, slot_at, status, note) VALUES (%s, %s, %s, %s)
                                  ON CONFLICT DO NOTHING RETURNING id""",
                               (r["id"], slot, "skipped" if full else "queued", f"Đã chạm trần {r['daily_cap']} lần hôm nay" if full else ""))
        if run_id is None or full:
            continue
        queued += 1
        ctx.emit(SCHEDULE_EVENT, run_id=run_id, automation_id=r["id"])
    return {"queued": queued}


@m.on(SCHEDULE_EVENT)
async def run_scheduled_automation(ctx: Ctx, event: Event) -> None:
    """MỘT lượt của luật theo lịch: hành động bằng quyền người đặt luật (nạp lại lúc này), kết quả ghi vào đúng hàng đã giữ chỗ. Lỗi hệ thống ⇒ ném ra:
    outbox thử lại, hàng vẫn "Đang chờ chạy" (khoá chống lặp theo (luật, lượt) ⇒ thử lại không nhân đôi)."""
    if ctx.historical:
        return
    run = await ctx.one("SELECT * FROM flow.automation_runs WHERE id = %s AND status = 'queued' AND job IS NULL FOR UPDATE", (event.payload["run_id"],))
    if run is None:
        return
    r = await ctx.one("SELECT * FROM flow.automations WHERE id = %s", (run["automation_id"],))
    w = when_of(None, None, r["schedule"]) if r else None
    if r is None or not r["active"] or w is None:
        out = Outcome("skipped", "Luật đã tắt / không còn theo lịch")
    else:
        out = await _execute(ctx, r, w, {}, f"auto:{r['id']}:{run['slot_at'].isoformat()}", int(event.payload.get(executor.CHAIN_KEY) or 0), None)
    await _finish_run(ctx, run["id"], out)


# ── Nhờ trợ lý soạn (ADR 0265 mục 1): trợ lý của TỪNG người nhận, không người, bộ công cụ hẹp — chỉ ra thẻ đề xuất + một lời báo ──

async def _agent_draft(rp: Principal, rule: Mapping[str, Any], job: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Một lần soạn cho MỘT người (mẫu ``work._agent_exception``): quyền của chính người đó (trợ lý = người gọi thứ hai), chỉ công cụ luật khai ∩ công cụ
    người đó dùng được, lượt KHÔNG NGƯỜI ⇒ lệnh ghi thành thẻ đề xuất (``agent_policy.decide``; lệnh trợ lý vốn tự làm không vào bộ — ``draft_tool_ok``).
    Trần số lượt gọi model + token mỗi lần; đo token (kể cả khi lỗi) vào lượt dùng AI của người đó."""
    from pydantic_ai import Agent, UsageLimits
    from pydantic_ai.usage import RunUsage

    from barista.agent import chat
    from barista.agent import usage as metering
    from barista.agent.llm import CHAT_SETTINGS, model_for

    async def emit(_: dict[str, Any]) -> None:
        return None

    wanted = set(rule["tools"])
    ops = [(op, mode) for op, mode in await chat.catalog(rp) if op.name in wanted and draft_tool_ok(op)]
    if not ops:
        raise Invalid("không còn công cụ nào của luật mà người này dùng được")
    run = chat.ToolRun(rp, emit, f"auto:{job['key']}:{rp.user_id}", recent_modules=tuple(sorted({op.module for op, _ in ops})))
    tools = run.tools(ops)
    if run.plan is not None:   # bộ HẸP của luật mang đủ ngay từ đầu — không để lớp "tìm công cụ theo câu hỏi" cắt bớt (lượt này không có câu hỏi)
        run.plan.visible |= {op.name for op, _ in ops}
    model = model_for(rp, DRAFT_MODEL)
    agent = Agent(model, instructions=await chat.instructions(rp), tools=tools, model_settings=CHAT_SETTINGS)
    task = f"[Luật tự động “{rule['name']}” chạy theo lịch — không có ai đang chat] {job['text']} {DRAFT_STYLE}"
    used, ok, msgs = RunUsage(), False, []
    try:
        with executor.tracing(f"auto:{rule['id']}"):
            result = await agent.run(chat.with_facts(task, await chat.turn_facts(rp)), usage=used,
                                     usage_limits=UsageLimits(request_limit=DRAFT_REQUESTS, total_tokens_limit=DRAFT_TOKENS))
        ok, msgs = True, result.new_messages()
        return str(result.output).strip(), [c for c in run.cards if c.get("kind") == "proposal"]
    finally:
        await metering.record(rp, surface="work", category="auto:draft", modules=metering.category_of(run.steps)[1], usage=used,
                              model=getattr(model, "model_name", ""), messages=msgs, ok=ok, steps=run.steps)


async def _draft_for(sysp: Principal, rule: Mapping[str, Any], job: Mapping[str, Any], user_id: UUID) -> dict[str, Any]:
    from barista.agent import morning

    async with db.tenant_tx(sysp, read_only=True) as conn:
        rp = await auth.load(conn, sysp.tenant_id, user_id=user_id)
    if rp is None:
        raise Invalid("người nhận không còn hoạt động trong công ty")
    if not rp.ai:
        raise Invalid(f"{rp.name}: công ty tắt trợ lý")
    try:
        text, cards = await _agent_draft(rp, rule, job)
    except Exception as e:   # model lỗi / hết hạn mức / quá trần token ⇒ lần soạn này hỏng, có ghi; người khác trong cùng lượt vẫn được soạn
        raise Invalid(f"{rp.name}: {e}") from e
    filed = await morning.file_cards(rp, f"{morning.AUTO}:{job['key']}:{user_id}", cards, f"Luật tự động “{rule['name']}”")
    return {"user_id": str(user_id), "name": rp.name, "text": text, "filed": filed}


async def _prepare_drafts(prep: Prep, inp: NoInput) -> list[dict[str, Any]]:
    """Bước chuẩn bị (ngoài phiên ghi) của vòng soạn: thuê tối đa ``DRAFTS_PER_SWEEP`` việc chờ soạn (phiên ghi ngắn), gọi model cho từng người nhận — không giữ kết
    nối / khoá dòng nào lúc chờ model. Tiến trình chết giữa chừng ⇒ hết thuê, lượt sau làm lại (thẻ và thông báo có khoá chống lặp)."""
    async with db.tenant_tx(prep.p) as conn:
        cur = await conn.execute(
            """UPDATE flow.automation_runs SET lease_until = now() + make_interval(secs => %s)
               WHERE id IN (SELECT id FROM flow.automation_runs WHERE status = 'queued' AND job IS NOT NULL
                            AND (lease_until IS NULL OR lease_until < now()) ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED)
               RETURNING id, automation_id, job, lease_until""", (DRAFT_LEASE_S, DRAFTS_PER_SWEEP))
        jobs = await cur.fetchall()
        if not jobs:
            return []
        cur = await conn.execute("SELECT id, name, tools, active FROM flow.automations WHERE id = ANY(%s)", ([j["automation_id"] for j in jobs],))
        rules = {r["id"]: r for r in await cur.fetchall()}
    out = []
    for j in jobs:
        rule = rules.get(j["automation_id"])
        res: dict[str, Any] = {"run_id": j["id"], "lease": j["lease_until"], "name": rule["name"] if rule else "", "people": [], "errors": []}
        if rule is None or not rule["active"]:
            res["errors"].append("luật đã tắt")
        else:
            for uid in j["job"]["users"]:
                try:
                    res["people"].append(await _draft_for(prep.p, rule, j["job"], UUID(uid)))
                except Exception as e:
                    log.warning("luật %s: trợ lý soạn cho %s lỗi: %r", rule["id"], uid, e)
                    res["errors"].append(str(e)[:200])
        out.append(res)
    return out


@m.command("run_automation_drafts", resource="flow.automations", title="Trợ lý soạn theo luật tự động", internal=True, agent="deny",
           prepare=_prepare_drafts,
           sweep="""SELECT DISTINCT tenant_id FROM flow.automation_runs WHERE status = 'queued' AND job IS NOT NULL
                    AND (lease_until IS NULL OR lease_until < now())""")
async def run_automation_drafts(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Vòng quét (quyền hệ thống): ghi kết quả lượt soạn — chỉ khi việc còn đúng lượt thuê của lượt này — rồi báo từng người nhận (lời trợ lý + số thẻ
    đề xuất chờ họ xác nhận). Không có đường nào từ đây tới lệnh ghi chạy thật: thẻ chỉ chạy khi người nhận bấm Xác nhận, bằng quyền của họ."""
    done = 0
    for x in ctx.prepared or []:
        people, errors = x["people"], x["errors"]
        cards = sum(p["filed"] for p in people)
        note = (f"Trợ lý đã soạn cho {len(people)} người, {cards} thẻ đề xuất" if people else "Không soạn được") + (f" — lỗi: {'; '.join(errors)}" if errors else "")
        n = await ctx.exec("UPDATE flow.automation_runs SET status = %s, note = %s, lease_until = NULL WHERE id = %s AND status = 'queued' AND lease_until = %s",
                           ("done" if people else "failed", note[:500], x["run_id"], x["lease"]))
        if not n:
            continue
        done += 1
        for p in people:
            body = p["text"][:1800] + (f"\n\n{p['filed']} đề xuất chờ bạn xác nhận ở “Trợ lý soạn sẵn”." if p["filed"] else "")
            await notify.send(ctx.conn, [UUID(p["user_id"])], kind="automation", title=f"{x['name']}: trợ lý đã soạn", body=body or "Không có gì cần báo.",
                              push="Trợ lý đã soạn xong một việc theo lịch", key=f"auto:draft:{x['run_id']}:{p['user_id']}", actor=None)
    return {"drafted": done}
