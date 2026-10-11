"""Execute both gateway orchestration boundaries without host services."""

from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
SKIPS = "cleanup,npm-packages,secrets,aws,mcps,cortex,meridian,codex,mozart,agent-guidance,codegraph,codegraph-indexes,ollama-daemon,skills,ddns,caddy,opencode-restart"


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def _sandbox(tmp_path, platform):
    home = tmp_path / "home"
    home.mkdir()
    source = tmp_path / "source"
    scripts = source / "scripts"
    lib = scripts / "lib"
    bin_dir = tmp_path / "bin"
    trace = tmp_path / "trace"
    common = """
info() { :; }; ok() { :; }; warn() { :; }
die() { printf '%s\\n' "$*" >&2; exit 1; }
record() { printf '%s\\n' "$1" >> "$TRACE"; }
"""
    _write(lib / "common.sh", common)
    _write(
        lib / "common_args.sh",
        """
COMMON_DRY_RUN=0; COMMON_FORWARD_ARGS=()
parse_common_args() { for arg in "$@"; do
  if [[ "$arg" == --dry-run ]]; then COMMON_DRY_RUN=1; COMMON_FORWARD_ARGS=(--dry-run); fi
done; }
""",
    )
    _write(lib / "env.sh", "load_env() { return 0; }\n")
    _write(lib / "tier_detect.sh", "detect_tier() { TIER=free; }\n")
    _write(lib / "tier_args.sh", "build_tier_extra_args() { TIER_EXTRA_ARGS=(); }\n")
    _write(
        lib / "litellm_service.sh",
        """
litellm_require_database_url() { record database; }
litellm_ensure_prisma_client() { record prisma; }
litellm_service_env_sync() { mkdir -p "$(dirname "$1")"; [[ -f "$1" ]] || printf 'stable-env\\n' > "$1"; record env; }
litellm_service_plist() { printf '%s/Library/LaunchAgents/com.litellm.proxy.plist' "$HOME"; }
litellm_service_start() { record start; }
litellm_service_restart() { record restart; }
litellm_service_stop() { record stop; }
""",
    )
    _write(
        lib / "openwebui_service.sh",
        """
openwebui_service_env_sync() { record webui-env; }
openwebui_service_restart() { record webui-restart; }
openwebui_service_wait_healthy() { return 0; }
""",
    )
    commands = """#!/bin/bash
set -euo pipefail
record() { printf '%s\\n' "$1" >> "$TRACE"; }
case "$(basename "$0")" in
uname) printf '%s\\n' "$TEST_PLATFORM";;
sleep) :;;
curl) [[ "${FAILURE:-}" != readiness ]] || exit 22; record ready;;
systemctl) [[ "$*" != *restart* ]] || record restart;;
python3.11)
  mkdir -p "$3/bin"
  printf '#!/bin/bash\\nexit 0\\n' > "$3/bin/python"; chmod +x "$3/bin/python";;
python3)
  name="$(basename "$1")"; shift
  case "$name" in
  configure-litellm.py)
    if [[ "$*" == *--require-ready* ]]; then
      [[ "${FAILURE:-}" != provision ]] || exit 1
      mkdir -p "$HOME/.local/share/litellm/clients"
      [[ -f "$HOME/.local/share/litellm/clients/opencode.key" ]] || printf 'stable-key' > "$HOME/.local/share/litellm/clients/opencode.key"
      record provision
    else record generate; fi;;
  configure-litellm-venv.py) record patch;;
  configure-opencode.py|configure-pi.py|configure-jetbrains-ai.py|configure-openwebui.py)
    if [[ "$*" == *--dry-run* ]]; then record preview; exit 0; fi
    [[ -s "$HOME/.local/share/litellm/clients/opencode.key" ]] || exit 9
    record "$name";;
  *) exit 8;;
  esac;;
*) exit 7;;
esac
"""
    for name in ("python3", "python3.11", "curl", "uname", "sleep", "systemctl"):
        _write(bin_dir / name, commands)
    for name in (
        "configure-litellm.py",
        "configure-litellm-venv.py",
        "configure-jetbrains-ai.py",
    ):
        _write(scripts / name, '#!/bin/bash\nexec python3 "$0" "$@"\n')
    wrapper = scripts / "configure-all.sh"
    _write(wrapper, (ROOT / "scripts/configure-all.sh").read_text())
    env = {
        "HOME": str(home),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "TRACE": str(trace),
        "TEST_PLATFORM": platform,
        "DOTFILES_RUN_LITELLM_SETUP": "1",
        "DOTFILES_USE_LITELLM_PROXY": "1",
        "DOTFILES_RUN_OPENCODE_SETUP": "1",
        "DOTFILES_RUN_PI_SETUP": "1",
        "DOTFILES_RUN_JUNIE_CLI_SETUP": "1",
        "DOTFILES_RUN_OPENWEBUI_SETUP": "1",
        "DOTFILES_RUN_OPENWEBUI_TERMINAL_SETUP": "1",
        "DOTFILES_RUN_OPENWEBUI_COMPUTER_SETUP": "1",
        "LITELLM_DATABASE_URL": "fixture",
    }
    return source, wrapper, env, trace


def _events(trace):
    return trace.read_text().splitlines() if trace.exists() else []


@pytest.mark.parametrize("platform", ["Darwin", "Linux"])
def test_wrapper_fresh_gateway_precedes_all_clients_and_repeat_is_stable(
    tmp_path, platform
):
    _, wrapper, env, trace = _sandbox(tmp_path, platform)
    for run in range(2):
        result = subprocess.run(
            ["/bin/bash", str(wrapper), "--skip", SKIPS],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        events = _events(trace)
        if run == 0:
            first = events[:]
            assert (
                events.index("generate")
                < events.index("patch")
                < events.index("restart")
                < events.index("ready")
                < events.index("provision")
            )
            for client in (
                "configure-opencode.py",
                "configure-pi.py",
                "configure-jetbrains-ai.py",
                "configure-openwebui.py",
            ):
                assert events.index("provision") < events.index(client)
            key = Path(env["HOME"]) / ".local/share/litellm/clients/opencode.key"
            service = Path(env["HOME"]) / ".local/share/litellm/service.env"
            before = (key.read_bytes(), service.read_bytes())
        else:
            assert events[len(first) :] == first
            assert (key.read_bytes(), service.read_bytes()) == before


@pytest.mark.parametrize("failure", ["readiness", "provision"])
def test_wrapper_failure_prevents_client_invocation(tmp_path, failure):
    _, wrapper, env, trace = _sandbox(tmp_path, "Darwin")
    result = subprocess.run(
        ["/bin/bash", str(wrapper), "--skip", SKIPS],
        env={**env, "FAILURE": failure},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert not any(item.startswith("configure-") for item in _events(trace))


def test_wrapper_dry_run_does_not_provision_or_restart(tmp_path):
    _, wrapper, env, trace = _sandbox(tmp_path, "Darwin")
    result = subprocess.run(
        ["/bin/bash", str(wrapper), "--skip", SKIPS, "--dry-run"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert not set(_events(trace)) & {"generate", "patch", "restart", "provision"}
    assert not (Path(env["HOME"]) / ".local/share/litellm").exists()


def test_wrapper_explicit_skip_preserves_existing_gateway(tmp_path):
    _, wrapper, env, trace = _sandbox(tmp_path, "Darwin")
    key = Path(env["HOME"]) / ".local/share/litellm/clients/opencode.key"
    _write(key, "existing-key")
    result = subprocess.run(
        ["/bin/bash", str(wrapper), "--skip", SKIPS + ",litellm"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert not set(_events(trace)) & {"generate", "patch", "restart", "provision"}
    assert key.read_text() == "existing-key"


def test_rendered_gateway_template_provisions_before_junie(tmp_path):
    chezmoi = shutil.which("chezmoi")
    if not chezmoi:
        pytest.skip("chezmoi unavailable; integrated CLI preflight remains required")
    source, _, env, trace = _sandbox(tmp_path, "Darwin")
    names = sorted(p.name for p in (ROOT / ".chezmoiscripts").glob("*.tmpl"))
    gateway_name = "run_onchange_15-litellm.sh.tmpl"
    assert "run_onchange_31-litellm.sh.tmpl" not in names
    for name in (
        "run_onchange_16-configure-opencode.sh.tmpl",
        "run_onchange_18-configure-pi.sh.tmpl",
        "run_onchange_30-openwebui.sh.tmpl",
    ):
        assert names.index(gateway_name) < names.index(name)
    text = (ROOT / ".chezmoiscripts/run_onchange_15-litellm.sh.tmpl").read_text()
    for include in re.findall(r'include "([^"]+)"', text):
        path = source / include
        if not path.exists():
            _write(path, "# fixture hash input\n")
    rendered = subprocess.run(
        [chezmoi, "--source", str(source), "execute-template"],
        input=text,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert rendered.returncode == 0, rendered.stderr
    result = subprocess.run(
        ["/bin/bash"],
        input=rendered.stdout,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    events = _events(trace)
    assert (
        events.index("generate")
        < events.index("patch")
        < events.index("start")
        < events.index("ready")
        < events.index("provision")
        < events.index("configure-jetbrains-ai.py")
    )
