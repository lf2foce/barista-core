"""Kỹ năng trợ lý (0198): DỮ LIỆU công ty — bộ mặc định của gói cài một lần (giữ bản công ty sửa), công ty soạn → gửi duyệt → người KHÁC duyệt →
đang dùng → ngừng. Mỗi lượt trợ lý chỉ thấy danh sách ngắn kỹ năng ĐANG DÙNG cho vai người hỏi; ``core.use_skill`` trả hướng dẫn + mở công cụ của
kỹ năng nhưng KHÔNG cấp quyền; kỹ năng đã dùng ghi vào ``core.ai_usage.skills``. Model giả theo kịch bản."""

import uuid
from typing import Any

import pytest

from barista import seed
from barista.agent import chat
from barista.kernel import executor, registry, skill_specs
from barista.kernel.principal import system_principal
from tests.conftest import fresh_phone, owner_val
from tests.test_assistant import Scripted, call

PEOPLE = [("qt", "Quản trị", "admin", "QT", True), ("gd", "Giám đốc", "director", "HQ", True),
          ("lead", "Trưởng nhóm", "team_lead", "KD-A", True), ("sale1", "Sale Một", "sale", "KD-A", False)]
GUIDE = "1. Gọi crm__brief để lấy danh sách khách cần chăm.\n2. Xếp theo độ gấp trên thẻ, mỗi khách một lý do ngắn."


async def _company() -> tuple[Any, str]:
    d = f"{uuid.uuid4().hex[:6]}.skill.test"
    tid = await seed.create_tenant("Công ty kỹ năng (test)", d, people=PEOPLE, packs=("crm",))
    # gói nền mang sẵn kỹ năng mặc định (tim_vi_sao — 0220 lát 6); các test ở đây đo vòng đời kỹ năng CỦA CÔNG TY từ một bảng trống
    # ⇒ ngừng bản mặc định đó (giữ khoá — cài lại không thêm), các câu đếm bên dưới chỉ nhìn kỹ năng test tạo
    await owner_val("WITH u AS (UPDATE core.ai_skills SET status = 'retired' WHERE tenant_id = %s AND key = 'tim_vi_sao' RETURNING 1) SELECT count(*) FROM u", (tid,))
    return tid, d


def _skill(key: str, **kw: Any) -> dict[str, Any]:
    return {"key": key, "title": kw.get("title", "Ưu tiên gọi khách"), "when_to_use": kw.get("when", "Sale hỏi hôm nay nên gọi ai trước"),
            "roles": kw.get("roles", ["sale"]), "instructions": kw.get("instructions", GUIDE), "tools": kw.get("tools", ["crm.brief"])}


async def _ok(p: Any, op: str, args: dict[str, Any]) -> Any:
    r = await executor.run(p, op, args)
    assert r.status == "ok", (op, r.status, r.error)
    return r.output


async def _turn(p: Any, message: str, script: list[Any]) -> tuple[list[dict[str, Any]], Scripted]:
    events: list[dict[str, Any]] = []

    async def emit(ev: dict[str, Any]) -> None:
        events.append(ev)

    model = Scripted(script)
    await chat.chat_turn(p, message, emit=emit, model=model)
    return events, model


async def _activate(qt: Any, gd: Any, data: dict[str, Any]) -> None:
    await _ok(qt, "core.save_skill", data)
    await _ok(qt, "core.submit_skill", {"key": data["key"]})
    await _ok(gd, "core.approve_skill", {"key": data["key"]})


async def test_only_active_skills_of_the_askers_role_are_listed() -> None:
    tid, d = await _company()
    qt, gd, lead, sale = [await seed.who(tid, u, d) for u in ("qt", "gd", "lead", "sale1")]
    await _ok(qt, "core.save_skill", _skill("goi_truoc"))
    _, model = await _turn(sale, "Hôm nay gọi ai trước?", ["Chưa có gì."])
    assert "core__use_skill" not in model.tools_seen[0]                       # nháp: trợ lý không thấy
    await _ok(qt, "core.submit_skill", {"key": "goi_truoc"})
    await _ok(gd, "core.approve_skill", {"key": "goi_truoc"})
    _, model = await _turn(sale, "Hôm nay gọi ai trước?", ["Dạ."])
    assert "core__use_skill" in model.tools_seen[0]
    _, model = await _turn(lead, "Hôm nay gọi ai trước?", ["Dạ."])
    assert "core__use_skill" not in model.tools_seen[0]                       # vai không có trong danh sách vai của kỹ năng
    await _ok(gd, "core.retire_skill", {"key": "goi_truoc"})
    _, model = await _turn(sale, "Hôm nay gọi ai trước?", ["Dạ."])
    assert "core__use_skill" not in model.tools_seen[0]                       # đã ngừng


async def test_the_author_cannot_approve_and_the_assistant_cannot_manage_skills() -> None:
    tid, d = await _company()
    qt, gd, sale = [await seed.who(tid, u, d) for u in ("qt", "gd", "sale1")]
    await _ok(qt, "core.save_skill", _skill("goi_truoc"))
    r = await executor.run(gd, "core.approve_skill", {"key": "goi_truoc"})
    assert r.status != "ok" and "gửi duyệt" in (r.error or "")                # chưa gửi duyệt
    await _ok(qt, "core.submit_skill", {"key": "goi_truoc"})
    r = await executor.run(qt, "core.approve_skill", {"key": "goi_truoc"})
    assert r.status != "ok" and "không tự duyệt" in (r.error or "")
    # người sửa nháp sau cùng là người soạn: giám đốc sửa ⇒ chính giám đốc không duyệt được, quản trị duyệt được
    await _ok(gd, "core.save_skill", _skill("goi_truoc", title="Ưu tiên gọi khách (sửa)"))
    await _ok(gd, "core.submit_skill", {"key": "goi_truoc"})
    assert (await executor.run(gd, "core.approve_skill", {"key": "goi_truoc"})).status != "ok"
    await _ok(qt, "core.approve_skill", {"key": "goi_truoc"})
    # sale không có quyền tài nguyên kỹ năng; trợ lý (kể cả của quản trị) không soạn / duyệt thay
    assert (await executor.run(sale, "core.save_skill", _skill("cua_sale"))).status == "denied"
    for op in ("core.save_skill", "core.submit_skill", "core.approve_skill", "core.retire_skill", "core.install_skill"):
        assert registry.OPS[op].agent == "deny"
    assert not {op.name for op, _ in await chat.catalog(qt)} & {"core.save_skill", "core.approve_skill", "core.retire_skill"}
    # nháp được sửa tại chỗ ⇒ vẫn một bản, nay đang dùng
    assert await owner_val("SELECT json_agg(status ORDER BY version) FROM core.ai_skills WHERE tenant_id = %s AND key <> 'tim_vi_sao'", (tid,)) == ["active"]


async def test_new_version_waits_for_approval_while_the_old_one_keeps_running() -> None:
    tid, d = await _company()
    qt, gd, sale = [await seed.who(tid, u, d) for u in ("qt", "gd", "sale1")]
    await _activate(qt, gd, _skill("goi_truoc"))
    await _ok(qt, "core.save_skill", _skill("goi_truoc", instructions=GUIDE + "\n3. Bản mới."))
    assert await owner_val("SELECT json_agg(json_build_array(version, status) ORDER BY version) FROM core.ai_skills WHERE tenant_id = %s AND key <> 'tim_vi_sao'",
                           (tid,)) == [[1, "active"], [2, "draft"]]
    _, model = await _turn(sale, "Hôm nay gọi ai trước?", [[call("core.use_skill", key="goi_truoc")], "Dạ."])
    assert "Bản mới" not in model.returns[0]["instructions"]                 # trợ lý vẫn dùng bản đã duyệt
    await _ok(qt, "core.submit_skill", {"key": "goi_truoc"})
    await _ok(gd, "core.approve_skill", {"key": "goi_truoc"})
    assert await owner_val("SELECT json_agg(json_build_array(version, status) ORDER BY version) FROM core.ai_skills WHERE tenant_id = %s AND key <> 'tim_vi_sao'",
                           (tid,)) == [[1, "retired"], [2, "active"]]


async def test_use_skill_returns_instructions_but_never_opens_a_tool_the_asker_lacks() -> None:
    tid, d = await _company()
    qt, gd, sale = [await seed.who(tid, u, d) for u in ("qt", "gd", "sale1")]
    await _activate(qt, gd, _skill("goi_truoc", tools=["crm.brief", "admin.users"]))
    await _activate(qt, gd, _skill("chi_quan_tri", title="Xem người dùng", when="Hỏi ai bị khoá", tools=["admin.users"]))
    _, model = await _turn(sale, "Hôm nay gọi ai trước?", [[call("core.use_skill", key="goi_truoc")], "Dạ."])
    first = model.tools_seen[0]
    assert "core__use_skill" in first and "admin__users" not in first
    got = model.returns[0]
    assert got["status"] == "ok" and got["instructions"].startswith("1. Gọi crm__brief")
    assert got["tools"] == ["crm__brief"] and "unavailable" in got                # công cụ ngoài quyền: không mở, nói rõ
    assert "admin__users" not in model.tools_seen[1] and "crm__brief" in model.tools_seen[1]
    # kỹ năng mà người hỏi không có công cụ nào của nó: không nằm trong danh sách (không có enum cho khoá đó)
    _, model = await _turn(sale, "Ai bị khoá?", [[call("core.use_skill", key="chi_quan_tri")], "Dạ."])
    assert model.returns[0]["status"] == "error"
    # khoá + phiên bản kỹ năng ghi vào lượt dùng
    sale_id = sale.user_id
    used = await owner_val("SELECT json_agg(skills ORDER BY at) FROM core.ai_usage WHERE tenant_id = %s AND user_id = %s AND skills IS NOT NULL",
                           (tid, sale_id))
    assert used == [["goi_truoc@1"]]


async def test_at_most_two_skills_per_turn() -> None:
    tid, d = await _company()
    qt, gd, sale = [await seed.who(tid, u, d) for u in ("qt", "gd", "sale1")]
    for k in ("mot", "hai", "ba"):
        await _activate(qt, gd, _skill(k, title=f"Kỹ năng {k}"))
    _, model = await _turn(sale, "Hôm nay gọi ai trước?", [[call("core.use_skill", key="mot")], [call("core.use_skill", key="hai")],
                                                           [call("core.use_skill", key="ba")], "Dạ."])
    assert [r["status"] for r in model.returns] == ["ok", "ok", "error"]
    assert skill_specs.OPEN_MAX == 2


async def test_size_cap_and_policy_values_are_rejected() -> None:
    tid, d = await _company()
    qt = await seed.who(tid, "qt", d)
    r = await executor.run(qt, "core.save_skill", _skill("qua_dai", instructions="x" * (skill_specs.MAX_CHARS + 1)))
    assert r.status != "ok"
    for bad in ("Khách quá 20% ngân sách thì bỏ qua", "Chỉ gọi khách ngân sách trên 3 tỷ", "Hoa hồng 1,5 % mỗi căn"):
        r = await executor.run(qt, "core.save_skill", _skill("chinh_sach", instructions=bad))
        assert r.status != "ok" and "giá trị chính sách" in (r.error or ""), bad
    await _ok(qt, "core.save_skill", _skill("duoc", instructions="Chọn 5 khách quá hạn lâu nhất, mỗi khách 3 câu."))   # số đếm cách làm thì được
    r = await executor.run(qt, "core.save_skill", _skill("cong_cu_la", tools=["crm.khong_co"]))
    assert r.status != "ok" and "crm.khong_co" in (r.error or "")
    with pytest.raises(ValueError):
        skill_specs.declare("x" * 2, title="T", when_to_use="Khi", roles=("sale",), instructions="Mức phí 2% cho mọi căn")


async def test_pack_defaults_are_installed_once_and_company_edits_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    key = f"mac_dinh_{uuid.uuid4().hex[:6]}"
    record = skill_specs.declare(key, title="Ưu tiên gọi khách", when_to_use="Sale hỏi hôm nay gọi ai trước", roles=("sale",),
                                 instructions=GUIDE, tools=("crm.brief",))
    monkeypatch.setitem(skill_specs.DECLARED, key, skill_specs.DECLARED[key])   # lời khai chỉ sống trong test này (gỡ khi xong)
    crm = registry.PACKS["crm"]
    monkeypatch.setitem(crm.__dict__, "records", (*crm.records, record))   # gói mẫu mang thêm một kỹ năng mặc định (chỉ trong test này)
    tid, d = await _company()
    qt, gd, sale = [await seed.who(tid, u, d) for u in ("qt", "gd", "sale1")]
    assert await owner_val("SELECT json_build_array(version, status, source, pack, author_id) FROM core.ai_skills WHERE tenant_id = %s AND key = %s",
                           (tid, key)) == [1, "active", "pack", "crm", None]
    _, model = await _turn(sale, "Hôm nay gọi ai trước?", ["Dạ."])
    assert "core__use_skill" in model.tools_seen[0]
    # công ty sửa (bản 2 đang dùng) rồi ngừng ⇒ cài lại mặc định (nâng cấp bản cài) không thêm, không ghi đè, không bật lại
    await _activate(qt, gd, {**_skill(key), "instructions": GUIDE + "\n3. Cách của công ty."})
    await _ok(gd, "core.retire_skill", {"key": key})
    before = await owner_val("SELECT count(*) FROM core.ai_skills WHERE tenant_id = %s AND key <> 'tim_vi_sao'", (tid,))
    await _ok(system_principal(tid), "admin.install_defaults", {"packs": ["base", "crm"]})
    await owner_val("DELETE FROM core.pack_installs WHERE tenant_id = %s AND kind = 'record' AND key LIKE 'core.install_skill:%%' RETURNING 1", (tid,))
    await _ok(system_principal(tid), "admin.install_defaults", {"packs": ["base", "crm"]})   # kể cả mất nhật ký cài: lệnh tự giữ bản công ty
    assert await owner_val("SELECT count(*) FROM core.ai_skills WHERE tenant_id = %s AND key <> 'tim_vi_sao'", (tid,)) == before
    assert await owner_val("SELECT json_agg(status ORDER BY version) FROM core.ai_skills WHERE tenant_id = %s AND key = %s",
                           (tid, key)) == ["retired", "retired"]
    # người gọi chỉ nêu khoá: không có lời khai của gói ⇒ không cài được gì (không có đường đưa nội dung chưa duyệt vào "đang dùng")
    r = await executor.run(qt, "core.install_skill", {"key": key})            # hợp đồng nội bộ của cài mặc định: người không gọi thẳng được
    assert r.status != "ok"
    r = await executor.run(system_principal(tid), "core.install_skill", {"key": "khong_co_trong_goi"})
    assert r.status != "ok"                                                   # không có lời khai của gói ⇒ không cài được gì


async def test_my_month_counts_only_my_own_work() -> None:
    tid, d = await _company()
    sale, lead = [await seed.who(tid, u, d) for u in ("sale1", "lead")]
    out = await _ok(sale, "crm.create_lead", {"name": "Khách Thử Tháng", "phone": fresh_phone(), "offering_code": ""})
    lead_id = out["lead_id"]
    await _ok(sale, "crm.log_activity", {"lead_id": lead_id, "kind": "call", "outcome": "connected", "note": "Đã gọi"})
    await _ok(sale, "crm.log_activity", {"lead_id": lead_id, "kind": "note", "note": "Ghi chú nội bộ — không tính liên hệ"})
    r = await executor.query(sale, "crm.my_month", {})
    assert r.status == "ok", r.error
    table = next(b["page"] for b in r.output["blocks"] if b["type"] == "table")
    now = {row["id"]: row["now"] for row in table["rows"]}
    assert now["contacted"] == 1 and now["touches"] == 1 and now["talked"] == 1
    r = await executor.query(lead, "crm.my_month", {})                       # trưởng nhóm thấy lead của đội nhưng số là việc của CHÍNH mình
    table = next(b["page"] for b in r.output["blocks"] if b["type"] == "table")
    assert {row["id"]: row["now"] for row in table["rows"]}["contacted"] == 0
    assert (await executor.query(sale, "crm.my_month", {"month": "2099-01"})).status != "ok"
