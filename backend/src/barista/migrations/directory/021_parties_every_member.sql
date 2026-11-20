-- module: parties, core — lý do: mọi thành viên công ty có đúng một định danh ngay khi vào công ty; trigger đặt trên core.memberships
-- Forms pick people from the directory; until now a member only got a party the first time commission needed one, so new members could not be chosen.
INSERT INTO parties.parties(tenant_id,kind,display_name,user_id)
SELECT m.tenant_id,'person',u.name,u.id FROM core.memberships m JOIN core.users u ON u.id=m.user_id
ON CONFLICT (tenant_id,user_id) DO NOTHING;
CREATE FUNCTION parties.ensure_for_membership() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
 INSERT INTO parties.parties(tenant_id,kind,display_name,user_id)
 SELECT NEW.tenant_id,'person',u.name,u.id FROM core.users u WHERE u.id=NEW.user_id
 ON CONFLICT (tenant_id,user_id) DO NOTHING;
 RETURN NEW;
END $$;
CREATE TRIGGER party_for_member AFTER INSERT ON core.memberships FOR EACH ROW EXECUTE FUNCTION parties.ensure_for_membership();
