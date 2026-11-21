"""Danh sách chọn khai ``creates=`` (api 1.71): ô chọn rỗng có lối "Tạo mới". Mọi ``creates`` trỏ tới lệnh có thật, người dùng được thao tác thật; form chỉ mang
``options_create`` khi NGƯỜI XEM chạy được lệnh tạo (không có quyền ⇒ không có nút)."""

import uuid
from typing import Any

import pytest

from barista import seed
from barista.kernel import registry
from tests.conftest import q

PEOPLE = [("chu", "Chủ mẫu", "admin", "QT", True), ("sale1", "Bán hàng mẫu", "sale", "KD-A", False)]


def test_every_creates_points_at_a_public_command_and_a_query_that_exists() -> None:
    assert registry.CREATES, "chưa truy vấn chọn nào khai creates="
    for query, command in registry.CREATES.items():
        assert query in registry.OPS and registry.OPS[query].kind == "query", query
        op = registry.OPS.get(command)
        assert op is not None and op.kind == "command" and not op.internal, (query, command)


@pytest.fixture(scope="module")
async def co() -> dict[str, Any]:
    d = f"{uuid.uuid4().hex[:6]}.demo"
    tid = await seed.create_tenant(f"Thử tạo mới {d}", d, people=PEOPLE, packs=seed.ALL_PACKS)
    return {"chu": await seed.who(tid, "chu", d), "sale1": await seed.who(tid, "sale1", d)}


def _spec_of(p: Any, command: str) -> dict[str, Any]:
    from barista.http_api import _options_create
    from barista.kernel.forms import form_spec

    spec = form_spec(registry.OPS[command], p.vocab, p.fields)
    _options_create(spec["fields"], p)
    return spec


async def test_the_form_carries_the_create_command_only_for_who_may_run_it(co: dict[str, Any]) -> None:
    await q(co["chu"], "papers.template_options")   # nạp danh sách (rỗng ở công ty chưa có mẫu)
    owner = {f["key"]: f.get("options_create") for f in _spec_of(co["chu"], "papers.start_draft")["fields"]}
    assert owner["template_id"] == {"command": "papers.create_template", "title": "Tạo mẫu văn bản"}
    other = _spec_of(co["sale1"], "papers.start_draft")["fields"] if co["sale1"].can("papers", "write") else []
    assert all(not f.get("options_create") for f in other)   # vai không quản lý mẫu ⇒ không thấy nút


async def test_find_time_accepts_one_person_sent_as_a_plain_string(co: dict[str, Any]) -> None:
    """Bộ lọc chọn MỘT người gửi chuỗi chứ không phải danh sách — trước đây lỗi "Input should be a valid list" (rà UI 20/11)."""
    from barista.kernel import executor

    r = await executor.query(co["chu"], "schedule.find_time", {"attendee_emails": co["chu"].email, "resource_ids": ""})
    assert r.status == "ok", (r.status, r.error)


def test_every_starter_process_template_parses() -> None:
    """Mẫu quy trình dựng sẵn phải qua đúng bộ phân tích của form (không để người bấm mẫu rồi gặp lỗi cú pháp)."""
    from barista.modules.flow.cases import parse_template
    from barista.modules.flow.starters import STARTERS

    assert len(STARTERS) >= 3
    for name, _, text in STARTERS:
        stages = parse_template(text, "")
        assert stages and all(st["items"] for st in stages), name
