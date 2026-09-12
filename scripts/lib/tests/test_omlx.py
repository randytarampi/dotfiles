import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import discover_models
import local_engines
import omlx
import tier_resolve


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


def test_verify_omlx_settings_reports_invalid_memory_guard():
    verify = _load_script("verify_config", "verify-config.py")
    settings = local_engines.merge_omlx_settings({})
    settings["memory"]["memory_guard_tier"] = "weird"
    assert "memory.memory_guard_tier" in " ".join(
        verify.validate_omlx_settings(settings)
    )


def test_discovery_merges_omlx_and_ollama_with_ollama_collision_wins():
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
        ("shared", "ollama"),
        ("ollama-only", "ollama"),
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
            },
        ),
    ):
        provider = configure_opencode.build_local_provider("omlx", models)
    assert provider["options"]["baseURL"] == "http://omlx:8000/v1"
    assert provider["models"] == {"qwen": {"name": "qwen"}}

    with (patch.dict(os.environ, {"DOTFILES_RUN_OMLX_SETUP": "0"}, clear=False),):
        assert configure_opencode.build_local_provider("omlx", models) is None


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
            "list_local_ollama_models",
            return_value=[
                {"name": "legacy", "provider": "ollama"},
                {"name": "winner", "provider": "omlx"},
            ],
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
            "list_local_ollama_models",
            return_value=[
                {"name": "unknown/model", "provider": "lmstudio"},
                {"name": "legacy", "provider": "ollama"},
            ],
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
