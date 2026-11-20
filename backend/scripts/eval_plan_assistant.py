"""Đo trợ lý LẬP KẾ HOẠCH / GIAO VIỆC (quyết định 0073) bằng MODEL THẬT trên bản cài sàn môi giới mẫu đang chạy (dev).

Người hỏi: trưởng marketing (mkt@sanmau.demo). Mỗi câu chạy N lần SONG SONG. Đạt khi: gọi đúng công cụ, không lạc đề, không chạm trần bước, và
với câu lập kế hoạch thì thẻ đề xuất có ≥ 3 việc, mọi người nhận nằm trong danh sách ứng viên code đưa (flow.assignees), không dồn hết cho
một người, không hạn nào sau mốc. Các câu chỉ soạn ĐỀ XUẤT — không ghi gì vào DB.

Chạy:  uv run python scripts/eval_plan_assistant.py [--core http://127.0.0.1:8300] [--runs 5] [--only K1] [--user giamdoc@sanmau.demo]
"""

import argparse
import asyncio
import json
import os
import pathlib
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import eval_assistant as ev

DB = ""   # DB TẠM của lần đo — đặt từ --db / BARISTA_EVAL_DB trong main (không đoán tên DB)
USER = "mkt@sanmau.demo"
CAP_ERR = "hết số bước"
MILESTONE = date(2026, 12, 15)


@dataclass
class Case:
    key: str
    ask: str
    allowed: set[str]
    must: set[str]
    proposal: str = ""
    assignee: str = ""             # nếu có: đề xuất giao việc phải đúng người này
    user: str = ""                 # người hỏi riêng của câu này (trống = --user)
    expect: dict[str, Any] = field(default_factory=dict)   # ô bắt buộc có trong thẻ đề xuất (giá trị "*" = có là được)


@dataclass
class Run:
    tools: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    proposals: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""
    seconds: float = 0.0


PLAN_TOOLS = {"flow.assignees", "flow.propose_plan", "flow.adjust_task", "flow.metrics", "flow.teams", "flow.request_team", "flow.request_vendor", "flow.plans", "flow.plan", "flow.tasks", "catalog.projects", "mkt.campaigns",
              "mkt.overview", "flow.templates", "flow.template", "kb.search", "flow.needs_action"}
CASES = [
    Case("K1", "Lên kế hoạch chuẩn bị marketing cho đợt mở bán dự án Hồ Sen, mốc 15/12/2026, giao cho đội marketing.", PLAN_TOOLS,
         {"flow.assignees"}, proposal="flow.propose_plan"),
    Case("K2", "Giao cho bạn Hạnh viết 3 bài đăng giới thiệu Hồ Sen, hạn 20/11/2026.", PLAN_TOOLS | {"flow.create_task"},
         {"flow.create_task", "flow.propose_plan"}, assignee="mkt2@sanmau.demo"),
    Case("K3", "Kế hoạch nào của đội đang có nguy cơ trễ?", PLAN_TOOLS, {"flow.plans"}),
    # cần ít nhất một kế hoạch của đội có việc quá hạn trong dữ liệu
    Case("K4", "Kế hoạch nào đang trễ thì nhắc những người đang chậm giúp anh.", PLAN_TOOLS | {"flow.remind_plan"}, {"flow.remind_plan"},
         proposal="flow.remind_plan"),
    # 0074 B–C: mục tiêu đo được, nhờ phòng khác, thuê ngoài
    Case("K5", "Đảm bảo sự kiện mở bán Hồ Sen có 50 khách đặt lịch tham dự trước 30/11, giao đội marketing lo.", PLAN_TOOLS, {"flow.metrics"},
         proposal="flow.propose_plan", user="giamdoc@sanmau.demo", expect={"goal_metric": "crm.metric_appointments", "goal_target": 50}),
    Case("K6", "Lên kế hoạch mời khách dự mở bán Hồ Sen, mốc 30/11: đội mình làm thiệp và quảng cáo, nhờ phòng kinh doanh A gọi mời khách cũ.",
         PLAN_TOOLS, {"flow.propose_plan"}, proposal="flow.propose_plan", expect={"request_team": "*"}),
    Case("K7", "Thuê ngoài dựng phim 3D giới thiệu dự án Hồ Sen, ngân sách tối đa 30 triệu, cần trước 25/11.", PLAN_TOOLS, {"flow.request_vendor"},
         proposal="flow.request_vendor", expect={"budget_vnd": 30_000_000}),
]


async def chat(c: httpx.AsyncClient, h: dict[str, str], message: str) -> Run:
    run, final, t0 = Run(), {}, time.monotonic()
    async with c.stream("POST", "/v1/assistant/chat/stream", json={"message": message}, headers=h, timeout=300) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e["type"] == "step":
                final[e["n"]] = e
            elif e["type"] == "card" and e["card"]["kind"] == "proposal":
                run.proposals.append(e["card"])
            elif e["type"] == "done":
                run.text = e.get("text") or ""
    run.seconds = time.monotonic() - t0
    for n in sorted(final):
        run.tools.append(final[n]["tool"])
        if final[n].get("status") == "error":
            run.errors.append(final[n].get("error") or "")
    return run


def psql(sql: str) -> str:
    return subprocess.run(["psql", "-h", "localhost", "-d", DB, "-tAc", sql], capture_output=True, text=True).stdout.strip()


def expect_problems(inp: dict[str, Any], expect: dict[str, Any]) -> list[str]:
    """Ô bắt buộc trong thẻ: ở đầu vào lệnh, hoặc (request_team…) ở ít nhất một việc của kế hoạch."""
    out = []
    for k, v in expect.items():
        got = [inp.get(k)] + [t.get(k) for t in inp.get("tasks") or []]
        if not any(g not in (None, "", []) and (v == "*" or g == v) for g in got):
            out.append(f"thiếu {k}={v}")
    return out


def plan_problems(card: dict[str, Any], allowed_emails: set[str]) -> list[str]:
    tasks = card["input"].get("tasks") or []
    out = []
    if len(tasks) < 3:
        out.append(f"chỉ {len(tasks)} việc")
    people = {t.get("assignee_email", "").lower() for t in tasks}
    if people - allowed_emails:
        out.append(f"người ngoài danh sách {sorted(people - allowed_emails)}")
    if len(people) < 2 and len(tasks) >= 3:
        out.append("dồn hết cho một người")
    late = [t["title"] for t in tasks if t.get("due_on") and date.fromisoformat(t["due_on"]) > MILESTONE]
    if late:
        out.append(f"hạn sau mốc: {late}")
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", default="http://127.0.0.1:8300")
    ap.add_argument("--db", default=os.environ.get("BARISTA_EVAL_DB", ""), help="DB TẠM của lần đo, đã nạp mẫu (bắt buộc; hoặc BARISTA_EVAL_DB)")
    ap.add_argument("--force-db", action="store_true", help="cố ý đo trên DB dev / thử nghiệm (không nên)")
    ap.add_argument("--autonomy", choices=["off", "full", "keep"], default="off",
                    help="mức tự chủ của công ty: off = thận trọng (lệnh ghi chỉ thành thẻ đề xuất), full = Toàn quyền như demo (lệnh rủi ro thấp chạy thẳng), keep = giữ")
    ap.add_argument("--owner", default="admin@sanmau.demo", help="chủ tài khoản dev dùng để đặt mức tự chủ")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--only", default="")
    ap.add_argument("--user", default=USER, help="người hỏi (vd giám đốc: phạm vi việc cả công ty)")
    a = ap.parse_args()
    global DB
    DB = a.db
    ev.guard_db(DB, force=a.force_db)
    tenant = psql("SELECT id FROM core.tenants ORDER BY created_at LIMIT 1")
    h = {"X-Dev-User": a.user, "X-Tenant-Id": tenant}
    only = {x.strip().upper() for x in a.only.split(",") if x.strip()}
    async with httpx.AsyncClient(base_url=a.core) as c:
        await ev.set_autonomy(c, tenant, a.autonomy, owner=a.owner)
        r = await c.post("/v1/queries/flow.assignees", json={"team_code": "MKT"}, headers=h)
        allowed = {row["email"] for row in r.json()["output"]["rows"]}
        print(f"Ứng viên đội MKT theo code: {sorted(allowed)}")
        total_ok = total = 0
        for case in [x for x in CASES if not only or x.key in only]:
            hh = {**h, "X-Dev-User": case.user} if case.user else h
            runs = await asyncio.gather(*(chat(c, hh, case.ask) for _ in range(a.runs)))
            oks = 0
            for i, r in enumerate(runs, 1):
                extra = [t for t in r.tools if t not in case.allowed and t not in ev.NEUTRAL_TOOLS]   # core.open_tools = cơ chế nạp công cụ, không lạc đề
                cap = any(CAP_ERR in e for e in r.errors)
                hit = bool(set(r.tools) & case.must)
                problems: list[str] = []
                if case.proposal:
                    cards = [p for p in r.proposals if p["command"] == case.proposal]
                    problems = [] if (not cards and a.autonomy != "off" and case.proposal in r.tools) else ["không có thẻ " + case.proposal] if not cards else (
                        plan_problems(cards[-1], allowed) if case.proposal == "flow.propose_plan" and not case.expect else [])
                    if cards:
                        problems += expect_problems(cards[-1]["input"], case.expect)
                # công ty Toàn quyền: flow.create_task chạy thẳng ⇒ không có thẻ để đọc người nhận (người nhận chỉ kiểm được ở đường thẻ — chạy --autonomy off)
                direct = a.autonomy != "off" and "flow.create_task" in r.tools and not any(x["command"] == "flow.create_task" for x in r.proposals)
                if case.assignee and not direct:
                    got = {(x["input"].get("assignee_email") or "").lower() for x in r.proposals if x["command"] == "flow.create_task"}
                    got |= {t.get("assignee_email", "").lower() for x in r.proposals if x["command"] == "flow.propose_plan" for t in x["input"].get("tasks", [])}
                    if got != {case.assignee}:
                        problems.append(f"giao cho {sorted(got)}")
                ok = hit and not extra and not cap and not problems
                oks += ok
                plan = next((p for p in r.proposals if p["command"] == "flow.propose_plan"), None)
                detail = ""
                if plan:
                    tasks = plan["input"].get("tasks") or []
                    detail = f" · {len(tasks)} việc / {len({t.get('assignee_email') for t in tasks})} người"
                print(f"  {case.key}{i}: {'ĐẠT' if ok else 'TRƯỢT'} · {len(r.tools)} bước {r.tools}{detail}"
                      + (f" · LẠC ĐỀ {extra}" if extra else "") + (" · CHẠM TRẦN" if cap else "") + (f" · {problems}" if problems else "")
                      + f" · {r.seconds:.0f}s")
                if not ok:
                    print("     ⇒ " + re.sub(r"\s+", " ", r.text)[:240])
            total_ok, total = total_ok + oks, total + len(runs)
            print(f"[{case.key}] {oks}/{len(runs)} đạt · bước trung vị {statistics.median(len(r.tools) for r in runs)} · "
                  f"{statistics.median(r.seconds for r in runs):.0f}s — {case.ask}")
    print(f"TỔNG: {total_ok}/{total} đạt")


if __name__ == "__main__":
    asyncio.run(main())
