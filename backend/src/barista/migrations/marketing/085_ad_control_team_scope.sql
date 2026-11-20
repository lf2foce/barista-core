-- module: mkt — 0209 (bổ sung 14/11/2026): điều khiển quảng cáo theo ĐỘI. Phát hiện / sổ thay đổi / chuyển đổi mang người phụ trách + đội của CHIẾN DỊCH
-- (bản chụp lúc ghi, cùng nghĩa ``mkt.campaigns.owner_id / team_id``) ⇒ phạm vi quyền ``mkt.ad_control`` thu hẹp được dòng như ``mkt`` thu hẹp chiến dịch:
-- "team" = chiến dịch mình phụ trách + của đội mình quản lý (cây con), "all" = cả công ty. Dòng không biết chiến dịch (đối tượng kênh chưa có ở Marketing,
-- kế hoạch / [[offering]] gom chiến dịch của nhiều đội) để trống ⇒ chỉ phạm vi "all" thấy. ``mkt.ad_policy`` vẫn là MỘT dòng của cả công ty (sửa: quyền
-- ``mkt.ad_policy`` manage, kiểm ở lệnh) — nhưng chính sách cũ ``core.can_see(…, NULL, NULL)`` chỉ cho phạm vi "all" ĐỌC; người phạm vi hẹp hơn phải đọc
-- được ngưỡng duyệt áp lúc họ bấm ⇒ đọc theo "có quyền điều khiển quảng cáo ở phạm vi nào đó".
-- Không khoá ngoại: cột là bản chụp (đội đổi tên / giải thể không chặn, không sửa sổ cũ).
-- Chỉ THÊM cột trống được + điền từ chiến dịch + đặt lại chính sách RLS cùng hàm thấy dòng. Không bảo trì: bản cũ chạy song song ghi dòng KHÔNG có đội ⇒
-- chỉ phạm vi "all" thấy; quyền của công ty đang chạy không đổi (bộ mặc định chỉ thêm — marketer của công ty cũ vẫn "all" tới khi quản trị sửa) ⇒ lệnh
-- của bản cũ không bị chặn ghi.
SET LOCAL lock_timeout = '10s';

ALTER TABLE mkt.ad_findings ADD COLUMN owner_id uuid, ADD COLUMN team_id uuid;
ALTER TABLE mkt.ad_changes ADD COLUMN owner_id uuid, ADD COLUMN team_id uuid;
ALTER TABLE mkt.ad_conversions ADD COLUMN owner_id uuid, ADD COLUMN team_id uuid;
COMMENT ON COLUMN mkt.ad_findings.team_id IS 'đội của chiến dịch lúc ghi (mkt.campaigns.team_id) — trống = chỉ phạm vi all thấy';
COMMENT ON COLUMN mkt.ad_changes.team_id IS 'đội của chiến dịch lúc ghi (mkt.campaigns.team_id) — trống = chỉ phạm vi all thấy';
COMMENT ON COLUMN mkt.ad_conversions.team_id IS 'đội của chiến dịch lúc ghi (mkt.campaigns.team_id) — trống = chỉ phạm vi all thấy';

-- ── Điền dòng đã có từ chiến dịch (migration chạy role owner, BYPASSRLS). Nhiều chiến dịch (kế hoạch / [[offering]]): chỉ khi CHUNG một người / một đội. ──
WITH links AS (
    SELECT f.id, c.owner_id, c.team_id FROM mkt.ad_findings f
    JOIN mkt.campaigns c ON c.tenant_id = f.tenant_id AND c.code = f.scope_key WHERE f.scope = 'campaign'
    UNION ALL
    SELECT f.id, c.owner_id, c.team_id FROM mkt.ad_findings f
    JOIN mkt.ads a ON a.tenant_id = f.tenant_id AND a.code = f.scope_key JOIN mkt.campaigns c ON c.id = a.campaign_id WHERE f.scope = 'ad'
    UNION ALL
    SELECT f.id, c.owner_id, c.team_id FROM mkt.ad_findings f
    CROSS JOIN LATERAL jsonb_array_elements_text(CASE WHEN jsonb_typeof(f.figures -> 'campaigns') = 'array' THEN f.figures -> 'campaigns' ELSE '[]' END) k(code)
    JOIN mkt.campaigns c ON c.tenant_id = f.tenant_id AND c.code = k.code WHERE f.scope IN ('plan', 'offering')
), one AS (
    SELECT id, CASE WHEN count(*) = count(owner_id) AND count(DISTINCT owner_id) = 1 THEN (array_agg(owner_id))[1] END AS owner_id,
               CASE WHEN count(*) = count(team_id) AND count(DISTINCT team_id) = 1 THEN (array_agg(team_id))[1] END AS team_id
    FROM links GROUP BY id
)
UPDATE mkt.ad_findings f SET owner_id = one.owner_id, team_id = one.team_id FROM one WHERE f.id = one.id;

WITH links AS (
    SELECT x.id, c.owner_id, c.team_id FROM mkt.ad_changes x
    JOIN mkt.campaigns c ON c.tenant_id = x.tenant_id AND c.code = x.ref WHERE x.level = 'campaign'
    UNION ALL
    SELECT x.id, c.owner_id, c.team_id FROM mkt.ad_changes x
    JOIN mkt.ads a ON a.tenant_id = x.tenant_id AND a.code = x.ref JOIN mkt.campaigns c ON c.id = a.campaign_id WHERE x.level = 'ad'
    UNION ALL
    SELECT DISTINCT x.id, c.owner_id, c.team_id FROM mkt.ad_changes x
    JOIN mkt.ads a ON a.tenant_id = x.tenant_id AND a.adset_code = x.ref JOIN mkt.campaigns c ON c.id = a.campaign_id WHERE x.level = 'adset'
), one AS (
    SELECT id, CASE WHEN count(*) = count(owner_id) AND count(DISTINCT owner_id) = 1 THEN (array_agg(owner_id))[1] END AS owner_id,
               CASE WHEN count(*) = count(team_id) AND count(DISTINCT team_id) = 1 THEN (array_agg(team_id))[1] END AS team_id
    FROM links GROUP BY id
)
UPDATE mkt.ad_changes x SET owner_id = one.owner_id, team_id = one.team_id FROM one WHERE x.id = one.id;

UPDATE mkt.ad_conversions v SET owner_id = c.owner_id, team_id = c.team_id
FROM mkt.campaigns c WHERE c.tenant_id = v.tenant_id AND c.code = v.campaign_code AND v.campaign_code <> '';

-- ── RLS: cùng tài nguyên, nay có cột người phụ trách / đội (core.can_see như mkt.campaigns) ──
DROP POLICY p_scope ON mkt.ad_findings;
CALL core.secure_table('mkt.ad_findings', 'mkt.ad_control', 'owner_id', 'team_id');
DROP POLICY p_scope ON mkt.ad_changes;
CALL core.secure_table('mkt.ad_changes', 'mkt.ad_control', 'owner_id', 'team_id');
DROP POLICY p_scope ON mkt.ad_conversions;
CALL core.secure_table('mkt.ad_conversions', 'mkt.ad_control', 'owner_id', 'team_id');

-- Chính sách quảng cáo: một dòng / công ty, mọi phạm vi ``mkt.ad_control`` (own / unit / team / all) đọc được — ngưỡng phải duyệt, tỷ giá, công tắc chuyển đổi
-- áp cho lần bấm của họ (đọc không thấy ⇒ code dùng ngưỡng MẶC ĐỊNH, sai với công ty). Ghi chỉ qua mkt.set_ad_policy (quyền mkt.ad_policy manage) và tác nhân
-- theo dõi (quyền hệ thống, ``last_watch_on``). Dạng InitPlan như core.secure_table (0145).
DROP POLICY p_scope ON mkt.ad_policy;
CREATE POLICY p_scope ON mkt.ad_policy
    USING (tenant_id = (SELECT core.tenant()) AND (SELECT core.scope('mkt.ad_control')) <> 'none')
    WITH CHECK (tenant_id = (SELECT core.tenant()) AND (SELECT core.scope('mkt.ad_control')) <> 'none');
