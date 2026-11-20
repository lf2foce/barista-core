"""PID 1 của container API: nhận SIGTERM ⇒ VẪN PHỤC VỤ thêm 20 giây (Traefik phát hiện container Swarm mới bằng cách hỏi lại mỗi ~15
giây — rút ngay thì có lúc không còn bản nào nhận: đo 31/10 ra 32 lượt 503 / 6 giây với 1 bản), rồi bật cờ rút (/health 503 ⇒ Traefik
thôi gửi request mới), đợi 6 giây cho lượt kiểm tra kế tiếp, rồi mới chuyển SIGTERM cho Uvicorn — còn 60 giây để xong request / stream.
Tổng ≤ 86 giây < stop_grace_period 100 giây (deploy/stack.json)."""

import os
import signal
import subprocess
import time
from pathlib import Path


def main() -> int:
    flag = Path(os.environ.get("BARISTA_DRAIN_FILE", "/tmp/barista-draining"))
    flag.unlink(missing_ok=True)
    # địa chỉ người gọi: KHÔNG tin X-Forwarded-For theo địa chỉ bên gửi (mạng overlay dùng chung, địa chỉ Traefik đổi khi chạy lại); tin theo KHOÁ
    # X-Barista-Edge do Traefik của stack này / BFF gắn — lớp ``barista.edge`` (review 0150 lát 4, 10/11). Xử lý forwarded của uvicorn TẮT.
    # kết nối rảnh giữ 95 giây (mặc định uvicorn 5): Traefik giữ kết nối tới container 90 giây, BFF web giữ theo Keep-Alive core báo (N-5) —
    # client luôn đóng trước server ⇒ không dùng lại kết nối server vừa đóng (502), và lần bấm sau vài giây không phải mở kết nối mới (12/11/2026)
    keep = os.environ.setdefault("BARISTA_KEEP_ALIVE_S", "95")
    child = subprocess.Popen(["uvicorn", "barista.edge:app", "--host", "0.0.0.0", "--port", "8300", "--no-proxy-headers",
                              "--timeout-keep-alive", keep, "--timeout-graceful-shutdown", "60"], start_new_session=True)
    stop_at: float | None = None
    flagged = False
    forwarded = False

    def drain(signum: int, frame: object) -> None:
        nonlocal stop_at
        if stop_at is None:
            stop_at = time.monotonic()

    signal.signal(signal.SIGTERM, drain)
    signal.signal(signal.SIGINT, drain)
    while child.poll() is None:
        if stop_at is not None:
            waited = time.monotonic() - stop_at
            if not flagged and waited >= 20:
                flag.touch()
                flagged = True
            if not forwarded and waited >= 26:
                child.send_signal(signal.SIGTERM)
                forwarded = True
        time.sleep(0.1)
    return child.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
