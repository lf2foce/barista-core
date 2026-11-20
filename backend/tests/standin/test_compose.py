"""SOẠN THẢO lát 1 (0118) trên hồ sơ của một gói ngành, qua gói thế vai: bản LÕI (ADR 0190) của ``barista-packs:real_estate/tests/test_compose.py`` — mẫu có phiên
bản, bản soạn điền từ hồ sơ (đơn dịch vụ) bằng quyền người soạn, nguồn từng ô, cập nhật số khi hồ sơ đổi, ban hành đi đúng ``papers.issue`` + tệp cất kho,
ô công ty không bao giờ do người gõ, bản soạn ẩn với người không đọc được hồ sơ nguồn. Gói thế vai khai danh mục ô / số liệu / "ai thấy hồ sơ" qua ba
điểm cắm của ``papers``; công ty thế vai chưa có mẫu nào ⇒ test tự tạo + phát hành mẫu (``slip_template``)."""

import base64
import io
from typing import Any
from uuid import uuid4

import pytest
from docx import Document

from barista.db import db
from barista.kernel import events, executor, files
from barista.kernel.clock import today
from barista.kernel.executor import core_ctx
from barista.kernel.principal import Principal, system_principal
from barista.modules.papers.template import fmt
from tests.conftest import Who, approved, ok, owner_val, q
from tests.standin import pack
from tests.standin.conftest import DOMAIN

SLIP_NAME = "Phiếu đặt hàng"
SLIP = """# Phiếu đặt hàng
Hôm nay, {{draft_date|dai}}, {{entity_name}} (mã số thuế {{entity_tax_code}}, địa chỉ {{entity_address}}) lập phiếu đặt hàng cho đơn {{order_code}}.
## Điều 1. Bên đặt hàng
- Khách hàng: {{customer_name}}
- Địa chỉ liên hệ: {{customer_address}}
- Nội dung đơn: {{order_title}}
{{#if signed_on}}
- Ngày ký hợp đồng: {{signed_on}}
{{/if}}
## Điều 2. Giá trị và lịch thanh toán
Bên đặt hàng thanh toán {{amount_vnd}} (bằng chữ: {{amount_vnd|chu}}) theo lịch dưới đây.
{{stages}}"""
ADDRESS = "Địa chỉ liên hệ của khách"


# ── Với DB ──

async def slip_template(admin: Principal) -> str:
    """Mẫu phiếu đặt hàng của công ty thế vai (đã phát hành) — tạo một lần mỗi DB test; mỗi lần gọi đặt lại thông tin công ty / pháp nhân in trên giấy
    (test khác cùng DB có thể đã đổi)."""
    assert {"value": f"{pack.ORDER}/phieu_xac_nhan_don", "label": "Phiếu xác nhận đơn (Đơn dịch vụ)"} in await q(admin, "papers.starter_options")
    await ok(admin, "papers.set_settings", company_name="Công ty cổ phần Thế Vai", short_name="TV", address="Số 1 phố Mẫu, Hà Nội", tax_code="0100000001",
             phone="0100000000", bank_account="0000 0000 0000", bank_name="Ngân hàng Mẫu — chi nhánh Hà Nội", signer_name="Nguyễn Văn Mẫu")
    e = await owner_val("SELECT row_to_json(e) FROM core.legal_entities e WHERE tenant_id = %s AND is_default", (admin.tenant_id,))
    await ok(admin, "admin.upsert_entity", code=e["code"], name=e["name"], tax_code="0100000001", address="Số 1 phố Mẫu, Hà Nội", is_default=True)
    have = next((o["value"] for o in await q(admin, "papers.template_options") if o["label"].startswith(SLIP_NAME)), None)
    if have is None:
        t = await ok(admin, "papers.create_template", name=SLIP_NAME, key="phieu_dat_hang", subject_kind=pack.ORDER, abbr="PDH", body=SLIP,
                     signers="BÊN CUNG CẤP | BÊN ĐẶT HÀNG")
        await ok(admin, "papers.publish_template", template_id=str(t["template_id"]))
        have = next(o["value"] for o in await q(admin, "papers.template_options") if o["label"].startswith(SLIP_NAME))
    return have


async def new_order(admin: Principal, sale: Principal, customer: str = "Khách Soạn Thử") -> str:
    out = await ok(admin, "orderdesk.create", title="Tư vấn triển khai", customer=customer, owner_email=sale.email, amount_vnd=5_000_000_000)
    await events.drain()
    return out["code"]


def docx_text(b64: str) -> str:
    doc = Document(io.BytesIO(base64.b64decode(b64)))
    cells = [c.text for t in doc.tables for row in t.rows for c in row.cells]
    return "\n".join([p.text for p in doc.paragraphs] + cells)


def section(page: dict[str, Any], title: str) -> dict[str, Any]:
    return next(s for s in page["sections"] if s["title"].startswith(title))


async def test_order_slip_from_an_order_end_to_end(who: Who) -> None:
    admin, sale = await who("admin"), await who("sale1")
    tid = await slip_template(admin)
    code = await new_order(admin, sale)

    draft = await ok(sale, "papers.start_draft", template_id=tid, ref=code.lower())
    assert draft["rev"] == 1 and draft["missing"] == [ADDRESS]   # đơn không giữ địa chỉ ⇒ người nhập; "Ngày ký" nằm trong mục điều kiện tắt ⇒ không tính
    pid = str(draft["paper_id"])
    page = await q(sale, "papers.view", paper_id=pid)
    facts = {f["label"]: f["value"] for f in section(page, "Dữ kiện")["fields"]}
    assert facts["Giá trị đơn"] == f"5.000.000.000 đồng — từ hồ sơ {code}" and facts["Tên pháp nhân ký"].endswith("— thông tin công ty")
    assert facts[ADDRESS] == "(thiếu)" and "Còn thiếu" in [s["title"][:9] for s in page["sections"]]
    assert section(page, "Điều 2").get("text", "").startswith("Bên đặt hàng thanh toán 5.000.000.000 đồng (bằng chữ: Năm tỷ đồng)")
    plan = next(s for s in page["sections"] if s.get("table"))["table"]
    assert plan["columns"] == ["Đợt", "Nội dung", "Hạn", "Số tiền"] and plan["total"] == ["Tổng cộng", "5.000.000.000 đồng"]
    assert {a["command"] for a in page["actions"]} == {"papers.refresh_draft", "papers.set_field"}   # sale không có quyền ban hành
    assert "papers.issue" in {a["command"] for a in (await q(admin, "papers.view", paper_id=pid))["actions"]}

    text = docx_text((await q(sale, "papers.export", paper_id=pid))["docx_b64"])
    assert "DỰ THẢO" in text and "PHIẾU ĐẶT HÀNG" in text and "Năm tỷ đồng" in text and "BÊN ĐẶT HÀNG" in text and "Đặt cọc" in text

    # ô thiếu ⇒ không ban hành, lời báo nói rõ ô nào
    r = await executor.run(admin, "papers.issue", {"paper_id": pid})
    assert r.status == "invalid" and f"còn thiếu: {ADDRESS}" in (r.error or "")

    # người nhập dữ kiện: ghi tên + lúc + căn cứ; ô hồ sơ đã có giá trị thì không sửa ở đây
    assert [o["value"] for o in await q(sale, "papers.fillable_field_options", paper_id=pid)] == ["customer_address"]
    bad = await executor.run(sale, "papers.set_field", {"paper_id": pid, "key": "amount_vnd", "value": "1.000.000"})
    assert bad.status == "invalid" and "hồ sơ gốc" in (bad.error or "")
    got = await ok(sale, "papers.set_field", paper_id=pid, key="customer_address", value="Số 9 phố Mẫu, Hà Nội", reason="khách cung cấp")
    assert got["rev"] == 2 and got["missing"] == []
    person = await owner_val("""SELECT row_to_json(f) FROM papers.fields f WHERE document_id = %s AND rev = 2 AND key = 'customer_address'""",
                             (draft["paper_id"],))
    assert person["origin"] == "person" and person["entered_by"] == str(sale.user_id) and person["reason"] == "khách cung cấp"

    # hồ sơ đổi sau khi soạn (đơn được ký ⇒ có ngày ký) ⇒ ban hành bị chặn, "Cập nhật số" ra bản mới kèm thay đổi, dữ kiện người nhập vẫn giữ
    await ok(admin, "orderdesk.sign", code=code)
    await events.drain()
    day = f"{today():%d/%m/%Y}"
    r = await executor.run(admin, "papers.issue", {"paper_id": pid})
    assert r.status == "invalid" and "Hồ sơ đã đổi sau bản soạn (Ngày ký)" in (r.error or "")
    fresh = await ok(sale, "papers.refresh_draft", paper_id=pid)
    assert fresh["rev"] == 3 and [(c["label"], c["before"], c["after"]) for c in fresh["changes"]] == [("Ngày ký", "(trống)", day)]
    assert (await ok(sale, "papers.refresh_draft", paper_id=pid))["changes"] == []          # không đổi gì ⇒ không ra bản
    page = await q(sale, "papers.view", paper_id=pid)
    assert section(page, "Thay đổi ở bản 3")["fields"] == [{"label": "Ngày ký", "value": f"(trống) → {day}", "format": "text"}]
    assert f"người nhập: {sale.name}" in {f["label"]: f["value"] for f in section(page, "Dữ kiện")["fields"]}[ADDRESS]

    # ban hành: số theo loại của mẫu (dãy riêng), tệp .docx cất ở kho tệp, tải lại đúng tệp đó kể cả khi hồ sơ đổi tiếp
    out = await ok(admin, "papers.issue", paper_id=pid)
    assert "/PDH-" in out["symbol"]
    fid = await owner_val("SELECT file_id FROM papers.files WHERE document_id = %s AND role = 'issued'", (draft["paper_id"],))
    assert fid is not None and await owner_val("SELECT module FROM core.files WHERE id = %s", (fid,)) == "papers"
    first = (await q(admin, "papers.export", paper_id=pid))["docx_b64"]
    assert f"Số: {out['symbol']}" in docx_text(first) and "DỰ THẢO" not in docx_text(first) and f"Ngày ký hợp đồng: {day}" in docx_text(first)
    # dữ kiện người nhập in CÓ đánh dấu: số chú thích sau giá trị + dòng "do ai nhập ngày nào" cuối văn bản
    assert "Địa chỉ liên hệ: Số 9 phố Mẫu, Hà Nội [1]" in docx_text(first)
    assert f"[1] {ADDRESS}: thông tin do {sale.name} nhập ngày" in docx_text(first)
    assert out["symbol"] in (await q(admin, "papers.view", paper_id=pid))["sections"][0]["fields"][0]["value"]
    await owner_val("UPDATE orderdesk.orders SET title = 'Tư vấn triển khai — giai đoạn 2' WHERE code = %s AND tenant_id = %s RETURNING 1",
                    (code, admin.tenant_id))                                                # hồ sơ đổi tiếp sau ban hành (gói không có lệnh sửa đơn)
    assert (await q(admin, "papers.export", paper_id=pid))["docx_b64"] == first
    assert (await executor.run(sale, "papers.refresh_draft", {"paper_id": pid})).status == "invalid"   # đã ban hành ⇒ khoá
    revs = await owner_val("SELECT array_agg(via ORDER BY rev) FROM papers.revisions WHERE document_id = %s", (draft["paper_id"],))
    assert revs == ["template", "person", "refresh"]
    listing = await q(sale, "papers.drafts")
    row = next(r for r in listing["rows"] if str(r["id"]) == pid)
    assert row["status"] == "issued" and row["rev"] == 3 and row["missing"] == 0


async def test_composer_needs_to_see_the_record_and_cancelled_orders_are_refused(who: Who) -> None:
    admin, sale1, sale2 = await who("admin"), await who("sale1"), await who("sale2")
    tid = await slip_template(admin)
    code = await new_order(admin, sale1)
    r = await executor.run(sale2, "papers.start_draft", {"template_id": tid, "ref": code})   # sale khác, phạm vi "của mình"
    assert r.status == "not_found" and "không có quyền xem" in (r.error or "")
    r = await executor.run(sale1, "papers.start_draft", {"template_id": tid, "ref": "DH-KHONG-CO"})
    assert r.status == "not_found"
    await approved(admin, "orderdesk.cancel", code=code, reason="khách đổi ý")   # huỷ đơn qua duyệt
    assert await owner_val("SELECT status FROM orderdesk.orders WHERE code = %s AND tenant_id = %s", (code, admin.tenant_id)) == "cancelled"
    r = await executor.run(sale1, "papers.start_draft", {"template_id": tid, "ref": code})
    assert r.status == "invalid" and "đã huỷ" in (r.error or "")


async def test_templates_are_versioned_and_published_versions_never_change(who: Who) -> None:
    admin, sale = await who("admin"), await who("sale1")
    await slip_template(admin)                                                              # thông tin công ty / pháp nhân in trên giấy
    t = await ok(admin, "papers.create_template", starter=f"{pack.ORDER}/phieu_xac_nhan_don", key="phieu_thu_phien_ban",
                 name="Phiếu thử phiên bản", abbr="PTV")                                    # thân + bên ký lấy từ mẫu gợi ý của gói
    tid = str(t["template_id"])
    assert (await executor.run(sale, "papers.start_draft", {"template_id": tid, "ref": "x"})).status == "not_found"   # chưa phát hành
    await ok(admin, "papers.publish_template", template_id=tid)
    code = await new_order(admin, sale)
    first = await ok(sale, "papers.start_draft", template_id=tid, ref=code)
    assert first["missing"] == []
    # mẫu chỉ dùng ô trong danh mục của loại hồ sơ; sửa mẫu cần quyền mẫu văn bản
    bad = await executor.run(admin, "papers.save_template_version", {"template_id": tid, "body": "# Phiếu\n{{khong_co}}"})
    assert bad.status == "invalid" and "khong_co" in (bad.error or "")
    assert (await executor.run(sale, "papers.save_template_version", {"template_id": tid, "body": "# Phiếu"})).status == "denied"
    v2 = await ok(admin, "papers.save_template_version", template_id=tid, body="# Phiếu rút gọn\nKhách {{customer_name}}, đơn {{order_code}}.",
                  signers="BÊN A | BÊN B")
    assert v2["version"] == 2
    view = await q(admin, "papers.template_view", template_id=tid)
    assert any(a["command"] == "papers.publish_template" for a in view["actions"])
    assert "{{customer_name}}" in [f["label"] for f in section(view, "Ô dùng được")["fields"]]
    # bản đã phát hành không sửa được, kể cả bằng SQL
    with pytest.raises(Exception, match="đã phát hành"):
        await owner_val("UPDATE papers.template_versions SET body = 'x' WHERE template_id = %s AND version = 1 RETURNING 1", (tid,))
    await ok(admin, "papers.publish_template", template_id=tid)
    second = await ok(sale, "papers.start_draft", template_id=tid, ref=code)
    assert section(await q(sale, "papers.view", paper_id=str(second["paper_id"])), "Thông tin")["fields"][1]["value"] == "Phiếu thử phiên bản — v2"
    # bản soạn trước giữ mẫu v1 (cập nhật số vẫn ráp theo v1: còn bảng lịch thanh toán mà v2 đã bỏ)
    await owner_val("UPDATE orderdesk.orders SET title = 'Tư vấn vận hành' WHERE code = %s AND tenant_id = %s RETURNING 1", (code, admin.tenant_id))
    again = await ok(sale, "papers.refresh_draft", paper_id=str(first["paper_id"]))
    assert again["rev"] == 2 and [c["label"] for c in again["changes"]] == ["Nội dung đơn"]
    page = await q(sale, "papers.view", paper_id=str(first["paper_id"]))
    assert section(page, "Thông tin")["fields"][1]["value"] == "Phiếu thử phiên bản — v1" and any(s.get("table") for s in page["sections"])
    assert "Tư vấn vận hành" in str(page["sections"])
    # mỗi mẫu một dãy số riêng
    assert (await ok(admin, "papers.issue", paper_id=str(first["paper_id"])))["symbol"].startswith("01/PTV-")


async def test_agent_proposes_a_draft_and_cannot_issue(who: Who) -> None:
    admin, sale = await who("admin"), await who("sale1")
    tid = await slip_template(admin)
    code = await new_order(admin, sale)
    r = await executor.run(sale.as_agent(), "papers.start_draft", {"template_id": tid, "ref": code})
    assert r.status == "proposed" and r.output["preview"]["missing"] == [ADDRESS]
    pid = (await ok(sale, "papers.start_draft", template_id=tid, ref=code))["paper_id"]
    assert (await executor.run(admin.as_agent(), "papers.issue", {"paper_id": str(pid)})).status == "denied"


async def test_issued_file_is_readable_only_through_the_paper(who: Who) -> None:
    """Tệp ban hành thuộc module papers (0122): module khác biết file_id cũng không mở được."""
    admin, sale = await who("admin"), await who("sale1")
    tid = await slip_template(admin)
    code = await new_order(admin, sale)
    pid = (await ok(sale, "papers.start_draft", template_id=tid, ref=code))["paper_id"]
    await ok(sale, "papers.set_field", paper_id=str(pid), key="customer_address", value="Số 1 phố Mẫu")
    await ok(admin, "papers.issue", paper_id=str(pid))
    fid = await owner_val("SELECT file_id FROM papers.files WHERE document_id = %s", (pid,))
    async with db.tenant_tx(admin, rollback=True) as conn:
        with pytest.raises(Exception, match="phân hệ khác"):
            await files.read(core_ctx(conn, admin, module="kb"), fid)


async def test_company_facts_are_never_typed_on_a_paper(who: Who) -> None:
    """Review 08/11: sale gõ số tài khoản công ty vào ô trống ⇒ quản trị ban hành ⇒ giấy in tài khoản lạ. Ô công ty / pháp nhân chỉ lấy từ cấu hình
    công ty; trống ⇒ chặn ban hành, lời báo chỉ chỗ sửa. Kể cả bản cũ lỡ có dòng "người nhập" cho ô đó cũng không được in."""
    admin, sale = await who("admin"), await who("sale1")
    await slip_template(admin)
    t = await ok(admin, "papers.create_template", name="Giấy thử tài khoản", key="thu_tai_khoan", subject_kind=pack.ORDER, abbr="TK",
                 body="# Giấy thử\nChuyển khoản vào {{company_bank_account}} tại {{company_bank_name}}. Khách {{customer_name}}.")
    await ok(admin, "papers.publish_template", template_id=str(t["template_id"]))
    old = await owner_val("SELECT bank_account FROM papers.settings WHERE tenant_id = %s", (admin.tenant_id,))
    assert old
    await owner_val("UPDATE papers.settings SET bank_account = '' WHERE tenant_id = %s RETURNING 1", (admin.tenant_id,))
    try:
        d = await ok(sale, "papers.start_draft", template_id=str(t["template_id"]), ref=await new_order(admin, sale))
        pid = str(d["paper_id"])
        assert d["missing"] == ["Số tài khoản công ty"]
        assert await q(sale, "papers.fillable_field_options", paper_id=pid) == []
        r = await executor.run(sale, "papers.set_field", {"paper_id": pid, "key": "company_bank_account", "value": "9999 SALE OWN ACCOUNT"})
        assert r.status == "invalid" and "thông tin công ty / pháp nhân" in (r.error or "")
        r = await executor.run(admin, "papers.issue", {"paper_id": pid})
        assert r.status == "invalid" and "thông tin công ty / pháp nhân còn trống: Số tài khoản công ty" in (r.error or "")
        assert "Quản trị › Pháp nhân hoặc Thông tin công ty trên giấy tờ" in (r.error or "")
        # dòng "người nhập" giả cho ô công ty (bản cũ / ghi tay vào DB) ⇒ vẫn không in, vẫn chặn
        await owner_val("""UPDATE papers.fields SET value = '"9999 SALE OWN ACCOUNT"', display = '9999 SALE OWN ACCOUNT', origin = 'person',
                           entered_by = %s, entered_at = now() WHERE document_id = %s AND key = 'company_bank_account' RETURNING 1""", (sale.user_id, d["paper_id"]))
        await owner_val("UPDATE papers.revisions SET missing = '{}' WHERE document_id = %s RETURNING 1", (d["paper_id"],))
        r = await executor.run(admin, "papers.issue", {"paper_id": pid})
        assert r.status == "invalid" and "Số tài khoản công ty" in (r.error or "")
        refreshed = await ok(sale, "papers.refresh_draft", paper_id=pid)
        assert refreshed["missing"] == ["Số tài khoản công ty"]
        assert "9999" not in docx_text((await q(sale, "papers.export", paper_id=pid))["docx_b64"])
    finally:
        await owner_val("UPDATE papers.settings SET bank_account = %s WHERE tenant_id = %s RETURNING 1", (old, admin.tenant_id))
    await ok(sale, "papers.refresh_draft", paper_id=pid)
    assert "/TK-" in (await ok(admin, "papers.issue", paper_id=pid))["symbol"]


async def test_drafts_are_hidden_from_people_who_cannot_read_the_source_record(who: Who) -> None:
    """Review 08/11: bản soạn chép số liệu hồ sơ ⇒ vai có giấy tờ "toàn công ty" mà không đọc được hồ sơ nguồn (vd trưởng phòng phân tích) không thấy
    bản soạn ở danh sách, tìm nhanh, xem, tải; giấy không theo mẫu vẫn theo luật cũ."""
    admin, sale = await who("admin"), await who("sale1")
    await approved(admin, "admin.upsert_role", key="research_head", label="Trưởng phòng phân tích")
    await approved(admin, "admin.set_grant", role="research_head", resource="papers", scope="all", actions=["read", "write"])
    local = f"rh{uuid4().hex[:8]}"
    await approved(admin, "admin.invite_user", email=f"{local}@{DOMAIN}", name="Trưởng phân tích thử", role="research_head", team_code="", is_lead=False)
    rh = await who(local)
    assert rh.can("papers", "read") and not rh.can("orderdesk", "read")
    pid = str((await ok(sale, "papers.start_draft", template_id=await slip_template(admin), ref=await new_order(admin, sale, "Khách Ẩn Thử")))["paper_id"])
    letter = await ok(rh, "papers.draft_letter", recipient="Phòng thử", subject="thử", body="Nội dung")
    assert pid not in {str(r["id"]) for r in (await q(rh, "papers.drafts"))["rows"]}
    listed = {str(r["id"]) for r in (await q(rh, "papers.list"))["rows"]}
    assert pid not in listed and str(letter["paper_id"]) in listed
    for op in ("papers.view", "papers.export"):
        assert (await executor.query(rh, op, {"paper_id": pid})).status == "not_found", op
    assert await q(rh, "papers.fillable_field_options", paper_id=pid) == []
    assert (await executor.run(rh, "papers.set_field", {"paper_id": pid, "key": "customer_address", "value": "x"})).status == "not_found"

    def found(page: Any) -> set[str]:   # ⌘K: mã giấy mở được từ kết quả tìm
        return {str((i.get("open") or {}).get("input", {}).get("paper_id")) for b in page.get("blocks", []) for i in b.get("items", [])}

    assert pid not in found(await q(rh, "core.find", q="PHIẾU ĐẶT HÀNG")) and pid in found(await q(admin, "core.find", q="PHIẾU ĐẶT HÀNG"))
    assert pid in {str(r["id"]) for r in (await q(admin, "papers.drafts"))["rows"]}       # người đọc được đơn vẫn thấy


async def test_control_characters_are_refused_and_never_reach_the_docx(who: Who) -> None:
    """Review 08/11: ký tự điều khiển trong dữ kiện người nhập làm hỏng .docx / PDF / ban hành (lỗi 500) ⇒ từ chối ở đầu vào."""
    admin, sale = await who("admin"), await who("sale1")
    pid = str((await ok(sale, "papers.start_draft", template_id=await slip_template(admin), ref=await new_order(admin, sale)))["paper_id"])
    for inp in ({"value": "Số 1\x01"}, {"value": "Số 1", "reason": "x\x07"}):
        r = await executor.run(sale, "papers.set_field", {"paper_id": pid, "key": "customer_address", **inp})
        assert r.status == "invalid" and "ký tự điều khiển" in (r.error or ""), inp
    bad = await executor.run(admin, "papers.save_template_version", {"template_id": await slip_template(admin), "body": "# A\x02"})
    assert bad.status == "invalid" and "ký tự điều khiển" in (bad.error or "")
    assert fmt("Tên\x0bkhách\x00", "text") == "Tênkhách"   # chữ từ hồ sơ: bỏ ký tự điều khiển khi in
    await ok(sale, "papers.set_field", paper_id=pid, key="customer_address", value="Số 1 phố Mẫu")
    assert "Số 1 phố Mẫu [1]" in docx_text((await q(sale, "papers.export", paper_id=pid))["docx_b64"])


async def test_template_series_never_collide_with_pack_paper_kinds(who: Who) -> None:
    admin, sale = await who("admin"), await who("sale1")
    pid = (await ok(sale, "papers.start_draft", template_id=await slip_template(admin), ref=await new_order(admin, sale)))["paper_id"]
    assert await owner_val("SELECT kind FROM papers.documents WHERE id = %s", (pid,)) == "mau_phieu_dat_hang"
    r = await executor.run(system_principal(admin.tenant_id), "papers.save_draft",
                           {"kind": "mau_phieu_dat_hang", "kind_label": "x", "summary": "x", "content": {}})
    assert r.status == "invalid" and "mau_" in (r.error or "")
