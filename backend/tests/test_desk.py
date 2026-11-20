"""BÀN VIỆC (0094): một điểm vào "tôi phải làm gì" — mục là màn module khai ``desk=`` (đúng quyền người xem, mở ra đủ nút như cũ), câu tín hiệu
do code tính từ chính truy vấn của màn; lời chào của trợ lý là góc nhìn gọn của cùng các câu đó. Màn việc không lặp ở menu."""

import re
from datetime import timedelta
from typing import Any

import pytest

from barista import workspaces
from barista.agent import desk
from barista.kernel import executor
from barista.kernel.clock import today
from barista.kernel.principal import Principal
from tests.conftest import Who, fresh_phone, ok


def _sections(page: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["screen"]["key"]: {**s, "group": g["key"]} for g in page["groups"] for s in g["sections"]}


def _menu(p: Principal) -> set[str]:
    return {s["key"] for w in workspaces.manifest(p, "")["workspaces"] for s in w["screens"]}


async def test_sale_sees_own_overdue_and_marketer_sees_no_customer_items(who: Who) -> None:
    sale3, mkt = await who("sale3"), await who("mkt")
    lead = (await ok(sale3, "crm.create_lead", name="Khách quá hạn gọi lại", phone=fresh_phone(), offering_code="HSR"))["lead_id"]
    await ok(sale3, "crm.log_activity", lead_id=lead, kind="call", outcome="connected", next_follow_up=(today() - timedelta(days=2)).isoformat())
    items = await desk.attention(sale3)
    assert items and all({"tone", "title", "detail", "query"} <= i.keys() for i in items)
    assert any(i["query"] == "crm.brief" for i in items)                     # có khách quá hạn / chưa gọi
    assert not any(i["query"] in ("leadflow.overview", "core.approvals") for i in items)   # sale không có số toàn công ty / phiếu duyệt
    assert not any(i["query"] in ("crm.brief", "deals.installments_due") for i in await desk.attention(mkt))   # marketing không thấy khách


async def test_manager_sees_company_signals_and_the_list_is_short_and_urgent_first(who: Who) -> None:
    giamdoc = await who("giamdoc")
    items = await desk.attention(giamdoc)
    assert len(items) <= 5
    tones = [i["tone"] for i in items]
    assert tones == sorted(tones, key=lambda t: {"bad": 0, "warn": 1}.get(t, 2))   # gấp nhất lên đầu


async def test_brief_is_my_customers_only_even_for_people_who_can_see_everyone(who: Who) -> None:
    """Giám đốc thấy mọi khách nhưng không gọi thay sale: bản tin + "hôm nay em thấy" không được giao việc gọi khách của người khác."""
    giamdoc, sale1 = await who("giamdoc"), await who("sale1")
    lead = (await ok(sale1, "crm.create_lead", name="Khách riêng của An", phone=fresh_phone(), offering_code="HSR"))["lead_id"]
    await ok(sale1, "crm.log_activity", lead_id=lead, kind="call", outcome="connected", next_follow_up=(today() - timedelta(days=2)).isoformat())
    page = (await executor.query(giamdoc, "crm.brief", {})).output
    titles = " ".join(b.get("title", "") for b in page["blocks"])
    assert "Khách riêng của An" not in titles
    assert not any(i["query"] == "crm.brief" for i in await desk.attention(giamdoc))
    mine = (await executor.query(sale1, "crm.brief", {})).output
    assert "Khách riêng của An" in " ".join(b.get("title", "") for b in mine["blocks"])


async def test_one_entry_point_work_screens_live_on_the_desk_not_in_menus(who: Who) -> None:
    """Một điểm vào: màn VIỆC (việc của tôi, khách cần gọi, lịch, phòng khác nhờ, trợ lý theo dõi…) chỉ ở bàn việc; màn ĐỐI TƯỢNG có tín hiệu
    (kế hoạch, giữ căn…) vẫn ở menu. Mở từ bàn = đúng màn đó: cùng truy vấn, đủ nút (nhận / từ chối việc nhờ) — không mất năng lực."""
    sale1, lead_kd = await who("sale1"), await who("truonga")
    page, menu = _sections(await desk.desk(sale1)), _menu(sale1)
    for key in ("today", "agenda", "tasks"):
        assert page[key]["group"] == "do" and key not in menu
    assert "followups" in page and "followups" not in menu and page["followups"]["group"] == "decide"
    assert "plans" in page and "plans" in menu                                   # đối tượng: ở menu, bàn việc có tín hiệu
    assert "stuck" not in page                                                    # hồ sơ kẹt: cần phạm vi đội — sale không có
    desk_specs = {x["key"] for x in workspaces.manifest(sale1, "")["desk"]}                  # vẫn mở được từ lời chào / thông báo / ⌘K
    assert {"today", "agenda", "tasks", "followups"} <= desk_specs and "stuck" not in desk_specs
    team = _sections(await desk.desk(lead_kd))
    assert "stuck" in team and "requests" in team and "requests" not in _menu(lead_kd)
    commands = {a["command"] for a in team["requests"]["screen"]["row_actions"]}
    assert {"flow.accept_request", "flow.decline_request"} <= commands                # đủ nút như màn cũ
    groups = [g["key"] for g in (await desk.desk(lead_kd))["groups"]]
    assert groups == [k for k in ("decide", "do", "watch") if k in groups]           # quyết → làm → theo dõi


async def test_director_keeps_the_lead_queue_but_without_the_team_signal(who: Who) -> None:
    """``team_only``: người phạm vi cả công ty không nhận CÂU "lead chờ anh/chị chia" (đã có tín hiệu toàn công ty) — nhưng màn vẫn mở được."""
    gd = await who("giamdoc")
    sec = _sections(await desk.desk(gd)).get("lead_queue")
    assert sec is not None and sec["lines"] == [] and not sec["unchecked"]


async def test_a_source_that_fails_is_shown_unchecked_not_as_nothing_to_do(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    sale1 = await who("sale1")
    real = executor.query

    async def broken(p: Principal, name: str, inp: dict[str, Any] | None = None) -> executor.Result:
        return executor.Result("error", error="hỏng thử") if name == "flow.tasks" else await real(p, name, inp)
    monkeypatch.setattr(executor, "query", broken)
    sec = _sections(await desk.desk(sale1))["tasks"]
    assert sec["unchecked"] and sec["lines"] == []


def test_personal_work_screens_must_join_the_desk() -> None:
    """Gói mới góp nguồn việc vào bàn chung, không tự dựng thêm hộp việc cá nhân: màn mang tên kiểu "của tôi / hôm nay / cần xử lý / chờ bạn"
    phải khai ``desk=``."""
    looks_personal = re.compile(r"của tôi|hôm nay|cần xử lý|chờ bạn|chờ chia|cần xem lại|cần làm")
    offenders = [f"{w.key}.{s.key} “{s.title}”" for w in workspaces.workspaces() for s in w.screens
                 if looks_personal.search(s.title.lower()) and s.desk is None]
    assert not offenders, offenders


def test_role_homes_pin_only_menu_screens() -> None:
    """Màn việc đã ở bàn việc ⇒ màn ghim theo vai chỉ trỏ màn có trong menu (ghim màn việc = mất lặng lẽ, vai trống màn đầu)."""
    from barista.kernel import registry
    desk_only = {s.key for w in workspaces.workspaces() for s in w.screens if not s.in_menu}
    pinned = {k for p in registry.PACKS.values() for r in p.roles.values() if r.home for k in r.home[1]}
    pinned |= {k for p in registry.PACKS.values() for extra in p.pins.values() for k in extra[1]}
    assert not pinned & desk_only, pinned & desk_only


async def test_a_broken_signal_or_slow_source_is_unchecked_too(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Câu tín hiệu lỗi (vd đọc sai tên ô) hay nguồn quá hạn ⇒ "chưa kiểm được", không im lặng như hết việc."""
    import asyncio
    from dataclasses import replace

    sale1 = await who("sale1")
    real = workspaces.desk_screens

    def broken_say() -> list[Any]:
        out = []
        for ws, s in real():
            if s.key == "tasks" and s.desk is not None:
                s = replace(s, desk=replace(s.desk, say=lambda o: 1 / 0))
            out.append((ws, s))
        return out
    monkeypatch.setattr(workspaces, "desk_screens", broken_say)
    assert _sections(await desk.desk(sale1))["tasks"]["unchecked"]
    monkeypatch.setattr(workspaces, "desk_screens", real)

    query = executor.query

    async def slow(p: Principal, name: str, inp: dict[str, Any] | None = None) -> executor.Result:
        if name == "flow.tasks":
            await asyncio.sleep(10)
        return await query(p, name, inp)
    monkeypatch.setattr(executor, "query", slow)
    monkeypatch.setattr(desk, "SOURCE_TIMEOUT_S", 0.5)   # 50 ms có thể hết hạn cả nguồn khoẻ khi chạy 4 worker trên Postgres thật
    secs = _sections(await desk.desk(sale1))
    assert secs["tasks"]["unchecked"] and not secs["today"]["unchecked"]          # một nguồn chậm không kéo cả bàn
    for task, _ in list(desk._flights.values()):   # lượt quá hạn vẫn chạy nốt (không huỷ giữa chừng) — dọn để test sau không ghép vào lượt ngủ 10 giây
        task.cancel()


async def test_concurrent_askers_share_one_run_and_a_timeout_never_cancels_it(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mở app gọi bàn việc + lời chào + gợi ý CÙNG LÚC ⇒ mỗi nguồn chạy MỘT lượt cho cả ba (đo 18/11/2026: trước đó mọi nguồn chạy ba lần, màn đang xem
    chờ). Quá hạn chỉ thôi chờ: truy vấn đang chạy dở không bị huỷ (huỷ giữa lúc psycopg gửi / nhận làm hỏng kết nối trả về pool). Lệnh commit sau lúc
    lượt bắt đầu ⇒ người hỏi sau đó chạy lượt mới."""
    import asyncio

    sale1 = await who("sale1")
    before = executor.writes(sale1.tenant_id)
    await ok(sale1, "crm.create_lead", name="Khách để đếm lệnh", phone=fresh_phone(), offering_code="HSR")
    assert executor.writes(sale1.tenant_id) == before + 1                           # lệnh commit ⇒ số lệnh của công ty tăng
    calls: list[str] = []
    finished: list[str] = []
    query = executor.query

    async def counting(p: Principal, name: str, inp: dict[str, Any] | None = None) -> executor.Result:
        calls.append(name)
        if name == "flow.tasks":
            await asyncio.sleep(0.6)
        r = await query(p, name, inp)
        finished.append(name)
        return r
    monkeypatch.setattr(executor, "query", counting)
    monkeypatch.setattr(desk, "SOURCE_TIMEOUT_S", 0.3)
    pages = await asyncio.gather(desk.desk(sale1), desk.desk(sale1), desk.attention(sale1))
    assert calls.count("flow.tasks") == 1 and all(_sections(pg)["tasks"]["unchecked"] for pg in pages[:2])
    await asyncio.sleep(0.6)
    assert "flow.tasks" in finished                                                # quá hạn nhưng lượt đọc vẫn chạy xong, không bị huỷ
    await desk.desk(sale1)
    assert calls.count("flow.tasks") == 2                                          # lượt xong là bỏ — không nhớ kết quả
    for task, _ in list(desk._flights.values()):
        task.cancel()
    monkeypatch.setattr(desk, "SOURCE_TIMEOUT_S", 3.0)
    monkeypatch.setattr(executor, "writes", lambda tenant_id: 0)
    first = asyncio.ensure_future(desk.desk(sale1))
    await asyncio.sleep(0.05)
    monkeypatch.setattr(executor, "writes", lambda tenant_id: 1)                   # có lệnh commit trong lúc lượt đầu đang chạy
    await asyncio.gather(first, desk.desk(sale1))
    assert calls.count("flow.tasks") == 4


async def test_no_result_is_reused_across_requests(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Không nhớ kết quả nguồn: quyền ở công ty dịch vụ lẫn ở từng công ty khách được đọc có thể vừa đổi — mỗi lần mở bàn chạy lại bằng quyền lúc đó."""
    sale1 = await who("sale1")
    calls: list[str] = []
    query = executor.query

    async def counting(p: Principal, name: str, inp: dict[str, Any] | None = None) -> executor.Result:
        calls.append(name)
        return await query(p, name, inp)
    monkeypatch.setattr(executor, "query", counting)
    await desk.desk(sale1)
    first = calls.count("flow.tasks")
    await desk.desk(sale1)
    assert first == 1 and calls.count("flow.tasks") == 2


async def test_an_unexpected_failure_costs_one_entry_not_the_whole_desk(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    sale1 = await who("sale1")
    query, spec = executor.query, workspaces.screen_spec

    async def exploding(p: Principal, name: str, inp: dict[str, Any] | None = None) -> executor.Result:
        if name == "flow.tasks":
            raise ConnectionError("mất kết nối thử")
        return await query(p, name, inp)

    def broken_spec(p: Principal, s: Any) -> Any:
        if s.key == "agenda":
            raise RuntimeError("hỏng thử")
        return spec(p, s)
    monkeypatch.setattr(executor, "query", exploding)
    monkeypatch.setattr(workspaces, "screen_spec", broken_spec)
    secs = _sections(await desk.desk(sale1))
    assert secs["tasks"]["unchecked"] and "agenda" not in secs and "today" in secs


def test_a_screen_contributed_twice_is_one_desk_entry() -> None:
    keys = [(s.query, s.key) for _, s in workspaces.desk_screens()]
    assert len(keys) == len(set(keys))


async def test_whole_desk_deadline_and_the_rail_count(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cả bàn có hạn chót: quá hạn ⇒ mục còn lại "chưa kiểm được", bàn vẫn trả. Số trên nút "Việc" do server đếm: mục có tín hiệu đỏ / vàng
    + thẻ đề xuất sáng đang mở (đếm bằng một câu đọc — không kích hoạt soạn đề xuất)."""
    import asyncio

    sale1 = await who("sale1")
    page = await desk.desk(sale1)
    hot = sum(1 for g in page["groups"] for s in g["sections"] if any(x["tone"] in ("bad", "warn") for x in s["lines"]))
    assert page["count"] == hot + page["morning"] and hot >= 1 and page["unchecked"] == 0 and not page["morning_unchecked"]

    query = executor.query

    async def slow(p: Principal, name: str, inp: dict[str, Any] | None = None) -> executor.Result:
        await asyncio.sleep(0.2)
        return await query(p, name, inp)
    monkeypatch.setattr(executor, "query", slow)
    monkeypatch.setattr(desk, "DESK_DEADLINE_S", 0.05)
    late = await desk.desk(sale1)
    secs = [s for g in late["groups"] for s in g["sections"]]
    assert secs and all(s["unchecked"] for s in secs if s["screen"]["key"] in ("tasks", "today"))
    assert late["unchecked"] >= 2                                                   # app biết là "chưa kiểm được", không phải hết việc

    async def broken_count(p: Principal) -> int:
        raise ConnectionError("thử")
    monkeypatch.setattr(desk.morning, "open_count", broken_count)
    monkeypatch.setattr(desk, "DESK_DEADLINE_S", 12.0)
    monkeypatch.setattr(executor, "query", query)
    page = await desk.desk(sale1)
    assert page["morning_unchecked"] and page["morning"] == 0 and page["unchecked"] >= 1
