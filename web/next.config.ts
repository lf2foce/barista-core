import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  devIndicators: false,
  // dev: bản cài thứ hai mở bằng 127.0.0.1 (cookie tách khỏi localhost của bản chính — runbook 01); Next dev mặc định chặn tài nguyên dev từ host khác
  allowedDevOrigins: ["127.0.0.1"],
  // bản cài thứ hai chạy song song ở máy dev (môi trường kế toán — runbook 01): thư mục build riêng, không giẫm .next của bản chính
  ...(process.env.BARISTA_DIST_DIR ? { distDir: process.env.BARISTA_DIST_DIR } : {}),
  // ảnh Docker (deploy/web.Dockerfile): server.js + node_modules tối thiểu; chạy dev không đổi
  ...(process.env.BARISTA_STANDALONE ? { output: "standalone" as const } : {}),
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
