"""Quyền dùng gói (156): gói MẶC ĐỊNH của bản cài (BARISTA_PACKS) mọi công ty bật được; gói khác bày ở chợ gói là "Liên hệ để mở", quản trị công ty
không tự bật — nhà cung cấp cấp quyền (python -m barista.entitle) rồi mới bật; đã dùng thì tắt / bật lại không phải cấp lại."""

import uuid
from typing import Any

from barista import seed
from barista.config import settings
from barista.kernel import executor
from tests.conftest import ok, owner_val, q


async def test_paid_packs_need_the_vendor_to_open_them() -> None:
    d = f"{uuid.uuid4().hex[:6]}.goi.test"
    tid = await seed.create_tenant("Gói trả tiền (test)", d, packs=("finance",), people=[("admin", "Quản trị", "admin", "QT", True)])
    admin = await seed.who(tid, "admin", d)
    old = settings.packs
    settings.packs = "finance,talk"                                                         # gói mặc định của bản cài này
    try:
        store = json_of(await q(admin, "admin.pack_store"))
        assert "Liên hệ để mở" in store
        info = await q(admin, "admin.pack_info", pack="training")
        assert not any(a["command"] == "admin.set_pack" for a in info["actions"])           # chưa có quyền ⇒ không có nút Bật
        r = await executor.run(admin, "admin.set_pack", {"pack": "training", "enabled": True})
        assert r.status == "invalid" and "liên hệ nhà cung cấp" in r.error
        await ok(admin, "admin.set_pack", pack="talk", enabled=True)                        # gói mặc định: tự bật được

        await owner_val("""INSERT INTO core.tenant_entitlements (tenant_id, pack, granted_by, note) VALUES (%s, 'training', 'nhà cung cấp', 'HĐ test')
                           RETURNING 1""", (tid,))                                          # nhà cung cấp mở gói (như python -m barista.entitle)
        await ok(admin, "admin.set_pack", pack="training", enabled=True)
        await ok(admin, "admin.set_pack", pack="training", enabled=False)
        await ok(admin, "admin.set_pack", pack="training", enabled=True)                     # đã có quyền ⇒ tắt / bật lại không phải cấp lại
    finally:
        settings.packs = old


def json_of(x: Any) -> str:
    import json
    return json.dumps(x, ensure_ascii=False)
