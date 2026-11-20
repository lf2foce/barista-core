"""Khớp + xếp hạng cho TÌM NHANH ⌘K (0087) và ô lọc "Tìm" của màn danh sách — MỘT định nghĩa cho mọi nguồn, lõi lẫn gói.

Cách khớp: từ khoá gấp chữ (bỏ dấu, chữ thường — ``text.fold``) tách thành TỪ; dòng khớp khi MỌI từ có trong chuỗi gấp của các trường nguồn khai
(``core.vn_fold``, base/097) — "nguyen thi ly" khớp "Nguyễn Thị Lý" lẫn "Lý Nguyễn Thị" (tên nhập ngược), "sgb e10" khớp căn E1015 dự án SGB. SĐT: chỉ
khi người gõ toàn chữ số (≥ 3) — "E608" không còn khớp khách có "608" trong số.

Xếp hạng (số nhỏ lên trước), trên trường CHÍNH (các trường ``rank`` nêu) và chuỗi gộp:
  0 khớp ĐÚNG cả trường ("GD-SGB-E608", "Nguyễn Thị Lý")
  1 trường bắt đầu bằng từ khoá, trọn từ ("Nguyễn Thị Chi" cho "nguyen thi")
  2 mọi từ là TRỌN TỪ ở đâu đó ("Thanh Bình Nguyễn Thị")
  3 trường bắt đầu bằng từ khoá, từ cuối dở ("Nguyen Thinh", "SGB-E107" cho "sgb-e10"; đuôi SĐT cho "6499")
  4 mọi từ khớp ở ĐẦU một từ
  5 chỉ chứa
cùng hạng thì trường chính ngắn hơn (gần từ khoá hơn) lên trước — nguồn thêm tiêu chí sau (mới nhất…).

Điều kiện chạy dưới RLS của người tìm như mọi câu SQL của module (không chỉ mục trigram: xem base/097). Tham số đặt tên ``_…`` — nguồn gộp
``{**t.params, …}`` với tham số riêng của mình (kiểu ``%(tên)s``)."""

import re
from typing import Any, LiteralString

from barista.kernel.fields import phone_search
from barista.kernel.text import fold

ROWS = 8            # mỗi nhóm hiện tối đa 8 dòng (0087)
MAX_WORDS = 6       # từ thứ 7 trở đi bỏ (ô tìm 60 ký tự)
_W: tuple[LiteralString, ...] = ("%(_w0)s", "%(_w1)s", "%(_w2)s", "%(_w3)s", "%(_w4)s", "%(_w5)s")
_S: tuple[LiteralString, ...] = ("%(_s0)s", "%(_s1)s", "%(_s2)s", "%(_s3)s", "%(_s4)s", "%(_s5)s")   # đầu một từ
_T: tuple[LiteralString, ...] = ("%(_t0)s", "%(_t1)s", "%(_t2)s", "%(_t3)s", "%(_t4)s", "%(_t5)s")   # trọn từ
_PHONE_LIKE = re.compile(r"[\d\s+().\-]+")
_EDGE = "[^a-z0-9]"   # ranh giới từ trên chuỗi đã gấp (chữ Việt đã thành a–z)


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _re_escape(s: str) -> str:
    """Thoát ký tự đặc biệt cho biểu thức chính quy của Postgres (ARE): ``\\`` + ký tự không phải chữ / số là chính ký tự đó."""
    return re.sub(r"([^\w\s])", r"\\\1", s)


class SearchTerms:
    """Từ khoá đã chuẩn hoá của một lượt tìm. ``match`` / ``rank`` trả đoạn SQL (``LiteralString``) dùng tham số ``params``; ``LIMIT``
    ``%(_n)s`` lấy dư một dòng để ``core.find`` biết còn nữa (dòng "xem tất cả" — ``Module.search(more=)``)."""

    def __init__(self, q: str) -> None:
        self.q = " ".join(q.split())
        self.folded = fold(self.q)
        self.words: tuple[str, ...] = tuple(dict.fromkeys(re.findall(r"[^\W_]+", self.folded)))[:MAX_WORDS]
        self.digits = phone_search(self.q) if _PHONE_LIKE.fullmatch(self.q) else ""
        params: dict[str, Any] = {"_f": self.folded, "_fp": _like_escape(self.folded) + "%", "_fw": f"^{_re_escape(self.folded)}({_EDGE}|$)",
                                  "_n": ROWS + 1, "_d": f"%{self.digits}%", "_de": f"%{self.digits}"}
        for i, w in enumerate(self.words):
            params[f"_w{i}"] = f"%{w}%"
            params[f"_s{i}"] = f"(^|{_EDGE}){w}"
            params[f"_t{i}"] = f"(^|{_EDGE}){w}({_EDGE}|$)"
        self.params = params

    @staticmethod
    def _hay(cols: tuple[LiteralString, ...]) -> LiteralString:
        return f"core.vn_fold({cols[0]})" if len(cols) == 1 else "core.vn_fold(concat_ws(' ', " + ", ".join(cols) + "))"

    def _every(self, hay: LiteralString, op: LiteralString, names: tuple[LiteralString, ...]) -> LiteralString:
        return " AND ".join(f"{hay} {op} {names[i]}" for i in range(len(self.words))) if self.words else "false"

    def match(self, *cols: LiteralString, tel: LiteralString | None = None) -> LiteralString:
        """Điều kiện: mọi từ có trong các trường ``cols`` (gộp), HOẶC (khi từ khoá là số điện thoại) cột SĐT ``tel`` (biểu thức SQL — số E.164 đã lưu) chứa các chữ số."""
        assert cols
        words = self._every(self._hay(cols), "LIKE", _W)
        if tel is not None and self.digits:
            return f"(({words}) OR {tel} LIKE %(_d)s)"
        return f"({words})"

    def rank(self, *cols: LiteralString, tel: LiteralString | None = None) -> LiteralString:
        """Hạng của một dòng đã khớp (số nguyên, nhỏ = gần hơn): ``hạng × 1000 + độ dài trường đầu`` — hạng ở docstring của module. ``cols`` = các
        trường CHÍNH (mã / tên; trường đầu quyết độ dài), có thể khác các trường ``match`` (vd khớp cả nội dung, xếp theo mã)."""
        assert cols
        each: list[LiteralString] = [f"core.vn_fold({c})" for c in cols]
        hay = self._hay(cols)
        exact: LiteralString = " OR ".join(f"{e} = %(_f)s" for e in each)
        whole_start: LiteralString = " OR ".join(f"{e} ~ %(_fw)s" for e in each)
        part_start: LiteralString = " OR ".join(f"{e} LIKE %(_fp)s" for e in each)
        if tel is not None and self.digits:
            part_start = f"{part_start} OR {tel} LIKE %(_de)s"   # khớp đuôi SĐT ("4 số cuối") gần hơn khớp giữa số
        return (f"((CASE WHEN {exact} THEN 0 WHEN {whole_start} THEN 1 WHEN {self._every(hay, '~', _T)} THEN 2 WHEN {part_start} THEN 3"
                f" WHEN {self._every(hay, '~', _S)} THEN 4 ELSE 5 END) * 1000 + least(length(coalesce({cols[0]}, '')), 999))")
