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


async def issue(p: Principal, pid: str) -> executor.Result:
    """Bấm Phát hành như app: mở giấy, gửi lại căn cứ nút mang theo (0251 — giấy theo hồ sơ bắt buộc có căn cứ)."""
    page = await q(p, "papers.view", paper_id=pid)
    btn = next((a for a in page["actions"] if a["command"] == "papers.issue"), None)
    return await executor.run(p, "papers.issue", {"paper_id": pid}, expect_basis=executor.basis_of_token(btn["basis"]) if btn else None)


async def issued(p: Principal, pid: str) -> dict[str, Any]:
    r = await issue(p, pid)
    assert r.status == "ok", (r.status, r.error)
    return r.output


def field_of(page: dict[str, Any], label: str) -> dict[str, Any]:
    return next(f for s in page["sections"] for f in s["fields"] if f["label"] == label)


async def test_order_slip_reads_live_numbers_and_issues_once(who: Who) -> None:
    """ADR 0251: giấy chưa phát hành luôn là SỐ MỚI NHẤT của hồ sơ; ô thiếu là chỗ trống trên giấy, chạm điền; phát hành chốt số + cấp số + .docx đóng băng."""
    admin, sale = await who("admin"), await who("sale1")
    tid = await slip_template(admin)
    code = await new_order(admin, sale)

    draft = await ok(sale, "papers.start_draft", template_id=tid, ref=code.lower())
    assert draft["missing"] == [ADDRESS] and draft["company_missing"] == []   # "Ngày ký" nằm trong mục điều kiện tắt ⇒ không tính
    pid = str(draft["paper_id"])
    page = await q(sale, "papers.view", paper_id=pid)
    belongs = field_of(page, "Thuộc")
    assert belongs["value"].startswith(f"Đơn dịch vụ {code}") and belongs["open"]["query"] == "orderdesk.case_file"   # chạm mở hồ sơ
    assert "Đang lập" in page["subtitle"] and "Dự thảo" not in page["subtitle"]                # chứng từ giao dịch: không dùng chữ "dự thảo"
    gap = field_of(page, ADDRESS)
    assert gap["value"] == "" and gap["action"]["command"] == "papers.set_field" and gap["action"]["preset"]["key"] == "customer_address"
    sheet = page["sheet"]
    assert sheet["title"] == "PHIẾU ĐẶT HÀNG" and sheet["draft_mark"] == "CHƯA PHÁT HÀNH"
    pay = next(b for b in sheet["blocks"] if b["text"].startswith("Bên đặt hàng thanh toán"))
    assert pay["text"].startswith("Bên đặt hàng thanh toán 5.000.000.000 đồng (bằng chữ: Năm tỷ đồng)")
    assert {"t": "5.000.000.000 đồng", "k": "amount_vnd", "layer": "record"} in pay["runs"]                 # số từ hồ sơ: khoá, sửa ở hồ sơ
    hole = next(r for b in sheet["blocks"] for r in b.get("runs", []) if r.get("k") == "customer_address")
    assert hole["layer"] == "missing" and [a["preset"]["key"] for a in sheet["actions"]] == ["customer_address"]   # ô thiếu: chạm điền ngay trên giấy
    plan = next(b for b in sheet["blocks"] if b["type"] == "table")
    assert plan["columns"] == ["Đợt", "Nội dung", "Hạn", "Số tiền"] and plan["total"] == ["Tổng cộng", "5.000.000.000 đồng"]
    assert page["actions"] == []                                                            # sale lập được, không phát hành (thẩm quyền loại giấy)
    mine = next(r for r in (await q(sale, "papers.list"))["rows"] if str(r["id"]) == pid)
    assert mine["can_discard"] is True and mine["can_issue"] is False                        # bỏ bản đang lập: nút của dòng, không ở thanh trên
    issue_btn = next(a for a in (await q(admin, "papers.view", paper_id=pid))["actions"] if a["command"] == "papers.issue")
    assert issue_btn["title"] == "Phát hành" and issue_btn["basis"]
    assert (await executor.query(await who("giamdoc"), "papers.view", {"paper_id": pid})).status == "not_found"   # chỉ người lập + người phát hành thấy

    text = docx_text((await q(sale, "papers.export", paper_id=pid))["docx_b64"])
    assert "CHƯA PHÁT HÀNH" in text and "PHIẾU ĐẶT HÀNG" in text and "Năm tỷ đồng" in text and "BÊN ĐẶT HÀNG" in text and "Đặt cọc" in text

    # ô thiếu ⇒ không phát hành, lời báo nói rõ ô nào
    r = await issue(admin, pid)
    assert r.status == "invalid" and f"còn thiếu: {ADDRESS}" in (r.error or "")

    # điền ngay trên giấy: ghi tên + lúc + căn cứ; ô hồ sơ đã có giá trị thì không sửa ở đây
    bad = await executor.run(sale, "papers.set_field", {"paper_id": pid, "key": "amount_vnd", "value": "1.000.000"})
    assert bad.status == "invalid" and "hồ sơ gốc" in (bad.error or "")
    await ok(sale, "papers.set_field", paper_id=pid, key="customer_address", value="Số 9 phố Mẫu, Hà Nội", reason="khách cung cấp")
    person = await owner_val("SELECT row_to_json(e) FROM papers.entries e WHERE document_id = %s AND key = 'customer_address'", (draft["paper_id"],))
    assert person["entered_by"] == str(sale.user_id) and person["reason"] == "khách cung cấp"
    assert await owner_val("SELECT count(*) FROM papers.revisions WHERE document_id = %s", (draft["paper_id"],)) == 0   # không còn "bản N" khi đang lập

    # hồ sơ đổi ⇒ giấy tự theo, không nút "cập nhật số"; người đang xem bản cũ bấm phát hành ⇒ "xem lại" (căn cứ 0071), không cấp số
    seen = next(a for a in (await q(admin, "papers.view", paper_id=pid))["actions"] if a["command"] == "papers.issue")["basis"]
    await ok(admin, "orderdesk.sign", code=code)
    await events.drain()
    day = f"{today():%d/%m/%Y}"
    assert f"Ngày ký hợp đồng: {day}" in str((await q(sale, "papers.view", paper_id=pid))["sheet"])
    stale = await executor.run(admin, "papers.issue", {"paper_id": pid}, expect_basis=executor.basis_of_token(seen))
    assert stale.status == "conflict" and "Căn cứ đã đổi từ lúc bạn xem: Số liệu đơn dịch vụ" in (stale.error or "")
    assert await owner_val("SELECT status FROM papers.documents WHERE id = %s", (draft["paper_id"],)) == "draft"

    # phát hành: số theo loại của mẫu (dãy riêng), tệp .docx cất ở kho tệp, tải lại đúng tệp đó kể cả khi hồ sơ đổi tiếp
    fresh = next(a for a in (await q(admin, "papers.view", paper_id=pid))["actions"] if a["command"] == "papers.issue")["basis"]
    out = (await executor.run(admin, "papers.issue", {"paper_id": pid}, expect_basis=executor.basis_of_token(fresh))).output
    assert "/PDH-" in out["symbol"]
    fid = await owner_val("SELECT file_id FROM papers.files WHERE document_id = %s AND role = 'issued'", (draft["paper_id"],))
    assert fid is not None and await owner_val("SELECT module FROM core.files WHERE id = %s", (fid,)) == "papers"
    first = (await q(admin, "papers.export", paper_id=pid))["docx_b64"]
    assert f"Số: {out['symbol']}" in docx_text(first) and "CHƯA PHÁT HÀNH" not in docx_text(first) and f"Ngày ký hợp đồng: {day}" in docx_text(first)
    # dữ kiện người nhập in CÓ đánh dấu: số chú thích sau giá trị + dòng "do ai nhập ngày nào" cuối văn bản
    assert "Địa chỉ liên hệ: Số 9 phố Mẫu, Hà Nội [1]" in docx_text(first)
    assert f"[1] {ADDRESS}: thông tin do {sale.name} nhập ngày" in docx_text(first)
    issued = await q(await who("giamdoc"), "papers.view", paper_id=pid)                          # đã phát hành ⇒ người chỉ đọc thấy, không nút ghi
    assert field_of(issued, "Số")["value"] == out["symbol"] and issued["actions"] == [] and "Đã phát hành" in issued["subtitle"]
    await owner_val("UPDATE orderdesk.orders SET title = 'Tư vấn triển khai — giai đoạn 2' WHERE code = %s AND tenant_id = %s RETURNING 1",
                    (code, admin.tenant_id))                                                # hồ sơ đổi tiếp sau phát hành (gói không có lệnh sửa đơn)
    assert (await q(admin, "papers.export", paper_id=pid))["docx_b64"] == first
    assert "giai đoạn 2" not in str((await q(admin, "papers.view", paper_id=pid))["sheet"])   # trang đã phát hành đọc bản chốt
    assert (await executor.run(sale, "papers.set_field", {"paper_id": pid, "key": "customer_address", "value": "x"})).status == "invalid"
    revs = await owner_val("SELECT array_agg(via ORDER BY rev) FROM papers.revisions WHERE document_id = %s", (draft["paper_id"],))
    assert revs == ["issue"]
    row = next(r for r in (await q(admin, "papers.list", family="record"))["rows"] if str(r["id"]) == pid)
    assert row["state"] == "record.issued" and row["can_issue"] is False

    # hết hiệu lực: chỉ người có thẩm quyền loại giấy; số + tệp giữ nguyên
    assert (await executor.run(sale, "papers.void", {"paper_id": pid})).status == "denied"
    await ok(admin, "papers.void", paper_id=pid)
    assert "Hết hiệu lực" in (await q(admin, "papers.view", paper_id=pid))["subtitle"]
    assert (await q(admin, "papers.export", paper_id=pid))["docx_b64"] == first


async def test_unissued_papers_are_seen_by_author_and_issuer_only(who: Who) -> None:
    """ADR 0251: bản chưa cấp số chỉ người lập + người có thẩm quyền loại đó thấy (RLS); thẩm quyền theo LOẠI giấy — giám đốc ban hành công văn của
    mình, không thấy / không phát hành chứng từ đơn của sale; danh sách việc của người phát hành có dòng "đã lập sẵn"."""
    admin, sale1, sale2, boss = await who("admin"), await who("sale1"), await who("sale2"), await who("giamdoc")
    pid = str((await ok(sale1, "papers.start_draft", template_id=await slip_template(admin), ref=await new_order(admin, sale1)))["paper_id"])
    for p in (sale2, boss):
        assert pid not in {str(r["id"]) for r in (await q(p, "papers.list"))["rows"]}
    assert pid in {str(r["id"]) for r in (await q(sale1, "papers.list"))["rows"]}
    waiting = await q(admin, "papers.pending")
    row = next(r for r in waiting["rows"] if str(r["id"]) == pid)
    assert row["what"].endswith("đã lập sẵn") and row["can_issue"] and row["family"] == "record"
    assert pid not in {str(r["id"]) for r in (await q(boss, "papers.pending"))["rows"]}
    # công văn: họ hành chính — giám đốc soạn thấy nút Ban hành; sale không thấy công văn chưa ban hành của giám đốc
    letter = str((await ok(boss, "papers.draft_letter", recipient="Phòng thử", subject="thử thẩm quyền", body="Nội dung"))["paper_id"])
    acts = {a["command"]: a["title"] for a in (await q(boss, "papers.view", paper_id=letter))["actions"]}
    assert acts["papers.issue"] == "Ban hành" and "Dự thảo" in (await q(boss, "papers.view", paper_id=letter))["subtitle"]
    assert (await executor.query(sale1, "papers.view", {"paper_id": letter})).status == "not_found"
    assert (await executor.run(boss, "papers.issue", {"paper_id": pid})).status == "not_found"     # chứng từ đơn: giám đốc không có thẩm quyền
    await ok(boss, "papers.issue", paper_id=letter)
    assert "Đã ban hành" in (await q(boss, "papers.view", paper_id=letter))["subtitle"]


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
    assert "mẫu v2" in (await q(sale, "papers.view", paper_id=str(second["paper_id"])))["subtitle"]
    # giấy lập trước giữ mẫu v1 (số SỐNG vẫn ráp theo v1: còn bảng lịch thanh toán mà v2 đã bỏ) — hồ sơ đổi thì giấy tự theo, không bấm gì
    await owner_val("UPDATE orderdesk.orders SET title = 'Tư vấn vận hành' WHERE code = %s AND tenant_id = %s RETURNING 1", (code, admin.tenant_id))
    page = await q(sale, "papers.view", paper_id=str(first["paper_id"]))
    assert "mẫu v1" in page["subtitle"] and any(b["type"] == "table" for b in page["sheet"]["blocks"])
    assert "Tư vấn vận hành" in str(page["sheet"])
    # mỗi mẫu một dãy số riêng
    assert (await issued(admin, str(first["paper_id"])))["symbol"].startswith("01/PTV-")


async def test_agent_prepares_a_paper_and_only_a_person_issues(who: Who) -> None:
    """Trợ lý / luật tự động lập sẵn được (chưa cấp số, rủi ro thấp); phát hành luôn do người có thẩm quyền bấm. Một mẫu cho loại hồ sơ ⇒ không hỏi mẫu."""
    admin, sale = await who("admin"), await who("sale1")
    await slip_template(admin)
    code = await new_order(admin, sale)
    only = {r["name"] for r in await owner_val(
        "SELECT coalesce(json_agg(json_build_object('name', name)), '[]') FROM papers.templates WHERE tenant_id = %s AND subject_kind = %s AND active",
        (admin.tenant_id, pack.ORDER))}
    r = await executor.run(sale.as_agent(), "papers.start_draft", {"ref": code, "subject_kind": pack.ORDER})
    if len(only) == 1:
        assert r.status == "ok" and r.output["missing"] == [ADDRESS]
    else:   # bộ test khác đã thêm mẫu cùng loại ⇒ phải chọn
        assert r.status == "invalid" and "chọn một mẫu" in (r.error or "")
        r = await executor.run(sale.as_agent(), "papers.start_draft", {"ref": code, "template_id": await slip_template(admin)})
        assert r.status == "ok"
    assert (await executor.run(admin.as_agent(), "papers.issue", {"paper_id": str(r.output["paper_id"])})).status == "denied"


async def test_issued_file_is_readable_only_through_the_paper(who: Who) -> None:
    """Tệp ban hành thuộc module papers (0122): module khác biết file_id cũng không mở được."""
    admin, sale = await who("admin"), await who("sale1")
    tid = await slip_template(admin)
    code = await new_order(admin, sale)
    pid = (await ok(sale, "papers.start_draft", template_id=tid, ref=code))["paper_id"]
    await ok(sale, "papers.set_field", paper_id=str(pid), key="customer_address", value="Số 1 phố Mẫu")
    await issued(admin, str(pid))
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
        assert d["missing"] == [] and d["company_missing"] == ["Số tài khoản công ty"]   # không nằm trong danh sách thiếu của tờ giấy
        note = field_of(await q(sale, "papers.view", paper_id=pid), "Thông tin công ty")
        assert note["value"].startswith("Công ty chưa khai số tài khoản công ty") and "open" not in note and "action" not in note   # sale: chỉ một dòng
        assert field_of(await q(admin, "papers.view", paper_id=pid), "Thông tin công ty")["open"]["query"] == "papers.settings_view"   # quản trị: tới màn khai
        r = await executor.run(sale, "papers.set_field", {"paper_id": pid, "key": "company_bank_account", "value": "9999 SALE OWN ACCOUNT"})
        assert r.status == "invalid" and "thông tin công ty / pháp nhân" in (r.error or "")
        r = await issue(admin, pid)
        assert r.status == "invalid" and "công ty chưa khai Số tài khoản công ty" in (r.error or "") and "Quản trị khai một lần" in (r.error or "")
        # dòng "người nhập" giả cho ô công ty (ghi tay vào DB) ⇒ vẫn không in, vẫn chặn
        await owner_val("""INSERT INTO papers.entries (tenant_id, document_id, key, value, display, entered_by)
                           VALUES (%s, %s, 'company_bank_account', '"9999 SALE OWN ACCOUNT"', '9999 SALE OWN ACCOUNT', %s) RETURNING 1""",
                        (admin.tenant_id, d["paper_id"], sale.user_id))
        r = await issue(admin, pid)
        assert r.status == "invalid" and "Số tài khoản công ty" in (r.error or "")
        assert "9999" not in docx_text((await q(sale, "papers.export", paper_id=pid))["docx_b64"])
    finally:
        await owner_val("UPDATE papers.settings SET bank_account = %s WHERE tenant_id = %s RETURNING 1", (old, admin.tenant_id))
    assert "/TK-" in (await issued(admin, pid))["symbol"]   # công ty khai xong ⇒ giấy tự theo, phát hành được


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
    listed = {str(r["id"]) for r in (await q(rh, "papers.list"))["rows"]}
    assert pid not in listed and str(letter["paper_id"]) in listed
    for op in ("papers.view", "papers.export"):
        assert (await executor.query(rh, op, {"paper_id": pid})).status == "not_found", op
    assert (await executor.run(rh, "papers.set_field", {"paper_id": pid, "key": "customer_address", "value": "x"})).status == "not_found"

    def found(page: Any) -> set[str]:   # ⌘K: mã giấy mở được từ kết quả tìm
        return {str((i.get("open") or {}).get("input", {}).get("paper_id")) for b in page.get("blocks", []) for i in b.get("items", [])}

    assert pid not in found(await q(rh, "core.find", q="PHIẾU ĐẶT HÀNG")) and pid in found(await q(admin, "core.find", q="PHIẾU ĐẶT HÀNG"))
    assert pid in {str(r["id"]) for r in (await q(admin, "papers.list"))["rows"]}         # người phát hành đọc được đơn vẫn thấy


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


async def test_issue_needs_the_basis_the_issuer_saw_and_freezes_exactly_that(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rà Codex (OpenAI) 21/11/2026 — hai ca thật trên nhánh lát 1:
    1. Giấy theo hồ sơ mà lệnh KHÔNG mang căn cứ người bấm đã thấy ⇒ không cấp số ("mở lại giấy"), không chạy mù.
    2. Hồ sơ đổi SAU bước so căn cứ, TRƯỚC bước chốt ⇒ tệp phát hành vẫn là ĐÚNG bản đã so (một ảnh dữ liệu cho cả lệnh — ``consistent``)."""
    from barista.modules.papers import compose
    admin, sale = await who("admin"), await who("sale1")
    code = await new_order(admin, sale, "Khách Chốt Đúng Bản")
    pid = str((await ok(sale, "papers.start_draft", template_id=await slip_template(admin), ref=code))["paper_id"])
    await ok(sale, "papers.set_field", paper_id=pid, key="customer_address", value="Số 5 phố Mẫu")

    blind = await executor.run(admin, "papers.issue", {"paper_id": pid})
    assert blind.status == "conflict" and "Mở lại giấy" in (blind.error or "")
    assert await owner_val("SELECT status FROM papers.documents WHERE id = %s", (pid,)) == "draft"

    real_live = compose.live

    async def changed_between_check_and_freeze(ctx: Any, d: dict[str, Any]) -> Any:
        # người khác sửa đơn và COMMIT ngay sau khi lệnh đã so căn cứ (kết nối riêng của owner) — rồi lệnh mới đọc để chốt
        await owner_val("UPDATE orderdesk.orders SET title = 'Nội dung ĐỔI SAU KHI SO' WHERE code = %s AND tenant_id = %s RETURNING 1",
                        (code, admin.tenant_id))
        return await real_live(ctx, d)

    seen = executor.basis_of_token(next(a for a in (await q(admin, "papers.view", paper_id=pid))["actions"] if a["command"] == "papers.issue")["basis"])
    monkeypatch.setattr(compose, "live", changed_between_check_and_freeze)   # chỉ trong lệnh phát hành: sau khi executor đã so căn cứ
    r = await executor.run(admin, "papers.issue", {"paper_id": pid}, expect_basis=seen)
    monkeypatch.setattr(compose, "live", real_live)
    assert r.status == "ok", r.error
    out = r.output
    text = docx_text((await q(admin, "papers.export", paper_id=pid))["docx_b64"])
    assert f"Số: {out['symbol']}" in text and "Tư vấn triển khai" in text and "ĐỔI SAU KHI SO" not in text   # tệp = bản đã so
    assert await owner_val("SELECT title FROM orderdesk.orders WHERE code = %s AND tenant_id = %s", (code, admin.tenant_id)) == "Nội dung ĐỔI SAU KHI SO"
