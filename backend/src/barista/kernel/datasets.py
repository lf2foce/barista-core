"""TẬP DỮ LIỆU CÓ CHIỀU / CHỈ SỐ (quyết định 0100) — module khai MỘT LẦN "dữ liệu của tôi xem được theo những chiều nào, đo bằng gì"; lõi nhóm / cộng
trên TOÀN BỘ dòng bằng quyền người xem (RLS như mọi truy vấn) — không ai phải viết thêm truy vấn báo cáo cho từng kiểu xem.

* ``base``: câu SELECT của module (chỉ schema của module + bảng lõi được phép — bất biến 3) trả các cột mà chiều / chỉ số / ngày tham chiếu qua ``t.``.
* Chiều (``Dim``) = biểu thức trên ``t`` (nhóm / lọc được); chỉ số (``Measure``) = ``count(...)`` / ``sum(...)`` — CỘNG ĐƯỢC, nên tổng các nhóm
  = tổng chung (ô tổng không phải tính riêng, không lệch). Ngày (``dates``) = cột ngày lọc theo kỳ, nửa mở [từ, trước).
* Mọi tên trong câu SQL đến từ KHAI BÁO của module (đã kiểm khoá); giá trị người dùng chỉ đi qua tham số ⇒ không có đường chèn SQL.
* Trả trang bảng ĐỦ dòng (``total``) — quá ``MAX_GROUPS`` nhóm ⇒ từ chối (lọc thêm / bỏ chiều), không cắt im lặng.
* Nguồn của con số (0097): ``DrillTo`` ánh xạ chiều / ngày / chỉ số sang bộ lọc của truy vấn danh sách gốc ⇒ ô tổng bấm ra đúng các dòng tạo nên nó
  (``tests/test_datasets.py`` kiểm khớp). Không ánh xạ được (lọc nhiều giá trị, chiều không có bộ lọc tương ứng) ⇒ không gắn nguồn, không đoán.
"""

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, LiteralString

from psycopg import sql
from pydantic import field_validator

from barista.kernel import ui
from barista.kernel.clock import today
from barista.kernel.errors import Invalid
from barista.kernel.fields import Input
from barista.kernel.fields import field as ifield

if TYPE_CHECKING:
    from barista.kernel.executor import Ctx

MAX_GROUPS = 500
SCOPE_LABEL = {"own": "của bạn", "unit": "của đội bạn thuộc", "team": "của đội bạn", "all": "cả công ty"}


@dataclass(frozen=True)
class Dim:
    label: str
    expr: LiteralString                       # biểu thức trên t (vd "t.project_code", "to_char(t.signed_on, 'YYYY-MM')")
    labels: dict[str, str] | None = None      # giá trị → nhãn (cột badge)
    month: bool = False                       # giá trị dạng YYYY-MM ⇒ xếp tăng dần, biểu đồ đường được


@dataclass(frozen=True)
class Measure:
    label: str
    expr: LiteralString                       # count(...) / sum(...) — cộng được
    fmt: str = "number"                       # number · money
    # lớp 3 (1.59): chỉ số của một nhóm thông tin (vd tổng phí môi giới) — cột / ô số / chuỗi biểu đồ của chỉ số khai ``needs`` này, người xem thiếu quyền
    # nhận bảng không có chỉ số đó (số trong dòng cũng bỏ ở cửa ra)
    needs: str | None = None

    def __post_init__(self) -> None:
        if not re.match(r"^(count|sum)\(", self.expr):
            raise ValueError(f"chỉ số “{self.label}”: chỉ count(...) / sum(...) — tổng các nhóm phải bằng tổng chung")


@dataclass(frozen=True)
class Ratio:
    """CHỈ SỐ TỶ LỆ (0220, 1.40): ``num`` / ``den`` là khoá hai chỉ số CỘNG ĐƯỢC của cùng tập — mỗi nhóm và ô tổng tính tổng tử ÷ tổng mẫu (không cộng
    các tỷ lệ). Mẫu 0 ⇒ trống (không đo được), không phải 0. ``fmt``: percent (×100, một số lẻ) · money (làm tròn đồng, vd chi phí trên lead) · number."""
    label: str
    num: str
    den: str
    fmt: str = "percent"
    needs: str | None = None   # lớp 3 (1.59) — như ``Measure.needs`` (tỷ lệ có tử / mẫu nhạy cảm, vd ROAS)

    def __post_init__(self) -> None:
        if self.fmt not in ("percent", "money", "number"):
            raise ValueError(f"tỷ lệ “{self.label}”: định dạng percent / money / number")

    def of(self, num: int | float, den: int | float) -> float | int | None:
        if not den:
            return None
        v = num / den
        return round(v * 100, 1) if self.fmt == "percent" else round(v) if self.fmt == "money" else round(v, 2)


@dataclass(frozen=True)
class DrillTo:
    """Mở ra các dòng gốc: ``query`` (bảng danh sách của module), ``dims`` chiều → trường lọc, ``dates`` ngày → (trường từ, trường trước),
    ``measures`` chỉ số → cột cộng của bảng (None = đếm dòng), ``base`` bộ lọc cố định (vd trạng thái "tất cả")."""
    query: str
    dims: dict[str, str] = field(default_factory=dict)
    dates: dict[str, tuple[str, str]] = field(default_factory=dict)
    measures: dict[str, str | None] = field(default_factory=dict)
    base: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Dataset:
    key: str                                  # module.tên
    title: str
    description: str
    resource: str
    base: LiteralString
    dims: dict[str, Dim]
    measures: dict[str, Measure | Ratio]
    dates: dict[str, str]                     # cột ngày của base → nhãn
    drill: DrillTo | None = None
    # ĐỘ PHỦ (0220, 1.42): SQL một dòng, cột tuỳ chọn ``complete_through`` (số đủ tới ngày) và ``reliable_from`` (từ ngày số mới đáng tin) — theo cột
    # ngày ĐẦU của tập. Trang ghi rõ khi kỳ đang xem vượt ra ngoài (không hiện số thấp giả như số thật).
    coverage: LiteralString | None = None

    @property
    def query(self) -> str:
        module, name = self.key.split(".", 1)
        return f"{module}.ds_{name}"

    def __post_init__(self) -> None:
        names = [*self.dims, *self.measures, *self.dates]
        bad = [k for k in names if not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", k)]
        if bad or len(set(names)) != len(names) or not self.measures:
            raise ValueError(f"tập dữ liệu {self.key}: khoá chiều / chỉ số / ngày phải là chữ thường, không trùng ({bad or names})")
        for k, m in self.measures.items():
            if isinstance(m, Ratio) and not all(isinstance(self.measures.get(x), Measure) for x in (m.num, m.den)):
                raise ValueError(f"tập dữ liệu {self.key}: tỷ lệ {k} phải chia hai chỉ số cộng được của cùng tập ({m.num} / {m.den})")


DATASETS: dict[str, Dataset] = {}


class DatasetIn(Input):
    measures: list[str] = ifield("Chỉ số", [], help="Khoá chỉ số (trống = chỉ số đầu tiên)")
    by: list[str] = ifield("Nhóm theo", [], help="Tối đa 2 khoá chiều (trống = một dòng tổng)")
    where: dict[str, list[str]] = ifield("Lọc", {}, help="{khoá chiều: [giá trị]} — giữ dòng có chiều thuộc các giá trị")
    date: str = ifield("Theo ngày", "", help="Khoá cột ngày để lọc kỳ (trống = ngày đầu tiên của tập)")
    date_from: dt.date | None = ifield("Từ ngày", None)    # (kiểu viết đủ: trường "date" ở trên che tên kiểu date trong lớp)
    date_before: dt.date | None = ifield("Trước ngày", None, help="Không gồm ngày này")

    # Mô hình AI hay gửi MỘT giá trị trần ("by": "status", "where": {"status": "signed"}) thay vì danh sách — nhận như danh sách một phần tử
    # (chỉ nới cách viết; khoá / giá trị lạ vẫn bị từ chối ở ``_check``). Soạn với AI từng thử lại cùng một lệnh sai 5 lần vì chỗ này.
    @field_validator("measures", "by", mode="before")
    @classmethod
    def _one_or_many(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else v

    @field_validator("where", mode="before")
    @classmethod
    def _where_values(cls, v: Any) -> Any:
        return {k: [x] if isinstance(x, str | int) and not isinstance(x, bool) else x for k, x in v.items()} if isinstance(v, dict) else v


def describe(ds: Dataset) -> dict[str, Any]:
    """Mô tả cho trợ lý / app: chiều, chỉ số, ngày — chỉ khai báo, không có số liệu."""
    return {"dataset": ds.key, "query": ds.query, "title": ds.title, "description": ds.description,
            "dims": {k: {"label": d.label, **({"values": d.labels} if d.labels else {}), **({"month": True} if d.month else {})}
                     for k, d in ds.dims.items()},
            "measures": {k: {"label": m.label, "format": m.fmt, **({"ratio": f"{m.num} / {m.den}"} if isinstance(m, Ratio) else {})}
                         for k, m in ds.measures.items()},
            "dates": ds.dates}


def _check(ds: Dataset, inp: DatasetIn) -> tuple[list[str], str]:
    measures = inp.measures or [next(iter(ds.measures))]
    unknown = [k for k in measures if k not in ds.measures] + [k for k in [*inp.by, *inp.where] if k not in ds.dims]
    if unknown:
        raise Invalid(f"{ds.title}: không có {', '.join(unknown)} — chiều: {', '.join(ds.dims)}; chỉ số: {', '.join(ds.measures)}")
    if len(inp.by) > 2 or len(set(inp.by)) != len(inp.by):
        raise Invalid("Nhóm theo tối đa 2 chiều khác nhau")
    dcol = inp.date or next(iter(ds.dates), "")
    if (inp.date_from or inp.date_before) and dcol not in ds.dates:
        raise Invalid(f"{ds.title}: không lọc theo ngày “{inp.date}” được — có: {', '.join(ds.dates) or 'không có cột ngày'}")
    return measures, dcol


async def run(ctx: Ctx, ds: Dataset, inp: DatasetIn) -> dict[str, Any]:
    measures, dcol = _check(ds, inp)
    sel = [sql.SQL("({}) AS {}").format(sql.SQL(ds.dims[k].expr), sql.Identifier(k)) for k in inp.by]
    base = list(dict.fromkeys(x for k in measures for x in _parts(ds, k)))   # chỉ số cộng cần chạy SQL (tỷ lệ ⇒ tử + mẫu)
    sel += [sql.SQL("{} AS {}").format(sql.SQL(_sum(ds, k).expr), sql.Identifier(k)) for k in base]
    where: list[sql.Composable] = [sql.SQL("TRUE")]
    params: list[Any] = []
    for k, vals in inp.where.items():
        where.append(sql.SQL("({})::text = ANY(%s)").format(sql.SQL(ds.dims[k].expr)))
        params.append([str(v) for v in vals])
    if inp.date_from:
        where.append(sql.SQL("t.{} >= %s").format(sql.Identifier(dcol)))
        params.append(inp.date_from)
    if inp.date_before:
        where.append(sql.SQL("t.{} < %s").format(sql.Identifier(dcol)))
        params.append(inp.date_before)
    q = sql.SQL("SELECT {sel} FROM ({base}) t WHERE {where}").format(
        sel=sql.SQL(", ").join(sel), base=sql.SQL(ds.base), where=sql.SQL(" AND ").join(where))
    if inp.by:
        q = sql.SQL("{} GROUP BY {} ORDER BY {} DESC NULLS LAST LIMIT {}").format(
            q, sql.SQL(", ").join(sql.Literal(i + 1) for i in range(len(inp.by))), sql.Literal(len(inp.by) + 1), sql.Literal(MAX_GROUPS + 1))
    rows = await ctx.all(q, params)
    if len(rows) > MAX_GROUPS:
        raise Invalid(f"{ds.title}: hơn {MAX_GROUPS} nhóm — lọc thêm hoặc bỏ bớt chiều nhóm")
    totals = {k: 0 for k in base}
    for i, r in enumerate(rows):
        r["id"] = i
        for k in base:
            r[k] = int(r[k] or 0)
            totals[k] += r[k]
        for k in measures:
            if isinstance(m := ds.measures[k], Ratio):
                r[k] = m.of(r[m.num], r[m.den])
        for k in [x for x in base if x not in measures]:
            del r[k]   # tử / mẫu chỉ để tính tỷ lệ — không hiện cột người dùng không chọn
        for k in inp.by:
            r[k] = "" if r[k] is None else str(r[k])
    first = ds.measures[measures[0]]
    if inp.by and isinstance(first, Ratio):   # SQL xếp theo tử / mẫu; chỉ số đầu là tỷ lệ ⇒ xếp lại theo tỷ lệ (nhóm không đo được xuống cuối)
        rows.sort(key=lambda r: (r[measures[0]] is None, -(r[measures[0]] or 0)))
    cols = [ui.col(k, ds.dims[k].label, "badge" if ds.dims[k].labels else "text", labels=ds.dims[k].labels) for k in inp.by]
    for c, k in zip(cols, inp.by, strict=True):
        if ds.dims[k].month:
            c["order"] = "asc"   # chiều tháng: biểu đồ / bảng theo thứ tự thời gian
    for k in measures:
        c = ui.col(k, ds.measures[k].label, ds.measures[k].fmt, needs=ds.measures[k].needs)
        if isinstance(ds.measures[k], Ratio):
            c["ratio"] = True   # chỉ THÊM trường: cột tỷ lệ — nơi gộp dòng (biểu đồ, nhóm "Khác") không được cộng các tỷ lệ
        cols.append(c)
    kpis = []
    for k in measures:
        m = ds.measures[k]
        value = m.of(totals[m.num], totals[m.den]) if isinstance(m, Ratio) else totals[k]
        k_ = ui.kpi(m.label, value, m.fmt, drill=None if isinstance(m, Ratio) else _drill(ds, inp, dcol, k), needs=m.needs)
        k_["key"] = k   # khoá chỉ số — app tự ghép lấy đúng ô (và nguồn của nó) theo khoá
        kpis.append(k_)
    scope = SCOPE_LABEL.get(ctx.p.scope(ds.resource), "trong phần bạn được xem")
    page = ui.table(rows, cols, title=ds.title, kpis=kpis, empty="Không có dữ liệu trong bộ lọc này",
                    note=f"Số trong phạm vi {scope} — tính trên toàn bộ dòng, không cắt.")
    page["total"] = len(rows)
    page["source"] = await provenance(ctx, ds, inp, dcol, scope)
    chart = _picture(ds, inp.by, measures, rows)
    if chart:
        page["header"] = [chart]   # chỉ THÊM (khối đầu bảng): hỏi "theo tháng / theo nhóm" ⇒ người THẤY hình dù trợ lý gọi thẳng truy vấn này (0220 lát 5)
    return page


CHART_GROUPS = 12


def _picture(ds: Dataset, by: list[str], measures: list[str], rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Hình đi kèm bảng tổng hợp MỘT chiều (≥ 2 nhóm): chiều tháng ⇒ đường theo thời gian, còn lại ⇒ thanh ngang nhóm lớn trước (≤ 12 nhóm — nhiều
    hơn thì bảng mới là thứ cần đọc). Chỉ vẽ chỉ số ĐẦU (các chỉ số khác đơn vị khác nhau không chung một trục). Chốt chặn tối thiểu, không phải bộ
    luật chọn hình — muốn hình khác thì ghép khối (app tự ghép)."""
    if len(by) != 1 or len(rows) < 2:
        return None
    dim, key = ds.dims[by[0]], measures[0]
    m = ds.measures[key]
    if dim.month:
        shown = sorted(rows, key=lambda r: r[by[0]])
    elif len(rows) > CHART_GROUPS:
        return None
    else:
        shown = rows
    names = dim.labels or {}

    def label(v: str) -> str:
        if dim.month:
            y, _, mo = v.partition("-")
            return f"T{int(mo)}/{y}" if y.isdigit() and mo.isdigit() else (v or "—")
        return names.get(v, v or "—")
    unit = {"money": "vnd", "percent": "pct"}.get(m.fmt, "count")
    return ui.b_chart("line" if dim.month else "hbar", [label(r[by[0]]) for r in shown], [ui.line(m.label, [r[key] for r in shown], needs=m.needs)],
                      title=f"{m.label} theo {dim.label[:1].lower()}{dim.label[1:]}", unit=unit)


async def provenance(ctx: Ctx, ds: Dataset, inp: DatasetIn, dcol: str, scope: str) -> dict[str, Any]:
    """NGUỒN GỐC của số (0220, 1.42 — chỉ THÊM trường ``source`` của trang): tập nào, phạm vi quyền của người xem, lúc tính, số đủ tới ngày nào, và cảnh
    báo khi kỳ đang xem vượt độ phủ — để không ai đọc số thiếu thành "không xảy ra"."""
    src: dict[str, Any] = {"kind": "dataset", "label": ds.title, "scope": scope, "as_of": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
    if not ds.coverage or dcol != next(iter(ds.dates), ""):
        return src
    cov = await ctx.one(ds.coverage) or {}
    through, reliable = cov.get("complete_through"), cov.get("reliable_from")
    warn = []
    if through:
        src["through"] = through.isoformat()
        end = (inp.date_before - dt.timedelta(days=1)) if inp.date_before else today()
        if through < end:
            warn.append(f"Số đủ tới {through:%d/%m/%Y} — sau ngày đó còn thiếu")
    if reliable:
        src["reliable_from"] = reliable.isoformat()
        if inp.date_from is None or inp.date_from < reliable:
            warn.append(f"Trước {reliable:%d/%m/%Y} số chưa đầy đủ")
    if warn:
        src["warn"] = "; ".join(warn)
    return src


def _parts(ds: Dataset, k: str) -> tuple[str, ...]:
    m = ds.measures[k]
    return (m.num, m.den) if isinstance(m, Ratio) else (k,)


def _sum(ds: Dataset, k: str) -> Measure:
    m = ds.measures[k]
    assert isinstance(m, Measure)
    return m


def _drill(ds: Dataset, inp: DatasetIn, dcol: str, measure: str) -> dict[str, Any] | None:
    d = ds.drill
    if d is None or measure not in d.measures:
        return None
    out = dict(d.base)
    for k, vals in inp.where.items():
        if k not in d.dims or len(vals) != 1:
            return None   # lọc nhiều giá trị / chiều không có bộ lọc tương ứng ở danh sách gốc ⇒ không gắn nguồn (không đoán)
        out[d.dims[k]] = vals[0]
    if inp.date_from or inp.date_before:
        if dcol not in d.dates:
            return None
        lo, hi = d.dates[dcol]
        out[lo], out[hi] = inp.date_from, inp.date_before
    return ui.drill(d.query, out, sum=d.measures[measure])
