"""Plan and create unselected, generated pi-subagents profile artifacts."""

import hashlib
import json
import re
import os
from pathlib import Path

from tier_registry import materialize_role_models

PROFILE_SCHEMA = 1
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build_profile(registry, preset, categories, role_to_builtin, role_overrides=None):
    roles = materialize_role_models(registry, preset, categories, role_overrides)
    preset_roles = registry["presets"][preset]
    overrides = {}
    for role, builtin in role_to_builtin.items():
        model = roles.get(role)
        if (
            not isinstance(model, str)
            or "/" not in model
            or model.startswith("_local:")
            or not all(model.split("/", 1))
        ):
            raise ValueError(
                f"Unresolved or unqualified model for {role}; profile not generated"
            )
        entry = {"model": model}
        variant = preset_roles.get(role, {}).get("variant")
        if variant:
            if not isinstance(variant, str):
                raise ValueError(f"Invalid thinking value for {role}")
            entry["thinking"] = variant
        overrides[builtin] = entry
    return {"subagents": {"agentOverrides": overrides}}


def profile_target(agent_dir, name, mode):
    if not SAFE_NAME.fullmatch(name) or ".." in name:
        raise ValueError("unsafe preset name")
    if mode != "global":
        raise ValueError("Installed profile loader supports only global profiles")
    return Path(agent_dir) / "profiles" / "pi-subagents" / f"{name}.json"


def _manifest_path(agent_dir, name):
    # Sibling to profiles/, never inside the plugin's profile search directory.
    return Path(agent_dir) / "profiles" / ".dotfiles-pi-subagents" / f"{name}.json"


def plan_profile(agent_dir, name, profile, source_digest, mode="global"):
    target = profile_target(agent_dir, name, mode)
    manifest = _manifest_path(agent_dir, name)
    if any(
        path.is_symlink()
        for path in (target, *target.parents, manifest, *manifest.parents)
    ):
        return {"disposition": "REFUSED_OWNERSHIP", "reason": "symlink path"}
    try:
        _validate_existing_private_directory(target.parent)
        _validate_existing_private_directory(manifest.parent)
    except OSError as error:
        return {
            "disposition": "REFUSED_OWNERSHIP",
            "reason": str(error),
            "target": target,
            "manifest": manifest,
        }
    encoded = (json.dumps(profile, sort_keys=True, indent=2) + "\n").encode()
    if not target.exists():
        if manifest.exists():
            return {"disposition": "REFUSED_OWNERSHIP", "reason": "orphaned manifest"}
        return {
            "disposition": "GENERATE_ONLY",
            "target": target,
            "manifest": manifest,
            "content": encoded,
            "source_digest": source_digest,
            "artifact_digest": digest(encoded),
        }
    if not target.is_file() or not manifest.is_file():
        return {"disposition": "REFUSED_OWNERSHIP", "reason": "unowned artifact"}
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
            raise ValueError("invalid manifest")
        record = data["profiles"][name]
        if not isinstance(record, dict):
            raise ValueError("invalid record")
        current = target.read_bytes()
    except (OSError, ValueError, KeyError, TypeError):
        return {
            "disposition": "REFUSED_OWNERSHIP",
            "reason": "invalid ownership record",
        }
    if (
        data.get("schema") != PROFILE_SCHEMA
        or data.get("generator") != "configure-pi.py"
        or record.get("target") != str(target)
        or record.get("artifact_digest") != digest(current)
    ):
        return {"disposition": "REFUSED_OWNERSHIP", "reason": "artifact changed"}
    if current == encoded and record.get("source_digest") == source_digest:
        return {"disposition": "UNCHANGED", "target": target}
    return {
        "disposition": "REFUSED_CONCURRENCY_UNPROVEN",
        "reason": "no exclusive update boundary",
    }


def create_profile(plan):
    """Exclusive-create profile, then exclusive-create provenance manifest."""
    if plan.get("disposition") != "GENERATE_ONLY":
        return plan.get("disposition")
    target, manifest = plan["target"], plan["manifest"]
    try:
        _ensure_private_directory(target.parent)
        with target.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(plan["content"])
    except FileExistsError:
        return "REFUSED_CONCURRENCY_UNPROVEN"
    except OSError:
        return "REFUSED_OWNERSHIP"
    try:
        if manifest.exists() or manifest.is_symlink():
            return "REFUSED_CONCURRENCY_UNPROVEN"
        document = {
            "schema": PROFILE_SCHEMA,
            "generator": "configure-pi.py",
            "profiles": {},
        }
        document["profiles"][target.stem] = {
            "target": str(target),
            "source_digest": plan["source_digest"],
            "artifact_digest": plan["artifact_digest"],
        }
        _ensure_private_directory(manifest.parent)
        with manifest.open("x", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except (FileExistsError, OSError):
        # Preserve orphaned artifacts for explicit recovery; checking then
        # unlinking would race another writer and could remove its content.
        return "REFUSED_CONCURRENCY_UNPROVEN"
    return "GENERATE_ONLY"


def _ensure_private_directory(directory):
    """Create private leaves; refuse existing directories with unsafe ownership or mode."""
    missing = []
    current = directory
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            raise OSError(f"no existing parent for private directory: {directory}")
        current = parent

    for leaf in reversed(missing):
        leaf.mkdir(mode=0o700)

    _validate_existing_private_directory(directory)


def _validate_existing_private_directory(directory):
    """Read-only validation shared by planning and execution."""
    if not directory.exists():
        return
    metadata = directory.stat()
    owner = getattr(os, "getuid", lambda: None)()
    if (
        not directory.is_dir()
        or (owner is not None and metadata.st_uid != owner)
        or metadata.st_mode & 0o777 != 0o700
    ):
        raise OSError(f"unsafe profile directory ownership or permissions: {directory}")
