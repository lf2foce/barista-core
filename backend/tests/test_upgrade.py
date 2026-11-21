"""Diễn tập NÂNG CẤP (review 02/11): DB sạch không chứng minh được migration đối xử đúng với dữ liệu đã có. Khung: dựng DB tới một migration
(``legacy(until=…)`` — DB đời trước 0130: một dãy số chung, sổ cũ; hoặc ``migrate(until="<gói>/<tên>")``), nạp dữ liệu kiểu cũ, chạy tiếp bằng
runner theo gói (ánh xạ sổ cũ rồi chạy phần còn thiếu — đúng đường nâng cấp của bản cài thật), kiểm. Từ bản gốc 0085 các kịch bản chuyển đổi của chuỗi 001–186 không còn đường xảy ra
(không DB nào ở đời đó) ⇒ đã bỏ; migration mới chạm dữ liệu đã có thêm kịch bản ở đây (runbook 12). DB riêng ``barista_erp_upgrade``
(scripts/dev-db.sh) — bị xoá sạch mỗi lần chạy."""

import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import AsyncConnection

from barista.kernel.migrate import BASELINE, LEDGER, LEGACY, chains, migrate, pending_plan, provenance, steps
from barista.seed import reset_schemas
from tests.conftest import MAKE_UPGRADE_DB, UPGRADE_URL, ensure_db

URL = UPGRADE_URL


# Bản phân phối có chuỗi migration thuộc LỊCH SỬ mà các kịch bản dựng lại. Gói đã rời lõi mà chuỗi của nó từng nằm trong lịch sử chung (bố cục cũ, dãy số
# chung) thêm tên bản phân phối của mình vào đây trong conftest của bộ test gói (cùng file mới / bảo trì của nó ở ``NEW_FILES`` / ``NEW_MAINTENANCE`` /
# ``RLS_INITPLAN``) rồi dùng lại các dụng cụ của file này — 0191. Bộ test lõi không thêm gì: chỉ gói của lõi.
HISTORY_DISTS = {"barista"}


def _restrict_to_core(monkeypatch: pytest.MonkeyPatch) -> None:
    """Các kịch bản nâng cấp mô tả LỊCH SỬ MIGRATION CỦA LÕI (bố cục cũ, dãy số chung, danh sách file mới): gói ngoài repo đang cài (0155) có chuỗi
    migration riêng do test của gói kiểm — ở đây bộ nạp chỉ thấy các gói của lõi ⇒ kết quả như khi không cài gói nào."""
    from barista.kernel import loader

    monkeypatch.setattr(loader, "LOADED", [e for e in loader.LOADED if e.dist in HISTORY_DISTS])


@pytest.fixture(autouse=True)
def _core_chain_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _restrict_to_core(monkeypatch)


async def _db() -> None:
    if MAKE_UPGRADE_DB:
        await ensure_db(URL)
    try:
        await (await AsyncConnection.connect(URL)).close()
    except Exception:
        pytest.skip(f"Không nối được DB nâng cấp {URL.rpartition('/')[2]} — kiểm Postgres local + BARISTA_TEST_ADMIN_URL (DB tự tạo theo lần chạy)")
    await reset_schemas(URL)


RETIRED = Path(__file__).parent / "fixtures" / "retired-migrations"   # file đã nghỉ (dời / tách — 0130 ghi chú 08/11), NGUYÊN BẢN như lúc đã chạy
OLD_LAST = "052"   # bố cục cũ (main 292e910: bản gốc một khối ở gói nền, khoá gói sales / brokerage) dừng ở số này; số lớn hơn là file mới
# File của chuỗi migration CỦA MỘT GÓI ĐÃ RỜI LÕI (0191) không liệt kê ở đây: test nâng cấp của gói thêm vào ba tập dưới (``.update`` trong conftest của
# gói, cùng ``HISTORY_DISTS``) — vd hai file đổi khoá gói (N10) của Bất động sản / Hoa hồng là file bảo trì do gói khai.
NEW_MAINTENANCE: set[str] = set()
# N11 (0145): chính sách RLS dạng InitPlan — mỗi gói một file, không bảo trì (cùng nghĩa chính sách)
RLS_INITPLAN = {f"{p}/054_rls_initplan.sql" for p in ("books", "crm", "directory", "finance",
                                                      "hr", "kpi", "marketing", "meet", "schedule", "talk",
                                                      "training")} | {"base/054_rls_initplan_core.sql"}   # base: tên mới (COMMENT_ONLY)
# File đổi tên chỉ để sửa CHÚ THÍCH (thân SQL từ dòng 2 giữ nguyên — bản cũ nhắc tên khách, không giữ được ở lõi kể cả trong fixtures; ADR 0233):
# provenance.json khai tên cũ + checksum bản cũ; ở đây ghim sha256 của phần từ dòng 2 (đo trên bản cũ lúc đổi tên) để chứng minh thân SQL không đổi.
COMMENT_ONLY = {("base", "054_rls_initplan_core.sql"): "03f34ed2863092a773026eb6733baefa7d44338ccd122efc1b44b5141e3a1ce3"}
NEW_MAINTENANCE = NEW_MAINTENANCE | {"base/057_session_claims.sql"}   # 057 (N11b, 0146): bản app cũ chỉ set_config ⇒ bị đóng sau migration ⇒ DỪNG dịch vụ
# 070 / 069 (0158): lịch chung — dựng lại ràng buộc loại trừ + chính sách RLS của lịch dưới khoá độc quyền; sao chép hẹn khách thành lịch một lần lúc
# chuyển (bản cũ còn chạy sẽ tạo hẹn không có lịch — vòng quét crm.heal_appointments vá thêm) ⇒ DỪNG dịch vụ
NEW_MAINTENANCE = NEW_MAINTENANCE | {"schedule/070_calendar.sql", "crm/069_appointments_on_calendar.sql"}
# 083 / 082 (0199): SĐT lưu E.164 — đổi khoá gộp khách / định danh ``0…`` → ``+84…``; bản cũ chạy song song ghi / tra dạng ``0…`` ⇒ khách trùng ⇒ DỪNG dịch vụ
NEW_MAINTENANCE = NEW_MAINTENANCE | {"crm/083_customer_phone_e164.sql", "directory/082_phone_e164.sql"}
# 084 (0204): chia lead theo kế hoạch chiến dịch — đổi khoá duy nhất của leadflow.rules / members ⇒ ON CONFLICT của bản cũ không khớp ⇒ DỪNG dịch vụ
NEW_MAINTENANCE = NEW_MAINTENANCE | {"crm/084_campaign_plan_routing.sql"}
NEW_FILES = NEW_MAINTENANCE | RLS_INITPLAN | {"word/001_documents.sql",   # 0227: văn bản Word sửa trong app (schema mới, chỉ thêm) ⇒ không bảo trì
                                              "mgmt/001_snapshots.sql",   # 0213: bản chốt tháng của Báo cáo quản trị (bảng mới, chỉ thêm) ⇒ không bảo trì
                                              "base/089_task_start_deps.sql",   # 0222: việc thêm start_on / phase / depends_on (cột có mặc định, ràng buộc kiểm trên dòng mới) ⇒ không bảo trì
                                              "base/090_kb_file_sha256.sql",   # 0225: cột sha256 của file gốc kho tài liệu (cột trống được + chỉ mục) — chỉ THÊM, bản cũ không đọc / ghi cột này ⇒ không bảo trì
                                              "mgmt/002_snapshot_source.sql",   # 0213: nguồn gốc bản chốt (cột có mặc định) ⇒ không bảo trì
                                              "compose/070_brochure_layout_share.sql",   # 0224: cột layout (có mặc định) + bảng link chia sẻ + nới ràng buộc định dạng tệp xuất (thêm html) — chỉ THÊM ⇒ không bảo trì
                                              "base/092_task_plan_ref.sql",   # 0222 §15/11: cột plan_ref (mã STT / WBS của dòng trong file kế hoạch, trống được) + chỉ mục — chỉ THÊM, bản cũ không đọc / ghi cột này ⇒ không bảo trì
                                              "base/091_import_mappings.sql",   # 0226: bảng mới core.import_mappings (ánh xạ cột nhập Excel nhớ theo công ty) — chỉ THÊM ⇒ không bảo trì
                                              "compose/072_marketing_kinds.sql",   # 0231: nới ràng buộc loại bản soạn (thêm tờ rơi, bài đăng) — chỉ nới ⇒ không bảo trì
                                              "base/107_company_brand.sql",   # 0249 lát 2: schema + bảng mới admin.brand (nhận diện thương hiệu của công ty) — chỉ THÊM ⇒ không bảo trì
                                              # 0249 lát 2: cột có mặc định (ấn phẩm về hồ sơ / cho khách, công thức, nguồn) + chép nhận diện sang admin.brand, giao tệp logo
                                              # cho module Quản trị, khoá ghi bảng cũ (bản cũ còn đọc được; sửa ở bản cũ bị từ chối) ⇒ không bảo trì
                                              "compose/073_piece_subject_brand_move.sql",
                                              "compose/071_derived_images.sql",   # 0224 §8: bảng bộ đệm ảnh trích từ PDF (bảng mới, quyền theo tenant) — chỉ THÊM ⇒ không bảo trì
                                              "compose/069_rls_initplan.sql",   # 069: viết lại chính sách RLS của gói Soạn với AI thành InitPlan (như 054 của các gói khác) ⇒ không bảo trì
                                              "base/058_kinds_off.sql",   # 058: gói bật tính một lần mỗi câu, không bảo trì
                                             "talk/059_kinds_off.sql", "meet/059_kinds_off.sql",   # 059: chính sách của gói sinh sau 058 cũng có vế "gói tắt"
                                             "base/060_kb_visible_shelves.sql",   # 060: kệ đọc được tính một lần mỗi câu, không bảo trì
                                             "base/061_flow_cases_kinds_off.sql",   # 061: hồ sơ — gói bật tính một lần mỗi câu, không bảo trì
                                             "finance/061_payments_line_index.sql",   # 061: chỉ mục thanh toán theo dòng sao kê, không bảo trì
                                             "base/053_approvals_separation_of_duties.sql",   # 053: 0144 mục 4, chỉ thêm trigger ⇒ không bảo trì
                                              "base/055_agent_autorun_switch.sql",            # 055: 0144 công tắc tự chủ theo lớp, chỉ thêm cột ⇒ không bảo trì
                                              "base/056_agent_preset_rule.sql",               # 056: luật duyệt cho mức tự chủ đặt sẵn, chỉ thêm dòng ⇒ không bảo trì
                                              # 063 (0150 lát 1): gói Lương mới (schema riêng), bảng điều kiện trả rỗng của hr,
                                              # cột phần đi qua Lương của quyết định mở hoa hồng (mặc định 0)
                                              "pay/063_pay.sql", "hr/063_hr_terms.sql",
                                              "finance/063_confidential_settlements.sql",     # 063: cột + chính sách hạn chế, chỉ chặn dòng có đánh dấu mật
                                              # 064–065 (0150 lát 3–4): cột có mặc định + bảng người phụ thuộc rỗng của hr, nới ràng buộc + cột có mặc
                                              # định của pay, gói Chấm công mới (schema riêng) ⇒ không bảo trì
                                              "hr/064_hr_pay_terms.sql", "pay/064_pay_statutory.sql", "timekeeping/065_timekeeping.sql",
                                              # 064 (0151): tài khoản ngân hàng / dòng tiền ra / số hoá đơn; xác nhận giao dịch — chỉ thêm bảng / cột ⇒ không bảo trì
                                              "finance/064_bank_accounts_invoices.sql",
                                              # 064 / 065 (0151 phần 2): lịch riêng giao dịch, người đứng doanh số ngoài, thuộc tính căn, vai / lịch sử chi
                                              # 066 / 065 (0151 sửa sau review): rào xác nhận + trừ phần đã chi hệ cũ, SĐT người đứng doanh số, nguồn dòng tiền ra
                                              "finance/065_out_line_source.sql",
                                              # 066 / 067 (0152): hàm công tắc cho RLS; cột luật / trạng thái chia lead có mặc định giữ hành vi cũ, bảng thành viên,
                                              # chính sách hạn chế "chỉ thành viên" chỉ chặn khi công ty bật công tắc (mặc định tắt) ⇒ không bảo trì
                                              "base/066_setting_on.sql", "crm/066_leadflow_plus.sql",
                                              "crm/067_members_only_holder_sees.sql",
                                              # 067 / 068 / 063 (0153): nới CHECK loại mặc định; cột có mặc định + bảng chặng rỗng + khoá ngoại trên cột mới
                                              # trống + trigger chỉ chạm cột mới ⇒ không bảo trì
                                              "base/067_pack_install_records.sql", "crm/068_lead_safety.sql",
                                              # 066 / 068 / 069 (0156): bản ghi sửa tiền chỉ thêm (giấy báo có, đảo phiếu thu / đợt đã chi), danh mục khoản thu / chi,
                                              # liên kết giao dịch thay thế + đợt chưa có hạn (bỏ NOT NULL), lý do khoá / nguồn hàng của căn ⇒ không bảo trì
                                              "finance/066_money_corrections.sql", "finance/067_payment_reversal_cap.sql",
                                              # 070 / 071 (runbook 15): cột loại lỗi model có thể trống + CHECK NOT VALID, rồi VALIDATE ở bước riêng ⇒ không bảo trì
                                              "base/070_ai_usage_error_class.sql", "base/071_ai_usage_error_class_validate.sql",
                                              "schedule/055_schedule_always_on.sql",         # 055 (0157): quyền mặc định của gói luôn bật cho công ty có trước, chỉ thêm dòng
                                              # 065 / 069 (0160): bảng trưởng đội theo ngày rỗng; mẫu chia / đề xuất / sổ sửa rỗng + trigger chỉ chặn khi có đề xuất còn cờ
                                              "hr/065_team_heads.sql",
                                              # 070 (0160 phần 2): cột danh mục vai có mặc định, bảng ánh xạ chữ cũ, dòng danh mục mặc định ⇒ không bảo trì
                                              # 066 / 071 (0160 review): cột có thể trống / mặc định ghi người sửa cơ cấu + trigger đóng dòng; danh sách người sửa của đề xuất,
                                              # lý do lùi ngày của mẫu, hàm trigger xác nhận thay thế (chỉ chặn khi có đề xuất) ⇒ không bảo trì
                                              "hr/066_structure_editors.sql",
                                              # 072 / 070 (0162): cột có mặc định (số đã lập khoản thu hồi của quyết định mở; chứng từ gốc của giao dịch) ⇒ không bảo trì
                                              # 073 / 074 (dọn nợ 11/11): trigger kiểm dòng MỚI chặn thu hồi luỹ kế âm; cho phép rỗng + ghi chú ngừng dùng cột bản chép giờ hẹn ⇒ không bảo trì
                                              "crm/074_appointments_at_deprecated.sql",
                                              # 072 / 073 (0163): bảng yêu cầu mở gói + bảng đếm lượt xem — bảng mới rỗng, chỉ thêm ⇒ không bảo trì
                                              "base/072_pack_requests.sql", "base/073_usage_reads.sql",
                                              # 068 (0147 lát A, bản demo): nới CHECK bản chất khoản phải thu + ràng buộc cho giá trị mới ⇒ không bảo trì
                                              "finance/068_deferred_nature.sql",
                                              # 069 (0165): bảng loại thuế + sổ hoá đơn GTGT mới, rỗng, chỉ thêm ⇒ không bảo trì
                                              "finance/069_vat_output.sql",
                                              # 070 (0170): phiếu chi phí + dòng + đối tượng tập hợp chi phí — bảng mới, rỗng, chỉ thêm ⇒ không bảo trì
                                              "finance/070_expense_bills.sql",
                                              # 072 (0183): cột cho phép trống + chỉ mục duy nhất một phần (chuyển pháp nhân của phiếu chi phí) ⇒ không bảo trì
                                              "finance/072_bill_transfer.sql",
                                              # 074 / 066 (0169): cột múi giờ công ty có mặc định + hàm mới; thay thân core.today() và hai trigger chấm công (cùng
                                              # chữ ký, cùng kết quả cho múi Việt Nam) ⇒ không bảo trì
                                              "base/074_company_timezone.sql", "timekeeping/066_company_timezone.sql",
                                              # 075 / 075 (0168): nhật ký nhập (hai bảng mới rỗng); lịch sử chia lead nạp từ hệ khác (bảng mới rỗng) + cột
                                              # lead_state cho phép trống ⇒ không bảo trì
                                              "base/075_import_log.sql", "crm/075_leadflow_history.sql",
                                              # 071 / 072 (0171): cột có mặc định + bảng ký gửi rỗng; chỉ mục "một giao dịch mỗi lượt bán" tạo trước rồi bỏ
                                              # chỉ mục cũ (với dòng cũ ràng buộc y hệt) ⇒ không bảo trì
                                              # 076 / 073 / 074 (0172): chỉ mục mốc chỉ số crm; điền ngày huỷ còn trống + chỉ mục; cột loại + mã dòng gốc của
                                              # bản ghi đảo lịch sử chi (có mặc định) ⇒ không bảo trì
                                              "crm/076_metric_ranges.sql",
                                              # 077 / 076 / 074 (0174): cột có mặc định + CHECK chỉ ràng buộc dòng nạp mới + chỉ mục một phần (chăm sóc nạp
                                              # từ hệ khác); cột hạn giữ nội dung dòng có mặc định + quyền ghi cột; nới CHECK loại sự kiện giao dịch ⇒ không bảo trì
                                              "crm/077_activity_import.sql", "base/076_import_row_retention.sql", "base/077_apps_publish.sql",
                                              # 071 (0179): cột có mặc định + CHECK chỉ ràng buộc dòng nạp (mọi dòng đang có không có tiền tố import.) ⇒ không bảo trì
                                              "schedule/071_events_import.sql",
                                              "schedule/072_kind_tone.sql",   # 0240: cột tone (màu loại lịch) có mặc định ⇒ không bảo trì
                                              "schedule/073_calendar_own_view.sql",   # 0240: chỉ THÊM dòng quyền mặc định ⇒ không bảo trì
                                              # 071 / 075 / 078 (0177): cột bên nợ + cột bên trả phí cho phép trống + chỉ mục một phần; cột cửa sổ phát lại có mặc định +
                                              # quyền xoá bảng khoá + chỉ mục BRIN ⇒ không bảo trì
                                              "finance/071_receivable_party.sql",
                                              "base/078_idempotency_retention.sql",
                                              # 076 (0180): bảng mới (ghi nhận doanh số + danh mục vai) + trigger trên bảng mới + nới CHECK loại sự kiện giao dịch
                                              # (NOT VALID rồi VALIDATE) + dòng danh mục / quyền mới ⇒ không bảo trì
                                              # 077 (0184): một cột có mặc định + CHECK ba giá trị (hình thức thanh toán của giao dịch) ⇒ không bảo trì
                                              # 078 (0182): schema mới (hàng nhận theo thoả thuận), bảng rỗng ⇒ không bảo trì
                                              "finance/073_placement_receipts.sql",
                                              # 079 / 080 / 078 / 079 (0185): cột dấu nạp lịch sử có mặc định / cho phép trống (sự kiện, hồ sơ, lead) + ghi chú cột + chỉ
                                              # mục một phần trên lead sống ⇒ không bảo trì
                                              "base/079_events_historical.sql", "base/080_flow_cases_historical.sql", "crm/078_lead_loaded_at.sql",
                                              "crm/079_parked_lead_state.sql",
                                              # 081 (0193): bảng hạng AI theo vai / chi theo người — bảng mới, không bảo trì
                                              "base/081_ai_tiers.sql",
                                              # 082 (0193 — chính sách AI của nhà vận hành): cột cho phép trống / có mặc định + trigger chỉ chặn dòng quyền
                                              # MỚI của tài nguyên chưa ai được cấp ⇒ không bảo trì
                                              "base/082_ai_policy_operator.sql",
                                              # 083 (0198): bảng kỹ năng trợ lý mới (rỗng) + cột kỹ năng đã dùng cho phép trống ở core.ai_usage ⇒ không bảo trì
                                              "base/083_ai_skills.sql",
                                              # 0195–0197: cột chi phí / chiến dịch, bảng chia chi phí + quảng cáo theo ngày, SĐT quốc tế + đổi đầu số 01x — không bảo trì
                                              "crm/081_phone_new_mobile_prefix.sql",
                                              "crm/082_customer_phone_international.sql",
                                              "directory/081_phone_new_mobile_prefix.sql",
                                              "marketing/081_spend_delivery_campaign_origin.sql",
                                              "marketing/082_cost_shares.sql",
                                              "marketing/083_ads_and_ad_days.sql",
                                              # 067 (0202): chứng chỉ nhân sự — bốn bảng mới rỗng của hr (loại, chứng chỉ, số + bản scan, sổ nhắc) ⇒ không bảo trì
                                              "hr/067_credentials.sql",
                                              # 084 (0199): quốc gia công ty — cột có mặc định VN, chỉ dùng cho hiển thị SĐT ⇒ không bảo trì
                                              "base/084_company_country.sql",
                                              # 085 (0206): cửa nhà vận hành — thay thân trigger chặn quyền của nhà vận hành, cột vai bảo lãnh cho phép trống,
                                              # bỏ NOT NULL người cài, CHECK + khoá ngoại NOT VALID ⇒ không bảo trì
                                              "base/085_operator_door.sql",
                                              # 086 (0205 §5c): bảng MỚI core.login_codes (mã đăng nhập qua email) — không đụng bảng đang sống ⇒ không bảo trì
                                              "base/086_login_codes.sql",
                                              # 087 (0207): cột core.remote_apps.shadow có mặc định false — bản cũ không đọc ⇒ không bảo trì
                                              "base/087_app_shadow.sql",
                                              # 088 (0218): bảng MỚI core.role_tools (bộ công cụ trợ lý theo vai) rỗng — bản cũ không đọc ⇒ không bảo trì
                                              "base/088_role_tools.sql",
                                              # 085 (0210): cột crm.leads.ad_id trống được — bản cũ không ghi / không đọc ⇒ không bảo trì
                                              "crm/085_lead_ad.sql",
                                              # 086 (0214): cột leadflow.rules.hide_contact mặc định false — bản cũ không đọc / ghi ⇒ không bảo trì
                                              "crm/086_leadflow_hide_contact.sql",
                                              # 087 (0060 lát 1): cột crm.leads.extra mặc định {} — bản cũ không đọc / ghi ⇒ không bảo trì
                                              "crm/087_lead_extra.sql",
                                              # base/093 (0060 lát 1): bảng MỚI core.field_defs — bản cũ không đọc ⇒ không bảo trì
                                              "base/093_field_defs.sql",
                                              "base/094_docread.sql",   # 0232: hai bảng mới (hồ sơ đọc tài liệu theo công ty, bộ nhớ kết quả đọc) + nới ràng buộc ai_usage.surface thêm "docread" — chỉ THÊM / NỚI, bản cũ không đọc / ghi ⇒ không bảo trì
                                              "base/095_embed_profiles.sql",   # 0232: bảng MỚI core.embed_profiles (hồ sơ nhúng theo công ty) — chỉ THÊM, bản cũ không đọc / ghi ⇒ không bảo trì
                                              "base/096_role_hints.sql",   # 0239 phần 4: bảng MỚI core.role_hints (câu gợi ý trợ lý theo vai công ty đổi) — chỉ THÊM ⇒ không bảo trì
                                              "base/097_search_fold.sql",   # 0087: extension unaccent + hàm core.vn_fold (tìm không dấu) — chỉ THÊM, không khoá bảng ⇒ không bảo trì
                                              "base/098_template_default_published.sql",   # rà form 18/11: nới ràng buộc papers.template_versions (bộ mẫu mặc định phát hành không tên người) — bản cũ không ghi trống ⇒ không bảo trì
                                              "base/099_audit_partial_indexes.sql",   # quét màn 18/11: hai chỉ mục từng phần trên core.audit_log — chỉ THÊM, lock_timeout 10s ⇒ không bảo trì
                                              "base/100_connectors.sql",   # khung kết nối chung (0244): schema MỚI connectors — chỉ THÊM, bản cũ không đọc ⇒ không bảo trì
                                              "base/101_connector_routes.sql",   # webhook chung (1.66): bảng MỚI connectors.routes — chỉ THÊM ⇒ không bảo trì
                                              "base/104_ledger_whole_entries.sql",   # rà 20/11: bút toán một khối giữ bằng trigger (cùng giao dịch, ≥ 2 dòng) + đảo một lần — bản cũ ghi đầu + dòng cùng giao dịch ⇒ không bảo trì
                                              "base/105_record_files.sql",   # ADR 0249: bảng MỚI core.record_files + core.record_file_removals (tệp của hồ sơ) — chỉ THÊM, bản cũ không đọc / ghi ⇒ không bảo trì
                                              "base/106_file_shared_uses.sql",   # ADR 0249: cột core.file_copy_grants.by_reference có mặc định false (dòng cũ là bản sao) — bản cũ không đọc / ghi ⇒ không bảo trì
                                              "finance/077_receivable_issued_facts.sql",   # rà 20/11: số lúc lập của khoản phải thu — cột mới + trigger điền khi chèn (bản cũ chèn được) ⇒ không bảo trì
                                              "base/102_field_changes.sql",   # ADR 0246: bảng MỚI core.field_changes (lịch sử thay đổi theo trường) — chỉ THÊM, bản cũ không đọc / ghi ⇒ không bảo trì
                                              "crm/088_lost_reasons.sql",   # ADR 0246: bảng MỚI crm.lost_reasons + cột crm.leads.lost_reason_key trống được — bản cũ không đọc / ghi ⇒ không bảo trì
                                              "crm/089_stage_idle_days.sql",   # ADR 0246: cột crm.stages.idle_days trống được — bản cũ không đọc / ghi ⇒ không bảo trì
                                              # 103 (ADR 0246): luật tự động theo ngày + hành động chạy lệnh / đề xuất — cột mới có mặc định / trống được, nới CHECK
                                              # (bản cũ chỉ ghi luật theo sự kiện, hành động báo / tạo việc — vẫn thoả ràng buộc mới) ⇒ không bảo trì
                                              "base/103_automation_dates_actions.sql",
                                              # 084 (0209): bảng MỚI của điều khiển quảng cáo (chính sách, phát hiện, sổ thay đổi, chuyển đổi) — bản cũ không đọc ⇒ không bảo trì
                                              "marketing/084_ad_control.sql",
                                              # 085 (0209 §đội): cột người phụ trách / đội trống được trên phát hiện / sổ thay đổi / chuyển đổi + đặt lại RLS cùng tài
                                              # nguyên — bản cũ ghi dòng không đội (chỉ phạm vi "all" thấy), quyền công ty đang chạy không đổi ⇒ không bảo trì
                                              "marketing/085_ad_control_team_scope.sql",
                                              # 074 (0211): cột acct.payables.due_on trống được — bản cũ không ghi / không đọc ⇒ không bảo trì
                                              "finance/074_payable_due_on.sql",
                                              # 075 (0217): cột acct.statement_lines.deducted_vnd mặc định 0 + bảng khấu trừ mới rỗng + nới hai CHECK (với deducted_vnd = 0
                                              # trùng CHECK cũ trên mọi dòng bản cũ ghi; NOT VALID rồi VALIDATE) — bản cũ không đọc / ghi khấu trừ ⇒ không bảo trì
                                              "finance/075_line_deductions.sql",
                                              # 076 (0223): hồ sơ điều tra khoản lệch — ba bảng mới rỗng, chỉ thêm ⇒ bản cũ không đọc ⇒ không bảo trì
                                              "finance/076_discrepancy_dossiers.sql"}


def old_layout() -> list[tuple[str, str, Path]]:
    """Bố cục migration TRƯỚC khi tách bản gốc theo gói + đổi khoá gói (N10, main ``292e910``): [(gói cũ, tên, file nguyên bản)] theo một thứ
    tự chạy hợp lệ của runner theo gói lúc đó (gói nền trước — bản gốc một khối chứa mọi bảng — rồi các gói). Dựng lại từ ``provenance.json``:
    file dời / tách ⇒ khoá cũ + bản ở ``fixtures/retired-migrations`` (dời nguyên vẹn: chính file hiện nay); gói đổi khoá ⇒ khoá cũ."""
    chains_ = chains()
    prov = provenance(chains_)
    old_pack = {new: old for old, new in prov.renamed.items()}
    out: dict[tuple[str, str], Path] = {}
    for pack, found in chains_:
        for name, path in found.items():
            if (pack, name) in prov.moved and (pack, name) not in COMMENT_ONLY:   # đổi tên chỉ sửa chú thích: file MỚI sau bố cục cũ (NEW_FILES)
                (op, on), _ = prov.moved[(pack, name)]
                out.setdefault((op, on), RETIRED / op / on if (RETIRED / op / on).exists() else path)
            elif f"{pack}/{name}" not in NEW_FILES and (name.startswith(f"{BASELINE}/") or name.split("_", 1)[0] <= OLD_LAST):
                out[(old_pack.get(pack, pack), name)] = path
    base = sorted((k for k in out if k[0] == "base"), key=lambda k: (not k[1].startswith(f"{BASELINE}/"), k[1]))
    return [(p, n, out[(p, n)]) for p, n in base + [k for k in out if k[0] != "base"]]


def legacy_names() -> list[str]:
    """Thứ tự của runner TRƯỚC 0130: bản gốc, rồi mọi ``NNN_*.sql`` của mọi gói theo tên (một dãy số chung) — tên trong sổ cũ = tên file."""
    every = [n for _, n, _ in old_layout()]
    return sorted(n for n in every if n.startswith(f"{BASELINE}/")) + sorted(n for n in every if not n.startswith(f"{BASELINE}/"))


def new_keys_of(ran: dict[str, str]) -> set[str]:
    """Khoá mới (``gói/tên``) được đánh dấu đã chạy khi các file cũ ``{tên cũ: gói cũ}`` đã chạy — ánh xạ của ``provenance.json`` (tên cũ là duy
    nhất trong bố cục cũ)."""
    prov = provenance(chains())
    done = {(p, n) for n, p in ran.items()}
    out: set[str] = set()
    for pack, found in chains():
        olds = [o for o, to in prov.renamed.items() if to == pack]
        for n in found:
            moved_from = prov.moved[(pack, n)][0] if (pack, n) in prov.moved else None
            if (pack, n) in done or moved_from in done or any((o, n) in done for o in olds):
                out.add(f"{pack}/{n}")
    return out


async def _run_old(url: str, ledger: str, until: str | None) -> list[str]:
    """Chạy ``old_layout`` như runner cũ; ``ledger``: sổ dãy số chung (khoá tên) hoặc sổ theo gói như ``main`` 292e910 (chưa có ``moved_from``)."""
    import hashlib

    layout = old_layout()
    where = {n: (pack, p) for pack, n, p in layout}
    if ledger == LEGACY:
        order = [(where[n][0], n, where[n][1]) for n in legacy_names()]
        create = f"CREATE TABLE IF NOT EXISTS {LEGACY} (filename TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    else:
        order = layout
        create = (f"CREATE TABLE IF NOT EXISTS {LEDGER} (pack TEXT NOT NULL, name TEXT NOT NULL, checksum TEXT NOT NULL,"
                  " applied_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (pack, name))")
    done: list[str] = []
    async with await AsyncConnection.connect(url, autocommit=True) as c:
        await c.execute(create)
        if ledger == LEGACY:
            ran = {(where[r[0]][0], r[0]) for r in await (await c.execute(f"SELECT filename FROM {LEGACY}")).fetchall()}
        else:
            ran = {(r[0], r[1]) for r in await (await c.execute(f"SELECT pack, name FROM {LEDGER}")).fetchall()}
        for pack, name, path in order:
            key = name if ledger == LEGACY else f"{pack}/{name}"
            if (pack, name) in ran:
                continue
            body = path.read_text()
            async with c.transaction():
                if (step := steps().get(f"{pack}/{name}")) is not None:
                    await step(c)
                await c.execute(body.encode())
                if ledger == LEGACY:
                    await c.execute(f"INSERT INTO {LEGACY} (filename, checksum) VALUES (%s, %s)", (name, hashlib.sha256(body.encode()).hexdigest()))
                else:
                    await c.execute(f"INSERT INTO {LEDGER} (pack, name, checksum) VALUES (%s, %s, %s)",
                                    (pack, name, hashlib.sha256(body.encode()).hexdigest()))
            done.append(key)
            if key == until:
                break
    return done


async def old_packs(url: str, *, until: str | None = None) -> list[str]:
    """Dựng DB như bản cài ``main`` 292e910 (runner theo gói, bản gốc một khối, khoá ``sales`` / ``brokerage``) — ``until``: ``"<gói cũ>/<tên>"``."""
    return await _run_old(url, LEDGER, until)


async def test_data_written_on_the_baseline_survives_every_later_migration() -> None:
    """Dữ liệu ghi trên DB vừa dựng từ bản gốc ⇒ chạy mọi migration sau bản gốc ⇒ không mất, kế hoạch phát hành rỗng."""
    await _db()
    try:
        last_base = [f"base/{n}" for n in dict(chains())["base"] if n.startswith(f"{BASELINE}/")][-1]
        await migrate(URL, until=last_base)
        tid = uuid.uuid4()
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await c.execute("INSERT INTO core.tenants (id, name) VALUES (%s, 'Công ty trên bản gốc')", (tid,))
        await migrate(URL)
        async with await AsyncConnection.connect(URL) as c:
            assert (await (await c.execute("SELECT name FROM core.tenants WHERE id = %s", (tid,))).fetchone()) == ("Công ty trên bản gốc",)
        assert await pending_plan(URL) == {"pending": [], "maintenance": []}
    finally:
        await reset_schemas(URL)


async def test_an_old_pack_layout_install_stopped_midway_runs_only_what_it_lacks() -> None:
    """Bản cài ``main`` 292e910 dừng giữa chừng (sau ``base/040`` cũ — đã tách thành nền + Trao đổi): phần đã chạy chỉ ánh xạ; phần thiếu chạy
    theo gói, kể cả nửa Trao đổi của file đã tách ở sau; xong không còn gì."""
    await _db()
    try:
        ran = await old_packs(URL, until="base/040_chat_files_team_spaces.sql")
        rest = {f"{p}/{n}" for p, found in chains() for n in found} - new_keys_of({k.split("/", 1)[1]: k.split("/", 1)[0] for k in ran})
        assert {"talk/000_baseline/09_talk.sql", "talk/031_talk_daily.sql", "talk/040_links_team_rooms.sql", "base/040_company_settings.sql"}.isdisjoint(rest)
        assert {"base/041_file_store.sql", "talk/041_attachment_file_ref.sql", "crm/024_customer_identity.sql", "directory/024_party_identity.sql"} <= rest
        plan = await pending_plan(URL)
        assert set(plan["pending"]) == rest
        assert await migrate(URL) == plan["pending"]
        assert await pending_plan(URL) == {"pending": [], "maintenance": []}
    finally:
        await reset_schemas(URL)


def test_retired_originals_are_the_files_the_provenance_vouches_for() -> None:
    """Bản nguyên của file đã nghỉ (fixtures) đúng là bản ``provenance.json`` khai checksum — phép kiểm checksum của sổ cũ có căn cứ; mỗi file đã
    nghỉ được gói cho khai ``retired`` (gói nhận gỡ sau này vẫn yên)."""
    import hashlib

    prov = provenance(chains())
    for (pack, name), ((op, on), sha) in prov.moved.items():
        src = RETIRED / op / on if (RETIRED / op / on).exists() else dict(chains())[pack][name]
        if (pack, name) in COMMENT_ONLY:   # bản cũ không giữ được: thân SQL (từ dòng 2) đúng như bản cũ
            body = b"".join(src.read_bytes().splitlines(keepends=True)[1:])
            assert hashlib.sha256(body).hexdigest() == COMMENT_ONLY[(pack, name)], (pack, name)
            continue
        assert hashlib.sha256(src.read_bytes()).hexdigest() == sha, (pack, name)
    assert {old for old, _ in prov.moved.values() if old[0] not in prov.renamed} <= set(prov.gone)
    assert {f"{p.parent.name}/{p.name}" for p in RETIRED.rglob("*.sql")} == {f"{o}/{n}" for (o, n), _ in prov.moved.values()
                                                                            if (RETIRED / o / n).exists()}


async def test_a_comment_only_rename_runs_on_a_fresh_db_and_counts_as_applied_where_the_old_file_ran() -> None:
    """ADR 0233 (16/11/2026): ``base/054_rls_initplan.sql`` đổi tên thành ``base/054_rls_initplan_core.sql`` chỉ để sửa chú thích dòng đầu, qua cơ chế
    dời file của ``provenance.json`` (tên cũ + checksum bản cũ). DB mới chạy file mới; DB đã chạy file cũ (sổ ghi tên cũ + checksum cũ) KHÔNG chạy lại
    gì — sổ thêm dòng tên mới mang ``moved_from``, dòng cũ giữ làm vết; sổ ghi checksum cũ khác bản khai ⇒ dừng."""
    import hashlib

    new, old = ("base", "054_rls_initplan_core.sql"), ("base", "054_rls_initplan.sql")
    (_, sha) = provenance(chains()).moved[new]
    path = dict(chains())["base"][new[1]]
    await _db()
    try:
        # DB mới: file mới chạy như mọi migration, sổ không có tên cũ
        assert await migrate(URL, until="base/054_rls_initplan_core.sql")
        ledger = await _ledger(URL)
        assert new in ledger and old not in ledger and ledger[new][2] is None
        # như bản cài đã chạy file CŨ tới 054: sổ ghi tên cũ + checksum cũ (thân SQL như nhau ⇒ schema như nhau)
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await c.execute(f"UPDATE {LEDGER} SET name = %s, checksum = %s WHERE pack = 'base' AND name = %s", (old[1], sha, new[1]))
        plan = await pending_plan(URL)
        assert "base/054_rls_initplan_core.sql" not in plan["pending"] and plan["pending"]
        ran = await migrate(URL)
        assert "base/054_rls_initplan_core.sql" not in ran and ran == plan["pending"]
        ledger = await _ledger(URL)
        assert ledger[old][0] == sha                                                   # dòng cũ giữ nguyên (vết lịch sử)
        assert ledger[new][0] == hashlib.sha256(path.read_bytes()).hexdigest() and ledger[new][2] == "base/054_rls_initplan.sql"
        assert ledger[new][1] == ledger[old][1]                                        # lúc chạy của file cũ
        assert await pending_plan(URL) == {"pending": [], "maintenance": []}
        assert await migrate(URL) == []
        # sổ ghi file cũ với nội dung KHÁC bản khai ⇒ không đánh dấu đã chạy, dừng
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await c.execute(f"DELETE FROM {LEDGER} WHERE pack = 'base' AND name = %s", (new[1],))
            await c.execute(f"UPDATE {LEDGER} SET checksum = %s WHERE pack = 'base' AND name = %s", ("0" * 64, old[1]))
        with pytest.raises(RuntimeError, match="nội dung khác"):
            await migrate(URL)
    finally:
        await reset_schemas(URL)


async def test_a_stray_sql_file_outside_a_pack_folder_stops_migrate_and_the_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Nhánh cũ gộp vào mang ``044_x.sql`` ở GỐC ``migrations/`` (git gộp không xung đột) ⇒ runner + kế hoạch DỪNG trước câu SQL nào, không
    lặng lẽ bỏ qua file."""
    from barista.kernel import migrate as runner

    await _db()
    try:
        root = tmp_path / "migrations"
        (root / "crm").mkdir(parents=True)
        (root / "044_commerce.sql").write_text("-- module: core\nCREATE SCHEMA should_not_exist;")
        real = runner.roots()
        monkeypatch.setattr(runner, "roots", lambda: {**real, root: {"crm"}})
        for run in (migrate, pending_plan):
            with pytest.raises(RuntimeError, match=r"044_commerce\.sql nằm ở gốc"):
                await run(URL)
        async with await AsyncConnection.connect(URL) as c:
            assert (await (await c.execute("SELECT to_regnamespace('core')")).fetchone()) == (None,)
    finally:
        await reset_schemas(URL)


async def test_a_database_built_before_the_baseline_is_refused_not_overlaid() -> None:
    """DB còn ghi tên migration của chuỗi cũ ⇒ runner + kế hoạch phát hành DỪNG (dựng lại DB), không chạy bản gốc chồng lên."""
    await _db()
    try:
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await c.execute("CREATE TABLE public.barista_migrations (filename TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ DEFAULT now())")
            await c.execute("INSERT INTO public.barista_migrations (filename, checksum) VALUES ('182_drop_retired_money_and_request_tables.sql', 'x')")
        with pytest.raises(RuntimeError, match="trước bản gốc"):
            await migrate(URL)
        with pytest.raises(RuntimeError, match="trước bản gốc"):
            await pending_plan(URL)
    finally:
        await reset_schemas(URL)


async def test_migrate_runs_the_same_ordering_check_before_any_sql(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Đường khởi động trực tiếp (migrate khi chạy server / seed) chặn trùng số như kế hoạch phát hành — trước khi chạy câu SQL nào."""
    from barista.kernel import migrate as runner

    await _db()
    try:
        real = runner.chains()
        (tmp_path / "001_a.sql").write_text("-- module: core\nCREATE SCHEMA should_not_exist;")
        (tmp_path / "001_b.sql").write_text("-- module: core\nSELECT 1;")
        dup = {"001_a.sql": tmp_path / "001_a.sql", "001_b.sql": tmp_path / "001_b.sql"}
        monkeypatch.setattr(runner, "chains", lambda: [*real, ("training", dup)])   # trùng số TRONG một gói
        with pytest.raises(RuntimeError, match="cùng số 001"):
            await migrate(URL)
        async with await AsyncConnection.connect(URL) as c:
            assert (await (await c.execute("SELECT to_regnamespace('core')")).fetchone()) == (None,)   # chưa chạy gì, kể cả bản gốc
    finally:
        await reset_schemas(URL)


async def test_0119_old_journals_get_their_vietnam_posting_date_and_finance_gets_the_reopen_chain() -> None:
    """Bút toán ghi trước 0119 nhận ngày hạch toán = ngày Việt Nam của lúc ghi (17:30 UTC = 00:30 hôm sau giờ VN); sau nâng cấp ghi mới phải
    có ngày; công ty đã bật Tài chính có chuỗi duyệt mở lại kỳ, chuỗi công ty đã có giữ nguyên. Migration cần bảo trì."""
    await _db()
    try:
        await migrate(URL, until="base/044_papers_templates.sql")
        tid, other = uuid.uuid4(), uuid.uuid4()
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await c.execute("INSERT INTO core.tenants(id,name) VALUES(%s,'Công ty cũ'),(%s,'Công ty đã có chuỗi')", (tid, other))
            await c.execute("INSERT INTO core.legal_entities(tenant_id,code,name,is_default) VALUES(%s,'CHINH','Cũ',true),(%s,'CHINH','Khác',true)",
                            (tid, other))
            await c.execute("INSERT INTO core.tenant_packs(tenant_id,pack) VALUES(%s,'finance'),(%s,'finance')", (tid, other))
            await c.execute("INSERT INTO core.approval_chains(tenant_id,key,label,steps) VALUES(%s,'period_reopen','Công ty tự đặt',%s)",
                            (other, '[{"kind":"role","role":"accountant"}]'))
            for t in (tid, other):
                await c.execute("INSERT INTO ledger.accounts(tenant_id,code,name,kind) VALUES(%s,'cash:bank','Tiền','asset'),"
                                "(%s,'revenue:services','DT','revenue')", (t, t))
            jid = (await (await c.execute(
                """INSERT INTO ledger.journals(tenant_id,at,memo,source,idempotency_key) VALUES(%s,'2026-10-31 17:30:00+00','cũ','test','old-1')
                   RETURNING id""", (tid,))).fetchone() or [None])[0]
            await c.execute("""INSERT INTO ledger.postings(tenant_id,journal_id,account_id,amount_vnd)
                               SELECT %s,%s,id,CASE WHEN code='cash:bank' THEN 5 ELSE -5 END FROM ledger.accounts WHERE tenant_id=%s""",
                            (tid, jid, tid))
        assert "base/045_ledger_posted_on_period_locks.sql" in (await pending_plan(URL))["maintenance"]
        assert "finance/046_opening_balances.sql" not in (await pending_plan(URL))["maintenance"]
        await migrate(URL)
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            async def one(sql: Any, *p: Any) -> Any:
                row = await (await c.execute(sql, p)).fetchone()
                return row[0] if row else None
            assert str(await one("SELECT posted_on FROM ledger.journals WHERE id=%s", jid)) == "2026-11-01"
            assert await one("SELECT count(*) FROM ledger.postings WHERE journal_id=%s", jid) == 2
            steps = "SELECT steps FROM core.approval_chains WHERE tenant_id=%s AND key='period_reopen'"
            assert await one(steps, tid) == [{"kind": "role", "role": "director"}]
            assert await one(steps, other) == [{"kind": "role", "role": "accountant"}]
            await c.execute("BEGIN")   # SQL thẳng không khai ngày (migration cũ chạy sau 045) ⇒ ngày VN của lúc ghi
            new = await one("INSERT INTO ledger.journals(tenant_id,at,memo,source,idempotency_key) VALUES(%s,'2026-11-02 18:00:00+00','mới','test',"
                            "'new-1') RETURNING posted_on", tid)
            await c.execute("ROLLBACK")
            assert str(new) == "2026-11-03"
        assert await pending_plan(URL) == {"pending": [], "maintenance": []}
    finally:
        await reset_schemas(URL)


async def _column(c: AsyncConnection[Any]) -> str:
    cur = await c.execute("""SELECT t.typname FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid
                             WHERE a.attrelid = 'kb.chunks'::regclass AND a.attname = 'embedding'""")
    return ((await cur.fetchone()) or ("",))[0]


async def test_0141_kb_vectors_on_a_db_without_pgvector_then_upgrade_keeps_the_vectors() -> None:
    """base/052 trên DB đã có đoạn tài liệu, DB KHÔNG có pgvector (giả lập bằng barista.kb_vector=off — ảnh postgres:18-alpine của bản demo): migration không
    hỏng, cột real[], đoạn cũ chờ nhúng. Sau đó bản cài chuyển sang ảnh có pgvector ⇒ ``upgrade`` đổi cột sang vector, giữ nguyên vector đã nhúng,
    dựng chỉ mục HNSW. Không cửa sổ bảo trì."""
    from barista.modules.kb import vectors

    await _db()
    off = URL + ("&" if "?" in URL else "?") + "options=-c%20barista.kb_vector%3Doff"
    try:
        await migrate(URL, until="base/045_ledger_posted_on_period_locks.sql")
        tid, doc = uuid.uuid4(), uuid.uuid4()
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await c.execute("INSERT INTO core.tenants (id, name) VALUES (%s, 'Công ty có tài liệu')", (tid,))
            await c.execute("INSERT INTO kb.shelves (tenant_id, key, name, everyone) VALUES (%s, 'common', 'Chung', true)", (tid,))
            await c.execute("INSERT INTO kb.documents (id, tenant_id, title, body) VALUES (%s, %s, 'Quy định cọc', 'x')", (doc, tid))
            await c.execute("INSERT INTO kb.chunks (tenant_id, document_id, seq, text, folded) VALUES (%s, %s, 1, 'Đặt cọc 5%%', 'dat coc 5%%')",
                            (tid, doc))
        assert "base/052_kb_vectors.sql" not in (await pending_plan(URL))["maintenance"]
        await migrate(off)
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            assert await _column(c) == "_float4"                                           # real[]
            cur = await c.execute("SELECT embedding IS NULL, embed_model FROM kb.chunks WHERE document_id = %s", (doc,))
            assert await cur.fetchone() == (True, None)                                     # đoạn cũ: chờ vòng quét nhúng
            v = [0.0] * 768
            v[3] = 1.0
            await c.execute("UPDATE kb.chunks SET embedding = %s::real[], embed_model = 'm', embed_dims = 768 WHERE document_id = %s", (v, doc))
            with pytest.raises(Exception, match="chunks_embedding_meta"):                   # vector không ghi model / số chiều ⇒ chặn
                await c.execute("UPDATE kb.chunks SET embed_model = NULL WHERE document_id = %s", (doc,))
            has_vector = await (await c.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).fetchone()
        if not has_vector:
            assert "chưa có extension vector" in await vectors.upgrade(URL)
            return
        assert "pgvector" in await vectors.upgrade(URL)
        assert "Đã ở pgvector" in await vectors.upgrade(URL)                                # chạy lại không sao
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            assert await _column(c) == "vector"
            cur = await c.execute("SELECT (embedding::real[])[4], embed_model FROM kb.chunks WHERE document_id = %s", (doc,))
            assert await cur.fetchone() == (1.0, "m")
            assert await (await c.execute("SELECT 1 FROM pg_indexes WHERE indexname = 'idx_kb_chunks_hnsw'")).fetchone()
    finally:
        await reset_schemas(URL)


async def test_0141_fresh_db_with_pgvector_gets_the_vector_column_and_index() -> None:
    await _db()
    try:
        await migrate(URL)
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            has_vector = await (await c.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).fetchone()
            assert await _column(c) == ("vector" if has_vector else "_float4")
            idx = await (await c.execute("SELECT 1 FROM pg_indexes WHERE indexname = 'idx_kb_chunks_hnsw'")).fetchone()
            assert bool(idx) == bool(has_vector)
    finally:
        await reset_schemas(URL)


async def test_0157_existing_companies_get_the_always_on_schedule_defaults_without_overriding_their_choices() -> None:
    """Lịch & nguồn lực thành gói luôn bật (0157): phát hành chỉ chạy migration ⇒ công ty đang chạy nhận quyền mặc định của gói (chưa từng cài), quyền
    công ty đã có / đã gỡ (có nhật ký) giữ nguyên, vai chưa có thì chưa cài. Gói không còn cần dòng ``tenant_packs``; dòng cũ giữ."""
    await _db()
    try:
        await migrate(URL, until="schedule/054_rls_initplan.sql")
        fresh, kept, removed, using = (uuid.uuid4() for _ in range(4))
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            for t in (fresh, kept, removed, using):
                await c.execute("INSERT INTO core.tenants(id,name) VALUES(%s,'Công ty')", (t,))
                await c.execute("INSERT INTO core.roles(tenant_id,key,label) VALUES(%s,'admin','Quản trị'),(%s,'director','Giám đốc')", (t, t))
            await c.execute("INSERT INTO core.grants(tenant_id,role,resource,scope,actions) VALUES(%s,'director','schedule','own','{read}')", (kept,))
            await c.execute("INSERT INTO core.pack_installs(tenant_id,kind,key,pack,version) VALUES(%s,'grant','admin|schedule','schedule','x')", (removed,))
            await c.execute("INSERT INTO core.tenant_packs(tenant_id,pack) VALUES(%s,'schedule')", (using,))
        await migrate(URL)
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            async def grants(t: Any) -> dict[str, tuple[str, list[str]]]:
                cur = await c.execute("SELECT role, scope, actions FROM core.grants WHERE tenant_id = %s AND resource = 'schedule'", (t,))
                return {r[0]: (r[1], sorted(r[2])) for r in await cur.fetchall()}
            async def logged(t: Any) -> list[str]:
                cur = await c.execute("SELECT key FROM core.pack_installs WHERE tenant_id = %s AND kind = 'grant' AND pack = 'schedule' ORDER BY 1", (t,))
                return [r[0] for r in await cur.fetchall()]
            both = {"admin": ("all", ["manage", "read", "write"]), "director": ("all", ["read"])}
            assert await grants(fresh) == both and await logged(fresh) == ["admin|schedule", "admin|schedule.details", "director|schedule"]   # 070 (0158)
            assert await grants(kept) == {"admin": both["admin"], "director": ("own", ["read"])}            # quyền công ty đã đặt: giữ
            assert await grants(removed) == {"director": both["director"]}                                  # đã cài rồi gỡ (có nhật ký): không cài lại
            assert await grants(using) == both
            assert await (await c.execute("SELECT count(*) FROM core.tenant_packs WHERE pack = 'schedule'")).fetchone() == (1,)   # dòng cũ giữ
        assert await migrate(URL) == [] and await pending_plan(URL) == {"pending": [], "maintenance": []}
    finally:
        await reset_schemas(URL)


async def test_0249_brand_moves_to_company_settings_with_its_logo_and_the_old_table_turns_read_only() -> None:
    """ADR 0249 lát 2: nhận diện thương hiệu của công ty đang chạy chép từ ``compose.brand`` sang ``admin.brand`` (cài đặt công ty), tệp logo giao cho module
    Quản trị (mở được từ đó), bảng cũ khoá ghi (bản cũ còn đọc); công ty đã có dòng mới thì không bị ghi đè."""
    await _db()
    try:
        await migrate(URL, until="compose/072_marketing_kinds.sql")
        moved, kept = uuid.uuid4(), uuid.uuid4()
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            for t in (moved, kept):
                await c.execute("INSERT INTO core.tenants(id,name) VALUES(%s,'Công ty')", (t,))
            logo = (await (await c.execute(
                """INSERT INTO core.files (tenant_id, object_key, filename, content_type, size_bytes, sha256, module)
                   VALUES (%s, %s, 'logo.png', 'image/png', 10, %s, 'compose') RETURNING id""", (moved, f"t/{moved}/{uuid.uuid4()}", "a" * 64))).fetchone() or (None,))[0]
            await c.execute("INSERT INTO compose.brand (tenant_id, primary_color, font, footer, logo_file_id, logo_filename) VALUES (%s,'AA0000','Calibri','Chân',%s,'logo.png')",
                            (moved, logo))
            await c.execute("INSERT INTO compose.brand (tenant_id, primary_color) VALUES (%s,'00AA00')", (kept,))
            await c.execute("INSERT INTO admin.brand (tenant_id, primary_color) VALUES (%s,'0000AA')", (kept,))   # công ty đã đặt ở chỗ mới
        assert "compose/073_piece_subject_brand_move.sql" in await migrate(URL)
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            got = await (await c.execute("SELECT primary_color, font, footer, logo_file_id, logo_filename FROM admin.brand WHERE tenant_id = %s", (moved,))).fetchone()
            assert got == ("AA0000", "Calibri", "Chân", logo, "logo.png")
            assert await (await c.execute("SELECT module FROM core.files WHERE id = %s", (logo,))).fetchone() == ("admin",)   # module Quản trị mở được logo
            assert await (await c.execute("SELECT primary_color FROM admin.brand WHERE tenant_id = %s", (kept,))).fetchone() == ("0000AA",)   # không ghi đè
            assert await (await c.execute("SELECT has_table_privilege('barista_app', 'compose.brand', 'INSERT'), has_table_privilege('barista_app', 'compose.brand', "
                                          "'UPDATE'), has_table_privilege('barista_app', 'compose.brand', 'SELECT')")).fetchone() == (False, False, True)
        assert await migrate(URL) == []
    finally:
        await reset_schemas(URL)


def test_0157_backfill_grants_are_exactly_what_the_schedule_pack_declares() -> None:
    """``schedule/055`` chép tay ``Pack(...).grants`` của gói (migration không gọi được Python) ⇒ hai bên không được lệch lặng lẽ."""
    import re

    from barista.kernel import registry

    sql = next(p for pack, found in chains() if pack == "schedule" for n, p in found.items() if n == "055_schedule_always_on.sql").read_text()
    rows = {(r, sc, tuple(a.replace("'", "").replace(" ", "").split(","))) for r, sc, a in
            re.findall(r"\('(\w+)',\s*'(\w+)',\s*ARRAY\[([^\]]*)\]\)", sql)}
    # quyền gói Lịch thêm SAU 055 (0158: bận / rảnh cho vai bán hàng, quyền chi tiết schedule.details) cài cho công ty có trước ở schedule/070
    # … và schedule/073 (0240: kế toán / marketing xem lịch của mình)
    later = {(r, res) for name in ("070_calendar.sql", "073_calendar_own_view.sql")
             for r, res in re.findall(r"\('(\w+)',\s*'(schedule(?:\.details)?)',\s*'\w+',\s*ARRAY", next(
                 p for pack, found in chains() if pack == "schedule" for n, p in found.items() if n == name).read_text())}
    declared = {(role, scope, tuple(actions.split())) for role, res, scope, actions in registry.PACKS["schedule"].grants
                if res == "schedule" and (role, res) not in later}
    assert rows == declared, (rows, declared)
    assert {(role, res) for role, res, *_ in registry.PACKS["schedule"].grants} == {(r, "schedule") for r, _, _ in declared} | {
        x for x in later if x[0] in {g[0] for g in registry.PACKS["schedule"].grants}}   # 055 ∪ 070 = đúng bộ quyền của gói
    in_list = set(re.findall(r"'(\w+)'", re.search(r"r\.key IN \(([^)]*)\)", sql).group(1)))   # type: ignore[union-attr]
    assert in_list == {r for r, _, _ in declared}


# ───────────────────── Bản LÕI của các kịch bản nâng cấp đã dời sang thư mục test của gói ngành (ADR 0187) ─────────────────────
# Các kịch bản dưới đây dựng DB cũ CHỈ bằng bảng / dòng của lõi (không dòng, bảng, migration nào của gói ngành) ⇒ xanh cả khi gói ngành không
# được nạp. Tập file chờ chạy tính từ ``chains()`` (gói ngành còn trong repo thì chuỗi của nó vẫn có mặt), không viết cứng tên file của gói ngành.
#
# GIỚI HẠN (đã báo, chưa xử lý): bố cục CŨ không dựng trọn được khi thiếu gói Hoa hồng — bản nguyên của ``finance/017_acct_payables.sql``
# (fixtures/retired-migrations, checksum do finance/provenance.json khai) có câu ``UPDATE acct.payouts … FROM <bảng phiếu của gói Hoa hồng>`` không điều
# kiện. Vì vậy: kịch bản về SỔ dừng bố cục cũ TRƯỚC file đó (dãy số chung: ``LEGACY_CORE_STOP``; bố cục theo gói: hết gói nền — ``_old_base_last``),
# kịch bản về DỮ LIỆU dựng bằng runner theo gói (``migrate(until=…)`` như 0119 / 0141 / 0157 ở trên). Phần chỉ chứng minh được với bố cục cũ TRỌN
# VẸN (bản cài chạy hết dãy cũ ⇒ còn đúng danh sách file mới; file dời / tách của directory, finance, crm, marketing nhận ``moved_from``) hiện
# chỉ có ở bản kịch bản nằm trong thư mục test của gói ngành.
LEGACY_CORE_STOP = "016_parties.sql"

# Khoá gói cũ mà migration của LÕI đọc trong ``core.tenant_packs`` / ``tenant_entitlements`` (crm/024, base/037: công ty đang bật gói bán hàng ngành
# nhận Khách hàng, Danh mục, KPI, từ vựng). Chỉ là chữ trong bảng của lõi — không cần gói nào được nạp.
OLD_INDUSTRY_PACK = "sales"
# gói của lõi (để lọc kế hoạch phát hành khi gói ngành còn trong repo cũng có file chờ chạy)
CORE_PACKS = frozenset({"base", "books", "compose", "directory", "finance", "hr", "kpi", "meet", "mgmt", "pay", "schedule", "crm", "marketing",
                        "talk", "timekeeping", "training"})


async def legacy(url: str, *, until: str | None = None) -> list[str]:
    """Dựng DB như bản cài đời trước 0130 (runner cũ): chạy theo ``legacy_names``, ghi ``public.barista_migrations (filename, checksum)``."""
    return await _run_old(url, LEGACY, until)


def _last_baseline(pack: str) -> str:
    """Mã file cuối của bản gốc gói ``pack`` — dừng ở đây: gói vừa có bảng như bản gốc, chưa chạy migration nào của chính nó."""
    return [f"{pack}/{n}" for n in dict(chains())[pack] if n.startswith(f"{BASELINE}/")][-1]


def _old_base_last() -> str:
    """Mã file cuối của gói nền trong bố cục theo gói cũ (``main`` 292e910): mọi file nay đã dời / tách sang Trao đổi đều nằm trước đó."""
    return [f"{p}/{n}" for p, n, _ in old_layout() if p == "base"][-1]


def _every() -> set[str]:
    return {f"{p}/{n}" for p, found in chains() for n in found}


def _lacking(ran_old: dict[str, str]) -> set[str]:
    """File hiện nay còn phải chạy sau khi các file cũ ``{tên cũ: gói cũ}`` đã chạy."""
    return _every() - new_keys_of(ran_old)


def _key_now(old: str) -> str:
    """Khoá hiện nay của một khoá gói cũ: gói nhận (``renamed_from``) đang nạp ⇒ khoá mới sau migration đổi khoá của gói đó; không ⇒ giữ nguyên."""
    return provenance(chains()).renamed.get(old, old)


async def _ledger(url: str) -> dict[tuple[str, str], tuple[Any, ...]]:
    async with await AsyncConnection.connect(url) as c:
        cur = await c.execute(f"SELECT pack, name, checksum, applied_at, to_jsonb(l)->>'moved_from' FROM {LEDGER} l")   # sổ cũ chưa có cột
        return {(r[0], r[1]): tuple(r[2:]) for r in await cur.fetchall()}


def _without(pack: str, extra: dict[str, Any] | None = None) -> Any:
    """``chains`` như khi gói ``pack`` đã gỡ (bản phân phối không còn), tuỳ chọn thêm file vào một gói khác."""
    real = chains()

    def fake() -> list[tuple[str, dict[str, Any]]]:
        return [(p, {**f, **(extra or {}).get(p, {})}) for p, f in real if p != pack]
    return fake


async def test_existing_customers_get_an_identity_and_keep_their_data_core() -> None:
    """0110 / 0111 — crm/024 trên khách đã có (gói Khách hàng ở bản gốc, Danh mục đã có định danh): mỗi khách một định danh ngoài danh mục chọn, SĐT
    giữ nguyên; trùng email ghi nghi trùng, không tự gộp; công ty đang bật gói bán hàng ngành (khoá cũ) tự có gói Khách hàng + Danh mục; nhu cầu cũ
    hiện được thành chữ. Phát hành kiểu bảo trì."""
    await _db()
    try:
        await migrate(URL, until=_last_baseline("crm"))
        assert "crm/024_customer_identity.sql" in (await pending_plan(URL))["maintenance"]
        tid = uuid.uuid4()
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await c.execute("INSERT INTO core.tenants (id, name) VALUES (%s, 'Công ty cũ')", (tid,))
            await c.execute("INSERT INTO core.tenant_packs (tenant_id, pack) VALUES (%s, %s)", (tid, OLD_INDUSTRY_PACK))
            await c.execute("INSERT INTO core.tenant_entitlements (tenant_id, pack, granted_by, note) VALUES (%s, %s, 'test', '')", (tid, OLD_INDUSTRY_PACK))
            for name, phone, email in (("Khách A", "0100000001", "chung@mau.test"), ("Khách B", "0100000002", " CHUNG@mau.test "),
                                       ("Khách C", "0100000003", "")):
                await c.execute("INSERT INTO crm.customers (tenant_id, name, phone, email) VALUES (%s, %s, %s, %s)", (tid, name, phone, email))
            cid = (await (await c.execute("SELECT id FROM crm.customers WHERE phone = '0100000001'")).fetchone())[0]   # type: ignore[index]
            await c.execute("""INSERT INTO crm.leads (tenant_id, customer_id, source, interest)
                               VALUES (%s, %s, 'self', '{"bedrooms": 2, "direction": "Nam", "purpose": "ở", "budget_vnd": 5}')""", (tid, cid))
        await migrate(URL)
        async with await AsyncConnection.connect(URL) as c:
            async def one(sql: str) -> Any:
                row = await (await c.execute(sql)).fetchone()   # type: ignore[arg-type]
                return row[0] if row else None
            assert await one("SELECT count(*) FROM crm.customers WHERE party_id IS NULL") == 0
            assert await one("SELECT count(DISTINCT party_id) FROM crm.customers") == 3
            assert await one("SELECT count(*) FROM parties.parties WHERE listed = false") == 3
            assert await one("SELECT count(*) FROM parties.identifiers WHERE kind = 'phone'") == 3
            assert await one("SELECT count(*) FROM parties.identifiers WHERE kind = 'email'") == 1      # hai khách cùng email: giữ một, không chọi nhau
            assert await one("SELECT count(*) FROM parties.merge_candidates WHERE status = 'open'") == 1   # …và ghi nghi trùng
            assert await one("SELECT count(*) FROM parties.parties WHERE merged_into IS NOT NULL") == 0     # không tự gộp
            assert await one("SELECT name || ':' || phone FROM crm.customers WHERE phone = '+84100000002'") == "Khách B:+84100000002"   # 0199: khoá E.164
            assert await one("SELECT count(*) FROM parties.identifiers WHERE kind = 'phone' AND value LIKE '+84%'") == 3
            assert await one("SELECT interest->>'detail' FROM crm.leads") == "2PN · Nam · ở"
            assert await one("SELECT pack FROM core.subject_kinds WHERE kind = 'crm.lead'") == "crm"
            # khoá cũ đổi sang khoá mới chỉ khi gói nhận đang nạp (migration đổi khoá là của gói đó)
            assert await one("SELECT array_agg(pack ORDER BY pack) FROM core.tenant_packs") == sorted(["crm", "directory", _key_now(OLD_INDUSTRY_PACK)])
            assert await one("SELECT count(*) FROM core.tenant_entitlements WHERE pack IN ('crm', 'directory')") == 2
        assert await pending_plan(URL) == {"pending": [], "maintenance": []}
    finally:
        await reset_schemas(URL)


async def test_0117_old_done_tasks_become_self_reported_and_old_code_keeps_working_core() -> None:
    """0117 — base/033 trên dữ liệu đã có: việc đã xong trước đó là "tự báo"; bản cũ (không biết done_by) vẫn đánh dấu xong / mở lại việc thường
    trong lúc chuyển bản, nhưng không đánh dấu xong được việc có bộ kiểm; công ty đang chạy có chuỗi duyệt ngoại lệ."""
    await _db()
    try:
        await migrate(URL, until="base/025_team_home.sql")
        tid, uid, done, todo = (uuid.uuid4() for _ in range(4))
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await c.execute("INSERT INTO core.tenants (id, name) VALUES (%s, 'Công ty việc cũ')", (tid,))
            await c.execute("INSERT INTO core.users (id, email, name) VALUES (%s, 'viec0117@example.test', 'Người giả')", (uid,))
            await c.execute("""INSERT INTO flow.tasks (id, tenant_id, title, assignee_id, status, done_at) VALUES
                               (%s, %s, 'Việc xong từ trước', %s, 'done', now()), (%s, %s, 'Việc đang làm', %s, 'todo', NULL)""",
                            (done, tid, uid, todo, tid, uid))
        await migrate(URL)
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            async def one(sql: Any, *p: Any) -> Any:
                row = await (await c.execute(sql, p)).fetchone()
                return row[0] if row else None
            assert await one("SELECT done_by FROM flow.tasks WHERE id = %s", done) == "self"
            assert await one("SELECT done_by FROM flow.tasks WHERE id = %s", todo) is None
            # câu ghi của bản cũ (set_task trước 0117): không đụng done_by
            await c.execute("UPDATE flow.tasks SET status = 'done', done_at = now() WHERE id = %s", (todo,))
            assert await one("SELECT done_by FROM flow.tasks WHERE id = %s", todo) == "self"
            await c.execute("UPDATE flow.tasks SET status = 'todo', done_at = NULL WHERE id = %s", (todo,))
            assert await one("SELECT done_by FROM flow.tasks WHERE id = %s", todo) is None
            # việc có bộ kiểm trỏ tới một đối tượng (loại đối tượng của lõi; DB không kiểm tên bộ kiểm — đó là việc của registry)
            await c.execute("UPDATE flow.tasks SET subject_kind = 'flow.plan', subject_id = %s, done_check = 'flow.plan_done' WHERE id = %s",
                            (uuid.uuid4(), todo))
            with pytest.raises(Exception, match="không tự đánh dấu xong"):
                await c.execute("UPDATE flow.tasks SET status = 'done', done_at = now() WHERE id = %s", (todo,))
            assert await one("SELECT steps FROM core.approval_chains WHERE tenant_id = %s AND key = 'task_override'", tid) == [{"kind": "team_lead"}]
            assert await one("SELECT count(*) FROM core.subject_kinds WHERE kind IN ('flow.plan', 'flow.task')") == 2
        assert await pending_plan(URL) == {"pending": [], "maintenance": []}
    finally:
        await reset_schemas(URL)


async def test_existing_consents_say_whose_they_are_after_0112_core() -> None:
    """0112 — directory/029 trên dòng đồng ý đã có: dòng ghi người đồng ý thay là chính người đó thành "chính người này"; dòng có người khác thành
    "người giám hộ"; quan hệ cũ có vết kết thúc rỗng; bản cũ (không biết given_as) vẫn chèn được."""
    await _db()
    try:
        await migrate(URL, until="directory/024_party_identity.sql")
        tid, child, parent = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await c.execute("INSERT INTO core.tenants (id, name) VALUES (%s, 'Công ty cũ')", (tid,))
            for pid, name in ((child, "Con"), (parent, "Mẹ")):
                await c.execute("INSERT INTO parties.parties (id, tenant_id, kind, display_name) VALUES (%s, %s, 'person', %s)", (pid, tid, name))
            await c.execute("INSERT INTO parties.relations (tenant_id, party_id, related_party_id, kind) VALUES (%s, %s, %s, 'guardian_of')",
                            (tid, parent, child))
            await c.execute("""INSERT INTO parties.consents (tenant_id, party_id, purpose, granted, given_by_party_id) VALUES
                               (%s, %s, 'care', true, %s), (%s, %s, 'care', true, %s), (%s, %s, 'marketing', false, NULL)""",
                            (tid, child, child, tid, child, parent, tid, child))
        await migrate(URL)
        async with await AsyncConnection.connect(URL) as c:
            async def one(sql: str) -> Any:
                row = await (await c.execute(sql)).fetchone()   # type: ignore[arg-type]
                return row[0] if row else None
            assert await one("SELECT array_agg(given_as || ':' || purpose ORDER BY given_as, purpose) FROM parties.consents") == \
                ["guardian:care", "subject:care", "subject:marketing"]
            assert await one("SELECT count(*) FROM parties.consents WHERE given_by_party_id = party_id") == 0
            assert await one("SELECT ended_reason || ended_rule || coalesce(ended_by::text, '') FROM parties.relations") == ""
            # bản cũ (không biết given_as) vẫn chèn được: cột sinh tự động
            await c.execute("INSERT INTO parties.consents (tenant_id, party_id, purpose, granted) VALUES (%s, %s, 'care', false)", (tid, child))
        assert await pending_plan(URL) == {"pending": [], "maintenance": []}
    finally:
        await reset_schemas(URL)


async def test_0124_vocabulary_and_kpi_pack_reach_existing_companies_without_overriding_their_choices_core() -> None:
    """0124 — base/037 trên công ty đã có: công ty đang bật gói bán hàng ngành (khoá cũ) nhận chữ "Dự án" (từ vựng) + gói KPI tách ra (đang bật, quyền
    dùng, quyền mặc định còn thiếu); quyền công ty đã xoá (có trong nhật ký cài) KHÔNG cài lại; quyền đã có giữ nguyên phạm vi; công ty khác không nhận gì."""
    await _db()
    try:
        await migrate(URL, until="base/036_flow_task_source.sql")
        plan = await pending_plan(URL)
        assert "base/037_vocabulary_and_shared_capabilities.sql" in plan["maintenance"]       # bản cũ không biết bảng / gói mới ⇒ phát hành kiểu bảo trì
        old, other = uuid.uuid4(), uuid.uuid4()
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            for tid, name in ((old, "Sàn cũ"), (other, "Trung tâm cũ")):
                await c.execute("INSERT INTO core.tenants (id, name) VALUES (%s, %s)", (tid, name))
            await c.execute("INSERT INTO core.tenant_packs (tenant_id, pack) VALUES (%s, %s), (%s, 'crm')", (old, OLD_INDUSTRY_PACK, other))
            await c.execute("INSERT INTO core.tenant_entitlements (tenant_id, pack, granted_by, note) VALUES (%s, %s, 'test', '')", (old, OLD_INDUSTRY_PACK))
            for role in ("admin", "sale", "team_lead"):
                await c.execute("INSERT INTO core.roles (tenant_id, key, label) VALUES (%s, %s, %s)", (old, role, role))
            uid = uuid.uuid4()
            await c.execute("INSERT INTO core.users (id, email, name) VALUES (%s, 'ghim037@example.test', 'Người giả')", (uid,))
            await c.execute("""INSERT INTO core.user_pins (tenant_id, user_id, workspace, key) VALUES (%s, %s, %s, 'kpi_mine'),
                               (%s, %s, %s, 'leads')""", (old, uid, OLD_INDUSTRY_PACK, old, uid, OLD_INDUSTRY_PACK))   # ghim màn KPI (đổi) + màn khác (giữ)
            # admin: quyền KPI đã cài từ gói cũ (phạm vi công ty tự đổi thành "đội" — giữ); sale: công ty cũ chưa có; team_lead: công ty đã xoá (có nhật ký)
            await c.execute("INSERT INTO core.grants (tenant_id, role, resource, scope, actions) VALUES (%s, 'admin', 'kpi', 'team', ARRAY['read'])", (old,))
            await c.execute("""INSERT INTO core.pack_installs (tenant_id, kind, key, pack, version) VALUES
                               (%s, 'grant', 'admin|kpi', %s, NULL), (%s, 'grant', 'team_lead|kpi', %s, NULL)""",
                            (old, OLD_INDUSTRY_PACK, old, OLD_INDUSTRY_PACK))
        await migrate(URL)
        async with await AsyncConnection.connect(URL) as c:
            async def one(sql: str, *p: Any) -> Any:
                row = await (await c.execute(sql, p)).fetchone()   # type: ignore[arg-type]
                return row[0] if row else None
            # KPI tách ra (037); Khách hàng + Danh mục do crm/024 — ở runner theo gói chạy SAU gói nền nên cũng thấy công ty này
            assert await one("SELECT array_agg(pack ORDER BY pack) FROM core.tenant_packs WHERE tenant_id = %s", old) == \
                sorted(["crm", "directory", "kpi", _key_now(OLD_INDUSTRY_PACK)])
            assert await one("SELECT array_agg(pack ORDER BY pack) FROM core.tenant_packs WHERE tenant_id = %s", other) == ["crm"]
            assert await one("SELECT count(*) FROM core.tenant_entitlements WHERE tenant_id = %s AND pack = 'kpi'", old) == 1
            assert await one("SELECT label FROM core.vocabulary WHERE tenant_id = %s AND term = 'offering'", old) == "Dự án"
            assert await one("SELECT count(*) FROM core.vocabulary WHERE tenant_id = %s", other) == 0
            assert await one("SELECT count(*) FROM core.pack_installs WHERE tenant_id = %s AND kind = 'vocab' AND key = 'offering'", old) == 1
            assert await one("SELECT scope FROM core.grants WHERE tenant_id = %s AND role = 'admin' AND resource = 'kpi'", old) == "team"   # giữ
            assert await one("""SELECT scope || ':' || array_to_string(actions, ',') FROM core.grants
                                WHERE tenant_id = %s AND role = 'sale' AND resource = 'kpi'""", old) == "own:read"
            assert await one("SELECT count(*) FROM core.grants WHERE tenant_id = %s AND role = 'team_lead' AND resource = 'kpi'", old) == 0   # đã xoá: thôi
            assert await one("SELECT pack FROM core.pack_installs WHERE tenant_id = %s AND key = 'admin|kpi'", old) == "kpi"
            assert await one("SELECT count(*) FROM core.grants WHERE tenant_id = %s AND resource = 'kpi'", other) == 0
            assert await one("SELECT workspace FROM core.user_pins WHERE tenant_id = %s AND key = 'kpi_mine'", old) == "kpi"   # sang phân hệ mới
            assert await one("SELECT workspace FROM core.user_pins WHERE tenant_id = %s AND key = 'leads'", old) == OLD_INDUSTRY_PACK
        assert await pending_plan(URL) == {"pending": [], "maintenance": []}
    finally:
        await reset_schemas(URL)


async def test_0110_offering_columns_keep_every_lead_rule_and_campaign_under_the_new_names_core() -> None:
    """0110 / bất biến 11 — crm/038 + marketing/038 đổi project_* thành offering_* ở crm / leadflow / mkt trên dữ liệu đã có: không mất dòng nào, chỉ mục
    duy nhất lead × sản phẩm vẫn chặn trùng, luật chia lead chọn đúng luật cụ thể nhất, số theo chiến dịch × sản phẩm vẫn tính; điều kiện tự động hoá, sự
    kiện chờ, đề xuất mở và phiếu chờ duyệt đổi khoá theo; bản đã chạy (lịch sử) và lệnh / sự kiện của module khác giữ nguyên. Phát hành kiểu bảo trì.
    Hai chặng theo thứ tự gói: crm/038 trên lead / luật đã có, rồi marketing/038 trên chiến dịch đã có."""
    from psycopg.errors import UniqueViolation

    await _db()
    try:
        await migrate(URL, until="crm/024_customer_identity.sql")
        assert {"crm/038_offering_columns.sql", "marketing/038_campaign_offering_columns.sql"} <= set((await pending_plan(URL))["maintenance"])
        tid, uid, cust, hsr, lead_hsr, lead_text, camp, auto = (uuid.uuid4() for _ in range(8))
        old_in = '{"name": "Khách", "phone": "0100000099", "project_code": "HSR"}'
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await c.execute("INSERT INTO core.tenants (id, name) VALUES (%s, 'Sàn cũ')", (tid,))
            await c.execute("INSERT INTO core.users (id, email, name) VALUES (%s, 'sale@cu.test', 'Sale')", (uid,))
            await c.execute("INSERT INTO crm.customers (id, tenant_id, name, phone) VALUES (%s, %s, 'Khách A', '0100000001')", (cust, tid))
            await c.execute("""INSERT INTO crm.leads (id, tenant_id, customer_id, project_id, project_code, source, campaign_id)
                               VALUES (%s, %s, %s, %s, 'HSR', 'ads', %s), (%s, %s, %s, NULL, 'Dự án gõ tay', 'self', NULL)""",
                            (lead_hsr, tid, cust, hsr, camp, lead_text, tid, cust))
            await c.execute("""INSERT INTO leadflow.rules (tenant_id, name, project_code, source, effective_from, first_call_min)
                               VALUES (%s, 'Chung', '', '', '2026-01-01', 15), (%s, 'Riêng HSR quảng cáo', 'HSR', 'ads', '2026-01-01', 5)""", (tid, tid))
            await c.execute("INSERT INTO leadflow.lead_state (lead_id, tenant_id, project_code, source, status) VALUES (%s, %s, 'HSR', 'ads', 'waiting')",
                            (lead_hsr, tid))
            await c.execute("""INSERT INTO flow.automations (id, tenant_id, name, trigger, conditions, action, user_id, message, owner_id)
                               VALUES (%s, %s, 'Báo HSR', 'crm.lead_created', %s, 'notify', %s, 'Lead mới', %s)""",
                            (auto, tid, '[{"field": "source", "op": "eq", "value": "ads"}, {"field": "project_code", "op": "eq", "value": "HSR"}]', uid, uid))
            # sự kiện / đề xuất / phiếu của module KHÁC cũng mang khoá project_code (ở đó là tên thật của trường) ⇒ migration không được đụng
            await c.execute("INSERT INTO core.events (id, tenant_id, type, payload) VALUES (1000001, %s, 'crm.lead_created', %s),"
                            " (1000002, %s, 'flow.plan_started', %s)",
                            (tid, '{"source": "ads", "project_code": "HSR"}', tid, '{"project_code": "HSR"}'))
            await c.execute("""INSERT INTO core.proposals (tenant_id, user_id, day, key, command, title, input, status)
                               VALUES (%s, %s, '2026-11-01', 'k-open', 'crm.create_lead', 'Mở', %s, 'open'),
                                      (%s, %s, '2026-11-01', 'k-done', 'crm.create_lead', 'Đã chạy', %s, 'done'),
                                      (%s, %s, '2026-11-01', 'k-other', 'flow.start_plan', 'Kế hoạch', '{"project_code": "HSR"}', 'open')""",
                            (tid, uid, old_in, tid, uid, old_in, tid, uid))
            await c.execute("""INSERT INTO core.approvals (tenant_id, chain, command, input, run_as, summary, requested_by, steps, status)
                               VALUES (%s, 'c', 'mkt.upsert_campaign', '{"code": "X1", "project_code": "HSR"}', 'requester', 'Chiến dịch', %s, '[]', 'pending'),
                                      (%s, 'c', 'flow.start_plan', '{"project_code": "HSR"}', 'requester', 'Kế hoạch', %s, '[]', 'pending')""",
                            (tid, uid, tid, uid))
        assert "crm/038_offering_columns.sql" in await migrate(URL, until=_last_baseline("marketing"))
        async with await AsyncConnection.connect(URL, autocommit=True) as c:   # chiến dịch kiểu cũ: gói marketing vừa ở bản gốc
            await c.execute("""INSERT INTO mkt.campaigns (id, tenant_id, code, name, channel, project_id, project_code, owner_id, start_on)
                               VALUES (%s, %s, 'FB-HSR', 'FB Hồ Sen', 'facebook', %s, 'HSR', %s, '2026-01-01')""", (camp, tid, hsr, uid))
        assert "marketing/038_campaign_offering_columns.sql" in await migrate(URL)
        assert await migrate(URL) == []
        async with await AsyncConnection.connect(URL) as c:
            async def one(sql: str, *p: Any) -> Any:
                row = await (await c.execute(sql, p)).fetchone()   # type: ignore[arg-type]
                return row[0] if row else None
            async def cols(schema: str, table: str) -> set[str]:
                rows = await (await c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
                                              (schema, table))).fetchall()
                return {r[0] for r in rows}
            for schema, table in (("crm", "leads"), ("leadflow", "rules"), ("leadflow", "lead_state"), ("mkt", "campaigns")):
                got = await cols(schema, table)
                assert "offering_code" in got and not {"project_code", "project_id"} & got, (schema, table)
            assert "offering_id" in await cols("crm", "leads") and "offering_id" in await cols("mkt", "campaigns")
            indexes = await (await c.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'crm'")).fetchall()
            assert "uq_leads_customer_offering" in {r[0] for r in indexes}
            # không mất dòng, giá trị nguyên vẹn
            assert await one("SELECT count(*) FROM crm.leads WHERE customer_id = %s", cust) == 2
            assert await one("SELECT offering_id FROM crm.leads WHERE id = %s", lead_hsr) == hsr
            assert await one("SELECT offering_code FROM crm.leads WHERE id = %s", lead_text) == "Dự án gõ tay"
            assert await one("SELECT offering_id FROM crm.leads WHERE id = %s", lead_text) is None
            assert await one("SELECT offering_id FROM mkt.campaigns WHERE id = %s", camp) == hsr
            assert await one("SELECT offering_code FROM leadflow.lead_state WHERE lead_id = %s", lead_hsr) == "HSR"
            # chỉ mục duy nhất lead × sản phẩm (có id ⇒ theo id, chưa có id ⇒ theo chữ) vẫn chặn trùng
            for offering_id, code in ((hsr, "HSR"), (None, "Dự án gõ tay")):
                with pytest.raises(UniqueViolation):
                    async with c.transaction():
                        await c.execute("INSERT INTO crm.leads (tenant_id, customer_id, offering_id, offering_code) VALUES (%s, %s, %s, %s)",
                                        (tid, cust, offering_id, code))
            async with c.transaction():
                await c.execute("INSERT INTO crm.leads (tenant_id, customer_id, offering_code) VALUES (%s, %s, 'Chữ khác')", (tid, cust))
                await c.execute("DELETE FROM crm.leads WHERE offering_code = 'Chữ khác'")
            # luật chia lead: cùng cách chọn như leadflow._rule_for (cụ thể nhất thắng: nguồn + sản phẩm trước luật chung)
            pick = """SELECT name FROM leadflow.rules WHERE tenant_id = %s AND offering_code IN (%s, '') AND source IN (%s, '')
                      AND effective_from <= '2026-06-01'
                      ORDER BY (source = %s AND source <> '') DESC, (offering_code = %s AND offering_code <> '') DESC, effective_from DESC LIMIT 1"""
            assert await one(pick, tid, "HSR", "ads", "ads", "HSR") == "Riêng HSR quảng cáo"
            assert await one(pick, tid, "DTP", "ads", "ads", "DTP") == "Chung"
            with pytest.raises(UniqueViolation):
                async with c.transaction():
                    await c.execute("""INSERT INTO leadflow.rules (tenant_id, name, offering_code, source, effective_from)
                                       VALUES (%s, 'Trùng', 'HSR', 'ads', '2026-01-01')""", (tid,))
            # số theo chiến dịch × sản phẩm (cùng dạng bộ lọc `l.offering_code = …` của chỉ số crm / mkt)
            assert await one("""SELECT count(*) FROM crm.leads l JOIN mkt.campaigns k ON k.id = l.campaign_id
                                WHERE k.tenant_id = %s AND k.code = 'FB-HSR' AND l.offering_code = 'HSR'""", tid) == 1
            assert await one("SELECT count(*) FROM crm.leads l WHERE l.tenant_id = %s AND l.offering_code = 'Dự án gõ tay'", tid) == 1
            # dữ liệu đã lưu nói theo khoá cũ đổi khoá theo
            assert await one("SELECT conditions FROM flow.automations WHERE id = %s", auto) == [
                {"field": "source", "op": "eq", "value": "ads"}, {"field": "offering_code", "op": "eq", "value": "HSR"}]   # thứ tự giữ nguyên
            assert await one("SELECT payload FROM core.events WHERE id = 1000001") == {"source": "ads", "offering_code": "HSR"}
            assert await one("SELECT payload FROM core.events WHERE id = 1000002") == {"project_code": "HSR"}                 # sự kiện loại khác: giữ
            assert await one("SELECT input->>'offering_code' FROM core.proposals WHERE key = 'k-open'") == "HSR"
            assert await one("SELECT input ? 'project_code' FROM core.proposals WHERE key = 'k-open'") is False
            assert await one("SELECT input ? 'project_code' FROM core.proposals WHERE key = 'k-done'") is True                # đã chạy = lịch sử
            assert await one("SELECT input ? 'project_code' FROM core.proposals WHERE key = 'k-other'") is True               # lệnh của module khác, không đụng
            assert await one("SELECT input FROM core.approvals WHERE command = 'mkt.upsert_campaign'") == {"code": "X1", "offering_code": "HSR"}
            assert await one("SELECT input FROM core.approvals WHERE command = 'flow.start_plan'") == {"project_code": "HSR"}
        assert await pending_plan(URL) == {"pending": [], "maintenance": []}
    finally:
        await reset_schemas(URL)


async def test_an_install_on_the_global_sequence_moves_to_pack_keys_without_rerunning_any_file_core() -> None:
    """0130 lát 3 — bản cài dựng bằng runner cũ (một dãy số chung, sổ ``public.barista_migrations``, bản gốc một khối), dừng ở ``LEGACY_CORE_STOP``:
    kế hoạch phát hành (chỉ đọc) ánh xạ trong bộ nhớ, không ghi gì; migrate chuyển từng dòng sang ``(gói, tên)`` giữ checksum + lúc chạy — kể cả
    bản gốc nay thuộc gói khác — KHÔNG chạy lại file nào đã chạy; chạy lại không đổi gì; sổ cũ để nguyên (vết lịch sử)."""
    await _db()
    try:
        ran = await legacy(URL, until=LEGACY_CORE_STOP)
        owner = {n: pack for pack, n, _ in old_layout()}
        mapped, rest = new_keys_of({n: owner[n] for n in ran}), _lacking({n: owner[n] for n in ran})
        tid = uuid.uuid4()
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await c.execute("INSERT INTO core.tenants (id, name) VALUES (%s, 'Công ty đời dãy số chung')", (tid,))
            old = {r[0]: (r[1], r[2]) for r in await (await c.execute(f"SELECT filename, checksum, applied_at FROM {LEGACY}")).fetchall()}
        assert set(old) == set(ran) and len(mapped) == len(ran)                                          # mỗi file cũ đã chạy ⇒ đúng một khoá mới
        plan = await pending_plan(URL)
        assert set(plan["pending"]) == rest and len(plan["pending"]) == len(rest)
        async with await AsyncConnection.connect(URL) as c:
            assert (await (await c.execute("SELECT to_regclass(%s)", (LEDGER,))).fetchone()) == (None,)   # kế hoạch không ghi
        assert sorted(await migrate(URL)) == sorted(rest)                                                # không file cũ nào chạy lại
        new = await _ledger(URL)
        for n, (checksum, at) in old.items():                     # mỗi dòng cũ: có trong sổ mới, cùng checksum + lúc chạy
            assert (checksum, at) in [v[:2] for (_, name), v in new.items() if name == n], n
        assert {tuple(k.split("/", 1)) for k in _every()} <= set(new)                                    # mọi file hiện nay: đã chạy hoặc đã ánh xạ
        for pack, name in (("talk", "000_baseline/09_talk.sql"), ("crm", "000_baseline/11_crm.sql"), ("finance", "000_baseline/07_acct.sql"),
                           ("kpi", "010_kpi.sql"), ("finance", "013_acct_entities.sql"), ("directory", "016_parties.sql")):
            assert new[(pack, name)][:2] == old[name], (pack, name)                                      # lịch sử chung đã chia theo gói, theo tên
        async with await AsyncConnection.connect(URL) as c:
            assert {r[0]: (r[1], r[2]) for r in await (await c.execute(f"SELECT filename, checksum, applied_at FROM {LEGACY}")).fetchall()} == old
            assert (await (await c.execute("SELECT name FROM core.tenants WHERE id = %s", (tid,))).fetchone()) == ("Công ty đời dãy số chung",)
        assert await migrate(URL) == [] and await pending_plan(URL) == {"pending": [], "maintenance": []}
    finally:
        await reset_schemas(URL)


async def test_an_install_part_way_through_the_global_sequence_runs_only_what_it_lacks_in_pack_order_core() -> None:
    """Bản cài dừng giữa dãy số chung (``LEGACY_CORE_STOP``): phần đã chạy chỉ ánh xạ (kể cả bản gốc nay đã tách sang gói khác); phần còn thiếu chạy
    theo thứ tự gói (nền trước) — kế hoạch nói đúng mã sẽ chạy, đúng file cần bảo trì."""
    await _db()
    try:
        ran = await legacy(URL, until=LEGACY_CORE_STOP)
        owner = {n: pack for pack, n, _ in old_layout()}
        rest = _lacking({n: owner[n] for n in ran})
        plan = await pending_plan(URL)
        assert set(plan["pending"]) == rest and {"finance/017_payables.sql", "base/041_file_store.sql"} <= set(plan["maintenance"])
        assert {"finance/017_payables.sql", "base/020_finance_directory_defaults.sql", "crm/024_customer_identity.sql", "talk/031_talk_daily.sql",
                "base/040_company_settings.sql", "talk/040_links_team_rooms.sql"} <= rest
        assert not {"talk/000_baseline/09_talk.sql", "crm/000_baseline/11_crm.sql", "kpi/010_kpi.sql", "directory/016_parties.sql",
                    "base/015_chat_mode.sql"} & rest                                             # đã chạy (bản gốc tách theo gói) ⇒ chỉ ánh xạ
        order = [k.split("/")[0] for k in plan["pending"]]
        assert order.index("base") < order.index("finance") < order.index("crm") < order.index("talk")   # theo gói, nền trước
        assert await migrate(URL) == plan["pending"]
        assert await pending_plan(URL) == {"pending": [], "maintenance": []}
    finally:
        await reset_schemas(URL)


async def test_an_install_on_the_old_pack_layout_maps_moved_and_split_files_without_rerunning_any_core() -> None:
    """0130 ghi chú 08/11 — bản cài dựng bằng runner theo gói của ``main`` 292e910 (bản gốc một khối ở gói nền, sổ chưa có ``moved_from``) đã chạy
    hết gói nền, có dữ liệu ⇒ nâng cấp: file nay dời / tách sang gói khác (bản gốc từng gói, Trao đổi 031 + nửa 040–042) được đánh dấu đã chạy với
    ``moved_from`` + lúc chạy của file gốc, KHÔNG chạy lại; dòng sổ cũ giữ nguyên; chỉ phần chưa chạy chạy; gói công ty đang bật giữ nguyên."""
    await _db()
    try:
        ran = await old_packs(URL, until=_old_base_last())
        assert {k.split("/")[0] for k in ran} == {"base"}
        mapped = new_keys_of({k.split("/", 1)[1]: "base" for k in ran})
        before = await _ledger(URL)
        tid, other = uuid.uuid4(), uuid.uuid4()
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await c.execute("INSERT INTO core.tenants (id, name) VALUES (%s, 'Công ty cũ'), (%s, 'Công ty chỉ tài chính')", (tid, other))
            await c.execute("INSERT INTO core.tenant_packs (tenant_id, pack) VALUES (%s, 'crm'), (%s, 'directory'), (%s, 'finance'), (%s, 'finance')",
                            (tid, tid, tid, other))
            await c.execute("INSERT INTO core.tenant_entitlements (tenant_id, pack, granted_by) VALUES (%s, 'crm', 'x'), (%s, 'finance', 'x')", (tid, tid))
            await c.execute("INSERT INTO core.pack_installs (tenant_id, kind, key, pack) VALUES (%s, 'grant', 'director|parties', 'directory')", (tid,))
        plan = await pending_plan(URL)
        assert set(plan["pending"]) == _every() - mapped
        applied = await migrate(URL)
        assert applied == plan["pending"] and not set(applied) & mapped                              # không file đã chạy nào chạy lại
        new = await _ledger(URL)
        for key, (checksum, at, _) in before.items():
            assert new[key][:2] == (checksum, at), key                                               # dòng cũ giữ nguyên (vết lịch sử)
        for pack, name, origin in (("talk", "000_baseline/09_talk.sql", "000_baseline/09_talk.sql"),   # bản gốc một khối ⇒ bản gốc của gói
                                   ("crm", "000_baseline/11_crm.sql", "000_baseline/11_crm.sql"),
                                   ("finance", "000_baseline/07_acct.sql", "000_baseline/07_acct.sql"),
                                   ("talk", "031_talk_daily.sql", "031_talk_daily.sql"),                # dời nguyên vẹn
                                   ("talk", "040_links_team_rooms.sql", "040_chat_files_team_spaces.sql"),   # tách đôi: hai nửa cùng gốc
                                   ("base", "040_company_settings.sql", "040_chat_files_team_spaces.sql"),
                                   ("talk", "041_attachment_file_ref.sql", "041_files.sql"), ("base", "041_file_store.sql", "041_files.sql"),
                                   ("talk", "042_attachments_file_required.sql", "042_files_required.sql"),
                                   ("base", "042_kb_files_required.sql", "042_files_required.sql")):
            assert new[(pack, name)][1:] == (before[("base", origin)][1], f"base/{origin}"), (pack, name)
        assert {tuple(k.split("/", 1)) for k in _every()} <= set(new)                                 # mọi file hiện nay: đã chạy hoặc đã ánh xạ
        async with await AsyncConnection.connect(URL) as c:
            async def one(sql: str, *p: Any) -> Any:
                row = await (await c.execute(sql, p)).fetchone()   # type: ignore[arg-type]
                return row[0] if row else None
            assert await one("SELECT array_agg(pack ORDER BY pack) FROM core.tenant_packs WHERE tenant_id = %s", tid) == ["crm", "directory", "finance"]
            # directory/016 (bản cài này chưa chạy) bật Danh mục cho công ty đang có Tài chính; ngoài ra không gói nào tự bật / tự mất
            assert await one("SELECT array_agg(pack ORDER BY pack) FROM core.tenant_packs WHERE tenant_id = %s", other) == ["directory", "finance"]
            assert set(await one("SELECT array_agg(pack) FROM core.tenant_entitlements WHERE tenant_id = %s", tid)) >= {"crm", "finance"}
            assert await one("SELECT pack FROM core.pack_installs WHERE tenant_id = %s AND key = 'director|parties'", tid) == "directory"
        assert await migrate(URL) == [] and await pending_plan(URL) == {"pending": [], "maintenance": []}
    finally:
        await reset_schemas(URL)


async def test_an_applied_file_that_differs_from_what_its_parts_were_split_from_stops_core() -> None:
    """Sổ ghi file cũ với checksum KHÁC bản mà phần tách chép lại (DB chạy một bản sửa tay) ⇒ không đánh dấu đã chạy, DỪNG trước câu SQL nào."""
    await _db()
    try:
        await old_packs(URL, until=_old_base_last())
        tables = "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await c.execute(f"UPDATE {LEDGER} SET checksum = 'sửa tay' WHERE pack = 'base' AND name = '041_files.sql'")
            before = await (await c.execute(tables)).fetchone()
        for run in (pending_plan, migrate):
            with pytest.raises(RuntimeError, match=r"041_files\.sql đã chạy với nội dung khác"):
                await run(URL)
        async with await AsyncConnection.connect(URL) as c:
            assert await (await c.execute(tables)).fetchone() == before
            assert (await (await c.execute(f"SELECT count(*) FROM {LEDGER} WHERE name = '041_file_store.sql'")).fetchone()) == (0,)   # chưa chạy gì
    finally:
        await reset_schemas(URL)


async def test_removing_a_pack_that_received_old_files_keeps_the_givers_rows_quiet_core(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gỡ gói đã NHẬN file cũ (Trao đổi nhận bản gốc + 031 + nửa 040–042 từ gói nền) trên bản cài cũ (đã chạy hết gói nền của bố cục theo gói cũ)
    ⇒ dòng sổ của gói nền về các file đó được nhận là đã nghỉ (``retired`` của gói cho), không thành "file lạ"; không gì chạy."""
    from barista.kernel import migrate as runner

    await _db()
    try:
        await old_packs(URL, until=_old_base_last())
        applied = await migrate(URL)
        assert applied and not [k for k in applied if k.startswith("talk/") and k.split("/", 1)[1] < f"{OLD_LAST}_"]   # file cũ của Trao đổi: chỉ ánh xạ
        monkeypatch.setattr(runner, "chains", _without("talk"))
        assert await pending_plan(URL) == {"pending": [], "maintenance": []}
        assert await migrate(URL) == []
    finally:
        await reset_schemas(URL)


async def test_rows_of_an_uninstalled_pack_are_kept_and_ignored_core(monkeypatch: pytest.MonkeyPatch) -> None:
    """0130: gỡ một gói (``kpi``) ⇒ dòng sổ của nó — đã ánh xạ hay còn ở sổ cũ — giữ nguyên, không làm vỡ kế hoạch / migrate; cài lại ⇒ ánh xạ
    nốt dòng cũ, chỉ chạy file chưa chạy của gói đó."""
    from barista.kernel import migrate as runner

    await _db()
    try:
        ran = await legacy(URL, until=LEGACY_CORE_STOP)
        assert "010_kpi.sql" in ran
        owner = {n: pack for pack, n, _ in old_layout()}
        rest = _lacking({n: owner[n] for n in ran})
        kpi_new = {k for k in rest if k.startswith("kpi/")}   # file chưa chạy của chính gói đã gỡ: không chạy khi gỡ, chạy khi cài lại
        assert kpi_new and "kpi/010_kpi.sql" not in kpi_new
        monkeypatch.setattr(runner, "chains", _without("kpi"))
        assert set((await pending_plan(URL))["pending"]) == rest - kpi_new                 # sổ cũ chưa ánh xạ, gói đã gỡ: bỏ qua dòng của nó
        assert sorted(await migrate(URL)) == sorted(rest - kpi_new)
        async with await AsyncConnection.connect(URL) as c:
            rows = await (await c.execute(f"SELECT pack, name FROM {LEDGER} WHERE name = '010_kpi.sql'")).fetchall()
            assert rows == []                                                       # chưa ánh xạ được (không biết gói) — sổ cũ vẫn giữ
            assert (await (await c.execute(f"SELECT count(*) FROM {LEGACY} WHERE filename = '010_kpi.sql'")).fetchone()) == (1,)
        monkeypatch.undo()
        _restrict_to_core(monkeypatch)   # undo gỡ cả phần giới hạn về gói của lõi
        assert sorted(await migrate(URL)) == sorted(kpi_new)                        # cài lại ⇒ ánh xạ nốt, không chạy lại file cũ nào
        monkeypatch.setattr(runner, "chains", _without("kpi"))
        assert await pending_plan(URL) == {"pending": [], "maintenance": []}     # đã ánh xạ rồi gỡ: dòng (kpi, …) giữ, không kiểm
        assert await migrate(URL) == []
        async with await AsyncConnection.connect(URL) as c:
            assert (await (await c.execute(f"SELECT count(*) FROM {LEDGER} WHERE pack = 'kpi'")).fetchone()) == (1 + len(kpi_new),)
    finally:
        await reset_schemas(URL)


async def test_a_new_file_reusing_an_old_global_name_does_not_break_the_mapped_ledger_core(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Sổ cũ đã ánh xạ; sau đó gói ``kpi`` gỡ và gói khác có file trùng TÊN một file cũ (``010_kpi.sql``) ⇒ không ánh xạ lại dòng cũ vào gói
    mới (checksum khác), file mới chạy như migration chưa chạy của gói đó."""
    from barista.kernel import migrate as runner

    await _db()
    try:
        assert "010_kpi.sql" in await legacy(URL, until=LEGACY_CORE_STOP)
        assert "kpi/010_kpi.sql" not in await migrate(URL)                                 # dòng cũ đã ánh xạ vào gói kpi
        reuse = tmp_path / "010_kpi.sql"
        reuse.write_text("-- module: talk\nSELECT 1;")
        monkeypatch.setattr(runner, "chains", _without("kpi", {"talk": {"010_kpi.sql": reuse}}))
        assert await pending_plan(URL) == {"pending": ["talk/010_kpi.sql"], "maintenance": []}
        assert await migrate(URL) == ["talk/010_kpi.sql"]
        assert await pending_plan(URL) == {"pending": [], "maintenance": []}
    finally:
        await reset_schemas(URL)


async def test_0104_reclassifies_wallets_per_entity_without_replaying_cash_or_expense_core() -> None:
    """0104 — finance/017 trên sổ cái đã có ví: đã ghi nhận 18, đã rút 14 (đã chuyển 10, còn treo 4) ở pháp nhân thứ nhất ⇒ một nghĩa vụ 4; pháp nhân
    thứ hai có khoản ứng 6 ⇒ một khoản ứng 6; ví về 0, không dòng tiền / chi phí nào bị ghi lại; một đăng nhập thuộc hai công ty ⇒ hai định danh
    riêng; lệnh chi cũ nhận người nhận. Danh sách migration cần bảo trì của lõi từ điểm đó đúng như đã khai."""
    from psycopg.rows import dict_row

    from barista.kernel import ledger
    from barista.kernel.executor import core_ctx
    from barista.kernel.principal import system_principal

    await _db()
    try:
        await legacy(URL, until="015_chat_mode.sql")
        tid, other, uid, e1, e2, req = [uuid.uuid4() for _ in range(6)]
        async with await AsyncConnection[dict[str, Any]].connect(URL, row_factory=dict_row) as c:
            await c.execute("INSERT INTO core.tenants(id,name) VALUES(%s,'Upgrade A'),(%s,'Upgrade B')", (tid, other))
            await c.execute("INSERT INTO core.roles(tenant_id,key,label) VALUES(%s,'sale','Sale'),(%s,'sale','Sale')", (tid, other))
            await c.execute("INSERT INTO core.users(id,email,name) VALUES(%s,'upgrade104@example.test','Người giả')", (uid,))
            await c.execute("INSERT INTO core.memberships(tenant_id,user_id,role) VALUES(%s,%s,'sale'),(%s,%s,'sale')", (tid, uid, other, uid))
            await c.execute("INSERT INTO core.legal_entities(id,tenant_id,code,name,is_default) VALUES(%s,%s,'E1','E1',true),(%s,%s,'E2','E2',false)",
                            (e1, tid, e2, tid))
            await c.execute("INSERT INTO core.tenant_packs(tenant_id,pack) VALUES(%s,'finance')", (tid,))
            # mã ĐỜI ĐÓ đặt biến phiên bằng set_config (DB ở 015 chưa có core.open_session của N11b); kết nối owner: RLS không áp, chỉ cần DEFAULT core.tenant()
            await c.execute("SELECT set_config('app.tenant_id', %s, true), set_config('app.system', 'on', true)", (str(tid),))
            ctx = core_ctx(c, system_principal(tid))
            wallet = f"wallet:{uid}"
            await ledger.ensure_account(ctx, wallet, "Ví cũ", "liability", uid)

            # bút toán kiểu CŨ (trước 0119: chưa có cột ngày hạch toán) — ghi thẳng như mã đời đó, không qua ledger.post hiện tại
            async def old_post(key: str, source: str, entity: uuid.UUID, lines: list[tuple[str, int]]) -> None:
                for code, kind in ((x, "liability" if x.startswith(("wallet", "payout")) else "asset" if x == "cash:bank" else "expense")
                                   for x, _ in lines):
                    await ledger.ensure_account(ctx, code, code, kind)
                jid = (await (await c.execute("INSERT INTO ledger.journals(memo,source,idempotency_key,entity_id) VALUES('cũ',%s,%s,%s) RETURNING id",
                                              (source, key, entity))).fetchone() or {})["id"]
                for code, amount in lines:
                    await c.execute("INSERT INTO ledger.postings(journal_id,account_id,amount_vnd) SELECT %s,id,%s FROM ledger.accounts WHERE code=%s",
                                    (jid, amount, code))
            await old_post("old-earn", "wallet.earn", e1, [("expense:bonus", 18_000_000), (wallet, -18_000_000)])
            await old_post("old-clearing", "wallet.payout", e1, [(wallet, 14_000_000), ("payout:clearing", -14_000_000)])
            await old_post("old-bank", "acct.payout", e1, [("payout:clearing", 10_000_000), ("cash:bank", -10_000_000)])
            await old_post("old-advance", "wallet.advance", e2, [(wallet, 6_000_000), ("cash:bank", -6_000_000)])
            await c.execute("""INSERT INTO acct.payouts(tenant_id,user_id,amount_vnd,source,source_ref,label,entity_id)
                               VALUES(%s,%s,4000000,'wallet.request',%s,'Cũ',%s)""", (tid, uid, str(req), e1))
            before = (await (await c.execute("SELECT id,amount_vnd FROM ledger.postings ORDER BY id")).fetchall())
        maintenance = {k for k in (await pending_plan(URL))["maintenance"] if k.split("/")[0] in CORE_PACKS}
        assert maintenance == {"finance/017_payables.sql", "directory/024_party_identity.sql", "crm/024_customer_identity.sql", "base/025_team_home.sql",
                               "directory/029_parties_minors.sql", "schedule/030_schedule.sql",
                               "base/037_vocabulary_and_shared_capabilities.sql", "crm/038_offering_columns.sql",
                               "marketing/038_campaign_offering_columns.sql",
                               "base/041_file_store.sql", "base/042_kb_files_required.sql",
                               "talk/041_attachment_file_ref.sql", "talk/042_attachments_file_required.sql",
                               "base/045_ledger_posted_on_period_locks.sql", "base/057_session_claims.sql",
                               "crm/083_customer_phone_e164.sql", "directory/082_phone_e164.sql", "crm/084_campaign_plan_routing.sql",
                               "schedule/070_calendar.sql", "crm/069_appointments_on_calendar.sql"}   # 0158
        await migrate(URL)
        assert await migrate(URL) == []
        async with await AsyncConnection[dict[str, Any]].connect(URL, row_factory=dict_row) as c:
            assert (await (await c.execute("SELECT count(DISTINCT id) n FROM parties.parties WHERE user_id=%s", (uid,))).fetchone() or {})["n"] == 2
            anchors = await (await c.execute("SELECT kind,amount_vnd,entity_id FROM acct.payables WHERE tenant_id=%s ORDER BY amount_vnd", (tid,))).fetchall()
            assert anchors == [{"kind": "payable", "amount_vnd": 4_000_000, "entity_id": e1}, {"kind": "advance", "amount_vnd": 6_000_000, "entity_id": e2}]
            old = await (await c.execute("SELECT id,amount_vnd FROM ledger.postings WHERE id=ANY(%s) ORDER BY id", ([r["id"] for r in before],))).fetchall()
            assert old == before
            sums = await (await c.execute("""SELECT a.code,sum(p.amount_vnd)::bigint total FROM ledger.postings p
                                             JOIN ledger.accounts a ON a.id=p.account_id WHERE a.tenant_id=%s GROUP BY a.code""", (tid,))).fetchall()
            amounts = {r["code"]: r["total"] for r in sums}
            assert amounts[wallet] == 0 and amounts["cash:bank"] == -16_000_000
            assert amounts["expense:bonus"] == 18_000_000 and amounts["payout:clearing"] == -4_000_000
            party = (await (await c.execute("SELECT id FROM parties.parties WHERE tenant_id=%s AND user_id=%s", (tid, uid))).fetchone() or {})["id"]
            assert amounts[f"payable:{party}"] == -4_000_000 and amounts[f"advance:{party}"] == 6_000_000   # số dư ví sang nghĩa vụ / khoản ứng
            assert (await (await c.execute("SELECT party_id FROM acct.payouts WHERE source_ref=%s", (str(req),))).fetchone() or {})["party_id"] == party
    finally:
        await reset_schemas(URL)


async def test_running_install_gets_settlement_chain_and_directory_defaults_without_overwriting_core() -> None:
    """base/020 — phát hành chỉ chạy migration (không ``seed --upgrade``): công ty đã bật Tài chính phải có chuỗi duyệt chi nghĩa vụ, giám đốc đọc được
    danh mục, công ty đã được cấp Tài chính được cấp cả danh mục. Chuỗi công ty đã sửa, hoặc đã từng cài rồi xoá, giữ nguyên."""
    await _db()
    try:
        await migrate(URL, until="base/015_chat_mode.sql")
        fresh, edited, removed, entitled_only = (uuid.uuid4() for _ in range(4))
        custom = '[{"kind":"role","role":"accountant"}]'
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            for t in (fresh, edited, removed, entitled_only):
                await c.execute("INSERT INTO core.tenants(id,name) VALUES(%s,'Công ty')", (t,))
            for t in (fresh, edited, removed):
                await c.execute("INSERT INTO core.tenant_packs(tenant_id,pack) VALUES(%s,'finance'),(%s,'directory')", (t, t))
                await c.execute("INSERT INTO core.roles(tenant_id,key,label) VALUES(%s,'director','Giám đốc')", (t,))
            await c.execute("INSERT INTO core.approval_chains(tenant_id,key,label,steps) VALUES(%s,'settlement_payment','Công ty sửa',%s)", (edited, custom))
            await c.execute("INSERT INTO core.pack_installs(tenant_id,kind,key,pack,version) VALUES(%s,'chain','settlement_payment','finance','x')", (removed,))
            await c.execute("INSERT INTO core.tenant_entitlements(tenant_id,pack,granted_by) VALUES(%s,'finance','thử')", (entitled_only,))
        await migrate(URL)
        async with await AsyncConnection.connect(URL) as c:
            async def one(sql: Any, *p: Any) -> Any:
                row = await (await c.execute(sql, p)).fetchone()
                return row[0] if row else None
            steps = "SELECT steps FROM core.approval_chains WHERE tenant_id=%s AND key='settlement_payment'"
            assert await one(steps, fresh) == [{"kind": "role", "role": "director"}]
            assert await one(steps, edited) == [{"kind": "role", "role": "accountant"}]
            assert await one(steps, removed) is None
            assert await one("SELECT scope FROM core.grants WHERE tenant_id=%s AND role='director' AND resource='parties'", fresh) == "all"
            assert await one("SELECT pack FROM core.tenant_entitlements WHERE tenant_id=%s AND pack='directory'", entitled_only) == "directory"
    finally:
        await reset_schemas(URL)


async def test_messages_written_before_0115_stay_readable_and_become_searchable_core() -> None:
    """0115 — talk/031 trên trao đổi đã có: tin cũ giữ nguyên chữ, tìm được không dấu (cột body_fold tính cho dòng cũ), ràng buộc mới
    (tin chỉ có tệp) không làm hỏng tin cũ, chưa tin nào bị coi là sửa / thu hồi / ghim; không cần cửa sổ bảo trì."""
    await _db()
    try:
        await migrate(URL, until=_last_baseline("talk"))
        tid, uid, rid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await c.execute("INSERT INTO core.tenants (id, name) VALUES (%s, 'Công ty cũ')", (tid,))
            await c.execute("INSERT INTO core.users (id, email, name) VALUES (%s, 'nguoicu@mau.test', 'Người Cũ')", (uid,))
            await c.execute("INSERT INTO talk.rooms (id, tenant_id, name) VALUES (%s, %s, 'Phòng cũ')", (rid, tid))
            await c.execute("INSERT INTO talk.threads (id, tenant_id, subject_kind, subject_id, title) VALUES (%s, %s, 'talk.room', %s, 'Phòng cũ')",
                            (rid, tid, rid))
            await c.execute("INSERT INTO talk.messages (tenant_id, thread_id, author_id, body) VALUES (%s, %s, %s, 'Khách HẸN ký Hợp Đồng ở Đà Lạt')",
                            (tid, rid, uid))
        plan = await pending_plan(URL)
        assert "talk/031_talk_daily.sql" in plan["pending"] and "talk/031_talk_daily.sql" not in plan["maintenance"]
        await migrate(URL)
        async with await AsyncConnection.connect(URL) as c:
            row = await (await c.execute("SELECT body, body_fold, has_files, edited_at, recalled_at, pinned_at FROM talk.messages")).fetchone()
            assert row == ("Khách HẸN ký Hợp Đồng ở Đà Lạt", "khach hen ky hop dong o da lat", False, None, None, None)
            assert (await (await c.execute("SELECT kind FROM core.subject_kinds WHERE kind = 'talk.direct'")).fetchone()) == ("talk.direct",)
        assert await pending_plan(URL) == {"pending": [], "maintenance": []}
    finally:
        await reset_schemas(URL)


async def test_0122_bytea_bodies_move_into_the_store_and_stay_readable_core() -> None:
    """0122 — 041 + bước dữ liệu + 042 trên thân tệp bytea đã có (kb.files từ bản gốc, talk.attachments từ 031): mỗi thân vào kho, một dòng core.files
    (module = schema), file_id điền đủ, cột data bỏ; đọc lại từ kho cùng sha256, cùng cỡ. Hai chặng theo thứ tự gói: nền (kb) rồi Trao đổi."""
    import hashlib

    from barista.kernel import files

    await _db()
    try:
        await migrate(URL, until="base/040_company_settings.sql")
        tid, uid, doc, thread = (uuid.uuid4() for _ in range(4))
        pdf, png = b"%PDF-1.4 hop dong cu" + bytes(range(256)) * 300, b"\x89PNG\r\n\x1a\n" + b"\x07" * 5000
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await c.execute("INSERT INTO core.tenants (id, name) VALUES (%s, 'Công ty tệp cũ')", (tid,))
            await c.execute("INSERT INTO core.users (id, email, name) VALUES (%s, 'tep0122@example.test', 'Người giả')", (uid,))
            await c.execute("INSERT INTO kb.shelves (tenant_id, key, name, everyone) VALUES (%s, 'common', 'Chung', true)", (tid,))
            await c.execute("INSERT INTO kb.documents (id, tenant_id, title, body) VALUES (%s, %s, 'Hợp đồng cũ', 'x')", (doc, tid))
            await c.execute("""INSERT INTO kb.files (document_id, tenant_id, filename, content_type, size_bytes, data, created_by)
                               VALUES (%s, %s, 'hop-dong.pdf', 'application/pdf', %s, %s, %s)""", (doc, tid, len(pdf), pdf, uid))
        ran = await migrate(URL, until="talk/040_links_team_rooms.sql")              # thân tệp kb vào kho; Trao đổi còn cột bytea
        assert {"base/041_file_store.sql", "base/042_kb_files_required.sql"} <= set(ran)
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await c.execute("""INSERT INTO talk.threads (id, tenant_id, subject_kind, subject_id, title) VALUES (%s, %s, 'talk.room', %s, 'Phòng')""",
                            (thread, tid, uuid.uuid4()))
            mid = (await (await c.execute("""INSERT INTO talk.messages (tenant_id, thread_id, author_id, body) VALUES (%s, %s, %s, 'ảnh')
                                              RETURNING id""", (tid, thread, uid))).fetchone())[0]   # type: ignore[index]
            await c.execute("""INSERT INTO talk.attachments (tenant_id, message_id, filename, content_type, size_bytes, sha256, data, created_by)
                               VALUES (%s, %s, 'anh.png', 'image/png', %s, %s, %s, %s)""",
                            (tid, mid, len(png), hashlib.sha256(png).hexdigest(), png, uid))
        assert {"talk/041_attachment_file_ref.sql", "talk/042_attachments_file_required.sql"} <= set(await migrate(URL))
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            async def one(sql: Any, *p: Any) -> Any:
                row = await (await c.execute(sql, p)).fetchone()
                return row if row else None
            for table, body, module in (("kb.files", pdf, "kb"), ("talk.attachments", png, "talk")):
                key, sha, size, mod, owner, ten = await one(
                    f"""SELECT f.object_key, f.sha256, f.size_bytes, f.module, f.created_by, f.tenant_id FROM {table} t
                        JOIN core.files f ON f.id = t.file_id""")   # type: ignore[misc]
                assert (sha, size, mod, owner, ten) == (hashlib.sha256(body).hexdigest(), len(body), module, uid, tid)
                assert b"".join([ch async for ch in files.store().read(key)]) == body
            assert await one("""SELECT count(*) FROM information_schema.columns WHERE column_name = 'data'
                                AND table_schema || '.' || table_name IN ('kb.files', 'talk.attachments')""") == (0,)
            assert not [p for p in await files.verify(c) if p["file_id"] in {str(r[0]) for r in await (await c.execute(
                "SELECT id FROM core.files WHERE tenant_id = %s", (tid,))).fetchall()}]
        assert await pending_plan(URL) == {"pending": [], "maintenance": []}
    finally:
        await reset_schemas(URL)


async def test_links_in_messages_written_before_0128_show_in_the_files_tab_core() -> None:
    """0128 — 040 trên trao đổi đã có: link trong tin cũ được rút theo đúng luật của code (bỏ dấu câu cuối, tên miền chữ thường), tin đã thu hồi
    không rút; phòng cũ không gắn đội; công ty cũ chưa có dòng cài đặt (đọc theo mặc định của gói); không cần cửa sổ bảo trì."""
    from barista.modules import talk

    await _db()
    try:
        await migrate(URL, until="talk/031_talk_daily.sql")
        tid, uid, rid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        body = "Bảng giá ở https://Mau.example.vn/bang-gia?id=1, còn hợp đồng: http://tai-lieu.test/hd.pdf."
        long_body = f"tên miền dài https://{'a' * 300}.test/x và link quá dài https://dai.test/{'b' * 2100}"   # không làm hỏng migration
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await c.execute("INSERT INTO core.tenants (id, name) VALUES (%s, 'Công ty cũ')", (tid,))
            await c.execute("INSERT INTO core.users (id, email, name) VALUES (%s, 'nguoicu@mau.test', 'Người Cũ')", (uid,))
            await c.execute("INSERT INTO talk.rooms (id, tenant_id, name) VALUES (%s, %s, 'Phòng cũ')", (rid, tid))
            await c.execute("INSERT INTO talk.threads (id, tenant_id, subject_kind, subject_id, title) VALUES (%s, %s, 'talk.room', %s, 'Phòng cũ')",
                            (rid, tid, rid))
            await c.execute("INSERT INTO talk.messages (tenant_id, thread_id, author_id, body) VALUES (%s, %s, %s, %s)", (tid, rid, uid, body))
            await c.execute("INSERT INTO talk.messages (tenant_id, thread_id, author_id, body) VALUES (%s, %s, %s, %s)", (tid, rid, uid, long_body))
            await c.execute("""INSERT INTO talk.messages (tenant_id, thread_id, author_id, body, recalled_at)
                               VALUES (%s, %s, %s, 'Tin đã được thu hồi https://bi-thu-hoi.test', now())""", (tid, rid, uid))
        plan = await pending_plan(URL)
        assert "talk/040_links_team_rooms.sql" in plan["pending"] and "talk/040_links_team_rooms.sql" not in plan["maintenance"]
        await migrate(URL)
        async with await AsyncConnection.connect(URL) as c:
            rows = await (await c.execute("SELECT url, host FROM talk.links ORDER BY url")).fetchall()
            assert rows == sorted([*talk.links(body), *talk.links(long_body)])
            assert sorted(talk.links(body)) == [("http://tai-lieu.test/hd.pdf", "tai-lieu.test"), ("https://Mau.example.vn/bang-gia?id=1", "mau.example.vn")]
            assert [len(h) for _, h in talk.links(long_body)] == [255]                       # tên miền cắt 255; link > 2000 ký tự bỏ qua
            assert (await (await c.execute("SELECT team_id FROM talk.rooms")).fetchone()) == (None,)
            assert (await (await c.execute("SELECT count(*) FROM core.settings")).fetchone()) == (0,)
        assert await pending_plan(URL) == {"pending": [], "maintenance": []}
    finally:
        await reset_schemas(URL)


async def test_0122_move_refuses_without_a_configured_store_and_checks_every_object_before_dropping_data_core() -> None:
    """Review 08/11: (1) kho không cấu hình rõ (container chạy một lần thiếu volume) ⇒ bước dữ liệu từ chối, 042 hoàn, cột data còn nguyên;
    (2) đọc lại từ kho lệch sha256 ⇒ 042 hoàn; (3) tên tệp cũ dài hơn trần của sổ ⇒ cắt, giữ đuôi."""
    import hashlib

    from barista.config import settings
    from barista.kernel import files

    class Bent(files.LocalStore):   # kho "ghi được" nhưng đọc ra khác
        async def read(self, key: str):   # type: ignore[override]
            async for c in super().read(key):
                yield c[:-1] + b"X"

    async def state(c: Any) -> tuple[Any, ...]:
        row = await (await c.execute("""SELECT (SELECT count(*) FROM information_schema.columns WHERE table_schema = 'kb' AND table_name = 'files'
                                                AND column_name = 'data'), (SELECT count(*) FROM core.files),
                                               (SELECT count(*) FROM kb.files WHERE file_id IS NULL)""")).fetchone()
        return tuple(row or ())

    await _db()
    old_store, old_dir = files._store, settings.files_dir
    try:
        await migrate(URL, until="base/040_company_settings.sql")
        tid, doc = uuid.uuid4(), uuid.uuid4()
        pdf, long_name = b"%PDF-1.4 ten dai", "Hợp đồng " + "rất dài " * 60 + ".pdf"
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await c.execute("INSERT INTO core.tenants (id, name) VALUES (%s, 'Công ty tên dài')", (tid,))
            await c.execute("INSERT INTO kb.shelves (tenant_id, key, name, everyone) VALUES (%s, 'common', 'Chung', true)", (tid,))
            await c.execute("INSERT INTO kb.documents (id, tenant_id, title, body) VALUES (%s, %s, 'Tên dài', 'x')", (doc, tid))
            await c.execute("""INSERT INTO kb.files (document_id, tenant_id, filename, content_type, size_bytes, data)
                               VALUES (%s, %s, %s, 'application/pdf', %s, %s)""", (doc, tid, long_name, len(pdf), pdf))
        await migrate(URL, until="base/041_file_store.sql")
        files.use_store(None)
        settings.files_dir = ""                                                          # (1) không có thư mục kho chỉ định rõ
        with pytest.raises(RuntimeError, match="BARISTA_FILES_DIR"):
            await migrate(URL)
        settings.files_dir = old_dir
        files.use_store(Bent(old_dir))                                                   # (2) đọc lại lệch
        with pytest.raises(RuntimeError, match="lệch sha256"):
            await migrate(URL)
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            assert await state(c) == (1, 0, 1)                                           # 042 hoàn hết: data còn, chưa dòng sổ nào
        files.use_store(old_store)
        await migrate(URL)
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            name, sha = (await (await c.execute("SELECT filename, sha256 FROM core.files")).fetchone()) or ("", "")
            assert len(name) == files.NAME_MAX and name.endswith(".pdf") and name.startswith("Hợp đồng rất dài")   # (3)
            assert sha == hashlib.sha256(pdf).hexdigest()
            assert (await (await c.execute("SELECT filename FROM kb.files")).fetchone()) == (long_name,)   # bảng module giữ tên gốc
    finally:
        settings.files_dir = old_dir
        files.use_store(old_store)
        await reset_schemas(URL)


async def _077_company(c: Any, tid: Any, rows: dict[str, tuple[Any, ...]]) -> dict[str, Any]:
    """Công ty + khoản phải thu kiểu cũ (trước finance/077): (bản chất, số hiện tại, phí hiện tại, đã giảm, trạng thái, bút toán lập, thuế đã cộng)."""
    await c.execute("INSERT INTO core.tenants(id,name) VALUES(%s,'Công ty phải thu')", (tid,))
    eid = (await (await c.execute("INSERT INTO core.legal_entities(tenant_id,code,name,is_default) VALUES(%s,'CHINH','Chính',true) RETURNING id",
                                  (tid,))).fetchone() or [None])[0]
    await c.execute("INSERT INTO ledger.accounts(tenant_id,code,name,kind) VALUES(%s,'receivable:customers','PT','asset'),"
                    "(%s,'revenue:services','DT','revenue')", (tid, tid))
    ids: dict[str, Any] = {}
    for i, (k, (nature, amount, fee, credited, status, issued, vat)) in enumerate(rows.items()):
        opening = k.startswith("dau_ky")   # khoản đầu kỳ: bút toán lập theo từng dòng của lô, khoá ở recognized_key
        rid = (await (await c.execute(
            """INSERT INTO acct.receivables(tenant_id,code,source,source_ref,label,nature,amount_vnd,fee_vnd,revenue_account,payee_key,due_on,status,
                                            credited_vnd)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'revenue:services',%s,core.today(),%s,%s) RETURNING id""",
            (tid, f"TTCU{i:04d}", "test.opening" if opening else "test.order", k, k, nature, amount, fee, "" if nature == "revenue" else "nguoi_nhan",
             status, credited))).fetchone() or [None])[0]
        ids[k] = rid
        if issued:
            key = f"acct.opening_batch:lo1:{i}" if opening else f"acct.ar:{rid}"
            await c.execute("UPDATE acct.receivables SET recognized_key=%s WHERE id=%s", (key, rid))
            async with c.transaction():   # bút toán một khối (base/104): đầu + dòng cùng giao dịch
                jid = (await (await c.execute("INSERT INTO ledger.journals(tenant_id,memo,source,idempotency_key) VALUES(%s,'lập','acct.receivable',%s)"
                                              " RETURNING id", (tid, key))).fetchone() or [None])[0]
                await c.execute("""INSERT INTO ledger.postings(tenant_id,journal_id,account_id,amount_vnd)
                                   SELECT %s,%s,id,CASE WHEN code='receivable:customers' THEN %s ELSE -%s END FROM ledger.accounts WHERE tenant_id=%s""",
                                (tid, jid, issued, issued, tid))
        if vat:
            await c.execute("""INSERT INTO acct.vat_invoices(tenant_id,receivable_id,entity_id,kind,symbol,number,issued_on,category,treatment,rate_bps,
                                                             price_mode,base_vnd,tax_vnd,gross_vnd,rule_refs)
                               VALUES(%s,%s,%s,'invoice','1C26TAA',%s,core.today(),'dich_vu','standard',1000,'exclusive',%s,%s,%s,'{vn-vat@test:r}')""",
                            (tid, rid, eid, str(i + 1), amount - vat, vat, amount))
    return ids


async def test_finance_077_fills_the_issued_amount_from_the_source_not_the_state() -> None:
    """finance/077 (rà Codex 20/11, hai lần): số lúc lập lấy từ NGUỒN — bút toán lập ``acct.ar:<id>`` khi có; không có thì số hiện tại trừ thuế của
    hoá đơn giá chưa thuế còn hiệu lực. Đúng cả khi khoản bị giảm rồi huỷ bằng đường khác (lập 100, giảm 20, huỷ 80 ⇒ 100) và khi cộng VAT (lập
    100, cộng thuế 10 ⇒ 100, không phải 110) và với khoản đầu kỳ (bút toán từng dòng của lô, khoá ở ``recognized_key``). Sau nâng cấp: chèn mới tự điền, sửa bị chặn."""
    await _db()
    try:
        await migrate(URL, until="finance/076_discrepancy_dossiers.sql")
        tid = uuid.uuid4()
        rows = {"giu": ("revenue", 50, 50, 0, "open", 50, 0), "giam_roi_huy": ("revenue", 80, 80, 20, "void", 100, 0),
                "giam_het": ("revenue", 80, 80, 100, "void", 100, 0), "giam_mot_phan": ("revenue", 70, 70, 30, "open", 100, 0),
                "cong_vat": ("revenue", 110, 110, 0, "open", 100, 10), "thu_ho_vat": ("pass_through", 110, 30, 0, "open", None, 10),
                "dau_ky_giam": ("revenue", 80, 80, 20, "open", 100, 0)}
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            ids = await _077_company(c, tid, rows)
        await migrate(URL)
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            got = {k: tuple((await (await c.execute("SELECT issued_amount_vnd, issued_fee_vnd FROM acct.receivables WHERE id=%s", (rid,))).fetchone()) or ())
                   for k, rid in ids.items()}
            assert got == {"giu": (50, 50), "giam_roi_huy": (100, 100), "giam_het": (100, 100), "giam_mot_phan": (100, 100),
                           "cong_vat": (100, 100), "thu_ho_vat": (100, 20), "dau_ky_giam": (100, 100)}
            with pytest.raises(psycopg.errors.RaiseException, match="không sửa được"):
                await c.execute("UPDATE acct.receivables SET issued_amount_vnd = 1 WHERE id=%s", (ids["giu"],))
    finally:
        await reset_schemas(URL)


async def test_finance_077_stops_when_the_sources_disagree() -> None:
    """finance/077: khoản có bút toán lập 100, chưa giảm, không hoá đơn, mà số hiện tại 90 ⇒ hai nguồn không khớp ⇒ migration DỪNG, nêu mã khoản —
    không đoán."""
    await _db()
    try:
        await migrate(URL, until="finance/076_discrepancy_dossiers.sql")
        async with await AsyncConnection.connect(URL, autocommit=True) as c:
            await _077_company(c, uuid.uuid4(), {"lech": ("revenue", 90, 90, 0, "open", 100, 0)})
        with pytest.raises(Exception, match="TTCU0000"):
            await migrate(URL)
    finally:
        await reset_schemas(URL)
