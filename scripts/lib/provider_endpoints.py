"""Shared provider endpoint metadata for generated agent configurations."""

import json
import re
from pathlib import Path

POOLSIDE_ROUTED_MODEL_ID = "poolside/laguna-s-2.1"
POOLSIDE_ROUTED_CONTEXT = 262112
POOLSIDE_ROUTED_OUTPUT = 32768
POOLSIDE_ROUTED_MODEL_INFO = {
    "max_input_tokens": 262144,
    "max_output_tokens": POOLSIDE_ROUTED_OUTPUT,
}


def conservative_limit(existing, ceiling):
    """Cap a positive numeric configured limit without widening it."""
    if (
        isinstance(existing, (int, float))
        and not isinstance(existing, bool)
        and existing > 0
    ):
        return min(existing, ceiling)
    return ceiling


PROVIDER_ENDPOINTS = {
    "google": {
        "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api": "openai-completions",
        "apiKeyEnv": "GEMINI_API_KEY",
        "allowlist": "configs/opencode/google-models.json",
    },
    "openrouter": {
        "baseUrl": "https://openrouter.ai/api/v1",
        "api": "openai-completions",
        "apiKeyEnv": "OPENROUTER_API_KEY",
        "allowlist": "configs/opencode/openrouter-models.json",
    },
    "opencode": {
        "baseUrl": "https://opencode.ai/zen/v1",
        "api": "openai-completions",
        "apiKeyEnv": "OPENCODE_API_KEY",
        "allowlist": "configs/opencode/opencode-models.json",
    },
    "cerebras": {
        "baseUrl": "https://api.cerebras.ai/v1",
        "apiKeyEnv": "CEREBRAS_API_KEY",
    },
    "cohere": {
        "baseUrl": "https://api.cohere.ai",
        "apiKeyEnv": "COHERE_API_KEY",
    },
    "poolside": {
        "baseUrl": "https://inference.poolside.ai/v1",
        "apiKeyEnv": "POOLSIDE_API_KEY",
    },
    "huggingface": {
        "baseUrl": "https://router.huggingface.co",
        "apiKeyEnv": "HF_TOKEN",
    },
    "groq": {
        "baseUrl": "https://api.groq.com/openai/v1",
        "api": "openai-completions",
        "apiKeyEnv": "GROQ_API_KEY",
        "allowlist": "configs/opencode/groq-models.json",
    },
    "mistral": {
        "baseUrl": "https://api.mistral.ai/v1",
        "api": "openai-completions",
        "apiKeyEnv": "MISTRAL_API_KEY",
        "allowlist": "configs/opencode/mistral-models.json",
    },
    "cloudflare": {
        "baseUrl": None,  # Account-specific; see provider_base_url().
        "api": "openai-completions",
        "apiKeyEnv": "CLOUDFLARE_API_TOKEN",
        "allowlist": "configs/opencode/cloudflare-models.json",
    },
    "nvidia-nim": {
        "baseUrl": "https://integrate.api.nvidia.com/v1",
        "api": "openai-completions",
        "apiKeyEnv": "NVIDIA_NIM_API_KEY",
        "allowlist": "configs/opencode/nvidia-nim-models.json",
    },
}


def provider_models(provider):
    """Return sorted model IDs from a provider's checked-in allowlist."""
    root = Path(__file__).resolve().parents[2]
    path = root / PROVIDER_ENDPOINTS[provider]["allowlist"]
    with path.open(encoding="utf-8") as file:
        models = json.load(file).get("models", {})
    return sorted(models)

OPTIONAL_INFERENCE_PROVIDERS = ("groq", "mistral", "cloudflare", "nvidia-nim")


def provider_base_url(provider, environ=None):
    """Resolve only trusted provider origins; never interpolate an arbitrary host."""
    import os

    spec = PROVIDER_ENDPOINTS[provider]
    if provider != "cloudflare":
        return spec["baseUrl"]
    env = os.environ if environ is None else environ
    account = str(env.get("CLOUDFLARE_ACCOUNT_ID", "")).strip()
    if not account:
        return None
    if not re.fullmatch(r"[a-fA-F0-9]{32}", account):
        raise ValueError("CLOUDFLARE_ACCOUNT_ID must be a 32-character hex ID")
    return (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{account}/ai/v1"
    )
