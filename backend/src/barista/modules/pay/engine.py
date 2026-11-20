"""HÀM TÍNH PHIẾU LƯƠNG THUẦN (0150 §4.4): không DB, không ngữ cảnh lệnh, không đồng hồ — chỉ thư viện chuẩn (``tests/test_pay.py`` đọc AST).

Đầu vào là BẢN CHỤP lưu trên phiếu (``pay.slips.inputs``): người + loại hợp đồng tại ngày chi, các dòng công, mức, danh mục khoản lương (phiên bản),
quy tắc luật (phiên bản + trạng thái xác minh), tiền ứng còn cấn được. Tính lại một phiếu từ bản chụp phải ra đúng từng dòng (test "phát lại").

Thứ tự: (1) thu nhập — ``fixed`` (lương cố định của tháng: dòng công ``pay.fixed`` một lần mỗi người mỗi tháng — review 09/11), ``prorata`` (mức tháng ×
ngày công có lương / ngày công chuẩn, từ dòng công ``days.paid`` / ``days.unpaid`` của bảng công đã chốt — lát 4), ``rate_x_qty`` (công × mức cụ thể nhất
có hiệu lực ngày làm), ``overtime`` (giờ ``ot.150/200/300`` × đơn giá giờ × hệ số luật ``OT_PAY``), ``from_source`` (tiền nguồn đã tính), ``manual``
(khoản nhập tay đã duyệt; khoản khấu trừ thì trừ); (2) khấu trừ theo luật — người không ký hợp đồng lao động / ký dưới N tháng: 10% theo từng lần chi
từ ngưỡng (``PIT_FLAT_10``); hợp đồng lao động (lát 3): bảo hiểm người lao động đóng có trần (BHXH / BHYT theo lương cơ sở, BHTN theo lương tối thiểu
vùng), đoàn phí, thuế TNCN luỹ tiến LUỸ KẾ trong tháng chi sau giảm trừ gia cảnh + bảo hiểm (miễn phần chênh làm thêm giờ); phần doanh nghiệp đóng là
dòng ``employer`` (không trừ vào phiếu); (3) cấn tiền ứng; (4) thực nhận. Quy tắc chưa xác minh: kỳ thật ⇒ phiếu DỪNG (``held``), không có dòng luật;
kỳ chạy bóng ⇒ tính, mọi dòng dựa vào nó mang dấu ``unverified``. Mỗi dòng luật ghi MỌI quy tắc nó dựa vào trong ``rule_ref`` (nối bằng " + ").
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

UNIT_LABEL = {"session": "buổi", "hour": "giờ", "shift": "ca", "day": "ngày", "piece": "lượt", "month": "tháng"}
WITHHOLDING = "PIT_FLAT_10"
WITHHOLDING_ACCOUNT = "tax:withheld"
PROGRESSIVE, DEDUCTIONS, OT_EXEMPT, OT_PAY = "PIT_PROGRESSIVE", "PIT_DEDUCTIONS", "PIT_OT_EXEMPT", "OT_PAY"
INSURANCE_BASE, REGIONAL = "INSURANCE_BASE", "REGIONAL_MIN_WAGE"
CONTRIBUTION = "contribution"   # tham số có value.kind này = một khoản đóng (bảo hiểm / công đoàn) — vòng lặp ``_insurance`` áp mọi mã loại này
# MỌI mã quy tắc engine đọc THEO TÊN — nguồn duy nhất của hợp đồng mã với bộ ``vn-payroll`` (0154): phiên bản mới nhất của bộ phải có đủ; mã khác trong
# file chỉ hợp lệ khi là khoản đóng (``CONTRIBUTION``). ``pay.policy`` dẫn mã khoản công ty bị chặn từ đây, không giữ danh sách thứ hai.
RULE_CODES = (WITHHOLDING, PROGRESSIVE, DEDUCTIONS, OT_EXEMPT, OT_PAY, INSURANCE_BASE, REGIONAL)
# Dòng công của bảng công đã chốt (hợp đồng công bố cho nguồn chấm công — ``timekeeping``): ngày có lương / không lương của tháng, giờ làm thêm theo hệ số.
# ``days.std`` = ngày công CHUẨN của tháng theo lịch làm việc của công ty (cả những ngày trước khi vào làm / sau khi nghỉ — review 10/11): mẫu số của
# lương theo ngày công và đơn giá giờ làm thêm. Nguồn không gửi ⇒ mẫu số = có lương + không lương.
ATTENDANCE = {"days.paid": "paid", "days.unpaid": "unpaid", "days.std": "std"}
OT_ROLES = ("ot.150", "ot.200", "ot.300")
# Tài khoản nợ phải trả / chi phí của khoản luật (sổ cái do gói Lương khai): người lao động đóng ⇒ giữ lại trên phiếu; doanh nghiệp đóng ⇒ chi phí.
FUND_LIABILITY = {"BHXH": "payable:insurance", "BHYT": "payable:insurance", "BHTN": "payable:insurance", "KPCD": "payable:union",
                  "DOANPHI": "payable:union"}
FUND_EXPENSE = {"BHXH": "expense:insurance", "BHYT": "expense:insurance", "BHTN": "expense:insurance", "KPCD": "expense:union"}
NOT_DEDUCTIBLE = ("DOANPHI",)   # đoàn phí không phải bảo hiểm bắt buộc ⇒ không giảm thu nhập tính thuế


def _vnd(n: int) -> str:
    return f"{n:,}".replace(",", ".") + " đ"


def _qty(q: Decimal) -> str:
    return format(q.normalize(), "f").replace(".", ",")


def _ref(c: dict[str, Any]) -> str:
    return f"company:components@v{c['version']}:{c['code']}"


def pick_rate(rates: list[dict[str, Any]], *, party: str, unit: str, role_key: str, day: str) -> dict[str, Any] | None:
    """Mức CỤ THỂ NHẤT có hiệu lực ngày làm: của chính người > đúng vai > chung (vai trống); cùng độ cụ thể ⇒ hiệu lực gần nhất, rồi dòng ghi sau."""
    best: tuple[tuple[int, int, str, int], dict[str, Any]] | None = None
    for i, r in enumerate(rates):
        if r["unit"] != unit or r["effective_from"] > day or (r["party_id"] not in (None, party)) or r["role_key"] not in ("", role_key):
            continue
        key = (int(r["party_id"] == party), int(r["role_key"] == role_key and role_key != ""), r["effective_from"], i)
        if best is None or key > best[0]:
            best = (key, r)
    return best[1] if best else None


def _month(day: str) -> str:
    return day[:7]


def _mm(month: str) -> str:
    return f"{month[5:7]}/{month[:4]}"


def progressive(x: int, brackets: list[list[Any]]) -> int:
    """Thuế luỹ tiến từng phần của thu nhập tính thuế ``x`` (bậc: [trần trên hoặc None, bps]); lấy phần nguyên đồng."""
    tax, lo = 0, 0
    for hi, bps in brackets:
        if hi is None or x <= hi:
            tax += (max(x, lo) - lo) * bps
            break
        tax += (hi - lo) * bps
        lo = hi
    return tax // 10000


def _covers(v: dict[str, Any], terms: dict[str, Any]) -> bool:
    """Hợp đồng thuộc diện của quy tắc: loại nằm trong danh sách và (không phải có thời hạn, hoặc đủ số tháng tối thiểu)."""
    kind, months = terms.get("contract_kind"), terms.get("term_months")
    return kind in v["contract_kinds"] and (kind != "fixed_term" or (months is not None and months >= int(v.get("min_term_months") or 0)))


def _applies(rule: dict[str, Any], terms: dict[str, Any]) -> bool:
    kind, months = terms.get("contract_kind"), terms.get("term_months")
    short = rule.get("short_term_months_below")
    return kind in rule["contract_kinds"] or (kind == "fixed_term" and months is not None and short is not None and months < short)


@dataclass
class Part:
    """Dòng + thông điệp của MỘT bước. Mỗi bước dựng ``Part`` riêng rồi trả về (không chia sẻ giữa các bước); ``compute`` ghép theo thứ tự bước."""
    lines: list[dict[str, Any]] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)

    def add(self, c: dict[str, Any], signed: int, basis: str, entries: list[str], *, ref: str = "", unv: bool = False) -> None:
        """Dòng của một khoản công ty; ``signed`` = ảnh hưởng lên số được trả (dương = cộng). Khoản thu nhập ra âm (thu hồi lớn hơn phát sinh) ⇒
        dòng khấu trừ cùng khoản."""
        if signed == 0:
            return
        label = c["label"]
        if c["kind"] == "earning" and signed < 0:
            label = f"Thu hồi: {label}"
        elif c["kind"] == "deduction" and signed > 0:
            label = f"Hoàn: {label}"
        self.lines.append({"component": c["code"], "label": label, "kind": "earning" if signed > 0 else "deduction", "amount_vnd": abs(signed),
                           "basis": basis, "rule_ref": f"{ref} + {_ref(c)}" if ref else _ref(c), "account": c["expense_account"],
                           "taxable": bool(c["taxable"]), "unverified": unv, "entries": entries})

    def rule(self, law: dict[str, dict[str, Any]], real: bool, code: str, what: str) -> dict[str, Any] | None:
        """Quy tắc luật ``code`` của bản chụp: thiếu ⇒ dừng; chưa xác minh ⇒ ghi vào ``unverified`` (kỳ thật: dừng). Trả None khi KHÔNG được tính."""
        r = law.get(code)
        if r is None:
            self.held.append(f"Chưa có quy định {what} hiệu lực cho kỳ này")
            return None
        if r["status"] != "verified":
            self.unverified.append(r["ref"])
            if real:
                self.held.append(f"Quy tắc {r['ref']} ({what}) chưa xác minh — chờ kế toán xác minh")
                return None
        return r


@dataclass
class Earnings:
    """Bước 1: dòng thu nhập + số liệu dẫn xuất cho bước sau (``month_base`` / ``attendance`` cho bảo hiểm, ``premium`` cho thuế)."""
    part: Part
    gross: int
    less: int
    taxable: int
    premium: int
    month_base: dict[str, int]
    attendance: dict[str, dict[str, Decimal]]


@dataclass
class Deductions:
    """Bước 2: dòng luật (bảo hiểm, đoàn phí, thuế), tổng trừ trên phiếu ``tax`` và ``figures`` cho kỳ phụ cùng tháng chi."""
    part: Part
    tax: int
    figures: dict[str, Any]


def _round(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _std_days(a: dict[str, Decimal] | None) -> Decimal:
    """Ngày công chuẩn của tháng: ``days.std``; nguồn không gửi ⇒ có lương + không lương; không có bảng công ⇒ 0."""
    return (a["std"] or a["paid"] + a["unpaid"]) if a else Decimal(0)


def _first_by(comps: dict[str, dict[str, Any]], calculator: str, source: str | None = None) -> dict[str, Any] | None:
    """Khoản đầu tiên (theo mã) có cách tính ``calculator`` (và nguồn ``source`` nếu nêu)."""
    return next((x for _, x in sorted(comps.items()) if x["calculator"] == calculator and (source is None or x["source"] == source)), None)


def compute(inputs: dict[str, Any]) -> dict[str, Any]:
    """Bản chụp ⇒ {lines, gross_vnd, deductions_vnd, net_vnd, held, unverified, figures}. ``held`` không rỗng ⇒ phiếu không được ghi sổ (kỳ thật dừng ở
    nháp). ``figures``: thu nhập chịu thuế / thuế / bảo hiểm của phiếu — kỳ phụ cùng tháng chi đọc lại để tính thuế luỹ kế. Chỉ ghép bốn bước (xem
    đầu tệp): thu nhập → khấu trừ theo luật → cấn tiền ứng → thực nhận."""
    law: dict[str, dict[str, Any]] = inputs.get("law") or {}
    real = inputs["run_kind"] != "shadow"
    earn = _earnings(inputs, law, real)
    ded = _statutory_deductions(inputs, law, real, earn)
    unverified = [*earn.part.unverified, *ded.part.unverified]
    offset = _offset_advance(inputs, earn.gross - earn.less - ded.tax, bool(unverified))
    return _settle(earn, ded, offset, unverified)


# ── (1) thu nhập ──

def _earnings(inputs: dict[str, Any], law: dict[str, dict[str, Any]], real: bool) -> Earnings:
    part = Part()
    comps: dict[str, dict[str, Any]] = inputs["components"]
    attendance, ot_entries, plain = _split_entries(inputs["entries"])
    _rate_lines(part, plain, comps, inputs["rates"], inputs["party"]["id"])
    month_base = _amount_lines(part, inputs["entries"], comps, attendance)
    premium = _overtime_lines(part, ot_entries, comps, law, real, attendance, month_base)
    gross = sum(x["amount_vnd"] for x in part.lines if x["kind"] == "earning")
    less = sum(x["amount_vnd"] for x in part.lines if x["kind"] == "deduction")
    if not part.lines:
        part.held.append("Không có khoản nào để trả")
    taxable = sum((1 if x["kind"] == "earning" else -1) * x["amount_vnd"] for x in part.lines if x["taxable"])
    return Earnings(part, gross, less, taxable, premium, month_base, attendance)


def _split_entries(entries: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Decimal]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Dòng công (``qty``) ⇒ bảng công đã chốt theo tháng (ngày có lương / không lương / chuẩn), giờ làm thêm, và phần còn lại (công × mức)."""
    attendance: dict[str, dict[str, Decimal]] = {}
    ot_entries: list[dict[str, Any]] = []
    plain: list[dict[str, Any]] = []
    for e in (e for e in entries if e["kind"] == "qty"):
        if e["unit"] == "day" and e["role_key"] in ATTENDANCE:
            a = attendance.setdefault(_month(e["work_date"]), {"paid": Decimal(0), "unpaid": Decimal(0), "std": Decimal(0)})
            a[ATTENDANCE[e["role_key"]]] += Decimal(e["quantity"])
        elif e["unit"] == "hour" and e["role_key"] in OT_ROLES:
            ot_entries.append(e)
        else:
            plain.append(e)
    return attendance, ot_entries, plain


def _rate_lines(part: Part, plain: list[dict[str, Any]], comps: dict[str, dict[str, Any]], rates: list[dict[str, Any]], party: str) -> None:
    """Công × mức — gom theo (khoản, mức) cho dễ đọc; dòng công không có mức ⇒ dừng phiếu (không đoán giá)."""
    groups: dict[tuple[str, str], list[Any]] = {}
    for e in plain:
        r = pick_rate(rates, party=party, unit=e["unit"], role_key=e["role_key"], day=e["work_date"])
        c = comps.get(r["component"]) if r else None
        if r is None or c is None or c["calculator"] != "rate_x_qty":
            what = f"{UNIT_LABEL.get(e['unit'], e['unit'])}{' ' + e['role_key'] if e['role_key'] else ''} ngày {e['work_date']}"
            part.held.append(f"Chưa có mức cho {what}" if r is None else f"Mức của {what} gắn khoản {r['component']} không phải khoản tính theo công")
            continue
        g = groups.setdefault((c["code"], str(r["id"])), [Decimal(0), [], r])
        g[0] += Decimal(e["quantity"])
        g[1].append(e["id"])
    for (code, _), (q, ids, r) in sorted(groups.items()):
        part.add(comps[code], _round(q * r["amount_vnd"]),
                 f"{_qty(q)} {UNIT_LABEL[r['unit']]}{' ' + r['role_key'] if r['role_key'] else ''} × {_vnd(r['amount_vnd'])}", ids)


def _amount_component(e: dict[str, Any], comps: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Khoản lương nhận dòng tiền ``e``: khoản nêu tên (lương cố định ⇒ khoản ``fixed`` / ``prorata``; còn lại ⇒ khoản nhập tay) hoặc khoản
    nhận tiền từ nguồn (đúng nguồn, rồi khoản nguồn trống). None = không có khoản hợp lệ."""
    if e["component"]:
        c = comps.get(e["component"])
        ok = c is not None and (c["calculator"] in ("fixed", "prorata") if e["source"] == "pay.fixed" else c["calculator"] == "manual")
        return c if ok else None
    src = e["source"].split(".")[0]
    return _first_by(comps, "from_source", src) or _first_by(comps, "from_source", "")


def _amount_lines(part: Part, entries: list[dict[str, Any]], comps: dict[str, dict[str, Any]], attendance: dict[str, dict[str, Decimal]]) -> dict[str, int]:
    """Tiền nguồn đã tính + khoản nhập tay + lương tháng (cố định / theo ngày công). Trả ``month_base``: tháng → tổng lương tháng đủ công (cố định +
    theo ngày công) — đơn giá giờ làm thêm, kiểm sàn."""
    sums: dict[str, list[Any]] = {}
    monthly: dict[tuple[str, str], list[Any]] = {}   # (khoản theo ngày công, tháng) → [tổng mức tháng, dòng công]
    month_base: dict[str, int] = {}
    for e in (e for e in entries if e["kind"] == "amount"):
        c = _amount_component(e, comps)
        if c is None:
            part.held.append(f"Chưa có khoản lương nhận tiền từ nguồn {e['source']}" if not e["component"]
                             else f"Khoản {e['component']} không còn dùng / sai cách tính")
            continue
        if e["source"] == "pay.fixed":
            month_base[_month(e["work_date"])] = month_base.get(_month(e["work_date"]), 0) + int(e["amount_vnd"])
        if c["calculator"] == "prorata":
            g = monthly.setdefault((c["code"], _month(e["work_date"])), [0, []])
        else:
            g = sums.setdefault(c["code"], [0, []])
        g[0] += int(e["amount_vnd"])
        g[1].append(e["id"])
    for code, (total, ids) in sorted(sums.items()):
        c = comps[code]
        part.add(c, total if c["kind"] == "earning" else -total, "Mức cố định theo tháng" if c["calculator"] == "fixed" else f"{len(ids)} khoản từ sổ công",
                 ids)
    for (code, month), (total, ids) in sorted(monthly.items()):
        c, a = comps[code], attendance.get(month)
        std = _std_days(a)
        if not std or a is None:
            part.held.append(f"Chưa có bảng công đã chốt tháng {_mm(month)} — {c['label']} tính theo ngày công")
            continue
        part.add(c, _round(Decimal(total) * a["paid"] / std), f"{_qty(a['paid'])}/{_qty(std)} ngày công tháng {_mm(month)} × {_vnd(total)}", ids)
    return month_base


def _overtime_lines(part: Part, ot_entries: list[dict[str, Any]], comps: dict[str, dict[str, Any]], law: dict[str, dict[str, Any]], real: bool,
                    attendance: dict[str, dict[str, Decimal]], month_base: dict[str, int]) -> int:
    """Làm thêm giờ: giờ × đơn giá giờ (lương tháng / ngày công chuẩn / giờ một ngày) × hệ số luật. Trả phần chênh trên 100% có thể được miễn thuế."""
    if not ot_entries:
        return 0
    oc = _first_by(comps, "overtime")
    rule = part.rule(law, real, OT_PAY, "tiền làm thêm giờ")
    if oc is None:
        part.held.append("Có giờ làm thêm nhưng chưa có khoản lương làm thêm (cách tính: làm thêm giờ)")
    if oc is None or rule is None:
        return 0
    groups: dict[tuple[str, str], list[Any]] = {}
    for e in ot_entries:
        g = groups.setdefault((_month(e["work_date"]), e["role_key"]), [Decimal(0), []])
        g[0] += Decimal(e["quantity"])
        g[1].append(e["id"])
    premium = 0
    for (month, role), (hours, ids) in sorted(groups.items()):
        a, base = attendance.get(month), month_base.get(month, 0)
        if not _std_days(a) or base <= 0:
            part.held.append(f"Giờ làm thêm tháng {_mm(month)}: cần lương tháng và bảng công đã chốt để tính đơn giá giờ")
            continue
        std = min(_std_days(a), Decimal(rule["max_std_days"]))
        hourly = Decimal(base) / (std * Decimal(rule["hours_per_day"]))
        mult = int(rule["multipliers_bps"][role])
        if oc["taxable"]:   # khoản làm thêm đã không chịu thuế cả khoản ⇒ không trừ phần chênh lần nữa (review 10/11)
            premium += _round(hours * hourly * (mult - 10000) / 10000)
        part.add({**oc, "label": f"{oc['label']} {mult / 100:g}%"}, _round(hours * hourly * mult / 10000),
                 f"{_qty(hours)} giờ × {_vnd(_round(hourly))}/giờ ({_vnd(base)} / {_qty(std)} ngày / {rule['hours_per_day']} giờ) × {mult / 100:g}%", ids,
                 ref=rule["ref"], unv=rule["status"] != "verified")
    return premium


# ── (2) khấu trừ theo luật ──

def _statutory_deductions(inputs: dict[str, Any], law: dict[str, dict[str, Any]], real: bool, earn: Earnings) -> Deductions:
    """Bảo hiểm bắt buộc theo HỢP ĐỒNG (từ đủ 1 tháng), ĐỘC LẬP với cách khấu trừ thuế (review 10/11: hợp đồng 1–<3 tháng vẫn đóng bảo hiểm dù thuế là
    10% theo lần chi); rồi thuế: 10% theo lần chi hoặc luỹ tiến. Quy tắc nào áp cho hợp đồng nào do dữ liệu luật quyết, không do mã."""
    part, figures = Part(), dict[str, Any]()
    terms = inputs.get("terms")
    if terms is None:
        part.held.append("Chưa ghi loại hợp đồng của người này (Lương › Loại hợp đồng)")
        return Deductions(part, 0, figures)
    ins = _insurance(inputs, terms, law, real, earn.month_base, earn.attendance, part)
    statutory, employee_ins, employer, insured = ins if ins is not None else (0, 0, 0, False)
    tax = statutory + _income_tax(inputs, terms, law, real, earn, employee_ins, ins is not None, part, figures)
    if employee_ins or employer or insured:
        figures.update({"insurance_vnd": employee_ins, "employer_vnd": employer, "insured": insured})
    return Deductions(part, tax, figures)


def _income_tax(inputs: dict[str, Any], terms: dict[str, Any], law: dict[str, dict[str, Any]], real: bool, earn: Earnings, employee_ins: int,
                insurance_ok: bool, part: Part, figures: dict[str, Any]) -> int:
    """Chọn cách khấu trừ thuế theo hợp đồng và quy tắc có hiệu lực; trả thuế của phiếu (0 khi dừng / không thuộc diện)."""
    flat, prog = law.get(WITHHOLDING), law.get(PROGRESSIVE)
    if flat is not None and _applies(flat, terms):
        return _flat_tax(flat, real, earn.taxable, part, figures)
    if prog is not None and _covers(prog, terms):
        if insurance_ok:   # thuế luỹ tiến trừ bảo hiểm ⇒ bảo hiểm dừng thì thuế cũng chưa tính (vẫn nói quy tắc thuế nào chờ xác minh)
            return _progressive_tax(inputs, terms, law, real, earn.taxable, earn.premium, employee_ins, part, figures)
        pend = [law[k]["ref"] for k in (PROGRESSIVE, DEDUCTIONS) if k in law and law[k]["status"] != "verified"]
        if pend:
            part.unverified += pend
            part.held.append(f"Quy tắc chưa xác minh: {', '.join(pend)} — chờ kế toán xác minh từng quy tắc (thuế luỹ tiến)")
    elif terms.get("contract_kind") == "probation" and prog is not None:
        part.held.append("Hợp đồng thử việc: chờ kế toán chọn cách khấu trừ thuế (10% theo lần chi hay luỹ tiến) — câu hỏi mở của 0150 lát 3")
    elif flat is None and prog is None:
        part.held.append(f"Chưa có quy định khấu trừ hiệu lực ngày chi {inputs['pay_date']}")
    else:
        part.held.append("Hợp đồng lao động: chưa có quy định thuế thu nhập luỹ tiến hiệu lực ngày chi — chưa tính được phiếu")
    return 0


def _flat_tax(rule: dict[str, Any], real: bool, taxable: int, part: Part, figures: dict[str, Any]) -> int:
    """Khấu trừ theo tỉ lệ cố định từng lần chi, từ ngưỡng (``PIT_FLAT_10``)."""
    verified = rule["status"] == "verified"
    if not verified:
        part.unverified.append(rule["ref"])
        if real:
            part.held.append(f"Quy tắc {rule['ref']} (khấu trừ {rule['rate_bps'] / 100:g}% theo lần chi) chưa xác minh — chờ kế toán xác minh")
    if not (verified or not real):
        return 0
    over = taxable >= rule["min_payment_vnd"]
    pit = taxable * rule["rate_bps"] // 10000 if over and taxable > 0 else 0
    part.lines.append({"component": WITHHOLDING, "label": f"Khấu trừ thuế thu nhập {rule['rate_bps'] / 100:g}%", "kind": "withholding", "amount_vnd": pit,
                       "basis": (f"{rule['rate_bps'] / 100:g}% × {_vnd(taxable)} chịu thuế của lần chi" if over
                                 else f"Không khấu trừ: {_vnd(max(taxable, 0))} dưới ngưỡng {_vnd(rule['min_payment_vnd'])} một lần chi"),
                       "rule_ref": rule["ref"], "account": WITHHOLDING_ACCOUNT, "taxable": False, "unverified": not verified, "entries": []})
    figures.update({"taxable_vnd": max(taxable, 0), "pit_vnd": pit})
    return pit


# ── (3) cấn tiền ứng, (4) thực nhận ──

def _offset_advance(inputs: dict[str, Any], left: int, unverified: bool) -> Part:
    """Cấn tiền ứng đang mở (nguồn công ty cho phép trong khoản cấn ứng) vào số còn lại ``left`` sau khấu trừ; ``unverified`` = số cấn phụ thuộc số
    khấu trừ chưa xác minh (kỳ chạy bóng)."""
    part = Part()
    oc = _first_by(inputs["components"], "advance_offset")
    advances = int(inputs.get("advances_vnd") or 0)
    if oc is not None and advances > 0 and left > 0:
        part.lines.append({"component": oc["code"], "label": oc["label"], "kind": "offset", "amount_vnd": min(advances, left),
                           "basis": f"Tiền ứng còn mở {_vnd(advances)} (nguồn {oc['source']})", "rule_ref": _ref(oc),
                           "account": f"advance:{oc['source']}", "taxable": False, "unverified": unverified, "entries": []})
    return part


def _settle(earn: Earnings, ded: Deductions, offset: Part, unverified: list[str]) -> dict[str, Any]:
    """Ghép các bước theo thứ tự, đánh số dòng, tính thực nhận. Thực nhận trước cấn ứng không dương ⇒ dừng (review 09/11: tính SAU thuế)."""
    lines = [*earn.part.lines, *ded.part.lines, *offset.lines]
    held = [*earn.part.held, *ded.part.held]
    if lines and earn.gross - earn.less - ded.tax <= 0:
        held.append("Thực nhận không dương (khấu trừ / thu hồi / thuế lớn hơn thu nhập kỳ này) — để kỳ sau")
    for i, x in enumerate(lines, 1):
        x["seq"] = i
    deductions = earn.less + ded.tax + sum(x["amount_vnd"] for x in offset.lines)
    return {"lines": lines, "gross_vnd": earn.gross, "deductions_vnd": deductions, "net_vnd": earn.gross - deductions, "held": held,
            "unverified": list(dict.fromkeys(unverified)), "figures": ded.figures}


def _insurance(inputs: dict[str, Any], terms: dict[str, Any], law: dict[str, dict[str, Any]], real: bool, month_base: dict[str, int],
               attendance: dict[str, dict[str, Decimal]], part: Part) -> tuple[int, int, int, bool] | None:
    """Bảo hiểm bắt buộc + đoàn phí theo HỢP ĐỒNG (0150 §6): người lao động — dòng khấu trừ; doanh nghiệp — dòng ``employer``. Trả (khấu trừ trên phiếu,
    bảo hiểm được trừ khi tính thuế, phần doanh nghiệp đóng, có đóng không); None = phiếu dừng (thiếu / chưa xác minh quy tắc, thiếu dữ kiện)."""
    contribs = sorted((code for code, r in law.items() if r.get("kind") == CONTRIBUTION), key=lambda k: (law[k]["payer"] != "employee", k))
    covered = [k for k in contribs if _covers(law[k], terms) and (not law[k].get("members_only") or terms.get("union_member"))]
    if not covered:
        return 0, 0, 0, False
    need = [*covered, INSURANCE_BASE, REGIONAL]
    missing = [k for k in need if k not in law]
    if missing:
        part.held.append(f"Chưa có quy định {', '.join(missing)} hiệu lực cho kỳ này")
        return None
    pending = [law[k]["ref"] for k in need if law[k]["status"] != "verified"]
    part.unverified += pending
    if real and pending:
        part.held.append(f"Quy tắc chưa xác minh: {', '.join(pending)} — chờ kế toán xác minh từng quy tắc (bảo hiểm bắt buộc)")
        return None
    insured, region = int(terms.get("insured_vnd") or 0), terms.get("work_region")
    period = _month(inputs["period"])
    prior = inputs.get("prior") or {}
    if region is None or not insured:
        part.held.append("Chưa ghi lương đóng bảo hiểm / vùng nơi làm việc của người này (Lương › Điều kiện trả)")
        return None
    floor = int(law[REGIONAL][region])
    if insured < floor:
        part.held.append(f"Lương đóng bảo hiểm {_vnd(insured)} thấp hơn lương tối thiểu vùng {region} {_vnd(floor)}")
    if 0 < month_base.get(period, 0) < floor:
        part.held.append(f"Lương tháng {_vnd(month_base[period])} thấp hơn lương tối thiểu vùng {region} {_vnd(floor)}")
    unpaid = attendance.get(period, {}).get("unpaid", Decimal(0))
    employee_ins, statutory, employer = 0, 0, 0
    for k in covered:
        r = law[k]
        cap_ref = [law[INSURANCE_BASE]["ref"]] + ([law[REGIONAL]["ref"]] if r["cap"] == "regional" else [])
        base_salary = int(law[INSURANCE_BASE]["base_salary_vnd"])
        mult = int(law[INSURANCE_BASE]["cap_multiple"])
        cap = {"base": base_salary * mult, "regional": floor * mult}.get(r["cap"])
        basis_vnd = min(insured, cap) if cap else insured
        amount = basis_vnd * int(r["rate_bps"]) // 10000
        note = f"{int(r['rate_bps']) / 100:g}% × {_vnd(basis_vnd)}" + (f" (trần {_vnd(cap)})" if cap and insured > cap else "")
        if r.get("cap_ratio_bps"):
            top = base_salary * int(r["cap_ratio_bps"]) // 10000
            if amount > top:
                amount, note = top, note + f", tối đa {_vnd(top)}"
        skip = int(r.get("skip_if_unpaid_days_at_least") or 0)
        if prior.get("insured_period"):
            amount, note = 0, "Không tính: đã đóng ở phiếu khác của cùng tháng công"
        elif skip and unpaid >= skip and r["fund"] != "DOANPHI":
            amount, note = 0, f"Không đóng: nghỉ không lương {_qty(unpaid)} ngày (từ {skip} ngày trong tháng)"
        employee = r["payer"] == "employee"
        part.lines.append({"component": k, "label": r["label"], "kind": "withholding" if employee else "employer", "amount_vnd": amount, "basis": note,
                      "rule_ref": " + ".join([r["ref"], *cap_ref]), "account": FUND_LIABILITY[r["fund"]] if employee else FUND_EXPENSE[r["fund"]],
                      "taxable": False, "unverified": r["status"] != "verified" or any(law[c]["status"] != "verified" for c in (INSURANCE_BASE, REGIONAL)
                                                                                      if law[c]["ref"] in cap_ref),
                      "entries": []})
        if employee:
            statutory += amount
            employee_ins += amount if r["fund"] not in NOT_DEDUCTIBLE else 0
        else:
            employer += amount
    return statutory, employee_ins, employer, bool(employee_ins)


def _progressive_tax(inputs: dict[str, Any], terms: dict[str, Any], law: dict[str, dict[str, Any]], real: bool, taxable: int, premium: int,
                     employee_ins: int, part: Part, figures: dict[str, Any]) -> int:
    """Thuế TNCN luỹ tiến LUỸ KẾ trong THÁNG CHI: thuế của tổng thu nhập tháng chi (sau giảm trừ gia cảnh + bảo hiểm, miễn phần chênh làm thêm) trừ
    thuế đã khấu trừ ở phiếu đã chốt trước đó cùng tháng chi. Trả số thuế của phiếu này."""
    need = [PROGRESSIVE, DEDUCTIONS] + ([OT_EXEMPT] if premium else [])
    missing = [k for k in need if k not in law]
    if missing:
        part.held.append(f"Chưa có quy định {', '.join(missing)} hiệu lực cho kỳ này")
        return 0
    pending = [law[k]["ref"] for k in need if law[k]["status"] != "verified"]
    part.unverified += pending
    if real and pending:
        part.held.append(f"Quy tắc chưa xác minh: {', '.join(pending)} — chờ kế toán xác minh từng quy tắc (thuế luỹ tiến)")
        return 0
    prior = inputs.get("prior") or {}
    exempt = premium if premium and law.get(OT_EXEMPT, {}).get("exempt_premium") else 0
    this = max(taxable - exempt, 0)
    d = law[DEDUCTIONS]
    deps = int(terms.get("dependants") or 0)
    allowance = int(d["self_vnd"]) + deps * int(d["dependant_vnd"])
    total = int(prior.get("taxable_vnd") or 0) + this
    ins_total = int(prior.get("insurance_vnd") or 0) + employee_ins
    assessable = max(total - ins_total - allowance, 0)
    tax_total = progressive(assessable, law[PROGRESSIVE]["brackets"])
    tax = max(tax_total - int(prior.get("pit_vnd") or 0), 0)
    refs = [law[PROGRESSIVE]["ref"], d["ref"]] + ([law[OT_EXEMPT]["ref"]] if exempt else [])
    earlier = f"; đã khấu trừ trước trong tháng chi {_vnd(int(prior['pit_vnd']))}" if prior.get("pit_vnd") else ""
    part.lines.append({"component": PROGRESSIVE, "label": "Thuế thu nhập cá nhân (luỹ tiến)", "kind": "withholding", "amount_vnd": tax,
                  "basis": (f"Tính thuế {_vnd(assessable)} = chịu thuế {_vnd(total)}" + (f" (đã trừ {_vnd(exempt)} làm thêm miễn thuế)" if exempt else "")
                            + f" − bản thân {_vnd(int(d['self_vnd']))}" + (f" − {deps} người phụ thuộc × {_vnd(int(d['dependant_vnd']))}" if deps else "")
                            + f" − bảo hiểm {_vnd(ins_total)}; thuế luỹ tiến {_vnd(tax_total)}{earlier}"),
                  "rule_ref": " + ".join(refs), "account": WITHHOLDING_ACCOUNT, "taxable": False,
                  "unverified": any(law[c]["status"] != "verified" for c in (PROGRESSIVE, DEDUCTIONS)) or bool(exempt and law[OT_EXEMPT]["status"] != "verified"),
                  "entries": []})
    figures.update({"taxable_vnd": this, "pit_vnd": tax})
    return tax
