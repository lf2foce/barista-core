"""Lead (khách × thứ khách quan tâm): tra danh mục qua gói ngành, thêm lead, danh sách, chăm sóc, nhu cầu, chặng, chuyển người, việc hôm nay."""

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from psycopg import errors as pg
from psycopg.types.json import Jsonb

from barista.api import (
    Conflict,
    Ctx,
    DateIn,
    Day,
    ImportKey,
    Input,
    Invalid,
    Money,
    NoInput,
    NotFound,
    OptionalPhone,
    SearchTerms,
    extra_fields,
    field,
    today,
    ui,
    zone,
)
from barista.api.lead_labels import CONTACT_KIND, SOURCES
from barista.modules.crm import (
    ACTIVITY,
    OUTCOMES,
    STAGE_TONES,
    STAGES,
    _set_owner,
    _stage_event,
    interest_text,
    m,
    reveal,
)
from barista.modules.crm.identity import _customer_of, _identifiers


class Offering(Input):
    code: str


async def _offering(ctx: Ctx, code: str) -> dict[str, Any] | None:
    """Tra thứ khách quan tâm qua gói ngành đang bật. Chưa gói nào cung cấp danh mục ⇒ giữ chữ người nhập (id trống)."""
    if not code.strip():
        return None
    hits = await ctx.plugs("crm.offering", system=True, code=code)
    if hits:
        return hits[0]
    if await ctx.plugged("crm.offering"):
        raise NotFound(f"Không có [[offering|l]] {code} trong danh mục (mã hoặc bí danh)")
    return {"id": None, "code": code.strip().upper(), "name": code.strip()}


async def offering_names(ctx: Ctx, codes: Iterable[str | None]) -> dict[str, str]:
    """Mã → TÊN người đọc được (tên trong danh mục của gói ngành đang bật), cho chữ gửi KHÁCH (tin soạn sẵn…): khách không biết mã nội bộ
    (``GOI-A``, ``HSR``). Mã không có tên (không gói danh mục, mã lạ, tên trống / trùng mã) ⇒ KHÔNG có trong kết quả — chỗ gọi bỏ phần đó, không in mã."""
    out: dict[str, str] = {}
    for code in dict.fromkeys(c for c in codes if c and c.strip()):
        hit = (await ctx.plugs("crm.offering", system=True, code=code) or [None])[0]
        name = str((hit or {}).get("name") or "").strip()
        if name and name.lower() != code.strip().lower():
            out[code] = name
    return out


async def _campaign(ctx: Ctx, code: str, ad_code: str = "") -> dict[str, Any] | None:
    """Chiến dịch theo mã, nếu công ty dùng gói có chiến dịch. Mã sai ⇒ báo; không có gói đó ⇒ bỏ qua, lead vẫn vào. ``ad_code``: mẫu quảng cáo nguồn gửi
    kèm (0210) — mẫu lạ không chặn lead (``ad_id`` trống)."""
    if not code.strip():
        return None
    hits = await ctx.plugs("crm.campaign", system=True, code=code, ad_code=ad_code)
    if hits:
        return hits[0]
    if await ctx.plugged("crm.campaign"):
        raise NotFound(f"Không có chiến dịch {code}")
    return None


@m.query("offering_by_code", resource=None, title="Tra [[offering|l]] quan tâm", internal=True, agent="deny", callers=("leadflow", "mkt"), family="call_log",
         why="Đường nhập luật chia lead, chiến dịch và lead telesale của tổng đài (gói khai consumes(\"call_log\"): mã lấy từ tên chiến dịch gọi) (bên gọi đã qua quyền của nó, có thể không có quyền xem danh mục): tra MỘT mã qua gói ngành đang bật. "
             "Trả id, code, name (+ lead_routing nếu gói danh mục có cờ đang chia lead — leadflow dừng chia khi tắt) hoặc NotFound; chưa có danh mục thì id trống.")
async def offering_by_code(ctx: Ctx, inp: Offering) -> dict[str, Any] | None:
    return await _offering(ctx, inp.code)


@m.query("offering_options", resource=None, title="[[offering]] quan tâm")
async def offering_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [o for part in await ctx.plugs("crm.offering_options") for o in part]   # bằng quyền người xem: ai không được xem danh mục thì ô trống


m.extensible("lead", "Khách / lead")   # công ty tự thêm trường cho lead (0060): giá trị ở crm.leads.extra, lõi kiểm kiểu ở executor


class CreateLead(Input):
    name: str = field("Họ tên khách", example="Nguyễn Thị Lan", max_length=120)
    phone: OptionalPhone = field("SĐT", None, example="0100000123", help="Không có số thì điền email hoặc kênh khác")
    offering_code: str = field("[[offering]] quan tâm", "", options_query="crm.offering_options", example="HSR")
    source: Literal["ads", "telesales", "self", "referral", "event", "import"] = field("Nguồn", "self", options=SOURCES)
    note: str = field("Ghi chú", "", kind="textarea", max_length=4000)
    # rà form nhóm B 18/11/2026 (ADR 0225): 15 ô ⇒ 5 ô; mã chiến dịch / mẫu quảng cáo chọn theo [[offering|l]] đã chọn (không gõ mã); còn lại gấp lại
    campaign_code: str = field("Chiến dịch", "", example="FB-T10", help="Lead từ quảng cáo — để tính chi phí / lead", group="Lead từ quảng cáo",
                               aliases=("Mã chiến dịch",), options_query="mkt.campaign_options", options_with=("offering_code",))
    ad_code: str = field("Mẫu quảng cáo", "", max_length=40, example="120201234567890", group="Lead từ quảng cáo", aliases=("Mã mẫu quảng cáo",),
                         help="Mẫu quảng cáo khách bấm (nếu biết) — để biết mẫu nào ra khách thật",
                         options_query="mkt.ad_options", options_with=("campaign_code",))
    budget_vnd: Money | None = field("Ngân sách", None, ge=0, example="4 tỷ", group="Thêm chi tiết")
    wishes: str = field("Mong muốn", "", max_length=300, example="cần tư vấn cuối tuần", help="Điều khách nói về nhu cầu", group="Thêm chi tiết")
    birthday: Day | None = field("Sinh nhật", None, example="15/11/1988", group="Thêm chi tiết")
    email: str = field("Email", "", kind="email", max_length=200, group="Thêm chi tiết")
    contact_kind: Literal["", "zalo", "facebook", "other"] = field("Kênh liên hệ khác", "", options=CONTACT_KIND, group="Thêm chi tiết")
    contact_value: str = field("Mã trên kênh đó", "", max_length=200, group="Thêm chi tiết",
                               help="Mã / số của khách trên kênh, không dùng tên hiển thị vì có thể trùng người khác")
    captured_on: Day | None = field("Ngày khách để lại thông tin", None, group="Thêm chi tiết", placeholder="Hôm nay",
                                    help="Lead nhập muộn / từ file: ghi ngày thật để số marketing tính đúng ngày (trống = hôm nay)")
    owner_email: str = field("Giao cho", "", options_query="admin.member_options", group="Thêm chi tiết", aliases=("Giao cho (email)",),
                             placeholder="Chính tôi", help="Trống = chính bạn. Giao cho người khác cần quyền quản lý khách hàng.")
    extra: dict[str, Any] = field("Thông tin riêng của công ty", {}, hidden=True, agent=False)   # trường công ty tự thêm — form / trợ lý gửi ô x_<khoá>


async def _leads_existing(ctx: Ctx, keys: list[tuple[str, ...]]) -> dict[tuple[str, ...], dict[str, Any]]:
    """Nhập Excel nhận ra lead đã có (0226): cùng khách (SĐT) + cùng [[offering|l]] quan tâm — lệnh sẽ báo "đã có lead" cho đúng cặp này. Chỉ lead người nhập thấy."""
    rows = await ctx.all("""SELECT c.phone, coalesce(l.offering_code, '') AS offering_code, c.name FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id
                            WHERE c.phone = ANY(%s)""", ([k[0] for k in keys],))
    return {(r["phone"].lower(), r["offering_code"].lower()): {"name": r["name"]} for r in rows}


@m.command("create_lead",
           purpose="Đưa một khách mới vào danh sách chăm sóc, có người phụ trách rõ ràng, để không bỏ sót khách đã hỏi.", reversible="Chuyển khách sang chặng Mất nếu thêm nhầm (lead không xoá hẳn được)",
           resource="crm", title="Thêm khách / lead", importable=True, import_key=ImportKey(("phone", "offering_code"), _leads_existing),
           extra="lead")
async def create_lead(ctx: Ctx, inp: CreateLead) -> dict[str, Any]:
    owner = ctx.p.user_id
    if inp.owner_email and inp.owner_email.lower() != ctx.p.email:
        ctx.require("crm", "manage")
        owner = await ctx.val(
            "SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()"
            " WHERE u.email = %s AND mb.active", (inp.owner_email.lower(),))
        if owner is None:
            raise NotFound(f"Không có người {inp.owner_email} trong công ty")
    if owner is None:
        raise Invalid("Lead cần người phụ trách")
    contacts = _identifiers(inp.phone, inp.email, inp.contact_kind, inp.contact_value)
    if not contacts:
        raise Invalid("Cần ít nhất một cách liên hệ: SĐT, email hoặc kênh khác")
    team = await ctx.home_team(owner)
    offering = await _offering(ctx, inp.offering_code)
    campaign = await _campaign(ctx, inp.campaign_code, inp.ad_code)

    party = await ctx.call_system("parties.ensure_person", name=inp.name, identifiers=contacts, source="crm")
    found = await _customer_of(ctx, party["party_id"], inp.phone)
    customer_id = found["id"] if found else None
    if customer_id is None:
        try:
            async with ctx.conn.transaction():
                customer_id = await ctx.val(
                    """INSERT INTO crm.customers (name, phone, email, birthday, owner_id, team_id, note, party_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (inp.name, inp.phone, inp.email, inp.birthday, owner, team, inp.note, party["party_id"]))
        except pg.UniqueViolation as e:
            raise Conflict("Khách này đã là khách của người khác trong công ty — báo trưởng nhóm nếu cần chuyển khách") from e
    interest = {k: v for k, v in {"budget_vnd": inp.budget_vnd, "wishes": inp.wishes.strip()}.items() if v not in (None, "")}
    try:
        async with ctx.conn.transaction():
            lead_id = await ctx.val(
                """INSERT INTO crm.leads (customer_id, offering_id, offering_code, source, campaign_id, ad_id, interest, extra, owner_id, team_id,
                                          next_follow_up, loaded_at, captured_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN NULL ELSE core.today() END, CASE WHEN %s THEN now() END, %s) RETURNING id""",
                (customer_id, offering and offering["id"], offering["code"] if offering else "", inp.source,
                 campaign and campaign["id"], campaign and campaign.get("ad_id"), Jsonb(interest), Jsonb(inp.extra), owner, team, ctx.historical, ctx.historical,   # nạp lịch sử (0185): không hẹn "gọi lại hôm nay"
                 datetime.combine(inp.captured_on, datetime.min.time().replace(hour=12), tzinfo=zone()) if inp.captured_on else None))
    except pg.UniqueViolation as e:
        raise Conflict("Khách này đã có lead cho [[offering|l]] này") from e
    await _stage_event(ctx, lead_id, "created", None, "lead", data={"source": inp.source})
    ctx.emit("crm.lead_created", lead_id=lead_id, campaign_id=campaign and campaign["id"], source=inp.source,
             offering_code=offering["code"] if offering else "")
    return {"lead_id": lead_id, "customer_id": customer_id,
            "subject": {"label": f"{inp.name} · {offering['code'] if offering else 'chưa chọn [[offering|l]]'}",
                        "open": {"query": "crm.journey", "input": {"lead_id": str(lead_id)}}}}


WHO_FILTERS = "Theo đội / người"   # gấp sẵn: sale chỉ thấy khách của mình, không cần; trưởng nhóm mở ra để xem từng người


class LeadFilter(Input):
    stage: str = field("Chặng", "open", options={"open": "Đang theo", "": "Tất cả", **STAGES})
    q: str = field("Tìm (tên / SĐT)", "")
    team_code: str = field("Đội / khu vực", "", options_query="admin.team_options", group=WHO_FILTERS,
                           help="Mã hoặc tên (vd KV-MN, \"Miền Nam\"); gồm cả các đội con (khu vực ⇒ mọi khối, team bên dưới); trống = mọi đội trong phạm vi bạn xem")
    owner: str = field("Người phụ trách", "", options_query="admin.member_options", group=WHO_FILTERS,
                       help="Trống = mọi người trong phạm vi bạn xem (trưởng nhóm: cả đội) — chọn một người để xem riêng khách của họ")


@m.query("leads", resource="crm", title="Khách & lead")
async def leads(ctx: Ctx, inp: LeadFilter) -> dict[str, Any]:
    team = inp.team_code.strip()
    if team and not await ctx.val("SELECT 1 FROM core.teams WHERE upper(code) = upper(%s) OR name ILIKE '%%' || %s || '%%' LIMIT 1", (team, team)):
        raise NotFound(f"Không có đội / khu vực “{team}” — xem mã trong danh sách đội")   # không trả 0 khách như thể hết khách
    # Lọc + sắp + LIMIT trên crm.leads TRƯỚC, nối tên khách / người / đội SAU (N11 — F6): dưới RLS planner ước core.users / core.teams 1 dòng
    # nên nối trước thì so từng lead với từng người. Lead thấy được ⇒ khách của nó thấy được (chính sách crm.customers) ⇒ nối sau không rơi dòng.
    from barista.modules.crm import idle  # nạp muộn: giữ thứ tự thao tác trong sổ đăng ký

    find = SearchTerms(inp.q)   # cùng cách khớp với ⌘K (0087): có dấu hay không, từng từ; SĐT khi gõ toàn số
    rows = await ctx.all(
        """WITH RECURSIVE tree AS (SELECT id FROM core.teams WHERE %(team)s <> ''
                                     AND (upper(code) = upper(%(team)s) OR name ILIKE '%%' || %(team)s || '%%')
                                   UNION SELECT t.id FROM core.teams t JOIN tree ON t.parent_id = tree.id),
           pick AS MATERIALIZED (
               SELECT l.id, l.customer_id, l.offering_code, l.stage, coalesce(l.pipeline_stage, l.stage) AS pipeline, l.source, l.interest, l.owner_id,
                      l.team_id, l.next_follow_up,
                      l.last_activity_at, l.created_at, l.captured_at, l.pipeline_stage AS pipeline_key,
                      (l.next_follow_up IS NOT NULL AND l.next_follow_up <= core.today() AND l.stage IN ('lead','prospect')) AS due,
                      -- ô đếm trên CẢ tập đã lọc (cửa sổ tính trước LIMIT), không phải 1.000 dòng hiển thị
                      count(*) OVER () AS n_all,
                      count(*) FILTER (WHERE l.next_follow_up IS NOT NULL AND l.next_follow_up <= core.today() AND l.stage IN ('lead','prospect')) OVER () AS n_due
               FROM crm.leads l
               WHERE (%(st)s = '' OR (%(st)s = 'open' AND l.stage IN ('lead', 'prospect')) OR l.stage = %(st)s)
                 AND (%(q)s = '' OR l.customer_id IN (SELECT c.id FROM crm.customers c WHERE """ + find.match("c.name", tel="c.phone") + """))
                 AND (%(team)s = '' OR l.team_id IN (SELECT id FROM tree))
                 AND (%(own)s = '' OR l.owner_id = (SELECT u.id FROM core.users u WHERE lower(u.email) = lower(%(own)s)))
               ORDER BY due DESC, l.next_follow_up NULLS LAST, l.created_at DESC LIMIT 1000)
           SELECT l.id, c.name AS customer, c.phone, l.offering_code, l.stage, l.pipeline, l.source, l.interest, coalesce(u.name, 'Chưa chia') AS owner,
                  tm.name AS team, l.next_follow_up, l.last_activity_at, l.created_at, l.due, l.n_all, l.n_due,
                  -- nằm im (ADR 0246): chỉ cho 1.000 dòng đã chọn, chỉ lead đang theo ở chặng có ngưỡng — công thức một nguồn ở crm/idle.py
                  st.idle_days AS idle_limit, CASE WHEN st.idle_days IS NOT NULL THEN """ + idle.IDLE_FOR + """ END AS idle_for
           FROM pick l JOIN crm.customers c ON c.id = l.customer_id LEFT JOIN core.users u ON u.id = l.owner_id
                LEFT JOIN core.teams tm ON tm.id = l.team_id
                LEFT JOIN crm.stages st ON st.key = l.pipeline_key AND st.idle_days IS NOT NULL AND l.stage IN ('lead', 'prospect')
           ORDER BY l.due DESC, l.next_follow_up NULLS LAST, l.created_at DESC""",
        {**find.params, "st": inp.stage, "q": find.q, "team": team, "own": inp.owner.strip()})
    total, due = (int(rows[0]["n_all"]), int(rows[0]["n_due"])) if rows else (0, 0)
    await reveal.mask_rows(ctx, rows)   # lead chờ bấm nhận theo luật có che ⇒ số đã che (0214)
    for r in rows:
        r["interest_text"] = interest_text(r.pop("interest"))
        r["due_flag"] = "Đến hạn gọi" if r.pop("due") else ""
        limit_days, days = r.pop("idle_limit"), r.pop("idle_for")
        r["idle_flag"] = idle.flag(days, limit_days)
        r["idle_days"] = days if r["idle_flag"] else None
        del r["n_all"], r["n_due"]
    from barista.modules.crm import stages as pipeline  # nạp muộn: giữ thứ tự thao tác trong sổ đăng ký

    names, colors = await pipeline.labels(ctx), await pipeline.tones(ctx)
    stage_list = await pipeline.stage_rows(ctx)
    lost_keys = ["lost", *(r["key"] for r in stage_list if r["phase"] == "lost")]
    idle_n = sum(1 for r in rows if r["idle_flag"])
    idle_kpi = [ui.kpi("Nằm im quá lâu", idle_n, tone="orange" if idle_n else None)] if any(r["idle_days"] for r in stage_list) else []
    return ui.table(rows, [
        ui.col("due_flag", "", "badge", tones={"Đến hạn gọi": "orange"}),
        ui.col("idle_flag", "", "badge", tones={f: "orange" for f in {r["idle_flag"] for r in rows if r["idle_flag"]}}),
        ui.col("customer", "Khách"), ui.col("phone", "SĐT", "phone"), ui.col("offering_code", "[[offering]]"),
        ui.col("pipeline", "Chặng", "badge", labels=names, tones=colors), ui.col("stage", "Giai đoạn", "badge", labels=STAGES, tones=STAGE_TONES),
        ui.col("interest_text", "Nhu cầu"),
        ui.col("source", "Nguồn", "badge", labels=SOURCES), ui.col("owner", "Phụ trách"), ui.col("team", "Đội"),
        ui.col("next_follow_up", "Hẹn gọi", "date"), ui.col("last_activity_at", "Chăm sóc gần nhất", "datetime"),
    ], title="Khách & lead", limit=1000, kpis=[ui.kpi("Đang theo" if inp.stage == "open" else "Số lead", total), ui.kpi("Đến hạn gọi", due, tone="orange" if due else None),
                                                          *idle_kpi],
        # Kanban theo chặng của công ty: kéo = crm.set_stage (chặng giai đoạn "Mất" cần lý do ⇒ form; "Giao dịch" chỉ do bước tạo giao dịch — server từ chối)
        views=ui.views(board=ui.board_view("pipeline", "customer", "offering_code",
                                           move={"command": "crm.set_stage", "id_key": "lead_id", "column_key": "stage", "form_columns": lost_keys}),
                       calendar=ui.calendar_view("next_follow_up", "customer", "owner")))


class LogActivity(Input):
    lead_id: UUID = field("Lead", hidden=True)
    kind: Literal["call", "meet", "zalo", "email", "visit", "send_profile", "note"] = field("Hình thức", "call", options=ACTIVITY)
    outcome: Literal["", "connected", "no_answer", "busy", "wrong_number"] = field(
        "Kết quả cuộc gọi", "", options=OUTCOMES, help="Chỉ cho hình thức Gọi điện — để đếm tỷ lệ gọi được")
    note: str = field("Nội dung", "", kind="textarea", max_length=4000)
    next_follow_up: Day | None = field("Hẹn chăm sóc lại", None)


@m.command("log_activity", resource="crm", title="Ghi chăm sóc", agent="allow")  # ghi chú, không đổi tiền / quyền
async def log_activity(ctx: Ctx, inp: LogActivity) -> dict[str, Any]:
    if not await ctx.val("SELECT 1 FROM crm.leads WHERE id = %s", (inp.lead_id,)):
        raise NotFound("Không thấy lead")
    if inp.outcome and inp.kind != "call":
        raise Invalid("Kết quả cuộc gọi chỉ dùng cho hình thức Gọi điện")
    await claim_if_invited(ctx, inp.lead_id)
    await ctx.exec("INSERT INTO crm.activities (lead_id, kind, outcome, note, by_user) VALUES (%s, %s, %s, %s, %s)",
                   (inp.lead_id, inp.kind, inp.outcome, inp.note, ctx.p.user_id))
    # không nghe máy / máy bận mà chưa hẹn ⇒ tự hẹn gọi lại sau 4 giờ trong ngày (quá giờ ⇒ ngày mai) — học từ bds
    follow = inp.next_follow_up
    if follow is None and inp.outcome in ("no_answer", "busy"):
        follow = today()
    # "Đã liên hệ" có MỘT định nghĩa (chia lead, trần lead chưa gọi, hạn gọi, thẻ buổi sáng): mọi hình thức trừ ghi chú nội bộ
    was = await ctx.val("UPDATE crm.leads SET last_activity_at = CASE WHEN %s THEN last_activity_at ELSE now() END, next_follow_up = %s,"
                        " updated_at = now() WHERE id = %s RETURNING old.next_follow_up", (inp.kind == "note", follow, inp.lead_id))
    await ctx.track("crm.lead", inp.lead_id, "next_follow_up", was, follow)   # lịch sử thay đổi (ADR 0246)
    moved = await _first_contact_moves_on(ctx, inp) if inp.kind != "note" else None
    return {"lead_id": inp.lead_id, **({"stage_moved_to": moved} if moved else {})}


async def _first_contact_moves_on(ctx: Ctx, inp: LogActivity) -> str | None:
    """Rà 23/11/2026: 342 / 437 lead ở chặng "Mới" đã được chăm sóc mà chặng không đổi ⇒ phễu vô nghĩa. Lần ĐẦU nói chuyện được với khách (gọi nghe máy, gặp,
    dẫn đi xem) là SỰ THẬT ghi được, không phải phán đoán ⇒ lead đang ở chặng ĐẦU TIÊN của công ty thì sang chặng kế (theo thứ tự công ty đặt) qua đúng lệnh
    chuyển chặng (lịch sử chặng, sự kiện). Chặng khác, chặng do người đặt sau đó: không đụng. Trả tên chặng mới (hiện cho người ghi) hoặc None."""
    if not ((inp.kind == "call" and inp.outcome == "connected") or inp.kind in ("meet", "visit")):
        return None
    stages = await ctx.all("SELECT key, label, phase, sort FROM crm.stages WHERE active AND phase IN ('lead', 'prospect') ORDER BY sort, key")
    if len(stages) < 2:
        return None
    current = await ctx.val("SELECT pipeline_stage FROM crm.leads WHERE id = %s", (inp.lead_id,))
    if current != stages[0]["key"]:
        return None
    from barista.modules.crm.stages import move_block  # nạp muộn: giữ thứ tự thao tác trong sổ đăng ký
    if await move_block(ctx, current, stages[1]["key"]):   # công ty khai chuyển chặng (ADR 0264) mà cặp này chưa được phép ⇒ không tự chuyển
        return None
    await ctx.call("crm.set_stage", lead_id=str(inp.lead_id), stage=stages[1]["key"])
    return str(stages[1]["label"])


async def invited(ctx: Ctx, lead_ids: list[UUID]) -> dict[str, Any]:
    """Lead (trong ``lead_ids`` — bên gọi đã đọc qua RLS) người xem đang được MỜI nhận (ADR 0264): {lead_id: hạn lời mời}."""
    if not lead_ids or ctx.p.user_id is None:
        return {}
    got: dict[str, Any] = (await ctx.call_system("leadflow.invite_for", lead_ids=lead_ids, user_id=ctx.p.user_id))["invited"]
    return got


async def claim_if_invited(ctx: Ctx, lead_id: UUID) -> None:
    """"Ai cập nhật trước thì giữ" (ADR 0264): người được mời ghi chăm sóc / đổi chặng lead chưa ai giữ ⇒ nhận lead TRƯỚC (đúng lệnh ``leadflow.claim``, quyền
    của chính họ — trợ lý thì thành thẻ đề xuất), rồi mới ghi. Lead đã có người phụ trách ⇒ không làm gì."""
    row = await ctx.one("SELECT id, owner_id FROM crm.leads WHERE id = %s", (lead_id,))
    if row is None or row["owner_id"] is not None:
        return
    if str(row["id"]) in await invited(ctx, [row["id"]]):
        await ctx.call("leadflow.claim", lead_id=str(row["id"]))


class LeadRef(Input):
    lead_id: UUID = field("Lead", hidden=True)


class UpdateInterest(Input):
    lead_id: UUID = field("Lead", hidden=True)
    budget_vnd: Money | None = field("Ngân sách", None, ge=0, example="5 tỷ")
    wishes: str = field("Mong muốn", "", max_length=300, example="muốn gặp cuối tuần, cần tư vấn thêm",
                        help="Điều khách nói về nhu cầu")


@m.command("update_interest",
           subject=("crm.lead", "lead_id"), purpose="Ghi lại nhu cầu mới khách vừa nói sau buổi gặp hoặc cuộc gọi, để người chăm tiếp thấy đúng nhu cầu hiện tại.", reversible="Chạy lại lệnh với nhu cầu cũ (dòng thời gian của khách ghi trước → sau)",
           resource="crm", title="Cập nhật nhu cầu khách", agent="propose")
async def update_interest(ctx: Ctx, inp: UpdateInterest) -> dict[str, Any]:
    """Sau buổi gặp / cuộc gọi: nhu cầu MỚI khách nói. Ô để trống = giữ nguyên. Ghi "trước → sau" vào dòng thời gian của khách để biết
    nhu cầu đã đổi thế nào."""
    return await _merge_interest(ctx, inp.lead_id, {"budget_vnd": inp.budget_vnd, "wishes": inp.wishes.strip()})


async def _merge_interest(ctx: Ctx, lead_id: UUID, patch: dict[str, Any]) -> dict[str, Any]:
    lead = await ctx.one("SELECT l.interest, c.name FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id WHERE l.id = %s",
                         (lead_id,))
    if lead is None:
        raise NotFound("Không thấy lead")
    old: dict[str, Any] = dict(lead["interest"] or {})
    new = {**old, **{k: v for k, v in patch.items() if v not in (None, "")}}
    if new == old:
        raise Invalid("Không có gì mới để cập nhật")
    await ctx.exec("UPDATE crm.leads SET interest = %s, updated_at = now() WHERE id = %s", (Jsonb(new), lead_id))
    await ctx.track("crm.lead", lead_id, "budget", old.get("budget_vnd"), new.get("budget_vnd"))   # lịch sử thay đổi (ADR 0246)
    before, after = interest_text(old) or "chưa ghi", interest_text(new)
    await ctx.exec("INSERT INTO crm.activities (lead_id, kind, note, by_user) VALUES (%s, 'note', %s, %s)",
                   (lead_id, f"Nhu cầu: {before} → {after}", ctx.p.user_id))
    changes = [{"label": "Mong muốn", "before": str(old.get("wishes") or ""), "after": str(new["wishes"])}] if new.get("wishes") != old.get("wishes") else []
    return {"lead_id": lead_id, "summary": f"Nhu cầu {lead['name']}: {after}", "changes": changes}


class SetLeadFields(Input):
    lead_id: UUID = field("Lead", hidden=True)
    extra: dict[str, Any] = field("Thông tin riêng của công ty", {}, hidden=True, agent=False)


@m.command("set_lead_fields",
           subject=("crm.lead", "lead_id"), purpose="Cập nhật các thông tin riêng mà công ty theo dõi cho khách, để hồ sơ khách đủ và đúng.", reversible="Chạy lại lệnh với giá trị cũ",
           resource="crm", title="Cập nhật thông tin riêng của khách", agent="propose", extra="lead")
async def set_lead_fields(ctx: Ctx, inp: SetLeadFields) -> dict[str, Any]:
    """Ghi các trường RIÊNG công ty tự thêm cho khách (Quản trị › Trường riêng của công ty). Ô để trống = giữ nguyên. Ghi "trường: giá trị" vào dòng
    thời gian của khách."""
    lead = await ctx.one("SELECT l.extra, c.name FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id WHERE l.id = %s", (inp.lead_id,))
    if lead is None:
        raise NotFound("Không thấy lead")
    new = {**(lead["extra"] or {}), **inp.extra}
    if new == (lead["extra"] or {}):
        raise Invalid("Không có gì mới để cập nhật")
    # ghép ở DB (extra || phần mới): hai người sửa hai trường khác nhau cùng lúc không đè mất của nhau
    await ctx.exec("UPDATE crm.leads SET extra = extra || %s, updated_at = now() WHERE id = %s", (Jsonb(inp.extra), inp.lead_id))
    text = "; ".join(f"{label}: {value}" for label, value in extra_fields.shown(ctx.p.fields, "lead", inp.extra))
    await ctx.exec("INSERT INTO crm.activities (lead_id, kind, note, by_user) VALUES (%s, 'note', %s, %s)", (inp.lead_id, f"Thông tin riêng: {text}", ctx.p.user_id))
    return {"lead_id": inp.lead_id, "summary": f"{lead['name']}: {text}"}


class MergeInterest(Input):
    lead_id: UUID
    patch: dict[str, Any]


@m.command("merge_interest", resource="crm", title="Ghi nhu cầu khách theo ngành", internal=True, agent="deny")
async def merge_interest(ctx: Ctx, inp: MergeInterest) -> dict[str, Any]:
    """Gói ngành ghi thuộc tính nhu cầu của ngành mình qua ``call_as`` (quyền người gọi: lead không thấy ⇒ NotFound). Ghi trước → sau vào dòng thời gian."""
    return await _merge_interest(ctx, inp.lead_id, inp.patch)


@m.query("lead_need", resource="crm", title="Nhu cầu của một lead", internal=True, agent="deny")
async def lead_need(ctx: Ctx, inp: LeadRef) -> dict[str, Any]:
    """Gói ngành gợi ý hàng cho khách qua ``call_as`` (quyền người gọi: lead không thấy ⇒ NotFound). Trả nhu cầu, id thứ khách quan tâm, tên khách; không SĐT."""
    lead = await ctx.one("SELECT l.interest, l.offering_id, c.name FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id WHERE l.id = %s", (inp.lead_id,))
    if lead is None:
        raise NotFound("Không thấy lead")
    return {**lead, "interest_text": interest_text(lead["interest"])}


class SetStage(Input):
    lead_id: UUID = field("Lead", hidden=True)
    stage: str = field("Chặng", "prospect", options_query="crm.stage_options",
                       help="Chặng của công ty (Quan tâm, Đã gặp…) hoặc giai đoạn Lead / Tiềm năng / Mất")
    # ADR 0246: lý do mất CHỌN trong danh mục của công ty (Quản trị › Lý do mất khách); ghi chú ngắn tuỳ chọn. Gửi chữ không chọn mã (nhập Excel, trợ lý, gói
    # ngành) ⇒ máy đoán lý do từ chữ, chữ gốc giữ làm ghi chú
    lost_reason_key: str = field("Lý do mất", "", options_query="crm.lost_reason_options", help="Chỉ khi chuyển sang chặng Mất")
    lost_reason: str = field("Ghi chú lý do", "", max_length=500, help="Tuỳ chọn — vài chữ thêm cho lý do đã chọn")


# auto=True (ADR 0246): luật tự động của công ty được chuyển chặng (vd hẹn chăm sóc lại quá 7 ngày ⇒ "Không liên lạc được") — việc ghi rủi ro thấp, người
# đảo được bằng chính lệnh này; giai đoạn Giao dịch vẫn không đặt tay được, sang "Mất" vẫn cần lý do (giá trị cố định của luật)
@m.command("set_stage", resource="crm", title="Chuyển chặng", risk="low", auto=True)
async def set_stage(ctx: Ctx, inp: SetStage) -> dict[str, Any]:
    """Chặng là dữ liệu công ty (0153), giai đoạn do code ép: giai đoạn "Giao dịch" chỉ đến từ việc tạo giao dịch thật (module Giao dịch) — không
    đánh dấu tay; sang "Mất" cần lý do (chọn trong danh mục — ADR 0246). Chọn lại đúng chặng đang có ⇒ không ghi gì. Lý do của lần mất gần nhất ở lại
    trên lead khi lead quay lại theo; mỗi lần mất có trong lịch sử chặng."""
    from barista.modules.crm import lost_reasons  # nạp muộn: giữ thứ tự thao tác trong sổ đăng ký
    from barista.modules.crm.stages import move_block, resolve

    await claim_if_invited(ctx, inp.lead_id)   # người được mời đổi chặng trước ⇒ nhận lead (ADR 0264)
    phase, pipeline = await resolve(ctx, inp.stage)
    note = inp.lost_reason.strip()
    key, label = await lost_reasons.pick(ctx, inp.lost_reason_key, note) if phase == "lost" else (None, "")
    before = await ctx.one("SELECT stage, pipeline_stage FROM crm.leads WHERE id = %s AND stage <> 'deal' FOR UPDATE", (inp.lead_id,))
    if before is None:
        raise NotFound("Không thấy lead (hoặc lead đã thành giao dịch)")
    if before["stage"] == phase and before["pipeline_stage"] == pipeline and phase != "lost":
        return {"lead_id": inp.lead_id, "stage": phase, "pipeline_stage": pipeline, "unchanged": True}
    frm, to = before["pipeline_stage"] or before["stage"], pipeline or phase
    if frm != to and (why := await move_block(ctx, frm, to)):   # công ty khai cặp chuyển chặng được phép (ADR 0264) — kể cả luật tự động
        raise Invalid(why)
    if phase == "lost":
        await ctx.exec("UPDATE crm.leads SET stage = %s, pipeline_stage = %s, lost_reason = %s, lost_reason_key = %s, updated_at = now() WHERE id = %s",
                       (phase, pipeline, note, key, inp.lead_id))
    else:   # rời Mất: lý do lần mất gần nhất ở lại (ADR 0246 — không mất khi lead quay lại theo)
        await ctx.exec("UPDATE crm.leads SET stage = %s, pipeline_stage = %s, updated_at = now() WHERE id = %s", (phase, pipeline, inp.lead_id))
    lost = {"lost_reason_key": key, "lost_reason_label": label} if phase == "lost" else {}
    if before["stage"] != phase:
        await _stage_event(ctx, inp.lead_id, "stage", before["stage"], phase, note or label, data={"pipeline": pipeline, **lost})
    elif before["pipeline_stage"] != pipeline or lost:   # Mất → Mất (đổi chặng mất / ghi lại lý do): vẫn để lại dấu lý do trong lịch sử
        await _stage_event(ctx, inp.lead_id, "pipeline", before["pipeline_stage"], pipeline, note or label, data=lost)
    return {"lead_id": inp.lead_id, "stage": phase, "pipeline_stage": pipeline, **({"lost_reason_key": key} if phase == "lost" else {})}


class Assign(Input):
    lead_id: UUID = field("Lead", hidden=True)
    owner_email: str = field("Giao cho", options_query="admin.member_options")


@m.command("assign", resource="crm", action="manage", title="Chuyển người phụ trách")
async def assign(ctx: Ctx, inp: Assign) -> dict[str, Any]:
    """Trưởng nhóm chuyển trong đội mình (RLS chặn chuyển sang đội ngoài phạm vi); giám đốc chuyển toàn công ty. Trạng thái chia lead đi theo
    trong CÙNG giao dịch (hạn mới cho người mới — 0153). Chọn lại đúng người đang phụ trách ⇒ không đổi gì (không đặt lại đồng hồ hạn)."""
    owner = await ctx.val(
        "SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()"
        " WHERE u.email = %s AND mb.active", (inp.owner_email.lower(),))
    if owner is None:
        raise NotFound(f"Không có người {inp.owner_email}")
    lead = await ctx.one("SELECT owner_id, offering_code FROM crm.leads WHERE id = %s", (inp.lead_id,))   # quyền NGƯỜI GỌI (RLS)
    if lead is None:
        raise NotFound("Không thấy lead")
    if lead["owner_id"] == owner:
        return {"lead_id": inp.lead_id, "owner_id": owner, "unchanged": True}
    if why := (await ctx.call_system("leadflow.receiver_check", user_id=owner, offering_code=lead["offering_code"]))["why"]:
        raise Invalid(why)   # công tắc "chỉ thành viên" (0152): không giao lead của [[offering|l]] cho người không thuộc
    # trạng thái chia TRƯỚC (khoá dòng của nó trước dòng lead — cùng thứ tự với vòng quét), rồi mới ghi người phụ trách bằng quyền người gọi
    await ctx.call_system("leadflow.follow_owner", lead_id=inp.lead_id, owner_id=owner, by=ctx.p.user_id)
    await _set_owner(ctx, inp.lead_id, owner, reason="Chuyển tay")
    return {"lead_id": inp.lead_id, "owner_id": owner}


@m.query("reminders", resource="crm", title="Việc hôm nay")
async def reminders(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Một danh sách việc: khách đến hạn gọi lại, sinh nhật khách 7 ngày tới, đợt đóng tiền của khách 7 ngày tới."""
    rows: list[dict[str, Any]] = []
    follow = await ctx.all(
        """SELECT l.id, c.name, c.phone, l.offering_code, l.next_follow_up FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id
           WHERE l.stage IN ('lead', 'prospect') AND l.next_follow_up <= core.today() ORDER BY l.next_follow_up LIMIT 200""")
    await reveal.mask_rows(ctx, follow)   # 0214
    for r in follow:
        rows.append({"id": f"f-{r['id']}", "kind": "follow_up", "when": r["next_follow_up"], "customer": r["name"],
                     "phone": r["phone"], "detail": f"Gọi lại khách quan tâm {r['offering_code'] or '[[offering|l]]'}", "amount": None})
    bdays = await ctx.all(
            """WITH b AS (
                   SELECT c.id, c.name, c.phone, c.birthday, ARRAY(SELECT l.id FROM crm.leads l WHERE l.customer_id = c.id) AS lead_ids,
                          (c.birthday + make_interval(years => (extract(year FROM core.today()) - extract(year FROM c.birthday))::int))::date AS this_year
                   FROM crm.customers c WHERE c.birthday IS NOT NULL)
               SELECT * FROM (SELECT id, name, phone, birthday, lead_ids,
                                     CASE WHEN this_year < core.today() THEN (this_year + interval '1 year')::date ELSE this_year END AS next_bday
                              FROM b) x
               WHERE next_bday - %s::date <= 7""", (today(),))   # lọc 7 ngày trong SQL — không kéo mọi khách có ngày sinh về (N11)
    hidden = await reveal.gated(ctx, (i for r in bdays for i in r["lead_ids"]))
    for r in bdays:
        if r["lead_ids"] and all(str(i) in hidden for i in r["lead_ids"]):   # dòng theo KHÁCH: che khi mọi lead người xem thấy của khách đang che (0214)
            r["phone"] = reveal.mask_phone(r["phone"])
        if (r["next_bday"] - today()).days <= 7:
            rows.append({"id": f"b-{r['id']}", "kind": "birthday", "when": r["next_bday"], "customer": r["name"],
                         "phone": r["phone"], "detail": f"Sinh nhật ({r['birthday']:%d/%m})", "amount": None})
    for part in await ctx.plugs("crm.customer_due", days=7):
        for r in part:
            rows.append({"id": r["id"], "kind": "installment", "when": r["due_on"], "customer": r["customer"],
                         "phone": "", "detail": f"{r['detail']} — nhắc khách đóng tiền", "amount": r["amount_vnd"]})
    rows.sort(key=lambda r: r["when"])
    kinds = {"follow_up": "Gọi lại", "birthday": "Sinh nhật", "installment": "Đóng tiền"}
    return ui.table(rows, [
        ui.col("kind", "Việc", "badge", labels=kinds, tones={"follow_up": "orange", "birthday": "purple", "installment": "blue"}),
        ui.col("when", "Ngày", "date"), ui.col("customer", "Khách"), ui.col("phone", "SĐT", "phone"),
        ui.col("detail", "Nội dung", width=320), ui.col("amount", "Số tiền", "money"),
    ], title="Việc hôm nay", kpis=[ui.kpi(label, sum(1 for r in rows if r["kind"] == k)) for k, label in kinds.items()],
        empty="Hôm nay không có việc nhắc")


@m.query("lead_status", resource="crm", title="Trạng thái một lead (mã)", agent="allow")
async def lead_status(ctx: Ctx, inp: LeadRef) -> dict[str, Any]:
    """Trạng thái MỘT lead bằng mã, cho hệ ngoài gọi ngược sau webhook (0140): chặng, người phụ trách, đội, người (danh bạ) — chỉ mã + mã
    chặng / nguồn, không tên / SĐT khách. Ngoài phạm vi xem của người gọi (RLS) ⇒ không thấy."""
    row = await ctx.one(
        """SELECT l.id AS lead_id, c.party_id, l.customer_id, l.stage, l.owner_id, l.team_id, l.offering_code, l.source, l.created_at, l.updated_at
           FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id WHERE l.id = %s""", (inp.lead_id,))
    if row is None:
        raise NotFound("Không thấy lead (không có hoặc ngoài phạm vi xem)")
    return row


# ── Nguồn ngày cho luật tự động theo thời gian (ADR 0246) ──
# Chỗ cắm sẵn: khi crm có phép tính "lead nằm yên ở một chặng bao lâu" (ADR 0246 phần lead đứng yên), khai thêm MỘT nguồn ``stage_idle`` cùng
# hình (ngày = lần đổi chặng gần nhất trong crm.stage_events) — động cơ luật không đổi gì.
@m.date_source("follow_up_due", "Lead — hẹn chăm sóc lại", date_label="hẹn chăm sóc lại", resource="crm",
               fields={"source": "Nguồn", "offering_code": "[[offering]] quan tâm", "stage": "Giai đoạn", "pipeline": "Chặng", "team": "Đội",
                       "owner": "Người phụ trách"}, ids=("lead_id",), open=("crm.journey", {"lead_id": "lead_id"}),
               why="Động cơ luật tự động (flow, ADR 0246) xét luật theo ngày bằng quyền hệ thống: lead CÒN MỞ (giai đoạn lead / tiềm năng) có ngày hẹn "
                   "chăm sóc lại trong khoảng được hỏi — trả mã lead, ngày hẹn, người phụ trách (mã + tên), nguồn, sản phẩm quan tâm, giai đoạn / chặng, tên đội; "
                   "KHÔNG tên / SĐT khách. Hành động của luật chạy bằng quyền người đặt luật (nạp lại mỗi lần), không bằng quyền hệ thống")
async def follow_up_due(ctx: Ctx, inp: DateIn) -> list[dict[str, Any]]:
    return await ctx.all(
        """SELECT l.id::text AS ref, l.id AS lead_id, l.next_follow_up AS "on", l.owner_id, l.source, l.offering_code, l.stage,
                  l.pipeline_stage AS pipeline, t.name AS team, u.name AS owner
           FROM crm.leads l LEFT JOIN core.teams t ON t.id = l.team_id LEFT JOIN core.users u ON u.id = l.owner_id
           WHERE l.stage IN ('lead', 'prospect') AND l.next_follow_up BETWEEN %s AND %s ORDER BY l.next_follow_up, l.id LIMIT 2000""",
        (inp.since, inp.until))
