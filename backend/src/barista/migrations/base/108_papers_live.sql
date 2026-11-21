-- module: papers, core — lý do: ADR 0251 — giấy tờ đọc số SỐNG của hồ sơ + thẩm quyền ban hành theo LOẠI giấy.
--   1. ``papers.entries``: dữ kiện NGƯỜI NHẬP của một giấy chưa ban hành (ô hồ sơ không giữ) — ai, lúc nào, căn cứ. Chỉ thêm: nhập lại = dòng mới,
--      dòng mới nhất của mỗi ô thắng. Bản chụp lúc soạn (``papers.revisions`` / ``papers.fields``) không còn dùng cho bản đang lập; ban hành ghi MỘT bản
--      chốt vào đó (``via = 'issue'``). Dữ kiện người nhập của bản chưa ban hành cũ chép sang (chỉ THÊM dòng, bảng cũ giữ nguyên).
--   2. ``authority``: tài nguyên THẨM QUYỀN của loại giấy (khai tĩnh: lõi ``papers.office`` — văn bản hành chính; gói khai cho chứng từ của mình). Vai có
--      ``manage`` trên tài nguyên đó ban hành / phát hành loại đó. Giấy chép thẩm quyền lúc lập; mẫu giữ lựa chọn của công ty ('' = theo loại hồ sơ).
--      Giấy đã có nhận mặc định ``papers.office`` (người ban hành như trước: vai có quyền văn bản hành chính).
--   3. Chính sách đọc: bản CHƯA ban hành chỉ người lập + người có thẩm quyền loại đó (phạm vi của tài nguyên thẩm quyền) thấy; đã ban hành / hết
--      hiệu lực như cũ (phạm vi ``papers``) và người có thẩm quyền loại đó. Tài liệu gửi khách / hồ sơ nội bộ (``layout = 'handout'``: bản tư vấn, bản tin
--      phân tích, bộ hồ sơ kiểm tra…) KHÔNG có bước ban hành — không phải "bản chưa ban hành" ⇒ đọc như cũ theo phạm vi ``papers``. Tài nguyên thẩm quyền CHỈ cấp cho vai ban hành — cấp ``read`` cho vai khác
--      là cho vai đó thấy bản chưa ban hành (tests/test_papers_live.py chặn trong bộ mặc định).
-- Không bảo trì: bản cũ chạy song song ghi giấy mới với ``authority`` mặc định và vẫn đọc / ghi được dòng của chính người đó.
SET LOCAL lock_timeout = '10s';

ALTER TABLE papers.documents ADD COLUMN authority text DEFAULT 'papers.office'::text NOT NULL
    CHECK (authority ~ '^[a-z_]+(\.[a-z_]+)*$');
ALTER TABLE papers.templates ADD COLUMN authority text DEFAULT ''::text NOT NULL
    CHECK (authority = '' OR authority ~ '^[a-z_]+(\.[a-z_]+)*$');
GRANT UPDATE (authority) ON papers.templates TO barista_app;

ALTER TABLE papers.revisions DROP CONSTRAINT revisions_via_check;
ALTER TABLE papers.revisions ADD CONSTRAINT revisions_via_check CHECK (via IN ('template', 'refresh', 'person', 'issue'));

CREATE TABLE papers.entries (
    id uuid DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    tenant_id uuid DEFAULT core.tenant() NOT NULL REFERENCES core.tenants(id) ON DELETE CASCADE,
    document_id uuid NOT NULL REFERENCES papers.documents(id) ON DELETE CASCADE,
    key text NOT NULL CHECK (key ~ '^[a-z][a-z0-9_]*$'),
    value jsonb NOT NULL,
    display text DEFAULT ''::text NOT NULL,
    reason text DEFAULT ''::text NOT NULL CHECK (length(reason) <= 500),
    entered_by uuid NOT NULL REFERENCES core.users(id),
    entered_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);
CREATE INDEX idx_entries_document ON papers.entries (document_id, key, entered_at DESC);
CALL core.secure_child('papers.entries', 'papers.documents', 'document_id');
GRANT SELECT, INSERT ON papers.entries TO barista_app;

INSERT INTO papers.entries (tenant_id, document_id, key, value, display, reason, entered_by, entered_at)
SELECT f.tenant_id, f.document_id, f.key, f.value, f.display, f.reason, f.entered_by, f.entered_at
FROM papers.fields f JOIN papers.documents d ON d.id = f.document_id AND f.rev = d.rev
WHERE d.status = 'draft' AND d.template_version_id IS NOT NULL AND f.origin = 'person';

-- Phạm vi của MỘT tài nguyên đọc từ cột (thẩm quyền của từng giấy): bảng phạm vi của phiên — trong chính sách luôn gọi dạng InitPlan
-- ``(SELECT core.my_scopes())`` (tính một lần mỗi câu). Quyền hệ thống ⇒ NULL (chính sách tự xét ``core.is_system()`` trước).
CREATE FUNCTION core.my_scopes() RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$ SELECT CASE WHEN core.is_system() THEN NULL ELSE coalesce(core.claims() -> 'scopes', '{}'::jsonb) END $$;

-- Người có THẨM QUYỀN loại giấy = phạm vi của tài nguyên ``authority`` trên dòng (own / unit / team / all như core.can_see) — MỘT biểu thức cho USING lẫn WITH CHECK.
DO $$
DECLARE
    sc CONSTANT text := '((SELECT core.my_scopes()) ->> authority)';
    authority_ok CONSTANT text := format('((SELECT core.is_system()) OR %1$s = ''all''
        OR (%1$s IN (''own'', ''team'', ''unit'') AND created_by IS NOT NULL AND created_by = (SELECT core.me()))
        OR (%1$s = ''team'' AND team_id IS NOT NULL AND team_id = ANY ((SELECT core.my_teams())::uuid[]))
        OR (%1$s = ''unit'' AND team_id IS NOT NULL AND team_id = ANY ((SELECT core.my_units())::uuid[])))', sc);
BEGIN
    DROP POLICY p_scope ON papers.documents;
    EXECUTE format($p$CREATE POLICY p_scope ON papers.documents
        USING (tenant_id = core.tenant() AND (((status <> 'draft' OR layout = 'handout') AND core.can_see('papers', created_by, team_id))
                                              OR (status = 'draft' AND created_by IS NOT NULL AND created_by = core.me())
                                              OR %1$s))
        WITH CHECK (tenant_id = core.tenant() AND (core.can_see('papers', created_by, team_id) OR %1$s))$p$, authority_ok);
END $$;
CALL core.initplan_policies(ARRAY['papers']);

-- Ai đang ban hành được (``manage`` trên ``papers``) giữ nguyên quyền ban hành văn bản hành chính — chỉ THÊM dòng quyền, dòng cũ giữ nguyên.
INSERT INTO core.grants (tenant_id, role, resource, scope, actions)
SELECT g.tenant_id, g.role, 'papers.office', g.scope, ARRAY['read', 'write', 'manage']
FROM core.grants g
WHERE g.resource = 'papers' AND 'manage' = ANY (g.actions)
  AND NOT EXISTS (SELECT 1 FROM core.grants x WHERE x.tenant_id = g.tenant_id AND x.role = g.role AND x.resource = 'papers.office');
