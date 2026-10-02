"""Focused offline tests for provider-exact static model parity."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "verify-slim-invariants.py"
SPEC = importlib.util.spec_from_file_location("verify_slim_invariants", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


def run(groups, catalogues):
    return VERIFY._offline_model_parity_violations(
        junie={
            "providers": {
                provider: {}
                for provider in (
                    "google",
                    "openai",
                    "openrouter",
                    "ollama-cloud",
                    "github-copilot",
                    "opencode",
                    "litellm",
                    "meridian",
                    "ollama",
                    "omlx",
                )
            },
            "groups": groups,
        },
        allowlists=catalogues,
        codex_source='DEFAULT_OLLAMA_CLOUD_MODEL = "cloud-default"',
    )


def test_allowlist_names_are_not_model_ids(tmp_path, monkeypatch):
    path = tmp_path / "openai.json"
    path.write_text('{"models": {"real-id": {"name": "Pretty name"}}}')
    monkeypatch.setattr(VERIFY, "MODEL_ALLOWLIST_PATHS", {"openai": path})
    assert VERIFY._model_allowlists() == {"openai": {"real-id"}}
    assert VERIFY._model_allowlist_violations(
        {"presets": {"x": {"task": {"model": "openai/Pretty name"}}}}
    )
    assert not VERIFY._model_allowlist_violations(
        {"presets": {"x": {"task": {"model": "openai/real-id"}}}}
    )


def test_direct_and_litellm_google_pair_is_valid():
    groups = {
        "google-gemini-flash": {
            "provider": "google",
            "primaryModel": "gemini-3.8-flash",
            "fasterModel": "gemini-3.5-flash-lite",
            "fasterProvider": "google",
        },
        "litellm-google-gemini-flash": {
            "provider": "litellm",
            "primaryModel": "google/models/gemini-3.8-flash",
            "fasterModel": "google/models/gemini-3.5-flash-lite",
        },
    }
    assert (
        run(
            groups,
            {
                "google": {"gemini-3.8-flash", "gemini-3.5-flash-lite"},
                "ollama-cloud": {"cloud-default"},
            },
        )
        == []
    )


def test_partial_or_misqualified_pair_fails():
    groups = {
        "google-gemini-flash": {
            "provider": "google",
            "primaryModel": "gemini-3.8-flash",
            "fasterModel": "gemini-3.5-flash-lite",
            "fasterProvider": "google",
        },
        "litellm-google-gemini-flash": {
            "provider": "litellm",
            "primaryModel": "google/models/gemini-3.8-flash",
            "fasterModel": "google/models/gemini-3.6-flash-lite",
        },
        "openrouter-x": {"provider": "openrouter", "primaryModel": "same/leaf"},
    }
    errors = run(
        groups,
        {
            "google": {"gemini-3.8-flash", "gemini-3.5-flash-lite"},
            "openrouter": set(),
            "ollama-cloud": {"cloud-default"},
        },
    )
    assert any("does not match direct group" in error for error in errors)
    assert any("same/leaf" in error for error in errors)


def test_unknown_provider_fails_closed_even_when_name_matches_catalogue():
    groups = {
        "misspelled-google": {
            "provider": "googlle",
            "primaryModel": "Pretty name",
        }
    }
    errors = run(
        groups,
        {"google": {"Pretty name"}, "ollama-cloud": {"cloud-default"}},
    )
    assert any("unknown provider 'googlle'" in error for error in errors)


def test_unknown_provider_declared_in_junie_still_fails_closed():
    errors = VERIFY._offline_model_parity_violations(
        junie={
            "providers": {"googlle": {}},
            "groups": {
                "mistyped": {"provider": "googlle", "primaryModel": "gemini-3.8-flash"}
            },
        },
        allowlists={"google": {"gemini-3.8-flash"}, "ollama-cloud": {"cloud-default"}},
        codex_source='DEFAULT_OLLAMA_CLOUD_MODEL = "cloud-default"',
    )
    assert any("unknown provider 'googlle'" in error for error in errors)


def test_unknown_faster_provider_fails_closed():
    groups = {
        "group": {
            "provider": "google",
            "primaryModel": "gemini-3.8-flash",
            "fasterProvider": "googlle",
            "fasterModel": "gemini-3.5-flash-lite",
        }
    }
    errors = run(
        groups,
        {
            "google": {"gemini-3.8-flash", "gemini-3.5-flash-lite"},
            "ollama-cloud": {"cloud-default"},
        },
    )
    assert any("unknown provider 'googlle'" in error for error in errors)


def test_litellm_proxy_group_rejects_direct_faster_provider():
    groups = {
        "google-gemini-flash": {
            "provider": "google",
            "primaryModel": "gemini-3.8-flash",
            "fasterModel": "gemini-3.5-flash-lite",
            "fasterProvider": "google",
        },
        "litellm-google-gemini-flash": {
            "provider": "litellm",
            "primaryModel": "google/models/gemini-3.8-flash",
            "fasterModel": "google/models/gemini-3.5-flash-lite",
            "fasterProvider": "google",
        },
    }
    errors = run(
        groups,
        {
            "google": {"gemini-3.8-flash", "gemini-3.5-flash-lite"},
            "ollama-cloud": {"cloud-default"},
        },
    )
    assert any(
        "fasterProvider must be 'litellm' or absent" in error for error in errors
    )
