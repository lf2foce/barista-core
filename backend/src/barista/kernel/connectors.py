"""KHUNG KẾT NỐI CHUNG (ADR 0244) — phần nhân: hợp đồng BỘ NỐI, sổ đăng ký, mã hoá khoá, HTTP an toàn, nạp một kết nối thành bộ nối có khoá.

Lõi giữ mọi thứ không phụ thuộc hệ ngoài (bảng ``connectors.*``, lịch kéo có con trỏ + thử lại, cửa webhook, nhật ký, màn "Kết nối ngoài", sức khoẻ —
``modules/connectors.py``). Gói kết nối viết:

* **bộ nối** — lớp con của ``Connector``: thử kết nối, kéo một lô từ con trỏ, đọc webhook. Chỉ đổi dữ liệu hệ ngoài về MỤC (dict JSON) của gói; không chạm
  DB, không biết quyền. Gọi HTTP qua ``client()``; link do hệ ngoài trả về (tải tệp) qua ``check_public_url`` trước khi tải.
* **lệnh nhận** — ``@m.connector(BộNối)`` trên một hàm ``(ctx, inp: Delivery)``: lõi giao lô mục (kéo hoặc webhook) cho lệnh này bằng quyền hệ thống của
  công ty, TRONG một điểm lưu riêng của kết nối. Lệnh nhận gọi lệnh của module sở hữu dữ liệu (tạo lead…), chống lặp theo mã của hệ ngoài — kéo lại một
  lô (con trỏ chưa tiến vì lần trước lỗi) không được ghi trùng.

Mạng luôn NGOÀI giao dịch DB: kéo / thử kết nối chạy ở bước chuẩn bị của lệnh lõi (``prepare``), lệnh nhận chỉ ghi.
"""

import asyncio
import base64
import hashlib
import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from barista.config import settings
from barista.kernel import inbound
from barista.kernel.fields import Input
from barista.kernel.fields import field as ffield

ErrorKind = Literal["auth", "rate_limit", "transient", "invalid", "other"]
ERROR_KINDS: dict[str, str] = {"auth": "Khoá sai / hết hạn / thiếu quyền", "rate_limit": "Hệ ngoài giới hạn số lần gọi", "transient": "Lỗi tạm thời / mạng",
                               "invalid": "Hệ ngoài từ chối dữ liệu", "other": "Lỗi khác", "deliver": "Barista không nhận được dữ liệu",
                               "missing": "Bản cài chưa có bộ nối / gói đang tắt", "secrets": "Chưa có khoá"}
TIMEOUT_S = 20.0
KEY_RE = r"^[a-z][a-z0-9_]{1,39}$"
MAX_ITEMS = 500          # một lô tối đa — bộ nối trả nhiều hơn ⇒ lõi từ chối lô (lỗi lập trình của bộ nối)
ROUTE_RE = re.compile(r"[A-Za-z0-9_.:-]{1,120}\Z")   # khoá định tuyến webhook chung (khớp ràng buộc của connectors.routes)
MAX_ROUTES = 500         # khoá định tuyến tối đa của một kết nối


class ConnectorError(Exception):
    """Lỗi phía hệ ngoài / mạng — câu NGƯỜI ĐỌC ĐƯỢC, không mang khoá, không mang thân phản hồi. ``kind``: ``auth`` (khoá sai / hết hạn / thiếu quyền —
    lõi dừng kéo tới khi quản trị sửa khoá), ``rate_limit`` (lùi theo ``retry_after_s``), ``transient`` (lùi dần), ``invalid`` / ``other``."""

    def __init__(self, kind: ErrorKind, message: str, *, retry_after_s: int = 0) -> None:
        super().__init__(message[:400])
        self.kind: ErrorKind = kind
        self.retry_after_s = max(0, int(retry_after_s))


@dataclass(frozen=True)
class Secret:
    """Một khoá kết nối cần (quản trị dán trong app, lõi mã hoá). ``name`` = ``webhook`` là khoá kiểm webhook VÀO (mặc định ``hook_check`` dùng nó)."""
    name: str
    label: str
    help: str = ""
    required: bool = True


@dataclass(frozen=True)
class Batch:
    """Kết quả MỘT lần kéo: mục mới (dict JSON — bộ nối đã đổi về dạng của gói) + con trỏ để lần sau kéo tiếp. ``more`` = còn nữa ⇒ lõi kéo tiếp ngay
    lượt sau (không chờ ``every_min``). Con trỏ chỉ được lõi ghi SAU KHI lệnh nhận ghi xong lô."""
    items: list[dict[str, Any]] = field(default_factory=list)
    cursor: str = ""
    more: bool = False
    claimed: tuple[str, ...] = ()   # khoá định tuyến (vd mã Trang) lần kéo này CHỨNG MINH token của kết nối đọc được ⇒ lõi xác minh định tuyến webhook chung


@dataclass(frozen=True)
class Hook:
    """Một webhook VÀO đã đọc được. ``event_id``: mã sự kiện của hệ ngoài (lõi chống lặp theo nó). ``items``: mục giao NGAY cho lệnh nhận (webhook mang đủ
    dữ liệu); ``wake``: chỉ đánh thức lượt kéo của kết nối (webhook là chuông — dữ liệu kéo theo lô)."""
    event_id: str
    items: list[dict[str, Any]] = field(default_factory=list)
    wake: bool = False


class Connector:
    """Hợp đồng MỘT bộ nối. Lớp con khai thuộc tính lớp + ghi đè hàm cần. Lõi dựng ``Cls(secrets, settings)`` mỗi lần dùng (không giữ trạng thái giữa các
    lượt — trạng thái là con trỏ). ``secrets``: khoá đã giải mã theo tên trong ``Cls.secrets``; ``settings``: cài đặt kết nối (dữ liệu, không bí mật)."""

    key: ClassVar[str] = ""
    label: ClassVar[str] = ""
    secrets: ClassVar[tuple[Secret, ...]] = ()
    every_min: ClassVar[int] = 15          # nhịp kéo khi không có gì mới; 0 = bộ nối không kéo (chỉ webhook / chỉ đẩy ra)
    webhook: ClassVar[bool] = False        # nhận webhook VÀO ở ``/v1/hooks/connectors/<công ty>/<mã kết nối>``
    hook_headers: ClassVar[tuple[str, ...]] = ("x-webhook-token",)   # tiêu đề mang khoá tĩnh khi hệ ngoài không ký thân
    mode_command: ClassVar[str] = ""   # lệnh CỦA GÓI giữ việc đổi "chỉ ghi nhận" ↔ "chạy thật" (gói có luật riêng, vd một đường nhận lead) ⇒ màn lõi từ chối, chỉ đường
    url_settings: ClassVar[tuple[str, ...]] = ()   # khoá cài đặt là ĐỊA CHỈ người nhập (vd "site_url") ⇒ lõi kiểm lúc lưu; gọi tới đó bằng ``client_for``
    # WEBHOOK CHUNG (api 1.66): nhà cung cấp chỉ cho MỘT địa chỉ webhook cho cả app (Meta) ⇒ cửa ``/v1/hooks/shared/<key>`` của bản cài, khoá kiểm là khoá
    # CỦA BẢN CÀI (không theo kết nối). Tin chỉ ĐÁNH THỨC kết nối sở hữu khoá định tuyến đã xác minh (``claims`` + ``Batch.claimed``) — dữ liệu đi lượt kéo.
    shared_hook: ClassVar[bool] = False

    @classmethod
    def claims(cls, settings: Mapping[str, Any]) -> list[str]:
        """Khoá định tuyến kết nối này nhận (vd mã các Trang khai trong cài đặt). Lõi ghi lúc lưu kết nối, CHƯA hiệu lực tới khi lượt kéo xác minh."""
        return []

    @classmethod
    def shared_check(cls, headers: Mapping[str, str], body: bytes) -> str:
        """Tin ở cửa chung có đúng của nhà cung cấp không (khoá của bản cài): "" = đạt, không thì mã lý do. Mặc định ĐÓNG — bộ nối bật ``shared_hook``
        phải viết hàm này (sổ đăng ký từ chối nếu không)."""
        return "no_key"

    @classmethod
    def shared_verify(cls, query: Mapping[str, str]) -> str | None:
        """GET kiểm địa chỉ trước khi nhà cung cấp đăng ký gửi tin (Meta: ``hub.challenge``) ⇒ chữ trả lại; sai / không hỗ trợ ⇒ None."""
        return None

    @classmethod
    def route_keys(cls, payload: Any) -> list[str]:
        """Khoá định tuyến nêu trong một tin ở cửa chung (vd mã Trang của từng mục) — không ném lỗi, dạng lạ ⇒ []."""
        return []


    def __init__(self, secrets: Mapping[str, str], settings: Mapping[str, Any]) -> None:
        self.secret = dict(secrets)
        self.settings = dict(settings)

    async def check(self) -> str:
        """Khoá dùng được ⇒ câu ngắn cho quản trị (vd "3 trang Facebook"); không ⇒ ``ConnectorError``."""
        raise ConnectorError("other", "Bộ nối chưa có cách thử kết nối")

    async def pull(self, cursor: str) -> Batch:
        """Một lô mục mới TỪ con trỏ ``cursor`` ("" = lần đầu). Lỗi ⇒ ``ConnectorError`` (con trỏ không tiến, chạy lại an toàn)."""
        return Batch(cursor=cursor)

    def hook_check(self, headers: Mapping[str, str], body: bytes) -> str:
        """Webhook có đúng của hệ ngoài không: "" = đạt, không thì mã lý do của ``inbound.verify_inbound``. Mặc định: khoá ``webhook`` của kết nối, đủ ba
        dạng ký của lõi (Standard Webhooks, ``x-signature`` có giờ / thân) hoặc khoá tĩnh ở ``hook_headers``."""
        return inbound.verify_inbound(dict(headers), body, self.secret.get("webhook", ""), token_headers=self.hook_headers)

    def hook_parse(self, payload: Any) -> Hook | None:
        """Đọc thân webhook (JSON đã giải) ⇒ ``Hook``; thiếu mã sự kiện / dạng lạ ⇒ None (lõi từ chối 400, ghi log). Không bao giờ ném lỗi."""
        return None


class Delivery(Input):
    """Đầu vào của LỆNH NHẬN (``@m.connector``): MỘT mục — lõi giao từng mục trong điểm lưu riêng (mục lỗi không kéo cả lô, nằm ở hàng thử lại). Mục mang
    dữ liệu khách ⇒ không chép vào nhật ký thao tác (``audit=False``)."""
    connection_id: UUID = ffield("Kết nối")
    item: dict[str, Any] = ffield("Mục", audit=False)
    via: Literal["pull", "hook"] = ffield("Đường vào", "pull")


@dataclass(frozen=True)
class Kind:
    cls: type[Connector]
    owner: str          # module của gói khai bộ nối — chỉ module này lưu / mở kết nối loại đó
    receive: str        # lệnh nhận của gói (``<module>.connector_<key>``)


KINDS: dict[str, Kind] = {}


def register(cls: type[Connector], owner: str, receive: str) -> None:
    if not issubclass(cls, Connector) or not re.fullmatch(KEY_RE, cls.key or ""):
        raise ValueError(f"bộ nối {cls!r}: phải là lớp con của Connector, ``key`` dạng {KEY_RE}")
    if not cls.label.strip():
        raise ValueError(f"bộ nối {cls.key}: thiếu ``label``")
    if cls.key in KINDS:
        raise ValueError(f"bộ nối {cls.key} khai hai lần ({KINDS[cls.key].owner} và {owner})")
    names = [s.name for s in cls.secrets]
    if len(set(names)) != len(names) or any(not re.fullmatch(r"^[a-z][a-z0-9_]*$", n) for n in names):
        raise ValueError(f"bộ nối {cls.key}: tên khoá trùng / sai dạng")
    if cls.webhook and "webhook" not in names:
        raise ValueError(f"bộ nối {cls.key}: nhận webhook thì phải khai khoá ``webhook`` (không có chế độ không kiểm)")
    if cls.shared_hook and any(getattr(cls, f).__func__ is getattr(Connector, f).__func__ for f in ("shared_check", "route_keys", "claims")):
        raise ValueError(f"bộ nối {cls.key}: bật webhook chung thì phải viết shared_check, route_keys và claims (không có cửa chung không kiểm)")
    KINDS[cls.key] = Kind(cls, owner, receive)


# ── Mã hoá khoá: AES-GCM, khoá dẫn từ ``BARISTA_CONNECTOR_KEY`` của bản cài; dữ liệu gắn (AAD) = công ty + kết nối + tên ⇒ chép bản mã sang chỗ khác không giải được ──

class SecretsOff(Exception):
    """Bản cài chưa đặt khoá mã hoá ⇒ không nhận / không đọc khoá kết nối."""


BLOB_V1 = b"\x01"   # byte phiên bản đầu bản mã: đổi cách mã hoá về sau thì đọc được bản cũ


def _aead(raw: str | None = None) -> AESGCM:
    raw = (settings.connector_key if raw is None else raw or "").strip()
    if len(raw) < 32:
        raise SecretsOff("Bản cài chưa đặt BARISTA_CONNECTOR_KEY (≥ 32 ký tự) — nhà vận hành đặt rồi khởi động lại; chưa có thì không lưu được khoá kết nối")
    return AESGCM(hashlib.sha256(b"barista-connector-v1|" + raw.encode()).digest())


def _aad(tenant_id: UUID, connection_id: UUID, name: str) -> bytes:
    return f"{tenant_id}|{connection_id}|{name}".encode()


def seal(tenant_id: UUID, connection_id: UUID, name: str, value: str) -> bytes:
    """Luôn mã bằng khoá HIỆN TẠI của bản cài. Bản mã = byte phiên bản + nonce 12 byte + ciphertext (AES-GCM, AAD = công ty | kết nối | tên)."""
    nonce = os.urandom(12)
    return BLOB_V1 + nonce + _aead().encrypt(nonce, value.encode(), _aad(tenant_id, connection_id, name))


def unseal(tenant_id: UUID, connection_id: UUID, name: str, blob: bytes) -> str:
    """Giải bằng khoá hiện tại, không được thì bằng khoá CŨ (``BARISTA_CONNECTOR_KEY_OLD`` — đổi khoá bản cài: đặt khoá mới, giữ khoá cũ ở biến này tới khi
    mọi khoá kết nối đã được lưu lại bằng khoá mới). Không giải được ⇒ quản trị dán lại khoá."""
    data = bytes(blob)
    if data[:1] != BLOB_V1:
        raise SecretsOff("Khoá đã lưu ở dạng không nhận ra — quản trị dán lại khoá")
    aad = _aad(tenant_id, connection_id, name)
    for raw in (settings.connector_key, settings.connector_key_old):
        if raw and len(raw.strip()) >= 32:
            try:
                return _aead(raw).decrypt(data[1:13], data[13:], aad).decode()
            except InvalidTag:
                continue
    raise SecretsOff("Khoá đã lưu không giải được (khoá mã hoá của bản cài đã đổi) — quản trị dán lại khoá")


def hint(value: str) -> str:
    """Bốn ký tự cuối để quản trị nhận ra khoá đang dùng — không bao giờ hiện cả khoá."""
    v = value.strip()
    return ("…" + v[-4:]) if len(v) >= 12 else "…"


# ── Nạp một kết nối thành bộ nối có khoá (trong giao dịch đang mở của lõi — chỉ đọc DB, không gọi mạng) ──

async def build(conn: Any, connection_id: UUID, *, owner: str = "") -> tuple[dict[str, Any], Connector]:
    """(dòng kết nối, bộ nối đã có khoá). ``owner`` khác "" ⇒ kết nối phải thuộc module đó (gói không mở kết nối của gói khác). Lỗi người đọc được:
    ``LookupError`` (không có / không thuộc), ``ConnectorError("missing")`` (bản cài thiếu bộ nối), ``SecretsOff``."""
    cur = await conn.execute("SELECT * FROM connectors.connections WHERE id = %s", (connection_id,))
    row = await cur.fetchone()
    if row is None or (owner and row["owner"] != owner):
        raise LookupError("Không có kết nối này")
    kind = KINDS.get(row["kind"])
    if kind is None:
        raise ConnectorError("other", f"Bản cài chưa có bộ nối “{row['kind']}” (gói chưa cài)")
    cur = await conn.execute("SELECT name, sealed FROM connectors.secrets WHERE connection_id = %s", (connection_id,))
    secrets = {r["name"]: unseal(row["tenant_id"], connection_id, r["name"], r["sealed"]) for r in await cur.fetchall()}
    return dict(row), kind.cls(secrets, row["settings"] or {})


def missing_secrets(cls: type[Connector], have: set[str]) -> list[str]:
    return [s.label for s in cls.secrets if s.required and s.name not in have]


# ── HTTP: một chỗ dựng client ⇒ test / dữ liệu mẫu thay bằng httpx.MockTransport, không gọi hệ thật ──

_transport: httpx.AsyncBaseTransport | None = None


def use_transport(t: httpx.AsyncBaseTransport | None) -> None:
    """Test / dữ liệu mẫu: mọi lời gọi HTTP của bộ nối đi qua ``t``. None = mạng thật."""
    global _transport
    _transport = t


async def client_for(base_url: str, headers: Mapping[str, str] | None = None, *, timeout_s: float = TIMEOUT_S) -> httpx.AsyncClient:
    """Client tới địa chỉ gốc DO NGƯỜI NHẬP (site WordPress, máy CMS của công ty…): chỉ https, mọi địa chỉ phân giải được phải là internet công khai —
    quản trị công ty không trỏ được khung (kèm khoá) vào mạng nội bộ của máy chủ (chặn SSRF). Địa chỉ cố định của nhà cung cấp dùng ``client``.
    Giới hạn còn lại: DNS đổi giữa lúc kiểm và lúc gọi (ghi ở ADR 0244)."""
    await check_public_url(base_url)
    return client(base_url, headers, timeout_s=timeout_s)


def check_url_setting(value: str) -> str:
    """Kiểm nhanh lúc LƯU một địa chỉ người nhập (không phân giải tên — chạy trong giao dịch): https, có tên máy, không phải IP nội bộ / localhost.
    Kiểm đủ (phân giải tên) ở ``client_for`` mỗi lần gọi. Trả "" khi đạt, không thì câu lỗi."""
    parts = urlsplit(value.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        return "phải là địa chỉ https://…"
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        return "không được trỏ vào máy nội bộ"
    try:
        if not ipaddress.ip_address(host).is_global:
            return "không được trỏ vào địa chỉ nội bộ"
    except ValueError:
        pass
    return ""


def client(base_url: str = "", headers: Mapping[str, str] | None = None, *, timeout_s: float = TIMEOUT_S) -> httpx.AsyncClient:
    """Không theo chuyển hướng (không mang khoá sang máy khác), không dùng proxy môi trường, có hạn thời gian."""
    return httpx.AsyncClient(base_url=base_url, headers=dict(headers or {}), timeout=timeout_s, follow_redirects=False, trust_env=False,
                             transport=_transport)


async def check_public_url(url: str) -> None:
    """Link do HỆ NGOÀI trả về (tệp, ghi âm) chỉ được trỏ ra internet công khai (chặn SSRF): https, mọi địa chỉ phân giải được đều không phải nội bộ /
    loopback / link-local / dành riêng. Qua transport giả (test) thì không phân giải."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ConnectorError("invalid", "Link của hệ ngoài không phải https")
    if _transport is not None:
        return
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(parts.hostname, parts.port or 443)
    except OSError:
        raise ConnectorError("transient", "Không phân giải được máy của hệ ngoài") from None
    for *_, addr in infos:
        if not ipaddress.ip_address(addr[0]).is_global:
            raise ConnectorError("invalid", "Link của hệ ngoài trỏ vào địa chỉ nội bộ — từ chối")


def error_of(exc: BaseException) -> tuple[str, str, int]:
    """(loại, câu người đọc được, giây chờ) của một lỗi khi gọi bộ nối — lỗi lạ (lỗi lập trình của bộ nối) không lộ chi tiết ra màn."""
    if isinstance(exc, ConnectorError):
        return exc.kind, str(exc), exc.retry_after_s
    if isinstance(exc, SecretsOff):
        return "secrets", str(exc), 0
    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError)):
        return "transient", "Hệ ngoài không trả lời kịp", 0
    if isinstance(exc, httpx.HTTPError):
        return "transient", "Lỗi mạng khi gọi hệ ngoài", 0
    return "other", f"Bộ nối lỗi ({type(exc).__name__})", 0


def b64(body: bytes) -> str:
    return base64.b64encode(body).decode()


# ── Gói gọi (qua ``barista.api.connectors``): lưu / liệt kê / mở kết nối của CHÍNH gói — họ ``connector``, đích kiểm ``owned_by`` ──
# Thao tác đích ở module ``connectors`` của lõi; gọi bằng QUYỀN HỆ THỐNG trong Ctx của gói (module gọi là gói).
_SAVE, _OF_KIND = "connectors.save", "connectors.of_kind"


async def save(ctx: Any, cls: type[Connector], *, label: str, settings: dict[str, Any] | None = None, secrets: dict[str, str] | None = None,
               connection_id: UUID | None = None, mode: str | None = None) -> dict[str, Any]:
    """Tạo (``connection_id`` None) hoặc sửa kết nối của bộ nối GÓI NÀY khai, từ lệnh có form của gói. Khoá rỗng = giữ khoá cũ. Trả
    ``{connection_id, webhook_url}``. Gói khai ``m.consumes("connector")``."""
    return await ctx.call_system(_SAVE, kind=cls.key, label=label, settings=settings or {}, secrets=secrets or {}, connection_id=connection_id, mode=mode)


async def connections(ctx: Any, cls: type[Connector]) -> list[dict[str, Any]]:
    """Kết nối của bộ nối GÓI NÀY khai (không có khoá): id, label, status, mode, settings, last_ok_at, last_error."""
    return await ctx.call_system(_OF_KIND, kind=cls.key)


async def open(prep: Any, connection_id: UUID) -> Connector:
    """Bộ nối đã có khoá của một kết nối thuộc GÓI NÀY — CHỈ ở bước chuẩn bị (``prepare=``, ngoài giao dịch, gọi mạng được). Không có / không thuộc gói
    ⇒ ``NotFound``. Không gọi hệ ngoài trong handler (giao dịch đang mở)."""
    from barista.db import db
    from barista.kernel import principal
    from barista.kernel.errors import NotFound
    from barista.kernel.executor import Prep
    if not isinstance(prep, Prep):
        raise TypeError("connectors.open chỉ dùng ở bước chuẩn bị (prepare=) — không gọi hệ ngoài trong giao dịch")
    async with db.tenant_tx(principal.elevate(prep.p), read_only=True) as conn:
        try:
            _, inst = await build(conn, connection_id, owner=prep.module)
        except LookupError:
            raise NotFound("Không có kết nối này") from None
    return inst


# ── Webhook CHUNG (api 1.66): tra công ty có kết nối SỞ HỮU khoá định tuyến đã xác minh — xuyên công ty nên đọc bằng owner, CHỈ mã công ty ──

def clean_keys(keys: Any) -> list[str]:
    """Khoá định tuyến hợp lệ, không trùng, tối đa ``MAX_ROUTES`` (bộ nối trả gì cũng không làm lõi ghi / tra chuỗi lạ)."""
    out: list[str] = []
    for k in keys if isinstance(keys, (list, tuple, set)) else ():
        if isinstance(k, (str, int)) and ROUTE_RE.fullmatch(str(k)) and str(k) not in out:
            out.append(str(k))
    return out[:MAX_ROUTES]


async def route_tenants(kind: str, keys: list[str]) -> list[UUID]:
    """Công ty có kết nối ``kind`` sở hữu (đã xác minh) một trong ``keys``. Không trả gì khác — đánh thức chạy bằng quyền hệ thống của TỪNG công ty."""
    if not keys:
        return []
    from barista.db import db
    async with db.owner.connection() as conn:
        cur = await conn.execute("""SELECT DISTINCT tenant_id FROM connectors.routes WHERE kind = %s AND route_key = ANY(%s) AND verified_at IS NOT NULL""",
                                 (kind, keys))
        return [r["tenant_id"] for r in await cur.fetchall()]
