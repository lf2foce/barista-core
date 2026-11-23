"""THANH BÊN THEO VAI (ADR 0255 lát 1) — phần THUẦN: ghép hai nhóm "Theo vai" / "Đã ghim" từ khoá phân hệ người xem thấy (không DB), khai báo ``Pack(rail=)`` bị
kiểm lúc khai, gói tắt không góp mặc định. Phần có DB (manifest theo vai, ghim, công ty đổi) ở ``tests/standin/test_rail.py``."""

import pytest

from barista.kernel import rail, registry
from barista.kernel.registry import RAIL_MAX, Pack


def test_two_groups_show_each_workspace_once_and_only_what_the_person_sees() -> None:
    visible = ["a", "b", "c", "d"]
    assert rail.build(["b", "a"], ["d"], visible) == {"role": ["b", "a"], "pinned": ["d"]}
    assert rail.build(["a", "b"], ["b", "c", "c", "a"], visible) == {"role": ["a", "b"], "pinned": ["c"]}   # đã ở "Theo vai" ⇒ không lặp ở "Đã ghim"; trùng chỉ giữ một
    assert rail.build(["a", "x"], ["y", "d"], visible) == {"role": ["a"], "pinned": ["d"]}   # phân hệ không thấy (mất quyền / gói tắt) bị lọc, không báo lỗi


def test_no_visible_default_means_no_groups_so_the_menu_stays_as_before() -> None:
    assert rail.build([], ["a"], ["a", "b"]) is None
    assert rail.build(["x", "y"], ["a"], ["a", "b"]) is None   # mặc định không còn cái nào thấy được ⇒ không có nhóm (không để thanh bên trống)


def test_the_role_group_is_capped() -> None:
    keys = [f"w{i}" for i in range(RAIL_MAX + 3)]
    out = rail.build(keys, [], keys)
    assert out is not None and out["role"] == keys[:RAIL_MAX] and RAIL_MAX == 6


def test_packs_that_are_off_contribute_nothing() -> None:
    p = Pack("rail_probe", "Gói thử", "Gói giả thử thanh bên", kind="industry", rail={"sale": ("a", "b"), "boss": ("c",)})
    try:
        assert {"a", "b"} <= set(rail.pack_default("sale"))   # mọi gói đã nạp (không nêu ``on``) — kể cả gói ngoài đang cài trong môi trường này
        assert rail.pack_default("boss", {"base"}) == ()                                     # gói tắt ⇒ không góp
        assert rail.pack_default("boss", {"base", "rail_probe"}) == ("c",)
        other = Pack("rail_probe2", "Gói thử 2", "Gói giả thử thanh bên", kind="industry", rail={"boss": ("c", "d")})   # hai gói cùng góp một vai: thứ tự nạp, bỏ trùng
        try:
            assert rail.pack_default("boss", {"rail_probe", "rail_probe2"}) == ("c", "d")
        finally:
            registry.PACKS.pop(other.key)
    finally:
        registry.PACKS.pop(p.key)


@pytest.mark.parametrize("bad", [
    {"sale": ()},                                   # rỗng
    {"sale": ("a", "a")},                           # trùng
    {"sale": tuple(f"w{i}" for i in range(RAIL_MAX + 1))},   # vượt trần
    {"sale": ("Co_Hoa",)},                          # khoá phân hệ sai dạng
    {"sale": ("co-gach",)},
    {"Sai Vai": ("a",)},                            # khoá vai sai dạng
])
def test_a_bad_declaration_is_refused_when_the_pack_is_declared(bad: dict[str, tuple[str, ...]]) -> None:
    with pytest.raises(ValueError, match="thanh bên"):
        Pack("rail_bad", "Gói lỗi", "Gói giả khai thanh bên sai", kind="industry", rail=bad)
    assert "rail_bad" not in registry.PACKS   # khai lỗi không để lại gói dở dang
