"""Người đang gọi — đọc từ DB (memberships + grants + cây tổ chức), KHÔNG BAO GIỜ từ client."""

import copy
import weakref
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import UUID

ACTIONS = ("read", "write", "approve", "manage")
# own < unit (đội mình THAM GIA, 0125 §A) < team (đội mình làm trưởng + cây con, 0062) < all. Thứ tự là thứ tự rộng dần; `unit` không phải con của `team`.
SCOPES = ("own", "unit", "team", "all")


@dataclass(frozen=True, slots=True)
class Grant:
    scope: str
    actions: frozenset[str]


@dataclass(frozen=True)
class Principal:
    tenant_id: UUID
    user_id: UUID | None
    email: str = ""
    name: str = ""
    role: str = ""
    role_label: str = ""
    grants: Mapping[str, Grant] = field(default_factory=dict)
    # Ba câu về đội, ý nghĩa riêng (kernel/teams.py): đội chính · đội tham gia · đội quản lý (làm trưởng + cây con = phạm vi "team")
    home_team: UUID | None = None
    member_teams: tuple[UUID, ...] = ()
    managed_teams: tuple[UUID, ...] = ()
    system: bool = False  # bên nghe sự kiện / lệnh đã duyệt chạy hộ: thấy cả công ty
    approved_by: tuple[UUID, ...] = ()
    # PHẠM VI phiếu đã duyệt (0066): lệnh + chuỗi duyệt của phiếu. Chỉ miễn luật duyệt cho đúng lệnh đó, hoặc lệnh lồng cùng chuỗi người duyệt
    approved_scope: tuple[str, str] | None = None
    via: str = "human"  # "agent": trợ lý làm thay — quyền y như người (≤ người), thêm rào chế độ agent ở executor
    link_target: str | None = None  # người qua link công khai (via="link"): loại link + đối tượng link trỏ tới — không phải thành viên (0064)
    link_ref: UUID | None = None
    ai: bool = True  # công ty cho phép trợ lý AI (core.tenant_profile.ai_enabled) — tắt ⇒ llm.model_for không dựng model nào
    ai_block: str = ""  # khác rỗng = công ty (0092) hoặc chính người này (0193) đã dùng hết hạn mức AI tháng này — llm.model_for từ chối, câu này là lời báo
    # HẠNG trợ lý AI của người (ADR 0193 — core.ai_tiers theo vai): khoá hạng · model hạng dùng ("" = model mặc định của bản cài) · được nói chuyện bằng
    # giọng không (mặc định KHÔNG — người gọi không nạp từ DB không mở được Gemini Live). Hạn mức theo người gộp vào ``ai_block``.
    ai_tier: str = ""
    ai_model: str = ""
    ai_voice: bool = False
    ai_fallback: bool = False  # công ty cho phép nhà cung cấp AI THỨ HAI (cài đặt ``core.ai_allow_fallback_vendor``, mặc định tắt) — llm.model_for chỉ nối chuỗi dự phòng khi bật
    settings: Mapping[str, bool] | None = None  # cài đặt công ty ĐÃ CÓ DÒNG (0128): khoá → bật / tắt; None / thiếu khoá ⇒ mặc định khai trong code (``company_settings.on``)
    packs: frozenset[str] | None = None  # gói nghiệp vụ công ty đang bật (0045); None = chưa nạp (executor tự tra)
    app_id: UUID | None = None  # app từ xa (via="app", 0140): app nào đang gọi — mã ngoài / chống lặp gắn theo app
    shadow: bool = False  # app đang CHỈ GHI NHẬN (0207 — người vận hành bật): lệnh của app chạy như nạp lịch sử (không báo, không tính hạn, không webhook)
    fields: Mapping[str, tuple[Any, ...]] | None = None  # trường tuỳ biến công ty đang dùng (0060): đối tượng → định nghĩa (kernel/extra_fields.FieldDef); None = không có
    vocab: Mapping[str, str] | None = None  # nhãn riêng của công ty (0124): khoá khái niệm → nhãn; None = nhãn trung tính (quyền hệ thống, link công khai)

    def can(self, resource: str | None, action: str) -> bool:
        """``resource=None``: thao tác của mọi thành viên (hộp duyệt, hồ sơ bản thân) — handler tự kiểm dòng."""
        if self.system or resource is None:
            return True
        g = self.grants.get(resource)
        return g is not None and action in g.actions

    def scope(self, resource: str) -> str:
        if self.system:
            return "all"
        g = self.grants.get(resource)
        return g.scope if g else "none"

    def has_scope(self, resource: str | None, minimum: str | None) -> bool:
        """Báo cáo toàn công ty cần phạm vi "all" — người phạm vi own chạy sẽ ra số thiếu mà tưởng là đủ."""
        if minimum is None or resource is None or self.system:
            return True
        rank = {"none": 0, **{s: i for i, s in enumerate(SCOPES, 1)}}
        return rank[self.scope(resource)] >= rank[minimum]

    @property
    def scopes(self) -> dict[str, str]:
        return {r: g.scope for r, g in self.grants.items()}

    def as_agent(self) -> Principal:
        """Trợ lý làm thay người này: CÙNG quyền, cùng phạm vi dòng — chỉ đánh dấu để executor áp chế độ agent. Giữ LOẠI cấp của bản gốc; bản tự dựng
        đi qua đây vẫn là bản tự dựng (không "rửa" ``replace(p, system=True).as_agent()`` thành hợp lệ). Không có ``as_system``: nâng quyền hệ thống
        chỉ lõi làm (``elevate`` — ``ctx.call_system`` sau khi kiểm hợp đồng ``callers=``)."""
        agent = replace(self, via="agent")
        r = _record(self)
        return issued(agent, kind=r.kind, lease=r.lease) if r else agent

    def session_key(self) -> tuple[object, ...]:
        """Những gì quyết định biến phiên DB — đổi thì phải đặt lại set_config."""
        return (self.tenant_id, self.user_id, self.email, self.managed_teams, self.member_teams, tuple(sorted(self.scopes.items())), self.system, self.link_ref)


def elevate(p: Principal, *, approved_by: tuple[UUID, ...] = ()) -> Principal:
    """Nâng quyền hệ thống — CHỈ lõi gọi (executor: ``call_system`` sau khi kiểm hợp đồng ``callers=``, căn cứ của lệnh). Bỏ dấu link: RLS theo quyền
    hệ thống, không theo phạm vi của link (0064). Giữ ``via``: nâng quyền từ trợ lý vẫn chịu chính sách trợ lý của công ty (0066)."""
    return issued(replace(p, system=True, approved_by=approved_by or p.approved_by, link_ref=None), kind=CORE)


def system_principal(tenant_id: UUID, user_id: UUID | None = None, *, approved_by: tuple[UUID, ...] = ()) -> Principal:
    return issued(Principal(tenant_id=tenant_id, user_id=user_id, name="Hệ thống", system=True, approved_by=approved_by), kind=CORE)


# NGƯỜI GỌI DO LÕI CẤP (0130). QUYỀN NẰM Ở SỔ RIÊNG CỦA LÕI, ĐỐI TƯỢNG CHỈ LÀ TAY CẦM: lúc cấp, lõi chép SÂU người gọi (bản chụp không ai khác
# giữ) + loại cấp + "hợp đồng thuê" (cửa HTTP). Mọi chỗ kiểm (cửa executor, Ctx, gọi lồng, route) so tay cầm với bản chụp — sửa tại chỗ
# (``object.__setattr__(p, "system", True)``, đổi ``p.grants[...]``), chép (``copy``, ``replace``, ``pickle``), lớp con ⇒ không khớp ⇒ từ chối.
#   EDGE     — cấp ở CỬA HTTP / phiên (web.current, app từ xa, link). Gắn với REQUEST đã cấp: hết request ⇒ hết hạn (``Lease``). Route của gói
#              (``web.run`` / ``web.query``) CHỈ nhận loại này, còn hạn.
#   CORE     — lõi tự dựng: nạp từ DB (auth.load), quyền hệ thống (system_principal, elevate), phiếu duyệt chạy lại, vòng quét, lượt trợ lý.
#   DELEGATE — ``ctx.principal_of``: quyền NGƯỜI KHÁC, chỉ để HỎI HỘ qua ``ctx.call_as`` — KHÔNG BAO GIỜ chạy lệnh (ở mọi Ctx); truy vấn của người
#              khác chỉ khi là truy vấn nội bộ, hoặc bên gọi chạy bằng quyền hệ thống. Cửa executor và route từ chối.
#   JOB      — ``ctx.job_for``: lõi cấp cho việc NỀN đang chạy bằng quyền hệ thống (bên nghe sự kiện, vòng quét — không trong căn cứ) để làm LỆNH bằng
#              quyền người phụ trách. Không qua cửa executor / route; quyết định duyệt không bao giờ (chỉ EDGE còn hạn — ``executor.DECISIONS``).
EDGE, CORE, DELEGATE, JOB = "edge", "core", "delegate", "job"


class Lease:
    """Thời hạn của người gọi cấp ở cửa HTTP: còn khi request đã cấp còn chạy (``http_api`` tắt khi phản hồi xong, cả luồng SSE)."""
    __slots__ = ("active",)

    def __init__(self) -> None:
        self.active = True


@dataclass(frozen=True, slots=True)
class _Record:
    ref: weakref.ref[Principal]
    snapshot: Principal
    kind: str
    lease: Lease | None


_RECORDS: dict[int, _Record] = {}
# Hợp đồng thuê của REQUEST đang chạy — ``http_api`` đặt cho mỗi request HTTP / websocket, tắt khi phản hồi xong. Ngoài request (ngữ cảnh mới, luồng
# mới, việc nền) ⇒ None ⇒ không cấp được người gọi EDGE (đóng khi không rõ).
LEASE: ContextVar[Lease | None] = ContextVar("barista_request_lease", default=None)


@contextmanager
def leased() -> Iterator[None]:
    """Hợp đồng thuê cho mã LÕI đóng vai người bấm ở cửa HTTP (dữ liệu mẫu, test): người gọi ``edge`` cấp bên trong hết hạn khi ra khỏi khối."""
    lease = Lease()
    token = LEASE.set(lease)
    try:
        yield
    finally:
        lease.active = False
        LEASE.reset(token)


def edge(p: Principal) -> Principal:
    """Cấp ``p`` là người gọi của REQUEST đang chạy (EDGE, hết hạn cùng request). Ngoài request ⇒ chỉ là CORE (route không dùng được)."""
    lease = LEASE.get()
    return issued(p, kind=EDGE, lease=lease) if lease is not None and lease.active else issued(p, kind=CORE)


def _drop(key: int) -> None:
    r = _RECORDS.get(key)
    if r is not None and r.ref() is None:
        del _RECORDS[key]


def issued(p: Principal, *, kind: str = CORE, lease: Lease | None = None) -> Principal:
    if kind not in (EDGE, CORE, DELEGATE, JOB):
        raise ValueError(f"loại người gọi {kind} lạ")
    if type(p) is not Principal:
        raise TypeError("chỉ cấp cho Principal (không lớp con)")
    old = _RECORDS.get(id(p))
    if old is None or old.ref() is not p:
        weakref.finalize(p, _drop, id(p))
    _RECORDS[id(p)] = _Record(weakref.ref(p), copy.deepcopy(p), kind, lease)
    return p


def _record(p: object) -> _Record | None:
    """Bản ghi của lõi cho tay cầm này — None nếu không do lõi cấp, hoặc tay cầm đã bị sửa so với bản chụp lúc cấp."""
    if type(p) is not Principal:
        return None
    r = _RECORDS.get(id(p))
    if r is None or r.ref() is not p or p != r.snapshot:
        return None
    return r


def kind_of(p: object) -> str | None:
    """Loại cấp của người gọi (EDGE / CORE / DELEGATE) — None = không do lõi cấp hoặc đã bị sửa."""
    r = _record(p)
    return r.kind if r else None


def is_issued(p: object, *kinds: str) -> bool:
    k = kind_of(p)
    return k is not None and (not kinds or k in kinds)


def live_edge(p: object) -> bool:
    """Người gọi cấp ở cửa HTTP, request cấp nó CÒN chạy (giữ lại dùng sau / dùng ở request khác ⇒ False)."""
    r = _record(p)
    return r is not None and r.kind == EDGE and r.lease is not None and r.lease.active


def lease_of(p: object) -> Lease | None:
    r = _record(p)
    return r.lease if r else None
