import copy
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from free_preset_runtime import (
    DynamicFreeRegistryError,
    _consumer_key,
    _safe_owned_file,
    prepare_dynamic_free_registry,
)
from free_gateway_evidence import validate_free_gateway_evidence
from free_route_manifest import build_manifest
from free_route_manifest import config_binding_digest
from free_preset_runtime import load_gateway_free_evidence

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


def test_gateway_consumer_credentials_are_isolated():
    env = {
        "LITELLM_OPENCODE_KEY": "open-key",
        "LITELLM_PI_KEY": "pi-key",
        "LITELLM_JUNIE_KEY": "junie-key",
    }
    assert _consumer_key(env, "pi") == "pi-key"
    assert _consumer_key(env, "junie") == "junie-key"
    assert _consumer_key(env, "opencode") == "open-key"
    with pytest.raises(DynamicFreeRegistryError):
        _consumer_key({"LITELLM_OPENCODE_KEY": "open-key"}, "pi")
    with pytest.raises(DynamicFreeRegistryError):
        _consumer_key(env, "other")


def test_gateway_evidence_rejects_symlink_ancestor(tmp_path):
    target = tmp_path / "target"
    target.write_text("synthetic")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(DynamicFreeRegistryError):
        _safe_owned_file(link / "child")


def test_gateway_evidence_requires_current_config_binding(monkeypatch, tmp_path):
    root = tmp_path / "litellm"
    clients = root / "clients"
    clients.mkdir(parents=True)
    alias = "zen"
    expected_routes = [
        {
            "model_name": alias,
            "litellm_params": {
                "model": "opencode/big-pickle-free",
                "api_base": "https://opencode.ai/zen/v1",
            },
        }
    ]
    (root / "config.yaml").write_text(
        "model_list:\n"
        "  - model_name: zen\n"
        "    litellm_params:\n"
        "      model: opencode/changed-free\n"
        "      api_base: https://opencode.ai/zen/v1\n"
        "      api_key: synthetic-never-reported\n",
        encoding="utf-8",
    )
    binding = config_binding_digest(expected_routes)
    (root / "free-route-manifest.json").write_text(
        json.dumps({"config_binding_sha256": binding}), encoding="utf-8"
    )
    (root / "free-route-config.json").write_text(
        json.dumps({"config_binding_sha256": binding, "routes": expected_routes}),
        encoding="utf-8",
    )
    key_path = clients / "opencode.key"
    key_path.write_text("synthetic-client-key\n", encoding="utf-8")
    os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
    monkeypatch.setattr(
        "model_catalogues.get_catalogue",
        lambda *args, **kwargs: pytest.fail(
            "stale config must refuse before catalogue call"
        ),
    )

    with pytest.raises(DynamicFreeRegistryError, match="current config"):
        load_gateway_free_evidence({"LITELLM_ROOT": str(root)})

    (root / "config.yaml").write_text(
        "model_list:\n"
        "  - model_name: zen\n"
        "    litellm_params:\n"
        "      model: opencode/big-pickle-free\n"
        "      api_base: https://opencode.ai/zen/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "model_catalogues.get_catalogue",
        lambda *args, **kwargs: {"data": [{"id": "zen"}]},
    )
    evidence = load_gateway_free_evidence({"LITELLM_ROOT": str(root)})
    assert evidence["expected_config_routes"] == expected_routes


def test_snapshot_to_materializer_preserves_registry_and_qualifies_routes():
    path = (
        Path(__file__).resolve().parents[3]
        / "configs/opencode/oh-my-opencode-slim.json"
    )
    registry = json.loads(path.read_text())
    original = copy.deepcopy(registry)
    snapshot = {
        "models": {
            "opencode": {
                "models": {
                    "big-pickle": {
                        "tool_call": True,
                        "reasoning": True,
                        "modalities": {"input": ["text", "image"], "output": ["text"]},
                    }
                }
            },
        },
        "source": "synthetic",
        "observed_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
    }
    calls = []

    def fetch(provider, key, base):
        calls.append((provider, base))
        return ["big-pickle"]

    transformed, audit = prepare_dynamic_free_registry(
        registry,
        ["free", "omo-slim-opencode-zen-free"],
        {"OPENCODE_API_KEY": "synthetic"},
        metadata_fetch=lambda: snapshot,
        route_fetch=fetch,
        clock=lambda: NOW,
    )
    assert calls == [("opencode", "https://opencode.ai/zen/v1")]
    for preset in ("free", "omo-slim-opencode-zen-free"):
        assert (
            transformed["presets"][preset]["observer"]["model"] == "opencode/big-pickle"
        )
        assert audit["presets"][preset]["eligible_count"] == 1
    assert registry == original


def test_gateway_unknown_refuses_without_direct_fallback(monkeypatch, tmp_path):
    def forbidden():
        pytest.fail("gateway refusal must precede direct discovery")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("LITELLM_PORT", raising=False)
    with pytest.raises(DynamicFreeRegistryError, match="gateway"):
        prepare_dynamic_free_registry(
            {},
            ["free"],
            {"DOTFILES_USE_LITELLM_PROXY": "1"},
            metadata_fetch=forbidden,
            clock=lambda: NOW,
        )


def test_gateway_evidence_flows_through_real_validator_and_materializer(monkeypatch):
    monkeypatch.setattr(
        "free_preset_runtime.validate_free_gateway_evidence",
        validate_free_gateway_evidence,
    )
    path = (
        Path(__file__).resolve().parents[3]
        / "configs/opencode/oh-my-opencode-slim.json"
    )
    registry = json.loads(path.read_text())
    original = copy.deepcopy(registry)
    entries = [
        {
            "model_name": "zen",
            "litellm_params": {
                "model": "opencode/big-pickle-free",
                "api_base": "https://opencode.ai/zen/v1",
            },
            "_route_provenance": {
                "upstream_host": "opencode",
                "upstream_model_id": "big-pickle-free",
                "metadata_model_id": "big-pickle-free",
                "metadata_provider": "opencode",
                "source_kind": "live-catalogue",
                "observed_at": (NOW - timedelta(minutes=1)).isoformat(),
                "expires_at": (NOW + timedelta(hours=1)).isoformat(),
            },
        }
    ]
    manifest = build_manifest(entries, now=NOW)
    models_dev = {
        "source": "models.dev",
        "observed_at": (NOW - timedelta(minutes=1)).isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "models": {
            "opencode": {
                "models": {
                    "big-pickle-free": {
                        "reasoning": True,
                        "tool_call": True,
                        "modalities": {"input": ["text", "image"], "output": ["text"]},
                    }
                }
            }
        },
    }
    bundle = {
        "manifest": manifest,
        "expected_config_routes": entries,
        "gateway_models": {
            "source": "gateway /v1/models",
            "observed_at": (NOW - timedelta(minutes=1)).isoformat(),
            "expires_at": (NOW + timedelta(hours=1)).isoformat(),
            "models": ["zen"],
        },
        "trusted_endpoints": {"https://opencode.ai/zen/v1": "opencode"},
        "trusted_native": {},
    }
    transformed, audit = prepare_dynamic_free_registry(
        registry,
        ["omo-slim-opencode-zen-free"],
        {"DOTFILES_USE_LITELLM_PROXY": "1"},
        metadata_fetch=lambda: models_dev,
        clock=lambda: NOW,
        gateway_evidence=bundle,
    )
    assert (
        transformed["presets"]["omo-slim-opencode-zen-free"]["observer"]["model"]
        == "zen"
    )
    assert audit["presets"]["omo-slim-opencode-zen-free"]["eligible_count"] == 1
    assert registry == original
