"""0068: ngày nghỉ / làm bù theo năm, không nằm trong phiên bản luật. Nghĩa vụ giữ đúng lịch đã dùng khi sinh hạn."""

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from barista.kernel import business_calendar as cal
from barista.kernel import regulations
from tests.conftest import owner_val
from tests.test_books import _company, _open


def test_holidays_makeup_days_and_missing_years_are_explicit() -> None:
    assert cal.years()
    for before, after in [("2025-04-30", "2025-05-05"), ("2025-04-26", "2025-04-26"),
                          ("2026-01-02", "2026-01-05"), ("2026-01-10", "2026-01-10"),
                          ("2026-02-20", "2026-02-23"), ("2026-04-30", "2026-05-04"), ("2026-08-31", "2026-09-03")]:
        got = cal.next_workday("vn-public", date.fromisoformat(before))
        assert got.day == date.fromisoformat(after) and got.refs and got.warnings   # lịch soạn sẵn chưa được kế toán xác nhận
    unknown = cal.next_workday("vn-public", date(2030, 6, 1))
    assert unknown.day == date(2030, 6, 3) and unknown.refs == ("vn-public/2030@weekends-only",)
    assert "Chưa có lịch" in unknown.warnings[0]
    with pytest.raises(ValueError, match="Không có lịch"):
        cal.next_workday("typo", date(2026, 1, 1))


def test_missing_year_preserves_the_selected_weekly_schedule() -> None:
    sunday = date(2027, 1, 10)
    all_week = cal.next_workday("vn-ca-tuan", sunday)
    assert all_week.day == sunday and all_week.warnings
    assert "chưa xét ngày lễ" in all_week.warnings[0]
    assert cal.next_workday("vn-public", sunday).day == date(2027, 1, 11)


async def test_cross_year_calendar_and_new_calendar_version_do_not_rewrite_existing_filings(tmp_path: Path) -> None:
    shipped = cal.ROOT
    root = tmp_path / "calendars"
    shutil.copytree(shipped, root)
    try:
        path = root / "vn-public" / "2025" / "2026-11-02.json"
        raw = json.loads(path.read_text())
        raw["holidays"].append("2025-12-31")
        path.write_text(json.dumps(raw))
        cal.reload(root)
        moved = cal.next_workday("vn-public", date(2025, 12, 31))
        assert moved.day == date(2026, 1, 5) and len(moved.refs) == 2   # kiểm cả hai năm, kể cả ngày nghỉ sau ngày đầu năm
        tid, _ = await _company()
        await _open(tid, "2026-10-05")
        before = await owner_val("SELECT jsonb_agg(jsonb_build_array(due_on, original_due_on, rule_ref, calendar_ref) ORDER BY id) FROM books.filings WHERE tenant_id = %s", (tid,))
        path = root / "vn-public" / "2026" / "2026-11-02.json"
        raw = json.loads(path.read_text())
        raw["version"] = "2026-11-03"
        raw["holidays"].append("2026-11-02")
        (path.parent / "2026-11-03.json").write_text(json.dumps(raw))
        cal.reload()
        v = regulations.version_on("vn-tax", date(2026, 9, 30))
        assert regulations.due_date(v, date(2026, 9, 1), 1, 31) == date(2026, 11, 3)
        await _open(tid, "2026-10-05")   # chạy lại không tính lại nghĩa vụ đã có
        assert before == await owner_val("SELECT jsonb_agg(jsonb_build_array(due_on, original_due_on, rule_ref, calendar_ref) ORDER BY id) FROM books.filings WHERE tenant_id = %s", (tid,))
    finally:
        cal.reload(shipped)


def test_conflicting_calendar_dates_are_rejected(tmp_path: Path) -> None:
    shipped = cal.ROOT
    root = tmp_path / "calendars"
    shutil.copytree(shipped, root)
    path = root / "vn-public" / "2026" / "2026-11-02.json"
    raw = json.loads(path.read_text())
    raw["holidays"].append(raw["working_days"][0])
    path.write_text(json.dumps(raw))
    try:
        cal.reload(root)
        with pytest.raises(ValueError, match="không hợp lệ"):
            cal.years()
    finally:
        cal.reload(shipped)


def test_previous_workday_walks_back_for_internal_deadlines() -> None:
    sat = date(2026, 11, 7)
    assert cal.previous_workday("vn-public", sat).day == date(2026, 11, 6)        # việc nội bộ: xong thứ Sáu
    assert cal.next_workday("vn-public", sat).day == date(2026, 11, 9)            # hạn theo luật: sang thứ Hai (giữ nguyên)
    assert cal.previous_workday("vn-ca-tuan", sat).day == sat                     # đội làm cả tuần

