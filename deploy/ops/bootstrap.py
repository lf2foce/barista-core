#!/usr/bin/env python3
"""Chuẩn bị MỘT lần trên VPS (người vận hành chạy, CI không gọi được). Chỉ thư viện chuẩn.

  bootstrap.py [--instance <tên>] prepare
                         — BẢN CÀI theo instances/<tên>.json (chép từ barista-ops) (không nêu ⇒ bản cài chứa file này — /root/<tên>/ops; không suy ra được ⇒ dừng). Thư mục /root/<tên> (700), bí mật sinh tại chỗ,
                           project Dokploy "<dokploy_project, mặc định project>": compose "<project> Database" (deploy ngay, volume giữ mãi) + stack "<project> App"
                           (chưa deploy — lần phát hành đầu dùng `deploy.py [--instance <tên>] manual-v2 <sha>`), operations.json, cron sao
                           lưu. Chạy lại an toàn: thứ đã có thì giữ.

Bí mật không bao giờ in ra trừ mật khẩu cổng web + khoá demo (người dùng cần để vào) — in MỘT lần khi vừa sinh. Khoá Gemini đặt trước
ở /root/<tên>/llm.key (600) rồi bị xoá sau khi chép vào runtime.env.

runtime.env chạy lại (review F3): bootstrap chỉ SỞ HỮU các khoá trong ``_runtime`` — ``owned`` (đặt lại mỗi lần: chế độ đăng nhập, cổng, địa chỉ
nội bộ…) và ``once`` (sinh / tính một lần, đã có thì giữ). MỌI khoá khác đang có trong runtime.env (Google, cảnh báo Telegram, R2, TURN, tổng đài,
BARISTA_PACKS… — người vận hành thêm sau) giữ nguyên giá trị: không có danh sách cho phép nào phải nhớ cập nhật.
"""

import json
import secrets
import time
from pathlib import Path

from deploy import Deployment, command, default_instance, load_instance, render, write_private

HERE = Path(__file__).resolve().parent


def _dokploy() -> Deployment:
    d = object.__new__(Deployment)
    d.key = Path("/root/.dokploy-api-key").read_text().strip()
    d.dokploy_container = command("docker", "ps", "-q", "--filter", "label=com.docker.swarm.service.name=dokploy")
    return d


def _environment(d: Deployment, inst: dict) -> str:
    # Project Dokploy chứa hai compose của bản cài: mặc định = "project" của bản cài; "dokploy_project" (tuỳ chọn) cho nhiều bản cài DEMO cùng một
    # project cho gọn. CHỈ dùng để tìm / tạo project — tên compose ("<project> Database" / "<project> App") vẫn theo "project" riêng của bản cài nên không trùng.
    project = inst.get("dokploy_project") or inst["project"]
    description = inst.get("dokploy_project_description") or inst["project_description"]

    def find() -> str | None:
        for p in d.api("project.all"):
            if p["name"] == project:
                envs = p.get("environments") or []
                return next((e["environmentId"] for e in envs if e["name"] == "production"), envs[0]["environmentId"] if envs else None)
        return None
    if (eid := find()) is None:
        d.api("project.create", {"name": project, "description": description})
        eid = find()
    if eid is None:
        raise RuntimeError(f"Không tạo / tìm được environment của project {project}")
    return eid


def _secrets(root: Path, inst: dict) -> dict[str, str]:
    path = root / "secrets.json"
    if path.exists():
        return json.loads(path.read_text())
    gate_pw = "-".join(secrets.token_urlsafe(4) for _ in range(3))
    s = {k: secrets.token_urlsafe(24) for k in ("POSTGRES_PASSWORD", "OWNER_PASSWORD", "APP_PASSWORD", "BARISTA_SESSION_SECRET")}
    # "gate": false (ADR 0194) không dùng cổng — vẫn sinh để smoke / bật lại cổng sau không cần sửa secrets.json
    s.update(BARISTA_DEMO_KEY=secrets.token_urlsafe(18), gate_user=inst.get("gate_user") or inst["name"], gate_password=gate_pw)
    write_private(path, s)
    print(f"\n*** Mật khẩu cổng web (trình duyệt hỏi): người dùng '{s['gate_user']}' / mật khẩu '{gate_pw}'")
    print(f"*** Mã truy cập demo cho app Mac / iPhone (Cài đặt › Bản demo): {s['BARISTA_DEMO_KEY']}\n", flush=True)
    return s


def _runtime(root: Path, inst: dict, s: dict[str, str], app_name: str) -> str:
    llm_file = root / "llm.key"
    old = dict(line.split("=", 1) for line in (root / "runtime.env").read_text().splitlines() if "=" in line) \
        if (root / "runtime.env").exists() else {}
    llm = llm_file.read_text().strip() if llm_file.exists() else old.get("BARISTA_LLM_API_KEY", "")
    if not llm:
        raise RuntimeError(f"Thiếu khoá Gemini: đặt vào {llm_file} (chmod 600) rồi chạy lại")
    gate = command("openssl", "passwd", "-apr1", s["gate_password"])
    db = inst["db_host"]
    production = inst.get("env") == "production"   # = deploy.production (ADR 0194)
    once = {
        # kho tệp (0122): bản cài khai "files_store" (vd lc: local) ⇒ đặt sẵn khi runtime.env chưa có — KHÔNG chép cấu hình kho của bản cài khác (runbook 11)
        **({"BARISTA_FILES_STORE": inst["files_store"]} if inst.get("files_store") else {}),
        # khoá ký link kho thư mục — đổi ⇒ link đã gửi hỏng
        "BARISTA_FILES_SIGN_SECRET": secrets.token_urlsafe(32),
        # app từ xa (0140): khoá dẫn xuất khoá ký webhook (đổi ⇒ mọi app phải nhận lại khoá)
        "BARISTA_APPS_SIGNING_SECRET": secrets.token_urlsafe(32),
        # khoá proxy biên (review 0150 lát 4): Traefik của stack + BFF gắn X-Barista-Edge, lõi chỉ tin X-Forwarded-For khi đúng khoá (barista/edge.py)
        "BARISTA_EDGE_SECRET": secrets.token_urlsafe(32),
        "BARISTA_CONNECTOR_KEY": secrets.token_urlsafe(48),   # khung kết nối (0244): mã hoá khoá kết nối của công ty — mất ⇒ quản trị dán lại khoá
        # người vận hành đổi tên DB khi dựng lại từ bản gốc (runbook 11 "Dựng lại DB") ⇒ chạy lại prepare không trả về DB cũ
        "BARISTA_DATABASE_URL": f"postgresql://barista_app:{s['APP_PASSWORD']}@{db}:5432/barista",
        "BARISTA_OWNER_DATABASE_URL": f"postgresql://barista_owner:{s['OWNER_PASSWORD']}@{db}:5432/barista",
    }
    owned = {
        "BARISTA_LLM_API_KEY": llm, "BARISTA_DEMO_KEY": s["BARISTA_DEMO_KEY"], "BARISTA_SESSION_SECRET": s["BARISTA_SESSION_SECRET"],
        "BARISTA_BASIC_AUTH": f"{s['gate_user']}:{gate}", "BARISTA_MIGRATE_ON_START": "false",
        # chế độ của bản cài (ADR 0194): production ⇒ tắt đăng nhập dev (bản demo như cũ); BARISTA_ENV chỉ có ở production
        "BARISTA_DEV_AUTH": "false" if production else "true", **({"BARISTA_ENV": "production"} if production else {}),
        # mạng chung dokploy-network có dịch vụ "api" của stack khác (barista-demo) ⇒ gọi bằng TÊN ĐẦY ĐỦ, duy nhất của dịch vụ Swarm
        "BARISTA_CORE_URL": f"http://{app_name}_api:8300",
        "TZ": "Asia/Ho_Chi_Minh",
    }
    # mọi khoá người vận hành thêm (docstring đầu file); khoá ``once`` để trống ⇒ sinh lại như trước
    kept = {k: v for k, v in old.items() if k not in owned and k != "BARISTA_ENV" and (v or k not in once)}
    env = {**once, **kept, **owned}
    if any("\n" in v for v in env.values()):
        raise ValueError("Giá trị cấu hình nhiều dòng")
    text = "".join(f"{k}={v}\n" for k, v in env.items())
    (root / "runtime.env").write_text(text)
    (root / "runtime.env").chmod(0o600)
    llm_file.unlink(missing_ok=True)
    return text


def _compose(d: Deployment, root: Path, eid: str, name: str, app: str, kind: str, file: str, env: str, deploy: bool) -> dict:
    ops = json.loads((root / "operations.json").read_text()) if (root / "operations.json").exists() else {}
    cid = ops.get(f"{app}_compose_id")
    created = cid is None
    if created:
        cid = d.api("compose.create", {"name": name, "appName": app, "environmentId": eid, "composeType": kind,
                                       "sourceType": "raw", "composeFile": file})["composeId"]
        ops[f"{app}_compose_id"] = cid
        write_private(root / "operations.json", ops)
    # luôn ghi lại file + env (không deploy): stack lấy ảnh thật ở lần phát hành kế tiếp (deploy.py chỉ thay trường image)
    d.api("compose.update", {"composeId": cid, "composeFile": file, "env": env, "autoDeploy": False})
    if deploy and created:   # DB chỉ dựng lần đầu — chạy lại bootstrap không đụng Postgres đang chạy
        d.api("compose.deploy", {"composeId": cid, "title": "Bootstrap"})
    return d.api("compose.one?composeId=" + cid)


def record_database(root: Path, container: str) -> None:
    """Ghi container Postgres ĐANG chạy của bản cài vào operations.json — phát hành bảo trì (deploy.py ``backup_database``) sao lưu qua tên này
    trước khi migrate, thiếu là từ chối (edu 11/11/2026 phải thêm tay). Tên container lấy từ docker, không đoán theo mẫu. ``database_name`` chỉ đặt
    khi chưa có: bản cài dựng lại DB từ bản gốc (vd ``barista_v3`` — runbook 11) giữ tên đã đổi."""
    ops = json.loads((root / "operations.json").read_text())
    ops["postgres_container"] = container.lstrip("/")
    ops.setdefault("database_name", "barista")
    write_private(root / "operations.json", ops)


def prepare(inst: dict) -> None:
    root, project = Path(inst["root"]), inst["project"]
    if not (HERE / "initdb" / "01-roles.sh").is_file():
        # compose.yaml gắn {{root}}/ops/initdb vào docker-entrypoint-initdb.d: thiếu ⇒ Postgres lần đầu KHÔNG tạo role app / owner, mà initdb
        # chỉ chạy một lần (volume giữ mãi) ⇒ phải xoá volume dựng lại. Dừng TRƯỚC khi dựng DB (edu 11/11/2026 quên chép — runbook 11 bước chép ops)
        raise FileNotFoundError(f"Thiếu {HERE / 'initdb' / '01-roles.sh'}: chép deploy/initdb/ vào {HERE}/ (runbook 11) rồi chạy lại")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    (root / "backups").mkdir(mode=0o700, exist_ok=True)
    s = _secrets(root, inst)
    d = _dokploy()
    eid = _environment(d, inst)
    db_env = "".join(f"{k}={s[k]}\n" for k in ("POSTGRES_PASSWORD", "OWNER_PASSWORD", "APP_PASSWORD"))
    compose = render((HERE / "compose.yaml").read_text(), inst, json_text=False)
    stack = render((HERE / "stack.json").read_text(), inst, json_text=True)
    db = _compose(d, root, eid, f"{project} Database", inst["db_app"], "docker-compose", compose, db_env, deploy=True)
    app = _compose(d, root, eid, f"{project} App", inst["app_app"], "stack", stack, "", deploy=False)
    runtime = _runtime(root, inst, s, app["appName"])
    d.api("compose.update", {"composeId": app["composeId"], "env": runtime, "autoDeploy": False})
    ops = json.loads((root / "operations.json").read_text())
    ops.update(compose_id=app["composeId"], prefix=app["appName"], db_app=db["appName"], api_url=f"https://{inst['api_host']}",
               web_url=f"https://{inst['web_host']}", gate_user=s["gate_user"], gate_password=s["gate_password"], api_key_file="/root/.dokploy-api-key")
    write_private(root / "operations.json", ops)
    for _ in range(60):   # đợi Postgres lần đầu (initdb tạo role + database)
        ids = command("docker", "ps", "-q", "--filter", "label=com.docker.compose.project=" + db["appName"],
                      "--filter", "label=com.docker.compose.service=postgres").split()
        pg = json.loads(command("docker", "inspect", ids[0]))[0] if ids else None
        if pg and pg["State"].get("Health", {}).get("Status") == "healthy":
            record_database(root, pg["Name"])
            break
        time.sleep(3)
    else:
        raise TimeoutError(f"Postgres {project} chưa khoẻ sau 3 phút")
    cron = Path(inst["cron_file"])
    # backup.sh tự lấy thư mục bản cài từ chỗ nó nằm (<thư mục bản cài>/ops/backup.sh)
    cron.write_text(f"# Sao lưu DB {project} hằng ngày ({inst['backup_cron']}, giờ VPS), giữ 14 bản — deploy/ops/backup.sh\n"
                    f"{inst['backup_cron']} root /bin/sh {HERE / 'backup.sh'} {db['appName']} >> {root / 'backups' / 'backup.log'} 2>&1\n")
    cron.chmod(0o644)
    flag = f" --instance {inst['name']}"
    print("Đã chuẩn bị: DB", db["appName"], "· stack", app["appName"],
          f"· lần đầu: git archive --format=tar.gz <sha> | ssh vps python3 {root}/ops/deploy.py{flag} manual-v2 <sha>", flush=True)


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    name = default_instance()
    if args[:1] == ["--instance"] and len(args) >= 2:
        name, args = args[1], args[2:]
    if name is None:
        raise SystemExit("Thiếu bản cài: nêu --instance <tên> (không có bản cài mặc định). Dùng: bootstrap.py [--instance <tên>] prepare")
    if args == ["prepare"]:
        prepare(load_instance(name))
    else:
        raise SystemExit("Dùng: bootstrap.py [--instance <tên>] prepare")
