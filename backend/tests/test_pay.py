"""0150 lát 1 "Trả theo công": khe ``pay.record_work`` + sổ công chỉ thêm, hàm tính thuần + bản chụp, kỳ nháp → duyệt → chốt qua ``acct``, quy tắc
10% chỉ tính khi đã xác minh (kỳ chạy bóng thì tính nhưng không bao giờ chốt / ghi sổ / tới tay người lao động), quyền theo phiếu dưới SQL thẳng,
tách bạch nhiệm vụ ở DB. Các ngành dựng bằng chính hàm của dữ liệu mẫu (``barista.seed_pay``). Nguồn công của gói ngoài lõi (họ ``work_log`` — vd buổi dạy
đã xác nhận của gói Đào tạo) cùng kịch bản đầu cuối chốt → ghi sổ một lần → chi một đợt trên nguồn đó kiểm ở test của gói (repo gói:
``edu/tests/test_edu_pay.py``)."""

import ast
import asyncio
import json
import re
import shutil
import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from barista import seed_pay
from barista.db import db
from barista.kernel import executor, registry, regulations
from barista.kernel.agent_policy import risk_of
from barista.kernel.clock import month_of, today
from barista.kernel.executor import core_ctx
from barista.kernel.forms import field_specs
from barista.kernel.principal import Principal
from barista.modules.pay import engine
from barista.workspaces import manifest
from tests.conftest import edge, ok, owner_val, q

SHIPPED = regulations.ROOT
RULE = "PIT_FLAT_10"


@pytest.fixture
def verified(tmp_path: Path) -> Iterator[Path]:
    """Bản sao bộ quy định trong đó kế toán (giả) ĐÃ xác minh quy tắc 10% ở cả hai phiên bản — test đường chốt thật. Xong trả lại bộ phát hành."""
    root = tmp_path / "regulations"
    shutil.copytree(SHIPPED, root)
    for f in (root / "vn-payroll").glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        d["params"][RULE] |= {"status": "verified", "verified_by": "Kế toán thử (test), 09/11/2026"}
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    regulations.reload(root)
    yield root
    regulations.reload(SHIPPED)


def _domain(tag: str) -> str:
    return f"{tag}{uuid.uuid4().hex[:6]}.demo"


async def _sql(p: Principal, sql: Any, params: Any = None) -> list[dict[str, Any]]:
    async with db.tenant_tx(p, read_only=True) as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()


async def _decide(p: Principal, approval_id: Any) -> dict[str, Any]:
    return await ok(edge(p), "core.decide", approval_id=str(approval_id), approve=True)


async def _slips(run_id: Any) -> list[dict[str, Any]]:
    rows = await owner_val("SELECT coalesce(jsonb_agg(to_jsonb(s) ORDER BY s.name), '[]') FROM pay.slips s WHERE s.run_id = %s", (run_id,))
    return list(rows)


def _replay_ok(inputs: dict[str, Any], stored: list[dict[str, Any]]) -> bool:
    out = engine.compute(inputs)
    keys = ("seq", "component", "label", "kind", "amount_vnd", "basis", "rule_ref", "account", "unverified")
    return [{k: x[k] for k in keys} for x in out["lines"]] == [{k: x[k] for k in keys} for x in stored]


# ── Cấu trúc (đọc mã / registry) ──

def test_engine_is_pure() -> None:
    """``pay/engine.py``: chỉ thư viện chuẩn — không DB, không ngữ cảnh lệnh, không đồng hồ, không lõi."""
    tree = ast.parse(Path(engine.__file__).read_text())
    mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                                                                                        for a in n.names}
    assert mods <= {"dataclasses", "decimal", "typing"}, mods
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"now", "today", "ctx", "psycopg", "time"}, names & {"now", "today", "ctx", "psycopg", "time"}


def test_pay_speaks_no_industry_words() -> None:
    words = re.compile(r"giáo viên|học sinh|học viên|lớp học|buổi học|môi giới|bất động sản|căn hộ|\bspa\b|kỹ thuật viên|bệnh nhân|khám|ca làm", re.I)
    bad = []
    for src in sorted(Path(engine.__file__).parent.glob("*.py")):
        tree = ast.parse(src.read_text())
        doc = id(tree.body[0].value) if tree.body and isinstance(tree.body[0], ast.Expr) else None
        bad += [f"{src.name}:{n.lineno}: {n.value[:60]}" for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) != doc and words.search(n.value)]
    assert not bad, bad
    assert registry.pack_kind("pay") == "capability" and set(registry.pack_requires("pay")) >= {"directory", "finance", "hr"}


def test_every_pay_command_that_touches_money_or_authority_is_denied_to_the_assistant() -> None:
    """Trợ lý chỉ soạn kỳ nháp / tính lại (không ô tiền) — mọi lệnh khác của Lương (gửi duyệt, chốt, chi, nhập tay, đổi mức / khoản / loại hợp đồng)
    là ``deny``; không lệnh nào nới được (0144)."""
    cmds = [o for o in registry.OPS.values() if o.kind == "command" and o.module == "pay"]
    soft = {"pay.draft_run", "pay.compute_run"}
    assert {o.name for o in cmds if o.agent != "deny"} == soft
    for o in cmds:
        assert not o.promotable, o.name
        if o.name in soft:
            assert o.agent == "propose" and risk_of(o) == "low" and not [s for s in field_specs(o) if s["kind"] == "money"], o.name
    terms, seam = registry.get("hr.set_terms"), registry.get("pay.record_work")
    assert terms is not None and terms.agent == "deny"
    assert seam is not None and seam.callers == ("timekeeping",) and seam.family == "work_log"   # gói ngành / gói ngoài lõi: consumes("work_log") — 0186


def test_unverified_rule_holds_a_real_slip_and_marks_every_shadow_number() -> None:
    """Hàm thuần, không DB: cùng bản chụp, kỳ thật ⇒ phiếu DỪNG, không dòng khấu trừ; kỳ bóng ⇒ có dòng khấu trừ mang dấu chưa xác minh."""
    comp = {"code": "TRA", "version": 1, "label": "Thù lao", "kind": "earning", "calculator": "rate_x_qty", "taxable": True,
            "expense_account": "expense:payroll", "source": ""}
    base = {"version": 1, "run_kind": "regular", "period": "2026-10-01", "period_end": "2026-10-31", "pay_date": "2026-10-31",
            "party": {"id": "p1", "name": "A"}, "terms": {"contract_kind": "service", "term_months": None, "effective_from": "2026-01-01"},
            "entries": [{"id": "e1", "work_date": "2026-10-05", "kind": "qty", "unit": "session", "quantity": "20", "amount_vnd": None,
                         "role_key": "x.lead", "component": "", "source": "x.y"}],
            "rates": [{"id": "r1", "component": "TRA", "unit": "session", "role_key": "", "party_id": None, "amount_vnd": 200_000,
                       "effective_from": "2026-01-01"},
                      {"id": "r2", "component": "TRA", "unit": "session", "role_key": "x.lead", "party_id": None, "amount_vnd": 300_000,
                       "effective_from": "2026-01-01"}],
            "components": {"TRA": comp}, "advances_vnd": 0,
            "law": {RULE: {"rate_bps": 1000, "min_payment_vnd": 5_000_000, "contract_kinds": ["service"], "short_term_months_below": 3,
                           "ref": "vn-payroll@2026-07-01:PIT_FLAT_10", "status": "assumption", "verified_by": "", "label": "x"}}}
    real = engine.compute(base)
    assert real["held"] and "chưa xác minh" in real["held"][0] and [x["kind"] for x in real["lines"]] == ["earning"]
    assert real["lines"][0]["amount_vnd"] == 6_000_000                                   # mức đúng vai thắng mức chung
    shadow = engine.compute({**base, "run_kind": "shadow"})
    assert not shadow["held"] and shadow["unverified"] == ["vn-payroll@2026-07-01:PIT_FLAT_10"]
    tax = shadow["lines"][-1]
    assert (tax["kind"], tax["amount_vnd"], tax["unverified"]) == ("withholding", 600_000, True) and shadow["net_vnd"] == 5_400_000
    ok_ = engine.compute({**base, "law": {RULE: {**base["law"][RULE], "status": "verified"}}})
    assert not ok_["held"] and ok_["net_vnd"] == 5_400_000
    below = engine.compute({**base, "entries": [{**base["entries"][0], "quantity": "10"}], "law": {RULE: {**base["law"][RULE], "status": "verified"}}})
    assert below["lines"][-1]["amount_vnd"] == 0 and "dưới ngưỡng" in below["lines"][-1]["basis"] and below["net_vnd"] == 3_000_000
    emp = engine.compute({**base, "terms": {"contract_kind": "indefinite", "term_months": None}})
    assert any("luỹ tiến" in h for h in emp["held"])                                     # lát 1: nhân viên chính thức chặn, không đoán thuế
    # S3: 10 triệu chịu thuế + khấu trừ 9,5 triệu không giảm thuế ⇒ thực nhận âm SAU thuế ⇒ dừng (trước đây lọt tới lúc chốt)
    comps = {"LUONG": {**comp, "code": "LUONG", "calculator": "fixed"},
             "PHAT": {**comp, "code": "PHAT", "kind": "deduction", "calculator": "manual", "taxable": False}}
    neg = engine.compute({**base, "components": comps, "law": {RULE: {**base["law"][RULE], "status": "verified"}},
                          "entries": [{"id": "f", "work_date": "2026-10-01", "kind": "amount", "unit": "", "quantity": "0", "amount_vnd": 10_000_000,
                                       "role_key": "", "component": "LUONG", "source": "pay.fixed"},
                                      {"id": "m", "work_date": "2026-10-05", "kind": "amount", "unit": "", "quantity": "0", "amount_vnd": 9_500_000,
                                       "role_key": "", "component": "PHAT", "source": "pay.manual"}]})
    assert neg["net_vnd"] == -500_000 and any("không dương" in h for h in neg["held"])


# ── Các ngành, đầu cuối ──



async def test_approver_with_a_slip_in_the_run_cannot_lock_it(verified: Path) -> None:
    """Trigger DB: người duyệt có phiếu trong kỳ ⇒ không chốt; phiếu duyệt thành "lỗi", kỳ vẫn chờ duyệt, rút về nháp được."""
    co = await seed_pay.service_shop(_domain("pa"), "Cơ sở thử tách bạch (test)", runs=False)
    w, tid = co["who"], co["tenant_id"]
    # kế toán (người duyệt chốt) có một dòng công trong kỳ
    await ok(w["luong"], "hr.set_terms", party_id=await seed_pay._party_of(tid, w["ketoan"].email), contract_kind="service",
             effective_from=date(today().year, 1, 1).isoformat())
    await ok(w["luong"], "pay.import_work", person=w["ketoan"].email, work_date=today().isoformat(), unit="piece", quantity=1, role_key="goi_dau",
             ref="KT-1")
    run = await ok(w["luong"], "pay.draft_run", period=month_of(today()).isoformat(), pay_date=today().isoformat())
    sub = await ok(w["luong"], "pay.submit_run", run_id=run["run_id"])
    out = await _decide(w["ketoan"], sub["approval_id"])
    assert out["status"] == "failed" and "Người duyệt có phiếu" in out["error"], out
    assert await owner_val("SELECT status FROM pay.runs WHERE id = %s", (run["run_id"],)) == "submitted"
    assert await owner_val("SELECT count(*) FROM acct.payables WHERE tenant_id = %s", (tid,)) == 0
    with pytest.raises(psycopg.errors.RestrictViolation, match="rút kỳ về nháp"):         # đổi ngày khi đang chờ duyệt rồi chốt: không
        async with db.owner_tx() as c:
            await c.execute("UPDATE pay.runs SET posted_on = posted_on - 1 WHERE id = %s", (run["run_id"],))
    # hàng rào cuối ở DB, không qua lệnh: chốt thẳng bằng chủ bảng vẫn bị trigger từ chối
    with pytest.raises(psycopg.errors.RestrictViolation, match="Người duyệt có phiếu"):
        async with db.owner_tx() as c:
            await c.execute("UPDATE pay.runs SET status = 'locked', approved_by = %s, locked_at = now() WHERE id = %s",
                            ([w["ketoan"].user_id], run["run_id"]))
    with pytest.raises(psycopg.errors.RestrictViolation, match=r"(?i)ngày hạch toán"):   # S4: chốt không đổi ngày đã gửi duyệt
        async with db.owner_tx() as c:
            await c.execute("UPDATE pay.runs SET status = 'locked', approved_by = %s, locked_at = now(), posted_on = posted_on - 1 WHERE id = %s",
                            ([w["giamdoc"].user_id], run["run_id"]))
    await ok(w["luong"], "pay.return_to_draft", run_id=run["run_id"])
    # S2: tự nhập công / đặt mức riêng cho chính mình — không; người duyệt khoản nhập tay là người được trả ⇒ DB từ chối
    r = await executor.run(w["luong"], "pay.import_work", {"person": w["luong"].email, "work_date": today().isoformat(), "unit": "piece",
                                                            "quantity": 1, "role_key": "goi_dau", "ref": "SELF-1"})
    assert r.status == "denied" and "chính mình" in r.error, (r.status, r.error)
    await ok(w["luong"], "pay.upsert_component", code="THUONG", label="Thưởng", calculator="manual", expense_account="expense:payroll")
    await ok(w["luong"], "pay.upsert_component", code="LUONG_THANG", label="Lương tháng", calculator="fixed", expense_account="expense:payroll")
    r = await executor.run(w["luong"], "pay.set_rate", {"component": "LUONG_THANG", "unit": "month", "amount_vnd": 9_000_000,
                                                         "party_id": await seed_pay._party_of(tid, w["luong"].email), "effective_from": "2026-01-01"})
    assert r.status == "denied"
    req = await ok(w["luong"], "pay.request_entry", person=w["ketoan"].email, component="THUONG", amount_vnd=500_000, work_date=today().isoformat(),
                   reason="Thử: người duyệt tự duyệt khoản của mình")
    assert "500" not in await owner_val("SELECT summary FROM core.approvals WHERE id = %s", (req["approval_id"],))   # S1: phiếu duyệt chỉ mang mã
    out = await _decide(w["ketoan"], req["approval_id"])
    assert out["status"] == "failed" and "chính mình" in out["error"], out
    assert await owner_val("SELECT count(*) FROM pay.work_entries WHERE tenant_id = %s AND source = 'pay.manual'", (tid,)) == 0
    # tự ghi điều kiện trả của chính mình: không
    r = await executor.run(w["luong"], "hr.set_terms", {"party_id": await seed_pay._party_of(tid, w["luong"].email), "contract_kind": "service",
                                                         "effective_from": "2026-01-01"})
    assert r.status == "denied"


async def test_locked_accounting_period_refuses_the_submission(verified: Path) -> None:
    """Khoá kỳ 0119: ngày hạch toán của kỳ lương thuộc kỳ đã khoá ⇒ gửi duyệt bị chặn rõ, không để chốt hỏng sau khi duyệt."""
    co = await seed_pay.service_shop(_domain("pl"), "Cơ sở thử khoá kỳ (test)", runs=False)
    w = co["who"]
    period = month_of(today())
    await ok(w["ketoan"], "acct.lock_period", locked_through=min(regulations.period_end(period), today()).isoformat(), reason="Thử khoá kỳ")
    run = await ok(w["luong"], "pay.draft_run", period=period.isoformat(), pay_date=today().isoformat())
    r = await executor.run(w["luong"], "pay.submit_run", {"run_id": str(run["run_id"])})
    assert r.status == "invalid" and "khoá" in r.error, r.error
    assert await owner_val("SELECT status FROM pay.runs WHERE id = %s", (run["run_id"],)) == "draft"


async def test_service_shop_imports_from_excel_and_a_team_lead_never_sees_pay(verified: Path) -> None:
    co = await seed_pay.service_shop(_domain("ps"), "Cơ sở thử lương (test)", runs=False)
    w, tid = co["who"], co["tenant_id"]
    assert co["imported"] == len(seed_pay.SERVICE_ROWS)
    again = await seed_pay.imports.run_import(w["luong"], "pay.import_work",
                                              [{"Người": w["ktvlan"].email, "Ngày làm": f"{today():%d/%m/%Y}", "Đơn vị": "Lượt / sản phẩm", "Số lượng": 1,
                                                "Loại việc": "goi_dau", "Mã chứng từ": "PDV-0101-001"}], dry_run=False)
    assert again.output["rows"][0]["status"] in ("ok", "conflict")
    before = await owner_val("SELECT count(*) FROM pay.work_entries WHERE tenant_id = %s", (tid,))
    # khoản nhập tay đi qua duyệt; người xin không tự duyệt
    req = await ok(w["luong"], "pay.request_entry", person=w["ktvmai"].email, component="THUONG", amount_vnd=300_000, work_date=today().isoformat(),
                   reason="Thưởng khách khen (mẫu)")
    assert (await _decide(w["ketoan"], req["approval_id"]))["status"] == "executed"
    assert await owner_val("SELECT count(*) FROM pay.work_entries WHERE tenant_id = %s", (tid,)) == before + 1
    # hai lần lập kỳ cùng lúc: đúng một kỳ thật mở cho pháp nhân (khoá + chỉ mục duy nhất), không dòng công nào vào hai phiếu
    inp = {"period": month_of(today()).isoformat(), "pay_date": today().isoformat()}
    both = await asyncio.gather(executor.run(w["luong"], "pay.draft_run", inp), executor.run(w["luong"], "pay.draft_run", inp))
    assert sorted(r.status for r in both) in (["invalid", "ok"], ["conflict", "ok"]), [(r.status, r.error) for r in both]
    run = next(r.output for r in both if r.status == "ok")
    assert await owner_val("""SELECT count(*) - count(DISTINCT se.entry_id) FROM pay.slip_entries se JOIN pay.slips s ON s.id = se.slip_id
                              WHERE s.tenant_id = %s""", (tid,)) == 0
    by = {s["name"]: s for s in await _slips(run["run_id"])}
    assert by["Lý Thị Lan"]["gross_vnd"] == 6 * 60_000 + 3 * 150_000 + 2 * 120_000 + (60_000 if again.output["rows"][0]["status"] == "ok" else 0)
    assert by["Vương Ngọc Mai"]["gross_vnd"] == 5 * 150_000 + 4 * 60_000 + 300_000
    sub = await ok(w["luong"], "pay.submit_run", run_id=run["run_id"])
    assert (await _decide(w["ketoan"], sub["approval_id"]))["status"] == "executed"
    # người không có quyền phiếu (giám đốc mặc định cũng không) không thấy dòng nào dưới SQL thẳng
    assert await _sql(w["giamdoc"], "SELECT id FROM pay.slips") == []
    assert await _sql(w["giamdoc"], "SELECT id FROM pay.work_entries") == []
    assert len(await _sql(w["ktvlan"], "SELECT id FROM pay.slips")) == 1
    # màn do server mô tả: ai thấy màn nào, màn nào cũng mở được (phụ trách lương: đủ; kế toán: đọc; kỹ thuật viên: chỉ phiếu của mình)
    seen = {}
    for k in ("luong", "ketoan", "ktvlan", "giamdoc"):
        screens = [sc for ws in manifest(w[k], "x")["workspaces"] if ws["key"] == "pay" for sc in ws["screens"]]
        seen[k] = {sc["key"] for sc in screens}
        for sc in screens:
            r = await executor.query(w[k], sc["query"], {})
            assert r.ok, (k, sc["query"], r.error)
    assert {"pay_runs", "pay_entries", "pay_rates", "pay_components", "pay_terms", "pay_rules"} <= seen["luong"]   # bất thường: ở bàn việc
    assert (await q(w["luong"], "pay.anomalies"))["kpis"][0]["value"] >= 0
    assert seen["ktvlan"] == {"pay_my"} and "pay_runs" in seen["ketoan"] and seen["giamdoc"] <= {"pay_my"}
    # sổ công / mức chỉ thêm
    for sql in ("UPDATE pay.work_entries SET quantity = 99 WHERE tenant_id = %s", "DELETE FROM pay.rates WHERE tenant_id = %s",
                "UPDATE pay.components SET label = 'x' WHERE tenant_id = %s"):
        with pytest.raises(psycopg.errors.RaiseException):
            async with db.owner_tx() as c:
                await c.execute(sql, (tid,))


async def test_withholding_threshold_follows_the_pay_date(verified: Path) -> None:
    """Thuế theo NGÀY CHI: cùng 3 triệu, chi trước 01/07/2026 (ngưỡng 2 triệu) bị khấu trừ, chi từ 01/07/2026 (ngưỡng 5 triệu) thì không."""
    from barista.modules.pay.runs import law_on

    before, after = law_on(date(2026, 6, 30))[RULE], law_on(date(2026, 7, 1))[RULE]
    assert (before["min_payment_vnd"], after["min_payment_vnd"]) == (2_000_000, 5_000_000)
    assert before["ref"].startswith("vn-payroll@2026-01-01") and after["ref"].startswith("vn-payroll@2026-07-01")


async def test_pay_off_keeps_todays_paths(who: Any) -> None:
    """Công ty chưa bật Lương (công ty mẫu của bộ test): khe nối trả None, hoa hồng giữ đường cũ — không gì đổi khi nâng cấp."""
    admin = await who("admin")
    async with db.tenant_tx(admin) as conn:
        got = await core_ctx(conn, admin, module="timekeeping").call_system_optional(
            "pay.record_work", source="timekeeping.day", source_ref="x", key="timekeeping.day:x", user_id=admin.user_id, work_date=today().isoformat(),
            kind="qty", unit="session", quantity=1)
    assert got is None
    assert "pay" not in {w["key"] for w in manifest(admin, "x")["workspaces"]}


async def test_fixed_salary_is_paid_once_per_month_across_runs_and_entities(verified: Path) -> None:
    """B1 (review 09/11): lương cố định là DÒNG CÔNG ``pay.fixed:<người>:<tháng>:<khoản>`` — tháng đã chốt không lập kỳ thật thứ hai; pháp nhân thứ
    hai không trả lại; phiếu dừng thì tháng đó ở lại cho kỳ sau; không lập kỳ cho tháng chưa tới."""
    co = await seed_pay.service_shop(_domain("pf"), "Cơ sở thử lương cố định (test)", runs=False)
    w, tid = co["who"], co["tenant_id"]
    luong = w["luong"]
    await ok(luong, "pay.upsert_component", code="LUONG_THANG", label="Lương tháng", calculator="fixed", expense_account="expense:payroll")
    for who_ in ("ktvlan", "giamdoc"):   # giám đốc chưa ghi loại hợp đồng ⇒ phiếu dừng
        await ok(luong, "pay.set_rate", component="LUONG_THANG", unit="month", amount_vnd=6_000_000,
                 party_id=await seed_pay._party_of(tid, w[who_].email), effective_from=date(today().year - 1, 1, 1).isoformat())
    this = month_of(today())
    last = month_of(this - timedelta(days=1))
    run = await ok(luong, "pay.draft_run", period=last.isoformat(), pay_date=today().isoformat())
    held = {s["name"]: s["held_reason"] for s in await _slips(run["run_id"])}
    assert held["Lý Thị Lan"] == "" and "loại hợp đồng" in held["Lê Hồng Nhung"]
    sub = await ok(luong, "pay.submit_run", run_id=run["run_id"])
    assert (await _decide(w["ketoan"], sub["approval_id"]))["status"] == "executed"
    r = await executor.run(luong, "pay.draft_run", {"period": last.isoformat(), "pay_date": today().isoformat()})
    assert r.status == "invalid" and "đã chốt" in r.error                                 # tháng đã chốt: không kỳ thật thứ hai
    r = await executor.run(luong, "pay.draft_run", {"period": (this + timedelta(days=40)).isoformat(), "pay_date": (this + timedelta(days=45)).isoformat()})
    assert r.status == "invalid" and "chưa tới" in r.error
    cur = await ok(luong, "pay.draft_run", period=this.isoformat(), pay_date=today().isoformat())
    by = {s["name"]: s for s in await _slips(cur["run_id"])}
    boss = by["Lê Hồng Nhung"]
    fixed = [e for e in boss["inputs"]["entries"] if e["source"] == "pay.fixed"]
    assert sorted(e["amount_vnd"] for e in fixed) == [6_000_000, 6_000_000] and boss["held_reason"]   # tháng trước (dừng) + tháng này
    assert sum(e["amount_vnd"] for e in by["Lý Thị Lan"]["inputs"]["entries"] if e["source"] == "pay.fixed") == 6_000_000   # chỉ tháng này
    # khoá dòng chênh so ĐÚNG khoá (không LIKE): khoản "LUONGXTHANG" có dòng chênh không lẫn vào "LUONG_THANG"
    await ok(luong, "pay.upsert_component", code="LUONGXTHANG", label="Phụ cấp tháng", calculator="fixed", expense_account="expense:payroll")
    lan = await seed_pay._party_of(tid, w["ktvlan"].email)
    for amount in (1_000_000, 2_000_000):
        await ok(luong, "pay.set_rate", component="LUONGXTHANG", unit="month", amount_vnd=amount, party_id=lan, effective_from=last.isoformat())
        await ok(luong, "pay.compute_run", run_id=cur["run_id"])
    keys = await owner_val("""SELECT jsonb_agg(split_part(key, ':', 4) || ':' || amount_vnd ORDER BY created_at) FROM pay.work_entries
                              WHERE tenant_id = %s AND party_id = %s AND source = 'pay.fixed' AND work_date = %s""", (tid, lan, this))
    assert keys == ["LUONG_THANG:6000000", "LUONGXTHANG:1000000", "LUONGXTHANG:1000000"], keys
    # pháp nhân thứ hai cùng tháng: lương cố định đã ghi ở pháp nhân đầu ⇒ không ghi lần hai
    await ok(w["chu"], "admin.upsert_entity", code="PN2", name="Pháp nhân Hai (test)")
    second = await ok(luong, "pay.draft_run", period=this.isoformat(), pay_date=today().isoformat(), entity_code="PN2")
    assert await _slips(second["run_id"]) == []
    assert await owner_val("""SELECT count(*) FROM pay.work_entries WHERE tenant_id = %s AND source = 'pay.fixed'""", (tid,)) == 6
