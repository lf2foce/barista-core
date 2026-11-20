"""DỮ LIỆU DEV "ĐẦY ĐỦ" — lớp bồi thêm lên sàn mẫu (``--profile san-moi-gioi``) để máy dev có việc nào cũng bấm thử được (runbook 18, ``backend/scripts/dev_reset.sh``).

Khác ``san-moi-gioi-demo`` (9 tháng lịch sử cho câu chuyện phân tích, ~12 phút): lớp này gọn nhưng phủ NHIỀU MÀN, và mọi mốc tính theo NGÀY CHẠY — lead quá hạn / chưa
gọi / hẹn hôm nay, khách sắp đóng tiền, hẹn hôm nay, chấm công tháng này, kế hoạch đang trễ… ngày nào reset cũng có việc để làm.

Luật như ``seed.py``: dữ liệu GIẢ (tên ghép, SĐT dải 010…, ``0100200001`` trở lên); nghiệp vụ đi qua ĐÚNG lệnh bằng đúng vai; chỉ DẤU THỜI GIAN "đã trôi qua" (ngày
tạo lead, hạn đợt tiền…) chỉnh thẳng bằng owner. Phần của gói ngành do GÓI khai ở ``devmock(tid)`` của phần mẫu (``barista.seed_samples``) — lõi không biết tên gói.
Mỗi phần chạy độc lập, hỏng một phần thì báo và đi tiếp (dòng ``!``).

    python -m barista.seed_devmock              # cả hai phần; đã bồi rồi thì thôi (--force để chạy lại phần còn thiếu)
    python -m barista.seed_devmock --static     # phần nặng — dựng MỘT lần vào DB mẫu (dev_reset.sh)
    python -m barista.seed_devmock --daily      # phần rẻ tính theo hôm nay — sau mỗi lần nhân bản DB mẫu
"""

import asyncio
import random
import sys
import traceback
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from barista.db import db
from barista.kernel import events, executor
from barista.kernel.clock import VN, add_months, month_of, today
from barista.kernel.principal import Principal, system_principal
from barista.seed import DOMAIN, _owner_rows, run, samples, seed_training, who

MARK_EMAIL = f"luong@{DOMAIN}"   # người chỉ lớp này thêm — có rồi ⇒ đã bồi


async def q(p: Principal, name: str, /, **inp: Any) -> Any:
    return executor.raise_for(await executor.query(p, name, inp))


async def _packs(tid: UUID) -> set[str]:
    return {r["pack"] for r in await _owner_rows("SELECT pack FROM core.tenant_packs WHERE tenant_id = %s", (tid,))}


async def _sql(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Ghi bằng owner — CHỈ dấu thời gian 'đã trôi qua'; câu lệnh phải có RETURNING để lấy được hàng."""
    return await _owner_rows(sql, params)


# ── Người ──
EXTRA_PEOPLE = [   # (email, tên, vai, đội, trưởng đội) — chỉ thêm khi vai đã có (gói tương ứng đang bật)
    ("luong", "Đoàn Văn Tú", "payroll", "HCNS", False),
    ("chamcong", "Trịnh Thu Trang", "hr_officer", "HCNS", False),
    ("sale5", "Trương Quốc Kiên", "sale", "KD-B", False),
    ("sale6", "Doãn Thị Nhàn", "sale", "KD-C", False),
]

GIVEN = ["An", "Bình", "Chi", "Dũng", "Giang", "Hà", "Hải", "Hạnh", "Hiếu", "Hoa", "Huy", "Khang", "Lan", "Linh", "Long", "Mai", "Nam", "Nga", "Phong", "Phúc",
         "Quân", "Sơn", "Tâm", "Thảo", "Thắng", "Trang", "Trung", "Uyên", "Vân", "Việt", "Yến"]
SURNAME = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Vũ", "Đặng", "Bùi", "Đỗ", "Hồ", "Ngô", "Dương", "Lý", "Mai", "Trịnh", "Phan", "Võ", "Tạ"]
MIDDLE = ["Văn", "Thị", "Minh", "Thu", "Quốc", "Ngọc", "Hữu", "Thanh", "Gia", "Bảo", "Đức", "Mỹ"]


class Fake:
    """Tên + SĐT giả không trùng nhau (``0100200001`` trở lên: đầu 010 không tồn tại ở VN)."""

    def __init__(self, seed: int = 7, phone0: int = 100200001) -> None:
        self.rnd, self.n, self.phone0 = random.Random(seed), 0, phone0

    seen: set[str]

    def person(self) -> tuple[str, str]:
        """Tên chưa dùng (cả của dữ liệu nền — ``know``): tên + SĐT khách là khoá, trùng thì lệnh tạo lead báo xung đột."""
        self.seen = getattr(self, "seen", set())
        while True:
            name = f"{self.rnd.choice(SURNAME)} {self.rnd.choice(MIDDLE)} {self.rnd.choice(GIVEN)}"
            if name.lower() not in self.seen:
                break
        self.seen.add(name.lower())
        self.n += 1
        return name, f"0{self.phone0 + self.n}"

    def know(self, names: list[str]) -> None:
        self.seen = {n.lower() for n in names} | getattr(self, "seen", set())


async def people(tid: UUID, on: set[str]) -> str:
    have = {r["email"] for r in await _owner_rows(
        "SELECT u.email FROM core.memberships m JOIN core.users u ON u.id = m.user_id WHERE m.tenant_id = %s", (tid,))}
    roles = {r["key"] for r in await _owner_rows("SELECT key FROM core.roles WHERE tenant_id = %s", (tid,))}
    n = 0
    for local, name, role, team, lead in EXTRA_PEOPLE:
        if f"{local}@{DOMAIN}" in have or role not in roles:
            continue
        await run(system_principal(tid), "admin.invite_user", email=f"{local}@{DOMAIN}", name=name, role=role, team_code=team, is_lead=lead)   # như upgrade_sample
        n += 1
    return f"{n} người"


# ── Thẻ buổi sáng, lead, hẹn ──

async def brief(tid: UUID, f: Fake) -> str:
    """Mỗi sale đủ MỌI loại dòng của thẻ 'Khách cần gọi hôm nay': quá hạn gọi lại, chưa ai gọi, hẹn gọi hôm nay, khách nóng, sắp nguội — cộng khách đang đàm
    phán và một lead mất (cho cột chặng)."""
    t = today()
    # thứ khách quan tâm: lấy từ lead đã có (lõi không đọc bảng của gói ngành — 0187); chưa có thì mã chung của công ty mẫu
    offs = [r["offering_code"] for r in await _owner_rows(
        "SELECT DISTINCT offering_code FROM crm.leads WHERE tenant_id = %s AND offering_code IS NOT NULL ORDER BY 1", (tid,))] or ["GOI-A"]
    n = 0
    for k, local in enumerate(("sale1", "sale2", "sale3", "sale4", "sale5", "sale6")):
        try:
            p = await who(tid, local)
        except AssertionError:
            continue

        async def lead(note: str = "", p: Principal = p, k: int = k) -> str:
            nonlocal n
            name, phone = f.person()
            out = await run(p, "crm.create_lead", name=name, phone=phone, offering_code=offs[(n + k) % len(offs)], source=f.rnd.choice(["self", "referral", "telesales"]),
                            budget_vnd=f.rnd.choice([3, 4, 5, 6]) * 10**9, wishes=note)
            n += 1
            return str(out["lead_id"])

        for d in (1, 2, 4):   # quá hạn gọi lại (đã chăm sóc, hẹn gọi lại đã qua)
            lid = await lead("khách hẹn gọi lại")
            await run(p, "crm.log_activity", lead_id=lid, kind="call", outcome="connected", note="Khách xin thêm thời gian cân nhắc",
                      next_follow_up=(t - timedelta(days=d)).isoformat())
        for d in (2, 3):   # chưa ai gọi (lead về từ hôm trước)
            lid = await lead("lead về chưa ai gọi")
            await _sql("UPDATE crm.leads SET created_at = now() - make_interval(days => %s), updated_at = now() - make_interval(days => %s) WHERE id = %s RETURNING id",
                       (d, d, lid))
        for _ in range(2):   # hẹn gọi hôm nay
            lid = await lead("hẹn gọi hôm nay")
            await run(p, "crm.log_activity", lead_id=lid, kind="call", outcome="connected", note="Hẹn gọi lại hôm nay để chốt lịch xem nhà",
                      next_follow_up=t.isoformat())
        lid = await lead("khách nóng, vừa gặp")   # khách nóng: tiềm năng, nói chuyện trong 7 ngày
        await run(p, "crm.set_stage", lead_id=lid, stage="met")
        await run(p, "crm.log_activity", lead_id=lid, kind="meet", note="Gặp tại sàn, khách thích căn 2PN view hồ", next_follow_up=(t + timedelta(days=2)).isoformat())
        await _sql("UPDATE crm.activities SET at = now() - interval '3 days' WHERE lead_id = %s RETURNING id", (lid,))
        lid = await lead("sắp nguội")   # sắp nguội: chăm sóc lần cuối ≥ 10 ngày
        await run(p, "crm.log_activity", lead_id=lid, kind="zalo", note="Đã gửi bảng giá qua Zalo", next_follow_up=(t + timedelta(days=5)).isoformat())
        await _sql("UPDATE crm.leads SET last_activity_at = now() - interval '12 days' WHERE id = %s RETURNING id", (lid,))
        await _sql("UPDATE crm.activities SET at = now() - interval '12 days' WHERE lead_id = %s RETURNING id", (lid,))
        lid = await lead("đang đàm phán")
        await run(p, "crm.set_stage", lead_id=lid, stage="negotiating")
        await run(p, "crm.log_activity", lead_id=lid, kind="call", outcome="connected", note="Khách đã chốt căn, đang hỏi chiết khấu",
                  next_follow_up=(t + timedelta(days=1)).isoformat())
        lid = await lead("không còn nhu cầu")
        await run(p, "crm.set_stage", lead_id=lid, stage="lost", lost_reason="Khách đã mua dự án khác")
    await events.drain()
    return f"{n} lead cho thẻ buổi sáng của 6 sale"


async def appointments(tid: UUID) -> str:
    """Lịch hẹn HÔM NAY + ngày mai + tuần này cho mọi sale (``seed._seed_appointments`` chỉ làm cho sale1)."""
    rows = await _owner_rows(
        """SELECT l.id, u.email FROM crm.leads l JOIN core.users u ON u.id = l.owner_id WHERE l.tenant_id = %s AND l.stage IN ('lead', 'prospect')
           ORDER BY l.created_at DESC""", (tid,))
    by: dict[str, list[Any]] = {}
    for r in rows:
        by.setdefault(r["email"], []).append(r["id"])
    now = datetime.now(VN)
    n = 0
    for local in ("sale2", "sale3", "sale4", "sale5", "sale6"):
        ids = by.get(f"{local}@{DOMAIN}", [])
        if len(ids) < 3:
            continue
        p = await who(tid, local)
        for lid, (kind, day, hour) in zip(ids, (("visit", 0, 15), ("meet", 0, 17), ("visit", 1, 9), ("call", 3, 10)), strict=False):
            when = (now + timedelta(days=day)).replace(hour=hour, minute=0, second=0, microsecond=0)
            if when <= now:   # giờ hôm nay đã qua ⇒ dời sang giờ gần nhất còn lại
                when = now + timedelta(hours=1)
            await run(p, "crm.schedule", lead_id=lid, kind=kind, day=when.date().isoformat(), time=f"{when:%H}:00", place="Phòng trưng bày của công ty")
            n += 1
    return f"{n} lịch hẹn hôm nay / ngày mai / tuần này"


async def installments_due(tid: UUID) -> str:
    """Đợt đóng tiền của khách SẮP ĐẾN HẠN (1–7 ngày tới) + vài đợt đã quá hạn — màn 'khách sắp đóng tiền' của từng sale và việc thu của kế toán."""
    rows = await _owner_rows(
        """SELECT i.id FROM deals.installments i JOIN deals.deals d ON d.id = i.deal_id WHERE i.tenant_id = %s AND i.paid_vnd = 0 AND d.status IN ('signed', 'deposited')
           ORDER BY d.code, i.seq""", (tid,))
    t = today()
    n = 0
    for r, d in zip(rows, [1, 2, 3, 3, 5, 6, 7, -3, -9, -20], strict=False):
        await _sql("UPDATE deals.installments SET due_on = %s WHERE id = %s RETURNING id", (t + timedelta(days=d), r["id"]))
        n += 1
    return f"{n} đợt đóng tiền dời về quanh hôm nay (1–7 ngày tới, 3 đợt quá hạn)"


# ── Duyệt, kế hoạch, tự động hoá ──

async def approvals(tid: UUID, on: set[str]) -> str:
    """Phiếu CHỜ DUYỆT ở các chuỗi của lõi: đề nghị chi nghĩa vụ (nghỉ phép, làm thêm ở phần chấm công). Phiếu của gói ngành (tạm ứng hoa hồng, ngân sách…)
    do gói góp ở hook ``devmock`` của phần mẫu — lõi không nêu thao tác của gói ngành (0187)."""
    n = 0
    ketoan = await who(tid, "ketoan")
    pay = await _owner_rows("SELECT id FROM acct.payables WHERE tenant_id = %s AND kind = 'payable' ORDER BY id LIMIT 2", (tid,))
    for r in pay:
        try:
            await run(ketoan, "acct.request_payment", payable_id=str(r["id"]), amount_vnd=5_000_000, recipient_instruction="0101000300 — Ngân hàng Mẫu A — CONG TY MAU",
                      reason="Thanh toán đợt 1 theo hoá đơn (mẫu)")
            n += 1
        except Exception as e:
            print(f"    (đề nghị chi: {str(e)[:90]})")
    return f"{n} phiếu chờ duyệt (tạm ứng, đề nghị chi)"


async def plans(tid: UUID) -> str:
    """Kế hoạch thứ hai cho dòng thời gian (Gantt): mở bán đợt 2 — 4 giai đoạn, việc chờ nhau, xong / đang làm / chưa làm (kế hoạch đầu ở ``seed.seed_plan``)."""
    title = "Mở bán Hồ Sen đợt 2 — kế hoạch 8 tuần"
    if await _owner_rows("SELECT 1 FROM flow.plans WHERE tenant_id = %s AND title = %s", (tid, title)):
        return "kế hoạch Gantt đã có"
    t = today()
    gd = await who(tid, "giamdoc")
    spec = [   # (việc, người, giai đoạn, chờ, bắt đầu (ngày so với hôm nay), hạn, trạng thái)
        ("Chốt chính sách bán hàng đợt 2 với CĐT", "ketoan", "Chuẩn bị", "", -21, -14, "done"),
        ("Duyệt ngân sách marketing đợt 2", "ketoan", "Chuẩn bị", "Chốt chính sách bán hàng đợt 2 với CĐT", -14, -9, "done"),
        ("Bộ nhận diện + tờ giới thiệu đợt 2", "mkt2", "Chuẩn bị", "Chốt chính sách bán hàng đợt 2 với CĐT", -12, -3, "done"),
        ("Landing page + form nhận khách", "digital", "Truyền thông", "Bộ nhận diện + tờ giới thiệu đợt 2", -4, 5, "doing"),
        ("Chạy quảng cáo Facebook / Google", "mkt3", "Truyền thông", "Landing page + form nhận khách", 3, 21, "todo"),
        ("Đào tạo đội kinh doanh về chính sách mới", "truonga", "Truyền thông", "Chốt chính sách bán hàng đợt 2 với CĐT", -2, 3, "doing"),
        ("Sự kiện giới thiệu căn mẫu", "hcns", "Bán hàng", "Đào tạo đội kinh doanh về chính sách mới", 4, 17, "todo"),
        ("Mở giữ chỗ cho khách VIP", "truongb", "Bán hàng", "Sự kiện giới thiệu căn mẫu", 18, 24, "todo"),
        ("Ký HĐMB đợt đầu", "salesadmin", "Bán hàng", "Mở giữ chỗ cho khách VIP", 25, 40, "todo"),
        ("Đối chiếu công nợ phí với CĐT", "ketoan", "Nghiệm thu", "Ký HĐMB đợt đầu", 41, 52, "todo"),
    ]
    out = await run(gd, "flow.propose_plan", title=title, team_code="KD-A", due_on=(t + timedelta(days=56)).isoformat(),
                    goal="Đạt 40 giao dịch giữ chỗ trong 8 tuần; mọi hồ sơ ký đủ giấy tờ trước khi đối chiếu phí với chủ đầu tư.",
                    tasks=[{"title": a, "assignee_email": f"{b}@{DOMAIN}", "phase": c, "after": d, "start_on": (t + timedelta(days=max(e, 0))).isoformat(),
                            "due_on": (t + timedelta(days=max(g, 1))).isoformat()} for a, b, c, d, e, g, _ in spec])
    pid = out["plan_id"]
    for a, b, _c, _d, e, g, status in spec:
        task = (await _owner_rows("SELECT id FROM flow.tasks WHERE plan_id = %s AND title = %s", (pid, a)))[0]["id"]
        # ngày bắt đầu / hạn trong quá khứ chỉnh bằng owner (lệnh không cho đặt hạn đã qua) — chỉ DẤU THỜI GIAN
        await _sql("UPDATE flow.tasks SET start_on = %s, due_on = %s, created_at = now() - interval '22 days' WHERE id = %s RETURNING id",
                   (t + timedelta(days=e), t + timedelta(days=g), task))
        if status != "todo":
            await run(await who(tid, b), "flow.set_task", task_id=str(task), status=status)
            if status == "done":
                await _sql("UPDATE flow.tasks SET done_at = %s WHERE id = %s RETURNING id", (datetime.combine(t + timedelta(days=g), datetime.min.time()), task))
    await _sql("UPDATE flow.plans SET created_at = now() - interval '22 days' WHERE id = %s RETURNING id", (pid,))
    return f"kế hoạch Gantt: {len(spec)} việc, 4 giai đoạn"


async def automations(tid: UUID) -> str:
    admin = await who(tid, "admin")
    n = 0
    for name, trigger, cond, msg, team in (
            ("Lead quảng cáo mới ⇒ báo team Digital", "crm.lead_created", "Nguồn = ads", "Lead mới {Nguồn} — chia trong 15 phút", "DIG"),):
        try:
            await run(admin, "flow.save_automation", name=name, trigger=trigger, conditions=cond, action="notify", team_code=team, message=msg, trial=False)
            n += 1
        except Exception as e:
            print(f"    (luật {name}: {str(e)[:120]})")
    try:
        await run(await who(tid, "truonga"), "flow.request_team", title="Gọi mời 30 khách cũ tham dự mở bán đợt 2", team_code="MKT",
                  due_on=(today() + timedelta(days=6)).isoformat())
        n += 1
    except Exception as e:
        print(f"    (nhờ phòng: {str(e)[:120]})")
    return f"{n} luật tự động hoá / việc nhờ phòng"


async def leadflow_history(tid: UUID) -> str:
    """Lịch sử chia lead nạp từ hệ khác (``leadflow.import_history``): vài lead từng qua tay 2 người, có thu hồi vì trễ hạn gọi."""
    admin = await who(tid, "admin")
    leads = await _owner_rows("""SELECT l.id, u.email FROM crm.leads l JOIN core.users u ON u.id = l.owner_id WHERE l.tenant_id = %s AND l.stage IN ('lead', 'prospect', 'deal')
                                  ORDER BY l.created_at LIMIT 8""", (tid,))
    now = datetime.now(VN)
    n = 0
    for i, r in enumerate(leads):
        a, b = f"sale{1 + i % 3}@{DOMAIN}", r["email"]
        t0 = now - timedelta(days=30 + i * 3)
        rows: list[dict[str, Any]] = [
            {"kind": "held", "user_email": a, "at": t0.isoformat(), "until": (t0 + timedelta(days=4)).isoformat(), "started": "rule", "ended": "recalled"},
            {"kind": "recalled", "user_email": a, "at": (t0 + timedelta(days=4)).isoformat()},
            {"kind": "held", "user_email": b, "at": (t0 + timedelta(days=4)).isoformat(), "started": "manual"}]
        for j, row in enumerate(rows):
            try:
                await run(admin, "leadflow.import_history", lead_id=str(r["id"]), origin="he_cu", ext_ref=f"dev-{i}-{j}", **row)
                n += 1
            except Exception as e:
                print(f"    (lịch sử chia lead: {str(e)[:120]})")
                return f"{n} dòng lịch sử chia lead (dừng sớm)"
    return f"{n} dòng lịch sử chia lead"


# ── Chấm công + lương (gói timekeeping / pay) ──

STAFF = [("sale1", 15_000_000), ("sale2", 13_000_000), ("sale3", 12_000_000), ("hcns", 14_000_000), ("nhansu", 11_000_000)]
OFFICE_IP, OUTSIDE_IP = "203.0.113.5", "198.51.100.23"   # dải TÀI LIỆU (TEST-NET) — không phải IP của ai


async def _party(tid: UUID, email: str) -> str:
    return str((await _owner_rows("SELECT p.id FROM parties.parties p JOIN core.users u ON u.id = p.user_id WHERE p.tenant_id = %s AND u.email = %s", (tid, email)))[0]["id"])


async def timekeeping_pay(tid: UUID, on: set[str]) -> str:
    """Chấm công theo IP văn phòng (tháng trước đã chốt; tháng này tới hôm qua có giờ muộn / ngoài mạng / quên bấm ra), nghỉ phép + làm thêm, rồi Lương: hợp đồng,
    người phụ thuộc, lương tháng theo công, kỳ nháp + kỳ chạy bóng — như ``seed_pay.office`` nhưng trên công ty mẫu này."""
    from barista.modules.timekeeping.common import holidays
    from barista.modules.timekeeping.sheet import working_days
    if "timekeeping" not in on:
        return "bỏ qua: gói timekeeping tắt"
    hr, chu = await who(tid, "chamcong"), await who(tid, "admin")
    lead = await who(tid, "truonga")
    await run(hr, "timekeeping.upsert_site", code="VP", name="Văn phòng chính (mẫu)", ip_ranges="203.0.113.0/28")
    await run(hr, "timekeeping.upsert_pattern", code="HC", name="Giờ hành chính", weekdays="1,2,3,4,5", starts="08:00", ends="17:00", break_minutes=60, grace_minutes=5)
    await run(hr, "timekeeping.install_leave_types")
    first = add_months(month_of(today()), -1)
    for local, _ in STAFF:
        await run(hr, "timekeeping.enrol", person=f"{local}@{DOMAIN}", pattern_code="HC", site_code="VP", mode="punch", valid_from=first.isoformat())
    await run(chu, "timekeeping.enrol", person=f"giamdoc@{DOMAIN}", pattern_code="HC", site_code="VP", mode="exception", valid_from=first.isoformat())
    await run(hr, "timekeeping.grant_annual_leave", year=first.year)
    if first.year != today().year:
        await run(hr, "timekeeping.grant_annual_leave", year=today().year)
    days = working_days(first, today() - timedelta(days=1), weekdays=[1, 2, 3, 4, 5], holidays=holidays(first, today()))
    prev = [d for d in days if d < month_of(today())]
    ot_day, leave_day, unpaid_day, forgot_day = prev[-2], prev[-4], prev[-6], prev[-7]
    outside = {("sale2", prev[-3]), ("sale2", prev[-8]), ("sale2", days[-2]), ("sale1", days[-1])}
    for i, d in enumerate(days):
        for j, (local, _) in enumerate(STAFF):
            if (local, d) in (("sale1", leave_day), ("sale2", unpaid_day)):
                continue
            arrive = f"07:{50 + (i + j) % 9:02d}" if not (local == "hcns" and d == prev[-5]) else "08:25"
            leave_at = "19:10" if (local, d) == ("sale1", ot_day) else f"17:{(i * 7 + j * 3) % 30:02d}"
            ip = OUTSIDE_IP if (local, d) in outside else OFFICE_IP
            await run(hr, "timekeeping.import_punch", person=f"{local}@{DOMAIN}", at=f"{d:%d/%m/%Y} {arrive}", direction="in", ip=ip)
            if not (local == "sale2" and d in (days[-1], forgot_day)):
                await run(hr, "timekeeping.import_punch", person=f"{local}@{DOMAIN}", at=f"{d:%d/%m/%Y} {leave_at}", direction="out", ip=ip)
    for who_, kind, day in (("sale1", "PHEP_NAM", leave_day), ("sale2", "KHONG_LUONG", unpaid_day)):
        got = await run(await who(tid, who_), "timekeeping.request_leave", leave_type=kind, from_day=day.isoformat(), reason="Việc gia đình (mẫu)")
        await run(lead, "core.decide", approval_id=got["approval_id"], approve=True, note="")
    got = await run(await who(tid, "sale1"), "timekeeping.request_overtime", day=ot_day.isoformat(), hours=2, reason="Hoàn thiện hồ sơ khách gấp (mẫu)")
    await run(lead, "core.decide", approval_id=got["approval_id"], approve=True, note="")
    friday = today() + timedelta(days=(4 - today().weekday()) % 7 or 7)   # đơn nghỉ đang CHỜ duyệt
    await run(await who(tid, "sale2"), "timekeeping.request_leave", leave_type="PHEP_NAM", from_day=friday.isoformat(), reason="Về quê (mẫu)")
    got = await run(await who(tid, "sale2"), "timekeeping.request_correction", day=forgot_day.isoformat(), clock="17:20", direction="out", reason="Quên bấm ra ca (mẫu)")
    await run(lead, "core.decide", approval_id=got["approval_id"], approve=True, note="")
    flagged = await q(lead, "timekeeping.flagged", days=62, pending_only=True)
    for r in flagged["rows"]:
        if r["at"][:7] == f"{first:%Y-%m}":
            await run(lead, "timekeeping.review_punch", punch_id=r["id"], decision="accept", note="Gặp khách theo lịch đã báo (mẫu)")
    closed = await run(hr, "timekeeping.close_period", month=first.isoformat(), confirm_days=5)
    msg = f"chấm công {len(days)} ngày × {len(STAFF)} người, đã chốt tháng trước ({closed['people']} người)"
    if "pay" not in on:
        return msg
    return msg + "; " + await payroll(tid, first)


async def payroll(tid: UUID, first: date) -> str:
    luong = await who(tid, "luong")
    for local, salary in STAFF:
        await run(luong, "hr.set_terms", party_id=await _party(tid, f"{local}@{DOMAIN}"), contract_kind="indefinite", insured_vnd=salary, work_region="I",
                  effective_from=date(today().year, 1, 1).isoformat(), note="Hợp đồng lao động không thời hạn (mẫu)")
    await run(luong, "hr.add_dependant", party_id=await _party(tid, f"sale1@{DOMAIN}"), name="Nguyễn Minh Khang", relation="child",
              registered_from=date(today().year, 1, 1).isoformat(), note="Đã nộp tờ khai đăng ký (mẫu)")
    for c in ({"code": "LUONG", "label": "Lương theo ngày công", "calculator": "prorata", "expense_account": "expense:payroll"},
              {"code": "LAM_THEM", "label": "Làm thêm giờ", "calculator": "overtime", "expense_account": "expense:payroll"}):
        await run(luong, "pay.upsert_component", **c)
    for local, salary in STAFF:
        await run(luong, "pay.set_rate", effective_from=date(today().year, 1, 1).isoformat(), component="LUONG", unit="month",
                  party_id=await _party(tid, f"{local}@{DOMAIN}"), amount_vnd=salary)
    await run(luong, "pay.draft_run", period=first.isoformat(), pay_date=today().isoformat())
    await run(luong, "pay.draft_run", period=first.isoformat(), pay_date=today().isoformat(), kind="shadow")
    return f"lương: kỳ {first:%m/%Y} nháp + kỳ chạy bóng"


async def enable_pack(tid: UUID, pack: str) -> None:
    """Bật thêm một gói cho công ty (như chủ tài khoản bấm ở Gói & ứng dụng; quyền dùng gói cấp trước như ``seed.create_tenant``)."""
    await _sql("INSERT INTO core.tenant_entitlements (tenant_id, pack, granted_by, note) VALUES (%s, %s, 'seed', 'dữ liệu dev') ON CONFLICT DO NOTHING RETURNING pack", (tid, pack))
    await run(await who(tid, "admin"), "admin.set_pack", pack=pack, enabled=True)
    # quyền mặc định của gói MỞ RỘNG thẩm quyền (vd Phụ trách lương ghi điều kiện hợp đồng) không tự áp khi bật gói sau — như upgrade_sample, đặt qua quyền hệ thống
    from barista import presets
    from barista.kernel import registry
    mine = set(getattr(registry.PACKS[pack], "roles", None) or {})   # vai do chính gói khai
    have = {(r["role"], r["resource"]) for r in await _owner_rows("SELECT role, resource FROM core.grants WHERE tenant_id = %s", (tid,))}
    roles = {r["key"] for r in await _owner_rows("SELECT key FROM core.roles WHERE tenant_id = %s", (tid,))}
    for role, res, scope, actions in presets.grants():
        if role in mine and role in roles and (role, res) not in have:
            await run(system_principal(tid), "admin.set_grant", role=role, resource=res, scope=scope, actions=actions.split())


async def training(tid: UUID, on: set[str]) -> str:
    if "training" not in on:
        return "bỏ qua: gói training tắt"
    if await _owner_rows("SELECT 1 FROM training.courses WHERE tenant_id = %s LIMIT 1", (tid,)):
        return "khoá học đã có"
    await seed_training(tid)
    return "1 khoá học (từ chính sách bán hàng), giao cho team A"


# ── Chi phí 3 tháng + quảng cáo theo mẫu / ngày ──

async def expenses(tid: UUID) -> str:
    """Chi phí marketing / văn phòng của 3 tháng gần nhất (một số gắn dự án = đối tượng chi phí) — cho Kết quả kinh doanh và số THỰC TẾ của ngân sách."""
    ketoan, t = await who(tid, "ketoan"), today()
    for code, label in (("HSR", "Hồ Sen Residence"), ("DTP", "Đồi Thông Park")):
        try:
            await run(ketoan, "acct.set_cost_object", code=code, label=label, kind="Dự án")
        except Exception as e:
            print(f"    (đối tượng chi phí {code}: {str(e)[:100]})")
    n = 0
    for back in (3, 2, 1, 0):
        first = add_months(month_of(t), -back)
        for i, (cat, amount, obj, memo) in enumerate((("marketing", 62_000_000, "HSR", "Quảng cáo Facebook / Google Hồ Sen"), ("marketing", 41_000_000, "DTP", "Quảng cáo TikTok Đồi Thông"),
                                                     ("other", 33_000_000, "", "Thuê văn phòng"), ("other", 12_500_000, "", "Điện, nước, internet"))):
            billed = first.replace(day=min(5 + i * 6, 28))
            if billed > t:   # tháng này: chỉ phiếu đã tới ngày
                continue
            try:
                await run(ketoan, "acct.record_expense", category=cat, amount_vnd=amount + back * 1_500_000, vat_rate="10", cost_object=obj, supplier="Nhà cung cấp mẫu",
                          supplier_tax_code="0100000093", doc_no=f"DV-{first:%y%m}-{i}", bill_on=billed.isoformat(),
                          bank_ref=f"FT{first:%y%m}DV{i}", memo=memo)
                n += 1
            except Exception as e:
                print(f"    (chi phí {memo}: {str(e)[:100]})")
    return f"{n} phiếu chi phí 4 tháng"


async def ads(tid: UUID, f: Fake) -> str:
    """Quảng cáo trong từng chiến dịch (3 mẫu) với số theo NGÀY 6 tuần gần nhất — cho Hiệu quả quảng cáo / Mẫu ra khách. Chi phí và lead nền tảng của mỗi ngày CHIA từ số
    chiến dịch đã có (``mkt.spend`` của dữ liệu nền) theo tỷ lệ 50 / 30 / 20 — tổng ngày không đổi nên số của câu chuyện nền giữ nguyên; chiến dịch chưa có số ngày thì sinh số mới.
    Ghi qua mkt.record_ad_day (tổng ngày của chiến dịch dựng lại từ các quảng cáo)."""
    mkt, t = await who(tid, "mkt"), today()
    camps = await _owner_rows("SELECT id, code, name FROM mkt.campaigns WHERE tenant_id = %s ORDER BY code", (tid,))
    shares = (50, 30, 20)
    n = 0
    for c in camps:
        have = {r["day"]: r for r in await _owner_rows("SELECT day, amount_vnd, leads_platform FROM mkt.spend WHERE campaign_id = %s AND day >= %s",
                                                        (c["id"], t - timedelta(days=42)))}
        for ai, (kind, title) in enumerate((("image", "Căn 2PN view hồ — giá từ 4,6 tỷ"), ("video", "Tham quan căn hộ mẫu"), ("carousel", "Ưu đãi chiết khấu 2% tháng này"))):
            code = f"{c['code'].replace('-', '')}{ai + 1:02d}"[:40]
            await run(mkt, "mkt.upsert_ad", campaign_code=c["code"], code=code, name=f"{c['name']} · mẫu {ai + 1}", adset_code=f"{code}S", adset_name=f"Nhóm {ai + 1}",
                      title=title, body="Nhận bảng giá và chính sách bán hàng mới nhất — để lại số điện thoại.", media_type=kind)
        for d in range(42, 0, -1):
            day = t - timedelta(days=d)
            row = have.get(day)
            total = int(row["amount_vnd"]) if row else f.rnd.randint(1_500_000, 4_500_000)
            leads = int(row["leads_platform"]) if row else f.rnd.choice([0, 1, 1, 2, 3])
            for ai, sh in enumerate(shares):
                code = f"{c['code'].replace('-', '')}{ai + 1:02d}"[:40]
                spend = total * sh // 100 + (total - sum(total * x // 100 for x in shares) if ai == 0 else 0)
                clicks = int(spend / f.rnd.randint(2300, 4200))
                lead = leads * sh // 100 + (leads - sum(leads * x // 100 for x in shares) if ai == 0 else 0)
                await run(mkt, "mkt.record_ad_day", ad_code=code, day=day.isoformat(), amount_vnd=spend, impressions=clicks * f.rnd.randint(28, 55), clicks=clicks,
                          leads_platform=lead, inbox=f.rnd.choice([0, 1, 2]))
                n += 1
    return f"{len(camps)} chiến dịch × 3 mẫu × 42 ngày = {n} dòng số quảng cáo (chia từ chi phí chiến dịch đã có)"


# ── Chạy ──

async def build(tid: UUID, mode: str = "all") -> list[str]:
    """Bồi dữ liệu dev vào công ty mẫu ``tid`` — trả các dòng báo cáo (dòng ``!`` = phần hỏng). ``mode``: ``static`` = phần nặng, dựng MỘT lần vào DB mẫu (``dev_reset.sh``
    nhân bản DB mẫu cho mỗi lần reset); ``daily`` = phần RẺ tính theo hôm nay (lead cho thẻ buổi sáng, lịch hẹn, đợt đóng tiền), chạy sau MỖI lần nhân bản; ``all`` = cả hai."""
    on = await _packs(tid)
    f = Fake(seed=11, phone0=100210001) if mode == "daily" else Fake(seed=7)   # hai dải SĐT riêng: phần tĩnh (khách của giao dịch) và phần hôm nay không đụng nhau
    f.know([r["name"] for r in await _owner_rows("SELECT name FROM crm.customers WHERE tenant_id = %s", (tid,))])
    lines: list[str] = []

    async def step(name: str, fn: Callable[[], Awaitable[str | None]]) -> None:
        t0 = asyncio.get_event_loop().time()
        try:
            got = await fn()
            lines.append(f"  {name}: {got or 'xong'} ({asyncio.get_event_loop().time() - t0:.0f}s)")
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            lines.append(f"! {name}: HỎNG — {type(e).__name__}: {str(e)[:200]}")

    static, daily = mode in ("all", "static"), mode in ("all", "daily")
    if static:
        await step("người thêm", lambda: people(tid, on))
        kit = SimpleNamespace(run=run, who=who, sql=_owner_rows, fake=f, domain=DOMAIN)   # gói chỉ dùng barista.api: lõi đưa sẵn dụng cụ nạp mẫu
        await step("chi phí", lambda: expenses(tid))
        for key, mod in samples().items():   # phần dev của gói ngành đang bật (lõi không biết tên gói)
            if key in on and (fn := getattr(mod, "devmock", None)) is not None:
                await step(f"gói {key}", lambda fn=fn: fn(tid, kit))   # type: ignore[misc]
        await step("quảng cáo theo ngày", lambda: ads(tid, f))
        await step("phiếu chờ duyệt", lambda: approvals(tid, on))
        await step("kế hoạch Gantt", lambda: plans(tid))
        await step("tự động hoá / nhờ phòng", lambda: automations(tid))
        await step("lịch sử chia lead", lambda: leadflow_history(tid))
        # Lương bật SAU hoa hồng: công ty bật gói Lương thì hoa hồng của người hưởng cá nhân đi vào sổ công của Lương (0150 §9), KHÔNG vào ví — bật trước thì ví hoa hồng,
        # rút / ứng của sàn mẫu rỗng. Hoa hồng đã về ví ở trên giữ nguyên; từ đây hoa hồng mới phát sinh mới đi qua kỳ lương.
        if "pay" not in on:
            await step("bật gói Lương", lambda: enable_pack(tid, "pay"))
            on = await _packs(tid)
            await step("người của Lương", lambda: people(tid, on))   # vai payroll chỉ có từ khi bật gói
        await step("chấm công + lương", lambda: timekeeping_pay(tid, on))
        await step("đào tạo", lambda: training(tid, on))
    if daily:   # rẻ, tính theo hôm nay — chạy sau MỖI lần nhân bản DB mẫu
        await step("lead cho thẻ buổi sáng", lambda: brief(tid, f))
        await step("lịch hẹn", lambda: appointments(tid))
        await step("đợt đóng tiền", lambda: installments_due(tid))
    await events.drain()
    return lines


async def main() -> None:
    from barista.config import settings
    if settings.env == "production":
        raise SystemExit("Dừng: BARISTA_ENV=production — không bồi dữ liệu dev")
    await db.open(settings.database_url, settings.owner_database_url)
    try:
        rows = await _owner_rows("SELECT id, name FROM core.tenants ORDER BY created_at LIMIT 2", ())
        if len(rows) != 1:
            raise SystemExit(f"Dừng: cần đúng một công ty mẫu trong DB, thấy {len(rows)}")
        tid = rows[0]["id"]
        mode = "static" if "--static" in sys.argv else "daily" if "--daily" in sys.argv else "all"
        if mode != "daily" and await _owner_rows("SELECT 1 FROM core.users WHERE email = %s", (MARK_EMAIL,)) and "--force" not in sys.argv:
            print("Đã bồi dữ liệu dev (có luong@…) — chạy seed --reset để làm lại, hoặc --force để chạy lại từng phần")
            return
        print(f"Bồi dữ liệu dev ({mode}) vào {rows[0]['name']}:")
        out = await build(tid, mode)
        print("\n".join(out))
        if any(x.startswith("!") for x in out):
            print("CẢNH BÁO: có phần hỏng (dòng !) — xem stderr")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
