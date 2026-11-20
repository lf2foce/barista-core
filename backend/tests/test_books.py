"""Sổ sách & nghĩa vụ (0063): kỳ tháng tự mở CHỐNG LẶP (chạy lại / hai lượt cùng lúc vẫn một kỳ), chạy bù khi nghỉ, dừng ở kỳ cuối, người phụ trách
nghỉ ⇒ kỳ không người + báo; hạn tháng / quý / năm theo danh mục (dữ liệu); hồ sơ theo chặng tự mở; "đã nộp" cần căn cứ; mục kiểm đọc dữ kiện thật."""

import asyncio
import json
import uuid
from datetime import date
from typing import Any
from uuid import UUID

from barista import seed
from barista.agent import chat
from barista.kernel import events, executor
from barista.kernel.principal import Principal, system_principal
from barista.modules.books import applies, due_on
from tests.conftest import ok, owner_val, q
from tests.test_regulations import _company as _reg_company

DOMAIN = "sosach.test"


async def _company() -> tuple[UUID, dict[str, Principal]]:
    """Công ty riêng cho mỗi test (chỉ gói Sổ sách) — kỳ / nghĩa vụ không lẫn giữa các test."""
    d = f"{uuid.uuid4().hex[:6]}.{DOMAIN}"
    tid = await seed.create_tenant("Công ty sổ sách (test)", d, packs=("books",), about="khách dịch vụ kế toán (test)",
                                   people=[("admin", "Quản trị", "admin", "QT", True), ("kt1", "Kế toán Một", "bookkeeper", "KT", False),
                                           ("kt2", "Soát xét Hai", "reviewer", "KT", True)])
    people = {k: await seed.who(tid, k, d) for k in ("admin", "kt1", "kt2")}
    await ok(people["admin"], "books.configure", owner_email=people["kt1"].email, reviewer_email=people["kt2"].email, start_period="2026-07-15")
    await ok(people["admin"], "books.set_profile", fact="bhxh=no")   # không lao động ⇒ không nghĩa vụ BHXH tháng (giữ test gọn theo quý / năm)
    return tid, people


async def _open(tid: UUID, as_of: str) -> dict[str, Any]:
    r = await executor.run(system_principal(tid), "books.open_due", {"as_of": as_of})
    assert r.ok, r.error
    return r.output


def test_due_dates_follow_the_obligation_cadence() -> None:
    assert due_on(date(2026, 9, 1), 1, 31) == date(2026, 10, 31)      # GTGT quý 3 ⇒ cuối tháng 10
    assert due_on(date(2026, 10, 1), 1, 20) == date(2026, 11, 20)     # khai tháng ⇒ ngày 20 tháng sau
    assert due_on(date(2026, 12, 1), 3, 31) == date(2027, 3, 31)      # BCTC năm ⇒ 31/03 năm sau
    assert due_on(date(2027, 1, 1), 1, 31) == date(2027, 2, 28)       # kẹp theo độ dài tháng
    assert [m for m in range(1, 13) if applies("quarterly", date(2026, m, 1))] == [3, 6, 9, 12]
    assert [m for m in range(1, 13) if applies("yearly", date(2026, m, 1))] == [12]


async def test_periods_open_once_catch_up_and_carry_their_filings() -> None:
    tid, people = await _company()
    assert await owner_val("SELECT count(*) FROM books.obligations WHERE tenant_id = %s AND active", (tid,)) == 5   # theo bộ quy định
    out = await _open(tid, "2026-11-05")                                                   # nghỉ từ tháng 7 ⇒ chạy bù 07 → 10
    assert out["periods"] == ["07/2026", "08/2026", "09/2026", "10/2026"] and not out["unassigned"]
    assert (await _open(tid, "2026-11-20"))["opened"] == 0                                 # chạy lại: không kỳ trùng
    due = await owner_val("""SELECT array_agg(f.label || '@' || f.due_on ORDER BY f.label) FROM books.filings f JOIN books.periods p ON p.id = f.period_id
                             WHERE p.tenant_id = %s""", (tid,))
    # chỉ kỳ cuối quý có nghĩa vụ quý; hạn 31/10/2026 là thứ Bảy ⇒ dời sang thứ Hai 02/11 (lịch của bộ quy định)
    assert due == ["Tờ khai thuế GTGT quý 3/2026@2026-11-02", "Tờ khai thuế TNCN khấu trừ quý 3/2026@2026-11-02"]
    await events.drain()
    assert await owner_val("SELECT count(*) FROM flow.cases WHERE tenant_id = %s AND subject_kind = 'books.period'", (tid,)) == 4   # hồ sơ tự mở

    both = await asyncio.gather(*(executor.run(system_principal(tid), "books.open_due", {"as_of": "2026-12-03"}) for _ in range(2)))
    assert sorted(r.output["opened"] for r in both) == [0, 1]                              # hai lượt cùng lúc ⇒ đúng một kỳ 11/2026
    assert await owner_val("SELECT count(*) FROM books.periods WHERE tenant_id = %s", (tid,)) == 5

    page = await q(people["kt1"], "books.periods")
    sep = next(r for r in page["rows"] if r["label"] == "09/2026")
    assert sep["stage"] == "Thu chứng từ" and sep["pending"] == 2 and sep["owner"] == "Kế toán Một"


async def test_end_period_stops_opening_and_a_departed_owner_is_flagged() -> None:
    tid, people = await _company()
    await ok(people["admin"], "books.configure", owner_email=people["kt1"].email, reviewer_email=people["kt2"].email,
             start_period="2026-07-01", end_period="2026-08-31")
    assert (await _open(tid, "2027-01-10"))["periods"] == ["07/2026", "08/2026"]           # ngừng dịch vụ sau 08 ⇒ không mở thêm

    tid, people = await _company()
    await owner_val("UPDATE core.memberships SET active = false WHERE tenant_id = %s AND user_id = %s RETURNING 1", (tid, people["kt1"].user_id))
    out = await _open(tid, "2026-08-02")
    assert out["periods"] == ["07/2026"] and out["unassigned"]
    assert await owner_val("SELECT owner_id FROM books.periods WHERE tenant_id = %s", (tid,)) is None
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND kind = 'books'", (people["kt2"].user_id,)) == 1
    await events.drain()                                                                   # hồ sơ vẫn mở; việc tự sinh CHỜ người, không lỗi
    case = await owner_val("SELECT id FROM flow.cases WHERE tenant_id = %s AND subject_kind = 'books.period'", (tid,))
    assert case is not None and await owner_val("SELECT count(*) FROM flow.tasks WHERE case_id = %s", (case,)) == 0
    assert await owner_val("SELECT count(*) FROM core.deliveries WHERE tenant_id = %s AND last_error <> ''", (tid,)) == 0
    await ok(people["admin"], "books.configure", owner_email=people["admin"].email, reviewer_email=people["kt2"].email,
             start_period="2026-07-01")
    await events.drain()                                                                   # có người phụ trách ⇒ hồ sơ + việc chặng hiện tại về người đó
    assert await owner_val("SELECT owner_id FROM flow.cases WHERE id = %s", (case,)) == people["admin"].user_id
    assert await owner_val("SELECT assignee_id FROM flow.tasks WHERE case_id = %s AND origin = 'stage'", (case,)) == people["admin"].user_id


async def test_filing_needs_evidence_and_the_case_item_reads_real_facts() -> None:
    tid, people = await _company()
    await _open(tid, "2026-10-02")
    sep = await owner_val("SELECT id FROM books.periods WHERE tenant_id = %s AND period_start = '2026-09-01'", (tid,))
    check = {"subject_id": sep, "args": {}}
    r = await executor.query(system_principal(tid), "books.filings_done", check)
    assert r.output["status"] == "missing" and "2/2" in r.output["value"]
    filings = (await q(people["kt1"], "books.filings"))["rows"]
    r = await executor.run(people["kt1"], "books.mark_filing", {"filing_id": filings[0]["id"], "status": "filed"})
    assert r.status == "invalid"                                                           # đã nộp mà không căn cứ ⇒ chặn
    await ok(people["kt1"], "books.mark_filing", filing_id=filings[0]["id"], status="filed", evidence="Mã giao dịch 11020261000001")
    await ok(people["kt1"], "books.mark_filing", filing_id=filings[1]["id"], status="not_required", evidence="Quý 3 không chi trả lương")
    r = await executor.query(system_principal(tid), "books.filings_done", check)
    assert r.output["status"] == "verified"
    proposal = await executor.run(people["kt1"].as_agent(), "books.configure", {"start_period": "2026-01-01"})
    assert proposal.status == "denied"                                                    # trợ lý không đổi thiết lập dịch vụ


async def test_summary_counts_every_open_period_not_just_the_newest() -> None:
    tid, people = await _company()
    await ok(people["admin"], "books.configure", owner_email=people["kt1"].email, reviewer_email=people["kt2"].email,
             start_period="2025-08-01")
    assert len((await _open(tid, "2026-11-05"))["periods"]) == 15                         # 08/2025 → 10/2026
    await owner_val("""UPDATE books.filings f SET status = 'filed', evidence = 'đã nộp (test)', done_at = now() FROM books.periods p
                       WHERE p.id = f.period_id AND p.tenant_id = %s AND p.period_start >= '2025-11-01' RETURNING 1""", (tid,))
    s = await q(people["kt1"], "books.summary")                                            # 12 kỳ mới nhất đã xong; quý 3/2025 còn treo
    assert (s["overdue"], s["pending"], s["period"], s["next_due"]) == (2, 2, "09/2025", "2025-10-31")


async def test_filing_history_is_append_only_and_reopening_keeps_the_trail() -> None:
    import psycopg

    from barista.db import db

    tid, people = await _company()
    await _open(tid, "2026-10-02")
    kt1 = people["kt1"]
    fid = (await q(kt1, "books.filings"))["rows"][0]["id"]
    await ok(kt1, "books.mark_filing", filing_id=fid, status="filed", evidence="Mã giao dịch 11020261000002")
    r = await executor.run(kt1, "books.mark_filing", {"filing_id": fid, "status": "pending"})
    assert r.status == "invalid"                                                           # mở lại phải có lý do
    await ok(kt1, "books.mark_filing", filing_id=fid, status="pending", evidence="Nộp nhầm kỳ, nộp bổ sung")
    await ok(kt1, "books.mark_filing", filing_id=fid, status="filed", evidence="Mã giao dịch 11020261000003 (bổ sung)")
    page = await q(kt1, "books.filing", filing_id=fid)
    trail = next(b for b in page["blocks"] if b["type"] == "list")["items"]
    assert [t["title"].split(" — ")[0] for t in trail] == ["Đã nộp", "Chưa nộp", "Đã nộp"]   # mới nhất trước, lần mở lại còn nguyên
    assert "11020261000002" in trail[2]["title"]
    async with db.tenant_tx(kt1) as conn:                                                  # vai ứng dụng không sửa / xoá được nhật ký
        for sql in ("UPDATE books.filing_events SET note = 'sửa' WHERE filing_id = %s", "DELETE FROM books.filing_events WHERE filing_id = %s"):
            try:
                async with conn.transaction():
                    await conn.execute(sql, (fid,))
                raise AssertionError(sql)
            except psycopg.errors.InsufficientPrivilege:
                pass


# Dời từ test_accounting_home / test_demo_accounting khi gói Dịch vụ kế toán tách khỏi lõi (10/11/2026): hai kịch bản chỉ cần gói Sổ sách.

async def test_filings_table_shows_the_calendar_column_only_when_something_needs_checking() -> None:
    tid, people = await _reg_company("2026-07-01")                                              # nghĩa vụ theo luật dời theo lịch giả định ⇒ có cảnh báo
    assert (await executor.run(system_principal(tid), "books.open_due", {"as_of": "2026-11-05"})).ok
    cols = {c["key"] for c in (await q(people["kt"], "books.filings"))["columns"]}
    assert "calendar_warning" in cols

    d = f"{uuid.uuid4().hex[:6]}.tukhai.test"                                               # không theo bộ quy định, tự khai, giữ nguyên ngày
    tid = await seed.create_tenant("Công ty tự khai (test)", d, packs=("books",), about="test",
                                   people=[("admin", "Quản trị", "admin", "QT", True), ("kt", "Kế toán", "bookkeeper", "KT", False)])
    people = {k: await seed.who(tid, k, d) for k in ("admin", "kt")}
    await ok(people["admin"], "books.configure", owner_email=people["kt"].email, start_period="2026-07-01", reg_pack="")
    await ok(people["admin"], "books.upsert_obligation", code="RIENG", label="Báo cáo riêng", cadence="monthly", due_months=1, due_day=10,
             holiday_rule="keep")
    assert (await executor.run(system_principal(tid), "books.open_due", {"as_of": "2026-11-05"})).ok
    page = await q(people["kt"], "books.filings")
    assert page["rows"] and "calendar_warning" not in {c["key"] for c in page["columns"]}, json.dumps(page["columns"])[:200]


async def test_tool_groups_can_be_switched_off_or_limited_per_company() -> None:
    d = f"{uuid.uuid4().hex[:6]}.nhom.test"
    tid = await seed.create_tenant("Nhóm công cụ (test)", d, packs=("books",),
                                   people=[("admin", "Quản trị", "admin", "QT", True), ("kt1", "Kế toán Một", "bookkeeper", "KT", False)])
    admin, kt1 = await seed.who(tid, "admin", d), await seed.who(tid, "kt1", d)

    def books_tools(cat: list[Any]) -> dict[str, str]:
        return {op.name: mode for op, mode in cat if op.module == "books"}

    before = books_tools(await chat.catalog(kt1))
    assert before["books.periods"] == "allow" and before["books.create_request"] == "propose"
    await ok(admin, "admin.set_agent_group", group="books.*", mode="deny")
    assert books_tools(await chat.catalog(kt1)) == {}                                       # tắt nhóm ⇒ trợ lý không thấy công cụ nào của nhóm
    r = await executor.run(kt1.as_agent(), "books.create_request", {"title": "x", "items": "- y"})
    assert r.status == "denied"                                                             # chạy hộ cũng bị chặn ở cửa executor, không chỉ ẩn
    await ok(admin, "admin.set_agent_group", group="books.*", mode="propose")
    limited = books_tools(await chat.catalog(kt1))
    assert limited["books.periods"] == "allow"                                              # xem không đổi (query không có "đề xuất")
    assert all(m == "propose" for n, m in limited.items() if n in ("books.create_request", "books.mark_filing"))
    page = await chat.executor.query(admin, "admin.agent_groups", {})
    row = next(x for x in page.output["rows"] if x["id"] == "books.*")
    assert row["state"] == "propose"
    await ok(admin, "admin.set_agent_group", group="books.*", mode="")
    assert books_tools(await chat.catalog(kt1)) == before                                   # bỏ siết ⇒ về theo quyền người hỏi
