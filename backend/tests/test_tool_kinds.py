"""Một trợ lý, công cụ theo loại (registry.tool_kind): công cụ THIẾT LẬP không mang theo mỗi lượt — ẩn tới khi trợ lý gọi core__open_setup trong
lượt đó (đỡ token, đỡ chọn nhầm). Việc hằng ngày vẫn đủ công cụ từ bước đầu."""


from barista.agent import chat
from tests.conftest import Who
from tests.test_assistant import Scripted
from tests.test_resume import Log


async def test_roles_without_setup_rights_get_no_open_setup_tool(who: Who) -> None:
    sale = await who("sale1")
    model = Scripted(["Chào anh."])
    await chat.chat_turn(sale, "chào", emit=Log(), model=model)
    assert "core__open_setup" not in model.tools_seen[0]                                   # không có quyền thiết lập ⇒ không có cửa mở
