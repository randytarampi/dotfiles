#!/usr/bin/env python3
"""Verify structural invariants of oh-my-opencode-slim.json.

Model-assignment swaps are a frequent edit to configs/opencode/oh-my-opencode-slim.json
(see git history). A recurring bug class is fallback-array redundancy: a role's
primary model left in its own fallback list, a council fallback mirroring an
alpha/beta/gamma/synth member, or within-array duplicates. These waste fallback
slots and mask the real alternative order. This check catches them at `make verify`.

Invariants enforced:
  1. No role's primary model appears in that role's own fallback array.
  2. No council fallback entry mirrors an alpha/beta/gamma/synth member of the
     same tier's council preset.
  3. No within-array duplicates in any fallback array.
  4. Preset names are consistent across the role presets, council presets, and
     tier definitions.
  5. Top-level council alpha/beta/gamma models match each tier definition.
  6. Every configured model is present in its provider's model allowlist.

The fallback arrays live at `_tiers.<tier>.fallback.<role>`. Role primaries live
at `presets.<preset>.<role>.model` (the council synth primary is
`presets.<preset>.council.model`). Council member models live at
`council.presets.<tier>.{alpha,beta,gamma}.model`.

Exit codes:
  0 — all fallback invariants hold
  1 — one or more invariant violations found
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SLIM_PATH = REPO_ROOT / "configs" / "opencode" / "oh-my-opencode-slim.json"
MODEL_ALLOWLIST_PATHS = {
    "openai": REPO_ROOT / "configs" / "opencode" / "openai-models.json",
    "anthropic": REPO_ROOT / "configs" / "opencode" / "anthropic-models.json",
    "ollama-cloud": REPO_ROOT / "configs" / "opencode" / "ollama-cloud-models.json",
}


def _model(cfg):
    return cfg.get("model") if isinstance(cfg, dict) else None


def _role_primary(presets, council_presets, tier, role):
    """Primary model for a role in a tier.

    For the `council` role, the primary is the council synthesizer
    (presets.<tier>.council.model). For other roles it is presets.<tier>.<role>.model.
    """
    if role == "council":
        return _model(presets.get(tier, {}).get("council"))
    return _model(presets.get(tier, {}).get(role))


def _council_members(council_presets, tier):
    """Set of alpha/beta/gamma member models for a tier's council preset."""
    preset = council_presets.get(tier, {})
    members = set()
    for member in ("alpha", "beta", "gamma"):
        value = _model(preset.get(member))
        if value:
            members.add(value)
    return members


def _model_allowlists():
    """Load provider model IDs from the checked-in OpenCode allowlists."""
    allowlists = {}
    for provider, path in MODEL_ALLOWLIST_PATHS.items():
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
        models = config.get("models", {})
        allowlists[provider] = set(models)
        if isinstance(models, dict):
            allowlists[provider].update(
                entry.get("name")
                for entry in models.values()
                if isinstance(entry, dict) and entry.get("name")
            )
    return allowlists


def _iter_model_values(value, path=""):
    """Yield every model string, including fallback-array model entries."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if key == "model" and isinstance(child, str):
                yield child_path, child
            else:
                yield from _iter_model_values(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            if ".fallback." in path and isinstance(child, str):
                yield child_path, child
            else:
                yield from _iter_model_values(child, child_path)


def _model_allowlist_violations(data):
    """Validate provider-prefixed models and permit dynamic local models."""
    allowlists = _model_allowlists()
    violations = []
    for path, model in _iter_model_values(data):
        if model.startswith("_local:") or model.startswith("ollama/"):
            continue
        if "/" not in model:
            violations.append(
                f"{path} = {model!r} has no provider prefix or local placeholder"
            )
            continue
        provider, model_id = model.split("/", 1)
        if provider not in allowlists:
            violations.append(f"{path} = {model!r} uses unknown provider '{provider}'")
        elif model_id not in allowlists[provider]:
            violations.append(
                f"{path} = {model!r} is not in {provider} model allowlist"
            )
    return violations


def _preset_violations(presets, council_presets, tiers):
    """Validate preset names and council model synchronization."""
    violations = []
    preset_names = set(presets)
    council_names = set(council_presets)
    tier_names = set(tiers)
    all_names = preset_names | council_names | tier_names

    for label, names in (
        ("presets", preset_names),
        ("council.presets", council_names),
        ("_tiers", tier_names),
    ):
        for name in sorted(all_names - names):
            violations.append(f"{label} is missing preset name {name!r}")

    for tier in sorted(tier_names):
        tier_council = tiers.get(tier, {}).get("council", {})
        tier_presets = tier_council.get("presets", {})
        if set(tier_presets) != {tier}:
            violations.append(
                f"_tiers.{tier}.council.presets names {sorted(tier_presets)}; "
                f"expected [{tier!r}]"
            )

        top_preset = council_presets.get(tier, {})
        nested_preset = tier_presets.get(tier, {})
        for member in ("alpha", "beta", "gamma"):
            top_model = _model(top_preset.get(member))
            nested_model = _model(nested_preset.get(member))
            if top_model != nested_model:
                violations.append(
                    f"council.presets.{tier}.{member}.model = {top_model!r} "
                    f"does not match _tiers.{tier}.council.presets.{tier}."
                    f"{member}.model = {nested_model!r}"
                )
    return violations


def main():
    if not SLIM_PATH.exists():
        print(f"✗ {SLIM_PATH} not found")
        sys.exit(1)

    with open(SLIM_PATH) as f:
        data = json.load(f)

    presets = data.get("presets", {})
    tiers = data.get("_tiers", {})
    council_presets = data.get("council", {}).get("presets", {})

    violations = []
    violations.extend(_preset_violations(presets, council_presets, tiers))
    violations.extend(_model_allowlist_violations(data))

    for tier, tier_block in tiers.items():
        if not isinstance(tier_block, dict):
            continue
        fallback = tier_block.get("fallback", {})
        if not isinstance(fallback, dict):
            continue

        members = _council_members(council_presets, tier)
        synth = _model(presets.get(tier, {}).get("council"))
        if synth:
            members_with_synth = members | {synth}
        else:
            members_with_synth = members

        for role, arr in fallback.items():
            if not isinstance(arr, list):
                continue

            primary = _role_primary(presets, council_presets, tier, role)
            if role == "council":
                forbidden = members_with_synth
            else:
                forbidden = {primary} if primary else set()

            # 1 & 2: forbidden entries (primary in own fallback, or mirrored members)
            for idx, entry in enumerate(arr):
                if entry in forbidden:
                    label = (
                        "council member/synth" if role == "council" else "role primary"
                    )
                    violations.append(
                        f"_tiers.{tier}.fallback.{role}[{idx}] = {entry!r} "
                        f"duplicates {label} for tier '{tier}'"
                    )

            # 3: within-array duplicates
            seen = {}
            for idx, entry in enumerate(arr):
                if entry in seen:
                    violations.append(
                        f"_tiers.{tier}.fallback.{role}[{idx}] = {entry!r} "
                        f"duplicates earlier index {seen[entry]}"
                    )
                else:
                    seen[entry] = idx

    print("oh-my-opencode-slim invariants")
    print("=" * 60)

    if violations:
        print(f"\n\u26a0\ufe0f  {len(violations)} invariant violation(s):")
        for v in violations:
            print(f"  \u2717 {v}")
        print(
            "\nA role's fallback array must list *alternatives* to its primary, "
            "never the primary itself. A council fallback must not mirror an "
            "alpha/beta/gamma/synth member of the same tier. Remove the redundant "
            "entries (do not merely reorder)."
        )
        sys.exit(1)
    else:
        print(
            "\n\u2713 All slim invariants hold (fallback arrays, preset names, "
            "council synchronization, and model allowlists)."
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
