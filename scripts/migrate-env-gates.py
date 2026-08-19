#!/usr/bin/env python3
"""Migrate ~/.env gate names to the current DOTFILES_RUN_*_SETUP scheme.

This script performs a one-way migration of deprecated gate names to their
current counterparts, preserving existing values and optionally inheriting
values for newly-split gates. It is idempotent: running it twice is a no-op.

Usage:
    scripts/migrate-env-gates.py              # Migrate ~/.env
    scripts/migrate-env-gates.py --dry-run     # Preview changes without writing
    scripts/migrate-env-gates.py --env /path    # Use a different env file

Design:
    - Conservative parser: extracts KEY='VALUE' assignments without evaluating
      shell code. Never prints secret values.
    - Renames: replaces old gate key with new gate key on the same line,
      preserving the value and comment.
    - Inherits: for newly-split gates (e.g., MOZART split from MCP_SETUP),
      if the new gate is absent, it inherits the value of its predecessor.
    - Removes: deprecated gates that have no current equivalent are commented
      out with a migration note.
    - Backs up ~/.env to ~/.env.bak before writing.

When to run:
    Run after pulling changes that rename DOTFILES_RUN_* gates. The migration
    is safe and idempotent. After migrating, run `make reset && make deploy`
    to apply the new gate values.

Adding future migrations:
    Update the MIGRATIONS list below with (old_key, new_key, inherit_from) tuples.
    - old_key: deprecated gate name to remove
    - new_key: current gate name to add/rename to
    - inherit_from: if new_key is absent, inherit the value from this gate
      (set to None if no inheritance)
    The script handles the rest automatically.
"""

import argparse
import os
import re
import sys
from pathlib import Path

# --- Migration definitions ---------------------------------------------------
# Each entry: (old_key, new_key, inherit_from)
# - old_key is removed (renamed to new_key if new_key absent)
# - if new_key absent and inherit_from set, new_key inherits inherit_from's value
# - if old_key absent, skip
#
# v1→v2 Caddy migration notes:
# - CADDY_ALLOW_PUBLIC + CADDY_LOCALHOST_ACCESS collapse into CADDY_ACCESS
# - v1 public exposure maps to CADDY_ACCESS='public'
# - v1 defaults and localhost-only modes map to CADDY_ACCESS='localhost'
# - v1 auth/zone env vars are removed because they now live in config files
MIGRATIONS = [
    # Pi is a new opt-in integration; its documented settings are preserved.
    ("DOTFILES_RUN_PI", "DOTFILES_RUN_PI_SETUP", None),
    # Gate renames (value preserved, key changed)
    ("DOTFILES_RUN_MACOS_DEFAULTS", "DOTFILES_RUN_MACOS_DEFAULTS_SETUP", None),
    ("DOTFILES_RUN_MACOS_SECURITY", "DOTFILES_RUN_MACOS_SECURITY_SETUP", None),
    ("DOTFILES_RUN_INSTALL_PACKAGES", "DOTFILES_RUN_PACKAGES_SETUP", None),
    ("DOTFILES_RUN_MERIDIAN_LAUNCHD", "DOTFILES_RUN_MERIDIAN_SETUP", None),
    ("DOTFILES_RUN_OPENCODE_PLUGINS_SETUP", "DOTFILES_RUN_OPENCODE_TOOLS_SETUP", None),
    # Gate splits (new gate inherits from shared predecessor if absent)
    (
        "DOTFILES_RUN_MOZART_SETUP_OLD",
        "DOTFILES_RUN_MOZART_SETUP",
        "DOTFILES_RUN_MCP_SETUP",
    ),
    ("DOTFILES_RUN_CODEGRAPH_SETUP_OLD", "DOTFILES_RUN_CODEGRAPH_SETUP", None),
    (
        "DOTFILES_RUN_AGENT_GUIDANCE_SETUP_OLD",
        "DOTFILES_RUN_AGENT_GUIDANCE_SETUP",
        None,
    ),
    ("DOTFILES_RUN_SECRETS_SETUP_OLD", "DOTFILES_RUN_SECRETS_SETUP", None),
    # Secrets gate split: DOTFILES_RUN_OPENCODE_SETUP still controls script 16,
    # so we inherit from it into DOTFILES_RUN_SECRETS_SETUP without renaming.
    (
        "DOTFILES_RUN_SECRETS_SETUP_OLD",
        "DOTFILES_RUN_SECRETS_SETUP",
        "DOTFILES_RUN_OPENCODE_SETUP",
    ),
    # OpenCode web gate: add _SETUP suffix for consistency
    ("DOTFILES_RUN_OPENCODE_WEB", "DOTFILES_RUN_OPENCODE_WEB_SETUP", None),
    # Caddy gate split: retain CADDY_SETUP for Caddy and inherit it into the
    # independent DDNS, ACME, and Plannotator paste gates when absent.
    (
        "DOTFILES_RUN_CADDY_SETUP",
        "DOTFILES_RUN_DDNS_SETUP",
        "DOTFILES_RUN_CADDY_SETUP",
    ),
    (
        "DOTFILES_RUN_CADDY_SETUP",
        "DOTFILES_RUN_ACME_SETUP",
        "DOTFILES_RUN_CADDY_SETUP",
    ),
    (
        "DOTFILES_RUN_CADDY_SETUP",
        "DOTFILES_RUN_PLANNOTATOR_PASTE_SETUP",
        "DOTFILES_RUN_CADDY_SETUP",
    ),
]

# Removed integrations are deleted from ~/.env rather than retained as
# commented-out settings, so they cannot be rediscovered by future tooling.
REMOVED_ENV_VARS = {"DOTFILES_RUN_SMALLCODE_SETUP", "DOTFILES_SMALLCODE_TIER"}
REMOVED_ENV_PREFIXES = ("SMALLCODE_",)

CADDY_V1_REMOVALS = {
    "CADDY_BASIC_AUTH_USERS": "moved to caddy-auth.conf",
    "CADDY_BASIC_AUTH_HASH": "moved to caddy-auth.conf",
    "CADDY_BASIC_AUTH_USER": "moved to caddy-auth.conf",
    "CADDY_DOMAINS": "auto-derived from ddns-zones.json",
    "DDNS_RECORD_NAME": "moved to ddns-zones.json",
    "DDNS_RECORD_TTL": "moved to ddns-zones.json",
    "ROUTE53_HOSTED_ZONE_ID": "moved to ddns-zones.json",
}

CADDY_V1_REMOVALS_DELETE = {
    "OPENCODE_SERVER_PASSWORD",
    "OPENCODE_SERVER_USERNAME",
}

# Assignment parser: matches KEY='VALUE' or # KEY='VALUE'
ASSIGNMENT_RE = re.compile(r"^(\s*)(#?\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def parse_env_line(line):
    """Parse an env line into (indent, comment_prefix, key, value, trailing)."""
    match = ASSIGNMENT_RE.match(line.rstrip("\n"))
    if not match:
        return None
    indent, comment_prefix, key, value = match.groups()
    return indent, comment_prefix, key, value


def read_env_lines(path):
    """Read all lines from an env file, returning list of (line, parsed_or_None)."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return f.readlines()


def normalize_env_value(value):
    """Return an env scalar without surrounding single/double quotes."""
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped


def get_active_value(lines, key):
    """Return the first active value for key, or None if absent."""
    for line in lines:
        parsed = parse_env_line(line)
        if parsed and not parsed[1].strip().startswith("#") and parsed[2] == key:
            return parsed[3]
    return None


def format_caddy_access_line(indent, value):
    """Format a quoted CADDY_ACCESS assignment."""
    return f"{indent}CADDY_ACCESS='{value}'\n"


def comment_migrated_line(indent, key, value, note):
    """Format a commented-out migrated env line."""
    return f"{indent}# {key}={value}  # {note}\n"


def get_active_keys(lines):
    """Return set of active (uncommented) keys in the env file."""
    keys = set()
    for line in lines:
        parsed = parse_env_line(line)
        if parsed and not parsed[1].strip().startswith("#"):
            keys.add(parsed[2])
    return keys


def migrate_env(lines, dry_run=False):
    """Migrate env lines according to MIGRATIONS. Returns (new_lines, changes)."""
    active_keys = get_active_keys(lines)
    changes = []
    migrated_keys = {}  # old_key → new_key

    # Build rename map from MIGRATIONS (only for entries where old_key exists)
    rename_map = {}
    inherit_map = {}
    for old_key, new_key, inherit_from in MIGRATIONS:
        # A split entry uses the existing gate as its inheritance source; it
        # must remain in place rather than being renamed to the sub-gate.
        if old_key != inherit_from:
            rename_map[old_key] = new_key
        if inherit_from:
            inherit_map[new_key] = inherit_from

    caddy_access_input_value = get_active_value(lines, "CADDY_ACCESS")
    caddy_localhost_access_value = get_active_value(lines, "CADDY_LOCALHOST_ACCESS")

    force_localhost = (
        caddy_localhost_access_value is not None
        and normalize_env_value(caddy_localhost_access_value) == "0"
    )
    caddy_access_line_index = None
    caddy_access_indent = ""

    new_lines = []
    for line in lines:
        parsed = parse_env_line(line)
        if not parsed:
            new_lines.append(line)
            continue

        indent, comment_prefix, key, value = parsed
        is_commented = comment_prefix.strip().startswith("#")

        if key in REMOVED_ENV_VARS or key.startswith(REMOVED_ENV_PREFIXES):
            changes.append(f"Removed obsolete SmallCode setting: {key}")
            continue

        if key in CADDY_V1_REMOVALS_DELETE:
            changes.append(f"Removed: {key} ({CADDY_V1_REMOVALS[key]})")
            continue

        if key == "CADDY_ACCESS" and not is_commented:
            if caddy_access_line_index is not None:
                new_lines.append(
                    comment_migrated_line(
                        indent,
                        key,
                        value,
                        "duplicate entry removed during migration",
                    )
                )
                changes.append("Removed: duplicate CADDY_ACCESS entry")
            else:
                caddy_access_line_index = len(new_lines)
                caddy_access_indent = indent
                current_value = normalize_env_value(value)
                desired_value = "localhost" if force_localhost else current_value
                if current_value != desired_value:
                    new_lines.append(format_caddy_access_line(indent, desired_value))
                    changes.append(
                        f"Updated: CADDY_ACCESS → '{desired_value}' (v1→v2 migration)"
                    )
                else:
                    new_lines.append(line)
                active_keys.add("CADDY_ACCESS")
            continue

        if key == "CADDY_ALLOW_PUBLIC" and not is_commented:
            if (
                caddy_access_input_value is not None
                or caddy_access_line_index is not None
            ):
                new_lines.append(
                    comment_migrated_line(
                        indent,
                        key,
                        value,
                        "migrated to CADDY_ACCESS in v2",
                    )
                )
                changes.append("Removed: CADDY_ALLOW_PUBLIC (replaced by CADDY_ACCESS)")
            else:
                desired_value = (
                    "localhost"
                    if force_localhost
                    else (
                        "public" if normalize_env_value(value) == "1" else "localhost"
                    )
                )
                new_lines.append(format_caddy_access_line(indent, desired_value))
                caddy_access_line_index = len(new_lines) - 1
                caddy_access_indent = indent
                active_keys.add("CADDY_ACCESS")
                changes.append(
                    f"Renamed: CADDY_ALLOW_PUBLIC → CADDY_ACCESS ('{desired_value}')"
                )
            continue

        if key == "CADDY_LOCALHOST_ACCESS" and not is_commented:
            if normalize_env_value(value) == "0":
                force_localhost = True
                if caddy_access_line_index is not None:
                    current_access = parse_env_line(new_lines[caddy_access_line_index])
                    current_access_value = (
                        normalize_env_value(current_access[3])
                        if current_access
                        else None
                    )
                    if current_access_value != "localhost":
                        new_lines[caddy_access_line_index] = format_caddy_access_line(
                            caddy_access_indent,
                            "localhost",
                        )
                        changes.append(
                            "Updated: CADDY_ACCESS → 'localhost' (CADDY_LOCALHOST_ACCESS=0)"
                        )
                else:
                    new_lines.append(format_caddy_access_line(indent, "localhost"))
                    caddy_access_line_index = len(new_lines) - 1
                    caddy_access_indent = indent
                    active_keys.add("CADDY_ACCESS")
                    changes.append(
                        "Renamed: CADDY_LOCALHOST_ACCESS → CADDY_ACCESS ('localhost')"
                    )
            else:
                new_lines.append(
                    comment_migrated_line(
                        indent,
                        key,
                        value,
                        "localhost access now follows CADDY_ACCESS",
                    )
                )
                changes.append(
                    "Removed: CADDY_LOCALHOST_ACCESS (handled by CADDY_ACCESS)"
                )
            continue

        if key in CADDY_V1_REMOVALS and not is_commented:
            new_lines.append(
                comment_migrated_line(
                    indent,
                    key,
                    value,
                    CADDY_V1_REMOVALS[key],
                )
            )
            changes.append(f"Removed: {key} ({CADDY_V1_REMOVALS[key]})")
            continue

        if key in rename_map and not is_commented:
            new_key = rename_map[key]
            # Only rename if new_key doesn't already exist as active
            if new_key not in active_keys:
                new_line = f"{indent}{comment_prefix}{new_key}={value}\n"
                new_lines.append(new_line)
                changes.append(f"Renamed: {key} → {new_key} (value preserved)")
                active_keys.add(new_key)
                migrated_keys[key] = new_key
            else:
                # New key already exists — comment out the old one
                new_line = f"{indent}# {key}={value}  # migrated to {new_key}\n"
                new_lines.append(new_line)
                changes.append(
                    f"Commented out: {key} (new key {new_key} already present)"
                )
        else:
            new_lines.append(line)

    if caddy_access_line_index is None:
        new_lines.append(format_caddy_access_line("", "localhost"))
        changes.append("Added: CADDY_ACCESS='localhost' (v1 default migration)")

    # Handle inheritance: for new gates that are absent, inherit from predecessor
    for old_key, new_key, inherit_from in MIGRATIONS:
        if new_key in active_keys:
            continue
        if not inherit_from or inherit_from not in active_keys:
            continue
        # Find the inherit_from line and add new_key with same value
        for i, line in enumerate(new_lines):
            parsed = parse_env_line(line)
            if (
                not parsed
                or parsed[2] != inherit_from
                or parsed[1].strip().startswith("#")
            ):
                continue
            indent, comment_prefix, _, value = parsed
            # Insert new gate line right after the inherited line
            new_gate_line = (
                f"{indent}{new_key}={value}  # inherited from {inherit_from}\n"
            )
            new_lines.insert(i + 1, new_gate_line)
            changes.append(f"Added: {new_key} (inherited from {inherit_from})")
            active_keys.add(new_key)
            break

    return new_lines, changes


def main():
    parser = argparse.ArgumentParser(
        description="Migrate ~/.env gate names to current DOTFILES_RUN_*_SETUP scheme"
    )
    parser.add_argument(
        "--env", default=os.path.expanduser("~/.env"), help="Path to env file"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview changes without writing"
    )
    parser.add_argument(
        "--no-backup", action="store_true", help="Skip backup before writing"
    )
    args = parser.parse_args()

    env_path = Path(args.env).expanduser()

    if not env_path.exists():
        print(f"ERROR: env file not found: {env_path}", file=sys.stderr)
        return 2

    lines = read_env_lines(env_path)
    new_lines, changes = migrate_env(lines, dry_run=True)

    if not changes:
        print(f"No migrations needed — {env_path} is already up to date.")
        return 0

    print(f"Env file: {env_path}")
    print(f"Changes ({len(changes)}):")
    for change in changes:
        print(f"  • {change}")

    if args.dry_run:
        print("\n(dry-run — no changes written)")
        return 0

    if not args.no_backup:
        backup_path = env_path.with_suffix(env_path.suffix + ".bak")
        backup_path.write_text(env_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Backup: {backup_path}")

    with env_path.open("w", encoding="utf-8") as f:
        f.writelines(new_lines)
    print(f"Written: {env_path}")
    print("\nNext steps:")
    print("  make reset && make deploy")
    print("  make verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
