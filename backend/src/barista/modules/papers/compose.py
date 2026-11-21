"""SOẠN THẢO (0118 lát 1) — văn bản ráp từ MẪU CÓ PHIÊN BẢN và SỐ LIỆU THẬT của hồ sơ, chưa có trợ lý viết.

* Mẫu (``papers.templates``) có phiên bản (``template_versions``): thân Markdown có ô ``{{khoá}}`` (``template.py``); phát hành ⇒ khoá.
* Ô lấy từ đâu: gói tự khai qua HAI điểm cắm của ``papers`` (0110) — ``papers.field_catalog`` (danh mục ô theo loại hồ sơ + mẫu gợi ý; quyền
  hệ thống, chỉ là siêu dữ liệu) và ``papers.field_values`` (số liệu MỘT hồ sơ, chạy bằng QUYỀN NGƯỜI SOẠN — không thấy hồ sơ ⇒ không soạn
  được). ``papers`` không biết ngành nào; ô chung (pháp nhân, người ký, ngày lập) đọc ở ``core.legal_entities`` + ``papers.settings``.
* SỐ SỐNG (ADR 0251 — sửa 0118): bản chưa cấp số LUÔN là số mới nhất của hồ sơ — mở / tải / ban hành đều đọc lại ``papers.field_values`` bằng quyền người
  đang xem; không có "bản N", không có "Cập nhật số từ hồ sơ". Chỉ dữ kiện NGƯỜI NHẬP (ô hồ sơ không giữ) lưu theo giấy (``papers.entries``: ai, lúc nào,
  căn cứ). Ban hành = chốt số (MỘT bản chốt ở ``revisions`` + ``fields``, ``via='issue'``) + cấp số + cất .docx đóng băng (``kernel/files.py``, 0122);
  số đổi giữa lúc xem và lúc bấm ⇒ căn cứ của lệnh (0071, ``issue_basis``) báo "xem lại". Còn ô thiếu ⇒ không ban hành.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.kernel import executor, registry, ui
from barista.kernel.clock import today, zone
from barista.kernel.errors import Denied, Invalid, NotFound
from barista.kernel.executor import Ctx
from barista.kernel.fields import Input, NoInput
from barista.kernel.fields import field as ffield
from barista.kernel.screens import Action as A
from barista.kernel.screens import Open, Screen
from barista.kernel.text import fold
from barista.modules.papers import (
    FAMILIES,
    OFFICE,
    _insert,
    authority_label,
    can_discard,
    can_issue,
    family_of,
    file_name,
    issue_action,
    m,
    settings,
    status_label,
    tail_actions,
)
from barista.modules.papers.template import (
    CONTROL,
    KINDS,
    Field,
    TemplateError,
    empty,
    fill,
    fmt,
    jsonable,
    mark,
    parse,
    parse_value,
    used,
    validate,
)

m.consumes("plug")   # danh mục ô của gói là siêu dữ liệu ⇒ hỏi bằng quyền hệ thống; SỐ LIỆU luôn hỏi bằng quyền người soạn

# Ô CHUNG của mọi giấy — không thuộc gói nào: thông tin công ty (papers.settings) + pháp nhân ký (core.legal_entities, gói trả entity_id).
COMMON = (Field("company_name", "Tên công ty"), Field("entity_name", "Tên pháp nhân ký"),
          Field("entity_tax_code", "Mã số thuế của pháp nhân"), Field("entity_address", "Địa chỉ pháp nhân"),
          Field("company_phone", "Điện thoại công ty"), Field("company_bank_account", "Số tài khoản công ty"),
          Field("company_bank_name", "Ngân hàng nhận tiền"), Field("signer_name", "Họ tên người đại diện ký"),
          Field("signer_title", "Chức vụ người đại diện"), Field("draft_date", "Ngày lập văn bản", "date"))
COMMON_KEYS = frozenset(f.key for f in COMMON)   # KHÔNG người nhập: chỉ từ Quản trị › Pháp nhân / Thông tin công ty trên giấy tờ
KIND_PREFIX = "mau_"   # loại giấy của mẫu = mau_<khoá mẫu> ⇒ dãy số không bao giờ trùng loại giấy gói soạn qua save_draft


@dataclass
class Subject:
    """Một loại hồ sơ có thể lấy số liệu (gói khai qua ``papers.field_catalog``)."""
    kind: str
    label: str
    ref_label: str
    fields: dict[str, Field]
    starters: list[dict[str, Any]] = field(default_factory=list)
    authority: str = OFFICE   # tài nguyên THẨM QUYỀN mặc định của giấy lập từ loại hồ sơ này (gói khai ``authority`` — ADR 0251)


async def subjects(ctx: Ctx) -> dict[str, Subject]:
    out: dict[str, Subject] = {}
    for got in await ctx.plugs("papers.field_catalog", system=True):
        for raw in got if isinstance(got, list) else [got]:
            own = {f.key: f for f in map(Field.of, raw["fields"])}
            if clash := sorted(set(own) & COMMON_KEYS):
                raise RuntimeError(f"{raw['subject_kind']}: ô {clash} trùng ô chung của giấy tờ")
            authority = raw.get("authority") or OFFICE
            if authority not in registry.RESOURCES:
                raise RuntimeError(f"{raw['subject_kind']}: thẩm quyền {authority} chưa khai là tài nguyên của module nào")
            out[raw["subject_kind"]] = Subject(raw["subject_kind"], raw["label"], raw.get("ref_label") or "Mã hồ sơ",
                                               {**{f.key: f for f in COMMON}, **own}, list(raw.get("starters") or []), authority)
    return out


async def _subject(ctx: Ctx, kind: str) -> Subject:
    s = (await subjects(ctx)).get(kind)
    if s is None:
        raise Invalid("Không có gói nào đang bật cung cấp loại hồ sơ của mẫu này")
    return s


async def record(ctx: Ctx, kind: str, ref: str) -> dict[str, Any]:
    """Số liệu MỘT hồ sơ, bằng quyền người đang chạy (soạn / cập nhật / ban hành). Vào ``{subject_kind, ref}`` (mã người gõ; lần sau là ``ref``
    chuẩn gói đã trả). Gói trả ``{subject_id, ref, title, values, entity_id?, blocked?}`` hoặc None (không phải loại của gói); không gói nào
    thấy ⇒ như không có hồ sơ."""
    for got in await ctx.plugs("papers.field_values", subject_kind=kind, ref=ref.strip()):
        return got
    raise NotFound(f"Không thấy hồ sơ “{ref}” — sai mã, hoặc bạn không có quyền xem hồ sơ này")


async def _common(ctx: Ctx, entity_id: Any, draft_on: date) -> dict[str, Any]:
    s = await settings(ctx)
    e = await ctx.one("SELECT name, tax_code, address FROM core.legal_entities WHERE id = coalesce(%s::uuid, core.default_entity())",
                      (str(entity_id) if entity_id else None,)) or {}
    return {"company_name": s["company_name"], "entity_name": e.get("name"), "entity_tax_code": e.get("tax_code"),
            "entity_address": e.get("address"), "company_phone": s["phone"], "company_bank_account": s["bank_account"],
            "company_bank_name": s["bank_name"], "signer_name": s["signer_name"], "signer_title": s["signer_title"],
            "draft_date": draft_on.isoformat()}


def _rows(subj: Subject, keys: list[str], rec: dict[str, Any], common: dict[str, Any],
          carried: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Ô của một bản: giá trị hồ sơ trước; hồ sơ trống ⇒ giữ dữ kiện người đã nhập ở bản trước; không có cả hai ⇒ ô thiếu (value None)."""
    values = {**common, **jsonable(rec.get("values") or {})}
    src = {"subject_kind": subj.kind, "subject_id": str(rec["subject_id"]), "ref": rec["ref"]}
    out = {}
    for k in keys:
        f = subj.fields[k]
        v = values.get(k)
        if not empty(v):
            out[k] = {"key": k, "label": f.label, "kind": f.kind, "value": v, "display": fmt(v, f.kind), "origin": "record",
                      "source": {"common": True} if k in COMMON_KEYS else src, "reason": "", "entered_by": None, "entered_at": None}
        elif k in carried and f.person:   # ô công ty / ô hồ sơ không bao giờ lấy dữ kiện người nhập, kể cả bản cũ lỡ có
            out[k] = carried[k]
        else:
            out[k] = {"key": k, "label": f.label, "kind": f.kind, "value": None, "display": "", "origin": "record",
                      "source": {"common": True} if k in COMMON_KEYS else src, "reason": "", "entered_by": None, "entered_at": None}
    return out


def _mark(rows: dict[str, dict[str, Any]], rec: dict[str, Any]) -> str:
    """Dấu số liệu HỒ SƠ (ô người nhập tính là hồ sơ trống — đổi dữ kiện người nhập không phải hồ sơ đổi)."""
    return mark({"entity": str(rec.get("entity_id") or ""), **{k: r["value"] if r["origin"] == "record" else None for k, r in rows.items()}})


async def _write_rev(ctx: Ctx, doc_id: Any, rev: int, rows: dict[str, dict[str, Any]], body: str, mk: str) -> None:
    """BẢN CHỐT lúc ban hành (0251): ô nào từ hồ sơ nào / người nhập — trang của giấy đã cấp số đọc từ đây (số không đổi theo hồ sơ nữa)."""
    await ctx.exec("""INSERT INTO papers.revisions (document_id, rev, via, mark, body, missing, changes, created_by)
                      VALUES (%s, %s, 'issue', %s, %s, '{}', '[]', %s)""", (doc_id, rev, mk, body, ctx.p.user_id))
    async with ctx.conn.cursor() as cur:
        await cur.executemany(
            """INSERT INTO papers.fields (document_id, rev, key, label, kind, value, display, origin, source, reason, entered_by, entered_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [(doc_id, rev, r["key"], r["label"], r["kind"], None if r["value"] is None else Jsonb(r["value"]), r["display"], r["origin"],
              Jsonb(r["source"]), r["reason"], r["entered_by"], r["entered_at"]) for r in rows.values()])


def _content(t: dict[str, Any], filled: Any, notes: list[str]) -> dict[str, Any]:
    return {"title": filled.title, "blocks": filled.blocks, "signers": list(t["signers"]), "template": f"{t['name']} — mẫu v{t['version']}",
            "notes": notes}


async def _notes(ctx: Ctx, rows: dict[str, dict[str, Any]]) -> tuple[dict[str, int], list[str]]:
    """Ô người nhập ⇒ số chú thích sau giá trị + dòng chú thích cuối văn bản (ai nhập, ngày nào). Luôn in — không có kiểu ẩn."""
    person = [r for r in rows.values() if r["origin"] == "person"]
    names = {str(u["id"]): u["name"] for u in await ctx.all("SELECT id, name FROM core.users WHERE id = ANY(%s)",
                                                            ([r["entered_by"] for r in person],))} if person else {}
    marks = {r["key"]: i for i, r in enumerate(person, 1)}
    notes = [f"[{marks[r['key']]}] {r['label']}: thông tin do {names.get(str(r['entered_by']), 'người soạn')} nhập ngày "
             f"{r['entered_at'].astimezone(zone()):%d/%m/%Y}, không lấy từ hồ sơ." for r in person]
    return marks, notes


def _check_body(subj: Subject, body: str) -> list[Any]:
    try:
        blocks = parse(body)
    except TemplateError as e:
        raise Invalid(str(e)) from None
    if errs := validate(blocks, subj.fields):
        raise Invalid("Mẫu dùng ô không hợp lệ: " + "; ".join(errs))
    return blocks


def _signers(raw: str) -> list[str]:
    out = [x.strip().upper() for x in raw.split("|") if x.strip()]
    if len(out) > 3:
        raise Invalid("Tối đa ba bên ký")
    return out


# ── Mẫu ──

@m.query("subject_kind_options", resource="papers", title="Loại hồ sơ lấy số liệu")
async def subject_kind_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [{"value": s.kind, "label": s.label} for s in (await subjects(ctx)).values()]


@m.query("starter_options", resource="papers", title="Mẫu gợi ý của gói")
async def starter_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [{"value": f"{s.kind}/{x['key']}", "label": f"{x['name']} ({s.label})"} for s in (await subjects(ctx)).values() for x in s.starters]


@m.query("authority_options", resource="papers", title="Ai ban hành / phát hành giấy của mẫu")
async def authority_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Tài nguyên THẨM QUYỀN đã khai (ADR 0251): văn bản hành chính của lõi + thẩm quyền gói khai cho chứng từ theo loại hồ sơ. Ai giữ ``manage`` trên mục
    nào là dữ liệu ma trận quyền (Quản trị › Phân quyền)."""
    out = {OFFICE: authority_label(OFFICE)}
    for s in (await subjects(ctx)).values():
        out.setdefault(s.authority, authority_label(s.authority))
    return [{"value": k, "label": v} for k, v in out.items()]


class NewTemplate(Input):
    starter: str = ffield("Mẫu gợi ý của gói", "", options_query="papers.starter_options",
                          help="Chọn để lấy sẵn tên, loại hồ sơ, thân mẫu — mọi ô trống bên dưới lấy theo mẫu gợi ý")
    name: str = ffield("Tên mẫu", "", max_length=120, example="Phiếu giữ chỗ")
    key: str = ffield("Mã mẫu", "", max_length=40, group="Tuỳ chọn khác", help="Để trống — máy đặt từ tên mẫu. Mỗi mã một dãy số riêng trong năm")
    subject_kind: str = ffield("Loại hồ sơ lấy số liệu", "", options_query="papers.subject_kind_options")
    abbr: str = ffield("Viết tắt trong số ký hiệu", "", max_length=12, group="Tuỳ chọn khác", help="Để trống — máy lấy chữ cái đầu của tên mẫu")
    body: str = ffield("Thân mẫu", "", kind="textarea", max_length=60_000,
                       help="# Tên văn bản · ## Mục · - dòng · {{ô}} · {{ô|chu}} số tiền bằng chữ · {{ô|dai}} ngày viết dài · {{#if ô}} … {{/if}}")
    signers: str = ffield("Các bên ký (cách nhau dấu |)", "", max_length=200, example="BÊN A | BÊN B")
    authority: str = ffield("Ai phát hành", "", options_query="papers.authority_options", group="Tuỳ chọn khác",
                            help="Để trống — theo loại hồ sơ (gói khai). Vai có quyền quản lý mục này mới phát hành / ban hành giấy của mẫu")
    only_new: bool = ffield("Chỉ thêm khi chưa có", False, hidden=True, agent=False,
                            help="Bộ mặc định của gói: công ty đã có mẫu cùng mã (kể cả đã sửa / ngừng dùng) thì giữ nguyên của công ty")
    publish: bool = ffield("Phát hành ngay", False, hidden=True, agent=False,
                           help="Bộ mặc định của gói: mẫu có sẵn để soạn ngay; công ty sửa thân mẫu = phiên bản mới, bản đang dùng không đổi")


@m.command("create_template", resource="papers.templates", action="manage", title="Tạo mẫu văn bản", agent="propose",
           shows=("papers.template_view", "template_id"))
async def create_template(ctx: Ctx, inp: NewTemplate) -> dict[str, Any]:
    """Mẫu mới + phiên bản 1 (nháp). Mẫu gợi ý của gói chỉ là điểm khởi đầu — chép thành dữ liệu của công ty, công ty sửa / phát hành.
    ``Pack(records=…)`` của gói dùng đúng lệnh này để cài BỘ MẶC ĐỊNH (``only_new`` + ``publish``): chỉ thêm mã chưa có."""
    v = inp.model_dump()
    if inp.only_new and inp.starter and await ctx.val("SELECT 1 FROM papers.templates WHERE key = %s", (inp.starter.partition("/")[2],)):
        return {"added": False}
    if inp.starter:
        kind, _, key = inp.starter.partition("/")
        st = next((x for x in (await _subject(ctx, kind)).starters if x["key"] == key), None)
        if st is None:
            raise NotFound("Không có mẫu gợi ý này")
        v = {"subject_kind": kind, "key": st["key"], "name": st["name"], "abbr": st.get("abbr", ""), "body": st["body"],
             "signers": " | ".join(st.get("signers") or ())} | {k: x for k, x in v.items() if x and k != "starter"}
    missing = [label for k, label in (("name", "tên mẫu"), ("subject_kind", "loại hồ sơ"), ("body", "thân mẫu")) if not v[k]]
    if missing:
        raise Invalid("Thiếu " + ", ".join(missing) + " (hoặc chọn một mẫu gợi ý)")
    if not v["key"]:   # máy đặt từ tên (0225 — không bắt người dùng nghĩ ra mã); đã có ⇒ thêm hậu tố chữ (mã mẫu chỉ gồm chữ thường và gạch dưới)
        v["key"] = await _template_key(ctx, v["name"])
    if not v["abbr"]:
        v["abbr"] = _abbr(v["name"])
    subj = await _subject(ctx, v["subject_kind"])
    _check_body(subj, v["body"])
    v.setdefault("authority", "")   # mẫu gợi ý của gói: trống = theo loại hồ sơ
    if v["authority"] and v["authority"] not in {o["value"] for o in await authority_options(ctx, NoInput())}:
        raise Invalid("Chọn người phát hành trong danh sách thẩm quyền đã khai")
    if await ctx.val("SELECT 1 FROM papers.templates WHERE key = %s", (v["key"],)):
        raise Invalid(f"Đã có mẫu mã {v['key']}")
    tid = await ctx.val("""INSERT INTO papers.templates (key, name, subject_kind, abbr, authority, created_by) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                        (v["key"], v["name"].strip(), v["subject_kind"], v["abbr"].upper(), v["authority"], ctx.p.user_id))
    await ctx.exec("INSERT INTO papers.template_versions (template_id, version, body, signers, created_by) VALUES (%s, 1, %s, %s, %s)",
                   (tid, v["body"], _signers(v["signers"]), ctx.p.user_id))
    if inp.publish:
        await ctx.exec("UPDATE papers.template_versions SET status = 'published', published_by = %s, published_at = now() WHERE template_id = %s",
                       (ctx.p.user_id, tid))
    return {"template_id": tid, "version": 1, "status": "published" if inp.publish else "draft", "added": True}


class TemplateActive(Input):
    template_id: UUID = ffield("Mẫu", hidden=True)
    active: bool = ffield("Đang dùng", True, help="Tắt = mẫu không còn trong danh sách soạn; văn bản đã soạn giữ nguyên, bật lại bất cứ lúc nào")


@m.command("set_template_active", resource="papers.templates", action="manage", title="Ngừng dùng / dùng lại mẫu", agent="propose")
async def set_template_active(ctx: Ctx, inp: TemplateActive) -> dict[str, Any]:
    """Công ty bỏ mẫu mặc định của gói không dùng tới: ngừng dùng (không xoá — văn bản đã soạn trỏ về mẫu). Bộ mặc định không tự thêm lại: mã mẫu đã có."""
    t = await _template(ctx, inp.template_id)
    await ctx.exec("UPDATE papers.templates SET active = %s WHERE id = %s", (inp.active, t["id"]))
    return {"template_id": t["id"], "active": inp.active, "summary": f"Mẫu “{t['name']}” " + ("đang dùng" if inp.active else "đã ngừng dùng")}


def _abbr(name: str) -> str:
    """Viết tắt máy đoán: chữ cái đầu mỗi từ của tên mẫu, hoa, tối đa 12 ("Biên bản bàn giao" → "BBBG")."""
    return "".join(w[0] for w in re.findall(r"[^\W\d_]+", name.upper()))[:12]


async def _template_key(ctx: Ctx, name: str) -> str:
    words = re.findall(r"[a-z]+", fold(name))
    base = "_".join(words)[:37].strip("_") or "mau"
    base = base if len(base) >= 2 else base + "_mau"
    key, i = base, 0
    while await ctx.val("SELECT 1 FROM papers.templates WHERE key = %s", (key,)) or key == "letter":
        i += 1
        key = f"{base}_{chr(96 + i)}" if i <= 26 else f"{base}_{chr(96 + i // 26)}{chr(96 + i % 26 or 26)}"
    return key


class TemplateRef(Input):
    template_id: UUID = ffield("Mẫu", hidden=True)


class TemplateBody(TemplateRef):
    body: str = ffield("Thân mẫu", kind="textarea", max_length=60_000)
    signers: str = ffield("Các bên ký (cách nhau dấu |)", "", max_length=200)


async def _template(ctx: Ctx, template_id: UUID) -> dict[str, Any]:
    t = await ctx.one("SELECT * FROM papers.templates WHERE id = %s", (template_id,))
    if t is None:
        raise NotFound("Không thấy mẫu")
    return t


@m.command("save_template_version", resource="papers.templates", action="manage", title="Sửa thân mẫu (bản nháp)", agent="propose",
           shows=("papers.template_view", "template_id"))
async def save_template_version(ctx: Ctx, inp: TemplateBody) -> dict[str, Any]:
    """Sửa bản nháp đang có, hoặc mở phiên bản mới khi mọi phiên bản đã phát hành — bản đã phát hành không bao giờ đổi."""
    t = await _template(ctx, inp.template_id)
    _check_body(await _subject(ctx, t["subject_kind"]), inp.body)
    signers = _signers(inp.signers)
    got = await ctx.val("""UPDATE papers.template_versions SET body = %s, signers = %s WHERE template_id = %s AND status = 'draft'
                           RETURNING version""", (inp.body, signers, t["id"]))
    if got is None:
        got = await ctx.val("""INSERT INTO papers.template_versions (template_id, version, body, signers, created_by)
                               SELECT %s, coalesce(max(version), 0) + 1, %s, %s, %s FROM papers.template_versions WHERE template_id = %s
                               RETURNING version""", (t["id"], inp.body, signers, ctx.p.user_id, t["id"]))
    return {"template_id": t["id"], "version": got, "status": "draft"}


@m.command("publish_template", resource="papers.templates", action="manage", title="Phát hành phiên bản mẫu", agent="deny")
async def publish_template(ctx: Ctx, inp: TemplateRef) -> dict[str, Any]:
    """Bản nháp ⇒ phiên bản dùng cho bản soạn MỚI; bản soạn đã mở giữ phiên bản của nó. Phát hành là quyết định chính sách ⇒ chỉ người."""
    t = await _template(ctx, inp.template_id)
    v = await ctx.one("SELECT id, version, body FROM papers.template_versions WHERE template_id = %s AND status = 'draft' FOR UPDATE", (t["id"],))
    if v is None:
        raise Invalid("Mẫu không có bản nháp để phát hành")
    _check_body(await _subject(ctx, t["subject_kind"]), v["body"])   # danh mục ô của gói có thể đã đổi từ lúc lưu
    await ctx.exec("UPDATE papers.template_versions SET status = 'published', published_by = %s, published_at = now() WHERE id = %s",
                   (ctx.p.user_id, v["id"]))
    return {"template_id": t["id"], "version": v["version"], "status": "published"}


class OptionsOf(Input):
    subject_kind: str = ffield("Loại hồ sơ", "", hidden=True, help="Nút bấm từ một hồ sơ điền sẵn loại — danh sách mẫu chỉ còn mẫu của loại đó")


@m.query("template_options", resource="papers", title="Mẫu văn bản đã phát hành", creates="papers.create_template")
async def template_options(ctx: Ctx, inp: OptionsOf) -> list[dict[str, Any]]:
    """Mẫu người này soạn được: đã phát hành, còn dùng, loại hồ sơ nguồn đang có gói cung cấp. Nhóm theo loại hồ sơ (``group``), trong nhóm: mẫu công ty
    dùng nhiều nhất trước — mẫu đầu danh sách mang ``suggested`` (máy đoán, người đổi được). Mỗi dòng: ``label`` = tên mẫu — loại hồ sơ nguồn."""
    labels = {s.kind: s.label for s in (await subjects(ctx)).values()}
    rows = await ctx.all("""SELECT t.id, t.name, t.subject_kind,
                                   (SELECT count(*) FROM papers.documents d JOIN papers.template_versions v ON v.id = d.template_version_id
                                    WHERE v.template_id = t.id) AS used
                            FROM papers.templates t WHERE t.active AND (%(k)s = '' OR t.subject_kind = %(k)s)
                            AND EXISTS (SELECT 1 FROM papers.template_versions v WHERE v.template_id = t.id AND v.status = 'published')""", {"k": inp.subject_kind})
    rows = sorted((r for r in rows if r["subject_kind"] in labels), key=lambda r: (labels[r["subject_kind"]], -r["used"], r["name"]))
    out: list[dict[str, Any]] = []
    first: set[str] = set()
    for r in rows:
        o: dict[str, Any] = {"value": str(r["id"]), "label": f"{r['name']} — từ {labels[r['subject_kind']].lower()}", "group": labels[r["subject_kind"]]}
        if r["subject_kind"] not in first:
            first.add(r["subject_kind"])
            o["suggested"] = True
        out.append(o)
    return out


class RefOptions(Input):
    template_id: UUID | None = ffield("Mẫu", None, hidden=True)
    subject_kind: str = ffield("Loại hồ sơ", "", hidden=True)
    q: str = ffield("Tìm", "", max_length=80, hidden=True, help="Gõ một phần tên / mã — ô chọn có tìm (nếu app hỗ trợ) gửi kèm")


@m.query("draft_ref_options", resource="papers", title="Hồ sơ lấy số liệu cho văn bản")
async def draft_ref_options(ctx: Ctx, inp: RefOptions) -> list[dict[str, Any]]:
    """Danh sách hồ sơ NGƯỜI NÀY đọc được, theo loại hồ sơ của mẫu — tên người nhìn hiểu (gói viết nhãn), không mã trơ. Gói trả ``{value, label}``
    qua điểm cắm ``papers.subject_options`` (chạy bằng quyền người soạn — RLS của gói áp). Chưa biết mẫu / loại: nếu mọi mẫu đang dùng cùng một loại hồ sơ
    thì theo loại đó; nhiều loại ⇒ gộp, nhãn mang tên loại để phân biệt."""
    subs = await subjects(ctx)
    kind = inp.subject_kind
    if inp.template_id is not None:
        kind = await ctx.val("SELECT subject_kind FROM papers.templates WHERE id = %s", (inp.template_id,)) or kind
    kinds = [kind] if kind else sorted({r["subject_kind"] for r in await ctx.all(
        "SELECT DISTINCT subject_kind FROM papers.templates WHERE active")} & set(subs))
    out: list[dict[str, Any]] = []
    for k in kinds:
        if k not in subs:
            continue
        for got in await ctx.plugs("papers.subject_options", subject_kind=k, q=inp.q.strip()[:80]):
            out += [{"value": o["value"], "label": o["label"] if len(kinds) == 1 else f"{subs[k].label} · {o['label']}"} for o in got]
    return out[:300]


@m.query("templates_list", resource="papers", title="Mẫu văn bản")
async def templates_list(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    labels = {s.kind: s.label for s in (await subjects(ctx)).values()}
    rows = await ctx.all("""SELECT t.id, t.name, t.key, t.abbr, t.subject_kind, t.active,
                                   (SELECT max(version) FROM papers.template_versions v WHERE v.template_id = t.id AND v.status = 'published') AS published,
                                   (SELECT version FROM papers.template_versions v WHERE v.template_id = t.id AND v.status = 'draft') AS draft
                            FROM papers.templates t ORDER BY t.name""")
    for r in rows:
        r["subject"] = labels.get(r["subject_kind"], f"{r['subject_kind']} (gói đang tắt)")
        r["has_draft"] = r["draft"] is not None
        r["state"] = "off" if not r["active"] else "draft" if r["published"] is None else "published"
    return ui.table(rows, [
        ui.col("name", "Mẫu", width=220), ui.col("subject", "Lấy số liệu từ"), ui.col("abbr", "Viết tắt"),
        ui.col("published", "Phiên bản đang dùng", "number"), ui.col("draft", "Bản nháp", "number"),
        ui.col("state", "Trạng thái", "badge", labels={"draft": "Chưa phát hành", "published": "Đang dùng", "off": "Ngừng dùng"},
               tones={"draft": "orange", "published": "green", "off": "gray"}),
    ], title="Mẫu văn bản", limit=500)


@m.query("template_view", resource="papers", title="Xem mẫu văn bản")
async def template_view(ctx: Ctx, inp: TemplateRef) -> dict[str, Any]:
    t = await _template(ctx, inp.template_id)
    versions = await ctx.all("""SELECT v.version, v.status, v.body, v.signers, v.published_at, u.name AS publisher FROM papers.template_versions v
                                LEFT JOIN core.users u ON u.id = v.published_by WHERE v.template_id = %s ORDER BY v.version DESC""", (t["id"],))
    subj = (await subjects(ctx)).get(t["subject_kind"])
    latest = versions[0]
    pub = next((v for v in versions if v["status"] == "published"), None)
    sections = [
        ui.section("Thông tin", [
            ui.field("Tên mẫu", t["name"]), ui.field("Mã", t["key"]), ui.field("Viết tắt trong số ký hiệu", t["abbr"] or "(không)"),
            ui.field("Lấy số liệu từ", subj.label if subj else f"{t['subject_kind']} (gói đang tắt)"),
            ui.field("Phiên bản đang dùng", f"v{pub['version']} — {pub['publisher'] or 'bộ mẫu mặc định của gói'}, {pub['published_at'].astimezone(zone()):%d/%m/%Y}" if pub
                     else "(chưa phát hành — chưa soạn được)"),
            ui.field("Các bên ký", " · ".join(latest["signers"]) or "(không khung ký)"),
            ui.field("Ai phát hành", authority_label(t["authority"] or (subj.authority if subj else OFFICE))),
        ]),
        ui.section(f"Thân mẫu v{latest['version']} — {'bản nháp' if latest['status'] == 'draft' else 'đã phát hành'}", text=latest["body"]),
    ]
    if subj:
        sections.append(ui.section("Ô dùng được trong thân mẫu",
                                   [ui.field("{{" + f.key + "}}", f"{f.label} · {KINDS[f.kind]}" + (f" ({f.help})" if f.help else "")
                                             + (" · người soạn nhập được khi hồ sơ trống" if f.person else ""))
                                    for f in subj.fields.values()]))
    page = ui.detail(t["name"], sections, subtitle=f"Mẫu văn bản · {len(versions)} phiên bản")
    page["actions"] = [ui.act("papers.save_template_version", "Sửa thân mẫu", {"template_id": str(t["id"])},
                              values={"body": latest["body"], "signers": " | ".join(latest["signers"])})]
    if latest["status"] == "draft":
        page["actions"].append(ui.act("papers.publish_template", f"Phát hành v{latest['version']}", {"template_id": str(t["id"])}, "primary"))
    return page


# ── Lập giấy từ hồ sơ ──

class StartDraft(Input):
    """Lập TỪ HỒ SƠ (ADR 0251): nút trên thẻ hồ sơ của gói điền sẵn hồ sơ + loại; mẫu tự chọn = mẫu đang dùng của công ty cho loại đó (nhiều mẫu mới hỏi)."""
    template_id: UUID | None = ffield("Mẫu", None, options_query="papers.template_options", options_with=("subject_kind",),
                                      help="Để trống — dùng mẫu đang dùng của công ty cho loại hồ sơ này (có nhiều mẫu thì chọn)")
    ref: str = ffield("Hồ sơ", max_length=60, options_query="papers.draft_ref_options", options_with=("template_id", "subject_kind"),
                      help="Hồ sơ lấy số liệu cho giấy — chỉ hiện hồ sơ bạn xem được")
    subject_kind: str = ffield("Loại hồ sơ", "", hidden=True, agent=False)


async def _published(ctx: Ctx, template_id: UUID | None, subject_kind: str) -> dict[str, Any]:
    """Phiên bản đang dùng của mẫu được chọn — hoặc, khi không chọn, của MẪU DUY NHẤT công ty đang dùng cho loại hồ sơ đó."""
    rows = await ctx.all("""SELECT DISTINCT ON (t.id) t.id AS template_id, t.key, t.name, t.abbr, t.subject_kind, t.authority, v.id AS version_id,
                                   v.version, v.body, v.signers
                            FROM papers.templates t JOIN papers.template_versions v ON v.template_id = t.id AND v.status = 'published'
                            WHERE t.active AND (t.id = %(t)s OR (%(t)s::uuid IS NULL AND t.subject_kind = %(k)s))
                            ORDER BY t.id, v.version DESC""", {"t": template_id, "k": subject_kind})
    if template_id is None and not subject_kind:
        raise Invalid("Chọn mẫu")
    if not rows:
        raise NotFound("Mẫu không còn dùng hoặc chưa có phiên bản phát hành" if template_id else
                       "Công ty chưa có mẫu đang dùng cho loại hồ sơ này — Quản trị › Mẫu văn bản")
    if len(rows) > 1:
        raise Invalid("Có " + str(len(rows)) + " mẫu cho loại hồ sơ này (" + ", ".join(sorted(r["name"] for r in rows)) + ") — chọn một mẫu")
    return rows[0]


@m.command("start_draft", resource="papers", title="Lập giấy từ hồ sơ", agent="allow", auto=True, risk="low", shows=("papers.view", "paper_id"))
async def start_draft(ctx: Ctx, inp: StartDraft) -> dict[str, Any]:
    """Lập giấy theo mẫu từ một hồ sơ, bằng quyền người lập (không thấy hồ sơ ⇒ không lập được). Trợ lý / luật tự động lập sẵn được (rủi ro thấp: chưa
    cấp số, chưa ai thấy ngoài người lập + người có thẩm quyền); PHÁT HÀNH luôn do người bấm (``papers.issue`` agent="deny")."""
    t = await _published(ctx, inp.template_id, inp.subject_kind)
    subj = await _subject(ctx, t["subject_kind"])
    blocks = _check_body(subj, t["body"])
    rec = await record(ctx, t["subject_kind"], inp.ref)
    if rec.get("blocked"):
        raise Invalid(f"{rec['title']}: {rec['blocked']} — không lập {t['name'].lower()} được")
    sheet = await _sheet(ctx, subj, blocks, rec, today(), {})
    out = await _insert(ctx, kind=KIND_PREFIX + t["key"], kind_label=t["name"], title=sheet.filled.title.upper(), abbr=t["abbr"], layout="contract",
                        summary=f"{t['name']} — {rec['title']}", recipient="", content=_content(t, sheet.filled, sheet.notes), ref=rec["ref"],
                        authority=t["authority"] or subj.authority)
    await ctx.exec("UPDATE papers.documents SET template_version_id = %s, subject_kind = %s, subject_id = %s WHERE id = %s",
                   (t["version_id"], t["subject_kind"], rec["subject_id"], out["paper_id"]))
    return {**out, "missing": [subj.fields[k].label for k in sheet.missing], "company_missing": [subj.fields[k].label for k in sheet.company]}


@dataclass
class Sheet:
    """Tờ giấy theo SỐ HIỆN TẠI (0251): ô (nguồn từng ô), nội dung đã điền, chú thích người nhập, ô thiếu chia theo ai bổ sung."""
    rows: dict[str, dict[str, Any]]
    filled: Any
    notes: list[str]
    missing: list[str]   # ô thiếu CỦA TỜ GIẤY: hồ sơ chưa có / người nhập được
    company: list[str]   # thông tin công ty / pháp nhân còn trống — Quản trị khai MỘT lần, không điền trên từng giấy
    mark: str


async def _sheet(ctx: Ctx, subj: Subject, blocks: list[Any], rec: dict[str, Any], draft_on: date,
                 entries: dict[str, dict[str, Any]]) -> Sheet:
    rows = _rows(subj, list(used(blocks)), rec, await _common(ctx, rec.get("entity_id"), draft_on), entries)
    marks, notes = await _notes(ctx, rows)
    filled = fill(blocks, subj.fields, {k: r["value"] for k, r in rows.items()}, marks)
    for b in filled.blocks:   # lớp của tờ giấy: ô CÔNG TY (pháp nhân, tài khoản…) tách khỏi số từ hồ sơ — sửa ở hai chỗ khác nhau
        for r in b.get("runs", ()):
            if r.get("k") in COMMON_KEYS and r.get("layer") == "record":
                r["layer"] = "company"
    return Sheet(rows, filled, notes, [k for k in filled.missing if k not in COMMON_KEYS], [k for k in filled.missing if k in COMMON_KEYS],
                 _mark(rows, rec))


async def _entries(ctx: Ctx, doc_id: Any, subj: Subject) -> dict[str, dict[str, Any]]:
    """Dữ kiện người nhập của giấy — dòng mới nhất mỗi ô; chỉ ô gói cho người nhập (ô công ty / ô hồ sơ không bao giờ lấy từ đây)."""
    got = await ctx.all("""SELECT DISTINCT ON (key) key, value, display, reason, entered_by, entered_at FROM papers.entries
                           WHERE document_id = %s ORDER BY key, entered_at DESC, id DESC""", (doc_id,))
    return {r["key"]: {"key": r["key"], "label": subj.fields[r["key"]].label, "kind": subj.fields[r["key"]].kind, "value": r["value"],
                       "display": r["display"], "origin": "person", "source": {"person": True}, "reason": r["reason"],
                       "entered_by": r["entered_by"], "entered_at": r["entered_at"]}
            for r in got if r["key"] in subj.fields and subj.fields[r["key"]].person and r["key"] not in COMMON_KEYS}


async def _template_of(ctx: Ctx, d: dict[str, Any]) -> tuple[dict[str, Any], Subject, list[Any]]:
    t = await ctx.one("""SELECT t.key, t.name, t.abbr, t.subject_kind, v.version, v.body, v.signers FROM papers.template_versions v
                         JOIN papers.templates t ON t.id = v.template_id WHERE v.id = %s""", (d["template_version_id"],))
    assert t is not None
    return t, await _subject(ctx, t["subject_kind"]), parse(t["body"])   # phiên bản đã phát hành ⇒ thân đã qua kiểm lúc phát hành


def _draft_on(d: dict[str, Any]) -> date:
    return d["created_at"].astimezone(zone()).date()


async def _record_of(ctx: Ctx, d: dict[str, Any], *, clicker: bool = False) -> dict[str, Any]:
    """Số liệu hồ sơ của giấy. ``clicker``: trong CĂN CỨ của lệnh (quyền hệ thống, chỉ đọc) — đọc bằng quyền NGƯỜI BẤM nạp lại (``principal_of``): số liệu
    gói đọc qua ``call_as`` lồng (căn, dự án…) không chạy được bằng quyền hệ thống, và người xem / người bấm là một người nên dấu lúc xem = dấu lúc bấm."""
    if clicker and ctx.p.user_id and (who := await ctx.principal_of(ctx.p.user_id)) is not None:
        got = [r for name in registry.PLUGS.get("papers.field_values", ())
               if (r := await ctx.call_as_optional(name, who, subject_kind=d["subject_kind"], ref=d["ref"])) is not None]
        if not got:
            raise NotFound("Không thấy hồ sơ")
        rec = got[0]
    else:
        rec = await record(ctx, d["subject_kind"], d["ref"])   # mã chuẩn gói đã trả lúc lập
    if str(rec["subject_id"]) != str(d["subject_id"]):
        raise Invalid(f"Mã {d['ref']} giờ trỏ tới hồ sơ khác — lập giấy mới")
    return rec


async def live(ctx: Ctx, d: dict[str, Any]) -> tuple[dict[str, Any], Subject, dict[str, Any], Sheet]:
    """Giấy chưa cấp số theo SỐ MỚI NHẤT, bằng quyền NGƯỜI ĐANG XEM / BẤM (0251) — mở, tải, ban hành đều qua đây."""
    t, subj, blocks = await _template_of(ctx, d)
    rec = await _record_of(ctx, d)
    return t, subj, rec, await _sheet(ctx, subj, blocks, rec, _draft_on(d), await _entries(ctx, d["id"], subj))


async def live_document(ctx: Ctx, d: dict[str, Any]) -> dict[str, Any]:
    """Dòng giấy với nội dung điền theo số hiện tại (để dựng .docx / PDF của bản chưa cấp số)."""
    t, _, _, sheet = await live(ctx, d)
    return {**d, "content": _content({**t, "signers": t["signers"]}, sheet.filled, sheet.notes), "title": sheet.filled.title.upper()}


def _label(subj: Subject, ks: list[str]) -> str:
    return ", ".join(subj.fields[k].label if k in subj.fields else k for k in ks)


COMPANY_FIX = "Quản trị khai một lần ở Quản trị › Pháp nhân / Thông tin công ty trên giấy tờ"


async def freeze(ctx: Ctx, d: dict[str, Any]) -> None:
    """Gọi từ ``papers.issue`` (đã khoá dòng, đã kiểm thẩm quyền): đọc số MỚI NHẤT bằng quyền người bấm; còn ô thiếu ⇒ chặn; đủ ⇒ CHỐT nội dung lên giấy
    + một bản chốt nguồn từng ô (``via='issue'``). Số đổi giữa lúc xem và lúc bấm đã bị căn cứ của lệnh chặn trước handler (``issue_basis``)."""
    t, subj, rec, sheet = await live(ctx, d)
    if rec.get("blocked"):
        raise Invalid(f"{rec['title']}: {rec['blocked']} — không {FAMILIES[family_of(d['authority'])]['issue'].lower()} được")
    if sheet.missing or sheet.company:
        person = [k for k in sheet.missing if subj.fields[k].person]
        record_ = [k for k in sheet.missing if k not in person]
        parts = ([f"công ty chưa khai {_label(subj, sheet.company)} — {COMPANY_FIX}"] if sheet.company else []) + \
                ([f"hồ sơ còn thiếu: {_label(subj, record_)} — bổ sung ở hồ sơ gốc"] if record_ else []) + \
                ([f"còn thiếu: {_label(subj, person)} — chạm ô trống trên giấy để điền"] if person else [])
        raise Invalid(f"Chưa {FAMILIES[family_of(d['authority'])]['issue'].lower()} được — " + "; ".join(parts) + ".")
    rev = d["rev"] + 1
    await ctx.exec("UPDATE papers.documents SET content = %s, title = %s, rev = %s WHERE id = %s",
                   (Jsonb(_content(t, sheet.filled, sheet.notes)), sheet.filled.title.upper(), rev, d["id"]))
    await _write_rev(ctx, d["id"], rev, sheet.rows, sheet.filled.markdown, sheet.mark)


async def issue_basis(ctx: Ctx, paper_id: UUID) -> dict[str, str]:
    """Căn cứ của ``papers.issue`` (0071): dấu số liệu hồ sơ + dấu dữ kiện người nhập của giấy theo mẫu chưa cấp số. Chạy bằng quyền hệ thống, chỉ đọc
    (executor), khoá dòng giấy tới khi lệnh xong. Giấy khác (công văn…) ⇒ không căn cứ."""
    # lúc bấm (lệnh): khoá dòng giấy tới khi lệnh xong — không khe giữa so và chạy (0071). Lúc xem (trang dựng nút, transaction chỉ đọc): đọc thường
    lock = await ctx.val("SELECT current_setting('transaction_read_only') = 'off'")
    d = await ctx.one("SELECT * FROM papers.documents WHERE id = %s FOR UPDATE" if lock else "SELECT * FROM papers.documents WHERE id = %s", (paper_id,))
    if d is None or d["template_version_id"] is None or d["status"] != "draft":
        return {}
    who = await ctx.principal_of(ctx.p.user_id) if ctx.p.user_id else None
    if who is None or not who.can(d["authority"], "manage"):
        return {}   # người bấm không có thẩm quyền loại giấy này ⇒ không căn cứ; handler báo đúng lỗi (không thấy / không có quyền), không lộ "giấy có đó"
    _, subj, blocks = await _template_of(ctx, d)
    try:
        rec = await _record_of(ctx, d, clicker=True)
    except (NotFound, Invalid):
        return {}   # hồ sơ mất / trỏ chỗ khác ⇒ handler báo lỗi rõ
    entries = await _entries(ctx, d["id"], subj)
    rows = _rows(subj, list(used(blocks)), rec, await _common(ctx, rec.get("entity_id"), _draft_on(d)), entries)
    return {f"Số liệu {subj.label.lower()} {d['ref']}": _mark(rows, rec),
            "Dữ kiện người nhập trên giấy": mark({k: [r["value"], str(r["entered_by"]), r["entered_at"].isoformat()] for k, r in entries.items()})}


class SetField(Input):
    paper_id: UUID = ffield("Giấy", hidden=True)
    key: str = ffield("Ô cần điền", hidden=True, max_length=60)
    value: str = ffield("Giá trị", max_length=500, help="Số tiền: 50.000.000 · ngày: 08/11/2026")
    reason: str = ffield("Căn cứ", "", max_length=500, help="Vd “khách cung cấp khi ký” — lưu cùng giấy, không in lên giấy")


def can_fill(ctx: Ctx, d: dict[str, Any]) -> bool:
    """Điền ô thiếu của giấy chưa cấp số: người lập hoặc người có thẩm quyền loại giấy đó."""
    return d["status"] == "draft" and can_discard(ctx, d)


@m.command("set_field", resource="papers", title="Điền ô còn thiếu", agent="propose", shows=("papers.view", "paper_id"))
async def set_field(ctx: Ctx, inp: SetField) -> dict[str, Any]:
    """Dữ kiện hồ sơ KHÔNG giữ (vd địa chỉ liên hệ của khách): người điền ngay trên giấy, ghi tên + lúc + căn cứ; giấy in rõ ô này là người nhập.
    Ô hồ sơ đã có giá trị không sửa ở đây — sửa ở hồ sơ gốc, giấy tự theo (một nguồn cho mỗi con số)."""
    d = await ctx.one("SELECT * FROM papers.documents WHERE id = %s AND template_version_id IS NOT NULL FOR UPDATE", (inp.paper_id,))
    if d is None:
        raise NotFound("Không thấy giấy lập theo mẫu")
    await require_seen(ctx, d)
    if d["status"] != "draft":
        raise Invalid("Giấy đã cấp số — không điền thêm được")
    if not can_fill(ctx, d):
        raise Denied("Chỉ người lập giấy hoặc người có thẩm quyền loại giấy này điền được")
    _, subj, blocks = await _template_of(ctx, d)
    f = subj.fields.get(inp.key)
    if f is None or inp.key not in used(blocks):
        raise NotFound("Mẫu này không có ô đó")
    if inp.key in COMMON_KEYS:
        raise Invalid(f"“{f.label}” là thông tin công ty / pháp nhân — {COMPANY_FIX}, không điền trên từng giấy")
    rec = await _record_of(ctx, d)
    if not f.person or not empty(jsonable(rec.get("values") or {}).get(inp.key)):
        raise Invalid(f"“{f.label}” lấy từ hồ sơ — sửa / bổ sung ở hồ sơ gốc, giấy tự theo")
    if CONTROL.search(inp.reason):
        raise Invalid("Căn cứ có ký tự điều khiển — gõ lại")
    try:
        value = parse_value(inp.value, f.kind)
    except ValueError as e:
        raise Invalid(f"{f.label}: {e}") from None
    await ctx.exec("INSERT INTO papers.entries (document_id, key, value, display, reason, entered_by) VALUES (%s, %s, %s, %s, %s, %s)",
                   (d["id"], inp.key, Jsonb(value), fmt(value, f.kind), inp.reason.strip(), ctx.p.user_id))
    return {"paper_id": d["id"], "key": inp.key, "summary": f"{f.label}: {fmt(value, f.kind)}"}


# ── Ai thấy bản soạn: bản soạn chép số liệu hồ sơ ⇒ chỉ người ĐỌC ĐƯỢC hồ sơ nguồn (quyền của chính họ, hỏi gói qua điểm cắm) mới thấy ──

async def seen(ctx: Ctx, docs: list[dict[str, Any]]) -> set[str]:
    """Mã những giấy theo mẫu trong ``docs`` mà người xem đọc được hồ sơ nguồn — một lần hỏi mỗi loại hồ sơ. Gói tắt / không trả ⇒ không thấy."""
    by: dict[str, list[str]] = {}
    for d in docs:
        if d.get("template_version_id") is not None:
            by.setdefault(d["subject_kind"], []).append(str(d["subject_id"]))
    ok: set[str] = set()
    for kind, ids in by.items():
        for got in await ctx.plugs("papers.subjects_seen", subject_kind=kind, ids=sorted(set(ids))):
            ok |= {str(x) for x in got}
    return {str(d["id"]) for d in docs if d.get("template_version_id") is not None and str(d["subject_id"]) in ok}


async def visible(ctx: Ctx, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Giấy không theo mẫu giữ nguyên luật cũ (RLS papers); giấy theo mẫu thêm điều kiện đọc được hồ sơ nguồn."""
    ok = await seen(ctx, docs)
    return [d for d in docs if d.get("template_version_id") is None or str(d["id"]) in ok]


async def require_seen(ctx: Ctx, d: dict[str, Any]) -> None:
    if d.get("template_version_id") is not None and not await seen(ctx, [d]):
        raise NotFound("Không thấy giấy tờ")


# ── Xem: CHÍNH TỜ GIẤY — "Thuộc" hồ sơ nào đầu trang, ô thiếu là chỗ trống chạm để điền, nút chính + Tải; thông tin phụ một dòng nhỏ ──

def _when(ts: Any) -> str:
    return f"{ts.astimezone(zone()):%d/%m/%Y %H:%M}" if ts else ""


async def _belongs(ctx: Ctx, d: dict[str, Any], subj: Subject | None, title: str) -> dict[str, Any]:
    """Dòng "Thuộc: <loại hồ sơ> <mã> — <tên>" — chạm mở hồ sơ khi loại đó khai đối tượng (``m.subject``) và người xem thấy nó."""
    label = subj.label if subj else d["subject_kind"]
    text = f"{label} {d['ref']}" + (f" — {title}" if title and title != d["ref"] else "")
    got = await ctx.call_as_optional(registry.SUBJECTS[d["subject_kind"]][1], ctx.p, subject_id=d["subject_id"]) \
        if d["subject_kind"] in registry.SUBJECTS else None
    opened = (got or {}).get("open")
    return ui.field("Thuộc", text, open={**opened, "title": label} if opened else None)


def _source(r: dict[str, Any], names: dict[str, str]) -> str:
    if r["origin"] == "person":
        return f"{r['display']} — {names.get(str(r['entered_by']), 'người nhập')} nhập {_when(r['entered_at'])}" + (f" · {r['reason']}" if r["reason"] else "")
    return f"{r['display']} — {'thông tin công ty' if r['source'].get('common') else 'từ hồ sơ ' + str(r['source'].get('ref', ''))}"


async def draft_page(ctx: Ctx, d: dict[str, Any]) -> dict[str, Any]:
    """Một trang cho mọi trạng thái: chưa cấp số ⇒ số SỐNG (đọc lại hồ sơ bằng quyền người xem); đã cấp số ⇒ bản chốt lúc ban hành."""
    fam = FAMILIES[family_of(d["authority"])]
    t = await ctx.one("""SELECT t.name, t.subject_kind, v.version FROM papers.template_versions v JOIN papers.templates t ON t.id = v.template_id
                         WHERE v.id = %s""", (d["template_version_id"],))
    assert t is not None
    subj = (await subjects(ctx)).get(t["subject_kind"])
    author = await ctx.val("SELECT name FROM core.users WHERE id = %s", (d["created_by"],)) or ""
    head: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    if d["status"] == "draft" and subj is not None:
        _, _, rec, sheet = await live(ctx, d)
        head.append(await _belongs(ctx, d, subj, rec.get("title") or ""))
        content = _content({"name": t["name"], "version": t["version"], "signers": d["content"].get("signers", [])}, sheet.filled, sheet.notes)
        rows = sheet.rows
        fill_ok = can_fill(ctx, d)
        if rec.get("blocked"):
            sections.append(ui.section("Không phát hành được", text=f"{rec['title']}: {rec['blocked']}."))
        if sheet.missing:   # ô trống của TỜ GIẤY — chạm để điền (người nhập được) / bổ sung ở hồ sơ (ô hồ sơ)
            gaps = []
            for k in sheet.missing:
                f = subj.fields[k]
                if f.person and fill_ok:
                    fills.append(ui.act("papers.set_field", f"Điền: {f.label}", {"paper_id": str(d["id"]), "key": k}, "primary"))
                    gaps.append(ui.field(f.label, "", action=fills[-1], placeholder="Chạm để điền"))
                else:
                    gaps.append(ui.field(f.label, "Hồ sơ chưa có (hoặc bạn không được xem) — bổ sung ở hồ sơ gốc" if not f.person else "Người lập giấy điền"))
            sections.append(ui.section(f"Còn thiếu — chưa {fam['issue'].lower()} được", gaps))
        if sheet.company:   # ô CÔNG TY: một dòng, không bắt điền từng giấy; người quản trị thấy đường tới đúng màn khai
            admin = ctx.p.can("core.admin", "manage")   # người khai được (quản trị) mới có đường tới màn khai; người khác chỉ đọc một dòng
            sections.append(ui.section("", [ui.field("Thông tin công ty", f"Công ty chưa khai {_label(subj, sheet.company).lower()} — {COMPANY_FIX}",
                                                     open={"query": "papers.settings_view", "input": {}, "title": "Thông tin công ty trên giấy tờ"} if admin
                                                     else None)]))
        if can_issue(ctx, d) and not rec.get("blocked"):
            actions.append(issue_action(d, executor.basis_token(await executor.basis_of(ctx, registry.OPS["papers.issue"], {"paper_id": str(d["id"])}))))
    else:
        content = d["content"]
        rows = {r["key"]: r for r in await ctx.all("SELECT * FROM papers.fields WHERE document_id = %s AND rev = %s", (d["id"], d["rev"]))}
        head.append(await _belongs(ctx, d, subj, d["summary"].removeprefix(t["name"] + " — ")))
        head.append(ui.field("Số", d["symbol"] or "—"))
    actions += tail_actions(ctx, d)
    sheet = {"title": (content.get("title") or d["kind_label"]).upper(), "number": f"Số: {d['symbol']}" if d["symbol"] else
             f"Số: (cấp khi {fam['issue'].lower()})", "draft_mark": fam["draft_mark"] if d["status"] == "draft" else "",
             "blocks": content.get("blocks", []), "signers": content.get("signers", []), "notes": content.get("notes", []), "actions": fills,
             "legend": [{"layer": "record", "label": f"Số từ {(subj.label if subj else 'hồ sơ').lower()} — sửa ở hồ sơ, chạm để mở"},
                        {"layer": "company", "label": "Thông tin công ty — sửa ở Quản trị"},
                        {"layer": "missing", "label": "Còn thiếu — chạm để điền"}, {"layer": "person", "label": "Người nhập trên giấy (ghi tên, căn cứ)"}],
             # chạm số từ hồ sơ ⇒ mở đúng hồ sơ để sửa (cùng đích với dòng "Thuộc") — số không sửa trên giấy
             **({"open": head[0]["open"]} if head and head[0].get("open") else {})}
    names = {str(u["id"]): u["name"] for u in await ctx.all("SELECT id, name FROM core.users WHERE id = ANY(%s)",
                                                            ([r["entered_by"] for r in rows.values() if r["entered_by"]],))}
    shown = [r for r in sorted(rows.values(), key=lambda r: r["key"]) if r["value"] is not None]
    if shown:
        sections.append(ui.section("Chi tiết: số lấy từ đâu", [ui.field(r["label"], _source(r, names)) for r in shown]))
    name = file_name(d)
    who = f"{author} lập {_when(d['created_at'])}" if author else f"lập {_when(d['created_at'])}"
    page = ui.detail(d["summary"], [ui.section("", head), *sections],
                     subtitle=" · ".join((d["kind_label"], status_label(d), f"mẫu v{t['version']}", who)))
    page["original"] = {"title": "Bản in", "path": f"/v1/papers/{d['id']}/pdf", "filename": f"{name}.pdf", "page": None, "kind": "pdf"}
    page["links"] = [{"title": "Tải .docx", "path": f"/v1/papers/{d['id']}/docx", "filename": f"{name}.docx"},
                     {"title": "Tải .pdf", "path": f"/v1/papers/{d['id']}/pdf", "filename": f"{name}.pdf"}]
    page["actions"] = actions
    # TỜ GIẤY (0251): app vẽ chính tờ giấy làm màn mặc định — chữ chia mẩu theo lớp (``runs``: số từ hồ sơ khoá / ô thiếu chạm điền / người nhập), nút
    # điền ở ``sheet.actions`` (khoá ô = ``preset.key``). App chưa vẽ ``sheet`` ⇒ còn "Bản in" (PDF) + các mục trên. Chỉ THÊM trường.
    page["sheet"] = sheet
    return page


m.screens("docs", (
    Screen("paper_templates", "Mẫu văn bản", "doc.on.doc", "papers.templates_list", needs_action=True, group="Thiết lập",   # rà vai 18/11/2026: màn THIẾT LẬP — chỉ người sửa được thấy trên menu (người đọc dùng kết quả ở màn việc)
           actions=(A("papers.create_template", style="primary"),),
           row_actions=(A("papers.publish_template", bind={"template_id": "id"}, when={"has_draft": [True]}, style="primary"),
                        A("papers.set_template_active", "Ngừng dùng", bind={"template_id": "id"}, preset={"active": False}, when={"state": ["draft", "published"]}),
                        A("papers.set_template_active", "Dùng lại", bind={"template_id": "id"}, preset={"active": True}, when={"state": ["off"]})),
           open=Open("papers.template_view", {"template_id": "id"}, "Xem mẫu")),
), rank=-2)   # 0231: soạn theo mẫu + mẫu văn bản ngay sau giấy tờ


# ── .docx khuôn "contract": Quốc hiệu, tên văn bản, số, các mục, bảng, khung ký nhiều bên ──

def render_contract(d: dict[str, Any], s: dict[str, Any]) -> bytes:
    import io

    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt
    from docx.styles.style import ParagraphStyle

    c = d["content"]
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.top_margin, sec.bottom_margin, sec.left_margin, sec.right_margin = Cm(2), Cm(2), Cm(3), Cm(1.5)
    normal = doc.styles["Normal"]
    assert isinstance(normal, ParagraphStyle)
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(13)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")  # type: ignore[union-attr]
    center, right, just = WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.RIGHT, WD_ALIGN_PARAGRAPH.JUSTIFY

    def para(target: Any, text: str, *, bold: bool = False, italic: bool = False, size: float = 13, align: Any = None,
             underline: bool = False, indent: bool = False, after: float = 3) -> Any:
        p = target.add_paragraph()
        if align is not None:
            p.alignment = align
        if indent:
            p.paragraph_format.first_line_indent = Cm(1)
        p.paragraph_format.space_after = Pt(after)
        r = p.add_run(text)
        r.bold, r.italic, r.underline, r.font.size = bold, italic, underline, Pt(size)
        return p

    if d["status"] == "draft":
        para(doc, FAMILIES[family_of(d["authority"])]["draft_mark"], bold=True, size=12, align=right)
    para(doc, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", bold=True, size=12, align=center, after=0)
    para(doc, "Độc lập - Tự do - Hạnh phúc", bold=True, size=13, align=center, underline=True)
    para(doc, "")
    para(doc, (c.get("title") or d["kind_label"]).upper(), bold=True, size=14, align=center, after=0)
    number = d["symbol"] or (f"    /{d['abbr']}-{s['short_name'] or 'CT'}" if d["abbr"] else f"    /{s['short_name'] or 'CT'}")
    para(doc, f"Số: {number}", size=13, align=center, after=8)
    aligns = {"c": center, "r": right, "l": WD_ALIGN_PARAGRAPH.LEFT}
    for b in c.get("blocks", []):
        if b["type"] == "heading":
            para(doc, b["text"], bold=True, size=13, after=4)
        elif b["type"] == "bullet":
            p = para(doc, f"- {b['text']}", size=13, align=just)
            p.paragraph_format.left_indent = Cm(0.5)
        elif b["type"] == "table":
            table = doc.add_table(rows=1, cols=len(b["columns"]))
            table.style = "Table Grid"
            for j, h in enumerate(b["columns"]):
                cell = table.rows[0].cells[j]
                cell.paragraphs[0].text = ""
                para(cell, h, bold=True, size=12, align=center)
            for row in b["rows"]:
                cells = table.add_row().cells
                for j, v in enumerate(row):
                    cells[j].paragraphs[0].text = ""
                    para(cells[j], v, size=12, align=aligns[(b.get("align") or ["l"] * len(row))[j]])
            if tot := b.get("total"):
                cells = table.add_row().cells
                span = len(b["columns"]) - (len(tot) - 1)
                merged = cells[0].merge(cells[span - 1]) if span > 1 else cells[0]
                merged.paragraphs[0].text = ""
                para(merged, tot[0], bold=True, size=12, align=center)
                for j, v in enumerate(tot[1:]):
                    cells[span + j].paragraphs[0].text = ""
                    para(cells[span + j], v, bold=True, size=12, align=right)
            para(doc, "", after=2)
        else:
            para(doc, b["text"], size=13, align=just, indent=True)
    if notes := c.get("notes"):   # dữ kiện người nhập: luôn in, ngay trên khung ký
        para(doc, "", after=2)
        for n in notes:
            para(doc, n, italic=True, size=11, after=0)
    if signers := c.get("signers"):
        para(doc, "")
        cells = doc.add_table(rows=1, cols=len(signers)).rows[0].cells
        for cell, who in zip(cells, signers, strict=True):
            cell.paragraphs[0].text = ""
            para(cell, who, bold=True, size=13, align=center, after=0)
            para(cell, "(Ký, ghi rõ họ tên)", italic=True, size=12, align=center)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
