"""0227 — Văn bản Word sửa trong app: bộ đọc / áp thay đổi TẠI CHỖ trên file gốc (phần thuần) và module (tải lên, mở, lưu phiên bản, lệch phiên bản,
khôi phục, riêng tư theo chủ, file hỏng / macro bị từ chối, đường HTTP tải lên / tải về)."""

import base64
import io
import json
import uuid
import zipfile
from typing import Any

import pytest
from docx import Document
from docx.oxml.ns import qn
from httpx import ASGITransport, AsyncClient

from barista import seed
from barista.http_api import app
from barista.kernel import executor, registry
from barista.modules.word import docx_model as dm
from tests.conftest import ok, q

PEOPLE = [("chu", "Trần Minh Khôi", "admin", "QT", True), ("an", "Lê Hoài An", "staff", "HCNS", False), ("binh", "Đỗ Thanh Bình", "staff", "HCNS", False)]


async def test_authoring_entry_uses_only_enabled_editors() -> None:
    domain = f"authoring-{uuid.uuid4().hex[:8]}.demo"
    tid = await seed.create_tenant("Công ty thử soạn", domain, people=PEOPLE, packs=("word",))
    person = await seed.who(tid, "chu", domain)
    page = await q(person, "papers.studio")
    destinations = [item["open"]["query"] for block in page["blocks"] if block["type"] == "tiles" for item in block["items"]]
    assert "word.home" in destinations
    await ok(person, "admin.set_pack", pack="word", enabled=False)
    page = await q(await seed.who(tid, "chu", domain), "papers.studio")
    destinations = [item["open"]["query"] for block in page["blocks"] if block["type"] == "tiles" for item in block["items"]]
    assert "word.home" not in destinations


async def test_authoring_entry_drops_malformed_plug_results_and_tiles_the_viewer_cannot_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rà 22/11: phần cắm ``papers.authoring`` trả sai hình (một đối tượng thay vì danh sách, ô thiếu ``title`` / ``open``) bị bỏ, màn vẫn mở; ô trỏ tới truy vấn
    người mở không chạy được không hiện."""
    domain = f"authoring-{uuid.uuid4().hex[:8]}.demo"
    tid = await seed.create_tenant("Công ty thử soạn", domain, people=PEOPLE, packs=("word",))
    staff = await seed.who(tid, "an", domain)
    locked = next(n for n, op in sorted(registry.OPS.items()) if op.kind == "query" and not op.internal and op.resource and not executor.can_run(staff, n))
    good = {"title": "Văn bản Word", "open": {"query": "word.home", "input": {}}}

    async def plugs(self: Any, point: str, /, **inp: Any) -> list[Any]:
        return [{"title": "một ô trơn", "open": {"query": "word.home", "input": {}}},
                ["rác", {"title": ""}, {"title": "Không mở", "open": {"query": "word.home", "input": []}},
                 {"title": "Khoá", "open": {"query": locked, "input": {}}}, good]]

    monkeypatch.setattr(executor.Ctx, "plugs", plugs)
    page = await q(staff, "papers.studio")
    tiles = [item for block in page["blocks"] if block["type"] == "tiles" for item in block["items"]]
    assert [t["title"] for t in tiles if t["open"]["query"] != "papers.list"] == ["Văn bản Word"]


def _sample() -> bytes:
    d = Document()
    d.add_heading("Hợp đồng dịch vụ", 1)
    p = d.add_paragraph("Bên A: ")
    p.add_run("Công ty Mẫu").bold = True
    p.add_run(" (sau đây gọi là Bên A)")
    d.add_paragraph("Điều khoản một", style="List Number")
    d.add_paragraph("Điều khoản hai", style="List Number")
    t = d.add_table(rows=2, cols=2)
    t.style = "Table Grid"
    for (r, c), text in {(0, 0): "Hạng mục", (0, 1): "Số tiền", (1, 0): "Phí", (1, 1): "100.000.000"}.items():
        t.cell(r, c).text = text
    link = d.add_paragraph("Xem thêm tại ")
    h = link._p.makeelement(qn("w:hyperlink"), {})
    run = link._p.makeelement(qn("w:r"), {})
    text = link._p.makeelement(qn("w:t"), {})
    text.text = "trang web"
    run.append(text)
    h.append(run)
    link._p.append(h)
    d.sections[0].header.paragraphs[0].text = "ĐẦU TRANG CÔNG TY"
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def _part(data: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read(name)


def _texts(model: dict[str, Any]) -> list[str]:
    return [("".join(r.get("text", "") for r in b["runs"]) if b["t"] == "p" else "<bảng>") for b in model["blocks"]]


# ── Phần thuần ──

def test_reading_gives_runs_with_effective_format_lists_tables_and_locks_what_it_does_not_understand() -> None:
    m = dm.read(_sample())
    head, party, one, two, table, link = m["blocks"]
    assert head["style"]["heading"] == 1 and head["runs"][0]["b"] is True
    assert [(r["text"], bool(r.get("b"))) for r in party["runs"]] == [("Bên A: ", False), ("Công ty Mẫu", True), (" (sau đây gọi là Bên A)", False)]
    assert (one["list"]["label"], two["list"]["label"]) == ("1.", "2.")
    assert table["borders"] is True and [[c["blocks"][0]["runs"][0]["text"] for c in r["cells"]] for r in table["rows"]] == [
        ["Hạng mục", "Số tiền"], ["Phí", "100.000.000"]]
    assert table["rows"][1]["cells"][1]["blocks"][0]["id"] == "b4.r1.c1.b0"
    assert link["locked"] == "có liên kết" and "".join(r["text"] for r in link["runs"]) == "Xem thêm tại trang web"   # khoá nhưng vẫn hiện đủ chữ
    assert m["stats"] == {"blocks": 10, "locked": 1, "images_skipped": 0, "header_footer": True} and m["page"]["w"] > 500
    assert "Phí | 100.000.000" in dm.plain_text(m)


def test_edits_touch_only_what_changed_and_keep_run_formatting() -> None:
    src = _sample()
    out = dm.apply(src, [
        {"op": "set", "id": "b1", "runs": [{"src": 0, "text": "Bên A: "}, {"src": 1, "text": "Công ty", "b": True}, {"src": 1, "text": " Mới", "b": True},
                                           {"src": 2, "text": " nghiêng", "i": True}]},
        {"op": "insert", "after": "b1", "runs": [{"src": 0, "text": "Đoạn thêm 1"}]},
        {"op": "insert", "after": "b1", "runs": [{"src": 0, "text": "Đoạn thêm 2"}]},
        {"op": "insert", "before": "b0", "runs": [{"src": 0, "text": "Dòng đầu"}]},
        {"op": "delete", "id": "b3"},
        {"op": "set", "id": "b4.r1.c1.b0", "runs": [{"src": 0, "text": "120.000.000\ndòng hai"}]}])
    m = dm.read(out)
    assert _texts(m) == ["Dòng đầu", "Hợp đồng dịch vụ", "Bên A: Công ty Mới nghiêng", "Đoạn thêm 1", "Đoạn thêm 2", "Điều khoản một", "<bảng>",
                         "Xem thêm tại trang web"]
    party = m["blocks"][2]["runs"]   # mảnh liền nhau cùng nguồn + cùng định dạng được gộp; đậm giữ nguyên, nghiêng chỉ thêm ở lượt đã đổi
    assert [(r["text"], bool(r.get("b")), bool(r.get("i"))) for r in party] == [("Bên A: ", False, False), ("Công ty Mới", True, False), (" nghiêng", False, True)]
    assert m["blocks"][0]["style"].get("heading") == 1                                   # đoạn thêm chép định dạng đoạn của đoạn mốc
    assert m["blocks"][6]["rows"][1]["cells"][1]["blocks"][0]["runs"][0]["text"] == "120.000.000\ndòng hai"
    # phần không sửa giữ NGUYÊN TỪNG BYTE: kiểu chữ, đầu trang, đánh số
    names = zipfile.ZipFile(io.BytesIO(src)).namelist()
    assert zipfile.ZipFile(io.BytesIO(out)).namelist() == names   # cùng các phần, cùng thứ tự — không phần nào bị bỏ hay thêm
    for name in [n for n in names if n != "word/document.xml"]:
        assert _part(out, name) == _part(src, name), name


def test_paragraph_and_character_formatting_are_written_in_place_and_odd_values_refused() -> None:
    src = _sample()
    out = dm.apply(src, [
        {"op": "para", "id": "b1", "align": "center", "line": 1.5, "indent": 36},
        {"op": "set", "id": "b1", "runs": [{"src": 0, "text": "Bên A: ", "sz": 16, "color": "c00000", "font": "Arial"}, {"src": 1, "text": "Công ty Mẫu", "b": True}]},
        {"op": "insert", "after": "b1", "runs": [{"src": 0, "text": "Ký tên"}], "align": "right"}])
    b1, added = dm.read(out)["blocks"][1:3]
    assert (b1["style"]["align"], b1["style"]["line"], b1["style"]["ind_l"]) == ("center", 1.5, 36.0)
    assert [(r["text"], r.get("sz"), r.get("color"), r.get("font"), bool(r.get("b"))) for r in b1["runs"]] == [
        ("Bên A: ", 16.0, "C00000", "Arial", False), ("Công ty Mẫu", b1["runs"][1].get("sz"), None, b1["runs"][1].get("font"), True)]
    assert (added["style"].get("align"), added["runs"][0]["text"]) == ("right", "Ký tên")
    Document(io.BytesIO(out))                                                           # file vẫn mở được bằng thư viện Word
    props = Document(io.BytesIO(out)).paragraphs[1].runs[0]._r.rPr
    assert props is not None
    rpr = [c.tag.split("}")[1] for c in props]
    assert rpr == sorted(rpr, key=dm.RPR_ORDER.index)                                  # thuộc tính chữ đúng thứ tự lược đồ
    same = dm.read(dm.apply(src, [{"op": "set", "id": "b1", "runs": [{"src": 0, "text": "Bên A: ", "color": "<x>", "font": "a\"b", "sz": 9000}]}]))["blocks"][1]["runs"][0]
    assert (same.get("color"), same.get("font"), same.get("sz")) == tuple(dm.read(src)["blocks"][1]["runs"][0].get(k) for k in ("color", "font", "sz"))   # giá trị lạ bị bỏ
    for bad in ({"align": "giữa"}, {"line": 99}, {"indent": -1}, {}):
        with pytest.raises(dm.BadDocx):
            dm.apply(src, [{"op": "para", "id": "b1", **bad}])
    with pytest.raises(dm.BadDocx):
        dm.apply(src, [{"op": "para", "id": "b5", "align": "center"}])   # đoạn có liên kết: chỉ xem


def test_table_cell_borders_and_tool_generated_widths_are_read_from_the_cells() -> None:
    d = Document()
    t = d.add_table(rows=1, cols=2)
    t.style = "Table Grid"
    for c, (text, width) in enumerate([("Tên đơn vị", "30%"), ("Quốc hiệu", "70%")]):
        cell = t.cell(0, c)
        cell.text = text
        pr = cell._tc.get_or_add_tcPr()
        for old in pr.findall(qn("w:tcW")):
            pr.remove(old)
        pr.append(pr.makeelement(qn("w:tcW"), {qn("w:type"): "pct", qn("w:w"): width}))
        borders = pr.makeelement(qn("w:tcBorders"), {})
        for side in ("top", "left", "bottom", "right"):
            borders.append(borders.makeelement(qn(f"w:{side}"), {qn("w:val"): "nil"}))
        pr.append(borders)
    for g in t._tbl.tblGrid.findall(qn("w:gridCol")):
        g.set(qn("w:w"), "100")   # lưới giả như file do công cụ sinh
    tbl_w = t._tbl.tblPr.makeelement(qn("w:tblW"), {qn("w:type"): "pct", qn("w:w"): "5000"})
    for old in t._tbl.tblPr.findall(qn("w:tblW")):
        t._tbl.tblPr.remove(old)
    t._tbl.tblPr.append(tbl_w)
    out = io.BytesIO()
    d.save(out)
    table = next(b for b in dm.read(out.getvalue())["blocks"] if b["t"] == "table")
    assert table["borders"] is True                                                    # kiểu bảng có kẻ ô…
    assert [c["borders"] for c in table["rows"][0]["cells"]] == [False, False]         # …nhưng ô đặt "không viền" thì ô thắng (khối quốc hiệu)
    assert (table["w_pct"], table["cols"]) == (100.0, [30.0, 70.0])                    # độ rộng ghi ở ô theo %, không lấy lưới giả


def test_bad_edits_and_bad_files_are_refused_without_writing_anything() -> None:
    src = _sample()
    for ops, word in (([{"op": "set", "id": "b5", "runs": [{"text": "x"}]}], "không sửa trực tiếp"),        # đoạn khoá (có liên kết)
                      ([{"op": "delete", "id": "b5"}], "không xoá trực tiếp"),
                      ([{"op": "set", "id": "b99", "runs": []}], "tải lại"),                                # mã không còn trong file
                      ([{"op": "set", "id": "b4", "runs": []}], "tải lại"),                                 # mã của bảng, không phải đoạn
                      ([{"op": "boom", "id": "b1"}], "không hợp lệ"), ([], "Không có thay đổi"),
                      ([{"op": "set", "id": "b1", "runs": [{"text": "x" * (dm.TEXT_MAX + 1)}]}], "tối đa")):
        with pytest.raises(dm.BadDocx, match=word):
            dm.apply(src, ops)
    for junk in (b"khong phai zip", b"PK\x03\x04" + b"\0" * 40):
        with pytest.raises(dm.BadDocx, match="Không đọc được"):
            dm.read(junk)
    macro = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(src)) as zin, zipfile.ZipFile(macro, "w") as zout:
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        zout.writestr("word/vbaProject.bin", b"macro")
    with pytest.raises(dm.BadDocx, match="macro"):
        dm.read(macro.getvalue())


def test_deleting_the_only_paragraph_of_a_cell_empties_it_instead() -> None:
    out = dm.apply(_sample(), [{"op": "delete", "id": "b4.r0.c0.b0"}])
    cell = dm.read(out)["blocks"][4]["rows"][0]["cells"][0]
    assert len(cell["blocks"]) == 1 and cell["blocks"][0]["runs"] == []   # ô bảng phải còn một đoạn (file Word hợp lệ)


# ── Module ──

async def _company() -> dict[str, Any]:
    domain = f"of{uuid.uuid4().hex[:6]}.demo"
    tid = await seed.create_tenant("Công ty thử Văn bản Word (test)", domain, people=PEOPLE, packs=("word",))
    return {name: await seed.who(tid, name, domain) for name, *_ in PEOPLE}


def _h(p: Any) -> dict[str, str]:
    return {"X-Dev-User": p.email, "X-Tenant-Id": str(p.tenant_id)}


def _word(card: dict[str, Any]) -> dict[str, Any]:
    return next(b for b in card["blocks"] if b["type"] == "word")


def test_the_pack_is_optional_and_every_write_is_closed_to_the_assistant() -> None:
    pack = registry.PACKS["word"]
    assert pack.kind == "capability" and not pack.always_on and registry.MODULE_PACK["word"] == "word"
    writes = [o for o in registry.OPS.values() if o.module == "word" and o.kind == "command"]
    assert {o.name for o in writes} == {"word.add", "word.save", "word.restore", "word.rename", "word.archive", "word.assist",
                                         "word.create", "word.send"}
    assert all(o.agent == "deny" for o in writes)


async def test_upload_edit_save_versions_conflict_restore_and_download() -> None:
    w = await _company()
    an = w["an"]
    src = _sample()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        up = await c.post("/v1/word/upload", headers={**_h(an), "x-filename": "H%E1%BB%A3p%20%C4%91%E1%BB%93ng.docx"}, content=src)
        assert up.status_code == 200, up.text
        did = up.json()["output"]["document_id"]
        assert (await c.post("/v1/word/upload", headers={**_h(an), "x-filename": "x.pdf"}, content=b"%PDF")).json()["status"] == "invalid"
        assert (await c.post("/v1/word/upload", headers={**_h(an), "x-filename": "hong.docx"}, content=b"rac")).json()["status"] == "invalid"

    card = await q(an, "word.view", document_id=did)
    word = _word(card)
    assert card["title"] == "Hợp đồng" and word["version"] == 1 and word["editable"] and len(word["blocks"]) == 6
    assert any("chỉ xem" in n for n in word["notes"]) and any("Bản in" in n for n in word["notes"])
    assert "Bên A: Công ty Mẫu" in card["blocks"][1]["markdown"]                                # bản chữ cho app chưa vẽ trình sửa / trợ lý

    ops = [{"op": "set", "id": "b1", "runs": [{"src": 0, "text": "Bên A: "}, {"src": 1, "text": "Công ty Sao Mai", "b": True}]},
           {"op": "insert", "after": "b3", "runs": [{"src": 0, "text": "Điều khoản ba"}]}]
    saved = await ok(an, "word.save", document_id=did, base_version=1, ops_json=json.dumps(ops))
    assert saved["version"] == 2
    stale = await executor.run(an, "word.save", {"document_id": did, "base_version": 1, "ops_json": json.dumps(ops)})
    assert stale.status == "conflict" and "mới hơn" in stale.error                              # mở ở hai nơi: không ghi đè im lặng
    locked = await executor.run(an, "word.save", {"document_id": did, "base_version": 2,
                                                    "ops_json": json.dumps([{"op": "set", "id": "b6", "runs": [{"text": "x"}]}])})
    assert locked.status == "invalid" and "không sửa trực tiếp" in locked.error

    word = _word(await q(an, "word.view", document_id=did))
    assert word["version"] == 2 and "".join(r["text"] for r in word["blocks"][1]["runs"]) == "Bên A: Công ty Sao Mai"
    assert word["blocks"][4]["list"]["label"] == "3." and word["blocks"][4]["runs"][0]["text"] == "Điều khoản ba"

    back = await ok(an, "word.restore", document_id=did, version=1)
    assert back["version"] == 3
    vs = (await q(an, "word.versions", document_id=did))["rows"]
    assert [(v["version"], v["note"], v["current"]) for v in vs] == [(3, "Khôi phục bản 1", "yes"), (2, "Sửa 1 đoạn, thêm 1 đoạn", "no"), (1, "Tải lên", "no")]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        now = await c.get(f"/v1/word/{did}/docx", headers=_h(an))
        v2 = await c.get(f"/v1/word/{did}/docx?version=2", headers=_h(an))
        assert now.status_code == 200 and now.content == src and "attachment" in now.headers["content-disposition"]   # bản khôi phục = đúng file gốc
        assert "Công ty Sao Mai" in dm.plain_text(dm.read(v2.content))
        assert _part(v2.content, "word/header1.xml") == _part(src, "word/header1.xml")           # đầu trang đi nguyên qua lần sửa
        # người khác trong công ty: không thấy, không tải, không sửa (văn bản của riêng người tạo)
        other = await c.get(f"/v1/word/{did}/docx", headers=_h(w["binh"]))
        assert other.status_code == 404
    assert (await executor.query(w["binh"], "word.view", {"document_id": did})).status == "not_found"
    assert (await executor.query(w["chu"], "word.view", {"document_id": did})).status == "not_found"       # quản trị cũng không
    assert (await executor.run(w["binh"], "word.save", {"document_id": did, "base_version": 3, "ops_json": json.dumps(ops)})).status == "not_found"
    assert (await q(w["binh"], "word.list"))["rows"] == []

    await ok(an, "word.rename", document_id=did, title="Hợp đồng dịch vụ 2026")
    assert [r["title"] for r in (await q(an, "word.list"))["rows"]] == ["Hợp đồng dịch vụ 2026"]
    await ok(an, "word.archive", document_id=did)
    assert (await q(an, "word.list"))["rows"] == []
    gone = await executor.run(an, "word.save", {"document_id": did, "base_version": 3, "ops_json": json.dumps(ops)})
    assert gone.status == "invalid" and "đã cất" in gone.error
    assert not _word(await q(an, "word.view", document_id=did))["editable"]


async def test_the_editor_assistant_only_proposes_text_for_the_paragraphs_sent_and_writes_nothing() -> None:
    from pydantic_ai import ModelMessage, ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from barista.modules import word

    people = await _company()
    an, binh = people["an"], people["binh"]
    up = await ok(an, "word.add", filename="hop-dong.docx", title="Hợp đồng", data_b64=base64.b64encode(_sample()).decode())
    seen: list[str] = []

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(str(messages[-1]))
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            "reply": "Đã viết lại trang trọng hơn.",
            "edits": [{"id": "b1", "text": "Bên A: Công ty Cổ phần Mẫu"}, {"id": "b2", "text": "Điều khoản một"},     # b2 gửi lên y nguyên ⇒ bỏ
                      {"id": "b9", "text": "đoạn không gửi lên"}],                                                     # mã lạ ⇒ bỏ
            "additions": [{"after": "b1", "text": "Đại diện: Giám đốc"}, {"after": "x", "text": "lạc chỗ"}]})])

    paras = json.dumps([{"id": "b1", "text": "Bên A: Công ty Mẫu (sau đây gọi là Bên A)"}, {"id": "b2", "text": "Điều khoản một"}])
    word.ASSIST_MODEL = FunctionModel(reply, model_name="word-fake")
    try:
        out = await ok(an, "word.assist", document_id=up["document_id"], instruction="Viết trang trọng hơn", paragraphs_json=paras)
        r = await executor.run(binh, "word.assist", {"document_id": up["document_id"], "instruction": "x", "paragraphs_json": paras})
        assert r.status == "not_found"                                                 # văn bản của người khác: không gọi model
        assert len(seen) == 1 and "[b1] Bên A" in seen[0] and "Viết trang trọng hơn" in seen[0]
        r = await executor.run(an.as_agent(), "word.assist", {"document_id": up["document_id"], "instruction": "x", "paragraphs_json": paras})
        assert r.status == "denied"                                                    # trợ lý chat không tự gọi thay người
    finally:
        word.ASSIST_MODEL = None
    assert out["edits"] == [{"id": "b1", "before": "Bên A: Công ty Mẫu (sau đây gọi là Bên A)", "text": "Bên A: Công ty Cổ phần Mẫu"}]
    assert out["additions"] == [{"after": "b1", "text": "Đại diện: Giám đốc"}] and out["reply"].startswith("Đã viết lại")
    assert (await q(an, "word.versions", document_id=up["document_id"]))["rows"][0]["version"] == 1   # không ghi gì: vẫn phiên bản 1


def test_a_blank_document_is_a4_with_one_empty_paragraph_ready_to_type() -> None:
    m = dm.read(dm.blank())
    assert (round(m["page"]["w"]), round(m["page"]["h"])) == (595, 842)                 # A4 dọc
    assert [(b["t"], b.get("locked", ""), b["runs"]) for b in m["blocks"]] == [("p", "", [])]
    assert m["blocks"][0]["base"].get("font") == "Times New Roman" and m["blocks"][0]["base"].get("sz") == 13
    out = dm.apply(dm.blank(), [{"op": "set", "id": "b0", "runs": [{"src": -1, "text": "Kính gửi Ban Giám đốc"}]},
                                {"op": "insert", "after": "b0", "runs": [{"src": -1, "text": "Đoạn hai"}]}])
    assert dm.plain_text(dm.read(out)).split("\n")[:2] == ["Kính gửi Ban Giám đốc", "Đoạn hai"]


async def test_create_then_send_to_a_room_members_read_it_others_do_not_and_the_draft_stays_private() -> None:
    domain = f"wordshare-{uuid.uuid4().hex[:6]}.test"
    tid = await seed.create_tenant("Công ty thử gửi văn bản (test)", domain, people=PEOPLE, packs=("word", "talk"))
    chu, an, binh = [await seed.who(tid, n, domain) for n in ("chu", "an", "binh")]
    doc = await ok(an, "word.create")
    assert doc["title"] == "Văn bản mới"
    view = _block(await q(an, "word.view", document_id=doc["document_id"]))
    assert view["share"] == {"targets": []} and view["ops"]["send"] == "word.send"           # chưa ở phòng nào: nút có, danh sách rỗng
    room = await ok(an, "talk.create_room", name="Phòng soạn thảo", members=[binh.email])
    target = f"talk.room:{room['room_id']}"
    assert [o["value"] for o in _block(await q(an, "word.view", document_id=doc["document_id"]))["share"]["targets"]] == [target]
    first = await _bytes_of(an, doc["document_id"])
    await ok(an, "word.save", document_id=doc["document_id"], base_version=1,
             ops_json=json.dumps([{"op": "set", "id": "b0", "runs": [{"src": -1, "text": "Đề xuất gửi nhóm"}]}]))
    sent = await ok(an, "word.send", document_id=doc["document_id"], target=target, note="Mọi người xem giúp")
    assert sent["version"] == 2 and sent["message_id"]
    thread = await q(binh, "talk.thread", subject_kind="talk.room", subject_id=str(room["room_id"]))
    tab = await q(binh, "talk.files", thread_id=next(b for b in thread["blocks"] if b["type"] == "thread")["thread_id"], kind="file")
    rows = [i for b in tab["blocks"] if b["type"] == "list" for i in b["items"]]
    assert [r["title"] for r in rows] == ["Văn bản mới.docx"]                                  # người trong phòng thấy tệp …
    got = await executor.query(binh, "talk.file", {"attachment_id": rows[0]["file"]["id"]})
    assert got.status == "ok"                                                                   # … và tải được
    assert base64.b64decode(got.output["data_b64"]) == await _bytes_of(an, doc["document_id"]) != first   # đúng phiên bản đang lưu lúc gửi
    assert (await executor.query(chu, "talk.thread", {"subject_kind": "talk.room", "subject_id": str(room["room_id"])})).status == "not_found"
    assert (await executor.query(binh, "word.view", {"document_id": doc["document_id"]})).status == "not_found"   # bản gốc vẫn riêng
    r = await executor.run(binh, "word.send", {"document_id": doc["document_id"], "target": target})
    assert r.status == "not_found"                                                              # không gửi được văn bản của người khác
    other = await ok(chu, "talk.create_room", name="Phòng không có An", members=[binh.email])
    r = await executor.run(an, "word.send", {"document_id": doc["document_id"], "target": f"talk.room:{other['room_id']}"})
    assert r.status == "not_found"                                                              # không gửi vào phòng mình không ở
    r = await executor.run(an, "word.send", {"document_id": doc["document_id"], "target": "lung-tung"})
    assert r.status == "invalid"
    r = await executor.run(an.as_agent(), "word.send", {"document_id": doc["document_id"], "target": target})
    assert r.status == "denied"                                                                 # trợ lý không gửi thay người


async def test_without_messaging_there_is_no_send_button_and_sending_says_why() -> None:
    people = await _company()   # chỉ bật Văn bản Word
    an = people["an"]
    doc = await ok(an, "word.create", title="Ghi chú riêng")
    assert "share" not in _block(await q(an, "word.view", document_id=doc["document_id"]))
    r = await executor.run(an, "word.send", {"document_id": doc["document_id"], "target": f"talk.room:{uuid.uuid4()}"})
    assert r.status == "invalid" and "Tin nhắn" in r.error


def _block(page: dict[str, Any]) -> dict[str, Any]:
    return next(b for b in page["blocks"] if b["type"] == "word")


async def _bytes_of(p: Any, document_id: Any) -> bytes:
    f = await executor.query(p, "word.file", {"document_id": document_id})
    assert f.status == "ok", f.error
    return base64.b64decode(f.output["data_b64"])


async def test_a_company_without_the_pack_has_none_of_it() -> None:
    domain = f"on{uuid.uuid4().hex[:6]}.demo"
    tid = await seed.create_tenant("Công ty không bật Văn bản Word (test)", domain, people=PEOPLE, packs=())
    an = await seed.who(tid, "an", domain)
    assert (await executor.query(an, "word.list", {})).status != "ok"
    r = await executor.run(an, "word.add", {"filename": "a.docx", "data_b64": base64.b64encode(_sample()).decode()})
    assert r.status != "ok"
