"""MENU APP do server mô tả (manifest), lọc theo quyền từng người — RÁP từ màn các module tự khai (``kernel/screens.py``, 0069).

Mỗi màn = một truy vấn (bảng / chi tiết) + nút lệnh trên thanh công cụ + nút lệnh trên từng dòng + mở chi tiết khi chạm
dòng (``row_click``: bấm MỘT lần mở chi tiết / nút mặc định — trường THÊM, chỉ có khi người xem chạy được đích). App Mac chỉ vẽ theo đây: thêm màn / đổi menu / đổi quyền KHÔNG cần build lại app.
Người không có quyền chạy truy vấn của màn ⇒ không thấy màn; không có quyền chạy lệnh ⇒ không thấy nút.
File này không biết module / gói nào: phân hệ, màn, màn chính theo vai đều đọc từ registry.
``min_scope="all"``: màn cấp công ty (kế toán xem hoa hồng mọi sale) — sale có quyền xem hoa hồng CỦA MÌNH không thấy màn
này ở phân hệ Kế toán. ``needs_action``: màn nhập liệu — chỉ hiện khi người đó chạy được ít nhất một lệnh nhập.
"""

from collections.abc import Iterable
from dataclasses import replace
from typing import Any
from uuid import UUID

from barista.kernel import clock, company_settings, executor, needs, registry, screens, ui, vocab
from barista.kernel.attention import Line, kpi, line, num
from barista.kernel.executor import can_run
from barista.kernel.fields import DEFAULT_COUNTRY, NATIONAL
from barista.kernel.forms import field_specs
from barista.kernel.principal import Principal, system_principal
from barista.kernel.screens import Action, Desk, Open, Screen, Workspace
from barista.kernel.text import fold

A = Action

def _waiting_approvals(o: Any) -> Line | None:
    n = kpi(o, "Chờ bạn duyệt")
    return line("warn", f"{num(n)} phiếu đang chờ anh/chị duyệt", "Em đã chạy thử từng phiếu — anh/chị chỉ cần xem và bấm") if n else None


# Phiếu duyệt là của lõi — màn duy nhất khai ở đây; là một mục "cần tôi quyết" của bàn việc (0094).
INBOX = Screen("inbox", "Phiếu duyệt", "checkmark.seal", "core.approvals", row_actions=(
    A("core.decide", "Duyệt", bind={"approval_id": "id"}, preset={"approve": True}, when={"mine": ["Chờ bạn"]}, style="primary"),
    A("core.decide", "Từ chối", bind={"approval_id": "id"}, preset={"approve": False}, when={"mine": ["Chờ bạn"]}, style="destructive"),
), row_click="open", open=Open("core.approval", {"approval_id": "id"}, "Phiếu"), desk=Desk("decide", say=_waiting_approvals, menu=True, rank=10))


ACCOUNT = "account"   # phân hệ giả của các màn CÁ NHÂN — manifest gửi ở ``account``, không nằm trong ``workspaces``


def workspaces() -> tuple[Workspace, ...]:
    """Mọi phân hệ (đã ráp màn góp), theo thứ tự menu."""
    return screens.assemble()


def desk_screens() -> list[tuple[str, Screen]]:
    """Màn góp vào bàn việc (0094): (phân hệ, màn) — phiếu duyệt của lõi + màn module khai ``desk=``; MỖI MÀN MỘT LẦN (cùng màn góp vào hai phân
    hệ vẫn là một mục — khoá theo truy vấn + màn)."""
    seen: set[tuple[str, str]] = {(INBOX.query, INBOX.key)}
    out: list[tuple[str, Screen]] = [("", INBOX)]
    for w in workspaces():
        for s in w.screens:
            if s.desk is not None and (s.query, s.key) not in seen:
                seen.add((s.query, s.key))
                out.append((w.key, s))
    return out


def screen_spec(p: Principal, s: Screen) -> dict[str, Any] | None:
    """Mô tả màn cho app (đã lọc quyền) — None nếu người này không mở được màn."""
    spec = _screen(p, s)
    return None if spec is None else vocab.apply(spec, p.vocab)


def _can(p: Principal, name: str) -> bool:
    return can_run(p, name)


def _action(p: Principal, a: Action) -> dict[str, Any] | None:
    if a.slot:   # điểm cắm nút (0124): gói đang bật cắm lệnh của mình ⇒ thay nút gốc (tên nút lấy từ lệnh thay)
        a = next((replace(a, command=c, title="", slot="") for c in registry.ACTION_PLUGS.get(a.slot, ()) if _can(p, c)), a)
    if not _can(p, a.command):
        return None
    op = registry.OPS[a.command]
    return {"command": a.command, "title": a.title or op.title, "bind": a.bind, "prefill": a.prefill, "preset": a.preset, "when": a.when,
            "style": a.style, "importable": op.importable and op.import_table is None, **({"import_only": True} if op.import_table is not None else {})}


PRIMARY_DEFAULT = 2


def primary_filters(filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ô lọc CHÍNH của màn (rà bố cục 18/11/2026): app để ô chính ngoài hàng đầu, ô còn lại vào nút "Bộ lọc". Truy vấn khai ``field(primary=True)``
    ⇒ đúng các ô đó; không khai ô nào ⇒ ``PRIMARY_DEFAULT`` ô đầu không thuộc nhóm gấp (``group``). Chỉ THÊM trường ``primary`` — app cũ bỏ qua."""
    if any(f.get("primary") for f in filters):
        return filters
    picked = {f["key"] for f in [f for f in filters if not f.get("group")][:PRIMARY_DEFAULT]}
    return [{**f, "primary": True} if f["key"] in picked else f for f in filters]


def _screen(p: Principal, s: Screen) -> dict[str, Any] | None:
    if not _can(p, s.query):
        return None
    if s.feature and not company_settings.on(p.settings, s.feature):   # công ty tắt nghiệp vụ này (1.63) ⇒ không vai nào có màn
        return None
    op = registry.OPS[s.query]
    # màn thừa hưởng phạm vi tối thiểu của truy vấn: khỏi hiện màn mở ra chỉ để báo thiếu quyền (min_scope khai một lần ở truy vấn)
    if (s.min_scope and not p.has_scope(op.resource, s.min_scope)) or (op.min_scope and not p.has_scope(op.resource, op.min_scope)):
        return None
    if s.max_scope and op.resource and p.scope(op.resource) == "all" and s.max_scope != "all":
        return None
    if s.needs and not needs.meets(p, ui.needs_of(s.needs)):   # nội dung chính của màn là nhóm thông tin người xem không có (1.59)
        return None
    actions = [x for a in s.actions if (x := _action(p, a))]
    if s.needs_action and not actions:
        return None
    row_actions = [x for a in s.row_actions if (x := _action(p, a))]
    opener = {"query": s.open.query, "bind": s.open.bind, "title": s.open.title} if s.open and _can(p, s.open.query) else None
    # bấm một lần (Screen.row_click): chỉ gửi khi người xem chạy được đích (chi tiết mở được / nút còn sau lọc quyền) — không thì bấm chỉ chọn dòng
    click = s.row_click if (s.row_click == "open" and opener) or any(a["command"] == s.row_click for a in row_actions) else ""
    return {
        "key": s.key, "title": s.title, "icon": s.icon, "tint": s.tint or None, "query": s.query, "group": s.group,
        **({} if s.pinnable else {"pinnable": False}),
        **({"badge": s.badge} if s.badge else {}),
        **({"filters_side": True} if s.filters_side else {}),
        "filters": primary_filters([f for f in field_specs(op, p.vocab) if not f["hidden"] and f["key"] not in s.input] + list(s.fields)),
        "input": ui.jsonable_input(s.input),   # đầu vào cố định (0098) — app gửi kèm mọi lần chạy truy vấn của màn
        "ask": {"title": s.ask.title, "prompt": s.ask.prompt, **({"mode": s.ask.mode} if s.ask.mode else {}),
                **({"app_id": s.ask.app_id} if s.ask.app_id else {})} if s.ask else None,
        "actions": actions,
        "row_actions": row_actions,
        "open": opener,
        **({"row_click": click} if click else {}),
    }


# Màn chính theo VAI: phân hệ đứng đầu + màn ghim lên đầu khung làm việc (vẫn lọc theo quyền thật) — vai khai ``Role.home`` ở gói của mình,
# gói khác GÓP thêm cho vai đó bằng ``Pack.pins`` (vd gói bán hàng ghim màn giao dịch cho giám đốc). Phân hệ chính có thể là nhiều phân hệ
# (cùng một vai làm ở nhiều loại công ty) — phân hệ không thấy được trong công ty đó tự bị bỏ qua.
# Vai công ty tự tạo ⇒ mặc định: 2 màn đầu của mỗi phân hệ thấy được.
def role_home(role: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    firsts: list[str] = []
    pins: list[str] = []
    for p in registry.PACKS.values():
        r = p.roles.get(role)
        if r and r.home:
            first, mine = r.home
            firsts += [first] if isinstance(first, str) else list(first or ())
            pins += mine
    for p in registry.PACKS.values():
        if (extra := p.pins.get(role)) is not None:
            first, more = extra
            firsts += [first] if first else []
            pins += [k for k in more if k not in pins]
    return tuple(firsts), tuple(pins)


class Personal:
    """Phần menu RIÊNG của một người (0098), nạp từ DB bằng quyền người đó: màn riêng (app tự ghép…) theo phân hệ + ghim theo người."""

    def __init__(self, screens_: dict[str, list[Screen]] | None = None, pins: list[tuple[str, str]] | None = None) -> None:
        self.screens = screens_ or {}
        self.pins = pins or []


def _personal_screen(d: dict[str, Any]) -> Screen | None:
    try:
        a = d.get("ask") if isinstance(d.get("ask"), dict) else None
        ask = screens.Ask(str(a.get("title") or ""), str(a.get("prompt") or ""), str(a.get("mode") or ""), str(a.get("app_id") or "")) if a else None
        return Screen(str(d["key"]), str(d["title"]), str(d.get("icon") or "square.grid.2x2"), str(d["query"]), tint=str(d.get("tint") or ""), ask=ask,
                      input=dict(d.get("input") or {}), group=str(d.get("group") or ""),
                      fields=tuple(f for f in d.get("filters") or [] if isinstance(f, dict) and isinstance(f.get("key"), str)),
                      filters_side=d.get("filters_side") is True)
    except (KeyError, TypeError, ValueError):
        return None


async def personal(p: Principal) -> Personal:
    """Chạy các nguồn màn riêng + ghim của người xem qua executor (quyền người đó, chỉ đọc). Nguồn lỗi / không quyền ⇒ bỏ qua — menu không vỡ."""
    out = Personal()
    for src in screens.PERSONAL:
        r = await executor.query(p, src.query, {})
        if r.ok and isinstance(r.output, list):
            out.screens.setdefault(src.into, []).extend(s for d in r.output if isinstance(d, dict) and (s := _personal_screen(d)))
    r = await executor.query(p, "core.pins", {})
    if r.ok and isinstance(r.output, list):
        out.pins = [(str(x["workspace"]), str(x["key"])) for x in r.output]
    return out


def manifest(p: Principal, tenant_name: str, team_name: str = "", mine: Personal | None = None, *, timezone: str = "",
             country: str = "") -> dict[str, Any]:
    """``timezone``: múi giờ công ty (0169, tên IANA) — app / web hiện giờ theo múi này (trường THÊM; trống = mặc định của bản cài).
    ``country``: quốc gia công ty (0199) + ``phone``: luật hiện SĐT của nước đó — số lưu E.164; số bắt đầu ``+<code>`` hiện dạng nội địa (``trunk`` +
    phần còn lại, có nhóm), số khác giữ ``+…``. ``phone`` null = nước lõi chưa có luật nội địa ⇒ hiện mọi số dạng ``+…``."""
    firsts, pins = role_home(p.role)
    mine = mine or Personal()
    spaces = []
    account: list[dict[str, Any]] = []
    for w in sorted(workspaces(), key=lambda w: w.key not in firsts):  # phân hệ chính của vai lên đầu
        if p.packs is not None and w.pack and w.pack not in p.packs:
            continue   # gói của phân hệ chưa bật ở công ty này ⇒ không có phân hệ (màn góp từ gói nền không kéo nó lên menu)
        own = mine.screens.get(w.key, [])
        screens_ = [x for s in (*own, *w.screens) if s.in_menu and (x := _screen(p, s))]   # màn việc chỉ ở bàn việc (0094); màn riêng lên đầu
        if w.key == ACCOUNT:   # màn CÁ NHÂN (trí nhớ của trợ lý…): không lên menu phân hệ — app vẽ trong menu tài khoản
            account = screens_
            continue
        if screens_:
            spaces.append({"key": w.key, "title": w.title, "icon": w.icon, "screens": screens_, **({} if w.phone else {"phone": False})})
    visible = {s["key"]: w["key"] for w in spaces for s in w["screens"]}
    by_place = {(w["key"], s["key"]) for w in spaces for s in w["screens"]}
    # ghim của chính người đó lên trước (đúng phân hệ đã ghim), rồi ghim mặc định theo vai — một màn một lần. Ghim lưu khoá của màn đã gộp vào màn khác
    # (``Screen.aliases``) ⇒ trỏ tới màn thay nó, đúng phân hệ của màn đó
    moved = {old: (w.key, s.key) for w in workspaces() for s in w.screens for old in s.aliases if (w.key, s.key) in by_place}
    pinned = []
    for ws, k in mine.pins:
        place = (ws, k) if (ws, k) in by_place else moved.get(k) or ((visible[k], k) if k in visible else None)   # màn đổi phân hệ mà giữ khoá ⇒ ghim vẫn đúng
        if place and not any((x["workspace"], x["key"]) == place for x in pinned):
            pinned.append({"workspace": place[0], "key": place[1], "mine": True})
    for k in pins:   # ghim mặc định của vai (gói khai) — khoá đã gộp cũng trỏ sang màn thay nó
        key = k if k in visible else (moved.get(k) or ("", ""))[1]
        if key in visible and not any(x["key"] == key for x in pinned):
            pinned.append({"workspace": visible[key], "key": key})
    if not pinned:
        pinned = [{"workspace": w["key"], "key": s["key"]} for w in spaces for s in
                  [x for x in w["screens"] if x["group"] != "Nhập liệu" and x.get("pinnable", True)][:2]][:5]
    return vocab.apply({   # từ vựng công ty (0124): tên màn / nhãn lọc viết bằng dấu nhãn ⇒ đổi thành nhãn của công ty người xem, ở MỘT chỗ
        "user": {"id": str(p.user_id), "name": p.name, "email": p.email, "role": p.role, "role_label": p.role_label,
                 "team": team_name},
        "tenant": {"id": str(p.tenant_id), "name": tenant_name, "timezone": timezone or clock.default_zone().key,
                   "country": (c := country or DEFAULT_COUNTRY),
                   "phone": {"code": NATIONAL[c][0], "trunk": NATIONAL[c][1]} if c in NATIONAL else None},
        "workspaces": spaces,
        "pins": pinned,
        "inbox": _screen(p, INBOX),
        "account": account,   # trường THÊM (19/11/2026): app cũ bỏ qua
        # màn VIỆC chỉ ở bàn việc (0094) — vẫn là màn mở được (lời chào trợ lý, thông báo, ⌘K tìm tới), chỉ không lặp ở menu
        "desk": [{**x, "workspace": ws} for ws, s in desk_screens() if ws and not s.in_menu and (x := _screen(p, s))],
        # truy vấn người này chạy được (gói đang bật + quyền) — app chỉ gọi phần gắn thêm (hộp tin, …) khi có tên ở đây, khỏi gọi để nhận 404 / 403
        "queries": sorted(n for n, op in registry.OPS.items() if op.kind == "query" and _can(p, n)),
    }, p.vocab)


# Màn chọn vai (dev): nhóm + mô tả "vai này thấy gì" + một câu hỏi mẫu — người mới biết bấm vào vai nào để thử gì. Vai khai ``Role.picker``.
PICKER_ORDER = ("Ban giám đốc", "Quản lý", "Chức năng", "Nhân viên")


def role_picker(role: str) -> tuple[str, str, str]:
    for p in registry.PACKS.values():
        r = p.roles.get(role)
        if r and r.picker:
            return r.picker
    return ("Khác", "", "")


async def picker_titles(tenant_ids: Iterable[UUID]) -> dict[tuple[str, str], str]:
    """Chức danh HIỆN TẠI theo (công ty, người) từ gói khai ``Module.titles`` — chạy bằng quyền hệ thống của từng công ty. Gói tắt / không gói nào khai /
    lỗi ⇒ không có dòng (nhãn suy từ vai + đội). Nhiều gói cùng khai ⇒ gói nạp trước thắng."""
    out: dict[tuple[str, str], str] = {}
    for tid in dict.fromkeys(tenant_ids):
        for name in registry.TITLES:
            r = await executor.query(system_principal(tid), name, {}, quiet=True)
            if r.ok and isinstance(r.output, dict):
                for uid, title in r.output.items():
                    if isinstance(title, str) and title.strip():
                        out.setdefault((str(tid), str(uid)), title.strip())
    return out


def picker_label(role: str, team: str | None, lead: bool | None, title: str = "") -> str:
    """Nhãn người trên nút chọn vai. Có CHỨC DANH ⇒ chức danh (trưởng: chức danh đã nói đơn vị; người khác: kèm đội nếu chức danh chưa nêu). Không ⇒ suy từ
    vai + đội nhà: "Trưởng Marketing" / "Marketing", "Sale · Team Kinh doanh A"; tên đội chỉ lặp lại vai ("Quản trị hệ thống" ở đội "Quản trị") ⇒ chỉ vai."""
    if title:
        return title if lead or not team or fold(team) in fold(title) else f"{title} · {team}"
    if not team:
        return role
    if fold(role) != fold(team) and fold(role).startswith(fold(team)):
        return role
    if lead:
        return f"{role} · {team}" if role.lower().startswith("trưởng") else f"Trưởng {team}" if role == team else f"{role} · trưởng {team}"
    return role if role == team else f"{role} · {team}"


def picker_hint(role: str, title: str, hint: str) -> str:
    """Có chức danh khác tên vai ⇒ mô tả nêu rõ VAI QUYỀN người đó giữ ("Vai Trưởng nhóm — Chỉ thấy đội của mình…"): chức danh nói người làm gì, vai nói
    người thấy gì. Mô tả đã mở bằng tên vai ("Quản trị hệ thống: …") ⇒ không lặp."""
    if not title or fold(title) == fold(role):
        return hint
    body = hint[len(role) + 1:].strip() if hint.lower().startswith(role.lower() + ":") else hint
    return f"Vai {role} — {body[:1].upper()}{body[1:]}" if body else f"Vai {role}"


def picker_card(row: dict[str, Any], title: str = "") -> dict[str, Any]:
    """Một nút chọn vai từ dòng thô của ``auth.dev_logins``: ``role`` = nhãn hiện cạnh tên (chức danh, hoặc suy từ vai + đội), ``role_label`` = vai quyền,
    ``title`` = chức danh (trống nếu không gói nào cung cấp), ``group`` / ``hint`` / ``ask`` theo vai (``Role.picker``)."""
    out = {k: v for k, v in row.items() if k not in ("team", "is_lead", "user_id")}
    group, hint, ask = role_picker(row["role_key"])
    return {**out, "role": picker_label(row["role"], row["team"], row["is_lead"], title), "role_label": row["role"], "title": title, "group": group,
            "hint": picker_hint(row["role"], title, hint), "ask": ask}
