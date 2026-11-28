"""ĐƯA CÔNG TY VÀO DÙNG (ADR 0263) — module lõi của hành trình onboard 7 chặng: lượt chuyển dữ liệu từ hệ cũ (sổ mã gốc + dấu vân tay), đối chiếu có ngưỡng
+ ký, danh sách việc theo chặng, ngày chuyển (go / no-go).

* BỘ CHẠY chuyển dữ liệu là mã trong tiến trình (``barista.api.onboard.load`` — không có ở HTTP, trợ lý không có): mọi lệnh nghiệp vụ chạy
  ``historical=True`` qua executor; phần sổ sách của lượt (mở lượt, sổ mã gốc, ngoại lệ, đối chiếu) ghi qua các lệnh NỘI BỘ của module này bằng quyền
  hệ thống — vẫn một cửa, vẫn nhật ký.
* KÝ đối chiếu ``onboard.sign`` qua chuỗi duyệt ``onboard_signoff`` (dữ liệu của công ty). NGÀY CHUYỂN ``onboard.go_live``: chỉ chủ tài khoản, trợ lý
  không làm, qua chuỗi ``onboard_go_live``; từ chối khi còn việc bắt buộc chưa xong / lượt cuối chưa xong / còn chỉ số lệch chưa ký; đạt thì chạy các
  BƯỚC NGÀY CHUYỂN do module khai (``Module.cutover``) theo thứ tự trong CÙNG giao dịch rồi ghi ngày chuyển. Không có "hoàn tác ngày chuyển".
* DANH SÁCH VIỆC: lõi khai bộ mặc định theo 7 chặng (``CORE_STEPS``); gói khai thêm bằng ``Pack(onboard_steps=…)`` (chỉ hiện khi công ty bật gói).
  Bảng ``onboard.checklist`` chỉ giữ phần công ty đã xác nhận — nâng gói thêm việc mới không ghi đè gì.
Module không biết gói / module nào khác: bước chuyển và việc của gói đọc từ sổ đăng ký.
"""

from datetime import timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, BaseModel, StringConstraints

from barista.api import Action as A
from barista.api import Conflict, Ctx, Day, Denied, Input, Invalid, NoInput, NotFound, Open, Screen, field, today, ui, zone
from barista.kernel import packs, registry
from barista.kernel.registry import STAGES, Module, OnboardStep

m = Module("onboard", "Đưa vào dùng", pack="base",
           resources={"onboard": "Đưa công ty vào dùng: lượt chuyển dữ liệu, đối chiếu, danh sách việc, ngày chuyển (số cả công ty)"})

KINDS = {"mock": "Chuyển thử", "final": "Lượt cuối"}
RUN_STATUS = {"running": "Đang chạy", "done": "Xong", "failed": "Lỗi"}
REASONS = {"bad_source": "Dữ liệu nguồn sai / thiếu", "missing_ref": "Thiếu bản ghi được tham chiếu", "duplicate": "Trùng mã nguồn trong lượt",
           "rejected": "Lệnh từ chối", "needs_approval": "Lệnh cần duyệt — không nạp qua duyệt", "changed_after_load": "Nguồn đổi sau khi đã nạp"}
STEP_STATUS = {"todo": "Chưa xong", "done": "Đã xong", "skipped": "Bỏ qua"}
MIN_MOCK_RUNS = 2   # ADR 0263 chặng 3: ít nhất hai lượt chuyển thử đạt (không lệch, hoặc lệch đã ký) trước lượt cuối

# Bộ việc MẶC ĐỊNH của lõi theo 7 chặng (ADR 0263 mục A). Việc ``auto`` do code kiểm (không người đánh dấu).
CORE_STEPS: tuple[OnboardStep, ...] = (
    OnboardStep("fit_gap", 1, "Bảng fit-gap: cho khách xem quy trình có sẵn của gói, ghi phần lệch (chuẩn / cấu hình / phát triển)",
                evidence="Tệp hoặc đường dẫn bảng fit-gap"),
    OnboardStep("process_signed", 1, "Khách ký quy trình vận hành trên Barista", evidence="Biên bản quy trình đã ký"),
    OnboardStep("configured", 2, "Cấu hình công ty xong: vai, quyền, chuỗi duyệt, cài đặt, từ vựng, mẫu quy trình, luật chia lead",
                evidence="Ai rà, ngày rà"),
    OnboardStep("people", 2, "Mời đủ người dùng thật với đúng vai và đội", evidence="Số người đã mời / còn thiếu"),
    OnboardStep("mock_runs", 3, f"Ít nhất {MIN_MOCK_RUNS} lượt chuyển thử đạt đối chiếu (không lệch, hoặc lệch đã ký)"),
    OnboardStep("uat", 4, "Người dùng thật chạy trọn nghiệp vụ trên dữ liệu đã chuyển — biên bản kiểm thử chấp nhận", evidence="Biên bản kiểm thử"),
    OnboardStep("training", 5, "Đào tạo theo vai: mỗi vai một buổi trên màn của vai", evidence="Danh sách người đã học"),
    OnboardStep("legacy_frozen", 6, "Khoá nhập liệu ở hệ cũ (chỉ đọc) trước lượt chuyển cuối", evidence="Lúc khoá, ai khoá"),
    OnboardStep("quiet_check", 6, "Kiểm im sau nạp đạt trên bản sao DB (scripts/check_quiet_after_load.py in IM)", evidence="Lúc chạy, kết quả"),
    OnboardStep("go_no_go", 6, "Họp go / no-go: quyết định chuyển, người quyết", evidence="Biên bản go / no-go"),
    OnboardStep("automations_on", 7, "Bật các luật tự động đang chạy thử (Quy trình › Luật tự động) sau ngày chuyển", required=False),
    OnboardStep("hypercare", 7, "Trực sau chuyển 2–4 tuần: nhật ký sự cố, mức dùng màn (Báo cáo chạy song song, Sức khoẻ dữ liệu)", required=False,
                evidence="Nhật ký sự cố"),
)
AUTO = {"base.mock_runs"}


def steps_for(on: frozenset[str] | set[str]) -> list[tuple[str, OnboardStep]]:
    """(khoá đầy đủ, việc) của lõi + của các gói ``on`` — theo chặng, lõi trước, rồi theo gói."""
    out = [(f"base.{s.key}", s) for s in CORE_STEPS]
    out += [(f"{p.key}.{s.key}", s) for p in sorted(registry.PACKS.values(), key=lambda p: p.key) if p.key in on and p.key != "base"
            for s in p.onboard_steps]
    return sorted(out, key=lambda ks: ks[1].stage)


def cutovers_for(on: frozenset[str] | set[str]) -> list[registry.Cutover]:
    return sorted((c for c in registry.CUTOVERS.values() if packs.module_on(frozenset(on), c.module)), key=lambda c: (c.order, c.key))


# ── Sổ sách của lượt chuyển: lệnh NỘI BỘ, chỉ bộ chạy ``barista.api.onboard.load`` gọi bằng quyền hệ thống ──

Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]   # khoá nguồn / thực thể (khớp CHECK của bảng)


class OpenRun(Input):
    source: Key = field("Nguồn")
    kind: Literal["mock", "final"] = field("Loại lượt")
    source_cut_at: AwareDatetime | None = field("Mốc dữ liệu nguồn", None)
    mapping_version: str = field("Phiên bản ánh xạ", "", max_length=80)
    by_user: UUID = field("Người chạy", hidden=True)


@m.command("open_run", resource="onboard", action="manage", title="Mở lượt chuyển dữ liệu", internal=True, agent="deny")
async def open_run(ctx: Ctx, inp: OpenRun) -> dict[str, Any]:
    """Một lượt chuyển của một nguồn. Lượt đang chạy của cùng nguồn (bộ chạy chết giữa chừng) được đóng là lỗi — lượt mới thay."""
    await ctx.exec("UPDATE onboard.runs SET status = 'failed', error = 'Bị thay bằng lượt mới (lượt cũ không chạy xong)', finished_at = now()"
                   " WHERE source = %s AND status = 'running'", (inp.source,))
    rid = await ctx.val("""INSERT INTO onboard.runs (source, kind, source_cut_at, mapping_version, by_user) VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                        (inp.source, inp.kind, inp.source_cut_at, inp.mapping_version, inp.by_user))
    return {"run_id": rid}


class MapIn(Input):
    source: Key = field("Nguồn")
    entity: Key = field("Thực thể")


@m.query("source_map", resource="onboard", title="Sổ mã gốc của một thực thể", internal=True, agent="deny")
async def source_map(ctx: Ctx, inp: MapIn) -> dict[str, Any]:
    rows = await ctx.all("SELECT source_id, target_id, fingerprint FROM onboard.source_ids WHERE source = %s AND entity = %s", (inp.source, inp.entity))
    return {"rows": rows}


class IdRow(BaseModel):
    source_id: str
    target_id: UUID
    fingerprint: str


class PutIds(Input):
    run_id: UUID = field("Lượt", hidden=True)
    source: Key = field("Nguồn")
    entity: Key = field("Thực thể")
    rows: list[IdRow] = field("Dòng", [])


@m.command("put_ids", resource="onboard", action="manage", title="Ghi sổ mã gốc", internal=True, agent="deny")
async def put_ids(ctx: Ctx, inp: PutIds) -> dict[str, Any]:
    """Thêm mã gốc mới; mã đã có ⇒ cập nhật dấu vân tay + lượt (sau lệnh cập nhật). Không đổi mã đích của mã gốc đã có (một mã gốc một bản ghi)."""
    n = 0
    for r in inp.rows:
        n += await ctx.exec("""INSERT INTO onboard.source_ids (source, entity, source_id, target_id, fingerprint, run_id) VALUES (%s, %s, %s, %s, %s, %s)
                               ON CONFLICT (tenant_id, source, entity, source_id) DO UPDATE SET fingerprint = EXCLUDED.fingerprint, run_id = EXCLUDED.run_id,
                                   updated_at = now() WHERE onboard.source_ids.target_id = EXCLUDED.target_id""",
                            (inp.source, inp.entity, r.source_id, r.target_id, r.fingerprint, inp.run_id))
    return {"written": n}


class ExceptionRow(BaseModel):
    entity: str
    source_id: str = ""
    reason: Literal["bad_source", "missing_ref", "duplicate", "rejected", "needs_approval", "changed_after_load"]
    detail: str = ""


class AddExceptions(Input):
    run_id: UUID = field("Lượt", hidden=True)
    rows: list[ExceptionRow] = field("Dòng", [])


@m.command("add_exceptions", resource="onboard", action="manage", title="Ghi ngoại lệ của lượt chuyển", internal=True, agent="deny")
async def add_exceptions(ctx: Ctx, inp: AddExceptions) -> dict[str, Any]:
    for r in inp.rows:
        await ctx.exec("INSERT INTO onboard.exceptions (run_id, entity, source_id, reason, detail) VALUES (%s, %s, %s, %s, %s)",
                       (inp.run_id, r.entity, r.source_id[:200], r.reason, r.detail[:2000]))
    return {"written": len(inp.rows)}


class CountIn(Input):
    source: Key = field("Nguồn")
    entity: Key = field("Thực thể")


@m.query("loaded_count", resource="onboard", title="Số bản ghi đã nạp của một thực thể", internal=True, agent="deny")
async def loaded_count(ctx: Ctx, inp: CountIn) -> dict[str, Any]:
    return {"n": await ctx.val("SELECT count(*) FROM onboard.source_ids WHERE source = %s AND entity = %s", (inp.source, inp.entity))}


class ReconRow(BaseModel):
    metric: str
    label: str = ""
    period: str = ""
    source_value: float
    target_value: float
    tolerance_pct: float = 0
    status: Literal["khop", "lech"]


class FinishRun(Input):
    run_id: UUID = field("Lượt", hidden=True)
    status: Literal["done", "failed"] = field("Trạng thái")
    error: str = field("Lỗi", "", max_length=2000)
    counts: dict[str, int] = field("Số đếm", {})
    recon: list[ReconRow] = field("Đối chiếu", [])


@m.command("finish_run", resource="onboard", action="manage", title="Đóng lượt chuyển dữ liệu", internal=True, agent="deny")
async def finish_run(ctx: Ctx, inp: FinishRun) -> dict[str, Any]:
    row = await ctx.one("SELECT status FROM onboard.runs WHERE id = %s FOR UPDATE", (inp.run_id,))
    if row is None:
        raise NotFound("Không có lượt chuyển này")
    if row["status"] != "running":
        raise Conflict("Lượt chuyển này đã đóng")
    for r in inp.recon:
        await ctx.exec("""INSERT INTO onboard.recon (run_id, metric, label, period, source_value, target_value, tolerance_pct, status)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                       (inp.run_id, r.metric, r.label[:160], r.period, r.source_value, r.target_value, r.tolerance_pct, r.status))
    await ctx.exec("UPDATE onboard.runs SET status = %s, error = %s, counts = %s, finished_at = now() WHERE id = %s",
                   (inp.status, inp.error, Jsonb(inp.counts), inp.run_id))
    return {"run_id": inp.run_id, "mismatches": sum(1 for r in inp.recon if r.status == "lech")}


# ── Đọc: lượt chuyển, đối chiếu, ngoại lệ ──

RUN_ROWS = """SELECT r.id, r.source, r.kind, r.status, r.started_at, r.finished_at, r.counts, r.error, r.mapping_version, r.source_cut_at,
                     u.name AS by_name,
                     (SELECT count(*) FROM onboard.recon c WHERE c.run_id = r.id AND c.status = 'lech') AS mismatches,
                     (SELECT count(*) FROM onboard.exceptions e WHERE e.run_id = r.id) AS exceptions,
                     s.signed_at, su.name AS signed_name, au.name AS approved_name
              FROM onboard.runs r LEFT JOIN core.users u ON u.id = r.by_user
              LEFT JOIN onboard.signoffs s ON s.run_id = r.id LEFT JOIN core.users su ON su.id = s.signed_by
              LEFT JOIN core.users au ON au.id = s.approved_by[cardinality(s.approved_by)]"""


def _passed(r: dict[str, Any]) -> bool:
    """Lượt ĐẠT: chạy xong, và không còn chỉ số lệch hoặc lệch đã có người ký."""
    return r["status"] == "done" and (not r["mismatches"] or r["signed_at"] is not None)


@m.query("runs", resource="onboard", title="Lượt chuyển dữ liệu", min_scope="all")
async def runs(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Mọi lượt chuyển dữ liệu từ hệ cũ: thử / cuối, số tạo / cập nhật / bỏ qua / ngoại lệ, chỉ số lệch, ai ký."""
    rows = await ctx.all(RUN_ROWS + " ORDER BY r.started_at DESC LIMIT 200")
    out = []
    for r in rows:
        c = r["counts"] or {}
        out.append({"id": r["id"], "started_at": r["started_at"], "source": r["source"], "kind": r["kind"], "status": r["status"],
                    "created": c.get("created", 0), "updated": c.get("updated", 0), "skipped": c.get("skipped", 0), "exceptions": r["exceptions"],
                    "mismatches": r["mismatches"], "signed": "signed" if r["signed_at"] else ("none" if not r["mismatches"] else "waiting"),
                    "by_name": r["by_name"] or "—", "can_sign": "yes" if r["status"] == "done" and r["signed_at"] is None else "no"})
    return ui.table(out, [ui.col("started_at", "Lúc chạy", "datetime"), ui.col("source", "Nguồn"),
                          ui.col("kind", "Loại", "badge", labels=KINDS, tones={"mock": "gray", "final": "blue"}),
                          ui.col("status", "Trạng thái", "badge", labels=RUN_STATUS, tones={"running": "orange", "done": "green", "failed": "red"}),
                          ui.col("created", "Tạo", "number"), ui.col("updated", "Cập nhật", "number"), ui.col("skipped", "Giữ nguyên", "number"),
                          ui.col("exceptions", "Ngoại lệ", "number"), ui.col("mismatches", "Chỉ số lệch", "number"),
                          ui.col("signed", "Ký đối chiếu", "badge", labels={"signed": "Đã ký", "none": "Không lệch", "waiting": "Chờ ký"},
                                 tones={"signed": "green", "none": "gray", "waiting": "orange"}),
                          ui.col("by_name", "Người chạy")],
                    title="Lượt chuyển dữ liệu", limit=200,
                    empty="Chưa có lượt chuyển nào — bộ chuyển chạy bằng mã trong tiến trình (runbook 23), không chạy từ màn này",
                    note="Chạy lại cùng nguồn không tạo trùng: bản ghi đã nạp có dấu vân tay giữ nguyên thì bỏ qua; nguồn đổi thì cập nhật hoặc thành ngoại lệ.")


class RunIn(Input):
    run_id: UUID = field("Lượt chuyển", hidden=True)


@m.query("run", resource="onboard", title="Biên bản đối chiếu một lượt chuyển", min_scope="all")
async def run(ctx: Ctx, inp: RunIn) -> dict[str, Any]:
    """Một lượt chuyển: số tạo / cập nhật / giữ nguyên / ngoại lệ, từng chỉ số đối chiếu (nguồn ↔ Barista, theo tháng nếu khai, ngưỡng), ngoại lệ theo lý
    do, ai ký."""
    r = await ctx.one(RUN_ROWS + " WHERE r.id = %s", (inp.run_id,))
    if r is None:
        raise NotFound("Không có lượt chuyển này")
    c = r["counts"] or {}
    recon = await ctx.all("SELECT metric, label, period, source_value, target_value, tolerance_pct, status FROM onboard.recon WHERE run_id = %s"
                          " ORDER BY status DESC, metric, period", (inp.run_id,))
    reasons = await ctx.all("SELECT reason, count(*) AS n FROM onboard.exceptions WHERE run_id = %s GROUP BY reason ORDER BY n DESC", (inp.run_id,))
    blocks: list[dict[str, Any]] = [ui.b_kpis([
        ui.kpi("Tạo mới", c.get("created", 0)), ui.kpi("Cập nhật", c.get("updated", 0)), ui.kpi("Giữ nguyên", c.get("skipped", 0)),
        ui.kpi("Ngoại lệ", r["exceptions"], tone="orange" if r["exceptions"] else "green"),
        ui.kpi("Chỉ số lệch", r["mismatches"], tone="red" if r["mismatches"] else "green")])]
    if r["status"] == "failed":
        blocks.append(ui.b_callout("warn", "Lượt chuyển lỗi giữa chừng", r["error"] or "Không rõ lỗi",
                                   facts=["Phần đã ghi giữ nguyên (sổ mã gốc chống trùng) — sửa lỗi rồi chạy lại cùng nguồn."]))
    if r["signed_at"]:
        blocks.append(ui.b_callout("ok", "Đã ký biên bản đối chiếu",
                                   f"{r['signed_name'] or '—'} ký lúc {r['signed_at'].astimezone(zone()):%H:%M %d/%m/%Y}"
                                   + (f", {r['approved_name']} duyệt" if r["approved_name"] else "")))
    elif r["status"] == "done" and r["mismatches"]:
        blocks.append(ui.b_callout("warn", "Còn chỉ số lệch chưa ký", "Xem từng dòng lệch và ngoại lệ; chấp nhận thì ký biên bản (qua chuỗi duyệt của công ty)."))
    rows = [{"id": f"{x['metric']}|{x['period']}", "label": x["label"] or x["metric"], "period": x["period"] or "Cả lượt",
             "source_value": float(x["source_value"]), "target_value": float(x["target_value"]),
             "diff": float(x["target_value"]) - float(x["source_value"]), "tolerance": f"± {float(x['tolerance_pct']):g} %", "status": x["status"]}
            for x in recon]
    blocks.append(ui.b_table(ui.table(rows, [ui.col("label", "Chỉ số", width=260), ui.col("period", "Kỳ"), ui.col("source_value", "Hệ cũ", "number"),
                                             ui.col("target_value", "Barista", "number"), ui.col("diff", "Chênh", "number"), ui.col("tolerance", "Ngưỡng"),
                                             ui.col("status", "Kết quả", "badge", labels={"khop": "Khớp", "lech": "Lệch"}, tones={"khop": "green", "lech": "red"})],
                                      title="Đối chiếu nguồn ↔ Barista", empty="Lượt này chưa có chỉ số đối chiếu")))
    if reasons:
        blocks.append(ui.b_list([{"title": REASONS.get(x["reason"], x["reason"]), "subtitle": f"{x['n']} dòng", "tone": "orange"} for x in reasons],
                                "Ngoại lệ theo lý do"))
    actions = [ui.act("onboard.sign", "Ký biên bản đối chiếu", {"run_id": str(inp.run_id)}, "primary")] \
        if r["status"] == "done" and r["signed_at"] is None and ctx.p.can("onboard", "manage") else []
    return ui.card(f"{KINDS[r['kind']]} · {r['source']}", blocks, icon="arrow.left.arrow.right",
                   subtitle=f"Chạy lúc {r['started_at'].astimezone(zone()):%H:%M %d/%m/%Y} bởi {r['by_name'] or '—'}"
                            + (f" · ánh xạ {r['mapping_version']}" if r["mapping_version"] else "")
                            + (f" · dữ liệu nguồn tới {r['source_cut_at'].astimezone(zone()):%H:%M %d/%m/%Y}" if r["source_cut_at"] else ""),
                   actions=actions, open=[{"title": "Xem các dòng ngoại lệ", "query": "onboard.exceptions", "input": {"run_id": str(inp.run_id)}}])


@m.query("exceptions", resource="onboard", title="Ngoại lệ của một lượt chuyển", min_scope="all")
async def exceptions(ctx: Ctx, inp: RunIn) -> dict[str, Any]:
    """Dòng nguồn KHÔNG vào Barista ở lượt này: thực thể, mã ở hệ cũ, lý do, chi tiết — không tự điền thay dữ liệu thiếu."""
    rows = await ctx.all("SELECT id, entity, source_id, reason, detail, at FROM onboard.exceptions WHERE run_id = %s ORDER BY entity, id LIMIT 2000",
                         (inp.run_id,))
    return ui.table(rows, [ui.col("entity", "Thực thể"), ui.col("source_id", "Mã ở hệ cũ"), ui.col("reason", "Lý do", "badge", labels=REASONS),
                           ui.col("detail", "Chi tiết", width=420)], title="Ngoại lệ", limit=2000, empty="Lượt này không có ngoại lệ")


# ── Ký biên bản đối chiếu ──

class SignIn(Input):
    run_id: UUID = field("Lượt chuyển", hidden=True)
    note: str = field("Ghi chú khi ký", "", max_length=1000, help="Vd chấp nhận lệch tháng 3 do hệ cũ ghi trùng phiếu thu")


@m.command("sign", resource="onboard", action="manage", title="Ký biên bản đối chiếu", agent="deny",
           purpose="Xác nhận số liệu chuyển từ hệ cũ đã được đối chiếu và chấp nhận — điều kiện để được chuyển sang Barista.",
           irreversible=True, effect="Biên bản ghi tên người ký và người duyệt; không gỡ chữ ký được — số lệch về sau phải chạy lượt mới và ký lại.")
async def sign(ctx: Ctx, inp: SignIn) -> dict[str, Any]:
    """Ký một lượt đã chạy xong (kể cả còn chỉ số lệch — người ký chấp nhận chúng). Qua chuỗi duyệt ``onboard_signoff`` của công ty; người duyệt ghi tên."""
    r = await ctx.one("SELECT status, (SELECT count(*) FROM onboard.recon c WHERE c.run_id = r.id AND c.status = 'lech') AS mismatches"
                      " FROM onboard.runs r WHERE id = %s FOR UPDATE", (inp.run_id,))
    if r is None:
        raise NotFound("Không có lượt chuyển này")
    if r["status"] != "done":
        raise Invalid("Chỉ ký lượt đã chạy xong")
    if await ctx.val("SELECT 1 FROM onboard.signoffs WHERE run_id = %s", (inp.run_id,)):
        raise Conflict("Lượt này đã ký")
    await ctx.exec("INSERT INTO onboard.signoffs (run_id, signed_by, approved_by, note, mismatches) VALUES (%s, %s, %s, %s, %s)",
                   (inp.run_id, ctx.p.user_id, list(ctx.p.approved_by), inp.note.strip(), r["mismatches"]))
    return {"run_id": inp.run_id, "summary": f"Đã ký biên bản đối chiếu ({r['mismatches']} chỉ số lệch được chấp nhận)" if r["mismatches"]
            else "Đã ký biên bản đối chiếu (mọi chỉ số khớp)"}


# ── Danh sách việc ──

async def _on(ctx: Ctx) -> frozenset[str]:
    return await packs.enabled(ctx.conn, ctx.p.tenant_id)


async def _runs_state(ctx: Ctx) -> list[dict[str, Any]]:
    return await ctx.all(RUN_ROWS + " ORDER BY r.started_at DESC")


def _auto_done(key: str, runs_: list[dict[str, Any]]) -> tuple[bool, str]:
    if key == "base.mock_runs":
        n = sum(1 for r in runs_ if r["kind"] == "mock" and _passed(r))
        return n >= MIN_MOCK_RUNS, f"{n} lượt chuyển thử đạt"
    return False, ""


async def _steps(ctx: Ctx) -> list[dict[str, Any]]:
    on = await _on(ctx)
    have = {r["key"]: r for r in await ctx.all("""SELECT c.key, c.status, c.evidence, c.confirmed_at, u.name AS who FROM onboard.checklist c
                                                  LEFT JOIN core.users u ON u.id = c.confirmed_by""")}
    roles = {r["key"]: r["label"] for r in await ctx.all("SELECT key, label FROM core.roles")}
    runs_ = await _runs_state(ctx)
    out = []
    for key, s in steps_for(on):
        h = have.get(key) or {}
        status, evidence = h.get("status", "todo"), h.get("evidence", "")
        if key in AUTO:
            done, evidence = _auto_done(key, runs_)
            status = "done" if done else "todo"
        out.append({"id": key, "stage": s.stage, "stage_label": f"{s.stage}. {STAGES[s.stage]}", "title": s.title,
                    "role": roles.get(s.role, s.role) if s.role else "Quản trị", "required": "yes" if s.required else "no", "status": status,
                    "evidence": evidence, "evidence_hint": s.evidence, "who": h.get("who") or ("Hệ thống kiểm" if key in AUTO else ""),
                    "at": h.get("confirmed_at"), "auto": key in AUTO})
    return out


async def blockers(ctx: Ctx) -> list[str]:
    """Vì sao CHƯA chuyển được (rỗng = được bấm ngày chuyển). Một nơi tính — màn và lệnh cùng dùng."""
    out = [f"Việc bắt buộc chưa xong: {s['title']}" for s in await _steps(ctx) if s["required"] == "yes" and s["status"] != "done"]
    final = next((r for r in await _runs_state(ctx) if r["kind"] == "final"), None)
    if final is None:
        out.append("Chưa có lượt chuyển cuối (final)")
    elif final["status"] != "done":
        out.append(f"Lượt chuyển cuối mới nhất {RUN_STATUS[final['status']].lower()} — chạy lại lượt cuối")
    elif final["mismatches"] and final["signed_at"] is None:
        out.append(f"Lượt chuyển cuối còn {final['mismatches']} chỉ số lệch chưa ký")
    return out


async def _live(ctx: Ctx) -> dict[str, Any] | None:
    return await ctx.one("SELECT l.cut_date, l.live_at, l.steps, u.name AS by_name FROM onboard.live l LEFT JOIN core.users u ON u.id = l.by_user")


@m.query("checklist", resource="onboard", title="Đưa công ty vào dùng", min_scope="all")
async def checklist(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Danh sách việc theo 7 chặng (khảo sát → cấu hình → chuyển thử → kiểm thử → đào tạo → ngày chuyển → trực sau chuyển): ai chịu trách nhiệm, bắt
    buộc hay không, bằng chứng, ai xác nhận — và vì sao chưa chuyển được."""
    rows = await _steps(ctx)
    live = await _live(ctx)
    header: list[dict[str, Any]] = []
    unavailable: dict[str, str] = {}
    if live:
        header.append(ui.b_callout("ok", f"Đã chuyển sang Barista từ ngày {live['cut_date']:%d/%m/%Y}",
                                   f"{live['by_name'] or '—'} bấm lúc {live['live_at'].astimezone(zone()):%H:%M %d/%m/%Y}",
                                   facts=[f"{x['label']}: {x['summary']}" for x in live["steps"]]))
        unavailable["onboard.go_live"] = "Công ty đã chuyển"
    elif why := await blockers(ctx):
        header.append(ui.b_callout("warn", "Chưa chuyển sang Barista được", facts=why))
        unavailable["onboard.go_live"] = why[0]
    else:
        header.append(ui.b_callout("ok", "Đủ điều kiện chuyển", "Chủ tài khoản bấm “Chuyển sang Barista” (qua chuỗi duyệt của công ty)."))
    done = sum(1 for r in rows if r["status"] == "done")
    return ui.table(rows, [ui.col("stage_label", "Chặng", width=170), ui.col("title", "Việc", width=420), ui.col("role", "Phụ trách"),
                           ui.col("required", "Bắt buộc", "badge", labels={"yes": "Bắt buộc", "no": "Nên có"}, tones={"yes": "blue", "no": "gray"}),
                           ui.col("status", "Trạng thái", "badge", labels=STEP_STATUS, tones={"todo": "orange", "done": "green", "skipped": "gray"}),
                           ui.col("evidence", "Bằng chứng", width=240), ui.col("who", "Người xác nhận"), ui.col("at", "Lúc", "datetime")],
                    title="Đưa công ty vào dùng", header=header, unavailable=unavailable,
                    kpis=[ui.kpi("Việc đã xong", f"{done}/{len(rows)}", "text"),
                          ui.kpi("Bắt buộc còn lại", sum(1 for r in rows if r["required"] == "yes" and r["status"] != "done"))],
                    note="Việc của gói chỉ hiện khi công ty bật gói. Việc “Hệ thống kiểm” tự đạt theo lượt chuyển dữ liệu.")


class MarkStep(Input):
    key: str = field("Việc", hidden=True)
    status: Literal["done", "skipped", "todo"] = field("Trạng thái", "done", options=STEP_STATUS)
    evidence: str = field("Bằng chứng", "", max_length=500, help="Biên bản, tệp, đường dẫn, số đếm — người khác đọc là kiểm lại được")
    run_id: UUID | None = field("Lượt chuyển liên quan", None, hidden=True)


@m.command("mark_step", resource="onboard", action="manage", title="Xác nhận việc onboard", agent="propose", risk="low",
           purpose="Ghi một việc trong danh sách đưa công ty vào dùng đã xong (kèm bằng chứng) để biết còn thiếu gì trước ngày chuyển.",
           reversible="Đánh dấu lại “Chưa xong”")
async def mark_step(ctx: Ctx, inp: MarkStep) -> dict[str, Any]:
    step = dict(steps_for(await _on(ctx))).get(inp.key)
    if step is None:
        raise NotFound("Không có việc này trong danh sách của công ty")
    if inp.key in AUTO:
        raise Invalid("Việc này hệ thống tự kiểm theo lượt chuyển dữ liệu — không đánh dấu tay")
    if inp.status == "skipped" and step.required:
        raise Invalid("Việc bắt buộc không bỏ qua được")
    if inp.status == "done" and step.evidence and not inp.evidence.strip():
        raise Invalid(f"Cần bằng chứng: {step.evidence}")
    if await ctx.val("SELECT 1 FROM onboard.live"):
        raise Conflict("Công ty đã chuyển — danh sách đã chốt")
    await ctx.exec("""INSERT INTO onboard.checklist (key, status, evidence, run_id, confirmed_by) VALUES (%s, %s, %s, %s, core.me())
                      ON CONFLICT (tenant_id, key) DO UPDATE SET status = EXCLUDED.status, evidence = EXCLUDED.evidence, run_id = EXCLUDED.run_id,
                          confirmed_by = EXCLUDED.confirmed_by, confirmed_at = now()""",
                   (inp.key, inp.status, inp.evidence.strip(), inp.run_id))
    return {"key": inp.key, "summary": f"{step.title}: {STEP_STATUS[inp.status].lower()}"}


# ── Ngày chuyển ──

class GoLive(Input):
    cut_date: Day = field("Ngày chuyển (ngày đầu làm việc trên Barista)", example="01/12/2026",
                          help="Dữ liệu trước ngày này là của hệ cũ; kỳ kế toán khoá tới hết ngày hôm trước")
    note: str = field("Ghi chú", "", max_length=1000, example="Theo biên bản go / no-go ngày 30/11")


async def _gate(ctx: Ctx, inp: GoLive) -> str:
    """Kiểm TRƯỚC khi gửi phiếu duyệt (không để người duyệt nhận phiếu chắc chắn bị từ chối) và lại ngay trước khi chạy (lệnh sau duyệt)."""
    if not await ctx.val("SELECT owner FROM core.memberships WHERE user_id = core.me() AND active"):
        raise Denied("Chỉ chủ tài khoản của công ty bấm ngày chuyển")
    if live := await _live(ctx):
        raise Conflict(f"Công ty đã chuyển sang Barista từ ngày {live['cut_date']:%d/%m/%Y}")
    if inp.cut_date > today():
        raise Invalid("Ngày chuyển không sau hôm nay — bấm vào đúng ngày chuyển (hoặc sau)")
    if inp.cut_date < today() - timedelta(days=31):
        raise Invalid("Ngày chuyển lùi quá 31 ngày — kiểm lại ngày")
    if why := await blockers(ctx):
        raise Invalid("Chưa chuyển được: " + "; ".join(why))
    steps = [c.label for c in cutovers_for(await _on(ctx))]
    return f"Chuyển sang Barista từ ngày {inp.cut_date:%d/%m/%Y}" + (f" — chạy: {', '.join(steps)}" if steps else "")


async def _run_step(ctx: Ctx, c: registry.Cutover, cut_date: Any) -> dict[str, Any]:
    """Một bước ngày chuyển (lệnh nội bộ của module khai, hợp đồng ``callers=("onboard",)``). Gói tắt ⇒ bỏ qua; lỗi ⇒ dừng cả ngày chuyển."""
    try:
        got = await ctx.call_system_optional(c.command, cut_date=cut_date)
    except Exception as e:
        raise Invalid(f"Bước “{c.label}” không chạy được: {e}") from e
    if got is None:
        return {"key": c.key, "label": c.label, "status": "skipped", "summary": "gói chưa bật"}
    return {"key": c.key, "label": c.label, "status": "skipped" if got.get("skipped") else "done", "summary": str(got.get("summary") or "xong")}


@m.command("go_live", resource="onboard", action="manage", title="Chuyển sang Barista", agent="deny", sensitive=_gate,
           purpose="Chốt ngày công ty bắt đầu làm việc trên Barista: khoá kỳ cũ, chạy các bước chuyển của các gói, ghi ngày chuyển.",
           irreversible=True, effect="Các bước chuyển (khoá kỳ kế toán tới hết ngày hôm trước…) chạy ngay; không có hoàn tác ngày chuyển — "
                                    "tạm dừng tự động hoá bằng lệnh sẵn có nếu cần.")
async def go_live(ctx: Ctx, inp: GoLive) -> dict[str, Any]:
    """Chỉ chủ tài khoản; trợ lý không làm; qua chuỗi duyệt ``onboard_go_live``. Kiểm lại mọi điều kiện, chạy các bước ngày chuyển theo thứ tự trong cùng
    giao dịch (một bước lỗi ⇒ không bước nào ghi), ghi ngày chuyển + kết quả từng bước."""
    await ctx.exec("SELECT pg_advisory_xact_lock(hashtextextended('onboard-go-live:' || core.tenant()::text, 0))")
    await _gate(ctx, inp)
    done = [await _run_step(ctx, c, inp.cut_date) for c in cutovers_for(await _on(ctx))]
    final = await ctx.val("SELECT id FROM onboard.runs WHERE kind = 'final' ORDER BY started_at DESC LIMIT 1")
    await ctx.exec("INSERT INTO onboard.live (cut_date, by_user, approved_by, run_id, steps, note) VALUES (%s, core.me(), %s, %s, %s, %s)",
                   (inp.cut_date, list(ctx.p.approved_by), final, Jsonb(done), inp.note.strip()))
    return {"cut_date": inp.cut_date, "steps": done, "summary": f"Đã chuyển sang Barista từ ngày {inp.cut_date:%d/%m/%Y} ({len(done)} bước chuyển)"}


m.screens("admin", (
    Screen("onboard", "Đưa công ty vào dùng", "checklist", "onboard.checklist", group="Đưa vào dùng", tint="blue", min_scope="all", pinnable=False,
           about="Danh sách việc 7 chặng từ khảo sát tới trực sau chuyển, và nút chuyển sang Barista khi đủ điều kiện",
           actions=(A("onboard.go_live", "Chuyển sang Barista", style="primary"),),
           row_actions=(A("onboard.mark_step", "Đã xong", bind={"key": "id"}, prefill={"evidence": "evidence"}, preset={"status": "done"},
                          when={"status": ["todo", "skipped"], "auto": [False]}, style="primary"),
                        A("onboard.mark_step", "Bỏ qua", bind={"key": "id"}, preset={"status": "skipped"}, when={"status": ["todo"], "required": ["no"],
                                                                                                                 "auto": [False]}),
                        A("onboard.mark_step", "Mở lại", bind={"key": "id"}, preset={"status": "todo"}, when={"status": ["done", "skipped"], "auto": [False]}))),
    Screen("onboard_runs", "Lượt chuyển dữ liệu", "arrow.left.arrow.right", "onboard.runs", group="Đưa vào dùng", tint="indigo", min_scope="all",
           pinnable=False, about="Mỗi lần nạp dữ liệu từ hệ cũ: số tạo / cập nhật / ngoại lệ, đối chiếu với hệ cũ, ai ký",
           open=Open("onboard.run", {"run_id": "id"}, "Biên bản đối chiếu"), row_click="open",
           row_actions=(A("onboard.sign", "Ký đối chiếu", bind={"run_id": "id"}, when={"can_sign": ["yes"]}, style="primary"),)),
), rank=30)
