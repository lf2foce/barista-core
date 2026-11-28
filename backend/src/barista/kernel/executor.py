"""MỘT CỬA: mọi lệnh / truy vấn (API, nhập Excel, bên nghe sự kiện, lệnh sau duyệt, agent sau này) đi qua đây.

Lệnh (``run``): kiểm quyền hành động → kiểm dữ liệu → mở giao dịch dưới quyền người gọi (RLS) → khoá chống lặp →
luật duyệt (nếu lệnh bị đặt luật: tạo phiếu thay vì chạy) → handler → ghi sự kiện outbox + nhật ký CÙNG giao dịch.
Truy vấn (``query``): kiểm quyền → giao dịch READ ONLY (handler đọc không ghi được — DB chặn).
"""

import asyncio
import base64
import hashlib
import json
import logging
import re
import typing
import weakref
from collections.abc import Awaitable, Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any, LiteralString
from uuid import UUID

import psycopg
from psycopg import errors as pg
from psycopg import pq, sql
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ValidationError
from pydantic_core import to_jsonable_python

from barista.db import Conn, db, set_session
from barista.kernel import (
    alerts,
    approvals,
    clock,
    company_settings,
    extra_fields,
    mode,
    needs,
    notify,
    packs,
    principal,
    registry,
    screens,
    teams,
    timing,
    ui,
    usage,
    vocab,
    webhooks,
)
from barista.kernel import (
    preview as previews,
)
from barista.kernel.agent_policy import Caller, Company, Run, decide
from barista.kernel.errors import Conflict, Denied, Invalid, NotFound, OpError
from barista.kernel.principal import Principal

log = logging.getLogger("barista.executor")

type SQL = LiteralString | sql.SQL | sql.Composed

ACTION_LABEL = {"read": "xem", "write": "ghi", "approve": "duyệt", "manage": "quản lý"}
ADMIN_RESOURCE = "core.admin"   # tài nguyên của lệnh quản trị lõi (người, vai, quyền, đội, gói)
# lệnh có thể đổi quyền / thành viên / gói ⇒ báo kênh tức thì nạp lại quyền (0115): quản trị lõi + cửa nhà vận hành (0206 — ``kernel/operator_door``)
AUTH_RESOURCES = frozenset({ADMIN_RESOURCE, "core.operator"})
# DỰNG Ctx (0130, review 08/11): CHỈ lõi, qua ``core_ctx`` — cờ dưới đây chỉ bật trong lúc lõi dựng; ngữ cảnh mới (task / luồng / Context() rỗng) ⇒
# cờ tắt ⇒ từ chối (đóng khi không rõ). QUYỀN CỦA Ctx (kết nối, người gọi, module, module gọi) nằm ở SỔ RIÊNG của lõi lúc dựng, không ở thuộc tính
# trên đối tượng gói cầm: mọi chỗ kiểm (gọi lồng, điểm cắm, sổ tệp, ``owned_by``) đọc từ sổ và đối chiếu tay cầm — sửa, xoá, chép (``copy``,
# ``replace``, ``__dict__``) ⇒ không khớp ⇒ từ chối.
_MINTING: ContextVar[bool] = ContextVar("barista_ctx_minting", default=False)
# đang chạy một dòng của FILE người dùng tải lên (đường nhập tệp của lõi — kernel/imports, có lưới ô); lõi đặt, gói chỉ đọc ``ctx.from_file`` (0151)
FROM_FILE: ContextVar[bool] = ContextVar("barista_from_file", default=False)
# NẠP LỊCH SỬ từ hệ khác (0185): cả chuỗi lệnh — gọi lồng, sự kiện phát ra và bên nghe của chúng — chỉ GHI dữ liệu tham chiếu: không thông báo, không mở việc theo
# dõi, không gửi app từ xa. Chỉ mã trong tiến trình bật (``run(historical=True)``, ``imports.run_import(historical=True)``; ``events`` bật lại khi giao sự kiện đã đánh
# dấu) — không phải ô đầu vào, không có ở cửa HTTP / trợ lý; NGOẠI LỆ DUY NHẤT: app từ xa người vận hành đặt CHỈ GHI NHẬN (0207 — cột core.remote_apps.shadow, app không
# tự đổi được) ⇒ cửa /v1/commands chạy lệnh của app đó ở chế độ này. Gói chỉ đọc ``ctx.historical``.
HISTORICAL = mode.HISTORICAL   # cờ nằm ở module lá ``kernel/mode`` (``notify.send`` cùng đọc)
# LỆNH đang chạy (tên thao tác — lõi đặt ở ``run`` / gọi lồng) — lịch sử thay đổi theo trường (ADR 0246) ghi "đổi qua lệnh nào"; gói không đặt
_COMMAND: ContextVar[str] = ContextVar("barista_command", default="")
_SEALED = ("conn", "p", "module", "caller", "readonly", "background")
_CTXS: dict[int, tuple[weakref.ref[Ctx], Any, Principal, str, str, bool, Any]] = {}
# QUYẾT ĐỊNH DUYỆT chỉ từ người bấm ở cửa HTTP (tay cầm EDGE còn hạn — 0144): không gọi lồng, không qua trợ lý / app / việc nền / ``call_as``
DECISIONS = frozenset({"core.decide"})
# NGOẠI LỆ DUY NHẤT của luật trên: lớp nạp dữ liệu hệ cũ (``kernel/onboard.load(approver_email=…)`` — ADR 0263) quyết phiếu do CHÍNH lượt nạp sinh ra, bằng
# quyền một NGƯỜI THỨ HAI có thật trong công ty, qua ``decide_in_load`` bên dưới. Biến giữ đúng tay cầm của người duyệt đó trong lúc lời gọi chạy — không phải ô
# đầu vào, không có ở cửa HTTP / trợ lý / gọi lồng (``invoke`` vẫn chặn mọi quyết định).
_LOAD_DECIDER: ContextVar[Principal | None] = ContextVar("barista_load_decider", default=None)
CORE_MODULES = ("",)   # Ctx lõi dựng không gắn module (test, việc của lõi) — không có mã gói nào đứng sau


@contextmanager
def historical(on: bool = True) -> Iterator[None]:
    """Chạy khối bên trong ở chế độ nạp lịch sử (``on=False``: tắt hẳn — báo lỗi hệ thống của chính lần nạp vẫn tới quản trị)."""
    token = HISTORICAL.set(on)
    try:
        yield
    finally:
        HISTORICAL.reset(token)


# ĐỘ SÂU CHUỖI PHẢN ỨNG TỰ ĐỘNG (ADR 0246): hành động của luật "Nếu… thì…" chạy trong ``chained(n)`` ⇒ mọi sự kiện lệnh đó phát mang ``_chain = n``
# trong payload; động cơ luật đọc lại để chặn vòng lặp (luật A kích luật B kích luật A…). Lõi không biết luật nào — chỉ giữ con số.
CHAIN: ContextVar[int] = ContextVar("barista_chain", default=0)
CHAIN_KEY = "_chain"


@contextmanager
def chained(depth: int) -> Iterator[None]:
    token = CHAIN.set(depth)
    try:
        yield
    finally:
        CHAIN.reset(token)


def _drop_ctx(key: int) -> None:
    e = _CTXS.get(key)
    if e is not None and e[0]() is None:
        del _CTXS[key]


def sealed(c: object) -> tuple[Principal, str, str]:
    """(người gọi, module, module gọi) của một Ctx THEO SỔ CỦA LÕI — Ctx không do lõi dựng / tay cầm khác bản ghi ⇒ ``TypeError``."""
    e = _CTXS.get(id(c))
    if (e is None or e[0]() is not c or e[1] is not c.conn or e[2] is not c.p   # type: ignore[attr-defined]
            or (e[3], e[4], e[5]) != (c.module, c.caller, c.readonly) or e[6] is not c.background   # type: ignore[attr-defined]
            or not principal.is_issued(e[2])):
        raise TypeError("Ctx không do lõi dựng hoặc đã bị sửa (0130)")
    return e[2], e[3], e[4]


def _readonly(c: Ctx) -> bool:
    sealed(c)
    return _CTXS[id(c)][5]


def _background(c: Ctx) -> principal.Lease | None:
    """Hợp đồng thuê của lượt VIỆC NỀN (bên nghe sự kiện, vòng quét) đã dựng Ctx này — None = chuỗi do người / app khởi đầu."""
    sealed(c)
    return _CTXS[id(c)][6]


def core_ctx(conn: Conn, p: Principal, *args: Any, **kw: Any) -> Ctx:
    """Ngữ cảnh lệnh do lõi dựng (executor, bên nghe, nhập Excel, việc nền của gói nền, test)."""
    token = _MINTING.set(True)
    try:
        return Ctx(conn, p, *args, **kw)
    finally:
        _MINTING.reset(token)


def core_prep(p: Principal, module: str = "") -> Prep:
    """Bước chuẩn bị do LÕI dựng cho đường nhập file (0226): gọi model NGOÀI giao dịch (``ai.run(prep, …)``) mà không giữ kết nối DB trong lúc chờ."""
    return _prep(p, module)


@dataclass(frozen=True)
class Prep:
    """Bước CHUẨN BỊ của một LỆNH (``Module.command(prepare=…)``, ADR 0200 — mở rộng ``prepare`` của truy vấn, 0141): chạy NGOÀI mọi giao dịch, ngay trước
    khi handler chạy thật, để gọi mạng (model, nhà cung cấp) mà không giữ kết nối DB / khoá dòng trong lúc chờ. Đọc dữ liệu bằng ``query`` — một truy
    vấn qua cửa executor bằng quyền người gọi (giao dịch đọc ngắn, trả kết nối ngay); ``ai.run(prep, …)`` gọi model như trong handler. Không bao giờ
    chạy khi chạy thử / soạn đề xuất / gửi phiếu duyệt / phát lại theo khoá chống lặp. Lõi dựng, gói không tự dựng (như ``Ctx``)."""

    p: Principal
    module: str = ""
    preview: bool = False   # cùng tên với ``Ctx.preview`` để ``ai.run`` nhận cả hai — bước chuẩn bị không bao giờ là chạy thử
    # tệp gửi KÈM lệnh (lệnh nhận tệp — ADR 0262): cùng nội dung ``Ctx.files`` mà handler sẽ thấy ⇒ đọc / gọi model trên tệp NGOÀI giao dịch
    files: dict[str, bytes | bytearray] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _MINTING.get():
            raise TypeError("Prep chỉ do lõi dựng (executor) — 0130")

    async def query(self, name: str, **inp: Any) -> Any:
        """Kết quả truy vấn ``name`` bằng quyền người gọi; không chạy được ⇒ ném lỗi cùng loại (``Denied`` / ``NotFound`` / ``Conflict`` / ``Invalid``)."""
        r = await query(self.p, name, inp)
        if r.ok:
            return r.output
        raise {"denied": Denied, "not_found": NotFound, "conflict": Conflict}.get(r.status, Invalid)(r.error or name)

    async def plugs(self, point: str, /, **inp: Any) -> list[Any]:
        """Hỏi mọi gói đang bật đã cắm vào điểm của CHÍNH module này (như ``Ctx.plugs(system=True)``, cùng hợp đồng ``Module.consumes("plug")``) — NGOÀI
        giao dịch của lệnh: mỗi phần cắm chạy như một truy vấn riêng bằng quyền hệ thống (giao dịch ĐỌC ngắn, không giữ khoá nào của lệnh); phần cắm khai
        ``prepare=`` (1.37) gọi nhà cung cấp ở bước đó — ngoài MỌI giao dịch, không giữ kết nối (vd ghi thay đổi lên kênh quảng cáo — 0209). Gói tắt /
        phần cắm không có ⇒ bỏ; trả None ⇒ bỏ; lỗi nghiệp vụ ⇒ ném cùng loại (``Invalid`` / ``Conflict``) — phần cắm muốn báo "không thấy" thì ném
        ``Invalid``. 1.28."""
        if point.split(".")[0] != self.module:
            raise Denied(f"Điểm cắm {point} chỉ module chủ được hỏi")
        if self.module not in registry.FAMILY_CALLERS["plug"]:
            raise Denied(f"module {self.module or '(không rõ)'} không khai dùng họ thao tác “plug” (Module.consumes — 0084)")
        sys_p = principal.elevate(self.p)
        out = []
        for name in registry.PLUGS.get(point, ()):
            r = await _query(sys_p, name, inp)
            if r.status in ("not_found", "denied"):
                continue
            if not r.ok:
                raise (Conflict if r.status == "conflict" else Invalid)(r.error or name)
            if r.output is not None:
                out.append(r.output)
        return out


def _prep(p: Principal, module: str, files: dict[str, bytes | bytearray] | None = None) -> Prep:
    token = _MINTING.set(True)
    try:
        return Prep(p, module, files=dict(files or {}))
    finally:
        _MINTING.reset(token)


class _Prepare(Exception):
    """Trong ``_run``: lệnh có ``prepare`` sắp chạy handler thật mà chưa chuẩn bị ⇒ thoát giao dịch (chưa ghi gì), chuẩn bị ngoài giao dịch, chạy lại."""


_UNPREPARED: Any = object()
# Số lần công ty có LỆNH commit trong tiến trình này — chỉ để việc đọc dùng chung kết quả ĐANG CHẠY (bàn việc) biết có lệnh mới commit sau lúc nó bắt
# đầu ⇒ không ghép người hỏi sau lệnh vào lượt đọc cũ. Không phải bộ nhớ kết quả; nhiều tiến trình thì mỗi tiến trình đếm riêng (chỉ ghép trong tiến trình).
_WRITES: dict[UUID, int] = {}


def writes(tenant_id: UUID) -> int:
    return _WRITES.get(tenant_id, 0)


def door(p: Principal, what: str) -> Principal:
    """Cửa executor (run / query / propose): người gọi phải do lõi cấp và không phải quyền NGƯỜI KHÁC nạp hộ (``principal_of`` — chỉ cho
    ``call_as``)."""
    if not principal.is_issued(p, principal.EDGE, principal.CORE):
        raise TypeError(f"{what}: người gọi không do lõi cấp, hoặc là quyền người khác nạp bằng principal_of (chỉ dùng cho ctx.call_as) — 0130")
    return p


HTTP = {"ok": 200, "proposed": 200, "dry_run": 200, "pending_approval": 202, "invalid": 422, "not_found": 404, "denied": 403, "conflict": 409, "error": 500}


@dataclass
class Result:
    status: str
    output: Any = None
    error: str = ""
    approval_id: str | None = None
    replayed: bool = False

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "pending_approval")

    @property
    def http_status(self) -> int:
        return HTTP[self.status]

    def to_json(self) -> dict[str, Any]:
        return {"status": self.status, "output": self.output, "error": self.error, "approval_id": self.approval_id,
                "replayed": self.replayed}


@dataclass
class Ctx:
    """Thứ handler nhận: kết nối trong giao dịch hiện tại + người gọi. Handler KHÔNG mở giao dịch riêng."""

    conn: Conn
    p: Principal
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    pending_approval: UUID | None = None
    # việc chạy SAU KHI giao dịch commit (đánh thức / khởi động workflow DBOS…) — không bao giờ trong giao dịch
    after: list[Callable[[], Awaitable[None]]] = field(default_factory=list)
    # đang CHẠY THỬ để soạn đề xuất cho trợ lý (sẽ hoàn lại) — lệnh lồng công ty đặt "đề xuất" được chạy thử cùng, không đòi xác nhận lần nữa
    preview: bool = False
    # module đang chạy (thao tác / bên nghe / việc nền của module đó) — ``call_system`` chỉ tới thao tác có hợp đồng cho module này (0083)
    module: str = ""
    # module GỌI tới thao tác này bằng ``call_system`` ("" = không qua call_system) — hợp đồng nhận dữ liệu thuộc về bên gọi kiểm bằng ``owned_by``
    caller: str = ""
    # nội dung tệp gửi KÈM lệnh (route tải lên đọc từ thân request) — KHÔNG nằm trong đầu vào: đầu vào chỉ mang sha256 + cỡ (nhật ký, chống
    # lặp băm cái nhỏ), handler đối chiếu sha256 với bytes ở đây. Không qua base64, không chép thêm bản nào.
    files: dict[str, bytes | bytearray] = field(default_factory=dict)
    # kết quả bước ``prepare`` của truy vấn (chạy NGOÀI giao dịch, trước handler — 0141); None khi không khai / gọi lồng
    prepared: Any = None
    # CĂN CỨ của lệnh (0071) chạy trong Ctx CHỈ ĐỌC: không gọi lệnh nào, không làm thay ai (``job_for``), không xin duyệt
    readonly: bool = False
    # VIỆC NỀN (lõi đánh dấu lúc dựng — bên nghe sự kiện, vòng quét): hợp đồng thuê của đúng lượt chạy đó; chỉ Ctx này (và con của nó) được ``job_for``
    background: Any = None
    # những gì lệnh vừa ĐỔI qua ``ctx.track`` (loại đối tượng, mã, trường, cũ, mới) — con của gọi lồng dùng CHUNG danh sách với cha: bản chạy thử chuẩn
    # (``kernel/preview.py``, ADR 0253) gom "cũ → mới" từ đây, module khai lịch sử là có ngay, không khai thêm gì
    changes: list[tuple[str, Any, str, Any, Any]] = field(default_factory=list)

    @property
    def from_file(self) -> bool:
        """Lệnh đang chạy cho một dòng của TỆP tải lên qua đường nhập của lõi (không phải form / API / dán bảng) — do lõi đặt, không phải ô đầu vào."""
        return FROM_FILE.get()

    @property
    def historical(self) -> bool:
        """Lệnh / bên nghe đang chạy trong một lần NẠP LỊCH SỬ từ hệ khác (0185) — do lõi đặt. Dữ liệu ghi lúc này là tham chiếu: module không khởi động nhắc việc,
        chuyển người, việc theo dõi (``ctx.notify`` và ``work.open`` đã tự im); đánh dấu dòng để vòng quét về sau không coi là việc vừa tới hạn."""
        return HISTORICAL.get()

    def __post_init__(self) -> None:
        if not _MINTING.get():
            raise TypeError("Ctx chỉ do lõi dựng (executor.core_ctx) — gói nhận ctx của lõi, không tự dựng (0130)")
        if not principal.is_issued(self.p):
            raise TypeError("Ctx: người gọi không do lõi cấp (0130)")
        _CTXS[id(self)] = (weakref.ref(self), self.conn, self.p, self.module, self.caller, self.readonly, self.background)
        weakref.finalize(self, _drop_ctx, id(self))

    def __setattr__(self, name: str, value: Any) -> None:
        # chỉ Ctx ĐANG SỐNG mới bị khoá: ``id()`` được cấp lại cho đối tượng mới khi Ctx cũ đã chết mà bản ghi của nó chưa kịp bị ``_drop_ctx`` dọn
        # (bản ghi đó có ``weakref`` đã chết) — Ctx mới dựng ngay trên địa chỉ ấy không được coi là "đã dựng xong" (thấy ở bộ test, 12/11/2026)
        if name in _SEALED and (e := _CTXS.get(id(self))) is not None and e[0]() is self:
            raise TypeError(f"Ctx.{name} cố định từ lúc lõi dựng (0130)")
        object.__setattr__(self, name, value)

    def sees(self, need: Any) -> bool:
        """Người gọi có thấy phần khai ``need`` không (cùng luật cửa ra áp cho ``ui.*(needs=)`` — lớp 3, api 1.59)? Chỉ để KHỎI TÍNH phần sẽ bị bỏ
        (vd không gọi sang gói hoa hồng khi người xem không thấy hoa hồng) hoặc chọn câu chữ — phần hiện ra vẫn phải khai ``needs=`` ở khối / cột."""
        return needs.meets(sealed(self)[0], ui.needs_of(need))

    def owned_by(self, owner: str, what: str) -> None:
        """Hợp đồng quyền hệ thống chỉ cho module gọi đụng dữ liệu CỦA CHÍNH NÓ (nguồn khoản phải thu, loại việc theo dõi…) — 0083."""
        caller = sealed(self)[2]
        if caller and owner != caller:
            raise Denied(f"{what} thuộc module {owner} — module {caller} không được đụng qua hợp đồng hệ thống")

    def after_commit(self, fn: Callable[[], Awaitable[None]]) -> None:
        self.after.append(fn)

    def _read_only_sql(self, query: SQL) -> None:
        if _readonly(self):
            text = query.as_string(self.conn) if isinstance(query, sql.Composable) else str(query)
            if not re.match(r"\s*(--[^\n]*\n\s*)*select\b", text, re.IGNORECASE):
                raise TypeError("Căn cứ chỉ đọc — chỉ câu SELECT (khoá dòng FOR SHARE / FOR UPDATE được), không ghi (0071)")

    async def all(self, query: SQL, params: Any = None) -> list[dict[str, Any]]:
        self._read_only_sql(query)
        cur = await self.conn.execute(query, params)
        return await cur.fetchall()

    async def one(self, query: SQL, params: Any = None) -> dict[str, Any] | None:
        self._read_only_sql(query)
        cur = await self.conn.execute(query, params)
        return await cur.fetchone()

    async def val(self, query: SQL, params: Any = None) -> Any:
        row = await self.one(query, params)
        return None if row is None else next(iter(row.values()))

    async def exec(self, query: SQL, params: Any = None) -> int:
        self._read_only_sql(query)
        cur = await self.conn.execute(query, params)
        return cur.rowcount

    async def notify(self, users: Iterable[UUID | None], *, kind: str, title: str, body: str = "", push: str = "",
                     open: dict[str, Any] | None = None, key: str | None = None) -> int:
        if _readonly(self):
            raise TypeError("Căn cứ chỉ đọc — không gửi thông báo (0071)")
        """Thông báo cho người cần biết, cùng giao dịch (quyết định 0044) — người gây ra việc không tự nhận. Nạp lịch sử (0185) ⇒ không báo ai, trả 0."""
        if HISTORICAL.get():
            return 0
        return await notify.send(self.conn, users, kind=kind, title=title, body=body, push=push, open=open, key=key, actor=self.p.user_id)

    async def track(self, kind: str, subject_id: Any, field_key: str, old: Any, new: Any) -> bool:
        """Ghi LỊCH SỬ THAY ĐỔI một trường đã khai (``Module.tracks`` — ADR 0246, api 1.69) trong CÙNG giao dịch: lệnh hoàn ⇒ lịch sử cũng mất. Cũ = mới ⇒
        không ghi; nạp lịch sử / chỉ ghi nhận (0185) ⇒ không ghi (dữ liệu tham chiếu không thành "vừa đổi"). Chỉ trường của đối tượng thuộc module đang chạy.
        Trả True nếu đã ghi."""
        from barista.kernel import history
        return await history.record(self, kind, subject_id, field_key, old, new)

    async def company(self) -> str:
        """Tên công ty đang chạy (giấy tờ, tin nhắn gửi khách, trang link công khai)."""
        return await self.val("SELECT name FROM core.tenants WHERE id = core.tenant()") or ""

    async def home_team(self, user_id: UUID | None) -> UUID | None:
        """Đội chính của một người — đơn vị mặc định gắn vào hồ sơ người đó tạo / phụ trách (kernel/teams.py). None = chưa thuộc đội."""
        return await teams.home(self.conn, self.p.tenant_id, user_id)

    async def notify_team(self, team_id: UUID | None, **kw: Any) -> int:
        """Thông báo cho mọi thành viên đang hoạt động của một đội (việc chung chưa ai nhận, lead chờ chia)."""
        return await self.notify(await notify.team_members(self.conn, team_id), **kw)

    def emit(self, event_type: str, **payload: Any) -> None:
        if _readonly(self):
            raise TypeError("Căn cứ chỉ đọc — không phát sự kiện (0071)")
        """Sự kiện ghi cùng giao dịch (outbox) — bên nghe chạy SAU khi giao dịch này commit, đúng một lần."""
        self.events.append((event_type, to_jsonable_python(payload)))

    async def call(self, name: str, /, **inp: Any) -> Any:
        """Gọi thao tác của module khác bằng CHÍNH quyền người gọi (RLS áp như thường)."""
        return await invoke(self, name, inp)

    async def call_system(self, name: str, /, **inp: Any) -> Any:
        """Gọi thao tác NỘI BỘ của module khác bằng quyền hệ thống (vd giữ căn khi tạo giao dịch, đếm lead cho marketing).
        Chỉ thao tác khai ``internal=True`` — đó là hợp đồng hẹp module kia công bố, không phải cửa sau."""
        return await invoke(self, name, inp, elevate=True)

    async def call_record_only(self, name: str, /, **inp: Any) -> Any:
        """Như ``call`` (CHÍNH quyền người gọi) nhưng cả chuỗi chạy ở chế độ CHỈ GHI NHẬN — cùng chế độ nạp lịch sử (0185) / app chỉ ghi nhận (0207):
        dữ liệu vào đủ, không thông báo, không việc theo dõi, không hạn đang chạy (lead nằm đỗ), sự kiện phát ra mang dấu ``historical`` (bên nghe cũng ở
        chế độ đó, không gửi app từ xa). Chỉ BỚT tác dụng phụ, không thêm quyền. Cho gói nhận dữ liệu từ nhà cung cấp trong lúc công ty còn chạy song song
        hệ khác (vd lead form quảng cáo khi chia lead vẫn ở hệ cũ) — chế độ là cấu hình của công ty trên kết nối, không phải ô đầu vào của người ngoài. 1.31."""
        events: list[tuple[str, dict[str, Any]]] = []
        with historical():
            out = await invoke(self, name, inp, events=events)
            await flush_events(self.conn, sealed(self)[0], events)   # ghi sự kiện NGAY trong chế độ này (dấu historical), cùng giao dịch
        return out

    async def call_as(self, name: str, who: Principal, /, **inp: Any) -> Any:
        """Gọi thao tác NỘI BỘ của module khác bằng quyền ``who`` (chính người gọi, hoặc người khác nạp bằng ``principal_of``) —
        HẸP hơn quyền hệ thống: RLS của người đó áp. Dùng để hỏi "người này có thấy đối tượng này không" (quyết định 0046).
        ``who`` phải do lõi cấp (``ctx.p``, ``ctx.principal_of``, trợ lý làm thay của người đó) — người gọi tự dựng ⇒ lỗi (0130)."""
        return await invoke(self, name, inp, as_principal=self._vouched(who), allow_internal=True)

    def _vouched(self, who: Principal) -> Principal:
        me = sealed(self)[0]
        # ctx.p; quyền người khác nạp bằng principal_of (chỉ hỏi hộ); người gọi "việc" lõi cấp bằng job_for (chỉ trong việc nền quyền hệ thống)
        if who is me or principal.is_issued(who, principal.DELEGATE, principal.JOB):
            return who
        raise TypeError("call_as chỉ nhận ctx.p hoặc người nạp bằng ctx.principal_of — không nhận người gọi tự dựng / đã sửa / giữ lại (0130)")

    async def principal_of(self, user_id: UUID) -> Principal | None:
        """Quyền của một người khác trong công ty, nạp lại từ DB (None = không còn hoạt động) — CHỈ để hỏi hộ qua ``call_as`` (loại DELEGATE: cửa
        executor và route từ chối). Lượt của trợ lý ⇒ người nạp ra cũng là trợ lý (không rơi chế độ trợ lý khi nạp lại chính mình)."""
        from barista.kernel import auth
        me = sealed(self)[0]
        p = await auth.load(self.conn, me.tenant_id, user_id=user_id)
        if p is None:
            return None
        return principal.issued(replace(p, via="agent") if me.via == "agent" else p, kind=principal.DELEGATE)

    async def job_for(self, user_id: UUID) -> Principal | None:
        """Người gọi "VIỆC" do lõi cấp (loại JOB) để việc NỀN — bên nghe sự kiện / vòng quét, đang chạy bằng quyền hệ thống — làm LỆNH bằng quyền
        người phụ trách (nạp lại từ DB; None = không còn hoạt động). Không có trong Ctx người dùng / căn cứ; không bao giờ quyết định duyệt (0144)."""
        from barista.kernel import auth
        me, _, _ = sealed(self)
        lease = _background(self)
        if lease is None or not lease.active or _readonly(self):
            raise TypeError("job_for chỉ dùng trong việc nền lõi dựng (bên nghe sự kiện, vòng quét) — không trong chuỗi do người khởi đầu, không trong "
                            "căn cứ (0144)")
        p = await auth.load(self.conn, me.tenant_id, user_id=user_id)
        return principal.issued(p, kind=principal.JOB, lease=lease) if p is not None else None   # hết lượt việc nền ⇒ hết hạn

    async def call_optional(self, name: str, /, **inp: Any) -> Any:
        """Gọi sang gói KHÔNG bắt buộc (quyền người gọi): công ty chưa bật gói đó, hoặc người này không có quyền với phần thêm đó
        ⇒ None, không lỗi — phần gắn thêm không bao giờ làm vỡ thẻ chính (quyết định 0045)."""
        try:
            return await invoke(self, name, inp)
        except (packs.PackOff, Denied):
            return None

    async def call_system_optional(self, name: str, /, **inp: Any) -> Any:
        """Như ``call_system`` nhưng sang gói không bắt buộc: gói tắt ⇒ None."""
        try:
            return await invoke(self, name, inp, elevate=True)
        except packs.PackOff:
            return None

    async def call_as_optional(self, name: str, who: Principal, /, **inp: Any) -> Any:
        """Như ``call_as`` nhưng sang gói không bắt buộc: gói tắt / ``who`` không có quyền với phần thêm đó ⇒ None."""
        who = self._vouched(who)   # ngoài try: người gọi tự dựng là lỗi lập trình, không phải "không có quyền" ⇒ không nuốt thành None
        try:
            return await invoke(self, name, inp, as_principal=who, allow_internal=True)
        except (packs.PackOff, Denied):
            return None

    async def plugs(self, point: str, /, *, system: bool = False, **inp: Any) -> list[Any]:
        """Hỏi mọi gói đang bật đã cắm vào điểm của CHÍNH module này (0110). Mặc định chạy bằng quyền người gọi: gói tắt / thiếu quyền ⇒ bỏ phần đó.
        ``system``: tra cứu hẹp không phụ thuộc người xem (danh mục, số đếm) — vẫn qua hợp đồng họ thao tác ``Module.consumes("plug")``."""
        me, module, _ = sealed(self)
        if point.split(".")[0] != module:
            raise Denied(f"Điểm cắm {point} chỉ module chủ được hỏi")
        out = []
        for name in registry.PLUGS.get(point, ()):
            try:
                got = await (invoke(self, name, inp, elevate=True) if system else invoke(self, name, inp, as_principal=me, allow_internal=True))
            except (packs.PackOff, Denied):
                continue
            if got is not None:
                out.append(got)
        return out

    async def plugged(self, point: str) -> bool:
        """Có gói đang bật nào cắm vào điểm này không — phân biệt "không có danh mục" với "có danh mục mà không khớp"."""
        on = await packs.enabled(self.conn, self.p.tenant_id)
        return any(packs.module_on(on, name.split(".")[0]) for name in registry.PLUGS.get(point, ()))

    async def request_approval(self, *, chain: str, command: str, input: dict[str, Any], summary: str,
                               on_reject: str | None = None) -> UUID:
        """Xin duyệt cho một lệnh NỘI BỘ; duyệt đủ ⇒ lõi chạy ``command`` bằng quyền hệ thống, ghi tên người duyệt. Lệnh phải là của CHÍNH module
        xin, hoặc khai module này trong ``callers=`` (như ``call_system`` — kiểm lúc xin, vì lúc chạy lại là đường riêng của lõi)."""
        me, module, _ = sealed(self)
        op = registry.get(command)
        if op is None or not op.internal:
            raise RuntimeError(f"{command}: lệnh sau duyệt phải là thao tác nội bộ")
        if principal.kind_of(me) == principal.DELEGATE:
            raise Denied(f"{command}: Ctx đang hỏi hộ bằng quyền người khác (principal_of) — không xin duyệt mang tên họ (0144)")
        if _readonly(self):
            raise TypeError("Căn cứ chỉ đọc — không xin duyệt (0071)")
        if op.module != module and module not in op.callers:
            raise Denied(f"{command}: module {module or '(không rõ)'} không có trong hợp đồng quyền hệ thống ({', '.join(op.callers) or 'không khai'})")
        payload = to_jsonable_python(input)
        aid = await approvals.create(self.conn, self.p, chain=chain, command=command, input=payload,
                                     summary=summary, run_as="system", on_reject=on_reject, basis=await basis_of(self, op, payload))
        self.pending_approval = aid
        return aid

    def require(self, resource: str, action: str) -> None:
        if not self.p.can(resource, action):
            raise Denied(f"Không có quyền {ACTION_LABEL[action]} “{registry.RESOURCES.get(resource, resource)}”")


def validated(p: Principal, op: registry.Op, inp: Any) -> BaseModel:
    """Đầu vào đã kiểm của một thao tác — MỘT nơi cho mọi cửa (app, thẻ đề xuất, trợ lý, API, gọi lồng). Lệnh khai ``extra=`` (0060): gộp ô phẳng
    ``x_<khoá>`` vào ``extra`` rồi kiểm theo định nghĩa trường của CÔNG TY người gọi (``p.fields``; người gọi không mang định nghĩa — quyền hệ thống,
    link — thì mọi giá trị tuỳ biến bị từ chối). Trường lạ / sai kiểu ⇒ ``Invalid``; định nghĩa đổi sau lúc soạn đề xuất ⇒ kiểm lại lúc xác nhận."""
    if not op.extra:
        return op.input.model_validate(inp)
    model = op.input.model_validate(extra_fields.fold(inp))
    return model.model_copy(update={"extra": extra_fields.check(extra_fields.of(p.fields, op.extra), getattr(model, "extra", None))})


def jsonable(v: Any) -> Any:
    return to_jsonable_python(v)


def _deny_message(op: registry.Op) -> str:
    res = registry.RESOURCES.get(op.resource or "", op.resource)
    if op.min_scope == "team":
        return f"“{op.title}” cần quyền {ACTION_LABEL[op.action]} “{res}” phạm vi đội trở lên"
    if op.min_scope:
        return f"“{op.title}” là số liệu toàn công ty — cần quyền {ACTION_LABEL[op.action]} “{res}” phạm vi cả công ty"
    return f"Không có quyền {ACTION_LABEL[op.action]} “{res}” (thao tác: {op.title})"


def group_key(op: registry.Op) -> str:
    """Khoá NHÓM công cụ của trợ lý = phân hệ của thao tác (``books.*``) — công ty bật / tắt / siết cả nhóm một lần."""
    return f"{op.module}.*"


def effective_mode(op: registry.Op, overrides: dict[str, str], autorun: bool = False) -> str:
    """Chế độ trợ lý cho DANH MỤC công cụ / màn quản trị (0144): hỏi ``agent_policy.decide`` như một lượt chat có người, chưa đếm trần — con số
    cuối cùng là của ``_run`` lúc chạy. ``autorun`` = công tắc công ty. Không tự tính ở đây: mọi lý do nằm trong ``decide``."""
    return decide(op, Caller(via="agent", attended=True), Company(overrides, within_caps=True, autorun_low_risk=autorun)).mode


async def policy_overrides(conn: Conn, op: registry.Op | None = None) -> dict[str, str]:
    """Chính sách công ty cho trợ lý (``core.agent_policies``): {lệnh hoặc "<module>.*": mode} — của một thao tác (lệnh + nhóm) hay cả công ty.
    Nơi DUY NHẤT ngoài quản trị đọc bảng này; người quyết là ``agent_policy.decide``."""
    if op is None:
        cur = await conn.execute("SELECT command, mode FROM core.agent_policies")
    else:
        cur = await conn.execute("SELECT command, mode FROM core.agent_policies WHERE command = ANY(%s)", ([op.name, group_key(op)],))
    return {r["command"]: r["mode"] for r in await cur.fetchall()}


async def autorun_switch(conn: Conn) -> tuple[bool, int]:
    """Công tắc công ty "lệnh rủi ro thấp đảo được ⇒ làm ngay" + trần số lần / ngày của nó (``core.agent_trust`` — 0144). Chưa có dòng ⇒ tắt."""
    cur = await conn.execute("SELECT autorun_low_risk, autorun_daily_cap FROM core.agent_trust")
    row = await cur.fetchone()
    return (bool(row["autorun_low_risk"]), int(row["autorun_daily_cap"])) if row else (False, 20)


async def company_for(conn: Conn, op: registry.Op, *, within_caps: bool | None = None) -> Company:
    """Đầu vào "công ty" cho ``decide``: chính sách lệnh + nhóm và công tắc — một chỗ nạp."""
    on, _ = await autorun_switch(conn)
    return Company(await policy_overrides(conn, op), within_caps, on)


def _caller(p: Principal, *, attended: bool = False) -> Caller:
    return Caller(via=p.via, attended=attended, system=p.system, kind=principal.kind_of(p) or "edge")


def money_in(op: registry.Op, payload: dict[str, Any]) -> int:
    """Tổng các ô TIỀN của một đầu vào (ô kiểu money theo form — ``*_vnd`` hoặc ``Money``) — trần tiền của thang tin cậy (0143)."""
    from barista.kernel.forms import field_specs

    total = 0
    for spec in field_specs(op):
        if spec.get("kind") == "money" and isinstance(payload.get(spec["key"]), int | float):
            total += int(payload[spec["key"]])
    return total


def autorun_ref(op: registry.Op, payload: dict[str, Any], out: Any) -> str:
    """Nhật ký lần trợ lý tự làm (0143): ``autorun:<đối tượng>`` — đối tượng lấy ở ô ``op.target`` của đầu vào, không có thì của kết quả (việc mới
    tạo) ⇒ vòng quét so với lệnh ĐẢO của người trên cùng đối tượng."""
    v = payload.get(op.target) if op.target else None
    if v is None and op.target and isinstance(out, dict):
        v = out.get(op.target)
    return f"autorun:{v}" if v is not None else "autorun"


async def _autorun_ok(conn: Conn, op: registry.Op, payload: dict[str, Any]) -> bool:
    """Trợ lý chạy THẲNG một lệnh được nới (0143): trong trần số lần / ngày và trần tiền của chính sách công ty thì ghi bộ đếm (cùng giao dịch —
    lệnh hỏng thì đếm cũng hoàn) và chạy; vượt ⇒ False (bên gọi rơi về đề xuất). Không có chính sách (đã bị gỡ) ⇒ False."""
    cur = await conn.execute("SELECT daily_cap, money_cap_vnd FROM core.agent_policies WHERE command = %s AND mode = 'allow'", (op.name,))
    pol = await cur.fetchone()
    if pol is None:   # không có dòng nới riêng ⇒ chỉ còn đường công tắc công ty (0144): trần ngày của công tắc, không có trần tiền (lệnh low)
        on, cap = await autorun_switch(conn)
        if not (on and op.promotable):
            return False
        pol = {"daily_cap": cap, "money_cap_vnd": None}
    money = money_in(op, payload)
    if pol["money_cap_vnd"] is not None and money > pol["money_cap_vnd"]:
        return False
    cur = await conn.execute(
        """INSERT INTO core.agent_autoruns (command, runs, money_vnd) VALUES (%s, 1, %s)
           ON CONFLICT (tenant_id, command, day) DO UPDATE SET runs = core.agent_autoruns.runs + 1, money_vnd = core.agent_autoruns.money_vnd + EXCLUDED.money_vnd
           RETURNING runs""", (op.name, money))
    row = await cur.fetchone()
    return pol["daily_cap"] is None or (row is not None and int(row["runs"]) <= int(pol["daily_cap"]))


class NeedsConfirmation(Invalid):
    """Trợ lý chạy một lệnh mà bên trong gọi lồng lệnh công ty đặt "đề xuất" ⇒ cả lệnh thành thẻ đề xuất (``run`` bắt), người bấm mới chạy."""


async def agent_denied(conn: Conn, p: Principal, op: registry.Op) -> bool:
    """Trợ lý bị CẤM ở thao tác này theo ``decide`` (soạn đề xuất, truy vấn) — chỉ câu hỏi có / không, phần còn lại ``_run`` quyết."""
    return decide(op, _caller(p), await company_for(conn, op, within_caps=True)).mode == "deny"


def feature_off(p: Principal, op: registry.Op) -> bool:
    """``op`` chỉ thuộc màn của một NGHIỆP VỤ CÔNG TY ĐÃ TẮT (``Screen.feature``, 1.63) ⇒ người và trợ lý của công ty không chạy được — tắt là tắt, không
    chỉ ẩn màn (anh Anh 19/11/2026). Không áp cho quyền hệ thống (gọi lồng, sự kiện) và APP TỪ XA (nguồn nạp dữ liệu máy–máy: công ty tắt màn không
    được làm vỡ đường nạp; bật lại là có đủ dữ liệu)."""
    if p.system or p.via == "app":
        return False
    key = screens.feature_ops().get(op.name)
    return key is not None and not company_settings.on(p.settings, key)


def allowed(p: Principal, op: registry.Op) -> bool:
    """Người này chạy được ``op`` không — quyền + gói nghiệp vụ (khi principal đã mang gói; executor vẫn tra lại trong giao dịch) + nghiệp vụ công ty
    đang bật (``feature_off``)."""
    if p.packs is not None and packs.missing(p.packs, op) is not None:
        return False
    if feature_off(p, op):
        return False
    if op.public is not None:   # link công khai (0064): thao tác của khách chỉ người cầm đúng loại link chạy — nhân viên / hệ thống không chạy thay
        return p.via == "link" and not p.system and op.public == p.link_target
    if p.via == "link" and not p.system:   # người qua link không chạy gì khác (thao tác public tự gọi hợp đồng nội bộ thì đã nâng quyền hệ thống)
        return False
    if p.via == "app" and not p.system and (op.resource is None or (op.agent == "deny" and not op.apps)):
        # app từ xa (0140) chỉ chạy thao tác gắn TÀI NGUYÊN nó được cấp — thao tác "của mọi thành viên" (hộp duyệt, quyết định duyệt, hồ sơ bản thân,
        # thông báo…) là của NGƯỜI; thao tác trợ lý bị cấm (gộp người, tạm ngừng nhận lead, chính sách…) app cũng không, trừ lệnh khai ``apps=True``.
        # Gọi lồng bằng hợp đồng hệ thống thì đã nâng quyền (p.system) ⇒ không vướng.
        return False
    return p.can(op.resource, op.action) and p.has_scope(op.resource, op.min_scope)


def can_run(p: Principal, name: str) -> bool:
    """Người này có bấm được lệnh / mở được truy vấn ``name`` không — MỘT định nghĩa cho mọi nút app vẽ (màn danh mục, trang chi tiết, thẻ).
    Chỉ để ẩn nút; chạy thật vẫn kiểm đủ ở ``run`` / ``query`` (RLS, gói đang bật, luật duyệt)."""
    op = registry.get(name)
    if op is None or op.internal or not allowed(p, op):
        return False
    return p.packs is None or packs.missing(p.packs, op) is None


def visible_actions(p: Principal, out: Any) -> Any:
    """Bỏ khỏi đầu ra truy vấn các nút (``actions`` = [{command, …}] ở trang / thẻ / khối, mọi tầng) người xem không bấm được — trang chi tiết
    không hiện "Tôi đã xử lý" cho người chỉ có quyền xem. Danh sách khác tên ``actions`` (vd hành động của quyền: chuỗi) giữ nguyên."""
    if isinstance(out, list):
        return [visible_actions(p, x) for x in out]
    if not isinstance(out, dict):
        return out
    res = {k: visible_actions(p, v) for k, v in out.items()}
    if isinstance(d := res.get("drill"), dict) and "query" in d and not can_run(p, d["query"]):
        res.pop("drill")   # nguồn của ô số (0097): người xem không mở được truy vấn đó ⇒ ô số không bấm được (không lộ đường vào)
    acts = res.get("actions")
    if isinstance(acts, list) and acts and all(isinstance(a, dict) and "command" in a for a in acts):
        res["actions"] = [a for a in acts if can_run(p, a["command"])]
    if isinstance(a := res.get("action"), dict) and "command" in a and not can_run(p, a["command"]):
        res.pop("action")   # ô / mục bấm được (ui.field(action=) — 0251): người xem không chạy được lệnh ⇒ ô thường
    if "label" in res and isinstance(o := res.get("open"), dict) and "query" in o and not can_run(p, o["query"]):
        res.pop("open")   # ô dẫn sang trang người xem không mở được (vd màn Quản trị) ⇒ chữ thường, không lộ đường vào
    return res


async def approval_chain(conn: Conn, p: Principal, name: str) -> Any:
    """Chuỗi duyệt công ty đặt cho lệnh ``name`` (None = không phải duyệt). Hệ thống (vòng quét, bên nghe sự kiện, lệnh nội bộ xin duyệt) không xét.
    Lệnh đang chạy SAU duyệt: phiếu chỉ bao ĐÚNG lệnh đã duyệt, và lệnh lồng có CÙNG chuỗi người duyệt; lệnh lồng có chuỗi khác (vd A qua trưởng
    nhóm, B công ty bắt qua giám đốc) vẫn phải duyệt riêng — ``invoke`` từ chối, phiếu A thành "lỗi" kèm lý do (review 02/11)."""
    if p.system:
        return None
    cur = await conn.execute("SELECT chain FROM core.approval_rules WHERE command = %s", (name,))
    rule = await cur.fetchone()
    chain = rule["chain"] if rule else None
    if chain and p.approved_scope and (name == p.approved_scope[0] or chain == p.approved_scope[1]):
        return None
    return chain


async def approval_for(ctx: Ctx, op: registry.Op, model: BaseModel) -> tuple[Any, str]:
    """(chuỗi duyệt, mô tả thay đổi cho người duyệt) của LẦN GỌI này: luật công ty đặt cho lệnh (dữ liệu), thu hẹp bằng ``op.sensitive`` nếu lệnh
    khai — vd sửa người dùng chỉ phải duyệt khi đổi vai / chủ tài khoản / trưởng đội (0029). (None, "") = chạy thẳng."""
    chain = await approval_chain(ctx.conn, ctx.p, op.name)
    if not chain or op.sensitive is None:
        return chain, ""
    what = await op.sensitive(ctx, model)
    return (chain, what) if what else (None, "")


async def invoke(parent: Ctx, name: str, inp: dict[str, Any] | BaseModel, *, elevate: bool = False,
                 as_principal: Principal | None = None, events: list[tuple[str, dict[str, Any]]] | None = None,
                 allow_internal: bool = False, approval_handled: bool = False, trusted: bool = False) -> Any:
    """Chạy một thao tác TRONG giao dịch của ``parent`` (gọi lồng, lệnh sau duyệt, nhập Excel). Quyền vẫn kiểm.
    ``allow_internal``: module gọi thao tác nội bộ bằng quyền KHÔNG phải hệ thống (``call_as``) — RLS người đó áp.
    Luật duyệt áp như ``run`` (quyết định 0066): người thường gọi lồng một lệnh công ty đặt "phải duyệt" ⇒ lỗi rõ, KHÔNG chạy thẳng — gọi lồng
    không thành phiếu con (đầu ra lệnh cha không biết đợi phiếu). ``approval_handled``: bên gọi tự xử lý luật duyệt (xem trước nhập Excel).
    ``trusted``: CHỈ lõi (phiếu duyệt chạy lại lệnh bằng quyền người xin / quyền hệ thống có tên người duyệt) — hợp đồng đã kiểm lúc xin duyệt."""
    pp, pmod, _ = sealed(parent)   # quyền của Ctx cha đọc từ sổ của lõi, không từ thuộc tính gói cầm
    op = registry.get(name)
    if op is None:
        raise NotFound(f"Không có thao tác {name}")
    await packs.require(parent.conn, pp.tenant_id, op)
    if op.consistent and ((await (await parent.conn.execute("SHOW transaction_isolation")).fetchone()) or {}).get("transaction_isolation") != "repeatable read":
        # BẤT BIẾN (1.35): lệnh cần đọc nhất quán không bao giờ chạy trong giao dịch thường mà không ai biết. Gọi lồng (kể cả phiếu duyệt chạy lại lệnh
        # trong giao dịch của ``core.decide``) không đổi được mức cách ly ⇒ từ chối rõ thay vì chạy với số có thể lệch giữa các trang.
        raise Conflict(f"{name}: lệnh cần đọc nhất quán (một ảnh dữ liệu) — không chạy qua đường gọi lồng / sau duyệt; chạy trực tiếp")
    p = pp
    contract = elevate
    if name in DECISIONS:
        raise Denied(f"{name}: quyết định duyệt chỉ do người bấm (cửa HTTP) — không gọi lồng, không qua trợ lý / việc nền (0144)")
    ro = _CTXS[id(parent)][5]
    if ro and op.kind == "command":
        raise Denied(f"{name}: căn cứ của lệnh chỉ ĐỌC — không chạy lệnh (0071)")
    if as_principal is not None:
        kind = principal.kind_of(as_principal)
        if kind is None or as_principal.tenant_id != pp.tenant_id:
            raise TypeError(f"{name}: người gọi thay không do lõi cấp / đã sửa, hoặc thuộc công ty khác (0130)")
        if kind == principal.DELEGATE:
            # quyền NGƯỜI KHÁC (principal_of) chỉ để HỎI HỘ: không bao giờ chạy lệnh (0144); truy vấn công khai bằng quyền người khác chỉ khi bên gọi
            # chạy bằng quyền hệ thống. Nạp lại CHÍNH mình (cùng người) đọc như chính mình.
            if op.kind == "command":
                raise Denied(f"{name}: quyền người khác (principal_of) chỉ dùng để hỏi hộ — không chạy lệnh; việc nền dùng ctx.job_for (0144)")
            if as_principal.user_id != pp.user_id and not pp.system and not op.internal:
                raise Denied(f"{name}: quyền người khác (principal_of) chỉ dùng để hỏi hộ qua truy vấn nội bộ")
        elif kind == principal.JOB:
            lease = _CTXS[id(parent)][6]
            if lease is None or not lease.active or principal.lease_of(as_principal) is not lease or ro:
                raise TypeError(f"{name}: người gọi việc (job_for) chỉ dùng trong đúng lượt việc nền đã cấp nó (0144)")
        elif as_principal is not pp and not trusted:
            raise TypeError(f"{name}: module chỉ gọi thay bằng ctx.p, người nạp bằng ctx.principal_of hoặc ctx.job_for (0130)")
        p = as_principal
    elif elevate:
        if op.kind == "command" and principal.kind_of(pp) == principal.DELEGATE:
            # Ctx đang hỏi hộ bằng quyền người khác ⇒ không nâng lên quyền hệ thống để chạy LỆNH mang tên họ (đọc qua hợp đồng hẹp vẫn được)
            raise Denied(f"{name}: Ctx đang hỏi hộ bằng quyền người khác (principal_of) — không chạy lệnh quyền hệ thống (0144)")
        p = principal.elevate(p)   # hợp đồng callers= kiểm ngay dưới — đường nâng quyền duy nhất của module
    # hợp đồng quyền hệ thống (0083) cho MỌI lần chạy thao tác nội bộ bằng quyền hệ thống từ mã của một module — ``call_system``, và cả ``ctx.call``
    # / ``call_as`` khi Ctx cha vốn chạy bằng quyền hệ thống (bên nghe sự kiện): đích khai module nào được gọi; họ thao tác theo hàm đăng ký của họ đó
    if op.internal and p.system and (elevate or (not trusted and pmod not in CORE_MODULES and op.module != pmod)):
        contract = True
        by_family = bool(op.family) and pmod in registry.FAMILY_CALLERS.get(op.family, ())
        if op.callers and pmod not in op.callers and not by_family:
            raise Denied(f"{name}: module {pmod or '(không rõ)'} không có trong hợp đồng quyền hệ thống ({', '.join(op.callers)})")
        if not op.callers and not op.family:
            raise Denied(f"{name} chưa khai hợp đồng quyền hệ thống (callers + why — 0083)")
        # họ thao tác (đối tượng / bộ kiểm / chỉ số…): chỉ module đã khai TIÊU THỤ họ đó (``Module.consumes``); không rõ module đang chạy ⇒ không.
        # Thao tác khai cả ``callers=`` lẫn ``family=``: module trong callers HOẶC module tiêu thụ họ đều qua (gói ngoài lõi lõi không biết tên).
        if op.family and not op.callers and not by_family:
            raise Denied(f"{name}: module {pmod or '(không rõ)'} không khai dùng họ thao tác “{op.family}” (Module.consumes — 0084)")
    elif elevate:
        raise Denied(f"{name} không phải thao tác nội bộ — không chạy bằng quyền hệ thống")
    if pp.via == "agent":
        p = p.as_agent()   # đổi quyền / người thực hiện không xoá nguồn gốc trợ lý (kể cả gọi lồng tiếp)
    if op.kind == "command" and principal.kind_of(p) == principal.DELEGATE:   # cả khi Ctx cha đang chạy bằng quyền hỏi hộ rồi gọi tiếp
        raise Denied(f"{name}: quyền người khác (principal_of) chỉ dùng để hỏi hộ — không chạy lệnh (0144)")
    if op.internal and not p.system and not allow_internal:
        raise Denied(f"{name} là thao tác nội bộ")
    if not allowed(p, op):
        raise Denied(_deny_message(op))
    # trợ lý gọi LỒNG (module A đọc hộ dữ liệu module B bằng quyền trợ lý): chính sách nhóm / thao tác của công ty áp như gọi thẳng — tắt nhóm
    # ⇒ trợ lý không tới dữ liệu nhóm đó kể cả qua hợp đồng hệ thống. Worker thuần hệ thống không mang via="agent" nên không bị siết theo trợ lý.
    # Danh sách lựa chọn (*_options) là siêu dữ liệu form.
    if p.via == "agent" and not op.name.endswith("_options"):
        d = decide(op, _caller(p), await company_for(parent.conn, op), Run(nested=True, preview=parent.preview))
        if d.mode == "deny":
            raise Denied(f"Trợ lý không được dùng “{op.title}” ở công ty này ({'; '.join(d.why)})")
        if d.mode == "propose":   # công ty đặt "đề xuất" ⇒ gọi lồng cũng không ghi thẳng: lệnh ngoài thành thẻ đề xuất (``run``), người bấm mới chạy
            raise NeedsConfirmation(f"“{op.title}” ở công ty này cần người xác nhận")
    # lệnh có trường tuỳ biến (0060): kiểm lại ``extra`` kể cả khi bên gọi đưa sẵn model — không có đường nào cho giá trị chưa kiểm tới handler
    model = inp if isinstance(inp, op.input) and not op.extra else validated(p, op, inp.model_dump(mode="json") if isinstance(inp, BaseModel) else inp)
    switched = p.session_key() != pp.session_key()
    if switched:
        await set_session(parent.conn, p)
    try:
        child = core_ctx(parent.conn, p, parent.events if events is None else events, after=parent.after, preview=parent.preview,
                         module=op.module, caller=pmod if contract else "", readonly=ro, background=_CTXS[id(parent)][6], changes=parent.changes)
        if op.kind == "command" and not approval_handled and (await approval_for(child, op, model))[0]:
            # Invalid (không phải Denied): gọi sang gói không bắt buộc nuốt Denied thành None — ở đây phải hiện ra, không im lặng bỏ qua
            raise Invalid(f"“{op.title}” phải qua duyệt ở công ty này — không chạy kèm trong thao tác khác; làm riêng lệnh đó để gửi phiếu duyệt")
        t1, t2 = ui._PARTS.set(None), ui._PAGING.set(None)   # gọi lồng luôn nhận thẻ / bảng đủ — phần tải sau và trang chỉ của lượt ngoài cùng
        t3 = _COMMAND.set(op.name) if op.kind == "command" else None   # truy vấn lồng không đổi "lệnh đang chạy"
        try:
            out = await op.handler(child, model)
        finally:
            ui._PARTS.reset(t1)
            ui._PAGING.reset(t2)
            if t3 is not None:
                _COMMAND.reset(t3)
    finally:
        if switched and parent.conn.info.transaction_status != pq.TransactionStatus.INERROR:
            await set_session(parent.conn, pp)
    if child.pending_approval:
        parent.pending_approval = child.pending_approval
    if op.kind == "command" and principal.kind_of(p) == principal.JOB:
        # LỆNH chạy bằng tay cầm "việc": nhật ký ghi NGƯỜI (quyền dùng) + đây là việc nền của module nào + ai khởi đầu (người gây ra sự kiện, nếu có)
        with tracing(f"job:{pmod}" + (f":initiator={pp.user_id}" if pp.user_id else "")):
            await _audit(parent.conn, p, name, model.model_dump(mode="json"), "ok")
    # ĐIỂM CHẶN GHI (0124): kết quả gọi lồng chảy vào module khác — nhiều nơi LƯU chữ đó (tên luồng trao đổi, tiêu đề yêu cầu / hồ sơ, thông báo). Đổi dấu nhãn
    # thành nhãn công ty NGAY ĐÂY nên dữ liệu lưu luôn là chữ thật (ảnh chụp lúc ghi), không bao giờ là dấu ``[[…]]``. Bên gọi là worker (quyền hệ thống,
    # không mang nhãn) ⇒ nạp nhãn công ty từ bộ nhớ đệm.
    terms = pp.vocab if pp.vocab is not None else await vocab.terms(parent.conn, pp.tenant_id)
    return vocab.apply(out, terms)


async def basis_of(parent: Ctx, op: registry.Op, inp: dict[str, Any] | BaseModel) -> dict[str, str] | None:
    """Căn cứ HIỆN TẠI của lệnh (0071) — None nếu lệnh không khai. Quyền hệ thống: căn cứ chỉ là dấu phiên bản, và lúc xin (người xin / trợ lý)
    với lúc quyết (người duyệt) phải đọc ra cùng một thứ, không lệch theo phạm vi quyền của người đang bấm."""
    if op.basis is None:
        return None
    model = inp if isinstance(inp, op.input) else op.input.model_validate(inp)
    pp = sealed(parent)[0]
    sp = principal.elevate(pp)
    switched = sp.session_key() != pp.session_key()
    if switched:
        await set_session(parent.conn, sp)
    try:
        # căn cứ CHỈ ĐỌC (Ctx chỉ đọc: chỉ câu SELECT, không lệnh / thông báo / sự kiện / job_for). KHÔNG chạy trong điểm lưu hoàn lại: khoá dòng
        # căn cứ lấy (``FOR SHARE`` / ``FOR UPDATE``) phải giữ tới khi lệnh commit — hoàn điểm lưu sẽ nhả khoá (0071, test_approval_basis)
        out = await op.basis(core_ctx(parent.conn, sp, preview=parent.preview, module=op.module, readonly=True), model)
    finally:
        if switched and parent.conn.info.transaction_status != pq.TransactionStatus.INERROR:
            await set_session(parent.conn, pp)
    return {str(k): str(v) for k, v in out.items()}


async def pins_of(parent: Ctx, op: registry.Op, model: BaseModel) -> dict[str, Any]:
    """Ô đầu vào mà lệnh khai ``pin`` (xem ``registry.Op.pin``): giá trị HIỆN TẠI lúc soạn. Chỉ trợ lý đi đường này (``_propose``) và ô ``agent=False`` không nằm
    trong schema trợ lý thấy ⇒ giá trị ghim LUÔN thắng giá trị trợ lý lỡ gửi kèm (model đoán 0 / 1 rồi lệnh báo BASIS_CHANGED dù không ai đổi gì — eval 11/11).
    Đọc như ``basis_of``: Ctx CHỈ ĐỌC, quyền hệ thống — ghim chỉ là dấu phiên bản, và phải thấy đúng dòng mà lệnh sau duyệt (quyền hệ thống) so lại; đọc theo
    RLS của người soạn thì người có quyền lệnh (tài nguyên riêng của lệnh xác nhận) mà không đọc được bảng ⇒ ghim rỗng ⇒ phiên bản mặc định 0 ⇒ BASIS_CHANGED."""
    if op.pin is None:
        return {}
    pp = sealed(parent)[0]
    sp = principal.elevate(pp)
    switched = sp.session_key() != pp.session_key()
    if switched:
        await set_session(parent.conn, sp)
    try:
        out = await op.pin(core_ctx(parent.conn, sp, preview=parent.preview, module=op.module, readonly=True), model)
    finally:
        if switched and parent.conn.info.transaction_status != pq.TransactionStatus.INERROR:
            await set_session(parent.conn, pp)
    if bad := set(out) - registry.agent_only_fields(op.input):
        raise TypeError(f"{op.name}: pin chỉ được ghim ô agent=False, không phải {sorted(bad)}")
    return dict(out)


def basis_changed(then: dict[str, str] | None, now: dict[str, str] | None, *, shared_only: bool = False) -> list[str]:
    """Nhãn các căn cứ đã đổi (thêm / bớt / khác dấu). Lúc xin không có căn cứ ⇒ không so (phiếu tạo trước khi lệnh khai ``basis``).
    ``shared_only``: người đã sửa đầu vào trên thẻ ⇒ chỉ so căn cứ cùng nhãn (nhãn gọi tên đối tượng: "Giá căn A-1203") — đối tượng người
    tự chọn lại không tính là "đã đổi", đối tượng vẫn giữ mà dữ liệu đổi thì vẫn chặn."""
    if then is None:
        return []
    now = now or {}
    keys = then.keys() & now.keys() if shared_only else then.keys() | now.keys()
    return sorted(k for k in keys if then.get(k) != now.get(k))


def basis_token(basis: dict[str, str] | None) -> str:
    """Căn cứ ⇒ chữ ASCII gắn vào nút trang (``ui.act(basis=)``) để app gửi lại ở tiêu đề ``X-Expect-Basis`` (0251). Chỉ là dấu phiên bản, không phải dữ liệu:
    app sửa chữ này cũng chỉ làm mất phép so cho CHÍNH người bấm — quyền vẫn do executor."""
    return base64.urlsafe_b64encode(json.dumps(basis or {}, sort_keys=True, ensure_ascii=False).encode()).decode() if basis else ""


def basis_of_token(raw: str | None) -> dict[str, str] | None:
    """Ngược ``basis_token``; trống / hỏng ⇒ None (không so — như app cũ không gửi)."""
    if not raw:
        return None
    try:
        got = json.loads(base64.urlsafe_b64decode(raw.encode()))
    except (ValueError, UnicodeError):
        return None
    return {str(k): str(v) for k, v in got.items()} if isinstance(got, dict) else None


def stale_message(changed: list[str]) -> str:
    return f"Căn cứ đã đổi từ lúc xin: {', '.join(changed)} — xem lại rồi làm lại"


async def check_deferred(conn: Conn) -> None:
    """Chạy thử (đề xuất của trợ lý, xem trước nhập Excel) kết thúc bằng hoàn điểm lưu ⇒ ràng buộc HOÃN tới commit (sổ cái
    cân nợ / có) chưa bao giờ được kiểm ⇒ "chạy thử được" chưa chắc commit được. Gọi NGAY TRƯỚC khi hoàn: ép kiểm bây giờ.
    Hoàn điểm lưu trả chế độ hoãn về như cũ (đã thử trên Postgres) ⇒ không ảnh hưởng các dòng chạy sau trong cùng giao dịch."""
    await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


async def run_after(ctx: Ctx) -> None:
    """Chạy các việc hẹn sau commit; lỗi ở đây không làm hỏng kết quả lệnh (việc đã ghi, chỉ là tín hiệu tới muộn)."""
    for fn in ctx.after:
        try:
            await fn()
        except Exception:   # giao dịch đã commit: một việc sau commit lỗi không được chặn việc kế — đã log.exception
            log.exception("việc sau commit lỗi")
    ctx.after.clear()


async def flush_events(conn: Conn, p: Principal, events: list[tuple[str, dict[str, Any]]]) -> None:
    """Ghi sự kiện + việc giao cho từng bên nghe. Bên nghe thuộc gói ĐANG TẮT không nhận việc — quyết định LÚC sự kiện xảy ra, cùng giao dịch với
    lệnh gây ra nó (lệnh đã giữ khoá trạng thái gói): sự kiện xảy ra khi gói bật thì phản ứng chắc chắn chạy (tắt gói bị chặn khi còn việc chờ —
    admin.set_pack); xảy ra khi gói tắt thì không có phản ứng, bật lại không chạy bù."""
    on = await packs.enabled(conn, p.tenant_id) if events else frozenset()
    past = HISTORICAL.get()   # sự kiện của lần nạp lịch sử (0185): đánh dấu ⇒ bên nghe chạy cũng ở chế độ đó (``events._deliver``); không gửi app từ xa
    depth = CHAIN.get()   # phát trong hành động của luật tự động (ADR 0246) ⇒ ghi độ sâu chuỗi để luật kế tiếp chặn vòng lặp
    for event_type, payload in events:
        if depth:
            payload = {**payload, CHAIN_KEY: depth}
        cur = await conn.execute("INSERT INTO core.events (type, payload, actor, historical) VALUES (%s, %s, %s, %s) RETURNING id",
                                 (event_type, Jsonb(payload), p.user_id, past))
        row = await cur.fetchone()
        assert row is not None
        subs = [s for s in registry.subscribers_for(event_type) if packs.subscriber_on(on, s)]
        if subs:
            async with conn.cursor() as c:
                await c.executemany("INSERT INTO core.deliveries (event_id, subscriber) VALUES (%s, %s)",
                                    [(row["id"], s.name) for s in subs])
        if not past and webhooks.by_event(event_type):   # sự kiện công bố cho app từ xa (0140): thư đi ghi cùng giao dịch
            await webhooks.enqueue(conn, p, row["id"], event_type, payload)
    events.clear()


def storable(out: Any) -> Any:
    """Kết quả lệnh ở mọi chỗ LƯU LẠI hoặc ĐƯA RA NGOÀI (bảng chống lặp, lịch sử chat, trả cho model) — BỎ khoá ``once_*`` (bí mật chỉ trả MỘT
    lần cho người bấm, vd mã link gửi khách — 0064): không bao giờ nằm trong DB / gửi tới nhà cung cấp model."""
    return {k: v for k, v in out.items() if not k.startswith("once_")} if isinstance(out, dict) else out


def _private_fields(command: str) -> bool:
    op = registry.get(command)
    return op is not None and any(isinstance(f.json_schema_extra, dict) and f.json_schema_extra.get("audit") is False
                                  for f in op.input.model_fields.values())


def _replayable(command: str, out: Any) -> Any:
    """Kết quả lưu ở bảng chống lặp (``core.idempotency`` — chỉ lọc theo công ty, không xoá theo đối tượng). Lệnh có trường ``audit=False``
    (nội dung thuộc quyền nơi khác — điều nhớ riêng, file…) chỉ giữ MÃ + cờ / số: câu tóm tắt có thể chép nội dung, và "quên" / xoá ở module
    không với tới bảng này. Lần gọi lặp vẫn nhận đúng mã đã tạo."""
    out = storable(out)
    if not isinstance(out, dict) or not _private_fields(command):
        return out
    return {k: v for k, v in out.items()
            if k == "id" or k.endswith(("_id", "_ids")) or v is None or isinstance(v, bool | int | float)}


def _status_only(out: Any) -> dict[str, Any]:
    """Bản tối thiểu của kết quả cho bảng chống lặp (``keep_output=False``): chỉ mã phiếu duyệt (để lần gọi lặp biết lệnh đang chờ duyệt)."""
    return {"approval_id": out["approval_id"]} if isinstance(out, dict) and out.get("approval_id") else {}


def _loggable(command: str, inp: Any) -> Any:
    """Trường khai ``field(..., audit=False)`` (nội dung file, ảnh…) không chép vào nhật ký: nhật ký cả công ty quản trị đọc được, còn nội dung
    đó thuộc quyền nơi khác (kệ tài liệu…). Ghi thay bằng cỡ + sha256 — vẫn đối chiếu được đúng file nào."""
    op = registry.get(command)
    data = jsonable(inp)
    if op is None or not isinstance(data, dict):
        return data
    for key, f in op.input.model_fields.items():
        extra = f.json_schema_extra if isinstance(f.json_schema_extra, dict) else {}
        if extra.get("audit") is False and isinstance(data.get(key), str):
            raw = data[key].encode()
            data[key] = {"omitted": True, "chars": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        elif extra.get("audit") is False and isinstance(data.get(key), dict | list):
            # tiêu đề webhook (mang khoá / chữ ký — 0172 §2), danh sách nhạy cảm: chỉ TÊN khoá (dict) / số phần tử + sha256, không giá trị
            raw = json.dumps(data[key], sort_keys=True, ensure_ascii=False, default=str).encode()
            data[key] = {"omitted": True, "sha256": hashlib.sha256(raw).hexdigest(),
                         **({"keys": sorted(map(str, data[key]))[:60]} if isinstance(data[key], dict) else {"items": len(data[key])})}
    return data


# MÃ VẾT (migration 001): nơi sinh ra thao tác — "chat:<lượt>" (trợ lý trong trò chuyện + lần người bấm xác nhận thẻ của lượt đó),
# "morning:<đề xuất>", "work:<việc>". Đặt một lần ở chỗ khởi động (``with tracing(...)``); mọi lệnh chạy bên trong — kể cả gọi lồng — tự mang vào
# nhật ký thao tác. '' = người làm trực tiếp.
_trace: ContextVar[str] = ContextVar("barista_trace", default="")


def current_command() -> str:
    """Lệnh đang chạy (gọi lồng: lệnh trong cùng) — rỗng ngoài một lệnh (bên nghe sự kiện tự ghi SQL)."""
    return _COMMAND.get()


def current_trace() -> str:
    """Vết đang đặt (rỗng = người làm trực tiếp) — module ghi kèm làm NGUỒN của thứ nó sinh ra (vd mục nhớ — 0091)."""
    return _trace.get()


@contextmanager
def tracing(trace: str) -> Iterator[None]:
    token = _trace.set(trace[:200])
    try:
        yield
    finally:
        _trace.reset(token)


async def _audit(conn: Conn, p: Principal, command: str, inp: Any, status: str, error: str = "", ref: str = "") -> None:
    await conn.execute(
        "INSERT INTO core.audit_log (user_id, approved_by, system, via, command, input, status, error, ref, trace)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (p.user_id, list(p.approved_by), p.system, p.via, command, Jsonb(_loggable(command, inp)), status, error[:2000], ref, _trace.get()),
    )


async def _system_error(p: Principal, name: str, inp: Any, r: Result, e: BaseException) -> None:
    """Lỗi BẤT NGỜ (ROADMAP A4): người dùng thấy câu chung; NHẬT KÝ (quản trị đọc) ghi loại + nội dung lỗi để sửa được; sự kiện
    ``core.system_error`` (bên nghe của công ty báo quản trị); kênh của nhà vận hành (``alerts``). Giao dịch riêng — giao dịch gốc đã hỏng."""
    try:
        async with db.tenant_tx(p) as conn:
            await _audit(conn, p, name, inp, "error", f"{r.error} [{type(e).__name__}: {e}]")
            with historical(False):   # lỗi của CHÍNH lần nạp là chuyện hôm nay — quản trị vẫn được báo
                await flush_events(conn, p, [("core.system_error", {"command": name, "kind": type(e).__name__})])
    except Exception:  # nhật ký hỏng không được che lỗi gốc
        log.exception("không ghi được lỗi hệ thống của %s", name)
    alerts.operator(p.tenant_id, name, type(e).__name__, str(e))


async def _failed(p: Principal, name: str, inp: Any, e: BaseException, model: type[BaseModel] | None) -> Result:
    """Truy vấn / soạn đề xuất lỗi: lỗi hệ thống ghi như lệnh (lỗi nghiệp vụ — sai dữ liệu, thiếu quyền — chỉ trả về, không ghi)."""
    r = map_error(e, model)
    if r.status == "error":
        await _system_error(p, name, inp, r, e)
    return r


async def record_dismissed(p: Principal, command: str, inp: Any, *, conn: Conn | None = None) -> None:
    """Người BỎ một đề xuất của trợ lý (0091) — ghi nhật ký cùng vết với lúc trợ lý soạn: tín hiệu "đề xuất → xác nhận / sửa / bỏ" cho trí nhớ
    doanh nghiệp. Không chạy gì; gọi trong ``tracing`` của lượt / thẻ. Truyền ``conn`` khi đã ở trong giao dịch để không mượn thêm kết nối pool."""
    if conn is not None:
        await _audit(conn, p, command, inp, "dismissed", "")
        return
    await _audit_alone(p, command, inp, "dismissed", "")


async def _audit_alone(p: Principal, command: str, inp: Any, status: str, error: str) -> None:
    try:
        async with db.tenant_tx(p) as conn:
            await _audit(conn, p, command, inp, status, error)
    except Exception:  # nhật ký hỏng không được che lỗi gốc
        log.exception("không ghi được nhật ký cho %s", command)


def scoped_key(p: Principal, key: str, *, shared: bool = False) -> str:
    """Khoá chống lặp theo người gọi: ``<user>:<khoá>`` (hệ thống: ``system``). Dài quá cột ⇒ băm phần khoá. ``shared``: khoá của LƯỢT NẠP dữ liệu hệ cũ
    (``run(load_key=True)``) — ``load:<khoá>``, chung cho mọi người chạy của lượt nạp trong công ty."""
    who = "load" if shared else str(p.user_id) if p.user_id else "system"
    scoped = f"{who}:{key}"
    return scoped if len(scoped) <= 300 else f"{who}:{hashlib.sha256(key.encode()).hexdigest()}"


def _input_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


_PG_INVALID = (pg.CheckViolation, pg.ForeignKeyViolation, pg.NotNullViolation, pg.RaiseException,
               pg.InvalidTextRepresentation, pg.NumericValueOutOfRange, pg.InvalidDatetimeFormat,
               pg.DatetimeFieldOverflow, pg.ExclusionViolation)


def map_error(e: BaseException, model: type[BaseModel] | None = None) -> Result:
    if isinstance(e, OpError):
        return Result(e.status, error=e.message)
    if isinstance(e, ValidationError):
        return Result("invalid", error=explain(e, model))
    if isinstance(e, pg.InsufficientPrivilege):
        return Result("denied", error="Ngoài phạm vi quyền: không được ghi dòng này.")
    if isinstance(e, pg.SerializationFailure):   # lệnh đọc nhất quán (``consistent``) đụng dòng người khác vừa sửa — chạy lại là đủ
        return Result("conflict", error="Dữ liệu vừa thay đổi trong lúc chạy — thử lại.")
    if isinstance(e, pg.UniqueViolation):
        return Result("conflict", error=f"Trùng dữ liệu đã có ({e.diag.constraint_name or 'khoá duy nhất'}).")
    if isinstance(e, _PG_INVALID):
        return Result("invalid", error=(e.diag.message_primary or str(e)))
    log.exception("lỗi hệ thống", exc_info=e)
    return Result("error", error="Lỗi hệ thống — đã ghi lại để kiểm tra.")


_PYDANTIC_VI = {
    "missing": "bắt buộc",
    "extra_forbidden": "trường không có trong mẫu",
    "int_parsing": "phải là số nguyên",
    "int_type": "phải là số nguyên",
    "float_parsing": "phải là số",
    "bool_parsing": "phải là có / không",
    "date_parsing": "ngày không hợp lệ",
    "date_from_datetime_parsing": "ngày không hợp lệ",
    "uuid_parsing": "mã không hợp lệ",
    "string_too_short": "không được để trống",
}


_BOUNDS = {"greater_than_equal": "phải từ {} trở lên", "greater_than": "phải lớn hơn {}", "less_than_equal": "tối đa {}", "less_than": "phải nhỏ hơn {}"}


def _one_of(expected: str, f: Any) -> str:
    """Lỗi "không nằm trong danh sách": nói bằng NHÃN người đọc (cái họ thấy trong ô chọn / mẫu Excel), tiếng Việt — không in lại chuỗi kỹ thuật của pydantic
    ('a', 'b' or 'c'). Ô không có nhãn khai thì dùng chính giá trị."""
    values = [v.strip().strip("'\"") for v in re.split(r",\s*|\s+or\s+", expected) if v.strip()]
    extra = f.json_schema_extra if f is not None and isinstance(f.json_schema_extra, dict) else {}
    ui_raw = extra.get("ui")
    ui: dict[str, Any] = ui_raw if isinstance(ui_raw, dict) else {}
    labels_raw = ui.get("options")
    labels: dict[Any, Any] = labels_raw if isinstance(labels_raw, dict) else {}
    shown = [str(labels.get(v, v)) for v in values]
    if len(shown) > 1:
        return "phải là một trong: " + ", ".join(shown[:-1]) + " hoặc " + shown[-1]
    return "phải là: " + (shown[0] if shown else "giá trị trong danh sách")


def explain(e: ValidationError, model: type[BaseModel] | None) -> str:
    parts = []
    for err in e.errors():
        loc = err.get("loc") or ()
        key = str(loc[0]) if loc else ""
        f = model.model_fields.get(key) if model else None
        label = (f.title if f and f.title else key) or "dữ liệu"
        if f and len(loc) >= 3 and isinstance(loc[1], int):   # ô con của form nhiều dòng: nói rõ dòng nào, ô nào
            inner = next((a for a in typing.get_args(f.annotation) if isinstance(a, type) and issubclass(a, BaseModel)), None)
            sub = inner.model_fields.get(str(loc[2])) if inner else None
            label = f"{label} dòng {loc[1] + 1}, {(sub.title if sub and sub.title else loc[2])}"
        t = err.get("type", "")
        if t == "value_error":
            msg = str(err.get("ctx", {}).get("error") or err.get("msg"))
        elif t == "literal_error":
            msg = _one_of(str(err.get("ctx", {}).get("expected", "")), f)
        elif t in _BOUNDS:
            ctx = err.get("ctx", {})
            msg = _BOUNDS[t].format(ctx.get("ge", ctx.get("gt", ctx.get("le", ctx.get("lt", "")))))
        else:
            msg = _PYDANTIC_VI.get(t, err.get("msg", ""))
        parts.append(f"{label}: {msg}")
    return "; ".join(parts)


def _unsent(preview: Any) -> Any:
    """Chạy thử CHƯA làm gì: câu ``summary`` của lệnh kể việc đã xong ("Đã gửi đơn nghỉ…") — trên thẻ đề xuất thành "Sẽ gửi đơn nghỉ…". Một chỗ, theo trạng
    thái ``proposed`` của kết quả — handler không phải tự biết mình đang chạy thử hay chạy thật."""
    # "… đã xác nhận trước đó" / "Đã … trước đó": việc ĐÃ có từ trước (chạy lại không làm gì) — không phải việc chưa gửi, giữ nguyên
    if (isinstance(preview, dict) and isinstance(preview.get("summary"), str) and preview["summary"].startswith("Đã ")
            and "trước đó" not in preview["summary"]):
        return {**preview, "summary": "Sẽ " + preview["summary"][3:]}
    return preview


async def _propose(conn: Conn, p: Principal, op: registry.Op, model: BaseModel, payload: dict[str, Any], why: tuple[str, ...] = (),
                   reason: str = "") -> Result:
    """Trợ lý: chạy thử thật rồi bỏ (điểm lưu) ⇒ thẻ đề xuất. Đề xuất hiện ra là chạy được tại lúc soạn — căn đã bán, trùng SĐT, sai quyền dòng…
    báo ngay cho trợ lý thay vì để người bấm rồi mới lỗi. Lệnh lồng công ty đặt "đề xuất" chạy thử cùng (``preview``). Kết quả mang ``brief`` — BẢN CHẠY THỬ
    CHUẨN (``kernel/preview.py``, ADR 0253): hồ sơ · cũ → mới · vì sao · đảo được không, dựng ngay trong điểm lưu (còn thấy thứ vừa đổi) — ``reason`` là lý
    do của NGUỒN gợi ý (thẻ sáng / luật), thắng ``purpose=`` của lệnh."""
    async with conn.transaction():
        ctx = core_ctx(conn, p, preview=True, module=op.module)
        if pinned := await pins_of(ctx, op, model):   # chốt phiên bản người sẽ thấy: thẻ mang nó, lúc bấm lệnh so lại đúng nó
            model = op.input.model_validate({**payload, **jsonable(pinned)})
            payload = model.model_dump(mode="json")
        basis = await basis_of(ctx, op, model)   # TRƯỚC handler: căn cứ là thứ người sẽ thấy trên thẻ, không phải sau khi chạy thử
        preview = _unsent(jsonable(await op.handler(ctx, model)))
        await check_deferred(conn)
        brief = await _brief(ctx, op, payload, preview, reason)
        raise psycopg.Rollback()
    await _audit(conn, p, op.name, payload, "proposed")
    return Result("proposed", {"command": op.name, "title": op.title, "input": payload, "preview": preview, "brief": brief, "basis": basis,
                               **({"why": list(why)} if why else {})})   # vì sao thành đề xuất (0144) — thẻ hiện được, app cũ bỏ qua


async def _brief(ctx: Ctx, op: registry.Op, payload: dict[str, Any], out: Any, reason: str = "") -> dict[str, Any]:
    """Bản chạy thử chuẩn của lần chạy thử vừa xong (``kernel/preview.py``). Không dựng được ≠ lệnh hỏng: thẻ vẫn có tên lệnh + đầu vào, người duyệt vẫn
    chạy được (log để sửa)."""
    try:
        return await previews.build(ctx, op, payload, out, reason=reason)
    except Exception:
        log.exception("không dựng được bản chạy thử của %s", op.name)
        return {"title": op.title, "subject": None, "changes": [], "reason": reason or op.purpose, "reversible": previews.reversibility(op)}


async def _brief_for_approval(conn: Conn, p: Principal, op: registry.Op, model: BaseModel, payload: dict[str, Any]) -> dict[str, Any]:
    """Phiếu duyệt mang CÙNG bản chạy thử chuẩn với thẻ đề xuất: chạy thử lệnh trong điểm lưu (rồi hoàn) ngay lúc xin, người duyệt đọc "hồ sơ nào · cũ → mới ·
    vì sao" thay vì tên lệnh. Lệnh chạy thử lỗi (căn cứ thiếu, gọi lồng cần duyệt…) ⇒ bản mô tả không có "cũ → mới" — phiếu vẫn gửi như trước."""
    brief: dict[str, Any] = {"title": op.title, "subject": None, "changes": [], "reason": op.purpose, "reversible": previews.reversibility(op)}
    try:
        async with conn.transaction():
            ctx = core_ctx(conn, p, preview=True, module=op.module)
            out = _unsent(jsonable(await op.handler(ctx, model)))
            await check_deferred(conn)
            brief = await _brief(ctx, op, payload, out)
            raise psycopg.Rollback()
    except Exception:   # chỉ bản mô tả — việc xin duyệt không được hỏng vì nó
        log.info("phiếu duyệt %s: chạy thử không ra bản mô tả đủ", op.name, exc_info=True)
    return brief


def localized(p: Principal, r: Result) -> Result:
    """Cửa ra CHỮ (0124): đổi dấu nhãn từ vựng công ty trong kết quả + lời lỗi thành nhãn thật của công ty người gọi (không có nhãn riêng ⇒ trung tính).
    Một chỗ cho mọi thao tác — module chỉ viết ``[[khoá]]``, không tự thay chữ. Cũng là cửa ra QUYỀN THEO KHỐI (lớp 3, 1.59): phần khai ``needs=`` mà
    người gọi không đủ quyền bị bỏ ở đây (``needs.cut``) — một chỗ cho web, app, trợ lý, xuất Excel, bản chốt."""
    return Result(r.status, vocab.apply(needs.cut(p, r.output), p.vocab), vocab.resolve(r.error, p.vocab), r.approval_id, r.replayed)


async def _mark_failed(p: Principal, on_fail: Callable[[Conn, Result], Awaitable[None]], r: Result) -> None:
    try:
        async with db.tenant_tx(p) as conn:
            await on_fail(conn, r)
    except Exception:
        log.exception("không cập nhật được trạng thái lỗi cho thẻ đang chạy")


async def run(p: Principal, name: str, inp: dict[str, Any], *, key: str | None = None, quiet: bool = False,
              expect_basis: dict[str, str] | None = None, edited: bool = False, files: dict[str, bytes | bytearray] | None = None,
              attended: bool = False, background: bool = False, keep_output: bool = True, historical: bool = False,
              load_key: bool = False,
              dry_run: bool = False, claim: Callable[[Conn, bool], Awaitable[Result | None]] | None = None,
              on_prepare: Callable[[Conn], Awaitable[None]] | None = None,
              on_done: Callable[[Conn, Result], Awaitable[None]] | None = None,
              on_fail: Callable[[Conn, Result], Awaitable[None]] | None = None) -> Result:
    """``dry_run``: CHỈ quyền hệ thống (cửa nhà vận hành — 0206): kiểm và chạy handler thật trong điểm lưu rồi HOÀN — không ghi gì (kể cả nhật ký, khoá
    chống lặp, sự kiện); kết quả trạng thái ``dry_run`` mang đầu ra của lần chạy thử.
    ``historical``: CHỈ mã trong tiến trình (lớp nạp dữ liệu hệ cũ — 0185): lệnh, gọi lồng và mọi sự kiện nó phát chạy ở chế độ nạp lịch sử.
    ``load_key``: CHỈ lớp nạp dữ liệu hệ cũ (``kernel/onboard`` — cần ``historical``): khoá chống lặp thuộc LƯỢT NẠP của công ty (``load:<khoá>``), không thuộc
    người gọi ⇒ lượt bị ngắt mà lượt sau do người khác chạy (đổi người chạy của một thực thể) vẫn nhận lại kết quả cũ, không tạo trùng. Không gian ``load:``
    không trùng được với khoá của người (``<mã người>:…``) hay ``system:…``.
    ``background``: CHỈ lõi (vòng quét) — lượt VIỆC NỀN, có hợp đồng thuê riêng cho ``ctx.job_for``, hết khi lệnh xong.
    ``keep_output``: False ⇒ bảng chống lặp chỉ giữ trạng thái (+ mã phiếu duyệt), không giữ kết quả — cho đường nhập hàng loạt, nơi người gọi chỉ đọc trạng thái
    / lỗi của từng dòng (``imports._run_rows``) và khoá là băm nội dung dòng; lần gọi lặp vẫn nhận ``replayed`` + đúng trạng thái, cùng khoá khác nội dung vẫn ``conflict`` (0177).
    ``claim`` / ``on_prepare`` / ``on_done`` / ``on_fail``: CHỈ đường xác nhận thẻ đề xuất (``morning.decide``, ``chat.decide``) — khoá dòng thẻ
    (``FOR UPDATE``) và đóng thẻ trong CÙNG một giao dịch / MỘT kết nối với lệnh (không giữ kết nối 1 rồi mượn kết nối 2). Với lệnh có ``op.prepare``
    gọi hệ ngoài: ``on_prepare`` ghi nhận thẻ ``running`` và commit TRƯỚC khi nhả kết nối gọi mạng (chặn Bỏ / Xác nhận thứ hai chen vào giữa);
    ``on_fail`` đóng thẻ khi lỗi, yêu cầu đối soát nếu kết quả ghi ngoài chưa rõ. Cả hai trường hợp đều chặn bấm chạy lại."""
    door(p, f"run {name}")
    if dry_run and not p.system:
        return Result("denied", error="Chạy thử không ghi chỉ dành cho lệnh vận hành chạy bằng quyền hệ thống")
    if name in DECISIONS and not (principal.live_edge(p) or _LOAD_DECIDER.get() is p):
        return Result("denied", error="Quyết định duyệt chỉ do người bấm trong app (phiên đăng nhập của chính request này) — 0144")
    if load_key and not historical:
        raise TypeError("load_key chỉ dùng cho lớp nạp lịch sử (historical=True)")
    lease = principal.Lease() if background else None
    token = HISTORICAL.set(True) if historical else None   # chỉ BẬT: lệnh chạy bên trong một lần nạp lịch sử thừa hưởng, không tắt được
    command = _COMMAND.set(name)
    try:
        with timing.span("executor"):
            return localized(p, await _run(p, name, inp, key=key, quiet=quiet, expect_basis=expect_basis, edited=edited, files=files, attended=attended,
                                           background=lease, keep_output=keep_output, dry_run=dry_run, claim=claim, on_prepare=on_prepare,
                                           on_done=on_done, on_fail=on_fail, load_key=load_key))
    finally:
        _COMMAND.reset(command)
        if token is not None:
            HISTORICAL.reset(token)
        if lease is not None:
            lease.active = False


async def _run(p: Principal, name: str, inp: dict[str, Any], *, key: str | None = None, quiet: bool = False,
               expect_basis: dict[str, str] | None = None, edited: bool = False, files: dict[str, bytes | bytearray] | None = None,
               attended: bool = False, background: principal.Lease | None = None, keep_output: bool = True,
               prepared: Any = _UNPREPARED, dry_run: bool = False, claim: Callable[[Conn, bool], Awaitable[Result | None]] | None = None,
               on_prepare: Callable[[Conn], Awaitable[None]] | None = None,
               on_done: Callable[[Conn, Result], Awaitable[None]] | None = None,
               on_fail: Callable[[Conn, Result], Awaitable[None]] | None = None, claimed: bool = False, load_key: bool = False) -> Result:
    """Chạy một LỆNH (ghi) trong giao dịch riêng. ``key``: khoá chống lặp (cùng khoá + cùng dữ liệu ⇒ trả kết quả cũ).
    ``quiet``: cho vòng quét định kỳ — kết quả là dict toàn số 0 (không có gì xảy ra) thì KHÔNG ghi nhật ký thao tác.
    ``attended``: trợ lý đang làm trong lượt CÓ NGƯỜI — MẶC ĐỊNH False, chỉ lượt chat (``agent/chat.py: ToolRun`` với ``defer=True``) bật; mọi
    đường khác mang quyền trợ lý (việc theo dõi chạy nền, đề xuất buổi sáng, MCP, bản tin, gọi lồng) không được chạy thẳng lệnh đã nới (0143):
    đầu vào có thể chứa chữ người ngoài nộp qua link (review an ninh 08/11). Chỉ áp cho người là trợ lý; người thật không bị ảnh hưởng.
    ``expect_basis``: căn cứ người bấm đã thấy (thẻ đề xuất, 0071) — khác căn cứ hiện tại ⇒ ``conflict``, không chạy; ``edited``: người đã sửa
    đầu vào trên thẻ ⇒ chỉ so căn cứ cùng nhãn."""
    op = registry.get(name)
    if op is None or op.kind != "command" or (op.internal and not p.system):
        return Result("not_found", error=f"Không có lệnh {name}")
    if p.packs is not None and (pack := packs.missing(p.packs, op)) is not None:
        return Result("not_found", error=packs.off_message(pack))
    if not allowed(p, op):
        await _audit_alone(p, name, inp, "denied", _deny_message(op))
        return Result("denied", error=_deny_message(op))
    try:
        model = validated(p, op, inp)
    except ValidationError as e:
        return Result("invalid", error=explain(e, op.input))
    except Invalid as e:   # trường tuỳ biến lạ / sai kiểu (0060)
        return Result("invalid", error=str(e))
    payload = model.model_dump(mode="json")
    if p.via == "app" and not key:
        # app từ xa (0140) gọi qua mạng, có thể gửi lại khi mất phản hồi ⇒ lệnh ghi BẮT BUỘC khoá chống lặp (lần gửi lại nhận đúng kết quả cũ)
        return Result("invalid", error="Ứng dụng phải gửi Idempotency-Key cho mọi lệnh ghi")
    autorun = False   # trợ lý chạy THẲNG một lệnh được công ty nới (0143) — nhật ký ghi ref="autorun"
    given_key = key   # khoá như người gọi gửi — lần chạy lại sau bước chuẩn bị nhận đúng khoá này (bên dưới ``key`` thành khoá theo người gọi)
    need_prepare = False
    res: Result | None = None
    try:
        async with db.tenant_tx(p, consistent=op.consistent) as conn:
            # ``claimed``: pha 2 của một thẻ ĐÃ chiếm ``running`` trước khi gọi ngoài (bước chuẩn bị chỉ đọc không chiếm ⇒ pha 2 vẫn thấy thẻ đang chờ)
            if claim is not None and (short := await claim(conn, claimed)) is not None:
                return short
            # lệnh ghi: khoá chia sẻ trạng thái gói + đọc gói thật (đổi gói khoá độc quyền ⇒ không lọt lệnh của gói vừa tắt)
            await packs.require_for_write(conn, p.tenant_id, op)
            if p.via == "agent":
                # Quyền của agent ≤ quyền người (cùng principal, cùng RLS). Thêm rào chế độ trợ lý — MỘT nơi quyết: agent_policy.decide (0144).
                company, caller = await company_for(conn, op, within_caps=True), _caller(p, attended=attended)
                d = decide(op, caller, company)   # chưa đếm trần: hỏi trước có cần đếm không
                if d.mode == "deny":
                    raise Denied(f"Trợ lý không được làm “{op.title}” — việc này người phải tự làm ({'; '.join(d.why)})")
                if d.mode == "allow" and op.agent != "allow":   # được nới / công tắc (0143 / 0144): đếm trần trong cùng giao dịch rồi quyết lại
                    d = decide(op, caller, Company(company.overrides, await _autorun_ok(conn, op, payload), company.autorun_low_risk))
                if d.mode == "propose":
                    return await _propose(conn, p, op, model, payload, why=d.why)
                autorun = op.agent != "allow"
            if key:
                # Khoá chống lặp thuộc NGƯỜI GỌI (hai người trùng khoá không nhận kết quả của nhau) và được GIỮ trước khi đọc:
                # hai request cùng khoá tới cùng lúc ⇒ request sau chờ request trước commit rồi trả lại kết quả đó, không chạy
                # lần hai, không lỗi trùng khoá.
                key = scoped_key(p, key, shared=load_key)
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"idem:{p.tenant_id}:{key}",))
                cur = await conn.execute("SELECT command, input_hash, status, output FROM core.idempotency WHERE key = %s", (key,))
                prev = await cur.fetchone()
                if prev is not None:
                    if prev["command"] != name or prev["input_hash"] != _input_hash(payload):
                        return Result("conflict", error="Khoá chống lặp đã dùng cho dữ liệu khác")
                    out = prev["output"] or {}
                    replayed_res = Result(prev["status"], out, approval_id=out.get("approval_id") if isinstance(out, dict) else None, replayed=True)
                    if on_done is not None and replayed_res.status in ("ok", "pending_approval"):
                        await on_done(conn, replayed_res)
                    return replayed_res
            ctx = core_ctx(conn, p, module=op.module, files=files or {}, background=background,
                           prepared=None if prepared is _UNPREPARED else prepared)
            status, out, note = "ok", None, ""
            # chạy thử (cửa nhà vận hành ``--dry-run``) KHÔNG chạy bước chuẩn bị: bước đó gọi ra ngoài (đổi quảng cáo, gửi chuyển đổi, gọi hãng giao…)
            # và không hoàn được ⇒ handler chạy với ``ctx.prepared = None`` (nhánh "chưa chuẩn bị" — không gửi gì) trong điểm lưu rồi hoàn
            ready = op.prepare is None or prepared is not _UNPREPARED or dry_run   # handler sắp chạy thật mà chưa chuẩn bị ⇒ _Prepare
            # Thẻ đã chiếm quyền thực thi trước khi gọi hệ ngoài. Căn cứ và luật duyệt chốt ở pha đó.
            # Pha ghi kết quả không sinh phiếu duyệt cho một thay đổi đã gửi.
            finishing_claim = claimed
            chain, what = (None, "") if finishing_claim else await approval_for(ctx, op, model)
            if chain and p.via == "app":
                # app không gửi phiếu duyệt: lệnh sau duyệt chạy lại bằng quyền NGƯỜI xin — app không phải thành viên để nạp lại (0140)
                raise Denied(f"“{op.title}” phải qua duyệt ở công ty này — ứng dụng không gửi phiếu duyệt; người trong công ty làm lệnh này")
            if chain and await approvals.sole_owner(conn, p, chain):
                # chủ tài khoản DUY NHẤT tự đổi: không ai trên để duyệt (0029) ⇒ chạy ngay, ghi rõ trong nhật ký, báo mọi quản trị
                chain, note = None, approvals.SOLE_OWNER_NOTE + (f": {what}" if what else "")
            basis = None
            if not finishing_claim:
                if chain or expect_basis is not None or op.basis_required:
                    basis = await basis_of(ctx, op, model)
                if op.basis_required and expect_basis is None and basis:
                    raise Conflict(op.basis_required)   # 0251: lệnh chốt thứ người bấm phải nhìn trước
                if changed := basis_changed(expect_basis, basis, shared_only=edited):
                    raise Conflict(f"Căn cứ đã đổi từ lúc bạn xem: {', '.join(changed)} — xem lại rồi làm lại")
            if chain:
                brief = await _brief_for_approval(conn, p, op, model, payload)
                if what and not brief["changes"]:   # mô tả của luật duyệt (``sensitive``) là "đổi gì" — thẻ phiếu hiện nó như dòng thay đổi
                    brief = {**brief, "changes": [{"label": "Nội dung đổi", "before": "", "after": what}]}
                aid = await approvals.create(conn, p, chain=chain, command=name, input=payload,
                                             summary=brief["title"] + (f" — {what}" if what else ""), preview=brief,
                                             run_as="requester", basis=basis)
                ctx.pending_approval = aid
                out = {"approval_id": str(aid), "message": "Đã gửi phiếu duyệt"}
            elif not ready:
                if on_prepare is None or not op.prepare_writes:
                    raise _Prepare()   # không có thẻ để chiếm, hoặc bước chuẩn bị chỉ đọc (lỗi thì thẻ vẫn chờ, bấm lại được — rà 22/11)
                # chiếm thẻ nguyên tử sang 'running' và COMMIT giao dịch 1 TRƯỚC khi nhả kết nối gọi hệ ngoài (chặn Bỏ / Xác nhận thứ hai chen vào)
                await on_prepare(conn)
                need_prepare = True
            elif dry_run:
                # cửa nhà vận hành ``--dry-run`` (0206): chạy thật trong điểm lưu rồi hoàn — ra đúng lỗi / kết quả như chạy thật, không để lại gì
                async with conn.transaction():
                    out = jsonable(await op.handler(ctx, model))
                    await check_deferred(conn)
                    raise psycopg.Rollback()
                return Result("dry_run", out)
            elif p.via == "agent":
                # trợ lý được làm thẳng — trừ khi bên trong gọi lồng lệnh công ty đặt "đề xuất": hoàn phần đã chạy, cả lệnh thành đề xuất
                try:
                    async with conn.transaction():
                        out = jsonable(await op.handler(ctx, model))
                except NeedsConfirmation:
                    return await _propose(conn, p, op, model, payload)
                await flush_events(conn, p, ctx.events)
            else:
                out = jsonable(await op.handler(ctx, model))
                await flush_events(conn, p, ctx.events)
            if not need_prepare:
                if note:
                    await approvals.tell_admins_self_approved(conn, p, op.title + (f" — {what}" if what else ""))
                if ctx.pending_approval:
                    status = "pending_approval"
                if op.resource in AUTH_RESOURCES and status == "ok":
                    # lệnh quản trị có thể đổi quyền / thành viên / gói ⇒ báo các kênh tức thì đang giữ quyền nạp sẵn (0115) — gửi khi commit
                    await conn.execute("SELECT pg_notify('barista_auth', %s)", (str(p.tenant_id),))
                if not (quiet and isinstance(out, dict) and not any(out.values())):
                    await _audit(conn, p, name, payload, status, error=note, ref=str(ctx.pending_approval or (autorun_ref(op, payload, out) if autorun else "")))
                if key:
                    await conn.execute(
                        "INSERT INTO core.idempotency (key, command, input_hash, status, output) VALUES (%s, %s, %s, %s, %s)",
                        (key, name, _input_hash(payload), status, Jsonb(_replayable(name, out) if keep_output else _status_only(out))))
                res = Result(status, out, approval_id=str(ctx.pending_approval) if ctx.pending_approval else None)
                if on_done is not None and res.status in ("ok", "pending_approval"):
                    await on_done(conn, res)
        if not need_prepare:
            assert res is not None
            _WRITES[p.tenant_id] = writes(p.tenant_id) + 1   # đã commit (ra khỏi giao dịch) — xem ``writes``
            await run_after(ctx)
            return res
    except _Prepare:
        pass   # giao dịch đã hoàn (chưa ghi gì) — chuẩn bị bên dưới, NGOÀI giao dịch, rồi chạy lại với kết quả
    except Exception as e:   # mọi lỗi của lệnh ⇒ Result đã phân loại (map_error) + nhật ký; không ném tiếp ra ngoài
        r = map_error(e, op.input)
        if dry_run:
            return r   # chạy thử không ghi gì — kể cả nhật ký lỗi (người vận hành đọc lỗi ngay ở dòng lệnh)
        if r.status == "error":
            await _system_error(p, name, payload, r, e)
        else:
            await _audit_alone(p, name, payload, r.status, r.error)
        return r
    # ADR 0200: nhả kết nối trước khi gọi mạng. Thẻ đã chiếm được ghi kết quả trong một giao dịch mới.
    assert op.prepare is not None
    prepared_outside = False
    try:
        async with asyncio.timeout(previews.EXTERNAL_DECISION_S if need_prepare else None):
            value = await op.prepare(_prep(p, op.module, files), model)
            prepared_outside = True
            finished = await _run(p, name, inp, key=given_key, quiet=quiet, expect_basis=expect_basis, edited=edited, files=files, attended=attended,
                                  background=background, keep_output=keep_output, prepared=value, dry_run=dry_run, claim=claim, on_prepare=on_prepare,
                                  on_done=on_done, on_fail=on_fail, claimed=need_prepare, load_key=load_key)
    except asyncio.CancelledError:
        if need_prepare and on_fail is not None:
            await asyncio.shield(_mark_failed(p, on_fail, Result(previews.RECONCILIATION, error=previews.RECONCILIATION_LABEL)))
        raise
    except Exception as e:
        r = map_error(e, op.input)
        if need_prepare and on_fail is not None:
            # HỢP ĐỒNG phân loại (rà 22/11): bước chuẩn bị TỰ TỪ CHỐI bằng lỗi nghiệp vụ (``Invalid`` / ``Denied`` / ``Conflict``… — map_error ra trạng thái
            # khác "error") là chắc chắn CHƯA gửi ⇒ ``failed``. Mọi lỗi khác (mạng, hết giờ kể cả timeout của thư viện HTTP, lỗi lạ) hoặc lỗi SAU khi bước
            # chuẩn bị đã xong ⇒ không biết hệ ngoài đã nhận chưa ⇒ cần đối soát, không bao giờ coi là "chưa làm".
            definite = not prepared_outside and r.status != "error"
            await _mark_failed(p, on_fail, r if definite else Result(previews.RECONCILIATION, error=r.error))
        if r.status == "error":
            await _system_error(p, name, payload, r, e)
        else:
            await _audit_alone(p, name, payload, r.status, r.error)
        return r
    if finished.status not in ("ok", "pending_approval") and need_prepare and on_fail is not None:
        await _mark_failed(p, on_fail, Result(previews.RECONCILIATION, error=finished.error))
    if finished.status == previews.RECONCILIATION:
        log.error("%s: hệ ngoài đã nhận lệnh nhưng phần ghi trong Barista không chạy (thẻ đã đóng) — cần đối soát tay", name)
    return finished


async def decide_in_load(approver: Principal, approval_id: UUID | str, *, approve: bool = True, note: str = "") -> Result:
    """LỐI QUYẾT PHIẾU CỦA LỚP NẠP (ADR 0263, bổ sung 28/11/2026) — CHỈ ``kernel/onboard`` gọi, mã trong tiến trình như ``historical``: không có ở cửa HTTP,
    trợ lý không có, không gọi lồng được. Người duyệt là một NGƯỜI có thật của công ty, quyền nạp từ DB (tay cầm CORE; không quyền hệ thống, không trợ lý,
    không app / link); lệnh chạy là ĐÚNG ``core.decide`` — vẫn kiểm người đó thuộc bước duyệt hiện tại (người xin không bao giờ nằm trong đó — bất biến 6),
    vẫn ghi tên người duyệt vào phiếu + nhật ký, lệnh đích vẫn chạy bằng quyền người xin nạp lại. Cả lượt ở chế độ nạp lịch sử (không thông báo)."""
    if not principal.is_issued(approver, principal.CORE) or approver.system or approver.via != "human" or approver.user_id is None \
            or approver.link_ref is not None:
        return Result("denied", error="Quyết phiếu trong lượt nạp: người duyệt phải là một thành viên có thật của công ty (không quyền hệ thống / trợ lý / app)")
    token = _LOAD_DECIDER.set(approver)
    try:
        return await run(approver, next(iter(DECISIONS)), {"approval_id": str(approval_id), "approve": approve, "note": note}, historical=True)
    finally:
        _LOAD_DECIDER.reset(token)


async def propose(p: Principal, name: str, inp: dict[str, Any], *, reason: str = "") -> Result:
    door(p, f"propose {name}")
    return localized(p, await _propose_as(p, name, inp, reason))


async def _propose_as(p: Principal, name: str, inp: dict[str, Any], reason: str = "") -> Result:
    """Soạn ĐỀ XUẤT bằng quyền ``p`` (thường ``p.as_agent()``) dù chính sách cho trợ lý "làm thẳng" — cho đề xuất soạn sẵn không có người đang
    nhìn (đề xuất buổi sáng, 0074): luôn chỉ chạy thử rồi bỏ; lệnh trợ lý bị cấm ("deny") vẫn bị cấm. Kết quả: ``proposed`` hoặc lỗi như ``run``."""
    op = registry.get(name)
    if op is None or op.kind != "command" or op.internal:
        return Result("not_found", error=f"Không có lệnh {name}")
    if p.packs is not None and (pack := packs.missing(p.packs, op)) is not None:
        return Result("not_found", error=packs.off_message(pack))
    if not allowed(p, op):
        return Result("denied", error=_deny_message(op))
    try:
        model = validated(p, op, inp)
    except ValidationError as e:
        return Result("invalid", error=explain(e, op.input))
    except Invalid as e:   # trường tuỳ biến lạ / sai kiểu (0060)
        return Result("invalid", error=str(e))
    payload = model.model_dump(mode="json")
    try:
        async with db.tenant_tx(p) as conn:
            await packs.require_for_write(conn, p.tenant_id, op)
            if p.via == "agent" and await agent_denied(conn, p, op):
                raise Denied(f"Trợ lý không được làm “{op.title}” — việc này người phải tự làm")
            return await _propose(conn, p, op, model, payload, reason=reason)
    except Exception as e:   # mọi lỗi của lệnh ⇒ Result đã phân loại + nhật ký (_failed), không ném tiếp
        return await _failed(p, name, payload, e, op.input)


async def query(p: Principal, name: str, inp: dict[str, Any] | None = None, *, quiet: bool = False, later: bool = False,
                part: str | None = None, page: ui.Paging | None = None, count: bool = True) -> Result:
    """``later``: bên gọi tự tải khối tải sau (``ui.b_later`` / ``ui.part`` — web qua ``?later=1``); mặc định executor chạy luôn các khối đó và ghép
    vào thẻ. ``part``: chỉ dựng một phần ``ui.part`` của thẻ (web tải phần đó — ``?part=``). ``page``: bên gọi xin MỘT trang của bảng (``?limit=``
    / ``?offset=``) — xem ``ui.slice_page`` / ``ui.paging``. ``count=False``: không tính vào bộ đếm lượt dùng (thẻ gọn tự tải của bàn việc — không phải người mở màn)."""
    door(p, f"query {name}")
    with timing.span("executor"):
        r = localized(p, await _query_with(p, name, inp, quiet=quiet, parts=ui._Parts(name, dict(inp or {}), part) if part or later else None, page=page, count=count))
    if page is not None and r.ok:
        r = Result(r.status, ui.slice_page(r.output, page), approval_id=r.approval_id)
    if not later and r.ok:
        r = await _fill_later(p, r, 0)
    return r


_COUNT: ContextVar[bool] = ContextVar("query_counted", default=True)   # lượt này có vào bộ đếm lượt dùng không (``query(count=False)`` — thẻ gọn tự tải)


async def _query_with(p: Principal, name: str, inp: dict[str, Any] | None, *, quiet: bool = False, parts: ui._Parts | None = None,
                      page: ui.Paging | None = None, count: bool = True) -> Result:
    """``_query`` với chế độ phần tải sau / trang của lượt này (biến ngữ cảnh — handler đọc qua ``ui.part`` / ``ui.paging``)."""
    t1, t2, t3 = ui._PARTS.set(parts), ui._PAGING.set(page), _COUNT.set(count)
    try:
        return await _query(p, name, inp, quiet=quiet)
    finally:
        ui._PARTS.reset(t1)
        ui._PAGING.reset(t2)
        _COUNT.reset(t3)


LATER_DEPTH = 2   # khối tải sau trỏ tới thẻ lại có khối tải sau: mở tối đa 2 tầng (thẻ tự trỏ vào mình không lặp mãi)


async def _fill_later(p: Principal, r: Result, depth: int) -> Result:
    """Thay mỗi khối ``later`` bằng các khối của truy vấn nó trỏ (cùng người gọi, qua đúng cửa ``query``) — lần lượt theo thứ tự trong thẻ."""
    out = r.output
    if not isinstance(out, dict) or out.get("kind") != "card" or not any(isinstance(b, dict) and b.get("type") == "later" for b in out.get("blocks") or []):
        return r
    blocks: list[Any] = []
    for b in out["blocks"]:
        if not (isinstance(b, dict) and b.get("type") == "later"):
            blocks.append(b)
            continue
        if depth >= LATER_DEPTH:
            continue
        q, sub_inp, want = str(b.get("query") or ""), b.get("input") or {}, b.get("part")
        sub = localized(p, await _query_with(p, q, sub_inp, parts=ui._Parts(q, dict(sub_inp), str(want)) if want else None))
        if sub.ok:
            sub = await _fill_later(p, sub, depth + 1)
        if sub.ok and isinstance(sub.output, dict) and sub.output.get("kind") == "card":
            blocks += sub.output.get("blocks") or []
        elif sub.status == "error":
            blocks.append(ui.b_callout("muted", b.get("title") or "Phần này chưa tải được", "Tạm thời chưa tải được phần này — mở lại màn sau ít phút."))
        # không có quyền / không có truy vấn / gói tắt ⇒ bỏ phần đó (như ``call_optional``)
    return Result(r.status, {**out, "blocks": blocks}, approval_id=r.approval_id)


async def _query(p: Principal, name: str, inp: dict[str, Any] | None = None, *, quiet: bool = False) -> Result:
    """Chạy một TRUY VẤN (đọc) trong giao dịch READ ONLY. ``quiet``: kiểm tra lặp lại của kênh tức thì (mỗi người nghe, mỗi tín hiệu) — lỗi bất ngờ
    (DB bận, mất kết nối) trả ``error`` cho bên gọi tự thử lại, KHÔNG ghi lỗi hệ thống / báo nhà vận hành mỗi lần (một sự cố thành nghìn dòng)."""
    op = registry.get(name)
    if op is None or op.kind != "query" or (op.internal and not p.system):
        return Result("not_found", error=f"Không có truy vấn {name}")
    if p.packs is not None and (pack := packs.missing(p.packs, op)) is not None:
        return Result("not_found", error=packs.off_message(pack))
    if not allowed(p, op):
        return Result("denied", error=_deny_message(op))
    try:
        model = op.input.model_validate(inp or {})
        prepared = None
        if op.prepare is not None and op.family == "plug":
            # phần cắm gọi nhà cung cấp (1.37 — ``Module.plug(prepare=)``): gói phải đang bật TRƯỚC khi gọi mạng (giao dịch đọc ngắn), rồi bước gọi mạng
            # chạy ngoài mọi giao dịch, đọc DB bằng ``Prep.query``
            async with db.tenant_tx(p, read_only=True) as conn:
                await packs.require(conn, p.tenant_id, op)
            prepared = await op.prepare(_prep(p, op.module), model)
        elif op.prepare is not None:
            prepared = await op.prepare(p, model)   # gọi mạng NGOÀI giao dịch (không giữ kết nối)
        async with db.tenant_tx(p, read_only=True) as conn:
            await packs.require(conn, p.tenant_id, op)
            if p.via == "agent" and await agent_denied(conn, p, op):
                raise Denied(f"Trợ lý không được xem “{op.title}” theo chính sách công ty")
            ctx = core_ctx(conn, p, module=op.module, prepared=prepared)
            with timing.span("handler"):
                out: Any = None
                try:
                    out = await op.handler(ctx, model)
                except ui.PartDone:
                    pass
                if (parts := ui._PARTS.get()) is not None and parts.want is not None:   # chỉ dựng một phần (``ui.part``): thẻ = các khối của phần đó
                    out = ui.card("", parts.got or [])
            zone = clock.zone()   # múi giờ công ty của giao dịch (0169) — bộ đếm mức dùng ghi theo NGÀY của công ty
        await run_after(ctx)   # việc hẹn sau khi đọc xong (vd ghi dấu mục nhớ đã được đưa ra) — giao dịch đọc không ghi được
        if not quiet and _COUNT.get():
            try:
                with clock.using(zone):
                    usage.note_query(p, op.module, op.name)   # mức dùng (0163): chỉ tăng bộ đếm trong bộ nhớ, không SQL; lỗi gì cũng không được làm hỏng truy vấn
            except Exception:
                log.exception("bộ đếm mức dùng lỗi")
        return Result("ok", visible_actions(p, jsonable(out)))
    except Exception as e:   # lỗi tạm của truy vấn 'quiet' ⇒ báo thử lại (đã log); còn lại ⇒ Result đã phân loại (_failed)
        if quiet and not isinstance(e, OpError | ValidationError):
            log.warning("truy vấn %s lỗi tạm thời: %s", name, type(e).__name__)
            return Result("error", error="Tạm thời không kiểm được — thử lại sau")
        return await _failed(p, name, inp or {}, e, op.input)


def raise_for(r: Result) -> Any:
    """Tiện cho seed / test: lỗi thì ném."""
    if not r.ok:
        raise Invalid(f"{r.status}: {r.error}")
    return r.output
