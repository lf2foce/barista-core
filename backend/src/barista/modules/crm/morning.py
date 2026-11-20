"""Thẻ buổi sáng: sáng nay chăm khách nào trước (độ gấp, lý do, tin soạn sẵn), hẹn hôm nay, khách sắp đóng tiền; câu tín hiệu cho bàn việc."""

from datetime import date, datetime, timedelta
from typing import Any

from barista.api import Ctx, Line, NoInput, kpi, line, num, phone_display, today, ui, zone
from barista.modules.crm import APPT_KIND, interest_text, m, reveal
from barista.modules.crm.agenda import open_appointments
from barista.modules.crm.journey import _day, _draft
from barista.modules.crm.leads import offering_names

# Thứ tự gấp: quá hạn gọi lại > lead mới chưa gọi > hẹn hôm nay > khách nóng > sắp nguội.
URGENCY = {"first_call": ("Lead mới — gọi ngay", "bad", -1), "overdue": ("Quá hạn gọi lại", "bad", 0), "uncalled": ("Chưa ai gọi", "bad", 1), "due_today": ("Hẹn gọi hôm nay", "warn", 2),
           "hot": ("Khách nóng", "accent", 3), "cooling": ("Sắp nguội", "muted", 4)}


@m.query("brief", resource="crm", title="Việc hôm nay")
async def brief(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Sáng nay chăm khách nào trước — chỉ khách CỦA NGƯỜI XEM (giám đốc thấy mọi khách nhưng không gọi thay sale; góc nhìn quản lý là
    "Chia lead & hạn gọi"): xếp theo độ gấp, mỗi khách có LÝ DO, dữ kiện đứng sau và tin nhắn soạn sẵn; kèm đợt khách sắp đóng
    tiền. Chỉ soạn, không gửi. Số do server tính, trợ lý chỉ nói lại."""
    t = today()
    leads = await ctx.all(
        """SELECT l.id, l.stage, l.offering_code, l.interest, l.next_follow_up, l.last_activity_at, l.created_at, c.name AS customer, c.phone,
                  (SELECT max(a.at) FROM crm.activities a WHERE a.lead_id = l.id AND (a.outcome = 'connected' OR a.kind IN ('meet', 'visit'))) AS last_talk
           FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id
           WHERE l.stage IN ('lead', 'prospect') AND l.owner_id = core.me() LIMIT 2000""")   # khách CỦA TÔI, không phải mọi khách tôi được xem
    await reveal.mask_rows(ctx, leads)   # lead chờ bấm nhận theo luật có che ⇒ số đã che, nút là "Nhận lead" (0214)
    ranked: list[tuple[int, date, str, dict[str, Any], list[str]]] = []
    now = datetime.now(zone())
    sla = await ctx.call_system("leadflow.lead_sla", lead_ids=[r["id"] for r in leads if r["last_activity_at"] is None])
    for r in leads:
        facts: list[str] = []
        kind = None
        s = sla.get(str(r["id"]))
        if s and s["status"] in ("offered", "waiting") and r["last_activity_at"] is None:   # lead vừa được chia: có hạn gọi lần đầu
            kind = "first_call"
            due_at = s["first_call_due"].astimezone(zone())
            late = int((now - due_at).total_seconds() // 60)
            facts.append(f"Hạn gọi lần đầu {due_at:%H:%M %d/%m}" + (f" — đã trễ {late} phút, có thể bị thu hồi" if late > 0 else ""))
        elif r["next_follow_up"] and r["next_follow_up"] < t:
            kind = "overdue"
            facts.append(f"Hẹn gọi lại {r['next_follow_up']:%d/%m} — trễ {(t - r['next_follow_up']).days} ngày")
        elif r["last_activity_at"] is None and (t - _day(r["created_at"])).days >= 1:
            kind = "uncalled"
            facts.append(f"Lead về {_day(r['created_at']):%d/%m}, chưa có lần chăm sóc nào")
        elif r["next_follow_up"] == t:
            kind = "due_today"
            facts.append("Đã hẹn gọi lại hôm nay")
        elif r["stage"] == "prospect" and r["last_talk"] and (t - _day(r["last_talk"])).days <= 7:
            kind = "hot"
            facts.append(f"Tiềm năng, nói chuyện lần cuối {_day(r['last_talk']):%d/%m}")
        elif r["last_activity_at"] and (t - _day(r["last_activity_at"])).days >= 10:
            kind = "cooling"
            facts.append(f"Chăm sóc lần cuối {_day(r['last_activity_at']):%d/%m} ({(t - _day(r['last_activity_at'])).days} ngày trước)")
        if kind is None:
            continue
        if r["interest"]:
            facts.append("Khách cần: " + interest_text(r["interest"]))
        ranked.append((URGENCY[kind][2], r["next_follow_up"] or _day(r["created_at"]), kind, r, facts))
    # Hoà (cùng độ gấp, cùng ngày — lead nhập hàng loạt có cùng created_at / hẹn hôm nay): lead về trước lên trước, rồi theo mã. SQL trên không có ORDER BY nên
    # không có khoá phụ thì thứ tự hoà là thứ tự đống của Postgres — đổi theo lần UPDATE, mỗi lần mở bản tin một khác (và "5 khách đầu" cắt ngẫu nhiên).
    ranked.sort(key=lambda x: (x[0], x[1], x[3]["created_at"], x[3]["id"]))

    due = [r for part in await ctx.plugs("crm.customer_due", days=7, mine=True) for r in part]
    sender = ctx.p.name.split()[-1] if ctx.p.name else "em"
    appts = await open_appointments(ctx, datetime.combine(t, datetime.min.time(), zone()), datetime.combine(t + timedelta(days=1), datetime.min.time(), zone()),
                                    mine=True)   # giờ hẹn từ lịch chung (0158)
    blocks: list[dict[str, Any]] = [ui.b_kpis([
        ui.kpi("Hẹn hôm nay", len(appts)),
        ui.kpi("Cần chăm hôm nay", len(ranked)),
        ui.kpi("Quá hạn / chưa gọi", sum(1 for x in ranked if x[2] in ("first_call", "overdue", "uncalled")),
               tone="bad" if any(x[2] in ("first_call", "overdue", "uncalled") for x in ranked) else None),
        ui.kpi("Khách sắp đóng tiền (7 ngày)", len(due))])]
    ask = None
    for part in await ctx.plugs("crm.brief_extra"):   # việc tiếp theo sinh từ sự kiện thật của gói khác (cuộc gọi vừa xong, số lạ…) — bằng quyền người xem, gói tắt thì thôi (0178)
        blocks += part.get("blocks") or []
        ask = ask or part.get("ask")
    if appts:
        blocks.append(ui.b_list([{"title": f"{x['name']} — {APPT_KIND[x['kind']].lower()}", "subtitle": x["place"], "tone": "accent",
                                  "badge": f"{x['at'].astimezone(zone()):%H:%M}", "open": {"query": "crm.journey", "input": {"lead_id": str(x["lead_id"])}}}
                                 for x in appts], "Lịch hẹn hôm nay"))
    names = await offering_names(ctx, (x[3]["offering_code"] for x in ranked[:5]))   # tin gửi khách nói TÊN, không nói mã nội bộ
    for _, _, kind, r, facts in ranked[:5]:
        label, tone, _rank = URGENCY[kind]
        blocks.append(ui.b_callout(tone, f"{label}: {r['customer']} · {r['offering_code'] or 'chưa chọn [[offering|l]]'}" + (f" ({phone_display(r['phone'])})" if r["phone"] else ""),
                                   facts=facts, draft=_draft(sender, r, kind, names.get(r["offering_code"] or "")),
                                   actions=[ui.act("leadflow.accept", "Nhận lead để xem số", {"lead_id": str(r["id"])}, "primary") if r.get("contact_hidden")
                                            else ui.act("crm.log_activity", "Ghi đã gọi", {"lead_id": str(r["id"]), "kind": "call"}, "primary")]))
    rest = ranked[5:]
    if rest:
        blocks.append(ui.b_list([{"title": f"{r['customer']} — {URGENCY[k][0]}", "subtitle": fa[0] if fa else "",
                                  "tone": URGENCY[k][1], "open": {"query": "crm.journey", "input": {"lead_id": str(r["id"])}}}
                                 for _, _, k, r, fa in rest[:15]], f"Còn {len(rest)} khách khác"))
    if due:
        blocks.append(ui.b_list([{"title": f"{d['customer']} — {d['title']}",
                                  "subtitle": f"Hạn {d['due_on']:%d/%m}" + (" — QUÁ HẠN" if d["state"] == "overdue" else ""),
                                  "badge": f"{d['amount_vnd'] - d['paid_vnd']:,} đ".replace(",", "."), "tone": "bad" if d["state"] == "overdue" else "accent",
                                  "open": d["open"]} for d in due[:10]],
                                "Khách sắp / đã quá hạn đóng tiền"))
    return ui.card("Sáng nay chăm khách nào trước", blocks, subtitle=f"{len(ranked)} khách · {t:%d/%m/%Y}", icon="sun.max", ask=ask)


# ---- Câu tín hiệu của bàn việc (0094): khai trên màn (desk=Desk(..., say=...)); agent/desk.py chạy bằng quyền người xem


def _attn_brief(o: Any) -> Line | None:
    return (line("bad", f"{num(kpi(o, 'Quá hạn / chưa gọi'))} khách quá hạn hoặc chưa gọi", "Em đã soạn sẵn tin nhắn cho từng người — anh/chị chỉ cần gọi", "Hôm nay tôi nên gọi lead nào trước?") if kpi(o, "Quá hạn / chưa gọi") else None)


def _attn_brief_2(o: Any) -> Line | None:
    return (line("accent", f"{num(kpi(o, 'Hẹn hôm nay'))} lịch hẹn hôm nay", "Xem giờ, địa điểm và khách từng hẹn",
            "Hôm nay tôi có những cuộc hẹn nào?") if kpi(o, "Hẹn hôm nay") else None)
