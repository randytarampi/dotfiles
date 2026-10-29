"""Tests for Codex configuration reconciliation."""

import importlib.util
import os
import shutil
import subprocess
import sys

try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[2]
SCRIPT = REPO_ROOT / "scripts" / "configure-codex.py"
CONFIGURE_ALL = REPO_ROOT / "scripts" / "configure-all.sh"
SPEC = importlib.util.spec_from_file_location("configure_codex", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
configure_codex = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure_codex)


@pytest.mark.parametrize(
    "root_setting",
    ["", 'model_provider = "openai"\n'],
)
def test_summary_does_not_treat_profile_provider_as_root_default(root_setting):
    config = (
        f'{root_setting}\n[model_providers.litellm]\nname = "Gateway"\n'
        '\n[profiles.litellm]\nmodel = "openai/gpt-6-luna"\n'
        'model_provider = "litellm"\n'
    )
    summary = "\n".join(
        configure_codex.build_summary_lines(config, "config.toml", "m", "note", "")
    )
    assert "opt-in profile: litellm" in summary
    assert "not the root default" in summary
    assert "runtime profile selection: unknown" in summary
    assert "root provider: litellm" not in summary


def test_summary_reports_litellm_root_default_without_inactive_warning():
    config = 'model_provider = "litellm"\n[model_providers.litellm]\nname = "Gateway"\n'
    summary = "\n".join(
        configure_codex.build_summary_lines(config, "config.toml", "m", "note", "")
    )
    assert "not the root default" not in summary
    assert "root provider: litellm" in summary


def test_summary_rejects_invalid_toml():
    config = "[broken\n"
    with pytest.raises(configure_codex.tomllib.TOMLDecodeError):
        configure_codex.build_summary_lines(config, "config.toml", "m", "note", "")


@pytest.mark.parametrize("proxy_gate", ["0", "1"])
def test_litellm_provider_requires_both_gates(monkeypatch, proxy_gate):
    monkeypatch.setenv("DOTFILES_RUN_CODEX_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", proxy_gate)
    assert bool(configure_codex.build_litellm_provider_config()) == (proxy_gate == "1")


def test_litellm_provider_requires_setup_gate(monkeypatch):
    monkeypatch.delenv("DOTFILES_RUN_CODEX_SETUP", raising=False)
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    assert configure_codex.build_litellm_provider_config() == ""


def test_litellm_profile_is_opt_in_and_selects_gateway_model(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_CODEX_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    profiles = tomllib.loads(configure_codex.build_profiles_config("model"))["profiles"]
    assert profiles["litellm"] == {
        "model": "openai/gpt-6-luna",
        "model_provider": "litellm",
    }

    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    profiles = tomllib.loads(configure_codex.build_profiles_config("model"))["profiles"]
    assert "litellm" not in profiles


@pytest.mark.parametrize("shell_mode", ["login", "noninteractive"])
@pytest.mark.parametrize(
    "gates",
    [
        ("0", "0", "0"),
        ("1", "0", "0"),
        ("0", "1", "0"),
        ("0", "0", "1"),
        ("1", "1", "0"),
        ("1", "0", "1"),
        ("0", "1", "1"),
        ("1", "1", "1"),
    ],
)
def test_managed_shell_exports_codex_key_only_with_all_gates(
    tmp_path, gates, shell_mode
):
    home = tmp_path / "home"
    key_path = home / ".local/share/litellm/clients/codex.key"
    key_path.parent.mkdir(parents=True)
    key_path.write_text("synthetic-codex-key")
    script = REPO_ROOT / "dot_dotfiles/shell/variables.sh"
    command = (
        f'source "{script}"; bash -c \'printf "%s" "${{LITELLM_CODEX_KEY-unset}}"\''
    )
    args = ["bash"]
    if shell_mode == "login":
        args.append("--login")
    args.extend(["-c", command])
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "DOTFILES_RUN_CODEX_SETUP": gates[0],
        "DOTFILES_USE_LITELLM_PROXY": gates[1],
        "DOTFILES_RUN_LITELLM_SETUP": gates[2],
    }
    result = subprocess.run(args, env=env, capture_output=True, text=True, check=True)
    assert result.stdout == (
        "synthetic-codex-key" if gates == ("1", "1", "1") else "unset"
    )
    assert result.stderr == ""


@pytest.mark.parametrize("shell_mode", ["login", "noninteractive"])
@pytest.mark.parametrize("enabled", [True, False])
def test_managed_shell_preserves_inherited_codex_key(tmp_path, shell_mode, enabled):
    home = tmp_path / "home"
    home.mkdir()
    gates = ("1", "1", "1") if enabled else ("1", "0", "1")
    script = REPO_ROOT / "dot_dotfiles/shell/variables.sh"
    command = (
        f'source "{script}"; bash -c \'printf "%s" "${{LITELLM_CODEX_KEY-unset}}"\''
    )
    args = ["bash"]
    if shell_mode == "login":
        args.append("--login")
    args.extend(["-c", command])
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "LITELLM_CODEX_KEY": "manual-inherited-key",
        "DOTFILES_RUN_CODEX_SETUP": gates[0],
        "DOTFILES_USE_LITELLM_PROXY": gates[1],
        "DOTFILES_RUN_LITELLM_SETUP": gates[2],
    }
    result = subprocess.run(args, env=env, capture_output=True, text=True, check=True)
    assert result.stdout == "manual-inherited-key"
    assert result.stderr == ""


def test_reconcile_managed_block_repeatably_and_gate_off_preserves_user_toml(
    monkeypatch,
):
    monkeypatch.setenv("DOTFILES_RUN_CODEX_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("LITELLM_PORT", "4567")
    original = (
        '# User config\nmodel_provider = "openai"\n[profiles.mine]\nmodel = "x"\n'
    )
    enabled = configure_codex.reconcile_litellm_provider(original, True)
    assert configure_codex.reconcile_litellm_provider(enabled, True) == enabled
    parsed = tomllib.loads(enabled)
    assert parsed["model_provider"] == "openai"
    assert (
        parsed["model_providers"]["litellm"]["base_url"] == "http://127.0.0.1:4567/v1"
    )
    disabled = configure_codex.reconcile_litellm_provider(enabled, False)
    assert tomllib.loads(disabled)["profiles"]["mine"]["model"] == "x"
    assert "model_providers" not in tomllib.loads(disabled)


@pytest.mark.parametrize(
    "content",
    [
        '[model_providers.litellm]\nname = "User provider"\n',
        "# BEGIN DOTFILES MANAGED PROVIDER litellm\n[model_providers.litellm]\n",
        "# BEGIN DOTFILES MANAGED PROVIDER litellm\n[model_providers.other]\n# END DOTFILES MANAGED PROVIDER litellm\n",
    ],
)
def test_reconcile_refuses_unmanaged_or_ambiguous_content_unchanged(content):
    before = content.encode()
    with pytest.raises(ValueError):
        configure_codex.reconcile_litellm_provider(content, True)
    assert content.encode() == before


def test_main_writes_valid_toml_twice_and_gate_off_removes_owned_block(
    tmp_path, monkeypatch
):
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    original = (
        '# User setting\nmodel_provider = "openai"\n[profiles.mine]\nmodel = "keep"\n'
    )
    config_path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_RUN_CODEX_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setattr(
        configure_codex, "resolve_ollama_cloud_model", lambda: ("m", "note")
    )
    monkeypatch.setattr(configure_codex, "resolve_local_model", lambda: "")
    monkeypatch.setattr(configure_codex, "load_env", lambda: None)
    monkeypatch.setattr(configure_codex, "logger", MagicMock())
    snapshots = []
    for _ in range(2):
        with patch.object(sys, "argv", ["configure-codex.py"]):
            configure_codex.main()
        persisted = config_path.read_text(encoding="utf-8")
        tomllib.loads(persisted)
        snapshots.append(persisted)
    assert snapshots[0] == snapshots[1]
    assert persisted
    assert persisted.count("BEGIN DOTFILES MANAGED PROVIDER litellm") == 1
    stat = config_path.stat()
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    with patch.object(sys, "argv", ["configure-codex.py"]):
        configure_codex.main()
    assert "model_providers.litellm" not in config_path.read_text(encoding="utf-8")
    assert stat.st_ino == config_path.stat().st_ino


@pytest.mark.parametrize(
    "original",
    [
        '[model_providers.litellm]\nname = "user"\n',
        '[model_providers.meridian]\nname = "user"\n',
        '[profiles.local]\nmodel = "user"\n',
    ],
)
def test_main_collision_does_not_change_bytes_or_stat(tmp_path, monkeypatch, original):
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text(original, encoding="utf-8")
    before = config_path.stat()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_RUN_CODEX_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setattr(
        configure_codex, "resolve_ollama_cloud_model", lambda: ("m", "note")
    )
    monkeypatch.setattr(configure_codex, "resolve_local_model", lambda: "")
    monkeypatch.setattr(configure_codex, "load_env", lambda: None)
    monkeypatch.setattr(configure_codex, "logger", MagicMock())
    with patch.object(sys, "argv", ["configure-codex.py"]):
        with pytest.raises(SystemExit, match="1"):
            configure_codex.main()
    assert config_path.read_text(encoding="utf-8") == original
    assert config_path.stat().st_mtime_ns == before.st_mtime_ns
    assert config_path.stat().st_ino == before.st_ino


def test_pinned_pre_owned_meridian_fixture_requires_manual_review_without_writes(
    tmp_path, monkeypatch
):
    # Provider excerpt from ac37ec0e; an unmarked profile is also deliberately
    # included as ambiguous user state, not claimed as historical output.
    original = (
        '[model_providers.meridian]\nname = "Meridian"\n'
        'base_url = "http://127.0.0.1:3456/v1"\nwire_api = "responses"\n'
        'requires_openai_auth = true\nenv_key = "MERIDIAN_API_KEY"\n'
        '\n[profiles.meridian]\nmodel = "claude-sonnet-5-5"\n'
        'model_provider = "meridian"\nmodel_reasoning_effort = "high"\n'
    )
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_bytes(original.encode())
    before = config_path.stat()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        configure_codex, "resolve_ollama_cloud_model", lambda: ("m", "note")
    )
    monkeypatch.setattr(configure_codex, "resolve_local_model", lambda: "")
    monkeypatch.setattr(configure_codex, "load_env", lambda: None)
    logger = MagicMock()
    monkeypatch.setattr(configure_codex, "logger", logger)
    with patch.object(sys, "argv", ["configure-codex.py"]):
        with pytest.raises(SystemExit, match="1"):
            configure_codex.main()
    assert config_path.read_bytes() == original.encode()
    after = config_path.stat()
    assert (after.st_ino, after.st_mtime_ns, after.st_mode) == (
        before.st_ino,
        before.st_mtime_ns,
        before.st_mode,
    )
    assert "[model_providers.meridian]" in str(logger.critical.call_args)
    assert "manually remove or rename" in str(logger.critical.call_args)

    # Simulate the owner's reviewed removal of just the conflicting sections.
    retained = 'model_provider = "openai"\n\n[profiles.custom]\nmodel = "kept"\n'
    config_path.write_text(retained)
    with patch.object(sys, "argv", ["configure-codex.py"]):
        configure_codex.main()
        first = config_path.read_bytes()
        configure_codex.main()
    assert config_path.read_bytes() == first
    assert 'model_provider = "openai"' in first.decode()
    assert '[profiles.custom]\nmodel = "kept"' in first.decode()


def test_codex_registry_is_provisionable_but_not_live_verified(monkeypatch):
    import litellm_config

    row = next(
        row for row in litellm_config.CLIENT_REGISTRY if row["client"] == "codex"
    )
    assert row["classification"] == "eligible"
    assert row["classification_status"] == (
        "eligible; not live-verified"
        " (dedicated key + routed task + deploy markers owner-gated)"
    )
    assert row["credential_policy"] == "provision"
    assert row["setup_gates"] == [
        "DOTFILES_RUN_CODEX_SETUP=1",
        "DOTFILES_USE_LITELLM_PROXY=1",
        "DOTFILES_RUN_LITELLM_SETUP=1",
    ]
    assert "model_providers.litellm" in row["consumer_read_path"]
    assert row["transports"] == ["Responses API", "OpenAI-compatible"]


def test_configure_all_routes_codex_through_poetry(tmp_path):
    wrapper = CONFIGURE_ALL.read_text(encoding="utf-8")
    assert (
        'run_step "Codex configuration" poetry --directory "$DOTFILES_DIR" run python "$SCRIPT_DIR/configure-codex.py"'
        in wrapper
    )
    assert (
        'run_step "Codex configuration" python3 "$SCRIPT_DIR/configure-codex.py"'
        not in wrapper
    )

    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    record = tmp_path / "poetry-args"
    run_step_source = wrapper[
        wrapper.index("run_step() {") : wrapper.index(
            "\n}\n", wrapper.index("run_step() {")
        )
        + 3
    ]
    helper = tmp_path / "run-step.sh"
    helper.write_text(run_step_source + "\n", encoding="utf-8")
    poetry_stub = stub_bin / "poetry"
    poetry_stub.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" > "$POETRY_ARGS_FILE"\nexit 37\n',
        encoding="utf-8",
    )
    poetry_stub.chmod(0o755)
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; source "$2"; run_step "Codex configuration" poetry --directory /fixture/root run python /fixture/configure-codex.py',
            "test",
            str(REPO_ROOT / "scripts" / "lib" / "common.sh"),
            str(helper),
        ],
        env={
            "PATH": f"{stub_bin}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "POETRY_ARGS_FILE": str(record),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Codex configuration failed" in result.stderr
    assert record.read_text(encoding="utf-8").strip() == (
        "--directory /fixture/root run python /fixture/configure-codex.py"
    )


def test_codex_runtime_preflight_reports_missing_poetry_and_parser():
    script = """
set -euo pipefail
source "$1"
require_codex_runtime /fixture/root
"""
    common = REPO_ROOT / "scripts/lib/common.sh"
    configure = REPO_ROOT / "scripts/configure-all.sh"
    preflight = configure.read_text(encoding="utf-8")
    helper = preflight[preflight.index("require_codex_runtime() {") :]
    helper = helper[: helper.index("\n}") + 2]

    def run_with_path(path):
        wrapper = path / "configure-preflight.sh"
        wrapper.write_text(helper + "\n" + script, encoding="utf-8")
        result = subprocess.run(
            ["/bin/bash", str(wrapper), str(common)],
            env={"PATH": f"{path}:/usr/bin:/bin", "HOME": str(path)},
            capture_output=True,
            text=True,
            check=False,
        )
        return result

    # Use a private temporary directory, not repo files.
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        missing_poetry = run_with_path(path)
        assert missing_poetry.returncode == 1
        assert "requires Poetry" in missing_poetry.stderr

        poetry = path / "poetry"
        poetry.write_text(
            '#!/bin/sh\ncase "$*" in *tomllib*|*tomli*) exit 1;; esac\nexit 0\n',
            encoding="utf-8",
        )
        poetry.chmod(0o755)
        missing_parser = run_with_path(path)
        assert missing_parser.returncode == 1
        assert "requires the locked project TOML parser" in missing_parser.stderr


def test_configure_all_resolves_poetry_project_from_outside_checkout(tmp_path):
    configure_all = (REPO_ROOT / "scripts/configure-all.sh").read_text(encoding="utf-8")
    assert 'DOTFILES_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"' in configure_all
    assert 'poetry --directory "$DOTFILES_DIR"' in configure_all
    outside_checkout = tmp_path / "caller"
    outside_checkout.mkdir()
    assert outside_checkout.resolve() != REPO_ROOT


def test_locked_poetry_runtime_imports_codex_parser_and_dry_runs(tmp_path):
    poetry = shutil.which("poetry")
    if not poetry:
        pytest.skip("Poetry is required for the runtime probe")
    runtime = subprocess.run(
        [
            poetry,
            "run",
            "python",
            "-c",
            "import sys; print(sys.executable); print(sys.version_info[:2])",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert runtime.returncode == 0, runtime.stderr
    version = tuple(map(int, runtime.stdout.splitlines()[1].strip("()").split(", ")))
    parser_module = "tomli" if version < (3, 11) else "tomllib"
    assert (
        subprocess.run(
            [poetry, "run", "python", "-c", f"import {parser_module}"],
            cwd=REPO_ROOT,
            check=False,
        ).returncode
        == 0
    )

    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("", encoding="utf-8")
    env = {
        "HOME": str(home),
        "PATH": os.environ["PATH"],
        "DOTFILES_RUN_CODEX_SETUP": "0",
        "DOTFILES_USE_LITELLM_PROXY": "0",
        "DOTFILES_OPENCODE_TIER": "local",
        "PYTHONPATH": str(REPO_ROOT / "scripts" / "lib"),
    }
    help_result = subprocess.run(
        [poetry, "run", "python", str(SCRIPT), "--help"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_result.returncode == 0, help_result.stderr
    dry_run = subprocess.run(
        [poetry, "run", "python", str(SCRIPT), "--dry-run", "--no-backup"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert "Would configure providers" in dry_run.stdout + dry_run.stderr
    assert not (home / ".codex" / "config.toml").exists()


def test_python310_locked_runtime_when_explicitly_requested(tmp_path):
    if os.environ.get("DOTFILES_TEST_CODEX_PY310") != "1":
        pytest.skip(
            "Set DOTFILES_TEST_CODEX_PY310=1 with the isolated Python 3.10 Poetry env"
        )
    poetry = shutil.which("poetry")
    assert poetry
    runtime = subprocess.run(
        [
            poetry,
            "run",
            "python",
            "-c",
            "import sys,tomli; print(sys.version_info[:2]); print(tomli.__version__); print(sys.executable)",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert runtime.returncode == 0, runtime.stderr
    assert runtime.stdout.splitlines()[0] == "(3, 10)"
    assert runtime.stdout.splitlines()[1] == "2.4.1"

    home = tmp_path / "py310-home"
    home.mkdir()
    env = {
        "HOME": str(home),
        "PATH": os.environ["PATH"],
        "DOTFILES_RUN_CODEX_SETUP": "0",
        "DOTFILES_USE_LITELLM_PROXY": "0",
        "DOTFILES_OPENCODE_TIER": "local",
        "PYTHONPATH": str(REPO_ROOT / "scripts" / "lib"),
    }
    for name in ("POETRY_VIRTUALENVS_IN_PROJECT", "POETRY_VIRTUALENVS_PATH"):
        if name in os.environ:
            env[name] = os.environ[name]
    for args, phrase in (
        (["--help"], "usage:"),
        (["--dry-run", "--no-backup"], "Would configure providers"),
    ):
        result = subprocess.run(
            [poetry, "run", "python", str(SCRIPT), *args],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert phrase in result.stdout + result.stderr
    assert not (home / ".codex" / "config.toml").exists()
