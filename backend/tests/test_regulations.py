"""Bộ quy định có phiên bản (quyết định 0067): luật / nghị định là DỮ LIỆU theo ngày hiệu lực — có văn bản mới thì thêm một file phiên bản, không sửa
code; kỳ nào dùng đúng phiên bản hiệu lực ngày cuối kỳ đó; kỳ đã mở giữ hạn đã tính (ghi phiên bản + căn cứ); khách chỉ khai dữ kiện, không sửa luật."""

import json
import shutil
import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from barista import seed
from barista.kernel import executor, loader, regulations
from barista.kernel.principal import Principal, system_principal
from tests.conftest import ok, owner_val, q

SHIPPED = regulations.ROOT


@pytest.fixture
def law_root(tmp_path: Path) -> Iterator[Path]:
    """Bản sao bộ quy định đang phát hành — test thêm phiên bản (nghị định mới) vào đây; xong trả lại bộ thật."""
    root = tmp_path / "regulations"
    shutil.copytree(SHIPPED, root)
    regulations.reload(root)
    yield root
    regulations.reload(SHIPPED)


def _write(root: Path, version: dict[str, Any]) -> None:
    (root / version["pack"] / f"{version['version']}.json").write_text(json.dumps(version, ensure_ascii=False), encoding="utf-8")


def _latest(root: Path) -> dict[str, Any]:
    return json.loads(sorted((root / "vn-tax").glob("*.json"))[-1].read_text(encoding="utf-8"))


async def _company(start: str) -> tuple[UUID, dict[str, Principal]]:
    d = f"{uuid.uuid4().hex[:6]}.quydinh.test"
    tid = await seed.create_tenant("Công ty theo luật (test)", d, packs=("books",), about="khách dịch vụ kế toán (test)",
                                   people=[("admin", "Quản trị", "admin", "QT", True), ("kt", "Kế toán", "bookkeeper", "KT", False)])
    people = {k: await seed.who(tid, k, d) for k in ("admin", "kt")}
    await ok(people["admin"], "books.configure", owner_email=people["kt"].email, start_period=start)
    return tid, people


async def _open(tid: UUID, as_of: str) -> None:
    assert (await executor.run(system_principal(tid), "books.open_due", {"as_of": as_of})).ok


async def _filings(tid: UUID, code: str) -> list[tuple[str, str, str]]:
    rows = await owner_val("""SELECT coalesce(jsonb_agg(jsonb_build_array(to_char(p.period_start, 'MM/YYYY'), f.due_on, f.rule_ref) ORDER BY p.period_start), '[]')
                              FROM books.filings f JOIN books.periods p ON p.id = f.period_id JOIN books.obligations o ON o.id = f.obligation_id
                              WHERE f.tenant_id = %s AND o.code = %s""", (tid, code))
    return [tuple(r) for r in rows]


def test_every_shipped_version_loads_and_cites_its_basis() -> None:
    regulations.reload(SHIPPED)
    # thư mục nào cũng nạp được, không bộ bị bỏ sót; bộ ĐI CÙNG gói ngoài lõi đang cài (``regulations.EXTRA`` — 0155) nằm ở package của gói
    assert set(regulations.packs()) - set(regulations.EXTRA) == {p.name for p in SHIPPED.iterdir() if p.is_dir()}
    for pack, vs in regulations.packs().items():   # MỌI bộ phát hành (không chỉ vn-tax): căn cứ, nguồn, trạng thái, ngày phát hành
        assert [v.version for v in vs] == sorted(v.version for v in vs), pack
        for v in vs:
            assert v.status in regulations.STATUS and v.sources and v.summary, (pack, v.version)
            assert all(r.basis and r.status in regulations.STATUS for r in v.obligations), (pack, v.version)
            assert all(len(p["basis"]) > 20 and p["status"] in regulations.STATUS and p["label"] for p in v.params.values()), (pack, v.version)
            assert v.params or v.obligations, (pack, v.version)
            assert v.published_on <= date.today(), (pack, v.version)                      # ngày phát hành không ở tương lai (không lách cờ hồi tố)
            assert not v.retroactive or v.retroactive_reason, (pack, v.version)
            if v.obligations:
                assert v.calendar_basis, (pack, v.version)
    vs = regulations.packs()["vn-tax"]
    assert len(vs) >= 2
    # luật đổi đúng ngày hiệu lực: ngưỡng chứng từ không dùng tiền mặt 20 triệu → 5 triệu từ 01/07/2025 (Luật GTGT 2024)
    assert regulations.param("vn-tax", "non_cash_min_vnd", date(2025, 6, 30)) == 20_000_000
    assert regulations.param("vn-tax", "non_cash_min_vnd", date(2025, 7, 1)) == 5_000_000
    assert regulations.version_on("vn-tax", date(2019, 12, 31)) is None
    v = regulations.version_on("vn-tax", date(2026, 10, 1))
    assert v is not None and regulations.due_date(v, date(2026, 9, 1), 1, 31) == date(2026, 11, 2)   # 31/10/2026 thứ Bảy ⇒ thứ Hai


def test_a_contradictory_or_misnamed_version_is_rejected(law_root: Path) -> None:
    bad: dict[str, Any] = {**_latest(law_root), "version": "2027-01-01", "effective_from": "2027-01-01"}
    gtgt = next(o for o in bad["obligations"] if o["code"] == "GTGT")
    bad["obligations"].append({**gtgt, "label": "GTGT trùng"})                       # hai quy tắc cùng mã cùng áp ⇒ luật mâu thuẫn
    _write(law_root, bad)
    regulations.reload()
    with pytest.raises(regulations.RegulationError, match="cùng mã"):
        regulations.packs()
    (law_root / "vn-tax" / "2027-01-01.json").unlink()
    _write(law_root, {**_latest(law_root), "version": "2027-01-01", "effective_from": "2027-02-01"})
    regulations.reload()
    with pytest.raises(regulations.RegulationError, match="trùng tên"):
        regulations.packs()


async def test_each_period_uses_the_law_in_force_at_its_end_and_keeps_it(law_root: Path) -> None:
    tid, _ = await _company("2025-05-01")
    await _open(tid, "2025-09-05")
    # BHXH tháng: quy định cũ hạn cuối CHÍNH tháng đó; Luật BHXH 2024 (01/07/2025) ⇒ cuối tháng sau. Ngày nghỉ dời theo lịch nghiệp vụ (0068):
    # 31/05/2025 thứ Bảy ⇒ 02/06; 31/08/2025 Chủ nhật, 01–02/09 nghỉ Quốc khánh ⇒ 03/09.
    assert await _filings(tid, "BHXH") == [("05/2025", "2025-06-02", "vn-tax@2020-07-01:BHXH"), ("06/2025", "2025-06-30", "vn-tax@2020-07-01:BHXH"),
                                           ("07/2025", "2025-09-03", "vn-tax@2025-07-01:BHXH"), ("08/2025", "2025-09-30", "vn-tax@2025-07-01:BHXH")]
    assert "41/2024/QH15" in await owner_val("SELECT basis FROM books.filings WHERE tenant_id = %s AND rule_ref LIKE '%%2025-07-01:BHXH' LIMIT 1",
                                             (tid,))

    # NGHỊ ĐỊNH MỚI: từ 01/01/2027 hạn GTGT quý thành ngày 25 — chỉ thêm một file phiên bản, không sửa code
    new: dict[str, Any] = {**_latest(law_root), "version": "2027-01-01", "effective_from": "2027-01-01", "summary": "Giả lập văn bản mới (test)"}
    for o in new["obligations"]:
        if o["code"] == "GTGT" and o["cadence"] == "quarterly":
            o["due"], o["basis"] = {"months": 1, "day": 25}, "Văn bản giả lập (test)"
    _write(law_root, new)
    regulations.reload()
    await _open(tid, "2027-04-02")
    gtgt = await _filings(tid, "GTGT")
    assert gtgt[:2] == [("06/2025", "2025-07-31", "vn-tax@2020-07-01:GTGT"),                # kỳ đã mở: giữ nguyên hạn + phiên bản đã tính
                        ("09/2025", "2025-10-31", "vn-tax@2025-07-01:GTGT")]
    assert ("12/2026", "2027-02-01", "vn-tax@2025-07-01:GTGT") in gtgt                        # quý 4/2026 kết thúc trước mốc ⇒ luật cũ (31/01 CN ⇒ 01/02)
    assert ("03/2027", "2027-04-26", "vn-tax@2027-01-01:GTGT") in gtgt                        # quý 1/2027 ⇒ luật mới, 25/04 CN ⇒ 26/04
    assert await owner_val("SELECT rule_version FROM books.obligations WHERE tenant_id = %s AND code = 'GTGT'", (tid,)) == "2027-01-01"


async def test_clients_declare_facts_not_law_and_manual_obligations_stay_theirs() -> None:
    tid, p = await _company("2026-07-01")
    admin = p["admin"]
    await ok(admin, "books.upsert_obligation", code="PHI-KHAC", label="Báo cáo riêng của khách", cadence="monthly", due_months=1, due_day=10)
    r = await executor.run(admin, "books.upsert_obligation", {"code": "GTGT", "label": "GTGT", "cadence": "monthly", "due_months": 1, "due_day": 20})
    assert r.status == "invalid" and "bộ quy định" in (r.error or "")                        # luật không sửa tay ở danh mục
    await ok(admin, "books.set_profile", fact="vat_cadence=monthly")                         # khách khai GTGT theo tháng ⇒ nghĩa vụ đổi theo
    assert await owner_val("SELECT cadence || '/' || due_day FROM books.obligations WHERE tenant_id = %s AND code = 'GTGT'", (tid,)) == "monthly/20"
    await ok(admin, "books.set_profile", fact="pit_cadence=none")                            # không trả thu nhập khấu trừ ⇒ ngừng TNCN + QT-TNCN
    off = await owner_val("SELECT array_agg(code ORDER BY code) FROM books.obligations WHERE tenant_id = %s AND NOT active", (tid,))
    assert off == ["QT-TNCN", "TNCN"]
    assert (await executor.run(admin, "books.set_profile", {"fact": "entity_kind=household"})).status == "invalid"   # loại hình đổi ở Thiết lập
    assert (await executor.run(admin, "books.set_profile", {"fact": "vat_cadence=weekly"})).status == "invalid"
    assert await owner_val("SELECT source FROM books.obligations WHERE tenant_id = %s AND code = 'PHI-KHAC'", (tid,)) == "manual"

    card = await q(p["kt"], "books.regulation")
    assert card["subtitle"].startswith("vn-tax@") and "Giả định" in json.dumps(card, ensure_ascii=False)
    await ok(admin, "books.configure", owner_email=p["kt"].email, start_period="2026-07-01", reg_pack="")   # bỏ bộ quy định
    assert await owner_val("SELECT count(*) FROM books.obligations WHERE tenant_id = %s AND source = 'rule'", (tid,)) == 0   # thành tự khai
    await ok(admin, "books.upsert_obligation", code="GTGT", label="Tờ khai thuế GTGT", cadence="quarterly", due_months=1, due_day=31)


async def test_a_new_version_between_periods_updates_the_list_and_tells_the_accountant(law_root: Path) -> None:
    """Phiên bản mới có hiệu lực GIỮA hai kỳ: vòng quét hằng ngày đổi danh mục ngay + báo một lần; kỳ đang mở không đổi hạn."""
    tid, p = await _company("2026-10-01")
    await _open(tid, "2026-12-05")
    before = await _filings(tid, "GTGT")
    sync = lambda day: executor.run(system_principal(tid), "books.sync_rules", {"as_of": day})  # noqa: E731
    assert (await sync("2026-12-20")).output == {"changed": 0}                                    # chưa có gì mới
    new: dict[str, Any] = {**_latest(law_root), "version": "2026-12-15", "effective_from": "2026-12-15", "summary": "Giả lập văn bản mới (test)."}
    for o in new["obligations"]:
        if o["code"] == "GTGT" and o["cadence"] == "quarterly":
            o["due"], o["basis"] = {"months": 1, "day": 25}, "Văn bản giả lập (test)"
    _write(law_root, new)
    regulations.reload()
    assert (await sync("2026-12-21")).output == {"changed": 1}
    assert await owner_val("SELECT due_day || '@' || rule_version FROM books.obligations WHERE tenant_id = %s AND code = 'GTGT'", (tid,)) == "25@2026-12-15"
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND kind = 'books' AND title LIKE '%%phiên bản mới%%'",
                           (p["kt"].user_id,)) == 1
    assert (await sync("2026-12-22")).output == {"changed": 0}                                    # báo một lần
    assert await _filings(tid, "GTGT") == before                                                  # kỳ đã mở giữ hạn đã tính
    assert await owner_val("SELECT rules_synced_on FROM books.setup WHERE tenant_id = %s", (tid,)) == date(2026, 12, 22)


async def test_periods_before_the_first_version_get_no_law_and_say_so() -> None:
    """Kỳ trước phiên bản đầu tiên: KHÔNG lấy luật phiên bản sau áp ngược — không sinh nghĩa vụ theo luật, báo thiếu dữ liệu quy định."""
    tid, p = await _company("2019-11-01")
    out = (await executor.run(system_principal(tid), "books.open_due", {"as_of": "2020-08-05"})).output
    assert out["no_rules"] == ["11/2019", "12/2019", "01/2020", "02/2020", "03/2020", "04/2020", "05/2020", "06/2020"]
    assert await _filings(tid, "BHXH") == [("07/2020", "2020-07-31", "vn-tax@2020-07-01:BHXH")]   # từ phiên bản đầu tiên mới có
    assert await owner_val("SELECT count(*) FROM books.filings f JOIN books.periods x ON x.id = f.period_id WHERE f.tenant_id = %s"
                           " AND x.period_start < '2020-07-01'", (tid,)) == 0
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE '%%chưa có dữ liệu quy định%%'",
                           (p["kt"].user_id,)) == 1


async def test_holiday_handling_is_declared_per_obligation_not_inferred_from_who_declared_it() -> None:
    """Gặp ngày nghỉ là CHÍNH SÁCH khai trên từng nghĩa vụ (0068): luật điền sẵn; nghĩa vụ tự khai thì kế toán chọn giữ nguyên hoặc dời theo lịch."""
    tid, p = await _company("2025-07-01")
    base = {"label": "Báo cáo riêng", "cadence": "monthly", "due_months": 1, "due_day": 31}
    await ok(p["admin"], "books.upsert_obligation", code="RIENG-GIU", **base, holiday_rule="keep")
    await ok(p["admin"], "books.upsert_obligation", code="RIENG-DOI", **base, holiday_rule="next_workday", holiday_calendar="vn-public")
    bad = await executor.run(p["admin"], "books.upsert_obligation", {"code": "RIENG-SAI", **base, "holiday_rule": "next_workday",
                                                                     "holiday_calendar": "khong-co"})
    assert bad.status == "invalid"
    await _open(tid, "2025-08-05")
    rows = await owner_val("""SELECT jsonb_object_agg(o.code, f.due_on || '|' || f.calendar_ref) FROM books.filings f
                              JOIN books.obligations o ON o.id = f.obligation_id WHERE f.tenant_id = %s""", (tid,))
    # hạn gốc 31/08/2025 (Chủ nhật), 01–02/09 nghỉ Quốc khánh
    assert rows["RIENG-GIU"] == "2025-08-31|"                                                 # giữ đúng ngày đã khai
    assert rows["RIENG-DOI"].startswith("2025-09-03|vn-public/2025@")                         # dời theo lịch, ghi bản lịch đã dùng
    assert rows["BHXH"].startswith("2025-09-03|vn-public/2025@")                              # luật: bộ quy định điền chính sách dời
    rule = await owner_val("SELECT holiday_rule || '/' || holiday_calendar FROM books.obligations WHERE tenant_id = %s AND code = 'BHXH'", (tid,))
    assert rule == "next_workday/vn-public"


def test_each_rule_carries_its_own_status_and_only_verified_rules_pass_the_gate(law_root: Path) -> None:
    """0150 §6: status + verified_by theo TỪNG quy tắc (không khai ⇒ theo file); status của file là tóm tắt; ``require_verified`` đóng khi không rõ."""
    pay = json.loads((law_root / "vn-payroll" / "2026-07-01.json").read_text(encoding="utf-8"))
    ref = "vn-payroll@2026-07-01:PIT_FLAT_10"
    assert regulations.require_verified([ref]) == [ref]                                   # bản phát hành: chưa ai xác minh
    assert regulations.require_verified(["vn-payroll@2099-01-01:PIT_FLAT_10", "khong-co@1:X"]) == ["vn-payroll@2099-01-01:PIT_FLAT_10", "khong-co@1:X"]
    rule = pay["params"]["PIT_FLAT_10"]
    _write(law_root, {**pay, "params": {"PIT_FLAT_10": {**rule, "status": "verified", "verified_by": ""}}})
    regulations.reload()
    with pytest.raises(regulations.RegulationError, match="verified_by"):               # xác minh mà không ghi ai, ngày nào
        regulations.packs()
    _write(law_root, {**pay, "status": "verified", "verified_by": "Kế toán A, 01/11/2026"})
    regulations.reload()
    with pytest.raises(regulations.RegulationError, match="còn quy tắc chưa xác minh"):   # file nói đã xác minh, quy tắc nói chưa
        regulations.packs()
    _write(law_root, {**pay, "params": {"PIT_FLAT_10": {**rule, "status": "verified", "verified_by": "Kế toán A, 01/11/2026"}}})
    regulations.reload()
    v = regulations.version_on("vn-payroll", date(2026, 11, 1))
    assert v is not None and v.status == "assumption" and v.rule_status("PIT_FLAT_10") == ("verified", "Kế toán A, 01/11/2026")
    assert regulations.require_verified([ref]) == []
    old = regulations.version_on("vn-tax", date(2025, 8, 1))
    assert old is not None and old.rule_status("BHXH") == ("assumption", "")              # file cũ không khai từng quy tắc ⇒ theo file


# ── Hợp đồng mã (0154): mã lạ / thiếu mã bắt buộc không được im lặng ──

def _payroll_newest(root: Path) -> tuple[Path, dict[str, Any]]:
    f = sorted((root / "vn-payroll").glob("*.json"))[-1]
    return f, json.loads(f.read_text(encoding="utf-8"))


def _put(f: Path, d: dict[str, Any]) -> None:
    f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    regulations.reload()


def test_every_shipped_pack_meets_its_code_contract_both_ways() -> None:
    """Mọi bộ phát hành CÓ người dùng khai hợp đồng, mọi mã trong file là mã người dùng biết, phiên bản mới nhất có đủ mã bắt buộc.
    Hợp đồng từng bộ: vn-payroll = mã engine lương đọc theo tên + mọi khoản đóng (value.kind=contribution); vn-labour = PUBLIC_HOLIDAYS / OT_CAPS / ANNUAL_LEAVE;
    vn-personal-data = hai ngưỡng tuổi; vn-tax = mọi nghĩa vụ + dữ kiện được sổ sách áp, tham số chỉ để hiện."""
    loader.load()
    regulations.reload(SHIPPED)
    regulations.check_contracts(strict=True)
    assert set(regulations.CONTRACTS) >= set(regulations.packs())
    c = regulations.CONTRACTS["vn-payroll"]
    newest = regulations.packs()["vn-payroll"][-1]
    assert c.required <= set(newest.params) and regulations.known_codes("vn-payroll") >= set(newest.params)
    # một nguồn cho "mã luật" ở pay: danh sách chặn khoản công ty dẫn từ hợp đồng, không phải bản thứ hai
    from barista.modules.pay import engine, policy
    assert policy.law_codes() == set(engine.RULE_CODES) | {k for v in regulations.packs()["vn-payroll"] for k, p in v.params.items()
                                                            if isinstance(p["value"], dict) and p["value"].get("kind") == "contribution"}
    assert {"SI_EMPLOYEE", "UNION_MEMBER_FEE", "OT_PAY", "PIT_FLAT_10"} <= policy.law_codes()


def test_a_forgotten_or_unknown_code_is_a_load_error_not_a_silent_skip(law_root: Path) -> None:
    loader.load()
    f, d = _payroll_newest(law_root)
    _put(f, {**d, "params": {**d["params"], "PIT_NEW_RULE": {"label": "Quy tắc mới", "value": {"x": 1}, "basis": "Văn bản giả lập (test) điều 1"}}})
    with pytest.raises(regulations.RegulationError, match="mã tham số lạ PIT_NEW_RULE"):    # engine không đọc ⇒ không bao giờ áp ⇒ chặn
        regulations.check_contracts()
    # khoản đóng mới (value.kind = contribution) được vòng lặp chung của engine áp ⇒ hợp lệ, không cần sửa code
    contrib = {**d["params"]["SI_EMPLOYER"], "label": "Bảo hiểm tai nạn (test)"}
    _put(f, {**d, "params": {**d["params"], "AI_EMPLOYER": contrib}})
    regulations.check_contracts()
    # quên một mã bắt buộc ở phiên bản mới nhất (chép thiếu)
    _put(f, {**d, "params": {k: v for k, v in d["params"].items() if k != "OT_PAY"}})
    with pytest.raises(regulations.RegulationError, match="thiếu mã bắt buộc OT_PAY"):
        regulations.check_contracts()
    # bộ không có module nào khai hợp đồng ⇒ test nghiêm lỗi; nghĩa vụ ở bộ mà module không áp nghĩa vụ ⇒ lỗi
    _put(f, d)
    lab = sorted((law_root / "vn-labour").glob("*.json"))[-1]
    ld = json.loads(lab.read_text(encoding="utf-8"))
    _put(lab, {**ld, "obligations": [{"code": "X", "label": "x", "cadence": "monthly", "due": {"months": 0, "day": 10}, "basis": "điều 1 (test)"}]})
    with pytest.raises(regulations.RegulationError, match="không module nào áp nghĩa vụ"):
        regulations.check_contracts()
    _put(lab, ld)
    shutil.copytree(law_root / "vn-labour", law_root / "vn-orphan")
    for x in (law_root / "vn-orphan").glob("*.json"):
        x.write_text(x.read_text(encoding="utf-8").replace('"vn-labour"', '"vn-orphan"'), encoding="utf-8")
    regulations.reload()
    regulations.check_contracts()   # khi nạp lõi chỉ kiểm bộ đã có người khai
    with pytest.raises(regulations.RegulationError, match="chưa có module nào khai"):
        regulations.check_contracts(strict=True)


# ── Ngày phát hành + hồi tố (0154) ──

def test_a_backdated_version_must_say_why(law_root: Path) -> None:
    _, d = _payroll_newest(law_root)
    base = {k: v for k, v in d.items() if k != "retroactive_reason"}
    ver = {"version": "2026-09-01", "effective_from": "2026-09-01"}
    out = law_root / "vn-payroll" / "2026-09-01.json"

    def attempt(**over: Any) -> None:
        _put(out, {**base, **ver, **over})
        regulations.packs()

    attempt(published_on="2026-08-15")                                   # phát hành TRƯỚC hiệu lực: bình thường
    attempt(published_on="2026-09-01")                                   # cùng ngày: không hồi tố
    for bad in ({"published_on": "2026-11-10"}, {"published_on": "2026-11-10", "retroactive_reason": "  "}):
        with pytest.raises(regulations.RegulationError, match="retroactive_reason"):
            attempt(**bad)
    attempt(published_on="2026-11-10", retroactive_reason="Nghị định ban hành muộn, áp từ đầu tháng 9 — kế toán trưởng chấp thuận 10/11/2026")
    assert regulations.version_on("vn-payroll", date(2026, 9, 30)).retroactive    # type: ignore[union-attr]
    for bad_date in ("10/11/2026", "2026-1-5"):
        with pytest.raises(regulations.RegulationError, match="published_on"):
            attempt(published_on=bad_date)
    gone = {k: v for k, v in base.items() if k != "published_on"}
    _put(out, {**gone, **ver})
    with pytest.raises(regulations.RegulationError, match="published_on"):
        regulations.packs()


# ── So sánh hai phiên bản (0154) ──

def test_compare_two_versions_lists_added_removed_and_changed(law_root: Path) -> None:
    f = sorted((law_root / "vn-tax").glob("*.json"))[-1]
    d = json.loads(f.read_text(encoding="utf-8"))
    new: dict[str, Any] = json.loads(json.dumps({**d, "version": "2027-01-01", "effective_from": "2027-01-01", "published_on": "2026-12-01"}))
    new.pop("retroactive_reason", None)
    for o in new["obligations"]:
        if o["code"] == "GTGT" and o["cadence"] == "quarterly":
            o["due"] = {"months": 1, "day": 25}
        if o["code"] == "BCTC":
            o["status"], o["verified_by"] = "verified", "Kế toán thử — 01/12/2026"
    new["obligations"] = [o for o in new["obligations"] if o["code"] != "QT-TNCN"]
    new["params"]["non_cash_min_vnd"]["value"] = 10_000_000
    new["params"]["new_threshold_vnd"] = {"label": "Ngưỡng mới", "value": 1, "basis": "Văn bản giả lập (test) điều 2"}
    new["calendar"] = {**new["calendar"], "shift": "none"}
    _write(law_root, new)
    regulations.reload()
    vs = regulations.packs()["vn-tax"]
    rows = {r["id"]: r for r in regulations.compare(vs[-2], vs[-1])}
    assert rows["param:non_cash_min_vnd"]["change"] == "changed" and "5000000" in rows["param:non_cash_min_vnd"]["old"] and "10000000" in rows["param:non_cash_min_vnd"]["new"]
    assert rows["param:new_threshold_vnd"]["change"] == "added" and rows["param:new_threshold_vnd"]["old"] == ""
    removed = next(r for r in rows.values() if r["kind"] == "obligation" and r["code"].startswith("QT-TNCN"))
    assert removed["change"] == "removed" and removed["new"] == "" and removed["basis"]
    gtgt = next(r for r in rows.values() if r["code"].startswith("GTGT") and r["change"] == "changed")
    assert "hạn ngày 25" in gtgt["new"] and gtgt["value_changed"]
    bctc = next(r for r in rows.values() if r["code"].startswith("BCTC"))
    assert bctc["status_change"] == "Giả định — chưa xác nhận → Đã xác nhận" and not bctc["value_changed"]
    assert rows["calendar:shift"]["change"] == "changed"
    assert "param:quarterly_vat_revenue_max_vnd" not in rows                           # không đổi ⇒ không có dòng
    assert regulations.compare(vs[-1], vs[-1]) == []


async def test_compare_versions_is_a_read_for_the_company_in_its_default_pack(law_root: Path) -> None:
    _, people = await _company("2026-07-01")
    for who in people.values():
        out = await q(who, "books.compare_versions")                                  # mặc định: bộ công ty đang theo, hai phiên bản mới nhất
        assert out["title"].endswith("2020-07-01 → 2025-07-01") and out["rows"]
        assert {"kind", "code", "change", "old", "new", "basis", "status_change"} <= set(out["rows"][0])
    out = await q(people["kt"], "books.compare_versions", pack="vn-payroll", from_version="2026-01-01", to_version="2026-07-01")
    assert any(r["code"] == "INSURANCE_BASE" for r in out["rows"])
    r = await executor.query(people["kt"], "books.compare_versions", {"pack": "vn-tax", "from_version": "2099-01-01"})
    assert r.status == "invalid"
    r = await executor.query(people["kt"], "books.compare_versions", {"pack": "vn-nothing"})
    assert r.status == "not_found"
    card = await q(people["kt"], "books.regulation")
    assert any(o["query"] == "books.compare_versions" for o in card["open"])


async def test_a_backdated_version_does_not_move_periods_already_opened(law_root: Path) -> None:
    """Sổ sách không tính lại kỳ đã mở: phiên bản HỒI TỐ đổi hạn GTGT chỉ ảnh hưởng kỳ mở SAU; nghĩa vụ đã sinh giữ hạn + phiên bản đã tính."""
    tid, _ = await _company("2026-01-01")
    await _open(tid, "2026-05-05")
    before = await _filings(tid, "GTGT")
    new: dict[str, Any] = {**_latest(law_root), "version": "2026-03-01", "effective_from": "2026-03-01", "published_on": "2026-05-06",
                           "retroactive_reason": "Giả lập (test): văn bản ban hành muộn, kế toán trưởng chấp thuận"}
    for o in new["obligations"]:
        if o["code"] == "GTGT":
            o["due"] = {"months": 1, "day": 10}
    _write(law_root, new)
    regulations.reload()
    assert (await executor.run(system_principal(tid), "books.sync_rules", {})).ok
    assert await _filings(tid, "GTGT") == before


def test_a_pack_ships_its_own_rule_set_and_no_set_has_two_owners(law_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """0155 đợt ba: bộ quy định của gói ngoài lõi nằm trong package của gói (``declare(…, root=)``) — nạp cùng các bộ của lõi, cùng luật kiểm; một tên bộ
    có ở cả lõi lẫn gói ⇒ lỗi nạp (mỗi bộ một chủ); hai gói khai hai thư mục cho một bộ ⇒ lỗi."""
    monkeypatch.setattr(regulations, "EXTRA", dict(regulations.EXTRA))
    monkeypatch.setattr(regulations, "CONTRACTS", dict(regulations.CONTRACTS))
    theirs = tmp_path / "pack" / "regulations"
    (theirs / "zz-pack").mkdir(parents=True)
    v = {**_latest(law_root), "pack": "zz-pack", "title": "Bộ của gói (test)", "obligations": [],
         "params": {"limit": {"label": "Ngưỡng", "value": 3, "basis": "Văn bản giả lập (test) điều 1 khoản 2"}}}
    _write(theirs, v)
    regulations.declare("zz-pack", required=("limit",), root=theirs)
    assert regulations.param("zz-pack", "limit", date.fromisoformat(v["effective_from"])) == 3 and "vn-tax" in regulations.packs()
    regulations.check_contract("zz-pack", regulations.packs()["zz-pack"])
    with pytest.raises(regulations.RegulationError, match="hai thư mục"):
        regulations.declare("zz-pack", required=("limit",), root=tmp_path / "elsewhere")
    (law_root / "zz-pack").mkdir()
    _write(law_root, v)
    regulations.reload()
    with pytest.raises(regulations.RegulationError, match="hai nơi"):
        regulations.packs()
    regulations.EXTRA.pop("zz-pack")
    regulations.reload()
