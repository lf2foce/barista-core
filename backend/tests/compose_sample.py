"""Dữ liệu mẫu GIẢ cho hai ấn phẩm nhiều trang (Hồ sơ chào bán 10 trang, Tóm tắt 4 trang) — dùng cho test + chụp ảnh trang. Tên / số / liên hệ đều giả (SĐT đầu 010);
ảnh là bộ ảnh minh hoạ của lõi (``compose/stock``)."""

from typing import Any

from barista.modules.compose import stock

SRC = {"query": "mau.du_lieu", "title": "Dữ liệu mẫu", "input": {}, "at": "2026-11-21T09:00:00+07:00"}
BRAND: dict[str, Any] = {"primary_color": "1F5F4A", "font": "Arial", "footer": "Công ty Mẫu · mau.example · Tài liệu bảo mật", "logo": None, "logo_type": ""}


def _k(label: str, value: Any, fmt: str = "text") -> dict[str, Any]:
    return {"label": label, "value": value, "format": fmt}


def _kpis(title: str, *ks: dict[str, Any], estimate: bool = False) -> dict[str, Any]:
    return {"type": "data", "origin": "data", "flags": [], "data": {"title": title, "note": "", "total_rows": 0, "columns": [], "rows": [], "kpis": list(ks), "source": SRC,
                                                                     **({"estimate": True} if estimate else {})}}


def _table(title: str, cols: list[tuple[str, str, str]], rows: list[dict[str, Any]], estimate: bool = False) -> dict[str, Any]:
    return {"type": "data", "origin": "data", "flags": [], "data": {"title": title, "note": "", "total_rows": len(rows), "kpis": [], "source": SRC,
                                                                     "columns": [{"key": k, "label": lb, "format": f} for k, lb, f in cols], "rows": rows,
                                                                     **({"estimate": True} if estimate else {})}}


def _h(text: str, slot: str, level: int = 1) -> dict[str, Any]:
    return {"type": "heading", "text": text, "level": level, "origin": "ai", "flags": [], "slot": slot}


def _p(text: str, slot: str) -> dict[str, Any]:
    return {"type": "paragraph", "text": text, "level": 1, "origin": "ai", "flags": [], "slot": slot}


def _img(stock_id: str, slot: str, caption: str = "") -> dict[str, Any]:
    it = stock.get(stock_id)
    assert it is not None
    return {**stock.block(it, caption), "flags": [], "slot": slot}


def _ids(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": f"b{i + 1}", **b} for i, b in enumerate(blocks)]


def memo10() -> tuple[str, list[dict[str, Any]]]:
    unit = [("kind", "Loại căn", "text"), ("n", "Số căn còn", "number"), ("area", "Diện tích", "number"), ("price", "Giá từ", "money")]
    units = [{"kind": "Studio", "n": 24, "area": 32, "price": 1_850_000_000}, {"kind": "1 phòng ngủ", "n": 54, "area": 48, "price": 2_850_000_000},
             {"kind": "2 phòng ngủ", "n": 113, "area": 70, "price": 4_130_000_000}, {"kind": "3 phòng ngủ", "n": 31, "area": 96, "price": 6_200_000_000}]
    dist = [("to", "Điểm đến", "text"), ("km", "Khoảng cách (km)", "number"), ("min", "Thời gian (phút)", "number")]
    far = [{"to": "Trung tâm thành phố", "km": 7.5, "min": 20}, {"to": "Sân bay quốc tế", "km": 18, "min": 35}, {"to": "Ga trung tâm", "km": 9, "min": 25}, {"to": "Trường quốc tế", "km": 1.2, "min": 5}]
    comps = [("name", "Dự án so sánh", "text"), ("price", "Giá bán TB / m²", "money"), ("rate", "Tỷ lệ bán", "percent"), ("year", "Năm bàn giao", "text")]
    cmp_rows = [{"name": "Dự án A", "price": 62_000_000, "rate": 88, "year": "2025"}, {"name": "Dự án B", "price": 58_500_000, "rate": 74, "year": "2026"},
                {"name": "Dự án C", "price": 66_000_000, "rate": 91, "year": "2024"}]
    fin = [("item", "Chỉ tiêu", "text"), ("v1", "Năm 1", "money"), ("v2", "Năm 2", "money"), ("v3", "Năm 3", "money")]
    fin_rows = [{"item": "Doanh thu", "v1": 420_000_000_000, "v2": 610_000_000_000, "v3": 780_000_000_000}, {"item": "Lợi nhuận gộp", "v1": 130_000_000_000, "v2": 214_000_000_000, "v3": 296_000_000_000}]
    blocks = [
        _img("do-thi-bia-a4-ngang", "p1"), _p("Hồ sơ chào bán dành cho nhà đầu tư tổ chức", "p1"),
        _kpis("Cốt lõi", _k("Tổng số căn", 1260, "number"), _k("Diện tích đất", 8.4, "number"), _k("Giá từ", 1_850_000_000, "money"), _k("Bàn giao", "Quý 4/2027")),
        _h("Tài liệu này giúp nhà đầu tư quyết định có đi tiếp hay không", "p2"),
        _p("Số liệu trong tài liệu lấy từ hệ thống của công ty tại thời điểm in; số ước tính được ghi rõ. Không dùng cho mục đích khác khi chưa có đồng ý bằng văn bản của bên bán.", "p2"),
        _p("Mọi số liệu thị trường là thông tin tham khảo, có thể thay đổi.", "p2"),
        _h("Dự án nằm đúng nơi nhu cầu ở thực đang tăng nhanh nhất", "p3"),
        _p("Khu căn hộ nằm ven mặt nước, cách trung tâm 20 phút, có đủ trường học, y tế và công viên trong bán kính hai ki-lô-mét.", "p3"),
        _p("Cấu trúc sản phẩm thiên về căn vừa túi tiền, hợp nhóm khách mua để ở.", "p3"),
        _kpis("Tóm tắt", _k("Tổng số căn", 1260, "number"), _k("Đã bán", 74, "percent"), _k("Giá bán TB", 62_000_000, "money"), _k("Chủ đầu tư", "Công ty Mẫu")),
        _h("Sáu điểm khiến khu căn hộ khác biệt so với nhóm cạnh tranh", "p4"), _img("duong-pho-dai-3x1", "p4"),
        _kpis("Điểm nổi bật", _k("Mặt nước", "12 ha"), _k("Cây xanh", "40%"), _k("Tiện ích nội khu", "18 loại"), _k("Mật độ xây dựng", "28%"), _k("Chỗ đỗ xe", "1,1 / căn"), _k("Trường học", "Trong 1 km")),
        _h("Vị trí cách trung tâm 20 phút và sát tuyến giao thông mới", "p5"), _img("do-thi-bia-16x9", "p5"),
        _p("Tuyến vành đai sắp thông xe giúp rút ngắn thời gian tới sân bay và ga trung tâm.", "p5"), _table("Khoảng cách", dist, far),
        _h("Tài sản gồm bốn loại căn, hợp cả nhu cầu ở lẫn cho thuê", "p6"), _img("phong-khach-16x9", "p6"), _img("phong-ngu-1x1", "p6"), _table("Cơ cấu căn", unit, units),
        _h("Cầu thực vượt cung mới ba năm liền ở khu vực này", "p7"), _img("ho-boi-tien-ich-16x9", "p7"),
        _p("Nguồn cung mới tập trung ở phân khúc cao cấp, trong khi căn tầm trung ngày càng khan.", "p7"),
        _kpis("Thị trường", _k("Cung mới / năm", "9.800 căn"), _k("Hấp thụ", 82, "percent"), _k("Tăng giá / năm", 9.5, "percent")),
        _h("Giá chào của dự án nằm giữa nhóm so sánh và còn dư địa tăng", "p8"), _table("Dự án so sánh", comps, cmp_rows),
        _h("Dòng tiền ba năm đầu đủ trả nợ vay và còn dư", "p9"), _kpis("Chỉ báo", _k("Biên lợi nhuận gộp", 31, "percent"), _k("Vốn vay / tổng đầu tư", 40, "percent"), estimate=True),
        _table("Tài chính chỉ báo", fin, fin_rows, estimate=True),
        _h("Bốn bước từ quan tâm tới ký kết trong sáu tuần", "p10"), _p("Gửi thư quan tâm, ký cam kết bảo mật, xem dữ liệu chi tiết, chào giá ràng buộc.", "p10"),
        _p("Liên hệ để nhận bộ dữ liệu đầy đủ", "footer"), _kpis("Người phụ trách", _k("Họ tên", "Nguyễn Văn Mẫu"), _k("Điện thoại", "0100000000"), _k("Email", "mau@mau.example")),
    ]
    blocks[-2]["slot"] = blocks[-1]["slot"] = "footer"
    return "Khu căn hộ ven nước", _ids(blocks)


def teaser4() -> tuple[str, list[dict[str, Any]]]:
    market = [("seg", "Phân khúc", "text"), ("price", "Giá TB / m²", "money"), ("rate", "Hấp thụ", "percent")]
    rows = [{"seg": "Tầm trung", "price": 42_000_000, "rate": 86}, {"seg": "Cao cấp", "price": 78_000_000, "rate": 61}]
    fin = [("item", "Chỉ tiêu", "text"), ("v", "Giá trị", "money")]
    blocks = [
        _img("do-thi-bia-16x9", "p1"), _p("Tóm tắt chào bán dành cho nhà đầu tư", "p1"),
        _kpis("Cốt lõi", _k("Tổng số căn", 1260, "number"), _k("Giá từ", 1_850_000_000, "money"), _k("Bàn giao", "Quý 4/2027")),
        _h("Khu căn hộ vừa túi tiền ở nơi nhu cầu thực đang tăng", "p2"), _img("toa-nha-3x4", "p2"),
        _p("Dự án gồm 1.260 căn, tập trung loại một và hai phòng ngủ cho khách mua để ở.", "p2"),
        _kpis("Điểm nổi bật", _k("Mặt nước", "12 ha"), _k("Cây xanh", "40%"), _k("Tiện ích", "18 loại"), _k("Đã bán", 74, "percent")),
        _h("Cách trung tâm 20 phút, cầu thực vượt cung mới ba năm liền", "p3"), _img("sanh-16x9", "p3"), _p("Tuyến vành đai sắp thông xe giúp rút ngắn thời gian tới sân bay.", "p3"),
        _table("Thị trường", market, rows),
        _h("Dòng tiền chỉ báo đủ trả nợ vay, bước tiếp theo là xem dữ liệu chi tiết", "p4"),
        _table("Chỉ báo tài chính", fin, [{"item": "Doanh thu năm 1", "v": 420_000_000_000}, {"item": "Lợi nhuận gộp năm 1", "v": 130_000_000_000}], estimate=True),
        _p("Liên hệ để nhận bộ dữ liệu đầy đủ", "footer"), _kpis("Người phụ trách", _k("Họ tên", "Nguyễn Văn Mẫu"), _k("Điện thoại", "0100000000")),
    ]
    blocks[-1]["slot"] = "footer"
    return "Khu căn hộ ven nước", _ids(blocks)
