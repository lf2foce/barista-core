"""Mỗi bất biến của đường tiền có MỘT nơi giữ (``docs/MONEY-INVARIANTS.md``). Test cấu trúc chặn tái phạm: module không tự so dòng bút toán, không
chạm bảng sổ cái, không tự dựng bút toán đảo — và trang bất biến không mục: mọi nơi giữ / test nó nêu phải có thật."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "backend" / "src" / "barista"
TESTS = ROOT / "backend" / "tests"
PAGE = ROOT / "docs" / "MONEY-INVARIANTS.md"
KERNEL_LEDGER = SRC / "kernel" / "ledger.py"

# chỗ chỉ sổ cái được làm — nằm ngoài ``kernel/ledger.py`` là đang giữ bất biến lần thứ hai
EVERYWHERE = {
    "ghi bảng sổ cái ngoài cửa ghi": re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+ledger\.(journals|postings)\b", re.I),
}
IN_MODULES = {   # module nghiệp vụ: không đọc thẳng bảng sổ cái, không tự so / tự dựng dòng bút toán
    "chạm bảng sổ cái bằng SQL": re.compile(r"\bledger\.(journals|postings)\b"),
    "tự so dòng bút toán": re.compile(r"""\[["']lines["']\]\s*[!=]=|[!=]=\s*\w+\[["']lines["']\]|sorted\(\s*\w+\[["']lines["']\]"""),
    "tự dựng bút toán đảo": re.compile(r"""\(c,\s*-a\)\s+for\s+c,\s*a\s+in\s+\w+\[["']lines["']\]"""),
}


def _hits(paths: list[Path], rules: dict[str, re.Pattern[str]]) -> list[str]:
    return [f"{p.relative_to(SRC)}:{n}: {why}" for p in paths for n, line in enumerate(p.read_text().splitlines(), 1)
            for why, rx in rules.items() if rx.search(line)]


def test_only_the_ledger_decides_replay_reversal_and_touches_ledger_tables() -> None:
    py = [p for p in SRC.rglob("*.py") if p != KERNEL_LEDGER]
    bad = _hits(py, EVERYWHERE) + _hits([p for p in py if "modules" in p.relative_to(SRC).parts], IN_MODULES)
    assert not bad, "Bất biến tiền giữ ở sổ cái (docs/MONEY-INVARIANTS.md) — gọi ledger.post / reverse / replayed, không viết bản so riêng:\n" + "\n".join(bad)


def test_the_invariant_page_names_real_owners_and_real_tests() -> None:
    """Mỗi tên test trên trang (``file::test`` hoặc ``::test`` tiếp theo cùng file) phải có thật; nơi giữ nêu trên trang phải có trong mã."""
    text = PAGE.read_text()
    named, current = [], ""
    for f, fn in re.findall(r"`(test_\w+)?::(test_\w+)`", text):
        current = f or current
        named.append((current, fn))
    assert named, "trang bất biến phải nêu test"
    missing = [f"{f}::{fn}" for f, fn in named if not re.search(rf"^(async )?def {fn}\(", (TESTS / f"{f}.py").read_text(), re.M)]
    assert not missing, f"trang bất biến nêu test không có: {missing}"
    code = "\n".join(p.read_text() for p in SRC.rglob("*.py")) + "\n".join(p.read_text() for p in SRC.rglob("*.sql"))
    acct = SRC / "modules" / "acct"
    # nơi giữ ở mã Python: tên trên trang trỏ đúng FILE giữ (``acct.<file>.<hàm>``) — chuyển hàm sang file khác thì trang phải đổi theo
    for owner, where, needle in (("ledger.write_journal", None, "FUNCTION ledger.write_journal("), ("ux_journals_reverse_once", None, "INDEX ux_journals_reverse_once"),
                                 ("ledger.replayed", KERNEL_LEDGER, "def replayed("),
                                 ("acct.receivables._replayed_receivable", acct / "receivables.py", "def _replayed_receivable("),
                                 ("acct.cash_in.fee_earned", acct / "cash_in.py", "def fee_earned("),
                                 ("acct.cash_in._cash_line", acct / "cash_in.py", "async def _cash_line("),
                                 ("acct.cash_in._allocate", acct / "cash_in.py", "async def _allocate("),
                                 ("ledger_read._snapshot", acct / "ledger_read.py", "def _snapshot(")):
        assert owner in text and needle in (where.read_text() if where else code), owner
