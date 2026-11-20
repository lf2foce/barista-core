"""Tệp ở kho đối tượng, DB giữ tham chiếu (quyết định 0122) — mọi dòng "Giữ bằng" của ADR + phần sao tệp giữa module, chạy với nơi chứa thư mục cục
bộ (thư mục tạm riêng của tiến trình test — conftest). Bộ nối R2 có bài kiểm riêng (``test_files_r2.py``) chỉ chạy khi có khoá."""

import base64
import hashlib
import logging
import tracemalloc
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from barista.db import db
from barista.http_api import app
from barista.kernel import executor, files
from barista.kernel.errors import Denied, Invalid, NotFound
from barista.kernel.executor import Ctx, core_ctx
from barista.kernel.principal import Principal, system_principal
from barista.modules import kb, talk
from tests.conftest import Who, ok, owner_val, q
from tests.test_talk_daily import PNG, _lead, _post_file

MB = 1024 * 1024


@asynccontextmanager
async def as_module(p: Principal, module: str, *, rollback: bool = False) -> AsyncIterator[Ctx]:
    """Giao dịch dưới quyền ``p`` như handler của ``module`` (kernel/files kiểm theo ctx.module)."""
    async with db.tenant_tx(p, rollback=rollback) as conn:
        yield core_ctx(conn, p, module=module)


async def _talk_file(sale: Principal, data: bytes = PNG) -> tuple[dict[str, str], str, UUID]:
    """Tệp của Trao đổi trên hồ sơ của ``sale`` ⇒ (đối tượng, mã tệp đính kèm, mã sổ tệp)."""
    subj = await _lead(sale)
    sent = await _post_file(sale, subj, "anh.png", data)
    att = await owner_val("SELECT id FROM talk.attachments WHERE message_id = %s", (sent["message_id"],))
    fid = await owner_val("SELECT file_id FROM talk.attachments WHERE id = %s", (att,))
    return {**subj, "message_id": sent["message_id"]}, str(att), fid


async def _kb_file(gd: Principal, data: bytes, content_type: str = "application/pdf", name: str = "hd.pdf") -> tuple[str, UUID]:
    doc = str((await ok(gd, "kb.add_document", title=f"Tệp {uuid.uuid4().hex[:6]}", kind="other", body="Nội dung"))["document_id"])
    await ok(gd, "kb.attach_original", document_id=doc, filename=name, content_type=content_type, data_b64=base64.b64encode(data).decode())
    return doc, await owner_val("SELECT file_id FROM kb.files WHERE document_id = %s", (doc,))


def _path(key: str) -> Path:
    s = files.store()
    assert isinstance(s, files.LocalStore)
    return s.root / key


async def _key(fid: UUID) -> str:
    return await owner_val("SELECT object_key FROM core.files WHERE id = %s", (fid,))


# ── mở: quyền kiểm ở DB mỗi lần ──

async def test_knowing_a_file_id_is_not_enough_to_open_it(who: Who) -> None:
    sale1, sale2, other = await who("sale1"), await who("sale2"), await who("admin", other=True)
    _, _, fid = await _talk_file(sale1)
    async with as_module(sale1, "talk") as ctx:
        assert await files.read(ctx, fid) == PNG
    async with as_module(sale2, "talk") as ctx:                       # không thấy hồ sơ (RLS của module) ⇒ biết file_id cũng không mở được
        with pytest.raises(Denied):
            await files.open(ctx, fid)
        with pytest.raises(Denied):
            await files.signed_url(ctx, fid)
    async with as_module(other, "talk") as ctx:                       # công ty khác ⇒ không thấy cả dòng sổ
        with pytest.raises(NotFound):
            await files.open(ctx, fid)
    async with as_module(sale1, "kb") as ctx:                         # cùng công ty, khác module ⇒ từ chối
        with pytest.raises(Denied):
            await files.open(ctx, fid)


async def test_a_failed_transaction_after_put_leaves_no_ledger_row_and_the_orphan_is_swept(who: Who) -> None:
    gd = await who("giamdoc")
    stored, key = None, ""
    with pytest.raises(RuntimeError):
        async with as_module(gd, "kb") as ctx:
            stored = await files.put(ctx, b"%PDF-1.4 mo coi", "mo-coi.pdf", "application/pdf")
            key = await ctx.val("SELECT object_key FROM core.files WHERE id = %s", (stored.id,))
            raise RuntimeError("dòng của module hỏng")
    assert stored is not None and key
    assert await owner_val("SELECT count(*) FROM core.files WHERE id = %s", (stored.id,)) == 0
    assert _path(key).exists()                                         # đối tượng đã lên kho trước dòng sổ
    async with db.owner.connection() as conn:
        await files.delete_orphan_objects(conn)                        # chưa quá thời gian chờ ⇒ chưa xoá
        assert _path(key).exists()
        foreign = f"t/{uuid.uuid4()}/{uuid.uuid4()}"                    # đối tượng của công ty KHÔNG có trong DB này (kho lỡ dùng chung)
        await files.store().write(foreign, files._chunks(b"cua DB khac"))
        await files.delete_orphan_objects(conn, age=timedelta(0))
    assert not _path(key).exists()
    assert _path(foreign).exists()                                     # không bao giờ xoá đối tượng của DB khác
    assert files.orphan_age() >= timedelta(days=14)                    # mặc định dài hơn tuổi bản dump cũ nhất (khôi phục DB cũ không mất tệp)


async def test_signed_links_are_short_carry_safe_headers_and_never_for_active_types(who: Who) -> None:
    gd = await who("giamdoc")
    pdf = b"%PDF-1.4 ban ky"
    _, fid = await _kb_file(gd, pdf, name="Hợp đồng \"A\".pdf")
    _, svg = await _kb_file(gd, b"<svg onload=alert(1)/>", "image/svg+xml", "a.svg")
    _, html = await _kb_file(gd, b"<html><script>", "text/html; charset=utf-8", "a.html")
    async with as_module(gd, "kb") as ctx:
        with pytest.raises(Invalid):
            await files.signed_url(ctx, fid, ttl=61)
        with pytest.raises(Invalid):
            await files.signed_url(ctx, fid, ttl=0)
        url = await files.signed_url(ctx, fid, ttl=60)
        inline = await files.signed_url(ctx, fid, inline=True)
        for active in (svg, html):
            with pytest.raises(Denied):
                await files.signed_url(ctx, active)
            with pytest.raises(Denied):
                await files.signed_url(ctx, active, inline=True)
    qs = parse_qs(urlsplit(url).query)
    assert qs["response-content-disposition"][0].startswith("attachment; filename=")
    assert qs["response-content-type"] == ["application/pdf"] and int(qs["expires"][0]) <= files.time.time() + 60
    assert parse_qs(urlsplit(inline).query)["response-content-disposition"][0].startswith("inline;")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        got = await c.get(urlsplit(url).path, params={k: v[0] for k, v in qs.items()})
        assert got.status_code == 200 and got.content == pdf
        assert got.headers["content-disposition"] == qs["response-content-disposition"][0] and got.headers["x-content-type-options"] == "nosniff"
        forged = {k: v[0] for k, v in qs.items()} | {"response-content-disposition": "inline"}
        assert (await c.get(urlsplit(url).path, params=forged)).status_code == 404     # tiêu đề nằm trong phần đã ký
        expired = parse_qs(urlsplit(files.store().signed_url(await _key(fid), -1, content_type="application/pdf",
                                                             disposition="attachment")).query)
        assert (await c.get(urlsplit(url).path, params={k: v[0] for k, v in expired.items()})).status_code == 404


async def test_release_keeps_the_object_and_the_purge_honours_retention_and_legal_hold(who: Who) -> None:
    gd = await who("giamdoc")
    doc, first = await _kb_file(gd, b"%PDF-1.4 ban 1")
    await ok(gd, "kb.attach_original", document_id=doc, filename="hd2.pdf", content_type="application/pdf",
             data_b64=base64.b64encode(b"%PDF-1.4 ban 2").decode())                        # thay bản ⇒ bản cũ release, không xoá
    assert await owner_val("SELECT released_at IS NOT NULL FROM core.files WHERE id = %s", (first,))
    assert _path(await _key(first)).exists()
    assert base64.b64decode((await q(gd, "kb.original", document_id=doc))["data_b64"]) == b"%PDF-1.4 ban 2"
    _, held = await _kb_file(gd, b"%PDF-1.4 giu phap ly")
    _, recent = await _kb_file(gd, b"%PDF-1.4 moi release")
    for fid in (held, recent):
        await owner_val("DELETE FROM kb.files WHERE file_id = %s RETURNING 1", (fid,))
    await owner_val("UPDATE core.files SET released_at = now() - interval '31 days' WHERE id = ANY(%s) RETURNING 1", ([first, held],))
    await owner_val("UPDATE core.files SET released_at = now() - interval '10 days' WHERE id = %s RETURNING 1", (recent,))
    await owner_val("UPDATE core.files SET legal_hold = true WHERE id = %s RETURNING 1", (held,))
    keys = {f: await _key(f) for f in (first, held, recent)}
    async with db.owner_tx() as conn:                                   # công ty chưa đặt thời hạn ⇒ giữ mãi
        assert first not in {k for k in await files.purge_expired(conn)}
    assert await owner_val("SELECT count(*) FROM core.files WHERE id = %s", (first,)) == 1
    await owner_val("""INSERT INTO core.tenant_profile (tenant_id, file_retention_days) VALUES (%s, 30)
                       ON CONFLICT (tenant_id) DO UPDATE SET file_retention_days = 30 RETURNING 1""", (gd.tenant_id,))
    try:
        async with db.owner_tx() as conn:
            gone = await files.purge_expired(conn)
        for k in gone:
            await files.store().delete(k)
    finally:
        await owner_val("UPDATE core.tenant_profile SET file_retention_days = NULL WHERE tenant_id = %s RETURNING 1", (gd.tenant_id,))
    assert keys[first] in gone and keys[held] not in gone and keys[recent] not in gone
    assert not _path(keys[first]).exists() and _path(keys[held]).exists() and _path(keys[recent]).exists()
    assert await owner_val("SELECT count(*) FROM core.files WHERE id = ANY(%s)", ([held, recent],)) == 2


async def test_restore_check_reports_missing_objects_and_wrong_hashes(who: Who) -> None:
    gd = await who("giamdoc")
    _, good = await _kb_file(gd, b"%PDF-1.4 dung")
    _, lost = await _kb_file(gd, b"%PDF-1.4 mat")
    _, bent = await _kb_file(gd, b"%PDF-1.4 lech")
    _path(await _key(lost)).unlink()
    _path(await _key(bent)).write_bytes(b"%PDF-1.4 LECH")                                 # cùng cỡ, khác nội dung
    async with db.owner.connection() as conn:
        found = {p["file_id"]: p["problem"] for p in await files.verify(conn)}
    assert found.get(str(lost)) == "missing" and found.get(str(bent)) == "sha256" and str(good) not in found


async def test_tampered_object_changes_the_digest_and_open_reports_it(who: Who, caplog: pytest.LogCaptureFixture) -> None:
    gd = await who("giamdoc")
    doc, fid = await _kb_file(gd, b"%PDF-1.4 bang chung")
    sp = system_principal(gd.tenant_id)
    async with as_module(sp, "kb") as ctx:
        before = await kb.file_digest(ctx, kb.DocRef(document_id=UUID(doc)))
    assert before == hashlib.sha256(b"%PDF-1.4 bang chung").hexdigest()
    _path(await _key(fid)).write_bytes(b"%PDF-1.4 bang CHUNG")
    async with as_module(sp, "kb") as ctx:
        after = await kb.file_digest(ctx, kb.DocRef(document_id=UUID(doc)))
    assert after == hashlib.sha256(b"%PDF-1.4 bang CHUNG").hexdigest() != before          # dấu đọc lại từ kho, không phải cột lưu sẵn
    assert await owner_val("SELECT sha256 FROM core.files WHERE id = %s", (fid,)) == before
    await owner_val("UPDATE core.files SET integrity_failed_at = NULL WHERE id = %s RETURNING 1", (fid,))
    with caplog.at_level(logging.ERROR, logger="barista.files"):
        assert (await executor.query(gd, "kb.original", {"document_id": doc})).ok           # đọc qua server: lệch chỉ biết khi đã truyền xong…
    assert any("sha256_mismatch" in r.getMessage() for r in caplog.records)               # …nên ghi lại để điều tra
    assert await owner_val("SELECT integrity_failed_at IS NOT NULL FROM core.files WHERE id = %s", (fid,))


def _stream(total: int, seen: list[int]) -> Iterator[bytes]:
    for _ in range(total // files.CHUNK):
        seen.append(1)
        yield bytes(files.CHUNK)


async def test_put_streams_stops_early_when_too_big_and_memory_does_not_grow_with_size(who: Who) -> None:
    gd = await who("giamdoc")
    tdir = _path(f"t/{gd.tenant_id}/{uuid.uuid4()}").parent
    before = set(tdir.iterdir()) if tdir.exists() else set()
    seen: list[int] = []
    async with as_module(gd, "kb", rollback=True) as ctx:
        with pytest.raises(Invalid):
            await files.put(ctx, _stream(10 * MB, seen), "lon.bin", "application/octet-stream", max_bytes=1 * MB)
    assert len(seen) <= MB // files.CHUNK + 1                                             # dừng ngay khi vượt, không đọc hết 10 MB
    assert set(tdir.iterdir()) == before                                                  # không để lại đối tượng / bản ghi dở
    tracemalloc.start()
    try:
        async with as_module(gd, "kb", rollback=True) as ctx:
            stored = await files.put(ctx, _stream(25 * MB, []), "lon.bin", "application/octet-stream")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert stored.size == 25 * MB and peak < 2 * MB, peak


# ── sao tệp giữa module ──

async def test_copy_needs_a_one_time_grant_for_this_module_and_person(who: Who) -> None:
    sale1, sale2, gd = await who("sale1"), await who("sale2"), await who("giamdoc")
    _, _, fid = await _talk_file(sale1)
    async with as_module(sale2, "talk") as ctx:                       # không qua được RLS của module gốc ⇒ không xin được quyền sao
        with pytest.raises(Denied):
            await files.grant_copy(ctx, fid, "kb")
    async with as_module(gd, "kb", rollback=True) as ctx:             # không có quyền sao
        with pytest.raises(Denied):
            await files.copy(ctx, fid, uuid.uuid4())
    async with as_module(gd, "talk") as ctx:
        for_x = await files.grant_copy(ctx, fid, "mkt")
        once = await files.grant_copy(ctx, fid, "kb")
        late = await files.grant_copy(ctx, fid, "kb")
        someone_else = await files.grant_copy(ctx, fid, "kb", granted_to=sale1.user_id)
    async with as_module(gd, "kb", rollback=True) as ctx:             # cấp cho module X, module Y dùng ⇒ từ chối
        with pytest.raises(Denied):
            await files.copy(ctx, fid, for_x)
    async with as_module(gd, "kb", rollback=True) as ctx:             # cấp cho người khác
        with pytest.raises(Denied):
            await files.copy(ctx, fid, someone_else)
    await owner_val("UPDATE core.file_copy_grants SET created_at = created_at - interval '1 hour', "
                    "expires_at = created_at - interval '1 hour' + interval '1 second' WHERE id = %s RETURNING 1", (late,))
    async with as_module(gd, "kb", rollback=True) as ctx:             # hết hạn
        with pytest.raises(Denied):
            await files.copy(ctx, fid, late)
    async with as_module(gd, "kb") as ctx:
        first = await files.copy(ctx, fid, once)
    async with as_module(gd, "kb", rollback=True) as ctx:             # dùng lần hai
        with pytest.raises(Denied):
            await files.copy(ctx, fid, once)
    assert await owner_val("SELECT copy_id FROM core.file_copy_grants WHERE id = %s", (once,)) == first.id
    assert await owner_val("SELECT count(*) FROM core.file_copy_grants WHERE used_at IS NOT NULL AND id = ANY(%s)",
                           ([for_x, late, someone_else],)) == 0           # thử sai không tiêu quyền


async def test_a_copy_lives_on_its_own_and_each_module_opens_only_its_own(who: Who) -> None:
    sale1, gd = await who("sale1"), await who("giamdoc")
    subj, _, fid = await _talk_file(sale1)
    doc = str((await ok(gd, "kb.add_document", title=f"Lưu từ trao đổi {uuid.uuid4().hex[:6]}", kind="other", body="Ảnh"))["document_id"])
    async with as_module(gd, "talk") as talk_ctx:                     # cấp và dùng trong CÙNG giao dịch
        grant = await files.grant_copy(talk_ctx, fid, "kb")
        kb_ctx = core_ctx(talk_ctx.conn, gd, module="kb")
        copied = await files.copy(kb_ctx, fid, grant)
        await kb_ctx.exec("INSERT INTO kb.files (document_id, filename, content_type, size_bytes, file_id) VALUES (%s, 'anh.png', 'image/png', %s, %s)",
                          (doc, copied.size, copied.id))
    row = await owner_val("SELECT row_to_json(f) FROM core.files f WHERE id = %s", (copied.id,))
    src = await owner_val("SELECT row_to_json(f) FROM core.files f WHERE id = %s", (fid,))
    assert (row["module"], row["copied_from"], row["sha256"], row["size_bytes"]) == ("kb", str(fid), src["sha256"], src["size_bytes"])
    assert row["object_key"] != src["object_key"]
    async with as_module(gd, "kb") as ctx:
        assert await files.read(ctx, copied.id) == PNG
        with pytest.raises(Denied):
            await files.open(ctx, fid)                                 # module đích không mở bản gốc
    async with as_module(gd, "talk") as ctx:
        with pytest.raises(Denied):
            await files.open(ctx, copied.id)                           # module gốc không mở bản sao
        assert await files.release(ctx, fid)
    await owner_val("DELETE FROM talk.messages WHERE id = %s RETURNING 1", (subj["message_id"],))
    _path(src["object_key"]).unlink()                                  # bản gốc mất hẳn khỏi kho…
    assert base64.b64decode((await q(gd, "kb.original", document_id=doc))["data_b64"]) == PNG   # …bản sao vẫn đọc được


async def test_copying_does_not_pull_the_file_through_the_app(who: Who) -> None:
    sale1, gd = await who("sale1"), await who("giamdoc")
    big = PNG + b"\2" * (talk.FILE_MAX - len(PNG) - 1024)
    _, _, fid = await _talk_file(sale1, big)
    async with as_module(gd, "talk", rollback=True) as talk_ctx:
        grant = await files.grant_copy(talk_ctx, fid, "kb")
        tracemalloc.start()
        try:
            copied = await files.copy(core_ctx(talk_ctx.conn, gd, module="kb"), fid, grant)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        key = await talk_ctx.val("SELECT object_key FROM core.files WHERE id = %s", (copied.id,))
    assert copied.size == len(big) and peak < 1 * MB, peak
    assert _path(key).read_bytes() == big


# ── vòng đời theo dòng của module ──

async def test_cascade_deleted_rows_are_released_by_the_sweep_but_recall_is_not(who: Who) -> None:
    sale1, truonga = await who("sale1"), await who("truonga")
    subj, att, fid = await _talk_file(sale1)
    await ok(sale1, "talk.recall_message", message_id=subj["message_id"])
    async with db.owner_tx() as conn:
        await files.release_orphan_rows(conn)
    assert await owner_val("SELECT released_at FROM core.files WHERE id = %s", (fid,)) is None   # thu hồi tin KHÔNG release
    assert base64.b64decode((await q(sale1, "talk.file", attachment_id=att))["data_b64"]) == PNG   # người gửi vẫn mở được
    assert (await executor.query(truonga, "talk.file", {"attachment_id": att})).status == "not_found"
    async with as_module(truonga, "talk") as ctx:                     # kernel cũng chặn: dòng module không còn thấy được
        with pytest.raises(Denied):
            await files.open(ctx, fid)
    await owner_val("DELETE FROM talk.messages WHERE id = %s RETURNING 1", (subj["message_id"],))   # xoá theo dây chuyền
    assert await owner_val("SELECT count(*) FROM talk.attachments WHERE id = %s", (att,)) == 0
    async with db.owner_tx() as conn:
        assert await files.release_orphan_rows(conn) >= 1
    assert await owner_val("SELECT released_at IS NOT NULL FROM core.files WHERE id = %s", (fid,))
    assert _path(await _key(fid)).exists()                             # release không xoá đối tượng


async def test_sweep_once_runs_end_to_end(who: Who) -> None:
    out = await files.sweep_once()
    assert set(out) == {"released", "purged", "orphan_objects"}


def test_r2_switch_is_explicit() -> None:
    from barista.config import settings
    old, kind = files._store, settings.files_store
    try:
        files.use_store(None)
        settings.files_store = "khong-co"
        with pytest.raises(RuntimeError, match="chưa có bộ nối"):
            files.store()
        settings.files_store = "r2"
        if not settings.r2_secret_access_key:
            with pytest.raises(RuntimeError, match="BARISTA_R2_ACCESS_KEY_ID"):   # thiếu khoá ⇒ báo rõ, không lặng lẽ dùng thư mục
                files.store()
    finally:
        settings.files_store = kind
        files.use_store(old)


async def test_store_and_api_refuse_to_run_without_explicit_config() -> None:
    """Review 08/11: không có thư mục kho mặc định (container --rm thiếu volume ⇒ mất tệp); API thiếu khoá ký link kho thư mục ⇒ không khởi động."""
    from barista.config import settings
    old, d, secret = files._store, settings.files_dir, settings.files_sign_secret
    try:
        files.use_store(None)
        settings.files_dir = ""
        with pytest.raises(RuntimeError, match="BARISTA_FILES_DIR"):
            files.store()
        files.use_store(None)
        kind, settings.files_store = settings.files_store, ""                # biến đặt mà rỗng (stack quên chọn) ⇒ lỗi, không lặng lẽ chạy local
        with pytest.raises(RuntimeError, match="BARISTA_FILES_STORE rỗng"):
            files.store()
        settings.files_store = kind
        settings.files_dir = d
        files.use_store(None)
        settings.files_sign_secret = ""
        with pytest.raises(RuntimeError, match="BARISTA_FILES_SIGN_SECRET"):
            await files.check_config()
        settings.files_sign_secret = "khoa-thu"
        await files.check_config()
    finally:
        settings.files_dir, settings.files_sign_secret = d, secret
        files.use_store(old)


def test_fit_name_keeps_the_extension() -> None:
    assert files.fit_name("  a.pdf ") == "a.pdf" and files.fit_name("") == "tep"
    long = files.fit_name("x" * 500 + ".docx")
    assert len(long) == files.NAME_MAX and long.endswith(".docx")
    assert len(files.fit_name("y" * 500)) == files.NAME_MAX


def test_disposition_is_sanitised() -> None:
    d = files.disposition("attachment", '../../Hợp đồng "A"\r\n.pdf')
    assert d.startswith('attachment; filename="') and "\r" not in d and "\n" not in d and "../" not in d and d.count('"') == 2
    assert "%22" not in d and d.endswith("A.pdf")
