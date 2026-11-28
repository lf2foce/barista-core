"""BỀ MẶT CÔNG BỐ CHO GÓI (0130) — mọi thứ một gói cần để khai và chạy nghiệp vụ, không hơn. Gói (trong repo hay bản phân phối riêng) import
từ ĐÂY; ``barista.kernel.*`` là nội bộ lõi, đổi được bất kỳ lúc nào (``tests/test_pack_surface.py`` chặn gói import nội bộ, trừ danh sách miễn
có lý do).

Gồm: khai gói / module (``Module``, ``Pack``, ``Role``, ``Flow``, đầu vào của các họ thao tác), form (``Input``, ``field``, kiểu ô), ngữ cảnh
lệnh ``Ctx`` (mọi lời gọi được phép là phương thức của nó: ``ctx.call`` / ``call_optional`` / ``call_system`` / ``call_as`` / ``emit`` /
``plugs``…), lỗi, giờ (múi giờ công ty — 0169), màn do server mô tả (``ui``, ``Screen``, ``Workspace``…), mục cần chú ý, chữ / tiền, và vài dịch vụ của lõi
dưới dạng module con CHỈ mang phần gói dùng (``files``, ``entities``, ``links``, ``notify``, ``regulations``, ``business_calendar``,
``company_settings``, ``metrics``, ``ratelimit``, ``ledger.declare``, ``ai`` — một lượt model qua công tắc AI của công ty, 1.2; ``tenancy`` — công ty khách của
công ty dịch vụ, 1.8) và ``web`` — HTTP cho route module tự khai (``Module.route``, 1.1).

PHIÊN BẢN (semver, ``__version__``) — hợp đồng giữa lõi và gói:
* bỏ / đổi tên / đổi chữ ký một tên đã công bố ⇒ tăng số LỚN (gói khai ``requires-core`` cũ không nạp được nữa — phải sửa gói);
* thêm tên mới / thêm tham số có mặc định ⇒ tăng số GIỮA;
* sửa lỗi không đổi bề mặt ⇒ tăng số CUỐI.
Danh sách tên chốt ở ``tests/api_surface.json`` — đổi bề mặt mà không đổi phiên bản ở cả hai nơi thì test đỏ.
"""

from barista.api import (
    ai,
    brochure,
    business_calendar,
    company_settings,
    connectors,
    convert,
    docread,
    entities,
    extra_fields,
    files,
    findings,
    history,
    inbound,
    lead_labels,
    ledger,
    links,
    metrics,
    notify,
    onboard,
    ratelimit,
    record_files,
    regulations,
    tenancy,
    web,
)
from barista.kernel import ui
from barista.kernel.attention import Line, flagged, kpi, line, num
from barista.kernel.clock import VN, add_months, day_of, day_start, month_of, shown, today, zone, zone_label
from barista.kernel.datasets import Dim, DrillTo, Measure, Ratio
from barista.kernel.errors import Conflict, Denied, Invalid, NotFound, OpError
from barista.kernel.executor import Ctx as _Ctx
from barista.kernel.executor import NeedsConfirmation, can_run
from barista.kernel.executor import Prep as _Prep
from barista.kernel.extract import DocType, F
from barista.kernel.fields import (
    Day,
    Input,
    MetricIn,
    Money,
    NoInput,
    OptionalPhone,
    OptionsIn,
    Percent,
    Phone,
    field,
    parse_money,
    parse_phone,
    phone_display,
    phone_search,
)
from barista.kernel.principal import Principal as _Principal
from barista.kernel.registry import (
    CheckIn,
    CutoverIn,
    DateIn,
    Event,
    Flow,
    Hints,
    ImportKey,
    ImportTable,
    InfoGroup,
    Module,
    OnboardStep,
    Pack,
    Question,
    Role,
    SearchIn,
    SubjectRef,
    WorkIn,
)
from barista.kernel.screens import Action, Ask, Desk, Open, Screen, Workspace, menu_pages
from barista.kernel.search import SearchTerms
from barista.kernel.skill_specs import declare as skill
from barista.kernel.text import code_from_name, dong, fold, like, money, name_key, reflow, snake_from_name, vnd_text, vnd_words
from barista.kernel.workhours import Hours, add_working_minutes

# KIỂU người gọi chỉ để chú thích (``viewer: Principal``) — không dựng được (bí danh kiểu, không phải lớp): người gọi do lõi cấp (``ctx.p``,
# ``ctx.principal_of``); ``ctx.call_as`` từ chối người gọi tự dựng.
type Principal = _Principal
# NGỮ CẢNH lệnh: cũng chỉ là KIỂU — lõi dựng ``Ctx`` cho thao tác / bên nghe / route đang chạy; gói không dựng (giả module trong ``callers=``,
# giả quyền hệ thống) — 1.1.1, review 08/11. Lõi còn kiểm lúc dựng (chỉ ``executor.core_ctx``) và mỗi lần gọi lồng.
type Ctx = _Ctx
# BƯỚC CHUẨN BỊ của lệnh (``Module.command(prepare=…)``, 1.22 — ADR 0200): chỉ là KIỂU, lõi dựng; ``prep.query`` đọc bằng quyền người gọi, ``ai.run(prep, …)``
# gọi model NGOÀI giao dịch.
type Prep = _Prep

__version__ = "1.86.0"   # 1.4: Module.command(import_rows=…) (0151) · 1.5: business_calendar.holidays; Hours(off=…) ngày nghỉ trong đồng hồ giờ làm việc (0152)
#                         1.7: gộp ba nhánh cùng mang 1.6 — regulations.declare / compare / known_codes (0154) · Module.inbound (webhook VÀO từ nhà cung cấp) · Pack(records=…) cấu hình mặc định qua lệnh của module sở hữu (0153)
#                              · Module.consumes nhận thêm họ outreach / doc_read / paper_draft — dịch vụ lõi mở cho gói ngoài (0155)
#                         1.8: NeedsConfirmation — lệnh tự biết việc trợ lý định làm chạm lịch / dữ liệu của NGƯỜI KHÁC ⇒ cả lệnh thành thẻ đề xuất (0158)
#                         1.9: tenancy (công ty khách của công ty dịch vụ) · họ receivable / remittance / follow_up mở cho gói ngoài (doc_read thêm kb.mentions)
#                              · Role(suggest=, showcase=) câu gợi ý của vai do gói khai — ba gói rời lõi (0155)
#                         1.10: Pack(hints=) + Hints — gói góp câu gợi ý / câu xuyên module cho vai của gói KHÁC (lõi không viết cứng câu của ngành)
#                         1.11: Module.titles — gói cung cấp chức danh hiện tại theo người (nhãn màn chọn vai; lõi không đọc bảng của gói)
#                         1.12: zone / zone_label / day_start / day_of — MÚI GIỜ CÔNG TY (0169). ``VN`` còn giữ nhưng chỉ nghĩa là múi của LUẬT Việt Nam;
#                              ngày / giờ / hiển thị của công ty dùng ``zone()``
#                         1.13: inbound.verify_inbound — một cách kiểm chữ ký / khoá / giờ ký cho cửa webhook VÀO (0172) · metrics.run(at=…) mốc
#                              timestamptz lọc kỳ bằng khoảng trên cột (dùng được chỉ mục)
#                         1.14: Ctx.historical — lệnh / bên nghe đang chạy trong một lần nạp lịch sử từ hệ khác (0185); gói chỉ đọc, lõi đặt
#                         1.15: họ thao tác mở identity / consent / booking / work_log / call_log (gói khai m.consumes) · regulations.declare(root=…)
#                              bộ quy định đi cùng gói — hai gói nữa rời lõi (0155 đợt ba)
#                         1.16: họ thao tác mở settlement / placement / ledger_ref / structure / lead_conversion / company_figures; họ receivable / identity /
#                              follow_up / paper_draft nhận thêm thao tác — lõi thôi nêu tên module của gói ngành trong callers= (0186)
#                         1.17: ledger.declare(mechanism=…) — gói tự đánh dấu tài khoản cơ chế của mình (không vào danh mục khoản thu / chi), 0189
#                         1.18: lead_labels (SOURCES / CONTACT_KIND) — bộ mã của form thêm lead cho gói ngành có form chuyển dòng cho crm.create_lead; · 1.21: parse_phone (0197) nhận số quốc tế khi nói rõ (+ / 00) ⇒ dạng E.164, đổi đầu số di động cũ 11 chữ số sang mới, thêm số 0 đầu cho 9 chữ số di động;
#                              Bất động sản + Hoa hồng rời lõi (0191)
#                         1.19: Role(ai_tier=…) — hạng trợ lý AI mặc định của vai do gói khai (model, hạn mức theo người, giọng nói — ADR 0193)
#                         1.22: Module.command(prepare=…) + Prep — bước gọi mạng / model NGOÀI giao dịch trước handler; ai.run nhận Prep (ADR 0200)
#                         1.20: skill(…) — KỸ NĂNG TRỢ LÝ mặc định của gói (dòng cho ``Pack(records=…)``; nội dung kiểm lúc nạp, cài qua install_defaults — 0198)
#                         1.23: parse_phone trả E.164 cho MỌI số (số Việt Nam ``+84…``, không còn ``0…``) + phone_display (chữ theo quốc gia công ty) + phone_search
#                         1.26: Screen(row_click=…) — bấm MỘT lần vào dòng mở chi tiết ("open") / nút mặc định của dòng (12/11/2026)
#                              (chữ số để tìm, người gõ ``09…``) — 0199. Gói lưu SĐT phải đổi khoá đã lưu bằng migration của gói.
#                         1.24: plug ``crm.offering`` có thể trả ``lead_routing`` (bool) — tắt ⇒ leadflow không tự chia lead của [[offering|l]] đó, lead vẫn vào và
#                              nằm Lead chờ chia (lý do ``paused``); không trả ⇒ coi là bật.
#                         1.25: chia lead theo KẾ HOẠCH chiến dịch (0204, migration bảo trì crm/084): leadflow.set_rule / set_member nhận ``plan_code``;
#                              plug ``crm.campaign`` trả ``plan_code``. Gói diễn tập nâng cấp phải kê crm/084.
#                         1.27: họ company_figures nhận thêm ``mkt.ad_results`` — kết quả quảng cáo của một kỳ tuỳ ý (chiến dịch + quảng cáo, độ tin cậy, kết quả bán
#                              qua ``crm.lead_outcome``); ``mkt.export_campaigns`` thêm ``budget_vnd`` (gói kết nối Meta — ADR 0208)
#                         1.28: lead nhớ MẪU quảng cáo (0210, migration crm/085 — không bảo trì): plug ``crm.campaign`` nhận thêm ``ad_code`` (gói tự cài
#                              điểm cắm này phải thêm trường — Input cấm trường lạ) và trả thêm ``ad_id``; ``leadflow.intake`` / ``crm.create_lead`` nhận
#                              ``ad_code``; ``crm.leads.ad_id``. Gói diễn tập nâng cấp phải kê crm/085.
#                         1.29: ĐIỀU KHIỂN QUẢNG CÁO không phụ thuộc kênh ở lõi (0209, migration marketing/084 — không bảo trì): ``Prep.plugs`` (hỏi điểm cắm
#                              của chính module NGOÀI giao dịch của lệnh); điểm cắm ``mkt.ad_objects`` / ``mkt.ad_apply`` / ``mkt.ad_convert`` cho gói kênh
#                              quảng cáo; họ company_figures nhận ``mkt.fx_rates``; họ lead_conversion nhận ``crm.record_lead_outcome`` (gói ghi kết quả bán
#                              báo lead đã ký ⇒ sự kiện ``crm.lead_outcome_recorded``); ``mkt.ad_results`` thêm trường theo quảng cáo (lead / lead chất lượng /
#                              kết quả bán của lead biết mẫu) + tên / trạng thái / ``with_ad`` theo chiến dịch; sự kiện ``mkt.ad_changed`` sau mỗi lần đổi trên kênh.
#                         1.30: họ settlement — ``acct.recognize_payable`` nhận ``due_on`` tuỳ chọn (ngày đến hạn chi, chỉ để Dòng tiền dự phóng xếp
#                              tháng; không đổi số, không vào khoá chống lặp khi trống — 0211, migration finance/074 không bảo trì); dòng của
#                              ``<module>.cashflow_lines`` trả thêm ``note`` tuỳ chọn. Gói diễn tập nâng cấp phải kê finance/074.
#                         1.31: nhận form lead quảng cáo thẳng từ kênh (0216, 0208 §0.3.0 — không migration): ``Module.inbound(verify=)`` cửa GET kiểm địa chỉ
#                              webhook (Meta ``hub.challenge``, chữ trơn); ``prepare=`` cho lệnh nội bộ là VÒNG QUÉT (gọi nhà cung cấp ngoài giao dịch);
#                              ``Ctx.call_record_only`` (gọi lồng ở chế độ chỉ ghi nhận — 0185 / 0207); họ company_figures nhận ``leadflow.external_lead_days``.
#                         1.33: ``menu_pages(phân hệ)`` — trang khai tĩnh của một phân hệ theo thứ tự menu (chốt tháng của Báo cáo quản trị chụp từng trang — 0213,
#                              migration mgmt/001 không bảo trì). (1.32 do phiên khác giữ chỗ.)
#                         1.35: ``Module.command(consistent=True)`` — cả lệnh đọc MỘT ảnh dữ liệu (REPEATABLE READ, đặt ở câu đầu giao dịch); xung đột ghi
#                              (SerializationFailure) ⇒ ``conflict`` "thử lại". Cho lệnh đọc nhiều ghi ít bản ghi MỚI (chốt tháng 0213).
#                         1.36: gói hàng hoá (0217, migration finance/075 — không bảo trì): họ thao tác mở ``journal`` — ``acct.post_entry`` bút toán KHÔNG
#                              dùng tiền giữa các tài khoản cố định do CHÍNH gói gọi khai (tiền tố inventory / expense / revenue / payable; đảo = ``reverses_ref`` + ``reverses_source`` cùng module);
#                              ``acct.settle_line`` — MỘT dòng sao kê = nhiều khoản phải thu − khấu trừ của bên trả (phí, tiền đã hoàn khách) vào tài khoản chi phí /
#                              phải trả của gói của bảng kê, chạy bằng QUYỀN NGƯỜI BẤM (``ctx.call`` / ``call_as``), không mở cho quyền hệ thống. Gói diễn tập nâng
#                              cấp phải kê finance/075.
#                         1.37: ``Module.plug(prepare=)`` — phần cắm gọi nhà cung cấp NGOÀI mọi giao dịch (không giữ kết nối) khi module chủ hỏi qua
#                              ``Prep.plugs``; vòng gửi quảng cáo ``mkt.dispatch_ad_changes`` / ``send_ad_conversions`` gọi kênh ở bước chuẩn bị (0209).
#                         1.38: ngân sách so với thực tế (0219 — không migration): họ company_figures nhận ``acct.ledger_month_totals`` (phát sinh SỔ CÁI theo
#                              pháp nhân × tháng ngày hạch toán × tài khoản doanh thu / chi phí × đối tượng chi phí — phần chi phí của phiếu chia theo dòng phiếu, còn
#                              lại là đối tượng trống, cộng lại = sổ cái); họ ledger_ref nhận ``acct.pnl_account`` (mã tài khoản / mục khoản thu chi ⇒ tài khoản) và
#                              ``acct.cost_object`` (một đối tượng tập hợp chi phí). (1.38 do phiên khác giữ chỗ.)
#                         1.40: ``Ratio(nhãn, tử, mẫu, fmt)`` — chỉ số TỶ LỆ của tập dữ liệu (0220): tổng tử ÷ tổng mẫu ở mỗi nhóm và ô tổng, mẫu 0 ⇒ trống.
#                         1.42: ``Module.dataset(coverage=)`` — độ phủ của tập (``complete_through`` / ``reliable_from``); trang tập dữ liệu + mọi khối
#                              dashboard thêm trường ``source`` (nguồn, phạm vi, số đủ tới ngày, cảnh báo — 0220). (1.41 bỏ.)
#                         1.43: bộ công cụ trợ lý theo vai (0218, migration base/088 — không bảo trì): ``Role(tools=…)`` — nhóm (khoá module) / thao tác
#                              trợ lý của vai mang sẵn mỗi lượt; ``Pack(tools={vai: …})`` — gói góp nhóm / thao tác CỦA MÌNH vào bộ của vai gói khác.
#                              Cài qua ``admin.install_defaults`` (chỉ thêm), công ty sửa trong app. Không cấp quyền.
#                         1.44: ``business_calendar.non_working(lịch, từ, đến)`` — ngày nghỉ (cuối tuần / lễ) để tô bóng dòng thời gian kế hoạch (0222; khối ``timeline`` +
#                              ``ui.views(timeline=)`` ở kernel.ui, không phải bề mặt gói). Migration base/089 — thêm cột, không bảo trì.
#                         1.45: ``brochure`` — công thức tờ giới thiệu soạn từ dữ liệu cho điểm cắm ``compose.brochure`` của Soạn với AI (0224); ``web.public_url`` — địa chỉ công khai của core cho link gửi khách; không migration bảo trì
#                         1.46: ``ImportKey`` + ``Module.command(import_key=)`` — khoá tự nhiên của lệnh nhập Excel: màn nhập tự khớp cột (0226) báo dòng "thêm mới / cập nhật / trùng"; migration base/091 (core.import_mappings — ánh xạ cột nhớ theo công ty), không bảo trì
#                         1.47: ``convert`` (``to_pdf`` / ``Unavailable``) — đổi file văn phòng sang PDF để xem, cho gói ngoài nền (gói Văn bản Word — 0227).
#                         1.48: ``ImportTable`` + ``Module.command(import_table=)`` — lệnh nhập Excel nhận CẢ BẢNG làm một lần gọi (nhập kế hoạch vào Gantt — 0222); migration base/092
#                         1.49: ``field(kind="time" | "month")`` — ô giờ "HH:MM" / tháng "YYYY-MM": app vẽ ô chọn giờ / tháng (không gõ chữ); giá trị vẫn là chuỗi, không migration. App chưa biết kiểu ⇒ ô chữ
#                         1.50: trường tuỳ biến theo công ty (0060 lát 1) — ``Module.extensible(đối tượng, nhãn)`` + ``Module.command(extra=đối tượng)`` (input có ô
#                               ``extra: dict``; lõi gộp ô phẳng ``x_<khoá>`` và kiểm kiểu ở executor), ``extra_fields.shown`` (chữ hiện trên thẻ).
#                         1.51: ``findings`` — gói / module góp phát hiện cho Báo cáo chạy song song (0230): ``finding`` / ``unmeasured`` (chưa đo được ⇒ không số, không 0) / ``metric`` /
#                              ``impact_money`` / ``impact_count`` + chữ số kiểu Việt; truy vấn nội bộ ``<module>.findings_report``. Chỉ đọc, không migration.
#                         1.52: ``docread`` — đọc tài liệu / OCR (0232): ``read`` → ``Doc`` (chữ, bảng, ảnh, phương pháp từng trang, số đo, thông báo), ``read_images``, ``extract_images``, ``read_free``.
#                              Model, trần trang / token, công tắc là hồ sơ của công ty do nhà vận hành đặt — gói chỉ nêu mục đích.
#                         1.53: ``Pack(kind="company")`` — gói RIÊNG của một công ty (ADR 0233 D): không là mặc định của bản cài, không lên chợ / danh mục gói,
#                               công ty không được cấp quyền thì không thấy / không bật được; còn lại như mọi gói (chỉ thêm, chỉ dùng ``barista.api``).
#                         1.54: họ ``receivable`` (chỉ thêm, không migration): ``acct.receipt_facts`` nhận ``before`` (đã thu tính tới trước một ngày — mốc
#                               cắt kỳ chi) và trả thêm ``category`` / ``last_received_on``; ``acct.receivables_for`` trả thêm ``category`` (mục khoản thu).
#                         1.55: ``ui.b_later(query, input, title=)`` — khối TẢI SAU: thẻ về trước với phần nhanh, app đỡ được tự tải phần nặng theo thứ tự khối;
#                              bên gọi khác nhận thẻ đã ghép đủ (executor mở khối). Chỉ thêm, không migration.
#                         1.56: nạp lịch sử ghi đúng ngày (0185): họ ``settlement`` — ``acct.recognize_recovery`` / ``release_recovery`` / ``void_payables``
#                              nhận ``on`` (ngày hạch toán, không sau hôm nay); họ ``receivable`` — ``acct.void_receivables`` nhận ``on`` (ngày huỷ),
#                              ``acct.receipt_facts`` trả thêm ``last_reversed_on`` (ngày đảo phiếu thu gần nhất). Chỉ thêm, không migration.
#                         1.57: ``InfoGroup`` + ``Pack(info_groups=…)`` — gói khai NHÓM THÔNG TIN NHẠY CẢM (nhãn, tài nguyên quyền, nơi quyền được thực thi,
#                               phạm vi hỗ trợ) cho màn "Ai thấy thông tin gì" (ADR 0239 phần 3); ``Question`` + ``Pack(questions=…)`` — câu hỏi cấu hình khi đưa
#                               công ty vào dùng (câu nghiệp vụ, lựa chọn, mặc định + lý do, ai quyết, lệnh ghi) cho người và agent cấu hình cùng đọc. Màn đọc / ghi ``core.grants`` — không bảng quyền thứ hai.
#                               Gói khai nhóm cần lõi ≥ 1.57 (lõi cũ không nhận tham số) ⇒ ``requires-core`` của gói nâng theo.
#                         1.58: ``SearchTerms`` — khớp có dấu / không dấu theo từng từ + xếp hạng (đúng mã → đầu chuỗi → đầu từ → chứa) cho nguồn tìm ⌘K
#                              và ô lọc danh sách; ``Module.search(more=)`` dòng "xem tất cả" mở màn danh sách đã lọc (0087, base/097 ``core.vn_fold``)
#                         1.59: QUYỀN THEO KHỐI (lớp 3, ADR 0239 bổ sung): ``ui.col / kpi / field / section / line / b_*(…, needs=)`` + ``ui.gated`` / ``ui.need`` —
#                               khối / ô số / cột / chuỗi / mục khai nhóm thông tin (khoá ``InfoGroup`` hoặc tài nguyên, ``:phạm vi`` tuỳ chọn); cửa ra của executor
#                               bỏ phần người xem thiếu quyền + giá trị cột trong từng dòng (web, app, trợ lý, Excel, bản chốt). ``Ctx.sees(needs)`` (khỏi tính phần
#                               sẽ bị bỏ). ``Module.query(info=…)``: nhóm thông tin truy vấn có thể chứa — màn "Ai thấy thông tin gì" liệt kê màn theo đó.
#                               Gói dùng ``needs=`` cần lõi ≥ 1.59 (lõi cũ báo lỗi tham số lạ lúc nạp, không lặng lẽ hiện hết) ⇒ ``requires-core`` nâng theo.
#                         1.60: ``ui.part(khoá, hàm, title=)`` — phần tải sau trong CÙNG truy vấn (web tải từng phần theo thứ tự khối, ``?part=``); trang phía server
#                              ``ui.paging()`` / ``ui.table(paged_total=)`` / ``ui.slice_page`` (``?limit=&offset=``, ``paging`` {offset, limit, total, complete}). Chỉ thêm.
#                         1.61: LỚP VẼ FORM (agent form D): ``field(options_args={tham_số: trường})`` — ô chọn phụ thuộc ô khác (tải lại + xoá khi ô kia đổi); truy vấn chọn nhận
#                              ``q`` / ``limit`` (``OptionsIn``) và trả thêm ``hint`` (``ui.options(hint=)``) cho ô chọn có tìm. Chỉ thêm — truy vấn chọn cũ chạy như trước.
#                         1.62: ``code_from_name`` / ``snake_from_name`` — mã máy đặt từ TÊN (0225: form không bắt gõ mã / khoá; rà form nhóm A 18/11/2026) — chỉ thêm
#                         1.63: ``Screen(feature=<khoá Module.setting>)`` — NGHIỆP VỤ CÔNG TY CÓ DÙNG: công ty tắt công tắc ⇒ màn không lên menu / bàn việc của vai nào
#                         1.64: họ mở ``sale_source`` — gói ngành báo một lần bán cho gói năng lực trả theo doanh số (hoa hồng) mà hai gói không nêu tên nhau — chỉ thêm
#                              (19/11/2026; lõi ``acct`` / ``mkt`` + gói ngành dùng). Chỉ thêm — màn không khai ``feature`` như trước.
#                         1.65: ``barista.api.connectors`` + ``Module.connector`` — KHUNG KẾT NỐI CHUNG (ADR 0244): bộ nối (``Connector``: check / pull / webhook), lệnh nhận
#                              từng mục, lưu / mở kết nối (họ mở ``connector``); lõi giữ kết nối, khoá mã hoá, lịch kéo, cửa webhook, màn "Kết nối ngoài". Chỉ thêm.
#                         1.66: webhook CHUNG theo bộ nối — ``Connector.shared_hook`` + ``claims`` / ``shared_check`` / ``shared_verify`` / ``route_keys``, ``Batch.claimed``; cửa ``/v1/hooks/shared/<bộ nối>`` chỉ đánh thức kết nối sở hữu khoá đã xác minh (Meta: một webhook / app) — chỉ thêm
#                         1.67: ``web.app_url(path)`` — địa chỉ web của bản cài; trang web chung ``/oauth/<module>`` (nhà cung cấp OAuth trả về ⇒ gọi ``<module>.finish_<…>`` bằng quyền người đang đăng nhập — gói không cần đường công khai) — chỉ thêm
#                         1.68: họ mở ``ledger_read`` (0245) — ``acct.ledger_lines`` (dòng bút toán theo kỳ, trang đủ bút toán) và ``acct.ledger_balances`` (số dư tại một ngày): gói kế toán đọc sổ cái chi tiết không qua màn xuất MISA — chỉ thêm, không migration
#                         1.69 (ADR 0246 — chung số với phiên tự động hoá; phần LỊCH SỬ THAY ĐỔI): ``Module.tracks(loại, trường, nhãn, fmt=, choices=, needs=, empty=)``
#                              khai trường có lịch sử của đối tượng CỦA CHÍNH module; ``Ctx.track(loại, id, trường, cũ, mới)`` ghi trong cùng giao dịch (nạp lịch sử ⇒ im);
#                              ``history.block(ctx, loại, id)`` khối "Lịch sử thay đổi" cho thẻ; truy vấn ``core.history`` (trợ lý). Migration base/102 — bảng mới, không bảo trì.
#                         1.69: (ADR 0246, luật tự động) ``Module.date_source`` + ``DateIn`` — nguồn ngày cho luật theo thời gian ("N ngày trước / sau <ngày> của bản ghi") · ``Module.trigger(ids=, owner=)`` khoá mã làm đầu vào lệnh + người phụ trách bản ghi · ``Module.command(auto=True)`` lệnh luật tự động được chạy — chỉ thêm
#                         1.70: (bổ sung ADR 0245) họ ``ledger_read`` trả ``snapshot = {token, journals, mapping}`` và nhận ``snapshot=`` — sổ hoặc bảng Ánh xạ tài khoản đổi giữa
#                              các lần đọc của một báo cáo (token chung cho dòng + số dư cùng pháp nhân) ⇒ Conflict (gói không ghép hai trạng thái); gói lưu snapshot cùng báo cáo đã duyệt để biết ánh xạ đổi sau duyệt.
#                         1.71: ``Module.query(creates="<module>.<lệnh>")`` — truy vấn chọn khai lệnh tạo thêm một mục; form gửi ``options_create`` cho người có quyền lệnh đó, ô chọn rỗng hiện "Tạo mới" — chỉ thêm, không migration
#                         1.72 (ADR 0249 lát 1 — chung số với phiên lát 2; phần TỆP CỦA HỒ SƠ): ``Module.record_files(loại, labels=)`` khai đối tượng CỦA CHÍNH module nhận
#                              tệp gắn vào; ``record_files.block(ctx, loại, id)`` khối "Tệp" cho thẻ; lệnh ``core.attach_file`` / ``core.remove_file``, truy vấn ``core.files_of``.
#                              ``files.grant`` (đổi tên từ ``grant_copy``, chưa công bố) + ``files.adopt`` — một tệp nhiều chỗ dùng, không chép; ``files.copy`` (bản độc lập);
#                              ``files.sniff`` / ``clean_name`` / ``ACCEPTED`` / ``MEDIA`` / ``kind_label`` / ``size_text`` — loại tệp nhận được một nơi quyết. Migration base/105, 106 — chỉ thêm, không bảo trì.
#                         1.72: (ADR 0250) ``Module.command(intent=(ô, …))`` — form theo ý định: form hỏi trước các ô này + "Mô tả / thả file", "Soạn giúp" (``core.draft_input``) soạn phần còn lại thành chip máy đoán — chỉ thêm, không migration (1.72 chung với ADR 0249)
#                         1.72 (chung với lát 1 của ADR 0249 — phần lát 2, ấn phẩm gắn hồ sơ): ``brochure.kind(…, subject=, piece=, customer=)`` — công thức nói về
#                              MỘT hồ sơ (loại ``<module>.<loại>``), làm ra loại ấn phẩm nào, có nhận khách không; điểm cắm ``compose.brochure`` thêm câu hỏi
#                              ``op="subjects"`` ⇒ ``{"subjects": [{value, label}]}`` (hồ sơ người soạn chọn được); ``brochure.recipe(…, subject_label=)``, ``template``
#                              trống = mẫu mặc định của loại. Bỏ hai bộ ``brochure.SLOTS`` / ``TEMPLATES`` (không xuất ra bề mặt, đã lệch — danh sách ở phân hệ Ấn phẩm).
#                              Migration base/107 (bảng ``admin.brand``) + compose/073 — chỉ thêm, không bảo trì.
#                         1.73: ``files.signed_url`` cho gói (link ký ≤ 60 giây, ảnh / PDF xem tại chỗ) + hai khối nền ``ui.b_image`` / ``ui.b_split`` (gói tự ghép bố cục: ảnh nguồn bên trái, dòng đọc bên phải…) — chỉ thêm, không migration
#                         1.74: SỐ TÓM TẮT cho màn tổng hợp của gói (đọc bằng quyền người xem — ``ctx.call_as_optional(…, ctx.p)``): ``health.summary``, ``leadflow.summary``, ``acct.cashflow_projection``, ``core.approvals_summary`` — cùng con số màn của module đó hiện, không dựng màn; họ ``receivable`` nhận ``acct.receivable_totals`` (tổng phải thu / đã thu theo mã gốc của nguồn gọi — ô tổng của màn danh sách trên cả tập). Chỉ thêm, không migration
#                         1.75 (ADR 0251, CHỈ THÊM): ``Module.command(basis_required=…)`` — người bấm phải nêu căn cứ đã thấy; ``ui.act(basis=…)`` nút trang mang căn cứ (app gửi lại ở ``X-Expect-Basis``); ``ui.field(open=, action=, placeholder=)`` ô trang chi tiết bấm được; ``papers.field_catalog`` nhận ``authority`` (tài nguyên thẩm quyền phát hành chứng từ của gói)
#                         1.77 (ADR 0253, CHỈ THÊM): ``Module.command(subject=(loại, ô), purpose=, effect=, irreversible=, reversible=)`` — mô tả cho NGƯỜI DUYỆT: hồ sơ nào · vì sao · hệ quả · đảo được không; lõi dựng BẢN CHẠY THỬ CHUẨN (hồ sơ · cũ → mới từ ``ctx.track`` · vì sao · đảo được) cho thẻ đề xuất, thẻ buổi sáng, phiếu duyệt, thông báo; form_spec thêm ``confirm`` / ``effect``. Migration base/109 (cột ``preview``) — chỉ thêm, không bảo trì.
#                         1.78 (ADR 0254, CHỈ THÊM): ``OpError`` lên ``barista.api`` (gói gọi ``ctx.call`` bắt chung một lớp lỗi nghiệp vụ) + họ mở ``brand`` (đọc nhận diện công ty ``admin.brand``
#                              không cần lõi khai ``callers=`` tên gói) — đi cùng việc tách Ấn phẩm ra gói ngoài. Không migration
#                         1.79 (CHỈ THÊM, rà 22/11): CỬA SOẠN CHUNG ``papers.studio`` (màn "Soạn" của phân hệ Văn bản & tài liệu) + điểm cắm ``papers.authoring``: gói khai
#                              ``@m.plug("papers.authoring", nhãn)``, đầu vào ``NoInput``, trả DANH SÁCH ô ``{title, subtitle?, module?, open: {query, input}}``; lõi bỏ (ghi log) kết quả sai
#                              hình và ẩn ô người mở không chạy được ``open.query``. Khối ``composer`` (web vẽ): mỗi mục ``kinds`` thêm ``paper`` ("deck" · "page") + ``example``,
#                              khối thêm ``default_kind`` — web không viết cứng khoá loại ấn phẩm; ``stream`` là đường luồng bắt buộc (vắng / sai ⇒ web báo lỗi).
#                              ``Module.command(prepare_writes=False)``: bước ``prepare`` CHỈ ĐỌC — thẻ đề xuất không chiếm ``running`` trước khi gọi, lỗi mạng để thẻ chờ
#                              (mặc định True: lỗi lưng chừng ⇒ cần đối soát, ADR 0200 bổ sung 22/11). Không migration
#                         1.80 (CHỈ THÊM, ADR 0251 lát 2): mẫu giấy tờ có ĐOẠN TỰ DO ``{{doan:khoá|gợi ý}}`` (một dòng riêng; agent / người lập viết qua
#                              ``papers.write_paragraph``, chưa viết thì không in) — gói khai mẫu gợi ý dùng được; ``Screen(full_frame=True)``: màn tự vẽ trọn khung làm việc
#                              trong phân hệ nhiều màn (màn đầu ⇒ vào phân hệ là vào thẳng). Migration word/002 (cột chat) — chỉ thêm, không bảo trì
#                         1.81 (CHỈ THÊM): ``Screen(needs_plug="<module>.<điểm>")`` — màn chỉ lên menu khi một gói ĐANG BẬT cắm vào điểm đó (Soạn thảo cần
#                              trình soạn văn bản mới ``papers.studio_doc`` — gói Word); gói tắt ⇒ màn ẩn, phân hệ mở như thường. Không migration
#                         1.82 (CHỈ THÊM, ADR 0255 lát 1): ``Pack(rail={vai: (phân hệ…)})`` — phân hệ hiện sẵn trên thanh bên trái theo vai (gói khai cho vai của mình
#                              hoặc của gói khác, như ``pins``; tối đa 6; công ty đổi được, người ghim thêm được). Manifest thêm ``rail`` + ``workspaces[].pinned``;
#                              ``core.pin`` / ``core.unpin`` nhận khoá trống = ghim cả PHÂN HỆ (migration base/111 nới CHECK, không bảo trì)
#                         1.83 (ADR 0256 — bề mặt Python không đổi; số tăng để gói khai được "cần lõi có cơ cấu đội một nguồn"): đội và trưởng đội theo ngày
#                              ghi ở lõi — thao tác ``admin.set_team_lead`` / ``admin.set_team_since``; ``hr.set_team_head`` bỏ, ``hr.set_assignment`` chỉ còn
#                              người quản lý trực tiếp (không ``team_code``). Dữ liệu mẫu / test của gói ghi cơ cấu qua hai thao tác mới
#                         1.84 (ADR 0258 — bề mặt Python không đổi): thủ tục DB ``core.tenant_fks(schemas)`` — migration của gói gọi cho schema của mình để
#                              khoá ngoại giữa hai bảng cùng mang ``tenant_id`` mang cả ``tenant_id``; ``tests/test_meta.py`` chặn khoá ngoại thiếu
#                         1.85 (ADR 0262): ``docread.register`` / ``ExternalReader`` / ``Reader`` / ``Request`` — GÓI khai bộ đọc (OCR trên máy, dịch vụ tài liệu);
#                              công ty chọn bộ đọc bằng hồ sơ đọc theo mục đích (chuỗi), không đổi gói nghiệp vụ. Tên trùng bộ đã có ⇒ từ chối
#                              + ``Module.command(upload="pdf,xml,jpg")``: lệnh NHẬN TỆP — nút chọn / thả nhiều tệp, mỗi tệp một lượt ``POST /v1/uploads/<lệnh>``
#                              (nội dung ở ``ctx.files["file"]``, đầu vào có filename / size / sha256); thay route tệp riêng từng module
#                         1.86 (CHỈ THÊM, ADR 0263): ĐƯA CÔNG TY VÀO DÙNG — ``onboard`` (hợp đồng bộ chuyển dữ liệu hệ cũ: ``Entity`` / ``Metric`` / ``Converter`` /
#                              ``Problem`` + bộ chạy ``load`` — lệnh qua executor ``historical=True``, sổ mã gốc + dấu vân tay, đối chiếu có ngưỡng);
#                              ``Module.cutover(step, label, order=, resource=, why=)`` + ``CutoverIn`` (bước ngày chuyển gói tự khai — ``onboard.go_live`` chạy theo thứ tự);
#                              ``Pack(onboard_steps=…)`` + ``OnboardStep`` (việc của gói trong danh sách 7 chặng). Migration base/116 (schema onboard) — chỉ thêm
#                              + ``Module.config(key, label, read=, write=, order=, match=, mutable=)`` (ADR 0263 C — tài nguyên cấu hình gói tự khai cho gói cấu hình
#                              công ty ``admin.export_config`` / ``admin.import_config``: truy vấn đọc trả mục ở dạng đầu vào của lệnh ghi sẵn có). Migration
#                              base/117 (bảng ``admin.config_bundles`` giữ tệp đã tải lên + luật duyệt ``admin.import_config``) — chỉ thêm

__all__ = [
    "VN",
    "Action",
    "Ask",
    "CheckIn",
    "Conflict",
    "Ctx",
    "CutoverIn",
    "DateIn",
    "Day",
    "Denied",
    "Desk",
    "Dim",
    "DocType",
    "DrillTo",
    "Event",
    "F",
    "Flow",
    "Hints",
    "Hours",
    "ImportKey",
    "ImportTable",
    "InfoGroup",
    "Input",
    "Invalid",
    "Line",
    "Measure",
    "MetricIn",
    "Module",
    "Money",
    "NeedsConfirmation",
    "NoInput",
    "NotFound",
    "OnboardStep",
    "OpError",
    "Open",
    "OptionalPhone",
    "OptionsIn",
    "Pack",
    "Percent",
    "Phone",
    "Prep",
    "Principal",
    "Question",
    "Ratio",
    "Role",
    "Screen",
    "SearchIn",
    "SearchTerms",
    "SubjectRef",
    "WorkIn",
    "Workspace",
    "__version__",
    "add_months",
    "add_working_minutes",
    "ai",
    "brochure",
    "business_calendar",
    "can_run",
    "code_from_name",
    "company_settings",
    "connectors",
    "convert",
    "day_of",
    "day_start",
    "docread",
    "dong",
    "entities",
    "extra_fields",
    "field",
    "files",
    "findings",
    "flagged",
    "fold",
    "history",
    "inbound",
    "kpi",
    "lead_labels",
    "ledger",
    "like",
    "line",
    "links",
    "menu_pages",
    "metrics",
    "money",
    "month_of",
    "name_key",
    "notify",
    "num",
    "onboard",
    "parse_money",
    "parse_phone",
    "phone_display",
    "phone_search",
    "ratelimit",
    "record_files",
    "reflow",
    "regulations",
    "shown",
    "skill",
    "snake_from_name",
    "tenancy",
    "today",
    "ui",
    "vnd_text",
    "vnd_words",
    "web",
    "zone",
    "zone_label",
]
