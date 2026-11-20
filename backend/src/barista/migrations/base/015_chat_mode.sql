-- module: core
-- 015 (0098): CHẾ ĐỘ của cuộc trò chuyện — "assistant" (trợ lý cá nhân, như cũ) hoặc "builder" (TRÌNH TẠO APP: lời dặn + bộ công cụ riêng,
-- kiểu Claude Design). Cuộc trò chuyện trình tạo không lẫn vào lịch sử của trợ lý cá nhân. Chỉ THÊM.
ALTER TABLE core.chat_threads ADD COLUMN mode TEXT NOT NULL DEFAULT 'assistant' CHECK (mode IN ('assistant', 'builder'));
