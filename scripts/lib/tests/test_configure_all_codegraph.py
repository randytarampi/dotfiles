import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "configure-all.sh"


def run_configure(script, env):
    return subprocess.run(
        ["bash", str(script), "--skip", "cleanup,codex,litellm,openwebui"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_codegraph_installer_uses_disposable_codex_home(tmp_path):
    home = tmp_path / "home"
    config = home / ".codex/config.toml"
    config.parent.mkdir(parents=True)
    guidance = config.parent / "AGENTS.md"
    original = "# [mcp_servers.codegraph]\n# BEGIN managed\n"
    original_guidance = "local Codex guidance\n"
    config.write_text(original)
    guidance.write_text(original_guidance)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "record"
    codegraph = bin_dir / "codegraph"
    codegraph.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$CODEX_HOME" "$*" > "$RECORD"\n'
        'mkdir -p "$CODEX_HOME"\n'
        'printf "destructive rewrite\\n" > "$CODEX_HOME/config.toml"\n'
        'printf "%s\\n" "${GUIDANCE:-CodeGraph guidance}" >> "$CODEX_HOME/AGENTS.md"\n'
        'exit "${FAKE_CODEGRAPH_EXIT:-0}"\n'
    )
    codegraph.chmod(0o755)
    env = base_env(tmp_path, home, bin_dir, record)
    env["GUIDANCE"] = "updated CodeGraph guidance"
    attempts = stub_commands(tmp_path)

    result = run_configure(SCRIPT, env)

    assert result.returncode == 0, result.stderr
    codex_home, args = record.read_text().splitlines()
    assert args == "install -y --target auto --location global"
    assert Path(codex_home).name.startswith("dotfiles-codegraph-codex.")
    assert not Path(codex_home).exists()
    assert config.read_text() == original
    assert guidance.read_text() == original_guidance + "updated CodeGraph guidance\n"
    assert not attempts.exists()


def test_codegraph_failure_preserves_failure_and_cleans_codex_home(tmp_path):
    home = tmp_path / "home"
    real_home = home / ".codex"
    real_home.mkdir(parents=True)
    guidance = real_home / "AGENTS.md"
    guidance.write_text("preserve this guidance\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "record"
    codegraph = bin_dir / "codegraph"
    codegraph.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$CODEX_HOME" "$*" > "$RECORD"\n'
        'printf "must not be copied\\n" >> "$CODEX_HOME/AGENTS.md"\n'
        "exit 7\n"
    )
    codegraph.chmod(0o755)
    env = base_env(tmp_path, home, bin_dir, record)
    attempts = stub_commands(tmp_path)

    result = run_configure(SCRIPT, env)

    assert result.returncode == 1
    codex_home, args = record.read_text().splitlines()
    assert args == "install -y --target auto --location global"
    assert not Path(codex_home).exists()
    assert guidance.read_text() == "preserve this guidance\n"
    assert not attempts.exists()


def test_custom_codex_home_guidance_is_preserved_and_config_untouched(tmp_path):
    codex_home = tmp_path / "custom-codex"
    codex_home.mkdir()
    config = codex_home / "config.toml"
    config.write_text("# custom ownership comment\n")
    guidance = codex_home / "AGENTS.md"
    guidance.write_text("custom guidance\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "record"
    codegraph = bin_dir / "codegraph"
    codegraph.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$CODEX_HOME" "$*" > "$RECORD"\n'
        'printf "updated custom guidance\\n" >> "$CODEX_HOME/AGENTS.md"\n'
        'printf "destructive rewrite\\n" > "$CODEX_HOME/config.toml"\n'
    )
    codegraph.chmod(0o755)
    env = base_env(tmp_path, tmp_path / "home", bin_dir, record)
    env["CODEX_HOME"] = str(codex_home)
    attempts = stub_commands(tmp_path)

    result = run_configure(SCRIPT, env)

    assert result.returncode == 0, result.stderr
    sandbox, args = record.read_text().splitlines()
    assert args == "install -y --target auto --location global"
    assert Path(sandbox).name.startswith("dotfiles-codegraph-codex.")
    assert not Path(sandbox).exists()
    assert config.read_text() == "# custom ownership comment\n"
    assert guidance.read_text() == "custom guidance\nupdated custom guidance\n"
    assert not attempts.exists()


def base_env(tmp_path, home, bin_dir, record):
    return {
        "HOME": str(home),
        "PATH": f"{bin_dir}:{tmp_path}/stubs:/usr/bin:/bin",
        "TMPDIR": str(tmp_path),
        "RECORD": str(record),
        "DOTFILES_RUN_CODEGRAPH_SETUP": "1",
        "DOTFILES_RUN_CODEX_SETUP": "0",
        "DOTFILES_RUN_LITELLM_SETUP": "0",
    }


def stub_commands(tmp_path):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    record = tmp_path / "service-command-attempts"
    for name in ("launchctl", "pkill", "systemctl", "curl", "sleep"):
        command = stubs / name
        command.write_text(
            f'#!/bin/sh\nprintf "%s\\n" "{name}" >> "{record}"\nexit 99\n'
        )
        command.chmod(0o755)
    return record
