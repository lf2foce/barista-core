"""HỒ SƠ ĐIỀU TRA KHOẢN LỆCH (0223) — PHẦN TÍNH, hàm thuần (test thẳng, không chạm DB): ứng viên có điểm, lời giải thích theo mẫu đã biết, hành động đề xuất,
tin nháp hỏi bên trả.

Nguyên tắc: CODE tính mọi con số và mọi câu kết luận; model (nếu có) chỉ được diễn đạt lại từ các sự thật này — không đoán nguyên nhân. Mỗi lời giải thích mang
BẰNG CHỨNG (mã khoản, số tiền, mã dòng) và độ chắc: ``high`` (nội dung chuyển khoản / số hoá đơn / số học chỉ ra đúng một khoản), ``medium`` / ``low``
("chưa chắc" — chỉ là trùng khớp số học hoặc tên, KHÔNG phải xác nhận của bên trả). Tương quan không bao giờ được viết thành nguyên nhân.
"""

from collections.abc import Iterator
from datetime import date
from itertools import combinations
from typing import Any

from barista.api import name_key
from barista.modules.acct.bank import invoice_numbers_in

MIN_SCORE = 30           # điểm tối thiểu để một khoản phải thu là ứng viên
MAX_CANDIDATES = 5
OVERDUE_MIN_SCORE = 50   # dòng còn mới chỉ dựng hồ sơ khi có khoản QUÁ HẠN trúng ít nhất mức này (mã khoản / mã riêng / số hoá đơn, hoặc tên + số tiền)
DATE_WINDOW_DAYS = 30
DUP_WINDOW_DAYS = 7
VAT_TOLERANCE_VND = 1_000   # lệch ≤ mức này vẫn coi là "đúng số chưa gồm thuế" (làm tròn của bên trả)
POOL_MAX = 14               # tổ hợp nhiều khoản chỉ tìm trong chừng này ứng viên tốt nhất
SUBSET_MAX = 6

PATTERNS = {"exact": "Khớp đủ, chưa ghi", "multi": "Một lần chuyển cho nhiều khoản", "vat_short": "Thiếu đúng phần thuế GTGT",
            "other_entity": "Có thể là tiền của pháp nhân khác", "duplicate": "Nghi chuyển trùng", "fee": "Thiếu nhỏ — nghi phí / khấu trừ của bên trả",
            "partial": "Trả một phần", "over": "Chuyển thừa", "unknown": "Chưa tìm thấy khoản nào khớp"}
PRIORITY = list(PATTERNS)          # thứ tự ưu tiên khi cùng độ chắc
CONFIDENCE = {"high": "Chắc", "medium": "Chưa chắc", "low": "Chưa chắc (yếu)"}
CONF_RANK = {"high": 0, "medium": 1, "low": 2}
REF_REASONS = ("mã khoản trong nội dung", "mã riêng trong nội dung", "số hoá đơn trong nội dung")


def vnd(n: int) -> str:
    return f"{n:,}".replace(",", ".") + " đ"


def _pct(bps: int) -> str:
    return f"{bps / 100:g}%"


# ── Chuẩn bị ──

def prepare(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Thêm các khoá đã chuẩn hoá (một lần cho cả vòng quét) vào bản sao của từng khoản phải thu chưa huỷ."""
    out = []
    for r in recs:
        if r["status"] == "void":
            continue
        rest = 0 if r["status"] == "paid" else r["amount_vnd"] - r["paid_vnd"]
        out.append({**r, "rest": rest, "k_code": name_key(r["code"]), "k_party": name_key(r.get("party") or ""),
                    "k_keys": [k for x in r.get("match_keys") or [] if len(k := name_key(x)) >= 4],
                    "k_hints": [k for x in r.get("match_hints") or [] if len(k := name_key(x)) >= 4],
                    "inv": str(int(n)) if (n := "".join(ch for ch in str(r.get("invoice_no") or "").rsplit("-", 1)[-1] if ch.isdigit())) else ""})
    return out


def _payer_match(payer_key: str, party_key: str) -> bool:
    return len(payer_key) >= 4 and len(party_key) >= 4 and (payer_key in party_key or party_key in payer_key)


def vat_net(r: dict[str, Any], vat: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Số khoản này sẽ về nếu bên trả chuyển SỐ CHƯA GỒM THUẾ — chỉ khi chưa thu đồng nào (còn nguyên số phải thu) và công ty có dữ liệu thuế của khoản:
    ``kind='invoice'`` = hoá đơn GTGT đã ghi (thuế chính xác), ``'category'`` = suy từ loại thuế + bộ quy định (GIẢ ĐỊNH: hoá đơn chưa ghi)."""
    v = vat.get(r["code"])
    if not v or not v.get("tax_vnd") or r["paid_vnd"] or r["rest"] != r["amount_vnd"] or r["rest"] <= v["tax_vnd"]:
        return None
    return {"kind": v["kind"], "rate_bps": v.get("rate_bps"), "tax_vnd": v["tax_vnd"], "expected_vnd": r["rest"] - v["tax_vnd"]}


# ── Ứng viên ──

def score_candidates(ln: dict[str, Any], recs: list[dict[str, Any]], vat: dict[str, dict[str, Any]], on: date) -> list[dict[str, Any]]:
    """Khoản phải thu CÙNG PHÁP NHÂN mà dòng tiền có thể đang trả, theo điểm (0–100) cộng từ: mã trong nội dung (50 / 40 / 20), tên bên chuyển, số tiền,
    ngày gần hạn. Điểm chỉ để XẾP HẠNG (không phải xác suất) — lý do từng điểm ghi kèm."""
    key = name_key(f"{ln['memo']} {ln['payer']}")
    payer = name_key(ln["payer"] or "")
    invs = invoice_numbers_in(key)
    left = ln["left"]
    out = []
    for r in recs:
        if r["entity_id"] != ln["entity_id"]:
            continue
        s, why = 0, []
        if r["k_code"] and r["k_code"] in key:
            s, why = s + 50, [*why, REF_REASONS[0]]
        elif any(k in key for k in r["k_keys"]):
            s, why = s + 40, [*why, REF_REASONS[1]]
        elif r["inv"] and r["inv"] in invs:
            s, why = s + 40, [*why, REF_REASONS[2]]
        elif any(k in key for k in r["k_hints"]):
            s, why = s + 20, [*why, "mã gợi ý trong nội dung"]
        if _payer_match(payer, r["k_party"]):
            s, why = s + 25, [*why, "tên bên chuyển trùng bên trả"]
        if r["rest"] > 0:
            if left == r["rest"]:
                s, why = s + 25, [*why, "đúng số còn phải thu"]
            elif (n := vat_net(r, vat)) and abs(left - n["expected_vnd"]) <= VAT_TOLERANCE_VND:
                s, why = s + 22, [*why, "đúng số còn phải thu trừ thuế GTGT"]
            elif r["rest"] > left > 0 and left * 2 >= r["rest"]:
                s, why = s + 6, [*why, "bằng một phần số còn phải thu"]
        elif left == r["amount_vnd"]:
            s, why = s + 10, [*why, "đúng số của khoản đã thu đủ"]
        if abs((ln["received_on"] - r["due_on"]).days) <= DATE_WINDOW_DAYS:
            s, why = s + 5, [*why, "ngày gần hạn thu"]
        if s >= MIN_SCORE:
            out.append({"code": r["code"], "party": r["party"] or "", "amount_vnd": r["amount_vnd"], "paid_vnd": r["paid_vnd"], "rest_vnd": r["rest"],
                        "due_on": r["due_on"].isoformat(), "overdue": r["rest"] > 0 and r["due_on"] < on, "source": r["source"], "score": min(s, 100),
                        "why": why, "has_ref": any(w in REF_REASONS for w in why), "payer_match": "tên bên chuyển trùng bên trả" in why})
    out.sort(key=lambda c: (-c["score"], c["due_on"], c["code"]))
    return out[:MAX_CANDIDATES]


def _subsets(items: list[tuple[str, int]], target: int, limit: int = 3) -> list[list[str]]:
    """Tổ hợp 2..SUBSET_MAX khoản có tổng ĐÚNG ``target`` (tối đa ``limit``); đủ nhỏ (POOL_MAX) nên duyệt thẳng, không cần quy hoạch động."""
    found: list[list[str]] = []
    for n in range(2, min(SUBSET_MAX, len(items)) + 1):
        for combo in combinations(items, n):
            if sum(v for _, v in combo) == target:
                found.append([c for c, _ in combo])
                if len(found) >= limit:
                    return found
    return found


# ── Lời giải thích ──

def explain(ln: dict[str, Any], recs: list[dict[str, Any]], cands: list[dict[str, Any]], others: list[dict[str, Any]], vat: dict[str, dict[str, Any]],
            entities: dict[str, dict[str, Any]], fee_tolerance: int) -> list[dict[str, Any]]:
    """Các lời giải thích CODE tìm được, xếp theo độ chắc rồi theo ưu tiên mẫu. Mỗi mục: ``{pattern, label, confidence, sure, summary, evidence, codes}``.
    ``others`` = dòng sao kê khác cùng pháp nhân, cùng số tiền, gần ngày (đã lọc ở tầng DB). Không mẫu nào khớp ⇒ ``unknown``."""
    left, key = ln["left"], name_key(f"{ln['memo']} {ln['payer']}")
    payer = name_key(ln["payer"] or "")
    by_code = {r["code"]: r for r in recs}
    out: list[dict[str, Any]] = []

    def add(pattern: str, conf: str, summary: str, evidence: dict[str, Any], codes: list[str]) -> None:
        out.append({"pattern": pattern, "label": PATTERNS[pattern], "confidence": conf, "sure": conf == "high", "summary": summary, "evidence": evidence,
                    "codes": codes})

    owing = [c for c in cands if c["rest_vnd"] > 0]
    top = owing[0] if owing else None
    top_r = by_code[top["code"]] if top else None

    # 1. khớp đủ — nội dung chỉ ra khoản, số tiền đúng bằng số còn phải thu
    if top and top["has_ref"] and left == top["rest_vnd"]:
        add("exact", "high", f"Số tiền {vnd(left)} đúng bằng số còn phải thu của {top['code']} và nội dung chuyển khoản có {top['why'][0]}. Chỉ chờ ghi phiếu thu.",
            {"receivable": top["code"], "rest_vnd": top["rest_vnd"], "received_vnd": left}, [top["code"]])

    # 2. một lần chuyển cho nhiều khoản — tổng các khoản còn phải thu = đúng số tiền
    pool = [c for c in owing if c["has_ref"] or c["payer_match"]][:POOL_MAX]
    if len(pool) >= 2:
        subs = _subsets([(c["code"], c["rest_vnd"]) for c in pool], left)
        if subs:
            flag = {c["code"]: c["has_ref"] or c["payer_match"] for c in pool}
            first = subs[0]
            conf = "high" if len(subs) == 1 and all(flag[c] for c in first) and any(c["has_ref"] for c in pool if c["code"] in first) else (
                "medium" if len(subs) == 1 else "low")
            rests = {c["code"]: c["rest_vnd"] for c in pool}
            add("multi", conf, f"Tổng số còn phải thu của {' + '.join(first)} = {' + '.join(vnd(rests[c]) for c in first)} = {vnd(left)}, đúng số tiền về"
                + ("" if conf == "high" else f" (trùng khớp số học — {'có ' + str(len(subs)) + ' tổ hợp khác nhau' if len(subs) > 1 else 'chưa có nội dung nào xác nhận'}; "
                                                f"chưa phải lời xác nhận của bên trả)") + ".",
                {"receivables": first, "rests": [rests[c] for c in first], "sum_vnd": left, "alternatives": subs[1:]}, first)

    # 3–5. một khoản trúng nhưng số tiền không bằng số còn phải thu
    if top and top_r is not None and left != top["rest_vnd"]:
        diff = top["rest_vnd"] - left
        sure_ref = top["has_ref"]
        net = vat_net(top_r, vat)
        if net and abs(left - net["expected_vnd"]) <= VAT_TOLERANCE_VND:
            conf = "high" if sure_ref and net["kind"] == "invoice" else "medium"
            rate = f" {_pct(net['rate_bps'])}" if net["rate_bps"] else ""
            add("vat_short", conf,
                f"Số tiền {vnd(left)} = {top['code']} ({vnd(top['rest_vnd'])}) trừ thuế GTGT{rate} ({vnd(net['tax_vnd'])}) = {vnd(net['expected_vnd'])}: "
                f"dấu hiệu bên trả chuyển số CHƯA gồm thuế" + (" (thuế theo hoá đơn đã ghi)" if net["kind"] == "invoice" else " (thuế suy từ loại thuế công ty — hoá đơn chưa ghi, là giả định)")
                + ("." if conf == "high" else "; chưa phải xác nhận của bên trả."),
                {"receivable": top["code"], "rest_vnd": top["rest_vnd"], "tax_vnd": net["tax_vnd"], "rate_bps": net["rate_bps"], "expected_vnd": net["expected_vnd"],
                 "received_vnd": left, "tax_source": net["kind"]}, [top["code"]])
        elif 0 < diff <= fee_tolerance:
            add("fee", "medium" if top["score"] >= 40 else "low",
                f"Thiếu {vnd(diff)} so với số còn phải thu của {top['code']} ({vnd(top['rest_vnd'])}) — nhỏ hơn ngưỡng {vnd(fee_tolerance)} nên có thể là phí chuyển khoản "
                "hoặc khoản bên trả giữ lại; chưa có chứng cứ nào nói rõ lý do.",
                {"receivable": top["code"], "rest_vnd": top["rest_vnd"], "received_vnd": left, "diff_vnd": diff, "tolerance_vnd": fee_tolerance}, [top["code"]])
        elif diff > 0:
            ratio = round(100 * left / top["rest_vnd"], 1)
            add("partial", "high" if sure_ref else ("medium" if top["payer_match"] else "low"),
                f"Số tiền {vnd(left)} bằng {ratio:g}% số còn phải thu của {top['code']} ({vnd(top['rest_vnd'])}); còn thiếu {vnd(diff)}. "
                "Đây là phép so số học — chưa rõ lý do bên trả chuyển thiếu.",
                {"receivable": top["code"], "rest_vnd": top["rest_vnd"], "received_vnd": left, "diff_vnd": diff, "ratio_pct": ratio}, [top["code"]])
        else:
            add("over", "high" if sure_ref else ("medium" if top["payer_match"] else "low"),
                f"Số tiền {vnd(left)} lớn hơn số còn phải thu của {top['code']} ({vnd(top['rest_vnd'])}) {vnd(-diff)}: ghi đủ số còn phải thu, phần thừa nằm lại ở tiền về chưa phân bổ.",
                {"receivable": top["code"], "rest_vnd": top["rest_vnd"], "received_vnd": left, "over_vnd": -diff}, [top["code"]])

    # 6. tiền của pháp nhân khác — khoản của pháp nhân khác có mã trong nội dung / đúng số tiền
    for r in recs:
        if r["entity_id"] == ln["entity_id"] or r["rest"] <= 0:
            continue
        by_ref = r["k_code"] in key
        by_amount = r["rest"] == left and _payer_match(payer, r["k_party"])
        if not (by_ref or by_amount):
            continue
        own, here = entities.get(str(r["entity_id"]), {}), entities.get(str(ln["entity_id"]), {})
        add("other_entity", "high" if by_ref else "medium",
            f"{'Nội dung có mã' if by_ref else 'Số tiền và tên bên chuyển trùng khoản'} {r['code']} — khoản của pháp nhân {own.get('code', '?')} ({own.get('name', '')}), "
            f"nhưng tiền về tài khoản pháp nhân {here.get('code', '?')}. Thu hộ giữa hai pháp nhân chưa hỗ trợ: cần chuyển nội bộ hoặc bên trả chuyển lại.",
            {"receivable": r["code"], "receivable_entity": own.get("code", ""), "line_entity": here.get("code", ""), "rest_vnd": r["rest"], "by": "ref" if by_ref else "amount"},
            [r["code"]])
        break

    # 7. nghi chuyển trùng — dòng khác cùng bên chuyển, cùng số tiền, gần ngày; hoặc khoản trúng đã thu đủ
    twin = next((o for o in others if o["entity_id"] == ln["entity_id"] and o["id"] != ln["id"]
                 and (_payer_match(payer, name_key(o["payer"] or "")) or (not payer and name_key(o["memo"] or "") == name_key(ln["memo"] or "")))), None)
    settled = next((c for c in cands if c["rest_vnd"] <= 0 and c["paid_vnd"] > 0 and c["has_ref"]), None)
    if twin or settled:
        done = bool(twin and (twin["status"] == "applied" or twin["allocated_vnd"] > 0))
        conf = "high" if (twin and done and payer) or (settled and twin) else ("medium" if twin or settled else "low")
        bits = []
        if twin:
            bits.append(f"có dòng sao kê khác {twin['bank_ref'] or twin['id']} ngày {twin['received_on']:%d/%m/%Y} cùng số tiền {vnd(twin['amount_vnd'])}, cùng bên chuyển"
                        + (" và đã ghi vào khoản" if done else " nhưng chưa ghi"))
        if settled:
            bits.append(f"khoản {settled['code']} nêu trong nội dung đã thu đủ ({vnd(settled['paid_vnd'])})")
        add("duplicate", conf, "Dấu hiệu chuyển trùng: " + "; ".join(bits) + ("." if conf == "high" else ". Chưa chắc — thanh toán định kỳ cùng số tiền cũng trông như vậy."),
            {"twin_line": str(twin["id"]) if twin else "", "twin_bank_ref": twin["bank_ref"] if twin else "", "twin_status": twin["status"] if twin else "",
             "settled_receivable": settled["code"] if settled else "", "amount_vnd": left},
            [settled["code"]] if settled else [])

    if not out:
        near = [c["code"] for c in cands[:3]]
        add("unknown", "low", "Không thấy mã khoản, số hoá đơn, bên trả hay số tiền nào khớp rõ với khoản phải thu đang mở"
            + (f"; gần nhất: {', '.join(near)} (điểm thấp, chỉ để tham khảo)" if near else "") + ". Cần hỏi bên chuyển.", {"nearest": near}, [])
    out.sort(key=lambda e: (CONF_RANK[e["confidence"]], PRIORITY.index(e["pattern"])))
    return out


# ── Hành động đề xuất (mỗi bước = MỘT lệnh Tài chính có sẵn, người bấm xác nhận — hồ sơ không ghi sổ) ──

def _step(command: str, title: str, **inp: Any) -> dict[str, Any]:
    return {"command": command, "title": title, "input": inp}


def actions_for(dossier: str, ln: dict[str, Any], primary: dict[str, Any], vat_invoiced: set[str], payer_label: str) -> list[dict[str, Any]]:
    """Hành động theo lời giải thích chính. ``vat_invoiced`` = khoản đã có hoá đơn GTGT (không giảm phải thu thẳng được)."""
    lid, pat, ev = str(ln["id"]), primary["pattern"], primary["evidence"]
    acts: list[dict[str, Any]] = []

    def apply(code: str) -> dict[str, Any]:
        return _step("acct.apply_line", f"Ghi phiếu thu vào {code}", line_id=lid, code=code)

    if pat in ("exact", "over"):
        acts.append({"key": "apply", "label": f"Ghi phiếu thu vào {primary['codes'][0]}", "why": "Số tiền đủ để ghi; phần thừa (nếu có) nằm lại ở tiền chưa phân bổ.",
                     "steps": [apply(primary["codes"][0])], "money": True})
    elif pat == "multi":
        acts.append({"key": "allocate_many", "label": f"Ghi lần lượt vào {len(primary['codes'])} khoản", "why": "Một lần chuyển, nhiều khoản — mỗi bước là một phiếu thu.",
                     "steps": [apply(c) for c in primary["codes"]], "money": True})
    elif pat == "vat_short":
        code = ev["receivable"]
        acts.append({"key": "allocate_partial", "label": f"Ghi phần đã nhận vào {code}", "why": "Ghi đúng số tiền đã về; phần thuế còn thiếu vẫn là khoản phải thu.",
                     "steps": [apply(code)], "money": True})
        acts.append({"key": "vat_follow_up", "label": "Ghi nhận chênh lệch thuế GTGT cần bên trả bù",
                     "why": "Không xoá nợ: đánh dấu phần thiếu là thuế GTGT để theo dõi tới khi bên trả bù đủ.",
                     "steps": [apply(code), _step("acct.mark_dossier", "Ghi nhận thiếu thuế GTGT cần theo dõi", code=dossier, mark="vat_follow_up",
                                                  note=f"Bên trả chuyển số chưa gồm thuế GTGT {vnd(ev['tax_vnd'])} của {code}")], "money": True})
    elif pat in ("fee", "partial"):
        code, diff = ev["receivable"], ev["diff_vnd"]
        acts.append({"key": "allocate_partial", "label": f"Ghi phần đã nhận vào {code}", "why": f"Ghi {vnd(ev['received_vnd'])}; còn nợ {vnd(diff)} — chưa xoá.",
                     "steps": [apply(code)], "money": True})
        if code not in vat_invoiced:
            acts.append({"key": "settle_deduction", "label": f"Ghi nhận và xoá phần thiếu {vnd(diff)} (cần duyệt)",
                         "why": "Chỉ khi kế toán / bên trả xác nhận phần giữ lại là hợp lệ (phí, khấu trừ). Giảm phải thu qua giấy báo có — người duyệt khác người xin.",
                         "steps": [apply(code), _step("acct.write_off_dossier", f"Xoá phần thiếu {vnd(diff)} của {code}", code=dossier, receivable=code, amount_vnd=diff,
                                                      reason=f"Bên trả giữ lại {vnd(diff)} (phí / khấu trừ) — kế toán xác nhận, hồ sơ {dossier}")], "money": True,
                         "approval": True})
    elif pat == "other_entity":
        acts.append({"key": "other_entity", "label": f"Đánh dấu là tiền của pháp nhân {ev['receivable_entity']}",
                     "why": "Không ghi sổ: chỉ ghi nhận để kế toán chuyển nội bộ / yêu cầu bên trả chuyển lại.",
                     "steps": [_step("acct.mark_dossier", "Đánh dấu tiền của pháp nhân khác", code=dossier, mark="other_entity",
                                     note=f"Khoản {ev['receivable']} thuộc {ev['receivable_entity']}, tiền về tài khoản {ev['line_entity']}")], "money": False})
    if pat != "exact":
        acts.append({"key": "ask_payer", "label": f"Soạn tin hỏi {payer_label} (chỉ nháp — không tự gửi)", "why": "Tin nháp dựng từ các con số của hồ sơ; người gửi là kế toán.",
                     "steps": [_step("acct.draft_dossier_ask", "Soạn tin nháp", code=dossier)], "money": False})
    if pat in ("unknown", "duplicate"):
        acts.append({"key": "dismiss", "label": "Đóng hồ sơ — không phải khoản lệch", "why": "Ghi rõ lý do (vd chuyển nội bộ, đã xử lý ngoài hệ thống).",
                     "steps": [_step("acct.dismiss_dossier", "Đóng hồ sơ", code=dossier)], "money": False})
    return acts


def draft_ask(ln: dict[str, Any], primary: dict[str, Any], cand: dict[str, Any] | None) -> str:
    """Tin nháp hỏi bên trả, dựng bằng mẫu từ sự thật của hồ sơ. Không gửi, không có số nào ngoài hồ sơ."""
    who = (ln.get("payer") or "").strip() or "Quý khách"
    head = (f"Kính gửi {who},\nNgày {ln['received_on']:%d/%m/%Y} chúng tôi nhận được khoản chuyển {vnd(ln['amount_vnd'])}"
            + (f" (mã giao dịch {ln['bank_ref']})" if ln.get("bank_ref") else "") + (f", nội dung “{ln['memo'].strip()[:120]}”" if (ln.get("memo") or "").strip() else "") + ".")
    ev, pat = primary["evidence"], primary["pattern"]
    if pat in ("partial", "fee", "vat_short"):
        body = (f"Khoản này đối chiếu với {ev['receivable']} (số còn phải thu {vnd(ev['rest_vnd'])}); số nhận được thấp hơn {vnd(ev['rest_vnd'] - ev['received_vnd'])}. "
                + (f"Chúng tôi thấy chênh lệch trùng phần thuế GTGT {vnd(ev['tax_vnd'])}. " if pat == "vat_short" else "")
                + "Anh/chị vui lòng cho biết lý do chênh lệch và thời điểm chuyển phần còn lại (hoặc chứng từ khấu trừ nếu có).")
    elif pat == "multi":
        body = (f"Chúng tôi dự kiến ghi khoản này vào {', '.join(ev['receivables'])}. Anh/chị vui lòng xác nhận đúng ý, hoặc cho biết khoản nào cần ghi.")
    elif pat == "duplicate":
        body = ("Chúng tôi thấy có hai khoản chuyển giống nhau về số tiền trong thời gian ngắn. Anh/chị vui lòng xác nhận có chuyển trùng không; "
                "nếu trùng, chúng tôi sẽ trao đổi cách hoàn lại hoặc trừ vào kỳ thanh toán sau.")
    elif pat == "other_entity":
        body = (f"Khoản phải trả {ev['receivable']} thuộc pháp nhân {ev['receivable_entity']}, nhưng tiền về tài khoản pháp nhân {ev['line_entity']}. "
                "Anh/chị vui lòng xác nhận để chúng tôi xử lý nội bộ hoặc đề nghị chuyển lại đúng tài khoản.")
    elif pat == "over":
        body = (f"Số nhận được cao hơn số phải thanh toán của {ev['receivable']} là {vnd(ev['over_vnd'])}. Anh/chị vui lòng cho biết phần thừa dùng để thanh toán khoản nào, "
                "hoặc đề nghị chúng tôi hoàn lại.")
    else:
        body = ("Hiện chúng tôi chưa xác định được khoản phải thu tương ứng. Anh/chị vui lòng cho biết mã thanh toán / số hợp đồng / số hoá đơn mà khoản chuyển này thanh toán.")
    return f"{head}\n{body}\nXin cảm ơn."


def best_overdue(cands: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Ứng viên quá hạn trúng đủ mạnh để dựng hồ sơ cho dòng còn mới."""
    return next((c for c in cands if c["overdue"] and c["score"] >= OVERDUE_MIN_SCORE), None)


def iter_codes(expl: list[dict[str, Any]]) -> Iterator[str]:
    for e in expl[:1]:
        yield from e["codes"]
