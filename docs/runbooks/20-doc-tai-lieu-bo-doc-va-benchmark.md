# 20 · Đọc tài liệu / OCR: cấu hình bộ đọc theo công ty, kiểm soát chi phí, chạy benchmark

Năng lực lõi `barista/docread/` (ADR 0232). Người dùng cuối không cấu hình gì: tải file như trước. Runbook này cho **nhà vận hành** và **người phát triển**.
Soạn: Claude Sonnet (Anthropic), 16/11/2026.

## 1. Cách hoạt động (một đoạn)

`docread.read(principal, tên_file, dữ_liệu, purpose="kb")` trả `Doc` (trang, chữ markdown, bảng có tiêu đề, ảnh, phương pháp từng trang, thông báo, token / chi phí). Bộ MIỄN PHÍ đọc trước (`office`: Word có bảng, Excel, PowerPoint, chữ ·
`pypdf`: PDF có chữ + bảng bằng code) và gắn cờ trang cần giúp (`scanned` = không có lớp chữ; `table_ragged` = bảng lệch cột). Bộ TỐN TIỀN (`model`) chỉ nhận đúng các trang đó, trong trần của hồ sơ. Không đọc được bằng model
(công tắc tắt, chạm trần, hết hạn mức, AI tắt, lỗi) ⇒ trang giữ kết quả miễn phí và `Doc.notices` nói lý do (kho tài liệu hiện câu đó cho người tải). Kết quả có tốn model được nhớ theo (băm file, cấu hình): cùng file + cùng cấu hình không trả tiền hai lần.

## 2. Hồ sơ đọc (dữ liệu, không phải code)

Lớp chồng (sau thắng): mặc định trong mã → `BARISTA_DOCREAD` của bản cài → dòng công ty mục đích `*` → dòng công ty đúng mục đích.

| Khoá | Ý nghĩa | Mặc định |
|---|---|---|
| `chain` | bộ đọc theo thứ tự: `office`, `pypdf`, `model` (+ bộ ngoài đã đăng ký). Bỏ `model` = KHÔNG BAO GIỜ tốn model | `office, pypdf, model` |
| `model` | `nhà_cung_cấp:tên_model` cho mục đích này; trống = model mặc định của bản cài. Khoá theo nhà cung cấp (`llm_keys`, như hạng trợ lý AI) | trống |
| `scanned_pdf` | model đọc trang PDF scan / chụp | bật |
| `tables` | `off` (pypdf trần) · `text` (bảng bằng code, miễn phí) · `auto` (bằng code; bảng lệch cột thì nhờ model) · `model` (mọi trang có bảng nhờ model) | `auto` |
| `images_ocr` | model chép CHỮ trong ảnh (hoá đơn, biên lai, ảnh chụp trang hợp đồng) | bật |
| `images_caption` | model MÔ TẢ ảnh để tìm được ("phối cảnh hồ bơi") | **tắt** |
| `max_model_pages` | trần trang / ảnh gửi model mỗi tài liệu (0 = không trang nào) | 30 |
| `monthly_tokens` | hạn mức token đọc tài liệu cả công ty / tháng (đếm `core.ai_usage` loại `docread:*`, trừ token đọc cache); chạm ⇒ dừng model, giữ bản miễn phí, báo rõ | không đặt |

Mục đích đã biết: `kb` (kho tài liệu), `kb_images`, `brochure_images`, `invoice`, `credential`, `other`; mục đích khác dùng được (khoá chữ thường).

**Bản cài** (`barista-ops/instances/<tên>.json`, biến `BARISTA_DOCREAD`, JSON):

```json
{"default": {"max_model_pages": 20},
 "purposes": {"invoice": {"model": "google:gemini-3.8-flash", "images_caption": false, "max_model_pages": 3}}}
```

**Công ty** (chỉ nhà vận hành — tài nguyên `core.ai_policy`, quản trị công ty không thấy / sửa; mỗi lệnh ghi người + lý do vào nhật ký):

```bash
cd backend
uv run --no-sync python -m barista.ai_policy --tenant "Tên công ty" docread-show
uv run --no-sync python -m barista.ai_policy --tenant "Tên công ty" docread-set invoice \
    --model google:gemini-3.8-flash --images-ocr yes --max-pages 3 --monthly-tokens 2000000 --by "Tên người" --why "HĐ 11/2026"
uv run --no-sync python -m barista.ai_policy --tenant "Tên công ty" docread-set '*' --tables text --by "Tên" --why "tắt model cho bảng"
uv run --no-sync python -m barista.ai_policy --tenant "Tên công ty" docread-set kb --clear model,monthly_tokens --by "Tên" --why "về mặc định"
uv run --no-sync python -m barista.ai_policy --tenant "Tên công ty" docread-clear invoice --by "Tên" --why "…"
```

`docread-show` in hồ sơ ĐANG ÁP theo mục đích (và nguồn: mặc định / bản cài / công ty), cùng số lượt + token + USD của THÁNG NÀY theo mục đích. Hiệu lực trong ≤ 30 giây. Màn dùng AI của nhà vận hành (`core.ai_token_report`) thấy loại "Đọc tài liệu · <mục đích>".

### Công thức theo nhu cầu

- **Rẻ nhất:** `--chain office,pypdf` (không model; scan không đọc được, người tải nhận câu báo) hoặc chỉ tắt phần tốn: `--scanned no --images-ocr no`.
- **Mặc định cân bằng:** chuỗi mặc định — chữ miễn phí, model chỉ đọc trang scan + bảng lệch cột.
- **Chính xác tối đa cho bảng:** `--tables model` (mọi trang có bảng nhờ model; tốn hơn).
- **Hoá đơn:** `invoice` với model tốt + `--max-pages 3` + `--monthly-tokens`; không bật mô tả ảnh.
- Công ty chưa đồng ý gửi dữ liệu cho nhà cung cấp AI: tắt trợ lý AI của công ty (`ai_enabled`) — mọi lượt đọc model tự dừng, thông báo `ai_off`.

## 3. Ảnh

(a) **Lấy ảnh trong PDF / Word / PowerPoint**: `docread.extract_images(tên, dữ_liệu, limit=…)` — miễn phí, lọc ảnh rác, bỏ trùng băm (tờ giới thiệu dùng). (b) **Chép chữ** và (c) **mô tả**: `docread.read` với file ảnh, hoặc `docread.read_images(p, ảnh, purpose=…)` cho một loạt
(mỗi băm một lần). Prompt chỉ đòi phần công tắc đang bật. (d) Tạo ảnh không thuộc đây (ADR 0224 §8.4).

**Đơn vị nhúng (chuẩn bị embedding đa phương thức, ADR 0232):** `doc.units()` — chữ trang, bảng (tiêu đề + dòng), ảnh (`ref` = băm + `file_id` của bản đã lưu), âm thanh (chỗ để sẵn); khoá ổn định theo băm file. Nơi gọi lưu ảnh bằng `files.put` rồi điền `ImageRef.file_id`.

## 4. Benchmark

```bash
cd backend
# thử khung đo, không tốn tiền (model giả đọc đúng đáp án)
PYTHONPATH=src python -m barista.docread.bench --fake --detail
# chạy thật — TỐN token; thu hẹp bằng --configs / --fixtures. Khoá đọc từ file .env, không in ra
PYTHONPATH=src python -m barista.docread.bench --models google:gemini-3.8-flash,anthropic:claude-sonnet-5 \
    --key-file /đường/dẫn/.env --key-var TÊN_BIẾN_KHOÁ --out /tmp/docread-bench
```

Cấu hình tự dựng: `free` (pypdf trần) · `free+tables` · `auto@<model>` · `auto+caption@<model>` · `model-all@<model>` (model đọc MỌI trang PDF — mốc để so chi phí). Mỗi model có khoá riêng thì đặt `llm_keys` / biến môi trường chuẩn của nhà đó trước khi chạy.
Cột: sai từ / sai ký tự so với đáp án (0% = đúng hoàn toàn) · ô bảng (đúng từng ô theo vị trí) · đúng trang · đủ số (chuỗi bắt buộc như số hoá đơn, tổng tiền) · mô tả ảnh chạm từ khoá · token · USD (≈ VND theo `--vnd-rate`, giả định) · USD / ảnh ·
trang gửi model · giây / trang · thông báo (`model_error`, `ai_off`… — một cấu hình lỗi hiện ở đây, không bị lẫn thành số 0). USD = `—` khi model chưa có trong bảng giá (không đoán). Tài liệu thật của khách KHÔNG đưa vào repo: để một thư mục ở máy (file + `truth.json` cùng định dạng với `docread/fixtures/truth.json`: mỗi trang là danh sách khối câu chữ / `{"table": {header, rows}}`, `facts`, `markers`) rồi chạy `--dir thư_mục`.

Dựng lại bộ tài liệu giả (cần Google Chrome): `PYTHONPATH=src python -m barista.docread.fixtures.make`.

### Số đo đã có (16/11/2026, `gemini-3.8-flash`, bộ giả, một lần chạy)

`free` sai từ 40,9% / ô bảng 40% · `free+tables` ô bảng 80% · `auto` sai từ 0,2% / ô bảng 100% / đúng trang 100%, 4 trang → model, ≈ 0,0090 USD · `model-all` cùng độ chính xác, 7 trang → model, ≈ 0,0205 USD. Hoá đơn ≈ 0,0025 USD / ảnh.
Chỉ là mốc đầu — xem ADR 0232 mục "Số đo" để biết giới hạn.

## 5. Thêm một bộ đọc (dịch vụ ngoài)

Lớp con `docread.ExternalReader` (`name`, `kinds`, `async read(req, doc)` điền `doc.pages`, cộng `doc.usage`), gọi `docread.register(MyReader())` lúc khởi động (module của gói / bản cài), thêm tên vào `chain`. `paid = True` ⇒ chịu cùng trần trang / hạn mức như `model`.
Dữ liệu công ty đi ra ngoài ⇒ chỉ bật cho công ty đã đồng ý (như nhà cung cấp AI thứ hai — ADR 0193). Hiện chưa có bộ ngoài nào đi kèm.

## 6. Sự cố

| Triệu chứng | Nguyên nhân thường gặp | Làm gì |
|---|---|---|
| Người tải PDF scan nhận "đã tắt trợ lý AI" | công ty tắt AI | bật lại khi khách đồng ý, hoặc tải bản PDF có chữ |
| "… hết hạn mức token đọc tài liệu" | chạm `monthly_tokens` | `docread-show` xem đã dùng; nâng trần hoặc chờ đầu tháng |
| "… trần mỗi tài liệu là N trang" | `max_model_pages` | nâng trần nếu hợp lý; hoặc tách file |
| "chưa cấu hình trợ lý AI / model chưa có khoá" | model của hồ sơ thuộc nhà cung cấp chưa có khoá (`llm_keys`) | thêm khoá nhà đó hoặc bỏ `model` khỏi hồ sơ (dùng model bản cài) |
| Bảng ra lệch cột (`ragged_table`) | PDF khác kiểu (ô gộp, bảng nghiêng) | đặt `--tables auto` (nhờ model) hoặc `model`; báo để bổ sung tài liệu mẫu vào bộ benchmark |
| Muốn đọc lại một file bằng model khác | bộ nhớ theo (băm, cấu hình) | đổi `model` trong hồ sơ ⇒ tự đọc lại; trong mã: `read(..., refresh=True)` |

## 7. Nhúng (tìm theo nghĩa): model THEO CÔNG TY (ADR 0232, mục "Nhúng theo công ty")

Soạn: Claude Sonnet (Anthropic), 16/11/2026. Chủ sản phẩm duyệt 16/11: model nhúng cấu hình được theo từng công ty (do nhà vận hành đặt, người dùng cuối không thấy) — khách thấy Gemini đắt thì chuyển sang model mở tự dựng (EmbeddingGemma…) mà tìm kiếm vẫn chạy.

### 7.1 Hồ sơ nhúng (dữ liệu: mặc định bản cài → dòng công ty ở `core.embed_profiles`)

| Khoá | Ý nghĩa | Mặc định |
|---|---|---|
| `model` | `nhà:tên` — `google:gemini-embedding-001`, `openai:text-embedding-3-small`, hoặc **`openai-compatible:<tên>`** (Ollama / vLLM / TEI trên máy của mình, kèm `base_url`). Rỗng = tắt tìm theo nghĩa cho công ty | `settings.embed_model` |
| `dims` | số chiều LƯU. **Chuẩn 768** (chỉ mục HNSW dựng sẵn). Số khác ⇒ mục 7.4 | `settings.embed_dims` |
| `base_url`, `key_ref` | địa chỉ máy chủ tương thích OpenAI; `key_ref` là TÊN mục trong `llm_keys` của bản cài (khoá không bao giờ vào DB; máy cục bộ thường không cần) | — |
| `truncate` | model trả nhiều chiều hơn `dims` ⇒ cắt phía mình rồi chuẩn hoá. **Chỉ cho model huấn luyện kiểu Matryoshka** (EmbeddingGemma, nomic…); cắt model khác làm vector hỏng mà không báo lỗi. Máy tự dựng KHÔNG nhận tham số `dimensions` (vLLM từ chối model không hỗ trợ) — Gemini / OpenAI thì nhận qua API | không |
| `query_prefix`, `document_prefix` | tiền tố chữ model đòi (EmbeddingGemma `task: search result \| query: ` / `title: none \| text: `, nomic `search_query: ` / `search_document: `, e5 `query: ` / `passage: `) — lệnh `embed-set` điền sẵn cho model quen thuộc (`embed_profile.PRESETS`) | — |
| `usd_per_mtok` | giá nếu nhà thứ ba tương thích OpenAI (máy nhà: 0 USD) | 0 cho máy tự dựng |
| `min_sim` | ngưỡng giống của nhánh nghĩa. **0,65 đo cho Gemini — mỗi model một phân bố điểm: đo lại bằng bench rồi đặt** | `kb_vector_min_sim` |

Mặc định bản cài đặt bằng `BARISTA_EMBED_MODEL` / `BARISTA_EMBED_DIMS` / `BARISTA_EMBED_PROFILE` (JSON: `base_url`, `key_ref`, `truncate`, tiền tố…). Khoá máy tự dựng: `llm_keys: {"may-nhung": {"api_key_file": "...", "api_key_var": "..."}}`, hồ sơ trỏ `key_ref: may-nhung`.

### 7.2 Đổi model cho MỘT công ty (chỉ nhà vận hành; ghi người + lý do)

```
python -m barista.ai_policy --tenant "Tên" embed-show                       # hồ sơ đang áp, tiến độ nhúng, token / chi phí tháng này theo model
python -m barista.ai_policy --tenant "Tên" embed-set --model openai-compatible:embeddinggemma \
    --base-url http://127.0.0.1:11434/v1 --dry-run                           # CHẠY THỬ: gọi thử máy chủ, kiểm số chiều + chỉ mục, ước chi phí / thời gian
python -m barista.ai_policy --tenant "Tên" embed-set --model ... --by "Tên" --why "khách thấy Gemini đắt"   # áp thật
python -m barista.ai_policy --tenant "Tên" embed-set --rollback --by ... --why ...   # quay lại hồ sơ ngay trước lần đổi model
python -m barista.ai_policy --tenant "Tên" embed-clear --by ... --why ...             # về mặc định bản cài; `embed-set --off` = công ty chỉ tìm từ khoá
```

`--dry-run` in: kết quả gọi thử (số chiều model thật sự trả, giây / lượt), số đoạn phải nhúng lại, token ước (~3 ký tự / token), chi phí ước (bảng giá công khai; máy tự dựng 0 USD), thời gian ước (số lô × giây / lô ÷ `embed_concurrency`). **Từ chối** (không ghi gì) khi: máy chủ không trả lời / khoá sai; model trả số chiều khác hồ sơ mà chưa khai `truncate`; pgvector chưa có chỉ mục cho số chiều đó.

**Điều gì xảy ra sau khi đổi** (một mô hình, không trộn):
1. Câu tìm của công ty đó chỉ so vector có `embed_model` + `embed_dims` = hồ sơ MỚI. Lúc đầu chưa đoạn nào có ⇒ chỉ tìm từ khoá (kết quả nói rõ lý do); đoạn đã nhúng model mới dần xuất hiện trong kết quả. Công ty khác không bị đụng.
2. Vòng quét `kb.embed_sweep` (mỗi phút, mọi tiến trình) thấy công ty còn đoạn khác model ⇒ nhúng nền, tôn trọng `embed_concurrency`, công tắc AI và hạn mức của công ty. Muốn chạy ngay: `python -m barista.modules.kb.vectors backfill --tenant <id>`. Tiến độ: `embed-show` hoặc `vectors status` (đã nhúng / tổng, %, lỗi, đoạn còn mang vector model khác).
3. **Vector cũ:** mỗi đoạn giữ MỘT vector — vector cũ của một đoạn chỉ mất khi vector mới của CHÍNH đoạn đó đã ghi (không giữ hai bản). Quyết định: không dựng bảng vector thứ hai (giảm bảo trì, không nhân đôi dung lượng / chỉ mục).
   Hệ quả cho **quay lại**: `embed-set --rollback` ngay lập tức dùng lại vector cũ của các đoạn CHƯA bị thay; các đoạn đã nhúng model mới phải nhúng lại bằng model cũ (tốn đúng phần đã chuyển). Muốn quay lại không tốn gì: quyết định trước khi vòng quét chạy xong, hoặc chạy thử trên công ty nhỏ trước.
4. Đoạn nhúng hỏng (lỗi model cũ) được cho thử lại ngay khi đổi hồ sơ (`kb.embed_requeue`).

### 7.3 Đo lượt dùng

Mỗi lượt nhúng ghi MỘT dòng `core.ai_usage` (`surface=kb`, `category=kb:embed` / `kb:embed_query`, cột `model`) ⇒ theo công ty × model. Máy tự dựng: **0 USD** (không phải NULL = "chưa có giá") nhưng vẫn đếm lượt + token (token là ƯỚC TÍNH ~3 ký tự / token khi máy chủ không trả số thật). Thấy ở `embed-show` và bảng "Nhúng theo model" trong báo cáo token của nhà vận hành (`ai_policy report`).

### 7.4 Số chiều khác 768

Cột `kb.chunks.embedding` là `vector` không cố định số chiều; chỉ mục HNSW là biểu thức một phần `WHERE embed_dims = N`. Số chiều chưa có chỉ mục ⇒ trên pgvector hệ thống TỪ CHỐI (tìm từ khoá + lý do, không nhúng) thay vì quét tuần tự âm thầm; DB không pgvector (nhánh mảng) dùng mọi số chiều ≤ 4.000. Dựng chỉ mục khi (và chỉ khi) một công ty cần:

```
python -m barista.modules.kb.vectors add-index --dims 1536     # ≤ 2.000: vector; 2.001–4.000: halfvec (độ chính xác nửa); CONCURRENTLY, chạy lại không sao
```

Chỉ mục cho phép nằm trong migration của bản cài nếu muốn lặp lại được (cùng câu `CREATE INDEX … USING hnsw ((embedding::vector(N)) vector_cosine_ops) WHERE embed_dims = N`). Không dựng "phòng xa". Lên 1.536 / 3.072 chỉ khi bench cho thấy đáng (mục 7.5).

### 7.5 Benchmark truy hồi — quyết định bằng số

```
python -m barista.docread.bench --retrieval --fake                       # thử khung đo offline (model băm từ — KHÔNG phải chất lượng)
python -m barista.docread.bench --retrieval --detail --key-file <.env> --key-var <BIẾN KHOÁ> \
    --embed-configs google:gemini-embedding-001@768,google:gemini-embedding-001@1536,openai-compatible:embeddinggemma@768@http://127.0.0.1:11434/v1
```

Bộ `docread/fixtures/retrieval_vi.json`: 70 đoạn + 44 câu hỏi tiếng Việt GIẢ (đồng nghĩa, nói thường ngày, không dấu, viết tắt; 30 đoạn gây nhiễu cùng chủ đề). In recall@5, recall@10, MRR@10, token, chi phí, giây nhúng bộ, ms / câu hỏi; cấu hình không chạy được ghi lý do mà không chặn các cấu hình khác. Bộ thật của khách để ở máy: `--dir` có `retrieval.json` cùng định dạng.

**Số đo thật (16/11/2026, một máy ở Việt Nam, 70 đoạn × 44 câu, `gemini-embedding-001`; 768 chạy 4 lần, 1536 chạy 3 lần (một lần lỗi tạm của nhà cung cấp, chạy lại được), 3072 chạy 1 lần — kết quả chất lượng giống nhau mọi lần):**

| cấu hình | recall@5 | recall@10 | MRR@10 | token (ƯỚC) | USD | giây nhúng bộ | ms / câu hỏi |
|---|---|---|---|---|---|---|---|
| Gemini 768 | 1,000 | 1,000 | 0,989 | 2.845 | 0,00043 | 2,5–3,0 | 350–365 |
| Gemini 1536 | 1,000 | 1,000 | 0,989 | 2.845 | 0,00043 | 1,5–2,2 | 340–510 |
| Gemini 3072 | 1,000 | 1,000 | 0,989 | 2.845 | 0,00043 | 1,5 | 375 |
| EmbeddingGemma / máy tự dựng | chưa đo — không có máy chủ nào chạm tới được lúc chạy (Ollama chưa cài trên máy dev) | | | | | | |

Đọc số này đúng: bộ nhỏ và BÃO HOÀ — Gemini đạt trần ở mọi số chiều (một câu xếp hạng 2), nên **không phân biệt được 768 với 1536 / 3072**; giữ 768 (không có bằng chứng để trả thêm lưu trữ + chỉ mục). Nó cũng chưa đủ khó để so Gemini với model mở: cần bộ lớn hơn / khó hơn (hoặc bộ thật của khách) trước khi kết luận chuyển model có giữ chất lượng. Giá thật của Gemini cho cả bộ ~0,0004 USD — kho tài liệu nhỏ thì tiền nhúng gần như không đáng kể; lý do chuyển model là khối lượng lớn, độ trễ cục bộ, hoặc dữ liệu không được ra ngoài.

### 7.6 Sự cố nhúng

| Triệu chứng | Làm gì |
|---|---|
| Tìm chỉ ra từ khoá sau khi đổi model | đúng thiết kế trong lúc nhúng lại — xem tiến độ `embed-show`; chưa chạy thì kiểm vòng quét / `backfill` |
| "chưa có chỉ mục cho N chiều" | `vectors add-index --dims N` (hoặc đặt lại `dims` 768) |
| "model embedding trả N chiều, hồ sơ cần M" | model Matryoshka ⇒ `--truncate yes`; không ⇒ `--dims N` + chỉ mục |
| Máy tự dựng chậm lần đầu (nạp model) ⇒ "quá hạn" | tăng `embed_timeout_s` / khởi động ấm máy chủ; đoạn hỏng tự thử lại sau 1 giờ (hoặc `embed_requeue` khi đổi hồ sơ) |
| Kết quả nghĩa kém sau khi đổi sang model mở | đặt tiền tố đúng model; đo lại `min_sim` bằng bench (ngưỡng 0,65 là của Gemini) |
