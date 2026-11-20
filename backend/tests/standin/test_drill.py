"""NGUỒN GỐC CON SỐ (0097) trên màn của một gói ngành, qua gói thế vai: bản LÕI (ADR 0190) của ``barista-packs:real_estate/tests/test_drill.py`` — ô số khai
``drill`` thì bấm ra ĐÚNG các dòng tạo nên nó: đếm (hoặc cộng cột ``sum``) các dòng của nguồn = con số. Test quét MỌI màn mọi vai của công ty thế vai mở
được (màn của lõi + màn Đơn dịch vụ của gói): ô số nào khai nguồn mà lệch là đỏ — không cần nhớ từng ô."""

from typing import Any

from barista import workspaces
from barista.kernel import executor
from barista.kernel.principal import Principal
from tests.conftest import Who
from tests.standin import helpers

ROLES = ("giamdoc", "ketoan", "truonga", "sale1", "mkt", "admin")


def _kpis(page: Any) -> list[dict[str, Any]]:
    """Mọi ô số trong trang: KPI của bảng, khối kpis của thẻ, bảng lồng trong thẻ."""
    if not isinstance(page, dict):
        return []
    out = list(page.get("kpis") or [])
    for b in page.get("blocks") or []:
        if b.get("type") == "kpis":
            out += b.get("items") or []
        if b.get("type") == "table":
            out += _kpis(b.get("page"))
    return out


async def _checked(p: Principal) -> tuple[list[tuple[str, str, Any]], list[str]]:
    """Các ô số có nguồn đã kiểm ``(truy vấn của màn, nhãn ô, con số)`` + các ô lệch."""
    m = workspaces.manifest(p, "", mine=await workspaces.personal(p))   # cả app tự ghép (0098) của người đó
    screens = [s for w in m["workspaces"] for s in w["screens"]] + list(m.get("desk") or [])
    seen, bad, done = set(), [], []
    for s in screens:
        if (s["query"], s["key"]) in seen:
            continue
        seen.add((s["query"], s["key"]))
        r = await executor.query(p, s["query"], s.get("input") or {})
        if not r.ok:
            continue
        for k in _kpis(r.output):
            d = k.get("drill")
            if not d:
                continue
            src = await executor.query(p, d["query"], d["input"])
            assert src.ok and src.output["kind"] == "table", (s["query"], k["label"], src.error)
            rows = src.output["rows"]
            got = sum(int(x.get(d["sum"]) or 0) for x in rows) if d.get("sum") else len(rows)
            done.append((s["query"], k["label"], k["value"]))
            if got != k["value"]:
                bad.append(f"{p.email} · {s['query']} · “{k['label']}”: ô số {k['value']} ≠ nguồn {got}")
    return done, bad


async def test_every_declared_source_adds_up_to_its_number(who: Who) -> None:
    """Công ty thế vai không có dữ liệu nạp sẵn ⇒ dựng đơn mở / đã ký / đã huỷ của hai người (đơn ký sinh khoản phải thu phí bên Tài chính) để các ô
    số khác 0 — số 0 khớp nguồn rỗng không chứng minh gì."""
    await helpers.order(who, owner="sale1", amount=900_000_000)
    await helpers.signed(who, owner="sale1", amount=2_000_000_000)
    await helpers.signed(who, owner="sale3", amount=3_500_000_000)
    done, wrong = [], []
    for local in ROLES:
        d, bad = await _checked(await who(local))
        done += [(local, *x) for x in d]
        wrong += bad
    assert not wrong, wrong
    assert len(done) >= 6   # ít nhất các ô đã khai: đơn (đã ký, giá trị, phí) + phải thu (2) ở các vai có quyền
    orders = {(who_, label): v for who_, query, label, v in done if query == "orderdesk.list"}
    assert {label for _, label in orders} == {"Đã ký", "Giá trị đã ký", "Phí đã ký"}
    assert orders[("giamdoc", "Đã ký")] >= 2 and orders[("giamdoc", "Giá trị đã ký")] >= 5_500_000_000        # cả công ty
    assert 0 < orders[("sale1", "Đã ký")] < orders[("giamdoc", "Đã ký")]                                    # nguồn cũng theo quyền người xem
    assert any(query == "acct.receivables" and v > 0 for _, query, _, v in done)                           # ô số của lõi (phải thu) cũng có nguồn khớp
