"""Trường tuỳ biến theo công ty (0060, 1.50) — phần gói cần ngoài ``Module.extensible`` / ``Module.command(extra=)``: chữ hiện của các trường công ty
đang dùng trên một bản ghi (``shown(ctx.p.fields, "<đối tượng>", row["extra"])`` ⇒ [(nhãn, chữ)]). Kiểm kiểu và gộp ô ``x_<khoá>`` do lõi làm ở executor."""

from barista.kernel.extra_fields import shown

__all__ = ["shown"]
