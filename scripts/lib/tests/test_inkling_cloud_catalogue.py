"""Offline contract for the optional paid Ollama Cloud Inkling model.

Live Ollama and LiteLLM inference must be qualified separately on the owner host.
"""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GENERATOR = ROOT / "scripts/generate-jetbrains-profiles.py"
SPEC = importlib.util.spec_from_file_location("inkling_junie_generator", GENERATOR)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

MODEL = "inkling-2-small"
GROUP = "ollama-cloud-inkling"
TWIN = f"litellm-{GROUP}"
ALIAS = f"ollama-cloud/{MODEL}"


def read_json(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_inkling_is_curated_and_opt_in_without_changing_existing_tiers():
    allowlist = read_json("configs/opencode/ollama-cloud-models.json")
    groups = read_json("configs/junie/model-groups.json")
    registry = read_json("configs/opencode/oh-my-opencode-slim.json")

    assert allowlist["models"][MODEL] == {"name": MODEL}
    assert groups["providers"]["ollama-cloud"]["baseUrl"] == "https://ollama.com/v1"
    assert groups["providers"]["ollama-cloud"]["apiKeyEnv"] == "OLLAMA_API_KEY"
    assert groups["groups"][GROUP] == {
        "provider": "ollama-cloud",
        "primaryModel": MODEL,
    }
    # Cataloguing a paid model must not opt it into any OMO-Slim tier,
    # including the dynamic-free and Zen-free presets.
    assert ALIAS not in json.dumps(registry)


def test_inkling_gateway_twin_requires_the_exact_qualified_alias():
    groups = read_json("configs/junie/model-groups.json")["groups"]
    derived = MODULE.derive_litellm_groups(groups)
    assert not MODULE.routed_group_violations(groups, derived)
    assert GROUP in MODULE.select_model_groups(groups, use_litellm=False)
    assert GROUP not in MODULE.select_model_groups(groups, use_litellm=True)
    expected = {"provider": "litellm", "primaryModel": ALIAS}
    assert derived[TWIN] == expected
    assert MODULE.select_model_groups(groups, use_litellm=True)[TWIN] == expected

    # Absent/incorrect gateway discovery must fail closed, not send the
    # request to a direct Ollama endpoint or silently choose a substitute.
    assert TWIN not in MODULE.filter_litellm_groups({TWIN: expected}, None)
    assert TWIN not in MODULE.filter_litellm_groups({TWIN: expected}, {MODEL})
    assert MODULE.filter_litellm_groups({TWIN: expected}, {ALIAS}) == {
        TWIN: expected
    }
