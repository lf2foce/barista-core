"""NẠP LỊCH SỬ từ hệ khác (0185): dữ liệu hệ cũ là tham chiếu — cả chuỗi lệnh chạy ``historical=True`` (gọi lồng, sự kiện, bên nghe) rồi MỌI vòng quét
không sinh thông báo, việc theo dõi, sự kiện hạn lead, không đổi người giữ. Cùng chuỗi chạy thường thì có — phép thử có nghĩa."""

import itertools
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from barista import seed
from barista.db import db
from barista.kernel import events, executor, registry
from barista.kernel.clock import VN, today
from barista.kernel.principal import Principal, system_principal
from tests.conftest import fresh_phone, ok, owner_val, q

_seq = itertools.count(1)
PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("truong", "Trưởng nhóm A", "team_lead", "KD-A", True),
          ("a", "Sale A", "sale", "KD-A", False), ("b", "Sale B", "sale", "KD-A", False), ("c", "Sale C", "sale", "KD-A", False)]


async def _company() -> tuple[UUID, str, dict[str, Principal]]:
    domain = f"hl{next(_seq)}.demo"
    tid = await seed.create_tenant(f"Công ty nạp lịch sử {domain}", domain, people=PEOPLE)
    return tid, domain, {k[0]: await seed.who(tid, k[0], domain) for k in PEOPLE}


async def _rule(admin: Principal, **kw: Any) -> None:
    await ok(admin, "leadflow.set_rule", **{"name": "Chia lead", "effective_from": (today() - timedelta(days=1)).isoformat(), "cap_uncalled": 0,
                                           "roles": ["sale"], "first_call_min": 15, "max_recalls": 2, "days": "all", "open_at": "00:00",
                                           "close_at": "24:00", "remind_before_min": 0, "escalate": True, "team_code": "KD-A", **kw})


def _at(days_ago: int, h: int = 9) -> str:
    return (datetime.now(VN).replace(hour=h, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)).isoformat()


async def _hist(p: Principal, name: str, /, **inp: Any) -> Any:
    r = await executor.run(p, name, inp, historical=True)
    assert r.status == "ok", (name, r.status, r.error)
    return r.output


async def test_historical_mode_is_inherited_never_switched_off_and_not_an_input() -> None:
    tid, _, w = await _company()
    await _rule(w["admin"])
    # lệnh lồng + bên nghe thừa hưởng: cửa nạp lead chạy nạp lịch sử ⇒ người nhận không được báo, dòng trạng thái chia là lead đỗ
    out = await _hist(w["admin"], "leadflow.intake", name="Khách cũ", phone=fresh_phone())
    await events.drain()
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE tenant_id = %s", (tid,)) == 0
    assert await owner_val("SELECT imported_at IS NOT NULL FROM leadflow.lead_state WHERE lead_id = %s", (out["lead_id"],))
    # không phải ô đầu vào: người gọi không tự bật qua dữ liệu lệnh
    r = await executor.run(w["admin"], "leadflow.intake", {"name": "Khách", "phone": fresh_phone(), "historical": True})
    assert r.status == "invalid"
    # chạy thường ngay sau đó: chế độ không rò sang lệnh kế
    live = await ok(w["admin"], "leadflow.intake", name="Khách mới", phone=fresh_phone())
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE tenant_id = %s", (tid,)) == 1
    assert await owner_val("SELECT imported_at IS NULL FROM leadflow.lead_state WHERE lead_id = %s", (live["lead_id"],))
    # việc theo dõi không mở trong lần nạp
    r = await executor.run(w["admin"], "work.start", {"kind": "collection", "subject": "TT000000"}, historical=True)
    assert r.status == "invalid" and "nạp lịch sử" in r.error


async def test_parked_leads_stay_out_of_the_sweep_until_barista_routes_them() -> None:
    tid, d, w = await _company()
    await _rule(w["admin"])
    leads = []
    for i, status in enumerate(("waiting", "contacted", "queued")):
        got = await ok(w["admin"], "crm.create_lead", name=f"Khách cũ {i}", phone=fresh_phone(), owner_email=f"a@{d}", source="import")
        await ok(w["admin"], "leadflow.import_state", lead_id=str(got["lead_id"]), origin="he_cu", status=status, assigned_at=_at(200),
                 first_call_at=_at(200, 10) if status == "contacted" else None)
        leads.append(UUID(str(got["lead_id"])))
    waiting, _, queued = leads
    for _ in range(2):
        swept = await ok(system_principal(tid), "leadflow.enforce_sla")
        assert not any(swept.values()), swept
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE tenant_id = %s", (tid,)) == 0
    assert await owner_val("SELECT count(*) FROM leadflow.sla_events WHERE tenant_id = %s", (tid,)) == 0
    assert (await ok(w["admin"], "leadflow.drain"))["assigned"] == 0   # hàng chờ đỗ không tự chia
    # màn tổng quan: lead đỗ không là "kẹt" / "quá hạn", đếm riêng
    page = await q(w["admin"], "leadflow.overview")
    kpis = {k["label"]: k["value"] for k in next(b["items"] for b in page["blocks"] if b["type"] == "kpis")}
    assert kpis["Kẹt — không ai nhận"] == 0 and kpis["Đang chờ gọi lần đầu"] == 0 and kpis["Nạp từ hệ cũ — chưa kích hoạt"] == 3
    assert (await q(w["truong"], "leadflow.late"))["rows"] == []
    # người thật khoá tài khoản của người giữ khi mọi lead còn đỗ: sự kiện THẬT ⇒ lead chuyển qua luật chia, thành lead sống
    # (trước đó: quản lý giao lead hàng chờ, chuyển tay lead chờ gọi ⇒ sống, hạn tính từ bây giờ)
    await ok(w["admin"], "leadflow.assign", lead_id=str(queued), owner_email=f"b@{d}")
    await ok(w["admin"], "leadflow.reassign", lead_id=str(waiting), owner_email=f"c@{d}", reason="kích hoạt lead cũ")
    for lid in (queued, waiting):
        st = await owner_val("SELECT (imported_at IS NULL AND status = 'waiting' AND first_call_due > now()) FROM leadflow.lead_state WHERE lead_id = %s", (lid,))
        assert st is True
    # nạp lại trạng thái hệ cũ lên lead đã sống ⇒ từ chối
    clash = await executor.run(w["admin"], "leadflow.import_state", {"lead_id": str(waiting), "origin": "he_cu", "status": "waiting", "assigned_at": _at(1)})
    assert clash.status == "conflict"


async def test_sweep_leaves_loaded_leads_of_departed_owners_but_a_real_deactivation_moves_them() -> None:
    tid, d, w = await _company()
    await _rule(w["admin"])
    # lead hệ cũ CHƯA có trạng thái chia, và một lead có trạng thái chia đỗ — đều của Sale A; một lead sống để vòng quét có việc
    bare = await _hist(w["admin"], "crm.create_lead", name="Khách cũ 1", phone=fresh_phone(), owner_email=f"a@{d}", source="import")
    parked = await _hist(w["admin"], "crm.create_lead", name="Khách cũ 2", phone=fresh_phone(), owner_email=f"a@{d}", source="import")
    await _hist(w["admin"], "leadflow.import_state", lead_id=str(parked["lead_id"]), origin="he_cu", status="contacted", assigned_at=_at(90),
                first_call_at=_at(90, 10))
    await ok(w["admin"], "leadflow.intake", name="Khách mới", phone=fresh_phone(), owner_email=f"b@{d}")
    # người đã nghỉ ở hệ cũ bị khoá TRONG lần nạp: bên nghe không chia lại lead của họ; vòng quét về sau cũng không
    await _hist(w["admin"], "admin.update_member", email=f"a@{d}", active=False)
    await events.drain()
    swept = await ok(system_principal(tid), "leadflow.enforce_sla")
    assert swept["handed_over"] == 0, swept
    for lid in (bare["lead_id"], parked["lead_id"]):
        assert await owner_val("SELECT owner_id FROM crm.leads WHERE id = %s", (lid,)) == w["a"].user_id
    assert await owner_val("SELECT count(*) FROM leadflow.sla_events WHERE tenant_id = %s AND kind = 'handed_over'", (tid,)) == 0
    # mở lại rồi khoá THẬT (không nạp lịch sử): việc của hôm nay ⇒ cả lead đỗ lẫn lead chưa có trạng thái chia chuyển qua luật chia
    await ok(w["admin"], "admin.update_member", email=f"a@{d}", active=True)
    await ok(w["admin"], "admin.update_member", email=f"a@{d}", active=False)
    await events.drain()
    for lid in (bare["lead_id"], parked["lead_id"]):
        assert await owner_val("SELECT owner_id FROM crm.leads WHERE id = %s", (lid,)) not in (None, w["a"].user_id)
        assert await owner_val("SELECT imported_at IS NULL FROM leadflow.lead_state WHERE lead_id = %s", (lid,))


async def test_escalations_are_one_digest_per_recipient_kind_and_hour() -> None:
    tid, _, w = await _company()
    await _rule(w["admin"], grace_min=20)
    ids = [UUID(str((await ok(w["admin"], "leadflow.intake", name=f"Khách {i}", phone=fresh_phone()))["lead_id"])) for i in range(7)]
    lead_notes = "SELECT count(*) FROM core.notifications WHERE tenant_id = %s AND user_id = %s AND kind = 'lead_escalated'"
    async with db.owner.connection() as conn:
        await conn.execute("UPDATE leadflow.lead_state SET first_call_due = now() - interval '1 minute' WHERE lead_id = ANY(%s)", (ids[:5],))
    got = await ok(system_principal(tid), "leadflow.enforce_sla")
    assert got["breached"] == 5 and got["escalated"] == 5
    assert await owner_val(lead_notes, (tid, w["truong"].user_id)) == 1                     # MỘT tin cho 5 lead
    note = await owner_val("SELECT title FROM core.notifications WHERE tenant_id = %s AND user_id = %s AND kind = 'lead_escalated'", (tid, w["truong"].user_id))
    assert note == "Quá hạn gọi lần đầu: 5 lead"
    assert await owner_val("SELECT count(*) FROM leadflow.sla_events WHERE tenant_id = %s AND kind = 'escalated'", (tid,)) == 5   # sổ vẫn ghi từng lead
    # người GIỮ vẫn được nhắc từng lead (tin gửi cho người phải làm)
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE tenant_id = %s AND kind = 'lead' AND title LIKE 'Quá hạn gọi%%'", (tid,)) == 5
    # lượt quét sau trong cùng giờ có thêm lead quá hạn ⇒ không thêm tin cùng loại cho cùng người
    async with db.owner.connection() as conn:
        await conn.execute("UPDATE leadflow.lead_state SET first_call_due = now() - interval '1 minute' WHERE lead_id = ANY(%s)", (ids[5:],))
    got = await ok(system_principal(tid), "leadflow.enforce_sla")
    assert got["escalated"] == 2
    assert await owner_val(lead_notes, (tid, w["truong"].user_id)) == 1


async def test_credentials_loaded_from_an_old_system_stay_silent_until_verified() -> None:
    """Chứng chỉ nhân sự (0202): nạp từ hệ cũ ⇒ tham chiếu — kể cả chứng chỉ "sắp hết hạn" theo sổ cũ, vòng nhắc im (câu chọn công ty không chọn) tới khi
    người phụ trách xác nhận; xác nhận rồi chỉ nhắc mốc SAU ngày xác nhận — chứng chỉ đã hết hạn lúc nạp không bao giờ báo "đã hết hạn"."""
    tid, d, w = await _company()
    admin = w["admin"]
    await ok(admin, "hr.save_credential_kind", key="the_an_toan", name="Thẻ an toàn lao động", remind_days=30)

    def exp(days: int) -> str:
        return (today() + timedelta(days=days)).isoformat()

    await _hist(admin, "hr.record_credential", person=f"a@{d}", kind_key="the_an_toan", number="AT-1", expires_on=exp(-900))   # hết hạn từ lâu
    await _hist(admin, "hr.record_credential", person=f"b@{d}", kind_key="the_an_toan", number="AT-2", expires_on=exp(10))     # sắp hết hạn theo sổ cũ
    await events.drain()
    notes = "SELECT count(*) FROM core.notifications WHERE tenant_id = %s AND kind = 'hr_credential'"
    due = registry.SWEEPS["hr.remind_credentials"]
    async with db.owner.connection() as conn:
        assert tid not in {r["tenant_id"] for r in await (await conn.execute(due)).fetchall()}
    for _ in range(2):
        assert await ok(system_principal(tid), "hr.remind_credentials") == {"claimed": 0, "sent": 0}
    assert await owner_val(notes, (tid,)) == 0
    assert await owner_val("SELECT count(*) FROM hr.credential_notices WHERE tenant_id = %s", (tid,)) == 0
    # người phụ trách đối chiếu xong ⇒ xác nhận: chứng chỉ còn hạn vào vòng nhắc từ hôm nay; chứng chỉ đã hết hạn lúc nạp vẫn im
    assert (await ok(admin, "hr.verify_credentials"))["verified"] == 2
    assert (await ok(system_principal(tid), "hr.remind_credentials"))["claimed"] == 1
    assert await owner_val(notes + " AND user_id = %s", (tid, w["b"].user_id)) == 1
    assert await owner_val(notes + " AND user_id = %s", (tid, w["a"].user_id)) == 0
    # đối chứng chạy thường: ghi thường một chứng chỉ sắp hết hạn ⇒ được nhắc ngay ở lượt kế
    await ok(admin, "hr.record_credential", person=f"c@{d}", kind_key="the_an_toan", number="AT-3", expires_on=exp(5))
    assert (await ok(system_principal(tid), "hr.remind_credentials"))["claimed"] == 1
    assert await owner_val(notes + " AND user_id = %s", (tid, w["c"].user_id)) == 1
