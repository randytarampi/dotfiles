import hashlib
import os
import json
import io
import shlex
import subprocess
import stat
import urllib.error
from collections import Counter
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import litellm_config
import model_catalogues
from constants import OMLX_DEFAULT_PORT
from provider_endpoints import POOLSIDE_ROUTED_MODEL_INFO


@pytest.mark.parametrize("engine_order", [("omlx", "ollama"), ("ollama", "omlx")])
def test_modality_cross_engine_collision_emits_both_qualified_aliases(engine_order):
    models = {
        "omlx": [{"name": "shared-model", "model_type": "embedding"}],
        "ollama": [{"name": "shared-model", "model_type": "reranker"}],
    }
    with (
        patch.object(litellm_config, "active_engines", return_value=engine_order),
        patch.object(
            litellm_config, "_safe_models", side_effect=lambda engine: models[engine]
        ),
        patch.object(
            litellm_config,
            "local_endpoint_for",
            side_effect=lambda engine, _kind: (f"http://{engine}/v1", None),
        ),
    ):
        rows = litellm_config._modality_entries({})

    qualified = {row["model_name"] for row in rows if row["_alias_kind"] == "qualified"}
    assert qualified == {"ollama/shared-model", "omlx/shared-model"}
    assert not [row for row in rows if row["model_name"] == "shared-model"]


def test_single_engine_modality_alias_keeps_bare_and_namespaced_twins():
    with (
        patch.object(litellm_config, "active_engines", return_value=["omlx"]),
        patch.object(
            litellm_config,
            "_safe_models",
            return_value=[{"name": "only-model", "model_type": "embedding"}],
        ),
        patch.object(
            litellm_config,
            "local_endpoint_for",
            return_value=("http://omlx/v1", None),
        ),
    ):
        rows = litellm_config._modality_entries({})
    assert {row["model_name"] for row in rows} == {"only-model", "omlx/only-model"}


LIVE_CATALOGUE = litellm_config._live_catalogue

CONFIGURE_SPEC = spec_from_file_location(
    "configure_litellm", Path(__file__).resolve().parents[2] / "configure-litellm.py"
)
CONFIGURE = module_from_spec(CONFIGURE_SPEC)
CONFIGURE_SPEC.loader.exec_module(CONFIGURE)


def test_rates_snapshot_refresh_failure_degrades_without_failing_deploy():
    """A read timeout in the rates snapshot keeps the deploy alive (deploy evidence)."""

    def refresh_stub(dry_run=False):
        raise TimeoutError("The read operation timed out")

    with patch.object(CONFIGURE, "_sync_runtime_rates_snapshot", refresh_stub):
        assert CONFIGURE._refresh_rates_snapshot(dry_run=False) == "unchanged"
        assert CONFIGURE._refresh_rates_snapshot(dry_run=True) == "unchanged"


PI_SPEC = spec_from_file_location(
    "configure_pi", Path(__file__).resolve().parents[2] / "configure-pi.py"
)
PI = module_from_spec(PI_SPEC)
PI_SPEC.loader.exec_module(PI)
MIGRATE_SPEC = spec_from_file_location(
    "migrate_env_gates", Path(__file__).resolve().parents[2] / "migrate-env-gates.py"
)
MIGRATE = module_from_spec(MIGRATE_SPEC)
MIGRATE_SPEC.loader.exec_module(MIGRATE)


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith(
            (
                "DOTFILES_",
                "OPENAI_API_KEY",
                "OPENAI_",
                "ANTHROPIC_",
                "GEMINI_",
                "OPENROUTER_",
                "OPENCODE_",
                "OLLAMA_",
                "MERIDIAN_",
                "OMLX_",
                "COHERE_",
                "HF_TOKEN",
                "CEREBRAS_",
            )
        ):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(litellm_config, "active_engines", lambda: [])
    monkeypatch.setattr(litellm_config, "_live_catalogue", lambda *args: [])
    # Models.dev rates stay hermetic: no network, no index leakage between
    # tests — every rate lookup falls back to the installed cost map seams.
    monkeypatch.setattr(litellm_config, "_MODELS_DEV_FETCH", lambda **kwargs: {})
    litellm_config._MODELS_DEV_RATES_LOADED = False
    litellm_config._MODELS_DEV_RATES = {}
    # Keep the bundled-cost-map loader hermetic: machines with a real LiteLLM
    # venv must not leak live rates into catalogue-emission assertions.
    monkeypatch.setattr(litellm_config, "_BUILTIN_COST_MAP_LOADED", True)
    monkeypatch.setattr(litellm_config, "BUILTIN_COST_MAP", {})


def test_cloud_keys_and_meridian_are_conditional(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("MERIDIAN_API_KEY", "meridian")
    entries = litellm_config.compute_model_list()
    aliases = {entry["model_name"] for entry in entries}
    assert "openai/default" not in aliases
    assert all(entry["model_name"].startswith("meridian/") for entry in entries)
    assert any(
        "openai/gpt-6-luna: UNKNOWN" in note
        for note in litellm_config.last_generation_notes
    )
    assert "anthropic/default" not in aliases
    monkeypatch.setattr(litellm_config, "is_meridian_configured", lambda: True)
    assert "meridian/claude-sonnet-5-5" in {
        entry["model_name"] for entry in litellm_config.compute_model_list()
    }


@pytest.mark.parametrize(
    "provider,filename,model",
    [
        ("github_copilot", "api-key.json", "github-copilot/gpt-4o"),
        ("chatgpt", "auth.json", "chatgpt/gpt-5.2"),
    ],
)
def test_oauth_cache_controls_generation_without_api_key(
    tmp_path, monkeypatch, provider, filename, model
):
    cache_dir = tmp_path / provider
    cache_dir.mkdir()
    credentials = {"expires_at": 4102444800, "token": "stub"}
    if provider == "chatgpt":
        credentials.update(access_token="access", refresh_token="refresh")
    (cache_dir / filename).write_text(json.dumps(credentials))
    env = {"HOME": str(tmp_path)}
    env["DOTFILES_LITELLM_OAUTH_PROVIDERS"] = "1"
    env[
        (
            "GITHUB_COPILOT_TOKEN_DIR"
            if provider == "github_copilot"
            else "CHATGPT_TOKEN_DIR"
        )
    ] = str(cache_dir)
    aliases = {
        item["model_name"]: item for item in litellm_config.compute_model_list(env)
    }
    assert model in aliases
    assert "api_key" not in aliases[model]["litellm_params"]
    if provider == "github_copilot":
        assert aliases[model]["litellm_params"]["model"] == "github_copilot/gpt-4o"
    absent = litellm_config.compute_model_list(
        {
            "HOME": str(tmp_path / "absent"),
            "DOTFILES_LITELLM_OAUTH_PROVIDERS": "1",
        }
    )
    assert not any(item["model_name"].startswith(provider + "/") for item in absent)
    assert any(
        note.startswith(f"{provider}: OAuth token cache absent/expired")
        for note in litellm_config.last_generation_notes
    )


@pytest.mark.parametrize(
    "provider,filename,valid_cache",
    [
        (
            "github_copilot",
            "api-key.json",
            {"token": "copilot", "expires_at": 4102444800},
        ),
        (
            "chatgpt",
            "auth.json",
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_at": 4102444800,
            },
        ),
    ],
)
def test_oauth_generation_requires_explicit_gate_and_valid_cache(
    tmp_path, provider, filename, valid_cache
):
    cache_dir = tmp_path / provider
    cache_dir.mkdir()
    cache_path = cache_dir / filename
    cache_path.write_text(json.dumps(valid_cache))
    cache_dir_env = (
        "GITHUB_COPILOT_TOKEN_DIR"
        if provider == "github_copilot"
        else "CHATGPT_TOKEN_DIR"
    )
    base_env = {"HOME": str(tmp_path), cache_dir_env: str(cache_dir)}
    off_entries = litellm_config.compute_model_list(base_env)
    assert not any(
        entry["model_name"].startswith(provider + "/") for entry in off_entries
    )
    assert any(
        f"OAuth provider {provider} withheld: DOTFILES_LITELLM_OAUTH_PROVIDERS=0"
        in note
        for note in litellm_config.last_generation_notes
    )
    on_env = {**base_env, "DOTFILES_LITELLM_OAUTH_PROVIDERS": "1"}
    on_entries = litellm_config.compute_model_list(on_env)
    model_prefix = "github-copilot/" if provider == "github_copilot" else provider + "/"
    assert any(entry["model_name"].startswith(model_prefix) for entry in on_entries)
    cache_path.write_text(json.dumps({**valid_cache, "expires_at": 1}))
    expired_entries = litellm_config.compute_model_list(on_env)
    assert not any(
        entry["model_name"].startswith(model_prefix) for entry in expired_entries
    )
    assert any(
        note.startswith(f"{provider}: OAuth token cache absent/expired")
        for note in litellm_config.last_generation_notes
    )


@pytest.mark.parametrize("catalogue", [["m1", "m2"], []])
def test_live_catalogue_entries_and_fallback(monkeypatch, catalogue):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-" + "or-" + "stub")
    monkeypatch.setattr(litellm_config, "_live_catalogue", lambda *args: catalogue)
    # These tests validate full-catalogue emission shapes; opt out of the default free scope.
    monkeypatch.setenv("DOTFILES_LITELLM_CATALOGUE_SCOPE", "full")

    entries = litellm_config.compute_model_list()
    aliases = {entry["model_name"]: entry for entry in entries}

    if catalogue:
        assert {"openrouter/m1", "openrouter/m2"} <= aliases.keys()
        assert aliases["openrouter/m1"]["litellm_params"]["api_key"] == (
            "os.environ/OPENROUTER_API_KEY"
        )
        assert "api_base" in aliases["openrouter/m1"]["litellm_params"]
    else:
        assert "openrouter/default" not in aliases
        assert not aliases
        assert any(
            "openrouter: UNKNOWN" in note
            for note in litellm_config.last_generation_notes
        )


def test_live_catalogue_failure_is_recorded_and_other_providers_continue(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-" + "or-" + "stub")
    monkeypatch.setenv("GOOGLE_API_KEY", "google-stub")
    monkeypatch.setenv("GEMINI_API_KEY", "google-stub")
    # The stub catalogue id is not a curated registry reference; full scope
    # keeps the emission-shape assertions focused on failure handling.
    monkeypatch.setenv("DOTFILES_LITELLM_CATALOGUE_SCOPE", "full")

    def catalogue(provider, *args):
        if provider == "openrouter":
            raise litellm_config.LiveCatalogueError("stub outage")
        return ["available-model"]

    monkeypatch.setattr(litellm_config, "_live_catalogue", catalogue)
    entries = litellm_config.compute_model_list()
    assert "google/models/available-model" in {item["model_name"] for item in entries}
    assert any(
        "openrouter: UNKNOWN" in note for note in litellm_config.last_generation_notes
    )


def test_unknown_catalogue_retains_previous_cloud_entries_but_not_new_models(
    monkeypatch,
):
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    monkeypatch.setenv("DOTFILES_LITELLM_CATALOGUE_SCOPE", "full")
    previous = [
        {
            "model_name": "ollama-cloud/glm-5.3-flash",
            "litellm_params": {
                "model": "openai/glm-5.3-flash",
                "api_base": "https://ollama.com/v1",
                "api_key": "os.environ/OLLAMA_API_KEY",
            },
        }
    ]
    monkeypatch.setattr(
        litellm_config,
        "_live_catalogue",
        lambda *args: (_ for _ in ()).throw(
            litellm_config.LiveCatalogueError("offline")
        ),
    )
    entries = litellm_config.compute_model_list(previous_entries=previous)
    aliases = {entry["model_name"] for entry in entries}
    assert "ollama-cloud/glm-5.3-flash" in aliases
    assert "ollama-cloud/new-model" not in aliases


@pytest.mark.parametrize("catalogue_unavailable", [False, True])
def test_cohere_retains_previous_alias_when_live_model_is_unknown(
    monkeypatch, catalogue_unavailable
):
    previous = {
        "model_name": "cohere_chat/command-existing",
        "litellm_params": {
            "model": "cohere_chat/command-existing",
            "api_key": "os.environ/COHERE_API_KEY",
        },
    }
    monkeypatch.setattr(
        litellm_config,
        "_registry_model_refs",
        lambda: {"cohere/command-existing", "cohere/command-new"},
    )

    def catalogue(*args):
        if catalogue_unavailable:
            raise litellm_config.LiveCatalogueError("offline")
        return ["command-live"]

    monkeypatch.setattr(litellm_config, "_live_catalogue", catalogue)
    entries = litellm_config.compute_model_list(
        {"COHERE_API_KEY": "synthetic-key", "DOTFILES_LITELLM_CATALOGUE_SCOPE": "full"},
        previous_entries=[previous],
    )
    cohere = {
        entry["model_name"]: entry
        for entry in entries
        if entry["model_name"].startswith("cohere_chat/")
    }
    expected = {"cohere_chat/command-existing"}
    if not catalogue_unavailable:
        expected.add("cohere_chat/command-live")
    assert set(cohere) == expected
    assert (
        cohere["cohere_chat/command-existing"]["litellm_params"]
        == previous["litellm_params"]
    )


@pytest.mark.parametrize(
    "provider,key,old_model,new_model,retained_aliases",
    [
        (
            "cohere",
            "COHERE_API_KEY",
            "command-old",
            "command-new",
            {"cohere_chat/command-old", "cohere/command-old"},
        ),
        (
            "poolside",
            "POOLSIDE_API_KEY",
            "poolside/laguna-old",
            "poolside/laguna-new",
            {"poolside/laguna-old"},
        ),
    ],
)
def test_provider_catalogue_outage_retains_ledger_family_until_recovery(
    monkeypatch, provider, key, old_model, new_model, retained_aliases
):
    monkeypatch.setattr(litellm_config, "active_engines", lambda: [])
    monkeypatch.setattr(
        litellm_config,
        "_registry_model_refs",
        lambda: {
            f"{provider}/{old_model.removeprefix(provider + '/')}",
            f"{provider}/{new_model.removeprefix(provider + '/')}",
        },
    )
    monkeypatch.setenv("DOTFILES_LITELLM_CATALOGUE_SCOPE", "full")
    environ = {key: "synthetic-key", "DOTFILES_LITELLM_CATALOGUE_SCOPE": "full"}
    catalogue = {"models": [old_model]}
    monkeypatch.setattr(
        litellm_config,
        "_live_catalogue",
        lambda *_args: catalogue["models"],
    )

    healthy = litellm_config.compute_model_list(environ)
    healthy_aliases = {item["model_name"] for item in healthy}
    assert retained_aliases <= healthy_aliases

    def outage(*_args):
        raise litellm_config.LiveCatalogueError("simulated outage")

    monkeypatch.setattr(litellm_config, "_live_catalogue", outage)
    retained = litellm_config.compute_model_list(environ, previous_entries=healthy)
    retained_names = {item["model_name"] for item in retained}
    assert retained_aliases <= retained_names
    assert any(
        f"{provider}: UNKNOWN; ledger family retained" in note
        for note in litellm_config.last_generation_notes
    )

    catalogue["models"] = [new_model]
    monkeypatch.setattr(
        litellm_config,
        "_live_catalogue",
        lambda *_args: catalogue["models"],
    )
    recovered = litellm_config.compute_model_list(environ, previous_entries=retained)
    recovered_names = [item["model_name"] for item in recovered]
    # Stability: still-requested families remain served at recovery under the
    # single-fetch policy (verified: cohere twins + poolside namespace).
    assert retained_aliases <= set(recovered_names)
    # Recovery: fresh emission for what the catalogue now lists, and the
    # still-registry-requested old family stays served (the pre-existing
    # LIVE_CATALOGUE_PROVIDERS requested-absent retention policy) — alias
    # mapping must be stable across the outage/restart, with every alias
    # emitted exactly once (no duplicate identical entries).
    assert any(
        item.startswith(f"{provider}/") and new_model.split("/")[-1] in item
        for item in recovered_names
    )
    duplicates = sorted(
        name for name, count in Counter(recovered_names).items() if count > 1
    )
    assert duplicates == [], f"duplicate aliases after recovery: {duplicates}"


def test_poolside_catalogue_is_fetched_once_per_generation(monkeypatch):
    """One fetch owns selection; first-failure/second-success is impossible."""
    monkeypatch.setattr(litellm_config, "active_engines", lambda: [])
    monkeypatch.setattr(litellm_config, "_registry_model_refs", lambda: set())
    calls = []
    catalogue = ["poolside/laguna-current"]

    def fetch(provider, *_args):
        calls.append(provider)
        return catalogue

    monkeypatch.setattr(litellm_config, "_live_catalogue", fetch)
    environ = {
        "POOLSIDE_API_KEY": "synthetic-key",
        "DOTFILES_LITELLM_CATALOGUE_SCOPE": "full",
    }
    current = litellm_config.compute_model_list(environ)
    assert calls == ["poolside"]
    assert "poolside/laguna-current" in {item["model_name"] for item in current}

    previous = [
        {
            "model_name": "poolside/laguna-stale",
            "litellm_params": {
                "model": "openai/poolside/laguna-stale",
                "api_base": litellm_config.PROVIDER_ENDPOINTS["poolside"]["baseUrl"],
                "api_key": "os.environ/POOLSIDE_API_KEY",
            },
        }
    ]
    calls.clear()
    catalogue[:] = ["poolside/laguna-current"]
    selected = litellm_config.compute_model_list(environ, previous_entries=previous)
    names = {item["model_name"] for item in selected}
    assert calls == ["poolside"]
    assert "poolside/laguna-current" in names
    assert "poolside/laguna-stale" not in names

    def outage(provider, *_args):
        calls.append(provider)
        raise litellm_config.LiveCatalogueError("simulated outage")

    monkeypatch.setattr(litellm_config, "_live_catalogue", outage)
    calls.clear()
    retained = litellm_config.compute_model_list(environ, previous_entries=previous)
    assert calls == ["poolside"]
    assert "poolside/laguna-stale" in {item["model_name"] for item in retained}


def test_meridian_generates_all_allowlisted_model_aliases(monkeypatch):
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    allowlist = json.loads(
        (root / "configs/opencode/anthropic-models.json").read_text()
    )["models"]
    monkeypatch.setattr(litellm_config, "is_meridian_configured", lambda: True)
    entries = litellm_config.compute_model_list({"MERIDIAN_API_KEY": "test-key"})
    aliases = {entry["model_name"] for entry in entries}
    missing = sorted(
        f"meridian/{model}" for model in allowlist if f"meridian/{model}" not in aliases
    )
    assert not missing, f"Uncovered canonical identities: {missing}"
    # The Anthropic wire adapter appends /v1/messages itself, so the wire
    # base must be root-only (regression for POST /v1/v1/messages 404s).
    meridian = next(
        entry
        for entry in entries
        if entry["model_name"] == "meridian/claude-sonnet-5-5"
    )
    assert not meridian["litellm_params"]["api_base"].endswith("/v1")


def test_qualified_model_alias_survives_bare_alias_collision(monkeypatch):
    monkeypatch.setattr(litellm_config, "active_engines", lambda: ["omlx", "ollama"])
    monkeypatch.setattr(
        litellm_config,
        "iter_engine_models",
        lambda provider: ["foo" if provider == "omlx" else "omlx/foo"],
    )
    monkeypatch.setattr(litellm_config, "list_cloud_ollama_models", lambda: [])
    monkeypatch.setattr(
        litellm_config,
        "local_endpoint_for",
        lambda provider, proto: (
            (f"http://127.0.0.1:{OMLX_DEFAULT_PORT}/v1", None)
            if provider == "omlx"
            else None
        ),
    )
    entries = litellm_config.compute_model_list({})
    aliases = [item["model_name"] for item in entries]
    assert aliases.count("omlx/foo") == 1
    surviving = next(item for item in entries if item["model_name"] == "omlx/foo")
    assert surviving["litellm_params"]["model"] == "openai/foo"


@pytest.mark.parametrize(
    "provider,key,api_base,wire_prefix,model_id",
    [
        (
            "cohere",
            "COHERE_API_KEY",
            "https://api.cohere.ai",
            "cohere_chat/",
            "command-r",
        ),
        (
            "huggingface",
            "HF_TOKEN",
            "https://router.huggingface.co",
            "huggingface/",
            "org/model",
        ),
    ],
)
def test_live_catalogue_key_providers_generate_only_with_keys(
    monkeypatch, provider, key, api_base, wire_prefix, model_id
):
    monkeypatch.setattr(litellm_config, "_live_catalogue", lambda *args: [model_id])
    entries = litellm_config.compute_model_list(
        {
            # Full scope: the stub id is not a curated registry selection.
            "DOTFILES_LITELLM_CATALOGUE_SCOPE": "full",
            key: "test-key",
        }
    )
    alias = f"{wire_prefix}{model_id}"
    entry = next(item for item in entries if item["model_name"] == alias)
    assert entry["litellm_params"]["model"] == alias
    assert "api_base" not in entry["litellm_params"]
    assert entry["litellm_params"]["api_key"] == f"os.environ/{key}"
    assert not any(
        item["model_name"].startswith(wire_prefix)
        for item in litellm_config.compute_model_list({"UNRELATED": ""})
    )


def test_poolside_emits_direct_provider_lane(monkeypatch):
    # The live Poolside catalogue returns poolside/-prefixed ids (verified
    # against inference.poolside.ai/v1/models in the #77 spike); the
    # standalone emission only accepts its own namespace.
    monkeypatch.setattr(
        litellm_config,
        "_live_catalogue",
        lambda *args: ["poolside/laguna-s-2.1"],
    )
    entries = litellm_config.compute_model_list(
        {"DOTFILES_LITELLM_CATALOGUE_SCOPE": "full", "POOLSIDE_API_KEY": "test-key"}
    )
    entry = next(
        item for item in entries if item["model_name"] == "poolside/laguna-s-2.1"
    )
    assert entry["litellm_params"]["model"] == "openai/poolside/laguna-s-2.1"
    assert entry["litellm_params"]["api_base"] == "https://inference.poolside.ai/v1"
    assert entry["litellm_params"]["model_info"] == POOLSIDE_ROUTED_MODEL_INFO
    rendered = litellm_config.render_config(entries=[entry])
    parsed = yaml.safe_load(rendered)
    item = parsed["model_list"][0]
    assert "model_info" not in item["litellm_params"]
    assert item["model_info"] == POOLSIDE_ROUTED_MODEL_INFO


def test_cohere_emits_live_catalogue_lane(monkeypatch):
    monkeypatch.setattr(
        litellm_config, "_live_catalogue", lambda *args: ["command-a-03-2025"]
    )
    entries = litellm_config.compute_model_list(
        {"DOTFILES_LITELLM_CATALOGUE_SCOPE": "full", "COHERE_API_KEY": "test-key"}
    )
    entry = next(
        item
        for item in entries
        if item["model_name"] == "cohere_chat/command-a-03-2025"
    )
    assert entry["litellm_params"]["model"] == "cohere_chat/command-a-03-2025"


def test_cohere_slim_refs_match_live_catalogue(monkeypatch):
    monkeypatch.setattr(
        litellm_config, "_live_catalogue", lambda *args: ["command-a-03-2025"]
    )
    entries = litellm_config.compute_model_list(
        {"COHERE_API_KEY": "test-key", "DOTFILES_LITELLM_CATALOGUE_SCOPE": "free"}
    )
    assert "cohere_chat/command-a-03-2025" in {item["model_name"] for item in entries}


def test_poolside_strips_provider_prefix_and_obeys_free_scope(monkeypatch):
    monkeypatch.setattr(
        litellm_config,
        "_live_catalogue",
        lambda *args: ["poolside/laguna-xs-2.1", "poolside/laguna-s-2.1"],
    )
    entries = litellm_config.compute_model_list(
        {"POOLSIDE_API_KEY": "test-key", "DOTFILES_LITELLM_CATALOGUE_SCOPE": "free"}
    )
    aliases = {item["model_name"] for item in entries}
    assert "poolside/laguna-xs-2.1" in aliases
    assert "poolside/laguna-s-2.1" in aliases
    assert not any(name.startswith("poolside/poolside/") for name in aliases)


def test_poolside_is_not_emitted_in_free_scope_without_registry_reference(monkeypatch):
    monkeypatch.setattr(
        litellm_config, "_registry_model_refs", lambda: {"openai/gpt-6-luna"}
    )
    monkeypatch.setattr(
        litellm_config, "_live_catalogue", lambda *args: ["poolside/laguna-xs-2.1"]
    )
    entries = litellm_config.compute_model_list(
        {"POOLSIDE_API_KEY": "test-key", "DOTFILES_LITELLM_CATALOGUE_SCOPE": "free"}
    )
    assert not any(item["model_name"].startswith("poolside/") for item in entries)


def test_registry_identities_have_exact_qualified_aliases(monkeypatch):
    """Verify catalogued registry identities get exact aliases and unknowns don't."""
    root = Path(__file__).resolve().parents[3]
    litellm_config._registry_model_refs()
    opencode_allowlist = json.loads(
        (root / "configs/opencode/opencode-models.json").read_text()
    )["models"]
    keys = {
        "openai": "OPENAI_API_KEY",
        "google": "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "ollama-cloud": "OLLAMA_API_KEY",
        "opencode": "OPENCODE_API_KEY",
    }
    # Expected identities are parsed independently from the source files.
    junie = json.loads((root / "configs/junie/model-groups.json").read_text())
    slim = json.loads((root / "configs/opencode/oh-my-opencode-slim.json").read_text())
    expected = set()
    prefixes = (
        "openai/",
        "google/models/",
        "openrouter/",
        "ollama-cloud/",
        "opencode/",
    )

    def independent_walk(value):
        if isinstance(value, dict):
            provider = value.get("provider")
            for field, provider_field in (
                ("primaryModel", "provider"),
                ("fasterModel", "fasterProvider"),
                ("model", "provider"),
            ):
                model = value.get(field)
                actual_provider = value.get(provider_field) or provider
                if not isinstance(model, str) or model.startswith(
                    ("http://", "https://", "_local:")
                ):
                    continue
                if actual_provider in (
                    "openai",
                    "google",
                    "openrouter",
                    "ollama-cloud",
                    "opencode",
                ):
                    prefix = {
                        "openai": "openai/",
                        "google": "google/models/",
                        "openrouter": "openrouter/",
                        "ollama-cloud": "ollama-cloud/",
                        "opencode": "opencode/",
                    }[actual_provider]
                    expected.add(
                        model
                        if model.startswith(prefix)
                        else f"{prefix}{model.removeprefix('models/') if actual_provider == 'google' else model}"
                    )
                elif model.startswith(prefixes):
                    expected.add(model)
            for field, child in value.items():
                if field == "fallback":
                    independent_fallback(child)
                elif field not in ("primaryModel", "fasterModel", "model"):
                    independent_walk(child)
        elif isinstance(value, list):
            for child in value:
                independent_walk(child)

    def independent_fallback(value):
        if isinstance(value, str) and value.startswith(prefixes):
            expected.add(value)
        elif isinstance(value, (dict, list)):
            values = value.values() if isinstance(value, dict) else value
            for child in values:
                independent_fallback(child)

    independent_walk(junie)
    independent_walk(slim)
    docs = (root / "docs/TIERS.md").read_text()
    import re

    in_tier_table = False
    for line in docs.splitlines():
        if line.startswith("### "):
            in_tier_table = " Tier" in line
        if in_tier_table and "|" in line:
            expected.update(
                re.findall(
                    r"`((?:ollama-cloud|opencode|openrouter|google/models|openai)/[A-Za-z0-9._:/-]+)`",
                    line,
                )
            )
    assert "https://example.com/openai/ignored" not in expected
    assert "_local:placeholder" not in expected
    # The free preset's sole openrouter/nvidia reference was retargeted to
    # opencode/nemotron-3.5-lightning-free for keyless machines; restore an
    # openrouter canary here if a keyed free reference returns to the registry.
    verified = {
        ref
        for ref in expected
        if ref.startswith(("openai/", "google/models/", "openrouter/"))
    }
    verified.update(
        ref
        for ref in expected
        if ref.startswith("opencode/") and ref.split("/", 1)[1] in opencode_allowlist
    )
    catalogue_ids = {
        "google": [
            ref.split("/", 2)[2] for ref in verified if ref.startswith("google/models/")
        ],
        "openrouter": [
            ref.split("/", 1)[1] for ref in verified if ref.startswith("openrouter/")
        ],
        "openai": [
            ref.split("/", 1)[1] for ref in verified if ref.startswith("openai/")
        ],
        "ollama-cloud": [],
    }
    monkeypatch.setattr(
        litellm_config,
        "_live_catalogue",
        lambda provider, *args: catalogue_ids.get(provider, []),
    )
    environ = {key: "test-key" for key in keys.values()}
    entries = litellm_config.compute_model_list(environ)
    aliases = {item["model_name"] for item in entries}
    unknown = sorted(expected - verified)
    print(f"UNKNOWN registry IDs: {unknown}")
    assert not (
        verified - aliases
    ), f"Uncovered identities: {sorted(verified - aliases)}"
    assert any(
        "UNKNOWN registry reference" in note
        for note in litellm_config.last_generation_notes
    )
    assert any(
        note.startswith("ollama-cloud/") and "UNKNOWN" in note
        for note in litellm_config.last_generation_notes
    )
    for ref in expected:
        if ref in verified:
            assert ref in aliases, f"confirmed identity missing: {ref}"
        else:
            assert any(
                note.startswith(f"{ref}: UNKNOWN")
                for note in litellm_config.last_generation_notes
            ), ref


def test_registry_collector_handles_fallback_and_provider_owned_nested_id():
    fixture = {
        "groups": {"owned": {"provider": "openrouter", "primaryModel": "openai/gpt-x"}},
        "_tiers": {"tier": {"fallback": [["openai/gpt-5.4-mini"]]}},
    }
    assert litellm_config._collect_model_refs(fixture) == {
        "openrouter/openai/gpt-x",
        "openai/gpt-5.4-mini",
    }


def test_markdown_model_collector_uses_captured_model_cell_refs():
    fixture = """### Example Tier
| Role | Model |
| --- | --- |
| main | `openrouter/vendor/model-x` |
| ignored | `https://example.com/openai/nope` |
| placeholder | `_local:code` |
"""
    assert litellm_config._markdown_model_refs(fixture) == {"openrouter/vendor/model-x"}


def test_model_field_collector_uses_faster_provider_not_primary():
    fixture = {
        "groups": {
            "route": {
                "provider": "openai",
                "primaryModel": "primary-model",
                "fasterProvider": "google",
                "fasterModel": "faster-model",
                "url": "https://example.com/openai/ignored",
                "placeholder": "_local:code",
            }
        }
    }
    refs = litellm_config._collect_model_refs(fixture)
    assert refs == {"openai/primary-model", "google/models/faster-model"}


@pytest.mark.parametrize(
    "provider,key",
    [
        ("openai", "OPENAI_API_KEY"),
        ("opencode", "OPENCODE_API_KEY"),
        ("cerebras", "CEREBRAS_API_KEY"),
    ],
)
def test_empty_provider_sources_never_emit_default_aliases(monkeypatch, provider, key):
    monkeypatch.setattr(litellm_config, "_live_catalogue", lambda *args: [])
    monkeypatch.setattr(litellm_config, "provider_models", lambda name: [])
    entries = litellm_config.compute_model_list({key: "test-key"})
    assert not any(item["model_name"].endswith("/default") for item in entries)
    assert not any(item["model_name"].startswith(f"{provider}/") for item in entries)


def test_cerebras_catalogue_entry_has_no_api_base(monkeypatch):
    monkeypatch.setattr(litellm_config, "_live_catalogue", lambda *args: ["model-x"])
    entries = litellm_config.compute_model_list(
        {
            # Full scope: the stub id is not a curated registry selection.
            "DOTFILES_LITELLM_CATALOGUE_SCOPE": "full",
            "CEREBRAS_API_KEY": "test-key",
        }
    )
    entry = next(item for item in entries if item["model_name"] == "cerebras/model-x")
    assert entry["litellm_params"]["model"] == "cerebras/model-x"
    assert "api_base" not in entry["litellm_params"]


def test_free_scope_openrouter_keeps_curated_and_free_suffixes(monkeypatch):
    monkeypatch.setattr(
        litellm_config,
        "_live_catalogue",
        lambda *args: ["free-model:free", "paid-model"],
    )
    # The real registry no longer carries a curated ling ref (upstream churn);
    # stub a fixed curated reference so the curated-branch assertion runs.
    monkeypatch.setattr(
        litellm_config,
        "_registry_model_refs",
        lambda: {"openrouter/curated-model"},
    )
    entries = litellm_config.compute_model_list({"OPENROUTER_API_KEY": "test-" + "key"})
    aliases = {item["model_name"] for item in entries}
    assert "openrouter/free-model:free" in aliases
    # The curated bare key maps to its raw ':free' spelling — the bare paid
    # twin must never be emitted.
    assert "openrouter/curated-model" not in aliases
    assert "openrouter/curated-model:free" not in aliases
    assert "openrouter/paid-model" not in aliases


def test_free_scope_prefers_free_spelling_for_curated_openrouter(monkeypatch):
    # Upstream can carry bare and ':free' spellings of the same model under
    # one canonical key; the mapping must deterministically prefer the
    # ':free' spelling so the bare paid-route twin never emits.
    monkeypatch.setattr(
        litellm_config,
        "_live_catalogue",
        lambda *args: ["dupe-model", "dupe-model:free"],
    )
    monkeypatch.setattr(
        litellm_config,
        "_registry_model_refs",
        lambda: {"openrouter/dupe-model"},
    )
    entries = litellm_config.compute_model_list({"OPENROUTER_API_KEY": "test-" + "key"})
    aliases = {item["model_name"] for item in entries}
    assert aliases == {"openrouter/dupe-model:free"}


def test_free_scope_unreferenced_providers_emit_nothing(monkeypatch):
    monkeypatch.setattr(litellm_config, "_live_catalogue", lambda *args: ["paid-model"])
    entries = litellm_config.compute_model_list({"HF_TOKEN": "test-" + "key"})
    assert not any(item["model_name"].startswith("huggingface/") for item in entries)
    assert any(
        "huggingface: UNKNOWN (no confirmed models)" in note
        for note in litellm_config.last_generation_notes
    )


def test_full_scope_keeps_whole_catalogue(monkeypatch):
    monkeypatch.setattr(
        litellm_config, "_live_catalogue", lambda *args: ["m-one", "m-two"]
    )
    entries = litellm_config.compute_model_list(
        {"DOTFILES_LITELLM_CATALOGUE_SCOPE": "full", "HF_TOKEN": "test-" + "key"}
    )
    aliases = {item["model_name"] for item in entries}
    assert "huggingface/m-one" in aliases
    assert "huggingface/m-two" in aliases


def test_bogus_scope_is_a_hard_error_before_fetching(monkeypatch):
    calls = []

    def sentinel(*args):
        calls.append(args)
        return []

    monkeypatch.setattr(litellm_config, "_live_catalogue", sentinel)
    with pytest.raises(RuntimeError, match="DOTFILES_LITELLM_CATALOGUE_SCOPE"):
        litellm_config.compute_model_list({"DOTFILES_LITELLM_CATALOGUE_SCOPE": "bogus"})
    assert calls == []


@pytest.mark.parametrize(
    "provider,key",
    [("cohere", "COHERE_API_KEY"), ("huggingface", "HF_TOKEN")],
)
def test_new_catalogue_failure_is_local_and_visible(monkeypatch, provider, key):
    monkeypatch.setattr(
        litellm_config,
        "_live_catalogue",
        lambda name, *args: (
            (_ for _ in ()).throw(litellm_config.LiveCatalogueError("stub failure"))
            if name == provider
            else ["other-model"]
        ),
    )
    entries = litellm_config.compute_model_list(
        {
            # Full scope: the surviving stub id is not a curated registry
            # selection; the failing provider's silence stays asserted below.
            "DOTFILES_LITELLM_CATALOGUE_SCOPE": "full",
            key: "test-key",
            "GEMINI_API_KEY": "google-key",
        }
    )
    assert not any(item["model_name"].startswith("cohere_chat/") for item in entries)
    assert not any(item["model_name"].startswith("huggingface/") for item in entries)
    assert any(item["model_name"] == "google/models/other-model" for item in entries)
    assert any(
        f"{provider}: UNKNOWN" in note for note in litellm_config.last_generation_notes
    )


def test_cohere_catalogue_uses_chat_filter_and_paginates(monkeypatch):
    pages = [
        {
            "models": [
                {"name": "chat-one", "endpoints": ["chat"]},
                {"name": "embed-only", "endpoints": ["embed"]},
                {
                    "name": "deprecated-chat",
                    "endpoints": ["chat"],
                    "is_deprecated": True,
                },
            ],
            "next_page_token": "page-2",
        },
        {"models": [{"name": "chat-two", "endpoints": ["chat"]}]},
    ]
    urls = []

    def response(request, timeout):
        urls.append(request.full_url)
        return io.BytesIO(json.dumps(pages.pop(0)).encode())

    monkeypatch.setattr(litellm_config, "open_same_origin", response)
    monkeypatch.setattr(litellm_config, "_live_catalogue", LIVE_CATALOGUE)
    result = litellm_config._live_catalogue("cohere", "key", "https://api.cohere.ai")
    assert result == ["chat-one", "chat-two"]
    assert "page_size=1000" in urls[0]
    assert "page_token=page-2" in urls[1]


def test_malformed_cohere_catalogue_yields_note_and_no_entries(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "test-key")
    monkeypatch.setattr(
        litellm_config,
        "open_same_origin",
        lambda *args, **kwargs: io.BytesIO(b'{"wrong": []}'),
    )
    monkeypatch.setattr(litellm_config, "_live_catalogue", LIVE_CATALOGUE)
    entries = litellm_config.compute_model_list()
    assert not any(item["model_name"].startswith("cohere_chat/") for item in entries)
    assert any(
        "cohere: UNKNOWN" in note for note in litellm_config.last_generation_notes
    )


@pytest.mark.parametrize(
    "payloads,cap",
    [
        ([{"models": [], "next_page_token": "repeat"}] * 2, 20),
        ([{"models": [], "next_page_token": f"page-{i}"} for i in range(2)], 2),
    ],
)
def test_cohere_catalogue_rejects_repeated_token_and_page_cap(
    monkeypatch, payloads, cap
):
    pages = list(payloads)
    monkeypatch.setattr(litellm_config, "COHERE_CATALOGUE_MAX_PAGES", cap)
    monkeypatch.setattr(
        litellm_config,
        "open_same_origin",
        lambda *args, **kwargs: io.BytesIO(json.dumps(pages.pop(0)).encode()),
    )
    monkeypatch.setattr(litellm_config, "_live_catalogue", LIVE_CATALOGUE)
    with pytest.raises(litellm_config.LiveCatalogueError):
        litellm_config._live_catalogue("cohere", "key", "https://api.cohere.ai")


def test_cohere_catalogue_rejects_non_string_page_token(monkeypatch):
    monkeypatch.setattr(
        litellm_config,
        "open_same_origin",
        lambda *args, **kwargs: io.BytesIO(b'{"models": [], "next_page_token": 7}'),
    )
    monkeypatch.setattr(litellm_config, "_live_catalogue", LIVE_CATALOGUE)
    with pytest.raises(litellm_config.LiveCatalogueError, match="must be a string"):
        litellm_config._live_catalogue("cohere", "key", "https://api.cohere.ai")


def test_openai_registry_reference_requires_key_and_confirmed_catalogue(monkeypatch):
    requested = sorted(
        ref.split("/", 1)[1]
        for ref in litellm_config._registry_model_refs()
        if ref.startswith("openai/")
    )
    assert requested
    model_id = requested[0]
    monkeypatch.setattr(litellm_config, "_live_catalogue", lambda *args: [])
    before = litellm_config.compute_model_list({"OPENAI_API_KEY": "test-key"})
    assert not any(item["model_name"] == f"openai/{model_id}" for item in before)
    monkeypatch.setattr(
        litellm_config,
        "_live_catalogue",
        lambda provider, *args: [model_id] if provider == "openai" else [],
    )
    entries = litellm_config.compute_model_list({"OPENAI_API_KEY": "test-key"})
    assert any(item["model_name"] == f"openai/{model_id}" for item in entries)
    assert not any(item["model_name"] == "openai/default" for item in entries)
    unkeyed = litellm_config.compute_model_list({"UNRELATED": ""})
    assert not any(item["model_name"].startswith("openai/") for item in unkeyed)
    assert any(
        f"openai/{model_id}: UNKNOWN OPENAI_API_KEY is unset" in note
        for note in litellm_config.last_generation_notes
    )


def test_openai_subscription_transport_emits_verified_intersection_and_wire_model(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        litellm_config, "_registry_model_refs", lambda: {"openai/gpt-a", "openai/gpt-b"}
    )
    cache = tmp_path / ".config/litellm/chatgpt/auth.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(
        json.dumps(
            {"access_token": "a", "refresh_token": "r", "expires_at": 4102444800}
        )
    )
    verified = tmp_path / ".local/share/litellm/chatgpt_verified_models.json"
    verified.parent.mkdir(parents=True)
    verified.write_text('["gpt-a"]')
    verified.chmod(0o600)
    entries = litellm_config.compute_model_list(
        {
            "HOME": str(tmp_path),
            "DOTFILES_LITELLM_OAUTH_PROVIDERS": "1",
        }
    )
    openai_entries = {
        entry["model_name"]: entry
        for entry in entries
        if entry["model_name"].startswith("openai/")
    }
    assert openai_entries["openai/gpt-a"]["litellm_params"]["model"] == "chatgpt/gpt-a"
    assert "openai/gpt-b" not in openai_entries
    assert any(
        "openai/gpt-b: subscription-transport candidate NOT verified" in note
        for note in litellm_config.last_generation_notes
    )


@pytest.mark.parametrize("contents", [None, "not-json", '{"gpt-a": true}', '["gpt-a"]'])
def test_openai_subscription_verified_file_missing_or_malformed_is_empty(
    tmp_path, monkeypatch, contents
):
    monkeypatch.setattr(
        litellm_config, "_registry_model_refs", lambda: {"openai/gpt-a"}
    )
    cache = tmp_path / ".config/litellm/chatgpt/auth.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(
        json.dumps(
            {"access_token": "a", "refresh_token": "r", "expires_at": 4102444800}
        )
    )
    verified = tmp_path / ".local/share/litellm/chatgpt_verified_models.json"
    if contents is not None:
        verified.parent.mkdir(parents=True, exist_ok=True)
        verified.write_text(contents)
        verified.chmod(0o600 if contents != '["gpt-a"]' else 0o644)
    entries = litellm_config.compute_model_list(
        {"HOME": str(tmp_path), "DOTFILES_LITELLM_OAUTH_PROVIDERS": "1"}
    )
    assert not any(entry["model_name"] == "openai/gpt-a" for entry in entries)
    assert any(
        "openai/gpt-a: subscription-transport candidate NOT verified" in note
        for note in litellm_config.last_generation_notes
    )


def test_openai_api_key_transport_takes_precedence_over_oauth(tmp_path, monkeypatch):
    monkeypatch.setattr(
        litellm_config, "_registry_model_refs", lambda: {"openai/gpt-a"}
    )
    monkeypatch.setattr(litellm_config, "_live_catalogue", lambda *_args: ["gpt-a"])
    cache = tmp_path / ".config/litellm/chatgpt/auth.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(
        json.dumps(
            {"access_token": "a", "refresh_token": "r", "expires_at": 4102444800}
        )
    )
    verified = tmp_path / ".local/share/litellm/chatgpt_verified_models.json"
    verified.parent.mkdir(parents=True)
    verified.write_text('["gpt-a"]')
    verified.chmod(0o600)
    entries = litellm_config.compute_model_list(
        {
            "HOME": str(tmp_path),
            "OPENAI_API_KEY": "direct-key",
            "DOTFILES_LITELLM_OAUTH_PROVIDERS": "1",
        }
    )
    entry = next(item for item in entries if item["model_name"] == "openai/gpt-a")
    assert entry["litellm_params"]["model"] == "openai/gpt-a"


def test_openai_service_lanes_are_direct_key_only_and_gate_default_off(monkeypatch):
    models = {"gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna"}
    monkeypatch.setattr(
        litellm_config,
        "_registry_model_refs",
        lambda: {f"openai/{model}" for model in models},
    )
    monkeypatch.setattr(
        litellm_config, "_live_catalogue", lambda *_args: sorted(models)
    )
    base = {
        "OPENAI_API_KEY": "test-key",
        "DOTFILES_LITELLM_CATALOGUE_SCOPE": "full",
    }
    off = litellm_config.compute_model_list(base)
    assert not any(item["model_name"].endswith(("-flex", "-fast")) for item in off)
    on = litellm_config.compute_model_list(
        {**base, "DOTFILES_USE_OPENAI_SERVICE_LANES": "1"}
    )
    aliases = {item["model_name"]: item for item in on}
    for model in models:
        for tier in ("flex", "fast"):
            alias = f"openai/{model}-{tier}"
            assert aliases[alias]["litellm_params"]["service_tier"] == tier
    assert sum(name.endswith(("-flex", "-fast")) for name in aliases) == 6
    oauth = litellm_config.compute_model_list(
        {"DOTFILES_USE_OPENAI_SERVICE_LANES": "1"}
    )
    assert not any(item["model_name"].endswith(("-flex", "-fast")) for item in oauth)


def test_openai_service_lane_unknown_not_expanded_and_collision_fails(monkeypatch):
    monkeypatch.setattr(
        litellm_config, "_registry_model_refs", lambda: {"openai/unlisted-model"}
    )
    monkeypatch.setattr(
        litellm_config, "_live_catalogue", lambda *_args: ["unlisted-model"]
    )
    env = {
        "OPENAI_API_KEY": "test-key",
        "DOTFILES_LITELLM_CATALOGUE_SCOPE": "full",
        "DOTFILES_USE_OPENAI_SERVICE_LANES": "1",
    }
    assert not any(
        "-flex" in item["model_name"] for item in litellm_config.compute_model_list(env)
    )
    monkeypatch.setattr(
        litellm_config, "_registry_model_refs", lambda: {"openai/gpt-6.1-sol"}
    )
    monkeypatch.setattr(
        litellm_config,
        "_live_catalogue",
        lambda *_args: ["gpt-6.1-sol", "gpt-6.1-sol-flex"],
    )
    with pytest.raises(ValueError, match="service-lane alias collision"):
        litellm_config.compute_model_list(env)


def test_service_lane_rates_and_gate_off_render(monkeypatch):
    rates_path = (
        Path(__file__).resolve().parents[3] / "configs/litellm/model-rates.json"
    )
    rates = json.loads(rates_path.read_text())["models"]
    standard = {
        "gpt-6-astra": (1e-5, 5e-5, 1e-6, 1.25e-5),
        "gpt-6-luna": (1e-7, 5e-7, 1e-8, 1.25e-7),
    }
    rate_keys = (
        "input_cost_per_token",
        "output_cost_per_token",
        "cache_read_input_token_cost",
        "cache_creation_input_token_cost",
    )
    sol = (2e-6, 1e-5, 1e-7, 2.5e-6)
    for suffix, multiplier in (("flex", 0.5), ("fast", 2.0)):
        assert tuple(rates[f"gpt-6.1-sol-{suffix}"][key] for key in rate_keys) == tuple(
            value * multiplier for value in sol
        )
    for model, values in standard.items():
        assert tuple(rates[model][key] for key in rate_keys) == values
        for suffix, multiplier in (("flex", 0.5), ("fast", 2.0)):
            lane = rates[f"{model}-{suffix}"]
            assert tuple(lane[key] for key in rate_keys) == tuple(
                value * multiplier for value in values
            )
    monkeypatch.setattr(litellm_config, "_registry_model_refs", lambda: set())
    unset = litellm_config.render_config({})
    head_fixture = litellm_config.render_config(
        {"DOTFILES_USE_OPENAI_SERVICE_LANES": "0"}
    )
    assert unset == head_fixture


def test_bare_alias_ambiguity_is_removed_in_either_provider_order(monkeypatch):
    monkeypatch.setattr(litellm_config, "iter_engine_models", lambda provider: ["same"])
    monkeypatch.setattr(litellm_config, "list_cloud_ollama_models", lambda: [])
    monkeypatch.setattr(
        litellm_config,
        "local_endpoint_for",
        lambda *_: (f"http://127.0.0.1:{OMLX_DEFAULT_PORT}/v1", None),
    )
    monkeypatch.setattr(
        litellm_config, "get_ollama_local_base_url", lambda: "http://127.0.0.1:11434/v1"
    )
    generated = []
    for order in (["ollama", "omlx"], ["omlx", "ollama"]):
        monkeypatch.setattr(litellm_config, "active_engines", lambda order=order: order)
        entries = litellm_config.compute_model_list({"UNRELATED": ""})
        aliases = {entry["model_name"] for entry in entries}
        assert {"ollama/same", "omlx/same"} <= aliases
        assert "same" not in aliases
        generated.append(entries)
    assert generated[0] == generated[1]


def test_legacy_litellm_canaries_or_migrate_idempotently_and_explicit_wins():
    old_names = MIGRATE.LITELLM_PROXY_OR_MIGRATION[0]
    lines = [f"{name}='0'\n" for name in old_names]
    lines[1] = f"{old_names[1]}='true'\n"
    migrated, changes = MIGRATE.migrate_env(lines)
    assert "DOTFILES_USE_LITELLM_PROXY='1'" in "".join(migrated)
    assert all(
        any(line.startswith(f"# {name}=") for line in migrated) for name in old_names
    )
    migrated_again, second_changes = MIGRATE.migrate_env(migrated)
    assert migrated_again == migrated
    assert second_changes == []

    explicit, _ = MIGRATE.migrate_env([*lines, "DOTFILES_USE_LITELLM_PROXY='0'\n"])
    assert "DOTFILES_USE_LITELLM_PROXY='0'\n" in explicit
    assert "DOTFILES_USE_LITELLM_PROXY='1'" not in "".join(explicit)

    comments, _ = MIGRATE.migrate_env([f"# {name}='1'\n" for name in old_names])
    assert not any(line.startswith("DOTFILES_USE_LITELLM_PROXY=") for line in comments)


def test_cohere_service_key_syncs_from_config_reference(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    root = tmp_path / "litellm"
    root.mkdir()
    env_path = root / "service.env"
    (root / "config.yaml").write_text("api_key: os.environ/COHERE_API_KEY\n")
    (tmp_path / ".env").write_text("COHERE_API_KEY='cohere-test-key'\n")
    script = (
        f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
        f"source {shlex.quote(str(helper))}; "
        f"litellm_service_env_sync {shlex.quote(str(env_path))}"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env={"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "COHERE_API_KEY=cohere-test-key" in env_path.read_text()
    (tmp_path / ".env").write_text("")
    result = subprocess.run(
        ["bash", "-c", script],
        env={"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "COHERE_API_KEY=" not in env_path.read_text()


def test_generation_notes_persist_atomically_with_private_mode(tmp_path):
    path = tmp_path / "litellm" / "last_generation_notes.json"
    CONFIGURE._persist_generation_notes(["openai/model: UNKNOWN"], path)
    assert json.loads(path.read_text()) == ["openai/model: UNKNOWN"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_local_registry_models_use_protocol_specific_entries(monkeypatch):
    monkeypatch.setattr(litellm_config, "active_engines", lambda: ["omlx", "ollama"])
    monkeypatch.setattr(
        litellm_config,
        "iter_engine_models",
        lambda provider: [{"name": "model-a" if provider == "omlx" else "model-b"}],
    )
    monkeypatch.setattr(
        litellm_config,
        "local_endpoint_for",
        lambda provider, protocol: (
            ("http://127.0.0.1:8000/v1", None) if provider == "omlx" else None
        ),
    )
    aliases = {
        entry["model_name"]: entry for entry in litellm_config.compute_model_list()
    }
    assert aliases["omlx/model-a"]["litellm_params"]["model"] == "openai/model-a"
    assert aliases["ollama/model-b"]["litellm_params"]["model"] == "ollama/model-b"
    assert (
        aliases["model-a"]["litellm_params"]
        == aliases["omlx/model-a"]["litellm_params"]
    )
    assert (
        aliases["model-b"]["litellm_params"]
        == aliases["ollama/model-b"]["litellm_params"]
    )


def test_ollama_cloud_stubs_are_routed_through_local_daemon_without_duplicates(
    monkeypatch,
):
    monkeypatch.setattr(litellm_config, "active_engines", lambda: ["ollama"])
    monkeypatch.setattr(
        litellm_config,
        "iter_engine_models",
        lambda provider: [
            {"name": "glm-5.3:cloud"},
            {"name": "qwen3-cloud"},
            {"name": "local-model"},
        ],
    )
    monkeypatch.setattr(
        litellm_config,
        "list_cloud_ollama_models",
        lambda: [{"name": "glm-5.3:cloud"}, {"name": "deepseek-cloud"}],
    )
    monkeypatch.setattr(
        litellm_config, "get_ollama_local_base_url", lambda: "http://127.0.0.1:11434"
    )

    entries = litellm_config.compute_model_list()
    aliases = [entry["model_name"] for entry in entries]
    by_alias = {entry["model_name"]: entry for entry in entries}

    # Cloud stubs keep exactly the ollama/-namespaced alias: the naked
    # '<stub>' spelling was a duplicate of the same daemon route.
    assert aliases.count("ollama/glm-5.3:cloud") == 1
    assert "glm-5.3:cloud" not in aliases
    assert {"ollama/qwen3-cloud", "ollama/deepseek-cloud"} <= set(aliases)
    assert "qwen3-cloud" not in aliases and "deepseek-cloud" not in aliases
    assert {"ollama/local-model", "local-model"} <= set(aliases)
    assert by_alias["ollama/glm-5.3:cloud"]["litellm_params"] == {
        "model": "ollama/glm-5.3:cloud",
        "api_base": "http://127.0.0.1:11434",
    }


def test_model_list_and_config_are_stable_across_discovery_order(tmp_path, monkeypatch):
    monkeypatch.setattr(litellm_config, "active_engines", lambda: ["omlx", "ollama"])
    discovered = {
        "omlx": ["shared", "omlx-zeta", "omlx-alpha", "omlx-alpha"],
        "ollama": ["shared-cloud", "local-zeta", "shared", "shared-cloud"],
    }
    monkeypatch.setattr(
        litellm_config,
        "iter_engine_models",
        lambda provider: [{"name": name} for name in discovered[provider]],
    )
    monkeypatch.setattr(
        litellm_config,
        "local_endpoint_for",
        lambda provider, protocol: (
            ("http://127.0.0.1:8000/v1", "OMLX_API_KEY") if provider == "omlx" else None
        ),
    )
    cloud_models = [{"name": name} for name in ("shared-cloud", "cloud-alpha")]
    monkeypatch.setattr(
        litellm_config, "list_cloud_ollama_models", lambda: cloud_models
    )
    monkeypatch.setattr(
        litellm_config,
        "get_ollama_local_base_url",
        lambda: "http://127.0.0.1:11434/v1",
    )
    catalogue = ["router-alpha", "router-zeta"]
    monkeypatch.setattr(litellm_config, "_live_catalogue", lambda *args: catalogue)
    environ = {
        # Full scope: stub catalogue ids are not curated registry selections,
        # and the ordering-stability assertion needs the full alias set.
        "DOTFILES_LITELLM_CATALOGUE_SCOPE": "full",
        "LITELLM_PORT": "4400",
        "OMLX_API_KEY": "test-omlx-key",
        "OPENROUTER_API_KEY": "test-openrouter-key",
    }

    first_entries = litellm_config.compute_model_list(environ)
    first_config = litellm_config.render_config(environ, first_entries)
    config_path = tmp_path / "config.yaml"
    assert litellm_config.write_config(config_path, environ, first_entries) is True
    first_bytes = config_path.read_bytes()

    discovered["omlx"].reverse()
    discovered["ollama"].reverse()
    cloud_models.reverse()
    second_entries = litellm_config.compute_model_list(environ)
    second_config = litellm_config.render_config(environ, second_entries)
    assert second_entries == first_entries
    assert second_config == first_config
    assert litellm_config.write_config(config_path, environ, second_entries) is False
    assert config_path.read_bytes() == first_bytes

    aliases = [entry["model_name"] for entry in second_entries]
    assert set(aliases) == {
        "omlx/omlx-alpha",
        "omlx-alpha",
        "omlx/omlx-zeta",
        "omlx-zeta",
        "omlx/shared",
        "ollama/cloud-alpha",
        "cloud-alpha",
        "ollama/local-zeta",
        "local-zeta",
        "ollama/shared",
        "ollama/shared-cloud",
        "openrouter/router-alpha",
        "openrouter/router-zeta",
    }
    assert aliases.count("omlx/omlx-alpha") == 1
    assert aliases.count("omlx-alpha") == 1
    by_alias = {entry["model_name"]: entry for entry in second_entries}
    assert by_alias["omlx/shared"]["litellm_params"] == {
        "model": "openai/shared",
        "api_base": "http://127.0.0.1:8000/v1",
        "api_key": "os.environ/OMLX_API_KEY",
    }
    assert by_alias["ollama/shared-cloud"]["litellm_params"] == {
        "model": "ollama/shared-cloud",
        "api_base": "http://127.0.0.1:11434",
    }
    assert "shared" not in aliases


def test_app_key_provisioning_is_alias_idempotent_and_mode_600(tmp_path, monkeypatch):
    calls = []

    def fake_request(url, method, master_key, payload=None, timeout=5):
        calls.append((url, method, payload))
        if "/key/list" in url:
            return {"keys": [{"key_alias": "opencode"}]}
        if url.endswith("/key/generate"):
            return {"key": f"sk-generated-{payload['key_alias']}"}
        return {"status": "healthy"}

    monkeypatch.setattr(CONFIGURE, "_request_json", fake_request)
    path = tmp_path / "service.env"
    path.write_text("LITELLM_MASTER_KEY='master'\n", encoding="utf-8")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_RUN_OPENCODE_SETUP", "1")
    monkeypatch.setenv("DOTFILES_RUN_PI_SETUP", "1")
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.setenv("DOTFILES_RUN_JUNIE_CLI_SETUP", "1")
    monkeypatch.setenv("DOTFILES_RUN_CODEX_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_COPILOT_SETUP", "0")
    monkeypatch.setattr(CONFIGURE.shutil, "which", lambda _name: "/bin/client")

    CONFIGURE.provision_app_keys("master", path, "http://127.0.0.1:4000")

    assert sorted(c[2]["key_alias"] for c in calls if c[1] == "POST") == sorted(
        ("pi", "openwebui", "junie", "codex", "claude")
    )
    content = path.read_text(encoding="utf-8")
    assert "LITELLM_PI_KEY=sk-generated-pi" in content
    assert "LITELLM_OPENCODE_KEY" not in content
    assert path.stat().st_mode & 0o777 == 0o600


def test_opencode_key_file_is_private_atomic_idempotent_and_rotatable(tmp_path):
    service = tmp_path / "service.env"
    service.write_text("LITELLM_OPENCODE_KEY='dummy-one'\n", encoding="utf-8")
    CONFIGURE._write_opencode_key(service)
    target = tmp_path / "clients" / "opencode.key"
    assert target.read_text(encoding="utf-8") == "dummy-one"
    assert target.parent.stat().st_mode & 0o777 == 0o700
    assert target.stat().st_mode & 0o777 == 0o600
    inode = target.stat().st_ino
    CONFIGURE._write_opencode_key(service)
    assert target.stat().st_ino == inode
    service.write_text("LITELLM_OPENCODE_KEY='dummy-two'\n", encoding="utf-8")
    CONFIGURE._write_opencode_key(service)
    assert target.read_text(encoding="utf-8") == "dummy-two"
    assert target.stat().st_ino != inode


def test_opencode_key_file_missing_key_and_symlink_fail_closed(tmp_path):
    service = tmp_path / "service.env"
    service.write_text("LITELLM_PI_KEY='dummy'\n", encoding="utf-8")
    CONFIGURE._write_opencode_key(service)
    assert not (tmp_path / "clients").exists()
    target_dir = tmp_path / "clients"
    target_dir.mkdir()
    target = target_dir / "opencode.key"
    target.symlink_to(tmp_path / "outside")
    service.write_text("LITELLM_OPENCODE_KEY='dummy'\n", encoding="utf-8")
    CONFIGURE._write_opencode_key(service)
    assert target.is_symlink()
    assert not (tmp_path / "outside").exists()


def test_junie_key_file_is_idempotent_rotatable_and_symlink_safe(tmp_path):
    service = tmp_path / "service.env"
    service.write_text("LITELLM_JUNIE_KEY='dummy-junie-one'\n", encoding="utf-8")
    CONFIGURE._write_junie_key(service)
    target = tmp_path / "clients/junie.key"
    assert target.read_text(encoding="utf-8") == "dummy-junie-one"
    assert target.parent.stat().st_mode & 0o777 == 0o700
    assert target.stat().st_mode & 0o777 == 0o600
    inode = target.stat().st_ino
    CONFIGURE._write_junie_key(service)
    assert target.stat().st_ino == inode
    service.write_text("LITELLM_JUNIE_KEY='dummy-junie-two'\n", encoding="utf-8")
    CONFIGURE._write_junie_key(service)
    assert target.read_text(encoding="utf-8") == "dummy-junie-two"
    target.unlink()
    target.symlink_to(tmp_path / "outside")
    CONFIGURE._write_junie_key(service)
    assert target.is_symlink()
    assert not (tmp_path / "outside").exists()


def test_pi_key_file_is_private_idempotent_and_rotatable(tmp_path):
    service = tmp_path / "service.env"
    service.write_text("LITELLM_PI_KEY='dummy-one'\n", encoding="utf-8")
    CONFIGURE._write_pi_key(service)
    target = tmp_path / "clients" / "pi.key"
    assert target.read_text(encoding="utf-8") == "dummy-one"
    assert target.parent.stat().st_mode & 0o777 == 0o700
    assert target.stat().st_mode & 0o777 == 0o600
    inode = target.stat().st_ino
    CONFIGURE._write_pi_key(service)
    assert target.stat().st_ino == inode
    service.write_text("LITELLM_PI_KEY='dummy-two'\n", encoding="utf-8")
    CONFIGURE._write_pi_key(service)
    assert target.read_text(encoding="utf-8") == "dummy-two"
    assert target.stat().st_ino != inode


def test_codex_key_file_is_private_idempotent_and_rotatable(tmp_path):
    service = tmp_path / "service.env"
    service.write_text("LITELLM_CODEX_KEY='dummy-one'\n", encoding="utf-8")
    CONFIGURE._write_codex_key(service)
    target = tmp_path / "clients" / "codex.key"
    assert target.read_text(encoding="utf-8") == "dummy-one"
    assert target.parent.stat().st_mode & 0o777 == 0o700
    assert target.stat().st_mode & 0o777 == 0o600
    inode = target.stat().st_ino
    CONFIGURE._write_codex_key(service)
    assert target.stat().st_ino == inode
    service.write_text("LITELLM_CODEX_KEY='dummy-two'\n", encoding="utf-8")
    CONFIGURE._write_codex_key(service)
    assert target.read_text(encoding="utf-8") == "dummy-two"


def test_provisioned_client_key_writers_cover_every_registered_app_key():
    import litellm_config

    assert set(CONFIGURE.PROVISIONED_CLIENT_KEY_WRITERS) == set(litellm_config.APP_KEYS)
    assert set(CONFIGURE.PROVISIONED_CLIENT_KEY_WRITERS) == {
        "opencode",
        "pi",
        "junie",
        "openwebui",
        "codex",
        "claude",
        "copilot-cli",
    }


def test_pi_provider_override_requires_private_file_and_uses_command_key(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    providers = {
        name: {"baseUrl": f"https://{name}.example/v1", "apiKey": "direct"}
        for name in ("openai", "ollama", "omlx")
    }
    with pytest.raises(RuntimeError):
        PI.apply_litellm_provider_overrides(providers)
    assert all(
        provider["baseUrl"].startswith("https://") for provider in providers.values()
    )
    target = tmp_path / ".local/share/litellm/clients/pi.key"
    target.parent.mkdir(parents=True)
    target.write_text("dummy", encoding="utf-8")
    target.parent.chmod(0o700)
    target.chmod(0o600)
    PI.apply_litellm_provider_overrides(providers)
    assert all(p["baseUrl"] == "http://127.0.0.1:4000/v1" for p in providers.values())
    assert all(
        p["apiKey"] == f"!cat {shlex.quote(str(target))}" for p in providers.values()
    )


def test_pi_provider_override_rejects_symlinked_key_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    directory = tmp_path / ".local/share/litellm/clients"
    directory.mkdir(parents=True)
    (directory / "pi.key").symlink_to(tmp_path / "outside")
    providers = {"openai": {"baseUrl": "https://direct/v1", "apiKey": "direct"}}
    with pytest.raises(RuntimeError):
        PI.apply_litellm_provider_overrides(providers)


def test_existing_alias_recreates_file_from_service_env_without_generation(
    tmp_path, monkeypatch
):

    def fake_request(url, method, master_key, payload=None, timeout=5):
        if "/key/list" in url:
            return {"keys": [{"key_alias": alias} for alias in CONFIGURE.APP_KEYS]}
        return {"status": "healthy"}

    monkeypatch.setattr(CONFIGURE, "_request_json", fake_request)
    service = tmp_path / "service.env"
    service.write_text(
        "LITELLM_OPENCODE_KEY='dummy-existing'\n"
        "LITELLM_JUNIE_KEY='dummy-junie-existing'\n",
        encoding="utf-8",
    )
    CONFIGURE.provision_app_keys(
        "dummy-master", service, "http://127.0.0.1:4000", enforce_registry_gates=False
    )
    assert (tmp_path / "clients/opencode.key").read_text(encoding="utf-8") == (
        "dummy-existing"
    )
    junie_key = tmp_path / "clients/junie.key"
    assert junie_key.read_text(encoding="utf-8") == "dummy-junie-existing"
    assert junie_key.parent.stat().st_mode & 0o777 == 0o700
    assert junie_key.stat().st_mode & 0o777 == 0o600


def test_provision_repairs_drifted_client_key_hash(tmp_path, monkeypatch):
    """A materialized key whose registered hash drifted from the local value
    gets repaired in place — Open WebUI rendered no models while its row
    hashed a value that no longer existed on disk."""
    aligns = []
    calls = []

    def fake_request(url, method, master_key, payload=None, timeout=5):
        calls.append((url, method, payload))
        if "/key/list" in url:
            return {
                "keys": [
                    {
                        "key_alias": "openwebui",
                        "token": hashlib.sha256(b"sk-stale").hexdigest(),
                    }
                ]
            }
        if url.endswith("/key/generate"):
            return {"key": "sk-generated-openwebui"}
        return {"status": "healthy"}

    monkeypatch.setattr(CONFIGURE, "_request_json", fake_request)
    monkeypatch.setattr(
        CONFIGURE,
        "_align_client_token_hash",
        lambda alias, token_hash, previous_hash, service_env_path: aligns.append(
            (alias, token_hash, previous_hash)
        ),
    )
    service = tmp_path / "service.env"
    service.write_text("LITELLM_MASTER_KEY='master'\n", encoding="utf-8")
    clients = tmp_path / "clients"
    clients.mkdir(mode=0o700)
    key_file = clients / "openwebui.key"
    key_file.write_text("sk-current", encoding="utf-8")
    os.chmod(key_file, 0o600)

    CONFIGURE.provision_app_keys(
        "master", service, "http://127.0.0.1:4000", enforce_registry_gates=False
    )

    assert aligns == [
        (
            "openwebui",
            hashlib.sha256(b"sk-current").hexdigest(),
            # The UPDATE is constrained to the row that was read.
            hashlib.sha256(b"sk-stale").hexdigest(),
        )
    ]
    # The drifted alias owns canonical local material — never re-generated
    # (only the unprovisioned siblings take the /key/generate path).
    generated = [
        payload["key_alias"]
        for url, _, payload in calls
        if "/key/generate" in url and isinstance(payload, dict)
    ]
    assert "openwebui" not in generated


def test_provision_keeps_matching_client_key_hash(tmp_path, monkeypatch):
    aligns = []

    def fake_request(url, method, master_key, payload=None, timeout=5):
        if "/key/list" in url:
            return {
                "keys": [
                    {
                        "key_alias": "openwebui",
                        "token": hashlib.sha256(b"sk-current").hexdigest(),
                    }
                ]
            }
        return {"status": "healthy"}

    monkeypatch.setattr(CONFIGURE, "_request_json", fake_request)
    monkeypatch.setattr(
        CONFIGURE,
        "_align_client_token_hash",
        lambda alias, token_hash, previous_hash, service_env_path: aligns.append(
            (alias, token_hash, previous_hash)
        ),
    )
    service = tmp_path / "service.env"
    service.write_text("LITELLM_MASTER_KEY='master'\n", encoding="utf-8")
    clients = tmp_path / "clients"
    clients.mkdir(mode=0o700)
    key_file = clients / "openwebui.key"
    key_file.write_text("sk-current", encoding="utf-8")
    os.chmod(key_file, 0o600)

    CONFIGURE.provision_app_keys("master", service, "http://127.0.0.1:4000")

    assert aligns == []


def test_provision_dry_run_reports_drift_without_repair(tmp_path, monkeypatch, caplog):
    aligns = []

    def fake_request(url, method, master_key, payload=None, timeout=5):
        if "/key/list" in url:
            return {
                "keys": [
                    {
                        "key_alias": "openwebui",
                        "token": hashlib.sha256(b"sk-stale").hexdigest(),
                    }
                ]
            }
        return {"status": "healthy"}

    monkeypatch.setattr(CONFIGURE, "_request_json", fake_request)
    monkeypatch.setattr(
        CONFIGURE,
        "_align_client_token_hash",
        lambda alias, token_hash, previous_hash, service_env_path: aligns.append(
            (alias, token_hash, previous_hash)
        ),
    )
    service = tmp_path / "service.env"
    service.write_text("LITELLM_MASTER_KEY='master'\n", encoding="utf-8")
    clients = tmp_path / "clients"
    clients.mkdir(mode=0o700)
    key_file = clients / "openwebui.key"
    key_file.write_text("sk-current", encoding="utf-8")
    os.chmod(key_file, 0o600)

    CONFIGURE.provision_app_keys(
        "master",
        service,
        "http://127.0.0.1:4000",
        dry_run=True,
        enforce_registry_gates=False,
    )

    assert aligns == []
    assert any(
        "drifted" in message and "dry-run" in message for message in caplog.messages
    )


def test_materialized_client_key_value_strips_and_falls_back(tmp_path):
    service = tmp_path / "service.env"
    service.write_text(
        "LITELLM_OPENWEBUI_KEY='sk-from-env'\n" "LITELLM_PI_KEY='sk-pi-env'\n",
        encoding="utf-8",
    )
    clients = tmp_path / "clients"
    clients.mkdir(mode=0o700)
    padded = clients / "openwebui.key"
    padded.write_text("  sk-current\n", encoding="utf-8")
    os.chmod(padded, 0o600)
    value = CONFIGURE._materialized_client_key_value(
        padded, "LITELLM_OPENWEBUI_KEY", service
    )
    # The hash is computed over the credential the clients actually send.
    assert value == "sk-current"
    ws = clients / "pi.key"
    ws.write_text("   \n", encoding="utf-8")
    os.chmod(ws, 0o600)
    # A whitespace-only file is unmaterialized from the FILE side, but the
    # service.env fallback supplies usable material — both sides agree on
    # the same stripped credential the clients send.
    assert CONFIGURE._key_file_materialized(ws, "LITELLM_PI_KEY", service)
    assert (
        CONFIGURE._materialized_client_key_value(ws, "LITELLM_PI_KEY", service)
        == "sk-pi-env"
    )
    # Without any env value there is nothing to repair with.
    bare = tmp_path / "bare.env"
    bare.write_text("LITELLM_OPENWEBUI_KEY=''\n", encoding="utf-8")
    assert not CONFIGURE._key_file_materialized(ws, "LITELLM_PI_KEY", bare)
    assert CONFIGURE._materialized_client_key_value(ws, "LITELLM_PI_KEY", bare) == ""


def test_app_key_hash_alignment_requires_constrained_row(tmp_path, monkeypatch):
    probe = {}

    class FakeProc:
        returncode = 0
        stdout = "[]\n"
        stderr = ""

    def fake_run(argv, **kwargs):
        probe["argv"] = argv
        probe["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = tmp_path / "service.env"
    service.write_text("DATABASE_URL='postgresql://gw/db'\n", encoding="utf-8")
    # A fake managed-venv interpreter so the glob resolves before the stub.
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    (venv_bin / "python3").write_text("", encoding="utf-8")

    import pytest

    with pytest.raises(RuntimeError, match="unexpected alignment result"):
        CONFIGURE._align_client_token_hash(
            "openwebui",
            "new-hash",
            "old-hash",
            service,
        )
    argv = probe["argv"]
    assert argv[-3:] == ["new-hash", "openwebui", "old-hash"]
    assert "AND token = $3" in argv[2]
    assert probe["kwargs"].get("timeout") == 60

    FakeProc.stdout = json.dumps([{"key_alias": "openwebui"}]) + "\n"
    CONFIGURE._align_client_token_hash("openwebui", "new-hash", "old-hash", service)


def test_app_key_hash_alignment_times_out(tmp_path, monkeypatch):
    import pytest

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=60)

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = tmp_path / "service.env"
    service.write_text("DATABASE_URL='postgresql://gw/db'\n", encoding="utf-8")
    # A fake managed-venv interpreter so the glob resolves before the stub.
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    (venv_bin / "python3").write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="timed out"):
        CONFIGURE._align_client_token_hash("openwebui", "h", "old", service)


def test_app_key_provisioning_treats_400_generate_as_alias_exists(
    tmp_path, monkeypatch
):
    """Proxy versions where /key/list hides key_alias: a 400 on generate
    means the alias already exists — provisioning stays idempotent."""
    generate_attempts = []

    def fake_request(url, method, master_key, payload=None, timeout=5):
        if "/key/list" in url:
            return {"keys": []}
        if url.endswith("/key/generate"):
            alias = payload["key_alias"]
            generate_attempts.append(alias)
            raise urllib.error.HTTPError(url, 400, "Bad Request", None, None)
        return {"status": "healthy"}

    monkeypatch.setattr(CONFIGURE, "_request_json", fake_request)
    path = tmp_path / "service.env"
    path.write_text("LITELLM_MASTER_KEY='master'\n", encoding="utf-8")

    CONFIGURE.provision_app_keys(
        "master", path, "http://127.0.0.1:4000", enforce_registry_gates=False
    )

    # Codex is config-only; credential provisioning is deferred.
    # Responses transport and credential qualification remain separate work;
    # the generic key-generation path must not provision this client.
    registry = {row["client"]: row for row in CONFIGURE.CLIENT_REGISTRY}
    expected_clients = [
        c
        for c in CONFIGURE.APP_KEYS
        if "OpenAI-compatible" in registry[c]["transports"] or c == "claude"
    ]
    assert len(generate_attempts) == len(expected_clients)
    # A 400 is not persisted as a key; the service env keeps its prior state.
    assert not any(
        name in path.read_text(encoding="utf-8") for name in CONFIGURE.APP_KEYS.values()
    )


def test_app_key_provisioning_recovers_stranded_alias_and_materializes_key(
    tmp_path, monkeypatch
):
    requests = []

    def fake_request(url, method, master_key, payload=None, timeout=30):
        requests.append((url, method, payload, timeout))
        if "/health/" in url:
            return {"status": "healthy"}
        if "/key/list" in url:
            return {"keys": [{"key_alias": "openwebui", "token": "hash-openwebui"}]}
        if url.endswith("/key/delete"):
            assert payload == {"keys": ["hash-openwebui"]}
            return {"deleted_keys": 1}
        if url.endswith("/key/generate"):
            return {"key": "sk-regenerated-openwebui"}
        return {"keys": []}

    monkeypatch.setattr(CONFIGURE, "_request_json", fake_request)
    service_env = tmp_path / "service.env"
    service_env.write_text("LITELLM_MASTER_KEY='master'\n")
    CONFIGURE.provision_app_keys(
        "master", service_env, "http://127.0.0.1:4000", enforce_registry_gates=False
    )
    recovery_calls = [
        url.removeprefix("http://127.0.0.1:4000")
        for url, method, payload, _timeout in requests
        if method == "POST"
        and (
            payload.get("keys") == ["hash-openwebui"]
            or payload.get("key_alias") == "openwebui"
        )
    ]
    assert recovery_calls == ["/key/delete", "/key/generate"]
    client_key = tmp_path / "clients/openwebui.key"
    assert client_key.read_text() == "sk-regenerated-openwebui"
    assert client_key.stat().st_mode & 0o777 == 0o600
    assert all(timeout == 30 for *_, timeout in requests)


def test_app_key_provisioning_never_deletes_alias_with_local_key(tmp_path, monkeypatch):
    requests = []

    def fake_request(url, method, master_key, payload=None, timeout=30):
        requests.append((url, method, payload))
        if "/key/list" in url:
            return {"keys": [{"key_alias": "openwebui", "token": "hash-openwebui"}]}
        return {"status": "healthy"}

    monkeypatch.setattr(CONFIGURE, "_request_json", fake_request)
    service_env = tmp_path / "service.env"
    service_env.write_text("LITELLM_MASTER_KEY='master'\n")
    client_dir = tmp_path / "clients"
    client_dir.mkdir()
    (client_dir / "openwebui.key").write_text("working-key")
    CONFIGURE.provision_app_keys("master", service_env, "http://127.0.0.1:4000")
    assert not any(url.endswith("/key/delete") for url, _, _ in requests)


def test_copilot_cli_registry_is_separate_and_opt_in():
    row = next(
        row for row in CONFIGURE.CLIENT_REGISTRY if row["client"] == "copilot-cli"
    )
    assert row["env_name"] == "LITELLM_COPILOT_CLI_KEY"
    assert row["key_file"].endswith("/copilot-cli.key")
    assert row["classification"] == "eligible"
    assert "live request remains UNKNOWN" in row["classification_status"]
    assert "DOTFILES_RUN_COPILOT_SETUP=1" in row["setup_gates"]
    assert "DOTFILES_USE_LITELLM_PROXY=1" in row["setup_gates"]
    assert row["config_owner"] == "scripts/copilot-litellm"
    assert row["credential_policy"] == "provision"
    assert "OpenAI-compatible" in row["transports"]


def test_copilot_litellm_launcher_isolated_environment_and_policy(tmp_path):
    launcher = Path(__file__).resolve().parents[2] / "copilot-litellm"
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    clients = home / ".local/share/litellm/clients"
    clients.mkdir(parents=True)
    bin_dir.mkdir()
    recorder = bin_dir / "copilot"
    recorder.write_text(
        '#!/bin/sh\nif [ "$1" = "--help" ]; then printf "%s\\n" "--model" "--resume"; exit 0; fi\n'
        'printf "%s\\n" "$@" > "$RECORD_ARGS"\n'
        'env | sort > "$RECORD_ENV"\n'
    )
    recorder.chmod(0o755)
    key = clients / "copilot-cli.key"
    key.write_text("test-private-key")
    key.chmod(stat.S_IRUSR | stat.S_IWUSR)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "RECORD_ARGS": str(tmp_path / "args"),
        "RECORD_ENV": str(tmp_path / "env"),
        "DOTFILES_RUN_COPILOT_SETUP": "1",
        "DOTFILES_USE_LITELLM_PROXY": "1",
        "DOTFILES_RUN_LITELLM_SETUP": "1",
        "DOTFILES_COPILOT_LITELLM_MODEL": "openai/model-alias",
        "COPILOT_PROVIDER_BEARER_TOKEN": "inherited-bearer",
        "COPILOT_PROVIDER_API_KEY_COMMAND": "secret-command",
        "COPILOT_PROVIDER_HEADERS": "Authorization: inherited",
        "COPILOT_PROVIDER_MODEL_ID": "wrong-id",
        "COPILOT_PROVIDER_WIRE_MODEL": "wrong-wire",
        "COPILOT_PROVIDER_WIRE_API": "responses",
        "COPILOT_PROVIDER_TRANSPORT": "websockets",
        "COPILOT_PROVIDER_TYPE": "anthropic",
        "COPILOT_MODEL": "wrong-model",
    }
    result = subprocess.run(
        [str(launcher), "--resume", "session-id", "--prompt", "- explain this"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "args").read_text().splitlines() == [
        "--resume",
        "session-id",
        "--prompt",
        "- explain this",
    ]
    child_env = dict(
        line.split("=", 1)
        for line in (tmp_path / "env").read_text().splitlines()
        if "=" in line
    )
    assert child_env["COPILOT_PROVIDER_API_KEY"] == "test-private-key"
    assert child_env["COPILOT_PROVIDER_BASE_URL"] == "http://127.0.0.1:4000/v1"
    assert child_env["COPILOT_PROVIDER_TYPE"] == "openai"
    assert child_env["COPILOT_PROVIDER_WIRE_API"] == "completions"
    assert child_env["COPILOT_PROVIDER_TRANSPORT"] == "http"
    assert child_env["COPILOT_MODEL"] == "openai/model-alias"
    for name in (
        "COPILOT_PROVIDER_BEARER_TOKEN",
        "COPILOT_PROVIDER_API_KEY_COMMAND",
        "COPILOT_PROVIDER_HEADERS",
        "COPILOT_PROVIDER_MODEL_ID",
        "COPILOT_PROVIDER_WIRE_MODEL",
    ):
        assert name not in child_env


def test_copilot_litellm_accepts_openrouter_free_twin_alias(tmp_path):
    launcher = Path(__file__).resolve().parents[2] / "copilot-litellm"
    home = tmp_path / "home"
    key = home / ".local/share/litellm/clients/copilot-cli.key"
    key.parent.mkdir(parents=True)
    key.write_text("synthetic-private-key")
    key.chmod(0o600)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    record = tmp_path / "child-model"
    fake = binaries / "copilot"
    fake.write_text(f'#!/bin/sh\nprintf "%s" "$COPILOT_MODEL" > "{record}"\n')
    fake.chmod(0o700)
    alias = "openrouter/vendor/twin:free"
    env = {
        "HOME": str(home),
        "PATH": f"{binaries}:/usr/bin:/bin",
        "DOTFILES_RUN_COPILOT_SETUP": "1",
        "DOTFILES_USE_LITELLM_PROXY": "1",
        "DOTFILES_RUN_LITELLM_SETUP": "1",
        "DOTFILES_COPILOT_LITELLM_MODEL": alias,
        "DOTFILES_COPILOT_LITELLM_BASE_URL": "http://localhost:4000/v1",
    }
    launched = subprocess.run([str(launcher)], env=env, capture_output=True, text=True)
    assert launched.returncode == 0, launched.stderr
    assert record.read_text() == alias
    record.unlink()

    preview = subprocess.run(
        [str(launcher), "--dry-run"],
        env={**env, "HOME": str(tmp_path / "no-key-home")},
        capture_output=True,
        text=True,
    )
    assert preview.returncode == 0, preview.stderr
    assert "credential state UNKNOWN" in preview.stdout
    assert not record.exists()


@pytest.mark.parametrize("model", ["", "   ", "-leading-option"])
def test_copilot_litellm_still_rejects_invalid_aliases(tmp_path, model):
    launcher = Path(__file__).resolve().parents[2] / "copilot-litellm"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    fake = binaries / "copilot"
    fake.write_text("#!/bin/sh\nexit 99\n")
    fake.chmod(0o700)
    result = subprocess.run(
        [str(launcher), "--dry-run"],
        env={
            "HOME": str(tmp_path),
            "PATH": f"{binaries}:/usr/bin:/bin",
            "DOTFILES_RUN_COPILOT_SETUP": "1",
            "DOTFILES_USE_LITELLM_PROXY": "1",
            "DOTFILES_RUN_LITELLM_SETUP": "1",
            "DOTFILES_COPILOT_LITELLM_MODEL": model,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "explicit alias" in result.stderr


@pytest.mark.parametrize(
    "key_text,mode,symlink",
    [("", 0o600, False), ("key", 0o644, False), ("key", 0o600, True)],
)
def test_copilot_litellm_rejects_unsafe_keys(tmp_path, key_text, mode, symlink):
    launcher = Path(__file__).resolve().parents[2] / "copilot-litellm"
    home = tmp_path / "home"
    clients = home / ".local/share/litellm/clients"
    clients.mkdir(parents=True)
    key = clients / "copilot-cli.key"
    key.write_text(key_text)
    key.chmod(mode)
    if symlink:
        target = clients / "target"
        key.rename(target)
        key.symlink_to(target)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "copilot"
    fake.write_text("#!/bin/sh\nexit 99\n")
    fake.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "DOTFILES_RUN_COPILOT_SETUP": "1",
        "DOTFILES_USE_LITELLM_PROXY": "1",
        "DOTFILES_RUN_LITELLM_SETUP": "1",
        "DOTFILES_COPILOT_LITELLM_MODEL": "alias",
    }
    result = subprocess.run([str(launcher)], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "dedicated key file" in result.stderr


def test_copilot_litellm_rejects_env_key_conflict(tmp_path):
    launcher = Path(__file__).resolve().parents[2] / "copilot-litellm"
    home = tmp_path / "home"
    clients = home / ".local/share/litellm/clients"
    clients.mkdir(parents=True)
    key = clients / "copilot-cli.key"
    key.write_text("file-key")
    key.chmod(0o600)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "copilot"
    fake.write_text(
        '#!/bin/sh\nif [ "$1" = "--help" ]; then printf "%s\\n" "--model"; exit 0; fi\n'
    )
    fake.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "DOTFILES_RUN_COPILOT_SETUP": "1",
        "DOTFILES_USE_LITELLM_PROXY": "1",
        "DOTFILES_RUN_LITELLM_SETUP": "1",
        "DOTFILES_COPILOT_LITELLM_MODEL": "alias",
        "LITELLM_COPILOT_CLI_KEY": "different",
    }
    result = subprocess.run([str(launcher)], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "conflicts" in result.stderr


@pytest.mark.parametrize(
    "gate",
    [
        "DOTFILES_RUN_COPILOT_SETUP",
        "DOTFILES_USE_LITELLM_PROXY",
        "DOTFILES_RUN_LITELLM_SETUP",
    ],
)
def test_copilot_litellm_requires_both_gates(gate, tmp_path):
    launcher = Path(__file__).resolve().parents[2] / "copilot-litellm"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "copilot"
    fake.write_text('#!/bin/sh\n[ "$1" = "--help" ] && exit 0\n')
    fake.chmod(0o755)
    result = subprocess.run(
        [str(launcher)],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), gate: "1"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "gate" in result.stderr


def test_copilot_litellm_dry_run_needs_no_key_and_rejects_model_override(tmp_path):
    launcher = Path(__file__).resolve().parents[2] / "copilot-litellm"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    marker = tmp_path / "child-ran"
    child = binaries / "copilot"
    child.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    child.chmod(0o700)
    env = {
        "PATH": f"{binaries}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "DOTFILES_RUN_COPILOT_SETUP": "1",
        "DOTFILES_USE_LITELLM_PROXY": "1",
        "DOTFILES_RUN_LITELLM_SETUP": "1",
        "DOTFILES_COPILOT_LITELLM_MODEL": "model-alias",
    }
    result = subprocess.run(
        [str(launcher), "--dry-run"], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "credential state UNKNOWN" in result.stdout
    assert not marker.exists()
    assert not (tmp_path / ".local/share/litellm/clients").exists()
    result = subprocess.run(
        [str(launcher), "--model=other"], env=env, capture_output=True, text=True
    )
    assert result.returncode == 2


def test_copilot_litellm_dry_run_requires_cli(tmp_path):
    launcher = Path(__file__).resolve().parents[2] / "copilot-litellm"
    result = subprocess.run(
        [str(launcher), "--dry-run"],
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "DOTFILES_RUN_COPILOT_SETUP": "1",
            "DOTFILES_USE_LITELLM_PROXY": "1",
            "DOTFILES_RUN_LITELLM_SETUP": "1",
            "DOTFILES_COPILOT_LITELLM_MODEL": "model-alias",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "copilot CLI not found" in result.stderr
    assert not (tmp_path / ".local").exists()


@pytest.mark.parametrize(
    "config_kind",
    ["default-file", "default-directory", "default-symlink", "broken-symlink"],
)
def test_copilot_litellm_refuses_default_provider_config_before_key_read(
    tmp_path, config_kind
):
    launcher = Path(__file__).resolve().parents[2] / "copilot-litellm"
    home = tmp_path / "home"
    providers = home / ".copilot/providers.json"
    providers.parent.mkdir(parents=True)
    if config_kind == "default-directory":
        providers.mkdir()
    elif config_kind == "default-symlink":
        target = tmp_path / "provider-target"
        target.write_text("not inspected")
        providers.symlink_to(target)
    elif config_kind == "broken-symlink":
        providers.symlink_to(tmp_path / "missing-provider-target")
    else:
        providers.write_text("not inspected")
    result = subprocess.run(
        [str(launcher), "--dry-run"],
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "DOTFILES_RUN_COPILOT_SETUP": "1",
            "DOTFILES_USE_LITELLM_PROXY": "1",
            "DOTFILES_RUN_LITELLM_SETUP": "1",
            "DOTFILES_COPILOT_LITELLM_MODEL": "alias",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "providers.json" in result.stderr
    assert "not inspected" not in result.stderr
    assert not (home / ".local/share/litellm/clients").exists()


@pytest.mark.parametrize("override", ["", "/custom/providers.json"])
def test_copilot_litellm_refuses_provider_config_override(tmp_path, override):
    launcher = Path(__file__).resolve().parents[2] / "copilot-litellm"
    result = subprocess.run(
        [str(launcher), "--dry-run"],
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "COPILOT_PROVIDERS_CONFIG": override,
            "DOTFILES_RUN_COPILOT_SETUP": "1",
            "DOTFILES_USE_LITELLM_PROXY": "1",
            "DOTFILES_RUN_LITELLM_SETUP": "1",
            "DOTFILES_COPILOT_LITELLM_MODEL": "alias",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "COPILOT_PROVIDERS_CONFIG" in result.stderr


@pytest.mark.parametrize("home_override", ["", "alternate"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_copilot_litellm_refuses_copilot_home_override(
    home_override, dry_run, tmp_path
):
    launcher = Path(__file__).resolve().parents[2] / "copilot-litellm"
    alternate_home = tmp_path / "alternate/copilot-home"
    alternate_config = alternate_home / "providers.json"
    alternate_config.parent.mkdir(parents=True)
    alternate_config.write_text('{"secret":"must-not-leak"}')
    marker = tmp_path / "child-ran"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "copilot"
    fake.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    fake.chmod(0o755)
    result = subprocess.run(
        [str(launcher), *(["--dry-run"] if dry_run else [])],
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "COPILOT_HOME": str(alternate_home) if home_override else "",
            "DOTFILES_RUN_COPILOT_SETUP": "1",
            "DOTFILES_USE_LITELLM_PROXY": "1",
            "DOTFILES_RUN_LITELLM_SETUP": "1",
            "DOTFILES_COPILOT_LITELLM_MODEL": "alias",
            "CHILD_MARKER": str(marker),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "COPILOT_HOME" in result.stderr
    if home_override:
        assert home_override not in result.stderr
    assert "must-not-leak" not in result.stderr + result.stdout
    assert alternate_config.read_text() == '{"secret":"must-not-leak"}'
    assert not marker.exists()


def test_copilot_preview_checks_nonsecret_inputs_without_running_child(tmp_path):
    launcher = Path(__file__).resolve().parents[2] / "copilot-litellm"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    marker = tmp_path / "child-ran"
    child = binaries / "copilot"
    child.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    child.chmod(0o700)
    env = {
        "HOME": str(tmp_path),
        "PATH": f"{binaries}:/usr/bin:/bin",
        "DOTFILES_RUN_COPILOT_SETUP": "1",
        "DOTFILES_USE_LITELLM_PROXY": "1",
        "DOTFILES_RUN_LITELLM_SETUP": "1",
        "DOTFILES_COPILOT_LITELLM_MODEL": "fixture/model",
    }
    good = subprocess.run(
        [str(launcher), "--dry-run"], env=env, capture_output=True, text=True
    )
    assert good.returncode == 0, good.stderr
    assert "credential state UNKNOWN" in good.stdout
    for name, value in (
        ("DOTFILES_RUN_COPILOT_SETUP", "0"),
        ("DOTFILES_USE_LITELLM_PROXY", "0"),
        ("DOTFILES_RUN_LITELLM_SETUP", "0"),
        ("DOTFILES_COPILOT_LITELLM_MODEL", ""),
        ("DOTFILES_COPILOT_LITELLM_BASE_URL", "https://untrusted.example"),
    ):
        bad = subprocess.run(
            [str(launcher), "--dry-run"],
            env={**env, name: value},
            capture_output=True,
            text=True,
        )
        assert bad.returncode == 1
        assert "are valid" not in bad.stdout
    assert not marker.exists()
    assert not (tmp_path / ".local").exists()


def test_copilot_litellm_refuses_unsearchable_provider_directory(tmp_path):
    launcher = Path(__file__).resolve().parents[2] / "copilot-litellm"
    home = tmp_path / "home"
    copilot_dir = home / ".copilot"
    copilot_dir.mkdir(parents=True)
    copilot_dir.chmod(0)
    try:
        result = subprocess.run(
            [str(launcher), "--dry-run"],
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(home),
                "DOTFILES_RUN_COPILOT_SETUP": "1",
                "DOTFILES_USE_LITELLM_PROXY": "1",
                "DOTFILES_RUN_LITELLM_SETUP": "1",
                "DOTFILES_COPILOT_LITELLM_MODEL": "alias",
            },
            capture_output=True,
            text=True,
        )
    finally:
        copilot_dir.chmod(0o700)
    assert result.returncode == 1
    assert "cannot establish" in result.stderr


def test_model_catalogue_fetch_uses_45_second_deadline_and_timeout_is_unknown(
    monkeypatch,
):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"data": []}'

    def open_stub(request, *, timeout):
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(model_catalogues, "open_same_origin", open_stub)
    model_catalogues.get_catalogue("http://127.0.0.1:4000/v1/models")
    assert seen["timeout"] == 45

    monkeypatch.setattr(
        model_catalogues,
        "get_catalogue",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()),
    )
    stats = {"checked": 0, "skipped": 0}
    assert (
        model_catalogues.get_models("http://127.0.0.1:4000/v1/models", stats=stats)
        is None
    )
    assert stats == {"checked": 0, "skipped": 1}


def test_app_key_provisioning_defers_on_unexpected_generate_errors(
    tmp_path, monkeypatch
):
    def fake_request(url, method, master_key, payload=None, timeout=5):
        if "/key/list" in url:
            return {"keys": []}
        if url.endswith("/key/generate"):
            raise urllib.error.HTTPError(url, 503, "Service Unavailable", None, None)
        return {"status": "healthy"}

    monkeypatch.setattr(CONFIGURE, "_request_json", fake_request)
    path = tmp_path / "service.env"
    path.write_text("LITELLM_MASTER_KEY='master'\n", encoding="utf-8")

    # Returns cleanly (the outer handler warns and defers), does not raise.
    CONFIGURE.provision_app_keys("master", path, "http://127.0.0.1:4000")
    assert not any(
        name in path.read_text(encoding="utf-8") for name in CONFIGURE.APP_KEYS.values()
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/unexpected",
        "https://127.0.0.1:4000/key/list",
        "http://example.test:4000/key/list",
    ],
)
def test_litellm_provisioning_rejects_non_loopback_or_non_http_urls(url, monkeypatch):
    monkeypatch.setattr(
        CONFIGURE.urllib.request,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("unsafe URL must not be opened"),
    )
    with pytest.raises(ValueError, match="restricted to loopback HTTP"):
        CONFIGURE._request_json(url, "GET", "master")


def test_render_uses_environment_references_and_no_inline_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-render")
    rendered = litellm_config.render_config()
    assert "do-not-render" not in rendered
    assert "api_key: os.environ/OPENAI_API_KEY" not in rendered
    assert "master_key: os.environ/LITELLM_MASTER_KEY" in rendered
    assert "database_url: os.environ/DATABASE_URL" in rendered


def test_render_is_idempotent(tmp_path):
    path = tmp_path / "config.yaml"
    assert litellm_config.write_config(path, {}) is True
    assert litellm_config.write_config(path, {}) is False


def test_litellm_service_missing_plist_fails_hermetically(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    script = (
        f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
        f"source {shlex.quote(str(helper))}; litellm_service_start"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True)
    assert result.returncode == 1


def test_litellm_env_sync_in_sparse_environment(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    env_path = tmp_path / "litellm" / "service.env"
    script = (
        f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
        f"source {shlex.quote(str(helper))}; "
        f"litellm_service_env_sync {shlex.quote(str(env_path))}"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env={"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")},
        capture_output=True,
    )
    assert result.returncode == 0
    assert env_path.is_file()
    assert env_path.stat().st_mode & 0o777 == 0o600
    env_text = env_path.read_text(encoding="utf-8")
    names = {line.split("=", 1)[0] for line in env_text.splitlines() if "=" in line}
    assert "DISABLE_ADMIN_UI" in names


def test_litellm_env_sync_preserves_service_managed_app_keys(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    env_path = tmp_path / "litellm" / "service.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text(
        "LITELLM_MASTER_KEY='sk-test-master-key'\n"
        "LITELLM_OPENCODE_KEY='sk-open-code'\n"
        "LITELLM_PI_KEY='sk-pi'\n"
        "LITELLM_OPENWEBUI_KEY='sk-webui'\n"
        "LITELLM_JUNIE_KEY='sk-junie'\n",
        encoding="utf-8",
    )
    script = (
        f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
        f"source {shlex.quote(str(helper))}; "
        f"litellm_service_env_sync {shlex.quote(str(env_path))}"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0
    values = dict(
        line.split("=", 1)
        for line in env_path.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    assert values["LITELLM_OPENCODE_KEY"] == "sk-open-code"
    assert values["LITELLM_PI_KEY"] == "sk-pi"
    assert values["LITELLM_OPENWEBUI_KEY"] == "sk-webui"
    assert values["LITELLM_JUNIE_KEY"] == "sk-junie"
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_litellm_env_sync_disable_admin_ui_override(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    env_path = tmp_path / "litellm" / "service.env"
    env_path.parent.mkdir(parents=True)

    def sync(extra_env, target):
        script = (
            f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
            f"source {shlex.quote(str(helper))}; "
            f"litellm_service_env_sync {shlex.quote(str(env_path))}"
        )
        result = subprocess.run(
            ["bash", "-c", script],
            env={
                "HOME": str(tmp_path),
                "PATH": os.environ.get("PATH", ""),
                **extra_env,
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        return dict(
            line.split("=", 1)
            for line in env_path.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )

    assert sync({}, None)["DISABLE_ADMIN_UI"] == "True"
    assert (
        sync({"LITELLM_DISABLE_ADMIN_UI": "False"}, None)["DISABLE_ADMIN_UI"] == "False"
    )
    assert sync({}, None)["DISABLE_ADMIN_UI"] == "True"


def test_litellm_env_sync_database_url_precedence(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    env_path = tmp_path / "litellm" / "service.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("DATABASE_URL='persisted'\n", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL='legacy'\nLITELLM_DATABASE_URL='new'\n", encoding="utf-8"
    )
    script = (
        f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
        f"source {shlex.quote(str(helper))}; "
        f"litellm_service_env_sync {shlex.quote(str(env_path))}"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env={"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "DATABASE_URL=new" in env_path.read_text(encoding="utf-8")


def test_litellm_env_sync_removes_missing_or_empty_provider_keys(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    root = tmp_path / "litellm"
    root.mkdir()
    env_path = root / "service.env"
    config_path = root / "config.yaml"
    config_path.write_text("api_key: os.environ/COHERE_API_KEY\n", encoding="utf-8")

    def sync(home_env):
        env_path.write_text("COHERE_API_KEY=stale\n", encoding="utf-8")
        (tmp_path / ".env").write_text(home_env, encoding="utf-8")
        script = (
            f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
            f"source {shlex.quote(str(helper))}; "
            f"litellm_service_env_sync {shlex.quote(str(env_path))}"
        )
        result = subprocess.run(
            ["bash", "-c", script],
            env={"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return env_path.read_text(encoding="utf-8")

    assert "COHERE_API_KEY=" not in sync("")
    assert "COHERE_API_KEY=" not in sync("COHERE_API_KEY=''\n")


def test_litellm_env_sync_oauth_cache_overrides_remove_and_restore(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    root = tmp_path / "litellm"
    root.mkdir()
    env_path = root / "service.env"
    env_file = tmp_path / ".env"
    overrides = {
        "CHATGPT_TOKEN_DIR": "/custom/chatgpt",
        "CHATGPT_AUTH_FILE": "custom-auth.json",
        "GITHUB_COPILOT_TOKEN_DIR": "/custom/copilot",
        "GITHUB_COPILOT_ACCESS_TOKEN_FILE": "custom-access.json",
        "GITHUB_COPILOT_API_KEY_FILE": "custom-api.json",
    }
    env_file.write_text(
        "LITELLM_MASTER_KEY='sk-test-master-key'\n"
        + "".join(f"{key}='{value}'\n" for key, value in overrides.items()),
        encoding="utf-8",
    )
    script = (
        f"""\
source {shlex.quote(str(helper))}
litellm_service_env_sync {shlex.quote(str(env_path))} || exit 1
"""
        + "\n".join(
            f"grep -Fqx {shlex.quote(f'{key}={value}')} {shlex.quote(str(env_path))} || exit 1"
            for key, value in overrides.items()
        )
        + f"""
: > {shlex.quote(str(env_file))}
litellm_service_env_sync {shlex.quote(str(env_path))} || exit 1
"""
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env={"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    service_values = dict(
        line.split("=", 1) for line in env_path.read_text(encoding="utf-8").splitlines()
    )
    assert not set(overrides).intersection(service_values)


def test_litellm_env_sync_removes_provider_from_same_shell_scope(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    root = tmp_path / "litellm"
    root.mkdir()
    env_path = root / "service.env"
    (root / "config.yaml").write_text(
        "api_key: os.environ/COHERE_API_KEY\n", encoding="utf-8"
    )
    (tmp_path / ".env").write_text("COHERE_API_KEY='cohere-test'\n", encoding="utf-8")
    script = f"""\
source {shlex.quote(str(helper))}
litellm_service_env_sync {shlex.quote(str(env_path))} || exit 1
if ! grep -q '^COHERE_API_KEY=cohere-test$' {shlex.quote(str(env_path))}; then
  printf 'first sync did not persist expected credential\n' >&2
  exit 1
fi
: > {shlex.quote(str(tmp_path / ".env"))}
litellm_service_env_sync {shlex.quote(str(env_path))} || exit 1
if [[ -n "${{COHERE_API_KEY:-}}" ]]; then
  printf 'provider credential leaked across sync calls\n' >&2
  exit 1
fi
"""
    result = subprocess.run(
        ["bash", "-c", script],
        env={"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_litellm_env_sync_local_only_config_does_not_copy_bootstrap_keys(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    root = tmp_path / "litellm"
    root.mkdir()
    env_path = root / "service.env"
    (root / "config.yaml").write_text("model: local/model\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY='bootstrap-secret'\n", encoding="utf-8"
    )
    script = (
        f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
        f"source {shlex.quote(str(helper))}; "
        f"litellm_service_env_sync {shlex.quote(str(env_path))}"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env={"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "OPENAI_API_KEY" not in env_path.read_text(encoding="utf-8")


def test_litellm_env_sync_is_byte_identical_on_repeated_runs(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    root = tmp_path / "litellm"
    root.mkdir()
    env_path = root / "service.env"
    (root / "config.yaml").write_text(
        "api_key: os.environ/COHERE_API_KEY\n", encoding="utf-8"
    )
    (tmp_path / ".env").write_text("COHERE_API_KEY='cohere-test'\n", encoding="utf-8")
    script = (
        f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
        f"source {shlex.quote(str(helper))}; "
        f"litellm_service_env_sync {shlex.quote(str(env_path))}"
    )
    first = None
    for run in range(2):
        result = subprocess.run(
            ["bash", "-c", script],
            env={"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        if run == 0:
            first = env_path.read_bytes()
        else:
            assert env_path.read_bytes() == first


def test_litellm_env_sync_database_url_legacy_precedes_persisted(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    env_path = tmp_path / "litellm" / "service.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("DATABASE_URL='persisted'\n", encoding="utf-8")
    (tmp_path / ".env").write_text("DATABASE_URL='legacy'\n", encoding="utf-8")
    script = (
        f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
        f"source {shlex.quote(str(helper))}; "
        f"litellm_service_env_sync {shlex.quote(str(env_path))}"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env={"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "DATABASE_URL=legacy" in env_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("openssl_body", ["return 1", ":"])
def test_litellm_master_key_generation_rejects_failure_or_empty(tmp_path, openssl_body):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    env_path = tmp_path / "service.env"
    script = (
        f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
        f"openssl() {{ {openssl_body}; }}; "
        f"source {shlex.quote(str(helper))}; "
        f"litellm_service_env_sync {shlex.quote(str(env_path))}"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True)
    assert result.returncode == 1
    assert not env_path.exists()


def test_litellm_keyed_local_and_meridian_shapes(monkeypatch):
    monkeypatch.setattr(litellm_config, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(
        litellm_config, "iter_engine_models", lambda _: [{"name": "local"}]
    )
    monkeypatch.setattr(
        litellm_config,
        "local_endpoint_for",
        lambda *_: ("http://local/v1", "OMLX_API_KEY"),
    )
    monkeypatch.setattr(litellm_config, "is_meridian_configured", lambda: True)
    monkeypatch.setenv("MERIDIAN_API_KEY", "meridian")
    entries = litellm_config.compute_model_list()
    assert (
        any(
            item["litellm_params"].get("api_key") == "os.environ/OMLX_API_KEY"
            for item in entries
        )
        is False
    )
    monkeypatch.setenv("OMLX_API_KEY", "omlx")
    keyed = litellm_config.compute_model_list()
    keyed_by_name = {item["model_name"]: item for item in keyed}
    assert keyed_by_name["omlx/local"]["litellm_params"]["api_key"] == (
        "os.environ/OMLX_API_KEY"
    )
    assert (
        keyed_by_name["local"]["litellm_params"]
        == keyed_by_name["omlx/local"]["litellm_params"]
    )
    assert any(
        item["litellm_params"].get("api_key") == "os.environ/OMLX_API_KEY"
        for item in keyed
    )
    assert any(item["model_name"] == "meridian/claude-sonnet-5-5" for item in keyed)


def test_google_uses_native_adapter(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini")
    monkeypatch.setattr(
        litellm_config, "_live_catalogue", lambda *args: ["gemini-3.8-flash"]
    )
    entries = litellm_config.compute_model_list()
    google = next(
        item
        for item in entries
        if item["model_name"] == "google/models/gemini-3.8-flash"
    )
    assert google["litellm_params"]["model"].startswith("gemini/")
    # Native gemini/ adapter must NOT receive the OpenAI-compatible
    # generativelanguage /v1beta/openai base URL (vertex_llm_base.py builds
    # .../openai/models/<id>:generateContent from it — wrong path for the
    # native adapter). api_base is omitted so LiteLLM uses its native
    # GenerativeLanguage defaults.
    assert not google["litellm_params"].get("api_base")


@pytest.mark.parametrize(
    "env_name, provider, model, api_base",
    [
        (
            "OPENROUTER_API_KEY",
            "openrouter",
            "openrouter/test-model",
            "https://openrouter.ai/api/v1",
        ),
        (
            "OPENCODE_API_KEY",
            "opencode",
            "openai/big-pickle",
            "https://opencode.ai/zen/v1",
        ),
        (
            "OLLAMA_API_KEY",
            "ollama-cloud",
            "openai/test-model",
            "https://ollama.com/v1",
        ),
    ],
)
def test_cloud_provider_routes_use_recorded_upstreams(
    monkeypatch, env_name, provider, model, api_base
):
    # Split-concat keeps the literal out of trufflehog Lob's key-shaped string
    # matching (precedent: test_litellm.py's sk- stub fixtures).
    monkeypatch.setenv(env_name, "test-" + "key")
    model_id = model.split("/", 1)[-1]
    monkeypatch.setattr(litellm_config, "_live_catalogue", lambda *args: [model_id])
    alias = f"{provider}/default"
    if provider == "opencode":
        alias = "opencode/big-pickle"
    else:
        alias = f"{provider}/{model_id}"
    entry = next(
        item
        for item in litellm_config.compute_model_list(
            {
                # The provider key must ride the env-dict: compute_model_list
                # reads only the dict it is given, not os.environ. Full scope
                # keeps the stub id emitted even though it is not a curated
                # registry selection.
                "DOTFILES_LITELLM_CATALOGUE_SCOPE": "full",
                env_name: "test-key",
            }
        )
        if item["model_name"] == alias
    )
    params = entry["litellm_params"]
    expected_wire = f"openai/{model_id}" if provider == "openrouter" else model
    assert params["model"] == expected_wire
    if provider == "cerebras":
        assert "api_base" not in params
    else:
        assert params["api_base"] == api_base
    assert params["api_key"] == f"os.environ/{env_name}"


@pytest.mark.parametrize(
    "entry",
    [
        {
            "model_name": "bad",
            "litellm_params": {"api_base": "http://127.0.0.1:4000/v1"},
        },
        {"model_name": "bad", "litellm_params": {"api_base": "~/.mozart/mozart.json"}},
        {"model_name": "bad", "litellm_params": {"model": "mozart-router/default"}},
    ],
)
def test_render_rejects_mozart_and_self_routing(entry):
    with pytest.raises(litellm_config.RoutingInvariantError):
        litellm_config.render_config(entries=[entry])


@pytest.mark.parametrize(
    "entry",
    [
        {
            "model_name": "openai/wolf.mozart-v1",
            "litellm_params": {"model": "openai/wolf.mozart-v1"},
        },
        {
            "model_name": "alias-mozart-router-v1",
            "litellm_params": {"model": "openai/ordinary-model"},
        },
    ],
)
def test_render_allows_legitimate_substrings(entry):
    assert "model_list:" in litellm_config.render_config(entries=[entry])


def test_generator_uses_litellm_port_override():
    entry = {
        "model_name": "bad",
        "litellm_params": {"api_base": "http://127.0.0.1:4100/v1"},
    }
    with pytest.raises(litellm_config.RoutingInvariantError):
        litellm_config.render_config(environ={"LITELLM_PORT": "4100"}, entries=[entry])


def test_mozart_template_has_no_litellm_gateway():
    template = Path(__file__).resolve().parents[3] / "configs/mozart-router/mozart.json"
    config = json.loads(template.read_text(encoding="utf-8"))
    assert all(
        "127.0.0.1:4000" not in json.dumps(gateway)
        for gateway in config.get("gateways", {}).values()
    )


def _fake_prisma_venv(tmp_path):
    venv = tmp_path / "venv"
    bin_dir = venv / "bin"
    bin_dir.mkdir(parents=True)
    schema = venv / "lib/python3.12/site-packages/litellm_proxy_extras/schema.prisma"
    schema.parent.mkdir(parents=True)
    schema.write_text("// fixture schema\n", encoding="utf-8")
    ready = tmp_path / "prisma-client-ready"
    calls = tmp_path / "prisma-generate-calls"
    python = bin_dir / "python"
    python.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'if [[ "$*" == *"site.getsitepackages"* ]]; then\n'
        '  printf "%s\\n" "$FAKE_PRISMA_SCHEMA"\n'
        "  exit 0\n"
        "fi\n"
        'if [[ "$*" == *"from prisma import Prisma"* ]]; then\n'
        '  [[ -f "$FAKE_PRISMA_READY" ]] && exit 0\n'
        "  exit 1\n"
        "fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    prisma = bin_dir / "prisma"
    prisma.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'case ":$PATH:" in *":$FAKE_PRISMA_BIN:"*) ;; *) exit 3 ;; esac\n'
        'if [[ "$1" == "format" && "$2" == "--schema" && -f "$3" ]]; then\n'
        "  exit 0\n"
        "fi\n"
        '[[ "$1" == "generate" && "$2" == "--schema" && "$3" == "$FAKE_PRISMA_SCHEMA" ]] || exit 2\n'
        'printf "%s\\n" called >> "$FAKE_PRISMA_CALLS"\n'
        'if [[ "${FAKE_PRISMA_MODE:-success}" == "success" ]]; then\n'
        '  touch "$FAKE_PRISMA_READY"\n'
        '  mkdir -p "$(dirname "$FAKE_PRISMA_PACKAGED")"\n'
        '  cp "$FAKE_PRISMA_SCHEMA" "$FAKE_PRISMA_PACKAGED"\n'
        "fi\n"
        '[[ "${FAKE_PRISMA_MODE:-success}" != "fail" ]]\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    prisma.chmod(0o755)
    return venv, schema, ready, calls


def _run_prisma_guard(venv, schema, ready, calls, **overrides):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "FAKE_PRISMA_SCHEMA": str(schema),
        "FAKE_PRISMA_READY": str(ready),
        "FAKE_PRISMA_CALLS": str(calls),
        "FAKE_PRISMA_BIN": str(venv / "bin"),
        "FAKE_PRISMA_PACKAGED": str(schema.parent.parent / "prisma/schema.prisma"),
        **overrides,
    }
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; litellm_ensure_prisma_client "$2"',
            "prisma-test",
            str(helper),
            str(venv),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_litellm_prisma_guard_generates_missing_client_once(tmp_path):
    venv, schema, ready, calls = _fake_prisma_venv(tmp_path)
    first = _run_prisma_guard(venv, schema, ready, calls)
    second = _run_prisma_guard(venv, schema, ready, calls)

    assert first.returncode == 0
    assert second.returncode == 0
    assert ready.is_file()
    assert calls.read_text(encoding="utf-8").splitlines() == ["called"]


def test_litellm_prisma_guard_regenerates_stale_importable_client(tmp_path):
    venv, schema, ready, calls = _fake_prisma_venv(tmp_path)
    packaged = schema.parent.parent / "prisma/schema.prisma"
    packaged.parent.mkdir()
    packaged.write_text("// older generated schema\n", encoding="utf-8")
    ready.touch()

    result = _run_prisma_guard(venv, schema, ready, calls)

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == ["called"]
    assert packaged.read_text(encoding="utf-8") == schema.read_text(encoding="utf-8")


def test_litellm_prisma_guard_fails_if_generation_does_not_create_client(tmp_path):
    venv, schema, ready, calls = _fake_prisma_venv(tmp_path)
    result = _run_prisma_guard(
        venv, schema, ready, calls, FAKE_PRISMA_MODE="incomplete"
    )

    assert result.returncode != 0
    assert "client remains unavailable" in result.stderr
    assert not ready.exists()


def test_litellm_prisma_guard_fails_if_schema_is_missing(tmp_path):
    venv, schema, ready, calls = _fake_prisma_venv(tmp_path)
    schema.unlink()

    result = _run_prisma_guard(venv, schema, ready, calls)

    assert result.returncode != 0
    assert "schema is unavailable" in result.stderr
    assert not calls.exists()


def test_litellm_template_prepares_prisma_before_service_start():
    template = (
        Path(__file__).resolve().parents[3]
        / ".chezmoiscripts/run_onchange_15-litellm.sh.tmpl"
    ).read_text(encoding="utf-8")
    prepare = template.index('litellm_ensure_prisma_client "$VENV"')
    assert prepare < template.index("systemctl --user enable --now litellm.service")
    assert prepare < template.index("litellm_service_start")


def test_litellm_env_sync_local_model_cost_map_rule(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "litellm_service.sh"
    env_path = tmp_path / "litellm" / "service.env"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL='postgresql://u:pw@127.0.0.1:5432/litellm'\n"
        "LITELLM_MASTER_KEY='sk-test-master-key-0123456789'\n",
        encoding="utf-8",
    )
    script = (
        f"HOME={shlex.quote(str(tmp_path))}; export HOME; "
        f"source {shlex.quote(str(helper))}; "
        f"litellm_service_env_sync {shlex.quote(str(env_path))}"
    )
    subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    lines = dict(
        line.split("=", 1)
        for line in env_path.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    # Repo-managed default: never fetch the remote cost map in the scrubbed
    # launchd environment (single worker stalls on the remote fetch).
    assert lines["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"

    # User override passes through.
    env_file.write_text(
        env_file.read_text(encoding="utf-8") + "LITELLM_LOCAL_MODEL_COST_MAP='False'\n",
        encoding="utf-8",
    )
    subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    lines = dict(
        line.split("=", 1)
        for line in env_path.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    assert lines["LITELLM_LOCAL_MODEL_COST_MAP"] == "False"


def test_google_catalogue_paginates_and_collects_union(monkeypatch):
    pages = [
        {
            "data": [{"id": "gemini-3.5-flash"}, {"id": "a-first-id"}],
            "nextPageToken": "tok-2",
        },
        {"data": [{"id": "zebra-last"}]},
    ]
    urls = []

    def response(request, timeout):
        urls.append(request.full_url)
        return io.BytesIO(json.dumps(pages.pop(0)).encode())

    monkeypatch.setattr(litellm_config, "open_same_origin", response)
    monkeypatch.setattr(litellm_config, "_live_catalogue", LIVE_CATALOGUE)
    result = litellm_config._live_catalogue(
        "google", "test-key", "https://generativelanguage.googleapis.com"
    )
    assert result == ["a-first-id", "gemini-3.5-flash", "zebra-last"]
    # The OpenAI-compatible endpoint rejects pageSize; page 1 is fetched bare.
    assert "pageSize=" not in urls[0]
    assert "pageToken=tok-2" in urls[1]


def test_google_catalogue_uses_bearer_auth_header(monkeypatch):
    headers_seen = []

    def response(request, timeout):
        headers_seen.append(request.headers)
        return io.BytesIO(json.dumps({"data": [{"id": "x"}]}).encode())

    monkeypatch.setattr(litellm_config, "open_same_origin", response)
    monkeypatch.setattr(litellm_config, "_live_catalogue", LIVE_CATALOGUE)
    litellm_config._live_catalogue(
        "google", "test-key", "https://generativelanguage.googleapis.com"
    )
    assert headers_seen[0].get("Authorization") == "Bearer test-key"


def test_malformed_google_catalogue_yields_note_and_no_entries(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        litellm_config,
        "open_same_origin",
        lambda *args, **kwargs: io.BytesIO(b'{"wrong": []}'),
    )
    monkeypatch.setattr(litellm_config, "_live_catalogue", LIVE_CATALOGUE)
    entries = litellm_config.compute_model_list()
    assert not any(item["model_name"].startswith("google/models/") for item in entries)
    assert any(
        "google: UNKNOWN" in note for note in litellm_config.last_generation_notes
    )


@pytest.mark.parametrize(
    "payloads,cap",
    [
        ([{"data": [], "nextPageToken": "repeat"}] * 2, 10),
        ([{"data": [], "nextPageToken": f"tok-{i}"} for i in range(2)], 2),
    ],
)
def test_google_catalogue_rejects_repeated_token_and_page_cap(
    monkeypatch, payloads, cap
):
    pages = list(payloads)
    monkeypatch.setattr(litellm_config, "GOOGLE_CATALOGUE_MAX_PAGES", cap)
    monkeypatch.setattr(
        litellm_config,
        "open_same_origin",
        lambda *args, **kwargs: io.BytesIO(json.dumps(pages.pop(0)).encode()),
    )
    monkeypatch.setattr(litellm_config, "_live_catalogue", LIVE_CATALOGUE)
    with pytest.raises(litellm_config.LiveCatalogueError):
        litellm_config._live_catalogue(
            "google", "key", "https://generativelanguage.googleapis.com"
        )


def test_google_catalogue_rejects_non_string_nextpagetoken(monkeypatch):
    monkeypatch.setattr(
        litellm_config,
        "open_same_origin",
        lambda *args, **kwargs: io.BytesIO(b'{"data": [], "nextPageToken": 7}'),
    )
    monkeypatch.setattr(litellm_config, "_live_catalogue", LIVE_CATALOGUE)
    with pytest.raises(litellm_config.LiveCatalogueError, match="must be a string"):
        litellm_config._live_catalogue(
            "google", "key", "https://generativelanguage.googleapis.com"
        )


def test_google_coverage_canonicalizes_native_catalogue_ids(monkeypatch):
    requested = sorted(
        ref.split("/", 1)[1]
        for ref in litellm_config._registry_model_refs()
        if ref.startswith("google/")
    )
    assert requested
    model_id = requested[0]
    # Native Google catalogue shape returns ids with the "models/" prefix;
    # coverage comparison must canonicalize before subtracting.
    monkeypatch.setattr(
        litellm_config,
        "_live_catalogue",
        lambda provider, *args: (
            [f"models/{model_id}"] if provider == "google" else []
        ),
    )
    litellm_config.compute_model_list()
    assert not any(
        f"google/{model_id}: UNKNOWN" in note
        for note in litellm_config.last_generation_notes
    )


def test_openrouter_pricing_sidecar_captured_from_catalogue(monkeypatch):
    payload = {
        "data": [
            {"id": "free-one:free", "pricing": {"prompt": "0", "completion": "0"}},
            {
                "id": "paid-one",
                "pricing": {"prompt": "0.000001", "completion": "0.0000027"},
            },
            {"id": "no-pricing"},
        ]
    }
    calls = []

    def response(request, timeout):
        calls.append(request.full_url)
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(litellm_config, "open_same_origin", response)
    # Re-bind the real fetcher: the autouse hermetic fixture stubs it first.
    monkeypatch.setattr(litellm_config, "_live_catalogue", LIVE_CATALOGUE)
    result = litellm_config._live_catalogue(
        "openrouter", "key", "https://openrouter.ai/api/v1"
    )
    assert result == ["free-one:free", "no-pricing", "paid-one"]
    assert litellm_config._LIVE_CATALOGUE_PRICES["openrouter"] == {
        "free-one:free": ("0", "0"),
        "paid-one": ("0.000001", "0.0000027"),
    }


def test_openrouter_rows_price_bare_and_free_aliases(monkeypatch):
    monkeypatch.setattr(
        litellm_config, "_registry_model_refs", lambda: {"openrouter/model-a:free"}
    )
    payload = {
        "data": [{"id": "model-a:free", "pricing": {"prompt": "0", "completion": "0"}}]
    }
    monkeypatch.setattr(
        litellm_config,
        "open_same_origin",
        lambda *args, **kwargs: io.BytesIO(json.dumps(payload).encode()),
    )
    monkeypatch.setattr(litellm_config, "_live_catalogue", LIVE_CATALOGUE)
    entries = litellm_config.compute_model_list({"OPENROUTER_API_KEY": "test-" + "key"})
    aliases = {item["model_name"]: item for item in entries}
    # Free scope advertises the upstream ':free' spelling; rates come from
    # the sidecar keyed on that same raw id.
    assert aliases["openrouter/model-a:free"]["litellm_params"]["model_info"] == {
        "input_cost_per_token": 0.0,
        "output_cost_per_token": 0.0,
    }


def test_openrouter_malformed_pricing_leaves_rows_unpriced(monkeypatch):
    monkeypatch.setattr(
        litellm_config, "_registry_model_refs", lambda: {"openrouter/busted"}
    )
    payload = {
        "data": [
            {"id": "busted", "pricing": {"prompt": "not-a-number", "completion": "0"}}
        ]
    }
    monkeypatch.setattr(
        litellm_config,
        "open_same_origin",
        lambda *args, **kwargs: io.BytesIO(json.dumps(payload).encode()),
    )
    monkeypatch.setattr(litellm_config, "_live_catalogue", LIVE_CATALOGUE)
    entries = litellm_config.compute_model_list({"OPENROUTER_API_KEY": "test-" + "key"})
    assert entries and all(
        "model_info" not in item["litellm_params"] for item in entries
    )


def test_non_openrouter_rows_never_carry_pricing(monkeypatch):
    monkeypatch.setattr(litellm_config, "_registry_model_refs", lambda: set())
    monkeypatch.setattr(
        litellm_config,
        "_live_catalogue",
        lambda *args: ["gpt-5.5"] if args[0] == "openai" else [],
    )
    litellm_config._LIVE_CATALOGUE_PRICES["openai"] = {"gpt-5.5": ("0", "0")}
    try:
        entries = litellm_config.compute_model_list(
            {"DOTFILES_LITELLM_CATALOGUE_SCOPE": "full", "OPENAI_API_KEY": "test-key"}
        )
    finally:
        litellm_config._LIVE_CATALOGUE_PRICES.clear()
    assert entries and all(
        "model_info" not in item["litellm_params"] for item in entries
    )


def _seed_builtin_cost_map(monkeypatch, fixture):
    monkeypatch.setattr(litellm_config, "_BUILTIN_COST_MAP_LOADED", True)
    monkeypatch.setattr(litellm_config, "BUILTIN_COST_MAP", fixture)


CLAUDE_FIXTURE_ENTRY = {
    "input_cost_per_token": 2e-06,
    "output_cost_per_token": 1e-05,
    "cache_read_input_token_cost": 1e-07,
    "max_input_tokens": 1000000,
    "max_output_tokens": 128000,
    "mode": "chat",
}


@pytest.mark.parametrize(
    "model_id, input_rate, output_rate, cache_read, cache_write",
    [
        ("claude-sonnet-5-5", 2e-06, 1e-05, 1e-07, 2.5e-06),
        ("claude-haiku-5-5", 1e-07, 5e-07, 1e-08, 1.25e-07),
    ],
)
def test_builtin_rate_snapshot_loads_checked_in_claude_rates(
    monkeypatch, tmp_path, model_id, input_rate, output_rate, cache_read, cache_write
):
    """Load checked-in Claude token and cache rates without a LiteLLM install."""
    from pathlib import Path

    # The snapshot is a checked-in repo artifact: reading the real file keeps
    # this test deterministic in CI (no venv, no network).
    snapshot = json.loads(
        (
            Path(__file__).resolve().parents[3] / "configs/litellm/model-rates.json"
        ).read_text()
    )
    expected = {
        "input_cost_per_token": input_rate,
        "output_cost_per_token": output_rate,
        "cache_read_input_token_cost": cache_read,
        "cache_creation_input_token_cost": cache_write,
        "max_input_tokens": 1000000,
        "max_output_tokens": 128000,
        "mode": "chat",
    }
    assert snapshot["models"][model_id] == expected
    monkeypatch.setattr(litellm_config, "_BUILTIN_COST_MAP_LOADED", False)
    monkeypatch.setenv("LITELLM_ROOT", str(tmp_path / "missing-litellm"))
    assert (
        litellm_config.model_rates("anthropic", model_id, f"anthropic/{model_id}")
        == expected
    )


def test_builtin_rates_price_claude_wires_from_bare_map_keys(monkeypatch):
    _seed_builtin_cost_map(
        monkeypatch, {"claude-sonnet-5-5": dict(CLAUDE_FIXTURE_ENTRY)}
    )
    info = litellm_config.builtin_rates("anthropic/claude-sonnet-5-5")
    assert info == {
        "input_cost_per_token": 2e-06,
        "output_cost_per_token": 1e-05,
        "cache_read_input_token_cost": 1e-07,
        "max_input_tokens": 1000000,
        "max_output_tokens": 128000,
        "mode": "chat",
    }


def test_builtin_rates_prices_gemini_via_namespaced_key(monkeypatch):
    _seed_builtin_cost_map(
        monkeypatch,
        {
            "gemini/gemini-3.8-flash": {
                "input_cost_per_token": 7.5e-07,
                "output_cost_per_token": 3.75e-06,
            }
        },
    )
    info = litellm_config.builtin_rates("gemini/gemini-3.8-flash")
    assert info is not None
    assert info["input_cost_per_token"] == 7.5e-07
    assert "mode" not in info  # absent fields are omitted, not invented


def test_builtin_rates_never_prices_subscription_chatgpt_wires(monkeypatch):
    _seed_builtin_cost_map(
        monkeypatch,
        {
            "gpt-6-luna": dict(CLAUDE_FIXTURE_ENTRY),
            "chatgpt/gpt-6-luna": {"input_cost_per_token": None},
        },
    )
    assert litellm_config.builtin_rates("chatgpt/gpt-6-luna") is None


def _seed_models_dev_fetch(monkeypatch, catalogue):
    litellm_config._MODELS_DEV_RATES_LOADED = False
    litellm_config._MODELS_DEV_RATES = {}
    monkeypatch.setattr(litellm_config, "_MODELS_DEV_FETCH", lambda **kwargs: catalogue)


def test_models_dev_rates_index_builds_and_converts_units(monkeypatch):
    _seed_models_dev_fetch(
        monkeypatch,
        {
            "ollama-cloud": {
                "models": {
                    "glm-5.3-flash": {
                        "cost": {"input": 0.15, "output": 0.5, "cache_read": 0.03},
                        "limit": {"context": 1000000, "output": 131072},
                    }
                }
            }
        },
    )
    index = litellm_config._models_dev_rates()
    assert "ollama-cloud" in index
    assert "glm-5.3-flash" in index["ollama-cloud"]
    info = litellm_config.model_rates("ollama", "glm-5.3-flash:cloud", "ollama/x")
    # models.dev prices $/1M tokens; LiteLLM wants $/token.
    assert info == {
        "input_cost_per_token": 1.5e-07,
        "output_cost_per_token": 5e-07,
        "cache_read_input_token_cost": 3e-08,
        "max_input_tokens": 1000000,
        "max_output_tokens": 131072,
    }


def test_models_dev_rates_support_ollama_hyphen_cloud_stubs(monkeypatch):
    _seed_models_dev_fetch(
        monkeypatch,
        {
            "ollama-cloud": {
                "models": {
                    "nemotron-3-nano:30b": {
                        "cost": {"input": 0.2, "output": 0.6},
                    }
                }
            }
        },
    )
    info = litellm_config.model_rates("ollama", "nemotron-3-nano:30b-cloud", "ollama/x")
    assert info["input_cost_per_token"] == pytest.approx(2e-07)
    assert info["output_cost_per_token"] == pytest.approx(6e-07)


def test_models_dev_rates_chatgpt_subscription_estimate(monkeypatch):
    _seed_models_dev_fetch(
        monkeypatch,
        {"openai": {"models": {"gpt-6-luna": {"cost": {"input": 0.1, "output": 0.5}}}}},
    )
    info = litellm_config.model_rates("openai", "gpt-6-luna", "chatgpt/gpt-6-luna")
    assert info["input_cost_per_token"] == pytest.approx(1e-07)
    assert info["output_cost_per_token"] == pytest.approx(5e-07)


def test_models_dev_rates_fall_back_to_installed_map(monkeypatch):
    _seed_models_dev_fetch(monkeypatch, {})
    _seed_builtin_cost_map(
        monkeypatch, {"claude-sonnet-5-5": dict(CLAUDE_FIXTURE_ENTRY)}
    )
    info = litellm_config.model_rates(
        "meridian", "claude-sonnet-5-5", "anthropic/claude-sonnet-5-5"
    )
    assert info == dict(CLAUDE_FIXTURE_ENTRY)


def test_models_dev_rates_precedence_over_installed_map(monkeypatch):
    _seed_models_dev_fetch(
        monkeypatch,
        {
            "anthropic": {
                "models": {"claude-sonnet-5-5": {"cost": {"input": 2, "output": 10}}}
            }
        },
    )
    _seed_builtin_cost_map(
        monkeypatch, {"claude-sonnet-5-5": dict(CLAUDE_FIXTURE_ENTRY)}
    )
    info = litellm_config.model_rates(
        "meridian", "claude-sonnet-5-5", "anthropic/claude-sonnet-5-5"
    )
    assert info["input_cost_per_token"] == 2e-06
    assert info["output_cost_per_token"] == 1e-05


def test_models_dev_rates_degraded_fetch_keeps_generation_running(monkeypatch):
    def explode(**kwargs):
        raise OSError("offline")

    litellm_config._MODELS_DEV_RATES_LOADED = False
    litellm_config._MODELS_DEV_RATES = {}
    monkeypatch.setattr(litellm_config, "_MODELS_DEV_FETCH", explode)
    _seed_builtin_cost_map(
        monkeypatch, {"claude-sonnet-5-5": dict(CLAUDE_FIXTURE_ENTRY)}
    )
    info = litellm_config.model_rates(
        "meridian", "claude-sonnet-5-5", "anthropic/claude-sonnet-5-5"
    )
    assert info == dict(CLAUDE_FIXTURE_ENTRY)


def test_build_rates_snapshot_uses_bare_keys_and_provenance(monkeypatch):
    _seed_models_dev_fetch(
        monkeypatch,
        {
            "openai": {
                "models": {
                    "gpt-6-luna": {
                        "cost": {"input": 0.1, "output": 0.5},
                        "limit": {"context": 1050000, "output": 128000},
                    }
                }
            },
            "ollama-cloud": {
                "models": {"glm-5.3-flash": {"cost": {"input": 0.15, "output": 0.5}}}
            },
        },
    )
    snapshot = litellm_config.build_rates_snapshot({})
    assert "gpt-6-luna" in snapshot["models"]
    assert snapshot["models"]["chatgpt/gpt-6-luna"]["mode"] == "responses"
    assert snapshot["models"]["chatgpt/gpt-6-luna"]["litellm_provider"] == "chatgpt"
    # The chatgpt twin prices the namespaced bundled entry that shadows the
    # bare key at request-time resolution (subscription-lane estimate).
    assert snapshot["models"]["chatgpt/gpt-6-luna"][
        "input_cost_per_token"
    ] == pytest.approx(1e-07)
    # Daemon cloud-stub request spellings get priced twins too — the bundled
    # map never prices ':cloud'/-cloud forms and that's why their spend was $0.
    stub = snapshot["models"]["glm-5.3-flash"]
    for twin in (
        "glm-5.3-flash:cloud",
        "glm-5.3-flash-cloud",
        "ollama/glm-5.3-flash:cloud",
    ):
        assert (
            snapshot["models"][twin]["input_cost_per_token"]
            == stub["input_cost_per_token"]
        )
    assert snapshot["_provenance"]["source"] == "models.dev api.json"
    assert "fetched" in snapshot["_provenance"]


def test_builtin_rates_unknown_wire_stays_unpriced(monkeypatch):
    _seed_builtin_cost_map(
        monkeypatch, {"claude-sonnet-5-5": dict(CLAUDE_FIXTURE_ENTRY)}
    )
    assert litellm_config.builtin_rates("opencode/big-pickle") is None
    assert litellm_config.builtin_rates("ollama/gemma4:31b-cloud") is None


def test_meridian_entries_carry_cached_claude_rates(monkeypatch):
    _seed_builtin_cost_map(
        monkeypatch, {"claude-sonnet-5-5": dict(CLAUDE_FIXTURE_ENTRY)}
    )
    monkeypatch.setattr(litellm_config, "is_meridian_configured", lambda: True)
    entries = litellm_config.compute_model_list({"MERIDIAN_API_KEY": "meridian"})
    target = next(
        item for item in entries if item["model_name"] == "meridian/claude-sonnet-5-5"
    )
    info = target["litellm_params"]["model_info"]
    assert info["input_cost_per_token"] == 2e-06
    assert info["output_cost_per_token"] == 1e-05


def test_previous_entries_ledger_round_trip(tmp_path):
    """The deployed-entries ledger writes and reads back stdlib-only."""
    config_path = tmp_path / "config.yaml"
    entries = [
        {
            "model_name": "ollama-cloud/glm-5.3-flash",
            "litellm_params": {"model": "openai/glm-5.3-flash"},
        }
    ]
    config_path.parent.mkdir(parents=True, exist_ok=True)
    assert litellm_config.read_previous_entries(config_path) == []
    litellm_config.write_previous_entries(config_path, entries)
    ledger = litellm_config.previous_entries_ledger_path(config_path)
    assert ledger.name == "config.models.json"
    assert litellm_config.read_previous_entries(config_path) == entries
    # No PyYAML anywhere in the module: the deploy template's interpreter
    # lacks it, so retention must ride the stdlib JSON ledger.
    module_source = Path(litellm_config.__file__).read_text(encoding="utf-8")
    assert "import yaml" not in module_source


def test_backup_recovery_is_explicit_staged_and_regenerated(tmp_path, monkeypatch):
    backup = tmp_path / "recovery.yaml"
    backup.write_text(
        'model_list:\n  - model_name: "openai/gpt-6-luna"\n'
        '    litellm_params:\n      model: "openai/gpt-6-luna"\n'
        "      api_key: os.environ/OPENAI_API_KEY\n"
        '      api_base: "https://api.openai.com/v1"\n'
        "litellm_settings:\n  drop_params: true\n  telemetry: false\n"
        "general_settings:\n  master_key: os.environ/LITELLM_MASTER_KEY\n"
        "  database_url: os.environ/DATABASE_URL\n",
        encoding="utf-8",
    )
    authorized = [
        {
            "model_name": "openai/gpt-6-luna",
            "litellm_params": {"model": "openai/gpt-6-luna"},
        }
    ]
    rendered, entries = litellm_config.stage_backup_entries(backup, authorized)
    assert entries[0]["model_name"] == "openai/gpt-6-luna"
    assert 'model_name: "openai/gpt-6-luna"' in rendered
    assert backup.exists()
    with pytest.raises(ValueError, match="no currently authorized"):
        litellm_config.stage_backup_entries(backup, [])
    with pytest.raises(ValueError, match="truncated"):
        litellm_config.stage_backup_text(
            'model_list:\n  - model_name: "openai/a"\n    litellm_params:\n', authorized
        )
    with pytest.raises(ValueError, match="truncated|malformed|trailing"):
        litellm_config.stage_backup_text(
            backup.read_text(encoding="utf-8") + "garbage\n", authorized
        )


def test_routed_poolside_backup_metadata_round_trips_without_trusting_backup():
    entry = {
        "model_name": "poolside/laguna-s-2.1",
        "litellm_params": {
            "model": "openai/poolside/laguna-s-2.1",
            "model_info": POOLSIDE_ROUTED_MODEL_INFO,
        },
    }
    text = litellm_config.render_config(entries=[entry])
    rendered, entries = litellm_config.stage_backup_text(text, [entry])
    assert rendered == text
    assert entries == [entry]
    with pytest.raises(ValueError, match="no currently authorized"):
        litellm_config.stage_backup_text(text, [])
    model_info_line = f"    model_info: {json.dumps(POOLSIDE_ROUTED_MODEL_INFO)}"
    for invalid in ("[]", "null", "not-json"):
        malformed = text.replace(model_info_line, f"    model_info: {invalid}")
        with pytest.raises(ValueError):
            litellm_config.stage_backup_text(malformed, [entry])


def test_local_family_shrink_guard_retains_ledger_on_outage(monkeypatch):
    ledger = [
        {
            "model_name": "omlx/Qwen3.8-27B-4bit",
            "litellm_params": {"model": "openai/Qwen3.8-27B-4bit"},
        },
        {
            "model_name": "omlx/Qwen3-ASR-1.7B-8bit",
            "litellm_params": {
                "model": "openai/Qwen3-ASR-1.7B-8bit",
                "api_base": "http://127.0.0.1:11427/v1",
            },
        },
    ]

    def outage(*_args, **_kwargs):
        raise RuntimeError("simulated discovery outage")

    monkeypatch.setattr(litellm_config, "active_engines", lambda: ["omlx"])
    monkeypatch.setattr(litellm_config, "iter_engine_models", outage)
    monkeypatch.setattr(
        litellm_config,
        "local_endpoint_for",
        lambda *args, **kwargs: ("http://127.0.0.1:9", "OMLX_API_KEY"),
    )
    entries = litellm_config.compute_model_list(
        environ={"OMLX_API_KEY": "k"}, previous_entries=ledger
    )
    names = {entry["model_name"] for entry in entries}
    assert "omlx/Qwen3.8-27B-4bit" in names
    assert "omlx/Qwen3-ASR-1.7B-8bit" in names
    assert any(
        "ledger family retained (bare twins regenerate on recovery)" in note
        for note in litellm_config.last_generation_notes
    )
    monkeypatch.setattr(
        litellm_config, "local_endpoint_for", lambda *args, **kwargs: None
    )
    entries = litellm_config.compute_model_list(
        environ={"OMLX_API_KEY": "k"}, previous_entries=ledger
    )
    names = {entry["model_name"] for entry in entries}
    assert "omlx/Qwen3.8-27B-4bit" in names
    assert any(
        "ledger family retained" in note
        for note in litellm_config.last_generation_notes
    )


def test_ollama_typed_publication_reaches_gateway_via_show_metadata(monkeypatch):
    """R8 P0.1: an untyped installed Ollama embedding model publishes its canonical alias.

    The real discovery payload is untyped (name/size/provider only), so the
    publication boundary classifies it through the cached /api/show hook;
    failures and non-embedding results stay UNKNOWN honestly — never guessed
    qualified — and the chat containment keeps the specialized name excluded.
    """

    def discovery(provider, include_cloud=False):
        if provider == "ollama":
            return [
                {
                    "name": "embeddinggemma-2:740m-mxfp8",
                    "size_gb": 0.5,
                    "provider": "ollama",
                },
                {"name": "chat-model", "provider": "ollama"},
            ]
        return []

    monkeypatch.setattr(litellm_config, "active_engines", lambda: ["ollama"])
    monkeypatch.setattr(litellm_config, "iter_engine_models", discovery)
    monkeypatch.setattr(
        litellm_config,
        "local_endpoint_for",
        lambda *args, **kwargs: ("http://127.0.0.1:11434/v1", "OLLAMA_API_KEY"),
    )
    monkeypatch.setattr(litellm_config, "list_cloud_ollama_models", lambda: [])
    monkeypatch.setattr(
        litellm_config,
        "_ollama_metadata",
        lambda name: (
            {"capabilities": ["embedding"], "model_type": None}
            if "embeddinggemma" in name
            else {"capabilities": ["completion"], "model_type": "llm"}
        ),
    )
    entries = litellm_config.compute_model_list(environ={}, previous_entries=[])
    names = [entry["model_name"] for entry in entries]
    canonical = "ollama/embeddinggemma-2:740m-mxfp8"
    assert canonical in names
    assert "embeddinggemma-2:740m-mxfp8" in names  # unique bare modality twin
    assert names.count(canonical) == 1  # typed publication is not chat-duplicated
    assert "chat-model" in names  # untyped chat stays published
    # Non-embedding metadata → absent honestly (no guessed qualification).
    monkeypatch.setattr(
        litellm_config,
        "_ollama_metadata",
        lambda name: {"capabilities": ["completion"], "model_type": "llm"},
    )
    entries = litellm_config.compute_model_list(environ={}, previous_entries=[])
    names = [entry["model_name"] for entry in entries]
    assert canonical not in names
    assert "embeddinggemma-2:740m-mxfp8" not in names
    assert "chat-model" in names


def test_base_url_lanes_without_key_env_carry_a_placeholder_credential(monkeypatch):
    """Every api_base lane needs a non-empty api_key or LiteLLM 500s per request.

    LiteLLM's OpenAI-compatible driver refuses requests with 'Missing
    credentials' when litellm_params lacks api_key, even for unauthenticated
    local endpoints — observed live on the embedding lane (B1 evidence note).
    The ollama lane inputs are stubbed so this is deterministic under the
    hermetic environment fixture.
    """
    real_iter = litellm_config.iter_engine_models

    def fake_iter_engine_models(provider):
        if provider == "ollama":
            return [{"name": "embeddinggemma-2:740m-mxfp8"}]
        return real_iter(provider)

    monkeypatch.setattr(litellm_config, "active_engines", lambda: ["ollama"])
    monkeypatch.setattr(litellm_config, "iter_engine_models", fake_iter_engine_models)
    monkeypatch.setattr(
        litellm_config,
        "get_ollama_local_base_url",
        lambda *args, **kwargs: "http://127.0.0.1:11434/v1",
    )
    monkeypatch.setattr(litellm_config, "list_cloud_ollama_models", lambda: [])
    monkeypatch.setattr(
        litellm_config,
        "_ollama_metadata",
        lambda name: (
            {"capabilities": ["embedding"], "model_type": "embedding"}
            if "embeddinggemma" in name
            else {"capabilities": ["completion"], "model_type": "llm"}
        ),
    )
    entries = litellm_config.compute_model_list(environ={}, previous_entries=[])
    names = [entry["model_name"] for entry in entries]
    # Non-vacuous anchor: the vulnerable lane must actually be in the population.
    assert "ollama/embeddinggemma-2:740m-mxfp8" in names
    assert "embeddinggemma-2:740m-mxfp8" in names
    credless = sorted(
        entry["model_name"]
        for entry in entries
        if isinstance(entry, dict)
        and entry["litellm_params"].get("api_base")
        and not entry["litellm_params"].get("api_key")
    )
    assert credless == [], f"api_base lanes without any api_key: {credless}"
