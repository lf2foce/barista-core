"""KỸ NĂNG CỦA TRỢ LÝ (ADR 0198 ở barista-docs; thiết kế 0095 "Bổ sung 05/11") — cách làm một việc hay gặp, là DỮ LIỆU của công ty.

* Một kỹ năng: khoá, tên, KHI DÙNG (một câu), vai thấy, hướng dẫn markdown (có trần), công cụ cần (nhu cầu khai báo — KHÔNG phải quyền), trạng thái
  nháp → đang dùng → ngừng, phiên bản, nguồn (gói / công ty), người soạn, người duyệt. Bảng ``core.ai_skills`` (migration base/083).
* Gói mang bộ MẶC ĐỊNH bằng ``barista.api.skill(...)`` trong ``Pack(records=…)`` ⇒ ``admin.install_defaults`` gọi ``core.install_skill`` (chỉ thêm: công
  ty đã có khoá đó thì giữ của công ty; nội dung lấy từ lời khai của gói trong code, không từ người gọi ⇒ không có đường đưa nội dung chưa duyệt vào
  "đang dùng"). Công ty soạn / sửa kỹ năng của mình: ``save_skill`` (nháp) → ``submit_skill`` → ``approve_skill`` (người KHÁC người soạn — bất biến 6)
  → ``retire_skill``. Bản đang dùng vẫn chạy trong lúc bản nháp mới chờ duyệt.
* Ai được soạn / duyệt = quyền tài nguyên ``core.ai_skills`` (ma trận quyền là dữ liệu — bản cài / chủ tài khoản thu hẹp được). Trợ lý không soạn,
  không duyệt kỹ năng (``agent="deny"``).
* Nạp ba mức (agent/chat.py): mỗi lượt chỉ DANH SÁCH NGẮN (tên + khi dùng) của kỹ năng đang dùng cho vai người hỏi (trần ``LIST_MAX``); model gọi
  ``core.use_skill(khoá)`` mới nhận hướng dẫn + công cụ khai trong kỹ năng (vẫn lọc theo quyền người hỏi); tối đa ``OPEN_MAX`` kỹ năng / lượt.
* Một nguồn luật: hướng dẫn nói CÁCH làm và gọi công cụ / nguồn nào — không chép tỷ lệ, ngưỡng, mức tiền của chính sách vào chữ (``lint`` chặn
  phần trăm và số tiền). Số liệu cho người dùng vẫn từ công cụ, trên thẻ.
"""

from typing import Any

from barista.kernel import registry, ui
from barista.kernel.core_ops import m
from barista.kernel.errors import Invalid, NotFound
from barista.kernel.executor import Ctx
from barista.kernel.fields import Input, NoInput, field
from barista.kernel.skill_specs import DECLARED, INSTALL, KEY, LIST_MAX, MAX_CHARS, TITLE_MAX, WHEN_MAX, lint

STATUS = {"draft": "Nháp", "active": "Đang dùng", "retired": "Ngừng dùng"}
SOURCE = {"pack": "Mặc định của gói", "company": "Của công ty"}


def _check_tools(tools: list[str]) -> list[str]:
    out = list(dict.fromkeys(t.strip().replace("__", ".") for t in tools if t.strip()))
    bad = [t for t in out if (op := registry.get(t)) is None or op.internal]
    if bad:
        raise Invalid(f"Công cụ không có (hoặc là thao tác nội bộ): {', '.join(bad)}")
    return out


async def _check_roles(ctx: Ctx, roles: list[str]) -> list[str]:
    out = list(dict.fromkeys(r.strip() for r in roles if r.strip()))
    have = {r["key"] for r in await ctx.all("SELECT key FROM core.roles WHERE key = ANY(%s)", (out,))}
    if missing := [r for r in out if r not in have]:
        raise Invalid(f"Vai không có trong công ty: {', '.join(missing)}")
    return out


class SkillIn(Input):
    key: str = field("Mã kỹ năng", max_length=60, example="uu_tien_goi_khach", help="Chữ thường không dấu, gạch dưới — không đổi sau khi tạo")
    title: str = field("Tên kỹ năng", max_length=TITLE_MAX, example="Ưu tiên gọi khách hôm nay")
    when_to_use: str = field("Khi dùng", max_length=WHEN_MAX, example="Sale hỏi hôm nay nên gọi ai trước",
                             help="Một câu — trợ lý đọc câu này mỗi lượt để chọn kỹ năng")
    roles: list[str] = field("Vai thấy kỹ năng", [], options_query="admin.role_options")
    instructions: str = field("Hướng dẫn", kind="textarea", max_length=MAX_CHARS,
                              help="Các bước làm, gọi công cụ nào, trình bày ra sao. KHÔNG ghi tỷ lệ / mức tiền / ngưỡng của chính sách — trợ lý đọc từ công cụ")
    tools: list[str] = field("Công cụ cần", [], help="Tên thao tác (vd crm.brief). Kỹ năng không cấp quyền: người không có quyền công cụ thì trợ lý không dùng được")


class SkillKey(Input):
    key: str = field("Mã kỹ năng", max_length=60, options_query="core.skill_options")


@m.command("save_skill", resource="core.ai_skills", title="Soạn / sửa kỹ năng trợ lý", agent="deny", category="setup")
async def save_skill(ctx: Ctx, inp: SkillIn) -> dict[str, Any]:
    """Ghi BẢN NHÁP của một kỹ năng (khoá mới ⇒ phiên bản 1; khoá đã có ⇒ nháp phiên bản kế tiếp, bản đang dùng vẫn chạy tới khi nháp được duyệt).
    Sửa lại nháp đã gửi duyệt ⇒ phải gửi lại. Người sửa cuối là người soạn — không tự duyệt được."""
    key = inp.key.strip().lower()
    if not KEY.match(key):
        raise Invalid("Mã kỹ năng: chữ thường không dấu, số, gạch dưới (bắt đầu bằng chữ, 2–60 ký tự)")
    if bad := lint(inp.title, inp.when_to_use, inp.instructions, inp.roles, inp.tools):
        raise Invalid("; ".join(bad))
    roles, tools = await _check_roles(ctx, inp.roles), _check_tools(inp.tools)
    data = (inp.title.strip(), inp.when_to_use.strip(), roles, inp.instructions.strip(), tools)
    n = await ctx.exec("""UPDATE core.ai_skills SET title = %s, when_to_use = %s, roles = %s, instructions = %s, tools = %s, author_id = core.me(),
                              submitted_at = NULL, updated_at = now() WHERE key = %s AND status = 'draft'""", (*data, key))
    if not n:
        version = await ctx.val("SELECT coalesce(max(version), 0) + 1 FROM core.ai_skills WHERE key = %s", (key,))
        pack = await ctx.val("SELECT pack FROM core.ai_skills WHERE key = %s ORDER BY version DESC LIMIT 1", (key,))
        await ctx.exec("""INSERT INTO core.ai_skills (key, version, title, when_to_use, roles, instructions, tools, status, source, pack, author_id)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, 'draft', 'company', %s, core.me())""", (key, version, *data, pack))
    version = await ctx.val("SELECT version FROM core.ai_skills WHERE key = %s AND status = 'draft'", (key,))
    return {"key": key, "version": version, "summary": f"Đã lưu nháp “{data[0]}” (phiên bản {version}) — gửi duyệt để người khác duyệt"}


@m.command("submit_skill", resource="core.ai_skills", title="Gửi duyệt kỹ năng trợ lý", agent="deny", category="setup")
async def submit_skill(ctx: Ctx, inp: SkillKey) -> dict[str, Any]:
    row = await ctx.one("UPDATE core.ai_skills SET submitted_at = now() WHERE key = %s AND status = 'draft' RETURNING version, title", (inp.key,))
    if row is None:
        raise NotFound("Kỹ năng này không có bản nháp")
    return {"key": inp.key, "version": row["version"], "summary": f"Đã gửi duyệt “{row['title']}” (phiên bản {row['version']})"}


@m.command("approve_skill", resource="core.ai_skills", action="manage", title="Duyệt kỹ năng trợ lý", agent="deny", category="setup")
async def approve_skill(ctx: Ctx, inp: SkillKey) -> dict[str, Any]:
    """Bản nháp đã gửi duyệt ⇒ đang dùng (bản đang dùng cũ ngừng). Người duyệt phải KHÁC người soạn (bất biến 6; DB cũng chặn). Kiểm lại công cụ /
    vai lúc duyệt: công cụ đã gỡ / vai đã xoá từ lúc soạn ⇒ không duyệt."""
    d = await ctx.one("SELECT * FROM core.ai_skills WHERE key = %s AND status = 'draft' FOR UPDATE", (inp.key,))
    if d is None:
        raise NotFound("Kỹ năng này không có bản nháp chờ duyệt")
    if d["submitted_at"] is None:
        raise Invalid("Bản nháp chưa gửi duyệt")
    if d["author_id"] is not None and d["author_id"] == ctx.p.user_id:
        raise Invalid("Người soạn không tự duyệt kỹ năng của mình — nhờ người khác có quyền duyệt")
    _check_tools(list(d["tools"]))
    await _check_roles(ctx, list(d["roles"]))
    await ctx.exec("UPDATE core.ai_skills SET status = 'retired', retired_at = now(), updated_at = now() WHERE key = %s AND status = 'active'", (inp.key,))
    await ctx.exec("""UPDATE core.ai_skills SET status = 'active', approved_by = core.me(), approved_at = now(), updated_at = now()
                      WHERE key = %s AND version = %s""", (inp.key, d["version"]))
    return {"key": inp.key, "version": d["version"], "summary": f"Đã duyệt “{d['title']}” (phiên bản {d['version']}) — trợ lý dùng từ lượt sau"}


@m.command("retire_skill", resource="core.ai_skills", action="manage", title="Ngừng dùng kỹ năng trợ lý", agent="deny", category="setup")
async def retire_skill(ctx: Ctx, inp: SkillKey) -> dict[str, Any]:
    row = await ctx.one("""UPDATE core.ai_skills SET status = 'retired', retired_at = now(), updated_at = now() WHERE key = %s AND status = 'active'
                           RETURNING version, title""", (inp.key,))
    if row is None:
        raise NotFound("Kỹ năng này không có bản đang dùng")
    return {"key": inp.key, "version": row["version"], "summary": f"Đã ngừng “{row['title']}” — trợ lý không dùng từ lượt sau"}


class InstallIn(Input):
    key: str = field("Mã kỹ năng của gói", max_length=60)


@m.command("install_skill", resource="core.ai_skills", title="Cài kỹ năng mặc định của gói", internal=True, agent="deny", callers=("admin",),
           why="admin.install_defaults cài kỹ năng mặc định gói khai (Pack(records=…) qua barista.api.skill) — kể cả khi quản trị công ty bật gói mà "
               "chưa có quyền kỹ năng. Người gọi chỉ nêu khoá; nội dung lấy từ lời khai của gói trong code. Chỉ thêm khoá công ty chưa có. Trả {key, added}.")
async def install_skill(ctx: Ctx, inp: InstallIn) -> dict[str, Any]:
    """Bộ mặc định của gói (``admin.install_defaults``): nội dung lấy từ lời khai của gói (``DECLARED``, review trong repo) — người gọi chỉ nêu khoá.
    Công ty đã có khoá này (bất kỳ phiên bản / trạng thái nào — kể cả đã ngừng) ⇒ giữ của công ty. Gói của kỹ năng phải đang bật."""
    spec = DECLARED.get(inp.key)
    pack = next((pk.key for pk in registry.PACKS.values() if any(r[0] == INSTALL and r[1] == inp.key for r in pk.records)), None)
    if spec is None or pack is None:
        raise NotFound(f"Không có kỹ năng mặc định {inp.key}")
    if pack not in registry.always_on() and not await ctx.val("SELECT 1 FROM core.tenant_packs WHERE pack = %s", (pack,)):
        raise Invalid(f"Gói của kỹ năng {inp.key} chưa bật")
    n = await ctx.exec("""INSERT INTO core.ai_skills (key, version, title, when_to_use, roles, instructions, tools, status, source, pack)
                          SELECT %s, 1, %s, %s, %s, %s, %s, 'active', 'pack', %s WHERE NOT EXISTS (SELECT 1 FROM core.ai_skills WHERE key = %s)""",
                       (spec.key, spec.title, spec.when_to_use, list(spec.roles), spec.instructions, list(spec.tools), pack, spec.key))
    return {"key": spec.key, "added": bool(n)}


@m.query("skill_options", resource="core.ai_skills", title="Kỹ năng trợ lý")
async def skill_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all("SELECT DISTINCT ON (key) key AS value, title AS label FROM core.ai_skills ORDER BY key, version DESC"))


@m.query("skills", resource="core.ai_skills", title="Kỹ năng trợ lý")
async def skills(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Mỗi khoá một dòng: bản đang dùng (nếu có) + bản nháp đang chờ (nếu có), vai thấy, công cụ, người soạn / duyệt."""
    rows = await ctx.all(
        """SELECT k.key AS id, k.key, coalesce(d.title, a.title, r.title) AS title, coalesce(a.when_to_use, d.when_to_use, r.when_to_use) AS when_to_use,
                  CASE WHEN a.key IS NOT NULL THEN 'active' WHEN d.key IS NOT NULL THEN 'draft' ELSE 'retired' END AS status,
                  a.version AS active_version, d.version AS draft_version, (d.submitted_at IS NOT NULL) AS submitted,
                  array_to_string(coalesce(a.roles, d.roles, r.roles), ', ') AS roles, array_to_string(coalesce(a.tools, d.tools, r.tools), ', ') AS tools,
                  coalesce(a.source, d.source, r.source) AS source, ua.name AS author, uv.name AS approver,
                  length(coalesce(a.instructions, d.instructions, r.instructions)) AS chars
           FROM (SELECT DISTINCT key FROM core.ai_skills) k
           LEFT JOIN core.ai_skills a ON a.key = k.key AND a.status = 'active'
           LEFT JOIN core.ai_skills d ON d.key = k.key AND d.status = 'draft'
           LEFT JOIN LATERAL (SELECT * FROM core.ai_skills x WHERE x.key = k.key ORDER BY x.version DESC LIMIT 1) r ON true
           LEFT JOIN core.users ua ON ua.id = coalesce(d.author_id, a.author_id)
           LEFT JOIN core.users uv ON uv.id = a.approved_by
           ORDER BY k.key""")
    for r in rows:
        r["draft_state"] = ("submitted" if r["submitted"] else "editing") if r["draft_version"] else ""
    return ui.table(rows, [ui.col("title", "Kỹ năng", width=220), ui.col("when_to_use", "Khi dùng", width=280),
                           ui.col("status", "Trạng thái", "badge", labels=STATUS, tones={"active": "green", "draft": "orange", "retired": "gray"}),
                           ui.col("active_version", "Bản đang dùng", "number"),
                           ui.col("draft_state", "Bản nháp", "badge", labels={"submitted": "Chờ duyệt", "editing": "Đang soạn"},
                                  tones={"submitted": "orange", "editing": "gray"}),
                           ui.col("roles", "Vai thấy", width=160), ui.col("tools", "Công cụ", width=220), ui.col("chars", "Độ dài (ký tự)", "number"),
                           ui.col("source", "Nguồn", "badge", labels=SOURCE), ui.col("author", "Người soạn"), ui.col("approver", "Người duyệt")],
                    title="Kỹ năng trợ lý", limit=500, empty="Chưa có kỹ năng nào — bật gói có kỹ năng mặc định hoặc bấm “Soạn / sửa kỹ năng trợ lý”.",
                    note=f"Mỗi lượt trợ lý chỉ đọc tên + khi dùng của tối đa {LIST_MAX} kỹ năng đang dùng cho vai người hỏi; hướng dẫn chỉ nạp khi cần. "
                         "Kỹ năng không cấp quyền: công cụ người hỏi không có quyền thì trợ lý vẫn không dùng được.")


@m.query("skill", resource="core.ai_skills", title="Một kỹ năng trợ lý")
async def skill(ctx: Ctx, inp: SkillKey) -> dict[str, Any]:
    rows = await ctx.all("SELECT s.*, u.name AS author, v.name AS approver FROM core.ai_skills s LEFT JOIN core.users u ON u.id = s.author_id "
                         "LEFT JOIN core.users v ON v.id = s.approved_by WHERE s.key = %s AND s.status <> 'retired' ORDER BY s.version DESC", (inp.key,))
    if not rows:
        raise NotFound("Không có kỹ năng này (hoặc đã ngừng)")
    sections = [ui.section(f"{STATUS[r['status']]} — phiên bản {r['version']}", [
        ui.field("Tên", r["title"]), ui.field("Khi dùng", r["when_to_use"]), ui.field("Vai thấy", ", ".join(r["roles"])),
        ui.field("Công cụ", ", ".join(r["tools"]) or "—"), ui.field("Nguồn", SOURCE[r["source"]]),
        ui.field("Người soạn", r["author"] or "Gói (review trong repo)"), ui.field("Người duyệt", r["approver"] or "—"),
        ui.field("Đã gửi duyệt", "Có" if r["submitted_at"] else "Chưa") if r["status"] == "draft" else ui.field("Duyệt lúc", r["approved_at"], "datetime"),
    ], text=r["instructions"]) for r in rows]
    return ui.detail(rows[0]["title"], sections, subtitle=inp.key)


async def for_role(conn: Any, role: str) -> list[dict[str, Any]]:
    """Kỹ năng ĐANG DÙNG cho vai (trợ lý đọc mỗi lượt bằng giao dịch của người hỏi — RLS cách ly công ty). Thứ tự xác định (giữ bộ đệm prompt)."""
    cur = await conn.execute("""SELECT key, version, title, when_to_use, instructions, tools FROM core.ai_skills
                                WHERE status = 'active' AND %s = ANY(roles) ORDER BY key LIMIT %s""", (role, LIST_MAX * 3))
    return list(await cur.fetchall())

