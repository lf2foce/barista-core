"""Bộ nối R2 (0122) trên bucket THỬ thật — chỉ chạy khi có tệp khoá ``~/.config/barista/r2.env`` (hoặc ``BARISTA_R2_TEST_ENV``); không có ⇒ bỏ qua.
Mọi khoá dưới tiền tố riêng ``t/test-<uuid>/`` và bị xoá ở cuối. Không in giá trị khoá ra đâu cả (thông báo lỗi tự viết, không kèm URL)."""

import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from barista.kernel import files

ENV = Path(os.environ.get("BARISTA_R2_TEST_ENV", "~/.config/barista/r2.env")).expanduser()
pytestmark = pytest.mark.skipif(not ENV.is_file(), reason="không có khoá bucket R2 thử")


def _store() -> files.R2Store:
    env: dict[str, str] = {}
    for line in ENV.read_text().splitlines():
        k, sep, v = line.strip().partition("=")
        if sep and not k.startswith("#"):
            env[k.removeprefix("export ").strip()] = v.strip().strip("'\"")
    return files.R2Store(account_id=env["R2_ACCOUNT_ID"], access_key_id=env["R2_ACCESS_KEY_ID"],
                         secret_access_key=env["R2_SECRET_ACCESS_KEY"], bucket=env["R2_BUCKET_FILES"],
                         part=5 * 1024 * 1024)   # phần nhỏ nhất R2 nhận — vẫn đi đường nhiều phần, ít byte phải đẩy qua mạng


async def _chunks(data: bytes) -> AsyncIterator[bytes]:
    for i in range(0, len(data), files.CHUNK):
        yield data[i:i + files.CHUNK]


async def test_r2_put_read_copy_sign_list_delete() -> None:
    s = _store()
    prefix = f"t/test-{uuid.uuid4()}/"
    small, big = prefix + str(uuid.uuid4()), prefix + str(uuid.uuid4())
    copied, aborted = prefix + str(uuid.uuid4()), prefix + str(uuid.uuid4())
    small_body = b"%PDF-1.4 thu R2 " + os.urandom(1000)
    big_body = os.urandom(s.part + 12345)                          # hai phần: một phần đủ cỡ + phần cuối
    try:
        await s.write(small, _chunks(small_body))
        await s.write(big, _chunks(big_body))
        for key, body in ((small, small_body), (big, big_body)):
            got = hashlib.sha256()
            async for c in s.read(key):
                got.update(c)
            assert got.hexdigest() == hashlib.sha256(body).hexdigest(), "đọc lại lệch sha256"
            assert await s.size(key) == len(body), "cỡ lệch"
        await s.copy(small, copied)                                   # CopyObject phía kho
        assert b"".join([c async for c in s.read(copied)]) == small_body, "bản sao lệch"

        async def too_big() -> AsyncIterator[bytes]:
            async for c in _chunks(os.urandom(s.part + files.CHUNK)):  # vừa đủ để mở lần tải nhiều phần rồi hỏng
                yield c
            raise files.TooBig
        with pytest.raises(files.TooBig):
            await s.write(aborted, too_big())                          # tải nhiều phần bị huỷ ⇒ không để lại đối tượng
        assert await s.size(aborted) is None, "lần tải hỏng để lại đối tượng"

        disp = files.disposition("attachment", "Hợp đồng thử.pdf")
        url = s.signed_url(small, 60, content_type="application/pdf", disposition=disp)
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(url)
            assert r.status_code == 200 and r.content == small_body, f"link ký trả {r.status_code}"
            assert r.headers.get("content-disposition") == disp and r.headers.get("content-type") == "application/pdf", "tiêu đề không theo link ký"
            assert (await c.get(url.split("?", 1)[0])).status_code in (400, 401, 403), "đọc không ký phải bị từ chối"
            bad = url.replace("response-content-disposition=attachment", "response-content-disposition=inline")
            assert (await c.get(bad)).status_code == 403, "sửa tiêu đề đã ký phải bị từ chối"

        listed = [k async for k in s.keys(datetime.now(UTC) + timedelta(minutes=5), prefix=prefix)]
        assert set(listed) == {small, big, copied}, "liệt kê lệch"
        assert [k async for k in s.keys(datetime.now(UTC) - timedelta(hours=1), prefix=prefix)] == []   # mới tạo ⇒ chưa "cũ"
        with pytest.raises(FileNotFoundError):
            [c async for c in s.read(prefix + str(uuid.uuid4()))]
        backup = f"backups/test-{uuid.uuid4()}.dump"                   # khoá sao lưu (ngoài sổ tệp) — thử trên bucket TỆP thử, không khoá bucket
        try:
            await s.write(backup, _chunks(b"pg_dump thu"))
            assert await s.size(backup) == len(b"pg_dump thu"), "không đẩy được bản sao lưu"
        finally:
            await s.delete(backup)
        with pytest.raises(ValueError):
            await s.write("backups/../t/x", _chunks(b"x"))              # khoá lạ bị chặn trước khi gọi kho
    finally:
        for key in (small, big, copied, aborted):
            await s.delete(key)
        assert [k async for k in s.keys(datetime.now(UTC) + timedelta(minutes=5), prefix=prefix)] == [], "còn sót khoá thử"
        await s.client.aclose()


async def test_r2_bad_keys_fail_at_boot_without_leaking_them() -> None:
    good = _store()
    try:
        await good.ping()                                               # khoá đúng ⇒ qua
    finally:
        await good.client.aclose()
    secret = "khong-phai-khoa-" + uuid.uuid4().hex
    bad = files.R2Store(account_id=good.host.split(".", 1)[0], access_key_id=good._key_id, secret_access_key=secret, bucket=good.bucket)
    try:
        with pytest.raises(RuntimeError, match="HTTP 403") as e:
            await bad.ping()
        assert secret not in str(e.value) and good._key_id not in str(e.value), "lời lỗi lộ khoá"
    finally:
        await bad.client.aclose()
