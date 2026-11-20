-- module: crm — 0197: cùng luật với directory/081 cho SĐT của khách (``crm.customers.phone`` là khoá gộp khách, UNIQUE theo công ty). Chỉ đổi dòng mà
-- số mới chưa thuộc khách khác trong cùng công ty; dòng đụng nhau giữ nguyên + báo — không tự gộp hai khách. Gói crm cần gói directory nên chạy
-- sau directory/081. Chỉ UPDATE theo luật mà mã mới cũng sinh ra ⇒ không cần dừng dịch vụ.
SET LOCAL lock_timeout = '10s';

CREATE TEMP TABLE _phone_prefix (old text PRIMARY KEY, new text NOT NULL) ON COMMIT DROP;
INSERT INTO _phone_prefix VALUES ('0162', '032'), ('0163', '033'), ('0164', '034'), ('0165', '035'), ('0166', '036'), ('0167', '037'), ('0168', '038'),
    ('0169', '039'), ('0120', '070'), ('0121', '079'), ('0122', '077'), ('0126', '076'), ('0128', '078'), ('0123', '083'), ('0124', '084'), ('0125', '085'),
    ('0127', '081'), ('0129', '082'), ('0186', '056'), ('0188', '058'), ('0199', '059');

DO $$
DECLARE changed int; clashed int;
BEGIN
    UPDATE crm.customers c SET phone = p.new || substr(c.phone, 5)
    FROM _phone_prefix p
    WHERE c.phone ~ '^01[0-9]{9}$' AND left(c.phone, 4) = p.old
      AND NOT EXISTS (SELECT 1 FROM crm.customers d WHERE d.tenant_id = c.tenant_id AND d.phone = p.new || substr(c.phone, 5));
    GET DIAGNOSTICS changed = ROW_COUNT;
    SELECT count(*) INTO clashed FROM crm.customers c JOIN _phone_prefix p ON left(c.phone, 4) = p.old WHERE c.phone ~ '^01[0-9]{9}$';
    RAISE NOTICE 'crm.customers: % SĐT đổi sang đầu số mới; % SĐT giữ nguyên vì số mới đã thuộc khách khác (cần người xem để gộp)', changed, clashed;
END $$;
