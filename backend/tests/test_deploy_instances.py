"""Bản cài thứ hai trên cùng VPS (runbook 11 "Bản cài thứ hai"): deploy/stack.json + compose.yaml là MẪU điền từ barista-ops/instances/<tên>.json
(repo riêng tư cạnh repo lõi — lõi chia sẻ đối tác không mang file bản cài, 0155). Test dùng hai bản cài MẪU trung tính ở ``fixtures/instances``
(không đọc, không nêu bản cài thật); bản cài mẫu phải ra ĐÚNG chữ đã ghim; hai bản cài không được chung tên router / miền / volume / DB / cron; ảnh dùng chung nên
dọn ảnh của bản cài này không được xoá ảnh bản cài kia đang chạy / để quay lui; seed --upgrade của bản cài một gói (vd ngân hàng mẫu) không nhận dữ liệu sàn mẫu."""

import hashlib
import importlib.util
import io
import json
import tarfile
import uuid
from pathlib import Path

import pytest

from barista import seed
from barista.config import settings
from tests.conftest import owner_val

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("barista_deploy_instances", REPO / "deploy/ops/deploy.py")
assert SPEC is not None and SPEC.loader is not None
ops = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ops)

# sha256 của deploy/stack.json / deploy/compose.yaml điền theo bản cài mẫu ``demo`` (phần chữ như ở e575240, trước khi thành mẫu). Đổi CÓ CHỦ Ý
# mẫu triển khai (RAM, nhãn…) ⇒ cập nhật hai số này cùng commit — test giữ việc tách mẫu không lặng lẽ đổi bản đang chạy.
# 10/11 (0150 lát 4, review): khoá proxy biên BARISTA_EDGE_SECRET (BẮT BUỘC — thiếu ⇒ stack không khởi động) cho api + web, middleware Traefik
# gắn X-Barista-Edge ở router https của api + web
# 16/11 (0233): ảnh ``barista-api`` / ``barista-web`` (trước mang tên bản cài đầu) + bản cài mẫu trung tính thay bản cài thật — đã đối chiếu: bản
# cài thật điền mẫu mới, đổi lại tên ảnh cũ, ra đúng hai số cũ (chỉ khác tên ảnh)
# 18/11: compose.yaml — cpus / mem_limit / shared_buffers thành khoá tuỳ chọn của bản cài (mặc định như cũ 0.75 / 512m) + dòng command
# shared_buffers (mặc định 128MB = mặc định của Postgres) ⇒ đổi hash compose có chủ ý
STACK_SHA256 = "b033358f0f4057b69afed14467d27e73937533b8f9838e6a2ca39ece549883da"
COMPOSE_SHA256 = "6b41d18a659ca09f5e7358e0052356d7370d41f439f8057204e61928895cc4aa"
SHA = "b" * 40
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "instances"   # bản cài MẪU: demo (sàn môi giới mẫu), bank (ngân hàng mẫu)


def _render(name: str) -> tuple[dict, str, dict]:
    inst = ops.load_instance(name, FIXTURES)
    stack = ops.render((REPO / "deploy/stack.json").read_text(), inst, json_text=True)
    compose = ops.render((REPO / "deploy/compose.yaml").read_text(), inst, json_text=False)
    return inst, compose, json.loads(stack)


def test_the_sample_install_renders_byte_identical_to_the_pinned_files():
    # phần mẫu ở chế độ DEMO: y hệt trước khi có bản cài; biến thêm của bản cài kiểm riêng bên dưới. Phần production (ADR 0194) kiểm ở
    # test_deploy_production; ở đây ghim mẫu demo (bản cài demo khác vẫn ra đúng như trước)
    inst = {**ops.load_instance("demo", FIXTURES), "env": "demo", "api_env": {}}
    stack = ops.render((REPO / "deploy/stack.json").read_text(), inst, json_text=True)
    compose = ops.render((REPO / "deploy/compose.yaml").read_text(), inst, json_text=False)
    # biến thêm SAU khi có bản cài, có chủ đích (mỗi dòng một lý do): TURN Cloudflare cho gọi video khác mạng (0114 bổ sung 09/11)
    for added in ('        "BARISTA_TURN_CLOUDFLARE_KEY_ID": "${BARISTA_TURN_CLOUDFLARE_KEY_ID}",\n',
                  '        "BARISTA_TURN_CLOUDFLARE_TOKEN": "${BARISTA_TURN_CLOUDFLARE_TOKEN}",\n',
                  # khung kết nối chung (0244): khoá mã hoá khoá kết nối của công ty — không có ⇒ app không nhận khoá kết nối, phần khác chạy như thường
                  '        "BARISTA_CONNECTOR_KEY": "${BARISTA_CONNECTOR_KEY}",\n',
                  # khoá app Meta của BẢN CÀI: appsecret_proof cho lời gọi Graph (tuỳ chọn; token kết nối nay ở khung kết nối — 0244)
                  '        "BARISTA_META_ADS_APP_SECRET": "${BARISTA_META_ADS_APP_SECRET}",\n',
                  # khoá kiểm địa chỉ webhook CHUNG của app Meta (GET hub.verify_token — 1.66); trống ⇒ cửa chung từ chối kiểm địa chỉ
                  '        "BARISTA_META_ADS_VERIFY_TOKEN": "${BARISTA_META_ADS_VERIFY_TOKEN}",\n',
                  # "Kết nối Facebook" (OAuth, 1.67): mã app Meta của bản cài + danh sách quyền xin (trống = mặc định của gói)
                  '        "BARISTA_META_ADS_APP_ID": "${BARISTA_META_ADS_APP_ID}",\n',
                  '        "BARISTA_META_ADS_SCOPES": "${BARISTA_META_ADS_SCOPES}",\n',
                  # đăng nhập bằng mã qua email (0205 §5c): máy chủ thư + địa chỉ gửi; trống ⇒ trang đăng nhập không hiện ô email
                  '        "BARISTA_SMTP_URL": "${BARISTA_SMTP_URL}",\n',
                  '        "BARISTA_MAIL_FROM": "${BARISTA_MAIL_FROM}",\n'):
        assert stack.count(added) == 1, added
        stack = stack.replace(added, "")
    # cổng mật khẩu web đặt lại 11/11/2026 (bỏ 04/11 lúc demo còn dữ liệu giả; bản cài đầu nay có dữ liệu thật) — xem test_every_instance_web_router_sits_behind_the_password_gate
    for added in ('          "traefik.http.middlewares.demo-gate.basicauth.users": "${BARISTA_BASIC_AUTH:?BARISTA_BASIC_AUTH thiếu — cổng mật khẩu web bắt buộc (runbook 11)}",\n',
                  '          "traefik.http.middlewares.demo-gate.basicauth.realm": "DEMO",\n'):
        assert stack.count(added) == 1, added
        stack = stack.replace(added, "")
    gated = '          "traefik.http.routers.demo-web.middlewares": "demo-gate,demo-edge",'
    assert stack.count(gated) == 1
    stack = stack.replace(gated, '        "traefik.http.routers.demo-web.middlewares": "demo-edge",')
    assert hashlib.sha256(stack.encode()).hexdigest() == STACK_SHA256
    assert hashlib.sha256(compose.encode()).hexdigest() == COMPOSE_SHA256


def test_an_install_differs_only_by_its_public_urls():
    """Bản cài thêm đúng các biến địa chỉ công khai so với bản trước khi có bản cài: địa chỉ web cho link gửi khách (thiếu ⇒ link trỏ localhost:3100)."""
    inst = {**ops.load_instance("demo", FIXTURES), "env": "demo"}   # biến bản cài tự khai, xét ở mẫu demo (production thêm biến của chế độ — test_deploy_production)
    full = json.loads(ops.render((REPO / "deploy/stack.json").read_text(), inst, json_text=True))
    base = json.loads(ops.render((REPO / "deploy/stack.json").read_text(), {**inst, "api_env": {}}, json_text=True))
    env_full, env_base = full["services"]["api"]["environment"], base["services"]["api"]["environment"]
    diff = {k: v for k, v in env_full.items() if env_base.get(k) != v}
    assert diff.pop("BARISTA_WEB_URL") == "https://demo.vi-du.test"
    # địa chỉ API công khai theo api_host (13/11/2026): thiếu ⇒ OAuth / MCP issuer của bản cài demo là localhost:8300
    assert diff.pop("BARISTA_PUBLIC_API_URL") == "https://demo-api.vi-du.test"
    # client Google "Web" (12/11/2026, đăng nhập thật — production): không phải bí mật, khai ở file bản cài (runbook 09)
    assert set(diff) <= {"BARISTA_GOOGLE_WEB_CLIENT_ID"} and all(v.endswith(".apps.googleusercontent.com") for v in diff.values())
    full["services"]["api"]["environment"] = env_base
    assert full == base


def _labels(stack: dict) -> dict[str, str]:
    return {k: v for s in ("api", "web") for k, v in stack["services"][s]["deploy"]["labels"].items()}


def test_instances_share_images_but_nothing_else():
    names = [i["name"] for i in ops.known_instances(FIXTURES)]
    assert names == ["bank", "demo"]
    rendered = {n: _render(n) for n in names}
    for n, (inst, compose, stack) in rendered.items():
        assert "{{" not in compose and "{{" not in json.dumps(stack), n
        assert f"aliases: [{inst['db_host']}]" in compose and f"{inst['root']}/ops/initdb:" in compose
        assert stack["services"]["api"]["volumes"] == [f"{inst['root']}/files:{ops.FILES_MOUNT}"]
        assert stack["services"]["api"]["environment"]["BARISTA_DEMO_COMPANY"] == inst["demo_company"]
        labels = _labels(stack)
        assert labels[f"traefik.http.routers.{n}-api.rule"] == f"Host(`{inst['api_host']}`)"
        assert labels[f"traefik.http.routers.{n}-web.rule"] == f"Host(`{inst['web_host']}`)"
        assert {s["image"] for s in stack["services"].values()} == {"barista-api:bootstrap", "barista-web:bootstrap"}   # ảnh dùng chung
    # mỗi thứ mang tên trên máy / mạng chung / Traefik / Dokploy chỉ thuộc MỘT bản cài
    for key in ("root", "project", "db_app", "app_app", "db_host", "api_host", "web_host", "cron_file", "gate_user"):
        values = [r[0][key] for r in rendered.values()]
        assert len(set(values)) == len(values), key
    routers = [{k.split(".")[3] for k in _labels(r[2]) if k.startswith("traefik.http.routers.")} for r in rendered.values()]
    assert not set.intersection(*routers)
    bank = rendered["bank"][0]
    assert bank["seed_profile"] == "bank" and bank["files_store"] == "local"   # không chép kho / bucket của bản cài khác (runbook 11 cảnh báo)
    assert rendered["bank"][2]["services"]["api"]["environment"]["BARISTA_WEB_URL"] == "https://bank.vi-du.test"
    # đồng hồ demo (kernel/clock.py): chứng từ mẫu ghi ngày cuối tháng 11/2026 — chỉ bản demo ngân hàng mẫu đặt, bản dùng thật thì không
    assert rendered["bank"][2]["services"]["api"]["environment"]["BARISTA_DEMO_TODAY"] == "2026-11-30"
    assert "BARISTA_DEMO_TODAY" not in rendered["demo"][2]["services"]["api"]["environment"]


needs_edu = pytest.mark.skipif(not (ops.instances_dir() / "edu.json").is_file(), reason="không có barista-ops/instances/edu.json")


@needs_edu
def test_edu_demo_install_pins_the_pack_that_brings_its_seed_profile():
    """Bản demo chuỗi trung tâm (edu.phenau.com): gói Đào tạo đã rời lõi (0155 đợt ba) ⇒ bản cài GHIM gói ``edu`` — hồ sơ nạp mẫu ``edu-demo`` đi cùng
    gói (điểm vào ``barista.seed_profiles``), lõi không còn mang. Khai demo (dữ liệu giả), tên công ty khớp công ty mà hồ sơ dựng, kho tệp thư mục (stack đòi
    BARISTA_FILES_STORE), giờ sao lưu không đụng bản cài khác."""
    inst = ops.load_instance("edu")
    stack = json.loads(ops.render((REPO / "deploy/stack.json").read_text(), inst, json_text=True))
    if "edu" not in inst["packs"]:   # file bản cài ở repo barista-ops còn ở trạng thái trước khi gói rời lõi — phát hành kế tiếp cần ghim trước
        pytest.skip("barista-ops/instances/edu.json chưa ghim gói edu: hồ sơ edu-demo nay đi cùng gói — thêm \"packs\": {\"edu\": …} trước lần phát hành tới")
    assert "edu" in inst["packs"] and inst["env"] == "demo"   # gói khác (vd. gói đối tác) được ghim thêm cạnh gói edu and inst["seed_profile"] == "edu-demo" not in seed.BUILTIN_PROFILES
    assert inst["files_store"] == "local"
    if (prof := seed.pack_profile("edu-demo")) is not None:   # gói đang cài ở môi trường test: tên công ty khớp công ty hồ sơ dựng
        assert prof.PACK == "edu" and inst["demo_company"] == prof.NAME and "edu-demo" in seed.profiles()
    assert stack["services"]["api"]["environment"]["BARISTA_WEB_URL"] == "https://edu.phenau.com"
    assert "BARISTA_DEMO_TODAY" not in stack["services"]["api"]["environment"]   # lịch sử demo tính lùi từ hôm nay, không ghim ngày
    crons = [i["backup_cron"] for i in ops.known_instances()]
    assert len(set(crons)) == len(crons)


def test_dokploy_project_is_optional_and_only_picks_the_dokploy_project(tmp_path, monkeypatch):
    """``dokploy_project`` (tuỳ chọn): nhiều bản cài demo chung MỘT project Dokploy; tên compose vẫn theo ``project`` riêng. Vắng ⇒ như cũ."""
    monkeypatch.syspath_prepend(str(REPO / "deploy/ops"))
    spec = importlib.util.spec_from_file_location("barista_deploy_bootstrap_env", REPO / "deploy/ops/bootstrap.py")
    assert spec is not None and spec.loader is not None
    boot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(boot)

    class Fake:
        def __init__(self, existing: list[str]) -> None:
            self.projects, self.created = list(existing), []

        def api(self, endpoint: str, body: dict | None = None):
            if endpoint == "project.all":
                return [{"name": n, "environments": [{"name": "production", "environmentId": f"env-{n}"}]} for n in self.projects]
            assert endpoint == "project.create" and body is not None
            self.projects.append(body["name"])
            self.created.append(body)
            return {}

    base = {"project": "EDU", "project_description": "mô tả riêng"}
    d = Fake([])
    assert boot._environment(d, base) == "env-EDU" and d.created == [{"name": "EDU", "description": "mô tả riêng"}]   # vắng ⇒ project của bản cài
    d = Fake([])
    shared = {**base, "dokploy_project": "Barista demo", "dokploy_project_description": "chung"}
    assert boot._environment(d, shared) == "env-Barista demo" and d.created == [{"name": "Barista demo", "description": "chung"}]
    d = Fake(["Barista demo"])   # đã có project chung (bản cài trước tạo) ⇒ dùng lại, không tạo thêm
    assert boot._environment(d, {**shared, "project": "OTHER"}) == "env-Barista demo" and d.created == []
    (tmp_path / "x.json").write_text(json.dumps({"name": "x", "root": "/root/x", "db_host": "x-db", "api_host": "a.x", "web_host": "w.x",
                                                 "cron_file": "/etc/cron.d/x", "dokploy_project": "  "}))
    with pytest.raises(ValueError, match="dokploy_project"):
        ops.load_instance("x", tmp_path)


def test_instance_file_must_match_its_name_and_root(tmp_path):
    (tmp_path / "x.json").write_text(json.dumps({**ops.load_instance("bank", FIXTURES), "name": "x"}))
    with pytest.raises(ValueError, match="/root/x"):
        ops.load_instance("x", tmp_path)
    with pytest.raises(ValueError, match="không hợp lệ"):
        ops.load_instance("../demo")


def test_there_is_no_default_install_outside_an_install_folder(tmp_path):
    """Lõi không có bản cài mặc định (0233): ở repo (deploy/ops) không suy ra được ⇒ deploy.py / bootstrap.py không nêu ``--instance`` thì dừng;
    trên VPS file nằm ở <thư mục bản cài>/ops ⇒ khoá SSH ép ``deploy.py receive`` (không nêu bản cài) nhận đúng bản cài chứa nó."""
    import shutil
    import subprocess
    import sys

    assert ops.default_instance() is None
    for script in ("deploy.py", "bootstrap.py"):
        r = subprocess.run([sys.executable, str(REPO / "deploy/ops" / script), "receive" if script == "deploy.py" else "prepare"],
                           capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
        assert r.returncode != 0 and "Thiếu bản cài" in r.stderr, (script, r.stderr)
    (tmp_path / "demo" / "ops" / "instances").mkdir(parents=True)
    shutil.copy(REPO / "deploy/ops/deploy.py", tmp_path / "demo" / "ops" / "deploy.py")
    shutil.copy(FIXTURES / "demo.json", tmp_path / "demo" / "ops" / "instances" / "demo.json")
    spec = importlib.util.spec_from_file_location("barista_deploy_installed", tmp_path / "demo" / "ops" / "deploy.py")
    assert spec is not None and spec.loader is not None
    installed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installed)
    assert installed.default_instance() == "demo"


def _two_installs(tmp_path, monkeypatch, services: str) -> Path:
    """Hai bản cài trỏ thư mục tạm: demo chạy SHA cũ "1…", để quay lui "2…" (ảnh mang tên CŨ ``legacy-*`` — như bản cài chạy trước khi đổi tên
    ảnh); bank chạy "3…". Docker giả trả ``services`` cho `service ls`."""
    d = tmp_path / "instances"
    d.mkdir()
    for name, release, rollback in (("demo", "1" * 40, "2" * 40), ("bank", "3" * 40, None)):
        root = tmp_path / name
        root.mkdir()
        (d / f"{name}.json").write_text(json.dumps({**ops.load_instance(name, FIXTURES), "root": str(root)}))
        (root / "release.json").write_text(json.dumps({"sha": release}))
        if rollback:
            (root / "rollback.json").write_text(json.dumps({"images": {"api": f"legacy-api:{rollback}", "web": f"legacy-web:{rollback}"}}))
    # load_instance chốt root = /root/<tên>; ở đây thư mục tạm ⇒ bỏ chốt đó, giữ phần đọc file
    monkeypatch.setattr(ops, "known_instances", lambda directory=None: [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))])
    monkeypatch.setattr(ops, "command", lambda *a, **k: services if a[:3] == ("docker", "service", "ls") else "")
    return d


def test_pruning_one_install_keeps_images_the_other_runs_or_rolls_back_to(tmp_path, monkeypatch):
    _two_installs(tmp_path, monkeypatch, services=f"barista-api:{'4' * 40}\npostgres:18-alpine\nbarista-web:{'4' * 40}@sha256:abc\n")
    assert ops.shas_in_use() == {"1" * 40, "2" * 40, "3" * 40, "4" * 40}
    listed = "\n".join(f"{r}-{s}:{c * 40}" for r in ("barista", "legacy") for s in ("api", "web") for c in "12345")
    listed += f"\nbarista-api:bootstrap\nother:latest\notherapp-api:{'5' * 40}"   # ảnh của ứng dụng khác trên máy (thẻ trông như sha) không bao giờ bị đụng"
    removed: list[str] = []

    def docker(*args, **kw):
        if args[:2] == ("docker", "images"):
            return listed
        if args[:3] == ("docker", "service", "ls"):
            return f"barista-api:{'4' * 40}"
        return ""
    monkeypatch.setattr(ops, "command", docker)
    monkeypatch.setattr(ops.subprocess, "run", lambda args, **kw: removed.append(args[-1]))
    d = ops.Deployment.__new__(ops.Deployment)
    d.prune(keep={"5" * 40})   # bản bank vừa phát hành = 5…; trước đây chỉ giữ 5… + ảnh cũ của chính nó
    assert removed == []
    d.prune(keep=set())
    # ảnh tên cũ (sổ quay lui của demo nhắc ``legacy-*``) được dọn như ảnh mới khi hết dùng; ảnh đang chạy / để quay lui giữ
    assert sorted(removed) == [f"barista-api:{'5' * 40}", f"barista-web:{'5' * 40}", f"legacy-api:{'5' * 40}", f"legacy-web:{'5' * 40}"]


def test_pruning_stops_when_it_cannot_tell_what_others_use(monkeypatch):
    removed: list = []
    monkeypatch.setattr(ops, "shas_in_use", lambda: (_ for _ in ()).throw(ValueError("instances/x.json hỏng")))
    monkeypatch.setattr(ops.subprocess, "run", lambda args, **kw: removed.append(args))
    ops.Deployment.__new__(ops.Deployment).prune(keep=set())
    assert removed == []


def _archive(tmp_path) -> Path:
    path = tmp_path / "src.tar.gz"
    with tarfile.open(path, "w:gz") as t:
        data = (REPO / "deploy/stack.json").read_bytes()
        info = tarfile.TarInfo("deploy/stack.json")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    return path


@pytest.mark.parametrize("have", [True, False])
def test_build_reuses_the_shared_image_of_this_commit_and_renders_the_stack(tmp_path, monkeypatch, have):
    monkeypatch.setenv("BARISTA_BUILD_ON_VPS", "1")   # đường dựng trên VPS (CI hỏng); đường mặc định kéo ảnh CI: test_build_pulls_the_ci_image…
    monkeypatch.setattr(ops, "_builder", lambda: [])   # máy có buildx (CI) ⇒ không tạo builder thật; builder có trần: test_deploy_packs
    built: list[str] = []
    state = {"barista-api": SHA if have else None, "barista-web": SHA if have else None}
    monkeypatch.setattr(ops, "_revision", lambda image: state[image.split(":")[0]])

    def docker(*args, **kw):
        assert args[:2] == ("docker", "build")
        tag = args[args.index("-t") + 1]
        built.append(tag)
        state[tag.split(":")[0]] = SHA
        return ""
    monkeypatch.setattr(ops, "command", docker)
    inst = {**ops.load_instance("bank", FIXTURES), "root": str(tmp_path), "packs": {}}   # mã thử không kèm gói
    text, images = ops.build(SHA, _archive(tmp_path), inst)
    stack = json.loads(text)
    assert images == {"api": f"barista-api:{SHA}", "web": f"barista-web:{SHA}"}   # bản cài không khai gói ⇒ thẻ ảnh như trước khi có gói
    assert built == ([] if have else [f"barista-api:{SHA}", f"barista-web:{SHA}"])
    assert stack["services"]["api"]["volumes"] == [f"{tmp_path}/files:{ops.FILES_MOUNT}"]
    assert "traefik.http.routers.bank-web.rule" in stack["services"]["web"]["deploy"]["labels"]


def test_build_pulls_the_ci_image_instead_of_building_on_the_vps(tmp_path, monkeypatch):
    """Sự cố 23/11/2026: dựng ảnh trên VPS dùng chung làm Swarm khởi động lại mọi dịch vụ. Mặc định: kéo ảnh CI của commit (images.yml), đặt tên
    tại máy như trước (Dokploy / quay lui / dọn ảnh không đổi), gỡ tên registry; không ``docker build`` nào. Chưa kéo được tới hạn ⇒ dừng, không
    tự dựng."""
    monkeypatch.delenv("BARISTA_BUILD_ON_VPS", raising=False)
    calls: list[tuple] = []
    state: dict[str, str] = {}
    monkeypatch.setattr(ops, "_revision", lambda image: state.get(image))

    def docker(*args, **kw):
        calls.append(args)
        if args[:2] == ("docker", "tag"):
            state[args[3]] = SHA
        return ""
    monkeypatch.setattr(ops, "command", docker)
    pulled: list[str] = []
    monkeypatch.setattr(ops.subprocess, "run", lambda args, **kw: pulled.append(args[-1]) or ops.subprocess.CompletedProcess(args, 0, "", ""))
    inst = {**ops.load_instance("bank", FIXTURES), "root": str(tmp_path), "packs": {}}
    _, images = ops.build(SHA, _archive(tmp_path), inst)
    assert images == {"api": f"barista-api:{SHA}", "web": f"barista-web:{SHA}"}
    assert pulled == [f"{ops.REGISTRY}/barista-api:{SHA}", f"{ops.REGISTRY}/barista-web:{SHA}"]
    assert calls == [("docker", "tag", f"{ops.REGISTRY}/barista-api:{SHA}", f"barista-api:{SHA}"), ("docker", "image", "rm", f"{ops.REGISTRY}/barista-api:{SHA}"),
                     ("docker", "tag", f"{ops.REGISTRY}/barista-web:{SHA}", f"barista-web:{SHA}"), ("docker", "image", "rm", f"{ops.REGISTRY}/barista-web:{SHA}")]

    state.clear()
    monkeypatch.setattr(ops, "PULL_WAIT", 0)
    monkeypatch.setattr(ops.subprocess, "run", lambda args, **kw: ops.subprocess.CompletedProcess(args, 1, "", "manifest unknown"))
    calls.clear()
    with pytest.raises(ValueError, match="BARISTA_BUILD_ON_VPS=1"):
        ops.build(SHA, _archive(tmp_path), inst)
    assert calls == []                                                              # ảnh CI chưa có ⇒ dừng, không dựng trên VPS


def test_migrate_seeds_the_profile_of_the_install(tmp_path, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(ops, "command", lambda *a, **k: calls.append(a) or "")
    d = ops.Deployment.__new__(ops.Deployment)
    d.instance, d.root = ops.load_instance("bank", FIXTURES), tmp_path
    d.migrate(f"barista-api:{SHA}")
    assert calls == [calls[0]] and calls[0][-5:] == (f"barista-api:{SHA}", "-m", "barista.seed", "--profile", "bank")


async def test_pack_profile_upgrade_sets_demo_autonomy_and_refuses_other_installs(monkeypatch):
    """Hồ sơ nạp mẫu của một GÓI (vd ``bank`` của gói tài trợ thương mại — bản phân phối riêng): ``--upgrade`` chỉ đặt mức tự chủ demo, và dừng
    khi công ty demo không bật gói của hồ sơ (không nhận nhầm dữ liệu sàn mẫu / bản cài khác)."""
    d = f"b{uuid.uuid4().hex[:8]}.demo"
    t = await seed.create_tenant(f"Công ty thử {d}", d, people=[("admin", "Quản trị thử", "admin", "QT", True)], packs=("training",),
                                 about="công ty thử (test)")
    monkeypatch.setattr(settings, "demo_company", "")
    await seed.upgrade_pack_demo(t, {"training"}, "training")   # không phải bản cài demo ⇒ không đặt mức tự chủ
    assert await owner_val("SELECT (SELECT count(*) FROM core.agent_policies WHERE tenant_id = %(t)s)"
                           " + (SELECT count(*) FROM core.agent_trust WHERE tenant_id = %(t)s)", {"t": t}) == 0
    monkeypatch.setattr(settings, "demo_company", "Công ty thử (demo)")
    await seed.upgrade_pack_demo(t, {"training"}, "training")
    assert await owner_val("SELECT count(*) FROM core.agent_policies WHERE tenant_id = %s", (t,)) > 0
    with pytest.raises(SystemExit, match="training"):
        await seed.upgrade_pack_demo(t, {"real_estate"}, "training")


def test_seed_profiles_of_installed_packs_come_from_their_entry_points(monkeypatch, tmp_path):
    """Lõi không biết tên hồ sơ của gói: ``--profile`` nhận hồ sơ có sẵn + hồ sơ do gói đã cài khai (``barista.seed_profiles``); tên trùng hồ sơ có
    sẵn / khai hai lần ⇒ dừng; gói không cài ⇒ hồ sơ không có (argparse từ chối, bước phát hành dừng trước khi chạm DB trống)."""
    import importlib.metadata as md

    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "fake_profile.py").write_text('PACK = "training"\nNAME = "X"\nDONE = "ok"\n')
    eps = [md.EntryPoint("bank", "fake_profile", seed.PROFILE_GROUP)]
    real = md.entry_points
    monkeypatch.setattr(md, "entry_points", lambda **kw: md.EntryPoints(eps) if kw.get("group") == seed.PROFILE_GROUP else real(**kw))
    prof = seed.pack_profile("bank")
    assert seed.profiles()[-1] == "bank" and prof is not None and prof.PACK == "training" and seed.pack_profile("sample") is None
    eps.append(md.EntryPoint("bank", "other_profile", seed.PROFILE_GROUP))
    with pytest.raises(SystemExit, match="hai lần"):
        seed.pack_profile("bank")
    eps[:] = [md.EntryPoint("sample", "fake_profile", seed.PROFILE_GROUP)]
    with pytest.raises(SystemExit, match="hai lần"):
        seed.pack_profile("sample")
    eps.clear()
    assert "bank" not in seed.profiles() and seed.pack_profile("bank") is None


# thiếu khoá ⇒ phát hành từ chối khởi động (không lặng lẽ coi mọi người gọi là địa chỉ Traefik — review 10/11)
REQUIRED_EDGE = "${BARISTA_EDGE_SECRET:?BARISTA_EDGE_SECRET thiếu — chạy lại bootstrap (runbook 11)}"


def test_bootstrap_generates_the_edge_key_once_and_the_stack_puts_it_on_both_routers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Khoá proxy biên (review 0150 lát 4): bootstrap sinh MỘT lần vào runtime.env, chạy lại giữ nguyên; stack gắn nó vào api + web (biến môi trường)
    và vào middleware Traefik của router https của cả hai."""
    monkeypatch.syspath_prepend(str(REPO / "deploy/ops"))
    spec = importlib.util.spec_from_file_location("barista_deploy_bootstrap", REPO / "deploy/ops/bootstrap.py")
    assert spec is not None and spec.loader is not None
    boot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(boot)
    monkeypatch.setattr(boot, "command", lambda *a: "$apr1$gate")
    inst = {"db_host": "db"}
    s = {"APP_PASSWORD": "a", "OWNER_PASSWORD": "o", "BARISTA_DEMO_KEY": "d", "BARISTA_SESSION_SECRET": "s", "gate_user": "u", "gate_password": "p"}
    (tmp_path / "llm.key").write_text("k")
    first = dict(line.split("=", 1) for line in boot._runtime(tmp_path, inst, s, "demo").splitlines())
    again = dict(line.split("=", 1) for line in boot._runtime(tmp_path, inst, s, "demo").splitlines())
    assert len(first["BARISTA_EDGE_SECRET"]) >= 32 and again["BARISTA_EDGE_SECRET"] == first["BARISTA_EDGE_SECRET"]
    stack = json.loads((REPO / "deploy/stack.json").read_text())
    for svc in ("api", "web"):
        assert stack["services"][svc]["environment"]["BARISTA_EDGE_SECRET"] == REQUIRED_EDGE
        assert stack["services"][svc]["deploy"]["labels"][f"traefik.http.routers.{{{{name}}}}-{svc}.middlewares"].split(",")[-1] == "{{name}}-edge"   # web: cổng mật khẩu trước, edge sau
    labels = stack["services"]["api"]["deploy"]["labels"]
    assert labels["traefik.http.middlewares.{{name}}-edge.headers.customrequestheaders.X-Barista-Edge"] == REQUIRED_EDGE
    assert "barista.edge:app" in (REPO / "deploy/serve.py").read_text() and '"*"' not in (REPO / "deploy/serve.py").read_text()


def test_bootstrap_records_the_postgres_container_so_a_maintenance_release_can_back_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bản cài mới (edu 11/11/2026): bootstrap không ghi postgres_container ⇒ phát hành bảo trì đầu tiên từ chối ("cần postgres_container").
    Giờ prepare ghi tên container thật; database_name đã đổi (dựng lại DB từ bản gốc) thì giữ."""
    monkeypatch.syspath_prepend(str(REPO / "deploy/ops"))
    spec = importlib.util.spec_from_file_location("barista_deploy_bootstrap_pg", REPO / "deploy/ops/bootstrap.py")
    assert spec is not None and spec.loader is not None
    boot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(boot)
    ops = tmp_path / "operations.json"
    ops.write_text(json.dumps({"compose_id": "c1"}))
    boot.record_database(tmp_path, "/edu-db-x-postgres-1")
    assert json.loads(ops.read_text()) == {"compose_id": "c1", "postgres_container": "edu-db-x-postgres-1", "database_name": "barista"}
    ops.write_text(json.dumps({"database_name": "barista_v3", "postgres_container": "old"}))
    boot.record_database(tmp_path, "demo-db-y-postgres-1")
    assert json.loads(ops.read_text()) == {"database_name": "barista_v3", "postgres_container": "demo-db-y-postgres-1"}


def test_every_instance_web_router_sits_behind_the_password_gate():
    """11/11/2026: bản demo đầu tiên lên dữ liệu THẬT mà cổng mật khẩu đã bị bỏ từ 04/11 (612454c2, lúc còn dữ liệu giả) ⇒ ai cũng mở được trang chọn vai
    và danh sách người dùng. Cổng là MẶC ĐỊNH của mọi bản cài: router web phải đi qua middleware basicauth lấy BARISTA_BASIC_AUTH (bootstrap sinh)."""
    # bản cài mẫu + mọi bản cài thật đang có ở barista-ops (nếu có repo đó — không nêu tên); bản khai ``"gate": false`` (production, hoặc demo công khai toàn dữ liệu giả) cố ý bỏ cổng
    for inst in [*ops.known_instances(FIXTURES), *ops.known_instances()]:
        if inst.get("gate") is False:
            continue
        name, stack = inst["name"], json.loads(ops.render((REPO / "deploy/stack.json").read_text(), inst, json_text=True))
        labels = stack["services"]["web"]["deploy"]["labels"] if "deploy" in stack["services"]["web"] else stack["services"]["web"]["labels"]
        chain = labels[f"traefik.http.routers.{name}-web.middlewares"].split(",")
        assert chain[0] == f"{name}-gate", (name, chain)
        assert labels[f"traefik.http.middlewares.{name}-gate.basicauth.users"].startswith("${BARISTA_BASIC_AUTH"), name


def test_only_a_public_demo_opens_dev_login_to_apps_without_the_demo_key():
    """``"gate": false`` của bản demo = demo CÔNG KHAI (toàn dữ liệu giả) ⇒ lõi nhận đăng nhập dev không cần X-Demo-Key — app iPhone gọi thẳng API cũng thấy
    trang chọn vai như web. Bản có cổng (dữ liệu thật) và production KHÔNG bật; production có ``gate: false`` vẫn không (lõi còn chặn khởi động)."""
    inst = ops.load_instance("demo", FIXTURES)
    api = lambda i: json.loads(ops.render((REPO / "deploy/stack.json").read_text(), i, json_text=True))["services"]["api"]["environment"]  # noqa: E731
    assert api({**inst, "env": "demo", "gate": False})["BARISTA_DEMO_OPEN"] == "true"
    assert "BARISTA_DEMO_OPEN" not in api({**inst, "env": "demo"})
    assert "BARISTA_DEMO_OPEN" not in api({**inst, "env": "production", "gate": False, "api_env": {"BARISTA_GOOGLE_WEB_CLIENT_ID": "x.apps"}})


async def test_pack_samples_choose_their_profiles_finish_last_and_add_companies(monkeypatch, tmp_path, capsys):
    """Phần mẫu của gói ngoài lõi (``barista.seed_samples``, 0155 đợt ba) — lõi không biết tên gói: ``PROFILES`` chọn biến thể công ty mẫu gói tự bật
    (không khai = chỉ công ty đủ gói của bộ test; ``()`` = không ở đâu), ``finish`` chạy ở bước cuối chỉ khi công ty bật gói, ``COMPANIES`` góp công ty
    vào hồ sơ nhiều công ty. Gói giả ở đây mượn khoá hai gói của lõi (``findings``, ``mgmt``: tắt mặc định, không có trong công ty mẫu)."""
    import importlib.metadata as md

    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "fake_sample_a.py").write_text('PROFILES = ("san-moi-gioi",)\nasync def finish(tid):\n    return f"xong {tid}"\n')
    (tmp_path / "fake_sample_b.py").write_text('PROFILES = ()\nasync def _co():\n    return {"done": "thêm một công ty"}\nCOMPANIES = {"pay-demo": _co}\n')
    (tmp_path / "fake_sample_c.py").write_text("TEAMS = []\n")
    eps = [md.EntryPoint("findings", "fake_sample_a", seed.SAMPLE_GROUP), md.EntryPoint("mgmt", "fake_sample_b", seed.SAMPLE_GROUP),
           md.EntryPoint("training", "fake_sample_c", seed.SAMPLE_GROUP), md.EntryPoint("khong_cai", "fake_sample_c", seed.SAMPLE_GROUP)]
    real = md.entry_points
    monkeypatch.setattr(md, "entry_points", lambda **kw: md.EntryPoints(eps) if kw.get("group") == seed.SAMPLE_GROUP else real(**kw))
    assert set(seed.samples()) == {"findings", "mgmt", "training"}                              # gói chưa nạp ⇒ bỏ qua
    assert seed.profile_packs("san-moi-gioi") == ("findings",) and seed.profile_packs("san-moi-gioi-demo") == ()
    assert seed.profile_packs("full") == ("training",) and seed.sample_packs() == (*seed.ALL_PACKS, "training")
    d = f"s{uuid.uuid4().hex[:8]}.demo"
    people = [("admin", "Quản trị thử", "admin", "QT", True)]
    off = await seed.create_tenant(f"Công ty mẫu tắt {d}", d, people=people, packs=(), about="công ty thử (test)")
    capsys.readouterr()
    await seed.sample_finish(off)
    assert capsys.readouterr().out == ""                                                       # công ty không bật gói ⇒ bước cuối của gói không chạy
    on = await seed.create_tenant(f"Công ty mẫu bật {d}", f"on-{d}", people=people, packs=("findings",), about="công ty thử (test)")
    await seed.sample_finish(on)
    assert capsys.readouterr().out == f"xong {on}\n"
    from barista import seed_pay

    for name in ("office", "service_shop"):
        async def _none() -> dict:
            return {}
        monkeypatch.setattr(seed_pay, name, _none)
    assert (await seed_pay.seed_pay())["extra"] == {"mgmt": {"done": "thêm một công ty"}}


async def test_the_one_person_shop_profile_is_a_core_company_packs_only_add_their_samples(monkeypatch):
    """Hồ sơ ``cua-hang`` (ADR 0262 mục C): LÕI dựng công ty một người trung tính (một chủ, Tài chính, công tắc công ty nhiều người tắt, chủ được ghi tiền khách
    trả); gói đã cài khai ``PROFILES`` chứa "cua-hang" được bật + góp mẫu. Không gói nào khai ⇒ công ty vẫn dựng được, chỉ có phần của lõi."""
    import importlib.metadata as md

    real = md.entry_points
    monkeypatch.setattr(md, "entry_points", lambda **kw: md.EntryPoints([]) if kw.get("group") == seed.SAMPLE_GROUP else real(**kw))
    assert "cua-hang" in seed.profiles() and seed.profile_packs("cua-hang") == ()
    tid = await seed.shop_company(name=f"Cửa hàng thử {uuid.uuid4().hex[:6]} (dữ liệu giả)")
    chu = await seed.who(tid, "chu", seed.SHOP_DOMAIN)
    assert {"finance", "talk"} <= set(await seed._packs_on(tid))
    members = await seed._owner_rows("SELECT count(*) AS n FROM core.memberships WHERE tenant_id = %s AND active", (tid,))
    assert members[0]["n"] == 1 and chu.role == "admin"
    off = await seed._owner_rows("SELECT key FROM core.settings WHERE tenant_id = %s AND NOT enabled", (tid,))
    assert set(seed.SHOP_SETTINGS_OFF) <= {r["key"] for r in off}
