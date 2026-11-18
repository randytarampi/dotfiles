"""Offline regression contracts for the November 2026 model refresh.

Run with PYTHONPATH=scripts/lib pytest scripts/lib/tests/test_model_refresh_contract.py -q.
Live endpoint and installed-client inference remain owner-only acceptance checks.
"""

import json
from math import isclose
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_paid_cloud_orchestration_promotes_deepseek_without_changing_specialists():
    slim = load("configs/opencode/oh-my-opencode-slim.json")
    candidate = "ollama-cloud/deepseek-v4.1-flash"
    allowlist = load("configs/opencode/ollama-cloud-models.json")["models"]
    assert "deepseek-v4.1-flash" in allowlist
    for tier in ("pro", "pro-plus"):
        orchestrator = slim["presets"][tier]["orchestrator"]
        assert orchestrator["model"] == candidate
        assert orchestrator["variant"] == "max"
        assert candidate not in slim["_tiers"][tier]["fallback"]["orchestrator"]
        council = slim["council"]["presets"][tier]
        nested_council = slim["_tiers"][tier]["council"]["presets"][tier]
        assert council["alpha"]["model"] == candidate
        assert nested_council["alpha"]["model"] == candidate
    assert slim["presets"]["pro"]["designer"]["model"] == "ollama-cloud/glm-5.3-flash"
    assert slim["presets"]["pro-plus"]["designer"]["model"] == "openai/gpt-6-luna"
    assert slim["_tiers"]["pro"]["fallback"]["orchestrator"] == ["ollama-cloud/glm-5.3"]
    assert slim["_tiers"]["pro-plus"]["fallback"]["orchestrator"] == [
        "openai/gpt-6.2-sol"
    ]


def test_terra_and_upstream_translations():
    slim = load("configs/opencode/oh-my-opencode-slim.json")
    p = slim["presets"]
    assert p["plus"]["orchestrator"]["model"] == "openai/gpt-6.1-terra"
    assert p["plus"]["orchestrator"]["variant"] == "high"
    assert p["plus-anthropic"]["orchestrator"]["variant"] == "high"
    assert p["plus"]["oracle"]["model"] == "openai/gpt-6.2-sol"
    assert p["omo-slim-openai"]["orchestrator"]["variant"] == "medium"
    assert p["omo-slim-openai"]["fixer"]["model"] == "openai/gpt-6.2-sol"
    assert p["omo-slim-openai"]["fixer"]["variant"] == "low"
    assert p["omo-slim-thirty-dollars"]["oracle"]["model"] == "openai/gpt-6.2-astra"
    forty = p["omo-slim-forty-dollars"]
    assert forty["orchestrator"]["model"] == "anthropic/claude-opus-5-5"
    assert forty["oracle"]["model"] == "openai/gpt-6.2-astra"
    assert forty["fixer"]["model"] == "openai/gpt-6.2-sol"
    assert set(p) == set(slim["_tiers"])
    for name in p:
        nested = slim["_tiers"][name]["council"]["presets"][name]
        assert p[name]["council"]["model"] == nested["council"]["model"]


def test_junie_candidates_and_allowlists():
    groups = load("configs/junie/model-groups.json")["groups"]
    assert groups["openai"]["primaryModel"] == "gpt-6.1-terra"
    assert groups["ollama-cloud-mimo"]["primaryModel"] == "mimo-v2.6-pro"
    assert groups["ollama-cloud-mimo"]["fasterModel"] == "mimo-v2.6-flash"
    assert groups["ollama-cloud-kimi"]["primaryModel"] == "kimi-k3.1-pro"
    assert groups["ollama-cloud-kimi"]["fasterModel"] == "kimi-k2.7-code"
    for provider, models in {
        "openai": ("gpt-6.1-terra",),
        "ollama-cloud": ("mimo-v2.6-pro", "mimo-v2.6-flash", "kimi-k3.1-pro"),
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


def test_kimi_pro_replaces_only_paid_kimi_seats():
    slim = load("configs/opencode/oh-my-opencode-slim.json")
    pro_model = "ollama-cloud/kimi-k3.1-pro"
    assert slim["presets"]["pro"]["oracle"]["model"] == pro_model
    assert slim["council"]["presets"]["pro"]["gamma"]["model"] == pro_model
    assert (
        slim["_tiers"]["pro"]["council"]["presets"]["pro"]["gamma"]["model"]
        == pro_model
    )
    assert "kimi-k3" in load("configs/opencode/ollama-cloud-models.json")["models"]
    # Paid Ollama Cloud models must never enter the strict-free configurations.
    for name in ("free", "omo-slim-opencode-zen-free"):
        assert "kimi-k3.1-pro" not in json.dumps(slim["presets"][name])
        assert "kimi-k3.1-pro" not in json.dumps(slim["_tiers"][name])


def test_gpt62_one_for_one_registry_migration_and_pricing():
    paths = (
        "configs/opencode/openai-models.json",
        "configs/opencode/oh-my-opencode-slim.json",
        "configs/junie/model-groups.json",
    )
    for path in paths:
        content = (ROOT / path).read_text(encoding="utf-8")
        assert "gpt-6.1-sol" not in content
        assert "gpt-6-astra" not in content
        assert "gpt-6.2-sol" in content
        assert "gpt-6.2-astra" in content
    rates = load("configs/litellm/model-rates.json")["models"]
    assert isclose(rates["gpt-6.2-sol"]["input_cost_per_token"], 2e-6)
    assert isclose(rates["gpt-6.2-sol"]["output_cost_per_token"], 1e-5)
    assert isclose(rates["gpt-6.2-astra"]["input_cost_per_token"], 8e-6)
    assert isclose(rates["gpt-6.2-astra"]["cache_read_input_token_cost"], 4e-7)
    assert isclose(rates["gpt-6.2-astra"]["output_cost_per_token"], 4e-5)


def test_webui_and_litellm_upgrade_pins_are_coherent():
    webui = (ROOT / ".chezmoiscripts/run_onchange_30-openwebui.sh.tmpl").read_text()
    litellm = (ROOT / ".chezmoiscripts/run_onchange_15-litellm.sh.tmpl").read_text()
    caddy = (ROOT / ".chezmoiscripts/run_onchange_24-install-caddy.sh.tmpl").read_text()
    template = (ROOT / "dot_dotfiles/shell/.env.example").read_text()
    assert "DOTFILES_OPENWEBUI_VERSION:-0.13.4" in webui
    assert "DOTFILES_OPENWEBUI_TERMINAL_VERSION:-0.17.20" in webui
    assert "DOTFILES_OPENWEBUI_COMPUTER_VERSION:-0.9.21" in webui
    assert "DOTFILES_LITELLM_VERSION:-1.108.11" in litellm
    assert 'DOTFILES_OPENWEBUI_VERSION" | default "0.13.4"' in caddy
    for version in ("0.13.4", "0.17.20", "0.9.21", "1.108.11"):
        assert version in template
    assert "ENABLE_TOOL_SERVERS" in template
    assert "ENABLE_TOOL_PERMISSIONS_AUTO" in template
