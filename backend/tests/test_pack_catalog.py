"""Danh mục gói chưa cài (0163): Store hiện thẻ "Chưa có trên bản cài" cho gói bán được mà mã KHÔNG nằm trên bản cài. Danh mục là file JSON tĩnh
(``settings.pack_catalog_file``) — chỉ chữ; không thao tác, không màn, không hành vi của gói. File vắng / hỏng ⇒ Store như cũ."""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from barista import seed
from barista.config import settings
from barista.kernel import executor, pack_catalog, registry
from tests.conftest import ok, owner_val, q

ADMIN = [("admin", "Quản trị", "admin", "QT", True)]
GHOST = {"key": "ghost_pack", "title": "Gói ma", "description": "Mô tả gói ma", "kind": "industry", "version": "v1.2.0", "requires_core": ">=1.9,<2",
         "requires": ["finance"], "summary": "Thêm sổ ma", "modules": ["Phần ma"], "roles": ["Vai ma"]}


def _write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: Any) -> None:
    f = tmp_path / "pack_catalog.json"
    f.write_text(body if isinstance(body, str) else json.dumps(body))
    monkeypatch.setattr(settings, "pack_catalog_file", str(f))


async def _admin() -> Any:
    d = f"{uuid.uuid4().hex[:6]}.danhmuc.test"
    tid = await seed.create_tenant("Công ty danh mục (test)", d, packs=("finance",), people=ADMIN)
    return tid, await seed.who(tid, "admin", d)


def _tiles(store: dict[str, Any], title: str) -> list[dict[str, Any]]:
    return [t for b in store["blocks"] if b.get("type") == "tiles" and title in b["title"] for t in b["items"]]


async def test_without_a_catalog_file_the_store_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, admin = await _admin()
    monkeypatch.setattr(settings, "pack_catalog_file", str(tmp_path / "khong-co.json"))
    store = await q(admin, "admin.pack_store")
    assert not [b for b in store["blocks"] if b.get("type") == "tiles" and "chưa có trên bản cài" in b["title"]]
    r = await executor.query(admin, "admin.catalog_pack", {"pack": "ghost_pack"})
    assert r.status == "not_found"


@pytest.mark.parametrize("body", ["{không phải json", "[]", '{"packs": "x"}', '{"packs": [1, null, {"key": "Bad Key"}, {"key": "x", "kind": "core", "title": "t"}]}', ""])
async def test_a_malformed_catalog_never_crashes_the_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    _, admin = await _admin()
    _write(tmp_path, monkeypatch, body)
    store = await q(admin, "admin.pack_store")
    assert store["blocks"] and not _tiles(store, "chưa có trên bản cài")


async def test_catalog_cards_appear_for_packs_not_installed_and_are_text_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    installed = next(iter(registry.PACKS.values()))
    _write(tmp_path, monkeypatch, {"format": 1, "packs": [GHOST, {**GHOST, "key": installed.key, "title": "Trùng gói cài"}, {**GHOST, "key": "ghost_pack"}]})
    ops_before, screens_before = set(registry.OPS), set(registry.PACKS)
    _, admin = await _admin()
    store = await q(admin, "admin.pack_store")
    cards = _tiles(store, "chưa có trên bản cài")
    assert [c["title"] for c in cards] == ["Gói ma"]                                  # gói đã cài thắng; khoá trùng chỉ một thẻ
    assert cards[0]["badge"] == "Chưa có trên bản cài · Liên hệ" and cards[0]["tone"] == "warn"
    assert all(t["badge"] != cards[0]["badge"] for b in store["blocks"] if b.get("type") == "tiles" and "chưa có" not in b["title"] for t in b["items"])
    assert cards[0]["open"] == {"query": "admin.catalog_pack", "input": {"pack": "ghost_pack"}}
    page = await q(admin, "admin.catalog_pack", pack="ghost_pack")
    text = json.dumps(page, ensure_ascii=False)
    assert "Mô tả gói ma" in text and ">=1.9,<2" in text and "Thêm sổ ma" in text and "không nằm trên máy chủ này" in text
    assert [a["command"] for a in page["actions"]] == ["admin.request_pack"]
    # không rò hành vi: đọc danh mục không đăng ký thao tác / gói / màn nào
    assert set(registry.OPS) == ops_before and set(registry.PACKS) == screens_before and "ghost_pack" not in registry.PACKS
    r = await executor.query(admin, "admin.pack_info", {"pack": "ghost_pack"})
    assert r.status != "ok"
    r = await executor.run(admin, "admin.set_pack", {"pack": "ghost_pack", "enabled": True})
    assert r.status != "ok"
    assert "ghost_pack" not in {t["open"]["input"].get("pack") for b in store["blocks"] if b.get("type") == "tiles"
                                for t in b["items"] if t["open"]["query"] == "admin.pack_info"}


async def test_the_loader_drops_bad_rows_one_by_one(tmp_path: Path) -> None:
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"packs": [GHOST, {**GHOST, "key": "two", "title": ""}, {**GHOST, "key": "three", "requires": "finance"}, "x",
                                       {**GHOST, "key": "ok2", "kind": "capability", "description": None}]}))
    assert [e.key for e in pack_catalog.load(f)] == ["ghost_pack", "ok2"]


async def test_requesting_a_pack_records_one_row_notifies_and_is_admin_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, monkeypatch, {"format": 1, "packs": [GHOST]})
    tid, admin = await _admin()
    other_tid, _ = await _admin()
    out = await ok(admin, "admin.request_pack", pack="ghost_pack", note="Cần cho phòng cho thuê")
    assert out["added"] is True
    again = await ok(admin, "admin.request_pack", pack="ghost_pack")
    assert again["added"] is False
    assert await owner_val("SELECT count(*) FROM core.pack_requests WHERE tenant_id = %s", (tid,)) == 1
    assert await owner_val("SELECT count(*) FROM core.pack_requests WHERE tenant_id = %s", (other_tid,)) == 0   # công ty khác không thấy / không bị ghi
    page = await q(admin, "admin.catalog_pack", pack="ghost_pack")
    assert page["actions"] == [] and "Đã gửi yêu cầu" in json.dumps(page, ensure_ascii=False)
    r = await executor.run(admin, "admin.request_pack", {"pack": "finance"})                  # gói đã cài: không xin được
    assert r.status == "not_found"
    assert registry.OPS["admin.request_pack"].agent == "deny"                                  # trợ lý không xin thay


async def test_a_non_admin_cannot_request_a_pack_or_open_the_catalog_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, monkeypatch, {"format": 1, "packs": [GHOST]})
    d = f"{uuid.uuid4().hex[:6]}.danhmuc2.test"
    tid = await seed.create_tenant("Công ty danh mục 2 (test)", d, packs=("finance",),
                                   people=[*ADMIN, ("kt", "Kế toán", "accountant", "KT", False)])
    staff = await seed.who(tid, "kt", d)
    for r in (await executor.run(staff, "admin.request_pack", {"pack": "ghost_pack"}), await executor.query(staff, "admin.catalog_pack", {"pack": "ghost_pack"})):
        assert r.status == "denied"
    assert await owner_val("SELECT count(*) FROM core.pack_requests WHERE tenant_id = %s", (tid,)) == 0


async def test_the_store_is_off_the_menu_and_its_page_still_reaches_the_remote_apps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from barista.workspaces import workspaces

    # Store ẩn khỏi menu từ 24/11/2026 (ADR 0261 — tầng hệ thống; người dùng thấy "Kết nối & kỹ năng"); truy vấn vẫn chạy cho nhà vận hành
    assert not any(s.query == "admin.pack_store" for w in workspaces() for s in w.screens)
    monkeypatch.setattr(settings, "pack_catalog_file", str(tmp_path / "khong-co.json"))
    _, admin = await _admin()
    store = await q(admin, "admin.pack_store")
    apps = [t for b in store["blocks"] if b.get("type") == "tiles" and "kết nối" in b["title"] for t in b["items"]]
    assert [t["open"] for t in apps] == [{"query": "connect.apps", "input": {}}]
    await q(admin, "connect.apps")                                            # thẻ mở được thật
