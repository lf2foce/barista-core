-- module: core — lý do: phạm vi "unit" = các đội người đó THAM GIA (0125 §A), khác "team" = đội làm trưởng + cây con (0062).
-- Chỉ THÊM: không dòng quyền nào đổi, người đang dùng phạm vi own / team / all thấy đúng những dòng như trước. Không vào deployment.json:
-- bản cũ chạy song song vẫn gọi core.can_see với own / team / all như cũ; nó chưa biết "unit" nhưng cũng chưa dòng quyền nào mang "unit"
-- (bộ mặc định không dùng), và nếu quản trị đã đặt thì bản cũ không đặt biến phiên mới ⇒ hàm trả đội rỗng ⇒ như "own" (đóng, không mở rộng).

ALTER TABLE core.grants DROP CONSTRAINT grants_scope_check;
ALTER TABLE core.grants ADD CONSTRAINT grants_scope_check CHECK (scope = ANY (ARRAY['own'::text, 'unit'::text, 'team'::text, 'all'::text]));

-- Đội người này THAM GIA (member_teams của Principal, nạp bằng kernel/teams.member_of), đặt vào biến phiên cùng lúc với app.teams.
CREATE FUNCTION core.my_units() RETURNS uuid[]
    LANGUAGE sql STABLE
    AS $$ SELECT coalesce(nullif(current_setting('app.member_teams', true), '')::uuid[], '{}'::uuid[]) $$;

-- Một hàm thấy dòng cho cả đọc lẫn ghi: thêm đúng một nhánh. Nhánh own / team / all giữ nguyên chữ.
CREATE OR REPLACE FUNCTION core.can_see(resource text, owner uuid, team uuid) RETURNS boolean
    LANGUAGE sql STABLE
    AS $$
    SELECT CASE core.scope(resource)
        WHEN 'all'  THEN true
        WHEN 'team' THEN (owner IS NOT NULL AND owner = core.me()) OR (team IS NOT NULL AND team = ANY(core.my_teams()))
        WHEN 'unit' THEN (owner IS NOT NULL AND owner = core.me()) OR (team IS NOT NULL AND team = ANY(core.my_units()))
        WHEN 'own'  THEN owner IS NOT NULL AND owner = core.me()
        ELSE false
    END
$$;
