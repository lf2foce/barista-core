"""MÀN HÌNH DO SERVER MÔ TẢ — khai báo (quyết định 0069).

Mỗi màn = một truy vấn (bảng / chi tiết) + nút lệnh trên thanh công cụ + nút lệnh trên từng dòng + mở chi tiết khi chạm dòng (``row_click``: bấm MỘT lần
mở chi tiết / nút mặc định của dòng). Module SỞ HỮU
phân hệ khai ``m.workspace(Workspace(...))``; module khác GÓP màn vào phân hệ đó bằng ``m.screens(key, (...), rank=)``. ``workspaces.py`` chỉ
ráp (``assemble``) và lọc theo quyền từng người. Kernel không biết module / gói nào.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

type Line = dict[str, Any]


@dataclass(frozen=True)
class Action:
    command: str
    title: str = ""
    bind: dict[str, str] = field(default_factory=dict)  # trường lệnh ← cột của dòng (CỐ ĐỊNH, ẩn khỏi form — vd mã bản ghi)
    prefill: dict[str, str] = field(default_factory=dict)  # trường lệnh ← cột của dòng, ĐIỀN SẴN nhưng vẫn sửa được (form "Sửa")
    preset: dict[str, Any] = field(default_factory=dict)  # trường lệnh = giá trị cố định
    when: dict[str, list[Any]] = field(default_factory=dict)  # chỉ hiện khi cột của dòng thuộc các giá trị
    style: str = "default"  # default · primary · destructive
    # ĐIỂM CẮM NÚT (0124): gói khác có thể THAY nút này bằng lệnh của mình (``Module.plug_action``) khi gói đó bật và người xem chạy được lệnh đó;
    # không có ai cắm ⇒ giữ nút gốc. Chỉ cho nút thanh công cụ không ràng cột (bind / prefill / preset trống).
    slot: str = ""


@dataclass(frozen=True)
class Ask:
    """Nút đưa câu mở đầu vào chat. ``mode``: chế độ trò chuyện riêng (0098 — "builder": mở TRÌNH TẠO APP thay vì trợ lý cá nhân)."""
    title: str
    prompt: str = ""
    mode: str = ""
    app_id: str = ""   # chế độ làm trên một đối tượng cụ thể (vd trình tạo mở trên app này)


@dataclass(frozen=True)
class Open:
    query: str
    bind: dict[str, str]
    title: str = ""


# Màu nút ghim / ô biểu tượng của màn — tên màu HỆ THỐNG (app tự đổi sáng / tối). Chọn theo Ý NGHĨA của màn, không theo phân hệ: đỏ = cần xử lý,
# cam = hôm nay / hạn, xanh dương = khách / người, teal = hàng / tài sản, chàm = giấy tờ / hồ sơ, xanh lá = tiền / xong, tím = trợ lý theo dõi,
# hồng = quảng cáo. Trống ⇒ app dùng màu của phân hệ.
TINTS = frozenset({"blue", "teal", "green", "orange", "red", "pink", "purple", "indigo", "mint", "brown", "gray", "yellow", "cyan"})


# BÀN VIỆC (0094): MỘT điểm vào "việc của tôi" — ba nhóm theo điều người phải làm, không theo module.
DESK_GROUPS = {"decide": "Chờ tôi duyệt", "do": "Cần tôi làm", "watch": "Đang theo dõi"}


@dataclass(frozen=True)
class Desk:
    """Màn này góp vào BÀN VIỆC (0094) — khai MỘT lần trên chính màn: mục của bàn, số / câu tóm tắt, lời chào của trợ lý đều lấy từ đây.

    * Màn VIỆC (mặc định, ``menu=False``): trả lời "tôi phải làm gì" (việc của tôi, khách cần gọi, phòng khác nhờ…) ⇒ CHỈ ở bàn việc, không
      lặp ở menu phân hệ. Mở từ bàn = đúng màn đó, đủ nút như cũ.
    * Màn ĐỐI TƯỢNG có tín hiệu (``menu=True``: giữ căn, lịch đóng tiền, kế hoạch…) ⇒ vẫn ở menu; bàn việc chỉ hiện câu tín hiệu khi có.
    ``say``: kết quả truy vấn của màn (chạy bằng quyền người xem) → câu tóm tắt (``attention.line``) / danh sách câu / None. Code viết, model
    không tham gia. ``team_only``: người phạm vi "all" trên tài nguyên đó không nhận CÂU tín hiệu (đã có tín hiệu toàn công ty) — mục vẫn có. ``slow``: truy vấn quét nhiều
    công ty — được chờ lâu hơn (không nhớ kết quả: quyền ở từng công ty có thể vừa đổi). ``rank``: thứ tự trong nhóm (nhỏ lên trước).
    ``doers_only`` (ADR 0239 phần 4, api 1.57): mục là việc của NGƯỜI XỬ LÝ — chỉ người có quyền GHI tài nguyên của truy vấn màn nhận nó như việc của
    mình (đúng nhóm, có màu, vào số trên nút "Việc", lời chào trợ lý, câu "việc hôm nay"); người chỉ xem vẫn thấy mục ở "Đang theo dõi", không màu,
    không đếm (vd giám đốc thấy "Cảnh báo chi" dạng tổng hợp, không bị kéo vào việc của kế toán). Ai xử lý là dữ liệu ma trận quyền, không tên vai."""
    group: str
    say: Callable[[Any], Line | list[Line | None] | None] | None = None
    input: dict[str, Any] = field(default_factory=dict)
    menu: bool = False
    team_only: bool = False
    slow: bool = False
    rank: int = 500
    doers_only: bool = False

    def __post_init__(self) -> None:
        if self.group not in DESK_GROUPS:
            raise ValueError(f"nhóm bàn việc {self.group} lạ — chọn trong {sorted(DESK_GROUPS)}")


@dataclass(frozen=True)
class Screen:
    key: str
    title: str
    icon: str
    query: str
    group: str = ""
    actions: tuple[Action, ...] = ()
    row_actions: tuple[Action, ...] = ()
    open: Open | None = None
    min_scope: str | None = None
    max_scope: str | None = None  # màn CÁ NHÂN (ví của tôi…): người phạm vi rộng hơn mức này không cần ⇒ ẩn
    needs_action: bool = False
    tint: str = ""
    desk: Desk | None = None   # góp vào bàn việc (0094)
    input: dict[str, Any] = field(default_factory=dict)   # đầu vào CỐ ĐỊNH của truy vấn (ẩn khỏi bộ lọc) — vd màn app tự ghép: mã app (0098)
    fields: tuple[dict[str, Any], ...] = ()   # bộ lọc KHAI THÊM (cùng hình FieldSpec) — vd tham số của app tự ghép (0098)
    ask: Ask | None = None   # nút "nhờ trợ lý" trên thanh công cụ (vd "Tạo app" ⇒ mở trình tạo app)
    pinnable: bool = True   # False ⇒ không tự ghim khi vai chưa có màn ghim (màn thiết lập / việc ít dùng — vẫn ở menu)
    badge: str = ""   # nhãn nhỏ cạnh tên màn trên menu (vd "Thử nghiệm") — chỉ THÊM trường vào manifest, app cũ bỏ qua
    full_frame: bool = False   # màn TỰ VẼ trọn khung làm việc (không thẻ bọc, không đầu khung) như một phân hệ riêng — vd "Soạn" (agent + văn bản): chỉ THÊM, app cũ vẽ như màn thường
    filters_side: bool = False   # bộ lọc của màn xếp thành CỘT BÊN TRÁI (như bộ lọc của báo cáo BI) thay vì hàng trên đầu — chỉ web vẽ; app cũ / native vẫn hiện bộ lọc ở đầu (0166)
    # BẤM MỘT LẦN vào dòng làm gì (12/11/2026): "open" = mở ``open`` (chi tiết); tên lệnh của MỘT nút trong ``row_actions`` = mở form nút đó. Trống ⇒ bấm chỉ chọn
    # dòng (như trước). Bấm vẫn CHỌN dòng (thanh thao tác của dòng còn sau khi đóng chi tiết); chọn mà không mở: ⌘/Ctrl-bấm hoặc phím cách (web), ⌘-bấm (Mac)
    row_click: str = ""
    # KHOÁ CŨ của màn (1.59): màn gộp từ nhiều màn (vd một màn Giao dịch thay ba — quyền theo khối, 0239) khai khoá các màn đã bỏ ⇒ ghim của người dùng
    # còn lưu khoá cũ vẫn trỏ tới màn này (không phá ghim). Chỉ để tìm lại, không hiện ở đâu.
    aliases: tuple[str, ...] = ()
    # NỘI DUNG CHÍNH của màn là một nhóm thông tin (1.59, lớp 3 — cùng cú pháp ``ui.*(needs=)``): người xem đọc được tài nguyên của truy vấn mà không có
    # nhóm này ⇒ màn không lên menu (vd Lịch đóng tiền của khách với người đọc giao dịch chỉ để đối chiếu chiến dịch). Không phải "ẩn màn trùng".
    needs: str | tuple[str, ...] = ()
    # NGHIỆP VỤ CÔNG TY CÓ DÙNG (1.63): khoá một cài đặt công ty (``Module.setting``, 0128). Công ty TẮT công tắc ⇒ màn không lên menu / bàn việc của
    # bất kỳ vai nào. Khác quyền (vai này được làm gì) — đây là "công ty có làm việc này không" (vd thu hộ – chi trả, hàng ký gửi): một ô tick ở
    # Quản trị › Cài đặt công ty thay cho thu hồi quyền từng vai. Tắt ⇒ ẩn MÀN và người / trợ lý không chạy được thao tác chỉ thuộc màn đó (``feature_ops``, ``executor.feature_off``).
    feature: str = ""

    @property
    def in_menu(self) -> bool:
        """Màn việc chỉ ở bàn việc; màn thường và màn đối tượng có tín hiệu ở menu phân hệ."""
        return self.desk is None or self.desk.menu

    def __post_init__(self) -> None:
        if self.tint and self.tint not in TINTS:
            raise ValueError(f"màn {self.key}: màu {self.tint} lạ — chọn trong {sorted(TINTS)}")
        if self.row_click == "open" and self.open is None:
            raise ValueError(f"màn {self.key}: row_click='open' nhưng màn không khai open=")
        if self.row_click not in ("", "open") and sum(a.command == self.row_click for a in self.row_actions) != 1:
            raise ValueError(f"màn {self.key}: row_click={self.row_click!r} phải là 'open' hoặc lệnh của ĐÚNG MỘT nút trong row_actions")


@dataclass(frozen=True)
class Workspace:
    key: str
    title: str
    icon: str
    screens: tuple[Screen, ...]
    order: int = 50          # vị trí trên menu (nhỏ lên trước)
    phone: bool = True       # False ⇒ app iPhone không hiện phân hệ này (màn quá rộng cho màn nhỏ — vd App của tôi, 0098)
    pack: str = ""           # gói của module SỞ HỮU phân hệ (registry điền) — công ty chưa bật gói ⇒ không có phân hệ, kể cả màn góp từ gói nền



@dataclass(frozen=True)
class Contribution:
    into: str
    rank: int
    seq: int
    screens: tuple[Screen, ...]


WORKSPACES: dict[str, Workspace] = {}
CONTRIB: list[Contribution] = []


@dataclass(frozen=True)
class PersonalSource:
    """Màn RIÊNG TỪNG NGƯỜI (0098 — app tự ghép): một truy vấn của module, chạy bằng quyền người đó lúc ráp menu, trả danh sách màn
    ``[{key, title, icon, tint?, query, input}]`` góp vào phân hệ ``into``. Kernel chỉ ráp + lọc quyền như màn khai tĩnh, không biết module nào."""
    into: str
    query: str


PERSONAL: list[PersonalSource] = []


def assemble() -> tuple[Workspace, ...]:
    """Phân hệ theo ``order``; trong mỗi phân hệ: màn của chủ (rank 0) rồi màn góp theo ``rank`` / thứ tự khai."""
    out = []
    for ws in sorted(WORKSPACES.values(), key=lambda w: (w.order, w.key)):
        parts = [(0, -1, ws.screens)] + [(c.rank, c.seq, c.screens) for c in CONTRIB if c.into == ws.key]
        parts.sort(key=lambda t: (t[0], t[1]))
        out.append(Workspace(ws.key, ws.title, ws.icon, tuple(s for *_, ss in parts for s in ss), ws.order, ws.phone, ws.pack))
    return tuple(out)


_FEATURE_OPS: tuple[tuple[int, int, int], dict[str, str]] | None = None


def _ops_of(s: Screen) -> set[str]:
    return {s.query, *([s.open.query] if s.open else ()), *(a.command for a in (*s.actions, *s.row_actions))}


def feature_ops() -> dict[str, str]:
    """Thao tác CHỈ thuộc màn của một nghiệp vụ có công tắc (``Screen.feature``, 1.63) → khoá công tắc. Thao tác mà một màn KHÔNG gắn công tắc cũng dùng
    (vd nút "Nhập giao dịch" trên dòng hàng ký gửi) không tính — nó là việc chung. Công ty tắt công tắc ⇒ người, trợ lý, MCP không chạy được các thao
    tác này (``executor.feature_off`` trong ``allowed`` — một chỗ quyết): tắt nghiệp vụ là tắt, không chỉ ẩn màn."""
    global _FEATURE_OPS
    stamp = (len(WORKSPACES), len(CONTRIB), sum(len(w.screens) for w in WORKSPACES.values()))   # màn khai lúc nạp module; test nạp thêm gói ⇒ tính lại
    if _FEATURE_OPS is not None and _FEATURE_OPS[0] == stamp:
        return _FEATURE_OPS[1]
    featured: dict[str, str] = {}
    shared: set[str] = set()
    for ws in assemble():
        for s in ws.screens:
            if s.feature:
                for name in _ops_of(s):
                    featured.setdefault(name, s.feature)
            else:
                shared |= _ops_of(s)
    out = {name: f for name, f in featured.items() if name not in shared}
    _FEATURE_OPS = (stamp, out)
    return out


def menu_pages(workspace: str) -> tuple[Screen, ...]:
    """Trang khai TĨNH của một phân hệ — màn của chủ + màn các module góp, đúng thứ tự menu; không gồm màn riêng từng người (``PERSONAL``). Cho module
    CHỦ phân hệ làm việc trên chính menu của nó (vd chốt tháng chụp từng trang — 0213). Không cấp quyền gì: chạy truy vấn của màn vẫn kiểm quyền người gọi."""
    return next((w.screens for w in assemble() if w.key == workspace), ())
