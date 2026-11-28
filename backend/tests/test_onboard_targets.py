"""KHUNG CHUYỂN DỮ LIỆU — mã đích và phần đọc lại (ADR 0263, bổ sung sau diễn tập) trên dữ liệu GIẢ: lệnh không trả mã UUID khai ``target=`` hoặc
``keyless=True`` (sổ mã gốc ghi dòng không mã đích — không bao giờ "không trả mã" sau khi lệnh đã ghi); ``ids.need`` trên thực thể không mã là lỗi cấu hình
dừng lượt; ``lookup`` bổ sung đầu vào từ phía Barista mà không đổi dấu vân tay; một chỉ số đối chiếu lỗi chỉ làm hỏng chỉ số đó; ngoại lệ đọc đủ, lọc được."""

from typing import Any
from uuid import UUID

import pytest

from barista import seed
from barista.api import onboard as ob
from tests.conftest import owner_val, q
from tests.test_onboard_load import _names, company, people_entity

ROWS = [{"source_id": "1", "name": "Người Giả Một"}, {"source_id": "2", "name": "Người Giả Hai"}]


def _rename(rows: list[dict[str, Any]], **kw: Any) -> ob.Entity:
    """Lệnh HÀNH ĐỘNG trên bản ghi đã nạp (ở đây: sửa tên) — bản thân nó không sinh bản ghi mới."""
    def to_input(r: dict[str, Any], ids: ob.Ids) -> dict[str, Any]:
        return {"party_id": str(ids.need("person", r["source_id"])), "display_name": f"{r['name']} (đã rà)"}

    return ob.Entity("review", lambda: list(rows), "parties.update", to_input, label="Rà tên", **kw)


async def test_a_keyless_entity_is_recorded_without_a_target_and_never_reruns() -> None:
    tid, domain = await company()

    def after(r: dict[str, Any], ids: ob.Ids) -> dict[str, Any]:
        ids.require("review", r["source_id"])   # "đã rà chưa" — hỏi được trên thực thể không mã
        assert ids.has("review", r["source_id"]) and not ids.has("review", "khong-co")
        return {"display_name": f"Liên hệ của {r['name']}", "kind": "person"}

    conv = ob.Converter("hecu", (people_entity(ROWS), _rename(ROWS, keyless=True), ob.Entity("contact", lambda: list(ROWS), "parties.create", after, "party_id")))
    first = await ob.load(tid, conv, by_email=f"admin@{domain}")
    assert (first.status, first.created, first.exceptions) == ("done", 6, 0)
    assert first.entities["review"] == {"extracted": 2, "created": 2, "updated": 0, "skipped": 0, "exceptions": 0, "seconds": first.entities["review"]["seconds"]}
    assert await owner_val("SELECT count(*) FROM onboard.source_ids WHERE tenant_id = %s AND entity = 'review' AND target_id IS NULL", (tid,)) == 2
    assert [r["status"] for r in first.recon if r["metric"] == "count.review"] == ["khop"]
    again = await ob.load(tid, conv, by_email=f"admin@{domain}")
    assert (again.created, again.skipped, again.exceptions) == (0, 6, 0)
    assert "Người Giả Một (đã rà)" in await _names(tid)


async def test_asking_a_keyless_entity_for_an_id_is_a_configuration_error_that_stops_the_run() -> None:
    tid, domain = await company()

    def wrong(r: dict[str, Any], ids: ob.Ids) -> dict[str, Any]:
        return {"display_name": str(ids.need("review", r["source_id"])), "kind": "person"}

    conv = ob.Converter("hecu", (people_entity(ROWS), _rename(ROWS, keyless=True), ob.Entity("contact", lambda: list(ROWS), "parties.create", wrong, "party_id")))
    with pytest.raises(ob.ConverterError, match="keyless"):
        await ob.load(tid, conv, by_email=f"admin@{domain}")
    assert await owner_val("SELECT status FROM onboard.runs WHERE tenant_id = %s", (tid,)) == "failed"
    # tham chiếu một thực thể không có trong bộ chuyển (gõ sai tên) cũng dừng lượt thay vì thành hàng loạt "thiếu tham chiếu"
    typo = ob.Entity("contact", lambda: list(ROWS), "parties.create", lambda r, ids: {"display_name": str(ids.need("persn", "1")), "kind": "person"}, "party_id")
    with pytest.raises(ob.ConverterError, match="persn"):
        await ob.load(tid, ob.Converter("hecu", (people_entity(ROWS), typo)), by_email=f"admin@{domain}")


async def test_a_target_function_names_the_record_an_action_touched() -> None:
    tid, domain = await company()
    conv = ob.Converter("hecu", (people_entity(ROWS), _rename(ROWS, target=lambda out, inp, ids: UUID(inp["party_id"]))))
    r = await ob.load(tid, conv, by_email=f"admin@{domain}")
    assert (r.created, r.exceptions) == (4, 0)
    same = await owner_val("""SELECT count(*) FROM onboard.source_ids a JOIN onboard.source_ids b ON b.tenant_id = a.tenant_id AND b.source_id = a.source_id
                              AND b.entity = 'person' AND b.target_id = a.target_id WHERE a.tenant_id = %s AND a.entity = 'review'""", (tid,))
    assert same == 2
    # hàm trả None ⇒ dòng vẫn ghi (không mã); thực thể khác hỏi mã của dòng đó thì là "thiếu tham chiếu", không phải lỗi lặng
    tid, domain = await company()
    conv = ob.Converter("hecu", (people_entity(ROWS), _rename(ROWS, target=lambda out, inp, ids: None)))
    r = await ob.load(tid, conv, by_email=f"admin@{domain}")
    assert (r.created, r.exceptions) == (4, 0)
    assert await owner_val("SELECT count(*) FROM onboard.source_ids WHERE tenant_id = %s AND target_id IS NULL", (tid,)) == 2


async def test_a_wrong_id_key_stops_the_run_and_the_fixed_converter_creates_no_duplicate() -> None:
    """Lệnh ĐÃ ghi mà đầu ra không có mã như ``id_key`` khai ⇒ không phải ngoại lệ của dòng ("không trả mã") mà là bộ chuyển khai sai: dừng ngay ở dòng đầu.
    Sửa khai báo rồi chạy lại: khoá chống lặp trả kết quả cũ ⇒ không tạo bản thứ hai."""
    tid, domain = await company()
    bad = ob.Entity("person", lambda: list(ROWS), "parties.create", lambda r, ids: {"display_name": r["name"], "kind": "person"}, "khong_co")
    with pytest.raises(ob.ConverterError, match="khong_co"):
        await ob.load(tid, ob.Converter("hecu", (bad,)), by_email=f"admin@{domain}")
    assert await _names(tid) == ["Người Giả Một"]
    assert await owner_val("SELECT count(*) FROM onboard.exceptions WHERE tenant_id = %s", (tid,)) == 0
    good = ob.Entity("person", lambda: list(ROWS), "parties.create", lambda r, ids: {"display_name": r["name"], "kind": "person"}, "party_id")
    r = await ob.load(tid, ob.Converter("hecu", (good,)), by_email=f"admin@{domain}")
    assert (r.created, r.exceptions) == (2, 0)
    assert await _names(tid) == ["Người Giả Hai", "Người Giả Một"]


def test_an_entity_declares_exactly_one_way_to_get_its_target() -> None:
    from barista.kernel.onboard import _check

    def e(**kw: Any) -> ob.Entity:
        return ob.Entity("person", list, "parties.create", lambda r, ids: {}, **kw)

    for kw in ({}, {"id_key": "party_id", "keyless": True}, {"keyless": True, "target": lambda o, i, ids: None}):
        with pytest.raises(ValueError, match="ĐÚNG MỘT"):
            _check(ob.Converter("hecu", (e(**kw),)), {})
    with pytest.raises(ValueError, match="ke_toan"):
        _check(ob.Converter("hecu", (e(keyless=True, actor="ke_toan"),)), {})
    _check(ob.Converter("hecu", (e(keyless=True, actor="ke_toan"),)), {"ke_toan": "kt@vi-du.demo"})


async def test_lookup_reads_barista_before_the_command_without_touching_the_fingerprint() -> None:
    tid, domain = await company()
    await ob.load(tid, ob.Converter("hecu", (people_entity(ROWS),)), by_email=f"admin@{domain}")
    seen: list[str] = []

    async def lookup(rec: dict[str, Any], inp: dict[str, Any], reader: ob.Reader) -> dict[str, Any]:
        page = await reader.query("onboard.runs")   # một truy vấn bất kỳ phía Barista, bằng quyền người chạy thực thể
        seen.append(rec["source_id"])
        return {**inp, "display_name": f"{inp['display_name']} · lượt {len(page['rows'])}"}

    review = ob.Entity("review", lambda: list(ROWS), "parties.update",
                       lambda r, ids: {"party_id": str(ids.need("person", r["source_id"])), "display_name": r["name"]}, keyless=True, lookup=lookup)
    conv = ob.Converter("hecu", (people_entity(ROWS), review))
    r = await ob.load(tid, conv, by_email=f"admin@{domain}")
    assert (r.created, r.skipped) == (2, 2) and seen == ["1", "2"]
    assert await _names(tid) == ["Người Giả Hai · lượt 2", "Người Giả Một · lượt 2"]
    again = await ob.load(tid, conv, by_email=f"admin@{domain}")   # phía Barista đã đổi (thêm một lượt) nhưng nguồn giữ nguyên ⇒ giữ nguyên, không tra lại
    assert (again.created, again.updated, again.skipped) == (0, 0, 4) and seen == ["1", "2"]


async def test_one_failing_metric_is_marked_and_the_run_still_finishes() -> None:
    tid, domain = await company()
    admin = await seed.who(tid, "admin", domain)

    async def broken(reader: ob.Reader) -> float:
        raise RuntimeError("truy vấn đối chiếu hỏng (giả)")

    async def fine(reader: ob.Reader) -> float:
        return 2.0

    conv = ob.Converter("hecu", (people_entity(ROWS),), metrics=(ob.Metric("money.broken", "Chỉ số hỏng", lambda: 1.0, broken),
                                                                 ob.Metric("people.total", "Số người", lambda: 2.0, fine)))
    r = await ob.load(tid, conv, by_email=f"admin@{domain}")
    assert (r.status, r.created) == ("done", 2)
    assert {x["metric"]: x["status"] for x in r.recon} == {"count.person": "khop", "money.broken": "loi", "people.total": "khop"}
    assert [x["metric"] for x in r.mismatches] == ["money.broken"]   # chưa đối chiếu được ⇒ không coi là khớp
    assert await owner_val("SELECT note FROM onboard.recon WHERE run_id = %s AND status = 'loi'", (r.run_id,)) == "RuntimeError: truy vấn đối chiếu hỏng (giả)"
    runs = await q(admin, "onboard.runs")
    assert [(x["status"], x["mismatches"], x["signed"]) for x in runs["rows"]] == [("done", 1, "waiting")]
    page = await q(admin, "onboard.run", run_id=str(r.run_id))
    table = next(b for b in page["blocks"] if b.get("type") == "table" and "Đối chiếu" in b["page"]["title"])
    assert [(x["status"], x["note"]) for x in table["page"]["rows"] if x["status"] == "loi"] == [("loi", "RuntimeError: truy vấn đối chiếu hỏng (giả)")]


async def test_exceptions_are_counted_in_full_and_filter_by_reason_and_entity() -> None:
    tid, domain = await company()
    admin = await seed.who(tid, "admin", domain)
    rows = [{"source_id": str(i), "name": "  "} for i in range(7)] + [{"source_id": "ok", "name": "Người Giả"}]
    contacts = [{"source_id": f"c{i}", "person": "99"} for i in range(3)]
    contact = ob.Entity("contact", lambda: contacts, "parties.create",
                        lambda r, ids: {"display_name": str(ids.need("person", r["person"])), "kind": "person"}, "party_id")
    r = await ob.load(tid, ob.Converter("hecu", (people_entity(rows), contact)), by_email=f"admin@{domain}")
    assert r.exceptions == 10
    assert r.reasons == [{"entity": "person", "reason": "bad_source", "n": 7}, {"entity": "contact", "reason": "missing_ref", "n": 3}]
    page = await q(admin, "onboard.exceptions", run_id=str(r.run_id))
    assert page["total"] == 10 and len(page["rows"]) == 10
    only = await q(admin, "onboard.exceptions", run_id=str(r.run_id), reason="missing_ref")
    assert only["total"] == 3 and {x["entity"] for x in only["rows"]} == {"contact"}
    assert (await q(admin, "onboard.exceptions", run_id=str(r.run_id), entity="person", reason="missing_ref"))["total"] == 0
    # bên gọi xin trang ⇒ đúng một trang + TỔNG của cả tập lọc (không cắt lặng lẽ)
    from barista.kernel import executor, ui

    part = (await executor.query(admin, "onboard.exceptions", {"run_id": str(r.run_id)}, page=ui.Paging(offset=4, limit=4))).output
    assert len(part["rows"]) == 4 and part["paging"] == {"offset": 4, "limit": 4, "total": 10, "complete": True}
    assert "5–8 của 10" in part["note"]
