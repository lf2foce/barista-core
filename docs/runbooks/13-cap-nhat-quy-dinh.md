# 13 · Có luật / nghị định mới — cập nhật bộ quy định

Quyết định: 0067; phát hành an toàn (hợp đồng mã, hồi tố, so sánh, cổng test): 0154. Bộ quy định ở `backend/src/barista/regulations/<bộ>/<ngày hiệu lực>.json`
(bộ hiện có: `vn-tax` — Thuế & BHXH Việt Nam; `vn-payroll` — lương: thuế, bảo hiểm, làm thêm; `vn-labour` — ngày lễ, trần làm thêm, phép năm; `vn-edu` — giáo dục ngoài nhà trường, ĐI CÙNG gói Đào tạo ở repo gói: `barista-packs/edu/src/barista_pack_edu/regulations/vn-edu/`, không còn ở lõi (0155); `vn-personal-data` — ngưỡng tuổi bảo vệ dữ liệu cá nhân, 0112). Mỗi file là bản chụp ĐẦY ĐỦ quy định áp từ ngày đó. Không sửa code.

## Thêm phiên bản

1. Chép file mới nhất thành `<ngày hiệu lực của văn bản>.json` (vd `2026-07-01.json`). Sửa `version` + `effective_from` cho trùng tên file, và đặt `published_on` = ngày phát hành thật (yyyy-mm-dd, hôm nay). **Hiệu lực sớm hơn ngày phát hành là HỒI TỐ** (kỳ nháp tính lại sẽ đổi số): bắt buộc thêm `retroactive_reason` (vì sao, ai chấp thuận, ngày) — thiếu thì file không nạp được. Chép MỌI quy tắc từ phiên bản trước: phiên bản mới nhất phải đủ mã bắt buộc.
2. Sửa đúng phần văn bản đổi:
   - hạn nộp ⇒ `obligations[].due` (`months` = số tháng sau tháng cuối kỳ, 0 = trong chính tháng đó; `day` = ngày, 31 = cuối tháng);
   - nghĩa vụ mới / bãi bỏ ⇒ thêm / xoá phần tử `obligations` (mã giữ nguyên nếu là cùng nghĩa vụ — kỳ cũ / mới nối được với nhau);
   - áp cho một nhóm khách ⇒ `when` trên dữ kiện `profile` (cần dữ kiện mới ⇒ thêm vào `profile` kèm `options` + `default`);
   - ngưỡng / mức ⇒ `params`;
   - ngày lễ KHÔNG nằm ở đây — xem mục Lịch nghỉ lễ bên dưới (0068).
3. Mỗi quy tắc ghi `basis`: số hiệu văn bản + điều / khoản / điểm. Cập nhật `summary` (một câu: đổi gì) và `sources`.
4. `status`: `assumption` cho tới khi người có chuyên môn soát; soát xong ⇒ `verified` + `verified_by` ("Tên — dd/mm/yyyy").
5. Kiểm: `cd backend && uv run pytest -q tests/test_regulations.py tests/test_pay_statutory.py tests/test_books.py` (nạp mọi file: sai cấu trúc / thiếu căn cứ / hai quy tắc
   mâu thuẫn / mã lạ / thiếu mã bắt buộc / hồi tố không lý do ⇒ đỏ), rồi cả bộ test. Xem diff NGHIỆP VỤ giữa hai phiên bản ở Sổ sách › Bộ quy định › "So với phiên bản trước"
   (`books.compare_versions`: thêm / bỏ / đổi từng tham số, nghĩa vụ, dữ kiện, lịch + đổi trạng thái xác minh) rồi gửi kế toán soát trước khi phát hành.
6. Phát hành như bình thường (runbook 11). Tới ngày hiệu lực, vòng quét hằng ngày đổi danh mục nghĩa vụ của mọi khách và báo người phụ trách /
   soát xét (một lần). Kỳ kết thúc từ ngày hiệu lực theo luật mới; kỳ đã mở giữ hạn cũ (ghi phiên bản đã tính).

**Hợp đồng mã (0154).** Mỗi bộ có module dùng nó khai `regulations.declare(bộ, required=…, optional=…, open_kind=…, obligations=…)`; mã trong file phải nằm trong hợp đồng, nếu không
`RegulationError` "mã tham số lạ …" (gõ sai mã, hoặc quy tắc mới mà code chưa đọc ⇒ sẽ KHÔNG bao giờ được áp). Muốn thêm mã mới: sửa module (đọc mã đó) + thêm vào `required` / `optional` cùng commit.
Khoản đóng mới của lương (bảo hiểm / công đoàn) chỉ cần `value.kind = "contribution"` — vòng lặp chung của engine áp, không sửa code. Hợp đồng từng bộ: `vn-payroll` = 7 mã
engine đọc theo tên (`engine.RULE_CODES`) + mọi khoản đóng; `vn-labour` = `PUBLIC_HOLIDAYS`, `OT_CAPS`, `ANNUAL_LEAVE`; `vn-personal-data` = `dual_consent_from_age`, `majority_age`;
`vn-edu` = `blocked`, `disclose`, `consent_purposes`; `vn-tax` = mọi nghĩa vụ + dữ kiện (sổ sách áp), tham số chỉ hiển thị. Bộ MỚI: module dùng nó phải khai trước khi phát hành (test
`test_every_shipped_pack_meets_its_code_contract_both_ways` đỏ nếu thiếu). Kiểm này cũng chạy lúc nạp lõi: file sai ⇒ dịch vụ không khởi động.

**Văn bản đẻ ra LOẠI quy tắc code chưa biết** (vd hạn tính theo ngày làm việc thứ N, nghĩa vụ phát sinh theo sự kiện thay vì định kỳ): thêm loại ở
`kernel/regulations.py` + test, ghi quyết định mới. Không nhét công thức vào JSON.

## Lịch nghỉ lễ năm mới (0068)

Lịch tách khỏi luật: `backend/src/barista/calendars/vn-public/<năm>/<ngày công bố>.json` — cuối tuần, `holidays`, `working_days` (ngày làm bù),
`sources` (link văn bản chính thức), `basis`, `status`. Chép file năm trước, sửa ngày theo thông báo chính thức, chạy `tests/test_business_calendar.py`.
Năm chưa có lịch: giữ lịch tuần của bản gần nhất của cùng lịch, không dùng lại ngày lễ năm cũ; hiện cảnh báo "chưa có lịch năm …".
Với `vn-public` vẫn nghỉ thứ Bảy / Chủ nhật; `vn-ca-tuan` vẫn làm cả tuần. Nạp lịch không đổi hạn các kỳ đã mở hoặc việc đã giao.

## Bổ sung nghĩa vụ / đổi hạn một kỳ

Nghĩa vụ tự khai: khi thêm ở Danh mục nghĩa vụ, chọn "Hạn rơi vào ngày nghỉ" (giữ nguyên / dời theo lịch) đúng theo văn bản của nghĩa vụ đó.

Soát xét / quản trị: Kỳ sổ sách › Bổ sung nghĩa vụ; Nghĩa vụ & hạn nộp › Điều chỉnh hạn — căn cứ bắt buộc, hạn ban đầu + lịch sử giữ nguyên.

## Kỳ đã mở mà luật đổi hồi tố

Hiếm. Kỳ sổ sách đã mở không tự đổi (đúng chủ ý — hạn đã báo khách). Soát xét dùng "Điều chỉnh hạn" cho từng nghĩa vụ của kỳ đó, ghi căn cứ.

**Lương (0154):** kỳ lương NHÁP tính lại theo luật hiện hành nên phiên bản hồi tố làm đổi số. Màn kỳ lương hiện cờ "Luật đã đổi phiên bản từ lúc tính" (quy tắc nào, phiên bản cũ → mới) TRƯỚC
khi bấm Tính lại; lệnh Tính lại trả `law_changes` + tổng thực nhận trước / sau. Kế toán xem cờ rồi mới tính lại. Kỳ đã gửi duyệt / chốt không tính lại, giữ nguyên `rule_ref` — sửa sai là kỳ
"sửa sai", không tính đè.

## Khách đổi dữ kiện

Sổ sách › Bộ quy định › Khai dữ kiện (vd chuyển khai GTGT theo tháng, không còn lao động) — áp cho kỳ mở sau. Loại hình (doanh nghiệp / hộ) đổi ở
Thiết lập dịch vụ.

## Test luật khi không có GitHub CI (0154)

Workflow GitHub chỉ chạy tay (tài khoản hết hạn mức). Cổng thật là `deploy/release.sh`: chạy `ruff + pyright + pytest -q` ĐẦY ĐỦ trên bản `git archive` của commit trước khi gửi lên máy chủ,
không có đường bỏ qua. Cổng sớm: `.githooks/pre-push` (bật bằng `bash scripts/install_hooks.sh`, runbook 11) — khi đổi `backend/src/barista/regulations/`, `kernel/regulations.py` hoặc
`modules/pay/engine.py` nó chạy `test_regulations`, `test_pay_statutory`, `test_books`, `test_business_calendar`, `test_minors`, `test_pack_surface` (~45 giây). Bỏ qua khẩn cấp:
`git push --no-verify` — rồi `release.sh` vẫn chạy đủ bộ test.
