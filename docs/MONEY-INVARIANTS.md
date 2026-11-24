# Bất biến của đường tiền: ai giữ, kiểm ở đâu, test nào

Mỗi bất biến có **đúng một nơi giữ**. Nơi gọi không kiểm lại bằng tiêu chí riêng; cần thoát sớm trước việc phụ thì gọi đúng hàm của nơi giữ.
Sửa một bất biến ⇒ sửa ở nơi giữ, chạy test ghi bên cạnh. Thêm nghiệp vụ ghi tiền mới ⇒ đi qua các nơi giữ dưới đây, không viết bản so riêng.
`backend/tests/test_money_owners.py` chặn tái phạm (module tự so / tự dựng dòng bút toán, ghi bảng sổ cái ngoài `kernel/ledger.py`) và kiểm trang này không
mục (mọi nơi giữ, mọi test nêu dưới đây phải có thật). Ghi sổ trả `ledger.Posted(journal_id, created)` — việc phụ sau khi ghi (phân bổ, chứng từ)
chỉ chạy khi `created`.

## Ba lớp, không chồng nhau

| Lớp | Sở hữu | Không làm |
|---|---|---|
| **Sổ cái** — `kernel/ledger.py` + schema `ledger` | Một bút toán: hình dạng, khoá chống lặp của bút toán, đảo, khoá kỳ, đọc theo ngày hạch toán | Không biết khoản phải thu, phiếu thu, phí |
| **Tài chính** — `modules/acct` (`receivables.py`, `cash_in.py`, `reconcile.py`, `payouts.py`; phần dùng chung `common.py`) | Chứng từ nghiệp vụ: khoản phải thu (số lúc lập, trạng thái, giấy báo có), dòng sao kê và phân bổ, lệnh chi, phép tính tiền (phí thu hộ) | Không ghi thẳng bảng `ledger.*`, không tự so dòng bút toán |
| **Hợp đồng đọc sổ** — `modules/acct/ledger_read.py` | Dữ liệu sổ cho gói + dấu ảnh dữ liệu | Không lập báo cáo pháp định (việc của gói) |

## Sổ cái

| Bất biến | Nơi giữ | Test |
|---|---|---|
| Bút toán là MỘT khối: ≥ 2 dòng khác 0, tổng 0; dòng chỉ vào bút toán ghi cùng giao dịch — không thêm dòng vào bút toán đã ghi. Ghi vẫn dưới RLS của vai ứng dụng (không hàm quyền đặc biệt) | DB (base/104): trigger `t_postings_same_entry` + trigger hoãn `t_journals_whole`; `ledger.write_journal` ghi đầu + dòng một lời gọi | `test_goods_books::test_the_app_role_writes_whole_entries_only`, `::test_writing_the_ledger_still_needs_the_ledger_grant`, `test_ledger_invariants::test_unbalanced_journal_rejected_by_python_and_by_the_database` |
| Chỉ thêm: không sửa / xoá dòng, sửa = bút toán đảo | DB: trigger `ledger.immutable` (000_baseline) | `test_ledger_invariants::test_ledger_is_append_only` |
| Một khoá chống lặp = một bút toán; gọi lại khác dòng (so từng dòng Nợ / Có, không gộp ròng), khác pháp nhân / ngày đã nêu ⇒ Conflict — cả khi hai yêu cầu đồng thời | `ledger.replayed` — `ledger.post` gọi trước khi ghi VÀ khi thua đua | `test_goods_books::test_replay_compares_debit_and_credit_lines_not_the_net`, `::test_same_key_different_amount_at_the_same_time_loser_gets_conflict` |
| Mỗi bút toán gốc đảo đúng một lần | DB: chỉ mục duy nhất `ux_journals_reverse_once` (base/104) | `test_goods_books::test_an_entry_is_reversed_once_even_with_two_keys_or_at_the_same_time` |
| Kỳ đã khoá theo pháp nhân không nhận bút toán | DB: trigger khoá kỳ (base/045) | `test_ledger_dates::test_lock_is_enforced_by_the_database_even_for_a_raw_owner_insert`, `::test_lock_of_one_entity_does_not_block_another` |
| Tạo mới hay đọc lại — bên gọi biết chính xác | `ledger.post` / `ledger.reverse` trả `Posted(journal_id, created)` — một đường duy nhất | các test trên |

## Tài chính

| Bất biến | Nơi giữ | Test |
|---|---|---|
| Khoản phải thu gọi lại cùng (nguồn, tham chiếu) so với DỮ KIỆN LÚC LẬP (bản chất, số tiền, phí, tài khoản, mục, người nhận; pháp nhân / ngày khi nêu rõ) — tuần tự lẫn đồng thời | `acct.receivables._replayed_receivable`; số lúc lập `issued_amount_vnd` / `issued_fee_vnd` do DB điền lúc chèn và chặn sửa (finance/077) | `test_goods_books::test_receivable_replay_with_another_amount_is_a_conflict`, `::test_receivable_replay_compares_the_issued_facts_not_the_adjusted_amount`, `test_upgrade::test_finance_077_…` |
| Phí thu hộ: một công thức số nguyên (luỹ kế, làm tròn nửa lên) cho ghi nhận và dự phóng; thu đủ = đúng bằng phí | `acct.cash_in.fee_earned` | `test_goods_books::test_pass_through_fee_is_exact_integer_math` |
| Một giao dịch ngân hàng = một dòng sao kê; phiếu thu là phần phân bổ của dòng (ADR 0057) | `acct.cash_in._cash_line`, `acct.cash_in._allocate` | `test_goods_books::test_one_net_bank_line_settles_many_receivables_with_deductions`, `test_discrepancy_dossier` |
| Số TỔNG LƯU SẴN là bản tóm tắt của dòng chi tiết, không phải nguồn: `acct.receivables.paid_vnd` = phiếu thu − đảo phiếu, `credited_vnd` = các lần ghi giảm; `acct.statement_lines.allocated_vnd` = phiếu thu − đảo + thu hồi + phân bổ vào khoản đặt, `deducted_vnd` = các khoản khấu trừ. Lưu để lọc / sắp trang trên cả tập mà không cộng lại từng dòng; mỗi lệnh ghi dòng chi tiết cập nhật tổng trong CÙNG giao dịch, dưới khoá dòng của khoản / dòng sao kê | `acct.cash_in._allocate`, `acct.corrections`, `acct.placements` (nơi DUY NHẤT sửa tổng); kiểm lệch theo tập trên dữ liệu thật: `acct/integrity.py` `_drift` (mục `money_drift` của kiểm toàn vẹn tiền) | `test_money_stress::test_set_based_drift_check_matches_the_per_row_definition`, `test_money_stress::_money_ok` sau từng kịch bản ghi đồng thời. Đo 24/11/2026 trên 5.923 khoản phải thu của một bản cài: 0 lệch (Codex) |

## Luật cho gói ghi tiền (qua các họ thao tác của Tài chính)

- **Chứng từ tiền đã lập không lập lại với dữ kiện khác.** Muốn đổi: giảm / huỷ (giấy báo có, huỷ khoản), rồi lập lại bằng **tham chiếu mới** — thêm lần
  lập vào tham chiếu (`<tham chiếu>#2`, tất định từ những gì đã lập để lần thử lại ra đúng tham chiếu cũ). Gửi lại khác dữ kiện với tham chiếu cũ ⇒ Conflict.
- **Gói không tự kiểm trùng dữ kiện tiền** (số tiền, phí, ngày): gửi thẳng, lõi so (bảng trên). Ngày / pháp nhân chỉ gửi khi người dùng nêu rõ — gửi
  "hôm nay" thay cho ô trống biến lần nhập lại hôm sau thành Conflict.
- **Vòng quét lập hàng loạt cô lập lỗi từng mục** (điểm lưu theo khách / chứng từ): một Conflict không dừng cả công ty, mục lỗi được báo.

## Đọc sổ cho gói

| Bất biến | Nơi giữ | Test |
|---|---|---|
| Một báo cáo không ghép hai trạng thái sổ hay hai phiên bản ánh xạ: token phủ CẢ sổ của pháp nhân (không theo kỳ), dùng chung cho số dư và dòng | `ledger_read._snapshot`, `ledger.journal_count` (đúng vì bút toán một khối + chỉ thêm) | `test_ledger_read::test_snapshot_refuses_to_mix_two_ledger_states_or_two_mappings`, `::test_one_token_covers_opening_balances_and_period_lines` |
