#!/usr/bin/env python3
"""Verify structural invariants of oh-my-opencode-slim.json fallback arrays.

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

    print("oh-my-opencode-slim fallback invariants")
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
            "\n\u2713 All fallback invariants hold (no primary-in-own-fallback, "
            "no council-member mirrors, no within-array dupes)."
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
