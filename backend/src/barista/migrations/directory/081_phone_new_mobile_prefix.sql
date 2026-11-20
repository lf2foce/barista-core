-- module: parties — 0197: SĐT là khoá gộp người. ``parse_phone`` giờ đổi đầu số di động cũ 11 chữ số (0162…, 0120…) sang đầu số mới 10 chữ số (Bộ
-- TT&TT 2018) ⇒ chuẩn hoá lại định danh ĐÃ LƯU theo cùng luật, nếu không số cũ đã lưu và số mới nhập sau thành hai người. Chỉ đổi dòng mà số mới
-- CHƯA thuộc định danh khác trong cùng công ty; dòng đụng nhau giữ nguyên + báo (gộp hai người là việc của người, không phải migration).
-- Chỉ UPDATE giá trị theo luật mà mã mới cũng sinh ra ⇒ bản cũ chạy song song không vỡ ⇒ không cần dừng dịch vụ.
SET LOCAL lock_timeout = '10s';

CREATE TEMP TABLE _phone_prefix (old text PRIMARY KEY, new text NOT NULL) ON COMMIT DROP;
INSERT INTO _phone_prefix VALUES ('0162', '032'), ('0163', '033'), ('0164', '034'), ('0165', '035'), ('0166', '036'), ('0167', '037'), ('0168', '038'),
    ('0169', '039'), ('0120', '070'), ('0121', '079'), ('0122', '077'), ('0126', '076'), ('0128', '078'), ('0123', '083'), ('0124', '084'), ('0125', '085'),
    ('0127', '081'), ('0129', '082'), ('0186', '056'), ('0188', '058'), ('0199', '059');

DO $$
DECLARE changed int; clashed int;
BEGIN
    UPDATE parties.identifiers i SET value = p.new || substr(i.value, 5)
    FROM _phone_prefix p
    WHERE i.kind = 'phone' AND i.value ~ '^01[0-9]{9}$' AND left(i.value, 4) = p.old
      AND NOT EXISTS (SELECT 1 FROM parties.identifiers d
                      WHERE d.tenant_id = i.tenant_id AND d.kind = 'phone' AND d.value = p.new || substr(i.value, 5));
    GET DIAGNOSTICS changed = ROW_COUNT;
    SELECT count(*) INTO clashed FROM parties.identifiers i JOIN _phone_prefix p ON left(i.value, 4) = p.old
    WHERE i.kind = 'phone' AND i.value ~ '^01[0-9]{9}$';
    RAISE NOTICE 'parties.identifiers: % SĐT đổi sang đầu số mới; % SĐT giữ nguyên vì số mới đã thuộc người khác (cần người xem để gộp)', changed, clashed;
END $$;
