"""Ngày bắt đầu khi hạn bị lịch làm việc lùi (20/11/2026: dữ liệu mẫu tạo vào thứ Sáu ⇒ "bắt đầu 21/11 sau hạn 20/11", cả bộ test đỏ). Người giao chọn
bắt đầu thứ Bảy, hạn Chủ nhật — hợp lệ; hạn lùi về thứ Sáu ⇒ bắt đầu cũng lùi về thứ Sáu, không báo lỗi người giao không gây ra."""

from datetime import date

import pytest

from barista.kernel.errors import Invalid
from barista.modules.flow.schedule_rules import check_schedule, fit_start

FRI, SAT, SUN, MON = date(2026, 11, 20), date(2026, 11, 21), date(2026, 11, 22), date(2026, 11, 23)


def test_a_start_overtaken_by_a_moved_deadline_follows_it() -> None:
    assert fit_start(SAT, SUN, FRI) == FRI
    check_schedule(fit_start(SAT, SUN, FRI), FRI)


def test_a_start_already_after_the_chosen_deadline_is_still_refused() -> None:
    assert fit_start(MON, SUN, FRI) == MON
    with pytest.raises(Invalid, match="sau hạn"):
        check_schedule(fit_start(MON, SUN, FRI), FRI)


def test_nothing_changes_when_the_deadline_did_not_move_or_there_is_no_start() -> None:
    assert fit_start(FRI, MON, MON) == FRI
    assert fit_start(None, SUN, FRI) is None
    assert fit_start(SAT, None, None) == SAT
