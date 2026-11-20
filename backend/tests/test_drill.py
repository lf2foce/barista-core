"""Nguồn gốc con số (0097): ô số khai ``drill`` thì bấm ra ĐÚNG các dòng tạo nên nó — đếm (hoặc cộng cột ``sum``) các dòng của nguồn = con số.
Test quét MỌI màn mọi vai mẫu mở được: ô số nào khai nguồn mà lệch là đỏ — không cần nhớ từng ô. Nguồn người xem không mở được thì không lộ."""


from barista.kernel import executor
from tests.conftest import Who


async def test_drill_is_dropped_when_the_viewer_cannot_run_the_source_query(who: Who) -> None:
    page = {"kpis": [{"label": "x", "value": 1, "drill": {"query": "admin.users", "input": {}, "sum": None}}]}
    assert "drill" not in executor.visible_actions(await who("sale1"), page)["kpis"][0]
    assert "drill" in executor.visible_actions(await who("admin"), page)["kpis"][0]
