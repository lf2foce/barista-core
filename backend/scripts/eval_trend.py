"""XU HƯỚNG EVAL từ lịch sử JSON trong repo (``docs/evals/*.json`` — ghi bởi ``eval_*.py --save``; công cụ dev, không phải dữ liệu khách — 0144 mục 7).

    uv run python scripts/eval_trend.py [--dir ../docs/evals] [--set base] [--label gemini-3.8-flash-low] [--last 20]

In: mỗi lần chạy một dòng (ngày, commit, bộ, nhãn, đạt %, vượt quyền, số lệch, giây trung vị, USD / lượt); rồi so lần MỚI NHẤT với lần TRƯỚC cùng
(bộ, nhãn): câu trước đạt mà giờ trượt (hồi quy), câu trước trượt giờ đạt. Không chạy model, không tốn tiền."""

import argparse
import json
import pathlib
import sys
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_DIR = HERE.parents[1] / "docs" / "evals"


def load(dir_: pathlib.Path) -> list[dict[str, Any]]:
    runs = []
    for p in sorted(dir_.glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and "records" in d:
            runs.append({**d, "file": p.name})
    return sorted(runs, key=lambda r: str(r.get("at", "")))


def row(r: dict[str, Any]) -> str:
    s = r.get("stats") or {}
    return (f"{str(r.get('at', ''))[:16]:16} {str(r.get('commit', ''))[:8]:8} {r.get('set', '')!s:9} {str(r.get('label', ''))[:24]:24} "
            f"đạt {s.get('đạt %', 0):5.1f}% · vượt quyền {int(s.get('vượt quyền', 0))} · số lệch {int(s.get('số lệch', 0))} · "
            f"{s.get('giây (trung vị)', 0):4.1f}s · {s.get('USD / lượt', 0):.4f} USD"
            + (f" · bộ đệm {s['đọc bộ đệm %']:.0f}%" if "đọc bộ đệm %" in s else "") + f" · {len(r.get('records') or [])} lượt")


def passed(r: dict[str, Any]) -> set[str]:
    """Câu ĐẠT ở mọi lượt của lần chạy."""
    by: dict[str, list[bool]] = {}
    for x in r.get("records") or []:
        by.setdefault(str(x["case"]), []).append(bool(x["ok"]))
    return {k for k, v in by.items() if all(v)}


def diff(prev: dict[str, Any], cur: dict[str, Any]) -> tuple[list[str], list[str]]:
    a, b = passed(prev), passed(cur)
    return sorted(a - b), sorted(b - a)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(DEFAULT_DIR))
    ap.add_argument("--set", default="")
    ap.add_argument("--label", default="")
    ap.add_argument("--last", type=int, default=20)
    a = ap.parse_args(argv)
    runs = [r for r in load(pathlib.Path(a.dir)) if (not a.set or r.get("set") == a.set) and (not a.label or r.get("label") == a.label)]
    if not runs:
        print(f"chưa có lịch sử eval ở {a.dir} (chạy eval_*.py --save)")
        return 0
    for r in runs[-a.last:]:
        print(row(r))
    cur = runs[-1]
    prev = next((r for r in reversed(runs[:-1]) if r.get("set") == cur.get("set") and r.get("label") == cur.get("label")), None)
    if prev is None:
        print("(chưa có lần trước cùng bộ + nhãn để so)")
        return 0
    worse, better = diff(prev, cur)
    print(f"So với {str(prev.get('at', ''))[:16]} ({prev.get('commit', '')}): hồi quy {len(worse)} câu {worse or ''} · tốt lên {len(better)} câu {better or ''}")
    return 1 if worse else 0


if __name__ == "__main__":
    sys.exit(main())
