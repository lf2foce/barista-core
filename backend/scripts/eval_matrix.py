"""Bàn đo cố định (quyết định 0095): chạy SONG SONG nhiều cấu hình trợ lý trên cùng bộ đề, mỗi cấu hình một server + một DB riêng (cấu hình là
của cả tiến trình; số token đọc theo người + thời gian), rồi in bảng so + dựng báo cáo HTML lịch sử gọi công cụ + dọn dẹp.

    uv run python scripts/eval_matrix.py --set all --runs 3 --out /tmp/eval-0511 \\
        "đủ:model=deepseek:deepseek-flash,thinking=medium,tools=all,key=/tmp/ds.env:DEEPSEEK_KEY" \\
        "tìm 15:model=deepseek:deepseek-flash,thinking=medium,tools=retrieve,k=15,key=/tmp/ds.env:DEEPSEEK_KEY" \\
        "đủ +100 nhiễu:model=deepseek:deepseek-flash,thinking=medium,tools=all,noise=100,key=/tmp/ds.env:DEEPSEEK_KEY"

Khoá: ``key=<file .env>:<tên biến>`` (không truyền khoá trên dòng lệnh). Mọi DB tạm tên ``barista_erp_mx_<số>`` — xoá khi xong (``--keep`` giữ lại).
"""

import argparse
import asyncio
import json
import os
import pathlib
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field

HERE = pathlib.Path(__file__).parent
BACKEND = HERE.parent


@dataclass
class Config:
    name: str
    opts: dict[str, str]
    port: int
    db: str
    out: pathlib.Path
    env: dict[str, str] = field(default_factory=dict)


def _parse(spec: str, i: int, out: pathlib.Path) -> Config:
    name, rest = spec.split(":", 1)
    opts: dict[str, str] = {}
    for part in rest.split(","):
        k, v = part.split("=", 1)
        opts[k.strip()] = v.strip()
    c = Config(name, opts, 8400 + i, f"barista_erp_mx_{i}", out)
    db = f"postgresql://barista_app:barista_app@localhost:5432/{c.db}"
    owner = f"postgresql://barista_owner:barista_owner@localhost:5432/{c.db}"
    c.env = {"BARISTA_DATABASE_URL": db, "BARISTA_OWNER_DATABASE_URL": owner, "BARISTA_DEV_AUTH": "1", "BARISTA_WORK_ENGINE": "false",
             "BARISTA_MORNING_INTERVAL_S": "0", "BARISTA_PUBLIC_API_URL": f"http://127.0.0.1:{c.port}",
             "BARISTA_LLM_MODEL": opts.get("model", "google:gemini-3.8-flash"), "BARISTA_AGENT_THINKING": opts.get("thinking", "low"),
             "BARISTA_AGENT_TOOLS": opts.get("tools", "all"), "BARISTA_AGENT_TOOL_K": opts.get("k", "15"),
             "BARISTA_EVAL_NOISE_TOOLS": opts.get("noise", "0")}
    if "key" in opts:
        path, var = opts["key"].rsplit(":", 1)
        c.env |= {"BARISTA_LLM_API_KEY_FILE": path, "BARISTA_LLM_API_KEY_VAR": var}
    return c


async def _sh(*args: str, env: dict[str, str] | None = None, log: pathlib.Path | None = None) -> int:
    out = log.open("w") if log else subprocess.DEVNULL
    p = await asyncio.create_subprocess_exec(*args, cwd=BACKEND, env={**os.environ, **(env or {})}, stdout=out, stderr=subprocess.STDOUT)
    return await p.wait()


async def _one(c: Config, a: argparse.Namespace) -> None:
    await _sh("dropdb", "--if-exists", c.db)
    await _sh("createdb", "-O", "barista_owner", c.db)
    await _sh("uv", "run", "python", "-m", "barista.seed", "--reset", env=c.env, log=c.out / f"seed-{c.port}.log")
    server = await asyncio.create_subprocess_exec("uv", "run", "uvicorn", "barista.main:app", "--host", "127.0.0.1", "--port", str(c.port),
                                                  cwd=BACKEND, env={**os.environ, **c.env},
                                                  stdout=(c.out / f"server-{c.port}.log").open("w"), stderr=subprocess.STDOUT)
    try:
        for _ in range(120):
            if await _sh("curl", "-sf", f"http://127.0.0.1:{c.port}/health") == 0:
                break
            await asyncio.sleep(1)
        else:
            raise RuntimeError(f"{c.name}: server không lên — xem {c.out}/server-{c.port}.log")
        await _sh("uv", "run", "python", "scripts/eval_assistant.py", "--core", f"http://127.0.0.1:{c.port}", "--db", c.db, "--runs", str(a.runs),
                  "--set", a.set, *(["--only", a.only] if a.only else []), "--json", str(c.out / f"{c.port}.json"),
                  env={"PYTHONUNBUFFERED": "1"}, log=c.out / f"eval-{c.port}.log")
    finally:
        server.terminate()
        await server.wait()


def _psql(db: str, sql: str) -> str:
    return subprocess.run(["psql", "-h", "localhost", "-d", db, "-tAc", sql], capture_output=True, text=True).stdout.strip()


def _summary(configs: list[Config]) -> str:
    sys.path.insert(0, str(HERE))
    import eval_assistant as ev

    hard = {x.key for x in ev.HARD}
    head = ("cấu hình", "đạt cơ bản", "đạt khó", "vượt quyền", "có sẵn ngay", "mở thêm", "gọi nhiễu", "giây TV", "giây 10% chậm",
            "token", "USD/lượt", "đọc cache", "công cụ gửi", "tuyển ms")
    rows: list[tuple[str, ...]] = [head]
    for c in configs:
        path = c.out / f"{c.port}.json"
        if not path.exists():
            rows.append((c.name, "LỖI — xem log", *[""] * (len(head) - 2)))
            continue
        r = json.loads(path.read_text())
        base, hd = [x for x in r if x["case"] not in hard], [x for x in r if x["case"] in hard]
        secs = sorted(x["seconds"] for x in r)
        cache = _psql(c.db, "SELECT round(100.0 * sum(cache_read_tokens) / nullif(sum(input_tokens), 0)) FROM core.ai_usage")
        rows.append((c.name, f"{sum(x['ok'] for x in base)}/{len(base)}", f"{sum(x['ok'] for x in hd)}/{len(hd)}",
                     str(sum(x["overreach"] for x in r)), f"{100 * statistics.mean(x.get('first_try', 0) for x in r):.0f}%",
                     f"{statistics.mean(x.get('opened', 0) for x in r):.2f}", str(sum(x.get("noise_calls", 0) for x in r)),
                     f"{statistics.median(secs):.1f}", f"{secs[max(0, int(len(secs) * 0.9) - 1)]:.1f}",
                     f"{statistics.mean(x['tokens'] for x in r) / 1000:.0f}k", f"{statistics.mean(x['cost'] for x in r):.4f}", f"{cache or '?'}%",
                     f"{min(x.get('offered', 0) for x in r)}–{max(x.get('offered', 0) for x in r)}",
                     f"{statistics.mean(x.get('plan_ms', 0) for x in r):.1f}"))
    return "\n".join("| " + " | ".join(row) + " |" + ("\n|" + "---|" * len(head) if i == 0 else "") for i, row in enumerate(rows))


async def _run(configs: list[Config], a: argparse.Namespace) -> list[BaseException | None]:
    return list(await asyncio.gather(*(_one(c, a) for c in configs), return_exceptions=True))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("configs", nargs="+", help='"tên:model=…,thinking=…,tools=all|retrieve|groups,k=15,noise=0,key=file:VAR"')
    ap.add_argument("--set", default="all", choices=["base", "hard", "all"])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default=f"/tmp/eval-matrix-{time.strftime('%Y%m%d-%H%M')}")
    ap.add_argument("--keep", action="store_true", help="giữ DB tạm (mở lại lịch sử sau)")
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    configs = [_parse(s, i, out) for i, s in enumerate(a.configs)]
    t0 = time.monotonic()
    for c, res in zip(configs, asyncio.run(_run(configs, a)), strict=True):
        if isinstance(res, BaseException):
            print(f"{c.name}: {res}")
    table = _summary(configs)
    report = out / "lich-su-goi-cong-cu.html"
    subprocess.run(["uv", "run", "python", "scripts/eval_report.py", "--out", str(report),
                    *[f"{c.name}={c.db}:{c.out / f'{c.port}.json'}" for c in configs if (c.out / f"{c.port}.json").exists()]],
                   cwd=BACKEND, check=False, capture_output=True)
    (out / "bang-so.md").write_text(table + "\n", encoding="utf-8")
    print(table)
    print(f"\n{len(configs)} cấu hình · {time.monotonic() - t0:.0f} giây · bảng: {out / 'bang-so.md'} · lịch sử: {report}")
    if not a.keep:
        for c in configs:
            subprocess.run(["dropdb", "--if-exists", c.db], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
