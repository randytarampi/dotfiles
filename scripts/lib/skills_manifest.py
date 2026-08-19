#!/usr/bin/env python3
"""Shared manifest parsing, profile gating, and stale skill detection for skills management.

Used by configure-skills.py to reconcile installed skills against the declarative manifest
in configs/skills/skills.json.
"""

import json
import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SELF = os.path.realpath(__file__)
SCRIPT_DIR = os.path.dirname(SELF)
DOTFILES_DIR = os.path.dirname(SCRIPT_DIR)
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")

if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger  # noqa: E402

HOME = os.path.expanduser("~")
JUNIE_DIR = os.path.realpath(os.path.join(HOME, ".junie"))

SKILL_TARGET_DIRS = [
    os.path.join(HOME, ".agents", "skills"),
    os.path.join(HOME, ".config", "opencode", "skills"),
    os.path.join(HOME, ".claude", "skills"),
    os.path.join(HOME, ".gemini", "skills"),
    os.path.join(HOME, ".codex", "skills"),
    os.path.join(HOME, ".cursor", "skills"),
    os.path.join(JUNIE_DIR, "skills"),
    os.path.join(HOME, ".copilot", "skills"),
    os.path.join(HOME, ".pi", "agent", "skills"),
    os.path.join(HOME, ".snowflake", "cortex", "skills"),
    os.path.join(HOME, ".gemini", "antigravity-cli", "skills"),
]

CANONICAL_STORE = os.path.join(HOME, ".local", "share", "dotfiles", "skills")


@dataclass
class SkillEntry:
    """A single skill from the manifest."""

    source: str
    name: str
    profile: str = "global"
    is_repo_local: bool = False

    @property
    def skill_id(self):
        """Unique identifier for this skill: source/name."""
        return f"{self.source}/{self.name}"


@dataclass
class ManifestProfile:
    """A profile from the manifest."""

    name: str
    description: str
    gate: Optional[str] = None
    platform: Optional[str] = None
    skills: list[SkillEntry] = field(default_factory=list)

    @property
    def is_active(self):
        """Check if this profile should be active based on gate and platform."""
        if self.platform and platform.system().lower() != self.platform:
            return False
        if self.gate:
            return os.environ.get(self.gate, "0") == "1"
        # global profile is always active
        return True


def load_manifest(manifest_path: str) -> dict:
    """Load the index and category manifests, merging their declarations.

    A non-index file remains supported for callers using the historical single
    manifest format. The skills.json index is merged with all skills.*.json
    category files when passed directly or when the directory is passed.
    """
    if os.path.isdir(manifest_path):
        manifest_dir = manifest_path
        paths = [os.path.join(manifest_dir, "skills.json")] + sorted(
            os.path.join(manifest_dir, name)
            for name in os.listdir(manifest_dir)
            if name.startswith("skills.") and name.endswith(".json")
        )
    elif (
        os.path.isfile(manifest_path)
        and os.path.basename(manifest_path) == "skills.json"
    ):
        manifest_dir = os.path.dirname(manifest_path)
        paths = [manifest_path] + sorted(
            os.path.join(manifest_dir, name)
            for name in os.listdir(manifest_dir)
            if name.startswith("skills.") and name.endswith(".json")
        )
    elif os.path.isfile(manifest_path):
        paths = [manifest_path]
    else:
        logger.warning("Skills manifest not found: %s", manifest_path)
        return {}

    merged = {
        "profiles": {},
        "preinstalled": {"skills": []},
        "repo_local": {"skills": []},
    }
    for path in dict.fromkeys(paths):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse manifest %s: %s", path, exc)
            return {}

        for profile_name, profile_data in data.get("profiles", {}).items():
            if profile_name in merged["profiles"]:
                raise ValueError(f"Duplicate skills profile name: {profile_name}")
            merged["profiles"][profile_name] = profile_data
        for section in ("preinstalled", "repo_local"):
            merged[section]["skills"] = list(
                dict.fromkeys(
                    merged[section]["skills"] + data.get(section, {}).get("skills", [])
                )
            )

    return merged


def parse_manifest(manifest_path: str) -> list[SkillEntry]:
    """Parse the manifest and return the list of active skill entries.

    Respects profile gates and platform filters.
    Only returns skills from active profiles.
    """
    manifest = load_manifest(manifest_path)
    if not manifest:
        return []

    active_skills: list[SkillEntry] = []

    profiles = manifest.get("profiles", {})
    for profile_name, profile_data in profiles.items():
        profile = ManifestProfile(
            name=profile_name,
            description=profile_data.get("description", ""),
            gate=profile_data.get("gate"),
            platform=profile_data.get("platform"),
        )

        if not profile.is_active:
            logger.info(
                "Profile '%s' not active (gate=%s, platform=%s)",
                profile_name,
                profile.gate or "none",
                profile.platform or "any",
            )
            continue

        for skill in profile_data.get("skills", []):
            active_skills.append(
                SkillEntry(
                    source=skill["source"],
                    name=skill["name"],
                    profile=profile_name,
                    is_repo_local=False,
                )
            )

    # Repo-local skills (stored directly in configs/skills/)
    repo_local = manifest.get("repo_local", {})
    for skill in repo_local.get("skills", []):
        active_skills.append(
            SkillEntry(
                source=skill.get("source", "local"),
                name=skill["name"],
                profile="repo_local",
                is_repo_local=True,
            )
        )

    return active_skills


def _profile_skills(manifest_path: str, active_only: bool) -> list[SkillEntry]:
    """Return profile entries, optionally applying profile activation rules."""
    manifest = load_manifest(manifest_path)
    if not manifest:
        return []

    entries = []
    for profile_name, profile_data in manifest.get("profiles", {}).items():
        profile = ManifestProfile(
            name=profile_name,
            description=profile_data.get("description", ""),
            gate=profile_data.get("gate"),
            platform=profile_data.get("platform"),
        )
        if active_only and not profile.is_active:
            continue
        entries.extend(
            SkillEntry(
                source=skill["source"],
                name=skill["name"],
                profile=profile_name,
            )
            for skill in profile_data.get("skills", [])
        )
    return entries


def _declared_non_profile_skills(manifest: dict) -> list[SkillEntry]:
    """Return preinstalled and repo-local declarations as SkillEntry objects."""
    entries = [
        SkillEntry(source="preinstalled", name=name, profile="preinstalled")
        for name in manifest.get("preinstalled", {}).get("skills", [])
    ]
    entries.extend(
        SkillEntry(
            source=skill.get("source", "local"),
            name=skill["name"],
            profile="repo_local",
            is_repo_local=True,
        )
        for skill in manifest.get("repo_local", {}).get("skills", [])
    )
    return entries


def _unique_skill_entries(entries: list[SkillEntry]) -> list[SkillEntry]:
    """Deduplicate declarations by skill name, preserving manifest order."""
    seen = set()
    unique = []
    for entry in entries:
        if entry.name not in seen:
            seen.add(entry.name)
            unique.append(entry)
    return unique


def get_catalog_skills(manifest_path: str) -> list[SkillEntry]:
    """Return every profile/repo-local skill, including inactive gated profiles.

    Preinstalled entries are discovery declarations, not catalog cache entries;
    they are included by ``get_globally_active_skills`` instead.
    """
    manifest = load_manifest(manifest_path)
    if not manifest:
        return []
    return _unique_skill_entries(
        _profile_skills(manifest_path, active_only=False)
        + [
            entry
            for entry in _declared_non_profile_skills(manifest)
            if entry.is_repo_local
        ]
    )


def get_globally_active_skills(manifest_path: str) -> list[SkillEntry]:
    """Return active profile skills plus preinstalled and repo-local skills."""
    manifest = load_manifest(manifest_path)
    if not manifest:
        return []
    return _unique_skill_entries(
        _profile_skills(manifest_path, active_only=True)
        + _declared_non_profile_skills(manifest)
    )


def get_preinstalled_skills(manifest_path: str) -> set[str]:
    """Return the set of preinstalled skill names from the manifest.

    These skills are already installed by other means (e.g. Plannotator installer).
    configure-skills.py symlinks them but does not fetch or remove them.
    """
    manifest = load_manifest(manifest_path)
    if not manifest:
        return set()
    preinstalled = manifest.get("preinstalled", {})
    return set(preinstalled.get("skills", []))


def get_active_skill_names(active_skills: list[SkillEntry]) -> set[str]:
    """Return the set of skill names from the active manifest entries."""
    return {skill.name for skill in active_skills}


def get_installed_skills(canonical_store: str = CANONICAL_STORE) -> set[str]:
    """Return the set of skill names currently installed in the canonical store."""
    if not os.path.isdir(canonical_store):
        return set()
    return {
        entry
        for entry in os.listdir(canonical_store)
        if os.path.isdir(os.path.join(canonical_store, entry))
        and not entry.startswith(".")
    }


def get_symlinked_skills(target_dir: str) -> set[str]:
    """Return the set of skill names symlinked (or copied) into a target agent dir."""
    if not os.path.isdir(target_dir):
        return set()
    return {
        entry
        for entry in os.listdir(target_dir)
        if (
            os.path.islink(os.path.join(target_dir, entry))
            or os.path.isdir(os.path.join(target_dir, entry))
        )
        and not entry.startswith(".")
    }


def find_stale_skills(active_names: set[str], installed_names: set[str]) -> set[str]:
    """Return skills that are installed but not in the active manifest."""
    return installed_names - active_names


def find_missing_skills(active_names: set[str], installed_names: set[str]) -> set[str]:
    """Return skills that are in the manifest but not yet installed."""
    return active_names - installed_names


def fetch_skill_via_cli(source: str, name: str, dry_run: bool = False) -> bool:
    """Fetch a skill from upstream via the `skills` CLI into the canonical store.

    Returns True on success (or dry-run), False on failure.
    """
    import shutil
    import subprocess

    skills_cli = shutil.which("skills")
    if not skills_cli:
        logger.warning(
            "`skills` CLI not found on PATH — cannot fetch %s/%s", source, name
        )
        return False

    skill_id = f"{source}/{name}"

    if dry_run:
        logger.info("[DRY RUN] Would fetch: %s", skill_id)
        return True

    try:
        result = subprocess.run(
            [skills_cli, "add", source, "--skill", name, "--global", "-y"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        # The CLI historically installs into ~/.agents/skills/. Relocate that
        # result into our non-discovered canonical store when necessary.
        skill_dir = os.path.join(CANONICAL_STORE, name)
        legacy_dir = os.path.join(os.path.expanduser("~"), ".agents", "skills", name)
        if not os.path.isdir(skill_dir) and os.path.isdir(legacy_dir):
            os.makedirs(CANONICAL_STORE, exist_ok=True)
            os.replace(legacy_dir, skill_dir)
        if os.path.isdir(skill_dir):
            logger.info("Fetched: %s", skill_id)
            return True
        else:
            logger.warning(
                "Failed to fetch %s: %s",
                skill_id,
                result.stderr.strip() or result.stdout.strip(),
            )
            return False
    except subprocess.TimeoutExpired:
        logger.warning("Timeout fetching %s", skill_id)
        return False
    except Exception as exc:
        logger.warning("Error fetching %s: %s", skill_id, exc)
        return False


def install_repo_local_skill(
    skill_name: str,
    source_dir: str,
    canonical_store: str = CANONICAL_STORE,
    dry_run: bool = False,
) -> bool:
    """Copy a repo-local skill directory into the canonical store.

    Copies the full skill tree (SKILL.md + references/ + any other files).
    """
    import shutil as shutil_mod

    target_dir = os.path.join(canonical_store, skill_name)

    if dry_run:
        logger.info(
            "[DRY RUN] Would copy repo-local skill: %s -> %s", source_dir, target_dir
        )
        return True

    try:
        if os.path.exists(target_dir):
            shutil_mod.rmtree(target_dir)
        shutil_mod.copytree(source_dir, target_dir)
        logger.info("Copied repo-local skill: %s -> %s", source_dir, target_dir)
        return True
    except Exception as exc:
        logger.warning("Failed to copy repo-local skill %s: %s", skill_name, exc)
        return False


def symlink_skill_to_targets(
    skill_name: str,
    canonical_store: str = CANONICAL_STORE,
    target_dirs: Optional[list[str]] = None,
    dry_run: bool = False,
) -> int:
    """Symlink a skill from the canonical store to all agent target dirs.

    Falls back to directory copy on Windows or when symlinks fail.
    Returns the number of targets successfully linked.
    """
    import shutil as shutil_mod

    if target_dirs is None:
        target_dirs = SKILL_TARGET_DIRS

    source_path = os.path.join(canonical_store, skill_name)

    if not os.path.isdir(source_path):
        logger.warning("Source skill not found in canonical store: %s", source_path)
        return 0

    linked = 0
    is_windows = platform.system() == "Windows"

    for target_base in target_dirs:
        target_path = os.path.join(target_base, skill_name)

        # Skip if target is the canonical store itself
        if os.path.realpath(target_base) == os.path.realpath(canonical_store):
            continue

        if dry_run:
            logger.info("[DRY RUN] Would symlink: %s -> %s", source_path, target_path)
            linked += 1
            continue

        try:
            os.makedirs(target_base, exist_ok=True)

            # Preserve an already-correct symlink so reconciliation is idempotent.
            if os.path.islink(target_path) and os.path.realpath(
                target_path
            ) == os.path.realpath(source_path):
                linked += 1
                continue

            # Remove existing symlink/dir if present
            if os.path.islink(target_path) or os.path.exists(target_path):
                if os.path.islink(target_path):
                    os.unlink(target_path)
                elif os.path.isdir(target_path):
                    shutil_mod.rmtree(target_path)
                else:
                    os.unlink(target_path)

            if is_windows:
                # Windows: copy directory (symlinks need admin/Developer Mode)
                shutil_mod.copytree(source_path, target_path)
            else:
                os.symlink(source_path, target_path)

            linked += 1
        except OSError as exc:
            logger.warning("Failed to link %s -> %s: %s", source_path, target_path, exc)
            # Fallback to copy
            try:
                if os.path.exists(target_path):
                    shutil_mod.rmtree(target_path)
                shutil_mod.copytree(source_path, target_path)
                linked += 1
            except Exception as copy_exc:
                logger.warning("Copy fallback also failed: %s", copy_exc)

    return linked


def remove_stale_from_targets(
    stale_names: set[str],
    target_dirs: Optional[list[str]] = None,
    canonical_store: str = CANONICAL_STORE,
    dry_run: bool = False,
) -> int:
    """Remove stale skills from all agent target dirs (but not the canonical store).

    Returns the number of removals.
    """
    import shutil as shutil_mod

    if target_dirs is None:
        target_dirs = SKILL_TARGET_DIRS

    removed = 0
    for skill_name in sorted(stale_names):
        for target_base in target_dirs:
            # Don't remove from canonical store — that's handled by CLI
            if os.path.realpath(target_base) == os.path.realpath(canonical_store):
                continue

            target_path = os.path.join(target_base, skill_name)
            if not os.path.exists(target_path) and not os.path.islink(target_path):
                continue

            if dry_run:
                logger.info("[DRY RUN] Would remove stale: %s", target_path)
                removed += 1
                continue

            try:
                if os.path.islink(target_path):
                    os.unlink(target_path)
                elif os.path.isdir(target_path):
                    shutil_mod.rmtree(target_path)
                else:
                    os.unlink(target_path)
                removed += 1
            except OSError as exc:
                logger.warning("Failed to remove %s: %s", target_path, exc)

    return removed


def remove_stale_from_canonical_store(
    stale_names: set[str],
    canonical_store: str = CANONICAL_STORE,
    dry_run: bool = False,
) -> int:
    """Remove stale skill directories from the custom canonical cache."""
    import shutil as shutil_mod

    removed = 0
    for skill_name in sorted(stale_names):
        path = os.path.join(canonical_store, skill_name)
        if not os.path.lexists(path):
            continue
        if dry_run:
            logger.info("[DRY RUN] Would remove stale canonical skill: %s", path)
            removed += 1
            continue
        try:
            if os.path.islink(path) or not os.path.isdir(path):
                os.unlink(path)
            else:
                shutil_mod.rmtree(path)
            removed += 1
        except OSError as exc:
            logger.warning("Failed to remove canonical skill %s: %s", skill_name, exc)
    return removed


def remove_stale_from_lock_file(
    stale_names: set[str],
    lock_path: Optional[str] = None,
    dry_run: bool = False,
) -> int:
    """Remove stale skill names from the CLI lock file."""
    lock_path = lock_path or os.path.join(HOME, ".agents", ".skill-lock.json")
    if not os.path.isfile(lock_path):
        return 0
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        skills = data.get("skills", [])

        def entry_name(entry):
            return entry if isinstance(entry, str) else entry.get("name")

        kept = [entry for entry in skills if entry_name(entry) not in stale_names]
        removed = len(skills) - len(kept)
        if removed and not dry_run:
            data["skills"] = kept
            with open(lock_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
        if removed:
            logger.info(
                "%s %d stale lock entries",
                "Would remove" if dry_run else "Removed",
                removed,
            )
        return removed
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to clean skill lock file %s: %s", lock_path, exc)
        return 0


def verify_reconciliation(manifest_path: str) -> dict:
    """Check canonical and discovery stores after reconciliation."""
    catalog_names = {entry.name for entry in get_catalog_skills(manifest_path)}
    active_names = {entry.name for entry in get_globally_active_skills(manifest_path)}
    installed_names = get_installed_skills(CANONICAL_STORE)
    violations = []
    if installed_names != catalog_names:
        violations.append(
            f"canonical store differs (missing={sorted(catalog_names - installed_names)}, "
            f"extra={sorted(installed_names - catalog_names)})"
        )

    broken_links = []
    inactive_links = []
    for target_dir in SKILL_TARGET_DIRS:
        if not os.path.isdir(target_dir):
            continue
        for name in os.listdir(target_dir):
            path = os.path.join(target_dir, name)
            if name.startswith("."):
                continue
            if os.path.islink(path):
                if not os.path.exists(path):
                    broken_links.append(path)
                elif name not in active_names and os.path.realpath(
                    path
                ) == os.path.realpath(os.path.join(CANONICAL_STORE, name)):
                    inactive_links.append(path)
    if broken_links:
        violations.append(f"{len(broken_links)} broken symlinks")
    if inactive_links:
        violations.append(f"{len(inactive_links)} inactive managed symlinks")

    lock_path = os.path.join(HOME, ".agents", ".skill-lock.json")
    lock_names = set()
    if os.path.isfile(lock_path):
        try:
            with open(lock_path, "r", encoding="utf-8") as f:
                lock_names = {
                    entry if isinstance(entry, str) else entry.get("name")
                    for entry in json.load(f).get("skills", [])
                }
        except (OSError, json.JSONDecodeError):
            violations.append("skill lock file is unreadable")
    stale_lock = lock_names - catalog_names
    if stale_lock:
        violations.append(f"{len(stale_lock)} stale lock entries")
    return {
        "catalog": len(catalog_names),
        "active": len(active_names),
        "removed": len(stale_lock),
        "broken_links": len(broken_links),
        "violations": violations,
    }


def remove_stale_via_cli(stale_names: set[str], dry_run: bool = False) -> int:
    """Remove stale skills with the `skills` CLI from all managed locations.

    The CLI removes skills from both the canonical store and agent target dirs.
    Returns the number of skills successfully removed (or previewed).
    """
    import shutil
    import subprocess

    skills_cli = shutil.which("skills")
    if not skills_cli:
        logger.warning("`skills` CLI not found on PATH — cannot remove stale skills")
        return 0

    removed = 0
    for skill_name in sorted(stale_names):
        if dry_run:
            logger.info("[DRY RUN] Would run: skills remove %s --global -y", skill_name)
            removed += 1
            continue

        try:
            result = subprocess.run(
                [skills_cli, "remove", skill_name, "--global", "-y"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                logger.info("Removed stale skill: %s", skill_name)
                removed += 1
            else:
                logger.warning(
                    "Failed to remove stale skill %s: %s",
                    skill_name,
                    result.stderr.strip() or result.stdout.strip(),
                )
        except subprocess.TimeoutExpired:
            logger.warning("Timeout removing stale skill: %s", skill_name)
        except Exception as exc:
            logger.warning("Error removing stale skill %s: %s", skill_name, exc)

    return removed
