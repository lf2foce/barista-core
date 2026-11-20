# Barista

Nền tảng vận hành doanh nghiệp theo gói, có trợ lý AI dùng cùng quyền và cửa thực thi với người dùng. Code dùng chung; cấu hình từng công ty là dữ liệu.

## Bắt đầu từ đây

- Người trong team: đọc `barista-docs/BAT-DAU.md` (repo nội bộ) trước — bốn repo, cài máy, bảng điều phối, cách gộp code.
- [Tổng quan hệ thống](docs/OVERVIEW.md): sản phẩm, các tầng và tình trạng triển khai.
- [Từ điển kiến trúc](docs/GLOSSARY.md): nguồn định nghĩa chung cho Pack, Workspace, Module, Screen, Tool, Skill và Workflow.
- [Kiến trúc](docs/ARCHITECTURE.md): ranh giới và hợp đồng kỹ thuật.
- Các quyết định: lý do, phương án chọn / bỏ, trạng thái từng thiết kế.
- [Runbook](docs/runbooks/README.md): cách chạy và vận hành.
- [Quy tắc sửa repo](CLAUDE.md): bất biến và kiểm tra trước khi hoàn tất.

## Hai loại từ điển

**Từ điển kiến trúc** ở [docs/GLOSSARY.md](docs/GLOSSARY.md) là nơi duy nhất định nghĩa thuật ngữ dùng trong code và thiết kế. Các ADR dẫn tới đó, chỉ bổ sung quyết định riêng.

**Từ điển nghiệp vụ / chỉ số** ở [docs/references/tu-dien-nghiep-vu.md](docs/references/tu-dien-nghiep-vu.md) phục vụ thống nhất nghĩa với khách: lead, doanh số, thực thu… Các định nghĩa này cần đối chiếu phiên bản code và xác nhận nghiệp vụ; không phải tên khái niệm kiến trúc. Tài liệu không tự thay đổi cách tính trong phần mềm.

## Trợ lý: công cụ và kỹ năng

Trợ lý nạp công cụ theo nhu cầu (0095), kỹ năng là dữ liệu của công ty (0201) và mỗi vai có bộ công cụ
mặc định (0218) — đều đã chạy. Ứng dụng AI của nhân viên (Claude, ChatGPT…) đọc qua MCP và soạn đề xuất chờ người bấm (0093, 0228).

## Giấy phép

Lõi Barista mã nguồn mở theo [Apache 2.0](LICENSE) — Copyright 2026 The Barista Authors ([NOTICE](NOTICE)).
Gói nghiệp vụ (pack) có repo và giấy phép riêng của bên làm gói. Tên "Barista": [TRADEMARKS.md](TRADEMARKS.md).
Đóng góp: [CONTRIBUTING.md](CONTRIBUTING.md).
