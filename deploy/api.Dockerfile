# Core Barista (FastAPI) — dựng TRÊN VPS bởi deploy/ops/deploy.py, ngữ cảnh build = gốc repo, gắn mã commit.
FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app
# Xem trước Word / Excel trong app = đổi sang PDF bằng LibreOffice chạy ngầm (0102); font có dấu tiếng Việt + font cùng khổ Times / Arial.
# PDF / ảnh trang ấn phẩm = in chính trang HTML bằng Chromium không giao diện (0252): gói `chromium-headless-shell` (bản tối thiểu, không có giao diện / ngôn ngữ thừa;
# ~+230 MB cài đặt cùng thư viện phụ thuộc). Trang tự chứa (phông + ảnh nhúng) nên không cần thêm font hệ thống. Dựng ảnh thiếu `chromium-headless-shell` ⇒ build lỗi ngay, không đợi lúc chạy.
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
        fonts-dejavu-core fonts-liberation chromium-headless-shell \
    && command -v chromium-headless-shell \
    && rm -rf /var/lib/apt/lists/*
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev
COPY deploy/serve.py ./
# GÓI NGOÀI LÕI của bản cài (0155): deploy/release.sh đặt bản phân phối từng gói (git archive đúng tag của barista-packs) vào deploy/packs/<khoá>/
# của mã gửi lên; trong repo lõi thư mục chỉ có README ⇒ ảnh không có gói nào. Cài editable như chính lõi (uv sync) — dữ liệu mẫu của gói
# (vd hồ sơ L/C cho seed --profile bank) nằm cạnh mã ở /app/packs/<khoá>/. Không có dependency mới: --no-deps (gói chỉ cần lõi).
COPY deploy/packs ./packs
RUN for d in ./packs/*/; do if [ -f "$d/pyproject.toml" ]; then uv pip install --python /app/.venv/bin/python --no-deps -e "$d" || exit 1; fi; done
ARG BARISTA_RELEASE=dev
LABEL org.opencontainers.image.revision=$BARISTA_RELEASE
# Migration + nạp mẫu chạy MỘT lần ở bước phát hành (python -m barista.seed), không bao giờ trong bản API đang phục vụ.
ENV BARISTA_RELEASE=$BARISTA_RELEASE BARISTA_MIGRATE_ON_START=false PATH=/app/.venv/bin:$PATH
EXPOSE 8300
HEALTHCHECK --interval=3s --timeout=3s --start-period=20s --retries=2 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8300/health', timeout=2).read()"
CMD ["python", "serve.py"]
