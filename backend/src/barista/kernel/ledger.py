"""Sổ cái ghi kép — cửa DUY NHẤT để ghi tiền. Module không INSERT vào schema ledger trực tiếp.

Quy ước dấu: dương = NỢ, âm = CÓ. Mỗi bút toán tổng 0 (DB kiểm lúc commit), chỉ thêm (DB chặn sửa / xoá), sửa sai =
bút toán đảo (``reverse``). ``key`` chống lặp: cùng key ⇒ trả bút toán cũ, không ghi lần hai.

HAI MỐC THỜI GIAN (0119): ``at`` = LÚC GHI (dấu vết kiểm toán, DB tự điền); ``posted_on`` = NGÀY HẠCH TOÁN (ngày của công ty — 0169) — mọi báo cáo, xuất MISA,
tập sổ cái cắt kỳ theo cột này. ``post`` bắt buộc ``on`` (không mặc định — mỗi chỗ ghi tiền chọn ngày có chủ đích), ngày sau hôm nay bị từ chối.
KHOÁ KỲ theo pháp nhân (``ledger.period_locks``, chỉ thêm, dòng mới nhất là mốc): trigger ``t_journals_period_lock`` chặn MỌI đường ghi có
``posted_on`` ≤ mốc, kể cả INSERT thẳng. Trigger lấy khoá advisory DÙNG CHUNG theo pháp nhân, ``lock_through`` / ``reopen_to`` lấy khoá RIÊNG cùng
khoá ⇒ mốc không tiến được khi một bút toán của pháp nhân đó đang ghi dở, và bút toán bắt đầu sau khi mốc tiến đọc mốc mới.

Tài khoản (mã ``loại[:chi tiết]``) do MODULE CỦA GÓI khai lúc nạp (``declare``) — kernel chỉ giữ CƠ CHẾ (ghi kép, cân bằng, đảo, chống lặp,
khoá hạn mức), không biết ngành nào có tài khoản gì. Tài khoản có chủ / theo đối tượng (``wallet:<người>``, ``held:<người nhận>``,
``deposit:<khoản cọc>``…) tạo bằng ``ensure_account`` lúc dùng lần đầu. Danh mục đang khai: ``ACCOUNTS``.
"""

import sys
from datetime import date
from typing import Any
from uuid import UUID

from psycopg import errors as pg

from barista.kernel.clock import today
from barista.kernel.errors import Denied, Invalid, NotFound
from barista.kernel.executor import Ctx

KINDS = ("asset", "liability", "equity", "revenue", "expense")
# Tài khoản cố định module đã khai (mã → tên, loại) — tạo trong sổ của công ty khi lần đầu ghi vào.
ACCOUNTS: dict[str, tuple[str, str]] = {}


# Ai khai tài khoản nào (0188): gốc mã Python của nơi gọi ``declare`` — ``barista.modules.<khoá>`` với module trong repo lõi, tên bản phân phối
# (``barista_pack_x``) với gói ngoài. Dùng để chặn gói này ghi chi phí / doanh thu vào tài khoản do gói KHÁC khai qua họ thao tác mở.
OWNERS: dict[str, set[str]] = {}
SHARED_ROOT = "barista.modules.acct"   # sơ đồ tài khoản nền của Tài chính: gói nào cũng dùng được
# Tài khoản CƠ CHẾ (0189): do một quy tắc của module tự ghi (điều chỉnh đầu kỳ, phí ứng theo chính sách…) — không phải mục người chọn khi phân loại
# khoản thu / chi. Module KHAI tài khoản tự đánh dấu (``declare(..., mechanism=…)``); nơi dựng danh mục không nêu mã của module khác.
MECHANISM: set[str] = set()


def code_root(name: str) -> str:
    parts = name.split(".")
    return ".".join(parts[:3]) if parts[:2] == ["barista", "modules"] and len(parts) > 2 else parts[0]


def declare(accounts: dict[str, tuple[str, str]], *, mechanism: tuple[str, ...] = ()) -> None:
    """Module khai tài khoản cố định của mình lúc nạp (như khai lệnh / tài nguyên). Khai trùng mã mà khác nghĩa ⇒ lỗi ngay lúc khởi động.
    Nơi gọi là CHỦ của tài khoản (``OWNERS``). ``mechanism``: các mã trong lần khai này là tài khoản cơ chế (``MECHANISM``) — không vào danh mục
    khoản thu / chi để người chọn."""
    root = code_root(sys._getframe(1).f_globals.get("__name__", ""))
    if stray := set(mechanism) - set(accounts):
        raise ValueError(f"tài khoản cơ chế phải nằm trong chính lần khai: {sorted(stray)}")
    for code, (name, kind) in accounts.items():
        if kind not in KINDS:
            raise ValueError(f"loại tài khoản lạ: {code} {kind}")
        if (old := ACCOUNTS.get(code)) is not None and old != (name, kind):
            raise ValueError(f"tài khoản {code} khai hai nghĩa: {old} / {(name, kind)}")
        ACCOUNTS[code] = (name, kind)
        OWNERS.setdefault(code, set()).add(root)
    MECHANISM.update(mechanism)


def usable_by(code: str, module: str) -> bool:
    """Module ``module`` (khoá) có được ghi vào tài khoản đã khai ``code`` qua họ thao tác mở không: tài khoản do chính gói đó khai (cùng gốc mã với
    các thao tác của module), hoặc thuộc sơ đồ nền của Tài chính. Không rõ module ⇒ không."""
    from barista.kernel import registry

    owners = OWNERS.get(code, set())
    if SHARED_ROOT in owners:
        return True
    roots = {code_root(op.handler.__module__) for name, op in registry.OPS.items() if name.startswith(module + ".")}
    return bool(roots & owners)


def accounts(kind: str) -> dict[str, str]:
    """Tài khoản cố định đã khai theo loại: {mã: tên} — vd ô chọn tài khoản chi phí của một khoản lương (0150)."""
    return {code: name for code, (name, k) in ACCOUNTS.items() if k == kind}


def _need_writer(ctx: Ctx) -> None:
    if ctx.p.scope("ledger") != "all":
        raise Denied("Chỉ kế toán hoặc tiến trình hệ thống được ghi sổ cái")


async def ensure_account(ctx: Ctx, code: str, name: str, kind: str, owner: UUID | None = None) -> UUID:
    _need_writer(ctx)
    if kind not in KINDS:
        raise Invalid(f"loại tài khoản lạ: {kind}")
    await ctx.exec(
        "INSERT INTO ledger.accounts (code, name, kind, owner_user_id) VALUES (%s, %s, %s, %s)"
        " ON CONFLICT (tenant_id, code) DO NOTHING", (code, name, kind, owner))
    aid = await ctx.val("SELECT id FROM ledger.accounts WHERE code = %s", (code,))
    assert aid is not None
    return aid


async def post(ctx: Ctx, *, key: str, memo: str, source: str, lines: list[tuple[str, int]], on: date, ref: str = "",
               limits: dict[str, tuple[int | None, int | None]] | None = None, reverses: UUID | None = None,
               entity: UUID | None = None) -> UUID:
    """Ghi một bút toán. ``on``: NGÀY HẠCH TOÁN (0119) — bắt buộc, không sau hôm nay; kỳ của pháp nhân đã khoá tới ngày đó ⇒ DB từ chối (báo rõ,
    không tự dời ngày). Trùng ``key`` ⇒ trả bút toán cũ, ngày đã ghi KHÔNG đổi (sửa ngày = đảo rồi ghi lại).
    ``limits``: {mã tài khoản: (số dư tối thiểu, tối đa) SAU khi ghi} — chặn rút quá / vượt hạn mức,
    kiểm dưới khoá theo tài khoản nên hai yêu cầu đồng thời không cùng lọt (số dư CẢ CÔNG TY — mọi pháp nhân).
    ``entity``: PHÁP NHÂN của bút toán (0099) — trống = pháp nhân mặc định; cả bút toán thuộc MỘT pháp nhân ⇒ sổ từng pháp nhân tự cân."""
    _need_writer(ctx)
    if on > today():
        raise Invalid(f"Ngày hạch toán {on:%d/%m/%Y} sau hôm nay — chưa ghi trước cho kỳ tương lai")
    lines = [(c, a) for c, a in lines if a != 0]
    if len(lines) < 2 or sum(a for _, a in lines) != 0:
        raise Invalid(f"Bút toán “{memo}” lệch nợ / có")
    existing = await ctx.val("SELECT id FROM ledger.journals WHERE idempotency_key = %s", (key,))
    if existing:
        return existing
    codes = sorted({c for c, _ in lines})
    for c in codes:
        if c in ACCOUNTS:
            await ensure_account(ctx, c, *ACCOUNTS[c])
    accounts = {r["code"]: r["id"] for r in await ctx.all("SELECT id, code FROM ledger.accounts WHERE code = ANY(%s)", (codes,))}
    if missing := [c for c in codes if c not in accounts]:
        raise NotFound(f"Chưa có tài khoản sổ cái: {', '.join(missing)}")
    for code, (lo, hi) in (limits or {}).items():
        aid = accounts[code]
        await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (str(aid),))
        bal = await ctx.val("SELECT coalesce(sum(amount_vnd), 0)::bigint AS b FROM ledger.postings WHERE account_id = %s", (aid,))
        after = int(bal) + sum(a for c, a in lines if c == code)
        if (lo is not None and after < lo) or (hi is not None and after > hi):
            raise Invalid(f"Vượt hạn mức tài khoản {code}")
    try:
        jid = await ctx.val(
            "INSERT INTO ledger.journals (memo, source, source_ref, idempotency_key, reverses, created_by, entity_id, posted_on)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"   # pháp nhân trống ⇒ trigger điền mặc định của công ty; rồi trigger khoá kỳ kiểm ngày
            " ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING id",
            (memo, source, ref, key, reverses, ctx.p.user_id, entity, on))
    except pg.RaiseException as e:   # kỳ đã khoá (trigger) ⇒ lỗi nghiệp vụ, nói rõ mốc
        raise Invalid(e.diag.message_primary or str(e)) from e
    if jid is None:  # yêu cầu song song cùng key vừa ghi xong
        return await ctx.val("SELECT id FROM ledger.journals WHERE idempotency_key = %s", (key,))
    async with ctx.conn.cursor() as cur:
        await cur.executemany(
            "INSERT INTO ledger.postings (journal_id, account_id, amount_vnd, memo) VALUES (%s, %s, %s, %s)",
            [(jid, accounts[c], a, memo) for c, a in lines])
    return jid


async def reverse(ctx: Ctx, *, original_key: str, key: str, memo: str, source: str, on: date | None = None) -> UUID | None:
    """Bút toán đảo của bút toán ``original_key`` (không có ⇒ None). Đảo hai lần cùng ``key`` ⇒ một lần. ``on``: ngày hạch toán của bút toán
    đảo — trống = HÔM NAY (kỳ gốc đã khoá vẫn đảo được, số nằm ở kỳ hiện tại); được chọn ngày của bút toán gốc nếu kỳ đó còn mở, không trước ngày gốc."""
    _need_writer(ctx)
    orig = await ctx.one("SELECT id, source_ref, entity_id, posted_on FROM ledger.journals WHERE idempotency_key = %s", (original_key,))
    if orig is None:
        return None
    day = on or today()
    if day < orig["posted_on"]:
        raise Invalid(f"Bút toán đảo không được trước ngày hạch toán của bút toán gốc ({orig['posted_on']:%d/%m/%Y})")
    lines = await ctx.all(
        "SELECT a.code, p.amount_vnd FROM ledger.postings p JOIN ledger.accounts a ON a.id = p.account_id WHERE p.journal_id = %s",
        (orig["id"],))
    return await post(ctx, key=key, memo=memo, source=source, ref=orig["source_ref"], on=day,
                      lines=[(r["code"], -r["amount_vnd"]) for r in lines], reverses=orig["id"], entity=orig["entity_id"])   # đảo cùng pháp nhân


async def journal(ctx: Ctx, key: str) -> dict[str, Any] | None:
    """Bút toán theo khoá chống lặp: {id, posted_on, entity_id, source_ref, lines: [(mã tài khoản, số tiền)]} — None nếu chưa có. Module ghi sổ đọc lại
    bút toán của chính nó (so khi gọi lại, đảo có điều kiện) mà không chạm bảng sổ cái."""
    j = await ctx.one("SELECT id, posted_on, entity_id, source_ref FROM ledger.journals WHERE idempotency_key = %s", (key,))
    if j is None:
        return None
    rows = await ctx.all("SELECT a.code, p.amount_vnd FROM ledger.postings p JOIN ledger.accounts a ON a.id = p.account_id WHERE p.journal_id = %s",
                         (j["id"],))
    return {**j, "lines": [(r["code"], int(r["amount_vnd"])) for r in rows]}


async def balance(ctx: Ctx, code: str) -> int:
    """Số dư theo quy ước dấu (dương = nợ). Tài khoản không thấy (RLS) hoặc chưa có ⇒ 0."""
    v = await ctx.val(
        "SELECT coalesce(sum(p.amount_vnd), 0)::bigint AS b FROM ledger.postings p JOIN ledger.accounts a ON a.id = p.account_id"
        " WHERE a.code = %s", (code,))
    return int(v or 0)


async def prefix_balance(ctx: Ctx, prefix: str) -> int:
    """Tổng số dư mọi tài khoản ``prefix:…`` (vd mọi tài khoản tiền thu hộ ``held:*``)."""
    v = await ctx.val(
        "SELECT coalesce(sum(p.amount_vnd), 0)::bigint FROM ledger.postings p JOIN ledger.accounts a ON a.id = p.account_id"
        " WHERE a.code LIKE %s || ':%%'", (prefix,))
    return int(v or 0)


async def statement(ctx: Ctx, code: str, limit: int = 200) -> list[dict[str, Any]]:
    """Sổ chi tiết một tài khoản, mới nhất trước theo NGÀY HẠCH TOÁN (``posted_on``); ``at`` = lúc ghi."""
    return await ctx.all(
        """SELECT j.posted_on, p.at, p.memo, p.amount_vnd FROM ledger.postings p JOIN ledger.accounts a ON a.id = p.account_id
           JOIN ledger.journals j ON j.id = p.journal_id
           WHERE a.code = %s ORDER BY j.posted_on DESC, p.at DESC, p.id DESC LIMIT %s""", (code, limit))


async def owner_balances(ctx: Ctx, prefix: str) -> list[dict[str, Any]]:
    """Số dư các tài khoản có chủ theo tiền tố (vd 'wallet'): [{owner_user_id, name, balance}] — RLS: người phạm vi own
    chỉ nhận dòng của mình."""
    return await ctx.all(
        """SELECT a.owner_user_id, u.name, coalesce(sum(p.amount_vnd), 0)::bigint AS balance
           FROM ledger.accounts a JOIN core.users u ON u.id = a.owner_user_id LEFT JOIN ledger.postings p ON p.account_id = a.id
           WHERE a.code LIKE %s || ':%%' GROUP BY a.owner_user_id, u.name ORDER BY u.name""", (prefix,))


async def balances(ctx: Ctx, entity: UUID | None = None) -> list[dict[str, Any]]:
    """Số dư mọi tài khoản — cả công ty (gộp mọi pháp nhân) hoặc MỘT pháp nhân."""
    return await ctx.all(
        """SELECT a.code, a.name, a.kind, coalesce(sum(p.amount_vnd) FILTER (WHERE %(e)s::uuid IS NULL OR j.entity_id = %(e)s), 0)::bigint AS balance
           FROM ledger.accounts a LEFT JOIN ledger.postings p ON p.account_id = a.id LEFT JOIN ledger.journals j ON j.id = p.journal_id
           GROUP BY a.id ORDER BY a.kind, a.code""", {"e": entity})


async def journal_lines(ctx: Ctx, date_from: Any, date_to: Any, entity: UUID | None = None) -> list[dict[str, Any]]:
    """Các dòng bút toán có NGÀY HẠCH TOÁN trong kỳ (0119 — không theo lúc ghi) — đầu vào cho xuất MISA (mỗi pháp nhân một dữ liệu MISA ⇒ lọc
    ``entity``)."""
    return await ctx.all(
        """SELECT j.id AS journal_id, j.posted_on AS day, j.memo, j.source, j.source_ref,
                  a.code, a.name AS account_name, p.amount_vnd, j.entity_id
           FROM ledger.journals j JOIN ledger.postings p ON p.journal_id = j.id JOIN ledger.accounts a ON a.id = p.account_id
           WHERE j.posted_on BETWEEN %(f)s AND %(t)s AND (%(e)s::uuid IS NULL OR j.entity_id = %(e)s)
           ORDER BY j.posted_on, j.at, j.id, p.amount_vnd DESC""", {"f": date_from, "t": date_to, "e": entity})


async def journal_page(ctx: Ctx, date_from: date, date_to: date, entity: UUID | None, after: tuple[date, UUID] | None, limit: int) -> list[dict[str, Any]]:
    """Dòng bút toán của tối đa ``limit`` BÚT TOÁN có ngày hạch toán trong kỳ, sau con trỏ ``after`` = (ngày, mã bút toán) — trang đủ bút toán cho
    hợp đồng đọc sổ cái của gói (0245): một bút toán không bị cắt giữa hai trang. Thứ tự (ngày hạch toán, mã bút toán)."""
    return await ctx.all(
        """WITH js AS (SELECT j.id, j.posted_on FROM ledger.journals j
                       WHERE j.posted_on BETWEEN %(f)s AND %(t)s AND (%(e)s::uuid IS NULL OR j.entity_id = %(e)s)
                         AND (%(cd)s::date IS NULL OR (j.posted_on, j.id) > (%(cd)s::date, %(cj)s::uuid))
                       ORDER BY j.posted_on, j.id LIMIT %(n)s)
           SELECT j.id AS journal_id, j.posted_on, j.entity_id, j.memo, j.source, j.source_ref,
                  a.code, a.name, a.kind, a.owner_user_id, p.amount_vnd
           FROM js JOIN ledger.journals j ON j.id = js.id JOIN ledger.postings p ON p.journal_id = j.id JOIN ledger.accounts a ON a.id = p.account_id
           ORDER BY js.posted_on, js.id, p.amount_vnd DESC, p.id""",
        {"f": date_from, "t": date_to, "e": entity, "cd": after[0] if after else None, "cj": after[1] if after else None, "n": limit})


async def balances_on(ctx: Ctx, on: date, entity: UUID | None = None) -> list[dict[str, Any]]:
    """Số dư theo (pháp nhân, tài khoản) gồm mọi bút toán có ngày hạch toán ≤ ``on``; chỉ dòng khác 0 (0245)."""
    return await ctx.all(
        """SELECT j.entity_id, a.code, a.name, a.kind, a.owner_user_id, sum(p.amount_vnd)::bigint AS balance
           FROM ledger.journals j JOIN ledger.postings p ON p.journal_id = j.id JOIN ledger.accounts a ON a.id = p.account_id
           WHERE j.posted_on <= %(on)s AND (%(e)s::uuid IS NULL OR j.entity_id = %(e)s)
           GROUP BY j.entity_id, a.id HAVING sum(p.amount_vnd) <> 0
           ORDER BY a.kind, a.code""", {"on": on, "e": entity})


async def month_totals(ctx: Ctx, date_from: date, date_to: date, kinds: tuple[str, ...], entity: UUID | None = None) -> list[dict[str, Any]]:
    """Phát sinh ròng (dương = nợ) theo (pháp nhân, tháng của NGÀY HẠCH TOÁN, tài khoản) cho các loại tài khoản ``kinds`` — đầu vào báo cáo kết quả
    kinh doanh (0170): số chỉ từ sổ cái. Tháng là ``YYYY-MM`` của ``posted_on`` (ngày của công ty — 0169), không theo lúc ghi."""
    bad = set(kinds) - set(KINDS)
    if bad:
        raise Invalid(f"loại tài khoản lạ: {', '.join(sorted(bad))}")
    return await ctx.all(
        """SELECT j.entity_id, to_char(j.posted_on, 'YYYY-MM') AS month, a.code, a.name, a.kind, sum(p.amount_vnd)::bigint AS amount_vnd
           FROM ledger.journals j JOIN ledger.postings p ON p.journal_id = j.id JOIN ledger.accounts a ON a.id = p.account_id
           WHERE j.posted_on BETWEEN %(f)s AND %(t)s AND a.kind = ANY(%(k)s) AND (%(e)s::uuid IS NULL OR j.entity_id = %(e)s)
           GROUP BY 1, 2, 3, 4, 5 ORDER BY 2, 5, 3""", {"f": date_from, "t": date_to, "k": list(kinds), "e": entity})


async def accounts_active_through(ctx: Ctx, codes: list[str], entity: UUID, through: date) -> set[str]:
    """Trong ``codes``: tài khoản đã có bút toán của pháp nhân ``entity`` với ngày hạch toán ≤ ``through`` (hàng rào số dư đầu kỳ — 0119). Cặp
    bút toán gốc + bút toán đảo của nó chỉ bỏ qua khi CẢ HAI ≤ ``through`` (đã triệt tiêu trước D — vd lô đầu kỳ đã đảo để ghi lại); đảo sau D
    thì hoạt động trước D vẫn tính."""
    rows = await ctx.all(
        """SELECT DISTINCT a.code FROM ledger.postings p JOIN ledger.accounts a ON a.id = p.account_id JOIN ledger.journals j ON j.id = p.journal_id
           WHERE a.code = ANY(%(c)s) AND j.entity_id = %(e)s AND j.posted_on <= %(d)s
             AND NOT EXISTS (SELECT 1 FROM ledger.journals r WHERE r.reverses = j.id AND r.posted_on <= %(d)s)
             AND NOT EXISTS (SELECT 1 FROM ledger.journals o WHERE o.id = j.reverses AND o.posted_on <= %(d)s)""",
        {"c": codes, "e": entity, "d": through})
    return {r["code"] for r in rows}


# ── Khoá kỳ theo pháp nhân (0119) — mốc = dòng mới nhất của ``ledger.period_locks``; chặn ở trigger ``t_journals_period_lock`` ──

async def hold_period(ctx: Ctx, entity: UUID) -> None:
    """Khoá RIÊNG theo pháp nhân tới hết giao dịch — cùng khoá trigger khoá kỳ lấy (dùng chung). Giữ khoá này ⇒ không bút toán nào của pháp
    nhân đang ghi dở, và bút toán mới chờ tới khi giao dịch này xong rồi mới đọc mốc."""
    await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended('ledger.period_lock:' || %s::text, 0))", (entity,))


async def locked_through(ctx: Ctx, entity: UUID) -> date | None:
    """Mốc khoá hiện hành của pháp nhân (None = chưa khoá ngày nào)."""
    row = await ctx.one("SELECT locked_through FROM ledger.period_locks WHERE entity_id = %s ORDER BY id DESC LIMIT 1", (entity,))
    return row["locked_through"] if row else None


async def lock_through(ctx: Ctx, *, entity: UUID, through: date, reason: str = "") -> date | None:
    """TIẾN mốc khoá tới ``through`` (≤ hôm nay, sau mốc hiện hành). Trả mốc cũ. Dưới khoá riêng của pháp nhân ⇒ bút toán đang ghi dở xong trước."""
    _need_writer(ctx)
    if through > today():
        raise Invalid(f"Không khoá trước được: {through:%d/%m/%Y} sau hôm nay")
    await hold_period(ctx, entity)
    old = await locked_through(ctx, entity)
    if old is not None and through <= old:
        raise Invalid(f"Kỳ đã khoá tới {old:%d/%m/%Y} — khoá chỉ tiến, muốn lùi thì xin mở lại kỳ (có duyệt)")
    await ctx.exec("INSERT INTO ledger.period_locks (entity_id, locked_through, reason, created_by) VALUES (%s, %s, %s, %s)",
                   (entity, through, reason.strip(), ctx.p.user_id))
    return old


async def reopen_to(ctx: Ctx, *, entity: UUID, through: date | None, reason: str) -> date | None:
    """LÙI mốc khoá về ``through`` (None = mở hết). Chỉ chạy sau phiếu duyệt (người xin không tự duyệt — người gọi là lệnh sau duyệt, có tên
    người duyệt); cần lý do. Trả mốc cũ."""
    _need_writer(ctx)
    if not ctx.p.approved_by:
        raise Denied("Mở lại kỳ đã khoá cần phiếu đã được người khác duyệt")
    if not reason.strip():
        raise Invalid("Mở lại kỳ cần lý do")
    await hold_period(ctx, entity)
    old = await locked_through(ctx, entity)
    if old is None or (through is not None and through >= old):
        raise Invalid("Mốc mở lại phải trước mốc khoá hiện hành" if old else "Pháp nhân này chưa khoá kỳ nào")
    if ctx.p.approved_scope is None:
        raise Denied("Mở lại kỳ đã khoá cần phiếu đã được người khác duyệt")
    command, chain = ctx.p.approved_scope
    # DB chỉ cho LÙI mốc qua hàm này (chủ bảng), và hàm kiểm phiếu duyệt đang chạy khớp lệnh / chuỗi / người xin / pháp nhân
    await ctx.exec("SELECT ledger.reopen_period_lock(%s, %s, %s, %s, %s, %s, %s)",
                   (entity, through, reason.strip(), ctx.p.user_id, list(ctx.p.approved_by), command, chain))
    return old


async def period_locks(ctx: Ctx, entity: UUID | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Lịch sử mốc khoá (mới nhất trước) — mỗi dòng là một lần tiến / mở lại."""
    return await ctx.all(
        """SELECT l.id, l.entity_id, l.locked_through, l.reason, l.created_by, l.approved_by, l.at FROM ledger.period_locks l
           WHERE %(e)s::uuid IS NULL OR l.entity_id = %(e)s ORDER BY l.id DESC LIMIT %(n)s""", {"e": entity, "n": limit})
