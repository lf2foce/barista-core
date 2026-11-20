"""Ô chọn có tìm (1.61): cửa ``/v1/options`` nhận ``q`` / ``limit`` cho MỌI truy vấn chọn (truy vấn không khai thì cửa lọc / cắt thay, truy vấn khai thì tự lọc),
dòng chọn mang ``hint``, ô phụ thuộc khai ``options_args`` đổi tên tham số. Truy vấn chọn cũ không kèm gì ⇒ danh sách đầy như trước."""

from httpx import ASGITransport, AsyncClient

from barista.http_api import app
from barista.kernel import registry, ui
from barista.kernel.fields import Input, OptionsIn, field
from tests.conftest import Who

ROWS = [{"value": "a@x.vn", "label": "Nguyễn Thị Lan", "hint": "Trưởng nhóm"}, {"value": "b@x.vn", "label": "Lê Văn Lâm", "hint": "Sale"},
        {"value": "c@x.vn", "label": "Phạm Lan Anh", "hint": "Kế toán"}]


def test_narrow_options_matches_without_accents_by_every_word_and_puts_label_prefix_first() -> None:
    assert [o["value"] for o in ui.narrow_options(ROWS, "lan")] == ["a@x.vn", "c@x.vn"]          # nhãn bắt đầu bằng từ khoá xếp trước
    assert [o["value"] for o in ui.narrow_options(ROWS, "LAN anh")] == ["c@x.vn"]                # mọi từ phải có
    assert [o["value"] for o in ui.narrow_options(ROWS, "sale")] == ["b@x.vn"]                    # khớp cả dòng phụ
    assert [o["value"] for o in ui.narrow_options(ROWS, "pham")] == ["c@x.vn"]                    # bỏ dấu
    assert [o["value"] for o in ui.narrow_options(ROWS, "")] == ["a@x.vn", "b@x.vn", "c@x.vn"]   # không từ khoá = đủ
    assert len(ui.narrow_options(ROWS, "", 2)) == 2 and ui.narrow_options(ROWS, "zzz") == []
    assert ui.narrow_options({"kind": "card"}, "x") == {"kind": "card"}                         # không phải danh sách: giữ nguyên


def test_options_rows_carry_a_hint_only_when_there_is_one() -> None:
    rows = [{"v": 1, "l": "A", "h": "phụ"}, {"v": 2, "l": "B", "h": None}]
    assert ui.options(rows, "v", "l", hint="h") == [{"value": "1", "label": "A", "hint": "phụ"}, {"value": "2", "label": "B"}]
    assert ui.options(rows, "v", "l") == [{"value": "1", "label": "A"}, {"value": "2", "label": "B"}]   # mặc định cũ


def test_options_args_reaches_the_form_spec_and_options_in_has_q_and_limit() -> None:
    class F(Input):
        project: str = field("Dự án", options_query="x.projects")
        unit: str = field("Căn", options_query="x.units", options_args={"project_code": "project"})

    from barista.kernel.forms import _model_specs
    specs = {s["key"]: s for s in _model_specs(F)}
    assert specs["unit"]["options_args"] == {"project_code": "project"} and "options_args" not in specs["project"]
    assert (OptionsIn().q, OptionsIn().limit) == ("", 50)


async def test_options_door_narrows_a_plain_query_and_keeps_old_calls_whole(who: Who) -> None:
    truonga = await who("truonga")
    h = {"X-Dev-User": truonga.email, "X-Tenant-Id": str(truonga.tenant_id)}
    assert "admin.member_options" in registry.OPS
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        full = (await c.get("/v1/options/admin.member_options", headers=h)).json()["output"]
        assert len(full) >= 3
        one = full[1]["label"].split()[-1].split(" (")[0]
        hit = (await c.get(f"/v1/options/admin.member_options?q={one}&limit=2", headers=h)).json()["output"]
        assert 1 <= len(hit) <= 2 and all(o["value"] in {f["value"] for f in full} for o in hit)
        assert (await c.get("/v1/options/admin.member_options?q=zzzkhongco", headers=h)).json()["output"] == []
