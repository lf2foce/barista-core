# 21 · Gói của đối tác: tạo, thử, tự kiểm, gửi lên bản cài

Dành cho đội ngoài bên vận hành Barista muốn viết gói của riêng mình: đối tác cùng xây lõi hoặc một công ty khác (ADR 0242). Gói nằm trong **repo
của bạn**. Bạn tạo nhánh, đánh tag và thử thoải mái ở đó. Gói ở lại repo của bạn, không cần gửi vào repo gói chính thức của Barista. Khi
gói lên một bản cài, bên vận hành review mã rồi ghim đúng commit bạn gửi, như với mọi mã chạy trong lõi.

Hợp đồng đầy đủ của một gói (mục phải khai, tiền, quyền, điểm cắm): runbook 12. Gói nối Barista với một hệ ngoài (CRM, WordPress, CMS, tổng đài…): runbook 22 — dùng khung kết nối chung của lõi, tạo bằng `pack.sh new … --connector`. Runbook này là đường đi từ lúc tạo gói tới lúc gói lên bản cài.

## Vì sao có bước review mã

Gói chạy CHUNG tiến trình với lõi và có cùng kết nối DB. Các luật (một cửa executor, RLS, không SQL sang schema khác, chỉ import `barista.api`) được giữ
bằng test và quét mã, không có hộp cát nào cô lập gói. Vì vậy mọi mã chạy trong lõi đều qua review trước khi lên bản cài: mã của lõi, gói chính thức
và gói của đối tác. `pack.sh check` ĐẠT là điều kiện để bắt đầu review. Những luật máy đọc được đã đúng, nên người review chỉ còn đọc phần nghiệp vụ.

Không muốn chạy mã trong lõi (chỉ cần đọc / ghi dữ liệu Barista từ hệ thống của bạn)? Dùng **app ngoài**: hệ thống của bạn gọi API bằng token ứng dụng
(ADR 0140). Quyền theo vai của app, quản trị công ty tự cài và gỡ app, không cần bước review mã. Đổi lại, app chỉ chạy được các lệnh mở cho app (`apps=True`) và không
thêm bảng hay màn vào Barista được.

## 1. Chuẩn bị máy

Clone `barista-core`, cài Postgres local, `uv`, Python 3.14 (runbook 01), rồi `cd backend && uv sync --frozen`. Bộ test của lõi chạy được là đủ.

## 2. Tạo gói

Từ repo lõi:

```bash
bash scripts/pack.sh new kho_lanh ../goi-cua-toi/kho_lanh --title "Kho lạnh"
```

- **Khoá** (`kho_lanh`) đồng thời là tên module và tên schema. Đặt theo NGHIỆP VỤ, không theo tên khách. Khoá trùng module / schema của lõi bị từ chối.
- Một repo của bạn có thể chứa nhiều gói, mỗi gói một thư mục (như repo gói chính thức).
- Khuôn sinh ra một gói chạy được ngay: `Pack` + module + một lệnh + một truy vấn + màn, migration 001 có RLS, test ở ranh giới (người không có quyền
  bị từ chối, công ty khác không thấy dữ liệu, mã chạy chỉ dùng `barista.api`), README, `.gitignore`.
- `requires-core` được đặt theo `barista.api` của lõi lúc sinh gói, ví dụ `">=1.63,<2"`.

## 3. Thử trên máy

```bash
uv pip install --python backend/.venv/bin/python --no-deps -e ../goi-cua-toi/kho_lanh   # --python bắt buộc: cài vào môi trường của LÕI
cd backend && .venv/bin/python -m pytest ../../goi-cua-toi/kho_lanh/tests               # test của gói
```

Chạy lõi trên máy (runbook 01) thì gói tự nạp. Bật gói cho công ty thử ở Gói & ứng dụng. Gỡ gói: `uv pip uninstall --python backend/.venv/bin/python
barista-pack-kho-lanh`. Lõi chạy tiếp như cũ, dữ liệu của gói vẫn nằm trong DB.

## 4. Tự kiểm trước khi gửi

```bash
bash scripts/pack.sh check ../goi-cua-toi/kho_lanh               # commit HEAD của gói (file chưa commit KHÔNG được kiểm)
bash scripts/pack.sh check ../goi-cua-toi/kho_lanh --worktree    # cả thay đổi chưa commit (khi đang làm)
bash scripts/pack.sh check ../goi-cua-toi/kho_lanh --core-ref origin/main   # trên lõi mới nhất thay cho HEAD đang checkout
bash scripts/pack.sh check ../goi-cua-toi/kho_lanh --with ../goi-khac/abc   # gói khác mà test của gói này cần
```

Lệnh không sửa gì ở máy bạn: nó dựng lõi sạch (`git archive`) trong thư mục tạm, chép gói vào đó rồi chạy bốn bước:

| Bước | Kiểm |
|---|---|
| 1. Đọc tĩnh | `pyproject.toml`: tên `barista-pack-*`, version `X.Y.Z`, đúng MỘT điểm vào `barista.packs`, `requires-core` chứa `barista.api` của lõi, có `-p tests.conftest`; `Pack("<khoá>", tên, mô tả, kind=…)` viết bằng chuỗi chữ; migration nằm trong `migrations/<khoá>/` và dòng đầu là `-- module: …`; có `tests/test_*.py` |
| 2. Nạp | cài gói vào môi trường lõi sạch, nạp bằng loader của lõi, import mọi module con. Tên ngoài `barista.api`, hay `Pack` / `Module` khai sai, đều lỗi ở bước này |
| 3. Gói | ruff, pyright, pytest của gói |
| 4. Luật của lõi | các test cấu trúc của lõi (`backend/tests/pack_guards.txt`) chạy lại với gói đã cài: không SQL sang schema khác, gọi gói khác đúng cách, múi giờ, mã người, hợp đồng quyền hệ thống, chỉ import bề mặt công bố |

Kết quả cuối là `ĐẠT` (thoát 0) hoặc `KHÔNG ĐẠT` (thoát 1, kèm từng lỗi). Gói khuôn ĐẠT trong khoảng 75 giây trên máy 8 GB. Ngoại lệ có lý do của một gói
(vd một truy vấn mở đầu bằng động từ ghi) khai ở `<package>/core_guards.json`, mỗi dòng kèm lý do (README của repo gói chính thức, mục test cấu trúc).

## 5. Gửi lên bản cài

1. Đánh tag CÓ CHÚ THÍCH `<khoá>/vX.Y.Z` khớp `version` trong `pyproject.toml` (`git tag -a kho_lanh/v0.1.0 -m "…"`), đẩy lên repo của bạn. Không dời
   tag đã gửi, sửa thì ra số mới.
2. Gửi cho bên vận hành: đường repo, tag, commit, kết quả `pack.sh check`.
3. Bên vận hành review mã của đúng commit, chạy lại `pack.sh check`, rồi ghim commit đó vào file bản cài.
4. Mỗi bản mới lặp lại từ bước 1. Bản cài chỉ chạy commit đã ghim.

Phía bên vận hành (để bạn biết gói đi đường nào): repo của bạn được clone cạnh repo lõi trên máy phát hành; file bản cài khai gói kèm nguồn —
`"<khoá>": {"version": "vX.Y.Z", "commit": "<sha 40 ký tự>", "repo": "<tên thư mục repo của bạn>"}`. Máy phát hành lấy đúng commit đó từ tag
`<khoá>/vX.Y.Z` (tag phải có chú thích và đã đẩy lên `origin` của repo bạn), kiểm `requires-core`, chạy test của gói cùng lõi, rồi gửi kèm mã lõi.
Tag trỏ commit khác commit đã ghim thì lượt phát hành dừng. Gói của bạn và gói chính thức có thể cùng nằm trên một bản cài.

## Luật hay quên

- Mã chạy chỉ import `barista.api`. Thiếu thứ gì thì xin thêm vào lõi (mở issue / MR ở `barista-core`), không import `barista.kernel`.
- Migration đã chạy ở bất kỳ DB nào thì không sửa nữa: thay đổi viết vào file số tiếp theo. Lõi chặn khi checksum lệch.
- Không đưa tên khách, email hay số điện thoại thật, khoá API vào mã, test hay dữ liệu mẫu. Repo lõi có thể được công khai; gói của bạn sẽ được người
  khác đọc.
- Tiền không ghi thẳng vào sổ: đi qua các lệnh của `acct` (runbook 12, dòng "Tiền").
- `kind="company"`: gói riêng của MỘT công ty (không lên chợ gói). `industry` / `capability` theo runbook 12.
