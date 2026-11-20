-- module: core — lý do: 0029 bước 1 — "chủ tài khoản" là TRÁCH NHIỆM của một thành viên (duyệt thay đổi quyền / vai / luật duyệt), kiêm cùng vai
-- nghiệp vụ (giám đốc vẫn là giám đốc). core.memberships chỉ giữ MỘT vai mỗi người nên không biểu diễn được bằng một vai trong core.roles.
-- CHỈ THÊM: một cột boolean mặc định false — bản cũ chạy song song lúc chuyển không đọc / ghi cột này, dòng nó thêm nhận false ⇒ không cần cửa
-- sổ bảo trì (không vào deployment.json). Ai là chủ tài khoản đầu tiên do admin.install_defaults gán theo quy tắc trong 0029 (seed --upgrade).
ALTER TABLE core.memberships ADD COLUMN owner BOOLEAN NOT NULL DEFAULT false;
COMMENT ON COLUMN core.memberships.owner IS 'Chủ tài khoản công ty (0029): duyệt thay đổi quyền / vai / luật duyệt; luôn còn ít nhất một người đang hoạt động';
-- Vai NHẬP MÔN (0029, review 08/11): mời tài khoản MỚI với vai này chạy thẳng; vai khác / trưởng đội / quản trị phải qua chủ tài khoản. Gói khai
-- mặc định (Role(entry=True)); công ty sửa ở Vai trò (đổi cũng phải duyệt). CHỈ THÊM: bản cũ không đọc cột này.
ALTER TABLE core.roles ADD COLUMN entry BOOLEAN NOT NULL DEFAULT false;
COMMENT ON COLUMN core.roles.entry IS 'Vai nhập môn (0029): mời người mới với vai này không cần chủ tài khoản duyệt';
