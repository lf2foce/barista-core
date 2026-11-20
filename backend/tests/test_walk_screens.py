"""Bộ quét màn hình (``scripts/walk_screens.py``): luật phân loại nội dung màn + cờ thoát ``--fail-on-error``.

Không cần backend: ``http`` bị thay bằng máy chủ giả trả manifest + trang mẫu (bảng đủ dòng / 0 dòng / thẻ toàn 0 / thẻ có biểu đồ / lỗi / từ chối).
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "walk_screens.py"


@pytest.fixture
def ws():
    spec = importlib.util.spec_from_file_location("walk_screens", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def table(rows=0, kpis=(), empty="Chưa có dữ liệu", **kw):
    return {"kind": "table", "title": "t", "kpis": [{"label": "k", "value": v} for v in kpis], "columns": [], "rows": [{"id": i} for i in range(rows)],
            "empty": empty, "note": "", **kw}


def card(*blocks, **kw):
    return {"kind": "card", "title": "t", "blocks": list(blocks), "actions": [], "open": [], **kw}


def run(ws, page, status=200, screen=None):
    out = {"status": "ok", "output": page} if status == 200 else {"status": "error", "error": "boom"}
    info = ws.read_page(page)
    return ws.classify(status, out, info, screen or {}), info


def test_table_with_rows_has_data(ws):
    (cls, _), _ = run(ws, table(rows=2))
    assert cls == "co_du_lieu"


def test_table_zero_rows_is_empty_even_if_kpi_nonzero(ws):
    (cls, note), _ = run(ws, table(rows=0, kpis=[5]))
    assert cls == "trong" and "ô số khác 0" in note


def test_empty_table_with_invitation(ws):
    (cls, _), info = run(ws, table(rows=0, empty_actions=[{"command": "x"}]))
    assert cls == "trong_co_loi_moi" and info["loi_moi"] == ["empty_actions"]
    (cls, _), info = run(ws, table(rows=0), screen={"actions": [{"command": "x"}]})
    assert cls == "trong_co_loi_moi" and info["loi_moi"] == ["man_actions"]


def test_card_all_zero_kpis_is_empty(ws):
    kp = {"type": "kpis", "items": [{"label": "a", "value": 0}, {"label": "b", "value": "0"}, {"label": "c", "value": None}, {"label": "d", "value": "—"}]}
    (cls, _), _ = run(ws, card(kp))
    assert cls == "trong"


def test_card_with_any_data_signal(ws):
    kp0 = {"type": "kpis", "items": [{"label": "a", "value": 0}]}
    assert run(ws, card(kp0, {"type": "kpis", "items": [{"label": "b", "value": 3}]}))[0][0] == "co_du_lieu"
    assert run(ws, card(kp0, {"type": "table", "page": table(rows=1)}))[0][0] == "co_du_lieu"
    assert run(ws, card(kp0, {"type": "chart", "series": [{"values": [None, 0, 2]}]}))[0][0] == "co_du_lieu"
    assert run(ws, card(kp0, {"type": "chart", "series": [{"values": [None, 0]}]}))[0][0] == "trong"   # biểu đồ toàn 0 / null = trống
    assert run(ws, card(kp0, {"type": "list", "items": [{"title": "x"}]}))[0][0] == "co_du_lieu"


def test_card_text_only_counts_when_long(ws):
    assert run(ws, card({"type": "text", "markdown": "Chưa có gì"}))[0][0] == "trong"
    assert run(ws, card({"type": "text", "markdown": "x" * 250}))[0][0] == "co_du_lieu"


def test_empty_queue_is_marked_normal(ws):
    (cls, _), info = run(ws, table(rows=0, empty="Không có phiếu nào chờ duyệt"))
    assert cls == "trong" and info["trong_binh_thuong"] and info["giai_thich"]
    _, info = run(ws, table(rows=0, empty="Chưa có lô đầu kỳ"))
    assert not info.get("trong_binh_thuong")


def test_error_and_denied(ws):
    assert run(ws, None, status=500)[0][0] == "loi"
    assert ws.classify(403, {"status": "denied", "error": "x"}, ws.read_page(None), {})[0] == "tu_choi"
    assert ws.classify(0, {"error": "timeout"}, ws.read_page(None), {})[0] == "loi"
    assert ws.classify(422, {"status": "invalid", "error": "thiếu ô"}, ws.read_page(None), {})[0] == "loi"


def fake_server(ws, monkeypatch, pages, delays=None):
    manifest = {"user": {"name": "A", "role": "x", "role_label": "Vai X"}, "pins": [{"workspace": "w", "key": "ok"}], "inbox": None, "desk": [],
                "workspaces": [{"key": "w", "title": "W", "screens": [{"key": k, "title": k, "query": f"q.{k}", "group": "", "input": {}, "filters": [], "actions": []}
                                                                 for k in pages]}]}

    def http(base, user, tenant, path, body=None, timeout=120):
        if path == "/v1/manifest":
            return 200, manifest, 0.01, 10
        name = path.rsplit("/", 1)[-1][2:]
        status, page = pages[name]
        out = {"status": "ok", "output": page} if status == 200 else {"status": "error" if status >= 500 else "denied", "error": "e"}
        return status, out, (delays or {}).get(name, 0.01), 100

    monkeypatch.setattr(ws, "http", http)


def test_main_exit_codes_and_outputs(ws, monkeypatch, tmp_path):
    fake_server(ws, monkeypatch, {"ok": (200, table(rows=1)), "empty": (200, table(rows=0)), "bad": (500, None)}, delays={"ok": 4.0})
    argv = ["walk_screens.py", "--tenant", "t", "--users", "a@x", "--out", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", argv)
    assert ws.main() == 0   # không cờ ⇒ chỉ báo cáo
    monkeypatch.setattr(sys, "argv", [*argv, "--fail-on-error"])
    assert ws.main() == 1
    data = json.loads((tmp_path / "walk.json").read_text())
    by = {m["man"]: m for m in data[0]["man"]}
    assert by["ok"]["phan_loai"] == "co_du_lieu" and by["ok"]["cham"] and by["ok"]["ghim"]
    assert by["empty"]["phan_loai"] == "trong" and by["bad"]["phan_loai"] == "loi"
    md = (tmp_path / "walk.md").read_text()
    assert "Ma trận màn × vai" in md and "`q.bad`" in md and "D*" in md   # D* = có dữ liệu + chậm


def test_main_clean_run_passes_with_fail_on_error(ws, monkeypatch, tmp_path):
    fake_server(ws, monkeypatch, {"ok": (200, table(rows=1)), "empty": (200, table(rows=0))})
    monkeypatch.setattr(sys, "argv", ["walk_screens.py", "--tenant", "t", "--users", "a@x", "--out", str(tmp_path), "--fail-on-error", "--fail-on-slow"])
    assert ws.main() == 0


def test_calendar_block_counts_as_data_and_unknown_block_not_empty(ws):
    assert run(ws, card({"type": "calendar", "items": []}))[0][0] == "trong"
    assert run(ws, card({"type": "calendar", "items": [{"id": 1}]}))[0][0] == "co_du_lieu"
    (cls, _), info = run(ws, card({"type": "weirdo", "items": [{"id": 1}]}))
    assert cls == "co_du_lieu" and info["khoi_la"] == ["weirdo"]   # khối lạ có dòng con ⇒ không kết luận trống


def test_suspicious_text(ws):
    uid = "c193f441-52b0-4738-b3ad-15add40e76c1"
    cols = [{"key": "id"}, {"key": "name"}, {"key": "owner_id"}]
    page = table(rows=0, kpis=[None, "None"])
    page["columns"], page["rows"] = cols, [{"id": uid, "name": uid, "owner_id": uid}, {"id": uid, "name": "ok", "owner_id": uid}]
    got = ws.suspicious(page)
    assert any(g.startswith("o:") and uid in g for g in got) and any(g == "k: None" for g in got)   # cột id / *_id không tính; KPI None thật (không đo được) không tính
    assert len([g for g in got if g.startswith("k:")]) == 1
    assert ws.suspicious(table(rows=1)) == []
    unmapped = table(rows=0)
    unmapped["columns"], unmapped["rows"] = [{"key": "debit"}], [{"debit": "????"}]
    assert ws.suspicious(unmapped) == ["o: ????"]   # tài khoản chưa ánh xạ hiện "????" (Xuất MISA)
