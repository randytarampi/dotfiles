from unittest.mock import patch

import discover_models
import omlx
import tier_resolve


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
    settings = omlx.merge_omlx_settings(
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
    assert settings["auth"]["api_key"] == "secret"
    assert settings["huggingface"]["endpoint"] == "https://hf.example"
    assert settings["custom"] == {"keep": True}


def test_merge_omlx_settings_off_disables_prefill_guard():
    settings = omlx.merge_omlx_settings(
        {"memory": {"memory_guard_tier": "safe", "custom": True}},
        {"OMLX_MEMORY_GUARD": "off"},
    )
    assert "memory_guard_tier" not in settings["memory"]
    assert settings["memory"]["prefill_memory_guard"] is False
    assert settings["memory"]["custom"] is True
    assert "auth" not in settings
    assert "huggingface" not in settings


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
        patch.object(discover_models, "is_omlx_configured", return_value=True),
        patch.object(
            discover_models, "check_omlx_daemon", return_value=(True, "HTTP 200")
        ),
        patch.object(discover_models, "list_omlx_models", return_value=omlx_models),
    ):
        run.return_value.stdout = ollama_output
        models = discover_models.list_local_ollama_models()
    assert [(model["name"], model["provider"]) for model in models] == [
        ("shared", "ollama"),
        ("ollama-only", "ollama"),
        ("omlx-only", "omlx"),
    ]


def test_discovery_keeps_ollama_models_when_omlx_merge_fails():
    ollama_output = "NAME ID SIZE MODIFIED\nollama-only abc 1 GB now\n"
    with (
        patch.object(discover_models, "find_ollama", return_value="ollama"),
        patch.object(discover_models.subprocess, "run") as run,
        patch.object(discover_models, "is_omlx_configured", return_value=True),
        patch.object(
            discover_models, "check_omlx_daemon", return_value=(True, "HTTP 200")
        ),
        patch.object(
            discover_models, "list_omlx_models", side_effect=RuntimeError("omlx down")
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
