"""Bấm MỘT lần mở dòng (12/11/2026 — chủ doanh nghiệp: "bấm người xem chi tiết phải hai lần"): màn khai ``row_click`` = "open" (mở chi tiết) hoặc lệnh của
một nút trên dòng; manifest chỉ gửi khi người xem chạy được đích — không thì bấm chỉ chọn dòng như cũ. Trường THÊM: app cũ bỏ qua."""

import pytest

from barista import workspaces
from barista.kernel.screens import Action, Open, Screen
from barista.workspaces import manifest
from tests.conftest import Who


async def test_staff_list_opens_history_on_one_click(who: Who) -> None:
    admin = await who("admin")
    screens = {s["key"]: s for w in manifest(admin, "Công ty")["workspaces"] for s in w["screens"]}
    staff = screens["hr_staff"]
    assert staff["row_click"] == "open" and staff["open"]["query"] == "hr.history"
    assert all(s.get("row_click", "open") == "open" and (s.get("row_click") is None or s["open"]) for s in screens.values())


async def test_row_click_is_sent_only_when_the_viewer_can_run_the_target(who: Who) -> None:
    s = Screen("t", "Thử", "square", "core.approvals", row_actions=(Action("admin.invite_user", "Mời"),), row_click="admin.invite_user")
    assert workspaces.screen_spec(await who("admin"), s)["row_click"] == "admin.invite_user"  # type: ignore[index]
    sale = workspaces.screen_spec(await who("sale1"), s)
    assert sale is not None and sale["row_actions"] == [] and "row_click" not in sale        # nút bị lọc quyền ⇒ bấm chỉ chọn dòng
    hidden = Screen("t2", "Thử", "square", "core.approvals", open=Open("admin.users", {"id": "id"}), row_click="open")
    assert workspaces.screen_spec(await who("admin"), hidden)["row_click"] == "open"  # type: ignore[index]
    out = workspaces.screen_spec(await who("sale1"), hidden)
    assert out is not None and out["open"] is None and "row_click" not in out              # không mở được chi tiết ⇒ không bấm-mở


def test_row_click_must_name_a_real_target() -> None:
    with pytest.raises(ValueError, match="không khai open"):
        Screen("x", "X", "square", "core.approvals", row_click="open")
    with pytest.raises(ValueError, match="ĐÚNG MỘT nút"):
        Screen("x", "X", "square", "core.approvals", row_actions=(Action("core.decide", "Duyệt"), Action("core.decide", "Từ chối")), row_click="core.decide")
    with pytest.raises(ValueError, match="ĐÚNG MỘT nút"):
        Screen("x", "X", "square", "core.approvals", row_click="hr.history")
