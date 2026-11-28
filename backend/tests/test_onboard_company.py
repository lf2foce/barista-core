"""TẠO CÔNG TY THẬT (ADR 0263 mục D, 0194 Còn mở 3): ``operator create-company`` — gói + cài mặc định + mời quản trị đầu tiên (chủ tài khoản), KHÔNG nạp
dữ liệu mẫu; chạy lại cùng tên không tạo công ty thứ hai."""

import argparse
import itertools

import pytest

from barista import operator
from tests.conftest import owner_val

_seq = itertools.count(1)


def _args(name: str, email: str, **kw: object) -> argparse.Namespace:
    base = {"cmd": "create-company", "tenant": "", "name": name, "admin_email": email, "admin_name": "Quản trị Giả", "admin_role": "admin",
            "packs": "crm", "about": "", "ai": False, "by": "người vận hành thử", "why": "test", "dry_run": False}
    return argparse.Namespace(**(base | kw))


async def test_create_company_installs_defaults_invites_the_first_admin_and_seeds_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    n = next(_seq)
    name, email = f"Công ty thật giả {n}", f"quantri{n}@congty-thu.invalid"
    await operator.create_company(_args(name, email, dry_run=True))
    assert await owner_val("SELECT count(*) FROM core.tenants WHERE name = %s", (name,)) == 0
    await operator.create_company(_args(name, email))
    tid = await owner_val("SELECT id FROM core.tenants WHERE name = %s", (name,))
    packs = await owner_val("SELECT array_agg(pack ORDER BY pack) FROM core.tenant_packs WHERE tenant_id = %s", (tid,))
    assert {"crm", "directory"} <= set(packs) and "schedule" not in packs   # gói bắt buộc tự kèm; gói luôn bật không có dòng
    assert await owner_val("SELECT count(*) FROM core.memberships WHERE tenant_id = %s", (tid,)) == 1
    assert await owner_val("""SELECT mb.owner AND mb.active FROM core.memberships mb JOIN core.users u ON u.id = mb.user_id
                              WHERE mb.tenant_id = %s AND u.email = %s""", (tid, email))
    assert await owner_val("SELECT count(*) FROM core.approval_chains WHERE tenant_id = %s AND key = 'onboard_go_live'", (tid,)) == 1
    assert await owner_val("SELECT count(*) FROM crm.leads WHERE tenant_id = %s", (tid,)) == 0   # không dữ liệu mẫu
    assert not await owner_val("SELECT ai_enabled FROM core.tenant_profile WHERE tenant_id = %s", (tid,))
    with pytest.raises(operator.Refused):
        await operator.create_company(_args(name, f"khac{n}@congty-thu.invalid"))
    with pytest.raises(operator.Refused):
        await operator.create_company(_args(f"Công ty lạ {n}", email, packs="khong_co_goi_nay"))
    assert "chủ tài khoản" in capsys.readouterr().out
