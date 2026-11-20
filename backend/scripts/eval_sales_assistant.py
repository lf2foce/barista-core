"""Đo trợ lý của SALE (MODEL THẬT) trên bản cài sàn môi giới mẫu đang chạy — đi thẳng không, và tốn bao nhiêu token thật (core.ai_usage: input / cache).
Mỗi câu chạy N lần SONG SONG (các câu nối tiếp nhau để quy token về đúng câu). Các câu chỉ xem / đề xuất — ghi chép nhanh (crm.log_activity,
trợ lý được làm thẳng) có ghi thật vào DB dev.

CẦN core đang chạy CÓ gói Bất động sản + Hoa hồng (repo barista-packs, đã rời lõi — 0191; bộ đề này chưa dời theo gói, quyết định D3): core không cài
hai gói thì các câu về căn / giao dịch / hoa hồng hỏng vì không có công cụ, không phải vì trợ lý.

Chạy:  uv run --no-sync python scripts/eval_sales_assistant.py [--core http://127.0.0.1:8300] [--runs 5] [--only S1,S3] [--label truoc]
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

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import eval_assistant as ev

DB = ""   # DB TẠM của lần đo — đặt từ --db / BARISTA_EVAL_DB trong main (không đoán tên DB)
USER = "sale1@sanmau.demo"
CAP_ERR = "hết số bước"


@dataclass
class Case:
    key: str
    ask: str
    allowed: set[str]
    must: set[str]                 # phải gọi ít nhất một trong số này
    proposal: str = ""             # nếu có: phải có đề xuất lệnh này


@dataclass
class Run:
    tools: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)
    text: str = ""
    seconds: float = 0.0


CASES = [
    Case("S1", "Hôm nay em nên gọi lead nào trước?", {"crm.brief", "crm.leads", "crm.reminders", "crm.agenda", "crm.journey", "leadflow.mine"},
         {"crm.brief", "crm.leads", "leadflow.mine"}),
    Case("S2", "Hồ Sen còn căn 2 phòng ngủ nào dưới 5 tỷ không?", {"core.find", "catalog.units", "catalog.axis_summary", "catalog.projects",
                                                                  "catalog.unit_profile"}, {"catalog.units"}),
    Case("S3", "Chị Nguyễn Thị Lan vừa gọi, hẹn xem nhà mẫu 10h sáng thứ 7 tuần này, ghi lại giúp em.",
         {"core.find", "crm.leads", "crm.journey", "crm.log_activity", "crm.schedule", "crm.agenda"}, {"core.find", "crm.leads"}, proposal="crm.schedule"),
    Case("S4", "Ví hoa hồng của em đang có bao nhiêu, ứng tối đa được bao nhiêu?", {"commission.wallet", "commission.accruals", "commission.requests",
                                                                                      "commission.plans"}, {"commission.wallet"}),
    Case("S5", "Chính sách bán hàng Hồ Sen chiết khấu thanh toán sớm bao nhiêu phần trăm?", {"kb.search", "kb.quote", "kb.read", "kb.document",
                                                                                               "kb.documents"}, {"kb.search"}),
    Case("S6", "Soạn bản giới thiệu căn 06A01 Hồ Sen để gửi khách.", {"catalog.draft_unit_brief", "core.find", "catalog.unit_profile",
                                                                     "catalog.units", "papers.view"}, {"catalog.draft_unit_brief"}),
]


async def chat(c: httpx.AsyncClient, h: dict[str, str], message: str) -> Run:
    run, final, t0 = Run(), {}, time.monotonic()
    async with c.stream("POST", "/v1/assistant/chat/stream", json={"message": message}, headers=h, timeout=240) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e["type"] == "step":
                final[e["n"]] = e
            elif e["type"] == "card" and e["card"]["kind"] == "proposal":
                run.proposals.append(e["card"]["command"])
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
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    global DB
    DB = a.db
    ev.guard_db(DB, force=a.force_db)
    tenant = psql("SELECT id FROM core.tenants ORDER BY created_at LIMIT 1")
    uid = psql(f"SELECT id FROM core.users WHERE email = '{USER}'")
    h = {"X-Dev-User": USER, "X-Tenant-Id": tenant}
    only = {x.strip().upper() for x in a.only.split(",") if x.strip()}
    total_ok = total = 0
    async with httpx.AsyncClient(base_url=a.core) as c:
        await ev.set_autonomy(c, tenant, a.autonomy, owner=a.owner)
        for case in [x for x in CASES if not only or x.key in only]:
            since = psql("SELECT now()")
            runs = await asyncio.gather(*(chat(c, h, case.ask) for _ in range(a.runs)))
            await asyncio.sleep(1)
            use = psql(f"""SELECT coalesce(avg(input_tokens), 0)::int || '|' || coalesce(avg(cache_read_tokens), 0)::int || '|' || coalesce(avg(requests), 0)::numeric(4,1)
                           FROM core.ai_usage WHERE user_id = '{uid}' AND surface = 'chat' AND at >= '{since}'""").split("|")
            oks = 0
            for i, r in enumerate(runs, 1):
                extra = [t for t in r.tools if t not in case.allowed and t not in ev.NEUTRAL_TOOLS]
                cap = any(CAP_ERR in e for e in r.errors)
                # công ty thận trọng ⇒ phải là THẺ; Toàn quyền / giữ nguyên ⇒ lệnh rủi ro thấp chạy thẳng thành công cũng đúng (0143)
                ran = a.autonomy != "off" and case.proposal in r.tools and case.proposal not in r.errors
                hit = bool(set(r.tools) & case.must) and (not case.proposal or case.proposal in r.proposals or ran)
                ok = hit and not extra and not cap
                oks += ok
                print(f"  {case.key}{i}: {'ĐẠT' if ok else 'TRƯỢT'} · {len(r.tools)} bước {r.tools}" + (f" · đề xuất {r.proposals}" if r.proposals else "")
                      + (f" · LẠC ĐỀ {extra}" if extra else "") + (" · CHẠM TRẦN" if cap else "") + f" · {r.seconds:.0f}s")
                if not ok:
                    print("     ⇒ " + re.sub(r"\s+", " ", r.text)[:200])
            total_ok, total = total_ok + oks, total + len(runs)
            print(f"[{case.key}] {oks}/{len(runs)} đạt · bước trung vị {statistics.median(len(r.tools) for r in runs)} · "
                  f"token vào TB/lượt {int(use[0]):,} (cache {int(use[1]):,}) · gọi model TB {use[2]} · {statistics.median(r.seconds for r in runs):.0f}s — {case.ask}")
    print(f"TỔNG {a.label}: {total_ok}/{total} đạt")


if __name__ == "__main__":
    asyncio.run(main())
