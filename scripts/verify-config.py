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
import subprocess
from pathlib import Path
from typing import Optional

HOME = Path.home()


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
        "DOTFILES_RUN_SMALLCODE_SETUP",
        "SmallCode config",
        [
            HOME / ".config/smallcode/config.toml",
            HOME / ".config/smallcode/.env",
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
        ],
    ),
    (
        "DOTFILES_RUN_CADDY_SETUP",
        "Caddy config",
        CADDY_CHECK_PATHS,
    ),
]


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

        all_exist = True
        for path in paths:
            if path.exists():
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
    opencode_web_gate = os.environ.get("DOTFILES_RUN_OPENCODE_WEB", "0") == "1"
    opencode_web_plist = HOME / "Library/LaunchAgents/com.opencode.web.plist"
    if opencode_web_gate:
        if opencode_web_plist.exists():
            print(f"  \u2713 OpenCode web LaunchAgent: {opencode_web_plist}")
        else:
            print(f"  \u2717 OpenCode web LaunchAgent: MISSING {opencode_web_plist}")
            exit_code = 1

        if opencode_config is not None:
            server = opencode_config.get("server", {})
            if isinstance(server, dict) and "port" in server and "cors" in server:
                print(
                    "  \u2713 OpenCode web server: port and cors configured in opencode.json"
                )
            else:
                print(
                    "  \u2717 OpenCode web server: missing port and/or cors in opencode.json"
                )
                exit_code = 1
        elif opencode_json.exists():
            print("  \u2717 OpenCode web server: could not parse opencode.json")
            exit_code = 1
        else:
            print("  \u2717 OpenCode web server: opencode.json not found")
            exit_code = 1
    else:
        print("  \u2298 OpenCode web (gate DOTFILES_RUN_OPENCODE_WEB=0, skipped)")

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

    if exit_code == 0:
        print("\nAll enabled features have their output files.")
    else:
        print(
            "\nSome enabled features are missing output files. Run 'make configure' to regenerate."
        )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
