"""SỔ SÁCH & NGHĨA VỤ — gói năng lực "books" (quyết định 0063): công ty khách của dịch vụ kế toán, hoặc công ty có kế toán nội bộ.

* MISA (hoặc phần mềm kế toán khác) giữ SỔ; ở đây là VIỆC: kỳ sổ sách theo chặng, nghĩa vụ + hạn nộp, người phụ trách / soát xét, căn cứ đã nộp.
* Nghĩa vụ THEO LUẬT đến từ BỘ QUY ĐỊNH có phiên bản (``kernel/regulations.py``, quyết định 0067): khách chỉ khai dữ kiện (kỳ khai GTGT, có lao
  động…); luật / nghị định mới = thêm phiên bản, không sửa code. Mỗi nghĩa vụ của kỳ ghi phiên bản + căn cứ đã tính hạn. Nghĩa vụ kế toán tự
  thêm vẫn là dữ liệu của công ty.
* Kỳ sổ sách tháng M mở từ ngày 1 tháng M+1 bằng vòng quét (``open_due``): KHOÁ DUY NHẤT (công ty, tháng) ⇒ chạy lại / hai worker vẫn một kỳ;
  nghỉ vài ngày thì chạy bù mọi tháng SAU kỳ mới nhất (không lấp lỗ kỳ đã huỷ tay); không vượt kỳ cuối (ngừng dịch vụ).
* Kỳ là ĐỐI TƯỢNG quy trình bám (``books.period``) — gói Quy trình tự mở hồ sơ theo chặng (mẫu "Sổ sách kỳ" trong presets); mục "nghĩa vụ đã
  xử lý" là BỘ KIỂM đọc dữ kiện thật, không ai tự khai đạt.
"""

from datetime import date
from typing import Any, Literal
from uuid import UUID

from barista.api import Action as A
from barista.api import (
    CheckIn,
    Conflict,
    Ctx,
    Day,
    Denied,
    Desk,
    Flow,
    Input,
    Invalid,
    Line,
    Module,
    NoInput,
    NotFound,
    Open,
    Pack,
    Role,
    Screen,
    SubjectRef,
    Workspace,
    add_months,
    business_calendar,
    field,
    flagged,
    line,
    month_of,
    regulations,
    today,
    ui,
    zone,
)

BOOKS_FLOW = """# Thu chứng từ | Kế toán phụ trách | tối đa 10 ngày
- Sao kê ngân hàng đủ tháng [xác nhận: bookkeeper, reviewer]
- Hoá đơn mua vào / bán ra đủ [xác nhận: bookkeeper, reviewer]
> Gửi khách danh sách chứng từ còn thiếu | 2 ngày
# Hạch toán | Kế toán phụ trách | tối đa 7 ngày
- Đã hạch toán đủ chứng từ của kỳ trên phần mềm kế toán [xác nhận: bookkeeper, reviewer]
# Soát xét | Kế toán soát xét | tối đa 4 ngày
- Đã soát xét số liệu kỳ + tờ khai dự thảo [xác nhận: reviewer]
# Nộp & bàn giao | Kế toán phụ trách
- Nghĩa vụ của kỳ đã nộp / đã xử lý = books.filings_done
- Đã gửi khách báo cáo kỳ [xác nhận: bookkeeper, reviewer]"""

# Gói 'books' tự khai (0069) — kernel không biết tên gói; vai / đội / mẫu mặc định xem presets, màn & "cần chú ý" khai ngay dưới.
Pack("books", "Sổ sách & nghĩa vụ",
     "Kỳ sổ sách tháng tự mở theo chặng (thu chứng từ → hạch toán → soát xét → nộp), danh mục nghĩa vụ + hạn nộp, căn cứ đã nộp — phần mềm kế toán vẫn giữ sổ; ngành nào cũng dùng",
     roles={
         "bookkeeper": Role("Kế toán phụ trách", ai_tier="manager",
                            home=("books", ("periods", "filings", "client_requests")),   # gói dịch vụ nhiều khách góp phân hệ + màn của nó qua Pack.pins
                            picker=("Nhân viên", "Dịch vụ kế toán: làm kỳ sổ sách của khách mình phụ trách — mỗi khách một công ty riêng.", "Kỳ nào của tôi còn thiếu chứng từ?"),
                            grants=(("books", "all", "read write"), ("books.setup", "all", "read"),
                                    ("kb.common", "all", "read write"),
                                    ("papers", "own", "read write"), ("work", "own", "read write"), ("asks", "all", "read write"),
                                    ("flow", "all", "read write"), ("flow.templates", "all", "read"),
                                    ("talk", "all", "read write"), ("training", "own", "read write"),)),
         "reviewer": Role("Kế toán soát xét", ai_tier="manager",
                          home=("books", ("periods", "filings", "client_requests", "flow_board")),
                          picker=("Quản lý", "Dịch vụ kế toán: soát xét kỳ sổ sách, thấy tiến độ mọi khách mình là thành viên.", "Khách nào đang quá hạn nộp?"),
                          grants=(("books", "all", "read write"), ("books.setup", "all", "read write"),
                                  ("kb.common", "all", "read write"),
                                  ("papers", "own", "read write"), ("work", "own", "read write"), ("asks", "all", "read write"),
                                  ("flow", "all", "read write"), ("flow.templates", "all", "read"),
                                  ("talk", "all", "read write"), ("training", "own", "read write"),)),
     },
     grants=(("admin", "books", "all", "read write manage"), ("admin", "books.setup", "all", "read write"),
             ("director", "books", "all", "read"), ("director", "books.setup", "all", "read write"),),
     flows=(Flow("so_sach_ky", "Sổ sách kỳ (giả định)", "books.period", True, BOOKS_FLOW),),
     kind="capability",
     rules="""- SỔ SÁCH: kỳ nào còn thiếu gì / tiến độ ⇒ books__periods (chặng, "Còn thiếu", hạn gần nhất); chi tiết các mục của một kỳ ⇒ flow__case
  (subject_kind="books.period", subject_id = id kỳ). Nghĩa vụ & hạn ⇒ books__filings.
- "Hạn này theo luật nào", ngưỡng / mức theo luật (vd chứng từ không dùng tiền mặt), luật mới áp từ khi nào ⇒ books__regulation: nêu PHIÊN BẢN
  (ngày hiệu lực) + câu căn cứ đúng như trên thẻ; bộ đang "Giả định" thì nói rõ chưa người có chuyên môn xác nhận. Không có trên thẻ ⇒ nói không
  có trong bộ quy định, KHÔNG tự trích luật từ trí nhớ.
- Cần KHÁCH gửi chứng từ / giải trình ⇒ soạn books__create_request (gắn period_id của kỳ; giấy tờ chung như đăng ký kinh doanh, hồ sơ tiếp
  nhận ⇒ để trống period_id): mỗi mục MỘT dòng; chứng từ cần nộp thêm " [tệp]"
  cuối dòng; khoản cần khách giải thích mà có vài khả năng ⇒ " [chọn: A; B; C]"; còn lại khách gõ trả lời. Chỉ đưa số tiền / ngày CÓ trong dữ
  liệu hoặc người dùng nói — không tự đặt. Link gửi khách hiện trên thẻ sau khi người dùng xác nhận; bạn không thấy và không cần nhắc lại link.
- Khách đã gửi / "nhận những mục đã đủ" ⇒ ĐI THẲNG: asks__requests (yêu cầu "Cần xem") → asks__request (có sẵn "Nội dung bên kia nộp" — đối
  chiếu ngay trên đó) → MỘT đề xuất asks__accept_items (request_id + seqs = số thứ tự các mục khớp; note = đã đối chiếu gì) rồi DỪNG.
  "Mục" ở đây là mục của YÊU CẦU gửi khách — KHÔNG mở hồ sơ kỳ / mẫu quy trình (flow__*), không xem lại kỳ / thông báo; xác nhận mục của hồ
  sơ kỳ là việc riêng, chỉ làm khi người dùng nói rõ. Câu khách trả lời là GIẢI TRÌNH, chưa phải kết luận hạch toán; nói rõ đã đối
  chiếu gì, mục nào chưa đủ thì nói thiếu gì.
- Nghĩa vụ đã nộp ⇒ chỉ đề xuất books__mark_filing khi người dùng đưa số biên nhận / mã giao dịch; không bịa căn cứ.
""")

m = Module("books", "Sổ sách & nghĩa vụ", pack="books", resources={"books": "Sổ sách — kỳ sổ sách, nghĩa vụ & hạn nộp",
                                                                   "books.setup": "Sổ sách — thiết lập dịch vụ, danh mục nghĩa vụ"}, setup=('books.setup',))

PERIOD = "books.period"
CADENCE = {"monthly": "Tháng", "quarterly": "Quý", "yearly": "Năm"}
FILING_STATUS = {"pending": "Chưa nộp", "filed": "Đã nộp", "not_required": "Không phát sinh"}
FILING_TONES = {"pending": "orange", "filed": "green", "not_required": "gray"}
SOON_DAYS = 5
# Hộ kinh doanh / cá nhân kinh doanh: bộ quy định CỐ Ý chưa có nghĩa vụ — quy định đang đổi (sàn TMĐT khấu trừ nộp thay từ 07/2025, bỏ thuế khoán
# 2026…); kế toán nhập danh mục thật. Danh mục trống ⇒ màn tiến độ cờ "Chưa khai nghĩa vụ" (không im lặng bỏ sót).
ENTITY_KIND = {"company": "Doanh nghiệp", "household": "Hộ / cá nhân kinh doanh"}
SOURCE = {"rule": "Theo bộ quy định", "manual": "Kế toán tự khai"}
# không phải dữ kiện khách tự khai: loại hình đổi ở Thiết lập dịch vụ
PROFILE_FIXED = {"entity_kind"}
# hợp đồng mã với bộ quy định mặc định của sổ sách (0154): MỌI nghĩa vụ + dữ kiện trong file đều được áp (``obligations_for``); tham số chỉ để hiện trên màn
# Bộ quy định (chưa mã nào đọc) ⇒ ``optional``. Bộ khác có nghĩa vụ mà chọn cho sổ sách thì khai thêm ở đây — thiếu khai là test đỏ.
regulations.declare("vn-tax", optional=("non_cash_min_vnd", "quarterly_vat_revenue_max_vnd"), obligations=True)


def reg_options() -> dict[str, str]:
    """Chỉ bộ quy định có nghĩa vụ định kỳ — bộ chỉ mang tham số cho gói khác (bảo vệ dữ liệu cá nhân, 0112) không phải lựa chọn của sổ sách."""
    return {**{k: vs[-1].title for k, vs in regulations.packs().items() if vs[-1].obligations}, "": "Không theo bộ quy định — kế toán tự khai"}


def due_on(period_month: date, due_months: int, due_day: int) -> date:
    """Hạn = ngày ``due_day`` (kẹp theo độ dài tháng) của tháng thứ ``due_months`` sau tháng ``period_month`` (tháng cuối kỳ nghĩa vụ), chưa dời
    ngày nghỉ — hạn thật tính qua ``regulations.due_date`` với phiên bản quy định của kỳ."""
    return regulations.due_date(None, period_month, due_months, due_day)


def applies(cadence: str, period_month: date) -> bool:
    """Nghĩa vụ có kỳ kết thúc ở tháng này không (quý: tháng 3/6/9/12; năm: tháng 12)."""
    return cadence == "monthly" or (cadence == "quarterly" and period_month.month % 3 == 0) or (cadence == "yearly" and period_month.month == 12)


def filing_label(label: str, cadence: str, pm: date) -> str:
    return {"monthly": f"{label} tháng {pm:%m/%Y}", "quarterly": f"{label} quý {(pm.month - 1) // 3 + 1}/{pm.year}",
            "yearly": f"{label} năm {pm.year}"}[cadence]


async def _member(ctx: Ctx, email: str) -> UUID | None:
    if not email.strip():
        return None
    uid = await ctx.val("SELECT u.id FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.tenant_id = core.tenant()"
                        " WHERE u.email = %s AND mb.active", (email.strip().lower(),))
    if uid is None:
        raise NotFound(f"{email} không phải thành viên đang hoạt động của công ty này")
    return uid


async def _active(ctx: Ctx, user_id: UUID | None) -> bool:
    return user_id is not None and bool(await ctx.val("SELECT 1 FROM core.memberships WHERE user_id = %s AND active", (user_id,)))


# ── Thiết lập (quyền books.setup) ──

class Configure(Input):
    owner_email: str = field("Kế toán phụ trách", "", options_query="admin.member_options")
    reviewer_email: str = field("Người soát xét", "", options_query="admin.member_options")
    start_period: Day = field("Làm từ kỳ (tháng)", example="01/10/2026", help="Lấy theo tháng của ngày nhập")
    end_period: Day | None = field("Kỳ cuối (ngừng dịch vụ)", None, help="Trống = đang làm; kỳ sau tháng này không mở nữa")
    entity_kind: Literal["company", "household"] = field("Loại", "company", options=ENTITY_KIND)
    reg_pack: str | None = field("Bộ quy định", None, options=reg_options(),
                                 help="Nghĩa vụ + hạn theo luật lấy từ bộ quy định có phiên bản (đổi luật ⇒ tự theo từ ngày hiệu lực). Trống = giữ như cũ")


@m.command("configure", resource="books.setup", action="write", title="Thiết lập dịch vụ sổ sách", agent="deny")
async def configure(ctx: Ctx, inp: Configure) -> dict[str, Any]:
    """Người phụ trách / soát xét / kỳ bắt đầu / kỳ cuối. Đổi người phụ trách ⇒ các kỳ đang mở chuyển theo (hồ sơ + việc đi cùng)."""
    owner, reviewer = await _member(ctx, inp.owner_email), await _member(ctx, inp.reviewer_email)
    start, end = month_of(inp.start_period), month_of(inp.end_period) if inp.end_period else None
    if end and end < start:
        raise Invalid("Kỳ cuối phải từ kỳ bắt đầu trở đi")
    if inp.reg_pack and inp.reg_pack not in reg_options():
        raise Invalid(f"Không có bộ quy định {inp.reg_pack}")
    await ctx.exec(
        """INSERT INTO books.setup (owner_id, reviewer_id, start_period, end_period, entity_kind, reg_pack)
           VALUES (%s, %s, %s, %s, %s, coalesce(%s, 'vn-tax'))
           ON CONFLICT (tenant_id) DO UPDATE SET owner_id = EXCLUDED.owner_id, reviewer_id = EXCLUDED.reviewer_id,
               start_period = EXCLUDED.start_period, end_period = EXCLUDED.end_period, entity_kind = EXCLUDED.entity_kind,
               reg_pack = coalesce(%s, books.setup.reg_pack), updated_at = now()""",
        (owner, reviewer, start, end, inp.entity_kind, inp.reg_pack, inp.reg_pack))
    team = await ctx.home_team(owner)
    moved = await ctx.all("""UPDATE books.periods SET owner_id = %s, reviewer_id = %s, team_id = %s WHERE status = 'open'
                             AND (owner_id IS DISTINCT FROM %s OR reviewer_id IS DISTINCT FROM %s) RETURNING id""",
                          (owner, reviewer, team, owner, reviewer))
    for r in moved:
        ctx.emit("subject.changed", kind=PERIOD, id=r["id"])
    added = await _sync_rules(ctx, today())
    return {"start_period": start, "end_period": end, "moved": len(moved), "obligations_added": added,
            "summary": f"Sổ sách từ kỳ {start:%m/%Y}" + (f" tới {end:%m/%Y}" if end else "") + (f"; {len(moved)} kỳ đang mở đổi người" if moved else "")}


async def _version(ctx: Ctx, on: date) -> tuple[dict[str, Any] | None, regulations.Version | None]:
    setup = await ctx.one("SELECT * FROM books.setup")
    if setup is None or not setup["reg_pack"]:
        return setup, None
    return setup, regulations.version_on(setup["reg_pack"], on)


async def _sync_rules(ctx: Ctx, on: date) -> int:
    """Đưa nghĩa vụ THEO LUẬT về đúng phiên bản quy định có hiệu lực ngày ``on`` + hồ sơ khách: thêm / sửa nghĩa vụ áp dụng, ngừng nghĩa vụ không
    còn áp dụng. Không đụng nghĩa vụ kế toán tự khai (kể cả trùng mã). Bỏ bộ quy định ⇒ nghĩa vụ cũ thành tự khai (kế toán giữ, sửa được).
    Trả số nghĩa vụ vừa thêm."""
    setup, v = await _version(ctx, on)
    if setup is None:
        return 0
    if not setup["reg_pack"]:
        await ctx.exec("UPDATE books.obligations SET source = 'manual' WHERE source = 'rule'")
        return 0
    if v is None:
        return 0
    rules = v.obligations_for({**setup["profile"], "entity_kind": setup["entity_kind"]})
    added = 0
    for r in rules:
        row = await ctx.one(
            """INSERT INTO books.obligations (code, label, cadence, due_months, due_day, source, basis, rule_version, holiday_rule, holiday_calendar)
               VALUES (%s, %s, %s, %s, %s, 'rule', %s, %s, %s, %s)
               ON CONFLICT (tenant_id, code) DO UPDATE SET label = EXCLUDED.label, cadence = EXCLUDED.cadence, due_months = EXCLUDED.due_months,
                   due_day = EXCLUDED.due_day, active = true, basis = EXCLUDED.basis, rule_version = EXCLUDED.rule_version,
                   holiday_rule = EXCLUDED.holiday_rule, holiday_calendar = EXCLUDED.holiday_calendar
               WHERE books.obligations.source = 'rule' RETURNING (xmax = 0) AS inserted""",
            (r.code, r.label, r.cadence, r.months, r.day, r.basis, v.version, v.holiday_rule, v.calendar_id if v.holiday_rule != "keep" else ""))
        added += bool(row and row["inserted"])
    await ctx.exec("UPDATE books.obligations SET active = false WHERE source = 'rule' AND active AND NOT (code = ANY(%s))", ([r.code for r in rules],))
    return added


class ObligationIn(Input):
    code: str = field("Mã", max_length=30, example="GTGT")
    label: str = field("Tên nghĩa vụ", max_length=120, example="Tờ khai thuế GTGT")
    cadence: Literal["monthly", "quarterly", "yearly"] = field("Kỳ", "quarterly", options=CADENCE)
    due_months: int = field("Hạn sau kỳ (tháng)", 1, ge=0, le=12, help="Số tháng sau tháng cuối kỳ (0 = trong chính tháng đó)")
    due_day: int = field("Ngày hạn", 31, ge=1, le=31, help="31 = ngày cuối tháng")
    holiday_rule: Literal["keep", "next_workday"] = field("Hạn rơi vào ngày nghỉ", "keep", options=regulations.HOLIDAY_RULES,
                                                          help="Theo đúng văn bản / thoả thuận của nghĩa vụ này — không phải hạn nào cũng tự dời")
    holiday_calendar: str = field("Lịch ngày nghỉ", "vn-public", options={c: c for c in business_calendar.calendars()},
                                  help="Chỉ dùng khi dời sang ngày làm việc kế tiếp")
    active: bool = field("Đang áp dụng", True)


@m.command("upsert_obligation", resource="books.setup", action="write", title="Thêm / sửa nghĩa vụ")
async def upsert_obligation(ctx: Ctx, inp: ObligationIn) -> dict[str, Any]:
    """Áp cho các kỳ MỞ SAU khi sửa — kỳ đã mở giữ hạn đã tính (sửa từng nghĩa vụ của kỳ nếu cần). Nghĩa vụ THEO LUẬT không sửa ở đây."""
    code = inp.code.strip().upper().replace(" ", "-")
    if await ctx.val("SELECT 1 FROM books.obligations WHERE code = %s AND source = 'rule'", (code,)):
        raise Invalid(f"“{code}” theo bộ quy định — khai dữ kiện ở Sổ sách › Bộ quy định (vd kỳ khai tháng / quý) hoặc chọn \"kế toán tự khai\" "
                      "ở Thiết lập dịch vụ")
    cal = inp.holiday_calendar if inp.holiday_rule == "next_workday" else ""
    if cal and cal not in business_calendar.calendars():
        raise Invalid(f"Không có lịch ngày nghỉ {cal}")
    await ctx.exec(
        """INSERT INTO books.obligations (code, label, cadence, due_months, due_day, active, holiday_rule, holiday_calendar)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (tenant_id, code) DO UPDATE SET label = EXCLUDED.label, cadence = EXCLUDED.cadence, due_months = EXCLUDED.due_months,
               due_day = EXCLUDED.due_day, active = EXCLUDED.active, holiday_rule = EXCLUDED.holiday_rule, holiday_calendar = EXCLUDED.holiday_calendar""",
        (code, inp.label, inp.cadence, inp.due_months, inp.due_day, inp.active, inp.holiday_rule, cal))
    return {"code": code, "summary": f"{inp.label}: theo {CADENCE[inp.cadence].lower()}, hạn ngày {inp.due_day} sau {inp.due_months} tháng"}


# ── Theo luật mới (vòng quét mỗi ngày một lần / công ty) ──

class SyncRules(Input):
    as_of: Day | None = field("Tính tới ngày", None, hidden=True)


@m.command("sync_rules", resource="books", title="Áp phiên bản quy định mới", internal=True,
           sweep="SELECT tenant_id FROM books.setup WHERE reg_pack <> '' AND (rules_synced_on IS NULL OR rules_synced_on < core.today_of(tenant_id))")
async def sync_rules(ctx: Ctx, inp: SyncRules) -> dict[str, int]:
    """Phiên bản quy định mới có hiệu lực GIỮA hai kỳ ⇒ danh mục nghĩa vụ đổi ngay (không đợi mở kỳ sau) + báo người phụ trách / soát xét. Kỳ đang
    mở GIỮ hạn đã tính — luật mới áp cho kỳ kết thúc từ ngày hiệu lực (0067). Mỗi công ty mỗi ngày một lần (``rules_synced_on``)."""
    on = inp.as_of or today()
    setup = await ctx.one("SELECT * FROM books.setup FOR UPDATE")
    if setup is None or not setup["reg_pack"]:
        return {"changed": 0}
    before = await ctx.val("SELECT max(rule_version) FROM books.obligations WHERE source = 'rule' AND active")
    await _sync_rules(ctx, on)
    await ctx.exec("UPDATE books.setup SET rules_synced_on = %s", (on,))
    v = regulations.version_on(setup["reg_pack"], on)
    if v is None or not before or before == v.version:
        return {"changed": 0}
    await ctx.notify([setup["owner_id"], setup["reviewer_id"]], kind="books", key=f"reg:{v.ref}",
                     title=f"{v.title}: phiên bản mới hiệu lực từ {v.effective_from:%d/%m/%Y}",
                     body=f"{v.summary} Áp cho kỳ kết thúc từ ngày này; kỳ đang mở giữ hạn đã tính.",
                     push="Bộ quy định có phiên bản mới",
                     open={"query": "books.regulation", "input": {}})
    return {"changed": 1}


# ── Mở kỳ (vòng quét, quyền hệ thống của chính công ty này) ──

class OpenDue(Input):
    as_of: Day | None = field("Tính tới ngày", None, hidden=True)


# tháng sau kỳ mới nhất (chưa có ⇒ kỳ bắt đầu) đã hết và chưa vượt kỳ cuối
@m.command("open_due", resource="books", title="Mở kỳ sổ sách đến hạn", internal=True,
           sweep="""SELECT s.tenant_id FROM books.setup s
                    CROSS JOIN LATERAL (SELECT coalesce((SELECT max(p.period_start) + interval '1 month' FROM books.periods p
                                                         WHERE p.tenant_id = s.tenant_id), s.start_period)::date AS next) n
                    WHERE n.next < date_trunc('month', core.today_of(s.tenant_id)) AND (s.end_period IS NULL OR n.next <= s.end_period)""")
async def open_due(ctx: Ctx, inp: OpenDue) -> dict[str, Any]:
    """Mở mọi tháng SAU kỳ mới nhất đã có (chưa có ⇒ từ kỳ bắt đầu) tới tháng vừa hết, không vượt kỳ cuối. Khoá dòng thiết lập ⇒ hai lượt cùng
    lúc chạy nối tiếp; khoá duy nhất (công ty, tháng) chặn nốt. Người phụ trách không còn hoạt động ⇒ kỳ mở không người phụ trách + báo."""
    setup = await ctx.one("SELECT * FROM books.setup FOR UPDATE")
    if setup is None:
        return {"opened": 0}
    latest = await ctx.val("SELECT max(period_start) FROM books.periods")
    month = add_months(latest, 1) if latest else setup["start_period"]
    last = add_months(month_of(inp.as_of or today()), -1)
    if setup["end_period"]:
        last = min(last, setup["end_period"])
    owner = setup["owner_id"] if await _active(ctx, setup["owner_id"]) else None
    reviewer = setup["reviewer_id"] if await _active(ctx, setup["reviewer_id"]) else None
    team = await ctx.home_team(owner)
    opened: list[date] = []
    no_rules: list[date] = []   # kỳ trước phiên bản quy định đầu tiên: KHÔNG lấy luật của phiên bản sau áp ngược — báo thiếu dữ liệu quy định
    while month <= last:
        pid = await ctx.val("""INSERT INTO books.periods (period_start, owner_id, reviewer_id, team_id) VALUES (%s, %s, %s, %s)
                               ON CONFLICT (tenant_id, period_start) DO NOTHING RETURNING id""", (month, owner, reviewer, team))
        if pid is not None:
            # luật áp cho kỳ = phiên bản hiệu lực ngày cuối kỳ (chạy bù qua mốc đổi luật ⇒ mỗi tháng đúng phiên bản của nó)
            end = regulations.period_end(month)
            await _sync_rules(ctx, end)
            _, v = await _version(ctx, end)
            if setup["reg_pack"] and v is None:
                no_rules.append(month)
            for o in await ctx.all("SELECT * FROM books.obligations WHERE active ORDER BY code"):
                if o["source"] == "rule" and v is None:
                    continue
                if applies(o["cadence"], month):
                    ref = f"{setup['reg_pack']}@{o['rule_version']}:{o['code']}" if o["source"] == "rule" else ""
                    # chính sách ngày nghỉ KHAI RÕ trên nghĩa vụ (0068) — luật điền sẵn, kế toán chọn cho nghĩa vụ tự khai; không suy từ nguồn
                    due = regulations.apply_holiday(o["holiday_rule"], o["holiday_calendar"],
                                                    regulations.raw_due(month, o["due_months"], o["due_day"]))
                    await ctx.exec("""INSERT INTO books.filings (period_id, obligation_id, label, due_on, rule_ref, basis, calendar_ref, calendar_warning)
                                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                                   (pid, o["id"], filing_label(o["label"], o["cadence"], month),
                                    due.day, ref, o["basis"], ", ".join(due.refs), "; ".join(due.warnings)))
            ctx.emit("subject.created", kind=PERIOD, id=pid)
            opened.append(month)
        month = add_months(month, 1)
    if opened:   # danh mục hiển thị theo luật HIỆN HÀNH (chạy bù qua kỳ cũ để lại phiên bản cũ)
        await _sync_rules(ctx, inp.as_of or today())
    if no_rules:
        admins = [r["user_id"] for r in await ctx.all("SELECT user_id FROM core.memberships WHERE role = 'admin' AND active")]
        await ctx.notify([owner, reviewer, *admins], kind="books", key=f"no_rules:{no_rules[0]:%Y-%m}",
                         title=f"Kỳ {', '.join(f'{x:%m/%Y}' for x in no_rules)} chưa có dữ liệu quy định",
                         body="Trước phiên bản đầu tiên của bộ quy định — nghĩa vụ theo luật KHÔNG tự sinh cho các kỳ này; kế toán thêm tay ở từng kỳ",
                         open={"query": "books.regulation", "input": {}})
    if opened and owner is None:
        admins = [r["user_id"] for r in await ctx.all("SELECT user_id FROM core.memberships WHERE role = 'admin' AND active")]
        await ctx.notify([reviewer, *admins], kind="books", title=f"Kỳ sổ sách {', '.join(f'{x:%m/%Y}' for x in opened)} chưa có người phụ trách",
                         body="Người phụ trách trong thiết lập không còn hoạt động — chọn người ở Sổ sách › Thiết lập dịch vụ",
                         open={"query": "books.periods", "input": {}})
    return {"opened": len(opened), "periods": [f"{x:%m/%Y}" for x in opened], "unassigned": bool(opened) and owner is None,
            "no_rules": [f"{x:%m/%Y}" for x in no_rules]}


# ── Làm việc (quyền books) ──

class AddFiling(Input):
    period_id: UUID = field("Kỳ sổ sách", hidden=True)
    obligation_code: str = field("Nghĩa vụ", options_query="books.obligation_options")
    due_on: Day = field("Hạn của nghĩa vụ này", help="Nhập đúng hạn đã đối chiếu; không tự dời ngày nghỉ / sửa các kỳ khác")
    evidence: str = field("Căn cứ bổ sung", max_length=500)


@m.query("obligation_options", resource="books", title="Nghĩa vụ có thể bổ sung")
async def obligation_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return [{"value": r["code"], "label": f"{r['code']} · {r['label']}"} for r in
            await ctx.all("SELECT code, label FROM books.obligations ORDER BY code")]


@m.command("add_filing", resource="books.setup", title="Bổ sung nghĩa vụ vào kỳ", category="daily", agent="propose",
           shows=("books.filing", "filing_id"))
async def add_filing(ctx: Ctx, inp: AddFiling) -> dict[str, Any]:
    """Bổ sung một nghĩa vụ trong danh mục vào một kỳ ĐANG MỞ (kể cả nghĩa vụ hiện ngừng áp dụng). Không đổi mẫu / kỳ khác, căn cứ bắt buộc."""
    period = await ctx.one("SELECT * FROM books.periods WHERE id = %s FOR UPDATE", (inp.period_id,))
    if period is None:
        raise NotFound("Không thấy kỳ sổ sách (hoặc bạn không có quyền xem)")
    if period["status"] != "open":
        raise Invalid("Không bổ sung nghĩa vụ vào kỳ đã huỷ")
    if not inp.evidence.strip():
        raise Invalid("Cần căn cứ bổ sung nghĩa vụ")
    obligation = await ctx.one("SELECT * FROM books.obligations WHERE code = %s", (inp.obligation_code.strip().upper(),))
    if obligation is None:
        raise NotFound("Nghĩa vụ chưa có trong danh mục — thêm ở Danh mục nghĩa vụ trước")
    if await ctx.val("SELECT 1 FROM books.filings WHERE period_id = %s AND obligation_id = %s", (inp.period_id, obligation["id"])):
        raise Conflict("Kỳ này đã có nghĩa vụ đó — mở nghĩa vụ hiện có để xử lý")
    fid = await ctx.val("""INSERT INTO books.filings (period_id, obligation_id, label, due_on, basis) VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                        (inp.period_id, obligation["id"], filing_label(obligation["label"], obligation["cadence"], period["period_start"]),
                         inp.due_on, inp.evidence.strip()))
    await ctx.exec("""INSERT INTO books.filing_events (filing_id, status, note, by_user, kind, new_due_on)
                      VALUES (%s, 'pending', %s, core.me(), 'added', %s)""", (fid, inp.evidence.strip(), inp.due_on))
    ctx.emit("subject.changed", kind=PERIOD, id=inp.period_id)
    return {"filing_id": fid, "summary": f"Bổ sung {obligation['label']} kỳ {period['period_start']:%m/%Y}, hạn {inp.due_on:%d/%m/%Y}"}


class RescheduleFiling(Input):
    filing_id: UUID = field("Nghĩa vụ", hidden=True)
    expected_due_on: Day = field("Hạn đang đối chiếu", hidden=True)
    due_on: Day = field("Hạn mới")
    evidence: str = field("Căn cứ điều chỉnh", max_length=500, help="Số văn bản / nội dung xác nhận; không thay đổi nghĩa vụ của kỳ khác")


@m.command("reschedule_filing", resource="books.setup", title="Điều chỉnh hạn nghĩa vụ", category="daily", agent="propose",
           shows=("books.filing", "filing_id"))
async def reschedule_filing(ctx: Ctx, inp: RescheduleFiling) -> dict[str, Any]:
    """Đổi hạn một nghĩa vụ chưa xử lý, giữ hạn gốc + lịch sử. Kiểm hạn người dùng đã xem để không ghi đè thay đổi của người khác."""
    f = await ctx.one("""SELECT f.*, p.status AS period_status FROM books.filings f JOIN books.periods p ON p.id = f.period_id
                         WHERE f.id = %s FOR UPDATE OF f, p""", (inp.filing_id,))
    if f is None:
        raise NotFound("Không thấy nghĩa vụ (hoặc bạn không có quyền xem)")
    if f["period_status"] != "open" or f["status"] != "pending":
        raise Invalid("Chỉ điều chỉnh hạn của nghĩa vụ chưa xử lý trong kỳ đang mở")
    if not inp.evidence.strip():
        raise Invalid("Cần căn cứ điều chỉnh hạn")
    if f["due_on"] != inp.expected_due_on:
        raise Conflict("Hạn đã được người khác điều chỉnh — mở lại nghĩa vụ để đối chiếu")
    if f["due_on"] == inp.due_on:
        raise Invalid("Hạn mới trùng hạn hiện tại")
    await ctx.exec("""INSERT INTO books.filing_events (filing_id, status, note, by_user, kind, old_due_on, new_due_on)
                      VALUES (%s, %s, %s, core.me(), 'rescheduled', %s, %s)""",
                   (f["id"], f["status"], inp.evidence.strip(), f["due_on"], inp.due_on))
    await ctx.exec("UPDATE books.filings SET due_on = %s WHERE id = %s", (inp.due_on, f["id"]))
    ctx.emit("subject.changed", kind=PERIOD, id=f["period_id"])
    return {"filing_id": f["id"], "summary": f"{f['label']}: hạn {f['due_on']:%d/%m/%Y} → {inp.due_on:%d/%m/%Y}"}

class MarkFiling(Input):
    filing_id: UUID = field("Nghĩa vụ", hidden=True)
    status: Literal["filed", "not_required", "pending"] = field("Tình trạng", "filed", options=FILING_STATUS)
    evidence: str = field("Căn cứ / lý do", "", max_length=500, example="Mã giao dịch nộp tờ khai 11020260100123",
                          help="Bắt buộc: số biên nhận / mã giao dịch khi đã nộp; lý do khi không phát sinh hoặc mở lại")
    document_id: UUID | None = field("Tài liệu", None, hidden=True)


@m.command("mark_filing", resource="books", title="Cập nhật nghĩa vụ")
async def mark_filing(ctx: Ctx, inp: MarkFiling) -> dict[str, Any]:
    f = await ctx.one("SELECT f.*, p.status AS period_status FROM books.filings f JOIN books.periods p ON p.id = f.period_id WHERE f.id = %s FOR UPDATE OF f",
                      (inp.filing_id,))
    if f is None:
        raise NotFound("Không thấy nghĩa vụ (hoặc bạn không có quyền xem kỳ này)")
    if f["period_status"] != "open":
        raise Invalid("Kỳ đã huỷ")
    if not inp.evidence.strip():
        raise Invalid("Cần căn cứ: số biên nhận / mã giao dịch khi đã nộp, lý do khi không phát sinh hoặc mở lại")
    if inp.status == f["status"]:
        raise Invalid(f"Nghĩa vụ đang ở trạng thái “{FILING_STATUS[inp.status]}” rồi")
    done = inp.status != "pending"
    # nhật ký CHỈ THÊM (151): mở lại không xoá dấu vết — tranh chấp với khách (nộp trễ, ai chịu phạt) dựa vào đây
    await ctx.exec("INSERT INTO books.filing_events (filing_id, status, note, document_id, by_user) VALUES (%s, %s, %s, %s, %s)",
                   (f["id"], inp.status, inp.evidence.strip(), inp.document_id, ctx.p.user_id))
    await ctx.exec("""UPDATE books.filings SET status = %s, evidence = %s, document_id = %s, done_by = %s,
                      done_at = CASE WHEN %s THEN now() END WHERE id = %s""",
                   (inp.status, inp.evidence.strip() if done else "", inp.document_id if done else None, ctx.p.user_id if done else None, done, f["id"]))
    ctx.emit("subject.changed", kind=PERIOD, id=f["period_id"])
    return {"filing_id": f["id"], "status": inp.status, "summary": f"{f['label']}: {FILING_STATUS[inp.status].lower()}"}


class PeriodFilter(Input):
    status: Literal["", "open", "cancelled"] = field("Kỳ", "open", options={"": "Tất cả", "open": "Đang mở", "cancelled": "Đã huỷ"})


async def _flow_status(ctx: Ctx, period_id: Any) -> dict[str, Any] | None:
    """Hồ sơ theo chặng của một kỳ (quyền người gọi). Trợ lý hỏi mà nhóm Quy trình tắt ⇒ None (bảng vẫn chạy, cột chặng để trống)."""
    try:
        return await ctx.call_as("flow.subject_status", ctx.p, subject_kind=PERIOD, subject_id=period_id)
    except Denied:
        return None


async def _period_rows(ctx: Ctx, status: str = "open", limit: int = 24) -> list[dict[str, Any]]:
    rows = await ctx.all(
        """SELECT p.id, p.period_start, p.status, p.owner_id, u.name AS owner, rv.name AS reviewer,
                  count(f.id) FILTER (WHERE f.status = 'pending') AS pending,
                  min(f.due_on) FILTER (WHERE f.status = 'pending') AS next_due,
                  count(f.id) FILTER (WHERE f.status = 'pending' AND f.due_on < core.today()) AS overdue
           FROM books.periods p LEFT JOIN core.users u ON u.id = p.owner_id LEFT JOIN core.users rv ON rv.id = p.reviewer_id
           LEFT JOIN books.filings f ON f.period_id = p.id
           WHERE %(s)s = '' OR p.status = %(s)s GROUP BY p.id, u.name, rv.name ORDER BY p.period_start DESC LIMIT %(n)s""", {"s": status, "n": limit})
    t = today()
    for r in rows:
        st = await _flow_status(ctx, r["id"])
        r["label"], r["subject_kind"] = f"{r['period_start']:%m/%Y}", PERIOD
        r["stage"] = ("Xong" if st and st["status"] == "done" else (st["stage_name"] if st else "Chưa có hồ sơ"))
        r["missing"] = ", ".join(st["missing"][:3]) if st and st["status"] == "open" else ""
        r["flag"] = ("Quá hạn" if r["overdue"] else "Chưa có người phụ trách" if r["owner_id"] is None and r["status"] == "open"
                     else "Sắp đến hạn" if r["next_due"] and (r["next_due"] - t).days <= SOON_DAYS else "")
    return rows


@m.query("periods", resource="books", title="Kỳ sổ sách")
async def periods(ctx: Ctx, inp: PeriodFilter) -> dict[str, Any]:
    """Kỳ đang mở: đang ở chặng nào, ai giữ, còn thiếu gì, nghĩa vụ chưa nộp + hạn gần nhất. Bấm một kỳ ⇒ hồ sơ theo chặng."""
    rows = await _period_rows(ctx, inp.status, 120)
    flags = {"Quá hạn": "red", "Chưa có người phụ trách": "red", "Sắp đến hạn": "orange"}
    return ui.table(rows, [
        ui.col("flag", "", "badge", tones=flags), ui.col("label", "Kỳ"), ui.col("stage", "Đang ở chặng"), ui.col("owner", "Phụ trách"),
        ui.col("reviewer", "Soát xét"), ui.col("pending", "Nghĩa vụ chưa nộp", "number"), ui.col("next_due", "Hạn gần nhất", "date"),
        ui.col("missing", "Còn thiếu", width=320),
    ], kpis=[ui.kpi("Kỳ đang mở", sum(r["status"] == "open" for r in rows)),
             ui.kpi("Quá hạn", sum(bool(r["overdue"]) for r in rows), tone="red" if any(r["overdue"] for r in rows) else None),
             ui.kpi("Chưa có người phụ trách", sum(r["flag"] == "Chưa có người phụ trách" for r in rows))],
        empty="Chưa có kỳ nào — kiểm Thiết lập dịch vụ (kỳ bắt đầu) rồi chờ vòng quét mở kỳ", limit=120)


class FilingFilter(Input):
    status: Literal["", "pending", "filed", "not_required"] = field("Tình trạng", "pending", options={"": "Tất cả", **FILING_STATUS})


@m.query("filings", resource="books", title="Nghĩa vụ & hạn nộp")
async def filings(ctx: Ctx, inp: FilingFilter) -> dict[str, Any]:
    rows = await ctx.all(
        """SELECT f.id, f.label, f.due_on, f.original_due_on, f.calendar_warning, f.status, f.evidence, f.done_at,
                  to_char(p.period_start, 'MM/YYYY') AS period, u.name AS done_by,
                  f.due_on - core.today() AS days_left, (SELECT count(*) FROM books.filing_events e WHERE e.filing_id = f.id) AS changes
           FROM books.filings f JOIN books.periods p ON p.id = f.period_id LEFT JOIN core.users u ON u.id = f.done_by
           WHERE p.status = 'open' AND (%(s)s = '' OR f.status = %(s)s) ORDER BY f.status <> 'pending', f.due_on LIMIT 500""", {"s": inp.status})
    for r in rows:
        r["flag"] = "" if r["status"] != "pending" else ("Quá hạn" if r["days_left"] < 0 else "Sắp đến hạn" if r["days_left"] <= SOON_DAYS else "")
    # cột cảnh báo lịch chỉ hiện khi CÓ dòng cần đối chiếu — lịch đã xác nhận thì không chiếm chỗ bảng
    warn = [ui.col("calendar_warning", "Lịch cần đối chiếu", width=260)] if any(r["calendar_warning"] for r in rows) else []
    return ui.table(rows, [
        ui.col("flag", "", "badge", tones={"Quá hạn": "red", "Sắp đến hạn": "orange"}), ui.col("label", "Nghĩa vụ"), ui.col("period", "Kỳ sổ sách"),
        ui.col("due_on", "Hạn", "date"), ui.col("status", "Tình trạng", "badge", labels=FILING_STATUS, tones=FILING_TONES), *warn,
        ui.col("evidence", "Căn cứ", width=260), ui.col("done_by", "Người cập nhật"), ui.col("changes", "Lần cập nhật", "number"),
    ], kpis=[ui.kpi("Chưa nộp", sum(r["status"] == "pending" for r in rows)), ui.kpi("Quá hạn", sum(r["flag"] == "Quá hạn" for r in rows))],
        empty="Không có nghĩa vụ nào ở tình trạng này", limit=500)


class FilingRef(Input):
    filing_id: UUID = field("Nghĩa vụ", hidden=True)


@m.query("filing", resource="books", title="Lịch sử nghĩa vụ")
async def filing(ctx: Ctx, inp: FilingRef) -> dict[str, Any]:
    """Một nghĩa vụ + MỌI lần cập nhật (ai, lúc nào, căn cứ / lý do) — kể cả các lần mở lại."""
    f = await ctx.one("""SELECT f.*, p.status AS period_status, to_char(p.period_start, 'MM/YYYY') AS period FROM books.filings f JOIN books.periods p ON p.id = f.period_id
                         WHERE f.id = %s""", (inp.filing_id,))
    if f is None:
        raise NotFound("Không thấy nghĩa vụ (hoặc bạn không có quyền xem kỳ này)")
    log = await ctx.all("""SELECT e.status, e.kind, e.old_due_on, e.new_due_on, e.note, e.at, u.name FROM books.filing_events e LEFT JOIN core.users u ON u.id = e.by_user
                           WHERE e.filing_id = %s ORDER BY e.at DESC, e.id DESC""", (f["id"],))
    def event_title(e: dict[str, Any]) -> str:
        if e["kind"] == "rescheduled":
            return f"Đổi hạn {e['old_due_on']:%d/%m/%Y} → {e['new_due_on']:%d/%m/%Y} — {e['note']}"
        return f"{'Bổ sung nghĩa vụ' if e['kind'] == 'added' else FILING_STATUS[e['status']]} — {e['note']}"
    items = [{"title": event_title(e), "subtitle": f"{e['name'] or 'Hệ thống'} · {e['at'].astimezone(zone()):%d/%m/%Y %H:%M}",
              "tone": FILING_TONES[e["status"]]} for e in log]
    return ui.card(f["label"], [
        ui.b_kpis([ui.kpi("Tình trạng", FILING_STATUS[f["status"]], "text"), ui.kpi("Hạn", f"{f['due_on']:%d/%m/%Y}", "text"),
                   ui.kpi("Hạn ban đầu", f"{f['original_due_on']:%d/%m/%Y}", "text"), ui.kpi("Kỳ sổ sách", f["period"], "text")]),
        ui.b_text(f"{f['rule_ref'] or 'Bổ sung theo căn cứ / danh mục tự khai'}\n\n{f['basis']}\n\nLịch: {f['calendar_ref'] or 'Hạn nhập trực tiếp hoặc dữ liệu trước 0068'}", "Nguồn tính hạn"),
        *([ui.b_callout("warn", "Lịch cần đối chiếu", f["calendar_warning"])] if f["calendar_warning"] else []),
        ui.b_list(items, "Lịch sử (chỉ thêm, không sửa / xoá)") if items else ui.b_callout("muted", "Chưa cập nhật lần nào"),
    ], subtitle="Nghĩa vụ của kỳ sổ sách", icon="checklist",
        actions=[ui.act("books.reschedule_filing", "Điều chỉnh hạn", {"filing_id": str(f["id"]), "expected_due_on": f["due_on"].isoformat(),
                                                                    "due_on": f["due_on"].isoformat()})]
        if f["period_status"] == "open" and f["status"] == "pending" else [])


def _rule_text(due_months: int, due_day: int) -> str:
    day = "cuối tháng" if due_day == 31 else due_day
    return f"Ngày {day} trong tháng cuối kỳ" if due_months == 0 else f"Ngày {day}, {due_months} tháng sau kỳ"


@m.query("obligations", resource="books", title="Danh mục nghĩa vụ")
async def obligations(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all("SELECT id, code, label, cadence, due_months, due_day, active, source, basis, rule_version, holiday_rule, holiday_calendar"
                         " FROM books.obligations ORDER BY active DESC, source DESC, code")
    for r in rows:
        r["rule"] = _rule_text(r["due_months"], r["due_day"])
        r["holiday"] = regulations.HOLIDAY_RULES[r["holiday_rule"]] + (f" ({r['holiday_calendar']})" if r["holiday_calendar"] else "")
        r["state"] = "on" if r["active"] else "off"
    setup, v = await _version(ctx, today())
    kind = setup["entity_kind"] if setup else "company"
    note = ("Hộ / cá nhân kinh doanh: bộ quy định chưa có nghĩa vụ cho loại này (quy định đang đổi — sàn TMĐT khấu trừ nộp thay từ 07/2025, bỏ thuế "
            "khoán 2026). Kế toán thêm nghĩa vụ thật ở nút Thêm. " if kind == "household" else "")
    law = (f"Nghĩa vụ theo luật: {v.title}, phiên bản hiệu lực từ {v.effective_from:%d/%m/%Y} ({regulations.STATUS[v.status].lower()}) — đổi dữ kiện "
           "ở Sổ sách › Bộ quy định. " if v else "Không theo bộ quy định — mọi nghĩa vụ do kế toán tự khai. ")
    return ui.table(rows, [
        ui.col("code", "Mã"), ui.col("label", "Nghĩa vụ"), ui.col("cadence", "Kỳ", labels=CADENCE), ui.col("rule", "Hạn"),
        ui.col("holiday", "Gặp ngày nghỉ", width=220),
        ui.col("source", "Nguồn", "badge", labels=SOURCE, tones={"rule": "blue", "manual": "gray"}), ui.col("basis", "Căn cứ", width=320),
        ui.col("state", "", "badge", labels={"on": "Đang áp dụng", "off": "Ngừng"}, tones={"on": "green", "off": "gray"}),
    ], note=note + law + "Sửa chỉ áp cho kỳ mở sau. Gặp ngày nghỉ: theo chính sách khai trên từng nghĩa vụ (luật điền sẵn, tự khai thì chọn).",
        empty="Chưa có nghĩa vụ nào")


def _num(v: Any) -> str:
    return f"{v:,}".replace(",", ".") if isinstance(v, int) else str(v)


@m.query("regulation", resource="books", title="Bộ quy định đang áp dụng")
async def regulation(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Luật / nghị định công ty này đang theo: phiên bản hiệu lực hôm nay (giả định hay đã xác nhận), dữ kiện khách đã khai, nghĩa vụ + căn cứ
    điều khoản, tham số (ngưỡng tiền mặt…), phiên bản sắp hiệu lực. Trả lời câu hỏi "hạn này theo luật nào" — trích ``basis``, không tự suy."""
    setup, v = await _version(ctx, today())
    if setup is None or v is None:
        why = "Chưa thiết lập dịch vụ sổ sách" if setup is None else (
            "Không theo bộ quy định — kế toán tự khai" if not setup["reg_pack"] else "Bộ quy định chưa có phiên bản hiệu lực hôm nay")
        return ui.card("Bộ quy định", [ui.b_callout("warn", why, "Chọn bộ quy định ở Sổ sách › Thiết lập dịch vụ.")], icon="building.columns")
    facts = v.facts({**setup["profile"], "entity_kind": setup["entity_kind"]})
    rules = v.obligations_for(facts)
    soon = regulations.upcoming(setup["reg_pack"], today())
    blocks: list[dict[str, Any]] = [
        ui.b_kpis([ui.kpi("Bộ", v.title, "text"), ui.kpi("Phiên bản", f"Hiệu lực từ {v.effective_from:%d/%m/%Y}", "text"),
                   ui.kpi("Trạng thái", regulations.STATUS[v.status], "text", tone="orange" if v.status == "assumption" else "green")]),
    ]
    if v.status == "assumption":
        blocks.append(ui.b_callout("warn", "Giả định — chưa người có chuyên môn xác nhận",
                                   "Hạn và căn cứ dưới đây là bản soạn sẵn; kế toán trưởng / đối tác chuyên môn xác nhận trước khi dựa vào để tư vấn khách."))
    blocks += [
        ui.b_list([{"title": f"{d['label']}: {d['options'][facts[k]]}", "subtitle": d["basis"]} for k, d in v.profile.items()], "Dữ kiện của khách"),
        ui.b_list([{"title": f"{r.label} — {CADENCE[r.cadence].lower()}, {_rule_text(r.months, r.day).lower()}", "subtitle": r.basis} for r in rules]
                  or [{"title": "Không có nghĩa vụ nào theo bộ quy định cho hồ sơ này", "subtitle": "Kế toán tự khai ở Danh mục nghĩa vụ"}],
                  "Nghĩa vụ theo luật"),
        ui.b_list([{"title": f"{p['label']}: {_num(p['value'])}", "subtitle": p["basis"]} for p in v.params.values()], "Tham số"),
        ui.b_callout("muted", "Hạn rơi vào ngày nghỉ", v.calendar_basis or "Không dời"),
    ]
    if soon:
        blocks.append(ui.b_list([{"title": f"Từ {x.effective_from:%d/%m/%Y}", "subtitle": x.summary} for x in soon], "Sắp hiệu lực"))
    blocks.append(ui.b_list([{"title": s["ref"], "subtitle": s.get("url") or ""} for s in v.sources], f"Văn bản ({v.summary})"))
    return ui.card(f"Bộ quy định: {v.title}", blocks, subtitle=v.ref, icon="building.columns",
                   actions=[ui.act("books.set_profile", "Khai dữ kiện", style="primary")],
                   open=[{"title": "So với phiên bản trước", "query": "books.compare_versions", "input": {"pack": v.pack, "to_version": v.version}}]
                   if any(x.effective_from < v.effective_from for x in regulations.packs()[v.pack]) else [])


class CompareVersions(Input):
    pack: str = field("Bộ quy định", "", max_length=40, help="Trống = bộ công ty đang theo", example="vn-tax")
    from_version: str = field("Từ phiên bản", "", max_length=10, help="Ngày hiệu lực yyyy-mm-dd; trống = phiên bản liền trước phiên bản mới", example="2020-07-01")
    to_version: str = field("Tới phiên bản", "", max_length=10, help="Trống = phiên bản mới nhất", example="2025-07-01")


CHANGE = {"added": "Thêm", "removed": "Bỏ", "changed": "Đổi"}
KIND = {"param": "Tham số", "obligation": "Nghĩa vụ", "profile": "Dữ kiện hồ sơ", "calendar": "Ngày nghỉ"}


@m.query("compare_versions", resource="books", title="So sánh hai phiên bản quy định")
async def compare_versions(ctx: Ctx, inp: CompareVersions) -> dict[str, Any]:
    """Khác biệt giữa hai phiên bản của một bộ quy định — tham số, nghĩa vụ, dữ kiện hồ sơ, lịch: thêm / bỏ / đổi, giá trị cũ → mới, căn cứ, đổi trạng
    thái xác minh. Chỉ đọc (0154). Soát trước khi phát hành: cho kế toán thấy ĐÚNG phần văn bản mới đổi, không phải đọc lại cả file."""
    setup = await ctx.one("SELECT reg_pack FROM books.setup")
    pack = inp.pack or (setup["reg_pack"] if setup else "")
    versions = regulations.packs().get(pack, ())
    if not versions:
        raise NotFound("Không thấy bộ quy định này")
    by_ver = {v.version: v for v in versions}
    new = by_ver.get(inp.to_version) if inp.to_version else versions[-1]
    if new is None:
        raise NotFound(f"Bộ {pack} không có phiên bản {inp.to_version}")
    if inp.from_version:
        old = by_ver.get(inp.from_version)
    else:
        old = next((v for v in reversed(versions) if v.effective_from < new.effective_from), None)
    if old is None:
        raise Invalid("Không có phiên bản trước để so" if not inp.from_version else f"Bộ {pack} không có phiên bản {inp.from_version}")
    rows = regulations.compare(old, new)
    return ui.table(
        rows, [ui.col("kind", "Loại", "badge", labels=KIND), ui.col("code", "Mã"), ui.col("change", "Thay đổi", "badge", labels=CHANGE,
                                                                                    tones={"added": "green", "removed": "red", "changed": "orange"}),
               ui.col("old", "Cũ", width=260), ui.col("new", "Mới", width=260), ui.col("status_change", "Trạng thái", width=200), ui.col("basis", "Căn cứ", width=300)],
        title=f"{new.title}: {old.version} → {new.version}", empty="Hai phiên bản giống nhau từng quy tắc",
        kpis=[ui.kpi("Thêm", sum(r["change"] == "added" for r in rows)), ui.kpi("Bỏ", sum(r["change"] == "removed" for r in rows)),
              ui.kpi("Đổi", sum(r["change"] == "changed" for r in rows))],
        note=("Phiên bản mới ghi HỒI TỐ: " + new.retroactive_reason + " " if new.retroactive else "") + new.summary)


@m.query("profile_options", resource="books", title="Dữ kiện khai được")
async def profile_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    _, v = await _version(ctx, today())
    if v is None:
        return []
    return [{"value": f"{k}={o}", "label": f"{d['label']}: {lab}"} for k, d in v.profile.items() if k not in PROFILE_FIXED
            for o, lab in d["options"].items()]


class SetProfile(Input):
    fact: str = field("Dữ kiện", options_query="books.profile_options", example="vat_cadence=monthly")


@m.command("set_profile", resource="books.setup", action="write", title="Khai dữ kiện cho bộ quy định")
async def set_profile(ctx: Ctx, inp: SetProfile) -> dict[str, Any]:
    """Khách khai GTGT theo tháng, không còn lao động… ⇒ nghĩa vụ theo luật đổi theo, áp cho kỳ MỞ SAU (kỳ đã mở giữ hạn đã tính)."""
    key, _, value = inp.fact.partition("=")
    _, v = await _version(ctx, today())
    if v is None:
        raise Invalid("Công ty này không theo bộ quy định — chọn ở Thiết lập dịch vụ")
    d = v.profile.get(key)
    if d is None or key in PROFILE_FIXED or value not in d["options"]:
        raise Invalid(f"Dữ kiện “{inp.fact}” không có trong {v.title} (phiên bản {v.effective_from:%d/%m/%Y})")
    await ctx.exec("UPDATE books.setup SET profile = profile || jsonb_build_object(%s::text, %s::text), updated_at = now()", (key, value))
    added = await _sync_rules(ctx, today())
    return {"fact": inp.fact, "obligations_added": added, "summary": f"{d['label']}: {d['options'][value]} — áp cho kỳ mở sau"}


@m.query("setup_view", resource="books.setup", title="Thiết lập dịch vụ sổ sách")
async def setup_view(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    s = await ctx.one("""SELECT s.*, u.name AS owner, u.email AS owner_email, rv.name AS reviewer, rv.email AS reviewer_email FROM books.setup s
                         LEFT JOIN core.users u ON u.id = s.owner_id LEFT JOIN core.users rv ON rv.id = s.reviewer_id""")
    if s is None:
        return ui.card("Chưa thiết lập dịch vụ sổ sách", [ui.b_callout("warn", "Chưa có thiết lập", "Chọn người phụ trách, soát xét và kỳ bắt đầu.")],
                       actions=[ui.act("books.configure", "Thiết lập", style="primary")], icon="book.closed")
    return ui.card("Thiết lập dịch vụ sổ sách", [ui.b_kpis([
        ui.kpi("Phụ trách", s["owner"] or "Chưa có", "text"), ui.kpi("Soát xét", s["reviewer"] or "Chưa có", "text"),
        ui.kpi("Từ kỳ", f"{s['start_period']:%m/%Y}", "text"), ui.kpi("Kỳ cuối", f"{s['end_period']:%m/%Y}" if s["end_period"] else "Đang làm", "text"),
        ui.kpi("Loại", ENTITY_KIND[s["entity_kind"]], "text"), ui.kpi("Bộ quy định", reg_options().get(s["reg_pack"], s["reg_pack"]), "text")])],
        actions=[ui.act("books.configure", "Sửa", {"owner_email": s["owner_email"] or "", "reviewer_email": s["reviewer_email"] or "",
                                                    "start_period": s["start_period"].isoformat(),
                                                    "end_period": s["end_period"].isoformat() if s["end_period"] else None,
                                                    "entity_kind": s["entity_kind"], "reg_pack": s["reg_pack"]}, "primary")],
        icon="book.closed")


@m.query("summary", resource="books", title="Tóm tắt sổ sách (cho màn nhiều khách)")
async def summary(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Một dòng / công ty cho màn tiến độ của công ty dịch vụ (``kernel/portfolio``), chạy bằng quyền người xem tại công ty này. Tính trên TOÀN BỘ kỳ
    đang mở còn việc (nghĩa vụ chưa nộp hoặc hồ sơ chưa xong) — không cắt theo số kỳ hiển thị; kỳ cần chú ý = hạn chưa nộp gần nhất. HỢP ĐỒNG ra
    (qua JSON): ngày là chuỗi ``YYYY-MM-DD`` hoặc null."""
    rows = await ctx.all(
        """SELECT p.id, p.period_start, p.owner_id, u.name AS owner,
                  count(f.id) FILTER (WHERE f.status = 'pending') AS pending,
                  min(f.due_on) FILTER (WHERE f.status = 'pending') AS next_due,
                  count(f.id) FILTER (WHERE f.status = 'pending' AND f.due_on < core.today()) AS overdue
           FROM books.periods p LEFT JOIN core.users u ON u.id = p.owner_id LEFT JOIN books.filings f ON f.period_id = p.id
           WHERE p.status = 'open' GROUP BY p.id, u.name""")
    try:
        cases: dict[str, str] = await ctx.call_as("flow.subject_states", ctx.p, subject_kind=PERIOD)
    except Denied:   # trợ lý hỏi, nhóm Quy trình tắt ⇒ coi mọi kỳ còn mở là còn việc (không đọc hồ sơ)
        cases = {}
    live = [r for r in rows if r["pending"] or cases.get(str(r["id"])) != "done"]
    worst = min(live, key=lambda r: (r["next_due"] or date.max, r["period_start"])) if live else None
    st = await _flow_status(ctx, worst["id"]) if worst else None
    return {"open": len(live), "pending": sum(r["pending"] for r in live), "overdue": sum(r["overdue"] for r in live),
            "obligations": await ctx.val("SELECT count(*) FROM books.obligations WHERE active"),
            "unassigned": sum(r["owner_id"] is None for r in live),
            "period": f"{worst['period_start']:%m/%Y}" if worst else "", "owner": worst["owner"] or "" if worst else "",
            "stage": (st["stage_name"] if st and st["status"] == "open" else "Xong" if st else "Chưa có hồ sơ") if worst else "",
            "missing": ", ".join(st["missing"][:3]) if st and st["status"] == "open" else "",
            "next_due": worst["next_due"].isoformat() if worst and worst["next_due"] else None}


# ── Yêu cầu gửi khách qua link: năng lực chung asks (0075); Sổ sách chỉ chọn ĐỐI TƯỢNG (kỳ / sổ sách công ty) và soạn mục (0077) ──
# Câu khách trả lời là GIẢI TRÌNH chờ kế toán nhận (asks.accept_items) — không tự thành kết luận hạch toán. File khách nộp vào kệ đã chọn.

COMPANY = "books.company"


class CreateRequest(Input):
    title: str = field("Tiêu đề", max_length=200, example="Chứng từ tháng 10/2026")
    items: str = field("Cần khách gửi / trả lời", kind="textarea", max_length=6000,
                       example="- Sao kê Vietcombank tháng 10 [tệp]\n- Khoản 35tr ngày 14/10 là gì? [chọn: Trả nhà cung cấp; Tạm ứng; Chi cá nhân]",
                       help="Mỗi dòng một mục. Cuối dòng: [tệp] = khách nộp file; [chọn: A; B; C] = khách bấm chọn; không ghi = khách gõ trả lời")
    note: str = field("Lời nhắn cho khách", "", kind="textarea", max_length=1000)
    due_on: Day | None = field("Hạn nộp", None, help="Có hạn ⇒ hệ thống nhắc bạn từ 1 ngày trước hạn nếu khách chưa gửi đủ")
    shelf: str = field("Kệ lưu file khách nộp", "", options_query="kb.upload_shelves", help="Trống = kệ đầu tiên")
    days: int = field("Link dùng được (ngày)", 14, ge=1, le=60)
    period_id: UUID | None = field("Kỳ sổ sách", None, hidden=True, help="Trống = giấy tờ chung của công ty (hồ sơ tiếp nhận, đăng ký kinh doanh…)")


@m.command("create_request", resource="books", title="Gửi yêu cầu cho khách (link)", shows=("asks.request", "once_link"))
async def create_request(ctx: Ctx, inp: CreateRequest) -> dict[str, Any]:
    """Yêu cầu + link gửi khách, gắn kỳ sổ sách (hoặc sổ sách công ty khi là giấy tờ chung). Link chỉ trả MỘT lần; cần lại ⇒ "Tạo lại link"."""
    return await ctx.call("asks.create", subject_kind=PERIOD if inp.period_id else COMPANY, subject_id=str(inp.period_id or ctx.p.tenant_id),
                          title=inp.title, items=inp.items, note=inp.note, due_on=inp.due_on.isoformat() if inp.due_on else None,
                          shelf=inp.shelf, days=inp.days)


@m.subject(COMPANY, "Sổ sách công ty")
async def subject_company(ctx: Ctx, inp: SubjectRef) -> dict[str, Any]:
    """Đối tượng LOGIC (không bảng): giấy tờ chung của công ty — mã = mã công ty. Ai có quyền Sổ sách thì gửi được; thấy yêu cầu chung của
    người khác khi có phạm vi Sổ sách cả công ty (books.subject_visible_company), của mình / đội thì luôn thấy (asks, người gửi)."""
    if inp.subject_id != ctx.p.tenant_id or not (ctx.p.system or ctx.p.can("books", "read")):
        raise NotFound("Không thấy sổ sách công ty")
    return {"title": "Sổ sách công ty", "owner_id": None, "team_id": None, "state": "active", "open": {"query": "books.filings", "input": {}}}


# ── Đối tượng quy trình + bộ kiểm ──

@m.subject(PERIOD, "Kỳ sổ sách")
async def subject_period(ctx: Ctx, inp: SubjectRef) -> dict[str, Any]:
    p = await ctx.one("SELECT * FROM books.periods WHERE id = %s", (inp.subject_id,))
    if p is None:
        raise NotFound("Không thấy kỳ sổ sách")
    return {"title": f"Sổ sách tháng {p['period_start']:%m/%Y}", "owner_id": p["owner_id"], "team_id": p["team_id"],
            "state": "cancelled" if p["status"] == "cancelled" else "active", "open": {"query": "books.filings", "input": {}}}


@m.checker("filings_done", "Nghĩa vụ của kỳ đã nộp / đã xử lý", PERIOD)
async def filings_done(ctx: Ctx, inp: CheckIn) -> dict[str, Any]:
    rows = await ctx.all("SELECT status, done_at FROM books.filings WHERE period_id = %s", (inp.subject_id,))
    left = sum(r["status"] == "pending" for r in rows)
    if left:
        return {"status": "missing", "value": f"Còn {left}/{len(rows)} nghĩa vụ chưa nộp"}
    return {"status": "verified", "value": f"{len(rows)} nghĩa vụ" if rows else "Kỳ này không có nghĩa vụ đến hạn",
            "at": max((r["done_at"] for r in rows), default=None)}


@m.pack_blockers()
async def pack_blockers(ctx: Ctx, inp: NoInput) -> list[str]:
    n = await ctx.val("""SELECT count(*) FROM books.filings f JOIN books.periods p ON p.id = f.period_id
                         WHERE p.status = 'open' AND f.status = 'pending'""")
    return [f"{n} nghĩa vụ chưa nộp ở các kỳ đang mở — xử lý / huỷ kỳ trước"] if n else []


# ---- Câu tín hiệu của bàn việc (0094): khai trên màn (desk=Desk(..., say=...)); agent/desk.py chạy bằng quyền người xem

def _attn_filings(o: Any) -> Line | None:
    return (line("bad", f"{flagged(o, 'Quá hạn')} nghĩa vụ thuế đã quá hạn", "Nộp rồi ghi căn cứ, hoặc điều chỉnh hạn có căn cứ", "Nghĩa vụ thuế nào đang quá hạn?") if flagged(o, "Quá hạn") else None)


def _attn_filings_2(o: Any) -> Line | None:
    return (line("warn", f"{flagged(o, 'Sắp đến hạn')} nghĩa vụ thuế sắp đến hạn", "Trong 5 ngày tới — chuẩn bị hồ sơ") if flagged(o, "Sắp đến hạn") else None)


# ---- Màn hình (0069): module tự khai phân hệ / góp màn vào phân hệ khác; workspaces.py chỉ ráp và lọc theo quyền
m.workspace(Workspace("books", "Sổ sách", "book.closed.fill", (
    Screen("periods", "Kỳ sổ sách", "calendar.circle.fill", "books.periods", tint="blue",
           row_actions=(A("books.create_request", "Gửi yêu cầu cho khách", bind={"period_id": "id"}, when={"status": ["open"]}, style="primary"),
                        A("books.add_filing", "Bổ sung nghĩa vụ", bind={"period_id": "id"}, when={"status": ["open"]})),
           open=Open("flow.case", {"subject_kind": "subject_kind", "subject_id": "id"}, "Hồ sơ kỳ")),
    Screen("client_requests", "Yêu cầu gửi khách", "paperplane.fill", "asks.requests", tint="teal", actions=(A("books.create_request", style="primary"),),
           open=Open("asks.request", {"once_link": "id"}, "Yêu cầu gửi khách")),
    Screen("filings", "Nghĩa vụ & hạn nộp", "checklist.checked", "books.filings", tint="orange",
           desk=Desk("do", say=lambda o: [_attn_filings(o), _attn_filings_2(o)], menu=True, rank=160),
           row_actions=(A("books.mark_filing", "Đã nộp", bind={"filing_id": "id"}, when={"status": ["pending"]}, style="primary"),
                        A("books.mark_filing", "Không phát sinh", bind={"filing_id": "id"}, preset={"status": "not_required"}, when={"status": ["pending"]}),
                        A("books.mark_filing", "Mở lại", bind={"filing_id": "id"}, preset={"status": "pending"},
                          when={"status": ["filed", "not_required"]}),
                        A("books.reschedule_filing", "Điều chỉnh hạn", bind={"filing_id": "id", "expected_due_on": "due_on"},
                          prefill={"due_on": "due_on"}, when={"status": ["pending"]})),
           open=Open("books.filing", {"filing_id": "id"}, "Lịch sử nghĩa vụ")),
    Screen("obligations", "Danh mục nghĩa vụ", "list.bullet.rectangle", "books.obligations", group="Thiết lập",
           actions=(A("books.upsert_obligation", style="primary"),),
           row_actions=(A("books.upsert_obligation", "Sửa", prefill={"code": "code", "label": "label", "cadence": "cadence", "due_months": "due_months",
                                                                    "due_day": "due_day", "active": "active",
                                                                    "holiday_rule": "holiday_rule", "holiday_calendar": "holiday_calendar"},
                          when={"source": ["manual"]}),)),
    Screen("regulation", "Bộ quy định", "building.columns", "books.regulation", group="Thiết lập"),
    Screen("books_setup", "Thiết lập dịch vụ", "gearshape", "books.setup_view", group="Thiết lập"),
), order=60))
