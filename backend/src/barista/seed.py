"""Dữ liệu mẫu (GIẢ — tên công ty, khách, SĐT đều bịa; SĐT đầu 010 không tồn tại ở VN).

CÔNG TY MẪU của lõi là công ty thương mại – dịch vụ TRUNG TÍNH (ADR 0187): người, đội, vai, khách / lead, chiến dịch, chia lead, quy trình, việc, trao
đổi, tài chính (tài khoản ngân hàng, phiếu chi phí, nợ nhà cung cấp, tiền về chưa phân bổ) — chỉ gói của lõi. Phần của một NGÀNH (vd sàn môi giới:
dự án, căn, giao dịch, hoa hồng) do gói ngành đã cài góp qua điểm vào ``barista.seed_samples`` (``samples()``); file này không nêu tên gói ngành nào.

Khung công ty (vai trò, ma trận quyền, đội, người, chuỗi duyệt) ghi bằng owner; dữ liệu nghiệp vụ đi qua CHÍNH các lệnh
(executor) bằng vai người thật — seed chạy được nghĩa là cả luồng chạy được.

    uv run python -m barista.seed --reset     # xoá sạch DB dev, migrate, nạp lại
"""

import argparse
import asyncio
import dataclasses
import importlib.metadata
import inspect
import random
from collections.abc import Awaitable, Callable
from datetime import timedelta
from types import ModuleType
from typing import Any
from uuid import UUID

import barista.modules  # noqa: F401
from barista import presets
from barista.config import settings
from barista.db import db
from barista.kernel import auth, events, executor, packs, principal
from barista.kernel.clock import add_months, month_of, today
from barista.kernel.migrate import migrate
from barista.kernel.principal import Principal, system_principal

# Tên công ty demo là CẤU HÌNH của bản cài (BARISTA_DEMO_COMPANY), không phải của lõi — mặc định đánh dấu rõ là dữ liệu giả.
TENANT = settings.demo_company.strip() or "Công ty mẫu (dữ liệu giả)"
COMPANY = settings.demo_company.strip() or "Công ty mẫu"
BRAND = settings.demo_company.strip() or "Mẫu"   # phần tên riêng ghép vào tên pháp nhân GIẢ
# Pháp nhân (0099): công ty mẫu nhiều pháp nhân — mặc định "CHINH" (tên công ty) + hai pháp nhân dịch vụ / miền Nam. Tên, mã số thuế GIẢ; gói ngành
# gọi lại tên theo ngành của nó (phần mẫu ``prepare``).
ENTITIES = [("MG", f"Công ty CP Dịch vụ {BRAND}", "0100000001"), ("MN", f"Công ty TNHH {BRAND} Miền Nam", "0100000002")]
DOMAIN = "sanmau.demo"   # tên miền đăng nhập dev của công ty mẫu (app / script dev ghi sẵn ``<vai>@sanmau.demo``) — chỉ là chuỗi giả
# Thứ công ty mẫu bán khi không gói ngành nào góp danh mục (``OFFERINGS`` của phần mẫu): chữ tự do trên lead / chiến dịch — (mã, tên gọi ngắn)
OFFERINGS = (("GOI-A", "Gói A"), ("GOI-B", "Gói B"))

# Kinh doanh theo cây: KHU VỰC › KHỐI KINH DOANH › TEAM — trưởng mỗi tầng thấy cả cây con (core.teams.parent_id, kernel/teams.managed)
TEAMS = [("HQ", COMPANY, "company", None),
         ("KV-MB", "Khu vực Miền Bắc", "branch", "HQ"), ("KHOI-1", "Khối kinh doanh 1", "sales", "KV-MB"),
         ("KD-A", "Team Kinh doanh A", "sales", "KHOI-1"), ("KD-B", "Team Kinh doanh B", "sales", "KHOI-1"),
         ("KV-MN", "Khu vực Miền Nam", "branch", "HQ"), ("KHOI-2", "Khối kinh doanh 2", "sales", "KV-MN"),
         ("KD-C", "Team Kinh doanh C", "sales", "KHOI-2"),
         ("MKT", "Marketing", "marketing", "HQ"), ("DIG", "Digital", "marketing", "MKT"), ("KT", "Kế toán", "accounting", "HQ"), ("QT", "Quản trị", "admin", "HQ"),
         ("HCNS", "Hành chính – Nhân sự", "department", "HQ")]   # gói ngoài lõi góp thêm đội / người qua ``samples()``

# email, tên, vai trò, đội, trưởng đội
PEOPLE = [
    ("giamdoc", "Phạm Minh Đức", "director", "HQ", True),
    ("admin", "Lê Thu Hà", "admin", "QT", True),
    ("ketoan", "Vũ Thị Mai", "accountant", "KT", True),
    ("mkt", "Đỗ Quang Huy", "marketer", "MKT", True),
    ("digital", "Trịnh Minh Thư", "coordinator", "DIG", True),
    ("truonga", "Trần Bảo Nam", "team_lead", "KD-A", True),
    ("sale1", "Nguyễn Văn An", "sale", "KD-A", False),
    ("sale2", "Hoàng Thị Bích", "sale", "KD-A", False),
    ("truongb", "Bùi Thanh Tùng", "team_lead", "KD-B", True),
    ("sale3", "Đặng Gia Khánh", "sale", "KD-B", False),
    # quản lý từng tầng (vai "Trưởng nhóm" ở nút cao hơn ⇒ phạm vi đội = cả cây con): GĐ khu vực, GĐ khối
    ("gdmb", "Phan Hữu Quân", "team_lead", "KV-MB", True),
    ("gdkhoi1", "Lương Thị Diệp", "team_lead", "KHOI-1", True),
    ("gdmn", "Kiều Văn Sơn", "team_lead", "KV-MN", True),
    ("gdkhoi2", "Mạc Thị Yến", "team_lead", "KHOI-2", True),
    ("truongc", "Lâm Ngọc Châu", "team_lead", "KD-C", True),
    ("sale4", "Hồ Đức Phát", "sale", "KD-C", False),
    # khối văn phòng (0073): vai theo VIỆC, phòng là đội — sales admin nhập liệu không quản trị hệ thống; HCNS có trưởng phòng
    ("salesadmin", "Lý Thu Trang", "sales_admin", "KD-A", False),
    ("hcns", "Mai Thanh Hương", "office", "HCNS", True),
    ("nhansu", "Phan Văn Lộc", "staff", "HCNS", False),
    ("mkt2", "Võ Thị Hạnh", "marketer", "MKT", False),
    ("mkt3", "Tạ Quốc Bảo", "marketer", "MKT", False),
    ("tpmkt", "Quách Minh Tâm", "marketing_head", "MKT", False),   # trưởng phòng marketing: đọc hết chi phí + lead cả công ty
]

# Chức danh GIẢ ghi vào Hồ sơ làm việc (gói hr, 0107) — màn chọn vai hiện chức danh này (``Module.titles``), vai quyền vẫn theo PEOPLE.
# Ai không có tên ở đây ⇒ chức danh mặc định theo vai (``ROLE_TITLES``).
TITLES = {
    "giamdoc": "Tổng giám đốc", "admin": "Trưởng phòng Quản trị hệ thống", "ketoan": "Kế toán trưởng", "mkt": "Trưởng phòng Marketing",
    "digital": "Trưởng nhóm Digital (điều phối lead)", "gdmb": "Giám đốc Khu vực Miền Bắc", "gdmn": "Giám đốc Khu vực Miền Nam",
    "gdkhoi1": "Giám đốc Khối kinh doanh 1", "gdkhoi2": "Giám đốc Khối kinh doanh 2", "truonga": "Trưởng team Kinh doanh A",
    "truongb": "Trưởng team Kinh doanh B", "truongc": "Trưởng team Kinh doanh C", "salesadmin": "Chuyên viên Sales admin",
    "hcns": "Trưởng phòng Hành chính – Nhân sự", "nhansu": "Chuyên viên nhân sự", "mkt2": "Chuyên viên Marketing nội dung",
    "mkt3": "Chuyên viên quảng cáo số",
}
ROLE_TITLES = {"sale": "Chuyên viên kinh doanh", "team_lead": "Trưởng nhóm kinh doanh", "director": "Giám đốc", "accountant": "Kế toán"}

NAMES = ["Nguyễn Thị Lan", "Trần Văn Bình", "Lê Hoàng Yến", "Phạm Quốc Việt", "Hoàng Minh Thư", "Vũ Đức Long", "Đặng Thu Trang",
         "Bùi Văn Hải", "Đỗ Ngọc Ánh", "Hồ Thanh Sơn", "Ngô Bảo Châu", "Dương Mỹ Linh", "Lý Gia Huy", "Mai Phương Thảo",
         "Trịnh Công Minh", "Phan Thị Hoa", "Võ Hữu Phước", "Tạ Minh Khoa", "Lương Thùy Dung", "Châu Tuấn Kiệt",
         "Kiều Anh Thơ", "Đinh Quang Vinh", "Cao Thị Nhung", "La Văn Tài"]


# Tài liệu mẫu (GIẢ) của công ty thương mại – dịch vụ; tài liệu của ngành do gói ngành góp.
DOCS: list[dict[str, str]] = [
    {"title": "Chính sách bán hàng và chiết khấu 2026 (mẫu)", "shelf": "common", "kind": "policy",
     "body": """# Chiết khấu
Khách thanh toán đủ trong 7 ngày kể từ ngày ký hợp đồng: chiết khấu 2% giá trị hợp đồng (trước VAT).
Khách ký từ gói thứ hai trở lên trong cùng năm: chiết khấu thêm 1% cho gói thứ hai.
# Quà tặng
Hợp đồng từ 200 triệu đồng: tặng một buổi đào tạo sử dụng tại văn phòng khách.
# Thanh toán
Đặt cọc 30% khi ký hợp đồng; 40% khi bàn giao; 30% còn lại trong 30 ngày sau nghiệm thu.
# Bảo hành
Bảo hành 12 tháng kể từ ngày nghiệm thu; phản hồi yêu cầu hỗ trợ trong 4 giờ làm việc."""},
    {"title": "Kịch bản tư vấn khách hàng mới (mẫu)", "shelf": "common", "kind": "script",
     "body": """# Mở đầu
Hỏi khách đang dùng giải pháp nào, quy mô bao nhiêu người, ngân sách dự kiến và thời điểm cần triển khai.
# Gợi ý
Khách dưới 20 người: giới thiệu Gói A; khách từ 20 người hoặc nhiều chi nhánh: giới thiệu Gói B.
# Xử lý từ chối
Khách chê giá: so sánh tổng chi phí 12 tháng và nhấn chiết khấu 2% khi thanh toán sớm."""},
    {"title": "Quy trình đối chiếu công nợ và thanh toán nhà cung cấp", "shelf": "finance", "kind": "process",
     "body": """# Định kỳ
Cuối mỗi tháng kế toán đối chiếu công nợ từng nhà cung cấp theo hoá đơn đã ghi, gửi biên bản trong 5 ngày làm việc.
# Đề nghị chi
Hoá đơn ghi nợ nhà cung cấp phải có đề nghị chi được duyệt trước khi chuyển khoản.
# Tiền về
Tiền về chưa rõ khoản nào: ghi là tiền chưa phân bổ, hỏi bộ phận kinh doanh trong 2 ngày làm việc.
# Ghi sổ
Phiếu thu nhập theo sao kê ngân hàng, ghi mã giao dịch ngân hàng để không ghi hai lần."""},
    {"title": "Định hướng kinh doanh quý I/2027 (mẫu — nội bộ ban giám đốc)", "shelf": "management", "kind": "other",
     "body": """# Mục tiêu
Doanh số ký 12 tỷ; mở thêm một nhóm kinh doanh tại khu vực phía Nam.
# Ngân sách
Ngân sách marketing tối đa 400 triệu; ưu tiên kênh có chi phí trên mỗi khách thấp nhất quý trước."""},
]


ONBOARD_FLOW = """# Giấy tờ | Quản trị
- Hợp đồng lao động đã ký
- CCCD (bản chụp)
> Gửi hợp đồng lao động cho nhân sự mới | 1 ngày
# Tài khoản & đào tạo | Trưởng nhóm
- Đã cấp tài khoản hệ thống [xác nhận: admin]
- Đã học chính sách bán hàng và chiết khấu
- Đã đi gặp khách cùng người kèm (tuỳ chọn)
> Kèm đi gặp khách một buổi | 7 ngày"""


async def reset_schemas(owner_url: str) -> None:
    from psycopg import AsyncConnection
    async with await AsyncConnection.connect(owner_url, autocommit=True) as conn:
        # mọi schema của bản cài (do role chủ sở hữu tạo: module + dbos) — không giữ danh sách tay phải nhớ thêm khi có module mới
        cur = await conn.execute("SELECT nspname FROM pg_namespace WHERE nspowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)"
                                 " AND nspname <> 'public'")
        for (s,) in await cur.fetchall():
            await conn.execute(f'DROP SCHEMA IF EXISTS "{s}" CASCADE')  # type: ignore[arg-type]
        await conn.execute("DROP TABLE IF EXISTS public.barista_migrations, public.barista_pack_migrations")   # sổ cũ (một dãy số) + sổ theo gói (0130)


# Ánh xạ MISA mẫu của tài khoản do LÕI khai — GIẢ ĐỊNH, kế toán khách xác nhận (ADR 0104 §10). Người hưởng có thể là tổ chức ngoài nên "payable" không
# đặt 334. Tài khoản do gói ngành khai: gói tự ánh xạ trong phần mẫu của nó (``ensure_account_map(tid, <bảng của gói>)``).
ACCOUNT_MAP = [
    ("cash:bank", "1121", "Tiền gửi ngân hàng VND"), ("receivable:customers", "131", "Phải thu khách hàng"),
    ("revenue:services", "5113", "Doanh thu cung cấp dịch vụ"),
    ("payout:clearing", "3388", "Phải trả khác"), ("payable", "3388", "Phải trả khác — nghĩa vụ với người hưởng"),
    ("advance", "141", "Tạm ứng"), ("recovery", "1388", "Phải thu khác — khoản cần thu hồi"),
    ("tax:withheld", "3335", "Thuế thu nhập cá nhân"), ("revenue:advance_charge", "515", "Doanh thu tài chính — phí ứng"),
    ("unapplied:bank", "3388", "Phải trả khác — tiền về chưa phân bổ"),
    ("revenue:other_income", "711", "Thu nhập khác"), ("expense:bonus", "641", "Chi phí bán hàng — thưởng"),   # 0156: danh mục khoản thu / chi mặc định
    ("expense:marketing", "641", "Chi phí bán hàng — marketing"), ("expense:other", "811", "Chi phí khác"),
    # 0170: phiếu chi phí — tiền mặt, thuế GTGT đầu vào, tài khoản chi phí riêng công ty khai (expense:<mã>) chưa ánh xạ riêng ⇒ chi phí quản lý; 0165: đầu ra
    ("cash:on_hand", "1111", "Tiền mặt VND"), ("tax:vat_input", "1331", "Thuế GTGT được khấu trừ của hàng hoá, dịch vụ"),
    ("tax:vat_output", "33311", "Thuế GTGT đầu ra phải nộp"), ("expense", "642", "Chi phí quản lý doanh nghiệp — mặc định cho khoản chi chưa ánh xạ riêng"),
]
# Tài khoản ngân hàng mẫu (0151) — ngân hàng, số tài khoản, TÊN CỘT đều GIẢ: hai kiểu file hay gặp (hai cột ghi có / ghi nợ; một cột số tiền có dấu,
# tiêu đề tiếng Anh). Mẫu cột thật của từng ngân hàng khách dùng là việc cấu hình khi có file sao kê thật.
BANK_ACCOUNTS = [
    {"code": "NHA-MG", "label": "Ngân hàng Mẫu A — pháp nhân MG", "bank": "Ngân hàng Mẫu A", "account_no": "0101000123", "entity_code": "MG",
     "col_date": "Ngày giao dịch", "col_credit": "Số tiền ghi có", "col_debit": "Số tiền ghi nợ", "col_memo": "Nội dung", "col_ref": "Số tham chiếu",
     "col_party": "Tên đối ứng"},
    {"code": "NHB-MN", "label": "Ngân hàng Mẫu B — pháp nhân MN", "bank": "Ngân hàng Mẫu B", "account_no": "0202000456", "entity_code": "MN",
     "col_date": "Booking Date", "col_signed": "Amount", "col_memo": "Description", "col_ref": "Reference", "col_party": "Counterparty"},
]
ALL_PACKS = ("crm", "kpi", "finance", "marketing", "talk", "training", "hr")   # công ty mẫu trong TEST: đủ gói của lõi
#                                                                     (+ gói đã cài có phần mẫu — ``samples()``, ``sample_packs()``)
# Công ty mẫu mặc định (``--profile sample``): gói của lõi; gói đã cài tự khai mình thuộc hồ sơ nào (``PROFILES`` của phần mẫu — ``profile_packs``),
# gói khác công ty bật thêm ở Gói & ứng dụng nếu bản cài cung cấp
SAMPLE_PACKS = ("crm", "kpi", "finance", "marketing", "talk", "hr")


SAMPLE_GROUP = "barista.seed_samples"   # gói ngoài lõi góp phần mẫu vào CÔNG TY MẪU (đội, người, dữ liệu) — điểm vào ``<khoá gói> = "<module>"``


def samples() -> dict[str, ModuleType]:
    """Phần mẫu do GÓI ĐÃ NẠP khai (điểm vào ``barista.seed_samples``, tên = khoá gói) — lõi không biết tên gói. Module mang (mọi tên đều tuỳ chọn):
    ``TEAMS`` / ``PEOPLE`` (góp vào khung công ty mẫu); ``PROFILES`` — gói BẬT ở biến thể nào của công ty mẫu: ``"full"`` (công ty đủ gói của bộ test —
    mặc định khi không khai), ``"sample"`` (công ty mẫu mặc định) và / hoặc tên hồ sơ của một gói (vd ``"san-moi-gioi"``, ``"san-moi-gioi-demo"``; ``()`` = không tự bật
    ở đâu); ``OFFERINGS = ((mã, tên gọi ngắn), …)`` — thứ công ty mẫu BÁN khi gói bật (gói đầu tiên khai thắng; không gói nào khai ⇒ ``OFFERINGS`` của
    lõi, chữ tự do): lõi đặt chiến dịch / lead / luật chia lead mẫu theo các mã này; ``async prepare(tid)`` — TRƯỚC dữ liệu nghiệp vụ của lõi, chỉ khi
    công ty bật gói: danh mục mà dòng mẫu của lõi trỏ tới (các mã ``OFFERINGS``), chính sách, tài liệu của ngành; ``async seed(tid)`` — SAU dữ liệu
    nghiệp vụ của lõi: nghiệp vụ của ngành trên khách / lead đã có (trả ``dict`` thì gộp vào kết quả ``seed_business`` — bộ test đọc); ``async
    finish(tid) -> str | None`` — bước CUỐI của hồ sơ và của ``--upgrade`` khi công ty bật gói (phải chạy lại an toàn; trả dòng để in); ``COMPANIES =
    {"<hồ sơ nhiều công ty>": async () -> {"done": chữ, …}}`` — một công ty mẫu gói góp vào hồ sơ nhiều công ty của lõi (hiện có ``pay-demo``).
    Gói chưa nạp ⇒ bỏ qua."""
    from barista.kernel import registry
    return {ep.name: ep.load() for ep in sorted(importlib.metadata.entry_points(group=SAMPLE_GROUP), key=lambda e: e.name)
            if ep.name in registry.PACKS}


def sample_packs() -> tuple[str, ...]:
    """Gói của công ty mẫu ĐỦ GÓI (công ty test): gói của lõi + gói ngoài lõi đã cài có phần mẫu khai ``"full"`` (mặc định — như lúc gói còn ở lõi)."""
    return (*ALL_PACKS, *profile_packs("full"))


def profile_packs(profile: str) -> tuple[str, ...]:
    """Gói ngoài lõi đã cài tự khai mình bật ở biến thể ``profile`` của công ty mẫu (``PROFILES`` của phần mẫu; không khai = chỉ ``"full"``)."""
    return tuple(k for k, mod in samples().items() if profile in getattr(mod, "PROFILES", ("full",)))


async def sample_finish(tid: UUID) -> None:
    """Bước cuối của phần mẫu các gói công ty ĐANG BẬT (``finish`` — vd nạp dữ liệu cần mọi thứ khác đã có); in dòng gói trả về."""
    on = {r["pack"] for r in await _owner_rows("SELECT pack FROM core.tenant_packs WHERE tenant_id = %s", (tid,))}
    for key, mod in samples().items():
        if key in on and (fn := getattr(mod, "finish", None)) is not None and (line := await fn(tid)):
            print(line)


def sample_people() -> list[tuple[str, str, str, str, bool]]:
    return [*PEOPLE, *(x for mod in samples().values() for x in getattr(mod, "PEOPLE", ()))]


async def create_tenant(name: str, domain: str, people: list[tuple[str, str, str, str, bool]] | None = None,
                        packs: tuple[str, ...] | None = None, about: str = "công ty thương mại – dịch vụ (mẫu)") -> UUID:
    """Khung công ty: đội + người của công ty mẫu (owner — chưa có ai để chạy lệnh), rồi cài cấu hình MẶC ĐỊNH của phần nền + các gói chọn
    (vai, quyền, đội, chuỗi + luật duyệt, mẫu quy trình — ``barista/presets.py``) qua đúng lệnh ``admin.install_defaults`` mà bật gói sau cũng dùng.
    Không nêu ``people`` / ``packs`` ⇒ người / gói của công ty mẫu (kể cả phần mẫu của gói ngoài lõi đã cài — ``samples()``)."""
    from barista.kernel.registry import always_on, pack_closure
    people = sample_people() if people is None else people
    packs = sample_packs() if packs is None else packs
    packs = tuple(sorted(pack_closure(set(packs)) - always_on()))   # gói luôn bật (0157) không có dòng tenant_packs — install_defaults cài kèm "base"
    async with db.owner_tx() as c:
        cur = await c.execute("INSERT INTO core.tenants (name) VALUES (%s) RETURNING id", (name,))
        row = await cur.fetchone()
        assert row is not None
        tid: UUID = row["id"]
        for pack in packs:   # gói nghiệp vụ (0045) — công ty mẫu dùng đủ; kèm quyền dùng gói (156) để tắt rồi bật lại không cần cấp lại
            await c.execute("INSERT INTO core.tenant_packs (tenant_id, pack) VALUES (%s, %s)", (tid, pack))
            await c.execute("INSERT INTO core.tenant_entitlements (tenant_id, pack, granted_by, note) VALUES (%s, %s, 'seed', 'dữ liệu mẫu')",
                            (tid, pack))
        # hồ sơ công ty cho trợ lý (0056) — cấu hình của công ty mẫu, không viết cứng trong code
        await c.execute("INSERT INTO core.tenant_profile (tenant_id, about) VALUES (%s, %s)", (tid, about))
        # vai người mẫu đang giữ phải có trước khi thêm người (gói chưa bật vẫn cần vai để gán; quyền thì chỉ theo gói đang bật)
        for key in sorted({role for _, _, role, _, _ in people}):
            await c.execute("INSERT INTO core.roles (tenant_id, key, label) VALUES (%s, %s, %s)", (tid, key, presets.role_label(key)))
        team_ids: dict[str, UUID] = {}
        for code, tname, kind, parent in [*TEAMS, *(x for mod in samples().values() for x in getattr(mod, "TEAMS", ()))]:
            cur = await c.execute("INSERT INTO core.teams (tenant_id, code, name, kind, parent_id) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                                  (tid, code, tname if code != "HQ" else name, kind, team_ids.get(parent or "")))
            r = await cur.fetchone()
            assert r is not None
            team_ids[code] = r["id"]
        led: set[str] = set()   # một đội một trưởng tại một thời điểm (0256): danh sách mẫu khai hai trưởng cùng đội ⇒ người khai TRƯỚC giữ, người sau là thành viên
        for local, pname, role, team, lead in people:
            lead = lead and team not in led
            if lead:
                led.add(team)
            cur = await c.execute(
                "INSERT INTO core.users (email, name) VALUES (%s, %s) ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                (f"{local}@{domain}", pname))
            r = await cur.fetchone()
            assert r is not None
            await c.execute("INSERT INTO core.memberships (tenant_id, user_id, role) VALUES (%s, %s, %s)", (tid, r["id"], role))
            await c.execute("INSERT INTO core.team_members (tenant_id, user_id, team_id, is_lead) VALUES (%s, %s, %s, %s)",
                            (tid, r["id"], team_ids[team], lead))
        # kệ tài liệu (0051) — ai đọc là DỮ LIỆU, quản trị sửa được: Chung = cả công ty; Kế toán; Ban giám đốc
        for key, sname, everyone, roles in (("common", "Chung", True, []), ("finance", "Kế toán", False, ["accountant", "director"]),
                                            ("management", "Ban giám đốc", False, ["admin", "director"])):
            await c.execute("INSERT INTO kb.shelves (tenant_id, key, name, everyone, roles) VALUES (%s, %s, %s, %s, %s)",
                            (tid, key, sname, everyone, roles))
    executor.raise_for(await executor.run(system_principal(tid), "admin.install_defaults", {"packs": ["base", *packs]}))
    return tid


async def who(tenant_id: UUID, local: str, domain: str = DOMAIN) -> Principal:
    async with db.owner.connection() as conn:
        p = await auth.load(conn, tenant_id, email=f"{local}@{domain}")
    assert p is not None, local
    return p


async def run(p: Principal, name: str, /, **inp: Any) -> Any:
    if name in executor.DECISIONS:   # dữ liệu mẫu đóng vai người bấm Duyệt trong app (quyết định chỉ từ tay cầm EDGE còn hạn — 0144)
        with principal.leased():
            return executor.raise_for(await executor.run(principal.edge(dataclasses.replace(p)), name, inp))
    return executor.raise_for(await executor.run(p, name, inp))


async def seed_titles(tid: UUID, admin: Principal, since: str, titles: dict[str, str] | None = None, by_role: dict[str, str] | None = None) -> int:
    """Chức danh GIẢ của từng người (Hồ sơ làm việc, gói hr) — theo người, không thì theo vai, không thì nhãn vai: chưa có hồ sơ ⇒ ghi từ ``since`` (mã NV#### theo thứ tự email); hồ sơ đang mở khác chức danh
    ⇒ ghi chức danh mới từ hôm nay (dòng cũ tự đóng). Chạy lại an toàn (bản demo đang chạy: ``--upgrade``). Trả số dòng đã ghi."""
    titles, by_role = TITLES if titles is None else titles, ROLE_TITLES if by_role is None else by_role
    rows = await _owner_rows("""SELECT p.id AS party, u.email, m.role, e.employee_code AS code, e.title, e.entity_id, e.started_on FROM core.memberships m
                                JOIN core.users u ON u.id = m.user_id JOIN parties.parties p ON p.tenant_id = m.tenant_id AND p.user_id = u.id
                                LEFT JOIN LATERAL (SELECT employee_code, title, entity_id, started_on FROM hr.employments x WHERE x.party_id = p.id AND x.ended_on IS NULL
                                                   ORDER BY started_on DESC LIMIT 1) e ON true
                                WHERE m.tenant_id = %s AND m.active ORDER BY u.email""", (tid,))
    used = {r["code"] for r in rows if r["code"]}
    fresh = (f"NV{i:04d}" for i in range(1, 10_000) if f"NV{i:04d}" not in used)
    n = 0
    for r in rows:
        title = titles.get(r["email"].split("@")[0]) or by_role.get(r["role"]) or presets.role_label(r["role"])
        if title == r["title"]:
            continue
        if r["code"] and (r["entity_id"] is not None or r["started_on"] >= today()):
            continue   # hồ sơ theo pháp nhân (người dùng ghi) không đè; hồ sơ mới từ hôm nay ⇒ ngày đổi phải sau ngày đó — để mai
        await run(admin, "hr.set_employment", party_id=str(r["party"]), employee_code=r["code"] or next(fresh), title=title,
                  started_on=today().isoformat() if r["code"] else since)
        n += 1
    return n


async def _samples_on(tid: UUID) -> dict[str, ModuleType]:
    """Phần mẫu của các gói công ty ĐANG BẬT (theo khoá gói)."""
    on = {r["pack"] for r in await _owner_rows("SELECT pack FROM core.tenant_packs WHERE tenant_id = %s", (tid,))}
    return {k: mod for k, mod in samples().items() if k in on}


def offerings_of(mods: dict[str, ModuleType]) -> tuple[tuple[str, str], ...]:
    """Thứ công ty mẫu bán: ``OFFERINGS`` của gói đang bật đầu tiên có khai (danh mục của ngành), không thì chữ tự do của lõi."""
    return next((tuple(mod.OFFERINGS) for mod in mods.values() if getattr(mod, "OFFERINGS", None)), OFFERINGS)


async def seed_business(tid: UUID) -> dict[str, Any]:
    """Dữ liệu nghiệp vụ của công ty mẫu, qua CHÍNH các lệnh bằng vai người thật. Phần của ngành: ``prepare`` (trước) / ``seed`` (sau) của phần mẫu các
    gói công ty đang bật — xem ``samples()``. Trả ``{"tenant_id": …}`` + mọi ``dict`` mà ``seed`` của gói trả."""
    rnd = random.Random(42)
    t = today()
    admin, ketoan, mkt = await who(tid, "admin"), await who(tid, "ketoan"), await who(tid, "mkt")
    for team in ("KD-A", "KD-B", "KD-C"):   # lịch làm việc là dữ liệu công ty (mig 169): đội kinh doanh làm cả cuối tuần, văn phòng theo mặc định nghỉ T7–CN
        await run(system_principal(tid), "flow.set_work_calendar", team_code=team, calendar="vn-ca-tuan")
    sales = {k: await who(tid, k) for k in ("sale1", "sale2", "sale3")}

    # Pháp nhân (0099): mặc định "CHINH" (tên công ty) + hai pháp nhân mẫu. Tên / mã số thuế / địa chỉ GIẢ (địa chỉ là ô chung in trên giấy — 0118).
    for code, name, tax in ENTITIES:
        await run(admin, "admin.upsert_entity", code=code, name=name, tax_code=tax, address="Số 1 phố Mẫu, quận Mẫu, Hà Nội")

    mods = await _samples_on(tid)
    for mod in mods.values():   # danh mục / chính sách của ngành mà dòng mẫu bên dưới trỏ tới
        if (fn := getattr(mod, "prepare", None)) is not None:
            await fn(tid)
    offerings = offerings_of(mods)
    (a, a_name), (b, b_name) = offerings[0], offerings[1 % len(offerings)]

    # Ánh xạ MISA (GIẢ ĐỊNH — chờ khách xác nhận) + tài khoản ngân hàng
    for prefix, acc, label in ACCOUNT_MAP:
        await run(ketoan, "acct.set_account_map", ledger_prefix=prefix, misa_account=acc, label=label + " — giả định")
    for bank in BANK_ACCOUNTS:   # tài khoản ngân hàng + mẫu cột sao kê (GIẢ — mẫu thật của ngân hàng khách chưa có, 0151): dữ liệu công ty, sửa trong app
        await run(ketoan, "acct.set_bank_account", **bank)

    # Kho tài liệu + thông tin công ty trên giấy tờ (GIẢ)
    giamdoc = await who(tid, "giamdoc")
    for d in DOCS:
        await run(giamdoc, "kb.add_document", **d)
    await run(admin, "papers.set_settings", company_name=f"Công ty cổ phần {COMPANY}", short_name="".join(w[0] for w in COMPANY.split()).upper()[:4],
              address="Số 1 phố Mẫu, quận Mẫu, Hà Nội", tax_code="0000000000", phone="0100 000 000",
              bank_account="0000 0000 0000", bank_name="Ngân hàng Mẫu — chi nhánh Hà Nội", city="Hà Nội",
              signer_title="Giám đốc", signer_name="Phạm Minh Đức")

    # Marketing
    camps = [(f"FB-{a}-T10", f"FB lead form {a_name}", "facebook", a, 180_000_000, 45),
             (f"GG-{a}-T10", f"Google search {a_name}", "google", a, 90_000_000, 45),
             (f"TT-{b}-T10", f"TikTok {b_name}", "tiktok", b, 60_000_000, 30)]
    for code, name, ch, off, budget, days in camps:
        await run(mkt, "mkt.upsert_campaign", code=code, name=name, channel=ch, offering_code=off, budget_vnd=budget,
                  start_on=(t - timedelta(days=days)).isoformat(), end_on=(t + timedelta(days=30)).isoformat())
        for d in range(days, 0, -1):
            await run(mkt, "mkt.record_spend", campaign_code=code, day=(t - timedelta(days=d)).isoformat(),
                      amount_vnd=rnd.randint(budget // 120, budget // 70) // 1000 * 1000, leads_platform=rnd.randint(0, 5))

    # Khách / lead
    phone = 100000001
    for i, name in enumerate(NAMES):
        owner = ("sale1", "sale2", "sale3")[i % 3]
        camp = camps[i % 3] if i % 2 == 0 else None
        bd = t + timedelta(days=rnd.randint(0, 6)) if i % 5 == 0 else t - timedelta(days=rnd.randint(30, 300))
        off = camp[3] if camp else rnd.choice([a, b])
        source = "ads" if camp else rnd.choice(["self", "referral", "telesales"])
        budget = rnd.choice([3, 4, 5, 6, 7]) * 10**9
        out = await run(sales[owner], "crm.create_lead", name=name, phone=f"0{phone + i}", offering_code=off, source=source,
                        campaign_code=camp[0] if camp else "", budget_vnd=budget, birthday=bd.replace(year=rnd.randint(1975, 1995)).isoformat())
        if i % 3 != 2:
            await run(sales[owner], "crm.log_activity", lead_id=out["lead_id"], kind=rnd.choice(["call", "zalo", "send_profile"]),
                      note="Đã gửi báo giá, khách hẹn gặp cuối tuần", next_follow_up=(t - timedelta(days=rnd.randint(-3, 2))).isoformat())
        if i % 4 == 0:
            await run(sales[owner], "crm.set_stage", lead_id=out["lead_id"], stage="prospect")
    # Khu vực Miền Nam (team C): ít khách hơn — để GĐ khu vực / khối thấy đúng cây của mình, GĐ công ty thấy cả hai khu vực
    sale4 = await who(tid, "sale4")
    for i, name in enumerate(["Ninh Bảo Ngọc", "Quách Thành Đạt", "Trương Mỹ Lệ", "Phí Hoàng Long", "Viên Thị Thảo"]):
        out = await run(sale4, "crm.create_lead", name=name, phone=f"0{100070001 + i}", offering_code=b, source=("self", "referral")[i % 2],
                        budget_vnd=rnd.choice([4, 5, 6]) * 10**9)
        if i % 2 == 0:
            await run(sale4, "crm.log_activity", lead_id=out["lead_id"], kind="call", note="Khách quan tâm, hẹn gọi lại",
                      next_follow_up=(t + timedelta(days=i)).isoformat())

    # Chia lead (GIẢ ĐỊNH — công ty chỉnh ở Kinh doanh › Lead đi đâu): luật chung tự chia cho sale; lead QUẢNG CÁO về hàng chờ team Digital
    # để người điều phối chia (có gợi ý). Lead sự kiện vào hệ thống tự chia và sale đã gọi ngay (để thẻ "gọi đúng hạn" có số thật).
    common = dict(team_code="HQ", roles=["sale"], strategy="rotate", cap_uncalled=10, first_call_min=15, followup_hours=48, max_recalls=2,
                  days="mon_sat", open_at="08:30", close_at="18:00", effective_from="2026-01-01")
    await run(admin, "leadflow.set_rule", name="Chia lead chung (giả định)", **common)
    await run(admin, "leadflow.set_rule", name="Lead quảng cáo qua team Digital (giả định)", source="ads", mode="coordinator",
              coord_team_code="DIG", **common)
    for i, nm in enumerate(["Lương Khánh Vy", "Tạ Quốc Hưng", "Âu Ngọc Diệp"]):
        await run(admin, "leadflow.intake", name=nm, phone=f"0{100090001 + i}", offering_code=a, source="ads", campaign_code=camps[0][0],
                  provider="meta", external_ref=f"seed-{i}", wishes="cần tư vấn trong tuần", budget_vnd=4 * 10**9)
    by_id = {str(p.user_id): p for p in sales.values()}
    for i, nm in enumerate(["Kha Minh Triết", "Lạc Thu Trang"]):
        got = await run(admin, "leadflow.intake", name=nm, phone=f"0{100090011 + i}", offering_code=b, source="event", wishes="hẹn gặp tại văn phòng")
        if (holder := by_id.get(str(got["assigned_to"]))) is not None:
            await run(holder, "crm.log_activity", lead_id=got["lead_id"], kind="call", outcome="connected", note="Gọi ngay sau sự kiện")

    # Quy trình: hồ sơ nhận người mới mở tay (quy trình tự do); hồ sơ của ngành tự mở theo nghiệp vụ của gói
    await run(admin, "flow.set_template", key="nhan_su_moi", name="Nhận sale mới (giả định)", source=ONBOARD_FLOW)
    await run(giamdoc, "flow.open_case", key="nhan_su_moi", title="Nhận sale mới — Nguyễn Văn An", owner_email=f"truonga@{DOMAIN}")
    await _seed_appointments(sales)
    await _seed_tasks(tid, sales)
    await seed_rooms(tid)
    await _seed_finance(tid)
    await events.drain()

    out: dict[str, Any] = {"tenant_id": tid}
    for mod in mods.values():   # nghiệp vụ của ngành trên khách / lead đã có — chỉ gói công ty bật
        if (fn := getattr(mod, "seed", None)) is not None and isinstance(got := await fn(tid), dict):
            out |= got
    if "training" in {r["pack"] for r in await _owner_rows("SELECT pack FROM core.tenant_packs WHERE tenant_id = %s", (tid,))}:
        await seed_training(tid)
    await seed_plan(tid)
    await seed_kpi(tid)
    return out


# Tài chính mẫu chỉ bằng đường của LÕI (không gói ngành): một phiếu chi phí đã trả, một hoá đơn ghi nợ nhà cung cấp (nghĩa vụ chờ đề nghị chi), một
# khoản tiền về chưa rõ của ai — theo danh mục khoản chi MẶC ĐỊNH (công ty chưa khai danh mục riêng). Khoản phải thu / phiếu thu sinh từ nghiệp vụ của
# gói nguồn (họ ``receivable``) — lõi không có lệnh nào cho người dùng tự lập khoản phải thu.
async def _seed_finance(tid: UUID) -> None:
    ketoan, t = await who(tid, "ketoan"), today()
    await run(ketoan, "acct.record_expense", category="other", amount_vnd=33_000_000, vat_rate="10", supplier="Công ty Cho thuê Văn phòng Mẫu",
              supplier_tax_code="0100000091", doc_no="VP-0001", bill_on=month_of(t).isoformat(), bank_ref="FT26EXP0001", memo="Thuê văn phòng tháng này")
    await run(ketoan, "acct.record_expense", category="marketing", amount_vnd=45_000_000, mode="payable", supplier="Công ty Truyền thông Mẫu",
              supplier_tax_code="0100000092", doc_no="QC-0001", bill_on=month_of(t).isoformat(), due_on=(t + timedelta(days=20)).isoformat(),
              memo="Quảng cáo trực tuyến đợt 1")
    await run(ketoan, "acct.record_unapplied", amount_vnd=12_000_000, received_on=month_of(t).isoformat(), memo="CK KHACH LE CHUA RO HOP DONG",
              payer="NGUYEN VAN MAU", bank_ref="FT26IN0001")


# KPI mẫu (0101): chỉ tiêu cuộc gọi tháng trước + tháng này cho từng sale — thực tế tính từ dữ liệu mẫu ở trên. Chỉ tiêu theo chỉ số của ngành
# (vd giao dịch ký) do phần mẫu của gói ngành đặt.
KPI_CALLS = {"sale1": 60, "sale2": 50, "sale3": 45, "sale4": 30}


async def seed_kpi(tid: UUID) -> None:
    """Chỉ tiêu nhập qua đúng lệnh kpi.set_target (như quản lý nhập tay / Excel). Chạy lặp lại an toàn (đặt lại cùng số)."""
    if "kpi" not in {r["pack"] for r in await _owner_rows("SELECT pack FROM core.tenant_packs WHERE tenant_id = %s", (tid,))}:
        return
    admin = await who(tid, "admin")
    this = today().replace(day=1)
    last = (this - timedelta(days=1)).replace(day=1)
    for period in (last, this):
        for local, calls in KPI_CALLS.items():
            await run(admin, "kpi.set_target", period=f"{period:%Y-%m}", metric="crm.metric_calls", user_email=f"{local}@{DOMAIN}", target=calls)


# Kế hoạch mẫu (0073): giám đốc giao một đợt ra mắt cho NHIỀU PHÒNG — đang chạy được một tuần, 2 việc xong, 1 việc QUÁ HẠN ⇒ mở thẻ là thấy
# tiến độ từng phòng, cờ "Trễ" và nút nhắc / giao lại do code chọn. Tạo bằng đúng lệnh trợ lý soạn (flow.propose_plan); chỉ phần "đã trôi
# qua một tuần" (ngày tạo, ngày xong, một hạn đã qua) chỉnh thẳng bằng owner — lệnh không cho đặt hạn trong quá khứ.
DEMO_PLAN = "Ra mắt gói dịch vụ mới — đợt 1"
DEMO_PLAN_TASKS = [  # việc, người, hạn (ngày tính từ hôm nay), trạng thái
    ("Chốt key visual & bộ ấn phẩm ra mắt", "mkt2", 1, "done"),
    ("Landing page + form đăng ký tham dự", "digital", 2, "done"),
    ("Dự trù & tạm ứng ngân sách sự kiện", "ketoan", -2, "todo"),
    ("Chạy quảng cáo thu lead đợt ra mắt", "mkt3", 5, "doing"),
    ("Kịch bản bán hàng & đào tạo đội kinh doanh", "truonga", 10, "todo"),
    ("Hợp đồng mẫu & phiếu đăng ký", "salesadmin", 12, "todo"),
    ("Hậu cần, địa điểm & quà tặng sự kiện", "hcns", 14, "todo"),
    ("Mời khách cũ & khách tiềm năng tham dự", "truongb", 17, "todo"),
]


# Giai đoạn + phụ thuộc của việc mẫu (0222) — để dòng thời gian của kế hoạch có nhóm và mũi tên ngay trên dữ liệu mẫu: việc → (giai đoạn, chờ việc nào, bắt đầu sau hôm nay mấy ngày)
DEMO_PLAN_SCHEDULE = {
    "Chốt key visual & bộ ấn phẩm ra mắt": ("Chuẩn bị", "", None),
    "Landing page + form đăng ký tham dự": ("Chuẩn bị", "Chốt key visual & bộ ấn phẩm ra mắt", 1),
    "Dự trù & tạm ứng ngân sách sự kiện": ("Chuẩn bị", "", None),
    "Chạy quảng cáo thu lead đợt ra mắt": ("Chạy", "Landing page + form đăng ký tham dự", 3),
    "Kịch bản bán hàng & đào tạo đội kinh doanh": ("Chạy", "", 3),
    "Hợp đồng mẫu & phiếu đăng ký": ("Chạy", "Kịch bản bán hàng & đào tạo đội kinh doanh", 6),
    "Hậu cần, địa điểm & quà tặng sự kiện": ("Sự kiện", "Dự trù & tạm ứng ngân sách sự kiện", 7),
    "Mời khách cũ & khách tiềm năng tham dự": ("Sự kiện", "Chạy quảng cáo thu lead đợt ra mắt", 8),
}


async def seed_plan(tid: UUID) -> None:
    if await _owner_rows("SELECT 1 FROM flow.plans WHERE tenant_id = %s AND title = %s", (tid, DEMO_PLAN)):
        return
    gd = await who(tid, "giamdoc")
    t0 = today()
    out = await run(gd, "flow.propose_plan", title=DEMO_PLAN, team_code="MKT", due_on=(t0 + timedelta(days=21)).isoformat(),
                    goal="Đủ ấn phẩm, lead và khách mời cho sự kiện ra mắt; đội kinh doanh nắm sản phẩm, hợp đồng sẵn sàng trước ngày ra mắt.",
                    tasks=[{"title": title, "assignee_email": f"{local}@{DOMAIN}", "due_on": (t0 + timedelta(days=max(days, 1))).isoformat(),
                            "phase": DEMO_PLAN_SCHEDULE[title][0], "after": DEMO_PLAN_SCHEDULE[title][1],
                            **({"start_on": (t0 + timedelta(days=DEMO_PLAN_SCHEDULE[title][2])).isoformat()} if DEMO_PLAN_SCHEDULE[title][2] is not None else {})}
                           for title, local, days, _ in DEMO_PLAN_TASKS])
    pid = out["plan_id"]
    for title, local, days, status in DEMO_PLAN_TASKS:
        tid_ = (await _owner_rows("SELECT id FROM flow.tasks WHERE plan_id = %s AND title = %s", (pid, title)))[0]["id"]
        if status != "todo":
            await run(await who(tid, local), "flow.set_task", task_id=str(tid_), status=status)
        if days < 1:
            await _owner_rows("UPDATE flow.tasks SET due_on = %s WHERE id = %s RETURNING id", (t0 + timedelta(days=days), tid_))
    await _owner_rows("UPDATE flow.plans SET created_at = now() - interval '7 days' WHERE id = %s RETURNING id", (pid,))
    await _owner_rows("""UPDATE flow.tasks SET created_at = now() - interval '7 days',
                                done_at = CASE WHEN status = 'done' THEN now() - interval '3 days' END WHERE plan_id = %s RETURNING id""", (pid,))


async def ensure_account_map(tid: UUID, rows: list[tuple[str, str, str]] | None = None) -> list[str]:
    """Thêm ánh xạ MISA mẫu còn thiếu (``rows`` — mặc định bảng của lõi; gói ngành đưa bảng tài khoản của nó), KHÔNG đè tiền tố kế toán đã có (kể cả
    đã sửa) — chạy lại an toàn trên bản cài đang chạy. Trả các tiền tố vừa thêm."""
    have = {r["ledger_prefix"] for r in await _owner_rows("SELECT ledger_prefix FROM acct.account_map WHERE tenant_id = %s", (tid,))}
    added = []
    for prefix, acc, label in (ACCOUNT_MAP if rows is None else rows):
        if prefix not in have:
            await run(system_principal(tid), "acct.set_account_map", ledger_prefix=prefix, misa_account=acc, label=label + " — giả định")
            added.append(prefix)
    return added


async def install_defaults_everywhere() -> int:
    """Phần đầu của ``--upgrade``, chạy cho MỌI công ty trong DB (chạy lặp an toàn): mặc định mới của các gói đang bật qua admin.install_defaults
    (chỉ thêm phần thiếu) — vd chuỗi + luật "đổi quyền qua chủ tài khoản", chủ tài khoản đầu tiên, vai nhập môn (0029). Không có bước này thì
    bản phát hành mới chạy mà luật chưa có ⇒ quản trị vẫn tự đổi quyền được (runbook 11). Trả số công ty đã cài."""
    rows = await _owner_rows("SELECT t.id, t.name, coalesce(array_agg(p.pack) FILTER (WHERE p.pack IS NOT NULL), '{}') AS packs"
                             " FROM core.tenants t LEFT JOIN core.tenant_packs p ON p.tenant_id = t.id GROUP BY t.id, t.name ORDER BY t.created_at", ())
    for r in rows:
        out = await run(system_principal(r["id"]), "admin.install_defaults", packs=["base", *sorted(r["packs"])])
        if out.get("summary"):
            print(f"{r['name']}: {out['summary']}")
    return len(rows)


DEMO_AUTONOMY = "full"   # bản DEMO: trợ lý "Toàn quyền" (0144) để người xem thấy trợ lý tự làm + Hoàn tác; công ty thật mặc định tắt


async def demo_autonomy(tid: UUID) -> str | None:
    """Đặt mức tự chủ của BẢN DEMO qua đúng lệnh ``admin.set_agent_preset`` (quyền hệ thống: không phiếu; ghi dữ liệu đã có — công tắc + chính
    sách). CHỈ khi công ty CHƯA từng đặt (không dòng ``core.agent_trust`` / ``core.agent_policies``) — chạy lại / ``--upgrade`` không đè lựa chọn
    người xem demo đã đổi. Chỉ gọi từ hồ sơ demo (san-moi-gioi-demo, hồ sơ của gói) và ``upgrade_demo``; ``install_defaults`` (mọi công ty) không đụng."""
    touched = await _owner_rows("""SELECT 1 AS x FROM core.agent_trust WHERE tenant_id = %(t)s
                                   UNION ALL SELECT 1 FROM core.agent_policies WHERE tenant_id = %(t)s LIMIT 1""", {"t": tid})
    if touched:
        return None
    await run(system_principal(tid), "admin.set_agent_preset", preset=DEMO_AUTONOMY)
    return DEMO_AUTONOMY


BUILTIN_PROFILES = ("sample", "pay-demo")   # công ty mẫu trung tính của lõi (+ gói đã cài tự khai thuộc hồ sơ này) · nhiều công ty của gói Lương
PROFILE_GROUP = "barista.seed_profiles"   # gói ngoài lõi khai hồ sơ nạp mẫu của bản cài nó (vd ``bank``, ``accounting``, ``research``)


def pack_profile(name: str) -> ModuleType | None:
    """Hồ sơ nạp mẫu do một GÓI ĐÃ CÀI khai (điểm vào ``barista.seed_profiles``: ``<tên> = "<module>"``) — lõi không biết tên gói. Module hồ sơ
    mang ``PACK`` (gói phải bật ở công ty demo), ``NAME`` (tên công ty mặc định), ``async seed(name) -> tenant_id``, ``DONE`` (dòng in khi xong),
    tuỳ chọn ``AUTONOMY = False`` (không đặt mức tự chủ demo); ``seed`` nhận thêm ``extra_packs`` nếu khai tham số đó (``--with-packs``); tuỳ chọn
    ``async upgrade(tid)`` — phần của hồ sơ trong ``--upgrade`` (không khai ⇒ chỉ đặt mức tự chủ demo — ``upgrade_pack_demo``).
    Tên trùng hồ sơ có sẵn / hai gói cùng tên ⇒ dừng. Không có ⇒ None."""
    eps = [ep for ep in importlib.metadata.entry_points(group=PROFILE_GROUP) if ep.name == name]
    if not eps:
        return None
    if name in BUILTIN_PROFILES or len(eps) > 1:
        raise SystemExit(f"Dừng: hồ sơ nạp mẫu {name} khai hai lần ({', '.join(sorted(str(e.dist.name if e.dist else '?') for e in eps))})")
    return eps[0].load()


def profiles() -> list[str]:
    return [*BUILTIN_PROFILES, *sorted(ep.name for ep in importlib.metadata.entry_points(group=PROFILE_GROUP))]


async def upgrade_pack_demo(tid: UUID, on: set[str], pack: str) -> None:
    """``--upgrade --profile <hồ sơ của gói>`` (vd ``bank``) trên bản cài demo của gói: mặc định mới đã cài ở ``install_defaults_everywhere``; dữ liệu
    của gói là màn trình diễn ⇒ chỉ đặt mức tự chủ demo như ``upgrade_demo`` — KHÔNG thêm người / kế hoạch / câu chuyện của sàn mẫu."""
    if pack not in on:
        raise SystemExit(f"Dừng: công ty demo không bật gói {pack} — đây không phải bản cài của hồ sơ này (đổi --profile)")
    if settings.demo_company.strip() and await demo_autonomy(tid):   # bản cài DEMO (BARISTA_DEMO_COMPANY) — cài thật không
        print("Trợ lý demo: Toàn quyền (tự làm việc nhỏ đảo được, có Hoàn tác)")
    await events.drain()
    print(f"OK: demo của gói {pack} đã cập nhật")


async def upgrade_demo(profile: str = "sample") -> None:
    """Đưa bản demo ĐANG CHẠY lên dữ liệu mẫu mới mà không xoá gì (chạy lặp lại an toàn). ``profile`` = hồ sơ nạp mẫu của bản cài (file bản cài ở
    barista-ops, khoá "seed_profile"): hồ sơ của gói ⇒ ``upgrade`` của hồ sơ nếu có khai, không thì ``upgrade_pack_demo``; ``sample`` (mặc định) ⇒
    ``upgrade_sample`` — công ty không có người của công ty mẫu thì dừng trước khi thêm người / kế hoạch mẫu vào nhầm bản cài khác."""
    rows = await _owner_rows("SELECT id, name FROM core.tenants ORDER BY created_at", ())
    if len(rows) != 1:
        raise SystemExit(f"Dừng: bản demo phải đúng một công ty (đang có {len(rows)})")
    tid = rows[0]["id"]
    on = {r["pack"] for r in await _owner_rows("SELECT pack FROM core.tenant_packs WHERE tenant_id = %s", (tid,))}
    if (prof := pack_profile(profile)) is not None:
        if (fn := getattr(prof, "upgrade", None)) is None:
            await upgrade_pack_demo(tid, on, prof.PACK)
            return
        if prof.PACK not in on:
            raise SystemExit(f"Dừng: công ty demo không bật gói {prof.PACK} — đây không phải bản cài của hồ sơ này (đổi --profile)")
        await fn(tid)
        return
    await upgrade_sample(tid)


async def upgrade_sample(tid: UUID) -> None:
    """Phần CÔNG TY MẪU của ``--upgrade``: tên công ty theo BARISTA_DEMO_COMPANY, mặc định mới của các gói đang bật (vai / đội / quyền — qua
    admin.install_defaults; quyền mặc định ĐỔI phạm vi thì đặt lại bằng admin.set_grant, vì install chỉ thêm), người mẫu còn thiếu
    (admin.invite_user), chức danh, ánh xạ MISA, kế hoạch + chỉ tiêu mẫu, rồi bước cuối của phần mẫu các gói đang bật (``finish`` — vd câu chuyện của
    ngành). Chỉ cho bản cài demo — công ty thật đổi quyền trong app. Công ty không có người nào của công ty mẫu ⇒ dừng (bản cài của hồ sơ khác)."""
    members = {r["email"] for r in await _owner_rows("""SELECT u.email FROM core.memberships mb JOIN core.users u ON u.id = mb.user_id
                                                       WHERE mb.tenant_id = %s""", (tid,))}
    if not any(e.endswith(f"@{DOMAIN}") for e in members):
        raise SystemExit("Dừng: công ty demo không phải công ty mẫu của lõi — chạy --upgrade với --profile của bản cài này (vd --profile bank)")
    old = (await _owner_rows("SELECT name FROM core.tenants WHERE id = %s", (tid,)))[0]["name"]
    on = {r["pack"] for r in await _owner_rows("SELECT pack FROM core.tenant_packs WHERE tenant_id = %s", (tid,))}
    if settings.demo_company.strip() and old != TENANT:
        await _owner_rows("UPDATE core.tenants SET name = %s WHERE id = %s RETURNING id", (TENANT, tid))
        await _owner_rows("UPDATE core.teams SET name = %s WHERE tenant_id = %s AND code = 'HQ' RETURNING id", (COMPANY, tid))
        await _owner_rows("UPDATE papers.settings SET company_name = upper(%s), short_name = %s WHERE tenant_id = %s RETURNING tenant_id",
                          (f"Công ty cổ phần {COMPANY}", COMPANY[:10], tid))
        await _owner_rows("UPDATE talk.rooms SET name = %s WHERE tenant_id = %s AND name LIKE %s RETURNING id",
                          (f"{COMPANY} · Toàn công ty", tid, "% · Toàn công ty"))
        print(f"Đổi tên công ty: {old} → {TENANT}")
    system = system_principal(tid)
    await run(system, "admin.install_defaults", packs=["base", *sorted(on)])
    have = {(r["role"], r["resource"]): (r["scope"], set(r["actions"]))
            for r in await _owner_rows("SELECT role, resource, scope, actions FROM core.grants WHERE tenant_id = %s", (tid,))}
    rank = {"own": 1, "unit": 2, "team": 3, "all": 4}
    for role, res, scope, actions in presets.grants():
        cur = have.get((role, res))
        if cur and cur != (scope, set(actions.split())):
            wider = rank[scope] > rank[cur[0]] or not set(actions.split()) <= cur[1]
            if wider and presets.widens_authority(res, actions.split()):   # như install_defaults: KHÔNG lặng lẽ mở rộng thẩm quyền tiền / quyền
                print(f"Quyền mặc định mới CHƯA áp (mở rộng thẩm quyền — chủ tài khoản cấp trong app): {role} · {res} → {scope} {actions}")
                continue
            await run(system, "admin.set_grant", role=role, resource=res, scope=scope, actions=actions.split())
            print(f"Quyền mặc định mới: {role} · {res} → {scope} {actions}")
    for local, name, role, team, lead in sample_people():
        if f"{local}@{DOMAIN}" not in members:
            await run(system, "admin.invite_user", email=f"{local}@{DOMAIN}", name=name, role=role, team_code=team, is_lead=lead)
            print(f"Thêm người mẫu: {name} ({role}, {team})")
    if "hr" in on and (n := await seed_titles(tid, await who(tid, "admin"), since=today().isoformat())):
        print(f"Chức danh mẫu: {n} người")
    if added := await ensure_account_map(tid):
        print(f"Ánh xạ MISA mẫu còn thiếu: {', '.join(added)}")
    await seed_plan(tid)
    await seed_kpi(tid)
    await sample_finish(tid)   # bước cuối của phần mẫu các gói đang bật (chạy lại: gói tự bỏ qua phần đã có)
    if settings.demo_company.strip() and await demo_autonomy(tid):   # bản cài DEMO (BARISTA_DEMO_COMPANY — deploy/stack.json) — cài thật không
        print("Trợ lý demo: Toàn quyền (tự làm việc nhỏ đảo được, có Hoàn tác)")
    await events.drain()
    print(f"OK: demo {TENANT} đã cập nhật")


async def sample_company(name: str = TENANT, profile: str = "sample", extra_packs: tuple[str, ...] = (),
                         people: list[tuple[str, str, str, str, bool]] | None = None, titles: bool = True,
                         then: Callable[[UUID], Awaitable[None]] | None = None) -> UUID:
    """CÔNG TY MẪU của một hồ sơ: gói của lõi (``SAMPLE_PACKS``) + gói đã cài khai mình thuộc ``profile`` + ``extra_packs``; dữ liệu nghiệp vụ, chức
    danh mẫu (``titles``), phần riêng của hồ sơ (``then``), rồi bước cuối của phần mẫu các gói. Hồ sơ của gói (``barista.seed_profiles``) gọi lại hàm
    này với tên hồ sơ của nó — lõi không biết tên hồ sơ nào ngoài ``sample``."""
    tid = await create_tenant(name, DOMAIN, people=people, packs=(*SAMPLE_PACKS, *profile_packs(profile), *extra_packs))
    await seed_business(tid)
    if titles:
        await seed_titles(tid, await who(tid, "admin"), since=add_months(today(), -9).isoformat())
    if then is not None:
        await then(tid)
    await sample_finish(tid)
    return tid


# Đào tạo (0061) — khoá mẫu từ tài liệu mẫu "Chính sách bán hàng và chiết khấu"; câu trích là NGUYÊN VĂN tài liệu (lệnh kiểm lại).
TRAINING_COURSE = "CS-BH"
TRAINING_QUESTIONS = [
    ("Khách thanh toán đủ trong 7 ngày kể từ ngày ký hợp đồng thì được chiết khấu bao nhiêu?", "1%; 2%; 3%; 5%", 2,
     "Khách thanh toán đủ trong 7 ngày kể từ ngày ký hợp đồng: chiết khấu 2% giá trị hợp đồng (trước VAT)."),
    ("Khách ký từ gói thứ hai trở lên trong cùng năm được chiết khấu thêm bao nhiêu cho gói thứ hai?", "0,5%; 1%; 2%; 3%", 2,
     "Khách ký từ gói thứ hai trở lên trong cùng năm: chiết khấu thêm 1% cho gói thứ hai."),
    ("Khi ký hợp đồng khách đặt cọc bao nhiêu?", "10%; 20%; 30%; 40%", 3,
     "Đặt cọc 30% khi ký hợp đồng; 40% khi bàn giao; 30% còn lại trong 30 ngày sau nghiệm thu."),
    ("Thời hạn bảo hành kể từ ngày nghiệm thu là bao lâu?", "6 tháng; 12 tháng; 18 tháng; 24 tháng", 2,
     "Bảo hành 12 tháng kể từ ngày nghiệm thu; phản hồi yêu cầu hỗ trợ trong 4 giờ làm việc."),
]


async def seed_training(tid: UUID) -> None:
    gd = await who(tid, "giamdoc")
    async with db.owner.connection() as conn:
        cur = await conn.execute("SELECT id FROM kb.documents WHERE tenant_id = %s AND title = %s", (tid, DOCS[0]["title"]))
        row = await cur.fetchone()
    assert row is not None
    doc = row["id"]
    await run(gd, "training.upsert_course", code=TRAINING_COURSE, title="Chính sách bán hàng và chiết khấu (mẫu)",
              description="Sale nắm chiết khấu, quà tặng, tiến độ thanh toán và bảo hành trước khi tư vấn khách.", pass_pct=75)
    await run(gd, "training.add_lesson", course_code=TRAINING_COURSE, document_id=doc, note="Đọc hết bốn mục: chiết khấu, quà tặng, thanh toán, bảo hành.")
    for prompt, choices, ans, quote in TRAINING_QUESTIONS:
        await run(gd, "training.add_question", course_code=TRAINING_COURSE, prompt=prompt, choices=choices, answer=ans, document_id=doc, quote=quote)
    await run(gd, "training.publish_course", code=TRAINING_COURSE)
    await run(gd, "training.assign", course_code=TRAINING_COURSE, team_code="KD-A", due_on=(today() + timedelta(days=7)).isoformat())


async def _seed_appointments(sales: dict[str, Principal]) -> None:
    """Vài lịch hẹn cho lịch của sale: hôm nay + mai + tuần sau (giờ hôm nay có thể đã qua ⇒ dời sang giờ gần nhất còn lại)."""
    from datetime import datetime

    from barista.kernel.clock import VN
    s1 = sales["sale1"]
    cur = None
    async with db.owner.connection() as conn:
        cur = await conn.execute("SELECT l.id FROM crm.leads l JOIN core.users u ON u.id = l.owner_id WHERE u.email = %s AND l.stage IN ('lead','prospect')"
                                 " ORDER BY l.created_at LIMIT 3", (s1.email,))
        leads = [r["id"] for r in await cur.fetchall()]
    if len(leads) < 3:
        return
    now = datetime.now(VN)
    base = today() if now.hour < 19 else today() + timedelta(days=1)
    spots = [(base, f"{max(now.hour + 2, 9) % 24:02d}:00" if base == today() else "09:00", "visit", "Phòng trưng bày của công ty"),
             (base + timedelta(days=1), "14:30", "meet", "Văn phòng công ty"), (base + timedelta(days=6), "10:00", "call", "")]
    for lead, (d, hhmm, kind, place) in zip(leads, spots, strict=True):
        await run(s1, "crm.schedule", lead_id=lead, kind=kind, day=d.isoformat(), time=hhmm, place=place)


async def _seed_tasks(tid: UUID, sales: dict[str, Principal]) -> None:
    """Việc tự tạo cho bảng việc có đủ cột: quá hạn, hôm nay, đang làm, kẹt, của đội chưa ai nhận."""
    t = today()
    s1, ketoan, truonga = sales["sale1"], await who(tid, "ketoan"), await who(tid, "truonga")
    await run(s1, "flow.create_task", title="Gọi lại chị Lan chốt lịch gặp", due_on=t.isoformat())
    late = await run(s1, "flow.create_task", title="Gửi báo giá chi tiết cho anh Bình", due_on=t.isoformat())
    await _owner_rows("UPDATE flow.tasks SET due_on = %s WHERE id = %s RETURNING id", (t - timedelta(days=2), late["task_id"]))   # lệnh không nhận hạn đã qua
    doing = await run(s1, "flow.create_task", title="Chuẩn bị hợp đồng cho khách Lê Hoàng Yến", due_on=(t + timedelta(days=3)).isoformat())
    await run(s1, "flow.set_task", task_id=doing["task_id"], status="doing")
    stuck = await run(s1, "flow.create_task", title="Xin giám đốc xác nhận chiết khấu cho khách ký hai gói", due_on=(t + timedelta(days=1)).isoformat())
    await run(s1, "flow.set_task", task_id=stuck["task_id"], status="blocked", blocked_reason="Chờ giám đốc trả lời email")
    await run(truonga, "flow.create_task", title="Họp nhóm tổng kết tuần", team_code="KD-A", due_on=(t + timedelta(days=2)).isoformat())
    await run(ketoan, "flow.create_task", title="Đối chiếu sao kê ngân hàng tuần này", due_on=t.isoformat())


# Phòng nhắn tin mẫu (0048): (tên, mục đích, người lập, thành viên, tin) — tin: (phút trước, người gửi, nội dung, nhắc ai, thẻ chia sẻ).
# Thời gian lùi về quá khứ cho giống thật; Trần Bảo Nam (trưởng nhóm A — vai demo mặc định) còn vài tin chưa đọc + một lần bị nhắc tên.
# Phòng của ngành (vd phòng của một dự án) do phần mẫu của gói ngành thêm bằng ``seed_rooms(tid, <phòng của gói>)``.
ROOMS: list[tuple[str, str, str, list[str], list[tuple[int, str, str, list[str], dict[str, Any] | None]]]] = [
    ("Kinh doanh A", "Chia việc, báo nhanh khách, chốt lịch trong đội", "truonga", ["sale1", "sale2", "digital"], [
        (1560, "truonga", "Sáng nay đội mình chốt 3 việc: An gọi lại chị Lan chốt lịch gặp, Bích chuẩn bị hợp đồng cho khách mới, "
                          "anh xem lại báo giá của An trước 11h.", [], None),
        (1552, "sale1", "Dạ em nhận. Chị Lan hẹn gặp chiều thứ Bảy, em đặt lịch trên hệ thống rồi ạ.", [], None),
        (1540, "sale2", "Hợp đồng em gửi khách chiều nay, còn thiếu thông tin xuất hoá đơn của khách.", [], None),
        (1490, "digital", "Đội A có 4 lead quảng cáo mới về sáng nay: em chia An 2, Bích 2. Nhớ gọi trong 15 phút nhé.", [], None),
        (1486, "truonga", "@An @Bích ưu tiên gọi lead mới trước 10h, vướng gì cứ nhắn trong phòng.", ["sale1", "sale2"], None),
        (190, "sale1", "Báo nhanh: hôm qua em gọi 12 khách, 3 khách muốn nhận báo giá.", [], None),
        (160, "truonga", "Cả đội xem lại khách của mình nhé — mỗi người mở ra chỉ thấy đúng khách mình phụ trách.", [],
         {"query": "crm.leads", "input": {}, "title": "Khách của tôi"}),
        (42, "sale2", "Khách Bùi Văn Hải chuyển cọc rồi ạ, em gửi kế toán xác nhận.", [], None),
        (18, "digital", "Thêm 1 lead từ form trang web, em vừa chia cho An.", [], None),
    ]),
    (f"{COMPANY} · Toàn công ty", "Thông báo chung của công ty", "admin",
     ["giamdoc", "ketoan", "mkt", "digital", "truonga", "sale1", "sale2", "truongb", "sale3"], [
        (4300, "admin", "Từ thứ Hai mọi người đăng nhập Barista Desk bằng tài khoản Google công ty. Ai chưa vào được nhắn chị Hà nhé.", [], None),
        (2700, "ketoan", "Kỳ chi phí tháng này: chốt chứng từ ngày 25. Ai có hoá đơn cần thanh toán thì gửi kế toán trước ngày đó.", [], None),
        (95, "giamdoc", "Chúc mừng đội A ký 2 hợp đồng tuần trước 🎉 Tuần này cả công ty cố lên!", [], None),
    ]),
]


async def seed_rooms(tid: UUID, rooms: list[tuple[str, str, str, list[str], list[tuple[int, str, str, list[str], dict[str, Any] | None]]]] | None = None) -> int:
    """Nạp phòng mẫu (``rooms`` — mặc định ``ROOMS`` của lõi) qua CHÍNH các lệnh (tạo phòng, gửi tin bằng vai người thật), rồi lùi giờ tin cho giống
    hội thoại thật. Chạy lại được: phòng trùng tên đã có thì bỏ qua."""
    from datetime import datetime

    from barista.kernel.clock import VN
    made = 0
    now = datetime.now(VN)
    for name, purpose, owner, members, msgs in (ROOMS if rooms is None else rooms):
        async with db.owner.connection() as conn:
            cur = await conn.execute("SELECT 1 FROM talk.rooms WHERE tenant_id = %s AND name = %s", (tid, name))
            if await cur.fetchone():
                continue
        people = {k: await who(tid, k) for k in {owner, *members, *(m[1] for m in msgs)}}
        out = await run(people[owner], "talk.create_room", name=name, purpose=purpose, members=[people[k].email for k in members])
        room = {"subject_kind": "talk.room", "subject_id": str(out["room_id"])}
        for ago, author, body, mention, card in msgs:
            r = await run(people[author], "talk.post", **room, body=body, mentions=[people[k].email for k in mention],
                          **({"card": card} if card else {}))
            async with db.owner.connection() as conn:
                at = now - timedelta(minutes=ago)
                await conn.execute("UPDATE talk.messages SET created_at = %s WHERE id = %s", (at, r["message_id"]))
                await conn.execute("UPDATE talk.threads SET last_message_at = %s WHERE id = %s", (at, r["thread_id"]))
                await conn.execute("UPDATE core.notifications SET created_at = %s WHERE tenant_id = %s AND created_at > now() - interval '1 minute'"
                                   " AND open->>'query' = 'talk.thread'", (at, tid))
        made += 1
    return made


async def _packs_on(tid: UUID) -> set[str]:
    return {r["pack"] for r in await _owner_rows("SELECT pack FROM core.tenant_packs WHERE tenant_id = %s", (tid,))}


async def _owner_rows(sql: Any, params: Any) -> list[dict[str, Any]]:
    async with db.owner.connection() as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()


async def add_company(profile: str, extra_packs: tuple[str, ...] = ()) -> None:
    """``--add``: thêm công ty mẫu của MỘT hồ sơ gói vào bản demo ĐANG CÓ dữ liệu (bản cài nhiều công ty — mỗi người đăng nhập vào đúng công ty của mình).
    Công ty cùng tên đã có ⇒ bỏ qua (chạy lại an toàn). Chỉ hồ sơ của gói (``NAME`` riêng) — công ty mẫu của lõi không thêm được lần hai."""
    prof = pack_profile(profile)
    if prof is None:
        raise SystemExit(f"Dừng: --add chỉ cho hồ sơ của gói đã cài (không phải {profile})")
    name = prof.NAME
    async with db.owner.connection() as conn:
        if await (await conn.execute("SELECT 1 FROM core.tenants WHERE name = %s", (name,))).fetchone():
            print(f"Đã có công ty \"{name}\" — bỏ qua")
            return
    kw = {"extra_packs": extra_packs} if "extra_packs" in inspect.signature(prof.seed).parameters else {}
    tid = await prof.seed(name=name, **kw)
    if getattr(prof, "AUTONOMY", True):
        await demo_autonomy(tid)
    print(prof.DONE)


async def main(reset: bool, profile: str, upgrade: bool = False, extra_packs: tuple[str, ...] = (), add: bool = False) -> None:
    """``profile``: dữ liệu mẫu của BẢN CÀI nào — cùng một bộ code, mỗi bản cài chỉ có công ty + gói của nó (0063).
    ``sample``: công ty mẫu của lõi (mặc định, DB dev ``barista_erp_dev``) — gói ngành đã cài khai mình thuộc hồ sơ này thì góp phần của nó (vd sàn
    môi giới). Hồ sơ của gói đã cài (``san-moi-gioi``, ``accounting``, ``research``, ``bank``…): DB riêng."""
    packs.check_config()   # BARISTA_PACKS nêu khoá gói lạ ⇒ dừng trước khi chạm DB (phát hành chạy seed trước khi đổi bản)
    real = settings.env == "production"   # bản cài production (ADR 0194): chỉ migration (+ mặc định khi --upgrade), không bao giờ dữ liệu mẫu
    if real and reset:
        raise SystemExit("Dừng: BARISTA_ENV=production — không --reset (xoá sạch DB) trên bản cài production")
    if real and add:
        raise SystemExit("Dừng: BARISTA_ENV=production — không thêm công ty mẫu vào bản cài production")
    if reset:
        app_db, owner_db = (u.rsplit("/", 1)[-1].split("?")[0] for u in (settings.database_url, settings.owner_database_url))
        if app_db != owner_db:   # cấu hình lệch (vd chỉ đặt một biến) ⇒ xoá nhầm DB khác — dừng trước khi xoá
            raise SystemExit(f"Dừng: BARISTA_DATABASE_URL ({app_db}) và BARISTA_OWNER_DATABASE_URL ({owner_db}) trỏ hai DB khác nhau")
        await reset_schemas(settings.owner_database_url)
    await migrate(settings.owner_database_url)
    await db.open(settings.database_url, settings.owner_database_url)
    try:
        async with db.owner.connection() as conn:
            cur = await conn.execute("SELECT 1 FROM core.tenants LIMIT 1")
            if await cur.fetchone():
                if add and not real:
                    await add_company(profile, extra_packs)
                    return
                if upgrade:
                    n = await install_defaults_everywhere()
                    if real:
                        print(f"OK: đã cài mặc định mới cho {n} công ty — production: không đụng dữ liệu mẫu")
                        return
                    if n == 1:
                        await upgrade_demo(profile)
                    else:
                        print(f"OK: đã cài mặc định mới cho {n} công ty — bỏ phần dữ liệu mẫu (chỉ cho bản demo một công ty)")
                    return
                print("Đã có dữ liệu mẫu — chạy với --reset để nạp lại, hoặc --upgrade để cập nhật bản demo đang chạy")
                return
        if real:
            print("OK: production — đã chạy migration, DB trống KHÔNG nạp mẫu (công ty thật: runbook 11 mục \"Chế độ production\")")
            return
        if (prof := pack_profile(profile)) is not None:   # hồ sơ do gói đã cài khai (vd ``bank`` — gói tài trợ thương mại, 0072)
            kw = {"extra_packs": extra_packs} if "extra_packs" in inspect.signature(prof.seed).parameters else {}
            tid = await prof.seed(name=settings.demo_company.strip() or prof.NAME, **kw)
            if getattr(prof, "AUTONOMY", True):
                await demo_autonomy(tid)
            print(prof.DONE)
            return
        if profile == "pay-demo":   # gói Lương (0150): dịch vụ (lượt, Excel), văn phòng (chấm công IP) + công ty mẫu của gói ngành đã cài
            from barista.seed_pay import OFFICE_DOMAIN, SERVICE_DOMAIN, seed_pay
            got = await seed_pay()
            print(f"OK: Lương & thù lao mẫu · {got['service']['imported']} dòng lượt dịch vụ nhập Excel · "
                  f"kỳ nháp (cộng tác viên chờ kế toán xác minh quy tắc 10%) + kỳ chạy bóng · đăng nhập dev: luong (phụ trách lương) / ketoan / "
                  f"giamdoc @{SERVICE_DOMAIN}; người được trả: ktvlan, ktvmai · VĂN PHÒNG "
                  f"@{OFFICE_DOMAIN}: chấm công theo IP, {got['office']['closed']} bảng công tháng trước đã chốt — nhansu (Nhân sự), truongkd (trưởng đội), "
                  f"nvan / nvbinh / nvchi (nhân viên), luong, ketoan")
            for extra in got["extra"].values():   # công ty mẫu do gói đã cài góp (``COMPANIES["pay-demo"]`` của phần mẫu)
                print(extra["done"])
            return
        tid = await sample_company(TENANT, "sample", extra_packs)
        print(f"OK: tenant {tid} · công ty mẫu · gói: {', '.join(sorted(await _packs_on(tid)))} · đăng nhập dev: <vai>@{DOMAIN}")
    finally:
        await db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="xoá sạch schema rồi nạp lại (CHỈ DB dev)")
    ap.add_argument("--profile", choices=profiles(), default="sample",
                    help="dữ liệu mẫu của bản cài nào (hồ sơ của gói — vd accounting: chạy với BARISTA_DATABASE_URL / BARISTA_OWNER_DATABASE_URL trỏ DB riêng)")
    ap.add_argument("--upgrade", action="store_true", help="bản demo ĐANG CHẠY: thêm mặc định / người / kế hoạch mẫu mới, đổi tên công ty — không xoá gì"
                    " (bản cài của một gói: --upgrade --profile <hồ sơ của gói>, vd bank)")
    ap.add_argument("--with-packs", default="", help="gói THÊM vào công ty mẫu, cách nhau dấu phẩy — vd findings,word (gói tuỳ chọn, mặc định tắt)")
    ap.add_argument("--add", action="store_true", help="bản demo ĐANG CÓ dữ liệu: THÊM công ty mẫu của --profile (hồ sơ gói) bên cạnh công ty có sẵn — đã có thì bỏ qua")
    args = ap.parse_args()
    asyncio.run(main(args.reset, args.profile, args.upgrade, tuple(x.strip() for x in args.with_packs.split(",") if x.strip()), args.add))
