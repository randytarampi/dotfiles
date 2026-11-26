"""V1 -> V2 model metadata conversion at the OpenCode provider boundary.

The conversion is what keeps authored role variants resolvable: OpenCode V2
builds model variants from ``capabilities.reasoning.effort`` and the model's
``variants`` array, while the V1 ``reasoning_options`` list declares nothing.
When the V1 field survives, every generated model silently falls back to the
provider package default (``low``/``medium``/``high``) and an authored ``max``
or ``xhigh`` role variant raises ``VariantUnavailableError`` at resolution.
"""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "configure_opencode_native", ROOT / "scripts/configure-opencode.py"
)
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)


def _ollama_cloud_entry():
    """A catalogue entry shaped like the live LiteLLM provider model."""
    return {
        "id": "ollama-cloud/deepseek-v4.1-flash",
        "name": "deepseek-v4.1-flash",
        "reasoning": True,
        "reasoning_options": [
            {"type": "toggle"},
            {"type": "effort", "values": ["low", "high", "max"]},
        ],
        "capabilities": {"input": ["text", "image"], "output": ["text"]},
    }


def test_reasoning_options_become_v2_capability_and_v1_keys_are_removed():
    native = configure._native_model_entry(_ollama_cloud_entry())

    assert "reasoning" not in native
    assert "reasoning_options" not in native
    assert native["capabilities"]["reasoning"] == {
        "supported": True,
        "toggle": True,
        "effort": ["low", "high", "max"],
    }
    assert native["capabilities"]["input"] == ["text", "image"]
    assert native["modelID"] == "ollama-cloud/deepseek-v4.1-flash"


def test_effort_without_an_explicit_supported_flag_infers_support():
    native = configure._native_model_entry(
        {"reasoning_options": [{"type": "effort", "values": ["low", "high"]}]}
    )

    assert native["capabilities"]["reasoning"] == {
        "supported": True,
        "effort": ["low", "high"],
    }


def test_budget_tokens_become_a_budget_object():
    native = configure._native_model_entry(
        {
            "reasoning": True,
            "reasoning_options": [{"type": "budget_tokens", "min": 1024, "max": 8192}],
        }
    )

    assert native["capabilities"]["reasoning"] == {
        "supported": True,
        "budget": {"min": 1024, "max": 8192},
    }


def test_non_reasoning_model_declares_supported_false_only():
    native = configure._native_model_entry({"reasoning": False})

    assert native["capabilities"]["reasoning"] == {"supported": False}


def test_malformed_reasoning_options_add_no_capability():
    for options in (
        "invalid",
        [{"type": "effort", "values": 123}],
        [{"type": "effort", "values": []}],
        [{"type": "unknown"}],
        ["not-a-dict"],
    ):
        native = configure._native_model_entry({"reasoning_options": options})
        assert "reasoning_options" not in native
        assert "reasoning" not in (native.get("capabilities") or {})


def test_other_v1_conversions_still_apply():
    native = configure._native_model_entry(
        {
            "id": "m",
            "modalities": {"input": ["text"], "output": ["text"]},
            "tool_call": True,
            "status": "deprecated",
            "cost": {"input": 1, "cache_read": 0.1, "cache_write": 0.2},
            "variants": {"fast": {"reasoningEffort": "low"}},
        }
    )

    assert native["capabilities"]["tools"] is True
    assert native["capabilities"]["input"] == ["text"]
    assert native["disabled"] is True
    assert native["cost"]["cache"] == {"read": 0.1, "write": 0.2}
    assert native["variants"] == [
        {"id": "fast", "settings": {"reasoningEffort": "low"}}
    ]


def test_conversion_is_idempotent():
    once = configure._native_model_entry(_ollama_cloud_entry())
    twice = configure._native_model_entry(once)

    assert twice == once


def test_preset_max_variant_survives_into_declared_v2_effort():
    """The orchestration role keeps `max` only because the model declares it."""
    providers = {
        "litellm": {
            "models": {"ollama-cloud/deepseek-v4.1-flash": _ollama_cloud_entry()}
        }
    }
    slim = {
        "presets": {
            "pro": {
                "orchestrator": {
                    "model": "litellm/ollama-cloud/deepseek-v4.1-flash",
                    "variant": "max",
                }
            }
        }
    }

    configure.clamp_slim_variants(slim, providers)
    assert slim["presets"]["pro"]["orchestrator"]["variant"] == "max"

    configured = configure._native_model_entry(
        providers["litellm"]["models"]["ollama-cloud/deepseek-v4.1-flash"]
    )
    rendered = json.dumps(configured)
    assert '"max"' in rendered
    assert "reasoning_options" not in rendered


def test_undocumented_max_variant_is_clamped_to_a_declared_effort():
    providers = {
        "litellm": {
            "models": {
                "m": {
                    "reasoning_options": [{"type": "effort", "values": ["low", "high"]}]
                }
            }
        }
    }
    slim = {
        "presets": {"pro": {"orchestrator": {"model": "litellm/m", "variant": "max"}}}
    }

    configure.clamp_slim_variants(slim, providers)

    assert slim["presets"]["pro"]["orchestrator"]["variant"] == "high"
