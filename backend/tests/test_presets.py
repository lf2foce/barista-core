"""Cấu hình mặc định đi kèm gói (quyết định 0059): lõi + gói là code, cấu hình công ty là dữ liệu. Bật gói ⇒ cài vai, quyền, đội, chuỗi duyệt,
mẫu quy trình của gói — CHỈ THÊM phần thiếu, không đè chỗ công ty đã sửa."""


from barista import presets
from barista.kernel import registry
from tests.conftest import owner_val


def test_every_preset_belongs_to_a_real_pack_and_resource() -> None:
    rl = presets.roles()
    rows = presets.grants()
    assert {role for role, *_ in rows} <= set(rl)
    assert len(rows) == len({(role, res) for role, res, *_ in rows}), "một (vai, tài nguyên) chỉ khai ở một gói"
    assert all(res in registry.RESOURCE_MODULE or res.startswith("core.") or res == "ledger" for _, res, _, _ in rows)
    for p in registry.PACKS.values():
        for role, res, _, _ in p.grants:      # gói khai quyền cho vai của gói KHÁC ⇒ chỉ lên tài nguyên của chính mình
            assert presets.resource_pack(res) == p.key and rl[role][1] != p.key, (p.key, role, res)
        for f in p.flows:
            assert not f.subject or f.subject in registry.SUBJECTS
        if p.kind != "industry":   # gói nền / năng lực không biết tên tài nguyên gói ngành — gói ngành tự cấp qua Pack.grants (0069)
            for role, r in p.roles.items():
                assert not [g for g in r.grants if registry.pack_kind(presets.resource_pack(g[0])) == "industry"], (p.key, role)


async def test_subject_kind_pack_matches_the_module_that_owns_it() -> None:
    rows = await owner_val("SELECT jsonb_object_agg(kind, pack) FROM core.subject_kinds")
    expect = {k: registry.MODULE_PACK[op.split(".")[0]] for k, (_, op) in registry.SUBJECTS.items()}
    assert rows == expect
