"""Lệnh VẬN HÀNH: CHÍNH SÁCH TRỢ LÝ AI của một công ty (ADR 0193 — hạng, model, hạn mức token theo người, vai → hạng, báo cáo token). Của NHÀ VẬN HÀNH,
không phải của khách: quản trị công ty không thấy / sửa (tài nguyên ``core.ai_policy`` không cấp được cho vai nào). Chạy trên máy chủ của bản cài,
qua CÙNG executor bằng quyền hệ thống (một cửa — bất biến 1); mỗi lệnh ghi ``--by`` (người vận hành) + ``--why`` (lý do) vào nhật ký thao tác (không
hiện ở Nhật ký của khách). Trần chi cả công ty (USD, lưới an toàn) đặt bằng ``python -m barista.entitle --ai-budget`` (0092). Runbook 15.

  uv run python -m barista.ai_policy --tenant "Sàn mẫu" show
  uv run python -m barista.ai_policy --tenant "Sàn mẫu" set-tier basic --tokens 5000000 --by "Anh" --why "HĐ 11/2026"
  uv run python -m barista.ai_policy --tenant "Sàn mẫu" set-tier basic --model deepseek:deepseek-flash --by "Anh" --why "đổi model"
  uv run python -m barista.ai_policy --tenant "Sàn mẫu" set-role sale manager --by "Anh" --why "sale cấp cao"
  uv run python -m barista.ai_policy --tenant "Sàn mẫu" set-model gd@cty.vn anthropic:claude-sonnet-5 --by "Anh" --why "GĐ xin"   # "-" = theo hạng
  uv run python -m barista.ai_policy --tenant "Sàn mẫu" docread-show
  uv run python -m barista.ai_policy --tenant "Sàn mẫu" docread-set invoice --model google:gemini-3.8-flash --images-ocr yes --max-pages 5 --monthly-tokens 2000000 --by "Anh" --why "HĐ"
  uv run python -m barista.ai_policy --tenant "Sàn mẫu" docread-clear invoice --by "Anh" --why "về mặc định"
  uv run python -m barista.ai_policy --tenant "Sàn mẫu" embed-show
  uv run python -m barista.ai_policy --tenant "Sàn mẫu" embed-set --model openai-compatible:embeddinggemma --base-url http://127.0.0.1:11434/v1 --dry-run
  uv run python -m barista.ai_policy --tenant "Sàn mẫu" embed-set --model openai-compatible:embeddinggemma --base-url http://127.0.0.1:11434/v1 --by "Anh" --why "khách thấy Gemini đắt"
  uv run python -m barista.ai_policy --tenant "Sàn mẫu" embed-set --rollback --by "Anh" --why "quay lại Gemini"
  uv run python -m barista.ai_policy --tenant "Sàn mẫu" embed-clear --by "Anh" --why "về mặc định"
  uv run python -m barista.ai_policy --tenant "Sàn mẫu" report --month 2026-11 --xlsx token-2026-11.xlsx
  uv run python -m barista.ai_policy --tenant "Sàn mẫu" log
"""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import barista.modules  # noqa: F401 — nạp mọi module để registry đủ (như seed)
from barista.config import settings
from barista.db import db
from barista.kernel import executor, registry, ui
from barista.kernel.ai_tiers import tokens_text
from barista.kernel.principal import system_principal


async def tenant_of(name: str) -> tuple[UUID, str]:
    async with db.owner_tx() as c:
        cur = await c.execute("SELECT id, name FROM core.tenants WHERE id::text = %s OR name ILIKE %s ORDER BY created_at", (name, f"%{name}%"))
        rows = await cur.fetchall()
    if len(rows) != 1:
        raise SystemExit(f"Cần đúng một công ty khớp “{name}”, đang có {len(rows)}: " + "; ".join(r["name"] for r in rows))
    return rows[0]["id"], rows[0]["name"]


async def run(tid: UUID, command: str, inp: dict[str, Any]) -> dict[str, Any]:
    r = await executor.run(system_principal(tid), command, inp)
    if r.status != "ok":
        raise SystemExit(f"{command}: {r.status} — {r.error}")
    return r.output or {}


async def query(tid: UUID, name: str, inp: dict[str, Any] | None = None) -> Any:
    r = await executor.query(system_principal(tid), name, inp or {})
    if r.status != "ok":
        raise SystemExit(f"{name}: {r.status} — {r.error}")
    return r.output


def _tables(card: dict[str, Any]) -> list[dict[str, Any]]:
    return [b["page"] for b in card.get("blocks", []) if b.get("type") == "table"]


async def show(tid: UUID, name: str) -> None:
    tiers = await query(tid, "core.ai_tiers")
    roles = await query(tid, "core.ai_role_tiers")
    async with db.owner_tx() as c:
        cur = await c.execute("SELECT ai_enabled, ai_monthly_usd FROM core.tenant_profile WHERE tenant_id = %s", (tid,))
        prof = await cur.fetchone()
    cap = prof["ai_monthly_usd"] if prof else None
    print(f"{name} — trợ lý AI {'bật' if prof and prof['ai_enabled'] else 'TẮT'}; trần cả công ty: "
          + (f"{cap:.2f} USD / tháng" if cap is not None else "không đặt") + " (barista.entitle)")
    for t in tiers["rows"]:
        print(f"  [{t['key']}] {t['label']}{' (mặc định)' if t['is_default'] else ''}: {t['model_text']}; {t['cap_text']} / người / tháng; "
              f"giọng nói {'có' if t['voice'] else 'không'}; đặt riêng: {t['choices_text']}; {t['people']} người, "
              f"{tokens_text(t['used'])} tháng này, {t['out']} người hết hạn mức")
    print("  Vai → hạng: " + "; ".join(f"{r['label']} → {r['tier_label']}" for r in roles["rows"]))


async def docread_show(tid: UUID, name: str) -> None:
    out = await query(tid, "core.docread_profiles")
    print(f"{name} — hồ sơ đọc tài liệu (ADR 0232); token đọc tài liệu tháng này: {tokens_text(out['tokens_month'])}")
    for r in out["rows"]:
        usd = f", ≈ {r['usd']:.4f} USD" if r["usd"] is not None and r["runs"] else ""
        print(f"  [{r['purpose']}] {r['label']}: {r['describe']} (nguồn: {r['source']}); tháng này {r['runs']} lượt, {tokens_text(r['tokens'])}{usd}")
    if out["company_all"]:
        print(f"  [*] hồ sơ chung của công ty: {json.dumps(out['company_all'], ensure_ascii=False)}")


async def docread_set(tid: UUID, a: argparse.Namespace) -> None:
    inp: dict[str, Any] = {"purpose": a.purpose, "by": a.by, "why": a.why}
    if a.chain is not None:
        inp["chain"] = [x.strip() for x in a.chain.split(",") if x.strip()]
    if a.model is not None:
        inp["model"] = "" if a.model == "-" else a.model
    for key, val in (("scanned_pdf", a.scanned), ("images_ocr", a.images_ocr), ("images_caption", a.images_caption)):
        if (v := _bool(val)) is not None:
            inp[key] = v
    if a.tables is not None:
        inp["tables"] = a.tables
    if a.max_pages is not None:
        inp["max_model_pages"] = a.max_pages
    if a.monthly_tokens is not None:
        inp["monthly_tokens"] = int(a.monthly_tokens.replace(".", "").replace("_", ""))
    if a.clear:
        inp["clear"] = [x.strip() for x in a.clear.split(",") if x.strip()]
    print((await run(tid, "core.set_docread_profile", inp))["summary"])


def _embed_profile(d: dict[str, Any]) -> Any:
    from barista.agent import embed_profile as ep

    return ep.Profile(**{k: d[k] for k in ep.KEYS if k in d}, source="công ty")


def _embed_inp(a: argparse.Namespace) -> dict[str, Any]:
    inp: dict[str, Any] = {"by": a.by, "why": a.why, "off": a.off, "rollback": a.rollback}
    for key, val in (("model", a.model), ("dims", a.dims), ("base_url", a.base_url), ("key_ref", a.key_ref), ("query_prefix", a.query_prefix),
                     ("document_prefix", a.document_prefix), ("usd_per_mtok", a.usd_per_mtok), ("min_sim", a.min_sim)):
        if val is not None:
            inp[key] = val
    if (v := _bool(a.truncate)) is not None:
        inp["truncate"] = v
    if a.clear:
        inp["clear"] = [x.strip() for x in a.clear.split(",") if x.strip()]
    return inp


async def embed_show(tid: UUID, name: str) -> None:
    from barista.modules.kb import vectors

    out = await query(tid, "core.embed_profile")
    print(f"{name} — hồ sơ nhúng (ADR 0232): {out['describe']}")
    if out["company_row"]:
        print(f"  đặt riêng bởi {out['updated_by'] or '?'} lúc {str(out['updated_at'])[:16]}; hồ sơ trước: "
              + (_embed_profile(out["previous"]).describe() if out["previous"] else "không có"))
    for r in await vectors.status(tid):
        print(f"  tiến độ: {r['embedded']}/{r['chunks']} đoạn đã nhúng bằng model đang dùng ({r['percent']}%), chờ {r['pending']}, lỗi {r['failed']}"
              + (f"; còn vector model khác: {r['other_models']}" if r["other_models"] else "") + f" [{r['backend']}]")
    for u in out["usage"]:
        usd = "chưa có giá" if u["usd"] is None else f"{u['usd']:.4f} USD"
        print(f"  tháng này · {u['model']}: {u['runs']} lượt, {tokens_text(u['tokens'])}, {usd}")


async def embed_set(tid: UUID, name: str, a: argparse.Namespace) -> None:
    """Đặt / đổi hồ sơ nhúng: tính hồ sơ mới (lệnh chạy thử) → kiểm máy chủ thật + số chiều + chỉ mục → ước chi phí / thời gian → (không ``--dry-run``) ghi."""
    from barista.agent import embed
    from barista.agent import embed_profile as ep
    from barista.modules.kb import vectors

    inp = _embed_inp(a)
    if a.model and not a.off:   # gợi ý theo model quen thuộc (điền sẵn các khoá chưa nêu — hồ sơ lưu là dữ liệu tường minh)
        pre = ep.preset(a.model)
        for k in ("truncate", "query_prefix", "document_prefix"):
            if k in pre and k not in inp and a.model.startswith(ep.COMPAT + ":") and not (k == "truncate" and not pre[k]):
                inp[k] = pre[k]
        if a.dims is None and (n := pre.get("native")) and n < ep.STANDARD_DIMS:
            inp["dims"] = n
    plan_out = await run(tid, "core.set_embed_profile", {**inp, "dry_run": True})
    new = _embed_profile(plan_out["profile"])
    print(plan_out["summary"])
    refuse = ""
    if not new.off:
        if a.skip_probe:
            pr = embed.Probe(True)
        else:
            pr = await embed.probe(new)
            print(f"  kiểm máy chủ: {'ĐƯỢC' if pr.ok else 'KHÔNG ĐƯỢC'} — model trả {pr.native_dims} chiều, {pr.seconds:.2f} giây / lượt thử {pr.message}")
        if not pr.ok:
            refuse = f"kiểm máy chủ hỏng: {pr.message}"
        out = await vectors.plan(tid, new, seconds_per_batch=pr.seconds or None if not a.skip_probe else None)
        cost = "chưa có giá trong bảng giá" if out["cost_usd_est"] is None else f"{out['cost_usd_est']:.4f} USD (≈ {out['cost_usd_est'] * 25_000:,.0f} đ)".replace(",", ".")
        print(f"  hiện tại: {out['current']}")
        print(f"  cần nhúng lại: {out['to_embed']}/{out['chunks']} đoạn (đã có đúng model mới: {out['already_new']}); ~{tokens_text(out['tokens_est'])} token ước, "
              f"chi phí ước {cost}, ~{out['seconds_est']} giây ({out['batches']} lô × ~{out['seconds_per_batch_assumed']:.1f} giây ÷ {settings.embed_concurrency} việc song song)")
        if out["dims_problem"]:
            refuse = refuse or out["dims_problem"]
    if refuse:
        raise SystemExit(f"KHÔNG ĐỔI: {refuse}")
    if a.dry_run:
        print("(chạy thử — chưa ghi gì)")
        return
    if not (a.by.strip() and a.why.strip()):
        raise SystemExit("Cần --by (người vận hành) và --why (lý do) — ghi vào nhật ký")
    done = await run(tid, "core.set_embed_profile", inp)
    print(done["summary"])
    if done["reembed"]:
        await run(tid, "kb.embed_requeue", {})
        print("  Máy chủ nhúng nền theo vòng quét (mỗi phút, tôn trọng embed_concurrency / công tắc AI / hạn mức). Chạy ngay từ máy này: "
              f"python -m barista.modules.kb.vectors backfill --tenant {tid}; theo dõi: embed-show")


def _bool(v: str | None) -> bool | None:
    return None if v is None else v.lower() in ("1", "yes", "on", "true", "co", "có")


async def set_tier(tid: UUID, a: argparse.Namespace) -> None:
    cur = next((t for t in (await query(tid, "core.ai_tiers"))["rows"] if t["key"] == a.key), None)
    base = {"key": a.key, "label": "", "model": "", "choices": [], "person_monthly_tokens": None, "voice": False, "is_default": False, "sort": 100}
    if cur:   # tham số không nêu ⇒ giữ giá trị đang có của hạng
        base |= {"label": cur["label"], "model": cur["model_in"], "choices": cur["choices_in"], "person_monthly_tokens": cur["cap_in"],
                 "voice": cur["voice"], "is_default": cur["is_default"], "sort": cur["sort"]}
    if a.label is not None:
        base["label"] = a.label
    if a.model is not None:
        base["model"] = "" if a.model == "-" else a.model
    if a.choices is not None:
        base["choices"] = [x.strip() for x in a.choices.split(",") if x.strip()]
    if a.tokens is not None:
        base["person_monthly_tokens"] = None if a.tokens.lower() in ("none", "-", "0") else int(a.tokens.replace(".", "").replace("_", ""))
    if (v := _bool(a.voice)) is not None:
        base["voice"] = v
    if a.default:
        base["is_default"] = True
    if a.sort is not None:
        base["sort"] = a.sort
    print((await run(tid, "core.save_ai_tier", {**base, "by": a.by, "why": a.why}))["summary"])


async def report(tid: UUID, month: str, xlsx: str) -> None:
    card = await query(tid, "core.ai_token_report", {"month": month})
    pages = _tables(card)
    print(card["title"])
    for row in pages[0]["rows"]:
        print(f"  {row['name']:<28} {row['tier']:<10} {row['runs']:>5} lượt  {row['quota']:>12,} token  "
              f"{'HẾT HẠN MỨC' if row['stopped_at'] else ''}".replace(",", "."))
    if xlsx:
        await asyncio.to_thread(Path(xlsx).write_bytes, ui.tables_xlsx(pages))
        print(f"Đã ghi {xlsx} ({len(pages)} trang tính)")


async def log(tid: UUID, limit: int) -> None:
    ops = [k for k in registry.OPS if registry.operator_command(k)]
    async with db.owner_tx() as c:
        cur = await c.execute("""SELECT at, command, status, input, error FROM core.audit_log WHERE tenant_id = %s AND command = ANY(%s)
                                 ORDER BY at DESC LIMIT %s""", (tid, ops, limit))
        for r in await cur.fetchall():
            inp = r["input"] or {}
            print(f"  {r['at']:%Y-%m-%d %H:%M} {r['command']} {r['status']} — {inp.get('by', '?')}: {inp.get('why', '')} "
                  f"{json.dumps({k: v for k, v in inp.items() if k not in ('by', 'why')}, ensure_ascii=False)} {r['error'] or ''}")


async def main(a: argparse.Namespace) -> None:
    await db.open(settings.database_url, settings.owner_database_url)
    try:
        tid, name = await tenant_of(a.tenant)
        writes = a.cmd in ("set-tier", "set-role", "set-model", "docread-set", "docread-clear", "embed-clear") or (a.cmd == "embed-set" and not a.dry_run)
        if writes and not (a.by.strip() and a.why.strip()):
            raise SystemExit("Cần --by (người vận hành) và --why (lý do) — ghi vào nhật ký")
        if a.cmd == "show":
            await show(tid, name)
        elif a.cmd == "set-tier":
            await set_tier(tid, a)
        elif a.cmd == "set-role":
            print((await run(tid, "core.set_role_ai_tier", {"role": a.role, "tier": a.tier, "by": a.by, "why": a.why}))["summary"])
        elif a.cmd == "set-model":
            out = await run(tid, "core.set_person_ai_model", {"email": a.email, "model": "" if a.model == "-" else a.model, "by": a.by, "why": a.why})
            print(out["summary"])
        elif a.cmd == "docread-show":
            await docread_show(tid, name)
        elif a.cmd == "docread-set":
            await docread_set(tid, a)
        elif a.cmd == "docread-clear":
            print((await run(tid, "core.clear_docread_profile", {"purpose": a.purpose, "by": a.by, "why": a.why}))["summary"])
        elif a.cmd == "embed-show":
            await embed_show(tid, name)
        elif a.cmd == "embed-set":
            await embed_set(tid, name, a)
        elif a.cmd == "embed-clear":
            print((await run(tid, "core.clear_embed_profile", {"by": a.by, "why": a.why}))["summary"])
            await run(tid, "kb.embed_requeue", {})
        elif a.cmd == "report":
            await report(tid, a.month, a.xlsx)
        elif a.cmd == "log":
            await log(tid, a.limit)
    finally:
        await db.close()


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m barista.ai_policy", description="Chính sách trợ lý AI của một công ty — chỉ nhà vận hành (ADR 0193)")
    ap.add_argument("--tenant", required=True, help="tên (một phần) hoặc id công ty")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="hạng, model, hạn mức, vai → hạng, token tháng này")
    t = sub.add_parser("set-tier", help="thêm / sửa một hạng (tham số không nêu ⇒ giữ giá trị đang có)")
    t.add_argument("key")
    t.add_argument("--label")
    t.add_argument("--model", help="nhà_cung_cấp:tên_model; - = mặc định của bản cài")
    t.add_argument("--choices", help="model đặt riêng được, ngăn bằng dấu phẩy")
    t.add_argument("--tokens", help="hạn mức token (mọi token trừ cache đọc) / người / tháng; none = không giới hạn")
    t.add_argument("--voice", help="yes / no")
    t.add_argument("--default", action="store_true", help="đặt làm hạng mặc định")
    t.add_argument("--sort", type=int)
    r = sub.add_parser("set-role", help="gán vai → hạng")
    r.add_argument("role")
    r.add_argument("tier")
    mo = sub.add_parser("set-model", help="đặt model riêng cho một người (trong danh sách của hạng); - = theo hạng")
    mo.add_argument("email")
    mo.add_argument("model")
    sub.add_parser("docread-show", help="hồ sơ đọc tài liệu đang áp theo mục đích + token tháng này")
    ds = sub.add_parser("docread-set", help="đặt hồ sơ đọc tài liệu của một mục đích (kb, kb_images, brochure_images, invoice…, * = chung); khoá không nêu giữ nguyên")
    ds.add_argument("purpose")
    ds.add_argument("--chain", help="bộ đọc theo thứ tự, vd office,pypdf,model (bỏ model = không tốn model)")
    ds.add_argument("--model", help="nhà_cung_cấp:tên_model; - = mặc định của bản cài")
    ds.add_argument("--scanned", help="yes / no — AI đọc trang scan")
    ds.add_argument("--tables", help="off / text / auto / model")
    ds.add_argument("--images-ocr", help="yes / no — AI chép chữ trong ảnh")
    ds.add_argument("--images-caption", help="yes / no — AI mô tả ảnh")
    ds.add_argument("--max-pages", type=int, help="trần trang AI đọc / tài liệu")
    ds.add_argument("--monthly-tokens", help="hạn mức token đọc tài liệu cả công ty / tháng")
    ds.add_argument("--clear", help="các khoá bỏ về mặc định, ngăn bằng dấu phẩy")
    dc = sub.add_parser("docread-clear", help="bỏ hồ sơ riêng của một mục đích (về mặc định bản cài)")
    dc.add_argument("purpose")
    sub.add_parser("embed-show", help="hồ sơ nhúng (tìm theo nghĩa) đang áp + tiến độ nhúng + lượt / token / chi phí tháng này theo model")
    es = sub.add_parser("embed-set", help="đổi model nhúng của công ty: kiểm máy chủ + ước chi phí / thời gian; --dry-run chỉ tính. Khoá không nêu giữ nguyên")
    es.add_argument("--model", help="nhà_cung_cấp:tên — google:gemini-embedding-001 · openai:text-embedding-3-small · openai-compatible:embeddinggemma (kèm --base-url)")
    es.add_argument("--off", action="store_true", help="tắt tìm theo nghĩa cho công ty (chỉ từ khoá)")
    es.add_argument("--dims", type=int, help="số chiều lưu (chuẩn 768; số khác cần: python -m barista.modules.kb.vectors add-index N)")
    es.add_argument("--base-url", help="máy chủ tương thích OpenAI (Ollama / vLLM / TEI), vd http://127.0.0.1:11434/v1")
    es.add_argument("--key-ref", help="TÊN mục trong llm_keys của bản cài (không dán khoá)")
    es.add_argument("--truncate", help="yes / no — cắt phía mình khi model Matryoshka trả nhiều chiều hơn (CHỈ model huấn luyện kiểu Matryoshka)")
    es.add_argument("--query-prefix", help="tiền tố câu hỏi model đòi")
    es.add_argument("--document-prefix", help="tiền tố đoạn tài liệu model đòi")
    es.add_argument("--usd-per-mtok", type=float, help="giá USD / triệu token (máy tự dựng mặc định 0)")
    es.add_argument("--min-sim", type=float, help="ngưỡng giống cosine của nhánh nghĩa (đo lại bằng bench --retrieval khi đổi model)")
    es.add_argument("--clear", help="các khoá bỏ về mặc định, ngăn bằng dấu phẩy")
    es.add_argument("--rollback", action="store_true", help="quay lại hồ sơ ngay trước lần đổi model gần nhất")
    es.add_argument("--dry-run", action="store_true", help="chỉ kiểm + ước, không ghi")
    es.add_argument("--skip-probe", action="store_true", help="không gọi thử máy chủ (không khuyến khích)")
    ec = sub.add_parser("embed-clear", help="bỏ hồ sơ nhúng riêng (về mặc định bản cài)")
    rp = sub.add_parser("report", help="báo cáo token theo người × tháng (căn cứ tính tiền)")
    rp.add_argument("--month", default="", help="YYYY-MM; trống = tháng này")
    rp.add_argument("--xlsx", default="", help="ghi ra file .xlsx (một trang tính mỗi bảng)")
    lg = sub.add_parser("log", help="nhật ký các lần đổi chính sách (người vận hành + lý do)")
    lg.add_argument("--limit", type=int, default=30)
    for p in (t, r, mo, ds, dc, es, ec):
        p.add_argument("--by", default="", help="người vận hành")
        p.add_argument("--why", default="", help="lý do")
    return ap


if __name__ == "__main__":
    asyncio.run(main(parser().parse_args()))
