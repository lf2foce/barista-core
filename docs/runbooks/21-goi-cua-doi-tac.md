# 21 · Gói của đối tác: tạo, thử, tự kiểm, gửi lên bản cài

Dành cho đội ngoài bên vận hành Barista muốn viết gói của riêng mình: đối tác cùng xây lõi hoặc một công ty khác (ADR 0242). Gói nằm trong **repo
của bạn**. Bạn tạo nhánh, đánh tag và thử thoải mái ở đó. Gói ở lại repo của bạn, không cần gửi vào repo gói chính thức của Barista. Khi
gói lên một bản cài, bên vận hành review mã rồi ghim đúng commit bạn gửi, như với mọi mã chạy trong lõi.

Hợp đồng đầy đủ của một gói (mục phải khai, tiền, quyền, điểm cắm): runbook 12. Gói nối Barista với một hệ ngoài (CRM, WordPress, CMS, tổng đài…): runbook 22 — dùng khung kết nối chung của lõi, tạo bằng `pack.sh new … --connector`. Runbook này là đường đi từ lúc tạo gói tới lúc gói lên bản cài.

## Vì sao có bước review mã

Gói chạy CHUNG tiến trình với lõi và có cùng kết nối DB. Các luật (một cửa executor, RLS, không SQL sang schema khác, chỉ import `barista.api`) được giữ
bằng test và quét mã, không có hộp cát nào cô lập gói. Vì vậy mọi mã chạy trong lõi đều qua review trước khi lên bản cài: mã của lõi, gói chính thức
và gói của đối tác. `pack.sh check` ĐẠT là điều kiện để bắt đầu review. Những luật máy đọc được đã đúng, nên người review chỉ còn đọc phần nghiệp vụ.

Gói khai trước nó cần quyền gì (mục "Quyền của gói" dưới), `pack.sh check` đọc mã và so với phần khai. Đây là kiểm tĩnh: nó bắt chỗ dùng vô ý và chỉ cho
người review chỗ cần soi, không ngăn được mã cố tình phá. Khi chợ gói mở cho bên ngoài, lớp cách ly (gói chạy tiến trình riêng) sẽ chặn theo đúng danh
sách quyền này (ADR 0247).

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

Chạy một lệnh từ repo lõi (cài gói editable vào `backend/.venv`, chạy migration mới trên DB dev **không xoá dữ liệu hiện có**, bật gói cho công ty mẫu, in màn + URL và khởi động API có tự nạp lại khi sửa mã gói):

```bash
bash scripts/pack.sh dev ../goi-cua-toi/kho_lanh
# Chỉ cài + chạy migration + bật gói (không mở uvicorn):
bash scripts/pack.sh dev ../goi-cua-toi/kho_lanh --no-serve
# Kèm gói khác mà gói này cần:
bash scripts/pack.sh dev ../goi-cua-toi/kho_lanh --with ../goi-khac/abc
```

Mở web ở cửa sổ khác (`cd web && pnpm dev` → `http://localhost:3100`), đăng nhập dev bằng tài khoản lệnh vừa in ra và bấm vào màn của gói.

Lưu ý tránh mất gói / mất dữ liệu dev:
- **Không** chạy lại `uv sync` sau khi đã cài gói (nó gỡ gói khỏi `backend/.venv`; nếu lỡ chạy thì chạy lại `pack.sh dev`).
- **Không** chạy `python -m barista.seed --reset` nếu muốn giữ dữ liệu dev hiện tại (`pack.sh dev` chỉ chạy migration mới và giữ nguyên DB).
- Chạy nhanh bộ test của gói: `cd backend && .venv/bin/python -m pytest ../../goi-cua-toi/kho_lanh/tests`.
- Gỡ gói khỏi môi trường lõi: `uv pip uninstall --python backend/.venv/bin/python barista-pack-kho-lanh` (lõi chạy tiếp như cũ, dữ liệu của gói vẫn nằm trong DB).

Hướng dẫn rút gọn 3 bước `new → dev → check` cho agent: [`SKILL.md`](../../SKILL.md).

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
| 1. Đọc tĩnh | `pyproject.toml`: tên `barista-pack-*`, version `X.Y.Z`, đúng MỘT điểm vào `barista.packs`, `requires-core` chứa `barista.api` của lõi, có `-p tests.conftest`; quyền khai khớp quyền mã dùng, mã chạy chỉ dùng thư viện cho phép (mục dưới); tên `barista.api` sắp bỏ (chú ý); `Pack("<khoá>", tên, mô tả, kind=…)` viết bằng chuỗi chữ; migration nằm trong `migrations/<khoá>/` và dòng đầu là `-- module: …`; có `tests/test_*.py` |
| 2. Nạp | cài gói vào môi trường lõi sạch, nạp bằng loader của lõi, import mọi module con. Tên ngoài `barista.api`, hay `Pack` / `Module` khai sai, đều lỗi ở bước này |
| 3. Gói | ruff, pyright, pytest của gói |
| 4. Luật của lõi | các test cấu trúc của lõi (`backend/tests/pack_guards.txt`) chạy lại với gói đã cài: không SQL sang schema khác, gọi gói khác đúng cách, múi giờ, mã người, hợp đồng quyền hệ thống, chỉ import bề mặt công bố |

Kết quả cuối là `ĐẠT` (thoát 0) hoặc `KHÔNG ĐẠT` (thoát 1, kèm từng lỗi). Gói khuôn ĐẠT trong khoảng 75 giây trên máy 8 GB. Ngoại lệ có lý do của một gói
(vd một truy vấn mở đầu bằng động từ ghi) khai ở `<package>/core_guards.json`, mỗi dòng kèm lý do (README của repo gói chính thức, mục test cấu trúc).

## Quyền của gói

Khai trong `pyproject.toml`:

```toml
[tool.barista]
requires-core = ">=1.69,<2"
permissions = ["network"]   # [] nếu không cần quyền nào
```

| Quyền | Mở phần nào của `barista.api` | Vì sao phải khai |
|---|---|---|
| `ai` | `ai`, `docread` | tốn hạn mức AI của công ty, gửi dữ liệu cho nhà cung cấp model |
| `network` | `connectors`, `inbound` | gửi dữ liệu ra hệ ngoài, giữ khoá của hệ ngoài (runbook 22) |
| `files` | `files`, `convert` | lưu / đọc / đổi tệp ở sổ tệp của lõi |
| `notify` | `notify` | gửi thông báo tới người dùng |
| `public` | `links`, `web`, `Module.route` | mở dữ liệu ra ngoài: link công khai, cửa HTTP riêng |
| `client_companies` | `tenancy` | chạy lệnh / truy vấn trong công ty khách của công ty dịch vụ |

- Phần còn lại của `barista.api` (màn, lệnh, truy vấn, quy định, pháp nhân, sổ cái chỉ khai tài khoản…) không cần khai.
- Họ thao tác hệ thống gói gọi (`m.consumes("ledger_read")`…) đã khai trong mã; `pack.sh check` in ra cho người review, không khai lại ở đây.
- Mã dùng quyền chưa khai ⇒ lỗi. Khai mà không dùng ⇒ chú ý (bỏ đi cho người review khỏi soi thừa).
- Mã chạy (`src/`) chỉ import `barista.api`, gói khác nó cần, thư viện chuẩn cho chữ / số / ngày / cấu trúc dữ liệu, `pydantic` và `Jsonb` / mã lỗi của
  `psycopg` (danh sách đủ: `IMPORTS_OK` trong `scripts/packkit/packkit.py`). Không quyền nào mở `os`, `subprocess`, `socket`, `urllib.request`, `httpx`,
  kết nối DB riêng, `eval` / `exec`: mạng đi qua khung kết nối, tệp qua sổ tệp, DB qua executor. Test của gói (`tests/`) không bị giới hạn.

## Lõi đổi `barista.api` thế nào

- Chỉ thêm ⇒ tăng số giữa (`1.69` → `1.70`). Gói khai `requires-core = ">=1.69,<2"` chạy tiếp trên mọi bản `1.x` sau.
- Bỏ hay đổi tên một thứ: đánh dấu trước ở `backend/tests/api_surface.json` → `deprecated` (dùng gì thay, ngày sớm nhất bị bỏ). Tên vẫn chạy ít nhất
  **90 ngày** và chỉ bị bỏ ở **bản lớn kế tiếp** (`2.0.0`). `pack.sh check` báo chú ý khi gói còn dùng tên đã đánh dấu, kèm thứ thay.

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
- Dùng thêm phần nhạy cảm của `barista.api` (AI, mạng, tệp, thông báo, link công khai) ⇒ thêm quyền vào `permissions` cùng commit.
- Migration đã chạy ở bất kỳ DB nào thì không sửa nữa: thay đổi viết vào file số tiếp theo. Lõi chặn khi checksum lệch.
- Không đưa tên khách, email hay số điện thoại thật, khoá API vào mã, test hay dữ liệu mẫu. Repo lõi có thể được công khai; gói của bạn sẽ được người
  khác đọc.
- Tiền không ghi thẳng vào sổ: đi qua các lệnh của `acct` (runbook 12, dòng "Tiền").
- `kind="company"`: gói riêng của MỘT công ty (không lên chợ gói). `industry` / `capability` theo runbook 12.
