"""CÂU GỢI Ý CỦA TRỢ LÝ THEO VAI là dữ liệu công ty (ADR 0239 phần 4).

* Bộ MẶC ĐỊNH do gói khai (``Role.suggest / showcase`` của vai gói mình, ``Pack.hints`` cho vai gói khác — api 1.8 / 1.10) và đọc thẳng từ mã: nâng gói
  ⇒ câu mới của gói tự có, không cần cài. Mỗi câu mặc định có KHOÁ ỔN ĐỊNH suy từ công cụ của nó (``crm.leads``, ``crm.leads#2`` nếu vai có hai câu
  cùng công cụ; câu xuyên module ``x:<công cụ>+<công cụ>``) — gói sửa chữ câu thì khoá giữ nguyên, phần công ty đã sửa không mất.
* Phần CÔNG TY ĐỔI ở ``core.role_hints`` (migration base/096): tắt một câu mặc định, sửa chữ (bản gốc của gói giữ nguyên — "về mặc định" = xoá dòng),
  thêm câu riêng của vai gắn MỘT công cụ trả lời nó (``c:<mã>``). Quản trị sửa ở Quản trị › Câu gợi ý trợ lý theo vai (``core.set_role_hint`` /
  ``core.reset_role_hint``) — cùng tài nguyên với Công cụ trợ lý theo vai (``core.ai_tools``, 0218).
* Câu chữ KHÔNG cấp quyền (rà Codex 0239 F3): câu chỉ hiện khi người hỏi chạy được MỌI công cụ của câu + có phạm vi cả công ty trên tài nguyên câu đòi
  (``agent/chat._offered``); công cụ vẫn kiểm quyền khi gọi, kể cả khi người dùng tự gõ câu đã bị ẩn.
"""

import uuid
from dataclasses import dataclass, replace
from typing import Any

from barista.kernel import registry, ui
from barista.kernel.core_ops import m
from barista.kernel.errors import Invalid, NotFound
from barista.kernel.executor import Ctx
from barista.kernel.fields import Input, NoInput, field

RESOURCE = "core.ai_tools"
ANY_ROLE = "*"
CUSTOM = "c:"
# Vai không có câu riêng dùng được (vai công ty tự tạo, gói của vai chưa bật…) ⇒ câu chung này + ``Pack.hints["*"]`` của gói đang bật
GENERIC: list[tuple[str, ...]] = [
    ("core.approvals", "Có phiếu nào đang chờ tôi duyệt không?"),
    ("admin.deliveries", "Có sự kiện nào đang lỗi cần xử lý không?"),
]


@dataclass(frozen=True)
class Hint:
    """Một câu gợi ý: ``kind`` "suggest" (câu quen của vai) / "showcase" (câu xuyên module); ``ops``: công cụ câu cần chạy được (suggest: một);
    ``company``: tài nguyên người hỏi phải có phạm vi cả công ty; ``origin``: "pack" / "edited" / "custom"."""
    key: str
    kind: str
    text: str
    ops: tuple[str, ...]
    company: tuple[str, ...] = ()
    origin: str = "pack"
    default_text: str = ""


def contributed(role: str, on: frozenset[str] | set[str]) -> tuple[list[tuple[str, ...]], list[tuple[str, tuple[str, ...]]]]:
    """Câu gợi ý / câu xuyên module (dạng gói khai) của ``role`` từ các gói ĐANG BẬT: gói sở hữu vai (``Role``) trước, rồi gói góp (``Pack.hints``)."""
    on_packs = [pk for pk in registry.PACKS.values() if pk.key in on]
    suggest: list[tuple[str, ...]] = []
    show: list[tuple[str, tuple[str, ...]]] = []
    for pk in on_packs:
        if (r := pk.roles.get(role)) is not None:
            suggest += r.suggest
            show += r.showcase
    for pk in on_packs:
        if (h := pk.hints.get(role)) is not None:
            suggest += h.suggest
            show += h.showcase
    return suggest, show


def _keyed(base: str, seen: dict[str, int]) -> str:
    seen[base] = seen.get(base, 0) + 1
    return base if seen[base] == 1 else f"{base}#{seen[base]}"


def defaults(role: str, on: frozenset[str] | set[str], *, generic: bool = False) -> list[Hint]:
    """Bộ câu mặc định của vai (chưa áp phần công ty đổi, chưa lọc quyền). ``generic``: bộ câu chung (vai không có câu riêng dùng được)."""
    own, show = contributed(role, on)
    items = [*GENERIC, *contributed(ANY_ROLE, on)[0]] if generic or not own else own
    seen: dict[str, int] = {}
    out = [Hint(_keyed(op, seen), "suggest", q, (op,), tuple(company), default_text=q) for op, q, *company in items]
    if not generic:
        out += [Hint(_keyed("x:" + "+".join(needs), seen), "showcase", q, tuple(needs), default_text=q) for q, needs in show]
    return out


def effective(role: str, on: frozenset[str] | set[str], rows: list[dict[str, Any]] | None, *, generic: bool = False) -> list[Hint]:
    """Bộ câu của vai SAU phần công ty đổi: câu riêng (đang bật) lên trước, câu mặc định bị tắt bỏ đi, câu sửa chữ thay chữ."""
    by = {r["key"]: r for r in rows or ()}
    out = [] if generic else [Hint(r["key"], "suggest", r["text"], (r["tool"],), origin="custom", default_text="")
                              for r in rows or () if r["key"].startswith(CUSTOM) and r["enabled"]]
    for h in defaults(role, on, generic=generic):
        r = by.get(h.key)
        if r is not None and not r["enabled"]:
            continue
        out.append(replace(h, text=r["text"], origin="edited") if r is not None and r["text"] else h)
    return out


async def for_role(conn: Any, role: str) -> list[dict[str, Any]]:
    """Phần công ty đổi cho vai (đọc bằng giao dịch của người hỏi — RLS cách ly công ty)."""
    cur = await conn.execute("SELECT key, enabled, text, tool FROM core.role_hints WHERE role = %s", (role,))
    return [dict(r) for r in await cur.fetchall()]


async def _on(ctx: Ctx) -> set[str]:
    return {*registry.always_on(), *(r["pack"] for r in await ctx.all("SELECT pack FROM core.tenant_packs"))}


async def _role(ctx: Ctx, role: str) -> str:
    if not await ctx.val("SELECT 1 FROM core.roles WHERE key = %s", (role,)):
        raise NotFound(f"Không có vai {role}")
    return role


class HintsIn(Input):
    role: str = field("Vai", "", options_query="admin.role_options", help="Trống = mọi vai")


ORIGIN = {"pack": "Mặc định của gói", "edited": "Công ty sửa chữ", "custom": "Câu riêng của công ty", "off": "Đã tắt"}


@m.query("role_hints", resource=RESOURCE, title="Câu gợi ý trợ lý theo vai")
async def role_hints(ctx: Ctx, inp: HintsIn) -> dict[str, Any]:
    """Mỗi câu gợi ý của mỗi vai một dòng: chữ đang dùng, chữ gốc của gói, công cụ trả lời, đang bật / tắt, do gói hay công ty. Lọc quyền từng người
    vẫn áp khi hiện (câu chỉ hiện cho người chạy được công cụ của câu)."""
    on = await _on(ctx)
    roles = await ctx.all("SELECT key, label FROM core.roles WHERE %s = '' OR key = %s ORDER BY label", (inp.role, inp.role))
    rows_by: dict[str, list[dict[str, Any]]] = {}
    for r in await ctx.all("SELECT role, key, enabled, text, tool FROM core.role_hints"):
        rows_by.setdefault(r["role"], []).append(r)
    out = []
    for r in roles:
        mine = rows_by.get(r["key"], [])
        by = {x["key"]: x for x in mine}
        shown = {h.key for h in effective(r["key"], on, mine)}
        for h in [*effective(r["key"], on, mine), *(d for d in defaults(r["key"], on) if d.key not in shown)]:
            off = h.key not in shown
            out.append({"id": f"{r['key']}|{h.key}", "role": r["key"], "role_label": r["label"], "key": h.key,
                        "kind": h.kind, "text": h.default_text if off else h.text, "default_text": h.default_text,
                        "tools": ", ".join(h.ops), "origin": "off" if off else h.origin, "enabled": not off,
                        "custom": h.origin == "custom", "changed": h.key in by})
    return ui.table(out, [ui.col("role_label", "Vai", width=150),
                          ui.col("text", "Câu gợi ý", width=380),
                          ui.col("kind", "Loại", "badge", labels={"suggest": "Câu quen", "showcase": "Nối nhiều phân hệ"}),
                          ui.col("origin", "Nguồn", "badge", labels=ORIGIN, tones={"pack": "gray", "edited": "blue", "custom": "green", "off": "orange"}),
                          ui.col("tools", "Công cụ trả lời", width=220), ui.col("default_text", "Chữ gốc của gói", width=300)],
                    title="Câu gợi ý trợ lý theo vai", limit=2000,
                    note="Câu hiện ở ô chat của người giữ vai, sau tối đa hai câu “việc hôm nay” (chỉ việc người đó làm được) và luôn chừa ít nhất hai chỗ cho câu của vai. "
                         "Câu chỉ hiện cho người chạy được công cụ của câu — sửa chữ không cấp quyền. Nâng gói không ghi đè chữ công ty đã sửa.")


class SetHint(Input):
    role: str = field("Vai", options_query="admin.role_options")
    key: str = field("Câu", "", hidden=True, help="Trống = thêm câu riêng của công ty cho vai này", max_length=200)   # dòng được bấm điền sẵn — không ai gõ khoá câu
    text: str = field("Chữ câu gợi ý", "", max_length=300, help="Trống = giữ chữ gốc của gói (câu mặc định)")
    tool: str = field("Công cụ trả lời câu", "", options_query="core.hint_tool_options",
                      help="Chỉ cho câu riêng: trợ lý dùng công cụ này để trả lời — câu chỉ hiện cho người chạy được công cụ đó")
    enabled: bool = field("Hiện câu này", True)


@m.query("hint_tool_options", resource=RESOURCE, title="Công cụ trả lời câu gợi ý")
async def hint_tool_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    on = await _on(ctx)
    return ui.options([{"value": n, "label": f"{registry.MODULES.get(op.module, op.module)} › {op.title}"} for n, op in sorted(registry.OPS.items())
                       if op.kind == "query" and not op.internal and op.public is None and registry.MODULE_PACK.get(op.module, "base") in on])


def _tool(name: str) -> str:
    op = registry.get(name.strip().replace("__", "."))
    if op is None or op.kind != "query" or op.internal or op.public is not None:
        raise Invalid(f"Không có công cụ đọc {name} — chọn trong danh sách")
    return op.name


@m.command("set_role_hint", resource=RESOURCE, title="Sửa / thêm câu gợi ý trợ lý của vai", category="setup", risk="low")
async def set_role_hint(ctx: Ctx, inp: SetHint) -> dict[str, Any]:
    """Câu mặc định của gói: bật / tắt, sửa chữ (trống = chữ gốc). Không nêu câu ⇒ thêm câu riêng (cần chữ + công cụ). Câu gợi ý không cấp quyền nên
    không qua chuỗi đổi quyền; trợ lý chỉ đề xuất (người bấm xác nhận)."""
    role = await _role(ctx, inp.role.strip())
    text = " ".join(inp.text.split())
    key = inp.key.strip()
    if not key or key.startswith(CUSTOM):
        if not text:
            raise Invalid("Câu riêng cần chữ câu gợi ý")
        tool = _tool(inp.tool) if inp.tool.strip() else (await ctx.val("SELECT tool FROM core.role_hints WHERE role = %s AND key = %s", (role, key)) if key else None)
        if not tool:
            raise Invalid("Câu riêng cần công cụ trả lời — chọn trong danh sách")
        key = key or CUSTOM + uuid.uuid4().hex[:10]
    else:
        default = next((h for h in defaults(role, await _on(ctx)) if h.key == key), None)
        if default is None:
            raise NotFound(f"Vai {role} không có câu mặc định {key}")
        tool, text = None, (text if text and text != default.default_text else "")
        if inp.enabled and not text:   # trở lại đúng mặc định ⇒ không giữ dòng
            await ctx.exec("DELETE FROM core.role_hints WHERE role = %s AND key = %s", (role, key))
            return {"role": role, "key": key, "summary": f"Vai {role}: câu “{default.default_text}” theo mặc định của gói"}
    await ctx.exec("""INSERT INTO core.role_hints (role, key, enabled, text, tool, updated_by) VALUES (%s, %s, %s, %s, %s, core.me())
                      ON CONFLICT (tenant_id, role, key) DO UPDATE SET enabled = EXCLUDED.enabled, text = EXCLUDED.text, tool = EXCLUDED.tool,
                                                                     updated_by = core.me(), updated_at = now()""",
                   (role, key, inp.enabled, text or None, tool))
    return {"role": role, "key": key, "summary": f"Vai {role}: {'hiện' if inp.enabled else 'tắt'} câu gợi ý" + (f" “{text}”" if text else "")}


class HintKey(Input):
    role: str = field("Vai", options_query="admin.role_options", hidden=True)
    key: str = field("Câu", max_length=200, hidden=True)


@m.command("reset_role_hint", resource=RESOURCE, title="Câu gợi ý về mặc định / xoá câu riêng", category="setup", risk="low",
           irreversible=True, effect="Xoá phần công ty đã sửa cho câu gợi ý này — câu quay về mặc định của gói, chữ đã sửa không khôi phục được.")
async def reset_role_hint(ctx: Ctx, inp: HintKey) -> dict[str, Any]:
    role = await _role(ctx, inp.role.strip())
    n = await ctx.exec("DELETE FROM core.role_hints WHERE role = %s AND key = %s", (role, inp.key.strip()))
    return {"role": role, "key": inp.key, "summary": ("Đã xoá câu riêng" if inp.key.startswith(CUSTOM) else "Câu về mặc định của gói") if n
            else "Câu vốn theo mặc định"}
