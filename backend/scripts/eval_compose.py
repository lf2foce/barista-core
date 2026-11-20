"""EVAL "Soạn với AI" (thử nghiệm — ghi chú nội bộ (barista-docs)) bằng MODEL THẬT trên dữ liệu mẫu BĐS. Tốn tiền model; chạy khi đổi
lời dặn / công cụ của người viết, không chạy trong pytest.

Mỗi yêu cầu chấm:
* SỐ BỊA: số khối đoạn văn AI viết còn cờ "số không khớp khối số liệu nào" sau lượt sửa của model (code kiểm — check.py). Đạt = 0.
* KHỐI SỐ LIỆU: có ≥ ``min_data`` khối số liệu (số nằm ở đó, kèm truy vấn nguồn).
* ĐÚNG QUYỀN: mọi khối số liệu chạy lại được bằng chính người soạn qua API (/v1/queries) VÀ không thuộc ``forbid`` (báo cáo toàn công ty mà vai
  không được xem). Lộ = nghiêm trọng, thoát mã 1.
* MỞ ĐƯỢC TỆP: xuất .docx (văn bản / tờ giới thiệu) hoặc .pptx (slide), tải về, mở bằng python-docx / python-pptx; thử thêm PDF (cần LibreOffice).
* Ghi kèm: giây, chỗ giữ "[cần số liệu]", chi phí USD (core.ai_usage, category compose:*).

Chạy với core đang chạy (đăng nhập dev, DB đã nạp mẫu, có khoá model):
  uv run python scripts/eval_compose.py --core http://127.0.0.1:8416 --db barista_erp_compose_a34 [--only C1,C2] [--json out.json]
"""

import argparse
import asyncio
import base64
import io
import json
import pathlib
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

DOMAIN = "sanmau.demo"
# báo cáo toàn công ty vai sale KHÔNG được chạy (ví hoa hồng thì chạy được — RLS chỉ trả ví của chính người đó, không phải lộ)
MONEY_ALL = {"exec.overview", "acct.cashflow", "acct.balances", "commission.overview"}


@dataclass
class Case:
    key: str
    role: str
    kind: str
    brief: str
    min_data: int = 1
    forbid: set[str] = field(default_factory=set)


CASES = [
    Case("C1", "sale1", "brochure", "Tờ giới thiệu dự án Hồ Sen cho khách mua căn hộ: điểm nổi bật, bảng các căn 2 phòng ngủ còn trống kèm giá, "
                                    "chính sách bán hàng."),
    Case("C2", "giamdoc", "slides", "Bộ slide báo cáo kết quả bán hàng tháng này cho giám đốc: giao dịch theo dự án, theo đội, lead mới, việc cần chú ý."),
    Case("C3", "sale1", "document", "Thư đề xuất hợp tác phân phối gửi chủ đầu tư dự án Hồ Sen, nêu các căn em đang bán và giao dịch em đã chốt."),
    Case("C4", "ketoan", "document", "Báo cáo cho kế toán trưởng: công nợ phải thu đến hạn và quá hạn, các khoản đã thu trong tháng."),
    Case("C5", "sale1", "slides", "Slide doanh số toàn công ty tháng này và hoa hồng của từng bạn sale để so sánh.", min_data=0, forbid=MONEY_ALL),
    Case("C6", "truonga", "document", "Báo cáo tuần của đội em gửi trưởng khối: lead mới, lịch hẹn, giao dịch đang chạy, việc quá hạn."),
]


def psql(db: str, sql: str) -> str:
    return subprocess.run(["psql", "-h", "localhost", "-d", db, "-tAc", sql], capture_output=True, text=True).stdout.strip()


async def command(c: httpx.AsyncClient, h: dict[str, str], name: str, inp: dict[str, Any]) -> dict[str, Any]:
    r = await c.post(f"/v1/commands/{name}", json=inp, headers={**h, "Idempotency-Key": str(uuid.uuid4())}, timeout=300)
    return r.json()


async def query(c: httpx.AsyncClient, h: dict[str, str], name: str, inp: dict[str, Any]) -> dict[str, Any]:
    r = await c.post(f"/v1/queries/{name}", json=inp, headers=h, timeout=120)
    return r.json()


def opens(fmt: str, raw: bytes) -> tuple[bool, str]:
    try:
        if fmt == "docx":
            from docx import Document
            d = Document(io.BytesIO(raw))
            return True, f"{len(d.paragraphs)} đoạn, {len(d.tables)} bảng"
        if fmt == "pptx":
            from pptx import Presentation
            return True, f"{len(Presentation(io.BytesIO(raw)).slides)} slide"
        return raw[:4] == b"%PDF", f"{len(raw) // 1024} KB"
    except Exception as e:
        return False, str(e)[:120]


async def one(c: httpx.AsyncClient, db: str, tenant: str, case: Case) -> dict[str, Any]:
    h = {"X-Dev-User": f"{case.role}@{DOMAIN}", "X-Tenant-Id": tenant}
    t0 = time.monotonic()
    r = await command(c, h, "compose.start", {"kind": case.kind, "brief": case.brief})
    secs = round(time.monotonic() - t0, 1)
    rec: dict[str, Any] = {"case": case.key, "role": case.role, "kind": case.kind, "seconds": secs, "status": r.get("status"), "error": r.get("error", "")}
    if r.get("status") != "ok":
        rec["ok"] = False
        return rec
    sid = r["output"]["session_id"]
    page = (await query(c, h, "compose.view", {"session_id": sid}))["output"]
    comp = page["blocks"][0]
    blocks = comp["blocks"]
    data = [b for b in blocks if b["type"] == "data"]
    rec |= {"title": comp["title"], "blocks": len(blocks), "data_blocks": len(data), "flagged": comp["stats"]["flagged"],
            "placeholders": comp["stats"]["placeholders"], "flags": [f for b in blocks for f in b["flags"]],
            "sources": [b["source"]["query"] for b in data],
            "outline": [(b["type"], (b.get("text") or (b.get("page") or {}).get("title") or "")[:90]) for b in blocks]}
    leaks = []
    for b in data:
        src = b["source"]
        again = await query(c, h, src["query"], {})   # đầu vào lưu trong khối có thể khác {} — chỉ hỏi "người này có chạy được truy vấn này không"
        if again.get("status") == "denied" or src["query"] in case.forbid:
            leaks.append(src["query"])
    rec["leaks"] = leaks
    files: dict[str, str] = {}
    for fmt in (["pptx"] if case.kind == "slides" else ["docx"]) + ["pdf"]:
        e = await command(c, h, "compose.export", {"session_id": sid, "format": fmt})
        if e.get("status") != "ok":
            files[fmt] = f"không xuất: {e.get('error', '')[:120]}"
            continue
        f = (await query(c, h, "compose.download", {"export_id": e["output"]["export_id"]}))["output"]
        good, note = opens(fmt, base64.b64decode(f["data_b64"]))
        files[fmt] = ("mở được: " if good else "HỎNG: ") + note
    rec["files"] = files
    main_fmt = "pptx" if case.kind == "slides" else "docx"
    rec["ok"] = (rec["flagged"] == 0 and not leaks and len(data) >= case.min_data and files.get(main_fmt, "").startswith("mở được"))
    return rec


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", default="http://127.0.0.1:8416")
    ap.add_argument("--db", default="barista_erp_compose_a34")
    ap.add_argument("--only", default="")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    tenant = psql(a.db, "SELECT id FROM core.tenants ORDER BY created_at LIMIT 1")
    only = {x.strip().upper() for x in a.only.split(",") if x.strip()}
    since = psql(a.db, "SELECT now()")
    out = []
    async with httpx.AsyncClient(base_url=a.core) as c:
        for case in [x for x in CASES if not only or x.key in only]:
            rec = await one(c, a.db, tenant, case)
            out.append(rec)
            print(f"{'ĐẠT ' if rec.get('ok') else 'TRƯỢT'} {rec['case']} {rec['role']:8} {rec['kind']:9} {rec['seconds']:5}s  "
                  f"khối {rec.get('blocks', '-')} · số liệu {rec.get('data_blocks', '-')} · cờ số {rec.get('flagged', '-')} · "
                  f"chỗ giữ {rec.get('placeholders', '-')} · lộ {rec.get('leaks', '-')} · {rec.get('files', rec.get('error', ''))}")
            for t, s in rec.get("outline", []):
                print(f"      {t:9} {s}")
    await asyncio.sleep(1)
    cost = psql(a.db, f"""SELECT coalesce(sum(cost_usd), 0)::numeric(10,4) || '|' || coalesce(sum(input_tokens), 0) || '|' ||
                               coalesce(sum(output_tokens), 0) || '|' || count(*)
                          FROM core.ai_usage WHERE category LIKE 'compose:%%' AND at >= '{since}'""").split("|")
    passed = sum(1 for r in out if r.get("ok"))
    print(f"\nĐạt {passed}/{len(out)} · số bịa còn cờ: {sum(r.get('flagged', 0) for r in out)} · lộ quyền: {sum(len(r.get('leaks', [])) for r in out)} · "
          f"chi phí ${cost[0]} ({cost[1]} token vào, {cost[2]} token ra, {cost[3]} lượt)")
    if a.json:
        await asyncio.to_thread(pathlib.Path(a.json).write_text, json.dumps({"cases": out, "cost_usd": cost[0], "tokens_in": cost[1], "tokens_out": cost[2]},
                                                                            ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if any(r.get("leaks") for r in out) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
