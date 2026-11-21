"""Đổi tài liệu văn phòng sang PDF để XEM trong app (quyết định 0102) — MỘT trình xem (PDF) cho mọi loại file: PDF giữ nguyên; Word / Excel /
PowerPoint / OpenDocument đổi bằng LibreOffice chạy ngầm (``soffice --headless``). Chỉ để xem — bản gốc vẫn là thứ được lưu / tải về.

* Máy chưa cài LibreOffice (dev) ⇒ ``Unavailable``: app quay về "tải về" như cũ, không lỗi.
* Chạy tối đa 2 lượt đổi cùng lúc (LibreOffice nặng); mỗi lượt một hồ sơ người dùng riêng trong thư mục tạm (không khoá nhau); quá 90 giây ⇒ huỷ.
* Nhớ kết quả trong tiến trình theo băm nội dung (24 file gần nhất) — mở lại cùng file không đổi lại.
* File của người dùng là dữ liệu không tin được: chỉ đổi định dạng, không chạy macro (LibreOffice chế độ không giao diện không chạy macro).

Ấn phẩm (ADR 0252): PDF / ảnh trang dựng từ CHÍNH trang HTML của bản xem bằng trình duyệt không giao diện (``html_to_pdf`` / ``html_to_png``) — không đi qua Word.
Trình duyệt chạy như thứ không tin: không mạng (DNS bị chặn + CSP ``default-src 'none'`` chèn vào trang — chỉ ảnh / phông nhúng dạng data URL), không script, hồ sơ
tạm riêng mỗi lượt, một lượt một lúc, hết giờ ⇒ diệt cả nhóm tiến trình, trần cỡ trang vào / cỡ tệp ra, heap JS có trần. Thiếu trình duyệt / lỗi ⇒ ``Unavailable``
với lời rõ ràng — KHÔNG lùi về đường khác. (Không đặt trần RSS bằng rlimit: Chromium giữ địa chỉ ảo rất lớn, ``RLIMIT_AS`` làm nó không khởi động được — trần bộ nhớ
của cả ảnh là ``mem_limit`` của container.)
"""

import asyncio
import hashlib
import os
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path

from barista.config import settings

OFFICE = frozenset({".docx", ".doc", ".odt", ".rtf", ".xlsx", ".xls", ".ods", ".pptx", ".ppt", ".odp"})
CACHE_MAX = 24
TIMEOUT_S = 90
_cache: OrderedDict[str, bytes] = OrderedDict()
_slots = asyncio.Semaphore(2)


class Unavailable(Exception):
    """Không xem trước được (máy chủ chưa cài LibreOffice / loại file không hỗ trợ / đổi lỗi) — app cho tải file gốc thay vì xem."""


def binary() -> str | None:
    return settings.soffice_path or shutil.which("soffice") or shutil.which("libreoffice")


def viewable(filename: str) -> bool:
    """Loại file xem được trong app (PDF, hoặc đổi được sang PDF)."""
    ext = Path(filename).suffix.lower()
    return ext == ".pdf" or ext in OFFICE


def _convert(exe: str, ext: str, data: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="barista-pdf-") as d:
        src = Path(d) / f"in{ext}"
        src.write_bytes(data)
        try:
            subprocess.run([exe, f"-env:UserInstallation=file://{d}/profile", "--headless", "--norestore", "--convert-to", "pdf",
                            "--outdir", d, str(src)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired as e:
            raise Unavailable("Đổi sang PDF quá lâu — tải file về để xem") from e
        out = Path(d) / "in.pdf"
        if not out.exists() or out.stat().st_size == 0:
            raise Unavailable("Không đổi được file này sang PDF — tải file về để xem")
        return out.read_bytes()


async def to_pdf(filename: str, data: bytes) -> bytes:
    """PDF để xem: PDF ⇒ chính nó; file văn phòng ⇒ đổi (có nhớ); loại khác / chưa cài LibreOffice ⇒ ``Unavailable``."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return data
    if ext not in OFFICE:
        raise Unavailable(f"Chưa xem trước được loại file {ext or '(không đuôi)'} — tải file về để xem")
    key = hashlib.sha256(data).hexdigest() + ext
    if (hit := _cache.get(key)) is not None:
        _cache.move_to_end(key)
        return hit
    exe = binary()
    if exe is None:
        raise Unavailable("Máy chủ chưa cài LibreOffice nên chưa xem trước được file Word / Excel — tải file về để xem")
    async with _slots:
        pdf = await asyncio.to_thread(_convert, exe, ext, data)
    _cache[key] = pdf
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)
    return pdf


# ── Trang HTML → PDF / ảnh bằng trình duyệt không giao diện (ấn phẩm — 0252) ──

CHROME_TIMEOUT_S = 60
HTML_MAX_BYTES = 24 * 1024 * 1024
OUT_MAX_BYTES = 40 * 1024 * 1024
CHROME_NAMES = ("chromium", "chromium-browser", "chromium-headless-shell", "google-chrome", "google-chrome-stable", "chrome")
CHROME_MAC = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CSP = "default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
_chrome_slot = asyncio.Semaphore(1)


def chrome_binary() -> str | None:
    return settings.chrome_path or next((p for n in CHROME_NAMES if (p := shutil.which(n))), None) or (CHROME_MAC if Path(CHROME_MAC).exists() else None)


def _sealed(html: str, script: str = "") -> str:
    """Chèn CSP chặn mọi tải từ ngoài vào ``<head>`` — lớp chắn thứ hai sau việc chặn DNS; trang không có ``<head>`` thì không tin. ``script`` (chỉ lúc ĐO bố cục): đúng một
    đoạn script của máy chủ, cho chạy bằng nonce ngẫu nhiên của lượt này — script khác (kể cả lọt qua chỗ thoát ký tự) vẫn bị chặn."""
    head = html.find("<head>")
    if head < 0:
        raise Unavailable("Trang không có thẻ <head> — không dựng được PDF")
    nonce = secrets.token_hex(12) if script else ""
    csp = CSP + (f"; script-src 'nonce-{nonce}'" if nonce else "")
    out = html[:head + 6] + f'<meta http-equiv="Content-Security-Policy" content="{csp}">' + html[head + 6:]
    if script:
        end = out.rfind("</body>")
        if end < 0:
            raise Unavailable("Trang không có thẻ </body> — không đo được bố cục")
        out = out[:end] + f'<script nonce="{nonce}">{script}</script>' + out[end:]
    return out


def _run_chrome(exe: str, html: str, mode: list[str], out_name: str, script: str = "", cancel: threading.Event | None = None) -> bytes:
    """``mode``: cờ chọn đầu ra, ``{out}`` = đường tệp ra trong thư mục tạm; ``--dump-dom`` ⇒ đầu ra là DOM in ra stdout. ``cancel`` được đặt ⇒ diệt trình duyệt sớm. Hàm chỉ
    RETURN / raise SAU KHI cả nhóm tiến trình trình duyệt đã bị diệt và đã thu (``wait``) — người gọi dựa vào đó để nhả chỗ giới hạn lượt."""
    with tempfile.TemporaryDirectory(prefix="barista-chrome-") as d:
        src, out = Path(d) / "in.html", Path(d) / out_name
        src.write_text(_sealed(html, script), encoding="utf-8")
        cmd = [exe, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-sync", "--disable-component-update",
               "--disable-background-networking", "--disable-dev-shm-usage", "--hide-scrollbars", "--mute-audio", f"--user-data-dir={d}/profile",
               "--host-resolver-rules=MAP * ~NOTFOUND", "--proxy-server=direct://", "--proxy-bypass-list=*", "--renderer-process-limit=1",
               "--js-flags=--max-old-space-size=256", *(x.format(out=out) for x in mode), f"file://{src}"]
        if hasattr(os, "geteuid") and os.geteuid() == 0:   # root trong container: sandbox của trình duyệt cần người dùng thường; trang là HTML do máy dựng, CSP chặn script
            cmd.insert(1, "--no-sandbox")
        with out.open("wb") if "--dump-dom" in mode else open(os.devnull, "wb") as sink:
            proc = subprocess.Popen(cmd, stdout=sink, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + CHROME_TIMEOUT_S
        try:
            while proc.poll() is None:
                if cancel is not None and cancel.is_set():
                    raise Unavailable("Đã huỷ dựng PDF")
                if time.monotonic() > deadline:
                    raise Unavailable("Dựng PDF quá lâu — thử lại sau, hoặc bớt ảnh")
                try:
                    proc.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)   # trình duyệt đẻ nhiều tiến trình con — diệt cả nhóm, không để sót
            except ProcessLookupError:
                pass
            proc.wait()   # thu tiến trình gốc: tới đây trình duyệt thật sự đã thoát
        if not out.exists() or out.stat().st_size == 0 or out.stat().st_size > OUT_MAX_BYTES:
            raise Unavailable("Trình duyệt không dựng được tệp — thử lại, hoặc báo quản trị nếu lỗi lặp lại")
        return out.read_bytes()


async def _chrome(html: str, mode: list[str], out_name: str, script: str = "") -> bytes:
    exe = chrome_binary()
    if exe is None:
        raise Unavailable("Máy chủ chưa cài trình duyệt để dựng PDF ấn phẩm — báo quản trị (cần Chromium trong ảnh API)")
    if len(html.encode()) > HTML_MAX_BYTES:
        raise Unavailable("Ấn phẩm quá nặng để dựng PDF (nhiều ảnh lớn) — bớt ảnh rồi thử lại")
    await _chrome_slot.acquire()   # chờ chỗ: huỷ ở đây chưa giữ gì ⇒ không có gì để nhả
    cancel = threading.Event()
    try:
        job = asyncio.ensure_future(asyncio.to_thread(_run_chrome, exe, html, mode, out_name, script, cancel))
    except BaseException:
        _chrome_slot.release()
        raise
    # Chỗ giới hạn nhả ĐÚNG MỘT LẦN, khi luồng chạy trình duyệt đã xong (``_run_chrome`` chỉ kết thúc sau khi diệt + thu cả nhóm tiến trình) — không nhả khi tác vụ chờ bị huỷ:
    # huỷ chỉ báo luồng diệt trình duyệt (``cancel``), còn trình duyệt cũ chưa thoát thì lượt sau vẫn phải chờ ⇒ không bao giờ hai trình duyệt cùng lúc.
    job.add_done_callback(lambda j: (_chrome_slot.release(), None if j.cancelled() else j.exception())[0])
    try:
        return await asyncio.shield(job)
    except asyncio.CancelledError:
        cancel.set()
        raise


async def html_to_pdf(html: str) -> bytes:
    """Trang HTML của bản xem ⇒ PDF giống hệt (khổ giấy do ``@page`` trong trang quyết). Phải là trang MỘT TỆP: ảnh + phông nhúng data URL, không tải gì ngoài."""
    pdf = await _chrome(html, ["--no-pdf-header-footer", "--print-to-pdf={out}"], "out.pdf")
    if not pdf.startswith(b"%PDF"):
        raise Unavailable("Trình duyệt trả tệp không phải PDF — thử lại")
    return pdf


async def html_to_png(html: str, width: int, height: int) -> bytes:
    """Trang HTML ⇒ ảnh PNG một khung ``width × height`` px (để xem / tự soát bố cục từng trang)."""
    return await _chrome(html, ["--screenshot={out}", f"--window-size={width},{height}"], "out.png")


async def html_measure(html: str, script: str, attr: str, *, size: tuple[int, int] | None = None) -> str:
    """Chạy MỘT đoạn script của máy chủ trong trang rồi đọc kết quả script ghi vào thuộc tính ``attr`` của thẻ ``<html>`` — để ĐO bố cục bằng chính trình duyệt sẽ in PDF
    (vd trang nào tràn). Cùng hàng rào như ``html_to_pdf``; chỉ script này chạy được (nonce). Script không ghi ``attr`` ⇒ ``Unavailable`` (không coi là "đo xong, không sao").
    ``size`` = cửa sổ đo (rộng, cao) px: cửa sổ mặc định của trình duyệt (~800 px) làm trang web thu nhỏ theo màn hình (media query), số đo lệch so với lúc in — đo bố cục khổ in thì đặt đủ rộng."""
    win = [f"--window-size={size[0]},{size[1]}"] if size else []
    dom = (await _chrome(html, ["--dump-dom", "--virtual-time-budget=8000", *win], "dom.html", script)).decode("utf-8", "replace")
    m = re.search(rf'<html[^>]*\s{re.escape(attr)}="([^"]*)"', dom)
    if m is None:
        raise Unavailable("Không đo được bố cục trang (trình duyệt không trả kết quả) — thử lại, hoặc báo quản trị nếu lỗi lặp lại")
    return m.group(1)
