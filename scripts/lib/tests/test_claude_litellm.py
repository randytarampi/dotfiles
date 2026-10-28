import json
from pathlib import Path
import subprocess
import os
import shutil
import sys
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "claude-litellm"


@pytest.mark.parametrize("mode, expected", [(0o600, 0), (0o644, 1)])
def test_gnu_stat_fallback_discards_failed_bsd_stdout(tmp_path, mode, expected):
    env, _ = _setup(tmp_path)
    key = Path(env["HOME"]) / ".local/share/litellm/clients/claude.key"
    key.chmod(mode)
    stub = tmp_path / "bin/stat"
    stub.unlink()
    stub.write_text(
        f"#!{sys.executable}\n"
        "import os, stat, sys\n"
        "if sys.argv[1] == '-f':\n"
        "    print('GNU filesystem output before failure')\n"
        "    sys.exit(1)\n"
        "info = os.stat(sys.argv[-1])\n"
        "print(format(stat.S_IMODE(info.st_mode), 'o') if sys.argv[2] == '%a' else info.st_uid)\n"
    )
    stub.chmod(0o700)
    result = subprocess.run([str(SCRIPT)], env=env, capture_output=True, text=True)
    assert result.returncode == expected, result.stderr
    if expected == 0:
        assert json.loads(result.stdout)["key"] == "dummy-key"
    else:
        assert "mode 0600" in result.stderr


def _setup(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    key = home / ".local/share/litellm/clients/claude.key"
    key.parent.mkdir(parents=True)
    key.write_text("dummy-key\n")
    key.chmod(0o600)
    capture = tmp_path / "capture.py"
    capture.write_text(
        "#!/usr/bin/env python3\nimport json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], 'base': os.environ.get('ANTHROPIC_BASE_URL'), 'key': os.environ.get('ANTHROPIC_API_KEY'), 'env_names': sorted(os.environ)}))\n"
    )
    capture.chmod(0o700)
    path = tmp_path / "bin"
    path.mkdir()
    for command in ("bash", "stat", "id", "python3", "tr", "dirname", "readlink"):
        resolved = shutil.which(command)
        if resolved:
            (path / command).symlink_to(resolved)
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": str(path),
        "DOTFILES_RUN_CLAUDE_SETUP": "1",
        "DOTFILES_USE_LITELLM_PROXY": "1",
        "DOTFILES_CLAUDE_BIN": str(capture),
        "DOTFILES_CLAUDE_LITELLM_MODEL": "served-test-alias",
        "ANTHROPIC_API_KEY": "inherited",
    }
    return env, capture


def test_disabled_wrapper_fails_without_side_effects(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run([str(SCRIPT)], env={"HOME": str(home)}, capture_output=True)
    assert result.returncode == 1
    assert list(home.iterdir()) == []


def test_gateway_wrapper_uses_session_settings_and_preserves_safe_args(tmp_path):
    env, _ = _setup(tmp_path)
    for name in (
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "ANTHROPIC_CUSTOM_HEADERS",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
    ):
        env[name] = "inherited-override"
    result = subprocess.run(
        [str(SCRIPT), "hello world"], env=env, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    received = json.loads(result.stdout)
    assert received["argv"] == [
        "--bare",
        "--setting-sources",
        "",
        "--settings",
        '{"model":"served-test-alias","env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:4000"}}',
        "hello world",
    ]
    assert received["base"] == "http://127.0.0.1:4000"
    assert received["key"] == "dummy-key"
    assert "dummy-key" not in result.args
    for name in (
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "ANTHROPIC_CUSTOM_HEADERS",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
    ):
        assert name not in received["env_names"]


def test_dry_run_does_not_open_key_or_execute_cli(tmp_path):
    env, capture = _setup(tmp_path)
    (Path(env["HOME"]) / ".local/share/litellm/clients/claude.key").unlink()
    capture.unlink()
    result = subprocess.run(
        [str(SCRIPT), "--dry-run"], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "credential value not read or displayed" in result.stdout
    assert not capture.exists()


def test_missing_key_and_endpoint_override_fail_before_exec(tmp_path):
    env, _ = _setup(tmp_path)
    (Path(env["HOME"]) / ".local/share/litellm/clients/claude.key").unlink()
    missing = subprocess.run([str(SCRIPT)], env=env, capture_output=True)
    assert missing.returncode == 1

    second = tmp_path / "second"
    second.mkdir()
    env, _ = _setup(second)
    conflict = subprocess.run(
        [str(SCRIPT), "--model", "direct-model"], env=env, capture_output=True
    )
    assert conflict.returncode == 2


def test_key_must_be_nonempty_private_regular_owned_and_nonconflicting(tmp_path):
    env, _ = _setup(tmp_path)
    key = Path(env["HOME"]) / ".local/share/litellm/clients/claude.key"
    key.write_text(" \n")
    assert subprocess.run([str(SCRIPT)], env=env, capture_output=True).returncode == 1
    key.write_text("dummy-key\n")
    key.chmod(0o644)
    assert subprocess.run([str(SCRIPT)], env=env, capture_output=True).returncode == 1
    key.chmod(0o600)
    env["LITELLM_CLAUDE_KEY"] = "other"
    assert subprocess.run([str(SCRIPT)], env=env, capture_output=True).returncode == 1


def test_key_symlink_is_rejected(tmp_path):
    env, _ = _setup(tmp_path)
    root = Path(env["HOME"]) / ".local/share/litellm/clients"
    key = root / "claude.key"
    key.unlink()
    (root / "actual.key").write_text("dummy-key\n")
    (root / "actual.key").chmod(0o600)
    key.symlink_to("actual.key")
    assert subprocess.run([str(SCRIPT)], env=env, capture_output=True).returncode == 1


def test_key_must_be_regular_file_and_owned_by_current_user(tmp_path):
    env, _ = _setup(tmp_path)
    clients = Path(env["HOME"]) / ".local/share/litellm/clients"
    key = clients / "claude.key"
    key.unlink()
    key.mkdir(mode=0o700)
    assert subprocess.run([str(SCRIPT)], env=env, capture_output=True).returncode == 1

    key.rmdir()
    key.write_text("dummy-key\n")
    key.chmod(0o600)
    try:
        os.chown(key, os.getuid() + 1, os.getgid())
    except PermissionError:
        pytest.skip("Cannot create a foreign-owned credential fixture")
    assert subprocess.run([str(SCRIPT)], env=env, capture_output=True).returncode == 1


def test_unsafe_overrides_and_disabled_gate_never_exec(tmp_path):
    env, capture = _setup(tmp_path)
    capture.unlink()
    for argument in (
        "--settings={}",
        "--setting-sources=user",
        "--bare",
        "--api-key-helper=x",
        "-m",
        "-m=unsafe",
        "--fallback-model",
        "--fallback-model=unsafe",
    ):
        result = subprocess.run([str(SCRIPT), argument], env=env, capture_output=True)
        assert result.returncode == 2
    env["DOTFILES_RUN_CLAUDE_SETUP"] = "0"
    result = subprocess.run([str(SCRIPT)], env=env, capture_output=True)
    assert result.returncode == 1
    assert not capture.exists()


def test_model_alias_is_required_and_key_internal_whitespace_rejected(tmp_path):
    env, _ = _setup(tmp_path)
    env.pop("DOTFILES_CLAUDE_LITELLM_MODEL")
    result = subprocess.run([str(SCRIPT)], env=env, capture_output=True)
    assert result.returncode == 1
    key = Path(env["HOME"]) / ".local/share/litellm/clients/claude.key"
    key.write_text("dummy key\n")
    key.chmod(0o600)
    env["DOTFILES_CLAUDE_LITELLM_MODEL"] = "served-test-alias"
    result = subprocess.run([str(SCRIPT)], env=env, capture_output=True)
    assert result.returncode == 1


def test_env_and_key_file_conflict_fails_before_exec(tmp_path):
    env, _ = _setup(tmp_path)
    env["LITELLM_CLAUDE_KEY"] = "different-key"
    result = subprocess.run([str(SCRIPT)], env=env, capture_output=True)
    assert result.returncode == 1


def test_gateway_wrapper_does_not_write_claude_settings(tmp_path):
    env, _ = _setup(tmp_path)
    subprocess.run([str(SCRIPT), "ok"], env=env, check=True, capture_output=True)
    assert not (Path(env["HOME"]) / ".claude").exists()
