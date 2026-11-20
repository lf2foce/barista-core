"""Bộ quy định có phiên bản (quyết định 0067) — luật / nghị định là DỮ LIỆU theo ngày hiệu lực, code chỉ biết vài LOẠI quy tắc.

Mỗi bộ (vd ``vn-tax`` — thuế & BHXH Việt Nam) là một thư mục ``barista/regulations/<bộ>/``, mỗi phiên bản một file ``<ngày hiệu lực>.json`` là
BẢN CHỤP ĐẦY ĐỦ (không phải phần chênh lệch) — đọc một file là biết toàn bộ quy định áp từ ngày đó, git log của thư mục là lịch sử luật.
Có nghị định mới ⇒ chép file mới nhất, sửa, đặt ngày hiệu lực, ghi căn cứ ⇒ phát hành; KHÔNG sửa code — trừ khi văn bản đẻ ra một LOẠI quy tắc
code chưa biết (khi đó thêm loại ở đây, có test).

Loại quy tắc code biết:
* ``obligations`` — nghĩa vụ định kỳ: mã, tên, kỳ (tháng / quý / năm), hạn = ngày ``day`` (31 = cuối tháng) của tháng thứ ``months`` sau tháng
  cuối kỳ (0 = trong chính tháng đó), điều kiện ``when`` trên HỒ SƠ khách, ``basis`` (điều khoản căn cứ — bắt buộc).
* ``params`` — tham số có căn cứ (ngưỡng, mức): ``{key: {label, value, basis}}``.
* ``profile`` — dữ kiện của khách mà quy tắc dựa vào (loại hình, kỳ khai…): tuỳ chọn + mặc định. Khách KHÔNG sửa luật, chỉ khai dữ kiện.
* ``calendar`` — hạn rơi vào ngày nghỉ thì dời (``shift: next_workday``), danh sách ngày lễ.

Phiên bản áp cho một kỳ = phiên bản mới nhất có ngày hiệu lực ≤ ngày cuối kỳ. ``status``: ``assumption`` (chưa người có chuyên môn xác nhận —
màn hình / trợ lý phải nói rõ) hoặc ``verified`` (ghi ai xác nhận, ngày nào).

TRẠNG THÁI TỪNG QUY TẮC (0150 §6): mỗi nghĩa vụ / tham số có ``status`` + ``verified_by`` RIÊNG (không khai ⇒ theo file). ``status`` của file là TÓM
TẮT: file ghi ``verified`` thì mọi quy tắc trong file phải ``verified``. Quy tắc tính tiền chỉ được dùng ở lần tính thật khi ĐÃ xác minh —
``require_verified(refs)`` trả các quy tắc chưa xác minh (mã ``<bộ>@<phiên bản>:<mã>``, như ``rule_ref`` đã lưu).

HỢP ĐỒNG MÃ (0154): mỗi bộ có NGƯỜI DÙNG (module đọc tham số theo tên / áp nghĩa vụ) khai ``declare(bộ, required=…, optional=…, …)`` lúc import.
Hai chiều: (1) mọi mã tham số / nghĩa vụ trong file phải là mã người dùng biết — mã lạ (gõ sai, văn bản đẻ ra loại quy tắc mới) ⇒ ``RegulationError``
chứ không im lặng bị bỏ qua; (2) phiên bản MỚI NHẤT của bộ phải có đủ ``required`` — quên một mã là lỗi, không phải "tính thiếu". ``check_contracts()``
chạy khi nạp lõi (``kernel/loader.py``) và trong ``tests/test_regulations.py`` trên mọi file phát hành.

NGÀY PHÁT HÀNH (0154): mỗi file ghi ``published_on`` (yyyy-mm-dd — ngày file được phát hành lên sản phẩm). ``effective_from`` < ``published_on`` là HỒI TỐ:
kỳ nháp tính lại sẽ đổi số ⇒ file phải ghi ``retroactive_reason`` (vì sao, ai chấp thuận), nếu không không nạp được.
"""

import calendar
import itertools
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from barista.kernel import business_calendar

ROOT = Path(__file__).resolve().parent.parent / "regulations"
# Bộ quy định ĐI CÙNG GÓI NGOÀI LÕI (0155): tên bộ → thư mục gốc trong package của gói (``<gốc>/<bộ>/<ngày hiệu lực>.json`` — cùng bố cục ``ROOT``),
# gói khai bằng ``declare(bộ, …, root=…)`` lúc import. Gỡ gói ⇒ bộ của nó không còn; lõi không mang bộ nào không có module của lõi dùng.
EXTRA: dict[str, Path] = {}
CADENCES = ("monthly", "quarterly", "yearly")
STATUS = {"assumption": "Giả định — chưa xác nhận", "verified": "Đã xác nhận"}
# CHÍNH SÁCH NGÀY NGHỈ của một nghĩa vụ (0068) — thuộc tính riêng, không suy từ "ai khai nghĩa vụ": bộ quy định điền sẵn cho nghĩa vụ theo luật,
# kế toán chọn cho nghĩa vụ tự khai; hạn đã được người có quyền điều chỉnh thì giữ đúng ngày đã xác nhận.
HOLIDAY_RULES = {"keep": "Giữ nguyên ngày", "next_workday": "Dời sang ngày làm việc kế tiếp"}
_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,29}$")


class RegulationError(ValueError):
    """File quy định sai cấu trúc — chặn lúc nạp (test kiểm mọi file trước khi phát hành)."""


@dataclass(frozen=True)
class Rule:
    code: str
    label: str
    cadence: str
    months: int
    day: int
    basis: str
    when: dict[str, tuple[str, ...]]
    status: str = "assumption"
    verified_by: str = ""

    def matches(self, facts: dict[str, str]) -> bool:
        return all(facts.get(k) in vals for k, vals in self.when.items())


@dataclass(frozen=True)
class Version:
    pack: str
    title: str
    version: str
    effective_from: date
    published_on: date
    retroactive_reason: str
    status: str
    verified_by: str
    summary: str
    sources: tuple[dict[str, str], ...]
    profile: dict[str, dict[str, Any]]
    shift: str
    calendar_id: str
    calendar_basis: str
    obligations: tuple[Rule, ...]
    params: Mapping[str, Mapping[str, Any]]   # chỉ đọc (review 09/11): trạng thái xác minh trong bộ nhớ đệm không lật được giữa chừng

    @property
    def ref(self) -> str:
        return f"{self.pack}@{self.version}"

    @property
    def holiday_rule(self) -> str:
        """Chính sách ngày nghỉ phiên bản này điền cho nghĩa vụ theo luật."""
        return "next_workday" if self.shift == "next_workday" else "keep"

    def facts(self, given: dict[str, Any]) -> dict[str, str]:
        """Hồ sơ khách đủ khoá theo phiên bản này: thiếu ⇒ mặc định; khoá phiên bản không khai ⇒ bỏ (phiên bản cũ không biết dữ kiện mới)."""
        return {k: str(given[k]) if given.get(k) in d["options"] else d["default"] for k, d in self.profile.items()}

    def obligations_for(self, given: dict[str, Any]) -> list[Rule]:
        f = self.facts(given)
        return [r for r in self.obligations if r.matches(f)]

    @property
    def retroactive(self) -> bool:
        return self.effective_from < self.published_on

    def rule_status(self, code: str) -> tuple[str, str] | None:
        """(status, verified_by) của MỘT quy tắc (tham số hoặc nghĩa vụ) theo mã; None = phiên bản không có mã này."""
        if code in self.params:
            return self.params[code]["status"], self.params[code]["verified_by"]
        hit = next((r for r in self.obligations if r.code == code), None)
        return (hit.status, hit.verified_by) if hit else None

def _str(d: dict[str, Any], key: str, where: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip():
        raise RegulationError(f"{where}: thiếu “{key}”")
    return v.strip()


def _rule_status(d: dict[str, Any], where: str, status: str, by: str) -> tuple[str, str]:
    """Trạng thái riêng của một quy tắc: không khai ⇒ theo file. Đã xác minh thì phải ghi ai, ngày nào."""
    st = str(d.get("status") or status)
    if st not in STATUS:
        raise RegulationError(f"{where}: status phải là {list(STATUS)}")
    who = str(d.get("verified_by") or (by if st == status else "")).strip()
    if st == "verified" and not who:
        raise RegulationError(f"{where}: đã xác minh thì ghi verified_by (ai, ngày nào)")
    return st, who


def _load(path: Path) -> Version:
    where = f"{path.parent.name}/{path.name}"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RegulationError(f"{where}: JSON hỏng — {e}") from e
    pack, ver = _str(raw, "pack", where), _str(raw, "version", where)
    if pack != path.parent.name or ver != path.stem or _str(raw, "effective_from", where) != ver:
        raise RegulationError(f"{where}: pack / version / effective_from phải trùng tên thư mục / tên file")
    published = _str(raw, "published_on", where)
    try:
        published_on = date.fromisoformat(published)
    except ValueError as e:
        raise RegulationError(f"{where}: published_on phải là yyyy-mm-dd") from e
    if published_on.isoformat() != published:
        raise RegulationError(f"{where}: published_on phải là yyyy-mm-dd")
    retro = str(raw.get("retroactive_reason") or "").strip()
    if date.fromisoformat(ver) < published_on and not retro:
        raise RegulationError(f"{where}: hiệu lực {ver} sớm hơn ngày phát hành {published} — hồi tố đổi số kỳ nháp, ghi retroactive_reason (vì sao, ai chấp thuận)")
    status = _str(raw, "status", where)
    if status not in STATUS:
        raise RegulationError(f"{where}: status phải là {list(STATUS)}")
    if status == "verified" and not str(raw.get("verified_by") or "").strip():
        raise RegulationError(f"{where}: đã xác nhận thì ghi verified_by (ai, ngày nào)")
    profile: dict[str, dict[str, Any]] = {}
    for key, d in (raw.get("profile") or {}).items():
        opts = d.get("options") or {}
        if not opts or d.get("default") not in opts:
            raise RegulationError(f"{where}: hồ sơ “{key}” cần options và default nằm trong options")
        profile[key] = {"label": _str(d, "label", f"{where} hồ sơ {key}"), "options": {str(k): str(v) for k, v in opts.items()},
                        "default": str(d["default"]), "basis": str(d.get("basis") or "")}
    rules: list[Rule] = []
    for o in raw.get("obligations") or []:
        w = f"{where} nghĩa vụ {o.get('code')}"
        code = _str(o, "code", w)
        if not _CODE.match(code) or o.get("cadence") not in CADENCES:
            raise RegulationError(f"{w}: mã / kỳ sai")
        due = o.get("due") or {}
        if not (isinstance(due.get("months"), int) and 0 <= due["months"] <= 12 and isinstance(due.get("day"), int) and 1 <= due["day"] <= 31):
            raise RegulationError(f"{w}: hạn sai (months 0–12, day 1–31)")
        when: dict[str, tuple[str, ...]] = {}
        for k, v in (o.get("when") or {}).items():
            vals = tuple(v) if isinstance(v, list) else (v,)
            if k not in profile or not set(vals) <= set(profile[k]["options"]):
                raise RegulationError(f"{w}: điều kiện “{k}={v}” không có trong hồ sơ của phiên bản")
            when[k] = vals
        rules.append(Rule(code, _str(o, "label", w), o["cadence"], due["months"], due["day"], _str(o, "basis", w), when,
                          *_rule_status(o, w, status, str(raw.get("verified_by") or ""))))
    # mỗi tổ hợp hồ sơ: một mã ứng với TỐI ĐA một quy tắc (hai quy tắc cùng mã cùng khớp = luật mâu thuẫn trong file)
    keys = list(profile)
    for combo in itertools.product(*(profile[k]["options"] for k in keys)):
        facts = dict(zip(keys, combo, strict=True))
        codes = [r.code for r in rules if r.matches(facts)]
        if len(codes) != len(set(codes)):
            raise RegulationError(f"{where}: hai quy tắc cùng mã cùng áp cho hồ sơ {facts}")
    params: dict[str, dict[str, Any]] = {}
    for key, p in (raw.get("params") or {}).items():
        if "value" not in p:
            raise RegulationError(f"{where} tham số {key}: thiếu value")
        st, who = _rule_status(p, f"{where} tham số {key}", status, str(raw.get("verified_by") or ""))
        params[key] = {"label": _str(p, "label", f"{where} tham số {key}"), "value": p["value"], "basis": _str(p, "basis", f"{where} tham số {key}"),
                       "status": st, "verified_by": who}
    # status của file là TÓM TẮT: "đã xác minh" cả file mà còn quy tắc chưa xác minh = file tự mâu thuẫn
    if status == "verified" and (any(r.status != "verified" for r in rules) or any(p["status"] != "verified" for p in params.values())):
        raise RegulationError(f"{where}: file ghi verified nhưng còn quy tắc chưa xác minh")
    cal = raw.get("calendar") or {}
    if cal.get("shift", "none") not in ("none", "next_workday"):
        raise RegulationError(f"{where}: calendar.shift phải là none / next_workday")
    cid = str(cal.get("id") or "")
    if cal.get("shift") == "next_workday" and not any(k == cid for k, _ in business_calendar.years()):
        raise RegulationError(f"{where}: calendar.id không có lịch tương ứng")
    return Version(pack, _str(raw, "title", where), ver, date.fromisoformat(ver), published_on, retro, status, str(raw.get("verified_by") or ""),
                   str(raw.get("summary") or ""), tuple(raw.get("sources") or ()), profile, cal.get("shift", "none"),
                   cid, str(cal.get("basis") or ""), tuple(rules), MappingProxyType({k: MappingProxyType(v) for k, v in params.items()}))


@cache
def packs() -> dict[str, tuple[Version, ...]]:
    """Mọi bộ quy định, phiên bản xếp theo ngày hiệu lực tăng dần. Đọc một lần / tiến trình (``reload`` cho test)."""
    out: dict[str, tuple[Version, ...]] = {}
    dirs = [*(sorted(p for p in ROOT.iterdir() if p.is_dir()) if ROOT.is_dir() else ()), *(root / name for name, root in sorted(EXTRA.items()))]
    for d in dirs:
        vs = sorted((_load(f) for f in d.glob("*.json")), key=lambda v: v.effective_from)
        if vs and d.name in out:
            raise RegulationError(f"{d.name}: bộ quy định có ở hai nơi (lõi và một gói) — mỗi bộ một chủ")
        if vs:
            out[d.name] = tuple(vs)
    return out


def reload(root: Path | None = None) -> None:
    global ROOT
    if root is not None:
        ROOT = root
    packs.cache_clear()


def version_on(pack: str, day: date) -> Version | None:
    """Phiên bản đang hiệu lực ngày ``day`` (None = bộ không có / ngày trước phiên bản đầu tiên)."""
    found = None
    for v in packs().get(pack, ()):
        if v.effective_from <= day:
            found = v
    return found


def require_verified(refs: Iterable[str]) -> list[str]:
    """Các quy tắc CHƯA xác minh trong ``refs`` (``<bộ>@<phiên bản>:<mã>``) — gồm cả mã không còn tìm thấy (bộ / phiên bản / mã lạ): không biết thì coi
    như chưa xác minh (đóng khi không rõ). Lần tính thật (tiền) chỉ chạy khi danh sách này rỗng (0150 §6)."""
    out = []
    for ref in dict.fromkeys(refs):
        pack, _, rest = ref.partition("@")
        ver, _, code = rest.partition(":")
        v = next((x for x in packs().get(pack, ()) if x.version == ver), None)
        st = v.rule_status(code) if v is not None and code else None
        if st is None or st[0] != "verified":
            out.append(ref)
    return out


def upcoming(pack: str, day: date) -> list[Version]:
    return [v for v in packs().get(pack, ()) if v.effective_from > day]


def param(pack: str, key: str, day: date) -> Any:
    """Giá trị tham số có hiệu lực ngày ``day`` (None = chưa có quy định)."""
    v = version_on(pack, day)
    return v.params[key]["value"] if v and key in v.params else None


def period_end(month: date) -> date:
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def raw_due(period_month: date, months: int, day: int) -> date:
    """Ngày ``day`` (kẹp theo độ dài tháng) của tháng thứ ``months`` sau tháng ``period_month`` — chưa xét ngày nghỉ."""
    y, m = divmod(period_month.month - 1 + months, 12)
    t = date(period_month.year + y, m + 1, 1)
    return t.replace(day=min(day, calendar.monthrange(t.year, t.month)[1]))


def apply_holiday(rule: str, calendar_id: str, day: date) -> business_calendar.Adjusted:
    """Áp CHÍNH SÁCH NGÀY NGHỈ đã khai của nghĩa vụ: giữ nguyên, hoặc dời theo lịch của NĂM chứa hạn (trả cả nguồn lịch / cảnh báo để lưu)."""
    if rule not in HOLIDAY_RULES:
        raise ValueError(f"Chính sách ngày nghỉ lạ: {rule}")
    return business_calendar.next_workday(calendar_id, day) if rule == "next_workday" else business_calendar.Adjusted(day)


def due_date(v: Version | None, period_month: date, months: int, day: int) -> date:
    return due_result(v, period_month, months, day).day


def due_result(v: Version | None, period_month: date, months: int, day: int) -> business_calendar.Adjusted:
    """Hạn của một quy tắc theo chính sách ngày nghỉ của phiên bản ``v`` (None ⇒ giữ nguyên ngày)."""
    d = raw_due(period_month, months, day)
    return apply_holiday(v.holiday_rule, v.calendar_id, d) if v is not None else business_calendar.Adjusted(d)


# ── Hợp đồng mã giữa bộ quy định và module dùng nó (0154) ──

@dataclass(frozen=True)
class Contract:
    """Điều module khai về bộ của mình. ``required`` — mã code ĐỌC THEO TÊN và không chạy được nếu thiếu (có trong phiên bản mới nhất). ``optional`` — mã
    biết nhưng không bắt buộc (hiển thị / chưa dùng). ``open_kind`` — tham số có ``value.kind`` bằng giá trị này được một vòng lặp chung áp dụng (không
    cần liệt kê tên). ``obligations`` — module có áp nghĩa vụ định kỳ của bộ (sổ sách); không thì file KHÔNG được chứa nghĩa vụ."""
    required: frozenset[str] = frozenset()
    optional: frozenset[str] = frozenset()
    open_kind: str = ""
    obligations: bool = False


CONTRACTS: dict[str, Contract] = {}


def declare(pack: str, *, required: Iterable[str] = (), optional: Iterable[str] = (), open_kind: str = "", obligations: bool = False,
            root: Path | None = None) -> None:
    """Module khai hợp đồng của bộ ``pack`` lúc import (gọi lại cùng nội dung an toàn; khác nội dung ⇒ lỗi — hai module không tranh một bộ).
    ``root`` (API 1.14): bộ ĐI CÙNG gói ngoài lõi — thư mục gốc trong package của gói, file ở ``<root>/<bộ>/<ngày hiệu lực>.json`` (``EXTRA``)."""
    c = Contract(frozenset(required), frozenset(optional), open_kind, obligations)
    if CONTRACTS.setdefault(pack, c) != c:
        raise RegulationError(f"{pack}: hai module khai hợp đồng mã khác nhau")
    if root is not None:
        if EXTRA.setdefault(pack, Path(root)) != Path(root):
            raise RegulationError(f"{pack}: hai gói khai hai thư mục khác nhau cho cùng một bộ")
        packs.cache_clear()


def known_codes(pack: str, versions: Iterable[Version] | None = None) -> frozenset[str]:
    """Mọi mã THAM SỐ module của bộ biết: ``required`` + ``optional`` + mã có ``value.kind == open_kind`` ở bất kỳ phiên bản nào (một nguồn cho
    ``pay.policy`` chặn khoản công ty trùng mã luật)."""
    c = CONTRACTS.get(pack)
    if c is None:
        return frozenset()
    out = set(c.required | c.optional)
    if c.open_kind:
        for v in versions if versions is not None else packs().get(pack, ()):
            out |= {k for k, p in v.params.items() if isinstance(p["value"], Mapping) and p["value"].get("kind") == c.open_kind}
    return frozenset(out)


def check_contract(pack: str, versions: tuple[Version, ...]) -> None:
    """Hợp đồng của MỘT bộ trên danh sách phiên bản: mã lạ ở bất kỳ phiên bản nào, thiếu mã bắt buộc ở phiên bản mới nhất, nghĩa vụ khi module không áp."""
    c = CONTRACTS.get(pack)
    if c is None:
        raise RegulationError(f"{pack}: bộ quy định chưa có module nào khai hợp đồng mã (regulations.declare) — không ai dùng thì đừng phát hành")
    known = known_codes(pack, versions)
    for v in versions:
        where = f"{pack}/{v.version}.json"
        if (unknown := sorted(set(v.params) - known)):
            raise RegulationError(f"{where}: mã tham số lạ {', '.join(unknown)} — code không đọc mã này nên sẽ không bao giờ được áp dụng "
                                  f"(gõ sai mã, hoặc loại quy tắc mới cần thêm vào module — runbook 13)")
        if v.obligations and not c.obligations:
            raise RegulationError(f"{where}: có nghĩa vụ nhưng không module nào áp nghĩa vụ của bộ này")
    if versions and (missing := sorted(c.required - set(versions[-1].params))):
        raise RegulationError(f"{pack}/{versions[-1].version}.json: thiếu mã bắt buộc {', '.join(missing)} — phiên bản mới nhất là bản chụp ĐẦY ĐỦ "
                              f"(chép từ phiên bản trước rồi sửa; bãi bỏ một quy tắc thật thì sửa module + ghi quyết định)")


def check_contracts(*, strict: bool = False) -> None:
    """Hợp đồng mọi bộ đã phát hành. ``strict``: bộ không có người khai cũng là lỗi (test); khi nạp lõi chỉ kiểm bộ đã có người khai (gói không cài
    thì module của nó không khai)."""
    for pack, versions in packs().items():
        if strict or pack in CONTRACTS:
            check_contract(pack, versions)


# ── So sánh hai phiên bản (0154) ──

def _text(v: Any, limit: int = 160) -> str:
    out = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, sort_keys=True)
    return out if len(out) <= limit else out[:limit - 1] + "…"


def _rule_entries(v: Version) -> dict[tuple[str, str], dict[str, Any]]:
    """Mọi quy tắc của một phiên bản thành ``(loại, khoá) → {value, shown, basis, status}`` để so từng cái."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for code, p in v.params.items():
        out[("param", code)] = {"value": p["value"], "shown": f"{p['label']}: {_text(p['value'])}", "basis": p["basis"], "status": p["status"]}
    for r in v.obligations:
        when = " & ".join(f"{k}={'|'.join(vs)}" for k, vs in sorted(r.when.items()))
        key = f"{r.code} [{when}]" if when else r.code
        due = {"cadence": r.cadence, "months": r.months, "day": r.day}
        out[("obligation", key)] = {"value": due, "shown": f"{r.label}: {r.cadence}, hạn ngày {r.day} sau {r.months} tháng", "basis": r.basis, "status": r.status}
    for k, d in v.profile.items():
        val = {"options": d["options"], "default": d["default"]}
        out[("profile", k)] = {"value": val, "shown": f"{d['label']}: mặc định {d['default']} ({', '.join(d['options'])})", "basis": d["basis"], "status": ""}
    out[("calendar", "shift")] = {"value": [v.shift, v.calendar_id], "shown": f"{v.shift} · {v.calendar_id or 'không lịch'}", "basis": v.calendar_basis, "status": ""}
    return out


def compare(a: Version, b: Version) -> list[dict[str, Any]]:
    """Khác biệt từ ``a`` (cũ) sang ``b`` (mới): tham số, nghĩa vụ, dữ kiện hồ sơ, lịch — thêm / bỏ / đổi. Quy tắc giống hệt (cả giá trị, căn cứ,
    trạng thái) không có dòng. Chỉ đọc."""
    old, new = _rule_entries(a), _rule_entries(b)
    rows: list[dict[str, Any]] = []
    for key in sorted(old.keys() | new.keys()):
        x, y = old.get(key), new.get(key)
        if x is not None and y is not None and all(x[k] == y[k] for k in ("value", "basis", "status")):
            continue   # chỉ khác nhãn hiển thị (nếu có) không phải đổi quy tắc
        change = "added" if x is None else "removed" if y is None else "changed"
        status = f"{STATUS[x['status']]} → {STATUS[y['status']]}" if x and y and x["status"] != y["status"] else ""
        rows.append({"id": f"{key[0]}:{key[1]}", "kind": key[0], "code": key[1], "change": change, "old": x["shown"] if x else "", "new": y["shown"] if y else "",
                     "value_changed": bool(x and y and x["value"] != y["value"]), "basis": (y or x or {}).get("basis", ""), "status_change": status})
    return rows
