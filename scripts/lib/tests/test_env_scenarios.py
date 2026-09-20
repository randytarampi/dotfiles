"""Hermetic, non-networked environment profiles for the configure layer."""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[2]
CONFIGURE_ALL = REPO_ROOT / "scripts" / "configure-all.sh"
ENV_EXAMPLE = REPO_ROOT / "dot_dotfiles" / "shell" / ".env.example"


def run_configure(home, **values):
    """Run the canonical configure entry point with no ambient gates."""
    skip_steps = values.pop("_skip", None)
    home.mkdir(parents=True, exist_ok=True)
    # This is deliberately an allowlist, rather than a denylist: tool-specific
    # path, endpoint, role/model, and credential overrides must not escape the
    # scenario's fake home.  Coverage/PYTHONPATH are retained for subprocesses.
    env = {
        key: os.environ[key]
        for key in (
            "PATH",
            "HOME",
            "USERPROFILE",
            "TMPDIR",
            "TMP",
            "TEMP",
            "COVERAGE_PROCESS_START",
            "PYTHONPATH",
        )
        if key in os.environ
    }
    env.update({key: str(value) for key, value in values.items()})
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[key] = str(home / key.removeprefix("XDG_").lower())
    env["DOTFILES_OPENCODE_TIER"] = "local"
    (home / ".env").write_text(
        ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    with (home / ".env").open("a", encoding="utf-8") as file:
        for key, value in values.items():
            if key.startswith("DOTFILES_RUN_") or key in {
                "OMLX_API_KEY",
                "OPENROUTER_API_KEY",
            }:
                file.write(f"{key}={value!r}\n")
    command = ["bash", str(CONFIGURE_ALL)]
    if skip_steps:
        command.extend(["--skip", skip_steps])
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    # The allowlisted environment is the isolation boundary: all expected
    # configure output is rooted below HOME, never the developer's home.
    assert str(Path.home()) not in combined_output(result)
    return result


def combined_output(result):
    return f"{result.stdout}\n{result.stderr}"


def test_minimal_profile_skips_all_gates(tmp_path):
    result = run_configure(tmp_path / "home")

    assert result.returncode == 0, result.stderr
    output = combined_output(result)
    lines = output.splitlines()
    expected_skips = {
        "DOTFILES_RUN_OPENCODE_SETUP": "DOTFILES_RUN_OPENCODE_SETUP not set — skipping OpenCode configuration",
        "DOTFILES_RUN_PI_SETUP": "DOTFILES_RUN_PI_SETUP not set — skipping Pi configuration",
        "DOTFILES_RUN_MCP_SETUP": "DOTFILES_RUN_MCP_SETUP not set — skipping MCP configuration",
        "DOTFILES_RUN_AGENT_GUIDANCE_SETUP": "DOTFILES_RUN_AGENT_GUIDANCE_SETUP not set — skipping agent guidance distribution",
        "DOTFILES_RUN_SKILLS_SETUP": "DOTFILES_RUN_SKILLS_SETUP not set — skipping skills distribution",
    }
    for gate, message in expected_skips.items():
        assert any(message in line for line in lines), gate
    # This harness exercises configure-all.sh only; chezmoi's package,
    # security, and system-default scripts are intentionally out of scope.
    assert not (tmp_path / "home" / ".config" / "opencode" / "opencode.json").exists()


def test_configuration_only_profile_generates_configs_without_system_setup(tmp_path):
    home = tmp_path / "home"
    result = run_configure(
        home,
        DOTFILES_RUN_OPENCODE_SETUP=1,
        DOTFILES_RUN_PI_SETUP=1,
        _skip="opencode-restart",
    )

    assert result.returncode == 0, result.stderr
    assert (home / ".config" / "opencode" / "opencode.json").is_file()
    assert (home / ".pi" / "agent" / "models.json").is_file()
    output = combined_output(result)
    assert any(
        "DOTFILES_RUN_PACKAGES_SETUP not set — skipping npm package reconciliation"
        in line
        for line in output.splitlines()
    )
    assert any(
        "DOTFILES_RUN_MCP_SETUP not set — skipping MCP configuration" in line
        for line in output.splitlines()
    )
    assert "Restarting OpenCode Web" not in output
    assert "OpenCode Web restarted." not in output
    assert not (home / ".config" / "caddy").exists()
    assert not (home / "Library").exists()


@pytest.fixture
def configure_pi_module():
    spec = importlib.util.spec_from_file_location(
        "configure_pi_scenario", REPO_ROOT / "scripts" / "configure-pi.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def local_engines_module():
    import local_engines

    return local_engines


@pytest.mark.parametrize(
    ("api_key", "expected"), [(None, "omlx"), ("fake-value-for-ci", "$OMLX_API_KEY")]
)
def test_omlx_subprocess_generates_models_json_and_provider_contract_is_unit_test(
    tmp_path, configure_pi_module, local_engines_module, monkeypatch, api_key, expected
):
    home = tmp_path / "home"
    values = {"DOTFILES_RUN_OMLX_SETUP": 1, "DOTFILES_RUN_PI_SETUP": 1}
    if api_key is not None:
        values["OMLX_API_KEY"] = api_key
    result = run_configure(home, **values)
    assert result.returncode == 0, result.stderr
    generated = json.loads((home / ".pi" / "agent" / "models.json").read_text())
    assert "providers" in generated
    omlx_provider = generated["providers"].get("omlx")
    if omlx_provider is not None:
        assert omlx_provider["apiKey"] == expected

    # The canonical subprocess is asserted above.  Provider-key semantics are
    # a provider-contract unit test because oMLX model discovery requires a
    # live daemon; do not let this helper silently stand in for subprocess output.
    # setitem restores the registry after the test instead of leaking the fake
    # health check into other tests.
    monkeypatch.setitem(
        local_engines_module.LOCAL_ENGINES,
        "omlx",
        {
            **local_engines_module.LOCAL_ENGINES["omlx"],
            "health_check": lambda: (True, ""),
        },
    )
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    if api_key is None:
        monkeypatch.delenv("OMLX_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OMLX_API_KEY", api_key)
    provider = configure_pi_module.build_local_provider("omlx", [])
    assert provider is not None
    assert provider["apiKey"] == expected


@pytest.mark.parametrize("api_key_present", [False, True])
def test_representative_cloud_key_changes_emitted_provider(tmp_path, api_key_present):
    home = tmp_path / "home"
    values = {"DOTFILES_RUN_PI_SETUP": 1}
    if api_key_present:
        values["OPENROUTER_API_KEY"] = "fake-value-for-ci"
    result = run_configure(home, **values)
    assert result.returncode == 0, result.stderr
    models = json.loads((home / ".pi" / "agent" / "models.json").read_text())
    if api_key_present:
        assert "openrouter" in models["providers"]
        assert models["providers"]["openrouter"]["apiKey"] == "$OPENROUTER_API_KEY"
    else:
        assert "openrouter" not in models["providers"]
