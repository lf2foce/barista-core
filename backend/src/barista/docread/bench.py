"""BENCHMARK bộ đọc tài liệu (ADR 0232): chạy bộ tài liệu GIẢ có đáp án (``fixtures/``) qua N cấu hình (chuỗi bộ đọc × model) và in bảng so sánh.

    python -m barista.docread.bench --fake                                   # chạy thử khung đo, model giả đọc đúng đáp án (không tốn tiền)
    python -m barista.docread.bench --models google:gemini-3.8-flash \\
        --key-file ../../phenau_v3/backend/.env --key-var PHENAU_GOOGLE_API_KEY   # chạy thật (tốn token — nhớ giới hạn --fixtures / --configs)
    python -m barista.docread.bench --configs free,auto --models a:b,c:d --out /tmp/docread-bench
    python -m barista.docread.bench --retrieval --fake                       # đo TRUY HỒI của cấu hình nhúng (recall@5/@10, MRR) — xem ``bench_retrieval``

Cấu hình: ``free`` (pypdf trần, không xử lý bảng) · ``free+tables`` (pypdf + bảng bằng code) · ``auto@<model>`` (chuỗi mặc định: chữ miễn phí, model chỉ đọc trang scan / bảng
lệch cột) · ``auto+caption@<model>`` (như trên, thêm mô tả ảnh) · ``model-all@<model>`` (model đọc MỌI trang PDF — mốc để so chi phí). Chỉ số: sai chữ theo từ (WER) và ký tự (CER) so với đáp án,
độ chính xác ô bảng, gán đúng trang, tỷ lệ chứa đủ số / chuỗi bắt buộc (``facts``), mô tả ảnh chạm từ khoá, token, chi phí USD / VND, giây / trang, số trang gửi model. Số đo thật nằm ở cột
``usd``; model chưa có trong bảng giá ⇒ ``—`` (không đoán). Khoá không bao giờ được in.
"""

import argparse
import asyncio
import json
import logging
import re
import sys
import time
import unicodedata
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from barista.docread import core
from barista.docread.policy import Policy
from barista.docread.types import Doc
from barista.kernel.text import fold as fold_text

FIX = Path(__file__).parent / "fixtures"
VND_PER_USD = 25_500   # tỷ giá GIẢ ĐỊNH để đổi ra VND — đổi bằng --vnd-rate


# ── đáp án: mỗi trang là danh sách khối (câu chữ | {"table": {header, rows}}) theo thứ tự đọc ──

def page_text(blocks: list[Any]) -> str:
    return " ".join(b if isinstance(b, str) else " ".join([*b["table"]["header"], *(c for r in b["table"]["rows"] for c in r)]) for b in blocks)


def truth_text(t: dict[str, Any]) -> str:
    return " ".join(page_text(p) for p in t["pages"])


def truth_tables(t: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"page": n, **b["table"]} for n, p in enumerate(t["pages"], 1) for b in p if not isinstance(b, str)]


# ── chỉ số (thuần hàm) ──

def norm_words(text: str) -> list[str]:
    """Từ của một đoạn chữ, bỏ cú pháp markdown / mốc trang (không phải nội dung)."""
    t = unicodedata.normalize("NFC", text)
    t = re.sub(r"(?m)^#{1,4}\s*Trang\s+\d+\s*$", " ", t)
    t = re.sub(r"(?m)^\|?\s*:?-{2,}[\s|:-]*$", " ", t)
    t = re.sub(r"[|#*]", " ", t)
    return t.split()


def edit_distance(a: list[Any] | str, b: list[Any] | str) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def error_rates(out: str, truth: str) -> tuple[float, float]:
    """(WER, CER) của ``out`` so với ``truth`` — 0 là đúng hoàn toàn; có thể > 1 khi thừa nhiều."""
    tw, ow = norm_words(truth), norm_words(out)
    if not tw:
        return (0.0 if not ow else 1.0), (0.0 if not ow else 1.0)
    tc, oc = " ".join(tw), " ".join(ow)
    return edit_distance(ow, tw) / len(tw), edit_distance(oc, tc) / max(len(tc), 1)


def _cell(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(s))).strip().casefold()


def table_score(truth: dict[str, Any], doc: Doc) -> float:
    """Tỷ lệ ô ĐÚNG (so khớp từng ô, theo vị trí dòng / cột) của bảng đáp án so với bảng ra sau nhất (cùng trang, tiêu đề giống nhất)."""
    want = [truth["header"], *truth["rows"]]
    cands = [t for t in doc.tables if t.page == truth["page"]] or list(doc.tables)
    if not cands:
        return 0.0
    best = 0.0
    for t in cands:
        got = t.cells()
        hit = sum(1 for i, row in enumerate(want) for j, c in enumerate(row) if i < len(got) and j < len(got[i]) and _cell(got[i][j]) == _cell(c))
        best = max(best, hit / sum(len(r) for r in want))
    return best


def attribution(truth: dict[str, Any], doc: Doc) -> float | None:
    """Mỗi trang đáp án có từ khoá riêng: ra đúng trang (có ở trang mình, không ở trang khác) ⇒ 1. None nếu tài liệu không có mốc."""
    marks = truth.get("markers") or []
    if not marks:
        return None
    by_n = {p.n: _cell(p.text) for p in doc.pages}
    ok = 0
    for i, ms in enumerate(marks, 1):
        mine = by_n.get(i, "")
        others = " ".join(t for n, t in by_n.items() if n != i)
        if all(_cell(m) in mine and _cell(m) not in others for m in ms):
            ok += 1
    return ok / len(marks)


def facts_recall(truth: dict[str, Any], doc: Doc) -> float | None:
    facts = truth.get("facts") or []
    if not facts:
        return None
    blob = _cell(doc.text() + " " + " ".join(i.text + " " + i.caption for i in doc.images))
    return sum(1 for f in facts if _cell(f) in blob) / len(facts)


def caption_hit(truth: dict[str, Any], doc: Doc) -> float | None:
    keys = truth.get("caption_any")
    if not keys:
        return None
    blob = fold_text(" ".join(i.caption + " " + i.text for i in doc.images) + " " + doc.text())
    return 1.0 if any(fold_text(k) in blob for k in keys) else 0.0


# ── cấu hình ──

def configs(models: list[str], names: list[str] | None = None) -> list[tuple[str, Policy]]:
    base = Policy(purpose="bench", max_model_pages=100)
    out: list[tuple[str, Policy]] = [
        ("free", Policy(**{**base.__dict__, "chain": ("office", "pypdf"), "tables": "off"})),
        ("free+tables", Policy(**{**base.__dict__, "chain": ("office", "pypdf"), "tables": "text"})),
    ]
    for m in models or [""]:
        tag = f"@{m}" if m else "@(mặc định)"
        out += [
            (f"auto{tag}", Policy(**{**base.__dict__, "model": m, "chain": ("office", "pypdf", "model"), "tables": "auto"})),
            (f"auto+caption{tag}", Policy(**{**base.__dict__, "model": m, "chain": ("office", "pypdf", "model"), "tables": "auto", "images_caption": True})),
            (f"model-all{tag}", Policy(**{**base.__dict__, "model": m, "chain": ("office", "model"), "tables": "model"})),
        ]
    if names:
        out = [(n, p) for n, p in out if any(n == k or n.startswith(k + "@") for k in names)]
    return out


# ── model giả: đọc ĐÚNG đáp án (chỉ để thử khung đo, không phải số thật) ──

CURRENT: ContextVar[str] = ContextVar("docread_bench_fixture", default="")


def render_blocks(blocks: list[Any]) -> str:
    from barista.docread.types import Table

    return "\n".join(b if isinstance(b, str) else Table(b["table"]["header"], b["table"]["rows"]).markdown() for b in blocks)


def fake_model(truth: dict[str, Any]) -> Any:
    from pydantic_ai import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    def reply(messages: list[Any], info: AgentInfo) -> Any:
        t = truth[CURRENT.get()]
        prompt = repr(messages[-1])
        if info.output_tools:   # ảnh: ImageRead
            text = render_blocks(t["pages"][0])
            cap = "Phối cảnh khu căn hộ có hồ bơi và ba toà nhà" if t.get("caption_any") else ""
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"text": text, "caption": cap, "title": "Ảnh"})])
        m = re.search(r"các trang gốc số ([\d, ]+)\.", prompt)
        pages = [int(x) for x in m.group(1).replace(" ", "").split(",") if x] if m else [1]
        out = []
        for n in pages:
            blocks = t["pages"][n - 1] if n - 1 < len(t["pages"]) else []
            out.append(f"## Trang {n}\n" + render_blocks(blocks))
        return ModelResponse(parts=[TextPart("\n".join(out))])

    return FunctionModel(reply, model_name="bench-fake")


# ── chạy ──

@dataclass
class Cell:
    config: str
    fixture: str
    wer: float | None = None
    cer: float | None = None
    tables: float | None = None
    pages_ok: float | None = None
    facts: float | None = None
    caption: float | None = None
    tokens: int = 0
    usd: Decimal | None = Decimal(0)
    seconds: float = 0.0
    sec_per_page: float = 0.0
    model_pages: int = 0
    methods: str = ""
    notices: list[str] = field(default_factory=list)


async def run_one(name: str, pol: Policy, fixture: str, all_truth: dict[str, Any], model: Any) -> Cell:
    from barista.kernel.principal import system_principal

    truth = all_truth[fixture]
    data = (FIX / fixture).read_bytes()
    tok = CURRENT.set(fixture)
    t0 = time.perf_counter()
    try:
        doc = await core.read(system_principal(UUID(int=0)), fixture, data, purpose="bench", policy=pol, model=model, cache=False, meter=False)
    except Exception as e:   # một cấu hình hỏng không làm mất cả bảng — ghi thành thông báo
        return Cell(name, fixture, notices=[f"LỖI {type(e).__name__}: {e}"])
    finally:
        CURRENT.reset(tok)
    wall = time.perf_counter() - t0
    c = Cell(name, fixture, tokens=doc.usage.tokens, usd=doc.usage.cost_usd, seconds=wall, model_pages=doc.usage.model_pages,
             sec_per_page=wall / max(len(doc.pages), 1), notices=[n.code for n in doc.notices])
    c.methods = ",".join(sorted({p.method or "—" for p in doc.pages}))
    if truth_text(truth).strip():
        c.wer, c.cer = error_rates(doc.text(), truth_text(truth))
    if tt := truth_tables(truth):
        c.tables = sum(table_score(t, doc) for t in tt) / len(tt)
    c.pages_ok = attribution(truth, doc)
    c.facts = facts_recall(truth, doc)
    c.caption = caption_hit(truth, doc)
    return c


def avg(xs: list[float | None]) -> float | None:
    v = [x for x in xs if x is not None]
    return sum(v) / len(v) if v else None


def summarise(cells: list[Cell], truth: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for name in dict.fromkeys(c.config for c in cells):
        mine = [c for c in cells if c.config == name]
        usd = Decimal(0)
        unknown = False
        for c in mine:
            if c.usd is None:
                unknown = c.model_pages > 0 or unknown
            else:
                usd += c.usd
        imgs = [c for c in mine if truth[c.fixture]["kind"] == "image"]
        img_usd = None if any(c.usd is None and c.model_pages for c in imgs) else sum((c.usd or Decimal(0)) for c in imgs)
        rows.append({"config": name, "wer": avg([c.wer for c in mine]), "cer": avg([c.cer for c in mine]), "tables": avg([c.tables for c in mine]),
                     "pages_ok": avg([c.pages_ok for c in mine]), "facts": avg([c.facts for c in mine]), "caption": avg([c.caption for c in mine]),
                     "tokens": sum(c.tokens for c in mine), "usd": None if unknown else usd, "model_pages": sum(c.model_pages for c in mine),
                     "sec_per_page": avg([c.sec_per_page for c in mine]) or 0.0, "seconds": sum(c.seconds for c in mine),
                     "usd_per_image": (None if img_usd is None or not imgs else img_usd / len(imgs)), "errors": [n for c in mine for n in c.notices if n.startswith("LỖI")],
                     "notices": sorted({n for c in mine for n in c.notices})})
    return rows


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def money(x: Decimal | None, rate: int, digits: int = 4) -> str:
    return "—" if x is None else f"{float(x):.{digits}f} (≈{float(x) * rate:,.0f}đ)".replace(",", ".")


def render(rows: list[dict[str, Any]], rate: int) -> str:
    head = ["cấu hình", "sai từ", "sai ký tự", "ô bảng", "đúng trang", "đủ số", "mô tả ảnh", "token", "USD (≈VND)", "USD / ảnh", "trang→model", "giây/trang", "thông báo"]
    body = [[r["config"], pct(r["wer"]), pct(r["cer"]), pct(r["tables"]), pct(r["pages_ok"]), pct(r["facts"]), pct(r["caption"]), f"{r['tokens']:,}".replace(",", "."),
             money(r["usd"], rate), money(r["usd_per_image"], rate, 5), str(r["model_pages"]), f"{r['sec_per_page']:.2f}", ",".join(r["notices"]) or "—"] for r in rows]
    w = [max(len(x[i]) for x in [head, *body]) for i in range(len(head))]
    line = lambda cells: "| " + " | ".join(c.ljust(w[i]) for i, c in enumerate(cells)) + " |"  # noqa: E731
    return "\n".join([line(head), "|" + "|".join("-" * (x + 2) for x in w) + "|", *(line(b) for b in body)])


def render_detail(cells: list[Cell]) -> str:
    out = ["", "Chi tiết theo (cấu hình × tài liệu):"]
    for c in cells:
        out.append(f"  {c.config:<34} {c.fixture:<16} sai từ {pct(c.wer):>7} ô bảng {pct(c.tables):>7} trang {pct(c.pages_ok):>6} số {pct(c.facts):>6} tok {c.tokens:>6} "
                   f"model {c.model_pages}tr {c.seconds:5.1f}s [{c.methods}] {' '.join(c.notices)}")
    return "\n".join(out)


def write_out(out: Path, text: str, rows: list[dict[str, Any]], cells: list[Cell], a: argparse.Namespace) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "bench.txt").write_text(text + "\n", encoding="utf-8")
    (out / "bench.json").write_text(json.dumps({"summary": rows, "cells": [c.__dict__ for c in cells], "vnd_rate": a.vnd_rate, "fake": a.fake}, ensure_ascii=False, indent=1, default=str),
                                    encoding="utf-8")


async def main(a: argparse.Namespace) -> int:
    if a.retrieval:   # chế độ khác: đo truy hồi của cấu hình nhúng (bộ đoạn + câu hỏi), không đọc tài liệu
        from barista.docread import bench_retrieval

        return await bench_retrieval.main(a)
    if not a.verbose:
        logging.getLogger("barista.docread").setLevel(logging.CRITICAL)   # lỗi model đã vào cột "thông báo" — không in cả vết ngăn xếp
        logging.getLogger("barista.agent").setLevel(logging.CRITICAL)
    global FIX
    if a.dir:
        FIX = Path(a.dir)   # bộ tài liệu riêng (file + truth.json cùng định dạng) — vd bộ thật của khách để ở máy, không đưa vào repo
    truth = json.loads((FIX / "truth.json").read_text(encoding="utf-8"))
    fixtures = [f for f in truth if not a.fixtures or f in a.fixtures.split(",")]
    models = [m for m in a.models.split(",") if m]
    cfgs = configs(models, a.configs.split(",") if a.configs else None)
    if a.key_file:
        from barista.agent.llm import read_key
        from barista.config import settings

        settings.llm_api_key = read_key("", a.key_file, a.key_var)   # chỉ trong tiến trình này, không in
        if not settings.llm_api_key:
            print("Không đọc được khoá model từ --key-file / --key-var", file=sys.stderr)
            return 2
    cells: list[Cell] = []
    for name, pol in cfgs:
        for fx in fixtures:
            model = fake_model(truth) if a.fake and pol.uses_model() else None
            cells.append(await run_one(name, pol, fx, truth, model))
            if a.verbose:
                print(f"  {name} × {fx} xong", file=sys.stderr)
    rows = summarise(cells, truth)
    text = (f"Benchmark đọc tài liệu — {len(fixtures)} tài liệu giả × {len(cfgs)} cấu hình"
            f"{' — MODEL GIẢ (đọc đúng đáp án, không có giá thật)' if a.fake else ''}; tỷ giá giả định {a.vnd_rate:,} VND/USD\n".replace(",", ".")
            + render(rows, a.vnd_rate) + (render_detail(cells) if a.detail else ""))
    print(text)
    if a.out:
        await asyncio.to_thread(write_out, Path(a.out), text, rows, cells, a)
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m barista.docread.bench", description="Benchmark bộ đọc tài liệu (ADR 0232)")
    ap.add_argument("--models", default="", help="model OCR cần đo, ngăn bằng dấu phẩy (nhà_cung_cấp:tên); trống = model mặc định của bản cài")
    ap.add_argument("--configs", default="", help="chỉ chạy các cấu hình này: free, free+tables, auto, auto+caption, model-all")
    ap.add_argument("--dir", default="", help="thư mục bộ tài liệu riêng (file + truth.json cùng định dạng với docread/fixtures); trống = bộ giả đi kèm")
    ap.add_argument("--fixtures", default="", help="chỉ chạy các tài liệu này (tên file, ngăn bằng dấu phẩy)")
    ap.add_argument("--fake", action="store_true", help="model giả đọc đúng đáp án — thử khung đo, không tốn tiền")
    ap.add_argument("--key-file", default="", help="file .env chứa khoá model (chỉ đọc biến --key-var, không in)")
    ap.add_argument("--key-var", default="", help="tên biến trong --key-file")
    ap.add_argument("--vnd-rate", type=int, default=VND_PER_USD, help="tỷ giá GIẢ ĐỊNH VND/USD")
    ap.add_argument("--out", default="", help="thư mục ghi bench.txt + bench.json")
    ap.add_argument("--detail", action="store_true", help="in thêm từng (cấu hình × tài liệu)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--retrieval", action="store_true", help="đo TRUY HỒI của cấu hình nhúng thay vì đọc tài liệu (bộ docread/fixtures/retrieval_vi.json hoặc --dir/retrieval.json)")
    ap.add_argument("--embed-configs", default="", help="với --retrieval: model@chiều hoặc openai-compatible:tên@chiều@địa_chỉ, ngăn bằng dấu phẩy "
                    "(vd google:gemini-embedding-001@768,google:gemini-embedding-001@1536)")
    return ap


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parser().parse_args())))
