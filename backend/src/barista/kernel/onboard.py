"""HỢP ĐỒNG BỘ CHUYỂN DỮ LIỆU HỆ CŨ (ADR 0263 mục B, api 1.86 — gói / repo bộ chuyển import qua ``barista.api.onboard``) — bộ chuyển của một khách (repo riêng, có phiên bản + test) chỉ còn ÁNH XẠ; khung ở lõi.

Bộ chuyển khai ``Converter(source, entities, metrics)``:

* ``Entity`` — một loại bản ghi, THEO THỨ TỰ nạp (người trước, khách sau, giao dịch sau cùng…). ``extract()`` trả bản ghi TRUNG TÍNH (dict, có
  ``source_id`` — mã ở hệ cũ; tên bảng / cột cũ chỉ sống trong ``extract``); ``create`` = tên lệnh Barista + ``to_input(rec, ids)`` dựng đầu vào của
  lệnh (tham chiếu bản ghi đã nạp: ``ids.need("person", rec["owner_id"])``); ``id_key`` = khoá mã đích trong đầu ra lệnh tạo. Tuỳ chọn ``update``
  (+ ``update_input(rec, ids, target_id)``) = lệnh cập nhật khi nguồn đổi sau lần nạp trước.
  Dữ liệu nguồn sai / thiếu ⇒ ``raise Problem("bad_source", "…")`` (hoặc ``ids.need`` ném ``missing_ref``) — dòng thành NGOẠI LỆ, không tự điền.
* ``Metric`` — chỉ số ĐỐI CHIẾU (đếm, tổng tiền…): ``source()`` tính ở hệ cũ, ``target(reader)`` tính ở Barista bằng TRUY VẤN qua executor (quyền người
  chạy), cùng đơn vị; ``by="month"`` ⇒ cả hai trả ``{"YYYY-MM": số}``; ``tolerance_pct`` = ngưỡng lệch chấp nhận (phần trăm của số nguồn). Mỗi
  thực thể tự có chỉ số đếm ``count.<thực thể>`` (bản ghi trích ra ↔ bản ghi trong sổ mã gốc, ngưỡng 0).

Bộ chạy ``load(tenant_id, converter, by_email=…, kind="mock" | "final")`` — mã trong tiến trình (không ở HTTP, trợ lý không có):
* mọi lệnh nghiệp vụ chạy qua executor bằng quyền NGƯỜI CHẠY với ``historical=True`` (dữ liệu hệ cũ là tham chiếu — không thông báo, không việc theo
  dõi, lead nằm đỗ — bất biến 12), khoá chống lặp theo (nguồn, thực thể, mã gốc, dấu vân tay) ⇒ chết giữa chừng chạy lại không tạo trùng;
* với từng bản ghi: chưa có trong sổ mã gốc ⇒ ``create`` + ghi sổ; có, DẤU VÂN TAY (băm bản ghi trung tính) giữ nguyên ⇒ bỏ qua; đổi ⇒ ``update``
  nếu khai, không thì ngoại lệ "đổi sau khi nạp". Phát hiện thay đổi bằng so dấu trên TOÀN BỘ bản kéo lại — không tin ngày sửa của nguồn;
* sau lượt: tính đối chiếu từng chỉ số ⇒ ``onboard.recon`` (khớp / lệch); người ký ở màn "Lượt chuyển dữ liệu" (``onboard.sign``).
Sổ sách của lượt (mở lượt, sổ mã gốc, ngoại lệ, đối chiếu) ghi qua lệnh nội bộ của module ``onboard`` bằng quyền hệ thống — một cửa. Runbook 23.
"""

import hashlib
import inspect
import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from barista.db import db
from barista.kernel import auth, executor
from barista.kernel.principal import Principal, system_principal

__all__ = ["Converter", "Entity", "Ids", "Metric", "Problem", "Reader", "Report", "compare", "fingerprint", "load"]

log = logging.getLogger("barista.onboard")

KEY = re.compile(r"[a-z][a-z0-9_]{0,39}")
METRIC = re.compile(r"[a-z][a-z0-9_.]{0,79}")
MONTH = re.compile(r"[0-9]{4}-[0-9]{2}")
BATCH = 200   # số dòng sổ mã gốc / ngoại lệ gửi một lần


class Problem(Exception):
    """Bản ghi nguồn không vào được — thành ngoại lệ của lượt (không tự điền). ``reason``: ``bad_source`` (dữ liệu nguồn sai / thiếu) hoặc
    ``missing_ref`` (bản ghi được tham chiếu chưa nạp)."""

    def __init__(self, reason: Literal["bad_source", "missing_ref"], detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


class Ids:
    """Tra mã Barista của bản ghi đã nạp: ``ids.get("person", "17")`` ⇒ UUID | None; ``ids.need(...)`` ⇒ UUID, thiếu thì ``Problem("missing_ref")``."""

    def __init__(self, maps: dict[str, dict[str, tuple[UUID, str, int]]]) -> None:
        self._maps = maps

    def get(self, entity: str, source_id: Any) -> UUID | None:
        hit = self._maps.get(entity, {}).get(str(source_id))
        return hit[0] if hit else None

    def need(self, entity: str, source_id: Any) -> UUID:
        got = self.get(entity, source_id) if source_id not in (None, "") else None
        if got is None:
            raise Problem("missing_ref", f"{entity} {source_id!r} chưa nạp")
        return got


@dataclass(frozen=True)
class Entity:
    name: str
    extract: Callable[[], Iterable[dict[str, Any]]]
    create: str
    to_input: Callable[[dict[str, Any], Ids], dict[str, Any]]
    id_key: str
    update: str = ""
    update_input: Callable[[dict[str, Any], Ids, UUID], dict[str, Any]] | None = None
    label: str = ""


type Value = float | int | dict[str, float | int]


class Reader:
    """Đọc phía Barista cho chỉ số đối chiếu — TRUY VẤN qua executor bằng quyền người chạy (không SQL thẳng)."""

    def __init__(self, p: Principal, source: str) -> None:
        self._p, self._source = p, source

    async def query(self, name: str, /, **inp: Any) -> Any:
        r = await executor.query(self._p, name, inp)
        if r.status != "ok":
            raise RuntimeError(f"{name}: {r.status} — {r.error}")
        return r.output

    async def loaded(self, entity: str) -> int:
        """Số bản ghi của thực thể đã có trong sổ mã gốc của nguồn này."""
        r = await executor.query(system_principal(self._p.tenant_id), "onboard.loaded_count", {"source": self._source, "entity": entity})
        if r.status != "ok":
            raise RuntimeError(f"onboard.loaded_count: {r.status} — {r.error}")
        return int(r.output["n"])


@dataclass(frozen=True)
class Metric:
    name: str
    label: str
    source: Callable[[], Value | Awaitable[Value]]
    target: Callable[[Reader], Awaitable[Value]]
    by: Literal["month"] | None = None
    tolerance_pct: float = 0.0


@dataclass(frozen=True)
class Converter:
    source: str
    entities: tuple[Entity, ...]
    metrics: tuple[Metric, ...] = ()
    mapping_version: str = ""


@dataclass
class Report:
    run_id: UUID
    status: str = "running"
    created: int = 0
    updated: int = 0
    skipped: int = 0
    exceptions: int = 0
    recon: list[dict[str, Any]] = field(default_factory=list)

    @property
    def mismatches(self) -> list[dict[str, Any]]:
        return [r for r in self.recon if r["status"] == "lech"]


def fingerprint(rec: dict[str, Any]) -> str:
    """Băm SHA-256 của bản ghi trung tính (khoá xếp thứ tự, giá trị chuỗi hoá ổn định) — giống nhau ⇔ nguồn không đổi."""
    raw = json.dumps(rec, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _check(conv: Converter) -> None:
    if not KEY.fullmatch(conv.source):
        raise ValueError(f"nguồn {conv.source!r}: chữ thường_gạch_dưới, tối đa 40 ký tự")
    names = [e.name for e in conv.entities]
    if not names or len(set(names)) != len(names) or not all(KEY.fullmatch(n) for n in names):
        raise ValueError("thực thể: ít nhất một, khoá chữ thường_gạch_dưới, không trùng")
    for e in conv.entities:
        if not e.create or not e.id_key or (e.update_input is not None and not e.update):
            raise ValueError(f"thực thể {e.name}: cần create + id_key (update_input chỉ đi cùng update)")
    mnames = [x.name for x in conv.metrics]
    if len(set(mnames)) != len(mnames) or any(not METRIC.fullmatch(n) or n.startswith("count.") for n in mnames):
        raise ValueError("chỉ số: khoá chữ thường (có dấu chấm), không trùng, không bắt đầu bằng count. (dành cho chỉ số đếm tự có)")
    if any(not 0 <= x.tolerance_pct <= 100 for x in conv.metrics):
        raise ValueError("ngưỡng lệch trong 0–100 %")


async def _value(v: Value | Awaitable[Value]) -> Value:
    return await v if inspect.isawaitable(v) else v   # type: ignore[return-value]


def _buckets(v: Value, by: str | None, name: str) -> dict[str, float]:
    if by is None:
        if isinstance(v, dict):
            raise ValueError(f"chỉ số {name}: không khai by=\"month\" thì trả một số")
        return {"": float(v)}
    if not isinstance(v, dict) or not all(isinstance(k, str) and MONTH.fullmatch(k) for k in v):
        raise ValueError(f"chỉ số {name}: by=\"month\" ⇒ trả {{\"YYYY-MM\": số}}")
    return {k: float(x) for k, x in v.items()}


def compare(metric: str, label: str, src: dict[str, float], tgt: dict[str, float], tolerance_pct: float) -> list[dict[str, Any]]:
    """Từng kỳ có ở một trong hai phía (thiếu = 0): khớp khi |Barista − hệ cũ| ≤ ngưỡng % của |hệ cũ| (hệ cũ = 0 ⇒ Barista phải = 0)."""
    out = []
    for period in sorted(set(src) | set(tgt)):
        s, t = round(src.get(period, 0.0), 2), round(tgt.get(period, 0.0), 2)
        ok = abs(t - s) <= abs(s) * tolerance_pct / 100 + 1e-9
        out.append({"metric": metric, "label": label, "period": period, "source_value": s, "target_value": t, "tolerance_pct": tolerance_pct,
                    "status": "khop" if ok else "lech"})
    return out


async def _sys(tid: UUID, name: str, inp: dict[str, Any]) -> Any:
    r = await (executor.query if name in ("onboard.source_map", "onboard.loaded_count") else executor.run)(system_principal(tid), name, inp)
    if r.status != "ok":
        raise RuntimeError(f"{name}: {r.status} — {r.error}")
    return r.output


async def _person(tid: UUID, email: str) -> Principal:
    async with db.owner.connection() as conn:
        p = await auth.load(conn, tid, email=email)
    if p is None:
        raise PermissionError(f"{email}: không phải thành viên đang hoạt động của công ty")
    if not p.can("onboard", "manage"):
        raise PermissionError(f"{email}: vai không có quyền chạy chuyển dữ liệu (Đưa vào dùng · quản lý)")
    return p


class _Run:
    def __init__(self, tid: UUID, conv: Converter, run_id: UUID) -> None:
        self.tid, self.conv, self.run_id = tid, conv, run_id
        self.puts: dict[str, list[dict[str, Any]]] = {}
        self.problems: list[dict[str, Any]] = []
        self.counts: dict[str, int] = {}

    def count(self, entity: str, what: str) -> None:
        self.counts[what] = self.counts.get(what, 0) + 1
        self.counts[f"{entity}:{what}"] = self.counts.get(f"{entity}:{what}", 0) + 1

    async def problem(self, entity: str, source_id: str, reason: str, detail: str) -> None:
        self.count(entity, "exceptions")
        self.problems.append({"entity": entity, "source_id": source_id[:200], "reason": reason, "detail": detail[:2000]})
        if len(self.problems) >= BATCH:
            await self.flush()

    async def put(self, entity: str, source_id: str, target: UUID, fp: str, version: int) -> None:
        self.puts.setdefault(entity, []).append({"source_id": source_id, "target_id": str(target), "fingerprint": fp, "version": version})
        if len(self.puts[entity]) >= BATCH:
            await self.flush()

    async def flush(self) -> None:
        for entity, rows in self.puts.items():
            if rows:
                await _sys(self.tid, "onboard.put_ids", {"run_id": str(self.run_id), "source": self.conv.source, "entity": entity, "rows": rows})
        self.puts = {}
        if self.problems:
            await _sys(self.tid, "onboard.add_exceptions", {"run_id": str(self.run_id), "rows": self.problems})
            self.problems = []


def _key(source: str, entity: str, source_id: str, step: str) -> str:
    """Khoá chống lặp của MỘT thay đổi: tạo = ``new`` (mỗi mã gốc tạo đúng một lần — chạy lại sau sự cố nhận lại kết quả cũ, không tạo trùng); cập nhật
    = ``v<lần thứ n>:<dấu>`` — thử lại đúng lần đó dùng lại khoá, lần thay đổi MỚI (kể cả quay về giá trị cũ A → B → A) là số khác ⇒ khoá khác."""
    return f"onboard:{source}:{entity}:{hashlib.sha256(source_id.encode()).hexdigest()[:20]}:{step}"


async def _entity(p: Principal, run: _Run, e: Entity, maps: dict[str, dict[str, tuple[UUID, str, int]]], extracted: dict[str, set[str]]) -> None:
    ids = Ids(maps)
    seen = extracted.setdefault(e.name, set())
    have = maps.setdefault(e.name, {})
    src = run.conv.source
    for rec in e.extract():
        sid = str(rec.get("source_id") or "").strip()
        if not sid:
            await run.problem(e.name, "", "bad_source", "bản ghi không có source_id")
            continue
        if sid in seen:
            await run.problem(e.name, sid, "duplicate", "mã nguồn xuất hiện hai lần trong cùng bản trích")
            continue
        seen.add(sid)
        old = have.get(sid)
        try:
            # dấu vân tay băm ĐẦU VÀO LỆNH sau ánh xạ + phiên bản ánh xạ ⇒ nguồn đổi HOẶC sửa ánh xạ (to_input / mapping_version) đều là "đổi"
            base = e.to_input(rec, ids)
            fp = fingerprint({"mapping": run.conv.mapping_version, "input": base})
            if old is not None and old[1] == fp:
                run.count(e.name, "skipped")
                continue
            if old is not None and not e.update:
                await run.problem(e.name, sid, "changed_after_load", "nguồn / ánh xạ đổi sau lần nạp trước; thực thể không khai lệnh cập nhật — sửa tay hoặc khai update")
                continue
            if old is None:
                cmd, inp, version, step = e.create, base, 0, "new"
            else:
                version = old[2] + 1
                cmd, inp, step = e.update, (e.update_input(rec, ids, old[0]) if e.update_input is not None else {**base, e.id_key: str(old[0])}), \
                    f"v{version}:{fp[:20]}"
        except Problem as x:
            await run.problem(e.name, sid, x.reason, x.detail)
            continue
        except Exception as x:   # lỗi ánh xạ của bộ chuyển trên một bản ghi ⇒ ngoại lệ của dòng đó, không dừng cả lượt
            await run.problem(e.name, sid, "bad_source", f"ánh xạ lỗi: {type(x).__name__}: {x}")
            continue
        r = await executor.run(p, cmd, inp, key=_key(src, e.name, sid, step), historical=True)
        if r.status == "conflict" and old is None:
            await run.problem(e.name, sid, "retry_conflict", f"{cmd}: lần tạo trước bị ngắt với nội dung khác — kiểm bản ghi ở Barista rồi chạy lại")
            continue
        if r.status == "pending_approval":
            await run.problem(e.name, sid, "needs_approval", f"{cmd} phải qua duyệt ở công ty này — bỏ luật duyệt trong lúc nạp hoặc nạp bằng vai khác")
            continue
        if r.status != "ok":
            await run.problem(e.name, sid, "rejected", f"{cmd}: {r.status} — {r.error or ''}")
            continue
        if old is None:
            raw = (r.output or {}).get(e.id_key) if isinstance(r.output, dict) else None
            try:
                target = UUID(str(raw))
            except ValueError:
                await run.problem(e.name, sid, "rejected", f"{cmd} không trả mã {e.id_key}")
                continue
            run.count(e.name, "created")
        else:
            target = old[0]
            run.count(e.name, "updated")
        have[sid] = (target, fp, version)
        await run.put(e.name, sid, target, fp, version)
    for sid in sorted(set(have) - seen):   # đã nạp ở lượt trước nhưng bản trích TOÀN BỘ lần này không còn ⇒ chênh lệch cần người xử lý, không tự xoá ở Barista
        await run.problem(e.name, sid, "missing_at_source", "bản ghi đã nạp không còn trong bản trích của nguồn — xoá ở hệ cũ? Xử lý tay ở Barista nếu cần")
    await run.flush()


async def load(tenant_id: UUID, converter: Converter, *, by_email: str, kind: Literal["mock", "final"] = "mock",
               source_cut_at: datetime | None = None) -> Report:
    """Một LƯỢT chuyển trọn bộ chuyển ``converter`` vào công ty ``tenant_id``, bằng quyền người ``by_email`` (phải có quyền quản lý "Đưa vào dùng" và
    quyền chạy các lệnh của bộ chuyển). Trả ``Report``; lỗi giữa chừng ⇒ lượt ghi ``failed`` (phần đã ghi giữ nguyên, chạy lại an toàn) rồi ném lại."""
    _check(converter)
    p = await _person(tenant_id, by_email)
    opened = await _sys(tenant_id, "onboard.open_run", {"source": converter.source, "kind": kind, "mapping_version": converter.mapping_version,
                                                        "source_cut_at": source_cut_at.isoformat() if source_cut_at else None, "by_user": str(p.user_id)})
    run = _Run(tenant_id, converter, UUID(str(opened["run_id"])))
    report = Report(run.run_id)
    try:
        maps: dict[str, dict[str, tuple[UUID, str, int]]] = {}
        for e in converter.entities:
            got = await _sys(tenant_id, "onboard.source_map", {"source": converter.source, "entity": e.name})
            maps[e.name] = {r["source_id"]: (UUID(str(r["target_id"])), r["fingerprint"], int(r["version"])) for r in got["rows"]}
        extracted: dict[str, set[str]] = {}
        for e in converter.entities:
            await _entity(p, run, e, maps, extracted)
        reader = Reader(p, converter.source)
        recon: list[dict[str, Any]] = []
        for e in converter.entities:
            recon += compare(f"count.{e.name}", f"Số bản ghi: {e.label or e.name}", {"": float(len(extracted.get(e.name, ())))},
                             {"": float(await reader.loaded(e.name))}, 0.0)
        for x in converter.metrics:
            recon += compare(x.name, x.label, _buckets(await _value(x.source()), x.by, x.name), _buckets(await x.target(reader), x.by, x.name),
                             x.tolerance_pct)
        counts = {k: run.counts.get(k, 0) for k in ("created", "updated", "skipped", "exceptions")} | run.counts
        await _sys(tenant_id, "onboard.finish_run", {"run_id": str(run.run_id), "status": "done", "counts": counts, "recon": recon})
    except Exception as x:
        log.exception("onboard: lượt %s của nguồn %s lỗi", run.run_id, converter.source)
        try:
            await run.flush()
            await _sys(tenant_id, "onboard.finish_run", {"run_id": str(run.run_id), "status": "failed", "error": f"{type(x).__name__}: {x}"[:2000],
                                                         "counts": run.counts})
        except Exception:
            log.exception("onboard: không đóng được lượt %s", run.run_id)
        raise
    report.status, report.recon = "done", recon
    report.created, report.updated, report.skipped, report.exceptions = (run.counts.get(k, 0) for k in ("created", "updated", "skipped", "exceptions"))
    return report
