"""Review-only, provider-scoped model inventory and PR reporting.

A public zero price and an authenticated model list are candidate evidence,
not a verified free entitlement, capability test or request-time admission.
"""

from __future__ import annotations

import argparse
import hashlib
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
INVALID_REVIEW = "inventory review fields are malformed"


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


OBSERVATION_FIELDS = frozenset(
    {"fetched_at", "pricing_observed_at", "pricing_expires_at"}
)


def review_fingerprint(current: dict) -> str:
    """Fingerprint candidate evidence without its observation timestamps."""
    stable = {
        key: value for key, value in current.items() if key not in OBSERVATION_FIELDS
    }
    canonical = json.dumps(
        stable, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validated_review_partitions(review: dict, zero: set[str]) -> None:
    required = (
        "new_candidates",
        "already_curated",
        "curated_not_listed",
        "curated_listed_not_zero",
    )
    for key in required:
        members = review.get(key)
        if not isinstance(members, list):
            raise ValueError(INVALID_REVIEW)
        if any(not isinstance(item, str) for item in members):
            raise ValueError(INVALID_REVIEW)
        if len(members) != len(set(members)):
            raise ValueError("review contains repeated model IDs")

    groups = {name: set(review[name]) for name in required}
    if groups["new_candidates"] & groups["already_curated"]:
        raise ValueError("review partitions overlap")
    if groups["new_candidates"] | groups["already_curated"] != zero:
        raise ValueError("review does not cover the zero-priced candidates")
    if (groups["curated_not_listed"] | groups["curated_listed_not_zero"]) & zero:
        raise ValueError("review contains overlapping model IDs")
    if groups["curated_not_listed"] & groups["curated_listed_not_zero"]:
        raise ValueError("review partitions overlap")


def _validated_counts(counts: dict, free_count: int) -> None:
    if not isinstance(counts, dict):
        raise ValueError("candidate counts are invalid")
    for field in ("catalogue", "classified", "free", "unclassified"):
        if type(counts.get(field)) is not int or counts[field] < 0:
            raise ValueError("candidate counts are invalid")
    if counts["catalogue"] < 1:
        raise ValueError("empty catalogue is not publishable")
    if counts["classified"] != counts["catalogue"]:
        raise ValueError("candidate classifications are incomplete")
    if counts["free"] != free_count:
        raise ValueError("candidate free count disagrees with models")
    if counts["unclassified"] != 0:
        raise ValueError("candidate counts or classification incomplete")


def _validated_current(current: dict) -> tuple[dict, dict, list[str], dict]:
    if not isinstance(current, dict):
        raise ValueError("only a provider-scoped inventory may publish")
    if current.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unknown candidate inventory schema")
    if current.get("status") != "available":
        raise ValueError("only an available inventory may publish")
    if current.get("source") != CONSOLE_SOURCE:
        raise ValueError("model inventory must come from the Console model list")
    if current.get("pricing_source") != MODELS_DEV_URL:
        raise ValueError("model inventory has unexpected pricing provenance")
    if current.get("provider_coverage") != provider_coverage(CONSOLE_SOURCE):
        raise ValueError("provider coverage does not match observed sources")

    current_models = _as_models(current)
    review = current.get("review")
    if not isinstance(review, dict):
        raise ValueError(INVALID_REVIEW)
    _validated_review_partitions(review, set(current_models))
    counts = current.get("counts")
    _validated_counts(counts, len(current_models))
    others = sorted(name for name in PROVIDER_ENDPOINTS if name != CONSOLE)
    return current_models, review, others, counts


def render_pr_body(current: dict, previous: dict | None = None) -> str:
    """Emit the repository PR template and distinguish evidence from entitlement."""
    current_models, review, others, counts = _validated_current(current)
    prior_models = _as_models(previous) if previous is not None else {}
    added = sorted(set(current_models) - set(prior_models))
    removed = sorted(set(prior_models) - set(current_models))
    price_changed = sorted(
        key
        for key in set(current_models) & set(prior_models)
        if current_models[key] != prior_models[key]
    )
    if previous is None:
        previous_note = "First known bot inventory.\n\n"
    elif prior_models == current_models:
        previous_note = (
            "The current bot tip already contains this model/price set. "
            "An earlier distinct baseline cannot be recovered from that tip.\n\n"
        )
    else:
        previous_note = "Compared with the preceding bot inventory.\n\n"

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
        + previous_note
        + "**Newly zero-priced:**\n"
        + _bullets(added)
        + "\n**No longer zero-priced or no longer listed:**\n"
        + _bullets(removed)
        + "\n**Changed zero-price tier details:**\n"
        + _bullets(price_changed)
        + "\n## Contributors and roles\n\n"
        "- **Implementation and other material contributions:** "
        "Woofdy GitHub App — published and committed the candidate report. "
        "The separate read-only refresh job fetched model and pricing evidence. "
        "The generator implementation is reviewed in its source-code PR.\n"
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
        + f"\n<!-- free-model-review-sha256: {review_fingerprint(current)} -->\n"
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
