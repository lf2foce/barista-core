"""ĐIỀU KHIỂN QUẢNG CÁO không phụ thuộc kênh (ADR 0209) trên Postgres thật, với KÊNH GIẢ khai ngay trong test (như ``test_dummy_pack``): hai gói kênh cắm
``mkt.ad_objects`` / ``mkt.ad_apply`` / ``mkt.ad_convert`` — lõi không biết tên kênh nào.

* tác nhân theo dõi ra CÙNG phát hiện (cùng số) dù công ty có 0 hay 2 kênh; có kênh ⇒ phát hiện kèm lệnh của đúng kênh; không kênh ⇒ chỉ đường làm tay;
* đề xuất của trợ lý không gọi kênh; người bấm ⇒ kênh được gọi đúng một lần NGOÀI giao dịch của lệnh (``Prep.plugs``), sổ ghi trước / sau / phản hồi;
  lỗi kênh ⇒ không có dòng "đã gửi";
* vượt ngưỡng chính sách ⇒ phiếu duyệt, không gọi kênh; duyệt ⇒ hàng đợi; vòng gửi thử lại lỗi tạm, lỗi hẳn ⇒ failed + báo;
* chuyển đổi: MẶC ĐỊNH TẮT (không ghi gì); bật ⇒ lead ký từ quảng cáo của kênh ⇒ một dòng; vòng gửi đưa CHỈ dấu băm; huỷ trước khi gửi ⇒ không gửi;
* KHÔNG lời gọi kênh nào giữ kết nối / giao dịch (``db.holding()`` ở phần gọi kênh của phần cắm — ``Module.plug(prepare=)``, api 1.37), kể cả hai vòng gửi;
  hai lượt vòng gửi chồng nhau ⇒ sổ ghi MỘT lần; gói kênh tắt ⇒ không gọi kênh; hỏi phần cắm trong giao dịch ⇒ phần gọi kênh không chạy.
"""

import asyncio
import hashlib
import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any

import pytest

from barista import seed
from barista.api import Conflict, Ctx, Input, Invalid, Module, Pack, Prep
from barista.db import db, holding
from barista.kernel import events, executor, packs, registry, screens
from barista.kernel.executor import core_ctx
from barista.kernel.principal import Principal, system_principal
from tests.conftest import edge, fresh_phone, ok, owner_val, q

PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("giamdoc", "Giám đốc", "director", "HQ", True), ("mkt", "Marketing", "marketer", "MKT", True),
          ("sale", "Sale", "sale", "KD-A", False)]
LIVE: tuple[Any, ...] = (registry.PACKS, registry.MODULES, registry.MODULE_PACK, registry.RESOURCES, registry.RESOURCE_MODULE, registry.OPS,
                         registry.SETUP_RESOURCES, registry.PACK_MODULE, screens.WORKSPACES, screens.CONTRIB)


class Channel:
    """Một kênh giả: gương trạng thái (mã ⇒ đối tượng), mọi lần ghi / gửi chuyển đổi, lỗi tiêm theo lượt."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}
        self.applied: list[dict[str, Any]] = []
        self.converted: list[dict[str, Any]] = []
        self.fail: list[Exception] = []
        self.in_tx: list[bool] = []          # mỗi lời gọi "nhà cung cấp": tác vụ có đang giữ kết nối / giao dịch không
        self.gate: asyncio.Event | None = None   # đặt ⇒ lời gọi kế tiếp dừng ở đây (sau khi lượt đã đọc kế hoạch) tới khi mở
        self.entered = asyncio.Event()

    async def vendor(self) -> None:
        """Điểm "gọi mạng" của kênh giả: ghi có đang giữ giao dịch không, lỗi tiêm theo lượt, cửa chặn cho test lượt chồng nhau."""
        self.in_tx.append(holding())
        if self.gate is not None:
            gate, self.gate = self.gate, None
            self.entered.set()
            await gate.wait()
        if self.fail:
            raise self.fail.pop(0)

    def add(self, level: str, ref: str, *, budget: int | None = None, status: str = "active") -> None:
        self.objects[(level, ref)] = {"level": level, "ref": ref, "name": f"{level} {ref}", "status": status, "live": status == "active",
                                      "daily_budget": budget, "unit": 1, "currency": "VND", "account": "acc-1"}


CH: dict[str, Channel] = {}


class RefsIn(Input):
    refs: list[dict[str, str]]


class ApplyIn(Input):
    channel: str
    level: str
    ref: str
    status: str | None = None
    daily_budget: int | None = None


class ConvertIn(Input):
    channel: str
    event_id: str
    event: str
    occurred_at: datetime
    value_vnd: int
    campaign_ref: str
    ad_ref: str
    match: dict[str, str]


def _register(key: str) -> None:
    Pack(key, f"Kênh thử {key}", "Kênh quảng cáo giả cho test lõi", kind="capability", requires=("marketing",))
    m = Module(key, f"Kênh {key}", pack=key, resources={key: "Kênh thử"})
    ch = CH[key]

    @m.plug("mkt.ad_objects", "Đối tượng trên kênh thử")
    async def _objects(ctx: Ctx, inp: RefsIn) -> dict[str, Any] | None:
        got = [ch.objects[(r["level"], r["ref"])] for r in inp.refs if (r["level"], r["ref"]) in ch.objects]
        return {"channel": key, "label": f"Kênh {key}", "objects": got} if got else None

    async def _apply_live(prep: Prep, inp: ApplyIn) -> dict[str, Any] | None:
        if inp.channel != key:
            return None
        await ch.vendor()
        o = ch.objects[(inp.level, inp.ref)]
        before = {"status": o["status"], "daily_budget": o["daily_budget"]}
        want = {k: v for k, v in (("status", inp.status), ("daily_budget", inp.daily_budget)) if v is not None}
        if all(o[k] == v for k, v in want.items()):   # kênh đọc trạng thái thật trước: đã đúng ⇒ không ghi
            return {"before": before, "already": True, "response": {}}
        o.update(want)
        o["live"] = o["status"] == "active"
        ch.applied.append({"level": inp.level, "ref": inp.ref, **want})
        return {"before": before, "already": False, "response": {"success": True}}

    @m.plug("mkt.ad_apply", "Đổi trên kênh thử", prepare=_apply_live)
    async def _apply(ctx: Ctx, inp: ApplyIn) -> dict[str, Any] | None:
        if inp.channel != key:
            return None
        if ctx.prepared is None:
            raise Invalid("Kênh thử chỉ được gọi ngoài giao dịch (Prep.plugs)")
        return ctx.prepared

    async def _convert_live(prep: Prep, inp: ConvertIn) -> dict[str, Any] | None:
        if inp.channel != key:
            return None
        await ch.vendor()
        ch.converted.append(inp.model_dump(mode="json"))
        return {"response": {"events_received": 1}}

    @m.plug("mkt.ad_convert", "Chuyển đổi về kênh thử", prepare=_convert_live)
    async def _convert(ctx: Ctx, inp: ConvertIn) -> dict[str, Any] | None:
        if inp.channel != key:
            return None
        if ctx.prepared is None:
            raise Invalid("Kênh thử chỉ được gọi ngoài giao dịch (Prep.plugs)")
        return ctx.prepared


@pytest.fixture(scope="module", autouse=True)
def channels() -> Iterator[None]:
    snap = [type(s)(s) for s in LIVE]
    plugs = {k: list(v) for k, v in registry.PLUGS.items()}
    for key in ("adfake", "adfake2"):
        CH[key] = Channel(key)
        _register(key)
    registry.validate()
    yield
    for live, old in zip(LIVE, snap, strict=True):
        live.clear()
        live.extend(old) if isinstance(live, list) else live.update(old)
    registry.PLUGS.clear()
    registry.PLUGS.update(plugs)


async def _company(*, with_channels: bool, people: list[tuple[str, str, str, str, bool]] = PEOPLE) -> tuple[Any, dict[str, Principal]]:
    d = f"a{uuid.uuid4().hex[:6]}.demo"
    tid = await seed.create_tenant(f"Công ty quảng cáo {d}", d, people=people, packs=("crm", "marketing"))
    if with_channels:
        for key in CH:
            await owner_val("INSERT INTO core.tenant_packs (tenant_id, pack) VALUES (%s, %s) RETURNING 1", (tid, key))
        packs.invalidate(tid)
    return tid, {k: await seed.who(tid, k, d) for k, *_ in people}


@pytest.fixture(scope="module")
async def bare() -> tuple[Any, dict[str, Principal]]:
    return await _company(with_channels=False)


@pytest.fixture(scope="module")
async def wired() -> tuple[Any, dict[str, Principal]]:
    return await _company(with_channels=True)


def _tag() -> str:
    return str(uuid.uuid4().int)[:10]


async def _today(tid: Any) -> date:
    got = await owner_val("SELECT core.today_of(%s)", (tid,))
    assert isinstance(got, date)
    return got


async def _camp(p: dict[str, Principal], ref: str, by: str = "mkt") -> None:
    """Chiến dịch ở Marketing cho một mã kênh (người phụ trách ``by``, đội chính của họ) — người phạm vi "team" chỉ đổi được đối tượng của chiến dịch mình thấy."""
    await ok(p[by], "mkt.upsert_campaign", code=ref, name=f"Chiến dịch {ref}", status="running")


async def _shop(p: dict[str, Principal], on: date, spend: str, *, leads: int = 0, qualify: int = 0, ad_leads: bool = True,
                by: str = "mkt") -> dict[str, str]:
    """Một chiến dịch + một quảng cáo có chi ở ngày ``on`` (người phụ trách ``by``); ``leads`` lead vào CRM (biết mẫu nếu ``ad_leads``), ``qualify`` lead lên
    tiềm năng."""
    t = _tag()
    c, a = f"C{t}", f"9{t}"
    await ok(p[by], "mkt.upsert_campaign", code=c, name=f"Chiến dịch {t}", start_on=on.isoformat(), status="running")
    await ok(p[by], "mkt.upsert_ad", campaign_code=c, code=a, name=f"QC {t}")
    await ok(p[by], "mkt.record_ad_day", ad_code=a, day=on.isoformat(), amount_vnd=spend, leads_platform=0)
    out = {"c": c, "a": a, "leads": ""}
    ids = []
    for i in range(leads):
        lead = (await ok(p["sale"], "crm.create_lead", name=f"Khách {t} {i}", phone=fresh_phone(), email=f"Khach{t}{i}@Example.com ",
                         source="ads", campaign_code=c, ad_code=a if ad_leads else "", captured_on=on.isoformat()))["lead_id"]
        if i < qualify:
            await ok(p["sale"], "crm.set_stage", lead_id=str(lead), stage="prospect")
        ids.append(str(lead))
    out["leads"] = ",".join(ids)
    return out


async def _sweep(tid: Any, name: str) -> dict[str, Any]:
    r = await executor.run(system_principal(tid), name, {}, background=True)
    assert r.status == "ok", (r.status, r.error)
    return r.output


async def _findings(tid: Any) -> dict[tuple[str, str, str], dict[str, Any]]:
    rows = await owner_val("""SELECT coalesce(jsonb_agg(row_to_json(f)), '[]') FROM (SELECT scope, scope_key, rule, figures, channel, proposal
                              FROM mkt.ad_findings WHERE tenant_id = %s AND state = 'open') f""", (tid,))
    return {(r["scope"], r["scope_key"], r["rule"]): r for r in rows}


async def _rewatch(tid: Any) -> dict[str, Any]:
    async with db.owner_tx() as cx:
        await cx.execute("DELETE FROM mkt.ad_changes WHERE tenant_id = %s", (tid,))
        await cx.execute("DELETE FROM mkt.ad_findings WHERE tenant_id = %s", (tid,))
        await cx.execute("UPDATE mkt.ad_policy SET last_watch_on = NULL WHERE tenant_id = %s", (tid,))
    return await _sweep(tid, "mkt.watch_ads")


async def test_the_watcher_files_the_same_findings_with_zero_or_two_channels(bare: Any, wired: Any) -> None:
    got: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    ids: dict[str, tuple[dict[str, str], dict[str, str]]] = {}
    for label, (tid, p) in (("bare", bare), ("wired", wired)):
        day = await _today(tid) - timedelta(days=2)
        bad = await _shop(p, day, "6000000", leads=2)                 # chi 6tr, 2 lead, 0 lead chất lượng ⇒ tạm dừng chiến dịch
        good = await _shop(p, day, "6000000", leads=2, qualify=1)     # có lead chất lượng ⇒ không đề xuất
        ad_only = await _shop(p, day, "3000000", leads=1, ad_leads=False)   # lead không biết mẫu ⇒ quảng cáo xét bằng lead nền tảng (0)
        if label == "wired":
            CH["adfake"].add("campaign", bad["c"], budget=1_000_000)
            CH["adfake"].add("ad", bad["a"])
            CH["adfake2"].add("campaign", ad_only["c"])
            CH["adfake2"].add("ad", ad_only["a"])
        await ok(p["admin"], "mkt.set_ad_policy", window_days=7, min_spend_vnd="5tr", max_qualified=0, ad_min_spend_vnd="2tr", max_open_pct=100)
        await _rewatch(tid)
        f = await _findings(tid)
        assert ("campaign", good["c"], "no_result") not in f
        assert ("campaign", bad["c"], "no_result") in f and ("ad", bad["a"], "no_result") in f, f.keys()   # quảng cáo xét bằng lead CRM (biết mẫu)
        assert ("ad", ad_only["a"], "no_platform_lead") in f
        got[label] = {(k[0], k[2]): v for k, v in f.items() if k[1] in (bad["c"], bad["a"], ad_only["a"])}
        ids[label] = bad, ad_only
    for k, v in got["bare"].items():
        assert v["figures"] | {"channel": ""} == got["wired"][k]["figures"] | {"channel": ""}, k   # cùng số dù có hay không có kênh
        assert v["proposal"] is None and v["channel"] == ""                                     # không kênh: chỉ đường làm tay
    bad, _ = ids["wired"]
    w = got["wired"]
    assert w[("campaign", "no_result")]["proposal"]["input"] == {"channel": "adfake", "level": "campaign", "ref": bad["c"], "status": "paused"}
    assert w[("ad", "no_platform_lead")]["proposal"]["input"]["channel"] == "adfake2"
    assert w[("campaign", "no_result")]["figures"]["spend"] == 6_000_000 and w[("campaign", "no_result")]["figures"]["outcome_count"] is None
    assert CH["adfake"].applied == [] and CH["adfake2"].applied == []   # tác nhân không bao giờ gọi kênh


async def test_manual_path_works_without_any_channel(bare: Any) -> None:
    tid, p = bare
    day = await _today(tid) - timedelta(days=2)
    s = await _shop(p, day, "7000000", leads=1)
    await _rewatch(tid)
    fid = await owner_val("SELECT id FROM mkt.ad_findings WHERE tenant_id = %s AND scope_key = %s AND rule = 'no_result'", (tid, s["c"]))
    r = await executor.run(p["mkt"], "mkt.set_ad_status", {"channel": "", "level": "campaign", "ref": s["c"], "status": "paused"})
    assert r.status == "invalid" and "Đã làm tay" in r.error
    assert (await executor.run(p["mkt"].as_agent(), "mkt.record_ad_manual", {"level": "campaign", "ref": s["c"], "action": "pause"})).status == "denied"
    got = await ok(p["mkt"], "mkt.record_ad_manual", level="campaign", ref=s["c"], action="pause", finding_id=str(fid), note="tắt ngoài app")
    assert "làm tay" in got["summary"]
    row = await owner_val("SELECT row_to_json(x) FROM (SELECT channel, mode, state, origin FROM mkt.ad_changes WHERE id = %s) x", (uuid.UUID(got["change_id"]),))
    assert row == {"channel": "", "mode": "manual", "state": "manual", "origin": "watcher"}
    assert await owner_val("SELECT state FROM mkt.ad_findings WHERE id = %s", (fid,)) == "done"


async def _ledger(tid: Any, ref: str) -> list[dict[str, Any]]:
    return await owner_val("""SELECT coalesce(jsonb_agg(row_to_json(x) ORDER BY x.created_at), '[]') FROM (SELECT action, mode, state, before, after, channel,
                              requested_by::text, approved_by::text[], response, error, created_at, confirmed_at IS NOT NULL AS confirmed
                              FROM mkt.ad_changes WHERE tenant_id = %s AND ref = %s) x""", (tid, ref))


async def test_a_click_calls_the_channel_once_outside_the_command_transaction(wired: Any) -> None:
    tid, p = wired
    ch, ref = CH["adfake"], f"C{_tag()}"
    ch.add("campaign", ref, budget=1_000_000)
    await _camp(p, ref)
    ch.applied.clear()
    target = {"channel": "adfake", "level": "campaign", "ref": ref, "status": "paused"}
    card = await executor.run(p["mkt"].as_agent(), "mkt.set_ad_status", target)
    assert card.status == "proposed", card.error
    assert card.output["preview"]["summary"].startswith("Sẽ tạm dừng chiến dịch") and ch.applied == []
    key = f"k-{uuid.uuid4()}"
    r1 = await executor.run(p["mkt"], "mkt.set_ad_status", target, key=key)
    r2 = await executor.run(p["mkt"], "mkt.set_ad_status", target, key=key)
    assert r1.status == "ok" and r2.replayed and ch.applied == [{"level": "campaign", "ref": ref, "status": "paused"}], (r1.status, r1.error)
    assert ch.in_tx and not any(ch.in_tx)   # phần gọi kênh của phần cắm (prepare=) chạy khi KHÔNG giữ kết nối / giao dịch nào
    r3 = await executor.run(p["giamdoc"], "mkt.set_ad_status", target)
    assert r3.status == "ok" and r3.output["already"] is True and len(ch.applied) == 1
    led = await _ledger(tid, ref)
    assert [(x["action"], x["state"], x["before"]["status"], x["after"]["status"]) for x in led] == [("pause", "sent", "active", "paused"),
                                                                                                    ("pause", "sent", "paused", "paused")]
    assert led[0]["requested_by"] == str(p["mkt"].user_id) and led[0]["response"]["success"] is True
    ref2 = f"C{_tag()}"
    ch.add("campaign", ref2)
    await _camp(p, ref2)
    ch.fail.append(Conflict("kênh tạm lỗi (giả)"))
    r = await executor.run(p["mkt"], "mkt.set_ad_status", {**target, "ref": ref2})
    assert r.status == "conflict" and await _ledger(tid, ref2) == [], (r.status, r.error)   # lỗi kênh ⇒ không có dòng nào
    assert (await executor.run(p["sale"], "mkt.set_ad_status", target)).status == "denied"
    out = await _sweep(tid, "mkt.dispatch_ad_changes")   # đối chiếu: gương kênh đã đúng ⇒ xác nhận
    assert out["confirmed"] >= 2 and all(x["confirmed"] for x in await _ledger(tid, ref))


async def test_policy_thresholds_route_big_budget_changes_through_approval_then_the_dispatcher(wired: Any) -> None:
    tid, p = wired
    ch, ref = CH["adfake"], f"C{_tag()}"
    ch.add("campaign", ref, budget=1_000_000)
    await _camp(p, ref)
    ch.applied.clear()
    t = {"channel": "adfake", "level": "campaign", "ref": ref}
    await ok(p["admin"], "mkt.set_ad_policy", budget_pct_max=20, budget_vnd_max="2tr", resume_needs_approval=True)
    small = await executor.run(p["mkt"], "mkt.set_ad_budget", {**t, "daily_budget": "1100000"})
    assert small.status == "ok" and ch.applied == [{"level": "campaign", "ref": ref, "daily_budget": 1_100_000}], (small.status, small.error)
    big = await executor.run(p["mkt"], "mkt.set_ad_budget", {**t, "daily_budget": "2200000"})
    assert big.status == "pending_approval" and len(ch.applied) == 1
    summary = await owner_val("SELECT summary FROM core.approvals WHERE id = %s", (uuid.UUID(str(big.approval_id)),))
    assert "vượt ngưỡng" in summary and "100%" in summary
    d = await executor.run(edge(p["giamdoc"]), "core.decide", {"approval_id": str(big.approval_id), "approve": True})
    assert d.status == "ok" and d.output["status"] == "executed", d.output
    led = await _ledger(tid, ref)
    assert [x["state"] for x in led] == ["sent", "queued"] and led[1]["approved_by"] == [str(p["giamdoc"].user_id)] and len(ch.applied) == 1
    ch.fail.append(Conflict("hạn mức kênh (giả)"))
    assert (await _sweep(tid, "mkt.dispatch_ad_changes"))["retry"] == 1
    ch.in_tx.clear()
    assert (await _sweep(tid, "mkt.dispatch_ad_changes"))["sent"] == 1 and ch.applied[-1]["daily_budget"] == 2_200_000
    assert ch.in_tx == [False]   # vòng gửi gọi kênh ở bước chuẩn bị — không trong giao dịch của lượt
    await ok(p["admin"], "mkt.set_ad_policy", budget_pct_max=1, budget_vnd_max="1tr")
    big2 = await executor.run(p["mkt"], "mkt.set_ad_budget", {**t, "daily_budget": "9000000"})
    await executor.run(edge(p["giamdoc"]), "core.decide", {"approval_id": str(big2.approval_id), "approve": True})
    ch.fail.append(Invalid("kênh từ chối ngân sách (giả)"))
    assert (await _sweep(tid, "mkt.dispatch_ad_changes"))["failed"] == 1
    last = (await _ledger(tid, ref))[-1]
    assert last["state"] == "failed" and "từ chối" in last["error"]
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND kind = 'mkt_ad_change'", (p["mkt"].user_id,)) >= 1
    await ok(p["admin"], "mkt.set_ad_policy")


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


async def test_conversions_are_off_by_default_and_send_only_hashes_when_on(wired: Any) -> None:
    tid, p = wired
    ch = CH["adfake"]
    day = await _today(tid) - timedelta(days=1)
    s = await _shop(p, day, "1000000", leads=3)
    ch.add("campaign", s["c"])
    ch.add("ad", s["a"])
    lead, lead2, lead3 = s["leads"].split(",")

    async def won(lead_id: str, ref: str, outcome: str = "won") -> None:
        async with db.tenant_tx(system_principal(tid)) as conn:
            cx = core_ctx(conn, system_principal(tid), module="crm")
            await cx.call("crm.record_lead_outcome", lead_id=lead_id, outcome=outcome, value_vnd=50_000_000, ref=ref)
            await executor.flush_events(conn, system_principal(tid), cx.events)
        await events.drain()

    await won(lead, "GD-1")   # chính sách mặc định: TẮT ⇒ không ghi gì
    assert await owner_val("SELECT count(*) FROM mkt.ad_conversions WHERE tenant_id = %s", (tid,)) == 0
    await ok(p["admin"], "mkt.set_ad_policy", conversions=True)
    await won(lead, "GD-1")
    await won(lead, "GD-1")   # cùng kết quả hai lần ⇒ một dòng
    await won(lead2, "GD-2")
    await won(lead2, "GD-2", "undone")   # huỷ trước khi gửi ⇒ không gửi
    rows = await owner_val("""SELECT jsonb_agg(row_to_json(c) ORDER BY c.outcome_ref) FROM (SELECT channel, ad_code, campaign_code, state, outcome_ref, value_vnd
                              FROM mkt.ad_conversions WHERE tenant_id = %s) c""", (tid,))
    assert rows == [{"channel": "adfake", "ad_code": s["a"], "campaign_code": s["c"], "state": "queued", "outcome_ref": "GD-1", "value_vnd": 50_000_000},
                    {"channel": "adfake", "ad_code": s["a"], "campaign_code": s["c"], "state": "cancelled", "outcome_ref": "GD-2", "value_vnd": 50_000_000}]
    cols = await owner_val("SELECT array_agg(column_name::text) FROM information_schema.columns WHERE table_schema = 'mkt' AND table_name = 'ad_conversions'")
    assert not [c for c in cols if any(w in c for w in ("phone", "email", "sha", "hash"))]
    ch.fail.append(Conflict("kênh tạm lỗi (giả)"))
    assert (await _sweep(tid, "mkt.send_ad_conversions"))["retry"] == 1
    async with db.owner_tx() as cx:
        await cx.execute("UPDATE mkt.ad_conversions SET next_at = now() WHERE tenant_id = %s", (tid,))
    ch.in_tx.clear()
    assert (await _sweep(tid, "mkt.send_ad_conversions"))["sent"] == 1
    assert ch.in_tx == [False]   # gửi chuyển đổi khi không giữ giao dịch nào
    sent = ch.converted[-1]
    phone, email = await owner_val("""SELECT ARRAY[c.phone, c.email] FROM crm.leads l JOIN crm.customers c ON c.id = l.customer_id WHERE l.id = %s""",
                                   (uuid.UUID(lead),))
    assert sent["match"] == {"email_sha256": _sha(email.strip().lower()), "phone_sha256": _sha(phone.removeprefix("+")), "phone_e164_sha256": _sha(phone)}
    assert phone not in str(sent) and email.strip().lower() not in str(sent).lower()
    assert sent["ad_ref"] == s["a"] and sent["value_vnd"] == 50_000_000 and sent["event"] == "won"
    # tắt lại trước khi gửi ⇒ dòng chờ không gửi
    await won(lead3, "GD-3")
    await ok(p["admin"], "mkt.set_ad_policy")
    assert (await _sweep(tid, "mkt.send_ad_conversions"))["cancelled"] == 1


async def _queued_change(p: dict[str, Principal], ref: str) -> str:
    """Một thay đổi ngân sách vượt ngưỡng ⇒ phiếu ⇒ duyệt ⇒ dòng ``queued`` chờ vòng gửi."""
    await _camp(p, ref)
    await ok(p["admin"], "mkt.set_ad_policy", budget_pct_max=1, budget_vnd_max="1tr")
    big = await executor.run(p["mkt"], "mkt.set_ad_budget", {"channel": "adfake", "level": "campaign", "ref": ref, "daily_budget": "5000000"})
    assert big.status == "pending_approval", (big.status, big.error)
    d = await executor.run(edge(p["giamdoc"]), "core.decide", {"approval_id": str(big.approval_id), "approve": True})
    assert d.status == "ok", d.output
    await ok(p["admin"], "mkt.set_ad_policy")
    return str(big.approval_id)


async def _overlap(tid: Any, ch: Channel, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Hai lượt vòng gửi chồng nhau: lượt A đọc kế hoạch rồi dừng ở lời gọi kênh; lượt B chạy trọn (đọc CÙNG dòng, gửi, ghi); rồi A gọi kênh xong và ghi."""
    ch.gate, ch.entered = asyncio.Event(), asyncio.Event()
    gate = ch.gate
    a = asyncio.create_task(_sweep(tid, name))
    await asyncio.wait_for(ch.entered.wait(), 10)
    b = await asyncio.wait_for(_sweep(tid, name), 20)
    gate.set()
    return await asyncio.wait_for(a, 20), b


async def test_two_overlapping_dispatch_runs_record_a_change_once(wired: Any) -> None:
    tid, p = wired
    ch, ref = CH["adfake"], f"C{_tag()}"
    ch.add("campaign", ref, budget=1_000_000)
    ch.applied.clear()
    ch.in_tx.clear()
    await _queued_change(p, ref)
    first, second = await _overlap(tid, ch, "mkt.dispatch_ad_changes")
    assert (second["sent"], first["sent"], first["retry"], first["failed"]) == (1, 0, 0, 0)   # lượt sau ghi; lượt chậm thấy dòng đã ghi ⇒ bỏ
    assert ch.applied == [{"level": "campaign", "ref": ref, "daily_budget": 5_000_000}]       # lời gọi trùng: kênh đọc thật trước ⇒ không ghi lại
    assert len(ch.in_tx) == 2 and not any(ch.in_tx)
    led = await _ledger(tid, ref)
    assert [x["state"] for x in led] == ["sent"] and led[0]["response"]["already"] is False
    assert await owner_val("SELECT attempts FROM mkt.ad_changes WHERE tenant_id = %s AND ref = %s", (tid, ref)) == 1


async def test_an_operator_dry_run_of_the_dispatcher_never_calls_the_channel(wired: Any) -> None:
    """Chạy thử (``dry_run`` — cửa nhà vận hành) không chạy bước chuẩn bị: thay đổi đã duyệt KHÔNG được gửi lên kênh, dòng vẫn chờ, không ghi gì."""
    tid, p = wired
    ch, ref = CH["adfake"], f"C{_tag()}"
    ch.add("campaign", ref, budget=1_000_000)
    ch.applied.clear()
    ch.in_tx.clear()
    await _queued_change(p, ref)
    r = await executor.run(system_principal(tid), "mkt.dispatch_ad_changes", {}, dry_run=True)
    assert r.status == "dry_run", (r.status, r.error)
    assert ch.applied == [] and ch.in_tx == []                                              # kênh không bị gọi
    assert await owner_val("SELECT state FROM mkt.ad_changes WHERE tenant_id = %s AND ref = %s", (tid, ref)) == "queued"


async def test_two_overlapping_conversion_runs_record_one_send_with_one_event_id(wired: Any) -> None:
    tid, p = wired
    ch = CH["adfake"]
    day = await _today(tid) - timedelta(days=1)
    s = await _shop(p, day, "1000000", leads=1)
    ch.add("campaign", s["c"])
    ch.add("ad", s["a"])
    await ok(p["admin"], "mkt.set_ad_policy", conversions=True)
    async with db.tenant_tx(system_principal(tid)) as conn:
        cx = core_ctx(conn, system_principal(tid), module="crm")
        await cx.call("crm.record_lead_outcome", lead_id=s["leads"], outcome="won", value_vnd=10_000_000, ref="GD-OV")
        await executor.flush_events(conn, system_principal(tid), cx.events)
    await events.drain()
    async with db.owner_tx() as cx2:   # chỉ dòng của test này tới hạn
        await cx2.execute("UPDATE mkt.ad_conversions SET next_at = now() + interval '1 day' WHERE tenant_id = %s AND outcome_ref <> 'GD-OV'", (tid,))
    ch.converted.clear()
    ch.in_tx.clear()
    first, second = await _overlap(tid, ch, "mkt.send_ad_conversions")
    assert second["sent"] + first["sent"] == 1 and first["retry"] == first["failed"] == 0
    assert len(ch.converted) == 2 and ch.converted[0]["event_id"] == ch.converted[1]["event_id"]   # gửi trùng mang cùng event_id ⇒ kênh khử trùng
    assert not any(ch.in_tx)
    row = await owner_val("""SELECT row_to_json(c) FROM (SELECT state, attempts FROM mkt.ad_conversions WHERE tenant_id = %s AND outcome_ref = 'GD-OV') c""",
                          (tid,))
    assert row == {"state": "sent", "attempts": 1}
    await ok(p["admin"], "mkt.set_ad_policy")


async def test_a_channel_pack_that_is_off_is_never_called_and_a_plug_asked_inside_a_transaction_does_not_call_out(bare: Any, wired: Any) -> None:
    ch = CH["adfake"]
    ref = f"C{_tag()}"
    ch.add("campaign", ref, budget=1_000_000)
    calls = len(ch.in_tx)
    inp = {"channel": "adfake", "level": "campaign", "ref": ref, "status": "paused"}
    off = await executor.query(system_principal(bare[0]), "adfake.plug_ad_apply", inp)   # công ty chưa bật gói kênh
    assert off.status == "not_found" and len(ch.in_tx) == calls                          # gói tắt ⇒ phần gọi kênh không chạy
    sp = system_principal(wired[0])
    async with db.tenant_tx(sp, rollback=True) as conn:   # hỏi qua ctx.plugs TRONG giao dịch ⇒ phần gọi kênh không chạy, phần cắm từ chối
        with pytest.raises(Invalid, match="ngoài giao dịch"):
            await core_ctx(conn, sp, module="mkt").plugs("mkt.ad_apply", system=True, **inp)
    assert len(ch.in_tx) == calls and ch.objects[("campaign", ref)]["status"] == "active"


async def test_ad_results_reports_crm_leads_per_ad(wired: Any) -> None:
    tid, p = wired
    day = await _today(tid) - timedelta(days=1)
    s = await _shop(p, day, "2000000", leads=2, qualify=1)
    sp = system_principal(tid)
    async with db.tenant_tx(sp, rollback=True) as conn:
        res = await core_ctx(conn, sp, module="mkt").call("mkt.ad_results", date_from=day, date_to=day)
    ad = next(a for a in res["ads"] if a["code"] == s["a"])
    camp = next(c for c in res["campaigns"] if c["code"] == s["c"])
    assert (ad["spend"], ad["leads"], ad["qualified"], ad["leads_platform"]) == (2_000_000, 2, 1, 0)
    assert (camp["leads"], camp["with_ad"], camp["status"]) == (2, 2, "running")


# ── Theo ĐỘI (0209 §đội, 14/11/2026): marketing chỉ thấy / đổi quảng cáo của chiến dịch mình + đội mình; trưởng phòng marketing thấy cả công ty ──

TEAM_PEOPLE = [("admin", "Quản trị", "admin", "QT", True), ("giamdoc", "Giám đốc", "director", "HQ", True),
               ("mkta", "Marketing A", "marketer", "DIG", True), ("mkta2", "Marketing A2", "marketer", "DIG", False),
               ("mktb", "Marketing B", "marketer", "HCNS", True),   # đội khác nhánh với DIG (đội nào không quan trọng — chỉ cần không chung cây)
               ("tpmkt", "Trưởng phòng marketing", "marketing_head", "MKT", False), ("sale", "Sale", "sale", "KD-A", False)]


@pytest.fixture(scope="module")
async def teams() -> tuple[Any, dict[str, Principal]]:
    return await _company(with_channels=True, people=TEAM_PEOPLE)


def _refs(page: dict[str, Any]) -> set[str]:
    return {r["ref"] for r in page["rows"] if r["ref"]}


async def test_a_marketer_sees_and_acts_only_on_their_team_s_ads_and_the_marketing_head_sees_all(teams: Any) -> None:
    tid, p = teams
    ch = CH["adfake"]
    # rà vai 18/11/2026: marketing thấy chiến dịch của ĐỘI MÌNH THAM GIA ("unit" — cả phòng), không chỉ đội mình làm trưởng
    assert p["mkta"].scope("mkt.ad_control") == "unit" and p["tpmkt"].scope("mkt.ad_control") == "all"   # bộ mặc định mới của vai
    day = await _today(tid) - timedelta(days=2)
    a = await _shop(p, day, "6000000", leads=2, by="mkta")     # đội A, trưởng đội phụ trách
    a2 = await _shop(p, day, "6500000", leads=2, by="mkta2")   # đội A, thành viên (không trưởng) phụ trách
    b = await _shop(p, day, "7000000", leads=2, by="mktb")     # đội B
    for s in (a, a2, b):
        ch.add("campaign", s["c"], budget=1_000_000)
        ch.add("ad", s["a"])
    await ok(p["admin"], "mkt.set_ad_policy", window_days=7, min_spend_vnd="5tr", max_qualified=0, ad_min_spend_vnd="2tr", max_open_pct=100)
    await _rewatch(tid)
    f = await _findings(tid)
    for s in (a, a2, b):   # tác nhân (quyền hệ thống) vẫn tạo phát hiện cho MỌI đội
        assert ("campaign", s["c"], "no_result") in f and ("ad", s["a"], "no_result") in f, f.keys()
    team = {r["k"]: r for r in await owner_val("""SELECT coalesce(jsonb_agg(jsonb_build_object('k', f.scope_key, 'owner', f.owner_id, 'team', f.team_id)), '[]')
                                                 FROM mkt.ad_findings f WHERE f.tenant_id = %s""", (tid,))}
    dig = await owner_val("SELECT id::text FROM core.teams WHERE tenant_id = %s AND code = 'DIG'", (tid,))
    assert team[a["c"]] == {"k": a["c"], "owner": str(p["mkta"].user_id), "team": dig} and team[a2["a"]]["owner"] == str(p["mkta2"].user_id)
    mine_a, mine_a2, mine_b = {a["c"], a["a"]}, {a2["c"], a2["a"]}, {b["c"], b["a"]}
    assert _refs(await q(p["mkta"], "mkt.ad_findings", state="all")) == mine_a | mine_a2     # trưởng đội A: chiến dịch của đội
    assert _refs(await q(p["mkta2"], "mkt.ad_findings", state="all")) == mine_a | mine_a2    # thành viên: cả chiến dịch của đội mình tham gia (như mkt.campaigns)
    assert _refs(await q(p["mktb"], "mkt.ad_findings", state="all")) == mine_b
    assert _refs(await q(p["tpmkt"], "mkt.ad_findings", state="all")) >= mine_a | mine_a2 | mine_b   # trưởng phòng: cả công ty
    # gợi ý buổi sáng theo người: chỉ phát hiện của đội mình
    for who, mine in (("mkta", mine_a | mine_a2), ("mktb", mine_b)):
        sug = await q(p[who], "mkt.ad_suggestions")
        assert sug and {x["input"]["ref"] for x in sug} <= mine, (who, sug)
    # đổi trên kênh: chiến dịch đội khác ⇒ không thấy, kênh KHÔNG bị gọi (chặn ngay ở bước chuẩn bị)
    fid_b = await owner_val("SELECT id::text FROM mkt.ad_findings WHERE tenant_id = %s AND scope_key = %s AND rule = 'no_result'", (tid, b["c"]))
    ch.applied.clear()
    other = {"channel": "adfake", "level": "campaign", "ref": b["c"]}
    for name, inp in (("mkt.set_ad_status", {**other, "status": "paused"}), ("mkt.set_ad_status", {**other, "status": "paused", "finding_id": fid_b}),
                      ("mkt.set_ad_budget", {**other, "daily_budget": "900000"}), ("mkt.set_ad_status", {**other, "level": "ad", "ref": b["a"], "status": "paused"}),
                      ("mkt.record_ad_manual", {**other, "action": "pause"}), ("mkt.dismiss_ad_finding", {"finding_id": fid_b})):
        r = await executor.run(p["mkta"], name, inp)
        assert r.status == "not_found", (name, inp, r.status, r.error)
    card = await executor.run(p["mkta"].as_agent(), "mkt.set_ad_status", {**other, "status": "paused"})
    assert card.status == "not_found", card.status                       # trợ lý của người đó cũng không soạn được thẻ cho đội khác
    assert ch.applied == [] and await owner_val("SELECT state FROM mkt.ad_findings WHERE id = %s", (uuid.UUID(fid_b),)) == "open"
    # đối tượng kênh chưa có ở Marketing (không biết đội): chỉ phạm vi "all" đổi được
    stray = f"C{_tag()}"
    ch.add("campaign", stray, budget=1_000_000)
    assert (await executor.run(p["mkta"], "mkt.set_ad_status", {**other, "ref": stray, "status": "paused"})).status == "not_found"
    mine = await ok(p["mkta"], "mkt.set_ad_status", channel="adfake", level="campaign", ref=a["c"], status="paused")
    theirs = await ok(p["tpmkt"], "mkt.set_ad_status", **other, status="paused")   # trưởng phòng đổi được chiến dịch của mọi đội
    loose = await ok(p["tpmkt"], "mkt.set_ad_status", channel="adfake", level="campaign", ref=stray, status="paused")
    assert ch.applied == [{"level": "campaign", "ref": x, "status": "paused"} for x in (a["c"], b["c"], stray)]
    cid = {k: v["change_id"] for k, v in (("a", mine), ("b", theirs), ("stray", loose))}

    async def changes(who: str) -> set[str]:
        return {r["id"] for r in (await q(p[who], "mkt.ad_changes"))["rows"]}

    assert await owner_val("SELECT team_id::text FROM mkt.ad_changes WHERE id = %s", (uuid.UUID(cid["a"]),)) == dig
    assert await changes("mkta") == {cid["a"]} and await changes("mktb") == {cid["b"]}   # đội B thấy thay đổi trưởng phòng làm trên chiến dịch đội B
    assert {cid["a"], cid["b"], cid["stray"]} <= await changes("tpmkt")                 # không biết đội ⇒ chỉ phạm vi "all"
    # chính sách quảng cáo vẫn của CẢ công ty: người phạm vi "team" đọc được ngưỡng duyệt áp cho lần bấm của mình (không rơi về ngưỡng mặc định 20%)
    await ok(p["admin"], "mkt.set_ad_policy", budget_pct_max=500, budget_vnd_max="50tr")
    r = await executor.run(p["mkta"], "mkt.set_ad_budget", {"channel": "adfake", "level": "campaign", "ref": a["c"], "daily_budget": "3000000"})
    assert r.status == "ok", (r.status, r.error)
    assert "500%" in str(await q(p["mkta"], "mkt.ad_policy_page"))
    # chuyển đổi về kênh: theo đội của chiến dịch lead đến
    await ok(p["admin"], "mkt.set_ad_policy", conversions=True)
    for s, ref in ((a, "GD-A"), (b, "GD-B")):
        async with db.tenant_tx(system_principal(tid)) as conn:
            cx = core_ctx(conn, system_principal(tid), module="crm")
            await cx.call("crm.record_lead_outcome", lead_id=s["leads"].split(",")[0], outcome="won", value_vnd=10_000_000, ref=ref)
            await executor.flush_events(conn, system_principal(tid), cx.events)
        await events.drain()

    async def convs(who: str) -> set[str]:
        return {r["campaign"] for r in (await q(p[who], "mkt.ad_conversions"))["rows"]}

    assert await convs("mkta") == {a["c"]} and await convs("mktb") == {b["c"]} and await convs("tpmkt") >= {a["c"], b["c"]}
    await ok(p["admin"], "mkt.set_ad_policy")


async def test_a_company_with_ad_spend_but_no_policy_row_is_still_watched() -> None:
    """Rà 23/11/2026: vòng quét chỉ chọn công ty đã có dòng ``mkt.ad_policy`` — mà dòng đó chỉ sinh ở lượt theo dõi đầu ⇒ công ty chưa mở màn Chính sách
    không bao giờ được theo dõi. Nay: có chi phí quảng cáo mà chưa có dòng chính sách ⇒ vẫn đến lượt (chính sách mặc định)."""
    from barista.db import db as _db
    from barista.modules.mkt.ad_control import WATCH_DUE
    async with _db.owner_tx() as conn:
        spenders = {r["tenant_id"] for r in await (await conn.execute("SELECT DISTINCT tenant_id FROM mkt.spend")).fetchall()}
        due = {r["tenant_id"] for r in await (await conn.execute(WATCH_DUE)).fetchall()}
        unpoliced = {r["tenant_id"] for r in await (await conn.execute(
            "SELECT DISTINCT s.tenant_id FROM mkt.spend s WHERE NOT EXISTS (SELECT 1 FROM mkt.ad_policy p WHERE p.tenant_id = s.tenant_id)")).fetchall()}
    assert unpoliced <= due and unpoliced <= spenders
