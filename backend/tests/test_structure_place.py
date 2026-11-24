"""Cơ cấu tổ chức không cấp cố định (ADR 0257): MỘT lệnh theo dòng xếp người vào đơn vị (tên hoặc đường dẫn, tạo nếu chưa có) + người quản lý trực tiếp;
màn Cơ cấu đội tính ra cấp, số người và gợi ý trưởng — máy không tự đặt trưởng."""

import uuid
from typing import Any

from barista.kernel import imports
from tests.conftest import Who, approved, fresh_sale, ok, owner_val, q


def _tag() -> str:
    return uuid.uuid4().hex[:6].upper()


async def _team(code_or_name: str) -> Any:
    return await owner_val("""SELECT json_build_object('code', t.code, 'kind', t.kind, 'parent', p.name) FROM core.teams t
                              LEFT JOIN core.teams p ON p.id = t.parent_id WHERE t.name = %s""", (code_or_name,))


async def _home(user_id: Any) -> str:
    return await owner_val("SELECT t.name FROM core.team_members tm JOIN core.teams t ON t.id = tm.team_id WHERE tm.user_id = %s AND tm.is_home", (user_id,))


async def _row(admin: Any, name: str) -> dict[str, Any]:
    return next(r for r in (await q(admin, "hr.org"))["rows"] if r["name"] == name)


async def test_one_command_builds_any_shape_of_tree_from_a_path(who: Who) -> None:
    admin, t = await who("admin"), _tag()
    a, b, c = await fresh_sale(who), await fresh_sale(who), await fresh_sale(who)
    # công ty chỉ có đội: ghi mỗi tên đội
    out = await ok(admin, "hr.place", email=a.email, unit=f"Team Sao Mai {t}")
    assert out["units_created"] == [f"Team Sao Mai {t}"] and await _home(a.user_id) == f"Team Sao Mai {t}"
    assert (await _team(f"Team Sao Mai {t}"))["parent"] is None
    # khu vực → đội (không có khối), và một nhánh khác sâu hơn trong cùng công ty
    await ok(admin, "hr.place", email=b.email, unit=f"Miền Bắc {t} / Team A {t}")
    await ok(admin, "hr.place", email=c.email, unit=f"Miền Nam {t} > Khối 2 {t} > Chi nhánh Q1 {t} > Team B {t}")
    assert (await _team(f"Team A {t}"))["parent"] == f"Miền Bắc {t}" and (await _team(f"Team B {t}"))["parent"] == f"Chi nhánh Q1 {t}"
    assert (await _team(f"Chi nhánh Q1 {t}"))["kind"] == "branch" and (await _team(f"Team B {t}"))["kind"] == "sales"   # loại đoán từ tên, không phải cấp
    levels = {r["name"]: r["level"] for r in (await q(admin, "hr.org"))["rows"]}
    assert (levels[f"Team Sao Mai {t}"], levels[f"Team A {t}"], levels[f"Team B {t}"]) == (1, 2, 4)
    assert (await _row(admin, f"Miền Nam {t}"))["people"] == 1 and (await _row(admin, f"Team B {t}"))["people"] == 1   # số người cộng dồn cây con
    # chạy lại đúng dòng đó: không tạo thêm, không đổi gì
    again = await ok(admin, "hr.place", email=c.email, unit=f"Miền Nam {t} / Khối 2 {t} / Chi nhánh Q1 {t} / Team B {t}")
    assert again["units_created"] == [] and again["moved"] is False
    assert await owner_val("SELECT count(*) FROM core.teams WHERE name = %s", (f"Team B {t}",)) == 1


async def test_a_whole_file_goes_through_the_shared_import_door(who: Who) -> None:
    admin, t = await who("admin"), _tag()
    boss, x, y = await fresh_sale(who), await fresh_sale(who), await fresh_sale(who)
    rows = [{"Email": boss.email, "Đơn vị": f"Chi nhánh Hà Nội {t}", "Quản lý trực tiếp": ""},
            {"Email": x.email, "Đơn vị": f"Chi nhánh Hà Nội {t}", "Quản lý trực tiếp": boss.email},
            {"Email": y.email, "Đơn vị": f"Chi nhánh Hà Nội {t}", "Quản lý trực tiếp": boss.email}]
    dry = await imports.run_import(admin, "hr.place", rows, dry_run=True)
    assert dry.ok, dry.error
    assert await owner_val("SELECT count(*) FROM core.teams WHERE name = %s", (f"Chi nhánh Hà Nội {t}",)) == 0      # chạy thử không ghi gì
    assert (await imports.run_import(admin, "hr.place", rows, dry_run=False)).ok
    assert (await imports.run_import(admin, "hr.place", rows, dry_run=False)).ok                                      # nhập lại cùng file
    assert await owner_val("SELECT count(*) FROM core.teams WHERE name = %s", (f"Chi nhánh Hà Nội {t}",)) == 1
    assert await _home(x.user_id) == f"Chi nhánh Hà Nội {t}"
    # một đỉnh tuyến báo cáo trong đội đang khuyết trưởng ⇒ gợi ý đúng người đó, kèm email cho nút "Ghi trưởng đội"; máy KHÔNG tự đặt
    row = await _row(admin, f"Chi nhánh Hà Nội {t}")
    assert row["vacant"] == "yes" and row["suggest_email"] == boss.email and "2 người bên dưới" in row["suggest"]
    assert await owner_val("""SELECT count(*) FROM core.team_members tm JOIN core.teams tt ON tt.id = tm.team_id WHERE tt.name = %s AND tm.is_lead""",
                           (f"Chi nhánh Hà Nội {t}",)) == 0
    await approved(admin, "admin.set_team_lead", team_code=row["code"], email=row["suggest_email"])
    row = await _row(admin, f"Chi nhánh Hà Nội {t}")
    assert row["vacant"] == "no" and row["suggest"] == ""


async def test_two_tops_are_listed_for_a_person_to_choose_never_picked(who: Who) -> None:
    admin, t = await who("admin"), _tag()
    top1, top2, p1, p2, p3 = [await fresh_sale(who) for _ in range(5)]
    unit = f"Chi nhánh Đà Nẵng {t}"
    for person, manager in ((top1, ""), (top2, ""), (p1, top1.email), (p2, top1.email), (p3, top2.email)):
        await ok(admin, "hr.place", email=person.email, unit=unit, manager=manager)
    row = await _row(admin, unit)
    assert row["suggest_email"] == "" and row["suggest"].startswith("Cần chọn: ") and "(2)" in row["suggest"] and "(1)" in row["suggest"]
    assert row["people"] == 5 and row["vacant"] == "yes"


async def test_a_row_that_names_the_unit_head_sets_the_lead_through_the_lead_command(who: Who) -> None:
    """Nguồn đã ghi rõ ai đứng đầu đơn vị (danh sách nhân sự, sơ đồ) ⇒ dòng mang cờ trưởng; đặt trưởng vẫn là đổi quyền: đi đúng đường duyệt của lệnh ghi trưởng."""
    admin, t = await who("admin"), _tag()
    head, member = await fresh_sale(who), await fresh_sale(who)
    unit = f"Chi nhánh Huế {t} / Phòng KD 1 {t}"
    await approved(admin, "hr.place", email=head.email, unit=unit, lead=True)
    await ok(admin, "hr.place", email=member.email, unit=unit, manager=head.email)
    row = await _row(admin, f"Phòng KD 1 {t}")
    assert row["vacant"] == "no" and row["people"] == 2 and row["level"] == 2
    assert await owner_val("""SELECT tm.user_id FROM core.team_members tm JOIN core.teams tt ON tt.id = tm.team_id WHERE tt.name = %s AND tm.is_lead""",
                           (f"Phòng KD 1 {t}",)) == head.user_id
    again = await ok(admin, "hr.place", email=head.email, unit=unit, lead=True)        # đã là trưởng: chạy lại không xin duyệt lần nữa, không đổi gì
    assert again["lead_set"] is False and again["moved"] is False
