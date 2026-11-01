"""Optional inference boundaries: account binding, strict-free scope and aliases."""

import json
from pathlib import Path

import pytest

import litellm_config
from provider_endpoints import (
    OPTIONAL_INFERENCE_PROVIDERS,
    provider_base_url,
    provider_models,
    selected_optional_providers,
)

ROOT = Path(__file__).resolve().parents[3]


def test_optional_providers_absent_without_opt_in():
    assert selected_optional_providers({}) == {}
    for provider in OPTIONAL_INFERENCE_PROVIDERS:
        assert provider_models(provider)


def test_optional_providers_ignore_missing_credentials_without_poisoning_others():
    selected = selected_optional_providers(
        {
            "DOTFILES_OPTIONAL_INFERENCE_PROVIDERS": "groq,mistral",
            "GROQ_API_KEY": "synthetic",
        }
    )
    assert selected == {"groq": "https://api.groq.com/openai/v1"}


@pytest.mark.parametrize(
    "selector",
    ("unknown", "groq,groq", "groq,", ",mistral"),
)
def test_optional_provider_gate_rejects_ambiguous_selectors(selector):
    with pytest.raises(ValueError):
        selected_optional_providers({"DOTFILES_OPTIONAL_INFERENCE_PROVIDERS": selector})


def test_cloudflare_endpoint_is_account_bound_and_constant_origin():
    account = "a" * 32
    env = {
        "DOTFILES_OPTIONAL_INFERENCE_PROVIDERS": "cloudflare",
        "CLOUDFLARE_API_TOKEN": "synthetic",
        "CLOUDFLARE_ACCOUNT_ID": account,
    }
    assert selected_optional_providers(env) == {
        "cloudflare": (
            "https://api.cloudflare.com/client/v4/accounts/" f"{account}/ai/v1"
        )
    }
    assert provider_base_url("cloudflare", env).startswith(
        "https://api.cloudflare.com/"
    )


@pytest.mark.parametrize("account", ("invalid", "a" * 31, "a" * 33, "a" * 32 + "/"))
def test_cloudflare_account_path_injection_is_rejected(account):
    with pytest.raises(ValueError):
        selected_optional_providers(
            {
                "DOTFILES_OPTIONAL_INFERENCE_PROVIDERS": "cloudflare",
                "CLOUDFLARE_API_TOKEN": "synthetic",
                "CLOUDFLARE_ACCOUNT_ID": account,
            }
        )


def test_full_scope_produces_qualified_aliases_without_key_disclosure(monkeypatch):
    monkeypatch.setattr(litellm_config, "active_engines", lambda: [])
    env = {
        "DOTFILES_OPTIONAL_INFERENCE_PROVIDERS": "groq,mistral,cloudflare,nvidia-nim",
        "DOTFILES_LITELLM_CATALOGUE_SCOPE": "full",
        "GROQ_API_KEY": "synthetic-groq",
        "MISTRAL_API_KEY": "synthetic-mistral",
        "CLOUDFLARE_API_TOKEN": "synthetic-cloudflare",
        "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
        "NVIDIA_NIM_API_KEY": "synthetic-nvidia",
    }
    aliases = {
        row["model_name"]: row["litellm_params"]
        for row in litellm_config.compute_model_list(env)
    }
    for provider in OPTIONAL_INFERENCE_PROVIDERS:
        for model in provider_models(provider):
            alias = f"{provider}/{model}"
            assert alias in aliases
            params = aliases[alias]
            assert params["api_key"] == (
                f"os.environ/{litellm_config.PROVIDER_ENDPOINTS[provider]['apiKeyEnv']}"
            )
    assert (
        aliases["nvidia-nim/openai/gpt-oss-20b"]["model"]
        == "nvidia_nim/openai/gpt-oss-20b"
    )
    assert (
        aliases["cloudflare/@cf/meta/llama-3.1-8b-instruct-fp8"]["model"]
        == "cloudflare/@cf/meta/llama-3.1-8b-instruct-fp8"
    )
    assert "synthetic-" not in json.dumps(aliases)


def test_free_scope_does_not_advertise_unqualified_optional_models(monkeypatch):
    monkeypatch.setattr(litellm_config, "active_engines", lambda: [])
    entries = litellm_config.compute_model_list(
        {
            "DOTFILES_OPTIONAL_INFERENCE_PROVIDERS": "groq",
            "DOTFILES_LITELLM_CATALOGUE_SCOPE": "free",
            "GROQ_API_KEY": "synthetic",
        }
    )
    assert not any(row["model_name"].startswith("groq/") for row in entries)


def test_review_action_keeps_trusted_model_selection_and_no_push_guards():
    workflow = (ROOT / ".github/workflows/agentic-review.yml").read_text(
        encoding="utf-8"
    )
    assert 'default: "opencode"' in workflow
    assert "steps.review_model.outputs.model" in workflow
    assert "TRUSTED_DOTFILES" in workflow
    assert "permission" in workflow and '"edit": "deny"' in workflow
    assert "review-no-push/pre-push" in workflow
    assert "anomalyco/opencode/github@" in workflow
    assert "NVIDIA NIM review requires explicit development-use approval" in workflow
