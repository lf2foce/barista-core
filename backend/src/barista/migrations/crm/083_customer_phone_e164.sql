-- module: crm, parties — lý do: khoá SĐT của khách và định danh người phải đổi CÙNG lượt (chuỗi gói chạy directory trước crm: lần nâng cấp từ bản rất cũ,
-- định danh do crm/024 sinh ra SAU directory/082 nên phải đổi lại ở đây; đổi là bất biến — chạy lại không đổi gì). 0199: SĐT lưu dạng E.164 cho MỌI số (``parse_phone`` trả ``+84…`` thay cho ``0…``). SĐT là khoá gộp khách (UNIQUE(tenant_id, phone)) ⇒ đổi
-- khoá đã lưu theo đúng luật mã mới sinh ra: ``0`` + 9–10 chữ số → ``+84`` + 9–10 chữ số. Dòng mà khoá mới đã thuộc khách khác giữ nguyên + báo (gộp hai
-- người là việc của người, không phải migration); còn dòng như vậy thì ràng buộc mới thêm NOT VALID (dòng mới / sửa vẫn bị kiểm) và cảnh báo.
-- CẦN DỪNG DỊCH VỤ (deployment.json): bản cũ chạy song song sẽ ghi / tra khoá ``0…`` ⇒ tạo khách trùng dạng cũ hoặc vỡ ràng buộc mới.
SET LOCAL lock_timeout = '10s';

ALTER TABLE crm.customers DROP CONSTRAINT customers_phone_check;

DO $$
DECLARE changed int;
BEGIN
    UPDATE parties.identifiers i SET value = '+84' || substr(i.value, 2)
    WHERE i.kind = 'phone' AND i.value ~ '^0[0-9]{9,10}$'
      AND NOT EXISTS (SELECT 1 FROM parties.identifiers d
                      WHERE d.tenant_id = i.tenant_id AND d.kind = 'phone' AND d.value = '+84' || substr(i.value, 2));
    GET DIAGNOSTICS changed = ROW_COUNT;
    RAISE NOTICE 'parties.identifiers (lượt crm): % SĐT đổi sang E.164', changed;
END $$;

DO $$
DECLARE changed int; clashed int;
BEGIN
    UPDATE crm.customers c SET phone = '+84' || substr(c.phone, 2)
    WHERE c.phone ~ '^0[0-9]{9,10}$'
      AND NOT EXISTS (SELECT 1 FROM crm.customers d WHERE d.tenant_id = c.tenant_id AND d.phone = '+84' || substr(c.phone, 2));
    GET DIAGNOSTICS changed = ROW_COUNT;
    SELECT count(*) INTO clashed FROM crm.customers WHERE phone !~ '^\+[1-9][0-9]{7,14}$';
    RAISE NOTICE 'crm.customers: % SĐT đổi sang E.164', changed;
    IF clashed = 0 THEN
        ALTER TABLE crm.customers ADD CONSTRAINT customers_phone_check CHECK (phone ~ '^\+[1-9][0-9]{7,14}$');
    ELSE
        ALTER TABLE crm.customers ADD CONSTRAINT customers_phone_check CHECK (phone ~ '^\+[1-9][0-9]{7,14}$') NOT VALID;
        RAISE WARNING 'crm.customers: % SĐT giữ dạng cũ vì khoá E.164 đã thuộc khách khác — cần người xem để gộp, rồi VALIDATE CONSTRAINT customers_phone_check', clashed;
    END IF;
END $$;
