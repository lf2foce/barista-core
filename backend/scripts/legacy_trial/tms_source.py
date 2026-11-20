"""LỚP MIGRATE (bất biến 11): nơi DUY NHẤT trong repo được nhắc tên bảng / cột TMS. Đọc chỉ-đọc qua SSH, đổi dòng cũ thành bản ghi trung tính.
File này không chứa dữ liệu; đầu ra ghi vào thư mục làm việc NGOÀI repo. Máy nguồn, tên DB và cột mã nhân viên là của BẢN CÀI (lõi không ghi tên
khách): đặt ``LEGACY_SSH_HOST``, ``LEGACY_SOURCE_DB``, ``LEGACY_EMPLOYEE_CODE_COLUMN`` lúc chạy (giá trị ở tài liệu nội bộ của bản cài)."""

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from typing import Any

SSH_HOST = os.environ.get("LEGACY_SSH_HOST", "")
SOURCE_DB = os.environ.get("LEGACY_SOURCE_DB", "")
CODE_COLUMN = os.environ.get("LEGACY_EMPLOYEE_CODE_COLUMN", "")

# Người có mặt trên giao dịch của một dự án: kể cả người đã xoá mềm (nghĩa vụ cũ vẫn cần định danh).
PEOPLE_SQL = """
SELECT DISTINCT u.id, u.full_name, u.account_type, <employee_code> AS employee_code, u.started_day, u.resigned_day, u.manager_id, r.name AS role_name
FROM nguoi_cham_soc n
JOIN deals d ON d.id = n.id_khach_hang_tiem_nang
JOIN giao_dich g ON g.id_khach_hang_tiem_nang = d.id AND g.deleted_at IS NULL
JOIN users u ON u.id = n.id_nguoi_cham_soc
LEFT JOIN roles r ON r.id = u.role_id
WHERE n.deleted_at IS NULL AND d.project_id = {project}
""".replace("<employee_code>", f"u.{CODE_COLUMN}" if re.fullmatch(r"[a-z_][a-z0-9_]*", CODE_COLUMN) else "NULL")
TOP_PROJECT_SQL = """
SELECT d.project_id FROM giao_dich g JOIN deals d ON d.id = g.id_khach_hang_tiem_nang
WHERE g.deleted_at IS NULL AND g.created_at >= '{since}' GROUP BY 1 ORDER BY count(*) DESC LIMIT 1
"""
DIMENSIONS_SQL = """
SELECT (SELECT count(*) FROM giao_dich g JOIN deals d ON d.id=g.id_khach_hang_tiem_nang WHERE g.deleted_at IS NULL AND d.project_id={project}) contracts,
       (SELECT count(*) FROM nguoi_cham_soc n JOIN deals d ON d.id=n.id_khach_hang_tiem_nang JOIN giao_dich g ON g.id_khach_hang_tiem_nang=d.id
        WHERE n.deleted_at IS NULL AND g.deleted_at IS NULL AND d.project_id={project}) entitlement_rows,
       (SELECT coalesce(sum(c.thuc_nhan),0)::bigint FROM chi_tiet_dot_thanh_toan c JOIN dot_thanh_toan t ON t.id=c.id_dot_thanh_toan
        JOIN giao_dich g ON g.id=t.id_giao_dich JOIN deals d ON d.id=g.id_khach_hang_tiem_nang
        WHERE c.deleted_at IS NULL AND t.deleted_at IS NULL AND g.deleted_at IS NULL AND c.loai_dong_tien=2 AND c.trang_thai_thanh_toan=2 AND d.project_id={project}) paid_out_vnd
"""


def read(sql: str) -> list[dict[str, Any]]:
    if not (SSH_HOST and SOURCE_DB and re.fullmatch(r"[a-z_][a-z0-9_]*", CODE_COLUMN)):
        raise RuntimeError("Thiếu nguồn: đặt LEGACY_SSH_HOST, LEGACY_SOURCE_DB, LEGACY_EMPLOYEE_CODE_COLUMN (chữ thường) — giá trị của bản cài")
    r = subprocess.run(["ssh", SSH_HOST, f"sql {SOURCE_DB}"], input=" ".join(sql.split()), capture_output=True, text=True, timeout=60, check=False)
    if r.returncode:
        raise RuntimeError(f"Nguồn báo lỗi: {r.stderr[:300]}")
    out = json.loads(r.stdout)
    if out.get("truncated"):
        raise RuntimeError("Kết quả bị cắt ở giới hạn nguồn: chia nhỏ phạm vi, không nạp thiếu")
    return [dict(zip(out["columns"], row, strict=True)) for row in out["rows"]]


@dataclass
class Person:
    """Bản ghi trung tính, không còn tên cột cũ."""

    source_id: str
    name: str
    outside: bool
    employee_code: str = ""
    title: str = ""
    started_on: date | None = None
    ended_on: date | None = None
    manager_source_id: str = ""
    issues: list[str] = field(default_factory=list)


def _day(v: Any) -> date | None:
    return date.fromisoformat(str(v)[:10]) if v else None


def person_of(row: dict[str, Any]) -> Person:
    """Thiếu dữ kiện thì ghi ngoại lệ, KHÔNG bịa (ngày vào, mã nhân viên)."""
    p = Person(
        source_id=str(row["id"]),
        name=(row.get("full_name") or "").strip(),
        outside=str(row.get("account_type")) != "0",
        employee_code=(row.get("employee_code") or "").strip(),
        title=(row.get("role_name") or "").strip(),
        started_on=_day(row.get("started_day")),
        ended_on=_day(row.get("resigned_day")),
        manager_source_id=str(row["manager_id"]) if row.get("manager_id") else "",
    )
    if not p.name:
        p.issues.append("thiếu tên")
    if not p.employee_code:
        p.issues.append("thiếu mã nhân viên")
    if not p.started_on:
        p.issues.append("thiếu ngày vào")
    return p
