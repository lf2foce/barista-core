#!/usr/bin/env python3
"""Bộ quét màn hình: mở TỪNG màn của TỪNG vai trên một backend đang chạy, ghi trạng thái / thời gian / kích thước / kiểu và PHÂN LOẠI nội dung.

Chỉ dùng thư viện chuẩn — chạy được cho bản cài bất kỳ (không cần venv của lõi). Chỉ ĐỌC (``GET /v1/manifest`` + ``POST /v1/queries/<khoá>``);
không chạy lệnh ghi. Đăng nhập bằng header dev (``X-Dev-User`` — cần ``BARISTA_DEV_AUTH=1`` trên backend, chỉ nhận từ máy local).

    python backend/scripts/walk_screens.py --base http://localhost:8300 --tenant <uuid> \\
        --users giamdoc@x.vn,ketoan@x.vn --out /tmp/walk --fail-on-error

Với mỗi người dùng: manifest → mọi màn trong menu + bàn việc + hộp tin → chạy truy vấn của màn với đầu vào CỐ ĐỊNH của màn (``input``) và để mặc định
mọi ô lọc (kể cả nhóm "Thêm điều kiện") — đúng cái app gửi lúc mới mở màn.

Phân loại (``phan_loai``): ``co_du_lieu`` · ``trong`` (bảng 0 dòng; thẻ mà mọi ô số 0 / rỗng, không bảng / danh sách / biểu đồ có dữ liệu) ·
``trong_co_loi_moi`` (trống nhưng có nút / lời mời hành động) · ``loi`` (HTTP ≥ 400 trừ 403, hoặc status ``error`` / ``invalid``) · ``tu_choi`` (403 — người này
không được mở màn dù menu hiện). Cờ ``cham`` riêng: > ``--slow`` giây (mặc định 3). ``--retry-slow`` chạy lại màn chậm một lần để tách "lần đầu nguội" khỏi chậm thật.
``--fail-on-error``: thoát mã 1 nếu có màn ``loi`` (hoặc ``tu_choi`` — menu hứa mà quyền từ chối); ``--fail-on-slow`` thêm màn chậm.
``--deep``: ngoài màn, còn mở (a) chi tiết của DÒNG ĐẦU (``open`` + ``bind``), (b) nguồn của MỌI ô số khai ``drill``, (c) mục ``open`` của thẻ, (d) danh sách
chọn của ô lọc (``options_query``) — chỗ web gọi khi người dùng bấm, và hay vỡ riêng trên dữ liệu thật; lỗi ghi ở ``sau`` và tính vào ``--fail-on-error``.
``--concurrency N``: mở N màn cùng lúc (web mở nhiều truy vấn song song — bắt lỗi 500 chỉ hiện khi đồng thời).

Đầu ra trong ``--out``: ``walk.json`` (từng màn × người) và ``walk.md`` (bảng tổng theo người + ma trận màn × vai + danh sách lỗi / chậm / trống).
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from typing import Any

ZERO = (None, 0, 0.0, "", "0", "—", "-", "0 đ", "0%", "0,0", "0.0")
DEFAULT_EMPTY = "Chưa có dữ liệu"
KNOWN_BLOCKS = ("kpis", "table", "chart", "list", "steps", "stages", "timeline", "board", "finding", "text", "callout", "picker", "tiles", "choices", "thread",
                "calendar")


def http(base: str, user: str, tenant: str, path: str, body: dict[str, Any] | None = None, timeout: float = 120) -> tuple[int, Any, float, int]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"X-Dev-User": user, "X-Tenant-Id": tenant, "Content-Type": "application/json"})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except Exception as e:  # hết giờ / mất kết nối
        return 0, {"error": f"{type(e).__name__}: {e}"}, time.perf_counter() - t, 0
    dt = time.perf_counter() - t
    try:
        return status, json.loads(raw or b"null"), dt, len(raw)
    except ValueError:
        return status, {"error": raw[:200].decode("utf-8", "replace")}, dt, len(raw)


def zero(v: Any) -> bool:
    if isinstance(v, bool):
        return not v
    if isinstance(v, str):
        return v.strip() in ZERO
    return v in ZERO


def chart_has_data(b: dict[str, Any]) -> bool:
    return any(not zero(x) for s in b.get("series") or [] for x in s.get("values") or [])


LEAK = re.compile(r"\bNone\b|\bnan\b|\bNaN\b|undefined|\[\[|\]\]|\{\{|Traceback|\bTODO\b|%s\b|\{[a-z_]+\}|0001-01-01|1970-01-01|\binf\b|\?{3,}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def suspicious(page: Any) -> list[str]:
    """Chữ nhìn là lỗi hiển thị (None / NaN / dấu nhãn chưa thay / ???? chưa ánh xạ / mã uuid ở cột chữ / ngày mốc 1970) — heuristic, người xem lại. Tối đa 5 mẫu."""
    found: list[str] = []

    def note(where: str, v: Any) -> None:
        if len(found) < 5 and (isinstance(v, str)) and (LEAK.search(v) or (where == "o" and UUID.search(v))):
            found.append(f"{where}: {v[:80]}")

    def kpi_notes(k: dict[str, Any]) -> None:
        if k.get("value") is not None:   # giá trị None = "không đo được" (hợp lệ, app vẽ —); chỉ chữ "None" đã thành chuỗi mới là lỗi
            note("k", str(k["value"]))
        note("k", k.get("label"))

    def table(t: dict[str, Any]) -> None:
        cols = [c["key"] for c in t.get("columns") or [] if c["key"] != "id" and not c["key"].endswith("_id")]
        for r in (t.get("rows") or [])[:200]:
            for c in cols:
                note("o", r.get(c))
        for k in t.get("kpis") or []:
            kpi_notes(k)
        for key in ("title", "note"):
            note("t", t.get(key))

    if isinstance(page, dict):
        if page.get("kind") == "table":
            table(page)
        else:
            for key in ("title", "subtitle"):
                note("t", page.get(key))
            for b in page.get("blocks") or []:
                if b.get("type") == "table":
                    table(b.get("page") or {})
                elif b.get("type") == "kpis":
                    for k in b.get("items") or []:
                        kpi_notes(k)
                else:
                    for key in ("title", "text", "markdown", "note"):
                        note("b", b.get(key))
                    for it in b.get("items") or []:
                        if isinstance(it, dict):
                            for key in ("title", "subtitle"):
                                note("b", it.get(key))
    return found


def read_page(page: Any) -> dict[str, Any]:
    """Mô tả một trang kết quả: kiểu + tín hiệu dữ liệu + lời mời hành động. Không đánh giá — ``classify`` làm."""
    info: dict[str, Any] = {"nghi_van": suspicious(page), "kieu": "khac", "dong": None, "kpi": 0, "kpi_khac_0": 0, "bieu_do": 0, "bieu_do_co_so": 0, "bang_trong_the": 0,
                            "dong_trong_the": 0, "muc": 0, "chu": 0, "goc_nhin": [], "loi_moi": [], "giai_thich": ""}
    if not isinstance(page, dict):
        return info
    k = page.get("kind")
    if k == "table":
        info["kieu"] = "bang"
        rows = page.get("rows") or []
        info["dong"] = len(rows)
        kp = page.get("kpis") or []
        info["kpi"], info["kpi_khac_0"] = len(kp), sum(not zero(x.get("value")) for x in kp)
        info["goc_nhin"] = sorted((page.get("views") or {}).keys())
        if page.get("empty_actions"):
            info["loi_moi"].append("empty_actions")
        for h in page.get("header") or []:
            if h.get("type") == "chart":
                info["bieu_do"] += 1
                info["bieu_do_co_so"] += chart_has_data(h)
        if not rows:
            e = (page.get("empty") or "").strip()
            info["giai_thich"] = e if e and e != DEFAULT_EMPTY else ""
        info["cat_dong"] = "dòng đầu" in (page.get("note") or "")   # chạm trần LIMIT: ô tổng chỉ tính trên các dòng đang hiện
    elif k == "detail":
        info["kieu"] = "chi_tiet"
        n = 0
        for s in page.get("sections") or []:
            n += sum(not zero(f.get("value")) for f in s.get("fields") or [])
            n += bool((s.get("text") or "").strip()) + bool((s.get("table") or {}).get("rows"))
        info["muc"] = n
        if page.get("actions"):
            info["loi_moi"].append("actions")
    elif k == "card":
        info["kieu"] = "the"
        if page.get("actions"):
            info["loi_moi"].append("actions")
        info["khoi"] = sorted({b.get("type") or "?" for b in page.get("blocks") or []})   # app gặp khối lạ thì BỎ QUA — so với bộ vẽ của web ở báo cáo
        for b in page.get("blocks") or []:
            t = b.get("type")
            if t == "kpis":
                items = b.get("items") or []
                info["kpi"] += len(items)
                info["kpi_khac_0"] += sum(not zero(x.get("value")) for x in items)
            elif t == "table":
                p = b.get("page") or {}
                info["bang_trong_the"] += 1
                info["dong_trong_the"] += len(p.get("rows") or [])
                if p.get("empty_actions"):
                    info["loi_moi"].append("empty_actions")
                if not p.get("rows") and (p.get("empty") or "").strip() not in ("", DEFAULT_EMPTY):
                    info["giai_thich"] = info["giai_thich"] or p["empty"].strip()
            elif t == "chart":
                info["bieu_do"] += 1
                info["bieu_do_co_so"] += chart_has_data(b)
            elif t == "list":
                info["muc"] += len(b.get("items") or [])
            elif t in ("steps", "stages"):
                info["muc"] += len(b.get(t) or [])
            elif t == "timeline":
                info["muc"] += sum(len(g.get("items") or []) for g in b.get("groups") or [])
                info["goc_nhin"].append("timeline")
            elif t == "board":
                info["muc"] += sum(len(c.get("cards") or c.get("items") or []) for c in b.get("columns") or [])
            elif t == "finding":
                info["muc"] += 1
            elif t == "text":
                info["chu"] += len((b.get("markdown") or "").strip())
            elif t == "calendar":   # lịch tuần / ngày: items = các lịch hẹn trong khoảng đang xem
                info["muc"] += len(b.get("items") or [])
                info["goc_nhin"].append("calendar")
            elif t not in KNOWN_BLOCKS:   # khối lạ: đừng kết luận TRỐNG chỉ vì không biết đọc — đếm danh sách con nếu có
                info.setdefault("khoi_la", []).append(t)
                info["muc"] += max((len(v) for k, v in b.items() if k in ("items", "rows", "cards", "events", "entries") and isinstance(v, list)), default=0)
            elif t in ("callout", "picker", "tiles", "choices", "thread"):
                if t in ("tiles", "choices"):
                    info["muc"] += len(b.get("items") or b.get("questions") or [])
                if b.get("actions"):
                    info["loi_moi"].append("callout_actions")
                if t == "callout" and (b.get("title") or b.get("text")):
                    info["giai_thich"] = info["giai_thich"] or (b.get("title") or b.get("text"))[:120]
    return info


def classify(status: int, out: Any, info: dict[str, Any], screen: dict[str, Any]) -> tuple[str, str]:
    """(phan_loai, ghi_chu). Quy tắc nằm ở đây MỘT chỗ."""
    if status == 403 or (isinstance(out, dict) and out.get("status") == "denied"):
        return "tu_choi", (out or {}).get("error", "") if isinstance(out, dict) else ""
    if status >= 400 or status == 0 or not isinstance(out, dict) or out.get("status") not in ("ok", "pending_approval", None):
        msg = out.get("error") or out.get("detail") or out.get("status") if isinstance(out, dict) else str(out)
        return "loi", f"HTTP {status}: {str(msg)[:200]}"
    if screen.get("actions"):
        info["loi_moi"].append("man_actions")
    note = ""
    kieu = info["kieu"]
    if kieu == "bang":
        has = bool(info["dong"])
        if not has and info["kpi_khac_0"]:
            note = "bảng 0 dòng nhưng có ô số khác 0"
    elif kieu == "chi_tiet":
        has = info["muc"] > 0
    elif kieu == "the":
        has = bool(info["kpi_khac_0"] or info["dong_trong_the"] or info["muc"] or info["bieu_do_co_so"] or info["chu"] >= 200)
    else:
        has = True   # trang lạ (không biết đọc) — không tự kết luận trống
        note = f"kiểu trang lạ: {(out.get('output') or {}).get('kind') if isinstance(out.get('output'), dict) else type(out.get('output')).__name__}"
    if has:
        return "co_du_lieu", note
    if (info["giai_thich"] or "").startswith(("Không có", "Không còn", "Không ")):
        info["trong_binh_thuong"] = True   # hàng đợi ngoại lệ: trống = tốt ("Không có phiếu nào chờ…"), khác với màn chưa có nghiệp vụ / chưa nạp
    return ("trong_co_loi_moi" if info["loi_moi"] else "trong"), note


def collect_deep(s: dict[str, Any], page: Any) -> list[tuple[str, str, dict[str, Any], dict[str, Any]]]:
    """(loại, truy vấn, đầu vào) cần mở sâu của MỘT màn: chi tiết dòng đầu, nguồn ô số, mục ``open`` của thẻ, danh sách chọn của ô lọc."""
    todo: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
    if not isinstance(page, dict):
        return todo
    if (op := s.get("open")) and page.get("kind") == "table" and page.get("rows"):
        row = page["rows"][0]
        if all(row.get(src) not in (None, "") for src in op["bind"].values()):
            todo.append(("mo_dong", op["query"], {k: row[src] for k, src in op["bind"].items()}, {}))
    kpis = list(page.get("kpis") or [])
    for b in page.get("blocks") or []:
        if b.get("type") == "kpis":
            kpis += b.get("items") or []
    for k in kpis:
        if d := k.get("drill"):
            todo.append(("drill", d["query"], dict(d.get("input") or {}), {"nhan": k.get("label", ""), "gia_tri": k.get("value"), "cot": d.get("sum"),
                                                                           "dang": k.get("format")}))
    for o in page.get("open") or []:
        todo.append(("mo_the", o["query"], dict(o.get("input") or {}), {}))
    for f in s.get("filters") or []:
        if f.get("options_query"):
            todo.append(("tuy_chon", f["options_query"], {}, {}))
    return todo


def drill_check(meta: dict[str, Any], rows: list[dict[str, Any]] | None, cut: bool) -> tuple[str, str]:
    """Ô số khai nguồn (``drill``) phải KHỚP các dòng nguồn: đếm dòng, hoặc cộng cột ``sum`` — trên dữ liệu THẬT (``tests/test_drill.py`` chỉ kiểm dữ liệu mẫu).
    Bảng nguồn chạm trần dòng ⇒ không so được (``khong_so_duoc``)."""
    v = meta["gia_tri"]
    if rows is None or isinstance(v, bool) or not isinstance(v, int | float) or meta.get("dang") not in ("number", "money"):
        return "ok", ""
    if cut:
        return "khong_so_duoc", "bảng nguồn chạm trần dòng"
    got = sum(float(r.get(meta["cot"]) or 0) for r in rows) if meta.get("cot") else len(rows)
    if abs(got - v) > 0.5:
        return "lech", f"ô “{meta['nhan']}” = {v:,.0f} nhưng nguồn ({'cộng ' + meta['cot'] if meta.get('cot') else 'đếm dòng'}) = {got:,.0f}"
    return "ok", ""


def walk_deep(base: str, user: str, tenant: str, s: dict[str, Any], page: Any, slow: float, seen: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for loai, q, inp, meta in collect_deep(s, page):
        key = (q, json.dumps(inp, sort_keys=True, default=str))
        if key not in seen:   # cùng truy vấn + đầu vào chỉ mở một lần cho một người (danh sách chọn lặp ở hàng chục màn)
            st, res, dt, size = http(base, user, tenant, f"/v1/queries/{q}", inp)
            ok = st == 200 and isinstance(res, dict) and res.get("status") in ("ok", "pending_approval", None)
            r = {"loai": loai, "truy_van": q, "http": st, "giay": round(dt, 2), "byte": size,
                 "ket_qua": "ok" if ok else "tu_choi" if st == 403 else "loi", "cham": dt > slow, "ghi_chu": ""}
            if not ok:
                msg = res.get("error") or res.get("detail") or res.get("status") if isinstance(res, dict) else res
                r["ghi_chu"] = f"HTTP {st}: {str(msg)[:200]}"
            elif loai == "tuy_chon" and not (res.get("output") if isinstance(res, dict) else None):
                r["ket_qua"] = "trong"   # ô lọc chọn từ danh sách mà danh sách rỗng
            if ok and loai == "drill":
                o = res.get("output") if isinstance(res, dict) else None
                r["_rows"] = o.get("rows") or [] if isinstance(o, dict) and o.get("kind") == "table" else None
                r["_cut"] = bool(isinstance(o, dict) and "dòng đầu" in (o.get("note") or ""))
            seen[key] = r
        r = {k: v for k, v in seen[key].items() if not k.startswith("_")}
        if loai == "drill" and seen[key]["ket_qua"] == "ok":
            r["ket_qua"], r["ghi_chu"] = drill_check(meta, seen[key].get("_rows"), seen[key].get("_cut", False))
            r["o_so"] = meta["nhan"]
        out.append({**r, "loai": loai})
    return out


def walk_screen(base: str, user: str, tenant: str, ws: str, group_title: str, s: dict[str, Any], slow: float, retry: bool,
                seen: dict[tuple[str, str], dict[str, Any]] | None = None) -> dict[str, Any]:
    q = s.get("query", "")
    st, out, dt, size = http(base, user, tenant, f"/v1/queries/{q}", dict(s.get("input") or {}))
    page = out.get("output") if isinstance(out, dict) else None
    info = read_page(page)
    phan_loai, note = classify(st, out, info, s)
    rec: dict[str, Any] = {"phan_he": ws, "phan_he_ten": group_title, "man": s["key"], "ten": s.get("title", ""), "nhom": s.get("group", ""), "truy_van": q,
                           "http": st, "giay": round(dt, 2), "byte": size, "cham": dt > slow, "phan_loai": phan_loai, "ghi_chu": note, **info,
                           "so_o_loc": len(s.get("filters") or []), "nut": len(s.get("actions") or [])}
    rec.setdefault("trong_binh_thuong", info.get("trong_binh_thuong", False))
    if seen is not None and phan_loai != "loi":
        rec["sau"] = walk_deep(base, user, tenant, s, page, slow, seen)
    if retry and dt > slow and phan_loai != "loi":
        _, _, dt2, _ = http(base, user, tenant, f"/v1/queries/{q}", dict(s.get("input") or {}))
        rec["giay_lan2"] = round(dt2, 2)
        rec["cham"] = max(dt, dt2) > slow and dt2 > slow   # chậm thật = cả hai lần chậm; lần đầu nguội thì giữ số để xem
        rec["cham_lan_dau_nguoi"] = dt > slow >= dt2
    return rec


def walk_user(base: str, tenant: str, user: str, slow: float, retry: bool, concurrency: int, only: set[str], deep: bool = False) -> dict[str, Any]:
    st, m, dt, _ = http(base, user, tenant, "/v1/manifest")
    if st != 200 or not isinstance(m, dict):
        return {"nguoi": user, "loi_manifest": f"HTTP {st}: {str(m)[:200]}", "man": []}
    todo: list[tuple[str, str, dict[str, Any]]] = []
    for w in m.get("workspaces", []):
        for s in w.get("screens", []):
            todo.append((w["key"], w["title"], s))
    if m.get("inbox"):
        todo.append(("_hop_tin", "Hộp tin", m["inbox"]))
    for s in m.get("desk", []):
        todo.append(("_ban_viec", "Bàn việc", s))
    if only:
        todo = [t for t in todo if t[0] in only]
    pinned = {(p["workspace"], p["key"]) for p in m.get("pins", [])}
    t0 = time.perf_counter()
    seen: dict[tuple[str, str], dict[str, Any]] | None = {} if deep else None
    if concurrency > 1:
        with ThreadPoolExecutor(concurrency) as ex:
            recs = list(ex.map(lambda t: walk_screen(base, user, tenant, t[0], t[1], t[2], slow, retry, seen), todo))
    else:
        recs = [walk_screen(base, user, tenant, w, wt, s, slow, retry, seen) for w, wt, s in todo]
    for r in recs:
        r["ghim"] = (r["phan_he"], r["man"]) in pinned
    u = m.get("user", {})
    return {"nguoi": user, "ten": u.get("name", ""), "vai": u.get("role", ""), "vai_nhan": u.get("role_label", ""), "manifest_giay": round(dt, 2),
            "tong_giay": round(time.perf_counter() - t0, 1), "man": recs}


def summarize(res: dict[str, Any]) -> dict[str, Any]:
    ms = res.get("man", [])
    c = {k: sum(r["phan_loai"] == k for r in ms) for k in ("co_du_lieu", "trong", "trong_co_loi_moi", "loi", "tu_choi")}
    ts = sorted(r["giay"] for r in ms)
    sau = [d for r in ms for d in r.get("sau", [])]
    return {"tong": len(ms), **c, "cham": sum(r["cham"] for r in ms), **({"sau": len(sau), "sau_loi": sum(d["ket_qua"] in ("loi", "tu_choi", "lech") for d in sau),
                                                                        "sau_trong": sum(d["ket_qua"] == "trong" for d in sau)} if any("sau" in r for r in ms) else {}), "giay_trung_vi": round(median(ts), 2) if ts else 0, "giay_max": ts[-1] if ts else 0}


SYM = {"co_du_lieu": "D", "trong": "T", "trong_co_loi_moi": "M", "loi": "L", "tu_choi": "X"}


def markdown(results: list[dict[str, Any]], slow: float) -> str:
    out = ["# Quét màn hình", "", f"Ngưỡng chậm: {slow:g} giây. Ký hiệu ma trận: **D** có dữ liệu · **T** trống · **M** trống, có lời mời hành động · **L** lỗi · **X** từ chối "
           "(menu hiện mà quyền từ chối) · hậu tố `*` = chậm · `-` = vai không thấy màn.", "", "## Tổng theo vai", "",
           "| Người | Vai | Màn | Có dữ liệu | Trống | Trống + lời mời | Lỗi | Từ chối | Chậm | Trung vị (s) | Chậm nhất (s) |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        if "loi_manifest" in r:
            out.append(f"| {r['nguoi']} | (lỗi manifest: {r['loi_manifest']}) |" + " |" * 9)
            continue
        s = summarize(r)
        out.append(f"| {r['nguoi']} | {r['vai_nhan'] or r['vai']} | {s['tong']} | {s['co_du_lieu']} | {s['trong']} | {s['trong_co_loi_moi']} | {s['loi']} | {s['tu_choi']} | "
                   f"{s['cham']} | {s['giay_trung_vi']} | {s['giay_max']} |")
    ok = [r for r in results if "loi_manifest" not in r]
    keys: dict[tuple[str, str], tuple[str, str]] = {}
    cell: dict[tuple[str, str, str], str] = {}
    for r in ok:
        for x in r["man"]:
            k = (x["phan_he"], x["man"])
            keys.setdefault(k, (x["phan_he_ten"], x["ten"]))
            cell[(*k, r["nguoi"])] = SYM[x["phan_loai"]] + ("*" if x["cham"] else "")
    out += ["", "## Ma trận màn × vai", "", "| Phân hệ | Màn | " + " | ".join(r["vai_nhan"] or r["vai"] for r in ok) + " |",
            "|---|---|" + "---|" * len(ok)]
    for k, (wt, t) in keys.items():
        out.append(f"| {wt} | {t} | " + " | ".join(cell.get((*k, r["nguoi"]), "-") for r in ok) + " |")
    out += ["", "## Màn lỗi / từ chối", ""]
    rows = [(r, x) for r in ok for x in r["man"] if x["phan_loai"] in ("loi", "tu_choi")]
    out += ["| Người | Màn | Truy vấn | Loại | Chi tiết |", "|---|---|---|---|---|"] if rows else ["Không có."]
    out += [f"| {r['nguoi']} | {x['phan_he_ten']} › {x['ten']} | `{x['truy_van']}` | {x['phan_loai']} | {x['ghi_chu'].replace('|', '/')[:160]} |" for r, x in rows]
    deep = [(r, x, d) for r in ok for x in r["man"] for d in x.get("sau", []) if d["ket_qua"] not in ("ok", "khong_so_duoc")]
    if any("sau" in x for r in ok for x in r["man"]):
        out += ["", "## Lỗi / trống khi mở sâu (chi tiết dòng đầu, nguồn ô số, mục open, danh sách chọn)", ""]
        out += ["| Người | Màn | Loại | Truy vấn | Kết quả | Chi tiết |", "|---|---|---|---|---|---|"] if deep else ["Không có."]
        out += [f"| {r['nguoi']} | {x['phan_he_ten']} › {x['ten']} | {d['loai']} | `{d['truy_van']}` | {d['ket_qua']} | {d['ghi_chu'].replace('|', '/')[:140]} |" for r, x, d in deep]
    out += ["", f"## Màn chậm (> {slow:g} giây)", ""]
    rows = sorted(((r, x) for r in ok for x in r["man"] if x["cham"]), key=lambda t: -t[1]["giay"])
    out += ["| Người | Màn | Giây | Lần 2 | Dòng |", "|---|---|---:|---:|---:|"] if rows else ["Không có."]
    out += [f"| {r['nguoi']} | {x['phan_he_ten']} › {x['ten']} | {x['giay']} | {x.get('giay_lan2', '')} | {x['dong'] if x['dong'] is not None else ''} |" for r, x in rows]
    sus = {(x["phan_he"], x["man"]): x for r in ok for x in r["man"] if x["nghi_van"]}
    out += ["", "## Nghi lỗi hiển thị (None / NaN / dấu nhãn chưa thay / mã uuid ở cột chữ — heuristic, người xem lại)", ""]
    out += ["| Màn | Mẫu |", "|---|---|"] if sus else ["Không có."]
    out += [f"| {x['phan_he_ten']} › {x['ten']} | {'; '.join(x['nghi_van'][:3]).replace('|', '/')} |" for x in sus.values()]
    cut = {(x["phan_he"], x["man"]): x for r in ok for x in r["man"] if x.get("cat_dong")}
    out += ["", "## Bảng chạm trần số dòng (ô tổng chỉ tính trên các dòng đang hiện)", ""]
    out += [f"- {x['phan_he_ten']} › {x['ten']} ({x['dong']} dòng)" for x in cut.values()] or ["Không có."]
    out += ["", "## Màn trống (gộp theo màn)", ""]
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for r in ok:
        for x in r["man"]:
            if x["phan_loai"] in ("trong", "trong_co_loi_moi"):
                a = agg.setdefault((x["phan_he"], x["man"]), {"ten": f"{x['phan_he_ten']} › {x['ten']}", "vai": [], "loi_moi": set(), "giai_thich": x["giai_thich"]})
                a["vai"].append(r["vai_nhan"] or r["vai"])
                a["loi_moi"].update(x["loi_moi"])
    out += ["| Màn | Trống ở vai | Lời mời | Câu giải thích trên màn |", "|---|---|---|---|"] if agg else ["Không có."]
    out += [f"| {a['ten']} | {', '.join(a['vai'])} | {', '.join(sorted(a['loi_moi'])) or '—'} | {(a['giai_thich'] or '—').replace('|', '/')[:100]} |" for a in agg.values()]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://localhost:8300")
    ap.add_argument("--tenant", required=True, help="mã công ty (X-Tenant-Id)")
    ap.add_argument("--users", required=True, help="email, cách nhau dấu phẩy (đăng nhập dev)")
    ap.add_argument("--out", default="walk_out")
    ap.add_argument("--slow", type=float, default=3.0)
    ap.add_argument("--retry-slow", action="store_true")
    ap.add_argument("--deep", action="store_true", help="mở thêm chi tiết dòng đầu, nguồn ô số (drill), mục open của thẻ, danh sách chọn của ô lọc")
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--workspace", default="", help="chỉ quét các phân hệ này (khoá, cách nhau dấu phẩy)")
    ap.add_argument("--fail-on-error", action="store_true", help="thoát mã 1 nếu có màn lỗi / bị từ chối")
    ap.add_argument("--fail-on-slow", action="store_true", help="cũng thoát mã 1 nếu có màn chậm")
    a = ap.parse_args()
    only = {x for x in a.workspace.split(",") if x}
    results = []
    for u in [x.strip() for x in a.users.split(",") if x.strip()]:
        r = walk_user(a.base, a.tenant, u, a.slow, a.retry_slow, a.concurrency, only, a.deep)
        results.append(r)
        s = summarize(r) if "loi_manifest" not in r else {"loi": "manifest"}
        print(f"{u}: {s}", flush=True)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "walk.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "walk.md").write_text(markdown(results, a.slow), encoding="utf-8")
    bad = any("loi_manifest" in r or any(x["phan_loai"] in ("loi", "tu_choi") or any(d["ket_qua"] in ("loi", "tu_choi", "lech") for d in x.get("sau", [])) for x in r["man"])
              for r in results)
    slow = any(x["cham"] for r in results for x in r.get("man", []))
    if (a.fail_on_error and bad) or (a.fail_on_slow and slow):
        print("FAIL: có màn lỗi / từ chối" if bad else "FAIL: có màn chậm", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
