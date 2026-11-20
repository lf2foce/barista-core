"""BENCHMARK TRUY HỒI của cấu hình nhúng (ADR 0232, mục "Nhúng theo công ty"): bộ đoạn + câu hỏi tiếng Việt GIẢ có đáp án (``fixtures/retrieval_vi.json``) qua N cấu hình nhúng
(model × số chiều × máy chủ) — nhúng đoạn, nhúng từng câu hỏi, xếp theo cosine, in recall@5 / recall@10 / MRR@10 kèm token, chi phí, độ trễ. Dùng để QUYẾT ĐỊNH đổi model hoặc
số chiều (vd Gemini 768 so với 1536, hoặc EmbeddingGemma tự dựng) và chọn ``min_sim`` trước khi nhà vận hành ``embed-set``.

    python -m barista.docread.bench --retrieval --fake                      # thử khung đo (model băm từ, KHÔNG phải số đo chất lượng thật)
    python -m barista.docread.bench --retrieval --key-file <.env> --key-var <BIẾN KHOÁ> \\
        --embed-configs google:gemini-embedding-001@768,google:gemini-embedding-001@1536
    python -m barista.docread.bench --retrieval \\
        --embed-configs google:gemini-embedding-001@768,openai-compatible:embeddinggemma@768@http://127.0.0.1:11434/v1

Cấu hình: ``model@số_chiều`` hoặc ``openai-compatible:tên@số_chiều@địa_chỉ`` (máy tự dựng: tiền tố + cắt Matryoshka điền theo gợi ý của model quen thuộc — ``embed_profile.PRESETS``).
Một cấu hình không chạy được (máy chủ không có, thiếu khoá) ⇒ ghi lý do trong bảng, không chặn các cấu hình khác. Khoá không bao giờ được in. Bộ thật của khách: ``--dir`` có ``retrieval.json``
cùng định dạng (``passages`` [{id, text}], ``questions`` [{q, expect: [id…]}]).
"""

import argparse
import asyncio
import hashlib
import json
import math
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic_ai.embeddings import EmbeddingModel, EmbeddingResult, EmbeddingSettings
from pydantic_ai.embeddings.result import EmbedInputType
from pydantic_ai.usage import RequestUsage

from barista.agent import embed, embed_profile
from barista.agent.embed_profile import Profile
from barista.kernel.text import fold

FIXTURES = Path(__file__).parent / "fixtures"
VND_PER_USD = 25_500
KS = (5, 10)


@dataclass
class Result:
    name: str
    dims: int
    ok: bool = True
    note: str = ""
    recall: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    tokens: int = 0
    usd: Decimal | None = None
    corpus_s: float = 0.0
    query_ms: float = 0.0
    misses: list[str] = field(default_factory=list)


def load(dir_: str = "") -> dict[str, Any]:
    path = (Path(dir_) / "retrieval.json") if dir_ else FIXTURES / "retrieval_vi.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = {p["id"] for p in data["passages"]}
    for q in data["questions"]:
        if not q["expect"] or not set(q["expect"]) <= ids:
            raise SystemExit(f"Câu hỏi “{q['q']}” trỏ tới đoạn không có trong bộ")
    return data


def parse_config(spec: str) -> Profile:
    """``model@dims`` | ``openai-compatible:tên@dims@địa_chỉ`` ⇒ hồ sơ (gợi ý tiền tố / cắt của model quen thuộc cho máy tự dựng)."""
    parts = spec.split("@", 2)
    model = parts[0]
    dims = int(parts[1]) if len(parts) > 1 and parts[1] else embed_profile.STANDARD_DIMS
    base = parts[2] if len(parts) > 2 else ""
    pre = embed_profile.preset(model)
    compat = model.startswith(embed_profile.COMPAT + ":")
    return Profile(model=model, dims=dims, base_url=base, truncate=bool(compat and pre.get("truncate")),
                   query_prefix=pre.get("query_prefix", "") if compat else "", document_prefix=pre.get("document_prefix", "") if compat else "", source="bench")


# ── chỉ số (thuần hàm) ──

def rank_of(order: Sequence[int], expect: set[int]) -> int | None:
    """Hạng (từ 1) của đoạn đúng đầu tiên, hoặc None."""
    for i, idx in enumerate(order, 1):
        if idx in expect:
            return i
    return None


def score(ranks: Sequence[int | None]) -> tuple[dict[int, float], float]:
    n = len(ranks) or 1
    rec = {k: sum(1 for r in ranks if r is not None and r <= k) / n for k in KS}
    mrr = sum(1.0 / r for r in ranks if r is not None and r <= 10) / n
    return rec, mrr


def order_by_cosine(q: Sequence[float], docs: Sequence[Sequence[float]]) -> list[int]:
    sims = [sum(a * b for a, b in zip(q, d, strict=True)) for d in docs]
    return sorted(range(len(docs)), key=lambda i: -sims[i])


# ── model băm từ: CHỈ để thử khung đo offline (so khớp chữ, không hiểu nghĩa) ──

class HashEmbedder(EmbeddingModel):
    def __init__(self, dims: int) -> None:
        super().__init__()
        self.dims = dims

    @property
    def model_name(self) -> str:
        return "hash-bow"

    @property
    def system(self) -> str:
        return "fake"

    async def embed(self, inputs: str | Sequence[str], *, input_type: EmbedInputType, settings: EmbeddingSettings | None = None) -> EmbeddingResult:
        inputs, settings = self.prepare_embed(inputs, settings)
        dims = (settings or {}).get("dimensions") or self.dims
        out = []
        for t in inputs:
            v = [0.0] * dims
            for w in fold(t).split():
                w = "".join(ch for ch in w if ch.isalnum())
                if w:
                    v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dims] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return EmbeddingResult(embeddings=out, inputs=inputs, input_type=input_type, usage=RequestUsage(input_tokens=sum(len(t) // 3 + 1 for t in inputs)),
                               model_name="hash-bow", provider_name="fake")


async def run_config(prof: Profile, data: dict[str, Any]) -> Result:
    res = Result(prof.model + (f" @ {prof.base_url}" if prof.base_url else ""), prof.dims)
    passages, questions = data["passages"], data["questions"]
    index = {p["id"]: i for i, p in enumerate(passages)}
    try:
        t0 = time.monotonic()
        docs = await embed.embed_texts(prof, [p["text"] for p in passages], kind="document")
        res.corpus_s = time.monotonic() - t0
        ranks: list[int | None] = []
        qsum = 0.0
        usd: Decimal | None = docs.cost_usd
        tokens = docs.tokens
        for q in questions:
            t1 = time.monotonic()
            qv = await embed.embed_texts(prof, [q["q"]], kind="query")
            qsum += time.monotonic() - t1
            tokens += qv.tokens
            usd = None if usd is None or qv.cost_usd is None else usd + qv.cost_usd
            order = order_by_cosine(qv.vectors[0], docs.vectors)
            rank = rank_of(order, {index[e] for e in q["expect"]})
            ranks.append(rank)
            if rank is None or rank > 5:
                res.misses.append(f"{q['q']} → hạng {rank or '>' + str(len(passages))}")
        res.recall, res.mrr = score(ranks)
        res.tokens, res.usd, res.query_ms = tokens, usd, qsum * 1000 / max(1, len(questions))
    except Exception as e:   # một cấu hình hỏng không chặn các cấu hình khác — ghi lý do (không có khoá trong thông điệp: lỗi nhà cung cấp không chứa khoá)
        res.ok, res.note = False, f"{type(e).__name__}: {str(e)[:200]}"
    return res


def money(x: Decimal | None, rate: int) -> str:
    return "—" if x is None else f"{float(x):.5f} USD (≈ {float(x) * rate:,.1f} đ)".replace(",", ".")


def render(results: list[Result], data: dict[str, Any], rate: int, fake: bool) -> str:
    head = (f"Benchmark truy hồi — {len(data['passages'])} đoạn × {len(data['questions'])} câu hỏi tiếng Việt GIẢ"
            + (" — MODEL BĂM TỪ (chỉ thử khung đo, KHÔNG phải chất lượng thật)" if fake else "") + f"; tỷ giá giả định {rate:,} VND/USD".replace(",", "."))
    lines = [head, f"{'cấu hình':<58} {'chiều':>5} {'recall@5':>9} {'recall@10':>10} {'MRR@10':>7} {'token':>7} {'chi phí':>30} {'nhúng bộ (s)':>13} {'ms / câu hỏi':>13}"]
    for r in results:
        if not r.ok:
            lines.append(f"{r.name:<58} {r.dims:>5}  KHÔNG CHẠY ĐƯỢC — {r.note}")
            continue
        lines.append(f"{r.name:<58} {r.dims:>5} {r.recall[5]:>9.3f} {r.recall[10]:>10.3f} {r.mrr:>7.3f} {r.tokens:>7} {money(r.usd, rate):>30} {r.corpus_s:>13.2f} {r.query_ms:>13.0f}")
    return "\n".join(lines)


def render_misses(results: list[Result]) -> str:
    out = []
    for r in results:
        if r.ok and r.misses:
            out.append(f"\n{r.name} @{r.dims} — câu ngoài top 5:\n  " + "\n  ".join(r.misses))
    return "\n".join(out)


def _write(out: Path, text: str, results: list[Result]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "bench-retrieval.txt").write_text(text + "\n", encoding="utf-8")
    (out / "bench-retrieval.json").write_text(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=1, default=str), encoding="utf-8")


async def main(a: argparse.Namespace) -> int:
    data = load(a.dir)
    if a.key_file:
        from barista.agent.llm import read_key
        from barista.config import settings

        settings.llm_api_key = read_key("", a.key_file, a.key_var)   # chỉ trong tiến trình này, không in
        if not settings.llm_api_key:
            print("Không đọc được khoá model từ --key-file / --key-var", file=sys.stderr)
            return 2
    specs = [s for s in a.embed_configs.split(",") if s]
    if a.fake:
        specs = specs or ["fake:hash-bow@768", "fake:hash-bow@1536"]
        embed.use_model(lambda prof: HashEmbedder(prof.dims))
    if not specs:
        print("Cần --embed-configs (hoặc --fake)", file=sys.stderr)
        return 2
    results = [await run_config(parse_config(s), data) for s in specs]
    text = render(results, data, a.vnd_rate, a.fake) + (render_misses(results) if a.detail else "")
    print(text)
    if a.out:
        await asyncio.to_thread(_write, Path(a.out), text, results)
    return 0 if all(r.ok for r in results) else 1
