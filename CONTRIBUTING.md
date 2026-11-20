# Đóng góp cho Barista

Cảm ơn bạn muốn góp sức. Lõi Barista phát hành theo giấy phép [Apache 2.0](LICENSE).

## Giấy phép của phần bạn đóng góp

Mã, tài liệu bạn gửi vào repo này được phát hành theo Apache 2.0 (mục 5 của giấy phép). Mỗi commit ký
[Developer Certificate of Origin](https://developercertificate.org/) bằng dòng:

    Signed-off-by: Tên Của Bạn <email@cua-ban>

(`git commit -s` tự thêm). Dòng này xác nhận bạn có quyền gửi phần đó theo giấy phép của dự án.

## Trước khi gửi

- Đọc [CLAUDE.md](CLAUDE.md): các luật bất biến của lõi (một cửa lệnh, quyền ở DB, migration chỉ thêm, gói chỉ dùng `barista.api`).
- Chạy kiểm tra như [docs/runbooks/README.md](docs/runbooks/README.md) hướng dẫn; test phải xanh.
- Một thay đổi lớn (bảng mới, đổi `barista.api`, đổi luật quyền): mở issue mô tả trước khi viết mã.

## Gói (pack) của bạn

Gói nghiệp vụ không cần nằm trong repo này: tạo bằng `scripts/pack.sh new`, tự kiểm bằng `scripts/pack.sh check`
([docs/runbooks/21-goi-cua-doi-tac.md](docs/runbooks/21-goi-cua-doi-tac.md)). Gói của bạn thuộc về bạn và theo giấy phép bạn chọn.

## Báo lỗ hổng bảo mật

Đừng mở issue công khai — dùng mục *Security → Report a vulnerability* của repo trên GitHub.
