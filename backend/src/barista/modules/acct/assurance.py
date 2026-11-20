"""BẢO ĐẢM TIỀN (ADR 0228 §D, chặng 2 "Business Assurance") — số đầu tiên: hồ sơ khoản lệch (0223) đã BẮT được bao nhiêu tiền, XỬ LÝ xong bao nhiêu, mất bao lâu.

Chỉ ĐỌC ``acct.discrepancy_dossiers`` (cùng schema; RLS như mọi truy vấn: người phạm vi hẹp chỉ thấy hồ sơ giao cho mình). Không cột mới, không trạng thái mới —
mọi định nghĩa dưới đây dựng trên đúng bốn trạng thái có sẵn ``open | resolved | written_off | dismissed`` và hai mốc ``created_at`` / ``closed_at``
(CHECK của bảng: ``status = 'open'`` ⇔ ``closed_at IS NULL``).

ĐỊNH NGHĨA (một nguồn cho tập dữ liệu ``acct.ds_dossiers`` lẫn màn "Bảo đảm tiền" — cùng ``BASE``):

* **Tiền lệch phát hiện** ("caught") = số tiền của GIAO DỊCH NGÂN HÀNG (dòng sao kê) mà hệ thống đã mở hồ sơ vì không khớp công nợ — ``facts.line.amount_vnd``.
  Số tiền của dòng sao kê không đổi sau khi nạp; hồ sơ chụp nó lúc dựng ⇒ đọc từ hồ sơ (không JOIN ``acct.statement_lines``, bảng đó chỉ người phạm vi cả
  công ty thấy ⇒ người phạm vi hẹp sẽ ra 0 đ giả). Tính theo NGÀY MỞ hồ sơ. Gồm cả hồ sơ sau đó bị đóng vì không phải khoản lệch (xem "Đã đóng — không phải
  khoản lệch" để trừ ra). KHÔNG phải "phần thiếu so với công nợ": phần thiếu lúc mở không được lưu bất biến (``facts`` được làm mới khi hồ sơ còn mở).
* **Đã xử lý xong** ("resolved / recovered") = hồ sơ ở ``resolved`` HOẶC ``written_off``: code đã kiểm dòng tiền phân bổ HẾT vào khoản phải thu và các khoản hồ
  sơ nhắm tới không còn nợ (``dossier._evaluate``). Tiền đã xử lý = số tiền của dòng sao kê đó (toàn bộ đã ghi vào công nợ). Tính theo NGÀY ĐÓNG.
* **Phần thiếu đã xoá** ("written off") = ``written_off_vnd``: phần phải thu đã giảm qua phiếu duyệt (``acct.write_off_dossier`` → giấy báo có) — tiền công ty
  CHẤP NHẬN không thu, không phải tiền thu về. Cộng trên mọi hồ sơ (kể cả còn mở); lọc theo ngày đóng thì chỉ gồm hồ sơ đã đóng trong kỳ.
* **Đã đóng — không phải khoản lệch** = ``dismissed`` (dòng sao kê bị bỏ qua, hoặc kế toán đóng có lý do). Không tính vào đã xử lý.
* **Còn mở** = ``open``; tiền = số tiền dòng sao kê của hồ sơ đang mở. Đồng nhất: phát hiện = đã xử lý + không phải khoản lệch + còn mở (cùng tập hồ sơ).
* **Số ngày xử lý** = ngày đóng − ngày mở (ngày theo múi giờ công ty, đóng cùng ngày = 0), CHỈ hồ sơ đã xử lý xong. Mốc mở là lúc HỒ SƠ được dựng (vòng quét /
  kế toán lập tay), không phải ngày tiền về; mốc đóng là lúc code thấy đủ điều kiện (phiếu thu mới, sau xoá phần thiếu, hoặc vòng quét ~20 phút).
  Tập dữ liệu chỉ cộng / đếm được ⇒ có TRUNG BÌNH (tổng ngày ÷ số hồ sơ); TRUNG VỊ chỉ ở màn (tính riêng bằng SQL trên cùng ``BASE``).
"""

from datetime import date
from typing import Any, LiteralString

from barista.api import Ctx, Dim, Input, Measure, Ratio, Screen, add_months, field, today, ui
from barista.modules.acct import G_REPORT, _vnd, m
from barista.modules.acct import dossier_logic as L
from barista.modules.acct.dossier import STATUS

# Một dòng = một hồ sơ khoản lệch. ``source`` = phần đầu (trước dấu chấm) nguồn của khoản phải thu ĐẦU TIÊN hồ sơ nhắm tới — đọc từ ứng viên đã chụp trong hồ sơ
# (RLS của hồ sơ, không phải của khoản phải thu); hồ sơ không nhắm khoản nào ⇒ ''.
BASE: LiteralString = """
    SELECT d.code, d.status, d.pattern, d.due_on, e.code AS entity, coalesce(u.name, u.email, '') AS assignee,
           split_part(coalesce((SELECT c->>'source' FROM jsonb_array_elements(d.facts->'candidates') c WHERE c->>'code' = d.target_codes[1] LIMIT 1), ''), '.', 1) AS source,
           coalesce((d.facts->'line'->>'amount_vnd')::bigint, 0) AS amount_vnd, d.written_off_vnd,
           d.status IN ('resolved', 'written_off') AS resolved,
           (d.created_at AT TIME ZONE core.tz())::date AS opened_on, (d.closed_at AT TIME ZONE core.tz())::date AS closed_on,
           CASE WHEN d.status IN ('resolved', 'written_off') THEN (d.closed_at AT TIME ZONE core.tz())::date - (d.created_at AT TIME ZONE core.tz())::date END AS days,
           d.facts->'line'->>'payer' AS payer
    FROM acct.discrepancy_dossiers d JOIN core.legal_entities e ON e.id = d.entity_id LEFT JOIN core.users u ON u.id = d.owner_id"""


m.dataset(
    "dossiers", "Hồ sơ khoản lệch", resource="acct",
    description=("Hồ sơ khoản lệch: tiền giao dịch có hồ sơ, đã xử lý (ghi hết vào công nợ), còn mở, ngày xử lý. Tháng mở = phát hiện; tháng "
                 "đóng = đã xử lý. Định nghĩa: màn Bảo đảm tiền."),
    base=BASE,
    dims={"opened_month": Dim("Tháng mở", "to_char(t.opened_on, 'YYYY-MM')", month=True),
          "closed_month": Dim("Tháng đóng", "coalesce(to_char(t.closed_on, 'YYYY-MM'), '')", month=True),
          "status": Dim("Kết quả", "t.status", labels=STATUS), "pattern": Dim("Dấu hiệu", "t.pattern", labels=L.PATTERNS),
          "entity": Dim("Pháp nhân", "t.entity"), "assignee": Dim("Người phụ trách", "t.assignee"),
          "source": Dim("Nguồn công nợ", "t.source")},
    measures={"opened": Measure("Số hồ sơ", "count(*)"),
              "caught_vnd": Measure("Tiền phát hiện", "sum(t.amount_vnd)", "money"),
              "resolved": Measure("Hồ sơ đã xử lý", "count(*) FILTER (WHERE t.resolved)"),
              "resolved_vnd": Measure("Tiền đã xử lý", "sum(t.amount_vnd) FILTER (WHERE t.resolved)", "money"),
              "written_off_vnd": Measure("Phần thiếu đã xoá", "sum(t.written_off_vnd)", "money"),
              "dismissed": Measure("Hồ sơ không phải lệch", "count(*) FILTER (WHERE t.status = 'dismissed')"),
              "dismissed_vnd": Measure("Tiền không phải lệch", "sum(t.amount_vnd) FILTER (WHERE t.status = 'dismissed')", "money"),
              "open": Measure("Hồ sơ còn mở", "count(*) FILTER (WHERE t.status = 'open')"),
              "open_vnd": Measure("Tiền còn mở", "sum(t.amount_vnd) FILTER (WHERE t.status = 'open')", "money"),
              "days_sum": Measure("Tổng ngày xử lý", "sum(t.days) FILTER (WHERE t.resolved)"),
              "avg_days": Ratio("Ngày xử lý trung bình", "days_sum", "resolved", "number")},
    dates={"opened_on": "Ngày mở hồ sơ", "closed_on": "Ngày đóng hồ sơ"},
    # trước ngày công ty đặt luật khoản lệch đầu tiên vòng quét chưa chạy (0223 soát 14/11) — hồ sơ khi đó chỉ do kế toán lập tay ⇒ số chưa đủ
    coverage="SELECT min(created_at)::date AS reliable_from FROM acct.discrepancy_rules WHERE active",
)


# ── Màn "Bảo đảm tiền" ──

PERIOD_SQL: LiteralString = """
    SELECT count(*) FILTER (WHERE t.opened_on >= %(a)s AND t.opened_on < %(b)s) AS opened,
           coalesce(sum(t.amount_vnd) FILTER (WHERE t.opened_on >= %(a)s AND t.opened_on < %(b)s), 0)::bigint AS caught_vnd,
           count(*) FILTER (WHERE t.resolved AND t.closed_on >= %(a)s AND t.closed_on < %(b)s) AS resolved,
           coalesce(sum(t.amount_vnd) FILTER (WHERE t.resolved AND t.closed_on >= %(a)s AND t.closed_on < %(b)s), 0)::bigint AS resolved_vnd,
           coalesce(sum(t.written_off_vnd) FILTER (WHERE t.closed_on >= %(a)s AND t.closed_on < %(b)s), 0)::bigint AS written_off_vnd,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY t.days) FILTER (WHERE t.resolved AND t.closed_on >= %(a)s AND t.closed_on < %(b)s) AS median_days
    FROM (""" + BASE + """) t"""

OPEN_SQL: LiteralString = """
    SELECT count(*) AS n, coalesce(sum(t.amount_vnd), 0)::bigint AS amount_vnd, count(*) FILTER (WHERE t.due_on < core.today()) AS late
    FROM (""" + BASE + """) t WHERE t.status = 'open'"""

TOP_SQL: LiteralString = """
    SELECT t.code, t.amount_vnd, t.pattern, t.assignee, t.due_on, t.payer FROM (""" + BASE + """) t
    WHERE t.status = 'open' ORDER BY t.amount_vnd DESC, t.due_on, t.code LIMIT %s"""

TOP_OPEN = 5


def _days(v: Any) -> str:
    if v is None:
        return "—"
    f = float(v)
    return f"{f:.0f} ngày" if f == int(f) else f"{f:.1f} ngày".replace(".", ",")


async def _period(ctx: Ctx, a: date, b: date) -> dict[str, Any]:
    return await ctx.one(PERIOD_SQL, {"a": a, "b": b}) or {}


def _kpis(p: dict[str, Any], when: str) -> list[dict[str, Any]]:
    return [ui.kpi(f"Tiền lệch phát hiện {when}", p.get("caught_vnd", 0), "money", hint=f"{p.get('opened', 0)} hồ sơ mở"),
            ui.kpi(f"Đã xử lý xong {when}", p.get("resolved_vnd", 0), "money", tone="green" if p.get("resolved") else None,
                   hint=f"{p.get('resolved', 0)} hồ sơ" + (f" · xoá phần thiếu {_vnd(p['written_off_vnd'])}" if p.get("written_off_vnd") else "")),
            ui.kpi(f"Trung vị ngày xử lý {when}", _days(p.get("median_days")), "text",
                   hint="ngày đóng − ngày mở hồ sơ" if p.get("median_days") is not None else "chưa có hồ sơ xử lý xong")]


class AssuranceIn(Input):
    top: int = field("Số hồ sơ đang mở lớn nhất", TOP_OPEN, ge=1, le=20)


@m.query("assurance", resource="acct", title="Bảo đảm tiền", min_scope="all")
async def assurance(ctx: Ctx, inp: AssuranceIn) -> dict[str, Any]:
    """Hồ sơ khoản lệch đã bắt được bao nhiêu tiền, xử lý xong bao nhiêu, trung vị bao nhiêu ngày — tháng này và 3 tháng trước; hồ sơ đang mở lớn nhất.
    Số do code tính trên toàn bộ hồ sơ của công ty (định nghĩa ở đầu ``acct/assurance.py``); trợ lý chỉ nói lại thẻ."""
    t = today()
    m0 = t.replace(day=1)
    now, before = await _period(ctx, m0, add_months(m0, 1)), await _period(ctx, add_months(m0, -3), m0)
    op = await ctx.one(OPEN_SQL) or {"n": 0, "amount_vnd": 0, "late": 0}
    top = await ctx.all(TOP_SQL, (inp.top,))
    m3 = add_months(m0, -3)
    blocks: list[dict[str, Any]] = [
        ui.b_kpis(_kpis(now, "tháng này")),
        ui.b_kpis(_kpis(before, f"T{m3.month}–T{add_months(m0, -1).month}")),
        ui.b_kpis([ui.kpi("Còn mở", op["amount_vnd"], "money", tone="orange" if op["n"] else "green", hint=f"{op['n']} hồ sơ"),
                   ui.kpi("Quá hạn xử lý", op["late"], tone="red" if op["late"] else None)]),
    ]
    if top:
        blocks.append(ui.b_list([{
            "title": f"{(r['payer'] or '').strip() or 'Chưa rõ bên chuyển'} — {_vnd(r['amount_vnd'])}",
            "subtitle": " · ".join([r["code"], L.PATTERNS.get(r["pattern"]) or r["pattern"], f"phụ trách: {r['assignee'] or 'chưa giao'}",
                                    f"hạn {r['due_on']:%d/%m/%Y}" + (f" (quá {(t - r['due_on']).days} ngày)" if r["due_on"] < t else "")]),
            "tone": "bad" if r["due_on"] < t else "warn", "open": {"query": "acct.dossier", "input": {"code": r["code"]}}} for r in top],
            "Hồ sơ đang mở lớn nhất"))
    else:
        blocks.append(ui.b_callout("ok", "Không còn hồ sơ khoản lệch nào đang mở"))
    blocks.append(ui.b_callout("muted", "Cách tính", "Tiền lệch phát hiện = số tiền của giao dịch ngân hàng có hồ sơ, theo ngày mở hồ sơ. Đã xử lý xong = dòng tiền "
                               "đã ghi hết vào công nợ và khoản nhắm tới hết nợ, theo ngày đóng (gồm hồ sơ có xoá phần thiếu qua duyệt — phần xoá là tiền chấp nhận "
                               "không thu). Hồ sơ đóng vì không phải khoản lệch không tính là đã xử lý."))
    return ui.card("Bảo đảm tiền", blocks, subtitle=f"Hồ sơ khoản lệch · tháng {t.month}/{t.year} và 3 tháng trước · số tính từ hồ sơ, không ước đoán", icon="lock.shield",
                   open=[{"title": "Khoản lệch cần xử lý", "query": "acct.dossiers", "input": {}},
                         {"title": "Tổng hợp theo chiều / tháng", "query": "acct.ds_dossiers", "input": {"by": ["opened_month"], "measures": ["caught_vnd", "resolved_vnd"]}}],
                   ask={"title": "Hỏi trợ lý về khoản lệch", "prompt": "Về số tiền hồ sơ khoản lệch đã phát hiện và xử lý: "})


m.screens("accounting", (
    Screen("money_assurance", "Bảo đảm tiền", "lock.shield", "acct.assurance", tint="indigo", min_scope="all", group=G_REPORT),
), rank=70)
