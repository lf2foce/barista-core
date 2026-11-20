-- module: core — lý do: 0144 mục 4 — TÁCH BẠCH NHIỆM VỤ ép ở DB. "Người xin không tự duyệt; một người không duyệt hai bước của cùng phiếu" (0029)
-- tới nay chỉ ép bằng Python (approvals.resolve_step loại requester khỏi người duyệt; core.decide kiểm approver_ids). Trigger này là hàng rào cuối,
-- không phụ thuộc đường gọi: mỗi quyết định mới ghi vào decisions phải do người KHÁC người xin và KHÁC mọi người đã quyết trước đó.
-- Ngoại lệ một chủ tài khoản (0029) KHÔNG đi qua bảng này (lệnh chạy ngay, không lập phiếu) nên trigger không cần cửa riêng.
-- Chỉ thêm trigger; dòng cũ không bị quét; bản cũ chạy song song vẫn ghi đúng (nó vốn không cho người xin tự duyệt).
CREATE OR REPLACE FUNCTION core.approvals_separation_of_duties() RETURNS trigger
    LANGUAGE plpgsql AS $$
DECLARE
    n_old integer := coalesce(jsonb_array_length(OLD.decisions), 0);
    n_new integer := coalesce(jsonb_array_length(NEW.decisions), 0);
    i integer;
    who uuid;
    earlier uuid[];
BEGIN
    IF n_new < n_old THEN
        RAISE EXCEPTION 'phiếu duyệt %: không xoá quyết định đã ghi', NEW.id USING ERRCODE = 'check_violation';
    END IF;
    IF n_new > n_old + 1 THEN
        RAISE EXCEPTION 'phiếu duyệt %: mỗi lần ghi chỉ thêm MỘT quyết định', NEW.id USING ERRCODE = 'check_violation';
    END IF;
    -- những quyết định cũ phải giữ nguyên người quyết — kiểm ở MỌI lần đổi cột, kể cả không thêm quyết định (đánh dấu stale ghi thêm trường khác thì được)
    FOR i IN 0 .. n_old - 1 LOOP
        IF (OLD.decisions->i->>'user_id') IS DISTINCT FROM (NEW.decisions->i->>'user_id') THEN
            RAISE EXCEPTION 'phiếu duyệt %: không được đổi người đã quyết ở bước %', NEW.id, i USING ERRCODE = 'check_violation';
        END IF;
    END LOOP;
    IF n_new = n_old THEN
        RETURN NEW;   -- không thêm quyết định (stale ghi thêm trường vào quyết định cuối, đổi trạng thái…)
    END IF;
    who := (NEW.decisions->(n_new - 1)->>'user_id')::uuid;
    IF who IS NULL THEN
        RAISE EXCEPTION 'phiếu duyệt %: quyết định thiếu người quyết', NEW.id USING ERRCODE = 'check_violation';
    END IF;
    IF who = NEW.requested_by THEN
        RAISE EXCEPTION 'phiếu duyệt %: người xin không tự duyệt phiếu của mình (0029)', NEW.id USING ERRCODE = 'check_violation';
    END IF;
    SELECT coalesce(array_agg((d->>'user_id')::uuid), '{}') INTO earlier FROM jsonb_array_elements(OLD.decisions) d;
    IF who = ANY(earlier) THEN
        RAISE EXCEPTION 'phiếu duyệt %: một người không duyệt hai bước của cùng phiếu (0029)', NEW.id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS approvals_separation_of_duties ON core.approvals;
CREATE TRIGGER approvals_separation_of_duties BEFORE UPDATE OF decisions ON core.approvals
    FOR EACH ROW EXECUTE FUNCTION core.approvals_separation_of_duties();

-- Hàng rào "bất kể đường vào" (review 09/11): (1) INSERT mang sẵn decisions thì vượt trigger UPDATE ⇒ phiếu mới phải chưa có quyết định;
-- (2) NGƯỜI XIN, công ty, cách chạy bất biến sau khi tạo — đổi requested_by rồi để người xin cũ duyệt là lách tách bạch nhiệm vụ.
-- KHÔNG khoá command / input / chain: migration hợp lệ vẫn đổi tên lệnh và khoá đầu vào của phiếu đang chờ (đổi khoá gói 049, cột offering 0110);
-- bảo vệ nội dung phiếu là việc của căn cứ (basis, 0071) và nhật ký, không phải của trigger này.
CREATE OR REPLACE FUNCTION core.approvals_immutable() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.decisions IS NOT NULL AND jsonb_array_length(NEW.decisions) > 0 THEN
            RAISE EXCEPTION 'phiếu duyệt %: phiếu mới không được mang sẵn quyết định', NEW.id USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.requested_by IS DISTINCT FROM OLD.requested_by OR NEW.run_as IS DISTINCT FROM OLD.run_as OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
        RAISE EXCEPTION 'phiếu duyệt %: người xin / công ty / cách chạy không đổi được sau khi tạo (0029)', NEW.id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS approvals_immutable ON core.approvals;
CREATE TRIGGER approvals_immutable BEFORE INSERT OR UPDATE OF requested_by, run_as, tenant_id ON core.approvals
    FOR EACH ROW EXECUTE FUNCTION core.approvals_immutable();

-- 0143 lát 2 (review 09/11): cửa sổ "đảo" là dữ liệu công ty, không phải hằng số code — người sửa / huỷ việc trợ lý tự làm trong ngần này ngày thì tính là sai.
ALTER TABLE core.agent_trust ADD COLUMN reverse_days integer NOT NULL DEFAULT 7 CHECK (reverse_days BETWEEN 1 AND 30);
