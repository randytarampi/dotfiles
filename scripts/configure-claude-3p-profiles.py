#!/usr/bin/env python3
"""Configure Claude-3p inference profiles from the OMO-Slim registry.

Schema finding: inferenceGatewayApiKey is app-sealed; the entry schema has no
path-reference field in its provider, auth, URL, models or metadata fields.
"""

import argparse
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import uuid

SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR / "lib"
sys.path.insert(0, str(LIB_DIR))

import logger  # noqa: E402
import tier_registry  # noqa: E402
from cli_helpers import add_common_args  # noqa: E402
from env import load_env  # noqa: E402

SCHEMA_FINDING = (
    "Schema finding: inferenceGatewayApiKey is app-sealed; no path-reference "
    "field exists in inferenceProvider, inferenceGatewayAuthScheme, "
    "inferenceGatewayBaseUrl, inferenceModels or metadata."
)
KEY_INSTRUCTION = (
    "apply the profile in the app's Configurations dropdown and enter the "
    "scoped key once — the app seals it (app-sealed field, schema finding)"
)
CLAUDE_TIERS = (
    ("oracle", "fable"),
    ("orchestrator", "opus"),
    ("designer", "sonnet"),
    ("librarian", "haiku"),
)
MODEL_ROLES = tuple(role for role, _ in CLAUDE_TIERS)


def read_private_key(path):
    """Read an owner-only, single-link regular file without following links."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("Claude-3p scoped key file missing or unsafe")
    info = path.stat()
    if (
        info.st_uid != os.getuid()
        or info.st_nlink != 1
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ValueError("Claude-3p scoped key must be owner-only mode 0600")
    value = path.read_text(encoding="utf-8").strip()
    if not value or any(char.isspace() for char in value):
        raise ValueError("Claude-3p scoped key is malformed")
    return value


def build_profiles(registry, served_ids, api_key=""):
    profiles = {}
    for preset in registry["presets"]:
        role_models = tier_registry.materialize_role_models(registry, preset, {})
        models = []
        missing = []
        family_tiers = dict(CLAUDE_TIERS)
        for role in MODEL_ROLES:
            family = family_tiers[role]
            model = role_models.get(role)
            if not isinstance(model, str) or not model:
                missing.append(f"{role} unresolved")
            elif served_ids is not None and model not in served_ids:
                missing.append(f"{role} model {model} is not served")
            models.append(
                {
                    "name": model,
                    "anthropicFamilyTier": family,
                    "isFamilyDefault": True,
                    "labelOverride": model,
                    "behavesAs": family,
                }
            )
        if missing:
            logger.warning("Refusing LiteLLM-%s: %s", preset, "; ".join(missing))
            continue
        entry = {
            "deploymentDisplayName": f"LiteLLM-{preset}",
            "inferenceCredentialKind": "static",
            "inferenceGatewayAuthScheme": "bearer",
            "inferenceGatewayBaseUrl": "http://127.0.0.1:4000",
            "inferenceProvider": "gateway",
            "inferenceModels": models,
        }
        if api_key:
            entry["inferenceGatewayApiKey"] = api_key
        profiles[preset] = entry
    return profiles


def _checked_path(path):
    absolute = path.absolute()
    for parent in reversed(absolute.parents):
        if parent.exists() and parent.is_symlink():
            raise ValueError(f"refusing symlink path component: {parent}")
    if path.is_symlink():
        raise ValueError(f"refusing symlink profile file: {path}")
    return path


def _preflight_write(path, content, *, backup=True):
    """Check owner state and backup conflicts before mutating any profile."""
    path = _checked_path(path)
    if path.exists():
        info = path.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError(f"refusing non-private or foreign profile file: {path}")
        if path.read_text(encoding="utf-8") == content:
            return False
        backup_path = path.with_name(path.name + ".bak")
        if backup and (backup_path.exists() or backup_path.is_symlink()):
            raise ValueError(f"backup already exists or unsafe: {backup_path}")
    return True


def _atomic_write(path, content, *, backup=True, dry_run=False):
    changed = _preflight_write(path, content, backup=backup)
    if not changed or dry_run:
        return changed
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _checked_path(path)
    if path.exists() and backup:
        # Never create a world-readable copy of an app-sealed/API key, even
        # briefly before chmod; exclusive creation prevents backup clobber.
        backup_path = path.with_name(path.name + ".bak")
        fd = os.open(str(backup_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(path.read_bytes())
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            backup_path.unlink(missing_ok=True)
            raise
    fd, temporary = tempfile.mkstemp(prefix=".dotfiles-claude-3p-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        # Existing-file replacement is limited to owner-checked files.
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def _validate_owned_profile(path, entry):
    """Do not adopt a foreign file that happens to share our generated UUID."""
    if not path.exists():
        return
    _checked_path(path)
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("existing profile is not a recognised managed profile") from None
    if not isinstance(current, dict) or any(
        current.get(key) != entry[key]
        for key in (
            "deploymentDisplayName",
            "inferenceProvider",
            "inferenceGatewayBaseUrl",
        )
    ):
        raise ValueError("existing profile is not a recognised managed profile")




def _read_served_ids(config_path):
    """Return exact *served* LiteLLM model_name aliases, never backend IDs."""
    if config_path.is_symlink() or not config_path.is_file():
        raise ValueError("LiteLLM registry config is unavailable or unsafe")
    import yaml

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    rows = config.get("model_list") if isinstance(config, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("LiteLLM registry has no model_list")
    result = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("model_name"), str):
            raise ValueError("LiteLLM registry contains invalid model row")
        alias = row["model_name"]
        if not alias or alias != alias.strip():
            raise ValueError("LiteLLM registry contains an unsafe model alias")
        result.add(alias)
    return result

def _local_category_models(registry, preset):
    """Resolve local role categories through the shared tier classifier."""
    from discover_models import list_local_ollama_models

    models = list_local_ollama_models()
    if not models:
        return {}
    return tier_registry.classify_models_for_preset(
        models,
        registry,
        preset,
        profile=tier_registry.get_profile_name(registry, preset),
    )


def _update_index(source, profiles):
    index_path = source / "_meta.json"
    if index_path.is_symlink():
        raise ValueError("refusing symlink Claude-3p config index")
    index = (
        json.loads(index_path.read_text(encoding="utf-8"))
        if index_path.exists()
        else {"entries": []}
    ) or {}
    if not isinstance(index, dict) or not isinstance(index.get("entries"), list):
        raise ValueError("Claude-3p config index has an unsupported schema")
    entries = {}
    for item in index["entries"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("Claude-3p index contains a malformed entry")
        name = item["name"]
        if name in entries:
            raise ValueError("Claude-3p index contains duplicate profile names")
        entries[name] = item
    for preset in profiles:
        name = f"LiteLLM-{preset}"
        entry_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "dotfiles-claude-3p-" + preset))
        if name in entries and entries[name].get("id") != entry_id:
            raise ValueError(f"refusing to replace unmanaged profile index: {name}")
        entries[name] = {"id": entry_id, "name": name}
    # Do not continue advertising skipped, now-unserved profiles from prior
    # runs. Preserve unrelated and non-dotfiles entries in the app's index.
    for name, item in list(entries.items()):
        if not name.startswith("LiteLLM-") or name[8:] in profiles:
            continue
        owned_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "dotfiles-claude-3p-" + name[8:]))
        if item.get("id") == owned_id:
            del entries[name]
    index["entries"] = sorted(entries.values(), key=lambda item: item["name"])
    return index_path, json.dumps(index, indent=2) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    add_common_args(parser, no_backup=True)
    parser.add_argument(
        "--source-dir", default="~/Library/Application Support/Claude-3p/configLibrary"
    )
    parser.add_argument(
        "--api-key-source", choices=("none", "key-file"), default="none"
    )
    parser.add_argument(
        "--allow-partial",
        action="append",
        default=[],
        metavar="PROFILE",
        help="Accept skipping additional unserved profiles by this exact name;"
        " every other skip still fails (exit 1).",
    )
    args = parser.parse_args(argv)
    load_env()
    if os.environ.get("DOTFILES_RUN_OPENCODE_SETUP", "0") != "1":
        logger.info(
            "DOTFILES_RUN_OPENCODE_SETUP is disabled; skipping Claude-3p profiles"
        )
        return 0
    try:
        registry = tier_registry.load_registry()
        served_ids = _read_served_ids(Path.home() / ".local/share/litellm/config.yaml")
        key = ""
        if args.api_key_source == "key-file":
            key = read_private_key(
                Path.home() / ".local/share/litellm/clients/claude.key"
            )
        profiles = {}
        missing_profiles = []
        for preset in registry["presets"]:
            categories = _local_category_models(registry, preset)
            role_models = tier_registry.materialize_role_models(
                registry, preset, categories
            )
            profile_registry = {
                "presets": {
                    preset: {
                        role: {"model": model} for role, model in role_models.items()
                    }
                }
            }
            generated = build_profiles(profile_registry, served_ids, key)
            if generated:
                profiles.update(generated)
            else:
                missing_profiles.append(preset)
        if missing_profiles:
            accepted_names = {
                name.removeprefix("LiteLLM-") for name in args.allow_partial
            }
            unaccepted = [
                name for name in missing_profiles if name not in accepted_names
            ]
            accepted = sorted(set(missing_profiles) & accepted_names)
            for name in accepted:
                logger.warning("Accepting skip for unserved profile: %s", name)
            logger.warning(
                "Skipping profiles with unserved or unresolved models: %s",
                ", ".join(missing_profiles),
            )
            if unaccepted:
                logger.error(
                    "Refusing partial Claude-3p profile set; %d unserved profile(s)"
                    " not accepted: %s",
                    len(unaccepted),
                    ", ".join(sorted(unaccepted)),
                )
                return 1
            if missing_profiles and not args.dry_run:
                logger.warning(
                    "Partial profile set delivered as explicitly accepted;"
                    " %d profile(s) skipped",
                    len(missing_profiles),
                )
        source = Path(args.source_dir).expanduser()
        if source.is_symlink():
            raise ValueError("refusing symlink config library")
        if not args.dry_run:
            source.mkdir(parents=True, exist_ok=True, mode=0o700)
        index_path, index_content = _update_index(source, profiles)
        planned = []
        for preset, entry in profiles.items():
            path = (
                source
                / f"{uuid.uuid5(uuid.NAMESPACE_URL, 'dotfiles-claude-3p-' + preset)}.json"
            )
            content = json.dumps(entry, indent=2, sort_keys=True) + "\n"
            _validate_owned_profile(path, entry)
            _preflight_write(path, content, backup=not args.no_backup)
            planned.append((preset, path, content))
        # Validate all known conflicts before writing any individual entry.
        _preflight_write(index_path, index_content, backup=not args.no_backup)
        for preset, path, content in planned:
            changed = _atomic_write(
                path, content, backup=not args.no_backup, dry_run=args.dry_run
            )
            logger.info(
                "%s Claude-3p profile LiteLLM-%s",
                (
                    "Would write"
                    if args.dry_run
                    else ("Updated" if changed else "Unchanged")
                ),
                preset,
            )
            if not key:
                logger.info("LiteLLM-%s: %s", preset, KEY_INSTRUCTION)
        _atomic_write(
            index_path, index_content, backup=not args.no_backup, dry_run=args.dry_run
        )
        return 0
    except (OSError, ValueError, KeyError) as exc:
        logger.error("Claude-3p profile generation refused: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
