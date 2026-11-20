"""Phát hiện cho Báo cáo chạy song song (0230, api 1.51): module / gói khai ``<module>.findings_report`` (``internal=True``, ``agent="deny"``, ``callers=("findings",)``)
rồi dựng kết quả bằng các hàm dưới đây — luật cứng (chưa đo được ⇒ không số, có số ⇒ có công thức) nằm ở ``Finding``. Hợp đồng đầy đủ: ``kernel/findings.py``."""

from barista.kernel.findings import (
    CONFIDENCE,
    Finding,
    Metric,
    contributors,
    day_text,
    dec_text,
    finding,
    impact_count,
    impact_money,
    median,
    metric,
    money_text,
    n_text,
    parse,
    pct_text,
    unmeasured,
)

__all__ = ["CONFIDENCE", "Finding", "Metric", "contributors", "day_text", "dec_text", "finding", "impact_count", "impact_money", "median", "metric", "money_text", "n_text",
           "parse", "pct_text", "unmeasured"]
