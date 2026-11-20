"""TÀI KHOẢN NGÂN HÀNG & TIỀN VÀO / RA (0151) — dùng chung mọi ngành, không gói nào được nhắc tên ở đây.

* Tài khoản ngân hàng của công ty là DỮ LIỆU (``acct.bank_accounts``): pháp nhân, số tài khoản và MẪU CỘT sao kê của ngân hàng đó (tên cột trong file
  tải về ứng với ô nào). Thêm ngân hàng mới = thêm một dòng, không sửa code.
* Nạp sao kê: lệnh ``acct.add_statement_line`` đọc cả LƯỚI ô của file (``read_statement`` — hàm thuần): tìm dòng tiêu đề sau phần đầu file, nhận ra
  tài khoản (số tài khoản ở phần đầu file / cột tài khoản / mẫu cột chỉ khớp một tài khoản), đổi tên cột theo mẫu của tài khoản, tách số tiền có
  dấu thành vào / ra, và cấp mã dòng cho dòng không có mã giao dịch ⇒ nạp lại cùng file (kể cả hai kỳ chồng nhau) không nhân đôi.
* Tiền RA chỉ để ĐỐI CHIẾU với lệnh chi đã ghi (``acct.bank_out_lines``): chi không có lệnh trong hệ thống, lệnh chi đã chuyển mà sao kê chưa thấy ⇒
  "Cảnh báo chi". Sổ cái chỉ ghi khi kế toán xác nhận chuyển khoản (đường cũ) — không ghi hai lần.
* Bản tin "tiền về" mỗi sáng cho người xem tiền cả công ty (theo ma trận quyền, không viết cứng vai) + màn tiền vào / ra theo tài khoản và kỳ.
* Cảnh báo chi gom từ gói NGUỒN qua điểm cắm ``acct.payout_flags`` (gói nguồn biết nghĩa vụ của mình đúng hay sai — vd giao dịch chưa xác nhận) +
  phần ngân hàng ở đây.
"""

import hashlib
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.api import Action as A
from barista.api import (
    Ctx,
    Day,
    Desk,
    Input,
    Invalid,
    Line,
    NoInput,
    NotFound,
    Screen,
    code_from_name,
    company_settings,
    entities,
    field,
    kpi,
    line,
    name_key,
    today,
    ui,
    zone,
)
from barista.modules.acct import G_IN, G_OUT, G_SETUP, MATCH, STATEMENT_COLUMNS, _matched, _vnd, m

# ── Mẫu cột của tài khoản: ô dòng sao kê → danh sách tên cột trong file của ngân hàng đó (thêm vào tên cột hay gặp, không thay) ──
MAPPED = {"received_on": "Cột ngày", "amount_vnd": "Cột tiền vào (ghi có)", "out_vnd": "Cột tiền ra (ghi nợ)",
          "signed": "Cột số tiền có dấu (âm = tiền ra)", "memo": "Cột nội dung", "payer": "Cột bên chuyển / nhận", "bank_ref": "Cột mã giao dịch"}
HEADER_SCAN = 40   # phần đầu file (tên ngân hàng, số tài khoản, kỳ sao kê…) thường vài dòng — tìm dòng tiêu đề trong chừng này dòng đầu


def _names(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"[;\n]", text) if x.strip()]


def _key(v: Any) -> str:
    return name_key(str(v or ""))


def _aliases(columns: dict[str, list[str]]) -> dict[str, str]:
    """khoá tên cột (bỏ dấu, chữ thường) → ô. Mẫu cột của tài khoản thắng tên cột hay gặp khi trùng."""
    out = {_key(label): k for k, (label, alias) in STATEMENT_COLUMNS.items() for label in (label, *alias)}
    out.update({_key(n): k for k, names in columns.items() for n in names if k in MAPPED})
    return out


def _day(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    if m_ := re.match(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", s):
        d, mo, y = map(int, m_.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    if m_ := re.match(r"(\d{4})-(\d{2})-(\d{2})", s):
        try:
            return date(*map(int, m_.groups()))
        except ValueError:
            return None
    return None


def _amount(v: Any) -> int | None:
    """Ô tiền của sao kê: số, "1.500.000", "1,500,000.00", "-2.000.000" — None nếu trống / không phải số."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return round(v)
    s = re.sub(r"\s|vnd|vnđ|đ", "", str(v).lower())
    if not s:
        return None
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.strip("-()+")
    s = re.sub(r"[.,]\d{2}$", "", s) if re.search(r"[.,]\d{3}[.,]\d{2}$", s) else s   # phần lẻ ",00" sau phân cách nghìn
    if not re.fullmatch(r"\d{1,3}([.,]\d{3})+|\d+", s):
        return None
    n = int(re.sub(r"[.,]", "", s))
    return -n if neg else n


def read_statement(grid: list[list[Any]], accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lưới ô của MỘT file sao kê → dòng ``{nhãn ô: giá trị, "_line": số dòng Excel}`` cho lệnh nạp dòng sao kê (hàm thuần, test thẳng).

    * Dòng tiêu đề = dòng đầu (trong ``HEADER_SCAN`` dòng) có cột ngày VÀ cột tiền (vào / ra / có dấu) theo tên cột hay gặp hoặc mẫu cột của một
      tài khoản; không thấy ⇒ trả rỗng (bên gọi dùng cách cũ: dòng đầu có chữ là tiêu đề).
    * Tài khoản: (1) số tài khoản của đúng một tài khoản đang dùng có trong phần đầu file; (2) không thì cột "Tài khoản ngân hàng" của từng dòng;
      (3) không thì đúng MỘT tài khoản mà mọi ô trong mẫu cột của nó đều có cột trong tiêu đề; (4) công ty chỉ có một tài khoản đang dùng ⇒ tài khoản đó. Không chắc ⇒ để trống
      (dòng vẫn nạp, kế toán thấy "chưa gắn tài khoản") — không đoán.
    * Cột không nhận ra giữ nguyên tên (vd "Pháp nhân") để bước khớp cột thường xử lý. Dòng không có ngày hoặc không có tiền (tổng cộng, số dư) bị bỏ.
    * Dòng không có mã giao dịch ⇒ mã dòng ``~`` + dấu vân tay (tài khoản, ngày, tiền, nội dung, thứ tự lần trùng) — nạp lại không nhân đôi."""
    active = [a for a in accounts if a.get("active", True)]
    head_at, cols = None, {}
    for i, row in enumerate(grid[:HEADER_SCAN]):
        for cand in [*active, None]:
            alias = _aliases((cand or {}).get("columns") or {})
            got = {j: alias[k] for j, v in enumerate(row) if (k := _key(v)) and k in alias}
            fields = set(got.values())
            if "received_on" in fields and fields & {"amount_vnd", "out_vnd", "signed"}:
                own = {_key(n) for names in ((cand or {}).get("columns") or {}).values() for n in names}
                if cand is None or own & {_key(v) for v in row}:
                    head_at, cols = i, got
                    break
        if head_at is not None:
            break
    if head_at is None:
        return []
    preamble = " ".join(str(v) for r in grid[:head_at] for v in r if v not in (None, ""))
    digits = re.sub(r"[\s.\-]", "", preamble)
    by_no = [a for a in active if a.get("account_no") and a["account_no"] in digits]
    account = by_no[0] if len(by_no) == 1 else None
    file_no = account["account_no"] if account else ""   # số tài khoản ĐỌC TỪ FILE (không phải đoán theo mẫu cột / tài khoản duy nhất)
    if account is None and "bank_account" not in cols.values():   # mẫu cột: MỌI ô tài khoản khai đều có cột trong tiêu đề, và chỉ một tài khoản như vậy
        heads = {_key(v) for v in grid[head_at]}
        same = [a for a in active if a.get("columns") and all({_key(n) for n in ns} & heads for ns in a["columns"].values())]
        account = same[0] if len(same) == 1 else None
    if account is None and len(active) == 1 and "bank_account" not in cols.values():
        account = active[0]
    known = {a["code"] for a in active} | {a.get("account_no") or "" for a in active}
    by_code = {a["code"]: a for a in active} | {a["account_no"]: a for a in active if a.get("account_no")}
    header = grid[head_at]
    seen: dict[tuple[Any, ...], int] = defaultdict(int)
    out = []
    for n, row in enumerate(grid[head_at + 1:], head_at + 2):
        cells = {cols[j]: v for j, v in enumerate(row) if j in cols and v not in (None, "")}
        rest = {str(header[j]).strip(): v for j, v in enumerate(row) if j not in cols and j < len(header) and header[j] not in (None, "")
                and v not in (None, "")}
        day = _day(cells.get("received_on"))
        signed = _amount(cells.get("signed"))
        credit = _amount(cells.get("amount_vnd"))
        debit = _amount(cells.get("out_vnd"))
        if signed is not None:
            credit, debit = (signed, None) if signed > 0 else (None, -signed) if signed < 0 else (None, None)
        credit = credit if credit and credit > 0 else None
        debit = abs(debit) if debit else None
        if day is None or (credit is None and debit is None):
            continue
        acc = account
        if acc is None and (v := str(cells.get("bank_account") or "").strip()):
            acc = by_code.get(v.upper()) or by_code.get(re.sub(r"\D", "", v))
            if acc is None and v.upper() not in known:
                rest["Tài khoản ngân hàng"] = v   # mã lạ: để lệnh báo lỗi rõ ở đúng dòng, không im lặng bỏ tài khoản
        memo = str(cells.get("memo") or "").strip()
        ref = str(cells.get("bank_ref") or "").strip()
        if not ref:   # vân tay KHÔNG theo tài khoản đoán (thêm tài khoản rồi nạp lại không đổi mã) — chỉ số tài khoản ghi trong file + nội dung dòng
            fp = (file_no, day.isoformat(), credit, debit, _key(memo), _key(cells.get("payer")))
            seen[fp] += 1
            ref = "~" + hashlib.sha256(repr((*fp, seen[fp])).encode()).hexdigest()[:20]
        labels = {k: STATEMENT_COLUMNS[k][0] for k in STATEMENT_COLUMNS}
        out.append({**rest, labels["received_on"]: day.isoformat(), labels["amount_vnd"]: credit, labels["out_vnd"]: debit,
                    labels["memo"]: memo, labels["payer"]: str(cells.get("payer") or "").strip(), labels["bank_ref"]: ref,
                    **({labels["bank_account"]: acc["code"]} if acc else {}), "_line": n})
    return out


async def statement_rows(ctx: Ctx, grid: list[list[Any]]) -> list[dict[str, Any]]:
    """Bộ đọc lưới cho ``acct.add_statement_line`` (``import_rows``): mẫu cột của các tài khoản công ty (quyền người nhập) ⇒ ``read_statement``; file
    không nhận ra dòng tiêu đề ⇒ rỗng ⇒ lõi đọc cách cũ (dòng đầu có chữ là tiêu đề, khớp tên cột hay gặp)."""
    accounts = await ctx.all("SELECT code, account_no, columns, active FROM acct.bank_accounts ORDER BY code")
    return read_statement(grid, accounts)


# ── Tài khoản ngân hàng ──

_COLS = "Cột trong file sao kê"   # mẫu cột của ngân hàng: máy đọc tiêu đề file thường tự nhận ra, nên gấp lại


class SetBankAccount(Input):
    code: str = field("Sửa tài khoản có sẵn", "", options_query="acct.bank_account_options", example="VCB-MG", max_length=30, group="Thêm chi tiết",
                      placeholder="Tài khoản mới — máy đặt mã từ tên", help="Chọn tài khoản để đổi; để trống khi thêm tài khoản mới")
    label: str = field("Tên gọi", example="VCB — Công ty MG (thu phí)", max_length=120)
    bank: str = field("Ngân hàng", "", example="Ngân hàng Mẫu", max_length=120)
    account_no: str = field("Số tài khoản", "", example="0101000123", max_length=40, help="Chỉ số — để nhận ra tài khoản từ phần đầu file sao kê")
    entity_code: str = field("Pháp nhân", "", options_query="admin.entity_options", placeholder="Pháp nhân mặc định", group="Thêm chi tiết")
    col_date: str = field(MAPPED["received_on"], "", max_length=300, group=_COLS, help="Tên cột trong file của ngân hàng này; nhiều tên cách nhau dấu ;")
    col_credit: str = field(MAPPED["amount_vnd"], "", max_length=300, group=_COLS)
    col_debit: str = field(MAPPED["out_vnd"], "", max_length=300, group=_COLS)
    col_signed: str = field(MAPPED["signed"], "", max_length=300, group=_COLS, help="Ngân hàng ghi MỘT cột số tiền, tiền ra mang dấu âm")
    col_memo: str = field(MAPPED["memo"], "", max_length=300, group=_COLS)
    col_party: str = field(MAPPED["payer"], "", max_length=300, group=_COLS)
    col_ref: str = field(MAPPED["bank_ref"], "", max_length=300, group=_COLS)
    active: bool = field("Đang dùng", True, group="Thêm chi tiết")


@m.command("set_bank_account", resource="acct", action="manage", title="Thêm / sửa tài khoản ngân hàng", agent="propose", category="setup")
async def set_bank_account(ctx: Ctx, inp: SetBankAccount) -> dict[str, Any]:
    """Tài khoản ngân hàng của công ty + mẫu cột sao kê của ngân hàng đó (dữ liệu công ty). Sửa = cùng mã."""
    code = inp.code.strip().upper()
    if not code:   # 0225: không bắt người dùng nghĩ ra mã — máy đặt theo tên gọi
        code = code_from_name(inp.label, {r["code"] for r in await ctx.all("SELECT code FROM acct.bank_accounts")})
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{1,29}", code):
        raise Invalid("Mã tài khoản: chữ in hoa không dấu, số, gạch (vd VCB-MG)")
    no = re.sub(r"[\s.\-]", "", inp.account_no)
    if not re.fullmatch(r"\d{0,30}", no):
        raise Invalid("Số tài khoản chỉ gồm chữ số")
    eid = await entities.resolve(ctx, inp.entity_code)
    columns = {k: _names(v) for k, v in (("received_on", inp.col_date), ("amount_vnd", inp.col_credit), ("out_vnd", inp.col_debit),
                                          ("signed", inp.col_signed), ("memo", inp.col_memo), ("payer", inp.col_party), ("bank_ref", inp.col_ref)) if _names(v)}
    if "signed" in columns and {"amount_vnd", "out_vnd"} & set(columns):
        raise Invalid("Chọn MỘT cách ghi tiền: cột số tiền có dấu, hoặc hai cột tiền vào / tiền ra")
    if no and await ctx.val("SELECT 1 FROM acct.bank_accounts WHERE account_no = %s AND code <> %s", (no, code)):
        raise Invalid(f"Số tài khoản {no} đã khai ở tài khoản khác")
    aid = await ctx.val(
        """INSERT INTO acct.bank_accounts (code, label, bank, account_no, entity_id, columns, active, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, core.me())
           ON CONFLICT (tenant_id, code) DO UPDATE SET label = EXCLUDED.label, bank = EXCLUDED.bank, account_no = EXCLUDED.account_no,
                  entity_id = EXCLUDED.entity_id, columns = EXCLUDED.columns, active = EXCLUDED.active RETURNING id""",
        (code, inp.label.strip(), inp.bank.strip(), no, eid, Jsonb(columns), inp.active))
    return {"bank_account_id": aid, "code": code, "summary": f"Tài khoản {code}: {len(columns)} cột theo mẫu ngân hàng"}


@m.query("bank_accounts", resource="acct", title="Tài khoản ngân hàng", min_scope="all")
async def bank_accounts(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("""SELECT b.id, b.code, b.label, b.bank, b.account_no, e.code AS entity, b.columns, b.active,
                                   (SELECT max(l.received_on) FROM acct.statement_lines l WHERE l.bank_account_id = b.id) AS last_in,
                                   (SELECT max(o.paid_on) FROM acct.bank_out_lines o WHERE o.bank_account_id = b.id) AS last_out
                            FROM acct.bank_accounts b JOIN core.legal_entities e ON e.id = b.entity_id ORDER BY b.active DESC, b.code""")
    for r in rows:
        cols = r.pop("columns") or {}
        r["format"] = " · ".join(f"{MAPPED[k].removeprefix('Cột ')}: {', '.join(v)}" for k, v in cols.items() if k in MAPPED) or "Tên cột hay gặp"
        r["state"] = "on" if r.pop("active") else "off"
        r.update({f"col_{k}": "; ".join(cols.get(c, [])) for k, c in (("date", "received_on"), ("credit", "amount_vnd"), ("debit", "out_vnd"),
                                                                       ("signed", "signed"), ("memo", "memo"), ("party", "payer"), ("ref", "bank_ref"))})
    return ui.table(rows, [
        ui.col("code", "Mã"), ui.col("label", "Tên gọi"), ui.col("bank", "Ngân hàng"), ui.col("account_no", "Số tài khoản"), ui.col("entity", "Pháp nhân"),
        ui.col("format", "Mẫu cột sao kê", width=320), ui.col("last_in", "Tiền vào gần nhất", "date"), ui.col("last_out", "Tiền ra gần nhất", "date"),
        ui.col("state", "Trạng thái", "badge", labels={"on": "Đang dùng", "off": "Ngừng"}, tones={"on": "green", "off": "gray"}),
    ], title="Tài khoản ngân hàng", empty="Chưa khai tài khoản ngân hàng — thêm để nạp sao kê nhiều ngân hàng, xem tiền vào / ra theo tài khoản",
        note="Mẫu cột là dữ liệu công ty: file sao kê của ngân hàng ghi tên cột thế nào thì khai đúng tên đó. Nạp sao kê ở màn Đối soát sao kê.")


@m.query("bank_account_options", resource="acct", title="Tài khoản ngân hàng")
async def bank_account_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all(
        """SELECT code AS value, CASE WHEN count(*) OVER (PARTITION BY lower(label)) > 1 THEN label || ' (' || code || ')' ELSE label END AS label
           FROM acct.bank_accounts WHERE active ORDER BY label, code"""))


async def bank_account(ctx: Ctx, code: str) -> dict[str, Any] | None:
    if not code.strip():
        return None
    acc = await ctx.one("SELECT * FROM acct.bank_accounts WHERE code = %s OR (account_no <> '' AND account_no = %s)",
                        (code.strip().upper(), re.sub(r"\D", "", code)))
    if acc is None:
        raise NotFound(f"Không có tài khoản ngân hàng {code}")
    if not acc["active"]:
        raise Invalid(f"Tài khoản {acc['code']} đã ngừng dùng")
    return acc


async def add_out_line(ctx: Ctx, *, account: dict[str, Any] | None, entity: UUID, paid_on: date, amount: int, memo: str, payee: str,
                       bank_ref: str, source: str) -> dict[str, Any]:
    lid = await ctx.val(
        """INSERT INTO acct.bank_out_lines (bank_account_id, entity_id, paid_on, amount_vnd, memo, payee, bank_ref, created_by, source)
           VALUES (%s, %s, %s, %s, %s, %s, %s, core.me(), %s) ON CONFLICT (tenant_id, bank_ref) WHERE bank_ref <> '' DO NOTHING RETURNING id""",
        (account["id"] if account else None, entity, paid_on, amount, memo, payee, bank_ref, source))
    return {"out_line_id": lid} if lid else {"skipped": f"mã giao dịch {bank_ref} đã nạp"}


# ── Tiền ra: đối chiếu với lệnh chi đã ghi ──

MATCH_DAYS = 3   # lệnh chi đã chuyển và dòng tiền ra cùng số tiền, lệch ngày trong ngần này ⇒ coi là một (khi không cùng mã giao dịch)


def pair_out(lines: list[dict[str, Any]], payouts: list[dict[str, Any]]) -> dict[Any, Any]:
    """Dòng tiền ra ↔ lệnh chi đã chuyển (hàm thuần): cùng mã giao dịch ngân hàng; không thì cùng số tiền, lệch ≤ ``MATCH_DAYS`` ngày, đúng MỘT ứng
    viên chưa ghép. Trả {id dòng: id lệnh chi}."""
    out: dict[Any, Any] = {}
    used: set[Any] = set()
    by_ref = {p["bank_ref"]: p for p in payouts if p["bank_ref"]}
    for ln in lines:
        if (p := by_ref.get(ln["bank_ref"])) is not None and p["id"] not in used:
            out[ln["id"]] = p["id"]
            used.add(p["id"])
    for ln in sorted((x for x in lines if x["id"] not in out), key=lambda x: (x["paid_on"], str(x["id"]))):
        cand = [p for p in payouts if p["id"] not in used and p["amount_vnd"] == ln["amount_vnd"] and p["transferred_on"]
                and abs((p["transferred_on"] - ln["paid_on"]).days) <= MATCH_DAYS]
        if len(cand) == 1:
            out[ln["id"]] = cand[0]["id"]
            used.add(cand[0]["id"])
    return out


async def _out_state(ctx: Ctx, since: date, until: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[Any, Any]]:
    """Dòng tiền ra trong kỳ + lệnh chi / chi trả đã chuyển quanh kỳ + phép ghép. Lệnh chi mật (0150) người xem không thấy thì không ghép — dòng của
    nó hiện "chưa có lệnh" cho người không có quyền xem lương (chỉ số tiền của chính sao kê, không lộ thêm gì)."""
    lines = await ctx.all("""SELECT o.*, b.code AS account FROM acct.bank_out_lines o LEFT JOIN acct.bank_accounts b ON b.id = o.bank_account_id
                             WHERE o.paid_on >= %s AND o.paid_on <= %s ORDER BY o.paid_on, o.id""", (since, until))
    lo, hi = since - timedelta(days=MATCH_DAYS), until + timedelta(days=MATCH_DAYS)
    payouts = await ctx.all("""SELECT id, bank_ref, amount_vnd, transferred_on, label, coalesce(recipient_name, '') AS who, 'payout' AS kind FROM acct.payouts
                               WHERE status = 'transferred' AND transferred_on BETWEEN %s AND %s
                               UNION ALL SELECT id, bank_ref, amount_vnd, transferred_on, reason, payee_name, 'remittance' FROM acct.remittances
                               WHERE status = 'transferred' AND transferred_on BETWEEN %s AND %s
                               UNION ALL SELECT id, bank_ref, total_vnd, bill_on, memo, supplier_name, 'bill' FROM acct.bills
                               WHERE mode = 'paid' AND pay_account = 'cash:bank' AND cancelled_on IS NULL AND bill_on BETWEEN %s AND %s""",
                            (lo, hi, lo, hi, lo, hi))   # 0170: phiếu chi phí đã trả ngay bằng chuyển khoản cũng là "lệnh chi trong hệ thống"
    return lines, payouts, pair_out(lines, payouts)


class OutFilter(Input):
    date_from: Day | None = field("Từ ngày", None, placeholder="Đầu tháng")
    date_to: Day | None = field("Đến ngày", None, placeholder="Hôm nay")
    state: str = field("Tình trạng", "unmatched", options={"unmatched": "Chưa có lệnh chi", "": "Tất cả", "matched": "Khớp lệnh chi",
                                                          "ignored": "Đã bỏ qua"})


OUT_STATE = {"matched": "Khớp lệnh chi", "unmatched": "Chưa có lệnh chi", "ignored": "Bỏ qua"}


@m.query("out_lines", resource="acct", title="Tiền ra theo sao kê", min_scope="all")
async def out_lines(ctx: Ctx, inp: OutFilter) -> dict[str, Any]:
    """Dòng tiền RA của sao kê, mỗi dòng khớp lệnh chi đã ghi (cùng mã giao dịch / cùng số tiền gần ngày) hay chưa. "Chưa có lệnh chi" = tiền đã ra
    khỏi tài khoản mà hệ thống không có lệnh chi nào — hoặc ghi bù, hoặc bỏ qua có lý do (thuê văn phòng, thuế… chưa quản lý ở đây)."""
    t = today()
    since, until = inp.date_from or t - timedelta(days=60), inp.date_to or t
    lines, payouts, pairs = await _out_state(ctx, since, until)
    by_id = {p["id"]: p for p in payouts}
    rows = []
    for ln in lines:
        p = by_id.get(pairs.get(ln["id"]))
        state = "ignored" if ln["status"] == "ignored" else "matched" if p else "unmatched"
        if inp.state and state != inp.state:
            continue
        rows.append({"id": ln["id"], "paid_on": ln["paid_on"], "account": ln["account"] or "", "amount_vnd": ln["amount_vnd"], "memo": ln["memo"],
                     "payee": ln["payee"], "bank_ref": "" if ln["bank_ref"].startswith("~") else ln["bank_ref"], "state": state,
                     "payout": f"{p['label']} — {p['who']}" if p else "", "note": ln["note"]})
    open_ = [r for r in rows if r["state"] == "unmatched"]
    return ui.table(rows, [
        ui.col("state", "Đối chiếu", "badge", labels=OUT_STATE, tones={"matched": "green", "unmatched": "red", "ignored": "gray"}),
        ui.col("paid_on", "Ngày", "date"), ui.col("amount_vnd", "Số tiền ra", "money"), ui.col("memo", "Nội dung", width=280),
        ui.col("payee", "Bên nhận"), ui.col("account", "Tài khoản"), ui.col("payout", "Lệnh chi trong hệ thống", width=240), ui.col("bank_ref", "Số tham chiếu NH"),
        ui.col("note", "Lý do bỏ qua", width=200),
    ], title=f"Tiền ra theo sao kê {since:%d/%m} – {until:%d/%m/%Y}", limit=2000, empty="Không có dòng tiền ra nào trong kỳ (nạp sao kê có cột ghi nợ)",
        kpis=[ui.kpi("Chưa có lệnh chi", len(open_), tone="red" if open_ else "green", hint=_vnd(sum(r["amount_vnd"] for r in open_)) if open_ else "")])


class IgnoreOut(Input):
    line_id: UUID = field("Dòng tiền ra", hidden=True)
    note: str = field("Lý do", max_length=500, example="Tiền thuê văn phòng tháng 11 — chưa quản lý trên hệ thống")


@m.command("ignore_out_line", resource="acct", title="Bỏ qua dòng tiền ra (có lý do)", agent="propose")
async def ignore_out_line(ctx: Ctx, inp: IgnoreOut) -> dict[str, Any]:
    if not inp.note.strip():
        raise Invalid("Cần lý do bỏ qua")
    got = await ctx.val("""UPDATE acct.bank_out_lines SET status = 'ignored', note = %s, decided_by = core.me(), decided_at = now()
                           WHERE id = %s AND status = 'open' RETURNING id""", (inp.note.strip(), inp.line_id))
    if got is None:
        raise NotFound("Không thấy dòng tiền ra đang chờ đối chiếu")
    return {"line_id": got, "summary": "Đã bỏ qua dòng tiền ra"}


# ── Số hoá đơn đã xuất ──

def invoice_number(text: str) -> str:
    """Phần SỐ của số hoá đơn để khớp nội dung chuyển khoản: "1C26TMG/0000050" ⇒ "50" (bỏ ký hiệu, bỏ số 0 đầu). Số phải đứng CUỐI — chỉ có ký hiệu
    ("1C26TMG") ⇒ rỗng."""
    m_ = re.search(r"(\d+)$", (text or "").strip())
    return str(int(m_.group(1))) if m_ else ""


# nội dung chuyển khoản đã bỏ dấu / khoảng trắng (name_key): "...hoa don so 0000050..." ⇒ "hoadonso0000050"
_INVOICE_IN_MEMO = re.compile(r"(?:hoadon|hddt|invoice)(?:gtgt|dientu|so|no)*0*(\d{1,10})(?!\d)")
# "HD" trần KHÔNG tính (vừa là "hợp đồng", vừa nằm trong mã khác như "PHD12"); chỉ "hoá đơn" / "HĐĐT" / "invoice" + số


def invoice_numbers_in(memo_key: str) -> set[str]:
    """Số hoá đơn bên trả ghi trong nội dung (đã qua name_key) — chỉ số đứng NGAY sau chữ "hoá đơn / HĐ / invoice", không lấy mọi con số."""
    return {str(int(x)) for x in _INVOICE_IN_MEMO.findall(memo_key)}


class RecordInvoice(Input):
    code: str = field("Khoản phải thu", options_query="acct.receivable_options", example="TT4K7Q2M")
    invoice_no: str = field("Số hoá đơn", example="1C26TMG-0000050", max_length=40, aliases=("Số HĐ", "Số hoá đơn điện tử", "Ký hiệu - số"))
    invoice_on: Day | None = field("Ngày hoá đơn", None, example="12/11/2026", aliases=("Ngày HĐ", "Ngày lập"), placeholder="Hôm nay")


@m.command("record_invoice", resource="acct", title="Ghi số hoá đơn đã xuất", importable=True, agent="propose")
async def record_invoice(ctx: Ctx, inp: RecordInvoice) -> dict[str, Any]:
    """Kế toán phát hành hoá đơn (phần mềm hoá đơn điện tử) rồi ghi số vào khoản phải thu ⇒ bên trả ghi số này khi chuyển khoản, đối soát tự khớp;
    người ghi = người xuất (lưu tên). Số đã dùng cho khoản khác cùng pháp nhân ⇒ từ chối. Không đổi số tiền / sổ cái."""
    no = inp.invoice_no.strip().upper()
    if not re.fullmatch(r"[0-9A-Z/._-]{1,40}", no) or not invoice_number(no):
        raise Invalid("Số hoá đơn: chữ số (có thể kèm ký hiệu, vd 1C26TMG-0000050)")
    r = await ctx.one("SELECT id, code, entity_id, status, invoice_no, source, source_ref FROM acct.receivables WHERE code = %s FOR UPDATE",
                      (inp.code.strip().upper(),))
    if r is None:
        raise NotFound(f"Không có khoản phải thu mã {inp.code}")
    if r["status"] == "void":
        raise Invalid(f"{r['code']} đã huỷ")
    from barista.modules.acct.vat import has_active_invoice
    if await has_active_invoice(ctx, r["id"]):   # 0165: số của hoá đơn GTGT đã ghi đi cùng bút toán thuế — đổi số = huỷ rồi ghi hoá đơn thay thế
        raise Invalid(f"{r['code']} đã ghi hoá đơn GTGT {r['invoice_no']} — đổi số bằng huỷ hoá đơn rồi ghi hoá đơn thay thế")
    if other := await ctx.val("SELECT code FROM acct.receivables WHERE entity_id = %s AND upper(invoice_no) = %s AND id <> %s AND status <> 'void'",
                              (r["entity_id"], no, r["id"])):
        raise Invalid(f"Số hoá đơn {no} đã ghi cho {other}")
    await ctx.exec("UPDATE acct.receivables SET invoice_no = %s, invoice_on = %s, invoiced_by = core.me() WHERE id = %s",
                   (no, inp.invoice_on or today(), r["id"]))
    ctx.emit("acct.invoice_recorded", receivable_id=r["id"], code=r["code"], source=r["source"], source_ref=r["source_ref"], invoice_no=no)
    return {"code": r["code"], "invoice_no": no, "summary": f"{r['code']}: hoá đơn số {no}" + (f" (thay {r['invoice_no']})" if r["invoice_no"] else "")}


# ── Bản tin tiền về + tiền vào / ra theo tài khoản ──

class DayIn(Input):
    day: Day | None = field("Ngày", None, placeholder="Hôm nay")


@m.query("cash_brief", resource="acct", title="Tiền về trong ngày", min_scope="all")
async def cash_brief(ctx: Ctx, inp: DayIn) -> dict[str, Any]:
    """Tiền VÀO tài khoản công ty trong một ngày (theo ngày tiền về của sao kê / phiếu thu): tổng, theo tài khoản, đã khớp khoản phải thu nào (số hoá
    đơn, bên trả), còn dòng nào chưa khớp; luỹ kế tháng; tiền ra cùng ngày theo sao kê. Số do code tính từ sao kê đã nạp — trợ lý chỉ nói lại thẻ."""
    d = inp.day or today()
    lines = await ctx.all("""SELECT l.id, l.amount_vnd, l.deducted_vnd, l.allocated_vnd, l.status, l.memo, l.payer, l.bank_ref, coalesce(b.label, '') AS account
                             FROM acct.statement_lines l LEFT JOIN acct.bank_accounts b ON b.id = l.bank_account_id
                             WHERE l.received_on = %s AND l.status <> 'ignored' ORDER BY l.amount_vnd DESC""", (d,))
    paid = await ctx.all("""SELECT r.code, r.invoice_no, r.party, r.label, p.amount_vnd, r.amount_vnd - r.paid_vnd AS rest
                            FROM acct.payments p JOIN acct.receivables r ON r.id = p.receivable_id
                            WHERE p.line_id = ANY(%s) ORDER BY p.amount_vnd DESC""", ([x["id"] for x in lines],))
    month = int(await ctx.val("SELECT coalesce(sum(amount_vnd), 0)::bigint FROM acct.statement_lines WHERE status <> 'ignored'"
                              " AND received_on >= %s AND received_on <= %s", (d.replace(day=1), d)) or 0)
    out = int(await ctx.val("SELECT coalesce(sum(amount_vnd), 0)::bigint FROM acct.bank_out_lines WHERE paid_on = %s", (d,)) or 0)
    total = sum(x["amount_vnd"] for x in lines)
    left = [x for x in lines if x["status"] == "open" and x["amount_vnd"] + x["deducted_vnd"] > x["allocated_vnd"]]
    hints = {r["id"]: r for r in await _matched(ctx, [x["id"] for x in left])} if left else {}
    by_acc: dict[str, int] = defaultdict(int)
    for x in lines:
        by_acc[x["account"] or "Chưa gắn tài khoản"] += x["amount_vnd"]
    blocks: list[dict[str, Any]] = [ui.b_kpis([
        ui.kpi("Tiền về", total, "money", tone="green" if total else None, hint=f"{len(lines)} dòng"),
        ui.kpi("Đã khớp & ghi thu", sum(max(0, x["allocated_vnd"] - x["deducted_vnd"]) for x in lines), "money"),   # tiền thật: phần bên trả giữ lại (0217) không về tài khoản
        ui.kpi("Chưa ghi thu", sum(x["amount_vnd"] + x["deducted_vnd"] - x["allocated_vnd"] for x in left), "money", tone="red" if left else None,
               hint=f"{len(left)} dòng" if left else ""),
        ui.kpi("Luỹ kế tháng", month, "money"), ui.kpi("Tiền ra (sao kê)", out, "money")])]
    if not lines:
        blocks.append(ui.b_callout("muted", f"Chưa có tiền về ngày {d:%d/%m/%Y}", "Theo sao kê đã nạp — nạp sao kê mới ở Kế toán › Đối soát sao kê."))
    if len(by_acc) > 1 or (by_acc and "Chưa gắn tài khoản" not in by_acc):
        blocks.append(ui.b_list([{"title": k, "badge": _vnd(v), "tone": "accent"} for k, v in sorted(by_acc.items(), key=lambda x: -x[1])],
                                "Theo tài khoản"))
    if paid:
        blocks.append(ui.b_list([{"title": f"{r['party'] or 'Bên trả'} — {_vnd(r['amount_vnd'])}",
                                  "subtitle": " · ".join(x for x in [r["code"], f"hoá đơn số {r['invoice_no']}" if r["invoice_no"] else "", r["label"],
                                                                     "đã thu đủ" if r["rest"] <= 0 else f"còn {_vnd(r['rest'])}"] if x),
                                  "tone": "ok" if r["rest"] <= 0 else "warn", "open": {"query": "acct.receivables", "input": {"q": r["code"], "status": ""}}}
                                 for r in paid], "Đã khớp khoản phải thu"))
    if left:
        blocks.append(ui.b_list([{"title": f"{x['payer'] or x['memo'][:60] or 'Không rõ bên chuyển'} — {_vnd(x['amount_vnd'] + x['deducted_vnd'] - x['allocated_vnd'])}",
                                  "subtitle": (f"{MATCH[h['match']]} {h['code']} · " if (h := hints.get(x["id"])) and h["code"] else "Chưa khớp khoản nào · ")
                                              + (x["memo"][:120] or ""),
                                  "tone": "bad", "open": {"query": "acct.reconcile", "input": {}}} for x in left], "Chưa ghi thu — kế toán đối soát"))
    return ui.card(f"Tiền về {d:%d/%m/%Y}", blocks, subtitle="Theo sao kê đã nạp và phiếu thu — số tính từ dữ liệu, không ước đoán", icon="banknote",
                   open=[{"title": "Tiền vào / ra theo tài khoản", "query": "acct.cash_moves", "input": {}}],
                   ask={"title": "Hỏi trợ lý về tiền về", "prompt": f"Về tiền về ngày {d:%d/%m/%Y}: "})


def _say_cash(o: Any) -> list[Line | None]:
    got = kpi(o, "Tiền về") or 0
    left = kpi(o, "Chưa ghi thu") or 0
    return [line("ok", f"Hôm nay tiền về {_vnd(int(got))}", "Theo sao kê đã nạp", "Hôm nay tiền về bao nhiêu, khớp hoá đơn nào?") if got else None,
            line("bad", f"{_vnd(int(left))} tiền về chưa ghi thu", "Kế toán đối soát (code đã gợi ý khoản phải thu) rồi ghi thu",
                 "Tiền về nào chưa khớp?") if left else None]


class Range(Input):
    date_from: Day | None = field("Từ ngày", None, placeholder="Đầu tháng")
    date_to: Day | None = field("Đến ngày", None, placeholder="Hôm nay")
    bank_account: str = field("Tài khoản", "", options_query="acct.bank_account_options")


@m.query("cash_moves", resource="acct", title="Tiền vào / ra theo tài khoản", min_scope="all")
async def cash_moves(ctx: Ctx, inp: Range) -> dict[str, Any]:
    """Theo từng tài khoản ngân hàng trong kỳ: tiền vào (sao kê + phiếu thu), tiền ra (sao kê), ròng, còn bao nhiêu tiền vào chưa đối soát, bao nhiêu
    tiền ra chưa có lệnh chi. Dòng không gắn tài khoản (phiếu thu nhập tay, sao kê cũ) gom một dòng riêng — không chia đoán."""
    t = today()
    since, until = inp.date_from or t.replace(day=1), inp.date_to or t
    if since > until:
        raise Invalid("Ngày bắt đầu sau ngày kết thúc")
    acc = await bank_account(ctx, inp.bank_account) if inp.bank_account.strip() else None
    where = "" if acc is None else " AND bank_account_id = %(a)s"
    prm = {"s": since, "u": until, "a": acc["id"] if acc else None}
    ins = await ctx.all("SELECT bank_account_id AS acc, count(*) AS n, coalesce(sum(amount_vnd), 0)::bigint AS v,"
                        " coalesce(sum(amount_vnd + deducted_vnd - allocated_vnd) FILTER (WHERE status = 'open'), 0)::bigint AS open"
                        " FROM acct.statement_lines WHERE status <> 'ignored' AND received_on BETWEEN %(s)s AND %(u)s" + where
                        + " GROUP BY 1", prm)
    lines, _payouts, pairs = await _out_state(ctx, since, until)
    names = {r["id"]: r for r in await ctx.all("SELECT b.id, b.code, b.label, e.code AS entity FROM acct.bank_accounts b"
                                                 " JOIN core.legal_entities e ON e.id = b.entity_id")}
    rows: dict[Any, dict[str, Any]] = {}

    def row(k: Any) -> dict[str, Any]:
        n = names.get(k)
        return rows.setdefault(k, {"id": str(k or "none"), "account": n["code"] if n else "—", "label": n["label"] if n else "Chưa gắn tài khoản",
                                   "entity": n["entity"] if n else "", "in_vnd": 0, "in_n": 0, "out_vnd": 0, "out_n": 0, "open_in": 0, "out_unmatched": 0})
    for r in ins:
        x = row(r["acc"])
        x["in_vnd"], x["in_n"], x["open_in"] = int(r["v"]), int(r["n"]), int(r["open"])
    for ln in lines:
        if acc is not None and ln["bank_account_id"] != acc["id"]:
            continue
        x = row(ln["bank_account_id"])
        x["out_vnd"] += ln["amount_vnd"]
        x["out_n"] += 1
        if ln["status"] == "open" and ln["id"] not in pairs:
            x["out_unmatched"] += ln["amount_vnd"]
    out = sorted(rows.values(), key=lambda r: (r["account"] == "—", r["account"]))
    for r in out:
        r["net_vnd"] = r["in_vnd"] - r["out_vnd"]
    return ui.table(out, [
        ui.col("account", "Tài khoản"), ui.col("label", "Tên gọi"), ui.col("entity", "Pháp nhân"), ui.col("in_vnd", "Tiền vào", "money"),
        ui.col("in_n", "Số dòng vào", "number"), ui.col("out_vnd", "Tiền ra", "money"), ui.col("out_n", "Số dòng ra", "number"),
        ui.col("net_vnd", "Ròng", "money"), ui.col("open_in", "Tiền vào chưa đối soát", "money"), ui.col("out_unmatched", "Tiền ra chưa có lệnh chi", "money"),
    ], title=f"Tiền vào / ra {since:%d/%m} – {until:%d/%m/%Y}" + (f" · {acc['code']}" if acc else ""), empty="Chưa có sao kê nào trong kỳ",
        kpis=[ui.kpi("Tiền vào", sum(r["in_vnd"] for r in out), "money", tone="green"), ui.kpi("Tiền ra", sum(r["out_vnd"] for r in out), "money"),
              ui.kpi("Ròng", sum(r["net_vnd"] for r in out), "money"),
              ui.kpi("Tiền ra chưa có lệnh chi", sum(r["out_unmatched"] for r in out), "money",
                     tone="red" if any(r["out_unmatched"] for r in out) else None)],
        note="Theo sao kê đã nạp (tiền ra chỉ có khi file có cột ghi nợ). Tiền vào gồm cả phiếu thu nhập tay (dòng “Chưa gắn tài khoản”).")


# ── Bản tin buổi sáng cho người xem tiền cả công ty (theo ma trận quyền) ──

DIGEST_HOUR = 7   # giờ công ty (0169): sau giờ này mới gửi bản tin hôm qua (sáng sớm kế toán chưa nạp sao kê thì bản tin đi khi có dòng đầu tiên)
_digest_sent: dict[str, date] = {}   # công ty → ngày đã gửi (mỗi tiến trình; khoá chống lặp của thông báo vẫn là hàng rào cuối)


@m.command("cash_digest", resource=None, title="Bản tin tiền về hôm qua", internal=True, agent="deny",
           sweep="SELECT DISTINCT tenant_id FROM acct.statement_lines WHERE received_on = core.today_of(tenant_id) - 1 AND status <> 'ignored'")
async def cash_digest(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Vòng quét: một thông báo / người / ngày cho thành viên đọc được Kế toán phạm vi cả công ty (ma trận quyền — không viết cứng vai): tiền về hôm
    qua bao nhiêu, bao nhiêu chưa khớp. Bấm ⇒ thẻ "Tiền về" của hôm qua. Tin đẩy không mang số tiền (đi qua máy chủ ngoài)."""
    t = today()
    key = str(ctx.p.tenant_id)
    if _digest_sent.get(key) == t or datetime.now(zone()).hour < DIGEST_HOUR:
        return {"sent": 0}
    d = t - timedelta(days=1)
    r = await ctx.one("""SELECT count(*) AS n, coalesce(sum(amount_vnd), 0)::bigint AS v,
                                coalesce(sum(amount_vnd + deducted_vnd - allocated_vnd) FILTER (WHERE status = 'open'), 0)::bigint AS open
                         FROM acct.statement_lines WHERE received_on = %s AND status <> 'ignored'""", (d,))
    people = await ctx.call_system("admin.members_with_grant", resource="acct", action="read", scope="all")
    assert r is not None
    sent = await ctx.notify([UUID(x) for x in people], kind="cash", title=f"Tiền về {d:%d/%m}: {_vnd(int(r['v']))} ({r['n']} dòng)",
                            body=(f"Chưa ghi thu: {_vnd(int(r['open']))}." if r["open"] else "Mọi dòng đã ghi thu."),
                            push="Bản tin tiền về hôm qua đã có", open={"query": "acct.cash_brief", "input": {"day": d.isoformat()}},
                            key=f"acct.cash_digest:{d.isoformat()}")
    _digest_sent[key] = t
    return {"sent": sent}


# ── Báo trước tháng dự phóng âm tiền (0211 lát 2) ──

LOW_ALERT = m.setting("cash_low_alert", "Báo trước tháng dự phóng âm tiền", default=True,
                      help="Bật: mỗi sáng kiểm Dòng tiền dự phóng 4 tháng; có tháng cuối kỳ âm tiền ⇒ báo người xem Kế toán cả công ty (cùng một tháng "
                           "âm: tối đa một lần mỗi tuần). Chỉ khi công ty có nạp sao kê gần đây — không thì số tiền hiện có chưa đối chiếu ngân hàng.")
_low_checked: dict[str, date] = {}   # công ty → ngày đã kiểm (mỗi tiến trình); khoá thông báo theo tuần là hàng rào chống lặp thật


@m.command("cash_low_alert", resource=None, title="Báo trước tháng dự phóng âm tiền", internal=True, agent="deny",
           sweep="SELECT DISTINCT tenant_id FROM acct.statement_lines WHERE received_on >= core.today_of(tenant_id) - 60")
async def cash_low_alert(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Vòng quét buổi sáng: chạy đúng truy vấn ``acct.cashflow`` (quyền hệ thống, số gộp — nghĩa vụ mật chỉ là tổng) và đọc trường ``alert`` (tháng
    đầu tiên âm tiền — code tính, không model). Có ⇒ một thông báo cho mỗi người đọc được Kế toán phạm vi cả công ty (ma trận quyền, không viết cứng
    vai); khoá theo (tháng âm, tuần) ⇒ cùng tháng âm không nhắc mỗi ngày. Tin đẩy không mang số tiền (đi qua máy chủ ngoài). Bấm ⇒ thẻ Dòng tiền."""
    from barista.modules.acct import CashflowInput, cashflow

    t = today()
    key = str(ctx.p.tenant_id)
    if _low_checked.get(key) == t or datetime.now(zone()).hour < DIGEST_HOUR:
        return {"sent": 0}
    _low_checked[key] = t
    if not await company_settings.enabled(ctx.conn, ctx.p.tenant_id, LOW_ALERT):
        return {"sent": 0}
    a = (await cashflow(ctx, CashflowInput(months=4))).get("alert")
    if not a:
        return {"sent": 0}
    people = await ctx.call_system("admin.members_with_grant", resource="acct", action="read", scope="all")
    year, week, _ = t.isocalendar()
    sent = await ctx.notify([UUID(x) for x in people], kind="cash", title=f"Dòng tiền dự phóng âm tháng {a['month']}",
                            body=f"Cuối tháng {a['month']} còn {_vnd(a['closing'])}. Xem khoản thu / chi từng tháng trên thẻ Dòng tiền dự phóng.",
                            push="Dòng tiền dự phóng có tháng âm tiền", open={"query": "acct.cashflow", "input": {}},
                            key=f"acct.cash_low:{a['month']}:{year}-W{week:02d}")
    return {"sent": sent, "month": a["month"]}


# ── Cảnh báo chi: gói nguồn nói nghĩa vụ nào chưa nên chi + phần ngân hàng ở đây ──

# điểm cắm "acct.payout_flags": gói nguồn nhận {payable_ids} (nghĩa vụ CỦA NÓ), trả [{payable_id, ref, flags: [chữ]}] — quyền người xem
HOLD = m.setting("hold_flagged_payments", "Chặn đề nghị chi khi gói nguồn cảnh báo", default=False,
                 help="Bật: đề nghị chi nghĩa vụ đang bị cảnh báo (vd giao dịch chưa được xác nhận) bị từ chối tới khi hết cảnh báo. Tắt: vẫn xin "
                      "được, phiếu duyệt ghi rõ cảnh báo để người duyệt thấy.")


async def source_flags(ctx: Ctx, payable_ids: list[UUID]) -> dict[str, dict[str, Any]]:
    """{payable_id: {ref, flags}} từ các gói nguồn đã cắm (quyền người gọi — gói tắt / thiếu quyền ⇒ không có phần đó)."""
    out: dict[str, dict[str, Any]] = {}
    if not payable_ids:
        return out
    # nhìn bằng quyền HỆ THỐNG (hợp đồng điểm cắm, chỉ nghĩa vụ được hỏi): cảnh báo / chặn không được trống chỉ vì người xin thiếu quyền đọc nguồn
    for part in await ctx.plugs("acct.payout_flags", system=True, payable_ids=[str(x) for x in payable_ids]):
        for r in part or []:
            got = out.setdefault(str(r["payable_id"]), {"ref": r.get("ref", ""), "flags": [], "blocks": []})
            got["flags"] += list(r.get("flags") or [])
            got["blocks"] += list(r.get("blocks") or [])
    return out


async def refuse_blocked(ctx: Ctx, payable_ids: list[UUID]) -> None:
    """BẤT BIẾN của gói nguồn (vd chi vượt quyền hưởng đã mở, gồm phần đã chi ở hệ cũ): có ⇒ từ chối. Gọi ở MỌI cửa lập chi (``prepare_payout``)."""
    got = await source_flags(ctx, payable_ids)
    hard = [f"{v['ref']}: {f}" if v["ref"] else f for v in got.values() for f in v["blocks"]]
    if hard:
        raise Invalid("Không chi được — " + "; ".join(hard[:5]))


async def flags_for_request(ctx: Ctx, payable_ids: list[UUID]) -> list[str]:
    """Cảnh báo của các nghĩa vụ sắp xin chi ⇒ câu cho phiếu duyệt; công ty bật "chặn" ⇒ từ chối luôn."""
    await refuse_blocked(ctx, payable_ids)   # báo sớm lúc xin; lúc lập chi (prepare_payout) kiểm lại — một cửa
    got = await source_flags(ctx, payable_ids)
    words = [f"{v['ref']}: {f}" if v["ref"] else f for v in got.values() for f in v["flags"]]
    if words and await company_settings.enabled(ctx.conn, ctx.p.tenant_id, HOLD):
        raise Invalid("Chưa chi được — " + "; ".join(words[:5]))
    return words


STALE_PAYOUT_DAYS = 7


@m.query("payout_checks", resource="acct", title="Cảnh báo chi", min_scope="all")
async def payout_checks(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Chặn chi sai trước khi tiền đi: (1) nghĩa vụ / lệnh chi mà gói nguồn cảnh báo (vd giao dịch chưa xác nhận, đã huỷ, chi vượt quyền đã mở);
    (2) tiền ra trên sao kê mà không có lệnh chi nào (chi ngoài hệ thống); (3) lệnh chi đã chuyển mà sao kê đã nạp tới ngày đó vẫn không thấy;
    (4) lệnh chi chờ chuyển quá lâu / đang tra soát. Mỗi dòng mở đúng chỗ xử lý."""
    t = today()
    from barista.modules.acct.settlements import _balances
    recent = await _balances(ctx, "p.kind='payable' AND (p.created_at > now() - interval '180 days')")
    paid_ids = {r["payable_id"] for r in await ctx.all(
        "SELECT DISTINCT a.payable_id FROM acct.payout_allocations a JOIN acct.payouts x ON x.id = a.payout_id WHERE x.status <> 'cancelled'"
        " AND x.created_at > now() - interval '180 days'")}
    watch = [r["id"] for r in recent if r["left_vnd"] > 0 or r["id"] in paid_ids]
    flagged = {k: v for k, v in (await source_flags(ctx, watch)).items() if v["flags"]}
    by_pid = {str(r["id"]): r for r in recent}
    # người nhận đứng trong câu: hai dòng cùng giao dịch là hai người khác nhau — kế toán cần biết chặn chi CHO AI (rà vai kế toán 18/11/2026)
    pids = sorted({str(by_pid[k]["party_id"]) for k in flagged if k in by_pid})
    who = await ctx.call_system("parties.names", party_ids=pids) if pids else {}

    def _payee(k: str) -> str:
        n = who.get(str(by_pid[k]["party_id"]), {}).get("name", "") if k in by_pid else ""
        return f"{n} · " if n else ""

    src_items = [{"title": f"{v['ref'] or by_pid[k]['label']} — {'; '.join(v['flags'])}",
                  "subtitle": f"{_payee(k)}{by_pid[k]['label']} · còn phải chi {_vnd(int(by_pid[k]['left_vnd']))}" if k in by_pid else "",
                  "badge": "Chưa nên chi" if by_pid.get(k, {}).get("left_vnd", 0) > 0 else "Đã chi", "tone": "bad",
                  "open": {"query": "acct.payables", "input": {}}} for k, v in flagged.items()]
    lines, _payouts, pairs = await _out_state(ctx, t - timedelta(days=90), t)
    no_order = [ln for ln in lines if ln["status"] == "open" and ln["id"] not in pairs]
    bank_items = [{"title": f"{ln['payee'] or ln['memo'][:60] or 'Không rõ bên nhận'} — {_vnd(ln['amount_vnd'])}",
                   "subtitle": f"{ln['paid_on']:%d/%m/%Y} · {ln['account'] or 'chưa gắn tài khoản'} · {ln['memo'][:100]}", "tone": "bad",
                   "badge": "Chi ngoài hệ thống", "open": {"query": "acct.out_lines", "input": {}}} for ln in no_order[:30]]
    covered = {r["entity_id"]: r["through"] for r in await ctx.all(
        "SELECT entity_id, max(paid_on) AS through FROM acct.bank_out_lines WHERE source = 'excel' GROUP BY entity_id")}
    # "đã thấy trên sao kê" chỉ tính dòng từ FILE sao kê — dòng gõ tay / ảnh không đóng được cảnh báo (review 10/11)
    matched = {pid for lid, pid in pairs.items() if next((ln for ln in lines if ln["id"] == lid), {}).get("source") == "excel"}
    missing = await ctx.all("""SELECT id, label, recipient_name, amount_vnd, transferred_on, bank_ref, entity_id FROM acct.payouts
                               WHERE status = 'transferred' AND transferred_on >= %s ORDER BY transferred_on""", (t - timedelta(days=90),))
    unseen = [p for p in missing if p["id"] not in matched and covered.get(p["entity_id"]) and covered[p["entity_id"]] >= p["transferred_on"]]
    unseen_items = [{"title": f"{p['label']} — {p['recipient_name']} {_vnd(p['amount_vnd'])}",
                     "subtitle": f"Chuyển {p['transferred_on']:%d/%m/%Y}, mã {p['bank_ref']} — sao kê đã nạp tới ngày đó nhưng không thấy", "tone": "warn",
                     "badge": "Chưa thấy trên sao kê", "open": {"query": "acct.payouts", "input": {}}} for p in unseen]
    stale = await ctx.all("""SELECT id, label, recipient_name, amount_vnd, status, created_at FROM acct.payouts
                             WHERE status = 'unknown' OR (status = 'pending_transfer' AND created_at < now() - make_interval(days => %s))
                             ORDER BY created_at""", (STALE_PAYOUT_DAYS,))
    stale_items = [{"title": f"{p['label']} — {p['recipient_name']} {_vnd(p['amount_vnd'])}",
                    "subtitle": "Đang tra soát" if p["status"] == "unknown" else f"Chờ chuyển từ {p['created_at']:%d/%m} (quá {STALE_PAYOUT_DAYS} ngày)",
                    "tone": "warn", "badge": "Lệnh chi treo", "open": {"query": "acct.payouts", "input": {}}} for p in stale]
    n = len(src_items) + len(no_order) + len(unseen_items) + len(stale_items)
    blocks: list[dict[str, Any]] = [ui.b_kpis([
        ui.kpi("Nghĩa vụ bị cảnh báo", len(src_items), tone="red" if src_items else "green"),
        ui.kpi("Chi ngoài hệ thống", len(no_order), tone="red" if no_order else None, hint=_vnd(sum(x["amount_vnd"] for x in no_order)) if no_order else ""),
        ui.kpi("Lệnh chi chưa thấy trên sao kê", len(unseen_items), tone="orange" if unseen_items else None),
        ui.kpi("Lệnh chi treo", len(stale_items), tone="orange" if stale_items else None)])]
    if not n:
        blocks.append(ui.b_callout("ok", "Chưa thấy khoản chi nào đáng ngờ", "Theo nghĩa vụ đang có và sao kê đã nạp."))
    for items, title in ((src_items, "Nghĩa vụ chưa nên chi (nghiệp vụ gốc báo — vd chi vượt phần hoa hồng đã mở)"), (bank_items, "Tiền ra không có lệnh chi trong hệ thống"),
                         (unseen_items, "Lệnh chi đã chuyển nhưng chưa thấy trên sao kê"), (stale_items, "Lệnh chi treo")):
        if items:
            blocks.append(ui.b_list(items, title))
    return ui.card("Cảnh báo chi", blocks, subtitle="Trước khi chi và sau khi chi — số từ nghĩa vụ, lệnh chi và sao kê đã nạp", icon="exclamationmark.shield",
                   ask={"title": "Hỏi trợ lý về cảnh báo chi", "prompt": "Trong các cảnh báo chi, khoản nào cần xử lý trước? "})


def _say_checks(o: Any) -> Line | None:
    n = sum(int(kpi(o, k) or 0) for k in ("Nghĩa vụ bị cảnh báo", "Chi ngoài hệ thống", "Lệnh chi chưa thấy trên sao kê"))
    return line("bad", f"{n} cảnh báo chi chưa xử lý", "Nghĩa vụ chưa nên chi, tiền ra không có lệnh chi, lệnh chi chưa thấy trên sao kê",
                "Có khoản chi nào đáng ngờ không?") if n else None


class PayoutRef(Input):
    payout_id: UUID


@m.query("payout_payables", resource="acct", title="Nghĩa vụ của một lệnh chi", internal=True, agent="deny", family="settlement",
         why="Gói khai consumes(\"settlement\") nghe acct.payout_transferred (không người bấm) để tính lại hồ sơ có nghĩa vụ vừa chi: lệnh chi của đúng nguồn bên gọi. "
             "Trả CHỈ [{payable_id, source_ref}] của nghĩa vụ nguồn bên gọi — không bên nhận, không tài khoản, không số tiền.")
async def payout_payables(ctx: Ctx, inp: PayoutRef) -> list[dict[str, Any]]:
    return await ctx.all("""SELECT p.id AS payable_id, p.source_ref FROM acct.payout_allocations a JOIN acct.payables p ON p.id = a.payable_id
                            WHERE a.payout_id = %s AND split_part(p.source, '.', 1) = %s""", (inp.payout_id, ctx.caller or ctx.module))


m.screens("accounting", (
    Screen("cash_brief", "Tiền về hôm nay", "banknote.fill", "acct.cash_brief", tint="green", group=G_IN,
           desk=Desk("watch", say=_say_cash, menu=True, rank=30)),
    Screen("cash_moves", "Tiền vào / ra theo tài khoản", "arrow.left.arrow.right.circle", "acct.cash_moves", tint="teal", group=G_IN),
), rank=12)
m.screens("accounting", (
    Screen("payout_checks", "Cảnh báo chi", "exclamationmark.shield", "acct.payout_checks", tint="red", group=G_OUT,
           desk=Desk("decide", say=_say_checks, menu=True, rank=40, doers_only=True)),   # việc của kế toán; giám đốc xem ở "Đang theo dõi"
    Screen("out_lines", "Tiền ra theo sao kê", "arrow.up.forward.square", "acct.out_lines", group=G_OUT,
           row_actions=(A("acct.ignore_out_line", "Bỏ qua (có lý do)", bind={"line_id": "id"}, when={"state": ["unmatched"]}),)),
), rank=21)
m.screens("accounting", (
    Screen("bank_accounts", "Tài khoản ngân hàng", "building.columns", "acct.bank_accounts", group=G_SETUP,
           actions=(A("acct.set_bank_account", style="primary"),),
           row_actions=(A("acct.set_bank_account", "Sửa", bind={"code": "code"},
                          prefill={"label": "label", "bank": "bank", "account_no": "account_no", "entity_code": "entity", "col_date": "col_date",
                                   "col_credit": "col_credit", "col_debit": "col_debit", "col_signed": "col_signed", "col_memo": "col_memo",
                                   "col_party": "col_party", "col_ref": "col_ref"}),)),
), rank=90)
