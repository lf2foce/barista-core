"""Cửa nhà vận hành (ADR 0206): đặt quyền / tạo vai / cài app / quyết phiếu đổi quyền bằng ``python -m barista.operator`` — qua executor bằng quyền hệ
thống, bắt buộc người vận hành + lý do, không hiện ở nhật ký của khách, không có đường HTTP / trợ lý / vai của khách; app ≤ vai bảo lãnh; khoá in một
lần, không vào nhật ký; quyết phiếu chạy lệnh bằng quyền người xin; chạy thử không ghi gì."""

import argparse
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest

from barista import operator, seed
from barista.config import settings
from barista.db import db
from barista.http_api import app
from barista.kernel import executor, registry, remote_apps
from barista.kernel.principal import system_principal
from tests.conftest import owner_val

PEOPLE = [("giamdoc", "Giám đốc thử", "director", "HQ", True), ("admin", "Quản trị thử", "admin", "QT", True),
          ("nv", "Nhân viên thử", "staff", "KD-A", False)]
OP = {"by": "Người vận hành thử", "why": "thiết lập ban đầu (test)"}
SRC = Path(__file__).resolve().parents[1] / "src" / "barista"


@pytest.fixture
async def co(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(settings, "apps_signing_secret", "test-signing-secret")
    d = f"op{uuid.uuid4().hex[:8]}.demo"
    tid = await seed.create_tenant(f"Công ty cửa vận hành {d}", d, people=PEOPLE, about="công ty thử (test)")
    return {"tid": tid, "d": d, **{k: await seed.who(tid, k, d) for k, *_ in PEOPLE}}


async def _op(tid: UUID, command: str, /, dry_run: bool = False, **inp: Any) -> executor.Result:
    return await executor.run(system_principal(tid), command, {**OP, **inp}, dry_run=dry_run)


def _args(*argv: str) -> argparse.Namespace:
    return operator.parser().parse_args(["--tenant", "x", *argv])


async def _grant(tid: UUID, role: str, resource: str) -> Any:
    return await owner_val("SELECT scope || ':' || array_to_string(actions, ',') FROM core.grants WHERE tenant_id = %s AND role = %s AND resource = %s",
                           (tid, role, resource))


async def _counts(tid: UUID) -> tuple[int, ...]:
    return tuple([await owner_val(f"SELECT count(*) FROM core.{t} WHERE tenant_id = %s", (tid,))
                  for t in ("audit_log", "grants", "roles", "remote_apps", "approvals", "notifications", "idempotency")])


async def test_set_grant_is_applied_audited_and_hidden_from_the_customer_log(co: dict[str, Any]) -> None:
    tid = co["tid"]
    r = await _op(tid, "admin.operator_set_grant", role="staff", resource="crm", scope="all", actions=["read", "write"])
    assert r.status == "ok", r.error
    assert await _grant(tid, "staff", "crm") == "all:read,write"
    assert "(đang:" in r.output["summary"]                                                   # trước / sau như trên phiếu duyệt
    assert not await owner_val("SELECT count(*) FROM core.approvals WHERE tenant_id = %s", (tid,))   # không thành phiếu
    row = await owner_val("""SELECT to_jsonb(a) FROM core.audit_log a WHERE tenant_id = %s AND command = 'admin.operator_set_grant'
                             ORDER BY id DESC LIMIT 1""", (tid,))
    assert row["status"] == "ok" and row["system"] and row["user_id"] is None
    assert row["input"]["by"] == OP["by"] and row["input"]["why"] == OP["why"] and row["input"]["resource"] == "crm"
    audit = (await executor.query(co["admin"], "admin.audit", {})).output["rows"]
    assert not [x for x in audit if x["command"].startswith(("admin.operator_", "connect.operator_", "core.operator_"))]
    # thu hồi; tài nguyên của nhà vận hành không cấp được kể cả bằng cửa này; quản trị không thể hạ hết quyền quản trị
    assert (await _op(tid, "admin.operator_set_grant", role="staff", resource="crm", scope="all", actions=[])).status == "ok"
    assert await _grant(tid, "staff", "crm") is None
    for res in ("core.operator", "core.ai_policy", "khong_co"):
        assert (await _op(tid, "admin.operator_set_grant", role="admin", resource=res, scope="all", actions=["read"])).status == "not_found"
    assert (await _op(tid, "admin.operator_set_grant", role="khong_co", resource="crm", scope="all", actions=["read"])).status == "not_found"


async def test_every_operator_write_needs_a_name_and_a_reason(co: dict[str, Any]) -> None:
    tid = co["tid"]
    for blank in ({"by": " "}, {"why": ""}):
        r = await executor.run(system_principal(tid), "admin.operator_set_grant",
                               {**OP, **blank, "role": "staff", "resource": "crm", "scope": "all", "actions": ["read"]})
        assert r.status == "invalid" and "lý do" in r.error
        r = await executor.run(system_principal(tid), "admin.operator_create_role", {**OP, **blank, "key": "feed_x", "label": "X"})
        assert r.status == "invalid"
    assert await _grant(tid, "staff", "crm") is None
    # dòng lệnh dừng TRƯỚC khi mở DB
    for argv in (("set-grant", "staff", "crm", "all", "read", "--why", "x"), ("decide", str(uuid.uuid4()), "approve", "--by", "Anh"),
                 ("install-app", "--name", "A", "--like-role", "staff", "--grants", "crm:all:read", "--by", "Anh", "--why", " ")):
        with pytest.raises(SystemExit, match="--by"):
            await operator.main(_args(*argv))


async def test_role_tools_defaults_fill_only_roles_without_a_toolset(co: dict[str, Any]) -> None:
    """Công ty cài trước 0218: chưa vai nào có bộ công cụ ⇒ cài mặc định; vai quản trị đã tự chọn bộ thì giữ nguyên (không thêm lại mục đã bỏ);
    không đổi vai / quyền nào khác."""
    tid = co["tid"]
    async def tools(role: str) -> Any:
        return await owner_val("SELECT tools FROM core.role_tools WHERE tenant_id = %s AND role = %s", (tid, role))
    director_default = await tools("director")
    assert director_default, "công ty mới đã có bộ mặc định — test cần một vai có bộ để mô phỏng công ty cũ"
    await owner_val("DELETE FROM core.role_tools WHERE tenant_id = %s AND role <> 'admin' RETURNING 1", (tid,))
    await owner_val("UPDATE core.role_tools SET tools = %s WHERE tenant_id = %s AND role = 'admin' RETURNING 1", (["kb.documents"], tid))
    before = await _counts(tid)
    dry = await _op(tid, "admin.operator_role_tools", dry_run=True)
    assert dry.status == "dry_run" and await tools("director") is None         # chạy thử: không ghi
    r = await _op(tid, "admin.operator_role_tools")
    assert r.status == "ok", r.error
    assert sorted(await tools("director")) == sorted(director_default)       # vai chưa có bộ ⇒ đúng bộ mặc định
    assert await tools("admin") == ["kb.documents"] and "admin" in r.output["kept"]   # vai đã chọn bộ ⇒ giữ nguyên
    after = await _counts(tid)
    assert after[1:3] == before[1:3]                                          # quyền + vai không đổi
    again = await _op(tid, "admin.operator_role_tools")
    assert again.status == "ok" and not again.output["installed"]             # chạy lại: không còn gì để cài
    with pytest.raises(SystemExit, match="--by"):
        await operator.main(_args("role-tools-defaults", "--why", "x"))


async def test_skills_defaults_add_only_missing_pack_skills_and_keep_company_edits(co: dict[str, Any]) -> None:
    """Công ty cài trước khi gói có kỹ năng mặc định mới (vd tim_vi_sao — 0220 lát 6): lệnh hẹp chỉ thêm khoá công ty CHƯA có; kỹ năng công ty đã
    sửa / đã ngừng giữ nguyên (không ghi đè, không bật lại); không đổi vai / quyền nào khác."""
    tid = co["tid"]
    async def skill(key: str) -> Any:
        return await owner_val("SELECT json_agg(json_build_array(version, status, source) ORDER BY version) FROM core.ai_skills "
                               "WHERE tenant_id = %s AND key = %s", (tid, key))
    assert await skill("tim_vi_sao") == [[1, "active", "pack"]], "công ty mới đã có kỹ năng mặc định — test mô phỏng công ty cũ"
    await owner_val("DELETE FROM core.ai_skills WHERE tenant_id = %s AND key = 'tim_vi_sao' RETURNING 1", (tid,))
    before = await _counts(tid)
    dry = await _op(tid, "admin.operator_skills", dry_run=True)
    assert dry.status == "dry_run" and await skill("tim_vi_sao") is None         # chạy thử: không ghi
    r = await _op(tid, "admin.operator_skills")
    assert r.status == "ok", r.error
    assert "tim_vi_sao" in r.output["installed"] and await skill("tim_vi_sao") == [[1, "active", "pack"]]
    assert (await _counts(tid))[1:3] == before[1:3]                              # quyền + vai không đổi
    await owner_val("UPDATE core.ai_skills SET status = 'retired' WHERE tenant_id = %s AND key = 'tim_vi_sao' RETURNING 1", (tid,))
    again = await _op(tid, "admin.operator_skills")
    assert again.status == "ok" and not again.output["installed"] and "tim_vi_sao" in again.output["kept"]
    assert await skill("tim_vi_sao") == [[1, "retired", "pack"]]                 # công ty đã ngừng ⇒ không bật lại
    with pytest.raises(SystemExit, match="--by"):
        await operator.main(_args("skills-defaults", "--why", "x"))


async def test_install_defaults_installs_only_what_was_picked(co: dict[str, Any]) -> None:
    """``install-defaults``: cùng vòng cài với nút Cài phần còn thiếu nhưng CHỈ phần được chọn — ``--kind record`` cài dòng cấu hình (vd kỹ năng mặc
    định), không cấp lại quyền đang thiếu; ``--only <khoá như báo cáo>`` cài đúng mục đó; chạy thử không ghi gì; khoá gõ sai bị từ chối."""
    tid = co["tid"]
    skill = "core.install_skill:tim_vi_sao"
    grant = await owner_val("""SELECT key FROM core.pack_installs WHERE tenant_id = %s AND kind = 'grant' AND key LIKE 'staff|%%'
                               ORDER BY key LIMIT 1""", (tid,))
    assert grant, "công ty mới đã có quyền mặc định của vai nhân viên"
    role, res = grant.split("|")
    scope_actions = await _grant(tid, role, res)
    # mô phỏng công ty cài từ trước khi gói có hai mục này: chưa có dòng, chưa có nhật ký cài
    await owner_val("DELETE FROM core.ai_skills WHERE tenant_id = %s AND key = 'tim_vi_sao' RETURNING 1", (tid,))
    await owner_val("DELETE FROM core.grants WHERE tenant_id = %s AND role = %s AND resource = %s RETURNING 1", (tid, role, res))
    await owner_val("DELETE FROM core.pack_installs WHERE tenant_id = %s AND (kind, key) IN (('record', %s), ('grant', %s)) RETURNING 1",
                    (tid, skill, grant))
    async def has_skill() -> bool:
        return bool(await owner_val("SELECT count(*) FROM core.ai_skills WHERE tenant_id = %s AND key = 'tim_vi_sao'", (tid,)))

    dry = await _op(tid, "admin.operator_install_defaults", dry_run=True, kinds=["record"])
    assert dry.status == "dry_run" and f"record {skill}" in dry.output["installed"]
    assert not await has_skill()                                                         # chạy thử: không ghi
    r = await _op(tid, "admin.operator_install_defaults", kinds=["record"])
    assert r.status == "ok", r.error
    assert f"record {skill}" in r.output["installed"] and await has_skill()
    assert not [x for x in r.output["installed"] if not x.startswith("record ")]         # chỉ dòng cấu hình
    assert await _grant(tid, role, res) is None                                          # quyền thiếu KHÔNG bị cấp
    r = await _op(tid, "admin.operator_install_defaults", only=[grant])                   # khoá đúng như báo cáo in
    assert r.status == "ok" and r.output["installed"] == [f"grant {grant}"], r.output
    assert await _grant(tid, role, res) == scope_actions
    again = await _op(tid, "admin.operator_install_defaults", only=[grant])
    assert again.status == "ok" and not again.output["installed"] and again.output["not_missing"] == [f"grant {grant}"]
    bads: list[dict[str, Any]] = [{"only": ["khong|co"]}, {"kinds": ["khong_co"]}, {}]
    for bad in bads:
        assert (await executor.run(system_principal(tid), "admin.operator_install_defaults", {**OP, **bad})).status == "invalid", bad
    a = _args("install-defaults", "--kind", "record", "--only", grant, "--only", skill, "--by", "Anh", "--why", "x")
    assert (a.kind, a.only, a.all) == (["record"], [grant, skill], False)
    with pytest.raises(SystemExit, match="--by"):
        await operator.main(_args("install-defaults", "--kind", "record", "--why", "x"))


async def _feed_role(tid: UUID) -> None:
    assert (await _op(tid, "admin.operator_create_role", key="feed_role", label="Nguồn dữ liệu thử")).status == "ok"
    assert (await _op(tid, "admin.operator_set_grant", role="feed_role", resource="crm", scope="all", actions=["read", "write"])).status == "ok"
    assert (await _op(tid, "admin.operator_set_grant", role="feed_role", resource="leadflow", scope="all", actions=["read"])).status == "ok"


async def test_create_role_refuses_an_existing_role_and_copies_grants(co: dict[str, Any]) -> None:
    tid = co["tid"]
    await _feed_role(tid)
    again = await _op(tid, "admin.operator_create_role", key="feed_role", label="Đổi tên")
    assert again.status == "invalid" and "Đã có vai" in again.error
    assert await owner_val("SELECT label FROM core.roles WHERE tenant_id = %s AND key = 'feed_role'", (tid,)) == "Nguồn dữ liệu thử"
    out = (await _op(tid, "admin.operator_create_role", key="feed_copy", label="Bản chép", copy_from="feed_role")).output
    assert out["copied"] == 2 and await _grant(tid, "feed_copy", "crm") == "all:read,write"
    assert (await _op(tid, "admin.operator_create_role", key="Feed-1", label="Sai")).status == "invalid"


async def test_install_app_is_bounded_by_the_like_role_and_the_secret_never_lands_in_logs(co: dict[str, Any], tmp_path: Path,
                                                                                          capsys: pytest.CaptureFixture[str],
                                                                                          caplog: pytest.LogCaptureFixture) -> None:
    tid = co["tid"]
    await _feed_role(tid)
    base: dict[str, Any] = {"name": "Nguồn thử", "like_role": "feed_role", "events": "", "webhook_url": ""}
    over = await _op(tid, "connect.operator_install", **base, grants="crm:all:read,write; leadflow:all:write")
    assert over.status == "denied" and "vượt quyền của vai" in over.error and "leadflow: write" in over.error
    assert (await _op(tid, "connect.operator_install", **base, grants="ledger:all:read")).status == "denied"        # vai không có
    team = await _op(tid, "connect.operator_install", **base, grants="crm:team:read")
    assert team.status == "invalid" and "own hoặc all" in team.error
    assert (await _op(tid, "connect.operator_install", **base, grants="core.operator:all:read")).status == "invalid"
    assert (await _op(tid, "connect.operator_install", **{**base, "like_role": "khong_co"}, grants="crm:all:read")).status == "not_found"
    assert not await owner_val("SELECT count(*) FROM core.remote_apps WHERE tenant_id = %s", (tid,))

    # dòng lệnh: lưới quyền cách nhau dấu cách; khoá vào file 600, KHÔNG in ra màn hình; không có trong log / nhật ký / bảng app
    caplog.set_level(logging.DEBUG)
    path = tmp_path / "app.env"
    await operator.install_app(tid, _args("install-app", "--name", "Nguồn thử", "--like-role", "feed_role", "--grants",
                                          "crm:all:read, write leadflow:all:read", "--secret-file", str(path), "--by", OP["by"], "--why", OP["why"]))
    env = dict(line.split("=", 1) for line in path.read_text().splitlines())
    secret, client_id = env["BARISTA_CLIENT_SECRET"], env["BARISTA_CLIENT_ID"]
    assert secret.startswith("bcs_") and env["BARISTA_WEBHOOK_SECRET"].startswith("whsec_")
    assert os.stat(path).st_mode & 0o777 == 0o600
    printed = capsys.readouterr().out
    assert client_id in printed and secret not in printed
    assert secret not in caplog.text
    a = await owner_val("SELECT to_jsonb(a) FROM core.remote_apps a WHERE tenant_id = %s", (tid,))
    assert a["sponsor_role"] == "feed_role" and a["installed_by"] is None and a["client_id"] == client_id and secret not in json.dumps(a)
    logged = await owner_val("SELECT jsonb_agg(to_jsonb(x)) FROM core.audit_log x WHERE tenant_id = %s", (tid,))
    assert secret not in json.dumps(logged) and env["BARISTA_WEBHOOK_SECRET"] not in json.dumps(logged)
    # lần cài THÀNH CÔNG (các lần bị từ chối phía trên cũng ghi nhật ký; jsonb_agg không giữ thứ tự ⇒ lọc theo trạng thái, không lấy dòng đầu)
    row = next(x for x in logged if x["command"] == "connect.operator_install" and x["status"] == "ok")
    assert row["input"]["by"] == OP["by"] and row["input"]["like_role"] == "feed_role"
    # file đã có ⇒ dừng TRƯỚC khi cài (không sinh app thứ hai)
    with pytest.raises(SystemExit, match="file phải CHƯA có"):
        await operator.install_app(tid, _args("install-app", "--name", "Lần hai", "--like-role", "feed_role", "--grants", "crm:all:read",
                                              "--secret-file", str(path), "--by", "x", "--why", "y"))
    assert await owner_val("SELECT count(*) FROM core.remote_apps WHERE tenant_id = %s", (tid,)) == 1

    # lúc chạy: quyền app = vai app ∩ quyền HIỆN TẠI của vai bảo lãnh; thu hẹp vai ⇒ app thu hẹp ngay
    tok = (await remote_apps.issue_token(client_id, secret))["access_token"]
    got = await remote_apps.principal_for(tok)
    assert got is not None
    p, _ = got
    assert p.via == "app" and p.grants["crm"].scope == "all" and p.grants["crm"].actions == {"read", "write"} and "leadflow" in p.grants
    assert (await _op(tid, "admin.operator_set_grant", role="feed_role", resource="crm", scope="all", actions=["read"])).status == "ok"
    got = await remote_apps.principal_for(tok)
    assert got is not None and got[0].grants["crm"].actions == {"read"}
    shown = (await executor.query(co["admin"], "connect.apps", {})).output["rows"][0]
    assert "nhà vận hành cài" in shown["sponsor"]

    # xoay khoá: in ra màn hình MỘT lần khi không có --secret-file; khoá cũ + token cũ hết hiệu lực; thu hồi
    await operator.rotate_app(tid, _args("rotate-app", "Nguồn thử", "--by", OP["by"], "--why", "lộ khoá (test)"))
    out = capsys.readouterr().out
    new = re.findall(r"BARISTA_CLIENT_SECRET=(\S+)", out)
    assert len(new) == 1 and new[0] != secret and out.count(new[0]) == 1 and new[0] not in caplog.text
    assert await remote_apps.principal_for(tok) is None
    with pytest.raises(remote_apps.TokenRefused):
        await remote_apps.issue_token(client_id, secret)
    rv = await _op(tid, "connect.operator_revoke", app_id=a["id"])
    assert rv.status == "ok" and await owner_val("SELECT status FROM core.remote_apps WHERE id = %s", (UUID(a["id"]),)) == "revoked"


async def test_decide_runs_another_users_request_as_that_requester(co: dict[str, Any]) -> None:
    tid, admin = co["tid"], co["admin"]
    r = await executor.run(admin, "admin.set_grant", {"role": "staff", "resource": "crm", "scope": "all", "actions": ["read"]})
    assert r.status == "pending_approval"
    rows = (await executor.query(system_principal(tid), "core.operator_pending", {})).output["rows"]
    assert [x["operator"] for x in rows if str(x["id"]) == r.approval_id] == [True]
    # người vận hành trùng người xin ⇒ không tự duyệt
    self_ = await _op(tid, "core.operator_decide", approval_id=r.approval_id, approve=True, by=admin.email)
    assert self_.status == "denied" and "không tự duyệt" in self_.error
    d = await _op(tid, "core.operator_decide", approval_id=r.approval_id, approve=True)
    assert d.status == "ok" and d.output["status"] == "executed", d
    assert await _grant(tid, "staff", "crm") == "all:read"
    run = await owner_val("""SELECT to_jsonb(a) FROM core.audit_log a WHERE tenant_id = %s AND command = 'admin.set_grant' AND ref = %s
                             ORDER BY id DESC LIMIT 1""",
                          (tid, r.approval_id))
    assert run["user_id"] == str(admin.user_id) and not run["system"] and run["status"] == "ok"   # chạy bằng quyền NGƯỜI XIN
    assert "nhà vận hành" in run["error"] and OP["by"] in run["error"] and OP["why"] in run["error"]
    ap = await owner_val("SELECT to_jsonb(a) FROM core.approvals a WHERE id = %s", (UUID(r.approval_id),))
    assert ap["status"] == "executed" and ap["decisions"][-1]["operator"] == OP["by"] and OP["why"] in ap["decisions"][-1]["note"]
    assert run["approved_by"] == [ap["decisions"][-1]["user_id"]] != [str(admin.user_id)]       # người quyết = danh tính nhà vận hành, không phải người xin
    assert not await owner_val("SELECT count(*) FROM core.memberships m JOIN core.users u ON u.id = m.user_id WHERE u.email = %s",
                               ("operator@operators.barista.invalid",))                        # không là thành viên công ty nào
    assert (await _op(tid, "core.operator_decide", approval_id=r.approval_id, approve=True)).status == "invalid"   # xong rồi
    # khách thấy lệnh của người xin (kèm ghi chú), không thấy lệnh của nhà vận hành
    audit = (await executor.query(admin, "admin.audit", {})).output["rows"]
    assert any(x["command"] == "admin.set_grant" and OP["by"] in (x["error"] or "") for x in audit)
    assert not any(x["command"] == "core.operator_decide" for x in audit)

    # từ chối; phiếu nghiệp vụ (chuỗi có người duyệt khác chủ tài khoản) ⇒ nhà vận hành không quyết
    r2 = await executor.run(admin, "admin.set_grant", {"role": "staff", "resource": "crm", "scope": "own", "actions": ["read"]})
    assert (await _op(tid, "core.operator_decide", approval_id=r2.approval_id, approve=False)).output["status"] == "rejected"
    assert await _grant(tid, "staff", "crm") == "all:read"
    r3 = await executor.run(admin, "admin.set_grant", {"role": "staff", "resource": "crm", "scope": "own", "actions": ["read"]})
    async with db.owner_tx() as c:
        await c.execute("""UPDATE core.approvals SET steps = '[{"kind": "team_lead", "label": "Trưởng nhóm"}]' WHERE id = %s""", (UUID(r3.approval_id),))
    biz = await _op(tid, "core.operator_decide", approval_id=r3.approval_id, approve=True)
    assert biz.status == "denied" and "phiếu nghiệp vụ" in biz.error


async def test_dry_run_writes_nothing(co: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    tid, admin = co["tid"], co["admin"]
    await _feed_role(tid)
    r = await executor.run(admin, "admin.set_grant", {"role": "staff", "resource": "crm", "scope": "all", "actions": ["read"]})
    before = await _counts(tid)
    dry = await _op(tid, "admin.operator_set_grant", dry_run=True, role="staff", resource="leadflow", scope="all", actions=["read"])
    assert dry.status == "dry_run" and "leadflow" in dry.output["summary"]
    assert (await _op(tid, "admin.operator_create_role", dry_run=True, key="feed_dry", label="Chạy thử")).status == "dry_run"
    assert (await _op(tid, "connect.operator_install", dry_run=True, name="Thử", like_role="feed_role", grants="crm:all:read",
                      events="", webhook_url="")).status == "dry_run"
    decided = await _op(tid, "core.operator_decide", dry_run=True, approval_id=r.approval_id, approve=True)
    assert decided.status == "dry_run" and decided.output["status"] == "executed"
    bad = await _op(tid, "admin.operator_set_grant", dry_run=True, role="khong_co", resource="crm", scope="all", actions=["read"])
    assert bad.status == "not_found"                                                          # lỗi như chạy thật, không ghi nhật ký lỗi
    await operator.install_app(tid, _args("install-app", "--name", "Thử", "--like-role", "feed_role", "--grants", "crm:all:read", "--dry-run",
                                          "--by", "x", "--why", "y"))
    assert "BARISTA_CLIENT_SECRET" not in capsys.readouterr().out
    assert await _counts(tid) == before
    assert await owner_val("SELECT status FROM core.approvals WHERE id = %s", (UUID(r.approval_id),)) == "pending"
    assert (await executor.run(admin, "admin.set_grant", {"role": "staff", "resource": "crm", "scope": "all", "actions": ["read"]},
                               dry_run=True)).status == "denied"                               # chạy thử chỉ cho quyền hệ thống


OPERATOR_OPS = ("admin.operator_set_grant", "admin.operator_create_role", "connect.operator_install", "connect.operator_rotate",
                "connect.operator_revoke", "core.operator_decide", "core.operator_pending")


async def test_the_operator_door_has_no_http_assistant_or_customer_path(co: dict[str, Any]) -> None:
    admin = co["admin"]
    for name in OPERATOR_OPS:
        op = registry.get(name)
        assert op is not None and op.internal and op.agent == "deny" and op.resource == "core.operator" and registry.operator_command(name), name
    assert "core.operator" in registry.OPERATOR_RESOURCES and "core.operator" not in registry.grantable()
    # mọi lệnh ghi trên tài nguyên của nhà vận hành đều nội bộ + trợ lý bị cấm (lệnh 0193 cũ không nội bộ nhưng cũng không vai nào chạy được)
    for name, op in registry.OPS.items():
        if op.resource == "core.operator":
            assert op.internal and op.agent == "deny", name
    inp = {**OP, "role": "admin", "resource": "crm", "scope": "all", "actions": ["read"]}
    for p in (admin, co["giamdoc"], admin.as_agent()):
        assert (await executor.run(p, "admin.operator_set_grant", inp)).status == "not_found"
        assert (await executor.query(p, "core.operator_pending", {})).status == "not_found"
    assert (await executor.run(admin, "admin.set_grant", {"role": "admin", "resource": "core.operator", "scope": "all",
                                                               "actions": ["read", "manage"]})).status == "not_found"
    with pytest.raises(Exception, match="nhà vận hành"):
        async with db.tenant_tx(admin) as c:
            await c.execute("INSERT INTO core.grants (role, resource, scope, actions) VALUES ('admin', 'core.operator', 'all', '{read,manage}')")
    # HTTP: không lệnh / truy vấn nào của cửa này tới được (kể cả khi đăng nhập là chủ tài khoản)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as h:
        for name in OPERATOR_OPS:
            kind = "commands" if registry.OPS[name].kind == "command" else "queries"
            res = await h.post(f"/v1/{kind}/{name}", json={})
            assert res.status_code in (401, 403, 404), (name, res.status_code)
    # mã: dòng lệnh không được nạp bởi HTTP / MCP / trợ lý / worker — chỉ chạy bằng ``python -m barista.operator``
    importers = [f.relative_to(SRC) for f in SRC.rglob("*.py") if f.name != "operator.py"
                 and re.search(r"^\s*(from barista import .*\boperator\b|import barista\.operator|from barista\.operator import)", f.read_text(), re.M)]
    assert importers == [], importers
