---
name: barista-pack
description: Create, run locally, and validate an external Barista pack in its own repository using the three-step loop `pack.sh new -> dev -> check`. Use whenever building, testing, or debugging a partner or third-party Barista pack against `barista-core`.
---

# Barista Pack Skill — `new → dev → check`

Hướng dẫn ngắn cho người và agent khi viết một **gói ngoài lõi (pack)** cho Barista trên repo riêng của bạn.

## Đọc nhanh trước khi viết
- Hợp đồng gói (bảng, RLS, quyền, điểm cắm, tiền, màn hình): [`docs/runbooks/12-them-mot-goi.md`](../../../docs/runbooks/12-them-mot-goi.md)
- Quy trình đối tác (tạo, thử, tự kiểm, khai quyền, gửi tag): [`docs/runbooks/21-goi-cua-doi-tac.md`](../../../docs/runbooks/21-goi-cua-doi-tac.md)
- Gói kết nối ra hệ ngoài (CRM / CMS / webhook / kéo theo lịch): [`docs/runbooks/22-goi-ket-noi.md`](../../../docs/runbooks/22-goi-ket-noi.md)

## Quy trình 3 bước (`new → dev → check`)

Chạy từ thư mục gốc của repo lõi `barista-core`:

```bash
# 1. Tạo khuôn gói mới (thêm --connector nếu là gói kết nối hệ ngoài)
bash scripts/pack.sh new <khoa_goi> ../<repo-cua-ban>/<khoa_goi> --title "Tên gói"

# 2. Cài vào môi trường dev, chạy migration trên DB thử riêng (mặc định barista_erp_packdev),
#    bật gói cho công ty mẫu và chạy API (tự nạp lại khi sửa mã gói)
bash scripts/pack.sh dev ../<repo-cua-ban>/<khoa_goi>
#    Chỉ chuẩn bị không mở uvicorn: thêm --no-serve
#    Đổi DB thử: thêm --db barista_erp_dev (chỉ nhận DB dev local, từ chối production)
#    Mở web khớp cổng theo đúng lệnh in ra ở cuối bước 2:
#    cd web && BARISTA_CORE_URL=http://127.0.0.1:8300 pnpm exec next dev -p 3100

# 3. Tự kiểm trên lõi sạch (đọc tĩnh, quyền, ruff, pyright, pytest gói + test cấu trúc lõi)
bash scripts/pack.sh check ../<repo-cua-ban>/<khoa_goi> --worktree
```

## 5 luật cứng (vi phạm ⇒ `pack.sh check` báo lỗi)

1. **Chỉ import `barista.api`:** mã chạy trong `src/` KHÔNG import `barista.kernel.*`, `barista.agent.*`, `os`, `subprocess`, `httpx`, `urllib.request`, kết nối DB riêng hay `eval`/`exec`. Thiếu dịch vụ ⇒ mở issue/PR thêm vào `barista.api`.
2. **Khai đủ quyền trong `pyproject.toml`:** dùng phần nhạy cảm của `barista.api` (`ai`, `network`, `files`, `notify`, `public`, `client_companies`) phải khai ở `[tool.barista] permissions = [...]`.
3. **Mọi bảng phải có RLS:** đặt migration trong `src/<pkg>/migrations/<khoa>/NNN_<ten>.sql` với dòng đầu `-- module: <khoa>`, khoá bảng qua `core.secure_table` / `core.secure_child` / `core.secure_tenant`. Không sửa migration đã chạy.
4. **Không `uv sync` hay `seed --reset` sau khi cài gói thử:** `uv sync` gỡ gói khỏi `backend/.venv`, còn `seed --reset` xoá DB dev. Dùng `pack.sh dev` (hoặc `uv run --no-sync` / `backend/.venv/bin/python`).
5. **Gửi lên bản cài bằng tag có chú thích:** gắn `git tag -a <khoa>/vX.Y.Z -m "..."` khớp `version` trong `pyproject.toml`, chạy lại `bash scripts/pack.sh check <thư mục gói>` (không `--worktree`) rồi gửi đường repo + tag + SHA cho bên vận hành review và ghim.
