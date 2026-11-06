"""Review-only, provider-scoped model inventory and PR reporting.

A public zero price and an authenticated model list are candidate evidence,
not a verified free entitlement, capability test or request-time admission.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from models_dev import MODELS_DEV_URL
from provider_endpoints import PROVIDER_ENDPOINTS

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = REPO_ROOT / "configs/opencode/opencode-models.json"
CONSOLE = "opencode"
CONSOLE_SOURCE = "https://opencode.ai/inference/v1/models"
SCHEMA_VERSION = 2


def provider_coverage(source: str) -> dict:
    """Do not mistake other installed provider configurations for live probes."""
    coverage = {
        key: {"status": "not_assessed"}
        for key in sorted(PROVIDER_ENDPOINTS)
        if key != CONSOLE
    }
    coverage[CONSOLE] = {"status": "observed", "source": source}
    return dict(sorted(coverage.items()))


def registry_review(
    candidates: list[dict], listed_ids: list[str], registry_path: Path = REGISTRY
) -> dict:
    """Compare the authoritative Console IDs with checked-in curated preferences."""
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    configured = registry.get("models") if isinstance(registry, dict) else None
    if not isinstance(configured, dict) or any(
        not isinstance(model, str)
        or not model.strip()
        or not isinstance(configured[model], dict)
        for model in configured
    ):
        raise ValueError("curated OpenCode model registry is malformed")
    if any(
        not isinstance(model, dict) or not isinstance(model.get("id"), str)
        for model in candidates
    ):
        raise ValueError("candidate inventory is malformed")
    curated = set(configured)
    zero = {model["id"] for model in candidates}
    listed = set(listed_ids)
    if not zero <= listed or len(zero) != len(candidates):
        raise ValueError("candidate inventory conflicts with Console listing")
    return {
        "new_candidates": sorted(zero - curated),
        "already_curated": sorted(zero & curated),
        "curated_not_listed": sorted(curated - listed),
        "curated_listed_not_zero": sorted((curated & listed) - zero),
    }


def _display(value: str) -> str:
    """Use safe inline HTML; backslashes do not escape Markdown code spans."""
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError("candidate name or provenance is too long or invalid")
    printable = "".join(
        character if character.isprintable() else f"\\u{ord(character):04x}"
        for character in value
    )
    escaped = html.escape(printable, quote=True).replace("`", "&#96;")
    return f"<code>{escaped}</code>"


def _bullets(values: list[str], *, limit: int = 30) -> str:
    if not values:
        return "- None.\n"
    rows = ["- " + _display(value) for value in values[:limit]]
    if len(values) > limit:
        rows.append(f"- … and {len(values) - limit} more.")
    return "\n".join(rows) + "\n"


def _as_models(payload: dict) -> dict:
    """Accept the old Zen JSON for a one-time bot-branch migration comparison."""
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("previous inventory has no models array")
    result = {}
    for model in payload["models"]:
        if (
            not isinstance(model, dict)
            or not isinstance(model.get("id"), str)
            or not isinstance(model.get("pricing"), dict)
            or model["id"] in result
        ):
            raise ValueError("previous inventory contains malformed model evidence")
        result[model["id"]] = model["pricing"]
    return result


def render_pr_body(current: dict, previous: dict | None = None) -> str:
    """Emit the repository PR template and distinguish evidence from entitlement."""
    if (
        not isinstance(current, dict)
        or current.get("schema_version") != SCHEMA_VERSION
        or current.get("status") != "available"
        or not isinstance(current.get("review"), dict)
        or not isinstance(current.get("provider_coverage"), dict)
    ):
        raise ValueError("only an available, provider-scoped inventory may publish")
    current_models = _as_models(current)
    prior_models = _as_models(previous) if previous is not None else {}
    review = current["review"]
    required = (
        "new_candidates",
        "already_curated",
        "curated_not_listed",
        "curated_listed_not_zero",
    )
    if any(
        not isinstance(review.get(key), list)
        or any(not isinstance(item, str) for item in review[key])
        for key in required
    ):
        raise ValueError("inventory review fields are malformed")
    if current.get("source") != CONSOLE_SOURCE:
        raise ValueError("model inventory must come from the Console model list")
    if current.get("pricing_source") != MODELS_DEV_URL:
        raise ValueError("model inventory has unexpected pricing provenance")
    if current["provider_coverage"] != provider_coverage(CONSOLE_SOURCE):
        raise ValueError("provider coverage does not match observed sources")
    others = sorted(name for name in PROVIDER_ENDPOINTS if name != CONSOLE)
    groups = {name: set(review[name]) for name in required}
    zero = set(current_models)
    if groups["new_candidates"] & groups["already_curated"]:
        raise ValueError("review partitions overlap")
    if groups["new_candidates"] | groups["already_curated"] != zero:
        raise ValueError("review does not cover the zero-priced candidates")
    if (
        (groups["curated_not_listed"] | groups["curated_listed_not_zero"]) & zero
        or groups["curated_not_listed"] & groups["curated_listed_not_zero"]
        or any(len(groups[name]) != len(review[name]) for name in required)
    ):
        raise ValueError("review contains overlapping or repeated model IDs")
    added = sorted(set(current_models) - set(prior_models))
    removed = sorted(set(prior_models) - set(current_models))
    price_changed = sorted(
        key
        for key in set(current_models) & set(prior_models)
        if current_models[key] != prior_models[key]
    )
    counts = current.get("counts", {})
    required_counts = ("catalogue", "classified", "free", "unclassified")
    if not isinstance(counts, dict) or any(
        type(counts.get(name)) is not int or counts[name] < 0
        for name in required_counts
    ):
        raise ValueError("candidate counts are invalid")
    if (
        counts["catalogue"] < 1
        or counts["classified"] != counts["catalogue"]
        or counts["free"] != len(current_models)
        or counts["unclassified"] != 0
    ):
        raise ValueError("candidate counts or classification incomplete")
    return (
        "## Outcome and scope\n\n"
        "Review-only free-model *candidates* from an authenticated Console "
        "model list and independent pricing metadata. Nothing here updates "
        "allowlists, presets, LiteLLM, Pi, Junie or runtime admission.\n\n"
        f"- **Observed provider:** OpenCode Console ({counts['catalogue']} listed; "
        f"{counts['free']} zero-price candidates; 0 unclassified).\n"
        f"- **Pricing evidence:** {_display(current['pricing_source'])}.\n"
        f"- **Not assessed in this hosted run:** {', '.join(others)}. "
        "These providers may participate in the general `free` preset via "
        "its separate, credential-bound dynamic resolver; they were not "
        "probed, and no zero-charge entitlement is inferred.\n\n"
        "### Newly discovered (not yet curated)\n\n"
        + _bullets(review["new_candidates"])
        + "\n### Already curated and still zero-priced in this snapshot\n\n"
        + _bullets(review["already_curated"])
        + "\n### Curated but missing from the live Console list\n\n"
        + _bullets(review["curated_not_listed"])
        + "\n### Curated and listed, but not zero-priced\n\n"
        + _bullets(review["curated_listed_not_zero"])
        + "\n### Since the preceding bot inventory\n\n"
        + (
            "Previous bot inventory found.\n\n"
            if previous
            else "First known bot inventory.\n\n"
        )
        + "**Newly zero-priced:**\n"
        + _bullets(added)
        + "\n**No longer zero-priced or no longer listed:**\n"
        + _bullets(removed)
        + "\n**Changed zero-price tier details:**\n"
        + _bullets(price_changed)
        + "\n## Contributors and roles\n\n"
        "- **Implementation and other material contributions:** "
        "Woofdy GitHub App — automatically fetched evidence and committed this "
        "generated candidate report. The generator implementation is reviewed "
        "in its separate source-code PR.\n"
        "- **Review:** None yet; this draft is not an approval.\n\n"
        "## Evidence\n\n"
        "- Integrated `make verify`: NOT RUN by this refresh workflow; "
        "the source-code change has its own CI.\n"
        "- Required CI: see checks attached to this generated PR head; "
        "not assumed passing.\n"
        "- Host/deploy validation: NOT RUN. Credentialed catalogue access "
        "and public pricing do not establish actual inference, tool/image "
        "support, account entitlement or zero-charge fallbacks.\n\n"
        "## Review disposition\n\n"
        "Draft only. Investigate newly discovered, missing and re-priced "
        "candidates. Never merge this snapshot as an automatic model promotion. "
        "Do not enable paid or unqualified routes in strict-free presets.\n\n"
        "## Risk and handoff\n\n"
        "Source and observed price windows can expire or diverge from account "
        "billing. Preserve the existing dynamic multi-provider resolver from "
        "#119, curated preferences from #139 and opt-in provider limits from "
        "#142. Request-time no-charge enforcement remains tracked in #87. "
        "Rollback by closing this draft, not by rewriting deployed configs.\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    current = json.loads(args.current.read_text(encoding="utf-8"))
    previous = (
        json.loads(args.previous.read_text(encoding="utf-8"))
        if args.previous and args.previous.is_file()
        else None
    )
    output = render_pr_body(current, previous)
    args.output.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
