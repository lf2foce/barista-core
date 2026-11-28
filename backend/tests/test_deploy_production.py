"""Chế độ production của bản cài (ADR 0194, review F3 12/11/2026): file bản cài khai ``"env": "production"`` ⇒ mẫu tắt đăng nhập demo của core + web,
đặt địa chỉ công khai, bắt buộc đăng nhập Google thật, không nạp mẫu; core + web từ chối khởi động khi cờ dev còn bật. Bản demo (bản cài mẫu ở fixtures/instances + bản cài thật ở barista-ops nếu có) ra
ĐÚNG như trước. Bootstrap chạy lại không làm mất biến người vận hành thêm vào runtime.env. Giá trị trong test là GIẢ."""

import copy
import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

from barista import production, seed
from barista.config import settings
from barista.http_api import app, lifespan

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ops = _load("barista_deploy_production", "deploy/ops/deploy.py")
packs = _load("barista_deploy_packs_production", "deploy/packs.py")
TEMPLATE = (REPO / "deploy/stack.json").read_text()
GOOGLE = "123-fake.apps.googleusercontent.com"
BASE = {"name": "acme", "root": "/root/acme", "project": "ACME", "project_description": "thử", "db_app": "acme-db", "app_app": "acme-app",
        "db_host": "acme-postgres", "api_host": "acme-api.example.vn", "web_host": "acme.example.vn", "demo_company": "Công ty thử",
        "seed_profile": "sample", "gate_user": "acme", "cron_file": "/etc/cron.d/acme-backup", "backup_cron": "0 3 * * *"}
PROD = {**BASE, "env": "production", "api_env": {"BARISTA_GOOGLE_WEB_CLIENT_ID": GOOGLE}}


def _legacy_render(template: str, inst: dict) -> str:
    """``render`` của deploy.py ở 8fc6a0e7 (trước chế độ production), chép nguyên — thước đo "bản demo ra đúng như trước"."""
    def value(m: re.Match) -> str:
        return json.dumps(inst[m.group(1)], ensure_ascii=False)[1:-1]
    text = ops.PLACEHOLDER.sub(value, template)
    if inst.get("api_env"):
        stack = json.loads(text)
        stack["services"]["api"]["environment"].update(inst["api_env"])
        text = json.dumps(stack, indent=2, ensure_ascii=False) + "\n"
    return text


def _write(tmp_path: Path, inst: dict) -> Path:
    (tmp_path / f"{inst['name']}.json").write_text(json.dumps(inst))
    return tmp_path


def test_demo_instances_render_exactly_as_before():
    """Mọi bản cài không khai production (bản cài mẫu của bộ test + bản cài thật ở barista-ops — có thì kiểm cả) + bản mẫu tổng hợp: stack.json y hệt render cũ.
    Khác biệt có chủ đích (1) 13/11/2026: bản cài có api_env nhận địa chỉ công khai theo host (BARISTA_PUBLIC_API_URL / BARISTA_WEB_URL — trước
    chỉ production có, bản demo thì lõi tưởng mình ở localhost:8300: OAuth / MCP issuer sai); api_env vẫn đè được. (2) 17/11/2026: ``"gate": false`` bỏ cổng
    mật khẩu (so JSON đã gỡ nhãn cổng); 25/11/2026: kèm ``BARISTA_DEMO_OPEN`` (app chọn vai không cần khoá). Không api_env và còn cổng ⇒ chữ y hệt."""
    fixtures = Path(__file__).resolve().parent / "fixtures" / "instances"
    real = ops.known_instances() if ops.instances_dir().is_dir() else []
    found = [i for i in [*ops.known_instances(fixtures), *real] if not ops.production(i)]
    for inst in [*found, BASE, {**BASE, "env": "demo", "api_env": {"BARISTA_WEB_URL": "https://acme.example.vn"}}]:
        if inst.get("api_env") or inst.get("gate") is False or inst.get("landing") is True or inst.get("support_email"):
            got, want = json.loads(ops.render(TEMPLATE, inst, json_text=True)), json.loads(_legacy_render(TEMPLATE, inst))
            if inst.get("api_env"):
                want["services"]["api"]["environment"] = {**want["services"]["api"]["environment"], "BARISTA_PUBLIC_API_URL": f"https://{inst['api_host']}",
                                                          "BARISTA_WEB_URL": f"https://{inst['web_host']}", **inst["api_env"]}
            if inst.get("gate") is False:   # khác biệt có chủ đích THỨ HAI (17/11/2026): demo công khai bỏ nhãn cổng mật khẩu + mắt xích đầu của router web
                labels, gate = want["services"]["web"]["deploy"]["labels"], f"{inst['name']}-gate"
                for k in [k for k in labels if k.startswith(f"traefik.http.middlewares.{gate}.")]:
                    del labels[k]
                chain = f"traefik.http.routers.{inst['name']}-web.middlewares"
                labels[chain] = ",".join(x for x in labels[chain].split(",") if x != gate)
                # khác biệt có chủ đích THỨ TƯ (25/11/2026): demo công khai mở trang chọn vai cho app gọi thẳng API (không cần khoá demo của BFF)
                want["services"]["api"]["environment"]["BARISTA_DEMO_OPEN"] = "true"
            if inst.get("landing") is True:   # khác biệt có chủ đích THỨ BA (23/11/2026): trang giới thiệu ở "/" cho người chưa đăng nhập
                want["services"]["web"]["environment"] = {**want["services"]["web"]["environment"], "BARISTA_WEB_HOME": "landing"}
            if inst.get("support_email"):   # khác biệt có chủ đích THỨ NĂM (28/11/2026): email liên hệ cho trang công khai /privacy, /support
                want["services"]["web"]["environment"] = {**want["services"]["web"]["environment"], "BARISTA_SUPPORT_EMAIL": inst["support_email"]}
            assert got == want, inst["name"]
        else:
            assert ops.render(TEMPLATE, inst, json_text=True) == _legacy_render(TEMPLATE, inst), inst["name"]
        stack = json.loads(ops.render(TEMPLATE, inst, json_text=True))
        api, web = stack["services"]["api"]["environment"], stack["services"]["web"]["environment"]
        assert api["BARISTA_DEV_AUTH"] == "true" and web["BARISTA_WEB_DEV_LOGIN"] == "1" and "BARISTA_ENV" not in api | web, inst["name"]
    assert {"demo", "bank"} <= {i["name"] for i in found}


def test_production_render_turns_demo_login_off_and_api_env_cannot_turn_it_back_on(tmp_path):
    inst = ops.load_instance("acme", _write(tmp_path, PROD))
    stack = json.loads(ops.render(TEMPLATE, inst, json_text=True))
    api, web = stack["services"]["api"]["environment"], stack["services"]["web"]["environment"]
    assert api["BARISTA_ENV"] == web["BARISTA_ENV"] == "production"
    assert api["BARISTA_DEV_AUTH"] == "false" and web["BARISTA_WEB_DEV_LOGIN"] == "0"
    assert api["BARISTA_GOOGLE_WEB_CLIENT_ID"] == GOOGLE
    assert api["BARISTA_PUBLIC_API_URL"] == "https://acme-api.example.vn" and api["BARISTA_WEB_URL"] == "https://acme.example.vn"
    # ngoài các biến trên, stack production y hệt bản demo cùng file bản cài
    demo = json.loads(_legacy_render(TEMPLATE, {**inst, "env": "demo"}))
    for svc, added in (("api", {"BARISTA_ENV", "BARISTA_DEV_AUTH", "BARISTA_PUBLIC_API_URL", "BARISTA_WEB_URL"}),
                       ("web", {"BARISTA_ENV", "BARISTA_WEB_DEV_LOGIN"})):
        got, was = stack["services"][svc].pop("environment"), demo["services"][svc].pop("environment")
        assert {k for k in got.keys() | was.keys() if got.get(k) != was.get(k)} == added, svc
    assert stack == demo   # cổng mật khẩu giữ mặc định
    for key in sorted(ops.PROFILE_KEYS):   # chế độ chỉ do "env" quyết — kể cả bản demo
        for env in ("production", "demo"):
            with pytest.raises(ValueError, match=key):
                ops.load_instance("acme", _write(tmp_path, {**PROD, "env": env, "api_env": {**PROD["api_env"], key: "1"}}))


def test_gate_false_drops_the_password_gate_in_production_and_public_demo(tmp_path):
    """``"gate": false`` bỏ cổng cả ở production lẫn bản demo công khai (17/11/2026, bổ sung ADR 0194: demo toàn dữ liệu giả, ai có link cũng vào);
    demo bỏ cổng vẫn là demo — đăng nhập dev giữ nguyên, không lén thành production. Chỉ nhận ``false``; vắng = có cổng."""
    for inst in ({**PROD, "gate": False}, {**BASE, "env": "demo", "gate": False}, {**BASE, "gate": False}):
        loaded = ops.load_instance("acme", _write(tmp_path, inst))
        text = ops.render(TEMPLATE, loaded, json_text=True)
        stack = json.loads(text)
        labels = stack["services"]["web"]["deploy"]["labels"]
        assert labels["traefik.http.routers.acme-web.middlewares"] == "acme-edge", inst
        assert "BARISTA_BASIC_AUTH" not in text and not any("acme-gate" in k for k in labels), inst
        assert (stack["services"]["web"]["environment"].get("BARISTA_ENV") == "production") == ops.production(loaded), inst
    gated = json.loads(ops.render(TEMPLATE, ops.load_instance("acme", _write(tmp_path, BASE)), json_text=True))
    assert gated["services"]["web"]["deploy"]["labels"]["traefik.http.routers.acme-web.middlewares"].split(",")[0] == "acme-gate"
    for bad in ({**PROD, "gate": True}, {**BASE, "gate": "no"}):
        with pytest.raises(ValueError, match="gate"):
            ops.load_instance("acme", _write(tmp_path, bad))


def test_landing_adds_only_the_web_home_flag_and_accepts_only_true(tmp_path):
    """``"landing": true`` (23/11/2026): web hiện trang giới thiệu ở "/" cho người chưa đăng nhập (web/src/proxy.ts) — chỉ thêm MỘT biến cho dịch vụ web,
    không đụng api / cổng / chế độ đăng nhập. Vắng ⇒ không có biến ("/" về thẳng /login như trước)."""
    plain = json.loads(ops.render(TEMPLATE, ops.load_instance("acme", _write(tmp_path, BASE)), json_text=True))
    landed = json.loads(ops.render(TEMPLATE, ops.load_instance("acme", _write(tmp_path, {**BASE, "landing": True})), json_text=True))
    assert "BARISTA_WEB_HOME" not in plain["services"]["web"]["environment"]
    assert landed["services"]["web"]["environment"].pop("BARISTA_WEB_HOME") == "landing"
    assert landed["services"]["web"] == plain["services"]["web"]   # (api nhận địa chỉ công khai theo host như mọi bản cài có tuỳ biến — test trên)
    for bad in ({**BASE, "landing": False}, {**BASE, "landing": "yes"}):
        with pytest.raises(ValueError, match="landing"):
            ops.load_instance("acme", _write(tmp_path, bad))


def test_support_email_adds_only_the_web_contact_and_must_be_an_email(tmp_path):
    """``"support_email"`` (28/11/2026): email liên hệ hiện trên trang công khai /privacy, /support của web (App Store bắt buộc có) — chỉ thêm MỘT biến
    cho dịch vụ web; lõi không ghi email nào. Vắng ⇒ không có biến (trang hướng người dùng tới quản trị công ty)."""
    plain = json.loads(ops.render(TEMPLATE, ops.load_instance("acme", _write(tmp_path, BASE)), json_text=True))
    mailed = json.loads(ops.render(TEMPLATE, ops.load_instance("acme", _write(tmp_path, {**BASE, "support_email": "hotro@acme.example.vn"})), json_text=True))
    assert "BARISTA_SUPPORT_EMAIL" not in plain["services"]["web"]["environment"]
    assert mailed["services"]["web"]["environment"].pop("BARISTA_SUPPORT_EMAIL") == "hotro@acme.example.vn"
    assert mailed["services"]["web"] == plain["services"]["web"]
    for bad in ({**BASE, "support_email": ""}, {**BASE, "support_email": "hotro"}, {**BASE, "support_email": True}, {**BASE, "support_email": "a b@x.vn"}):
        with pytest.raises(ValueError, match="support_email"):
            ops.load_instance("acme", _write(tmp_path, bad))


def test_production_without_real_login_stops_bootstrap_vps_and_release_machine(tmp_path):
    """load_instance là MỘT nguồn luật: bootstrap + deploy.py (VPS) gọi thẳng, release.sh qua ``packs.py instance`` — dừng trước test / gửi."""
    (tmp_path / "instances").mkdir()
    for bad, why in (({**PROD, "api_env": {}}, "BARISTA_GOOGLE_WEB_CLIENT_ID"),
                     ({**PROD, "api_env": {"BARISTA_GOOGLE_WEB_CLIENT_ID": "  "}}, "BARISTA_GOOGLE_WEB_CLIENT_ID"),
                     ({**PROD, "api_env": {**PROD["api_env"], "BARISTA_WEB_URL": "http://acme.example.vn"}}, "https")):
        with pytest.raises(ValueError, match=why):
            ops.load_instance("acme", _write(tmp_path, bad))
        _write(tmp_path / "instances", bad)
        with pytest.raises(SystemExit, match=why):
            packs.checked_instance_file("acme", tmp_path)
    _write(tmp_path / "instances", PROD)
    assert packs.checked_instance_file("acme", tmp_path) == tmp_path / "instances" / "acme.json"


def test_production_migrate_never_seeds_samples(tmp_path, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(ops, "command", lambda *a, **k: calls.append(a) or "")
    d = ops.Deployment.__new__(ops.Deployment)
    d.instance, d.root = {k: v for k, v in PROD.items() if k != "seed_profile"}, tmp_path   # production không cần hồ sơ nạp mẫu
    d.migrate("barista-api:" + "b" * 40)
    (call,) = calls
    assert call[-3:] == ("barista-api:" + "b" * 40, "-m", "barista.seed") and "--profile" not in call
    i = call.index("BARISTA_ENV=production")   # đặt THẲNG từ file bản cài, sau --env-file (runtime.env không quyết được)
    assert call[i - 1] == "-e" and call.index("--env-file") < i


class _FakeDb:
    def __init__(self, has_tenant: bool) -> None:
        self.has_tenant = has_tenant
        self.owner = self

    async def open(self, *_):
        pass

    async def close(self):
        pass

    def connection(self):
        db = self

        class Conn:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def execute(self, *_):
                class Cur:
                    async def fetchone(self):
                        return (1,) if db.has_tenant else None
                return Cur()
        return Conn()


async def test_seed_in_production_refuses_reset_and_never_seeds_samples(monkeypatch, capsys):
    """Kể cả chạy tay ``python -m barista.seed`` trên bản cài production: không --reset, DB trống chỉ migration, --upgrade chỉ cài mặc định."""
    monkeypatch.setattr(settings, "env", "production")
    migrated: list[str] = []

    async def fake_migrate(url: str):
        migrated.append(url)

    async def forbidden(*_a, **_k):
        raise AssertionError("production không được nạp mẫu")

    async def defaults():
        return 1

    monkeypatch.setattr(seed, "migrate", fake_migrate)
    monkeypatch.setattr(seed, "reset_schemas", forbidden)
    monkeypatch.setattr(seed, "sample_company", forbidden)
    monkeypatch.setattr(seed, "upgrade_demo", forbidden)
    monkeypatch.setattr(seed, "pack_profile", lambda *_: pytest.fail("production không được chọn hồ sơ nạp mẫu"))
    monkeypatch.setattr(seed, "install_defaults_everywhere", defaults)
    with pytest.raises(SystemExit, match="production"):
        await seed.main(True, "sample")
    assert migrated == []   # dừng trước khi chạm DB
    monkeypatch.setattr(seed, "db", _FakeDb(has_tenant=False))
    await seed.main(False, "sample")
    assert len(migrated) == 1 and "KHÔNG nạp mẫu" in capsys.readouterr().out
    monkeypatch.setattr(seed, "db", _FakeDb(has_tenant=True))
    await seed.main(False, "sample", upgrade=True)
    assert "production" in capsys.readouterr().out


def _settings(**update):
    return settings.model_copy(update={"env": "production", "dev_auth": False, "allow_app_temp": False, "webhook_allow_private": False,
                                       "demo_today": "", "llm_replay_dir": "", "google_web_client_id": GOOGLE,
                                       "public_api_url": "https://acme-api.example.vn", "web_url": "https://acme.example.vn", **update})


def test_core_production_check():
    assert production.errors(_settings()) == []
    assert production.errors(_settings(env="demo", dev_auth=True, google_web_client_id="", public_api_url="http://localhost:8300")) == []
    assert production.errors(_settings(env="", dev_auth=True)) == []   # máy dev
    assert production.errors(_settings(env="prod"))   # gõ sai ⇒ không lặng lẽ coi là máy dev
    for bad, why in (({"dev_auth": True}, "DEV_AUTH"), ({"allow_app_temp": True}, "ALLOW_APP_TEMP"),
                     ({"webhook_allow_private": True}, "WEBHOOK_ALLOW_PRIVATE"), ({"demo_today": "2026-11-30"}, "DEMO_TODAY"),
                     ({"llm_replay_dir": "/tmp/x"}, "LLM_REPLAY_DIR"), ({"google_web_client_id": ""}, "GOOGLE_WEB_CLIENT_ID"),
                     ({"public_api_url": "http://localhost:8300"}, "PUBLIC_API_URL"), ({"web_url": "http://localhost:3100"}, "WEB_URL")):
        (err,) = production.errors(_settings(**bad))
        assert why in err


def test_core_production_accepts_email_login_instead_of_google_and_checks_the_review_account():
    """ADR 0268: production cần đăng nhập THẬT — Google hoặc mã qua email (đã cấu hình thư); tài khoản người duyệt phải đủ email + mã 6 số."""
    mail = {"google_web_client_id": "", "smtp_url": "smtps://u:p@smtp.example.vn:465", "mail_from": "Barista <no-reply@example.vn>"}
    assert production.errors(_settings(**mail)) == []
    for half in ({"smtp_url": ""}, {"mail_from": ""}):   # thư nửa vời ⇒ không có đăng nhập thật nào
        (err,) = production.errors(_settings(**{**mail, **half}))
        assert "GOOGLE_WEB_CLIENT_ID" in err and "SMTP_URL" in err
    assert production.errors(_settings(**mail, review_login_email="reviewer@example.vn", review_login_code="123456")) == []
    for bad in ({"review_login_email": "reviewer@example.vn", "review_login_code": ""}, {"review_login_email": "", "review_login_code": "123456"},
                {"review_login_email": "reviewer@example.vn", "review_login_code": "12345"}, {"review_login_email": "reviewer@example.vn", "review_login_code": "abcdef"}):
        (err,) = production.errors(_settings(**mail, **bad))
        assert "REVIEW_LOGIN" in err


def test_production_instance_may_declare_email_login_instead_of_google(tmp_path):
    """ADR 0268: file bản cài production khai ``"email_login": true`` thì không đòi Google (lõi tự chặn khởi động nếu thư chưa cấu hình); chỉ nhận true."""
    inst = ops.load_instance("acme", _write(tmp_path, {**PROD, "api_env": {}, "email_login": True}))
    stack = json.loads(ops.render(TEMPLATE, inst, json_text=True))
    api, web = stack["services"]["api"]["environment"], stack["services"]["web"]["environment"]
    assert api["BARISTA_DEV_AUTH"] == "false" and web["BARISTA_WEB_DEV_LOGIN"] == "0" and "BARISTA_DEMO_OPEN" not in api
    for bad in ({**PROD, "email_login": False}, {**PROD, "email_login": "yes"}):
        with pytest.raises(ValueError, match="email_login"):
            ops.load_instance("acme", _write(tmp_path, bad))


async def test_api_refuses_to_start_in_production_with_dev_login_before_touching_the_database(monkeypatch):
    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "dev_auth", True)
    with pytest.raises(RuntimeError, match="BARISTA_DEV_AUTH"):
        async with lifespan(app):
            pytest.fail("không được lên")


def test_web_refuses_to_start_in_production_with_dev_login():
    run = subprocess.run(["node", str(REPO / "deploy/serve-web.mjs")], env={"PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin",
                         "BARISTA_ENV": "production", "BARISTA_WEB_DEV_LOGIN": "1"}, capture_output=True, text=True, timeout=20)
    assert run.returncode == 1 and "BARISTA_WEB_DEV_LOGIN" in run.stderr
    # session.ts: devLoginAllowed() tắt khi BARISTA_ENV=production (test vitest không chạy ở đây — chốt chữ)
    assert 'if (process.env.BARISTA_ENV === "production") return false;' in (REPO / "web/src/lib/server/session.ts").read_text()


def _bootstrap(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "deploy/ops"))
    boot = _load("barista_deploy_bootstrap_production", "deploy/ops/bootstrap.py")
    monkeypatch.setattr(boot, "command", lambda *a: "$apr1$fakegate")
    return boot


SECRETS = {"APP_PASSWORD": "fake-app", "OWNER_PASSWORD": "fake-owner", "BARISTA_DEMO_KEY": "fake-demo", "BARISTA_SESSION_SECRET": "fake-session",
           "gate_user": "acme", "gate_password": "fake-pw"}


def _env(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines())


def test_bootstrap_rerun_keeps_every_variable_the_operator_added(tmp_path, monkeypatch):
    """Review F3: prepare lần hai từng bỏ BARISTA_GOOGLE_WEB_CLIENT_ID / BARISTA_ALERT_TELEGRAM_* (chỉ giữ vài tiền tố). Nay giữ MỌI khoá bootstrap
    không sở hữu, nguyên giá trị; khoá bootstrap sở hữu (chế độ đăng nhập…) đặt lại đúng."""
    boot = _bootstrap(monkeypatch)
    (tmp_path / "llm.key").write_text("fake-llm")
    first = _env(boot._runtime(tmp_path, BASE, SECRETS, "acme-app"))
    added = {"BARISTA_GOOGLE_WEB_CLIENT_ID": GOOGLE, "BARISTA_ALERT_TELEGRAM_TOKEN": "fake-token", "BARISTA_ALERT_TELEGRAM_CHAT": "-100fake",
             "BARISTA_PACKS": "crm,finance", "BARISTA_R2_BUCKET": "fake-bucket", "BARISTA_TURN_CLOUDFLARE_TOKEN": "fake-turn",
             # dựng lại DB từ bản gốc đổi tên DB (runbook 11) — chạy lại prepare không trả về DB cũ
             "BARISTA_DATABASE_URL": "postgresql://barista_app:fake-app@acme-postgres:5432/barista_v3"}
    tampered = {**first, **added, "BARISTA_DEV_AUTH": "false", "BARISTA_ENV": "production"}
    (tmp_path / "runtime.env").write_text("".join(f"{k}={v}\n" for k, v in tampered.items()))
    again = _env(boot._runtime(tmp_path, BASE, SECRETS, "acme-app"))
    assert {k: again.get(k) for k in added} == added
    assert again["BARISTA_DEV_AUTH"] == "true" and "BARISTA_ENV" not in again   # bản demo như trước: chế độ do bootstrap quyết
    for k in ("BARISTA_EDGE_SECRET", "BARISTA_FILES_SIGN_SECRET", "BARISTA_APPS_SIGNING_SECRET", "BARISTA_LLM_API_KEY"):
        assert again[k] == first[k], k
    assert set(again) == set(first) | set(added)
    # chuyển sang production: tắt đăng nhập dev, khoá người vận hành vẫn còn; trở lại demo thì BARISTA_ENV biến mất
    prod = _env(boot._runtime(tmp_path, copy.deepcopy(PROD), SECRETS, "acme-app"))
    assert prod["BARISTA_ENV"] == "production" and prod["BARISTA_DEV_AUTH"] == "false" and {k: prod[k] for k in added} == added
    back = _env(boot._runtime(tmp_path, BASE, SECRETS, "acme-app"))
    assert "BARISTA_ENV" not in back and back["BARISTA_DEV_AUTH"] == "true"
    # khoá sinh-một-lần để trống ⇒ sinh lại (thiếu khoá biên thì stack không khởi động)
    (tmp_path / "runtime.env").write_text((tmp_path / "runtime.env").read_text().replace(f"BARISTA_EDGE_SECRET={first['BARISTA_EDGE_SECRET']}", "BARISTA_EDGE_SECRET="))
    assert len(_env(boot._runtime(tmp_path, BASE, SECRETS, "acme-app"))["BARISTA_EDGE_SECRET"]) >= 32
