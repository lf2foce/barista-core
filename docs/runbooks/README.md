# Runbook

Làm thế nào (chạy, kiểm, thêm, quản trị, xử lý sự cố). Vì sao làm vậy: `../decisions/`. Mỗi việc vận hành mới một file,
đánh số kế tiếp; sự cố mới gặp ⇒ thêm dòng vào `05-su-co.md` (triệu chứng · nguyên nhân · xử lý).

| Số | Việc |
|---|---|
| [01](01-chay-tren-may-dev.md) | Chạy trên máy dev |
| [02](02-kiem-tra-truoc-khi-xong.md) | Kiểm tra trước khi xong / commit |
| [03](03-them-mot-thao-tac.md) | Thêm một thao tác (lệnh / truy vấn) |
| [04](04-viec-quan-tri-thuong-gap.md) | Việc quản trị thường gặp (làm trong app, không cần lập trình) |
| [05](05-su-co.md) | Sự cố |
| [06](06-noi-nguon-tai-lieu-ngoai.md) | Nối nguồn tài liệu ngoài (Google Drive) — khi làm |
| [07](07-viec-theo-doi.md) | Việc trợ lý theo dõi (nhiều ngày, DBOS) — xem, sự cố, đổi workflow, thêm loại việc |
| [08](08-chia-lead-va-han-goi.md) | Chia lead, hạn gọi, thu hồi — đặt luật, hàng chờ, nghỉ phép, kiểm tra khi có chuyện |
| [09](09-dang-nhap-google.md) | Đăng nhập Google — cấu hình client ID, thêm / khoá người, gỡ liên kết khi email cấp lại, sự cố |
| [10](10-thong-bao-day-testflight.md) | Thông báo đẩy + TestFlight: khoá APNs, App Store Connect, thử trên máy thật / trình giả lập, sự cố |
| 11 (runbook 11, nội bộ) | Triển khai bản demo lên VPS: Docker + Swarm không gián đoạn, phát hành / quay lui, khoá CI, sao lưu |
| [12](12-them-mot-goi.md) | Thêm một gói: hợp đồng gói (mục phải khai), nghiệm thu trước phát hành (diễn tập nâng cấp, giữ cấu hình, khôi phục sao lưu) |
| [13](13-cap-nhat-quy-dinh.md) | Có luật / nghị định mới: thêm phiên bản bộ quy định (file JSON theo ngày hiệu lực, căn cứ, giả định / đã xác nhận) — không sửa code |
| [14](14-dien-tap-tat-khan-tro-ly.md) | Diễn tập tắt khẩn trợ lý — ba nút (tắt nhóm, gỡ nới, tắt công tắc), kịch bản, số đo phải ghi (0144 mục 6) |
| [14](14-demo-dao-tao.md) | Demo gói Đào tạo: nạp chuỗi ba trung tâm giả (`--profile edu-demo`), có gì trong dữ liệu, đăng nhập theo vai |
| [15](15-khoa-model-thu-lai-du-phong.md) | Khoá model, thử lại lỗi tạm, nhà cung cấp dự phòng + cầu dao, loại lỗi trong `core.ai_usage`, hàng rào chèn lệnh vào dữ liệu + eval |
| [16](16-nap-du-lieu-he-cu.md) | Nạp dữ liệu từ hệ cũ (migrate): rủi ro dữ liệu cũ bị coi là việc hôm nay (sự cố 47.700 thông báo), cờ `historical=True`, kiểm im lặng trước khi lên bản cài, rủi ro còn mở, ngày chuyển giao |
| 17 (runbook 17, nội bộ) | Cửa nhà vận hành (`python -m barista.operator`): đặt quyền, tạo vai, cài / xoay khoá / thu hồi app bảo lãnh bằng vai, quyết phiếu đổi quyền, cài CÓ CHỌN LỌC phần mặc định chưa cài (`install-defaults --kind … / --only …`, cùng vòng cài với nút Cài phần còn thiếu) — người + lý do, chạy thử, khoá in một lần |
| [18](18-db-dev-gia-day-du.md) | DB dev GIẢ đầy đủ: `bash backend/scripts/dev_reset.sh` (DB mẫu nhân bản + lớp bồi theo hôm nay), DB nào là giả / DB nào là DỮ LIỆU THẬT không được dùng demo, đăng nhập theo vai, file Excel thử nhập |
| [19](19-bao-cao-chay-song-song.md) | Báo cáo chạy song song (0230): bật, đọc, xuất cho hội đồng quản trị; điều kiện trước khi cho khách xem; thêm một phát hiện; dựng dữ liệu giả để bấm thử |
| [20](20-doc-tai-lieu-bo-doc-va-benchmark.md) | Đọc tài liệu / OCR (ADR 0232): hồ sơ đọc theo công ty × mục đích (model, công tắc, trần trang, hạn mức token), chi phí, ảnh, chạy benchmark, thêm bộ đọc ngoài, sự cố |
| [21](21-goi-cua-doi-tac.md) | Gói của đối tác / bên ngoài: repo riêng của họ, sinh khuôn `scripts/pack.sh new`, tự kiểm `scripts/pack.sh check` (lõi sạch, ruff, pyright, test của gói, luật cấu trúc của lõi), gửi tag để đọc mã trước khi ghim (ADR 0242) |
| [22](22-goi-ket-noi.md) | Gói kết nối: nối Barista với một hệ ngoài (CRM, WordPress, CMS, tổng đài, sàn, vận chuyển) trên khung kết nối chung của lõi — bộ nối + lệnh nhận + form kết nối; lõi lo khoá mã hoá, lịch kéo, thử lại, webhook, màn Kết nối ngoài (ADR 0244). Sinh khuôn `pack.sh new … --connector` |
