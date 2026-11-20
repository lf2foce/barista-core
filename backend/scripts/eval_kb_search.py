"""Đo tìm trong kho tài liệu: chỉ từ khoá vs lai (từ khoá + nghĩa, RRF) — ADR 0141, bộ câu hỏi ``tests/fixtures/kb_relevance_vi.json``.

Chạy trên DB RIÊNG đã nạp mẫu ``--profile san-moi-gioi-demo`` (một công ty), với khoá model thật:
  BARISTA_DATABASE_URL=…/barista_erp_kbq BARISTA_OWNER_DATABASE_URL=…/barista_erp_kbq BARISTA_FILES_DIR=… \
  BARISTA_LLM_API_KEY_FILE=… BARISTA_LLM_API_KEY_VAR=… uv run python scripts/eval_kb_search.py [--sims 0.55,0.6,0.65,0.7]

Thêm ``extra_docs`` nếu chưa có, nhúng mọi đoạn còn thiếu (đo chi phí), rồi với mỗi câu: hạng của đoạn đúng đầu tiên trong 10 đoạn đầu
(``kb.search`` bằng quyền giám đốc). In recall@5, MRR@10 cho từ khoá và cho lai ở từng ngưỡng độ giống, chi phí theo ``core.ai_usage``.
"""

import argparse
import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from barista.config import settings
from barista.db import db
from barista.kernel import executor
from barista.modules import kb
from barista.modules.kb import vectors
from barista.seed import DOMAIN, who

SET = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "kb_relevance_vi.json"


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", kb.fold(s)).strip()


async def _owner(sql: Any, params: Any = None) -> list[dict[str, Any]]:
    async with db.owner.connection() as c:
        cur = await c.execute(sql, params)
        return await cur.fetchall()


async def run_set(p: Any, queries: list[dict[str, Any]]) -> tuple[list[int | None], list[str]]:
    ranks: list[int | None] = []
    modes: list[str] = []
    for item in queries:
        r = await executor.query(p, "kb.search", {"q": item["q"], "limit": 10})
        assert r.ok, (item["q"], r.error)
        ids = [int(str(row["id"]).split(":")[1]) for row in r.output["rows"]]
        folded = {x["id"]: norm(x["folded"]) for x in await _owner("SELECT id, folded FROM kb.chunks WHERE id = ANY(%s)", (ids,))}
        want = [norm(e) for e in item["expect"]]
        rank = next((i for i, cid in enumerate(ids, 1) if any(w in folded.get(cid, "") for w in want)), None)
        ranks.append(rank)
        modes.append(r.output.get("search_mode", "?"))
    return ranks, modes


def metrics(ranks: list[int | None]) -> tuple[float, float]:
    n = len(ranks)
    return sum(1 for r in ranks if r and r <= 5) / n, sum(1 / r for r in ranks if r) / n


async def main(sims: list[float]) -> None:
    data = json.loads(SET.read_text())
    await db.open(settings.database_url, settings.owner_database_url)
    started = datetime.now(UTC)
    model = settings.embed_model
    try:
        tid = (await _owner("SELECT id FROM core.tenants ORDER BY created_at LIMIT 1"))[0]["id"]
        gd = await who(tid, "giamdoc", DOMAIN)
        have = {r["title"] for r in await _owner("SELECT title FROM kb.documents WHERE tenant_id = %s", (tid,))}
        for d in data["extra_docs"]:
            if d["title"] not in have:
                assert (await executor.run(gd, "kb.add_document", d)).ok
        await vectors.drain()
        filled = await vectors.embed_pending(tid)
        st = await vectors.status(tid)
        print(f"model {model} · {st[0]['backend']} · {st[0]['embedded']}/{st[0]['chunks']} đoạn có vector · lượt này {filled}")

        settings.embed_model = ""                       # nhánh nghĩa tắt ⇒ đúng đường "chỉ từ khoá" của kb.search
        kw, _ = await run_set(gd, data["queries"])
        settings.embed_model = model
        rows = [("từ khoá", kw)]
        for s in sims:
            settings.kb_vector_min_sim = s
            hy, modes = await run_set(gd, data["queries"])
            assert set(modes) == {"hybrid"}, modes
            rows.append((f"lai (ngưỡng {s})", hy))
        n = len(data["queries"])
        print(f"\n{n} câu hỏi · recall@5 · MRR@10")
        for name, ranks in rows:
            r5, mrr = metrics(ranks)
            print(f"  {name:<18} recall@5 {r5:.2f} ({sum(1 for r in ranks if r and r <= 5)}/{n}) · MRR {mrr:.3f}")
        print("\nhạng đoạn đúng (từ khoá → lai ở ngưỡng đầu):")
        for item, a, b in zip(data["queries"], kw, rows[1][1], strict=True):
            print(f"  {a or '-':>2} → {b or '-':>2}  {item['q']}")
        cost = await _owner("""SELECT category, count(*) AS calls, sum(input_tokens) AS tokens, sum(cost_usd) AS usd FROM core.ai_usage
                               WHERE tenant_id = %s AND at >= %s AND category LIKE 'kb:embed%%' GROUP BY category""", (tid, started))
        print("\nchi phí lượt này (token ƯỚC TÍNH — Gemini API không trả số token embedding):")
        for c in cost:
            print(f"  {c['category']}: {c['calls']} lượt · {c['tokens']} token · {c['usd']} USD")
    finally:
        await vectors.drain()
        await db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", default="0.55,0.6,0.65,0.7")
    a = ap.parse_args()
    asyncio.run(main([float(x) for x in a.sims.split(",")]))
