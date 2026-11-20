"""Cảnh báo lỗi DỒN DẬP cho nhà vận hành (``kernel/alerts.py`` — ``Burst`` + ``ErrorWatch``): đếm lỗi hệ thống (executor + HTTP 5xx / lỗi chưa bắt) theo cửa
sổ trượt; vượt ngưỡng ⇒ MỘT tin Telegram (bản cài, số lỗi, loại nhiều nhất, giờ), tối đa một tin / 15 phút, tin "đã hết lỗi" khi trở lại bình thường;
tin không mang nội dung yêu cầu / dữ liệu khách. Telegram giả, thời gian giả — không gọi mạng."""

import asyncio
import time
import uuid
from typing import Any

import httpx
import pytest

from barista.config import settings
from barista.kernel import alerts


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Kênh Telegram giả + bộ đếm mới + ngưỡng mặc định (5 lỗi / 5 phút, 20% khi ≥ 20 yêu cầu, 15 phút im, 3 phút yên)."""
    out: list[str] = []

    async def fake_send(text: str) -> None:
        out.append(text)

    monkeypatch.setattr(alerts, "_send", fake_send)
    monkeypatch.setattr(alerts, "burst", alerts.Burst())
    monkeypatch.setattr(alerts, "_last", {})
    for k, v in {"alert_telegram_token": "123:gia", "alert_telegram_chat": "-100gia", "alert_instance": "ban-cai-thu", "release": "c0ffee" * 7,
                 "alert_burst_errors": 5, "alert_burst_ratio": 0.2, "alert_burst_min_requests": 20, "alert_burst_window_s": 300.0,
                 "alert_burst_quiet_s": 900.0, "alert_burst_calm_s": 180.0}.items():
        monkeypatch.setattr(settings, k, v)
    return out


def test_burst_fires_once_then_stays_quiet_and_says_when_it_is_over(sent: list[str]) -> None:
    b, t = alerts.Burst(), 10_000.0
    for i in range(4):
        b.record(t + i, request=True, kind="OperationalError")
    assert b.check(t + 4) is None and not b.firing                                     # 4 lỗi: dưới ngưỡng
    b.record(t + 5, request=True, kind="PoolTimeout")
    b.record(t + 6, request=True, kind="OperationalError")
    msg = b.check(t + 6)
    assert msg is not None and b.firing
    assert "ban-cai-thu" in msg and "6 lỗi / 5 phút" in msg and "Loại nhiều nhất: OperationalError (5)" in msg
    assert "(giờ Việt Nam)" in msg and "c0ffeec0ffee" in msg
    for i in range(30):                                                                 # lỗi tiếp ⇒ không gửi thêm trong 15 phút
        b.record(t + 10 + i * 10, request=True, kind="OperationalError")
        assert b.check(t + 10 + i * 10) is None
    last_error = t + 10 + 29 * 10
    assert b.check(last_error + 299) is None                                            # lỗi còn trong cửa sổ ⇒ vẫn dồn dập
    assert b.check(last_error + 301) is None and b.firing                               # vừa dưới ngưỡng: chờ yên 3 phút
    over = b.check(last_error + 301 + 180)
    assert over is not None and "đã hết lỗi dồn dập" in over and not b.firing and "đỉnh" in over
    # đợt mới còn trong 15 phút kể từ tin trước ⇒ im, và đợt im thì không có tin "đã hết"; qua 15 phút ⇒ gửi lại
    t2 = last_error + 600
    for i in range(5):
        b.record(t2 + i, request=False, kind="HTTP 502")
    assert b.check(t2 + 5) is None and b.firing
    assert b.check(t2 + 305) is None and b.check(t2 + 305 + 180) is None and not b.firing
    t3 = t2 + 1000
    for i in range(5):
        b.record(t3 + i, request=False, kind="HTTP 502")
    assert "HTTP 502 (5)" in (b.check(t3 + 5) or "")


def test_ratio_counts_only_with_enough_requests(sent: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "alert_burst_errors", 100)                          # chỉ xét tỷ lệ
    b, t = alerts.Burst(), 50_000.0
    for i in range(10):
        b.record(t + i, request=True, kind="OperationalError" if i < 4 else None)
    assert b.check(t + 10) is None                                                      # 40% nhưng mới 10 yêu cầu (< 20)
    for i in range(20):
        b.record(t + 11 + i, request=True, kind=None)
    assert b.check(t + 31) is None                                                      # 4 / 30 = 13%
    for i in range(2):
        b.record(t + 32 + i, request=True, kind="OperationalError")
    assert b.check(t + 34) is None                                                      # 6 / 32 = 18%
    b.record(t + 35, request=True, kind="OperationalError")
    msg = b.check(t + 35)
    assert msg is not None and "7 lỗi / 5 phút (21% của 33 yêu cầu)" in msg


def test_ratio_threshold_fires_without_the_absolute_count(sent: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "alert_burst_errors", 100)
    b, t = alerts.Burst(), 70_000.0
    for i in range(25):
        b.record(t + i, request=True, kind="OperationalError" if i % 4 == 0 else None)   # 7 / 25 = 28%
    msg = b.check(t + 25)
    assert msg is not None and "7 lỗi / 5 phút (28% của 25 yêu cầu)" in msg


def _app(behaviour: dict[str, Any]) -> Any:
    async def inner(scope: Any, receive: Any, send: Any) -> None:
        mode = behaviour["mode"]
        if mode == "executor":   # executor gặp lỗi hệ thống trong request rồi trả 500 (map_error) ⇒ đếm MỘT lần, loại = lớp lỗi
            alerts.operator(uuid.uuid4(), "crm.leads", "OperationalError", "SSL SYSCALL error: khách Nguyễn Văn A 0101234567")
        if mode == "raise":
            raise RuntimeError("chi tiết nội bộ: hợp đồng HĐ-001 của khách")
        status = 200 if mode == "ok" else 500 if mode in ("executor", "500") else 502
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    return alerts.ErrorWatch(inner)


async def _hit(app: Any, n: int, path: str = "/v1/query/crm.leads?q=Nguy%E1%BB%85n") -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://core") as c:
        for _ in range(n):
            await c.get(path)


async def test_http_5xx_and_executor_errors_send_one_telegram_without_request_content(sent: list[str]) -> None:
    mode: dict[str, Any] = {"mode": "ok"}
    app = _app(mode)
    await _hit(app, 30)
    await _hit(app, 50, "/health")                                                       # kiểm sống: không tính
    mode["mode"] = "executor"
    await _hit(app, 3)
    mode["mode"] = "raise"
    await _hit(app, 2)
    await asyncio.sleep(0)
    assert alerts.burst.stats(time.monotonic())[:2] == (35, 5)                           # mỗi request đếm MỘT lần, /health không tính
    assert len(sent) == 2 and "crm.leads: OperationalError" in sent[0], sent             # tin từng lỗi (một / thao tác + loại) rồi tin dồn dập
    mode["mode"] = "500"
    await _hit(app, 20)
    await asyncio.sleep(0)
    burst_msgs = [m for m in sent if "dồn dập" in m]
    assert len(burst_msgs) == 1, sent
    text = burst_msgs[0]
    assert "ban-cai-thu" in text and "Loại nhiều nhất" in text
    for leak in ("Nguyễn", "0101234567", "HĐ-001", "crm.leads", "/v1/", "SSL SYSCALL"):
        assert leak not in text, leak


async def test_per_error_operator_alert_still_goes_out_before_a_burst_and_is_muted_during_one(sent: list[str]) -> None:
    tid = uuid.uuid4()
    alerts.operator(tid, "acct.post_entry", "KeyError", "x")
    await asyncio.sleep(0)
    assert len(sent) == 1 and "acct.post_entry: KeyError" in sent[0]
    for i in range(6):
        alerts.operator(tid, f"m.cmd{i}", "OperationalError", "y")
    await asyncio.sleep(0)
    assert sum("dồn dập" in m for m in sent) == 1
    assert not any("m.cmd5" in m for m in sent)                                          # đang dồn dập: tin tổng thay cho bão tin


async def test_without_a_channel_it_still_counts_but_sends_nothing(sent: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "alert_telegram_token", "")
    for _ in range(6):
        alerts.operator(uuid.uuid4(), "x.y", "OperationalError", "z")
    await asyncio.sleep(0)
    assert sent == [] and alerts.burst.firing


def test_instance_name_falls_back_to_the_public_api_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "alert_instance", "")
    monkeypatch.setattr(settings, "public_api_url", "https://api.vi-du.test")
    assert alerts.instance_name() == "api.vi-du.test"


async def test_an_error_in_work_detached_from_a_finished_request_is_still_counted(sent: list[str]) -> None:
    later: list[asyncio.Task[None]] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        async def detached() -> None:   # lượt tách khỏi HTTP (chép ngữ cảnh của request) lỗi SAU khi request đã trả lời
            await asyncio.sleep(0.01)
            alerts.note_error("OperationalError")

        later.append(asyncio.create_task(detached()))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    await _hit(alerts.ErrorWatch(inner), 1)
    await asyncio.gather(*later)
    assert alerts.burst.stats(time.monotonic())[:2] == (1, 1)


async def test_a_single_error_alert_carries_the_kind_but_never_the_error_text(sent: list[str]) -> None:
    """Tin từng lỗi cho nhà vận hành không kèm nội dung lỗi (có thể chứa tên / SĐT khách trong câu SQL hay giá trị vi phạm) — chỉ lệnh + loại lỗi + công ty."""
    alerts.operator(uuid.uuid4(), "deals.sign", "IntegrityError", "duplicate key value violates unique constraint (phone)=(0912345678) Nguyen Van A")
    await asyncio.sleep(0.05)
    assert sent and "deals.sign: IntegrityError" in sent[0]
    assert "0912345678" not in sent[0] and "Nguyen Van A" not in sent[0] and "duplicate key" not in sent[0]
