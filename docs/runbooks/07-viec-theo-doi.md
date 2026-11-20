# 07 · Việc trợ lý theo dõi (nhiều ngày, DBOS)

Vì sao làm vậy: 0021, 0026.
Mã: `backend/src/barista/modules/work.py` (nghiệp vụ, chạy được không cần DBOS) · `backend/src/barista/work_runtime.py` (nhịp DBOS).

## Chạy

- Server tự bật DBOS (`BARISTA_WORK_ENGINE=1`, mặc định). Hệ thống DBOS nằm ở schema `dbos` trong cùng database
  (`BARISTA_DBOS_DATABASE_URL` trống ⇒ dùng `BARISTA_OWNER_DATABASE_URL`). Sao lưu database là sao lưu cả hai.
- Tắt (`BARISTA_WORK_ENGINE=0`): việc vẫn được mở / ghi, chỉ không tự kiểm. Bật lại ⇒ vòng đối soát (5 phút, ngay khi khởi
  động) dựng workflow cho mọi việc đang mở.
- Trợ lý xử lý ngoại lệ cần khoá model như chat (runbook [01](01-chay-tren-may-dev.md)); không có khoá ⇒ soạn dự thảo + lời
  báo mẫu.
- Demo trên dữ liệu mẫu: `seed --reset` rồi bật server ⇒ ~10 giây sau: 4 việc xong (đã thu), 1 "Cần bạn xem" (GD-HSR-10B06
  quá hạn 5 ngày, trợ lý đã soạn dự thảo), 3 đang theo. Đăng nhập kế toán ⇒ màn ghim đầu "Việc đang theo". Ghi phiếu thu đủ
  cho HD-GD-HSR-10B06 ⇒ việc đóng sau ~1 giây.

## Xem tình hình

```sql
-- việc đang mở + lần kiểm tới (giờ VN)
SELECT status, subject_ref, agent_runs, next_check_at AT TIME ZONE 'Asia/Ho_Chi_Minh', left(last_note, 80)
FROM work.items WHERE status IN ('active', 'waiting') ORDER BY next_check_at;
-- workflow của từng việc
SELECT w.workflow_uuid, w.status, w.recovery_attempts, i.status AS item_status
FROM dbos.workflow_status w JOIN work.items i ON w.workflow_uuid LIKE 'work:%:' || i.id;
```

Trong app: Việc trợ lý theo dõi › Việc đang theo (kế toán: ghim đầu). Mở một việc ⇒ dòng thời gian, tải dự thảo, "Tôi đã
xử lý" / "Dừng theo dõi".

## Sự cố

| Triệu chứng | Nguyên nhân | Xử lý |
|---|---|---|
| Việc "Dừng (lỗi)" — "không còn hoạt động" | Người phụ trách bị khoá | Giao lại: mở việc mới (`work.start` bằng người mới) |
| Việc "Dừng (lỗi)" — "Dừng vì quyền" | Người phụ trách đổi vai / mất quyền xem đối tượng | Đúng thiết kế. Sửa quyền hoặc giao người khác |
| `next_check_at` trôi qua lâu mà không kiểm | Lỡ tín hiệu / workflow kẹt | Vòng đối soát tự đánh thức sau 10 phút; workflow `ERROR` ⇒ tự chạy tiếp. Còn kẹt: khởi động lại server (DBOS chạy tiếp workflow `PENDING`) |
| Không mở việc khi ký | Không ai có quyền ghi Kế toán phạm vi toàn công ty | Quản trị › Vai trò & quyền |
| Trợ lý không soạn dự thảo | Người phụ trách thiếu quyền Giấy tờ, hoặc quản trị đặt `commission.draft_fee_request` = cấm trợ lý | Đúng thiết kế (công cụ = quyền người ∩ chính sách) |

## Đổi workflow (deploy khi có việc đang ngủ)

- Sửa `work.step`, câu dặn trợ lý, mốc ngày (`GRACE_DAYS`, `REPEAT_DAYS`) ⇒ deploy bình thường.
- Đổi thứ tự / số bước, `recv` trong `follow` ⇒ tăng `WORKFLOW_VERSION` trong `work_runtime.py` cùng commit. Sau deploy,
  workflow bản cũ đứng yên (không chạy tiếp); vòng đối soát dựng workflow bản mới cho mọi việc mở. Kiểm bằng truy vấn trên:
  mọi việc mở có workflow `work:v<mới>:…`.
- KHÔNG xoá / sửa tay bảng `work.items` để "chạy lại" — bấm "Tôi đã xử lý" (đánh thức) hoặc "Dừng theo dõi".

## Thêm một loại việc (0076)

1. Trong module của GÓI sở hữu: `@m.work_kind("<khoá>", "<nhãn>", subject_help=…, tools=(…))` — thao tác nhận `{subject}`, chạy bằng QUYỀN
   NGƯỜI PHỤ TRÁCH, trả `{ref, title, state: done|cancelled|wait|open, facts: {…, due?, late_days?}, summary, closing?, task, fallback, draft?}`.
   Điều kiện xong do code của gói; `task` là lời dặn trợ lý khi quá hạn; `fallback` là lời báo khi không có model.
2. Mở việc khi có sự kiện: `ctx.call_system("work.open", …)` (hợp đồng `callers=` của `work.open` phải có module đó); có dữ kiện mới ⇒ `work.wake`.
3. Test: vòng đời + quyền (mẫu: `tests/test_work.py`, `tests/test_case_runner.py`).

## Trợ lý cầm hồ sơ nhiều ngày (0142)

- Mẫu quy trình có **Trợ lý theo dõi hồ sơ** (`follow`) ⇒ mỗi hồ sơ mở theo mẫu là một việc theo dõi loại `case` của người phụ trách hồ sơ. Mẫu
  mặc định `giao_dich` của gói Môi giới bật sẵn; mẫu công ty tự viết thì bật ở form Lưu mẫu quy trình.
- Lời dặn trợ lý theo chặng: dòng `~ …` trong chặng của mẫu (có phiên bản như mọi phần khác). Không có ⇒ lời dặn chung.
- Mỗi lần kiểm (code): chặng hiện tại, ngày ở chặng so với hạn chặng, mục còn thiếu, việc của hồ sơ quá hạn / kẹt. Quá hạn > 3 ngày ⇒ ngoại lệ:
  trợ lý của người phụ trách (bộ công cụ hẹp: xem hồ sơ / việc, giao lại / dời hạn / thêm việc, nhắc trên hồ sơ, gửi link nộp giấy tờ, tra quy
  trình) soạn ĐỀ XUẤT; thẻ vào `core.proposals` của người phụ trách ⇒ bấm ở "Trợ lý soạn sẵn" (app) — chạy bằng quyền người bấm.
- Nhắc SỚM (lát 2): hạn chặng hôm nay / ngày mai mà còn thiếu mục, hoặc bên ngoài đã gửi giấy tờ qua link chờ bạn nhận ⇒ "Cần bạn xem" trước khi
  kẹt. Yêu cầu gửi bên ngoài quá hạn nộp tính vào quá hạn của hồ sơ. Nhắc sớm và báo quá hạn có nhịp riêng (`work.events.data.reason`).
- Hồ sơ sang chặng / đóng / huỷ ⇒ việc được đánh thức ngay. Đổi người phụ trách ⇒ `work.handoff`: việc cũ dừng, việc mới cho người mới.
- Xem trợ lý đã làm gì hôm nay: màn "Việc trợ lý theo dõi" (ba số: đã kiểm, đã khép, đề xuất đã soạn) và câu tín hiệu trên bàn việc.

```sql
-- việc theo dõi hồ sơ + đề xuất đang mở của chúng
SELECT i.title, i.status, i.facts->>'stage_name' AS stage, i.facts->>'late_days' AS late,
       (SELECT count(*) FROM core.proposals p WHERE p.key LIKE 'work:' || i.id || ':%' AND p.status = 'open') AS proposals
FROM work.items i WHERE i.kind = 'case' AND i.status IN ('active', 'waiting') ORDER BY i.updated_at DESC;
```
