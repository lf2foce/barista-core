"""THƯỚC ĐO LÕI SẠCH (quyết định 0069): thêm một gói = MỘT file module khai ``Pack`` + ``Module`` (+ migration thật khi có bảng) — KHÔNG sửa
kernel/registry.py, workspaces.py, presets.py, agent/chat.py, agent/desk.py, http_api.py. Gói giả ở đây khai mọi thứ một gói thật khai, rồi kiểm nó
tự hiện ở danh mục gói, kế hoạch cài mặc định, menu, màn ghim, bàn việc, chợ gói — và gỡ ra sạch sau test."""

from collections.abc import Iterator
from typing import Any

import pytest

from barista import presets, workspaces
from barista.kernel import attention, executor, packs, registry, screens
from barista.kernel.attention import Line
from barista.kernel.executor import Ctx
from barista.kernel.fields import Input, NoInput
from barista.kernel.registry import Flow, Module, Pack, Role
from barista.kernel.screens import Desk, Screen, Workspace
from tests.conftest import Who, ok, owner_val, q

LIVE: tuple[Any, ...] = (registry.PACKS, registry.MODULES, registry.MODULE_PACK, registry.RESOURCES, registry.RESOURCE_MODULE, registry.OPS,
                         registry.SETUP_RESOURCES, registry.PACK_MODULE, registry.ROUTES, screens.WORKSPACES, screens.CONTRIB)


@pytest.fixture
def demo_pack() -> Iterator[None]:
    snap = [type(s)(s) for s in LIVE]
    plugs = {k: list(v) for k, v in registry.PLUGS.items()}   # danh sách lồng — chép sâu để gỡ đúng phần gói giả cắm thêm
    Pack("demo", "Gói thử", "Gói giả — thước đo lõi sạch", kind="industry", requires=("finance",), rules="- GÓI THỬ: quy tắc riêng.\n",
         roles={"demo_clerk": Role("Nhân viên thử", home=("demo", ("demo_list",)), picker=("Nhân viên", "Thử.", "Có gì mới?"),
                                   grants=(("demo", "own", "read write"), ("kb.common", "all", "read")))},
         grants=(("admin", "demo", "all", "read write manage"),), pins={"admin": (None, ("demo_list",))},
         teams=(("TH", "Thử", "demo"),), flows=(Flow("thu", "Quy trình thử", "", False, "# Bước 1 | Thử\n- Mục [xác nhận: @TH]"),))
    m = Module("demo", "Thử", pack="demo", resources={"demo": "Dữ liệu thử"}, setup=("demo",))

    @m.query("list", resource="demo", title="Danh sách thử")
    async def _list(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
        return {"rows": [], "kpis": [{"label": "Cần xem", "value": 1}]}

    def _say(o: Any) -> Line | None:
        return attention.line("warn", "1 mục thử") if attention.kpi(o, "Cần xem") else None

    m.workspace(Workspace("demo", "Thử", "star", (Screen("demo_list", "Danh sách", "list.bullet", "demo.list", desk=Desk("do", say=_say, menu=True)),),
                          order=15))
    m.screens("admin", (Screen("demo_entry", "Nhập thử", "square.and.pencil", "demo.list", group="Nhập liệu"),), rank=99)

    # Soạn thảo (0118): gói cắm ô số liệu cho văn bản — papers không biết gói nào, chỉ hỏi điểm cắm của mình
    @m.plug("papers.field_catalog", "Ô thử cho Soạn thảo")
    async def _catalog(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
        return {"subject_kind": "demo.thing", "label": "Đồ thử", "fields": [{"key": "thing_name", "label": "Tên đồ"},
                                                                            {"key": "thing_price", "label": "Giá đồ", "kind": "amount"}]}

    class ThingIn(Input):
        subject_kind: str
        ref: str

    @m.plug("papers.field_values", "Số liệu đồ thử", resource="demo")
    async def _values(ctx: Ctx, inp: ThingIn) -> dict[str, Any] | None:
        if inp.subject_kind != "demo.thing":
            return None
        return {"subject_id": "00000000-0000-0000-0000-00000000d0d0", "ref": inp.ref.upper(), "title": f"Đồ {inp.ref.upper()}",
                "values": {"thing_name": f"Đồ {inp.ref.upper()}", "thing_price": 1_250_000}}

    class SeenIn(Input):
        subject_kind: str
        ids: list[str]

    @m.plug("papers.subjects_seen", "Đồ thử người xem thấy", resource="demo")
    async def _seen(ctx: Ctx, inp: SeenIn) -> list[str] | None:
        return inp.ids if inp.subject_kind == "demo.thing" else None

    # đường HTTP riêng của gói (kênh tức thì, tệp…): khai ngay trong module — http_api.py gắn theo sổ đăng ký, không sửa lõi HTTP
    @m.route("GET", "/v1/demo/things/{ref}/file")
    async def _thing_file(ref: str) -> dict[str, str]:
        return {"ref": ref}

    registry.validate()
    yield
    for live, old in zip(LIVE, snap, strict=True):
        live.clear()
        live.extend(old) if isinstance(live, list) else live.update(old)
    registry.PLUGS.clear()
    registry.PLUGS.update(plugs)


def test_a_one_file_pack_shows_up_everywhere_without_touching_hub_files(demo_pack: None) -> None:
    assert registry.pack_closure({"demo"}) == {"demo", "finance", "directory"} and registry.pack_title("demo") == "Gói thử"
    items = presets.plan({"demo"}, {"base", "finance", "directory", "demo"}, {"admin", "accountant"})
    by = {k: [i.key for i in items if i.kind == k] for k in presets.KIND_ORDER}
    assert by["role"] == ["demo_clerk"] and by["team"] == ["TH"] and by["flow"] == ["thu"]
    assert {"demo_clerk|demo", "demo_clerk|kb.common", "admin|demo"} <= set(by["grant"])
    keys = [w.key for w in workspaces.workspaces()]
    assert keys[:3] == ["sales", "demo", "marketing"]                                   # order=15 chen giữa, không sửa workspaces.py
    admin_ws = next(w for w in workspaces.workspaces() if w.key == "admin")
    assert admin_ws.screens[-1].key == "demo_entry"                                     # góp màn vào phân hệ của module khác
    assert workspaces.role_home("demo_clerk") == (("demo",), ("demo_list",))
    assert workspaces.role_home("admin")[1][-1] == "demo_list" and workspaces.role_picker("demo_clerk")[0] == "Nhân viên"
    assert any(s.key == "demo_list" for _, s in workspaces.desk_screens())          # góp vào bàn việc bằng một khai báo trên màn
    assert "demo" in registry.SETUP_RESOURCES and registry.tool_kind(registry.OPS["demo.list"]) == "daily"
    assert ("demo", "GET", "/v1/demo/things/{ref}/file") in [(r.module, r.method, r.path) for r in registry.ROUTES]
    with pytest.raises(ValueError, match="đã có module khác khai"):
        Module("demo2", "Thử 2", pack="demo").route("get", "/v1/demo/things/{ref}/file")
    with pytest.raises(ValueError, match="/v1/"):
        Module("demo3", "Thử 3", pack="demo").route("GET", "/demo/x")
    with pytest.raises(ValueError, match="cần lý do"):
        Module("demo4", "Thử 4", pack="demo").route("GET", "/v1/demo4/x", public="có")
    assert not next(r for r in registry.ROUTES if r.module == "demo").public          # mặc định cần đăng nhập


async def test_the_store_lists_the_new_pack_and_previews_its_screens(demo_pack: None, who: Who) -> None:
    admin = await who("admin")
    store = await q(admin, "admin.pack_store")
    cards = {i["title"]: i for b in store["blocks"] if b["type"] == "tiles" for i in b["items"]}
    assert cards["Gói thử"]["badge"] == "Có sẵn" and cards["Gói thử"]["module"] == "demo"
    info = await q(admin, "admin.pack_info", pack="demo")
    lists = {b["title"]: b["items"] for b in info["blocks"] if b["type"] == "list"}
    assert [i["title"] for i in lists["Màn hình của gói"]] == ["Thử › Danh sách", "Quản trị › Nhập thử"]
    adds = next(v for k, v in lists.items() if k.startswith("Khi bật sẽ thêm"))
    assert {"Nhân viên thử", "Quy trình thử"} <= {i["title"] for i in adds}


async def test_the_pack_supplies_fields_to_compose_without_papers_naming_it(demo_pack: None, who: Who) -> None:
    admin = await who("admin")
    tenant = admin.tenant_id
    await owner_val("INSERT INTO core.tenant_packs (tenant_id, pack) VALUES (%s, 'demo') RETURNING 1", (tenant,))
    packs.invalidate(tenant)
    try:
        await owner_val("""INSERT INTO core.grants (tenant_id, role, resource, scope, actions) VALUES (%s, 'admin', 'demo', 'all', '{read,write}')
                           ON CONFLICT DO NOTHING RETURNING 1""", (tenant,))
        admin = await who("admin")
        assert {"value": "demo.thing", "label": "Đồ thử"} in await q(admin, "papers.subject_kind_options")
        t = await ok(admin, "papers.create_template", name="Biên nhận đồ thử", key="bien_nhan_do_thu", subject_kind="demo.thing", abbr="BN",
                     body="# Biên nhận\nNhận {{thing_name}}, giá {{thing_price}} ({{thing_price|chu}}), ngày {{draft_date}}.")
        await ok(admin, "papers.publish_template", template_id=str(t["template_id"]))
        d = await ok(admin, "papers.start_draft", template_id=str(t["template_id"]), ref="x1")
        page = await q(admin, "papers.view", paper_id=str(d["paper_id"]))
        assert d["missing"] == [] and "Nhận Đồ X1, giá 1.250.000 đồng (Một triệu hai trăm năm mươi nghìn đồng)" in str(page["sheet"]["blocks"])
        btn = next(a for a in page["actions"] if a["command"] == "papers.issue")   # giấy theo hồ sơ: bấm kèm căn cứ đã thấy (0251)
        r = await executor.run(admin, "papers.issue", {"paper_id": str(d["paper_id"])}, expect_basis=executor.basis_of_token(btn["basis"]))
        assert r.status == "ok" and r.output["symbol"].startswith("01/BN-")
    finally:
        await owner_val("DELETE FROM core.grants WHERE tenant_id = %s AND resource = 'demo' RETURNING 1", (tenant,))
        await owner_val("DELETE FROM core.tenant_packs WHERE tenant_id = %s AND pack = 'demo' RETURNING 1", (tenant,))
        packs.invalidate(tenant)


def test_registries_are_clean_after_the_demo_pack_is_gone() -> None:
    assert not any(op.startswith("demo.") for ops in registry.PLUGS.values() for op in ops)
    assert "demo" not in registry.PACKS and "demo" not in registry.MODULES and "demo.list" not in registry.OPS
    assert not any(r.module.startswith("demo") for r in registry.ROUTES)
    assert registry.pack_module("crm") == "crm" and registry.pack_module("base") == "core" and not registry.pack_module("demo")
    assert not any(s.query.startswith("demo.") for _, s in workspaces.desk_screens()) and "demo" not in screens.WORKSPACES
