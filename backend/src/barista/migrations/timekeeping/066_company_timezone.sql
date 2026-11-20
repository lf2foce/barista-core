-- module: timekeeping
-- 0169: khoá kỳ đã chốt xét THÁNG của giờ chấm theo múi giờ công ty (core.tz() — base/074) thay cho múi Việt Nam viết cứng. Công ty múi Việt Nam: cùng
-- kết quả. Thay thân hai hàm trigger (cùng chữ ký, cùng quyền) — không cần cửa sổ bảo trì.
CREATE OR REPLACE FUNCTION timekeeping.punches_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN
 IF timekeeping.month_closed(NEW.tenant_id, NEW.user_id, (NEW.at AT TIME ZONE core.tz())::date) THEN
  RAISE EXCEPTION 'Tháng này của người này đã chốt bảng công — điều chỉnh vào kỳ sau' USING ERRCODE = 'restrict_violation';
 END IF;
 IF NEW.method = 'import' AND NEW.user_id = NEW.created_by THEN
  RAISE EXCEPTION 'Không tự nhập giờ chấm cho chính mình' USING ERRCODE = 'check_violation';
 END IF;
 IF NEW.method = 'correction' AND NEW.user_id IS NOT NULL AND NEW.user_id = ANY (NEW.approved_by) THEN
  RAISE EXCEPTION 'Không tự duyệt giờ bổ sung của chính mình' USING ERRCODE = 'check_violation';
 END IF;
 IF NEW.method = 'web' AND NEW.user_id IS DISTINCT FROM NEW.created_by THEN
  RAISE EXCEPTION 'Chấm công trên app là của chính người chấm' USING ERRCODE = 'check_violation';
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION timekeeping.punch_reviews_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE r record;
BEGIN
 SELECT p.user_id, p.at INTO r FROM timekeeping.punches p WHERE p.tenant_id = NEW.tenant_id AND p.id = NEW.punch_id;
 IF r.user_id IS NOT NULL AND r.user_id = NEW.reviewed_by THEN
  RAISE EXCEPTION 'Không tự xét giờ chấm của chính mình — nhờ quản lý khác' USING ERRCODE = 'check_violation';
 END IF;
 IF timekeeping.month_closed(NEW.tenant_id, r.user_id, (r.at AT TIME ZONE core.tz())::date) THEN
  RAISE EXCEPTION 'Tháng này của người này đã chốt bảng công' USING ERRCODE = 'restrict_violation';
 END IF;
 RETURN NEW;
END $$;
