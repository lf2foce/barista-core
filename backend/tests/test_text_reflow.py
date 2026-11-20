"""Hiển thị tài liệu: dòng bị ngắt cứng giữa câu (PDF / gõ theo khổ giấy) được nối lại; danh sách, đề mục, đoạn mới giữ nguyên."""

from barista.kernel.text import reflow


def test_hard_wrapped_sentences_are_joined_but_lists_and_paragraphs_stay() -> None:
    src = ("chủ đầu tư chỉ được thu tiền đặt cọc không\nquá 5% giá bán.\nPhụ lục I Nghị định\n96/2024/NĐ-CP.\nCác bước:\n- Một\n- Hai\n"
           "1. Mục một\na) Điểm a\n\nĐoạn mới")
    assert reflow(src) == ("chủ đầu tư chỉ được thu tiền đặt cọc không quá 5% giá bán.\nPhụ lục I Nghị định 96/2024/NĐ-CP.\nCác bước:\n- Một\n- Hai\n"
                           "1. Mục một\na) Điểm a\n\nĐoạn mới")
