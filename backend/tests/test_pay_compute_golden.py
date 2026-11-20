"""Đặc tả hiện trạng của ``pay.engine.compute`` (hàm tính phiếu lương thuần): ~600 đầu vào (lương tháng / theo giờ / theo lượt, làm thêm giờ, trần bảo hiểm
theo vùng, thuế luỹ tiến và 10 %, khấu trừ, đoàn phí, tháng dở dang, cấn tiền ứng, quy tắc chưa xác minh, kỳ phụ có ``prior``) với đầu ra ĐÃ CHỤP.
So SÁT TỪNG BYTE (thứ tự dòng, ``rule_ref``, số nguyên đồng, thứ tự khoá) — refactor ``compute`` phải giữ nguyên; chỉ sinh lại golden
(``uv run python -m tests.pay_compute_cases``; file nén ``tests/golden/pay_compute.json.gz``) khi CỐ Ý đổi cách tính, và nói rõ trong commit."""

import copy
import json
from typing import Any

from barista.modules.pay import engine
from tests.pay_compute_cases import read_golden, resolve, run

_DOC = read_golden()
_CASES: list[dict[str, Any]] = _DOC["cases"]


def _dump(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def test_grid_is_broad() -> None:
    kinds = {"raises": 0, "held": 0, "clean": 0}
    for c in _CASES:
        o = c["output"]
        kinds["raises" if "raises" in o else "held" if o["held"] else "clean"] += 1
    assert len(_CASES) >= 600 and kinds["clean"] >= 200 and kinds["held"] >= 200, kinds
    lines = {(x["kind"], x["component"]) for c in _CASES if "lines" in c["output"] for x in c["output"]["lines"]}
    assert {k for k, _ in lines} == {"earning", "deduction", "withholding", "employer", "offset"}


def test_compute_matches_golden_byte_for_byte() -> None:
    bad: list[str] = []
    for case in _CASES:
        inputs = resolve(case["inputs"], _DOC["laws"])
        before = copy.deepcopy(inputs)
        got = run(inputs)
        if _dump(got) != _dump(case["output"]):
            bad.append(case["name"])
        elif inputs != before:
            bad.append(f"{case['name']} (sửa đầu vào — bản chụp trên phiếu phải bất biến)")
    assert not bad, f"{len(bad)}/{len(_CASES)} ca lệch golden, đầu tiên: {bad[:10]}"


def test_replaying_is_deterministic() -> None:
    c = next(c for c in _CASES if "lines" in c["output"] and c["output"]["lines"])
    inputs = resolve(c["inputs"], _DOC["laws"])
    assert _dump(engine.compute(copy.deepcopy(inputs))) == _dump(engine.compute(copy.deepcopy(inputs)))
