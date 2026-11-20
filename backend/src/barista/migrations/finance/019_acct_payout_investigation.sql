-- module: acct
-- 0104 C19: a payout under bank investigation may end as transferred OR cancelled; cancelled requires a recorded investigation result.
-- status_note holds the latest investigation basis; money facts stay protected.
ALTER TABLE acct.payouts ADD COLUMN status_note TEXT NOT NULL DEFAULT '';
CREATE OR REPLACE FUNCTION acct.protect_payout() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 IF (to_jsonb(OLD)-ARRAY['status','bank_ref','transferred_on','status_note'])<>(to_jsonb(NEW)-ARRAY['status','bank_ref','transferred_on','status_note'])
 THEN RAISE EXCEPTION 'Không sửa bên nhận/số tiền/căn cứ lệnh chi'; END IF;
 IF OLD.status NOT IN ('pending_transfer','unknown') OR
    (OLD.status='unknown' AND NEW.status NOT IN ('transferred','cancelled')) OR
    (NEW.status<>'transferred' AND (NEW.bank_ref<>OLD.bank_ref OR NEW.transferred_on IS DISTINCT FROM OLD.transferred_on))
 THEN RAISE EXCEPTION 'Chuyển trạng thái lệnh chi không hợp lệ'; END IF;
 IF OLD.status='unknown' AND NEW.status='cancelled' AND (btrim(NEW.status_note)='' OR NEW.status_note=OLD.status_note)
 THEN RAISE EXCEPTION 'Huỷ lệnh đang tra soát cần ghi kết quả tra soát'; END IF;
 RETURN NEW;
END $$;
-- Set-based settlement balances aggregate these by obligation.
CREATE INDEX payout_allocations_payable ON acct.payout_allocations(tenant_id,payable_id);
CREATE INDEX advance_offsets_payable ON acct.advance_offsets(tenant_id,payable_id);
CREATE INDEX advance_offsets_advance ON acct.advance_offsets(tenant_id,advance_id);
CREATE INDEX recovery_receipts_payable ON acct.recovery_receipts(tenant_id,payable_id);
CREATE INDEX payables_owner ON acct.payables(tenant_id,owner_id);
CREATE INDEX payables_party ON acct.payables(tenant_id,party_id,entity_id);
