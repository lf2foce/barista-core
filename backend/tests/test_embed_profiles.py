"""Hồ sơ nhúng THEO CÔNG TY (ADR 0232, mục "Nhúng theo công ty"): nhà vận hành đổi model embedding của MỘT công ty (vd Gemini → EmbeddingGemma tự dựng) mà tìm kiếm vẫn chạy.
Model GIẢ, tất định (``tests/test_kb_hybrid.Fake``) — không gọi mạng. Khẳng định chính: không bao giờ trộn vector của hai model; vòng quét nhúng lại đúng công ty đổi;
quay lại model cũ được; số chiều khác 768 cần chỉ mục; chỉ nhà vận hành đặt được; đo theo (công ty, model) và máy tự dựng 0 USD."""

import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any, LiteralString, cast

import pytest
import pytest_asyncio
from pydantic_ai.embeddings import EmbeddingResult, EmbeddingSettings
from pydantic_ai.embeddings.result import EmbedInputType

from barista import ai_policy
from barista.agent import embed, embed_profile
from barista.agent.embed_profile import Profile
from barista.config import settings
from barista.db import db
from barista.kernel import executor
from barista.kernel.errors import Invalid
from barista.kernel.principal import system_principal
from barista.modules.kb import vectors
from tests.conftest import OWNER_URL, Who, owner_val, q
from tests.test_kb_hybrid import Fake, _doc, _docs, _search, _syn

OP = {"by": "Anh (test)", "why": "kiểm thử hồ sơ nhúng"}
LOCAL = "openai-compatible:fake-local"
DEFAULT = "google:fake-embedding"
URL = "http://127.0.0.1:1/v1"   # không bao giờ được gọi — model giả được tiêm


class Native(Fake):
    """Model giả có số chiều GỐC cố định (như EmbeddingGemma 768, bge-m3 1024) và ghi lại ``settings`` đã nhận."""

    def __init__(self, native: int = 768, name: str = "fake-local", **kw: Any) -> None:
        super().__init__(**kw)
        self.native, self.name = native, name
        self.sent: list[dict[str, Any]] = []
        self.kinds: list[str] = []

    @property
    def model_name(self) -> str:
        return self.name

    async def embed(self, inputs: str | Sequence[str], *, input_type: EmbedInputType, settings: EmbeddingSettings | None = None) -> EmbeddingResult:
        self.sent.append(dict(settings or {}))
        self.kinds.append(input_type)
        return await super().embed(inputs, input_type=input_type, settings={"dimensions": self.native})


@pytest_asyncio.fixture
async def models(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Fake]]:
    """Mặc định bản cài = model ``google:fake-embedding`` (Fake); hồ sơ riêng ``openai-compatible:fake-local`` = Native. Hàm hồ sơ → model: đúng model theo hồ sơ."""
    by: dict[str, Fake] = {DEFAULT: Fake(), LOCAL: Native(768)}
    monkeypatch.setattr(settings, "embed_model", DEFAULT)
    monkeypatch.setattr(settings, "kb_vector_min_sim", 0.7)
    embed.use_model(lambda prof: by[prof.model] if prof.model in by else by.setdefault(prof.model, Native(prof.dims, prof.model)))
    embed_profile.invalidate()
    yield by
    await vectors.drain()
    embed.use_model(None)
    embed_profile.invalidate()


async def _set(tid: Any, **kw: Any) -> dict[str, Any]:
    r = await executor.run(system_principal(tid), "core.set_embed_profile", {**OP, **kw})
    assert r.status == "ok", (r.status, r.error)
    return r.output


async def _drop(*docs: str) -> None:
    """Xoá tài liệu test đã tạo ở công ty khác: đoạn của nó mang vector model giả ≠ model mặc định bản cài sẽ làm vòng quét coi công ty "còn việc" và phá test khác."""
    for d in (x for x in docs if x):
        await owner_val("DELETE FROM kb.documents WHERE id = %s RETURNING 1", (uuid.UUID(d),))


async def _clear(tid: Any) -> None:
    await executor.run(system_principal(tid), "core.clear_embed_profile", OP)
    embed_profile.invalidate(tid)


# ── hồ sơ thuần (không DB) ──

def test_validation_refuses_what_cannot_work_and_never_takes_a_secret() -> None:
    with pytest.raises(Invalid, match="base_url"):
        embed_profile.check(Profile(model=LOCAL))                                              # máy tự dựng phải có địa chỉ
    with pytest.raises(Invalid, match="base_url chỉ dùng"):
        embed_profile.check(Profile(model="google:gemini-embedding-001", base_url=URL))
    with pytest.raises(Invalid, match="truncate chỉ cho"):
        embed_profile.check(Profile(model="google:gemini-embedding-001", truncate=True))
    with pytest.raises(Invalid, match="Matryoshka"):                                           # model 1024 chiều gốc mà đòi 768 không cắt ⇒ chỉ dẫn
        embed_profile.check(Profile(model="openai-compatible:bge-m3", base_url=URL, dims=768))
    with pytest.raises(Invalid, match="chiều gốc"):
        embed_profile.check(Profile(model="openai-compatible:embeddinggemma", base_url=URL, dims=1024))
    with pytest.raises(Invalid):
        embed_profile.validate({"key_ref": "sk-abc def"})                                      # khoá không đi vào hồ sơ — chỉ TÊN mục
    with pytest.raises(Invalid):
        embed_profile.validate({"khong_co": 1})
    ok_ = embed_profile.check(Profile(model="openai-compatible:embeddinggemma", base_url=URL, dims=768, truncate=True))
    assert ok_.compat and ok_.self_hosted and not ok_.off


def test_changing_model_does_not_inherit_the_old_models_address_or_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "embed_model", LOCAL)
    monkeypatch.setattr(settings, "embed_profile", {"base_url": URL, "query_prefix": "q: "})
    inst = embed_profile.compose(None)
    assert inst.base_url == URL and inst.query_prefix == "q: " and inst.source == "bản cài"
    other = embed_profile.compose({"model": "google:gemini-embedding-001", "dims": 768})
    assert other.base_url == "" and other.query_prefix == "" and other.source == "công ty"     # không gửi tiền tố / địa chỉ của model cũ sang model mới


# ── nhúng theo hồ sơ ──

async def test_a_self_hosted_endpoint_gets_no_dimensions_param_truncates_matryoshka_and_prefixes_by_kind(who: Who, models: dict[str, Fake]) -> None:
    gd = await who("giamdoc")
    big = Native(1024)
    models[LOCAL] = big
    prof = Profile(model=LOCAL, base_url=URL, dims=768, truncate=True, query_prefix="Q: ", document_prefix="D: ", source="công ty")
    out = await embed.embed(gd, ["xin chào"], kind="document", profile=prof)
    assert len(out.vectors[0]) == 768 and abs(sum(x * x for x in out.vectors[0]) - 1.0) < 1e-6   # cắt rồi chuẩn hoá lại
    await embed.embed(gd, ["xin chào"], kind="query", profile=prof)
    assert big.sent == [{}, {}] and big.kinds == ["document", "query"]                       # không gửi dimensions (vLLM từ chối model không hỗ trợ)
    assert big.seen == ["D: xin chào", "Q: xin chào"]                                          # tiền tố khác nhau theo loại
    with pytest.raises(embed.Unavailable, match="1024 chiều"):
        await embed.embed(gd, ["xin chào"], kind="document", profile=Profile(model=LOCAL, base_url=URL, dims=768, source="công ty"))   # không cắt khi không khai
    cloud = Native(768)
    models["google:fake"] = cloud
    await embed.embed(gd, ["xin chào"], kind="document", profile=Profile(model="google:fake", dims=768, source="công ty"))
    assert cloud.sent == [{"dimensions": 768}]                                                 # nhà cung cấp ngoài nhận dimensions qua API


async def test_metering_is_per_company_and_model_and_self_hosted_costs_zero(who: Who, demo: dict[str, Any], models: dict[str, Fake]) -> None:
    gd = await who("giamdoc")
    prof = Profile(model=LOCAL, base_url=URL, dims=768, source="công ty")
    await embed.embed(gd, ["một", "hai"], kind="document", profile=prof)
    row = await owner_val("""SELECT jsonb_build_object('model', model, 'cost', cost_usd, 'tokens', input_tokens, 'cat', category) FROM core.ai_usage
                             WHERE tenant_id = %s AND model = %s ORDER BY id DESC LIMIT 1""", (demo["tenant"], LOCAL))
    assert row["model"] == LOCAL and float(row["cost"]) == 0.0 and row["tokens"] > 0 and row["cat"] == "kb:embed"   # 0 USD nhưng vẫn đếm token + lượt (không NULL = "chưa có giá")
    paid = Profile(model=LOCAL, base_url=URL, dims=768, usd_per_mtok=2.0, source="công ty")
    out = await embed.embed(gd, ["x" * 3000], kind="document", profile=paid)
    assert out.cost_usd is not None and abs(float(out.cost_usd) - out.tokens * 2.0 / 1e6) < 1e-12
    card = await q(system_principal(demo["tenant"]), "core.ai_token_report")
    tables = {b["page"]["title"]: b["page"] for b in card["blocks"] if b.get("type") == "table"}
    emb = next(t for k, t in tables.items() if k.startswith("Nhúng theo model"))
    assert any(r["model"] == LOCAL and r["runs"] >= 1 for r in emb["rows"])                      # báo cáo token của nhà vận hành thấy theo model


# ── đổi model của MỘT công ty ──

async def test_switching_one_company_never_mixes_models_then_the_sweep_reembeds_and_rollback_works(who: Who, demo: dict[str, Any], models: dict[str, Fake]) -> None:
    other, _mine = demo["other"], demo["tenant"]
    admin_o = await who("admin", other=True)
    gd = await who("giamdoc")
    w = uuid.uuid4().hex[:6]
    a, b = _syn(w)
    d_other = d_mine = ""
    try:
        d_other = await _doc(admin_o, f"Hoàn tiền {w}", f"Khách được {b} trong 7 ngày.", shelf="common")
        d_mine = await _doc(gd, f"Hoàn tiền {w}", f"Khách được {b} trong 7 ngày.")
        assert (await _search(admin_o, f"{a} {w}x"))["search_mode"] == "hybrid" and d_other in _docs(await _search(admin_o, f"{a} {w}x"))
        # chạy thử: không ghi gì
        dry = await _set(other, model=LOCAL, base_url=URL, dry_run=True)
        assert dry["dry_run"] and dry["reembed"] and await owner_val("SELECT count(*) FROM core.embed_profiles WHERE tenant_id = %s", (other,)) == 0
        out = await _set(other, model=LOCAL, base_url=URL)
        assert out["reembed"] and "nhúng lại" in out["summary"]
        assert (await executor.run(system_principal(other), "kb.embed_requeue", {})).ok
        # NGAY SAU KHI ĐỔI: chỉ từ khoá — không dùng vector model cũ; công ty KHÁC không bị đụng
        page = await _search(admin_o, f"{a} {w}x")
        assert page["search_mode"] == "keyword" and "model đang dùng" in page["semantic_off"] and d_other not in _docs(page)
        assert d_mine in _docs(await _search(gd, f"{a} {w}x"))
        st = (await vectors.status(other))[0]
        assert st["embedded"] == 0 and st["pending"] == st["chunks"] > 0 and st["percent"] == 0 and f"{DEFAULT}/768" in st["other_models"]
        old_calls = models[DEFAULT].calls
        due = await owner_val(f"SELECT count(*) FROM ({vectors.SWEEP}) s WHERE s.tenant_id = %s", (other,))
        assert due == 1                                                                           # vòng quét thấy công ty này
        assert (await executor.run(system_principal(other), "kb.embed_sweep", {}, quiet=True)).ok
        await vectors.drain()
        assert models[DEFAULT].calls == old_calls                                    # model cũ không bị gọi cho công ty đã đổi
        loc = models[LOCAL]
        assert isinstance(loc, Native) and loc.texts > 0
        st = (await vectors.status(other))[0]
        assert st["pending"] == 0 and st["percent"] == 100.0 and not st["other_models"]
        found = await _search(admin_o, f"{a} {w}x")
        assert found["search_mode"] == "hybrid" and d_other in _docs(found)                         # tìm lại được, bằng model MỚI
        assert await owner_val("SELECT bool_and(embed_model = %s) FROM kb.chunks WHERE document_id = %s", (LOCAL, d_other))
        assert await owner_val("SELECT bool_and(embed_model = %s) FROM kb.chunks WHERE document_id = %s", (DEFAULT, d_mine))   # công ty kia giữ nguyên
        # đo theo (công ty, model)
        shown = (await executor.query(system_principal(other), "core.embed_profile", {})).output or {}
        assert shown["effective"]["model"] == LOCAL and any(u["model"] == LOCAL and u["usd"] == 0 for u in shown["usage"])
        # quay lại model cũ
        back = await _set(other, rollback=True)
        assert back["profile"]["model"] == DEFAULT and back["reembed"]
        assert (await _search(admin_o, f"{a} {w}x"))["search_mode"] == "keyword"
        assert (await executor.run(system_principal(other), "kb.embed_sweep", {}, quiet=True)).ok
        await vectors.drain()
        assert d_other in _docs(await _search(admin_o, f"{a} {w}x"))
    finally:
        await _clear(other)
        await _drop(d_other, d_mine)


async def test_only_the_operator_sets_it_and_a_company_can_turn_semantic_search_off(who: Who, demo: dict[str, Any], models: dict[str, Fake]) -> None:
    admin_o = await who("admin", other=True)
    other = demo["other"]
    denied = await executor.run(admin_o, "core.set_embed_profile", {"model": LOCAL, "base_url": URL, **OP})
    assert denied.status == "denied"                                                             # quản trị công ty không tự đổi nhà cung cấp nhận dữ liệu của mình
    no_who = await executor.run(system_principal(other), "core.set_embed_profile", {"model": LOCAL, "base_url": URL, "by": "", "why": ""})
    assert no_who.status != "ok"
    for bad in ({"model": LOCAL}, {"model": "khong-co-nha:abc"}, {"model": "google:gemini-embedding-001", "dims": 99999}):
        r = await executor.run(system_principal(other), "core.set_embed_profile", {**bad, **OP})
        assert r.status != "ok", bad
    w = uuid.uuid4().hex[:6]
    a, b = _syn(w)
    docs: list[str] = []
    try:
        d = await _doc(admin_o, f"Tắt {w}", f"Khách được {b}.", shelf="common")
        docs.append(d)
        assert d in _docs(await _search(admin_o, f"{a} {w}x"))
        out = await _set(other, off=True)
        assert out["off"] and not out["reembed"]
        page = await _search(admin_o, f"{a} {w}x")
        assert page["search_mode"] == "keyword" and "tắt tìm theo nghĩa" in page["semantic_off"]
        assert await owner_val(f"SELECT count(*) FROM ({vectors.SWEEP}) s WHERE s.tenant_id = %s", (other,)) == 0   # tắt ⇒ vòng quét không chọn công ty này
    finally:
        await _clear(other)
        await _drop(*docs)


# ── số chiều khác 768 ──

@pytest.mark.parametrize("dims", [1536, 2048])   # 2048 > 2000 ⇒ chỉ mục halfvec
async def test_other_dimensions_need_their_own_index_and_then_search_works(who: Who, demo: dict[str, Any], models: dict[str, Fake], dims: int) -> None:
    other = demo["other"]
    admin_o = await who("admin", other=True)
    async with db.owner.connection() as c:
        b = await vectors.backend(c)
    if b.kind != "pgvector":
        pytest.skip("DB test không có pgvector")
    w = uuid.uuid4().hex[:6]
    a, bb = _syn(w)
    name = f"openai-compatible:wide-{dims}"
    d = ""
    try:
        await _set(other, model=name, base_url=URL, dims=dims, truncate=True)                     # lệnh KHÔNG kiểm chỉ mục (CLI + lúc chạy kiểm)
        d = await _doc(admin_o, f"Rộng {w}", f"Khách được {bb}.", shelf="common")
        page = await _search(admin_o, f"{a} {w}x")
        assert page["search_mode"] == "keyword" and "add-index" in page["semantic_off"]           # chưa có chỉ mục ⇒ từ chối, không quét tuần tự âm thầm
        assert await owner_val("SELECT bool_and(embedding IS NULL) FROM kb.chunks WHERE document_id = %s", (d,))   # và không nhúng (không tốn tiền vô ích)
        assert "Đã có chỉ mục" in await vectors.add_index(OWNER_URL, dims)
        assert "Đã có chỉ mục" in await vectors.add_index(OWNER_URL, dims)                         # chạy lại không sao
        assert (await executor.run(system_principal(other), "kb.embed_requeue", {})).ok
        await vectors.embed_pending(other)
        assert await owner_val("SELECT bool_and(embed_dims = %s) FROM kb.chunks WHERE document_id = %s", (dims, d))
        found = await _search(admin_o, f"{a} {w}x")
        assert found["search_mode"] == "hybrid" and d in _docs(found)
        plan = await vectors.plan(other, embed_profile.compose({"model": name, "dims": dims, "base_url": URL, "truncate": True}))
        assert plan["dims_problem"] == "" and plan["to_embed"] == 0
    finally:
        await _clear(other)
        if d:
            await _drop(d)
        async with db.owner.connection() as c:
            await c.execute(cast(LiteralString, f"DROP INDEX IF EXISTS kb.{vectors.index_name(dims)}"))
        vectors._INDEXED = None


async def test_dims_problem_messages() -> None:
    arr = vectors.Backend("array")
    pg = vectors.Backend("pgvector", "public")
    assert vectors.dims_problem(arr, 1536, frozenset()) == ""                                   # nhánh mảng: đúng tuyệt đối, không cần chỉ mục
    assert vectors.dims_problem(pg, 768, frozenset({768})) == ""
    assert "add-index 1024" in vectors.dims_problem(pg, 1024, frozenset({768}))
    assert "vượt trần" in vectors.dims_problem(pg, 5000, frozenset({768}))


# ── lệnh vận hành: chạy thử + kiểm máy chủ + ước chi phí ──

async def test_the_operator_command_checks_the_endpoint_estimates_cost_and_refuses_a_wrong_dimension(demo: dict[str, Any], models: dict[str, Fake],
                                                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    tid = demo["other"]
    p = ai_policy.parser()
    gd = await ai_policy.query(tid, "core.embed_profile")
    assert gd["source"] == "bản cài"
    # máy chủ trả 1024 chiều mà hồ sơ muốn 768 không cắt ⇒ từ chối, nói cách sửa
    models["openai-compatible:bge-m3"] = Native(1024, "bge-m3")
    args = p.parse_args(["--tenant", "x", "embed-set", "--model", "openai-compatible:bge-m3", "--base-url", URL, "--dry-run"])
    with pytest.raises(SystemExit, match="Matryoshka"):                                       # bge-m3 gốc 1024 chiều, không khai cắt ⇒ từ chối + chỉ cách sửa (dims=1024 + chỉ mục, hoặc truncate nếu đúng là Matryoshka)
        await ai_policy.embed_set(tid, "x", args)
    args = p.parse_args(["--tenant", "x", "embed-set", "--model", "openai-compatible:embeddinggemma", "--base-url", URL, "--dry-run"])
    models["openai-compatible:embeddinggemma"] = Native(768, "embeddinggemma")
    await ai_policy.embed_set(tid, "x", args)
    text = capsys.readouterr().out
    assert "kiểm máy chủ: ĐƯỢC" in text and "cần nhúng lại" in text and "0.0000 USD" in text and "chưa ghi gì" in text
    assert (await ai_policy.query(tid, "core.embed_profile"))["source"] == "bản cài"              # chạy thử không ghi
    models["openai-compatible:embeddinggemma"] = Native(1024, "embeddinggemma")                   # máy chủ lại phục vụ model 1024 chiều
    with pytest.raises(SystemExit, match="KHÔNG ĐỔI"):
        await ai_policy.embed_set(tid, "x", p.parse_args(["--tenant", "x", "embed-set", "--model", "openai-compatible:embeddinggemma", "--base-url", URL,
                                                          "--truncate", "no", "--dry-run"]))
    models["openai-compatible:embeddinggemma"] = Native(768, "embeddinggemma")
    try:
        real = p.parse_args(["--tenant", "x", "embed-set", "--model", "openai-compatible:embeddinggemma", "--base-url", URL, "--by", "Anh", "--why", "thử"])
        await ai_policy.embed_set(tid, "x", real)
        row = await owner_val("SELECT profile FROM core.embed_profiles WHERE tenant_id = %s", (tid,))
        assert row["model"] == "openai-compatible:embeddinggemma" and row["query_prefix"].startswith("task: search result") and row["truncate"] is True   # gợi ý model quen điền sẵn
        await ai_policy.embed_show(tid, "x")
        assert "hồ sơ nhúng" in capsys.readouterr().out
    finally:
        await _clear(tid)


async def test_plan_counts_only_chunks_that_still_need_the_new_model(who: Who, demo: dict[str, Any], models: dict[str, Fake]) -> None:
    gd = await who("giamdoc")
    w = uuid.uuid4().hex[:6]
    await _doc(gd, f"Kế hoạch {w}", f"Nội dung {w}.")
    tid = demo["tenant"]
    assert (await executor.run(system_principal(tid), "kb.embed_requeue", {})).ok          # test khác trong cùng tiến trình có thể để đoạn đang chờ sau lỗi
    await vectors.embed_pending(tid)
    same = await vectors.plan(tid, embed_profile.compose(None))
    assert same["to_embed"] == 0 and same["already_new"] == same["chunks"] > 0               # cùng model ⇒ không phải nhúng lại gì
    cloud = embed_profile.compose({"model": "google:gemini-embedding-001", "dims": 768})
    new = await vectors.plan(tid, cloud)
    assert new["to_embed"] == new["chunks"] and new["tokens_est"] > 0 and new["batches"] >= 1 and new["seconds_est"] > 0
    assert new["cost_usd_est"] is not None and new["cost_usd_est"] >= 0
    local = await vectors.plan(tid, embed_profile.compose({"model": LOCAL, "base_url": URL, "dims": 768}))
    assert local["cost_usd_est"] == 0


# ── benchmark truy hồi ──

def test_retrieval_metrics_are_computed_from_ranks() -> None:
    from barista.docread import bench_retrieval as br

    assert br.rank_of([3, 1, 2], {2}) == 3 and br.rank_of([3, 1], {9}) is None
    rec, mrr = br.score([1, 2, 6, None])
    assert rec == {5: 0.5, 10: 0.75} and abs(mrr - (1 + 0.5 + 1 / 6) / 4) < 1e-9
    assert br.order_by_cosine([1.0, 0.0], [[0.0, 1.0], [1.0, 0.0]]) == [1, 0]
    prof = br.parse_config("openai-compatible:embeddinggemma@768@http://h:1/v1")
    assert prof.compat and prof.truncate and prof.query_prefix.startswith("task:") and prof.base_url == "http://h:1/v1" and prof.dims == 768
    cloud = br.parse_config("google:gemini-embedding-001@1536")
    assert not cloud.truncate and cloud.dims == 1536 and cloud.query_prefix == ""


async def test_retrieval_bench_runs_offline_with_a_fake_embedder_and_one_broken_config_does_not_stop_the_rest(capsys: pytest.CaptureFixture[str]) -> None:
    from barista.docread import bench, bench_retrieval

    data = bench_retrieval.load()
    assert len(data["passages"]) >= 60 and len(data["questions"]) >= 40
    try:
        rc = await bench.main(bench.parser().parse_args(["--retrieval", "--fake", "--embed-configs", "fake:hash-bow@768,fake:hash-bow@1536", "--detail"]))
        text = capsys.readouterr().out
        assert rc == 0 and "MODEL BĂM TỪ" in text and "recall@5" in text and text.count("fake:hash-bow") >= 2
        embed.use_model(None)
        rc = await bench.main(bench.parser().parse_args(["--retrieval", "--embed-configs", "openai-compatible:embeddinggemma@768@http://127.0.0.1:1/v1"]))
        assert rc == 1 and "KHÔNG CHẠY ĐƯỢC" in capsys.readouterr().out                    # máy chủ không có ⇒ ghi lý do, không đổ cả lượt đo
    finally:
        embed.use_model(None)
