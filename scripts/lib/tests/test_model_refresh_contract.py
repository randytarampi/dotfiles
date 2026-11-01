"""Offline regression contracts for the November 2026 model refresh.

Run with PYTHONPATH=scripts/lib pytest scripts/lib/tests/test_model_refresh_contract.py -q.
Live endpoint and installed-client inference remain owner-only acceptance checks.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_terra_and_upstream_translations():
    slim = load("configs/opencode/oh-my-opencode-slim.json")
    p = slim["presets"]
    assert p["plus"]["orchestrator"]["model"] == "openai/gpt-6.1-terra"
    assert p["plus"]["oracle"]["model"] == "openai/gpt-6.1-sol"
    assert p["omo-slim-openai"]["orchestrator"]["variant"] == "medium"
    assert p["omo-slim-openai"]["fixer"]["model"] == "openai/gpt-6.1-sol"
    assert p["omo-slim-openai"]["fixer"]["variant"] == "low"
    assert p["omo-slim-thirty-dollars"]["oracle"]["model"] == "openai/gpt-6-astra"
    forty = p["omo-slim-fourty-dollars"]
    assert forty["orchestrator"]["model"] == "anthropic/claude-opus-5-5"
    assert forty["oracle"]["model"] == "openai/gpt-6-astra"
    assert forty["fixer"]["model"] == "openai/gpt-6.1-sol"
    assert set(p) == set(slim["_tiers"])
    for name in p:
        nested = slim["_tiers"][name]["council"]["presets"][name]
        assert p[name]["council"]["model"] == nested["council"]["model"]


def test_junie_candidates_and_allowlists():
    groups = load("configs/junie/model-groups.json")["groups"]
    assert groups["openai"]["primaryModel"] == "gpt-6.1-terra"
    assert groups["ollama-cloud-mimo"]["primaryModel"] == "mimo-v2.6-pro"
    assert groups["ollama-cloud-mimo"]["fasterModel"] == "mimo-v2.6-flash"
    assert groups["ollama-cloud-kimi"]["primaryModel"] == "kimi-k3"
    assert groups["ollama-cloud-kimi"]["fasterModel"] == "kimi-k2.7-code"
    for provider, models in {
        "openai": ("gpt-6.1-terra",),
        "ollama-cloud": ("mimo-v2.6-pro", "mimo-v2.6-flash"),
        "opencode": (
            "glm-5.3-flash-free",
            "deepseek-v4.1-flash-free",
            "nemotron-3.5-super-vl-free",
        ),
    }.items():
        assert set(models) <= set(
            load(f"configs/opencode/{provider}-models.json")["models"]
        )


def test_free_candidates_remain_fail_closed():
    slim = load("configs/opencode/oh-my-opencode-slim.json")
    assert (
        slim["presets"]["omo-slim-opencode-zen-free"]["observer"]["model"]
        == "opencode/nemotron-3.5-super-vl-free"
    )
    assert all(
        not chain
        for chain in slim["_tiers"]["omo-slim-opencode-zen-free"]["fallback"].values()
    )
