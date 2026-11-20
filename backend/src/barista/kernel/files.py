"""TỆP (quyết định 0122): thân tệp nằm ở KHO ĐỐI TƯỢNG, DB chỉ giữ tham chiếu; quyền kiểm ở DB mỗi lần mở.

* **Một giao diện, nhiều nơi chứa.** Module chỉ gọi ``put`` / ``open`` / ``read`` / ``digest`` / ``signed_url`` / ``release`` / ``grant_copy`` /
  ``copy`` — không biết SDK, không biết khoá đối tượng. Nơi chứa chọn bằng cấu hình ``BARISTA_FILES_STORE``: ``local`` = thư mục
  (``BARISTA_FILES_DIR``; dev, test, bản demo trên một máy); ``r2`` = Cloudflare R2 qua API tương thích S3 (``BARISTA_R2_*``). Nơi chứa khác =
  một lớp ``Store`` mới, không đổi gì ở module.
* **Một sổ ở lõi** (``core.files``, RLS theo công ty): mỗi đối tượng một dòng — khoá ``t/<công ty>/<uuid ngẫu nhiên>`` (không khoá theo băm: xoá
  không phải đếm nơi dùng), tên, loại, cỡ, sha256 lúc ghi, module đã tạo. Bảng của module trỏ vào sổ bằng ``file_id … REFERENCES core.files`` KHÔNG
  ``ON DELETE CASCADE`` — khoá ngoại là cách kernel biết bảng nào đang dùng tệp (``pg_constraint``) mà không phải biết tên module.
* **Thứ tự ghi:** đẩy đối tượng lên kho TRƯỚC (băm + đếm cỡ trong lúc đẩy, vượt cỡ thì dừng ngay), rồi mới thêm dòng sổ trong CÙNG giao dịch với
  dòng của module ⇒ không bao giờ có dòng trỏ vào tệp không tồn tại. Giao dịch hỏng ⇒ đối tượng mồ côi, vòng quét xoá sau thời gian chờ (``orphan_age``).
* **Mở = kiểm hai lớp:** chỉ module đã tạo tệp mở được (``core.files.module = ctx.module``), VÀ phải còn một dòng của chính module đó trỏ vào tệp mà
  người gọi thấy được dưới RLS — biết ``file_id`` không đủ để mở. Mở luôn băm lại trong lúc đọc; lệch dấu ⇒ ghi lỗi + báo nhà vận hành.
* **Thu hồi khác xoá:** ``release`` chỉ đánh dấu (dòng module trỏ tới tệp mất hẳn: thay bản, xoá mục); xoá thật do vòng dọn theo thời hạn lưu của
  công ty (``core.tenant_profile.file_retention_days``; trống = giữ mãi), không bao giờ khi ``legal_hold``.
* **Sao giữa module** chỉ qua ``grant_copy`` (module sở hữu, sau khi kiểm quyền người xem) + ``copy`` (module đích, một lần, cùng người, cùng giao
  dịch): sao đối tượng ở phía kho, không đọc thân tệp qua ứng dụng; bản sao sống độc lập và mang ``copied_from``.
"""

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import shutil
import time
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any
from urllib.parse import quote, urlencode
from uuid import UUID, uuid4

import httpx
from psycopg import AsyncConnection, sql
from psycopg.rows import dict_row

from barista.config import settings
from barista.kernel import alerts
from barista.kernel.errors import Denied, Invalid, NotFound
from barista.kernel.executor import Ctx

log = logging.getLogger("barista.files")

MB = 1024 * 1024
CHUNK = 64 * 1024
SIGNED_TTL_MAX = 60   # giây — link ký đi thẳng tới kho, bỏ qua kiểm quyền mỗi lần mở ⇒ rất ngắn, cấp ngay sau khi kiểm
GRANT_TTL = 60        # giây — quyền sao tệp sang module khác (dự phòng: cấp và dùng nằm trong cùng giao dịch của lệnh)
NAME_MAX = 300
BACKUP_RE = re.compile(r"^backups/[A-Za-z0-9._-]{1,200}$")


def fit_name(name: str, limit: int = NAME_MAX) -> str:
    """Tên cho sổ: bỏ khoảng trắng hai đầu, dài quá ``limit`` thì cắt phần thân, GIỮ đuôi tệp (tên rỗng ⇒ "tep")."""
    name = name.strip() or "tep"
    if len(name) <= limit:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem or len(ext) > 16:
        return name[:limit]
    return stem[:limit - len(ext) - 1].rstrip() + "." + ext


def orphan_age() -> timedelta:
    """Đối tượng không có dòng sổ chỉ bị xoá khi cũ hơn mức này (cấu hình bản cài, mặc định 30 ngày). Dài hơn tuổi của bản dump cũ nhất còn
    khôi phục được ⇒ khôi phục DB về bản cũ không làm vòng dọn xoá mất tệp ghi SAU bản dump đó (R2 không có versioning — không lấy lại được)."""
    return timedelta(days=settings.files_orphan_grace_days)
# t/<công ty>/<uuid>; phần giữa nhận cả tiền tố thử "test-<uuid>" (bài kiểm R2 thật — không bao giờ trùng khoá của công ty nào)
KEY_RE = re.compile(r"^t/[0-9a-z-]{1,64}/[0-9a-f-]{36}(\.part-[0-9a-f]{32})?$")
# loại được mở NGAY trong trình duyệt qua link ký; mọi loại khác tải về (attachment)
INLINE_OK = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"})
# loại trình duyệt có thể CHẠY (script trong html / svg / xml…) ⇒ không bao giờ cấp link ký, chỉ đọc qua server dưới dạng attachment
ACTIVE = frozenset({"text/html", "application/xhtml+xml", "image/svg+xml", "text/xml", "application/xml", "text/xsl", "application/xslt+xml",
                    "text/javascript", "application/javascript", "application/ecmascript", "text/ecmascript", "application/x-shockwave-flash",
                    "multipart/x-mixed-replace"})


class TooBig(Exception):
    pass


# ── Nơi chứa ──

class Store(ABC):
    """Giao diện một nơi chứa đối tượng. Khoá do kernel đặt (``t/<công ty>/<uuid>``); nơi chứa chỉ cất / đọc / sao / xoá / liệt kê."""

    kind: str

    @abstractmethod
    async def write(self, key: str, chunks: AsyncIterator[bytes | memoryview]) -> None:
        """Đẩy từng khúc; ``chunks`` ném lỗi giữa chừng (vượt cỡ) ⇒ không để lại đối tượng dở."""

    @abstractmethod
    def read(self, key: str) -> AsyncIterator[bytes]:
        """Đọc từng khúc. Không có ⇒ ``FileNotFoundError``."""

    @abstractmethod
    async def copy(self, src: str, dst: str) -> None:
        """Sao ở PHÍA KHO (R2 ``CopyObject``; thư mục: sao tệp bằng hệ điều hành) — không kéo thân tệp qua bộ nhớ ứng dụng."""

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def size(self, key: str) -> int | None: ...

    @abstractmethod
    def keys(self, older_than: datetime, prefix: str = "t/") -> AsyncIterator[str]:
        """Mọi khoá (kể cả bản đang ghi dở) cũ hơn mốc — cho vòng quét đối tượng mồ côi."""

    @abstractmethod
    def signed_url(self, key: str, ttl: int, *, content_type: str, disposition: str) -> str:
        """Link có hạn ``ttl`` giây, KÝ KÈM ``response-content-disposition`` + ``response-content-type`` (tiêu đề an toàn đi theo link)."""


def _check_key(key: str) -> str:
    if not KEY_RE.fullmatch(key):
        raise ValueError(f"khoá đối tượng không hợp lệ: {key!r}")
    return key


class LocalStore(Store):
    """Thư mục cục bộ — dev, test (mỗi tiến trình test một thư mục tạm), bản cài một máy (thư mục phải là volume bền — runbook 11).
    MỘT thư mục cho MỘT database: vòng quét xoá mọi đối tượng không có dòng sổ trong DB của nó.

    Link ký: không có máy chủ kho riêng ⇒ link là đường ``/v1/files/signed`` của chính core, ký HMAC (khoá, hạn, hai tiêu đề) — cùng hình dạng
    và cùng luật với link ký của R2 (hạn ≤ 60 giây, tiêu đề đi theo link). Route chỉ kiểm chữ ký rồi đọc đối tượng, không chạm DB."""

    kind = "local"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()

    def _path(self, key: str) -> Path:
        return self.root / _check_key(key)

    async def write(self, key: str, chunks: AsyncIterator[bytes | memoryview]) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(f"{path.name}.part-{uuid4().hex}")
        try:
            with part.open("xb") as f:
                async for c in chunks:
                    f.write(c)
            os.replace(part, path)
        except BaseException:
            part.unlink(missing_ok=True)
            raise

    async def read(self, key: str) -> AsyncIterator[bytes]:   # type: ignore[override]
        with self._path(key).open("rb") as f:
            while c := f.read(CHUNK):
                yield c

    async def copy(self, src: str, dst: str) -> None:
        s, d = self._path(src), self._path(dst)
        d.parent.mkdir(parents=True, exist_ok=True)
        part = d.with_name(f"{d.name}.part-{uuid4().hex}")
        try:
            await asyncio.to_thread(shutil.copyfile, s, part)   # macOS fcopyfile / Linux sendfile: không qua bộ đệm Python
            os.replace(part, d)
        except BaseException:
            part.unlink(missing_ok=True)
            raise

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    async def size(self, key: str) -> int | None:
        try:
            return self._path(key).stat().st_size
        except FileNotFoundError:
            return None

    async def keys(self, older_than: datetime, prefix: str = "t/") -> AsyncIterator[str]:   # type: ignore[override]
        base = self.root / "t"
        if not base.is_dir():
            return
        cutoff = older_than.timestamp()
        for tdir in sorted(base.iterdir()):
            if not tdir.is_dir():
                continue
            for f in sorted(tdir.iterdir()):
                key = f"t/{tdir.name}/{f.name}"
                if key.startswith(prefix) and KEY_RE.fullmatch(key) and f.is_file() and f.stat().st_mtime < cutoff:
                    yield key

    def signed_url(self, key: str, ttl: int, *, content_type: str, disposition: str) -> str:
        params = {"key": _check_key(key), "expires": str(int(time.time()) + ttl),
                  "response-content-type": content_type, "response-content-disposition": disposition}
        return f"{settings.public_api_url.rstrip('/')}/v1/files/signed?{urlencode({**params, 'signature': _sign(params)})}"


_EMPTY_SHA = hashlib.sha256(b"").hexdigest()
_S3 = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def _q(s: str, safe: str = "-_.~") -> str:
    return quote(s, safe=safe)


class R2Store(Store):
    """Cloudflare R2 qua API tương thích S3, ký SigV4 (vùng "auto"). Viết tay trên httpx (đã là phụ thuộc) thay vì boto3 / aioboto3: chỉ cần sáu thao
    tác (PUT, GET, HEAD, DELETE, CopyObject, ListObjectsV2) + tải nhiều phần + link ký; giữ được luồng bất đồng bộ và trần bộ nhớ (một phần
    ``part`` byte) mà không kéo botocore (~80 MB, đồng bộ). Đã đo trên bucket thử thật (``tests/test_files_r2.py``).

    Tải lên: tệp ≤ ``part`` ⇒ một PUT; lớn hơn ⇒ tải nhiều phần, các phần cùng cỡ ``part`` (R2 đòi mọi phần trừ phần cuối cùng cỡ), hỏng giữa chừng
    ⇒ huỷ lần tải (không để lại đối tượng). Không bật versioning / object lock (R2 không có — đo 08/11/2026): khoá đối tượng là uuid ngẫu nhiên,
    ghi một lần, không bao giờ ghi đè; chỉ vòng dọn theo thời hạn lưu xoá."""

    kind = "r2"
    RETRIES = 4   # PUT / GET / HEAD / DELETE / CopyObject / phần của lần tải nhiều phần đều chạy lại được; tạo lần tải lặp ⇒ phần dở, luật vòng đời dọn

    def __init__(self, *, account_id: str, access_key_id: str, secret_access_key: str, bucket: str, endpoint: str = "",
                 part: int = 8 * MB) -> None:
        if not (access_key_id and secret_access_key and bucket and (endpoint or account_id)):
            raise RuntimeError("BARISTA_FILES_STORE=r2 cần BARISTA_R2_ACCOUNT_ID (hoặc BARISTA_R2_ENDPOINT), BARISTA_R2_ACCESS_KEY_ID, "
                               "BARISTA_R2_SECRET_ACCESS_KEY, BARISTA_R2_BUCKET")
        self.endpoint = (endpoint or f"https://{account_id}.r2.cloudflarestorage.com").rstrip("/")
        self.host = self.endpoint.split("://", 1)[1]
        self.bucket, self._key_id, self._secret = bucket, access_key_id, secret_access_key
        self._client: httpx.AsyncClient | None = None
        self.part = max(part, 5 * MB)   # R2: mọi phần trừ phần cuối ≥ 5 MiB và cùng cỡ

    def __repr__(self) -> str:   # không bao giờ in khoá
        return f"R2Store(bucket={self.bucket!r})"

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
        return self._client

    def _path(self, key: str) -> str:
        if not BACKUP_RE.fullmatch(key):   # bản sao lưu (pg_dump…) ở bucket sao lưu — ngoài sổ tệp
            _check_key(key)
        return f"/{_q(self.bucket)}/{_q(key, safe='-_.~/')}"

    def _signing_key(self, day: str) -> bytes:
        k = hmac.new(f"AWS4{self._secret}".encode(), day.encode(), hashlib.sha256).digest()
        for part in ("auto", "s3", "aws4_request"):
            k = hmac.new(k, part.encode(), hashlib.sha256).digest()
        return k

    def _signature(self, method: str, path: str, query: dict[str, str], headers: dict[str, str], payload: str, stamp: str) -> tuple[str, str]:
        day = stamp[:8]
        canon_q = "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted(query.items()))
        names = sorted(h.lower() for h in headers)
        canon_h = "".join(f"{n}:{str(headers[next(h for h in headers if h.lower() == n)]).strip()}\n" for n in names)
        signed = ";".join(names)
        req = "\n".join([method, path, canon_q, canon_h, signed, payload])
        scope = f"{day}/auto/s3/aws4_request"
        to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(req.encode()).hexdigest()])
        return hmac.new(self._signing_key(day), to_sign.encode(), hashlib.sha256).hexdigest(), signed

    def _headers(self, method: str, path: str, query: dict[str, str], body: bytes | None = None,
                 extra: dict[str, str] | None = None) -> dict[str, str]:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        payload = hashlib.sha256(body).hexdigest() if body else _EMPTY_SHA
        headers = {"host": self.host, "x-amz-content-sha256": payload, "x-amz-date": stamp, **(extra or {})}
        sig, signed = self._signature(method, path, query, headers, payload, stamp)
        headers["authorization"] = (f"AWS4-HMAC-SHA256 Credential={self._key_id}/{stamp[:8]}/auto/s3/aws4_request, "
                                    f"SignedHeaders={signed}, Signature={sig}")
        del headers["host"]
        return headers

    async def _call(self, method: str, key: str | None, query: dict[str, str] | None = None, body: bytes | None = None,
                    extra: dict[str, str] | None = None, ok: tuple[int, ...] = (200,)) -> httpx.Response:
        path = self._path(key) if key is not None else f"/{_q(self.bucket)}"
        query = query or {}
        r: httpx.Response | None = None
        for attempt in range(self.RETRIES):
            try:   # mạng chập chờn (đo 08/11: ReadError giữa lúc đẩy phần 8 MB) ⇒ thử lại cùng yêu cầu, ký lại (giờ ký mới); 5xx cũng thử lại
                r = await self.client.request(method, self.endpoint + path, params=query or None, content=body,
                                              headers=self._headers(method, path, query, body, extra))
            except httpx.TransportError:
                if attempt == self.RETRIES - 1:
                    raise
                await asyncio.sleep(0.5 * 2 ** attempt)
                continue
            if r.status_code < 500 or attempt == self.RETRIES - 1:
                break
            await asyncio.sleep(0.5 * 2 ** attempt)
        assert r is not None
        if r.status_code == 404 and 404 not in ok:
            raise FileNotFoundError(key or self.bucket)
        if r.status_code not in ok:
            raise RuntimeError(f"R2 {method} trả {r.status_code}: {r.text[:200]}")
        return r

    async def write(self, key: str, chunks: AsyncIterator[bytes | memoryview]) -> None:
        buf = bytearray()
        upload: str | None = None
        etags: list[str] = []
        try:
            async for c in chunks:
                buf += c
                while len(buf) >= self.part and (upload is not None or len(buf) > self.part):
                    if upload is None:
                        r = await self._call("POST", key, {"uploads": ""})
                        upload = ET.fromstring(r.content).findtext(f"{_S3}UploadId") or ""
                    part = bytes(memoryview(buf)[:self.part])
                    del buf[:self.part]
                    r = await self._call("PUT", key, {"partNumber": str(len(etags) + 1), "uploadId": upload}, part)
                    etags.append(r.headers["etag"])
            if upload is None:
                await self._call("PUT", key, None, bytes(buf))
                return
            if buf or not etags:
                r = await self._call("PUT", key, {"partNumber": str(len(etags) + 1), "uploadId": upload}, bytes(buf))
                etags.append(r.headers["etag"])
            done = "".join(f"<Part><PartNumber>{i}</PartNumber><ETag>{e}</ETag></Part>" for i, e in enumerate(etags, 1))
            await self._call("POST", key, {"uploadId": upload}, f"<CompleteMultipartUpload>{done}</CompleteMultipartUpload>".encode())
        except BaseException:
            if upload is not None:
                try:
                    await self._call("DELETE", key, {"uploadId": upload}, ok=(200, 204, 404))
                except Exception:   # dọn lần tải dở là cố gắng hết sức (luật vòng đời của bucket dọn tiếp) — log rồi ném lại lỗi gốc ngay bên dưới
                    log.warning("R2: huỷ lần tải nhiều phần lỗi (luật vòng đời của bucket dọn phần dở)")
            raise

    async def read(self, key: str) -> AsyncIterator[bytes]:   # type: ignore[override]
        path = self._path(key)
        async with self.client.stream("GET", self.endpoint + path, headers=self._headers("GET", path, {})) as r:
            if r.status_code == 404:
                raise FileNotFoundError(key)
            if r.status_code != 200:
                raise RuntimeError(f"R2 GET trả {r.status_code}")
            async for c in r.aiter_bytes(CHUNK):
                yield c

    async def ping(self) -> None:
        """Lệnh có xác thực rẻ nhất (ListObjectsV2, 1 khoá). Lỗi ⇒ RuntimeError chỉ có mã HTTP / loại lỗi — không khoá, không thân trả về."""
        try:
            r = await self._call("GET", None, {"list-type": "2", "max-keys": "1", "prefix": "t/"}, ok=(200, 400, 401, 403, 404))
        except httpx.HTTPError as e:
            raise RuntimeError(f"R2: không tới được kho ({type(e).__name__}) — kiểm mạng / BARISTA_R2_ENDPOINT") from None
        if r.status_code != 200:
            raise RuntimeError(f"R2: bucket {self.bucket!r} từ chối (HTTP {r.status_code}) — kiểm BARISTA_R2_* (khoá, quyền, tên bucket)")

    async def copy(self, src: str, dst: str) -> None:
        await self._call("PUT", dst, None, None, {"x-amz-copy-source": self._path(src)})

    async def delete(self, key: str) -> None:
        await self._call("DELETE", key, ok=(200, 204, 404))

    async def size(self, key: str) -> int | None:
        r = await self._call("HEAD", key, ok=(200, 404))
        return int(r.headers["content-length"]) if r.status_code == 200 else None

    async def keys(self, older_than: datetime, prefix: str = "t/") -> AsyncIterator[str]:   # type: ignore[override]
        token = ""
        while True:
            q = {"list-type": "2", "prefix": prefix, **({"continuation-token": token} if token else {})}
            root = ET.fromstring((await self._call("GET", None, q)).content)
            for c in root.findall(f"{_S3}Contents"):
                key, mod = c.findtext(f"{_S3}Key") or "", c.findtext(f"{_S3}LastModified") or ""
                if KEY_RE.fullmatch(key) and datetime.fromisoformat(mod.replace("Z", "+00:00")) < older_than:
                    yield key
            if (root.findtext(f"{_S3}IsTruncated") or "").lower() != "true":
                return
            token = root.findtext(f"{_S3}NextContinuationToken") or ""

    def signed_url(self, key: str, ttl: int, *, content_type: str, disposition: str) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = self._path(key)
        query = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{self._key_id}/{stamp[:8]}/auto/s3/aws4_request",
                 "X-Amz-Date": stamp, "X-Amz-Expires": str(ttl), "X-Amz-SignedHeaders": "host",
                 "response-content-disposition": disposition, "response-content-type": content_type}
        sig, _ = self._signature("GET", path, query, {"host": self.host}, "UNSIGNED-PAYLOAD", stamp)
        return f"{self.endpoint}{path}?" + "&".join(f"{_q(k)}={_q(v)}" for k, v in sorted({**query, "X-Amz-Signature": sig}.items()))


_SECRET = secrets.token_bytes(32)


def _sign(params: dict[str, str]) -> str:
    key = settings.files_sign_secret.encode() or _SECRET   # trống chỉ ở test / công cụ: API không khởi động khi thiếu (check_config)
    msg = "\n".join(params[k] for k in ("key", "expires", "response-content-type", "response-content-disposition")).encode()
    return base64.urlsafe_b64encode(hmac.new(key, msg, hashlib.sha256).digest()).decode().rstrip("=")


def verify_signed(params: dict[str, str]) -> tuple[str, str, str] | None:
    """Link ký của nơi chứa thư mục ⇒ (khoá, content-type, content-disposition); sai chữ ký / hết hạn ⇒ None (không nói lý do)."""
    try:
        fields = {k: params[k] for k in ("key", "expires", "response-content-type", "response-content-disposition")}
        if int(fields["expires"]) < time.time() or not hmac.compare_digest(_sign(fields), params.get("signature", "")):
            return None
        return _check_key(fields["key"]), fields["response-content-type"], fields["response-content-disposition"]
    except (KeyError, ValueError):
        return None


def stream_object(key: str) -> AsyncIterator[bytes]:
    """Thân đối tượng cho route link ký (đã kiểm chữ ký) — chỉ nơi chứa thư mục cần."""
    return store().read(key)


_store: Store | None = None


def store() -> Store:
    """Nơi chứa theo cấu hình (dựng một lần mỗi tiến trình)."""
    global _store
    if _store is None:
        kind = settings.files_store   # biến KHÔNG đặt ⇒ mặc định "local" (dev / test); đặt mà RỖNG ⇒ lỗi (bản cài quên chọn — không lặng lẽ chạy local)
        if not kind:
            raise RuntimeError("BARISTA_FILES_STORE rỗng — bản cài phải chọn rõ 'local' hoặc 'r2' (runbook 11)")
        if kind == "local":
            # KHÔNG có thư mục mặc định: chạy trong container --rm mà thiếu volume thì thân tệp ghi vào chỗ sẽ biến mất (042 rồi bỏ cột data = mất hẳn)
            if not settings.files_dir:
                raise RuntimeError("BARISTA_FILES_DIR chưa đặt — kho tệp thư mục cần thư mục bền chỉ định rõ (dev: bash backend/scripts/dev-db.sh ghi "
                                   "backend/.env; bản cài: volume, runbook 11)")
            _store = LocalStore(settings.files_dir)
        elif kind == "r2":
            _store = R2Store(account_id=settings.r2_account_id, access_key_id=settings.r2_access_key_id,
                             secret_access_key=settings.r2_secret_access_key, bucket=settings.r2_bucket, endpoint=settings.r2_endpoint)
        else:
            raise RuntimeError(f"BARISTA_FILES_STORE={settings.files_store!r}: chưa có bộ nối cho nơi chứa này (có: 'local', 'r2')")
    return _store


async def check_config() -> None:
    """Gọi lúc khởi động API: cấu hình kho thiếu / sai ⇒ không lên (healthcheck hỏng ⇒ Swarm giữ bản cũ), thay vì lỗi ở lần tải tệp đầu tiên. Kho thư
    mục cần khoá ký link cố định: hai bản chạy chồng lúc đổi bản (start-first) phải nhận link của nhau. R2: một lệnh có xác thực, rẻ (liệt kê 1 khoá) —
    khoá sai / bucket sai lộ ra ngay; lời lỗi không mang khoá hay nội dung trả về."""
    s = store()
    if s.kind == "local" and not settings.files_sign_secret:
        raise RuntimeError("BARISTA_FILES_SIGN_SECRET chưa đặt — khoá ký link của kho tệp thư mục (runbook 11)")
    if isinstance(s, R2Store):
        await s.ping()


def use_store(s: Store | None) -> None:
    """Đổi nơi chứa (test: thư mục tạm của tiến trình). None = dựng lại theo cấu hình ở lần gọi sau."""
    global _store
    _store = s


# ── Sổ tệp ──

@dataclass(frozen=True)
class Stored:
    id: UUID
    sha256: str
    size: int


type Source = bytes | bytearray | memoryview | IO[bytes] | Iterable[bytes] | AsyncIterable[bytes]


async def _chunks(source: Source) -> AsyncIterator[bytes | memoryview]:
    if isinstance(source, bytes | bytearray | memoryview):
        view = memoryview(source)
        for i in range(0, len(view), CHUNK):
            yield view[i:i + CHUNK]   # lát của bộ nhớ sẵn có — không chép
    elif hasattr(source, "read"):
        reader: Any = source
        while c := reader.read(CHUNK):
            yield c
    elif isinstance(source, AsyncIterable):
        async for c in source:
            yield c
    else:
        for c in source:
            yield c


def _mod(ctx: Any) -> str:
    """Module của Ctx THEO SỔ CỦA LÕI (0130) — tệp thuộc module nào quyết theo bản ghi lúc lõi dựng Ctx, không theo thuộc tính gói cầm."""
    from barista.kernel.executor import sealed  # executor nạp sau files
    return sealed(ctx)[1]


def _new_key(tenant_id: UUID) -> str:
    return f"t/{tenant_id}/{uuid4()}"


async def put(ctx: Ctx, source: Source, filename: str, content_type: str, *, max_bytes: int | None = None) -> Stored:
    """Cất một tệp cho module đang chạy (``ctx.module``) ⇒ dòng ``core.files`` trong giao dịch của lệnh. Nhận LUỒNG: băm + đếm cỡ trong lúc đẩy,
    vượt ``max_bytes`` (không quá trần chung ``BARISTA_FILES_MAX_MB``) ⇒ dừng đọc ngay, không để lại đối tượng. Module thêm dòng của mình trỏ
    ``file_id`` vào kết quả trong CÙNG giao dịch."""
    if not _mod(ctx):
        raise RuntimeError("files.put cần module đang chạy (ctx.module)")
    limit = min(max_bytes or settings.files_max_mb * MB, settings.files_max_mb * MB)
    sha, size = hashlib.sha256(), 0

    async def counted() -> AsyncIterator[bytes | memoryview]:
        nonlocal size
        async for c in _chunks(source):
            size += len(c)
            if size > limit:
                raise TooBig
            sha.update(c)
            yield c

    key = _new_key(ctx.p.tenant_id)
    try:
        await store().write(key, counted())
    except TooBig:
        raise Invalid(f"Tệp quá {limit / MB:g} MB") from None
    if size == 0:
        await store().delete(key)
        raise Invalid("Tệp rỗng")
    digest = sha.hexdigest()
    fid = await ctx.val("""INSERT INTO core.files (object_key, filename, content_type, size_bytes, sha256, module, created_by)
                           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (key, fit_name(filename), content_type or "application/octet-stream", size, digest, _mod(ctx), ctx.p.user_id))
    return Stored(fid, digest, size)


async def _references(conn: Any) -> list[tuple[str, str, str]]:
    """(schema, bảng, cột) của mọi khoá ngoại MỘT cột trỏ vào ``core.files`` từ bảng module — kernel không biết tên module, hỏi catalog."""
    cur = await conn.execute(
        """SELECT n.nspname, c.relname, a.attname FROM pg_constraint k
           JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
           JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
           WHERE k.contype = 'f' AND k.confrelid = 'core.files'::regclass AND cardinality(k.conkey) = 1 AND n.nspname <> 'core'
           ORDER BY 1, 2, 3""")
    return [tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in await cur.fetchall()]


async def _visible(ctx: Ctx, file_id: UUID) -> bool:
    """Còn một dòng của CHÍNH module này trỏ vào tệp mà người gọi thấy dưới RLS (thu hồi tin, gỡ quyền, đóng link ⇒ hết thấy ngay)."""
    for schema, table, col in await _references(ctx.conn):
        if schema != _mod(ctx):
            continue
        q = sql.SQL("SELECT EXISTS (SELECT 1 FROM {} WHERE {} = %s)").format(sql.Identifier(schema, table), sql.Identifier(col))
        if await ctx.val(q, (file_id,)):
            return True
    return False


async def _own_row(ctx: Ctx, file_id: UUID) -> dict[str, Any]:
    f = await ctx.one("SELECT * FROM core.files WHERE id = %s", (file_id,))   # RLS: công ty khác ⇒ không thấy
    if f is None:
        raise NotFound("Không thấy tệp")
    if f["module"] != _mod(ctx):
        raise Denied(f"Tệp thuộc phân hệ khác — chỉ sao được qua quyền sao tệp (module {_mod(ctx) or '(không rõ)'})")
    if not await _visible(ctx, file_id):
        raise Denied("Không xem được mục chứa tệp này")
    return f


@dataclass
class Opened:
    """Tệp đã qua kiểm quyền. Đọc bằng ``chunks()`` (luồng) hoặc ``read()``; luôn băm lại trong lúc đọc — lệch dấu lúc ghi ⇒ ghi lỗi + báo."""

    id: UUID
    tenant_id: UUID
    filename: str
    content_type: str
    size: int
    sha256: str
    key: str
    actual_sha256: str = ""

    async def chunks(self) -> AsyncIterator[bytes]:
        sha = hashlib.sha256()
        try:
            async for c in store().read(self.key):
                sha.update(c)
                yield c
        except FileNotFoundError:
            await _integrity_error(self, "missing")
            raise NotFound("Tệp không còn trong kho — đã báo người vận hành") from None
        self.actual_sha256 = sha.hexdigest()
        if self.actual_sha256 != self.sha256:
            await _integrity_error(self, "sha256_mismatch")

    async def read(self) -> bytes:
        return b"".join([c async for c in self.chunks()])


async def _integrity_error(f: Opened, kind: str) -> None:
    """Đọc qua server: lệch chỉ biết khi đã truyền xong ⇒ ghi lại để điều tra (dấu ở sổ, ngoài giao dịch của lệnh — giao dịch đọc / hỏng không xoá
    được dấu) + báo nhà vận hành."""
    log.error("tệp %s (công ty %s) %s: sổ %s, kho %s", f.id, f.tenant_id, kind, f.sha256, f.actual_sha256 or "-")
    alerts.operator(f.tenant_id, "files.open", kind, f"tệp {f.id}")
    from barista.db import db
    try:
        async with db.owner.connection() as conn:
            await conn.execute("UPDATE core.files SET integrity_failed_at = now() WHERE id = %s", (f.id,))
    except Exception:   # dấu lỗi toàn vẹn chỉ để quản trị thấy; ghi không được thì log, không che lỗi gốc của lượt đọc
        log.exception("không ghi được dấu lỗi toàn vẹn của tệp %s", f.id)


async def open(ctx: Ctx, file_id: UUID) -> Opened:
    """Mở tệp của module đang chạy. Module đọc dòng CỦA MÌNH dưới RLS người xem trước; kernel kiểm lại: đúng module tạo, còn dòng module trỏ vào mà
    người gọi thấy. Tệp đã ``release`` vẫn mở được (bản gốc cho kiểm toán) nếu dòng module còn."""
    f = await _own_row(ctx, file_id)
    return Opened(f["id"], f["tenant_id"], f["filename"], f["content_type"], f["size_bytes"], f["sha256"], f["object_key"])


async def read(ctx: Ctx, file_id: UUID) -> bytes:
    return await (await open(ctx, file_id)).read()


async def digest(ctx: Ctx, file_id: UUID) -> str:
    """Dấu SHA-256 làm BẰNG CHỨNG: đọc lại toàn bộ đối tượng từ kho và băm — không trả cột lưu sẵn (0122 §7)."""
    f = await open(ctx, file_id)
    async for _ in f.chunks():
        pass
    return f.actual_sha256


def disposition(kind: str, filename: str) -> str:
    """``Content-Disposition`` với tên đã làm sạch (ASCII dự phòng + ``filename*`` UTF-8)."""
    clean = "".join(ch for ch in filename.replace("\\", "/").rsplit("/", 1)[-1] if ch >= " " and ch not in '"\x7f').strip(" .") or "tep"
    ascii_ = re.sub(r"[^A-Za-z0-9._-]+", "_", clean).strip("_") or "tep"
    return f"{kind}; filename=\"{ascii_}\"; filename*=UTF-8''{quote(clean, safe='')}"


async def signed_url(ctx: Ctx, file_id: UUID, ttl: int = SIGNED_TTL_MAX, *, inline: bool = False) -> str:
    """Link ký rất ngắn (≤ 60 giây) để xem trước / tải tệp lớn — cấp NGAY sau khi kiểm quyền (như ``open``). Link luôn mang
    ``response-content-disposition`` (mặc định attachment; inline chỉ cho ảnh / PDF) + ``response-content-type``; loại trình duyệt chạy được
    (html, svg, xml…) không bao giờ có link ký — chỉ đọc qua server dạng attachment."""
    if not 0 < ttl <= SIGNED_TTL_MAX:
        raise Invalid(f"Link ký chỉ được có hạn tối đa {SIGNED_TTL_MAX} giây")
    f = await _own_row(ctx, file_id)
    base = f["content_type"].split(";", 1)[0].strip().lower()
    if base in ACTIVE or base.endswith("+xml") or not base:
        raise Denied("Loại tệp này chỉ tải qua máy chủ — không cấp link ký")
    kind = "inline" if inline and base in INLINE_OK else "attachment"
    return store().signed_url(f["object_key"], ttl, content_type=f["content_type"], disposition=disposition(kind, f["filename"]))


async def release(ctx: Ctx, file_id: UUID) -> bool:
    """Dòng module trỏ tới tệp đã mất hẳn (thay bản, xoá mục) ⇒ đánh dấu, KHÔNG xoá đối tượng; vòng dọn theo thời hạn lưu của công ty xoá sau.
    Thu hồi tin / gỡ quyền KHÔNG gọi hàm này (bản gốc vẫn phải xem được)."""
    return bool(await ctx.exec("UPDATE core.files SET released_at = now() WHERE id = %s AND module = %s AND released_at IS NULL",
                               (file_id, _mod(ctx))))


async def grant_copy(ctx: Ctx, file_id: UUID, to_module: str, *, granted_to: UUID | None = None, ttl: int = GRANT_TTL) -> UUID:
    """Module SỞ HỮU tệp cho phép module ``to_module`` sao MỘT lần, cho ĐÚNG một người (mặc định người đang gọi) — sau khi đã kiểm người đó xem
    được mục chứa tệp (kernel kiểm lại như ``open``). Dùng ngay trong cùng giao dịch; hạn chỉ là dự phòng."""
    if not re.fullmatch(r"[a-z_]+", to_module):
        raise Invalid("Phân hệ đích không hợp lệ")
    await _own_row(ctx, file_id)
    return await ctx.val("""INSERT INTO core.file_copy_grants (file_id, to_module, granted_to, created_by, expires_at)
                            VALUES (%s, %s, %s, %s, now() + make_interval(secs => %s)) RETURNING id""",
                         (file_id, to_module, granted_to or ctx.p.user_id, ctx.p.user_id, max(1, min(ttl, GRANT_TTL))))


async def copy(ctx: Ctx, file_id: UUID, grant: UUID) -> Stored:
    """Module ĐÍCH dùng quyền sao: đánh dấu đã dùng TRONG CÙNG câu lệnh kiểm (một lần, đúng module, đúng người, còn hạn) ⇒ sao đối tượng ở phía kho
    (không đọc thân tệp qua ứng dụng) ⇒ dòng sổ mới thuộc module đích, ``copied_from`` = tệp gốc; cỡ / sha256 / loại giữ nguyên (mở vẫn băm lại).
    Lỗi giữa chừng ⇒ giao dịch hoàn (quyền chưa dùng), đối tượng đã sao thành mồ côi, vòng quét xoá sau thời gian chờ (``orphan_age``)."""
    used = await ctx.val("""UPDATE core.file_copy_grants SET used_at = now()
                            WHERE id = %s AND file_id = %s AND to_module = %s AND granted_to IS NOT DISTINCT FROM %s
                              AND used_at IS NULL AND expires_at > now()
                            RETURNING id""", (grant, file_id, _mod(ctx), ctx.p.user_id))
    if used is None:
        raise Denied("Không có quyền sao tệp này (quyền sao sai, đã dùng, hết hạn, hoặc cấp cho phân hệ / người khác)")
    src = await ctx.one("SELECT * FROM core.files WHERE id = %s", (file_id,))
    if src is None:
        raise NotFound("Không thấy tệp")
    key = _new_key(ctx.p.tenant_id)
    await store().copy(src["object_key"], key)
    fid = await ctx.val("""INSERT INTO core.files (object_key, filename, content_type, size_bytes, sha256, module, created_by, copied_from)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (key, src["filename"], src["content_type"], src["size_bytes"], src["sha256"], _mod(ctx), ctx.p.user_id, file_id))
    await ctx.exec("UPDATE core.file_copy_grants SET copy_id = %s WHERE id = %s", (fid, grant))
    return Stored(fid, src["sha256"], src["size_bytes"])


# ── Vòng quét (quyền chủ DB, xuyên công ty — kernel chỉ đọc catalog + bảng lõi) ──

def _unreferenced(refs: list[tuple[str, str, str]]) -> sql.Composable:
    parts = [sql.SQL("NOT EXISTS (SELECT 1 FROM {} r WHERE r.{} = f.id)").format(sql.Identifier(s, t), sql.Identifier(c)) for s, t, c in refs]
    return sql.SQL(" AND ").join(parts) if parts else sql.SQL("true")


async def release_orphan_rows(conn: Any) -> int:
    """Dòng sổ không còn bảng module nào trỏ tới (dòng module bị xoá theo dây chuyền: tin, tài liệu, công ty…) ⇒ ``release``."""
    q = sql.SQL("UPDATE core.files f SET released_at = now() WHERE f.released_at IS NULL AND {}").format(_unreferenced(await _references(conn)))
    return (await conn.execute(q)).rowcount


async def purge_expired(conn: Any) -> list[str]:
    """Xoá dòng sổ đã ``release`` quá thời hạn lưu CỦA CÔNG TY (trống = giữ mãi), không ``legal_hold``, không ai trỏ tới ⇒ trả khoá để xoá đối
    tượng SAU khi commit (xoá đối tượng hỏng ⇒ thành mồ côi, vòng sau xoá)."""
    q = sql.SQL("""DELETE FROM core.files f USING core.tenant_profile tp
                   WHERE tp.tenant_id = f.tenant_id AND tp.file_retention_days IS NOT NULL AND f.released_at IS NOT NULL AND NOT f.legal_hold
                     AND f.released_at < now() - make_interval(days => tp.file_retention_days) AND {}
                   RETURNING f.object_key""").format(_unreferenced(await _references(conn)))
    return [r["object_key"] if isinstance(r, dict) else r[0] for r in await (await conn.execute(q)).fetchall()]


async def delete_orphan_objects(conn: Any, *, age: timedelta | None = None) -> int:
    """Đối tượng không có dòng sổ và cũ hơn ``age`` (mặc định ``orphan_age()``: giao dịch hỏng sau ``put`` / ``copy``, bản ghi dở) ⇒ xoá.
    Chỉ xoá đối tượng của công ty CÓ trong DB này (khoá ``t/<công ty>/…``): kho lỡ dùng chung với DB khác (dev, test, bản cài khác) thì vòng dọn
    không bao giờ xoá đối tượng của bên kia — mã công ty là uuid, không trùng giữa các DB."""
    s, n, batch = store(), 0, []
    age = orphan_age() if age is None else age

    async def flush() -> int:
        cur = await conn.execute("SELECT object_key FROM core.files WHERE object_key = ANY(%s)", ([k.split(".part-")[0] for k in batch],))
        known = {r["object_key"] if isinstance(r, dict) else r[0] for r in await cur.fetchall()}
        cur = await conn.execute("SELECT id::text FROM core.tenants WHERE id::text = ANY(%s)", (list({k.split("/")[1] for k in batch}),))
        ours = {r["id"] if isinstance(r, dict) else r[0] for r in await cur.fetchall()}
        gone = [k for k in batch if k.split("/")[1] in ours and (".part-" in k or k not in known)]
        for k in gone:
            await s.delete(k)
        return len(gone)

    async for key in s.keys(datetime.now(UTC) - age):
        batch.append(key)
        if len(batch) >= 500:
            n += await flush()
            batch = []
    if batch:
        n += await flush()
    return n


async def sweep_once() -> dict[str, int]:
    from barista.db import db
    async with db.owner_tx() as conn:
        released = await release_orphan_rows(conn)
        keys = await purge_expired(conn)
    for k in keys:
        await store().delete(k)
    async with db.owner.connection() as conn:
        orphans = await delete_orphan_objects(conn)
    out = {"released": released, "purged": len(keys), "orphan_objects": orphans}
    if any(out.values()):
        log.info("dọn tệp: %s", out)
    return out


async def run_forever(interval_s: float) -> None:
    while True:
        try:
            await sweep_once()
        except Exception:   # vòng nền không được chết vì một lượt lỗi — log.exception rồi chạy lượt sau
            log.exception("vòng dọn tệp lỗi")
        await asyncio.sleep(interval_s)


# ── Kiểm khôi phục + chuyển thân tệp cũ (bytea) vào kho ──

async def verify(conn: Any) -> list[dict[str, str]]:
    """Diễn tập khôi phục (0122 §6): MỌI dòng sổ ⇒ đối tượng có, cỡ và sha256 khớp. Trả danh sách lỗi (rỗng = đạt). Đọc lại toàn bộ kho."""
    problems: list[dict[str, str]] = []
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute("SELECT id, object_key, size_bytes, sha256 FROM core.files ORDER BY created_at, id")
    for f in await cur.fetchall():
        sha, size = hashlib.sha256(), 0
        try:
            async for c in store().read(f["object_key"]):
                sha.update(c)
                size += len(c)
        except FileNotFoundError:
            problems.append({"file_id": str(f["id"]), "problem": "missing"})
            continue
        if size != f["size_bytes"]:
            problems.append({"file_id": str(f["id"]), "problem": "size"})
        elif sha.hexdigest() != f["sha256"]:
            problems.append({"file_id": str(f["id"]), "problem": "sha256"})
    return problems


async def move_bytea_into_store(conn: AsyncConnection[Any]) -> int:
    """Bước dữ liệu của migration 042 (chạy trong CÙNG giao dịch, ngay trước SQL của nó): mọi bảng module có ``file_id`` trỏ ``core.files`` VÀ còn
    cột ``data bytea`` ⇒ mỗi dòng chưa có ``file_id``: đẩy thân vào kho, thêm dòng sổ (module = schema của bảng), điền ``file_id``. Không biết tên
    module — hỏi catalog. Lỗi ⇒ giao dịch hoàn, đối tượng đã đẩy thành mồ côi (vòng quét xoá); chạy lại an toàn.

    Hai chốt trước khi 042 bỏ cột ``data`` (mất là mất hẳn): kho phải được CẤU HÌNH RÕ (``store()`` từ chối khi thiếu — không có thư mục mặc định
    trong container chạy một lần), và MỖI đối tượng vừa đẩy được ĐỌC LẠI từ kho, so sha256 với thân trong DB — lệch / thiếu ⇒ ném lỗi ⇒ cả 042 hoàn."""
    moved = 0
    s = store()
    cur = conn.cursor(row_factory=dict_row)
    for schema, table, col in await _references(cur):
        await cur.execute("""SELECT array_agg(column_name::text) AS cols FROM information_schema.columns
                             WHERE table_schema = %s AND table_name = %s""", (schema, table))
        cols = set(((await cur.fetchone()) or {}).get("cols") or [])
        await cur.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema = %s AND table_name = %s AND column_name = 'data'
                             AND data_type = 'bytea'""", (schema, table))
        if col != "file_id" or not await cur.fetchone():
            continue
        tbl, fcol = sql.Identifier(schema, table), sql.Identifier(col)
        pick = sql.SQL(", ").join([sql.SQL("tenant_id"), sql.SQL("data"),
                                   sql.SQL("filename") if "filename" in cols else sql.SQL("'tep'::text AS filename"),
                                   sql.SQL("content_type") if "content_type" in cols else sql.SQL("'application/octet-stream'::text AS content_type"),
                                   sql.SQL("created_by") if "created_by" in cols else sql.SQL("NULL::uuid AS created_by")])
        await cur.execute(sql.SQL("SELECT ctid::text AS row FROM {} WHERE {} IS NULL AND data IS NOT NULL FOR UPDATE").format(tbl, fcol))
        for (row,) in [tuple(r.values()) for r in await cur.fetchall()]:
            await cur.execute(sql.SQL("SELECT {} FROM {} WHERE ctid = %s::tid").format(pick, tbl), (row,))
            r = await cur.fetchone()
            assert r is not None
            data = bytes(r["data"])
            digest = hashlib.sha256(data).hexdigest()
            key = _new_key(r["tenant_id"])
            await s.write(key, _chunks(data))
            back = hashlib.sha256()
            try:
                async for c in s.read(key):
                    back.update(c)
            except FileNotFoundError:
                raise RuntimeError(f"chuyển tệp {schema}.{table}: đọc lại không thấy đối tượng vừa đẩy — dừng, cột data giữ nguyên") from None
            if back.hexdigest() != digest:
                raise RuntimeError(f"chuyển tệp {schema}.{table}: đọc lại từ kho lệch sha256 — dừng, cột data giữ nguyên")
            await cur.execute("""INSERT INTO core.files (tenant_id, object_key, filename, content_type, size_bytes, sha256, module, created_by)
                                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                              (r["tenant_id"], key, fit_name(r["filename"]), r["content_type"], len(data), digest, schema,
                               r["created_by"]))
            fid = (await cur.fetchone() or {}).get("id")
            await cur.execute(sql.SQL("UPDATE {} SET {} = %s WHERE ctid = %s::tid").format(tbl, fcol), (fid, row))
            moved += 1
    if moved:
        log.info("đã chuyển %d thân tệp từ bytea vào kho", moved)
    return moved


async def upload_backups(paths: list[str]) -> int:
    """Đẩy bản sao lưu (pg_dump, ảnh chụp kho thư mục) lên bucket SAO LƯU của R2 (``BARISTA_R2_BACKUP_BUCKET``; khoá bucket R2 chống xoá — runbook 11),
    khoá ``backups/<tên tệp>``. Chưa cấu hình ⇒ không làm gì (0)."""
    if not settings.r2_backup_bucket:
        return 0
    s = R2Store(account_id=settings.r2_account_id, access_key_id=settings.r2_access_key_id, secret_access_key=settings.r2_secret_access_key,
                bucket=settings.r2_backup_bucket, endpoint=settings.r2_endpoint)
    try:
        for path in paths:
            with Path(path).open("rb") as f:   # noqa: ASYNC230 — công cụ dòng lệnh chạy một lần (backup.sh), không phục vụ request
                await s.write(f"backups/{Path(path).name}", _chunks(f))
    finally:
        await s.client.aclose()
    return len(paths)


async def _main(argv: list[str]) -> int:
    import json
    import sys
    if argv[:1] == ["backup-upload"] and argv[1:]:
        n = await upload_backups(argv[1:])
        print(json.dumps({"uploaded": n, "bucket_configured": bool(settings.r2_backup_bucket)}))
        return 0
    if argv[:1] != ["verify"]:
        print("Dùng: python -m barista.kernel.files verify | backup-upload <tệp>…", file=sys.stderr)
        return 2
    async with await AsyncConnection.connect(settings.owner_database_url) as conn:
        problems = await verify(conn)
    print(json.dumps({"problems": problems, "ok": not problems}, ensure_ascii=False))
    return 1 if problems else 0


if __name__ == "__main__":
    import sys
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
