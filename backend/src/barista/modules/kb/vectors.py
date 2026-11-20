"""Tìm theo NGHĨA cho kho tài liệu (quyết định 0141): vector của từng đoạn + gộp với tìm từ khoá bằng RRF (k=60) ở ``kb.search``.

Hai cách cất vector, chọn lúc chạy theo KIỂU CỘT ``kb.chunks.embedding`` (migration ``base/052`` chọn theo việc DB có pgvector hay không):

* ``pgvector`` — cột ``vector`` + chỉ mục HNSW cosine (biểu thức ``embedding::vector(768)``, một phần ``embed_dims = 768``). Truy vấn bật
  ``hnsw.iterative_scan`` ⇒ chỉ mục quét tiếp tới khi đủ đoạn QUA bộ lọc quyền (không "lấy top-k rồi lọc").
* ``array`` — cột ``real[]`` (DB không có pgvector, vd ảnh ``postgres:18-alpine``): cosine tính bằng SQL thuần (vector đã chuẩn hoá ⇒ tích
  vô hướng) trên MỌI đoạn người hỏi đọc được — đúng tuyệt đối, chi phí tuyến tính theo số đoạn (giới hạn ở ADR 0141 §4).

Cả hai: cùng câu SQL với bộ lọc quyền (RLS kệ / ACL), hiệu lực, kệ, phạm vi — vector KHÔNG bao giờ lọc sau.

Không gọi mạng khi đang giữ transaction: câu tìm nhúng ở bước ``prepare`` (ngoài transaction); đoạn nhúng trong task NỀN riêng (trần
``embed_concurrency``), giữ đoạn bằng ``embed_claimed_at`` để việc theo tài liệu và vòng quét không nhúng trùng. Ghi đoạn xong ⇒ hẹn sau commit
``spawn``; vòng quét ``kb.embed_sweep`` chỉ KHỞI ĐỘNG việc nền (không chờ) cho công ty còn đoạn thiếu (tiến trình chết, lỗi mạng, công ty bật lại
AI, đổi model). Lệnh tay: ``python -m barista.modules.kb.vectors status|backfill|upgrade|add-index``.

MODEL THEO CÔNG TY (ADR 0232, ``agent/embed_profile``): mỗi công ty có hồ sơ nhúng riêng (mặc định bản cài). Câu tìm CHỈ so vector của model + số chiều ĐANG DÙNG của
công ty (không bao giờ trộn); đổi model ⇒ vòng quét nhúng lại dần, trong lúc đó đoạn chưa nhúng bằng model mới chỉ tìm được bằng từ khoá. Mỗi đoạn giữ MỘT vector —
vector cũ của một đoạn chỉ mất khi vector mới của chính đoạn đó đã ghi (quay lại model cũ chỉ phải nhúng lại phần đã đổi). Số chiều khác 768 cần chỉ mục HNSW riêng
(``add-index``); trên pgvector, số chiều chưa có chỉ mục bị từ chối thay vì quét tuần tự âm thầm.
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, LiteralString, cast
from uuid import UUID

from pydantic import Field

from barista.agent import embed, embed_profile
from barista.agent.embed_profile import Profile
from barista.api import Ctx, Input, Invalid, NoInput, field
from barista.config import settings
from barista.db import db
from barista.kernel import executor
from barista.kernel.principal import system_principal
from barista.modules.kb import m

log = logging.getLogger("barista.kb.vectors")

RRF_K = 60
INDEX_DIMS = 768          # chỉ mục HNSW dựng cho số chiều này (migration base/052)
RETRY_AFTER = "1 hour"    # đoạn nhúng hỏng: vòng quét thử lại sau chừng này (không đốt lượt gọi mỗi phút)


@dataclass(frozen=True)
class Backend:
    kind: Literal["pgvector", "array", "none"]
    schema: str = ""      # schema của extension vector (thường public)


async def backend(conn: Any) -> Backend:
    """Đọc KIỂU CỘT — một nguồn duy nhất: migration đặt theo extension có sẵn; ``upgrade`` đổi kiểu khi bản cài thêm pgvector sau."""
    cur = await conn.execute(
        """SELECT t.typname, n.nspname FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid JOIN pg_namespace n ON n.oid = t.typnamespace
           WHERE a.attrelid = 'kb.chunks'::regclass AND a.attname = 'embedding' AND NOT a.attisdropped""")
    r = await cur.fetchone()
    if r is None:
        return Backend("none")
    row = r if isinstance(r, dict) else {"typname": r[0], "nspname": r[1]}
    return Backend("pgvector", row["nspname"]) if row["typname"] == "vector" else Backend("array")


def literal(b: Backend, v: list[float], *, exact_array: bool = False) -> Any:
    """Tham số vector theo kiểu cột: pgvector nhận chữ ``[..]``, mảng nhận list."""
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]" if b.kind == "pgvector" and not exact_array else v


def similarity(b: Backend, dims: int, *, exact_array: bool = False) -> tuple[str, str]:
    """(biểu thức độ giống 0..1, biểu thức ORDER BY tăng dần) cho đoạn ``c`` với tham số ``%(qv)s``. ``exact_array``: tính kiểu mảng cả khi cột là
    vector (test so hai nhánh trên cùng dữ liệu)."""
    if b.kind == "pgvector" and not exact_array:
        s = b.schema
        typ = "vector" if dims <= embed_profile.VECTOR_INDEX_MAX else "halfvec"   # chỉ mục vector ≤ 2.000 chiều, halfvec ≤ 4.000 (pgvector)
        e = f"(c.embedding::{s}.{typ}({dims}))"
        dist = f"({e} OPERATOR({s}.<=>) %(qv)s::{s}.{typ}({dims}))"
        return f"(1 - {dist})", dist
    col = "c.embedding::real[]" if b.kind == "pgvector" else "c.embedding"
    sim = f"(SELECT sum(x * y) FROM unnest({col}, %(qv)s::real[]) AS v(x, y))"
    return sim, f"(-{sim})"


EXACT_ARRAY = False   # test: tính kiểu mảng cả trên cột vector — chứng minh hai nhánh lọc quyền như nhau trên cùng dữ liệu


@dataclass(frozen=True)
class Prepared:
    vector: list[float] | None
    reason: str = ""
    profile: Profile | None = None   # hồ sơ nhúng ĐÃ dùng cho câu này — nhánh vector lọc đúng model + số chiều của nó (một ảnh chụp, không đổi giữa chừng)


_INDEXED: tuple[float, frozenset[int]] | None = None
_INDEX_TTL_S = 60.0
_DIMS_IN_INDEX = re.compile(r"embed_dims\s*=\s*(\d+)")


async def indexed_dims(conn: Any) -> frozenset[int]:
    """Số chiều đã có chỉ mục HNSW một phần (đọc danh mục, nhớ 60 giây). Cột ``vector`` không cố định số chiều: chỉ số chiều có chỉ mục mới tìm nhanh."""
    global _INDEXED
    if _INDEXED and time.monotonic() - _INDEXED[0] < _INDEX_TTL_S:
        return _INDEXED[1]
    cur = await conn.execute("""SELECT indexdef FROM pg_indexes WHERE schemaname = 'kb' AND tablename = 'chunks' AND indexdef ILIKE '%%hnsw%%'""")
    out = frozenset(int(m.group(1)) for r in await cur.fetchall() if (m := _DIMS_IN_INDEX.search(r["indexdef"] if isinstance(r, dict) else r[0])))
    _INDEXED = (time.monotonic(), out)
    return out


def dims_problem(b: Backend, dims: int, indexed: frozenset[int]) -> str:
    """"" = số chiều dùng được; không ⇒ câu nói cách sửa. Nhánh mảng (không pgvector) quét đúng tuyệt đối nên không cần chỉ mục; pgvector cần chỉ mục cho số chiều đó."""
    if dims > embed_profile.MAX_DIMS:
        return f"số chiều {dims} vượt trần {embed_profile.MAX_DIMS} của pgvector (halfvec)"
    if b.kind == "pgvector" and dims not in indexed:
        return f"chưa có chỉ mục cho {dims} chiều — chạy: python -m barista.modules.kb.vectors add-index {dims}"
    return ""


async def prepare_query(p: Any, q: str) -> Prepared:
    """Bước ``prepare`` của ``kb.search``: chạy NGOÀI transaction, trước khi mượn kết nối cho câu tìm — gọi model (≤ ``embed_query_timeout_s``) không
    giữ kết nối DB nào; lượt đo ghi sau cuộc gọi bằng kết nối riêng, lúc không có kết nối nào khác đang giữ. Kiểm DB trong một transaction đọc ngắn
    (đã đóng trước khi gọi mạng): có cột vector chưa, số chiều có chỉ mục chưa, kho có đoạn nào nhúng bằng model ĐANG DÙNG của công ty chưa (người hỏi thấy)."""
    prof = await embed_profile.for_tenant(p)
    if reason := embed.unconfigured(prof):
        return Prepared(None, reason, prof)
    if not p.ai or p.ai_block:
        return Prepared(None, p.ai_block or "Công ty này đã tắt trợ lý AI", prof)
    async with db.tenant_tx(p, read_only=True) as conn:
        b = await backend(conn)
        if b.kind == "none":
            return Prepared(None, "DB chưa có cột vector", prof)
        if problem := dims_problem(b, prof.dims, await indexed_dims(conn)):
            return Prepared(None, problem, prof)
        cur = await conn.execute("SELECT EXISTS (SELECT 1 FROM kb.chunks c WHERE c.embed_model = %s AND c.embed_dims = %s)", (prof.model, prof.dims))
        if not (await cur.fetchone() or {}).get("exists"):
            return Prepared(None, "kho chưa có đoạn nào được nhúng bằng model đang dùng của công ty", prof)
    try:
        return Prepared(await embed.query_vector(p, q, prof), "", prof)
    except embed.Unavailable as e:
        return Prepared(None, e.reason, prof)


async def semantic(ctx: Ctx, q: str, where: str, params: dict[str, Any], select: str, *, k: int) -> tuple[list[dict[str, Any]], str]:
    """Nhánh vector của ``kb.search``: (đoạn giống nhất theo thứ tự, "") hoặc ([], lý do chỉ tìm từ khoá). ``where`` / ``select`` là ĐÚNG phần
    lọc + cột của nhánh từ khoá ⇒ hai nhánh chịu cùng một bộ lọc quyền / hiệu lực / kệ / phạm vi trong SQL. Vector câu hỏi lấy từ bước
    ``prepare`` (ngoài transaction); gọi lồng (không có bước đó) chỉ dùng vector đã nhớ — KHÔNG gọi mạng khi đang giữ transaction."""
    prep = ctx.prepared if isinstance(ctx.prepared, Prepared) else None
    if prep is None:
        prof = embed_profile.cached(ctx.p.tenant_id)
        v = embed.cached_query_vector(ctx.p, q, prof) if prof is not None else None
        prep = Prepared(v, "" if v is not None else "gọi lồng trong transaction — tìm theo nghĩa chỉ chạy khi gọi thẳng kb.search", prof)
    if prep.vector is None or prep.profile is None:
        return [], prep.reason
    b = await backend(ctx.conn)
    if b.kind == "none":
        return [], "DB chưa có cột vector"
    name, dims = prep.profile.model, prep.profile.dims
    exact_array = EXACT_ARRAY
    sim, order = similarity(b, dims, exact_array=exact_array)
    if b.kind == "pgvector" and not exact_array:
        # quét chỉ mục tiếp tới khi đủ k đoạn QUA bộ lọc (pgvector ≥ 0.8); thứ tự xấp xỉ ⇒ sắp lại theo độ giống bên dưới
        await ctx.exec("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
        await ctx.exec("SET LOCAL hnsw.ef_search = 100")
    rows = await ctx.all(cast(LiteralString,
        f"""SELECT {select}, {sim} AS sim
            FROM kb.chunks c JOIN kb.documents d ON d.id = c.document_id
            LEFT JOIN kb.shelves s ON s.tenant_id = d.tenant_id AND s.key = d.shelf
            WHERE c.embed_model = %(model)s AND c.embed_dims = {int(dims)} AND c.embedding IS NOT NULL AND {where}
            ORDER BY {order} LIMIT %(k)s"""),
        {**params, "qv": literal(b, prep.vector, exact_array=exact_array), "model": name, "k": k})
    rows.sort(key=lambda r: -float(r["sim"]))
    return [r for r in rows if float(r["sim"]) >= prep.profile.threshold], ""


def rrf(*ranked: list[Any]) -> dict[Any, float]:
    """Reciprocal Rank Fusion: điểm = Σ 1 / (60 + hạng) qua các danh sách (hạng từ 1)."""
    out: dict[Any, float] = {}
    for lst in ranked:
        for i, key in enumerate(lst, 1):
            out[key] = out.get(key, 0.0) + 1.0 / (RRF_K + i)
    return out


# ── Nhúng đoạn: hợp đồng hẹp chạy quyền hệ thống (việc nền / vòng quét / lệnh tay) ──

CLAIM_FOR = "5 minutes"   # việc nhúng giữ đoạn tối đa chừng này (tiến trình chết giữa chừng ⇒ hết giữ, việc khác nhận)


def _needs(c: str, model: str, dims: int) -> str:
    """Đoạn THIẾU vector hoặc vector KHÁC model / số chiều ``model`` / ``dims``; viết thành khoảng để dùng chỉ mục một phần (migration base/052)."""
    lit = "'" + model.replace("'", "''") + "'"
    return (f"({c}embedding IS NULL OR {c}embed_model < {lit} OR {c}embed_model > {lit} "
            f"OR ({c}embed_model = {lit} AND {c}embed_dims <> {int(dims)}))")


def _due(c: str) -> str:
    """Không đang bị giữ, và nếu vừa hỏng (lỗi / công ty tắt AI) thì đã qua thời gian chờ."""
    return (f"({c}embed_claimed_at IS NULL OR {c}embed_claimed_at < now() - interval '{CLAIM_FOR}') "
            f"AND ({c}embed_error IS NULL OR {c}embed_tried_at < now() - interval '{RETRY_AFTER}')")


class Claim(Input):
    model: str = field("Model đang dùng", max_length=200)   # hồ sơ nhúng của công ty do bên gọi nạp (core.embed_profiles — lõi, không phải việc của module kb)
    dims: int = field("Số chiều", ge=1, le=embed_profile.MAX_DIMS)
    limit: int = field("Số đoạn", 100, ge=1, le=1000)
    document_id: UUID | None = field("Chỉ tài liệu", None)
    skip: list[int] = Field(default_factory=list)   # đoạn lượt này đã thử (hỏng) — không nhận lại trong cùng lượt


@m.command("claim_embeddings", resource="kb.common", title="Nhận đoạn tài liệu cần nhúng", internal=True, agent="deny")
async def claim_embeddings(ctx: Ctx, inp: Claim) -> dict[str, Any]:
    """GIỮ (``embed_claimed_at``) các đoạn cần nhúng rồi trả chữ của chúng — việc nền theo tài liệu và vòng quét không nhúng trùng
    (``FOR UPDATE SKIP LOCKED``). Hỏi đúng tài liệu (vừa ghi) ⇒ bỏ qua thời gian chờ sau lỗi, vẫn tôn trọng việc đang giữ."""
    due = (f"(c2.embed_claimed_at IS NULL OR c2.embed_claimed_at < now() - interval '{CLAIM_FOR}')" if inp.document_id
           else _due("c2."))
    rows = await ctx.all(cast(LiteralString,
        f"""UPDATE kb.chunks c SET embed_claimed_at = now()
            FROM kb.documents d
            WHERE d.id = c.document_id AND c.id IN (
                SELECT c2.id FROM kb.chunks c2
                WHERE {_needs("c2.", inp.model, inp.dims)} AND {due} AND (%(doc)s::uuid IS NULL OR c2.document_id = %(doc)s::uuid)
                  AND NOT (c2.id = ANY(%(skip)s::bigint[]))
                ORDER BY c2.id LIMIT %(n)s FOR UPDATE OF c2 SKIP LOCKED)
            RETURNING c.id, c.document_id, d.created_by, concat_ws(E'\\n', d.title, nullif(c.heading, ''), c.text) AS body"""),
        {"doc": inp.document_id, "n": inp.limit, "skip": inp.skip})
    return {"chunks": sorted(rows, key=lambda r: r["id"])}


class Store(Input):
    model: str = field("Model", max_length=200)
    dims: int = field("Số chiều", ge=1, le=4096)
    # JSON [[mã đoạn, [số…]], …] — chuỗi để nhật ký thao tác chỉ ghi cỡ + sha256 (audit=False), không chép hàng nghìn con số
    vectors: str = field("Vector", "[]", audit=False, max_length=20_000_000)
    failed: list[int] = Field(default_factory=list)
    error: str = field("Lỗi", "", max_length=500)


@m.command("store_embeddings", resource="kb.common", title="Lưu vector của đoạn tài liệu", internal=True, agent="deny")
async def store_embeddings(ctx: Ctx, inp: Store) -> dict[str, int]:
    """Ghi vector + model + số chiều cho từng đoạn (đoạn đã bị xoá / thay trong lúc nhúng ⇒ bỏ qua) và bỏ giữ. Đoạn hỏng: ghi lý do + giờ, bỏ
    giữ ⇒ vòng quét đợi ``RETRY_AFTER`` rồi mới thử lại."""
    b = await backend(ctx.conn)
    if b.kind == "none":
        return {"stored": 0, "failed": 0}
    items = [(int(i), [float(x) for x in v]) for i, v in json.loads(inp.vectors)]
    if any(len(v) != inp.dims for _, v in items):
        raise Invalid("vector sai số chiều")
    cast_ = f"{b.schema}.vector" if b.kind == "pgvector" else "real[]"
    stored = 0
    async with ctx.conn.cursor() as cur:
        for cid, v in items:
            await cur.execute(cast(LiteralString,
                f"""UPDATE kb.chunks SET embedding = %s::{cast_}, embed_model = %s, embed_dims = %s, embed_tried_at = now(), embed_error = NULL,
                                         embed_claimed_at = NULL
                    WHERE id = %s"""), (literal(b, v), inp.model, inp.dims, cid))
            stored += cur.rowcount
    failed = 0
    if inp.failed:
        failed = await ctx.exec("UPDATE kb.chunks SET embed_tried_at = now(), embed_error = %s, embed_claimed_at = NULL WHERE id = ANY(%s)",
                                (inp.error[:500] or "lỗi", inp.failed))
    return {"stored": stored, "failed": failed}


# Vòng quét: chỉ đoạn thiếu / khác model của công ty (hồ sơ riêng ở core.embed_profiles; không có ⇒ mặc định bản cài — chuỗi baked lúc nạp, đổi cấu hình ⇒ khởi động lại),
# chưa bị giữ, không trong thời gian chờ sau lỗi — công ty tắt AI cũng vào thời gian chờ (việc nền đánh dấu) ⇒ không bị chọn lại mỗi phút.
# Hai nhánh: (1) công ty KHÔNG có hồ sơ riêng — dùng chỉ mục một phần như trước; (2) công ty có hồ sơ riêng (ít dòng) — so với model của chính nó, hồ sơ "tắt" bỏ qua.
_DEFAULT_ON = bool(embed.spec()[0])
SWEEP = cast(LiteralString, f"""SELECT tenant_id FROM (
    SELECT DISTINCT c.tenant_id FROM kb.chunks c
    WHERE {'true' if _DEFAULT_ON else 'false'} AND {_needs('c.', *embed.spec())} AND {_due('c.')}
      AND NOT EXISTS (SELECT 1 FROM core.embed_profiles ep WHERE ep.tenant_id = c.tenant_id)
    UNION
    SELECT DISTINCT c.tenant_id FROM core.embed_profiles ep JOIN kb.chunks c ON c.tenant_id = ep.tenant_id
    WHERE coalesce(ep.model, '') <> '' AND (c.embedding IS NULL OR c.embed_model IS DISTINCT FROM ep.model OR c.embed_dims IS DISTINCT FROM ep.dims)
      AND {_due('c.')}) due""")


@m.command("embed_sweep", resource="kb.common", title="Nhúng các đoạn tài liệu còn thiếu vector", internal=True, agent="deny",
           sweep=SWEEP)
async def embed_sweep(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Vòng quét: công ty còn đoạn chưa nhúng ⇒ sau commit KHỞI ĐỘNG việc nền (không chờ) — vòng quét chung (hạn xử lý của mọi module) không bao giờ
    đợi nhà cung cấp model. Hồ sơ nhúng của công ty (tắt / chưa có khoá) do việc nền kiểm — không làm gì thì thoát ngay."""
    tid = ctx.p.tenant_id

    async def start() -> None:
        spawn(tid, None)

    ctx.after_commit(start)
    return {}


@m.command("embed_requeue", resource="kb.common", title="Cho các đoạn nhúng hỏng thử lại ngay", internal=True, agent="deny")
async def embed_requeue(ctx: Ctx, inp: NoInput) -> dict[str, int]:
    """Sau khi nhà vận hành ĐỔI hồ sơ nhúng: bỏ thời gian chờ + giữ cũ của các đoạn (lỗi của model cũ không được chặn model mới)."""
    n = await ctx.exec("""UPDATE kb.chunks SET embed_error = NULL, embed_tried_at = NULL, embed_claimed_at = NULL
                          WHERE embed_error IS NOT NULL OR embed_claimed_at IS NOT NULL""")
    return {"requeued": n}


async def _run_quiet(tenant_id: UUID, document_id: UUID | None) -> None:
    async with _slots():   # trần việc nhúng chạy cùng lúc (mọi công ty) — lỗi nhà cung cấp không nhân lên thành trăm kết nối treo
        try:
            await embed_pending(tenant_id, document_id)
        except Exception:   # nhúng nền lỗi ⇒ log, tài liệu vẫn dùng được
            log.exception("nhúng đoạn tài liệu của công ty %s lỗi", tenant_id)


_TASKS: set[asyncio.Task[None]] = set()
_TENANT_RUNS: dict[UUID, asyncio.Task[None]] = {}
_SEM: dict[int, asyncio.Semaphore] = {}


def _slots() -> asyncio.Semaphore:
    loop = id(asyncio.get_running_loop())
    if loop not in _SEM:
        _SEM.clear()
        _SEM[loop] = asyncio.Semaphore(max(1, settings.embed_concurrency))
    return _SEM[loop]


def spawn(tenant_id: UUID, document_id: UUID | None) -> None:
    """Nhúng NỀN (task riêng, không ai chờ): một tài liệu vừa ghi, hoặc cả công ty (vòng quét — mỗi công ty tối đa một việc đang chạy). Hồ sơ nhúng của công ty
    (tắt / thiếu khoá) do ``embed_pending`` kiểm: không nhúng được thì thoát ngay, vòng quét làm sau."""
    if document_id is None and (run := _TENANT_RUNS.get(tenant_id)) is not None and not run.done():
        return
    t = asyncio.get_running_loop().create_task(_run_quiet(tenant_id, document_id))
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    if document_id is None:
        _TENANT_RUNS[tenant_id] = t
        t.add_done_callback(lambda _: _TENANT_RUNS.pop(tenant_id, None) if _TENANT_RUNS.get(tenant_id) is t else None)


async def drain() -> None:
    """Chờ mọi việc nhúng nền đang chạy (test, tắt máy chủ êm)."""
    while _TASKS:
        await asyncio.gather(*list(_TASKS), return_exceptions=True)


async def _meter_principal(tenant_id: UUID, user_id: UUID | None) -> Any:
    """Người ghi lượt đo + công tắc AI: người thêm tài liệu; tài liệu không người thêm (đồng bộ nguồn ngoài, link gửi) ⇒ chủ tài khoản. Nạp
    lại từ DB mỗi lần (công ty vừa tắt AI / hết hạn mức ⇒ dừng ngay)."""
    from barista.kernel import auth

    async with db.owner.connection() as conn:
        if user_id is not None and (p := await auth.load(conn, tenant_id, user_id=user_id)) is not None:
            return p
        cur = await conn.execute("""SELECT user_id FROM core.memberships WHERE tenant_id = %s AND active
                                    ORDER BY owner DESC, created_at, user_id LIMIT 1""", (tenant_id,))
        r = await cur.fetchone()
        return await auth.load(conn, tenant_id, user_id=r["user_id"]) if r else None


async def _store(sysp: Any, name: str, dims: int, *, done: list[tuple[int, list[float]]] = (), failed: list[int] = (),  # type: ignore[assignment]
                 error: str = "") -> int:
    w = await executor.run(sysp, "kb.store_embeddings", {"model": name, "dims": dims, "vectors": json.dumps(list(done)),
                                                         "failed": list(failed), "error": error}, quiet=True)
    if not w.ok:
        raise RuntimeError(w.error or "không ghi được vector")
    return int(w.output["stored"])


async def embed_pending(tenant_id: UUID, document_id: UUID | None = None, *, limit: int | None = None) -> dict[str, Any]:
    """Nhúng đoạn còn thiếu của một công ty (hoặc một tài liệu): GIỮ đoạn → gọi model NGOÀI transaction → ghi. Chạy lại được (chỉ làm phần thiếu).
    Lỗi tạm (mạng, quá hạn) ⇒ cả lô vào thời gian chờ, dừng việc của công ty này; lỗi riêng ⇒ thử TỪNG đoạn, chỉ đoạn hỏng vào thời gian chờ;
    công ty tắt AI / hết hạn mức ⇒ không gửi gì, các đoạn vào thời gian chờ (vòng quét không chọn lại mỗi phút)."""
    sysp = system_principal(tenant_id)
    done = failed = 0
    cost: Any = 0
    reason = ""
    tried: set[int] = set()   # lệnh theo tài liệu bỏ qua thời gian chờ ⇒ phải nhớ đoạn đã thử, kẻo nhận lại đoạn vừa hỏng mãi
    while limit is None or done + failed < limit:
        prof = await embed_profile.fresh(tenant_id)   # đọc lại mỗi lô: nhà vận hành đổi model giữa chừng ⇒ lô sau dùng model mới (không ghi vector model cũ)
        name, dims = prof.model, prof.dims
        if reason := embed.unconfigured(prof):
            break
        async with db.owner.connection() as oc:
            if reason := dims_problem(await backend(oc), dims, await indexed_dims(oc)):
                break
        r = await executor.run(sysp, "kb.claim_embeddings", {"model": name, "dims": dims, "limit": 100, "document_id": document_id,
                                                              "skip": sorted(tried)}, quiet=True)
        if not r.ok:
            reason = r.error or "không nhận được đoạn chờ nhúng"
            break
        rows = r.output["chunks"]
        if not rows:
            break
        tried.update(x["id"] for x in rows)
        by_owner: dict[Any, list[dict[str, Any]]] = {}
        for x in rows:
            by_owner.setdefault(x["created_by"], []).append(x)
        for owner, group in by_owner.items():
            ids = [x["id"] for x in group]
            p = await _meter_principal(tenant_id, owner)
            try:
                if p is None:
                    raise embed.Unavailable("công ty không còn thành viên nào", ai_off=True)
                out = await embed.embed(p, [x["body"] for x in group], kind="document", profile=prof)
            except embed.Unavailable as e:
                if e.transient or e.ai_off or len(group) == 1:
                    failed += len(ids)
                    await _store(sysp, name, dims, failed=ids, error=e.reason)
                    if e.transient or e.ai_off:   # nhà cung cấp đang lỗi / công ty tắt AI ⇒ dừng, các lô sau cũng sẽ vậy
                        reason = e.reason
                        break
                    continue
                assert p is not None
                for x in group:   # lỗi riêng của lô (một đoạn lạ làm hỏng cả lô) ⇒ thử từng đoạn
                    try:
                        one = await embed.embed(p, [x["body"]], kind="document", profile=prof)
                        done += await _store(sysp, one.model, one.dims, done=[(x["id"], one.vectors[0])])
                        cost = (cost or 0) + (one.cost_usd or 0)
                    except embed.Unavailable as e1:
                        failed += 1
                        await _store(sysp, name, dims, failed=[x["id"]], error=e1.reason)
                continue
            done += await _store(sysp, out.model, out.dims, done=list(zip(ids, out.vectors, strict=True)))
            cost = (cost or 0) + (out.cost_usd or 0)
        if reason:   # nhà cung cấp lỗi / công ty tắt AI: dừng việc của công ty này
            break
    return {"embedded": done, "failed": failed, "cost_usd": float(cost), "stopped": reason}


async def status(tenant_id: UUID | None = None) -> list[dict[str, Any]]:
    """Mỗi công ty (có đoạn): hồ sơ nhúng đang áp, số đoạn, đã nhúng bằng model ĐANG DÙNG, còn chờ, lỗi, % tiến độ, và đoạn đang mang vector của model KHÁC
    (``other_models`` — chuyển model chưa xong / giữ cho quay lại) — đọc bằng quyền chủ DB (lệnh vận hành)."""
    async with db.owner.connection() as conn:
        b = await backend(conn)
        cur = await conn.execute("""SELECT tenant_id, embed_model, embed_dims, count(*) AS n,
                                           count(*) FILTER (WHERE embed_error IS NOT NULL AND embedding IS NULL) AS failed
                                    FROM kb.chunks WHERE %s::uuid IS NULL OR tenant_id = %s::uuid
                                    GROUP BY tenant_id, embed_model, embed_dims ORDER BY tenant_id""", (tenant_id, tenant_id))
        rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for tid in dict.fromkeys(r["tenant_id"] for r in rows):
        prof = await embed_profile.fresh(tid)
        mine = [r for r in rows if r["tenant_id"] == tid]
        chunks = sum(r["n"] for r in mine)
        embedded = sum(r["n"] for r in mine if r["embed_model"] == prof.model and r["embed_dims"] == prof.dims)
        other = {f"{r['embed_model']}/{r['embed_dims']}": r["n"] for r in mine
                 if r["embed_model"] is not None and not (r["embed_model"] == prof.model and r["embed_dims"] == prof.dims)}
        out.append({"tenant_id": tid, "chunks": chunks, "embedded": embedded, "pending": 0 if prof.off else chunks - embedded,
                    "failed": sum(r["failed"] for r in mine), "percent": 100.0 if prof.off or not chunks else round(embedded * 100 / chunks, 1),
                    "other_models": other, "backend": b.kind, "model": prof.model, "dims": prof.dims, "profile": prof.describe(), "source": prof.source})
    return out


async def plan(tenant_id: UUID, new: Profile, *, seconds_per_batch: float | None = None) -> dict[str, Any]:
    """CHẠY THỬ đổi sang hồ sơ ``new``: bao nhiêu đoạn phải nhúng lại, ước token / chi phí / thời gian (không gọi model, không ghi gì). Token ước ~3 ký tự / token như
    lúc đo thật; giá theo bảng giá công khai (máy tự dựng: 0 hoặc giá nhà vận hành khai); thời gian = số lô × giây / lô (đo bằng ``embed.probe`` nếu có) ÷ số việc song song.
    Chỉ đoạn CHƯA có vector đúng model + số chiều mới đó mới tính (đã có ⇒ giữ)."""
    cur_prof = await embed_profile.fresh(tenant_id)
    async with db.owner.connection() as conn:
        cur = await conn.execute(
            """SELECT count(*) AS chunks,
                      count(*) FILTER (WHERE c.embed_model = %(m)s AND c.embed_dims = %(d)s) AS have,
                      coalesce(sum(length(concat_ws(E'\n', d.title, nullif(c.heading, ''), c.text)))
                               FILTER (WHERE c.embed_model IS DISTINCT FROM %(m)s OR c.embed_dims IS DISTINCT FROM %(d)s), 0) AS chars,
                      count(*) FILTER (WHERE c.embed_model IS NOT NULL AND c.embed_model = %(cm)s AND c.embed_dims = %(cd)s) AS current
               FROM kb.chunks c JOIN kb.documents d ON d.id = c.document_id WHERE c.tenant_id = %(t)s""",
            {"m": new.model, "d": new.dims, "t": tenant_id, "cm": cur_prof.model, "cd": cur_prof.dims})
        r = await cur.fetchone() or {}
        b = await backend(conn)
        indexed = await indexed_dims(conn)
    chunks, have, chars = int(r.get("chunks", 0)), int(r.get("have", 0)), int(r.get("chars", 0))
    todo = chunks - have
    tokens = -(-chars // 3)
    cost = embed.price(new, new.model.split(":", 1)[-1], new.provider, tokens) if todo else Decimal(0)
    batches = -(-todo // max(1, settings.embed_batch))
    per = seconds_per_batch if seconds_per_batch is not None else (1.0 if new.self_hosted else 0.8)
    secs = batches * per / max(1, settings.embed_concurrency)
    return {"chunks": chunks, "already_new": have, "to_embed": todo, "tokens_est": tokens, "cost_usd_est": None if cost is None else float(cost),
            "batches": batches, "seconds_est": round(secs, 1), "seconds_per_batch_assumed": per, "current": cur_prof.describe(),
            "current_embedded": int(r.get("current", 0)), "dims_problem": dims_problem(b, new.dims, indexed) if not new.off else "", "backend": b.kind}


UPGRADE_SQL = """Chạy SAU khi quản trị DB (superuser) đã cài pgvector vào DB này: CREATE EXTENSION vector SCHEMA public;"""


async def upgrade(owner_url: str) -> str:
    """Bản cài chuyển sang ảnh Postgres CÓ pgvector (ADR 0141 §5): đổi cột ``real[]`` → ``vector`` (giữ nguyên vector đã nhúng — pgvector có
    phép ép kiểu real[] → vector), dựng chỉ mục HNSW. Chạy lại không sao. Khoá bảng kb.chunks trong lúc viết lại (vài giây với vài nghìn đoạn)."""
    from psycopg import AsyncConnection

    async with await AsyncConnection.connect(owner_url, autocommit=True) as conn:
        cur = await conn.execute("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'vector'")
        ext = await cur.fetchone()
        if ext is None:
            return "DB chưa có extension vector. " + UPGRADE_SQL
        s = ext[0]
        b = await backend(conn)
        if b.kind == "none":
            return "Chưa chạy migration base/052 — chạy migrate trước"
        async with conn.transaction():
            if b.kind == "array":
                await conn.execute(cast(LiteralString,
                    f'ALTER TABLE kb.chunks ALTER COLUMN embedding TYPE "{s}".vector USING embedding::"{s}".vector'))
            await conn.execute(cast(LiteralString,
                f"""CREATE INDEX IF NOT EXISTS idx_kb_chunks_hnsw ON kb.chunks
                    USING hnsw ((embedding::"{s}".vector({INDEX_DIMS})) "{s}".vector_cosine_ops) WHERE embed_dims = {INDEX_DIMS}"""))
        return "Đã đổi sang pgvector + chỉ mục HNSW" if b.kind == "array" else "Đã ở pgvector — kiểm chỉ mục xong"


def index_name(dims: int) -> str:
    return f"idx_kb_chunks_hnsw_{int(dims)}"


async def add_index(owner_url: str, dims: int) -> str:
    """Dựng chỉ mục HNSW một phần cho MỘT số chiều khác 768 (ADR 0232 — chỉ khi một công ty được đặt hồ sơ nhúng với số chiều đó; không dựng sẵn "phòng xa").
    ≤ 2.000 chiều: ``vector``; 2.001–4.000: ``halfvec`` (độ chính xác nửa — pgvector). Chỉ mục viết thành biểu thức + ``WHERE embed_dims = N`` nên đoạn của các model
    số chiều khác không vào chỉ mục này. ``CREATE INDEX CONCURRENTLY`` (không khoá ghi). Chạy lại không sao. Cần quyền chủ DB; DB không pgvector (nhánh mảng) không cần chỉ mục."""
    from psycopg import AsyncConnection

    global _INDEXED
    if not 32 <= dims <= embed_profile.MAX_DIMS:
        return f"Số chiều phải trong 32–{embed_profile.MAX_DIMS}"
    async with await AsyncConnection.connect(owner_url, autocommit=True) as conn:
        cur = await conn.execute("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'vector'")
        ext = await cur.fetchone()
        b = await backend(conn)
        if ext is None or b.kind != "pgvector":
            return "DB đang ở nhánh mảng real[] (không pgvector): mọi số chiều ≤ 4.000 dùng được, không cần chỉ mục"
        s = ext[0]
        typ = "vector" if dims <= embed_profile.VECTOR_INDEX_MAX else "halfvec"
        await conn.execute(cast(LiteralString,
            f"""CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name(dims)} ON kb.chunks
                USING hnsw ((embedding::"{s}".{typ}({int(dims)})) "{s}".{typ}_cosine_ops) WHERE embed_dims = {int(dims)}"""))
    _INDEXED = None
    return f"Đã có chỉ mục HNSW {typ}({dims}) — {index_name(dims)}"


async def _cli(cmd: str, tenant: str | None, dims: int | None = None) -> None:
    from barista.kernel.migrate import migrate

    if cmd == "upgrade":
        print(await upgrade(settings.owner_database_url))
        return
    if cmd == "add-index":
        if not dims:
            raise SystemExit("add-index cần --dims N")
        print(await add_index(settings.owner_database_url, dims))
        return
    await db.open(settings.database_url, settings.owner_database_url)
    try:
        if cmd == "status":
            for r in await status(UUID(tenant) if tenant else None):
                print(f"{r['tenant_id']}  {r['embedded']}/{r['chunks']} đoạn ({r['percent']}%), chờ {r['pending']}, lỗi {r['failed']} — {r['profile']}"
                      + (f"; còn vector model khác: {r['other_models']}" if r["other_models"] else ""))
            return
        if cmd == "backfill":
            await migrate(settings.owner_database_url)
            for r in await status(UUID(tenant) if tenant else None):
                print(r["tenant_id"], await embed_pending(r["tenant_id"]))
    finally:
        await db.close()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Vector của kho tài liệu (ADR 0141, 0232)")
    ap.add_argument("cmd", choices=("status", "backfill", "upgrade", "add-index"),
                    help="status: tiến độ nhúng từng công ty · backfill: nhúng mọi đoạn còn thiếu (theo hồ sơ nhúng từng công ty) · upgrade: real[] → pgvector + HNSW "
                         "· add-index --dims N: chỉ mục HNSW cho số chiều khác 768")
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--dims", type=int, default=None)
    a = ap.parse_args()
    asyncio.run(_cli(a.cmd, a.tenant, a.dims))
