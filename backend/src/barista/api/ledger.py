"""Sổ cái của lõi — gói chỉ KHAI tài khoản cố định của mình và đọc danh mục đã khai. Ghi sổ đi qua gói Tài chính (``acct.issue_receivable`` …),
không ghi thẳng."""

from barista.kernel.ledger import accounts, declare

__all__ = ["accounts", "declare"]
