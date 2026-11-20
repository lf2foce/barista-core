"""Gói theo hai trục (quyết định 0056): năng lực dùng chung vs nghiệp vụ ngành, cộng hồ sơ cấu hình từng công ty. Tiêu chí nghiệm thu
(review 01/11): (1) công ty chỉ bật Nền không nhận menu, lời dặn, công cụ, việc nền BĐS; (2) công ty không phải BĐS dùng được Tài chính
dùng chung; (3) hai công ty BĐS cùng code nhưng cấu hình khác; đổi cấu hình không sửa code; (4) luồng ký → thu phí → hoa hồng vẫn đúng
(test_money / test_work / test_stage_deadline) và tắt gói Môi giới thì phản ứng ngành dừng."""

import re
import uuid
from pathlib import Path
from typing import Any

import pytest

from barista import seed
from barista.agent import chat, desk
from barista.kernel import executor, registry
from barista.workspaces import manifest
from tests.conftest import ok, q
from tests.module_src import module_files, rel

PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("ketoan", "Kế toán", "accountant", "KT", True), ("giamdoc", "Giám đốc", "director", "QT", False),
          ("sale1", "Nhân viên", "sale", "KD-A", False)]
INDUSTRY = {k for k, p in registry.PACKS.items() if p.kind == "industry"}
INDUSTRY_MODULES = {m for m, pk in registry.MODULE_PACK.items() if pk in INDUSTRY}
SRC = Path(__file__).resolve().parents[1] / "src" / "barista"


async def _tenant(packs: tuple[str, ...], about: str) -> tuple[Any, str]:
    domain = f"t{uuid.uuid4().hex[:6]}.demo"
    tid = await seed.create_tenant(f"Công ty thử {domain}", domain, people=PEOPLE, packs=packs, about=about)
    if "commission" in packs:   # như công ty thật: có chính sách hoa hồng — không thì phản ứng tính hoa hồng lỗi và (đúng) chặn tắt gói
        await ok(await seed.who(tid, "admin", domain), "commission.set_plan", name="Thử", effective_from="2026-01-01", tiers="0:2")
    return tid, domain


@pytest.fixture(scope="module")
async def base_only() -> tuple[Any, str]:
    return await _tenant((), "")


async def test_base_only_company_gets_no_industry_menu_prompt_or_tools(base_only: tuple[Any, str]) -> None:
    tid, domain = base_only
    admin = await seed.who(tid, "admin", domain)
    spaces = {w["key"] for w in manifest(admin, "x")["workspaces"]}
    screens = {s["query"].split(".")[0] for w in manifest(admin, "x")["workspaces"] for s in w["screens"]}
    assert not spaces & {"sales", "accounting", "marketing"} and not screens & INDUSTRY_MODULES
    tools = {op.name.split(".")[0] for op, _ in await chat.catalog(admin)}
    assert not tools & (INDUSTRY_MODULES | {"acct", "commission"})
    # việc theo dõi là bộ máy NỀN (0076): có, nhưng không loại việc ngành nào (thu phí CĐT, khách đóng tiền, khách trả tiền) giao được
    assert [o["value"] for o in await q(admin, "work.kind_options")] == ["case"]   # loại việc của gói NỀN (0142), không loại nào của ngành
    text = await chat.instructions(admin)
    for word in ("bất động sản", "crm__", "acct__", "ĐỐI SOÁT", "căn"):
        assert word not in text, word
    assert "Phân hệ công ty đang dùng: chỉ phần nền" in text
    for s in chat.showcase(admin, limit=10) + chat.suggestions(admin, limit=10):
        assert "căn" not in s and "CĐT" not in s, s

# 0079: không còn ngoại lệ ngành trong năng lực chung. Thêm ngoại lệ phải có lý do + ADR/ROADMAP.
# (commission từng ở đây — nó là gói NGÀNH gắn Bất động sản kể từ 11/11/2026 nên không còn là nợ của năng lực chung; ROADMAP N12 / N13.)
CAPABILITY_DEBT: set[str] = set()


def test_core_and_capability_modules_carry_no_industry_business() -> None:
    """0076: module của gói nền / năng lực chỉ là CƠ CHẾ — không lệnh nào cần gói ngành (``needs=``), không bên nghe nào phản ứng với sự kiện của gói
    ngành. Phần ngành (loại giấy, loại việc theo dõi, phản ứng khi ký HĐMB…) nằm ở gói ngành, khai qua điểm cắm (save_draft, work_kind, sự kiện)."""
    def cap(mod: str) -> bool:
        return mod in registry.MODULE_PACK and registry.pack_kind(registry.MODULE_PACK[mod]) in ("core", "capability") and mod not in CAPABILITY_DEBT

    bad = [f"{op.name} needs {op.needs}" for op in registry.OPS.values() if cap(op.name.split(".")[0]) and set(op.needs) & INDUSTRY]
    bad += [f"{s.name} nghe {s.event_type}" for s in registry.SUBSCRIBERS.values() if cap(s.name.split(".")[0])
            and (set(s.needs) & INDUSTRY or registry.MODULE_PACK.get(s.event_type.split(".")[0]) in INDUSTRY)]
    # phụ thuộc NGẦM (không khai needs=): gọi thẳng thao tác / nghe sự kiện của module gói ngành bằng tên — review 04/11 (health từng đếm
    # "giao dịch ký chưa có hoá đơn" bằng cách gọi crm / deals / catalog). Gợi ý ô nhập (options_query) không tính: hỏng thì ô trống, không sai số.
    industry_modules = {m for m, pk in registry.MODULE_PACK.items() if pk in INDUSTRY}
    call = re.compile(r"""(?:call\w*|run|query|invoke|on)\(\s*["']([a-z_]+)\.[a-z_]+["']""")
    files = module_files()
    for mod in registry.MODULE_PACK:
        for src in files.get(mod, []) if cap(mod) else []:
            bad += [f"{rel(src)}:{n}: {line.strip()[:120]}" for n, line in enumerate(src.read_text().splitlines(), 1)
                    for m in call.finditer(line) if m.group(1) in industry_modules]
    assert not bad, bad


# Từ ngữ của ngành cụ thể (BĐS, môi giới, L/C…) — lọt vào chữ người dùng / trợ lý thấy ở module chung là công ty ngành khác thấy (0081).
# "hoa hồng" KHÔNG ở đây: chữ chung của nhiều ngành (gói Hoa hồng là gói ngành gắn BĐS nhưng module chung vẫn có thể nhắc hoa hồng).
INDUSTRY_WORDS = re.compile(r"môi giới|CĐT|chủ đầu tư|căn hộ|mã căn|\bcăn \d|bất động sản|\bBĐS\b|Hồ Sen|\bL/C\b|thư tín dụng|HĐMB"
                            r"|nhà mẫu|xem nhà|gợi ý căn|giữ căn|bên sàn", re.I)


# Từ ngữ của ngành đào tạo (0116): gói Đào tạo (repo gói) là ngành, nên năng lực chung (định danh, lịch…) nói "người được giám hộ", "người trả", không nói
# "học sinh", "phụ huynh". "Người học" KHÔNG ở đây: gói Đào tạo nội bộ (`training`) dùng đúng chữ đó cho nhân viên học.
EDUCATION_WORDS = re.compile(r"học sinh|học viên|giáo viên|giáo vụ|lớp học|học phí|học thử|dạy thêm|sổ buổi|buổi học|phụ huynh|cơ sở dạy|tuyển sinh",
                             re.I)


# Từ của MỘT gói cụ thể (review độc lập Claude Fable 5.1, 08/11): gói nền và gói năng lực khác không nói bằng chữ của gói đó — vd vai Giám đốc
# của gói nền mô tả "giao dịch, chiến dịch, hàng chờ chia lead" thì công ty không bật Khách hàng / Marketing cũng đọc thấy. Từ ⇒ gói được nói
# (gói đó hoặc gói kéo theo nó): "giao dịch" — chặng Giao dịch của Khách hàng, giao dịch ngân hàng của Tài chính, mã giao dịch điện tử nộp tờ
# khai của Sổ sách; "chia lead" — Khách hàng; "chiến dịch" — Marketing, và Khách hàng (nguồn lead theo mã chiến dịch, điểm cắm crm.campaign).
OWNED_WORDS: dict[str, tuple[str, ...]] = {"giao dịch": ("crm", "finance", "books"), "chia lead": ("crm",), "chiến dịch": ("marketing", "crm")}


def _shared_strings() -> list[tuple[str, str, Path, int, str]]:
    """(module, gói, file, dòng, chuỗi) — mọi chuỗi hằng trong module của gói nền / năng lực, kể cả module ``core`` (``kernel/core_ops.py``: vai,
    quy tắc trợ lý của gói nền); bỏ chú thích đầu file (được nêu ví dụ ngành)."""
    import ast

    files = module_files()
    files["core"] = [SRC / "kernel" / "core_ops.py"]
    out = []
    for mod, pk in registry.MODULE_PACK.items():
        if registry.pack_kind(pk) not in ("core", "capability") or mod in CAPABILITY_DEBT:
            continue
        for src in files.get(mod, []):   # mọi file của module (package: cả file con — 0088)
            tree = ast.parse(src.read_text())
            skip = {id(tree.body[0].value)} if tree.body and isinstance(tree.body[0], ast.Expr) else set()
            out += [(mod, pk, src, n.lineno, n.value) for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip]
    return out


def test_shared_packs_do_not_speak_another_packs_words() -> None:
    bad = []
    for _mod, pk, src, line, v in _shared_strings():
        have = registry.pack_closure({pk})
        bad += [f"{rel(src)}:{line}: “{w}” (của {'/'.join(owners)}) — {v.strip()[:80]}"
                for w, owners in OWNED_WORDS.items() if not set(owners) & have and re.search(w, v, re.I)]
    assert not bad, "\n".join(bad)


def test_core_and_capability_modules_speak_no_industry_words() -> None:
    """0081: mọi CHUỖI trong module của gói nền / năng lực (nhãn ô, ví dụ, cột, ghi chú, tên màn, mô tả lệnh mà trợ lý đọc) không chứa từ của
    một ngành — chỉ chú thích đầu file được nêu ví dụ ngành. Tên tài nguyên quyền của gói nền / năng lực cũng vậy (màn phân quyền)."""
    bad = [f"{rel(src)}:{line}: {v.strip()[:80]}" for _mod, _pk, src, line, v in _shared_strings()
           if INDUSTRY_WORDS.search(v) or EDUCATION_WORDS.search(v)]
    bad += [f"tài nguyên {k}: {v}" for k, v in registry.RESOURCES.items()
            if registry.pack_kind(registry.MODULE_PACK.get(registry.RESOURCE_MODULE.get(k, ""), "base")) in ("core", "capability")
            and registry.RESOURCE_MODULE.get(k, "") not in CAPABILITY_DEBT
            and (INDUSTRY_WORDS.search(v) or EDUCATION_WORDS.search(v))]
    assert not bad, bad


def test_customer_capability_does_not_say_project() -> None:
    """0110: "dự án" là chữ của BĐS; trong Khách hàng và chia lead, khái niệm là "sản phẩm quan tâm"."""
    import ast

    bad = []
    files = module_files()
    for mod in ("crm", "leadflow"):
        for src in files.get(mod, []):
            tree = ast.parse(src.read_text())
            skip = {id(tree.body[0].value)} if tree.body and isinstance(tree.body[0], ast.Expr) else set()
            bad += [f"{rel(src)}:{n.lineno}: {n.value.strip()[:80]}" for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip and re.search(r"dự án", n.value, re.I)]
    assert not bad, bad


async def test_base_only_company_opens_every_screen_and_attention_without_errors(base_only: tuple[Any, str]) -> None:
    """Nghiệm thu thật (review 04/11): công ty chỉ bật gói nền — mọi màn của mọi vai mở được (không lỗi, không 'gói chưa bật'), mục cần chú ý
    chạy được. Quét chuỗi trong code không đủ: phụ thuộc ngầm (SQL, sự kiện, loại đối tượng) chỉ lộ khi chạy."""
    tid, domain = base_only
    for who in ("admin", "giamdoc", "ketoan", "sale1"):
        p = await seed.who(tid, who, domain)
        for w in manifest(p, "x")["workspaces"]:
            for sc in w["screens"]:
                r = await executor.query(p, sc["query"], {})
                assert r.ok, (who, sc["query"], r.status, r.error)
        await desk.attention(p)
