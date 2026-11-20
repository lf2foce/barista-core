-- module: sample
-- Gói mẫu ngoài lõi: bảng riêng trong schema của gói, qua core.secure_tenant như mọi bảng.
CREATE SCHEMA sample;
GRANT USAGE ON SCHEMA sample TO barista_app;

CREATE TABLE sample.notes (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL DEFAULT core.tenant() REFERENCES core.tenants(id) ON DELETE CASCADE,
    body       TEXT NOT NULL CHECK (btrim(body) <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CALL core.secure_tenant('sample.notes');
GRANT SELECT, INSERT ON sample.notes TO barista_app;
