"""Trường tuỳ biến theo công ty (0060 lát 1): MỘT định nghĩa ⇒ form, schema công cụ trợ lý, server cùng thấy; trường lạ / sai kiểu bị chặn ở executor
(mọi cửa); giá trị nằm ở bảng của module; ngừng trường ⇒ ẩn, giá trị còn; định nghĩa đổi giữa lúc soạn và lúc xác nhận ⇒ kiểm lại."""

import uuid

import httpx

from barista.agent import chat
from barista.http_api import app
from barista.kernel import executor, extra_fields, registry
from barista.kernel.forms import field_specs
from tests.conftest import Who, fresh_phone, ok, owner_val, q


def test_fold_and_check_are_strict() -> None:
    defs = (extra_fields.FieldDef("lead", "cong_to", "Mã công tơ", "text"), extra_fields.FieldDef("lead", "so_xe", "Số xe", "number"),
            extra_fields.FieldDef("lead", "ngay_ve", "Ngày về", "date"), extra_fields.FieldDef("lead", "hang", "Hạng", "choice", ("Vàng", "Bạc")))
    assert extra_fields.fold({"name": "A", "x_cong_to": "CT-1", "x_so_xe": ""}) == {"name": "A", "extra": {"cong_to": "CT-1"}}
    assert extra_fields.fold({"name": "A", "extra": {"hang": "Vàng"}, "x_hang": " "}) == {"name": "A", "extra": {"hang": "Vàng"}}   # ô trống không xoá
    assert extra_fields.fold({"name": "A"}) == {"name": "A"}
    out = extra_fields.check(defs, {"cong_to": "  CT  1 ", "so_xe": "2", "ngay_ve": "31/12/2026", "hang": "Bạc"})
    assert out == {"cong_to": "CT 1", "so_xe": 2, "ngay_ve": "2026-12-31", "hang": "Bạc"}
    assert extra_fields.check(defs, {"so_xe": None, "hang": ""}) == {}
    assert extra_fields.check(defs, {"so_xe": "1,5"}) == {"so_xe": 1.5}
    # số khổng lồ ("1e999999") phải bị từ chối TRƯỚC khi tới int() — review an ninh 15/11
    for bad in ({"la": 1}, {"so_xe": "hai"}, {"so_xe": True}, {"so_xe": "1e999999"}, {"so_xe": "1e15"}, {"so_xe": "9" * 40}, {"so_xe": "1e-9"},
                {"ngay_ve": "31/02/2026"}, {"hang": "Đồng"}, {"cong_to": {"a": 1}}):
        try:
            extra_fields.check(defs, bad)
        except extra_fields.Invalid:
            continue
        raise AssertionError(f"phải từ chối {bad}")
    assert extra_fields.shown({"lead": defs}, "lead", {"ngay_ve": "2026-12-31", "hang": "Bạc", "cu": "x"}) == [("Ngày về", "31/12/2026"), ("Hạng", "Bạc")]
    assert extra_fields.shown(None, "lead", {"hang": "Bạc"}) == []


async def test_one_definition_reaches_form_assistant_and_server(who: Who) -> None:
    admin = await who("admin")
    tag = uuid.uuid4().hex[:5]
    made = await ok(admin, "admin.save_field", subject="lead", label=f"Mã công tơ {tag}", kind="text")
    rank = await ok(admin, "admin.save_field", subject="lead", label=f"Hạng thẻ {tag}", kind="choice", options="Vàng, Bạc")
    k_text, k_rank = made["key"], rank["key"]
    assert extra_fields.KEY.fullmatch(k_text) and k_text != k_rank
    sale = await who("sale1")                                                    # người gọi nạp lại ⇒ mang định nghĩa mới
    op = registry.get("crm.create_lead")
    assert op is not None and op.extra == "lead"

    specs = {s["key"]: s for s in field_specs(op, sale.vocab, sale.fields)}
    assert "extra" not in specs and specs[f"x_{k_text}"]["label"] == f"Mã công tơ {tag}" and specs[f"x_{k_rank}"]["kind"] == "select"
    assert "extra" not in {s["key"] for s in field_specs(op)}                    # không có định nghĩa ⇒ ô thô vẫn không lộ
    schema = chat.tool_schema(op, sale.vocab, sale.fields)["properties"]
    assert "extra" not in schema and schema[f"x_{k_rank}"]["enum"] == ["Vàng", "Bạc"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        form = (await c.get("/v1/forms/crm.create_lead", headers={"X-Dev-User": sale.email, "X-Tenant-Id": str(sale.tenant_id)})).json()
    assert f"x_{k_text}" in {f["key"] for f in form["fields"]}                    # form app vẽ có ô mới, không sửa app

    name = f"Khách trường riêng {tag}"
    base = {"name": name, "phone": fresh_phone(), "offering_code": "HSR"}
    for bad in ({"x_khong_co": "1"}, {f"x_{k_rank}": "Đồng"}, {"extra": {"khong_co": 1}}):   # trường lạ / sai lựa chọn — cả dạng phẳng lẫn dạng gộp
        r = await executor.run(sale, "crm.create_lead", {**base, **bad})
        assert r.status == "invalid", (bad, r.status, r.error)
    lead = await ok(sale, "crm.create_lead", **base, **{f"x_{k_text}": "CT-88", f"x_{k_rank}": "Vàng"})
    assert await owner_val("SELECT extra FROM crm.leads WHERE id = %s", (lead["lead_id"],)) == {k_text: "CT-88", k_rank: "Vàng"}
    card = str(await q(sale, "crm.journey", lead_id=str(lead["lead_id"])))
    assert "CT-88" in card and f"Hạng thẻ {tag}" in card

    r = await executor.run(executor.principal.system_principal(sale.tenant_id), "crm.create_lead",
                           {**base, "phone": fresh_phone(), "owner_email": sale.email, f"x_{k_text}": "x"})
    assert r.status == "invalid"                                                 # người gọi không mang định nghĩa ⇒ giá trị tuỳ biến bị từ chối (đóng khi không rõ)

    await ok(sale, "crm.set_lead_fields", lead_id=str(lead["lead_id"]), **{f"x_{k_rank}": "Bạc"})
    assert await owner_val("SELECT extra FROM crm.leads WHERE id = %s", (lead["lead_id"],)) == {k_text: "CT-88", k_rank: "Bạc"}   # ô trống giữ nguyên

    # trợ lý soạn đề xuất ⇒ quản trị ngừng trường ⇒ người bấm Xác nhận: kiểm lại theo định nghĩa hiện tại, không ghi trường đã ngừng
    draft = await executor.propose(sale.as_agent(), "crm.set_lead_fields", {"lead_id": str(lead["lead_id"]), f"x_{k_text}": "CT-99"})
    assert draft.status == "proposed" and draft.output["input"]["extra"] == {k_text: "CT-99"}
    await ok(admin, "admin.save_field", subject="lead", key=k_text, label=f"Mã công tơ {tag}", kind="text", active=False)
    sale = await who("sale1")
    assert f"x_{k_text}" not in {s["key"] for s in field_specs(op, sale.vocab, sale.fields)}   # ngừng ⇒ ẩn khỏi form
    r = await executor.run(sale, "crm.set_lead_fields", draft.output["input"])
    assert r.status == "invalid"
    assert (await owner_val("SELECT extra FROM crm.leads WHERE id = %s", (lead["lead_id"],)))[k_text] == "CT-88"   # giá trị đã ghi vẫn còn
    assert "CT-88" not in str(await q(sale, "crm.journey", lead_id=str(lead["lead_id"])))                          # nhưng không hiện nữa

    r = await executor.run(admin, "admin.save_field", {"subject": "lead", "key": k_rank, "label": "Hạng", "kind": "text"})
    assert r.status == "invalid"                                                 # không đổi kiểu trường đã tạo
    r = await executor.run(sale, "admin.save_field", {"subject": "lead", "label": "Lén thêm", "kind": "text"})
    assert r.status == "denied"                                                  # chỉ quản trị định nghĩa trường
