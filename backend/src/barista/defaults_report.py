"""KIỂM PHẦN MẶC ĐỊNH CỦA BẢN CÀI — CHỈ ĐỌC (bước của phát hành, runbook 11): với MỖI công ty trong DB, phần mặc định của các gói đang bật mà công ty
chưa có (``admin.defaults_drift`` — cùng truy vấn màn Quản trị › Phần mặc định chưa cài, qua executor bằng quyền hệ thống). Không cài gì: cài là việc
của quản trị (nút trên màn) hoặc ``admin.install_defaults``.

  uv run python -m barista.defaults_report                    # mọi công ty, in báo cáo; mã thoát 0
  uv run python -m barista.defaults_report --tenant "Sàn mẫu" # một công ty
  uv run python -m barista.defaults_report --strict           # thiếu thứ QUAN TRỌNG (vai / quyền / chuỗi / luật duyệt) ⇒ mã thoát 3
  uv run python -m barista.defaults_report --json             # in JSON (máy đọc)

Ba loại: "chưa cài" (an toàn để cài thêm), "chờ chủ tài khoản" (quyền mở rộng thẩm quyền tiền / quyền — không tự cài), "công ty đổi khác" (đã gỡ / hẹp hơn
— quyết định của công ty, chỉ báo). ``deploy/ops/deploy.py`` chạy lệnh này từ ẢNH MỚI sau bước migrate.
"""

import argparse
import asyncio
import json
import sys
from typing import Any

import barista.modules  # noqa: F401 — nạp mọi module + gói đã cài để registry đủ (như seed)
from barista.config import settings
from barista.db import db
from barista.kernel import executor
from barista.kernel.principal import system_principal

EXIT_IMPORTANT = 3
LABEL = {"missing": "chưa cài", "held": "chờ chủ tài khoản cấp", "changed": "công ty đổi khác"}
SHOW = 40   # mỗi loại in tối đa ngần ấy dòng / công ty (JSON in đủ)


async def collect(tenant: str = "") -> list[dict[str, Any]]:
    async with db.owner_tx() as c:
        cur = await c.execute("SELECT id, name FROM core.tenants WHERE %s = '' OR id::text = %s OR name ILIKE %s ORDER BY created_at",
                              (tenant, tenant, f"%{tenant}%"))
        companies = await cur.fetchall()
    out = []
    for t in companies:
        r = await executor.query(system_principal(t["id"]), "admin.defaults_drift", {}, quiet=True)
        if r.status != "ok":
            out.append({"tenant": t["name"], "id": str(t["id"]), "error": f"{r.status}: {r.error}", "rows": []})
            continue
        rows = [{k: row[k] for k in ("kind", "key", "item", "pack", "status", "detail", "important")} for row in r.output["rows"]]
        out.append({"tenant": t["name"], "id": str(t["id"]), "rows": rows})
    return out


def important(companies: list[dict[str, Any]]) -> int:
    return sum(1 for c in companies for r in c["rows"] if r["important"]) + sum(1 for c in companies if c.get("error"))


def render(companies: list[dict[str, Any]]) -> str:
    lines = []
    for c in companies:
        if c.get("error"):
            lines.append(f"{c['tenant']}: KHÔNG KIỂM ĐƯỢC — {c['error']}")
            continue
        by = {s: [r for r in c["rows"] if r["status"] == s] for s in LABEL}
        if not c["rows"]:
            lines.append(f"{c['tenant']}: mặc định của các gói đang bật đã cài đủ")
            continue
        lines.append(f"{c['tenant']}: {', '.join(f'{len(v)} {LABEL[s]}' for s, v in by.items() if v)}")
        for s, rows in by.items():
            for r in rows[:SHOW]:
                mark = "!" if r["important"] else " "
                lines.append(f"  {mark} [{LABEL[s]}] {r['kind']} {r['key']} ({r['pack']})" + (f" — {r['detail']}" if r["detail"] else ""))
            if len(rows) > SHOW:
                lines.append(f"    … và {len(rows) - SHOW} mục {LABEL[s]} nữa (--json để xem đủ)")
    n = important(companies)
    lines.append(f"Thiếu thứ quan trọng (vai / quyền / chuỗi / luật duyệt, dấu !): {n}" if n else "Không thiếu thứ quan trọng")
    if any(r["status"] == "missing" for c in companies for r in c["rows"]):
        lines.append("Cài phần còn thiếu: Quản trị › Phần mặc định chưa cài › Cài phần còn thiếu (quản trị công ty bấm). "
                     "“Chờ chủ tài khoản”: chủ tài khoản cấp ở Vai trò & quyền. “Công ty đổi khác”: không sửa.")
    return "\n".join(lines)


async def amain(a: argparse.Namespace) -> int:
    await db.open(settings.database_url, settings.owner_database_url)
    try:
        companies = await collect(a.tenant)
    finally:
        await db.close()
    print(json.dumps(companies, ensure_ascii=False, indent=1) if a.json else render(companies), flush=True)
    return EXIT_IMPORTANT if a.strict and important(companies) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m barista.defaults_report", description="Kiểm phần mặc định của gói còn thiếu ở từng công ty — chỉ đọc")
    ap.add_argument("--tenant", default="", help="tên (một phần) hoặc id công ty; trống = mọi công ty")
    ap.add_argument("--strict", action="store_true", help="thiếu vai / quyền / chuỗi / luật duyệt ⇒ mã thoát 3")
    ap.add_argument("--json", action="store_true", help="in JSON")
    return asyncio.run(amain(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
