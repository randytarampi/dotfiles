#!/usr/bin/env python3
"""Migrate ~/.env gate names to the current DOTFILES_RUN_*_SETUP scheme.

This script performs a one-way migration of deprecated gate names to their
current counterparts, preserving existing values and optionally inheriting
values for newly-split gates. It is idempotent: running it twice is a no-op.

Usage:
    scripts/migrate-env-gates.py              # Preview ~/.env migration
    scripts/migrate-env-gates.py --dry-run     # Preview changes without writing
    scripts/migrate-env-gates.py --apply       # Write the migration
    scripts/migrate-env-gates.py --env /path   # Use a different env file

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
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from preset_migration import TIER_VALUE_MIGRATIONS, migrate_preset_value

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
    ("DATABASE_URL", "LITELLM_DATABASE_URL", None),
    ("DOTFILES_LOCAL_FALLBACK_ROLES", "DOTFILES_ROLE_MODELS", None),
    ("DOTFILES_LOCAL_FALLBACK_PLACEHOLDERS", "DOTFILES_CATEGORY_MODELS", None),
    (
        "DOTFILES_PROJECT_LOCAL_FALLBACK_ROLE",
        "DOTFILES_PROJECT_ROLE_MODELS",
        None,
    ),
    (
        "DOTFILES_PROJECT_LOCAL_FALLBACK_PLACEHOLDER",
        "DOTFILES_PROJECT_CATEGORY_MODELS",
        None,
    ),
    # Pi is a new opt-in integration; its documented settings are preserved.
    ("DOTFILES_RUN_PI", "DOTFILES_RUN_PI_SETUP", None),
    ("DOTFILES_RUN_CORTEX", "DOTFILES_RUN_CORTEX_SETUP", None),
    ("DOTFILES_CORTEX_CONNECTION", "CORTEX_CONNECTION", None),
    ("DOTFILES_SNOWFLAKE_HOME", "SNOWFLAKE_HOME", None),
    ("DOTFILES_CORTEX_AGENT_MODEL", "CORTEX_AGENT_MODEL", None),
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
    # Backup timer gate rename: preserve existing user opt-in values.
    (
        "DOTFILES_RUN_OPENWEBUI_BACKUP_SCHEDULE",
        "DOTFILES_RUN_OPENWEBUI_BACKUP_SETUP",
        None,
    ),
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

# Four retired per-client canaries OR into the single explicit proxy mode.
# This four-field record is applied only by migration, never runtime resolution.
LITELLM_PROXY_OR_MIGRATION = (
    (
        "DOTFILES_OPENWEBUI_USE_LITELLM",
        "DOTFILES_OPENCODE_USE_LITELLM",
        "DOTFILES_PI_USE_LITELLM",
        "DOTFILES_JUNIE_USE_LITELLM",
    ),
    "DOTFILES_USE_LITELLM_PROXY",
    "truthy-or",
    "legacy client canaries",
)

# Tier value renames. These are value migrations rather than environment-key
# migrations, so the existing key and quoting are preserved.
# Project `.opencode/.env` files are intentionally outside this script's
# ~/.env-only migration scope; configure-project.py normalizes them at read time.
TIER_VALUE_KEYS = {
    "DOTFILES_OPENCODE_TIER",
    "DOTFILES_PI_TIER",
    "DOTFILES_LOCAL_FALLBACK_PRESET",
}

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
    "OPENCODE_SERVER_USERNAME",
}

# Assignment parser: matches KEY='VALUE' or # KEY='VALUE'
ASSIGNMENT_RE = re.compile(r"^(\s*)(#\s*)?(export\s+)?([A-Za-z_][A-Za-z0-9_]*)(=)(.*)$")
LITERAL_ASSIGNMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
BARE_LITERAL_RE = re.compile(r"^[A-Za-z0-9_./:,+=@%#-]*$")


def parse_literal_value(source):
    value = source.rstrip("\r\n")
    if value.startswith("'"):
        close = value.find("'", 1)
        if close < 0:
            return None
        token, rest = value[1:close], value[close + 1 :]
        if rest and not re.match(r"^\s+#", rest):
            return None
        return token, value[: close + 1], rest
    if value.startswith('"') and value.endswith('"') and value.count('"') == 2:
        return value[1:-1], value, ""
    comment = re.search(r"\s+#", value)
    literal = value[: comment.start()] if comment else value
    rest = value[comment.start() :] if comment else ""
    if literal != literal.strip() or not BARE_LITERAL_RE.fullmatch(literal):
        return None
    return literal, literal, rest


def unsafe_value_keys(lines):
    """Return key names for active lines outside the literal .env grammar."""
    refused = []
    seen = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parsed_line = parse_env_line(line)
        if parsed_line and parsed_line[1].strip().startswith("#"):
            continue
        if not parsed_line:
            candidate = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)", line)
            refused.append(candidate.group(1) if candidate else "<unparsed line>")
            continue
        key, value = parsed_line[2], parsed_line[3]
        # The migration CLI accepts only bare and single-quoted shell literals.
        if value.startswith('"'):
            refused.append(key)
            continue
        parsed_value = parse_literal_value(value)
        if parsed_value is None:
            refused.append(key)
            continue
        token = parsed_value[0]
        if key in seen and seen[key] != token:
            refused.append(key)
        seen[key] = token
    for old, new, inherit_from in MIGRATIONS:
        # A split retains its source, so independently set targets are valid.
        if (
            old != inherit_from
            and old in seen
            and new in seen
            and seen[old] != seen[new]
        ):
            refused.append(new)
    return refused


def parse_env_line(line):
    """Parse an env line into (indent, export/comment prefix, key, value)."""
    content = line.rstrip("\r\n")
    match = ASSIGNMENT_RE.match(content)
    if not match:
        return None
    indent, comment, export, key, _separator, value = match.groups()
    # Preserve both comment and export attributes in the existing prefix slot.
    prefix = (comment or "") + (export or "")
    return indent, prefix, key, value


def read_env_lines(path):
    """Read all lines from an env file, returning list of (line, parsed_or_None)."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return f.readlines()


def normalize_env_value(value):
    """Decode supported literal values, retaining legacy helper fallback."""
    parsed = parse_literal_value(value)
    return parsed[0] if parsed is not None else value.strip()


def split_inline_comment(value):
    """Return an assignment value token and its preserved inline comment."""
    parsed = parse_literal_value(value)
    return (parsed[1], parsed[2]) if parsed is not None else (value.strip(), "")


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
    exported_keys = {
        parsed[2]
        for parsed in (parse_env_line(line) for line in lines)
        if parsed and parsed[1].startswith("export")
    }
    exported_targets = {
        new
        for old, new, inherit_from in MIGRATIONS
        if old in exported_keys and old != inherit_from
    }
    changes = []
    old_password = get_active_value(lines, "OPENCODE_SERVER_PASSWORD")
    new_password = get_active_value(lines, "OPENCODE_PASSWORD")
    if old_password is not None and new_password is not None:
        old_value = normalize_env_value(split_inline_comment(old_password)[0])
        new_value = normalize_env_value(split_inline_comment(new_password)[0])
        if old_value != new_value:
            raise ValueError(
                "OPENCODE_SERVER_PASSWORD conflicts with OPENCODE_PASSWORD; resolve manually"
            )
    for key, value in (
        ("OPENCODE_SERVER_PASSWORD", old_password),
        ("OPENCODE_PASSWORD", new_password),
    ):
        if (
            value is not None
            and normalize_env_value(split_inline_comment(value)[0]) == ""
        ):
            raise ValueError(f"{key} must not be empty")
    legacy_keys, proxy_key, _policy, legacy_label = LITELLM_PROXY_OR_MIGRATION
    explicit_proxy_value = get_active_value(lines, proxy_key)
    legacy_enabled = any(
        key in active_keys
        and normalize_env_value(
            split_inline_comment(get_active_value(lines, key) or "")[0]
        ).lower()
        in {"1", "true", "yes", "on"}
        for key in legacy_keys
    )
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
        newline = "\r\n" if line.endswith("\r\n") else "\n"
        is_commented = comment_prefix.strip().startswith("#")
        export_prefix = "export " if "export" in comment_prefix.split() else ""
        if key == "OPENCODE_SERVER_PASSWORD" and not is_commented:
            if new_password is None:
                new_lines.append(
                    f"{indent}{export_prefix}OPENCODE_PASSWORD={value}{newline}"
                )
                changes.append("Migrated: OPENCODE_SERVER_PASSWORD → OPENCODE_PASSWORD")
            else:
                changes.append("Removed duplicate: OPENCODE_SERVER_PASSWORD")
            continue
        if key in exported_targets and not is_commented and not comment_prefix:
            comment_prefix = "export "
            line = f"{indent}{comment_prefix}{key}={value}{newline}"
            changes.append(f"Preserved export attribute: {key}")
        comment_indent = indent + "# "

        if key in legacy_keys and not is_commented:
            new_lines.append(
                f"{comment_indent}{export_prefix}{key}={value}  # migrated to {proxy_key}{newline}"
            )
            changes.append(f"Deprecated: {key} → {proxy_key}")
            continue

        if (
            key in TIER_VALUE_KEYS
            and not is_commented
            and normalize_env_value(split_inline_comment(value)[0])
            in TIER_VALUE_MIGRATIONS
        ):
            value_token, inline_comment = split_inline_comment(value)
            old_value = normalize_env_value(value_token)
            new_value = migrate_preset_value(old_value)
            if value_token.startswith(("'", '"')):
                quote = value_token[0]
                value = f"{quote}{new_value}{quote}"
            else:
                value = new_value
            value += inline_comment
            line = f"{indent}{comment_prefix}{key}={value}{newline}"
            changes.append(f"Updated: {key} tier value {old_value} → {new_value}")

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
                new_line = f"{indent}{comment_prefix}{new_key}={value}{newline}"
                new_lines.append(new_line)
                changes.append(f"Renamed: {key} → {new_key} (value preserved)")
                active_keys.add(new_key)
                migrated_keys[key] = new_key
            else:
                # New key already exists — comment out the old one
                new_line = f"{comment_indent}{export_prefix}{key}={value}  # migrated to {new_key}{newline}"
                new_lines.append(new_line)
                changes.append(
                    f"Commented out: {key} (new key {new_key} already present)"
                )
        else:
            new_lines.append(line)

    if caddy_access_line_index is None and not any(
        parsed and parsed[2] == "CADDY_ACCESS" and not parsed[1].strip().startswith("#")
        for parsed in (parse_env_line(line) for line in new_lines)
    ):
        new_lines.append(format_caddy_access_line("", "localhost"))
        changes.append("Added: CADDY_ACCESS='localhost' (v1 default migration)")

    if explicit_proxy_value is None and legacy_enabled:
        new_lines.append(f"{proxy_key}='1'  # migrated from {legacy_label}\n")
        changes.append(f"OR-migrated legacy client canaries → {proxy_key}='1'")
    elif explicit_proxy_value is not None:
        # The new setting wins even when it explicitly disables the proxy.
        for key in legacy_keys:
            if key in active_keys:
                changes.append(f"Explicit {proxy_key} overrides legacy {key}")

    # Handle inheritance: for new gates that are absent, inherit from predecessor
    for old_key, new_key, inherit_from in MIGRATIONS:
        if get_active_value(new_lines, new_key) is not None:
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
            new_gate_line = f"{indent}{comment_prefix}{new_key}={value}  # inherited from {inherit_from}\n"
            new_lines.insert(i + 1, new_gate_line)
            changes.append(f"Added: {new_key} (inherited from {inherit_from})")
            active_keys.add(new_key)
            break

    if changes and lines:
        # Keep one source newline convention for generated lines as well as
        # rewrites, and retain a missing final terminator without joining keys.
        newline = next(
            (
                "\r\n" if line.endswith("\r\n") else "\n"
                for line in lines
                if line.endswith("\n")
            ),
            "\n",
        )
        new_lines = [line.rstrip("\r\n") + newline for line in new_lines]
        if new_lines and not lines[-1].endswith(("\r", "\n")):
            new_lines[-1] = new_lines[-1].rstrip("\r\n")
    return new_lines, changes


def main():
    parser = argparse.ArgumentParser(
        description="Migrate ~/.env gate names to current DOTFILES_RUN_*_SETUP scheme",
        epilog="By default, preview changes; pass --apply to write them.",
    )
    parser.add_argument(
        "--env", default=os.path.expanduser("~/.env"), help="Path to env file"
    )
    parser.add_argument(
        "--no-backup", action="store_true", help="Skip backup before writing"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply", action="store_true", help="Apply the planned migration"
    )
    mode.add_argument(
        "--dry-run", action="store_true", help="Preview changes without writing"
    )
    args = parser.parse_args()

    if os.name == "nt":
        print(
            "Refused: environment migration requires POSIX ownership and permissions",
            file=sys.stderr,
        )
        return 1

    env_path = Path(args.env).expanduser().absolute()

    # Normalize only macOS's system alias, not user-controlled symlinks below it.
    if sys.platform == "darwin" and str(env_path).startswith("/var/"):
        env_path = Path("/private/var") / env_path.relative_to("/var")
    if any(parent.is_symlink() for parent in (env_path, *env_path.parents)):
        print("Refused: env path or its parent directory is a symlink", file=sys.stderr)
        return 1
    try:
        target_stat = env_path.lstat()
    except FileNotFoundError:
        print(f"ERROR: env file not found: {env_path}", file=sys.stderr)
        return 2
    if (
        not stat.S_ISREG(target_stat.st_mode)
        or target_stat.st_nlink != 1
        or target_stat.st_uid != os.getuid()
    ):
        print(
            "Refused: env path must be an owned, unlinked regular file", file=sys.stderr
        )
        return 1

    backup_path = env_path.with_suffix(".bak")
    original_bytes = env_path.read_bytes()
    original_stat = target_stat
    lines = original_bytes.decode("utf-8").splitlines(keepends=True)
    refused = unsafe_value_keys(lines)
    if refused:
        for key in refused:
            print(f"Refused: {key} has non-literal or conflicting declaration")
        return 1
    new_lines, changes = migrate_env(lines, dry_run=True)

    if not changes:
        print(f"No migrations needed — {env_path} is already up to date.")
        return 0

    print(f"Env file: {env_path}")
    print(f"Changes ({len(changes)}):")
    for change in changes:
        print(f"  • {change}")

    if args.dry_run or not args.apply:
        print("\n(dry-run — no changes written)")
        return 0

    if not args.no_backup and (backup_path.is_symlink() or backup_path.exists()):
        print(
            f"Refused: backup path already exists: {backup_path}. "
            "Preserve it in a private location before retrying, or explicitly use --no-backup.",
            file=sys.stderr,
        )
        return 1
    if not args.no_backup:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            backup_fd = os.open(backup_path, flags, 0o600)
        except FileExistsError:
            print(
                f"Refused: backup path already exists: {backup_path}. "
                "Preserve it in a private location before retrying, or explicitly use --no-backup.",
                file=sys.stderr,
            )
            return 1
        try:
            with os.fdopen(backup_fd, "wb") as backup:
                backup.write(original_bytes)
                backup.flush()
                os.fsync(backup.fileno())
        except Exception:
            # Never remove a path here: another writer may have replaced it.
            raise
        print(f"Backup: {backup_path}")

    temporary_path = None
    try:
        temporary = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=env_path.parent,
            prefix=f".{env_path.name}.",
            delete=False,
        )
        temporary_path = Path(temporary.name)
        with temporary:
            temporary.writelines(new_lines)
        if any(parent.is_symlink() for parent in (env_path, *env_path.parents)):
            raise RuntimeError("env path changed during migration; backup retained")
        current_stat = env_path.lstat()
        original_identity = (
            original_stat.st_dev,
            original_stat.st_ino,
            original_stat.st_mode,
            original_stat.st_nlink,
            original_stat.st_uid,
            original_stat.st_mtime_ns,
            original_stat.st_size,
        )
        current_identity = (
            current_stat.st_dev,
            current_stat.st_ino,
            current_stat.st_mode,
            current_stat.st_nlink,
            current_stat.st_uid,
            current_stat.st_mtime_ns,
            current_stat.st_size,
        )
        if (
            current_identity != original_identity
            or env_path.read_bytes() != original_bytes
        ):
            raise RuntimeError("env file changed during migration; backup retained")
        original_mode = stat.S_IMODE(original_stat.st_mode)
        replacement_mode = original_mode & 0o600
        if replacement_mode != original_mode:
            print(
                f"Permissions narrowed: {original_mode:04o} -> {replacement_mode:04o}"
            )
        temporary_path.chmod(replacement_mode)
        temporary_path.replace(env_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    print(f"Written: {env_path}")
    if args.no_backup:
        print("Backup skipped (--no-backup)")
    print("\nNext steps:")
    print("  make reset && make deploy")
    print("  make verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
