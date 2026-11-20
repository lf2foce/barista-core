"""DANH MỤC GÓI chưa cài trên bản này (0163) — để khách biết còn gói nào, không để chạy gói nào.

Gói không mua thì KHÔNG nằm trên máy chủ (0155): mã, migration, thao tác, màn của gói không có. Danh mục là file JSON tĩnh
(``barista/pack_catalog.json``, hoặc ``settings.pack_catalog_file``) do máy phát hành ghi vào tar phát hành (``deploy/packs.py bundle``) từ
``pyproject.toml`` + khai ``Pack(...)`` của mọi gói trong repo gói — chữ mô tả, không mã. Lõi chỉ ĐỌC file này, không nêu tên gói nào; gói đã
cài (``registry.PACKS``) luôn thắng dòng cùng khoá trong danh mục (người gọi lọc).

Dạng file: ``{"format": 1, "packs": [{"key", "title", "description", "kind": "industry" | "capability", "version", "requires_core",
"requires": [khoá gói], "summary", "modules": [tên], "roles": [tên]}]}``. File vắng / hỏng / sai dạng ⇒ danh mục rỗng (chợ gói y như cũ,
không lỗi); dòng sai dạng bị bỏ riêng từng dòng.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from barista.config import settings

DEFAULT_FILE = Path(__file__).resolve().parent.parent / "pack_catalog.json"
KINDS = ("industry", "capability")
KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,40}\Z")
MAX_FILE = 512 * 1024
MAX_PACKS = 200


@dataclass(frozen=True)
class CatalogPack:
    key: str
    title: str
    description: str
    kind: str
    version: str
    requires_core: str
    requires: tuple[str, ...]
    summary: str
    modules: tuple[str, ...]
    roles: tuple[str, ...]


def _text(v: Any, limit: int) -> str | None:
    return v.strip()[:limit] if isinstance(v, str) and v.strip() else None


def _texts(v: Any, limit: int, count: int = 30) -> tuple[str, ...] | None:
    if v is None:
        return ()
    if not isinstance(v, list):
        return None
    items = [_text(x, limit) for x in v[:count]]
    return tuple(x for x in items if x)


def _entry(raw: Any) -> CatalogPack | None:
    if not isinstance(raw, dict):
        return None
    key = raw.get("key")
    if not isinstance(key, str) or not KEY_RE.fullmatch(key) or raw.get("kind") not in KINDS:
        return None
    title, desc = _text(raw.get("title"), 120), _text(raw.get("description"), 800) or ""
    requires, modules, roles = _texts(raw.get("requires"), 41), _texts(raw.get("modules"), 120), _texts(raw.get("roles"), 120)
    if title is None or requires is None or modules is None or roles is None:
        return None
    return CatalogPack(key, title, desc, raw["kind"], _text(raw.get("version"), 24) or "", _text(raw.get("requires_core"), 60) or "",
                       requires, _text(raw.get("summary"), 800) or "", modules, roles)


def load(path: Path | None = None) -> tuple[CatalogPack, ...]:
    """Mọi gói của danh mục (đã lọc dòng sai dạng, khoá trùng giữ dòng đầu); file vắng / hỏng ⇒ rỗng. Đọc mỗi lần gọi — file nhỏ, chợ gói ít mở."""
    f = path or (Path(settings.pack_catalog_file) if settings.pack_catalog_file else DEFAULT_FILE)
    try:
        if f.stat().st_size > MAX_FILE:
            return ()
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    rows = data.get("packs") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return ()
    out: dict[str, CatalogPack] = {}
    for raw in rows[:MAX_PACKS]:
        e = _entry(raw)
        if e is not None and e.key not in out:
            out[e.key] = e
    return tuple(out.values())


def not_installed(installed: set[str] | Any, path: Path | None = None) -> tuple[CatalogPack, ...]:
    """Gói trong danh mục mà bản cài này CHƯA có mã (khoá không nằm trong ``installed`` = ``registry.PACKS``)."""
    return tuple(e for e in load(path) if e.key not in installed)
