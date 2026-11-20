-- module: acct, ledger — lý do: chuyển số dư ví lịch sử sang nghĩa vụ/ứng bằng bút toán phân loại một lần
-- 0104: immutable obligations/advances; allocations, not mutable balances. Requires maintenance.
CREATE TABLE acct.payables (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL DEFAULT core.tenant(),
 party_id UUID NOT NULL, owner_id UUID, entity_id UUID NOT NULL,
 source TEXT NOT NULL CHECK (source ~ '^[a-z_]+\.[a-z_]+$'), source_ref TEXT NOT NULL CHECK (btrim(source_ref) <> ''),
 kind TEXT NOT NULL DEFAULT 'payable' CHECK (kind IN ('payable','advance','recovery')),
 label TEXT NOT NULL, amount_vnd BIGINT NOT NULL CHECK (amount_vnd > 0),
 tax_vnd BIGINT NOT NULL DEFAULT 0 CHECK (tax_vnd >= 0 AND tax_vnd <= amount_vnd),
 fee_vnd BIGINT NOT NULL DEFAULT 0 CHECK (fee_vnd >= 0),
 expense_account TEXT NOT NULL DEFAULT '', payload JSONB NOT NULL, journal_id UUID,
 tax_basis JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id,id), UNIQUE (tenant_id,source,source_ref)
);
CREATE TABLE acct.payable_adjustments (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL DEFAULT core.tenant(),
 payable_id UUID NOT NULL, amount_vnd BIGINT NOT NULL CHECK (amount_vnd > 0),
 action_key TEXT NOT NULL, reason TEXT NOT NULL CHECK (btrim(reason) <> ''), journal_id UUID,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (tenant_id,payable_id,action_key),
 FOREIGN KEY (tenant_id,payable_id) REFERENCES acct.payables(tenant_id,id)
);
ALTER TABLE acct.payouts ALTER COLUMN user_id DROP NOT NULL;
ALTER TABLE acct.payouts ADD COLUMN party_id UUID;
ALTER TABLE acct.payouts ADD COLUMN recipient_name TEXT NOT NULL DEFAULT '';
ALTER TABLE acct.payouts ADD COLUMN recipient_instruction TEXT NOT NULL DEFAULT '';
ALTER TABLE acct.payouts ADD COLUMN payload JSONB;
ALTER TABLE acct.payouts ADD CONSTRAINT payouts_tenant_id_id UNIQUE (tenant_id,id);
ALTER TABLE acct.payouts DROP CONSTRAINT payouts_status_check;
ALTER TABLE acct.payouts ADD CHECK (status IN ('pending_transfer','transferred','cancelled','unknown'));
CREATE TABLE acct.payout_allocations (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL DEFAULT core.tenant(),
 payout_id UUID NOT NULL, payable_id UUID NOT NULL, amount_vnd BIGINT NOT NULL CHECK (amount_vnd > 0),
 UNIQUE (tenant_id,payout_id,payable_id),
 FOREIGN KEY (tenant_id,payout_id) REFERENCES acct.payouts(tenant_id,id),
 FOREIGN KEY (tenant_id,payable_id) REFERENCES acct.payables(tenant_id,id)
);
CREATE TABLE acct.advance_offsets (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL DEFAULT core.tenant(),
 payable_id UUID NOT NULL, advance_id UUID NOT NULL, amount_vnd BIGINT NOT NULL CHECK (amount_vnd <> 0), journal_id UUID, reverses UUID,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY (tenant_id,payable_id) REFERENCES acct.payables(tenant_id,id),
 FOREIGN KEY (tenant_id,advance_id) REFERENCES acct.payables(tenant_id,id), CHECK (payable_id <> advance_id)
);
CALL core.secure_table('acct.payables','acct','owner_id');
CALL core.secure_child('acct.payable_adjustments','acct.payables','payable_id');
CALL core.secure_child('acct.payout_allocations','acct.payouts','payout_id');
CALL core.secure_child('acct.advance_offsets','acct.payables','payable_id');
GRANT UPDATE (journal_id) ON acct.payables TO barista_app;
GRANT SELECT,INSERT ON acct.payables,acct.payable_adjustments,acct.payout_allocations,acct.advance_offsets TO barista_app;
-- Metadata populated before RLS-restricted app access; IDs are looked up, never assumed equal to user IDs.
UPDATE acct.payouts p SET party_id=d.id, recipient_name=d.display_name
FROM parties.parties d WHERE d.tenant_id=p.tenant_id AND d.user_id=p.user_id;
ALTER TABLE acct.payouts ALTER COLUMN party_id SET NOT NULL;
UPDATE acct.payouts p SET payload=jsonb_build_object('legacy_request_kind',r.kind)
FROM commission.requests r WHERE p.source='commission.request' AND p.source_ref=r.id::text AND p.tenant_id=r.tenant_id;
-- Historical wallets are reclassified once, per entity, without replaying any cash/expense.
-- Migration owns this cross-module transfer; runtime modules still go through executor/ledger contracts.
DO $$ DECLARE r RECORD; target UUID; jid UUID; anchor UUID; BEGIN
 IF EXISTS(SELECT 1 FROM ledger.accounts a JOIN ledger.postings p ON p.account_id=a.id
           WHERE a.code LIKE 'wallet:%' AND NOT EXISTS(SELECT 1 FROM parties.parties d WHERE d.tenant_id=a.tenant_id AND d.user_id=a.owner_user_id)
           GROUP BY a.id HAVING sum(p.amount_vnd)<>0) THEN RAISE EXCEPTION '0104: ví có số dư chưa ánh xạ được người nhận'; END IF;
 FOR r IN SELECT a.id aid,a.tenant_id,a.owner_user_id,p.id party_id,p.display_name,j.entity_id,sum(x.amount_vnd)::bigint bal
          FROM ledger.accounts a JOIN ledger.postings x ON x.account_id=a.id JOIN ledger.journals j ON j.id=x.journal_id
          JOIN parties.parties p ON p.tenant_id=a.tenant_id AND p.user_id=a.owner_user_id
          WHERE a.code LIKE 'wallet:%' GROUP BY a.id,p.id,j.entity_id HAVING sum(x.amount_vnd)<>0
 LOOP
   INSERT INTO ledger.accounts(tenant_id,code,name,kind,owner_user_id)
   VALUES(r.tenant_id,(CASE WHEN r.bal<0 THEN 'payable:' ELSE 'advance:' END)||r.party_id::text,
          r.display_name,CASE WHEN r.bal<0 THEN 'liability' ELSE 'asset' END,r.owner_user_id)
   ON CONFLICT(tenant_id,code) DO NOTHING;
   SELECT id INTO target FROM ledger.accounts WHERE tenant_id=r.tenant_id AND code=(CASE WHEN r.bal<0 THEN 'payable:' ELSE 'advance:' END)||r.party_id::text;
   INSERT INTO ledger.journals(tenant_id,memo,source,source_ref,idempotency_key,entity_id)
   VALUES(r.tenant_id,'0104: phân loại số dư ví đầu kỳ','acct.opening',r.aid::text,
          'acct.opening:'||r.aid::text||':'||r.entity_id::text,r.entity_id) RETURNING id INTO jid;
   INSERT INTO ledger.postings(tenant_id,journal_id,account_id,amount_vnd,memo)
   VALUES(r.tenant_id,jid,r.aid,-r.bal,'0104 opening'),(r.tenant_id,jid,target,r.bal,'0104 opening');
   INSERT INTO acct.payables(tenant_id,party_id,owner_id,entity_id,source,source_ref,kind,label,amount_vnd,payload,journal_id)
   VALUES(r.tenant_id,r.party_id,r.owner_user_id,r.entity_id,'acct.opening',r.aid::text||':'||r.entity_id::text,
          CASE WHEN r.bal<0 THEN 'payable' ELSE 'advance' END,'Số dư đầu kỳ 0104',abs(r.bal),jsonb_build_object('wallet_id',r.aid),jid)
   RETURNING id INTO anchor;
 END LOOP;
END $$;

CREATE UNIQUE INDEX advance_offset_once ON acct.advance_offsets(tenant_id,payable_id,advance_id) WHERE amount_vnd>0;
CREATE TABLE acct.payment_requests (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id UUID NOT NULL DEFAULT core.tenant(),
 requested_by UUID NOT NULL,payload JSONB NOT NULL,approval_id UUID,
 status TEXT NOT NULL DEFAULT 'requested' CHECK(status IN ('requested','executed','rejected')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CALL core.secure_table('acct.payment_requests','acct','requested_by');
GRANT SELECT,INSERT,UPDATE ON acct.payment_requests TO barista_app;
-- Application may fill the journal anchor once, but never edit financial facts.
CREATE FUNCTION acct.immutable_payable() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 IF TG_OP='UPDATE' AND OLD.journal_id IS NULL AND NEW.journal_id IS NOT NULL
    AND (to_jsonb(OLD)-'journal_id')=(to_jsonb(NEW)-'journal_id') THEN RETURN NEW; END IF;
 RAISE EXCEPTION 'Nghĩa vụ chỉ thêm; sửa bằng điều chỉnh';
END $$;
CREATE TRIGGER payable_immutable BEFORE UPDATE OR DELETE ON acct.payables FOR EACH ROW EXECUTE FUNCTION acct.immutable_payable();
CREATE FUNCTION acct.immutable_settlement_fact() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 RAISE EXCEPTION 'Phân bổ/điều chỉnh chỉ thêm'; END $$;
CREATE TRIGGER adjustment_immutable BEFORE UPDATE OR DELETE ON acct.payable_adjustments FOR EACH ROW EXECUTE FUNCTION acct.immutable_settlement_fact();
CREATE TRIGGER offset_immutable BEFORE UPDATE OR DELETE ON acct.advance_offsets FOR EACH ROW EXECUTE FUNCTION acct.immutable_settlement_fact();
CREATE TRIGGER allocation_immutable BEFORE UPDATE OR DELETE ON acct.payout_allocations FOR EACH ROW EXECUTE FUNCTION acct.immutable_settlement_fact();

ALTER TABLE acct.statement_lines ADD CONSTRAINT statement_lines_tenant_id_id UNIQUE(tenant_id,id);
CREATE TABLE acct.recovery_receipts (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id UUID NOT NULL DEFAULT core.tenant(),
 payable_id UUID NOT NULL,line_id UUID NOT NULL,amount_vnd BIGINT NOT NULL CHECK(amount_vnd>0),journal_id UUID NOT NULL,
 UNIQUE(tenant_id,payable_id,line_id),
 FOREIGN KEY(tenant_id,payable_id) REFERENCES acct.payables(tenant_id,id),
 FOREIGN KEY(tenant_id,line_id) REFERENCES acct.statement_lines(tenant_id,id)
);
CALL core.secure_child('acct.recovery_receipts','acct.payables','payable_id');
GRANT SELECT,INSERT ON acct.recovery_receipts TO barista_app;
CREATE TRIGGER recovery_immutable BEFORE UPDATE OR DELETE ON acct.recovery_receipts FOR EACH ROW EXECUTE FUNCTION acct.immutable_settlement_fact();

-- Settlement facts must agree on recipient and legal entity even if a future handler forgets its checks.
CREATE FUNCTION acct.check_allocation_party() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE p acct.payables; x acct.payouts; BEGIN
 SELECT * INTO p FROM acct.payables WHERE tenant_id=NEW.tenant_id AND id=NEW.payable_id;
 SELECT * INTO x FROM acct.payouts WHERE tenant_id=NEW.tenant_id AND id=NEW.payout_id;
 IF p.id IS NULL OR x.id IS NULL OR p.party_id<>x.party_id OR p.entity_id<>x.entity_id THEN
   RAISE EXCEPTION 'Phân bổ khác người nhận hoặc pháp nhân'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER allocation_party BEFORE INSERT ON acct.payout_allocations FOR EACH ROW EXECUTE FUNCTION acct.check_allocation_party();
CREATE FUNCTION acct.check_offset_party() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE p acct.payables; a acct.payables; BEGIN
 SELECT * INTO p FROM acct.payables WHERE tenant_id=NEW.tenant_id AND id=NEW.payable_id;
 SELECT * INTO a FROM acct.payables WHERE tenant_id=NEW.tenant_id AND id=NEW.advance_id;
 IF p.id IS NULL OR a.id IS NULL OR p.kind<>'payable' OR a.kind<>'advance' OR p.party_id<>a.party_id OR p.entity_id<>a.entity_id
 THEN RAISE EXCEPTION 'Cấn ứng khác người nhận/pháp nhân hoặc sai loại'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER offset_party BEFORE INSERT ON acct.advance_offsets FOR EACH ROW EXECUTE FUNCTION acct.check_offset_party();
CREATE FUNCTION acct.protect_payout() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 IF (to_jsonb(OLD)-ARRAY['status','bank_ref','transferred_on'])<>(to_jsonb(NEW)-ARRAY['status','bank_ref','transferred_on'])
 THEN RAISE EXCEPTION 'Không sửa bên nhận/số tiền/căn cứ lệnh chi'; END IF;
 IF OLD.status NOT IN ('pending_transfer','unknown') OR
    (OLD.status='unknown' AND NEW.status<>'transferred') OR
    (NEW.status<>'transferred' AND (NEW.bank_ref<>OLD.bank_ref OR NEW.transferred_on IS DISTINCT FROM OLD.transferred_on))
 THEN RAISE EXCEPTION 'Chuyển trạng thái lệnh chi không hợp lệ'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER payout_protected BEFORE UPDATE ON acct.payouts FOR EACH ROW EXECUTE FUNCTION acct.protect_payout();
