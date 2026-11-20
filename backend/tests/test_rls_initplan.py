"""N11 — chính sách RLS tính biến phiên MỘT lần mỗi câu (InitPlan), không ở từng dòng (base/054_rls_initplan.sql, đo:
ghi chú nội bộ (barista-docs)). Gọi trần ``core.me()`` / ``core.can_see(…)`` trong chữ chính sách vẫn ĐÚNG nghĩa nhưng chậm 10–150 lần
ở quy mô vài chục nghìn dòng — test này chặn chính sách mới viết kiểu cũ (viết ``(SELECT core.me())``, hoặc tạo bảng bằng ``core.secure_*``)."""

import re
import uuid
from dataclasses import replace
from typing import Any

from barista.db import db
from tests.conftest import Who, owner_val

# hàm biến phiên: chỉ phụ thuộc biến phiên ⇒ trong chính sách phải nằm trong (SELECT …). pg_get_expr in "( SELECT core.me() AS me)".
BARE = re.compile(r"(?<!SELECT )core\.(tenant|me|link_ref|is_system|my_email|my_teams|my_units|scope|can_see)\(")


async def _policies() -> list[tuple[str, str, str]]:
    rows: list[list[str]] = await owner_val(
        """SELECT coalesce(json_agg(json_build_array(p.polrelid::regclass::text || ' ' || p.polname,
                                                     coalesce(pg_get_expr(p.polqual, p.polrelid), ''),
                                                     coalesce(pg_get_expr(p.polwithcheck, p.polrelid), ''))), '[]') FROM pg_policy p""")
    return [(r[0], r[1], r[2]) for r in rows]


async def test_no_policy_reads_session_variables_per_row() -> None:
    bad = [f"{name}: {m.group(0)}…" for name, q, w in await _policies() for m in [BARE.search(q) or BARE.search(w)] if m]
    assert not bad, "chính sách gọi biến phiên trần (viết (SELECT core.x()) — base/054_rls_initplan.sql):\n" + "\n".join(bad)


async def test_every_policy_calling_subject_visible_also_checks_the_pack_is_on() -> None:
    """058: ``core.subject_visible`` không còn kiểm "gói của loại đối tượng có bật" — việc đó nằm ở chính sách (InitPlan ``core.kinds_off()``). Chính sách
    thêm SAU này mà gọi ``subject_visible`` trần sẽ làm tắt gói không còn chặn dữ liệu của gói đó. Migration 058 chỉ kiểm lúc chạy; test này kiểm mãi mãi."""
    bad = [name for name, q, w in await _policies()
           if ("core.subject_visible(" in q and "kinds_off" not in q) or ("core.subject_visible(" in w and "kinds_off" not in w)]
    assert not bad, "chính sách gọi core.subject_visible thiếu vế NOT (kind = ANY(core.kinds_off())) (base/058_kinds_off.sql):\n" + "\n".join(bad)


async def test_rewrite_is_idempotent_on_every_policy() -> None:
    """core.initplan_policies chạy lại (migration 054 của từng gói, bản cài mới lẫn nâng cấp) không được đổi chữ thêm lần nào."""
    changed = await owner_val(
        """SELECT coalesce(string_agg(polrelid::regclass::text || ' ' || polname, ', '), '') FROM pg_policy
           WHERE core.rls_initplan(pg_get_expr(polqual, polrelid)) IS DISTINCT FROM pg_get_expr(polqual, polrelid)
              OR core.rls_initplan(pg_get_expr(polwithcheck, polrelid)) IS DISTINCT FROM pg_get_expr(polwithcheck, polrelid)""")
    assert changed == ""


async def test_can_see_rewrite_keeps_every_scope_branch() -> None:
    out = await owner_val("SELECT core.rls_initplan(%s)", ("tenant_id = core.tenant() AND core.can_see('crm', owner_id, team_id)",))
    for part in ("(SELECT core.scope('crm'))", "WHEN 'all' THEN true", "WHEN 'team'", "(SELECT core.my_teams())::uuid[]", "WHEN 'unit'",
                 "(SELECT core.my_units())::uuid[]", "WHEN 'own' THEN (owner_id IS NOT NULL AND owner_id = (SELECT core.me()))", "ELSE false"):
        assert part in out, (part, out)
    assert "core.can_see" not in out


async def test_subject_visible_is_inlinable_sql_compiled_from_registry() -> None:
    """F1: core.subject_visible là SQL (không plpgsql / DEFINER — mới inline được), mỗi loại đã đăng ký một nhánh."""
    lang, definer, body = (await owner_val(
        """SELECT json_build_array(l.lanname, p.prosecdef, p.prosrc) FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang
           WHERE p.oid = 'core.subject_visible(text, uuid)'::regprocedure"""))
    assert (lang, definer) == ("sql", False)
    for kind, fn in await owner_val("SELECT json_agg(json_build_array(kind, visible_fn)) FROM core.subject_kinds"):
        assert f"WHEN '{kind}' THEN coalesce({fn}(id), false)" in body


# Thân GỐC của core.subject_visible (plpgsql + EXECUTE động, bản gốc 00_core.sql) — giữ ở đây để so: bản sinh từ sổ loại phải cho ĐÚNG kết quả này.
OLD_SUBJECT_VISIBLE = """CREATE FUNCTION pg_temp.subject_visible_old(kind text, id uuid) RETURNS boolean LANGUAGE plpgsql STABLE AS $f$
DECLARE
    fn TEXT;
    ok BOOLEAN;
BEGIN
    SELECT s.visible_fn INTO fn FROM core.subject_kinds s WHERE s.kind = subject_visible_old.kind;
    IF fn IS NULL OR NOT core.subject_on(kind) THEN
        RETURN false;
    END IF;
    EXECUTE format('SELECT %s($1)', fn) INTO ok USING id;
    RETURN coalesce(ok, false);
END $f$"""


async def _subject_pairs() -> list[tuple[str | None, str]]:
    """(loại, mã) để so: mọi cặp subject_kind / subject_id đang có trong DB (trao đổi, hồ sơ, bộ nhớ, cuộc gọi…), mã thật của bảng chủ mỗi loại
    (đoán tên bảng từ khoá loại — chỉ trong test), một mã ngẫu nhiên mỗi loại, loại lạ / rỗng / NULL."""
    sql = await owner_val(
        """SELECT string_agg(format('SELECT %L::text AS k, %I::text AS kind, %I::text AS id FROM %I.%I', 1, 'subject_kind', 'subject_id',
                                    c.table_schema, c.table_name), ' UNION ')
           FROM information_schema.columns c
           WHERE c.column_name = 'subject_kind' AND EXISTS (SELECT 1 FROM information_schema.columns d WHERE d.table_schema = c.table_schema
                 AND d.table_name = c.table_name AND d.column_name = 'subject_id' AND d.data_type = 'uuid')
             AND c.table_schema NOT IN ('pg_catalog', 'information_schema')""")
    pairs: set[tuple[str | None, str]] = set()
    async with db.owner.connection() as conn:
        for r in await (await conn.execute(sql)).fetchall():
            if r["id"] is not None:
                pairs.add((r["kind"], r["id"]))
        kinds = [r["kind"] for r in await (await conn.execute("SELECT kind FROM core.subject_kinds ORDER BY kind")).fetchall()]
        for kind in kinds:
            schema, thing = kind.split(".")
            for table in (f"{thing}s", f"{thing}es", f"{thing[:-1]}ies", thing):
                if (await (await conn.execute("SELECT to_regclass(%s) AS t", (f"{schema}.{table}",))).fetchone() or {}).get("t"):
                    for r in await (await conn.execute(f'SELECT id::text AS id FROM {schema}."{table}" ORDER BY id LIMIT 25')).fetchall():  # type: ignore[arg-type]
                        pairs.add((kind, r["id"]))
                    break
            pairs.add((kind, str(uuid.uuid4())))
    pairs |= {("zz.none", str(uuid.uuid4())), ("", str(uuid.uuid4())), (None, str(uuid.uuid4()))}
    return sorted(pairs, key=lambda x: (x[0] or "", x[1]))


async def test_compiled_subject_visible_answers_exactly_like_the_old_function(who: Who) -> None:
    """F1 là đường quyền (ai đọc được trao đổi / bộ nhớ / cuộc gọi của một đối tượng): với MỌI loại đã đăng ký và nhiều kiểu người (giám đốc, quản
    trị, trưởng nhóm phạm vi team, cùng người đó phạm vi unit, sale chỉ của mình, lượt trợ lý, nhân viên không quyền nghiệp vụ, người công ty
    khác), hàm sinh ra trả ĐÚNG như hàm gốc ở từng cặp (loại, mã) — kể cả loại thuộc gói công ty đang tắt, loại lạ, mã không tồn tại."""
    pairs = await _subject_pairs()
    kinds = {k for k, _ in pairs}
    assert set(await owner_val("SELECT array_agg(kind) FROM core.subject_kinds")) <= kinds
    lead = await who("truonga")
    people = {"giám đốc": await who("giamdoc"), "quản trị": await who("admin"), "trưởng nhóm (team)": lead,
              "trưởng nhóm (unit)": replace(lead, grants={r: replace(g, scope="unit" if g.scope == "team" else g.scope) for r, g in lead.grants.items()}),
              "sale (own)": await who("sale1"), "trợ lý của sale": (await who("sale1")).as_agent(), "nhân viên HCNS": await who("nhansu"),
              "công ty khác": await who("sale1", other=True)}
    seen_true = 0
    # hàm gốc tạo bằng owner trong schema thật (057 thu hồi quyền TEMP của barista_app ⇒ app không tạo được pg_temp.*); xoá sau khi so
    async with db.owner.connection() as oc:
        await oc.execute(OLD_SUBJECT_VISIBLE.replace("pg_temp.subject_visible_old", "core.zz_subject_visible_old").replace("subject_visible_old.kind", "zz_subject_visible_old.kind"))
    try:
        for label, p in people.items():
            async with db.tenant_tx(p, rollback=True) as conn:
                cur = await conn.execute(
                    """SELECT x.kind, x.id, coalesce(NOT (x.kind = ANY ((SELECT core.kinds_off())::text[])), true) AND core.subject_visible(x.kind, x.id::uuid) AS new,
                              core.zz_subject_visible_old(x.kind, x.id::uuid) AS old
                       FROM unnest(%s::text[], %s::text[]) AS x(kind, id)""", ([k for k, _ in pairs], [i for _, i in pairs]))
                rows = await cur.fetchall()
            diff = [(r["kind"], r["id"], r["old"], r["new"]) for r in rows if r["new"] is not r["old"]]
            assert not diff, (label, diff[:5])
            seen_true += sum(1 for r in rows if r["new"])
    finally:
        async with db.owner.connection() as oc:
            await oc.execute("DROP FUNCTION IF EXISTS core.zz_subject_visible_old(text, uuid)")
    assert seen_true > 0   # có cặp thấy được ⇒ phép so không rỗng nghĩa


async def test_subject_kinds_trigger_recompiles_on_add_and_remove() -> None:
    """Gói thêm / bỏ một loại đối tượng (INSERT / DELETE core.subject_kinds trong migration của gói) ⇒ hàm sinh lại ngay, cùng giao dịch."""
    async def body(conn: Any) -> str:
        row = await (await conn.execute("SELECT prosrc FROM pg_proc WHERE oid = 'core.subject_visible(text, uuid)'::regprocedure")).fetchone()
        assert row is not None
        return str(row["prosrc"])

    async with db.owner.connection() as conn, conn.transaction(force_rollback=True):
        await conn.execute("INSERT INTO core.subject_kinds (kind, visible_fn) VALUES ('zz_test.thing', 'core.zz_none')")
        assert "WHEN 'zz_test.thing' THEN coalesce(core.zz_none(id), false)" in await body(conn)
        await conn.execute("UPDATE core.subject_kinds SET visible_fn = 'core.zz_other' WHERE kind = 'zz_test.thing'")
        assert "coalesce(core.zz_other(id), false)" in await body(conn) and "core.zz_none" not in await body(conn)
        await conn.execute("DELETE FROM core.subject_kinds WHERE kind = 'zz_test.thing'")
        assert "zz_test.thing" not in await body(conn)
        assert "WHEN 'talk.room' THEN" in await body(conn)


async def test_new_tables_get_initplan_policies_from_secure_procedures() -> None:
    """Gói thêm sau này tạo bảng bằng core.secure_table / secure_child / secure_tenant ⇒ chính sách đã ở dạng InitPlan, KHÔNG cần nhớ gọi
    core.initplan_policies (chỉ chính sách viết tay mới phải tự viết (SELECT …) — test đầu file chặn)."""
    async with db.owner.connection() as conn, conn.transaction(force_rollback=True):
        await conn.execute("""CREATE SCHEMA zz_n11;
            CREATE TABLE zz_n11.parent (id uuid PRIMARY KEY, tenant_id uuid NOT NULL, owner_id uuid, team_id uuid);
            CREATE TABLE zz_n11.child (id uuid PRIMARY KEY, tenant_id uuid NOT NULL, parent_id uuid NOT NULL);
            CREATE TABLE zz_n11.flat (id uuid PRIMARY KEY, tenant_id uuid NOT NULL);
            CALL core.secure_table('zz_n11.parent', 'zz', 'owner_id', 'team_id');
            CALL core.secure_child('zz_n11.child', 'zz_n11.parent', 'parent_id');
            CALL core.secure_tenant('zz_n11.flat');""")
        cur = await conn.execute("""SELECT polname, coalesce(pg_get_expr(polqual, polrelid), '') AS q, coalesce(pg_get_expr(polwithcheck, polrelid), '') AS w
                                    FROM pg_policy WHERE polrelid::regclass::text LIKE 'zz_n11.%'""")
        rows = await cur.fetchall()
    assert len(rows) == 4
    for r in rows:
        assert not BARE.search(r["q"]) and not BARE.search(r["w"]), r
        assert "SELECT core." in r["q"]


async def test_kinds_off_says_exactly_what_subject_on_says(who: Who) -> None:
    """058: vế InitPlan ``NOT (kind = ANY(core.kinds_off()))`` trả đúng như ``core.subject_on(kind)`` cho mọi loại trong sổ, loại lạ, rỗng và NULL —
    kể cả khi một gói của công ty đang tắt (bật lại cuối test)."""
    sale = await who("sale1")
    probe = """SELECT k, core.subject_on(k) AS old, coalesce(NOT (k = ANY ((SELECT core.kinds_off())::text[])), true) AS new
               FROM (SELECT kind AS k FROM core.subject_kinds UNION ALL VALUES ('zz.unknown'), (''), (NULL)) s"""
    async with db.tenant_tx(sale, read_only=True) as conn:
        rows = await (await conn.execute(probe)).fetchall()
    assert rows and all(r["old"] is r["new"] for r in rows), [(r["k"], r["old"], r["new"]) for r in rows if r["old"] is not r["new"]]
    pack = await owner_val("SELECT pack FROM core.subject_kinds WHERE pack <> 'base' AND kind = 'talk.room'")
    assert pack
    await owner_val("DELETE FROM core.tenant_packs WHERE tenant_id = %s AND pack = %s RETURNING 1", (sale.tenant_id, pack))
    try:
        async with db.tenant_tx(sale, read_only=True) as conn:
            rows = await (await conn.execute(probe)).fetchall()
        assert any(r["k"] == "talk.room" and r["old"] is False for r in rows)
        assert all(r["old"] is r["new"] for r in rows), [(r["k"], r["old"], r["new"]) for r in rows if r["old"] is not r["new"]]
    finally:
        await owner_val("INSERT INTO core.tenant_packs (tenant_id, pack) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING 1", (sale.tenant_id, pack))


# Thân GỐC của kb.shelf_visible (bản 04_kb.sql) — giữ ở đây để so: tập kệ tính một lần (060) phải cho ĐÚNG kết quả này ở từng kệ, từng kiểu người.
OLD_SHELF_VISIBLE = """CREATE FUNCTION core.zz_shelf_visible_old(k text) RETURNS boolean LANGUAGE sql STABLE AS $f$
    SELECT EXISTS (
        SELECT 1 FROM kb.shelves s
        WHERE s.tenant_id = core.tenant() AND s.key = k AND (
            s.everyone
            OR core.me() = ANY (s.user_ids)
            OR EXISTS (SELECT 1 FROM core.memberships mb WHERE mb.tenant_id = core.tenant() AND mb.user_id = core.me() AND mb.active
                         AND mb.role = ANY (s.roles))
            OR EXISTS (
                WITH RECURSIVE up AS (
                    SELECT t.id, t.parent_id FROM core.teams t JOIN core.team_members tm ON tm.team_id = t.id
                    WHERE tm.tenant_id = core.tenant() AND tm.user_id = core.me()
                    UNION
                    SELECT p.id, p.parent_id FROM core.teams p JOIN up ON p.id = up.parent_id)
                SELECT 1 FROM up WHERE up.id = ANY (s.team_ids))))
$f$"""


async def test_visible_shelves_answers_exactly_like_the_old_shelf_rule(who: Who) -> None:
    """060: ``kb.visible_shelves()`` là nơi DUY NHẤT định nghĩa "đọc được kệ nào"; với mọi kệ (mở cho mọi người, theo vai, theo người, theo đội — kể cả
    đội con), kệ không tồn tại và NULL, nhiều kiểu người (kể cả trợ lý, người công ty khác), ``k = ANY(visible_shelves())`` và ``kb.shelf_visible(k)``
    trả đúng như thân hàm cũ. Kệ thử theo người / vai / đội tạo ngay trong test (bỏ khi xong)."""
    sale, lead, admin = await who("sale1"), await who("truonga"), await who("admin")
    tag = uuid.uuid4().hex[:8]
    keys = [f"zz_{tag}_user", f"zz_{tag}_role", f"zz_{tag}_team", f"zz_{tag}_none"]
    team = await owner_val("SELECT team_id FROM core.team_members WHERE user_id = %s LIMIT 1", (sale.user_id,))
    parent = await owner_val("SELECT parent_id FROM core.teams WHERE id = %s", (team,))
    async with db.owner.connection() as oc:
        await oc.execute(OLD_SHELF_VISIBLE)
        await oc.execute("""INSERT INTO kb.shelves (tenant_id, key, name, everyone, user_ids, roles, team_ids) VALUES
                                (%(t)s, %(k0)s, %(k0)s, false, %(u)s, '{}', '{}'), (%(t)s, %(k1)s, %(k1)s, false, '{}', '{director}', '{}'),
                                (%(t)s, %(k2)s, %(k2)s, false, '{}', '{}', %(g)s), (%(t)s, %(k3)s, %(k3)s, false, '{}', '{zz_nobody}', '{}')""",
                         {"t": sale.tenant_id, "u": [sale.user_id], "g": [parent or team], **{f"k{i}": k for i, k in enumerate(keys)}})
    try:
        people = {"sale": sale, "trưởng nhóm": lead, "quản trị": admin, "giám đốc": await who("giamdoc"), "HCNS": await who("nhansu"),
                  "trợ lý của sale": sale.as_agent(), "công ty khác": await who("sale1", other=True)}
        shelves = [str(k) for k in await owner_val("SELECT array_agg(key) FROM kb.shelves")] + ["zz_khong_co"]
        seen = 0
        for label, p in people.items():
            async with db.tenant_tx(p, read_only=True) as conn:
                rows = await (await conn.execute(
                    """SELECT k, core.zz_shelf_visible_old(k) AS old, coalesce(k = ANY ((SELECT kb.visible_shelves())::text[]), false) AS new,
                              kb.shelf_visible(k) AS fn FROM unnest(%s::text[] || ARRAY[NULL]::text[]) k""", (shelves,))).fetchall()
            bad = [(r["k"], r["old"], r["new"], r["fn"]) for r in rows if not (r["old"] is r["new"] is r["fn"])]
            assert not bad, (label, bad)
            seen += sum(1 for r in rows if r["new"])
        assert seen > 0
    finally:
        async with db.owner.connection() as oc:
            await oc.execute("DELETE FROM kb.shelves WHERE key = ANY(%s)", (keys,))
            await oc.execute("DROP FUNCTION IF EXISTS core.zz_shelf_visible_old(text)")


async def test_no_policy_checks_the_shelf_rule_per_row() -> None:
    """060: chính sách kiểm kệ bằng tập tính một lần (``(SELECT kb.visible_shelves())``); gọi ``kb.shelf_visible(cột)`` trong chính sách vẫn đúng nghĩa
    nhưng chạy lại luật đệ quy cây đội ở TỪNG dòng (đo: ~6.200 lần / lượt kb.search) — chặn chính sách mới viết kiểu cũ."""
    bad = [name for name, q, w in await _policies() if "kb.shelf_visible(" in q or "kb.shelf_visible(" in w]
    assert not bad, "chính sách gọi kb.shelf_visible theo dòng — dùng coalesce(cột = ANY ((SELECT kb.visible_shelves())::text[]), false):\n" + "\n".join(bad)


async def test_no_policy_checks_pack_on_per_row() -> None:
    """061 (và 058): "gói của đối tượng có bật không" trong chính sách dùng tập ``(SELECT core.kinds_off())`` tính một lần mỗi câu; gọi
    ``core.subject_on(cột)`` trong chính sách vẫn đúng nghĩa nhưng chạy một EXISTS core.tenant_packs ở TỪNG dòng (đo: ~3.000 lần / lượt màn việc)."""
    bad = [name for name, q, w in await _policies() if "core.subject_on(" in q or "core.subject_on(" in w]
    assert not bad, "chính sách gọi core.subject_on theo dòng — dùng coalesce(NOT (cột = ANY ((SELECT core.kinds_off())::text[])), true):\n" + "\n".join(bad)
