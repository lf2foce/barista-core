"""SOẠN THẢO lát 1 (0118): mẫu có phiên bản, bản soạn điền từ hồ sơ bằng quyền người soạn, nguồn từng ô, cập nhật số khi hồ sơ đổi, ban hành đi
đúng ``papers.issue`` (cấp số) + tệp ban hành ở kho tệp (0122). Mẫu đầu tiên: phiếu giữ chỗ từ một giao dịch (gói BĐS cắm ô)."""


import pytest

from barista.kernel import registry
from barista.modules.papers.template import Field, TemplateError, fill, fmt, parse, parse_value, validate

# ── Phần thuần: cú pháp mẫu, định dạng kiểu Việt ──

CAT = {f.key: f for f in (Field("name", "Tên"), Field("price", "Giá", "amount"), Field("on", "Ngày", "date"), Field("area", "Diện tích", "number"),
                          Field("note", "Ghi chú"), Field("plan", "Lịch", "rows", (Field("label", "Đợt"), Field("amount", "Số tiền", "amount"))))}


def test_vietnamese_formats() -> None:
    assert fmt(5_600_000_000, "amount") == "5.600.000.000 đồng"
    assert fmt(5_600_000_000, "amount", "chu") == "Năm tỷ sáu trăm triệu đồng"
    assert fmt("2026-11-08", "date") == "08/11/2026" and fmt("2026-11-08", "date", "dai") == "ngày 08 tháng 11 năm 2026"
    assert fmt(75.5, "number") == "75,5" and fmt(1234, "number") == "1.234" and fmt(70.0, "number") == "70"
    assert parse_value("50.000.000 đ", "amount") == 50_000_000 and parse_value("08/11/2026", "date") == "2026-11-08"
    assert parse_value("75,5", "number") == 75.5
    with pytest.raises(ValueError):
        parse_value("năm mươi triệu", "amount")


def test_template_fills_values_conditions_and_tables_and_lists_gaps() -> None:
    body = ("# Phiếu {{name}}\n## Điều 1\nGiá {{price}} ({{price|chu}}) ngày {{on|dai}}\n{{plan}}\n{{#if note}}\n- Ghi chú: {{note}}\n{{/if}}\n"
            "{{#unless note}}\nKhông ghi chú, diện tích {{area}} m²\n{{/unless}}")
    blocks = parse(body)
    assert validate(blocks, CAT) == []
    got = fill(blocks, CAT, {"name": "A", "price": 2_000_000, "on": "2026-01-02", "plan": [{"label": "Cọc", "amount": 500_000},
                                                                                         {"label": "Ký", "amount": 1_500_000}]})
    assert got.title == "Phiếu A"
    assert [b["type"] for b in got.blocks] == ["heading", "para", "table", "para"]
    assert got.blocks[1]["text"] == "Giá 2.000.000 đồng (Hai triệu đồng) ngày ngày 02 tháng 01 năm 2026"
    assert got.blocks[2]["total"] == ["Tổng cộng", "2.000.000 đồng"] and got.blocks[2]["align"] == ["l", "r"]
    assert got.missing == ["area"] and "……" in got.blocks[3]["text"]   # mục điều kiện tắt (note) không tính thiếu
    assert "| Cọc | 500.000 đồng |" in got.markdown


def test_template_errors_are_clear() -> None:
    for body, msg in (("## Không tên", "Thiếu dòng tên"), ("# A\n{{#if x}}\nB", "chưa đóng"), ("# A\n{{/if}}", "không khớp"),
                      ("# A\nB {{#if x}} C", "đứng riêng"), ("# A\nB {{ x ", "viết sai")):
        with pytest.raises(TemplateError, match=msg):
            parse(body)
    errs = validate(parse("# A\n{{lạ}}\nGiá {{plan}} {{name|chu}} {{price|xx}}".replace("lạ", "unknown")), CAT)
    assert any("unknown" in e for e in errs) and any("phải đứng riêng" in e for e in errs)
    assert any("|chu chỉ dùng" in e for e in errs) and any("bộ lọc lạ" in e for e in errs)


def test_values_are_inserted_after_parsing_so_record_text_is_never_syntax() -> None:
    got = fill(parse("# A\nTên: {{name}}"), CAT, {"name": "{{price}} {{#if x}}"})
    assert got.blocks[0]["text"] == "Tên: {{price}} {{#if x}}" and got.missing == []


def test_agent_modes() -> None:
    ops = registry.OPS
    assert ops["papers.start_draft"].agent == "allow" and ops["papers.start_draft"].auto and ops["papers.set_field"].agent == "propose"
    assert ops["papers.issue"].agent == "deny" and ops["papers.void"].agent == "deny" and ops["papers.publish_template"].agent == "deny"
    assert ops["papers.issue"].basis is not None and "papers.refresh_draft" not in ops and "papers.drafts" not in ops
    assert all(ops[n].agent == "allow" for n in ("papers.pending", "papers.view", "papers.templates_list", "papers.template_view"))


def test_paper_authority_resources_are_granted_only_with_manage() -> None:
    """ADR 0251: tài nguyên THẨM QUYỀN của loại giấy (``papers.office`` + tài nguyên gói khai cho chứng từ của mình, tên ``<module>.papers``) quyết cả AI
    phát hành lẫn AI THẤY bản chưa phát hành (chính sách đọc của papers.documents). Cấp ``read`` / ``write`` không kèm ``manage`` cho một vai = cho vai đó
    thấy bản chưa phát hành của người khác mà không phát hành được — bộ mặc định của lõi và mọi gói đang cài không được làm vậy."""
    from barista import presets
    authority = {r for r in registry.RESOURCES if r == "papers.office" or r.endswith(".papers")}
    bad = [g for g in presets.grants() if g[1] in authority and "manage" not in g[3].split()]
    assert "papers.office" in authority and not bad, bad
