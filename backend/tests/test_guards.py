"""Test CANH GIỮ (ROADMAP N3): ba chỗ mà thêm đồ mới rất dễ quên một bước, nên máy phải nhắc thay người.

1. Điểm cắm (``m.plug`` / ``m.plug_action``): mỗi điểm phải có trong runbook 12 và phải có module chủ thật sự hỏi nó.
2. Quy ước tên thao tác: ``<module>.<tên_snake_case>``; truy vấn không mang động từ ghi, lệnh không mang động từ đọc (ngoại lệ ghi ở danh sách
   cho phép, mỗi dòng một lý do — thêm một dòng = đã xem từng chỗ, không phải tắt test).
3. Độ dài mô tả công cụ: mô tả quá trần bị ``chat._clip`` cắt khi gửi model — người viết mô tả dài phải biết là phần đuôi không tới được model.

Không có màn nào liệt kê điểm cắm (07/11/2026): nguồn duy nhất là ``registry.PLUGS`` / ``registry.ACTION_PLUGS``, nên "danh sách" ở đây là chính registry
và runbook 12; thêm màn liệt kê thì thêm kiểm khớp ở đây.
"""

import re
from pathlib import Path

import barista.main  # noqa: F401 — nạp mọi module để registry đủ
from barista.config import settings
from barista.kernel import registry
from tests.module_src import declared, module_files

RUNBOOK = Path(__file__).resolve().parents[2] / "docs" / "runbooks" / "12-them-mot-goi.md"


# ── 1. Điểm cắm ──

def _backticked(text: str) -> set[str]:
    return set(re.findall(r"`([^`\s]+)`", text))


def test_every_plug_point_is_in_runbook_12() -> None:
    documented = _backticked(RUNBOOK.read_text())
    points = sorted(set(registry.PLUGS) | set(registry.ACTION_PLUGS))
    assert len(points) >= 5   # test không rỗng (chỉ gói của lõi: 6 điểm có gói cắm; có gói ngành: thêm các điểm của Khách hàng / Giấy tờ / Tài chính)
    missing = [p for p in points if p not in documented]
    assert not missing, f"điểm cắm chưa có trong docs/runbooks/12-them-mot-goi.md (bảng “Góp vào năng lực chung” / “Thay nút của màn chung”): {missing}"


def test_runbook_12_names_no_plug_point_that_no_longer_exists() -> None:
    """Chiều ngược: runbook liệt kê điểm của module nào thì điểm đó phải còn trong registry (đổi tên / xoá điểm mà quên sửa tài liệu)."""
    rows = [ln for ln in RUNBOOK.read_text().splitlines() if "m.plug(" in ln or "m.plug_action(" in ln]
    assert rows
    known = set(registry.PLUGS) | set(registry.ACTION_PLUGS)
    # điểm module chủ còn HỎI (``ctx.plugs`` / ``ctx.plugged``) là điểm còn sống dù chưa gói nào ĐANG CÀI cắm vào (gói cắm nằm ngoài repo lõi — 0155)
    for key, files in module_files().items():
        known |= {t for f in files for t in re.findall(r'plug(?:s|ged)\("([a-z_]+\.[a-z_]+)"', f.read_text()) if t.split(".")[0] == key}
        known |= {t for f in files for t in re.findall(r'slot="([a-z_]+\.[a-z_]+)"', f.read_text()) if t.split(".")[0] == key}   # nút thay được (0124)
    ghost = sorted(t for ln in rows for t in _backticked(ln)
                   if t.split(".")[0] in registry.MODULES and t not in known and t not in registry.OPS and re.fullmatch(r"[a-z_]+\.[a-z_]+", t))
    assert not ghost, f"runbook 12 nêu điểm cắm không còn trong code: {ghost}"


def test_every_plug_point_is_actually_asked_by_its_owner_module() -> None:
    """Điểm cắm không ai hỏi = gói cắm vào mà không bao giờ chạy (êm, không lỗi). Module chủ phải gọi ``ctx.plugs("<điểm>")``; điểm nút phải có
    ``Action(slot="<điểm>")`` (``registry.validate`` đã kiểm phía nút, ở đây kiểm phía dữ liệu)."""
    src = {key: "\n".join(f.read_text() for f in files) for key, files in module_files().items()}
    unasked = [p for p in sorted(registry.PLUGS)
               if f'plugs("{p}"' not in src.get(p.split(".")[0], "") and f'plugged("{p}"' not in src.get(p.split(".")[0], "")]
    assert not unasked, f"điểm cắm mà module chủ không hỏi (ctx.plugs): {unasked}"
    for p in registry.PLUGS:
        assert p.split(".")[0] in registry.MODULES, f"điểm cắm {p}: không có module chủ {p.split('.')[0]}"


# ── 2. Quy ước tên thao tác ──

SNAKE = re.compile(r"[a-z][a-z0-9]*(_[a-z0-9]+)*")
# Động từ GHI không được mở đầu tên một TRUY VẤN (truy vấn không đổi dữ liệu); động từ ĐỌC không được mở đầu tên một LỆNH.
WRITE_VERBS = {"create", "add", "set", "update", "upsert", "delete", "remove", "save", "record", "issue", "approve", "cancel", "close", "open", "post",
               "send", "mark", "assign", "confirm", "reject", "void", "sign", "publish", "invite", "merge", "book", "submit"}   # không có "link": ở đây là DANH TỪ (link công khai, link_view)
READ_VERBS = {"list", "get", "show", "find", "search", "view", "read", "options", "summary"}
NAME_MAX = 40   # phần sau dấu chấm; tên dài hơn là dấu hiệu gộp hai việc (lúc viết, dài nhất 30)

# Ngoại lệ có chủ đích: tên → vì sao đúng. Đổi tên công khai làm vỡ hợp đồng Mac / web / trợ lý nên để nguyên; mục thừa (không còn vi phạm) ⇒ đỏ.
WRITE_VERB_QUERIES = {
    "apps.open": "“open” = mở app để xem (đọc lại từng nguồn bằng quyền người xem); tên công khai app Mac / web đã gọi",
    "parties.merge_candidates": "“merge” + danh từ: danh sách cặp NGHI trùng để người quyết định gộp (đọc thuần); lệnh gộp là parties.merge",
}
READ_VERB_COMMANDS = {
    "core.read_notifications": "“read” là động từ đánh dấu ĐÃ ĐỌC (ghi trạng thái thông báo), không phải đọc dữ liệu; tên công khai app đã gọi",
}
# Tên của gói ngoài repo đang cài: gói tự khai trong core_guards.json của nó (0191)
WRITE_VERB_QUERIES |= declared("write_verb_queries")
READ_VERB_COMMANDS |= declared("read_verb_commands")


def _verb(name: str) -> str:
    return name.split(".", 1)[1].split("_")[0]


def test_operation_names_follow_the_shape() -> None:
    bad = []
    for name, op in sorted(registry.OPS.items()):
        mod, _, short = name.partition(".")
        if not SNAKE.fullmatch(mod) or not SNAKE.fullmatch(short):
            bad.append(f"{name}: dạng <module>.<tên_snake_case> (chữ thường, số, gạch dưới; không gạch kép / đầu số)")
        elif mod != op.module:
            bad.append(f"{name}: tiền tố không phải module của thao tác ({op.module})")
        elif short.startswith(f"{mod}_"):
            bad.append(f"{name}: lặp tên module trong tên thao tác — bỏ “{mod}_”")
        elif len(short) > NAME_MAX:
            bad.append(f"{name}: tên dài {len(short)} > {NAME_MAX} — chia việc hoặc đặt tên ngắn hơn")
        elif not (op.title or "").strip():
            bad.append(f"{name}: thiếu title (tên người đọc thấy)")
    assert not bad, "\n".join(bad)


def test_queries_do_not_start_with_a_write_verb_and_commands_not_with_a_read_verb() -> None:
    bad = []
    for name, op in sorted(registry.OPS.items()):
        if op.kind == "query" and _verb(name) in WRITE_VERBS and name not in WRITE_VERB_QUERIES:
            bad.append(f"truy vấn {name} mở đầu bằng động từ ghi “{_verb(name)}” — đổi tên, hoặc thêm vào WRITE_VERB_QUERIES kèm lý do")
        if op.kind == "command" and _verb(name) in READ_VERBS and name not in READ_VERB_COMMANDS:
            bad.append(f"lệnh {name} mở đầu bằng động từ đọc “{_verb(name)}” — đổi tên, hoặc thêm vào READ_VERB_COMMANDS kèm lý do")
        if name.endswith("_options") and op.kind != "query":
            bad.append(f"{name}: tên *_options là danh sách lựa chọn của form — phải là truy vấn")
        if ".plug_" in name and not (op.kind == "query" and op.internal):
            bad.append(f"{name}: plug_* là truy vấn nội bộ do m.plug sinh ra")
    assert not bad, "\n".join(bad)


def test_the_name_allowlists_hold_no_stale_entry() -> None:
    loaded = [*WRITE_VERB_QUERIES, *READ_VERB_COMMANDS]   # dòng của gói ngoài chỉ có khi gói đang cài (gói tự khai) ⇒ mọi dòng đều xét
    stale = [n for n in WRITE_VERB_QUERIES if n in loaded and ((op := registry.OPS.get(n)) is None or op.kind != "query" or _verb(n) not in WRITE_VERBS)]
    stale += [n for n in READ_VERB_COMMANDS if n in loaded and ((op := registry.OPS.get(n)) is None or op.kind != "command" or _verb(n) not in READ_VERBS)]
    assert not stale, f"mục thừa trong danh sách cho phép: {stale}"


# ── 3. Độ dài mô tả công cụ ──

# Mô tả = docstring của handler (op.description). Lúc viết dài nhất là ``memory.remember`` = 798 ký tự (sau khi gộp khoảng trắng). Trần = 1000 =
# ``settings.agent_tool_desc_chars``: quá trần là bị ``chat._clip`` cắt ở câu gần nhất khi gửi model — phần đuôi (thường là lời dặn quan trọng) mất.
DESC_MAX = 1000


def test_no_tool_description_is_longer_than_what_the_model_is_sent() -> None:
    assert settings.agent_tool_desc_chars >= DESC_MAX, "trần gửi model thấp hơn trần canh giữ: mô tả hợp lệ vẫn bị cắt"
    long = sorted(((len(re.sub(r"\s+", " ", op.description or "")), name) for name, op in registry.OPS.items()), reverse=True)
    assert long[0][0] > 0 and long[0][0] <= DESC_MAX * 0.9, f"mô tả dài nhất {long[0]} đã sát trần {DESC_MAX} — nâng trần có chủ đích hoặc rút gọn"
    over = [f"{name} ({n} ký tự)" for n, name in long if n > DESC_MAX]
    assert not over, (f"mô tả công cụ dài quá {DESC_MAX} ký tự — bị cắt khi gửi model: {over}. Rút gọn (chi tiết ở ADR / runbook), "
                      "hoặc đưa lời dặn quan trọng lên đầu")


def test_only_the_connector_framework_opens_a_webhook_door() -> None:
    """MỘT cách nối ra hệ ngoài (ADR 0244): cửa webhook VÀO chỉ là cửa chung của khung kết nối (``connectors.receive_hook``). Gói — kể cả gói của đối tác
    đang cài — nhận webhook qua bộ nối (``Connector.webhook`` + ``hook_parse``), không tự mở ``Module.inbound`` với bảng khoá / kết nối riêng."""
    assert set(registry.INBOUND) <= {"connectors"}, f"gói tự mở cửa webhook: {sorted(set(registry.INBOUND) - {'connectors'})} — dùng khung kết nối (runbook 22)"
    assert set(registry.INBOUND_VERIFY) <= {"connectors"}, sorted(registry.INBOUND_VERIFY)
