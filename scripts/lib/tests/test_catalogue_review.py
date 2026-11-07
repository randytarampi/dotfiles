"""Regression checks for the multi-provider candidate review boundary."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from catalogue_review import (
    provider_coverage,
    registry_review,
    render_pr_body,
    review_fingerprint,
    require_fresher_observations,
)

ROOT = Path(__file__).resolve().parents[3]


def _inventory():
    return {
        "schema_version": 2,
        "status": "available",
        "source": "https://opencode.ai/inference/v1/models",
        "pricing_source": "https://models.dev/api.json",
        "fetched_at": "2026-11-06T20:00:00Z",
        "pricing_observed_at": "2026-11-06T20:00:00Z",
        "pricing_expires_at": "2026-11-07T20:00:00Z",
        "counts": {
            "catalogue": 3,
            "classified": 3,
            "free": 2,
            "unclassified": 0,
        },
        "models": [
            {"id": "known", "pricing": {"input": 0, "output": 0}},
            {"id": "new-model", "pricing": {"input": 0, "output": 0}},
        ],
        "review": {
            "new_candidates": ["new-model"],
            "already_curated": ["known"],
            "curated_not_listed": ["not-listed"],
            "curated_listed_not_zero": ["paid"],
        },
        "provider_coverage": provider_coverage(
            "https://opencode.ai/inference/v1/models"
        ),
    }


def test_coverage_does_not_pretend_other_providers_were_probed():
    coverage = provider_coverage("https://opencode.ai/inference/v1/models")
    assert coverage["opencode"]["status"] == "observed"
    for provider in (
        "openrouter",
        "google",
        "cohere",
        "poolside",
        "groq",
        "mistral",
        "cloudflare",
        "nvidia-nim",
    ):
        assert coverage[provider] == {"status": "not_assessed"}


def test_curated_delta_is_exact_and_never_promotes_new_ids(tmp_path):
    registry = tmp_path / "opencode-models.json"
    registry.write_text(
        json.dumps(
            {
                "models": {
                    "known": {"name": "known"},
                    "paid": {"name": "paid"},
                    "not-listed": {"name": "not-listed"},
                }
            }
        ),
        encoding="utf-8",
    )
    models = [
        {"id": "known", "pricing": {"input": 0, "output": 0}},
        {"id": "new-model", "pricing": {"input": 0, "output": 0}},
    ]
    expected = {
        "new_candidates": ["new-model"],
        "already_curated": ["known"],
        "curated_not_listed": ["not-listed"],
        "curated_listed_not_zero": ["paid"],
    }
    assert registry_review(models, ["known", "new-model", "paid"], registry) == expected
    assert "new-model" not in json.loads(registry.read_text())["models"]


@pytest.mark.parametrize(
    "bad_registry",
    [{"models": []}, {"models": {"bad": []}}, {"models": {"": {}}}],
)
def test_invalid_curated_registry_is_not_accepted(tmp_path, bad_registry):
    source = tmp_path / "invalid.json"
    source.write_text(json.dumps(bad_registry), encoding="utf-8")
    with pytest.raises(ValueError):
        registry_review([], [], source)


def test_generated_body_has_all_pr_template_sections_and_no_fake_ci_pass():
    current = _inventory()
    body = render_pr_body(current)
    for section in (
        "## Outcome and scope",
        "## Contributors and roles",
        "## Evidence",
        "## Review disposition",
        "## Risk and handoff",
    ):
        assert section in body
    assert "new-model" in body
    assert "not-listed" in body
    assert "mistral" in body
    assert "NOT RUN" in body
    assert "not assessed" in body.lower()
    assert "artefact" not in body.lower()
    assert "required CI: PASS" not in body


def test_review_digest_recovers_stale_pr_metadata_without_timestamp_churn():
    original = _inventory()
    fingerprint = review_fingerprint(original)
    body = render_pr_body(original)
    assert f"<!-- free-model-review-sha256: {fingerprint} -->" in body
    observed_later = json.loads(json.dumps(original))
    observed_later["fetched_at"] = "2026-11-07T04:00:00Z"
    observed_later["pricing_observed_at"] = "2026-11-07T04:00:00Z"
    observed_later["pricing_expires_at"] = "2026-11-08T04:00:00Z"
    assert review_fingerprint(observed_later) == fingerprint
    observed_later["models"][0]["pricing"]["output"] = 0.01
    assert review_fingerprint(observed_later) != fingerprint


def test_comparison_reads_legacy_bot_inventory_and_protects_markdown():
    current = _inventory()
    old = {
        "models": [
            {"id": "known", "pricing": {"input": 0, "output": 0}},
            {"id": "removed", "pricing": {"input": 0, "output": 0}},
        ]
    }
    current["models"][1]["id"] = "new|`<script>\n\u202e"
    current["review"]["new_candidates"] = ["new|`<script>\n\u202e"]
    body = render_pr_body(current, old)
    assert "removed" in body
    assert r"<code>new|&#96;&lt;script&gt;\u000a\u202e</code>" in body
    assert "<script>" not in body
    assert "`<script>" not in body


@pytest.mark.parametrize(
    "change",
    [
        {"status": "incomplete"},
        {"schema_version": 1},
        {"provider_coverage": {"opencode": {"status": "not_assessed"}}},
        {"source": "https://attacker.invalid/models"},
        {"pricing_source": "https://attacker.invalid/pricing"},
        {"counts": {"catalogue": 0, "classified": 0, "free": 2, "unclassified": 0}},
        {"counts": {"catalogue": True, "classified": 1, "free": 2, "unclassified": 0}},
        {
            "review": {
                "new_candidates": [],
                "already_curated": ["known"],
                "curated_not_listed": ["not-listed"],
                "curated_listed_not_zero": ["paid"],
            }
        },
        {
            "review": {
                "new_candidates": ["new-model", "new-model"],
                "already_curated": ["known"],
                "curated_not_listed": ["not-listed"],
                "curated_listed_not_zero": ["paid"],
            }
        },
    ],
)
def test_renderer_rejects_unqualified_inventory(change):
    current = _inventory()
    current.update(change)
    with pytest.raises(ValueError):
        render_pr_body(current)


def test_cli_renders_without_network_or_writing_source(tmp_path):
    current = _inventory()
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "review.md"
    input_path.write_text(json.dumps(current), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/lib/catalogue_review.py"),
            "--current",
            str(input_path),
            "--output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "new-model" in output_path.read_text(encoding="utf-8")
    assert json.loads(input_path.read_text()) == current


def test_pr_repair_does_not_invent_historical_model_deltas():
    current = _inventory()
    repaired = render_pr_body(current, current)
    assert "earlier distinct baseline cannot be recovered" in repaired
    assert "First known bot inventory" not in repaired
    assert "Compared with the preceding bot inventory" not in repaired
    previous = {
        "models": [
            {"id": "known", "pricing": {"input": 0, "output": 0}},
        ]
    }
    initial = render_pr_body(current, previous)
    assert "Compared with the preceding bot inventory" in initial
    assert "new-model" in initial
    assert "separate read-only refresh job" in initial


@pytest.mark.parametrize(
    "pricing",
    [
        {"input": 0, "output": 0.001},
        {"input": 0, "output": "nan"},
        {"input": 0, "output": float("inf")},
        {"input": 0, "output": False},
        {"input": 0, "output": 0, "cache_read": -1},
        {"input": 0, "output": 0, "cache_write": {}},
        {"input": 0},
    ],
)
def test_renderer_rejects_nonzero_or_malformed_candidate_prices(pricing):
    current = _inventory()
    current["models"][0]["pricing"] = pricing
    with pytest.raises(ValueError, match="invalid pricing evidence"):
        render_pr_body(current)


def test_renderer_rejects_more_free_models_than_listed():
    current = _inventory()
    current["counts"]["catalogue"] = 1
    current["counts"]["classified"] = 1
    with pytest.raises(ValueError, match="exceed the Console listing"):
        render_pr_body(current)


def test_prior_snapshot_observations_must_be_older():
    previous = _inventory()
    current = json.loads(json.dumps(previous))
    current["fetched_at"] = "2026-11-06T20:01:00+00:00"
    current["pricing_observed_at"] = "2026-11-06T20:01:00Z"
    require_fresher_observations(current, previous)

    # A shared pricing cache may have the same observation time; a newer
    # authenticated Console fetch with unchanged pricing remains valid.
    same_pricing_observation = json.loads(json.dumps(current))
    same_pricing_observation["pricing_observed_at"] = previous["pricing_observed_at"]
    require_fresher_observations(same_pricing_observation, previous)

    stale_fetch = json.loads(json.dumps(current))
    stale_fetch["fetched_at"] = previous["fetched_at"]
    with pytest.raises(ValueError, match="stale fetched_at"):
        require_fresher_observations(stale_fetch, previous)
    stale_pricing = json.loads(json.dumps(current))
    stale_pricing["pricing_observed_at"] = "2026-11-06T19:59:00Z"
    with pytest.raises(ValueError, match="stale pricing_observed_at"):
        require_fresher_observations(stale_pricing, previous)

    for malformed in ("not-a-date", "2026-11-06T21:00:00", None):
        bad = json.loads(json.dumps(current))
        bad["fetched_at"] = malformed
        with pytest.raises(ValueError, match="timestamp"):
            require_fresher_observations(bad, previous)


def test_freshness_cli_rejects_older_queued_result(tmp_path):
    previous = _inventory()
    current = json.loads(json.dumps(previous))
    current["fetched_at"] = "2026-11-06T19:00:00Z"
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before.write_text(json.dumps(previous), encoding="utf-8")
    after.write_text(json.dumps(current), encoding="utf-8")
    outcome = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/lib/catalogue_review.py"),
            "--current",
            str(after),
            "--previous",
            str(before),
            "--check-freshness",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert outcome.returncode != 0
    assert "stale fetched_at" in outcome.stderr


def test_renderer_rejects_false_multi_provider_observation():
    current = _inventory()
    current["provider_coverage"]["mistral"] = {
        "status": "observed",
        "source": "https://api.mistral.ai/v1/models",
    }
    with pytest.raises(ValueError, match="coverage"):
        render_pr_body(current)


def test_renderer_rejects_inconsistent_curated_categories():
    current = _inventory()
    current["review"]["curated_not_listed"] = ["known"]
    with pytest.raises(ValueError, match="overlapping"):
        render_pr_body(current)


def test_model_ids_are_not_truncated_or_interpreted_as_markdown():
    current = _inventory()
    precise = "model-" + "x" * 175
    current["models"][1]["id"] = precise
    current["review"]["new_candidates"] = [precise]
    assert f"<code>{precise}</code>" in render_pr_body(current)
    current["models"][1]["id"] = "x" * 257
    current["review"]["new_candidates"] = ["x" * 257]
    with pytest.raises(ValueError, match="too long"):
        render_pr_body(current)
