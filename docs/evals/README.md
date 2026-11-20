# Lịch sử eval trợ lý (JSON trong repo)

Mỗi lần chạy `backend/scripts/eval_*.py --save [--label <cấu hình>]` ghi một file `<ngày>-<bộ>[-<nhãn>].json` ở đây: `at`, `commit`, `core`, `set`,
`label`, `runs`, `stats` (đạt %, vượt quyền, số lệch, giây, token, USD / lượt) và `records` từng lượt (câu, vai, đạt / trượt, công cụ đã gọi, số mong
đợi vs số trên thẻ, lệnh đã chạy thẳng). Xem xu hướng và hồi quy: `uv run python scripts/eval_trend.py`.

Vì sao là file, không phải bảng trong sản phẩm (0144 mục 7, thống nhất 10/11): eval là công cụ dev, diff được trong git, không có câu hỏi RLS / migration;
khách không xem. Eval tốn tiền model nên **chỉ chạy khi được bảo** (đổi prompt / model / bộ công cụ, hoặc định kỳ) — không chạy trong CI, không chạy mỗi lần test.

Cách chấm (xem docstring `eval_assistant.py`): công cụ phải gọi (`must` / `all_of`), đề xuất phải có (`proposal`), công cụ bị cấm không được chạy thành công
(`forbid` ⇒ VƯỢT QUYỀN), sự thật phải nói ra (`facts`), **số trên thẻ phải khớp truy vấn thật chạy bằng quyền người hỏi** (`expect` — không so chuỗi con),
lệnh đã nới phải chạy thẳng (`autorun`). Bộ `autonomy` chạy trên công ty đã bật công tắc (preset Toàn quyền của demo).

**Ghi chú bộ câu (09/11):** `Case.proposal_or_run` nhận cả thẻ đề xuất lẫn lệnh chạy thẳng thành công — cho lệnh mà công ty đã nới (preset Toàn quyền) lẫn công ty
chưa nới; không nới `forbid` / vượt quyền. Lịch sử có thêm `đọc bộ đệm %` (token đọc từ bộ đệm prompt / token vào, từ `core.ai_usage.cache_read_tokens` — chỉ có khi
nhà cung cấp báo; file cũ không có khoá này thì bỏ chỉ số, không bịa). Nhãn `…-cases2` = bộ câu đã sửa S3 / T3 / G1 (base) và A1 (autonomy): số của nhãn cũ và mới
KHÔNG so thẳng được ở các câu đó.
