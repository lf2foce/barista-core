"""Bất biến khi nhiều thao tác chạy cùng lúc: khoá chống lặp theo người gọi + giữ trước khi đọc; luôn còn một quản trị."""

import asyncio
import dataclasses
import uuid
from typing import Any

import pytest

from barista import seed
from barista.db import db
from barista.kernel import executor, registry
from barista.kernel.executor import Ctx, core_ctx
from barista.modules import admin as admin_mod
from tests.conftest import OTHER_DOMAIN, Who, ok, owner_val


def _letter(tag: str) -> dict[str, str]:
    return {"recipient": "Công ty Mẫu", "subject": f"thử chống lặp {tag}", "body": "Nội dung thử."}


async def test_idempotency_key_belongs_to_caller(who: Who) -> None:
    """Hai người dùng TRÙNG khoá + trùng dữ liệu: mỗi người một kết quả, không ai nhận kết quả của người kia."""
    sale1, sale2 = await who("sale1"), await who("sale2")
    tag = uuid.uuid4().hex[:8]
    r1 = await executor.run(sale1, "papers.draft_letter", _letter(tag), key="k-trung")
    r2 = await executor.run(sale2, "papers.draft_letter", _letter(tag), key="k-trung")
    assert r1.ok and r2.ok and not r2.replayed
    assert r1.output["paper_id"] != r2.output["paper_id"]
    again = await executor.run(sale1, "papers.draft_letter", _letter(tag), key="k-trung")
    assert again.replayed and again.output["paper_id"] == r1.output["paper_id"]


async def test_same_key_at_the_same_time_runs_once(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hai request cùng khoá tới cùng lúc (bấm hai lần, mạng gửi lại): chạy MỘT lần, request kia nhận đúng kết quả đó.
    Handler chậm lại để hai giao dịch chắc chắn chồng nhau (không thì request đầu xong trước khi request sau đọc khoá)."""
    sale1 = await who("sale1")
    op = registry.OPS["papers.draft_letter"]

    async def slow(ctx: Ctx, inp: Any) -> Any:
        await asyncio.sleep(0.3)
        return await op.handler(ctx, inp)

    monkeypatch.setitem(registry.OPS, op.name, dataclasses.replace(op, handler=slow))
    tag = uuid.uuid4().hex[:8]
    a, b = await asyncio.gather(*(executor.run(sale1, "papers.draft_letter", _letter(tag), key=f"k-dong-thoi-{tag}")
                                  for _ in range(2)))
    assert a.ok and b.ok and sorted([a.replayed, b.replayed]) == [False, True]
    assert a.output["paper_id"] == b.output["paper_id"]
    assert await owner_val("SELECT count(*) FROM papers.documents WHERE summary LIKE %s", (f"%{tag}%",)) == 1


async def test_last_admin_cannot_be_removed_concurrently(who: Who, demo: dict) -> None:
    """Hai quản trị cùng tự hạ quyền một lúc: không được để công ty không còn quản trị. Tất định: giao dịch của B giữ khoá
    (chưa commit) trong lúc A chạy — không khoá thì A đếm thấy B còn quyền ⇒ cả hai cùng qua."""
    a = await who("admin", other=True)
    email_b = f"admin2-{uuid.uuid4().hex[:6]}@{OTHER_DOMAIN}"
    await ok(a, "admin.invite_user", email=email_b, name="Quản trị hai", role="admin", team_code="QT")
    b = await seed.who(demo["other"], email_b.split("@")[0], OTHER_DOMAIN)

    async with db.tenant_tx(b) as conn:  # B tự hạ quyền, đã qua kiểm tra, CHƯA commit
        await conn.execute("UPDATE core.memberships SET role = 'sale' WHERE user_id = %s", (b.user_id,))
        await admin_mod._ensure_admin_remains(core_ctx(conn, b))
        demote_a = asyncio.create_task(executor.run(a, "admin.update_member", {"email": a.email, "role": "sale"}))
        await asyncio.sleep(0.3)
        assert not demote_a.done()  # A đang chờ khoá của B
    r = await demote_a  # B commit ⇒ A đếm lại, thấy B đã hạ ⇒ A bị chặn
    assert r.status == "invalid" and "quản trị" in r.error
    left = await owner_val(
        """SELECT count(*) FROM core.memberships m JOIN core.grants g ON g.tenant_id = m.tenant_id AND g.role = m.role
           WHERE m.tenant_id = %s AND m.active AND g.resource = 'core.admin' AND 'manage' = ANY(g.actions)""", (demo["other"],))
    assert left >= 1
