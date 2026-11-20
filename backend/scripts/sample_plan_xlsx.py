"""Ghi file kế hoạch MẪU để thử nhập kế hoạch từ Excel (demo): ``python backend/scripts/sample_plan_xlsx.py <thư mục>``.
Ra 3 file: ke-hoach-ra-mat.xlsx (tự soạn, lộn xộn), ke-hoach-ra-mat-ban-sua.xlsx (bản sửa — nhập lại để thấy cập nhật), ke-hoach-ms-project.xlsx (kiểu MS Project)."""

import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.plan_samples import ms_project_rows, revised_plan_rows, sample_plan_rows, xlsx  # noqa: E402

from barista.kernel.clock import today  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
out.mkdir(parents=True, exist_ok=True)
t0 = today() + timedelta(days=(7 - today().weekday()) % 7 or 7)   # một thứ Hai sắp tới
(out / "ke-hoach-ra-mat.xlsx").write_bytes(xlsx(sample_plan_rows(t0)))
(out / "ke-hoach-ra-mat-ban-sua.xlsx").write_bytes(xlsx(revised_plan_rows(t0)))
(out / "ke-hoach-ms-project.xlsx").write_bytes(xlsx(ms_project_rows(t0), "Task_Table"))
print("đã ghi 3 file vào", out)
