"""Chạy migration SQL THEO GÓI (0130 lát 3). File đã chạy mà nội dung đổi ⇒ DỪNG (tạo migration mới, đừng sửa cũ: sửa file đã chạy là cách DEV
và PROD lệch schema mà không ai biết).

Bố cục: mỗi gói một thư mục ``<package của gói>/migrations/<khoá gói>/`` (gói trong repo: ``barista/migrations/<gói>/`` — ``kernel/loader.py``
``Entry.migrations``). Trong thư mục: ``NNN_<tên>.sql`` (dòng đầu ``-- module: <khoá>``), số tăng dần TRONG gói (hai gói cùng số không sao);
gói nền còn có ``000_baseline/NN_<module>.sql`` — BẢN GỐC (0085), chạy trước, là MỘT mục khi kiểm số. ``deployment.json`` (tuỳ chọn) của gói:
``{"maintenance": [tên file]}`` — migration cần dừng dịch vụ.

Thứ tự chạy: gói theo đúng thứ tự nạp (nền trước, rồi tầng → phụ thuộc → khoá); trong gói theo tên. Sổ: ``public.barista_pack_migrations``
khoá ``(pack, name)``. Mã một migration ở kế hoạch phát hành / ``until`` / ``steps``: ``"<gói>/<tên>"``.

Sổ CŨ (một dãy số chung, ``public.barista_migrations`` khoá ``filename``) — bản cài dựng trước 0130: mỗi dòng ánh xạ sang ``(gói, tên)`` theo TÊN
file (file giữ nguyên tên + nội dung khi chuyển vào thư mục gói, nên checksum giữ nguyên), tự chạy đầu mỗi lần migrate, không chạy lại file nào,
chạy lại không sao (``ON CONFLICT DO NOTHING``). Sổ cũ để nguyên (vết lịch sử). Kế hoạch phát hành (chỉ đọc) tính ánh xạ
trong bộ nhớ. Lịch sử chung trước 0130 xếp theo module dòng đầu (gói của module ĐẦU TIÊN khai). Gói đã gỡ: dòng sổ của nó được giữ, không kiểm.

DỜI / TÁCH / ĐỔI TÊN GÓI một migration ĐÃ CHẠY (``provenance.json`` của gói NHẬN — 0130, ghi chú 08/11): ``{"renamed_from": [khoá gói cũ],
"files": {tên mới: {"from": "<gói cũ>/<tên cũ>", "sha256": checksum của file cũ}}}``. Dòng sổ ``(gói cũ, tên)`` của gói đổi tên ⇒ ``(gói này,
tên)`` (cùng file, checksum giữ ⇒ vẫn kiểm như thường). Một file cũ ⇒ MỘT HAY NHIỀU file mới (dời nguyên vẹn, hoặc tách theo gói sở hữu bảng):
sổ ghi file cũ với ĐÚNG ``sha256`` khai ⇒ file mới được đánh dấu đã chạy (checksum của chính nó, giữ lúc chạy của file cũ, ``moved_from`` =
khoá cũ); sổ ghi checksum khác ⇒ DỪNG (file cũ đã chạy không phải bản mà phần tách ra chép lại). File cũ = "đã nghỉ": dòng sổ của nó giữ nguyên
(vết lịch sử — KHÔNG phải để ảnh cũ chạy lại: ảnh cũ không biết khoá mới / dữ liệu đã đổi; quay lui = phục hồi bản dump của bước bảo trì, runbook
11 + deploy/ops/deploy.py), không bị coi là file lạ. Gói CHO đi file ghi tên chúng ở ``"retired": [tên]`` của ``provenance.json`` của mình —
gói nhận bị gỡ sau này thì dòng sổ cũ ở gói cho vẫn được nhận là đã nghỉ (không thành "file lạ"). Phần tách ra thừa hưởng bước dữ liệu
(``steps``) của file gốc. Ánh xạ chạy đầu mỗi lần migrate, không chạy lại file nào, chạy lại không sao.
"""

import hashlib
import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import AsyncConnection

from barista.kernel import loader

BASELINE = "000_baseline"
LEDGER = "public.barista_pack_migrations"
LEGACY = "public.barista_migrations"
PROVENANCE = "provenance.json"
Key = tuple[str, str]   # (gói, tên trong thư mục gói)


def steps() -> dict[str, Callable[[AsyncConnection[Any]], Awaitable[object]]]:
    """BƯỚC DỮ LIỆU bằng Python chạy NGAY TRƯỚC SQL của migration mang mã, trong CÙNG giao dịch (việc SQL không làm được: đẩy thân tệp vào kho
    đối tượng — 0122). Lỗi ⇒ cả migration hoàn. Bước phải chạy lại được (chỉ làm phần chưa làm)."""
    from barista.kernel import files
    return {"base/042_files_required.sql": files.move_bytea_into_store}


@dataclass(frozen=True)
class Provenance:
    """Ánh xạ khai ở ``provenance.json`` của các gói đã nạp: ``moved`` khoá mới ⇒ (khoá cũ, checksum file cũ); ``renamed`` gói cũ ⇒ gói mới;
    ``retired`` khoá cũ không còn file (dòng sổ của chúng giữ, không kiểm)."""
    moved: dict[Key, tuple[Key, str]]
    renamed: dict[str, str]
    gone: frozenset[Key] = frozenset()   # gói cho khai ``retired`` (đúng cả khi gói nhận đã gỡ)

    @property
    def retired(self) -> set[Key]:
        return {old for old, _ in self.moved.values()} | set(self.gone)

    def origin(self, key: Key) -> Key:
        """Khoá GỐC của một file (đi ngược chuỗi dời / tách) — để phần tách ra thừa hưởng bước dữ liệu của file gốc."""
        seen = set()
        while key in self.moved and key not in seen:
            seen.add(key)
            key = self.moved[key][0]
        return key


def provenance(chains_: list[tuple[str, dict[str, Path]]]) -> Provenance:
    """Đọc ``provenance.json`` của mọi gói đã nạp + kiểm: tên mới phải là file của gói; khoá cũ không được là file đang có (đã nghỉ thật);
    gói cũ đã đổi tên không được còn nạp, không hai gói nhận cùng một gói cũ."""
    dirs = {e.key: e.migrations for e in loader.LOADED}
    current = {(pack, n) for pack, found in chains_ for n in found}
    loaded = {pack for pack, _ in chains_}
    moved: dict[Key, tuple[Key, str]] = {}
    renamed: dict[str, str] = {}
    gone: set[Key] = set()
    errors: list[str] = []
    merged: dict[str, dict[str, Path]] = {}
    for pack, found in chains_:
        merged.setdefault(pack, {}).update(found)
    for pack, found in merged.items():
        f = dirs[pack] / PROVENANCE if pack in dirs else None
        if f is None or not f.exists():
            continue
        data = json.loads(f.read_text())
        if extra := set(data) - {"renamed_from", "files", "retired"}:
            errors.append(f"{pack}/{PROVENANCE}: khoá lạ {', '.join(sorted(extra))}")
        for old in data.get("renamed_from", []):
            if old in loaded:
                errors.append(f"{pack}/{PROVENANCE}: gói cũ {old} vẫn đang nạp")
            elif old in renamed:
                errors.append(f"{pack}/{PROVENANCE}: gói cũ {old} đã được {renamed[old]} nhận")
            renamed[old] = pack
        for name in data.get("retired", []):
            if name in found:
                errors.append(f"{pack}/{PROVENANCE}: {name} khai đã nghỉ nhưng file vẫn còn")
            gone.add((pack, name))
        for name, src in data.get("files", {}).items():
            old_pack, _, old_name = str(src.get("from", "")).partition("/")
            sha = str(src.get("sha256", ""))
            if name not in found:
                errors.append(f"{pack}/{PROVENANCE}: {name} không có trong gói")
            elif not (old_pack and old_name and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)):
                errors.append(f"{pack}/{PROVENANCE}: {name} phải khai from = <gói>/<tên> và sha256 (64 ký tự hex)")
            elif (old_pack, old_name) in current:
                errors.append(f"{pack}/{PROVENANCE}: {name} khai từ {old_pack}/{old_name} nhưng file đó vẫn còn — dời / tách là nghỉ file cũ")
            else:
                moved[(pack, name)] = ((old_pack, old_name), sha)
    shas: dict[Key, set[str]] = {}
    for old, sha in moved.values():
        shas.setdefault(old, set()).add(sha)
    errors += [f"{o[0]}/{o[1]}: các phần tách khai checksum file cũ khác nhau" for o, s in shas.items() if len(s) > 1]
    if errors:
        raise RuntimeError("; ".join(errors))
    return Provenance(moved, renamed, frozenset(gone))


def chains() -> list[tuple[str, dict[str, Path]]]:
    """[(gói, {tên: file})] theo THỨ TỰ CHẠY — gói theo thứ tự nạp; trong gói: bản gốc rồi ``NNN_*.sql``. File ``.sql`` nằm ngoài thư mục của
    một gói đã nạp (gốc ``migrations/``, thư mục lạ) ⇒ DỪNG: runner không bao giờ lặng lẽ bỏ qua một migration (``stray_errors``)."""
    loader.load()
    if errors := stray_errors(roots()):
        raise RuntimeError("; ".join(errors))
    out = []
    for e in loader.LOADED:
        d = e.migrations
        found = {f"{BASELINE}/{p.name}": p for p in sorted((d / BASELINE).glob("*.sql"))}
        found.update({p.name: p for p in sorted(d.glob("*.sql"))})
        out.append((e.key, found))
    return out


def roots() -> dict[Path, set[str]]:
    """Thư mục ``migrations/`` của mỗi package có gói đã nạp ⇒ khoá các gói đặt thư mục migration ở đó."""
    out: dict[Path, set[str]] = {}
    for e in loader.LOADED:
        out.setdefault(e.migrations.parent, set()).add(e.key)
    return out


def stray_errors(roots_: dict[Path, set[str]]) -> list[str]:
    """``.sql`` mà runner sẽ KHÔNG chạy: ở gốc ``migrations/`` (dãy số chung trước 0130 — vd nhánh cũ gộp vào), trong thư mục không phải của gói
    đã nạp, hay trong thư mục con lạ của một gói (ngoài ``000_baseline/``)."""
    errors: list[str] = []
    for root, packs in roots_.items():
        if not root.is_dir():
            continue
        errors += [f"{p.name} nằm ở gốc {root.name}/ — migration phải ở {root.name}/<gói>/ (0130): chuyển vào thư mục gói sở hữu module dòng đầu"
                   for p in sorted(root.glob("*.sql"))]
        for d in sorted(x for x in root.iterdir() if x.is_dir() and not x.name.startswith(("_", "."))):
            if d.name not in packs:
                if any(d.rglob("*.sql")):
                    errors.append(f"{root.name}/{d.name}/ không phải thư mục của gói nào đã nạp — migration trong đó sẽ không chạy")
                continue
            errors += [f"{root.name}/{d.name}/{s.name}/ lạ — trong gói chỉ có NNN_*.sql và {BASELINE}/" for s in sorted(d.iterdir())
                       if s.is_dir() and s.name != BASELINE and not s.name.startswith(("_", ".")) and any(s.rglob("*.sql"))]
    return errors


def files() -> dict[str, Path]:
    """Mọi migration theo thứ tự chạy, khoá ``"<gói>/<tên>"``."""
    return {f"{pack}/{name}": p for pack, found in chains() for name, p in found.items()}


def policy() -> set[str]:
    """Mã các migration cần maintenance (``deployment.json`` của từng gói)."""
    dirs = {e.key: e.migrations for e in loader.LOADED}
    out: set[str] = set()
    for pack, found in chains():
        if (f := dirs[pack] / "deployment.json").exists():
            names = json.loads(f.read_text())["maintenance"]
            if missing := [n for n in names if n not in found]:
                raise RuntimeError(f"Chính sách triển khai của gói {pack} nhắc migration không tồn tại: {', '.join(missing)}")
            out |= {f"{pack}/{n}" for n in names}
    return out


def ordering_errors(chains_: Iterable[tuple[str, Iterable[str]]], applied: Iterable[Key], retired: Iterable[Key] = ()) -> list[str]:
    """TRONG MỖI GÓI: hai file trùng số; file số nhỏ tới sau khi số lớn hơn đã chạy ở máy đích (DB ở mỗi nơi dựng theo trình tự khác nhau);
    sổ ghi file không còn trong gói (xoá file / DB dựng từ chuỗi trước bản gốc 0085). Bản gốc là MỘT mục. Gói không cài: dòng sổ bỏ qua.
    File đã nghỉ (dời / tách sang chỗ khác — ``provenance.json``): dòng sổ bỏ qua."""
    errors: list[str] = []
    done = set(applied) - set(retired)
    for pack, names_ in chains_:
        names = list(names_)
        seen: dict[str, str] = {}
        for top in sorted({n.split("/", 1)[0] for n in names}):
            num = top.split("_", 1)[0]
            if num in seen:
                errors.append(f"Gói {pack}: hai migration cùng số {num}: {seen[num]} và {top} — đổi số một file")
            seen[num] = top
        mine = {n for p, n in done if p == pack}
        if stale := sorted(mine - set(names)):
            errors.append(f"Gói {pack}: DB còn ghi migration không có trong gói ({', '.join(stale[:3])}{'…' if len(stale) > 3 else ''}) — DB dựng "
                          "từ chuỗi trước bản gốc (0085) hoặc file đã bị xoá: dựng lại DB, không chạy chồng")
        elif mine:
            last = max(names.index(n) for n in mine)
            errors += [f"Migration {pack}/{n} chưa chạy nhưng đứng trước {pack}/{names[last]} đã chạy — đổi sang số lớn hơn rồi phát hành"
                       for n in names[:last] if n not in mine]
    return errors


def _checksum(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


async def _table(conn: AsyncConnection, name: str) -> bool:
    row = await (await conn.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))).fetchone()
    return bool(row and row[0])


def legacy_keys(legacy: Iterable[str], chains_: list[tuple[str, dict[str, Path]]], *, fresh: bool = True,
                retired: Iterable[Key] = ()) -> dict[str, Key]:
    """Dòng sổ cũ ⇒ khoá mới theo TÊN file (chỉ dòng CHƯA ánh xạ — bên gọi lọc). Tên ở hai gói đã cài ⇒ lỗi (không đoán). Tên không có ở gói
    nào đã cài ⇒ tên của một file ĐÃ NGHỈ (``retired`` — bên gọi ánh xạ tiếp qua ``provenance.json``), không thì bỏ qua: file của gói đã gỡ
    (0130 — dòng sổ của gói không cài không kiểm). Sổ cũ không có dòng bản gốc nào ⇒ DB dựng từ chuỗi trước bản gốc 0085 ⇒ lỗi (dựng lại,
    không chạy chồng) — chỉ xét khi sổ mới còn trống (``fresh``)."""
    legacy = list(legacy)
    if fresh and legacy and not any(f.startswith(f"{BASELINE}/") for f in legacy):
        raise RuntimeError(f"DB còn ghi migration không có trong repo ({', '.join(sorted(legacy)[:3])}{'…' if len(legacy) > 3 else ''}) — DB "
                           "dựng từ chuỗi trước bản gốc (0085): dựng lại DB, không chạy chồng")
    where: dict[str, list[str]] = {}
    for pack, found in chains_:
        for name in found:
            where.setdefault(name, []).append(pack)
    gone: dict[str, list[str]] = {}
    for pack, name in retired:
        gone.setdefault(name, []).append(pack)
    out = {}
    for filename in legacy:
        packs = where.get(filename) or gone.get(filename, [])
        if len(packs) > 1:
            raise RuntimeError(f"Sổ migration cũ: {filename} có ở nhiều gói ({', '.join(packs)}) — không ánh xạ được")
        if packs:
            out[filename] = (packs[0], filename)
    return out


Carry = dict[Key, tuple[str, Any, str | None]]   # khoá ⇒ (checksum, lúc chạy, moved_from) — dòng sẽ ghi vào sổ mới


async def _applied(conn: AsyncConnection, chains_: list[tuple[str, dict[str, Path]]],
                   prov: Provenance) -> tuple[dict[Key, str], Carry]:
    """(sổ mới, dòng CẦN GHI THÊM vào sổ mới). Dòng cần ghi: (1) sổ cũ CHƯA ánh xạ (tên đã có trong sổ mới, gói nào cũng được, bỏ qua — gỡ gói
    hay gói khác sau này dùng lại một tên cũ không làm vỡ); checksum phải khớp file ở chỗ mới, hoặc với file đã nghỉ thì khớp ``sha256`` khai;
    (2) ánh xạ ``provenance.json``: gói đổi tên, file dời / tách — tới khi không còn gì mới (dời nối tiếp)."""
    new: dict[Key, str] = {}
    when: dict[Key, Any] = {}
    if await _table(conn, LEDGER):
        cur = await conn.execute(f"SELECT pack, name, checksum, applied_at FROM {LEDGER}")
        for p, n, c, at in await cur.fetchall():
            new[(p, n)], when[(p, n)] = c, at
    paths = {(pack, n): p for pack, found in chains_ for n, p in found.items()}
    expected = {old: sha for old, sha in prov.moved.values()}
    carry: Carry = {}
    if await _table(conn, LEGACY):
        mapped = {n for _, n in new}
        cur = await conn.execute(f"SELECT filename, checksum, applied_at FROM {LEGACY}")
        rows = {f: (c, at) for f, c, at in await cur.fetchall() if f not in mapped}
        for filename, key in legacy_keys(rows, chains_, fresh=not new, retired=prov.retired).items():
            checksum, at = rows[filename]
            # file đã nghỉ: so với checksum khai ở gói nhận; gói nhận đã gỡ ⇒ không có gì để so (như dòng sổ của gói không cài)
            want = _checksum(paths[key]) if key in paths else expected.get(key, checksum)
            if want != checksum:
                raise RuntimeError(f"Migration {filename} (nay {key[0]}/{key[1]}) đã chạy nhưng nội dung đã đổi")
            carry[key] = (checksum, at, None)
    while True:
        have = {**{k: (c, when[k]) for k, c in new.items()}, **{k: (c, at) for k, (c, at, _) in carry.items()}}
        add: Carry = {}
        for key, (old, sha) in prov.moved.items():
            if key in have or old not in have:
                continue
            if have[old][0] != sha:
                raise RuntimeError(f"Migration {old[0]}/{old[1]} đã chạy với nội dung khác bản mà {key[0]}/{key[1]} tách / dời từ đó "
                                   f"({PROVENANCE} của gói {key[0]}) — không đánh dấu đã chạy được")
            add[key] = (_checksum(paths[key]), have[old][1], f"{old[0]}/{old[1]}")
        for (pack, name), (checksum, at) in have.items():
            if (to := prov.renamed.get(pack)) is not None and (to, name) in paths and (to, name) not in have:
                add[(to, name)] = (checksum, at, f"{pack}/{name}")
        if not add:
            return new, carry
        carry |= add


async def pending_plan(owner_url: str) -> dict[str, list[str]]:
    """Chỉ đọc: bản phát hành kiểm trước khi chạm dịch vụ / DB (deploy/ops/deploy.py chạy từ ẢNH MỚI). Hợp đồng: ``{"pending": [mã],
    "maintenance": [mã]}``, mã = ``"<gói>/<tên>"``. DB còn sổ cũ / file đã dời ⇒ ánh xạ trong bộ nhớ (không ghi)."""
    chains_ = chains()
    prov = provenance(chains_)
    maintenance = policy()
    async with await AsyncConnection.connect(owner_url) as conn:
        await conn.execute("SET TRANSACTION READ ONLY")
        new, carry = await _applied(conn, chains_, prov)
    done = {**new, **{k: c for k, (c, _, _) in carry.items()}}
    if errors := ordering_errors([(p, list(f)) for p, f in chains_], done, prov.retired):
        raise RuntimeError("; ".join(errors))
    pending = []
    for pack, found in chains_:
        for name, path in found.items():
            if (pack, name) in done:
                if _checksum(path) != done[(pack, name)]:
                    raise RuntimeError(f"Migration {pack}/{name} đã chạy nhưng nội dung đã đổi")
            else:
                pending.append(f"{pack}/{name}")
    return {"pending": pending, "maintenance": [n for n in pending if n in maintenance]}


async def migrate(owner_url: str, *, until: str | None = None) -> list[str]:
    """Trả mã các migration vừa chạy. ``until``: dừng sau migration mang mã này (chỉ cho test nâng cấp)."""
    applied: list[str] = []
    chains_ = chains()
    prov = provenance(chains_)
    policy()   # deployment.json nhắc file không có ⇒ dừng như kế hoạch phát hành
    async with await AsyncConnection.connect(owner_url, autocommit=True) as conn:
        # Nhiều tiến trình cùng khởi động (deploy nhiều bản) ⇒ chỉ một bên chạy; bên kia chờ rồi thấy đã chạy hết. Khoá theo phiên.
        await conn.execute("SELECT pg_advisory_lock(hashtextextended('barista-migrations', 0))")
        await conn.execute(f"CREATE TABLE IF NOT EXISTS {LEDGER} (pack TEXT NOT NULL, name TEXT NOT NULL, checksum TEXT NOT NULL,"
                           " applied_at TIMESTAMPTZ NOT NULL DEFAULT now(), moved_from TEXT, PRIMARY KEY (pack, name))")
        await conn.execute(f"ALTER TABLE {LEDGER} ADD COLUMN IF NOT EXISTS moved_from TEXT")   # sổ dựng trước khi có dời / tách file
        new, carry = await _applied(conn, chains_, prov)
        if carry:   # sổ cũ + file dời / tách / gói đổi tên ⇒ khoá mới — không chạy file nào, giữ checksum (file dời nguyên) + lúc chạy
            async with conn.transaction():
                for (pack, name), (checksum, at, origin) in carry.items():
                    await conn.execute(f"INSERT INTO {LEDGER} (pack, name, checksum, applied_at, moved_from) VALUES (%s, %s, %s, %s, %s)"
                                       " ON CONFLICT DO NOTHING", (pack, name, checksum, at, origin))
            new = {**new, **{k: c for k, (c, _, _) in carry.items()}}
        # cùng phép kiểm với kế hoạch phát hành, TRƯỚC khi chạy SQL: đường khởi động trực tiếp (migrate khi chạy server, seed) được bảo vệ như
        # đường phát hành. Sửa ở dev: seed --reset.
        if errors := ordering_errors([(p, list(f)) for p, f in chains_], new, prov.retired):
            raise RuntimeError("; ".join(errors))
        stepped = steps()
        for pack, found in chains_:
            for name, f in found.items():
                key = f"{pack}/{name}"
                body = f.read_text()
                checksum = hashlib.sha256(body.encode()).hexdigest()
                if (pack, name) in new:
                    if new[(pack, name)] != checksum:
                        raise RuntimeError(f"Migration {key} đã chạy nhưng nội dung đã đổi — tạo file mới thay vì sửa")
                else:
                    origin = "/".join(prov.origin((pack, name)))
                    async with conn.transaction():
                        if (step := stepped.get(key) or stepped.get(origin)) is not None:
                            await step(conn)
                        await conn.execute(body.encode())  # không tham số ⇒ giao thức đơn giản, chạy nhiều câu
                        await conn.execute(f"INSERT INTO {LEDGER} (pack, name, checksum) VALUES (%s, %s, %s)", (pack, name, checksum))
                    applied.append(key)
                if until is not None and key == until:
                    return applied
    return applied
