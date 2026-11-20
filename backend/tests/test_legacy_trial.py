"""Khung nạp thử (ADR 0106 §6) trên dữ liệu giả: lặp lại không trùng, thiếu dữ kiện thành ngoại lệ, không phát sinh tiền, tên cột cũ chỉ ở lớp nguồn."""

from datetime import date
from pathlib import Path
from uuid import UUID

from scripts.legacy_trial.load import Report, load_people, reconcile
from scripts.legacy_trial.tms_source import Person, person_of
from tests.conftest import Who, owner_val
from tests.module_src import module_files


class DictMap:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], UUID] = {}

    async def get(self, entity: str, source_id: str) -> UUID | None:
        return self.data.get((entity, source_id))

    async def put(self, entity: str, source_id: str, target_id: UUID, fingerprint: str) -> None:
        self.data[(entity, source_id)] = target_id


def test_missing_facts_become_issues_not_invented_values() -> None:
    p = person_of({"id": 7, "full_name": "  Người Giả ", "account_type": 1, "employee_code": None, "started_day": None, "resigned_day": None, "manager_id": None})
    assert p.outside and p.name == "Người Giả" and p.employee_code == "" and p.started_on is None
    assert set(p.issues) == {"thiếu mã nhân viên", "thiếu ngày vào"}


async def test_load_is_repeatable_and_touches_no_money(who: Who) -> None:
    admin = await who("admin")
    tag = str(id(admin))[-6:]
    people = [
        Person(source_id=f"m{tag}", name="Quản lý giả", outside=False, employee_code=f"Q{tag}", title="Trưởng phòng", started_on=date(2024, 1, 2)),
        Person(source_id=f"s{tag}", name="Nhân viên giả", outside=False, employee_code=f"S{tag}", started_on=date(2024, 2, 1), manager_source_id=f"m{tag}"),
        Person(source_id=f"c{tag}", name="Cộng tác giả", outside=True, issues=["thiếu mã nhân viên", "thiếu ngày vào"]),
        Person(source_id=f"x{tag}", name="Lạc giả", outside=True, manager_source_id="không-có"),
    ]
    ids = DictMap()
    journals = await owner_val("SELECT count(*) FROM ledger.journals WHERE tenant_id=%s", (admin.tenant_id,))
    first = await load_people(admin, people, ids, as_of=date(2026, 11, 6))
    assert (first.parties_created, first.employments, first.assignments) == (4, 2, 1)
    assert first.issues["quản lý ngoài tập nạp"] == 1
    again = await load_people(admin, people, ids, as_of=date(2026, 11, 6))
    assert (again.parties_created, again.parties_reused, again.employments, again.assignments) == (0, 4, 0, 0)
    assert await owner_val("SELECT count(*) FROM ledger.journals WHERE tenant_id=%s", (admin.tenant_id,)) == journals


def test_reconcile_names_what_was_not_loaded() -> None:
    lines = reconcile(Report(seen=3, employments=1), 3, {"contracts": 12})
    assert any("chưa nạp" in x and "contracts" in x for x in lines)


def test_legacy_names_stay_in_the_source_layer() -> None:
    root = Path(__file__).parent.parent
    legacy = ("nguoi_cham_soc", "chi_tiet_dot_thanh_toan", "dot_thanh_toan", "ty_le_hoa_hong")
    allowed = root / "scripts/legacy_trial/tms_source.py"
    for f in [*(root / "scripts/legacy_trial").glob("*.py"), *(f for files in module_files().values() for f in files), *(root / "src/barista/migrations").glob("0[12]*.sql")]:
        if f != allowed:
            text = f.read_text()
            assert not [w for w in legacy if w in text], f
