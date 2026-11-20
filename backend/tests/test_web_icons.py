"""Mọi tên icon (SF Symbols) server khai cho phân hệ / màn đều có ánh xạ lucide ở web — tên lạ rơi về CÙNG một ô lưới, hai mục menu trông như nhau
(11/11: 36 tên thiếu, "Quản lý giao dịch" và "KPI & xếp hạng" cùng một icon). App Mac dùng SF Symbols gốc nên không cần bảng này."""

import re
from pathlib import Path

import pytest

import barista.main  # noqa: F401 — nạp mọi module để có đủ phân hệ / màn
from barista import workspaces

ICONS = Path(__file__).resolve().parents[2] / "web" / "src" / "components" / "icons.tsx"


def test_every_declared_icon_has_a_web_mapping() -> None:
    if not ICONS.exists():
        pytest.skip("không có thư mục web cạnh backend")
    body = ICONS.read_text().split("const MAP", 1)[1].split("};", 1)[0]
    mapped = set(re.findall(r'^\s*"?([a-z0-9.]+)"?:\s*[A-Z]', body, re.M))
    names = {w.icon for w in workspaces.workspaces()} | {s.icon for w in workspaces.workspaces() for s in w.screens if getattr(s, "icon", "")}

    def ok(n: str) -> bool:   # cùng thứ tự tra như SfIcon: nguyên tên → bỏ ".fill" → thêm ".fill"
        return n in mapped or n.removesuffix(".fill") in mapped or f"{n}.fill" in mapped

    assert len(names) > 50, len(names)   # không đỗ vì danh sách rỗng
    missing = sorted(n for n in names if n and not ok(n))
    assert not missing, f"thêm vào MAP ở web/src/components/icons.tsx: {missing}"
