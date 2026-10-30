import json
from datetime import datetime, timedelta, timezone

import pytest

import free_route_manifest


def test_config_write_cannot_refresh_catalogue_evidence(tmp_path):
    import litellm_config

    entry = litellm_config._entry(
        "opencode/model-free",
        "openai/model-free",
        api_base="https://opencode.ai/zen/v1",
        upstream_host="opencode",
        upstream_model_id="opencode/model-free",
        source_kind="live-catalogue",
    )
    config = tmp_path / "config.yaml"
    assert litellm_config.write_config(config, entries=[entry])
    manifest = json.loads((tmp_path / "free-route-manifest.json").read_text())
    assert manifest["routes"][0]["evidence_state"] == "UNKNOWN"
    assert manifest["routes"][0]["observed_at"] is None


def test_manifest_projects_only_explicit_trusted_upstream_provenance():
    entry = {
        "model_name": "openai/gpt-6-luna",
        "litellm_params": {
            "model": "openai/gpt-6-luna",
            "api_base": "https://opencode.ai/zen/v1",
            "api_key": "SECRET-MUST-NOT-LEAK",
        },
        "_route_provenance": {
            "upstream_host": "opencode",
            "upstream_model_id": "openai/gpt-6-luna",
            "source_kind": "configured-provider",
        },
    }
    result = free_route_manifest.build_manifest([entry])
    route = result["routes"][0]
    assert route["upstream_host"] == "opencode"
    assert route["metadata_model_id"] == "openai/gpt-6-luna"
    assert route["classification"] == "UNKNOWN"
    assert "SECRET-MUST-NOT-LEAK" not in json.dumps(result)


def test_manifest_marks_missing_or_retained_evidence_unknown():
    result = free_route_manifest.build_manifest(
        [
            {
                "model_name": "cohere/model-free",
                "litellm_params": {"model": "cohere_chat/model-free"},
                "_route_provenance": {
                    "upstream_host": "cohere",
                    "upstream_model_id": "model-free",
                    "source_kind": "retained-ledger",
                    "observed_at": "2026-01-01T00:00:00Z",
                },
            },
            {
                "model_name": "lookalike/example-free",
                "litellm_params": {
                    "model": "openai/example-free",
                    "api_base": "https://openrouter.ai/api/v1",
                },
            },
        ]
    )
    retained, unknown = result["routes"]
    assert retained["classification"] == "UNKNOWN"
    assert retained["evidence_state"] == "UNKNOWN"
    assert retained["observed_at"] is None
    assert unknown["upstream_host"] == "unknown"
    assert unknown["classification"] == "UNKNOWN"


def test_manifest_sort_keeps_provenance_attached_and_requires_fresh_times():
    now = datetime.now(timezone.utc)
    entries = [
        {
            "model_name": "z/model-free",
            "litellm_params": {"model": "openai/z"},
            "_route_provenance": {
                "upstream_host": "openrouter",
                "upstream_model_id": "z-free",
                "source_kind": "live-catalogue",
            },
        },
        {
            "model_name": "a/model-free",
            "litellm_params": {"model": "openai/a"},
            "_route_provenance": {
                "upstream_host": "cohere",
                "upstream_model_id": "cohere/a-free",
                "source_kind": "live-catalogue",
            },
        },
    ]
    for entry in entries:
        entry["_route_provenance"].update(
            metadata_provider=entry["_route_provenance"]["upstream_host"],
            observed_at=now.isoformat(),
            expires_at=(now + timedelta(hours=1)).isoformat(),
        )
    manifest = free_route_manifest.build_manifest(entries, now=now)
    assert [
        (route["alias"], route["upstream_host"]) for route in manifest["routes"]
    ] == [("a/model-free", "cohere"), ("z/model-free", "openrouter")]
    assert all(route["evidence_state"] == "VERIFIED" for route in manifest["routes"])
    for entry in entries:
        entry["_route_provenance"].pop("observed_at")
        entry["_route_provenance"].pop("expires_at")
    assert (
        free_route_manifest.build_manifest(entries)["routes"][0]["evidence_state"]
        == "UNKNOWN"
    )
    stale = free_route_manifest.build_manifest(
        entries,
        observed_at=(now - timedelta(days=2)).isoformat(),
        expires_at=(now - timedelta(days=1)).isoformat(),
        now=now,
    )
    assert all(route["evidence_state"] == "UNKNOWN" for route in stale["routes"])
    assert free_route_manifest.binding_digest(
        entries
    ) == free_route_manifest.binding_digest(entries[::-1])


@pytest.mark.parametrize(
    "target", ["route-alias", "wire-model", "api-base", "upstream-model-id"]
)
def test_digest_changes_when_route_projection_changes(target):
    entry = {
        "model_name": "opencode/model-free",
        "litellm_params": {
            "model": "openai/model-free",
            "api_base": "https://opencode.ai/zen/v1",
        },
        "_route_provenance": {
            "upstream_host": "opencode",
            "upstream_model_id": "model-free",
            "source_kind": "configured-provider",
        },
    }
    original = free_route_manifest.binding_digest([entry])
    changed = json.loads(json.dumps(entry))
    if target == "route-alias":
        changed["model_name"] = "opencode/other-free"
    elif target == "wire-model":
        changed["litellm_params"]["model"] = "openai/other-free"
    elif target == "api-base":
        changed["litellm_params"]["api_base"] = "https://other.example/v1"
    else:
        changed["_route_provenance"]["upstream_model_id"] = "other-free"
    assert free_route_manifest.binding_digest([changed]) != original


def test_write_is_private_and_refuses_symlink_target(tmp_path):
    path = tmp_path / "manifest.json"
    free_route_manifest.write_manifest(path, {"routes": []})
    assert path.stat().st_mode & 0o777 == 0o600
    target = tmp_path / "target"
    target.write_text("untouched")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError):
        free_route_manifest.write_manifest(link, {"routes": []})
    assert target.read_text() == "untouched"


def test_write_refuses_symlink_parent_and_cleans_temp_on_failure(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    parent_link = tmp_path / "linked-parent"
    parent_link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        free_route_manifest.write_manifest(parent_link / "manifest.json", {})
    assert list(outside.iterdir()) == []

    target = tmp_path / "failure.json"

    def fail_replace(*_args):
        raise OSError("replace failure")

    monkeypatch.setattr(free_route_manifest.os, "replace", fail_replace)
    with pytest.raises(OSError):
        free_route_manifest.write_manifest(target, {})
    assert not target.exists()
    assert not list(tmp_path.glob(".free-route-manifest.*"))


def test_config_binding_uses_only_config_visible_route_projection():
    first = {
        "model_name": "same",
        "litellm_params": {"model": "openai/m", "api_base": "https://example.test/v1"},
        "_route_provenance": {"upstream_host": "one", "upstream_model_id": "one/m"},
    }
    second = {
        **first,
        "_route_provenance": {"upstream_host": "two", "upstream_model_id": "two/m"},
    }
    assert free_route_manifest.config_binding_digest(
        [first]
    ) == free_route_manifest.config_binding_digest([second])
