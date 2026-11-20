"""Dữ liệu mẫu gói Lương (0150 lát 1 "Trả theo công") — DỮ LIỆU GIẢ: tên người, tên công ty bịa; SĐT đầu 010.

Nhiều ngành, cùng một gói ``pay`` (không code riêng ngành nào):
* CƠ SỞ DỊCH VỤ: kỹ thuật viên hưởng theo lượt dịch vụ, nhập từ bảng Excel của cơ sở qua Nhập dữ liệu (đúng lệnh ``pay.import_work``).
* VĂN PHÒNG (0150 lát 3–4, ``office``): phòng Nhân sự riêng, chấm công theo IP văn phòng (lịch sử tháng trước + tháng này, vài lần ngoài mạng gắn cờ,
  một lần đi muộn, một ngày thiếu giờ ra), nghỉ phép năm / không lương, làm thêm có duyệt; tháng trước đã chốt bảng công và đẩy sang Lương; lương
  tháng theo ngày công + bảo hiểm + thuế luỹ tiến — kỳ thật DỪNG chờ kế toán xác minh từng quy tắc, kỳ chạy bóng có số.

Mỗi công ty dịch vụ có một kỳ nháp (phiếu cộng tác viên DỪNG: quy tắc khấu trừ 10% "chờ kế toán xác minh") và một kỳ chạy bóng (tính cả quy tắc chưa xác
minh, mọi số mang dấu chưa xác minh — không chốt, không chi, người lao động không thấy). Test dùng lại các hàm dựng (``tests/test_pay.py``).
Gói ngành đã cài góp thêm công ty mẫu của ngành nó (nguồn công riêng — họ ``work_log``; vd sàn phân phối: cộng tác viên hưởng hoa hồng) qua ``COMPANIES["pay-demo"]`` của phần mẫu
(``barista.seed.samples``); các hàm dựng chung ở đây (``OFFICE``, ``_pay_setup``, ``_runs``, ``_people``, ``_party_of``) là dụng cụ cho chúng.
Chạy: ``python -m barista.seed --reset --profile pay-demo`` với DB riêng.
"""

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from barista.db import db
from barista.kernel import imports
from barista.kernel.clock import add_months, month_of, today
from barista.kernel.principal import Principal
from barista.seed import create_tenant, run, who


async def q(p: Principal, name: str, /, **inp: Any) -> Any:
    from barista.kernel import executor

    return executor.raise_for(await executor.query(p, name, inp))

SERVICE_DOMAIN, OFFICE_DOMAIN = "luong-dichvu.demo", "luong-vanphong.demo"
OFFICE = [("giamdoc", "Lê Hồng Nhung", "director", "HQ", True), ("ketoan", "Phan Thu Hằng", "accountant", "KT", True),
          ("luong", "Đoàn Văn Tú", "payroll", "HCNS", False)]


def _year_start() -> str:
    return date(today().year, 1, 1).isoformat()


async def _people(tid: UUID, domain: str, locals_: tuple[str, ...]) -> dict[str, Principal]:
    return {k: await who(tid, k, domain) for k in locals_}


async def _party_of(tid: UUID, email: str) -> str:
    """Định danh của một thành viên (mỗi thành viên có một — mig 021)."""
    async with db.owner.connection() as conn:
        cur = await conn.execute("SELECT p.id FROM parties.parties p JOIN core.users u ON u.id = p.user_id WHERE p.tenant_id = %s AND u.email = %s",
                                 (tid, email))
        row = await cur.fetchone()
    assert row is not None, email
    return str(row["id"])


async def _pay_setup(luong: Principal, collaborators: list[str], components: list[dict[str, Any]], rates: list[dict[str, Any]]) -> None:
    for email in collaborators:   # cộng tác (không hợp đồng lao động) — khấu trừ 10% theo lần chi khi quy tắc đã xác minh
        await run(luong, "hr.set_terms", party_id=await _party_of(luong.tenant_id, email), contract_kind="service", effective_from=_year_start(),
                  note="Hợp đồng dịch vụ (mẫu)")
    for c in components:
        await run(luong, "pay.upsert_component", **c)
    for r in rates:
        await run(luong, "pay.set_rate", effective_from=_year_start(), **r)


async def _runs(luong: Principal) -> dict[str, Any]:
    """Kỳ tháng này: một kỳ nháp (phiếu cộng tác viên dừng chờ xác minh) + một kỳ chạy bóng."""
    period = month_of(today()).isoformat()
    real = await run(luong, "pay.draft_run", period=period, pay_date=today().isoformat())
    shadow = await run(luong, "pay.draft_run", period=period, pay_date=today().isoformat(), kind="shadow")
    return {"run_id": real["run_id"], "shadow_id": shadow["run_id"]}


SERVICE_ROWS = [("ktvlan", "goi_dau", 6), ("ktvlan", "massage", 3), ("ktvlan", "cham_soc_da", 2), ("ktvmai", "massage", 5), ("ktvmai", "goi_dau", 4)]


async def service_shop(domain: str = SERVICE_DOMAIN, name: str = "Cơ sở Chăm sóc Mẫu Hoa (dữ liệu giả)", *, runs: bool = True) -> dict[str, Any]:
    """Kỹ thuật viên cộng tác hưởng theo lượt; lượt dịch vụ nhập từ bảng Excel của cơ sở qua Nhập dữ liệu — đúng lệnh như nhập tay, từng dòng."""
    tid = await create_tenant(name, domain, people=[("chu", "Kiều Bảo Ngọc", "admin", "QT", True), *OFFICE,
                                                     ("ktvlan", "Lý Thị Lan", "staff", "HCNS", False), ("ktvmai", "Vương Ngọc Mai", "staff", "HCNS", False)],
                              packs=("pay",), about="cơ sở dịch vụ chăm sóc (mẫu): kỹ thuật viên hưởng theo lượt")
    luong = await who(tid, "luong", domain)
    await _pay_setup(luong, [f"ktvlan@{domain}", f"ktvmai@{domain}"],
                     [{"code": "TRA_THEO_LUOT", "label": "Thù lao theo lượt dịch vụ", "calculator": "rate_x_qty", "expense_account": "expense:payroll"},
                      {"code": "THUONG", "label": "Thưởng", "calculator": "manual", "expense_account": "expense:payroll"}],
                     [{"component": "TRA_THEO_LUOT", "unit": "piece", "role_key": "goi_dau", "amount_vnd": 60_000},
                      {"component": "TRA_THEO_LUOT", "unit": "piece", "role_key": "massage", "amount_vnd": 150_000},
                      {"component": "TRA_THEO_LUOT", "unit": "piece", "role_key": "cham_soc_da", "amount_vnd": 120_000}])
    day = today() - timedelta(days=1)
    rows = [{"Người": f"{who_}@{domain}", "Ngày làm": f"{day:%d/%m/%Y}", "Đơn vị": "Lượt / sản phẩm", "Số lượng": n, "Loại việc": kind,
             "Mã chứng từ": f"PDV-{day:%m%d}-{i:03d}"} for i, (who_, kind, n) in enumerate(SERVICE_ROWS, 1)]
    got = await imports.run_import(luong, "pay.import_work", rows, dry_run=False)
    assert got.ok and got.output["failed"] == 0, got.output
    out: dict[str, Any] = {"tenant_id": tid, "domain": domain, "imported": got.output["ok"],
                           "who": await _people(tid, domain, ("chu", "giamdoc", "ketoan", "luong", "ktvlan", "ktvmai"))}
    return {**out, **(await _runs(luong) if runs else {})}


OFFICE_IP, OUTSIDE_IP = "203.0.113.5", "198.51.100.23"   # dải TÀI LIỆU (TEST-NET) — không phải IP của ai
STAFF = [("truongkd", "Đặng Quốc Việt", "team_lead", "KD-A", True, 24_000_000), ("nvan", "Hoàng Minh An", "sale", "KD-A", False, 15_000_000),
         ("nvbinh", "Phí Thị Bình", "sale", "KD-A", False, 13_000_000), ("nvchi", "Lương Bảo Chi", "staff", "KD-A", False, 11_000_000)]


async def office(domain: str = OFFICE_DOMAIN, name: str = "Công ty Văn phòng Mẫu Sao Mai (dữ liệu giả)", *, runs: bool = True,
                 close: bool = True) -> dict[str, Any]:
    """Công ty văn phòng: Nhân sự (``nhansu``) đặt địa điểm theo IP văn phòng + giờ hành chính, gán chấm công, cấp phép năm; nhân viên kinh doanh chấm
    theo giờ vào / ra (nhập lịch sử như tệp máy chấm, có IP), trưởng đội xét giờ có cờ + duyệt đơn; tháng trước đã chốt cả công ty ⇒ công sang Lương;
    Lương: lương tháng theo ngày công, làm thêm, bảo hiểm, thuế luỹ tiến (người phụ thuộc)."""
    from barista.modules.timekeeping.common import holidays
    from barista.modules.timekeeping.sheet import working_days

    tid = await create_tenant(name, domain, people=[("chu", "Ngô Thanh Bình", "admin", "QT", True), *OFFICE, ("nhansu", "Trịnh Thu Trang", "hr_officer", "HCNS", True),
                                                     *[(a, b, c, d, e) for a, b, c, d, e, _ in STAFF]],
                              packs=("timekeeping", "pay"), about="công ty văn phòng (mẫu): lương tháng, chấm công theo IP văn phòng, phòng Nhân sự riêng")
    w = await _people(tid, domain, ("chu", "giamdoc", "ketoan", "luong", "nhansu", *[s[0] for s in STAFF]))
    hr, lead = w["nhansu"], w["truongkd"]
    await run(hr, "timekeeping.upsert_site", code="VP", name="Văn phòng chính", ip_ranges="203.0.113.0/28")
    await run(hr, "timekeeping.upsert_pattern", code="HC", name="Giờ hành chính", weekdays="1,2,3,4,5", starts="08:00", ends="17:00", break_minutes=60,
              grace_minutes=5)
    await run(hr, "timekeeping.install_leave_types")
    first = add_months(month_of(today()), -1)
    for local, *_ in STAFF:
        await run(hr, "timekeeping.enrol", person=f"{local}@{domain}", pattern_code="HC", site_code="VP", mode="punch", valid_from=first.isoformat())
    await run(w["chu"], "timekeeping.enrol", person=f"nhansu@{domain}", pattern_code="HC", site_code="VP", mode="exception", valid_from=first.isoformat())
    await run(hr, "timekeeping.grant_annual_leave", year=first.year)
    if first.year != today().year:
        await run(hr, "timekeeping.grant_annual_leave", year=today().year)
    # lịch sử giờ chấm (như tệp xuất của máy chấm, có IP): ngày làm việc của tháng trước + tháng này tới hôm qua
    days = working_days(first, today() - timedelta(days=1), weekdays=[1, 2, 3, 4, 5], holidays=holidays(first, today()))
    prev = [d for d in days if d < month_of(today())]
    ot_day, leave_day, unpaid_day, forgot_day = prev[-2], prev[-4], prev[-6], prev[-7]
    outside = {("nvbinh", prev[-3]), ("nvbinh", prev[-8]), ("nvbinh", days[-2]), ("nvan", days[-1])}
    for i, d in enumerate(days):
        for j, (local, *_) in enumerate(STAFF):
            if (local, d) in (("nvan", leave_day), ("nvchi", unpaid_day)):
                continue
            arrive = f"07:{50 + (i + j) % 9:02d}" if not (local == "nvchi" and d == prev[-5]) else "08:25"
            leave_at = "19:10" if (local, d) == ("nvan", ot_day) else f"17:{(i * 7 + j * 3) % 30:02d}"
            ip = OUTSIDE_IP if (local, d) in outside else OFFICE_IP
            await run(hr, "timekeeping.import_punch", person=f"{local}@{domain}", at=f"{d:%d/%m/%Y} {arrive}", direction="in", ip=ip)
            if not (local == "nvbinh" and d in (days[-1], forgot_day)):   # quên bấm ra ca: tháng trước (bổ sung có duyệt) + hôm qua (chưa bổ sung)
                await run(hr, "timekeeping.import_punch", person=f"{local}@{domain}", at=f"{d:%d/%m/%Y} {leave_at}", direction="out", ip=ip)
    # đơn: nghỉ phép năm + nghỉ không lương (đã duyệt), làm thêm 2 giờ (đã duyệt), một đơn nghỉ sắp tới (đang chờ)
    for who_, kind, day in (("nvan", "PHEP_NAM", leave_day), ("nvchi", "KHONG_LUONG", unpaid_day)):
        got = await run(w[who_], "timekeeping.request_leave", leave_type=kind, from_day=day.isoformat(), reason="Việc gia đình (mẫu)")
        await run(lead, "core.decide", approval_id=got["approval_id"], approve=True, note="")
    got = await run(w["nvan"], "timekeeping.request_overtime", day=ot_day.isoformat(), hours=2, reason="Hoàn thiện hồ sơ khách gấp (mẫu)")
    await run(lead, "core.decide", approval_id=got["approval_id"], approve=True, note="")
    friday = today() + timedelta(days=(4 - today().weekday()) % 7 or 7)
    await run(w["nvbinh"], "timekeeping.request_leave", leave_type="PHEP_NAM", from_day=friday.isoformat(), reason="Về quê (mẫu)")
    out: dict[str, Any] = {"tenant_id": tid, "domain": domain, "who": w, "closed": 0, "pushed": 0, "period": first, "ot_day": ot_day,
                           "leave_day": leave_day, "unpaid_day": unpaid_day, "forgot_day": forgot_day, "friday": friday}
    if not close:
        return out
    # quên bấm ra ca tháng trước ⇒ xin bổ sung, trưởng đội duyệt (ngày chưa đủ giờ chấm không tính công, chặn chốt)
    got = await run(w["nvbinh"], "timekeeping.request_correction", day=forgot_day.isoformat(), clock="17:20", direction="out",
                    reason="Quên bấm ra ca (mẫu)")
    await run(lead, "core.decide", approval_id=got["approval_id"], approve=True, note="")
    # giờ có cờ của tháng trước: trưởng đội xét (nhận) — tháng này còn chờ xét (bàn việc của trưởng đội / Nhân sự)
    flagged = await q(lead, "timekeeping.flagged", days=62, pending_only=True)
    for r in flagged["rows"]:
        if r["at"][:7] == f"{first:%Y-%m}":
            await run(lead, "timekeeping.review_punch", punch_id=r["id"], decision="accept", note="Gặp khách theo lịch đã báo (mẫu)")
    closed = await run(hr, "timekeeping.close_period", month=first.isoformat(), confirm_days=5)
    await run(w["nvan"], "timekeeping.confirm_timesheet", month=first.isoformat(), agree=True)
    # Lương: điều kiện trả theo hợp đồng lao động, người phụ thuộc, lương tháng theo ngày công + làm thêm
    luong = w["luong"]
    for local, _n, _r, _t, _l, salary in [*STAFF, ("nhansu", "", "", "", True, 16_000_000)]:
        party = await _party_of(tid, f"{local}@{domain}")
        await run(luong, "hr.set_terms", party_id=party, contract_kind="indefinite", insured_vnd=salary, work_region="I", union_member=local == "nvan",
                  effective_from=_year_start(), note="Hợp đồng lao động không thời hạn (mẫu)")
    await run(luong, "hr.add_dependant", party_id=await _party_of(tid, f"nvan@{domain}"), name="Hoàng Minh Khang", relation="child",
              registered_from=_year_start(), note="Đã nộp tờ khai đăng ký (mẫu)")
    await _pay_setup(luong, [], [{"code": "LUONG", "label": "Lương theo ngày công", "calculator": "prorata", "expense_account": "expense:payroll"},
                                 {"code": "LAM_THEM", "label": "Làm thêm giờ", "calculator": "overtime", "expense_account": "expense:payroll"}],
                     [{"component": "LUONG", "unit": "month", "party_id": await _party_of(tid, f"{local}@{domain}"), "amount_vnd": salary}
                      for local, *_, salary in [*STAFF, ("nhansu", "", "", "", True, 16_000_000)]])
    out |= {"closed": closed["people"], "pushed": closed["pushed"]}
    if runs:
        real = await run(luong, "pay.draft_run", period=first.isoformat(), pay_date=today().isoformat())
        shadow = await run(luong, "pay.draft_run", period=first.isoformat(), pay_date=today().isoformat(), kind="shadow")
        out |= {"run_id": real["run_id"], "shadow_id": shadow["run_id"]}
    return out


async def seed_pay() -> dict[str, Any]:
    office_ = await office()   # trước: công ty đầu tiên của bản cài (đăng nhập dev, kiểm hợp đồng app) là văn phòng chấm công + lương theo luật
    from barista.seed import samples
    extra = {key: await fn() for key, mod in samples().items() if (fn := getattr(mod, "COMPANIES", {}).get("pay-demo")) is not None}
    return {"office": office_, "service": await service_shop(), "extra": extra}
