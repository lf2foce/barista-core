"""Chuỗi duyệt — tạo phiếu và chốt người duyệt từng bước. (Quyết định duyệt / chạy lệnh đích: ``kernel/core_ops.py``.)

Bước trong chuỗi (dữ liệu, quản trị sửa):
  {"kind": "team_lead"}                 trưởng đội của người xin; người xin là trưởng ⇒ lên đội cha; cả nhánh chưa có trưởng ⇒ chủ tài khoản
  {"kind": "role", "role": "accountant"} mọi người đang hoạt động có vai trò đó
  {"kind": "user", "email": "…"}         đích danh
  {"kind": "owner"}                      chủ tài khoản công ty (core.memberships.owner — 0029; trách nhiệm kiêm cùng vai nghiệp vụ)
Người xin KHÔNG BAO GIỜ tự duyệt phiếu của mình; một người không duyệt hai bước của cùng phiếu. Ngoại lệ DUY NHẤT (0029): chuỗi chỉ gồm bước
chủ tài khoản mà người xin là chủ tài khoản đang hoạt động DUY NHẤT ⇒ không ai trên họ để duyệt — lệnh chạy ngay, nhật ký ghi "tự duyệt do chỉ có
một chủ tài khoản", mọi quản trị nhận thông báo (``sole_owner``).
"""

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from barista.db import Conn
from barista.kernel import notify, teams
from barista.kernel.errors import Invalid
from barista.kernel.principal import Principal

STEP_KINDS = {"team_lead": "Trưởng nhóm", "role": "Vai trò", "user": "Người cụ thể", "owner": "Chủ tài khoản"}
SOLE_OWNER_NOTE = "Tự duyệt do chỉ có một chủ tài khoản"
ADMIN_RESOURCE = "core.admin"


async def _rows(conn: Conn, sql: Any, params: Any) -> list[dict[str, Any]]:
    cur = await conn.execute(sql, params)
    return await cur.fetchall()


async def resolve_step(conn: Conn, tenant_id: UUID, step: dict[str, Any], requester: UUID,
                       exclude: set[UUID]) -> tuple[str, list[UUID]]:
    kind = step.get("kind")
    if kind == "team_lead":   # trưởng gần nhất đi NGƯỢC cây từ đội CHÍNH của người xin (kernel/teams.py)
        rows = await _rows(conn, """
            WITH RECURSIVE up AS (
                SELECT t.id, t.parent_id, 0 AS depth FROM core.teams t WHERE t.id = %(home)s
                UNION ALL
                SELECT t.id, t.parent_id, up.depth + 1 FROM core.teams t JOIN up ON t.id = up.parent_id
            )
            SELECT tm.user_id, up.depth FROM up
            JOIN core.team_members tm ON tm.team_id = up.id AND tm.is_lead
            JOIN core.memberships m ON m.user_id = tm.user_id AND m.tenant_id = core.tenant() AND m.active
            WHERE tm.user_id <> ALL(%(ex)s)
            ORDER BY up.depth""", {"home": await teams.home(conn, tenant_id, requester), "ex": list(exclude | {requester})})
        ids = [r["user_id"] for r in rows if r["depth"] == rows[0]["depth"]] if rows else []
        if not ids:   # người xin chưa thuộc đội nào, hoặc cả nhánh cây chưa có trưởng (đội mới, cơ cấu đang dựng lại): phiếu không kẹt — chủ tài khoản
            label, ids = await resolve_step(conn, tenant_id, {"kind": "owner"}, requester, exclude)   # duyệt thay, nhãn nói rõ vì sao
            return f"{label} (chưa có trưởng nhóm)", ids
        return step.get("label") or "Trưởng nhóm", ids
    if kind == "role":
        rows = await _rows(conn, """
            SELECT m.user_id, r.label FROM core.memberships m JOIN core.roles r ON r.tenant_id = m.tenant_id AND r.key = m.role
            WHERE m.role = %(role)s AND m.active AND m.user_id <> ALL(%(ex)s)""",
            {"role": step.get("role"), "ex": list(exclude | {requester})})
        label = step.get("label") or (rows[0]["label"] if rows else str(step.get("role")))
        return label, [r["user_id"] for r in rows]
    if kind == "user":
        rows = await _rows(conn, """
            SELECT u.id, u.name FROM core.users u JOIN core.memberships m ON m.user_id = u.id AND m.tenant_id = core.tenant()
            WHERE u.email = %(email)s AND m.active AND u.id <> ALL(%(ex)s)""",
            {"email": str(step.get("email", "")).lower(), "ex": list(exclude | {requester})})
        return step.get("label") or (rows[0]["name"] if rows else str(step.get("email"))), [r["id"] for r in rows]
    if kind == "owner":
        rows = await _rows(conn, "SELECT m.user_id FROM core.memberships m WHERE m.owner AND m.active AND m.user_id <> ALL(%(ex)s)",
                           {"ex": list(exclude | {requester})})
        return step.get("label") or STEP_KINDS["owner"], [r["user_id"] for r in rows]
    raise Invalid(f"Bước duyệt không hợp lệ: {step}")


async def sole_owner(conn: Conn, p: Principal, chain: str) -> bool:
    """Ngoại lệ một-chủ-tài-khoản (0029): chuỗi ``chain`` CHỈ gồm bước chủ tài khoản, người xin (NGƯỜI, không phải trợ lý) là chủ tài khoản đang
    hoạt động và không còn chủ tài khoản nào khác ⇒ True (bên gọi chạy ngay + ghi nhật ký + báo quản trị). Có chủ tài khoản thứ hai ⇒ False:
    phiếu đi người kia như thường."""
    if p.user_id is None or p.via != "human" or p.system:
        return False
    cur = await conn.execute("SELECT steps FROM core.approval_chains WHERE key = %s", (chain,))
    row = await cur.fetchone()
    if row is None or not row["steps"] or any(s.get("kind") != "owner" for s in row["steps"]):
        return False
    cur = await conn.execute("SELECT array_agg(user_id) AS ids FROM core.memberships WHERE owner AND active")
    ids = (await cur.fetchone() or {}).get("ids") or []
    return list(ids) == [p.user_id]


async def tell_admins_self_approved(conn: Conn, p: Principal, what: str) -> None:
    """Chủ tài khoản duy nhất tự đổi quyền ⇒ mọi người có quyền quản trị (trừ chính họ) biết ngay (0029)."""
    rows = await _rows(conn, """
        SELECT mb.user_id FROM core.memberships mb JOIN core.grants g ON g.tenant_id = mb.tenant_id AND g.role = mb.role
        WHERE mb.active AND g.resource = %(r)s AND g.scope = 'all' AND 'manage' = ANY(g.actions) AND mb.user_id <> %(me)s""",
                       {"r": ADMIN_RESOURCE, "me": p.user_id})
    await notify.send(conn, [r["user_id"] for r in rows], kind="approval_done", title=f"{SOLE_OWNER_NOTE}: {what}",
                      body=f"{p.name or p.email} là chủ tài khoản duy nhất nên thay đổi chạy ngay. Thêm chủ tài khoản thứ hai ở Quản trị › Người dùng "
                           "để thay đổi quyền có người duyệt.",
                      push="Một thay đổi quyền vừa chạy không qua người duyệt thứ hai", open={"query": "admin.audit", "input": {}}, actor=p.user_id)


async def create(conn: Conn, p: Principal, *, chain: str, command: str, input: dict[str, Any], summary: str,
                 run_as: str, on_reject: str | None = None, basis: dict[str, str] | None = None, preview: dict[str, Any] | None = None) -> UUID:
    """``basis``: căn cứ của lệnh lúc xin (0071) — ``core.decide`` tính lại mỗi lần người đồng ý; khác ⇒ phiếu "Căn cứ đã đổi", không chạy.
    ``summary``: đúng việc + hồ sơ + số tiền (không còn "<người>: <tên lệnh>" — người xin là cột riêng); ``preview``: bản chạy thử chuẩn của lệnh
    (``kernel/preview.py``, ADR 0253) — hồ sơ · cũ → mới · vì sao · đảo được; thẻ phiếu vẽ đúng nó, như thẻ đề xuất."""
    if p.user_id is None:
        raise Invalid("Phiếu duyệt cần người xin")
    cur = await conn.execute("SELECT label, steps FROM core.approval_chains WHERE key = %s", (chain,))
    row = await cur.fetchone()
    if row is None:
        raise Invalid(f"Chưa cấu hình chuỗi duyệt “{chain}” — vào Quản trị › Chuỗi duyệt")
    # Chốt người duyệt bước đầu ngay; các bước sau chốt khi tới lượt (người có thể đổi đội / nghỉ trong lúc chờ).
    label, ids = await resolve_step(conn, p.tenant_id, row["steps"][0], p.user_id, set())
    if not ids:
        raise Invalid(f"Bước duyệt “{label}” không có người duyệt (người xin không tự duyệt) — quản trị cần sửa chuỗi duyệt")
    steps = [{**s, "label": s.get("label") or STEP_KINDS.get(s.get("kind", ""), "")} for s in row["steps"]]
    steps[0]["label"] = label
    steps[0]["approver_ids"] = [str(i) for i in ids]
    cur = await conn.execute(
        """INSERT INTO core.approvals (chain, command, input, run_as, on_reject, summary, requested_by, steps, approver_ids,
                                       participant_ids, basis, preview)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (chain, command, Jsonb(input), run_as, on_reject, summary, p.user_id, Jsonb(steps), ids, ids,
         None if basis is None else Jsonb(basis), None if preview is None else Jsonb(preview)),
    )
    created = await cur.fetchone()
    assert created is not None
    await notify.send(conn, ids, kind="approval", title=f"{p.name or p.email} xin duyệt: {summary}", body=(preview or {}).get("reason") or "",
                      push="Có phiếu chờ anh/chị duyệt", open=open_of(created["id"]), actor=p.user_id)
    return created["id"]


def open_of(approval_id: Any) -> dict[str, Any]:
    """Đường mở ĐÚNG MỘT phiếu (thông báo, tin nhắn, "Đã gửi duyệt" trên thẻ) — không mở cả danh sách rồi bắt tìm (ADR 0253)."""
    return {"query": "core.approval", "input": {"approval_id": str(approval_id)}}
