"""CHỜ TÔI DUYỆT (ADR 0253) — MỘT danh sách TỪNG việc người này phải quyết: đề xuất trợ lý soạn (``core.proposals``, gồm thẻ buổi sáng / luật / ứng dụng AI
ngoài) + phiếu duyệt đang chờ (``core.approvals``). Cùng hợp đồng bản chạy thử (``brief``) cho cả hai; nhóm theo loại việc; số trên nút "Việc" = số việc ở đây.

DUYỆT LÔ (``decide_many``): lần lượt TỪNG thẻ qua đúng đường quyết của thẻ đó (``morning.decide`` / ``core.decide`` → executor — không đường chạy mới): mỗi thẻ
vẫn kiểm căn cứ (0071) — thẻ nào đã đổi thì báo conflict và ở lại danh sách, các thẻ khác vẫn đi tiếp; mỗi thẻ có dòng nhật ký riêng. Chỉ cùng MỘT lệnh
(đề xuất) hoặc cùng MỘT chuỗi + lệnh (phiếu duyệt); đề xuất của lệnh TIỀN / QUYỀN không gộp lô (người duyệt từng thẻ) — phiếu cùng chuỗi thì được: người
duyệt đã ở đúng vai, và đó chính là việc "duyệt 147 phiếu đảo phiếu thu cùng loại".
"""

from typing import Any
from uuid import UUID

from barista.agent import morning
from barista.db import db
from barista.kernel import agent_policy, executor, registry
from barista.kernel.errors import Invalid
from barista.kernel.principal import Principal

MAX_BATCH = 100
APPROVAL_LIMIT = 200


def _batchable(op: registry.Op) -> bool:
    """Đề xuất của lệnh này gộp lô được: rủi ro thấp (không tiền / quyền) và không phải lệnh duyệt."""
    return op.name not in executor.DECISIONS and agent_policy.risk_of(op) not in ("money", "authority")


async def waiting(p: Principal) -> list[dict[str, Any]]:
    """Mọi việc chờ ``p`` quyết, nhóm theo loại (cùng nhóm đứng liền nhau); đề xuất soạn ngay nếu hôm nay chưa soạn gì (như ``morning.cards``)."""
    items: list[dict[str, Any]] = []
    for card in await morning.cards(p):
        op = registry.get(card["command"])
        items.append({"kind": "proposal", "id": card["id"], "group": f"proposal:{card['command']}", "group_title": op.title if op else card["command"],
                      "title": card["title"], "reason": card.get("reason", ""), "brief": card["brief"], "batchable": op is not None and _batchable(op), "card": card})
    async with db.tenant_tx(p, read_only=True) as conn:
        cur = await conn.execute(
            """SELECT a.id, a.chain, a.command, a.summary, a.preview, a.created_at, u.name AS requester
               FROM core.approvals a JOIN core.users u ON u.id = a.requested_by
               WHERE a.status = 'pending' AND core.me() = ANY(a.approver_ids) ORDER BY a.created_at DESC LIMIT %s""", (APPROVAL_LIMIT,))
        rows = await cur.fetchall()
    for r in rows:
        op = registry.get(r["command"])
        brief = r["preview"] or {"title": r["summary"], "subject": None, "changes": [], "reason": "", "reversible": {"ok": None, "how": ""}}
        items.append({"kind": "approval", "id": str(r["id"]), "group": f"approval:{r['chain']}:{r['command']}",
                      "group_title": f"Phiếu duyệt · {op.title if op else r['command']}", "title": brief["title"], "reason": brief.get("reason", ""),
                      "requester": r["requester"], "brief": brief, "batchable": True})
    order = {g: i for i, g in enumerate(dict.fromkeys(x["group"] for x in items))}
    return sorted(items, key=lambda x: order[x["group"]])


async def count(p: Principal) -> int:
    """Số việc chờ quyết (CHỈ đọc — không soạn đề xuất): số trên nút "Việc"."""
    n = await morning.open_count(p)
    async with db.tenant_tx(p, read_only=True) as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM core.approvals WHERE status = 'pending' AND core.me() = ANY(approver_ids)")
        row = await cur.fetchone()
    return n + (int(row["n"]) if row else 0)


async def decide_many(p: Principal, kind: str, ids: list[str], decision: str) -> list[dict[str, Any]]:
    """Duyệt lô cùng MỘT loại việc (xem đầu file). Trả từng thẻ: {id, status, message}; không dừng ở thẻ lỗi."""
    if decision not in ("confirm", "dismiss") or kind not in ("proposal", "approval"):
        raise Invalid("Duyệt lô cần kind (proposal | approval) và decision (confirm | dismiss)")
    uniq = list(dict.fromkeys(ids))
    if not uniq or len(uniq) > MAX_BATCH:
        raise Invalid(f"Duyệt lô nhận từ 1 đến {MAX_BATCH} việc một lần")
    async with db.tenant_tx(p, read_only=True) as conn:
        if kind == "proposal":
            cur = await conn.execute("SELECT id, command FROM core.proposals WHERE id = ANY(%s) AND status = 'open'", ([UUID(i) for i in uniq],))
            rows = await cur.fetchall()
            same = {r["command"] for r in rows}
        else:
            cur = await conn.execute("SELECT id, chain, command FROM core.approvals WHERE id = ANY(%s) AND status = 'pending' AND core.me() = ANY(approver_ids)",
                                     ([UUID(i) for i in uniq],))
            rows = await cur.fetchall()
            same = {(r["chain"], r["command"]) for r in rows}
    if len(rows) != len(uniq) or len(same) != 1:
        raise Invalid("Duyệt lô chỉ cho các việc còn chờ và cùng một loại (cùng lệnh" + (" / cùng chuỗi duyệt)" if kind == "approval" else ")"))
    if kind == "proposal":
        op = registry.get(rows[0]["command"])
        if op is None or not _batchable(op):
            raise Invalid("Việc về tiền / quyền được duyệt từng thẻ — không gộp lô")
    out: list[dict[str, Any]] = []
    for id_ in uniq:
        if kind == "proposal":
            r = await morning.decide(p, UUID(id_), decision, None, f"batch:{id_}")
            out.append({"id": id_, "status": r["status"], "message": r["error"] or ("Đã bỏ" if r["status"] == "dismissed" else "Đã xong")})
        else:
            res = await executor.run(p, "core.decide", {"approval_id": id_, "approve": decision == "confirm"}, key=f"batch:{id_}")
            inner = res.output.get("status") if isinstance(res.output, dict) else ""
            out.append({"id": id_, "status": res.status if res.status != "ok" else (inner or "ok"),
                        "message": res.error or (res.output.get("warning") or res.output.get("error") or "" if isinstance(res.output, dict) else "")})
    return out
