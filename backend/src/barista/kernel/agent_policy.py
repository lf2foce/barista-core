"""MỘT HÀM quyết "trợ lý được làm gì" (quyết định 0144): ``decide(op, caller, company, run)`` — hàm THUẦN, không DB, trả chế độ + VÌ SAO.

``kernel/executor.py`` GỌI hàm này (lát 2 đã nối): ``effective_mode`` (danh mục công cụ / màn quản trị — chưa đếm trần) và nhánh chế độ trợ lý của
``_run`` (lượt thật, có đếm trần) đều hỏi ``decide`` — MỘT nơi quyết, không còn mảnh quyết định rải trong executor. ``tests/test_permission_matrix.py``
kiểm (a) các BẤT BIẾN không phụ thuộc hàm và (b) hàm này cho đúng kết quả với bảng chân lý mọi tổ hợp (lệnh × người gọi × chính sách công ty × lượt).
Hàm vẫn THUẦN: executor nạp sẵn chính sách công ty và số lần tự làm trong ngày rồi mới gọi.

Bốn nguồn đã được bên gọi NẠP SẴN (hàm không đọc gì thêm):
* ``op``: trần ``agent=``, mức rủi ro (``risk_of``), ``promotable``, loại thao tác, ``public`` / ``apps``.
* ``caller``: hai trục trực giao — ``via`` (human / agent / app / link / system = AI đang làm) + ``attended`` (lượt có người); ``kind`` (edge / core /
  delegate = tay cầm từ đâu ra, theo ctx-issued vòng 3 — chưa nối, mặc định "edge").
* ``company``: chính sách công ty cho lệnh và nhóm (``core.agent_policies``), lần tự làm này còn trong trần ngày / tiền không (``within_caps`` — đã
  đếm ở ngoài; None = chưa đếm ⇒ coi như vượt, fail closed).
* ``run``: gọi lồng hay không, xem trước (chạy thử) hay không.

Thứ tự ``deny < propose < allow``; tổ hợp không rơi vào nhánh nào ⇒ ``propose``.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from barista.kernel import registry

RISKS = ("read", "low", "money", "authority")   # mức rủi ro của một thao tác (0144 mục 3)
NEVER_ALLOW = ("money", "authority")              # không bao giờ để trợ lý tự làm, dù công ty đặt gì
AUTHORITY_MODULES = frozenset({"admin", "core", "connect"})
KINDS = ("edge", "core", "delegate")              # loại cấp tay cầm (ctx-issued vòng 3)


@dataclass(frozen=True)
class Caller:
    via: str = "human"          # human · agent · app · link · system
    attended: bool = False      # lượt có người (chỉ chat bật) — chỉ có nghĩa với via="agent"
    system: bool = False        # đang chạy bằng quyền hệ thống (bên nghe sự kiện, lệnh sau duyệt)
    kind: str = "edge"          # edge · core · delegate


@dataclass(frozen=True)
class Company:
    overrides: Mapping[str, str] = None  # type: ignore[assignment]  # {lệnh hoặc "<module>.*": deny|propose|allow}
    within_caps: bool | None = None       # lần tự làm này còn trong trần ngày / tiền? None = chưa đếm (fail closed)
    autorun_low_risk: bool = False        # công tắc công ty (0144): lệnh rủi ro thấp đảo được ⇒ làm ngay (như đã nới), có Hoàn tác

    def __post_init__(self) -> None:
        if self.overrides is None:
            object.__setattr__(self, "overrides", {})


@dataclass(frozen=True)
class Run:
    nested: bool = False   # gọi lồng từ một thao tác khác (``invoke``)
    preview: bool = False  # chạy thử (soạn đề xuất) — gọi lồng "đề xuất" không chặn


@dataclass(frozen=True)
class Decision:
    mode: str
    why: tuple[str, ...]

    @property
    def allow(self) -> bool:
        return self.mode == "allow"


def self_serve(op: registry.Op) -> bool:
    """Lệnh khai ``agent="allow"``: việc ghi nhỏ trợ lý được tự làm — module cam kết nó vô hại / sửa lại được (0144). Chỉ để MÔ TẢ cho người đọc
    (``kernel/preview.reversibility``); quyết định chế độ thật vẫn là ``decide``."""
    return op.agent == "allow"


def risk_of(op: registry.Op) -> str:
    """Mức rủi ro: khai tay (``risk=``) thắng; không khai thì SUY — truy vấn = read; module quản trị / lõi / app hoặc lệnh có ``sensitive=`` = authority;
    lệnh có ô tiền = money; còn lại = low. Suy diễn là bước chuyển (0144 mục 3) — ``test_permission_matrix`` liệt kê lệnh suy ra chọi với trần.
    Lệnh ``promotable`` (công ty nới được cho trợ lý TỰ LÀM) KHÔNG được dựa vào suy diễn: registry bắt khai ``risk="low"`` tường minh lúc đăng ký."""
    if op.risk:
        return op.risk
    if op.kind == "query":
        return "read"
    from barista.kernel.forms import field_specs  # nội bộ lõi; tránh vòng import lúc nạp registry

    if op.module in AUTHORITY_MODULES or op.sensitive is not None:   # quyền thắng tiền: lệnh quản trị có ô tiền (trần tiền của chính sách) vẫn là authority
        return "authority"
    if any(s.get("kind") == "money" for s in field_specs(op)):
        return "money"
    return "low"


def _min(*modes: str) -> str:
    order = registry.AGENT_MODES
    return order[min(order.index(m) for m in modes)]


DIRECT = Run()   # gọi thẳng, không xem trước

# ``why`` là chữ NGƯỜI DÙNG đọc (thẻ đề xuất, màn "Trợ lý được làm gì") — ngắn, tiếng Việt, không tên kỹ thuật
CEILING_WHY = {"allow": "mặc định: trợ lý được làm", "propose": "mặc định: việc ghi này cần người xác nhận", "deny": "mặc định: việc này người tự làm"}
MODE_WHY = {"allow": "tự làm", "propose": "chỉ đề xuất", "deny": "tắt"}


def decide(op: registry.Op, caller: Caller, company: Company, run: Run = DIRECT) -> Decision:
    """Chế độ cho LẦN GỌI này. Không thay ``executor.allowed`` (quyền + gói + link / app) — hàm này chỉ trả lời "trợ lý được làm tới đâu"; người
    thật, hệ thống, app, link ra ``allow`` kèm lý do để bước kiểm quyền thường áp."""
    group = f"{op.module}.*"
    o_cmd, o_grp = company.overrides.get(op.name), company.overrides.get(group)
    if caller.kind == "delegate" and op.kind == "command":
        return Decision("deny", ("tay cầm uỷ quyền (principal_of) chỉ đọc, không chạy lệnh",))
    if caller.via != "agent":
        return Decision("allow", (f"người gọi {caller.via}: theo quyền + gói, không có rào chế độ trợ lý",))
    why: list[str] = []
    if run.nested:
        # gọi LỒNG (invoke): chỉ chính sách CÔNG TY áp (trần code "propose/deny" = không gọi THẲNG, module vẫn dùng nội bộ — docstring company_mode)
        modes = [m for m in (o_cmd, o_grp) if m is not None]
        if not modes:
            return Decision("allow", ("gọi lồng: công ty không đặt chính sách cho lệnh / nhóm — module dùng nội bộ theo hợp đồng",))
        m = _min(*modes)
        if m == "deny":
            return Decision("deny", (f"gọi lồng: công ty tắt {'nhóm ' + group if o_grp == 'deny' else 'lệnh'}",))
        if m == "propose" and op.kind == "command" and not run.preview:
            return Decision("propose", ("gọi lồng: công ty đặt “đề xuất” cho lệnh này — lệnh ngoài thành thẻ, người bấm mới chạy",))
        return Decision("allow", ("gọi lồng: " + ("đang chạy thử" if run.preview else "công ty không siết tới mức đề xuất"),))
    # gọi THẲNG: trần code (nâng được cho lệnh promotable khi công ty đặt "allow" cho chính lệnh đó), rồi chặt nhất với chính sách nhóm / lệnh
    ceiling = op.agent
    if op.promotable and risk_of(op) == "low" and o_cmd == "allow":
        ceiling, why = "allow", ["công ty cho trợ lý tự làm lệnh này"]
    elif op.promotable and risk_of(op) == "low" and company.autorun_low_risk and o_cmd is None:
        ceiling, why = "allow", ["công ty bật tự làm cho việc nhỏ đảo được — có Hoàn tác"]
    else:
        why.append(CEILING_WHY[op.agent])
    picks = [ceiling] + [m for m in (o_grp, o_cmd) if m is not None and not (op.kind == "query" and m == "propose")]
    mode = _min(*picks)
    if o_grp is not None and mode == o_grp and o_grp != ceiling:
        why.append(f"công ty siết nhóm {registry.MODULES.get(op.module, op.module)}: {MODE_WHY[o_grp]}")
    if o_cmd is not None and o_cmd != "allow" and mode == o_cmd:
        why.append(f"công ty siết lệnh này: {MODE_WHY[o_cmd]}")
    if mode == "allow" and op.agent != "allow":   # chỉ tới đây khi được nới: thêm rào lượt có người + trần
        if not caller.attended:
            return Decision("propose", (*why, "lượt không người (việc nền, buổi sáng, gọi qua app) ⇒ chờ người xác nhận"))
        if not company.within_caps:
            return Decision("propose", (*why, "đã tới trần số lần tự làm hôm nay" if company.within_caps is False else "chưa đếm trần ⇒ chờ người xác nhận"))
        why.append("trong trần ngày / tiền, lượt có người")
    return Decision(mode, tuple(why))
