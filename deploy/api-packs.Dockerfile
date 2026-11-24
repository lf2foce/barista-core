# Lớp MỎNG ghép gói ngoài lõi của bản cài (0155) lên ảnh api CHỈ LÕI do CI dựng sẵn (.github/workflows/images.yml) — deploy/ops/deploy.py dựng
# trên VPS: chỉ chép gói + cài --no-deps (vài giây, ít RAM), không apt / uv sync như deploy/api.Dockerfile. BASE = ảnh lõi của CÙNG commit đã kéo về
# (``barista-api:<sha>``) ⇒ nhãn revision kế thừa từ ảnh lõi. Bước cài gói giống hệt api.Dockerfile — đổi một bên thì đổi cả hai.
ARG BASE
FROM ${BASE}
COPY deploy/packs ./packs
RUN for d in ./packs/*/; do if [ -f "$d/pyproject.toml" ]; then uv pip install --python /app/.venv/bin/python --no-deps -e "$d" || exit 1; fi; done
