"""Ô lọc CHÍNH của màn (rà bố cục 18/11/2026): app để ô chính ngoài hàng đầu, ô còn lại gom vào nút "Bộ lọc". Chỉ THÊM trường ``primary``."""

from typing import Any

from barista.kernel import forms, registry
from barista.kernel.fields import Input, field
from barista.workspaces import PRIMARY_DEFAULT, primary_filters


class _Flagged(Input):
    period: str = field("Kỳ", "month")
    q: str = field("Tìm", "", primary=True)
    more: str = field("Thêm", "", group="Lọc thêm")


def _f(key: str, group: str = "", primary: bool = False) -> dict[str, Any]:
    return {"key": key, "label": key, **({"group": group} if group else {}), **({"primary": True} if primary else {})}


def test_field_primary_is_only_added_when_declared() -> None:
    specs = {s["key"]: s for s in forms._model_specs(_Flagged)}
    assert specs["q"]["primary"] is True
    assert "primary" not in specs["period"] and "primary" not in specs["more"]   # app cũ không thấy trường lạ ở ô thường


def test_declared_primary_wins_over_the_default() -> None:
    out = primary_filters([_f("a"), _f("b"), _f("c", primary=True), _f("d")])
    assert [f["key"] for f in out if f.get("primary")] == ["c"]


def test_default_is_the_first_unfolded_fields() -> None:
    out = primary_filters([_f("x", group="Thêm điều kiện"), _f("a"), _f("b"), _f("c"), _f("y", group="Thêm điều kiện")])
    assert PRIMARY_DEFAULT == 2 and [f["key"] for f in out if f.get("primary")] == ["a", "b"]   # ô thuộc nhóm gấp không bao giờ tự thành chính
    assert [f["key"] for f in out] == ["x", "a", "b", "c", "y"]                                    # không đổi thứ tự / không bỏ ô
    assert primary_filters([]) == [] and [f.get("primary") for f in primary_filters([_f("g", group="G")])] == [None]


def test_accounting_lists_mark_search_and_period_as_primary() -> None:
    def prim(name: str) -> list[str]:
        return [s["key"] for s in forms.field_specs(registry.OPS[name]) if s.get("primary")]

    assert prim("acct.expenses") == ["q", "state"]
    assert prim("acct.misa_export") == ["month", "entity_code"]
