import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[2]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_check_hashes_finds_triggers_and_trackable_files(tmp_path):
    module = load_script("check-hashes")
    scripts = tmp_path / "scripts"
    configs = tmp_path / "configs"
    chezmoi = tmp_path / ".chezmoiscripts"
    (scripts / "lib").mkdir(parents=True)
    configs.mkdir()
    chezmoi.mkdir()
    (scripts / "used.py").write_text("print('used')")
    (scripts / "check-hashes.py").write_text("")
    (scripts / "lib" / "helper.py").write_text("")
    (configs / "settings.json").write_text("{}")
    (configs / "assets.txt").write_text("ignored")
    (chezmoi / "run_onchange_test.sh.tmpl").write_text(
        '# scripts/used.py: {{ include "scripts/used.py" | sha256sum }}\n'
        '# configs/settings.json: {{ include "configs/settings.json" | sha256sum }}\n'
    )
    module.CHEZMOI_SCRIPTS = chezmoi
    module.SCRIPTS_DIR = scripts
    module.CONFIGS_DIR = configs
    module.REPO_ROOT = tmp_path
    covered, triggers = module.find_hash_triggers()
    trackable = module.find_trackable_files()
    assert covered == {"scripts/used.py", "configs/settings.json"}
    assert triggers["run_onchange_test.sh.tmpl"] == [
        "scripts/used.py",
        "configs/settings.json",
    ]
    assert "scripts/check-hashes.py" not in trackable
    assert "scripts/lib/helper.py" in trackable
    assert "configs/assets.txt" not in trackable


def test_env_coverage_scans_and_classifies_documentation(tmp_path):
    module = load_script("check-env-coverage")
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "configure.py").write_text(
        "os.environ['DOTFILES_RUN_EXAMPLE_SETUP']; GH_TOKEN\n"
    )
    (scan / "check-ignore.py").write_text("DOTFILES_NOT_COUNTED")
    env_example = tmp_path / ".env.example"
    env_example.write_text("# DOTFILES_RUN_EXAMPLE_SETUP=0\nGH_TOKEN=x\n")
    module.SCAN_DIRS = [(scan, {".py"})]
    module.ENV_EXAMPLE = env_example
    assert module.find_referenced_vars() == {"DOTFILES_RUN_EXAMPLE_SETUP"}
    assert "GH_TOKEN" in module.find_referenced_env_names()
    assert module.find_documented_vars() == {"DOTFILES_RUN_EXAMPLE_SETUP"}
    assert module.find_documented_env_vars() == {
        "DOTFILES_RUN_EXAMPLE_SETUP",
        "GH_TOKEN",
    }
    assert module.alias_is_explained(
        ["# GH_TOKEN is the canonical name for GITHUB_TOKEN"],
        "GH_TOKEN",
        "GITHUB_TOKEN",
    )
    assert module.ownership_info({"DOTFILES_RUN_EXAMPLE_SETUP", "OTHER"}) == [
        ("DOTFILES_RUN_EXAMPLE_SETUP", "repo")
    ]


def test_docs_drift_reference_helpers_are_hermetic(tmp_path):
    module = load_script("check-docs-drift")
    document = tmp_path / "guide.md"
    document.write_text(
        "See `scripts/tool.py` and [guide](docs/guide.md#intro). "
        "Ignore [external](https://example.com)."
    )
    assert module.clean_target("<docs/guide.md#intro>") == "docs/guide.md"
    assert module.clean_target("https://example.com") is None
    assert module.looks_like_path("scripts/tool.py")
    assert not module.looks_like_path("a phrase")
    assert module.extract_references(document) == [
        ("scripts/tool.py", "backtick"),
        ("docs/guide.md", "Markdown link"),
    ]
    module.REPO_ROOT = tmp_path
    assert module.resolve_reference("guide.md") == document
    assert module.resolve_reference("../outside.md") is None


def test_migrate_env_renames_and_inherits_gate_values():
    module = load_script("migrate-env-gates")
    lines = [
        "DOTFILES_RUN_PI=1\n",
        "DOTFILES_RUN_MCP_SETUP='0'\n",
        "DOTFILES_OPENCODE_TIER='openai' # keep comment\n",
        "SMALLCODE_ENABLED=1\n",
    ]
    migrated, changes = module.migrate_env(lines)
    text = "".join(migrated)
    assert "DOTFILES_RUN_PI_SETUP=1" in text
    assert "DOTFILES_RUN_MOZART_SETUP='0'  # inherited" in text
    assert "DOTFILES_OPENCODE_TIER='omo-slim-openai' # keep comment" in text
    assert "SMALLCODE_ENABLED" not in text
    assert any(
        "DOTFILES_RUN_PI → DOTFILES_RUN_PI_SETUP" in change for change in changes
    )
    assert any("inherited from DOTFILES_RUN_MCP_SETUP" in change for change in changes)


def test_verify_ci_assets_reports_missing_and_valid_assets(tmp_path, capsys):
    module = load_script("verify-ci-assets")
    import sys

    original_argv = sys.argv
    sys.argv = ["verify-ci-assets.py"]
    asset = tmp_path / "asset.txt"
    asset.write_text("good")
    import hashlib

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"assets": {"asset.txt": "' + hashlib.sha256(b"good").hexdigest() + '"}}'
    )
    module.ROOT = tmp_path
    module.MANIFEST = manifest
    try:
        assert module.main() == 0
        assert "verified (1 assets)" in capsys.readouterr().out
        asset.write_text("changed")
        assert module.main() == 1
        assert "hash drift" in capsys.readouterr().out
    finally:
        sys.argv = original_argv


def test_brewfile_completeness_maps_categories_and_rejects_missing_files(
    tmp_path, monkeypatch
):
    module = load_script("verify-brewfile-completeness")
    assert module.category_to_brewfile("dev_cli") == "Brewfile"
    assert module.category_to_brewfile("desktop_gaming") == "Brewfile.desktop.gaming"
    repo = tmp_path / "repo"
    (repo / ".chezmoidata").mkdir(parents=True)
    (repo / ".chezmoidata" / "categories.yaml").write_text(
        "categories:\n  dev_cli:\n  desktop_gaming:\n"
    )
    (repo / "Brewfile").write_text('brew "jq"\n')
    module.SCRIPT_DIR = str(repo / "scripts")
    monkeypatch.setattr(
        module.sys, "exit", lambda code: (_ for _ in ()).throw(SystemExit(code))
    )
    try:
        module.main()
    except SystemExit as error:
        assert error.code == 1
