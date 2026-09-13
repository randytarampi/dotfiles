import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import discover_models
import local_engines
import omlx
import opencode_config
import tier_resolve


def test_get_preset_providers_extracts_provider_prefixes():
    assert opencode_config.get_preset_providers("pro-plus") == {
        "openai",
        "ollama-cloud",
    }
    assert "anthropic" in opencode_config.get_preset_providers("pro-plus-anthropic")
    assert opencode_config.get_preset_providers("plus") == {"openai"}


def _load_script(name, filename):
    path = Path(__file__).resolve().parents[3] / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_map_omlx_capabilities_by_model_type():
    assert omlx.map_omlx_capabilities({"model_type": "llm"}) == {
        "completion",
        "tools",
    }
    assert omlx.map_omlx_capabilities(
        {"model_type": "vlm", "enable_thinking": True}
    ) == {"completion", "thinking", "tools", "vision"}
    for model_type in ("audio_stt", "audio_tts", "audio_sts"):
        assert omlx.map_omlx_capabilities({"model_type": model_type}) == {"audio"}


def test_map_omlx_capabilities_respects_explicit_tool_support():
    assert "tools" not in omlx.map_omlx_capabilities(
        {"model_type": "llm", "supports_tools": False}
    )
    assert "tools" in omlx.map_omlx_capabilities(
        {"model_type": "embedding", "supports_tools": True}
    )


def test_list_omlx_models_marks_embedding_and_reranker_separately():
    models_payload = {
        "data": [
            {"id": "chat", "max_model_len": 4096},
            {"id": "embed", "max_model_len": 4096},
            {"id": "rank", "max_model_len": 4096},
        ]
    }
    status_payload = {
        "models": [
            {"id": "chat", "model_type": "llm", "thinking_default": True},
            {"id": "embed", "model_type": "embedding"},
            {"id": "rank", "model_type": "reranker"},
        ]
    }
    omlx._STATUS_CACHE.clear()
    with patch.object(omlx, "_get_json", side_effect=[models_payload, status_payload]):
        models = omlx.list_omlx_models()
    assert [model["name"] for model in models] == ["chat", "embed", "rank"]
    assert models[0]["provider"] == "omlx"
    assert models[1]["primary_category"] == "embedding"
    assert models[2]["primary_category"] == "reranker"
    assert "thinking" in models[0]["capabilities"]


def test_status_failure_does_not_cache_empty_result_or_admit_unknown_models():
    models_payload = {"data": [{"id": "untyped", "max_model_len": 4096}]}
    omlx._STATUS_CACHE.clear()
    with patch.object(
        omlx,
        "_get_json",
        side_effect=[
            models_payload,
            RuntimeError("status down"),
            RuntimeError("status down"),
        ],
    ) as get_json:
        models = omlx.list_omlx_models()
        assert omlx.get_omlx_model_status("untyped") == {}
        assert omlx.get_omlx_model_status("untyped") == {}
    assert models[0]["primary_category"] == "unknown"
    assert "completion" not in models[0]["capabilities"]
    assert get_json.call_count == 4
    with patch.object(tier_resolve, "get_model_details") as details:
        resolved = tier_resolve.resolve_roles_from_list(models)
    assert all("untyped" not in value for value in resolved.values())
    details.assert_not_called()


def test_merge_omlx_settings_uses_nested_schema_and_preserves_unmanaged_keys():
    settings = local_engines.merge_omlx_settings(
        {"server": {"log_level": "debug"}, "custom": {"keep": True}},
        {
            "OMLX_HOST": "0.0.0.0",
            "OMLX_PORT": "8100",
            "OMLX_MODEL_DIR": "/tmp/omlx-models",
            "OMLX_MEMORY_GUARD": "balanced",
            "OMLX_MAX_CONCURRENT_REQUESTS": "4",
            "OMLX_SSD_CACHE_DIR": "/tmp/omlx-cache",
            "OMLX_LOG_LEVEL": "debug",
            "OMLX_API_KEY": "secret",
            "OMLX_HF_ENDPOINT": "https://hf.example",
        },
    )
    assert settings["server"] == {
        "log_level": "debug",
        "host": "0.0.0.0",
        "port": 8100,
    }
    assert settings["model"]["model_dirs"] == ["/tmp/omlx-models"]
    assert settings["memory"]["memory_guard_tier"] == "balanced"
    assert settings["memory"]["prefill_memory_guard"] is True
    assert settings["scheduler"]["max_concurrent_requests"] == 4
    assert settings["cache"]["ssd_cache_dir"] == "/tmp/omlx-cache"
    assert settings["cache"]["enabled"] is True
    assert settings["cache"]["ssd_cache_max_size"] == "auto"
    assert settings["cache"]["hot_cache_max_size"] == "0"
    assert settings["cache"]["hot_cache_write_through"] is False
    assert settings["cache"]["initial_cache_blocks"] == 256
    assert settings["auth"]["api_key"] == "secret"
    assert settings["huggingface"]["endpoint"] == "https://hf.example"
    assert settings["mcp"]["expose_tools"] is False
    assert settings["custom"] == {"keep": True}


def test_merge_omlx_settings_off_disables_prefill_guard():
    settings = local_engines.merge_omlx_settings(
        {"memory": {"memory_guard_tier": "safe", "custom": True}},
        {"OMLX_MEMORY_GUARD": "off"},
    )
    assert "memory_guard_tier" not in settings["memory"]
    assert settings["memory"]["prefill_memory_guard"] is False
    assert settings["memory"]["custom"] is True
    assert "auth" not in settings
    assert "huggingface" not in settings


def test_verify_omlx_settings_accepts_valid_nested_cache_schema():
    verify = _load_script("verify_config", "verify-config.py")
    settings = local_engines.merge_omlx_settings({})
    assert verify.validate_omlx_settings(settings) == []


def test_merge_omlx_settings_pins_mcp_expose_tools_off():
    # Upstream default is true; the managed writer must converge it off.
    settings = local_engines.merge_omlx_settings(
        {"mcp": {"expose_tools": True, "config_path": "/tmp/mcp.json"}},
        {"OMLX_API_KEY": "secret"},
    )
    assert settings["mcp"]["expose_tools"] is False
    assert settings["mcp"]["config_path"] == "/tmp/mcp.json"
    explicit = local_engines.merge_omlx_settings({}, {"OMLX_MCP_EXPOSE_TOOLS": "1"})
    assert explicit["mcp"]["expose_tools"] is True


def test_verify_omlx_settings_reports_invalid_memory_guard():
    verify = _load_script("verify_config", "verify-config.py")
    settings = local_engines.merge_omlx_settings({})
    settings["memory"]["memory_guard_tier"] = "weird"
    assert "memory.memory_guard_tier" in " ".join(
        verify.validate_omlx_settings(settings)
    )


def test_discovery_merges_omlx_and_ollama_with_omlx_collision_wins():
    ollama_output = (
        "NAME ID SIZE MODIFIED\nshared abc 1 GB now\nollama-only def 2 GB now\n"
    )
    omlx_models = [
        {"name": "shared", "size_gb": 0.0, "provider": "omlx"},
        {"name": "omlx-only", "size_gb": 0.0, "provider": "omlx"},
    ]
    with (
        patch.object(discover_models, "find_ollama", return_value="ollama"),
        patch.object(discover_models.subprocess, "run") as run,
        patch.dict(os.environ, {"DOTFILES_RUN_OMLX_SETUP": "1"}, clear=False),
        patch.dict(
            local_engines.LOCAL_ENGINES["omlx"],
            {
                "health_check": lambda: (True, "HTTP 200"),
                "list_models": lambda include_cloud=False: omlx_models,
            },
        ),
    ):
        run.return_value.stdout = ollama_output
        models = discover_models.list_local_ollama_models()
    assert [(model["name"], model["provider"]) for model in models] == [
        ("ollama-only", "ollama"),
        ("shared", "omlx"),
        ("omlx-only", "omlx"),
    ]


def test_merged_discovery_forwards_include_cloud_to_ollama():
    with (
        patch.dict(os.environ, {"DOTFILES_RUN_OMLX_SETUP": "0"}, clear=False),
        patch.dict(
            local_engines.LOCAL_ENGINES["ollama"],
            {"list_models": lambda include_cloud=False: [{"name": "model:cloud"}]},
        ),
    ):
        models = discover_models.list_local_ollama_models(include_cloud=True)
    assert models == [{"name": "model:cloud"}]


def test_discovery_keeps_ollama_models_when_omlx_merge_fails():
    ollama_output = "NAME ID SIZE MODIFIED\nollama-only abc 1 GB now\n"
    with (
        patch.object(discover_models, "find_ollama", return_value="ollama"),
        patch.object(discover_models.subprocess, "run") as run,
        patch.dict(os.environ, {"DOTFILES_RUN_OMLX_SETUP": "1"}, clear=False),
        patch.dict(
            local_engines.LOCAL_ENGINES["omlx"],
            {
                "health_check": lambda: (True, "HTTP 200"),
                "list_models": lambda include_cloud=False: (_ for _ in ()).throw(
                    RuntimeError("omlx down")
                ),
            },
        ),
    ):
        run.return_value.stdout = ollama_output
        models = discover_models.list_local_ollama_models()
    assert models == [{"name": "ollama-only", "size_gb": 1.0, "provider": "ollama"}]


def test_resolve_roles_preserves_mixed_local_providers():
    models = [
        {"name": "qwen3-coder:7b", "size_gb": 7.0, "provider": "ollama"},
        {"name": "ornith-1.5:35b", "size_gb": 22.0, "provider": "omlx"},
    ]

    def details(model_name, provider="ollama"):
        return {
            "param_count": 35 if provider == "omlx" else 7,
            "capabilities": ["completion", "thinking", "tools", "vision"],
            "is_moe": False,
        }

    with patch.object(tier_resolve, "get_model_details", side_effect=details):
        resolved = tier_resolve.resolve_roles_from_list(models)
    assert resolved["code-gen"].startswith("omlx/")


def test_merged_pool_records_equivalent_ollama_names():
    ollama_output = "NAME ID SIZE MODIFIED\ngemma4:12b-mxfp8 abc 8 GB now\nollama-only def 2 GB now\n"
    omlx_models = [
        {"name": "gemma-4-12B-it-MLX-8bit", "size_gb": 6.3, "provider": "omlx"},
        {"name": "omlx-only", "size_gb": 0.0, "provider": "omlx"},
    ]
    with (
        patch.object(discover_models, "find_ollama", return_value="ollama"),
        patch.object(discover_models.subprocess, "run") as run,
        patch.dict(os.environ, {"DOTFILES_RUN_OMLX_SETUP": "1"}, clear=False),
        patch.dict(
            local_engines.LOCAL_ENGINES["omlx"],
            {
                "health_check": lambda: (True, "HTTP 200"),
                "list_models": lambda include_cloud=False: omlx_models,
            },
        ),
    ):
        run.return_value.stdout = ollama_output
        models = discover_models.list_local_ollama_models()
    by_name = {model["name"]: model for model in models}
    assert set(by_name) == {
        "gemma-4-12B-it-MLX-8bit",
        "omlx-only",
        "ollama-only",
    }
    assert by_name["gemma-4-12B-it-MLX-8bit"]["provider"] == "omlx"
    assert by_name["gemma-4-12B-it-MLX-8bit"]["equivalent_ollama_names"] == [
        "gemma4:12b-mxfp8"
    ]
    assert "equivalent_ollama_names" not in by_name["omlx-only"]
    assert "equivalent_ollama_names" not in by_name["ollama-only"]


def test_resolve_roles_unions_equivalent_ollama_capabilities():
    """Classification must bridge capabilities only Ollama reports (audio)."""
    models = [
        {
            "name": "gemma-4-12B-it-MLX-8bit",
            "size_gb": 6.3,
            "provider": "omlx",
            "equivalent_ollama_names": ["gemma4:12b-mxfp8"],
        }
    ]

    def details(model_name, provider="ollama"):
        if provider == "omlx":
            return {
                "param_count": 12,
                "capabilities": ["completion", "tools", "vision"],
                "is_moe": False,
            }
        return {
            "param_count": 12,
            "capabilities": ["completion", "vision", "audio"],
            "is_moe": False,
        }

    with patch.object(tier_resolve, "get_model_details", side_effect=details):
        resolved = tier_resolve.resolve_roles_from_list(models)
    assert resolved["audio"] == "omlx/gemma-4-12B-it-MLX-8bit"
    assert resolved["lightweight"] == "omlx/gemma-4-12B-it-MLX-8bit"


def test_resolve_roles_does_not_union_without_equivalents():
    models = [{"name": "gemma-4-12B-it-MLX-8bit", "size_gb": 6.3, "provider": "omlx"}]

    def details(model_name, provider="ollama"):
        if provider == "omlx":
            return {
                "param_count": 12,
                "capabilities": ["completion", "tools", "vision"],
                "is_moe": False,
            }
        return {
            "param_count": 12,
            "capabilities": ["completion", "vision", "audio"],
            "is_moe": False,
        }

    with patch.object(tier_resolve, "get_model_details", side_effect=details):
        resolved = tier_resolve.resolve_roles_from_list(models)
    assert resolved.get("audio") is None


def test_resolve_roles_keeps_audio_only_omlx_models_in_audio_category():
    models = [
        {
            "name": "speech-model",
            "size_gb": 4.0,
            "provider": "omlx",
            "model_type": "audio_stt",
            "capabilities": {"audio"},
        },
        {"name": "mini:7b", "size_gb": 7.0, "provider": "ollama"},
    ]

    def details(model_name, provider="ollama"):
        if provider == "omlx":
            return {
                "param_count": None,
                "capabilities": ["audio"],
                "is_moe": None,
            }
        return {"param_count": 7, "capabilities": ["completion", "tools"]}

    with patch.object(tier_resolve, "get_model_details", side_effect=details):
        resolved = tier_resolve.resolve_roles_from_list(models)
    assert resolved["audio"] == "omlx/speech-model"


def test_configure_opencode_omlx_provider_requires_reachable_daemon():
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    models = [{"name": "qwen", "provider": "omlx", "model_type": "llm"}]
    with (
        patch.dict(os.environ, {"DOTFILES_RUN_OMLX_SETUP": "1"}, clear=False),
        patch.dict(
            local_engines.LOCAL_ENGINES["omlx"],
            {
                "health_check": lambda: (True, "ok"),
                "base_url": lambda: "http://omlx:8000",
                "metadata_lookup": None,
                "metadata_capabilities": None,
            },
        ),
    ):
        provider = configure_opencode.build_local_provider("omlx", models)
    assert provider["options"]["baseURL"] == "http://omlx:8000/v1"
    assert provider["models"] == {"qwen": {"name": "qwen"}}

    with (patch.dict(os.environ, {"DOTFILES_RUN_OMLX_SETUP": "0"}, clear=False),):
        assert configure_opencode.build_local_provider("omlx", models) is None


def test_configure_opencode_local_provider_emits_modalities():
    """Vision-capable engine models must declare image input so OpenCode's
    client-side attachment gating accepts screenshots (registry-generic)."""
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    models = [
        {"name": "vlm-model", "provider": "omlx", "model_type": "vlm"},
        {"name": "text-model", "provider": "omlx", "model_type": "llm"},
    ]
    with (
        patch.dict(os.environ, {"DOTFILES_RUN_OMLX_SETUP": "1"}, clear=False),
        patch.dict(
            local_engines.LOCAL_ENGINES["omlx"],
            {
                "health_check": lambda: (True, "ok"),
                "base_url": lambda: "http://omlx:8000",
                "metadata_lookup": lambda name: (
                    {"model_type": "vlm"}
                    if name == "vlm-model"
                    else {"model_type": "llm"}
                ),
                "metadata_capabilities": lambda metadata: (
                    {"completion", "vision"}
                    if metadata.get("model_type") == "vlm"
                    else {"completion"}
                ),
            },
        ),
    ):
        provider = configure_opencode.build_local_provider("omlx", models)
    assert provider["models"]["vlm-model"]["modalities"] == {
        "input": ["text", "image"],
        "output": ["text"],
    }
    assert "modalities" not in provider["models"]["text-model"]


def test_configure_opencode_omlx_provider_is_absent_when_daemon_is_down():
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    with (
        patch.dict(os.environ, {"DOTFILES_RUN_OMLX_SETUP": "1"}, clear=False),
        patch.dict(
            local_engines.LOCAL_ENGINES["omlx"],
            {"health_check": lambda: (False, "down")},
        ),
    ):
        assert configure_opencode.build_local_provider("omlx", []) is None


def test_configure_opencode_omlx_provider_excludes_audio_only_models():
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    with (
        patch.dict(os.environ, {"DOTFILES_RUN_OMLX_SETUP": "1"}, clear=False),
        patch.dict(
            local_engines.LOCAL_ENGINES["omlx"], {"health_check": lambda: (True, "ok")}
        ),
    ):
        assert (
            configure_opencode.build_local_provider(
                "omlx",
                [{"name": "speech", "provider": "omlx", "model_type": "audio_stt"}],
            )
            is None
        )


def test_configure_opencode_global_cloud_tier_registers_omlx_provider():
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    models = [{"name": "chat", "provider": "omlx", "model_type": "llm"}]
    with (
        patch.dict(os.environ, {"DOTFILES_RUN_OMLX_SETUP": "1"}, clear=False),
        patch.dict(
            local_engines.LOCAL_ENGINES["omlx"], {"health_check": lambda: (True, "ok")}
        ),
    ):
        provider = configure_opencode.build_local_provider("omlx", models)
        config = {"provider": {}}
        configure_opencode.register_local_provider(config, "omlx", provider)
    assert config["provider"]["omlx"] is provider


def test_tier_switch_filters_omlx_models_when_gate_is_off():
    configure_tier = _load_script(
        "configure_opencode_tier", "configure-opencode-tier.py"
    )
    models = [
        {"name": "ollama-model", "provider": "ollama"},
        {"name": "omlx-model", "provider": "omlx"},
    ]
    with patch.dict(os.environ, {"DOTFILES_RUN_OMLX_SETUP": "0"}, clear=False):
        assert configure_tier.filter_omlx_models_for_gate(models) == [models[0]]


def test_merged_discovery_ignores_omlx_endpoint_env_when_gate_is_off(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "0")
    monkeypatch.setenv("OMLX_HOST", "omlx.example")
    monkeypatch.setenv("OMLX_PORT", "8000")
    monkeypatch.setenv("OMLX_BASE_URL", "http://omlx.example:8000")
    health_check = MagicMock(return_value=(True, "ok"))
    with (
        patch.object(discover_models, "find_ollama", return_value="ollama"),
        patch.object(discover_models.subprocess, "run") as run,
        patch.dict(local_engines.LOCAL_ENGINES["omlx"], {"health_check": health_check}),
    ):
        run.return_value.stdout = "NAME ID SIZE MODIFIED\nollama-only abc 1 GB now\n"
        models = discover_models.list_local_ollama_models()
    assert [model["name"] for model in models] == ["ollama-only"]
    health_check.assert_not_called()


def test_jetbrains_omlx_provider_uses_openai_completion_endpoint():
    generate_profiles = _load_script(
        "generate_jetbrains_profiles", "generate-jetbrains-profiles.py"
    )
    cfg = {
        "providers": {
            "omlx": {
                "baseUrl": "",
                "apiType": "OpenAICompletion",
                "apiKeyEnv": "OMLX_API_KEY",
            }
        }
    }
    with (
        patch.dict(
            local_engines.LOCAL_ENGINES["omlx"],
            {"base_url": lambda: "http://omlx:8000"},
        ),
        patch.dict(os.environ, {}, clear=True),
    ):
        provider = generate_profiles.build_provider_configs(cfg)["omlx"]
    assert provider["baseUrl"] == "http://omlx:8000/v1/chat/completions"
    assert "apiKey" not in provider


def test_jetbrains_wrapper_preserves_mixed_provider_metadata():
    wrapper = _load_script("configure_jetbrains_ai", "configure-jetbrains-ai.py")
    generator = _load_script(
        "generate_jetbrains_profiles", "generate-jetbrains-profiles.py"
    )
    mixed_models = [
        {"name": "ollama-chat", "provider": "ollama"},
        {"name": "omlx-chat", "provider": "omlx", "model_type": "llm"},
    ]
    captured = {}

    def run(command):
        captured["command"] = command
        return type("Result", (), {"returncode": 0})()

    with (
        patch.object(wrapper, "load_env", return_value=True),
        patch.object(wrapper, "list_local_ollama_models", return_value=mixed_models),
        patch.object(wrapper, "ensure_ai_dirs"),
        patch.object(wrapper.subprocess, "run", side_effect=run),
        patch.dict(os.environ, {"DOTFILES_RUN_OMLX_SETUP": "1"}, clear=False),
        patch.object(sys, "argv", ["configure-jetbrains-ai.py", "--skip", "dirs"]),
    ):
        wrapper.main()
    encoded = captured["command"][captured["command"].index("--local-models") + 1]
    parsed = generator.parse_local_models(encoded)
    assert parsed[1]["provider"] == "omlx"


def test_pi_omlx_provider_is_emitted_alongside_local_ollama_provider():
    configure_pi = _load_script("configure_pi", "configure-pi.py")
    with (
        patch.dict(os.environ, {"DOTFILES_RUN_OMLX_SETUP": "1"}, clear=False),
        patch.dict(
            local_engines.LOCAL_ENGINES["omlx"],
            {
                "health_check": lambda: (True, "ok"),
                "base_url": lambda: "http://omlx:8000",
            },
        ),
        patch.object(
            configure_pi,
            "model_entry",
            return_value={"id": "qwen", "contextWindow": 32768},
        ),
    ):
        providers = {"ollama": {"models": [{"id": "ollama-model"}]}}
        providers["omlx"] = configure_pi.build_local_provider("omlx", ["qwen"])
    assert "ollama" in providers and "omlx" in providers
    assert providers["omlx"]["baseUrl"] == "http://omlx:8000/v1"


def test_pi_omlx_provider_filters_non_chat_models():
    configure_pi = _load_script("configure_pi", "configure-pi.py")
    models = [
        {"name": "chat", "provider": "omlx", "model_type": "llm"},
        {"name": "speech", "provider": "omlx", "model_type": "audio_stt"},
        {"name": "embed", "provider": "omlx", "model_type": "embedding"},
    ]
    assert configure_pi.local_chat_model_ids(models, "omlx") == ["chat"]


def test_pi_omlx_context_ignores_ollama_cap():
    configure_pi = _load_script("configure_pi", "configure-pi.py")
    with patch.object(
        configure_pi,
        "_native_context",
        return_value=131072,
    ):
        with patch.dict(os.environ, {"OLLAMA_CONTEXT_LENGTH": "4096"}, clear=False):
            assert configure_pi._model_context_window("chat", True, "omlx") == 131072


def test_voice_omlx_llm_endpoint_and_api_key_are_conditional():
    voice = _load_script("configure_opencode_voice", "configure-opencode-voice.py")
    with (
        patch.dict(
            local_engines.LOCAL_ENGINES["omlx"],
            {"base_url": lambda: "http://omlx:8000"},
        ),
        patch.dict(os.environ, {"OMLX_API_KEY": "secret"}, clear=False),
    ):
        configured = voice.local_voice_provider("omlx/chat")
    assert configured == {
        "endpoint": "http://omlx:8000/v1",
        "model": "chat",
        "apiKeyEnv": "OMLX_API_KEY",
    }
    with patch.dict(os.environ, {}, clear=True):
        unconfigured = voice.local_voice_provider("omlx/chat")
    assert "apiKeyEnv" not in unconfigured


def test_acp_local_model_uses_combined_pool():
    acp = _load_script("configure_acp_agents", "configure-acp-agents.py")
    seen = []

    def resolve(models):
        seen.extend(models)
        return {"solo": "omlx/winner"}

    with (
        patch.object(
            acp,
            "active_engine_pools",
            return_value={
                "ollama": [{"name": "legacy", "provider": "ollama"}],
                "omlx": [{"name": "winner", "provider": "omlx"}],
            },
        ),
        patch.object(tier_resolve, "resolve_roles_from_list", side_effect=resolve),
    ):
        assert acp.local_model() == "omlx/winner"
    assert [model["name"] for model in seen] == ["legacy", "winner"]


def test_local_engine_registry_routes_a_third_engine_without_consumer_branch(
    monkeypatch,
):
    codex = _load_script("configure_codex", "configure-codex.py")
    fake = {
        "api": "openai",
        "base_url": lambda: "http://lmstudio:1234",
        "api_key_env": None,
        "anthropic_support": False,
        "gemini_support": False,
        "profile_provider": "lmstudio-local",
        "provider_config": True,
    }
    monkeypatch.setitem(local_engines.LOCAL_ENGINES, "lmstudio", fake)
    config = codex.build_provider_config("lmstudio/chat")
    assert "[model_providers.lmstudio-local]" in config
    assert 'base_url = "http://lmstudio:1234/v1"' in config


def _fake_lmstudio_engine():
    """A synthetic third engine satisfying the registry contract.

    This is the N-engine contract proof: every consumer below materializes
    its output through registry dispatch with NO consumer-side engine branch.
    """
    return {
        "gate_env": None,
        "api": "openai",
        "base_url": lambda: "http://lmstudio:1234",
        "api_key_env": None,
        "anthropic_support": False,
        "gemini_support": False,
        "profile_provider": "lmstudio-local",
        "provider_config": True,
        "health_check": None,
        "display_name": "LM Studio",
        "npm": "@ai-sdk/openai-compatible",
        "resolve_model": False,
        "context_fallback": 32768,
        "default_port": "1234",
        "chat_model_types": {"llm"},
        "gate_required": False,
        "drift_check": True,
        "details_provider_arg": False,
        "caddy_path": "/lmstudio/*",
        "port_env": "LMSTUDIO_PORT",
        "caddy_route": {"blocked": "/admin*", "allowed": "/v1/*"},
        "list_models": lambda include_cloud=False: [
            {
                "name": "lmchat",
                "provider": "lmstudio",
                "model_type": "llm",
                "capabilities": {"completion", "tools"},
                "primary_category": "llm",
                "size_gb": 0.0,
            }
        ],
        "audio_discovery": lambda: [
            {"name": "lmvoice", "provider": "lmstudio", "model_type": "audio_stt"}
        ],
        "metadata_lookup": None,
    }


def test_fleet_synthetic_engine_materializes_across_consumers(monkeypatch, tmp_path):
    """The N-engine contract: a registered engine materializes every
    consumer's output with no consumer-side engine branch."""
    fake = _fake_lmstudio_engine()
    monkeypatch.setitem(local_engines.LOCAL_ENGINES, "lmstudio", fake)
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("DOTFILES_USE_LOCAL_OMLX", None)

        # 1. Registry activation + merged discovery
        assert local_engines.engine_gate_active("lmstudio") is True
        assert "lmstudio" in local_engines.active_engines()
        pool = local_engines.merged_local_pool()
        assert any(m["name"] == "lmchat" and m["provider"] == "lmstudio" for m in pool)

        # 2. OpenCode provider block via the shared registry builder
        block = local_engines.local_provider_block("lmstudio", ["lmchat"])
        assert block["options"]["baseURL"] == "http://lmstudio:1234/v1"
        assert block["name"] == "LM Studio"
        assert "apiKey" not in block["options"]

        # 3. Pi provider via the registry-driven consumer
        configure_pi = _load_script("configure_pi", "configure-pi.py")
        pi_provider = configure_pi.build_local_provider("lmstudio", ["lmchat"])
        assert pi_provider["baseUrl"] == "http://lmstudio:1234/v1"

        # 4. Junie endpoint via the registry-driven consumer
        # (empty apiType → origin per Junie contract; the script appends
        # per-apiType paths, so also verify the OpenAICompletion emission)
        generate_profiles = _load_script(
            "generate_jetbrains_profiles", "generate-jetbrains-profiles.py"
        )
        junie = generate_profiles.build_provider_configs(
            {"providers": {"lmstudio": {}}}
        )
        assert junie["lmstudio"]["baseUrl"] == "http://lmstudio:1234"
        junie_openai = generate_profiles.build_provider_configs(
            {
                "providers": {
                    "lmstudio": {
                        "apiType": "OpenAICompletion",
                        "baseUrl": "http://lmstudio:1234/v1",
                    }
                }
            }
        )
        assert (
            junie_openai["lmstudio"]["baseUrl"]
            == "http://lmstudio:1234/v1/chat/completions"
        )

        # 5. Voice audio via the registry audio contract
        audio = local_engines.audio_models("lmstudio")
        assert [m["name"] for m in audio] == ["lmvoice"]
        voice = _load_script("configure_opencode_voice", "configure-opencode-voice.py")
        configured = voice.local_voice_provider("lmstudio/lmvoice")
        assert configured == {"endpoint": "http://lmstudio:1234/v1", "model": "lmvoice"}

        # 6. Caddy route via the registry caddy contract
        configure_caddy = _load_script("configure_caddy", "configure-caddy.py")
        route_block = configure_caddy.build_route_block(str(tmp_path))
        assert "handle_path /lmstudio/*" in route_block
        assert "reverse_proxy @lmstudio_read 127.0.0.1:1234" in route_block

        # 7. Tier filtering accepts the engine's models
        resolved = tier_resolve.resolve_roles_from_list(
            [{"name": "lmchat", "provider": "lmstudio", "size_gb": 0.0}]
        )
        assert resolved["code-gen"] == "lmstudio/lmchat"

        # 8. Provider-prefix validation derives from the registry
        assert local_engines.resolve_engine("lmstudio") is fake

        # 9. ACP winner routing through the shared resolver
        winner = local_engines.resolve_local_winner(
            [
                {"name": "lmchat", "provider": "lmstudio", "size_gb": 0.0},
                {"name": "ollama-chat", "provider": "ollama", "size_gb": 0.0},
            ]
        )
        assert winner == "lmstudio/lmchat"

        # 10. Codex profile via the registry (covered in detail by the
        # dedicated third-engine test above; assert the winner resolves here)
        assert local_engines.local_endpoint_for("lmstudio", "openai") == (
            "http://lmstudio:1234/v1",
            None,
        )

        # 11. Drift: deployed reference validated against the fake catalogue
        # (provider-aware patch returning BARE model ids, matching the real
        # deployed_engine_references contract which strips the provider prefix)
        drift = _load_script("check_model_drift", "check-model-drift.py")
        with patch.object(
            drift,
            "deployed_engine_references",
            side_effect=lambda provider: (
                {"lmchat"} if provider == "lmstudio" else set()
            ),
        ):
            violations = drift.check_local_engine_models()
        assert violations == []

    # Gate off → engine inactive everywhere
    fake["gate_env"] = "LMSTUDIO_GATE"
    with patch.dict(os.environ, {"LMSTUDIO_GATE": "0"}, clear=False):
        assert local_engines.engine_gate_active("lmstudio") is False
        assert "lmstudio" not in local_engines.active_engines()
        assert local_engines.iter_engine_models("lmstudio") == []


def test_unknown_engine_winner_falls_back_to_ollama(monkeypatch):
    acp = _load_script("configure_acp_agents", "configure-acp-agents.py")
    calls = []

    def resolve(models):
        calls.append(models)
        return (
            {"solo": "unknown/model"} if len(calls) == 1 else {"solo": "ollama/legacy"}
        )

    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "0")
    with (
        patch.object(
            acp,
            "active_engine_pools",
            return_value={
                "ollama": [{"name": "legacy", "provider": "ollama"}],
                "lmstudio": [{"name": "unknown/model", "provider": "lmstudio"}],
            },
        ),
        patch.object(tier_resolve, "resolve_roles_from_list", side_effect=resolve),
    ):
        assert acp.local_model() == "ollama/legacy"
    assert len(calls) == 2


def test_gemini_local_is_ollama_only():
    acp = _load_script("configure_acp_agents", "configure-acp-agents.py")
    assert "gemini--local" in acp.build_local_agents("ollama/chat")
    assert "gemini--local" not in acp.build_local_agents("omlx/chat")


def test_pi_omlx_provider_is_absent_when_gate_is_off():
    configure_pi = _load_script("configure_pi", "configure-pi.py")
    with patch.dict(os.environ, {"DOTFILES_RUN_OMLX_SETUP": "0"}, clear=False):
        assert configure_pi.build_local_provider("omlx", ["chat"]) is None


def test_mozart_injects_local_engine_gateways(monkeypatch):
    """N-engine contract: gate-active engines land as Mozart gateways via
    the registry, deduped by base URL."""
    mozart = _load_script("configure_mozart_router", "configure-mozart-router.py")
    gateways = {"ollama-cloud": {"baseUrl": "http://localhost:11434/v1"}}
    with (
        patch.dict(os.environ, {"DOTFILES_RUN_OMLX_SETUP": "1"}, clear=False),
        patch.dict(
            local_engines.LOCAL_ENGINES["omlx"],
            {
                "health_check": lambda: (True, "ok"),
                "base_url": lambda: "http://127.0.0.1:8000",
            },
        ),
    ):
        mozart.inject_local_engine_gateways(gateways)
    assert gateways["omlx"]["baseUrl"] == "http://127.0.0.1:8000/v1"
    assert gateways["omlx"]["adapter"] == "generic-openai"
    assert gateways["omlx"]["enabled"] is True
    # Dedupe: a gateway already pointing at the same base URL wins
    gateways2 = {"existing": {"baseUrl": "http://127.0.0.1:8000/v1"}}
    with (
        patch.dict(os.environ, {"DOTFILES_RUN_OMLX_SETUP": "1"}, clear=False),
        patch.dict(
            local_engines.LOCAL_ENGINES["omlx"],
            {
                "health_check": lambda: (True, "ok"),
                "base_url": lambda: "http://127.0.0.1:8000",
            },
        ),
    ):
        mozart.inject_local_engine_gateways(gateways2)
    assert "omlx" not in gateways2
    # Unreachable engine → skipped
    fake = dict(local_engines.LOCAL_ENGINES["omlx"])
    fake["health_check"] = lambda: (False, "down")
    monkeypatch.setitem(local_engines.LOCAL_ENGINES, "unreachable", fake)
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("DOTFILES_RUN_OMLX_SETUP", None)
        gateways3 = {}
        mozart.inject_local_engine_gateways(gateways3)
    assert "unreachable" not in gateways3


def test_sync_turboquant_kv_applies_ollama_kv_cache_type(tmp_path):
    """OLLAMA_KV_CACHE_TYPE drives oMLX per-model TurboQuant (q8_0→8, q4_0→4)."""
    settings_file = tmp_path / "model_settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "version": 1,
                "models": {
                    "m1": {
                        "turboquant_kv_enabled": False,
                        "turboquant_kv_bits": 4,
                        "turboquant_skip_last": True,
                        "unmanaged_key": "keep-me",
                    },
                    "m2": {"force_sampling": False},
                },
            }
        )
    )
    # q8_0 → 8-bit enabled; profiles without TurboQuant keys gain them;
    # unmanaged keys preserved
    env = {"OLLAMA_KV_CACHE_TYPE": "q8_0"}
    changed = local_engines.sync_turboquant_kv(settings_file, env)
    assert changed == 2
    document = json.loads(settings_file.read_text())
    m1 = document["models"]["m1"]
    assert m1["turboquant_kv_enabled"] is True
    assert m1["turboquant_kv_bits"] == 8
    assert m1["turboquant_skip_last"] is True
    assert m1["unmanaged_key"] == "keep-me"
    assert document["models"]["m2"]["turboquant_kv_enabled"] is True
    assert document["models"]["m2"]["turboquant_kv_bits"] == 8
    # Idempotent: re-run with the same env → no changes
    assert local_engines.sync_turboquant_kv(settings_file, env) == 0
    # q4_0 → 4-bit (both models flip)
    assert (
        local_engines.sync_turboquant_kv(
            settings_file, {"OLLAMA_KV_CACHE_TYPE": "q4_0"}
        )
        == 2
    )
    assert (
        json.loads(settings_file.read_text())["models"]["m1"]["turboquant_kv_bits"] == 4
    )


def test_sync_turboquant_kv_untouched_without_kv_type(tmp_path):
    """Unset or f16 OLLAMA_KV_CACHE_TYPE leaves per-model settings alone."""
    settings_file = tmp_path / "model_settings.json"
    original = {
        "version": 1,
        "models": {"m1": {"turboquant_kv_enabled": False, "custom": 1}},
    }
    settings_file.write_text(json.dumps(original))
    assert local_engines.sync_turboquant_kv(settings_file, {}) == 0
    assert (
        local_engines.sync_turboquant_kv(settings_file, {"OLLAMA_KV_CACHE_TYPE": "f16"})
        == 0
    )
    assert json.loads(settings_file.read_text()) == original


def test_junie_pool_profile_specs_gate_off_unchanged(monkeypatch):
    """Gate-off equivalence: no pool profile specs when no engine is active."""
    gen = _load_script("gen_profiles", "generate-jetbrains-profiles.py")
    specs = [("local", "ollama/m", "", "ollama", "ollama")]
    gen.append_pool_profile_specs(specs, pools={})
    assert len(specs) == 1


def test_junie_pool_profile_specs_one_per_model(monkeypatch):
    """N-engine contract: every chat-capable pool model becomes a spec."""
    gen = _load_script("gen_profiles", "generate-jetbrains-profiles.py")
    pools = {
        "lmstudio": [
            {
                "name": "chat-a",
                "provider": "lmstudio",
                "model_type": "llm",
                "primary_category": "llm",
            },
            {
                "name": "chat-b",
                "provider": "lmstudio",
                "model_type": "vlm",
                "primary_category": "vlm",
            },
            {
                "name": "embed-x",
                "provider": "lmstudio",
                "model_type": "embedding",
                "primary_category": "embedding",
            },
        ],
        "ollama": [{"name": "legacy", "provider": "ollama"}],
    }
    fake = {"resolve_model": False}
    monkeypatch.setitem(local_engines.LOCAL_ENGINES, "lmstudio", fake)
    monkeypatch.setitem(local_engines.LOCAL_ENGINES, "ollama", {"resolve_model": True})
    specs = []
    gen.append_pool_profile_specs(specs, pools=pools)
    names = [s[0] for s in specs]
    assert "local-lmstudio-chat-a" in names
    assert "local-lmstudio-chat-b" in names
    assert "local-ollama-legacy" not in names  # legacy pool skipped
    chat_a = next(s for s in specs if s[0] == "local-lmstudio-chat-a")
    assert chat_a[1] == "lmstudio/chat-a"
    assert chat_a[2] == "lmstudio/chat-b"
    chat_b = next(s for s in specs if s[0] == "local-lmstudio-chat-b")
    assert chat_b[2] == ""  # last chat model: no faster
    assert not any("embed" in n for n in names)  # embedding excluded


def test_extract_param_count_mlx_style_names():
    """First size token wins; quant suffixes and decimals never match."""
    from tier_resolve import extract_param_count

    cases = {
        "Qwen3.8-27B-MLX-4bit": 27,
        "Ornith-1.5-35B-A3B-MLX-4bit": 35,
        "gemma-4-12B-it-MLX-8bit": 12,
        "qwen3.8:27b-mlx": 27,
        "gemma4:12b-mxfp8": 12,
        "qwen2.5-coder:7b": 7,
        "gpt-oss:20b": 20,
        "Qwen3.8": 0,  # no size token
    }
    for name, expected in cases.items():
        assert extract_param_count(name) == expected, f"{name} -> {expected}"


def test_omlx_metadata_infers_moe_from_name(monkeypatch):
    """A3B-style MoE markers set is_moe; dense names get explicit False."""
    import omlx

    monkeypatch.setattr(
        omlx,
        "get_omlx_model_status",
        lambda name: {"model_type": "llm", "estimated_size": 1},
    )
    details = tier_resolve.get_model_details("Ornith-1.5-35B-A3B-MLX-4bit", "omlx")
    assert details["is_moe"] is True
    details = tier_resolve.get_model_details("Qwen3.8-27B-MLX-4bit", "omlx")
    assert details["is_moe"] is False


def test_classification_live_omlx_pool_shape():
    """Live-pool fixture: reasoning=Qwen, lightweight/vision=gemma, solo=Qwen."""
    models = [
        {
            "name": "Qwen3.8-27B-MLX-4bit",
            "size_gb": 16.86,
            "provider": "omlx",
            "model_type": "vlm",
            "capabilities": ["completion", "thinking", "tools", "vision"],
        },
        {
            "name": "gemma-4-12B-it-MLX-8bit",
            "size_gb": 13.35,
            "provider": "omlx",
            "model_type": "vlm",
            "capabilities": ["completion", "tools", "vision"],
        },
        {
            "name": "Ornith-1.5-35B-A3B-MLX-4bit",
            "size_gb": 20.48,
            "provider": "omlx",
            "model_type": "llm",
            "capabilities": ["completion", "thinking", "tools"],
        },
        {
            "name": "Ornith-1.5-9B-MLX-8bit",
            "size_gb": 9.5,
            "provider": "omlx",
            "model_type": "llm",
            "capabilities": ["completion", "thinking", "tools"],
        },
    ]

    def fake_details(model_name, provider="ollama"):
        caps = next(m["capabilities"] for m in models if m["name"] == model_name)
        moe = "A3B" in model_name
        return {
            "param_count": tier_resolve.extract_param_count(model_name) or None,
            "capabilities": caps,
            "architecture": None,
            "embedding_length": None,
            "context_length": None,
            "quantization": None,
            "is_moe": moe,
            "model_type": "llm",
        }

    with patch.object(tier_resolve, "get_model_details", side_effect=fake_details):
        resolved = tier_resolve.resolve_roles_from_list(models)
    assert resolved["reasoning"] == "omlx/Qwen3.8-27B-MLX-4bit"
    assert resolved["lightweight"] == "omlx/gemma-4-12B-it-MLX-8bit"
    assert resolved["vision"] == "omlx/gemma-4-12B-it-MLX-8bit"
    assert resolved["solo"] == "omlx/Qwen3.8-27B-MLX-4bit"


def test_merged_local_pool_prefers_omlx_equivalents(monkeypatch):
    """Same (family, params) identity: omlx entry replaces ollama; distinct kept."""
    fake_ollama = [
        {"name": "gemma4:12b-mxfp8", "size_gb": 8.1, "provider": "ollama"},
        {"name": "qwen2.5-coder:7b", "size_gb": 4.7, "provider": "ollama"},
    ]
    fake_omlx = [
        {"name": "gemma-4-12B-it-MLX-8bit", "size_gb": 13.35, "provider": "omlx"},
        {"name": "Qwen3.8-27B-MLX-4bit", "size_gb": 16.86, "provider": "omlx"},
    ]
    monkeypatch.setattr(
        local_engines,
        "iter_engine_models",
        lambda p, ic=False: fake_ollama if p == "ollama" else fake_omlx,
    )
    monkeypatch.setattr(local_engines, "active_engines", lambda: ["ollama", "omlx"])
    monkeypatch.setattr(local_engines, "engine_gate_active", lambda p: True)
    pool = local_engines.merged_local_pool()
    names = [m["name"] for m in pool]
    assert "gemma4:12b-mxfp8" not in names
    assert "gemma-4-12B-it-MLX-8bit" in names
    assert "qwen2.5-coder:7b" in names
    assert "Qwen3.8-27B-MLX-4bit" in names


def test_pi_role_models_env_bridge(monkeypatch):
    """DOTFILES_ROLE_MODELS bridges into role overrides like the flag form."""
    pi = _load_script("pi_cfg", "configure-pi.py")
    monkeypatch.setenv("DOTFILES_ROLE_MODELS", "observer=omlx/gemma-4-12B-it-MLX-8bit")
    monkeypatch.delenv("DOTFILES_LOCAL_FALLBACK_ROLES", raising=False)
    assert pi._role_models_from_env() == ["observer=omlx/gemma-4-12B-it-MLX-8bit"]

    monkeypatch.delenv("DOTFILES_ROLE_MODELS", raising=False)
    monkeypatch.setenv(
        "DOTFILES_LOCAL_FALLBACK_ROLES", "observer=ollama/gemma4:12b-mxfp8"
    )
    assert pi._role_models_from_env() == ["observer=ollama/gemma4:12b-mxfp8"]

    monkeypatch.delenv("DOTFILES_LOCAL_FALLBACK_ROLES", raising=False)
    assert pi._role_models_from_env() is None
