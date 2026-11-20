"""Tìm theo nghĩa + gộp RRF trong kho tài liệu (quyết định 0141). Model embedding GIẢ, tất định: từ trong ``ANCHORS`` dồn về cùng một chiều
(đồng nghĩa giả lập — từ khoá không khớp nhưng vector gần), từ khác băm vào chiều riêng. Mọi khẳng định đi qua ``kb.search`` như người /
trợ lý thật; nhánh mảng ``real[]`` (DB không pgvector) chạy trên CÙNG dữ liệu bằng ``vectors.EXACT_ARRAY``."""

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from pydantic_ai.embeddings import EmbeddingModel, EmbeddingResult, EmbeddingSettings
from pydantic_ai.embeddings.result import EmbedInputType
from pydantic_ai.usage import RequestUsage

from barista.agent import embed
from barista.config import settings
from barista.db import db
from barista.kernel import executor
from barista.kernel.clock import today
from barista.kernel.principal import issued, system_principal
from barista.modules import kb
from barista.modules.kb import vectors
from tests.conftest import Who, ok, owner_val, q

ANCHORS: dict[str, int] = {}


class Fake(EmbeddingModel):
    def __init__(self, *, delay: float = 0.0, fail: bool = False, poison: str = "", outage: bool = False) -> None:
        super().__init__()
        self.calls = 0
        self.texts = 0
        self.delay, self.fail, self.poison, self.outage = delay, fail, poison, outage
        self.seen: list[str] = []
        self.in_use: list[int] = []   # số kết nối app đang bị giữ lúc model được gọi (phải là 0: không gọi mạng trong giao dịch)

    @property
    def model_name(self) -> str:
        return "fake-embedding"

    @property
    def system(self) -> str:
        return "test"

    async def embed(self, inputs: str | Sequence[str], *, input_type: EmbedInputType,
                    settings: EmbeddingSettings | None = None) -> EmbeddingResult:
        inputs, settings = self.prepare_embed(inputs, settings)
        self.calls += 1
        self.texts += len(inputs)
        self.seen += list(inputs)
        st = db.app.get_stats()
        self.in_use.append(st.get("pool_size", 0) - st.get("pool_available", 0))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail or (self.poison and any(self.poison in t for t in inputs)):
            raise ValueError("model hỏng (giả)")
        if self.outage:
            raise ConnectionError("mất mạng (giả)")
        dims = settings.get("dimensions") or 768
        out = []
        for t in inputs:
            v = [0.0] * dims
            for w in kb.fold(t).split():
                w = "".join(ch for ch in w if ch.isalnum())
                if not w:
                    continue
                if w in ANCHORS:
                    v[ANCHORS[w]] += 10.0
                else:
                    v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dims] += 0.3
            out.append(v)
        return EmbeddingResult(embeddings=out, inputs=inputs, input_type=input_type, usage=RequestUsage(input_tokens=0),
                               model_name=self.model_name, provider_name=self.system)


@pytest_asyncio.fixture
async def fake(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Fake]:
    m = Fake()
    monkeypatch.setattr(settings, "embed_model", "test:fake-embedding")
    monkeypatch.setattr(settings, "kb_vector_min_sim", 0.7)
    embed.use_model(m)
    yield m
    await vectors.drain()
    embed.use_model(None)


def _syn(w: str) -> tuple[str, str]:
    """Hai từ khác nhau (từ khoá không khớp) mà model giả coi là đồng nghĩa."""
    a, b = f"syna{w}", f"synb{w}"
    ANCHORS[a] = ANCHORS[b] = len(ANCHORS) // 2 % 700 + 30
    return a, b


async def _doc(p: Any, title: str, body: str, **kw: Any) -> str:
    out = str((await ok(p, "kb.add_document", title=title, kind="policy", body=body, **kw))["document_id"])
    await vectors.drain()
    return out


async def _search(p: Any, text: str, **kw: Any) -> dict[str, Any]:
    return await q(p, "kb.search", q=text, **kw)


def _docs(page: dict[str, Any]) -> list[str]:
    return [str(r["document_id"]) for r in page["rows"]]


async def test_without_a_model_search_is_keyword_only_and_says_so(who: Who) -> None:
    gd = await who("giamdoc")
    w = uuid.uuid4().hex[:6]
    a, b = _syn(w)
    await _doc(gd, f"Hoàn tiền {w}", f"Khách được {b} trong 7 ngày.")
    page = await _search(gd, f"{a} {w}x")
    assert page["search_mode"] == "keyword" and page["semantic_off"] and "Chỉ tìm theo từ khoá" in page["note"]
    assert page["rows"] == []                                     # từ khoá khác hẳn ⇒ không thấy


async def test_hybrid_finds_the_paraphrase_and_fuses_with_keywords(who: Who, fake: Fake) -> None:
    gd = await who("giamdoc")
    w = uuid.uuid4().hex[:6]
    a, b = _syn(w)
    doc = await _doc(gd, f"Quy định đặt chỗ {w}", f"# Điều 2\nKhách được {b} khi dự án chậm mở bán.")
    n = await owner_val("SELECT count(*) FROM kb.chunks WHERE document_id = %s AND embed_model = %s AND embed_dims = 768",
                        (uuid.UUID(doc), settings.embed_model))
    assert n == 1                                                 # nhúng nền sau commit, ghi model + số chiều
    page = await _search(gd, f"{a} thế nào")
    assert page["search_mode"] == "hybrid" and "semantic_off" not in page
    hit = next(r for r in page["rows"] if str(r["document_id"]) == doc)
    assert hit["match"] == "semantic" and hit["similarity"] >= 0.7
    both = await _search(gd, b)
    assert next(r for r in both["rows"] if str(r["document_id"]) == doc)["match"] == "both"
    assert await owner_val("SELECT count(*) FROM core.ai_usage WHERE category IN ('kb:embed', 'kb:embed_query') AND surface = 'kb'") >= 2


@pytest.mark.parametrize("exact_array", [False, True], ids=["pgvector-or-column", "array-cosine"])
async def test_a_reader_never_gets_chunks_from_a_shelf_they_cannot_read(who: Who, fake: Fake, exact_array: bool,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """Đoạn của kệ Ban giám đốc GIỐNG HỆT câu hỏi (độ giống 1.0) vẫn không tới tay người không đọc được kệ — cả người lẫn trợ lý, cả hai nhánh."""
    monkeypatch.setattr(vectors, "EXACT_ARRAY", exact_array)
    gd, sale = await who("giamdoc"), await who("sale1")
    w = uuid.uuid4().hex[:6]
    a, b = _syn(w)
    secret = await _doc(gd, f"Ngân sách {w}", f"{a} {b} kế hoạch riêng", shelf="management")
    open_ = await _doc(gd, f"Chính sách chung {w}", f"{b} áp dụng cho mọi người — phần công khai thêm vài chữ khác",
                       context_code=f"P{w}")
    for p in (sale, sale.as_agent()):
        page = await _search(p, f"{a} {b} kế hoạch riêng")
        assert page["search_mode"] == "hybrid"
        assert secret not in _docs(page) and open_ in _docs(page)
    assert secret in _docs(await _search(gd, f"{a} {b} kế hoạch riêng"))
    # bộ lọc kệ / phạm vi người hỏi chọn cũng áp vào nhánh nghĩa
    assert open_ not in _docs(await _search(gd, f"{a} {b}", shelf="management"))
    assert open_ not in _docs(await _search(gd, f"{a} {b}", context_code="KHONGCO"))       # tài liệu phạm vi khác
    assert open_ in _docs(await _search(gd, f"{a} {b}", context_code=f"P{w}"))


async def test_superseded_and_expired_documents_stay_out_unless_history_is_asked(who: Who, fake: Fake) -> None:
    gd = await who("giamdoc")
    w = uuid.uuid4().hex[:6]
    a, b = _syn(w)
    old = await _doc(gd, f"Hoàn cọc {w} bản 1", f"Khách được {b} trong 30 ngày.", effective_from=str(today() - timedelta(days=40)))
    await _doc(gd, f"Hoàn cọc {w} bản 2", f"Thời hạn xử lý đổi thành 15 ngày làm việc {w}.", effective_from=str(today()),
               replaces_document_id=old)
    gone = await _doc(gd, f"Ưu đãi {w}", f"{b} cho khách tháng trước.", effective_to=str(today() - timedelta(days=1)))
    page = await _search(gd, a)
    assert page["search_mode"] == "hybrid" and old not in _docs(page) and gone not in _docs(page)
    hist = await _search(gd, a, include_history=True)
    states = {str(r["document_id"]): r["state"] for r in hist["rows"]}
    assert states.get(old) == "superseded" and states.get(gone) == "expired"


async def test_fallbacks_ai_off_timeout_and_model_error(who: Who, fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    gd = await who("giamdoc")
    w = uuid.uuid4().hex[:6]
    a, b = _syn(w)
    doc = await _doc(gd, f"Quy định {w}", f"{b} và {w}kw")
    off = issued(replace(gd, ai=False))
    page = await _search(off, f"{w}kw")
    assert page["search_mode"] == "keyword" and "tắt trợ lý AI" in page["semantic_off"] and doc in _docs(page)   # từ khoá vẫn chạy
    blocked = issued(replace(gd, ai_block="hết hạn mức"))
    assert (await _search(blocked, a))["semantic_off"] == "hết hạn mức"
    embed.use_model(Fake(delay=0.5))
    monkeypatch.setattr(settings, "embed_query_timeout_s", 0.05)
    page = await _search(gd, f"{w}kw")
    assert page["search_mode"] == "keyword" and "quá hạn" in page["semantic_off"] and doc in _docs(page)
    embed.use_model(Fake(fail=True))
    page = await _search(gd, f"{w}kw")
    assert page["search_mode"] == "keyword" and "lỗi" in page["semantic_off"]


async def test_resync_keeps_vectors_and_the_sweep_fills_missing_ones(who: Who, demo: dict[str, Any], fake: Fake) -> None:
    gd = await who("giamdoc")
    w = uuid.uuid4().hex[:6]
    body = f"# Mục 1\nĐoạn một {w}.\n# Mục 2\nĐoạn hai {w}."
    p = system_principal(demo["tenant"])
    r = await executor.run(p, "kb.sync_external", {"external_id": f"g{w}", "title": f"Drive {w}", "body": body, "shelf": "common"})
    assert r.ok
    doc = r.output["document_id"]
    await vectors.drain()
    before = fake.texts
    assert await owner_val("SELECT count(*) FROM kb.chunks WHERE document_id = %s AND embedding IS NOT NULL", (doc,)) == 2
    # đồng bộ lại y nguyên ⇒ không gọi model; sửa một mục ⇒ chỉ nhúng đoạn đó
    assert (await executor.run(p, "kb.sync_external", {"external_id": f"g{w}", "title": f"Drive {w}", "body": body, "shelf": "common"})).ok
    await vectors.drain()
    assert fake.texts == before
    body2 = body.replace("Đoạn hai", "Đoạn hai đã sửa")
    assert (await executor.run(p, "kb.sync_external", {"external_id": f"g{w}", "title": f"Drive {w}", "body": body2, "shelf": "common"})).ok
    await vectors.drain()
    assert fake.texts == before + 1
    # mất vector (tiến trình chết trước khi nhúng) ⇒ vòng quét nhúng lại; tài liệu không người thêm ⇒ lượt đo ghi cho chủ tài khoản
    await owner_val("UPDATE kb.chunks SET embedding = NULL, embed_model = NULL, embed_dims = NULL WHERE document_id = %s RETURNING 1", (doc,))
    assert (await executor.run(p, "kb.embed_sweep", {}, quiet=True)).ok
    await vectors.drain()
    assert await owner_val("SELECT count(*) FROM kb.chunks WHERE document_id = %s AND embedding IS NOT NULL", (doc,)) == 2
    assert gd.tenant_id == demo["tenant"]


async def test_failed_embedding_waits_before_retry_and_ai_off_company_is_never_sent(who: Who, demo: dict[str, Any], fake: Fake) -> None:
    gd = await who("giamdoc")
    w = uuid.uuid4().hex[:6]
    embed.use_model(Fake(fail=True))
    doc = await _doc(gd, f"Lỗi {w}", f"Nội dung {w}.")
    row = await owner_val("SELECT jsonb_build_object('tried', embed_tried_at IS NOT NULL, 'err', embed_error) FROM kb.chunks WHERE document_id = %s",
                          (uuid.UUID(doc),))
    assert row["tried"] and "lỗi" in row["err"]
    again = Fake()
    embed.use_model(again)
    await vectors.embed_pending(demo["tenant"])
    assert await owner_val("SELECT bool_and(embedding IS NULL) FROM kb.chunks WHERE document_id = %s", (uuid.UUID(doc),))  # vừa hỏng ⇒ chờ RETRY_AFTER
    out = await vectors.embed_pending(demo["tenant"], uuid.UUID(doc))
    assert out["embedded"] == 1                                    # hỏi đúng tài liệu (lệnh tay) ⇒ thử ngay
    # công ty tắt AI ⇒ việc nền không gửi đoạn nào ra nhà cung cấp; đoạn vào thời gian chờ (vòng quét không chọn lại công ty này mỗi phút)
    other = demo["other"]
    admin_o = await who("admin", other=True)
    await owner_val("UPDATE core.tenant_profile SET ai_enabled = false WHERE tenant_id = %s RETURNING 1", (other,))
    try:
        spy = Fake()
        embed.use_model(spy)
        r = await executor.run(admin_o, "kb.add_document", {"title": f"Tắt AI {w}", "body": f"Bí mật {w}", "shelf": "common"})
        assert r.ok
        await vectors.drain()
        assert spy.calls == 0
        row = await owner_val("""SELECT jsonb_build_object('vec', bool_or(embedding IS NOT NULL), 'err', min(embed_error))
                                 FROM kb.chunks WHERE document_id = %s""", (r.output["document_id"],))
        assert row["vec"] is False and "tắt trợ lý AI" in row["err"]
        sweep_due = await owner_val(f"SELECT count(*) FROM ({vectors.SWEEP}) s WHERE s.tenant_id = %s", (other,))
        assert sweep_due == 0
    finally:
        await owner_val("UPDATE core.tenant_profile SET ai_enabled = true WHERE tenant_id = %s RETURNING 1", (other,))


async def test_changing_the_model_means_old_vectors_are_not_used(who: Who, fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    gd = await who("giamdoc")
    w = uuid.uuid4().hex[:6]
    a, b = _syn(w)
    doc = await _doc(gd, f"Đổi model {w}", f"{b} nội dung")
    monkeypatch.setattr(settings, "embed_model", "test:fake-embedding-v2")
    page = await _search(gd, a)
    assert doc not in _docs(page)                                 # vector của model cũ không so với câu hỏi của model mới
    await vectors.embed_pending(gd.tenant_id, uuid.UUID(doc))
    assert doc in _docs(await _search(gd, a))


async def test_backend_is_read_from_the_column_type() -> None:
    async with db.owner.connection() as conn:
        b = await vectors.backend(conn)
        cur = await conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        has = await cur.fetchone() is not None
        assert b.kind == ("pgvector" if has else "array")
        if has:   # chỉ mục HNSW có và câu tìm dùng được nó (đủ nhiều đoạn ⇒ planner chọn chỉ mục)
            cur = await conn.execute("SELECT indexdef FROM pg_indexes WHERE schemaname = 'kb' AND indexname = 'idx_kb_chunks_hnsw'")
            assert "hnsw" in (await cur.fetchone() or {}).get("indexdef", "")


def test_rrf_puts_items_ranked_high_in_both_lists_first() -> None:
    fused = vectors.rrf(["a", "b", "c"], ["c", "a", "d"])
    assert sorted(fused, key=lambda k: -fused[k])[:2] == ["a", "c"]
    assert fused["a"] == pytest.approx(1 / 61 + 1 / 62)


async def test_query_embedding_never_holds_a_db_connection(who: Who, fake: Fake) -> None:
    """Review 08/11: nhúng câu tìm (tới 4 giây) chạy ở bước prepare NGOÀI giao dịch — lúc gọi model không kết nối app nào đang bị giữ."""
    gd = await who("giamdoc")
    w = uuid.uuid4().hex[:6]
    a, b = _syn(w)
    await _doc(gd, f"Kết nối {w}", f"{b} nội dung")
    fake.in_use.clear()
    page = await _search(gd, f"{a} câu mới {w}")
    assert page["search_mode"] == "hybrid" and fake.in_use == [0]


async def test_one_bad_chunk_does_not_fail_its_batch_and_outage_backs_off(who: Who, demo: dict[str, Any], fake: Fake) -> None:
    gd = await who("giamdoc")
    w = uuid.uuid4().hex[:6]
    embed.use_model(Fake(poison=f"POISON{w}"))
    doc = await _doc(gd, f"Lô {w}", f"# Mục 1\nĐoạn tốt {w}.\n# Mục 2\nĐoạn POISON{w} lạ.\n# Mục 3\nĐoạn tốt nữa {w}.")
    rows = await owner_val("""SELECT jsonb_agg(jsonb_build_object('ok', embedding IS NOT NULL, 'err', embed_error, 'held', embed_claimed_at)
                              ORDER BY seq) FROM kb.chunks WHERE document_id = %s""", (uuid.UUID(doc),))
    assert [r["ok"] for r in rows] == [True, False, True] and rows[1]["err"] and all(r["held"] is None for r in rows)
    # nhà cung cấp mất mạng ⇒ cả lô vào thời gian chờ, việc dừng (không thử từng đoạn)
    down = Fake(outage=True)
    embed.use_model(down)
    doc2 = await _doc(gd, f"Mất mạng {w}", f"# A\nMột {w}.\n# B\nHai {w}.")
    assert down.calls == settings.embed_attempts
    assert await owner_val("SELECT count(*) FROM kb.chunks WHERE document_id = %s AND embed_error IS NOT NULL", (uuid.UUID(doc2),)) == 2


async def test_sweep_never_waits_on_the_model_and_claims_prevent_double_work(who: Who, demo: dict[str, Any], fake: Fake) -> None:
    gd = await who("giamdoc")
    w = uuid.uuid4().hex[:6]
    await vectors.drain()
    slow = Fake(delay=0.6)
    embed.use_model(slow)
    r = await executor.run(gd, "kb.add_document", {"title": f"Chậm {w}", "body": f"# A\nMột {w}.\n# B\nHai {w}.", "shelf": "common"})
    assert r.ok
    t0 = asyncio.get_running_loop().time()
    assert (await executor.run(system_principal(demo["tenant"]), "kb.embed_sweep", {}, quiet=True)).ok
    assert asyncio.get_running_loop().time() - t0 < 0.4                   # vòng quét chung chỉ khởi động việc nền
    await vectors.drain()
    assert await owner_val("SELECT count(*) FROM kb.chunks WHERE document_id = %s AND embedding IS NOT NULL", (r.output["document_id"],)) == 2
    assert sum(1 for t in slow.seen if w in t) == 2                        # việc theo tài liệu + vòng quét: không đoạn nào nhúng hai lần
