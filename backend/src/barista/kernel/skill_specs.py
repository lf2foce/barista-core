"""Lời khai KỸ NĂNG MẶC ĐỊNH của gói (0198) + luật nội dung chung cho mọi kỹ năng — phần thuần (không DB, không thao tác), để ``barista.api.skill``
dùng được mà không kéo theo module lõi. Thao tác soạn / duyệt / cài: ``kernel/ai_skills.py``."""

import re
from dataclasses import dataclass
from typing import Any

KEY = re.compile(r"^[a-z][a-z0-9_]{1,59}$")
MAX_CHARS = 3000      # trần hướng dẫn một kỹ năng (ký tự) — không chuyển 20k token công cụ thành 20k token chỉ dẫn (0095)
TITLE_MAX = 80
WHEN_MAX = 240
TOOLS_MAX = 8         # công cụ khai trong một kỹ năng
LIST_MAX = 12         # số kỹ năng tối đa trong danh sách ngắn của MỘT lượt
OPEN_MAX = 2          # số kỹ năng model mở được trong một lượt
# giá trị chính sách không sống trong chữ của kỹ năng (một nguồn luật): phần trăm, số tiền — ngưỡng / mức phải đọc từ công cụ / cấu hình
POLICY_VALUE = re.compile(r"\d[\d.,]*\s*(?:%|phần\s*trăm|triệu|tr\b|tỷ|tỉ|nghìn|ngàn|k\b|đ\b|đồng|vnđ|vnd|usd|\$)", re.IGNORECASE)


@dataclass(frozen=True)
class SkillSpec:
    """Kỹ năng mặc định một gói khai (``barista.api.skill``) — nội dung review trong repo của gói."""
    key: str
    title: str
    when_to_use: str
    roles: tuple[str, ...]
    instructions: str
    tools: tuple[str, ...]


DECLARED: dict[str, SkillSpec] = {}
INSTALL = "core.install_skill"


def lint(title: str, when_to_use: str, instructions: str, roles: list[str] | tuple[str, ...], tools: list[str] | tuple[str, ...]) -> list[str]:
    """Lỗi nội dung (rỗng, quá dài, có giá trị chính sách) — cùng một luật cho bản của gói lẫn bản công ty soạn."""
    bad = []
    if not title.strip() or len(title) > TITLE_MAX:
        bad.append(f"tên kỹ năng 1–{TITLE_MAX} ký tự")
    if not when_to_use.strip() or len(when_to_use) > WHEN_MAX:
        bad.append(f"“khi dùng” 1–{WHEN_MAX} ký tự")
    if not instructions.strip() or len(instructions) > MAX_CHARS:
        bad.append(f"hướng dẫn 1–{MAX_CHARS} ký tự (đang {len(instructions)})")
    if not roles:
        bad.append("cần ít nhất một vai thấy kỹ năng")
    if len(tools) > TOOLS_MAX:
        bad.append(f"tối đa {TOOLS_MAX} công cụ")
    if hit := POLICY_VALUE.search(f"{when_to_use}\n{instructions}"):
        bad.append(f"có giá trị chính sách “{hit.group(0).strip()}” — tỷ lệ / mức tiền / ngưỡng đọc từ công cụ hoặc cấu hình, không chép vào kỹ năng")
    return bad


def declare(key: str, *, title: str, when_to_use: str, roles: tuple[str, ...], instructions: str, tools: tuple[str, ...] = ()) -> tuple[str, str, dict[str, Any]]:
    """Gói khai một kỹ năng mặc định ⇒ dòng cho ``Pack(records=…)``. Kiểm ngay lúc nạp gói (sai là lỗi khởi động, không đợi lúc cài)."""
    instructions = instructions.strip()
    if not KEY.match(key):
        raise ValueError(f"kỹ năng {key!r}: khoá dạng chữ thường_gạch_dưới (2–60 ký tự)")
    if key in DECLARED:
        raise ValueError(f"kỹ năng {key} khai hai lần")
    if bad := lint(title, when_to_use, instructions, roles, tools):
        raise ValueError(f"kỹ năng {key}: " + "; ".join(bad))
    DECLARED[key] = SkillSpec(key, title.strip(), when_to_use.strip(), tuple(roles), instructions, tuple(tools))
    return (INSTALL, key, {"key": key})
