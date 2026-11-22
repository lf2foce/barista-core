"""SĐT RA CHỮ CHO NGƯỜI ĐỌC (0199, bổ sung 15/11/2026): lưu E.164, hiện số nội địa LIỀN theo quốc gia công ty (``0898654936``, không nhóm, không ``+84``),
số nước khác đủ mã (``+14155552671``). Một hàm duy nhất ``phone_display`` (web: ``format.ts::phone``). Test này giữ ba thứ:

1. luật của hàm;
2. CANH GÁC mã nguồn: chữ đưa SĐT ra màn (f-string, ``" · ".join``, nối ``+``) không được nhét cột SĐT thô — phải qua ``phone_display`` / ``mask_phone``;
3. chữ gửi KHÁCH (tin soạn sẵn) không mang mã nội bộ của sản phẩm / dự án (``GOI-A``, ``HSR``): có tên thì nói tên, không có tên thì bỏ.
"""

import ast
import re
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook

from barista.kernel import ui
from barista.kernel.fields import phone_display
from barista.modules.crm.journey import _draft
from tests.module_src import py_files, rel

# ── 1. luật của hàm ──


@pytest.mark.parametrize(("stored", "country", "shown"), [
    ("+84898654936", "VN", "0898654936"),      # số Việt Nam ở công ty Việt Nam: nội địa liền, không nhóm
    ("+84912345678", None, "0912345678"),      # không nêu nước ⇒ nước của công ty (mặc định VN)
    ("+842412345678", "VN", "02412345678"),    # số bàn: mã vùng + số, vẫn liền
    ("+14155552671", "VN", "+14155552671"),    # số nước ngoài: giữ ĐỦ mã nước
    ("+442071838750", "VN", "+442071838750"),
    ("+84898654936", "US", "+84898654936"),    # số Việt Nam ở công ty nước khác (chưa có luật nội địa): quốc tế
    ("+14155552671", "US", "+14155552671"),
    ("0898654936", "VN", "0898654936"),        # không phải E.164 (dữ liệu cũ): trả nguyên
    ("0898 65• ••••", "VN", "0898 65• ••••"),  # số đã che: trả nguyên
    ("+84abc", "VN", "+84abc"),
    ("", "VN", ""),
    (None, "VN", ""),
])
def test_phone_display_rule(stored: str | None, country: str | None, shown: str) -> None:
    assert phone_display(stored, country) == shown


def test_phone_display_has_no_spaces_for_national_numbers() -> None:
    assert " " not in phone_display("+84898654936", "VN")


def test_excel_phone_cells_are_text_in_national_form() -> None:
    """Excel: ô SĐT là CHỮ (số 0 đầu không mất) và đúng dạng như trên màn."""
    page = ui.table([{"id": "1", "name": "A", "phone": "+84898654936"}, {"id": "2", "name": "B", "phone": "+14155552671"}, {"id": "3", "name": "C", "phone": None}],
                    [ui.col("name", "Khách"), ui.col("phone", "SĐT", "phone")], title="Khách")
    ws = load_workbook(BytesIO(ui.table_xlsx(page, "VN"))).active
    assert ws is not None
    assert [ws.cell(r, 2).value for r in (2, 3, 4)] == ["0898654936", "+14155552671", None]
    assert ws.cell(2, 2).data_type == "s" and ws.cell(2, 2).number_format == "@"
    ws_us = load_workbook(BytesIO(ui.table_xlsx(page, "US"))).active
    assert ws_us is not None and ws_us.cell(2, 2).value == "+84898654936"


# ── 2. canh gác mã nguồn ──

_PHONE_KEY = re.compile(r"(^|_)phone$")
_SAFE = {"phone_display", "mask_phone", "mask_value"}   # hàm đưa số ra chữ đúng luật
# {"<file>:<dòng>": lý do} — chỗ đưa cột SĐT thô vào chữ mà CÓ CHỦ ĐÍCH. Thêm một dòng = đã xem từng chỗ, không phải tắt test.
ALLOWED: dict[str, str] = {
    "modules/papers/__init__.py:606": "SĐT CÔNG TY trên giấy tờ: chữ tự do do quản trị gõ (papers.settings.phone), in đúng như họ muốn — không phải cột SĐT khách E.164",
}


def _name(n: ast.expr) -> str:
    return n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else ""


def _raw_phone(node: ast.AST) -> ast.expr | None:
    """Biểu thức có đọc cột / biến SĐT thô (``r["phone"]``, ``lead.phone``, ``customer_phone``) mà KHÔNG nằm trong lời gọi hàm đưa số ra chữ."""
    if isinstance(node, ast.Call) and _name(node.func) in _SAFE:
        return None
    if isinstance(node, ast.IfExp):   # ``x if r["phone"] else ""`` — vế ĐIỀU KIỆN chỉ hỏi có số hay không, không đưa số ra chữ
        return _raw_phone(node.body) or _raw_phone(node.orelse)
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str) and _PHONE_KEY.search(node.slice.value):
        return node
    if isinstance(node, ast.Attribute) and _PHONE_KEY.search(node.attr):
        return node
    if isinstance(node, ast.Name) and _PHONE_KEY.search(node.id):
        return node
    for c in ast.iter_child_nodes(node):
        if (hit := _raw_phone(c)) is not None:   # cả ``comprehension`` (không phải ast.expr) — ``for x in (r["phone"], …)``
            return hit
    return None


_KEY_CALLS = {"get", "setdefault", "pop", "add", "discard", "remove"}   # dict / set: chuỗi làm KHOÁ tra cứu, không phải chữ cho người đọc


def _is_lookup_key(s: ast.JoinedStr, parent: dict[ast.AST, ast.AST]) -> bool:
    """f-string chỉ làm KHOÁ tra cứu (``found[f"{phone}|{uid}"]``, ``have.get(f"…")``, ``f"…" in seen``, khoá của dict) — so khớp nội bộ, không hiện ra."""
    up = parent.get(s)
    if isinstance(up, ast.Subscript) and up.slice is s:
        return True
    if isinstance(up, ast.Call) and isinstance(up.func, ast.Attribute) and up.func.attr in _KEY_CALLS and s in up.args:
        return True
    if isinstance(up, ast.Compare) and any(isinstance(o, ast.In | ast.NotIn) for o in up.ops):
        return True
    return isinstance(up, ast.Dict) and s in up.keys


def _keeps_digits(arg: ast.expr) -> bool:
    """``"".join(ch for ch in phone if ch.isdigit())`` — chuẩn hoá (lấy chữ số), không đưa số ra chữ cho người đọc."""
    if not (isinstance(arg, ast.GeneratorExp | ast.ListComp) and isinstance(arg.elt, ast.Name) and len(arg.generators) == 1):
        return False
    g = arg.generators[0]
    return (isinstance(g.target, ast.Name) and g.target.id == arg.elt.id
            and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "isdigit" for i in g.ifs for c in ast.walk(i)))


def _text_sites(tree: ast.AST) -> list[tuple[int, ast.expr]]:
    """Chỗ ghép CHỮ cho người đọc: ô trong f-string, đối số của ``"…".join(…)``, vế của phép ``chuỗi + …`` — trừ f-string làm khoá tra cứu và
    ``join`` lọc chữ số (chuẩn hoá)."""
    parent = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    out: list[tuple[int, ast.expr]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.JoinedStr):
            if not _is_lookup_key(n, parent):
                out += [(v.lineno, v.value) for v in n.values if isinstance(v, ast.FormattedValue)]
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "join" and isinstance(n.func.value, ast.Constant):
            out += [(n.lineno, a) for a in n.args if not _keeps_digits(a)]
        elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
            for a, b in ((n.left, n.right), (n.right, n.left)):
                if isinstance(a, ast.Constant | ast.JoinedStr) and (isinstance(a, ast.JoinedStr) or isinstance(a.value, str)):
                    out.append((n.lineno, b))
    return out


def _offenders(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    seen: set[int] = set()
    bad = []
    for _, expr in _text_sites(tree):
        if (hit := _raw_phone(expr)) is not None and f"{rel(path)}:{hit.lineno}" not in ALLOWED and hit.lineno not in seen:
            seen.add(hit.lineno)
            bad.append(f"{rel(path)}:{hit.lineno}: {ast.unparse(hit)}")
    return bad


def test_no_screen_text_interpolates_a_raw_phone_column() -> None:
    """f-string / join / nối chuỗi trong mã module (lõi + gói đang cài) không được đưa cột SĐT THÔ (E.164 ``+84…``) ra chữ cho người đọc — cho nó đi qua
    ``phone_display`` (tiêu đề thẻ, dòng phụ, thẻ nhắc, tin soạn sẵn, thông báo). Cột SĐT của BẢNG khai ``ui.col(…, "phone")`` thì máy khách / Excel tự định dạng."""
    bad = [x for f in py_files() if f.suffix == ".py" and "seed" not in f.name for x in _offenders(f)]
    assert not bad, "SĐT thô trong chữ hiện cho người đọc (bọc ``phone_display(...)``):\n" + "\n".join(bad)


def test_the_guard_catches_the_known_bad_shapes(tmp_path: Path) -> None:
    f = tmp_path / "x.py"
    f.write_text('a = f"gọi ({r[\'phone\']})"\nb = " · ".join(x for x in (lead["phone"], 1) if x)\nc = "SĐT " + c.customer_phone\n'
                 'ok1 = f"gọi ({phone_display(r[\'phone\'])})"\nok2 = f"{mask_phone(r[\'phone\'])}"\nok3 = {"phone": r["phone"]}\n', encoding="utf-8")
    got = _offenders(f)
    assert len(got) == 3, got
    assert sorted(g.split(":")[1] for g in got) == ["1", "2", "3"]


# ── 3. chữ gửi khách không mang mã nội bộ ──

def _lead(code: str | None = "GOI-A", last: Any = None) -> dict[str, Any]:
    return {"customer": "Nguyễn Văn An", "offering_code": code, "interest": {"detail": "≤ 5 tỷ"}, "last_activity_at": last}


@pytest.mark.parametrize("kind", ["uncalled", "follow", "hot"])
@pytest.mark.parametrize("last", [None, "đã chăm"])
def test_customer_draft_never_shows_the_internal_code(kind: str, last: Any) -> None:
    for product in (None, "Hồ Sen"):
        text = _draft("Lan", _lead(last=last), kind, product)
        assert "GOI-A" not in text, text
        assert text.count("Hồ Sen") == (1 if product else 0)
        assert "None" not in text and "  " not in text   # bỏ phần tên thì câu vẫn trọn, không thừa khoảng trắng


def test_draft_after_a_previous_talk_names_the_product_or_omits_it() -> None:
    with_name = _draft("Lan", _lead(last="đã chăm"), "follow", "Hồ Sen")
    assert "Hôm trước mình có trao đổi về Hồ Sen" in with_name
    without = _draft("Lan", _lead(last="đã chăm"), "follow", None)
    # không có tên ⇒ câu vẫn tự nhiên: "về nhu cầu của anh/chị (≤ 5 tỷ)" khi có nhu cầu, "với nhau" khi không — không câu cụt "trao đổi (≤ 5 tỷ)"
    assert ("Hôm trước mình có trao đổi về nhu cầu của anh/chị (" in without) or ("Hôm trước mình có trao đổi với nhau." in without), without
    assert "trao đổi (" not in without


def test_guard_ignores_lookup_keys_and_digit_normalisation_but_not_display() -> None:
    src = """
seen = {}
k = seen.get(f"{r['customer_phone']}|{uid}")
x = found[f"{c['customer_phone']}|{u}"]
digits = "".join(ch for ch in inp.phone if ch.isdigit())
title = f"Gọi {r['customer_phone']}"
"""
    tree = ast.parse(src)
    hits = [ast.unparse(h) for _, e in _text_sites(tree) if (h := _raw_phone(e)) is not None]
    assert hits == ["r['customer_phone']"], hits   # chỉ dòng tiêu đề là chữ cho người đọc
