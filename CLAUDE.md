# Barista — quy tắc cho agent làm việc trên repo

> Người đóng góp từ ngoài: bỏ qua các đoạn nhắc `../barista-docs`, `../barista-packs`, `../barista-ops` — đó là các repo riêng của nhóm vận hành; mọi luật kỹ thuật bên dưới vẫn áp dụng đủ khi chỉ có repo này. Bắt đầu từ [README](README.md).

Thuật ngữ kiến trúc dùng chung: `docs/GLOSSARY.md` (dẫn từ README). Không định nghĩa lại Pack / Workspace / Module / Screen / Tool / Skill trong từng ADR; thuật ngữ mới hoặc đổi nghĩa thì cập nhật từ điển. Mục ghi “đề xuất” không phải tính năng đã có.

Lõi vận hành doanh nghiệp theo GÓI (lõi + năng lực dùng chung + gói ngành; cấu hình từng công ty là dữ liệu — 0056 / 0059). Tài liệu
riêng của khách KHÔNG nằm trong repo này (`docs/clients/` nếu có ở máy là lối tắt bị `.gitignore`), code và tài liệu ở đây KHÔNG nhắc tên khách.
**Người / agent trong team: trước khi làm đọc `../barista-docs/BAT-DAU.md` và luật ở đầu `../barista-docs/DIEU-PHOI.md`** (giữ chỗ số ADR / api / migration, ghi việc đang làm, khoá phát hành). Thông tin bản cài và khách cụ thể: `barista-docs/CLAUDE.md` (repo nội bộ). **Repo này CÔNG KHAI, mã nguồn mở Apache 2.0** (`LICENSE`, `NOTICE`):
commit đẩy lên — kể cả thông điệp commit — ai cũng đọc được ngay. Không đưa vào đây tên khách, email / số điện thoại thật, khoá API, dữ liệu khách,
script riêng một khách, ghi chú nội bộ hay tài liệu kinh doanh (chúng ở các repo riêng tư `barista-docs`, `barista-ops`). Người trong team: trước khi
đẩy main chạy `bash ../barista-ops/share/scan-core.sh HEAD` (quét tên khách / email thật / khoá) — phải ra "sạch". Greenfield: chỉ chuẩn
mới nhất, không lớp tương thích cũ. Kiến trúc lõi + lý do: `docs/ARCHITECTURE.md`.
**Bắt đầu từ `docs/OVERVIEW.md`.** Vì sao chọn cách này: ADR ở repo riêng `../barista-docs/decisions/` (mỗi quyết định một file — đọc trước khi đổi thứ
gì nền tảng; số giữ nguyên, mục lục `README.md` ở đó). Làm thế nào: `docs/runbooks/` (runbook vận hành bản cài — 11, 17 — ở `../barista-docs/runbooks/`).
Việc tiếp theo: lộ trình ở repo nội bộ (`../barista-docs/san-pham/ROADMAP.md`). Chốt quyết định mới ⇒ chép `../barista-docs/decisions/_template.md`, lấy số ở `../barista-docs/DIEU-PHOI.md`, commit ở
`barista-docs` cùng lúc đẩy code.

**Bố cục 4 repo (chốt 10/11/2026; ADR + ghi chú + runbook vận hành đã chuyển sang `barista-docs` ngày 16/11/2026):** ngang hàng trong `firstCC/`: `barista` (repo này: nhân lõi + `barista.api` + năng lực
chung, kể cả `pay` lương và `timekeeping` chấm công — CHIA SẺ đối tác) · `barista-packs` (gói ngành / chợ: mỗi gói một bản phân phối Python qua entry point `barista.packs`, chỉ import `barista.api`; hiện có
trade_finance, hosting, accounting_service, equity_research, edu, telephony, real_estate (module `catalog` / `deals` / `exec` / `allotments`), commission. Repo này KHÔNG còn gói ngành nào:
mã, migration, phần mẫu (hồ sơ `san-moi-gioi` / `san-moi-gioi-demo`) và test của Bất động sản + Hoa hồng ở `barista-packs` từ 12/11/2026 — `0191`) · `barista-docs` (ADR
`decisions/`, runbook nội bộ, `clients/<khách>/` — RIÊNG TƯ) · `barista-ops` (`instances/*.json` từng bản cài — RIÊNG TƯ). Repo này KHÔNG
chứa ADR, ghi chú nội bộ hay tên khách: `docs/decisions` và `docs/clients` (nếu có ở máy) chỉ là lối tắt cục bộ trỏ sang `../barista-docs/` (gitignore);
tài liệu và chú thích mã ở đây chỉ ghi SỐ ADR ("ADR 0045"), không link sang `barista-docs` (lõi công khai; người ngoài team không vào được repo đó); tên khách trong chữ viết "khách đầu tiên". Tên gói chấm công là `timekeeping` (không `time`, không `attendance` — trùng điểm danh của edu).

Cấu trúc: `backend/` (Python 3.14, uv, FastAPI, psycopg 3 — không ORM) · `macos/` (SwiftUI, SwiftPM — app "Barista Desk") · `web/` (Next.js 16, shadcn, Tailwind 4, pnpm — client thuần qua BFF, KHÔNG chạm DB: ADR 0047, đọc `web/CLAUDE.md`) · `ios/` (xcodegen, dùng chung mã `macos/Sources/BaristaDesk`; sửa mã chung ⇒ build cả Mac lẫn iOS — ADR 0041).

Chạy dev: `bash backend/scripts/dev-db.sh` (một lần; ghi thêm `backend/.env` thư mục kho tệp + khoá ký link — 0122, chạy lại nếu thiếu) → `cd backend && uv run python -m barista.seed --reset` (công ty mẫu TRUNG TÍNH của lõi) →
`BARISTA_DEV_AUTH=1 uv run uvicorn barista.main:app --port 8300` (đăng nhập dev mặc định tắt, chỉ nhận từ máy local; trợ lý cần
khoá model: `BARISTA_LLM_API_KEY` hoặc `BARISTA_LLM_API_KEY_FILE` + `BARISTA_LLM_API_KEY_VAR`) → `cd macos && swift run BaristaDesk`. Đăng nhập thật = Google OIDC tại core (cấu hình: `docs/runbooks/09-dang-nhap-google.md`).
**Sàn môi giới mẫu (demo BĐS + hoa hồng)** = cài hai gói vào môi trường lõi rồi chạy bằng `--no-sync` (`uv run` thường / `uv sync` GỠ gói — cài lại): từ gốc repo
`uv pip install --python backend/.venv/bin/python --no-deps -e ../barista-packs/real_estate -e ../barista-packs/commission` → `cd backend && uv run --no-sync python -m barista.seed --reset`
(công ty mẫu có thêm sàn mẫu; `--profile san-moi-gioi` / `--profile san-moi-gioi-demo` là hồ sơ của gói) → `BARISTA_DEV_AUTH=1 uv run --no-sync uvicorn barista.main:app --port 8300`. Gỡ: `uv sync`. Runbook 01.

Bất biến — sửa code mà phá một trong những điều này là sai, kể cả khi test xanh:
1. **Một cửa.** Mọi thao tác (API, nhập Excel, bên nghe sự kiện, lệnh sau duyệt, agent sau này) đi qua
   `kernel/executor.py` (`run` / `query` / `invoke`). Route không chạm DB nghiệp vụ.
2. **Quyền một nguồn, hai tầng.** `core.grants` (vai trò × tài nguyên → phạm vi own/unit/team/all + hành động) quyết định
   lệnh nào chạy được (executor) VÀ dòng nào thấy / ghi được (RLS qua biến phiên). Mọi bảng mới phải qua
   `core.secure_table` / `secure_child` / `secure_tenant` — `tests/test_meta.py` chặn bảng lọt. Báo cáo toàn công ty
   khai `min_scope="all"` (người phạm vi hẹp chạy sẽ ra số thiếu mà tưởng đủ).
3. **Ranh giới module.** Mỗi module một schema Postgres (mã: một file hoặc một package `modules/<khoá>/` — `0088`); SQL của module chỉ chạm schema của mình + `core.users /
   teams / team_members / memberships / legal_entities` (pháp nhân — 0099) + `core.embed_profiles` (chỉ vòng quét nhúng của `kb` ĐỌC để biết model nhúng của từng công ty — 0232) + `core.files / file_copy_grants` (sổ tệp — 0122: CHỈ qua `kernel/files.py` và khoá ngoại `file_id`; thân tệp ở kho đối tượng, không bytea trong bảng module). Cần dữ liệu module khác ⇒ `ctx.call` (quyền người gọi) hoặc `ctx.call_system`
   (chỉ thao tác `internal=True` — hợp đồng hẹp module kia công bố; đích khai `callers=` + `why=` (gọi bởi ai, quyền mở tới đâu, trả trường
   nào), trả đủ cho việc bên gọi làm, không trả dư rồi che ở bên gọi — `0083`, `tests/test_system_calls.py`). Mã người do người gọi nêu (`party_id`, `lead_id`…) không đi thẳng vào quyền hệ thống: `call_as`, hoặc kiểm bằng quyền họ trước và `why=` khai `[id: …]` (`0125`, `tests/test_internal_person_ids.py`). Phản ứng chéo ⇒ sự kiện outbox (`ctx.emit` + `@m.on`).
   Gói ngành mới dùng phần chung (khoản phải thu, quy trình, việc) qua `source / source_ref`; có BẢNG RIÊNG chỉ khi khác bản chất (vòng đời,
   ràng buộc, truy vấn cần chỉ mục, tiền) — không gộp bảng đang sống chỉ cho đồng bộ hình thức. Không code riêng cho một công ty (`0060`).
   Khoá ngoại không trỏ chéo module. Mỗi module thuộc một GÓI (`Module(pack=...)`, ADR 0045); gói TỰ KHAI bằng `Pack(...)`
   trong module đầu của gói, module tự khai màn (màn việc góp vào bàn việc bằng `desk=` — `0094`) / tài nguyên cấu hình — kernel, `workspaces.py`, `presets.py`, `agent/` không
   nhắc tên gói hay module nào (`0069`, `tests/test_dummy_pack.py` là thước đo). Gọi sang gói không
   bắt buộc ⇒ `ctx.call_optional` / `call_system_optional` / `call_as_optional` (gói tắt ⇒ None) — `tests/test_meta.py` chặn gọi thẳng.
4. **Tiền chỉ đi qua sổ cái** (`kernel/ledger.py`): ghi kép, tổng 0, chỉ thêm, sửa = bút toán đảo, khoá chống lặp. Bất biến nào do ai giữ, kiểm ở đâu, test nào: `docs/MONEY-INVARIANTS.md` (mỗi bất biến MỘT nơi giữ — nơi gọi không so lại).
   Không UPDATE số dư ở đâu cả — số dư là tổng dòng.
5. **Chính sách là dữ liệu.** Ma trận quyền, chuỗi duyệt, luật duyệt, chính sách hoa hồng (theo ngày hiệu lực), ánh xạ
   tài khoản MISA — quản trị sửa trong app; code chỉ kiểm hợp lệ. Không viết cứng tên người / vai trò vào code nghiệp vụ.
   Bộ MẶC ĐỊNH đi kèm gói (vai, quyền, đội, chuỗi duyệt, mẫu quy trình) khai ở `Pack(...)` của gói, `presets.py` ráp, cài qua MỘT lệnh `admin.install_defaults`
   (tạo công ty + bật gói), chỉ thêm phần thiếu — ADR 0059. Tiền vào: một giao dịch ngân hàng = một dòng `acct.statement_lines`,
   phiếu thu là phần phân bổ của dòng (ADR 0057).
   **Luật / nghị định** (hạn nộp, ngưỡng, mức) KHÔNG viết cứng trong module: bộ quy định có phiên bản `backend/src/barista/regulations/<bộ>/<ngày hiệu lực>.json`
   (`kernel/regulations.py`, ADR 0067, runbook 13) — văn bản mới = thêm một file, có căn cứ từng quy tắc.
6. **Người xin không tự duyệt**; lệnh sau duyệt chạy lại bằng quyền người xin (nạp lại) hoặc quyền hệ thống có ghi tên
   người duyệt — không có đường nâng quyền nào khác.
7. **App chỉ vẽ.** Manifest / trang / form do server mô tả (`workspaces.py`, `kernel/ui.py`, `kernel/forms.py`). Output app
   đọc chỉ được THÊM trường; đổi tên / bỏ ⇒ sửa app cùng commit và chạy `cd macos && swift run BaristaDesk --check-contract`.
8. **Trợ lý = người gọi thứ hai, không hơn người.** Agent chạy bằng `principal.as_agent()` qua đúng executor. Lệnh mới
   phải chọn có chủ đích tham số `agent=` (mặc định `propose`: thành thẻ đề xuất đã chạy thử; `allow` chỉ cho việc ghi
   rủi ro thấp; `deny` cho duyệt / đổi quyền của chính agent). Duyệt phiếu luôn do người. Số liệu cho người dùng nằm trên
   thẻ (kết quả truy vấn), không trong chữ model viết.
9. **Dữ liệu mẫu là giả** (`seed.py`): không tên tổ chức / người / SĐT thật (SĐT đầu 010). Công ty mẫu của lõi TRUNG TÍNH (thương mại – dịch vụ, hồ sơ
   `sample`); phần của một ngành do gói góp qua `barista.seed_samples` — `seed*.py` không nêu tên gói ngành (`0187`).
10. **Việc nhiều ngày: trạng thái ở `work.items`, DBOS chỉ giữ nhịp.** Điều kiện xong kiểm bằng code, bằng quyền người phụ trách
    (nạp lại mỗi lần). Đổi thứ tự / số bước của workflow `follow` ⇒ tăng `WORKFLOW_VERSION` (ADR 0026).
11. **Generic trước, migrate sau.** Barista là ERP dùng cho nhiều doanh nghiệp, không phải bản port hệ thống cũ của một khách.
    Thứ tự cố định: hiểu NGHIỆP VỤ → thiết kế theo best practice → mới map dữ liệu cũ. Cấu trúc bảng do mình quyết; tên bảng / cột
    của hệ thống cũ chỉ sống trong lớp migrate (`legacy_map`) và tài liệu mapping, không vào schema / code. Lý do "bên kia làm vậy"
    hoặc "để khớp dữ liệu đang có" không phải lý do hợp lệ cho một quyết định (ADR 0104 §0, §9).
12. **Dữ liệu nạp từ hệ cũ là tham chiếu.** Lớp nạp truyền `historical=True` ở mọi lời gọi ghi; vòng quét / bên nghe mới phải im trên bản ghi nhiều năm tuổi
    (`tests/test_historical_load.py`). DB vừa nạp chỉ lên bản cài sau khi `backend/scripts/check_quiet_after_load.py` in `IM` — `0185`, runbook 16.

12. **Nhập liệu tối thiểu — máy đoán, người xác nhận** (ADR 0225). Form mới KHÔNG có ô dán nội dung khi nội dung vốn nằm trong file (tải / thả file, máy đọc, file gốc để đối chiếu),
    KHÔNG bắt người dùng nghĩ ra mã / khoá (chọn từ danh sách có sẵn, hoặc máy đặt từ tên — `kernel/text.code_from_name`), KHÔNG hỏi thứ máy đoán được (tên từ nội dung, loại, kệ dùng gần nhất…).
    Giá trị máy đoán hiện sẵn dưới dạng chip sửa được + một nút chấp nhận tất cả; thứ đổi cách dùng dữ liệu (hiệu lực, thay bản cũ) là ĐỀ XUẤT, không tự áp. Ngày hiện cho người ở dạng không
    nhập nhằng ("1 tháng 12, 2026"); số d/m/y đọc theo quốc gia công ty (0169). Ngoại lệ có lý do: ghi chú / lý do ngắn là chữ do người gõ.

Kiểm tra trước khi xong — backend: `uv run ruff check src tests && uv run pyright && uv run pytest -q` (Postgres thật,
4 tiến trình song song, mỗi tiến trình một DB `<db>_gw0…` tự tạo — ~25 giây; tuần tự `-n0` ~40 giây; không đặt `BARISTA_TEST_*DATABASE_URL` ⇒ tên DB mang mã lần chạy, tự xoá khi xong — hai lần chạy cùng lúc không đụng nhau) (bộ test của repo này LÀ bộ test lõi — không gói ngành nào trong repo, `0187` / `0191`; test của gói ở `barista-packs/<gói>/tests`, chạy trong môi trường lõi: README của repo gói; test mới của lõi không dùng thao tác / dữ liệu của gói ngành; cơ chế lõi cần một gói ngành làm ví dụ ⇒ test ở `tests/standin/` qua gói thế vai `orderdesk` (`0190`). Test CẤU TRÚC đọc mã qua `tests/module_src.py` quét cả gói ngoài ĐANG CÀI — sau khi cài gói chạy lại `uv run --no-sync python -m pytest -q $(grep -v '^#' tests/pack_guards.txt)`; dòng cho phép của một gói do gói khai ở `<package>/core_guards.json`, không ghi vào test lõi (`0191`); máy 8 GB: chạy lần lượt, không cùng lúc); **trước khi đẩy main: `bash scripts/premerge.sh <bản cài>`** (BẮT BUỘC nêu bản cài; cây làm việc, môi trường tạm: lõi + gói GHIM của bản cài đó — `pack_guards.txt` + test từng gói, đúng các pha của release.sh; `--quick` chỉ test lõi liên quan ~vài phút, `--packs-main` gói ở main của barista-packs; có khoá phát hành ⇒ từ chối); trước phát hành đụng lõi / quyền: `bash backend/scripts/test_repeat.sh` một lần (3 lượt, bắt test chập chờn; hook pre-push nhanh: runbook 11); app: `swift build` + `--check-contract` khi đổi hợp đồng. Migration đã chạy KHÔNG sửa — tạo file mới `migrations/<gói>/NNN_<tên>.sql` trong thư mục của GÓI sở hữu module (số tăng dần trong gói;
hai gói cùng số không sao), dòng đầu `-- module: <khoá>`; cần dừng dịch vụ ⇒ thêm tên vào `migrations/<gói>/deployment.json` (bản gốc
`migrations/base/000_baseline/` — `0085`; sổ `public.barista_pack_migrations` khoá (gói, tên), sổ dãy số chung cũ tự ánh xạ — `0130`; lịch sử
chuỗi cũ: `docs/migrations-history.md`) (runner chặn checksum lệch).

Thêm gói: `docs/runbooks/12-them-mot-goi.md` (hợp đồng gói + nghiệm thu trước phát hành). Module của gói import từ `barista.api` (bề mặt
công bố có phiên bản semver — 0130), không từ `barista.kernel.*` / `agent` / `config` (`tests/test_pack_surface.py`; gói nền miễn, ngoại lệ có lý do
trong test). Thiếu thứ gói cần ⇒ thêm vào `barista.api` + `tests/api_surface.json` + tăng `__version__`. Gói nạp qua entry point `barista.packs` (`backend/pyproject.toml`, thứ tự do `kernel/loader.py` tính: tầng → `requires` → khoá — không
danh sách import tay; đổi danh sách ⇒ `uv sync`). HTTP của lõi ở `barista/http_api.py`. `deploy/` là MẪU triển khai chung của mọi bản cài
trên VPS (Docker Swarm + Dokploy; ảnh `barista-api` / `barista-web` dùng chung) — tên miền, project Dokploy, thư mục `/root/<tên>`, hồ sơ nạp mẫu là của BẢN CÀI, lấy từ file bản cài, lõi không ghi; file bản cài (`instances/*.json`, kể cả `"packs"`) CHỈ ở `barista-ops` — lõi không mang (0155). Lõi không nêu tên gói ngoài: dịch vụ lõi mở cho gói ngoài là họ thao tác `outreach` / `doc_read` / `paper_draft` / `receivable` / `remittance` / `follow_up` / `identity` / `consent` / `booking` / `work_log` / `call_log` / `settlement` / `placement` / `ledger_ref` / `structure` / `lead_conversion` / `company_figures` / `ledger_read` / `brand` (`registry.OPEN_FAMILIES`; gói khai `m.consumes`), `callers=` chỉ module của repo này và KHÔNG nêu module của gói ngành (0186; `tests/test_system_calls.py`). Đã rời lõi (repo `barista-packs`, tag `<khoá>/v<X.Y.Z>`): 10/11/2026 `trade_finance`, `hosting`, `accounting_service`, `equity_research`; 12/11/2026 `edu`, `telephony`, `real_estate`, `commission` (`0191`); 21/11/2026 `compose` (`0254` — sổ migration giữ nguyên khoá (gói, tên) + checksum nên DB đang chạy không chạy lại gì) (bản cài dùng chúng phải ghim ở `"packs"` của file bản cài — bản cài dùng hồ sơ `san-moi-gioi` ghim CẢ `real_estate` lẫn `commission`); phần mẫu của gói trong công ty mẫu qua điểm vào `barista.seed_samples` (`PROFILES` / `seed` / `finish` / `COMPANIES`), hồ sơ nạp mẫu qua `barista.seed_profiles` (vd `edu`, `edu-demo`, `bank`), bộ quy định riêng của gói đi cùng gói (`regulations.declare(…, root=)`).

Phát hành một bản cài = `INSTANCE=<tên> bash deploy/release.sh` (BẮT BUỘC nêu bản cài; test rồi gửi đúng commit
đã push; không gián đoạn, tự quay lui) — chỉ khi người dùng yêu cầu. Migration chỉ được THÊM (bản cũ còn chạy lúc chuyển). Runbook 11.

Commit: chờ người dùng yêu cầu rõ.
