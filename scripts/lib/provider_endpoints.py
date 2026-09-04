"""Shared provider endpoint metadata for generated agent configurations."""

import json
from pathlib import Path

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
}


def provider_models(provider):
    """Return sorted model IDs from a provider's checked-in allowlist."""
    root = Path(__file__).resolve().parents[2]
    path = root / PROVIDER_ENDPOINTS[provider]["allowlist"]
    with path.open(encoding="utf-8") as file:
        models = json.load(file).get("models", {})
    return sorted(models)
