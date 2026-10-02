import importlib.util
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ollama_cloud_wire_ids_test", LIB / "ollama_cloud_wire_ids.py"
)
WIRE_IDS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WIRE_IDS)


@pytest.mark.parametrize(
    "model_id, installed, expected",
    [
        ("gemma4:31b", ["gemma4:31b-cloud"], "gemma4:31b-cloud"),
        ("gpt-oss:120b", ["gpt-oss:120b-cloud"], "gpt-oss:120b-cloud"),
        ("glm-5.3", ["glm-5.3:cloud"], "glm-5.3:cloud"),
        (
            "org/repo/model:tag",
            ["org/repo/model:tag-cloud"],
            "org/repo/model:tag-cloud",
        ),
        ("org/repo/model:cloud", ["org/repo/model:cloud"], "org/repo/model:cloud"),
        ("gpt-oss:20b", ["gpt-oss:20b-cloud"], "gpt-oss:20b-cloud"),
    ],
)
def test_resolves_exact_installed_suffix_without_mutating_inner_tags(
    model_id, installed, expected
):
    assert WIRE_IDS.installed_cloud_stub(model_id, installed) == expected


def test_missing_failed_or_ambiguous_daemon_inventory_fails_closed():
    assert WIRE_IDS.installed_cloud_stub("gemma4:31b", None) is None
    assert WIRE_IDS.installed_cloud_stub("gemma4:31b", []) is None
    assert (
        WIRE_IDS.installed_cloud_stub("glm-5.3", ["glm-5.3:cloud", "glm-5.3-cloud"])
        is None
    )
    assert WIRE_IDS.proxied_cloud_ref("ollama-cloud/gemma4:31b", []) == (
        "ollama-cloud/gemma4:31b"
    )


def test_opencode_rewrites_primary_fallback_and_council_but_preserves_unmatched():
    preset = {
        "orchestrator": {"model": "ollama-cloud/gemma4:31b"},
        "fallback": "ollama-cloud/glm-5.3",
        "council": {
            "alpha": {"model": "ollama-cloud/gpt-oss:120b"},
            "beta": {"model": "ollama-cloud/mistral-large-3:675b"},
            "gamma": {"model": "ollama-cloud/missing:tag"},
        },
    }
    installed = [
        "gemma4:31b-cloud",
        "glm-5.3:cloud",
        "gpt-oss:120b-cloud",
        "mistral-large-3:675b-cloud",
    ]
    rewritten = WIRE_IDS.rewrite_cloud_refs(preset, installed)
    assert rewritten["orchestrator"]["model"] == "ollama/gemma4:31b-cloud"
    assert rewritten["fallback"] == "ollama/glm-5.3:cloud"
    assert rewritten["council"]["alpha"]["model"] == "ollama/gpt-oss:120b-cloud"
    assert rewritten["council"]["beta"]["model"] == "ollama/mistral-large-3:675b-cloud"
    assert rewritten["council"]["gamma"]["model"] == "ollama-cloud/missing:tag"


def test_pi_provider_entries_are_only_exact_installed_stubs():
    curated = [
        "gemma4:31b",
        "gpt-oss:120b",
        "gpt-oss:20b",
        "mistral-large-3:675b",
        "nemotron-3-nano:30b",
        "glm-5.3",
    ]
    installed = [
        {"name": "gemma4:31b-cloud"},
        {"name": "gpt-oss:120b-cloud"},
        {"name": "gpt-oss:20b-cloud"},
        {"name": "mistral-large-3:675b-cloud"},
        {"name": "nemotron-3-nano:30b-cloud"},
        {"name": "glm-5.3:cloud"},
    ]
    assert WIRE_IDS.installed_cloud_stubs(curated, installed) == [
        item["name"] for item in installed
    ]
    assert WIRE_IDS.installed_cloud_stubs(curated, None) == []
