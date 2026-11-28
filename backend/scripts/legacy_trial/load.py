"""Nạp thử một dự án lên DB RIÊNG ở máy (ADR 0106 §6). Mọi ghi qua executor; lặp lại không tạo trùng (sổ ánh xạ nguồn → đích); ngoại lệ được đếm, không bịa.

Mọi lệnh ghi chạy ``historical=True`` (bất biến 12 — dữ liệu hệ cũ là tham chiếu, không thông báo / việc theo dõi). Khung chung cho bản thật (sổ mã gốc
trên DB của bản cài, đối chiếu có ngưỡng, ký, ngày chuyển): ``barista.api.onboard`` + runbook 23 — script này giữ lại làm nạp thử trên DB riêng.

Đã làm: định danh + hồ sơ làm việc. CHƯA làm: giao dịch / quyền hưởng / chi, vì chưa có lệnh NHẬP LỊCH SỬ (ký lại bằng lệnh hiện tại sẽ phát lại phí, hoa hồng
và việc theo dõi theo chính sách hôm nay — đúng thứ ADR cấm): bước đó dừng rõ ràng thay vì đi đường tắt.

  BARISTA_DATABASE_URL=…/<db>_trial BARISTA_OWNER_DATABASE_URL=…/<db>_trial uv run python -m scripts.legacy_trial.load --workdir /đường/dẫn/ngoài/repo
"""

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from barista import seed
from barista.config import settings
from barista.db import db
from barista.kernel import executor
from barista.kernel.migrate import migrate
from barista.kernel.principal import Principal
from scripts.legacy_trial.tms_source import DIMENSIONS_SQL, PEOPLE_SQL, TOP_PROJECT_SQL, Person, person_of, read

SOURCE = "tms"
LEGACY_MAP_DDL = """CREATE SCHEMA IF NOT EXISTS legacy_map;
CREATE TABLE IF NOT EXISTS legacy_map.ids (source TEXT NOT NULL, entity TEXT NOT NULL, source_id TEXT NOT NULL, target_id UUID NOT NULL,
  fingerprint TEXT NOT NULL, loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (source, entity, source_id))"""


class IdMap(Protocol):
    """Nguồn × loại × mã cũ → mã mới. Nằm ngoài schema ứng dụng: chỉ DB nạp thử mới có (bất biến 11)."""

    async def get(self, entity: str, source_id: str) -> UUID | None: ...
    async def put(self, entity: str, source_id: str, target_id: UUID, fingerprint: str) -> None: ...


class PgIdMap:
    async def get(self, entity: str, source_id: str) -> UUID | None:
        async with db.owner_tx() as c:
            r = await (await c.execute("SELECT target_id FROM legacy_map.ids WHERE source=%s AND entity=%s AND source_id=%s", (SOURCE, entity, source_id))).fetchone()
        return r["target_id"] if r else None

    async def put(self, entity: str, source_id: str, target_id: UUID, fingerprint: str) -> None:
        async with db.owner_tx() as c:
            await c.execute("INSERT INTO legacy_map.ids(source,entity,source_id,target_id,fingerprint) VALUES(%s,%s,%s,%s,%s)",
                            (SOURCE, entity, source_id, target_id, fingerprint))


@dataclass
class Report:
    """Chỉ số đếm, không tên người."""

    seen: int = 0
    parties_created: int = 0
    parties_reused: int = 0
    employments: int = 0
    assignments: int = 0
    issues: Counter[str] = field(default_factory=Counter)


async def load_people(admin: Principal, people: list[Person], ids: IdMap, *, as_of: date) -> Report:
    """Người ngoài và nhân viên đều là party (không tạo đăng nhập giả). Hồ sơ làm việc chỉ khi đủ mã + ngày; người quản lý chỉ khi cũng có trong tập nạp."""
    out = Report(seen=len(people))
    for p in people:
        out.issues.update(p.issues)
        if await ids.get("person", p.source_id):
            out.parties_reused += 1
            continue
        if not p.name:
            continue
        r = await executor.run(admin, "parties.create", {"display_name": p.name, "kind": "person"}, key=f"{SOURCE}:person:{p.source_id}", historical=True)
        if r.status != "ok":
            out.issues[f"tạo định danh: {r.status}"] += 1
            continue
        await ids.put("person", p.source_id, r.output["party_id"], p.name)
        out.parties_created += 1
    for p in people:
        party = await ids.get("person", p.source_id)
        if party is None or not p.employee_code or not p.started_on or await ids.get("employment", p.source_id):
            continue
        r = await executor.run(admin, "hr.set_employment", {"party_id": str(party), "employee_code": p.employee_code, "title": p.title,
                                                             "started_on": p.started_on.isoformat()}, key=f"{SOURCE}:employment:{p.source_id}", historical=True)
        if r.status != "ok":
            out.issues[f"hồ sơ: {r.status}"] += 1
            continue
        await ids.put("employment", p.source_id, r.output["employment_id"], p.employee_code)
        out.employments += 1
        if p.ended_on:
            ended = await executor.run(admin, "hr.end_employment", {"employment_id": str(r.output["employment_id"]), "ended_on": p.ended_on.isoformat()},
                                       key=f"{SOURCE}:ended:{p.source_id}", historical=True)
            if ended.status != "ok":
                out.issues[f"ngày nghỉ: {ended.status}"] += 1
    for p in people:
        party = await ids.get("person", p.source_id)
        if party is None or not p.manager_source_id or await ids.get("assignment", p.source_id):
            continue
        boss = await ids.get("person", p.manager_source_id)
        if boss is None:
            out.issues["quản lý ngoài tập nạp"] += 1
            continue
        # Nguồn chỉ có cây HIỆN TẠI, không có ngày: lịch sử bắt đầu từ ngày chốt, không giả vờ biết trước đó.
        r = await executor.run(admin, "hr.set_assignment", {"party_id": str(party), "manager_party_id": str(boss), "valid_from": as_of.isoformat(),
                                                           # nạp hệ cũ luôn LÙI ngày (as_of là ngày chụp dữ liệu) — quá hạn mức lùi thì hr bắt ghi lý do
                                                           "reason": "Nạp từ hệ cũ — cơ cấu theo ngày chụp dữ liệu"},
                               key=f"{SOURCE}:assignment:{p.source_id}", historical=True)
        if r.status != "ok":
            out.issues[f"phân công: {r.status}"] += 1
            continue
        await ids.put("assignment", p.source_id, r.output["assignment_id"], str(boss))
        out.assignments += 1
    return out


def reconcile(report: Report, loaded_parties: int, source_dims: dict[str, Any]) -> list[str]:
    """Đối soát từng chiều: chiều nào chưa nạp được thì nói CHƯA, không báo khớp."""
    lines = [f"người: nguồn {report.seen} · định danh đích {loaded_parties} · thiếu {report.seen - loaded_parties}",
             f"hồ sơ làm việc đủ dữ kiện: {report.employments}/{report.seen}", f"phân công quản lý: {report.assignments}"]
    lines += [f"ngoại lệ · {k}: {v}" for k, v in sorted(report.issues.items())]
    lines += [f"chưa nạp (cần lệnh nhập lịch sử) · nguồn {k}: {v}" for k, v in source_dims.items()]
    return lines


def guard(workdir: Path) -> None:
    db_name = settings.owner_database_url.rsplit("/", 1)[-1].split("?")[0]
    if not db_name.endswith("_trial"):
        raise SystemExit(f"Dừng: chỉ nạp thử trên DB tên …_trial, đang trỏ {db_name}")
    repo = Path(__file__).resolve().parents[3]
    if workdir.resolve().is_relative_to(repo):
        raise SystemExit("Dừng: thư mục làm việc phải ở ngoài repo (dữ liệu thật không vào git)")


async def main(project: int | None, as_of: date) -> list[str]:
    await migrate(settings.owner_database_url)
    await db.open(settings.database_url, settings.owner_database_url)
    try:
        async with db.owner_tx() as c:
            await c.execute(LEGACY_MAP_DDL)
            have = await (await c.execute("SELECT id FROM core.tenants ORDER BY created_at LIMIT 1")).fetchone()
        tid = have["id"] if have else await seed.create_tenant("Nạp thử", "trial.local", packs=("finance", "hr"))
        admin = await seed.who(tid, "admin", "trial.local")
        pid = project or read(TOP_PROJECT_SQL.format(since="2025-03-19"))[0]["project_id"]
        people = [person_of(r) for r in read(PEOPLE_SQL.format(project=int(pid)))]
        dims = read(DIMENSIONS_SQL.format(project=int(pid)))[0]
        report = await load_people(admin, people, PgIdMap(), as_of=as_of)
        async with db.owner_tx() as c:
            n = (await (await c.execute("SELECT count(*) AS n FROM legacy_map.ids WHERE entity='person'")).fetchone())["n"]
        return reconcile(report, n, dims)
    finally:
        await db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", type=Path, required=True)
    ap.add_argument("--project", type=int, default=None)
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    a = ap.parse_args()
    guard(a.workdir)
    lines = asyncio.run(main(a.project, a.as_of))
    print("\n".join(lines))
    a.workdir.mkdir(parents=True, exist_ok=True)
    (a.workdir / "report.txt").write_text("\n".join(lines))
