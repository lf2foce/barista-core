"""Bàn đo bộ công cụ trợ lý theo VAI (ADR 0218) — model GIẢ có chủ đích, không gọi nhà cung cấp, chạy trên công ty mẫu của test.

Mỗi câu khai các công cụ ĐÚNG (gọi một trong số đó là đúng). Model giả làm như một model đọc đúng danh sách: công cụ đúng đang có ⇒ gọi luôn; chưa có
⇒ dùng đường mở thêm của trợ lý (``core.open_group`` nếu có, không thì ``core.open_tools`` với chính câu hỏi) rồi gọi; vẫn không có ⇒ trả lời
"không có công cụ" (= câu TRƯỢT: ngõ cụt). Số token là ƯỚC TÍNH xác định: bộ mã ``o200k_base`` của tiktoken trên đúng thứ mỗi lần gọi model gửi đi
(lời dặn + định nghĩa công cụ dạng JSON + tin nhắn, kể cả kết quả công cụ thật của dữ liệu mẫu) — không phải bộ mã của DeepSeek / Gemini, không trừ
phần đọc bộ đệm. Dùng để SO hai cách chọn công cụ trên cùng đề, không phải để tính tiền.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from pydantic_ai import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from barista.agent import chat
from barista.kernel import executor, registry


@lru_cache(maxsize=1)
def _enc() -> Any:
    import tiktoken

    return tiktoken.get_encoding("o200k_base")


def tokens(text: str) -> int:
    return len(_enc().encode(text, disallowed_special=()))


@dataclass
class Case:
    role: str                 # email local của người hỏi (dữ liệu mẫu)
    ask: str
    want: tuple[str, ...]     # gọi MỘT trong các công cụ này là đúng
    args: dict[str, Any] = field(default_factory=dict)
    denied: bool = False      # người hỏi KHÔNG có quyền công cụ này ⇒ đúng là không gọi được (không mở được bằng đường nào)


CASES: list[Case] = [
    # Sale
    Case("sale1", "Hôm nay em nên gọi lead nào trước?", ("crm.brief", "crm.reminders", "leadflow.mine", "crm.leads")),
    Case("sale1", "Tổng kết tháng của tôi", ("crm.my_month",)),
    Case("sale1", "Khách nào đến hạn gọi lại hôm nay?", ("crm.reminders", "crm.brief", "crm.leads")),
    Case("sale1", "30 ngày qua em liên hệ lead đúng hạn bao nhiêu phần trăm?", ("leadflow.scorecard",)),
    Case("sale1", "Lịch hẹn của em tuần này thế nào?", ("crm.agenda", "schedule.my_agenda", "schedule.week")),
    Case("sale1", "Chính sách bán hàng chiết khấu thanh toán sớm bao nhiêu phần trăm?", ("kb.search",), {"q": "chiết khấu thanh toán sớm"}),
    Case("sale1", "KPI của em tháng này đạt bao nhiêu rồi?", ("kpi.mine",)),
    Case("sale1", "Em còn bài đào tạo nào chưa làm không?", ("training.my",)),
    Case("sale1", "Thêm khách Lan 0109876543, quan tâm căn hộ 2 phòng ngủ", ("crm.create_lead",), {"name": "Lan", "phone": "0109876543"}),
    Case("sale1", "Em có việc nào sắp đến hạn không?", ("flow.tasks", "crm.brief", "crm.reminders")),
    # Trưởng nhóm
    Case("truonga", "Đội tôi đang để rơi lead nào — quá hạn gọi mà chưa ai xử lý?",
         ("leadflow.late", "crm.leads", "leadflow.queue", "leadflow.overview", "crm.brief")),
    Case("truonga", "Kế hoạch của đội tôi đang chạy tới đâu, có trễ không?", ("flow.plans",)),
    Case("truonga", "Giao cho bạn Bích gọi lại chị Nguyễn Thị Lan trước 5 giờ chiều mai.", ("flow.create_task",), {"title": "Gọi lại chị Lan"}),
    Case("truonga", "KPI đội tháng này thế nào, ai đứng đầu?", ("kpi.board", "kpi.ranking")),
    Case("truonga", "Ai trong đội phản hồi lead chậm nhất?", ("leadflow.scorecard",)),
    Case("truonga", "Tiến độ đào tạo của đội ra sao?", ("training.progress",)),
    Case("truonga", "Đội tôi hôm nay có việc gì đến hạn?", ("crm.reminders", "flow.tasks", "crm.brief")),
    Case("truonga", "Lịch tuần này của đội có gì?", ("schedule.week", "crm.agenda")),
    Case("truonga", "Khách nào trong đội lâu chưa được chăm sóc?", ("crm.leads", "crm.brief")),
    Case("truonga", "Có tài liệu quy trình chăm sóc khách không?", ("kb.search", "kb.documents"), {"q": "quy trình chăm sóc khách"}),
    # Giám đốc
    Case("giamdoc", "Tuần này công ty có gì cần tôi chú ý?", ("flow.needs_action", "health.overview", "core.approvals", "core.notifications")),
    Case("giamdoc", "Phiếu nào đang chờ tôi duyệt?", ("core.approvals",)),
    Case("giamdoc", "Dòng tiền dự kiến 30 ngày tới thế nào?", ("acct.cashflow",)),
    Case("giamdoc", "Chiến dịch quảng cáo nào đang hiệu quả nhất?", ("mkt.overview", "mkt.campaigns")),
    Case("giamdoc", "Khoản phải thu nào đang quá hạn?", ("acct.receivables",)),
    Case("giamdoc", "Kết quả kinh doanh tháng này lãi hay lỗ?", ("acct.pnl", "acct.pnl_data")),
    Case("giamdoc", "Dữ liệu đang có chỗ nào thiếu hay lỗi không?", ("health.overview",)),
    Case("giamdoc", "Tuần này lead mới tăng hay giảm, nguồn nào kéo xuống?", ("leadflow.overview", "crm.ds_leads", "mkt.overview", "crm.leads")),
    Case("giamdoc", "Xếp hạng KPI các đội tháng này?", ("kpi.ranking", "kpi.board")),
    Case("giamdoc", "Tháng này tiền vào, tiền ra từng tài khoản ngân hàng thế nào?", ("acct.cash_moves",)),
    # Marketing
    Case("mkt", "Chi phí quảng cáo tuần này bao nhiêu, ra bao nhiêu lead?", ("mkt.overview", "mkt.spend")),
    Case("mkt", "Chiến dịch Facebook tháng 10 đang ra sao?", ("mkt.campaigns", "mkt.overview")),
    Case("mkt", "Ghi chi phí quảng cáo hôm nay 2 triệu cho chiến dịch FB-HSR-T10.", ("mkt.record_spend",),
         {"campaign_code": "FB-HSR-T10", "day": "2026-11-14", "amount_vnd": 2000000}),
    Case("mkt", "Có đề xuất gì để tối ưu quảng cáo không?", ("mkt.ad_findings", "mkt.ad_suggestions")),
    Case("mkt", "Mẫu quảng cáo nào đang chạy?", ("mkt.creatives", "mkt.ads")),
    Case("mkt", "Đồng chi trả quảng cáo với chủ đầu tư tháng này thế nào?", ("mkt.cost_shares", "mkt.cost_share_statement")),
    Case("mkt", "Có tài liệu giới thiệu dự án Đồi Thông không?", ("kb.search", "kb.documents", "core.find"), {"q": "Đồi Thông"}),
    Case("mkt", "Tôi có việc gì đến hạn tuần này?", ("flow.tasks",)),
    Case("mkt", "Chi phí quảng cáo theo kênh tháng này?", ("mkt.ds_spend", "mkt.overview", "mkt.spend")),
    Case("mkt", "Xuất danh sách chiến dịch ra Excel giúp tôi.", ("mkt.export_campaigns",), denied=True),   # marketing KHÔNG có quyền xuất
    # Kế toán
    Case("ketoan", "Khoản phải thu nào đang quá hạn?", ("acct.receivables",)),
    Case("ketoan", "Dòng tiền dự kiến 30 ngày tới thế nào?", ("acct.cashflow",)),
    Case("ketoan", "Sao kê còn dòng nào chưa khớp không?", ("acct.reconcile", "acct.unapplied")),
    Case("ketoan", "Lệnh chi nào đang chờ chuyển khoản?", ("acct.payouts", "acct.remittances")),
    Case("ketoan", "Xuất dữ liệu MISA tháng này giúp tôi.", ("acct.misa_export",)),
    Case("ketoan", "Hôm nay tiền về bao nhiêu?", ("acct.cash_brief",)),
    Case("ketoan", "Có khoản chi nào đáng ngờ hay chi ngoài hệ thống không?", ("acct.payout_checks", "acct.out_lines")),
    Case("ketoan", "Tổng hợp thuế GTGT kỳ này?", ("acct.vat_summary",)),
    Case("ketoan", "Khoản phải trả nào sắp đến hạn?", ("acct.payables",)),
    Case("ketoan", "Hợp đồng đại lý quy định hạn thanh toán phí bao lâu?", ("kb.search",), {"q": "hạn thanh toán phí hợp đồng đại lý"}),
]

# NGOÀI bộ mặc định của vai (soạn SAU khi chọn bộ — để đo đường mở nhóm / tìm ngoài bộ, không chỉ câu bộ đã phủ)
OUTSIDE: list[Case] = [
    Case("sale1", "Có ai nhắn gì cho em trong trao đổi không?", ("talk.mine",)),
    Case("sale1", "Soạn giúp em công văn gửi chủ đầu tư xin bảng hàng", ("papers.draft_letter",), {"recipient": "CĐT", "subject": "bảng hàng", "body": "…"}),
    Case("sale1", "Em đã nhờ trợ lý nhớ những gì rồi?", ("memory.items", "memory.recall")),
    Case("truonga", "Trong phòng nhắn tin của đội hôm nay bàn gì?", ("talk.mine", "talk.thread")),
    Case("truonga", "Tìm giờ họp được cho cả đội chiều mai", ("schedule.find_time",)),
    Case("truonga", "Lead chờ chia của đội còn bao nhiêu, ai đang giữ?", ("leadflow.queue",)),
    Case("giamdoc", "Lịch tuần này của tôi có gì?", ("schedule.my_agenda", "schedule.week")),
    Case("giamdoc", "Ai trong công ty chưa đạt bài đào tạo?", ("training.progress",)),
    Case("giamdoc", "Có việc nào trợ lý đang theo mà chờ người không?", ("work.blockers", "work.inbox")),
    Case("mkt", "Sổ thay đổi quảng cáo tuần này có gì?", ("mkt.ad_changes",)),
    Case("mkt", "Có ai nhắn gì trong trao đổi của tôi không?", ("talk.mine",)),
    Case("mkt", "Bài đào tạo nào tôi chưa làm?", ("training.my",)),
    Case("ketoan", "Hoá đơn GTGT tháng này đã xuất những gì?", ("acct.vat_invoices",)),
    Case("ketoan", "Các kỳ kế toán nào đã khoá?", ("acct.periods",)),
    Case("ketoan", "Có ai nhắn gì cho tôi trong trao đổi không?", ("talk.mine",)),
]

ROLES = {"sale1": "Sale", "truonga": "Trưởng nhóm", "giamdoc": "Giám đốc", "mkt": "Marketing", "ketoan": "Kế toán"}


def _part_text(part: Any) -> str:
    if isinstance(part, UserPromptPart):
        return part.content if isinstance(part.content, str) else json.dumps([str(c) for c in part.content], ensure_ascii=False)
    if isinstance(part, ToolCallPart):
        return f"{part.tool_name} {json.dumps(part.args, ensure_ascii=False) if not isinstance(part.args, str) else part.args}"
    if isinstance(part, ToolReturnPart):
        return f"{part.tool_name} {json.dumps(part.content, ensure_ascii=False, default=str)}"
    if isinstance(part, TextPart):
        return part.content
    return str(getattr(part, "content", ""))


@dataclass
class Request:
    tools: list[str]
    instructions_tokens: int
    tools_tokens: int
    messages_tokens: int
    segments: list[tuple[int, int]] = field(default_factory=list)   # (dấu nội dung, token) theo thứ tự gửi: lời dặn → từng công cụ → từng phần tin nhắn

    @property
    def total(self) -> int:
        return self.instructions_tokens + self.tools_tokens + self.messages_tokens


class Bench(FunctionModel):
    """Model giả "đọc đúng": gọi công cụ đúng nếu thấy; không thấy thì mở thêm một lần rồi gọi; vẫn không có ⇒ trả lời trượt."""

    def __init__(self, case: Case) -> None:
        self.case = case
        self.requests: list[Request] = []
        self.tool_defs: list[dict[str, Any]] = []
        self.called = ""
        self.opened = 0
        self.dead_end = False
        super().__init__(self._respond, stream_function=self._stream, model_name="bench")

    def _decide(self, messages: list[ModelMessage], info: AgentInfo) -> Any:
        defs = [{"name": t.name, "description": t.description, "parameters": t.parameters_json_schema} for t in info.function_tools]
        if not self.requests:
            self.tool_defs = defs
        parts = [_part_text(pt) for m in messages for pt in m.parts if isinstance(m, ModelRequest | ModelResponse)]
        segs = [(hash(info.instructions or ""), tokens(info.instructions or ""))]
        segs += [(hash(json.dumps(d, ensure_ascii=False, sort_keys=True)), tokens(json.dumps(d, ensure_ascii=False))) for d in defs]
        segs += [(hash(t), tokens(t)) for t in parts]
        self.requests.append(Request([d["name"] for d in defs], segs[0][1], tokens(json.dumps(defs, ensure_ascii=False)), sum(t for _, t in segs[1 + len(defs):]), segs))
        names = {d["name"] for d in defs}
        if self.called:
            return "Dạ, em đã hiện kết quả ở thẻ bên trên."
        hit = next((w for w in self.case.want if chat.tool_name(w) in names), None)
        if hit is not None:
            self.called = hit
            return [ToolCallPart(tool_name=chat.tool_name(hit), args=dict(self.case.args), tool_call_id=f"b-{len(self.requests)}")]
        if self.opened == 0:
            self.opened = 1
            if "core__open_group" in names:
                group = registry.OPS[self.case.want[0]].module
                return [ToolCallPart(tool_name="core__open_group", args={"groups": [group]}, tool_call_id="b-open")]
            if "core__open_tools" in names:
                return [ToolCallPart(tool_name="core__open_tools", args={"query": self.case.ask}, tool_call_id="b-open")]
        self.dead_end = True
        return "Dạ, em chưa có công cụ cho việc này."

    def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        t = self._decide(messages, info)
        return ModelResponse(parts=[TextPart(t)] if isinstance(t, str) else t)

    async def _stream(self, messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        t = self._decide(messages, info)
        if isinstance(t, str):
            yield t
        else:
            yield {i: DeltaToolCall(name=c.tool_name, json_args=json.dumps(c.args), tool_call_id=c.tool_call_id) for i, c in enumerate(t)}


def uncached(req: Request, seen: list[list[tuple[int, int]]]) -> int:
    """Token KHÔNG đọc từ bộ đệm theo mô hình bộ đệm tiền tố (như DeepSeek / Gemini ngầm định): phần đầu giống hệt một lần gọi TRƯỚC đó của cùng người
    (đoạn trùng dài nhất, theo từng khối lời dặn / công cụ / tin nhắn) là đọc bộ đệm. Giả định thứ tự gửi lời dặn → công cụ → tin nhắn."""
    best = 0
    for prev in seen:
        n = 0
        for a, b in zip(prev, req.segments, strict=False):
            if a[0] != b[0]:
                break
            n += b[1]
        best = max(best, n)
    return sum(t for _, t in req.segments) - best


SEEN: dict[str, list[list[tuple[int, int]]]] = {}


async def run_case(p: Any, case: Case) -> dict[str, Any]:
    async def emit(_: dict[str, Any]) -> None:
        return None

    model = Bench(case)
    await chat.chat_turn(p, case.ask, emit=emit, model=model)
    first = model.requests[0]
    seen = SEEN.setdefault(case.role, [])
    fresh = 0
    for r in model.requests:
        fresh += uncached(r, seen)
        seen.append(r.segments)
    ok = (not model.called) if case.denied else (bool(model.called) and not model.dead_end)
    return {"role": case.role, "ask": case.ask, "ok": ok, "called": model.called,
            "first_call": ok and model.opened == 0, "opened": model.opened, "calls": len(model.requests),
            "tools_first": len(first.tools), "tools_tokens_first": first.tools_tokens, "instructions_tokens": first.instructions_tokens,
            "first_request_tokens": first.total, "turn_tokens": sum(r.total for r in model.requests), "turn_uncached": fresh, "tool_names_first": first.tools}


def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    import statistics

    out: dict[str, dict[str, Any]] = {}
    for local in ROLES:
        rs = [r for r in rows if r["role"] == local]
        if not rs:
            continue
        lists = [set(r["tool_names_first"]) for r in rs]
        stable = set.intersection(*lists)
        out[local] = {"n": len(rs), "ok": sum(r["ok"] for r in rs), "first_call": sum(r["first_call"] for r in rs),
                      "opened": sum(r["opened"] for r in rs),
                      "tools_first_median": statistics.median(r["tools_first"] for r in rs),
                      "tools_first_max": max(r["tools_first"] for r in rs),
                      "stable_tools": len(stable),
                      "tools_tokens_median": statistics.median(r["tools_tokens_first"] for r in rs),
                      "instructions_tokens": statistics.median(r["instructions_tokens"] for r in rs),
                      "first_request_median": statistics.median(r["first_request_tokens"] for r in rs),
                      "turn_median": statistics.median(r["turn_tokens"] for r in rs),
                      "turn_max": max(r["turn_tokens"] for r in rs),
                      "uncached_median": statistics.median(r["turn_uncached"] for r in rs[1:] or rs),   # bỏ câu đầu (bộ đệm còn trống)
                      "calls_median": statistics.median(r["calls"] for r in rs)}
    return out


async def _ok(p: Any, name: str, **inp: Any) -> None:
    r = await executor.run(p, name, inp)
    assert r.status == "ok", (name, r.status, r.error)


@asynccontextmanager
async def role_tools(admin: Any, role: str, tools: list[str] | None) -> AsyncIterator[None]:
    """Đặt tạm bộ của một vai (None = để trợ lý tự tuyển) rồi trả lại như cũ."""
    rows = {r["role"]: r for r in (await executor.query(admin, "core.role_tools", {})).output["rows"]}
    before = rows[role]
    if tools is None:
        await _ok(admin, "core.clear_role_tools", role=role)
    else:
        await _ok(admin, "core.set_role_tools", role=role, groups=[t for t in tools if "." not in t], tools=[t for t in tools if "." in t])
    try:
        yield
    finally:
        if before["state"] == "auto":
            await _ok(admin, "core.clear_role_tools", role=role)
        else:
            await _ok(admin, "core.set_role_tools", role=role, groups=before["groups_in"], tools=before["tools_in"])
