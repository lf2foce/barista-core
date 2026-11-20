# Phông nhúng của trang tạp chí (0224 §8)

| File | Phông | Độ đậm | Dùng cho |
|---|---|---|---|
| `CormorantGaramond-Medium-vi.woff2` | Cormorant Garamond (Cormorant Project Authors) | 500 | tiêu đề, số lớn |
| `BeVietnamPro-Regular-vi.woff2` | Be Vietnam Pro (Be Vietnam Pro Project Authors) | 400 | bảng, chữ nhỏ |
| `BeVietnamPro-SemiBold-vi.woff2` | Be Vietnam Pro | 600 | nhãn viết hoa giãn chữ |

Giấy phép **SIL Open Font License 1.1** (`OFL-*.txt`, nguyên văn của tác giả). Cả hai phông KHÔNG khai "Reserved Font Name" nên cắt tập ký tự / chọn một độ đậm được phép; giữ nguyên thông báo bản quyền trong file `OFL-*.txt`.
Nguồn: github.com/google/fonts (`ofl/cormorantgaramond`, `ofl/bevietnampro`), lấy 15/11/2026. Không tải lúc chạy — `editorial.font_css()` đọc các file này rồi nhúng dạng data URL vào trang.

Dựng lại (cần `fonttools` + `brotli`, chỉ lúc đổi phông):

```
fonttools varLib.instancer CormorantGaramond[wght].ttf wght=500 -o Corm500.ttf
U="U+0020-007E,U+00A0-00FF,U+0102-0103,U+0110-0111,U+0128-0129,U+0168-0169,U+01A0-01A1,U+01AF-01B0,U+0300-0301,U+0303,U+0309,U+0323,U+1EA0-1EF9,U+2013-2014,U+2018-201D,U+2022,U+2026,U+20AB,U+2212,U+00B2"
pyftsubset Corm500.ttf --unicodes="$U" --layout-features="kern,liga,lnum,tnum,locl,ccmp,mark,mkmk,calt,ss01" --flavor=woff2 --output-file=CormorantGaramond-Medium-vi.woff2
```
(Be Vietnam Pro: cùng lệnh `pyftsubset` trên `BeVietnamPro-Regular.ttf` / `-SemiBold.ttf`, không cần instancer.) Tập ký tự: Latin cơ bản + Latin-1 + toàn bộ ký tự tiếng Việt (U+1EA0–1EF9, Ă Đ Ơ Ư, dấu kết hợp), gạch ngang, nháy cong, `₫`, `²`.
