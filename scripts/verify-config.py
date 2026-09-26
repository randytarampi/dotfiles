#!/usr/bin/env python3
"""Verify that generated config files exist for enabled features.

Read-only drift check. For each DOTFILES_RUN_*_SETUP gate that is enabled,
verifies that the expected generated output files exist.

Exit codes:
  0 — all enabled features have their output files
  1 — one or more enabled features are missing output files
"""

import os
import sys
import json
import platform
import re
import shutil
import subprocess
import plistlib
import shlex
from pathlib import Path
from typing import Optional

HOME = Path.home()
OMLX_AUDIO_UPLOAD_SIZE_PATTERN = re.compile(
    r"^\d+(?:\.\d+)?(?:KB|MB|GB)$", re.IGNORECASE
)
CORTEX_HOME = Path(
    os.environ.get("SNOWFLAKE_HOME", str(HOME / ".snowflake")).strip()
).expanduser()


def get_brew_prefix() -> Optional[Path]:
    try:
        result = subprocess.run(
            ["brew", "--prefix"], capture_output=True, text=True, check=True
        )
        prefix = result.stdout.strip()
        if prefix:
            return Path(prefix)
    except Exception:
        pass
    for prefix in ["/opt/homebrew", "/usr/local"]:
        if Path(prefix).exists():
            return Path(prefix)
    return None


BREW_PREFIX = get_brew_prefix()
CADDY_CHECK_PATHS = [BREW_PREFIX / "etc/caddy/Caddyfile"] if BREW_PREFIX else []


def validate_caddy_auth_conf(path: Path) -> tuple[bool, int]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False, 0

    seen: set[tuple[str, str]] = set()
    valid_entries = 0
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue

        user, password_hash = line.split(":", 1)
        user = user.strip()
        password_hash = password_hash.strip()
        if not user or not password_hash:
            continue

        entry = (user, password_hash)
        if entry in seen:
            continue
        seen.add(entry)
        valid_entries += 1

    return valid_entries > 0, valid_entries


# Gate → list of (description, file path) checks
CHECKS = [
    (
        "DOTFILES_RUN_OPENCODE_SETUP",
        "OpenCode config",
        [
            HOME / ".config/opencode/opencode.json",
            HOME / ".config/opencode/oh-my-opencode-slim.json",
        ],
    ),
    (
        "DOTFILES_RUN_MCP_SETUP",
        "MCP configs",
        [
            HOME / ".config/opencode/mcp",
        ],
    ),
    (
        "DOTFILES_RUN_MOZART_SETUP",
        "Mozart router config",
        [
            HOME / ".mozart/mozart.json",
        ],
    ),
    (
        "DOTFILES_RUN_PI_SETUP",
        "Pi config",
        [
            HOME / ".pi/agent/settings.json",
            HOME / ".pi/agent/models.json",
            HOME / ".pi/agent/auth.json",
        ],
    ),
    (
        "DOTFILES_RUN_CORTEX_SETUP",
        "Cortex config",
        [
            CORTEX_HOME / "cortex/settings.json",
            CORTEX_HOME / "cortex/permissions.json",
            CORTEX_HOME / "cortex/mcp.json",
        ],
    ),
    (
        "DOTFILES_RUN_AGENT_GUIDANCE_SETUP",
        "Agent guidance files",
        [
            HOME / "AGENTS.md",
            HOME / ".claude/CLAUDE.md",
            HOME / ".codex/AGENTS.md",
            HOME / ".cursor/AGENTS.md",
            HOME / ".config/opencode/AGENTS.md",
            HOME / ".gemini/GEMINI.md",
            HOME / ".junie/AGENTS.md",
            HOME / ".copilot/copilot-instructions.md",
            HOME / ".pi/agent/AGENTS.md",
            HOME / ".snowflake/cortex/AGENTS.md",
            HOME / "Documents/Cline/Rules/AGENTS.md",
        ],
    ),
    (
        "DOTFILES_RUN_CADDY_SETUP",
        "Caddy config",
        CADDY_CHECK_PATHS,
    ),
    (
        "DOTFILES_RUN_SKILLS_SETUP",
        "Skills manifest",
        [
            Path(os.path.dirname(__file__), "..", "configs", "skills", "skills.json"),
            *sorted(
                Path(path)
                for path in Path(
                    os.path.dirname(__file__), "..", "configs", "skills"
                ).glob("skills.*.json")
            ),
        ],
    ),
    (
        "DOTFILES_RUN_OLLAMA_DAEMON_SETUP",
        "Ollama daemon env config",
        [
            HOME / "Library/LaunchAgents/com.dotfiles.ollama-env.plist",
        ],
    ),
    (
        "DOTFILES_RUN_OMLX_SETUP",
        "oMLX settings",
        [
            HOME / ".omlx/settings.json",
            Path("/Library/LaunchDaemons/com.dotfiles.omlx-wired-limit.plist"),
        ],
    ),
    (
        "DOTFILES_RUN_OPENWEBUI_SETUP",
        "Open WebUI deployment",
        # Platform-specific: the LaunchAgent (macOS) or the systemd user unit
        # (Linux) carries the service contract; the shared venv/data/service-env
        # paths are common. Full validation lives in the dedicated section below.
        (
            [
                HOME / "Library/LaunchAgents/com.openwebui.web.plist",
            ]
            if sys.platform == "darwin"
            else [HOME / ".config/systemd/user/open-webui.service"]
        )
        + [
            HOME / ".local/share/openwebui/venv",
            HOME / ".local/share/openwebui/data",
            HOME / ".local/share/openwebui/logs",
            HOME / ".local/share/openwebui/service.env",
        ],
    ),
]


def check_acp_agents(config):
    """Warn if acpAgents entry exists but CLI not on PATH."""
    acp_agents = config.get("acpAgents", {})
    for name, entry in acp_agents.items():
        cmd = entry.get("command", "")
        if cmd and not shutil.which(cmd):
            print(
                f"  \u26a0 ACP agent '{name}' configured but command '{cmd}' not found on PATH"
            )


def validate_omlx_settings(data):
    """Return schema violations for managed oMLX settings values."""
    errors = []
    if not isinstance(data, dict):
        return ["oMLX settings root must be a JSON object"]
    sections = {}
    for name in ("server", "model", "memory", "scheduler"):
        value = data.get(name, {})
        if not isinstance(value, dict):
            errors.append(f"{name} must be a JSON object")
            value = {}
        sections[name] = value
    server = sections["server"]
    if not isinstance(server.get("host"), str):
        errors.append("server.host must be a string")
    if not isinstance(server.get("port"), int) or not 1 <= server["port"] <= 65535:
        errors.append("server.port must be an integer from 1 to 65535")
    upload_size = server.get("max_audio_upload_size")
    if upload_size is not None and not isinstance(upload_size, str):
        errors.append(
            "server.max_audio_upload_size must be a string with an explicit unit "
            "such as 128MB"
        )
    elif isinstance(upload_size, str) and not OMLX_AUDIO_UPLOAD_SIZE_PATTERN.fullmatch(
        upload_size.strip()
    ):
        errors.append(
            "server.max_audio_upload_size must be a value with an explicit unit "
            "such as 128MB; unitless values are refused (oMLX would read them as bytes)"
        )
    model_dirs = sections["model"].get("model_dirs")
    if not isinstance(model_dirs, list) or not all(
        isinstance(value, str) for value in model_dirs
    ):
        errors.append("model.model_dirs must be a list of strings")
    memory = sections["memory"]
    if memory.get("prefill_memory_guard") is not False and memory.get(
        "memory_guard_tier"
    ) not in {"safe", "balanced", "aggressive"}:
        errors.append(
            "memory.memory_guard_tier must be safe, balanced, aggressive, or absent when prefill is false (OMLX_MEMORY_GUARD)"
        )
    scheduler = sections["scheduler"]
    if (
        not isinstance(scheduler.get("max_concurrent_requests"), int)
        or scheduler["max_concurrent_requests"] < 1
    ):
        errors.append("scheduler.max_concurrent_requests must be an integer >= 1")
    cache = data.get("cache", {})
    if cache.get("ssd_cache_dir") is not None and not isinstance(
        cache.get("ssd_cache_dir"), str
    ):
        errors.append("cache.ssd_cache_dir must be a string or null")
    if not isinstance(cache.get("enabled"), bool):
        errors.append("cache.enabled must be a boolean")
    if not isinstance(cache.get("ssd_cache_max_size"), str):
        errors.append("cache.ssd_cache_max_size must be a string")
    if not isinstance(cache.get("hot_cache_max_size"), str):
        errors.append("cache.hot_cache_max_size must be a string")
    if not isinstance(cache.get("hot_cache_write_through"), bool):
        errors.append("cache.hot_cache_write_through must be a boolean")
    if (
        not isinstance(cache.get("initial_cache_blocks"), int)
        or cache["initial_cache_blocks"] < 0
    ):
        errors.append("cache.initial_cache_blocks must be an integer >= 0")
    mcp = data.get("mcp", {})
    if not isinstance(mcp.get("expose_tools"), bool):
        errors.append("mcp.expose_tools must be a boolean")
    elif mcp["expose_tools"] and os.environ.get("OMLX_MCP_EXPOSE_TOOLS") != "1":
        errors.append("mcp.expose_tools must be false unless OMLX_MCP_EXPOSE_TOOLS=1")
    if mcp.get("config_path") is not None and not isinstance(
        mcp.get("config_path"), str
    ):
        errors.append("mcp.config_path must be a string or null")
    return errors


def check_ssh_permissions():
    """Warn if SSH config or private keys have wrong permissions."""
    ssh_dir = HOME / ".ssh"
    if not ssh_dir.exists():
        return

    # Check ~/.ssh/config should be 0600
    config = ssh_dir / "config"
    if config.exists():
        stat = config.stat()
        mode = stat.st_mode & 0o777
        if mode != 0o600:
            print(f"  \u26a0 SSH config permissions: {oct(mode)} (should be 600)")

    # Check private keys should be 0600
    for key in ssh_dir.glob("id_*"):
        if key.is_file() and not key.name.endswith(".pub"):
            stat = key.stat()
            mode = stat.st_mode & 0o777
            if mode != 0o600:
                print(
                    f"  \u26a0 SSH key {key.name} permissions: {oct(mode)} (should be 600)"
                )


def check_opencode_orphan_files():
    """Detect unmanaged OpenCode config files that may override opencode.json."""
    if os.environ.get("DOTFILES_RUN_OPENCODE_SETUP", "0") != "1":
        print(
            "  \u2298 OpenCode orphan file checks "
            "(gate DOTFILES_RUN_OPENCODE_SETUP=0, skipped)"
        )
        return 0

    opencode_dir = HOME / ".config/opencode"
    managed_config_path = opencode_dir / "opencode.json"
    dcp_json_path = opencode_dir / "dcp.json"
    dcp_config_path = opencode_dir / "dcp.jsonc"
    exit_code = 0

    if dcp_json_path.exists() and dcp_config_path.exists():
        print(
            "  \u2717 Both dcp.json and dcp.jsonc exist in ~/.config/opencode/ "
            "— dcp.jsonc will shadow dcp.json. Remove dcp.json."
        )
        exit_code = 1

    if dcp_config_path.exists():
        print(f"  \u2713 DCP config: {dcp_config_path}")
        try:
            content = dcp_config_path.read_text(encoding="utf-8")
            content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
            content = re.sub(r"(^|\s)//.*$", r"\1", content, flags=re.MULTILINE)
            dcp_config = json.loads(content)
        except (json.JSONDecodeError, OSError):
            dcp_config = {}

        if not isinstance(dcp_config, dict) or "compress" not in dcp_config:
            print(
                '  \u26a0 dcp.jsonc has no "compress" configuration — '
                "DCP will use defaults."
            )
    else:
        print(f"  \u2717 DCP config: MISSING {dcp_config_path}")
        exit_code = 1

    try:
        managed_config = json.loads(managed_config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        managed_config = {}
    managed_keys = set(managed_config) if isinstance(managed_config, dict) else set()

    for path in sorted(opencode_dir.glob("*.jsonc")):
        if path.name == "dcp.jsonc":
            continue

        try:
            content = path.read_text(encoding="utf-8")
            try:
                config = json.loads(content)
            except json.JSONDecodeError:
                content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
                content = re.sub(r"(^|\s)//.*$", r"\1", content, flags=re.MULTILINE)
                config = json.loads(content)
        except (json.JSONDecodeError, OSError):
            print(f"  \u26a0 Orphan config file: {path.name} (could not parse)")
            continue

        if not isinstance(config, dict):
            print(f"  \u26a0 Orphan config file: {path.name} (not managed by dotfiles)")
            continue

        if "plugin" in config:
            print(
                f"  \u2717 OpenCode override risk: {path.name} has a 'plugin' key "
                "that replaces the managed opencode.json plugin array"
            )
            exit_code = 1

        overlapping_keys = (set(config) & managed_keys) - {"plugin"}
        if overlapping_keys:
            for key in sorted(overlapping_keys):
                print(
                    f"  \u26a0 OpenCode override: {path.name} has key '{key}' "
                    "that may override opencode.json"
                )
        elif "plugin" not in config:
            print(
                f"  \u26a0 Orphan config file: {path.name} " "(not managed by dotfiles)"
            )

    for path in sorted(opencode_dir.glob("*.bak")):
        live = opencode_dir / path.name[: -len(".bak")]
        if not live.exists():
            print(f"  \u26a0 Stale backup: {path.name} (live file gone)")
            continue
        try:
            if path.read_bytes() == live.read_bytes():
                # Backup identical to the live file: the config regenerated
                # to the same content, so the backup adds nothing. Prune it.
                path.unlink()
                continue
        except OSError:
            pass
        print(f"  \u26a0 Stale backup: {path.name}")

    return exit_code


def main():
    exit_code = 0

    for gate, description, paths in CHECKS:
        enabled = os.environ.get(gate, "0") == "1"
        if not enabled:
            print(f"  \u2298 {description} (gate {gate}=0, skipped)")
            continue

        if gate == "DOTFILES_RUN_CADDY_SETUP" and BREW_PREFIX is None:
            print(f"  \u2298 {description} (brew not found, skipped)")
            continue

        if gate == "DOTFILES_RUN_OLLAMA_DAEMON_SETUP" and sys.platform != "darwin":
            print(f"  \u2298 {description} (non-macOS, skipped)")
            continue

        if gate == "DOTFILES_RUN_OMLX_SETUP" and (
            sys.platform != "darwin" or platform.machine() != "arm64"
        ):
            # oMLX is Apple-Silicon/macOS only (mirrors script 29's check).
            print(f"  \u2298 {description} (unsupported platform for oMLX, skipped)")
            continue

        all_exist = True
        for path in paths:
            if (
                gate == "DOTFILES_RUN_OPENWEBUI_SETUP"
                and sys.platform != "darwin"
                and "Library/LaunchAgents/com.openwebui.web.plist" in str(path)
            ):
                continue
            if path.exists():
                # Only the oMLX settings JSON gets schema validation; the
                # wired-limit LaunchDaemon plist is XML, not JSON.
                if gate == "DOTFILES_RUN_OMLX_SETUP" and path.suffix == ".json":
                    try:
                        settings = json.loads(path.read_text(encoding="utf-8"))
                        for error in validate_omlx_settings(settings):
                            print(f"  \u2717 {description}: {error}")
                            all_exist = False
                    except (OSError, json.JSONDecodeError):
                        print(f"  \u2717 {description}: INVALID JSON {path}")
                        all_exist = False
                        continue
                print(f"  \u2713 {description}: {path}")
            else:
                print(f"  \u2717 {description}: MISSING {path}")
                all_exist = False

        if not all_exist:
            exit_code = 1

    # ddns-route53 multi-zone checks (only when CADDY setup is enabled)
    caddy_gate = os.environ.get("DOTFILES_RUN_CADDY_SETUP", "0") == "1"
    if caddy_gate:
        zones_config_path = Path(
            os.path.expanduser(
                os.path.expandvars(
                    os.environ.get(
                        "CADDY_ZONES_CONFIG",
                        str(HOME / ".config/caddy/ddns-zones.json"),
                    )
                )
            )
        )

        if not zones_config_path.exists():
            print(
                f"  \u2298 ddns-route53 zones config (missing {zones_config_path}, skipped)"
            )
        else:
            try:
                with open(zones_config_path, encoding="utf-8") as f:
                    zones_data = json.load(f)
            except (json.JSONDecodeError, OSError):
                print(
                    f"  \u2717 ddns-route53 zones config: could not parse {zones_config_path}"
                )
                exit_code = 1
            else:
                if not isinstance(zones_data, dict):
                    print(
                        f"  ✗ ddns-route53 zones config: root must be a JSON object ({zones_config_path})"
                    )
                    exit_code = 1
                    zones_data = {}
                zones = zones_data.get("zones", [])
                if not isinstance(zones, list):
                    print(
                        f"  \u2717 ddns-route53 zones config: 'zones' must be a list ({zones_config_path})"
                    )
                    exit_code = 1
                else:
                    if not zones:
                        print(
                            f"  \u2298 ddns-route53 zones config (no zones listed in {zones_config_path})"
                        )

                    for zone in zones:
                        if not isinstance(zone, dict):
                            print(
                                "  \u2717 ddns-route53 zone entry: invalid zone object"
                            )
                            exit_code = 1
                            continue

                        zone_id = str(zone.get("hostedZoneId", "")).strip()
                        if not zone_id:
                            print(
                                "  \u2717 ddns-route53 zone entry: missing hostedZoneId"
                            )
                            exit_code = 1
                            continue

                        config_path = (
                            HOME / ".config/ddns-route53" / f"zone-{zone_id}.yml"
                        )
                        plist_path = (
                            HOME
                            / "Library/LaunchAgents"
                            / f"com.crazymax.ddns-route53.{zone_id}.plist"
                        )

                        if config_path.exists():
                            print(f"  \u2713 ddns-route53 zone config: {config_path}")
                        else:
                            print(
                                f"  \u2717 ddns-route53 zone config: MISSING {config_path}"
                            )
                            exit_code = 1

                        if plist_path.exists():
                            print(f"  \u2713 ddns-route53 LaunchAgent: {plist_path}")
                        else:
                            print(
                                f"  \u2717 ddns-route53 LaunchAgent: MISSING {plist_path}"
                            )
                            exit_code = 1
    else:
        print(
            "  \u2298 ddns-route53 multi-zone checks (gate DOTFILES_RUN_CADDY_SETUP=0, skipped)"
        )

    # Machine-local Caddy v2 files should be present when Caddy is enabled.
    if caddy_gate:
        caddy_local_checks = [
            (HOME / ".config/caddy/ddns-zones.json", "Caddy DDNS zones"),
        ]
        for path, label in caddy_local_checks:
            if path.exists():
                print(f"  \u2713 {label}: {path}")
            else:
                print(
                    f"  \u26a0 {label}: missing {path} (machine-local; create if needed)"
                )

        caddy_auth_path = HOME / ".config/caddy/caddy-auth.conf"
        auth_valid, auth_count = validate_caddy_auth_conf(caddy_auth_path)
        if caddy_auth_path.exists() and auth_valid:
            print(
                f"  \u2713 Caddy auth config: {caddy_auth_path} ({auth_count} valid entries)"
            )
        elif not caddy_auth_path.exists():
            print(f"  \u2717 Caddy auth config: MISSING {caddy_auth_path}")
            exit_code = 1
        else:
            print(
                f"  \u2717 Caddy auth config: no valid user:hash entries in {caddy_auth_path}"
            )
            exit_code = 1

    # Parse opencode.json once for content-inspecting checks (Meridian, CodeGraph)
    opencode_json = HOME / ".config/opencode/opencode.json"
    opencode_config = None
    if opencode_json.exists():
        try:
            with open(opencode_json) as f:
                opencode_config = json.load(f)
        except (json.JSONDecodeError, OSError):
            opencode_config = None

    # OpenCode web checks (only enforced when enabled): localhost binding +
    # server block shape. External auth is handled by Caddy.
    opencode_web_gate = os.environ.get("DOTFILES_RUN_OPENCODE_WEB_SETUP", "0") == "1"
    opencode_web_plist = HOME / "Library/LaunchAgents/com.opencode.web.plist"
    if opencode_web_gate:
        if opencode_web_plist.exists():
            print(f"  \u2713 OpenCode web LaunchAgent: {opencode_web_plist}")
        else:
            print(f"  \u2717 OpenCode web LaunchAgent: MISSING {opencode_web_plist}")
            exit_code = 1

        # The web port is pinned on the LaunchAgent command (serve --port
        # "${OPENCODE_SERVER_PORT:-4096}"), not in the shared server config:
        # a fixed server.port is inherited by every opencode process (acp,
        # TUI) and collides with the web service.
        plist_port_ok = False
        if opencode_web_plist.exists():
            try:
                with open(opencode_web_plist) as f:
                    plist_text = f.read()
                plist_port_ok = (
                    "serve" in plist_text and "OPENCODE_SERVER_PORT" in plist_text
                )
            except OSError:
                plist_port_ok = False
        if plist_port_ok:
            print(
                "  \u2713 OpenCode web LaunchAgent: serve pinned to OPENCODE_SERVER_PORT"
            )
        else:
            print(
                "  \u2717 OpenCode web LaunchAgent: serve command missing --port with OPENCODE_SERVER_PORT"
            )
            exit_code = 1

        if opencode_config is not None:
            server = opencode_config.get("server", {})
            if isinstance(server, dict) and "cors" in server:
                print("  \u2713 OpenCode web server: cors configured in opencode.json")
            else:
                print("  \u2717 OpenCode web server: missing cors in opencode.json")
                exit_code = 1
        elif opencode_json.exists():
            print("  \u2717 OpenCode web server: could not parse opencode.json")
            exit_code = 1
        else:
            print("  \u2717 OpenCode web server: opencode.json not found")
            exit_code = 1
    else:
        print("  \u2298 OpenCode web (gate DOTFILES_RUN_OPENCODE_WEB_SETUP=0, skipped)")

    # Open WebUI deployment artefacts and security modes.
    openwebui_gate = os.environ.get("DOTFILES_RUN_OPENWEBUI_SETUP", "0") == "1"
    openwebui_plist = HOME / "Library/LaunchAgents/com.openwebui.web.plist"
    openwebui_unit = HOME / ".config/systemd/user/open-webui.service"
    openwebui_root = HOME / ".local/share/openwebui"
    openwebui_mode_checks = [
        (openwebui_root / "data", "Open WebUI data directory"),
        (openwebui_root / "logs", "Open WebUI logs directory"),
        (openwebui_root / "service.env", "Open WebUI service env"),
    ]
    if openwebui_gate:
        venv_binary = openwebui_root / "venv/bin/open-webui"
        if not (venv_binary.is_file() and os.access(venv_binary, os.X_OK)):
            print(
                f"  \u2717 Open WebUI executable: MISSING or not executable {venv_binary}"
            )
            exit_code = 1
        else:
            print(f"  \u2713 Open WebUI executable: {venv_binary}")
        for path, label in openwebui_mode_checks:
            if path.exists():
                mode = path.stat().st_mode & 0o777
                expected = (
                    0o700
                    if path.name in {"data", "logs"}
                    else 0o600 if path.name == "service.env" else None
                )
                if expected is not None and mode != expected:
                    print(
                        f"  \u2717 {label}: mode {oct(mode)} (expected {oct(expected)})"
                    )
                    exit_code = 1
                else:
                    print(f"  \u2713 {label}: {path}")
            else:
                print(f"  \u2717 {label}: MISSING {path}")
                exit_code = 1
        service_env = openwebui_root / "service.env"
        required_names = {
            "WEBUI_SECRET_KEY",
            "WEBUI_ADMIN_EMAIL",
            "WEBUI_ADMIN_PASSWORD",
            "OPENWEBUI_API_KEY",
        }
        if service_env.is_file():
            names = {
                line.split("=", 1)[0].strip()
                for line in service_env.read_text(encoding="utf-8").splitlines()
                if "=" in line and line.split("=", 1)[0].strip()
            }
            missing_names = required_names - names
            if missing_names:
                print(
                    f"  \u2717 Open WebUI service env: missing required key names {sorted(missing_names)}"
                )
                exit_code = 1
            else:
                print("  \u2713 Open WebUI service env: required key names present")
        if openwebui_plist.is_file():
            plist_mode = openwebui_plist.stat().st_mode & 0o777
            plist_text = openwebui_plist.read_text(encoding="utf-8")
            if plist_mode != 0o600:
                print(
                    f"  \u2717 Open WebUI plist: mode {oct(plist_mode)} (expected 0o600)"
                )
                exit_code = 1
            try:
                plist = plistlib.loads(plist_text.encode())
                env_keys = set(plist.get("EnvironmentVariables", {}))
                if env_keys != {"DATA_DIR", "GLOBAL_LOG_LEVEL"}:
                    print(
                        f"  \u2717 Open WebUI plist: unexpected EnvironmentVariables keys {sorted(env_keys)}"
                    )
                    exit_code = 1
                if any(name in plist_text for name in required_names):
                    print(
                        "  \u2717 Open WebUI plist: secret or bootstrap variable name present"
                    )
                    exit_code = 1
                if (
                    "--noprofile --norc" not in plist_text
                    or "env -i" not in plist_text
                    or "bash -lc" in plist_text
                    or "DATA_DIR=" not in plist_text
                    or any(
                        name in plist_text
                        for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")
                    )
                ):
                    print("  \u2717 Open WebUI plist: unsafe login-shell wrapper")
                    exit_code = 1
            except (OSError, plistlib.InvalidFileException, ValueError):
                print("  \u2717 Open WebUI plist: invalid property list")
                exit_code = 1
        if sys.platform != "darwin":
            if not openwebui_unit.is_file():
                print(f"  \u2717 Open WebUI systemd unit: MISSING {openwebui_unit}")
                exit_code = 1
            else:
                unit_text = openwebui_unit.read_text(encoding="utf-8")
                if (
                    "DATA_DIR=" not in unit_text
                    or "--noprofile --norc" not in unit_text
                    or "env -i" not in unit_text
                    or "WEBUI_SECRET_KEY" in unit_text
                ):
                    print("  \u2717 Open WebUI systemd unit: service contract mismatch")
                    exit_code = 1
        logs_dir = openwebui_root / "logs"
        if logs_dir.exists() and (logs_dir.stat().st_mode & 0o777) != 0o700:
            print(
                f"  \u2717 Open WebUI logs directory: mode {oct(logs_dir.stat().st_mode & 0o777)} (expected 0o700)"
            )
            exit_code = 1
        caddyfile = CADDY_CHECK_PATHS[0] if CADDY_CHECK_PATHS else None
        if caddyfile and caddyfile.exists():
            caddy_text = caddyfile.read_text(encoding="utf-8")
            port = os.environ.get("OPENWEBUI_PORT", "8080")
            public = os.environ.get("DOTFILES_OPENWEBUI_PUBLIC", "0") == "1"
            access_mode = os.environ.get("CADDY_ACCESS", "localhost").strip().lower()
            matcher_required = access_mode == "lan" or not public
            chat_blocks = []
            lines = caddy_text.splitlines()
            for index, line in enumerate(lines):
                if "chat." not in line or not line.rstrip().endswith("{"):
                    continue
                depth = 0
                block = []
                for candidate in lines[index:]:
                    depth += candidate.count("{") - candidate.count("}")
                    block.append(candidate)
                    if depth == 0:
                        break
                chat_blocks.append("\n".join(block))
            functional_blocks = [
                block
                for block in chat_blocks
                if f"reverse_proxy 127.0.0.1:{port}" in block
            ]
            policies_ok = bool(functional_blocks) and all(
                ("@not_lan not remote_ip private_ranges" in block) == matcher_required
                for block in functional_blocks
            )
            if functional_blocks and policies_ok:
                print(f"  \u2713 Open WebUI Caddy site: {caddyfile}")
            else:
                print(
                    f"  \u2717 Open WebUI Caddy site: missing chat.* site in {caddyfile}"
                )
                exit_code = 1
        else:
            print("  \u2717 Open WebUI Caddy site: Caddyfile missing")
            exit_code = 1
    else:
        wrong_platform_service = (
            openwebui_plist if sys.platform == "darwin" else openwebui_unit
        )
        if wrong_platform_service.exists():
            print(
                f"  \u2717 Open WebUI service remains while gate is off: {wrong_platform_service}"
            )
            exit_code = 1
        else:
            print(
                "  \u2298 Open WebUI (gate DOTFILES_RUN_OPENWEBUI_SETUP=0, LaunchAgent absent)"
            )

    terminal_gate = (
        openwebui_gate
        and os.environ.get("DOTFILES_RUN_OPENWEBUI_TERMINAL_SETUP", "0") == "1"
    )
    terminal_plist = HOME / "Library/LaunchAgents/com.openwebui.terminal.plist"
    terminal_root = openwebui_root / "terminal-workspace"
    terminal_env = openwebui_root / "terminal.env"
    terminal_config = openwebui_root / "terminal.toml"
    terminal_binary = openwebui_root / "venv/bin/open-terminal"
    if terminal_gate:
        terminal_checks = [
            (terminal_binary, "Open Terminal executable"),
            (terminal_root, "Open Terminal workspace"),
            (terminal_env, "Open Terminal service env"),
            (terminal_config, "Open Terminal config"),
            (terminal_plist, "Open Terminal LaunchAgent"),
        ]
        for path, label in terminal_checks:
            if not path.exists():
                print(f"  \u2717 {label}: MISSING {path}")
                exit_code = 1
        if not (terminal_binary.is_file() and os.access(terminal_binary, os.X_OK)):
            print(
                f"  \u2717 Open Terminal executable: not executable {terminal_binary}"
            )
            exit_code = 1
        if terminal_root.exists() and (terminal_root.stat().st_mode & 0o777) != 0o700:
            print("  \u2717 Open Terminal workspace: expected mode 0o700")
            exit_code = 1
        if terminal_env.is_file():
            if terminal_env.stat().st_mode & 0o777 != 0o600:
                print("  \u2717 Open Terminal service env: expected mode 0o600")
                exit_code = 1
            names = {
                line.split("=", 1)[0].strip()
                for line in terminal_env.read_text(encoding="utf-8").splitlines()
                if "=" in line and line.split("=", 1)[0].strip()
            }
            required = {
                "OPEN_TERMINAL_API_KEY",
                "OPEN_TERMINAL_FILE_BROWSER_ROOT",
                "OPENWEBUI_TERMINAL_PORT",
            }
            if not required <= names:
                print("  \u2717 Open Terminal service env: required key names missing")
                exit_code = 1
        if terminal_plist.is_file():
            plist_mode = terminal_plist.stat().st_mode & 0o777
            plist_text = terminal_plist.read_text(encoding="utf-8")
            if plist_mode != 0o600:
                print("  \u2717 Open Terminal plist: expected mode 0o600")
                exit_code = 1
            if any(
                name in plist_text
                for name in (
                    "OPEN_TERMINAL_API_KEY",
                    "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY",
                    "WEBUI_SECRET_KEY",
                )
            ):
                print("  \u2717 Open Terminal plist: API key name/value present")
                exit_code = 1
            try:
                plist = plistlib.loads(plist_text.encode())
                env_keys = set(plist.get("EnvironmentVariables", {}))
                if env_keys != {"OPEN_TERMINAL_FILE_BROWSER_ROOT", "GLOBAL_LOG_LEVEL"}:
                    print("  \u2717 Open Terminal plist: unexpected environment keys")
                    exit_code = 1
                if (
                    "--noprofile --norc" not in plist_text
                    or "env -i" not in plist_text
                    or "bash -lc" in plist_text
                    or "OPEN_TERMINAL_FILE_BROWSER_ROOT=" not in plist_text
                ):
                    print("  \u2717 Open Terminal plist: unsafe login-shell wrapper")
                    exit_code = 1
            except (OSError, plistlib.InvalidFileException, ValueError):
                print("  \u2717 Open Terminal plist: invalid property list")
                exit_code = 1
        if terminal_config.is_file():
            config_mode = terminal_config.stat().st_mode & 0o777
            config_text = terminal_config.read_text(encoding="utf-8")
            expected_port = os.environ.get("OPENWEBUI_TERMINAL_PORT", "8123")
            expected_root = str(terminal_root)
            if config_mode != 0o600:
                print("  \u2717 Open Terminal config: expected mode 0o600")
                exit_code = 1
            if (
                'host = "127.0.0.1"' not in config_text
                or f"port = {expected_port}" not in config_text
                or f'file_browser_root = "{expected_root}"' not in config_text
            ):
                print(
                    "  \u2717 Open Terminal config: host/port/workspace settings mismatch"
                )
                exit_code = 1
    elif terminal_plist.exists():
        print(
            f"  \u2717 Open Terminal LaunchAgent remains while sub-gate is off: {terminal_plist}"
        )
        exit_code = 1
    else:
        print("  \u2298 Open Terminal (main/sub-gate disabled, LaunchAgent absent)")

    computer_gate = (
        openwebui_gate
        and os.environ.get("DOTFILES_RUN_OPENWEBUI_COMPUTER_SETUP", "0") == "1"
    )
    computer_root = HOME / ".local/share/cptr"
    computer_plist = HOME / "Library/LaunchAgents/com.openwebui.computer.plist"
    computer_venv = computer_root / "venv/bin/cptr"
    computer_data = computer_root / "data"
    computer_env = computer_root / "service.env"
    if computer_gate:
        for path, label in (
            (computer_venv, "cptr executable"),
            (computer_data, "cptr data directory"),
            (computer_env, "cptr service env"),
            (computer_plist, "cptr LaunchAgent"),
        ):
            if not path.exists():
                print(f"  \u2717 {label}: MISSING {path}")
                exit_code = 1
        if not (computer_venv.is_file() and os.access(computer_venv, os.X_OK)):
            print(f"  \u2717 cptr executable: not executable {computer_venv}")
            exit_code = 1
        if computer_data.exists() and (computer_data.stat().st_mode & 0o777) != 0o700:
            print("  \u2717 cptr data directory: expected mode 0o700")
            exit_code = 1
        if computer_env.is_file() and (computer_env.stat().st_mode & 0o777) != 0o600:
            print("  \u2717 cptr service env: expected mode 0o600")
            exit_code = 1
        computer_logs = computer_root / "logs"
        if not computer_logs.is_dir():
            print(
                "  \u2717 cptr logs directory: missing (may hold token-bearing setup log)"
            )
            exit_code = 1
        elif (computer_logs.stat().st_mode & 0o777) != 0o700:
            print("  \u2717 cptr logs directory: expected mode 0o700")
            exit_code = 1
        expected_cptr_data = str(computer_root / "data")
        expected_cptr_port = os.environ.get("OPENWEBUI_COMPUTER_PORT", "8124")
        if computer_env.is_file():
            env_values = {}
            for line in computer_env.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    env_values[key] = value.strip().strip("'\"")
            if set(env_values) != {"CPTR_DATA_DIR", "OPENWEBUI_COMPUTER_PORT"}:
                print("  \u2717 cptr service env: unexpected key names")
                exit_code = 1
            if env_values.get("CPTR_DATA_DIR") != expected_cptr_data:
                print("  \u2717 cptr service env: CPTR_DATA_DIR is not isolated")
                exit_code = 1
            if env_values.get("OPENWEBUI_COMPUTER_PORT") != expected_cptr_port:
                print("  \u2717 cptr service env: OPENWEBUI_COMPUTER_PORT drift")
                exit_code = 1
        if computer_plist.is_file():
            plist_mode = computer_plist.stat().st_mode & 0o777
            plist_text = computer_plist.read_text(encoding="utf-8")
            if plist_mode != 0o600:
                print("  \u2717 cptr plist: expected mode 0o600")
                exit_code = 1
            if any(
                name in plist_text
                for name in (
                    "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY",
                    "WEBUI_SECRET_KEY",
                    "GEMINI_",
                    "GOOGLE_",
                    "OPENROUTER_",
                    "MERIDIAN_",
                    "OLLAMA_API",
                    "GITHUB",
                    "GH_",
                )
            ):
                print("  \u2717 cptr plist: provider secret present")
                exit_code = 1
            if re.search(r"sk-[A-Za-z0-9_-]+", plist_text):
                print("  \u2717 cptr plist: sk- credential present")
                exit_code = 1
            if (
                "CPTR_DATA_DIR=" not in plist_text
                or "OPENWEBUI_COMPUTER_PORT=" not in plist_text
                or "env -i" not in plist_text
                or "--noprofile --norc" not in plist_text
                or "bash -lc" in plist_text
            ):
                print("  \u2717 cptr plist: unsafe or incomplete scrubbed wrapper")
                exit_code = 1
            try:
                plist = plistlib.loads(plist_text.encode())
                if "EnvironmentVariables" in plist:
                    print("  \u2717 cptr plist: EnvironmentVariables must be absent")
                    exit_code = 1
                arguments = " ".join(
                    str(item) for item in plist.get("ProgramArguments", [])
                )
                if (
                    "--host 127.0.0.1" not in arguments
                    or "--headless" not in arguments
                    or "--reload" in arguments
                ):
                    print("  \u2717 cptr plist: launch contract mismatch")
                    exit_code = 1
                env_match = re.search(r"env -i (.*?) /bin/bash", arguments)
                if not env_match:
                    print("  \u2717 cptr plist: env-i allowlist missing")
                    exit_code = 1
                else:
                    env_tokens = shlex.split(env_match.group(1))
                    env_values = {
                        token.split("=", 1)[0]: token.split("=", 1)[1]
                        for token in env_tokens
                        if "=" in token
                    }
                    env_keys = set(env_values)
                    if env_keys != {
                        "HOME",
                        "PATH",
                        "CPTR_DATA_DIR",
                        "OPENWEBUI_COMPUTER_PORT",
                    }:
                        print(
                            "  \u2717 cptr plist: wrapper environment allowlist mismatch"
                        )
                        exit_code = 1
                    if env_values.get("CPTR_DATA_DIR") != expected_cptr_data:
                        print("  \u2717 cptr plist: CPTR_DATA_DIR is not isolated")
                        exit_code = 1
                    if env_values.get("OPENWEBUI_COMPUTER_PORT") != expected_cptr_port:
                        print("  \u2717 cptr plist: wrapper port drift")
                        exit_code = 1
            except (OSError, plistlib.InvalidFileException, ValueError):
                print("  \u2717 cptr plist: invalid property list")
                exit_code = 1
    elif computer_plist.exists():
        print(
            f"  \u2717 cptr LaunchAgent remains while sub-gate is off: {computer_plist}"
        )
        exit_code = 1
    else:
        print("  \u2298 cptr (main/sub-gate disabled, LaunchAgent absent)")

    litellm_gate = os.environ.get("DOTFILES_RUN_LITELLM_SETUP", "0") == "1"
    litellm_root = HOME / ".local/share/litellm"
    litellm_plist = HOME / "Library/LaunchAgents/com.litellm.proxy.plist"
    expected_litellm_port = os.environ.get("LITELLM_PORT", "4000")
    if litellm_gate:
        litellm_paths = [
            (litellm_root / "venv/bin/litellm", "LiteLLM executable"),
            (litellm_root / "config.yaml", "LiteLLM config"),
            (litellm_root / "service.env", "LiteLLM service env"),
            (litellm_root / "data", "LiteLLM data directory"),
            (litellm_root / "logs", "LiteLLM logs directory"),
            (litellm_plist, "LiteLLM LaunchAgent"),
        ]
        for path, label in litellm_paths:
            if not path.exists():
                print(f"  \u2717 {label}: MISSING {path}")
                exit_code = 1
        for path in (litellm_root / "data", litellm_root / "logs"):
            if path.exists() and (path.stat().st_mode & 0o777) != 0o700:
                print(f"  \u2717 LiteLLM directory mode: {path}")
                exit_code = 1
        service_env = litellm_root / "service.env"
        litellm_env_values = {}
        if service_env.is_file():
            for line in service_env.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    litellm_env_values[key.strip()] = value.strip().strip("'\"")
            if service_env.stat().st_mode & 0o777 != 0o600:
                print("  \u2717 LiteLLM service env: key schema or mode mismatch")
                exit_code = 1
        if (litellm_root / "config.yaml").is_file():
            config_text = (litellm_root / "config.yaml").read_text(encoding="utf-8")
            refs = set(re.findall(r"os\.environ/([A-Z][A-Z0-9_]*)", config_text))
            expected_env = {"LITELLM_MASTER_KEY", "LITELLM_PORT"} | refs
            if set(litellm_env_values) != expected_env:
                print(
                    "  \u2717 LiteLLM service env: provider allowlist does not match config refs"
                )
                exit_code = 1
            master_key = litellm_env_values.get("LITELLM_MASTER_KEY", "")
            if not master_key.startswith("sk-") or len(master_key) < 16:
                print("  \u2717 LiteLLM service env: invalid master-key shape")
                exit_code = 1
            if litellm_env_values.get("LITELLM_PORT") != expected_litellm_port:
                print("  \u2717 LiteLLM service env: LITELLM_PORT drift")
                exit_code = 1
            api_key_lines = [
                line.strip()
                for line in config_text.splitlines()
                if line.strip().startswith("api_key:")
            ]
            if any(
                not (
                    line.split(":", 1)[1].strip().startswith("os.environ/")
                    or line.split(":", 1)[1].strip() == "none"
                )
                for line in api_key_lines
            ):
                print("  \u2717 LiteLLM config: inline api_key detected")
                exit_code = 1
            if (
                "telemetry: false" not in config_text
                or "master_key: os.environ/LITELLM_MASTER_KEY" not in config_text
            ):
                print("  \u2717 LiteLLM config: telemetry/master-key policy mismatch")
                exit_code = 1
        if litellm_plist.is_file():
            plist_mode = litellm_plist.stat().st_mode & 0o777
            plist_text = litellm_plist.read_text(encoding="utf-8")
            if (
                plist_mode != 0o600
                or "LITELLM_MASTER_KEY" in plist_text
                or "EnvironmentVariables" in plist_text
            ):
                print(
                    "  \u2717 LiteLLM plist: mode/secrets/environment contract mismatch"
                )
                exit_code = 1
            if (
                "env -i" not in plist_text
                or "--noprofile --norc" not in plist_text
                or "bash -lc" in plist_text
            ):
                print("  \u2717 LiteLLM plist: unsafe wrapper")
                exit_code = 1
            try:
                plist = plistlib.loads(plist_text.encode())
                arguments = " ".join(
                    str(item) for item in plist.get("ProgramArguments", [])
                )
                match = re.search(r"env -i (.*?) /bin/bash", arguments)
                if not match or {
                    token.split("=", 1)[0]
                    for token in shlex.split(match.group(1))
                    if "=" in token
                } != {"HOME", "PATH"}:
                    print(
                        "  \u2717 LiteLLM plist: wrapper environment allowlist mismatch"
                    )
                    exit_code = 1
                if (
                    "--config" not in arguments
                    or str(litellm_root / "config.yaml") not in arguments
                ):
                    print("  \u2717 LiteLLM plist: config path missing")
                    exit_code = 1
                if (
                    "--host 127.0.0.1" not in arguments
                    or "--num_workers 1" not in arguments
                ):
                    print("  \u2717 LiteLLM plist: launch contract mismatch")
                    exit_code = 1
                port_match = re.search(r'--port "?([^"\s]+)"?', arguments)
                if not port_match or port_match.group(1) != expected_litellm_port:
                    print("  \u2717 LiteLLM plist: launch port drift")
                    exit_code = 1
            except (OSError, plistlib.InvalidFileException, ValueError):
                print("  \u2717 LiteLLM plist: invalid property list")
                exit_code = 1
    elif litellm_plist.exists():
        print(
            f"  \u2717 LiteLLM LaunchAgent remains while gate is off: {litellm_plist}"
        )
        exit_code = 1
    else:
        print("  \u2298 LiteLLM (gate disabled, LaunchAgent absent)")

    backup_timer_gate = (
        openwebui_gate
        and os.environ.get("DOTFILES_RUN_OPENWEBUI_BACKUP_SCHEDULE", "0") == "1"
        and sys.platform == "darwin"
    )
    backup_timer = HOME / "Library/LaunchAgents/com.dotfiles.openwebui.backup.plist"
    if backup_timer_gate:
        if not backup_timer.is_file():
            print(f"  \u2717 Open WebUI backup timer: MISSING {backup_timer}")
            exit_code = 1
        else:
            timer_mode = backup_timer.stat().st_mode & 0o777
            if timer_mode != 0o600:
                print("  \u2717 Open WebUI backup timer: expected mode 0o600")
                exit_code = 1
            try:
                timer = plistlib.loads(
                    backup_timer.read_text(encoding="utf-8").encode()
                )
                interval = timer.get("StartCalendarInterval")
                if not isinstance(interval, dict) or interval != {
                    "Hour": 3,
                    "Minute": 0,
                }:
                    print(
                        "  \u2717 Open WebUI backup timer: expected StartCalendarInterval 03:00 exactly"
                    )
                    exit_code = 1
                if any(
                    key in timer for key in ("RunAtLoad", "KeepAlive", "StartInterval")
                ):
                    print(
                        "  \u2717 Open WebUI backup timer: continuous-run keys must be absent"
                    )
                    exit_code = 1
                arguments = " ".join(
                    str(item) for item in timer.get("ProgramArguments", [])
                )
                if (
                    "--noprofile --norc" not in arguments
                    or "env -i" not in arguments
                    or "bash -lc" in arguments
                ):
                    print("  \u2717 Open WebUI backup timer: unsafe shell wrapper")
                    exit_code = 1
                timer_env_match = re.search(r"env -i (.*?) /bin/bash", arguments)
                if not timer_env_match or {
                    token.split("=", 1)[0]
                    for token in shlex.split(timer_env_match.group(1))
                    if "=" in token
                } != {"HOME", "PATH"}:
                    print(
                        "  \u2717 Open WebUI backup timer: wrapper environment allowlist mismatch"
                    )
                    exit_code = 1
                log_path = str(HOME / ".local/share/openwebui/logs/backup-timer.log")
                timer_text = backup_timer.read_text(encoding="utf-8")
                # The deployed timer invokes make in the *deployed* checkout
                # (chezmoi source dir), which can differ from the checkout
                # running this verifier — resolve it via chezmoi, falling back
                # to this file's checkout.
                repo_path = None
                chezmoi_bin = shutil.which("chezmoi")
                if chezmoi_bin:
                    # Fixed-argument invocation of a resolved binary with no
                    # operator-controlled input — B603/B607 do not apply.
                    probe = subprocess.run(  # nosec B603, B607
                        [chezmoi_bin, "source-path"],
                        capture_output=True,
                        text=True,
                    )
                    if probe.returncode == 0 and probe.stdout.strip():
                        repo_path = probe.stdout.strip()
                if not repo_path:
                    repo_path = str(Path(__file__).resolve().parent.parent)
                if (
                    "openwebui-backup" not in arguments
                    or repo_path not in arguments
                    or log_path not in timer_text
                ):
                    print(
                        "  \u2717 Open WebUI backup timer: program or log contract mismatch"
                    )
                    exit_code = 1
            except (OSError, plistlib.InvalidFileException, ValueError):
                print("  \u2717 Open WebUI backup timer: invalid property list")
                exit_code = 1
    elif backup_timer.exists():
        print(
            f"  \u2717 Open WebUI backup timer remains while schedule gate is off: {backup_timer}"
        )
        exit_code = 1
    else:
        print("  \u2298 Open WebUI backup timer (gate disabled, plist absent)")

    # Optional Meridian plugin check (only enforced when enabled)
    meridian_gate = os.environ.get("DOTFILES_RUN_MERIDIAN_SETUP", "0") == "1"
    if meridian_gate:
        if opencode_config is not None:
            plugins = opencode_config.get("plugin", [])
            meridian_present = any(
                isinstance(plugin, str) and "meridian.ts" in plugin
                for plugin in plugins
            )
            if meridian_present:
                print("  \u2713 Meridian plugin: registered in opencode.json")
            else:
                print("  \u2717 Meridian plugin: not found in opencode.json")
                exit_code = 1
        elif opencode_json.exists():
            print("  \u2717 Meridian plugin: could not parse opencode.json")
            exit_code = 1
        else:
            print("  \u2717 Meridian plugin: opencode.json not found")
            exit_code = 1
    else:
        print("  \u2298 Meridian plugin (gate DOTFILES_RUN_MERIDIAN_SETUP=0, skipped)")

    # Optional Junie model profiles check (only enforced when enabled)
    junie_gate = os.environ.get("DOTFILES_RUN_JUNIE_CLI_SETUP", "0") == "1"
    junie_models_dir = HOME / ".junie" / "models"
    if junie_gate:
        if junie_models_dir.exists() and junie_models_dir.is_dir():
            has_models = any(junie_models_dir.iterdir())
            if has_models:
                print(f"  \u2713 Junie model profiles: {junie_models_dir}")
            else:
                print(f"  \u2717 Junie model profiles: empty {junie_models_dir}")
                exit_code = 1
        else:
            print(f"  \u2717 Junie model profiles: MISSING {junie_models_dir}")
            exit_code = 1
    else:
        print(
            "  \u2298 Junie model profiles (gate DOTFILES_RUN_JUNIE_CLI_SETUP=0, skipped)"
        )

    # CodeGraph MCP registration check (reuses cached opencode_config)
    codegraph_gate = os.environ.get("DOTFILES_RUN_CODEGRAPH_SETUP", "0") == "1"
    if codegraph_gate:
        if opencode_config is not None:
            mcps = opencode_config.get("mcp", {})
            if "codegraph" in mcps:
                print(f"  \u2713 CodeGraph MCP: registered in opencode.json")
            else:
                print(f"  \u2717 CodeGraph MCP: not found in opencode.json")
                exit_code = 1
        elif opencode_json.exists():
            print(f"  \u2717 CodeGraph MCP: could not parse opencode.json")
            exit_code = 1
        else:
            print(f"  \u2717 CodeGraph MCP: opencode.json not found")
            exit_code = 1
    else:
        print(f"  \u2298 CodeGraph MCP (gate DOTFILES_RUN_CODEGRAPH_SETUP=0, skipped)")

    # OpenCode orphan config check (only enforced when OpenCode setup is enabled)
    exit_code = max(exit_code, check_opencode_orphan_files())

    # ACP agent CLI availability check (runs whenever opencode.json is parseable)
    if opencode_config is not None:
        check_acp_agents(opencode_config)

    # SSH permissions drift check (always runs — security-critical)
    check_ssh_permissions()

    if exit_code == 0:
        print("\nAll enabled features have their output files.")
    else:
        print(
            "\nSome enabled features are missing output files. Run 'make configure' to regenerate."
        )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
