"""Bước kiểm phần mặc định của phát hành (``deploy/ops/deploy.py`` — ``defaults_check``): sau migrate, từ ảnh mới, chạy ``barista.defaults_report --strict``
(chỉ đọc) và IN báo cáo; thiếu thứ quan trọng ⇒ mặc định chỉ cảnh báo, ``"defaults_check": "block"`` ⇒ dừng TRƯỚC khi đổi bản (chỉ lượt rolling); bước kiểm
hỏng ⇒ cảnh báo, không chặn. Không gọi docker thật."""

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("barista_deploy_defaults", REPO / "deploy/ops/deploy.py")
assert _spec is not None and _spec.loader is not None
ops = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ops)
API = "barista-api:" + "b" * 40


def _deployment(tmp_path: Path, **inst: Any) -> Any:
    d = ops.Deployment.__new__(ops.Deployment)
    d.instance, d.root = {"name": "acme", **inst}, tmp_path
    return d


def _fake_run(monkeypatch: pytest.MonkeyPatch, code: int, out: str = "", err: str = "") -> list[list[str]]:
    calls: list[list[str]] = []

    def run(args: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, code, out, err)

    monkeypatch.setattr(ops.subprocess, "run", run)
    return calls


def test_report_runs_read_only_from_the_new_image_and_is_printed(tmp_path, monkeypatch, capsys):
    calls = _fake_run(monkeypatch, 0, "Công ty A: mặc định của các gói đang bật đã cài đủ\nKhông thiếu thứ quan trọng")
    _deployment(tmp_path).defaults_check(API, can_block=True)
    (call,) = calls
    assert call[-4:] == [API, "-m", "barista.defaults_report", "--strict"] and "--env-file" in call
    assert "--profile" not in call and "install" not in " ".join(call)   # không cài gì
    assert "đã cài đủ" in capsys.readouterr().out


def test_missing_important_defaults_only_warn_unless_the_install_asks_to_block(tmp_path, monkeypatch, capsys):
    _fake_run(monkeypatch, ops.DEFAULTS_MISSING, "Công ty A: 2 chưa cài\n  ! [chưa cài] chain duyet_moi (Gói)")
    _deployment(tmp_path).defaults_check(API, can_block=True)                              # vắng khoá ⇒ warn
    assert "phát hành TIẾP" in capsys.readouterr().out
    _deployment(tmp_path, defaults_check="block").defaults_check(API, can_block=False)    # maintenance: dịch vụ đã dừng ⇒ chỉ cảnh báo
    assert "phát hành TIẾP" in capsys.readouterr().out
    with pytest.raises(RuntimeError, match="DỪNG trước khi đổi bản"):
        _deployment(tmp_path, defaults_check="block").defaults_check(API, can_block=True)
    assert "chain duyet_moi" in capsys.readouterr().out                                    # báo cáo in TRƯỚC khi dừng


def test_a_broken_check_never_blocks_the_release(tmp_path, monkeypatch, capsys):
    _fake_run(monkeypatch, 1, "", "ModuleNotFoundError: No module named 'barista.defaults_report'")
    _deployment(tmp_path, defaults_check="block").defaults_check(API, can_block=True)
    assert "Không kiểm được phần mặc định (mã 1)" in capsys.readouterr().out

    def boom(*_: Any, **__: Any) -> Any:
        raise FileNotFoundError("docker")

    monkeypatch.setattr(ops.subprocess, "run", boom)
    _deployment(tmp_path, defaults_check="block").defaults_check(API, can_block=True)
    assert "FileNotFoundError" in capsys.readouterr().out


def test_rolling_release_checks_after_migrate_and_a_block_stops_before_switching(tmp_path, monkeypatch):
    steps: list[str] = []
    d = _deployment(tmp_path, defaults_check="block")
    old = {"services": {s: {"image": f"barista-{s}:" + "a" * 40} for s in ops.SERVICES}}
    monkeypatch.setattr(d, "current", lambda: {"composeFile": json.dumps(old)}, raising=False)
    monkeypatch.setattr(d, "migration_plan", lambda _img: {"maintenance": []}, raising=False)
    monkeypatch.setattr(d, "migrate", lambda _img: steps.append("migrate"), raising=False)
    monkeypatch.setattr(d, "apply", lambda *_: steps.append("apply"), raising=False)

    def check(_img: str, *, can_block: bool) -> None:
        steps.append(f"check:{can_block}")
        raise RuntimeError("thiếu")

    monkeypatch.setattr(d, "defaults_check", check, raising=False)
    with pytest.raises(RuntimeError, match="thiếu"):
        d.release("c" * 40, 1, json.dumps(old))
    assert steps == ["migrate", "check:True"]                                               # chưa đổi bản


def test_instance_file_only_accepts_warn_or_block(tmp_path):
    base = {"name": "acme", "root": "/root/acme", "db_host": "acme-postgres", "api_host": "acme-api.example.vn", "web_host": "acme.example.vn",
            "cron_file": "/etc/cron.d/acme-backup", "env": "demo"}
    for value, good in (("warn", True), ("block", True), ("stop", False)):
        (tmp_path / "acme.json").write_text(json.dumps({**base, "defaults_check": value}))
        if good:
            assert ops.load_instance("acme", tmp_path)["defaults_check"] == value
        else:
            with pytest.raises(ValueError, match="defaults_check"):
                ops.load_instance("acme", tmp_path)
