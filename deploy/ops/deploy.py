#!/usr/bin/env python3
"""Nhận lệnh phát hành từ CI + phát hành Swarm qua Dokploy; rolling hoặc maintenance theo migration. Chỉ dùng thư viện chuẩn (chép mô hình barista_bds/deploy/demo).

Người vận hành cài file này (chủ root) tại <thư mục bản cài>/ops/deploy.py (/root/<tên>/ops). BẢN CÀI (barista-ops/instances/<tên>.json — thư
mục, project Dokploy, tên miền, hồ sơ nạp mẫu…) chọn bằng `--instance <tên>`; không nêu ⇒ bản cài chứa chính file này (thư mục cha của ops/ — khoá
SSH ép lệnh `deploy.py receive` chạy như trước); không suy ra được ⇒ dừng, lõi không có bản cài mặc định.
deploy/stack.json + deploy/compose.yaml là MẪU, `{{khoá}}` điền từ file bản cài (`render`). Ảnh `barista-api:<thẻ>` / `barista-web:<sha>` DÙNG CHUNG
mọi bản cài (một lần dựng mỗi commit): có sẵn đúng nhãn revision ⇒ không dựng lại; dọn ảnh giữ ảnh mọi bản cài đang chạy / để quay lui. Tên ảnh
của bản đang chạy / để quay lui đọc từ Dokploy + release.json / rollback.json, không viết cứng — bản cài còn chạy ảnh mang tên cũ (trước 16/11/2026)
vẫn quay lui được, ảnh tên cũ được dọn như ảnh khác khi hết dùng. Khoá SSH của CI bị ép chạy `deploy.py receive`, không shell, không
chuyển tiếp — chỉ nhận đúng `deploy <sha 40 ký tự> <số lượt CI>`, kèm stdin là `git archive` (tar.gz) của đúng commit đó. Khác
barista_bds: ảnh lõi do GitHub Actions dựng sẵn (images.yml, GHCR công khai — từ 24/11/2026; trước đó dựng trên VPS), VPS chỉ kéo — không cần
khoá đọc GitHub; mã nhận được giải nén ra thư mục tạm riêng mỗi lần (gói ngoài lõi + stack.json lấy từ đó).

Một lần phát hành:
1. Lấy barista-api:<thẻ> + barista-web:<sha> (đã có ⇒ dùng lại): kéo ảnh CI của commit (``_fetch``; ``BARISTA_BUILD_ON_VPS=1`` ⇒ dựng như
   cũ), nhãn revision phải khớp sha.
   GÓI NGOÀI LÕI (0155): mã nhận được có thể kèm ``deploy/packs/<khoá>/`` + ``manifest.json`` (máy phát hành lấy đúng tag từ barista-packs —
   deploy/packs.py); bộ gói phải ĐÚNG ``"packs"`` của file bản cài trên máy này, không thì dừng. Có gói ⇒ ảnh api mang thẻ
   ``<sha>-p<dấu bộ gói>`` (lớp mỏng api-packs.Dockerfile cài từng gói lên ảnh lõi); VPS vẫn không cần quyền đọc GitHub.
2. Chạy `python -m barista.seed` từ ẢNH MỚI, một lần, ngoài Traefik (migration có advisory lock; DB trống thì nạp mẫu — bản cài
   ``"env": "production"`` KHÔNG nạp mẫu, chỉ migration: ``production``). Migration chỉ được
   THÊM — bản cũ vẫn chạy được trên schema mới trong lúc chuyển / khi quay lui. Migration lỗi ⇒ bản đang chạy không bị đụng.
   Ngay sau đó, cũng từ ảnh mới: KIỂM PHẦN MẶC ĐỊNH (``python -m barista.defaults_report`` — chỉ đọc) in vai / quyền / chuỗi / luật duyệt của gói
   đang bật mà công ty chưa có. Không cài gì. File bản cài ``"defaults_check": "block"`` ⇒ thiếu thứ quan trọng thì DỪNG (chỉ lượt rolling — bản cũ
   chạy tiếp trên schema mới); vắng / ``"warn"`` ⇒ chỉ in cảnh báo.
3. Lưu cấu hình Dokploy cũ, chọn ảnh mới cho cả hai dịch vụ, deploy stack (start-first, từng bản một).
4. Chờ cả hai dịch vụ hội tụ về bản mới khoẻ, kiểm URL công khai báo đúng commit. Lỗi ⇒ trả cấu hình cũ, deploy lại, chờ khoẻ.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAGE_PREFIX = "barista"   # ảnh <IMAGE_PREFIX>-api / -web — dùng chung mọi bản cài (0155)
FILES_MOUNT = "/var/lib/barista/files"   # thư mục kho tệp trong container api — khớp volume <thư mục bản cài>/files của deploy/stack.json
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
TAG_RE = re.compile(r"[0-9a-f]{40}(-p[0-9a-f]{12})?\Z")   # thẻ ảnh: <sha>, hoặc <sha>-p<dấu bộ gói> cho api có gói ngoài lõi (0155)
PACKS = Path("deploy") / "packs"   # trong mã nhận được: <khoá gói>/ (bản phân phối ở đúng tag) + manifest.json — deploy/packs.py ở máy phát hành đặt vào
NAME_RE = re.compile(r"[a-z][a-z0-9]{0,15}\Z")
SERVICES = ("api", "web")
DEFAULTS_CHECK = ("warn", "block")   # khoá "defaults_check" của file bản cài; vắng ⇒ warn
DEFAULTS_MISSING = 3                 # mã thoát của ``barista.defaults_report --strict`` khi thiếu vai / quyền / chuỗi / luật duyệt
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def image(service: str, tag: str) -> str:
    return f"{IMAGE_PREFIX}-{service}:{tag}"


def instances_dir() -> Path:
    """VPS: <thư mục bản cài>/ops/instances (chép cùng ops). Máy phát hành / dev: ``instances/`` của repo ``barista-ops`` — ``BARISTA_OPS_DIR`` nếu
    đặt, không thì thư mục ``barista-ops`` cạnh repo lõi (lõi chia sẻ đối tác KHÔNG mang file bản cài — 0155)."""
    sibling = [p / "barista-ops" / "instances" for p in HERE.parents]
    env = os.environ.get("BARISTA_OPS_DIR")
    return next((d for d in (HERE / "instances", *([Path(env) / "instances"] if env else []), *sibling) if d.is_dir()), HERE / "instances")


ENVS = ("production", "demo")   # khoá "env" của file bản cài (deploy/packs.py ở máy phát hành đọc cùng hai giá trị); vắng ⇒ production (ghim gói)

# CHẾ ĐỘ PRODUCTION (ADR 0194, review F3): chỉ khi file bản cài khai RÕ ``"env": "production"`` — vắng ``env`` vẫn ra đúng mẫu như trước (đăng nhập
# demo), chỉ phần ghim gói coi vắng là production. ``render`` ĐÈ các biến dưới lên mẫu (api_env không đè lại được); lõi (barista/production.py) và
# web (deploy/serve-web.mjs) từ chối khởi động nếu BARISTA_ENV=production mà cờ đăng nhập dev bật. Đăng nhập thật bắt buộc: client ID Google loại
# "Web" khai trong ``api_env`` (không phải bí mật; lõi công bố cho web qua /v1/auth/config) — thiếu ⇒ load_instance dừng (bootstrap, deploy.py trên
# VPS, deploy/packs.py ở bước đầu của release.sh). Địa chỉ công khai lấy từ api_host / web_host. Cổng mật khẩu giữ mặc định; ``"gate": false``
# bỏ cổng — an toàn của production không dựa vào cổng; bản demo khai nó là demo CÔNG KHAI (17/11/2026, bổ sung ADR 0194): ai có link cũng vào
# trang chọn vai, kể cả quản trị ⇒ chỉ cho bản cài toàn dữ liệu giả.
PRODUCTION_API = {"BARISTA_ENV": "production", "BARISTA_DEV_AUTH": "false"}
PRODUCTION_WEB = {"BARISTA_ENV": "production", "BARISTA_WEB_DEV_LOGIN": "0"}
PRODUCTION_LOGIN = ("BARISTA_GOOGLE_WEB_CLIENT_ID",)
PROFILE_KEYS = {"BARISTA_ENV", "BARISTA_DEV_AUTH", "BARISTA_WEB_DEV_LOGIN"}   # chỉ "env" quyết — api_env đặt ⇒ dừng


def production(inst: dict) -> bool:
    return inst.get("env") == "production"


def load_instance(name: str, directory: Path | None = None) -> dict:
    if not NAME_RE.fullmatch(name):
        raise ValueError(f"Tên bản cài không hợp lệ: {name!r}")
    inst = json.loads(((directory or instances_dir()) / f"{name}.json").read_text())
    if inst["name"] != name or inst["root"] != f"/root/{name}":
        raise ValueError(f"instances/{name}.json: name / root phải là {name} / /root/{name}")
    for key in ("db_host", "api_host", "web_host", "cron_file"):
        if not re.fullmatch(r"[a-zA-Z0-9./_-]+", inst[key]):
            raise ValueError(f"instances/{name}.json: {key} có ký tự lạ")
    if inst.get("env", "production") not in ENVS:
        raise ValueError(f"instances/{name}.json: \"env\" chỉ nhận {', '.join(ENVS)} (vắng = production)")
    for key in ("dokploy_project", "dokploy_project_description"):   # tuỳ chọn: project Dokploy dùng chung (bootstrap._environment)
        if key in inst and (not isinstance(inst[key], str) or not inst[key].strip() or "\n" in inst[key]):
            raise ValueError(f"instances/{name}.json: {key} phải là chuỗi một dòng, không trống")
    api_env = inst.get("api_env") or {}
    if clash := sorted(PROFILE_KEYS & set(api_env)):
        raise ValueError(f"instances/{name}.json: api_env không được đặt {', '.join(clash)} — chế độ đăng nhập do \"env\" quyết (ADR 0194)")
    if inst.get("defaults_check", "warn") not in DEFAULTS_CHECK:
        raise ValueError(f"instances/{name}.json: \"defaults_check\" chỉ nhận {', '.join(DEFAULTS_CHECK)} (vắng = warn)")
    if "gate" in inst and inst["gate"] is not False:
        raise ValueError(f"instances/{name}.json: \"gate\" chỉ nhận false (bỏ cổng mật khẩu) — vắng = có cổng")
    if "landing" in inst and inst["landing"] is not True:
        raise ValueError(f"instances/{name}.json: \"landing\" chỉ nhận true (trang giới thiệu ở / cho người chưa đăng nhập) — vắng = vào thẳng /login")
    if production(inst):
        if missing := [k for k in PRODUCTION_LOGIN if not isinstance(api_env.get(k), str) or not api_env[k].strip()]:
            raise ValueError(f"instances/{name}.json là \"env\": \"production\" nhưng api_env thiếu {', '.join(missing)} — production bắt buộc "
                             "đăng nhập Google thật (runbook 09; runbook 11 mục \"Chế độ production\")")
        if bad := [k for k in ("BARISTA_PUBLIC_API_URL", "BARISTA_WEB_URL") if k in api_env and not str(api_env[k]).startswith("https://")]:
            raise ValueError(f"instances/{name}.json: {', '.join(bad)} của bản production phải là https:// (vắng ⇒ lấy từ api_host / web_host)")
    return inst


def default_instance() -> str | None:
    """Bản cài chứa chính file này: <thư mục bản cài>/ops/deploy.py ⇒ tên thư mục bản cài (khoá SSH của CI ép ``deploy.py receive`` không nêu
    ``--instance``). Ở máy dev / phát hành (repo lõi) không suy ra được ⇒ None — bên gọi phải nêu bản cài."""
    name = HERE.parent.name
    return name if HERE.name == "ops" and NAME_RE.fullmatch(name) and (instances_dir() / f"{name}.json").is_file() else None


def known_instances(directory: Path | None = None) -> list[dict]:
    d = directory or instances_dir()
    return [load_instance(p.stem, d) for p in sorted(d.glob("*.json"))]


# Tài nguyên Postgres của bản cài (compose.yaml) — khoá tuỳ chọn trong instances/<tên>.json; vắng ⇒ mức demo nhỏ (18/11/2026: khách đầu tiên dữ liệu thật
# cần 1,5 CPU / 1 GB / shared_buffers 256MB, máy dùng chung nhiều app). Đổi số ở bản cài đang chạy = compose.update + deploy compose DB (khởi động lại
# Postgres vài giây), không chạy lại bootstrap.
DB_DEFAULTS = {"db_cpus": "0.75", "db_mem_limit": "512m", "db_shared_buffers": "128MB"}


def render(template: str, inst: dict, *, json_text: bool) -> str:
    """Điền `{{khoá}}` của mẫu stack.json / compose.yaml từ file bản cài; còn khoá lạ ⇒ dừng. ``json_text``: giá trị nằm trong chuỗi JSON (thoát
    nháy / gạch chéo). ``api_env`` (biến thêm cho dịch vụ api) trống và không production ⇒ trả đúng chữ mẫu đã điền — ra y hệt stack.json
    trước khi có bản cài. Có api_env hoặc production ⇒ địa chỉ công khai theo host (api_env đè được); ``"env": "production"`` ⇒ thêm ``PRODUCTION_API`` /
    ``PRODUCTION_WEB`` đè lên mẫu (sau api_env)."""
    inst = {**DB_DEFAULTS, **inst}

    def value(m: re.Match) -> str:
        if m.group(1) not in inst or not isinstance(inst[m.group(1)], str):
            raise KeyError(f"Mẫu cần {{{{{m.group(1)}}}}} — thiếu trong instances/{inst.get('name')}.json")
        v = inst[m.group(1)]
        return json.dumps(v, ensure_ascii=False)[1:-1] if json_text else v
    text = PLACEHOLDER.sub(value, template)
    if json_text and (inst.get("api_env") or production(inst) or inst.get("gate") is False or inst.get("landing") is True):
        stack = json.loads(text)
        api, web = stack["services"]["api"]["environment"], stack["services"]["web"]["environment"]
        # địa chỉ công khai theo host của bản cài — MỌI bản cài đi nhánh này (không chỉ production): thiếu ⇒ lõi tưởng mình ở localhost:8300 (OAuth /
        # MCP issuer, link gửi khách sai). api_env vẫn đè được (đặt sau)
        api.update(BARISTA_PUBLIC_API_URL=f"https://{inst['api_host']}", BARISTA_WEB_URL=f"https://{inst['web_host']}")
        api.update(inst.get("api_env") or {})
        if production(inst):
            api.update(PRODUCTION_API)
            web.update(PRODUCTION_WEB)
        if inst.get("landing") is True:   # trang giới thiệu sản phẩm ở "/" cho người chưa đăng nhập (web/src/proxy.ts); vắng ⇒ "/" về thẳng /login
            web["BARISTA_WEB_HOME"] = "landing"
        if inst.get("gate") is False and not production(inst):   # demo CÔNG KHAI ⇒ app gọi thẳng API cũng chọn được vai (không cần khoá demo của BFF)
            api["BARISTA_DEMO_OPEN"] = "true"
        if inst.get("gate") is False:   # bỏ cổng mật khẩu: nhãn basicauth + mắt xích đầu của router web
            labels, gate = stack["services"]["web"]["deploy"]["labels"], f"{inst['name']}-gate"
            for k in [k for k in labels if k.startswith(f"traefik.http.middlewares.{gate}.")]:
                del labels[k]
            chain = f"traefik.http.routers.{inst['name']}-web.middlewares"
            labels[chain] = ",".join(m for m in labels[chain].split(",") if m != gate)
        text = json.dumps(stack, indent=2, ensure_ascii=False) + "\n"
    return text


def shipped_packs(work: Path, instance: dict) -> dict:
    """Gói ngoài lõi đi kèm mã nhận được (``deploy/packs/manifest.json``) phải ĐÚNG bộ ``"packs"`` (khoá ⇒ phiên bản) của file bản cài TRÊN MÁY
    NÀY — không khai ⇒ không gói. Lệch (thiếu gói, thừa gói, khác phiên bản, thư mục không khớp manifest) ⇒ dừng trước khi dựng: bản cài không
    bao giờ chạy với bộ gói khác cấu hình của nó (vd mã gửi qua CI không kèm gói cho bản cài cần gói)."""
    f = work / PACKS / "manifest.json"
    manifest = json.loads(f.read_text()) if f.is_file() else {}
    # mục "packs": "v1.2.3" hoặc {"version": "v1.2.3", "commit": "<sha>"} (commit ghim tuỳ chọn — khớp deploy/packs.py ở máy phát hành)
    specs = {k: (v, "") if isinstance(v, str) else ((v or {}).get("version"), (v or {}).get("commit", "")) for k, v in (instance.get("packs") or {}).items()}
    want = {k: version for k, (version, _) in specs.items()}
    got = {k: (v or {}).get("version") for k, v in manifest.items()}
    if got != want:
        raise ValueError(f"Gói đi kèm {got or 'không có'} khác cấu hình bản cài {instance['name']} {want or 'không có'} — phát hành bằng "
                         "deploy/release.sh từ máy có barista-ops + barista-packs (runbook 11)")
    if (loose := sorted(k for k, (_, pin) in specs.items() if not pin)) and instance.get("env", "production") != "demo":
        raise ValueError(f"Gói {', '.join(loose)}: bản cài {instance['name']} không phải \"env\": \"demo\" mà chưa ghim commit — bản cài thật phải ghim "
                         "(barista-ops/instances/<tên>.json: {\"version\", \"commit\"})")
    moved = sorted(k for k, (_, pin) in specs.items() if pin and (manifest[k] or {}).get("commit") != pin)
    if moved:
        raise ValueError(f"Gói {', '.join(moved)}: commit đi kèm khác commit bản cài {instance['name']} ghim — tag bị dời / gửi nhầm bản; không cài")
    dirs = {p.name for p in (work / PACKS).iterdir() if p.is_dir()} if (work / PACKS).is_dir() else set()
    if dirs != set(manifest):
        raise ValueError(f"Thư mục gói {sorted(dirs)} không khớp manifest {sorted(manifest)}")
    return manifest


def api_tag(sha: str, manifest: dict) -> str:
    """Thẻ ảnh api: không gói ⇒ đúng sha (như trước khi có gói); có gói ⇒ ``<sha>-p<12 hex>`` theo (khoá, phiên bản, commit) từng gói — cùng commit lõi +
    cùng bộ gói ⇒ dùng lại ảnh, khác bộ gói ⇒ ảnh khác. Ảnh web không đổi theo gói."""
    if not manifest:
        return sha
    key = json.dumps({k: [v["version"], v["commit"]] for k, v in sorted(manifest.items())}, sort_keys=True)
    return f"{sha}-p{hashlib.sha256(key.encode()).hexdigest()[:12]}"


def command(*args: str, cwd: str | None = None, env: dict[str, str] | None = None) -> str:
    return subprocess.check_output(args, text=True, cwd=cwd, env=env).strip()


def write_private(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


class Deployment:
    def __init__(self, instance: dict):
        self.instance = instance
        self.root = Path(instance["root"])
        self.requested_at = datetime.now(UTC)
        self.config = json.loads((self.root / "operations.json").read_text())
        self.key = Path(self.config["api_key_file"]).read_text().strip()
        self.dokploy_container = command("docker", "ps", "-q", "--filter", "label=com.docker.swarm.service.name=dokploy")

    def api(self, endpoint: str, body: dict | None = None) -> dict:
        # Dokploy không mở cổng ra host: gọi API loopback từ trong container của nó; khoá quản trị không rời VPS, không nằm trong tham số.
        script = """
let input = ''; for await (const chunk of process.stdin) input += chunk;
const p = JSON.parse(input);
const r = await fetch('http://localhost:3000/api/' + p.path, {
  method: p.body === null ? 'GET' : 'POST',
  headers: {'x-api-key': p.key, 'Content-Type': 'application/json'},
  body: p.body === null ? undefined : JSON.stringify(p.body)
});
const text = await r.text();
process.stdout.write(JSON.stringify({status: r.status, body: text ? JSON.parse(text) : null}));
"""
        result = subprocess.run(
            ["docker", "exec", "-i", self.dokploy_container, "node", "--input-type=module", "-e", script],
            input=json.dumps({"path": endpoint, "body": body, "key": self.key}), text=True, capture_output=True, check=True, timeout=60)
        response = json.loads(result.stdout)
        if response["status"] >= 400:
            raise RuntimeError(f"Dokploy {endpoint.split('?')[0]} trả {response['status']}")
        return response["body"]

    def current(self) -> dict:
        return self.api("compose.one?composeId=" + self.config["compose_id"])

    def apply(self, compose_file: str, title: str) -> None:
        self.requested_at = datetime.now(UTC)
        self.api("compose.update", {"composeId": self.config["compose_id"], "composeFile": compose_file})
        self.api("compose.deploy", {"composeId": self.config["compose_id"], "title": title})

    def _healthy(self, service: str, image: str) -> bool:
        spec_all = json.loads(command("docker", "service", "inspect", service))[0]
        spec = spec_all["Spec"]
        state = spec_all.get("UpdateStatus", {}).get("State", "")
        selected = spec["TaskTemplate"]["ContainerSpec"]["Image"].split("@")[0]
        started = spec_all.get("UpdateStatus", {}).get("StartedAt", "")
        recent = bool(started) and datetime.fromisoformat(started.replace("Z", "+00:00")) >= self.requested_at
        if recent and selected != image and state in ("rollback_completed", "rollback_paused", "paused"):
            raise RuntimeError(f"Swarm từ chối bản mới của {service} và đã quay lui / tạm dừng")
        ids = command("docker", "ps", "-q", "--filter", "label=com.docker.swarm.service.name=" + service).split()
        containers = json.loads(command("docker", "inspect", *ids)) if ids else []
        want = spec["Mode"]["Replicated"]["Replicas"]
        # want > 0: `wait` chỉ dùng khi chờ dịch vụ CHẠY; ngay sau bật lại, spec cũ (replicas 0, không container) không được tính là xong.
        return (want > 0 and selected == image and state in ("", "completed", "rollback_completed") and len(containers) == want
                and all(c["Config"]["Image"].split("@")[0] == image and c["State"].get("Health", {}).get("Status") == "healthy"
                        for c in containers))

    def wait(self, images: dict[str, str]) -> None:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            try:
                if all(self._healthy(self.config["prefix"] + "_" + s, images[s]) for s in SERVICES):
                    return
            except subprocess.CalledProcessError:
                pass  # lần đầu dịch vụ có thể chưa được tạo
            time.sleep(2)
        raise TimeoutError("Swarm không hội tụ về bản khoẻ trong 300 giây")

    def _stopped(self, service: str) -> bool:
        """Đã dừng HẲN: Swarm đã nhận spec replicas 0 VÀ không còn container nào của dịch vụ. Không dựa vào `_healthy`: ngay sau
        `apply`, Swarm có thể chưa nhận spec mới (vẫn replicas 1, container cũ khoẻ) nên điều kiện "đúng ảnh cũ + đủ số" đúng ngay."""
        spec = json.loads(command("docker", "service", "inspect", service))[0]["Spec"]
        if spec["Mode"]["Replicated"]["Replicas"] != 0:
            return False
        return not command("docker", "ps", "-q", "--filter", "label=com.docker.swarm.service.name=" + service).split()

    def wait_stopped(self, timeout: float = 300) -> None:
        """Chờ container cũ thoát hẳn trước khi sao lưu / migrate (07/11/2026: job của bản cũ chạy giữa lúc migrate ⇒ lỗi cột mới).
        Hạn dài hơn stop_grace_period của api."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if all(self._stopped(self.config["prefix"] + "_" + s) for s in SERVICES):
                    return
            except subprocess.CalledProcessError:
                pass
            time.sleep(2)
        raise TimeoutError(f"Dịch vụ cũ chưa dừng hẳn sau {int(timeout)} giây — chưa sao lưu, chưa migrate")

    def smoke(self, sha: str) -> None:
        """Gọi đúng tên miền qua Traefik CỦA MÁY NÀY (curl --resolve …:443:127.0.0.1) — kiểm router + bản đang phục vụ, không phụ thuộc DNS
        công khai / Cloudflare. -k vì nối vào chính localhost (chứng chỉ công khai kiểm riêng khi đổi DNS)."""
        deadline = time.monotonic() + 30
        gate = f"{self.config['gate_user']}:{self.config['gate_password']}"
        checks = [(self.config["api_url"] + "/health", []), (self.config["web_url"] + "/api/health", ["-u", gate])]
        while True:
            try:
                for url, extra in checks:
                    host = url.split("/")[2]
                    for _ in range(3):
                        out = command("curl", "-sk", "--max-time", "5", "--resolve", f"{host}:443:127.0.0.1", *extra, url)
                        health = json.loads(out)
                        if health.get("status") != "ok" or (sha and health.get("release") != sha):
                            raise RuntimeError(f"{url} chưa phục vụ đúng bản {sha[:8]}: {health.get('status')}")
                return
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)

    def migrate(self, api_image: str) -> None:
        # Từ ẢNH MỚI, một lần, ngoài Traefik: migration (advisory lock) + nạp mẫu nếu DB trống. Lỗi ⇒ dừng, bản đang chạy không đụng.
        # Production (ADR 0194): BARISTA_ENV=production đặt THẲNG từ file bản cài (không tin runtime.env) và không --profile ⇒ seed chỉ migration,
        # không nạp mẫu kể cả DB trống (barista/seed.py từ chối --reset / mẫu khi production).
        # Kho tệp (0122): nơi chứa theo runtime.env (BARISTA_FILES_STORE=local|r2 + BARISTA_R2_*). Kho thư mục: CÙNG thư mục bền với dịch vụ api
        # (deploy/stack.json) — bước dữ liệu của migration 042 đẩy thân tệp cũ vào đây; thiếu cấu hình thì bước đó từ chối, 042 không chạy.
        files = self.root / "files"
        files.mkdir(mode=0o700, exist_ok=True)
        real = production(self.instance)
        command("docker", "run", "--rm", "--network", "dokploy-network", "--label", "traefik.enable=false",
                "--env-file", str(self.root / "runtime.env"), "-e", f"BARISTA_FILES_DIR={FILES_MOUNT}",
                *(["-e", "BARISTA_ENV=production"] if real else []),
                "-v", f"{files}:{FILES_MOUNT}", "--entrypoint", "python", api_image, "-m", "barista.seed",
                *([] if real else ["--profile", self.instance["seed_profile"]]))   # hồ sơ chỉ dùng khi DB trống (lần đầu); DB đã có dữ liệu thì seed bỏ qua

    def defaults_check(self, api_image: str, *, can_block: bool) -> None:
        """Sau migrate, từ ẢNH MỚI (mặc định của bản mới so với DB đích): in phần mặc định của gói đang bật mà công ty chưa có — CHỈ ĐỌC (truy vấn
        ``admin.defaults_drift`` trong giao dịch read-only), không cài gì. ``"defaults_check": "block"`` + thiếu thứ quan trọng ⇒ dừng TRƯỚC khi đổi bản
        (``can_block`` — lượt rolling; lượt maintenance đã dừng dịch vụ nên chỉ cảnh báo). Bước kiểm hỏng (ảnh cũ chưa có lệnh, DB chập chờn) ⇒ cảnh
        báo, không chặn — kiểm là để biết, không phải điều kiện sống còn của phát hành."""
        try:
            r = subprocess.run(["docker", "run", "--rm", "--network", "dokploy-network", "--label", "traefik.enable=false",
                                "--env-file", str(self.root / "runtime.env"), "--entrypoint", "python", api_image,
                                "-m", "barista.defaults_report", "--strict"], capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f"⚠ Không kiểm được phần mặc định ({type(error).__name__}) — bỏ qua bước kiểm", flush=True)
            return
        print("Kiểm phần mặc định của gói (chỉ đọc):", flush=True)
        print(r.stdout.rstrip() or "(không có báo cáo)", flush=True)
        if r.returncode not in (0, DEFAULTS_MISSING):
            print(f"⚠ Không kiểm được phần mặc định (mã {r.returncode}) — bỏ qua bước kiểm: {r.stderr.strip()[-400:]}", flush=True)
            return
        if r.returncode == DEFAULTS_MISSING:
            if self.instance.get("defaults_check", "warn") == "block" and can_block:
                raise RuntimeError("Thiếu vai / quyền / chuỗi / luật duyệt mặc định (báo cáo trên) và bản cài đặt \"defaults_check\": \"block\" — "
                                   "DỪNG trước khi đổi bản (bản cũ vẫn chạy, migration đã chạy chỉ thêm). Quản trị công ty cài phần còn thiếu "
                                   "(Quản trị › Phần mặc định chưa cài) rồi phát hành lại, hoặc đổi sang \"warn\".")
            print("⚠ Thiếu phần mặc định quan trọng — phát hành TIẾP (\"defaults_check\": \"warn\"); báo quản trị công ty cài thêm (runbook 11).",
                  flush=True)

    def migration_plan(self, api_image: str) -> dict:
        script = ("import asyncio,json; from barista.config import settings; from barista.kernel.migrate import pending_plan; "
                  "print(json.dumps(asyncio.run(pending_plan(settings.owner_database_url))))")
        return json.loads(command("docker", "run", "--rm", "--network", "dokploy-network", "--label", "traefik.enable=false",
                                  "--env-file", str(self.root / "runtime.env"), "--entrypoint", "python", api_image, "-c", script))

    def backup_database(self, sha: str) -> Path:
        """Dịch vụ đã dừng; pg_dump không ghi DB. Bản sao riêng của lần chuyển đổi, không bị cron xoá theo số bản hằng ngày."""
        container = self.config["postgres_container"]
        database = self.config.get("database_name", "barista")
        backups = self.root / "backups" / "cutover"
        backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = backups / f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{sha[:12]}.dump"
        with path.open("xb") as out:
            path.chmod(0o600)
            subprocess.run(["docker", "exec", container, "pg_dump", "-U", "postgres", "-Fc", database], stdout=out, check=True)
        if not path.stat().st_size:
            raise RuntimeError("Bản sao lưu chuyển đổi rỗng")
        return path

    def maintenance_release(self, sha: str, candidate: dict, old_file: str, old_images: dict[str, str], images: dict[str, str]) -> None:
        if not self.config.get("postgres_container"):
            raise ValueError("Maintenance cần postgres_container trong operations.json để sao lưu đúng DB trước chuyển đổi")
        stopped = json.loads(old_file)
        for service in SERVICES:
            stopped["services"][service]["deploy"]["replicas"] = 0
        parked = json.dumps(stopped)
        self.apply(parked, "Maintenance: dừng nhận yêu cầu và worker cũ")
        try:
            self.wait_stopped()  # container cũ thoát hẳn (spec replicas 0 VÀ không còn container), không chỉ đổi desired replicas
        except Exception:
            # Chưa sao lưu, chưa migrate: an toàn bật lại bản cũ.
            self.apply(old_file, "Dừng không kịp: bật lại bản cũ")
            self.wait(old_images)
            raise
        try:
            backup = self.backup_database(sha)
        except Exception:
            # Chưa chạy migration: an toàn bật lại bản cũ.
            self.apply(old_file, "Backup lỗi: bật lại bản cũ")
            self.wait(old_images)
            raise
        write_private(self.root / "rollback.json", {"composeFile": old_file, "images": old_images, "database_backup": str(backup),
                                                   "mode": "maintenance", "restore_database_before_old_code": True})
        try:
            self.migrate(images["api"])
            self.defaults_check(images["api"], can_block=False)
            self.apply(json.dumps(candidate), f"Maintenance deploy {sha[:12]}")
            self.wait(images)
            self.smoke(sha)
        except Exception as error:
            # Một số migration có thể đã commit. Không tự đưa image cũ lên schema mới, cũng không tự phục hồi/xoá dữ liệu.
            self.apply(parked, "Chuyển đổi lỗi: giữ dừng để phục hồi DB cùng mã nguồn")
            self.wait_stopped()
            raise RuntimeError(f"Chuyển đổi lỗi; dịch vụ giữ dừng. Bản sao lưu: {backup}. Xem runbook 11 để phục hồi DB + bản cũ.") from error

    def release(self, sha: str, generation: int, stack: str, *, maintenance: bool = False, images: dict[str, str] | None = None) -> None:
        """``stack``: deploy/stack.json của CHÍNH commit này — định nghĩa dịch vụ đi cùng code (đổi giới hạn RAM, thời gian rút… lên VPS
        theo commit); quay lui dùng bản đã lưu trong Dokploy. ``images``: ảnh ``build`` vừa dựng (api có gói mang thẻ riêng — ``api_tag``)."""
        if not SHA_RE.fullmatch(sha):
            raise ValueError("Cần mã commit đủ 40 ký tự")
        images = images or {s: image(s, sha) for s in SERVICES}
        state_file = self.root / "release.json"
        last = json.loads(state_file.read_text()) if state_file.exists() else {}
        if generation < last.get("generation", 0):
            raise ValueError("Từ chối lượt CI cũ hơn bản đang chạy")
        previous = self.current()
        old_file = previous["composeFile"]
        old = json.loads(old_file)
        old_images = {s: old["services"][s]["image"] for s in SERVICES}
        candidate = json.loads(stack)
        for s in SERVICES:
            candidate["services"][s]["image"] = images[s]
        plan = self.migration_plan(images["api"])
        # Lần phát hành ĐẦU của bản cài (bootstrap vừa tạo stack ảnh ":bootstrap", chưa có release.json): không có bản cũ nào chạy trên schema
        # ⇒ migration bảo trì của DB trống không cần dừng gì — rolling được. Đã từng phát hành thì luật như cũ.
        first = not state_file.exists() and all(i.endswith(":bootstrap") for i in old_images.values())
        if plan["maintenance"] and not maintenance and not first:
            raise ValueError("Bản này cần maintenance trước migration: " + ", ".join(plan["maintenance"]) +
                             ". Dùng RELEASE_MODE=maintenance; bản đang chạy chưa bị thay đổi.")
        if maintenance:
            self.maintenance_release(sha, candidate, old_file, old_images, images)
            write_private(state_file, {"sha": sha, "generation": generation, "at": datetime.now(UTC).isoformat(), "mode": "maintenance",
                                       "images": images})
            self.prune(keep={sha, *(i.split(":")[-1] for i in (*images.values(), *old_images.values()))}, repos=repos_of(*images.values(), *old_images.values()))
            print(f"Khoẻ sau maintenance: {sha}", flush=True)
            return
        self.migrate(images["api"])
        self.defaults_check(images["api"], can_block=True)   # "block" + thiếu ⇒ dừng ở đây: chưa đổi bản, bản cũ chạy tiếp trên schema mới
        write_private(self.root / "rollback.json", {"composeFile": old_file, "images": old_images})
        try:
            self.apply(json.dumps(candidate), f"Deploy {sha[:12]}")
            self.wait(images)
            self.smoke(sha)
        except Exception:
            print("Bản mới lỗi — trả cấu hình Dokploy cũ.", flush=True)
            self.apply(old_file, "Rollback")
            self.wait(old_images)
            old_tag = old_images["api"].split(":")[-1]
            self.smoke(old_tag[:40] if TAG_RE.fullmatch(old_tag) else "")
            raise
        write_private(state_file, {"sha": sha, "generation": generation, "at": datetime.now(UTC).isoformat(), "images": images})
        self.prune(keep={sha, *(i.split(":")[-1] for i in (*images.values(), *old_images.values()))}, repos=repos_of(*images.values(), *old_images.values()))
        print(f"Khoẻ: {sha}", flush=True)

    def prune(self, keep: set[str], repos: set[str] = frozenset()) -> None:
        """Giữ ảnh bản đang chạy + bản trước (quay lui nhanh) của MỌI bản cài (ảnh dùng chung); xoá ảnh cũ hơn để không đầy đĩa. Chỉ xoá ảnh của
        pipeline này: tên ``barista-api`` / ``barista-web`` + tên ảnh mà sổ phát hành / quay lui của bản cài nào đó, hay ``repos`` (ảnh bản mới + bản
        cũ của lượt này) có nhắc — ảnh tên cũ (trước khi đổi tên ảnh) được dọn khi hết dùng; ảnh của ứng dụng khác trên máy không bao giờ bị đụng."""
        try:
            keep = keep | shas_in_use()
            repos = {f"{IMAGE_PREFIX}-{s}" for s in SERVICES} | set(repos) | repos_of(*images_in_state())
        except Exception as error:   # không biết bản cài khác đang cần ảnh nào ⇒ không xoá gì (bản mới đã chạy khoẻ)
            print(f"Bỏ dọn ảnh: {error}", flush=True)
            return
        for line in command("docker", "images", "--format", "{{.Repository}}:{{.Tag}}").splitlines():
            repo, _, tag = line.partition(":")
            if repo in repos and TAG_RE.fullmatch(tag) and tag not in keep:
                subprocess.run(["docker", "image", "rm", line], capture_output=True)


def repos_of(*images: str) -> set[str]:
    """Tên ảnh (phần trước thẻ) của các ảnh ``<tên>:<thẻ>`` — bỏ digest ``@sha256:…``."""
    return {i.split("@")[0].rpartition(":")[0] for i in images if ":" in i.split("@")[0]}


def _states(directory: Path | None = None) -> list[dict]:
    """release.json + rollback.json của MỌI bản cài trên máy (đọc được)."""
    out = []
    for inst in known_instances(directory):
        root = Path(inst["root"])
        for name in ("release.json", "rollback.json"):
            try:
                out.append(json.loads((root / name).read_text()))
            except (OSError, ValueError):
                continue
    return out


def images_in_state(directory: Path | None = None) -> list[str]:
    """Ảnh mà sổ phát hành / quay lui của bất kỳ bản cài nào nhắc (``"images"``)."""
    return [str(v) for state in _states(directory) for v in (state.get("images") or {}).values()]


def shas_in_use(directory: Path | None = None) -> set[str]:
    """Thẻ ảnh (commit, hoặc commit + bộ gói) đang được dùng ở BẤT KỲ bản cài nào: bản đang chạy (release.json), bản để quay lui (rollback.json),
    ảnh mọi dịch vụ Swarm đang khai. Ảnh dùng chung ⇒ dọn ảnh của bản cài này không được xoá ảnh bản cài kia cần."""
    found: set[str] = set()
    for state in _states(directory):
        found.update(str(v).split(":")[-1] for v in [state.get("sha", ""), *(state.get("images") or {}).values()])
    found.update(i.split("@")[0].split(":")[-1] for i in command("docker", "service", "ls", "--format", "{{.Image}}").split())
    return {s for s in found if TAG_RE.fullmatch(s)}


def _revision(image: str) -> str | None:
    r = subprocess.run(["docker", "image", "inspect", image, "--format", '{{index .Config.Labels "org.opencontainers.image.revision"}}'],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


# Dựng ảnh trong một builder có TRẦN tài nguyên (sự cố 23/11/2026): dựng bằng daemon mặc định ăn hết RAM / CPU của VPS dùng chung (8 GB, đã swap) ⇒
# phiên Swarm quá hạn, MỌI dịch vụ trên máy bị khởi động lại. Builder ``docker-container`` của buildx chạy BuildKit trong một container có giới hạn
# bộ nhớ (không dùng swap) và CPU ⇒ dựng chậm hơn nhưng không giành tài nguyên của Docker / Swarm và các app đang chạy. Máy thiếu buildx ⇒ dựng như cũ.
BUILDER = "barista-limited"
BUILDER_LIMITS = ("memory=1536m", "memory-swap=1536m", "cpu-period=100000", "cpu-quota=150000")   # 1,5 GB, không swap · 1,5 CPU


def _builder() -> list[str]:
    """Tham số ``docker buildx build`` dùng builder có trần (tạo một lần, lần sau dùng lại cùng bộ nhớ đệm); không có buildx ⇒ [] (dựng như cũ)."""
    def ok(*args: str) -> bool:
        try:
            return subprocess.run(["docker", *args], capture_output=True).returncode == 0
        except FileNotFoundError:   # máy không có docker (test, máy dev)
            return False
    if not ok("buildx", "version"):
        return []
    if not ok("buildx", "inspect", BUILDER):
        command("docker", "buildx", "create", "--name", BUILDER, "--driver", "docker-container",
                *(x for opt in BUILDER_LIMITS for x in ("--driver-opt", opt)))
    return ["buildx", "build", "--builder", BUILDER, "--load"]


# ẢNH DỰNG SẴN Ở CI (sự cố 23/11/2026 — dựng trên VPS dùng chung làm Swarm khởi động lại mọi dịch vụ): mỗi commit trên main, GitHub Actions
# (.github/workflows/images.yml, repo công khai, không secret nào ngoài token sẵn có) đẩy ``<REGISTRY>/barista-api:<sha>`` (CHỈ lõi) +
# ``barista-web:<sha>`` lên GHCR công khai. VPS chỉ KÉO (mạng + đĩa, không ăn RAM như dựng) rồi đặt tên tại máy như trước (``barista-api:<sha>``…) ⇒
# Dokploy, quay lui, dọn ảnh không đổi. Api có gói ngoài lõi ⇒ lớp MỎNG trên ảnh lõi (deploy/api-packs.Dockerfile: chép gói + cài --no-deps, vài
# giây) — gói riêng tư không bao giờ lên CI công khai. Ảnh chưa có (CI còn chạy) ⇒ chờ tới ``PULL_WAIT``; quá hạn ⇒ DỪNG, không tự dựng trên VPS.
# ``BARISTA_BUILD_ON_VPS=1`` ⇒ dựng cả hai ảnh trên VPS như cũ (builder có trần) — chỉ khi CI hỏng mà buộc phải phát hành.
REGISTRY = os.environ.get("BARISTA_IMAGE_REGISTRY", "ghcr.io/lf2foce")
PULL_WAIT = 20 * 60   # giây — dựng ảnh ở CI ~5–10 phút sau khi đẩy main; release.sh kiểm ~8 phút trước khi tới đây
PULL_EVERY = 30


def _pull(service: str, sha: str) -> str:
    """Kéo ảnh CI ``<REGISTRY>/barista-<service>:<sha>``; chưa có ⇒ thử lại mỗi ``PULL_EVERY`` giây tới ``PULL_WAIT``. Trả tên ảnh ở registry."""
    remote = f"{REGISTRY}/{IMAGE_PREFIX}-{service}:{sha}"
    deadline = time.monotonic() + PULL_WAIT
    while True:
        r = subprocess.run(["docker", "pull", "--quiet", remote], capture_output=True, text=True)
        if r.returncode == 0:
            return remote
        if time.monotonic() >= deadline:
            raise ValueError(f"Chưa kéo được {remote} sau {PULL_WAIT // 60} phút ({r.stderr.strip()[-200:]}). Xem GitHub Actions 'images' của commit "
                             "này; CI hỏng mà buộc phải phát hành: BARISTA_BUILD_ON_VPS=1 (dựng trên VPS — rủi ro sự cố 23/11/2026)")
        print(f"  chờ ảnh CI {remote} …", flush=True)
        time.sleep(PULL_EVERY)


def _fetch(service: str, sha: str, target: str, work: str) -> None:
    """Ảnh ``target`` từ ảnh CI của commit: kéo ảnh lõi, đặt tên tại máy ``barista-<service>:<sha>`` (bỏ tên registry — dọn ảnh chỉ thấy tên
    tại máy); ``target`` khác ảnh lõi (api có gói) ⇒ dựng lớp mỏng ghép gói lên ảnh lõi."""
    core = image(service, sha)
    if _revision(core) != sha:
        remote = _pull(service, sha)
        command("docker", "tag", remote, core)
        command("docker", "image", "rm", remote)   # chỉ gỡ tên registry; ảnh còn tên tại máy
    if target != core:
        command("docker", "build", "--quiet", "--build-arg", f"BASE={core}", "-f", "deploy/api-packs.Dockerfile", "-t", target, ".", cwd=work)


def build(sha: str, source: Path, instance: dict) -> tuple[str, dict[str, str]]:
    """Ảnh của commit (tar.gz của `git archive <sha>`, kèm gói ngoài lõi của bản cài ở ``deploy/packs/`` — ``shipped_packs``) giải nén ra thư mục
    tạm: kéo ảnh CI (``_fetch``), hoặc dựng trên VPS khi ``BARISTA_BUILD_ON_VPS=1``; nhãn revision = sha. Ảnh đã có (bản cài khác vừa phát hành cùng
    commit + cùng bộ gói) ⇒ dùng lại. Trả (deploy/stack.json của commit đã điền theo bản cài, ảnh từng dịch vụ)."""
    with tempfile.TemporaryDirectory(prefix="build-", dir=instance["root"]) as work:
        with tarfile.open(source, "r:gz") as t:
            t.extractall(work, filter="data")
        images = {"api": image("api", api_tag(sha, shipped_packs(Path(work), instance))), "web": image("web", sha)}
        on_vps = os.environ.get("BARISTA_BUILD_ON_VPS") == "1"
        how = None
        for s in SERVICES:
            if _revision(images[s]) != sha and not on_vps:
                _fetch(s, sha, images[s], work)
            elif _revision(images[s]) != sha:
                how = _builder() if how is None else how
                command("docker", *(how or ["build"]), "--quiet", "--build-arg", f"BARISTA_RELEASE={sha}", "-f", f"deploy/{s}.Dockerfile",
                        "-t", images[s], ".", cwd=work)
            if _revision(images[s]) != sha:
                raise ValueError(f"Nhãn revision của {images[s]} không khớp commit")
        stack = render((Path(work) / "deploy" / "stack.json").read_text(), instance, json_text=True)
        json.loads(stack)   # hỏng thì dừng trước khi đụng gì
        return stack, images


def _read_source(into: Path) -> Path:
    """stdin = tar.gz mã nguồn, tối đa 200 MB."""
    path = into / "src.tar.gz"
    size = 0
    with path.open("wb") as target:
        while chunk := sys.stdin.buffer.read(1024 * 1024):
            size += len(chunk)
            if size > 200 * 1024 * 1024:
                raise ValueError("Mã nguồn gửi lên quá 200 MB")
            target.write(chunk)
    if size == 0:
        raise ValueError("Không nhận được mã nguồn (stdin rỗng)")
    return path


def _ship(sha: str, generation: int | None, instance: dict, *, maintenance: bool = False) -> None:
    root = Path(instance["root"])
    # một phát hành một lúc TRÊN MÁY (mọi bản cài: ảnh dùng chung + 8 GB RAM), rồi của bản cài: nhận mã + dựng + migration + deploy
    with (root.parent / ".barista-release.lock").open("w") as machine, (root / "deploy.lock").open("w") as lock:
        fcntl.flock(machine, fcntl.LOCK_EX)
        fcntl.flock(lock, fcntl.LOCK_EX)
        with tempfile.TemporaryDirectory(prefix="incoming-", dir=root) as incoming:
            stack, images = build(sha, _read_source(Path(incoming)), instance)
        if generation is None:   # chạy tay: dùng lại số lượt của bản đang chạy
            st = root / "release.json"
            generation = json.loads(st.read_text())["generation"] if st.exists() else 0
        Deployment(instance).release(sha, generation, stack, maintenance=maintenance, images=images)


def receive(instance: dict) -> None:
    request = re.fullmatch(r"deploy ([0-9a-f]{40}) ([0-9]{1,12})", os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    if request is None:
        raise ValueError(f"Khoá SSH này chỉ nhận lệnh phát hành {instance['project']}")
    sha, generation = request.groups()
    _ship(sha, int(generation), instance)


if __name__ == "__main__":
    args = sys.argv[1:]
    usage = "Dùng: deploy.py [--instance <tên>] receive | git archive … | deploy.py [--instance <tên>] manual-v2 <sha> hoặc manual-maintenance <sha>"
    name = default_instance()   # khoá SSH ép `deploy.py receive` không nêu bản cài ⇒ bản cài chứa file này
    if args[:1] == ["--instance"] and len(args) >= 2:
        name, args = args[1], args[2:]
    if name is None:
        raise SystemExit(f"Thiếu bản cài: nêu --instance <tên> (không có bản cài mặc định). {usage}")
    target = load_instance(name)
    if args == ["receive"]:
        receive(target)
    elif len(args) == 2 and args[0] in ("manual", "manual-v2", "manual-maintenance") and SHA_RE.fullmatch(args[1]):
        # người vận hành chạy tay (lần đầu): `git archive --format=tar.gz <sha> | ssh vps python3 deploy.py [--instance lc] manual <sha>`
        _ship(args[1], None, target, maintenance=args[0] == "manual-maintenance")
    else:
        raise SystemExit(usage)
