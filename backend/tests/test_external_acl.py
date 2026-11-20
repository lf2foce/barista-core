"""Tài liệu từ nguồn ngoài (Google Drive) — quyền = quyền ở nguồn VÀ kệ nội bộ. Giả lập bộ đồng bộ: thu hồi quyền, chuyển thư mục
(quyền kế thừa đổi), đổi thành viên nhóm. Mọi khẳng định chạy qua kb.search như người / trợ lý thật."""

from typing import Any

from barista.kernel import executor
from barista.kernel.principal import system_principal
from tests.conftest import Who, q


async def _sync(tenant: Any, op: str, **inp: Any) -> None:
    r = await executor.run(system_principal(tenant), op, inp)
    assert r.status == "ok", r.error


async def _finds(p, word: str) -> bool:
    return bool((await q(p, "kb.search", q=word))["rows"])


async def test_source_permission_and_shelf_both_required(who: Who) -> None:
    acc, gd, sale1, sale2 = await who("ketoan"), await who("giamdoc"), await who("sale1"), await who("sale2")
    t = acc.tenant_id
    await _sync(t, "admin.sync_groups", groups={"ketoan-team@sanmau.demo": [acc.email]})
    await _sync(t, "kb.sync_external", external_id="drive-A", title="Bảng đối soát nội bộ QQX1", body="Số liệu QQX1 nội bộ.",
                shelf="finance", acl=[{"type": "group", "principal": "ketoan-team@sanmau.demo"}])
    await _sync(t, "kb.sync_external", external_id="drive-B", title="Hợp đồng riêng ZZY2", body="Điều khoản ZZY2.",
                shelf="common", acl=[{"type": "user", "principal": sale1.email}])

    assert await _finds(acc, "QQX1")
    assert not await _finds(gd, "QQX1"), "giám đốc đọc được kệ kế toán nhưng KHÔNG được chia sẻ trên Drive ⇒ không thấy"
    assert await _finds(sale1, "ZZY2")
    assert not await _finds(sale2, "ZZY2"), "kệ chung không cấp rộng hơn quyền chia sẻ ở nguồn"
    assert await _finds(sale1.as_agent(), "ZZY2") and not await _finds(sale2.as_agent(), "ZZY2")  # trợ lý y như người

    # thu hồi chia sẻ ở nguồn ⇒ lần tìm kế tiếp đã không còn (trước khi nội dung tới model)
    await _sync(t, "kb.sync_external", external_id="drive-B", title="Hợp đồng riêng ZZY2", body="Điều khoản ZZY2.", shelf="common", acl=[])
    assert not await _finds(sale1, "ZZY2")

    # đổi thành viên nhóm ⇒ mất quyền theo
    await _sync(t, "admin.sync_groups", groups={"ketoan-team@sanmau.demo": []})
    assert not await _finds(acc, "QQX1")

    # chuyển thư mục: quyền kế thừa mới = cả tên miền — vẫn phải qua kệ: giám đốc / kế toán thấy, sale (không có kệ kế toán) không
    await _sync(t, "kb.sync_external", external_id="drive-A", title="Bảng đối soát nội bộ QQX1", body="Số liệu QQX1 nội bộ.",
                shelf="finance", acl=[{"type": "domain", "principal": "sanmau.demo"}])
    assert await _finds(gd, "QQX1") and await _finds(acc, "QQX1") and not await _finds(sale1, "QQX1")

    # xoá ở nguồn ⇒ xoá luôn
    await _sync(t, "kb.sync_external", external_id="drive-A", deleted=True)
    assert not await _finds(gd, "QQX1")


async def test_people_cannot_write_external_documents(who: Who) -> None:
    r = await executor.run(await who("giamdoc"), "kb.sync_external", {"external_id": "x", "title": "x", "body": "x"})
    assert r.status == "not_found"  # thao tác nội bộ — chỉ bộ đồng bộ (quyền hệ thống)
