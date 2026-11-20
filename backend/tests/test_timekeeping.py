"""0150 lát 4 "Chấm công" (gói ``timekeeping``): giờ chấm là sự thật (chỉ thêm, trượt luật IP ⇒ gắn cờ, không từ chối), bảng công là hàm thuần, chốt
tháng đẩy công sang Lương, người lao động xác nhận; nghỉ phép / làm thêm qua duyệt; quyền theo người / đội dưới SQL thẳng; tách bạch nhiệm vụ ở DB.
Dữ liệu: công ty văn phòng của ``seed_pay.office`` (đúng hàm của dữ liệu mẫu)."""

import ast
import json
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient

from barista import seed_pay
from barista.db import db
from barista.kernel import executor, registry
from barista.kernel.agent_policy import risk_of
from barista.kernel.clock import VN, today
from barista.kernel.principal import Principal
from barista.modules.timekeeping import sheet
from tests.conftest import edge, ok, owner_val, q

HC = {"weekdays": [1, 2, 3, 4, 5], "starts": "08:00", "ends": "17:00", "break_minutes": 60, "grace_minutes": 5}


@pytest.fixture(scope="module")
async def co() -> dict[str, Any]:
    return await seed_pay.office(f"tk{uuid.uuid4().hex[:6]}.demo", "Văn phòng thử (test)")


async def _sql(p: Principal, sql: Any, params: Any = None) -> list[dict[str, Any]]:
    async with db.tenant_tx(p, read_only=True) as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()


def _at(d: date, hm: str) -> datetime:
    return datetime.combine(d, datetime.strptime(hm, "%H:%M").time(), VN)


# ── Hàm thuần ──

def test_sheet_is_pure() -> None:
    tree = ast.parse(Path(sheet.__file__).read_text())
    mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                                                                                        for a in n.names}
    assert mods <= {"datetime", "typing"}, mods
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {"now", "today", "ctx", "psycopg"}


def test_day_rules_late_absent_leave_holiday_and_overtime_classes() -> None:
    mon, sat = date(2026, 10, 5), date(2026, 10, 10)
    def day(d: date, punches: list[tuple[str, str]], **kw: Any) -> dict[str, Any]:
        return sheet.day_sheet(d, pattern=HC, mode=kw.pop("mode", "punch"), holidays=kw.pop("holidays", {}),
                               punches=[{"at": _at(d, t), "direction": x, "flagged": False} for x, t in punches], leave=kw.pop("leave", None),
                               ot_minutes=kw.pop("ot", 0))
    late = day(mon, [("in", "08:12"), ("out", "17:00")])
    assert (late["status"], late["paid"], late["late_minutes"], late["worked_minutes"]) == ("work", 1.0, 12, 468)
    assert day(mon, [("in", "08:04"), ("out", "17:00")])["late_minutes"] == 0                  # trong phút được tha
    assert day(mon, [])["status"] == "absent" and day(mon, [])["unpaid"] == 1.0
    assert day(mon, [], mode="exception")["paid"] == 1.0                                        # đủ công theo lịch, không bắt chấm
    paid = day(mon, [], leave={"type": "PHEP_NAM", "paid_by": "company", "fraction": 1.0})
    sick = day(mon, [], leave={"type": "OM_BHXH", "paid_by": "insurance", "fraction": 1.0})
    assert (paid["paid"], paid["status"], sick["paid"], sick["unpaid"]) == (1.0, "leave", 0.0, 1.0)
    half = day(mon, [("in", "13:00"), ("out", "17:00")], leave={"type": "PHEP_NAM", "paid_by": "company", "fraction": 0.5})
    assert half["paid"] == 1.0 and half["late_minutes"] == 0
    half_open = day(mon, [("in", "16:50")])                                                  # review 10/11: vào mà không ra ⇒ chưa đủ, không công
    assert (half_open["status"], half_open["paid"], half_open["unpaid"], half_open["flags"]) == ("incomplete", 0.0, 0.0, ["missing_out"])
    assert day(mon, [("out", "17:00")])["flags"] == ["missing_in"]
    hol = day(date(2026, 9, 2), [], holidays={"2026-09-02": "Quốc khánh"})
    assert (hol["status"], hol["paid"]) == ("holiday", 1.0)
    assert day(mon, [("in", "08:00"), ("out", "19:00")], ot=120)["ot_class"] == "ot.150"
    assert day(mon, [("in", "08:00"), ("out", "18:00")], ot=120)["ot_minutes"] == 60            # chỉ phần thật làm ngoài lịch
    assert day(sat, [("in", "08:00"), ("out", "12:00")], ot=240)["ot_class"] == "ot.200"
    assert day(date(2026, 9, 2), [("in", "08:00"), ("out", "12:00")], ot=240, holidays={"2026-09-02": "x"})["ot_class"] == "ot.300"


# ── Cấu trúc ──

def test_the_assistant_never_punches_reviews_approves_closes_or_grants() -> None:
    cmds = {o.name: o for o in registry.OPS.values() if o.kind == "command" and o.module == "timekeeping"}
    hard = {"timekeeping.punch", "timekeeping.import_punch", "timekeeping.review_punch", "timekeeping.close_period", "timekeeping.grant_leave",
            "timekeeping.grant_annual_leave", "timekeeping.approve_leave", "timekeeping.approve_overtime", "timekeeping.apply_correction"}
    for name in hard:
        assert cmds[name].agent == "deny" and risk_of(cmds[name]) == "authority", name
    soft = {n for n, o in cmds.items() if o.agent == "propose"}
    assert {"timekeeping.request_leave", "timekeeping.request_overtime", "timekeeping.request_correction", "timekeeping.confirm_timesheet"} <= soft
    assert all(risk_of(cmds[n]) == "low" and not cmds[n].promotable for n in soft)
    assert all(o.risk for o in cmds.values()), [n for n, o in cmds.items() if not o.risk]   # mọi lệnh khai rủi ro có chủ đích
    assert "time" not in registry.PACKS and registry.pack_requires("timekeeping") == ("hr",)
    assert any(r.module == "timekeeping" and r.path == "/v1/timekeeping/punch" and not r.public for r in registry.ROUTES)


def test_punch_has_no_ip_field_the_person_could_fill() -> None:
    from barista.kernel.forms import field_specs

    assert {s["key"] for s in field_specs(registry.OPS["timekeeping.punch"])} == {"direction", "note"}


# ── Đầu cuối trên công ty mẫu ──

async def test_office_demo_closes_last_month_and_feeds_pay(co: dict[str, Any]) -> None:
    w, tid = co["who"], co["tenant_id"]
    assert co["closed"] == 4 and co["pushed"] >= 4                                          # 4 người chấm (Nhân sự tự không chốt mình)
    rows = await owner_val("""SELECT jsonb_agg(jsonb_build_array(role_key, quantity::float) ORDER BY role_key) FROM pay.work_entries e
                              JOIN core.users u ON u.id = e.user_id WHERE e.tenant_id = %s AND u.email = %s AND e.source = 'timekeeping.close'""",
                           (tid, f"nvan@{co['domain']}"))
    by = dict((r[0], r[1]) for r in rows)
    assert by["ot.150"] == 2.0 and by["days.paid"] >= 20 and "days.unpaid" not in by          # phép năm là ngày có lương
    chi = await owner_val("""SELECT (totals->>'unpaid_days')::float FROM timekeeping.close_lines l JOIN core.users u ON u.id = l.user_id
                             WHERE l.tenant_id = %s AND u.email = %s""", (tid, f"nvchi@{co['domain']}"))
    assert chi == 1.0                                                                      # một ngày nghỉ không lương
    # kỳ lương: quy tắc chưa xác minh ⇒ phiếu thật dừng; kỳ bóng có bảo hiểm + thuế luỹ tiến, mang dấu chưa xác minh
    slips = {s["name"]: s for s in await owner_val("SELECT jsonb_agg(to_jsonb(s)) FROM pay.slips s WHERE run_id = %s", (co["run_id"],))}
    assert all("chưa xác minh" in s["held_reason"] or "bảng công" in s["held_reason"] for s in slips.values()), {k: v["held_reason"] for k, v in slips.items()}
    shadow = {s["name"]: s for s in await owner_val("SELECT jsonb_agg(to_jsonb(s)) FROM pay.slips s WHERE run_id = %s", (co["shadow_id"],))}
    an = shadow["Hoàng Minh An"]
    assert not an["held_reason"] and an["figures"]["insured"] and an["figures"]["pit_vnd"] >= 0 and an["gross_vnd"] > 15_000_000
    lines = await owner_val("SELECT jsonb_agg(component) FROM pay.slip_lines WHERE slip_id = %s", (an["id"],))
    assert {"LUONG", "LAM_THEM", "SI_EMPLOYEE", "UNION_MEMBER_FEE", "PIT_PROGRESSIVE", "SI_EMPLOYER"} <= set(lines)
    assert "bảng công" in shadow["Trịnh Thu Trang"]["held_reason"]                           # Nhân sự: bảng công chưa ai chốt ⇒ không đoán
    # xác nhận: chính chủ, một lần
    assert (await q(w["nvan"], "timekeeping.pending_confirm"))["rows"] == []
    assert len((await q(w["nvbinh"], "timekeeping.pending_confirm"))["rows"]) == 1
    with pytest.raises(psycopg.errors.CheckViolation):
        async with db.tenant_tx(w["truongkd"]) as conn:   # trưởng đội không xác nhận thay (DB)
            await conn.execute("UPDATE timekeeping.close_lines SET confirmed_at = now() WHERE user_id = %s", (w["nvbinh"].user_id,))
    with pytest.raises(psycopg.errors.RestrictViolation):
        async with db.owner_tx() as conn:                  # số liệu đã chốt không sửa
            await conn.execute("UPDATE timekeeping.close_lines SET totals = '{}' WHERE tenant_id = %s", (tid,))
    # tháng đã chốt ⇒ không thêm giờ chấm vào tháng đó
    r = await executor.run(w["nhansu"], "timekeeping.import_punch", {"person": f"nvchi@{co['domain']}", "at": f"{co['unpaid_day']:%d/%m/%Y} 08:00",
                                                                    "direction": "in"})
    assert r.status == "denied" and "đã chốt" in r.error


async def test_punch_commands_turn_only_database_rule_violations_into_denied(co: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Chỉ vi phạm luật của DB (trigger / ràng buộc) mới thành ``denied`` kèm lý do; lỗi khác (mất kết nối, lỗi lập trình) phải nổi lên là lỗi hệ thống,
    không bị mặc thành "không được phép"."""
    from barista.kernel.executor import Ctx

    real_val = Ctx.val

    async def boom(self: Any, sql: Any, *a: Any, **kw: Any) -> Any:
        if "INSERT INTO timekeeping.punches" in str(sql):
            raise RuntimeError("giả lập lỗi không phải của luật DB")
        return await real_val(self, sql, *a, **kw)

    monkeypatch.setattr(Ctx, "val", boom)
    r = await executor.run(co["who"]["nhansu"], "timekeeping.import_punch",
                           {"person": f"nvchi@{co['domain']}", "at": f"{co['unpaid_day']:%d/%m/%Y} 08:00", "direction": "in"})
    assert r.status == "error", (r.status, r.error)


async def test_ip_rule_flags_instead_of_rejecting_and_the_ip_is_read_by_the_server(co: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from barista.config import settings
    from barista.main import app

    w = co["who"]
    monkeypatch.setattr(settings, "demo_key", "khoa-demo-thu")
    hdr = {"x-dev-user": f"nvchi@{co['domain']}", "x-tenant-id": str(co["tenant_id"]), "x-demo-key": "khoa-demo-thu"}
    # thân request khai "ip" ⇒ bị bỏ qua: máy chủ chỉ tin địa chỉ của kết nối
    for client, direction, want in ((("203.0.113.9", 4000), "in", []), (("198.51.100.77", 4000), "out", ["ip_outside"])):
        async with AsyncClient(transport=ASGITransport(app=app, client=client), base_url="http://test") as c:
            got = (await c.post("/v1/timekeeping/punch", json={"direction": direction, "ip": "203.0.113.9"}, headers=hdr)).json()
        assert got["status"] == "ok" and got["output"]["flags"] == want, got
    async with AsyncClient(transport=ASGITransport(app=app, client=("198.51.100.77", 4000)), base_url="http://test") as c:
        again = (await c.post("/v1/timekeeping/punch", json={"direction": "out"}, headers=hdr)).json()
    assert again["output"]["replayed"] is True                                              # bấm lặp trong 2 phút ⇒ một lần chấm
    # đường lệnh chung: không có IP ⇒ vẫn ghi, cờ "không đọc được"
    got = await ok(w["nvchi"], "timekeeping.punch", direction="in")
    assert got["flags"] == ["ip_unknown"]
    # trợ lý không chấm thay
    r = await executor.run(w["nvchi"].as_agent(), "timekeeping.punch", {"direction": "in"})
    assert r.status != "ok"
    # giờ chấm chỉ thêm; người xét không xét giờ của chính mình (DB)
    with pytest.raises(psycopg.errors.RestrictViolation):
        async with db.owner_tx() as conn:
            await conn.execute("UPDATE timekeeping.punches SET flags = '{}' WHERE user_id = %s", (w["nvchi"].user_id,))
    mine = await owner_val("SELECT id FROM timekeeping.punches WHERE user_id = %s AND flags <> '{}' ORDER BY at DESC LIMIT 1", (w["nvchi"].user_id,))
    with pytest.raises(psycopg.errors.CheckViolation):
        async with db.owner_tx() as conn:
            await conn.execute("INSERT INTO timekeeping.punch_reviews (tenant_id, punch_id, decision, reviewed_by) VALUES (%s, %s, 'accept', %s)",
                               (co["tenant_id"], mine, w["nvchi"].user_id))


async def test_people_see_their_own_leads_their_team_and_nobody_sees_pay_through_timekeeping(co: dict[str, Any]) -> None:
    w = co["who"]
    own = await _sql(w["nvan"], "SELECT DISTINCT user_id FROM timekeeping.punches")
    assert [r["user_id"] for r in own] == [w["nvan"].user_id]
    team = {r["user_id"] for r in await _sql(w["truongkd"], "SELECT DISTINCT user_id FROM timekeeping.punches")}
    assert team == {w[k].user_id for k in ("truongkd", "nvan", "nvbinh", "nvchi")}
    assert await _sql(w["truongkd"], "SELECT id FROM pay.slips") == []                      # trưởng đội duyệt công, không thấy tiền
    assert await _sql(w["nhansu"], "SELECT id FROM pay.slips") == []                        # Nhân sự không mặc nhiên thấy lương
    assert len(await _sql(w["nhansu"], "SELECT DISTINCT user_id FROM timekeeping.punches")) == 4
    sheet_ = await q(w["truongkd"], "timekeeping.timesheet", month=co["period"].isoformat())
    assert {r["name"] for r in sheet_["rows"]} == {"Đặng Quốc Việt", "Hoàng Minh An", "Phí Thị Bình", "Lương Bảo Chi"}
    assert "vnd" not in json.dumps([c["key"] for c in sheet_["columns"]])
    assert (await executor.query(w["nvan"], "timekeeping.timesheet", {})).status == "denied"


async def test_my_month_has_late_count_and_leave_left_on_the_card(co: dict[str, Any]) -> None:
    card = await q(co["who"]["nvchi"], "timekeeping.my_month", month=co["period"].isoformat())
    k = {i["label"]: i["value"] for b in card["blocks"] if b["type"] == "kpis" for i in b["items"]}
    assert k["Đi muộn (buổi)"] == 1 and k["Vắng"] == 0 and k["Phép năm còn lại"] == 12   # nghỉ không lương không trừ phép
    an = await q(co["who"]["nvan"], "timekeeping.my_month", month=co["period"].isoformat())
    k = {i["label"]: i["value"] for b in an["blocks"] if b["type"] == "kpis" for i in b["items"]}
    assert k["Phép năm còn lại"] == 11 and k["Làm thêm (giờ)"] == 2


async def test_leave_needs_balance_and_someone_else_and_overtime_has_caps(co: dict[str, Any]) -> None:
    w = co["who"]
    friday = co["friday"]
    r = await executor.run(w["nvbinh"], "timekeeping.request_leave", {"leave_type": "PHEP_NAM", "from_day": friday.isoformat()})
    assert r.status == "invalid" and "trùng" in r.error                                     # đơn thứ Sáu đã có (đang chờ)
    far = today() + timedelta(days=40)
    r = await executor.run(w["nvbinh"], "timekeeping.request_leave", {"leave_type": "PHEP_NAM", "from_day": far.isoformat(),
                                                                       "to_day": (far + timedelta(days=30)).isoformat()})
    assert r.status == "invalid" and "Không đủ phép" in r.error
    workday = today() - timedelta(days=max(0, today().weekday() - 3))   # thứ Hai–Năm gần nhất (cuối tuần trần làm thêm khác ngày thường; thứ Sáu đã có đơn)
    r = await executor.run(w["nvbinh"], "timekeeping.request_overtime", {"day": workday.isoformat(), "hours": 5, "reason": "x"})
    assert r.status == "invalid" and "trần một ngày" in r.error and "OT_CAPS" in r.error
    with pytest.raises(psycopg.errors.CheckViolation):
        async with db.owner_tx() as conn:   # tự duyệt đơn của mình: DB từ chối
            await conn.execute("UPDATE timekeeping.leave_requests SET status = 'approved', approved_by = ARRAY[user_id] WHERE status = 'requested'")



# ── Sửa sau review 10/11 ──

def _segments(*rows: tuple[date, str]) -> list[dict[str, Any]]:
    return [{"from": d, "mode": mode, "pattern": HC} for d, mode in rows]


def test_month_sheet_uses_standard_days_and_every_enrolment_row_of_the_month() -> None:
    """Người vào làm 20/10 đủ 9/9 ngày ⇒ 9 / 22 ngày công chuẩn (không phải đủ tháng); đổi cách chấm giữa tháng giữ nửa đầu; nghỉ việc (``off``) giữa
    tháng giữ những ngày đã làm."""
    oct1, until = date(2026, 10, 1), date(2026, 10, 31)
    work = [{"at": _at(d, t), "direction": x, "flagged": False} for d in sheet.working_days(oct1, until, weekdays=[1, 2, 3, 4, 5], holidays={})
            for x, t in (("in", "08:00"), ("out", "17:00"))]
    joiner = sheet.month_sheet(oct1, until=until, segments=_segments((date(2026, 10, 20), "punch")), holidays={}, punches=work, leave={}, overtime={})
    assert (joiner["totals"]["std_days"], joiner["totals"]["paid_days"], joiner["totals"]["unpaid_days"]) == (22, 9.0, 0.0)
    first_half = [p for p in work if p["at"].day < 15]
    moved = sheet.month_sheet(oct1, until=until, segments=_segments((date(2026, 9, 1), "punch"), (date(2026, 10, 15), "exception")), holidays={},
                              punches=first_half, leave={}, overtime={})
    assert moved["totals"]["paid_days"] == 22.0 and moved["totals"]["absent_days"] == 0                 # nửa đầu theo giờ chấm, nửa sau đủ công
    leaver = sheet.month_sheet(oct1, until=until, segments=_segments((date(2026, 9, 1), "punch"), (date(2026, 10, 16), "off")), holidays={},
                               punches=work, leave={}, overtime={})
    assert (leaver["totals"]["std_days"], leaver["totals"]["paid_days"]) == (22, 11.0)                  # 1–15/10: 11 ngày làm việc


async def test_only_the_edge_key_makes_forwarded_for_trusted(co: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Bản cài chạy ``barista.edge:app`` (``deploy/serve.py``): X-Forwarded-For chỉ được tin khi yêu cầu mang đúng khoá proxy biên (Traefik của stack /
    BFF gắn). Container khác trên mạng chung tự gửi "203.0.113.9" (IP văn phòng) — không khoá / sai khoá ⇒ bỏ qua, dùng địa chỉ kết nối; đúng khoá ⇒
    hop CUỐI; đăng nhập dev "từ máy local" giả bằng header không qua; trần lần xin token sai lại đếm theo địa chỉ người gọi thật."""
    from barista.config import settings
    from barista.edge import EdgeAddress
    from barista.kernel import ratelimit
    from barista.main import app

    key = "khoa-bien-thu-0123456789abcdef"
    edge = EdgeAddress(app, key)
    monkeypatch.setattr(settings, "demo_key", "khoa-demo-thu")
    hdr = {"x-dev-user": f"nvan@{co['domain']}", "x-tenant-id": str(co["tenant_id"]), "x-demo-key": "khoa-demo-thu"}

    async def punch(direction: str, extra: dict[str, str]) -> list[str]:
        async with AsyncClient(transport=ASGITransport(app=edge, client=("10.0.1.50", 4000)), base_url="http://test") as c:
            got = (await c.post("/v1/timekeeping/punch", json={"direction": direction}, headers={**hdr, **extra})).json()
        assert got["status"] == "ok", got
        return got["output"]["flags"]

    assert await punch("in", {"x-forwarded-for": "203.0.113.9"}) == ["ip_outside"]                              # không khoá
    assert await punch("out", {"x-forwarded-for": "203.0.113.9", "x-barista-edge": "sai-khoa-0123456789abcdef"}) == ["ip_outside"]
    assert await punch("in", {"x-forwarded-for": "198.51.100.1, 203.0.113.9", "x-barista-edge": key}) == []       # đúng khoá: hop cuối
    async with AsyncClient(transport=ASGITransport(app=edge, client=("10.0.1.50", 4000)), base_url="http://test") as c:
        r = await c.get("/v1/manifest", headers={"x-dev-user": f"nvan@{co['domain']}", "x-tenant-id": str(co["tenant_id"]), "x-forwarded-for": "127.0.0.1"})
    assert r.status_code in (401, 403)
    # trần lần xin token sai theo (client_id, địa chỉ người gọi THẬT): kẻ dò khoá sau Traefik không khoá được người khác cùng client_id
    monkeypatch.setitem(ratelimit.LIMITS, "app_token_fail", (2, 600))
    cid = f"app_{uuid.uuid4().hex[:10]}"

    async def token(addr: str) -> int:
        async with AsyncClient(transport=ASGITransport(app=edge, client=("10.0.1.9", 4000)), base_url="http://test") as c:
            return (await c.post("/v1/apps/token", data={"grant_type": "client_credentials", "client_id": cid, "client_secret": "sai"},
                                 headers={"x-forwarded-for": addr, "x-barista-edge": key})).status_code
    assert [await token("198.51.100.1") for _ in range(3)] == [401, 401, 429]
    assert await token("198.51.100.2") == 401


async def test_edge_layer_strips_its_key_and_reads_the_last_hop() -> None:
    from barista.edge import EdgeAddress, last_hop

    seen: list[Any] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    key = "k" * 24
    async with AsyncClient(transport=ASGITransport(app=EdgeAddress(inner, key), client=("10.0.1.9", 1)), base_url="http://test") as c:
        await c.get("/", headers={"x-barista-edge": key, "x-forwarded-for": "198.51.100.3"})
        await c.get("/", headers={"x-barista-edge": key, "x-forwarded-for": "not-an-ip"})
    assert seen[0]["client"] == ("198.51.100.3", 0) and seen[1]["client"][0] == "10.0.1.9"
    assert all(k != b"x-barista-edge" for s in seen for k, _ in s["headers"])
    assert last_hop(b"203.0.113.9, 198.51.100.4") == "198.51.100.4" and last_hop(b"") is None
    async with AsyncClient(transport=ASGITransport(app=EdgeAddress(inner, ""), client=("10.0.1.9", 1)), base_url="http://test") as c:
        await c.get("/", headers={"x-barista-edge": "", "x-forwarded-for": "198.51.100.3"})   # bản cài chưa có khoá ⇒ không tin gì
    assert seen[-1]["client"][0] == "10.0.1.9"


async def test_close_skips_people_with_open_days_and_a_lead_can_file_their_correction() -> None:
    """Ngày thiếu giờ chấm không tính công và giữ người đó lại khi chốt — những người khác của đội vẫn chốt; trưởng đội ghi thay đơn bổ sung giờ (cấp trên
    duyệt, không ai tự duyệt) rồi chốt nốt. Nghỉ việc giữa tháng giữ những ngày đã làm."""
    co = await seed_pay.office(f"tc{uuid.uuid4().hex[:6]}.demo", "Văn phòng chưa chốt (test)", runs=False, close=False)
    w, first, dom = co["who"], co["period"], co["domain"]
    sheet_ = await q(w["nhansu"], "timekeeping.timesheet", month=first.isoformat())
    assert next(x for x in sheet_["rows"] if x["name"] == "Phí Thị Bình")["missing"] == 1
    # nghỉ việc giữa tháng (cách chấm "off" từ một ngày tháng trước): những ngày đã làm vẫn trên bảng công
    await ok(w["chu"], "timekeeping.enrol", person=f"nvchi@{dom}", pattern_code="HC", site_code="VP", mode="off",
             valid_from=(co["unpaid_day"] + timedelta(days=1)).isoformat())
    chi = next(x for x in (await q(w["nhansu"], "timekeeping.timesheet", month=first.isoformat()))["rows"] if x["name"] == "Lương Bảo Chi")
    assert 0 < chi["paid"] < 22
    got = await ok(w["truongkd"], "timekeeping.close_period", month=first.isoformat(), team_code="KD-A")
    assert got["people"] == 2 and len(got["left"]) == 1 and got["left"][0].startswith("Phí Thị Bình:") and "thiếu giờ chấm ngày" in got["left"][0]
    assert f"{co['forgot_day']:%d/%m}" in got["left"][0]
    # trưởng đội ghi thay đơn bổ sung giờ cho người trong đội ⇒ phiếu duyệt lên cấp trên; xét giờ có cờ; chốt nốt
    cor = await ok(w["truongkd"], "timekeeping.request_correction", person=f"nvbinh@{dom}", day=co["forgot_day"].isoformat(), clock="17:20",
                   direction="out", reason="Bạn Bình quên bấm ra ca, đã xác nhận với bảo vệ")
    assert (await executor.run(edge(w["truongkd"]), "core.decide", {"approval_id": str(cor["approval_id"]), "approve": True})).status != "ok"
    assert (await ok(edge(w["giamdoc"]), "core.decide", approval_id=str(cor["approval_id"]), approve=True))["status"] == "executed"
    for r in (await q(w["truongkd"], "timekeeping.flagged", days=62, pending_only=True))["rows"]:
        if r["at"][:7] == f"{first:%Y-%m}":
            await ok(w["truongkd"], "timekeeping.review_punch", punch_id=r["id"], decision="accept", note="Gặp khách")
    again = await ok(w["truongkd"], "timekeeping.close_period", month=first.isoformat(), team_code="KD-A")
    assert again["people"] == 1 and not again["left"]
    r = await executor.run(w["nvan"], "timekeeping.request_correction", {"person": f"nvbinh@{dom}", "day": co["forgot_day"].isoformat(),
                                                                         "clock": "17:00", "direction": "out", "reason": "x"})
    assert r.status == "denied"                                                              # nhân viên không ghi thay người khác
    # gán cách chấm cho chính mình: DB chặn (không chỉ lệnh)
    with pytest.raises(psycopg.errors.CheckViolation):
        async with db.owner_tx() as conn:
            await conn.execute("""INSERT INTO timekeeping.enrolments (tenant_id, user_id, pattern_id, mode, valid_from, created_by)
                                  SELECT tenant_id, %s, id, 'exception', current_date, %s FROM timekeeping.patterns WHERE tenant_id = %s LIMIT 1""",
                               (w["nhansu"].user_id, w["nhansu"].user_id, co["tenant_id"]))


async def test_ip_rules_are_narrow_and_only_hr_sees_addresses(co: dict[str, Any]) -> None:
    w = co["who"]
    r = await executor.run(w["nhansu"], "timekeeping.upsert_site", {"code": "VP2", "name": "Rộng", "ip_ranges": "203.0.112.0/23"})
    assert r.status == "invalid" and "quá rộng" in r.error
    assert registry.OPS["timekeeping.enrol"].agent == "deny" and risk_of(registry.OPS["timekeeping.upsert_site"]) == "authority"
    lead = await q(w["truongkd"], "timekeeping.flagged", days=62)
    hr = await q(w["nhansu"], "timekeeping.flagged", days=62)
    assert lead["rows"] and all(not x["ip"] for x in lead["rows"] if x["user_id"] != w["truongkd"].user_id)
    assert any(x["ip"] for x in hr["rows"])
