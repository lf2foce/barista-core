"""Sổ đăng ký: mọi lệnh (ghi), truy vấn (đọc) và bên nghe sự kiện của mọi module.

Một module khai::

    m = Module("crm", "Khách hàng", resources={"crm": "Khách hàng & lead"})

    @m.command("create_lead", resource="crm", title="Thêm khách / lead", importable=True)
    async def create_lead(ctx: Ctx, inp: CreateLead) -> dict: ...

    @m.query("leads", resource="crm", title="Khách & lead")
    async def leads(ctx: Ctx, inp: LeadFilter) -> dict: ...

    @m.on("acct.payment_received", needs=("finance",))
    async def mark_paid(ctx: Ctx, event: Event) -> None: ...

Mỗi lệnh khai MỘT tài nguyên + MỘT hành động ⇒ ma trận quyền (core.grants) quyết định ai chạy được; RLS quyết định
chạy trên dòng nào. ``internal=True``: chỉ module khác gọi (``ctx.call`` / ``ctx.call_system``) hoặc lõi chạy sau
duyệt — API không gọi thẳng được.
"""

import dataclasses
import inspect
import re
import typing
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, LiteralString
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from barista.kernel import attention, company_settings, extra_fields, vocab
from barista.kernel import screens as scr
from barista.kernel.extract import KINDS, DocType, F
from barista.kernel.principal import ACTIONS


def agent_only_fields(model: type[BaseModel]) -> set[str]:
    """Ô khai ``field(..., agent=False)``: trợ lý không thấy / không điền — chỗ duy nhất ``Op.pin`` được ghi."""
    return {k for k, f in model.model_fields.items() if isinstance(f.json_schema_extra, dict) and f.json_schema_extra.get("agent") is False}


def validate_pin(full: str, pin: Pin | None, model: type[BaseModel]) -> None:
    """``pin`` chỉ ghim ô ``agent=False`` — lệnh không có ô nào như vậy thì khai ``pin`` là sai lúc đăng ký (``Module._register`` gọi; kiểm riêng được, không cần đăng ký)."""
    if pin is not None and not agent_only_fields(model):
        raise ValueError(f"{full}: pin chỉ ghim ô khai agent=False — lệnh chưa có ô nào như vậy")


def validate_intent(full: str, kind: str, internal: bool, intent: tuple[str, ...], model: type[BaseModel]) -> None:
    """Ô ý định (ADR 0250) phải là ô THẬT, người thấy được của đầu vào lệnh người gọi trực tiếp — không ô ẩn / ô trợ lý không thấy (``agent=False``)."""
    if kind != "command" or internal:
        raise ValueError(f"{full}: intent= chỉ cho lệnh người gọi trực tiếp (không truy vấn / lệnh nội bộ)")
    if len(set(intent)) != len(intent):
        raise ValueError(f"{full}: intent= có ô trùng")
    hidden = agent_only_fields(model)
    for k in intent:
        f = model.model_fields.get(k)
        ui = (f.json_schema_extra or {}).get("ui", {}) if f is not None and isinstance(f.json_schema_extra, dict) else {}
        if f is None or k in hidden or (isinstance(ui, dict) and ui.get("hidden")):
            raise ValueError(f"{full}: intent= {k!r} không phải ô người thấy được của đầu vào")


Kind = Literal["command", "query"]
AGENT_MODES = ("deny", "propose", "allow")  # thứ tự từ chặt tới lỏng
Basis = Callable[[Any, Any], Awaitable[dict[str, str]]]
Pin = Callable[[Any, Any], Awaitable[dict[str, Any]]]
Sensitive = Callable[[Any, Any], Awaitable[str | None]]


@dataclass(frozen=True)
class ImportKey:
    """KHOÁ TỰ NHIÊN của lệnh nhập Excel (0226, ``barista.api`` 1.46): để màn nhập nhận ra dòng nào là THÊM MỚI, dòng nào CẬP NHẬT, dòng nào TRÙNG.

    ``fields``: trường của đầu vào lệnh làm khoá (vd ``("phone",)``, ``("code",)``) — giá trị đã qua chuẩn hoá của kiểu (SĐT thành E.164…). Trường ĐẦU là
    định danh (dòng để trống ⇒ không nhận ra được, chạy như dòng mới); các trường sau được phép trống (vd khách + dự án quan tâm: không nêu dự án = lead chưa gắn dự án).
    ``existing``: ``async (ctx, danh sách khoá) → {khoá (tuple, chữ thường): bản ghi}`` — bản ghi đã có, theo ĐÚNG tên trường của đầu vào lệnh
    (chỉ cần các trường muốn so; chạy bằng quyền người nhập, chỉ đọc ⇒ bản ghi họ không thấy coi như chưa có).
    ``update``: None ⇒ lệnh này KHÔNG cập nhật (khoá đã có ⇒ "trùng — bỏ qua"); ``"self"`` ⇒ chính lệnh tự cập nhật khi khoá đã có (kiểu upsert; các trường
    file không có giữ nguyên giá trị đang có); tên lệnh khác ⇒ dùng lệnh đó (đầu vào = khoá + các trường đổi).
    Mặc định an toàn: ô đang có dữ liệu KHÔNG bị ghi đè nếu người nhập chưa bật "ghi đè" — chỉ điền ô đang trống."""
    fields: tuple[str, ...]
    existing: Callable[..., Awaitable[dict[tuple[str, ...], dict[str, Any]]]]
    update: str | None = None


@dataclass(frozen=True)
class ImportTable:
    """NHẬP CẢ BẢNG LÀM MỘT LẦN GỌI (0222 §15/11, ``barista.api`` 1.48): dòng của file KHÔNG chạy thành từng lệnh riêng mà đổ vào MỘT trường danh sách của đầu vào lệnh.
    Dùng khi dòng tham chiếu nhau (kế hoạch: mã WBS, việc trước, giai đoạn, ngày tính từ số ngày + việc trước) nên phải nhìn cả file mới hiểu được.

    ``field``: trường ``list[Mô hình dòng]`` của đầu vào lệnh; các trường của MÔ HÌNH DÒNG là trường để khớp cột (nhãn / ``aliases`` / lựa chọn như mọi lệnh nhập) và
    phải có ô ẩn ``line: int`` (số dòng Excel — lệnh dùng để báo lỗi / kết quả từng dòng). ``preset``: các trường CẤP LỆNH màn nhập được đặt sẵn (vd kế hoạch đích) — chỉ
    những trường này mới nhận từ ``plan.preset``.

    Lệnh trả ``{"import": {"rows": [{"line", "action": create|update|skip, "status": ok|invalid, "error", "diff": [{field, old, new}], "note"}], "notes": [chữ],
    "view": {...}}}`` — màn nhập vẽ ``view`` (bản xem trước dữ liệu kết quả) và đếm theo ``rows``. Chạy thử = chạy lệnh trong điểm lưu rồi bỏ; nhập thật = ``executor.run``
    (quyền, nhật ký thao tác, nhật ký nhập). Dòng sai kiểu bị loại TRƯỚC (kiểm bằng mô hình dòng) và báo từng dòng."""
    field: str
    preset: tuple[str, ...] = ()


@dataclass(frozen=True)
class Op:
    name: str
    kind: Kind
    module: str
    resource: str | None
    action: str
    handler: Callable[..., Awaitable[Any]]
    input: type[BaseModel]
    title: str
    description: str
    internal: bool
    importable: bool
    min_scope: str | None = None
    agent: str = "allow"  # trần khi trợ lý làm thay: allow · propose (soạn đề xuất, người bấm) · deny
    shows: tuple[str, str] | None = None  # lệnh: (truy vấn xem kết quả, khoá trong output = tham số truy vấn) ⇒ thẻ kết quả
    needs: tuple[str, ...] = ()  # gói nghiệp vụ thao tác này cần THÊM ngoài gói của module (vd giấy tờ phí môi giới cần "finance")
    category: str = ""  # loại công cụ của trợ lý: "" = theo tài nguyên (tool_kind), "setup" = thiết lập, "daily" = hằng ngày
    public: str | None = None  # loại link công khai (vd "asks.request"): CHỈ người cầm đúng loại link đó chạy được — không ai khác (0064)
    # CĂN CỨ (0071): hàm (ctx, input) → {nhãn: dấu phiên bản} — những gì lệnh dựa vào. Phiếu duyệt / thẻ đề xuất lưu lúc xin; căn cứ đổi trước
    # lúc chạy ⇒ không chạy. Chạy bằng quyền hệ thống TRƯỚC handler (handler có thể chính là thứ đổi căn cứ).
    basis: Basis | None = None
    # HỢP ĐỒNG QUYỀN HỆ THỐNG: thao tác nội bộ được gọi bằng ``ctx.call_system`` khai NGAY TẠI ĐÂY module nào được gọi và vì sao — lý do nêu
    # điều đã kiểm ở bên gọi, quyền được mở tới đâu, trả những trường nào (``tests/test_system_calls.py`` đối chiếu từng chỗ gọi)
    callers: tuple[str, ...] = ()
    why: str = ""
    # HỌ thao tác đăng ký qua hàm của registry (subject / check / metric / work / blockers) — gọi theo tên tính lúc chạy, hợp đồng là hàm đăng ký
    family: str = ""
    # lời mô tả cho NGƯỜI DÙNG trên form = ``description=`` khai rõ; docstring (``description`` khi không khai) là chữ cho trợ lý / người viết code
    # — có thể có từ kỹ thuật, không hiện trên form
    form_help: str = ""
    # đề xuất BÊN LỀ (vd ghi nhớ): trợ lý soạn thẻ nhỏ cạnh câu trả lời, lượt KHÔNG dừng chờ — người bấm lúc nào cũng được, bấm xong không chạy tiếp
    aside: bool = False
    # LẦN GỌI NÀO thuộc luật duyệt (0029): lệnh mà chỉ một phần lần gọi là nhạy cảm (sửa người dùng: đổi vai thì phải duyệt, đổi đội thường thì
    # không) khai hàm (ctx, input) → mô tả thay đổi cho người duyệt, hoặc None = lần gọi này không thuộc luật. Có luật hay không vẫn là DỮ LIỆU
    # (core.approval_rules); hàm chỉ thu hẹp trong lệnh đã bị đặt luật, và mô tả của nó hiện trên phiếu.
    sensitive: Sensitive | None = None
    # APP TỪ XA (0140, review 08/11): thao tác trợ lý bị cấm (``agent="deny"``) app cũng không chạy — trừ khi thao tác KHAI RÕ cho app (vd nạp
    # lead từ nguồn ngoài: trợ lý không nạp, app nguồn lead thì có)
    apps: bool = False
    # TRUY VẤN cần gọi mạng trước khi đọc (vd nhúng câu tìm — 0141): ``async (người gọi, đầu vào) → giá trị`` chạy NGOÀI giao dịch, trước khi mượn
    # kết nối DB; handler đọc kết quả ở ``ctx.prepared``. Gọi lồng (``ctx.call``) không chạy bước này — handler phải tự xử lý ``None``.
    # LỆNH (ADR 0200): ``async (Prep, đầu vào) → giá trị`` chạy ngoài giao dịch ngay trước khi handler chạy thật — xem ``Module.command``.
    prepare: Callable[..., Awaitable[Any]] | None = None
    # THANG TIN CẬY (0143): lệnh "propose" mà CÔNG TY được nới lên "allow" khi thành tích đề xuất đủ ngưỡng công ty đặt, có trần ngày / tiền —
    # module chọn có chủ đích (việc ghi rủi ro thấp, đảo được); lệnh duyệt / đổi quyền / tiền ra không bao giờ promotable
    promotable: bool = False
    # ĐẢO (0143 lát 2): lệnh promotable khai ĐỐI TƯỢNG nó tác động (``target`` — tên ô trong đầu vào hoặc kết quả, vd ``task_id``) và lệnh nào
    # của NGƯỜI chạy trên cùng đối tượng trong 7 ngày sau lần trợ lý tự làm thì coi là người sửa / huỷ việc trợ lý (``undone_by`` — "lệnh" hoặc
    # "lệnh:ô=giá trị", ô mang cùng tên ``target``). Bị đảo nhiều ⇒ tự hạ như lỗi.
    target: str = ""
    undone_by: tuple[str, ...] = ()
    # MỨC RỦI RO (0144 mục 3): read · low · money · authority — khai tay thắng, không khai thì ``agent_policy.risk_of`` suy; money / authority
    # không bao giờ ``agent="allow"`` hay ``promotable`` (chặn lúc khai)
    risk: str = ""
    # NHẬP FILE THEO DỮ LIỆU CÔNG TY: lệnh ``importable`` khai ``import_rows`` = ``async (ctx, lưới ô) → dòng {tiêu đề: giá trị}`` — đọc cả LƯỚI ô
    # của trang tính (gồm phần đầu file trước dòng tiêu đề) và ĐỔI tiêu đề cột theo cấu hình công ty (vd mẫu cột sao kê từng ngân hàng) TRƯỚC khi
    # khớp cột như mọi lệnh nhập. Chạy bằng quyền người nhập, chỉ đọc. Không khai / trả rỗng (không nhận ra file) ⇒ dòng đầu có chữ là tiêu đề như trước.
    import_rows: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None
    # GHIM LÚC SOẠN: ``async (ctx chỉ đọc quyền hệ thống, input) → {ô: giá trị}`` — ô ``agent=False`` mà đề xuất của trợ lý CHỐT theo giá trị hiện tại lúc soạn
    # (vd phiên bản phân bổ người xác nhận sẽ thấy). Thẻ mang giá trị đó; người bấm chạy lại đúng giá trị ⇒ đổi sau lúc soạn thì lệnh từ chối. Giá trị
    # trợ lý lỡ gửi kèm cho ô đó BỊ GHI ĐÈ (trợ lý không thấy ô này, không có gì để "khai"). Chỉ áp ở đường đề xuất (``_propose``); người gọi trực
    # tiếp (app, thẻ đã duyệt) tự khai và không bị ảnh hưởng.
    pin: Pin | None = None
    # ĐỌC NHẤT QUÁN (0213, 1.35): cả lệnh chạy trong MỘT ảnh dữ liệu (REPEATABLE READ) — mọi truy vấn trong lệnh thấy cùng một mốc, giao dịch khác
    # commit giữa chừng không lọt vào nửa sau. Chỉ cho lệnh ĐỌC nhiều ghi ít bản ghi MỚI (vd chụp báo cáo); UPDATE dòng người khác vừa sửa ⇒ "thử lại".
    consistent: bool = False
    # KHOÁ TỰ NHIÊN khi nhập Excel (0226, 1.46) — xem ``ImportKey``. Chỉ lệnh ``importable``.
    import_key: ImportKey | None = None
    # NHẬP CẢ BẢNG làm một lần gọi (1.48) — xem ``ImportTable``. Chỉ lệnh ``importable``; không đi cùng ``import_key`` / ``import_rows``.
    import_table: ImportTable | None = None
    # TRƯỜNG TUỲ BIẾN (0060, 1.50): lệnh nhận giá trị trường công ty tự định nghĩa cho ĐỐI TƯỢNG này (``Module.extensible``) — input có ô ``extra: dict``;
    # executor gộp ô phẳng ``x_<khoá>`` vào ``extra`` và kiểm kiểu theo định nghĩa của công ty TRƯỚC handler; handler lưu ``inp.extra`` vào bảng của mình.
    extra: str = ""
    # QUYỀN THEO KHỐI (lớp 3, 1.59): nhóm thông tin nhạy cảm (khoá ``InfoGroup`` / tài nguyên) mà đầu ra của truy vấn này CÓ THỂ chứa trong các phần
    # khai ``needs=`` — màn "Ai thấy thông tin gì" liệt kê màn theo khai này; test quét đỏ khi đầu ra có ``needs`` mà truy vấn không khai.
    info: tuple[str, ...] = ()
    # AN TOÀN CHO LUẬT TỰ ĐỘNG (ADR 0246, 1.69): lệnh module CHỌN CÓ CHỦ ĐÍCH cho hành động "chạy lệnh" của luật "Nếu… thì…" — chạy không người
    # bấm, bằng quyền người đặt luật (nạp lại mỗi lần), khoá chống lặp theo (luật, sự kiện / bản ghi + ngày). Chỉ lệnh ghi rủi ro thấp khai
    # ``risk="low"`` tường minh, không nội bộ, trợ lý không bị cấm — chặn lúc khai.
    auto: bool = False
    # FORM THEO Ý ĐỊNH (ADR 0250, 1.72): các ô form mở ra HỎI TRƯỚC (ý định của người — vd việc gì, mốc nào, ai chịu trách nhiệm) + một ô "Mô tả / thả file";
    # phần còn lại do "Soạn giúp" (``core.draft_input``) điền thành chip máy đoán, hoặc người bấm "Điền tay toàn bộ". Rỗng = form đủ ô như cũ.
    intent: tuple[str, ...] = ()
    # CĂN CỨ BẮT BUỘC (ADR 0251): khác rỗng ⇒ người bấm PHẢI nêu căn cứ đã thấy (``expect_basis`` — nút trang ``ui.act(basis=)`` / thẻ đề xuất / phiếu
    # duyệt) khi căn cứ hiện tại khác rỗng; thiếu ⇒ ``conflict`` với câu này, không chạy. Lệnh chốt thứ người bấm phải nhìn trước (vd phát hành giấy
    # theo số hồ sơ) — chạy "mù" từ một cửa không mang căn cứ là chốt thứ chưa ai xem.
    basis_required: str = ""


@dataclass(frozen=True)
class Event:
    id: int
    type: str
    payload: dict[str, Any]
    actor: Any
    at: Any = None   # lúc sự kiện xảy ra (core.events.created_at) — giao muộn / thử lại không đổi thời điểm này


@dataclass(frozen=True)
class Subscriber:
    name: str
    event_type: str
    handler: Callable[..., Awaitable[None]]
    needs: tuple[str, ...] = ()   # gói cần THÊM ngoài gói của module (vd bên nghe sự kiện ký giao dịch thuộc nghiệp vụ môi giới BĐS)
    # module + gói của bên nghe GHI RÕ lúc khai (review 08/11): lõi không bao giờ suy module từ tên chuỗi (``"flow.zz.grab".split(".")[0]``)
    module: str = ""
    pack: str = ""


OPS: dict[str, Op] = {}
# Danh sách chọn (truy vấn) ⇒ lệnh tạo thêm một mục của nó — ô chọn rỗng hiện nút "Tạo mới" (Module.query(creates=…))
CREATES: dict[str, str] = {}
# Bên nghe đổi tên / chuyển module (``Module.on(..., replaces=)``): tên cũ ⇒ bên nghe mới. Việc đã giao cho tên cũ (sự kiện phát lúc bản cũ còn
# chạy trong lúc đổi bản, hoặc đang chờ thử lại) chạy bằng bên nghe mới — không thành "không còn bên nghe" mà mất phản ứng.
REPLACED: dict[str, str] = {}
# HỌ thao tác do sổ đăng ký tạo (đối tượng, bộ kiểm, chỉ số, loại việc, điều kiện chặn tắt gói) ⇒ module nào được gọi BẰNG QUYỀN HỆ THỐNG. Module
# TIÊU THỤ tự khai (``Module.consumes``) — lõi không nhắc tên module (0069); executor so module đang chạy với tập này (0083 / 0084).
# Các họ ``OPEN_FAMILIES`` do thao tác lõi tự khai (``family=`` ở decorator, KÈM ``callers=`` cho module lõi đã biết): dịch vụ lõi mở cho gói ngoài
# mà lõi không biết tên — ``outreach`` (gửi yêu cầu ra ngoài qua link), ``doc_read`` (đọc lần trích / dấu tệp / có nhắc từ của tài liệu),
# ``paper_draft`` (lưu bản soạn), ``receivable`` (lập / huỷ / đọc khoản phải thu của CHÍNH nguồn gọi — ``ctx.owned_by``), ``remittance`` (tiền thu hộ
# đang giữ theo tài khoản của nguồn gọi + xin chi trả qua duyệt), ``follow_up`` (mở / đánh thức việc theo dõi của loại việc CHÍNH module gọi khai),
# và từ 1.14 (hai gói nữa rời lõi): ``identity`` (khớp / tạo định danh một người theo liên hệ, người được giám hộ của người vừa khớp), ``consent``
# (đọc trạng thái đồng ý hiệu lực / ghi một dòng đồng ý cho người thuộc đối tượng của module gọi), ``booking`` (đặt / nhả lịch nguồn
# ``<module gọi>.<loại>`` — ``ctx.owned_by`` — và tra nguồn lực theo mã), ``work_log`` (ghi công vào sổ công, nguồn của CHÍNH module gọi),
# ``call_log`` (nhật ký cuộc gọi của tổng đài vào Khách hàng: SĐT → lead, cuộc gọi thật thành chăm sóc, mã thứ khách quan tâm của chiến dịch gọi),
# và từ 1.16 (0186 — lõi thôi nêu tên module của gói ngành trong ``callers=``): ``settlement`` (nghĩa vụ phải trả người hưởng của CHÍNH nguồn gọi: ghi
# nhận, khoản thu hồi, lập chi ĐÃ DUYỆT, điều chỉnh huỷ, đọc vị thế / tình trạng chi — ``ctx.owned_by`` / nguồn của module gọi), ``placement`` (tiền
# công ty đặt tại đối tác: khuôn bút toán cố định trên tài khoản ``placed:<module gọi>-<khoá>``), ``ledger_ref`` (tra cứu sổ, chỉ đọc: một mục thu /
# chi của công ty, ngày còn trong kỳ mở), ``structure`` (cơ cấu quanh một người TẠI MỘT NGÀY: đội, chuỗi quản lý, người giới thiệu — chỉ mã),
# ``lead_conversion`` (lead thành thương vụ của gói: tìm / tạo khách theo SĐT trong phạm vi người nhập thấy, chuyển chặng; thương vụ huỷ ⇒ lead về
# tiềm năng), ``company_figures`` (SỐ GỘP toàn công ty cho trang báo cáo ``min_scope="all"`` của gói: phễu lead, nguồn của lead, chỉ tiêu công ty,
# ghi nhận / thực thu / tuổi nợ theo tiền tố nguồn; từ 1.31 số lead nguồn ngoài theo nhà cung cấp × ngày). Cùng đợt, họ cũ nhận thêm: ``receivable`` (căn cứ thu, chuyển tiền đã thu của khoản bị thay về chưa
# phân bổ), ``identity`` (định danh của thành viên / đối tác theo tên + mã số thuế / khách, tên + loại + liên kết đăng nhập theo mã), ``follow_up`` (việc
# cho người gắn bộ kiểm của chính module, tóm tắt hồ sơ của đối tượng chính module, ai có quyền để chọn người phụ trách), ``paper_draft`` (thông tin
# công ty in trên giấy). Từ 1.36 (0217): ``journal`` (bút toán KHÔNG dùng tiền giữa các tài khoản cố định do CHÍNH gói gọi khai — giá vốn, hàng đi đường, hàng
# nhập chưa có hoá đơn, chênh lệch kiểm kê, hàng bán bị trả lại). Từ 1.64: ``sale_source`` (nguồn doanh số báo MỘT LẦN BÁN của chính module gọi cho năng lực trả theo
# doanh số — hoa hồng: ghi nhận, huỷ, đổi người bán, mốc được hưởng; thao tác do GÓI năng lực khai, lõi chỉ mở họ để hai gói không nêu tên nhau).
# Từ 1.68 (0245): ``ledger_read`` (ĐỌC sổ cái chi tiết cho gói lập sổ / báo cáo kế toán: dòng bút toán theo kỳ theo trang đủ bút toán, số dư tại một
# ngày — theo pháp nhân, số hiệu đã ánh xạ, đối tượng sổ phụ; chỉ đọc, bên gọi khai ``min_scope="all"``).
OPEN_FAMILIES = ("outreach", "doc_read", "paper_draft", "receivable", "remittance", "follow_up",
                 "identity", "consent", "booking", "work_log", "call_log",
                 "settlement", "placement", "ledger_ref", "structure", "lead_conversion", "company_figures", "journal", "sale_source", "connector",
                 "ledger_read")
# ``tenancy`` không gắn thao tác nào: là QUYỀN DÙNG ``barista.api.tenancy`` (công ty khách của công ty dịch vụ) — module khai ``consumes("tenancy")`` mới gọi được.
FAMILIES = ("subject", "check", "metric", "work", "blockers", "plug", "tenancy", *OPEN_FAMILIES)
FAMILY_CALLERS: dict[str, set[str]] = {f: set() for f in FAMILIES}
# ĐIỂM CẮM (0110): "<module chủ>.<điểm>" → các thao tác gói khác cắm vào. Lõi không biết điểm nào; chỉ module chủ hỏi điểm của mình (``Ctx.plugs``).
PLUGS: dict[str, list[str]] = {}
# ĐIỂM CẮM NÚT (0124): "<module chủ>.<điểm>" → các LỆNH của gói khác thay nút ``Action(slot=điểm)`` trên màn của module chủ (``Module.plug_action``).
# Khác ``PLUGS`` (module chủ HỎI lúc chạy): nút nằm ở thực đơn app, nên chỉ cần tên lệnh; ``workspaces.py`` chọn theo gói đang bật + quyền người xem.
ACTION_PLUGS: dict[str, list[str]] = {}


@dataclass(frozen=True)
class Route:
    """ĐƯỜNG HTTP do module tự khai (``Module.route``) — kênh tức thì, tải tệp lên / về, trang dạng tệp. Lõi HTTP (``http_api.py``) gắn MỌI route
    đã khai sau route của lõi mà không biết module nào; handler là hàm FastAPI, xác thực bằng ``web.caller`` và chạy nghiệp vụ qua executor."""
    module: str
    method: str
    path: str
    handler: Callable[..., Awaitable[Any]]
    public: str = ""   # lý do route KHÔNG cần đăng nhập (vd quyền = mã link); rỗng ⇒ lõi đòi người gọi đã xác thực trước khi vào handler


ROUTES: list[Route] = []
ROUTE_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


@dataclass(frozen=True)
class Metric:
    """Chỉ số mục tiêu (0074 B) — khoá truy vấn nội bộ, nhãn, đơn vị ("khách", "giao dịch"), tài nguyên nó đếm. Số toàn công ty ⇒ chỉ người có
    phạm vi "all" trên tài nguyên đó mới chọn được (như báo cáo toàn công ty — ``min_scope="all"``)."""
    key: str
    label: str
    unit: str
    resource: str


METRICS: dict[str, Metric] = {}


@dataclass(frozen=True)
class Search:
    """Nguồn TÌM NHANH ⌘K (0087) — truy vấn nội bộ của module, nhãn nhóm kết quả ("Khách", "Tài liệu"), thứ tự nhóm (nhỏ lên trước);
    ``more`` = màn danh sách nhận ô ``q`` cùng cách khớp (``SearchTerms``) + đầu vào cố định — nguồn trả hơn 8 dòng thì ``core.find`` thêm
    dòng "xem tất cả" mở màn đó (chỉ khi người tìm mở được màn)."""
    op: str
    label: str
    rank: int
    more: tuple[str, dict[str, Any]] | None = None


SEARCHES: dict[str, Search] = {}
# CHỨC DANH HIỆN TẠI theo người (``Module.titles``): truy vấn nội bộ trả ``{user_id: chức danh}`` tại hôm nay. Lõi chỉ dùng để GHI NHÃN người (màn chọn vai)
# — không quyết quyền gì; gói khai tắt / không gói nào khai ⇒ nhãn suy từ vai + đội.
TITLES: list[str] = []


@dataclass(frozen=True)
class Trigger:
    """CÒ tự động hoá (0090) — sự kiện module phát mà quản trị đặt luật "Nếu… thì…" được. ``fields``: trường AN TOÀN để đặt điều kiện / ghép vào
    lời báo (không tên khách, không chữ tài liệu — lời báo có thể tới người không xem được đối tượng); ``open``: (truy vấn, {tham số: trường})
    để người nhận mở đúng thẻ bằng quyền của họ."""
    event_type: str
    label: str
    module: str
    fields: dict[str, str]
    open: tuple[str, dict[str, str]] | None
    # (ADR 0246) khoá MÃ trong sự kiện dùng làm đầu vào cho hành động "chạy lệnh" / "đề xuất" (khớp theo tên ô của lệnh, vd ``lead_id``) — mặc
    # định = các trường ``open`` trỏ tới; ``owner``: khoá mang NGƯỜI PHỤ TRÁCH bản ghi (hành động gửi được cho "người phụ trách")
    ids: tuple[str, ...] = ()
    owner: str = ""


TRIGGERS: dict[str, Trigger] = {}


class DateIn(BaseModel):
    """Đầu vào của NGUỒN NGÀY (``Module.date_source``, ADR 0246): bản ghi có ngày nằm trong [since, until] (cả hai đầu)."""
    model_config = ConfigDict(extra="forbid")
    since: date
    until: date


@dataclass(frozen=True)
class DateSource:
    """NGUỒN NGÀY cho luật tự động theo thời gian (ADR 0246) — module tự khai: truy vấn NỘI BỘ của chính module (``callers=("flow",)`` + ``why=``)
    nhận ``DateIn`` và trả các bản ghi còn mở có ngày trong khoảng: ``{ref, on, owner_id, <fields…>, <ids…>}`` — ``ref`` mã bản ghi (chuỗi), ``on``
    ngày của bản ghi, ``owner_id`` người phụ trách (hoặc None). ``fields``: trường AN TOÀN cho điều kiện / lời báo như ``Trigger`` (không tên khách);
    ``ids``: khoá mã làm đầu vào cho lệnh; ``date_label``: tên ngày ("hạn việc", "hẹn chăm sóc lại") để kể luật thành câu."""
    key: str
    label: str
    module: str
    date_label: str
    fields: dict[str, str]
    ids: tuple[str, ...]
    open: tuple[str, dict[str, str]] | None


DATE_SOURCES: dict[str, DateSource] = {}
DATE_RESERVED = frozenset({"ref", "on", "owner_id", "team_id"})


@dataclass(frozen=True)
class Webhook:
    """Sự kiện CÔNG BỐ cho app từ xa (0140) — tên ổn định ngoài hệ (``lead.assigned``) ↔ sự kiện outbox của module (``crm.lead_assigned``).
    ``ids``: khoá mang theo — CHỈ mã (``*_id``), app gọi ngược để đọc bằng quyền của nó. ``resource``: app phải XEM được tài nguyên này phạm vi cả
    công ty mới đăng ký được (sự kiện không lọc theo dòng — mã của dòng ngoài phạm vi hẹp không được lộ ra)."""
    name: str
    event_type: str
    module: str
    ids: tuple[str, ...]
    resource: str
    label: str


WEBHOOKS: dict[str, Webhook] = {}
# Cửa nhận webhook VÀO từ nhà cung cấp ngoài (0153): module → thao tác nội bộ nhận thân yêu cầu (tự kiểm chữ ký bằng khoá của kết nối)
INBOUND: dict[str, str] = {}
# Cửa KIỂM của webhook vào (``GET`` cùng đường dẫn): nhà cung cấp gọi để xác nhận địa chỉ trước khi gửi thư (Meta ``hub.challenge``…) — truy vấn nội bộ
# của chính module (đầu vào ``{ref, query}``) trả ``{text}``; route trả đúng chuỗi đó dạng chữ trơn. 1.31.
INBOUND_VERIFY: dict[str, str] = {}
ANY_TRIGGER = "*trigger*"   # bên nghe MỌI cò đã khai (động cơ tự động hoá) — lõi không biết cò nào


class SearchIn(BaseModel):
    """Đầu vào của thao tác TÌM (``Module.search``): từ khoá đã cắt khoảng trắng, ≥ 2 ký tự (``core.find`` kiểm trước khi gọi) — nguồn dựng
    ``SearchTerms(inp.q)`` để khớp / xếp hạng."""
    model_config = ConfigDict(extra="forbid")
    q: str
SUBSCRIBERS: dict[str, Subscriber] = {}
# Điểm cắm của quy trình (module flow, quyết định 0043) — module ngành tự khai, lõi quy trình không biết ngành nào:
#   SUBJECTS["<module>.<loại>"] = (nhãn, thao tác nội bộ trả {title, owner_id, team_id, state: active|done|cancelled, open?})
#   CHECKERS["<module>.<bộ kiểm>"] = (nhãn, loại đối tượng, gợi ý tham số) — thao tác nội bộ trả {status, value?, at?}
SUBJECTS: dict[str, tuple[str, str]] = {}
CHECKERS: dict[str, tuple[str, str, str]] = {}
# TRƯỜNG CÓ LỊCH SỬ THAY ĐỔI (ADR 0246, api 1.69): (loại đối tượng, khoá trường) ⇒ khai của module sở hữu đối tượng (``Module.tracks``); lệnh đổi trường
# gọi ``ctx.track`` — lõi ghi ``core.field_changes`` (kernel/history.py), không biết module nào.
HISTORY_FORMATS = ("text", "person", "date", "money", "number", "choice")


@dataclass(frozen=True)
class Tracked:
    kind: str
    field: str
    label: str
    module: str
    fmt: str = "text"
    choices: tuple[tuple[str, str], ...] = ()
    needs: tuple[str, ...] = ()     # nhóm thông tin / tài nguyên người xem phải có để thấy GIÁ TRỊ (0239) — thiếu ⇒ chỉ thấy "đã đổi"
    empty: str = "chưa có"          # chữ cho giá trị trống (vd "Hàng chờ" cho người phụ trách)


TRACKED: dict[tuple[str, str], Tracked] = {}
# TỆP CỦA HỒ SƠ (ADR 0249, api 1.72): loại đối tượng ⇒ khai của module sở hữu (``Module.record_files``) — đối tượng nào nhận tệp gắn vào, gợi ý vai của tệp.
# Lõi giữ bảng ``core.record_files``, lệnh gắn / gỡ, khối "Tệp" (kernel/record_files.py) — không biết module nào.


@dataclass(frozen=True)
class RecordFiles:
    kind: str
    module: str
    labels: tuple[str, ...] = ()    # gợi ý vai của tệp trên hồ sơ ("Hợp đồng", "CCCD"…) — người chọn, không bắt gõ


RECORD_FILES: dict[str, RecordFiles] = {}
# Loại CHỨNG TỪ để trích trường có nguồn (0070) — gói khai bằng ``Module.doc_type``; ``kb.extract`` đọc khuôn ở đây, không biết gói nào.
DOC_TYPES: dict[str, DocType] = {}
# Vòng quét định kỳ (sweeps.py): lệnh nội bộ khai ``sweep=`` = câu SQL trả cột ``tenant_id`` của các công ty đang có việc tới hạn cho lệnh đó.
# SQL nằm trong module (chỉ đọc bảng của chính module — test_meta kiểm như mọi SQL khác), bộ điều phối không biết ngành nào.
SWEEPS: dict[str, LiteralString] = {}


@dataclass(frozen=True)
class WorkKind:
    """Loại VIỆC THEO DÕI nhiều ngày (0021 / 0076) — gói khai bằng ``Module.work_kind``; module ``work`` (gói nền) chạy, không biết gói nào."""
    key: str
    label: str
    module: str
    op: str                   # thao tác nội bộ: {subject} ⇒ tình trạng theo DỮ LIỆU, chạy bằng quyền người phụ trách
    subject_help: str         # người / trợ lý ghi "theo dõi gì" thế nào
    tools: tuple[str, ...]    # công cụ trợ lý được dùng khi xử lý ngoại lệ (giao với công cụ người phụ trách có)


WORK_KINDS: dict[str, WorkKind] = {}


class SubjectRef(BaseModel):
    """Đầu vào của thao tác ĐỐI TƯỢNG (``Module.subject``)."""
    model_config = ConfigDict(extra="forbid")
    subject_id: UUID


class WorkIn(BaseModel):
    """Đầu vào của thao tác LOẠI VIỆC (``Module.work_kind``): đối tượng theo dõi (số hoá đơn, mã giao dịch#đợt…)."""
    model_config = ConfigDict(extra="forbid")
    subject: str


class CheckIn(BaseModel):
    """Đầu vào của BỘ KIỂM (``Module.checker``): đối tượng + tham số khai trong mẫu (vd ``seq=1``)."""
    model_config = ConfigDict(extra="forbid")
    subject_id: UUID
    args: dict[str, Any] = {}

RESOURCES: dict[str, str] = {}
RESOURCE_MODULE: dict[str, str] = {}   # tài nguyên → module khai nó (⇒ gói) — cấu hình mặc định cài quyền theo gói của tài nguyên
# Tài nguyên CỦA NHÀ VẬN HÀNH (ADR 0193 — chính sách trợ lý AI): khai như mọi tài nguyên (thao tác gắn vào được) nhưng không vai nào của khách cấp được —
# không hiện ở ma trận quyền / danh sách chọn, ``admin.set_grant`` từ chối, DB chặn bằng trigger (migration base/082); thao tác trên nó không hiện ở Nhật
# ký thao tác của khách. Nhà vận hành chạy bằng quyền hệ thống (``python -m barista.ai_policy``).
OPERATOR_RESOURCES: set[str] = set()


def grantable() -> dict[str, str]:
    """Tài nguyên quản trị công ty cấp được cho vai (mọi tài nguyên trừ tài nguyên của nhà vận hành)."""
    return {k: v for k, v in RESOURCES.items() if k not in OPERATOR_RESOURCES}


def operator_command(name: str) -> bool:
    """Thao tác thuộc tài nguyên của nhà vận hành — ẩn khỏi nhật ký khách đọc."""
    op = OPS.get(name)
    return op is not None and op.resource in OPERATOR_RESOURCES
MODULES: dict[str, str] = {}
# GÓI (quyết định 0045, 0056, 0069): bật / tắt theo từng công ty. "base" luôn bật. Gói cần gói khác ⇒ ``Pack(requires=...)``.
# Hai trục: gói NĂNG LỰC dùng chung mọi ngành (base, finance, talk…) và gói NGÀNH (nghiệp vụ của một ngành — ở repo gói). Gói ngành cắm tiền vào
# Tài chính qua khoản phải thu dùng chung (0057), không tự ghi sổ. Phần nghiệp vụ ngành nằm trong module dùng chung khai ``needs=`` gói ngành.
# KERNEL KHÔNG BIẾT TÊN GÓI NÀO (0069): mỗi gói tự khai bằng ``Pack(...)`` trong module đầu của gói (``modules/<tên>.py``), kèm vai / đội /
# chuỗi duyệt / mẫu quy trình mặc định (presets đọc từ đây) và quy tắc trợ lý; module khai màn (``Module.workspace / screens``) và mục
# "cần chú ý" (``Module.attention``). File ráp (workspaces.py, presets.py, agent/*) chỉ đọc registry — tests/test_meta.py chặn tên module lọt vào.
# LOẠI CÔNG CỤ của trợ lý (một agent, nhiều nhóm): "daily" = việc / báo cáo hằng ngày — nạp mỗi lượt; "setup" = thiết lập (danh mục gốc, quyền,
# vai, mẫu, thông số…) — ẨN tới khi trợ lý gọi "mở công cụ thiết lập" trong lượt đó. Mặc định: LỆNH GHI lên tài nguyên cấu hình (module khai
# ``Module(setup=...)``); ngoại lệ khai ``category=`` ngay tại chỗ.
TOOL_KINDS = {"daily": "Hằng ngày", "setup": "Thiết lập", "core": "Luôn mang theo",   # core: trợ lý mang MỌI lượt dù đang ở nhóm nào (0095)
              "builder": "Trình tạo"}   # builder: CHỈ có ở chế độ trình tạo (0098) — trợ lý cá nhân không bao giờ mang
# Lời dặn + công cụ thêm của từng CHẾ ĐỘ trò chuyện ngoài trợ lý cá nhân (module khai — ``Module.mode``): {chế độ: (lời dặn, công cụ thêm)}
MODES: dict[str, tuple[str, tuple[str, ...]]] = {}
# Trần số BƯỚC (lần gọi công cụ) của MỘT lượt ở chế độ đó (``Module.mode(max_steps=)``); không khai ⇒ trần chung ``agent_max_tool_calls``
MODE_STEPS: dict[str, int] = {}
SETUP_RESOURCES: set[str] = set()
# "company" (ADR 0233 D): gói RIÊNG của một công ty — bảng / nghiệp vụ / màn chỉ công ty đó có. Không lên chợ gói, không nằm trong gói mặc định của bản
# cài; chỉ công ty được nhà vận hành cấp quyền (``barista.entitle``) mới thấy và bật được. Chỉ THÊM (lệnh, màn, bên nghe, điểm cắm) — như mọi gói.
PACK_KINDS = ("core", "capability", "industry", "company")


def tool_kind(op: Op) -> str:
    # thiết lập = LỆNH GHI lên tài nguyên cấu hình; XEM cấu hình (chính sách hoa hồng, mẫu quy trình…) là câu hỏi hằng ngày
    return op.category or ("setup" if op.kind == "command" and op.resource in SETUP_RESOURCES else "daily")


@dataclass(frozen=True)
class Role:
    """Vai mặc định một gói mang vào công ty (0059): nhãn, quyền theo tài nguyên (tài nguyên, phạm vi, hành động), phân hệ chính + màn ghim
    (``home``: một phân hệ hoặc nhiều — phân hệ không thấy được trong công ty đó tự bị bỏ qua), thẻ chọn vai khi dev (``picker``: nhóm, mô tả,
    câu hỏi mẫu). Quyền lên tài nguyên của gói khác chỉ được cài khi gói đó đang bật (presets.plan)."""
    label: str
    grants: tuple[tuple[str, str, str], ...] = ()
    home: tuple[str | tuple[str, ...] | None, tuple[str, ...]] | None = None
    picker: tuple[str, str, str] | None = None
    # công ty CHƯA có chủ tài khoản đang hoạt động (0029) ⇒ người giữ vai này nhận trách nhiệm chủ tài khoản khi cài mặc định (admin.install_defaults)
    account_owner: bool = False
    # vai NHẬP MÔN (0029): mời tài khoản mới với vai này không cần chủ tài khoản duyệt (vai khác thì cần) — mặc định, công ty sửa được
    entry: bool = False
    # câu hỏi GỢI Ý của trợ lý cho vai (``(thao tác, câu hỏi, *tài nguyên cần phạm vi all)`` — hiện khi người đó chạy được thao tác) và câu XUYÊN
    # MODULE (``(câu hỏi, (thao tác cần đủ…))``) — vai của gói tự khai, trợ lý không nêu tên gói (1.8)
    suggest: tuple[tuple[str, ...], ...] = ()
    showcase: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # HẠNG TRỢ LÝ AI mặc định của vai (ADR 0193): khoá một hạng gói nền khai (``core.ai_tiers`` — model, hạn mức theo người, giọng nói). Cài qua
    # ``admin.install_defaults`` (chỉ thêm); "" = vai thuộc hạng mặc định của công ty. Công ty đổi ở Quản trị › Hạng trợ lý AI (api 1.19)
    ai_tier: str = ""
    # BỘ CÔNG CỤ TRỢ LÝ mặc định của vai (ADR 0218, api 1.43): mỗi mục là một NHÓM (khoá module — cả nhóm) hoặc một thao tác (``module.tên``) của gói
    # này hoặc gói nó cần. Trợ lý của vai mang sẵn phần này mỗi lượt (∩ quyền người hỏi ∩ chế độ trợ lý); nhóm khác mở khi cần. KHÔNG cấp quyền.
    # Cài qua ``admin.install_defaults`` (chỉ thêm); công ty sửa ở Quản trị › Công cụ trợ lý theo vai. () = gói không khai ⇒ trợ lý tự tuyển như cũ
    tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class Hints:
    """Câu gợi ý một gói GÓP cho vai của gói KHÁC (``Pack.hints``) — cùng dạng ``Role.suggest / showcase``. Khoá ``Pack.hints`` là khoá vai; khoá ``"*"`` =
    câu chung cho vai KHÔNG có gợi ý riêng. Chỉ hiện khi gói khai đang bật cho công ty và người đó chạy được thao tác (1.10)."""
    suggest: tuple[tuple[str, ...], ...] = ()
    showcase: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class InfoGroup:
    """Một NHÓM THÔNG TIN NHẠY CẢM gói khai (ADR 0239 phần 3, api 1.57) — hàng của màn "Ai thấy thông tin gì": ``resource`` là tài nguyên quyền
    (của chính gói) mà quyền ĐỌC của nó quyết định ai thấy nhóm này; ``applies`` nói ĐÚNG chỗ quyền đó được thực thi (rà Codex 0239 F2 — nhãn không
    hứa hơn mã: "số phí trên màn Marketing / Dự án", không phải "phí ở mọi nơi"); ``scopes`` = phạm vi nhóm này thực sự hiểu (sổ cái không có nghĩa
    "của mình" ⇒ chỉ ``("all",)``). Không phải bảng quyền thứ hai: màn đọc / ghi chính ``core.grants``."""
    key: str
    label: str
    resource: str
    applies: str
    scopes: tuple[str, ...] = ("own", "unit", "team", "all")
    # 1.59 (lớp 3): truy vấn của module KHÁC mà gói cắm số của nhóm này vào (qua điểm cắm, vd giá trị kết quả bán trên màn Marketing) — lõi không nêu
    # tên nhóm của gói nên không tự khai ``info=`` được; gói nói thay. Màn "Ai thấy thông tin gì" + test quét đọc cùng với ``Module.query(info=)``.
    shown_in: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not KEY.fullmatch(self.key):
            raise ValueError(f"nhóm thông tin {self.key!r}: khoá phải dạng chữ thường_gạch_dưới")
        if not self.scopes or set(self.scopes) - {"own", "unit", "team", "all"}:
            raise ValueError(f"nhóm thông tin {self.key}: phạm vi lạ {self.scopes}")


@dataclass(frozen=True)
class Question:
    """Một CÂU HỎI CẤU HÌNH khi đưa công ty vào dùng (api 1.57): người nghiệp vụ và agent cấu hình cùng đọc. ``ask``: câu hỏi bằng lời nghiệp vụ;
    ``options``: (giá trị, ý nghĩa); ``default``: giá trị gói chọn sẵn (best practice) + ``why`` lý do ngắn; ``decider``: ai trong công ty quyết
    (giám đốc / kế toán / quản trị…); ``command``: lệnh GHI cấu hình đó (đi qua executor như mọi lệnh — trợ lý chỉ đề xuất, lệnh đổi quyền giữ
    ``agent="deny"`` + chuỗi duyệt đổi quyền); ``key``: khoá cấu hình — ``grant:<vai>:<tài nguyên>`` (ô ma trận quyền), ``setting:<khoá>`` (cài đặt
    công ty 0128), ``hints:<vai>`` (câu gợi ý trợ lý), khác = khoá riêng của gói. Câu hỏi KHÔNG phải cấu hình: giá trị thật vẫn ở chỗ lệnh ghi."""
    key: str
    ask: str
    options: tuple[tuple[str, str], ...]
    default: str
    why: str
    decider: str
    command: str
    topic: str = ""

    def __post_init__(self) -> None:
        if self.default not in {v for v, _ in self.options}:
            raise ValueError(f"câu hỏi {self.key}: mặc định {self.default!r} không nằm trong lựa chọn")


def questions(on: frozenset[str] | set[str] | None = None) -> list[tuple[str, Question]]:
    """(gói, câu hỏi) của các gói ``on`` (None = mọi gói đã nạp), theo thứ tự nạp gói."""
    return [(pk.key, q) for pk in PACKS.values() if on is None or pk.key in on for q in pk.questions]


def info_groups(on: frozenset[str] | set[str] | None = None) -> list[tuple[str, InfoGroup]]:
    """(gói, nhóm) của các gói ``on`` (None = mọi gói đã nạp), theo thứ tự nạp gói; nhóm trỏ tài nguyên chưa khai / của nhà vận hành bị bỏ."""
    return [(pk.key, g) for pk in PACKS.values() if on is None or pk.key in on for g in pk.info_groups
            if g.resource in RESOURCES and g.resource not in OPERATOR_RESOURCES]


@dataclass(frozen=True)
class Flow:
    """Mẫu quy trình mặc định của gói (0043 / 0059): khoá, tên, loại đối tượng bám ("" = không bám), tự mở, nội dung; ``follow`` = trợ lý của người
    phụ trách cầm hồ sơ nhiều ngày (0142)."""
    key: str
    name: str
    subject: str
    auto_open: bool
    source: str
    follow: bool = False


@dataclass(frozen=True)
class Pack:
    """Một GÓI tự khai (0069). ``requires``: gói bắt buộc (bật gói này tức là có cả chúng). ``rules``: quy tắc trợ lý chỉ đưa vào lời dặn khi công
    ty bật gói. ``roles / teams / chains / rules_approval / flows``: cấu hình mặc định đi kèm gói (0059) — presets.plan đọc, cài qua
    ``admin.install_defaults``. ``records``: dòng cấu hình mặc định khác, cài bằng LỆNH của module sở hữu (bên cài không chạm bảng của module đó).
    ``always_on``: gói năng lực mà MỌI công ty đều cần (vd lịch & nguồn lực) — luôn bật như "base", không có dòng
    ``core.tenant_packs``, không tắt được, mặc định cài cùng "base"; chỉ ``requires`` gói luôn bật khác, không sở hữu loại đối tượng
    (``core.subject_kinds`` — chính sách RLS đọc ``tenant_packs``), không có lệnh rủi ro tiền / quyền (bỏ qua quyền dùng gói) — ``validate()`` chặn. Khai xong tự vào ``PACKS``; khai hai lần là lỗi."""
    key: str
    title: str
    description: str
    kind: str
    requires: tuple[str, ...] = ()
    rules: str = ""
    roles: dict[str, Role] = field(default_factory=dict)
    teams: tuple[tuple[str, str, str], ...] = ()                       # (mã, tên, loại)
    chains: tuple[tuple[str, str, tuple[dict[str, str], ...]], ...] = ()   # (khoá, nhãn, các bước)
    approval_rules: tuple[tuple[str, str], ...] = ()                   # (lệnh, chuỗi)
    flows: tuple[Flow, ...] = ()
    grants: tuple[tuple[str, str, str, str], ...] = ()                # (vai của gói KHÁC, tài nguyên của gói này, phạm vi, hành động)
    pins: dict[str, tuple[str | None, tuple[str, ...]]] = field(default_factory=dict)   # vai gói khác → (phân hệ chính, màn ghim) góp thêm
    hints: dict[str, Hints] = field(default_factory=dict)              # vai gói khác (hoặc "*") → câu gợi ý của trợ lý góp thêm (1.10)
    context: tuple[tuple[str, str], ...] = ()                          # (truy vấn, nhãn): danh mục ngắn nạp sẵn vào lời dặn trợ lý
    vocab: dict[str, str] = field(default_factory=dict)               # từ vựng MẶC ĐỊNH của ngành (0124): khoá khái niệm → nhãn; bật gói ⇒ công ty có sẵn
    rank_metric: str = ""                                              # chỉ số bảng xếp hạng KPI mở sẵn khi công ty bật gói này (0124) — khoá ``Module.metric``
    # dòng cấu hình MẶC ĐỊNH của gói ghi qua đúng lệnh của module sở hữu (0153): (lệnh, khoá, đầu vào) — vd chặng lead mặc định qua ``crm.save_stage``
    records: tuple[tuple[str, str, dict[str, Any]], ...] = ()
    always_on: bool = False                                            # LUÔN BẬT cho mọi công ty như "base" (0157): không tắt được, mặc định cài cho mọi công ty
    # vai của gói KHÁC → nhóm / thao tác của gói NÀY góp vào bộ công cụ trợ lý mặc định của vai đó (ADR 0218, cùng dạng ``Role.tools``; như ``pins``)
    tools: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # NHÓM THÔNG TIN NHẠY CẢM của gói (ADR 0239 phần 3, api 1.57): màn "Ai thấy thông tin gì" liệt kê nhóm × vai, ô = phạm vi quyền ĐỌC tài nguyên của nhóm
    info_groups: tuple[InfoGroup, ...] = ()
    # CÂU HỎI CẤU HÌNH khi đưa công ty vào dùng (api 1.57): câu hỏi nghiệp vụ → lựa chọn → mặc định + lý do → ai quyết → lệnh ghi (``Question``)
    questions: tuple[Question, ...] = ()

    def __post_init__(self) -> None:
        if self.key in PACKS:
            raise ValueError(f"gói {self.key} khai hai lần")
        if self.kind not in PACK_KINDS:
            raise ValueError(f"gói {self.key}: loại {self.kind} lạ")
        if self.key in self.requires or (self.key == "base" and self.requires):
            raise ValueError(f"gói {self.key}: requires không hợp lệ")
        if self.always_on and (self.key == "base" or self.kind in ("industry", "company")):
            raise ValueError(f"gói {self.key}: luôn bật chỉ dành cho gói năng lực / lõi (không phải gói ngành / gói riêng công ty, không phải base)")
        if len({g.key for g in self.info_groups}) != len(self.info_groups):
            raise ValueError(f"gói {self.key}: khoá nhóm thông tin trùng")
        PACKS[self.key] = self


PACKS: dict[str, Pack] = {}


def pack_requires(pack: str) -> tuple[str, ...]:
    p = PACKS.get(pack)
    return p.requires if p else ()


def pack_kind(pack: str, default: str = "capability") -> str:
    p = PACKS.get(pack)
    return p.kind if p else default


def pack_title(pack: str) -> str:
    p = PACKS.get(pack)
    return p.title if p else pack


def company_packs() -> frozenset[str]:
    """Gói RIÊNG công ty đang cài trên bản này (``kind="company"``) — không mặc định cho ai, chỉ theo quyền cấp từng công ty."""
    return frozenset(k for k, p in PACKS.items() if p.kind == "company")


def always_on() -> frozenset[str]:
    """Gói LUÔN BẬT ở mọi công ty: "base" + gói khai ``Pack(always_on=True)`` — không dòng ``tenant_packs``, không tắt được."""
    return frozenset({"base", *(k for k, p in PACKS.items() if p.always_on)})


def pack_order(pack: str) -> list[str]:
    """Gói + mọi gói nó bắt buộc (bắc cầu) THEO THỨ TỰ BẬT: gói được cần đứng trước gói cần nó, gói đích đứng cuối (0157)."""
    out: list[str] = []
    seen: set[str] = set()

    def visit(p: str) -> None:
        if p in seen:
            return
        seen.add(p)
        for r in pack_requires(p):
            visit(r)
        out.append(p)

    visit(pack)
    return out


def pack_closure(packs: tuple[str, ...] | set[str]) -> set[str]:
    """Gói + mọi gói nó bắt buộc (bắc cầu) — bật một gói ngành cần ``crm`` tức là có ``crm`` (và các gói nó cần)."""
    out: set[str] = set()
    todo = list(packs)
    while todo:
        p = todo.pop()
        if p not in out:
            out.add(p)
            todo.extend(pack_requires(p))
    return out


def validate() -> None:
    """Gọi SAU khi nạp hết gói (``kernel/loader.py``): mọi gói module / ``requires`` nhắc tới phải có thật (kiểm hoãn vì gói khai ở module, gói
    năng lực nạp trước gói ngành). ``needs=`` của thao tác / bên nghe được nhắc gói KHÔNG cài (bản cài chỉ có năng lực Hoa hồng, không có Bất động
    sản): phần đó coi như tắt mãi (``packs.missing`` trả gói thiếu; ``admin.set_pack`` không bật được gói không cài) — lõi vẫn chạy. Gõ sai khoá
    trong ``needs`` bắt ở ``tests/test_meta.py`` (bản cài đủ gói của repo: ``needs`` ⊆ gói đã nạp)."""
    bad = []
    bad += [f"module {m}: gói {p} lạ" for m, p in MODULE_PACK.items() if p not in PACKS]
    bad += [f"tệp của hồ sơ: {k} chưa khai m.subject (Module.record_files chỉ cho đối tượng đã khai)" for k in RECORD_FILES if k not in SUBJECTS]
    bad += [f"gói {p.key}: requires gói {r} lạ" for p in PACKS.values() for r in p.requires if r not in PACKS]
    bad += [f"gói {p.key}: gói năng lực không được bắt buộc gói ngành {r}" for p in PACKS.values() if p.kind != "industry"
            for r in p.requires if pack_kind(r) == "industry"]
    bad += [f"gói {p.key}: gói chung không được bắt buộc gói riêng công ty {r}" for p in PACKS.values() if p.kind != "company"
            for r in p.requires if pack_kind(r) == "company"]
    bad += [f"màn góp vào phân hệ {c.into} lạ" for c in scr.CONTRIB if c.into not in scr.WORKSPACES]
    bad += [f"màn riêng góp vào phân hệ {s.into} lạ" for s in scr.PERSONAL if s.into not in scr.WORKSPACES]
    bad += [f"màn riêng: truy vấn {s.query} lạ" for s in scr.PERSONAL if s.query not in OPS or OPS[s.query].kind != "query"]
    for ws in scr.assemble():
        keys = [sc.key for sc in ws.screens]
        bad += [f"phân hệ {ws.key}: màn {k} khai hai lần" for k in set(keys) if keys.count(k) > 1]
    bad += [f"gói {p.key}: chỉ số xếp hạng mặc định {p.rank_metric} chưa khai (Module.metric)" for p in PACKS.values() if p.rank_metric and p.rank_metric not in METRICS]
    slots = {a.slot for ws in scr.assemble() for s in ws.screens for a in s.actions if a.slot}
    bad += [f"điểm cắm nút {pt}: không nút nào khai slot này" for pt in ACTION_PLUGS if pt not in slots]
    bad += [f"nút slot {a.slot} của màn {s.key} có bind / prefill / preset" for ws in scr.assemble() for s in ws.screens for a in s.actions
            if a.slot and (a.bind or a.prefill or a.preset)]
    bad += [f"điểm cắm nút {pt}: lệnh {c} lạ / nội bộ" for pt, cmds in ACTION_PLUGS.items() for c in cmds
            if c not in OPS or OPS[c].kind != "command" or OPS[c].internal]
    bad += [f"gói {p.key}: từ vựng {k} chưa khai (Module.term) hoặc nhãn không hợp lệ" for p in PACKS.values() for k, v in p.vocab.items()
            if k not in vocab.TERMS or not vocab.clean(v) or len(v) > vocab.MAX_LABEL or "[[" in v]
    bad += _tool_entries_bad()
    if "base" not in PACKS:
        bad.append("thiếu gói base")
    bad += [f"gói {p.key}: luôn bật nhưng requires gói không luôn bật ({', '.join(sorted(set(p.requires) - always_on()))})"
            for p in PACKS.values() if p.always_on and set(p.requires) - always_on()]
    # gói luôn bật bỏ qua quyền dùng gói (0065) ⇒ không được mang lệnh tiền / quyền; không sở hữu loại đối tượng (RLS kinds_off đọc tenant_packs)
    from barista.kernel.agent_policy import risk_of  # nội bộ lõi; nạp muộn tránh vòng import
    fixed = {p.key for p in PACKS.values() if p.always_on}
    bad += [f"gói {MODULE_PACK[op.module]}: luôn bật nhưng có lệnh rủi ro {risk_of(op)} ({name}) — gói tiền / quyền phải qua quyền dùng gói"
            for name, op in OPS.items() if op.kind == "command" and MODULE_PACK.get(op.module) in fixed and risk_of(op) in ("money", "authority")]
    bad += [f"gói {MODULE_PACK[s.split('.')[0]]}: luôn bật nhưng sở hữu loại đối tượng {s} — RLS coi loại của gói không có dòng tenant_packs là tắt"
            for s in SUBJECTS if MODULE_PACK.get(s.split(".")[0]) in fixed]
    if bad:
        raise RuntimeError("; ".join(bad))


def tool_entry_module(entry: str) -> str | None:
    """Mục của bộ công cụ trợ lý (0218): khoá module (cả nhóm) hoặc tên thao tác — trả module của mục, None nếu không có / thao tác nội bộ."""
    if "." not in entry:
        return entry if entry in MODULES else None
    op = OPS.get(entry)
    return op.module if op is not None and not op.internal else None


def _tool_entries_bad() -> list[str]:
    """``Role.tools`` / ``Pack.tools`` chỉ nhắc nhóm / thao tác có thật của CHÍNH gói khai hoặc gói nó cần (luôn nạp cùng) — gõ sai bắt lúc khởi động."""
    bad = []
    for p in PACKS.values():
        own = pack_closure({p.key}) | always_on()
        entries = [(f"vai {k}", e) for k, r in p.roles.items() for e in r.tools] + [(f"vai {k} (góp)", e) for k, es in p.tools.items() for e in es]
        for who, e in entries:
            mod = tool_entry_module(e)
            if mod is None:
                bad.append(f"gói {p.key}: {who} — công cụ trợ lý {e} không có (khoá module hoặc thao tác không nội bộ)")
            elif MODULE_PACK.get(mod) not in own:
                bad.append(f"gói {p.key}: {who} — {e} thuộc gói {MODULE_PACK.get(mod)} (gói chỉ khai nhóm / thao tác của mình hoặc gói nó cần)")
    return bad


MODULE_PACK: dict[str, str] = {}
PACK_MODULE: dict[str, str] = {}   # gói → module TIÊU BIỂU = module khai ``Pack(...)`` (biểu tượng / màu thẻ gói vẽ theo module này)


def pack_module(pack: str) -> str:
    return PACK_MODULE.get(pack, "")


def table_row_model(model: type[BaseModel], table: ImportTable) -> type[BaseModel] | None:
    """Mô hình DÒNG của trường danh sách ``table.field`` trong đầu vào lệnh (None nếu khai sai)."""
    f = model.model_fields.get(table.field)
    args = typing.get_args(f.annotation) if f is not None else ()
    inner = args[0] if typing.get_origin(f.annotation if f is not None else None) is list and args else None
    return inner if isinstance(inner, type) and issubclass(inner, BaseModel) else None


def validate_import_table(full: str, table: ImportTable, model: type[BaseModel]) -> None:
    row = table_row_model(model, table)
    if row is None:
        raise ValueError(f"{full}: import_table.field phải là trường list[mô hình dòng] của đầu vào lệnh")
    if "line" not in row.model_fields:
        raise ValueError(f"{full}: mô hình dòng của import_table cần ô ẩn line: int (số dòng Excel)")
    if unknown := [k for k in table.preset if k not in model.model_fields or k == table.field]:
        raise ValueError(f"{full}: import_table.preset có trường không có trong đầu vào lệnh: {unknown}")


def _input_model(fn: Callable[..., Any]) -> type[BaseModel]:
    params = list(inspect.signature(fn).parameters)
    if len(params) != 2:
        raise TypeError(f"{fn.__qualname__}: handler phải có dạng (ctx, inp)")
    model = typing.get_type_hints(fn).get(params[1])
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise TypeError(f"{fn.__qualname__}: tham số thứ hai phải là model Pydantic")
    return model


class Module:
    def __init__(self, key: str, title: str, *, pack: str, resources: dict[str, str] | None = None,
                 setup: tuple[str, ...] = ()) -> None:
        """``setup``: tài nguyên CẤU HÌNH của module (lệnh ghi lên đó là công cụ "thiết lập" của trợ lý — ``tool_kind``). Khoá module / gói là chữ
        thường + gạch dưới (không dấu chấm: tên thao tác / bên nghe là ``<module>.<tên>``, hợp đồng ``callers=`` so theo khoá module)."""
        if not KEY.fullmatch(key) or not KEY.fullmatch(pack):
            raise ValueError(f"module {key!r} / gói {pack!r}: khoá phải dạng chữ thường_gạch_dưới (^[a-z][a-z0-9_]*$)")
        if pack == "base" and key not in BASE_MODULES:
            raise ValueError(f"module {key}: gói nền chỉ gồm các module lõi trong repo ({', '.join(sorted(BASE_MODULES))}) — gói khác khai Pack(...) riêng")
        if pack != "base" and pack not in PACKS:
            raise ValueError(f"module {key}: gói {pack} chưa khai — Pack({pack!r}, …) đứng trước Module trong module đầu của gói")
        if key in MODULES:
            raise ValueError(f"module {key} khai hai lần")
        self.key = key
        MODULES[key] = title
        MODULE_PACK[key] = pack
        if pack in PACKS and pack not in PACK_MODULE:   # module đầu tiên nạp SAU khi gói khai = module khai gói đó
            PACK_MODULE[pack] = key
        for r, label in (resources or {}).items():
            if r in RESOURCES:
                raise ValueError(f"tài nguyên {r} khai hai lần")
            RESOURCES[r] = label
            RESOURCE_MODULE[r] = key
        if unknown := set(setup) - set(resources or {}):
            raise ValueError(f"module {key}: tài nguyên thiết lập {unknown} chưa khai")
        SETUP_RESOURCES.update(setup)

    def _register(self, kind: Kind, name: str, resource: str | None, action: str, title: str, description: str,
                  internal: bool, importable: bool, min_scope: str | None = None, agent: str = "allow",
                  shows: tuple[str, str] | None = None, needs: tuple[str, ...] = (),
                  public: str | None = None, category: str = "", sweep: LiteralString = "",
                  basis: Basis | None = None, callers: tuple[str, ...] = (),
                  why: str = "", family: str = "", aside: bool = False,
                  sensitive: Sensitive | None = None, apps: bool = False,
                  prepare: Callable[..., Awaitable[Any]] | None = None,
                  promotable: bool = False, target: str = "",
                  undone_by: tuple[str, ...] = (), risk: str = "",
                  import_rows: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None,
                  pin: Pin | None = None, consistent: bool = False, import_key: ImportKey | None = None,
                  import_table: ImportTable | None = None, extra: str = "",
                  info: tuple[str, ...] = (), auto: bool = False, intent: tuple[str, ...] = (),
                  basis_required: str = "") -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
        full = f"{self.key}.{name}"
        if extra and not (kind == "command" and extra in extra_fields.SUBJECTS and extra_fields.SUBJECTS[extra].module == self.key):
            raise ValueError(f"{full}: extra={extra!r} chỉ cho lệnh của module đã khai đối tượng đó (Module.extensible)")
        if not NAME.fullmatch(name):
            raise ValueError(f"{full}: tên thao tác phải dạng chữ thường_gạch_dưới")
        if sweep and not (kind == "command" and internal):
            raise ValueError(f"{full}: chỉ lệnh nội bộ mới vào vòng quét định kỳ")
        if action not in ACTIONS:
            raise ValueError(f"{full}: hành động {action} lạ")
        if resource is not None and resource not in RESOURCES:
            raise ValueError(f"{full}: tài nguyên {resource} chưa khai")
        if full in OPS:
            raise ValueError(f"{full} khai hai lần")
        if agent not in AGENT_MODES:
            raise ValueError(f"{full}: chế độ agent {agent} lạ")
        if category not in ("", *TOOL_KINDS):
            raise ValueError(f"{full}: loại công cụ {category} lạ")
        if (callers or why) and not (internal and (callers or family) and why.strip()):
            raise ValueError(f"{full}: hợp đồng quyền hệ thống cần internal=True + callers (hoặc family) + why")
        if aside and not (kind == "command" and agent == "propose"):
            raise ValueError(f"{full}: đề xuất bên lề chỉ cho lệnh trợ lý soạn thẻ (agent='propose')")
        if promotable and not (kind == "command" and agent == "propose" and not internal and resource is not None):
            raise ValueError(f"{full}: promotable (0143) chỉ cho lệnh nghiệp vụ trợ lý soạn thẻ (agent='propose')")
        if promotable and not (target and undone_by):
            raise ValueError(f"{full}: promotable (0143) cần target= + undone_by= — không biết khi nào người đảo việc trợ lý thì không đo được đúng / sai")
        if import_rows is not None and not (kind == "command" and importable):
            raise ValueError(f"{full}: import_rows chỉ cho lệnh nhập hàng loạt (importable=True)")
        if import_key is not None and not (kind == "command" and importable and import_key.fields):
            raise ValueError(f"{full}: import_key chỉ cho lệnh nhập hàng loạt (importable=True) và phải có ít nhất một trường khoá")
        if import_table is not None:
            if not (kind == "command" and importable) or import_key is not None or import_rows is not None:
                raise ValueError(f"{full}: import_table chỉ cho lệnh nhập hàng loạt (importable=True), không đi cùng import_key / import_rows")
        if risk and risk not in ("read", "low", "money", "authority"):
            raise ValueError(f"{full}: mức rủi ro {risk} lạ (0144)")
        if risk in ("money", "authority") and (agent == "allow" or promotable):
            raise ValueError(f"{full}: rủi ro {risk} không bao giờ để trợ lý tự làm / nới được (0144) — hạ agent= hoặc bỏ promotable")
        if promotable and not risk:
            raise ValueError(f"{full}: promotable (0143) phải khai risk=\"low\" tường minh — lệnh nới được cho trợ lý tự làm không được dựa vào suy diễn "
                             f"(agent_policy.risk_of đoán \"low\" cho mọi lệnh không có ô tiền ngoài module quản trị / lõi)")
        if auto and not (kind == "command" and not internal and public is None and agent != "deny" and risk == "low"):
            raise ValueError(f"{full}: auto=True (lệnh luật tự động được chạy — ADR 0246) chỉ cho lệnh ghi không nội bộ, không link công khai, trợ lý không bị "
                             f"cấm, khai risk=\"low\" tường minh")
        if promotable and risk != "low":
            raise ValueError(f"{full}: chỉ lệnh rủi ro low mới nới được (0144)")

        def deco(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            validate_pin(full, pin, _input_model(fn))
            if import_table is not None:
                validate_import_table(full, import_table, _input_model(fn))
            if intent:
                validate_intent(full, kind, internal, intent, _input_model(fn))
            if extra and "extra" not in _input_model(fn).model_fields:
                raise ValueError(f"{full}: lệnh khai extra= cần ô extra: dict[str, Any] trong đầu vào (fields.extra_values())")
            OPS[full] = Op(full, kind, self.key, resource, action, fn, _input_model(fn), title,
                           description or (fn.__doc__ or "").strip(), internal, importable, min_scope, agent, shows, needs, category, public,
                           basis, callers, why, family, description, aside, sensitive, apps, prepare, promotable, target, undone_by, risk,
                           import_rows, pin, consistent, import_key, import_table, extra, info, auto, intent, basis_required)
            if sweep:
                SWEEPS[full] = sweep
            return fn

        return deco

    def command(self, name: str, *, resource: str | None, title: str, action: str = "write", description: str = "",
                internal: bool = False, importable: bool = False, agent: str = "propose",
                shows: tuple[str, str] | None = None, needs: tuple[str, ...] = (), public: str | None = None, category: str = "",
                sweep: LiteralString = "", basis: Basis | None = None, callers: tuple[str, ...] = (), why: str = "", family: str = "", aside: bool = False,
                sensitive: Sensitive | None = None, apps: bool = False, promotable: bool = False, target: str = "",
                undone_by: tuple[str, ...] = (), risk: str = "", min_scope: str | None = None,
                import_rows: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None, pin: Pin | None = None,
                prepare: Callable[..., Awaitable[Any]] | None = None, consistent: bool = False, import_key: ImportKey | None = None,
                import_table: ImportTable | None = None, extra: str = "", auto: bool = False, intent: tuple[str, ...] = (), basis_required: str = ""):
        """``intent`` (ADR 0250, 1.72): ô Ý ĐỊNH của form — form mở ra chỉ hỏi các ô này + một ô "Mô tả / thả file"; "Soạn giúp" (``core.draft_input``) để trợ lý
        soạn ĐỦ đầu vào của đúng lệnh này (chip máy đoán, người sửa rồi gửi qua executor như thường). Khai ô người biết mà máy không đoán được (việc gì, mốc
        nào, ai chịu trách nhiệm) — không khai ô máy suy ra được (danh sách việc, hạn từng việc). Không khai ⇒ form đủ ô như cũ.
        ``auto`` (ADR 0246, 1.69): luật tự động hoá của công ty được CHẠY lệnh này (hành động "chạy lệnh") — không người bấm, bằng quyền người
        đặt luật nạp lại mỗi lần, khoá chống lặp theo (luật, sự kiện / bản ghi + ngày). Chọn có chủ đích: chỉ việc ghi rủi ro thấp, đảo được
        (``risk="low"`` tường minh); lệnh tiền / quyền / duyệt không bao giờ.
        ``import_table``: nhập CẢ BẢNG làm một lần gọi (1.48) — xem ``ImportTable``.
        ``import_key``: khoá tự nhiên khi nhập Excel (0226, 1.46) — màn nhập báo "cập nhật / thêm mới / trùng" thay vì chạy mù; xem ``ImportKey``.
        ``consistent``: chạy cả lệnh trong MỘT ảnh dữ liệu (REPEATABLE READ — 1.35) — xem ``Op.consistent``.
        ``agent``: trần khi trợ lý làm thay. Mặc định ``propose`` — lệnh ghi do agent chỉ thành đề xuất, người bấm mới chạy.
        ``needs``: gói nghiệp vụ lệnh cần thêm (tắt ⇒ lệnh coi như không có, nút không hiện).
        ``sweep``: lệnh nội bộ chạy trong vòng quét định kỳ — SQL trả ``tenant_id`` các công ty đang có việc tới hạn (``SWEEPS``).
        ``basis``: căn cứ của lệnh (0071) — ``async (ctx, input) -> {nhãn người đọc: dấu phiên bản}``; cần tuyệt đối không khe thì khoá dòng
        (``FOR SHARE``) trong hàm. ``basis_required`` (0251): câu báo khi người bấm KHÔNG nêu căn cứ đã thấy mà căn cứ hiện tại khác rỗng ⇒ không chạy.
        ``callers`` + ``why``: hợp đồng quyền hệ thống (module được ``call_system`` tới lệnh này, lý do + quyền mở tới đâu).
        ``family``: dịch vụ lõi mở cho gói ngoài mà lõi không biết tên — module nào khai ``consumes(family)`` cũng được ``call_system`` (cùng ``callers=``).
        ``aside``: thẻ đề xuất của trợ lý là gợi ý BÊN LỀ — không chặn lượt trả lời (việc phụ người làm khi tiện, vd ghi nhớ).
        ``sensitive``: lần gọi nào thuộc luật duyệt công ty đặt cho lệnh này + mô tả cho người duyệt (0029) — None = mọi lần gọi.
        ``apps``: app từ xa được chạy lệnh này DÙ trợ lý bị cấm (``agent="deny"``) — chọn có chủ đích (0140).
        ``promotable``: công ty được nới trợ lý từ "propose" lên "allow" theo thang tin cậy (0143) — chỉ lệnh ghi rủi ro thấp, đảo được; kèm
        ``target`` (ô đối tượng trong đầu vào / kết quả) + ``undone_by`` (lệnh của người coi là đảo — "lệnh" hoặc "lệnh:ô=giá trị").
        ``min_scope``: phạm vi tối thiểu trên tài nguyên để CHẠY lệnh ("team" = trưởng nhóm trở lên) — cùng cửa ``executor.allowed`` với truy vấn, nên lệnh
        không phải của người này thì KHÔNG thành công cụ của trợ lý / nút trên app (thay vì để trợ lý thử rồi ``denied`` trong handler).
        ``prepare``: ``async (prep: Prep, đầu vào) → giá trị`` — bước gọi mạng (model, nhà cung cấp) chạy NGOÀI giao dịch ngay trước khi handler chạy THẬT
        (không khi chạy thử / soạn đề xuất / gửi phiếu duyệt / phát lại); đọc qua ``prep.query``, model qua ``ai.run(prep, …)``; handler đọc
        ``ctx.prepared`` (None khi chạy thử hoặc gọi lồng / sau duyệt — handler tự xử lý) và kiểm lại điều nó dựa vào trước khi ghi (ADR 0200).
        Lệnh nội bộ chỉ có ``prepare`` khi là VÒNG QUÉT (``sweep=``, 1.31): vòng quét chạy thẳng ở cửa executor bằng quyền hệ thống của công ty ⇒ bước
        chuẩn bị gọi nhà cung cấp ngoài giao dịch (``prep.query`` đọc bằng quyền hệ thống, giao dịch đọc ngắn), handler ghi kết quả."""
        if prepare is not None and internal and not sweep:
            raise ValueError(f"{self.key}.{name}: prepare chỉ cho lệnh người gọi trực tiếp hoặc vòng quét (không cho lệnh nội bộ khác)")
        if basis_required and basis is None:
            raise ValueError(f"{self.key}.{name}: basis_required cần basis=")
        return self._register("command", name, resource, action, title, description, internal, importable, agent=agent,
                              shows=shows, needs=needs, public=public, category=category, sweep=sweep, basis=basis, callers=callers, why=why,
                              family=family, aside=aside, sensitive=sensitive, apps=apps, promotable=promotable, target=target, undone_by=undone_by, risk=risk,
                              import_rows=import_rows, pin=pin, min_scope=min_scope, prepare=prepare, consistent=consistent, import_key=import_key, import_table=import_table, extra=extra,
                              auto=auto, intent=intent, basis_required=basis_required)

    def query(self, name: str, *, resource: str | None, title: str, action: str = "read", description: str = "",
              internal: bool = False, min_scope: str | None = None, agent: str = "allow", needs: tuple[str, ...] = (),
              public: str | None = None, category: str = "", callers: tuple[str, ...] = (), why: str = "", family: str = "",
              prepare: Callable[..., Awaitable[Any]] | None = None, info: tuple[str, ...] = (), creates: str = ""):
        """``min_scope="all"``: báo cáo toàn công ty — người phạm vi hẹp hơn không chạy được (thay vì ra số thiếu).
        ``info`` (1.59): nhóm thông tin nhạy cảm đầu ra có thể chứa ở phần khai ``needs=`` (khoá ``InfoGroup`` hoặc tài nguyên) — màn "Ai thấy thông
        tin gì" đọc khai này để nói bật / tắt một nhóm ảnh hưởng tới màn nào.
        ``callers`` + ``why``: hợp đồng quyền hệ thống — module được ``call_system`` tới truy vấn này, lý do + trả những trường nào.
        ``prepare``: bước gọi mạng chạy NGOÀI giao dịch trước handler (kết quả ở ``ctx.prepared``) — không giữ kết nối DB khi chờ nhà cung cấp.
        ``creates`` (1.71): truy vấn là danh sách CHỌN của một ô — tên lệnh tạo thêm một mục của danh sách đó (``<module>.<lệnh>``). Danh sách rỗng ⇒ ô hiện
        "Chưa có … — Tạo mới" mở form lệnh đó (người không có quyền lệnh thì không thấy nút), tạo xong ô tải lại. Khai MỘT lần ở truy vấn chọn, mọi form dùng ô đó đều có."""
        if creates:
            CREATES[f"{self.key}.{name}"] = creates
        return self._register("query", name, resource, action, title, description, internal, False, min_scope, agent, needs=needs, public=public,
                              category=category, callers=callers, why=why, family=family, prepare=prepare, info=info)

    def subject(self, kind: str, label: str):
        """Khai một loại ĐỐI TƯỢNG mà quy trình có thể bám (giao dịch, hợp đồng, học viên…): thao tác nội bộ nhận ``{subject_id}``,
        trả ``{title, owner_id, team_id, state, open}``. Module phát ``subject.created`` / ``subject.changed`` khi dữ kiện đổi."""
        SUBJECTS[kind] = (label, f"{self.key}.subject_{kind.split('.')[-1]}")
        return self.query(f"subject_{kind.split('.')[-1]}", resource=None, title=f"Đối tượng quy trình: {label}", internal=True, family="subject")

    def tracks(self, kind: str, field: str, label: str, *, fmt: str = "text", choices: dict[str, str] | None = None,
               needs: str | tuple[str, ...] | list[str] | None = None, empty: str = "chưa có") -> str:
        """Khai một TRƯỜNG CÓ LỊCH SỬ THAY ĐỔI của đối tượng ``kind`` (ADR 0246, api 1.69) — chỉ đối tượng của chính module (``<module>.<loại>``, đã khai
        ``m.subject``). Lệnh đổi trường gọi ``await ctx.track(kind, id, field, cũ, mới)`` trong CÙNG giao dịch; lõi ghi ai / lúc nào / qua lệnh nào / người
        hay trợ lý hay luật tự động, ai thấy đối tượng thì thấy lịch sử. ``fmt``: cách hiện giá trị (text · person = mã người · date · money = đồng ·
        number · choice + ``choices`` mã ⇒ nhãn). ``needs``: nhóm thông tin nhạy cảm (0239) — người xem thiếu quyền chỉ thấy "đã đổi", không thấy giá trị."""
        if kind.split(".")[0] != self.key:
            raise ValueError(f"module {self.key}: chỉ khai lịch sử cho đối tượng của chính mình ({self.key}.<loại>), không cho {kind}")
        if not KEY.fullmatch(field) or fmt not in HISTORY_FORMATS or bool(choices) != (fmt == "choice"):
            raise ValueError(f"lịch sử {kind}.{field}: khoá chữ thường_gạch_dưới, kiểu một trong {HISTORY_FORMATS}, ``choices`` đi cùng kiểu choice")
        if (kind, field) in TRACKED:
            raise ValueError(f"lịch sử {kind}.{field} khai hai lần")
        TRACKED[(kind, field)] = Tracked(kind, field, label, self.key, fmt, tuple((choices or {}).items()),
                                         (needs,) if isinstance(needs, str) else tuple(needs or ()), empty)
        return field

    def record_files(self, kind: str, *, labels: tuple[str, ...] | list[str] = ()) -> str:
        """Khai đối tượng ``kind`` (của chính module, đã khai ``m.subject``) NHẬN TỆP CỦA HỒ SƠ (ADR 0249, api 1.72): hợp đồng của giao dịch, CCCD của khách,
        chứng từ của một bộ hồ sơ. Lõi giữ tệp (``core.record_files``, quyền = ai thấy đối tượng), lệnh ``core.attach_file`` / ``core.remove_file`` và khối
        "Tệp" — thẻ của module đặt ``await record_files.block(ctx, kind, id)`` cạnh khối trao đổi / lịch sử. ``labels``: gợi ý vai của tệp (chọn, không gõ)."""
        if kind.split(".")[0] != self.key:
            raise ValueError(f"module {self.key}: chỉ khai tệp của hồ sơ cho đối tượng của chính mình ({self.key}.<loại>), không cho {kind}")
        if kind in RECORD_FILES:
            raise ValueError(f"tệp của hồ sơ {kind} khai hai lần")
        clean = tuple(x.strip() for x in labels if x.strip())
        if any(len(x) > 80 for x in clean):
            raise ValueError(f"tệp của hồ sơ {kind}: nhãn vai tệp tối đa 80 ký tự")
        RECORD_FILES[kind] = RecordFiles(kind, self.key, clean)
        return kind

    def checker(self, name: str, label: str, subject: str, args: str = ""):
        """Khai một BỘ KIỂM mục hồ sơ bằng dữ kiện thật (đợt đã thu, hợp đồng đã ký…): thao tác nội bộ nhận ``{subject_id, args}``,
        trả ``{status: missing|received|verified, value?, at?}``. Model / người không tự khai "đạt" cho mục có bộ kiểm."""
        CHECKERS[f"{self.key}.{name}"] = (label, subject, args)
        return self.query(name, resource=None, title=f"Kiểm: {label}", internal=True, family="check")

    def doc_type(self, name: str, label: str, fields: tuple[F, ...], *, help: str = "") -> DocType:
        """Khai một loại CHỨNG TỪ mà ``kb.extract`` trích được thành ô có nguồn (0070): mỗi ô ``F(key, nhãn, kiểu)`` — model điền giá trị +
        trang + câu nguyên văn, CODE đọc lại câu ra đúng giá trị mới tính. Gói của module tắt ⇒ không trích được loại này."""
        key = f"{self.key}.{name}"
        if key in DOC_TYPES:
            raise ValueError(f"loại chứng từ {key} khai hai lần")
        keys = [f.key for f in fields]
        if not fields or len(set(keys)) != len(keys):
            raise ValueError(f"loại chứng từ {key}: cần ô, không trùng khoá")
        if bad := [f.key for f in fields if f.kind not in KINDS]:
            raise ValueError(f"loại chứng từ {key}: kiểu ô lạ {bad}")
        DOC_TYPES[key] = DocType(key, label, self.key, fields, help)
        return DOC_TYPES[key]

    def work_kind(self, key: str, label: str, *, subject_help: str, tools: tuple[str, ...] = ()):
        """Khai một LOẠI VIỆC THEO DÕI (0076): thao tác nội bộ nhận ``{subject}``, chạy bằng QUYỀN NGƯỜI PHỤ TRÁCH (không thấy ⇒ ``Denied`` ⇒
        việc dừng), trả ``{ref, title, state: done|cancelled|wait|open, facts: {…, due?, late_days?}, summary, closing?, task, fallback,
        draft?: {command, input, title}}`` — ``task`` là lời dặn trợ lý khi quá hạn, ``fallback`` là lời báo khi không có model, ``draft``
        là lệnh soạn sẵn chạy bằng trợ lý của người phụ trách; ``attention`` (tuỳ chọn) = cần người SỚM dù chưa quá hạn (0142 lát 2 — nhắc lại theo
        nhịp riêng). Điều kiện xong do CODE của gói kiểm, không do model khai."""
        if key in WORK_KINDS:
            raise ValueError(f"loại việc {key} khai hai lần")
        WORK_KINDS[key] = WorkKind(key, label, self.key, f"{self.key}.work_{key}", subject_help, tools)
        return self.query(f"work_{key}", resource=None, title=f"Việc theo dõi: {label}", internal=True, family="work")

    def plug(self, point: str, label: str, *, resource: str | None = None, action: str = "read",
             prepare: Callable[..., Awaitable[Any]] | None = None):
        """Cắm vào điểm ``<module chủ>.<điểm>`` của một module năng lực (0110): thao tác nội bộ của module này, module chủ hỏi qua ``ctx.plugs``.
        Trả ``None`` = không có gì để góp. ``resource``: quyền người xem phải có để phần này hiện (thiếu quyền ⇒ bỏ phần đó, thẻ chính vẫn mở).
        ``prepare`` (1.37): ``async (Prep, đầu vào) → giá trị`` — phần GỌI NHÀ CUNG CẤP của phần cắm (vd ghi lên kênh quảng cáo — 0209), chạy NGOÀI mọi
        giao dịch, không giữ kết nối DB, khi module chủ hỏi qua ``Prep.plugs`` (bước chuẩn bị của lệnh / vòng quét); gói của phần cắm phải đang bật TRƯỚC
        khi bước này chạy. Đọc DB bằng ``prep.query`` (truy vấn ngắn, quyền hệ thống); handler nhận kết quả ở ``ctx.prepared`` (giao dịch đọc ngắn). Hỏi
        qua ``ctx.plugs`` (trong giao dịch của bên hỏi) ⇒ bước này KHÔNG chạy, ``ctx.prepared`` None — phần cắm gọi mạng phải từ chối (``Invalid``) chứ
        không gọi nhà cung cấp trong giao dịch."""
        short = point.split(".")[-1]
        PLUGS.setdefault(point, []).append(f"{self.key}.plug_{short}")
        return self.query(f"plug_{short}", resource=resource, action=action, title=label, internal=True, agent="deny", family="plug", prepare=prepare)

    def connector(self, cls: Any) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
        """Khai một BỘ NỐI ra hệ ngoài (ADR 0244, api 1.65) + LỆNH NHẬN của gói: ``@m.connector(BộNối)`` trên ``async (ctx, inp: connector.Delivery)``.
        Lõi (module ``connectors``) giữ kết nối / khoá / lịch kéo / webhook và giao TỪNG MỤC cho lệnh này bằng quyền hệ thống của công ty, trong điểm lưu
        riêng; lệnh nhận ghi bằng lệnh của module sở hữu dữ liệu và chống lặp theo mã của hệ ngoài. Lưu / mở kết nối: ``barista.api.connectors`` (họ
        ``connector`` — gói khai ``consumes("connector")``)."""
        from barista.kernel import connectors as kc

        def deco(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            name = f"connector_{getattr(cls, 'key', '')}"
            kc.register(cls, self.key, f"{self.key}.{name}")
            fn.__name__ = name
            return self.command(name, resource=None, title=f"Nhận dữ liệu: {cls.label}", internal=True, agent="deny", callers=("connectors",),
                                why=f"connectors.sync / connectors.receive_hook (ADR 0244): lõi giao từng mục bộ nối “{cls.key}” kéo / nhận webhook về cho gói "
                                    "sở hữu bộ nối, bằng quyền hệ thống của công ty có kết nối — mục đã do bộ nối của gói đổi về dạng của gói")(fn)
        return deco

    def term(self, key: str, label: str, meaning: str) -> None:
        """Khai một KHÁI NIỆM có nhãn riêng từng công ty (0124): ``label`` là chữ trung tính mặc định, ``meaning`` giải thích cho quản trị / trợ lý (không
        chứa nhãn). Chuỗi người đọc thấy dùng dấu ``[[khoá]]`` / ``[[khoá|l]]`` (kernel/vocab.py); gói ngành đặt nhãn mặc định ở ``Pack(vocab=...)``."""
        vocab.declare(key, label, meaning)

    def extensible(self, subject: str, label: str) -> str:
        """Khai một ĐỐI TƯỢNG công ty được tự thêm trường (0060, 1.50) — vd ``m.extensible("lead", "Khách / lead")``. Module giữ giá trị trong cột
        ``extra jsonb`` của bảng đối tượng; lệnh tạo / sửa khai ``extra=<đối tượng>``; thẻ chi tiết hiện bằng ``extra_fields.shown``. Không dùng cho
        tiền, quyền, trạng thái nghiệp vụ — những thứ đó là hợp đồng của module (code + test)."""
        return extra_fields.declare(subject, label, self.key).key

    def setting(self, name: str, label: str, *, default: bool, help: str) -> str:
        """Khai một CÀI ĐẶT CÔNG TY bật / tắt (0128) — khoá ``<module>.<tên>``. ``default`` đi kèm gói của module: bật gói ⇒ công ty có dòng mặc định
        (``admin.install_defaults``); quản trị đổi ở Quản trị › Cài đặt công ty. Module đọc bằng ``company_settings.enabled``."""
        return company_settings.declare(f"{self.key}.{name}", label, default=default, help=help, module=self.key).key

    def route(self, method: str, path: str, *, public: str = "") -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
        """Khai một ĐƯỜNG HTTP của module (``/v1/...``) — chỉ cho thứ không đi được qua ``/v1/commands`` / ``/v1/queries``: kênh SSE, thân tệp lên /
        xuống, tệp xem trước. Handler là hàm FastAPI (tham số đường dẫn / truy vấn như thường), xác thực bằng ``web.caller(request)`` (hoặc
        ``web.Listener`` / ``web.link_caller``) và đọc / ghi CHỈ qua executor (``web.query`` / ``web.run``). Gói tắt / chưa cài ⇒ route không có.
        MẶC ĐỊNH CẦN ĐĂNG NHẬP: lõi xác thực (phiên / dev; token app từ xa bị từ chối) TRƯỚC handler — thiếu ⇒ 401. ``public="<lý do>"``: route không
        cần đăng nhập (quyền nằm ở thứ khác, vd mã link), phải ghi lý do; danh sách route công khai được test chốt."""
        method = method.upper()
        if method not in ROUTE_METHODS:
            raise ValueError(f"module {self.key}: phương thức {method} lạ")
        if not path.startswith("/v1/"):
            raise ValueError(f"module {self.key}: đường {path} phải bắt đầu bằng /v1/")
        if public and len(public.strip()) < 10:
            raise ValueError(f"module {self.key}: route công khai {path} cần lý do (public=\"<vì sao không cần đăng nhập>\")")
        if any(r.method == method and r.path == path for r in ROUTES):
            raise ValueError(f"module {self.key}: {method} {path} đã có module khác khai")

        def deco(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            ROUTES.append(Route(self.key, method, path, fn, public.strip()))
            return fn

        return deco

    def plug_action(self, point: str, command: str) -> None:
        """Lệnh ``command`` của module này THAY nút ``Action(slot=point)`` của module chủ (0124) khi gói của lệnh bật và người xem chạy được nó —
        vd gói ngành thay nút "Thêm khách / lead" chung bằng form giàu hơn của ngành. Module chủ không biết gói nào; không ai cắm ⇒ nút gốc."""
        if not command.startswith(self.key + "."):
            raise ValueError(f"module {self.key}: chỉ cắm lệnh của chính module, không phải {command}")
        if point.split(".")[0] == self.key:
            raise ValueError(f"module {self.key}: điểm {point} là của chính module — cắm vào điểm của module khác")
        ACTION_PLUGS.setdefault(point, []).append(command)

    def consumes(self, *families: str) -> None:
        """Module này gọi các thao tác thuộc HỌ (``FAMILIES``) của module khác bằng quyền hệ thống (vd quy trình đọc đối tượng / bộ kiểm / chỉ số
        của gói ngành). Không khai ⇒ executor từ chối ``call_system`` tới thao tác của họ đó."""
        if bad := [f for f in families if f not in FAMILIES]:
            raise ValueError(f"module {self.key}: họ thao tác lạ {bad}")
        for f in families:
            FAMILY_CALLERS[f].add(self.key)

    def pack_blockers(self):
        """Khai điều kiện CHẶN TẮT gói của module (như khoá ngoại RESTRICT): thao tác nội bộ trả danh sách lý do — còn hợp đồng đang chạy, tiền
        chưa xử lý, phiếu đang chờ… ⇒ quản trị không tắt được gói tới khi xử lý xong (``admin.set_pack``)."""
        return self.query("pack_blockers", resource=None, title="Điều kiện chặn tắt gói", internal=True, agent="deny", family="blockers")

    def metric(self, name: str, label: str, unit: str, *, resource: str) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
        """CHỈ SỐ cho mục tiêu (0074 B): truy vấn nội bộ nhận ``MetricIn`` (từ ngày, phạm vi), trả ``{"value": số}`` — MỘT con số toàn công ty trong
        bộ lọc, không trả dòng nào (như đếm khối lượng việc). Kế hoạch có mục tiêu chọn chỉ số theo khoá; trợ lý chọn trong danh sách này."""
        key = f"{self.key}.{name}"
        if key in METRICS:
            raise ValueError(f"chỉ số {key} khai hai lần")

        def deco(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            METRICS[key] = Metric(key, label, unit, resource)
            return self.query(name, resource=None, title=label, internal=True, agent="deny", family="metric")(fn)
        return deco

    def search(self, name: str, label: str, *, resource: str, rank: int = 500, more: tuple[str, dict[str, Any]] | None = None
               ) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
        """TÌM NHANH ⌘K (0087): truy vấn nội bộ nhận ``SearchIn``, trả dòng ``{title, subtitle?, badge?, tone?, open: {query, input}}`` đã xếp
        hạng — khớp / xếp bằng ``SearchTerms`` (có dấu hay không, từng từ, đúng mã trước), ``LIMIT %(_n)s`` (8 + 1 để biết còn nữa).
        ``core.find`` gọi bằng QUYỀN NGƯỜI TÌM (``call_as``, RLS người đó) — thiếu quyền / gói tắt ⇒ bỏ nhóm, không lỗi. Lõi không biết module nào.
        ``more=(màn, đầu vào)``: màn danh sách CỦA MODULE NÀY nhận ô ``q`` lọc cùng cách — dòng thứ 9 thành "xem tất cả" mở màn đó (1.58)."""
        if more is not None and not more[0].startswith(self.key + "."):
            raise ValueError(f"nguồn tìm {self.key}.{name}: màn xem tất cả phải là màn của chính module ({more[0]})")
        key = f"{self.key}.{name}"
        if key in SEARCHES:
            raise ValueError(f"nguồn tìm {key} khai hai lần")

        def deco(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            SEARCHES[key] = Search(key, label, rank, more)
            return self.query(name, resource=resource, title=f"Tìm nhanh: {label.lower()}", internal=True, agent="allow", family="search")(fn)
        return deco

    def titles(self, *, resource: str) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
        """Nguồn CHỨC DANH HIỆN TẠI theo người (nhãn màn chọn vai): truy vấn nội bộ không đầu vào (``NoInput``), trả ``{user_id: chức danh}`` — chỉ
        người có chức danh đang hiệu lực HÔM NAY, chỉ chuỗi chức danh (không mã nhân viên, không đội, không ngày). Lõi chạy bằng quyền hệ thống của
        từng công ty khi gói đang bật (``workspaces.picker_titles``); chức danh chỉ là NHÃN — quyền vẫn theo vai. Mỗi module tối đa một nguồn."""
        name = f"{self.key}.current_titles"
        if name in TITLES:
            raise ValueError(f"module {self.key}: nguồn chức danh khai hai lần")

        def deco(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            TITLES.append(name)
            return self.query("current_titles", resource=resource, title="Chức danh hiện tại", internal=True, agent="deny", family="titles")(fn)
        return deco

    def trigger(self, event_type: str, label: str, fields: dict[str, str], *, open: tuple[str, dict[str, str]] | None = None,
                ids: tuple[str, ...] | None = None, owner: str = "") -> None:
        """Khai một sự kiện của CHÍNH module này làm CÒ tự động hoá (0090) — chỉ các ``fields`` khai ở đây mới dùng được trong điều kiện / lời báo.
        ``ids`` (ADR 0246, 1.69): khoá MÃ trong sự kiện được làm đầu vào cho lệnh của luật (mặc định: các khoá ``open`` trỏ tới); ``owner``: khoá mang
        người phụ trách bản ghi (luật gửi được cho "người phụ trách")."""
        if not event_type.startswith(self.key + "."):
            raise ValueError(f"module {self.key} chỉ khai cò cho sự kiện của chính nó: {event_type}")
        if event_type in TRIGGERS:
            raise ValueError(f"cò {event_type} khai hai lần")
        keys = tuple(ids) if ids is not None else tuple(k for k in dict.fromkeys((open or ("", {}))[1].values()) if k == "id" or k.endswith("_id"))
        if bad := [k for k in (*keys, *([owner] if owner else [])) if not (k == "id" or k.endswith("_id"))]:
            raise ValueError(f"cò {event_type}: ids / owner chỉ là khoá mã (*_id): {bad}")
        TRIGGERS[event_type] = Trigger(event_type, label, self.key, dict(fields), open, keys, owner)

    def date_source(self, name: str, label: str, *, date_label: str, fields: dict[str, str], resource: str, why: str,
                    ids: tuple[str, ...] = (), open: tuple[str, dict[str, str]] | None = None):
        """Khai một NGUỒN NGÀY cho luật tự động theo thời gian (ADR 0246, 1.69): "N ngày trước / sau <ngày> của bản ghi" ⇒ báo / tạo việc / đề xuất /
        chạy lệnh. Hàm được khai là truy vấn NỘI BỘ ``<module>.<name>`` (đầu vào ``DateIn``) chỉ động cơ luật (module ``flow``) gọi bằng quyền hệ thống
        — ``why=`` nêu bản ghi nào, trả trường nào (như mọi hợp đồng ``callers=``, 0083). Trả ``[{ref, on, owner_id, <fields>, <ids>}]`` của bản ghi CÒN
        MỞ (xong / huỷ thì không trả) có ``on`` trong khoảng. ``fields``: trường an toàn (không tên khách — lời báo có thể tới người không xem được
        bản ghi); ``ids``: khoá mã (``*_id``) làm đầu vào cho lệnh; ``open``: (truy vấn, {tham số: khoá}) để người nhận mở đúng thẻ."""
        full = f"{self.key}.{name}"
        if full in DATE_SOURCES:
            raise ValueError(f"nguồn ngày {full} khai hai lần")
        if bad := sorted(DATE_RESERVED & set(fields)):
            raise ValueError(f"nguồn ngày {full}: trường {bad} là khoá dành riêng (ref / on / owner_id / team_id)")
        if bad := [k for k in ids if not (k == "id" or k.endswith("_id"))]:
            raise ValueError(f"nguồn ngày {full}: ids chỉ là khoá mã (*_id): {bad}")
        DATE_SOURCES[full] = DateSource(full, label, self.key, date_label, dict(fields), tuple(ids), open)
        return self.query(name, resource=resource, title=f"Nguồn ngày: {label}", internal=True, agent="deny", callers=("flow",), why=why)

    def webhook(self, name: str, event_type: str, *, ids: tuple[str, ...], resource: str, label: str) -> None:
        """Công bố sự kiện ``event_type`` của module này cho app từ xa dưới tên ``name`` (0140). Thân webhook chỉ mang các khoá ``ids`` (mã), loại,
        lúc xảy ra — không tên, không SĐT, không số tiền."""
        if name in WEBHOOKS:
            raise ValueError(f"webhook {name} khai hai lần")
        if not re.fullmatch(r"[a-z]+\.[a-z_]+", name) or not event_type.startswith(self.key + "."):
            raise ValueError(f"webhook {name}: tên phải dạng <đối tượng>.<việc>, sự kiện phải của chính module {self.key}")
        if not ids or any(not i.endswith("_id") for i in ids):
            raise ValueError(f"webhook {name}: chỉ mang mã (khoá *_id)")
        if resource not in RESOURCES:
            raise ValueError(f"webhook {name}: tài nguyên {resource} chưa khai")
        WEBHOOKS[name] = Webhook(name, event_type, self.key, ids, resource, label)

    def inbound(self, op: str, *, verify: str = "") -> None:
        """KHÔNG DÙNG CHO GÓI (từ 1.65, ADR 0244): cửa webhook VÀO duy nhất là cửa của khung kết nối — gói nhận webhook bằng bộ nối
        (``Connector.webhook`` + ``hook_parse``, runbook 22); gói tự gọi hàm này sẽ không qua bước tự kiểm. Hàm còn lại cho chính khung kết nối của lõi.

        Module nhận webhook VÀO từ nhà cung cấp ngoài (0153) qua ``POST /v1/hooks/<module>/<công ty>/<mã kết nối>``. ``op``: tên thao tác NỘI BỘ
        của chính module (đầu vào ``{ref, headers, body_b64}``), chạy bằng quyền hệ thống của công ty trên đường dẫn — thao tác PHẢI tự kiểm chữ ký /
        khoá của kết nối (so sánh thời gian hằng) trước khi ghi gì, chống lặp theo mã sự kiện của nhà cung cấp, thiếu trường ⇒ từ chối có ghi log.
        ``verify`` (1.31): TRUY VẤN nội bộ của chính module cho ``GET`` cùng đường dẫn — nhà cung cấp kiểm địa chỉ trước khi đăng ký gửi thư (Meta:
        ``hub.mode`` / ``hub.verify_token`` / ``hub.challenge``). Đầu vào ``{ref, query}`` (tham số URL, đã cắt), trả ``{text}`` ⇒ route trả đúng chuỗi
        đó dạng chữ trơn; truy vấn PHẢI so khoá kiểm của kết nối bằng thời gian hằng, sai ⇒ ``Denied`` (route đếm lần sai như POST). Không khai ⇒ GET 404."""
        if self.key in INBOUND:
            raise ValueError(f"module {self.key} khai cửa webhook vào hai lần")
        if not op.startswith(self.key + ".") or (verify and not verify.startswith(self.key + ".")):
            raise ValueError(f"cửa webhook vào của {self.key} phải là thao tác của chính module")
        INBOUND[self.key] = op
        if verify:
            INBOUND_VERIFY[self.key] = verify

    def on_trigger(self) -> Callable[[Callable[..., Awaitable[None]]], Callable[..., Awaitable[None]]]:
        """Bên nghe MỌI cò tự động hoá đã khai (``Module.trigger``) — cho động cơ luật "Nếu… thì…"; cò của gói đang tắt không phát nên không tới."""
        def deco(fn: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
            name = f"{self.key}.{fn.__name__}"
            if name in SUBSCRIBERS:
                raise ValueError(f"bên nghe {name} khai hai lần")
            SUBSCRIBERS[name] = Subscriber(name, ANY_TRIGGER, fn, module=self.key, pack=MODULE_PACK[self.key])
            return fn
        return deco

    def suggests(self, query: str, *, rank: int = 500) -> None:
        """Nguồn gợi ý buổi sáng (0074 A) — truy vấn của CHÍNH module này trả ``[{command, input, reason, key, rank?}]`` cho người chạy nó."""
        if not query.startswith(self.key + "."):
            raise ValueError(f"module {self.key}: nguồn gợi ý phải là truy vấn của chính module, không phải {query}")
        attention.SOURCES.append(attention.Source(query, rank))

    def turn_context(self, query: str) -> None:
        """Nguồn NGỮ CẢNH của lượt trợ lý (0178) — truy vấn của CHÍNH module này trả ``{"context": str}`` (trống = không có gì); trợ lý ghép vào
        lời dặn mỗi lượt hỏi của người đó. Chỉ dòng ngắn, không SĐT / ghi âm / nội dung nhạy cảm — chi tiết trợ lý tự gọi công cụ lấy."""
        if not query.startswith(self.key + "."):
            raise ValueError(f"module {self.key}: nguồn ngữ cảnh phải là truy vấn của chính module, không phải {query}")
        attention.CONTEXTS.append(query)

    def workspace(self, ws: scr.Workspace) -> scr.Workspace:
        """Phân hệ (menu) do module này sở hữu (0069); module khác góp màn bằng ``screens``. ``workspaces.py`` chỉ ráp + lọc theo quyền."""
        if ws.key in scr.WORKSPACES:
            raise ValueError(f"phân hệ {ws.key} khai hai lần")
        ws = dataclasses.replace(ws, pack=MODULE_PACK[self.key])   # phân hệ thuộc gói của module sở hữu (lọc theo gói đã bật — manifest)
        scr.WORKSPACES[ws.key] = ws
        return ws

    def screens(self, into: str, items: tuple[scr.Screen, ...], *, rank: int = 10) -> None:
        """Góp màn vào phân hệ của module khác; ``rank`` xếp chỗ (màn của chủ phân hệ = 0, nhỏ lên trước)."""
        scr.CONTRIB.append(scr.Contribution(into, rank, len(scr.CONTRIB), items))

    def dataset(self, name: str, title: str, *, resource: str, base: LiteralString, dims: dict[str, Any], measures: dict[str, Any],
                dates: dict[str, str], description: str = "", drill: Any = None, coverage: LiteralString | None = None) -> None:
        """TẬP DỮ LIỆU có chiều / chỉ số (0100): khai một lần, lõi nhóm / cộng trên toàn bộ dòng bằng quyền người xem — đăng ký truy vấn
        ``<module>.ds_<tên>`` (trợ lý, app tự ghép, màn đều gọi như mọi truy vấn). ``dims`` / ``measures``: ``datasets.Dim`` / ``Measure`` / ``Ratio``.
        ``coverage``: SQL một dòng (``complete_through`` / ``reliable_from``) — độ phủ theo cột ngày đầu, trang ghi rõ khi kỳ xem vượt ra ngoài (1.42)."""
        from barista.kernel import datasets as dsk

        ds = dsk.Dataset(f"{self.key}.{name}", title, description, resource, base, dims, measures, dates, drill, coverage)
        if ds.key in dsk.DATASETS:
            raise ValueError(f"tập dữ liệu {ds.key} khai hai lần")
        dsk.DATASETS[ds.key] = ds

        async def handler(ctx: Any, inp: dsk.DatasetIn) -> dict[str, Any]:
            return await dsk.run(ctx, ds, inp)
        handler.__doc__ = (f"Tổng / đếm / cộng {title.lower()} nhóm theo từng chiều, theo tháng, theo kỳ — tính trên TOÀN BỘ dòng bạn được xem "
                           f"(không cắt), ô tổng bấm ra dòng gốc. {description} "
                           f"Chiều: {', '.join(f'{k} ({d.label})' for k, d in dims.items())}. "
                           f"Chỉ số: {', '.join(f'{k} ({m.label})' for k, m in measures.items())}. "
                           f"Ngày lọc kỳ: {', '.join(f'{k} ({v})' for k, v in dates.items()) or 'không có'}.")
        self.query(f"ds_{name}", resource=resource, title=f"Tổng hợp {title.lower()} theo chiều / tháng",
                   info=tuple(dict.fromkeys(str(n).partition(":")[0] for x in measures.values() if (n := getattr(x, "needs", None)))))(handler)

    def mode(self, key: str, rules: str, *, also: tuple[str, ...] = (), max_steps: int | None = None) -> None:
        """Một CHẾ ĐỘ trò chuyện riêng (0098 — "builder": trình tạo app): lời dặn của chế độ + công cụ thêm ngoài công cụ loại ``key``
        (vd mở app để xem thử). ``max_steps``: trần số bước một lượt của chế độ (việc dựng nhiều phần cần nhiều bước hơn một câu hỏi thường —
        trình tạo app 50, anh Anh chốt 15/11); trống ⇒ trần chung. Chạm trần ⇒ lượt báo "chạm trần", app vẽ nút Tiếp tục. Kernel / chat chỉ đọc
        registry, không biết module nào."""
        if key in MODES:
            raise ValueError(f"chế độ {key} khai hai lần")
        if max_steps is not None and not 1 <= max_steps <= 200:
            raise ValueError(f"chế độ {key}: trần bước 1–200")
        MODES[key] = (rules, also)
        if max_steps is not None:
            MODE_STEPS[key] = max_steps

    def personal_screens(self, into: str, query: str) -> None:
        """Màn riêng từng người (0098): ``query`` (của module này, chỉ đọc) trả danh sách màn ``[{key, title, icon, tint?, query, input}]`` của
        NGƯỜI XEM — góp vào phân hệ ``into``. Lỗi / không quyền ⇒ không góp gì (menu không vỡ)."""
        scr.PERSONAL.append(scr.PersonalSource(into, query))

    def on(self, event_type: str, *, needs: tuple[str, ...] = (), replaces: tuple[str, ...] = ()
           ) -> Callable[[Callable[..., Awaitable[None]]], Callable[..., Awaitable[None]]]:
        """``needs``: gói cần thêm để bên nghe chạy (tắt ⇒ sự kiện vẫn ghi, bên này không phản ứng). ``replaces``: tên bên nghe CŨ (cùng sự
        kiện, cùng payload) mà bên này thay — việc còn giao cho tên cũ chạy bằng bên này (đổi bản không mất phản ứng)."""
        def deco(fn: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
            name = f"{self.key}.{fn.__name__}"
            if name in SUBSCRIBERS:
                raise ValueError(f"bên nghe {name} khai hai lần")
            if not NAME.fullmatch(fn.__name__):
                raise ValueError(f"bên nghe {name}: tên hàm phải dạng chữ thường_gạch_dưới")
            SUBSCRIBERS[name] = Subscriber(name, event_type, fn, needs, module=self.key, pack=MODULE_PACK[self.key])
            for old in replaces:
                if old in REPLACED or old in SUBSCRIBERS:
                    raise ValueError(f"bên nghe {old} đã có / đã được thay")
                REPLACED[old] = name
            return fn

        return deco


# Module của GÓI NỀN (luôn bật, đi cùng lõi): danh sách đóng, nằm trong repo. Gói khác không tự nhận "base" (review 09/11).
BASE_MODULES = frozenset({"core", "admin", "apps", "asks", "connect", "connectors", "flow", "health", "kb", "memory", "papers", "work"})
KEY = re.compile(r"[a-z][a-z0-9_]*")      # khoá module / gói — không dấu chấm, không hoa, không rỗng
NAME = re.compile(r"[a-z_][a-z0-9_]*")     # phần tên của thao tác / bên nghe sau "<module>."


def get(name: str) -> Op | None:
    return OPS.get(name)


def subscribers_for(event_type: str) -> list[Subscriber]:
    return [s for s in SUBSCRIBERS.values() if s.event_type == event_type or (s.event_type == ANY_TRIGGER and event_type in TRIGGERS)]
