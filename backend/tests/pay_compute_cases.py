"""Lưới đầu vào cho bài kiểm tra "đặc tả hiện trạng" của ``pay.engine.compute`` (``tests/test_pay_compute_golden.py``).

Chạy lại để SINH golden từ hiện trạng:  ``cd backend && uv run python -m tests.pay_compute_cases``  (ghi ``tests/golden/pay_compute.json.gz`` — JSON một dòng nén gzip,
mtime = 0 nên cùng nội dung ⇒ cùng byte; đọc bằng ``read_golden()``; muốn xem bằng mắt: ``gzip -dc tests/golden/pay_compute.json.gz | python -m json.tool | less``).
Golden chứa cả đầu vào lẫn đầu ra ⇒ bài kiểm tra không phụ thuộc bộ sinh số ngẫu nhiên; chỉ chạy lại khi CỐ Ý đổi cách tính (và nói rõ trong commit).
"""

import copy
import gzip
import itertools
import json
import random
from datetime import date
from pathlib import Path
from typing import Any

from barista.modules.pay import engine
from barista.modules.pay.runs import law_on

GOLDEN = Path(__file__).parent / "golden" / "pay_compute.json.gz"

_BASE = {"label": "", "version": 1, "kind": "earning", "taxable": True, "expense_account": "expense:payroll", "source": ""}


def comp(code: str, calculator: str, **kw: Any) -> dict[str, Any]:
    return {**_BASE, "code": code, "label": kw.pop("label", code.title()), "calculator": calculator, **kw}


COMPONENTS: dict[str, dict[str, Any]] = {
    "LUONG": comp("LUONG", "prorata", label="Lương cơ bản"),
    "LUONGCD": comp("LUONGCD", "fixed", label="Lương cố định", version=3),
    "CONG": comp("CONG", "rate_x_qty", label="Tiền công", account_note="x") | {"expense_account": "expense:work"},
    "LAM_THEM": comp("LAM_THEM", "overtime", label="Làm thêm giờ"),
    "THUONG": comp("THUONG", "manual", label="Thưởng"),
    "PHAT": comp("PHAT", "manual", label="Phạt", kind="deduction", taxable=False),
    "PHUCAP": comp("PHUCAP", "manual", label="Phụ cấp ăn", taxable=False),
    "HOAHONG": comp("HOAHONG", "from_source", label="Hoa hồng", source="sales"),
    "KHAC": comp("KHAC", "from_source", label="Tiền nguồn khác", source=""),
    "UNG": comp("UNG", "advance_offset", label="Cấn tiền ứng", kind="deduction", source="advance", taxable=False),
}
# ``account_note`` không phải trường của khoản — bỏ đi cho gọn
COMPONENTS["CONG"].pop("account_note")


def law_variants() -> dict[str, dict[str, Any]]:
    """Các bộ quy tắc luật: bộ phát hành (2 phiên bản) × (đã xác minh hết / chưa / thiếu một quy tắc / chỉ một quy tắc chưa xác minh)."""
    out: dict[str, dict[str, Any]] = {}
    for tag, pay_day, end in (("new", date(2026, 11, 5), date(2026, 10, 31)), ("old", date(2026, 3, 5), date(2026, 2, 28))):
        base = law_on(pay_day, end)
        for r in base.values():
            r.pop("basis", None)
        ver = {k: {**v, "status": "verified", "verified_by": "Kế toán thử"} for k, v in base.items()}
        out[f"{tag}:assumption"] = copy.deepcopy(base)
        out[f"{tag}:verified"] = ver
        for code in sorted(base):
            out[f"{tag}:verified-minus:{code}"] = {k: v for k, v in ver.items() if k != code}
            out[f"{tag}:verified-except:{code}"] = {**ver, code: {**ver[code], "status": "assumption", "verified_by": ""}}
    out["empty"] = {}
    out["flat-only:verified"] = {k: v for k, v in out["new:verified"].items() if k in ("PIT_FLAT_10", "OT_PAY")}
    return out


LAWS = law_variants()
CORE_LAWS = ["new:verified", "new:assumption", "old:verified", "empty", "flat-only:verified", "new:verified-minus:PIT_PROGRESSIVE",
             "new:verified-minus:INSURANCE_BASE", "new:verified-except:PIT_DEDUCTIONS", "new:verified-except:INSURANCE_BASE",
             "new:verified-except:REGIONAL_MIN_WAGE", "new:verified-minus:PIT_FLAT_10", "new:verified-except:PIT_FLAT_10"]

_n = itertools.count()


def amount_entry(component: str, amount: int, *, source: str = "pay.manual", day: str = "2026-10-01") -> dict[str, Any]:
    return {"id": f"a{next(_n)}", "work_date": day, "kind": "amount", "unit": "", "quantity": "0", "amount_vnd": amount, "role_key": "",
            "component": component, "source": source}


def qty_entry(unit: str, qty: str, role: str = "", day: str = "2026-10-31") -> dict[str, Any]:
    return {"id": f"q{next(_n)}", "work_date": day, "kind": "qty", "unit": unit, "quantity": qty, "amount_vnd": None, "role_key": role, "component": "",
            "source": "timekeeping.close"}


def att(paid: str | None = None, unpaid: str | None = None, std: str | None = None, month: str = "2026-10") -> list[dict[str, Any]]:
    day = f"{month}-28"
    return [qty_entry("day", v, k, day) for k, v in (("days.paid", paid), ("days.unpaid", unpaid), ("days.std", std)) if v is not None]


def rate(rid: int, component: str, unit: str, amount: int, *, role: str = "", party: str | None = None, frm: str = "2026-01-01") -> dict[str, Any]:
    return {"id": rid, "component": component, "unit": unit, "role_key": role, "party_id": party, "effective_from": frm, "amount_vnd": amount}


def snapshot(*, law: str, entries: list[dict[str, Any]], terms: Any = "default", rates: list[dict[str, Any]] | None = None,
             comps: dict[str, dict[str, Any]] | None = None, kind: str = "regular", advances: int = 0, prior: dict[str, Any] | None = None,
             period: str = "2026-10-01", pay_date: str = "2026-11-05") -> dict[str, Any]:
    t = {"contract_kind": "indefinite", "term_months": None, "insured_vnd": 20_000_000, "work_region": "I", "union_member": False,
         "dependants": 1} if terms == "default" else terms
    return {"version": 2, "run_kind": kind, "period": period, "period_end": "2026-10-31", "pay_date": pay_date, "party": {"id": "p1", "name": "A"},
            "terms": t, "entries": entries, "rates": rates or [], "components": comps if comps is not None else COMPONENTS, "advances_vnd": advances,
            "law": f"@{law}", "prior": prior if prior is not None else {}}


def _terms(kind: str | None, months: int | None = None, insured: int | None = 20_000_000, region: str | None = "I", union: bool = False,
           deps: int = 0) -> dict[str, Any]:
    return {"contract_kind": kind, "term_months": months, "insured_vnd": insured, "work_region": region, "union_member": union, "dependants": deps}


TERMS = {
    "indef-20M-I-dep1": _terms("indefinite", deps=1),
    "indef-120M-I-union": _terms("indefinite", insured=120_000_000, union=True),
    "indef-5M-II": _terms("indefinite", insured=5_000_000, region="II", deps=3),
    "indef-3M-I-below-floor": _terms("indefinite", insured=3_000_000),
    "indef-4M-IV": _terms("indefinite", insured=4_000_000, region="IV"),
    "indef-no-region": _terms("indefinite", region=None),
    "indef-no-insured": _terms("indefinite", insured=None),
    "indef-union-30M-III": _terms("indefinite", insured=30_000_000, region="III", union=True, deps=2),
    "fixed12-15M-III": _terms("fixed_term", 12, 15_000_000, "III", deps=2),
    "fixed2-8M-I": _terms("fixed_term", 2, 8_000_000),
    "fixed3-8M-I": _terms("fixed_term", 3, 8_000_000),
    "fixed-none-months": _terms("fixed_term", None, 8_000_000),
    "fixed0": _terms("fixed_term", 0, 8_000_000),
    "probation": _terms("probation"),
    "service": _terms("service", insured=None, region=None),
    "none-kind": _terms(None),
}

ATTENDANCE = {
    "full": att("22", "0", "22"),
    "partial": att("15", "0", "22"),
    "partial-frac": att("17.5", "1.5", "22"),
    "long-unpaid": att("8", "14", "22"),
    "no-std": att("18", "2"),
    "none": [],
    "zero-paid": att("0", "22", "22"),
    "std26+": att("27", "0", "28"),
    "two-months": [*att("22", "0", "22"), *att("10", "10", "20", month="2026-09")],
}


def _core_grid() -> list[tuple[str, dict[str, Any]]]:
    """Lương tháng (cố định / theo ngày công) × luật × loại hợp đồng × bảng công × tiền ứng."""
    cases: list[tuple[str, dict[str, Any]]] = []
    rng = random.Random(20261111)
    combos = list(itertools.product(["new:verified"] * 4 + ["old:verified"] * 2 + CORE_LAWS, TERMS, ATTENDANCE))
    rng.shuffle(combos)
    for i, (law, term, a) in enumerate(combos[:200]):
        salary = rng.choice([30_000_000, 4_000_000, 12_345_678, 8_000_000, 90_000_000, 5_000_000])
        comp_code = rng.choice(["LUONG", "LUONG", "LUONGCD"])
        kind = rng.choice(["regular", "regular", "shadow"])
        prior = rng.choice([{}, {}, {"insured_period": True, "taxable_vnd": 10_000_000, "pit_vnd": 200_000, "insurance_vnd": 1_000_000},
                            {"taxable_vnd": 5_000_000, "pit_vnd": 0, "insurance_vnd": 0}])
        adv = rng.choice([0, 0, 1_000_000, 50_000_000])
        extras = rng.choice([[], [], [amount_entry("THUONG", 3_000_000)], [amount_entry("PHAT", 500_000)], [amount_entry("PHUCAP", 730_000)],
                             [amount_entry("THUONG", -9_000_000)]])
        entries = [amount_entry(comp_code, salary, source="pay.fixed"), *ATTENDANCE[a], *extras]
        cases.append((f"core/{i:03d}/{law}/{term}/{a}/{comp_code}/{kind}", snapshot(law=law, entries=entries, terms=TERMS[term], kind=kind, advances=adv,
                                                                                    prior=prior)))
    return cases


def _piece_rate() -> list[tuple[str, dict[str, Any]]]:
    """Công × mức: giờ / lượt / buổi / ca / ngày; mức cụ thể nhất; làm tròn nửa lên; thiếu mức; ngưỡng 10 %."""
    cases: list[tuple[str, dict[str, Any]]] = []
    rates = [rate(1, "CONG", "hour", 50_000), rate(2, "CONG", "hour", 80_000, role="senior"), rate(3, "CONG", "hour", 95_000, role="senior", party="p1"),
             rate(4, "CONG", "piece", 12_500, frm="2026-10-15"), rate(5, "CONG", "piece", 11_000), rate(6, "CONG", "session", 333_333),
             rate(7, "CONG", "shift", 150_000, party="p2"), rate(8, "CONG", "day", 1_000_001, role="x"), rate(9, "THUONG", "month", 9)]
    qty_sets = {
        "hours": [qty_entry("hour", "8", day="2026-10-02"), qty_entry("hour", "7.5", "senior", "2026-10-03")],
        "hours-party": [qty_entry("hour", "3", "senior"), qty_entry("hour", "3", "senior", "2026-10-02")],
        "pieces-split-rate": [qty_entry("piece", "100", day="2026-10-10"), qty_entry("piece", "100", day="2026-10-20")],
        "round-half": [qty_entry("session", "1.5"), qty_entry("session", "0.001")],
        "round-third": [qty_entry("session", "0.333"), qty_entry("session", "2.675")],
        "tiny": [qty_entry("hour", "0.01")],
        "no-rate-shift": [qty_entry("shift", "2")],
        "no-rate-piece-early": [qty_entry("piece", "10", day="2026-01-01")],
        "wrong-calc": [qty_entry("month", "1")],
        "big": [qty_entry("day", "20", "x"), qty_entry("hour", "160")],
        "mixed": [qty_entry("hour", "10"), qty_entry("piece", "40"), qty_entry("session", "3"), qty_entry("shift", "1")],
        "zero": [qty_entry("hour", "0")],
    }
    extras = {"none": [], "bonus": [amount_entry("THUONG", 1_000_000)], "penalty": [amount_entry("PHAT", 2_000_000)],
              "source": [amount_entry("", 750_000, source="sales.commission"), amount_entry("", 250_000, source="other.thing")],
              "recover": [amount_entry("THUONG", -40_000_000)]}
    i = 0
    for law, (qn, q), (en, ex), term, kind in itertools.product(
            ["new:verified", "new:assumption", "empty", "flat-only:verified", "new:verified-except:PIT_FLAT_10"], qty_sets.items(), extras.items(),
            ["service", "fixed2-8M-I", "indef-20M-I-dep1", "probation"], ["regular", "shadow"]):
        if (i := i + 1) % 31 and not (law == "new:verified" and qn in ("mixed", "round-half", "round-third", "no-rate-shift") and kind == "regular"):
            continue   # lưới thưa: ~1/9, riêng ca khó (luật đã xác minh, kỳ thật) giữ đủ
        cases.append((f"piece/{i:04d}/{law}/{qn}/{en}/{term}/{kind}", snapshot(law=law, entries=[*q, *ex], terms=TERMS[term], rates=rates, kind=kind)))
    return cases


def _overtime() -> list[tuple[str, dict[str, Any]]]:
    cases: list[tuple[str, dict[str, Any]]] = []
    ot_sets = {
        "ot150": [qty_entry("hour", "10", "ot.150")],
        "ot-all": [qty_entry("hour", "10", "ot.150"), qty_entry("hour", "4", "ot.200"), qty_entry("hour", "8", "ot.300")],
        "ot-frac": [qty_entry("hour", "2.75", "ot.150"), qty_entry("hour", "0.333", "ot.300")],
        "ot-two-months": [qty_entry("hour", "5", "ot.150"), qty_entry("hour", "6", "ot.150", "2026-09-30")],
        "ot-two-entries-same": [qty_entry("hour", "3", "ot.200", "2026-10-05"), qty_entry("hour", "4", "ot.200", "2026-10-12")],
    }
    nontax = {**COMPONENTS, "LAM_THEM": {**COMPONENTS["LAM_THEM"], "taxable": False}}
    no_oc = {k: v for k, v in COMPONENTS.items() if k != "LAM_THEM"}
    comp_sets = {"taxable": COMPONENTS, "nontaxable": nontax, "no-ot-component": no_oc}
    i = 0
    for law, (on, o), (an, a), (cn, cs), base, kind in itertools.product(
            ["new:verified", "new:assumption", "new:verified-minus:OT_PAY", "new:verified-except:OT_PAY", "new:verified-minus:PIT_OT_EXEMPT",
             "old:verified"], ot_sets.items(), ATTENDANCE.items(), comp_sets.items(), [30_000_000, 0, 7_777_777], ["regular", "shadow"]):
        if (i := i + 1) % 47 and not (law == "new:verified" and an in ("full", "std26+") and cn == "taxable" and kind == "regular" and base == 30_000_000):
            continue
        entries = [amount_entry("LUONGCD", base, source="pay.fixed"), *a, *o] if base else [*a, *o]
        cases.append((f"ot/{i:05d}/{law}/{on}/{an}/{cn}/{base}/{kind}", snapshot(law=law, entries=entries, comps=cs, kind=kind,
                                                                                  terms=TERMS["indef-20M-I-dep1"])))
    return cases


def _misc() -> list[tuple[str, dict[str, Any]]]:
    """Dòng nhập tay / từ nguồn / sai cách tính / hoàn / thu hồi / cấn ứng / thực nhận không dương / không có dòng nào."""
    c: list[tuple[str, dict[str, Any]]] = []
    flat = TERMS["service"]

    def add(name: str, **kw: Any) -> None:
        c.append((f"misc/{name}", snapshot(**kw)))

    for law in ("new:verified", "new:assumption"):
        for kind in ("regular", "shadow"):
            k = f"{law}/{kind}"
            add(f"empty/{k}", law=law, entries=[], kind=kind, terms=flat)
            add(f"no-terms/{k}", law=law, entries=[amount_entry("THUONG", 9_000_000)], terms=None, kind=kind)
            add(f"fixed-wrong-calc/{k}", law=law, entries=[amount_entry("THUONG", 6_000_000, source="pay.fixed")], terms=flat, kind=kind)
            add(f"manual-wrong-calc/{k}", law=law, entries=[amount_entry("LUONGCD", 6_000_000)], terms=flat, kind=kind)
            add(f"unknown-component/{k}", law=law, entries=[amount_entry("NOPE", 6_000_000)], terms=flat, kind=kind)
            add(f"source-fallback/{k}", law=law, entries=[amount_entry("", 6_000_000, source="sales.x"), amount_entry("", 1_000_000, source="zzz.y")],
                terms=flat, kind=kind)
            add(f"source-no-catch-all/{k}", law=law, entries=[amount_entry("", 6_000_000, source="zzz.y")], terms=flat, kind=kind,
                comps={x: v for x, v in COMPONENTS.items() if x != "KHAC"})
            add(f"net-negative/{k}", law=law, entries=[amount_entry("THUONG", 6_000_000), amount_entry("PHAT", 9_000_000)], terms=flat, kind=kind)
            add(f"net-exactly-zero/{k}", law=law, entries=[amount_entry("THUONG", 6_000_000), amount_entry("PHAT", 6_000_000)], terms=flat, kind=kind)
            add(f"refund-deduction/{k}", law=law, entries=[amount_entry("PHAT", -800_000), amount_entry("THUONG", 6_000_000)], terms=flat, kind=kind)
            add(f"clawback-earning/{k}", law=law, entries=[amount_entry("THUONG", -500_000), amount_entry("PHUCAP", 600_000)], terms=flat, kind=kind)
            add(f"zero-amount/{k}", law=law, entries=[amount_entry("THUONG", 0)], terms=flat, kind=kind)
            for adv in (0, 1, 2_000_000, 6_000_000, 99_000_000):
                add(f"advance-{adv}/{k}", law=law, entries=[amount_entry("THUONG", 6_000_000)], terms=flat, kind=kind, advances=adv)
                add(f"advance-no-component-{adv}/{k}", law=law, entries=[amount_entry("THUONG", 6_000_000)], terms=flat, kind=kind, advances=adv,
                    comps={x: v for x, v in COMPONENTS.items() if x != "UNG"})
            for amt in (4_999_999, 5_000_000, 5_000_001, 123_456_789):
                add(f"flat-threshold-{amt}/{k}", law=law, entries=[amount_entry("THUONG", amt)], terms=flat, kind=kind)
            add(f"two-prorata-months/{k}", law=law, terms=TERMS["indef-20M-I-dep1"], kind=kind,
                entries=[amount_entry("LUONG", 20_000_000, source="pay.fixed", day="2026-09-01"), amount_entry("LUONG", 25_000_000, source="pay.fixed"),
                         *ATTENDANCE["two-months"]])
            add(f"progressive-dependants-5/{k}", law=law, terms=_terms("indefinite", insured=30_000_000, deps=5), kind=kind,
                entries=[amount_entry("LUONGCD", 60_000_000, source="pay.fixed"), *ATTENDANCE["full"]])
            for prior in ({"pit_vnd": 9_000_000, "taxable_vnd": 1_000_000, "insurance_vnd": 0}, {"insured_period": True}, {"pit_vnd": 0}):
                add(f"prior-{sorted(prior)}/{k}", law=law, terms=TERMS["indef-20M-I-dep1"], kind=kind, prior=prior,
                    entries=[amount_entry("THUONG", 40_000_000)])
    for region in ("I", "II", "III", "IV"):
        for ins in (1_000_000, 3_700_000, 4_140_000, 4_730_000, 5_310_000, 50_600_000, 106_200_000, 106_200_001, 500_000_000):
            add(f"insurance-{region}-{ins}", law="new:verified", terms=_terms("indefinite", insured=ins, region=region, union=True),
                entries=[amount_entry("LUONGCD", max(ins, 6_000_000), source="pay.fixed"), *ATTENDANCE["full"]])
    add("bad-region-code", law="new:verified", terms=_terms("indefinite", region="V"), entries=[amount_entry("LUONGCD", 9_000_000, source="pay.fixed")])
    add("month-base-below-floor", law="new:verified", terms=_terms("indefinite", insured=6_000_000),
        entries=[amount_entry("LUONGCD", 4_000_000, source="pay.fixed"), *ATTENDANCE["full"]])
    add("pay-date-other-month", law="old:verified", pay_date="2026-03-05", period="2026-02-01", entries=[amount_entry("THUONG", 9_000_000)],
        terms=flat)
    return c


def build_cases() -> list[tuple[str, dict[str, Any]]]:
    return [*_core_grid(), *_piece_rate(), *_overtime(), *_misc()]


def resolve(inputs: dict[str, Any], laws: dict[str, Any]) -> dict[str, Any]:
    """Thay ``"@tên"`` bằng bộ quy tắc (bản sao — để kiểm hàm không sửa đầu vào)."""
    out = copy.deepcopy(inputs)
    if isinstance(out["law"], str):
        out["law"] = copy.deepcopy(laws[out["law"][1:]])
    return out


def run(inputs: dict[str, Any]) -> Any:
    """Kết quả để so: dict đầu ra, hoặc ``{"raises": <tên lỗi>}`` — hiện trạng cũng là một phần đặc tả."""
    try:
        return engine.compute(inputs)
    except Exception as e:  # đặc tả hiện trạng: ghi lại cả lỗi
        return {"raises": type(e).__name__}


def read_golden() -> dict[str, Any]:
    """Golden đã chụp (``{"laws": …, "cases": […]}``) — giải nén gzip rồi đọc JSON."""
    return json.loads(gzip.decompress(GOLDEN.read_bytes()).decode("utf-8"))


def write_golden(doc: dict[str, Any]) -> None:
    text = json.dumps(doc, ensure_ascii=False, indent=None, separators=(",", ":")) + "\n"
    GOLDEN.parent.mkdir(exist_ok=True)
    GOLDEN.write_bytes(gzip.compress(text.encode("utf-8"), compresslevel=9, mtime=0))   # mtime = 0 ⇒ sinh lại cùng nội dung ra cùng byte (diff git sạch)


def main() -> None:
    cases = build_cases()
    used = {c[1]["law"][1:] for c in cases}
    laws = {k: v for k, v in LAWS.items() if k in used}
    names = [n for n, _ in cases]
    assert len(set(names)) == len(names)
    doc = {"laws": laws, "cases": [{"name": n, "inputs": i, "output": run(resolve(i, laws))} for n, i in cases]}
    write_golden(doc)
    print(f"{len(cases)} cases, {GOLDEN.stat().st_size // 1024} KiB (gzip)")


if __name__ == "__main__":
    main()
