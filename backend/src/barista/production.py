"""Chế độ production của bản cài (ADR 0194, review F3): ``BARISTA_ENV=production`` ⇒ lõi KHÔNG lên khi còn đường vào / cờ chỉ dành cho dev
hay bản demo, hoặc thiếu đăng nhập thật. Gọi đầu ``lifespan`` (trước khi chạm DB): lỗi ⇒ healthcheck hỏng, Swarm giữ bản cũ. Mẫu triển khai
(deploy/ops/deploy.py ``render``) đặt đúng các giá trị này; ở đây là chốt bằng code, không dựa vào việc mẫu đúng."""

from barista.config import Settings, settings

ENVS = ("", "demo", "production")


def errors(s: Settings) -> list[str]:
    if s.env not in ENVS:   # gõ sai ("prod") mà coi như máy dev là mở cửa lặng lẽ
        return [f"BARISTA_ENV={s.env!r} — chỉ nhận demo | production (trống = máy dev)"]
    if s.env != "production":
        return []
    found = [f"BARISTA_{name.upper()} phải tắt" for name in ("dev_auth", "allow_app_temp", "webhook_allow_private") if getattr(s, name)]
    found += [f"BARISTA_{name.upper()} phải trống" for name in ("demo_today", "llm_replay_dir") if getattr(s, name)]
    if not s.google_web_client_id.strip():
        found.append("thiếu BARISTA_GOOGLE_WEB_CLIENT_ID — production chỉ đăng nhập Google thật (runbook 09)")
    found += [f"BARISTA_{name.upper()}={getattr(s, name)!r} phải là https:// công khai" for name in ("public_api_url", "web_url")
              if not getattr(s, name).startswith("https://")]
    return found


def check_config(s: Settings = settings) -> None:
    if found := errors(s):
        raise RuntimeError("Chế độ production từ chối khởi động: " + "; ".join(found))
