# Web Barista (Next.js 16, BFF) — dựng TRÊN VPS bởi deploy/ops/deploy.py, ngữ cảnh build = gốc repo.
FROM node:24-slim AS build
WORKDIR /app
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
# standalone + pnpm cần node_modules phẳng (bẫy đã gặp ở coldbrew)
RUN pnpm install --frozen-lockfile --config.node-linker=hoisted
COPY web/ ./
ENV NEXT_TELEMETRY_DISABLED=1 BARISTA_STANDALONE=1
RUN pnpm build

FROM node:24-slim
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3100 HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
COPY deploy/serve-web.mjs ./
ARG BARISTA_RELEASE=dev
LABEL org.opencontainers.image.revision=$BARISTA_RELEASE
ENV BARISTA_RELEASE=$BARISTA_RELEASE
EXPOSE 3100
# Health check (sự cố 23/11/2026): trước đây 3 giây/lần, timeout 3 giây, 2 lần thử ⇒ mỗi container sinh một tiến trình mới mỗi 3 giây (10 container ≈ 3
# tiến trình/giây lúc rảnh) và máy chậm vài giây là container còn sống bị Swarm giết, góp phần làm cả VPS khởi động lại. Nay: 30 giây/lần, timeout
# 10 giây, 3 lần; trong phút đầu (start-period) đo 3 giây/lần (start-interval) để phát hành vẫn thấy "khoẻ" sớm.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --start-interval=3s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3100/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "serve-web.mjs"]
