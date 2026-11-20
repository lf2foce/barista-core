"""So p95 trước → sau theo thao tác × vai từ hai tệp JSON của bench.py: ``uv run python scripts/perf/cmp.py before.json after.json``."""

import json
import pathlib
import sys

ROLES = ["sale", "lead", "khoi", "unit", "director", "acct"]


def cell(rows: list[dict], op: str, role: str) -> str:
    m = [x for x in rows if x["op"] == op and x["role"] == role]
    if not m:
        return "-"
    x = m[0]
    if x["status"] == "ok":
        return f"{x['p95']:.0f}"
    if x["status"] == "denied":
        return "x"
    return ">15s"


def main() -> None:
    before = json.loads(pathlib.Path(sys.argv[1]).read_text())
    after = json.loads(pathlib.Path(sys.argv[2]).read_text())
    ops: list[str] = []
    for x in before:
        if x["op"] not in ops:
            ops.append(x["op"])
    print(f"{'op (p95 ms trước→sau)':36}" + "".join(f"{ro:>14}" for ro in ROLES))
    for op in ops:
        print(f"{op:36}" + "".join(f"{cell(before, op, ro) + '→' + cell(after, op, ro):>14}" for ro in ROLES))


if __name__ == "__main__":
    main()
