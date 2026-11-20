# Runbook 19 · Báo cáo chạy song song (ADR 0230)

Người làm: Claude Sonnet (Anthropic), 15/11/2026. Dành cho người vận hành bản cài đang ở chế độ chỉ ghi nhận (0207) cạnh hệ đang dùng.

## Bật và đọc
1. Bật gói `findings` cho công ty (Gói & ứng dụng, hoặc `admin.set_pack`); muốn xuất tờ thì bật cả `compose`. Giám đốc và quản trị có quyền `findings` (phạm vi cả công ty).
2. Menu "Chạy song song" → "Báo cáo chạy song song". Kỳ mặc định = từ ngày lead đầu tiên vào hệ thống. Mỗi con số bấm được ra dòng gốc; mỗi phát hiện nêu công thức + độ tin cậy.
3. "Xuất tờ cho hội đồng quản trị" → bản soạn trong Soạn với AI (mẫu tạp chí, số là ảnh chụp truy vấn); xuất Word / PDF / trang web hoặc "Chia sẻ" bằng link có đếm lượt xem.
4. Báo cáo CHỈ ĐỌC — không thông báo, không chia lead, không webhook. An toàn để mở trên bản cài đang chỉ ghi nhận.

## Trước khi cho khách xem
- Khai mốc dữ liệu đáng tin ở Marketing ("Khai mốc dữ liệu tin cậy": chi phí đủ từ ngày, lead đủ từ ngày, chi phí đã nhập tới) — chưa khai thì số chi phí / lead có lưu ý "chưa kiểm độ đủ".
- Mọi nguồn lead nên gửi mã chiến dịch; dưới 50% lead quảng cáo gắn chiến dịch ⇒ các mục so chi phí với lead tự thành "chưa đo được".
- Các ước tính chồng lên nhau — không cộng thành tổng khi trình bày.

## Thêm một phát hiện (module hoặc gói)
Khai truy vấn nội bộ `<module>.findings_report(date_from, date_to)` (`internal=True`, `agent="deny"`, `callers=("findings",)`, `why=`), trả `{"findings": [...], "context": [...], "notes": [...]}` bằng `barista.api.findings`
(`finding` / `unmeasured` / `metric` / `impact_money` / `impact_count`). Luật: chưa đo được ⇒ `unmeasured(...)` (không số); đo được ⇒ nêu `formula` + `confidence`; bấm số mở dòng gốc ⇒ khai `drill` tới một truy vấn thường
`<module>.finding_rows` (`min_scope="all"`), tổng / số dòng khớp con số. Mẫu: `modules/mkt/findings.py`, gói ngoài: `barista-packs/commission/.../findings.py`.

## Dựng dữ liệu giả để bấm thử (máy dev)
`bash backend/scripts/dev_reset.sh` (DB giả, runbook 18) → `python backend/scripts/dev_shadow_data.py [--days 4]` (chiến dịch + chi phí 45 ngày, lead nạp bằng `leadflow.intake` ở chế độ lịch sử, phần lớn chưa có người nhận,
chiến dịch không ra lead, lead trùng, đồng chi trả 70/30 lệch) → vào màn bằng vai giám đốc, chọn kỳ "7 ngày qua". Lõi nạp gói theo entry point `barista.packs` (`backend/pyproject.toml`): máy dev dùng venv chung chưa `uv sync`
thì thêm dòng `findings = barista.modules.findings` vào `entry_points.txt` của dist-info `barista` trong một thư mục site RIÊNG đặt trước trên `PYTHONPATH` (không sửa venv dùng chung).
