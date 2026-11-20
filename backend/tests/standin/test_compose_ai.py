"""SOẠN VỚI AI (thử nghiệm — ghi chú nội bộ (barista-docs)) trên nguồn của một gói ngành, qua gói thế vai: bản LÕI (ADR 0190) của
``barista-packs:real_estate/tests/test_compose_ai.py`` — số chỉ từ khối số liệu (truy vấn của gói chạy bằng quyền người soạn), code kiểm số trong chữ AI, bản soạn
của riêng người tạo, xuất tệp qua kho tệp."""

import base64
import io
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio

from barista.kernel import executor
from barista.modules import compose
from tests.conftest import Who, ok, q
from tests.standin import helpers
from tests.test_compose_ai import _model


@pytest_asyncio.fixture(autouse=True)
async def compose_on(who: Who) -> AsyncIterator[None]:
    """"Soạn với AI" là gói TUỲ CHỌN, mặc định tắt: bật cho CÔNG TY THẾ VAI (như quản trị bấm ở "Store") rồi tắt lại sau test — bản của fixture
    ``compose_on`` trong ``tests/conftest.py`` (fixture đó bật cho công ty mẫu)."""
    admin = await who("admin")
    r = await executor.run(admin, "admin.set_pack", {"pack": "compose", "enabled": True})
    assert r.status == "ok", (r.status, r.error)
    try:
        yield
    finally:
        await executor.run(admin, "admin.set_pack", {"pack": "compose", "enabled": False})


def _draft(text: str) -> dict[str, Any]:
    return {"title": "Báo cáo đơn dịch vụ", "blocks": [{"type": "heading", "text": "Tình hình đơn"}, {"type": "paragraph", "text": text},
                                                      {"type": "data", "ref": "d1"}, {"type": "data", "ref": "d99"}]}


async def _start(who: Who, p: Any, outputs: list[dict[str, Any]]) -> tuple[dict[str, Any], list[int]]:
    """Như ``_start`` của ``tests/test_compose_ai.py`` nhưng khối số liệu lấy từ truy vấn của gói thế vai (người soạn có sẵn một đơn của mình)."""
    await helpers.order(who, owner="sale1", amount=1_500_000_000)
    model, calls = _model(outputs, {"query": "orderdesk.list", "title": "Đơn của tôi", "input": {}})
    compose.MODEL = model
    try:
        out = await ok(p, "compose.start", kind="document", brief="Báo cáo đơn dịch vụ tháng này cho giám đốc")
    finally:
        compose.MODEL = None
    assert out["status"] == "drafting"   # lệnh chỉ ghi "đang soạn"; lượt model chạy SAU commit (executor chờ xong mới trả lời)
    comp = (await q(p, "compose.view", session_id=out["session_id"]))["blocks"][0]
    assert comp["status"] == "ready", comp["error"]
    return {**out, **comp["stats"]}, calls


async def test_start_builds_blocks_with_sources_and_retries_invented_numbers(who: Who) -> None:
    sale = await who("sale1")
    out, calls = await _start(who, sale, [_draft("Tháng này có 987654 đơn."), _draft("Tình hình như bảng dưới. [cần số liệu: mục tiêu tháng]")])
    assert len(calls) == 3                                     # công cụ, bản có số bịa, bản sửa sau lời kiểm của code
    assert out["flagged"] == 0 and out["placeholders"] == 1 and out["data"] == 1   # mã lạ d99 bị bỏ
    page = await q(sale, "compose.view", session_id=out["session_id"])
    comp = page["blocks"][0]
    assert comp["type"] == "composer" and page["blocks"][1]["type"] == "callout"   # app chưa vẽ composer ⇒ bản chữ thay thế
    data = next(b for b in comp["blocks"] if b["type"] == "data")
    assert data["source"]["query"] == "orderdesk.list" and data["page"]["kind"] == "table" and data["page"]["rows"]
    para = next(b for b in comp["blocks"] if b["type"] == "paragraph")
    assert para["origin"] == "ai" and para["placeholders"] == ["[cần số liệu: mục tiêu tháng]"]
    # bản soạn của riêng người tạo
    other = await who("sale2")
    assert (await executor.query(other, "compose.view", {"session_id": str(out["session_id"])})).status == "not_found"
    assert out["session_id"] not in [r["id"] for r in (await q(other, "compose.list"))["rows"]]


async def test_flagged_numbers_block_export_until_fixed_and_files_open(who: Who) -> None:
    sale = await who("sale1")
    out, _ = await _start(who, sale, [_draft("Có 987654 đơn."), _draft("Vẫn 987654 đơn.")])
    sid = str(out["session_id"])
    assert out["flagged"] == 1
    r = await executor.run(sale, "compose.export", {"session_id": sid, "format": "docx"})
    assert r.status == "invalid" and "không khớp" in r.error
    comp = (await q(sale, "compose.view", session_id=sid))["blocks"][0]
    bad = next(b for b in comp["blocks"] if b["flags"])
    await ok(sale, "compose.remove_block", session_id=sid, block=bad["id"])
    for fmt in ("docx", "pptx"):
        e = await ok(sale, "compose.export", session_id=sid, format=fmt)
        f = await q(sale, "compose.download", export_id=e["export_id"])
        raw = base64.b64decode(f["data_b64"])
        assert raw[:2] == b"PK"
        if fmt == "docx":
            from docx import Document
            text = "\n".join(p.text for p in Document(io.BytesIO(raw)).paragraphs)
            # dòng nguồn in tên truy vấn của gói. Tên đó viết bằng dấu nhãn từ vựng ([[service_order]] — 0124) và tệp xuất hiện CHƯA đổi dấu nhãn thành
            # chữ của công ty (compose/render.py source_line) ⇒ ở đây chỉ kiểm có dòng nguồn + giờ lấy số, không kiểm chữ của nhãn
            assert "Nguồn: " in text and "(theo quyền xem của người soạn) — số liệu lúc" in text
        else:
            from pptx import Presentation
            assert len(Presentation(io.BytesIO(raw)).slides) >= 2
    other = await who("sale2")
    assert (await executor.query(other, "compose.download", {"export_id": str(e["export_id"])})).status == "not_found"
    v = (await q(sale, "compose.view", session_id=sid))["blocks"][0]
    assert [x["version"] for x in v["versions"]] == [2, 1] and len(v["exports"]) == 2
    await ok(sale, "compose.restore", session_id=sid, version=1)
    await ok(sale, "compose.edit_block", session_id=sid, block=bad["id"], text="Người viết: 987654 đơn")   # người chịu trách nhiệm
    assert (await ok(sale, "compose.export", session_id=sid, format="docx"))["filename"].endswith(".docx")
    await ok(sale, "compose.refresh_data", session_id=sid)
    await ok(sale, "compose.delete", session_id=sid)
    assert (await executor.query(sale, "compose.view", {"session_id": sid})).status == "not_found"
