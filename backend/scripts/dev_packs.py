"""Dựng "môi trường gói" cho máy dev KHÔNG đụng .venv của lõi (runbook 18).

Lõi nạp gói qua entry point `barista.packs` — thông tin này nằm trong `*.dist-info/entry_points.txt` của gói đã cài. Cài thật (`uv pip install -e`)
vào .venv dùng chung làm MỌI phiên khác trên máy đột nhiên thấy gói (test của họ đổi theo). Script này thay bằng một thư mục site riêng (gitignore):
mỗi gói một `<tên>-<phiên bản>.dist-info` (METADATA + entry_points.txt chép từ pyproject.toml của gói), còn mã gói thì chỉ cần nằm trên PYTHONPATH.

    python scripts/dev_packs.py <thư mục site> <thư mục barista-packs> gói1 gói2 …   # ghi site, in PYTHONPATH cần thêm
"""

import shutil
import sys
import tomllib
from pathlib import Path


def build(site: Path, packs_dir: Path, keys: list[str]) -> list[str]:
    if site.exists():
        shutil.rmtree(site)
    site.mkdir(parents=True)
    paths = [str(site)]
    for key in keys:
        root = packs_dir / key
        proj = tomllib.loads((root / "pyproject.toml").read_text())
        name, version = proj["project"]["name"], proj["project"]["version"]
        src = root / "src"
        if not src.is_dir():
            raise SystemExit(f"Thiếu {src} — gói {key} không có thư mục src")
        info = site / f"{name.replace('-', '_')}-{version}.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        lines: list[str] = []
        for group, eps in proj["project"].get("entry-points", {}).items():
            lines.append(f"[{group}]")
            lines += [f"{k} = {v}" for k, v in eps.items()]
            lines.append("")
        (info / "entry_points.txt").write_text("\n".join(lines))
        paths.append(str(src))
    return paths


if __name__ == "__main__":
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    print(":".join(build(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:])))
