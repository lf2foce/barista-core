"""BỀ MẶT CÔNG BỐ CHO GÓI (0130 lát 1): gói chỉ import ``barista.api`` (+ module của gói mình / gói nó cần); ``barista.kernel.*`` và các phần
nội bộ khác của lõi (``agent``, ``config``, ``db``, ``presets``, ``workspaces``…) là của lõi — đổi được bất kỳ lúc nào mà không báo gói.

Gói NỀN (``base``, loại ``core``) được miễn: nó đi cùng lõi, cùng phiên bản với lõi (quản trị, quy trình, tài liệu… đọc sổ đăng ký, chạy
thao tác thay người), không phát hành riêng. Ngoài đó chỉ những chỗ dưới đây — mỗi chỗ một lý do; thêm chỗ mới = thêm vào ``barista.api``
trước (bề mặt nhỏ, có phiên bản), miễn là ngoại lệ."""

import ast
import importlib
import importlib.util
import json
from pathlib import Path
from typing import Any, NamedTuple

import pytest

import barista.api as api
from barista.kernel import registry
from barista.kernel.executor import core_ctx
from barista.kernel.principal import elevate
from tests.conftest import Who
from tests.module_src import SRC, module_files, rel

SURFACE = Path(__file__).with_name("api_surface.json")

class Why(NamedTuple):
    """Một ngoại lệ: lý do MỘT dòng, ai chịu trách nhiệm gỡ, và kế hoạch gỡ (điều kiện / bước) — thiếu một trong ba ⇒ test đỏ."""
    reason: str
    owner: str
    plan: str


# (module, phần nội bộ của lõi) ⇒ lý do + người gỡ + kế hoạch. Giữ NHỎ; xoá dòng khi module chuyển sang bề mặt công bố. Thêm dòng mới phải ghi đủ
# ba phần (``test_every_exception_carries_a_reason_owner_and_plan``); số dòng có trần ``ALLOWED_MAX`` — tăng trần là quyết định có chủ đích.
ALLOWED: dict[tuple[str, str], Why] = {
    ("acct", "barista.kernel.ledger"): Why(
        "Tài chính là module DUY NHẤT ghi sổ cái (bất biến 4); gói khác qua acct.issue_receivable / request_remittance, khai tài khoản bằng api.ledger.declare",
        "gói Tài chính", "giữ lâu dài — chỉ gỡ nếu sổ cái thành dịch vụ của bề mặt (api.ledger có ghi bút toán); không có kế hoạch"),
    ("acct", "barista.kernel.registry"): Why(
        "dòng tiền hỏi mọi module khai '<module>.cashflow_lines' bằng cách duyệt sổ thao tác (có trước điểm cắm 0110)",
        "gói Tài chính", "đổi sang điểm cắm acct.cashflow (ctx.plugs) rồi xoá dòng này"),
    ("kpi", "barista.kernel.registry"): Why(
        "bảng xếp hạng là của MỌI chỉ số mọi gói khai (Module.metric) và chỉ số mặc định theo gói (Pack.rank_metric)",
        "gói KPI", "thêm api.metrics.catalog() (danh mục chỉ số + mặc định theo gói) vào bề mặt rồi xoá dòng này"),
    ("kpi", "barista.kernel.packs"): Why(
        "chỉ số mặc định chỉ lấy từ gói công ty đang bật",
        "gói KPI", "cùng bước với dòng trên: catalog trả sẵn theo gói đang bật của công ty"),
    ("meet", "barista.kernel.registry"): Why(
        "cuộc gọi gắn vào đối tượng của gói BẤT KỲ (registry.SUBJECTS: nhãn + thao tác xem bằng quyền người gọi)",
        "gói Gọi video", "thêm api.subjects (nhãn + mở đối tượng bằng quyền người gọi) dùng chung với talk rồi xoá dòng này"),
    ("meet", "barista.config"): Why(
        "máy chủ TURN / STUN và địa chỉ web là cấu hình của BẢN CÀI, không phải dữ liệu công ty",
        "gói Gọi video", "thêm api.install_settings (giá trị cấu hình bản cài gói được đọc, có danh sách) rồi xoá dòng này"),
    ("talk", "barista.kernel.registry"): Why(
        "trao đổi gắn vào đối tượng của gói BẤT KỲ (registry.SUBJECTS) và mở lại thẻ truy vấn trong tin (registry.get)",
        "gói Trao đổi", "cùng bước api.subjects với meet; mở lại thẻ bằng ctx.call thay cho registry.get"),
    ("compose", "barista.agent.chat"): Why(
        "danh sách truy vấn người soạn chạy được (catalog), lược đồ công cụ và bản rút gọn trang cho model",
        "gói Soạn với AI", "thêm api.assistant (danh mục thao tác người gọi chạy được + lược đồ công cụ + rút gọn trang) vào bề mặt rồi xoá dòng này"),
    ("compose", "barista.agent.toolset"): Why(
        "tìm truy vấn hợp nhu cầu số liệu theo nghĩa (retrieve_for)",
        "gói Soạn với AI", "cùng bước api.assistant"),
    ("compose", "barista.agent.llm"): Why(
        "cửa duy nhất có model: công tắc + hạn mức AI của công ty (model_for)",
        "gói Soạn với AI", "dùng api.ai như training (0200: model theo công tắc AI + ghi lượt dùng)"),
    ("compose", "barista.agent.usage"): Why(
        "ghi lượt dùng AI vào hạn mức của công ty",
        "gói Soạn với AI", "dùng api.ai như training"),
    ("compose", "barista.agent.reliability"): Why(
        "xếp loại lỗi nhà cung cấp model ⇒ câu cho người dùng (cùng chính sách thử lại / dự phòng với chat — runbook 15)",
        "gói Soạn với AI", "dùng api.ai như training (model + chính sách lỗi)"),
    ("compose", "barista.kernel.executor"): Why(
        "mỗi công cụ là một executor.query riêng (không giữ giao dịch qua lượt model); kết quả ghi bằng executor.run lệnh nội bộ; jsonable",
        "gói Soạn với AI", "api.web.query / run chỉ nhận người gọi cấp ở cửa HTTP — mở thêm cho việc chạy sau commit của chính người soạn rồi xoá dòng này"),
    ("compose", "barista.kernel.principal"): Why(
        "kiểu người gọi của việc chạy sau commit + elevate cho lệnh nội bộ ghi kết quả của chính người soạn",
        "gói Soạn với AI", "cùng bước với dòng executor"),
    ("compose", "barista.kernel.errors"): Why(
        "OpError — lỗi thao tác gộp (Invalid / NotFound / Denied) khi công cụ / xuất tệp lỗi",
        "gói Soạn với AI", "thêm OpError vào barista.api rồi xoá dòng này"),
}
ALLOWED_MAX = 15   # trần: chỉ giảm (−1 19/11: Soạn với AI dùng api.convert; 9 sau khi ba gói rời lõi + 9 của gói Soạn với AI nay là gói ngoài nền; −2 training dùng api.ai + Prep — 0200). Thêm ngoại lệ ⇒ tăng trần CÓ CHỦ ĐÍCH cùng commit, kèm lý do / người gỡ / kế hoạch

def test_every_exception_carries_a_reason_owner_and_plan() -> None:
    """Ngoại lệ nhập nội bộ lõi là NỢ có chủ: mỗi dòng có lý do một dòng, người gỡ và kế hoạch gỡ; không thêm dòng lặng lẽ."""
    bad = [f"{k}: {field}" for k, why in ALLOWED.items() for field in Why._fields
           if not isinstance(getattr(why, field), str) or not getattr(why, field).strip() or "\n" in getattr(why, field)]
    assert not bad, "ngoại lệ thiếu lý do / người gỡ / kế hoạch (một dòng mỗi phần): " + ", ".join(bad)
    assert len(ALLOWED) <= ALLOWED_MAX, f"thêm ngoại lệ ({len(ALLOWED)} > {ALLOWED_MAX}) — dùng barista.api; bất đắc dĩ thì tăng trần có chủ đích"


def _imports(path: Path) -> set[str]:
    """Mọi module ``barista.*`` một file import (cả import trong hàm); ``from barista.kernel import ledger`` ⇒ ``barista.kernel.ledger``."""
    out: set[str] = set()
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and (n.module or n.level):
            base = n.module or ""
            if n.level:   # tương đối (``from ..kernel import db``) ⇒ tính tên đầy đủ theo package chứa file
                pkg = _package(path)
                up = pkg[: len(pkg) - (n.level - 1)] if n.level > 1 else pkg
                base = ".".join([*up, *([base] if base else [])])
            for a in n.names:
                full = f"{base}.{a.name}"
                try:
                    importlib.import_module(full)
                    out.add(full)
                except ImportError:
                    out.add(base)
    return {m for m in out if m == "barista" or m.startswith("barista.")}


def _package(path: Path) -> list[str]:
    """Package chứa file (đi lên chừng nào còn ``__init__.py``): ``src/barista/modules/crm/leads.py`` ⇒ ``["barista", "modules", "crm"]``."""
    parts: list[str] = []
    d = path.resolve().parent
    while (d / "__init__.py").exists():
        parts.insert(0, d.name)
        d = d.parent
    return parts


def _internal(name: str) -> bool:
    return not (name == "barista.api" or name.startswith(("barista.api.", "barista.modules")))


def test_pack_modules_import_only_the_published_surface() -> None:
    bad, used = [], set()
    for mod, files in module_files().items():
        if registry.pack_kind(registry.MODULE_PACK[mod]) == "core":
            continue
        for p in files:
            for name in sorted(filter(_internal, _imports(p))):
                if (mod, name) in ALLOWED:
                    used.add((mod, name))
                else:
                    bad.append(f"{rel(p)}: import {name} — dùng barista.api (thiếu thì thêm vào bề mặt + tăng phiên bản)")
    assert not bad, "\n".join(bad)
    assert used == set(ALLOWED), f"miễn trừ không còn dùng — xoá: {set(ALLOWED) - used}"


def test_the_surface_is_versioned_and_changes_only_on_purpose() -> None:
    """Đổi bề mặt (thêm / bỏ tên) phải đi kèm đổi phiên bản semver ở ``barista.api`` VÀ ở ``tests/api_surface.json`` — một lần cố ý."""
    pinned = json.loads(SURFACE.read_text())
    subs = {n: sorted(getattr(api, n).__all__) for n in api.__all__ if getattr(getattr(api, n), "__name__", "").startswith("barista.api.")}
    assert sorted(api.__all__) == pinned["names"] and subs == pinned["modules"], "bề mặt đổi — sửa tests/api_surface.json + tăng __version__"
    assert api.__version__ == pinned["version"]
    major, minor, patch = (int(x) for x in api.__version__.split("."))
    assert major >= 1 and minor >= 0 and patch >= 0
    for name in api.__all__:
        assert getattr(api, name) is not None, name
    for sub, names in subs.items():   # module con chỉ mang đúng phần công bố, mỗi tên là CHÍNH đối tượng của lõi (không bọc)
        mod = getattr(api, sub)
        assert sorted(n for n in vars(mod) if not n.startswith("_")) == names, sub
        for n in names:
            home = f"barista.{sub}" if importlib.util.find_spec(f"barista.kernel.{sub}") is None else f"barista.kernel.{sub}"   # năng lực lớn có package riêng (docread — 0232)
            origin = importlib.import_module(home)
            assert getattr(mod, n) is getattr(origin, n), (sub, n)


DEPRECATION_DAYS = 90   # tên sắp bỏ còn chạy ít nhất từng này ngày kể từ lúc đánh dấu (ADR 0247, runbook 21)


def _deprecation_problems(register: dict[str, Any], names: set[str], version: str) -> list[str]:
    """Sổ ``deprecated`` của ``api_surface.json``: tên còn xuất (chưa bỏ được khi còn trong sổ), đủ trường, giữ ≥ 90 ngày, chỉ bỏ ở bản LỚN kế tiếp."""
    from datetime import date, timedelta

    major = int(version.split(".")[0])
    out: list[str] = []
    for name, d in register.items():
        if not isinstance(d, dict) or set(d) != {"since", "since_day", "use", "remove_after", "remove_in"}:
            out.append(f"{name}: cần đúng các trường since, since_day, use, remove_after, remove_in")
            continue
        if name not in names:
            out.append(f"{name}: đã bỏ khỏi barista.api mà còn trong sổ — bỏ tên chỉ ở bản {d['remove_in']}, rồi xoá dòng sổ cùng lúc")
        if not str(d["use"]).strip():
            out.append(f"{name}: thiếu 'use' (dùng gì thay)")
        try:
            if date.fromisoformat(d["remove_after"]) < date.fromisoformat(d["since_day"]) + timedelta(days=DEPRECATION_DAYS):
                out.append(f"{name}: remove_after phải cách since_day ít nhất {DEPRECATION_DAYS} ngày")
        except (TypeError, ValueError):
            out.append(f"{name}: since_day / remove_after phải dạng YYYY-MM-DD")
        if d["remove_in"] != f"{major + 1}.0.0":
            out.append(f"{name}: remove_in phải là bản lớn kế tiếp {major + 1}.0.0 — trong một bản lớn không bỏ tên")
        if tuple(int(x) for x in str(d["since"]).split(".")) > tuple(int(x) for x in version.split(".")):
            out.append(f"{name}: since {d['since']} sau phiên bản hiện tại {version}")
    return out


def test_deprecated_names_stay_published_for_ninety_days_and_go_only_in_the_next_major() -> None:
    """Luật bỏ tên ở bề mặt (ADR 0247): đánh dấu ở ``api_surface.json`` → ``deprecated`` (``pack.sh check`` báo gói đang dùng), tên vẫn chạy ít nhất
    90 ngày, chỉ bỏ ở bản lớn kế tiếp. Sổ trống là bình thường."""
    pinned = json.loads(SURFACE.read_text())
    assert _deprecation_problems(pinned.get("deprecated", {}), set(api.__all__), api.__version__) == []
    ok = {"since": "1.70.0", "since_day": "2026-11-20", "use": "ledger.read", "remove_after": "2027-02-18", "remove_in": "2.0.0"}
    assert _deprecation_problems({"fold": ok}, {"fold"}, "1.70.0") == []
    assert _deprecation_problems({"fold": ok}, set(), "1.70.0") != []                                     # bỏ trước bản lớn
    assert _deprecation_problems({"fold": {**ok, "remove_after": "2026-12-01"}}, {"fold"}, "1.70.0") != []   # chưa đủ 90 ngày
    assert _deprecation_problems({"fold": {**ok, "remove_in": "1.80.0"}}, {"fold"}, "1.70.0") != []          # bỏ giữa bản lớn


def test_the_core_does_not_depend_on_the_surface() -> None:
    """Bề mặt dựa vào lõi, không ngược lại: kernel / agent / file ráp không import ``barista.api`` (tránh vòng, giữ lõi đổi tự do)."""
    hubs = [*(SRC / "kernel").rglob("*.py"), *(SRC / "agent").rglob("*.py"), SRC / "workspaces.py", SRC / "presets.py"]
    bad = [str(p.relative_to(SRC)) for p in hubs if any(n == "barista.api" or n.startswith("barista.api.") for n in _imports(p))]
    assert not bad, bad


def test_relative_imports_into_the_core_are_caught(tmp_path: Path) -> None:
    """``from ..kernel import db`` trong một module gói cũng là import nội bộ lõi."""
    mod = tmp_path / "barista" / "modules" / "fake"
    mod.mkdir(parents=True)
    for d in (tmp_path / "barista", tmp_path / "barista" / "modules", mod):
        (d / "__init__.py").write_text("")
    (mod / "parts.py").write_text("from ...kernel import ledger\nfrom ... import config\nfrom . import other\nfrom ...api import ui\n")
    found = _imports(mod / "parts.py")
    assert {"barista.kernel.ledger", "barista.config"} <= found
    assert {n for n in found if _internal(n)} == {"barista.kernel.ledger", "barista.config"}


async def test_a_pack_cannot_forge_a_principal_for_call_as(who: Who) -> None:
    """``barista.api.Principal`` chỉ là KIỂU (không dựng được); ``ctx.call_as`` / ``call_as_optional`` chỉ nhận ``ctx.p`` hoặc người gọi lõi cấp
    (``ctx.principal_of``, trợ lý làm thay) — bản sửa ``dataclasses.replace(ctx.p, system=True)`` bị từ chối, kể cả ở bản tuỳ chọn (không nuốt
    thành None)."""
    import dataclasses
    import uuid

    import pytest

    from barista.db import db

    with pytest.raises(TypeError):
        api.Principal(tenant_id=uuid.uuid4(), user_id=None)   # type: ignore[operator]
    sale = await who("sale1")
    doc = str(uuid.uuid4())
    async with db.tenant_tx(sale, rollback=True) as conn:
        ctx = core_ctx(conn, sale, module="flow")
        forged = dataclasses.replace(sale, system=True)
        for call in (ctx.call_as, ctx.call_as_optional):
            with pytest.raises(TypeError, match=r"ctx\.principal_of"):
                await call("kb.readable", forged, document_id=doc)
        loaded = await ctx.principal_of(sale.user_id) if sale.user_id else None
        assert loaded is not None
        for p in (sale, loaded, loaded.as_agent()):
            assert await ctx.call_as("kb.readable", p, document_id=doc) is False


async def _in_handler(monkeypatch: pytest.MonkeyPatch, who_runs: object, body: object) -> dict[str, object]:
    """Chạy ``body(ctx, out)`` BÊN TRONG một handler thật (truy vấn ``core.notifications`` bị vá tạm) — như mã của một gói đang chạy."""
    import dataclasses

    from barista.kernel import executor

    op = registry.OPS["core.notifications"]
    out: dict[str, object] = {}

    async def probe(ctx: Any, inp: Any) -> Any:
        await body(ctx, out)   # type: ignore[operator]
        return await op.handler(ctx, inp)

    monkeypatch.setitem(registry.OPS, "core.notifications", dataclasses.replace(op, handler=probe))
    r = await executor.query(who_runs, "core.notifications", {})   # type: ignore[arg-type]
    assert r.ok, r.error
    return out


async def test_a_pack_route_cannot_run_the_executor_with_a_forged_principal(who: Who) -> None:
    """``barista.api.web.run`` / ``query`` (route của gói) chỉ nhận người gọi cấp ở CỬA HTTP — bản ``replace(…, system=True, tenant_id=…)``, quyền hệ
    thống lõi dựng, người lõi nạp từ DB đều bị từ chối TRƯỚC khi tới executor; người gọi của request vẫn chạy được."""
    import dataclasses

    from barista.kernel import principal

    sale, other = await who("sale1"), await who("admin", other=True)
    for bad in (dataclasses.replace(sale, system=True), dataclasses.replace(sale, system=True, tenant_id=other.tenant_id), elevate(sale), sale):
        with pytest.raises(TypeError, match="cửa HTTP"):
            await api.web.run(bad, "core.pin", {"workspace": "x", "key": "y"})
        with pytest.raises(TypeError, match="cửa HTTP"):
            await api.web.query(bad, "core.notifications", {})
    lease = principal.Lease()                                                     # như http_api mở cho một request
    edge = principal.issued(dataclasses.replace(sale), kind=principal.EDGE, lease=lease)   # như web.current cấp cho request đó
    assert (await api.web.query(edge, "core.notifications", {})).ok
    lease.active = False                                                          # request xong ⇒ giữ lại dùng sau bị từ chối
    with pytest.raises(TypeError, match="cửa HTTP"):
        await api.web.query(edge, "core.notifications", {})


async def test_a_pack_cannot_build_or_alter_a_ctx(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-review 08/11 (Claude Opus 5.5): gói từng dựng ``Ctx`` (giả module có trong ``callers=``, giả quyền hệ thống), kể cả trong ngữ cảnh MỚI
    (``asyncio.create_task(..., context=Context())``, luồng mới) nơi biến "module đang chạy" rỗng. Giờ đóng khi không rõ: ``Ctx`` chỉ dựng qua
    ``executor.core_ctx`` của lõi (cờ lõi giữ, ngữ cảnh mới không có); ``p`` / ``module`` / ``caller`` cố định từ lúc dựng, gọi lồng kiểm lại."""
    import asyncio
    import contextvars
    import dataclasses
    import threading

    from barista.db import db
    from barista.kernel.executor import Ctx

    sale, gd = await who("sale1"), await who("giamdoc")
    with pytest.raises(TypeError):
        api.Ctx(None, sale)   # type: ignore[operator]
    async with db.tenant_tx(gd, rollback=True) as conn:
        with pytest.raises(TypeError, match="core_ctx"):
            Ctx(conn, gd, module="flow")
        with pytest.raises(TypeError, match="core_ctx"):
            contextvars.Context().run(Ctx, conn, gd, module="flow")
        with pytest.raises(TypeError, match="lõi cấp"):
            core_ctx(conn, dataclasses.replace(sale, system=True), module="flow")
        mine = core_ctx(conn, gd, module="mkt")
        for field_, value in (("module", "flow"), ("p", elevate(gd)), ("caller", "flow")):
            with pytest.raises(TypeError, match="cố định"):
                setattr(mine, field_, value)
        with pytest.raises(TypeError, match="core_ctx"):
            dataclasses.replace(mine, module="flow")
        object.__setattr__(mine, "module", "flow")              # lách cả __setattr__ ⇒ gọi lồng vẫn thấy Ctx đã bị sửa
        with pytest.raises(TypeError, match="bị sửa"):
            await mine.call_system("flow.plan_tasks", plan_ids=[])
        assert await core_ctx(conn, gd, module="flow").call_system("flow.plan_tasks", plan_ids=[]) == []   # đường thật

    async def body(ctx: Any, out: dict[str, object]) -> None:
        async def forged() -> object:
            return await type(ctx)(ctx.conn, ctx.p, module="flow").call_system("flow.plan_tasks", plan_ids=[])
        for name, run in (("same", lambda: forged()),
                          ("fresh_task", lambda: asyncio.create_task(forged(), context=contextvars.Context()))):
            try:
                out[name] = await run()
            except TypeError as e:
                out[name] = str(e)
        box: list[object] = []

        def thread() -> None:
            try:
                box.append(type(ctx)(ctx.conn, ctx.p, module="flow"))
            except TypeError as e:
                box.append(str(e))
        t = threading.Thread(target=thread)
        t.start()
        t.join()
        out["thread"] = box[0]

    out = await _in_handler(monkeypatch, sale, body)
    assert all(isinstance(v, str) and "core_ctx" in v for v in out.values()), out


async def test_pack_code_cannot_mint_a_system_principal(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-review 08/11: ``ctx.p.as_system()`` từng ra người gọi hệ thống "lõi cấp" (kể cả gọi trong ``Context()`` rỗng / luồng mới) ⇒ thao tác nội bộ
    bằng quyền hệ thống, bỏ qua ``callers=``. Giờ không có ``as_system``: chỉ lõi nâng quyền (``elevate`` sau khi kiểm hợp đồng — ``ctx.call_system``);
    ``as_agent()`` không rửa bản tự dựng; quyền hệ thống lõi dựng cũng không qua được route (``web.*`` chỉ nhận người gọi cửa HTTP)."""
    import dataclasses
    import uuid

    from barista.kernel import principal

    sale = await who("sale1")
    assert not hasattr(sale, "as_system") and not hasattr(principal.Principal, "as_system")
    laundered = dataclasses.replace(sale, system=True).as_agent()
    assert not principal.is_issued(laundered)
    doc = str(uuid.uuid4())

    async def body(ctx: Any, out: dict[str, object]) -> None:
        for name, attempt in (("call_as", lambda: ctx.call_as("core.ai_runs", laundered, prefix="", since="2020-01-01T00:00:00Z")),
                              ("web", lambda: api.web.query(laundered, "kb.readable", {"document_id": doc}))):
            try:
                await attempt()
                out[name] = "ran"
            except TypeError as e:
                out[name] = str(e)

    out = await _in_handler(monkeypatch, sale, body)
    assert "principal_of" in str(out["call_as"]) and "cửa HTTP" in str(out["web"]), out


async def test_principal_of_is_only_for_call_as(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ctx.principal_of(người khác)`` chỉ để hỏi hộ bằng ``call_as`` (RLS người đó) — cửa executor và route từ chối, nên handler không "làm thay"
    giám đốc; lượt của trợ lý nạp lại chính mình vẫn là trợ lý."""
    import uuid

    from barista.kernel import executor

    sale, gd = await who("sale1"), await who("giamdoc")
    doc = str(uuid.uuid4())

    async def body(ctx: Any, out: dict[str, object]) -> None:
        boss = await ctx.principal_of(gd.user_id)
        for name, attempt in (("web", lambda: api.web.query(boss, "core.ai_usage", {})),
                              ("executor", lambda: executor.query(boss, "core.ai_usage", {}))):
            try:
                await attempt()
                out[name] = "ran"
            except TypeError as e:
                out[name] = str(e)
        out["call_as"] = await ctx.call_as("kb.readable", boss, document_id=doc)
        me = await ctx.principal_of(ctx.p.user_id)
        out["via"] = (ctx.p.via, me.via)

    out = await _in_handler(monkeypatch, sale.as_agent(), body)
    assert "cửa HTTP" in str(out["web"]) and "principal_of" in str(out["executor"]), out
    assert out["call_as"] is False and out["via"] == ("agent", "agent")


# ── Review lần 3 (08/11, Claude Opus 5.5): QUYỀN NẰM Ở SỔ RIÊNG CỦA LÕI, ĐỐI TƯỢNG CHỈ LÀ TAY CẦM — mỗi đầu dò của reviewer thành một test ──

async def test_authority_lives_in_the_core_record_not_on_the_principal(who: Who) -> None:
    """Chép (copy / deepcopy / replace / pickle / lớp con) không mang theo quyền; sửa tại chỗ một người gọi ĐÃ cấp (``object.__setattr__`` lên
    ``system`` / ``tenant_id``, đổi ``grants``) làm nó không còn khớp bản chụp của lõi ⇒ mọi cửa từ chối."""
    import copy
    import dataclasses
    import pickle

    from barista.kernel import principal
    from barista.kernel.principal import Grant

    sale = await who("sale1")
    for made in (copy.copy(sale), copy.deepcopy(sale), dataclasses.replace(sale), pickle.loads(pickle.dumps(sale))):
        assert principal.kind_of(made) is None

    class Sub(principal.Principal):
        pass

    assert principal.kind_of(Sub(**{f.name: getattr(sale, f.name) for f in dataclasses.fields(sale)})) is None
    for field_, value in (("system", True), ("tenant_id", (await who("admin", other=True)).tenant_id)):
        lease = principal.Lease()
        tgt = principal.issued(dataclasses.replace(sale), kind=principal.EDGE, lease=lease)
        assert principal.live_edge(tgt)
        object.__setattr__(tgt, field_, value)
        assert principal.kind_of(tgt) is None
        with pytest.raises(TypeError, match="cửa HTTP"):
            await api.web.query(tgt, "core.ai_usage", {})
    tgt = principal.issued(dataclasses.replace(sale, grants=dict(sale.grants)))
    tgt.grants["core.admin"] = Grant("all", frozenset({"read", "write", "manage"}))   # type: ignore[index]
    assert principal.kind_of(tgt) is None


async def test_mutating_ctx_p_in_place_is_refused(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    from barista.kernel import principal

    sale = await who("sale1")

    async def body(ctx: Any, out: dict[str, object]) -> None:
        object.__setattr__(ctx.p, "system", True)
        try:
            out["kind"] = principal.kind_of(ctx.p)
            for name, attempt in (("call", lambda: ctx.call("flow.plan_tasks", plan_ids=[])),
                                  ("web", lambda: api.web.query(ctx.p, "core.ai_usage", {}))):
                try:
                    await attempt()
                    out[name] = "ran"
                except TypeError as e:
                    out[name] = str(e)
        finally:
            object.__setattr__(ctx.p, "system", False)

    out = await _in_handler(monkeypatch, sale, body)
    assert out["kind"] is None and "bị sửa" in str(out["call"]) and "cửa HTTP" in str(out["web"]), out


async def test_the_ctx_record_lives_in_the_core(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sửa module của Ctx bằng mọi đường (``object.__setattr__``, ``__dict__``, ``copy.copy`` + ``__dict__.update``) không giả được module có trong
    ``callers=``: lõi đọc module / người gọi từ sổ của nó."""
    import copy

    sale = await who("sale1")

    async def body(ctx: Any, out: dict[str, object]) -> None:
        async def attempt(name: str, c: Any) -> None:
            try:
                await c.call_system("flow.plan_tasks", plan_ids=[])
                out[name] = "ran"
            except (TypeError, Exception) as e:
                out[name] = f"{type(e).__name__}: {e}"
        await attempt("control", ctx)                          # module thật (core) không có trong callers=
        c2 = copy.copy(ctx)
        c2.__dict__.update(module="flow")
        await attempt("copy_dict", c2)
        object.__setattr__(ctx, "module", "flow")
        await attempt("setattr", ctx)
        object.__setattr__(ctx, "module", "core")
        ctx.__dict__["caller"] = "flow"
        await attempt("dict_caller", ctx)
        ctx.__dict__["caller"] = ""

    out = await _in_handler(monkeypatch, sale, body)
    assert str(out["control"]).startswith("Denied") and "hợp đồng" in str(out["control"]), out
    assert all(str(out[k]).startswith("TypeError") and "bị sửa" in str(out[k]) for k in ("copy_dict", "setattr", "dict_caller")), out


async def test_principal_of_cannot_run_ops_as_the_director(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """``call_as(op, principal_of(giám đốc))`` từ mã chạy bằng quyền một người thường: chỉ truy vấn NỘI BỘ (hỏi hộ); truy vấn công khai / lệnh bị từ
    chối — không thành đường làm thay giám đốc."""
    import uuid

    sale, gd = await who("sale1"), await who("giamdoc")

    async def body(ctx: Any, out: dict[str, object]) -> None:
        boss = await ctx.principal_of(gd.user_id)
        for name, op, inp in (("public_query", "core.ai_usage", {}),
                              ("command", "flow.save_automation", {"name": "x", "trigger": "nope", "action": "notify", "message": "m"})):
            try:
                await ctx.call_as(op, boss, **inp)
                out[name] = "ran"
            except Exception as e:
                out[name] = f"{type(e).__name__}: {e}"
        out["internal_query"] = await ctx.call_as("kb.readable", boss, document_id=str(uuid.uuid4()))

    out = await _in_handler(monkeypatch, sale, body)
    assert all(str(out[k]).startswith("Denied") and "hỏi hộ" in str(out[k]) for k in ("public_query", "command")), out
    assert out["internal_query"] is False


async def test_kept_principals_expire_with_their_request_and_never_cross_tenants(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gói giữ ``ctx.p`` của một request (biến toàn cục) rồi dùng sau: request đã xong ⇒ ``web.*`` từ chối; ``call_as`` bằng người gọi của công ty
    khác (hay bất kỳ người gọi giữ lại nào không phải ``ctx.p`` / ``principal_of``) ⇒ từ chối, không đổi phiên DB sang công ty kia."""
    import dataclasses

    from barista.kernel import principal

    sale, gd, other = await who("sale1"), await who("giamdoc"), await who("admin", other=True)
    stash: dict[str, Any] = {}
    leases = {"gd": principal.Lease(), "other": principal.Lease()}

    async def grab(key: str) -> Any:
        async def body(ctx: Any, out: dict[str, object]) -> None:
            stash[key] = ctx.p
        return body

    await _in_handler(monkeypatch, principal.issued(dataclasses.replace(gd), kind=principal.EDGE, lease=leases["gd"]), await grab("gd"))
    await _in_handler(monkeypatch, principal.issued(dataclasses.replace(other), kind=principal.EDGE, lease=leases["other"]), await grab("other"))
    for lease in leases.values():
        lease.active = False                                   # hai request đó đã xong
    with pytest.raises(TypeError, match="cửa HTTP"):
        await api.web.query(stash["gd"], "core.ai_usage", {})

    async def body(ctx: Any, out: dict[str, object]) -> None:
        for name, who_ in (("other_tenant", stash["other"]), ("kept_gd", stash["gd"])):
            try:
                await ctx.call_as("kb.readable", who_, document_id="00000000-0000-0000-0000-000000000000")
                out[name] = "ran"
            except TypeError as e:
                out[name] = str(e)

    out = await _in_handler(monkeypatch, sale, body)
    assert all("ctx.p" in str(v) for k, v in out.items() if k in ("other_tenant", "kept_gd")), out


async def test_system_listener_plain_call_goes_through_the_callers_contract(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bên nghe sự kiện chạy bằng quyền hệ thống: ``ctx.call`` tới thao tác NỘI BỘ của module khác vẫn phải có tên trong ``callers=`` (như
    ``call_system``) — trước đây lọt qua."""
    from barista.kernel import principal

    sale = await who("sale1")

    async def body(ctx: Any, out: dict[str, object]) -> None:
        try:
            await ctx.call("flow.plan_tasks", plan_ids=[])
            out["call"] = "ran"
        except Exception as e:
            out["call"] = f"{type(e).__name__}: {e}"

    out = await _in_handler(monkeypatch, principal.system_principal(sale.tenant_id), body)
    assert str(out["call"]).startswith("Denied") and "hợp đồng" in str(out["call"]), out


# ── Review lần 4 (08/11, Claude Opus 5.5) — trong mô hình đe doạ 0130 / 0144: lỗi TIỆN TAY của tác giả gói làm sai người chịu trách nhiệm ──

async def test_delegates_never_run_commands_and_decisions_come_only_from_a_live_edge(who: Who) -> None:
    """Đầu dò B: lệnh xin duyệt có CĂN CỨ do gói viết; căn cứ chạy bằng quyền hệ thống ⇒ từng gọi được ``call_as("core.decide", principal_of(giám
    đốc))`` và nhật ký ghi giám đốc đã duyệt. Giờ: căn cứ chỉ đọc; quyền hỏi hộ (DELEGATE) không bao giờ chạy lệnh; quyết định duyệt không gọi lồng
    được và ở cửa executor chỉ nhận tay cầm EDGE còn hạn; việc nền làm lệnh bằng người gọi "việc" (``job_for``), không quyết định duyệt."""
    import dataclasses
    import uuid

    from barista.db import db
    from barista.kernel import executor, principal
    from tests.conftest import edge

    sale, gd = await who("sale1"), await who("giamdoc")
    aid = str(uuid.uuid4())
    seen: dict[str, str] = {}

    async def evil_basis(ctx: Any, model: Any) -> dict[str, str]:
        boss = await ctx.principal_of(gd.user_id)
        for name, attempt in (("decide", lambda: ctx.call_as("core.decide", boss, approval_id=aid, approve=True)),
                              ("command", lambda: ctx.call("flow.create_task", title="x")),
                              ("job", lambda: ctx.job_for(gd.user_id))):
            try:
                await attempt()
                seen[name] = "ran"
            except Exception as e:
                seen[name] = f"{type(e).__name__}: {e}"
        return {"v": "1"}

    op = dataclasses.replace(registry.OPS["flow.plan_tasks"], basis=evil_basis)
    async with db.tenant_tx(sale, rollback=True) as conn:
        assert await executor.basis_of(core_ctx(conn, sale, module="flow"), op, {"plan_ids": []}) == {"v": "1"}
    assert str(seen["decide"]).startswith("Denied") and "người bấm" in seen["decide"], seen
    assert str(seen["command"]).startswith("Denied") and "chỉ ĐỌC" in seen["command"], seen
    assert str(seen["job"]).startswith("TypeError"), seen

    gid = gd.user_id
    assert gid is not None
    sysp = principal.system_principal(sale.tenant_id)
    run_lease = principal.Lease()                            # lượt việc nền (events._deliver / vòng quét dựng)
    async with db.tenant_tx(sysp, rollback=True) as conn:
        ctx = core_ctx(conn, sysp, module="mkt", background=run_lease)   # như bên nghe sự kiện của một gói
        boss = await ctx.principal_of(gid)
        assert boss is not None
        with pytest.raises(Exception, match="không chạy lệnh"):
            await ctx.call_as("flow.create_task", boss, title="Việc giả danh giám đốc (test)")
        job = await ctx.job_for(gid)
        assert job is not None and principal.kind_of(job) == principal.JOB
        with pytest.raises(Exception, match="người bấm"):
            await ctx.call_as("core.decide", job, approval_id=aid, approve=True)
        assert (await ctx.call_as("flow.create_task", job, title="Việc nền làm bằng quyền người phụ trách (test)"))["task_id"]
        trace = await conn.execute("SELECT user_id, trace FROM core.audit_log WHERE command = 'flow.create_task' ORDER BY id DESC LIMIT 1")
        row = await trace.fetchone()
        assert row is not None and row["user_id"] == gid and row["trace"].startswith("job:mkt")   # người + việc nền + (người khởi đầu)
        run_lease.active = False                             # lượt việc nền xong ⇒ tay cầm "việc" giữ lại hết dùng được
        with pytest.raises(TypeError, match="đúng lượt"):
            await ctx.call_as("flow.create_task", job, title="Dùng lại tay cầm cũ (test)")
    async with db.tenant_tx(sale, rollback=True) as conn:
        with pytest.raises(TypeError, match="việc nền"):
            await core_ctx(conn, sale, module="mkt").job_for(gid)   # Ctx người dùng không làm thay ai

    r = await executor.run(gd, "core.decide", {"approval_id": aid, "approve": True})
    assert r.status == "denied" and "người bấm" in r.error              # tay cầm lõi nạp (CORE) không quyết định duyệt
    assert (await executor.run(edge(gd), "core.decide", {"approval_id": aid, "approve": True})).status == "not_found"   # người bấm thật: tới phiếu


async def test_request_approval_checks_the_callers_contract_when_asking(who: Who) -> None:
    """Xin duyệt một lệnh NỘI BỘ của module khác mà module mình không có trong ``callers=`` ⇒ từ chối lúc xin (lúc chạy lại là đường riêng của lõi)."""
    from barista.db import db

    kt = await who("ketoan")
    target = next(o for o in registry.OPS.values() if o.kind == "command" and o.internal and o.module != "mkt" and "mkt" not in o.callers)
    async with db.tenant_tx(kt, rollback=True) as conn:
        with pytest.raises(Exception, match="hợp đồng quyền hệ thống"):
            await core_ctx(conn, kt, module="mkt").request_approval(chain="remittance", command=target.name, input={}, summary="x")


def test_module_keys_are_validated_and_listeners_carry_their_module_and_pack() -> None:
    """Đầu dò C: ``Module("flow.zz", pack="evilpack")`` từng có bên nghe "flow.zz.grab" ⇒ lõi tách chuỗi ra module "flow" ⇒ qua ``callers=("flow",)``
    và được giao sự kiện dù gói không có. Giờ khoá module / gói chỉ chữ thường_gạch_dưới; bên nghe GHI RÕ module + gói lúc khai; module lạ ⇒ không bật."""
    from barista.kernel import packs

    for key, pack in (("flow.zz", "evilpack"), ("", "base"), ("Flow", "base"), ("ok_mod", "evil.pack"), ("flow zz", "base")):
        with pytest.raises(ValueError, match="khoá"):
            api.Module(key, "x", pack=pack)
    assert set(registry.MODULES) - {"core"} and all(registry.KEY.fullmatch(m) for m in registry.MODULES)
    for sub in registry.SUBSCRIBERS.values():
        assert sub.module in registry.MODULES and sub.pack == registry.MODULE_PACK[sub.module], sub.name
    on = frozenset({"base"})
    assert not packs.module_on(on, "khong_co_module_nay")
    any_sub = next(iter(registry.SUBSCRIBERS.values()))
    assert packs.subscriber_on(frozenset({any_sub.pack, *any_sub.needs}), any_sub)
    assert not packs.subscriber_on(frozenset({"base"} - {any_sub.pack}), dataclasses_replace(any_sub, pack="evilpack"))


def dataclasses_replace(obj: Any, **kw: Any) -> Any:
    import dataclasses

    return dataclasses.replace(obj, **kw)


async def test_a_system_ctx_does_not_accept_kept_core_principals(who: Who) -> None:
    """Đầu dò D: trong Ctx quyền hệ thống (bên nghe), một người gọi lõi nạp từ lâu (CORE, giữ lại) từng qua ``call_as``. Giờ chỉ ``ctx.p``,
    ``principal_of`` (hỏi hộ) hoặc ``job_for`` (việc nền)."""
    from barista.db import db
    from barista.kernel import principal

    sale, gd = await who("sale1"), await who("giamdoc")
    sysp = principal.system_principal(sale.tenant_id, sale.user_id)
    async with db.tenant_tx(sysp, rollback=True) as conn:
        with pytest.raises(TypeError, match="principal_of"):
            await core_ctx(conn, sysp, module="mkt").call_as("core.ai_usage", gd)


async def test_records_do_not_survive_id_reuse(who: Who) -> None:
    """Đầu dò E: sổ theo danh tính đối tượng — đối tượng chết, id được dùng lại cho đối tượng khác ⇒ không thừa hưởng bản ghi."""
    import dataclasses

    from barista.kernel import principal

    sale = await who("sale1")
    hits = 0
    for _ in range(2000):
        x = principal.issued(dataclasses.replace(sale))
        i = id(x)
        del x
        y = dataclasses.replace(sale)
        hits += id(y) == i and principal.kind_of(y) is not None
    assert hits == 0


async def test_pack_sql_cannot_switch_tenant_through_session_settings(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Đầu dò A — ĐÃ ĐÓNG (N11b, ADR 0146, mig 057): ``set_config('app.tenant_id', …)`` không còn là thứ RLS tin; claims do lõi ký."""
    sale, other = await who("sale1"), await who("admin", other=True)

    async def body(ctx: Any, out: dict[str, object]) -> None:
        await ctx.exec("SELECT set_config('app.tenant_id', %s, true)", (str(other.tenant_id),))
        out["other_members"] = await ctx.val("SELECT count(*) FROM core.memberships WHERE tenant_id = %s", (other.tenant_id,))
        await ctx.exec("SELECT set_config('app.tenant_id', %s, true)", (str(sale.tenant_id),))

    out = await _in_handler(monkeypatch, sale, body)
    assert out["other_members"] == 0



async def test_job_for_is_only_for_core_marked_background_runs(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Đầu dò P1: yêu cầu của người (sale) → ``call_system`` (Ctx con chạy quyền hệ thống) → ``job_for(giám đốc)`` → lệnh mang tên giám đốc, không
    nhật ký. Giờ ``job_for`` chỉ có trong lượt việc nền LÕI đánh dấu (bên nghe sự kiện, vòng quét) — quyền hệ thống của chuỗi do người khởi đầu không đủ."""
    import dataclasses

    from barista.db import db

    sale, gd = await who("sale1"), await who("giamdoc")
    seen: dict[str, str] = {}

    async def evil(ctx: Any, inp: Any) -> Any:
        try:
            await ctx.job_for(gd.user_id)
            seen["job"] = "ran"
        except TypeError as e:
            seen["job"] = str(e)
        return {"ok": True}

    op = registry.OPS["flow.notify_plan"]
    monkeypatch.setitem(registry.OPS, "flow.notify_plan", dataclasses.replace(op, handler=evil, callers=("mkt", "flow")))
    for p in (sale, sale.as_agent()):
        async with db.tenant_tx(p, rollback=True) as conn:
            await core_ctx(conn, p, module="mkt").call_system("flow.notify_plan", plan_id="00000000-0000-0000-0000-000000000000", title="x", body="x")
        assert "việc nền" in seen.pop("job"), p.via


async def test_a_delegate_ctx_cannot_elevate_or_ask_for_approval(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Đầu dò P2: ``principal_of(giám đốc)`` → ``call_as`` một truy vấn nội bộ → trong đó ``call_system`` (lệnh) / ``request_approval`` từng chạy với
    user_id = giám đốc. Giờ Ctx đang hỏi hộ bằng quyền người khác không nâng quyền hệ thống, không xin duyệt."""
    import dataclasses

    from barista.db import db

    sale, gd = await who("sale1"), await who("giamdoc")
    seen: dict[str, str] = {}

    async def q(ctx: Any, inp: Any) -> Any:
        for name, attempt in (("elevate", lambda: ctx.call_system("flow.notify_plan", plan_id="00000000-0000-0000-0000-000000000000", title="x",
                                                                   body="x")),
                              ("approval", lambda: ctx.request_approval(chain="task_override", command="flow.close_by_override", input={},
                                                                         summary="x"))):
            try:
                await attempt()
                seen[name] = "ran"
            except Exception as e:
                seen[name] = f"{type(e).__name__}: {e}"
        return []

    wl = registry.OPS["flow.workload"]
    monkeypatch.setitem(registry.OPS, "flow.workload", dataclasses.replace(wl, handler=q, callers=("mkt", "flow")))
    async with db.tenant_tx(sale, rollback=True) as conn:
        ctx = core_ctx(conn, sale, module="mkt")
        gid = gd.user_id
        assert gid is not None
        boss = await ctx.principal_of(gid)
        assert boss is not None
        await ctx.call_as("flow.workload", boss, user_ids=[])
    assert all(str(v).startswith("Denied") and "hỏi hộ" in str(v) for v in seen.values()) and len(seen) == 2, seen


async def test_basis_cannot_write_notify_or_emit(who: Who) -> None:
    """Đầu dò P3: căn cứ từng ``ctx.notify`` / ``ctx.exec("UPDATE …")`` được. Giờ Ctx căn cứ chỉ nhận câu SELECT (khoá dòng vẫn được — giữ tới commit),
    chặn thông báo / sự kiện."""
    import dataclasses

    from barista.db import db
    from barista.kernel import executor

    sale, gd = await who("sale1"), await who("giamdoc")
    seen: dict[str, str] = {}

    async def basis(ctx: Any, model: Any) -> dict[str, str]:
        for name, attempt in (("notify", lambda: ctx.notify([gd.user_id], kind="probe", title="x")),
                              ("exec", lambda: ctx.exec("UPDATE core.memberships SET active = active WHERE user_id = %s", (gd.user_id,))),
                              ("one", lambda: ctx.one("UPDATE core.memberships SET active = active WHERE user_id = %s RETURNING 1", (gd.user_id,)))):
            try:
                await attempt()
                seen[name] = "ran"
            except TypeError as e:
                seen[name] = str(e)
        try:
            ctx.emit("probe.x")
            seen["emit"] = "ran"
        except TypeError as e:
            seen["emit"] = str(e)
        seen["select"] = str(await ctx.val("SELECT 1 FROM core.memberships WHERE user_id = %s FOR SHARE", (gd.user_id,)))
        return {"v": "1"}

    op = dataclasses.replace(registry.OPS["flow.plan_tasks"], basis=basis)
    async with db.tenant_tx(sale, rollback=True) as conn:
        await executor.basis_of(core_ctx(conn, sale, module="flow"), op, {"plan_ids": []})
    assert seen.pop("select") == "1" and all("chỉ đọc" in v for v in seen.values()), seen


def test_only_in_tree_core_modules_belong_to_the_base_pack() -> None:
    """Review 09/11 #5: ``Module(pack="base")`` từng nhận cho module bất kỳ (gói nền luôn bật ⇒ một module lạ tự nhận "base" là luôn chạy). Giờ gói nền
    là danh sách đóng trong repo; gói khác phải khai ``Pack(...)`` trước."""
    assert {m for m, p in registry.MODULE_PACK.items() if p == "base"} == registry.BASE_MODULES
    with pytest.raises(ValueError, match="gói nền"):
        api.Module("not_core_mod", "x", pack="base")
    with pytest.raises(ValueError, match="chưa khai"):
        api.Module("orphan_mod", "x", pack="khong_khai_goi")
    assert "not_core_mod" not in registry.MODULES and "orphan_mod" not in registry.MODULES


async def test_a_ctx_built_on_a_recycled_id_is_not_sealed_by_the_dead_ones_record(who: Who) -> None:
    """Ctx cũ chết, ``id()`` cấp lại cho Ctx mới trước khi ``_drop_ctx`` dọn bản ghi cũ (weakref đã chết): Ctx mới VẪN dựng được (từng ra "Ctx.conn cố định
    từ lúc lõi dựng" ở bộ test, 12/11/2026); còn Ctx đang sống thì vẫn bị khoá."""
    import weakref

    from barista.db import db
    from barista.kernel import executor

    gd = await who("giamdoc")
    async with db.tenant_tx(gd, rollback=True) as conn:
        live = core_ctx(conn, gd)
        with pytest.raises(TypeError, match="cố định"):
            live.conn = conn
        tmp = core_ctx(conn, gd)
        ghost = weakref.ref(tmp)
        record = executor._CTXS[id(tmp)]
        del tmp
        assert ghost() is None
        x = object.__new__(executor.Ctx)   # địa chỉ của Ctx mới trùng một bản ghi chết (weakref đã chết) chưa được dọn
        executor._CTXS[id(x)] = (ghost, *record[1:])
        x.conn = conn                      # trước sửa: TypeError "Ctx.conn cố định từ lúc lõi dựng"
        executor._CTXS.pop(id(x), None)
