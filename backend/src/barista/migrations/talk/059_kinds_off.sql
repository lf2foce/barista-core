-- module: talk — lý do: 058 (ADR 0146, perf doc §11) — chính sách talk.* gọi core.subject_visible cần vế InitPlan "gói đang tắt" (core.kinds_off()); bản cài mới
-- chạy migration nền trước nên chính sách này sinh SAU 058. Chỉ đổi chữ chính sách, không đổi nghĩa ⇒ không cần bảo trì. Chạy lại không sao.
SET LOCAL lock_timeout = '10s';
SELECT core.kinds_off_policies();
