"""HỒ SƠ NHÚNG THEO CÔNG TY (ADR 0232, mục "Nhúng theo công ty") — CHÍNH SÁCH CỦA NHÀ VẬN HÀNH như hạng trợ lý AI (0193) và hồ sơ đọc tài liệu: chọn model embedding, số chiều,
máy chủ tự dựng (Ollama / vLLM / TEI) cho kho tài liệu của MỘT công ty. Tài nguyên ``core.ai_policy`` (khách không cấp được); trợ lý không đổi (``agent="deny"``); mỗi
lệnh ghi người vận hành + lý do. Chạy bằng ``python -m barista.ai_policy embed-show / embed-set / embed-clear`` (có chạy thử + kiểm máy chủ — runbook 20).
Lệnh này CHỈ ghi hồ sơ; việc nhúng lại là của vòng quét nền ``kb.embed_sweep`` (đoạn khác model ⇒ nhúng lại dần, trong lúc đó tìm từ khoá + đoạn đã nhúng model mới).
"""

import json
from typing import Any

from barista.kernel.ai_tiers import POLICY, _by, _who, _why
from barista.kernel.clock import today
from barista.kernel.core_ops import m
from barista.kernel.errors import Invalid
from barista.kernel.executor import Ctx
from barista.kernel.fields import Input, NoInput, field


class SetEmbedIn(Input):
    model: str | None = field("Model nhúng", None, max_length=120, help="nhà_cung_cấp:tên — google:gemini-embedding-001, openai:text-embedding-3-small, "
                              "openai-compatible:embeddinggemma (kèm base_url); để trống = giữ nguyên")
    off: bool = field("Tắt tìm theo nghĩa", False, help="Công ty này chỉ tìm từ khoá (đoạn đã nhúng vẫn giữ)")
    dims: int | None = field("Số chiều", None, ge=32, le=4000, help="Chuẩn 768; số khác cần chỉ mục HNSW riêng (kb.vectors add-index)")
    base_url: str | None = field("Địa chỉ máy chủ", None, max_length=200, help="Chỉ với openai-compatible, vd http://127.0.0.1:11434/v1")
    key_ref: str | None = field("Tên khoá", None, max_length=60, help="Tên mục trong llm_keys của bản cài (không dán khoá vào đây)")
    truncate: bool | None = field("Cắt Matryoshka", None, help="CHỈ cho model huấn luyện kiểu Matryoshka (EmbeddingGemma, nomic…)")
    query_prefix: str | None = field("Tiền tố câu hỏi", None, max_length=120)
    document_prefix: str | None = field("Tiền tố đoạn tài liệu", None, max_length=120)
    usd_per_mtok: float | None = field("Giá USD / triệu token", None, ge=0, le=1000, help="Máy tự dựng mặc định 0")
    min_sim: float | None = field("Ngưỡng giống", None, gt=0, le=0.99, help="Trống = mặc định bản cài (0,65 đo cho Gemini)")
    clear: list[str] = field("Bỏ khoá (về mặc định)", [], help="vd min_sim, query_prefix")
    rollback: bool = field("Quay lại hồ sơ trước", False, help="Đặt lại hồ sơ ngay trước lần đổi model gần nhất")
    dry_run: bool = field("Chỉ tính, không ghi", False, help="Trả hồ sơ SẼ áp, không ghi gì")
    by: str = _by()
    why: str = _why()


@m.command("set_embed_profile", resource=POLICY, action="manage", title="Đặt hồ sơ nhúng của công ty", agent="deny", category="setup")
async def set_embed_profile(ctx: Ctx, inp: SetEmbedIn) -> dict[str, Any]:
    """Gộp các khoá nêu vào hồ sơ nhúng của công ty (khoá không nêu giữ nguyên; ĐỔI model ⇒ không thừa hưởng địa chỉ / tiền tố của model cũ). Hồ sơ lưu ĐỦ khoá (dữ liệu tường
    minh). Đổi model / số chiều ⇒ nhớ hồ sơ cũ ở ``previous`` (quay lại = ``rollback``). Hiệu lực trong ≤ 30 giây (bản nhớ ngắn hạn); đoạn đã nhúng bằng model cũ không dùng
    nữa, vòng quét nhúng lại dần."""
    from barista.agent import embed_profile as ep

    if not inp.dry_run:
        _who(inp)
    row = await ctx.one("SELECT profile, previous FROM core.embed_profiles")
    cur_row = dict(row["profile"]) if row else None
    cur = ep.compose(cur_row)
    previous = dict(row["previous"]) if row and row["previous"] else None
    if inp.rollback:
        if previous is None:
            raise Invalid("Công ty chưa có hồ sơ trước để quay lại")
        new = ep.check(ep.compose(previous))
    else:
        upd = ep.validate({k: v for k, v in (
            ("model", "" if inp.off else inp.model), ("dims", inp.dims), ("base_url", inp.base_url), ("key_ref", inp.key_ref), ("truncate", inp.truncate),
            ("query_prefix", inp.query_prefix), ("document_prefix", inp.document_prefix), ("usd_per_mtok", inp.usd_per_mtok), ("min_sim", inp.min_sim)) if v is not None})
        base = cur if "model" not in upd or upd["model"] == cur.model else ep.Profile(model=upd["model"], dims=ep.STANDARD_DIMS)
        new = ep.apply(base, upd, "công ty")
        for k in inp.clear:
            if k not in ep.KEYS or k == "model":
                raise Invalid(f"Không bỏ được khoá “{k}”")
            new = ep.apply(new, {k: getattr(ep.Profile(), k)}, "công ty")
        new = ep.check(new)
    if not new.same_vectors(cur):
        previous = cur.as_dict()
    reembed = not new.same_vectors(cur) and not new.off
    if inp.dry_run:
        return {"profile": new.as_dict(), "reembed": reembed, "off": new.off, "dry_run": True, "summary": f"(chạy thử) hồ sơ nhúng sẽ là: {new.describe()}"}
    await ctx.exec("""INSERT INTO core.embed_profiles (profile, previous, updated_by) VALUES (%s::jsonb, %s::jsonb, %s)
                      ON CONFLICT (tenant_id) DO UPDATE SET profile = EXCLUDED.profile, previous = EXCLUDED.previous, updated_by = EXCLUDED.updated_by,
                                                            updated_at = now()""",
                   (json.dumps(new.as_dict()), json.dumps(previous) if previous else None, inp.by.strip()))
    ep.invalidate(ctx.p.tenant_id)
    return {"profile": new.as_dict(), "reembed": reembed, "off": new.off,
            "summary": f"Hồ sơ nhúng: {new.describe()}" + ("; các đoạn sẽ được nhúng lại bằng model mới (vòng quét nền)" if reembed else "")}


class ClearEmbedIn(Input):
    by: str = _by()
    why: str = _why()


@m.command("clear_embed_profile", resource=POLICY, action="manage", title="Bỏ hồ sơ nhúng riêng của công ty", agent="deny", category="setup")
async def clear_embed_profile(ctx: Ctx, inp: ClearEmbedIn) -> dict[str, Any]:
    """Về mặc định bản cài. Model khác mặc định ⇒ đoạn nhúng lại bằng model mặc định (như đổi model)."""
    from barista.agent import embed_profile as ep

    _who(inp)
    n = await ctx.exec("DELETE FROM core.embed_profiles")
    ep.invalidate(ctx.p.tenant_id)
    inst = ep.instance_profile()
    return {"removed": bool(n), "summary": (f"Đã bỏ hồ sơ nhúng riêng — dùng mặc định bản cài: {inst.describe()}" if n else "Công ty chưa có hồ sơ nhúng riêng")}


@m.query("embed_profile", resource=POLICY, title="Hồ sơ nhúng", agent="deny")
async def embed_profile_query(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Hồ sơ đang áp (đã chồng mặc định bản cài + dòng công ty), dòng riêng + hồ sơ trước, và lượt / token / chi phí nhúng THÁNG NÀY theo model (``core.ai_usage`` ``kb:embed*``)."""
    from barista.agent import embed_profile as ep

    row = await ctx.one("SELECT profile, previous, updated_at, updated_by FROM core.embed_profiles")
    eff = ep.compose(dict(row["profile"]) if row else None)
    first = today().replace(day=1)
    used = await ctx.all(
        """SELECT model, count(*) AS runs, coalesce(sum(input_tokens), 0) AS tokens, sum(cost_usd) AS usd, count(*) FILTER (WHERE cost_usd IS NULL) AS unpriced
           FROM core.ai_usage WHERE category LIKE 'kb:embed%%' AND at >= %s GROUP BY model ORDER BY tokens DESC""", (first,))
    return {"effective": eff.as_dict(), "describe": eff.describe(), "source": eff.source, "off": eff.off,
            "company_row": dict(row["profile"]) if row else None, "previous": dict(row["previous"]) if row and row["previous"] else None,
            "updated_at": row["updated_at"] if row else None, "updated_by": row["updated_by"] if row else None,
            "usage": [{"id": r["model"], "model": r["model"], "runs": int(r["runs"]), "tokens": int(r["tokens"]),
                       "usd": float(r["usd"]) if r["usd"] is not None else None, "unpriced": int(r["unpriced"])} for r in used]}
