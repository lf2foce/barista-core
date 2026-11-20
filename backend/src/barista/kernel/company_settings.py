"""CÀI ĐẶT CÔNG TY (quyết định 0128): công tắc bật / tắt một hành vi của gói, là DỮ LIỆU của từng công ty — không cứng trong mã (bất biến 5).

* Module khai công tắc một lần (``Module.setting(tên, nhãn, default=…, help=…)``) ⇒ khoá ``<module>.<tên>``. Mặc định đi KÈM GÓI của module: bật gói
  ⇒ ``admin.install_defaults`` ghi giá trị mặc định vào ``core.settings`` qua nhật ký cài (công ty đã đổi thì bật lại gói không ghi đè — như từ vựng 0124).
* Công ty chưa có dòng (bật gói TRƯỚC khi công tắc được khai) ⇒ giá trị mặc định khai trong code.
* Quản trị đổi trong app (``admin.set_setting``); module chỉ ĐỌC qua ``enabled`` — SQL của module không chạm ``core.settings`` (bất biến 3).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from barista.db import Conn

KEY = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
AI_FALLBACK_VENDOR = "core.ai_allow_fallback_vendor"   # khai ở kernel/core_ops.py; ``auth.load`` đọc vào ``Principal.ai_fallback`` (agent/llm.py: nhà cung cấp thứ hai)


@dataclass(frozen=True)
class Setting:
    """``label``: câu quản trị đọc (bật = điều câu nói xảy ra); ``help``: hệ quả khi bật / tắt; ``module``: module khai (gói = gói của module)."""
    key: str
    label: str
    help: str
    default: bool
    module: str


SETTINGS: dict[str, Setting] = {}


def declare(key: str, label: str, *, default: bool, help: str, module: str) -> Setting:
    if not KEY.match(key) or key in SETTINGS:
        raise ValueError(f"cài đặt {key} sai tên hoặc khai hai lần")
    if not key.startswith(module + "."):
        raise ValueError(f"cài đặt {key}: module {module} chỉ khai cài đặt của chính nó")
    SETTINGS[key] = Setting(key, label, help, default, module)
    return SETTINGS[key]


def on(values: Mapping[str, bool] | None, key: str) -> bool:
    """Công tắc ``key`` đang bật không, từ giá trị ĐÃ NẠP của công ty (``Principal.settings``) — chưa có dòng ⇒ mặc định khai trong code. Dùng ở chỗ
    không có kết nối (lọc màn theo ``Screen.feature``). Khoá chưa khai là lỗi lập trình."""
    s = SETTINGS.get(key)
    if s is None:
        raise KeyError(f"cài đặt {key} chưa khai (Module.setting)")
    return s.default if values is None else values.get(key, s.default)


async def values(conn: Conn, tenant_id: UUID) -> dict[str, bool]:
    """Giá trị công ty đã có dòng (chỉ khoá còn được khai)."""
    cur = await conn.execute("SELECT key, enabled FROM core.settings WHERE tenant_id = %s", (tenant_id,))
    return {r["key"]: bool(r["enabled"]) for r in await cur.fetchall() if r["key"] in SETTINGS}


async def enabled(conn: Conn, tenant_id: UUID, key: str) -> bool:
    """Công tắc ``key`` của công ty đang bật không (chưa có dòng ⇒ mặc định khai trong code). Khoá chưa khai là lỗi lập trình."""
    s = SETTINGS.get(key)
    if s is None:
        raise KeyError(f"cài đặt {key} chưa khai (Module.setting)")
    cur = await conn.execute("SELECT enabled FROM core.settings WHERE tenant_id = %s AND key = %s", (tenant_id, key))
    row = await cur.fetchone()
    return s.default if row is None else bool(row["enabled"])
