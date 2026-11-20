# 08 · Chia lead, hạn gọi, thu hồi — vận hành hằng ngày

Quyết định: 0034.

## Đặt luật chia (giám đốc kinh doanh / quản lý cả công ty)
Kinh doanh › **Lead đi đâu** › *Thêm luật chia* (hoặc nhờ trợ lý soạn — thẻ đề xuất hiện câu mô tả + cảnh báo trước khi bấm). Luật có *nguồn lead* và *cách nhận* (tự chia / về hàng chờ đội điều phối), quyết định 0038. Mỗi luật có **ngày hiệu lực**; muốn đổi thì thêm luật mới hiệu lực từ ngày đó (luật cũ giữ để tra cứu,
lead đã chia giữ hạn cũ). Trống "Dự án" = luật chung; có dự án = luật riêng, thắng luật chung. Cùng phạm vi + cùng ngày hiệu lực không thêm được lần hai.
- *Đội nhận lead*: gồm đội con. Trống = mọi người có vai trò được chọn.
- *Trần lead chưa gọi / người*: người đang giữ từng đó lead mà chưa gọi sẽ bị bỏ qua khi chia. 0 = không trần.
- *Hạn gọi lần đầu* tính bằng phút **làm việc** (ngoài giờ / Chủ nhật đồng hồ chưa chạy).

## Nạp lead từ nguồn ngoài + điều phối
Kinh doanh › **Lead chờ chia** › *Nạp lead* (hoặc Nhập liệu › Khách / lead; nhập tay hoặc Excel). Chỉ định được *giao cho đội* (về hàng chờ đội đó) hoặc *giao thẳng cho người*. Người điều phối (vai "Điều phối lead" / trưởng nhóm) thấy hàng chờ đội mình, mỗi lead có gợi ý người nhận — bấm "Giao cho …" hoặc nhờ trợ lý chia hộ. Có *Nhà cung cấp* + *Mã bên ngoài* thì gửi lại cùng mã không tạo lead mới.
Webhook tự động (Meta, web, Callio) sẽ gọi đúng lệnh này.

## Hàng chờ
Lead chưa chia được (chưa có luật, không ai đủ điều kiện, mọi người chạm trần, hoặc đã thu hồi hết số lần) nằm ở hàng chờ — chỉ phạm vi **cả công ty** thấy.
Màn **Chia lead & hạn gọi** hiện hàng chờ; *Chia lại hàng chờ* chạy lại ngay (vòng quét cũng tự thử mỗi phút). Chia tay: mở lead › Chuyển người phụ trách.

## Nghỉ phép
Sale bấm **Tạm ngừng / nhận lại lead** ở Việc hôm nay (đặt ngày hết hạn; để trống = nhận lại). Quản lý đặt cho người khác bằng cách điền email.

## Nạp dữ liệu từ hệ cũ (0185)
Quy trình đầy đủ, sự cố đã gặp và bước kiểm trước khi lên bản cài: [runbook 16](16-nap-du-lieu-he-cu.md). Phần dưới là riêng cho chia lead.
Dữ liệu hệ cũ là **tham chiếu** — không bao giờ khởi động nhắc việc, báo trưởng nhóm, thu hồi, chia lại, việc theo dõi hay lượt trợ lý.
- **Lớp nạp BẮT BUỘC truyền `historical=True`** ở mọi lời gọi ghi: `imports.run_import(p, lệnh, dòng, dry_run=False, historical=True)` và
  `executor.run(p, lệnh, dữ_liệu, historical=True)` — kể cả tạo lead, giao dịch, ký, khoản phải thu, phiếu thu, khoá tài khoản người đã nghỉ ở hệ cũ. Thiếu cờ thì
  lệnh chạy như việc của hôm nay: người nhận được báo, khoản quá hạn mở việc nhắc nợ, trợ lý chạy. Cờ chỉ bật được từ mã trong tiến trình (không có ở API / màn hình).
- **Lead đỗ:** trạng thái chia nạp (`leadflow.import_state`, hoặc bất kỳ lần chia nào trong lần nạp) mang `imported_at` — vòng quét bỏ qua hẳn. Màn Chia lead có ô
  "Nạp từ hệ cũ — chưa kích hoạt"; Lead chờ chia ghi nhãn trên từng lead. Lead thành **lead sống** (hạn tính từ lúc đó) khi: quản lý giao từ hàng chờ, chuyển tay lead
  chờ gọi, đổi người phụ trách lead chưa liên hệ, hoặc khoá tài khoản người giữ (ngoài lần nạp). Chưa có lệnh kích hoạt hàng loạt — đó là bước chuyển giao, làm khi chốt ngày.
- **Sau khi nạp, kiểm:** `SELECT count(*) FROM core.notifications WHERE created_at > <lúc bắt đầu nạp>` phải bằng 0 cho lần nạp; `work.items` không tăng;
  `SELECT count(*) FROM leadflow.lead_state WHERE imported_at IS NOT NULL` = số trạng thái đã nạp.
- Dữ liệu nạp TRƯỚC 0185 (không có dấu ở `crm.leads.loaded_at`, `flow.cases.historical`, `acct.receivables.followed_at`): nạp lại, hoặc đánh dấu tay trên bản cài đó trước khi bật vòng quét.

**Tin báo trưởng nhóm là bản gộp:** mỗi lượt quét gửi mỗi người một tin cho mỗi loại (quá hạn gọi lần đầu / quá hạn chăm sóc lại / lead chờ chia quá lâu) kèm số
lead; cùng loại tối đa một tin mỗi giờ mỗi người. Tra từng lead ở `leadflow.sla_events` (loại `escalated`).

## Kiểm tra khi có chuyện
- Lead không được thu hồi dù quá hạn: tiến trình đã chạy vòng quét chưa (`BARISTA_LEADFLOW_SWEEP_S`, mặc định 60; 0 = tắt)? Người giữ có ghi chăm sóc loại "Ghi chú" không (ghi chú KHÔNG tính là liên hệ)?
- Lịch sử một lead: bảng `leadflow.sla_events` (chia, vi phạm, thu hồi) + `crm.stage_events` (đổi người kèm lý do).
- Chạy tay một vòng quét trong shell: `executor.run(system_principal(<tenant>), "leadflow.enforce_sla", {})`.
