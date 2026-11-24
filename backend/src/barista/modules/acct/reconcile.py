"""ĐỐI SOÁT SAO KÊ (0055): code khớp dòng tiền vào chưa đối soát với khoản phải thu (``match_lines`` — hàm thuần), màn đối soát, ghi phiếu thu từ dòng
(một dòng / hàng loạt dòng khớp đủ — qua ``cash_in._allocate``), bỏ qua dòng."""

from collections import defaultdict
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from barista.api import (
    Ctx,
    Input,
    Invalid,
    NoInput,
    NotFound,
    entities,
    field,
    today,
    ui,
)
from barista.modules.acct import m
from barista.modules.acct.cash_in import _allocate, _payer_gate
from barista.modules.acct.common import _vnd
from barista.modules.acct.receivables import _rest, _target

# ── Đối soát sao kê (họp khách 31/10 — docs/clients/<khách>, F02/F03 — quyết định 0055) ──
#
# Dòng tiền VÀO (sao kê ngân hàng, bảng đối soát của bên trả, ảnh trợ lý đọc) nạp vào acct.statement_lines — chưa đụng sổ cái. CODE khớp từng
# dòng với KHOẢN PHẢI THU: mã khoản / mã riêng phụ (số giao dịch, hợp đồng) / chữ gợi ý (mã căn) trong nội dung chuyển khoản + so số tiền với số
# còn phải thu. Người bấm "ghi" ⇒ phân bổ (sổ cái, sự kiện cho gói nguồn). Chênh lệch KHÔNG tự xoá nợ: ghi phần đã nhận + một việc cho kế
# toán kèm gợi ý nguyên nhân (luật hạch toán phần chênh chờ khách chốt).

LINE_STATUS = {"open": "Chưa đối soát", "applied": "Đã phân bổ", "ignored": "Bỏ qua"}
MATCH = {"full": "Khớp đủ", "short": "Trả thiếu", "over": "Trả thừa", "amount_only": "Khớp theo số tiền", "unmatched": "Chưa khớp",
         "settled": "Khoản đã thu đủ", "parked": "Đã vào sổ — chờ phân bổ"}
MATCH_TONE = {"full": "green", "short": "orange", "over": "orange", "amount_only": "blue", "unmatched": "red", "settled": "gray", "parked": "purple"}
FEE_TOLERANCE = 50_000   # chênh ≤ mức này ⇒ nhiều khả năng phí chuyển khoản ngân hàng
DUE_SOON_DAYS = 10       # khoản phải thu chung tới hạn trong ngần này ngày mới được khớp theo mã căn / số tiền


def _hint(remaining: int, amount: int, payer: str) -> str:
    """Gợi ý nguyên nhân chênh (trả thiếu) — chỉ là GỢI Ý để kế toán hỏi lại bên trả, không tự hạch toán: phí ngân hàng, giữ lại 10% (bảo lưu),
    chuyển số trước VAT, chia đợt."""
    diff = remaining - amount
    if diff <= FEE_TOLERANCE:
        return "Chênh nhỏ — có thể là phí chuyển khoản ngân hàng"
    if abs(diff - round(remaining * 0.1)) <= max(1000, remaining // 1000):
        return f"Thiếu đúng 10% — có thể {payer} giữ lại 10% (bảo lưu / chờ đủ hồ sơ)"
    if abs(diff - round(remaining / 11)) <= max(1000, remaining // 1000):
        return f"Thiếu đúng phần VAT 10% — có thể {payer} chuyển số trước thuế"
    if abs(diff - round(remaining * 0.5)) <= max(1000, remaining // 1000):
        return f"Trả một nửa — có thể {payer} chia đợt"
    return f"Hỏi {payer} lý do (phạt, khấu trừ, chia đợt, chuyển nhầm số…)"


def _targets(receivables: list[dict[str, Any]], horizon: date | None) -> list[dict[str, Any]]:
    """``invoice`` = phần số của số hoá đơn đã xuất (0151). Mọi khoản phải thu một dòng tiền vào có thể trả cho, cùng một dạng để khớp. ``exact`` = mã khoản — thấy là nhận; ``unique`` = mã riêng
    phụ (số giao dịch, hợp đồng) — nhận khi đúng MỘT khoản trúng; ``hints`` = mã gợi ý (vd mã đơn vị / phòng) — nhận khi mọi khoản trúng thuộc CÙNG một bên
    trả (cùng ``group``) thì lấy khoản tới hạn sớm nhất còn nợ. ``soon`` = khoản đến hạn trong tầm (kỳ sau chưa tới không "ăn" tiền kỳ này khi
    khớp theo gợi ý / số tiền)."""
    from barista.api import name_key
    from barista.modules.acct.bank import invoice_number

    out: list[dict[str, Any]] = []
    for r in receivables:
        if r["status"] == "void":
            continue
        out.append({"id": r["id"], "ref": r["code"], "amount_vnd": r["amount_vnd"], "paid_vnd": r["paid_vnd"], "due_on": r["due_on"],
                    "payer": r.get("party") or "khách",
                    "group": (r.get("party") or "", tuple(sorted(r.get("match_hints") or [])), tuple(sorted(r.get("match_keys") or []))),   # cùng bên trả + cùng gợi ý (cọc lẫn tiền nhà)
                    "soon": horizon is None or r["due_on"] <= horizon, "exact": [(name_key(r["code"]), "mã khoản")],
                    "invoice": invoice_number(r.get("invoice_no") or ""),
                    "unique": [(k, "mã riêng") for x in r.get("match_keys") or [] if len(k := name_key(x)) >= 4],
                    "hints": [(k, "mã gợi ý") for h in r.get("match_hints") or [] if len(k := name_key(h)) >= 4]})
    return out


class _Keys:
    """Chỉ mục "mã nào nằm trong nội dung": thay vì thử từng mã của mọi khoản cho mỗi dòng (O(dòng × khoản) — 1.500 × 2.000 mất ~3 giây, N11), cắt
    nội dung thành mọi đoạn con có độ dài bằng độ dài một mã rồi tra từ điển. ``hits(key)`` = các (thứ tự khoản, thứ tự mã trong khoản) có mã
    nằm trong ``key`` — cùng tập với phép ``k in key`` cũ, bên gọi tự xếp theo thứ tự cũ."""

    def __init__(self, entries: list[list[str]]) -> None:
        self.by_key: dict[str, list[tuple[int, int]]] = {}
        for i, keys in enumerate(entries):
            for j, k in enumerate(keys):
                self.by_key.setdefault(k, []).append((i, j))
        self.lengths = sorted({len(k) for k in self.by_key})

    def hits(self, key: str) -> list[tuple[int, int]]:
        found: set[str] = set()
        for n in self.lengths:
            if n > len(key):
                break
            found.update(s for s in (key[p:p + n] for p in range(len(key) - n + 1)) if s in self.by_key)
        return sorted(h for k in found for h in self.by_key[k])


class _Owed:
    """Khoản đến hạn trong tầm còn nợ, theo SỐ CÒN PHẢI THU (đổi dần khi các dòng trước trả vào): tra "đúng một khoản bằng số tiền" và "ba khoản
    gần số tiền nhất" không phải duyệt mọi khoản cho mỗi dòng. Hoà khoảng cách ⇒ khoản đứng trước trong danh sách (như sắp xếp ổn định cũ)."""

    def __init__(self, left: list[int], soon: list[bool]) -> None:
        import bisect

        self._bisect = bisect
        self.soon = soon
        self.sorted = sorted((v, i) for i, v in enumerate(left) if soon[i] and v > 0)
        self.by_left: dict[int, set[int]] = defaultdict(set)
        for v, i in self.sorted:
            self.by_left[v].add(i)

    def set(self, i: int, old: int, new: int) -> None:
        if not self.soon[i]:
            return
        if old > 0:
            del self.sorted[self._bisect.bisect_left(self.sorted, (old, i))]
            self.by_left[old].discard(i)
        if new > 0:
            self._bisect.insort(self.sorted, (new, i))
            self.by_left[new].add(i)

    def equal(self, amount: int) -> set[int]:
        return self.by_left.get(amount, set())

    def nearest(self, amount: int, n: int = 3) -> list[int]:
        arr = self.sorted
        lo = self._bisect.bisect_left(arr, (amount, -1)) - 1
        hi = lo + 1
        cand: list[tuple[int, int]] = []
        while lo >= 0 or hi < len(arr):
            dl = amount - arr[lo][0] if lo >= 0 else None
            dh = arr[hi][0] - amount if hi < len(arr) else None
            d = dl if dh is None or (dl is not None and dl <= dh) else dh
            assert d is not None
            if len(cand) >= n and d > cand[-1][0]:
                break
            if d == dl and lo >= 0:
                cand.append((d, arr[lo][1]))
                lo -= 1
            else:
                cand.append((d, arr[hi][1]))
                hi += 1
        return [i for _, i in sorted(cand)[:n]]


def match_lines(lines: list[dict[str, Any]], receivables: list[dict[str, Any]], horizon: date | None = None) -> list[dict[str, Any]]:
    """Khớp bằng CODE (hàm thuần, test thẳng). Ưu tiên: mã khoản > mã riêng phụ (đúng một khoản) > SỐ HOÁ ĐƠN đã xuất ngay sau chữ "hoá đơn /
    HĐĐT / invoice" (đúng một khoản mang số đó, số tiền không vượt phần còn phải thu — 0151; ghi hàng loạt KHÔNG nhận, người xem từng dòng) > gợi ý (đúng một bên trả; khoản thu định kỳ ⇒
    kỳ tới hạn sớm nhất còn nợ) trong nội dung đã bỏ dấu / ký tự; không thấy mã ⇒ đúng MỘT khoản đến hạn có số còn phải thu bằng số tiền ⇒
    "khớp theo số tiền" (người xác nhận từng dòng). Hai dòng cùng trỏ một khoản ⇒ tính dồn theo ngày. Khi nhiều khoản cùng trúng, khoản đứng
    trước trong ``receivables`` thắng."""
    from barista.api import name_key
    from barista.modules.acct.bank import invoice_numbers_in

    targets = _targets(receivables, horizon)
    by_invoice: dict[str, list[int]] = defaultdict(list)
    for i, t in enumerate(targets):
        if t["invoice"]:
            by_invoice[t["invoice"]].append(i)
    left = {t["id"]: t["amount_vnd"] - t["paid_vnd"] for t in targets}
    pos = {id(t): i for i, t in enumerate(targets)}
    exact, unique = _Keys([[k for k, _ in t["exact"]] for t in targets]), _Keys([[k for k, _ in t["unique"]] for t in targets])
    hints = _Keys([[k for k, _ in t["hints"]] if t["soon"] else [] for t in targets])
    owed = _Owed([left[t["id"]] for t in targets], [t["soon"] for t in targets])
    out = []
    for ln in sorted(lines, key=lambda x: (x["received_on"], str(x["id"]))):
        key = name_key(f"{ln['memo']} {ln['payer']}")
        r: dict[str, Any] = {**ln, "match": "unmatched", "code": "", "remaining": None, "diff": None, "hint": "", "how": ""}
        t, how = None, ""
        if hit := exact.hits(key):
            i, j = hit[0]
            t, how = targets[i], targets[i]["exact"][j][1]
        if t is None:
            hits = {i: j for i, j in unique.hits(key)}
            if len(hits) == 1:
                i, j = next(iter(hits.items()))
                t, how = targets[i], targets[i]["unique"][j][1]
        if t is None and by_invoice:   # số hoá đơn SAU mã riêng; chỉ nhận khi đúng một khoản và số tiền không vượt phần còn phải thu
            cand = {i for no in invoice_numbers_in(key) for i in by_invoice.get(no, [])}
            if len(cand) == 1 and 0 < ln["amount_vnd"] <= left[targets[(c := next(iter(cand)))]["id"]]:
                t, how = targets[c], "số hoá đơn"
        if t is None:
            hits_h = hints.hits(key)
            if hits_h and len({targets[i]["group"] for i, _ in hits_h}) == 1:
                last = {i: j for i, j in hits_h}   # một khoản trúng nhiều mã gợi ý ⇒ nhãn của mã sau cùng (như dict cũ)
                ordered = sorted(((targets[i], targets[i]["hints"][j][1]) for i, j in last.items()), key=lambda h: h[0]["due_on"])
                t, how = next((h for h in ordered if left[h[0]["id"]] > 0), ordered[0])
        if t is None:
            same = owed.equal(ln["amount_vnd"])
            if len(same) == 1:
                t, how = targets[next(iter(same))], "số tiền"
        if t is None:
            near = [targets[i] for i in owed.nearest(ln["amount_vnd"])]
            r["hint"] = "Không thấy mã khoản / mã riêng / mã gợi ý trong nội dung" + (
                " — gần số tiền nhất: " + ", ".join(x["ref"] for x in near) if near else "")
            out.append(r)
            continue
        rem = left[t["id"]]
        r.update(code=t["ref"], remaining=rem, how=how)
        if rem <= 0:
            r.update(match="settled", hint=f"{t['ref']} đã thu đủ — có thể trả trùng, hỏi lại {t['payer']}")
        elif how == "số tiền":
            r.update(match="amount_only", diff=0, hint="Không có mã trong nội dung — chỉ khớp theo số tiền, kiểm trước khi ghi")
        elif ln["amount_vnd"] == rem:
            r.update(match="full", diff=0)
        elif ln["amount_vnd"] < rem:
            r.update(match="short", diff=rem - ln["amount_vnd"], hint=_hint(rem, ln["amount_vnd"], t["payer"]))
        else:
            r.update(match="over", diff=ln["amount_vnd"] - rem, hint=f"{t['payer']} chuyển thừa — ghi đủ số còn phải thu, phần thừa hỏi lại")
        if r["match"] in ("full", "short", "over", "amount_only"):
            left[t["id"]] = max(0, rem - ln["amount_vnd"])
            owed.set(pos[id(t)], rem, left[t["id"]])
        out.append(r)
    return out


async def _matched(ctx: Ctx, line_ids: list[UUID] | None = None) -> list[dict[str, Any]]:
    # dòng đã ghi một phần (tiền thừa sau lần phân bổ trước) chỉ khớp tiếp phần CHƯA phân bổ
    lines = await ctx.all("""SELECT id, received_on, amount_vnd + deducted_vnd - allocated_vnd AS amount_vnd, amount_vnd AS line_total, allocated_vnd, memo, payer,
                                    bank_ref, source, entity_id, accepted_at FROM acct.statement_lines
                             WHERE status = 'open' AND (%s::uuid[] IS NULL OR id = ANY(%s)) ORDER BY received_on, id""", (line_ids, line_ids))
    receivables = await ctx.all("""SELECT id, code, source, party, amount_vnd, paid_vnd, due_on, status, match_hints, match_keys, entity_id, invoice_no
                                   FROM acct.receivables WHERE status <> 'void'""")
    # 0099: tiền về tài khoản của pháp nhân nào chỉ khớp khoản phải thu CỦA pháp nhân đó (thu hộ giữa pháp nhân chưa hỗ trợ — không đề xuất)
    out: dict[Any, dict[str, Any]] = {}
    for eid in {ln["entity_id"] for ln in lines}:
        for r in match_lines([ln for ln in lines if ln["entity_id"] == eid], [t for t in receivables if t["entity_id"] == eid],
                             today() + timedelta(days=DUE_SOON_DAYS)):
            out[r["id"]] = r
    await _other_entity_hints(ctx, [r for r in out.values() if r["match"] == "unmatched"], receivables)
    return [out[ln["id"]] for ln in lines if ln["id"] in out]


async def _other_entity_hints(ctx: Ctx, unmatched: list[dict[str, Any]], receivables: list[dict[str, Any]]) -> None:
    """Dòng chưa khớp mà nội dung có MÃ KHOẢN của pháp nhân KHÁC ⇒ nói rõ (code biết, không để người / trợ lý đoán): tiền về nhầm tài khoản
    pháp nhân. Vẫn không tự ghép — thu hộ giữa pháp nhân chưa hỗ trợ (0099); người quyết hoàn lại / chuyển nội bộ."""
    if not unmatched:
        return
    from barista.api import name_key

    names = await entities.names(ctx)
    owing = [t for t in receivables if t["paid_vnd"] < t["amount_vnd"]]
    codes = _Keys([[name_key(t["code"])] for t in owing])
    for r in unmatched:
        key = name_key(f"{r['memo']} {r['payer']}")
        hit = next((owing[i] for i, _ in codes.hits(key) if owing[i]["entity_id"] != r["entity_id"]), None)
        if hit is None:
            continue
        own, other = names.get(str(hit["entity_id"]), {}), names.get(str(r["entity_id"]), {})
        r["hint"] = (f"Mã {hit['code']} là khoản của pháp nhân {own.get('code', '?')} ({own.get('name', '')}) nhưng tiền về tài khoản pháp nhân "
                     f"{other.get('code', '?')} — có thể bên trả chuyển nhầm tài khoản. Không ghép tự động (thu hộ giữa pháp nhân chưa hỗ trợ): "
                     f"hỏi {hit['party'] or 'bên trả'} chuyển lại đúng tài khoản, hoặc chuyển nội bộ giữa hai pháp nhân rồi ghi thu bên {own.get('code', '?')}")


RECONCILE_VIEW = {"": "Tất cả", "full": "Khớp đủ — ghi được ngay", "diff": "Lệch số tiền", "review": "Cần người xem"}
_VIEW_MATCH = {"full": ("full",), "diff": ("short", "over"), "review": ("amount_only", "unmatched", "settled", "parked")}
# thứ tự việc của kế toán: ghi dòng khớp đủ → xử lý lệch → xem dòng chưa rõ (rà vai kế toán 18/11/2026: "trả thiếu" từng chìm giữa dòng lẻ)
_MATCH_ORDER = {"full": 0, "short": 1, "over": 2, "amount_only": 3, "unmatched": 4, "parked": 5, "settled": 6}


class ReconcileIn(Input):
    view: str = field("Xem", "", options=RECONCILE_VIEW)


@m.query("reconcile", resource="acct", title="Đối soát sao kê", min_scope="all")
async def reconcile(ctx: Ctx, inp: ReconcileIn) -> dict[str, Any]:
    """Dòng sao kê chưa đối soát, mỗi dòng code đã khớp với khoản phải thu nào (theo mã trong nội dung chuyển khoản + số tiền): khớp đủ / trả thiếu /
    trả thừa / chỉ khớp theo số tiền / chưa khớp, kèm gợi ý nguyên nhân chênh. Ghi hàng loạt = acct.apply_statement (chỉ dòng khớp đủ);
    từng dòng = acct.apply_line."""
    rows = await _matched(ctx)
    total = lambda k: sum(r["amount_vnd"] for r in rows if r["match"] == k)  # noqa: E731
    full = [r for r in rows if r["match"] == "full"]
    for r in rows:
        # MỘT dòng một trạng thái trên mọi màn (rà 23/11/2026): dòng đã ghi vào sổ (tiền về chưa phân bổ — ``accepted_at``) mà chưa khớp khoản nào không
        # phải "chưa ghi" — nó ĐÃ ở sổ cái, chỉ chờ phân bổ vào khoản; màn "Tiền về chưa phân bổ" nói đúng điều này.
        r["state"] = "parked" if r.get("accepted_at") is not None and r["match"] in ("unmatched", "amount_only") else r["match"]
    kpi_rows = rows   # ô số luôn tính trên mọi dòng chờ đối soát, không theo bộ lọc xem
    if inp.view in _VIEW_MATCH:
        rows = [r for r in rows if r["match"] in _VIEW_MATCH[inp.view]]
    rows.sort(key=lambda r: (_MATCH_ORDER.get(r["match"], 9), str(r.get("received_on") or "")))
    return ui.table(rows, [
        # cột quyết định (khớp đâu, chênh bao nhiêu, vì sao) đứng trước — màn hẹp vẫn thấy không phải cuộn ngang
        ui.col("state", "Kết quả", "badge", labels=MATCH, tones=MATCH_TONE), ui.col("amount_vnd", "Số tiền chưa phân bổ", "money"),
        ui.col("code", "Khoản phải thu"), ui.col("diff", "Chênh lệch", "money"), ui.col("hint", "Gợi ý", width=260),
        ui.col("memo", "Nội dung chuyển khoản", width=280), ui.col("received_on", "Ngày", "date"), ui.col("how", "Khớp theo"),
        ui.col("remaining", "Còn phải thu", "money"), ui.col("payer", "Bên chuyển"), ui.col("bank_ref", "Số tham chiếu NH"),
        ui.col("allocated_vnd", "Đã ghi trước đó", "money"),
    ], title="Đối soát sao kê",
        # màn trống TỰ NÓI mình để làm gì + MỘT lối vào là tải file (không gõ tay từng dòng) — rà 23/11/2026, kế hoạch mục 3
        empty="Khớp từng khoản tiền về với khoản phải thu bằng mã trong nội dung chuyển khoản. Bắt đầu: tải file sao kê ngân hàng (Excel) — máy tự "
              "khớp, dòng khớp đủ ghi một lần; dòng lệch hiện gợi ý để bạn quyết.",
        empty_actions=[ui.act("acct.add_statement_line", "Tải sao kê (Excel)", import_only=True, style="primary")],
        # "Ghi các dòng khớp đủ" chỉ có khi CHẠY ĐƯỢC (trước là nút cố định trên thanh công cụ, bấm mới báo "không có dòng nào") — khối đầu bảng mang nút
        header=[ui.b_callout("ok", f"{len(full)} dòng khớp đủ — {_vnd(total('full'))}",
                             "Mỗi dòng khớp đúng một khoản phải thu, đúng số tiền: ghi phiếu thu một lần cho cả nhóm.",
                             actions=[ui.act("acct.apply_statement", f"Ghi {len(full)} dòng khớp đủ", style="primary")])] if full else None,
        kpis=[ui.kpi("Khớp đủ", len(full), tone="green" if full else None, hint=_vnd(total("full")) if full else ""),
              ui.kpi("Lệch số tiền", sum(1 for r in kpi_rows if r["match"] in ("short", "over")), tone="orange"),
              ui.kpi("Cần người xem", sum(1 for r in kpi_rows if r["state"] in ("amount_only", "unmatched", "settled")), tone="red",
                     hint="Chưa khớp mã / chỉ khớp số tiền / khoản đã thu đủ (có thể trả trùng)"),
              *([ui.kpi("Đã vào sổ, chờ phân bổ", sum(1 for r in kpi_rows if r["state"] == "parked"), tone="purple",
                        hint=_vnd(sum(r["amount_vnd"] for r in kpi_rows if r["state"] == "parked")) + " — đã ghi vào Tiền về chưa phân bổ")]
                if any(r["state"] == "parked" for r in kpi_rows) else []),
              ui.kpi("Tổng tiền chưa phân bổ", sum(r["amount_vnd"] for r in kpi_rows), "money", hint="Gồm cả tiền đã vào sổ, chờ phân bổ")],
        note="Khớp bằng mã trong nội dung chuyển khoản + số tiền (code tính, không phải AI đoán). Chênh lệch không tự xoá nợ: ghi phần đã nhận + "
             "giao việc cho kế toán.")


async def _apply(ctx: Ctx, r: dict[str, Any], ref: str = "", confirm_payer: bool = False) -> dict[str, Any]:
    """Một dòng sao kê → phân bổ vào khoản code đã khớp (hoặc khoản người chọn): số ghi = min(phần chưa phân bổ của dòng, số còn phải thu).
    Trả thiếu ⇒ khoản còn nợ + việc cho kế toán; trả thừa ⇒ phần thừa NẰM LẠI ở dòng (tiền về chưa phân bổ, dòng chưa xong) + việc."""
    no = (ref or r["code"]).strip().upper()
    if not no:
        raise Invalid("Dòng này chưa khớp khoản nào — chọn khoản phải thu")
    line = await ctx.one("SELECT * FROM acct.statement_lines WHERE id = %s AND status = 'open' FOR UPDATE", (r["id"],))
    if line is None:
        raise NotFound("Không thấy dòng sao kê đang chờ đối soát")
    t = await _target(ctx, no)
    if ref and no != r["code"]:   # người CHỌN khoản khác khoản code đã khớp ⇒ kiểm bên chuyển (0156)
        _payer_gate(line, t, confirm_payer)
    left, rest = line["amount_vnd"] + line["deducted_vnd"] - line["allocated_vnd"], _rest(t)
    if rest <= 0:
        raise Invalid(f"{no} đã thu đủ / đã huỷ — không ghi thêm")
    out = await _allocate(ctx, line, no, min(left, rest))
    who = t["party"] or "khách"
    diff = rest - left
    task = None
    if diff != 0:
        over = diff < 0
        title = f"{who} chuyển thừa {_vnd(-diff)} — {no}" if over else f"Chênh {_vnd(diff)} — {no} ({who} trả thiếu)"
        notes = (f"Dòng sao kê {line['received_on']:%d/%m/%Y}: {_vnd(left)}, nội dung “{line['memo']}”. "
                 + (f"Đã ghi đủ số còn phải thu; phần thừa {_vnd(-diff)} vẫn nằm ở dòng sao kê (tiền về chưa phân bổ) — phân bổ vào khoản khác "
                    f"hoặc hỏi lại {who}." if over
                    else f"Đã ghi phần đã nhận. Gợi ý: {_hint(rest, left, who)}. Hỏi {who} rồi xử lý phần còn lại — chưa xoá nợ."))
        task = (await ctx.call("flow.create_task", title=title[:300], notes=notes, due_on=(today()).isoformat()))["task_id"]
    return {"line_id": line["id"], "code": no, "payment_id": out["payment_id"], "amount_vnd": out["amount_vnd"], "diff": diff,
            "line_left": out["line_left"], "task_id": task}


class ApplyLine(Input):
    line_id: UUID = field("Dòng sao kê", hidden=True)
    code: str = field("Ghi vào", "", options_query="acct.collect_options",
                      placeholder="Khoản đã khớp", help="Dòng chưa khớp thì chọn khoản phải thu")
    confirm_payer: bool = field("Đúng bên trả", False, help="Chỉ khi tên bên chuyển trên sao kê khác bên trả của khoản đã chọn mà đúng là tiền của họ", group="Thêm chi tiết")


@m.command("apply_line", resource="acct", title="Ghi phiếu thu từ dòng sao kê")
async def apply_line(ctx: Ctx, inp: ApplyLine) -> dict[str, Any]:
    """Một dòng sao kê → phiếu thu (mặc định vào khoản code đã khớp; dòng chưa khớp / khớp theo số tiền thì người chọn). Trả thiếu ⇒ ghi phần
    đã nhận + việc "chênh lệch" cho kế toán; trả thừa ⇒ ghi đủ số còn phải thu + việc hỏi lại phần thừa."""
    rows = await _matched(ctx, [inp.line_id])
    if not rows:
        raise NotFound("Không thấy dòng sao kê đang chờ đối soát")
    res = await _apply(ctx, rows[0], inp.code, inp.confirm_payer)
    res["summary"] = f"Ghi {_vnd(res['amount_vnd'])} vào {res['code']}" + (
        " · đã giao việc xử lý chênh lệch" if res["task_id"] else "")
    return res


@m.command("apply_statement",
           purpose="Dòng tiền ngân hàng khớp đủ với khoản phải thu — ghi phiếu thu hàng loạt để công nợ khớp tiền về.", irreversible=True, effect="Ghi phiếu thu vào sổ cái cho mọi dòng khớp đủ; muốn sửa phải đảo từng phiếu thu.",
           resource="acct", title="Ghi phiếu thu các dòng khớp đủ")
async def apply_statement(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Ghi hàng loạt CHỈ các dòng khớp ĐỦ theo mã (khớp lại lúc bấm — không tin danh sách cũ). Mỗi dòng phân bổ vào khoản của nó ⇒ gói nguồn
    phản ứng (ghi nhận của gói, việc theo dõi tự đóng, hồ sơ chuyển chặng). Dòng lệch / chưa khớp để lại cho người xem từng dòng."""
    # khớp chỉ nhờ số hoá đơn (số ngắn, có thể trùng ngẫu nhiên) không ghi hàng loạt — kế toán xem từng dòng (acct.apply_line)
    done = [await _apply(ctx, r) for r in await _matched(ctx) if r["match"] == "full" and r["how"] != "số hoá đơn"]
    if not done:
        raise Invalid("Không có dòng nào khớp đủ để ghi")
    total = sum(d["amount_vnd"] for d in done)
    return {"applied": len(done), "total_vnd": total, "codes": [d["code"] for d in done],
            "summary": f"Ghi {len(done)} phiếu thu, tổng {_vnd(total)}"}


class IgnoreLine(Input):
    line_id: UUID = field("Dòng sao kê", hidden=True)
    note: str = field("Lý do", max_length=500, example="Tiền nội bộ chuyển giữa hai tài khoản công ty")


@m.command("ignore_line", resource="acct", title="Bỏ qua dòng sao kê")
async def ignore_line(ctx: Ctx, inp: IgnoreLine) -> dict[str, Any]:
    line = await ctx.one("SELECT allocated_vnd, amount_vnd, accepted_at FROM acct.statement_lines WHERE id = %s AND status = 'open' FOR UPDATE",
                         (inp.line_id,))
    if line is None:
        raise NotFound("Không thấy dòng sao kê đang chờ đối soát")
    if line["allocated_vnd"] or line["accepted_at"] is not None:   # 0156: đã vào sổ (kể cả khi phiếu thu đã đảo hết) ⇒ không bỏ qua được
        # tiền đã vào sổ ⇒ không "bỏ qua" được phần còn lại: phân bổ vào khoản khác; hoàn / ghi khác chờ khách chốt luật (G17)
        raise Invalid(f"Dòng đã ghi {_vnd(line['allocated_vnd'])} — phần còn lại {_vnd(line['amount_vnd'] - line['allocated_vnd'])} là tiền về "
                      "chưa phân bổ: phân bổ vào khoản khác (Ghi phiếu thu, chọn khoản)")
    await ctx.exec("""UPDATE acct.statement_lines SET status = 'ignored', note = %s, decided_by = core.me(), decided_at = now()
                      WHERE id = %s""", (inp.note, inp.line_id))
    return {"line_id": inp.line_id, "summary": "Đã bỏ qua dòng sao kê"}
