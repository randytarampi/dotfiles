from pathlib import Path

import pytest

from litellm_aliases import (
    canonical_allowlist_key,
    resolve_alias,
    resolve_canonical_identity,
)
from constants import get_litellm_proxy_mode
import litellm_config
from unittest.mock import patch


@pytest.mark.parametrize(
    "identity,mode,key,expected_model,expected_key",
    [
        ("openai/gpt-x", False, None, "openai/gpt-x", None),
        ("ollama-cloud/glm-x", False, Path("/key"), "ollama-cloud/glm-x", None),
        (
            "openai/gpt-x",
            True,
            Path("/private/client.key"),
            "litellm/openai/gpt-x",
            "{file:/private/client.key}",
        ),
        (
            "google/models/flash",
            True,
            "os.environ/LITELLM_PI_KEY",
            "litellm/google/models/flash",
            "os.environ/LITELLM_PI_KEY",
        ),
        (
            "ollama/model",
            True,
            "!cat '/private/pi.key'",
            "litellm/ollama/model",
            "!cat '/private/pi.key'",
        ),
    ],
)
def test_resolve_alias_identity_mode_and_key_shape(
    identity, mode, key, expected_model, expected_key
):
    result = (
        resolve_alias(
            identity, mode, client_key=key, gateway_url="http://127.0.0.1:4000/v1"
        )
        if mode
        else resolve_alias(identity, mode, client_key=key)
    )
    assert result["model"] == expected_model
    assert result["api_key"] == expected_key
    if mode:
        assert result["base_url"] == "http://127.0.0.1:4000/v1"
    else:
        assert result["base_url"] is None


def test_proxy_resolution_requires_explicit_gateway_and_key():
    with pytest.raises(ValueError, match="gateway_url"):
        resolve_alias("openai/gpt-x", True, client_key=Path("/key"))
    with pytest.raises(ValueError, match="client key"):
        resolve_alias("openai/gpt-x", True, gateway_url="http://127.0.0.1:4000/v1")


@pytest.mark.parametrize(
    "value,expected",
    [(None, False), ("0", False), ("false", False), ("1", True), ("true", True)],
)
def test_gate_resolution_is_explicit_and_defaults_to_direct(value, expected):
    env = {} if value is None else {"DOTFILES_USE_LITELLM_PROXY": value}
    assert get_litellm_proxy_mode(env) is expected


def test_canonical_identity_is_mode_invariant():
    identity = "ollama-cloud/glm-5.3-flash"
    direct = resolve_alias(identity, False)
    gateway = resolve_alias(
        identity,
        True,
        client_key="os.environ/LITELLM_PI_KEY",
        gateway_url="http://127.0.0.1:4000/v1",
    )
    assert direct["model"] == identity
    assert gateway["model"].removeprefix("litellm/") == identity


@pytest.mark.parametrize(
    "reference,mode,expected",
    [
        # F6: valid transport-wrapped identities normalize to canonical.
        ("litellm/ollama/some-model", True, "ollama/some-model"),
        (
            "litellm/google/models/gemini-3.8-flash",
            True,
            "google/models/gemini-3.8-flash",
        ),
        ("litellm/openai/gpt-x", True, "openai/gpt-x"),
        # Malformed shapes are rejected, never mis-resolved.
        ("litellm/ollama/", True, None),
        ("litellm//model", True, None),
        ("litellm/litellm/openai/gpt-x", True, None),
        # Transport refs are meaningless outside proxy mode.
        ("litellm/openai/gpt-x", False, None),
        # Canonical identities pass through direct mode unchanged.
        ("openai/gpt-x", False, "openai/gpt-x"),
    ],
)
def test_resolve_canonical_identity_shapes(reference, mode, expected):
    assert resolve_canonical_identity(reference, mode) == expected


def test_resolve_alias_rejects_empty_segments():
    with pytest.raises(ValueError, match="empty segments"):
        resolve_alias(
            "openai/",
            True,
            client_key="{file:/k}",
            gateway_url="http://127.0.0.1:4000/v1",
        )


@pytest.mark.parametrize(
    "model_id,expected",
    [
        ("models/gemini-3.8-flash", "gemini-3.8-flash"),
        ("gemini-3.8-flash", "gemini-3.8-flash"),
        ("inclusionai/ling-3.0-flash-sante:free", "inclusionai/ling-3.0-flash-sante"),
        ("inclusionai/ling-3.0-flash-sante", "inclusionai/ling-3.0-flash-sante"),
        ("inclusionai/ling-3.0-flash-fin-free", "inclusionai/ling-3.0-flash-fin-free"),
        ("nemotron-3.5-lightning-freetier", "nemotron-3.5-lightning-freetier"),
        (None, None),
        (123, 123),
    ],
)
def test_canonical_allowlist_key_strips_models_segment(model_id, expected):
    assert canonical_allowlist_key(model_id) == expected


def test_canonical_allowlist_key_collapses_upstream_free_rename():
    assert canonical_allowlist_key("upstream/new-id:free") == "upstream/new-id"


def test_raw_model_reference_keeps_endpoint_specific_wire_identity():
    from model_references import (
        Catalogue,
        EndpointIdentity,
        ModelReference,
        Outcome,
        compare_references,
    )

    direct_endpoint = EndpointIdentity("https://api.example/v1", "openai")
    gateway_endpoint = EndpointIdentity("https://gateway.example/v1", "litellm")
    wire = "upstream/new-id:free"
    direct = ModelReference(direct_endpoint, wire, "test", "model")
    gateway = ModelReference(gateway_endpoint, wire, "test", "model")
    assert direct.wire_id == gateway.wire_id == wire
    assert direct != gateway
    result = compare_references(
        [direct, gateway],
        {direct_endpoint: Catalogue(True, frozenset({wire}))},
    )
    outcomes = {row.reference.endpoint: row.outcome for row in result.results}
    assert outcomes == {
        direct_endpoint: Outcome.MATCH,
        gateway_endpoint: Outcome.UNKNOWN,
    }
    assert not result.complete


def test_safe_models_preserves_typed_records_and_chat_catalogue_filters_types():
    entries = [
        {"name": "mellum2:12b-a2.5b", "model_type": "llm"},
        {"name": "whisper-2", "model_type": "audio_stt"},
        {"name": "nomic-embed-text", "model_type": "embedding"},
        {"name": "markitdown", "model_type": "conversion"},
        {"name": "strands-decider:2b", "model_type": "decision"},
    ]
    with patch.object(litellm_config, "iter_engine_models", return_value=entries):
        assert litellm_config._safe_models("ollama") == entries
        with (
            patch.object(litellm_config, "active_engines", return_value=["ollama"]),
            patch.object(
                litellm_config,
                "get_ollama_local_base_url",
                return_value="http://127.0.0.1:11434",
            ),
            patch.object(litellm_config, "list_cloud_ollama_models", return_value=[]),
        ):
            published = litellm_config.compute_model_list(
                {"DOTFILES_LITELLM_CATALOGUE_SCOPE": "full"}
            )
    aliases = {entry["model_name"] for entry in published}
    assert "mellum2:12b-a2.5b" in aliases
    assert {"whisper-2", "nomic-embed-text"} <= aliases
    assert not {"markitdown", "strands-decider:2b"} & aliases


def test_chat_catalogue_excludes_untyped_specialized_discovery_records():
    # Real Ollama discovery shape: {name, size_gb, provider} only — no
    # model_type — so the typed filter cannot see these records. The
    # name-level guard must keep specialized models out of the chat
    # catalogue, including Oracle-reproduced shapes (both the bare name and
    # the provider-qualified form must be absent).
    entries = [
        {"name": "mellum2:12b-a2.5b", "size_gb": 8.0, "provider": "ollama"},
        {"name": "whisper-2", "size_gb": 3.0, "provider": "ollama"},
        {"name": "strands-decider:2b", "size_gb": 2.0, "provider": "ollama"},
    ]
    with (
        patch.object(litellm_config, "iter_engine_models", return_value=entries),
        patch.object(litellm_config, "active_engines", return_value=["ollama"]),
        patch.object(litellm_config, "list_cloud_ollama_models", return_value=[]),
        patch.object(
            litellm_config,
            "get_ollama_local_base_url",
            return_value="http://127.0.0.1:11434",
        ),
    ):
        published = litellm_config.compute_model_list(
            {"DOTFILES_LITELLM_CATALOGUE_SCOPE": "full"}
        )
    aliases = {entry["model_name"] for entry in published}
    assert aliases, "chat catalogue must not become empty under containment"
    assert not aliases & {
        "whisper-2",
        "ollama/whisper-2",
        "strands-decider:2b",
        "ollama/strands-decider:2b",
    }
    assert any("mellum2:12b-a2.5b" in alias for alias in aliases)
