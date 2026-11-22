"""VĂN BẢN WORD (0227) — mở file .docx ngay trong app, sửa đơn giản (chữ, thêm / xoá đoạn, đậm / nghiêng / gạch chân, chữ trong ô bảng), lưu thành
phiên bản mới, tải lại đúng file Word / xem bản in PDF.

* SỬA TẠI CHỖ trên file gốc (``docx_model.py``): không đổi Word → HTML → Word; chỗ không sửa giữ nguyên trong file. Đoạn có nội dung app chưa hiểu (liên
  kết, trường tự động, hình, theo dõi thay đổi) hiện nhưng khoá.
* MỖI LẦN LƯU = MỘT PHIÊN BẢN (chỉ thêm). Lưu gửi kèm phiên bản đã mở; lệch ⇒ ``Conflict`` (mở ở hai nơi) — không ghi đè im lặng. Khôi phục = thêm phiên
  bản mới chép từ bản cũ.
* Bản đầu: văn bản của RIÊNG người tạo (RLS theo chủ — như Soạn với AI / app tự ghép); chưa chia sẻ, chưa sửa cùng lúc. Gói khác đưa file sang là bước sau.
* Thân file ở kho tệp (0122), bảng chỉ giữ tham chiếu. File người dùng là dữ liệu không tin được: chỉ .docx không macro, có trần cỡ, chặn bom nén.
* Trợ lý: đọc được văn bản (bản chữ trên thẻ); KHÔNG tải lên / sửa / cất thay người ở bản đầu (``agent="deny"``).
"""

import base64
import hashlib
import json
import logging
from typing import Any
from urllib.parse import quote, unquote
from uuid import UUID

from pydantic import BaseModel
from pydantic import Field as PField

from barista.api import Action as A
from barista.api import (
    Conflict,
    Ctx,
    Denied,
    Input,
    Invalid,
    Module,
    NoInput,
    NotFound,
    Open,
    Pack,
    Prep,
    Screen,
    ai,
    convert,
    field,
    files,
    today,
    ui,
    web,
)
from barista.modules.word import docx_model as dm
from barista.modules.word import forms

log = logging.getLogger("barista.word")

Pack("word", "Văn bản Word",
     "Mở file Word ngay trong app, sửa đơn giản đúng trình bày gốc, lưu từng phiên bản, tải lại file Word hoặc xem bản in PDF",
     kind="capability")
m = Module("word", "Văn bản Word", pack="word")

MAX_DOCS = 300          # văn bản đang mở (chưa cất) mỗi người
NOTE = "Sửa trực tiếp trên file gốc: chữ, đoạn, phông / cỡ / màu chữ, đậm / nghiêng / gạch chân, căn lề, giãn dòng, thụt lề, ô bảng. Phần khác (đầu / chân trang, hình, mục lục) giữ nguyên trong file."


async def _doc(ctx: Ctx, document_id: UUID, *, lock: bool = False) -> dict[str, Any]:
    d = await ctx.one("SELECT * FROM word.documents WHERE id = %s" + (" FOR UPDATE" if lock else ""), (document_id,))
    if d is None:
        raise NotFound("Không thấy văn bản")
    return d


async def _bytes(ctx: Ctx, document_id: UUID, version: int) -> bytes:
    fid = await ctx.val("SELECT file_id FROM word.versions WHERE document_id = %s AND version = %s", (document_id, version))
    if fid is None:
        raise NotFound("Không thấy phiên bản này")
    return await files.read(ctx, fid)


async def _add_version(ctx: Ctx, document_id: UUID, version: int, data: bytes, filename: str, note: str) -> None:
    stored = await files.put(ctx, data, filename, dm.DOCX_MIME, max_bytes=dm.MAX_BYTES)   # kho trước, dòng sau (0122)
    await ctx.exec("INSERT INTO word.versions (document_id, version, file_id, size_bytes, note) VALUES (%s, %s, %s, %s, %s)",
                   (document_id, version, stored.id, stored.size, note[:300]))


def _filename(title: str) -> str:
    return (title.strip() or "van-ban")[:150] + ".docx"


def _model(data: bytes) -> dict[str, Any]:
    try:
        return dm.read(data)
    except dm.BadDocx as e:
        raise Invalid(str(e)) from e


# ── Tải lên ──

class AddIn(Input):
    filename: str = field("Tên file", max_length=300)
    title: str = field("Tên văn bản", "", max_length=200, help="Trống = tên file")
    data_b64: str = field("Nội dung (base64)", max_length=28_000_000, audit=False)


@m.command("add", resource=None, title="Thêm văn bản Word", agent="deny")
async def add(ctx: Ctx, inp: AddIn) -> dict[str, Any]:
    """Nhận một file .docx (qua đường tải lên của gói), kiểm đọc được rồi lưu thành văn bản mới, phiên bản 1."""
    name = inp.filename.strip()
    if not name.lower().endswith(".docx"):
        raise Invalid("Chỉ nhận file Word .docx (file .doc cũ: mở bằng Word rồi lưu lại dạng .docx)")
    try:
        data = base64.b64decode(inp.data_b64, validate=True)
    except ValueError as e:
        raise Invalid("File gửi lên không hợp lệ") from e
    _model(data)   # đọc thử: file hỏng / có macro / quá lớn ⇒ từ chối ngay, không lưu thứ không mở được
    if int(await ctx.val("SELECT count(*) FROM word.documents WHERE archived_at IS NULL") or 0) >= MAX_DOCS:
        raise Invalid(f"Đã có {MAX_DOCS} văn bản — cất bớt văn bản cũ rồi thêm tiếp")
    title = (inp.title.strip() or name[:-5].strip() or "Văn bản")[:200]
    did = await ctx.val("INSERT INTO word.documents (title, origin) VALUES (%s, %s) RETURNING id", (title, name[:200]))
    await _add_version(ctx, did, 1, data, name, "Tải lên")
    return {"document_id": did, "title": title, "summary": f"Đã thêm văn bản “{title}”"}


class CreateIn(Input):
    title: str = field("Tên văn bản", "", max_length=200, help="Trống = “Văn bản mới” — đổi tên ngay trên trang soạn")
    form: str = field("Khung thể thức", "", max_length=40, hidden=True, help="Khoá khung (forms.FORMS); trống ⇒ nhận theo lời mô tả")
    brief: str = field("Lời mô tả", "", max_length=2000, hidden=True, audit=False)


@m.command("create", resource=None, title="Tạo văn bản Word mới", agent="deny")
async def create(ctx: Ctx, inp: CreateIn) -> dict[str, Any]:
    """Văn bản TRẮNG khổ A4 (thể thức hành chính) ở khu vực riêng của người tạo, phiên bản 1 — mở thẳng trình soạn; viết bằng tay hoặc nhờ trợ lý
    soạn thảo viết từ trang trắng. Không hỏi gì thêm (0225): tên mặc định, đổi tên tại chỗ."""
    if int(await ctx.val("SELECT count(*) FROM word.documents WHERE archived_at IS NULL") or 0) >= MAX_DOCS:
        raise Invalid(f"Đã có {MAX_DOCS} văn bản — cất bớt văn bản cũ rồi thêm tiếp")
    title = (inp.title.strip() or "Văn bản mới")[:200]
    # KHUNG THỂ THỨC (0251 lát 2): code dựng bố cục theo loại (người chọn, hoặc nhận theo lời mô tả) — AI chỉ viết chữ vào khung. Không loại nào khớp ⇒ trang trắng
    key = inp.form if inp.form in forms.FORMS else forms.infer(inp.brief or inp.title) if (inp.brief or inp.form) else ""
    data = forms.build(key, await _letterhead(ctx), today()) if key else dm.blank()
    did = await ctx.val("INSERT INTO word.documents (title, origin) VALUES (%s, %s) RETURNING id", (title, "Tạo mới"))
    await _add_version(ctx, did, 1, data, _filename(title), "Tạo mới" + (f" · {forms.FORMS[key].label}" if key and key != "trang" else ""))
    return {"document_id": did, "title": title, "form": key, "summary": f"Đã tạo văn bản “{title}”"}


class SendIn(Input):
    document_id: UUID = field("Văn bản", hidden=True)
    target: str = field("Gửi vào", max_length=120, options_query="talk.share_targets", help="Phòng anh/chị đang ở, hoặc cuộc nhắn riêng đã có")
    note: str = field("Lời nhắn", "", kind="textarea", max_length=2000, audit=False)


@m.command("send", resource=None, title="Gửi văn bản vào phòng", agent="deny")
async def send(ctx: Ctx, inp: SendIn) -> dict[str, Any]:
    """Gửi PHIÊN BẢN ĐANG LƯU (file .docx) vào một phòng / cuộc nhắn riêng: thành một tin kèm tệp — người trong phòng xem / tải về, KHÔNG sửa
    bản của người gửi (bản gốc vẫn ở khu vực riêng, sửa tiếp được). Đi qua lệnh gửi tệp của gói Trao đổi bằng QUYỀN NGƯỜI GỬI (gói đó kiểm loại tệp,
    cỡ, quyền vào phòng). Công ty chưa bật Trao đổi ⇒ báo rõ. Trợ lý không gửi thay người (tin đã gửi người khác đã đọc)."""
    d = await _doc(ctx, inp.document_id)
    kind, _, sid = inp.target.strip().rpartition(":")
    try:
        subject_id = UUID(sid)
    except ValueError as e:
        raise Invalid("Chọn phòng để gửi") from e
    if not kind:
        raise Invalid("Chọn phòng để gửi")
    data = await _bytes(ctx, d["id"], d["version"])
    out = await ctx.call_optional("talk.post_file", subject_kind=kind, subject_id=subject_id, filename=_filename(d["title"]), size=len(data),
                                  sha256=hashlib.sha256(data).hexdigest(), body=inp.note.strip(), content_b64=base64.b64encode(data).decode())
    if out is None:
        raise Invalid("Chưa gửi được: công ty chưa bật Tin nhắn, hoặc anh/chị chưa được dùng Tin nhắn")
    return {"document_id": d["id"], "version": d["version"], "message_id": out.get("message_id"),
            "summary": f"Đã gửi “{d['title']}” (phiên bản {d['version']}) vào trao đổi"}


# ── Mở ──

class DocIn(Input):
    document_id: UUID = field("Văn bản", hidden=True)


# App chỉ vẽ (bất biến 7): khối `word` mang sẵn tên thao tác + đường tải lên — mã web không viết cứng tên thao tác của gói
CLIENT_OPS: dict[str, Any] = {"upload_url": "/v1/word/upload", "ops": {k: f"word.{k}" for k in (
    "home", "view", "save", "versions", "restore", "archive", "rename", "assist", "create", "send")}}


@m.query("view", resource=None, title="Mở văn bản Word")
async def view(ctx: Ctx, inp: DocIn) -> dict[str, Any]:
    """Văn bản để sửa: khối ``word`` (web vẽ trình sửa) + bản chữ cho app chưa vẽ được và cho trợ lý đọc."""
    d = await _doc(ctx, inp.document_id)
    model = _model(await _bytes(ctx, d["id"], d["version"]))
    stats = model["stats"]
    notes = [NOTE]
    if stats["locked"]:
        notes.append(f"{stats['locked']} đoạn có nội dung đặc biệt (liên kết, hình, trường tự động…) chỉ xem, không sửa ở đây.")
    if stats["header_footer"]:
        notes.append("Đầu / chân trang không hiện khi sửa — xem ở Bản in.")
    block = {"type": "word", "document_id": str(d["id"]), "title": d["title"], "version": d["version"], "editable": d["archived_at"] is None,
             "page": model["page"], "blocks": model["blocks"], "notes": notes,
             "docx_url": f"/v1/word/{d['id']}/docx", "pdf_url": f"/v1/word/{d['id']}/pdf", **CLIENT_OPS}
    # Gửi vào phòng (§2.11): nơi gửi do gói Trao đổi liệt kê bằng quyền người xem; công ty chưa bật Trao đổi / người này không dùng được ⇒ không có mục
    # "share" ⇒ web không vẽ nút. Danh sách rỗng (chưa ở phòng nào) vẫn gửi mục để web nói rõ "chưa có phòng nào".
    targets = await ctx.call_optional("talk.share_targets")
    if targets is not None:
        block["share"] = {"targets": targets}
    return ui.card(d["title"], [block, ui.b_text(dm.plain_text(model))], subtitle=f"Văn bản Word · phiên bản {d['version']}", icon="doc.richtext")


@m.query("home", resource=None, title="Văn bản Word")
async def home(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Trang của phân hệ: khối ``word`` ở chế độ THƯ VIỆN (web vẽ nút tải lên + danh sách, bấm mở trình sửa) + bảng chữ cho app chưa vẽ được."""
    rows = await ctx.all("""SELECT d.id, d.title, d.version, d.updated_at FROM word.documents d
                            WHERE d.archived_at IS NULL ORDER BY d.updated_at DESC LIMIT 500""")
    block = {"type": "word", "document_id": "", "title": "Văn bản Word", "version": 0, "editable": False, "page": {}, "blocks": [],
             "notes": ["Chỉ mình bạn thấy các văn bản này.", NOTE],
             **CLIENT_OPS,
             "documents": [{"id": str(r["id"]), "title": r["title"], "version": r["version"], "updated_at": r["updated_at"].isoformat()} for r in rows]}
    names = "\n".join(f"- {r['title']} (phiên bản {r['version']})" for r in rows[:50]) or "Chưa có văn bản nào."
    return ui.card("Văn bản Word", [block, ui.b_text(names)], subtitle="Tải file Word lên để sửa ngay trong app", icon="doc.richtext")


@m.query("list", resource=None, title="Văn bản Word của tôi")
async def list_(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("""SELECT d.id, d.title, d.origin, d.version, d.updated_at, v.size_bytes
                            FROM word.documents d JOIN word.versions v ON v.document_id = d.id AND v.version = d.version
                            WHERE d.archived_at IS NULL ORDER BY d.updated_at DESC LIMIT 500""")
    for r in rows:
        r["size"] = f"{max(1, round(r.pop('size_bytes') / 1024))} KB"
    return ui.table(rows, [ui.col("title", "Văn bản", width=320), ui.col("version", "Phiên bản", "number"), ui.col("updated_at", "Sửa lúc", "datetime"),
                           ui.col("size", "Cỡ file"), ui.col("origin", "File gốc")],
                    title="Văn bản Word", empty="Chưa có văn bản nào — bấm “Tải file Word lên”", note="Chỉ mình bạn thấy các văn bản này.")


@m.query("versions", resource=None, title="Các phiên bản của văn bản")
async def versions(ctx: Ctx, inp: DocIn) -> dict[str, Any]:
    d = await _doc(ctx, inp.document_id)
    rows = await ctx.all("""SELECT v.version, v.note, v.created_at, v.size_bytes FROM word.versions v WHERE v.document_id = %s
                            ORDER BY v.version DESC""", (d["id"],))
    for r in rows:
        r["id"] = r["version"]
        r["current"] = "yes" if r["version"] == d["version"] else "no"
        r["size"] = f"{max(1, round(r.pop('size_bytes') / 1024))} KB"
    return ui.table(rows, [ui.col("version", "Phiên bản", "number"), ui.col("note", "Việc đã làm", width=280), ui.col("created_at", "Lúc", "datetime"),
                           ui.col("size", "Cỡ file"), ui.col("current", "Hiện hành", "badge", labels={"yes": "Đang dùng", "no": ""}, tones={"yes": "green"})],
                    title=f"Phiên bản — {d['title']}", row_key="id")


# ── Lưu thay đổi ──

class SaveIn(Input):
    document_id: UUID = field("Văn bản", hidden=True)
    base_version: int = field("Phiên bản đã mở", ge=1)
    ops_json: str = field("Thay đổi (JSON)", max_length=6_000_000, audit=False)
    note: str = field("Ghi chú", "", max_length=200)


@m.command("save", resource=None, title="Lưu thay đổi văn bản Word", agent="deny")
async def save(ctx: Ctx, inp: SaveIn) -> dict[str, Any]:
    """Áp các thay đổi của người sửa vào ĐÚNG file của phiên bản đã mở, lưu thành phiên bản kế tiếp. Văn bản đã có bản mới hơn ⇒ ``Conflict``."""
    d = await _doc(ctx, inp.document_id, lock=True)
    if d["archived_at"] is not None:
        raise Invalid("Văn bản đã cất — bỏ cất rồi sửa")
    if d["version"] != inp.base_version:
        raise Conflict("Văn bản đã có phiên bản mới hơn (mở ở nơi khác?) — tải lại rồi sửa tiếp")
    try:
        ops = json.loads(inp.ops_json)
    except ValueError as e:
        raise Invalid("Thay đổi không hợp lệ") from e
    try:
        data = dm.apply(await _bytes(ctx, d["id"], d["version"]), ops)
        dm.read(data)   # file vừa ghi phải đọc lại được — không lưu thứ mình không mở lại được
    except dm.BadDocx as e:
        raise Invalid(str(e)) from e
    n = d["version"] + 1
    kinds = [o.get("op") for o in ops if isinstance(o, dict)]
    parts = [f"{label} {c} đoạn" for label, c in (("sửa", kinds.count("set")), ("thêm", kinds.count("insert")), ("xoá", kinds.count("delete")),
                                                    ("định dạng", kinds.count("para"))) if c]
    note = inp.note.strip() or (", ".join(parts).capitalize() if parts else "Sửa")
    await _add_version(ctx, d["id"], n, data, _filename(d["title"]), note)
    await ctx.exec("UPDATE word.documents SET version = %s, updated_at = now() WHERE id = %s", (n, d["id"]))
    return {"document_id": d["id"], "version": n, "summary": f"Đã lưu phiên bản {n}"}


# ── Trợ lý soạn thảo trong trình sửa (0227 §2.8) ──
# Model chỉ ĐỀ XUẤT chữ cho các đoạn người gửi; không ghi file. Người xem đề xuất, bấm áp dụng (thay chữ trên trang giấy), rồi tự bấm Lưu như mọi lần sửa.

ASSIST_MODEL: Any = None   # chỉ cho test (vẫn qua công tắc AI của công ty)
ASSIST_MAX_PARAS = 400
ASSIST_MAX_CHARS = 60_000
ASSIST_RULES = """Bạn là trợ lý soạn thảo văn bản tiếng Việt trong một trình sửa văn bản Word.
Bạn nhận yêu cầu của người dùng và danh sách đoạn văn, mỗi đoạn có mã [id]. Làm đúng yêu cầu trên các đoạn đó.
- "edits": đoạn cần ĐỔI chữ — trả mã đoạn và TOÀN BỘ chữ mới của đoạn. Đoạn không cần đổi thì KHÔNG đưa vào.
- "additions": đoạn MỚI cần thêm (vd viết tiếp, thêm điều khoản) — trả mã đoạn đứng ngay trước chỗ thêm và chữ của đoạn mới, theo thứ tự đọc.
- Chỉ dùng mã đoạn có trong danh sách. Giữ nguyên số liệu, tên riêng, ngày tháng, số hiệu văn bản trừ khi người dùng bảo đổi; không bịa thông tin mới.
- Chữ thuần, không dùng Markdown (không **, không #, không gạch đầu dòng tự thêm). Giữ văn phong hành chính / hợp đồng nếu văn bản là loại đó.
- "reply": một hai câu nói ngắn đã làm gì (hoặc trả lời câu hỏi nếu người dùng chỉ hỏi về văn bản — khi đó edits / additions để trống).
- Văn bản có KHUNG THỂ THỨC (bảng đầu: tên cơ quan, số ký hiệu, quốc hiệu, tiêu ngữ, ngày tháng; tên loại; bảng cuối: nơi nhận, chữ ký): GIỮ NGUYÊN
  quốc hiệu, tiêu ngữ, tên loại, ngày tháng và vị trí các đoạn; chỉ thay "……" bằng nội dung khi biết (trích yếu "V/v …" / "Về việc …", "Kính gửi: …",
  nơi nhận, chức vụ người ký). Đoạn "[Nội dung]" là chỗ viết NỘI DUNG: sửa đoạn đó thành đoạn nội dung đầu tiên (edits) và thêm các đoạn tiếp theo NGAY SAU
  nó (additions, theo thứ tự). Không thêm đoạn nội dung vào trong bảng khung. Văn bản hành chính theo Nghị định 30/2020/NĐ-CP: câu chữ ngắn gọn, rõ
  nghĩa, đúng văn phong hành chính.
- Văn bản của CÔNG TY người dùng: dùng đúng "Thông tin công ty" được cho (tên, địa chỉ, mã số thuế). KHÔNG viết chỗ giữ kiểu [Tên công ty], [Ngày]…; thông tin
  chưa có (tên người nhận, ngày cụ thể, số tiền…) thì để "……" cho người điền và nói rõ trong "reply" là còn chỗ nào cần điền."""


class AssistPara(BaseModel):
    id: str = PField(max_length=120)
    text: str = PField(max_length=20_000)


class AssistEdit(BaseModel):
    id: str = PField(description="mã đoạn có trong danh sách")
    text: str = PField(description="toàn bộ chữ mới của đoạn")


class AssistAddition(BaseModel):
    after: str = PField(description="mã đoạn đứng ngay trước đoạn mới")
    text: str = PField(description="chữ của đoạn mới")


class AssistOut(BaseModel):
    reply: str = PField(description="một hai câu ngắn: đã làm gì / trả lời")
    edits: list[AssistEdit] = PField(default_factory=list)
    additions: list[AssistAddition] = PField(default_factory=list)


class AssistIn(Input):
    document_id: UUID = field("Văn bản", hidden=True)
    instruction: str = field("Yêu cầu", max_length=2000)
    paragraphs_json: str = field("Các đoạn (JSON)", max_length=400_000, audit=False)
    scope: str = field("Phạm vi", "selection", max_length=20)


def _assist_paras(raw: str) -> list[AssistPara]:
    try:
        items = [AssistPara.model_validate(x) for x in json.loads(raw)]
    except (ValueError, TypeError) as e:
        raise Invalid("Danh sách đoạn không hợp lệ") from e
    items = [x for x in items if x.id]
    if not items:
        raise Invalid("Chọn đoạn cần sửa (hoặc để trống để làm trên cả văn bản)")
    if len(items) > ASSIST_MAX_PARAS or sum(len(x.text) for x in items) > ASSIST_MAX_CHARS:
        raise Invalid("Văn bản quá dài cho một lượt trợ lý — chọn một phần rồi hỏi")
    return items


async def _letterhead(ctx: Ctx) -> dict[str, Any]:
    e = await ctx.one("SELECT name, address, tax_code FROM core.legal_entities WHERE id = core.default_entity()")
    return {"name": (e or {}).get("name") or "", "address": (e or {}).get("address") or "", "tax_code": (e or {}).get("tax_code") or ""}


@m.query("letterhead", resource=None, title="Thông tin công ty để soạn văn bản", agent="deny")
async def letterhead(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Pháp nhân mặc định của công ty người gọi (tên, địa chỉ, mã số thuế) — trợ lý soạn thảo viết đúng tên công ty thay vì chỗ giữ. Thông tin công ty, ai
    trong công ty cũng thấy trên giấy tờ; chưa khai ⇒ rỗng."""
    return await _letterhead(ctx)


def _company_line(c: dict[str, Any]) -> str:
    parts = [f"tên: {c['name']}" if c.get("name") else "", f"địa chỉ: {c['address']}" if c.get("address") else "",
             f"mã số thuế: {c['tax_code']}" if c.get("tax_code") else ""]
    return "; ".join(x for x in parts if x) or "chưa khai — để \"……\" chỗ tên / địa chỉ công ty"


async def _assist(prep: Prep, inp: AssistIn) -> dict[str, Any]:
    """Bước CHUẨN BỊ (ADR 0200): kiểm văn bản là của người gọi (truy vấn nhẹ, quyền người gọi) rồi gọi model NGOÀI transaction DB (không giữ kết nối / khoá dòng lúc chờ)."""
    if not inp.instruction.strip():
        raise Invalid("Nhập yêu cầu cho trợ lý")
    await prep.query("word.versions", document_id=str(inp.document_id))   # không phải văn bản của mình ⇒ NotFound, không gọi model
    items = _assist_paras(inp.paragraphs_json)
    where = "các đoạn người dùng đang chọn" if inp.scope == "selection" else "toàn bộ văn bản"
    body = "\n".join(f"[{x.id}] {x.text}" for x in items)
    try:
        company = await prep.query("word.letterhead")
    except Exception:   # không đọc được thông tin công ty không làm hỏng lượt soạn — model để "……"
        company = {}
    try:
        out = await ai.run(prep, f"Yêu cầu: {inp.instruction.strip()}\n\nThông tin công ty: {_company_line(company)}.\n\nPhạm vi: {where}.\n\nCác đoạn:\n{body}",
                           instructions=ASSIST_RULES,
                           output_type=AssistOut, category="word:assist", modules=["word"], model=ASSIST_MODEL)
    except Denied:
        raise   # công ty tắt AI / hết hạn mức: câu của lõi đã rõ
    except Exception as e:   # nhà cung cấp model lỗi / chưa cấu hình khoá: người dùng thấy câu dễ hiểu, log giữ chi tiết để tra
        log.exception("word.assist: gọi model không được")
        raise Invalid("Trợ lý chưa trả lời được (model chưa sẵn sàng hoặc đang lỗi) — thử lại sau, hoặc báo quản trị") from e
    return {"ids": [x.id for x in items], "out": out.model_dump()}


@m.command("assist", resource=None, title="Trợ lý soạn thảo trong văn bản Word", agent="deny", prepare=_assist)
async def assist(ctx: Ctx, inp: AssistIn) -> dict[str, Any]:
    """Trả ĐỀ XUẤT của model cho các đoạn người gửi — code lọc: chỉ giữ mã đoạn có trong danh sách gửi lên, bỏ đề xuất trùng chữ cũ. Không ghi gì:
    người áp dụng trên trang giấy rồi tự lưu (phiên bản mới như mọi lần sửa). ``agent="deny"``: trợ lý chat không tự gọi thay người."""
    await _doc(ctx, inp.document_id)
    got = ctx.prepared
    if got is None:
        if ctx.preview:
            return {"summary": "Trợ lý sẽ đề xuất chữ mới cho các đoạn đã chọn"}
        raise Invalid("Trợ lý soạn thảo chỉ chạy trực tiếp từ trình sửa")
    sent = {x.id: x.text for x in _assist_paras(inp.paragraphs_json)}
    out = got["out"]
    edits = [{"id": e["id"], "before": sent[e["id"]], "text": e["text"].strip()} for e in out.get("edits", [])
             if e.get("id") in sent and e.get("text", "").strip() and e["text"].strip() != sent[e["id"]].strip()]
    additions = [{"after": a["after"], "text": a["text"].strip()} for a in out.get("additions", []) if a.get("after") in sent and a.get("text", "").strip()]
    return {"reply": str(out.get("reply") or "")[:1000], "edits": edits[:ASSIST_MAX_PARAS], "additions": additions[:100],
            "summary": f"Trợ lý đề xuất sửa {len(edits)} đoạn, thêm {len(additions)} đoạn" if edits or additions else "Trợ lý không đề xuất thay đổi"}


class RestoreIn(Input):
    document_id: UUID = field("Văn bản", hidden=True)
    version: int = field("Phiên bản", ge=1)


@m.command("restore", resource=None, title="Khôi phục phiên bản cũ", agent="deny")
async def restore(ctx: Ctx, inp: RestoreIn) -> dict[str, Any]:
    """Quay về một phiên bản cũ bằng cách THÊM phiên bản mới chép từ nó — không xoá gì."""
    d = await _doc(ctx, inp.document_id, lock=True)
    if inp.version == d["version"]:
        raise Invalid("Đây đang là phiên bản hiện hành")
    data = await _bytes(ctx, d["id"], inp.version)
    n = d["version"] + 1
    await _add_version(ctx, d["id"], n, data, _filename(d["title"]), f"Khôi phục bản {inp.version}")
    await ctx.exec("UPDATE word.documents SET version = %s, updated_at = now() WHERE id = %s", (n, d["id"]))
    return {"document_id": d["id"], "version": n, "summary": f"Đã khôi phục bản {inp.version} thành phiên bản {n}"}


class RenameIn(Input):
    document_id: UUID = field("Văn bản", hidden=True)
    title: str = field("Tên văn bản", max_length=200)


@m.command("rename", resource=None, title="Đổi tên văn bản", agent="deny")
async def rename(ctx: Ctx, inp: RenameIn) -> dict[str, Any]:
    if not inp.title.strip():
        raise Invalid("Tên không được trống")
    d = await _doc(ctx, inp.document_id, lock=True)
    await ctx.exec("UPDATE word.documents SET title = %s, updated_at = now() WHERE id = %s", (inp.title.strip(), d["id"]))
    return {"document_id": d["id"], "summary": "Đã đổi tên"}


@m.command("archive",
           purpose="Văn bản không còn cần ở danh sách — cất đi cho gọn.", irreversible=True, effect="Cất văn bản khỏi danh sách; phiên bản và tệp vẫn còn trong kho theo thời hạn lưu nhưng không có nút mở lại.",
           resource=None, title="Cất văn bản", agent="deny")
async def archive(ctx: Ctx, inp: DocIn) -> dict[str, Any]:
    """Cất khỏi danh sách (không xoá: phiên bản và file còn nguyên trong kho theo thời hạn lưu của công ty)."""
    d = await _doc(ctx, inp.document_id, lock=True)
    await ctx.exec("UPDATE word.documents SET archived_at = now() WHERE id = %s AND archived_at IS NULL", (d["id"],))
    return {"document_id": d["id"], "summary": f"Đã cất “{d['title']}”", "subject": {"label": f"Văn bản “{d['title']}”", "open": None}}


# ── File để tải / xem bản in ──

class FileIn(Input):
    document_id: UUID = field("Văn bản", hidden=True)
    version: int = field("Phiên bản", 0, ge=0, help="0 = phiên bản hiện hành")


@m.query("file", resource=None, title="File Word của văn bản", agent="deny")
async def file(ctx: Ctx, inp: FileIn) -> dict[str, Any]:
    d = await _doc(ctx, inp.document_id)
    data = await _bytes(ctx, d["id"], inp.version or d["version"])
    return {"filename": _filename(d["title"]), "data_b64": base64.b64encode(data).decode()}


@m.route("POST", "/v1/word/upload")
async def http_upload(request: web.Request, title: str = "") -> web.JSONResponse:
    """Tải một file .docx lên thành văn bản mới (thân request = file; tên file ở header ``x-filename``, mã hoá phần trăm)."""
    p = await web.caller(request)
    data = await request.body()
    if len(data) > dm.MAX_BYTES:
        raise web.HTTPException(413, f"File quá {dm.MAX_BYTES // (1024 * 1024)} MB")
    filename = unquote(request.headers.get("x-filename") or "")
    return web.result(await web.run(p, "word.add", {"filename": filename, "title": title, "data_b64": base64.b64encode(data).decode()}))


@m.route("GET", "/v1/word/{document_id}/docx")
async def http_docx(document_id: UUID, request: web.Request, version: int = 0) -> web.Response:
    p = await web.caller(request)
    r = await web.query(p, "word.file", {"document_id": str(document_id), "version": version})
    if not r.ok:
        return web.result(r)
    return web.Response(base64.b64decode(r.output["data_b64"]), media_type=dm.DOCX_MIME,
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(r.output['filename'])}"})


@m.route("GET", "/v1/word/{document_id}/pdf")
async def http_pdf(document_id: UUID, request: web.Request, version: int = 0) -> web.Response:
    """BẢN IN: PDF đổi từ chính file .docx (LibreOffice) — đúng từng trang, có đầu / chân trang."""
    p = await web.caller(request)
    r = await web.query(p, "word.file", {"document_id": str(document_id), "version": version})
    if not r.ok:
        return web.result(r)
    try:
        pdf = await convert.to_pdf(r.output["filename"], base64.b64decode(r.output["data_b64"]))
    except convert.Unavailable as e:
        raise web.HTTPException(409, str(e)) from e
    name = r.output["filename"].rsplit(".", 1)[0] + ".pdf"
    return web.Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}"})


@m.plug("papers.authoring", "Soạn hoặc mở file Word")
async def authoring(ctx: Ctx, inp: NoInput) -> list[dict[str, Any]]:
    # ``studio``: màn "Soạn" mở thẳng một VĂN BẢN MỚI bên phải + agent bên trái (0251 lát 2) — tạo lúc người gửi câu đầu, mở bằng ``view``
    return [{"title": "Văn bản Word", "subtitle": "Soạn trang trắng hoặc mở file .docx · sửa trực tiếp và nhờ trợ lý",
             "module": "word", "open": {"query": "word.home", "input": {}},
             "studio": {"create": "word.create", "view": "word.view", "assist": "word.assist", "home": "word.home", "label": "Văn bản mới",
                        # khung thể thức chọn được ở chip "Loại" (không chọn ⇒ máy chủ nhận theo lời mô tả — ``forms.infer``)
                        "forms": [{"key": f.key, "label": f.label} for f in forms.FORMS.values()]}}]


# 0231: không còn phân hệ riêng — sửa file Word là một việc của VĂN BẢN (hợp đồng, công văn, mẫu giấy tờ gắn hồ sơ — 0229), nằm cùng Giấy tờ trong phân hệ
# "Văn bản & tài liệu" (khoá "docs"), ngay sau giấy tờ, trước kho tài liệu.
m.screens("docs", (
    Screen("word_docs", "Sửa file Word", "doc.richtext", "word.home", tint="indigo"),
    Screen("word_list", "File Word đã mở", "list.bullet", "word.list", tint="indigo",
           open=Open("word.view", {"document_id": "id"}, "Văn bản"),
           row_actions=(A("word.rename", "Đổi tên", bind={"document_id": "id", "title": "title"}),
                        A("word.archive", "Cất", bind={"document_id": "id"}, style="destructive"))),
), rank=-1)
