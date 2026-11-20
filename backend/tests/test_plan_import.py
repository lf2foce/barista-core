"""Nhập KẾ HOẠCH từ Excel (0222 §15/11) qua màn nhập tự khớp (0226): file kế hoạch lộn xộn (STT / WBS, hạng mục, số ngày, người phụ trách viết tắt, việc trước kiểu MS Project,
dòng nhóm) → kế hoạch có giai đoạn / phụ thuộc / ngày tính được; nhập lại file đã sửa cập nhật đúng việc, không xoá.

Phần THUẦN (``flow/plan_logic``): cấp bậc, số ngày, việc trước + độ trễ, ngày tính theo lịch, vòng, khớp việc đã có. Phần CÓ DB: ``imports.analyze`` / ``run_plan`` trên lệnh
``flow.import_plan`` (nhập cả bảng làm một lần gọi) — chạy thử không ghi gì, nhập thật qua đúng đường lệnh."""

import itertools
from datetime import date, timedelta
from typing import Any

import pytest

from barista.db import db
from barista.kernel import import_match as im
from barista.kernel import imports
from barista.kernel.clock import today
from barista.modules.flow.plan_logic import (
    Row,
    WorkDays,
    build,
    match_existing,
    parse_duration,
    parse_percent,
)
from tests.conftest import Who, q
from tests.plan_samples import ms_project_rows, revised_plan_rows, sample_plan_rows
from tests.plan_samples import xlsx as _xlsx

# ── hàm thuần ──

WORK = WorkDays(lambda d: d.weekday() >= 5)   # thứ Bảy, Chủ nhật nghỉ
MON = date(2026, 11, 16)


def _built(rows: list[Row]) -> Any:
    return build(rows, WORK)


def test_wbs_hierarchy_makes_phases_and_groups_are_not_tasks() -> None:
    b = _built([Row(2, "Chuẩn bị", code="1"), Row(3, "Chốt ngân sách", code="1.1", start="2026-11-16", duration="3"),
                Row(4, "Thiết kế", code="1.2", duration="5 ngày", after="1.1"), Row(5, "Triển khai", code="2"), Row(6, "In ấn", code="2.1", duration="2", after="1.2"),
                Row(7, "Báo cáo", code="2.1.1", duration="1")])
    assert [g.title for g in b.groups] == ["Chuẩn bị", "Triển khai", "In ấn"] or {g.title for g in b.groups} >= {"Chuẩn bị", "Triển khai"}
    by = {t.title: t for t in b.tasks}
    assert by["Chốt ngân sách"].phase == "Chuẩn bị" and by["Báo cáo"].phase == "Triển khai › In ấn"
    assert "Chuẩn bị" not in by                                                    # dòng nhóm không thành việc


def test_roman_and_heading_rows_become_phases() -> None:
    b = _built([Row(2, "Chuẩn bị", code="I"), Row(3, "A", code="1", start="2026-11-16", due="2026-11-18"), Row(4, "B", code="2", start="2026-11-19", due="2026-11-20"),
                Row(5, "Chạy", code="II"), Row(6, "C", code="3", start="2026-11-23", due="2026-11-25")])
    assert {t.title: t.phase for t in b.tasks} == {"A": "Chuẩn bị", "B": "Chuẩn bị", "C": "Chạy"}
    b = _built([Row(2, "Giai đoạn một"), Row(3, "X", start="2026-11-16", due="2026-11-17"), Row(4, "Giai đoạn hai"), Row(5, "Y", duration="2", start="2026-11-18")])
    assert {t.title: t.phase for t in b.tasks} == {"X": "Giai đoạn một", "Y": "Giai đoạn hai"}
    flat = _built([Row(2, "Việc a"), Row(3, "Việc b")])                            # toàn dòng chỉ có tên = danh sách việc, không có nhóm nào
    assert not flat.groups and len(flat.tasks) == 2


def test_durations_in_days_weeks_hours() -> None:
    assert parse_duration("5") == (5, "") and parse_duration("5 ngày") == (5, "") and parse_duration("2 tuần") == (10, "") and parse_duration("2 wks") == (10, "")
    assert parse_duration("12h") == (2, "") and parse_duration("0 days") == (0, "") and parse_duration("1,5") == (2, "") and parse_duration("5 days?") == (5, "")
    assert parse_duration("")[0] is None and parse_duration("abc")[0] is None and "chưa hiểu" in parse_duration("abc")[1]


def test_percent_fraction_vs_percent() -> None:
    assert parse_percent(["0.5", "1", "0"]) == [50, 100, 0]            # ô định dạng % của Excel lưu 0,5
    assert parse_percent(["50%", "100", "25"]) == [50, 100, 25]
    assert parse_percent(["", "x"]) == [None, None]


def test_predecessors_ms_project_style_with_lag_and_dates_computed_on_workdays() -> None:
    b = _built([Row(2, "Móng", code="1", row_id="1", start="2026-11-16", duration="3"),
                Row(3, "Tường", code="2", row_id="2", duration="2", after="1FS+1d"),
                Row(4, "Mái", code="3", row_id="3", duration="1", after="2;1SS"),
                Row(5, "Hoàn thiện", code="4", row_id="4", after="Mái FF"),
                Row(6, "Bàn giao", code="5", row_id="5", after="99")])
    by = {t.title: t for t in b.tasks}
    assert by["Móng"].due == date(2026, 11, 18) and not by["Móng"].computed - {"due"}
    assert by["Tường"].start == date(2026, 11, 20) and by["Tường"].due == date(2026, 11, 23)   # Móng xong T4 18/11 → +1 ngày làm việc → 19, lag 1 → 20 (T6); 2 ngày: T6, T2 (qua cuối tuần)
    assert by["Mái"].start == date(2026, 11, 24) and {d.kind for d in by["Mái"].deps} == {"FS", "SS"}
    assert any("không tìm thấy" in w for w in by["Bàn giao"].warnings)
    assert by["Bàn giao"].deps == []


def test_unsupported_dependency_kinds_are_noted_not_computed() -> None:
    b = _built([Row(2, "A", code="1", start="2026-11-16", due="2026-11-17"), Row(3, "B", code="2", after="1FF")])
    t = {x.title: x for x in b.tasks}["B"]
    assert t.deps and t.deps[0].kind == "FF" and any("FF" in w for w in t.warnings) and t.start is None


def test_cycle_lists_the_rows() -> None:
    b = _built([Row(2, "A", row_id="1", after="3"), Row(3, "B", row_id="2", after="1"), Row(4, "C", row_id="3", after="2")])
    assert "vòng" in b.blocking and "dòng 2" in b.blocking and "dòng 4" in b.blocking
    assert "vòng" in _built([Row(2, "Tự chờ", row_id="1", after="2"), Row(3, "Kia", row_id="2", after="1")]).blocking


def test_summary_row_predecessor_expands_to_its_leaves() -> None:
    b = _built([Row(2, "G1", code="1"), Row(3, "a", code="1.1", start="2026-11-16", due="2026-11-17"), Row(4, "b", code="1.2", start="2026-11-18", due="2026-11-19"),
                Row(5, "G2", code="2", after="1"), Row(6, "c", code="2.1", duration="1")])
    c = {t.title: t for t in b.tasks}["c"]
    assert {d.target.title for d in c.deps} == {"a", "b"} and c.start == date(2026, 11, 20)


def test_unreadable_date_and_start_after_due_are_reported_per_row() -> None:
    b = _built([Row(2, "X", start="sớm", due="2026-11-20"), Row(3, "Y", start="2026-11-21", due="2026-11-20")])
    x, y = b.tasks
    assert any("không đọc được" in w for w in x.warnings) and x.start is None and not x.error
    assert "sau hạn" in y.error


def test_matching_existing_by_code_title_then_title_then_wbs_rename() -> None:
    rows = [Row(2, "Chốt ngân sách", code="1.1"), Row(3, "Thiết kế mới tên", code="1.2"), Row(4, "In ấn", code="9"), Row(5, "Việc lạ", code="1.5")]
    b = _built(rows)
    ex = [{"id": 1, "title": "Chốt ngân sách", "plan_ref": "1.1"}, {"id": 2, "title": "Thiết kế", "plan_ref": "1.2"}, {"id": 3, "title": "In ấn", "plan_ref": "2.1"},
          {"id": 4, "title": "Đã bỏ", "plan_ref": "3"}]
    got, missing = match_existing(b.tasks, ex)
    by = {t.title: got[id(t)]["id"] for t in b.tasks if id(t) in got}
    assert by == {"Chốt ngân sách": 1, "Thiết kế mới tên": 2, "In ấn": 3}          # lượt 3: cùng mã WBS 1.2, tên đổi
    assert [e["id"] for e in missing] == [4]


def test_weekday_prefixed_dates_read() -> None:
    assert im.unweekday("Mon 01/12/26") == "01/12/26" and im.unweekday("Thứ Hai, 01/12/2026") == "01/12/2026" and im.unweekday("T2 01/12/2026") == "01/12/2026"
    assert im._day("Thứ Ba 17/11/2026", "dmy") == "2026-11-17" and "date" in im.sniff("Mon 01/12/26")


def test_people_options_match_name_without_accents_initials_and_first_listed() -> None:
    idx = im.OptionIndex([("huy@x.vn", "Đỗ Quang Huy · MKT · 2 việc mở"), ("hanh@x.vn", "Võ Thị Hạnh · MKT"), ("an@x.vn", "Nguyễn Văn An"), ("an2@x.vn", "Lê Hoài An")])
    assert idx.resolve("Huy")[0] == "huy@x.vn" and idx.resolve("chị Hạnh")[0] == "hanh@x.vn" and idx.resolve("vo thi hanh")[0] == "hanh@x.vn"
    assert idx.resolve("NVA")[0] == "an@x.vn" and idx.resolve("DQH")[0] == "huy@x.vn" and idx.resolve("Huy, Hạnh")[0] == "huy@x.vn"
    assert idx.resolve("An")[0] is None                                          # hai người tên An ⇒ không đoán, để người chọn
    assert idx.resolve("Hanh (PM)")[0] == "hanh@x.vn" and idx.resolve("hanh@x.vn")[0] == "hanh@x.vn"


# ── nhập qua màn tự khớp (DB) ──

_seq = itertools.count(1)


async def _analyze(p: Any, data: bytes) -> dict[str, Any]:
    r = await imports.analyze(p, "flow.import_plan", imports.read_all(data), file_name="ke-hoach-ra-mat.xlsx")
    assert r.status == "ok", r.error
    return r.output


def _plan_json(a: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"sheet": a["sheet"], "header_line": a["header_line"], "mapping": {m["field"]: m["column"] for m in a["mapping"]}, **extra}


async def _tasks(plan_id: Any) -> dict[str, dict[str, Any]]:
    async with db.owner.connection() as conn:
        cur = await conn.execute("""SELECT t.*, u.email FROM flow.tasks t LEFT JOIN core.users u ON u.id = t.assignee_id WHERE plan_id = %s""", (plan_id,))
        return {r["title"]: r for r in await cur.fetchall()}


async def test_messy_plan_maps_previews_and_imports(who: Who) -> None:
    lead = await who("mkt")
    t0 = today() + timedelta(days=(7 - today().weekday()) % 7 or 7)   # một thứ Hai sắp tới
    data = _xlsx(sample_plan_rows(t0))
    a = await _analyze(lead, data)
    got = {m["field"]: a["columns"][m["column"]]["header"] for m in a["mapping"]}
    assert got["code"] == "STT" and got["title"] == "Hạng mục / Công việc" and got["assignee_email"] == "Người phụ trách" and got["start_on"] == "Bắt đầu"
    assert got["due_on"] == "Kết thúc" and got["duration"] == "Số ngày" and got["after"] == "Việc trước" and got["percent"] == "% hoàn thành" and got["notes"] == "Ghi chú"
    assert a["heading"] == "KẾ HOẠCH RA MẮT CHIẾN DỊCH THÁNG 12" and a["table"] == {"preset": ["plan_id", "plan_title"]}
    assert a["unmapped_required"] == []

    plan = _plan_json(a, preset={"plan_title": a["heading"]})
    dry = await imports.run_plan(lead, "flow.import_plan", imports.read_all(data), plan, dry_run=True, file_name="ke-hoach-ra-mat.xlsx")
    assert dry.status == "ok", dry.error
    out = dry.output
    assert not out["unresolved"] and out["summary"]["create"] == 6 and out["summary"]["failed"] == 0 and out["summary"]["update"] == 0
    view = out["plan_view"]
    assert view["target"]["new"] and view["target"]["title"] == a["heading"] and {p["title"] for p in view["phases"]} == {"Chuẩn bị", "Triển khai"}
    by = {t["title"]: t for t in view["tasks"]}
    assert by["Dựng landing page"]["after"] == [by["Thiết kế key visual"]["line"], by["Viết nội dung bài"]["line"]] and "start" in by["Dựng landing page"]["computed"]
    assert by["Nghiệm thu"]["end"] >= by["Chạy quảng cáo"]["end"] and by["Chốt ngân sách"]["who"].startswith("mkt@") and by["Dựng landing page"]["who"].startswith("mkt3")
    assert any("lịch làm việc" in n for n in out["notes"])
    tl = view["timeline"]                                                         # bản xem trước vẽ bằng đúng khối Gantt của thẻ kế hoạch
    assert tl["type"] == "timeline" and [g["title"] for g in tl["groups"]] == ["Chuẩn bị", "Triển khai"]
    items = {i["title"].split(" ", 1)[1]: i for g in tl["groups"] for i in g["items"]}
    assert items["Nghiệm thu"]["kind"] == "milestone" and items["Dựng landing page"]["after"] and items["Chốt ngân sách"]["status"] == "Thêm mới"
    assert await q(lead, "flow.plans") is not None
    n_plans = await _count_plans(a["heading"])
    assert n_plans == 0                                                         # chạy thử không ghi gì

    real = await imports.run_plan(lead, "flow.import_plan", imports.read_all(data), plan, dry_run=False, file_name="ke-hoach-ra-mat.xlsx", file_sha256="b" * 64)
    assert real.status == "ok", real.error
    pid = await _plan_id(a["heading"])
    tasks = await _tasks(pid)
    assert set(tasks) == {"Chốt ngân sách", "Thiết kế key visual", "Viết nội dung bài", "Dựng landing page", "Chạy quảng cáo", "Nghiệm thu"}   # nhóm không thành việc
    assert tasks["Chốt ngân sách"]["status"] == "done" and tasks["Thiết kế key visual"]["status"] == "doing" and tasks["Thiết kế key visual"]["phase"] == "Chuẩn bị"
    assert tasks["Dựng landing page"]["depends_on"] == [tasks["Thiết kế key visual"]["id"], tasks["Viết nội dung bài"]["id"]]
    assert tasks["Thiết kế key visual"]["plan_ref"] == "1.2" and tasks["Thiết kế key visual"]["start_on"] is not None
    assert tasks["Nghiệm thu"]["start_on"] == tasks["Nghiệm thu"]["due_on"]      # mốc 0 ngày
    assert tasks["Chốt ngân sách"]["email"].startswith("mkt@") and tasks["Viết nội dung bài"]["email"].startswith("mkt3")
    assert all(t["start_on"] is None or t["due_on"] is None or t["start_on"] <= t["due_on"] for t in tasks.values())
    # Gantt: khối timeline đọc được kế hoạch vừa nhập
    page = await q(lead, "flow.plan", plan_id=str(pid))
    assert "timeline" in str(page)


async def _count_plans(title: str) -> int:
    async with db.owner.connection() as conn:
        return int(next(iter((await (await conn.execute("SELECT count(*) FROM flow.plans WHERE title = %s", (title,))).fetchone() or {"c": 0}).values())))


async def _plan_id(title: str) -> Any:
    async with db.owner.connection() as conn:
        row = await (await conn.execute("SELECT id FROM flow.plans WHERE title = %s ORDER BY created_at DESC LIMIT 1", (title,))).fetchone()
    assert row is not None
    return row["id"]


async def test_reimport_updates_dates_adds_new_and_never_deletes(who: Who) -> None:
    lead = await who("mkt")
    t0 = today() + timedelta(days=(7 - today().weekday()) % 7 or 7)
    rows = sample_plan_rows(t0)
    title = f"Kế hoạch nhập lại {next(_seq)}"
    rows[0][0] = title
    data = _xlsx(rows)
    a = await _analyze(lead, data)
    plan = _plan_json(a, preset={"plan_title": title})
    assert (await imports.run_plan(lead, "flow.import_plan", imports.read_all(data), plan, dry_run=False, file_name="k.xlsx")).status == "ok"
    pid = await _plan_id(title)
    before = await _tasks(pid)

    # bản sửa: dời "Chốt ngân sách" + "Viết nội dung bài", đổi tên "Dựng landing page", thêm việc mới 2.4, bỏ "Chạy quảng cáo" khỏi file
    new = sample_plan_rows(t0)
    new[0][0] = title
    new[5][3], new[5][4] = t0 + timedelta(days=2), t0 + timedelta(days=5)          # "Chốt ngân sách" đã xong (100%) ⇒ giữ nguyên
    new[6][3], new[6][4] = t0 + timedelta(days=4), t0 + timedelta(days=8)          # "Thiết kế key visual": thêm ngày rõ ràng
    new[7][3] = t0 + timedelta(days=6)
    new[9][1] = "Dựng landing page v2"
    del new[10]                                                                    # bỏ "Chạy quảng cáo" (2.2) — "Nghiệm thu" vẫn chờ nó trong file → việc trước không tìm thấy
    new.insert(10, ["2.4", "Báo cáo kết quả", "Hanh", None, None, "2", "2.1", None, None])
    data2 = _xlsx(new)
    a2 = await _analyze(lead, data2)
    assert a2["layout"] and a2["layout"]["exact"]                                  # bố cục đã nhớ
    plan2 = _plan_json(a2, preset={"plan_id": str(pid)})
    dry = (await imports.run_plan(lead, "flow.import_plan", imports.read_all(data2), plan2, dry_run=True, file_name="k2.xlsx")).output
    s = dry["summary"]
    assert s["create"] == 1 and s["update"] >= 3 and s["failed"] == 0 and s["skip"] >= 1
    view = dry["plan_view"]
    assert not view["target"]["new"] and [m["title"] for m in view["missing"]] == ["Chạy quảng cáo"]
    assert "không còn trong file — giữ nguyên, không xoá" in view["summary"] and "1 việc mới" in view["summary"]
    assert "Hạn" in {d["field"] for r in dry["rows"] if r["action"] == "update" for d in r["diff"]}
    assert await _count_tasks(pid) == len(before)                                  # chạy thử không ghi

    assert (await imports.run_plan(lead, "flow.import_plan", imports.read_all(data2), plan2, dry_run=False, file_name="k2.xlsx")).status == "ok"
    after = await _tasks(pid)
    assert set(before) - set(after) == {"Dựng landing page"} and "Dựng landing page v2" in after and "Báo cáo kết quả" in after
    assert "Chạy quảng cáo" in after and after["Chạy quảng cáo"]["id"] == before["Chạy quảng cáo"]["id"]   # KHÔNG bị xoá, không đổi
    assert after["Dựng landing page v2"]["id"] == before["Dựng landing page"]["id"]                          # đổi tên cùng mã WBS = cùng việc
    assert after["Chốt ngân sách"]["due_on"] == before["Chốt ngân sách"]["due_on"]            # việc đã xong không bị đổi ngày
    assert after["Thiết kế key visual"]["due_on"] == t0 + timedelta(days=8) and after["Viết nội dung bài"]["start_on"] == t0 + timedelta(days=6)
    assert len(after) == len(before) + 1
    # nhập lại chính file này lần nữa: không còn gì đổi
    again = (await imports.run_plan(lead, "flow.import_plan", imports.read_all(data2), plan2, dry_run=True, file_name="k2.xlsx")).output
    assert again["summary"]["create"] == 0 and again["summary"]["update"] == 0 and again["summary"]["skip"] >= 5


async def _count_tasks(pid: Any) -> int:
    return len(await _tasks(pid))


async def test_cycle_in_file_is_a_clear_error_and_nothing_is_written(who: Who) -> None:
    lead = await who("mkt")
    title = f"Kế hoạch vòng {next(_seq)}"
    rows = [[title], ["ID", "Tên công việc", "Số ngày", "Việc trước"], [1, "A", 2, "3"], [2, "B", 2, "1"], [3, "C", 2, "2"]]
    data = _xlsx(rows)
    a = await _analyze(lead, data)
    r = await imports.run_plan(lead, "flow.import_plan", imports.read_all(data), _plan_json(a, preset={"plan_title": title}), dry_run=True)
    assert r.status == "invalid" and "vòng" in r.error and "dòng 3" in r.error and "dòng 5" in r.error
    assert await _count_plans(title) == 0


async def test_unknown_or_out_of_scope_names_wait_for_one_pick_and_unassigned_is_fine(who: Who) -> None:
    lead = await who("mkt")                          # trưởng phòng: giao được cho phòng mình, không giao được cho kinh doanh
    sale = await who("sale1")
    title = f"Kế hoạch phạm vi {next(_seq)}"
    d9 = today() + timedelta(days=9)
    rows = [[title], ["STT", "Công việc", "Người phụ trách", "Hạn"], ["1", "Việc của Hạnh", "Võ Thị Hạnh", d9], ["2", "Việc của kinh doanh", "Nguyễn Văn An", d9],
            ["3", "Việc không ai", None, d9], ["4", "Việc lạ", "Zorro", d9], ["5", "Việc nữa của Zorro", "Zorro", d9]]
    data = _xlsx(rows)
    a = await _analyze(lead, data)
    plan = _plan_json(a, preset={"plan_title": title})
    res = await imports.run_plan(lead, "flow.import_plan", imports.read_all(data), plan, dry_run=True)
    assert res.status == "ok", (res.status, res.error)
    pend = {v["raw"]: v["count"] for u in res.output["unresolved"] for v in u["values"]}
    assert pend == {"Nguyễn Văn An": 1, "Zorro": 2}                               # chọn MỘT lần cho mỗi tên, áp mọi dòng
    assert (await imports.run_plan(lead, "flow.import_plan", imports.read_all(data), plan, dry_run=False)).status == "invalid"   # chưa chọn xong thì chưa nhập
    picked = {**plan, "resolutions": {"assignee_email": {"zorro": "", "nguyenvanan": ""}}}
    real = await imports.run_plan(lead, "flow.import_plan", imports.read_all(data), picked, dry_run=False)
    assert real.status == "ok", real.error
    tasks = await _tasks(await _plan_id(title))
    assert len(tasks) == 5 and tasks["Việc của Hạnh"]["email"].startswith("mkt2")
    assert tasks["Việc lạ"]["team_id"] is not None or tasks["Việc lạ"]["assignee_id"] is not None   # chưa giao: việc của đội / người nhập giữ
    # đường lệnh trực tiếp (không qua danh sách chọn): người ngoài phạm vi bị từ chối ĐÚNG dòng đó, các dòng khác vẫn vào
    out = await executor_run(lead, {"plan_title": title + " c", "tasks": [{"line": 2, "title": "A", "assignee_email": sale.email}, {"line": 3, "title": "B"}]})
    bad = {x["line"]: x for x in out["import"]["rows"]}
    assert bad[2]["status"] == "invalid" and "ngoài phạm vi" in bad[2]["error"] and bad[3]["status"] == "ok"


async def executor_run(p: Any, inp: dict[str, Any]) -> Any:
    from barista.kernel import executor
    r = await executor.run(p, "flow.import_plan", inp)
    return r.output if r.status == "ok" else None


async def test_old_row_by_row_path_refuses_a_table_command(who: Who) -> None:
    lead = await who("mkt")
    r = await imports.run_import(lead, "flow.import_plan", [{"Công việc": "x"}], dry_run=True)
    assert r.status == "invalid" and "Nhập từ Excel" in r.error


@pytest.mark.parametrize("raw", ["Mon 01/12/26", "Thứ Hai, 01/12/2026"])
def test_dates_with_weekday_in_a_column_are_one_date_order(raw: str) -> None:
    out, _ = im.convert_dates({"d": im.Field("d", "Ngày", "date", False, ["ngay"], {})}, [{"d": raw}], "VN", __import__("zoneinfo").ZoneInfo("Asia/Ho_Chi_Minh"))
    assert out[0]["d"] == "2026-12-01"


async def test_ms_project_export_maps_and_resolves_id_predecessors_with_lag(who: Who) -> None:
    lead = await who("mkt")
    t0 = today() + timedelta(days=(7 - today().weekday()) % 7 or 7)
    data = _xlsx(ms_project_rows(t0), "Task_Table")
    a = await _analyze(lead, data)
    got = {m["field"]: a["columns"][m["column"]]["header"] for m in a["mapping"]}
    assert got["row_id"] == "ID" and got["code"] == "WBS" and got["title"] == "Task Name" and got["duration"] == "Duration" and got["after"] == "Predecessors"
    assert got["start_on"] == "Start" and got["due_on"] == "Finish" and got["assignee_email"] == "Resource Names" and got["percent"] == "% Complete"
    r = await imports.run_plan(lead, "flow.import_plan", imports.read_all(data), _plan_json(a, preset={"plan_title": "MS Project thử"}), dry_run=True)
    assert r.status == "ok", r.error
    view = r.output["plan_view"]
    by = {t["title"]: t for t in view["tasks"]}
    assert set(by) == {"Phỏng vấn các phòng", "Tổng hợp yêu cầu", "Thiết kế dữ liệu", "Thiết kế màn hình", "Bàn giao"}      # dòng tổng (1, 2) là giai đoạn, không phải việc
    assert by["Thiết kế dữ liệu"]["phase"] == "Thiết kế giải pháp" and by["Tổng hợp yêu cầu"]["after"] == [by["Phỏng vấn các phòng"]["line"]]
    assert by["Thiết kế màn hình"]["after"] == [by["Thiết kế dữ liệu"]["line"]] and "start" in by["Thiết kế màn hình"]["computed"]   # 5SS+2 days
    assert by["Bàn giao"]["after"] and by["Bàn giao"]["start"] and by["Bàn giao"]["end"] == by["Bàn giao"]["start"]            # 0 days = mốc
    assert by["Phỏng vấn các phòng"]["start"] == (t0).isoformat()                                                              # "Mon 16/11/26" đọc đúng ngày


async def test_revised_sample_is_a_stable_fixture(who: Who) -> None:
    t0 = today()
    assert revised_plan_rows(t0, "X")[0][0] == "X" and any(r[1] == "Báo cáo kết quả" for r in revised_plan_rows(t0)[4:])


async def test_importing_the_same_file_twice_never_makes_two_plans_nor_replays_a_stale_result(who: Who) -> None:
    lead = await who("mkt")
    t0 = today() + timedelta(days=(7 - today().weekday()) % 7 or 7)
    title = f"Kế hoạch bấm đúp {next(_seq)}"
    data = _xlsx(revised_plan_rows(t0, title))
    a = await _analyze(lead, data)
    plan = _plan_json(a, preset={"plan_title": title})
    first = await imports.run_plan(lead, "flow.import_plan", imports.read_all(data), plan, dry_run=False)
    second = await imports.run_plan(lead, "flow.import_plan", imports.read_all(data), plan, dry_run=False)
    assert first.output["summary"]["create"] == 6 and second.output["summary"]["create"] == 0 and second.output["summary"]["update"] == 0
    assert await _count_plans(title) == 1
    # xoá hết việc rồi nhập lại cùng file ⇒ phải chạy thật (không phát lại kết quả cũ)
    pid = await _plan_id(title)
    async with db.owner.connection() as conn:
        await conn.execute("DELETE FROM flow.tasks WHERE plan_id = %s", (pid,))
    third = await imports.run_plan(lead, "flow.import_plan", imports.read_all(data), plan, dry_run=False)
    assert third.output["summary"]["create"] == 6 and len(await _tasks(pid)) == 6
