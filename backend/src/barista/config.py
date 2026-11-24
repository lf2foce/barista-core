from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BARISTA_", env_file=".env", extra="ignore")

    # Chế độ của bản cài (ADR 0194): "" (máy dev) | "demo" | "production". production ⇒ barista/production.py chặn khởi động khi còn cờ dev / demo
    # hoặc thiếu đăng nhập thật, và seed không nạp mẫu. Bản cài: deploy/ops/deploy.py đặt theo "env" của file bản cài — không đặt tay.
    env: str = ""
    demo_today: str = ""   # đồng hồ demo (kernel/clock.py) — chỉ máy demo
    # Múi giờ MẶC ĐỊNH của bản cài (0169): công ty mới nhận múi này; mọi việc chạy ngoài phiên công ty (đăng nhập, vòng quét xuyên công ty) tính
    # theo múi này. Mỗi công ty đổi múi của mình ở Cài đặt (core.tenants.timezone). Tên IANA; không phải TZ của tiến trình (không còn ý nghĩa).
    default_timezone: str = "Asia/Ho_Chi_Minh"
    llm_replay_dir: str = ""   # lưu / phát lại lượt model đọc tài liệu (agent/llm.py) — chỉ máy demo, kịch bản demo ở repo của gói

    # barista_app: mọi request (KHÔNG BYPASSRLS). barista_owner: migration, đăng nhập, quét outbox xuyên công ty.
    database_url: str = "postgresql://barista_app:barista_app@localhost:5432/barista_erp_dev"
    owner_database_url: str = "postgresql://barista_owner:barista_owner@localhost:5432/barista_erp_dev"
    allow_app_temp: bool = False   # CHỈ dev/test nối app bằng owner: bỏ chốt "role app không có TEMP" (khi đó claims phiên 057 giả được)
    # Đăng nhập dev bằng header X-Dev-User (email) + X-Tenant-Id — MẶC ĐỊNH TẮT, bật bằng BARISTA_DEV_AUTH=1, và kể cả khi
    # bật cũng chỉ nhận request từ chính máy này. Bản thật thay bằng OIDC — xem ARCHITECTURE §7.
    dev_auth: bool = False
    # Bản demo trên VPS (dữ liệu giả): đăng nhập dev từ máy khác chỉ khi gửi đúng header X-Demo-Key. Trống ⇒ dev chỉ nhận từ máy chạy core.
    demo_key: str = ""
    # DEMO CÔNG KHAI (bản cài khai ``"gate": false`` — ADR 0194 bổ sung 17/11/2026: ai có link cũng chọn vai, kể cả quản trị ⇒ chỉ bản cài TOÀN dữ liệu giả):
    # đăng nhập dev nhận cả khi không có X-Demo-Key — app iPhone / Mac gọi thẳng API (không qua BFF giữ khoá) cũng thấy trang chọn vai như web. deploy.py
    # đặt theo file bản cài, không đặt tay; production từ chối khởi động khi bật (barista/production.py).
    demo_open: bool = False
    demo_company: str = ""   # tên công ty của bản cài demo (seed) — trống = "Sàn mẫu (dữ liệu giả)"; bản cài cho khách đặt tên khách
    # Gói MẶC ĐỊNH của bản cài (vd "crm,finance,marketing,talk"), cách nhau dấu phẩy — trống = mọi gói đều mặc định (máy dev). Mọi công
    # ty có quyền bật các gói này; gói khác vẫn bày ở chợ gói ("Liên hệ để mở"), nhà cung cấp cấp quyền khi khách trả tiền (python -m barista.entitle).
    packs: str = ""
    # Danh mục gói BÁN ĐƯỢC nhưng chưa cài trên bản này (0160): file JSON do `deploy/packs.py bundle` ghi vào tar phát hành (mặc định
    # `barista/pack_catalog.json` cạnh mã; không có file ⇒ chợ gói chỉ hiện gói đã cài). Đặt đường dẫn khác chỉ để thử.
    pack_catalog_file: str = ""
    # KHUNG KẾT NỐI (ADR 0244): khoá mã hoá khoá kết nối của công ty (AES-GCM, ≥ 32 ký tự). Trống ⇒ app không nhận khoá kết nối (nói rõ), phần khác chạy
    # như thường. Mất / đổi khoá này ⇒ khoá đã lưu không giải được (quản trị dán lại). deploy/ops/bootstrap.py sinh một lần cho bản cài.
    connector_key: str = ""
    connector_key_old: str = ""   # đổi khoá: khoá cũ để GIẢI khoá đã lưu (ghi luôn bằng khoá mới) — bỏ khi quản trị đã lưu lại mọi khoá
    public_api_url: str = "http://localhost:8300"   # địa chỉ công khai của core — MCP (0093): định danh OAuth issuer + tài nguyên /mcp
    mcp_allowed_hosts: str = ""
    # Ứng dụng AI được nối (0093): tên miền redirect cho phép, phẩy ngăn cách (vd "claude.ai,chatgpt.com"); rỗng = mọi nơi (dev)
    mcp_redirect_hosts: str = ""   # tên miền được gọi /mcp (chống DNS rebinding), vd "api.vi-du.test"; trống = không kiểm (máy dev)
    web_url: str = "http://localhost:3100"   # địa chỉ web khách mở link gửi khách (0064) — bản cài đặt BARISTA_WEB_URL theo tên miền của nó
    release: str = "dev"   # mã commit ảnh đang chạy (ảnh build đặt BARISTA_RELEASE) — /health trả để kiểm đúng bản sau deploy
    migrate_on_start: bool = True
    # Trợ lý: "provider:model" của Pydantic AI (đổi nhà cung cấp bằng một chuỗi). Khoá: llm_api_key, hoặc đọc một biến từ
    # file .env khác (llm_api_key_file + llm_api_key_var), hoặc biến môi trường chuẩn của provider (GOOGLE_API_KEY…).
    # Đọc tài liệu / OCR (ADR 0232, runbook 20): cấu hình MẶC ĐỊNH của bản cài (JSON): {"default": {...}, "purposes": {"invoice": {...}}} — mỗi mục là một
    # hồ sơ đọc (chuỗi bộ đọc, model, công tắc scan / bảng / ảnh, trần trang, hạn mức token tháng). Công ty ghi đè từng mục đích bằng
    # ``python -m barista.ai_policy docread-set`` (chỉ nhà vận hành). Trống = mặc định trong mã (chữ miễn phí; model chỉ đọc trang thật sự cần).
    docread: dict[str, Any] = {}
    llm_model: str = "google:gemini-3.8-flash"
    llm_api_key: str = ""
    llm_api_key_file: str = ""
    llm_api_key_var: str = ""
    # Độ bền khi gọi model (agent/reliability.py, runbook 15): lỗi TẠM (429, 5xx / quá tải, quá hạn, rớt kết nối) thử lại ở mức MỘT lần gọi model —
    # lùi theo cấp số nhân + nhiễu, theo Retry-After của nhà cung cấp; tổng số lần (kể cả lần đầu) và trần thời gian chờ có hạn. Không thử lại
    # lỗi khoá / cấu hình, nội dung bị bộ lọc an toàn chặn, yêu cầu sai.
    llm_retry_attempts: int = 3
    llm_retry_base_s: float = 0.5        # lần chờ đầu (giây); lần sau gấp đôi, có nhiễu ±25%
    llm_retry_max_delay_s: float = 8.0   # trần mỗi lần chờ
    llm_retry_after_max_s: float = 20.0  # nhà cung cấp bảo chờ lâu hơn ⇒ không chờ (chuyển dự phòng nếu có, không thì báo người)
    llm_retry_budget_s: float = 30.0     # đã tốn quá ngần này giây cho một lần gọi ⇒ thôi thử lại (người đang chờ)
    # Nhà cung cấp DỰ PHÒNG theo thứ tự (JSON) — chỉ dùng khi model chính lỗi tạm / sập / khoá hỏng / hết tín dụng, KHÔNG khi nội dung bị chặn
    # hay yêu cầu sai. Mỗi mục "provider:model" hoặc {"model": "provider:model", "api_key_file": "...", "api_key_var": "..."} (hoặc "api_key").
    # Không khai khoá: cùng nhà cung cấp với model chính ⇒ dùng khoá chính; khác ⇒ biến môi trường chuẩn của nhà cung cấp. Trống = như trước.
    llm_fallback_models: list[str | dict[str, str]] = []
    # KHOÁ THEO NHÀ CUNG CẤP cho model của HẠNG trợ lý AI (ADR 0193 — vd hạng "Cơ bản" dùng deepseek): JSON {"deepseek": {"api_key_file": "...",
    # "api_key_var": "DEEPSEEK_API_KEY"}, "openai": "<khoá>"}. Không khai: cùng nhà với model chính ⇒ khoá chính; khác ⇒ biến môi trường chuẩn của nhà đó
    # (DEEPSEEK_API_KEY…). Nhà chưa có khoá ⇒ hạng dùng model mặc định của bản cài (log một dòng) — không bao giờ gửi khoá nhà này sang nhà khác.
    llm_keys: dict[str, str | dict[str, str]] = {}
    # Cầu dao: N lần gọi hỏng liên tiếp (lỗi tạm / sập) trong cửa sổ W giây ⇒ bỏ qua nhà cung cấp đó M giây (chỉ khi còn dự phòng phía sau).
    llm_breaker_failures: int = 3
    llm_breaker_window_s: float = 60.0
    llm_breaker_cooldown_s: float = 60.0
    # Tìm theo nghĩa trong kho tài liệu (0141): model embedding dùng CÙNG khoá với trợ lý; "" = tắt (tìm từ khoá như cũ). Đổi model / số chiều
    # ⇒ đoạn cũ coi như chưa nhúng (vòng quét nhúng lại). Chỉ mục HNSW dựng sẵn cho 768 chiều — số chiều khác vẫn tìm đúng, chỉ là quét tuần tự.
    embed_model: str = "google:gemini-embedding-001"
    embed_dims: int = 768
    # Bổ sung cho mặc định bản cài khi model là máy chủ tự dựng (``openai-compatible:<model>``): base_url, key_ref (tên mục trong llm_keys), truncate,
    # query_prefix, document_prefix, usd_per_mtok, min_sim. Từng công ty đè bằng ``python -m barista.ai_policy embed-set`` (ADR 0232, runbook 20).
    embed_profile: dict[str, Any] = {}
    embed_batch: int = 50            # số đoạn mỗi lần gọi (Gemini nhận tối đa 100)
    embed_timeout_s: float = 15.0    # mỗi lần gọi khi nhúng tài liệu (nền, ngoài vòng quét chung)
    embed_attempts: int = 2          # số lần thử mỗi lô khi nhúng tài liệu (lỗi tạm); hỏng ⇒ đoạn chờ 1 giờ, vòng quét thử lại
    embed_concurrency: int = 2       # số việc nhúng nền chạy cùng lúc trong một tiến trình (mọi công ty)
    embed_query_timeout_s: float = 4.0   # nhúng câu tìm: quá hạn ⇒ tìm từ khoá và nói rõ
    kb_vector_min_sim: float = 0.65  # đoạn giống câu hỏi dưới ngưỡng này không vào kết quả — câu vô nghĩa đo ~0,57–0,60 (ADR 0141 §6)
    kb_vector_candidates: int = 40   # số đoạn gần nhất lấy từ nhánh vector trước khi gộp RRF
    # 0095: cách mang công cụ mỗi lượt — "retrieve" (mặc định) = phần cố định của vai + K công cụ CODE tìm theo câu hỏi (không thêm lượt model),
    # model tìm thêm khi thiếu bằng core.open_tools; "all" = mọi thao tác người hỏi chạy được (giữ để so bằng scripts/eval_matrix.py).
    # Trần phần cố định theo SỐ công cụ và DUNG LƯỢNG (ký tự mô tả + schema).
    agent_tools: str = "retrieve"
    agent_tool_k: int = 15
    agent_tool_budget: int = 40
    agent_tool_chars: int = 45_000
    # 0218: vai có BỘ CÔNG CỤ (core.role_tools) ⇒ phần cố định = bộ của vai; code chỉ tìm thêm K công cụ NGOÀI bộ theo câu hỏi (0 = không tìm, model mở
    # nhóm bằng core.open_group). Đo bằng tests/toolset_bench.py
    agent_tool_k_toolset: int = 3
    # CHỈ dev / eval: khai thêm N công cụ nhiễu như thật (nhân sự, kho, mua hàng…) để mô phỏng danh mục lớn — chỉ có tác dụng khi bật đăng nhập dev
    eval_noise_tools: int = 0
    # Đổi Word / Excel → PDF để xem trong app (kernel/convert.py, 0102): đường dẫn soffice; trống = tự tìm trong PATH (image API cài sẵn)
    soffice_path: str = ""
    # Dựng PDF / ảnh ấn phẩm từ trang HTML (kernel/convert.py, ADR 0252): đường dẫn trình duyệt không giao diện (Chromium / Chrome); trống = tự tìm trong PATH
    chrome_path: str = ""
    agent_thinking: str = "low"   # mức "nghĩ" của model trong chat (low / medium / high) — đổi được để đo bằng scripts/eval_assistant.py
    agent_tool_desc_chars: int = 1000  # độ dài tối đa phần mô tả mỗi công cụ gửi model (cắt ở ranh giới câu / từ) — đo bằng scripts/eval_*.py
    agent_max_tool_calls: int = 20   # trần số lần gọi công cụ / một câu hỏi (câu xuyên phân hệ ~11 bước — 0065); câu tổng quát nên đi qua công cụ "brief" của phân hệ (1–3 bước)
    dispatch_interval_s: float = 1.0
    # Giữ kết nối HTTP rảnh (12/11/2026): >0 ⇒ core báo ``Keep-Alive: timeout=<N-5>`` — BFF web (undici, mặc định chỉ giữ 4 giây) giữ kết nối tới core
    # giữa hai lần bấm thay vì mở kết nối mới. Uvicorn phải giữ ĐÚNG N giây (``--timeout-keep-alive``, deploy/serve.py đặt cùng biến) — máy dev để 0.
    keep_alive_s: float = 0.0
    # Nhận lượt trợ lý (review 12/11 F5, ADR 0200) — mức chung của bản cài, không theo từng khách; 0 = không giới hạn mục đó
    chat_body_max_bytes: int = 22 * 1024 * 1024   # thân request chat (câu hỏi + tối đa 3 ảnh 5 MB dạng base64) — quá ⇒ 413 trước khi đệm hết
    chat_message_max_chars: int = 8000             # độ dài câu hỏi gõ vào — tài liệu dài thì tải vào kho tài liệu rồi hỏi
    chat_turns_per_person: int = 3                 # lượt đang chạy cùng lúc của MỘT người (gồm lượt chạy tiếp sau khi app đã đóng) ⇒ 429
    chat_turns_per_company: int = 20               # …của một công ty ⇒ 429
    chat_turns_per_process: int = 60               # …của cả tiến trình API ⇒ 503
    chat_turn_max_s: float = 300.0                 # thời gian tối đa một lượt (kể cả khi người hỏi đã đóng app) — quá ⇒ dừng, phần đã làm vẫn lưu
    # Tắt máy chủ (F6): chờ lượt đang chạy xong tối đa ``grace`` giây; còn chạy ⇒ dừng, ghi trạng thái "bị ngắt", chờ thêm ``settle`` giây cho phần lưu
    # — tổng nằm trong khoảng dư của stop_grace_period sau khi Uvicorn đã chờ request (deploy/serve.py)
    shutdown_turn_grace_s: float = 8.0
    shutdown_turn_settle_s: float = 3.0
    ai_usage_wait_s: float = 5.0                   # ghi lượt dùng AI: chờ mượn kết nối tối đa — quá ⇒ xếp hàng ghi lại (F10), không treo / không bỏ
    # App từ xa (0140): khoá của bản cài để DẪN XUẤT khoá ký webhook từng app (không lưu khoá ký trong DB) — trống ⇒ không cấp khoá app, không gửi
    # webhook. Đổi khoá này ⇒ mọi app phải nhận lại khoá (xoay khoá). Bản cài: bootstrap sinh vào runtime.env; dev: scripts/dev-db.sh ghi backend/.env.
    apps_signing_secret: str = ""
    apps_token_ttl_s: int = 1800     # token truy cập của app (client credentials) — không có token làm mới, hết hạn thì xin lại bằng khoá
    webhook_interval_s: float = 2.0  # vòng gửi webhook; 0 = tắt
    # Gửi webhook tới http:// hoặc địa chỉ nội bộ (127.x, 10.x, 192.168.x…) — CHỈ máy dev / test. Bản thật: chỉ https tới địa chỉ công khai (chống SSRF).
    webhook_allow_private: bool = False
    # Tệp (0122): thân tệp ở kho đối tượng, DB giữ tham chiếu (core.files). "local" = thư mục (dev, test, bản cài một máy — phải là volume bền,
    # MỘT thư mục cho MỘT database); "r2" = Cloudflare R2 (tương thích S3) qua r2_* bên dưới.
    files_store: str = "local"
    files_dir: str = ""            # BẮT BUỘC với "local" (không có mặc định — xem files.store); dev: scripts/dev-db.sh ghi backend/.env
    files_max_mb: int = 30         # trần chung mỗi tệp; module đặt trần nhỏ hơn của mình
    files_sign_secret: str = ""    # khoá ký link /v1/files/signed của kho thư mục — BẮT BUỘC khi API chạy với "local" (bản cài: bootstrap sinh vào runtime.env)
    files_sweep_s: float = 3600.0  # vòng dọn: dòng sổ mồ côi ⇒ release, quá hạn lưu ⇒ xoá, đối tượng không sổ quá files_orphan_grace_days ⇒ xoá; 0 = tắt
    # đối tượng không có dòng sổ chỉ xoá khi cũ hơn N ngày — PHẢI dài hơn tuổi bản dump cũ nhất còn khôi phục (khôi phục DB cũ không làm mất tệp mới)
    files_orphan_grace_days: float = 30.0
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    r2_endpoint: str = ""          # trống ⇒ https://<r2_account_id>.r2.cloudflarestorage.com
    r2_backup_bucket: str = ""     # bucket SAO LƯU (pg_dump, khoá bucket R2 chống xoá) — khác bucket tệp; trống ⇒ không đẩy bản sao lưu lên R2
    # Báo lỗi hệ thống cho NHÀ VẬN HÀNH (kernel/alerts.py, ROADMAP A4): Telegram; trống ⇒ tắt. Mỗi (thao tác, loại lỗi) tối đa một tin / quiet_s.
    alert_telegram_token: str = ""
    alert_telegram_chat: str = ""
    alert_quiet_s: float = 600.0
    # Lỗi DỒN DẬP (kernel/alerts.py ``Burst``): đếm lỗi hệ thống (executor + HTTP 5xx) theo cửa sổ trượt; vượt ngưỡng ⇒ MỘT tin, hết lỗi ⇒ tin "đã hết".
    alert_burst_errors: int = 5            # ≥ ngần ấy lỗi trong cửa sổ ⇒ báo
    alert_burst_ratio: float = 0.2         # … hoặc ≥ tỷ lệ này trên số yêu cầu HTTP của cửa sổ (khi đủ ``alert_burst_min_requests``)
    alert_burst_min_requests: int = 20
    alert_burst_window_s: float = 300.0
    alert_burst_quiet_s: float = 900.0     # tối đa một tin "lỗi dồn dập" / ngần ấy giây
    alert_burst_calm_s: float = 180.0      # dưới ngưỡng liên tục ngần ấy giây ⇒ tin "đã hết lỗi"
    alert_instance: str = ""               # tên bản cài trong tin; trống ⇒ tên máy của ``public_api_url``
    # Việc theo dõi nhiều ngày (quyết định 0021): DBOS giữ nhịp ngủ / thức. Tắt ⇒ việc vẫn ghi nhận, chỉ không tự chạy.
    work_engine: bool = True
    dbos_database_url: str = ""  # trống ⇒ dùng owner_database_url (schema "dbos" trong cùng database)
    work_reconcile_s: float = 300.0
    # Vòng quét hạn xử lý lead (quyết định 0034): chấm hạn gọi, thu hồi, chia lại hàng chờ. 0 = tắt.
    leadflow_sweep_s: float = 60.0
    morning_interval_s: float = 900.0   # vòng soạn đề xuất buổi sáng (0074 A) — mỗi công ty một lượt / ngày sau 6 giờ của công ty đó (0169); 0 = tắt
    # Đăng nhập Google (quyết định 0040). client ID của mọi ứng dụng Barista (web / iOS / macOS) — id_token phải có aud thuộc danh sách này.
    google_client_ids: list[str] = []
    google_app_client_id: str = ""        # client ID app Mac dùng (loại "iOS" của Google, cũng chạy trên macOS) — trả cho app qua /v1/auth/config
    google_web_client_id: str = ""        # client ID loại "Web" cho web app (Google Identity Services) — tự được nhận làm aud, không cần liệt kê lại
    google_hosted_domains: list[str] = []  # trống = nhận cả Gmail cá nhân (quyền vào vẫn theo danh sách được mời)
    session_ttl_hours: int = 12
    # Gửi email hệ thống (0205 §5c — mã đăng nhập): SMTP chuẩn (kernel/mailer.py). Trống ⇒ không đăng nhập bằng mã email được (máy dev: mã ghi ra log).
    smtp_url: str = ""                    # smtps://<user>:<mật khẩu / khoá API>@<host>:465 — Resend: smtps://resend:<khoá>@smtp.resend.com:465
    mail_from: str = ""                   # "Công ty <no-reply@vi-du.test>" — tên miền gửi phải khai SPF / DKIM
    voice_model: str = "gemini-3.8-live"   # Gemini Live — lớp giọng nói (quyết định 0042)
    # Phiên giọng nói (ADR 0193): kiểm lại tư cách + công tắc AI + hạn mức và ghi chi phí Live mỗi chừng này giây; khoảng nào Gemini Live không báo số token
    # ⇒ ghi ƯỚC TÍNH theo phút (thận trọng — cao hơn mức thường gặp: 32 token âm thanh / giây; gemini-3.8-live 3 USD / 12 USD mỗi triệu token âm thanh vào / ra ⇒ vào + ra liên tục cả phút ≈ 0,03 USD)
    voice_check_s: float = 30.0
    voice_usd_per_minute: float = 0.05
    # Thông báo (quyết định 0044): nghe NOTIFY để báo app (SSE) + gửi đẩy APNs. Khoá APNs (.p8) đặt ngoài repo; trống ⇒ không gửi đẩy.
    notify_listen: bool = True
    apns_key_file: str = ""
    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_topic: str = "vn.barista.desk"      # bundle ID app iPhone
    # Gọi trực tiếp 1–1 (0114, 07/11): máy STUN / TURN của BẢN CÀI, phẩy ngăn cách. TURN theo kiểu "TURN REST API": khoá bí mật chung với coturn
    # (static-auth-secret) chỉ ở máy chủ; trình duyệt nhận tên + mật khẩu tạm do server ký (gắn cuộc gọi + ghế), hết hạn sau turn_ttl_s. Trống ⇒ không có TURN (mạng chặn
    # kết nối thẳng thì không gọi được). STUN mặc định của Google: chỉ để biết địa chỉ công khai, không chuyển tiếng.
    stun_urls: str = "stun:stun.l.google.com:19302"
    turn_urls: str = ""            # vd "turn:turn.example.vn:3478?transport=udp,turn:turn.example.vn:3478?transport=tcp,turns:turn.example.vn:5349"
    turn_secret: str = ""
    turn_secret_file: str = ""     # file chỉ chứa khoá (đặt ngoài repo) — dùng thay turn_secret
    # ngắn: người bị bỏ khỏi cuộc gọi (thu hồi, rút đồng ý, mất quyền) không lấy được cặp mới, nên chỉ chuyển tiếp được tới khi cặp đang cầm hết hạn;
    # trang đang gọi tự lấy cặp mới mỗi nửa hạn
    turn_ttl_s: int = 300
    # TURN của Cloudflare (Realtime TURN, 09/11): có khoá ⇒ mỗi ghế xin một cặp tạm qua API của Cloudflare (hạn = turn_ttl_s), thay cho turn_urls / coturn.
    # Không phải mở cổng trên VPS; chạy cả qua 443 (turns:) nên qua được tường lửa công ty. API lỗi ⇒ chỉ STUN, trang gọi vẫn mở.
    turn_cloudflare_key_id: str = ""
    turn_cloudflare_token: str = ""
    turn_cloudflare_token_file: str = ""   # file chỉ chứa API token của khoá TURN (đặt ngoài repo) — dùng thay turn_cloudflare_token


settings = Settings()
