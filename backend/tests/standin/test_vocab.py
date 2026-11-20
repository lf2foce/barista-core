"""TỪ VỰNG CÔNG TY qua một gói ngành (0124) — bản LÕI (ADR 0190) của ``barista-packs:real_estate/tests/test_vocab.py``: gói mang nhãn mặc định của ngành mình
(``Pack.vocab``), bật gói ⇒ công ty có sẵn nhãn đó (chỉ thêm phần thiếu, không ghi đè lựa chọn của công ty), mọi màn / form / công cụ trợ lý của mọi
vai hiện chữ CỦA CÔNG TY chứ không phải nhãn trung tính hay dấu nhãn, và dữ liệu LƯU (luồng trao đổi, yêu cầu ra ngoài, thông báo) là chữ thật."""

import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from barista import seed
from barista.db import db
from barista.kernel import notify, registry, vocab
from barista.kernel.forms import form_spec
from tests.conftest import Who, fresh_phone, ok, owner_val, q
from tests.standin import helpers, pack
from tests.test_vocab import _surfaces, crm_only  # noqa: F401

TERM, LABEL = "service_order", "Đơn dịch vụ"


def test_every_token_in_loaded_code_names_a_declared_term_and_every_pack_default_is_valid() -> None:
    """Dấu nhãn trong mã ĐANG NẠP (lõi + gói đã cài + gói thế vai) phải là khái niệm đã khai; nhãn mặc định của gói nào cũng chỉ đặt cho khái niệm đã khai."""
    token = re.compile(r"\[\[([a-z][a-z0-9_]*)(?:\|l)?\]\]")
    files = {Path(f) for name, m in list(sys.modules.items()) if name.startswith("barista") and (f := getattr(m, "__file__", None))}
    files.add(Path(pack.__file__))
    used = {(f.name, m) for f in files if f.suffix == ".py" for m in token.findall(f.read_text())}
    assert ("pack.py", TERM) in used, "gói thế vai phải viết chữ bằng dấu nhãn"
    assert not [u for u in used if u[1] not in vocab.TERMS], used
    for pk in registry.PACKS.values():
        assert set(pk.vocab) <= set(vocab.TERMS), pk.key
    assert registry.PACKS[pack.PACK].vocab[TERM] == LABEL and vocab.TERMS[TERM].label == "Đơn"


async def test_a_company_with_the_pack_reads_the_pack_label_on_every_screen_of_every_role(who: Who) -> None:
    """Công ty bật gói: nhãn mặc định của gói vào công ty; menu, màn, form, công cụ trợ lý của MỌI vai hiện nhãn đó — không nhãn trung tính ("Mở đơn"),
    không dấu nhãn nào lọt ra; chữ trong DÒNG dữ liệu (tiêu đề trang danh sách có đơn) cũng đổi."""
    await helpers.order(who, owner="sale1")                                            # có dòng ⇒ kết quả màn cũng được kiểm, không chỉ tiêu đề
    leaks: list[str] = []
    seen_form = seen_tool = seen_screen = False
    for local in ("giamdoc", "admin", "ketoan", "mkt", "truonga", "sale1", "salesadmin"):
        p = await who(local)
        assert (p.vocab or {}).get(TERM) == LABEL, local                                       # gói thế vai mang nhãn mặc định vào công ty khi bật
        surfaces = await _surfaces(p)
        leaks += [f"{local} · {where}" for where, text in surfaces.items() if "[[" in text]
        for where, text in surfaces.items():
            kind, _, name = where.partition(" ")
            op = registry.OPS.get(name)
            if op is None or not name.startswith("orderdesk.") or "[[" not in op.title:
                continue
            mine, neutral = vocab.resolve(op.title, p.vocab), vocab.resolve(op.title)
            assert mine != neutral and mine in text, (local, where)
            if kind == "form":
                assert json.loads(text)["title"] == mine, (local, where)
                seen_form = True
            elif kind == "tool":
                assert f"] {mine}" in text and f"] {neutral}." not in text, (local, where)   # tên công cụ trợ lý đọc: nhãn công ty
                seen_tool = True
            else:
                assert json.loads(text)["title"] == LABEL, (local, where)             # trang do thao tác trả về: executor đổi dấu nhãn
                seen_screen = True
        if "screen orderdesk.list" in surfaces:
            screens = [s for w in json.loads(surfaces["manifest"])["workspaces"] for s in w["screens"] if s["query"] == "orderdesk.list"]
            assert [s["title"] for s in screens] == [LABEL], local
    assert not leaks, leaks[:8]
    assert seen_form and seen_tool and seen_screen
    sale = await who("sale1")
    assert form_spec(registry.OPS["orderdesk.create"], sale.vocab)["title"] == "Mở đơn dịch vụ"
    assert form_spec(registry.OPS["orderdesk.create"], {})["title"] == "Mở đơn"         # công ty chưa có nhãn riêng: nhãn trung tính
    assert (await q(sale, "orderdesk.list"))["title"] == LABEL


async def test_turning_on_the_pack_installs_its_vocabulary_once_and_keeps_the_company_choice(crm_only: tuple[Any, str]) -> None:  # noqa: F811
    tid, d = crm_only
    admin = await seed.who(tid, "admin", d)
    assert await owner_val("SELECT count(*) FROM core.vocabulary WHERE tenant_id = %s", (tid,)) == 0
    await ok(admin, "admin.set_pack", pack=pack.PACK, enabled=True)
    assert await owner_val("SELECT label FROM core.vocabulary WHERE tenant_id = %s AND term = %s", (tid, TERM)) == LABEL
    assert await owner_val("SELECT count(*) FROM core.pack_installs WHERE tenant_id = %s AND kind = 'vocab'", (tid,)) == len(registry.PACKS[pack.PACK].vocab)
    assert (await seed.who(tid, "sale1", d)).vocab == registry.PACKS[pack.PACK].vocab
    await ok(await seed.who(tid, "admin", d), "admin.set_term", term=TERM, label="Phiếu việc")   # công ty đổi ý
    await ok(await seed.who(tid, "admin", d), "admin.set_pack", pack=pack.PACK, enabled=False)
    await ok(await seed.who(tid, "admin", d), "admin.set_pack", pack=pack.PACK, enabled=True)
    assert await owner_val("SELECT label FROM core.vocabulary WHERE tenant_id = %s AND term = %s", (tid, TERM)) == "Phiếu việc"   # bật lại không ghi đè
    assert (await seed.who(tid, "sale1", d)).vocab == {TERM: "Phiếu việc"}
    assert form_spec(registry.OPS["orderdesk.create"], (await seed.who(tid, "sale1", d)).vocab)["title"] == "Mở phiếu việc"


async def _stored_tokens() -> list[str]:
    """Mọi cột chữ / jsonb của luồng trao đổi, yêu cầu ra ngoài, hồ sơ quy trình và hộp thông báo còn dấu nhãn."""
    from psycopg import sql

    async with db.owner.connection() as conn:
        cur = await conn.execute("""SELECT table_schema AS s, table_name AS t, column_name AS c FROM information_schema.columns
                                    WHERE data_type IN ('text', 'character varying', 'jsonb') AND (table_schema IN ('talk', 'asks', 'flow')
                                          OR (table_schema = 'core' AND table_name = 'notifications'))""")
        cols = await cur.fetchall()
        out = []
        for c in cols:
            q_ = sql.SQL("SELECT count(*) AS n FROM {}.{} WHERE {}::text LIKE '%[[%'").format(sql.Identifier(c["s"]), sql.Identifier(c["t"]),
                                                                                                  sql.Identifier(c["c"]))
            if (await (await conn.execute(q_)).fetchone())["n"]:   # type: ignore[index]
                out.append(f"{c['s']}.{c['t']}.{c['c']}")
    return out


async def test_stored_titles_and_notifications_never_hold_a_label_token(who: Who) -> None:
    """Tên đối tượng mang dấu nhãn (lead chưa chọn thứ quan tâm) chảy sang luồng trao đổi, yêu cầu gửi ra ngoài và thông báo — dữ liệu LƯU là chữ công
    ty, không phải dấu nhãn; nơi ghi cuối cùng (``notify.send``) đổi dấu nhãn theo nhãn công ty — ở đây là nhãn mặc định của gói."""
    sale, lead_lead, admin = await who("sale1"), await who("truonga"), await who("admin")
    unset = f"chưa chọn {vocab.label('offering', sale.vocab, lower=True)}"
    lead = await ok(sale, "crm.create_lead", name=f"Khách chưa chọn {uuid.uuid4().hex[:4]}", phone=fresh_phone())
    await ok(lead_lead, "talk.post", subject_kind="crm.lead", subject_id=lead["lead_id"], body="Em xin ý kiến anh về khách này")
    await ok(admin, "asks.create", subject_kind="crm.lead", subject_id=lead["lead_id"], title="Bổ sung giấy tờ", recipient="Khách mẫu", items="- Số CCCD?")
    title = await owner_val("SELECT title FROM talk.threads WHERE subject_id = %s", (lead["lead_id"],))
    assert title.endswith(unset)                                                         # ảnh chụp theo nhãn công ty lúc ghi
    assert unset in await owner_val("SELECT subject_title FROM asks.requests WHERE subject_id = %s", (lead["lead_id"],))
    assert await owner_val("SELECT count(*) FROM core.notifications WHERE user_id = %s AND title LIKE %s", (sale.user_id, f"%{unset}")) >= 1
    # nơi ghi cuối cùng: ghi thẳng một thông báo có dấu nhãn ⇒ vẫn lưu chữ thật (nhãn của GÓI công ty đang bật)
    async with db.tenant_tx(sale) as conn:
        await notify.send(conn, [sale.user_id], kind="test", title="Xem [[service_order|l]] mới", body="[[service_order]] A", push="[[service_order|l]]")
    row = await owner_val("SELECT title || '|' || body || '|' || push FROM core.notifications WHERE user_id = %s AND kind = 'test'", (sale.user_id,))
    assert row == "Xem đơn dịch vụ mới|Đơn dịch vụ A|đơn dịch vụ"
    assert await _stored_tokens() == []
