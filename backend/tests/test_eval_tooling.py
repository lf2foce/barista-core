"""Hàm thuần của harness eval (không model, không DB): đọc số trên thẻ, lấy ô mong đợi, lịch sử JSON + xu hướng (0144 mục 7)."""

import importlib.util
import json
import pathlib
import sys
from typing import Any

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_numbers_are_read_from_kpis_rows_and_detail_fields() -> None:
    ev = _load("eval_assistant")
    page = {"kpis": [{"label": "Chưa xong", "value": 7}], "rows": [{"amount": "1.250.000", "name": "x"}],
            "sections": [{"fields": [{"label": "Tỉ lệ", "value": "12,5%"}]}]}
    pages = [{"query": "q", "input": {}, "page": page}]
    assert ev.pages_contain(pages, 7) and ev.pages_contain(pages, 1250000) and ev.pages_contain(pages, 12.5)
    assert ev.pages_contain(pages, 1251000)   # sai số 0,5%
    assert not ev.pages_contain(pages, 8) and not ev.pages_contain(pages, "không phải số") and not ev.pages_contain([], 7)
    assert ev.field_value(page, "kpi:Chưa xong") == 7 and ev.field_value(page, "rows[0].amount") == "1.250.000"
    blocks = {"blocks": [{"type": "kpis", "items": [{"label": "Đang mở", "value": 3}]}]}
    assert ev.field_value(blocks, "kpi:Đang mở") == 3 and ev.pages_contain([{"page": blocks}], 3)
    assert ev._num(True) is None and ev._num("3 tỷ") == 3.0


def test_history_file_and_trend_detect_regressions(tmp_path: pathlib.Path) -> None:
    ev, tr = _load("eval_assistant"), _load("eval_trend")
    def rec(case: str, ok: bool) -> dict[str, Any]:
        return {"case": case, "role": "sale1", "ok": ok, "overreach": False, "steps": 2, "seconds": 3.0, "tokens": 100, "cost": 0.001,
                "number_ok": None, "first_try": 1, "opened": 0, "noise_calls": 0, "plan_ms": 1.0}
    p1 = ev.save_run(tmp_path, set_="base", label="thử", core="http://x", runs=1, records=[rec("S1", True), rec("S2", True)])
    d1 = json.loads(p1.read_text())
    assert d1["set"] == "base" and d1["label"] == "thử" and d1["stats"]["đạt %"] == 100 and len(d1["records"]) == 2
    d1["at"] = "2026-11-01T10:00"   # giả lịch sử cũ hơn
    p1.write_text(json.dumps(d1))
    p2 = ev.save_run(tmp_path, set_="base", label="thử", core="http://x", runs=1, records=[rec("S1", False), rec("S2", True), rec("S3", True)])
    runs = tr.load(tmp_path)
    assert [r["file"] for r in runs] == [p1.name, p2.name]
    worse, better = tr.diff(runs[0], runs[1])
    assert worse == ["S1"] and better == ["S3"]
    assert tr.main(["--dir", str(tmp_path)]) == 1   # có hồi quy ⇒ mã thoát 1 (dùng được làm bước thủ công trước khi gộp)
    assert tr.main(["--dir", str(tmp_path / "trong")]) == 0


def test_proposal_or_run_accepts_a_direct_run_only_when_declared() -> None:
    ev = _load("eval_assistant")
    strict = ev.Case("X", "sale1", "?", proposal="crm.schedule")
    either = ev.Case("X", "sale1", "?", proposal="crm.schedule", proposal_or_run=True)
    carded, ran, neither = ev.Run(proposals=["crm.schedule"]), ev.Run(ok_tools=["crm.schedule"]), ev.Run(ok_tools=["crm.leads"])
    assert ev.proposal_met(strict, carded) and ev.proposal_met(either, carded)
    assert not ev.proposal_met(strict, ran) and ev.proposal_met(either, ran)        # chạy thẳng chỉ đạt khi câu khai nhận cả hai
    assert not ev.proposal_met(either, neither) and ev.proposal_met(ev.Case("Y", "sale1", "?"), neither)
    failed = ev.Run(tools=["crm.schedule"], errors=["lỗi"])                         # gọi mà lỗi (không vào ok_tools) không tính là chạy thẳng
    assert not ev.proposal_met(either, failed)


def test_cache_read_share_is_reported_only_when_recorded() -> None:
    ev = _load("eval_assistant")
    base = {"role": "r", "ok": True, "overreach": False, "steps": 1, "seconds": 2.0, "cost": 0.001}
    old = ev._stats([{**base, "case": "A", "tokens": 100}])
    assert "đọc bộ đệm %" not in old                                                # bản ghi cũ: không bịa
    new = ev._stats([{**base, "case": "A", "tokens": 100, "cache_read": 60}, {**base, "case": "B", "tokens": 100, "cache_read": 20}])
    assert new["đọc bộ đệm %"] == 40


def test_timekeeping_eval_cases_name_real_operations_and_roles() -> None:
    """Bộ eval Chấm công + Lương (``eval_timekeeping.py``): mọi công cụ / thẻ đề xuất / lệnh cấm nêu trong ca là thao tác có thật; vai là người của
    công ty văn phòng mẫu; mọi lệnh cấm mà trợ lý có thể đụng tới là ``deny`` hoặc duyệt (không bao giờ ``allow``)."""
    from barista import seed_pay
    from barista.kernel import loader, registry

    loader.load()
    ev = _load("eval_timekeeping")
    people = {"luong", "ketoan", "nhansu", *(s[0] for s in seed_pay.STAFF)}
    for c in ev.CASES:
        assert c.role in people, c.key
        for op in c.must | c.forbid | ({c.proposal} if c.proposal else set()):
            assert op in registry.OPS, (c.key, op)
        for op in c.forbid - {"pay.slip", "pay.run", "pay.runs", "pay.compare_slips"}:
            assert registry.OPS[op].agent == "deny" or op == "core.decide", (c.key, op)
        if c.proposal:
            assert registry.OPS[c.proposal].agent == "propose", (c.key, c.proposal)


class _FakeStream:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):  # type: ignore[no-untyped-def]
        for e in self.events:
            yield json.dumps(e)


class _FakeClient:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    def stream(self, *a: Any, **k: Any) -> _FakeStream:
        return _FakeStream(self.events)


async def test_a_denied_step_is_not_a_successful_tool_run() -> None:
    """Bước ``denied`` (executor từ chối, nhật ký ghi denied, không dòng nào đổi) KHÔNG vào ``ok_tools`` ⇒ không thành cờ VƯỢT QUYỀN giả (eval L4 11/11);
    bước ``ok`` của công cụ ``forbid`` vẫn vào ``ok_tools`` ⇒ rò thật vẫn bị bắt."""
    ev = _load("eval_assistant")
    steps = [{"type": "step", "n": 1, "tool": "leadflow.reassign", "status": "denied", "error": "cần quyền đội"},
             {"type": "step", "n": 2, "tool": "acct.cashflow", "status": "ok"},
             {"type": "step", "n": 3, "tool": "crm.assign", "status": "invalid", "error": "thiếu"},
             {"type": "done", "text": "xong"}]
    run = await ev.chat(_FakeClient(steps), {}, "hỏi")
    assert run.tools == ["leadflow.reassign", "acct.cashflow", "crm.assign"]
    assert run.ok_tools == ["acct.cashflow"] and run.errors == ["cần quyền đội", "thiếu"]
    case = ev.Case("L4", "sale1", "?", forbid={"leadflow.reassign", "acct.cashflow"})
    assert sorted(set(run.ok_tools) & case.forbid) == ["acct.cashflow"]


class _SeqClient:
    """Mỗi lần gọi ``stream`` trả bộ sự kiện kế tiếp (lần đầu: nhà cung cấp lỗi, lần hai: trả lời được)."""

    def __init__(self, *rounds: list[dict[str, Any]]) -> None:
        self.rounds, self.calls = list(rounds), 0

    def stream(self, *a: Any, **k: Any) -> _FakeStream:
        self.calls += 1
        return _FakeStream(self.rounds.pop(0) if len(self.rounds) > 1 else self.rounds[0])


_BUSY = [{"type": "done", "text": "Nhà cung cấp mô hình đang quá tải hoặc gián đoạn — anh/chị thử lại sau ít phút."}]
_GOOD = [{"type": "step", "n": 1, "tool": "crm.leads", "status": "ok"}, {"type": "done", "text": "Có 3 lead."}]


async def test_provider_overload_is_retried_once_and_labelled_not_counted_as_a_failure() -> None:
    ev = _load("eval_assistant")
    ev.PROVIDER_RETRY_WAIT = 0
    ok = _SeqClient(_BUSY, _GOOD)
    run = await ev.chat_retry(ok, {}, "hỏi")
    assert ok.calls == 2 and run.retried and not run.provider_error and run.tools == ["crm.leads"]     # lần hai trả lời được ⇒ chấm bình thường
    bad = _SeqClient(_BUSY)
    run2 = await ev.chat_retry(bad, {}, "hỏi")
    assert bad.calls == 2 and run2.provider_error                                                       # vẫn lỗi ⇒ nhãn riêng, vòng chấm bỏ qua
    case = ev.Case("X", "sale1", "?")
    assert (await ev.turn(_SeqClient(_BUSY), {}, case)).provider_error
    normal = await ev.chat_retry(_SeqClient(_GOOD), {}, "hỏi")
    assert not normal.provider_error and not normal.retried                                             # câu trả lời thường không bị coi là lỗi
