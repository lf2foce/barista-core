"""HỢP ĐỒNG QUYỀN HỆ THỐNG (0083) — mỗi ``ctx.call_system`` là một lần mở rộng quyền đọc / ghi trong công ty, phải có lý do khai ở thao tác đích.

Thao tác nội bộ được gọi bằng quyền hệ thống khai ``callers=`` (module nào được gọi) + ``why=`` (bên gọi đã kiểm gì, quyền mở tới đâu, trả những
trường nào) ngay tại decorator. Executor chặn lúc chạy (module gọi không có trong ``callers`` / đích chưa khai ⇒ Denied); test này đọc mã (AST) để
báo sớm từng chỗ gọi sai, kèm tệp + dòng.
"""

import ast
import pathlib
import re
import uuid

import pytest

import barista.main  # noqa: F401 — nạp mọi module để registry đủ
from barista.db import db
from barista.kernel import registry
from barista.kernel.errors import Denied, Invalid
from barista.kernel.executor import core_ctx
from tests.conftest import Who, owner_val
from tests.module_src import module_files, module_of, py_files

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "barista"
CALLS = ("call_system", "call_system_optional")

# Gọi theo HỌ thao tác module tự đăng ký (tên tính lúc chạy) — hợp đồng nằm ở hàm đăng ký họ đó trong registry. (module, hàm bao quanh) → lý do.
FAMILIES = {
    ("flow", "_subject"): "registry.SUBJECTS (m.subject): tiêu đề / trạng thái đối tượng gắn hồ sơ — người cập nhật hồ sơ đã thấy hồ sơ",
    ("flow", "_evaluate"): "m.check: mục kiểm của hồ sơ đọc đối tượng gắn hồ sơ — trả đạt / chưa + ghi chú",
    ("flow", "_done_when"): "m.check (0117): chạy thử bộ kiểm lúc GẮN vào việc để báo tham số sai ngay — người gắn đã thấy đối tượng; kết quả bỏ đi",
    ("flow", "_run_checker"): "m.check (0117): done_when của việc đọc đối tượng gắn việc — trả đạt / chưa; số đọc được chỉ hiện cho người thấy đối tượng",
    ("flow", "_goal"): "m.metric: MỘT con số toàn công ty; người gắn chỉ số phải có phạm vi all trên tài nguyên đó (_metric_on)",
    ("kpi", "_by"): "m.metric nhóm theo người / đội (0101): số cả công ty — KPI chỉ hiện dòng người xem thấy qua RLS của kpi.targets; tên người "
                    "khác trên xếp hạng theo chính sách kpi.policy",
    ("acct", "cashflow"): "<module>.cashflow_lines — mỗi thao tác khai callers=(\"acct\",); báo cáo dòng tiền min_scope=all",
    ("findings", "_one"): "<module>.findings_report — mỗi thao tác khai callers=(\"findings\",); Báo cáo chạy song song min_scope=all (0230)",
    ("health", "_collect"): "<module>.health_report — mỗi thao tác khai callers=(\"health\",); Sức khoẻ dữ liệu min_scope=all",
    ("connectors", "save"): "barista.api.connectors.save (0244): gói gọi connectors.save khai family=\"connector\" — đích kiểm owned_by theo module khai bộ "
                           "nối; gói phải consumes(\"connector\")",
    ("connectors", "connections"): "barista.api.connectors.connections (0244): connectors.of_kind khai family=\"connector\", owned_by theo module khai bộ nối",
    ("admin", "_install_items"): "Pack(records=…) (0153): lệnh cài mặc định là thao tác nội bộ khai callers=(\"admin\",) — vd hạng trợ lý AI (0193, "
                                   "chính sách của nhà vận hành); dữ liệu do gói khai, chỉ thêm phần thiếu; lệnh không nội bộ chạy bằng quyền người cài",
}


# Lõi KHÔNG nêu tên gói ngoài repo trong ``callers=`` (0155): dịch vụ lõi mở cho gói ngoài là thao tác khai ``family=`` (``registry.OPEN_FAMILIES``:
# outreach / doc_read / paper_draft / receivable / remittance / follow_up / identity / consent / booking / work_log / call_log), gói tự ``consumes`` họ đó. Gói ngoài ĐANG CÀI thì mã của nó được quét như mã trong repo (``_files``).


def _files() -> list[tuple[pathlib.Path, str | None]]:
    """Mã lõi + mã của gói ngoài repo ĐANG CÀI (``tests/module_src.py`` — 0191: file của gói thuộc đúng module của gói chứa nó)."""
    return [(f, module_of(f)) for f in py_files()]


def _sites() -> list[tuple[str, str, str | None, int]]:
    """(module gọi, hàm bao quanh, tên thao tác nếu là chuỗi cố định, dòng) của mọi lời gọi. Module gọi = module chứa file (package: thư mục —
    0088); file ngoài ``modules/`` (kernel, agent) tính theo tên file."""
    out: list[tuple[str, str, str | None, int]] = []
    for f, mod in _files():
        tree = ast.parse(f.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in CALLS and n.args:
                    a = n.args[0]
                    name = a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else None
                    out.append((mod or f.stem, fn.name, name, n.lineno))
    return list(dict.fromkeys(out))   # hàm lồng: một lời gọi gặp hai lần


def test_every_system_call_matches_a_declared_contract():
    bad: list[str] = []
    for mod, fn, name, line in _sites():
        where = f"{mod}:{line} ({fn})"
        if name is None:
            if (mod, fn) not in FAMILIES:
                bad.append(f"{where}: tên thao tác tính lúc chạy — dùng chuỗi cố định, hoặc khai họ thao tác trong FAMILIES kèm lý do")
            continue
        op = registry.get(name)
        if op is None or not op.internal:
            bad.append(f"{where}: {name} không phải thao tác nội bộ")
        elif not op.callers and op.family not in registry.OPEN_FAMILIES:
            bad.append(f"{where}: {name} chưa khai hợp đồng (callers= + why= ở decorator)")
        elif mod not in op.callers and not (op.family in registry.OPEN_FAMILIES and mod in registry.FAMILY_CALLERS[op.family]):
            bad.append(f"{where}: {mod} gọi {name} nhưng hợp đồng chỉ cho {op.callers or '—'} + module khai consumes({op.family!r})")
    assert not bad, "\n".join(bad)


def test_contracts_are_alive():
    sites = _sites()
    called = {name for _, _, name, _ in sites if name}
    by_name = {f for f, _, name, _ in sites if name is None}   # module gọi theo họ (tên tính lúc chạy)
    dead = sorted(op.name for op in registry.OPS.values() if op.callers and op.name not in called and by_name.isdisjoint(op.callers))
    assert not dead, f"hợp đồng quyền hệ thống không còn ai gọi — bỏ callers/why hoặc bỏ thao tác: {dead}"
    live_families = {(f, fn) for f, fn, name, _ in sites if name is None}
    assert set(FAMILIES) <= live_families, f"FAMILIES thừa: {sorted(set(FAMILIES) - live_families)}"


async def test_executor_refuses_callers_outside_the_contract(who: Who):
    """Hàng rào lúc chạy, không chỉ test đọc mã: module không có trong ``callers`` ⇒ Denied; module trong hợp đồng ⇒ chạy."""
    gd = await who("giamdoc")
    async with db.tenant_tx(gd, rollback=True) as conn:
        with pytest.raises(Denied, match="hợp đồng quyền hệ thống"):
            await core_ctx(conn, gd, module="mkt").call_system("flow.plan_tasks", plan_ids=[])
        with pytest.raises(Denied, match="hợp đồng quyền hệ thống"):
            await core_ctx(conn, gd).call_system("flow.plan_tasks", plan_ids=[])   # không rõ module đang chạy ⇒ không được
        assert await core_ctx(conn, gd, module="flow").call_system("flow.plan_tasks", plan_ids=[]) == []


async def test_family_operations_answer_only_modules_that_consume_the_family(who: Who):
    """Review 05/11: thao tác thuộc HỌ (chỉ số, đối tượng, bộ kiểm…) từng cho mọi lời gọi hệ thống qua, kể cả không rõ module ⇒ sale chỉ thấy
    lead của mình nhưng một ngữ cảnh không rõ module đếm được lead cả công ty. Giờ: chỉ module khai ``consumes`` họ đó (quy trình)."""
    gd = await who("giamdoc")
    since = "2026-01-01"
    async with db.tenant_tx(gd, rollback=True) as conn:
        for module in ("", "mkt", "crm"):
            with pytest.raises(Denied, match="họ thao tác"):
                await core_ctx(conn, gd, module=module).call_system("crm.metric_leads", since=since)
        got = await core_ctx(conn, gd, module="flow").call_system("crm.metric_leads", since=since)
        assert isinstance(got["value"], int)
    assert registry.FAMILY_CALLERS["metric"] == {"flow", "kpi"}


async def test_contracts_only_touch_the_callers_own_sources_and_work_kinds(who: Who, monkeypatch: pytest.MonkeyPatch):
    """Hợp đồng nhận dữ liệu thuộc về bên gọi (nguồn khoản phải thu, loại việc theo dõi) — module gọi chỉ đụng phần của chính nó. Module gọi là một
    module khai consumes("receivable", "follow_up") (gói ngành; ở đây giả bằng một module của lõi — bản dùng gói ngành thật: test của gói)."""
    monkeypatch.setattr(registry, "FAMILY_CALLERS", {**registry.FAMILY_CALLERS, "receivable": {*registry.FAMILY_CALLERS.get("receivable", ()), "mkt"},
                                                     "follow_up": {*registry.FAMILY_CALLERS.get("follow_up", ()), "mkt"}})
    gd = await who("giamdoc")
    async with db.tenant_tx(gd, rollback=True) as conn:
        ctx = core_ctx(conn, gd, module="mkt")
        with pytest.raises(Denied, match="thuộc module crm"):
            await ctx.call_system("acct.issue_receivable", source="crm.rent", source_ref="x", label="Thử", party="Thử",
                                  nature="revenue", amount_vnd=1_000, due_on="2026-12-01")
        with pytest.raises(Denied, match="thuộc module crm"):
            await ctx.call_system("acct.void_receivables", source="crm.rent", ref_prefix="x", due_after="2026-01-01")
        with pytest.raises(Denied, match="thuộc module acct"):
            await ctx.call_system("work.wake", kind="collection", subject_ref="x", text="thử")
        out = await ctx.call_system("acct.issue_receivable", source="mkt.rent", source_ref="x", label="Thử", party="Thử",
                                    nature="revenue", amount_vnd=1_000, due_on="2026-12-01")   # nguồn của chính module gọi ⇒ qua cửa
        assert out


async def test_extracted_values_need_the_caller_to_read_the_document(who: Who, monkeypatch: pytest.MonkeyPatch):
    """kb.extracted dùng lại lần trích của người khác — nhưng người đang gọi phải đọc được tài liệu bằng quyền CỦA HỌ (kệ / nguồn ngoài). Module gọi
    là một module khai consumes("doc_read") (gói ngoài lõi; ở đây giả bằng một module của lõi)."""
    monkeypatch.setattr(registry, "FAMILY_CALLERS", {**registry.FAMILY_CALLERS, "doc_read": {*registry.FAMILY_CALLERS["doc_read"], "mkt"}})
    sale, ketoan = await who("sale1"), await who("ketoan")
    doc = await owner_val("SELECT id FROM kb.documents WHERE tenant_id = %s AND shelf = 'finance' LIMIT 1", (ketoan.tenant_id,))
    assert doc is not None
    async with db.tenant_tx(sale, rollback=True) as conn:
        with pytest.raises(Denied, match="không đọc được tài liệu"):
            await core_ctx(conn, sale, module="mkt").call_system("kb.extracted", document_id=str(doc), doc_type="x.statement")
    async with db.tenant_tx(ketoan, rollback=True) as conn:   # đọc được kệ Kế toán ⇒ qua cửa (chưa trích loại này ⇒ None)
        assert await core_ctx(conn, ketoan, module="mkt").call_system("kb.extracted", document_id=str(doc), doc_type="x.statement") is None


SETTLEMENT = {"acct.recognize_payable", "acct.recognize_recovery", "acct.release_recovery", "acct.prepare_payout", "acct.void_payables", "acct.position",
              "acct.owner_positions", "acct.my_settlements", "acct.payable_states", "acct.payout_states", "acct.payout_payables", "acct.payouts_open",
              "acct.source_settlement_blockers"}
PLACEMENT = {"acct.place", "acct.place_refund", "acct.place_reverse", "acct.place_balances"}


def test_core_services_open_to_outside_packs_are_families_not_names():
    """Lõi không nêu tên gói ngoài (0155; thước đo ``test_dummy_pack``): dịch vụ lõi mở cho gói ngoài khai ``family=``, KHÔNG nằm trong ``callers=``
    — ``callers=`` chỉ có module của repo này."""
    open_ops = {op.name for op in registry.OPS.values() if op.family in registry.OPEN_FAMILIES}
    assert {"asks.open", "asks.close_subject", "asks.notify_owner", "kb.extracted", "kb.file_digest", "kb.mentions", "papers.save_draft",
            "acct.issue_receivable", "acct.void_receivables", "acct.receivables_for", "acct.balances_for", "acct.request_remittance",
            "work.open", "work.wake",
            # 1.14 (hai gói nữa rời lõi): định danh, đồng ý, đặt lịch, sổ công, nhật ký cuộc gọi
            "parties.ensure_person", "parties.add_dependant_for", "parties.call_consent", "parties.consent_summary", "parties.consent_summaries",
            "parties.record_contact_consent", "schedule.book", "schedule.release", "schedule.resources_for", "pay.record_work",
            "crm.leads_for_phones", "crm.record_call", "crm.offering_by_code",
            # 1.16 (0186 — lõi thôi nêu tên module của gói ngành): nghĩa vụ & chi, tiền đặt tại đối tác, tra cứu sổ, cơ cấu, lead thành thương vụ, số gộp
            *SETTLEMENT, *PLACEMENT, "acct.category", "acct.period_open", "hr.structure_on", "parties.referrers_on", "crm.link_deal", "crm.reopen_lead",
            "acct.collections", "crm.funnel", "crm.source_of_leads", "kpi.company_targets", "acct.release_receipts", "acct.receipt_facts",
            "parties.ensure_member", "parties.ensure_counterparty", "parties.names", "parties.resolve_for_payment", "crm.customer_party",
            "flow.module_task", "flow.subject_status", "admin.members_with_grant", "papers.company"} <= open_ops
    assert {op.name for op in registry.OPS.values() if op.family == "settlement"} == SETTLEMENT   # họ ghi tiền: thêm thao tác là quyết định có chủ đích
    assert {op.name for op in registry.OPS.values() if op.family == "placement"} == PLACEMENT
    assert set(registry.OPEN_FAMILIES) <= set(registry.FAMILIES)
    in_repo = set(module_files(SRC / "modules"))   # CHỈ module của repo lõi (module_files() không tham số tính cả gói ngoài đang cài)
    for op in registry.OPS.values():
        if op.module not in in_repo:   # thao tác của gói ngoài đang cài: hợp đồng nội bộ của chính gói nêu module của nó là đúng — test của gói kiểm
            continue
        assert set(op.callers) <= in_repo | {"core"}, f"{op.name}: callers= nêu module ngoài repo {sorted(set(op.callers) - in_repo)} — dùng family="


async def test_open_family_serves_only_modules_that_consume_it(who: Who, monkeypatch: pytest.MonkeyPatch):
    """Module chưa khai ``consumes("outreach")`` ⇒ Denied; khai rồi ⇒ qua cửa hợp đồng (module khai ``callers=`` vẫn qua như cũ)."""
    gd = await who("giamdoc")
    monkeypatch.setattr(registry, "FAMILY_CALLERS", {**registry.FAMILY_CALLERS, "outreach": set()})
    async with db.tenant_tx(gd, rollback=True) as conn:
        with pytest.raises(Denied, match="họ thao tác"):
            await core_ctx(conn, gd, module="mkt").call_system("asks.close_subject", subject_kind="flow.case", subject_id=str(uuid.uuid4()))
    registry.FAMILY_CALLERS["outreach"].add("mkt")
    async with db.tenant_tx(gd, rollback=True) as conn:
        got = await core_ctx(conn, gd, module="mkt").call_system("asks.close_subject", subject_kind="flow.case", subject_id=str(uuid.uuid4()))
        assert got == {"closed": 0}


# ── 0186: lõi không nêu tên module của gói NGÀNH (bước một của việc đưa gói ngành ra repo gói) ──

def _industry_modules() -> set[str]:
    return {m for m, pk in registry.MODULE_PACK.items() if registry.pack_kind(pk) == "industry"}


def test_core_contracts_do_not_name_modules_of_industry_packs():
    """Gói nền / gói năng lực không nêu module của gói ngành: không trong ``callers=`` (gói ngành khai ``consumes`` họ), không trong ``why=``."""
    industry = _industry_modules()
    named = re.compile(r"(?<![\w.])(?:" + "|".join(sorted(industry) or ["-"]) + r")\b")
    bad = [f"{op.name}: callers= nêu {sorted(set(op.callers) & industry)}" for op in registry.OPS.values()
           if op.module not in industry and set(op.callers) & industry]
    fields = re.compile(r"\{[^}]*\}")   # danh sách trường trả về ({leads, deals…}) là tên trường, không phải tên module
    bad += [f"{op.name}: why= nêu {sorted(set(named.findall(fields.sub('', op.why))))}" for op in registry.OPS.values()
            if op.module not in industry and op.internal and named.search(fields.sub("", op.why))]
    assert not bad, "\n".join(bad)


def test_core_source_does_not_name_operations_or_resources_of_industry_packs():
    """Mã của lõi (kernel, trợ lý, module gói nền / năng lực — cả chú thích, ví dụ trên form) không nhắc ``<module ngành>.<tên đã đăng ký>`` hay khoá gói
    ngành — kể cả dữ liệu mẫu của lõi (``seed*.py``: công ty mẫu trung tính, 0187). Phần mẫu của gói ngành còn trong repo (``samples/<gói>/``) là mã của
    gói, dời cùng gói ở bước ba."""
    industry = _industry_modules()
    if not industry:   # không gói ngành nào đang nạp (chạy lõi-không-gói, chưa cài gói ngoài) ⇒ không có tên nào để tìm
        return
    packs = {k for k, pk in registry.PACKS.items() if pk.kind == "industry"}
    known = set(registry.OPS) | set(registry.RESOURCES) | set(registry.SUBJECTS) | set(registry.CHECKERS) | set(registry.TRIGGERS)
    token = re.compile(r"(?<![\w.])((?:" + "|".join(sorted(industry) or ["-"]) + r")\.[a-z_]+)")
    quoted = re.compile(r"[\"'`](" + "|".join(sorted(packs) or ["-"]) + r")[\"'`]")   # khoá module đứng riêng (deals, exec, catalog) còn là từ thường
    bad: list[str] = []
    for f, mod in _files():
        if mod in industry or SRC / "samples" in f.parents or SRC not in f.parents:
            continue
        for i, line in enumerate(f.read_text().splitlines(), 1):
            hits = [t for t in token.findall(line) if t in known] + quoted.findall(line)
            if hits:
                bad.append(f"{f.relative_to(SRC)}:{i}: {sorted(set(hits))}")
    assert not bad, "\n".join(bad)


def _consumer(monkeypatch: pytest.MonkeyPatch, *families: str, module: str = "mkt") -> str:
    """Một module của lõi đóng vai GÓI NGÀNH khai ``consumes(<họ>)`` — lõi không có gói ngành nào để thử; bản dùng gói ngành thật nằm ở test của gói."""
    monkeypatch.setattr(registry, "FAMILY_CALLERS", {**registry.FAMILY_CALLERS, **{f: {*registry.FAMILY_CALLERS.get(f, ()), module} for f in families}})
    return module


async def test_settlement_family_is_scoped_to_the_callers_own_source_and_basic_form(who: Who, monkeypatch: pytest.MonkeyPatch):
    """Họ ``settlement`` mở cho gói ngoài lõi (0186): gói chỉ đụng nghĩa vụ / lệnh chi của nguồn mang tên nó, và ghi nghĩa vụ ở dạng cơ bản — tài khoản nợ
    gộp, khấu trừ vào tài khoản khác tax:withheld, cấn ứng của nguồn khác, đánh dấu mật là phần của module lõi có tên trong ``callers=``."""
    pack = _consumer(monkeypatch, "settlement")
    gd = await who("giamdoc")
    base = {"source_ref": "x", "party_id": str(uuid.uuid4()), "amount_vnd": 1_000_000, "expense_account": "expense:marketing", "label": "Thử", "basis": "x"}
    async with db.tenant_tx(gd, rollback=True) as conn:
        ctx = core_ctx(conn, gd, module=pack)
        for op, inp in (("acct.recognize_payable", {**base, "source": "pay.slip"}),
                        ("acct.recognize_recovery", {**base, "source": "pay.slip"}),
                        ("acct.prepare_payout", {"source": "pay.slip", "source_ref": "x", "party_id": base["party_id"], "amount_vnd": 1, "label": "Thử"}),
                        ("acct.void_payables", {"source": "pay.slip", "refs": ["x"], "action_key": "k", "reason": "thử"}),
                        ("acct.payouts_open", {"source": "pay.slip"}),
                        ("acct.payout_states", {"source": "pay.slip", "refs": ["x"]})):
            with pytest.raises(Denied, match="thuộc module pay"):
                await ctx.call_system(op, **inp)
        for extra in ({"liability_account": "payable:payroll"}, {"confidential": "pay.slips"}, {"allow_offset_from": ["pay"], "offset_vnd": 1},
                      {"expenses": [{"account": "expense:marketing", "amount_vnd": 1_000_000}]},
                      {"deductions": [{"account": "payable:payroll", "amount_vnd": 1, "basis": "thử"}]}):
            with pytest.raises(Denied, match="dạng cơ bản"):
                await ctx.call_system("acct.recognize_payable", **base, source=f"{pack}.payee", **extra)
        with pytest.raises(Denied, match="duyệt"):   # lập chi chỉ sau phiếu do người khác duyệt — gói không tự lập chi
            await ctx.call_system("acct.prepare_payout", source=f"{pack}.request", source_ref="x", party_id=base["party_id"], amount_vnd=1, label="Thử")
        assert set(await ctx.call_system("acct.payouts_open", source=f"{pack}.request")) == {"n", "amount_vnd"}   # nguồn của mình: đọc được
        for module in ("", "crm", "kpi"):   # module không khai consumes("settlement") không qua
            with pytest.raises(Denied, match="họ thao tác"):
                await core_ctx(conn, gd, module=module).call_system("acct.position", user_id=str(gd.user_id))


async def test_newly_opened_reads_stay_inside_the_callers_own_objects(who: Who, monkeypatch: pytest.MonkeyPatch):
    """0186: thao tác đọc vừa mở theo họ tự giới hạn — hồ sơ của loại đối tượng module gọi khai; công nợ gộp phải nêu một tiền tố nguồn; khoá tiền đặt
    không rơi vào vùng tên tài khoản của module khác."""
    from barista.modules.acct.placements import _accounts

    pack = _consumer(monkeypatch, "follow_up", "company_figures")
    gd = await who("giamdoc")
    other = next(k for k in registry.SUBJECTS if k.split(".")[0] not in (pack, "flow"))
    async with db.tenant_tx(gd, rollback=True) as conn:
        with pytest.raises(Denied, match="thuộc module"):
            await core_ctx(conn, gd, module=pack).call_system("flow.subject_status", subject_kind=other, subject_id=str(uuid.uuid4()))
        with pytest.raises(Invalid, match="tiền tố nguồn"):
            await core_ctx(conn, gd, module=pack).call_system("acct.collections", since="2026-01-01", until="2026-02-01")
    assert _accounts(f"{pack}.move", "x-1") == (f"placed:{pack}-x-1", f"placed_interest:{pack}-x-1")
    monkeypatch.setitem(registry.MODULES, f"{pack}_x", "Thử")
    with pytest.raises(Invalid, match="vùng tên"):
        _accounts(f"{pack}.move", "x-1")
    assert _accounts(f"{pack}_x.move", "1")[0] == f"placed:{pack}-x-1"   # chủ thật của vùng tên vẫn ghi được
