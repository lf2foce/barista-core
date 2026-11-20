"""CẤU HÌNH MẶC ĐỊNH ĐI KÈM GÓI (quyết định 0059, 0069) — vai, quyền, đội, chuỗi duyệt, luật duyệt, mẫu quy trình mỗi gói mang theo.

Lõi + gói là CODE (giống nhau mọi công ty); cấu hình công ty (của từng khách) là DỮ LIỆU. Đây chỉ là bộ MẶC ĐỊNH để bắt đầu:
* tạo công ty ⇒ cài phần nền + các gói chọn (``seed.create_tenant``);
* bật thêm gói sau ⇒ cài phần của gói đó (``admin.set_pack`` → ``admin.install_defaults``).
Chỉ THÊM phần còn thiếu (``ON CONFLICT DO NOTHING``; mẫu quy trình đã có thì bỏ qua) — chỗ công ty đã sửa không bị ghi đè. Sau khi cài, quản
trị sửa mọi thứ trong app.
MỖI GÓI TỰ KHAI phần mặc định của mình ở ``Pack(roles=, grants=, teams=, chains=, approval_rules=, flows=, vocab=)`` trong module đầu của gói (0069) — cài đặt công ty bật / tắt
khai bằng ``Module.setting`` (0128), mặc định theo gói của module:
vai của gói kèm quyền của vai đó lên mọi tài nguyên (``Role.grants``); quyền cho vai của gói KHÁC (admin, director…) lên tài nguyên của gói
này khai ở ``Pack.grants``. File này chỉ RÁP từ registry — không import module nghiệp vụ, không biết tên gói nào.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from barista.kernel import company_settings, registry


def roles() -> dict[str, tuple[str, str]]:
    """vai → (nhãn, gói mang vai này vào công ty) — gom từ mọi gói đã khai."""
    return {k: (r.label, p.key) for p in registry.PACKS.values() for k, r in p.roles.items()}


def entry_roles() -> list[str]:
    """Vai gói khai ``entry`` (0029): vai nhập môn mặc định — mời người mới với vai này chạy thẳng."""
    return sorted(k for p in registry.PACKS.values() for k, r in p.roles.items() if r.entry)


def owner_roles() -> list[str]:
    """Vai gói khai ``account_owner`` (0029): công ty chưa có chủ tài khoản đang hoạt động ⇒ người giữ vai này nhận trách nhiệm đó."""
    return sorted(k for p in registry.PACKS.values() for k, r in p.roles.items() if r.account_owner)


def role_label(role: str) -> str:
    return roles().get(role, (role, "base"))[0]


def grants() -> list[tuple[str, str, str, str]]:
    """(vai, tài nguyên, phạm vi, hành động) từ mọi gói: gói của vai khai quyền của vai mình (``Role.grants``); gói của tài nguyên khai quyền
    cho vai gói khác lên tài nguyên mình (``Pack.grants``). Một dòng được cài khi gói của TÀI NGUYÊN đang bật (vd quyền của kế toán lên
    tài nguyên của một gói ngành chỉ có khi bật gói đó)."""
    out = [(k, *g) for p in registry.PACKS.values() for k, r in p.roles.items() for g in r.grants]
    out += [g for p in registry.PACKS.values() for g in p.grants]
    return out


def resource_pack(resource: str) -> str:
    """Gói của một tài nguyên = gói của module khai nó (tài nguyên lõi ⇒ "base")."""
    return registry.MODULE_PACK.get(registry.RESOURCE_MODULE.get(resource, "core"), "base")


def subject_pack(kind: str) -> str:
    """Gói của loại đối tượng quy trình (``<module>.<loại>`` ⇒ gói của module đó; trống ⇒ "base"). Module không cài ⇒ chính tên module — không
    gói nào mang khoá đó nên không bao giờ "đang bật" (mẫu bám đối tượng của gói không cài không được cài)."""
    if not kind:
        return "base"
    mod = kind.split(".")[0]
    return registry.MODULE_PACK.get(mod, mod)


@dataclass(frozen=True)
class Item:
    """Một mục mặc định: loại + khoá (duy nhất trong công ty, cũng là khoá nhật ký cài) + gói + nội dung. ``version`` = dấu vân tay nội dung —
    sửa mẫu trong code ⇒ phiên bản mới ⇒ công ty đã cài bản cũ nhận ĐỀ XUẤT nâng cấp, không bị ghi đè."""
    kind: str
    key: str
    pack: str
    data: dict[str, Any]

    @property
    def version(self) -> str:
        return hashlib.sha1(json.dumps(self.data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


ROLE_TOOLS = "core.default_role_tools"   # thao tác nội bộ thêm bộ công cụ trợ lý mặc định cho vai (kernel/ai_tools.py, ADR 0218 — chỉ thêm)
ROLE_TIER = "core.default_role_ai_tier"   # thao tác nội bộ gán hạng trợ lý AI mặc định cho vai (kernel/ai_tiers.py, ADR 0193 — chỉ thêm)
KIND_ORDER = ("role", "team", "grant", "chain", "rule", "flow", "vocab", "setting", "record")   # thứ tự cài: vai / đội trước quyền và mẫu nhắc tới chúng


def plan(new: set[str], enabled: set[str], roles_present: set[str]) -> list[Item]:
    """Mục mặc định của các gói ``new`` (hàm thuần, test thẳng). ``enabled`` = mọi gói đang bật SAU khi bật (đã gồm "base"). Quyền (vai, tài
    nguyên) thuộc kế hoạch khi gói của tài nguyên đang bật VÀ (gói tài nguyên hoặc gói của vai) thuộc ``new`` — vai có sẵn nhận quyền tài nguyên
    gói mới; vai mới nhận quyền mọi tài nguyên đang bật. Đã cài hay chưa (nhật ký) do bên cài quyết, không phải ở đây."""
    rl = roles()
    fresh = [p for p in registry.PACKS.values() if p.key in new]
    entry = set(entry_roles())
    out = [Item("role", k, pack, {"label": label, "entry": k in entry}) for k, (label, pack) in rl.items() if pack in new]
    have = roles_present | {i.key for i in out}
    out += [Item("team", code, p.key, {"name": name, "kind": kind}) for p in fresh for code, name, kind in p.teams]
    for role, res, scope, actions in grants():
        if role not in have:
            continue
        rp, op = resource_pack(res), rl.get(role, ("", "base"))[1]
        if rp in enabled and (rp in new or op in new):
            out.append(Item("grant", f"{role}|{res}", rp if rp in new else op,
                            {"role": role, "resource": res, "scope": scope, "actions": actions.split()}))
    out += [Item("chain", key, p.key, {"label": label, "steps": list(steps)}) for p in fresh for key, label, steps in p.chains]
    out += [Item("rule", cmd, p.key, {"chain": chain}) for p in fresh for cmd, chain in p.approval_rules]
    # mẫu quy trình bám đối tượng của gói KHÁC (gói năng lực khai mẫu cho hồ sơ của gói ngành): cài khi cả hai gói đang bật, lúc bật gói nào sau
    out += [Item("flow", f.key, p.key, {"name": f.name, "subject_kind": f.subject, "auto_open": f.auto_open, "source": f.source, "follow": f.follow})
            for p in registry.PACKS.values() for f in p.flows
            if registry.pack_closure({p.key, subject_pack(f.subject)}) <= enabled and (p.key in new or subject_pack(f.subject) in new)]
    out += [Item("vocab", term, p.key, {"label": label}) for p in fresh for term, label in p.vocab.items()]   # nhãn mặc định của ngành (0124)
    # cài đặt công ty bật / tắt (0128): module khai bằng Module.setting, mặc định đi kèm gói của module
    out += [Item("setting", s.key, registry.MODULE_PACK.get(s.module, "base"), {"enabled": s.default})
            for s in company_settings.SETTINGS.values() if registry.MODULE_PACK.get(s.module, "base") in new]
    # dòng cấu hình mặc định ghi qua lệnh của module sở hữu (0153): cài khi gói của lệnh đang bật
    out += [Item("record", f"{cmd}:{key}", p.key, {"command": cmd, "input": data}) for p in fresh for cmd, key, data in p.records
            if resource_pack(cmd) in enabled]
    # hạng trợ lý AI mặc định của vai (ADR 0193): sau dòng cấu hình của gói nền (các hạng) — vai mới của gói mới nhận hạng gói khai cho nó
    out += [Item("record", f"{ROLE_TIER}:{k}", p.key, {"command": ROLE_TIER, "input": {"role": k, "tier": r.ai_tier}})
            for p in fresh for k, r in p.roles.items() if r.ai_tier]
    # bộ công cụ trợ lý mặc định của vai (ADR 0218): vai của gói khai ``Role.tools``; gói khác góp nhóm / công cụ của mình (``Pack.tools``) — như quyền:
    # cài khi gói góp đang bật VÀ (gói góp hoặc gói của vai) mới bật; vai chưa có thì chờ (cài khi vai tới)
    out += [Item("record", f"{ROLE_TOOLS}:{k}:{p.key}", p.key, {"command": ROLE_TOOLS, "input": {"role": k, "tools": list(r.tools)}})
            for p in fresh for k, r in p.roles.items() if r.tools]
    out += [Item("record", f"{ROLE_TOOLS}:{k}:{p.key}", p.key, {"command": ROLE_TOOLS, "input": {"role": k, "tools": list(t)}})
            for p in registry.PACKS.values() for k, t in p.tools.items()
            if t and k in have and p.key in enabled and (p.key in new or rl.get(k, ("", ""))[1] in new)]
    return sorted(out, key=lambda i: KIND_ORDER.index(i.kind))


def widens_authority(resource: str, actions: list[str]) -> bool:
    """Quyền này MỞ RỘNG thẩm quyền về tiền / quyền (0059, review 0151): có hành động ghi / quản lý / duyệt trên một tài nguyên mà ít nhất một lệnh của nó
    rủi ro ``money`` / ``authority`` (0144). Công ty ĐANG CHẠY không được cài lặng lẽ quyền như vậy khi nâng cấp — chủ tài khoản cấp (admin.set_grant)."""
    from barista.kernel.agent_policy import risk_of  # nội bộ lõi; nạp muộn tránh vòng import

    if not set(actions) & {"write", "manage", "approve"}:
        return False
    return any(op.kind == "command" and op.resource == resource and risk_of(op) in ("money", "authority") for op in registry.OPS.values())


# ── Đối chiếu mặc định ↔ cấu hình đang có (kiểm trước / sau phát hành; Quản trị › Phần mặc định chưa cài) ──
# Ba loại, KHÔNG lẫn nhau:
#   ``missing`` — chưa từng cài (không có trong nhật ký ``core.pack_installs``) VÀ công ty chưa có ⇒ ``admin.install_defaults`` thêm được, an toàn.
#   ``held``    — như missing nhưng là quyền MỞ RỘNG thẩm quyền tiền / quyền của gói ĐÃ cài từ trước ⇒ install_defaults cố ý KHÔNG cài lặng lẽ (0059);
#                 chủ tài khoản cấp tay (Quản trị › Vai trò & quyền) hoặc nhà vận hành ``set-grant``.
#   ``changed`` — công ty KHÁC mặc định: đã gỡ sau khi cài, hẹp hơn (phạm vi / hành động), chuỗi / luật / cài đặt khác. Là quyết định của công ty ⇒ chỉ báo,
#                 không đề xuất ghi đè, nút cài không đụng (install_defaults chỉ thêm phần thiếu).
# Rộng hơn mặc định / trùng mặc định ⇒ không báo. Từ vựng: công ty đổi chữ là tuỳ biến bình thường ⇒ chỉ báo khi thiếu.
IMPORTANT_KINDS = ("role", "grant", "chain", "rule")   # thiếu loại này ⇒ có việc không ai làm được / lệnh chạy thẳng không qua duyệt


@dataclass(frozen=True)
class Gap:
    kind: str
    key: str
    pack: str
    status: str   # missing | held | changed
    detail: str
    data: dict[str, Any]

    @property
    def important(self) -> bool:
        return self.status in ("missing", "held") and self.kind in IMPORTANT_KINDS


@dataclass
class Current:
    """Ảnh chụp cấu hình công ty để đối chiếu (bên gọi đọc DB; hàm ``drift`` thuần)."""
    logged: set[tuple[str, str]]
    known_packs: set[str]                              # gói đã có dòng nhật ký cài (nâng cấp ⇒ quyền mở rộng thẩm quyền bị giữ)
    roles: set[str]
    teams: set[str]
    grants: dict[tuple[str, str], tuple[str, set[str]]]   # (vai, tài nguyên) → (phạm vi, hành động)
    chains: dict[str, Any]                             # khoá → các bước
    rules: dict[str, str]                              # lệnh → chuỗi
    settings: dict[str, bool]
    vocab: set[str]
    flows: set[str]


def _narrower(have: tuple[str, set[str]], scope: str, actions: list[str]) -> str:
    """Quyền đang có hẹp hơn mặc định ở đâu (rỗng = bằng hoặc rộng hơn)."""
    from barista.kernel.principal import SCOPES

    got_scope, got = have
    why = []
    if SCOPES.index(got_scope) < SCOPES.index(scope):
        why.append(f"phạm vi {got_scope} < mặc định {scope}")
    if lack := [a for a in actions if a not in got]:
        why.append("thiếu " + "/".join(lack))
    return "; ".join(why)


def drift(items: list[Item], cur: Current, widens: Any = None) -> list[Gap]:
    """Đối chiếu kế hoạch mặc định (``plan`` của mọi gói đang bật) với cấu hình đang có. ``widens(resource, actions)`` — mặc định ``widens_authority``."""
    widens = widens or widens_authority
    out: list[Gap] = []
    for it in items:
        d, logged = it.data, (it.kind, it.key) in cur.logged

        def gap(status: str, detail: str = "", _it: Item = it) -> None:
            out.append(Gap(_it.kind, _it.key, _it.pack, status, detail, _it.data))

        if it.kind == "grant":
            have = cur.grants.get((d["role"], d["resource"]))
            if have is None:
                if logged:
                    gap("changed", "công ty đã gỡ quyền này")
                elif it.pack in cur.known_packs and widens(d["resource"], d["actions"]):
                    gap("held", "mở rộng thẩm quyền tiền / quyền — chủ tài khoản cấp")
                else:
                    gap("missing")
            elif why := _narrower(have, d["scope"], d["actions"]):
                gap("changed", f"đang {have[0]}: {' '.join(sorted(have[1]))} — {why}")
        elif it.kind in ("role", "team"):
            if it.key not in (cur.roles if it.kind == "role" else cur.teams):
                gap("changed", "công ty đã gỡ") if logged else gap("missing")
        elif it.kind == "chain":
            if it.key not in cur.chains:
                gap("changed", "công ty đã gỡ chuỗi") if logged else gap("missing")
            elif cur.chains[it.key] != d["steps"]:
                gap("changed", "các bước khác mặc định")
        elif it.kind == "rule":
            if it.key not in cur.rules:
                gap("changed", "công ty đã bỏ luật — lệnh chạy thẳng") if logged else gap("missing")
            elif cur.rules[it.key] != d["chain"]:
                gap("changed", f"qua chuỗi {cur.rules[it.key]} (mặc định {d['chain']})")
        elif it.kind == "setting":
            if it.key not in cur.settings:
                if not logged:
                    gap("missing")
            elif cur.settings[it.key] != d["enabled"]:
                gap("changed", f"đang {'bật' if cur.settings[it.key] else 'tắt'} (mặc định {'bật' if d['enabled'] else 'tắt'})")
        elif it.kind == "vocab":
            if it.key not in cur.vocab and not logged:
                gap("missing")
        elif it.kind == "flow":
            if it.key not in cur.flows and not logged:
                gap("missing")
        elif not logged:   # dòng cấu hình qua lệnh của module (0153): không đọc được "đang có" ở đây ⇒ theo nhật ký cài
            gap("missing")
    return out
