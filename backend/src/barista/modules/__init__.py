"""Module của các gói trong repo (một file hoặc một package — 0088). Gói nào được nạp và theo thứ tự nào do ``kernel/loader.py`` quyết
(entry point ``barista.packs`` — 0130; gói trong repo khai ở ``backend/pyproject.toml``). Import bất kỳ module nào ở đây cũng nạp đủ mọi gói
đã cài rồi ``registry.validate()``."""

from barista.kernel import loader

loader.load()
