"""Khai báo MÔ TẢ của lệnh cho bản chạy thử chuẩn (ADR 0253) — kiểm tĩnh, không DB.

Người duyệt đọc thẻ trong vài giây: hồ sơ nào, đổi gì, vì sao, đảo được không. Hai việc phải do lệnh KHAI (máy không suy được từ ``risk`` — từng suy sai):
  (a) lệnh tiền / quyền mà trợ lý có thể soạn thành thẻ ⇒ có ``purpose`` (vì sao làm việc này) và đã khai đảo được / không;
  (b) lệnh công khai không còn ô nào để điền (nút bấm) ⇒ đã phân loại đảo được / không — luật xác nhận (``preview.confirm_rule``) dựa vào đó:
      đảo được ⇒ chạy ngay khi chạm; không đảo được ⇒ hộp nói hệ quả (nên ``irreversible`` luôn đi cùng ``effect``);
  (c) ``subject`` nếu khai thì trỏ loại đã khai ``m.subject`` và ô đầu vào có thật."""

import pytest

from barista.kernel import forms, loader, preview, registry
from barista.kernel.agent_policy import risk_of

loader.load()

# Chưa khai được — ghi rõ lý do; rút dần về rỗng.
PENDING_MODULES = {
    "papers": "ADR 0251 đang làm ở phiên khác — gộp sau",
}
PENDING_KERNEL: set[str] = set()   # lệnh của lõi ở kernel/ chưa khai (đã khai xong hết — giữ tập để ngoại lệ mới phải ghi lý do)


def _public() -> list[registry.Op]:
    return sorted((o for o in registry.OPS.values() if o.kind == "command" and not o.internal), key=lambda o: o.name)


def _fieldless(op: registry.Op) -> bool:
    return not [f for f in forms.field_specs(op) if not f["hidden"]]


def _pending(op: registry.Op) -> bool:
    return op.module in PENDING_MODULES or op.name in PENDING_KERNEL


MONEY_OR_AUTHORITY = [o for o in _public() if risk_of(o) in ("money", "authority") and o.agent != "deny"]
BUTTONS = [o for o in _public() if _fieldless(o) and not _pending(o)]


def test_command_lists_are_not_empty() -> None:
    assert len(MONEY_OR_AUTHORITY) >= 40 and len(BUTTONS) >= 40   # bắt khi cách liệt kê hỏng, không để test xanh vì danh sách rỗng


@pytest.mark.parametrize("op", MONEY_OR_AUTHORITY, ids=lambda o: o.name)
def test_money_and_authority_commands_the_agent_can_propose_explain_themselves(op: registry.Op) -> None:
    assert op.purpose.strip(), f"{op.name}: thiếu purpose= (vì sao người ta làm việc này — một câu nghiệp vụ)"
    assert preview.reversibility(op)["ok"] is not None, f"{op.name}: chưa khai reversible= / irreversible=True + effect="


@pytest.mark.parametrize("op", BUTTONS, ids=lambda o: o.name)
def test_buttons_without_fields_are_classified(op: registry.Op) -> None:
    assert preview.reversibility(op)["ok"] is not None, f"{op.name}: nút không còn ô nào mà chưa phân loại đảo được / không"


@pytest.mark.parametrize("op", [o for o in _public() if o.irreversible], ids=lambda o: o.name)
def test_irreversible_commands_say_what_happens(op: registry.Op) -> None:
    assert op.effect.strip(), f"{op.name}: irreversible=True phải đi cùng effect= (hệ quả bằng chữ người đọc)"


@pytest.mark.parametrize("op", [o for o in _public() if o.subject is not None], ids=lambda o: o.name)
def test_declared_subject_points_at_a_real_kind_and_field(op: registry.Op) -> None:
    assert op.subject is not None
    kind, field = op.subject
    assert kind in registry.SUBJECTS, f"{op.name}: loại {kind} chưa khai bằng m.subject"
    assert field in op.input.model_fields, f"{op.name}: ô {field} không có trong đầu vào"


def test_pending_exceptions_are_real_and_still_needed() -> None:
    names = {o.name for o in _public()}
    assert PENDING_KERNEL <= names
    for n in PENDING_KERNEL:   # đã khai xong thì bỏ khỏi tập ngoại lệ
        assert preview.reversibility(registry.OPS[n])["ok"] is None, f"{n}: đã khai — xoá khỏi PENDING_KERNEL"
    assert set(PENDING_MODULES) <= {o.module for o in _public()}
