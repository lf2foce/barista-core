"""HỒ SƠ ĐỌC TÀI LIỆU THEO CÔNG TY (ADR 0232) — CHÍNH SÁCH CỦA NHÀ VẬN HÀNH như hạng trợ lý AI (0193): chọn bộ đọc, model, công tắc tốn tiền (scan / bảng / chữ trong ảnh / mô tả ảnh),
trần trang mỗi tài liệu, hạn mức token tháng. Tài nguyên ``core.ai_policy`` (khách không cấp được); trợ lý không đổi (``agent="deny"``); mỗi lệnh ghi người vận hành + lý do.
Chạy bằng ``python -m barista.ai_policy docread-show / docread-set / docread-clear`` (runbook 20). Lớp mặc định của bản cài: ``settings.docread``.
"""

import json
from typing import Any

from barista.kernel.ai_tiers import POLICY, _by, _who, _why
from barista.kernel.clock import today
from barista.kernel.core_ops import m
from barista.kernel.errors import Invalid
from barista.kernel.executor import Ctx
from barista.kernel.fields import Input, NoInput, field


class SetProfileIn(Input):
    purpose: str = field("Mục đích", max_length=40, example="kb", help="kb · kb_images · brochure_images · invoice · credential… hoặc * = mọi mục đích")
    chain: list[str] = field("Chuỗi bộ đọc", [], help="Thứ tự, vd office, pypdf, model. Bỏ model khỏi chuỗi = không bao giờ tốn model. Trống = giữ nguyên")
    model: str | None = field("Model đọc", None, max_length=120, help="nhà_cung_cấp:tên_model; chuỗi rỗng = model mặc định của bản cài; để trống = giữ nguyên")
    scanned_pdf: bool | None = field("AI đọc trang scan", None)
    tables: str | None = field("Xử lý bảng", None, help="off · text · auto · model")
    images_ocr: bool | None = field("AI chép chữ trong ảnh", None)
    images_caption: bool | None = field("AI mô tả ảnh", None)
    max_model_pages: int | None = field("Trần trang AI đọc / tài liệu", None, ge=0, le=500)
    monthly_tokens: int | None = field("Hạn mức token đọc tài liệu / tháng", None, ge=1000, le=10**12)
    clear: list[str] = field("Bỏ khoá (về mặc định bản cài)", [], help="Các khoá hồ sơ muốn bỏ, vd monthly_tokens, model")
    by: str = _by()
    why: str = _why()


@m.command("set_docread_profile", resource=POLICY, action="manage", title="Đặt hồ sơ đọc tài liệu", agent="deny", category="setup")
async def set_docread_profile(ctx: Ctx, inp: SetProfileIn) -> dict[str, Any]:
    """Gộp các khoá nêu vào hồ sơ của (công ty, mục đích); khoá không nêu giữ nguyên. Hiệu lực trong vài chục giây (bản nhớ ngắn hạn)."""
    from barista.docread import policy

    _who(inp)
    purpose = inp.purpose.strip()
    if purpose != "*" and not policy.KEY.match(purpose):
        raise Invalid("Mục đích: chữ thường không dấu, số, gạch dưới (2–40 ký tự) hoặc * = mọi mục đích")
    row = await ctx.val("SELECT profile FROM core.docread_profiles WHERE purpose = %s", (purpose,)) or {}
    upd: dict[str, Any] = {k: v for k, v in (
        ("chain", inp.chain or None), ("model", inp.model), ("scanned_pdf", inp.scanned_pdf), ("tables", inp.tables), ("images_ocr", inp.images_ocr),
        ("images_caption", inp.images_caption), ("max_model_pages", inp.max_model_pages), ("monthly_tokens", inp.monthly_tokens)) if v is not None}
    clean = {**row, **policy.validate(upd)}
    for k in inp.clear:
        clean.pop(k, None)
    policy.validate(clean)
    await ctx.exec("""INSERT INTO core.docread_profiles (purpose, profile, updated_by) VALUES (%s, %s::jsonb, %s)
                      ON CONFLICT (tenant_id, purpose) DO UPDATE SET profile = EXCLUDED.profile, updated_by = EXCLUDED.updated_by, updated_at = now()""",
                   (purpose, json.dumps(clean), inp.by.strip()))
    policy.invalidate(ctx.p.tenant_id)
    return {"purpose": purpose, "profile": clean, "summary": f"Hồ sơ đọc “{purpose}”: {policy.compose(purpose if purpose != '*' else 'other', {purpose: clean}).describe()}"}


class ClearProfileIn(Input):
    purpose: str = field("Mục đích", max_length=40)
    by: str = _by()
    why: str = _why()


@m.command("clear_docread_profile", resource=POLICY, action="manage", title="Bỏ hồ sơ đọc tài liệu của công ty", agent="deny", category="setup")
async def clear_docread_profile(ctx: Ctx, inp: ClearProfileIn) -> dict[str, Any]:
    from barista.docread import policy

    _who(inp)
    n = await ctx.exec("DELETE FROM core.docread_profiles WHERE purpose = %s", (inp.purpose.strip(),))
    policy.invalidate(ctx.p.tenant_id)
    return {"purpose": inp.purpose, "removed": bool(n), "summary": f"Đã bỏ hồ sơ riêng “{inp.purpose}” — dùng mặc định của bản cài" if n else f"Công ty chưa có hồ sơ riêng “{inp.purpose}”"}


@m.query("docread_profiles", resource=POLICY, title="Hồ sơ đọc tài liệu", agent="deny")
async def docread_profiles(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Mỗi mục đích: hồ sơ đang áp (đã chồng mặc định bản cài + dòng công ty), dòng riêng của công ty, token + chi phí đã dùng THÁNG NÀY (``core.ai_usage`` ``docread:*``)."""
    from barista.docread import policy

    rows = {r["purpose"]: dict(r["profile"]) for r in await ctx.all("SELECT purpose, profile FROM core.docread_profiles")}
    first = today().replace(day=1)
    used = {r["category"]: r for r in await ctx.all(
        """SELECT category, count(*) AS runs, coalesce(sum(greatest(input_tokens - cache_read_tokens, 0) + output_tokens), 0) AS tokens, sum(cost_usd) AS usd
           FROM core.ai_usage WHERE category LIKE 'docread:%%' AND at >= %s GROUP BY category""", (first,))}
    out = []
    for purpose in dict.fromkeys([*policy.PURPOSES, *(k for k in rows if k != "*")]):
        eff = policy.compose(purpose, rows)
        u = used.get(f"docread:{purpose}") or {}
        out.append({"id": purpose, "purpose": purpose, "label": policy.PURPOSES.get(purpose, purpose), "effective": policy.as_dict(eff), "describe": eff.describe(),
                    "source": eff.source, "company_row": rows.get(purpose), "runs": int(u.get("runs") or 0), "tokens": int(u.get("tokens") or 0),
                    "usd": float(u["usd"]) if u.get("usd") is not None else None})
    return {"rows": out, "company_all": rows.get("*"), "tokens_month": sum(r["tokens"] for r in out)}
