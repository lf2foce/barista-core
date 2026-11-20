# Runbook 03 · Thêm một thao tác (lệnh / truy vấn)

1. Trong module: `@m.command(...)` hoặc `@m.query(...)` với **tài nguyên + hành động** đúng ma trận quyền.
2. Chọn có chủ đích:
   - `agent=` — `propose` (mặc định lệnh ghi: thẻ đề xuất đã chạy thử), `allow` (ghi rủi ro thấp, vd ghi chú), `deny`
     (duyệt, ban hành, đổi quyền, dữ liệu nhị phân).
   - `min_scope="all"` cho báo cáo toàn công ty; `internal=True` cho hợp đồng hẹp giữa module.
   - `shows=("truy_vấn.xem", "khoá")` nếu kết quả nên hiện thành thẻ ngay sau khi chạy.
   - `importable=True` nếu nhập hàng loạt từ Excel được.
3. Input là model Pydantic dùng `field(...)` (nhãn tiếng Việt, loại ô, lựa chọn, ví dụ) — form, mẫu Excel, schema trợ lý tự sinh.
4. SQL chỉ chạm schema của module (+ `core.users/teams/team_members/memberships`). Cần module khác ⇒ `ctx.call` / `ctx.call_system`.
   Module khác thuộc GÓI không bắt buộc với gói của mình (0045) ⇒ `ctx.call_optional` / `call_system_optional` / `call_as_optional`
   (gói tắt ⇒ None, phải xử lý), hoặc khai `needs=("gói",)` trên thao tác nếu thiếu gói đó thì thao tác vô nghĩa.
   Module mới: `Module("khoa", "Tên", pack="...")`; đối tượng cho quy trình / trao đổi ⇒ `@m.subject` + hàm thấy-được ở DB
   (`core.subject_kinds`, 0046).
5. Bảng mới ⇒ `core.secure_table` / `secure_child` / `secure_tenant` trong migration MỚI. `test_meta` sẽ chặn nếu quên.
   `migrations/base/000_baseline/NN_<module>.sql` là BẢN GỐC (schema cuối của chuỗi cũ 001–186 — KHÔNG thêm / sửa ở đây); migration mới là
   `migrations/<gói của module>/NNN_<tên>.sql`, số lớn hơn mọi file của gói đó, dòng đầu `-- module: <khoá>` và chỉ đụng schema của module đó
   (0085, 0130). Số chỉ cần không trùng TRONG gói — hỏi phiên đang làm song song trên cùng gói trước khi lấy số.
6. Màn mới ⇒ `workspaces.py` (truy vấn + nút + nút dòng + mở chi tiết; `max_scope` nếu là màn cá nhân).
7. Test: quyền (ai được / không được, kể cả trợ lý), và luồng nghiệp vụ trên Postgres thật.
