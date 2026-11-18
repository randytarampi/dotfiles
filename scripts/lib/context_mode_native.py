"""Reconcile native Context Mode clients after dotfiles configuration generators.

Context Mode owns native MCP registration, hooks and skills. Pi and OpenCode
already register their native plugins through this repo's own generators, so
they are deliberately absent from this reconciler.
"""

import argparse
import os
import re
import shutil
import subprocess  # nosec B404 - fixed argv arrays, never shell=True
import sys
from pathlib import Path

AGENT_EXECUTABLES = (
    ("claude-code", "claude"),
    ("codex", "codex"),
    ("gemini", "gemini"),
    ("copilot-cli", "copilot"),
    ("qwen", "qwen"),
    ("omp", "omp"),
    ("kiro", "kiro-cli"),
    ("zcode", "zcode"),
)
MIN_NODE = (24, 15, 0)


def node_is_supported(which=shutil.which, run=subprocess.run):
    executable = which("node")
    if not executable:
        return False
    result = run([executable, "--version"], capture_output=True, text=True, check=False)
    if result.returncode:
        return False
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)\s*", result.stdout)
    return bool(match and tuple(map(int, match.groups())) >= MIN_NODE)


def vscode_present(which=shutil.which, environ=None):
    """Only stable VS Code, not Insiders, VSCodium or Cursor."""
    env = os.environ if environ is None else environ
    if which("code"):
        return True
    home = Path(env.get("HOME", str(Path.home())))
    if sys.platform == "darwin":
        return (
            Path("/Applications/Visual Studio Code.app").exists()
            or (home / "Applications/Visual Studio Code.app").exists()
        )
    if os.name == "nt":
        local_app_data = env.get("LOCALAPPDATA")
        return (
            bool(local_app_data)
            and (Path(local_app_data) / "Programs/Microsoft VS Code/Code.exe").is_file()
        )
    return (home / ".config/Code").is_dir()


def selected_agents(which=shutil.which, environ=None):
    agents = [agent for agent, binary in AGENT_EXECUTABLES if which(binary)]
    if vscode_present(which, environ):
        agents.append("vscode")
    return agents


def _ensure_client_cli(which, run, env):
    if not node_is_supported(which, run):
        raise RuntimeError("Context Mode requires Node >=24.15.0")
    npm = which("npm")
    if not npm:
        raise RuntimeError("Context Mode setup requires npm")
    if (
        not which("context-mode")
        or env.get("DOTFILES_RUN_UPDATE_SYSTEM_SETUP", "0") == "1"
    ):
        # @latest is resolved only at install or explicit update time.
        run([npm, "install", "--global", "context-mode@latest"], check=True)
    command = which("context-mode")
    if not command:
        raise RuntimeError("Context Mode CLI missing after installation")
    return command


def _configure_clients(command, agents, run):
    failures = []
    for agent in agents:
        # Upstream merges the client's native MCP, hooks and bundled skills.
        # Reconcile after the repo's other config writers at every deploy.
        outcome = run([command, "setup", "--yes", "--agents", agent], check=False)
        # A successful install can still leave disabled hooks. Doctor catches
        # a missing or rejected native registration.
        check = (
            run([command, "doctor", "--agents", agent], check=False)
            if outcome.returncode == 0
            else outcome
        )
        if outcome.returncode or check.returncode:
            failures.append(agent)
    if failures:
        raise RuntimeError("Context Mode setup failed: " + ", ".join(failures))


def planned_commands(which, agents, env):
    """Describe the work reconcile would do, without running it."""
    lines = []
    if (
        not which("context-mode")
        or env.get("DOTFILES_RUN_UPDATE_SYSTEM_SETUP", "0") == "1"
    ):
        lines.append("npm install --global context-mode@latest")
    command = which("context-mode") or "context-mode"
    for agent in agents:
        lines.append(f"{command} setup --yes --agents {agent}")
        lines.append(f"{command} doctor --agents {agent}")
    return lines


def reconcile(which=shutil.which, run=subprocess.run, environ=None, *, dry_run=False):
    env = os.environ if environ is None else environ
    if env.get("DOTFILES_RUN_CONTEXT_MODE_SETUP", "0") != "1":
        return 0
    agents = selected_agents(which, env)
    if not agents:
        print(
            "Context Mode: no eligible native clients; OpenCode/Pi use own generators"
        )
        return 0
    if dry_run:
        for line in planned_commands(which, agents, env):
            print(f"[dry-run] would run: {line}")
        return 0
    try:
        command = _ensure_client_cli(which, run, env)
        _configure_clients(command, agents, run)
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        print("Context Mode installation failed: " + str(exc), file=sys.stderr)
        return 1
    print("Context Mode configured for: " + ", ".join(agents))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report planned npm and native-client commands without running them",
    )
    args = parser.parse_args(argv)
    return reconcile(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
