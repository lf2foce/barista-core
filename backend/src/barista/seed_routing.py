"""DỮ LIỆU MẪU CHIA LEAD (0152) — chồng lên công ty mẫu (``seed.py``): thành viên theo thứ công ty bán (``offerings`` — hai mã) có hạng + trọng số, luật chia bản mới (lịch nghỉ lễ,
nhắc trước hạn, báo trưởng nhóm, ân hạn, đội trực ngoài giờ), và vài lead đi qua ĐÚNG lệnh để màn "Chia lead", "Phản hồi theo người", "Vì sao lead
tới tay ai" có chuyện thật: một lead đúng hạn, một lead quá hạn trong ân hạn (trưởng nhóm đã được báo), một lead bị thu hồi, một khách quay lại.
Dữ liệu GIẢ (tên ghép, SĐT đầu 010); chỉ dời HẠN bằng owner để giả "thời gian đã trôi" — không sửa ai giữ lead."""

from typing import Any
from uuid import UUID

from barista.db import db
from barista.kernel.clock import today
from barista.kernel.principal import system_principal
from barista.seed import DOMAIN, run, who

# (thứ bán thứ mấy, người, hạng, trọng số): sale1 bán thứ đầu giỏi ⇒ trọng số 2; sale3 hạng 2 của thứ đầu (nhận khi hạng 1 kín); sale4 chuyên thứ hai
MEMBERS = [(0, "sale1", 1, 2), (0, "sale2", 1, 1), (0, "sale3", 2, 1), (1, "sale4", 1, 2), (1, "sale2", 1, 1), (1, "sale3", 1, 1)]
LEADS = [("Thân Minh Quý", "0100095001"), ("Ông Thị Ngà", "0100095002"), ("Lã Văn Toàn", "0100095003"), ("Úc Hồng Nhung", "0100095004")]
MARK = "Chia lead theo thành viên (giả định)"


async def _owner(sql: str, params: Any) -> Any:
    async with db.owner.connection() as conn:
        cur = await conn.execute(sql, params)  # type: ignore[arg-type]
        return await cur.fetchall() if cur.description else None


async def seed_routing(tid: UUID, domain: str = DOMAIN, offerings: tuple[str, str] | None = None) -> dict[str, Any]:
    """``offerings``: hai mã thứ công ty bán (mặc định hai mã chữ tự do của công ty mẫu lõi; công ty có danh mục của gói ngành thì nêu mã của nó)."""
    from barista.seed import OFFERINGS

    first, second = offerings or (OFFERINGS[0][0], OFFERINGS[1][0])
    if await _owner("SELECT 1 FROM leadflow.rules WHERE tenant_id = %s AND name = %s", (tid, MARK)):
        return {"skipped": True}
    admin = await who(tid, "admin", domain)
    for nth, local, tier, weight in MEMBERS:
        await run(admin, "leadflow.set_member", offering_code=(first, second)[nth], user_email=f"{local}@{domain}", tier=tier, weight=weight,
                  note="giả định — quản lý chỉnh ở Kinh doanh › Thành viên")
    await run(admin, "leadflow.set_rule", name=MARK, offering_code=first, effective_from=today().isoformat(), receivers="members", roles=["sale"],
              strategy="rotate", cap_uncalled=10, first_call_min=120, remind_before_min=15, escalate=True, grace_min=30, max_recalls=2,
              days="mon_sat", open_at="08:30", close_at="18:00", calendar="vn-public", off_hours="duty", duty_team_code="KD-A",
              duty_days="all", duty_open_at="18:00", duty_close_at="22:00")
    digital = await who(tid, "digital", domain)
    got = [await run(admin, "leadflow.intake", name=nm, phone=ph, offering_code=first, source="referral", qualified_by_email=f"{digital.email}", wishes="cần tư vấn trong tuần")
           for i, (nm, ph) in enumerate(LEADS)]
    ids = [g["lead_id"] for g in got]
    holders = {str(g["lead_id"]): g["assigned_to"] for g in got}
    # lead 0: người giữ gọi ngay (ghi tay kết quả nói chuyện được; bản cài có tổng đài thì luật đòi cuộc gọi tổng đài — vẫn hiện "đang chờ")
    if (h := holders[str(ids[0])]) is not None:
        sale = await _person(tid, h)
        if sale is not None:
            await run(sale, "crm.log_activity", lead_id=ids[0], kind="call", outcome="connected", note="Gọi ngay sau khi nhận — khách hẹn gặp")
    # lead 1: quá hạn, đang trong ân hạn (trưởng nhóm đã được báo) · lead 2: quá hạn quá ân hạn ⇒ thu hồi sang người mới
    await _owner("UPDATE leadflow.lead_state SET first_call_due = now() - interval '5 minutes' WHERE lead_id = %s", (ids[1],))
    await _owner("UPDATE leadflow.lead_state SET first_call_due = now() - interval '2 hours' WHERE lead_id = %s", (ids[2],))
    sweep = system_principal(tid)
    await run(sweep, "leadflow.enforce_sla")
    await _owner("UPDATE leadflow.lead_state SET recall_at = now() - interval '1 minute' WHERE lead_id = %s", (ids[2],))
    await run(sweep, "leadflow.enforce_sla")
    # lead 3: khách để lại thông tin lần nữa qua form quảng cáo ⇒ sự kiện "khách quay lại", người đang chăm được báo
    await run(admin, "leadflow.intake", name=LEADS[3][0], phone=LEADS[3][1], offering_code=first, source="ads", provider="meta",
              external_ref="seed-route-back")
    return {"members": len(MEMBERS), "leads": len(ids)}


async def _person(tid: UUID, user_id: Any) -> Any:
    rows = await _owner("SELECT u.email FROM core.users u WHERE u.id = %s", (user_id,))
    if not rows:
        return None
    local, _, domain = rows[0]["email"].partition("@")
    return await who(tid, local, domain)
