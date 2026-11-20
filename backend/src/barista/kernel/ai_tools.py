"""BỘ CÔNG CỤ TRỢ LÝ THEO VAI (ADR 0218) — vai thấy sẵn những nhóm / công cụ nào mỗi lượt, là DỮ LIỆU của công ty.

* NHÓM công cụ = một module (khoá module, nhãn = tên module) — cùng đơn vị với công tắc nhóm của trợ lý (``admin.agent_groups``, 0144). Không gắn
  thẻ từng thao tác: nhóm suy từ sổ đăng ký.
* Bộ của một vai = danh sách MỤC: khoá nhóm (cả nhóm) hoặc tên thao tác (``module.tên`` — khi chỉ cần vài công cụ của một nhóm lớn). Bảng
  ``core.role_tools`` (migration base/088), một dòng / vai. Vai KHÔNG có dòng ⇒ trợ lý tự tuyển như trước (0095: phần cố định + tìm theo câu hỏi).
* Bộ công cụ KHÔNG BAO GIỜ thêm quyền: công cụ trợ lý mang = (bộ của vai ∪ lõi luôn mang ∪ nhóm đã mở) ∩ quyền người hỏi ∩ chế độ trợ lý
  (``agent/toolset.role_plan``). Nhóm ngoài bộ vẫn mở được giữa lượt (``core.open_group``) — không có ngõ cụt.
* Gói mang bộ MẶC ĐỊNH bằng ``Role(tools=…)`` / ``Pack(tools={vai: …})`` ⇒ ``admin.install_defaults`` gọi ``core.default_role_tools`` (chỉ THÊM mục
  còn thiếu, một lần theo nhật ký cài). Quản trị sửa ở Quản trị › Công cụ trợ lý theo vai (``core.set_role_tools``) hoặc trả về tự tuyển
  (``core.clear_role_tools``). Trợ lý không tự sửa bộ của mình (``agent="deny"``).
"""

from typing import Any

from barista.kernel import registry, ui
from barista.kernel.core_ops import m
from barista.kernel.errors import Invalid, NotFound
from barista.kernel.executor import Ctx
from barista.kernel.fields import Input, NoInput, field

RESOURCE = "core.ai_tools"
DEFAULT = "core.default_role_tools"
MAX_ITEMS = 80   # trần số mục một vai (nhóm + thao tác) — bộ là để GỌN; muốn tất cả thì để trợ lý tự tuyển


def _tool_op(op: registry.Op) -> bool:
    """Thao tác có thể là công cụ của trợ lý cá nhân (không nội bộ, không hộp chọn, không link công khai, không trình tạo app)."""
    return not op.internal and not op.name.endswith("_options") and op.public is None and registry.tool_kind(op) != "builder"


def groups() -> dict[str, str]:
    """Nhóm công cụ = module có ít nhất một thao tác làm công cụ được: khoá → nhãn (thứ tự sổ đăng ký)."""
    have = {op.module for op in registry.OPS.values() if _tool_op(op)}
    return {k: label for k, label in registry.MODULES.items() if k in have}


def expand(items: list[str] | tuple[str, ...]) -> set[str]:
    """Mục ⇒ tên thao tác: khoá nhóm ⇒ mọi thao tác công cụ của module; tên thao tác ⇒ chính nó. Mục lạ (gói đã gỡ) bỏ qua."""
    out: set[str] = set()
    wanted = {i for i in items if "." not in i}
    for name, op in registry.OPS.items():
        if _tool_op(op) and (op.module in wanted or name in items):
            out.add(name)
    return out


def _clean(items: list[str]) -> list[str]:
    out = list(dict.fromkeys(i.strip().replace("__", ".") for i in items if i.strip()))
    if bad := [i for i in out if registry.tool_entry_module(i) is None or ("." in i and not _tool_op(registry.OPS[i])) or ("." not in i and i not in groups())]:
        raise Invalid(f"Không có nhóm / công cụ trợ lý: {', '.join(bad)}")
    if len(out) > MAX_ITEMS:
        raise Invalid(f"Tối đa {MAX_ITEMS} mục một vai — chọn cả nhóm thay vì từng công cụ, hoặc để trợ lý tự tuyển")
    return out


async def _role(ctx: Ctx, role: str) -> str:
    if not await ctx.val("SELECT 1 FROM core.roles WHERE key = %s", (role,)):
        raise NotFound(f"Không có vai {role}")
    return role


async def for_role(conn: Any, role: str) -> list[str] | None:
    """Bộ của vai (trợ lý đọc mỗi lượt bằng giao dịch của người hỏi — RLS cách ly công ty); None = vai chưa đặt ⇒ trợ lý tự tuyển."""
    cur = await conn.execute("SELECT tools FROM core.role_tools WHERE role = %s", (role,))
    row = await cur.fetchone()
    return None if row is None else sorted(row["tools"])


class SetRoleTools(Input):
    role: str = field("Vai", options_query="admin.role_options")
    groups: list[str] = field("Nhóm công cụ (cả nhóm)", [], options_query="core.tool_group_options",
                              help="Trợ lý của vai mang sẵn MỌI công cụ của nhóm (trong quyền người hỏi). Nhóm khác trợ lý vẫn mở được khi câu hỏi cần")
    tools: list[str] = field("Thêm từng công cụ", [], help="Tên thao tác (vd acct.cashflow) — khi chỉ cần vài công cụ của một nhóm lớn")


class RoleKey(Input):
    role: str = field("Vai", options_query="admin.role_options")


@m.command("set_role_tools", resource=RESOURCE, title="Đặt bộ công cụ trợ lý của vai", agent="deny", category="setup", risk="low")
async def set_role_tools(ctx: Ctx, inp: SetRoleTools) -> dict[str, Any]:
    """Thay TOÀN BỘ bộ công cụ của một vai. Bộ không cấp quyền (người không có quyền một công cụ thì trợ lý vẫn không thấy) nên không qua chuỗi
    "đổi quyền"; công ty muốn duyệt thì đặt luật duyệt cho lệnh này. Có hiệu lực từ lượt hỏi sau."""
    role = await _role(ctx, inp.role.strip())
    items = _clean([*inp.groups, *inp.tools])
    await ctx.exec("""INSERT INTO core.role_tools (role, tools, updated_by) VALUES (%s, %s, core.me())
                      ON CONFLICT (tenant_id, role) DO UPDATE SET tools = EXCLUDED.tools, updated_by = core.me(), updated_at = now()""", (role, items))
    n = len(expand(items))
    return {"role": role, "tools": items, "summary": f"Trợ lý của vai {role} mang sẵn {len(items)} mục (≈ {n} công cụ, trong quyền từng người)"}


@m.command("clear_role_tools", resource=RESOURCE, title="Để trợ lý tự tuyển công cụ cho vai", agent="deny", category="setup", risk="low")
async def clear_role_tools(ctx: Ctx, inp: RoleKey) -> dict[str, Any]:
    """Bỏ bộ của vai ⇒ trợ lý tự tuyển như trước (công cụ trên màn chính của vai + tìm theo câu hỏi)."""
    role = await _role(ctx, inp.role.strip())
    n = await ctx.exec("DELETE FROM core.role_tools WHERE role = %s", (role,))
    return {"role": role, "summary": f"Vai {role}: trợ lý tự tuyển công cụ theo câu hỏi" if n else f"Vai {role} vốn chưa đặt bộ công cụ"}


class DefaultIn(Input):
    role: str
    tools: list[str]


@m.command("default_role_tools", resource=RESOURCE, title="Cài bộ công cụ trợ lý mặc định của vai", internal=True, agent="deny", callers=("admin",),
           why="admin.install_defaults cài bộ công cụ mặc định gói khai (Role.tools / Pack.tools — 0218) kể cả khi quản trị bật gói chưa có quyền "
               "core.ai_tools. Chỉ THÊM mục còn thiếu vào bộ của vai (chưa có dòng ⇒ tạo); mục lạ bỏ qua. Không cấp quyền. Trả {role, added}.")
async def default_role_tools(ctx: Ctx, inp: DefaultIn) -> dict[str, Any]:
    if not await ctx.val("SELECT 1 FROM core.roles WHERE key = %s", (inp.role,)):
        raise Invalid(f"Vai {inp.role} chưa có trong công ty")
    items = [i for i in dict.fromkeys(inp.tools) if registry.tool_entry_module(i) is not None]
    before = await ctx.val("SELECT tools FROM core.role_tools WHERE role = %s", (inp.role,))
    merged = sorted({*(before or []), *items})
    await ctx.exec("""INSERT INTO core.role_tools (role, tools) VALUES (%s, %s)
                      ON CONFLICT (tenant_id, role) DO UPDATE SET tools = EXCLUDED.tools, updated_at = now()""", (inp.role, merged))
    return {"role": inp.role, "added": len(merged) - len(before or [])}


@m.query("tool_group_options", resource=RESOURCE, title="Nhóm công cụ trợ lý")
async def tool_group_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    n: dict[str, int] = {}
    for op in registry.OPS.values():
        if _tool_op(op):
            n[op.module] = n.get(op.module, 0) + 1
    return ui.options([{"value": k, "label": f"{label} ({n[k]})"} for k, label in groups().items()])


@m.query("role_tools", resource=RESOURCE, title="Công cụ trợ lý theo vai")
async def role_tools(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Mỗi vai một dòng: đang đặt bộ hay để trợ lý tự tuyển, nhóm mang cả nhóm, công cụ lẻ, số công cụ tối đa (trước khi lọc quyền từng người)."""
    labels = groups()
    rows = []
    for r in await ctx.all("""SELECT r.key, r.label, t.tools, t.updated_at, u.name AS updated_by FROM core.roles r
                               LEFT JOIN core.role_tools t ON t.role = r.key LEFT JOIN core.users u ON u.id = t.updated_by ORDER BY r.key"""):
        items = list(r["tools"] or [])
        whole = [i for i in items if "." not in i]
        rows.append({"id": r["key"], "role": r["key"], "label": r["label"], "state": "set" if r["tools"] is not None else "auto",
                     "groups": ", ".join(labels.get(g) or g for g in whole), "tools": ", ".join(i for i in items if "." in i),
                     "count": len(expand(items)) if r["tools"] is not None else None, "updated_at": r["updated_at"], "updated_by": r["updated_by"],
                     "groups_in": whole, "tools_in": [i for i in items if "." in i]})
    return ui.table(rows, [ui.col("label", "Vai", width=160),
                           ui.col("state", "Trợ lý", "badge", labels={"set": "Theo bộ của vai", "auto": "Tự tuyển theo câu hỏi"},
                                  tones={"set": "green", "auto": "gray"}),
                           ui.col("groups", "Mang cả nhóm", width=260), ui.col("tools", "Công cụ lẻ", width=260),
                           ui.col("count", "Tối đa (công cụ)", "number"), ui.col("updated_by", "Người sửa"), ui.col("updated_at", "Sửa lúc", "datetime")],
                    title="Công cụ trợ lý theo vai", limit=500,
                    note="Trợ lý của vai mang sẵn các nhóm / công cụ này mỗi lượt (gọn ⇒ nhanh, ít token) và luôn thấy danh sách ngắn các nhóm khác để mở "
                         "khi câu hỏi cần. Bộ không cấp quyền: mỗi người chỉ thấy công cụ mình được dùng; nhóm công ty tắt ở “Nhóm công cụ của trợ lý” vẫn tắt.")
