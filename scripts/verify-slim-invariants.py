#!/usr/bin/env python3
"""Verify structural invariants of oh-my-opencode-slim.json.

Model-assignment swaps are a frequent edit to configs/opencode/oh-my-opencode-slim.json
(see git history). A recurring bug class is fallback-array redundancy: a role's
primary model left in its own fallback list, a council fallback mirroring an
alpha/beta/gamma/synth member, or within-array duplicates. These waste fallback
slots and mask the real alternative order. This check catches them at `make verify`.

Invariants enforced:
  1. No role's primary model appears in that role's own fallback array
     (enforced by `_primary_chain_violations`).
  2. No council fallback entry mirrors an alpha/beta/gamma/synth member of the
     same tier's council preset.
  3. No within-array duplicates in any fallback array (enforced by
     `_model_dedupe_violations`).
  4. Preset names are consistent across the role presets, council presets, and
     tier definitions; council synthesizer models and variants are synchronized.
  5. Top-level council alpha/beta/gamma models match each tier definition.
  6. Every configured model is present in its provider's model allowlist.
  7. Fallback entries within an array use distinct provider prefixes.

The fallback arrays live at `_tiers.<tier>.fallback.<role>`. Role primaries live
at `presets.<preset>.<role>.model` (the council synth primary is
`presets.<preset>.council.model`). Council member models live at
`council.presets.<tier>.{alpha,beta,gamma}.model`.

Exit codes:
  0 — all fallback invariants hold
  1 — one or more invariant violations found
"""

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))
from constants import get_litellm_proxy_mode  # noqa: E402 -- scripts/lib bootstrap.
from litellm_aliases import (  # noqa: E402 -- scripts/lib bootstrap.
    canonical_allowlist_key,
    resolve_canonical_identity,
)  # noqa: E402

SLIM_PATH = REPO_ROOT / "configs" / "opencode" / "oh-my-opencode-slim.json"
MODEL_ALLOWLIST_PATHS = {
    "openai": REPO_ROOT / "configs" / "opencode" / "openai-models.json",
    "anthropic": REPO_ROOT / "configs" / "opencode" / "anthropic-models.json",
    "ollama-cloud": REPO_ROOT / "configs" / "opencode" / "ollama-cloud-models.json",
    "opencode": REPO_ROOT / "configs" / "opencode" / "opencode-models.json",
    "github-copilot": REPO_ROOT / "configs" / "opencode" / "github-copilot-models.json",
    "google": REPO_ROOT / "configs" / "opencode" / "google-models.json",
    "openrouter": REPO_ROOT / "configs" / "opencode" / "openrouter-models.json",
}
SUPPORTED_JUNIE_PROVIDERS = frozenset(MODEL_ALLOWLIST_PATHS) | {
    "litellm",
    "meridian",
    "ollama",
    "omlx",
}
LITELLM_MODEL_PREFIXES = (
    ("google/models/", "google"),
    ("openrouter/", "openrouter"),
    ("openai/", "openai"),
)


def _model(cfg):
    return cfg.get("model") if isinstance(cfg, dict) else None


def _variant(cfg):
    return cfg.get("variant") if isinstance(cfg, dict) else None


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
        # Canonicalize keys so bare selections match upstream-spelled entries
        # (e.g. OpenRouter ':free' vs the gateway's bare aliases).
        allowlists[provider] = (
            {canonical_allowlist_key(key) for key in models}
            if isinstance(models, dict)
            else set()
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


def _model_allowlist_violations(data, proxy_mode=None):
    """Validate provider-prefixed models and permit dynamic local models."""
    if proxy_mode is None:
        proxy_mode = get_litellm_proxy_mode()
    allowlists = _model_allowlists()
    violations = []
    for path, model in _iter_model_values(data):
        if model.startswith("litellm/"):
            if not proxy_mode:
                violations.append(f"{path} = {model!r} uses LiteLLM in direct mode")
                continue
            canonical = resolve_canonical_identity(model, True)
            if canonical is None:
                violations.append(f"{path} = {model!r} has malformed LiteLLM reference")
                continue
            model = canonical
        if model.startswith("_local:") or model.startswith("ollama/"):
            continue
        if "/" not in model:
            violations.append(
                f"{path} = {model!r} has no provider prefix or local placeholder"
            )
            continue
        provider, model_id = model.split("/", 1)
        allowlist_key = canonical_allowlist_key(model_id)
        if provider not in allowlists:
            violations.append(f"{path} = {model!r} uses unknown provider '{provider}'")
        elif allowlist_key not in allowlists[provider]:
            violations.append(
                f"{path} = {model!r} is not in {provider} model allowlist"
            )
    return violations


def _canonical_model_reference(provider, model):
    """Normalize direct/proxy Junie wire IDs without collapsing provider scope."""
    if provider == "meridian":
        return "meridian", model
    if provider == "litellm":
        for prefix, catalogue in LITELLM_MODEL_PREFIXES:
            if model.startswith(prefix):
                return catalogue, model[len(prefix) :]
        return None
    return provider, model


def _check_junie_model_reference(name, group, field, allowlists, violations):
    model = group.get(field)
    if not model:
        return
    provider = group.get("provider")
    if field == "fasterModel":
        provider = group.get("fasterProvider", provider)
    path = f"configs/junie/model-groups.json groups.{name}.{field}"
    if provider not in SUPPORTED_JUNIE_PROVIDERS:
        violations.append(f"{path} references unknown provider {provider!r}")
        return
    if provider == "meridian":
        # Bare Claude aliases are Meridian API identifiers, not Anthropic IDs.
        return
    if provider == "litellm":
        for prefix, catalogue in LITELLM_MODEL_PREFIXES:
            if model.startswith(prefix):
                _require_allowlisted_model(
                    catalogue, model[len(prefix) :], path, allowlists, violations
                )
                return
        violations.append(
            f"{path} = {model!r} has no supported LiteLLM provider prefix"
        )
        return
    if provider in {
        "google",
        "openai",
        "openrouter",
        "ollama-cloud",
        "github-copilot",
        "opencode",
    }:
        _require_allowlisted_model(provider, model, path, allowlists, violations)


def _require_allowlisted_model(provider, model, path, allowlists, violations):
    if model not in allowlists.get(provider, set()):
        violations.append(f"{path} = {model!r} is not in {provider} model allowlist")


def _check_litellm_group_pairings(groups, violations):
    for name, proxy_group in groups.items():
        if not name.startswith("litellm-"):
            continue
        direct_name = name[len("litellm-") :]
        direct_group = groups.get(direct_name)
        if not direct_group:
            violations.append(
                f"configs/junie/model-groups.json groups.{name} has no direct group {direct_name!r}"
            )
            continue
        for field in ("primaryModel", "fasterModel"):
            direct_model = direct_group.get(field)
            proxy_model = proxy_group.get(field)
            if not direct_model and not proxy_model:
                continue
            if not direct_model or not proxy_model:
                violations.append(
                    f"groups.{name}.{field} and groups.{direct_name}.{field} are not paired"
                )
                continue
            direct_provider = direct_group.get("provider")
            if field == "fasterModel":
                direct_provider = direct_group.get("fasterProvider", direct_provider)
            if _canonical_model_reference(
                direct_provider, direct_model
            ) != _canonical_model_reference("litellm", proxy_model):
                violations.append(
                    f"groups.{name}.{field} = {proxy_model!r} does not match direct group {direct_name!r} {field} = {direct_model!r}"
                )


def _check_codex_default_model(source, allowlists, violations):
    match = re.search(
        r"^DEFAULT_OLLAMA_CLOUD_MODEL = ['\"]([^'\"]+)", source, re.MULTILINE
    )
    if not match:
        violations.append(
            "scripts/configure-codex.py has no DEFAULT_OLLAMA_CLOUD_MODEL"
        )
        return
    _require_allowlisted_model(
        "ollama-cloud",
        match.group(1),
        "scripts/configure-codex.py DEFAULT_OLLAMA_CLOUD_MODEL",
        allowlists,
        violations,
    )


def _offline_model_parity_violations(junie=None, allowlists=None, codex_source=None):
    """Check managed Junie and Codex model references against curated catalogues."""
    allowlists = allowlists if allowlists is not None else _model_allowlists()
    if junie is None:
        junie_path = REPO_ROOT / "configs" / "junie" / "model-groups.json"
        with open(junie_path, encoding="utf-8") as f:
            junie = json.load(f)
    violations = []
    groups = junie.get("groups", {})

    for name, group in groups.items():
        if name.startswith("litellm-") and group.get("provider") == "litellm":
            faster_provider = group.get("fasterProvider")
            if faster_provider not in (None, "litellm"):
                violations.append(
                    f"configs/junie/model-groups.json groups.{name}.fasterProvider "
                    f"must be 'litellm' or absent, not {faster_provider!r}"
                )
        for field in ("primaryModel", "fasterModel"):
            _check_junie_model_reference(name, group, field, allowlists, violations)

    _check_litellm_group_pairings(groups, violations)
    codex_path = REPO_ROOT / "scripts" / "configure-codex.py"
    source = (
        codex_source
        if codex_source is not None
        else codex_path.read_text(encoding="utf-8")
    )
    _check_codex_default_model(source, allowlists, violations)
    return violations


def _provider_dedupe_violations(arr, path="fallback"):
    """Return violations for repeated providers within one fallback array."""
    seen = {}
    violations = []
    for idx, entry in enumerate(arr):
        provider = entry.split("/", 1)[0]
        if provider in seen:
            violations.append(
                f"{path}[{idx}] = {entry!r} repeats provider '{provider}' "
                f"from earlier index {seen[provider]}"
            )
        else:
            seen[provider] = idx
    return violations


def _primary_chain_violations(arr, primary, path):
    """Return violations when a role's primary appears in its fallback chain."""
    if primary is None:
        return []
    return [
        f"{path}[{idx}] = {entry!r} duplicates role primary"
        for idx, entry in enumerate(arr)
        if entry == primary
    ]


def _model_dedupe_violations(arr, path):
    """Return violations for repeated models within one fallback array."""
    seen = {}
    violations = []
    for idx, entry in enumerate(arr):
        if entry in seen:
            violations.append(
                f"{path}[{idx}] = {entry!r} duplicates earlier index {seen[entry]}"
            )
        else:
            seen[entry] = idx
    return violations


def _preset_violations(presets, council_presets, tiers):
    """Validate preset names and council model/synthesizer synchronization."""
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

        top_synth = _model(presets.get(tier, {}).get("council"))
        nested_synth = _model(nested_preset.get("council"))
        if (top_synth is None) != (nested_synth is None) or (
            top_synth is not None and top_synth != nested_synth
        ):
            violations.append(
                f"presets.{tier}.council.model = {top_synth!r} "
                f"does not match _tiers.{tier}.council.presets.{tier}."
                f"council.model = {nested_synth!r}"
            )

        top_variant = _variant(presets.get(tier, {}).get("council"))
        nested_variant = _variant(nested_preset.get("council"))
        if (top_variant is None) != (nested_variant is None) or (
            top_variant is not None and top_variant != nested_variant
        ):
            violations.append(
                f"presets.{tier}.council.variant = {top_variant!r} "
                f"does not match _tiers.{tier}.council.presets.{tier}."
                f"council.variant = {nested_variant!r}"
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
    violations.extend(_offline_model_parity_violations())

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

            # 1: role primary must not appear in its fallback chain.
            violations.extend(
                _primary_chain_violations(
                    arr, primary, f"_tiers.{tier}.fallback.{role}"
                )
            )

            # 2: council fallback entries must not mirror council members/synth.
            for idx, entry in enumerate(arr):
                if entry in forbidden and entry != primary:
                    label = (
                        "council member/synth" if role == "council" else "role primary"
                    )
                    violations.append(
                        f"_tiers.{tier}.fallback.{role}[{idx}] = {entry!r} "
                        f"duplicates {label} for tier '{tier}'"
                    )

            # 3: no model may repeat within one fallback chain.
            violations.extend(
                _model_dedupe_violations(arr, f"_tiers.{tier}.fallback.{role}")
            )

            # 7: fallback alternatives must be provider-diverse.
            violations.extend(
                _provider_dedupe_violations(arr, f"_tiers.{tier}.fallback.{role}")
            )

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
