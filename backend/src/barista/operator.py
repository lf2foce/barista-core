"""CỬA NHÀ VẬN HÀNH (ADR 0206): đổi cấu hình của một công ty — đặt quyền cho vai, tạo vai, cài / xoay khoá / thu hồi app từ xa, quyết phiếu đổi quyền
đang chờ — KHÔNG qua tài khoản siêu quản trị trong app và không ai tự duyệt. Chạy trên máy chủ của bản cài (quyền vào máy chủ là lớp xác thực thứ
hai), qua CÙNG executor bằng quyền hệ thống (một cửa — bất biến 1); thao tác thuộc tài nguyên ``core.operator`` (không vai nào của khách cấp được,
``internal=True`` — không có đường HTTP / MCP / trợ lý / app). Mỗi lệnh ghi bắt buộc ``--by`` (người vận hành) + ``--why`` (lý do) vào nhật ký thao tác
(không hiện ở Nhật ký của khách; xem bằng lệnh ``log``). ``--dry-run``: kiểm + chạy thử trong giao dịch rồi hoàn — không ghi gì. Runbook 17.

  uv run python -m barista.operator --tenant "Sàn mẫu" create-role phenau_feed "Nguồn dữ liệu Phê Nâu" --by "Anh" --why "app nạp dữ liệu MKT"
  uv run python -m barista.operator --tenant "Sàn mẫu" set-grant phenau_feed mkt all read,write --by "Anh" --why "app nạp dữ liệu MKT"
  uv run python -m barista.operator --tenant "Sàn mẫu" set-setting acct.use_remittances off --by "Anh" --why "công ty không thu hộ"
  uv run python -m barista.operator --tenant "Sàn mẫu" install-app --name "Phê Nâu" --like-role phenau_feed \\
      --grants "mkt:all:read,write catalog:all:read,write" --secret-file /root/phenau-app.env --by "Anh" --why "HĐ 11/2026"
  uv run python -m barista.operator --tenant "Sàn mẫu" rotate-app "Phê Nâu" --secret-file /root/phenau-app-2.env --by "Anh" --why "lộ khoá"
  uv run python -m barista.operator --tenant "Sàn mẫu" revoke-app "Phê Nâu" --by "Anh" --why "ngừng hợp đồng"
  uv run python -m barista.operator --tenant "Sàn mẫu" install-defaults --kind record --dry-run --by "Anh" --why "mẫu văn bản mới của gói"
  uv run python -m barista.operator --tenant "Sàn mẫu" install-defaults --only "marketer|crm" --by "Anh" --why "quyền mới của gói"
  uv run python -m barista.operator --tenant "Sàn mẫu" pending
  uv run python -m barista.operator --tenant "Sàn mẫu" decide <mã phiếu> approve --by "Anh" --why "khách yêu cầu qua email 12/11"
  uv run python -m barista.operator --tenant "Sàn mẫu" log
  uv run python -m barista.operator create-company --name "Công ty Mẫu" --admin-email quantri@congty.vn --admin-name "Quản trị" \\
      --packs crm,finance --by "Anh" --why "HĐ 12/2026 — đưa vào dùng (runbook 23)"
"""

import argparse
import asyncio
import os
import re
from pathlib import Path
from typing import Any
from uuid import UUID

import barista.modules  # noqa: F401 — nạp mọi module để registry đủ (như seed)
from barista import ai_policy
from barista.config import settings
from barista.db import db
from barista.kernel import executor
from barista.kernel.principal import system_principal

WRITES = ("create-company", "set-grant", "set-setting", "create-role", "role-tools-defaults", "skills-defaults", "install-defaults", "install-app", "rotate-app", "revoke-app", "app-shadow", "decide")
NO_ACTIONS = ("", "-", "none")


class Refused(SystemExit):
    """Lệnh bị từ chối / lỗi — mã thoát 1 kèm lời (``SystemExit`` mang chữ in ra stderr)."""


def grants_text(raw: str) -> str:
    """``"mkt:all:read,write catalog:all:read,write"`` (cách nhau dấu cách) hoặc ``"…; …"`` → dạng ``connect`` hiểu (``;``)."""
    return ";".join(p for p in re.split(r"[;\s]+", re.sub(r",\s+", ",", raw.strip())) if p)


async def run(tid: UUID, command: str, inp: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    r = await executor.run(system_principal(tid), command, inp, dry_run=dry_run)
    if r.status not in ("ok", "dry_run"):
        raise Refused(f"{command}: {r.status} — {r.error}")
    return r.output or {}


async def query(tid: UUID, name: str, inp: dict[str, Any] | None = None) -> Any:
    r = await executor.query(system_principal(tid), name, inp or {})
    if r.status != "ok":
        raise Refused(f"{name}: {r.status} — {r.error}")
    return r.output


async def app_id_of(tid: UUID, ref: str) -> UUID:
    """App theo mã app, client_id hoặc tên (đúng một app đang chạy khớp)."""
    rows = (await query(tid, "connect.apps"))["rows"]
    hit = [r for r in rows if r["status"] == "active" and ref in (str(r["id"]), r["client_id"], r["name"])]
    if len(hit) != 1:
        raise Refused(f"Cần đúng một app đang chạy khớp “{ref}”, đang có {len(hit)}: " + "; ".join(f"{r['name']} ({r['client_id']})" for r in hit))
    return hit[0]["id"]


def _reserve(path: str) -> int:
    """Mở TRƯỚC khi cài / xoay khoá (O_EXCL, quyền 600): đã có file / không ghi được ⇒ dừng trước khi có khoá nào được sinh."""
    try:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as e:
        raise Refused(f"Không tạo được file khoá {path}: {e.strerror} (file phải CHƯA có; thư mục ghi được)") from e


def deliver(out: dict[str, Any], fd: int | None, path: str) -> None:
    """Khoá in MỘT lần: vào file (quyền 600, ưu tiên) hoặc ra màn hình — không bao giờ vào log / nhật ký / bảng chống lặp (``once_*``)."""
    lines = [f"BARISTA_CLIENT_ID={out['client_id']}", f"BARISTA_CLIENT_SECRET={out['once_client_secret']}",
             f"BARISTA_TOKEN_URL={out['token_url']}", f"BARISTA_WEBHOOK_SECRET={out['once_webhook_secret']}"]
    print(f"client_id: {out['client_id']}")
    if fd is not None:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
        print(f"Khoá đã ghi vào {path} (quyền 600) — chuyển cho bên app qua kênh an toàn rồi xoá file. Barista không hiện lại.")
        return
    print("Khoá (hiện MỘT lần — chép ngay, Barista không hiện lại):")
    print("\n".join(lines))


def _discard(fd: int | None, path: str) -> None:
    """Lệnh không thành ⇒ bỏ file khoá đã giữ chỗ (rỗng)."""
    if fd is not None:
        os.close(fd)
        Path(path).unlink(missing_ok=True)


async def _issue(tid: UUID, command: str, inp: dict[str, Any], a: argparse.Namespace) -> None:
    """Lệnh SINH khoá (cài / xoay khoá): chạy thử ⇒ chỉ mô tả, không khoá; chạy thật ⇒ giữ chỗ file trước, khoá in / ghi MỘT lần."""
    if a.dry_run:
        print(f"[chạy thử — không ghi gì] {(await run(tid, command, inp, dry_run=True))['summary']}")
        return
    fd = _reserve(a.secret_file) if a.secret_file else None
    try:
        out = await run(tid, command, inp, dry_run=False)
    except BaseException:
        _discard(fd, a.secret_file)
        raise
    print(out["summary"])
    deliver(out, fd, a.secret_file)


async def install_app(tid: UUID, a: argparse.Namespace) -> None:
    await _issue(tid, "connect.operator_install", {"name": a.name, "like_role": a.like_role, "grants": grants_text(a.grants), "events": a.events,
                                                   "webhook_url": a.webhook_url, "by": a.by, "why": a.why}, a)


async def rotate_app(tid: UUID, a: argparse.Namespace) -> None:
    await _issue(tid, "connect.operator_rotate", {"app_id": str(await app_id_of(tid, a.app)), "by": a.by, "why": a.why}, a)


async def pending(tid: UUID) -> None:
    rows = (await query(tid, "core.operator_pending"))["rows"]
    if not rows:
        print("Không có phiếu nào đang chờ")
    for r in rows:
        print(f"  {r['id']}  {r['created_at']:%Y-%m-%d %H:%M}  [{r['chain']}] {r['summary']} — người xin {r['requester']} <{r['email']}>, "
              f"bước: {r['step']}" + ("" if r["operator"] else "  (phiếu nghiệp vụ — nhà vận hành không quyết)"))


def company_packs(raw: str) -> list[str]:
    """``"crm,finance"`` ⇒ gói bật cho công ty (kèm gói chúng bắt buộc, bỏ gói luôn bật — không có dòng ``tenant_packs``). Gói lạ ⇒ từ chối."""
    from barista.kernel import registry

    asked = [x.strip() for x in re.split(r"[,\s]+", raw) if x.strip()]
    if bad := [x for x in asked if x not in registry.PACKS]:
        raise Refused(f"Gói lạ: {', '.join(bad)} — gói cài trên bản này: {', '.join(sorted(registry.PACKS))}")
    return sorted(registry.pack_closure(set(asked)) - registry.always_on())


async def create_company(a: argparse.Namespace) -> None:
    """TẠO CÔNG TY THẬT (ADR 0263 mục D, đóng 0194 Còn mở 3) trên bản cài ``production`` — hoặc bất kỳ bản cài nào — KHÔNG nạp dữ liệu mẫu: công ty + gói
    (kèm quyền dùng gói) + hồ sơ, cài mặc định của các gói qua ``admin.install_defaults``, mời quản trị đầu tiên (đăng nhập thật bằng email đó) qua
    ``admin.invite_user``, giao chủ tài khoản (``admin.ensure_owner``). Mọi bước nghiệp vụ qua executor bằng quyền hệ thống của công ty mới. Trợ lý AI
    mặc định TẮT (bật bằng ``--ai`` khi khách đã đồng ý). Lần trước lỗi giữa chừng (công ty cùng tên chưa có ai) ⇒ chạy lại làm tiếp, không tạo công ty thứ hai."""
    from barista.kernel import clock

    name, email = a.name.strip(), a.admin_email.strip().lower()
    if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise Refused("Cần --name và --admin-email hợp lệ")
    packs = company_packs(a.packs)
    if a.dry_run:
        print(f"[chạy thử — không ghi gì] Tạo “{name}”, gói: {', '.join(packs) or '(chỉ nền)'}; mời {email} vai {a.admin_role}; trợ lý AI "
              f"{'bật' if a.ai else 'tắt'}")
        return
    async with db.owner_tx() as c:
        rows = await (await c.execute("""SELECT t.id, (SELECT count(*) FROM core.memberships m WHERE m.tenant_id = t.id) AS members
                                         FROM core.tenants t WHERE t.name = %s""", (name,))).fetchall()
        if len(rows) > 1 or (rows and rows[0]["members"]):
            raise Refused(f"Đã có công ty “{name}” đang dùng — đổi tên, hoặc cài thêm bằng install-defaults / mời người trong app")
        if rows:
            tid = rows[0]["id"]
            print(f"Làm tiếp công ty “{name}” tạo dở ({tid})")
        else:
            got = await (await c.execute("INSERT INTO core.tenants (name, timezone) VALUES (%s, %s) RETURNING id",
                                         (name, clock.default_zone().key))).fetchone()
            assert got is not None
            tid = got["id"]
            await c.execute("INSERT INTO core.tenant_profile (tenant_id, about, ai_enabled) VALUES (%s, %s, %s)", (tid, a.about.strip()[:600], a.ai))
        for pack in packs:
            await c.execute("INSERT INTO core.tenant_packs (tenant_id, pack) VALUES (%s, %s) ON CONFLICT DO NOTHING", (tid, pack))
            await c.execute("""INSERT INTO core.tenant_entitlements (tenant_id, pack, granted_by, note) VALUES (%s, %s, 'operator', %s)
                               ON CONFLICT DO NOTHING""", (tid, pack, f"{a.by.strip()}: {a.why.strip()}"[:300]))
    out = await run(tid, "admin.install_defaults", {"packs": ["base", *packs]}, dry_run=False)
    print(f"{name}: {out.get('summary') or 'cài mặc định xong'}")
    await run(tid, "admin.invite_user", {"email": email, "name": a.admin_name.strip() or email, "role": a.admin_role, "team_code": "", "is_lead": False},
              dry_run=False)
    await run(tid, "admin.ensure_owner", {}, dry_run=False)
    print(f"OK: công ty “{name}” ({tid}) · gói {', '.join(packs) or '(chỉ nền)'} · quản trị đầu tiên {email} (vai {a.admin_role}, chủ tài khoản) — "
          f"đăng nhập bằng Google với đúng email này. Bước tiếp: Quản trị › Đưa công ty vào dùng (runbook 23)")


async def main(a: argparse.Namespace) -> None:
    if a.cmd in WRITES and not (a.by.strip() and a.why.strip()):
        raise Refused("Cần --by (người vận hành) và --why (lý do) — ghi vào nhật ký vận hành")
    if a.cmd != "create-company" and not a.tenant.strip():
        raise Refused("Cần --tenant (tên một phần hoặc id công ty)")
    await db.open(settings.database_url, settings.owner_database_url)
    try:
        if a.cmd == "create-company":
            await create_company(a)
            return
        tid, name = await ai_policy.tenant_of(a.tenant)
        who = {"by": getattr(a, "by", ""), "why": getattr(a, "why", "")}
        dry = bool(getattr(a, "dry_run", False))
        tag = "[chạy thử — không ghi gì] " if dry else ""
        if a.cmd == "set-grant":
            acts = [] if a.actions.strip().lower() in NO_ACTIONS else [x.strip() for x in a.actions.split(",") if x.strip()]
            out = await run(tid, "admin.operator_set_grant", {"role": a.role, "resource": a.resource, "scope": a.scope, "actions": acts, **who},
                            dry_run=dry)
            print(f"{tag}{name}: {out['summary']}")
        elif a.cmd == "set-setting":
            out = await run(tid, "admin.operator_set_setting", {"key": a.key, "enabled": a.state == "on", **who}, dry_run=dry)
            print(f"{tag}{name}: {out['summary']}")
        elif a.cmd == "create-role":
            out = await run(tid, "admin.operator_create_role", {"key": a.key, "label": a.label, "copy_from": a.copy_from, **who}, dry_run=dry)
            print(f"{tag}{name}: {out['summary']}")
        elif a.cmd == "role-tools-defaults":
            out = await run(tid, "admin.operator_role_tools", who, dry_run=dry)
            print(f"{tag}{name}: {out['summary']}")
        elif a.cmd == "skills-defaults":
            out = await run(tid, "admin.operator_skills", who, dry_run=dry)
            print(f"{tag}{name}: {out['summary']}")
        elif a.cmd == "install-defaults":
            out = await run(tid, "admin.operator_install_defaults", {"kinds": a.kind, "only": a.only, "everything": a.all, **who}, dry_run=dry)
            print(f"{tag}{name}: {out['summary']}")
            for x in out["installed"]:
                print(f"  + {x}")
        elif a.cmd == "install-app":
            await install_app(tid, a)
        elif a.cmd == "rotate-app":
            await rotate_app(tid, a)
        elif a.cmd == "revoke-app":
            out = await run(tid, "connect.operator_revoke", {"app_id": str(await app_id_of(tid, a.app)), **who}, dry_run=dry)
            print(f"{tag}{name}: thu hồi app — {out['tokens_revoked']} token, {out['webhooks_cancelled']} thư webhook chờ bị huỷ")
        elif a.cmd == "app-shadow":
            out = await run(tid, "connect.operator_shadow", {"app_id": str(await app_id_of(tid, a.app)), "on": a.state == "on", **who}, dry_run=dry)
            print(f"{tag}{name}: {out['summary']}")
        elif a.cmd == "pending":
            await pending(tid)
        elif a.cmd == "decide":
            out = await run(tid, "core.operator_decide", {"approval_id": a.approval_id, "approve": a.decision == "approve", **who}, dry_run=dry)
            print(f"{tag}{name}: phiếu {a.approval_id} → {out.get('status')}" + (f" — {out['error']}" if out.get("error") else "")
                  + (f" — {out['warning']}" if out.get("warning") else ""))
        elif a.cmd == "log":
            await ai_policy.log(tid, a.limit)
    finally:
        await db.close()


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m barista.operator", description="Cửa nhà vận hành — đổi cấu hình một công ty (ADR 0206)")
    ap.add_argument("--tenant", default="", help="tên (một phần) hoặc id công ty — phải khớp đúng một công ty (mọi lệnh trừ create-company)")
    write = argparse.ArgumentParser(add_help=False)
    write.add_argument("--by", default="", help="người vận hành (bắt buộc)")
    write.add_argument("--why", default="", help="lý do (bắt buộc)")
    write.add_argument("--dry-run", action="store_true", help="kiểm + chạy thử rồi hoàn — không ghi gì, không sinh khoá")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cc = sub.add_parser("create-company", parents=[write], help="tạo công ty THẬT (không dữ liệu mẫu): gói + cài mặc định + mời quản trị đầu tiên (0263)")
    cc.add_argument("--name", required=True, help="tên công ty")
    cc.add_argument("--admin-email", required=True, help="email đăng nhập (Google) của quản trị đầu tiên — thành chủ tài khoản")
    cc.add_argument("--admin-name", default="", help="họ tên quản trị đầu tiên")
    cc.add_argument("--admin-role", default="admin", help="vai của người đó (mặc định admin — Quản trị hệ thống; giám đốc: director)")
    cc.add_argument("--packs", default="", help="gói bật cho công ty, cách nhau dấu phẩy (gói bắt buộc tự kèm), vd crm,finance")
    cc.add_argument("--about", default="", help="một câu về công ty cho trợ lý (sửa sau ở Quản trị › Hồ sơ công ty)")
    cc.add_argument("--ai", action="store_true", help="bật trợ lý AI ngay (mặc định tắt tới khi khách đồng ý)")
    g = sub.add_parser("set-grant", parents=[write], help="đặt quyền của một vai (cùng kiểm như Quản trị › Vai trò & quyền, không thành phiếu)")
    g.add_argument("role")
    g.add_argument("resource")
    g.add_argument("scope", choices=("own", "unit", "team", "all"))
    g.add_argument("actions", help="read,write,approve,manage — “none” = thu hồi")
    st = sub.add_parser("set-setting", parents=[write], help="bật / tắt một cài đặt công ty (cùng kiểm như Quản trị › Cài đặt công ty) — vd nghiệp vụ "
                        "công ty không làm thì tắt để ẩn màn của nó")
    st.add_argument("key", help="khoá cài đặt, vd acct.use_remittances")
    st.add_argument("state", choices=("on", "off"))
    r = sub.add_parser("create-role", parents=[write], help="tạo vai mới (vd vai tích hợp chỉ làm khuôn cho app)")
    r.add_argument("key")
    r.add_argument("label")
    r.add_argument("--from", dest="copy_from", default="", help="chép quyền từ vai này")
    sub.add_parser("role-tools-defaults", parents=[write], help="cài bộ công cụ trợ lý mặc định (0218) cho vai CHƯA có bộ — không đụng vai / quyền khác")
    sub.add_parser("skills-defaults", parents=[write], help="cài kỹ năng trợ lý mặc định (0198) công ty CHƯA có — giữ nguyên kỹ năng đã sửa / đã ngừng")
    idf = sub.add_parser("install-defaults", parents=[write], help="cài CÓ CHỌN LỌC phần mặc định chưa cài (như báo cáo barista.defaults_report) — chỉ phần "
                                                                  "được chọn, cùng logic nút Cài phần còn thiếu")
    idf.add_argument("--kind", action="append", default=[], choices=("role", "team", "grant", "chain", "rule", "flow", "vocab", "setting", "record"),
                     help="chỉ loại này (lặp được)")
    idf.add_argument("--only", action="append", default=[], help="chỉ mục có khoá này, đúng như báo cáo in (lặp được), vd "
                                                                   "“<lệnh>:<khoá>” (dòng cấu hình), “<vai>|<tài nguyên>” (quyền)")
    idf.add_argument("--all", action="store_true", help="mọi mục còn thiếu (như nút trên màn) — phải nói rõ")
    i = sub.add_parser("install-app", parents=[write], help="cài app từ xa — quyền app ≤ quyền của --like-role; in khoá MỘT lần")
    i.add_argument("--name", required=True)
    i.add_argument("--like-role", required=True, help="vai bảo lãnh: quyền app ≤ quyền hiện tại của vai này (lúc cài và mỗi lần app gọi)")
    i.add_argument("--grants", required=True, help="“tài nguyên:own|all:read,write …” cách nhau dấu cách hoặc “;”")
    i.add_argument("--events", default="", help="sự kiện webhook, cách nhau dấu phẩy (cần --webhook-url)")
    i.add_argument("--webhook-url", "--addresses", dest="webhook_url", default="", help="https://… nhận webhook đã ký")
    i.add_argument("--secret-file", default="", help="ghi khoá vào file MỚI quyền 600 thay vì in ra màn hình (nên dùng)")
    ro = sub.add_parser("rotate-app", parents=[write], help="khoá mới ngay (khoá cũ + token hết hiệu lực); in khoá MỘT lần")
    ro.add_argument("app", help="tên, client_id hoặc mã app")
    ro.add_argument("--secret-file", default="")
    rv = sub.add_parser("revoke-app", parents=[write], help="thu hồi app ngay")
    rv.add_argument("app", help="tên, client_id hoặc mã app")
    sh = sub.add_parser("app-shadow", parents=[write], help="app CHỈ GHI NHẬN (on: dữ liệu vào đủ nhưng chưa giao cho ai — không báo, không tính hạn, không webhook) "
                                                           "hoặc CHẠY THẬT (off) — 0207")
    sh.add_argument("app", help="tên, client_id hoặc mã app")
    sh.add_argument("state", choices=("on", "off"))
    sub.add_parser("pending", help="phiếu đang chờ duyệt")
    d = sub.add_parser("decide", parents=[write], help="quyết phiếu ĐỔI QUYỀN đang chờ (thay chủ tài khoản); lệnh chạy bằng quyền người xin")
    d.add_argument("approval_id")
    d.add_argument("decision", choices=("approve", "reject"))
    lg = sub.add_parser("log", help="nhật ký lệnh của nhà vận hành (người + lý do)")
    lg.add_argument("--limit", type=int, default=30)
    return ap


if __name__ == "__main__":
    asyncio.run(main(parser().parse_args()))
