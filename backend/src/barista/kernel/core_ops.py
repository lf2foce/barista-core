"""Thao tác của lõi dùng chung mọi phân hệ: hộp duyệt + quyết định duyệt, hộp thông báo + thiết bị nhận đẩy."""

import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.db import set_session
from barista.kernel import auth, company_settings, operator_door, registry, screens, search, skill_specs, ui
from barista.kernel.approvals import resolve_step
from barista.kernel.clock import zone, zone_label
from barista.kernel.datasets import Dim, Measure
from barista.kernel.errors import Denied, Invalid, NotFound
from barista.kernel.executor import Ctx, basis_changed, basis_of, can_run, invoke, jsonable, map_error, stale_message
from barista.kernel.fields import Input, NoInput, field
from barista.kernel.principal import issued, system_principal
from barista.kernel.registry import InfoGroup, Module, Pack, Question, Role

Pack("base", "Nền",
     "Lõi, quyền, duyệt, thông báo, trợ lý, quản trị, quy trình & việc, tài liệu, giấy tờ — luôn có",
     roles={
         # bộ công cụ trợ lý mặc định (0218): nhóm / công cụ của gói nền; gói khác góp phần của mình (Pack.tools)
         "admin": Role("Quản trị hệ thống", ai_tier="manager", tools=("admin", "health", "core.ai_usage"),
                       home=("admin", ("users", "permissions", "deliveries", "audit")),
                       picker=("Quản lý", "Quản trị hệ thống: người dùng, quyền, kết nối, nhật ký — không tự duyệt tiền của chính mình.", "Ai đang có quyền gì?"),
                       grants=(("core.ai_usage", "all", "read"), ("health", "all", "read"),
                               ("core.admin", "all", "read write manage"), ("core.audit", "all", "read"),
                               ("core.ai_skills", "all", "read write manage"),   # kỹ năng trợ lý (0198): soạn + duyệt (người duyệt khác người soạn)
                               ("core.ai_tools", "all", "read write"),   # bộ công cụ trợ lý theo vai (0218) — không cấp quyền
                               ("core.approvals", "all", "read"), ("kb.common", "all", "read write manage"),
                               ("papers", "all", "read write manage"), ("papers.templates", "all", "read write manage"), ("flow", "all", "read write manage"),
                               ("flow.templates", "all", "read write manage"), ("flow.automations", "all", "read write manage"),
                               ("asks", "all", "read write"), ("connectors", "all", "read write manage"),
                               ("work", "all", "read write"),),
                       suggest=(("admin.deliveries", "Có sự kiện nào đang lỗi cần xử lý không?"),
                                ("admin.permissions", "Vai trò nào đang xem được số liệu của cả công ty?", "core.admin"),
                                ("admin.users", "Ai đang bị khoá tài khoản?"),
                                ("admin.audit", "Hôm nay có ai bị chặn quyền khi thao tác không?")),
                       showcase=(("Dữ liệu nào đang làm số báo cáo lệch, và việc nào đang kẹt chờ người?", ("health.overview", "flow.needs_action")),)),
         # công ty chưa có chủ tài khoản ⇒ giám đốc nhận trách nhiệm đó khi cài mặc định (0029); không có giám đốc ⇒ mọi quản trị
         "director": Role("Giám đốc", ai_tier="executive", account_owner=True,
                          tools=("health", "flow.plans", "flow.plan", "flow.propose_plan", "flow.assignees", "flow.resolve_assignment", "flow.create_task",
                                 "flow.set_task_timing", "flow.remind_plan"),   # kế hoạch & giao việc bằng lời (0222)
                          picker=("Ban giám đốc", "Thấy toàn công ty: số liệu kinh doanh, dòng tiền, việc của các đội.", "Tuần này công ty có gì cần tôi chú ý?"),
                          grants=(("core.ai_usage", "all", "read"), ("health", "all", "read"),
                                  ("core.audit", "all", "read"), ("core.approvals", "all", "read"),
                                  ("core.ai_skills", "all", "read write manage"),   # kỹ năng trợ lý = cách làm nghiệp vụ của công ty (0198)
                                  ("core.ai_tools", "all", "read write"),   # trợ lý của vai mang sẵn nhóm công cụ nào (0218)
                                  ("ledger", "all", "read"), ("kb.common", "all", "read write manage"), ("memory.legacy", "all", "read write"),
                                  ("papers", "all", "read write manage"), ("papers.templates", "all", "read write manage"), ("flow", "all", "read write manage"),
                                  ("flow.templates", "all", "read write manage"), ("flow.automations", "all", "read write manage"),
                               ("asks", "all", "read write"), ("connectors", "all", "read"),
                                  ("work", "all", "read"),),
                          suggest=(("core.approvals", "Có phiếu nào đang chờ tôi duyệt không?"),)),
         # Vai đặt theo VIỆC, không theo phòng (0073): phòng là đội — một phòng gộp chứa nhiều vai; trưởng phòng = is_lead (phạm vi "team").
         "office": Role("Hành chính", ai_tier="basic", tools=(*("flow.tasks", "flow.task", "flow.plans", "flow.plan", "flow.requests", "flow.adjust_task", "flow.set_task", "flow.request_team"), "papers.list", "papers.draft_letter"),
                        home=(None, ("plans", "flow_board")),
                        picker=("Văn phòng", "Hành chính văn phòng: tài sản, văn phòng phẩm, hợp đồng dịch vụ, giấy tờ công ty — không dữ liệu bán hàng.",
                                "Tuần này tôi còn việc gì chưa xong?"),
                        grants=(("kb.common", "all", "read write"), ("papers", "team", "read write"),
                                ("flow", "team", "read write"), ("flow.templates", "all", "read"), ("asks", "all", "read write"),)),
         # CÔNG NGHỆ (rà vai 18/11/2026, anh Anh chốt "tuỳ em chọn best practice"): người giữ hệ thống — người dùng, đội, vai, quyền, nhật ký, sức
         # khoẻ dữ liệu, nhập liệu, kết nối, quy trình, cấu trúc danh mục — KHÔNG khách / SĐT / giao dịch / phí / hoa hồng / kế toán / lương. Khác
         # "Quản trị hệ thống" (vai của chủ / giám đốc vận hành, gói nghiệp vụ còn góp quyền xem số). Mọi đổi quyền vẫn qua chủ tài khoản duyệt
         # (chuỗi access_change) ⇒ vai này không tự nâng mình. Gói khác góp quyền cấu trúc của mình bằng Pack.grants (vd cấu trúc dự án / căn).
         "tech": Role("Công nghệ", ai_tier="manager", tools=("admin", "health", "core.ai_usage"),
                      home=("admin", ("users", "permissions", "health", "audit")),
                      picker=("Chức năng", "Người dùng, đội, quyền, nhật ký, sức khoẻ dữ liệu, nhập liệu, kết nối, quy trình — không thấy khách, tiền, lương.",
                              "Nguồn dữ liệu nào đang cập nhật trễ?"),
                      grants=(("core.admin", "all", "read write manage"), ("core.audit", "all", "read"), ("core.ai_usage", "all", "read"),
                              ("core.ai_skills", "all", "read write"), ("core.ai_tools", "all", "read write"), ("health", "all", "read"),
                              ("kb.common", "all", "read write manage"), ("papers.templates", "all", "read write manage"),
                              ("flow.templates", "all", "read write manage"), ("flow.automations", "all", "read write manage"),
                              ("asks", "own", "read write"), ("work", "own", "read write"), ("connectors", "all", "read write manage"),),
                      suggest=(("health.overview", "Nguồn dữ liệu nào đang cập nhật trễ, bản ghi nào cần sửa?"),
                               ("admin.deliveries", "Có sự kiện nào đang lỗi cần xử lý không?"),
                               ("admin.users", "Ai đang bị khoá tài khoản?"),
                               ("admin.audit", "Hôm nay có ai bị chặn quyền khi thao tác không?"))),
         "staff": Role("Nhân viên", ai_tier="basic", entry=True, tools=("flow.tasks", "flow.task", "flow.plans", "flow.plan", "flow.requests", "flow.adjust_task", "flow.set_task", "flow.request_team"),
                       home=(None, ("plans", "flow_board")),
                       picker=("Văn phòng", "Vai chung cho phòng khác (nhân sự, kế hoạch, pháp lý…): việc được giao, quy trình, tài liệu.",
                               "Hôm nay tôi cần làm gì?"),
                       grants=(("kb.common", "all", "read"), ("papers", "own", "read write"),
                               ("flow", "team", "read write"), ("flow.templates", "all", "read"), ("asks", "all", "read write"),)),
     },
     teams=(("QT", "Quản trị hệ thống", "admin"), ("HCNS", "Hành chính – Nhân sự", "department")),
     # 0239 phần 3: sổ cái là thông tin nhạy cảm — chỉ có nghĩa "cả công ty" (chưa định nghĩa "sổ cái của mình")
     info_groups=(InfoGroup("ledger", "Sổ cái (số dư, công nợ, ví)", "ledger",
                            "Màn Sổ cái / Số dư sổ cái và số đọc thẳng từ sổ cái; màn kế toán khác theo quyền Kế toán", scopes=("all",)),),
     questions=(
         Question("grant:tech:core.admin", "Công ty có người giữ hệ thống (Công nghệ) tách khỏi giám đốc / quản trị không?",
                  (("yes", "Có — vai Công nghệ: người dùng, đội, quyền, nhật ký, kết nối; không thấy khách, tiền, lương"),
                   ("no", "Không — giám đốc / Quản trị hệ thống tự làm")),
                  "yes", "Người giữ hệ thống không cần thấy tiền và SĐT khách; tách vai thì ít người thấy thông tin nhạy cảm hơn",
                  "giám đốc", "admin.update_member", topic="Vai & quyền"),
         Question("hints:*", "Câu gợi ý của trợ lý cho từng vai có cần sửa / thêm câu riêng của công ty không?",
                  (("default", "Dùng bộ câu mặc định của gói theo vai"), ("custom", "Tắt / sửa chữ / thêm câu riêng theo vai")),
                  "default", "Bộ mặc định lấy từ câu hỏi chính của từng vai; công ty sửa khi đã dùng quen", "giám đốc",
                  "core.set_role_hint", topic="Trợ lý AI"),
     ),
     # thuê ngoài là cam kết chi ⇒ qua duyệt (0074 C); công ty sửa chuỗi / bỏ luật trong app
     # việc có bộ kiểm mà đóng không cần bằng chứng là ngoại lệ ⇒ trưởng nhóm của người xin duyệt (0117); công ty sửa chuỗi trong app
     # quản trị không tự nâng quyền (0029): đổi quyền / vai / luật / chuỗi duyệt / chủ tài khoản / trưởng đội ⇒ chủ tài khoản duyệt; luật của chính
     # set_rule cũng nằm dưới chuỗi này ⇒ bỏ bảo vệ cũng phải duyệt. Sửa người dùng / đội chỉ phải duyệt ở lần gọi đổi quyền (``sensitive``).
     chains=(("outsource", "Thuê ngoài (cam kết chi)", ({"kind": "role", "role": "director"},)),
             ("task_override", "Đóng việc không cần bằng chứng", ({"kind": "team_lead"},)),
             ("access_change", "Thay đổi quyền truy cập", ({"kind": "owner"},))),
     approval_rules=(("flow.request_vendor", "outsource"),
                     *((f"admin.{c}", "access_change") for c in ("set_grant", "set_info_access", "set_rule", "set_chain", "upsert_role", "update_member",
                                                                  "invite_user", "add_team", "remove_team", "upsert_team",
                                                                  "set_agent_policy", "set_agent_trust", "set_agent_preset")),   # nới tự chủ trợ lý (0143 / 0144) = cấp quyền
                     ("kb.save_shelf", "access_change"),   # mở thêm người đọc kệ hạn chế = cấp quyền đọc tài liệu
                     # app từ xa (0140): cài / đổi quyền / xoay khoá = cấp quyền cho một người gọi ngoài ⇒ như đổi quyền người
                     *((f"connect.{c}", "access_change") for c in ("install", "set_access", "rotate_secret"))),
     rules="""- LẬP KẾ HOẠCH / GIAO NHIỀU VIỆC ("lên kế hoạch …", "giao cho đội … làm …"): (1) flow__assignees (XẾP HẠNG theo khối lượng, cột Đội / Vai) —
  chỉ đặt team_code khi người dùng giới hạn một đội, mục tiêu cần nhiều phòng (mở bán, sự kiện, ra mắt) thì KHÔNG lọc. Kế hoạch mang tên một phòng vẫn
  gồm việc PHỐI HỢP mục tiêu cần ở phòng khác có trong danh sách (bán hàng: khách / tài liệu bán; hành chính: hậu cần, địa điểm; kế toán: ngân sách,
  thanh toán); phòng được nêu là phòng CHỦ TRÌ, nhận phần lớn việc. (2) Chia thành việc cụ thể THEO CHỨC NĂNG, mỗi việc MỘT người của đúng phòng (ưu
  tiên người xếp trên, không dồn một người); hạn lùi từ mốc người dùng nêu, việc chuẩn bị trước việc chạy. (3) MỘT flow__propose_plan cho cả kế hoạch
  (team_code = phòng chủ trì; owner_email TRỐNG — code tự lấy trưởng phòng — trừ khi người dùng nêu người phụ trách) rồi DỪNG. Không bịa email, không
  chọn người ngoài danh sách; việc cần phòng KHÁC mà người hỏi không giao được ⇒ request_team = mã phòng đó (tra flow__teams; trưởng phòng đó nhận và
  chia người), không bỏ việc; việc lẻ ⇒ flow__request_team. Thiếu mốc mà không suy được ⇒ hỏi lại một câu.
- MỤC TIÊU ĐO ĐƯỢC ("đạt 50 khách … trước ngày …"): flow__metrics chọn ĐÚNG khoá chỉ số (không tự đặt công thức; không khớp ⇒ nói rõ rồi lập kế
  hoạch thường) → flow__assignees → MỘT flow__propose_plan có goal_metric, goal_target, goal_context (mã phạm vi — vd mã [[offering|l]] — nếu người
  dùng giới hạn), mốc và các việc DẪN TỚI đích.
- GIAO MỘT VIỆC bằng một câu ("giao chị Hạnh làm báo giá trước thứ 6", có thể kèm "trong kế hoạch …") ⇒ flow__resolve_assignment (who / due / plan = ĐÚNG
  LỜI người dùng nói — KHÔNG tự đổi ra email hay ngày) ⇒ status ok: MỘT flow__create_task với assignee_email, due_on, plan_id lấy nguyên từ kết quả rồi
  DỪNG (người bấm xác nhận một lần); ambiguous ⇒ hỏi lại, chọn trong candidates; not_found / invalid ⇒ nói đúng issues, không chọn người / ngày khác.
  Cần xem người rảnh để chọn ⇒ flow__assignees (xếp theo khối lượng) thay vì đoán. Việc VỀ một khách / hồ sơ / kỳ sổ đang mở ⇒ kèm subject_kind +
  subject_id của thẻ, không chép tên vào tiêu đề. Tin trong trao đổi giống một việc ⇒ flow__task_from_message (message_id, title gọn, assignee nếu tin
  nêu) — thẻ đề xuất cho riêng người hỏi, không gửi gì vào trao đổi chung.
- LỊCH CỦA VIỆC TRONG KẾ HOẠCH ("bắt đầu thứ Hai", "B chờ A xong", "xếp vào giai đoạn Chuẩn bị") ⇒ flow__set_task_timing (start_on / phase / after = mã việc
  cùng kế hoạch, lấy từ flow__plan); code chặn vòng phụ thuộc và ngày bắt đầu sau hạn. Lập kế hoạch mới ⇒ ghi start_on / phase / after (tên việc khác,
  cách nhau ;) ngay trong flow__propose_plan.
- Thuê ngoài / freelancer / agency ⇒ flow__request_vendor (đề bài, ngân sách tối đa, hạn) — qua duyệt chi rồi mới sinh việc; gửi đề bài cho bên làm
  ⇒ flow__send_brief (link một lần; ngân sách KHÔNG gửi ra ngoài).
- Kế hoạch / mục tiêu tới đâu, có trễ không ⇒ flow__plans / flow__plan: nói đúng tiến độ, dự báo, lý do trên thẻ — không tự tính số. Nhắc ⇒
  flow__remind_plan; giao lại / dời hạn MỘT việc ⇒ flow__adjust_task (người mới xếp trên trong flow__assignees, hạn không quá mốc); thêm việc ⇒
  flow__create_task với plan_id; "Chờ nghiệm thu" / huỷ / mở lại ⇒ flow__set_plan (người phụ trách quyết — trợ lý soạn đề xuất).
- Việc có "Xong khi" (bộ kiểm) ĐÓNG TỰ ĐỘNG khi dữ kiện đạt — không đề xuất flow__set_task "done"; người dùng nói đã xong ⇒ flow__recheck_task, chưa
  đạt ⇒ nói thiếu gì theo thẻ. Đóng không cần bằng chứng là việc của người (tự xin, có lý do, qua duyệt) — trợ lý không xin thay.
- PHIẾU DUYỆT cụ thể ⇒ core__approvals tìm mã rồi core__approval (MỘT phiếu: trạng thái, bước, nút Duyệt / Từ chối cho người duyệt bước hiện tại;
  chia sẻ vào trao đổi được). Trợ lý KHÔNG tự duyệt.
""",
     # HẠNG TRỢ LÝ AI mặc định (ADR 0193, quyết định chủ sản phẩm 12/11/2026) — DỮ LIỆU, của NHÀ VẬN HÀNH (``core.ai_policy`` — khách không thấy / sửa);
     # vai khai hạng của mình bằng ``Role(ai_tier=…)``, vai không khai ⇒ hạng mặc định ("Cơ bản" — rẻ nhất, không giọng nói: thận trọng cho vai của gói
     # ngoài lõi). model trống = model mặc định của bản cài; nhà cung cấp chưa có khoá ⇒ model mặc định (llm._tier_model). Hạn mức theo người bằng TOKEN
     # (mọi token trừ token ĐỌC cache — usage.quota_tokens): "Cơ bản" 5.000.000 / người / tháng (≈ 40–75 lượt theo số đo 12/11 — ADR 0193), "Quản lý" / "Lãnh đạo" không giới hạn.
     records=(*(("core.default_ai_tier", key, {"key": key, **data}) for key, data in (
         ("basic", {"label": "Cơ bản", "model": "deepseek:deepseek-flash", "person_monthly_tokens": 5_000_000, "voice": False, "is_default": True,
                    "sort": 10}),
         ("manager", {"label": "Quản lý", "model": "", "person_monthly_tokens": None, "voice": False, "sort": 20}),
         ("executive", {"label": "Lãnh đạo", "model": "", "choices": ["anthropic:claude-sonnet-5", "openai:gpt-5.5"], "person_monthly_tokens": None,
                        "voice": True, "sort": 30}),
     )),
         # KỸ NĂNG mặc định của gói nền (0198, 0220 lát 6): CÁCH tìm vì sao một con số thay đổi — dữ liệu của công ty (sửa / ngừng / soạn bản riêng ở
         # Quản trị › Kỹ năng trợ lý); chỉ dùng số chuẩn qua công cụ, không nêu ngưỡng / mức trong chữ.
         skill_specs.declare(
             "tim_vi_sao", title="Tìm vì sao một con số thay đổi",
             when_to_use="Hỏi vì sao một số tăng / giảm / lệch so với kỳ trước hoặc so với mong đợi (lead, doanh thu, chi phí, tỷ lệ…), hoặc nhờ phân tích nguyên nhân",
             roles=("director", "admin", "team_lead", "sales_admin", "marketing_head", "marketer", "accountant"), tools=("apps.answer",),
             instructions="""
1. Xác định CON SỐ và HAI KỲ để so: kỳ đang hỏi và kỳ liền trước cùng độ dài (tháng này ↔ tháng trước, quý này ↔ quý trước, năm nay ↔ năm trước).
   Người hỏi không nêu kỳ ⇒ tháng này so với tháng trước, và nói rõ đã chọn như vậy.
2. Kiểm số có thật sự đổi: gọi apps__answer với hai khối kpi cùng chỉ số cho hai kỳ. Thẻ có dòng cảnh báo nguồn (số chưa đủ tới ngày nào) ⇒ nói
   điều đó TRƯỚC — số kỳ này thấp có thể chỉ vì dữ liệu chưa về đủ, không kết luận nguyên nhân kinh doanh trên số thiếu.
3. Tách theo TỪNG chiều của tập dữ liệu (mỗi lần một chiều, cả hai kỳ): gọi apps__answer với khối table chia theo chiều đó cho kỳ này và kỳ trước.
   Bắt đầu từ chiều gần nguyên nhân nhất với câu hỏi (nguồn, kênh, đội, người giữ, sản phẩm), tối đa bốn chiều.
4. Với mỗi chiều, tìm nhóm có CHÊNH LỆCH TUYỆT ĐỐI lớn nhất giữa hai kỳ (không phải thay đổi theo tỷ lệ của nhóm nhỏ). Chiều nào một hai nhóm chiếm
   phần lớn chênh lệch thì đó là chỗ cần nhìn; chiều nào mọi nhóm cùng đổi đều thì không phải nguyên nhân.
5. Số là tỷ lệ ⇒ xem riêng tử và mẫu: tỷ lệ đổi vì tử đổi hay vì mẫu đổi.
6. Trả lời ngắn: (a) số đổi bao nhiêu giữa hai kỳ; (b) một đến ba chỗ đóng góp nhiều nhất, mỗi chỗ một câu, nêu chiều và nhóm; (c) điều CHƯA biết
   (chiều chưa có trong số chuẩn, dữ liệu chưa đủ); (d) một việc nên làm tiếp. Kết thúc bằng MỘT lần apps__answer gồm biểu đồ so hai kỳ theo chiều
   giải thích được nhiều nhất — số nằm trên thẻ, không chép cả bảng vào chữ. Không đoán nguyên nhân ngoài dữ liệu; chỉ nói "có thể" khi số gợi ý vậy.
"""),
     ),
     kind="core")
m = Module("core", "Lõi", pack="base", setup=("core.admin",), resources={
    "core.admin": "Quản trị hệ thống (người dùng, tổ chức, quyền, chuỗi duyệt)",
    "core.audit": "Nhật ký thao tác",
    "core.approvals": "Mọi phiếu duyệt trong công ty",
    "ledger": "Sổ cái (công nợ, ví, tiền)",
    "core.ai_usage": "Lượt dùng trợ lý AI (theo người, loại việc)",
    # CỦA NHÀ VẬN HÀNH (0193): hạng, model, hạn mức token, báo cáo token — không vai nào của khách cấp được (``registry.OPERATOR_RESOURCES``, kernel/ai_tiers.py)
    "core.ai_policy": "Chính sách trợ lý AI (hạng, model, hạn mức, báo cáo token) — chỉ nhà vận hành",
    # CỦA NHÀ VẬN HÀNH (0206): cửa nhà vận hành — đặt quyền, tạo vai, cài app, quyết phiếu đổi quyền (``kernel/operator_door.py``, ``barista.operator``)
    "core.operator": "Cửa nhà vận hành (đặt quyền, tạo vai, cài ứng dụng, quyết phiếu đổi quyền) — chỉ nhà vận hành",
    # CỦA CÔNG TY (0198): kỹ năng trợ lý là cách làm nghiệp vụ — công ty soạn / duyệt; nhà vận hành thu hẹp được qua ma trận quyền (dữ liệu)
    "core.ai_skills": "Kỹ năng trợ lý (cách làm việc hay gặp — soạn, duyệt, ngừng)",
    "core.ai_tools": "Công cụ trợ lý theo vai (vai mang sẵn nhóm công cụ nào — không cấp quyền)",
})

# Nhà cung cấp AI THỨ HAI (agent/llm.py, runbook 15): bản cài có thể khai chuỗi dự phòng, nhưng dữ liệu công ty chỉ tới nhà thứ hai khi công ty BẬT
# công tắc này (chính sách là dữ liệu — quản trị đổi ở Cài đặt công ty). Mặc định TẮT: cần thoả thuận xử lý dữ liệu với nhà cung cấp thứ hai trước.
AI_FALLBACK_VENDOR = m.setting(
    "ai_allow_fallback_vendor", "Cho phép trợ lý dùng nhà cung cấp AI thứ hai khi nhà chính gián đoạn", default=False,
    help="Bật: khi nhà cung cấp AI chính lỗi tạm / quá tải, câu hỏi (kèm dữ liệu công ty trợ lý đọc) được gửi sang nhà cung cấp dự phòng của bản "
         "cài. Tắt: chỉ nhà chính — gián đoạn thì người dùng thấy thông báo lỗi. Chỉ bật khi đã có thoả thuận xử lý dữ liệu với nhà dự phòng.")
if AI_FALLBACK_VENDOR != company_settings.AI_FALLBACK_VENDOR:   # auth.load đọc đúng khoá này
    raise RuntimeError("khoá cài đặt nhà cung cấp AI thứ hai lệch giữa core_ops và company_settings")

INBOX_OPEN = {"query": "core.approvals", "input": {}}
STATUS = {"pending": "Chờ duyệt", "executed": "Đã duyệt", "rejected": "Từ chối", "failed": "Duyệt nhưng lỗi khi chạy",
          "stale": "Căn cứ đã đổi"}
TONES = {"pending": "orange", "executed": "green", "rejected": "gray", "failed": "red", "stale": "purple"}


class FindIn(Input):
    q: str = field("Tìm", max_length=60, example="Nguyễn Lan, 0912, A-101, ZZA")


@m.query("find", resource=None, title="Tìm nhanh", agent="allow", category="core")
async def find(ctx: Ctx, inp: FindIn) -> dict[str, Any]:
    """⌘K (0087): MỘT ô tìm cho mọi gói đang bật — mỗi module tự khai nguồn tìm (``Module.search``), lõi gọi từng nguồn bằng QUYỀN NGƯỜI TÌM
    (RLS người đó); gói tắt / thiếu quyền ⇒ bỏ nhóm, không lỗi. Lõi không biết module nào. Mỗi dòng mở thẳng đúng thẻ; nhóm còn hơn 8 dòng và
    nguồn khai màn danh sách (``more=``) ⇒ thêm dòng "xem tất cả". Màn / chức năng tìm ở app (lọc tên màn trong manifest người đó — không gọi server)."""
    q = " ".join(inp.q.split())
    if len(q) < 2:
        return ui.card("Tìm nhanh", [ui.b_callout("muted", "Gõ ít nhất 2 ký tự", "Tên khách, SĐT, mã hồ sơ, tên tài liệu, kế hoạch, việc…")],
                       icon="magnifyingglass")
    blocks: list[dict[str, Any]] = []
    for src in sorted(registry.SEARCHES.values(), key=lambda s: (s.rank, s.label)):
        rows = await ctx.call_as_optional(src.op, ctx.p, q=q)
        if not rows:
            continue
        items = rows[:search.ROWS]
        if len(rows) > search.ROWS and src.more and can_run(ctx.p, src.more[0]):   # còn nữa ⇒ mở màn danh sách đã lọc (cùng cách khớp)
            items.append({"title": f"Xem tất cả kết quả “{q}”", "subtitle": src.label, "tone": "muted",
                          "open": {"query": src.more[0], "input": {**src.more[1], "q": q}}})
        blocks.append(ui.b_list(items, src.label))
    if not blocks:
        blocks.append(ui.b_callout("muted", f"Không thấy “{q}”", "Chỉ tìm trong phần anh/chị được xem. Thử tên khác hoặc ít ký tự hơn."))
    return ui.card(f"Tìm “{q}”", blocks, icon="magnifyingglass")


class InboxFilter(Input):
    status: str = field("Trạng thái", "pending", options={"pending": "Chờ duyệt", "all": "Tất cả"})


@m.query("approvals", resource=None, title="Phiếu duyệt", category="core")
async def approvals(ctx: Ctx, inp: InboxFilter) -> dict[str, Any]:
    """Phiếu mình xin + phiếu đang chờ mình duyệt (+ mọi phiếu nếu có quyền core.approvals phạm vi all)."""
    rows = await ctx.all(
        """SELECT a.id, a.summary, a.status, a.created_at, a.current_step, a.steps, a.error, a.approver_ids,
                  u.name AS requester, (core.me() = ANY(a.approver_ids) AND a.status = 'pending') AS mine
           FROM core.approvals a JOIN core.users u ON u.id = a.requested_by
           WHERE (%(s)s = 'all' OR a.status = 'pending')
           ORDER BY (core.me() = ANY(a.approver_ids) AND a.status = 'pending') DESC, a.created_at DESC LIMIT 200""",
        {"s": inp.status})
    out = []
    for r in rows:
        steps = r["steps"]
        step = steps[r["current_step"]]["label"] if r["status"] == "pending" and r["current_step"] < len(steps) else ""
        out.append({"id": r["id"], "summary": r["summary"], "requester": r["requester"], "step": step,
                    "progress": f"{min(r['current_step'] + (r['status'] != 'pending'), len(steps))}/{len(steps)}",
                    "status": r["status"], "mine": "Chờ bạn" if r["mine"] else "", "created_at": r["created_at"],
                    "error": r["error"]})
    waiting = sum(1 for r in rows if r["mine"])
    return ui.table(out, [
        ui.col("mine", "", "badge", tones={"Chờ bạn": "orange"}),
        ui.col("summary", "Nội dung"), ui.col("requester", "Người xin"), ui.col("step", "Bước hiện tại"),
        ui.col("progress", "Tiến độ"), ui.col("status", "Trạng thái", "badge", labels=STATUS, tones=TONES),
        ui.col("created_at", "Lúc gửi", "datetime"), ui.col("error", "Lỗi"),
    ], title="Phiếu duyệt", kpis=[ui.kpi("Chờ bạn duyệt", waiting, tone="orange" if waiting else None)],
        empty="Không có phiếu nào đang chờ")


class ApprovalRef(Input):
    approval_id: UUID = field("Phiếu", hidden=True)


@m.query("approval", resource=None, title="Phiếu duyệt", category="core")
async def approval(ctx: Ctx, inp: ApprovalRef) -> dict[str, Any]:
    """MỘT phiếu (0089) — chia sẻ vào phòng trao đổi được: trạng thái + các bước; nút Duyệt / Từ chối CHỈ hiện cho người được duyệt ở bước
    hiện tại (``core.decide`` vẫn kiểm lại — app chỉ vẽ). Ai không xem được phiếu (RLS) ⇒ không thấy."""
    a = await ctx.one("""SELECT a.*, u.name AS requester FROM core.approvals a JOIN core.users u ON u.id = a.requested_by WHERE a.id = %s""",
                      (inp.approval_id,))
    if a is None:
        raise NotFound("Không thấy phiếu (hoặc bạn không xem được)")
    steps, decisions = a["steps"], a["decisions"] or []
    pending = a["status"] == "pending"
    rows = [{"label": s["label"], "state": "done" if i < a["current_step"] or (not pending and i <= a["current_step"])
             else "current" if pending and i == a["current_step"] else "todo",
             "note": next((d.get("note") or "" for d in decisions if d.get("step") == i), "")} for i, s in enumerate(steps)]
    mine = pending and ctx.p.user_id in (a["approver_ids"] or [])
    actions = [ui.act("core.decide", "Duyệt", {"approval_id": str(a["id"]), "approve": True}, "primary"),
               ui.act("core.decide", "Từ chối", {"approval_id": str(a["id"]), "approve": False}, "destructive")] if mine else []
    state = (f"Chờ duyệt bước {a['current_step'] + 1}/{len(steps)}: {steps[a['current_step']]['label']}" if pending and a["current_step"] < len(steps)
             else STATUS.get(a["status"], a["status"]))
    sub = f"{state} · {a['requester']} xin lúc {a['created_at'].astimezone(zone()):%d/%m %H:%M}"
    blocks: list[dict[str, Any]] = [ui.b_steps(rows, "Các bước duyệt")]
    if a["error"]:
        blocks.append(ui.b_callout("bad" if a["status"] == "failed" else "warn", "Ghi chú của phiếu", a["error"]))
    return ui.card(a["summary"], blocks, subtitle=("Chờ bạn duyệt · " if mine else "") + sub, icon="checkmark.seal", actions=actions)


async def _stale(ctx: Ctx, a: dict[str, Any], decisions: list[dict[str, Any]], changed: list[str]) -> dict[str, Any]:
    """Căn cứ đổi ⇒ phiếu kết thúc KHÔNG chạy (như từ chối về hệ quả: ``on_reject`` nhả thứ phiếu đang giữ), ghi khoá đổi + nhật ký."""
    msg = stale_message(changed)
    decisions[-1] = {**decisions[-1], "stale": changed}
    if a["on_reject"]:
        await invoke(ctx, a["on_reject"], a["input"], as_principal=system_principal(ctx.p.tenant_id, a["requested_by"]), trusted=True)
    await ctx.exec("UPDATE core.approvals SET status = 'stale', decisions = %s, approver_ids = '{}', error = %s, updated_at = now()"
                   " WHERE id = %s", (Jsonb(decisions), msg, a["id"]))
    await ctx.exec("INSERT INTO core.audit_log (user_id, via, command, input, status, error, ref) VALUES (%s, 'human', %s, %s, 'conflict', %s, %s)",
                   (ctx.p.user_id, a["command"], Jsonb(a["input"]), msg, str(a["id"])))
    await ctx.notify([a["requested_by"]], kind="approval_done", title=f"Phiếu hết căn cứ: {a['summary']}", body=msg,
                     push="Một phiếu của anh/chị cần làm lại", open=INBOX_OPEN)
    return {"status": "stale", "warning": msg, "changed": changed}   # "warning": app giữ hộp thoại, hiện câu này thay vì "Đã xong"


class Decide(Input):
    approval_id: UUID = field("Phiếu", hidden=True)
    approve: bool = field("Đồng ý", True)
    note: str = field("Ghi chú", "", kind="textarea", max_length=1000)


@m.command("decide", resource=None, title="Duyệt / từ chối phiếu", agent="deny")  # duyệt chỉ do NGƯỜI bấm
async def decide(ctx: Ctx, inp: Decide) -> dict[str, Any]:
    """Người được duyệt ở bước hiện tại quyết định. Bước cuối đồng ý ⇒ chạy lệnh đích ngay trong cùng lần ghi này
    (lỗi khi chạy ⇒ phiếu 'failed', quyết định vẫn ghi). Đồng ý ở BẤT KỲ bước nào mà căn cứ của lệnh đã đổi so với lúc xin (0071) ⇒ phiếu
    'stale', không chạy, không sang bước sau — người xin làm phiếu mới trên dữ liệu hiện tại."""
    a = await ctx.one("SELECT * FROM core.approvals WHERE id = %s FOR UPDATE", (inp.approval_id,))
    if a is None:
        raise NotFound("Không thấy phiếu duyệt")
    if a["status"] != "pending":
        raise Invalid("Phiếu đã xử lý xong")
    if ctx.p.via != "human":
        raise Denied("Phiếu duyệt chỉ do người bấm — trợ lý không duyệt thay")
    if ctx.p.user_id not in a["approver_ids"]:
        raise Denied("Bạn không phải người duyệt ở bước này")
    steps = a["steps"]
    step_no = a["current_step"]
    decisions = [*a["decisions"], {"step": step_no, "label": steps[step_no]["label"], "user_id": str(ctx.p.user_id),
                                   "name": ctx.p.name, "approve": inp.approve, "note": inp.note,
                                   "at": datetime.now(UTC).isoformat()}]
    if not inp.approve:
        return await _reject(ctx, a, decisions, f"{ctx.p.name}: {inp.note}" if inp.note else ctx.p.name)
    if changed := await _basis_moved(ctx, a):
        return await _stale(ctx, a, decisions, changed)
    deciders = {UUID(d["user_id"]) for d in decisions}
    nxt = step_no + 1
    if nxt < len(steps):
        label, ids = await resolve_step(ctx.conn, ctx.p.tenant_id, steps[nxt], a["requested_by"], deciders)
        if not ids:
            raise Invalid(f"Bước “{label}” không còn người duyệt khác — quản trị cần sửa chuỗi duyệt")
        steps[nxt] = {**steps[nxt], "label": label, "approver_ids": [str(i) for i in ids]}
        await ctx.exec("UPDATE core.approvals SET current_step = %s, steps = %s, approver_ids = %s, decisions = %s,"
                       " participant_ids = participant_ids || %s, updated_at = now() WHERE id = %s",
                       (nxt, Jsonb(steps), ids, Jsonb(decisions), ids, a["id"]))
        await ctx.notify(ids, kind="approval", title=f"Phiếu chờ anh/chị duyệt: {a['summary']}", body=f"{ctx.p.name} đã duyệt bước trước",
                         push="Có phiếu chờ anh/chị duyệt", open=INBOX_OPEN)
        return {"status": "pending", "next_step": label}
    # Bước cuối: chạy lệnh đích. Người duyệt ghi vào principal ⇒ nhật ký / sổ cái biết ai cho phép.
    return await _execute(ctx, a, decisions)


async def _reject(ctx: Ctx, a: dict[str, Any], decisions: list[dict[str, Any]], body: str) -> dict[str, Any]:
    if a["on_reject"]:
        await invoke(ctx, a["on_reject"], a["input"], as_principal=system_principal(ctx.p.tenant_id, a["requested_by"]), trusted=True)
    await ctx.exec("UPDATE core.approvals SET status = 'rejected', decisions = %s, approver_ids = '{}', updated_at = now()"
                   " WHERE id = %s", (Jsonb(decisions), a["id"]))
    await ctx.notify([a["requested_by"]], kind="approval_done", title=f"Phiếu bị từ chối: {a['summary']}", body=body,
                     push="Một phiếu của anh/chị bị từ chối", open=INBOX_OPEN)
    return {"status": "rejected"}


async def _basis_moved(ctx: Ctx, a: dict[str, Any]) -> list[str]:
    if a["basis"] is not None and (op := registry.get(a["command"])) is not None:
        return basis_changed(a["basis"], await basis_of(ctx, op, a["input"]))
    return []


async def _execute(ctx: Ctx, a: dict[str, Any], decisions: list[dict[str, Any]], note: str = "") -> dict[str, Any]:
    """Phiếu đã đủ người đồng ý ⇒ chạy lệnh đích (bất biến 6): bằng quyền NGƯỜI XIN nạp lại lúc này, hoặc quyền hệ thống mang tên người duyệt. ``note``:
    ghi chú cho nhật ký của lệnh đích (vd nhà vận hành quyết thay chủ tài khoản — 0206)."""
    approvers = tuple(UUID(d["user_id"]) for d in decisions)
    if a["run_as"] == "system":
        target = issued(replace(system_principal(ctx.p.tenant_id, a["requested_by"], approved_by=approvers), approved_scope=(a["command"], a["chain"])))
    else:
        requester = await auth.load(ctx.conn, ctx.p.tenant_id, user_id=a["requested_by"])
        if requester is None:
            raise Invalid("Người xin không còn hoạt động trong công ty — không chạy lệnh")
        # quyền người xin, nạp lại lúc này — không nâng quyền; phạm vi phiếu = đúng lệnh + chuỗi đã duyệt
        target = issued(replace(requester, approved_by=approvers, approved_scope=(a["command"], a["chain"])))   # lõi cấp (0130)
    nested: list[tuple[str, dict[str, Any]]] = []
    status, result, error = "executed", None, ""
    try:
        async with ctx.conn.transaction():
            result = jsonable(await invoke(ctx, a["command"], a["input"], as_principal=target, events=nested, trusted=True))
    except Exception as e:   # lệnh lồng bị từ chối / lỗi ⇒ ghi trạng thái failed của yêu cầu (map_error), không ném tiếp
        r = map_error(e)
        status, error = "failed", r.error
        nested.clear()
    await set_session(ctx.conn, ctx.p)
    ctx.events.extend(nested)
    if status == "executed" and (op := registry.get(a["command"])) is not None and op.resource == "core.admin":
        # lệnh quản trị chạy sau duyệt (vd đổi quyền — 0029) ⇒ kênh tức thì nạp lại quyền như khi chạy thẳng (0115)
        await ctx.exec("SELECT pg_notify('barista_auth', %s)", (str(ctx.p.tenant_id),))
    await ctx.exec(
        "INSERT INTO core.audit_log (user_id, approved_by, system, via, command, input, status, error, ref)"
        " VALUES (%s, %s, %s, 'human', %s, %s, %s, %s, %s)",
        (a["requested_by"], list(approvers), target.system, a["command"], Jsonb(a["input"]),
         "ok" if status == "executed" else "error", error or note, str(a["id"])))
    await ctx.exec("UPDATE core.approvals SET status = %s, decisions = %s, approver_ids = '{}', result = %s, error = %s,"
                   " updated_at = now() WHERE id = %s",
                   (status, Jsonb(decisions), Jsonb(result), error, a["id"]))
    await ctx.notify([a["requested_by"]], kind="approval_done",
                     title=f"Phiếu đã được duyệt: {a['summary']}" if status == "executed" else f"Phiếu duyệt xong nhưng lỗi khi chạy: {a['summary']}",
                     body=error, push="Một phiếu của anh/chị đã có kết quả", open=INBOX_OPEN)
    return {"status": status, "error": error, "result": result}


# ── Cửa nhà vận hành (0206): phiếu ĐỔI QUYỀN đang chờ — chỉ ``python -m barista.operator pending | decide`` (quyền hệ thống, có tên + lý do) ──

def _owner_chain(steps: list[dict[str, Any]]) -> bool:
    return bool(steps) and all(s.get("kind") == "owner" for s in steps)


@m.query("operator_pending", resource=operator_door.RESOURCE, title="Phiếu chờ duyệt (nhà vận hành)", internal=True, agent="deny")
async def operator_pending(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Mọi phiếu đang chờ của công ty; ``operator`` = nhà vận hành quyết được (chuỗi chỉ gồm bước chủ tài khoản)."""
    rows = await ctx.all("""SELECT a.id, a.chain, a.command, a.summary, a.steps, a.current_step, a.created_at, u.name AS requester, u.email
                            FROM core.approvals a JOIN core.users u ON u.id = a.requested_by WHERE a.status = 'pending' ORDER BY a.created_at""")
    for r in rows:
        steps = r.pop("steps")
        r["step"] = steps[r["current_step"]]["label"] if r["current_step"] < len(steps) else ""
        r["operator"] = _owner_chain(steps)
    return {"rows": rows}


class OperatorDecide(Input):
    approval_id: UUID = field("Phiếu", hidden=True)
    approve: bool = field("Đồng ý", True)
    by: str = operator_door.by_field()
    why: str = operator_door.why_field()


@m.command("operator_decide", resource=operator_door.RESOURCE, title="Quyết phiếu đổi quyền (nhà vận hành)", internal=True, agent="deny")
async def operator_decide(ctx: Ctx, inp: OperatorDecide) -> dict[str, Any]:
    """Nhà vận hành quyết THAY chủ tài khoản một phiếu ĐỔI QUYỀN (chuỗi chỉ gồm bước chủ tài khoản — 0029), cả phiếu một lần. Phiếu nghiệp vụ (tiền,
    giấy tờ…) có người duyệt khác ⇒ từ chối: người trong công ty duyệt. Đồng ý ⇒ lệnh đích chạy y như sau duyệt (bất biến 6): bằng quyền NGƯỜI XIN nạp
    lại, phạm vi phiếu = đúng lệnh + chuỗi; căn cứ đổi ⇒ không chạy. Phiếu + nhật ký lệnh đích ghi "Nhà vận hành <tên> — <lý do>". Người vận hành
    không quyết phiếu chính mình xin (tên / email trùng người xin)."""
    operator_door.require_who(inp)
    a = await ctx.one("SELECT * FROM core.approvals WHERE id = %s FOR UPDATE", (inp.approval_id,))
    if a is None:
        raise NotFound("Không thấy phiếu duyệt")
    if a["status"] != "pending":
        raise Invalid("Phiếu đã xử lý xong")
    if not _owner_chain(a["steps"]):
        raise Denied("Nhà vận hành chỉ quyết phiếu đổi quyền (chuỗi chủ tài khoản) — phiếu nghiệp vụ do người duyệt trong công ty quyết")
    who = await ctx.one("SELECT name, email FROM core.users WHERE id = %s", (a["requested_by"],))
    by = inp.by.strip().lower()
    if who and by in {(who["name"] or "").strip().lower(), (who["email"] or "").strip().lower(), (who["email"] or "").split("@")[0].lower()}:
        raise Denied("Người xin không tự duyệt (bất biến 6) — việc của chính người vận hành thì làm thẳng bằng cửa nhà vận hành (set-grant / install-app)")
    step_no = a["current_step"]
    noted = operator_door.note(inp.by, inp.why)
    me = await ctx.val("SELECT core.ensure_user(%s, %s)", (operator_door.SERVICE_EMAIL, operator_door.DECIDED_BY))
    decisions = [*a["decisions"], {"step": step_no, "label": a["steps"][step_no]["label"], "user_id": str(me), "operator": inp.by.strip(), "name": noted,
                                   "approve": inp.approve, "note": noted, "at": datetime.now(UTC).isoformat()}]
    if not inp.approve:
        return await _reject(ctx, a, decisions, noted)
    if changed := await _basis_moved(ctx, a):
        return await _stale(ctx, a, decisions, changed)
    return await _execute(ctx, a, decisions, note=("Duyệt bởi " + noted[:1].lower() + noted[1:])[:2000])


# ── Hộp thông báo (quyết định 0044) — mỗi người chỉ thấy hộp của mình (RLS), kể cả quản trị ──

class NotificationsIn(Input):
    limit: int = field("Số thông báo", 50, ge=1, le=200)


@m.query("notifications", resource=None, title="Thông báo của tôi", category="core")
async def notifications(ctx: Ctx, inp: NotificationsIn) -> dict[str, Any]:
    rows = await ctx.all("SELECT id, kind, title, body, open, created_at, read_at IS NOT NULL AS read FROM core.notifications"
                         " ORDER BY created_at DESC, id DESC LIMIT %s", (inp.limit,))
    unread = await ctx.val("SELECT count(*) FROM core.notifications WHERE read_at IS NULL")
    return {"unread": int(unread or 0), "items": rows}


class MarkRead(Input):
    ids: list[int] = field("Thông báo", [], hidden=True, help="Trống = đánh dấu đã đọc tất cả")


@m.command("read_notifications", resource=None, title="Đánh dấu thông báo đã đọc", agent="deny")
async def read_notifications(ctx: Ctx, inp: MarkRead) -> dict[str, int]:
    """Đã đọc là việc của người — trợ lý không đánh dấu thay (thông báo có thể bị bỏ sót)."""
    n = await ctx.exec("UPDATE core.notifications SET read_at = now() WHERE read_at IS NULL AND (cardinality(%s::bigint[]) = 0 OR id = ANY(%s))",
                       (inp.ids, inp.ids))
    return {"read": n}


class DeviceIn(Input):
    token: str = field("Mã thiết bị", max_length=200, hidden=True)   # app tự gửi — không ai gõ
    env: Literal["sandbox", "production"] = field("Môi trường APNs", "production", hidden=True)
    active: bool = field("Nhận thông báo", True, help="Tắt = đăng xuất trên máy này")


@m.command("register_device", resource=None, title="Đăng ký máy nhận thông báo", agent="deny")
async def register_device(ctx: Ctx, inp: DeviceIn) -> dict[str, Any]:
    """App gọi sau khi đăng nhập (và mỗi lần mở app — mã của Apple có thể đổi); gọi với ``active=false`` khi đăng xuất."""
    token = inp.token.lower()
    if not re.fullmatch(r"[0-9a-f]{32,200}", token):
        raise Invalid("Mã thiết bị không hợp lệ")
    await ctx.exec(
        """INSERT INTO core.devices (user_id, token, env) VALUES (core.me(), %s, %s)
           ON CONFLICT (tenant_id, user_id, token) DO UPDATE SET env = EXCLUDED.env, last_seen_at = now(),
               disabled_at = CASE WHEN %s THEN NULL ELSE now() END""", (token, inp.env, inp.active))
    return {"active": inp.active}


# ── Ghim theo người (0098): địa chỉ màn (phân hệ + khoá) — mở màn vẫn lọc quyền như thường, mất quyền ⇒ ghim tự ẩn ──

class PinIn(Input):
    workspace: str = field("Phân hệ", max_length=40, hidden=True)   # app điền từ màn đang xem — không ai gõ khoá màn
    key: str = field("Màn", max_length=80, hidden=True)


def _pin_ref(inp: PinIn) -> tuple[str, str]:
    if not re.fullmatch(r"[a-z_]+", inp.workspace) or not re.fullmatch(r"[a-z0-9_-]{1,80}", inp.key):
        raise Invalid("Màn không hợp lệ")
    return inp.workspace, inp.key


@m.command("pin", resource=None, title="Ghim lên sidebar", agent="allow", risk="low")   # 0144: của riêng người dùng, không phải quyền
async def pin(ctx: Ctx, inp: PinIn) -> dict[str, Any]:
    """Ghim một màn (của phân hệ, hoặc app tự ghép) lên đầu sidebar của CHÍNH người dùng — chỉ người đó thấy. Ghim rồi ⇒ giữ nguyên."""
    await ctx.exec("""INSERT INTO core.user_pins (workspace, key, position)
                      VALUES (%s, %s, (SELECT coalesce(max(position), 0) + 1 FROM core.user_pins)) ON CONFLICT DO NOTHING""",
                   _pin_ref(inp))
    return {"pinned": True, "summary": "Đã ghim lên sidebar"}


@m.command("unpin", resource=None, title="Bỏ ghim", agent="allow", risk="low")
async def unpin(ctx: Ctx, inp: PinIn) -> dict[str, Any]:
    """Bỏ một màn khỏi phần ghim của chính người dùng (màn ghim mặc định theo vai vẫn hiện). Ghim còn lưu KHOÁ CŨ của màn đã gộp vào màn này
    (``Screen.aliases``, 1.59 — màn gộp từ nhiều màn) bỏ cùng lúc — người bấm bỏ ghim trên màn họ đang thấy."""
    ws, key = _pin_ref(inp)
    old = [a for w in screens.assemble() if w.key == ws for sc in w.screens if sc.key == key for a in sc.aliases]
    n = await ctx.exec("DELETE FROM core.user_pins WHERE key = %s AND workspace = %s OR key = ANY(%s)", (key, ws, old))
    return {"pinned": False, "removed": n, "summary": "Đã bỏ ghim"}


@m.query("pins", resource=None, title="Màn tôi đã ghim", agent="deny")
async def pins(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    """Ghim của người đang xem (thứ tự đã xếp) — menu (manifest) ráp lên đầu sidebar, trước ghim mặc định theo vai."""
    return await ctx.all("SELECT workspace, key FROM core.user_pins ORDER BY position, created_at")



# ── Lượt dùng trợ lý AI (quyết định 0049; khách thấy lượt — tiền / token là của nhà vận hành, 0193) ──

USAGE_PERIODS = {"7d": "7 ngày qua", "30d": "30 ngày qua", "month": "Tháng này", "last_month": "Tháng trước"}


class UsageIn(Input):
    period: str = field("Kỳ", "30d", options=USAGE_PERIODS)


def _usage_range(period: str) -> tuple[datetime, datetime]:
    from datetime import timedelta

    from barista.kernel.clock import today, zone
    t = today()
    start = {"7d": t - timedelta(days=6), "30d": t - timedelta(days=29), "month": t.replace(day=1),
             "last_month": (t.replace(day=1) - timedelta(days=1)).replace(day=1)}[period]
    end = t.replace(day=1) if period == "last_month" else t + timedelta(days=1)
    return datetime(start.year, start.month, start.day, tzinfo=zone()), datetime(end.year, end.month, end.day, tzinfo=zone())


def _secs(ms: Any) -> str:
    """Mili giây ⇒ "4,2 giây" (None = nơi chạy chưa đo — "—", không phải 0)."""
    return "—" if ms is None else f"{float(ms) / 1000:.1f} giây".replace(".", ",")


class RunsIn(Input):
    prefix: str = field("Nhóm loại việc", max_length=60)
    since: datetime


@m.query("ai_runs", resource="core.ai_usage", title="Lượt AI theo loại việc", internal=True, agent="deny")
async def ai_runs(ctx: Ctx, inp: RunsIn) -> dict[str, int]:
    """Cho bảng đo của module (vd kết quả việc theo dõi): {loại việc: số lượt AI} của các nhóm bắt đầu bằng ``prefix`` từ ``since`` — đọc bằng QUYỀN
    NGƯỜI XEM (``call_as``, RLS theo ``core.ai_usage``). Chỉ số LƯỢT: tiền / token là của nhà vận hành (0193), khách không thấy."""
    rows = await ctx.all("""SELECT category, count(*) AS runs FROM core.ai_usage WHERE starts_with(category, %s) AND at >= %s GROUP BY category""",
                         (inp.prefix, inp.since))
    return {r["category"]: int(r["runs"]) for r in rows}


@m.query("ai_usage", resource="core.ai_usage", title="Lượt dùng trợ lý AI")
async def ai_usage(ctx: Ctx, inp: UsageIn) -> dict[str, Any]:
    """LƯỢT trợ lý theo loại việc, nơi chạy, người, ngày — trong phần người xem được xem (RLS theo ``core.ai_usage``). Khách chỉ thấy số lượt (ADR
    0193): không tiền, không token, không model, không hạn mức — những thứ đó là chính sách / căn cứ tính tiền của nhà vận hành
    (``core.ai_token_report``). Trần cả công ty (nhà vận hành đặt) chỉ hiện phần trăm đã dùng, không số tiền."""
    from barista.agent.usage import SURFACES, label
    from barista.kernel.clock import zone

    if inp.period not in USAGE_PERIODS:
        raise Invalid("Kỳ không hợp lệ")
    a, b = _usage_range(inp.period)
    rng = (a, b)
    groups = await ctx.all(
        """SELECT category, surface, count(*) AS runs, count(*) FILTER (WHERE NOT ok) AS failed, avg(duration_ms) AS ms,
                  percentile_cont(0.9) WITHIN GROUP (ORDER BY duration_ms) AS p90
           FROM core.ai_usage WHERE at >= %s AND at < %s GROUP BY category, surface ORDER BY count(*) DESC""", rng)
    people = await ctx.all(
        """SELECT u.name, count(*) AS runs FROM core.ai_usage x JOIN core.users u ON u.id = x.user_id
           WHERE x.at >= %s AND x.at < %s GROUP BY u.name ORDER BY runs DESC LIMIT 10""", rng)
    days = await ctx.all(
        """SELECT (at AT TIME ZONE core.tz())::date AS d, count(*) AS runs FROM core.ai_usage WHERE at >= %s AND at < %s GROUP BY d ORDER BY d""", rng)
    tools = await ctx.all(   # công cụ trợ lý gọi (157): lần gọi, số lượt, số người, số lần lỗi
        """SELECT t.tool, count(*) AS calls, count(DISTINCT x.id) AS runs, count(DISTINCT x.user_id) AS people,
                  (SELECT count(*) FROM core.ai_usage y, unnest(y.tool_errors) e WHERE e = t.tool AND y.at >= %(a)s AND y.at < %(b)s) AS errors
           FROM core.ai_usage x, unnest(x.tools) AS t(tool) WHERE x.at >= %(a)s AND x.at < %(b)s
           GROUP BY t.tool ORDER BY calls DESC LIMIT 30""", {"a": a, "b": b})
    runs = sum(g["runs"] for g in groups)
    failed = sum(g["failed"] for g in groups)
    timed = await ctx.one("""SELECT avg(duration_ms) AS ms, percentile_cont(0.9) WITHIN GROUP (ORDER BY duration_ms) AS p90, count(duration_ms) AS n
                             FROM core.ai_usage WHERE at >= %s AND at < %s""", rng)
    cats: dict[str, int] = {}
    for g in groups:
        cats[g["category"]] = cats.get(g["category"], 0) + int(g["runs"])
    top = sorted(cats.items(), key=lambda kv: -kv[1])[:8]
    lim = await ctx.one("""SELECT p.ai_monthly_usd AS cap, p.ai_warn_pct AS pct, coalesce(s.usd, 0) AS spent FROM core.tenant_profile p
                           LEFT JOIN core.ai_spend s ON s.tenant_id = p.tenant_id AND s.month = date_trunc('month', core.today())::date
                           WHERE p.tenant_id = core.tenant()""")
    used = min(100, int(lim["spent"] * 100 / lim["cap"])) if lim and lim["cap"] else None
    budget = ([ui.kpi("Hạn mức trợ lý tháng này", f"{used}%", "text",
                      tone="red" if used >= 100 else "orange" if used >= lim["pct"] else None,
                      hint="Cả công ty — hết hạn mức thì trợ lý tạm dừng tới đầu tháng sau (nhà cung cấp dịch vụ đặt)")]
              if used is not None and lim is not None else [])
    blocks: list[dict[str, Any]] = [ui.b_kpis([
        *budget, ui.kpi("Lượt chạy", runs), ui.kpi("Lượt lỗi", failed, tone="orange" if failed else None),
        ui.kpi("Thời gian / lượt", _secs(timed["ms"]) if timed and timed["n"] else "—", "text",
               hint=f"90% lượt xong trong {_secs(timed['p90'])} · đo {timed['n']} lượt" if timed and timed["n"] else "chưa có lượt nào được đo"),
    ])]
    if not groups:
        blocks.append(ui.b_callout("muted", "Chưa có lượt nào trong kỳ", "Trợ lý chạy là có số — mỗi lượt ghi một dòng."))
        return ui.card("Lượt dùng trợ lý AI", blocks, subtitle=USAGE_PERIODS[inp.period], icon="gauge.with.dots.needle.bottom.50percent")
    blocks += [
        ui.b_chart("bar", [label(k) for k, _ in top], [ui.line("Lượt", [v for _, v in top], "blue")],
                   title="Lượt theo loại việc", note="Loại việc = phân hệ của công cụ trợ lý dùng nhiều nhất trong lượt."),
        ui.b_chart("hbar", [r["name"] for r in people], [ui.line("Lượt", [int(r["runs"]) for r in people], "purple")], title="Lượt theo người"),
        ui.b_chart("bar", [f"{r['d']:%d/%m}" for r in days], [ui.line("Lượt", [int(r["runs"]) for r in days], "blue")], title="Lượt theo ngày"),
        ui.b_table(ui.table(
            [{"id": f"{g['category']}|{g['surface']}", "category": label(g["category"]), "surface": SURFACES.get(g["surface"], g["surface"]),
              "runs": g["runs"], "failed": g["failed"], "secs": _secs(g["ms"]), "p90": _secs(g["p90"])} for g in groups],
            [ui.col("category", "Loại việc"), ui.col("surface", "Nơi chạy"), ui.col("runs", "Lượt", "number"),
             ui.col("failed", "Lượt lỗi", "number"), ui.col("secs", "TB thời gian"), ui.col("p90", "90% lượt trong")],
            title="Chi tiết theo loại việc × nơi chạy")),
        *([ui.b_table(ui.table(
            [{"id": t["tool"], "tool": (registry.OPS[t["tool"]].title if t["tool"] in registry.OPS else t["tool"]),
              "module": registry.MODULES.get(t["tool"].split(".")[0], t["tool"].split(".")[0]), "calls": t["calls"], "runs": t["runs"],
              "people": t["people"], "errors": t["errors"]} for t in tools],
            [ui.col("tool", "Công cụ", width=220), ui.col("module", "Nhóm"), ui.col("calls", "Lần gọi", "number"), ui.col("runs", "Số lượt", "number"),
             ui.col("people", "Số người", "number"), ui.col("errors", "Lỗi", "number")],
            title="Công cụ trợ lý dùng nhiều", note="Từ khi bắt đầu ghi (migration 157). Công cụ hay lỗi ⇒ xem lại mô tả / tham số; ít ai dùng ⇒ cân nhắc "
                                                     "để sau cửa “mở công cụ thiết lập”."))] if tools else []),
        ui.b_callout("muted", "Đọc số này thế nào",
                     facts=["Một lượt = một câu hỏi / một việc trợ lý làm (kể cả lượt lỗi).",
                            f"{zone_label(head=True)}; kỳ {a.astimezone(zone()):%d/%m} → {(b.astimezone(zone())).strftime('%d/%m')} (không gồm ngày cuối)."]),
    ]
    return ui.card("Lượt dùng trợ lý AI", blocks, subtitle=USAGE_PERIODS[inp.period], icon="gauge.with.dots.needle.bottom.50percent")


# ── Tập dữ liệu sổ cái (0100): phát sinh Nợ / Có theo loại tài khoản, tài khoản, tháng, nguồn — kế toán / giám đốc tự xem số theo kiểu mình ──
m.dataset(
    "ledger", "Sổ cái", resource="ledger",
    description=("Mỗi dòng gốc là một dòng bút toán. Doanh thu, chi phí, tài sản, công nợ: dùng chỉ số amount (dương theo bản chất tài khoản) "
                 "lọc theo kind; debit / credit là phát sinh Nợ / Có thô (đối chiếu)."),
    # ``day`` = NGÀY HẠCH TOÁN (0119 — không phải lúc ghi); giữ tên cột ``day`` ⇒ hợp đồng của app không đổi
    base="""SELECT a.code AS account, a.name AS account_name, a.kind, p.amount_vnd, j.source, j.posted_on AS day
            FROM ledger.postings p JOIN ledger.accounts a ON a.id = p.account_id JOIN ledger.journals j ON j.id = p.journal_id""",
    dims={"kind": Dim("Loại tài khoản", "t.kind", labels={"asset": "Tài sản", "liability": "Nợ phải trả", "equity": "Vốn",
                                                            "revenue": "Doanh thu", "expense": "Chi phí"}),
          "account": Dim("Tài khoản", "t.account"), "account_name": Dim("Tên tài khoản", "t.account_name"),
          "source": Dim("Nguồn bút toán", "t.source"), "month": Dim("Tháng", "to_char(t.day, 'YYYY-MM')", month=True)},
    # "amount" trước (mặc định): số theo BẢN CHẤT tài khoản — doanh thu / nợ phải trả / vốn lấy bên Có, tài sản / chi phí lấy bên Nợ ⇒ "tổng doanh
    # thu", "tổng chi phí" luôn dương, không phải nhớ dấu (trợ lý từng lấy phát sinh Nợ của doanh thu ⇒ 0 đ)
    measures={"amount": Measure("Số phát sinh (theo bản chất tài khoản)",
                                "sum(CASE WHEN t.kind IN ('revenue', 'liability', 'equity') THEN -t.amount_vnd ELSE t.amount_vnd END)", "money"),
              "debit": Measure("Phát sinh Nợ", "sum(CASE WHEN t.amount_vnd > 0 THEN t.amount_vnd ELSE 0 END)", "money"),
              "credit": Measure("Phát sinh Có", "sum(CASE WHEN t.amount_vnd < 0 THEN -t.amount_vnd ELSE 0 END)", "money"),
              "net": Measure("Chênh lệch Nợ − Có", "sum(t.amount_vnd)", "money"), "count": Measure("Số dòng bút toán", "count(*)")},
    dates={"day": "Ngày hạch toán"},
)


# Nhật ký nhập file (0168 §2) + hạn giữ khoá chống lặp (0177 §2) + hạng trợ lý AI (0193) + kỹ năng trợ lý (0198) — đăng ký thao tác vào module ``core``;
# nạp sau cùng vì chúng dùng ``m`` ở trên
from barista.kernel import ai_hints, ai_skills, ai_tiers, ai_tools, docread_policy, embed_policy, idempotency_log, import_log  # noqa: E402, F401
