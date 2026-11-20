"""HẠNG TRỢ LÝ AI THEO VAI (ADR 0193) — CHÍNH SÁCH CỦA NHÀ VẬN HÀNH, không phải của khách (quyết định chủ sản phẩm 12/11/2026): mỗi hạng một model (hoặc
model mặc định của bản cài), danh sách model nhà vận hành có thể đặt riêng cho một người trong hạng, HẠN MỨC TOKEN của mỗi người / tháng (mọi token trừ token
đọc cache — ``usage.quota_tokens``), có được nói chuyện bằng giọng (Gemini Live) không; mỗi vai thuộc một hạng (vai chưa gán ⇒ hạng mặc định).

Tài nguyên ``core.ai_policy`` không vai nào của khách cấp được (``registry.OPERATOR_RESOURCES`` + trigger migration base/082) ⇒ quản trị công ty không
thấy / sửa hạng, model, hạn mức, báo cáo token; khách chỉ thấy số LƯỢT trợ lý (``core.ai_usage``). Nhà vận hành chạy bằng quyền hệ thống qua
``python -m barista.ai_policy`` (cùng executor — một cửa), mỗi lệnh ghi người vận hành + lý do vào nhật ký. Trợ lý không làm thay (``agent="deny"``).

Bộ mặc định đi kèm gói (``Pack(records=…)`` của gói nền + ``Role(ai_tier=…)`` của vai) cài qua ``admin.install_defaults`` bằng hai thao tác NỘI BỘ
(``default_ai_tier`` / ``default_role_ai_tier`` — hợp đồng ``callers=("admin",)``): chỉ thêm phần thiếu, kể cả khi quản trị công ty là người bật gói.
``auth.load`` đọc hạng vào người gọi; ``llm.model_for`` / ``llm.voice_for`` là cửa áp.
"""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from barista.kernel import registry, ui
from barista.kernel.clock import today, zone
from barista.kernel.core_ops import m
from barista.kernel.errors import Invalid, NotFound
from barista.kernel.executor import Ctx
from barista.kernel.fields import Input, NoInput, field

POLICY = "core.ai_policy"
registry.OPERATOR_RESOURCES.add(POLICY)
KEY = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
SPEC = re.compile(r"^[a-z][a-z0-9-]*:[^\s:]+$")
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
# Hạng "basic" như bản 081 (df66d796) đã cài: model + trần 2 USD / người. Công ty còn ĐÚNG giá trị đó (chưa ai đổi) ⇒ cài mặc định mới nâng lên model
# và hạn mức token hiện hành; giá trị nhà vận hành đã đổi thì giữ.
SHIPPED_081 = {"model": "deepseek:deepseek-chat", "person_monthly_usd": Decimal("2.00")}
# vai → hạng: hạng mặc định của vai chưa gán (SQL chung cho bảng vai, bảng hạng, lệnh đặt model)
TIER_OF_ROLE = """SELECT t.* FROM core.ai_tiers t LEFT JOIN core.ai_role_tiers r ON r.tenant_id = t.tenant_id AND r.role = %s
                  WHERE t.tenant_id = core.tenant() AND (t.key = r.tier OR (r.tier IS NULL AND t.is_default))"""


def _llm() -> Any:
    from barista.agent import llm  # nạp muộn: kernel không phụ thuộc lớp model lúc khởi động

    return llm


def _spec(raw: str, what: str) -> str:
    spec = raw.strip()
    if not SPEC.match(spec) or not _llm().known_provider(spec):
        raise Invalid(f"{what} “{raw}” không hợp lệ — dạng nhà_cung_cấp:tên_model, vd deepseek:deepseek-flash, google:gemini-3.8-flash, "
                      "anthropic:claude-sonnet-5, openai:gpt-5.5")
    return spec


def _model_label(spec: str | None) -> str:
    from barista.config import settings

    if not spec:
        return f"Mặc định của bản cài ({settings.llm_model})"
    return spec if _llm().usable(spec) else f"{spec} — chưa có khoá, đang dùng mặc định ({settings.llm_model})"


def tokens_text(n: Any) -> str:
    return "Không giới hạn" if n is None else f"{int(n):,}".replace(",", ".") + " token"


def _by() -> Any:
    return field("Người vận hành", max_length=80, help="Ai đổi chính sách — ghi vào nhật ký vận hành")


def _why() -> Any:
    return field("Lý do", max_length=300, help="Vì sao đổi (hợp đồng, yêu cầu của khách…) — ghi vào nhật ký vận hành")


def _who(inp: Any) -> None:
    if not inp.by.strip() or not inp.why.strip():
        raise Invalid("Cần người vận hành và lý do (ghi vào nhật ký)")


class TierIn(Input):
    key: str = field("Mã hạng", max_length=40, example="basic", help="Chữ thường không dấu, gạch dưới — không đổi sau khi tạo")
    label: str = field("Tên hạng", max_length=80, example="Cơ bản")
    model: str = field("Model", "", max_length=120, placeholder="Mặc định của bản cài",
                       help="Dạng nhà_cung_cấp:tên_model (vd deepseek:deepseek-flash). Trống = model mặc định của bản cài. Nhà cung cấp chưa có khoá ở bản "
                            "cài ⇒ trợ lý dùng model mặc định. Model khác nhà cung cấp chính nghĩa là dữ liệu công ty được gửi tới nhà đó.")
    choices: list[str] = field("Model nhà vận hành được đặt riêng cho người trong hạng", [],
                               help="Mỗi dòng một chuỗi model; trống = không đặt riêng. Chỉ model có khoá ở bản cài mới dùng được.")
    person_monthly_tokens: int | None = field("Hạn mức token mỗi người / tháng", None, kind="number", ge=1000, le=10**12,
                                              help="Mọi token trừ token đọc cache (vào − cache đọc + ra). Trống = không giới hạn theo người (vẫn chịu trần cả "
                                                   "công ty). Hết hạn mức ⇒ trợ lý dừng với riêng người đó tới ngày 1 tháng sau.")
    voice: bool = field("Được nói chuyện bằng giọng", False, help="Gemini Live — chi phí theo phút nói chuyện, cao hơn chat chữ")
    is_default: bool = field("Hạng mặc định", False, help="Hạng của vai chưa gán hạng (mỗi công ty đúng một hạng mặc định)")
    sort: int = field("Thứ tự", 100, ge=0, le=10_000)
    by: str = _by()
    why: str = _why()


def _tier_row(inp: Any) -> tuple[str, str, str | None, list[str], int | None, bool, int]:
    key = inp.key.strip().lower()
    if not KEY.match(key) or not inp.label.strip():
        raise Invalid("Mã hạng: chữ thường không dấu, số, gạch dưới (bắt đầu bằng chữ, 2–40 ký tự); cần tên hạng")
    model = _spec(inp.model, "Model") if inp.model.strip() else None
    choices = list(dict.fromkeys(_spec(c, "Model được đặt riêng") for c in inp.choices if c.strip()))
    return key, inp.label.strip(), model, choices, inp.person_monthly_tokens, inp.voice, inp.sort


@m.command("save_ai_tier", resource=POLICY, action="manage", title="Thêm / sửa hạng trợ lý AI", agent="deny", category="setup")
async def save_ai_tier(ctx: Ctx, inp: TierIn) -> dict[str, Any]:
    """Một hạng trợ lý AI của công ty — NHÀ VẬN HÀNH đặt (model = nhà cung cấp nhận dữ liệu công ty, hạn mức = chi phí). Trợ lý không đổi thay."""
    _who(inp)
    key, label, model, choices, cap, voice, sort = _tier_row(inp)
    was = await ctx.one("SELECT is_default FROM core.ai_tiers WHERE key = %s", (key,))
    others = await ctx.val("SELECT count(*) FROM core.ai_tiers WHERE key <> %s AND is_default", (key,))
    if not inp.is_default and (was or {}).get("is_default"):
        raise Invalid("Công ty cần đúng một hạng mặc định (cho vai chưa gán hạng) — đặt hạng khác làm mặc định trước")
    default = inp.is_default or not others   # công ty chưa có hạng mặc định nào ⇒ hạng đầu tiên là mặc định
    if default:
        await ctx.exec("UPDATE core.ai_tiers SET is_default = false WHERE is_default AND key <> %s", (key,))
    # trần USD theo người của 081 thôi dùng (082) ⇒ luôn ghi NULL: một nguồn cho hạn mức theo người là token
    await ctx.exec("""INSERT INTO core.ai_tiers (key, label, model, choices, person_monthly_tokens, person_monthly_usd, voice, sort, is_default, updated_by)
                      VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, %s, core.me())
                      ON CONFLICT (tenant_id, key) DO UPDATE SET label = EXCLUDED.label, model = EXCLUDED.model, choices = EXCLUDED.choices,
                          person_monthly_tokens = EXCLUDED.person_monthly_tokens, person_monthly_usd = NULL, voice = EXCLUDED.voice,
                          sort = EXCLUDED.sort, is_default = EXCLUDED.is_default, updated_by = EXCLUDED.updated_by, updated_at = now()""",
                   (key, label, model, choices, cap, voice, sort, default))
    return {"key": key, "added": True,
            "summary": f"Hạng {label}: {_model_label(model)}; {tokens_text(cap)} / người / tháng; giọng nói {'có' if voice else 'không'}"}


class DefaultTierIn(Input):
    key: str = field("Mã hạng", max_length=40)
    label: str = field("Tên hạng", max_length=80)
    model: str = field("Model", "", max_length=120)
    choices: list[str] = field("Model đặt riêng", [])
    person_monthly_tokens: int | None = field("Hạn mức token mỗi người / tháng", None, ge=1000, le=10**12)
    voice: bool = field("Giọng nói", False)
    is_default: bool = field("Hạng mặc định", False)
    sort: int = field("Thứ tự", 100, ge=0, le=10_000)


@m.command("default_ai_tier", resource=POLICY, action="manage", title="Cài hạng trợ lý AI mặc định", internal=True, agent="deny", callers=("admin",),
           why="admin.install_defaults cài hạng mặc định gói nền khai (Pack(records=…)) cho công ty — kể cả khi quản trị công ty bật gói (họ không có "
               "core.ai_policy). Chỉ thêm hạng chưa có; hạng còn đúng giá trị bản 081 đã cài (model + trần 2 USD, chưa ai đổi) thì nâng lên model / "
               "hạn mức token hiện hành. Trả {key, added}.")
async def default_ai_tier(ctx: Ctx, inp: DefaultTierIn) -> dict[str, Any]:
    key, label, model, choices, cap, voice, sort = _tier_row(inp)
    n = await ctx.exec("""INSERT INTO core.ai_tiers (key, label, model, choices, person_monthly_tokens, voice, sort, is_default, updated_by)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s AND NOT EXISTS (SELECT 1 FROM core.ai_tiers WHERE is_default), core.me())
                          ON CONFLICT (tenant_id, key) DO NOTHING""", (key, label, model, choices, cap, voice, sort, inp.is_default))
    if n:
        return {"key": key, "added": True}
    # công ty có trước (081): chỉ chỗ còn ĐÚNG giá trị đã cài thì đổi — giá trị nhà vận hành / công ty đã sửa giữ nguyên
    row = await ctx.one("SELECT model, person_monthly_usd, person_monthly_tokens FROM core.ai_tiers WHERE key = %s", (key,))
    if row is None:
        return {"key": key, "added": False}
    up = 0
    if cap is not None and row["person_monthly_tokens"] is None and row["person_monthly_usd"] == SHIPPED_081["person_monthly_usd"]:
        up += await ctx.exec("UPDATE core.ai_tiers SET person_monthly_tokens = %s, person_monthly_usd = NULL, updated_at = now() WHERE key = %s",
                             (cap, key))
    if model is not None and model != row["model"] and row["model"] == SHIPPED_081["model"]:
        up += await ctx.exec("UPDATE core.ai_tiers SET model = %s, updated_at = now() WHERE key = %s", (model, key))
    return {"key": key, "added": bool(up)}


class RoleTierIn(Input):
    role: str = field("Vai trò", options_query="admin.role_options")
    tier: str = field("Hạng trợ lý AI", options_query="core.ai_tier_options")
    by: str = _by()
    why: str = _why()


@m.command("set_role_ai_tier", resource=POLICY, action="manage", title="Gán hạng trợ lý AI cho vai", agent="deny", category="setup")
async def set_role_ai_tier(ctx: Ctx, inp: RoleTierIn) -> dict[str, Any]:
    """Vai → hạng. Đổi có hiệu lực từ lượt kế tiếp của người giữ vai (kể cả phiên giọng nói đang mở — kiểm lại định kỳ)."""
    _who(inp)
    role = await ctx.one("SELECT key, label FROM core.roles WHERE key = %s", (inp.role,))
    tier = await ctx.one("SELECT key, label FROM core.ai_tiers WHERE key = %s", (inp.tier,))
    if role is None or tier is None:
        raise Invalid("Không có vai hoặc hạng này")
    await ctx.exec("INSERT INTO core.ai_role_tiers (role, tier) VALUES (%s, %s) ON CONFLICT (tenant_id, role) DO UPDATE SET tier = EXCLUDED.tier",
                   (inp.role, inp.tier))
    return {"role": inp.role, "tier": inp.tier, "summary": f"Vai {role['label']} → hạng {tier['label']}"}


class DefaultRoleIn(Input):
    role: str = field("Vai trò", max_length=60)
    tier: str = field("Hạng", max_length=40)


@m.command("default_role_ai_tier", resource=POLICY, action="manage", title="Cài hạng trợ lý AI mặc định của vai", internal=True, agent="deny",
           callers=("admin",),
           why="admin.install_defaults gán hạng gói khai cho vai (Role(ai_tier=…)) khi cài / bật gói — kể cả khi quản trị công ty bật gói. Chỉ thêm: vai "
               "đã có hạng thì giữ; vai / hạng không còn thì bỏ qua. Trả {added}.")
async def default_role_ai_tier(ctx: Ctx, inp: DefaultRoleIn) -> dict[str, Any]:
    if not await ctx.val("SELECT 1 FROM core.roles WHERE key = %s", (inp.role,)) or not await ctx.val(
            "SELECT 1 FROM core.ai_tiers WHERE key = %s", (inp.tier,)):
        return {"added": False}   # vai / hạng công ty đã bỏ ⇒ thôi (không thử lại mãi)
    n = await ctx.exec("INSERT INTO core.ai_role_tiers (role, tier) VALUES (%s, %s) ON CONFLICT (tenant_id, role) DO NOTHING", (inp.role, inp.tier))
    return {"added": bool(n)}


class PersonModelIn(Input):
    email: str = field("Email người dùng", max_length=200)
    model: str = field("Model", "", max_length=120, help="Trống = model của hạng. Phải nằm trong danh sách đặt riêng của hạng người đó")
    by: str = _by()
    why: str = _why()


@m.command("set_person_ai_model", resource=POLICY, action="manage", title="Đặt model trợ lý AI riêng cho một người", agent="deny", category="setup")
async def set_person_ai_model(ctx: Ctx, inp: PersonModelIn) -> dict[str, Any]:
    """Nhà vận hành đặt model riêng cho một người (trong danh sách hạng của họ cho phép, bản cài có khoá) — người dùng không tự chọn, không thấy tên model."""
    _who(inp)
    u = await ctx.one("""SELECT u.id, mb.role FROM core.users u JOIN core.memberships mb ON mb.user_id = u.id AND mb.active
                         WHERE lower(u.email) = lower(%s)""", (inp.email.strip(),))
    if u is None:
        raise NotFound(f"Không có thành viên {inp.email}")
    spec = inp.model.strip()
    if not spec:
        await ctx.exec("DELETE FROM core.ai_choices WHERE user_id = %s", (u["id"],))
        return {"model": "", "summary": f"{inp.email}: dùng model của hạng"}
    tier = await ctx.one(TIER_OF_ROLE, (u["role"],))
    if tier is None or spec not in (tier["choices"] or []):
        raise Invalid("Hạng của người này không có model này trong danh sách đặt riêng")
    if not _llm().usable(spec):
        raise Invalid("Bản cài chưa có khoá cho nhà cung cấp của model này (BARISTA_LLM_KEYS — runbook 15)")
    await ctx.exec("""INSERT INTO core.ai_choices (user_id, model) VALUES (%s, %s)
                      ON CONFLICT (tenant_id, user_id) DO UPDATE SET model = EXCLUDED.model, updated_at = now()""", (u["id"], spec))
    return {"model": spec, "summary": f"{inp.email}: {spec} từ lượt sau"}


@m.query("ai_tier_options", resource=POLICY, title="Hạng trợ lý AI", agent="deny")
async def ai_tier_options(ctx: Ctx, inp: NoInput) -> list[dict[str, str]]:
    return ui.options(await ctx.all("SELECT key AS value, label FROM core.ai_tiers ORDER BY sort, label"))


PLACED = """WITH placed AS (SELECT m.user_id, m.role, coalesce(r.tier, (SELECT key FROM core.ai_tiers WHERE is_default)) AS tier
                            FROM core.memberships m LEFT JOIN core.ai_role_tiers r ON r.tenant_id = m.tenant_id AND r.role = m.role WHERE m.active)"""


@m.query("ai_tiers", resource=POLICY, title="Hạng trợ lý AI", agent="deny")
async def ai_tiers(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    """Các hạng + vai thuộc hạng + số người + token đã dùng tháng này + số người đã hết hạn mức (của người đang giữ các vai thuộc hạng)."""
    month = today().replace(day=1)
    rows = await ctx.all(
        PLACED + """
           SELECT t.key, t.label, t.model, t.choices, t.person_monthly_tokens AS cap, t.voice, t.is_default, t.sort,
                  (SELECT count(*) FROM placed p WHERE p.tier = t.key) AS people,
                  (SELECT coalesce(sum(s.tokens), 0) FROM core.ai_person_spend s JOIN placed p ON p.user_id = s.user_id
                   WHERE p.tier = t.key AND s.month = %(m)s) AS used,
                  (SELECT count(*) FROM core.ai_person_spend s JOIN placed p ON p.user_id = s.user_id
                   WHERE p.tier = t.key AND s.month = %(m)s AND t.person_monthly_tokens IS NOT NULL AND s.tokens >= t.person_monthly_tokens) AS out,
                  (SELECT string_agg(DISTINCT ro.label, ', ') FROM core.roles ro LEFT JOIN core.ai_role_tiers r ON r.tenant_id = ro.tenant_id AND r.role = ro.key
                   WHERE r.tier = t.key OR (r.tier IS NULL AND t.is_default)) AS roles
           FROM core.ai_tiers t ORDER BY t.sort, t.label""", {"m": month})
    for r in rows:
        r["id"] = r["key"]
        r["model_text"] = _model_label(r["model"])
        r["choices_text"] = ", ".join(c if _llm().usable(c) else f"{c} (chưa có khoá)" for c in r["choices"]) or "—"
        r["cap_text"] = tokens_text(r["cap"])
        r["voice_state"] = "yes" if r["voice"] else "no"
        r["default_state"] = "yes" if r["is_default"] else "no"
        r["model_in"] = r["model"] or ""
        r["choices_in"] = list(r["choices"])
        r["cap_in"] = int(r["cap"]) if r["cap"] is not None else None
        r["used"] = int(r["used"])
    yes_no = {"yes": "Có", "no": "Không"}
    return ui.table(rows, [ui.col("label", "Hạng", width=140), ui.col("roles", "Vai thuộc hạng", width=220), ui.col("people", "Người", "number"),
                           ui.col("model_text", "Model", width=260), ui.col("choices_text", "Đặt riêng được", width=200),
                           ui.col("cap_text", "Hạn mức / người / tháng"), ui.col("used", "Token tháng này", "number"),
                           ui.col("out", "Người đã hết hạn mức", "number"),
                           ui.col("voice_state", "Giọng nói", "badge", labels=yes_no, tones={"yes": "green", "no": "gray"}),
                           ui.col("default_state", "Mặc định", "badge", labels=yes_no, tones={"yes": "blue", "no": "gray"})],
                    title="Hạng trợ lý AI", limit=200,
                    empty="Chưa có hạng nào — chạy cài mặc định (nâng cấp bản cài) hoặc lệnh vận hành save_ai_tier.",
                    note="Chính sách của nhà vận hành — khách không thấy màn này. Mỗi vai thuộc một hạng; vai chưa gán thuộc hạng mặc định. Hết hạn mức token "
                         "⇒ trợ lý dừng với riêng người đó tới ngày 1 tháng sau; trần cả công ty (USD) vẫn áp cho mọi người.")


@m.query("ai_role_tiers", resource=POLICY, title="Vai → hạng trợ lý AI", agent="deny")
async def ai_role_tiers(ctx: Ctx, inp: NoInput) -> dict[str, Any]:
    rows = await ctx.all(
        """SELECT ro.key AS id, ro.key AS role, ro.label, coalesce(t.label, d.label) AS tier_label, coalesce(t.key, d.key) AS tier,
                  CASE WHEN t.key IS NULL THEN 'default' ELSE 'set' END AS how,
                  (SELECT count(*) FROM core.memberships m WHERE m.role = ro.key AND m.active) AS people
           FROM core.roles ro LEFT JOIN core.ai_role_tiers r ON r.tenant_id = ro.tenant_id AND r.role = ro.key
           LEFT JOIN core.ai_tiers t ON t.tenant_id = r.tenant_id AND t.key = r.tier
           LEFT JOIN core.ai_tiers d ON d.tenant_id = ro.tenant_id AND d.is_default
           ORDER BY ro.label""")
    return ui.table(rows, [ui.col("label", "Vai trò", width=200), ui.col("people", "Người", "number"), ui.col("tier_label", "Hạng trợ lý AI", width=160),
                           ui.col("how", "Cách gán", "badge", labels={"set": "Đã gán", "default": "Theo hạng mặc định"},
                                  tones={"set": "green", "default": "gray"})],
                    title="Vai → hạng trợ lý AI", limit=500, empty="Chưa có vai nào")


# ── BÁO CÁO TOKEN cho nhà vận hành: căn cứ tính tiền theo đầu người với khách (token gốc, không quy đổi theo giá nhà cung cấp) ──

class ReportIn(Input):
    month: str = field("Tháng", "", max_length=7, example="2026-11", help="YYYY-MM; trống = tháng này (giờ công ty)")


def month_range(raw: str) -> tuple[date, datetime, datetime]:
    """(ngày 1 của tháng, đầu tháng, đầu tháng sau) theo giờ công ty."""
    if raw and not MONTH.match(raw):
        raise Invalid("Tháng dạng YYYY-MM, vd 2026-11")
    first = date.fromisoformat(f"{raw}-01") if raw else today().replace(day=1)
    nxt = first.replace(year=first.year + 1, month=1) if first.month == 12 else first.replace(month=first.month + 1)
    return first, datetime(first.year, first.month, 1, tzinfo=zone()), datetime(nxt.year, nxt.month, 1, tzinfo=zone())


# Token: ``input_tokens`` ĐÃ gồm token đọc / ghi cache và token âm thanh (quy ước pydantic-ai — nhóm lồng nhau, không rời) ⇒ bốn loại RỜI để tính tiền
# kiểu nào cũng được: vào không cache = vào − cache đọc − cache ghi · cache đọc · cache ghi · ra. Token tính hạn mức = vào − cache đọc + ra
# (``usage.quota_tokens`` — không đếm cache đọc).
_SUMS = """count(*) AS runs, coalesce(sum(greatest(x.input_tokens - x.cache_read_tokens - x.cache_write_tokens, 0)), 0) AS tfresh,
           coalesce(sum(x.cache_read_tokens), 0) AS tcache, coalesce(sum(x.cache_write_tokens), 0) AS twrite, coalesce(sum(x.output_tokens), 0) AS tout,
           coalesce(sum(greatest(x.input_tokens - x.cache_read_tokens, 0) + x.output_tokens), 0) AS quota,
           coalesce(sum(x.input_tokens + x.output_tokens), 0) AS total,
           coalesce(sum(x.input_tokens + x.output_tokens) FILTER (WHERE x.category LIKE 'voice:live%%'), 0) AS voice_tokens,
           coalesce(sum(x.duration_ms) FILTER (WHERE x.category LIKE 'voice:live%%'), 0) / 1000 AS voice_s,
           sum(x.cost_usd) AS cost, count(*) FILTER (WHERE x.cost_usd IS NULL) AS unpriced"""
_TOKEN_COLS = [ui.col("runs", "Lượt", "number"), ui.col("tfresh", "Token vào (không cache)", "number"), ui.col("tcache", "Token cache đọc", "number"),
               ui.col("twrite", "Token cache ghi", "number"), ui.col("tout", "Token ra", "number"),
               ui.col("quota", "Token tính hạn mức", "number"), ui.col("total", "Tổng token (gồm cache)", "number"),
               ui.col("voice_tokens", "Token giọng nói (Live)", "number"), ui.col("voice_s", "Giây giọng nói", "number"),
               ui.col("cost", "Chi phí nội bộ (USD)"), ui.col("unpriced", "Lượt chưa có giá", "number")]


def _clean(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for r in rows:
        for k in ("tfresh", "tcache", "twrite", "tout", "quota", "total", "voice_tokens", "voice_s"):
            r[k] = int(r[k] or 0)
        r["cost"] = round(float(r["cost"]), 4) if r["cost"] is not None else None
    return rows


@m.query("ai_token_report", resource=POLICY, title="Token trợ lý AI theo người", agent="deny")
async def ai_token_report(ctx: Ctx, inp: ReportIn) -> dict[str, Any]:
    """Token GỐC của trợ lý theo người × tháng (kèm hạng / vai hiện tại, hạn mức, lúc hết hạn mức), theo hạng × vai, theo nơi chạy — căn cứ tính tiền
    theo đầu người với khách. Chi phí USD (bảng giá công khai) chỉ để nhà vận hành biết lãi lỗ, không phải căn cứ tính tiền. CHỈ nhà vận hành."""
    from barista.agent.usage import SURFACES

    first, a, b = month_range(inp.month)
    rng = {"a": a, "b": b, "m": first}
    people = _clean(await ctx.all(
        PLACED + f"""
           SELECT u.id, u.name, u.email, coalesce(ro.label, p.role, '') AS role, coalesce(t.label, '') AS tier, t.person_monthly_tokens AS cap,
                  s.stopped_at, {_SUMS}
           FROM core.ai_usage x JOIN core.users u ON u.id = x.user_id
           LEFT JOIN placed p ON p.user_id = x.user_id LEFT JOIN core.roles ro ON ro.key = p.role
           LEFT JOIN core.ai_tiers t ON t.key = p.tier
           LEFT JOIN core.ai_person_spend s ON s.user_id = x.user_id AND s.month = %(m)s
           WHERE x.at >= %(a)s AND x.at < %(b)s
           GROUP BY u.id, u.name, u.email, ro.label, p.role, t.label, t.person_monthly_tokens, s.stopped_at ORDER BY quota DESC""", rng))
    for r in people:
        r["cap_text"] = tokens_text(r["cap"])
        r["share"] = round(r["quota"] * 100 / r["cap"], 1) if r["cap"] else None
    tiers = _clean(await ctx.all(
        PLACED + f"""
           SELECT coalesce(t.label, '—') || ' · ' || coalesce(ro.label, p.role, '—') AS id, coalesce(t.label, '—') AS tier,
                  coalesce(ro.label, p.role, '—') AS role, count(DISTINCT x.user_id) AS people, {_SUMS}
           FROM core.ai_usage x LEFT JOIN placed p ON p.user_id = x.user_id LEFT JOIN core.roles ro ON ro.key = p.role
           LEFT JOIN core.ai_tiers t ON t.key = p.tier
           WHERE x.at >= %(a)s AND x.at < %(b)s GROUP BY 1, 2, 3 ORDER BY total DESC""", rng))
    surfaces = _clean(await ctx.all(
        f"""SELECT x.surface AS id, x.surface, count(DISTINCT x.user_id) AS people, {_SUMS} FROM core.ai_usage x
            WHERE x.at >= %(a)s AND x.at < %(b)s GROUP BY x.surface ORDER BY total DESC""", rng))
    for r in surfaces:
        r["surface"] = SURFACES.get(r["surface"], r["surface"])
    embeds = _clean(await ctx.all(
        f"""SELECT coalesce(x.model, '—') AS id, coalesce(x.model, '—') AS model, count(DISTINCT x.user_id) AS people, {_SUMS} FROM core.ai_usage x
            WHERE x.at >= %(a)s AND x.at < %(b)s AND x.category LIKE 'kb:embed%%' GROUP BY x.model ORDER BY total DESC""", rng))
    month = f"{first:%m/%Y}"
    note = ("Bốn loại rời: vào không cache + cache đọc + cache ghi + ra = tổng. Token tính hạn mức = tổng − cache đọc (cache đọc rẻ ~50 lần). Hạng / vai là "
            "hạng hiện tại của người. Chi phí USD theo bảng giá công khai, chỉ dùng nội bộ. Giây giọng nói và bộ đếm hạn mức đo từ bản 082.")
    blocks = [
        ui.b_kpis([ui.kpi("Người dùng trợ lý", len(people)), ui.kpi("Lượt", sum(r["runs"] for r in people)),
                   ui.kpi("Token tính hạn mức", sum(r["quota"] for r in people)),
                   ui.kpi("Người hết hạn mức", sum(1 for r in people if r["stopped_at"] is not None))]),
        ui.b_table(ui.table(people, [ui.col("name", "Người", width=180), ui.col("email", "Email", width=200), ui.col("role", "Vai"),
                                     ui.col("tier", "Hạng"), *_TOKEN_COLS, ui.col("cap_text", "Hạn mức / tháng"),
                                     ui.col("share", "% hạn mức", "number"), ui.col("stopped_at", "Hết hạn mức lúc", "datetime")],
                            title=f"Theo người {month}", note=note, empty="Chưa có lượt nào trong tháng")),
        ui.b_table(ui.table(tiers, [ui.col("tier", "Hạng"), ui.col("role", "Vai"), ui.col("people", "Người", "number"), *_TOKEN_COLS],
                            title=f"Theo hạng và vai {month}", empty="Chưa có lượt nào trong tháng")),
        ui.b_table(ui.table(surfaces, [ui.col("surface", "Nơi chạy"), ui.col("people", "Người", "number"), *_TOKEN_COLS],
                            title=f"Theo nơi chạy {month}", empty="Chưa có lượt nào trong tháng")),
        ui.b_table(ui.table(embeds, [ui.col("model", "Model nhúng (tìm theo nghĩa)"), ui.col("people", "Người", "number"), *_TOKEN_COLS],
                            title=f"Nhúng theo model {month}", note="Hồ sơ nhúng đặt theo công ty (ADR 0232). Model tự dựng: 0 USD nhưng vẫn đếm lượt + token; token nhúng là ƯỚC TÍNH "
                            "khi nhà cung cấp không trả số thật.", empty="Chưa có lượt nhúng nào trong tháng")),
    ]
    return ui.card(f"Token trợ lý AI tháng {month}", blocks, subtitle="Chỉ nhà vận hành — căn cứ tính tiền theo đầu người", icon="tablecells")
