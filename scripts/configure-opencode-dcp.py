#!/usr/bin/env python3
"""Write OpenCode v2's migration-aware CLI and quota client configuration."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from cli_helpers import add_common_args
from file_utils import backup_file, write_text_file

SCHEMA = "https://opencode.ai/v2/cli.json"
VOICE_PACKAGE = "@renjfk/opencode-voice"
PLUGIN_PACKAGES = {
    "@tarquinen/opencode-dcp": "@tarquinen/opencode-dcp@3.2.0",
    "@slkiser/opencode-quota": "@slkiser/opencode-quota@4.10.2",
}


class ConfigError(ValueError):
    """Raised when an existing configuration cannot be safely migrated."""


def config_dir() -> str:
    return os.path.abspath(
        os.path.expanduser(os.environ.get("OPENCODE_DIR", "~/.config/opencode"))
    )


def package_name(entry: Any) -> str | None:
    """Return a package's unversioned name, including unversioned scoped names."""
    if isinstance(entry, dict):
        value = entry.get("package")
        return package_name(value) if isinstance(value, str) else None
    if not isinstance(entry, str):
        return None
    if entry.startswith("@"):  # @scope/package[@version]
        slash = entry.find("/")
        at = entry.rfind("@")
        return entry[:at] if at > slash else entry
    return entry.split("@", 1)[0]


def is_voice_entry(entry: Any) -> bool:
    """Identify only the removed OpenCode voice package."""
    return package_name(entry) == VOICE_PACKAGE


def _read_object(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as config_file:
            loaded = json.load(config_file)
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"Could not read valid JSON from {path}: {error}") from error
    if not isinstance(loaded, dict):
        raise ConfigError(f"Configuration root must be an object: {path}")
    return loaded


def load_source(directory: str) -> tuple[dict[str, Any], str]:
    """Load cli.json, or legacy tui.json only when cli.json is absent."""
    cli_path = os.path.join(directory, "cli.json")
    legacy_path = os.path.join(directory, "tui.json")
    if os.path.exists(cli_path):
        return _read_object(cli_path), cli_path
    if os.path.exists(legacy_path):
        return _read_object(legacy_path), legacy_path
    return {}, cli_path


def _package_string(entry: Any) -> dict[str, Any] | None:
    """Convert package-shaped legacy strings; retain directive strings verbatim."""
    if isinstance(entry, str):
        # v2 distinguishes ordered string directives (notably leading '-') from
        # package objects.  A bare unscoped name is not package-shaped here.
        if entry.startswith("-") or not (
            (entry.startswith("@") and "/" in entry)
            or ("@" in entry and not entry.startswith("@"))
        ):
            return None
        return {"package": entry}
    if isinstance(entry, dict) and isinstance(entry.get("package"), str):
        return dict(entry)
    if isinstance(entry, (list, tuple)) and entry and isinstance(entry[0], str):
        result: dict[str, Any] = {"package": entry[0]}
        if len(entry) > 1 and isinstance(entry[1], dict):
            result["options"] = dict(entry[1])
        return result
    return None


def _dotted_keybinds(keybinds: Any) -> Any:
    if not isinstance(keybinds, dict):
        return keybinds
    # Vendored from anomalyco/opencode v2.0.15,
    # packages/tui/src/config/v1/keybind.ts (CommandMap), as consumed by
    # packages/cli/src/config/migrate.ts (migrateV1).  This is deliberately
    # explicit: underscores in command names are not generally separators.
    command_map = {
        "app_exit": "app.exit",
        "app_debug": "app.debug",
        "app_console": "app.console",
        "app_heap_snapshot": "app.heap_snapshot",
        "app_toggle_file_context": "app.toggle.file_context",
        "app_toggle_animations": "app.toggle.animations",
        "app_toggle_diffwrap": "app.toggle.diffwrap",
        "app_toggle_paste_summary": "app.toggle.paste_summary",
        "theme_list": "theme.switch",
        "theme_switch_mode": "theme.switch_mode",
        "theme_mode_lock": "theme.mode.lock",
        "session_new": "session.new",
        "session_list": "session.list",
        "session_rename": "session.rename",
        "session_delete": "session.delete",
        "session_queued_prompts": "session.queued_prompts",
        "session_quick_switch_1": "session.quick_switch.1",
        "session_quick_switch_2": "session.quick_switch.2",
        "session_quick_switch_3": "session.quick_switch.3",
        "session_quick_switch_4": "session.quick_switch.4",
        "session_quick_switch_5": "session.quick_switch.5",
        "session_quick_switch_6": "session.quick_switch.6",
        "session_quick_switch_7": "session.quick_switch.7",
        "session_quick_switch_8": "session.quick_switch.8",
        "session_quick_switch_9": "session.quick_switch.9",
        "session_tab_next": "session.tab.next",
        "session_tab_previous": "session.tab.previous",
        "session_tab_close": "session.tab.close",
        "session_tab_reopen": "session.tab.reopen",
        "session_tab_next_unread": "session.tab.next_unread",
        "session_tab_previous_unread": "session.tab.previous_unread",
        "session_timeline": "session.timeline",
        "session_fork": "session.fork",
        "session_export": "session.export",
        "session_copy": "session.copy",
        "session_move": "session.move",
        "session_interrupt": "session.interrupt",
        "session_background": "session.background",
        "session_compact": "session.compact",
        "session_pin_toggle": "session.pin.toggle",
        "queued_prompt_delete": "queued_prompt.delete",
        "sidebar_toggle": "session.sidebar.toggle",
        "scrollbar_toggle": "session.toggle.scrollbar",
        "status_view": "opencode.status",
        "debug_view": "opencode.debug",
        "diff_open": "diff.open",
        "diff_close": "diff.close",
        "diff_toggle": "diff.toggle",
        "diff_expand": "diff.expand",
        "diff_collapse": "diff.collapse",
        "diff_switch_focus": "diff.switch_focus",
        "diff_next_hunk": "diff.next_hunk",
        "diff_previous_hunk": "diff.previous_hunk",
        "diff_next_file": "diff.next_file",
        "diff_previous_file": "diff.previous_file",
        "diff_toggle_file_tree": "diff.toggle_file_tree",
        "diff_single_patch": "diff.single_patch",
        "diff_switch_source": "diff.switch_source",
        "diff_toggle_view": "diff.toggle_view",
        "diff_help": "diff.help",
        "editor_open": "prompt.editor",
        "prompt_submit": "prompt.submit",
        "prompt_queue": "prompt.queue",
        "prompt_editor_context_clear": "prompt.editor_context.clear",
        "prompt_images_view": "prompt.images.view",
        "input_clear": "prompt.clear",
        "input_paste": "prompt.paste",
        "input_submit": "input.submit",
        "input_newline": "input.newline",
        "input_move_left": "input.move.left",
        "input_move_right": "input.move.right",
        "input_move_up": "input.move.up",
        "input_move_down": "input.move.down",
        "input_select_left": "input.select.left",
        "input_select_right": "input.select.right",
        "input_select_up": "input.select.up",
        "input_select_down": "input.select.down",
        "input_delete": "input.delete",
        "input_backspace": "input.backspace",
        "input_undo": "input.undo",
        "input_redo": "input.redo",
        "history_previous": "prompt.history.previous",
        "history_next": "prompt.history.next",
        "messages_copy": "messages.copy",
        "messages_undo": "session.undo",
        "messages_redo": "session.redo",
        "display_thinking": "session.toggle.thinking",
        "agent_list": "agent.list",
        "agent_cycle": "agent.cycle",
        "variant_cycle": "variant.cycle",
        "model_list": "model.list",
        "mcp_list": "mcp.list",
        "terminal_suspend": "terminal.suspend",
        "terminal_title_toggle": "terminal.title.toggle",
    }
    native_targets = set(command_map.values())
    converted = {}
    unmapped = []
    for key, value in keybinds.items():
        target = command_map.get(key)
        if target is None and key in native_targets:
            target = key
        if target is None:
            unmapped.append(key)
        else:
            converted[target] = value
    if unmapped:
        raise ConfigError(
            "Unsupported legacy keybind(s); refusing unsafe conversion: "
            + ", ".join(sorted(unmapped))
        )
    return converted


def _native_theme(theme: Any) -> Any:
    if isinstance(theme, str):
        # v2.0.15 packages/cli/src/config/migrate.ts delegates this shape to
        # the TUI v1 migration: theme.name (not theme.theme).
        return {"name": theme}
    return theme


def _read_jsonc_object(path: str) -> dict[str, Any]:
    try:
        text = open(path, encoding="utf-8").read()
        cleaned = []
        in_string = escaped = False
        index = 0
        while index < len(text):
            char = text[index]
            if in_string:
                cleaned.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                index += 1
                continue
            if char == '"':
                in_string = True
                cleaned.append(char)
                index += 1
            elif text.startswith("//", index):
                index = text.find("\n", index)
                if index < 0:
                    break
            elif text.startswith("/*", index):
                end = text.find("*/", index + 2)
                if end < 0:
                    raise ConfigError(f"Unterminated comment in {path}")
                index = end + 2
            else:
                cleaned.append(char)
                index += 1
        normalized = re.sub(r",(\s*[}\]])", r"\1", "".join(cleaned))
        loaded = json.loads(normalized)
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"Could not read valid JSONC from {path}: {error}") from error
    if not isinstance(loaded, dict):
        raise ConfigError(f"Configuration root must be an object: {path}")
    return loaded


def build_config(existing: dict[str, Any]) -> dict[str, Any]:
    """Convert legacy client settings and replace managed plugins idempotently."""
    result = dict(existing)
    result["$schema"] = SCHEMA
    raw_plugins = result.get("plugins", result.get("plugin", []))
    if not isinstance(raw_plugins, list):
        raise ConfigError('The client "plugins" value must be an array')

    ordered: list[Any] = []
    managed_names = set(PLUGIN_PACKAGES)
    for raw_entry in raw_plugins:
        entry = _package_string(raw_entry)
        if entry is None:
            if isinstance(raw_entry, str):
                ordered.append(raw_entry)
            continue
        if is_voice_entry(entry):
            continue
        name = package_name(entry)
        if name and name not in managed_names:
            ordered.append(entry)
    for name, package in PLUGIN_PACKAGES.items():
        ordered.append({"package": package})

    result["plugins"] = ordered
    result.pop("plugin", None)
    if "theme" in result:
        result["theme"] = _native_theme(result["theme"])
    if "keybinds" in result:
        result["keybinds"] = _dotted_keybinds(result["keybinds"])
    return result


def build_quota_config(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {"enabledProviders": "auto"}
    result = _read_jsonc_object(path)
    result["enabledProviders"] = "auto"
    return result


def _write_json(path: str, content: dict[str, Any], no_backup: bool) -> None:
    if os.path.exists(path) and not no_backup:
        backup_path = backup_file(path, enabled=True)
        if backup_path:
            logger.info("Backed up existing configuration to %s", backup_path)
    write_text_file(path, json.dumps(content, indent=2) + "\n", backup=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Configure OpenCode v2 CLI plugins and quota surfaces",
        allow_abbrev=False,
    )
    add_common_args(parser, no_backup=True)
    args = parser.parse_args()
    directory = config_dir()
    output_path = os.path.join(directory, "cli.json")
    quota_path = os.path.join(directory, "opencode-quota", "quota-toast.jsonc")

    try:
        existing, source_path = load_source(directory)
        result = build_config(existing)
        quota_config = build_quota_config(quota_path)
    except ConfigError as error:
        logger.critical("OpenCode client configuration aborted: %s", error)
        raise SystemExit(1) from error

    if args.dry_run:
        logger.info(
            "\n".join(
                [
                    json.dumps(result, indent=2),
                    json.dumps(quota_config, indent=2),
                ]
            )
        )
        return

    try:
        os.makedirs(directory, exist_ok=True)
        os.makedirs(os.path.dirname(quota_path), exist_ok=True)
        _write_json(output_path, result, args.no_backup)
        _write_json(quota_path, quota_config, args.no_backup)
    except OSError as error:
        logger.critical("Failed to write OpenCode client configuration: %s", error)
        raise SystemExit(1) from error

    logger.info(
        "\n".join(
            [
                "OpenCode v2 client configuration complete!",
                "",
                f"cli.json written to: {output_path}",
                f"  • Imported settings from: {source_path}",
                "  • @tarquinen/opencode-dcp@3.2.0",
                "  • @slkiser/opencode-quota@4.10.2",
                f"quota-toast.jsonc written to: {quota_path}",
            ]
        )
    )


if __name__ == "__main__":
    main()
