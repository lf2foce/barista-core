-- module: crm, leadflow, flow, core — lý do: đổi tên cột "thứ khách quan tâm" (project_* → offering_*, 0110 "Còn mở", bất biến 11); khoá chữ cũ còn nằm trong dữ liệu đã lưu của flow (điều kiện tự động hoá) và core (sự kiện chờ giao, đề xuất / phiếu chờ duyệt) nên đổi cùng đợt, không để chúng lặng lẽ không khớp nữa. Tách từ crm/038_rename_offering_columns.sql (0130, ghi chú 08/11; cột của Marketing ở marketing/038_campaign_offering_columns.sql)
-- 0110 / 0124: cột ở crm, leadflow, mkt nói theo khái niệm nghiệp vụ (offering = thứ khách quan tâm và công ty bán), không theo ngành đầu tiên.
-- `catalog.projects`, `deals.project_*`, `commission` là dự án bất động sản thật — GIỮ tên. Cột `offering_id` vẫn trỏ catalog.projects như trước (gói ngành khai điểm cắm crm.offering).
-- Bảo trì (deployment.json): bản cũ chạy song song sẽ vỡ ngay vì cột đổi tên. Đã kiểm pg_depend / pg_policies / pg_views / hàm / trigger: không đối tượng nào ngoài chỉ mục và ràng buộc dưới đây nhắc cột cũ.

ALTER TABLE crm.leads RENAME COLUMN project_id TO offering_id;
ALTER TABLE crm.leads RENAME COLUMN project_code TO offering_code;
ALTER INDEX crm.uq_leads_customer_project RENAME TO uq_leads_customer_offering;
ALTER TABLE crm.leads RENAME CONSTRAINT leads_project_code_not_null TO leads_offering_code_not_null;

ALTER TABLE leadflow.rules RENAME COLUMN project_code TO offering_code;
ALTER TABLE leadflow.rules RENAME CONSTRAINT rules_project_code_not_null TO rules_offering_code_not_null;
ALTER TABLE leadflow.lead_state RENAME COLUMN project_code TO offering_code;
ALTER TABLE leadflow.lead_state RENAME CONSTRAINT lead_state_project_code_not_null TO lead_state_offering_code_not_null;

-- Tự động hoá đã lưu: điều kiện theo trường của cò `crm.lead_created` ("Dự án quan tâm = HSR" lưu {field: project_code}) — không đổi thì luật im lặng không khớp nữa.
UPDATE flow.automations a SET conditions = (
        SELECT jsonb_agg(CASE WHEN c->>'field' = 'project_code' THEN jsonb_set(c, '{field}', '"offering_code"') ELSE c END ORDER BY n)
          FROM jsonb_array_elements(a.conditions) WITH ORDINALITY AS t(c, n))
 WHERE a.trigger = 'crm.lead_created' AND a.conditions @> '[{"field": "project_code"}]';

-- Sự kiện lead mới: trường trong nội dung đổi tên theo cò cho MỌI sự kiện loại này (bảng không đánh dấu đã giao nên không lọc được "đang chờ"); nội dung chỉ đổi khoá, không đổi ý nghĩa, để luật vừa đổi ở trên khớp cả những sự kiện đang chờ.
UPDATE core.events SET payload = (payload - 'project_code') || jsonb_build_object('offering_code', payload->'project_code')
 WHERE type = 'crm.lead_created' AND payload ? 'project_code';

-- Đề xuất đang mở và phiếu đang chờ duyệt của bốn lệnh đổi khoá đầu vào: duyệt xong chạy lại bằng đúng khoá mới. Bản đã chạy / đã từ chối giữ nguyên (lịch sử).
UPDATE core.proposals SET input = (input - 'project_code') || jsonb_build_object('offering_code', input->'project_code')
 WHERE status = 'open' AND input ? 'project_code' AND command IN ('crm.create_lead', 'leadflow.intake', 'leadflow.set_rule', 'mkt.upsert_campaign');
UPDATE core.approvals SET input = (input - 'project_code') || jsonb_build_object('offering_code', input->'project_code')
 WHERE status = 'pending' AND input ? 'project_code' AND command IN ('crm.create_lead', 'leadflow.intake', 'leadflow.set_rule', 'mkt.upsert_campaign');
