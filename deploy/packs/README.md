# deploy/packs — gói ngoài lõi đi kèm một bản phát hành (0155)

Trong repo lõi thư mục này CHỈ có file này. Lúc phát hành, `deploy/release.sh` (qua `deploy/packs.py`) đặt vào đây bản phân phối của từng
gói mà file bản cài khai (`"packs": {"<khoá>": "v<X.Y.Z>"}` — `barista-ops/instances/<tên>.json`), lấy bằng `git archive` đúng tag
`<khoá>/v<X.Y.Z>` của repo `barista-packs`, cùng `manifest.json`. `deploy/api.Dockerfile` cài mọi gói ở đây vào ảnh api; `deploy/ops/deploy.py`
trên VPS dừng nếu bộ gói nhận được khác `"packs"` của file bản cài trên máy. Không commit gì khác vào thư mục này (`.gitignore`).

Ngoài ra `bundle` ghi `backend/src/barista/pack_catalog.json` (danh mục CHỮ của mọi gói trong repo gói, kể cả gói bản cài không cài — Store hiện "Chưa có trên bản cài", 0163);
lõi chỉ đọc file này, không nêu tên gói nào.
