"""scripts/provision_trial.py: names a trial's resources and renders its three configs."""

import importlib.util
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.guard]

ROOT = Path(__file__).resolve().parents[1]
APP = "wrangler.toml"
PROMOTE = "workers/promote/wrangler.toml"
RSS = "workers/rss/wrangler.toml"
OUT_APP = "wrangler.trial-alice.toml"
OUT_PROMOTE = "workers/promote/wrangler.trial-alice.toml"
OUT_RSS = "workers/rss/wrangler.trial-alice.toml"
OUTPUTS = [OUT_APP, OUT_PROMOTE, OUT_RSS]
ACCT = "0123456789abcdef0123456789abcdef"
DIGEST = "sha256:" + "a" * 64
KV = "fedcba9876543210fedcba9876543210"
D1 = "11111111-2222-4333-8444-555555555555"
SUFFIX = "0123456789abcdef"
RENDER = [
    "render",
    "--slug",
    "alice",
    "--domain",
    "example.com",
    "--account-id",
    ACCT,
    "--image-digest",
    DIGEST,
    "--kv-id",
    KV,
    "--d1-id",
    D1,
]


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    t = tmp_path / "tree"
    for rel in [
        "scripts/provision_trial.py",
        "scripts/derive-wrangler-config.sh",
        APP,
        PROMOTE,
        RSS,
    ]:
        (t / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, t / rel)
    (t / "scripts/derive-wrangler-config.sh").chmod(0o755)
    return t


def run(tree: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(tree / "scripts/provision_trial.py"), *args],
        capture_output=True,
        text=True,
        cwd=cwd or tree,
    )


def render(tree: Path, **override: str) -> subprocess.CompletedProcess:
    args = list(RENDER)
    for flag, value in override.items():
        args[args.index("--" + flag.replace("_", "-")) + 1] = value
    return run(tree, *args)


def listing(tree: Path) -> dict[str, bytes]:
    return {str(p.relative_to(tree)): p.read_bytes() for p in tree.rglob("*") if p.is_file()}


def lines_minus(text: str, drop: set[str]) -> list[str]:
    return [line for line in text.split("\n") if line not in drop]


def prod() -> dict:
    app, promote, rss = _toml(ROOT / APP), _toml(ROOT / PROMOTE), _toml(ROOT / RSS)
    return {
        "app": app,
        "promote": promote,
        "rss": rss,
    }


# --- names


def test_names_prints_seven_slugged_lines(tree: Path) -> None:
    before = listing(tree)
    done = run(tree, "names", "--slug", "alice", "--pages-suffix", SUFFIX)

    assert done.returncode == 0
    assert done.stdout == (
        "TRIAL_APP_WORKER=cyris-app-alice\n"
        "TRIAL_CONTAINER=cyris-app-cyriscontainer-alice\n"
        "TRIAL_PROMOTE_WORKER=cyris-promote-alice\n"
        "TRIAL_KV_TITLE=cyris-promote-alice\n"
        "TRIAL_D1_NAME=cyris-app-alice\n"
        f"TRIAL_PAGES_PROJECT=cyris-app-alice-{SUFFIX}\n"
        "TRIAL_RSS_WORKER=cyris-rss-alice\n"
    )
    assert listing(tree) == before
    again = run(tree, "names", "--slug", "alice", "--pages-suffix", SUFFIX)
    assert again.stdout == done.stdout
    assert all(re.fullmatch(r"TRIAL_[A-Z0-9_]+=[a-z0-9-]+", x) for x in done.stdout.split())


def test_no_trial_name_equals_a_production_name(tree: Path) -> None:
    p = prod()
    taken = {
        p["app"]["name"],
        p["app"]["containers"][0]["name"],
        p["promote"]["name"],
        p["rss"]["name"],
        p["rss"]["d1_databases"][0]["database_name"],
        "cyris-digest",
    }
    done = run(tree, "names", "--slug", "alice", "--pages-suffix", SUFFIX)
    values = [line.split("=", 1)[1] for line in done.stdout.splitlines()]

    assert all("alice" in v for v in values)
    assert not taken & set(values)


# --- validation


@pytest.mark.parametrize("slug", ["Alice", "a", "al-ice", "a" * 21])
def test_bad_slug_exits_2(tree: Path, slug: str) -> None:
    done = run(tree, "names", "--slug", slug, "--pages-suffix", SUFFIX)

    assert done.returncode == 2
    assert "--slug" in done.stderr
    assert done.stdout == ""


@pytest.mark.parametrize("suffix", ["abc", "0123456789ABCDEF"])
def test_bad_pages_suffix_exits_2(tree: Path, suffix: str) -> None:
    done = run(tree, "names", "--slug", "alice", "--pages-suffix", suffix)

    assert done.returncode == 2
    assert "--pages-suffix" in done.stderr


@pytest.mark.parametrize(
    "domain", ["https://example.com", "*.example.com", "example.com/x", "localhost"]
)
def test_bad_domain_exits_2_and_writes_nothing(tree: Path, domain: str) -> None:
    before = listing(tree)
    done = render(tree, domain=domain)

    assert done.returncode == 2
    assert "--domain" in done.stderr
    assert done.stdout == ""
    assert listing(tree) == before


@pytest.mark.parametrize(
    "flag,value", [("account_id", "acct"), ("kv_id", KV[:31]), ("account_id", ACCT.upper())]
)
def test_bad_ids_exit_2(tree: Path, flag: str, value: str) -> None:
    done = render(tree, **{flag: value})

    assert done.returncode == 2
    assert "--" + flag.replace("_", "-") in done.stderr
    assert not (tree / OUT_APP).exists()


def test_a_bad_second_render_leaves_the_first_outputs(tree: Path) -> None:
    assert render(tree).returncode == 0
    before = listing(tree)

    assert render(tree, domain="localhost").returncode == 2
    assert listing(tree) == before


@pytest.mark.parametrize(
    "d1",
    [
        "11111111222243338444555555555555",
        "11111111-2222-4333-8444-55555555555g",
        "11111111-2222-4333-8444-5555555555AA",
    ],
)
def test_bad_d1_id_exits_2(tree: Path, d1: str) -> None:
    done = render(tree, d1_id=d1)

    assert done.returncode == 2
    assert "--d1-id" in done.stderr
    assert not any((tree / o).exists() for o in OUTPUTS)


@pytest.mark.parametrize(
    "digest", ["release", "sha256:" + "a" * 63, "registry.cloudflare.com/x/y@sha256:" + "a" * 64]
)
def test_only_a_sha256_digest_is_accepted(tree: Path, digest: str) -> None:
    done = render(tree, image_digest=digest)

    assert done.returncode == 2
    assert "--image-digest" in done.stderr
    assert not any((tree / o).exists() for o in OUTPUTS)


# --- app config


def test_app_image_is_pinned_by_digest(tree: Path) -> None:
    render(tree)
    image = _toml(tree / OUT_APP)["containers"][0]["image"]

    assert image == f"registry.cloudflare.com/{ACCT}/cyris-app-cyriscontainer@{DIGEST}"


def test_app_is_renamed_private_and_routed(tree: Path) -> None:
    assert render(tree).returncode == 0
    app = _toml(tree / OUT_APP)

    assert app["name"] == "cyris-app-alice"
    assert app["vars"]["CYRIS_APP_WORKER_NAME"] == app["name"]
    assert app["vars"]["CYRIS_PRIVATE_ARCHIVE"] == "true"
    assert isinstance(app["vars"]["CYRIS_PRIVATE_ARCHIVE"], str)
    assert set(app["vars"]) == {"CYRIS_APP_WORKER_NAME", "CYRIS_PRIVATE_ARCHIVE"}
    assert app["containers"][0]["name"] == "cyris-app-cyriscontainer-alice"
    assert app["routes"] == [{"pattern": "alice.example.com", "custom_domain": True}]
    assert app["durable_objects"]["bindings"] == [{"class_name": "CyrisContainer", "name": "CYRIS"}]
    assert app["main"] == "workers/app/src/index.js"
    assert app["workers_dev"] is True
    assert app["preview_urls"] is False


def test_multi_label_domain_is_used_whole(tree: Path) -> None:
    render(tree, domain="trials.example.co.uk")

    assert _toml(tree / OUT_APP)["routes"][0]["pattern"] == "alice.trials.example.co.uk"


def test_app_differs_from_template_only_on_the_renamed_lines(tree: Path) -> None:
    render(tree)
    got = (tree / OUT_APP).read_text(encoding="utf-8")
    tracked = (ROOT / APP).read_text(encoding="utf-8")
    added = {
        'name = "cyris-app-alice"',
        'CYRIS_APP_WORKER_NAME = "cyris-app-alice"',
        'CYRIS_PRIVATE_ARCHIVE = "true"',
        'name = "cyris-app-cyriscontainer-alice"',
        f'image = "registry.cloudflare.com/{ACCT}/cyris-app-cyriscontainer@{DIGEST}"',
        "[[routes]]",
        'pattern = "alice.example.com"',
        "custom_domain = true",
    }
    dropped = {
        'name = "cyris-app"',
        'CYRIS_APP_WORKER_NAME = "cyris-app"',
        'name = "cyris-app-cyriscontainer"',
        'image = "./Dockerfile"',
    }
    got_lines = lines_minus(got, added)
    tracked_lines = lines_minus(tracked, dropped)

    assert got_lines == [*tracked_lines[:-1], "", ""]


def test_tracked_wrangler_toml_stays_fork_neutral() -> None:
    tracked = _toml(ROOT / APP)

    assert "CYRIS_PRIVATE_ARCHIVE" not in tracked["vars"]
    assert "routes" not in tracked


def test_render_is_deterministic_and_leaves_templates_alone(tree: Path, tmp_path: Path) -> None:
    templates = {
        rel: (tree / rel).read_bytes()
        for rel in [APP, PROMOTE, RSS, "scripts/derive-wrangler-config.sh"]
    }
    before = set(listing(tree))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    first = run(tree, *RENDER, cwd=elsewhere)
    snapshot = {o: (tree / o).read_bytes() for o in OUTPUTS}
    second = run(tree, *RENDER, cwd=elsewhere)

    assert first.stdout == "\n".join(OUTPUTS) + "\n"
    assert second.stdout == first.stdout
    assert {o: (tree / o).read_bytes() for o in OUTPUTS} == snapshot
    assert {rel: (tree / rel).read_bytes() for rel in templates} == templates
    assert set(listing(tree)) == before | set(OUTPUTS)
    assert list(elsewhere.iterdir()) == []


# --- promote and rss


def test_promote_differs_only_on_name_and_kv(tree: Path) -> None:
    render(tree)
    got = (tree / OUT_PROMOTE).read_text(encoding="utf-8").split("\n")
    tracked = (ROOT / PROMOTE).read_text(encoding="utf-8").split("\n")
    diff = [(a, b) for a, b in zip(tracked, got, strict=True) if a != b]

    assert len(got) == len(tracked)
    assert [b for _, b in diff] == [
        'name = "cyris-promote-alice"',
        f'  {{ binding = "PROMOTIONS", id = "{KV}" }}',
    ]
    promote = _toml(tree / OUT_PROMOTE)
    assert promote["kv_namespaces"] == [{"binding": "PROMOTIONS", "id": KV}]
    assert "3d0c23d0e8434434b5fb2d105ec145b6" not in "".join(got)


def test_rss_is_renamed_and_bound_to_the_trial_database(tree: Path) -> None:
    render(tree)
    text = (tree / OUT_RSS).read_text(encoding="utf-8")
    rss = tomllib.loads(text)
    tracked_text = (ROOT / RSS).read_text(encoding="utf-8")
    tracked = tomllib.loads(tracked_text)
    old_id = tracked["d1_databases"][0]["database_id"]
    drop_new = {
        'name = "cyris-rss-alice"',
        'database_name = "cyris-app-alice"',
        f'database_id = "{D1}"',
    }
    drop_old = {
        'name = "cyris-rss"',
        f'database_name = "{tracked["d1_databases"][0]["database_name"]}"',
        f'database_id = "{old_id}"',
    }

    assert rss["name"] == "cyris-rss-alice"
    for key in ("main", "compatibility_date", "triggers", "limits"):
        assert rss[key] == tracked[key]
    assert rss["d1_databases"] == [
        {"binding": "DB", "database_name": "cyris-app-alice", "database_id": D1}
    ]
    assert lines_minus(text, drop_new) == lines_minus(tracked_text, drop_old)
    assert "vars" not in rss
    assert "RSS_TOKEN" not in text
    assert 'name = "cyris-rss"' not in text.split("\n")
    assert old_id not in text
    names = run(tree, "names", "--slug", "alice", "--pages-suffix", SUFFIX).stdout
    assert f"TRIAL_D1_NAME={rss['d1_databases'][0]['database_name']}\n" in names


def test_the_templates_own_database_id_is_refused(tree: Path) -> None:
    old_id = _toml(ROOT / RSS)["d1_databases"][0]["database_id"]
    done = render(tree, d1_id=old_id)

    assert done.returncode == 2
    assert "--d1-id" in done.stderr
    assert not any((tree / o).exists() for o in OUTPUTS)


# --- satellites only

SATELLITES = ["render-satellites", "--slug", "alice", "--kv-id", KV, "--d1-id", D1]


def _provision(tree: Path):
    spec = importlib.util.spec_from_file_location(
        "provision_trial", tree / "scripts/provision_trial.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mark_satellites(tree: Path) -> None:
    (tree / ".env.trial-alice-wizard").write_text(
        f"SATELLITES_ONLY=yes\nTRIAL_KV_ID={KV}\nTRIAL_D1_ID={D1}\n", encoding="utf-8"
    )


def test_satellites_render_the_vote_and_rss_configs_and_no_app(tree: Path) -> None:
    done = run(tree, *SATELLITES)

    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == [OUT_PROMOTE, OUT_RSS]
    assert not (tree / OUT_APP).exists()
    satellites = {rel: (tree / rel).read_bytes() for rel in (OUT_PROMOTE, OUT_RSS)}
    (tree / OUT_PROMOTE).unlink()
    (tree / OUT_RSS).unlink()
    render(tree)
    assert {rel: (tree / rel).read_bytes() for rel in (OUT_PROMOTE, OUT_RSS)} == satellites


def test_satellites_need_no_domain_account_or_image(tree: Path) -> None:
    done = run(tree, *SATELLITES, "--domain", "example.com")

    assert done.returncode == 2
    assert "--domain" in done.stderr
    _none_written(tree)


def test_satellites_refuse_the_templates_own_database_id(tree: Path) -> None:
    old_id = _toml(ROOT / RSS)["d1_databases"][0]["database_id"]
    args = list(SATELLITES)
    args[args.index("--d1-id") + 1] = old_id

    done = run(tree, *args)

    assert done.returncode == 2
    _none_written(tree)


def test_satellites_refuse_a_slug_already_rendered_as_a_full_trial(tree: Path) -> None:
    render(tree)
    before = listing(tree)

    done = run(tree, *SATELLITES)

    assert done.returncode == 1
    assert OUT_APP in done.stderr
    assert listing(tree) == before


def test_a_satellites_slug_loads_without_an_app_config(tree: Path) -> None:
    assert run(tree, *SATELLITES).returncode == 0
    _mark_satellites(tree)

    configs = _provision(tree).load_rendered("alice")

    assert set(configs) == {"promote", "rss"}
    assert configs["rss"]["d1_databases"][0]["database_id"] == D1
    assert configs["promote"]["kv_namespaces"][0]["id"] == KV


def test_a_satellites_slug_is_refused_by_the_full_render(tree: Path) -> None:
    _mark_satellites(tree)

    done = render(tree)

    assert done.returncode == 1
    assert "render-satellites" in done.stderr
    _none_written(tree)


def test_a_satellites_slug_missing_a_config_is_told_to_render_satellites(tree: Path) -> None:
    _mark_satellites(tree)
    provision = _provision(tree)

    with pytest.raises(provision.ProductionNameError) as refused:
        provision.load_rendered("alice")

    assert str(refused.value).startswith(f"{OUT_PROMOTE} not found")
    assert "`provision_trial.py render-satellites` first" in str(refused.value)


def test_a_full_trial_without_its_app_config_is_still_refused(tree: Path) -> None:
    assert run(tree, *SATELLITES).returncode == 0
    provision = _provision(tree)

    with pytest.raises(provision.ProductionNameError, match=re.escape(f"{OUT_APP} not found")) as e:
        provision.load_rendered("alice")
    assert "`provision_trial.py render` first" in str(e.value)


def test_a_satellites_config_naming_production_is_refused(tree: Path) -> None:
    assert run(tree, *SATELLITES).returncode == 0
    _mark_satellites(tree)
    text = (tree / OUT_RSS).read_text(encoding="utf-8")
    (tree / OUT_RSS).write_text(text.replace('name = "cyris-rss-alice"', 'name = "cyris-rss"'))
    provision = _provision(tree)

    with pytest.raises(provision.ProductionNameError, match="a production name"):
        provision.load_rendered("alice")


# --- drifted templates


def _edit(tree: Path, rel: str, old: str, new: str) -> None:
    text = (tree / rel).read_text(encoding="utf-8")
    assert old in text
    (tree / rel).write_text(text.replace(old, new), encoding="utf-8")


def _none_written(tree: Path) -> None:
    assert not any((tree / o).exists() for o in OUTPUTS)


def test_missing_vars_line_exits_1(tree: Path) -> None:
    _edit(tree, APP, 'CYRIS_APP_WORKER_NAME = "cyris-app"\n', "")
    done = render(tree)

    assert done.returncode == 1
    assert "CYRIS_APP_WORKER_NAME" in done.stderr
    _none_written(tree)


def test_commented_image_anchor_fails(tree: Path) -> None:
    _edit(tree, APP, 'image = "./Dockerfile"', 'image = "./Dockerfile" # x')
    done = render(tree)

    assert done.returncode != 0
    _none_written(tree)


def test_split_kv_line_writes_no_output(tree: Path) -> None:
    _edit(
        tree,
        PROMOTE,
        '{ binding = "PROMOTIONS", id = "3d0c23d0e8434434b5fb2d105ec145b6" }',
        '{ binding = "PROMOTIONS",\n    id = "3d0c23d0e8434434b5fb2d105ec145b6" }',
    )
    done = render(tree)

    assert done.returncode == 1
    _none_written(tree)


def test_vars_line_moved_out_of_vars_fails_the_self_check(tree: Path) -> None:
    _edit(tree, APP, '[vars]\nCYRIS_APP_WORKER_NAME = "cyris-app"\n', "")
    _edit(
        tree,
        APP,
        'crons = ["0 * * * *"]',
        'crons = ["0 * * * *"]\nCYRIS_APP_WORKER_NAME = "cyris-app"',
    )
    done = render(tree)

    assert done.returncode == 1
    _none_written(tree)


def test_second_d1_table_names_the_rss_template(tree: Path) -> None:
    text = (tree / RSS).read_text(encoding="utf-8")
    (tree / RSS).write_text(
        text + '\n[[d1_databases]]\nbinding = "X"\ndatabase_name = "x"\ndatabase_id = "y"\n',
        encoding="utf-8",
    )
    done = render(tree)

    assert done.returncode == 1
    assert RSS in done.stderr
    _none_written(tree)


def test_commented_database_name_exits_1(tree: Path) -> None:
    _edit(tree, RSS, 'database_name = "cyris-rss"', 'database_name = "cyris-rss" # x')
    done = render(tree)

    assert done.returncode == 1
    assert "database_name" in done.stderr
    _none_written(tree)


def _drop_section(tree: Path, rel: str, start: str, end: str) -> None:
    text = (tree / rel).read_text(encoding="utf-8")
    cut = re.sub(rf"^{re.escape(start)}$.*?^{end}.*?$\n", "", text, flags=re.M | re.S)
    assert cut != text
    (tree / rel).write_text(cut, encoding="utf-8")


def test_zero_d1_tables_name_the_rss_template(tree: Path) -> None:
    _drop_section(tree, RSS, "[[d1_databases]]", "database_id = ")
    done = render(tree)

    assert done.returncode == 1
    assert RSS in done.stderr
    assert "d1_databases" in done.stderr
    assert "Traceback" not in done.stderr
    _none_written(tree)


def test_zero_container_tables_name_the_app_template(tree: Path) -> None:
    _drop_section(tree, APP, "[[containers]]", "regions = ")
    done = render(tree)

    assert done.returncode == 1
    assert APP in done.stderr
    assert "containers" in done.stderr
    assert "Traceback" not in done.stderr
    _none_written(tree)


def _drift_exits_1_naming(tree: Path, rel: str, *needles: str) -> None:
    done = render(tree)

    assert done.returncode == 1
    assert rel in done.stderr
    assert all(n in done.stderr for n in needles)
    assert "Traceback" not in done.stderr
    _none_written(tree)


def _remove_lines(tree: Path, rel: str, pattern: str) -> None:
    text = (tree / rel).read_text(encoding="utf-8")
    cut = re.sub(pattern, "", text, flags=re.M)
    assert cut != text
    (tree / rel).write_text(cut, encoding="utf-8")


def test_container_without_a_name_line_names_the_app_template(tree: Path) -> None:
    text = (tree / APP).read_text(encoding="utf-8")
    head, tail = text.split("[[containers]]\n", 1)
    (tree / APP).write_text(
        head + "[[containers]]\n" + re.sub(r"^name = .*\n", "", tail, count=1, flags=re.M),
        encoding="utf-8",
    )
    _drift_exits_1_naming(tree, APP, "name")


def test_rss_without_database_name_names_the_template(tree: Path) -> None:
    _remove_lines(tree, RSS, r"^database_name = .*\n")
    _drift_exits_1_naming(tree, RSS, "database_name")


def test_rss_without_database_id_names_the_template(tree: Path) -> None:
    _remove_lines(tree, RSS, r"^database_id = .*\n")
    _drift_exits_1_naming(tree, RSS, "database_id")


def test_promote_without_its_kv_entry_names_the_template(tree: Path) -> None:
    _remove_lines(tree, PROMOTE, r"^.*binding = \"PROMOTIONS\".*\n")
    _drift_exits_1_naming(tree, PROMOTE, "kv_namespaces")


def test_duplicated_root_name_names_the_app_template(tree: Path) -> None:
    text = (tree / APP).read_text(encoding="utf-8")
    (tree / APP).write_text(text.replace('name = "cyris-app"\n', 'name = "cyris-app"\n' * 2, 1))
    _drift_exits_1_naming(tree, APP, "name")


def test_missing_template_exits_1_naming_it(tree: Path) -> None:
    (tree / RSS).unlink()
    done = run(tree, "names", "--slug", "alice", "--pages-suffix", SUFFIX)

    assert done.returncode == 1
    assert RSS in done.stderr


# --- gitignore


def _ignored(repo: Path, rel: str) -> int:
    return subprocess.run(["git", "check-ignore", "-q", rel], cwd=repo).returncode


def test_generated_configs_are_ignored_one_call_per_path() -> None:
    assert [_ignored(ROOT, p) for p in OUTPUTS] == [0, 0, 0]


def _repo_with_gitignore(tmp_path: Path, replacement: str | None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    text = (
        (ROOT / ".gitignore")
        .read_text(encoding="utf-8")
        .replace("wrangler.trial-*.toml\n", "" if replacement is None else replacement + "\n")
    )
    (repo / ".gitignore").write_text(text, encoding="utf-8")
    return repo


def test_without_the_pattern_every_path_is_unignored(tmp_path: Path) -> None:
    repo = _repo_with_gitignore(tmp_path, None)

    assert [_ignored(repo, p) for p in OUTPUTS] == [1, 1, 1]


def test_a_root_only_pattern_misses_the_worker_directories(tmp_path: Path) -> None:
    repo = _repo_with_gitignore(tmp_path, "/wrangler.trial-*.toml")

    assert [_ignored(repo, p) for p in OUTPUTS] == [0, 1, 1]
