"""HỢP ĐỒNG BỘ CHUYỂN DỮ LIỆU HỆ CŨ (ADR 0263 mục B, api 1.86 — gói / repo bộ chuyển import qua ``barista.api.onboard``) — bộ chuyển của một khách (repo riêng, có phiên bản + test) chỉ còn ÁNH XẠ; khung ở lõi.

Bộ chuyển khai ``Converter(source, entities, metrics)``:

* ``Entity`` — một loại bản ghi, THEO THỨ TỰ nạp (người trước, khách sau, giao dịch sau cùng…). ``extract()`` trả bản ghi TRUNG TÍNH (dict, có
  ``source_id`` — mã ở hệ cũ; tên bảng / cột cũ chỉ sống trong ``extract``); ``create`` = tên lệnh Barista + ``to_input(rec, ids)`` dựng đầu vào của
  lệnh (tham chiếu bản ghi đã nạp: ``ids.need("person", rec["owner_id"])``). Tuỳ chọn ``update`` (+ ``update_input(rec, ids, target_id)``) = lệnh cập
  nhật khi nguồn đổi sau lần nạp trước. MÃ ĐÍCH của bản ghi — khai ĐÚNG MỘT trong ba:
    - ``id_key`` = khoá mã (UUID) trong đầu ra lệnh tạo;
    - ``target=(output, input, ids) -> UUID | None`` = hàm lấy mã khi lệnh không trả thẳng (lệnh hành động trên bản ghi đã nạp: mã chính là bản ghi
      đó; lệnh có lúc trả mã lúc không ⇒ ``None`` = dòng này không có mã);
    - ``keyless=True`` = lệnh chỉ GHI NHẬN (ký, ghi nhận thanh toán, cấu hình tiên quyết…) — sổ mã gốc ghi dòng không mã đích; thực thể khác kiểm "đã
      nạp chưa" bằng ``ids.require("deal_sign", …)``; ``ids.need`` / ``ids.get`` trên thực thể này là lỗi cấu hình (``ConverterError`` — dừng lượt).
  ``actor`` = khoá NGƯỜI CHẠY của thực thể (``load(actors={"ke_toan": "<email>"})`` — kế toán nhập phiếu thu, người khác xác nhận…): mỗi nhóm thực thể
  chạy bằng quyền của đúng người có vai đó; trống = người chạy lượt (``by_email``). ``settle=True`` = giao hết sự kiện đang chờ sau MỖI lệnh (thực thể
  sau, kể cả bản ghi kế trong cùng thực thể, cần kết quả của bên nghe); mặc định khung giao sau mỗi THỰC THỂ. ``lookup=async (rec, input, reader) ->
  input`` = bổ sung đầu vào bằng TRUY VẤN phía Barista (quyền người chạy thực thể) ngay trước khi chạy lệnh — không vào dấu vân tay.
  Dữ liệu nguồn sai / thiếu ⇒ ``raise Problem("bad_source", "…")`` (hoặc ``ids.need`` ném ``missing_ref``) — dòng thành NGOẠI LỆ, không tự điền.
* ``Metric`` — chỉ số ĐỐI CHIẾU (đếm, tổng tiền…): ``source()`` tính ở hệ cũ, ``target(reader)`` tính ở Barista bằng TRUY VẤN qua executor (quyền người
  chạy lượt, hoặc người khai ở ``actor``), cùng đơn vị; ``by="month"`` ⇒ cả hai trả ``{"YYYY-MM": số}``; ``tolerance_pct`` = ngưỡng lệch chấp nhận (phần
  trăm của số nguồn). Mỗi thực thể tự có chỉ số đếm ``count.<thực thể>`` (bản ghi trích ra ↔ bản ghi trong sổ mã gốc, ngưỡng 0). Một chỉ số lỗi khi tính
  ⇒ CHỈ chỉ số đó ghi ``loi`` (không tính được — chưa đối chiếu), lượt vẫn xong.

Bộ chạy ``load(tenant_id, converter, by_email=…, kind="mock" | "final", actors=…, approver_email=…)`` — mã trong tiến trình (không ở HTTP, trợ lý không có):
* TRƯỚC khi chạy: kiểm quyền từng (người chạy, lệnh) của bộ chuyển ⇒ thiếu thì MỘT ``PermissionError`` liệt kê đủ, không mở lượt; lệnh nào đang có luật
  duyệt mà lượt không khai người duyệt ⇒ ghi vào ``Report.warnings``.
* mọi lệnh nghiệp vụ chạy qua executor bằng quyền NGƯỜI CHẠY của thực thể với ``historical=True`` (dữ liệu hệ cũ là tham chiếu — không thông báo, không
  việc theo dõi, lead nằm đỗ — bất biến 12), khoá chống lặp theo (nguồn, thực thể, mã gốc, lần thay đổi) thuộc LƯỢT NẠP của công ty — không theo người
  chạy ⇒ chết giữa chừng chạy lại (kể cả bằng người khác) không tạo trùng;
* với từng bản ghi: chưa có trong sổ mã gốc ⇒ ``create`` + ghi sổ; có, DẤU VÂN TAY (băm đầu vào lệnh sau ánh xạ) giữ nguyên ⇒ bỏ qua; đổi ⇒ ``update``
  nếu khai, không thì ngoại lệ "đổi sau khi nạp". Phát hiện thay đổi bằng so dấu trên TOÀN BỘ bản kéo lại — không tin ngày sửa của nguồn;
* LỆNH PHẢI QUA DUYỆT: mã phiếu ghi vào sổ ``onboard.pending``; lượt sau hỏi trạng thái phiếu — đã thực hiện ⇒ ghi sổ mã gốc (không còn là ngoại lệ),
  còn chờ ⇒ ngoại lệ "chờ duyệt", bị từ chối / lỗi khi chạy sau duyệt ⇒ ngoại lệ "lệnh từ chối" (gửi lại khi nguồn / ánh xạ đổi). Khai ``approver_email``
  (một hoặc vài NGƯỜI THỨ HAI có thật trong công ty, có tên trong chuỗi duyệt; không trùng người chạy nào) ⇒ khung quyết ngay các phiếu do lượt sinh ra
  bằng đúng ``core.decide`` qua ``executor.decide_in_load`` — phiếu + nhật ký ghi tên người duyệt, lệnh đích chạy bằng quyền người xin (bất biến 6);
* sự kiện: giao hết sự kiện đang chờ (``kernel/events.drain`` — sự kiện của lượt nạp mang dấu nạp lịch sử) sau mỗi thực thể / mỗi lệnh ``settle``;
* sau lượt: tính đối chiếu từng chỉ số ⇒ ``onboard.recon`` (khớp / lệch / lỗi); người ký ở màn "Lượt chuyển dữ liệu" (``onboard.sign``).
Sổ sách của lượt (mở lượt, sổ mã gốc, phiếu chờ, ngoại lệ, đối chiếu) ghi qua lệnh nội bộ của module ``onboard`` bằng quyền hệ thống — một cửa. Runbook 23.
"""

import hashlib
import inspect
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from barista.db import db
from barista.kernel import auth, events, executor, registry
from barista.kernel.principal import Principal, system_principal

__all__ = ["Converter", "ConverterError", "Entity", "Ids", "Metric", "Problem", "Reader", "Report", "compare", "fingerprint", "load"]

log = logging.getLogger("barista.onboard")

KEY = re.compile(r"[a-z][a-z0-9_]{0,39}")
METRIC = re.compile(r"[a-z][a-z0-9_.]{0,79}")
MONTH = re.compile(r"[0-9]{4}-[0-9]{2}")
BATCH = 200   # số dòng sổ mã gốc / ngoại lệ gửi một lần

type Loaded = tuple[UUID | None, str, int]   # (mã đích — None = không có mã, dấu vân tay, số lần đã cập nhật)


class Problem(Exception):
    """Bản ghi nguồn không vào được — thành ngoại lệ của lượt (không tự điền). ``reason``: ``bad_source`` (dữ liệu nguồn sai / thiếu) hoặc
    ``missing_ref`` (bản ghi được tham chiếu chưa nạp)."""

    def __init__(self, reason: Literal["bad_source", "missing_ref"], detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


class ConverterError(Exception):
    """BỘ CHUYỂN khai sai (không phải dữ liệu nguồn sai): hỏi mã đích của thực thể ``keyless``, tham chiếu thực thể không có trong bộ chuyển, lệnh tạo
    không trả mã như ``id_key`` khai… ⇒ DỪNG lượt với câu lỗi rõ — sửa bộ chuyển rồi chạy lại (phần đã ghi giữ nguyên, khoá chống lặp không tạo trùng)."""


class _Stop(Exception):
    """Nội bộ: bản ghi này dừng ở đây với một dòng ngoại lệ (lý do của sổ ``onboard.exceptions``)."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason, self.detail = reason, detail


class Ids:
    """Tra bản ghi đã nạp: ``ids.get("person", "17")`` ⇒ UUID | None; ``ids.need(...)`` ⇒ UUID, thiếu thì ``Problem("missing_ref")``; ``ids.has(...)`` /
    ``ids.require(...)`` chỉ hỏi ĐÃ NẠP CHƯA (dùng được cho thực thể ``keyless`` — không có mã đích)."""

    def __init__(self, maps: dict[str, dict[str, Loaded]], keyless: frozenset[str] = frozenset(), known: frozenset[str] | None = None) -> None:
        self._maps, self._keyless, self._known = maps, keyless, known

    def _row(self, entity: str, source_id: Any) -> Loaded | None:
        if self._known is not None and entity not in self._known:
            raise ConverterError(f"tham chiếu thực thể {entity!r} không có trong bộ chuyển")
        return self._maps.get(entity, {}).get(str(source_id)) if source_id not in (None, "") else None

    def _keyed(self, entity: str) -> None:
        if entity in self._keyless:
            raise ConverterError(f"thực thể {entity!r} khai keyless (lệnh chỉ ghi nhận, không có mã đích) — hỏi “đã nạp chưa” bằng ids.has / ids.require")

    def has(self, entity: str, source_id: Any) -> bool:
        return self._row(entity, source_id) is not None

    def require(self, entity: str, source_id: Any) -> None:
        if self._row(entity, source_id) is None:
            raise Problem("missing_ref", f"{entity} {source_id!r} chưa nạp")

    def get(self, entity: str, source_id: Any) -> UUID | None:
        self._keyed(entity)
        hit = self._row(entity, source_id)
        return hit[0] if hit else None

    def need(self, entity: str, source_id: Any) -> UUID:
        self._keyed(entity)
        hit = self._row(entity, source_id)
        if hit is None:
            raise Problem("missing_ref", f"{entity} {source_id!r} chưa nạp")
        if hit[0] is None:
            raise Problem("missing_ref", f"{entity} {source_id!r} đã nạp nhưng lệnh không trả mã đích")
        return hit[0]


type Value = float | int | dict[str, float | int]


class Reader:
    """Đọc phía Barista cho chỉ số đối chiếu / ``Entity.lookup`` — TRUY VẤN qua executor bằng quyền người chạy (không SQL thẳng)."""

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
class Entity:
    name: str
    extract: Callable[[], Iterable[dict[str, Any]]]
    create: str
    to_input: Callable[[dict[str, Any], Ids], dict[str, Any]]
    id_key: str = ""
    update: str = ""
    update_input: Callable[[dict[str, Any], Ids, Any], dict[str, Any]] | None = None   # tham số thứ ba: mã đích (UUID; None với thực thể không mã)
    label: str = ""
    target: Callable[[Any, dict[str, Any], Ids], UUID | None] | None = None
    keyless: bool = False
    actor: str = ""
    settle: bool = False
    lookup: Callable[[dict[str, Any], dict[str, Any], Reader], Awaitable[dict[str, Any]]] | None = None


@dataclass(frozen=True)
class Metric:
    name: str
    label: str
    source: Callable[[], Value | Awaitable[Value]]
    target: Callable[[Reader], Awaitable[Value]]
    by: Literal["month"] | None = None
    tolerance_pct: float = 0.0
    actor: str = ""


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
    entities: dict[str, dict[str, float]] = field(default_factory=dict)   # thực thể → {extracted, created, updated, skipped, exceptions, seconds}
    reasons: list[dict[str, Any]] = field(default_factory=list)          # ngoại lệ gộp: [{entity, reason, n}] — nhiều trước
    warnings: list[str] = field(default_factory=list)                    # điều người chạy nên biết trước khi đọc số (lệnh có luật duyệt mà không khai người duyệt…)
    approvals_decided: int = 0                                           # lần người duyệt của lượt (``approver_email``) quyết một bước phiếu
    events_delivered: int = 0                                            # sự kiện khung đã giao giữa các lệnh / thực thể

    @property
    def mismatches(self) -> list[dict[str, Any]]:
        """Dòng đối chiếu CHƯA khớp: lệch, hoặc chỉ số không tính được (``loi``)."""
        return [r for r in self.recon if r["status"] != "khop"]


def fingerprint(rec: dict[str, Any]) -> str:
    """Băm SHA-256 của bản ghi trung tính (khoá xếp thứ tự, giá trị chuỗi hoá ổn định) — giống nhau ⇔ nguồn không đổi."""
    raw = json.dumps(rec, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _check(conv: Converter, actors: Mapping[str, str]) -> None:
    if not KEY.fullmatch(conv.source):
        raise ValueError(f"nguồn {conv.source!r}: chữ thường_gạch_dưới, tối đa 40 ký tự")
    names = [e.name for e in conv.entities]
    if not names or len(set(names)) != len(names) or not all(KEY.fullmatch(n) for n in names):
        raise ValueError("thực thể: ít nhất một, khoá chữ thường_gạch_dưới, không trùng")
    for e in conv.entities:
        if not e.create or (e.update_input is not None and not e.update):
            raise ValueError(f"thực thể {e.name}: cần create (update_input chỉ đi cùng update)")
        if sum((bool(e.id_key), e.target is not None, e.keyless)) != 1:
            raise ValueError(f"thực thể {e.name}: khai ĐÚNG MỘT cách lấy mã đích — id_key (khoá trong đầu ra lệnh tạo), target= (hàm), hoặc keyless=True "
                             "(lệnh chỉ ghi nhận, không có mã đích)")
    mnames = [x.name for x in conv.metrics]
    if len(set(mnames)) != len(mnames) or any(not METRIC.fullmatch(n) or n.startswith("count.") for n in mnames):
        raise ValueError("chỉ số: khoá chữ thường (có dấu chấm), không trùng, không bắt đầu bằng count. (dành cho chỉ số đếm tự có)")
    if any(not 0 <= x.tolerance_pct <= 100 for x in conv.metrics):
        raise ValueError("ngưỡng lệch trong 0–100 %")
    wanted = {e.actor for e in conv.entities} | {x.actor for x in conv.metrics}
    if missing := sorted(a for a in wanted if a and a not in actors):
        raise ValueError(f"bộ chuyển khai người chạy {', '.join(missing)} — nêu email của từng người ở load(actors={{…}})")
    if bad := sorted(a for a in actors if not KEY.fullmatch(a)):
        raise ValueError(f"khoá người chạy {', '.join(bad)}: chữ thường_gạch_dưới")


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


_QUERIES = ("onboard.source_map", "onboard.loaded_count", "onboard.pending_map", "core.approval_states")


async def _sys(tid: UUID, name: str, inp: dict[str, Any]) -> Any:
    r = await (executor.query if name in _QUERIES else executor.run)(system_principal(tid), name, inp)
    if r.status != "ok":
        raise RuntimeError(f"{name}: {r.status} — {r.error}")
    return r.output


async def _member(tid: UUID, email: str, what: str) -> Principal:
    async with db.owner.connection() as conn:
        p = await auth.load(conn, tid, email=email)
    if p is None or p.user_id is None:
        raise PermissionError(f"{what} {email}: không phải thành viên đang hoạt động của công ty")
    return p


async def _preflight(conv: Converter, runner: Principal, people: dict[str, Principal], approving: bool) -> list[str]:
    """Kiểm quyền từng (người chạy, lệnh) TRƯỚC khi mở lượt ⇒ thiếu thì MỘT danh sách (không để hàng nghìn dòng bị từ chối giữa lượt). Trả cảnh báo: lệnh đang
    có luật duyệt mà lượt không khai người duyệt."""
    missing: list[str] = []
    pairs: dict[tuple[str, str], None] = {}
    for e in conv.entities:
        for cmd in (e.create, e.update):
            if cmd:
                pairs[(e.actor, cmd)] = None
    for actor, cmd in pairs:
        p, who = people[actor], (f"người chạy “{actor}” ({people[actor].email})" if actor else f"người chạy lượt ({runner.email})")
        op = registry.get(cmd)
        if op is None or op.kind != "command" or op.internal:
            missing.append(f"{cmd}: không có lệnh này")
        elif not executor.allowed(p, op):
            need = f"{executor.ACTION_LABEL[op.action]} “{registry.RESOURCES.get(op.resource or '', op.resource)}”" \
                + (f" phạm vi {op.min_scope} trở lên" if op.min_scope else "")
            missing.append(f"{cmd} ({op.title}): {who}, vai {p.role_label or p.role}, không chạy được — cần quyền {need}, và gói của lệnh đang bật")
    if missing:
        raise PermissionError("Bộ chuyển chưa chạy được — thiếu quyền / lệnh (chưa ghi gì):\n- " + "\n- ".join(missing)
                              + "\nGiao thực thể cho đúng người có vai đó (Entity(actor=…) + load(actors=…)); không cấp quyền tạm, không tạo vai toàn quyền.")
    warnings: list[str] = []
    if not approving:
        ruled: dict[str, str] = {}
        for actor in dict.fromkeys(a for a, _ in pairs):
            async with db.tenant_tx(people[actor]) as conn:
                for a, cmd in pairs:
                    if a == actor and cmd not in ruled and (chain := await executor.approval_chain(conn, people[actor], cmd)):
                        ruled[cmd] = str(chain)
        warnings += [f"{cmd}: công ty đặt luật duyệt (chuỗi {chain}) — lượt không khai approver_email ⇒ dòng của lệnh này thành phiếu chờ người duyệt trong app; "
                     "lượt sau ghi nhận phiếu đã duyệt" for cmd, chain in ruled.items()]
    return warnings


class _Run:
    def __init__(self, tid: UUID, conv: Converter, run_id: UUID, people: dict[str, Principal], approvers: list[Principal]) -> None:
        self.tid, self.conv, self.run_id, self.people, self.approvers = tid, conv, run_id, people, approvers
        self.puts: dict[str, list[dict[str, Any]]] = {}
        self.waits: dict[str, list[dict[str, Any]]] = {}
        self.problems: list[dict[str, Any]] = []
        self.counts: dict[str, int] = {}
        self.reasons: dict[tuple[str, str], int] = {}
        self.decided = 0
        self.delivered = 0

    def count(self, entity: str, what: str) -> None:
        self.counts[what] = self.counts.get(what, 0) + 1
        self.counts[f"{entity}:{what}"] = self.counts.get(f"{entity}:{what}", 0) + 1

    async def problem(self, entity: str, source_id: str, reason: str, detail: str) -> None:
        self.count(entity, "exceptions")
        self.reasons[(entity, reason)] = self.reasons.get((entity, reason), 0) + 1
        self.problems.append({"entity": entity, "source_id": source_id[:200], "reason": reason, "detail": detail[:2000]})
        if len(self.problems) >= BATCH:
            await self.flush()

    async def put(self, entity: str, source_id: str, target: UUID | None, fp: str, version: int) -> None:
        self.puts.setdefault(entity, []).append({"source_id": source_id, "target_id": str(target) if target else None, "fingerprint": fp, "version": version})
        if len(self.puts[entity]) >= BATCH:
            await self.flush()

    async def wait(self, entity: str, source_id: str, approval_id: UUID, fp: str, version: int, attempt: int) -> None:
        self.waits.setdefault(entity, []).append({"source_id": source_id, "approval_id": str(approval_id), "fingerprint": fp, "version": version,
                                                  "attempt": attempt})
        if len(self.waits[entity]) >= BATCH:
            await self.flush()

    async def flush(self) -> None:
        base = {"run_id": str(self.run_id), "source": self.conv.source}
        for entity, rows in self.waits.items():   # phiếu chờ TRƯỚC sổ mã gốc: dòng vừa ghi sổ đóng luôn phiếu chờ của nó
            if rows:
                await _sys(self.tid, "onboard.put_pending", {**base, "entity": entity, "rows": rows})
        self.waits = {}
        for entity, rows in self.puts.items():
            if rows:
                await _sys(self.tid, "onboard.put_ids", {**base, "entity": entity, "rows": rows})
        self.puts = {}
        if self.problems:
            await _sys(self.tid, "onboard.add_exceptions", {"run_id": str(self.run_id), "rows": self.problems})
            self.problems = []

    async def states(self, ids: Iterable[UUID]) -> dict[UUID, dict[str, Any]]:
        """Trạng thái phiếu duyệt theo mã — hỏi lõi bằng truy vấn nội bộ (không đọc thẳng bảng)."""
        want, out = list(dict.fromkeys(ids)), {}
        for i in range(0, len(want), 500):
            got = await _sys(self.tid, "core.approval_states", {"ids": [str(x) for x in want[i:i + 500]]})
            out.update({UUID(str(r["id"])): r for r in got["rows"]})
        return out

    async def settle(self, approval_id: UUID) -> dict[str, Any] | None:
        """Trạng thái MỚI NHẤT của một phiếu; còn chờ và lượt có người duyệt (``approver_email``) ⇒ người đó quyết từng bước mình có tên, qua đúng đường quyết
        phiếu của lõi (``executor.decide_in_load`` → ``core.decide``). Một người không quyết hai bước của cùng phiếu (luật của lõi) ⇒ chuỗi nhiều bước cần
        đủ người. None = không thấy phiếu."""
        st = (await self.states([approval_id])).get(approval_id)
        tried: set[UUID] = set()
        while st is not None and st["status"] == "pending" and self.approvers:
            may = {UUID(str(x)) for x in st["approver_ids"] or []}
            who = next((a for a in self.approvers if a.user_id in may and a.user_id not in tried), None)
            if who is None or who.user_id is None:
                st["why"] = "không người duyệt nào của lượt nạp có tên ở bước này"
                break
            tried.add(who.user_id)
            r = await executor.decide_in_load(who, approval_id, note=f"Duyệt trong lượt chuyển dữ liệu từ hệ cũ (nguồn {self.conv.source}, lượt {self.run_id})")
            if r.status != "ok":
                st["why"] = f"không quyết được: {r.error or r.status}"
                break
            self.decided += 1
            st = (await self.states([approval_id])).get(approval_id)
        return st


async def _deliver() -> int:
    """Giao HẾT sự kiện đang tới hạn bằng đúng hàm giao của lõi. ``events.drain`` dừng sau một số vòng cố định (một thực thể vài chục nghìn lệnh phát nhiều
    hơn thế) ⇒ gọi tới khi một lượt không còn gì; sự kiện giao lỗi đã được lõi lùi hạn nên vòng này luôn dừng."""
    total = 0
    while n := await events.drain():
        total += n
    return total


_FP_TARGET = UUID(int=0)   # mã đích giả CHỈ để băm đầu vào cập nhật (không bao giờ gửi đi)


def _key(source: str, entity: str, source_id: str, step: str) -> str:
    """Khoá chống lặp của MỘT thay đổi: tạo = ``new`` (mỗi mã gốc tạo đúng một lần — chạy lại sau sự cố nhận lại kết quả cũ, không tạo trùng); cập nhật
    = ``v<lần thứ n>:<dấu>`` — thử lại đúng lần đó dùng lại khoá, lần thay đổi MỚI (kể cả quay về giá trị cũ A → B → A) là số khác ⇒ khoá khác; gửi lại
    sau một phiếu duyệt không chạy được thêm ``:r<lần gửi>``. Khoá thuộc lượt nạp của công ty (``executor.run(load_key=True)``), không thuộc người chạy."""
    return f"onboard:{source}:{entity}:{hashlib.sha256(source_id.encode()).hexdigest()[:20]}:{step}"


def _mapped[T](fn: Callable[..., T], *args: Any) -> T:
    """Gọi một hàm ánh xạ của bộ chuyển: ``Problem`` ⇒ ngoại lệ của dòng; lỗi khác của hàm trên MỘT bản ghi ⇒ ngoại lệ của dòng đó, không dừng cả lượt;
    ``ConverterError`` (bộ chuyển khai sai) đi tiếp ⇒ dừng lượt."""
    try:
        return fn(*args)
    except Problem as x:
        raise _Stop(x.reason, x.detail) from x
    except ConverterError:
        raise
    except Exception as x:
        raise _Stop("bad_source", f"ánh xạ lỗi: {type(x).__name__}: {x}") from x


def _target(e: Entity, cmd: str, output: Any, inp: dict[str, Any], ids: Ids) -> UUID | None:
    """Mã đích của bản ghi vừa tạo. Lệnh ĐÃ ghi ⇒ ở đây không còn "ngoại lệ của dòng": hoặc có mã, hoặc không mã (keyless / target trả None), hoặc bộ
    chuyển khai sai (dừng lượt — chạy lại nhận đúng kết quả cũ nhờ khoá chống lặp)."""
    if e.keyless:
        return None
    if e.target is not None:
        try:
            got = e.target(output, inp, ids)
        except ConverterError:
            raise
        except Exception as x:
            raise ConverterError(f"thực thể {e.name}: hàm target lỗi sau khi {cmd} đã ghi — {type(x).__name__}: {x}") from x
        if got is not None and not isinstance(got, UUID):
            raise ConverterError(f"thực thể {e.name}: hàm target phải trả UUID hoặc None (đang trả {type(got).__name__})")
        return got
    raw = output.get(e.id_key) if isinstance(output, dict) else None
    try:
        return UUID(str(raw))
    except ValueError:
        raise ConverterError(f"thực thể {e.name}: {cmd} không trả mã ở khoá {e.id_key!r} — lệnh này khai target= (hàm lấy mã) hoặc keyless=True "
                             "(lệnh chỉ ghi nhận), không khai id_key") from None


@dataclass
class _Waiting:
    approval_id: UUID
    fingerprint: str
    version: int
    attempt: int


async def _record(run: _Run, e: Entity, p: Principal, ids: Ids, reader: Reader, rec: dict[str, Any], sid: str, have: dict[str, Loaded],
                  waiting: dict[str, _Waiting], states: dict[UUID, dict[str, Any]]) -> None:
    """MỘT bản ghi: bỏ qua / tạo / cập nhật / ghi nhận phiếu đã duyệt — hoặc ``_Stop`` (một dòng ngoại lệ)."""
    src = run.conv.source
    old = have.get(sid)
    # dấu vân tay băm ĐẦU VÀO LỆNH sau ánh xạ + phiên bản ánh xạ ⇒ nguồn đổi HOẶC sửa ánh xạ (to_input / mapping_version) đều là "đổi";
    # cả đầu vào lệnh CẬP NHẬT (tính với một mã đích cố định cho ổn định) — trường chỉ cập nhật mới dùng đổi cũng là "đổi"
    base = _mapped(e.to_input, rec, ids)
    upd = _mapped(e.update_input, rec, ids, _FP_TARGET) if e.update_input is not None else None
    fp = fingerprint({"mapping": run.conv.mapping_version, "input": base, "update": upd})
    if old is not None and old[1] == fp:
        run.count(e.name, "skipped")
        return
    attempt = 0
    if (w := waiting.get(sid)) is not None:   # lần trước lệnh của bản ghi này thành PHIẾU DUYỆT — hỏi phiếu, không chạy lại khoá cũ
        st = states.get(w.approval_id)
        if st is not None and st["status"] == "pending" and w.fingerprint == fp:
            st = await run.settle(w.approval_id)
        status = st["status"] if st is not None else "gone"
        if status == "pending":
            assert st is not None
            raise _Stop("needs_approval", f"phiếu {w.approval_id} đang chờ duyệt" + (f" (bước “{st['step']}”)" if st.get("step") else "")
                        + (f" — {st['why']}" if st.get("why") else "")
                        + (" — nguồn / ánh xạ đã đổi từ lúc gửi: từ chối phiếu cũ để lượt sau gửi phiếu mới" if w.fingerprint != fp else ""))
        if status == "executed":   # phiếu đã duyệt, lệnh đã chạy ⇒ GHI NHẬN (với dấu của lần gửi)
            assert st is not None
            landed: Loaded = (old[0] if old is not None else _target(e, str(st.get("command") or e.create), st.get("result"), st.get("input") or base, ids),
                              w.fingerprint, w.version)
            run.count(e.name, "created" if old is None else "updated")
            have[sid] = old = landed
            await run.put(e.name, sid, *landed)
            del waiting[sid]
            if e.settle:
                run.delivered += await _deliver()
            if w.fingerprint == fp:
                return
        elif w.fingerprint == fp and (status == "rejected" or (status == "failed" and not run.approvers)):
            # người duyệt nói KHÔNG, hoặc duyệt xong lệnh từ chối mà lượt này không có người duyệt ⇒ không tự gửi phiếu mới mỗi lượt
            assert st is not None
            raise _Stop("rejected", f"phiếu {w.approval_id} " + ("bị từ chối" if status == "rejected" else f"đã duyệt nhưng lệnh không chạy được: {st.get('error') or ''}")
                        + " — sửa nguồn / ánh xạ (hoặc đổi mapping_version) để gửi lại")
        else:   # hết căn cứ / không còn phiếu / lệnh lỗi sau duyệt (lượt có người duyệt) / nguồn đã đổi sau phiếu hỏng ⇒ gửi lại bằng khoá của lần gửi mới
            attempt = w.attempt + 1
    if old is not None and not e.update:
        raise _Stop("changed_after_load", "nguồn / ánh xạ đổi sau lần nạp trước; thực thể không khai lệnh cập nhật — sửa tay hoặc khai update")
    if old is None:
        cmd, inp, version, step = e.create, base, 0, "new"
    else:
        version = old[2] + 1
        cmd, step = e.update, f"v{version}:{fp[:20]}"
        inp = _mapped(e.update_input, rec, ids, old[0]) if e.update_input is not None else {**base, e.id_key: str(old[0])} if e.id_key else base
    if attempt:
        step += f":r{attempt}"
    if e.lookup is not None:   # bổ sung từ phía Barista (không vào dấu vân tay) — chỉ khi lệnh sắp chạy thật
        try:
            inp = await e.lookup(rec, inp, reader)
        except Problem as x:
            raise _Stop(x.reason, x.detail) from x
        except ConverterError:
            raise
        except Exception as x:
            raise _Stop("rejected", f"tra cứu phía Barista lỗi: {type(x).__name__}: {x}") from x
    r = await executor.run(p, cmd, inp, key=_key(src, e.name, sid, step), historical=True, load_key=True)
    output = r.output
    if r.status == "conflict" and old is None:
        raise _Stop("retry_conflict", f"{cmd}: lần tạo trước bị ngắt với nội dung khác — kiểm bản ghi ở Barista rồi chạy lại ({r.error or ''})")
    if r.status == "pending_approval":
        aid = UUID(str(r.approval_id))
        st = await run.settle(aid)
        status = st["status"] if st is not None else "gone"
        if status != "executed":
            await run.wait(e.name, sid, aid, fp, version, attempt)
            waiting[sid] = _Waiting(aid, fp, version, attempt)
            if status == "pending":
                assert st is not None
                raise _Stop("needs_approval", f"{cmd} phải qua duyệt ở công ty này — phiếu {aid} chờ duyệt" + (f" (bước “{st['step']}”)" if st.get("step") else "")
                            + (f" — {st['why']}" if st.get("why") else " — lượt sau ghi nhận khi phiếu đã duyệt; hoặc khai approver_email (người thứ hai)"))
            raise _Stop("rejected", f"{cmd}: phiếu {aid} " + (f"đã duyệt nhưng lệnh không chạy được: {(st or {}).get('error') or ''}" if status == "failed"
                                                             else "bị từ chối" if status == "rejected" else "không chạy (hết căn cứ / không còn phiếu)"))
        assert st is not None
        output = st.get("result")
    elif r.status != "ok":
        raise _Stop("rejected", f"{cmd}: {r.status} — {r.error or ''}")
    target = _target(e, cmd, output, inp, ids) if old is None else old[0]
    run.count(e.name, "created" if old is None else "updated")
    have[sid] = (target, fp, version)
    await run.put(e.name, sid, target, fp, version)
    waiting.pop(sid, None)
    if e.settle:   # thực thể sau (hoặc bản ghi kế) cần kết quả của bên nghe sự kiện lệnh này phát
        run.delivered += await _deliver()


async def _entity(run: _Run, e: Entity, ids: Ids, maps: dict[str, dict[str, Loaded]], waits: dict[str, dict[str, _Waiting]],
                  extracted: dict[str, set[str]]) -> None:
    p = run.people[e.actor]
    reader = Reader(p, run.conv.source)
    seen = extracted.setdefault(e.name, set())
    have = maps.setdefault(e.name, {})
    waiting = waits.setdefault(e.name, {})
    states = await run.states(w.approval_id for w in waiting.values())
    for rec in e.extract():
        sid = str(rec.get("source_id") or "").strip()
        if not sid:
            await run.problem(e.name, "", "bad_source", "bản ghi không có source_id")
            continue
        if sid in seen:
            await run.problem(e.name, sid, "duplicate", "mã nguồn xuất hiện hai lần trong cùng bản trích")
            continue
        seen.add(sid)
        try:
            await _record(run, e, p, ids, reader, rec, sid, have, waiting, states)
        except _Stop as x:
            await run.problem(e.name, sid, x.reason, x.detail)
    for sid in sorted(set(have) - seen):   # đã nạp ở lượt trước nhưng bản trích TOÀN BỘ lần này không còn ⇒ chênh lệch cần người xử lý, không tự xoá ở Barista
        await run.problem(e.name, sid, "missing_at_source", "bản ghi đã nạp không còn trong bản trích của nguồn — xoá ở hệ cũ? Xử lý tay ở Barista nếu cần")
    await run.flush()
    # sự kiện các lệnh của thực thể này phát (bên nghe lập bản ghi mà thực thể SAU cần) — đúng hàm giao của lõi, cùng chế độ nạp lịch sử theo dấu của sự kiện
    run.delivered += await _deliver()


def _report(run: _Run, report: Report, extracted: dict[str, set[str]], seconds: dict[str, float]) -> None:
    report.created, report.updated, report.skipped, report.exceptions = (run.counts.get(k, 0) for k in ("created", "updated", "skipped", "exceptions"))
    report.entities = {e.name: {"extracted": len(extracted.get(e.name, ())), **{k: run.counts.get(f"{e.name}:{k}", 0)
                                                                                  for k in ("created", "updated", "skipped", "exceptions")},
                                "seconds": round(seconds.get(e.name, 0.0), 1)} for e in run.conv.entities}
    report.reasons = [{"entity": k[0], "reason": k[1], "n": n} for k, n in sorted(run.reasons.items(), key=lambda kv: (-kv[1], kv[0]))]
    report.approvals_decided, report.events_delivered = run.decided, run.delivered


async def load(tenant_id: UUID, converter: Converter, *, by_email: str, kind: Literal["mock", "final"] = "mock",
               source_cut_at: datetime | None = None, actors: Mapping[str, str] | None = None,
               approver_email: str | Sequence[str] = "") -> Report:
    """Một LƯỢT chuyển trọn bộ chuyển ``converter`` vào công ty ``tenant_id``. ``by_email`` = người chạy lượt (quyền quản lý "Đưa vào dùng"; chạy các thực thể
    không khai ``actor``); ``actors`` = {khoá người chạy của bộ chuyển: email} — từng người phải có quyền chạy lệnh của thực thể mình (kiểm TRƯỚC khi mở
    lượt, thiếu ⇒ ``PermissionError`` liệt kê đủ); ``approver_email`` = người thứ hai (hoặc vài người, theo các bước của chuỗi) quyết các phiếu duyệt lượt
    này sinh ra — không trùng người chạy nào. Trả ``Report``; lỗi giữa chừng ⇒ lượt ghi ``failed`` (phần đã ghi giữ nguyên, chạy lại an toàn) rồi ném lại."""
    actors = dict(actors or {})
    _check(converter, actors)
    runner = await _member(tenant_id, by_email, "người chạy lượt")
    if not runner.can("onboard", "manage"):
        raise PermissionError(f"{by_email}: vai không có quyền chạy chuyển dữ liệu (Đưa vào dùng · quản lý)")
    people = {"": runner} | {k: await _member(tenant_id, v, f"người chạy “{k}”") for k, v in actors.items()}
    emails = [approver_email] if isinstance(approver_email, str) else list(approver_email)
    approvers = [await _member(tenant_id, x, "người duyệt") for x in emails if x.strip()]
    if own := sorted({a.email for a in approvers if a.user_id in {p.user_id for p in people.values()}}):
        raise PermissionError(f"{', '.join(own)}: người chạy không tự duyệt — approver_email phải là NGƯỜI KHÁC mọi người chạy của lượt (bất biến 6)")
    warnings = await _preflight(converter, runner, people, bool(approvers))
    opened = await _sys(tenant_id, "onboard.open_run", {"source": converter.source, "kind": kind, "mapping_version": converter.mapping_version,
                                                        "source_cut_at": source_cut_at.isoformat() if source_cut_at else None, "by_user": str(runner.user_id)})
    run = _Run(tenant_id, converter, UUID(str(opened["run_id"])), people, approvers)
    report = Report(run.run_id, warnings=warnings)
    extracted: dict[str, set[str]] = {}
    seconds: dict[str, float] = {}
    try:
        maps: dict[str, dict[str, Loaded]] = {}
        for e in converter.entities:
            got = await _sys(tenant_id, "onboard.source_map", {"source": converter.source, "entity": e.name})
            maps[e.name] = {r["source_id"]: (UUID(str(r["target_id"])) if r["target_id"] else None, r["fingerprint"], int(r["version"])) for r in got["rows"]}
        waits: dict[str, dict[str, _Waiting]] = {}
        for r in (await _sys(tenant_id, "onboard.pending_map", {"source": converter.source}))["rows"]:
            waits.setdefault(r["entity"], {})[r["source_id"]] = _Waiting(UUID(str(r["approval_id"])), r["fingerprint"], int(r["version"]), int(r["attempt"]))
        ids = Ids(maps, frozenset(e.name for e in converter.entities if e.keyless), frozenset(e.name for e in converter.entities))
        for e in converter.entities:
            t0 = time.perf_counter()
            try:
                await _entity(run, e, ids, maps, waits, extracted)
            finally:
                seconds[e.name] = time.perf_counter() - t0
                run.counts[f"{e.name}:ms"] = int(seconds[e.name] * 1000)
        recon: list[dict[str, Any]] = []
        for e in converter.entities:
            recon += compare(f"count.{e.name}", f"Số bản ghi: {e.label or e.name}", {"": float(len(extracted.get(e.name, ())))},
                             {"": float(await Reader(runner, converter.source).loaded(e.name))}, 0.0)
        for x in converter.metrics:
            try:
                recon += compare(x.name, x.label, _buckets(await _value(x.source()), x.by, x.name),
                                 _buckets(await x.target(Reader(people[x.actor], converter.source)), x.by, x.name), x.tolerance_pct)
            except Exception as err:   # một chỉ số không tính được ⇒ CHỈ chỉ số đó là lỗi (chưa đối chiếu — chặn như lệch cho tới khi ký), lượt vẫn xong
                log.exception("onboard: chỉ số %s của nguồn %s không tính được", x.name, converter.source)
                recon.append({"metric": x.name, "label": x.label, "period": "", "source_value": 0.0, "target_value": 0.0, "tolerance_pct": x.tolerance_pct,
                              "status": "loi", "note": f"{type(err).__name__}: {err}"[:500]})
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
    _report(run, report, extracted, seconds)
    return report
