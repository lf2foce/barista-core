"""Báo cáo HTML từ các lần chạy ``eval_assistant.py``: mỗi câu hỏi, các cấu hình cạnh nhau, mỗi lượt = các bước gọi công cụ (tên, lọc gì, kết quả /
lỗi) + câu trả lời cuối + đạt / trượt, giây, token. Lịch sử đọc từ ``core.chat_threads`` của DB từng cấu hình; đạt / trượt từ file ``--json``.

    uv run python scripts/eval_report.py --out /tmp/bao-cao.html \\
        "DeepSeek low · đủ=barista_erp_g_low_full:/tmp/eval_G_low_full.json" \\
        "DeepSeek low · nhóm=barista_erp_g_low_grp:/tmp/eval_G_low_grp.json"
"""

import argparse
import html
import importlib.util
import json
import pathlib
import subprocess
import sys
from typing import Any

HERE = pathlib.Path(__file__).parent


def _cases() -> dict[str, tuple[str, str, bool]]:
    spec = importlib.util.spec_from_file_location("ev", HERE / "eval_assistant.py")
    assert spec and spec.loader
    ev = importlib.util.module_from_spec(spec)
    sys.modules["ev"] = ev
    spec.loader.exec_module(ev)
    hard = {c.key for c in ev.HARD}
    return {c.key: (c.role, c.ask, c.key in hard) for c in [*ev.CASES, *ev.HARD]}


def _threads(db: str) -> list[dict[str, Any]]:
    sql = """SELECT coalesce(json_agg(json_build_object('email', u.email, 'turns', t.turns) ORDER BY t.created_at), '[]')
             FROM core.chat_threads t JOIN core.users u ON u.id = t.user_id"""
    out = subprocess.run(["psql", "-h", "localhost", "-d", db, "-tAc", sql], capture_output=True, text=True, check=True).stdout
    return json.loads(out or "[]")


def _runs(db: str, records: list[dict[str, Any]], cases: dict[str, tuple[str, str, bool]]) -> dict[str, list[dict[str, Any]]]:
    """case → các lượt (bước, câu trả lời, đạt…). Khớp lượt trong DB với bản ghi đạt / trượt bằng câu hỏi + đúng dãy công cụ."""
    by_ask = {(f"{role}@sanmau.demo", ask): key for key, (role, ask, _) in cases.items()}
    pending = [dict(r) for r in records]
    out: dict[str, list[dict[str, Any]]] = {}
    for t in _threads(db):
        turns = t["turns"] or []
        if len(turns) < 2:
            continue
        key = by_ask.get((t["email"], turns[0].get("text", "")))
        if key is None:
            continue
        a = turns[1]
        steps = a.get("steps") or []
        tools = [s.get("tool") for s in steps]
        # công cụ chạy song song trong một bước được lưu theo thứ tự XONG, bản ghi eval theo số bước ⇒ khớp theo tập (không theo thứ tự)
        rec = next((r for r in pending if r["case"] == key and sorted(r["tools"]) == sorted(tools)), None)
        if rec is not None:
            pending.remove(rec)
        out.setdefault(key, []).append({"steps": steps, "text": a.get("text", ""), "rec": rec})
    return out


def _step(s: dict[str, Any]) -> str:
    st = s.get("status") or ""
    bad = st not in ("ok", "proposed", "pending_approval", "suggested", "running", "dismissed")
    note = f" — {html.escape(str(s.get('error') or st))}" if bad else (" — đề xuất" if st == "proposed" else "")
    summ = f" <span class=m>{html.escape(str(s.get('summary') or ''))}</span>" if s.get("summary") else ""
    return (f"<li class={'bad' if bad else 'ok'}><code>{html.escape(str(s.get('tool')))}</code> {html.escape(str(s.get('title') or ''))}"
            f"{summ}{note}</li>")


def render(configs: list[tuple[str, dict[str, list[dict[str, Any]]]]], cases: dict[str, tuple[str, str, bool]]) -> str:
    rows = []
    for key, (role, ask, hard) in cases.items():
        if not any(key in runs for _, runs in configs):
            continue
        cells = []
        for _, runs in configs:
            parts = []
            for i, r in enumerate(runs.get(key, []), 1):
                rec = r["rec"] or {}
                ok = rec.get("ok")
                badge = "<b class=pass>ĐẠT</b>" if ok else ("<b class=fail>TRƯỢT</b>" if ok is False else "<b>?</b>")
                meta = f"{rec.get('seconds', '?')} giây · {int(rec.get('tokens', 0)) // 1000}k token" if rec else ""
                parts.append(f"<div class=run><div class=hd>Lần {i} {badge} <span class=m>{len(r['steps'])} bước · {meta}</span></div>"
                             f"<ol>{''.join(_step(s) for s in r['steps'])}</ol>"
                             f"<details><summary>Câu trả lời</summary><p>{html.escape(r['text'])}</p></details></div>")
            cells.append(f"<td>{''.join(parts) or '<span class=m>—</span>'}</td>")
        rows.append(f"<tr><th><span class=k>{key}</span>{' <span class=hard>khó</span>' if hard else ''}<br><span class=m>{role}</span>"
                    f"<p>{html.escape(ask)}</p></th>{''.join(cells)}</tr>")
    head = "".join(f"<th>{html.escape(name)}</th>" for name, _ in configs)
    return f"""<!doctype html><html lang=vi><meta charset=utf-8><title>Lịch sử gọi công cụ — eval trợ lý</title>
<style>
:root{{color-scheme:light dark;--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--line:#d2d2d7;--ok:#1a7f37;--bad:#c9372c;--card:#f5f5f7}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1c1e;--fg:#f5f5f7;--mut:#a1a1a6;--line:#3a3a3c;--ok:#30d158;--bad:#ff6961;--card:#2c2c2e}}}}
body{{font:14px/1.45 -apple-system,system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:16px}}
table{{border-collapse:collapse;width:100%;table-layout:fixed}} th,td{{border:1px solid var(--line);padding:8px;vertical-align:top;text-align:left}}
thead th{{position:sticky;top:0;background:var(--bg);z-index:1}} tbody th{{width:220px;font-weight:400}}
.k{{font-weight:600}} .m{{color:var(--mut);font-size:12px}} .hard{{font-size:12px;color:var(--bad)}}
.run{{background:var(--card);border-radius:8px;padding:6px 8px;margin-bottom:6px}} .hd{{font-size:12px}}
ol{{margin:4px 0 2px 18px;padding:0}} li{{margin:1px 0;font-size:12px}} li.bad{{color:var(--bad)}} code{{font-size:12px}}
.pass{{color:var(--ok)}} .fail{{color:var(--bad)}} details p{{white-space:pre-wrap;font-size:12px;margin:4px 0}}
</style><h1>Lịch sử gọi công cụ — mỗi câu, mỗi cấu hình, 3 lần</h1>
<table><thead><tr><th>Câu hỏi</th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("config", nargs="+", help='"Tên=db:file.json"')
    a = ap.parse_args()
    cases = _cases()
    configs = []
    for spec in a.config:
        name, rest = spec.split("=", 1)
        db, path = rest.split(":", 1)
        configs.append((name, _runs(db, json.loads(pathlib.Path(path).read_text()), cases)))
    pathlib.Path(a.out).write_text(render(configs, cases), encoding="utf-8")
    print(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
