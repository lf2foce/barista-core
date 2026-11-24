"""KPI & xếp hạng (0101): một định nghĩa cho mỗi con số (KPI = chỉ số module khai = mục tiêu kế hoạch); mỗi người chỉ thấy KPI trong phạm vi
của mình; chỉ tiêu đội cộng cả cây đội con; xếp hạng ẩn tên người khác trừ khi công ty bật; chốt kỳ giữ số dù dữ liệu đổi sau; trưởng đội chỉ
đặt chỉ tiêu trong cây mình quản lý."""

from datetime import date, timedelta

from barista.kernel.clock import today
from barista.kernel.executor import run
from tests.conftest import Who, ok, owner_val, q

THIS = f"{today():%Y-%m}"


async def test_each_person_sees_only_kpi_in_their_scope(who: Who) -> None:
    sale1, truonga, giamdoc = await who("sale1"), await who("truonga"), await who("giamdoc")
    assert {r["user_id"] for r in (await q(sale1, "kpi.mine", period=THIS))["rows"]} == {str(sale1.user_id)}
    lead_rows = (await q(truonga, "kpi.board", period=THIS))["rows"]
    people = {r["subject"] for r in lead_rows if r["user_id"]}
    assert people and all(r["team"] == "Team Kinh doanh A" for r in lead_rows if r["user_id"])   # trưởng team A: chỉ người team A
    all_people = {r["subject"] for r in (await q(giamdoc, "kpi.board", period=THIS))["rows"] if r["user_id"]}
    assert people < all_people
    assert {r["user_id"] for r in (await q(sale1, "kpi.board", period=THIS))["rows"]} == {str(sale1.user_id)}   # sale gọi thẳng: vẫn chỉ của mình


async def test_closing_keeps_the_number_even_if_data_changes_later(who: Who) -> None:
    giamdoc, truonga = await who("giamdoc"), await who("truonga")
    last = (today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    assert (await run(truonga, "kpi.close_period", {"period": last})).status == "denied"   # trưởng team không chốt kỳ cả công ty
    out = await ok(giamdoc, "kpi.close_period", period=last)
    assert out["closed"] > 0
    board = (await q(giamdoc, "kpi.board", period=last))["rows"]
    assert all(r["closed"] and "đã đổi" not in r["closed"] for r in board)   # vừa chốt: số chốt = số hiện tại
    assert any(c["label"] == "Số đã chốt" for c in (await q(giamdoc, "kpi.board", period=last))["columns"])
    sale1 = await who("sale1")
    call = await owner_val("""UPDATE crm.activities SET at = %s WHERE id = (SELECT id FROM crm.activities WHERE by_user = %s AND kind = 'call'
                              AND at >= date_trunc('month', now()) LIMIT 1) RETURNING id""", (f"{last}-15 10:00+07", sale1.user_id))
    try:
        row = next(r for r in (await q(giamdoc, "kpi.board", period=last, metric="crm.metric_calls"))["rows"] if r["user_id"] == str(sale1.user_id))
        assert "đã đổi sau chốt" in row["closed"]                                 # số chốt giữ nguyên, có đánh dấu chênh với số hiện tại
    finally:
        await owner_val("UPDATE crm.activities SET at = now() WHERE id = %s RETURNING id", (call,))


async def test_team_lead_sets_targets_only_inside_the_tree_they_manage(who: Who) -> None:
    truonga = await who("truonga")
    out = await ok(truonga, "kpi.set_target", period=THIS, metric="crm.metric_calls", user_email="sale1@sanmau.demo", target=70)
    assert "70 cuộc" in out["summary"]
    r = await run(truonga, "kpi.set_target", {"period": THIS, "metric": "crm.metric_calls", "user_email": "sale4@sanmau.demo", "target": 70})
    assert r.status != "ok"                                                   # sale4 ở Miền Nam — ngoài cây team A
    await ok(truonga, "kpi.set_target", period=THIS, metric="crm.metric_calls", user_email="sale1@sanmau.demo", target=60)


async def test_a_metric_the_person_cannot_read_is_not_offered_set_or_ranked(who: Who) -> None:
    """Review 0101: chỉ số trả số CẢ CÔNG TY ⇒ phải đọc được tài nguyên của chỉ số mới chọn / đặt / xếp hạng (chỉ số tiền sau này không lộ qua KPI)."""
    from barista.kernel import registry
    sale1, truonga = await who("sale1"), await who("truonga")
    registry.METRICS["mkt.metric_secret_test"] = registry.Metric("mkt.metric_secret_test", "Chỉ số thử (marketing)", "đ", "mkt")
    try:
        assert "mkt.metric_secret_test" not in {o["value"] for o in await q(sale1, "kpi.metric_options")}
        r = await run(truonga, "kpi.set_target", {"period": THIS, "metric": "mkt.metric_secret_test", "user_email": "sale1@sanmau.demo", "target": 1})
        assert r.status in ("denied", "not_found")             # ngoài danh sách chọn của người đó ⇒ form không nhận; không thì lệnh từ chối
        rk = await run(sale1, "kpi.ranking", {"period": THIS, "metric": "mkt.metric_secret_test"})
        assert rk.status in ("denied", "not_found") and rk.output is None
    finally:
        registry.METRICS.pop("mkt.metric_secret_test", None)


async def test_copy_targets_to_a_new_period_never_overwrites(who: Who) -> None:
    giamdoc = await who("giamdoc")
    nxt = date(today().year + (today().month == 12), today().month % 12 + 1, 1).strftime("%Y-%m")
    first = await ok(giamdoc, "kpi.copy_targets", from_period=THIS, to_period=nxt)
    assert first["copied"] > 0
    again = await ok(giamdoc, "kpi.copy_targets", from_period=THIS, to_period=nxt)
    assert again["copied"] == 0 and again["skipped"] == first["copied"]                 # đã có ⇒ giữ nguyên, không nhân đôi
    assert len((await q(giamdoc, "kpi.board", period=nxt))["rows"]) == first["copied"]
    await owner_val("DELETE FROM kpi.targets WHERE period = %s RETURNING 1", (f"{nxt}-01",))


async def test_team_lead_sees_who_needs_coaching_first(who: Who) -> None:
    """Rà vai trưởng nhóm (18/11/2026): mở KPI đội, thứ nhìn thấy đầu là người cần kèm (chậm / không đạt, % thấp nhất lên đầu); bàn việc của trưởng
    nhóm có câu "N người trong đội đang chậm" khi có; sale không có câu này (màn đội không phải của họ)."""
    from barista.agent import desk

    truonga, sale1 = await who("truonga"), await who("sale1")
    board = await q(truonga, "kpi.board", period=THIS)
    lag = [r["status"] in ("Chậm", "Không đạt") for r in board["rows"]]
    assert lag == sorted(lag, reverse=True)                                           # mọi dòng chậm đứng trước dòng đúng nhịp / đạt
    slow = [r["pct"] for r in board["rows"] if r["status"] in ("Chậm", "Không đạt")]
    assert slow == sorted(slow)
    said = [i["title"] for i in await desk.attention(truonga) if i.get("query") == "kpi.board"]
    people_behind = {r["user_id"] for r in board["rows"] if r["user_id"] and r["status"] in ("Chậm", "Không đạt")}
    assert said == ([f"{len(people_behind)} người trong đội đang chậm chỉ tiêu tháng"] if people_behind else [])
    assert not [i for i in await desk.attention(sale1) if i.get("query") == "kpi.board"]


async def test_board_tiles_count_people_and_teams_separately_and_never_average_across_metrics(who: Who) -> None:
    """Rà 23/11/2026: ô "% đạt trung bình" lấy trung bình % của MỌI dòng — trộn dòng đội (đã gồm số của người) với dòng người, và trộn các chỉ số khác nhau.
    Ô số đếm riêng dòng người và dòng đội; không còn ô trung bình."""
    page = await q(await who("giamdoc"), "kpi.board", period=THIS)
    tiles = {k["label"]: k["value"] for k in page["kpis"]}
    persons = [r for r in page["rows"] if r["user_id"]]
    teams = [r for r in page["rows"] if not r["user_id"]]
    assert "% đạt trung bình" not in tiles
    assert tiles["Người đạt"] == f"{sum(1 for r in persons if r['status'] == 'Đạt')}/{len(persons)}"
    if teams:
        assert tiles["Đội đạt"].endswith(f"/{len(teams)}")


async def test_a_month_still_running_cannot_be_closed_and_the_board_says_why_before_anyone_clicks(who: Who) -> None:
    """Rà 23/11/2026 (cơ chế lõi G2): nút "Chốt KPI tháng" chạy được giữa tháng. Lệnh từ chối kỳ chưa hết; màn KPI đội nói trước lý do (nút mờ kèm câu),
    cùng MỘT câu với lời từ chối của lệnh. Màn tự nói mình để làm gì (G1)."""
    from barista.workspaces import workspaces

    gd = await who("giamdoc")
    page = await q(gd, "kpi.board", period=THIS)
    why = page["unavailable"]["kpi.close_period"]
    refused = await run(gd, "kpi.close_period", {"period": THIS})
    assert refused.status == "invalid" and refused.error == why
    last = (today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    assert "unavailable" not in await q(gd, "kpi.board", period=last)                # tháng đã hết: nút bình thường
    board = next(s for w in workspaces() for s in w.screens if s.key == "kpi_board")
    assert board.about.startswith("Theo dõi chỉ tiêu tháng")
