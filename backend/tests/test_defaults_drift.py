"""Kiểm phần mặc định còn thiếu (``admin.defaults_drift`` + ``barista.defaults_report``): mặc định mới của gói chưa cài ⇒ "chưa cài" (nút cài thêm được);
quyền mở rộng thẩm quyền của gói đã cài ⇒ "chờ chủ tài khoản" (nút cài KHÔNG cài); công ty cố ý thu hẹp / gỡ ⇒ "công ty đổi khác", không đề xuất ghi đè
và nút cài không đụng."""

import uuid
from typing import Any, LiteralString

from barista import defaults_report, presets, seed
from barista.db import db
from barista.kernel import executor
from tests.conftest import ok, owner_val, q

ADMIN = [("admin", "Quản trị", "admin", "QT", True)]


def _cur(**kw: Any) -> presets.Current:
    base: dict[str, Any] = {"logged": set(), "known_packs": set(), "roles": set(), "teams": set(), "grants": {}, "chains": {}, "rules": {},
                            "settings": {}, "vocab": set(), "flows": set()}
    return presets.Current(**{**base, **kw})


def _grant(role: str, res: str, scope: str, actions: str, pack: str = "p") -> presets.Item:
    return presets.Item("grant", f"{role}|{res}", pack, {"role": role, "resource": res, "scope": scope, "actions": actions.split()})


def test_drift_tells_missing_from_held_from_changed_and_ignores_wider() -> None:
    items = [_grant("sale", "a", "own", "read write"), _grant("sale", "b", "all", "read manage"), _grant("sale", "c", "team", "read write"),
             _grant("sale", "d", "own", "read"), _grant("sale", "e", "own", "read"),
             presets.Item("chain", "ch", "p", {"label": "Chuỗi", "steps": [{"role": "director"}]}),
             presets.Item("rule", "x.do", "p", {"chain": "ch"}), presets.Item("role", "clerk", "p", {"label": "Thư ký"})]
    cur = _cur(logged={("grant", "sale|c"), ("grant", "sale|d"), ("rule", "x.do")}, known_packs={"p"},
               grants={("sale", "c"): ("own", {"read", "write"}), ("sale", "e"): ("all", {"read", "write"})})
    got = {g.key: g for g in presets.drift(items, cur, widens=lambda res, acts: res == "b")}
    assert got["sale|a"].status == "missing" and got["sale|a"].important          # chưa từng cài, công ty chưa có ⇒ cài thêm được
    assert got["sale|b"].status == "held" and got["sale|b"].important             # mở rộng thẩm quyền, gói đã cài ⇒ chủ tài khoản cấp
    assert got["sale|c"].status == "changed" and "phạm vi own < mặc định team" in got["sale|c"].detail and not got["sale|c"].important
    assert got["sale|d"].status == "changed" and "đã gỡ" in got["sale|d"].detail   # đã cài rồi công ty gỡ
    assert "sale|e" not in got                                                     # rộng hơn mặc định ⇒ không báo
    assert got["ch"].status == "missing" and got["clerk"].status == "missing"
    assert got["x.do"].status == "changed" and "chạy thẳng" in got["x.do"].detail  # công ty bỏ luật duyệt: quyết định của công ty
    # gói CHƯA từng cài ở công ty: quyền mở rộng thẩm quyền vẫn là "chưa cài" (bật gói lần đầu cài đủ — như install_defaults)
    assert presets.drift([items[1]], _cur(), widens=lambda res, acts: True)[0].status == "missing"


async def _exec(sql: LiteralString, params: Any = None) -> None:
    async with db.owner.connection() as conn:
        await conn.execute(sql, params)


async def _company() -> tuple[Any, Any, str]:
    d = f"{uuid.uuid4().hex[:6]}.drift.test"
    name = f"Công ty kiểm mặc định {d}"
    tid = await seed.create_tenant(name, d, packs=("finance",), people=ADMIN, about="thử kiểm mặc định")
    return tid, await seed.who(tid, "admin", d), name


async def _plan(tid: Any) -> list[presets.Item]:
    roles = set(await _all("SELECT key FROM core.roles WHERE tenant_id = %s", (tid,)))
    on = {"base", *await _all("SELECT pack FROM core.tenant_packs WHERE tenant_id = %s", (tid,))}
    return presets.plan(on, on, roles)


async def _pick(tid: Any, *, widening: bool) -> presets.Item:
    """Một quyền mặc định có ghi / quản lý ĐÃ cài ở công ty (vai có sẵn), mở rộng thẩm quyền tiền / quyền hay không."""
    return next(i for i in await _plan(tid) if i.kind == "grant" and i.pack != "base" and len(i.data["actions"]) > 1
                and presets.widens_authority(i.data["resource"], i.data["actions"]) == widening)


async def _all(sql: LiteralString, params: Any) -> list[Any]:
    async with db.owner.connection() as conn:
        cur = await conn.execute(sql, params)
        return [next(iter(r.values())) for r in await cur.fetchall()]


def _rows(page: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in page["rows"]}


async def test_missing_default_is_reported_and_installed_while_a_deliberate_narrowing_is_only_reported() -> None:
    tid, admin, name = await _company()
    page = await q(admin, "admin.defaults_drift")
    assert not [r for r in page["rows"] if r["status"] == "missing" and r["kind"] in presets.IMPORTANT_KINDS], page["rows"]   # công ty mới: đủ
    assert page["header"][0]["tone"] == "ok"

    # 1) bản cập nhật thêm quyền mặc định sau khi công ty đã bật gói: chưa có dòng quyền, chưa có nhật ký cài
    new = await _pick(tid, widening=False)
    role, res = new.data["role"], new.data["resource"]
    await _exec("DELETE FROM core.grants WHERE tenant_id = %s AND role = %s AND resource = %s", (tid, role, res))
    await _exec("DELETE FROM core.pack_installs WHERE tenant_id = %s AND kind = 'grant' AND key = %s", (tid, new.key))
    # 2) công ty CỐ Ý thu hẹp một quyền mặc định khác (chỉ còn xem)
    narrow = None
    for i in await _plan(tid):
        if i.kind == "grant" and i.key != new.key and len(i.data["actions"]) > 1 and await owner_val(
                "SELECT count(*) FROM core.grants WHERE tenant_id = %s AND role = %s AND resource = %s", (tid, i.data["role"], i.data["resource"])):
            narrow = i
            break
    assert narrow is not None
    await _exec("UPDATE core.grants SET actions = '{read}' WHERE tenant_id = %s AND role = %s AND resource = %s",
                (tid, narrow.data["role"], narrow.data["resource"]))
    # 3) quyền MỞ RỘNG thẩm quyền mới của gói đã cài
    wide = await _pick(tid, widening=True)
    await _exec("DELETE FROM core.grants WHERE tenant_id = %s AND role = %s AND resource = %s", (tid, wide.data["role"], wide.data["resource"]))
    await _exec("DELETE FROM core.pack_installs WHERE tenant_id = %s AND kind = 'grant' AND key = %s", (tid, wide.key))

    page = await q(admin, "admin.defaults_drift")
    rows = _rows(page)
    assert rows[f"grant|{new.key}"]["status"] == "missing" and rows[f"grant|{new.key}"]["important"]
    assert rows[f"grant|{narrow.key}"]["status"] == "changed" and "thiếu" in rows[f"grant|{narrow.key}"]["detail"]
    assert not rows[f"grant|{narrow.key}"]["important"]
    assert rows[f"grant|{wide.key}"]["status"] == "held"
    assert page["header"][0]["tone"] == "warn" and page["header"][0]["actions"][0]["command"] == "admin.install_missing_defaults"

    # bước kiểm của phát hành đọc đúng truy vấn này (quyền hệ thống, giao dịch chỉ đọc)
    report = await defaults_report.collect(name)
    assert [c["tenant"] for c in report] == [name]
    assert defaults_report.important(report) >= 2 and "!" in defaults_report.render(report)

    # trợ lý không cài thay; người bấm cài thêm phần thiếu — không ghi đè chỗ công ty thu hẹp, không cài lặng lẽ quyền mở rộng thẩm quyền
    r = await executor.run(admin.as_agent(), "admin.install_missing_defaults", {})
    assert r.status == "denied", (r.status, r.error)
    out = await ok(admin, "admin.install_missing_defaults")
    assert "quyền" in out["summary"] and out["held"], out
    have = await _all("SELECT actions FROM core.grants WHERE tenant_id = %s AND role = %s AND resource = %s", (tid, role, res))
    assert have and set(have[0]) >= set(new.data["actions"])
    assert await _all("SELECT actions FROM core.grants WHERE tenant_id = %s AND role = %s AND resource = %s",
                      (tid, narrow.data["role"], narrow.data["resource"])) == [["read"]]
    assert not await _all("SELECT 1 FROM core.grants WHERE tenant_id = %s AND role = %s AND resource = %s", (tid, wide.data["role"], wide.data["resource"]))
    rows = _rows(await q(admin, "admin.defaults_drift"))
    assert f"grant|{new.key}" not in rows
    assert rows[f"grant|{narrow.key}"]["status"] == "changed" and rows[f"grant|{wide.key}"]["status"] == "held"
