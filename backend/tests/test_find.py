"""Tìm nhanh ⌘K (0087, ``core.find``): mỗi module tự khai nguồn tìm, gom theo ĐÚNG quyền người hỏi — khách người khác không lộ, thiếu quyền /
gói tắt thì bỏ nhóm, không lỗi."""

import uuid
from typing import Any

import pytest

from barista.kernel.search import SearchTerms
from tests import money_source
from tests.conftest import Who, fresh_phone, ok, q
from tests.test_journey import _blocks


def _groups(page: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {b["title"]: b["items"] for b in _blocks(page, "list")}


async def test_find_degrades_quietly(who: Who) -> None:
    sale1, mkt = await who("sale1"), await who("mkt")
    page = await q(sale1, "core.find", q="x")
    assert _blocks(page, "callout")[0]["title"].startswith("Gõ ít nhất")
    none = await q(sale1, "core.find", q="zzzzzzzz-khong-co")
    assert "Không thấy" in _blocks(none, "callout")[0]["title"]
    assert "Khách" not in _groups(await q(mkt, "core.find", q="Lan"))                  # marketing không có quyền khách ⇒ bỏ nhóm, không lỗi


async def test_find_reaches_documents_plans_and_tasks_and_follows_shelf_rights(who: Who) -> None:
    """Không chỉ gói Bán hàng: tài liệu (theo kệ người tìm đọc được), kế hoạch, việc — cùng một ô."""
    admin, sale1, gd = await who("admin"), await who("sale1"), await who("giamdoc")
    tag = uuid.uuid4().hex[:6]
    await ok(admin, "kb.add_document", title=f"Quy trình tìm {tag}", kind="process", body="Bước 1. Gọi khách.")
    shelf = (await ok(admin, "kb.save_shelf", name=f"Kệ kín {tag}", roles=["director", "admin"]))["key"]
    await ok(admin, "kb.add_document", title=f"Biên bản kín {tag}", shelf=shelf, kind="other", body="Nội dung kín.")
    docs = _groups(await q(sale1, "core.find", q=tag)).get("Tài liệu", [])
    assert [d["title"] for d in docs] == [f"Quy trình tìm {tag}"]                       # kệ chỉ GĐ + quản trị đọc ⇒ sale không thấy cả tên
    assert {d["title"] for d in _groups(await q(gd, "core.find", q=tag))["Tài liệu"]} == {f"Quy trình tìm {tag}", f"Biên bản kín {tag}"}
    plan = await ok(gd, "flow.propose_plan", title=f"Kế hoạch tìm {tag}", team_code="MKT",
                    tasks=[{"title": f"Việc tìm {tag}", "assignee_email": (await who("mkt2")).email}])
    found = _groups(await q(gd, "core.find", q=tag))
    assert found["Kế hoạch"][0]["open"] == {"query": "flow.plan", "input": {"plan_id": str(plan["plan_id"])}}
    assert found["Việc"][0]["title"] == f"Việc tìm {tag}"
    groups = list(_groups(await q(gd, "core.find", q=tag)))
    assert groups.index("Tài liệu") < groups.index("Kế hoạch") < groups.index("Việc")   # nhóm Khách có thể khớp phần số của tag


async def test_tables_declare_other_views_of_the_same_rows(who: Who) -> None:
    """Bảng ↔ Kanban ↔ Lịch (0087) ↔ Dòng thời gian (0222): server khai góc nhìn trên CÙNG các dòng; kéo thẻ chỉ ở bảng có lệnh (khách → crm.set_stage), cột cần hỏi thêm
    (Mất ⇒ lý do) mở form; bảng tiền / kế hoạch chỉ xem."""
    leads = await q(await who("truonga"), "crm.leads")
    board = leads["views"]["board"]
    assert board["by"] == "pipeline" and board["move"]["command"] == "crm.set_stage" and "lost" in board["move"]["form_columns"]   # 0153: cột = chặng của công ty
    assert leads["views"]["calendar"]["date"] == "next_follow_up"
    assert "move" not in (await q(await who("ketoan"), "acct.receivables"))["views"]["board"]
    plans = await q(await who("giamdoc"), "flow.plans")
    assert set(plans["views"]) == {"board", "calendar", "timeline"} and "move" not in plans["views"]["board"]   # 0222: + dòng thời gian


def _tag() -> str:
    """Chỉ chữ cái (không chữ số — từ khoá toàn số là tìm SĐT): một từ riêng cho mỗi lần chạy."""
    return "".join("abcdeghkmn"[int(ch, 16) % 10] for ch in uuid.uuid4().hex[:7])


def _titles(page: dict[str, Any], group: str) -> list[str]:
    return [i["title"] for i in _groups(page).get(group, [])]


def test_search_terms_fold_split_and_take_phone_digits_only_from_a_number() -> None:
    t = SearchTerms("  Nguyễn  THỊ Lý ")
    assert t.q == "Nguyễn THỊ Lý" and t.folded == "nguyen thi ly" and t.words == ("nguyen", "thi", "ly")
    assert SearchTerms("Đặng").words == ("dang",)
    assert SearchTerms("0912 345 678").digits == "912345678" and SearchTerms("6499").digits == "6499"
    assert SearchTerms("E608").digits == "" and SearchTerms("GD-SGB-E608").words == ("gd", "sgb", "e608")   # mã có số không phải SĐT
    assert SearchTerms("50%_x").words == ("50", "x")                                                       # ký tự đại diện không lọt vào mẫu
    assert SearchTerms("--").match("c.name") == "(false)"


async def test_find_ignores_accents_and_word_order_and_ranks_the_closest_name_first(who: Who) -> None:
    """"dang thi ly" (không dấu) ra "Đặng Thị Lý" (khớp đúng — lên đầu), tên nhập ngược "Lý Đặng Thị" (trọn từ), rồi tên chỉ chứa ("Lýna").
    Sale khác cùng đội không thấy khách của người khác qua ô tìm, dù trúng từ khoá (RLS)."""
    sale1, sale2 = await who("sale1"), await who("sale2")
    tag = _tag()
    for name in (f"Đặng Thị Lýna {tag}", f"{tag} Lý Đặng Thị Hoa", f"Đặng Thị Lý {tag}"):
        await ok(sale1, "crm.create_lead", name=name, phone=fresh_phone())
    found = _titles(await q(sale1, "core.find", q=f"dang thi ly {tag}"), "Khách")
    assert found == [f"Đặng Thị Lý {tag}", f"{tag} Lý Đặng Thị Hoa", f"Đặng Thị Lýna {tag}"]
    assert _titles(await q(sale1, "core.find", q=f"ĐẶNG THỊ LÝ {tag.upper()}"), "Khách") == found   # có dấu / chữ hoa: cùng kết quả
    assert "Khách" not in _groups(await q(sale2, "core.find", q=f"dang thi ly {tag}"))
    rows = (await q(sale1, "crm.leads", stage="", q=f"ly dang {tag}"))["rows"]                     # ô "Tìm" của Khách & lead khớp cùng cách
    assert {r["customer"] for r in rows} == set(found)


async def test_a_phone_is_searched_only_when_the_query_is_a_number(who: Who) -> None:
    sale1 = await who("sale1")
    tag, phone = _tag(), fresh_phone()
    await ok(sale1, "crm.create_lead", name=f"Khách số {tag}", phone=phone)
    assert f"Khách số {tag}" in _titles(await q(sale1, "core.find", q=phone[-6:]), "Khách")
    assert f"Khách số {tag}" not in _titles(await q(sale1, "core.find", q=f"E{phone[-6:]}"), "Khách")   # "E608" là mã căn, không phải đuôi SĐT


async def test_more_than_eight_hits_end_with_a_see_all_row_that_opens_the_filtered_list(who: Who) -> None:
    sale1 = await who("sale1")
    tag = _tag()
    for i in range(10):
        await ok(sale1, "crm.create_lead", name=f"Khách nhiều {tag} {'abcdefghkm'[i]}", phone=fresh_phone())
    items = _groups(await q(sale1, "core.find", q=f"khach nhieu {tag}"))["Khách"]
    assert len(items) == 9 and items[-1]["open"] == {"query": "crm.leads", "input": {"stage": "", "q": f"khach nhieu {tag}"}}
    assert items[-1]["title"].startswith("Xem tất cả")
    assert len((await q(sale1, items[-1]["open"]["query"], **items[-1]["open"]["input"]))["rows"]) == 10


async def test_find_reaches_parties_with_the_right_card_for_each_viewer(who: Who) -> None:
    """Danh mục cá nhân & tổ chức (chủ đầu tư, đối tác…): người xem được liên hệ mở hồ sơ; kế toán (không quyền liên hệ) mở danh mục đã lọc; sale không có nhóm."""
    admin, kt, sale1 = await who("admin"), await who("ketoan"), await who("sale1")
    tag = _tag()
    pid = (await ok(admin, "parties.create", display_name=f"Công ty Đầu tư Ánh Dương {tag}", kind="organization"))["party_id"]
    hit = _groups(await q(admin, "core.find", q=f"anh duong {tag}"))["Cá nhân & tổ chức"][0]
    assert hit["title"] == f"Công ty Đầu tư Ánh Dương {tag}" and hit["open"] == {"query": "parties.person", "input": {"party_id": str(pid)}}
    assert _groups(await q(kt, "core.find", q=f"cong ty dau tu {tag}"))["Cá nhân & tổ chức"][0]["open"]["query"] == "parties.list"
    assert [r["display_name"] for r in (await q(kt, "parties.list", q=f"dau tu {tag}"))["rows"]] == [f"Công ty Đầu tư Ánh Dương {tag}"]
    assert "Cá nhân & tổ chức" not in _groups(await q(sale1, "core.find", q=f"anh duong {tag}"))


async def test_find_reaches_receivables_by_payment_code_within_the_askers_scope(who: Who, monkeypatch: pytest.MonkeyPatch) -> None:
    money_source.use(monkeypatch)
    f = await money_source.revenue(who)
    kt = await who("ketoan")
    hit = _groups(await q(kt, "core.find", q=f["code"].lower()))["Khoản phải thu"][0]
    assert hit["title"].startswith(f["code"]) and hit["open"] == {"query": "acct.receivables", "input": {"status": "", "q": f["code"]}}
    assert [r["code"] for r in (await q(kt, "acct.receivables", **hit["open"]["input"]))["rows"]] == [f["code"]]
    assert "Khoản phải thu" not in _groups(await q(f["sale"], "core.find", q=f["code"]))   # sale không phụ trách khoản này


async def test_find_reaches_campaigns_without_accents(who: Who) -> None:
    mkt, sale1 = await who("mkt"), await who("sale1")
    tag = _tag()
    await ok(mkt, "mkt.upsert_campaign", code=f"FB-{tag.upper()}", name=f"Căn hộ Bùi Tá Hán {tag}")
    hit = _groups(await q(mkt, "core.find", q=f"can ho bui ta han {tag}"))["Chiến dịch"][0]
    assert hit["title"] == f"FB-{tag.upper()} — Căn hộ Bùi Tá Hán {tag}" and hit["open"]["query"] == "mkt.ads"
    assert "Chiến dịch" not in _groups(await q(sale1, "core.find", q=f"bui ta han {tag}"))
