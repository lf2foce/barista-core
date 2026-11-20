"""Đo màn / công cụ chính qua executor.query bằng principal thật (nạp như test: seed.who). Chỉ đọc. CẦN gói Bất động sản + Hoa hồng đã cài vào môi
trường lõi (bộ đo của sàn môi giới — chưa dời theo gói, 0191 D3); thiếu ⇒ dừng kèm lệnh cài.

uv run python bench.py [--runs 10] [--only substr] [--roles sale,lead] [--out file.json] [--xl]

``--xl``: dữ liệu ``gen_xl.sql`` (~800 người, 30 đội, cây 4 tầng, 29.000 giao dịch) — người đo là sale / trưởng đội của đội mới (26 người) và
GĐ khối 1 (6 đội ≈ 150 người); ``--set report`` chỉ đo các màn danh sách + báo cáo quản trị đề bài 11/11 nêu.
"""

import argparse
import asyncio
import json
import pathlib
import statistics
import time
from typing import Any

import psycopg

import barista.modules  # noqa: F401
from barista import seed
from barista.config import settings
from barista.db import db
from barista.kernel import executor

ROLES = {"sale": "kdd_1", "lead": "kdd_0", "khoi": "gdkhoi1", "unit": "qlvung", "director": "giamdoc", "acct": "ketoan"}
XL_ROLES = {"sale": "kd01_1", "lead": "kd01_0", "khoi": "gdkhoi1", "unit": "qlvung", "director": "giamdoc", "acct": "ketoan"}

# đếm câu SQL mỗi lượt (phát hiện N+1)
COUNT = {"n": 0}
_orig_c = psycopg.AsyncCursor.execute
_orig_x = psycopg.AsyncConnection.execute


async def _cx(self, *a, **k):  # type: ignore[no-untyped-def]
    COUNT["n"] += 1
    return await _orig_c(self, *a, **k)


async def _xx(self, *a, **k):  # type: ignore[no-untyped-def]
    COUNT["n"] += 1
    return await _orig_x(self, *a, **k)


psycopg.AsyncCursor.execute = _cx  # type: ignore[method-assign]
psycopg.AsyncConnection.execute = _xx  # type: ignore[method-assign]


async def owner_one(sql: Any, params: Any = None) -> Any:
    async with db.owner.connection() as c:
        r = await (await c.execute(sql, params)).fetchone()
    return None if r is None else next(iter(r.values()))


async def inputs_for(local: str) -> dict[str, Any]:
    uid = await owner_one("SELECT id FROM core.users WHERE email = %s", (f"{local}@{seed.DOMAIN}",))
    team = await owner_one("SELECT team_id FROM core.team_members WHERE user_id = %s AND is_home", (uid,))
    lead = await owner_one(
        "SELECT id FROM crm.leads WHERE owner_id = %s OR team_id = %s ORDER BY (owner_id = %s) DESC, created_at DESC LIMIT 1",
        (uid, team, uid),
    )
    deal = await owner_one(
        "SELECT code FROM deals.deals WHERE sale_user_id = %s OR team_id = %s ORDER BY (sale_user_id = %s) DESC LIMIT 1",
        (uid, team, uid),
    )
    deal_id = await owner_one("SELECT id FROM deals.deals WHERE code = %s", (deal,))
    room = await owner_one(
        "SELECT r.id FROM talk.rooms r JOIN talk.room_members m ON m.room_id = r.id WHERE m.user_id = %s ORDER BY r.team_id IS NULL LIMIT 1",
        (uid,),
    )
    room_thread = await owner_one("SELECT id FROM talk.threads WHERE subject_kind = 'talk.room' AND subject_id = %s", (room,))
    return {
        "lead": str(lead) if lead else None,
        "deal": deal,
        "deal_id": str(deal_id) if deal_id else None,
        "room": str(room) if room else None,
        "room_thread": str(room_thread) if room_thread else None,
    }


def report_cases(i: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Đường đọc chính đề bài 11/11 (danh sách + báo cáo quản trị) — mỗi màn một lần gọi executor.query như app."""
    out: list[tuple[str, str, dict[str, Any]]] = [
        ("crm.leads", "crm.leads (đang theo)", {}),
        ("crm.leads", "crm.leads (tất cả)", {"stage": ""}),
        ("crm.leads", "crm.leads tìm tên", {"stage": "", "q": "Nguyễn Văn"}),
        ("crm.leads", "crm.leads tìm SĐT", {"stage": "", "q": "0100001"}),
        ("deals.list", "deals.list", {}),
        ("deals.my_deals", "deals.my_deals", {}),
        ("leadflow.mine", "leadflow.mine", {}),
        ("leadflow.late", "leadflow.late", {}),
        ("leadflow.queue", "leadflow.queue", {}),
        ("commission.wallet", "commission.wallet", {}),
        ("commission.accruals", "commission.accruals", {}),
        ("acct.receivables", "acct.receivables", {}),
        ("exec.overview", "exec.overview", {}),
        ("exec.monthly", "exec.monthly (năm)", {"period": "year"}),
        ("exec.revenue_months", "exec.revenue_months", {}),
        ("exec.revenue_projects", "exec.revenue_projects", {}),
        ("exec.revenue_sales", "exec.revenue_sales", {}),
        ("exec.fees_cash", "exec.fees_cash", {}),
        ("acct.cashflow", "acct.cashflow", {}),
        ("kpi.board", "kpi.board", {}),
        ("mkt.overview", "mkt.overview", {}),
    ]
    if i["deal"]:
        out.append(("deals.case_file", "deals.case_file", {"code": i["deal"]}))
    return out


def cases(i: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    out: list[tuple[str, str, dict[str, Any]]] = [
        ("crm.leads", "crm.leads (đang theo)", {}),
        ("crm.leads", "crm.leads (tất cả)", {"stage": ""}),
        ("crm.leads", "crm.leads tìm tên", {"stage": "", "q": "Nguyễn Văn"}),
        ("crm.brief", "crm.brief", {}),
        ("crm.agenda", "crm.agenda", {}),
        ("crm.reminders", "crm.reminders", {}),
        ("crm.suggestions", "crm.suggestions", {}),
        ("leadflow.queue", "leadflow.queue", {}),
        ("leadflow.overview", "leadflow.overview", {}),
        ("deals.list", "deals.list", {}),
        ("deals.installments_due", "deals.installments_due", {}),
        ("deals.ds_deals", "deals.ds_deals by team×tháng", {"by": ["team", "signed_month"], "measures": ["count", "fee"]}),
        ("catalog.units", "catalog.units", {}),
        ("catalog.projects", "catalog.projects", {}),
        ("commission.wallet", "commission.wallet", {}),
        ("commission.wallets", "commission.wallets", {}),
        ("commission.accruals", "commission.accruals", {}),
        ("acct.receivables", "acct.receivables", {}),
        ("acct.cashflow", "acct.cashflow", {}),
        ("acct.reconcile", "acct.reconcile", {}),
        ("acct.balances", "acct.balances", {}),
        ("acct.ds_receivables", "acct.ds_receivables", {}),
        ("core.ds_ledger", "core.ds_ledger", {}),
        ("talk.inbox", "talk.inbox", {}),
        ("talk.mine", "talk.mine", {}),
        ("kb.search", "kb.search", {"q": "chiết khấu thanh toán sớm"}),
        ("kb.documents", "kb.documents", {}),
        ("flow.board", "flow.board", {}),
        ("flow.tasks", "flow.tasks", {}),
        ("flow.needs_action", "flow.needs_action", {}),
        ("exec.overview", "exec.overview", {}),
        ("work.inbox", "work.inbox", {}),
        ("core.find", "core.find tên", {"q": "Nguyễn Văn"}),
        ("core.find", "core.find SĐT", {"q": "0100001"}),
        ("core.find", "core.find chữ hiếm", {"q": "nhà mẫu"}),
    ]
    if i["lead"]:
        out += [
            ("crm.journey", "crm.journey", {"lead_id": i["lead"]}),
            ("catalog.suggest_for_lead", "catalog.suggest_for_lead", {"lead_id": i["lead"]}),
        ]
    if i["deal"]:
        out += [
            ("deals.case_file", "deals.case_file", {"code": i["deal"]}),
            ("talk.thread", "talk.thread (giao dịch)", {"subject_kind": "deals.deal", "subject_id": i["deal_id"]}),
        ]
    if i["room"]:
        out += [
            ("talk.thread", "talk.thread (phòng đội 4.000 tin)", {"subject_kind": "talk.room", "subject_id": i["room"]}),
            ("talk.files", "talk.files (phòng, link)", {"thread_id": i["room_thread"], "kind": "link"}),
        ]
    return out


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def size_of(out: Any) -> int:
    return len(json.dumps(out, default=str, ensure_ascii=False).encode())


NEEDS = ("catalog", "deals", "commission")   # bộ đo quy mô SÀN MÔI GIỚI: màn + dữ liệu sinh (gen*.sql) là của hai gói đã rời lõi (0191, quyết định D3)


async def main() -> tuple[list[dict[str, Any]], str]:
    from barista.kernel import registry

    if missing := [m for m in NEEDS if m not in registry.MODULES]:
        raise SystemExit(f"scripts/perf đo màn của gói Bất động sản + Hoa hồng (thiếu module {', '.join(missing)}) — cài hai gói vào môi trường lõi "
                         "trước: uv pip install --python backend/.venv/bin/python --no-deps -e ../barista-packs/real_estate -e ../barista-packs/commission "
                         "rồi chạy bằng uv run --no-sync (CLAUDE.md, runbook 01)")
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--only", default="")
    ap.add_argument("--roles", default="sale,lead,khoi,unit,director,acct")
    ap.add_argument("--out", default="")
    ap.add_argument("--xl", action="store_true")
    ap.add_argument("--set", default="all", choices=("all", "report"))
    a = ap.parse_args()
    roles = XL_ROLES if a.xl else ROLES
    pick = report_cases if a.set == "report" else cases
    await db.open(settings.database_url, settings.owner_database_url, max_size=2)
    tid = await owner_one("SELECT id FROM core.tenants ORDER BY created_at LIMIT 1")
    results = []
    try:
        for role in a.roles.split(","):
            local = roles[role]
            p = await seed.who(tid, local)
            inp = await inputs_for(local)
            for name, label, args in pick(inp):
                if a.only and a.only not in label:
                    continue
                COUNT["n"] = 0
                t0 = time.perf_counter()
                r = await executor.query(p, name, args)
                first = (time.perf_counter() - t0) * 1000
                nsql = COUNT["n"]
                if r.status != "ok" and first > 3000:
                    results.append(
                        {"role": role, "op": label, "status": r.status, "first": round(first, 1), "err": r.error[:120]}
                    )
                    print(f"{role:8} {label:40} {r.status} sau {first:.0f} ms: {r.error[:80]}", flush=True)
                    continue
                if r.status != "ok":
                    results.append({"role": role, "op": label, "status": r.status, "err": r.error[:120]})
                    print(f"{role:8} {label:40} {r.status}: {r.error[:80]}", flush=True)
                    continue
                times = []
                for _ in range(a.runs if first < 3000 else 1):
                    t0 = time.perf_counter()
                    await executor.query(p, name, args)
                    times.append((time.perf_counter() - t0) * 1000)
                row = {
                    "role": role,
                    "op": label,
                    "status": "ok",
                    "p50": round(statistics.median(times), 1),
                    "p95": round(pct(times, 0.95), 1),
                    "min": round(min(times), 1),
                    "first": round(first, 1),
                    "sql": nsql,
                    "kb": round(size_of(r.output) / 1024, 1),
                    "rows": len(r.output.get("rows", []))
                    if isinstance(r.output, dict) and isinstance(r.output.get("rows"), list)
                    else None,
                }
                results.append(row)
                print(
                    f"{role:8} {label:40} p50 {row['p50']:8.1f}  p95 {row['p95']:8.1f}  min {row['min']:7.1f}  sql {nsql:4}  {row['kb']:7.1f} KB  rows {row['rows']}",
                    flush=True,
                )
    finally:
        await db.close()
    return results, a.out


if __name__ == "__main__":
    rows, out_path = asyncio.run(main())
    if out_path:
        pathlib.Path(out_path).write_text(json.dumps(rows, ensure_ascii=False, indent=1))
