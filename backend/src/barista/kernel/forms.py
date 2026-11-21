"""Form của một lệnh — sinh từ model Pydantic của lệnh (nguồn duy nhất), không viết tay ở app."""

import typing
from collections.abc import Mapping
from types import NoneType, UnionType
from typing import Any, Literal

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined, to_jsonable_python

from barista.kernel import extra_fields, vocab
from barista.kernel.fields import validator_kind
from barista.kernel.registry import Op, table_row_model


def _strip_optional(t: Any) -> Any:
    if typing.get_origin(t) in (UnionType, typing.Union):
        args = [a for a in typing.get_args(t) if a is not NoneType]
        if len(args) == 1:
            return args[0]
    return t


def _infer(f: FieldInfo, key: str) -> tuple[str, dict[str, str] | None]:
    t = _strip_optional(f.annotation)
    metadata = list(f.metadata)
    if typing.get_origin(t) is typing.Annotated:  # Money | None: chú thích nằm trong Optional, không ở f.metadata
        t, *extra = typing.get_args(t)
        metadata += extra
    if typing.get_origin(t) is Literal:
        return "select", {str(a): str(a) for a in typing.get_args(t)}
    if typing.get_origin(t) is list:
        inner = typing.get_args(t)[0] if typing.get_args(t) else str
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            return "rows", None   # danh sách bản ghi (vd các việc của kế hoạch) ⇒ bảng nhiều dòng, ô con khai ở "fields"
        if typing.get_origin(inner) is Literal:
            return "multiselect", {str(a): str(a) for a in typing.get_args(inner)}
        return "multiselect", None
    if k := validator_kind(metadata):
        return k, None
    if t is bool:
        return "bool", None
    if t is int or t is float:
        return ("money" if key.endswith("_vnd") else "number"), None
    if getattr(t, "__name__", "") == "UUID":
        return "id", None
    if getattr(t, "__name__", "") == "date":
        return "date", None
    return "text", None


def field_specs(op: Op, terms: Mapping[str, str] | None = None, fields: Mapping[str, tuple[Any, ...]] | None = None) -> list[dict[str, Any]]:
    """Ô của form. ``terms`` = nhãn riêng của công ty (``Principal.vocab``, 0124) — chữ trong ô (nhãn, gợi ý, ví dụ, lựa chọn) đã đổi dấu nhãn; không
    truyền ⇒ nhãn trung tính (không bao giờ lộ dấu ``[[…]]``). ``fields`` = trường tuỳ biến của công ty (``Principal.fields``, 0060): lệnh khai
    ``extra=`` có thêm ô ``x_<khoá>`` sau các ô module khai; ô ``extra`` thô không bao giờ hiện."""
    return vocab.apply(_with_extra(op, _model_specs(op.input), fields), terms)


def _with_extra(op: Op, specs: list[dict[str, Any]], fields: Mapping[str, tuple[Any, ...]] | None) -> list[dict[str, Any]]:
    if not op.extra:
        return specs
    return [s for s in specs if s["key"] != "extra"] + extra_fields.specs(extra_fields.of(fields, op.extra))


def import_specs(op: Op, terms: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Ô để KHỚP CỘT khi nhập Excel: đầu vào lệnh — hoặc, với lệnh nhập cả bảng (``Op.import_table``, 1.48), ô của MÔ HÌNH DÒNG."""
    if op.import_table is None:
        return field_specs(op, terms)
    row = table_row_model(op.input, op.import_table)
    assert row is not None   # đã kiểm lúc khai (validate_import_table)
    return vocab.apply(_model_specs(row), terms)


def _model_specs(model: type[BaseModel]) -> list[dict[str, Any]]:
    out = []
    for key, f in model.model_fields.items():
        extra = f.json_schema_extra if isinstance(f.json_schema_extra, dict) else {}
        ui: dict[str, Any] = dict(extra.get("ui") or {})  # type: ignore[arg-type]
        kind, inferred_opts = _infer(f, key)
        options = ui.get("options") or inferred_opts
        # ô chữ có danh sách lựa chọn ⇒ là ô CHỌN (form vẽ danh sách; trợ lý nhận enum) — trừ khi khai kind rõ
        kind = ui.get("kind") or ("select" if options and kind == "text" else kind)
        spec: dict[str, Any] = {
            "key": key,
            "label": f.title or key,
            "kind": kind,
            "required": f.is_required(),
            "help": f.description or "",
            # ô mã (id) thường ẩn — điền từ dòng / thẻ; có danh sách chọn (options_query) thì HIỆN để người chọn khi mở form trống (vd "Mở hồ sơ")
            "hidden": bool(ui.get("hidden")) or (kind == "id" and not ui.get("options_query")),
        }
        if f.default is not PydanticUndefined and f.default is not None:
            spec["default"] = to_jsonable_python(f.default)
        if options:
            spec["options"] = [{"value": k, "label": v} for k, v in options.items()]
        if ui.get("options_query"):
            spec["options_query"] = ui["options_query"]
            if ui.get("options_with"):   # danh sách chọn tuỳ giá trị các trường này (chỉ THÊM trường — app cũ tải danh sách không kèm gì)
                spec["options_with"] = ui["options_with"]
            if ui.get("options_args"):   # chỉ THÊM: như options_with nhưng đổi tên tham số (1.61); app cũ bỏ qua ⇒ danh sách tải một lần như trước
                spec["options_args"] = ui["options_args"]
        if "example" in ui:
            spec["example"] = to_jsonable_python(ui["example"])
        if ui.get("group"):
            spec["group"] = ui["group"]   # chỉ thêm trường: nhóm gấp lại (app cũ bỏ qua ⇒ ô hiện hết như trước)
        if ui.get("placeholder"):
            spec["placeholder"] = ui["placeholder"]   # chỉ thêm trường: chữ mờ của ô trống
        if ui.get("primary"):
            spec["primary"] = True   # chỉ thêm trường: ô lọc chính của màn (app cũ bỏ qua ⇒ bày như trước)
        if ui.get("aliases"):
            spec["aliases"] = ui["aliases"]   # tên cột khác khi nhập Excel (chỉ thêm trường — app không cần đọc)
        if kind == "rows":   # ô con của mỗi dòng — app vẽ bảng thêm / bớt dòng; giá trị gửi lên là danh sách object
            spec["fields"] = _model_specs(typing.get_args(_strip_optional(f.annotation))[0])
        out.append(spec)
    return out


# Truy vấn "Soạn giúp" của form theo ý định (ADR 0250) — một cửa cho mọi lệnh khai ``intent=``
DRAFT_QUERY = "core.draft_input"


def form_spec(op: Op, terms: Mapping[str, str] | None = None, fields: Mapping[str, tuple[Any, ...]] | None = None) -> dict[str, Any]:
    spec: dict[str, Any] = {"command": op.name, "title": op.title, "description": op.form_help, "importable": op.importable,
                            "fields": _with_extra(op, _model_specs(op.input), fields)}
    # LUẬT XÁC NHẬN Ở MỘT CHỖ (ADR 0253, ``kernel/preview.confirm_rule``): "none" = đảo được ⇒ nút không ô nào chạy ngay khi chạm (báo kết quả, kèm Hoàn tác);
    # "consequence" = phá huỷ / không đảo được ⇒ hộp nói HỆ QUẢ bằng ``effect``. Chỉ THÊM trường — app cũ hỏi câu chung như trước.
    from barista.kernel.preview import confirm_rule  # nạp muộn: preview đọc field_specs của file này

    spec["confirm"], effect = confirm_rule(op)
    if effect:
        spec["effect"] = effect
    if op.intent:
        # chỉ THÊM trường (bất biến 7): app chưa biết ⇒ vẽ đủ ô như cũ. ``intent`` = ô hỏi trước; ``draft`` = truy vấn soạn phần còn lại
        spec["intent"] = list(op.intent)
        spec["draft"] = DRAFT_QUERY
    return vocab.apply(spec, terms)
