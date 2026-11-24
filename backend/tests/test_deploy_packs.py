"""GÓI NGOÀI LÕI ĐI KÈM BẢN PHÁT HÀNH (0155): máy phát hành (``deploy/packs.py``) đọc file bản cài ở barista-ops trước, lấy từng gói đúng tag của
barista-packs, kiểm ``requires-core`` với bề mặt ``barista.api`` của lõi, đóng chung một tar.gz; VPS (``deploy/ops/deploy.py``) chỉ nhận bộ gói
ĐÚNG file bản cài của nó và gắn thẻ ảnh api theo bộ gói. Repo gói ở đây là một repo git tạm (không chạm barista-packs thật)."""

import importlib.util
import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

import barista.api

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


packs = _load("barista_deploy_packs", "deploy/packs.py")
ops = _load("barista_deploy_ops_packs", "deploy/ops/deploy.py")
SHA = "c" * 40


# Bản cài mẫu (đủ khoá cho mẫu stack / compose) — file bản cài thật ở barista-ops, lõi chia sẻ không mang chúng.
SAMPLE_INSTANCE = {
    "name": "lc", "root": "/root/lc", "project": "LC", "project_description": "bản cài thử", "db_app": "lc-db", "app_app": "lc-app",
    "db_host": "lc-postgres", "api_host": "lc-api.example.test", "web_host": "lc.example.test", "demo_company": "Công ty thử (demo)",
    "seed_profile": "", "gate_user": "lc", "cron_file": "/etc/cron.d/lc-backup", "backup_cron": "45 3 * * *", "files_store": "local", "api_env": {},
}


def _git(repo: Path, *args: str) -> str:
    # tên người ghi riêng cho test: máy CI không khai user.name / user.email ⇒ `git tag -a` / commit lỗi (mã 128)
    who = ("-c", "user.name=Barista test", "-c", "user.email=test@example.invalid")
    return subprocess.run(["git", *who, "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _pack_repo(tmp_path: Path, *, version: str = "1.0.0", requires: str = ">=1.3,<2", tag: str | None = "demo_pack/v1.0.0") -> Path:
    """Repo gói tạm: thư mục ``demo_pack/`` (pyproject + package + test) giống bố cục barista-packs, một commit, một tag."""
    repo = tmp_path / "packs-repo"
    pkg = repo / "demo_pack" / "src" / "barista_pack_demo"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('from barista.api import Pack\nPack("demo_pack", "Gói thử", "", kind="industry")\n')
    (repo / "demo_pack" / "pyproject.toml").write_text(
        f'[project]\nname = "barista-pack-demo"\nversion = "{version}"\n'
        '[project.entry-points."barista.packs"]\ndemo_pack = "barista_pack_demo"\n'
        f'[tool.barista]\nrequires-core = "{requires}"\n')
    (repo / "demo_pack" / "WIP.txt").write_text("đã commit")
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "gói thử")
    if tag:
        _git(repo, "tag", "-a", tag, "-m", "bản gói thử")   # tag gói CÓ CHÚ THÍCH (DIEU-PHOI luật 5; deploy/packs.published)
    (repo / "demo_pack" / "WIP.txt").write_text("sửa dở — không được lọt vào bản gửi")
    return repo


def test_requires_core_is_checked_against_the_published_surface_version() -> None:
    assert packs.api_version((REPO / packs.API).read_text()) == barista.api.__version__
    assert packs.satisfies("1.3.0", ">=1.3,<2") and packs.satisfies("1.9.4", ">=1.3, <2")
    assert not packs.satisfies("2.0.0", ">=1.3,<2") and not packs.satisfies("1.2.9", ">=1.3")
    with pytest.raises(ValueError):
        packs.satisfies("1.3.0", "~=1.3")
    with pytest.raises(ValueError):
        packs.satisfies("1.3.0", "")


def test_the_instance_file_comes_only_from_barista_ops(tmp_path: Path) -> None:
    """Lõi chia sẻ đối tác không mang file bản cài (0155): chỉ ``<barista-ops>/instances/<tên>.json``; không có ⇒ dừng, không lùi về lõi."""
    ops_dir = tmp_path / "barista-ops"
    (ops_dir / "instances").mkdir(parents=True)
    assert not (REPO / "deploy" / "instances").exists()
    with pytest.raises(SystemExit, match="Không có"):
        packs.instance_file("lc", ops_dir)
    (ops_dir / "instances" / "lc.json").write_text("{}")
    assert packs.instance_file("lc", ops_dir) == ops_dir / "instances" / "lc.json"
    with pytest.raises(SystemExit, match="Không có"):
        packs.instance_file("nope", ops_dir)
    with pytest.raises(SystemExit, match="không hợp lệ"):
        packs.instance_file("../lc", ops_dir)


def test_staging_takes_the_tagged_pack_only_and_writes_a_manifest(tmp_path: Path) -> None:
    repo = _pack_repo(tmp_path)
    out = tmp_path / "stage"
    manifest = packs.stage({"name": "lc", "env": "demo", "packs": {"demo_pack": "v1.0.0"}}, repo, out, "1.3.0")
    assert manifest == {"demo_pack": {"version": "v1.0.0", "commit": _git(repo, "rev-parse", "HEAD"), "dist": "barista-pack-demo"}}
    staged = out / "deploy" / "packs"
    assert json.loads((staged / "manifest.json").read_text()) == manifest
    assert (staged / "demo_pack" / "WIP.txt").read_text() == "đã commit"     # đúng tag, không phải thư mục làm việc
    assert packs.stage({"name": "demo"}, repo, tmp_path / "none", "1.3.0") == {} and not (tmp_path / "none").exists()


@pytest.mark.parametrize(("kw", "packs_", "error"), [
    ({"tag": None}, {"demo_pack": "v1.0.0"}, "không có tag demo_pack/v1.0.0"),
    ({}, {"demo_pack": "v1.0.1"}, "không có tag demo_pack/v1.0.1"),
    ({"version": "1.0.1"}, {"demo_pack": "v1.0.0"}, "pyproject ghi version"),
    ({"requires": ">=2,<3"}, {"demo_pack": "v1.0.0"}, "cần lõi >=2,<3"),
    ({}, {"demo_pack": "1.0.0"}, "dạng v1.2.3"),
    ({}, {"other_pack": "v1.0.0"}, "không có tag other_pack/v1.0.0"),
])
def test_staging_refuses_a_missing_tag_a_wrong_version_or_a_core_mismatch(tmp_path: Path, kw: dict, packs_: dict, error: str) -> None:
    repo = _pack_repo(tmp_path, **kw)
    with pytest.raises(SystemExit, match=error):
        packs.stage({"name": "lc", "env": "demo", "packs": packs_}, repo, tmp_path / "stage", "1.3.0")


def test_a_pack_can_come_from_a_partners_own_repo_pinned_to_a_reviewed_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gói của ĐỐI TÁC (ADR 0242 phần 3): mục ``"packs"`` nêu ``"repo": "<thư mục>"`` ⇒ lấy đúng tag + commit ghim từ repo đó (cạnh repo lõi), gói khác
    của cùng bản cài vẫn từ repo gói chính thức; manifest ghi nguồn. Luôn phải ghim commit (kể cả bản demo); thiếu repo / tên lạ ⇒ dừng."""
    official = _pack_repo(tmp_path / "ours")
    (tmp_path / "partners").mkdir()
    theirs = _pack_repo(tmp_path / "partners")
    theirs = theirs.rename(tmp_path / "partners" / "goi-doi-tac")
    monkeypatch.setenv("BARISTA_PACK_REPOS_DIR", str(tmp_path / "partners"))
    pin = _git(theirs, "rev-parse", "demo_pack/v1.0.0^{commit}")
    inst = {"name": "x", "env": "demo", "packs": {"demo_pack": {"version": "v1.0.0", "commit": pin, "repo": "goi-doi-tac"}}}
    manifest = packs.stage(inst, tmp_path / "no-official-repo-needed", tmp_path / "out", "1.3.0")
    assert manifest["demo_pack"] == {"version": "v1.0.0", "commit": pin, "dist": "barista-pack-demo", "repo": "goi-doi-tac"}
    assert (tmp_path / "out" / "deploy" / "packs" / "demo_pack" / "WIP.txt").read_text() == "đã commit"
    assert ops.shipped_packs(tmp_path / "out", inst) == manifest                         # VPS: đối chiếu version + commit như mọi gói, bỏ qua "repo"
    # gói chính thức đi cùng gói đối tác thì repo chính thức vẫn phải có
    assert packs.stage({"name": "x", "packs": {"demo_pack": {"version": "v1.0.0", "commit": _git(official, "rev-parse", "HEAD")}}},
                       official, tmp_path / "out2", "1.3.0")["demo_pack"].get("repo") is None
    for bad, error in (({"version": "v1.0.0", "repo": "goi-doi-tac"}, "phải ghim commit đã review"),             # demo cũng phải ghim
                       ({"version": "v1.0.0", "commit": pin, "repo": "khong-co"}, "không thấy repo của đối tác"),
                       ({"version": "v1.0.0", "commit": "d" * 40, "repo": "goi-doi-tac"}, "tag đã bị dời"),          # commit ghim khác tag của họ
                       ({"version": "v1.0.0", "commit": pin, "repo": "../leo-ra-ngoai"}, "khoá chữ thường"),         # tên thư mục, không đường dẫn
                       ({"version": "v1.0.0", "commit": pin, "repo": "https://example.test/x.git"}, "khoá chữ thường")):   # không URL: máy phát hành không tự tải
        with pytest.raises(SystemExit, match=error):
            packs.stage({"name": "x", "env": "demo", "packs": {"demo_pack": bad}}, official, tmp_path / "bad", "1.3.0")


def test_staging_stops_when_a_packs_tests_need_a_pack_the_instance_does_not_list(tmp_path: Path) -> None:
    """0191: ``[tool.barista] test-requires`` — bộ test của gói là kịch bản xuyên gói, phát hành test gói trên đúng bộ gói của bản cài ⇒ bản cài phải
    khai cả gói đó (dừng trước khi test, có tên gói thiếu); khai đủ thì qua."""
    repo = _pack_repo(tmp_path)
    toml = repo / "demo_pack" / "pyproject.toml"
    toml.write_text(toml.read_text() + 'test-requires = ["other_pack"]\n')
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "test cần gói khác")
    _git(repo, "tag", "-f", "-a", "demo_pack/v1.0.0", "-m", "dời tag")
    with pytest.raises(SystemExit, match=r"bộ test của gói cần gói other_pack \(test-requires\)"):
        packs.stage({"name": "lc", "env": "demo", "packs": {"demo_pack": "v1.0.0"}}, repo, tmp_path / "s1", "1.3.0")
    toml.write_text(toml.read_text().replace('["other_pack"]', '["demo_pack"]'))
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "test cần gói đã khai")
    _git(repo, "tag", "-f", "-a", "demo_pack/v1.0.0", "-m", "dời tag")
    assert "demo_pack" in packs.stage({"name": "lc", "env": "demo", "packs": {"demo_pack": "v1.0.0"}}, repo, tmp_path / "s2", "1.3.0")


def test_an_instance_can_pin_the_tag_commit_and_a_moved_tag_stops_the_release(tmp_path: Path) -> None:
    """Tag xoá / tạo lại được (kể cả tag có chú thích) ⇒ bản cài GHIM commit của tag (``{"version", "commit"}``): đúng commit ⇒ như dạng ngắn; tag
    bị dời sang commit khác ⇒ dừng trước khi đóng gói. Dạng ngắn ``"v1.2.3"`` vẫn nhận; commit phải là sha đủ 40 ký tự."""
    repo = _pack_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    got = packs.stage({"name": "lc", "env": "demo", "packs": {"demo_pack": {"version": "v1.0.0", "commit": head}}}, repo, tmp_path / "s1", "1.3.0")
    assert got["demo_pack"]["commit"] == head
    assert packs.declared({"env": "demo", "packs": {"b": "v1.0.0", "a": {"version": "v2.0.0", "commit": head}}}) == {"a": ("v2.0.0", head), "b": ("v1.0.0", "")}
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "sửa sau tag")
    _git(repo, "tag", "-f", "-a", "demo_pack/v1.0.0", "-m", "dời tag")                     # ai đó dời tag sang commit mới
    with pytest.raises(SystemExit, match="tag đã bị dời"):
        packs.stage({"name": "lc", "env": "demo", "packs": {"demo_pack": {"version": "v1.0.0", "commit": head}}}, repo, tmp_path / "s2", "1.3.0")
    for bad in ({"version": "v1.0.0", "commit": head[:12]}, {"version": "v1.0.0", "sha": head}, {"commit": head}):
        with pytest.raises(SystemExit, match="40 ký tự"):
            packs.declared({"name": "lc", "env": "demo", "packs": {"demo_pack": bad}})


def test_premerge_can_stage_the_packs_at_a_branch_instead_of_the_pinned_tag(tmp_path: Path) -> None:
    """``scripts/premerge.sh --packs-main``: gói của bản cài lấy ở một ref của repo gói (chưa tag) — bắt gói vỡ trước khi gói được tag. Bỏ kiểm tag /
    ghim (ghim trỏ commit cũ vẫn qua), vẫn kiểm ``requires-core``; ref không có ⇒ dừng."""
    repo = _pack_repo(tmp_path, tag=None)
    old = _git(repo, "rev-parse", "HEAD")
    toml = repo / "demo_pack" / "pyproject.toml"
    toml.write_text(toml.read_text().replace('version = "1.0.0"', 'version = "1.0.1"'))
    (repo / "demo_pack" / "WIP.txt").write_text("đã commit")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "bản chưa tag")
    (repo / "demo_pack" / "WIP.txt").write_text("sửa dở")
    inst = {"name": "lc", "packs": {"demo_pack": {"version": "v1.0.0", "commit": old}}}
    got = packs.stage(inst, repo, tmp_path / "s1", "1.3.0", "HEAD")
    assert got == {"demo_pack": {"version": "v1.0.1", "commit": _git(repo, "rev-parse", "HEAD"), "dist": "barista-pack-demo"}}
    assert (tmp_path / "s1" / "deploy" / "packs" / "demo_pack" / "WIP.txt").read_text() == "đã commit"   # commit của ref, không thư mục làm việc
    with pytest.raises(SystemExit, match="cần lõi"):
        packs.stage(inst, repo, tmp_path / "s2", "2.0.0", "HEAD")
    with pytest.raises(SystemExit, match="không có ref nope"):
        packs.stage(inst, repo, tmp_path / "s3", "1.3.0", "nope")


def test_a_pinned_tag_must_be_annotated_and_the_same_on_the_shared_remote(tmp_path: Path) -> None:
    """14/11/2026: 7 tag gói chỉ có ở một máy, một tag trên GitHub là tag trần khác tag ở máy ⇒ máy / phiên khác không dựng lại được bản đang
    chạy. Phát hành DỪNG khi tag ghim không có chú thích, chưa có trên ``origin``, hoặc khác trên ``origin``; đúng cả ba ⇒ qua."""
    inst = {"name": "lc", "env": "demo", "packs": {"demo_pack": "v1.0.0"}}
    plain = _pack_repo(tmp_path / "a", tag=None)
    _git(plain, "tag", "demo_pack/v1.0.0")                                                    # tag trần
    with pytest.raises(SystemExit, match="không có chú thích"):
        packs.stage(inst, plain, tmp_path / "s0", "1.3.0")

    repo = _pack_repo(tmp_path / "b")                                                         # tag có chú thích, chưa có kho chung
    assert "demo_pack" in packs.stage(inst, repo, tmp_path / "s1", "1.3.0")                  # repo tạm không có origin ⇒ chỉ kiểm chú thích
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")
    with pytest.raises(SystemExit, match="chưa có trên origin"):
        packs.stage(inst, repo, tmp_path / "s2", "1.3.0")
    _git(repo, "push", "-q", "origin", "refs/tags/demo_pack/v1.0.0")
    assert packs.stage(inst, repo, tmp_path / "s3", "1.3.0")["demo_pack"]["commit"] == _git(repo, "rev-parse", "HEAD")
    commit = _git(repo, "rev-parse", "demo_pack/v1.0.0^{commit}")
    _git(repo, "push", "-q", "--force", "origin", f"{commit}:refs/tags/demo_pack/v1.0.0")    # GitHub giữ tag trần, máy giữ tag có chú thích
    with pytest.raises(SystemExit, match="khác trên origin"):
        packs.stage(inst, repo, tmp_path / "s4", "1.3.0")


def test_production_instances_must_pin_the_pack_commit_and_only_demo_may_not(tmp_path: Path) -> None:
    """Review 11/11: tag xoá / tạo lại được ⇒ bản cài THẬT bắt buộc ghim commit; chỉ ``"env": "demo"`` (dữ liệu giả) được dạng chuỗi ngắn. Máy phát hành
    (``deploy/packs.py``) và VPS (``deploy/ops/deploy.py``) cùng chặn; ``env`` lạ bị từ chối; vắng ``env`` = production."""
    repo = _pack_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    loose = {"name": "demo", "packs": {"demo_pack": "v1.0.0"}}                              # vắng env ⇒ production
    for inst in (loose, {**loose, "env": "production"}):
        with pytest.raises(SystemExit, match="chưa ghim commit"):
            packs.declared(inst)
        with pytest.raises(SystemExit, match="chưa ghim commit"):
            packs.stage(inst, repo, tmp_path / "s0", "1.3.0")                                # chặn trước khi đóng gói
    assert not (tmp_path / "s0").exists()
    assert packs.declared({**loose, "env": "demo"}) == {"demo_pack": ("v1.0.0", "")}         # demo: dạng ngắn vẫn nhận
    pinned = {**loose, "packs": {"demo_pack": {"version": "v1.0.0", "commit": head}}}
    assert packs.declared(pinned) == {"demo_pack": ("v1.0.0", head)}                        # production + ghim: qua
    assert packs.stage(pinned, repo, tmp_path / "s1", "1.3.0")["demo_pack"]["commit"] == head
    assert packs.declared({"name": "demo"}) == {} and packs.declared({"name": "demo", "packs": {}}) == {}   # không gói ⇒ không cần ghim
    with pytest.raises(SystemExit, match="chỉ nhận production, demo"):
        packs.declared({**loose, "env": "staging"})
    with pytest.raises(SystemExit, match="chỉ nhận production, demo"):
        packs.declared({"name": "demo", "env": "Demo"})                                      # kể cả bản cài không gói: env phải đúng nghĩa
    # VPS: cùng luật, không tin máy phát hành
    work = _shipped(tmp_path / "v", ONE, ("trade_finance",))
    with pytest.raises(ValueError, match="chưa ghim commit"):
        ops.shipped_packs(work, {"name": "demo", "packs": {"trade_finance": "v1.0.0"}})
    assert ops.shipped_packs(work, {"name": "lc", "env": "demo", "packs": {"trade_finance": "v1.0.0"}}) == ONE
    assert ops.shipped_packs(work, {"name": "demo", "packs": {"trade_finance": {"version": "v1.0.0", "commit": "a" * 40}}}) == ONE


def test_the_instance_loader_refuses_an_unknown_env(tmp_path: Path) -> None:
    d = tmp_path / "instances"
    d.mkdir()
    inst = {**SAMPLE_INSTANCE, "name": "lc"}
    (d / "lc.json").write_text(json.dumps({**inst, "env": "demo"}))
    assert ops.load_instance("lc", d)["env"] == "demo"
    (d / "lc.json").write_text(json.dumps({**inst, "env": "prod"}))
    with pytest.raises(ValueError, match="env"):
        ops.load_instance("lc", d)


def test_the_real_instance_files_in_barista_ops_pin_every_pack_unless_demo() -> None:
    """Các file bản cài thật (barista-ops cạnh repo lõi) qua được luật ghim — thiếu ghim ở bản cài thật thì phát hành dừng ở đây trước."""
    ops_dir = packs.ops_dir()
    files = sorted((ops_dir / "instances").glob("*.json")) if (ops_dir / "instances").is_dir() else []
    if not files:
        pytest.skip("không có repo barista-ops cạnh repo lõi")
    for f in files:
        inst = json.loads(f.read_text())
        assert packs.declared(inst) is not None, f.name
        ops.load_instance(inst["name"], f.parent)


def test_a_missing_packs_repo_stops_an_install_that_needs_packs(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="không thấy repo gói"):
        packs.stage({"name": "lc", "env": "demo", "packs": {"demo_pack": "v1.0.0"}}, tmp_path / "barista-packs", tmp_path / "stage", "1.3.0")


def test_the_bundle_is_the_core_archive_plus_the_packs(tmp_path: Path) -> None:
    repo = _pack_repo(tmp_path)
    core = tmp_path / "core.tar"

    def make_core(api: bytes) -> None:
        with tarfile.open(core, "w") as t:
            for name, data in (("deploy/packs/README.md", b"x"), (packs.API, api)):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
    make_core(b'__version__ = "2.0.0"\n')   # lõi của COMMIT phát hành (không phải thư mục làm việc) quyết requires-core
    with pytest.raises(SystemExit, match="cần lõi"):
        packs.bundle({"name": "lc", "env": "demo", "packs": {"demo_pack": "v1.0.0"}}, repo, core, tmp_path / "x.tar.gz")
    make_core(b'__version__ = "1.3.0"\n')
    out = tmp_path / "release.tar.gz"
    assert set(packs.bundle({"name": "lc", "env": "demo", "packs": {"demo_pack": "v1.0.0"}}, repo, core, out)) == {"demo_pack"}
    with tarfile.open(out, "r:gz") as t:
        names = set(t.getnames())
    assert {"deploy/packs/README.md", "deploy/packs/manifest.json", "deploy/packs/demo_pack/pyproject.toml",
            "deploy/packs/demo_pack/src/barista_pack_demo/__init__.py"} <= names
    assert not [n for n in names if Path(n).name.startswith("._")]


def _shipped(tmp_path: Path, manifest: dict | None, dirs: tuple[str, ...] = ()) -> Path:
    work = tmp_path / "work"
    (work / "deploy" / "packs").mkdir(parents=True)
    for d in dirs:
        (work / "deploy" / "packs" / d).mkdir()
    if manifest is not None:
        (work / "deploy" / "packs" / "manifest.json").write_text(json.dumps(manifest))
    return work


ONE = {"trade_finance": {"version": "v1.0.0", "commit": "a" * 40, "dist": "barista-pack-trade-finance"}}


def test_the_vps_takes_only_the_packs_its_own_instance_file_lists(tmp_path: Path) -> None:
    work = _shipped(tmp_path, ONE, ("trade_finance",))
    assert ops.shipped_packs(work, {"name": "lc", "env": "demo", "packs": {"trade_finance": "v1.0.0"}}) == ONE
    with pytest.raises(ValueError, match="khác cấu hình bản cài demo"):
        ops.shipped_packs(work, {"name": "demo", "packs": {}})                       # bản cài không khai gói ⇒ mã kèm gói bị từ chối
    with pytest.raises(ValueError, match="khác cấu hình"):
        ops.shipped_packs(work, {"name": "lc", "env": "demo", "packs": {"trade_finance": "v1.0.1"}})
    bare = _shipped(tmp_path / "b", None)
    assert ops.shipped_packs(bare, {"name": "demo"}) == {}
    with pytest.raises(ValueError, match="khác cấu hình bản cài lc"):
        ops.shipped_packs(bare, {"name": "lc", "env": "demo", "packs": {"trade_finance": "v1.0.0"}})   # mã lõi trần (vd CI) cho bản cài cần gói
    with pytest.raises(ValueError, match="không khớp manifest"):
        ops.shipped_packs(_shipped(tmp_path / "c", ONE), {"name": "lc", "env": "demo", "packs": {"trade_finance": "v1.0.0"}})
    pin = {"trade_finance": {"version": "v1.0.0", "commit": "a" * 40}}
    assert ops.shipped_packs(work, {"name": "lc", "env": "demo", "packs": pin}) == ONE                        # ghim đúng commit của manifest
    with pytest.raises(ValueError, match="commit đi kèm khác"):
        ops.shipped_packs(work, {"name": "lc", "env": "demo", "packs": {"trade_finance": {"version": "v1.0.0", "commit": "b" * 40}}})


def test_the_api_image_tag_follows_the_pack_set_and_pruning_knows_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tag = ops.api_tag(SHA, ONE)
    assert ops.api_tag(SHA, {}) == SHA and tag.startswith(f"{SHA}-p") and ops.TAG_RE.fullmatch(tag)
    assert ops.api_tag(SHA, {"trade_finance": {**ONE["trade_finance"], "commit": "b" * 40}}) != tag
    listed = f"barista-api:{tag}\nbarista-api:{SHA}-p{'0' * 12}\nbarista-web:{SHA}"
    removed: list[str] = []
    monkeypatch.setattr(ops, "shas_in_use", lambda: set())
    monkeypatch.setattr(ops, "images_in_state", lambda: [])
    monkeypatch.setattr(ops, "command", lambda *a, **k: listed if a[:2] == ("docker", "images") else "")
    monkeypatch.setattr(ops.subprocess, "run", lambda args, **kw: removed.append(args[-1]))
    ops.Deployment.__new__(ops.Deployment).prune(keep={SHA, tag})
    assert removed == [f"barista-api:{SHA}-p{'0' * 12}"]                               # ảnh cũ có gói cũng được dọn, ảnh đang dùng giữ


@pytest.mark.parametrize("on_vps", [True, False])
def test_build_tags_the_api_image_with_the_pack_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on_vps: bool) -> None:
    """Api có gói: dựng trên VPS (``BARISTA_BUILD_ON_VPS=1``) bằng api.Dockerfile, hoặc (mặc định) kéo ảnh lõi của CI rồi dựng LỚP MỎNG
    api-packs.Dockerfile ghép gói lên ảnh lõi — gói riêng tư không lên CI công khai. Web không có gói ⇒ chỉ kéo."""
    if on_vps:
        monkeypatch.setenv("BARISTA_BUILD_ON_VPS", "1")
        monkeypatch.setattr(ops, "_builder", lambda: [])   # máy có buildx (CI) ⇒ không tạo builder thật; builder có trần: test riêng bên dưới
    else:
        monkeypatch.delenv("BARISTA_BUILD_ON_VPS", raising=False)
        monkeypatch.setattr(ops, "_pull", lambda service, sha: f"{ops.REGISTRY}/barista-{service}:{sha}")
    src = tmp_path / "src.tar.gz"
    with tarfile.open(src, "w:gz") as t:
        for name, data in (("deploy/stack.json", (REPO / "deploy/stack.json").read_bytes()),
                           ("deploy/packs/manifest.json", json.dumps(ONE).encode()), ("deploy/packs/trade_finance/pyproject.toml", b"")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    built: list[str] = []
    state: dict[str, str] = {}
    monkeypatch.setattr(ops, "_revision", lambda image: state.get(image))

    files: list[str] = []

    def docker(*args, **kw):
        if args[:2] == ("docker", "tag"):
            state[args[3]] = SHA
        elif "-t" in args:
            tag = args[args.index("-t") + 1]
            built.append(tag)
            files.append(args[args.index("-f") + 1])
            state[tag] = SHA
        return ""
    monkeypatch.setattr(ops, "command", docker)
    inst = {**SAMPLE_INSTANCE, "root": str(tmp_path), "packs": {"trade_finance": {"version": "v1.0.0", "commit": "a" * 40}}}   # bản cài thật: ghim bắt buộc
    _, images = ops.build(SHA, src, inst)
    assert images == {"api": f"barista-api:{ops.api_tag(SHA, ONE)}", "web": f"barista-web:{SHA}"}
    if on_vps:
        assert built == list(images.values()) and files == ["deploy/api.Dockerfile", "deploy/web.Dockerfile"]
    else:
        assert built == [images["api"]] and files == ["deploy/api-packs.Dockerfile"]   # chỉ lớp ghép gói; ảnh lõi + web kéo từ CI
        assert state.get(f"barista-api:{SHA}") == SHA and state.get(f"barista-web:{SHA}") == SHA
    with pytest.raises(ValueError, match="khác cấu hình"):
        ops.build(SHA, src, {**inst, "packs": {}})


def test_the_catalog_lists_every_pack_of_the_repo_from_text_only_and_the_bundle_ships_it(tmp_path: Path) -> None:
    repo = _pack_repo(tmp_path)
    other = repo / "other_pack" / "src" / "barista_pack_other"
    other.mkdir(parents=True)
    # f-string kiểu Python 3.14 (dấu nháy lồng) — máy phát hành cũ không parse nổi cả file ⇒ vẫn đọc được lời gọi Pack(...) / Module(...)
    (other / "__init__.py").write_text(
        "from barista.api import Module, Pack, Role\n"
        'X = f"{ {\'a\': 1}[\'a\'] }"\n'
        'Pack("other_pack", "Gói khác", "Mô tả "\n     "dài", kind="capability", requires=("finance",),\n'
        '     roles={"r": Role("Vai khác", grants=())})\n'
        'm = Module("other", "Phần khác", pack="other_pack")\n'
        'raise SystemExit("không được chạy mã gói")\n')
    (repo / "other_pack" / "pyproject.toml").write_text(
        '[project]\nname = "barista-pack-other"\nversion = "2.1.0"\ndescription = "Tóm tắt gói khác"\n'
        '[tool.barista]\nrequires-core = ">=1.9,<2"\n')
    (repo / "_shared").mkdir()
    (repo / "_shared" / "x.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "gói khác")
    got = {p["key"]: p for p in packs.catalog(repo)["packs"]}
    assert set(got) == {"demo_pack", "other_pack"}                       # _shared không phải gói; gói chưa tag đọc từ HEAD
    assert got["demo_pack"]["version"] == "v1.0.0" and got["demo_pack"]["title"] == "Gói thử"
    o = got["other_pack"]
    assert (o["title"], o["description"], o["kind"], o["version"], o["requires"], o["modules"], o["roles"], o["requires_core"], o["summary"]) == (
        "Gói khác", "Mô tả dài", "capability", "v2.1.0", ["finance"], ["Phần khác"], ["Vai khác"], ">=1.9,<2", "Tóm tắt gói khác")
    core = tmp_path / "core.tar"
    with tarfile.open(core, "w") as t:
        data = b'__version__ = "1.3.0"\n'
        info = tarfile.TarInfo(packs.API)
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    out = tmp_path / "r.tar.gz"
    assert packs.bundle({"name": "x", "env": "demo", "packs": {}}, repo, core, out) == {}   # bản cài KHÔNG gói nào vẫn mang danh mục
    with tarfile.open(out, "r:gz") as t:
        shipped = json.loads(t.extractfile(packs.CATALOG).read())  # type: ignore[union-attr]
    assert {p["key"] for p in shipped["packs"]} == {"demo_pack", "other_pack"} and shipped["format"] == 1
    out2 = tmp_path / "r2.tar.gz"
    packs.bundle({"name": "x", "env": "demo", "packs": {}}, tmp_path / "no-repo", core, out2)   # không có repo gói ⇒ không ghi danh mục
    with tarfile.open(out2, "r:gz") as t:
        assert packs.CATALOG not in t.getnames()


def test_check_stamps_split_core_from_packs_and_web(tmp_path: Path) -> None:
    """deploy/checks.sh ``tree_digest``: dấu LÕI bỏ deploy/packs/ ⇒ cùng lõi, bộ gói khác (bản cài khác) vẫn dùng lại được kết quả test lõi của premerge;
    dấu ĐỦ đổi theo gói; dấu WEB chỉ theo web/. Thư mục sinh ra khi chạy (node_modules, .venv…) không làm đổi dấu."""
    checks = Path(__file__).resolve().parents[2] / "deploy" / "checks.sh"
    for f, text in {"backend/src/a.py": "x = 1\n", "deploy/ops/deploy.py": "#\n", "deploy/packs/edu/p.py": "y = 1\n", "web/app.ts": "1\n"}.items():
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text(text)

    def digests() -> tuple[str, str, str]:
        out = subprocess.run(["bash", "-c", f'. "{checks}"; for p in all core web; do tree_digest "$1" $p; done', "_", str(tmp_path)],
                             capture_output=True, text=True, check=True).stdout.split()
        assert len(out) == 3 and len(set(out)) == 3, out
        return out[0], out[1], out[2]

    full, core, web = digests()
    (tmp_path / "deploy/packs/edu/p.py").write_text("y = 2\n")            # bản cài khác / gói khác
    full2, core2, web2 = digests()
    assert full2 != full and (core2, web2) == (core, web)
    (tmp_path / "web/node_modules").mkdir()
    (tmp_path / "web/node_modules/z.js").write_text("z\n")                  # sinh ra khi cài ⇒ không tính
    (tmp_path / "backend/.venv").mkdir()
    (tmp_path / "backend/.venv/q").write_text("q\n")
    assert digests() == (full2, core2, web2)
    (tmp_path / "web/app.ts").write_text("2\n")
    assert digests()[:2] == (full2, core2) and digests()[2] != web2
    (tmp_path / "backend/src/a.py").write_text("x = 2\n")                  # lõi đổi ⇒ dấu lõi + dấu đủ đổi
    full3, core3, web3 = digests()
    assert full3 != full2 and core3 != core2 and web3 != web2
    # scripts/packkit/ và 3 file docs/ mà test lõi đọc cũng nằm trong dấu lõi + dấu đủ ⇒ sửa chúng vô hiệu cache cũ
    (tmp_path / "scripts/packkit").mkdir(parents=True)
    (tmp_path / "scripts/packkit/packkit.py").write_text("v = 1\n")
    full4, core4, web4 = digests()
    assert full4 != full3 and core4 != core3 and web4 == web3
    (tmp_path / "scripts/packkit/packkit.py").write_text("v = 2\n")
    full5, core5, web5 = digests()
    assert full5 != full4 and core5 != core4 and web5 == web4
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/MODULE-MAP.md").write_text("# map\n")
    full6, core6, web6 = digests()
    assert full6 != full5 and core6 != core5 and web6 == web5
    # test lõi còn đọc mã client (test_client_neutrality, test_web_icons, test_deploy_production) và SKILL.md (test_packkit) ⇒ cũng trong dấu lõi (rà 22/11)
    prev = (full6, core6, web6)
    for f in (".agents/skills/barista-pack/SKILL.md", "macos/Sources/BaristaDesk/A.swift", "ios/Sources/B.swift", "web/src/components/icons.tsx"):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text(f"{f}\n")
        cur = digests()
        assert cur[0] != prev[0] and cur[1] != prev[1], f
        assert (cur[2] != prev[2]) == f.startswith("web/"), f                # dấu web vẫn chỉ theo web/
        prev = cur


def test_tiered_core_and_per_pack_stamps_resume_after_partial_run(tmp_path: Path) -> None:
    """deploy/checks.sh: tách dấu lõi (core-main / core-upgrade) và dấu từng gói (guards / pack-<k>) ⇒ lỗi ở một gói hoặc premerge bỏ qua
    nhóm upgrade không làm mất công chạy lại các pha đã đạt; release.sh (run_upgrade=1) vẫn chạy đủ nhóm upgrade trước khi ghi dấu tổng."""
    checks = Path(__file__).resolve().parents[2] / "deploy" / "checks.sh"
    stamps = tmp_path / "stamps"
    log = tmp_path / "calls.log"
    work = tmp_path / "work"
    (work / "backend" / ".venv" / "bin").mkdir(parents=True)
    (work / "backend" / ".venv" / "bin" / "python").write_text("#!/bin/sh\n")
    (work / "backend" / ".venv" / "bin" / "python").chmod(0o755)
    (work / "backend" / "tests").mkdir(parents=True)
    (work / "backend" / "tests" / "pack_guards.txt").write_text("tests/test_meta.py\n")

    script = f'''
set -euo pipefail
export BARISTA_CHECK_STAMPS="{stamps}"
. "{checks}"
prepare_env() {{ echo "prepare" >> "{log}"; }}
check_core() {{ shift; echo "core:$*" >> "{log}"; }}
uv() {{
  echo "uv:$*" >> "{log}"
  case "$*" in
    *packs/p2/tests*) [ -f "{tmp_path}/fix_p2" ] || return 1 ;;
  esac
}}
check_core_tiered "{work}" "digC" "pm" 0
echo "---" >> "{log}"
check_core_tiered "{work}" "digC" "rel" 1
echo "---" >> "{log}"
check_core_tiered "{work}" "digC" "rel2" 1
echo "---" >> "{log}"
check_core_tiered "{work}" "digFull" "full" 1
echo "===" >> "{log}"
check_packs "{work}" "p1 p2" "digA" "pm" || true
[ -f "{stamps}/guards-digA.ok" ] && [ -f "{stamps}/pack-p1-digA.ok" ] && [ ! -f "{stamps}/pack-p2-digA.ok" ]
echo "---" >> "{log}"
touch "{tmp_path}/fix_p2"
check_packs "{work}" "p1 p2" "digA" "pm2"
echo "---" >> "{log}"
check_packs "{work}" "p1 p2" "digA" "rel"
'''
    subprocess.run(["bash", "-c", script], check=True)
    sections = log.read_text().split("===\n")
    c1, c2, c3, c4 = [s.strip().splitlines() for s in sections[0].split("---\n")]
    assert c1 == ["core:-m not upgrade"]                                            # premerge không upgrade: chỉ chạy lõi chính
    assert c2 == ["prepare", "uv:run --no-sync pytest -q -m upgrade"]               # release.sh: bỏ lõi chính, chỉ chạy bù upgrade
    assert c3 == []                                                                 # lần sau: có dấu tổng core-digC ⇒ 0 việc
    assert c4 == ["core:"]                                                          # chạy đủ từ đầu: gộp 1 lượt pytest (không tách nối tiếp)
    assert (stamps / "core-digC.ok").is_file() and (stamps / "core-digFull.ok").is_file()
    assert (stamps / "core-main-digFull.ok").is_file() and (stamps / "core-upgrade-digFull.ok").is_file()

    p1, p2, p3 = [s.strip().splitlines() for s in sections[1].split("---\n")]
    assert any("test_meta.py" in x for x in p1) and any("packs/p1/tests" in x for x in p1) and any("packs/p2/tests" in x for x in p1)
    # chạy lại sau khi sửa p2: KHÔNG chạy lại guards hay p1, chỉ chạy đúng p2
    assert not any("test_meta.py" in x for x in p2) and not any("packs/p1/tests" in x for x in p2) and any("packs/p2/tests" in x for x in p2)
    assert (stamps / "guards-digA.ok").is_file() and (stamps / "pack-p1-digA.ok").is_file() and (stamps / "pack-p2-digA.ok").is_file()
    assert p3 == []                                                                 # tất cả đã có dấu ⇒ không cài lại gói, 0 việc


def test_images_are_built_in_a_resource_capped_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sự cố 23/11/2026: dựng ảnh bằng daemon mặc định trên VPS dùng chung làm Swarm khởi động lại mọi dịch vụ. Có buildx ⇒ tạo (một lần) builder
    ``docker-container`` có trần bộ nhớ (không swap) + CPU rồi dựng trong đó; không có buildx / không có docker ⇒ dựng như cũ."""
    import subprocess
    made: list[tuple[str, ...]] = []
    monkeypatch.setattr(ops, "command", lambda *a, **k: made.append(a) or "")
    have = {"buildx version": 0, f"buildx inspect {ops.BUILDER}": 1}
    monkeypatch.setattr(subprocess, "run", lambda args, **k: subprocess.CompletedProcess(args, have.get(" ".join(args[1:]), 1)))
    assert ops._builder() == ["buildx", "build", "--builder", ops.BUILDER, "--load"]
    create = made[0]
    assert create[:6] == ("docker", "buildx", "create", "--name", ops.BUILDER, "--driver") and "docker-container" in create
    assert "memory=1536m" in create and "memory-swap=1536m" in create and "cpu-quota=150000" in create
    have["buildx version"] = 1
    assert ops._builder() == []                                                     # không có buildx ⇒ docker build như cũ

    def missing(*a: object, **k: object) -> None:
        raise FileNotFoundError("docker")
    monkeypatch.setattr(subprocess, "run", missing)
    assert ops._builder() == []                                                     # không có docker (máy dev / test)


def test_the_thin_pack_layer_installs_packs_exactly_like_the_full_api_image() -> None:
    """Ảnh CI chỉ có lõi; gói ghép trên VPS bằng api-packs.Dockerfile. Bước chép + cài gói phải GIỐNG HỆT api.Dockerfile (đường dựng trên VPS) —
    không thì cùng commit + cùng bộ gói ra hai ảnh khác nhau tuỳ đường."""
    def pack_steps(name: str) -> list[str]:
        lines = (REPO / "deploy" / name).read_text().splitlines()
        return [x for x in lines if x.startswith(("COPY deploy/packs", "RUN for d in ./packs"))]
    full, thin = pack_steps("api.Dockerfile"), pack_steps("api-packs.Dockerfile")
    assert len(full) == 2 and thin == full
