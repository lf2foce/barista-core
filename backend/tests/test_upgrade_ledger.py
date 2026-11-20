"""Sổ "file mới sau bố cục cũ" của diễn tập nâng cấp (``tests/test_upgrade.py``) phải khớp đúng các migration của lõi — chặn ngay ở bộ test LÕI.

Diễn tập nâng cấp của gói ngành (vd Bất động sản) dựng trọn bố cục migration cũ rồi khẳng định phần còn phải chạy ĐÚNG BẰNG ``NEW_FILES`` (và phần cần
dừng dịch vụ đúng bằng ``NEW_MAINTENANCE``). Thêm migration lõi mà quên ghi vào sổ thì chỉ test của gói đỏ (đã xảy ra hai lần ngày 12/11/2026) — test
này bắt cùng lỗi trong bộ test lõi, không cần DB, và nói đúng file phải thêm. Không DB: chỉ đọc cây migration + ``provenance.json`` / ``deployment.json``.
"""

import pytest

from barista.kernel.migrate import BASELINE, chains, policy, provenance
from tests.test_upgrade import COMMENT_ONLY, NEW_FILES, NEW_MAINTENANCE, OLD_LAST, _restrict_to_core

WHERE = "backend/tests/test_upgrade.py"


def _new_after_old_layout() -> tuple[set[str], set[str]]:
    """(file phải chạy sau bố cục cũ theo quy tắc của ``old_layout``, mọi file của lõi) — file dời / tách (``provenance.json``) đã có trong bố cục cũ."""
    chains_ = chains()
    moved = provenance(chains_).moved
    every, new = set(), set()
    for pack, found in chains_:
        for name in found:
            key = f"{pack}/{name}"
            every.add(key)
            if ((pack, name) in moved and (pack, name) not in COMMENT_ONLY) or name.startswith(f"{BASELINE}/"):   # đổi tên chỉ sửa chú thích = file mới của diễn tập (như test_upgrade.old_layout)
                continue
            if key in NEW_FILES or name.split("_", 1)[0] > OLD_LAST:
                new.add(key)
    return new, every


def test_every_core_migration_after_the_old_layout_is_listed_in_new_files(monkeypatch: pytest.MonkeyPatch) -> None:
    _restrict_to_core(monkeypatch)
    new, every = _new_after_old_layout()
    if missing := sorted(new - NEW_FILES):
        pytest.fail("Migration lõi mới chưa có trong sổ diễn tập nâng cấp — thêm vào NEW_FILES ở " + WHERE + " (kèm một dòng chú thích vì sao có / "
                    "không cần dừng dịch vụ): " + ", ".join(f'"{k}"' for k in missing))
    if stale := sorted(NEW_FILES - every):
        pytest.fail(f"NEW_FILES ở {WHERE} nhắc file không có trong cây migration của lõi (đổi tên / bỏ / thuộc gói ngoài?) — sửa hoặc xoá: "
                    + ", ".join(f'"{k}"' for k in stale))
    if moved := sorted(NEW_FILES - new):
        pytest.fail(f"NEW_FILES ở {WHERE} nhắc file đã có trong bố cục cũ (bản gốc / file dời theo provenance.json) — xoá khỏi NEW_FILES: "
                    + ", ".join(f'"{k}"' for k in moved))


def test_new_files_that_need_downtime_are_exactly_new_maintenance(monkeypatch: pytest.MonkeyPatch) -> None:
    _restrict_to_core(monkeypatch)
    new, _ = _new_after_old_layout()
    needs = policy() & new   # deployment.json của gói: file cần dừng dịch vụ
    if missing := sorted(needs - NEW_MAINTENANCE):
        pytest.fail(f"File mới cần dừng dịch vụ (deployment.json) chưa có trong NEW_MAINTENANCE ở {WHERE}: " + ", ".join(f'"{k}"' for k in missing))
    if extra := sorted(NEW_MAINTENANCE - needs):
        pytest.fail(f"NEW_MAINTENANCE ở {WHERE} nhắc file không phải file mới cần dừng dịch vụ — thêm vào deployment.json của gói hoặc bỏ khỏi "
                    "NEW_MAINTENANCE: " + ", ".join(f'"{k}"' for k in extra))


def test_the_guard_names_the_file_to_add(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chính thước đo: một file mới giả trong cây lõi ⇒ báo đúng tên file phải thêm."""
    from barista.kernel import migrate

    _restrict_to_core(monkeypatch)
    real = migrate.chains()
    pack, found = real[0]
    fake = f"{int(OLD_LAST) + 900:03d}_quen_ghi_so.sql"
    monkeypatch.setattr("tests.test_upgrade_ledger.chains", lambda: [(pack, {**found, fake: next(iter(found.values()))}), *real[1:]])
    with pytest.raises(pytest.fail.Exception) as e:
        test_every_core_migration_after_the_old_layout_is_listed_in_new_files(monkeypatch)
    assert f'"{pack}/{fake}"' in str(e.value) and "NEW_FILES" in str(e.value)
