"""BẢN ĐỒ MODULE (``docs/MODULE-MAP.md``) sinh từ code — file đã commit phải khớp code hiện tại (ROADMAP N4).

Đỏ ⇒ thêm / bỏ / chuyển thao tác, hợp đồng, điểm cắm, sự kiện… mà chưa sinh lại bản đồ: ``cd backend && uv run python scripts/module_map.py``
rồi commit file cùng thay đổi code.
"""

import importlib.util
from pathlib import Path

import barista.main  # noqa: F401 — nạp mọi module để registry đủ

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "module_map.py"


def _script():
    spec = importlib.util.spec_from_file_location("module_map", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_module_map_matches_code():
    mm = _script()
    text = mm.render()
    assert mm.OUT.exists(), f"chưa có docs/MODULE-MAP.md — chạy: {mm.REGEN}"
    assert mm.OUT.read_text() == text, f"docs/MODULE-MAP.md lệch code — chạy: {mm.REGEN} rồi commit file cùng thay đổi"


def test_module_map_is_deterministic():
    mm = _script()
    assert mm.render() == mm.render()
