import os
import json
import io
import shlex
import subprocess
import urllib.error
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

import litellm_config

LIVE_CATALOGUE = litellm_config._live_catalogue

CONFIGURE_SPEC = spec_from_file_location(
    "configure_litellm", Path(__file__).resolve().parents[2] / "configure-litellm.py"
)
CONFIGURE = module_from_spec(CONFIGURE_SPEC)
CONFIGURE_SPEC.loader.exec_module(CONFIGURE)
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


def test_meridian_generates_all_allowlisted_model_aliases(monkeypatch):
    import json
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
            ("http://127.0.0.1:11427/v1", None) if provider == "omlx" else None
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
    entries = litellm_config.compute_model_list({key: "test-key"})
    alias = f"{wire_prefix}{model_id}"
    entry = next(item for item in entries if item["model_name"] == alias)
    assert entry["litellm_params"]["model"] == alias
    assert "api_base" not in entry["litellm_params"]
    assert entry["litellm_params"]["api_key"] == f"os.environ/{key}"
    assert not any(
        item["model_name"].startswith(wire_prefix)
        for item in litellm_config.compute_model_list({"UNRELATED": ""})
    )


def test_registry_identities_have_exact_qualified_aliases(monkeypatch):
    root = Path(__file__).resolve().parents[3]
    refs = litellm_config._registry_model_refs()
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
    assert any(ref.startswith("openrouter/inclusionai/") for ref in expected)
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
    entries = litellm_config.compute_model_list({"CEREBRAS_API_KEY": "test-key"})
    entry = next(item for item in entries if item["model_name"] == "cerebras/model-x")
    assert entry["litellm_params"]["model"] == "cerebras/model-x"
    assert "api_base" not in entry["litellm_params"]


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
        {key: "test-key", "GEMINI_API_KEY": "google-key"}
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


def test_bare_alias_ambiguity_is_removed_in_either_provider_order(monkeypatch):
    monkeypatch.setattr(litellm_config, "iter_engine_models", lambda provider: ["same"])
    monkeypatch.setattr(litellm_config, "list_cloud_ollama_models", lambda: [])
    monkeypatch.setattr(
        litellm_config,
        "local_endpoint_for",
        lambda *_: ("http://127.0.0.1:11427/v1", None),
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

    assert aliases.count("ollama/glm-5.3:cloud") == 1
    assert aliases.count("glm-5.3:cloud") == 1
    assert {
        "ollama/qwen3-cloud",
        "qwen3-cloud",
        "ollama/deepseek-cloud",
        "deepseek-cloud",
    } <= set(aliases)
    assert {"ollama/local-model", "local-model"} <= set(aliases)
    assert by_alias["ollama/glm-5.3:cloud"]["litellm_params"] == {
        "model": "ollama/glm-5.3:cloud",
        "api_base": "http://127.0.0.1:11434",
    }
    assert (
        by_alias["glm-5.3:cloud"]["litellm_params"]
        == by_alias["ollama/glm-5.3:cloud"]["litellm_params"]
    )


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
        "shared-cloud",
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

    CONFIGURE.provision_app_keys("master", path, "http://127.0.0.1:4000")

    assert [c[2]["key_alias"] for c in calls if c[1] == "POST"] == [
        "pi",
        "openwebui",
        "junie",
    ]
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
    CONFIGURE.provision_app_keys("dummy-master", service, "http://127.0.0.1:4000")
    assert (tmp_path / "clients/opencode.key").read_text(encoding="utf-8") == (
        "dummy-existing"
    )
    junie_key = tmp_path / "clients/junie.key"
    assert junie_key.read_text(encoding="utf-8") == "dummy-junie-existing"
    assert junie_key.parent.stat().st_mode & 0o777 == 0o700
    assert junie_key.stat().st_mode & 0o777 == 0o600


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

    CONFIGURE.provision_app_keys("master", path, "http://127.0.0.1:4000")

    assert len(generate_attempts) == len(CONFIGURE.APP_KEYS)
    # A 400 is not persisted as a key; the service env keeps its prior state.
    assert not any(
        name in path.read_text(encoding="utf-8") for name in CONFIGURE.APP_KEYS.values()
    )


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
        for item in litellm_config.compute_model_list()
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
        / ".chezmoiscripts/run_onchange_31-litellm.sh.tmpl"
    ).read_text(encoding="utf-8")
    prepare = template.index('litellm_ensure_prisma_client "$VENV"')
    assert prepare < template.index("systemctl --user enable --now litellm.service")
    assert prepare < template.index("litellm_service_start")
