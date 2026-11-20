"""0179: nạp lịch từ hệ khác giữ giờ gốc + người làm vào ``schedule.events`` — không giữ chỗ, không mời, không báo; nạp lại không nhân đôi (kể cả dòng đã huỷ);
chỉ lịch đã qua; người / khách không khớp không làm mất dòng; trùng giờ chỉ cảnh báo; chỉ quản lý Lịch cả công ty, trợ lý không chạy."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from barista import seed
from barista.kernel import executor
from tests.conftest import ok, owner_val

PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("nv1", "Người một", "staff", "KD-A", False), ("nv2", "Người hai", "staff", "KD-A", False)]
T0 = datetime(2026, 5, 4, 9, 15, tzinfo=UTC)


@pytest.fixture(scope="module")
async def co() -> dict[str, Any]:
    domain = f"i{uuid.uuid4().hex[:6]}.demo"
    tid = await seed.create_tenant(f"Công ty nạp lịch {domain}", domain, people=PEOPLE, packs=("schedule",), about="thử nạp lịch")
    return {"tid": tid, "domain": domain, "admin": await seed.who(tid, "admin", domain), "nv1": await seed.who(tid, "nv1", domain)}


def row(ref: str, **kw: Any) -> dict[str, Any]:
    return {"origin": "he_cu", "ext_ref": ref, "title": "Hẹn xem căn", "starts_at": kw.pop("starts_at", T0.isoformat()), **kw}


async def test_import_keeps_original_time_author_and_attendees_without_side_effects(co: dict[str, Any]) -> None:
    ref, d = f"hen-{uuid.uuid4().hex[:6]}", co["domain"]
    notices = await owner_val("SELECT count(*) FROM core.notifications WHERE tenant_id = %s", (co["tid"],))
    out = await ok(co["admin"], "schedule.import_past", **row(ref, author_email=f"nv1@{d}", attendee_emails=[f"nv1@{d}", f"nv2@{d}"], note="Khách xem căn 12A"))
    assert out["inserted"] is True and out["matched_author"] is True and out["matched_attendees"] == 2
    ev = await owner_val("SELECT row_to_json(e) FROM schedule.events e WHERE id = %s", (out["event_id"],))
    assert datetime.fromisoformat(ev["starts_at"]) == T0 and datetime.fromisoformat(ev["ends_at"]) == T0 + timedelta(minutes=30)   # giờ gốc, không phải lúc nạp
    assert ev["status"] == "done" and ev["source"] == "import.he_cu" and ev["source_ref"] == ref and ev["imported_at"] and ev["author_ref"] == ""
    assert str(ev["created_by"]) == str(co["nv1"].user_id) and str(ev["imported_by"]) == str(co["admin"].user_id)
    assert await owner_val("SELECT count(*) FROM schedule.bookings WHERE event_id = %s", (out["event_id"],)) == 0   # không giữ chỗ nguồn lực
    rsvp = await owner_val("SELECT array_agg(DISTINCT rsvp) FROM schedule.attendees WHERE event_id = %s", (out["event_id"],))
    assert rsvp == ["accepted"]                                                                      # đã diễn ra ⇒ coi là đã nhận
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE tenant_id = %s", (co["tid"],)) == notices   # không báo ai


async def test_reimport_is_a_noop_even_for_a_cancelled_row_and_cancel_gets_a_reason(co: dict[str, Any]) -> None:
    ref = f"hen-{uuid.uuid4().hex[:6]}"
    first = await ok(co["admin"], "schedule.import_past", **row(ref, status="cancelled"))
    ev = await owner_val("SELECT row_to_json(e) FROM schedule.events e WHERE id = %s", (first["event_id"],))
    assert ev["status"] == "cancelled" and ev["cancel_reason"] == "Đã huỷ ở hệ nguồn"
    again = await ok(co["admin"], "schedule.import_past", **row(ref, status="cancelled"))
    assert again["inserted"] is False and again["event_id"] == first["event_id"]
    assert await owner_val("SELECT count(*) FROM schedule.events WHERE source = 'import.he_cu' AND source_ref = %s", (ref,)) == 1


async def test_unmatched_people_are_kept_by_source_reference_not_dropped(co: dict[str, Any]) -> None:
    out = await ok(co["admin"], "schedule.import_past", **row(f"hen-{uuid.uuid4().hex[:6]}", author_email="nghi-viec@nowhere.test", author_ref="u-77",
                                                             attendee_emails=["ai-do@nowhere.test"], attendee_refs=["u-80"]))
    assert out["inserted"] is True and out["matched_author"] is False and out["matched_attendees"] == 0
    ev = await owner_val("SELECT row_to_json(e) FROM schedule.events e WHERE id = %s", (out["event_id"],))
    assert ev["author_ref"] == "u-77" and sorted(ev["attendee_refs"]) == ["ai-do@nowhere.test", "u-80"] and str(ev["created_by"]) == str(co["admin"].user_id)


async def test_only_past_valid_rows_are_imported(co: dict[str, Any]) -> None:
    bad = [({"starts_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()}, "tương lai"), ({"ends_at": (T0 - timedelta(hours=1)).isoformat()}, "kết thúc"),
           ({"origin": "He Cu"}, "Hệ nguồn"), ({"ext_ref": " "}, "Mã dòng"), ({"cancel_reason": "x"}, "Lý do huỷ"), ({"starts_at": "2026-05-04T09:15:00"}, "")]
    for extra, _ in bad:
        r = await executor.run(co["admin"], "schedule.import_past", row(f"hen-{uuid.uuid4().hex[:6]}", **extra) | {k: v for k, v in extra.items() if k in ("origin", "ext_ref")})
        assert r.status == "invalid", (extra, r.status, r.error)


async def test_overlap_only_warns_and_roles_are_enforced(co: dict[str, Any]) -> None:
    d = co["domain"]
    a = await ok(co["admin"], "schedule.import_past", **row(f"hen-{uuid.uuid4().hex[:6]}", attendee_emails=[f"nv2@{d}"], starts_at=(T0 + timedelta(days=1)).isoformat()))
    b = await ok(co["admin"], "schedule.import_past", **row(f"hen-{uuid.uuid4().hex[:6]}", attendee_emails=[f"nv2@{d}"], starts_at=(T0 + timedelta(days=1, minutes=10)).isoformat()))
    assert a["warnings"] == [] and b["inserted"] is True and len(b["warnings"]) == 1       # trùng giờ: cảnh báo, vẫn nạp
    assert (await executor.run(co["nv1"], "schedule.import_past", row("hen-x"))).status == "denied"            # nhân viên không nạp
    assert (await executor.run(co["admin"].as_agent(), "schedule.import_past", row("hen-y"))).status == "denied"   # trợ lý không chạy
