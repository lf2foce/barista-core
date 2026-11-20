-- Sinh dữ liệu khối lượng quy mô khách đầu tiên (GIẢ: tên ghép ngẫu nhiên, SĐT đầu 010). Chạy bằng barista_owner trên DB đo riêng (xem README.md cùng thư mục).
\set ON_ERROR_STOP 1
SELECT setseed(0.37);
-- N11b (ADR 0146): :'tid'::uuid đọc claims do lõi ký, không còn đọc set_config('app.tenant_id') — script owner lấy mã công ty vào biến psql.
SELECT id AS tid FROM core.tenants ORDER BY created_at LIMIT 1 \gset
SET synchronous_commit = off;

-- ---------- tên giả ----------
CREATE TEMP TABLE fam(i int, v text);  INSERT INTO fam SELECT row_number() over (), v FROM unnest(ARRAY['Nguyễn','Trần','Lê','Phạm','Hoàng','Vũ','Đặng','Bùi','Đỗ','Hồ','Ngô','Dương','Lý','Mai','Trịnh','Phan','Võ','Tạ','Lương','Châu']) v;
CREATE TEMP TABLE mid(i int, v text);  INSERT INTO mid SELECT row_number() over (), v FROM unnest(ARRAY['Văn','Thị','Minh','Thu','Quốc','Đức','Ngọc','Hoàng','Thanh','Gia','Bảo','Mỹ','Hữu','Phương']) v;
CREATE TEMP TABLE giv(i int, v text);  INSERT INTO giv SELECT row_number() over (), v FROM unnest(ARRAY['An','Bình','Châu','Dung','Giang','Hà','Hải','Hạnh','Hiếu','Hoa','Hùng','Khánh','Lan','Linh','Long','Mai','Nam','Nga','Nhung','Phúc','Quân','Sơn','Tâm','Thảo','Thư','Trang','Tuấn','Vinh','Yến','Khoa']) v;
CREATE FUNCTION pg_temp.fake_name(n int) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT (SELECT v FROM fam WHERE i = 1 + n % 20) || ' ' || (SELECT v FROM mid WHERE i = 1 + (n / 20) % 14) || ' ' || (SELECT v FROM giv WHERE i = 1 + (n / 280) % 30) $$;

-- ---------- đội + người (15 đội KD, ~150 người) ----------
INSERT INTO core.teams (tenant_id, code, name, kind, parent_id)
SELECT :'tid'::uuid, 'KD-' || chr(67 + g), 'Team Kinh doanh ' || chr(67 + g), 'sales',
       (SELECT id FROM core.teams WHERE code = CASE WHEN g % 2 = 0 THEN 'KHOI-1' ELSE 'KHOI-2' END)
FROM generate_series(1, 12) g;   -- KD-D … KD-O

CREATE TEMP TABLE new_people AS
SELECT t.code AS team, k, CASE WHEN k = 0 THEN 'team_lead' ELSE 'sale' END AS role, (k = 0) AS lead,
       lower(replace(t.code, '-', '')) || '_' || k AS local
FROM core.teams t CROSS JOIN generate_series(0, 8) k WHERE t.code ~ '^KD-[D-O]$';
-- đội cũ KD-A/B/C thêm sale cho đủ 9 người / đội
INSERT INTO new_people SELECT t, k, 'sale', false, lower(replace(t, '-', '')) || '_x' || k FROM unnest(ARRAY['KD-A','KD-B','KD-C']) t, generate_series(1, 6) k;
INSERT INTO new_people VALUES ('KT', 1, 'accountant', false, 'ketoan2'), ('KT', 2, 'accountant', false, 'ketoan3'),
                              ('KHOI-1', 1, 'unit_lead', false, 'qlvung');   -- người phạm vi "unit": tham gia 3 đội

INSERT INTO core.users (email, name)
SELECT local || '@sanmau.demo', pg_temp.fake_name(1000 + row_number() over (order by local)::int) FROM new_people;
INSERT INTO core.roles (tenant_id, key, label) VALUES (:'tid'::uuid, 'unit_lead', 'Quản lý vùng (đo hiệu năng)');
INSERT INTO core.grants (tenant_id, role, resource, scope, actions)
SELECT tenant_id, 'unit_lead', resource, CASE WHEN scope = 'team' THEN 'unit' ELSE scope END, actions FROM core.grants WHERE role = 'team_lead';
INSERT INTO core.memberships (tenant_id, user_id, role)
SELECT :'tid'::uuid, u.id, np.role FROM new_people np JOIN core.users u ON u.email = np.local || '@sanmau.demo';
INSERT INTO core.team_members (tenant_id, user_id, team_id, is_lead, is_home)
SELECT :'tid'::uuid, u.id, t.id, np.lead, true FROM new_people np JOIN core.users u ON u.email = np.local || '@sanmau.demo' JOIN core.teams t ON t.code = np.team;
INSERT INTO core.team_members (tenant_id, user_id, team_id, is_lead, is_home)
SELECT :'tid'::uuid, u.id, t.id, false, false FROM core.users u, core.teams t WHERE u.email = 'qlvung@sanmau.demo' AND t.code IN ('KD-A', 'KD-B', 'KD-D');

-- sale = người vai sale ở đội KD (người nhận lead / giao dịch)
CREATE TEMP TABLE sales AS
SELECT row_number() over (order by u.id) AS n, u.id AS uid, tm.team_id AS tid FROM core.users u
JOIN core.memberships m ON m.user_id = u.id AND m.role = 'sale' JOIN core.team_members tm ON tm.user_id = u.id AND tm.is_home
JOIN core.teams t ON t.id = tm.team_id AND t.code LIKE 'KD-%';
SELECT count(*) AS sales FROM sales;
CREATE TEMP TABLE nsales AS SELECT count(*)::int AS c FROM sales;

-- ---------- dự án + bảng hàng (90 dự án, ~5.500 căn) ----------
INSERT INTO catalog.projects (tenant_id, code, name, developer, location, fee_rate_bps, status, entity_id)
SELECT :'tid'::uuid, 'P' || lpad(g::text, 3, '0'), 'Dự án mẫu ' || g, 'CĐT Mẫu ' || (g % 12), 'Khu ' || (g % 9), 200 + (g % 5) * 25,
       CASE WHEN g % 7 = 0 THEN 'closed' ELSE 'selling' END, (SELECT entity_id FROM catalog.projects WHERE code = 'HSR')
FROM generate_series(1, 90) g;
INSERT INTO catalog.units (tenant_id, project_id, code, building, floor, axis, bedrooms, area_m2, direction, view, price_vnd, status)
SELECT :'tid'::uuid, p.id, chr(65 + (k % 3)) || lpad((k / 3 % 30 + 1)::text, 2, '0') || lpad((k % 8 + 1)::text, 2, '0'),
       chr(65 + (k % 3)), k / 3 % 30 + 1, lpad((k % 8 + 1)::text, 2, '0'), 1 + k % 3, 48 + (k % 5) * 15, 'Đông Nam', 'Hồ',
       (2500 + (k * 37) % 4000)::bigint * 1000000, 'available'
FROM catalog.projects p CROSS JOIN generate_series(0, 59) k WHERE p.code LIKE 'P%';

-- ---------- khách (19.000) + lead (30.000) ----------
INSERT INTO crm.customers (tenant_id, name, phone, owner_id, team_id, created_at)
SELECT :'tid'::uuid, pg_temp.fake_name(g), '010' || lpad(g::text, 8, '0'), s.uid, s.tid, now() - (g % 900) * interval '1 day'
FROM generate_series(1, 19000) g JOIN sales s ON s.n = 1 + g % (SELECT c FROM nsales);

CREATE TEMP TABLE cust AS SELECT row_number() over (order by created_at, id) AS n, id, owner_id, team_id FROM crm.customers WHERE phone LIKE '010________' AND length(phone) = 11;
CREATE TEMP TABLE proj AS SELECT row_number() over (order by code) AS n, id, code FROM catalog.projects WHERE code LIKE 'P%';

INSERT INTO crm.leads (tenant_id, customer_id, offering_code, stage, source, interest, owner_id, team_id, next_follow_up, last_activity_at, created_at, updated_at, captured_at, lost_reason)
SELECT :'tid'::uuid, c.id, p.code,
       CASE WHEN r < 0.55 THEN 'lead' WHEN r < 0.78 THEN 'prospect' WHEN r < 0.88 THEN 'deal' ELSE 'lost' END,
       (ARRAY['ads','telesales','self','referral','event','import'])[1 + g % 6],
       jsonb_build_object('budget_vnd', (3 + g % 8)::bigint * 1000000000, 'bedrooms', 1 + g % 3),
       CASE WHEN g % 50 = 0 THEN NULL ELSE c.owner_id END, CASE WHEN g % 50 = 0 THEN NULL ELSE c.team_id END,
       CASE WHEN r < 0.78 THEN core.today() + ((g % 61) - 30) END,
       now() - (g % 120) * interval '1 day', now() - (g % 900) * interval '1 day', now() - (g % 100) * interval '1 day',
       now() - (g % 900) * interval '1 day', CASE WHEN r >= 0.88 THEN 'Không đủ tài chính' ELSE '' END
FROM (SELECT g, random() AS r FROM generate_series(1, 30000) g) x
JOIN cust c ON c.n = 1 + (g - 1) % 19000
JOIN proj p ON p.n = 1 + ((g - 1) / 19000 * 37 + g) % 90;

CREATE TEMP TABLE ld AS SELECT row_number() over (order by l.id) AS n, l.id, l.owner_id, l.team_id, l.stage, l.customer_id, l.offering_code, l.created_at FROM crm.leads l JOIN cust c ON c.id = l.customer_id;
CREATE INDEX ON ld (n);

INSERT INTO crm.activities (tenant_id, lead_id, kind, note, by_user, at, outcome)
SELECT :'tid'::uuid, ld.id, (ARRAY['call','zalo','note','meet','send_profile'])[1 + (ld.n + k) % 5], 'Chăm sóc lần ' || k, ld.owner_id,
       ld.created_at + k * interval '3 day', CASE WHEN (ld.n + k) % 5 = 0 THEN (ARRAY['connected','no_answer','busy'])[1 + k % 3] ELSE '' END
FROM ld CROSS JOIN generate_series(1, 3) k;
INSERT INTO crm.stage_events (tenant_id, lead_id, kind, from_stage, to_stage, at, by_user)
SELECT :'tid'::uuid, id, 'created', NULL, 'lead', created_at, owner_id FROM ld
UNION ALL SELECT :'tid'::uuid, id, 'assigned', NULL, NULL, created_at + interval '5 min', owner_id FROM ld
UNION ALL SELECT :'tid'::uuid, id, 'stage', 'lead', stage, created_at + interval '10 day', owner_id FROM ld WHERE stage <> 'lead';
INSERT INTO crm.appointments (tenant_id, lead_id, kind, at, place, status, created_by, closed_at)
SELECT :'tid'::uuid, id, (ARRAY['meet','visit','call'])[1 + n % 3], now() + ((n % 40) - 20) * interval '1 day' + (n % 10) * interval '1 hour', 'Văn phòng',
       CASE WHEN n % 40 < 20 THEN 'done' ELSE 'scheduled' END, owner_id, CASE WHEN n % 40 < 20 THEN now() - interval '1 day' END
FROM ld WHERE n % 5 = 0;
INSERT INTO leadflow.lead_state (tenant_id, lead_id, offering_code, status, holder_id, holders, assigned_at, first_call_due, first_call_at, first_call_ok, source, queue_reason)
SELECT :'tid'::uuid, id, offering_code,
       CASE WHEN owner_id IS NULL THEN 'queued' WHEN n % 40 = 1 THEN 'waiting' WHEN stage IN ('lead','prospect') THEN 'contacted' ELSE 'closed' END,
       owner_id, CASE WHEN owner_id IS NULL THEN '{}'::uuid[] ELSE ARRAY[owner_id] END, created_at, created_at + interval '15 min',
       CASE WHEN n % 40 = 1 OR owner_id IS NULL THEN NULL ELSE created_at + interval '10 min' END,
       CASE WHEN n % 40 = 1 OR owner_id IS NULL THEN NULL ELSE true END, 'ads', CASE WHEN owner_id IS NULL THEN 'no_receiver' ELSE '' END
FROM ld;
INSERT INTO leadflow.sla_events (tenant_id, lead_id, kind, user_id, at)
SELECT :'tid'::uuid, id, CASE WHEN n % 9 = 0 THEN 'first_call_breached' ELSE 'first_call_met' END, owner_id, created_at + interval '10 min' FROM ld;

-- ---------- giao dịch (~3.000, 6 đợt đóng tiền) ----------
CREATE TEMP TABLE unit_pick AS SELECT row_number() over (order by u.id) AS n, u.id, u.code, u.price_vnd, p.id AS pid, p.code AS pcode, p.fee_rate_bps
FROM catalog.units u JOIN catalog.projects p ON p.id = u.project_id WHERE p.code LIKE 'P%';
INSERT INTO deals.deals (tenant_id, code, project_id, project_code, unit_id, unit_code, customer_id, customer_name, lead_id, sale_user_id, team_id,
                         price_vnd, fee_rate_bps, fee_vnd, status, reserved_on, signed_on, signed_at, created_by, created_at, updated_at)
SELECT :'tid'::uuid, 'GD-' || u.pcode || '-' || u.code || '-' || x.n, u.pid, u.pcode, u.id, u.code, x.customer_id, c.name, x.id,
       coalesce(x.owner_id, (SELECT uid FROM sales WHERE n = 1)), x.team_id,
       u.price_vnd, u.fee_rate_bps, u.price_vnd * u.fee_rate_bps / 10000, x.st,
       (x.created_at + interval '20 day')::date,
       CASE WHEN x.st = 'signed' THEN (x.created_at + interval '30 day')::date END,
       CASE WHEN x.st = 'signed' THEN x.created_at + interval '30 day' END, x.owner_id, x.created_at + interval '20 day', now()
FROM (SELECT ld.*, row_number() over (order by ld.n) AS k,
             CASE WHEN ld.n % 20 < 14 THEN 'signed' WHEN ld.n % 20 < 17 THEN 'deposited' WHEN ld.n % 20 < 19 THEN 'reserved' ELSE 'cancelled' END AS st
      FROM ld WHERE stage = 'deal') x
JOIN unit_pick u ON u.n = x.k JOIN crm.customers c ON c.id = x.customer_id;
UPDATE catalog.units u SET status = 'sold' FROM deals.deals d WHERE d.unit_id = u.id AND d.status = 'signed';
CREATE TEMP TABLE dl AS SELECT row_number() over (order by id) AS n, * FROM deals.deals WHERE code ~ '^GD-P';
INSERT INTO deals.installments (tenant_id, deal_id, seq, label, due_on, amount_vnd, paid_vnd, paid_on)
SELECT :'tid'::uuid, d.id, k, 'Đợt ' || k, d.reserved_on + (k - 1) * 90, d.price_vnd * (ARRAY[5,25,20,20,25,5])[k] / 100,
       CASE WHEN d.reserved_on + (k - 1) * 90 < core.today() AND d.status = 'signed' THEN d.price_vnd * (ARRAY[5,25,20,20,25,5])[k] / 100 ELSE 0 END,
       CASE WHEN d.reserved_on + (k - 1) * 90 < core.today() AND d.status = 'signed' THEN d.reserved_on + (k - 1) * 90 END
FROM dl d CROSS JOIN generate_series(1, 6) k;
INSERT INTO deals.deal_events (tenant_id, deal_id, kind, at, by_user)
SELECT :'tid'::uuid, id, 'reserved', created_at, sale_user_id FROM dl
UNION ALL SELECT :'tid'::uuid, id, 'deposited', created_at + interval '3 day', sale_user_id FROM dl WHERE status IN ('signed','deposited')
UNION ALL SELECT :'tid'::uuid, id, 'signed', signed_at, sale_user_id FROM dl WHERE status = 'signed';

-- ---------- hoa hồng: accrual + payee cho giao dịch đã ký ----------
INSERT INTO commission.accruals (tenant_id, deal_id, deal_code, sale_user_id, team_id, plan_id, base_vnd, rate_bps, amount_vnd, earned_vnd, detail, status, created_at, earned_at, policy_snapshot)
SELECT :'tid'::uuid, d.id, d.code, d.sale_user_id, d.team_id, (SELECT id FROM commission.plans LIMIT 1), d.price_vnd, 200, d.price_vnd * 200 / 10000,
       CASE WHEN d.n % 3 = 0 THEN d.price_vnd * 200 / 10000 ELSE 0 END, '{"base":"deal_value"}',
       CASE WHEN d.n % 3 = 0 THEN 'earned' ELSE 'pending' END, d.signed_at, CASE WHEN d.n % 3 = 0 THEN d.signed_at + interval '40 day' END,
       (SELECT policy_snapshot FROM commission.accruals LIMIT 1)
FROM dl d WHERE d.status = 'signed';
ALTER TABLE commission.payees DISABLE TRIGGER payee_protected;
INSERT INTO commission.payees (tenant_id, accrual_id, party_id, user_id, team_id, role, display_name, deal_code, basis_kind, base_vnd, rate_bps, gross_vnd, earned_vnd, is_primary, policy_snapshot, status, created_at)
SELECT :'tid'::uuid, a.id, p.id, a.sale_user_id, a.team_id, 'sale', p.display_name, a.deal_code, 'deal_value', a.base_vnd, a.rate_bps, a.amount_vnd, a.earned_vnd, true,
       a.policy_snapshot, a.status, a.created_at
FROM commission.accruals a JOIN parties.parties p ON p.user_id = a.sale_user_id WHERE a.deal_code ~ '^GD-P';
ALTER TABLE commission.payees ENABLE TRIGGER payee_protected;

-- ---------- sổ cái: ví từng sale (payable / advance / recovery) + ~60.000 dòng ----------
INSERT INTO ledger.accounts (tenant_id, code, name, kind, owner_user_id)
SELECT :'tid'::uuid, pre || ':' || p.id, pre || ' ' || p.display_name, CASE WHEN pre = 'payable' THEN 'liability' ELSE 'asset' END, s.uid
FROM sales s JOIN parties.parties p ON p.user_id = s.uid CROSS JOIN unnest(ARRAY['payable','advance','recovery']) pre
ON CONFLICT (tenant_id, code) DO NOTHING;
CREATE TEMP TABLE jr AS
SELECT gen_random_uuid() AS id, g, a.id AS wallet, (500000 + (g * 7919) % 20000000)::bigint AS amt, now() - (g % 700) * interval '1 day' AS at
FROM generate_series(1, 30000) g
JOIN (SELECT row_number() over (order by id) AS n, id FROM ledger.accounts WHERE code LIKE 'payable:%') a ON a.n = 1 + g % (SELECT count(*) FROM ledger.accounts WHERE code LIKE 'payable:%');
INSERT INTO ledger.journals (id, tenant_id, at, memo, source, source_ref, idempotency_key, entity_id, posted_on, created_by)
SELECT id, :'tid'::uuid, at, 'Hoa hồng về ví (giả)', 'commission.earn', g::text, 'perf:' || g, (SELECT entity_id FROM catalog.projects WHERE code = 'HSR'),
       (at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date, NULL FROM jr;
INSERT INTO ledger.postings (tenant_id, journal_id, account_id, amount_vnd, memo, at)
SELECT :'tid'::uuid, id, (SELECT id FROM ledger.accounts WHERE code = 'expense:commission'), amt, '', at FROM jr
UNION ALL SELECT :'tid'::uuid, id, wallet, -amt, '', at FROM jr;

-- ---------- phải thu phí CĐT + sao kê + phiếu thu ----------
INSERT INTO acct.receivables (tenant_id, code, source, source_ref, label, party, nature, amount_vnd, fee_vnd, revenue_account, match_hints, paid_vnd, issued_on, due_on, status,
                              created_by, receivable_account, match_keys, recognized_key, entity_id)
SELECT :'tid'::uuid, 'HD-' || d.code, 'commission.fee', d.id::text, 'Phí môi giới ' || d.code, 'CĐT Mẫu ' || (d.n % 12), 'revenue', d.fee_vnd, d.fee_vnd, 'revenue:brokerage',
       ARRAY[d.unit_code], CASE WHEN d.n % 3 = 0 THEN d.fee_vnd ELSE 0 END, d.signed_on, d.signed_on + 30,
       CASE WHEN d.n % 3 = 0 THEN 'paid' ELSE 'open' END, NULL, 'receivable:developers', ARRAY[d.code], 'perf:' || d.id, (SELECT entity_id FROM catalog.projects WHERE code = 'HSR')
FROM dl d WHERE d.status = 'signed' AND d.fee_vnd > 0;
INSERT INTO acct.statement_lines (tenant_id, received_on, amount_vnd, memo, payer, bank_ref, source, status, allocated_vnd, entity_id)
SELECT :'tid'::uuid, r.issued_on + 20, r.amount_vnd, 'CK phi moi gioi ' || r.match_keys[1], r.party, 'PERF' || row_number() over (), 'excel', 'applied', r.amount_vnd, r.entity_id
FROM acct.receivables r WHERE r.status = 'paid' AND r.code ~ '^HD-GD-P';
INSERT INTO acct.statement_lines (tenant_id, received_on, amount_vnd, memo, payer, bank_ref, source, status, allocated_vnd, entity_id)
SELECT :'tid'::uuid, core.today() - g % 400, 1000000 * (1 + g % 500), 'Chuyen tien ' || g, 'Khach ' || g, 'PERFX' || g, 'excel', CASE WHEN g % 4 = 0 THEN 'open' ELSE 'ignored' END, 0,
       (SELECT entity_id FROM catalog.projects WHERE code = 'HSR')
FROM generate_series(1, 6000) g;
INSERT INTO acct.payments (tenant_id, receivable_id, line_id, amount_vnd, received_on, entity_id)
SELECT :'tid'::uuid, r.id, l.id, r.amount_vnd, l.received_on, r.entity_id
FROM acct.receivables r JOIN acct.statement_lines l ON l.memo = 'CK phi moi gioi ' || r.match_keys[1] WHERE r.code ~ '^HD-GD-P' AND r.status = 'paid';

-- ---------- quy trình: hồ sơ giao dịch + việc ----------
INSERT INTO flow.cases (tenant_id, template_id, subject_kind, subject_id, title, stage_key, status, owner_id, team_id, opened_at, stage_since)
SELECT :'tid'::uuid, (SELECT id FROM flow.templates WHERE key = 'giao_dich'), 'deals.deal', d.id, d.unit_code || ' — ' || d.customer_name || ' (' || d.code || ')',
       (SELECT stage_key FROM flow.cases LIMIT 1), CASE WHEN d.status = 'signed' AND d.n % 2 = 0 THEN 'done' ELSE 'open' END, d.sale_user_id, d.team_id, d.created_at, d.created_at
FROM dl d;
INSERT INTO flow.tasks (tenant_id, title, notes, status, assignee_id, team_id, due_on, case_id, stage_key, origin, created_at, updated_at, done_at, done_by)
SELECT :'tid'::uuid, 'Việc ' || k || ' hồ sơ ' || c.title, '', CASE WHEN k <= 4 THEN 'done' ELSE 'todo' END, c.owner_id, c.team_id, core.today() + (k - 4) * 3, c.id, c.stage_key, 'stage',
       c.opened_at, now(), CASE WHEN k <= 4 THEN now() END, CASE WHEN k <= 4 THEN 'self' END
FROM flow.cases c CROSS JOIN generate_series(1, 6) k WHERE c.title ~ '\(GD-P';
INSERT INTO flow.tasks (tenant_id, title, status, assignee_id, team_id, due_on, origin, created_at, updated_at, done_at, done_by)
SELECT :'tid'::uuid, 'Việc lẻ ' || g, CASE WHEN g % 3 = 0 THEN 'done' ELSE 'todo' END, s.uid, s.tid, core.today() + (g % 30) - 15, 'manual', now(), now(),
       CASE WHEN g % 3 = 0 THEN now() END, CASE WHEN g % 3 = 0 THEN 'self' END
FROM generate_series(1, 4000) g JOIN sales s ON s.n = 1 + g % (SELECT c FROM nsales);

-- ---------- việc theo dõi ----------
INSERT INTO work.items (tenant_id, kind, title, owner_id, team_id, subject_ref, status, check_every_s, next_check_at, created_at, updated_at)
SELECT :'tid'::uuid, 'fee_collection', 'Thu phí ' || r.code, (SELECT u.id FROM core.users u WHERE u.email = 'ketoan@sanmau.demo'), (SELECT id FROM core.teams WHERE code = 'KT'),
       r.code, CASE WHEN r.status = 'paid' THEN 'done' ELSE 'active' END, 86400, now() + interval '1 day', now(), now()
FROM acct.receivables r WHERE r.code ~ '^HD-GD-P';

-- ---------- kho tài liệu: 400 tài liệu × 15 đoạn ----------
INSERT INTO kb.documents (tenant_id, title, shelf, kind, context_code, body, created_by)
SELECT :'tid'::uuid, 'Tài liệu mẫu ' || g || ' dự án P' || lpad((1 + g % 90)::text, 3, '0'), CASE WHEN g % 10 = 0 THEN 'finance' ELSE 'common' END,
       (ARRAY['policy','project','legal','process','faq','script'])[1 + g % 6], 'P' || lpad((1 + g % 90)::text, 3, '0'), 'Nội dung mẫu ' || g,
       (SELECT id FROM core.users WHERE email = 'admin@sanmau.demo')
FROM generate_series(1, 400) g;
INSERT INTO kb.chunks (tenant_id, document_id, seq, heading, text, folded)
SELECT :'tid'::uuid, d.id, k, 'Mục ' || k,
       'Chính sách chiết khấu thanh toán sớm ' || k || '% cho căn hộ ' || (1 + k % 3) || ' phòng ngủ, hỗ trợ vay ngân hàng ' || (50 + k) || '% giá trị, tiến độ đóng tiền đợt ' || k || '. ' || repeat('Điều khoản bổ sung về bàn giao và phí quản lý. ', 8),
       lower('chinh sach chiet khau thanh toan som ' || k || ' cho can ho ' || (1 + k % 3) || ' phong ngu ho tro vay ngan hang ' || (50 + k) || ' gia tri tien do dong tien dot ' || k || ' ' || repeat('dieu khoan bo sung ve ban giao va phi quan ly ', 8))
FROM kb.documents d CROSS JOIN generate_series(1, 15) k WHERE d.title LIKE 'Tài liệu mẫu %';

-- ---------- trao đổi: ~4.000 luồng, 200.000 tin ----------
ALTER TABLE talk.messages DISABLE TRIGGER trg_talk_message;
INSERT INTO talk.threads (tenant_id, subject_kind, subject_id, title, created_by, created_at)
SELECT :'tid'::uuid, 'deals.deal', d.id, 'Giao dịch ' || d.code, d.sale_user_id, d.created_at FROM dl d
ON CONFLICT DO NOTHING;
INSERT INTO talk.threads (tenant_id, subject_kind, subject_id, title, created_by, created_at)
SELECT :'tid'::uuid, 'crm.lead', ld.id, 'Khách ' || ld.n, ld.owner_id, ld.created_at FROM ld WHERE ld.n % 30 = 0 AND ld.owner_id IS NOT NULL;
-- phòng đội cho 15 đội KD
INSERT INTO talk.rooms (tenant_id, name, purpose, team_id)
SELECT :'tid'::uuid, 'Phòng ' || t.name, 'Trao đổi đội', t.id FROM core.teams t WHERE t.code LIKE 'KD-%' ON CONFLICT DO NOTHING;
INSERT INTO talk.room_members (tenant_id, room_id, user_id)
SELECT :'tid'::uuid, r.id, tm.user_id FROM talk.rooms r JOIN core.team_members tm ON tm.team_id = r.team_id ON CONFLICT DO NOTHING;
INSERT INTO talk.threads (tenant_id, subject_kind, subject_id, title, created_at)
SELECT :'tid'::uuid, 'talk.room', r.id, r.name, now() - interval '300 day' FROM talk.rooms r WHERE r.team_id IS NOT NULL ON CONFLICT DO NOTHING;

CREATE TEMP TABLE th AS SELECT row_number() over (order by t.id) AS n, t.id, t.subject_kind, t.created_by,
  CASE t.subject_kind WHEN 'talk.room' THEN 4000 WHEN 'deals.deal' THEN 45 ELSE 20 END AS msgs
FROM talk.threads t WHERE t.title ~ '^(Giao dịch GD-P|Khách |Phòng )';
SELECT sum(msgs) AS planned_messages, count(*) AS threads FROM th;
-- người trong luồng: chủ + 2 người cùng đội (luồng giao dịch / lead) ; phòng đội: mọi thành viên
INSERT INTO talk.members (tenant_id, thread_id, user_id, reason, last_read_id)
SELECT DISTINCT :'tid'::uuid, th.id, x.uid, 'follow', 0 FROM th
JOIN LATERAL (SELECT th.created_by AS uid UNION SELECT s.uid FROM sales s WHERE s.n IN (1 + th.n % 100, 1 + (th.n * 7) % 100)) x ON x.uid IS NOT NULL
WHERE th.subject_kind <> 'talk.room';
INSERT INTO talk.members (tenant_id, thread_id, user_id, reason)
SELECT :'tid'::uuid, th.id, rm.user_id, 'follow' FROM th JOIN talk.threads t ON t.id = th.id JOIN talk.room_members rm ON rm.room_id = t.subject_id
WHERE th.subject_kind = 'talk.room' ON CONFLICT DO NOTHING;
CREATE TEMP TABLE thm AS SELECT thread_id, array_agg(user_id) AS us FROM talk.members WHERE thread_id IN (SELECT id FROM th) GROUP BY 1;
INSERT INTO talk.messages (tenant_id, thread_id, author_id, body, created_at)
SELECT :'tid'::uuid, th.id, m.us[1 + k % cardinality(m.us)],
       (ARRAY['Anh gọi khách chưa?','Khách hẹn cuối tuần xem nhà mẫu','Đã gửi bảng giá căn','CĐT báo chính sách mới tuần sau','Nhờ kế toán kiểm tiền cọc','Ok em','Khách hỏi hỗ trợ vay 70%'])[1 + k % 7] || ' #' || k,
       now() - (th.msgs - k) * interval '37 min'
FROM th JOIN thm m ON m.thread_id = th.id CROSS JOIN LATERAL generate_series(1, th.msgs) k;
ALTER TABLE talk.messages ENABLE TRIGGER trg_talk_message;
UPDATE talk.threads t SET last_message_at = x.at, last_message_id = x.mid
FROM (SELECT thread_id, max(created_at) AS at, max(id) AS mid FROM talk.messages GROUP BY 1) x WHERE x.thread_id = t.id;
UPDATE talk.members m SET last_read_id = greatest(0, t.last_message_id - (abs(hashtext(m.user_id::text)) % 30)) FROM talk.threads t WHERE t.id = m.thread_id AND t.last_message_id IS NOT NULL;
INSERT INTO talk.links (tenant_id, message_id, url, host)
SELECT :'tid'::uuid, id, 'https://example.com/tai-lieu/' || id, 'example.com' FROM talk.messages WHERE id % 100 = 0;

ANALYZE;
SELECT 'customers' t, count(*) FROM crm.customers UNION ALL SELECT 'leads', count(*) FROM crm.leads UNION ALL SELECT 'activities', count(*) FROM crm.activities
UNION ALL SELECT 'deals', count(*) FROM deals.deals UNION ALL SELECT 'installments', count(*) FROM deals.installments UNION ALL SELECT 'units', count(*) FROM catalog.units
UNION ALL SELECT 'postings', count(*) FROM ledger.postings UNION ALL SELECT 'messages', count(*) FROM talk.messages UNION ALL SELECT 'threads', count(*) FROM talk.threads
UNION ALL SELECT 'users', count(*) FROM core.memberships UNION ALL SELECT 'flow.tasks', count(*) FROM flow.tasks UNION ALL SELECT 'kb.chunks', count(*) FROM kb.chunks
UNION ALL SELECT 'receivables', count(*) FROM acct.receivables UNION ALL SELECT 'statement_lines', count(*) FROM acct.statement_lines UNION ALL SELECT 'parties', count(*) FROM parties.parties;
