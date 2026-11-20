# Barista — bức tranh tổng thể

> Lõi dùng chung cho nhiều công ty / ngành; ví dụ xuyên tài liệu là khách đầu tiên (sàn BĐS) — tài liệu riêng của khách ở `clients/<khách>/…`.

Lõi vận hành doanh nghiệp theo GÓI: **mỗi người một trợ lý** để hỏi và giao việc, bảng / form để soát và nhập hàng loạt; quyền,
tiền và giấy tờ do hệ thống kiểm soát, không do model. Ví dụ xuyên tài liệu là sàn môi giới / phân phối BĐS. Cập nhật 08/11/2026.
Gói, module, hợp đồng giữa module, điểm cắm, sự kiện: **[MODULE-MAP.md](MODULE-MAP.md)** (sinh từ code — file này không chép lại danh sách).

## 1. Các tầng

```
 Người dùng   Sale · Trưởng nhóm · Giám đốc · Kế toán · Marketing · Quản trị
     │
 App Mac      ┌ Trợ lý (chat + thẻ) ┐ ┌ Khung làm việc: thẻ lớn, màn ghim, bảng / form ┐   ← server mô tả, app chỉ vẽ
     │
 API          lệnh · truy vấn · form · nhập Excel · tải file · trợ lý (stream)
     │
 MỘT CỬA      executor: quyền hành động → kiểm dữ liệu → luật duyệt → chống lặp → nhật ký → sự kiện
     │            ▲ trợ lý (Pydantic AI) là một người gọi nữa — bằng quyền của chính người hỏi, ghi = đề xuất
 Gói         lõi (luôn bật) · năng lực dùng chung (khách hàng, định danh, tài chính, marketing, KPI, lịch, gọi video, trao đổi…)
              · ngành (BĐS bán / môi giới, cho thuê, dịch vụ kế toán, đào tạo, L/C, chứng khoán) — mỗi module một schema, bật / tắt theo
              công ty; danh sách thật: MODULE-MAP.md
     │            ▲ việc theo dõi nhiều ngày: DBOS giữ nhịp ngủ / thức, trợ lý của người phụ trách xử lý ngoại lệ
 Postgres     RLS (quyền theo dòng) · sổ cái ghi kép · outbox sự kiện · phiếu duyệt · schema dbos
     │
 Bên ngoài    Google Drive (đồng bộ tài liệu + quyền chia sẻ — chưa nối) · MISA (xuất file) · ngân hàng (lệnh chi)
```

## 2. Dòng nghiệp vụ chính

```
Chiến dịch QC ──► Lead ──► Nhu cầu ──► Gợi ý căn ──► Giữ chỗ / cọc ──► Ký HĐMB
 (marketing)     (sale)    (sale)     (trợ lý)       (sale / quản trị)   (quản trị)
                                                                          │ sự kiện
                                       ┌──────────────────────────────────┴──────────────┐
                                Hoá đơn phí MG gửi CĐT (kế toán)          Hoa hồng "chờ về" (theo chính sách ngày ký)
                                + VIỆC THEO DÕI cho trợ lý của kế toán: kiểm mỗi ngày; quá hạn ⇒ tra quy trình,
                                  soạn sẵn đề nghị thanh toán, báo "Cần bạn xem"; phiếu thu ⇒ thức ngay, đủ ⇒ đóng
                                       │ CĐT trả (từng đợt)                               │
                                Phiếu thu ──────────── sự kiện ──────────────► Hoa hồng về ví (theo tỷ lệ đã thu)
                                                                                          │
                                       Ứng / rút ví ──► duyệt (trưởng nhóm → kế toán) ──► Lệnh chi ──► Chuyển khoản
                                                                                          │
                                                          Sổ cái · đối soát công nợ · xuất MISA · dòng tiền dự phóng
```

Huỷ giao dịch (luật duyệt: giám đốc) ⇒ trả căn, huỷ hoá đơn chưa thu, thu hồi phần hoa hồng đã về ví — tự chạy qua sự kiện.

## 3. Mỗi vai mở app thấy gì

| Vai | Màn ghim | Hay hỏi trợ lý | Thấy dữ liệu |
|---|---|---|---|
| Sale | Việc hôm nay, Khách, Bảng hàng, Ví, Kho tài liệu | việc hôm nay, căn hợp khách, ví / hạn mức ứng | khách / giao dịch / ví **của mình** |
| Trưởng nhóm | Việc hôm nay, Khách, Giao dịch, Hoa hồng đội, Kho | việc của đội, phiếu chờ duyệt | **đội mình** (cả đội con) |
| Giám đốc | Giao dịch, Dòng tiền, Chiến dịch, Ví toàn công ty, Hoá đơn | doanh số, chiến dịch đốt tiền, dòng tiền âm | **cả công ty**; duyệt huỷ giao dịch |
| Kế toán | Việc đang theo, Hoá đơn, Dòng tiền, Lệnh chi, Giấy tờ | hoá đơn quá hạn, lệnh chi chờ, xuất MISA | tiền + giao dịch cả công ty; **không xem khách** |
| Marketing | Chiến dịch, Chi phí, Trục căn, Kho | chi phí / lead, ngân sách còn | chiến dịch của đội; lead chỉ là **số đếm** |
| Quản trị | Người dùng, Quyền, Sự kiện lỗi, Nhật ký, Nhập giao dịch | sự kiện lỗi, ai bị chặn quyền | cấu hình hệ thống + nhập liệu |

Tất cả là dữ liệu: quản trị đổi quyền / chuỗi duyệt / chính sách hoa hồng / trợ lý được làm gì trong app, không cần lập trình.

## 4. Ba lớp an toàn

1. **Quyền** — ma trận vai trò × tài nguyên (phạm vi + hành động), ép ở DB bằng RLS: lệnh quên lọc vẫn không lộ.
2. **Tiền** — mọi đồng qua sổ cái ghi kép: tổng 0, chỉ thêm, sửa bằng bút toán đảo, chống ghi trùng.
3. **Trợ lý** — không bao giờ hơn quyền người hỏi; ghi = thẻ đề xuất đã chạy thử, người bấm mới chạy; duyệt / ban hành chỉ
   người; tài liệu là dữ liệu, không phải lệnh.

## 5. Tình trạng

- **Hình dạng lõi (08/11/2026):** gói theo hai trục — năng lực dùng chung / ngành (0056), gói tự khai, kernel không biết tên gói (0069).
  Khách hàng + chia lead là năng lực chung; phần ngành (danh mục, giao dịch, đợt đóng tiền) cắm qua **điểm cắm** do module chủ khai (0110);
  `marketing` chỉ cần Khách hàng, `kpi` là gói năng lực riêng (0124). Một người một **định danh** ở `parties`: nhiều liên hệ, quan hệ, sự
  đồng ý chỉ thêm, gộp bằng con trỏ, dữ liệu trẻ em (0105, 0111, 0112). Năng lực mới: lịch & nguồn lực chống trùng bằng ràng buộc DB
  (`schedule`, 0113), gọi video gắn đối tượng + gọi 1–1 trong trình duyệt (`meet`, 0114), trao đổi đủ dùng hằng ngày (`talk`, 0115); gói
  ngành thứ ba kiểm chứng lõi: đào tạo `edu` lát 1–2 (0116; gói ở repo `barista-packs` từ 12/11/2026). **Từ vựng theo công ty**: code nói khoá khái niệm, chữ hiện ra là dữ liệu công
  ty (BĐS: "Dự án" — 0124). Phạm vi quyền **`unit`** = các đội mình tham gia, giữa `own` và `team`; mã người do người gọi nêu không đi
  thẳng vào quyền hệ thống (0125). Việc nhẹ có **`done_when`**: chỉ xong khi bộ kiểm thấy bằng chứng, mất căn cứ thì mở lại (0117).
- **Đọc tài liệu / OCR là một năng lực lõi (16/11/2026, ADR 0232):** `barista/docread` — một cửa `read()`, bộ đọc cắm được (Word / Excel / PowerPoint, PDF có chữ + bảng, model nhìn trang / ảnh), hồ sơ theo công ty × mục đích do nhà vận hành đặt (model, công tắc, trần trang, hạn mức token), chỉ trang cần mới tốn model, benchmark `python -m barista.docread.bench` (runbook 20).
- **Trợ lý cầm hồ sơ nhiều ngày (0142, nhánh `feat/case-runner` 08/11):** mẫu quy trình có `follow` ⇒ hồ sơ là một việc theo dõi của người phụ trách;
  lời dặn trợ lý theo chặng trong mẫu (`~`); đề xuất của việc theo dõi vào `core.proposals` (người bấm mới chạy); bàn việc nói trợ lý đã làm gì hôm nay.
- **Đã có từ trước:** khoản phải thu dùng chung (0057), quy trình & việc (`flow`), việc theo dõi nhiều ngày trên DBOS (0021, runbook
  [07](runbooks/07-viec-theo-doi.md)), lõi lead — chia theo luật, hạn gọi theo giờ làm việc, thu hồi (0034, runbook [08](runbooks/08-chia-lead-va-han-goi.md)),
  giữ căn có hạn (0035), link gửi khách (0064), thông báo (0044), app Mac + iPhone + web; trợ lý chạy thật với Gemini. Số test: `pytest -q`.
- **Chưa phát hành / chưa dùng thật:** phần lớn mục trên mới ở `main`, chưa lên bản demo; đăng nhập Google thật, thử
  khôi phục sao lưu, rà đường nâng quyền.
- **Việc tiếp theo:** đợt dọn N, rồi độ sâu BĐS cho khách đầu tiên; tệp ra R2 (0122) và ngày hạch toán / khoá kỳ
  (0119) do phiên khác soạn. Chờ khách: cách tính hoa hồng / ứng, nhiều người hưởng — `clients/<khách>/…` §9.

## 6. Bản đồ tài liệu

| Tài liệu | Trả lời câu hỏi |
|---|---|
| [GLOSSARY.md](GLOSSARY.md) | Từ điển kiến trúc dùng chung: gói / phân hệ / module / màn / tool / skill / workflow |
| `OVERVIEW.md` (file này) | Hệ thống là gì, chạy thế nào, đang ở đâu |
| [MODULE-MAP.md](MODULE-MAP.md) | Gói / module / hợp đồng / điểm cắm / sự kiện — sinh từ code (`backend/scripts/module_map.py`), test bắt lệch |
| `ARCHITECTURE.md` | Kiến trúc lõi: module nào giữ gì, quyền chi tiết, nhập liệu, trợ lý, đăng nhập |
| tài liệu riêng của khách | KHÔNG ở repo này — nằm ở repo nội bộ cùng thông tin bản cài |
| decisions/ | Vì sao chọn cách này (mỗi quyết định một file, có test giữ); mục lục có trạng thái hiệu lực |
| `runbooks/` | Làm thế nào: chạy, kiểm, thêm thao tác, quản trị, sự cố |
| lộ trình, việc tiếp theo | KHÔNG ở repo này — nằm ở repo nội bộ |
| `../CLAUDE.md` | Luật bất biến cho người / agent sửa code |
