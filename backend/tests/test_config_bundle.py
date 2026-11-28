"""GÓI CẤU HÌNH CÔNG TY (ADR 0263 mục C) trên dữ liệu GIẢ: xuất ở công ty nguồn → tải lên + nhập vào công ty mới (chỉ có mặc định của gói) ⇒ xuất lại ra
đúng ``config_version`` của tệp; nhập lần hai không đổi gì; chạy thử (``dry_run``) không ghi gì; người chưa là thành viên công ty đích ⇒ liệt kê "cần mời
trước", không tạo tài khoản, mục nhắc họ bỏ qua; tệp sửa tay sau khi xuất ⇒ từ chối; trợ lý không tải / nhập được."""

import hashlib
import itertools
import json
from typing import Any
from uuid import UUID

from barista import seed
from barista.kernel import config_bundle, executor, loader, registry
from barista.kernel.clock import today
from barista.kernel.principal import Principal
from tests.conftest import approved, ok, owner_val, q

_seq = itertools.count(1)
PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("gd", "Giám đốc", "director", "QT", False), ("a", "Sale A", "sale", "KD-A", False)]
SHARED = "chung.cauhinh@ngoai.demo"   # một người có mặt ở CẢ hai công ty (cùng email) — khoá ánh xạ người


async def _company() -> tuple[UUID, str]:
    domain = f"cfg{next(_seq)}.demo"
    tid = await seed.create_tenant(f"Công ty cấu hình {domain}", domain, people=PEOPLE, packs=("crm",))
    return tid, domain


async def _admin(tid: UUID, domain: str) -> Principal:
    return await seed.who(tid, "admin", domain)


async def _invite_shared(admin: Principal) -> None:
    await approved(admin, "admin.invite_user", email=SHARED, name="Người Dùng Chung", role="staff")


async def _customize(admin: Principal, *, extra_person: str = "") -> None:
    """Cấu hình "đã làm trên bản thử": vai + quyền mới, đội, chuỗi + luật duyệt (người duyệt theo email), cài đặt, từ vựng, trường riêng, mẫu quy trình,
    luật chia lead + thành viên."""
    await approved(admin, "admin.upsert_role", key="truong_cn", label="Trưởng chi nhánh", entry=False)
    await approved(admin, "admin.set_grant", role="truong_cn", resource="crm", scope="team", actions=["read", "write"])
    await approved(admin, "admin.upsert_team", code="CN-HN", name="Chi nhánh Hà Nội", kind="branch", parent_code="HQ")
    await approved(admin, "admin.set_chain", key="chi_lon", label="Chi lớn", steps=f"team_lead > role:director > user:{SHARED}")
    if extra_person:
        await approved(admin, "admin.set_chain", key="chi_rieng", label="Chi riêng", steps=f"user:{extra_person}")
    await approved(admin, "admin.set_rule", command="admin.upsert_entity", chain="chi_lon")
    await ok(admin, "admin.set_setting", key="leadflow.members_only", enabled=True)
    await ok(admin, "admin.set_term", term="offering", label="Dự án")
    await ok(admin, "admin.save_field", subject="lead", label="Mã công tơ", kind="text")
    await ok(admin, "flow.set_template", key="ho_so_thu", name="Hồ sơ thử", source="# Nhận hồ sơ\n- Đã nhận giấy tờ\n# Xong\n- Đã bàn giao")
    await ok(admin, "leadflow.set_rule", name="Lead mới vào đội A", effective_from=today().isoformat(), team_code="KD-A", roles=["sale"])
    await ok(admin, "leadflow.set_member", offering_code="GOI-A", user_email=SHARED, tier=1, weight=2)
    if extra_person:
        await ok(admin, "leadflow.set_member", offering_code="GOI-A", user_email=extra_person, tier=2)


async def _export(admin: Principal) -> dict[str, Any]:
    return await q(admin, "admin.export_config")


async def _upload(admin: Principal, bundle: dict[str, Any] | bytes) -> UUID:
    data = bundle if isinstance(bundle, bytes) else json.dumps(bundle, ensure_ascii=False).encode()
    r = await executor.run(admin, "admin.upload_config", {"filename": "cau-hinh.json", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()},
                           files={"file": data})
    assert r.status == "ok", (r.status, r.error)
    return UUID(str(r.output["bundle_id"]))


def test_every_config_resource_reads_its_own_module_and_writes_through_a_public_command() -> None:
    loader.load()
    registry.validate()
    keys = set(registry.CONFIGS)
    assert {"admin.roles", "admin.grants", "admin.teams", "admin.chains", "admin.rules", "admin.settings", "admin.vocabulary", "admin.fields",
            "flow.templates", "leadflow.rules", "leadflow.members"} <= keys
    assert registry.CONFIGS["admin.rules"].order == max(c.order for c in registry.CONFIGS.values())   # luật duyệt nhập sau cùng
    assert registry.get("admin.import_config").agent == "deny" and registry.get("admin.upload_config").agent == "deny"   # type: ignore[union-attr]


def test_people_are_found_in_any_string_of_an_item() -> None:
    assert config_bundle.people({"steps": "team_lead > user:GD@Cty.vn > owner", "x": ["@KT", "a b"], "n": 3}) == {"gd@cty.vn"}


async def test_export_then_import_into_a_fresh_company_gives_the_same_version_and_a_second_import_changes_nothing() -> None:
    src_tid, src_dom = await _company()
    src = await _admin(src_tid, src_dom)
    await _invite_shared(src)
    await _customize(src)
    bundle = await _export(src)
    assert bundle["format"] == "barista.config" and len(bundle["config_version"]) == 64
    assert {"admin.roles", "admin.grants", "flow.templates", "leadflow.rules", "leadflow.members"} <= set(bundle["resources"])
    text = json.dumps(bundle["resources"], ensure_ascii=False)
    assert src_dom not in text   # không người dùng của công ty nguồn: chỉ email người được NHẮC trong cấu hình (ở đây là người dùng chung)
    assert not any("_id" in k for block in bundle["resources"].values() for item in block["items"] for k in item)   # không mã nội bộ

    dst_tid, dst_dom = await _company()
    dst = await _admin(dst_tid, dst_dom)
    await _invite_shared(dst)
    before = (await _export(dst))["config_version"]
    assert before != bundle["config_version"]
    bid = await _upload(dst, bundle)

    # chạy thử: bảng so khác, KHÔNG ghi gì
    dry = await ok(dst, "admin.import_config", bundle_id=str(bid))
    assert dry["dry_run"] is True and dry["needs_invite"] == []
    statuses = {(x["resource"], x["name"]): x["status"] for x in dry["lines"]}
    assert statuses[("admin.roles", "truong_cn")] == "add" and statuses[("admin.teams", "CN-HN")] == "add"
    assert statuses[("admin.settings", "leadflow.members_only")] == "change"
    assert (await _export(dst))["config_version"] == before
    assert await owner_val("SELECT applied_at FROM admin.config_bundles WHERE id = %s", (bid,)) is None

    # nhập thật: một phiếu chủ tài khoản cho cả tệp, rồi xuất lại ra đúng phiên bản của tệp
    first = await approved(dst, "admin.import_config", bundle_id=str(bid), dry_run=False)
    assert first["failed"] == [], first["failed"]
    assert first["written"] > 0
    after = await _export(dst)
    assert after["config_version"] == bundle["config_version"], _first_diff(bundle, after)

    # nhập lần hai: không có gì để ghi ⇒ không thành phiếu, không thêm phiên bản mẫu quy trình
    versions = await owner_val("SELECT count(*) FROM flow.templates WHERE tenant_id = %s AND key = 'ho_so_thu'", (dst_tid,))
    r = await executor.run(dst, "admin.import_config", {"bundle_id": str(bid), "dry_run": False})
    assert r.status == "ok" and r.output["written"] == 0, (r.status, r.output)
    assert await owner_val("SELECT count(*) FROM flow.templates WHERE tenant_id = %s AND key = 'ho_so_thu'", (dst_tid,)) == versions == 1
    assert (await _export(dst))["config_version"] == bundle["config_version"]
    page = await q(dst, "admin.config_bundles")
    assert [x["state"] for x in page["rows"]] == ["same"]


def _first_diff(a: dict[str, Any], b: dict[str, Any]) -> str:
    for k in sorted(set(a["resources"]) | set(b["resources"])):
        x, y = a["resources"].get(k, {}).get("items"), b["resources"].get(k, {}).get("items")
        if x != y:
            return f"{k}: {[i for i in (x or []) if i not in (y or [])]} ≠ {[i for i in (y or []) if i not in (x or [])]}"
    return ""


async def test_people_missing_in_the_target_are_listed_not_created_and_their_items_wait() -> None:
    src_tid, src_dom = await _company()
    src = await _admin(src_tid, src_dom)
    await _invite_shared(src)
    only_src = f"a@{src_dom}"   # người chỉ có ở công ty nguồn
    await _customize(src, extra_person=only_src)
    bundle = await _export(src)
    dst_tid, dst_dom = await _company()
    dst = await _admin(dst_tid, dst_dom)
    await _invite_shared(dst)
    bid = await _upload(dst, bundle)
    dry = await ok(dst, "admin.import_config", bundle_id=str(bid))
    assert dry["needs_invite"] == [only_src]
    waiting = {(x["resource"], x["name"]) for x in dry["lines"] if x["status"] == "invite"}
    assert waiting == {("admin.chains", "chi_rieng"), ("leadflow.members", f"GOI-A · {only_src}")}
    done = await approved(dst, "admin.import_config", bundle_id=str(bid), dry_run=False)
    assert done["failed"] == [] and done["needs_invite"] == [only_src]
    # không tạo tài khoản / thành viên cho người chưa mời; mục nhắc họ chưa vào
    assert await owner_val("""SELECT count(*) FROM core.memberships mb JOIN core.users u ON u.id = mb.user_id
                              WHERE mb.tenant_id = %s AND u.email = %s""", (dst_tid, only_src)) == 0
    assert await owner_val("SELECT count(*) FROM core.approval_chains WHERE tenant_id = %s AND key = 'chi_rieng'", (dst_tid,)) == 0
    assert await owner_val("SELECT count(*) FROM core.approval_chains WHERE tenant_id = %s AND key = 'chi_lon'", (dst_tid,)) == 1
    card = await q(dst, "admin.config_diff", bundle_id=str(bid))
    assert any(b.get("type") == "callout" and only_src in (b.get("facts") or []) for b in card["blocks"])


async def test_a_file_edited_after_export_is_refused_and_the_assistant_cannot_upload_or_import() -> None:
    tid, domain = await _company()
    admin = await _admin(tid, domain)
    bundle = await _export(admin)
    bad = json.loads(json.dumps(bundle))
    bad["resources"]["admin.roles"]["items"].append({"key": "lau", "label": "Vai chèn tay", "entry": False})
    data = json.dumps(bad).encode()
    r = await executor.run(admin, "admin.upload_config", {"filename": "x.json", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()},
                           files={"file": data})
    assert r.status == "invalid" and "config_version" in r.error
    good = json.dumps(bundle).encode()
    r = await executor.run(admin.as_agent(), "admin.upload_config", {"filename": "x.json", "size": len(good), "sha256": hashlib.sha256(good).hexdigest()},
                           files={"file": good})
    assert r.status == "denied"
    bid = await _upload(admin, bundle)
    assert (await executor.run(admin.as_agent(), "admin.import_config", {"bundle_id": str(bid), "dry_run": False})).status == "denied"
    assert (await executor.run(admin.as_agent(), "admin.import_config", {"bundle_id": str(bid)})).status == "denied"
    # cùng công ty: tệp của chính mình ⇒ mọi mục giữ nguyên
    dry = await ok(admin, "admin.import_config", bundle_id=str(bid))
    assert dry["lines"] == [] and dry["current_version"] == bundle["config_version"]
