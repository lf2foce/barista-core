"""LƯƠNG TRONG DÒNG TIỀN DỰ PHÓNG (0211) — gói góp dòng chi cho ``acct.cashflow`` qua hợp đồng ``pay.cashflow_lines``.

Chia việc để KHÔNG đếm hai lần:
* Kỳ ĐÃ CHỐT: phần thực nhận đã là nghĩa vụ ở Tài chính (``acct.recognize_payable`` mang ``due_on`` = ngày chi) ⇒ Tài chính xếp theo tháng đến hạn;
  ở đây KHÔNG trả lại phần đó.
* Kỳ CHƯA CHỐT (nháp / chờ duyệt) và các tháng chưa lập kỳ: ước thực nhận — kỳ đang mở thì lấy số phiếu đã tính (bỏ phiếu đang dừng: chúng rời kỳ khi
  gửi duyệt; quá ngày chi mà chưa chốt ⇒ dồn tháng này), các tháng sau lấy kỳ đã chốt gần nhất của cùng pháp nhân làm mốc, ngày chi dời theo tháng
  (tháng ước đã qua không dồn về tháng này). Chưa từng chốt kỳ nào ⇒ chỉ có kỳ đang mở.
* Bảo hiểm / thuế TNCN / công đoàn trên phiếu (phần giữ lại của người lao động + phần doanh nghiệp đóng) là tiền nộp cơ quan nhà nước — chưa có lệnh nộp
  trong hệ thống ⇒ ước, xếp CÙNG THÁNG chi lương (sớm hơn hạn thật ⇒ dự phóng thận trọng; hạn nộp không viết cứng ở đây — bất biến 5). Kỳ đã chốt chỉ
  tính khi tháng chi là tháng này trở đi (không biết đã nộp chưa ⇒ không dồn khoản cũ vào tháng này).

Chỉ SỐ TỔNG cả công ty theo tháng — không người, không phiếu, không kỳ: phiếu lương là tài nguyên mật (``pay.slips``) mà người xem dòng tiền (Kế toán /
giám đốc) không nhất thiết có. Chỉ kỳ ``regular``: kỳ ngoài kỳ / sửa sai chốt xong đã là nghĩa vụ; kỳ chạy bóng không bao giờ chi.
"""

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from barista.api import Ctx, Input, add_months, today
from barista.modules.pay import m

NET_KEY, STATUTORY_KEY = "out_payroll", "out_payroll_statutory"
NOTE = ("Lương: kỳ đã chốt nằm trong nghĩa vụ phải trả theo ngày chi; kỳ chưa chốt ước theo kỳ đang tính hoặc kỳ đã chốt gần nhất; bảo hiểm, thuế TNCN, "
        "công đoàn trên phiếu xếp cùng tháng chi lương (ước — nộp thật theo hạn của quy định).")


class Months(Input):
    months: list[str]   # "MM/YYYY", tháng đầu = tháng này (acct.cashflow)


def _key(d: date) -> str:
    return f"{d.month:02d}/{d.year}"


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def _shift(pay_date: date, n: int) -> date:
    """Ngày chi của kỳ cách ``n`` tháng: cùng ngày trong tháng, kẹp theo độ dài tháng (chi ngày 31 ⇒ ngày cuối của tháng 30 ngày)."""
    first = add_months(pay_date, n)
    last = add_months(first, 1) - timedelta(days=1)
    return first.replace(day=min(pay_date.day, last.day))


_RUNS = """WITH latest AS (SELECT DISTINCT ON (entity_id) id FROM pay.runs WHERE kind = 'regular' AND status = 'locked'
                          ORDER BY entity_id, period DESC, seq DESC)
SELECT r.id, r.entity_id, r.period, r.pay_date, r.status, r.id IN (SELECT id FROM latest) AS latest,
       coalesce((SELECT sum(s.net_vnd) FROM pay.slips s WHERE s.run_id = r.id AND s.held_reason = ''), 0)::bigint AS net,
       coalesce((SELECT sum(l.amount_vnd) FROM pay.slip_lines l JOIN pay.slips s ON s.id = l.slip_id
                 WHERE s.run_id = r.id AND s.held_reason = '' AND l.kind IN ('withholding', 'employer')), 0)::bigint AS statutory
FROM pay.runs r
WHERE r.kind = 'regular' AND (r.status <> 'locked' OR r.pay_date >= %s OR r.id IN (SELECT id FROM latest))"""


@m.query("cashflow_lines", resource="pay", title="Dòng tiền dự phóng của lương", internal=True, agent="deny", callers=("acct",),
         why="acct.cashflow (Dòng tiền dự phóng, min_scope all — người xem có quyền Kế toán cả công ty): quỹ lương THỰC NHẬN của kỳ chưa chốt (ước) và "
             "bảo hiểm / thuế TNCN / công đoàn trên phiếu (giữ lại + doanh nghiệp đóng) theo tháng — SỐ TỔNG cả công ty, không người, không phiếu, "
             "không kỳ. Kỳ đã chốt: thực nhận đã là nghĩa vụ ở Tài chính, không trả lại ở đây.")
async def cashflow_lines(ctx: Ctx, inp: Months) -> dict[str, Any]:
    if not inp.months:
        return {"lines": []}
    want = list(inp.months)
    t = today()
    m0 = t.replace(day=1)
    mm, yy = (int(x) for x in want[-1].split("/"))
    horizon = date(yy, mm, 1)
    runs = await ctx.all(_RUNS, (m0,))
    if not runs:
        return {"lines": []}
    net: dict[str, int] = {k: 0 for k in want}
    statutory: dict[str, int] = {k: 0 for k in want}

    def put(into: dict[str, int], pay_date: date, v: int, *, overdue_to_now: bool) -> None:
        if pay_date < m0 and not overdue_to_now:
            return
        k = _key(max(pay_date, t))
        if k in into:
            into[k] += v

    by_entity: dict[UUID, list[dict[str, Any]]] = {}
    for r in runs:
        by_entity.setdefault(r["entity_id"], []).append(r)
    for rows in by_entity.values():
        base = next((r for r in rows if r["latest"]), None)
        open_ = next((r for r in rows if r["status"] != "locked"), None)
        for r in rows:   # kỳ đã chốt chi từ tháng này: chỉ phần nộp nhà nước (thực nhận đã ở nghĩa vụ của Tài chính)
            if r["status"] == "locked":
                put(statutory, r["pay_date"], int(r["statutory"]), overdue_to_now=False)
        if open_ is not None:   # kỳ đang mở: số phiếu đã tính; quá ngày chi mà chưa chốt ⇒ dồn tháng này
            put(net, open_["pay_date"], int(open_["net"]), overdue_to_now=True)
            put(statutory, open_["pay_date"], int(open_["statutory"]), overdue_to_now=True)
        if base is None:
            continue
        # các tháng sau kỳ đã chốt gần nhất chưa có kỳ: ước bằng kỳ đó, ngày chi dời theo tháng. Tháng ước đã qua KHÔNG dồn về tháng này — công ty
        # không lập kỳ cho tháng đó (chi ngoài hệ thống / ngừng dùng) thì cộng dồn nhiều tháng lương vào tháng này là số giả
        n = 1
        while True:
            period = add_months(base["period"], n)
            pay_on = _shift(base["pay_date"], _months_between(base["period"], period))
            if pay_on.replace(day=1) > horizon:
                break
            if open_ is None or period != open_["period"]:
                put(net, pay_on, int(base["net"]), overdue_to_now=False)
                put(statutory, pay_on, int(base["statutory"]), overdue_to_now=False)
            n += 1
    return {"lines": [{"key": NET_KEY, "label": "Lương kỳ chưa chốt (ước)", "kind": "out", "by_month": net},
                      {"key": STATUTORY_KEY, "label": "BH, thuế TNCN, công đoàn trên phiếu (ước)", "kind": "out", "by_month": statutory}],
            "note": NOTE}
