# Runbook 02 · Kiểm tra trước khi xong / commit

```bash
cd backend && uv run ruff check src tests && uv run pyright && uv run pytest -q     # Postgres thật, ~4 giây
cd ../macos && swift build && swift run BaristaDesk --check-contract                   # khi đổi output app đọc (cần backend chạy)
```

Migration theo GÓI (0130), đọc đúng thứ tự chạy: gói theo thứ tự nạp (`kernel/loader.py`: nền → năng lực → ngành, gói cần trước), trong gói
theo tên. Gói nền: `backend/src/barista/migrations/base/000_baseline/NN_<module>.sql` (bản gốc — schema cuối của chuỗi 001–186 cũ, KHÔNG sửa;
lịch sử: `docs/migrations-history.md`) rồi `base/NNN_<tên>.sql`; gói khác: `migrations/<gói>/NNN_<tên>.sql` (dòng đầu `-- module: <khoá>`).
Mã = `<gói>/<tên>`; runner + `pending_plan` chặn trùng số / tới muộn TRONG một gói, DB dựng trước bản gốc (0085) — lỗi ở máy dev: `seed --reset`.

Đo độ trễ trợ lý theo từng sự kiện: gửi một câu tới `POST /v1/assistant/chat/stream` và in thời điểm mỗi dòng NDJSON
(bước / thẻ / chữ đầu / xong). Thời gian thường nằm ở số lượt gọi model — xem quyết định 0012 trước khi tối ưu chỗ khác.
