import importlib.util
from pathlib import Path
import sys

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
import litellm_config

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "verify-local-ai-smoke.py"
SPEC = importlib.util.spec_from_file_location("verify_local_ai_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


def test_target_alias_comes_from_generator_canonical_local_entry(monkeypatch):
    monkeypatch.setattr(
        VERIFY, "list_local_ollama_models", lambda: [{"name": "fixture-model"}]
    )
    monkeypatch.setattr(
        VERIFY,
        "compute_model_list",
        lambda environ=None: [{"model_name": "ollama/fixture-model"}],
    )

    assert VERIFY.target_alias() == "ollama/fixture-model"


def test_target_alias_fails_closed_without_local_model(monkeypatch):
    monkeypatch.setattr(VERIFY, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(VERIFY, "compute_model_list", lambda environ=None: [])

    try:
        VERIFY.target_alias()
    except RuntimeError as error:
        assert "no local Ollama model" in str(error)
    else:
        raise AssertionError("missing local model must fail closed")


def test_generator_emits_canonical_ollama_alias(monkeypatch):
    monkeypatch.setattr(
        VERIFY, "list_local_ollama_models", lambda: [{"name": "fixture-model"}]
    )
    monkeypatch.setattr(
        VERIFY,
        "compute_model_list",
        lambda environ=None: compute_model_list_for_fixture(monkeypatch),
    )

    assert VERIFY.target_alias() == "ollama/fixture-model"


def test_ollama_requirement_policy_distinguishes_not_applicable_from_failure():
    """An oMLX-only host is not broken unless its contract requires Ollama."""
    assert (
        VERIFY.ollama_requirement_outcome(["omlx"], required=False) == "NOT_APPLICABLE"
    )
    assert VERIFY.ollama_requirement_outcome(["ollama"], required=True) == "FAILURE"


def compute_model_list_for_fixture(monkeypatch):
    monkeypatch.setattr(litellm_config, "active_engines", lambda: ["ollama"])
    monkeypatch.setattr(
        litellm_config,
        "iter_engine_models",
        lambda *_args, **_kwargs: [{"name": "fixture-model"}],
    )
    monkeypatch.setattr(
        litellm_config,
        "local_endpoint_for",
        lambda *_args, **_kwargs: ("http://127.0.0.1:11434/v1", None),
    )
    monkeypatch.setattr(litellm_config, "list_cloud_ollama_models", lambda: [])
    monkeypatch.setattr(litellm_config, "_registry_model_refs", lambda: set())
    monkeypatch.setattr(
        litellm_config, "_modality_entries", lambda *_args, **_kwargs: []
    )
    return litellm_config.compute_model_list(environ={})
