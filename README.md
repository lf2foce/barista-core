# Barista

**An open-source core for running a business by modules ("packs"), with an AI assistant that works under the same permissions and the same single command door as every human user.**
One codebase; each company's configuration is data. Permissions are enforced in the database (PostgreSQL row-level security), money goes through a double-entry ledger,
and the assistant can only *propose* changes that a person approves.

Nền tảng vận hành doanh nghiệp theo gói, có trợ lý AI dùng cùng quyền và cửa thực thi với người dùng. Code dùng chung; cấu hình từng công ty là dữ liệu.

## Thử trong 10 phút / Quick start

Cần / Requirements: Python 3.14 + [uv](https://docs.astral.sh/uv/), PostgreSQL (thử với 18), Node + pnpm (cho web). App Mac (`macos/`) chỉ cần nếu muốn dùng Swift.

```bash
bash backend/scripts/dev-db.sh              # một lần: tạo role + DB dev, ghi backend/.env (kho tệp, khoá ký)
cd backend && uv sync
uv run python -m barista.seed --reset       # công ty mẫu (dữ liệu GIẢ): crm, directory, finance, hr, kpi, marketing, talk
BARISTA_DEV_AUTH=1 uv run uvicorn barista.main:app --port 8300   # API: http://127.0.0.1:8300/docs
cd ../web && pnpm install && pnpm dev       # giao diện: http://localhost:3100
```

Mở http://localhost:3100, chọn một vai ở màn đăng nhập dev (ví dụ `giamdoc@sanmau.demo`). Bạn sẽ thấy công ty mẫu với khách, việc, kế toán, nhân sự.

- **Trợ lý AI** cần khoá model (Gemini mặc định): đặt `BARISTA_LLM_API_KEY`, hoặc `BARISTA_LLM_API_KEY_FILE` + `BARISTA_LLM_API_KEY_VAR`. Không có khoá thì mọi thứ khác vẫn chạy.
- `BARISTA_DEV_AUTH=1` chỉ để thử trên máy mình; đăng nhập thật là Google OIDC ([runbook 09](docs/runbooks/09-dang-nhap-google.md)).
- Chi tiết, DB test, chạy bộ test, bản cài thứ hai: [runbook 01](docs/runbooks/01-chay-tren-may-dev.md).

## Đọc tiếp

- [Tổng quan hệ thống](docs/OVERVIEW.md): sản phẩm, các tầng và tình trạng triển khai.
- [Từ điển kiến trúc](docs/GLOSSARY.md): nguồn định nghĩa chung cho Pack, Workspace, Module, Screen, Tool, Skill và Workflow.
- [Kiến trúc](docs/ARCHITECTURE.md): ranh giới và hợp đồng kỹ thuật.
- [Runbook](docs/runbooks/README.md): cách chạy và vận hành.
- [Làm gói riêng](docs/runbooks/21-goi-cua-doi-tac.md) ([`SKILL.md`](SKILL.md)): luồng 3 bước `scripts/pack.sh new` → `scripts/pack.sh dev` → `scripts/pack.sh check`. Gói chỉ dùng `barista.api` và có thể nằm repo riêng với giấy phép riêng.
- [Quy tắc sửa repo](CLAUDE.md): các luật bất biến và kiểm tra trước khi hoàn tất. [Đóng góp](CONTRIBUTING.md).
- Các quyết định thiết kế (ADR) được giữ ở một repo riêng; mã và tài liệu ở đây chỉ ghi số ADR ("ADR 0045"), phần lý do đã tóm trong tài liệu kiến trúc.

## Hai loại từ điển

**Từ điển kiến trúc** ở [docs/GLOSSARY.md](docs/GLOSSARY.md) là nơi duy nhất định nghĩa thuật ngữ dùng trong code và thiết kế. Các ADR dẫn tới đó, chỉ bổ sung quyết định riêng.

**Từ điển nghiệp vụ / chỉ số** ở [docs/references/tu-dien-nghiep-vu.md](docs/references/tu-dien-nghiep-vu.md) phục vụ thống nhất nghĩa với khách: lead, doanh số, thực thu… Các định nghĩa này cần đối chiếu phiên bản code và xác nhận nghiệp vụ; không phải tên khái niệm kiến trúc. Tài liệu không tự thay đổi cách tính trong phần mềm.

## Trợ lý: công cụ và kỹ năng

Trợ lý nạp công cụ theo nhu cầu (0095), kỹ năng là dữ liệu của công ty (0201) và mỗi vai có bộ công cụ
mặc định (0218) — đều đã chạy. Ứng dụng AI của nhân viên (Claude, ChatGPT…) đọc qua MCP và soạn đề xuất chờ người bấm (0093, 0228).

## Giấy phép

Lõi Barista mã nguồn mở theo [Apache 2.0](LICENSE) — Copyright 2026 The Barista Authors ([NOTICE](NOTICE)).
Gói nghiệp vụ (pack) có repo và giấy phép riêng của bên làm gói. Tên "Barista": [TRADEMARKS.md](TRADEMARKS.md).
Đóng góp: [CONTRIBUTING.md](CONTRIBUTING.md).
