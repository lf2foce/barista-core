-- module: parties — 0199: định danh SĐT lưu dạng E.164 (``parse_phone`` trả ``+84…`` thay cho ``0…``) ⇒ đổi giá trị đã lưu theo đúng luật mã mới sinh ra:
-- ``0`` + 9–10 chữ số → ``+84`` + 9–10 chữ số. Dòng mà giá trị mới đã thuộc định danh khác trong cùng công ty giữ nguyên + báo (gộp hai người là việc của
-- người). CẦN DỪNG DỊCH VỤ (deployment.json): bản cũ chạy song song sẽ ghi / tra dạng ``0…`` ⇒ một người thành hai.
SET LOCAL lock_timeout = '10s';

DO $$
DECLARE changed int; clashed int;
BEGIN
    UPDATE parties.identifiers i SET value = '+84' || substr(i.value, 2)
    WHERE i.kind = 'phone' AND i.value ~ '^0[0-9]{9,10}$'
      AND NOT EXISTS (SELECT 1 FROM parties.identifiers d
                      WHERE d.tenant_id = i.tenant_id AND d.kind = 'phone' AND d.value = '+84' || substr(i.value, 2));
    GET DIAGNOSTICS changed = ROW_COUNT;
    SELECT count(*) INTO clashed FROM parties.identifiers WHERE kind = 'phone' AND value ~ '^0[0-9]{9,10}$';
    RAISE NOTICE 'parties.identifiers: % SĐT đổi sang E.164; % SĐT giữ dạng cũ vì giá trị mới đã thuộc người khác (cần người xem để gộp)', changed, clashed;
END $$;
