from pathlib import Path

import pytest

from litellm_aliases import (
    canonical_allowlist_key,
    resolve_alias,
    resolve_canonical_identity,
)
from constants import get_litellm_proxy_mode


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
