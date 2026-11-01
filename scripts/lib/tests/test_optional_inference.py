"""Optional inference boundaries: account binding, strict-free scope and aliases."""

import json
import os
import re
import subprocess
import sys
import textwrap
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
    assert 'default: "free"' in workflow
    assert "steps.review_model.outputs.model" in workflow
    assert "--pool=" in workflow
    assert "CLOUDFLARE_ACCOUNT_ID: ${{ vars.CLOUDFLARE_ACCOUNT_ID }}" in workflow
    assert "HAS_MISTRAL_KEY" in workflow
    assert "HAS_GROQ_KEY" in workflow
    assert "TRUSTED_DOTFILES" in workflow
    assert "permission" in workflow and '"edit": "deny"' in workflow
    assert "review-no-push/pre-push" in workflow
    assert "anomalyco/opencode/github@" in workflow
    assert "NVIDIA NIM review requires explicit development-use approval" in workflow


def _run_review_model_setup(tmp_path, **overrides):
    """Execute the exact trusted workflow's embedded selector without secrets."""
    workflow = (ROOT / ".github/workflows/agentic-review.yml").read_text(
        encoding="utf-8"
    )
    marker = "      - name: Configure read-only OpenCode plan agent"
    fragment = workflow.split(marker, 1)[1].split(
        "      - name: Prepare native OpenCode Git authentication handoff", 1
    )[0]
    match = re.search(
        r"^          python3 - <<'PY'\n(?P<code>.*?)^          PY$",
        fragment,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    script = textwrap.dedent(match.group("code"))
    root = tmp_path
    root.joinpath("configs/opencode/ci").mkdir(parents=True)
    root.joinpath("configs/opencode/ci/opencode.json").write_text(
        (ROOT / "configs/opencode/ci/opencode.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    envfile = tmp_path / "github-env"
    outfile = tmp_path / "github-output"
    env = {
        **os.environ,
        "TRUSTED_DOTFILES": str(root),
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_ENV": str(envfile),
        "GITHUB_OUTPUT": str(outfile),
        "REVIEW_INFERENCE_PROVIDER": "free",
        "REVIEW_ALLOW_NVIDIA": "false",
        "HAS_MISTRAL_KEY": "false",
        "HAS_CLOUDFLARE_KEY": "false",
        "HAS_GROQ_KEY": "false",
        "HAS_NVIDIA_NIM_KEY": "false",
        "REVIEW_FREE_MISTRAL_APPROVED": "",
        "REVIEW_FREE_CLOUDFLARE_APPROVED": "",
        "REVIEW_FREE_GROQ_APPROVED": "",
        "CLOUDFLARE_ACCOUNT_ID": "",
        **overrides,
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        text=True,
        capture_output=True,
        check=False,
        env=env,
        cwd=tmp_path,
    )
    if result.returncode:
        return result, {}, {}
    config = json.loads((tmp_path / "opencode-review-config.json").read_text())
    emitted = dict(
        line.split("=", 1) for line in outfile.read_text(encoding="utf-8").splitlines()
    )
    return result, config, emitted


def test_review_free_defaults_to_existing_open_code_without_approved_routes(tmp_path):
    result, config, output = _run_review_model_setup(
        tmp_path,
        HAS_MISTRAL_KEY="true",
        HAS_CLOUDFLARE_KEY="true",
        CLOUDFLARE_ACCOUNT_ID="a" * 32,
    )
    assert result.returncode == 0, result.stderr
    assert config["model"] == "opencode/big-pickle"
    assert output["selected_provider"] == "opencode"


def test_review_free_uses_approved_mistral_primary_without_secret_literal(tmp_path):
    result, config, output = _run_review_model_setup(
        tmp_path,
        HAS_MISTRAL_KEY="true",
        REVIEW_FREE_MISTRAL_APPROVED="true",
    )
    assert result.returncode == 0, result.stderr
    assert config["model"] == "mistral/mistral-small-latest"
    assert output["selected_provider"] == "mistral"
    assert config["provider"]["mistral"]["options"]["apiKey"] == (
        "{env:MISTRAL_API_KEY}"
    )


def test_review_free_uses_account_bound_cloudflare_when_approved(tmp_path):
    account = "b" * 32
    result, config, output = _run_review_model_setup(
        tmp_path,
        HAS_CLOUDFLARE_KEY="true",
        CLOUDFLARE_ACCOUNT_ID=account,
        REVIEW_FREE_CLOUDFLARE_APPROVED="true",
    )
    assert result.returncode == 0, result.stderr
    assert output["selected_provider"] == "cloudflare"
    assert config["model"].startswith("cloudflare/")
    assert config["provider"]["cloudflare"]["options"]["baseURL"].endswith(
        f"/accounts/{account}/ai/v1"
    )


def test_review_nim_is_rejected_without_explicit_approval(tmp_path):
    result, _, _ = _run_review_model_setup(
        tmp_path,
        REVIEW_INFERENCE_PROVIDER="nvidia-nim",
        HAS_NVIDIA_NIM_KEY="true",
    )
    assert result.returncode != 0
    assert "development-use approval" in result.stderr


def test_review_cloudflare_rejects_malformed_account(tmp_path):
    result, _, _ = _run_review_model_setup(
        tmp_path,
        REVIEW_INFERENCE_PROVIDER="cloudflare",
        HAS_CLOUDFLARE_KEY="true",
        CLOUDFLARE_ACCOUNT_ID="not-valid",
    )
    assert result.returncode != 0
    assert "Invalid Cloudflare account ID" in result.stderr


def test_review_free_native_router_has_independent_ordered_deployments(tmp_path):
    result, config, output = _run_review_model_setup(
        tmp_path,
        HAS_MISTRAL_KEY="true",
        REVIEW_FREE_MISTRAL_APPROVED="true",
        HAS_GROQ_KEY="true",
        REVIEW_FREE_GROQ_APPROVED="true",
        HAS_NVIDIA_NIM_KEY="true",
        HAS_OPENCODE_KEY="true",
    )
    assert result.returncode == 0, result.stderr
    assert output["selected_provider"] == "review-free"
    assert config["model"] == "review-free/review-free"
    assert config["provider"]["review-free"]["options"]["baseURL"] == (
        "http://127.0.0.1:4907/v1"
    )
    router_config = json.loads(
        (tmp_path / "review-free-litellm.json").read_text(encoding="utf-8")
    )
    deployments = router_config["model_list"]
    assert [row["litellm_params"]["model"] for row in deployments] == [
        "mistral/mistral-small-latest",
        "groq/openai/gpt-oss-120b",
        "openai/big-pickle",
    ]
    assert [row["litellm_params"]["order"] for row in deployments] == [1, 2, 3]
    assert all(row["model_name"] == "review-free" for row in deployments)
    assert all(
        row["litellm_params"]["api_key"].startswith("os.environ/")
        for row in deployments
    )
    assert router_config["router_settings"]["num_retries"] == 1
    assert router_config["general_settings"]["master_key"] == (
        "os.environ/REVIEW_LITELLM_KEY"
    )
    assert not any("nvidia" in str(row) for row in deployments)


def test_review_free_router_includes_approved_cloudflare_with_account_url(tmp_path):
    account = "c" * 32
    result, config, output = _run_review_model_setup(
        tmp_path,
        HAS_MISTRAL_KEY="true",
        REVIEW_FREE_MISTRAL_APPROVED="true",
        HAS_CLOUDFLARE_KEY="true",
        REVIEW_FREE_CLOUDFLARE_APPROVED="true",
        CLOUDFLARE_ACCOUNT_ID=account,
    )
    assert result.returncode == 0, result.stderr
    assert output["selected_provider"] == "review-free"
    assert config["model"] == "review-free/review-free"
    rows = json.loads(
        (tmp_path / "review-free-litellm.json").read_text(encoding="utf-8")
    )["model_list"]
    assert len(rows) == 2
    assert rows[1]["litellm_params"]["model"].startswith("cloudflare/")
    assert rows[1]["litellm_params"]["api_base"].endswith(
        f"/accounts/{account}/ai/v1"
    )


def test_review_free_invalid_cloudflare_account_fails_closed(tmp_path):
    result, _, _ = _run_review_model_setup(
        tmp_path,
        HAS_CLOUDFLARE_KEY="true",
        REVIEW_FREE_CLOUDFLARE_APPROVED="true",
        CLOUDFLARE_ACCOUNT_ID="invalid",
    )
    assert result.returncode != 0
    assert "Invalid Cloudflare account ID" in result.stderr


def test_review_workflow_starts_local_router_only_for_qualified_free_pool():
    workflow = (ROOT / ".github/workflows/agentic-review.yml").read_text(
        encoding="utf-8"
    )
    assert "steps.review_model.outputs.selected_provider == 'review-free'" in workflow
    assert "--host 127.0.0.1 --port 4907" in workflow
    assert "litellm[proxy]==1.102.1" in workflow
    assert "RUNNER_TRACKING_ID" in workflow
    assert "Review with official OpenCode GitHub App" in workflow
    assert "Stop runner-local LiteLLM" in workflow
