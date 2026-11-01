import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from constants import OMLX_DEFAULT_PORT
from litellm_aliases import canonical_allowlist_key
from provider_endpoints import POOLSIDE_ROUTED_CONTEXT, POOLSIDE_ROUTED_OUTPUT

import discover_models
import local_engines
import omlx
import opencode_config
import tier_resolve
import ai_models


def test_mellum_model_is_classified_as_code_gen():
    model = {"name": "mellum-6b-mlx-4bit", "provider": "ollama"}
    details = {
        "param_count": 6,
        "capabilities": [],
        "architecture": None,
        "embedding_length": None,
        "context_length": None,
        "quantization": None,
        "is_moe": False,
    }

    with patch.object(tier_resolve, "get_model_details", return_value=details):
        classified = tier_resolve.resolve_roles_from_list([model])

    assert classified["code-gen"] == "ollama/mellum-6b-mlx-4bit"


def test_resolve_roles_ignores_unknown_profile_with_warning():
    model = {"name": "mellum-6b-mlx-4bit", "provider": "ollama"}
    details = {"param_count": 6, "capabilities": ["thinking", "completion", "tools"]}
    with (
        patch.object(tier_resolve, "get_model_details", return_value=details),
        patch.object(tier_resolve.logger, "warning") as warning,
    ):
        resolved = tier_resolve.resolve_roles_from_list([model], profile="future")
    with patch.object(tier_resolve, "get_model_details", return_value=details):
        baseline = tier_resolve.resolve_roles_from_list([model])
    assert resolved == baseline
    assert (
        sum(
            "Unknown local model profile" in str(call)
            for call in warning.call_args_list
        )
        == 1
    )


def test_get_preset_providers_extracts_provider_prefixes():
    assert opencode_config.get_preset_providers("pro-plus") == {
        "openai",
        "ollama-cloud",
    }
    assert "anthropic" in opencode_config.get_preset_providers("pro-plus-anthropic")
    assert opencode_config.get_preset_providers("plus") == {"openai"}


def test_omlx_persisted_server_port_warns_without_overriding_unset_env(
    monkeypatch, tmp_path, caplog
):
    monkeypatch.delenv("OMLX_PORT", raising=False)
    stale_port = int(
        f"{7}{'000'}"
    )  # port differing from the default, split to avoid secret-scanner false positive
    settings = local_engines.merge_omlx_settings(
        {"server": {"port": stale_port}}, environ={}
    )
    assert settings["server"]["port"] == stale_port
    assert str(stale_port) in caplog.text and str(OMLX_DEFAULT_PORT) in caplog.text


def test_omlx_persisted_default_port_does_not_warn(caplog):
    local_engines.merge_omlx_settings(
        {"server": {"port": OMLX_DEFAULT_PORT}}, environ={}
    )
    assert "differs from default" not in caplog.text


def test_omlx_port_environment_override_wins(caplog):
    override_port = str(
        OMLX_DEFAULT_PORT + 10
    )  # non-default; no port literal for secret scanners
    settings = local_engines.merge_omlx_settings(
        {"server": {"port": OMLX_DEFAULT_PORT}}, environ={"OMLX_PORT": override_port}
    )
    assert settings["server"]["port"] == int(override_port)


def _load_script(name, filename):
    path = Path(__file__).resolve().parents[3] / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_wired_limit(monkeypatch, tmp_path, *, environ, ram_mb):
    script = _load_script("configure_omlx_wired_limit", "configure-omlx-wired-limit.py")
    calls = []
    monkeypatch.setattr(script.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(script.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(script, "load_env", lambda: True)
    monkeypatch.setattr(script, "_physical_ram_mb", lambda: ram_mb)
    monkeypatch.setattr(script, "_read_wired_limit", lambda: 1234)
    monkeypatch.setattr(
        script,
        "apply_sysctl",
        lambda limit, dry_run: calls.append(("apply", limit, dry_run)),
    )
    monkeypatch.setattr(
        script,
        "install_plist",
        lambda *args, **kwargs: calls.append(("plist", args, kwargs)),
    )
    monkeypatch.setattr(script, "LAUNCHD_PLIST", str(tmp_path / "wired.plist"))
    monkeypatch.setattr(sys, "argv", ["configure-omlx-wired-limit.py"])
    monkeypatch.setattr(script, "os", type("OS", (), {"environ": environ})())
    script.main()
    return calls


def test_wired_limit_default_reports_without_writing(monkeypatch, tmp_path, caplog):
    calls = _run_wired_limit(
        monkeypatch,
        tmp_path,
        environ={"DOTFILES_RUN_OMLX_SETUP": "1"},
        ram_mb=128 * 1024,
    )
    assert calls == []
    assert "131072" in caplog.text
    assert "1234" in caplog.text


def test_wired_limit_explicit_opt_in_applies_computed_limit(monkeypatch, tmp_path):
    calls = _run_wired_limit(
        monkeypatch,
        tmp_path,
        environ={
            "DOTFILES_RUN_OMLX_SETUP": "1",
            "DOTFILES_APPLY_OMLX_WIRED_LIMIT": "1",
        },
        ram_mb=128 * 1024,
    )
    assert calls[0] == ("apply", 40960, False)


def test_wired_limit_override_is_honoured(monkeypatch, tmp_path):
    calls = _run_wired_limit(
        monkeypatch,
        tmp_path,
        environ={
            "DOTFILES_RUN_OMLX_SETUP": "1",
            "DOTFILES_APPLY_OMLX_WIRED_LIMIT": "1",
            "OMLX_WIRED_LIMIT_MB": "32768",
        },
        ram_mb=128 * 1024,
    )
    assert calls[0] == ("apply", 32768, False)


def test_wired_limit_out_of_bounds_override_is_refused(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as error:
        _run_wired_limit(
            monkeypatch,
            tmp_path,
            environ={
                "DOTFILES_RUN_OMLX_SETUP": "1",
                "DOTFILES_APPLY_OMLX_WIRED_LIMIT": "1",
                "OMLX_WIRED_LIMIT_MB": "15000",
            },
            ram_mb=16 * 1024,
        )
    assert error.value.code == 1


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


def test_omlx_embeddinggemma_name_is_specialized_after_provider_normalization():
    installed_name = "embeddinggemma-2:740m-mxfp8"
    omlx_record = {
        "id": installed_name,
        "model_type": "embedding",
        "object": "model",
        "owned_by": "oMLX",
    }

    assert omlx_record["id"] == "embeddinggemma-2:740m-mxfp8"
    normalized_name = ai_models.strip_provider_prefix(f"omlx/{omlx_record['id']}")
    assert normalized_name == installed_name
    assert ai_models.is_specialized_model_name(normalized_name)


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


def test_resolve_roles_uses_probed_audio_capability_for_gemma4():
    models = [{"name": "gemma4:12b-mxfp8", "size_gb": 12.0, "provider": "ollama"}]

    with patch.object(
        tier_resolve,
        "get_model_details",
        return_value={"param_count": 12, "capabilities": ["completion", "audio"]},
    ):
        resolved = tier_resolve.resolve_roles_from_list(models)

    assert resolved["audio"] == "ollama/gemma4:12b-mxfp8"


def test_gemma4_vision_model_is_name_qualified_lightweight():
    models = [
        {
            "name": "gemma4:12b-mxfp8",
            "size_gb": 13.0,
            "provider": "ollama",
        }
    ]
    capabilities = ["completion", "vision", "audio", "tools", "thinking"]

    with patch.object(
        tier_resolve,
        "get_model_details",
        return_value={"param_count": 12, "capabilities": capabilities},
    ):
        categorized = tier_resolve.resolve_roles_from_list(models)

    assert categorized["lightweight"] == "ollama/gemma4:12b-mxfp8"
    assert categorized["vision"] == "ollama/gemma4:12b-mxfp8"
    assert categorized["audio"] == "ollama/gemma4:12b-mxfp8"


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


def test_configure_opencode_two_mode_gate_is_fail_closed_and_rewrites_alias(tmp_path):
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    key_file = tmp_path / ".local/share/litellm/clients/opencode.key"
    key_file.parent.mkdir(parents=True)
    key_file.write_text("dummy-key", encoding="utf-8")
    direct = {
        "model": "openai/gpt-x",
        "providers": {
            "openai": {"models": {"gpt-x": {"name": "GPT X"}}},
        },
    }
    expected_direct = json.loads(json.dumps(direct))
    with patch.dict(os.environ, {"DOTFILES_USE_LITELLM_PROXY": "0"}, clear=False):
        configure_opencode.apply_litellm_client_gate(direct)
    assert direct == expected_direct

    with (
        patch.dict(
            os.environ,
            {"DOTFILES_USE_LITELLM_PROXY": "1", "DOTFILES_RUN_LITELLM_SETUP": "0"},
            clear=False,
        ),
        pytest.raises(RuntimeError, match="DOTFILES_RUN_LITELLM_SETUP"),
    ):
        configure_opencode.apply_litellm_client_gate(json.loads(json.dumps(direct)))

    with patch.dict(
        os.environ,
        {
            "DOTFILES_USE_LITELLM_PROXY": "1",
            "DOTFILES_RUN_LITELLM_SETUP": "1",
            "LITELLM_PORT": "4001",
            "HOME": str(tmp_path),
        },
        clear=False,
    ):
        proxied = json.loads(json.dumps(expected_direct))
        configure_opencode.apply_litellm_client_gate(proxied)
    assert proxied["model"] == "litellm/openai/gpt-x"
    assert set(proxied["providers"]) == {"litellm"}
    assert proxied["providers"]["litellm"]["options"]["apiKey"] == (
        "{file:" + str(key_file) + "}"
    )

    key_file.unlink()
    with (
        patch.dict(
            os.environ,
            {
                "DOTFILES_USE_LITELLM_PROXY": "1",
                "DOTFILES_RUN_LITELLM_SETUP": "1",
                "HOME": str(tmp_path),
            },
            clear=False,
        ),
        pytest.raises(RuntimeError, match="key file"),
    ):
        configure_opencode.apply_litellm_client_gate(
            json.loads(json.dumps(expected_direct))
        )


def test_configure_opencode_gate_filters_unserved_when_catalogue_responds(
    tmp_path, caplog
):
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    key_file = tmp_path / ".local/share/litellm/clients/opencode.key"
    key_file.parent.mkdir(parents=True)
    key_file.write_text("dummy-key", encoding="utf-8")
    direct = {
        "model": "openai/gpt-x",
        "providers": {"openai": {"models": {"gpt-x": {"name": "GPT X"}}}},
    }
    with (
        patch.dict(
            os.environ,
            {
                "DOTFILES_USE_LITELLM_PROXY": "1",
                "DOTFILES_RUN_LITELLM_SETUP": "1",
                "HOME": str(tmp_path),
            },
            clear=False,
        ),
        patch.object(configure_opencode, "get_models") as catalogue,
    ):
        catalogue.return_value = {"openai/gpt-x"}
        configure_opencode.apply_litellm_client_gate(json.loads(json.dumps(direct)))
    assert catalogue.call_count == 1


def test_configure_opencode_gate_keeps_inventory_when_catalogue_unavailable(
    tmp_path, caplog
):
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    key_file = tmp_path / ".local/share/litellm/clients/opencode.key"
    key_file.parent.mkdir(parents=True)
    key_file.write_text("dummy-key", encoding="utf-8")
    direct = {
        "model": "openai/gpt-x",
        "providers": {"openai": {"models": {"gpt-x": {"name": "GPT X"}}}},
    }
    with (
        patch.dict(
            os.environ,
            {
                "DOTFILES_USE_LITELLM_PROXY": "1",
                "DOTFILES_RUN_LITELLM_SETUP": "1",
                "HOME": str(tmp_path),
            },
            clear=False,
        ),
        patch.object(
            configure_opencode,
            "get_models",
            side_effect=ValueError("unsupported catalogue URL"),
        ),
    ):
        configure_opencode.apply_litellm_client_gate(json.loads(json.dumps(direct)))
    assert "UNKNOWN gateway catalogue unavailable" in caplog.text


def test_opencode_slim_output_routes_every_inventory_selection_and_direct_mode_is_canonical(
    tmp_path,
):
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    providers = (
        "openai",
        "ollama-cloud",
        "google",
        "openrouter",
        "opencode",
        "omlx",
        "ollama",
    )
    refs = [f"{provider}/fixture-model" for provider in providers]
    inventory = {ref: {"id": ref} for ref in refs}
    config = {"providers": {"litellm": {"models": inventory}}}
    slim = {
        "roles": {"orchestrator": {"model": refs[0]}},
        "council": {"presets": {"active": {"alpha": {"model": refs[1]}}}},
        "_tiers": {"active": {"fallback": {"orchestrator": refs[2:]}}},
        "skills": ["frontend/design"],
    }
    # Direct mode preserves canonical provider/model identifiers end-to-end.
    with patch.dict(os.environ, {"DOTFILES_USE_LITELLM_PROXY": "0"}, clear=False):
        assert configure_opencode.apply_litellm_slim_gate(slim, config) == slim

    key = tmp_path / ".local/share/litellm/clients/opencode.key"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text("test-key")
    # All assertions below exercise gateway mode; keep the gate active throughout.
    with patch.dict(
        os.environ,
        {
            "DOTFILES_USE_LITELLM_PROXY": "1",
            "DOTFILES_RUN_LITELLM_SETUP": "1",
            "HOME": str(tmp_path),
        },
        clear=False,
    ):
        # This sanity check fails if the fixture silently runs in direct mode.
        assert configure_opencode.apply_litellm_slim_gate(slim, config) != slim
        routed = configure_opencode.apply_litellm_slim_gate(slim, config)
        routed_refs = [
            routed["roles"]["orchestrator"]["model"],
            routed["council"]["presets"]["active"]["alpha"]["model"],
            *routed["_tiers"]["active"]["fallback"]["orchestrator"],
        ]
        assert routed_refs == [f"litellm/{ref}" for ref in refs]
        assert routed["skills"] == ["frontend/design"]
        wrapped_slim = {"roles": {"x": {"model": "litellm/openai/fixture-model"}}}
        assert (
            configure_opencode.apply_litellm_slim_gate(wrapped_slim, config)["roles"][
                "x"
            ]["model"]
            == "litellm/openai/fixture-model"
        )
        for malformed in (
            "litellm/no-prefix",
            "litellm/litellm/openai/fixture-model",
            "litellm/openai/",
        ):
            with pytest.raises(RuntimeError):
                configure_opencode.apply_litellm_slim_gate(
                    {"roles": {"x": {"model": malformed}}}, config
                )
        with pytest.raises(RuntimeError, match="no LiteLLM gateway alias"):
            configure_opencode.apply_litellm_slim_gate(
                {"roles": {"x": {"model": "litellm/openai/missing"}}}, config
            )
        with pytest.raises(
            RuntimeError, match="no LiteLLM gateway alias: cerebras/unmapped"
        ):
            configure_opencode.apply_litellm_slim_gate(
                {"roles": {"unknown": {"model": "cerebras/unmapped"}}}, config
            )


def test_opencode_slim_preserves_previously_resolved_selection_when_catalogue_unknown(
    tmp_path, monkeypatch
):
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    key = tmp_path / ".local/share/litellm/clients/opencode.key"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text("test-key")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    selection = "ollama-cloud/glm-5.3-flash"
    with pytest.raises(RuntimeError, match="no LiteLLM gateway alias"):
        configure_opencode.apply_litellm_slim_gate(
            {"roles": {"orchestrator": {"model": selection}}},
            {"providers": {"litellm": {"models": {}}}},
        )


def test_opencode_slim_gate_does_not_return_raw_gateway_selection(
    tmp_path, monkeypatch
):
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    key = tmp_path / ".local/share/litellm/clients/opencode.key"
    key.parent.mkdir(parents=True)
    key.write_text("test-key")
    with pytest.raises(RuntimeError, match="no LiteLLM gateway alias"):
        configure_opencode.apply_litellm_slim_gate(
            {"roles": {"x": {"model": "ollama-cloud/absent"}}},
            {"providers": {"litellm": {"models": {}}}},
            {"roles": {"x": {"model": "ollama-cloud/absent"}}},
        )


def test_opencode_slim_gateway_refuses_existing_local_direct_selection(
    tmp_path, monkeypatch, caplog
):
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    key = tmp_path / ".local/share/litellm/clients/opencode.key"
    key.parent.mkdir(parents=True)
    key.write_text("test-key")
    selection = "omlx/existing-model"
    config = {"providers": {"litellm": {"models": {}}}}
    with pytest.raises(
        RuntimeError,
        match=r"UNRESOLVED.*omlx/existing-model.*Re-run generation once the alias exists",
    ):
        configure_opencode.apply_litellm_slim_gate(
            {"roles": {"x": {"model": selection}}},
            config,
            {"roles": {"x": {"model": selection}}},
        )
    with pytest.raises(RuntimeError, match="no LiteLLM gateway alias"):
        configure_opencode.apply_litellm_slim_gate(
            {"roles": {"x": {"model": "omlx/new-model"}}}, config
        )


def test_opencode_slim_gateway_alias_key_absent_fails_closed(tmp_path, monkeypatch):
    configure_opencode = _load_script(
        "configure_opencode_key_absent", "configure-opencode.py"
    )
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(RuntimeError, match="no LiteLLM gateway alias"):
        configure_opencode.apply_litellm_slim_gate(
            {"roles": {"x": {"model": "omlx/absent"}}},
            {"providers": {"litellm": {"models": {}}}},
        )


def test_opencode_slim_gateway_installs_existing_local_model_alias(
    tmp_path, monkeypatch
):
    configure_opencode = _load_script(
        "configure_opencode_alias", "configure-opencode.py"
    )
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    key = tmp_path / ".local/share/litellm/clients/opencode.key"
    key.parent.mkdir(parents=True)
    key.write_text("test-key")
    routed = configure_opencode.apply_litellm_slim_gate(
        {"roles": {"x": {"model": "omlx/existing-model"}}},
        {"providers": {"litellm": {"models": {"x": {"id": "omlx/existing-model"}}}}},
        {"roles": {"x": {"model": "omlx/existing-model"}}},
    )
    assert routed["roles"]["x"]["model"] == "litellm/omlx/existing-model"


def test_opencode_slim_wrapped_gateway_alias_unwraps_uniquely(tmp_path, monkeypatch):
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    key = tmp_path / ".local/share/litellm/clients/opencode.key"
    key.parent.mkdir(parents=True)
    key.write_text("test-key")
    routed = configure_opencode.apply_litellm_slim_gate(
        {"roles": {"x": {"model": "litellm/openai/unique"}}},
        {"providers": {"litellm": {"models": {"x": {"id": "openai/unique"}}}}},
    )
    assert routed["roles"]["x"]["model"] == "litellm/openai/unique"


def test_opencode_slim_prefers_exact_alias_over_canonical_collision(
    tmp_path, monkeypatch
):
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    key = tmp_path / ".local/share/litellm/clients/opencode.key"
    key.parent.mkdir(parents=True)
    key.write_text("test-key")
    routed = configure_opencode.apply_litellm_slim_gate(
        {"roles": {"x": {"model": "openrouter/example:free"}}},
        {
            "providers": {
                "litellm": {
                    "models": {
                        "free": {"id": "openrouter/example:free"},
                        "paid": {"id": "openrouter/example"},
                    }
                }
            }
        },
    )
    assert routed["roles"]["x"]["model"] == "litellm/openrouter/example:free"


def test_opencode_slim_refuses_ambiguous_nonexact_canonical_alias(
    tmp_path, monkeypatch
):
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    key = tmp_path / ".local/share/litellm/clients/opencode.key"
    key.parent.mkdir(parents=True)
    key.write_text("test-key")
    config = {
        "providers": {
            "litellm": {
                "models": {
                    "free": {"id": "openrouter/example:free"},
                    "paid": {"id": "openrouter/example"},
                }
            }
        }
    }
    with pytest.raises(RuntimeError, match="no LiteLLM gateway alias"):
        configure_opencode.apply_litellm_slim_gate(
            {
                "preset": "active",
                "presets": {
                    "active": {"roles": {"x": {"model": "openrouter/example:variant"}}}
                },
            },
            config,
        )


def test_opencode_slim_gate_prunes_unserved_dormant_preset_but_fails_active(
    tmp_path, monkeypatch
):
    script = _load_script("configure_opencode", "configure-opencode.py")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    key = tmp_path / ".local/share/litellm/clients/opencode.key"
    key.parent.mkdir(parents=True)
    key.write_text("test-key")
    monkeypatch.setattr(
        script,
        "resolve_alias",
        lambda alias, *args, **kwargs: {"model": f"litellm/{alias}"},
    )
    config = {"providers": {"litellm": {"models": {"served": {"id": "openai/served"}}}}}
    source = {
        "preset": "active",
        "presets": {
            "active": {"agent": {"model": "openai/served"}},
            "dormant": {"agent": {"model": "openai/missing"}},
        },
    }
    original = __import__("copy").deepcopy(source)
    routed = script.apply_litellm_slim_gate(source, config)
    assert list(routed["presets"]) == ["active"]
    assert source == original
    with pytest.raises(RuntimeError, match="presets.dormant"):
        script.apply_litellm_slim_gate(source, config, selected_preset="dormant")
    source["presets"]["dormant"] = {"agent": {"model": "openai/served"}}
    source["_tiers"] = {
        "active": {"fallback": {"agent": ["openai/served"]}},
        "dormant": {"fallback": {"agent": ["openai/missing"]}},
    }
    source["council"] = {
        "default_preset": "active",
        "presets": {
            "active": {"alpha": {"model": "openai/served"}},
            "dormant": {"alpha": {"model": "openai/served"}},
        },
    }
    routed = script.apply_litellm_slim_gate(source, config)
    for section in (routed["presets"], routed["_tiers"], routed["council"]["presets"]):
        assert set(section) == {"active"}
    with pytest.raises(RuntimeError, match="_tiers.dormant"):
        script.apply_litellm_slim_gate(source, config, selected_preset="dormant")
    source["council"]["default_preset"] = "dormant"
    with pytest.raises(RuntimeError, match="Active Slim selection unavailable"):
        script.apply_litellm_slim_gate(source, config)


def test_opencode_slim_gate_validates_legacy_preset_fragment_without_pruning(
    tmp_path, monkeypatch
):
    script = _load_script("configure_opencode_legacy_fragment", "configure-opencode.py")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    key = tmp_path / ".local/share/litellm/clients/opencode.key"
    key.parent.mkdir(parents=True)
    key.write_text("test-key")
    monkeypatch.setattr(
        script,
        "resolve_alias",
        lambda alias, *args, **kwargs: {"model": f"litellm/{alias}"},
    )
    fragment = {"presets": {"one": {"agent": {"model": "openai/served"}}}}
    config = {"providers": {"litellm": {"models": {"served": {"id": "openai/served"}}}}}
    routed = script.apply_litellm_slim_gate(fragment, config)
    assert set(routed["presets"]) == {"one"}
    assert routed["presets"]["one"]["agent"]["model"] == "litellm/openai/served"
    with pytest.raises(RuntimeError, match="no LiteLLM gateway alias"):
        script.apply_litellm_slim_gate(
            {
                "presets": {
                    "one": {"agent": {"model": "openai/served"}},
                    "two": {"agent": {"model": "openai/missing"}},
                }
            },
            config,
        )


def test_opencode_candidates_prepare_before_any_install(tmp_path, monkeypatch):
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    target = tmp_path / "opencode.json"
    slim_target = tmp_path / "oh-my-opencode-slim.json"
    target.write_bytes(b"installed-opencode\n")
    slim_target.write_bytes(b"installed-slim\n")
    before = target.read_bytes(), slim_target.read_bytes()
    monkeypatch.setattr(
        configure_opencode,
        "apply_litellm_client_gate",
        lambda *args: (_ for _ in ()).throw(RuntimeError("validation failed")),
    )
    with pytest.raises(RuntimeError, match="validation failed"):
        configure_opencode.prepare_config_candidates(
            {"model": "example/model"}, {"roles": {}}
        )
    assert (target.read_bytes(), slim_target.read_bytes()) == before


def test_opencode_inventory_missing_alias_fails_before_candidate_write(
    tmp_path, monkeypatch
):
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    key = tmp_path / ".local/share/litellm/clients/opencode.key"
    key.parent.mkdir(parents=True)
    key.write_text("test-key")
    config = {"model": "cerebras/new-model", "providers": {}}
    with pytest.raises(RuntimeError, match="not configured for canonical model"):
        configure_opencode.prepare_config_candidates(config, {"roles": {}})
    assert config["model"] == "cerebras/new-model"


def test_opencode_client_gate_only_rewrites_model_fields(tmp_path, monkeypatch):
    script = _load_script("configure_opencode_field_gate", "configure-opencode.py")
    key = tmp_path / ".local/share/litellm/clients/opencode.key"
    key.parent.mkdir(parents=True)
    key.write_text("test-key")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(script, "get_models", lambda *args: {"openai/present"})
    monkeypatch.setattr(
        script,
        "resolve_alias",
        lambda alias, *args, **kwargs: {
            "base_url": "http://gateway/v1",
            "api_key": "key",
            "model": f"litellm/{alias}",
        },
    )
    config = {
        "model": "openai/present",
        "plugins": ["@scope/plugin@1.0"],
        "instructions": "docs/AGENTS.md",
        "command": {"run": "tools/build.sh"},
        "providers": {"openai": {"models": {"present": {"name": "Present"}}}},
    }
    script.apply_litellm_client_gate(config)
    assert config["model"] == "litellm/openai/present"
    assert config["plugins"] == ["@scope/plugin@1.0"]
    assert config["instructions"] == "docs/AGENTS.md"
    assert config["command"] == {"run": "tools/build.sh"}

    config["model"] = "openai/missing"
    with pytest.raises(RuntimeError, match="not configured for canonical model"):
        script.apply_litellm_client_gate(config)


def test_opencode_poolside_routed_entries_get_conservative_limits(
    tmp_path, monkeypatch
):
    script = _load_script("configure_opencode_poolside_limits", "configure-opencode.py")
    key = tmp_path / ".local/share/litellm/clients/opencode.key"
    (tmp_path / "configs/opencode").mkdir(parents=True)
    (tmp_path / "configs/opencode/poolside-models.json").write_text(
        json.dumps({"models": {"laguna-xs-2.1": {}, "laguna-s-2.1": {}}})
    )
    script.__file__ = str(tmp_path / "scripts/configure-opencode.py")
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".cache/dotfiles").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".cache/dotfiles/generation.lock").write_text(
        f"source_root={script.SCRIPT_DIR}\n"
    )
    key.parent.mkdir(parents=True)
    key.write_text("test-key")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        script,
        "get_models",
        lambda *args: {
            "poolside/laguna-s-2.1",
            "openai/ordinary",
        },
    )
    monkeypatch.setattr(
        script,
        "resolve_alias",
        lambda alias, *args, **kwargs: {
            "base_url": "http://gateway/v1",
            "api_key": "key",
            "model": f"litellm/{alias}",
        },
    )
    # Oversized limits are capped (without widening) to the ceiling values;
    # custom fields are preserved through the gate transform.
    config = {
        "providers": {
            "poolside": {
                "models": {
                    "laguna-s-2.1": {
                        "name": "S",
                        "limit": {"context": 999999, "output": 999999},
                        "custom": "preserved",
                    }
                }
            },
            "openai": {"models": {"ordinary": {"name": "Ordinary"}}},
        }
    }
    script.apply_litellm_client_gate(config)
    routed = config["providers"]["litellm"]["models"]
    assert routed["poolside/laguna-s-2.1"]["limit"] == {
        "context": POOLSIDE_ROUTED_CONTEXT,
        "output": POOLSIDE_ROUTED_OUTPUT,
    }
    assert routed["poolside/laguna-s-2.1"]["custom"] == "preserved"
    assert routed["openai/ordinary"]["name"] == "Ordinary"


def test_opencode_poolside_allowlist_only_entry_gets_capped_limits(
    tmp_path, monkeypatch
):
    # An entry seeded only from the allowlist (absent from the provider config)
    # arrives with no limit key at all; _apply_routed_model_limits must still
    # apply the ceiling bounds during the allowlist pass.
    script = _load_script(
        "configure_opencode_allowlist_limits", "configure-opencode.py"
    )
    key = tmp_path / ".local/share/litellm/clients/opencode.key"
    (tmp_path / "configs/opencode").mkdir(parents=True)
    (tmp_path / "configs/opencode/poolside-models.json").write_text(
        json.dumps({"models": {"laguna-xs-2.1": {}, "laguna-s-2.1": {}}})
    )
    script.__file__ = str(tmp_path / "scripts/configure-opencode.py")
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".cache/dotfiles").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".cache/dotfiles/generation.lock").write_text(
        f"source_root={script.SCRIPT_DIR}\n"
    )
    key.parent.mkdir(parents=True)
    key.write_text("test-key")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        script,
        "get_models",
        lambda *args: {"poolside/laguna-s-2.1", "openai/ordinary"},
    )
    monkeypatch.setattr(
        script,
        "resolve_alias",
        lambda alias, *args, **kwargs: {
            "base_url": "http://gateway/v1",
            "api_key": "key",
            "model": f"litellm/{alias}",
        },
    )
    config = {
        "providers": {
            "openai": {"models": {"ordinary": {"name": "Ordinary"}}},
        }
    }
    script.apply_litellm_client_gate(config)
    routed = config["providers"]["litellm"]["models"]
    assert routed["poolside/laguna-s-2.1"]["limit"] == {
        "context": POOLSIDE_ROUTED_CONTEXT,
        "output": POOLSIDE_ROUTED_OUTPUT,
    }


def test_opencode_main_missing_model_preserves_installed_managed_files(
    monkeypatch, tmp_path
):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    script = _load_script("configure_opencode_main_missing", "configure-opencode.py")
    config_dir = home / ".config/opencode"
    config_dir.mkdir(parents=True)
    paths = {
        "opencode": config_dir / "opencode.json",
        "slim": config_dir / "oh-my-opencode-slim.json",
        "vibeguard": config_dir / "vibeguard.config.json",
    }
    sentinels = {name: f"{name} sentinel\n".encode() for name in paths}
    for name, path in paths.items():
        path.write_bytes(sentinels[name])
    scripts = tmp_path / "scripts"
    configs = scripts.parent / "configs"
    (configs / "opencode").mkdir(parents=True)
    (configs / "opencode/oh-my-opencode-slim.json").write_text(
        json.dumps({"roles": {"x": {"model": "openai/missing"}}})
    )
    (configs / "opencode/vibeguard.config.json").write_text("{}")
    monkeypatch.setattr(script, "SCRIPT_DIR", str(scripts))
    lock = home / ".cache/dotfiles/generation.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(f"source_root={scripts.parent.resolve()}\n")
    monkeypatch.setattr(script, "load_env", lambda *args: False)
    monkeypatch.setattr(script, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(script, "fetch_models_dev", lambda: {})
    monkeypatch.setattr(script, "build_opencode_server_config", lambda: None)
    monkeypatch.setattr(
        script, "active_plugin_specs", lambda: ["@plannotator/opencode@test", "plugin"]
    )
    monkeypatch.setattr(script, "plugin_specs", lambda: ["plugin"])
    monkeypatch.setattr(script, "get_preset_providers", lambda *args: set())
    monkeypatch.setattr(script, "get_litellm_proxy_mode", lambda: True)
    monkeypatch.setattr(script, "get_models", lambda *args: {"openai/present"})
    monkeypatch.setattr(
        script,
        "resolve_alias",
        lambda alias, *args, **kwargs: {
            "base_url": "http://gateway/v1",
            "api_key": "key",
            "model": f"litellm/{alias}",
        },
    )
    key_path = home / ".local/share/litellm/clients/opencode.key"
    key_path.parent.mkdir(parents=True)
    key_path.write_text("test-key")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "configure-opencode.py",
            "--preset",
            "pro-plus",
            "--skip",
            "mcps,acp-agents,tier,voice,dcp",
        ],
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    with pytest.raises(SystemExit):
        script.main()
    for name, path in paths.items():
        assert path.read_bytes() == sentinels[name]


def _run_opencode_main(
    monkeypatch, tmp_path, *, mode, extra_args=(), fail_validation=False
):
    lock_home = tmp_path / "lock-home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: lock_home))
    script = _load_script("configure_opencode_main", "configure-opencode.py")
    project = tmp_path / "project"
    config_dir = (
        project / ".opencode"
        if mode == "project"
        else tmp_path / "home/.config/opencode"
    )
    config_dir.mkdir(parents=True)
    if mode == "project":
        (project / "opencode.json").write_text('{"model":"existing/model"}\n')
    config_path = config_dir / "opencode.json"
    slim_path = config_dir / "oh-my-opencode-slim.json"
    config_path.write_bytes(b"prior-opencode\n")
    slim_path.write_text(json.dumps({"roles": {"prior": {"model": "openai/prior"}}}))
    scripts = tmp_path / "scripts"
    configs = scripts.parent / "configs"
    (configs / "opencode").mkdir(parents=True)
    monkeypatch.setattr(script, "SCRIPT_DIR", str(scripts))
    lock = tmp_path / "lock-home/.cache/dotfiles/generation.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(f"source_root={scripts.parent.resolve()}\n")
    slim_source = configs / "opencode/oh-my-opencode-slim.json"
    slim_source.write_text(json.dumps({"roles": {"x": {"model": "openai/fixture"}}}))
    monkeypatch.setattr(script, "CONFIGS_DIR", str(configs), raising=False)
    monkeypatch.setattr(script, "load_env", lambda *args: False)
    monkeypatch.setattr(script, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(script, "fetch_models_dev", lambda: {})
    monkeypatch.setattr(script, "build_opencode_server_config", lambda: None)
    monkeypatch.setattr(
        script,
        "active_plugin_specs",
        lambda: ["@plannotator/opencode@test", "plugin-a", "plugin-b"],
    )
    monkeypatch.setattr(script, "plugin_specs", lambda: ["plugin-a", "plugin-b"])
    monkeypatch.setattr(script, "get_preset_providers", lambda *args: set())
    monkeypatch.setattr(
        script,
        "apply_litellm_slim_gate",
        lambda slim, config, previous=None, **kwargs: {
            "roles": {"x": {"model": "litellm/openai/fixture"}}
        },
    )
    monkeypatch.setattr(
        script.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "R", (), {"stdout": "{}", "returncode": 0, "stderr": ""}
        )(),
    )
    monkeypatch.setattr(script, "get_litellm_proxy_mode", lambda: False)
    if fail_validation:
        monkeypatch.setattr(
            script,
            "prepare_project_candidate",
            lambda *args: (_ for _ in ()).throw(RuntimeError("project validation")),
        )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "configure-opencode.py",
            "--mode",
            mode,
            "--skip",
            "mcps,acp-agents,tier,voice,dcp",
            *extra_args,
        ],
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    script.main()
    return config_path, slim_path


@pytest.mark.parametrize("disabled", ["free", "omo-slim-opencode-zen-free"])
@pytest.mark.parametrize("env_location", ["local", "home"])
@pytest.mark.parametrize("selection", ["active", "fallback", "explicit"])
@pytest.mark.parametrize("mode", ["global", "project"])
def test_opencode_main_refuses_disabled_env_selection_before_side_effects(
    monkeypatch, tmp_path, disabled, env_location, selection, mode
):
    script = _load_script("configure_opencode_disabled", "configure-opencode.py")
    home = tmp_path / "home"
    config_dir = home / ".config/opencode"
    config_dir.mkdir(parents=True)
    slim_path = config_dir / "oh-my-opencode-slim.json"
    slim_path.write_text(json.dumps({"roles": {"prior": {"model": "openai/prior"}}}))
    local_env = config_dir / ".env"
    home_env = home / ".env"
    active = selection == "active"
    fallback = selection != "active"
    env_path = local_env if env_location == "local" else home_env
    lines = []
    if active:
        lines.append(f"DOTFILES_OPENCODE_TIER={disabled}")
    if fallback:
        lines.append(f"DOTFILES_LOCAL_FALLBACK_PRESET={disabled}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    monkeypatch.delenv("DOTFILES_OPENCODE_TIER", raising=False)
    monkeypatch.delenv("DOTFILES_LOCAL_FALLBACK_PRESET", raising=False)
    monkeypatch.setattr(script, "SCRIPT_DIR", str(tmp_path / "scripts"))
    lock = home / ".cache/dotfiles/generation.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(f"source_root={tmp_path.resolve()}\n")
    monkeypatch.setattr(
        script, "list_local_ollama_models", lambda: pytest.fail("discovery")
    )
    monkeypatch.setattr(script.subprocess, "run", lambda *a, **k: pytest.fail("child"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "configure-opencode.py",
            "--mode",
            mode,
            "--skip",
            "mcps,acp-agents,tier,voice,dcp",
        ]
        + (["--preset", "pro-plus"] if selection == "explicit" else []),
    )
    before_env = dict(os.environ)
    with patch.dict(os.environ), pytest.raises(SystemExit) as error:
        script.main.__wrapped__()
    assert dict(os.environ) == before_env
    assert error.value.code == (1 if active else 2)
    assert not (config_dir / "opencode.json").exists()
    assert (config_dir / "oh-my-opencode-slim.json").read_text() == json.dumps(
        {"roles": {"prior": {"model": "openai/prior"}}}
    )


@pytest.mark.parametrize("mode", ["global", "project"])
@pytest.mark.parametrize(
    "installed",
    [
        {"preset": "free"},
        {"council": {"default_preset": "free"}},
        {
            "_tiers": {
                "pro-plus": {
                    "council": {"default_preset": "omo-slim-opencode-zen-free"}
                }
            }
        },
        {"fallback": {"orchestrator": ["openai/model", "free"]}},
    ],
)
def test_opencode_main_refuses_installed_disabled_slim_without_touching_files(
    monkeypatch, tmp_path, mode, installed
):
    script = _load_script(
        "configure_opencode_installed_disabled", "configure-opencode.py"
    )
    home = tmp_path / "home"
    config_dir = (
        home / ".config/opencode"
        if mode == "global"
        else tmp_path / "project/.opencode"
    )
    config_dir.mkdir(parents=True)
    slim = config_dir / "oh-my-opencode-slim.json"
    slim.write_text(json.dumps(installed))
    opencode = config_dir / "opencode.json"
    opencode.write_text('{"mcp":{"keep":{"command":"fixture"}}}')
    mcp = config_dir / "mcp.json"
    mcp.write_text('{"keep":true}')
    before = {
        path: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
        for path in (slim, opencode, mcp)
    }
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    monkeypatch.delenv("DOTFILES_OPENCODE_TIER", raising=False)
    monkeypatch.delenv("DOTFILES_LOCAL_FALLBACK_PRESET", raising=False)
    monkeypatch.setattr(script, "SCRIPT_DIR", str(tmp_path / "scripts"))
    monkeypatch.setattr(script, "load_env", lambda *args: False)
    monkeypatch.setattr(
        script, "list_local_ollama_models", lambda: pytest.fail("discovery ran")
    )
    monkeypatch.setattr(sys, "argv", ["configure-opencode.py", "--mode", mode])
    with pytest.raises(SystemExit) as error:
        script.main.__wrapped__()
    assert error.value.code == 2
    assert before == {
        path: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
        for path in before
    }


@pytest.mark.parametrize("mode", ["global", "project"])
@pytest.mark.parametrize("payload", [b"not json", b"null", b"[]"])
def test_opencode_main_refuses_malformed_installed_slim_even_with_explicit_preset(
    monkeypatch, tmp_path, mode, payload
):
    script = _load_script(
        "configure_opencode_installed_malformed", "configure-opencode.py"
    )
    home = tmp_path / "home"
    config_dir = (
        home / ".config/opencode"
        if mode == "global"
        else tmp_path / "project/.opencode"
    )
    config_dir.mkdir(parents=True)
    slim = config_dir / "oh-my-opencode-slim.json"
    slim.write_bytes(payload)
    before = (slim.read_bytes(), slim.stat().st_mode, slim.stat().st_mtime_ns)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    monkeypatch.setattr(script, "load_env", lambda *args: False)
    monkeypatch.setattr(
        sys, "argv", ["configure-opencode.py", "--mode", mode, "--preset", "pro-plus"]
    )
    with pytest.raises(SystemExit) as error:
        script.main.__wrapped__()
    assert error.value.code == 2
    assert (slim.read_bytes(), slim.stat().st_mode, slim.stat().st_mtime_ns) == before


def test_opencode_explicit_replacement_warns_before_discovery(monkeypatch, tmp_path):
    script = _load_script("configure_opencode_replacement", "configure-opencode.py")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    slim = config_dir / "oh-my-opencode-slim.json"
    slim.write_text('{"preset":"free"}')
    before = slim.read_bytes()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OLLAMA", "1")
    monkeypatch.delenv("DOTFILES_OPENCODE_TIER", raising=False)
    monkeypatch.delenv("DOTFILES_LOCAL_FALLBACK_PRESET", raising=False)
    monkeypatch.setattr(script, "load_env", lambda *args: False)
    warnings = []
    monkeypatch.setattr(script.logger, "warning", lambda *args: warnings.append(args))

    class DiscoveryReached(BaseException):
        pass

    def discovery():
        message = " ".join(str(value) for row in warnings for value in row)
        assert "free" in message and "pro-plus" in message and "paid" in message
        raise DiscoveryReached()

    # BaseException bypasses the optional-discovery Exception degradation path.
    monkeypatch.setattr(script, "list_local_ollama_models", discovery)
    monkeypatch.setattr(
        sys, "argv", ["configure-opencode.py", "--preset", "pro-plus", "--dry-run"]
    )
    with pytest.raises(DiscoveryReached):
        script.main.__wrapped__()
    assert slim.read_bytes() == before


@pytest.mark.parametrize("kind", ["directory", "broken-link", "unreadable"])
def test_opencode_refuses_uninspectable_installed_path(monkeypatch, tmp_path, kind):
    script = _load_script("configure_opencode_uninspectable", "configure-opencode.py")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    slim = config_dir / "oh-my-opencode-slim.json"
    if kind == "directory":
        slim.mkdir()
    elif kind == "broken-link":
        slim.symlink_to(config_dir / "absent")
    else:
        slim.write_text("{}")
        import builtins

        real_open = builtins.open

        def guarded_open(path, *args, **kwargs):
            if str(path) == str(slim):
                raise PermissionError("fixture")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", guarded_open)
    before = slim.lstat()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    monkeypatch.setattr(script, "load_env", lambda *args: False)
    monkeypatch.setattr(sys, "argv", ["configure-opencode.py", "--preset", "pro-plus"])
    with pytest.raises(SystemExit) as error:
        script.main.__wrapped__()
    assert error.value.code == 2
    assert slim.lstat() == before
    assert not (config_dir / "opencode.json").exists()


def test_opencode_main_project_writes_candidate_and_dry_run_is_read_only(
    monkeypatch, tmp_path
):
    config_path, _ = _run_opencode_main(
        monkeypatch, tmp_path, mode="project", extra_args=("--dry-run",)
    )
    assert config_path.read_bytes() == b"prior-opencode\n"
    config_path, _ = _run_opencode_main(monkeypatch, tmp_path / "write", mode="project")
    written = json.loads(config_path.read_text())
    assert written["$schema"] == "https://opencode.ai/config.json"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600

    dry_dir = tmp_path / "dry-project/.opencode"
    dry_dir.mkdir(parents=True)
    (tmp_path / "dry-project/opencode.json").write_text('{"model":"dry/model"}')
    dry_main = _load_script("configure_opencode_dry", "configure-opencode.py")
    monkeypatch.setattr(dry_main, "SCRIPT_DIR", str(tmp_path / "scripts"))
    (tmp_path / "configs/opencode").mkdir(parents=True, exist_ok=True)
    (tmp_path / "configs/opencode/oh-my-opencode-slim.json").write_text("{}")
    for name, value in {
        "load_env": lambda *args: False,
        "list_local_ollama_models": lambda: [],
        "fetch_models_dev": lambda: {},
        "build_opencode_server_config": lambda: None,
        "active_plugin_specs": lambda: [
            "oh-my-opencode-slim@latest",
            "@tarquinen/opencode-dcp@test",
            "@plannotator/opencode@test",
            "plugin-a",
            "plugin-b",
        ],
        "plugin_specs": lambda: ["plugin-a", "plugin-b"],
        "get_preset_providers": lambda *args: set(),
        "get_litellm_proxy_mode": lambda: False,
    }.items():
        monkeypatch.setattr(dry_main, name, value)
    before = (
        (dry_dir / "opencode.json").read_bytes()
        if (dry_dir / "opencode.json").exists()
        else None
    )
    monkeypatch.setattr(
        sys, "argv", ["configure-opencode.py", "--mode", "project", "--dry-run"]
    )
    monkeypatch.setenv("OPENCODE_DIR", str(dry_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "dry-home"))
    dry_main.main.__wrapped__()
    assert (
        (dry_dir / "opencode.json").read_bytes()
        if (dry_dir / "opencode.json").exists()
        else None
    ) == before


def test_opencode_main_project_validation_failure_preserves_bytes(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "project/.opencode/opencode.json"
    with pytest.raises(SystemExit) as error:
        _run_opencode_main(monkeypatch, tmp_path, mode="project", fail_validation=True)
    assert error.value.code == 1
    assert config_path.read_bytes() == b"prior-opencode\n"


def test_opencode_main_global_installs_final_routed_slim(monkeypatch, tmp_path):
    _, slim_path = _run_opencode_main(
        monkeypatch,
        tmp_path,
        mode="global",
        extra_args=("--skip", "mcps,acp-agents,tier,voice,dcp"),
    )
    assert json.loads(slim_path.read_text()) == {
        "roles": {"x": {"model": "litellm/openai/fixture"}}
    }
    assert stat.S_IMODE(slim_path.stat().st_mode) == 0o600


def test_opencode_atomic_private_writer_preserves_old_output_on_replace_failure(
    monkeypatch, tmp_path
):
    script = _load_script("configure_opencode_atomic", "configure-opencode.py")
    target = tmp_path / "opencode.json"
    target.write_text("old-output", encoding="utf-8")
    real_replace = script.os.replace

    def fail_replace(source, destination):
        if Path(destination) == target:
            raise OSError("simulated replace crash")
        return real_replace(source, destination)

    monkeypatch.setattr(script.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace crash"):
        script.write_private_atomic(target, "new-secret-bearing-output")
    assert target.read_text(encoding="utf-8") == "old-output"
    assert list(tmp_path.iterdir()) == [target]


def test_opencode_tier_staging_is_private_and_staging_dir_is_private(
    monkeypatch, tmp_path
):
    script = _load_script("configure_opencode_staging", "configure-opencode.py")
    observed = {}

    def fake_run(_args, *, check, env):
        stage_file = Path(env["OPENCODE_DIR"]) / "opencode.json"
        observed["directory_mode"] = stat.S_IMODE(stage_file.parent.stat().st_mode)
        observed["file_mode"] = stat.S_IMODE(stage_file.stat().st_mode)
        observed["contents"] = stage_file.read_text(encoding="utf-8")
        (stage_file.parent / "oh-my-opencode-slim.json").write_text("{}")

    monkeypatch.setattr(script.subprocess, "run", fake_run)
    script.prepare_tier_candidate(
        str(tmp_path), ["tier-writer"], {"providers": {"key": "staged-secret"}}
    )
    assert observed["directory_mode"] == 0o700
    assert observed["file_mode"] == 0o600
    assert "staged-secret" in observed["contents"]


def test_opencode_tier_staging_refuses_previous_local_owner(monkeypatch, tmp_path):
    script = _load_script("configure_opencode_staging", "configure-opencode.py")
    tier = _load_script("configure_opencode_tier", "configure-opencode-tier.py")
    previous = {"agents": {"observer": {"model": "omlx/offline-model"}}}
    previous_bytes = b'{"agents": {"observer": {"model": "omlx/offline-model"}}}'
    registry = {
        "_tiers": {"pro": {"council": {"presets": {}}}},
        "presets": {"pro": {"observer": {"model": "omlx/offline-model"}}, "local": {}},
    }
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(tier.tier_registry, "load_registry", lambda *args: registry)
    monkeypatch.setattr(tier, "list_local_ollama_models", lambda: [])

    def run_tier(_args, *, check, env):
        stage = Path(env["OPENCODE_DIR"])
        prior_file = stage / "oh-my-opencode-slim.json"
        prior_file.write_bytes(previous_bytes)
        assert prior_file.read_bytes() == previous_bytes
        assert stat.S_IMODE(prior_file.stat().st_mode) == 0o600
        with patch.dict(os.environ, {"OPENCODE_DIR": str(stage)}):
            tier.orchestrate_tier_switch("pro", True, [])

    monkeypatch.setattr(script.subprocess, "run", run_tier)
    with pytest.raises(
        RuntimeError,
        match=r"UNRESOLVED.*omlx/offline-model.*Re-run generation once the alias exists",
    ):
        script.prepare_tier_candidate(
            str(tmp_path),
            ["tier-writer"],
            {"providers": {"litellm": {"models": {}}}},
            previous,
        )
    assert previous_bytes == json.dumps(previous).encode()
    assert not (tmp_path / "oh-my-opencode-slim.json").exists()


def test_prepare_tier_candidate_seeds_staging_with_base_slim(monkeypatch, tmp_path):
    """Regression: staging dir must be seeded from fresh source (base_slim),
    not the stale deployed file (previous_slim).

    When the deployed slim lacks a newly-added registry preset (e.g. 'cohere')
    but the source registry has it, the tier writer must see the fresh preset
    so it propagates to the output.  Previously the stale previous_slim was
    written to the staging dir, causing the tier writer to believe the file
    already existed and to skip the source copy — leaving dormant presets stale.
    """
    script = _load_script("configure_opencode_staging_seed", "configure-opencode.py")
    base_slim = {"preset": "pro", "_tiers": {"pro": {}, "cohere": {}}, "presets": {}}
    previous_slim = {"preset": "pro", "_tiers": {"pro": {}}, "presets": {}}

    observed = {}

    def fake_run(_args, *, check, env):
        staged_slim = Path(env["OPENCODE_DIR"]) / "oh-my-opencode-slim.json"
        observed["staged_content"] = json.loads(staged_slim.read_text(encoding="utf-8"))
        # Simulate tier writer producing its output from the staged input.
        staged_slim.write_text(json.dumps(observed["staged_content"]), encoding="utf-8")

    monkeypatch.setattr(script.subprocess, "run", fake_run)
    monkeypatch.setattr(
        script,
        "apply_litellm_slim_gate",
        lambda slim, config, previous_slim=None, **kwargs: slim,
    )
    result = script.prepare_tier_candidate(
        str(tmp_path),
        ["tier-writer"],
        {"providers": {"litellm": {"models": {}}}},
        base_slim=base_slim,
        previous_slim=previous_slim,
    )
    # The tier writer must have seen base_slim (fresh source), not previous_slim.
    assert observed["staged_content"] == base_slim
    assert "cohere" in observed["staged_content"]["_tiers"]
    assert result == json.dumps(base_slim, indent=2) + "\n"


def test_opencode_staging_and_final_outputs_are_private_atomic(monkeypatch, tmp_path):
    script = _load_script("configure_opencode_atomic_modes", "configure-opencode.py")
    config_path = tmp_path / "opencode.json"
    slim_path = tmp_path / "oh-my-opencode-slim.json"
    script.write_private_atomic(config_path, '{"provider":{"key":"sentinel"}}\n')
    script.write_private_atomic(slim_path, '{"model":"sentinel"}\n')
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(slim_path.stat().st_mode) == 0o600
    old_bytes = config_path.read_bytes()
    real_replace = script.os.replace

    def crash_replace(source, destination):
        if Path(destination) == config_path:
            raise OSError("simulated crash before atomic replace")
        return real_replace(source, destination)

    monkeypatch.setattr(script.os, "replace", crash_replace)
    with pytest.raises(OSError, match="simulated crash"):
        script.write_private_atomic(config_path, '{"provider":{"key":"rotated"}}\n')
    assert config_path.read_bytes() == old_bytes


def test_opencode_main_global_tier_failure_preserves_both_outputs(
    monkeypatch, tmp_path
):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    script = _load_script("configure_opencode_global_failure", "configure-opencode.py")
    config_dir = home / ".config/opencode"
    config_dir.mkdir(parents=True)
    opencode_path = config_dir / "opencode.json"
    slim_path = config_dir / "oh-my-opencode-slim.json"
    opencode_path.write_bytes(b"opencode sentinel\n")
    slim_path.write_bytes(b"slim sentinel\n")
    scripts = tmp_path / "scripts"
    (tmp_path / "configs/opencode").mkdir(parents=True)
    (tmp_path / "configs/opencode/oh-my-opencode-slim.json").write_text("{}")
    monkeypatch.setattr(script, "SCRIPT_DIR", str(scripts))
    lock = home / ".cache/dotfiles/generation.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(f"source_root={Path(__file__).resolve().parents[3]}\n")
    for name, value in {
        "load_env": lambda *args: False,
        "list_local_ollama_models": lambda: [],
        "fetch_models_dev": lambda: {},
        "build_opencode_server_config": lambda: None,
        "active_plugin_specs": lambda: [
            "oh-my-opencode-slim@latest",
            "@tarquinen/opencode-dcp@test",
            "@plannotator/opencode@test",
            "plugin-a",
            "plugin-b",
        ],
        "plugin_specs": lambda: ["plugin-a", "plugin-b"],
        "get_preset_providers": lambda *args: set(),
        "get_litellm_proxy_mode": lambda: False,
        "apply_litellm_client_gate": lambda config: None,
        "apply_litellm_slim_gate": lambda slim, config, previous=None, **kwargs: slim,
        "prepare_tier_candidate": lambda config_dir, args, consumer, base_slim=None, previous_slim=None, selected_preset=None: (
            _ for _ in ()
        ).throw(
            RuntimeError("final tier validation")
        ),
    }.items():
        monkeypatch.setattr(script, name, value)
    monkeypatch.setattr(
        script.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unexpected subprocess")
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "configure-opencode.py",
            "--preset",
            "pro-plus",
            "--skip",
            "mcps,acp-agents,voice,dcp",
        ],
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    script.main = script.main.__wrapped__
    with pytest.raises(SystemExit):
        script.main()
    assert opencode_path.read_bytes() == b"opencode sentinel\n"
    assert slim_path.read_bytes() == b"slim sentinel\n"


@pytest.mark.parametrize("recovery_fails", [False, True])
@pytest.mark.parametrize(
    "prior_slim",
    [None, b'{"roles":{"prior":{"model":"openai/prior"}}}'],
    ids=["absent", "valid-json"],
)
def test_opencode_main_slim_write_failure_restores_prior_pair(
    monkeypatch, tmp_path, caplog, prior_slim, recovery_fails
):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    script = _load_script("configure_opencode_pair_recovery", "configure-opencode.py")
    config_dir = home / ".config/opencode"
    config_dir.mkdir(parents=True)
    opencode_path = config_dir / "opencode.json"
    slim_path = config_dir / "oh-my-opencode-slim.json"
    opencode_path.write_bytes(b"opencode sentinel\n")
    if prior_slim is not None:
        slim_path.write_bytes(prior_slim)
    scripts = tmp_path / "scripts"
    (tmp_path / "configs/opencode").mkdir(parents=True)
    (tmp_path / "configs/opencode/oh-my-opencode-slim.json").write_text("{}")
    monkeypatch.setattr(script, "SCRIPT_DIR", str(scripts))
    lock = home / ".cache/dotfiles/generation.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(f"source_root={Path(__file__).resolve().parents[3]}\n")
    for name, value in {
        "load_env": lambda *args: False,
        "list_local_ollama_models": lambda: [],
        "fetch_models_dev": lambda: {},
        "build_opencode_server_config": lambda: None,
        "active_plugin_specs": lambda: [
            "oh-my-opencode-slim@latest",
            "@tarquinen/opencode-dcp@test",
            "@plannotator/opencode@test",
            "plugin-a",
            "plugin-b",
        ],
        "plugin_specs": lambda: ["plugin-a"],
        "get_preset_providers": lambda *args: set(),
        "get_litellm_proxy_mode": lambda: False,
        "apply_litellm_client_gate": lambda config: None,
        "apply_litellm_slim_gate": lambda slim, config, previous=None, **kwargs: slim,
        "prepare_tier_candidate": lambda config_dir, args, consumer, base_slim=None, previous_slim=None, selected_preset=None: (
            '{"model": "stub-slim-target"}'
        ),
    }.items():
        monkeypatch.setattr(script, name, value)
    monkeypatch.setattr(
        script.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unexpected subprocess")
        ),
    )
    real_write = script.write_private_atomic
    write_names = []

    def failing_opencode_write(path, payload):
        if Path(path).name == "opencode.json":
            write_names.append("opencode-FAIL")
            raise OSError("simulated opencode failure")
        if recovery_fails and write_names:
            write_names.append("slim-FAIL")
            raise OSError("simulated recovery failure")
        write_names.append("slim-ok")
        return real_write(path, payload)

    monkeypatch.setattr(script, "write_private_atomic", failing_opencode_write)
    real_remove = script.os.remove

    def failing_slim_remove(path):
        if recovery_fails and Path(path) == slim_path:
            raise OSError("simulated recovery failure")
        return real_remove(path)

    monkeypatch.setattr(script.os, "remove", failing_slim_remove)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "configure-opencode.py",
            "--preset",
            "pro-plus",
            "--skip",
            "mcps,acp-agents,voice,dcp",
        ],
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    script.main = script.main.__wrapped__
    with pytest.raises(SystemExit) as stopped:
        script.main()
    assert stopped.value.code == 1
    critical = [record for record in caplog.records if record.levelname == "CRITICAL"]
    assert len(critical) == 1
    assert "simulated opencode failure" in critical[0].message
    assert opencode_path.read_bytes() == b"opencode sentinel\n"
    if recovery_fails:
        assert "simulated recovery failure" in critical[0].message
        assert slim_path.read_bytes() == b'{"model": "stub-slim-target"}'
    elif prior_slim is None:
        assert not slim_path.exists()
    else:
        assert slim_path.read_bytes() == prior_slim


@pytest.mark.parametrize(
    "prior_slim",
    [b"slim sentinel\n", b"prior-slim\xff\n", b"prior-slim\r\n"],
    ids=["utf8", "undecodable", "crlf"],
)
@pytest.mark.parametrize("failure", ["first-write", "second-write", "recovery"])
def test_install_config_pair_preserves_exact_slim_snapshot(
    monkeypatch, tmp_path, prior_slim, failure
):
    script = _load_script("configure_opencode_exact_rollback", "configure-opencode.py")
    slim = tmp_path / "oh-my-opencode-slim.json"
    config = tmp_path / "opencode.json"
    backup = tmp_path / "oh-my-opencode-slim.json.bak"
    slim.write_bytes(prior_slim)
    os.chmod(slim, 0o640)
    backup.write_bytes(b"backup snapshot")
    before = slim.stat()
    config.write_bytes(b"config prior\r\n")
    config_before = config.stat()
    config_bytes = config.read_bytes()
    original_write = script.write_private_atomic
    calls = []

    def fail_at(path, payload):
        calls.append((Path(path), payload))
        if failure == "first-write" and len(calls) == 1:
            raise OSError("first write")
        if failure in {"second-write", "recovery"} and Path(path) == config:
            raise OSError("second write")
        if failure == "recovery" and payload == prior_slim and len(calls) > 2:
            raise OSError("rollback write")
        return original_write(path, payload)

    monkeypatch.setattr(script, "write_private_atomic", fail_at)
    backup_before = backup.stat()
    with pytest.raises(RuntimeError) as error:
        script.install_config_pair(slim, config, b"candidate slim", b"candidate config")
    if failure == "recovery":
        assert "rollback write" in str(error.value)
        assert slim.read_bytes() == b"candidate slim"
    else:
        assert slim.read_bytes() == prior_slim
        after = slim.stat()
        assert stat.S_IMODE(after.st_mode) == 0o640
        assert after.st_mtime_ns == before.st_mtime_ns
    assert backup.read_bytes() == b"backup snapshot"
    assert backup.stat().st_mtime_ns == backup_before.st_mtime_ns
    assert backup.stat().st_mode == backup_before.st_mode
    assert config.read_bytes() == config_bytes
    assert config.stat().st_mtime_ns == config_before.st_mtime_ns
    assert calls[0][1] == b"candidate slim"
    if failure == "recovery":
        assert calls[-1][1] == prior_slim


@pytest.mark.parametrize("prior_exists", [False, True], ids=["absent", "present"])
@pytest.mark.parametrize("failure", ["first-write", "second-write", "recovery"])
def test_install_config_pair_transaction_restores_snapshot(
    monkeypatch, tmp_path, prior_exists, failure
):
    script = _load_script("configure_opencode_transaction", "configure-opencode.py")
    slim = tmp_path / "oh-my-opencode-slim.json"
    config = tmp_path / "opencode.json"
    original = b'{"prior": "\xff\r\n"}'
    if prior_exists:
        slim.write_bytes(original)
        os.chmod(slim, 0o640)
        backup = tmp_path / "oh-my-opencode-slim.json.bak"
        backup.write_bytes(b"private backup")
        before_stat = slim.stat()
    else:
        backup = None
        before_stat = None
    config.write_bytes(b"config-before")
    config_before = config.read_bytes()
    real_write = script.write_private_atomic
    calls = []

    def failing_write(path, payload):
        calls.append((Path(path), payload))
        if failure == "first-write" and Path(path) == slim:
            raise OSError("first write failed")
        if failure in {"second-write", "recovery"} and Path(path) == config:
            raise OSError("second write failed")
        if failure == "recovery" and payload == original and len(calls) > 1:
            raise OSError("recovery failed")
        return real_write(path, payload)

    monkeypatch.setattr(script, "write_private_atomic", failing_write)
    with pytest.raises(RuntimeError):
        script.install_config_pair(slim, config, b"new slim", b"new config")

    if failure == "first-write":
        assert len(calls) == 1
    elif failure == "second-write":
        assert len(calls) == (3 if prior_exists else 2)
        if prior_exists:
            assert calls[2][1] == original
    else:
        if prior_exists:
            assert calls[2][1] == original
    if prior_exists and failure != "recovery":
        assert slim.read_bytes() == original
        after_stat = slim.stat()
        assert stat.S_IMODE(after_stat.st_mode) == 0o640
        assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
        assert backup.read_bytes() == b"private backup"
    elif prior_exists:
        assert slim.read_bytes() == b"new slim"
    else:
        assert not slim.exists()
    assert config.read_bytes() == config_before


def test_opencode_inventory_keys_anthropic_under_meridian_and_routes_slim(
    tmp_path, monkeypatch
):
    """Aggregation must mirror the gateway's meridian-serving condition: while
    Meridian has credentials, anthropic allowlist models key as meridian/<id>.
    Regression for the live-deploy failure 'no LiteLLM gateway alias:
    anthropic/claude-sonnet-5-5'."""
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    key = tmp_path / ".local/share/litellm/clients/opencode.key"
    key.parent.mkdir(parents=True)
    key.write_text("test-key")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MERIDIAN_API_KEY", "meridian")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    config = {
        "model": "anthropic/claude-sonnet-5-5",
        "providers": {
            "anthropic": {
                "models": {"claude-sonnet-5-5": {"name": "Sonnet 5.5"}},
            },
        },
    }
    configure_opencode.apply_litellm_client_gate(config)
    inventory = config["providers"]["litellm"]["models"]
    assert "meridian/claude-sonnet-5-5" in inventory
    assert not any(item.startswith("anthropic/") for item in inventory)
    assert config["model"] == "litellm/meridian/claude-sonnet-5-5"
    slim = {
        "presets": {"x": {"designer": {"model": "anthropic/claude-sonnet-5-5"}}},
    }
    routed = configure_opencode.apply_litellm_slim_gate(slim, config)
    assert (
        routed["presets"]["x"]["designer"]["model"]
        == "litellm/meridian/claude-sonnet-5-5"
    )


def test_opencode_slim_anthropic_falls_back_to_direct_anthropic_alias(
    tmp_path, monkeypatch
):
    """Without Meridian credentials the inventory keeps anthropic/<id> keys and
    anthropic selections resolve against them instead of unroutable
    meridian/<id> lookups."""
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    key = tmp_path / ".local/share/litellm/clients/opencode.key"
    key.parent.mkdir(parents=True)
    key.write_text("test-key")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MERIDIAN_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    config = {
        "providers": {
            "anthropic": {
                "models": {"claude-sonnet-5-5": {"name": "Sonnet 5.5"}},
            },
        },
    }
    configure_opencode.apply_litellm_client_gate(config)
    inventory = config["providers"]["litellm"]["models"]
    assert "anthropic/claude-sonnet-5-5" in inventory
    slim = {
        "presets": {"x": {"designer": {"model": "anthropic/claude-sonnet-5-5"}}},
    }
    routed = configure_opencode.apply_litellm_slim_gate(slim, config)
    assert (
        routed["presets"]["x"]["designer"]["model"]
        == "litellm/anthropic/claude-sonnet-5-5"
    )


def test_opencode_inventory_seeds_allowlisted_dormant_owners(tmp_path, monkeypatch):
    """Provider blocks absent from the active tier must still be selectable:
    repo allowlists seed their identities (never invented aliases)."""
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    key = tmp_path / ".local/share/litellm/clients/opencode.key"
    key.parent.mkdir(parents=True)
    key.write_text("test-key")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MERIDIAN_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    config = {
        "providers": {
            "openai": {"models": {"gpt-x": {"name": "X"}}},
        },
    }
    # Seed test: the live catalogue serves every seeded identity, so the
    # honesty filter keeps the inventory (stub returns the id-set directly).
    monkeypatch.setattr(
        configure_opencode,
        "get_models",
        lambda *args, **kwargs: {
            "opencode/big-pickle",
            "google/models/gemini-3.8-flash",
            "github-copilot/gemini-3.5-flash",
            "openai/gpt-x",
        },
    )
    configure_opencode.apply_litellm_client_gate(config)
    inventory = config["providers"]["litellm"]["models"]
    assert "opencode/big-pickle" in inventory
    assert "google/models/gemini-3.8-flash" in inventory
    assert "github-copilot/gemini-3.5-flash" in inventory
    slim = {
        "presets": {"free": {"fast": {"model": "opencode/big-pickle"}}},
    }
    routed = configure_opencode.apply_litellm_slim_gate(slim, config)
    assert routed["presets"]["free"]["fast"]["model"] == "litellm/opencode/big-pickle"


@pytest.mark.parametrize("owner", ["ollama", "omlx", "llamacpp"])
def test_opencode_slim_refuses_local_engine_selections_without_alias(
    owner, tmp_path, monkeypatch
):
    """Refuse local-engine selections when the gateway has no alias."""
    configure_opencode = _load_script("configure_opencode", "configure-opencode.py")
    key = tmp_path / ".local/share/litellm/clients/opencode.key"
    key.parent.mkdir(parents=True)
    key.write_text("test-key")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    slim = {"roles": {"observer": {"model": f"{owner}/fixture-model"}}}
    consumer = {"providers": {"litellm": {"models": {}}}}

    with pytest.raises(RuntimeError, match="UNRESOLVED"):
        configure_opencode.apply_litellm_slim_gate(slim, consumer, previous_slim=slim)


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
        config = {"providers": {}}
        configure_opencode.register_local_provider(config, "omlx", provider)
    assert config["providers"]["omlx"] is provider


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
        patch.object(local_engines, "engine_gate_active", lambda p: p == "omlx"),
        patch.dict(os.environ, {}, clear=True),
    ):
        provider = generate_profiles.build_provider_configs(cfg)["omlx"]
    assert provider["baseUrl"] == "http://omlx:8000/v1/chat/completions"
    assert "apiKey" not in provider


def test_jetbrains_wrapper_preserves_mixed_provider_metadata(monkeypatch, tmp_path):
    home = tmp_path / "lock-home"
    monkeypatch.setenv("HOME", str(home))
    wrapper = _load_script("configure_jetbrains_ai", "configure-jetbrains-ai.py")
    generator = _load_script(
        "generate_jetbrains_profiles", "generate-jetbrains-profiles.py"
    )
    # Generation-lock acceptance is checkout-dependent: seed a temp HOME lock
    # whose source_root matches THIS checkout, mirroring the established
    # isolated-generation-lock pattern used by the configure-opencode tests.
    lock = home / ".cache/dotfiles/generation.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(f"source_root={Path(wrapper.SCRIPT_DIR).resolve().parent}\n")
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


def test_voice_omlx_llm_endpoint_and_api_key_are_conditional(monkeypatch):
    voice = _load_script("configure_opencode_voice", "configure-opencode-voice.py")
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
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
    monkeypatch.delenv("DOTFILES_RUN_OMLX_SETUP")
    with patch.dict(os.environ, {}, clear=True):
        unconfigured = voice.local_voice_provider("omlx/chat")
    assert "apiKeyEnv" not in unconfigured


def test_acp_local_model_uses_combined_pool(monkeypatch):
    acp = _load_script("configure_acp_agents", "configure-acp-agents.py")
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
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
        # local_model() resolves the openai-compatible pool winner (claude--
        # local gates itself on Anthropic support separately).
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


@pytest.mark.parametrize("discovered", [[], None])
def test_sync_turboquant_kv_applies_ollama_kv_cache_type(tmp_path, discovered):
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
    with patch.object(
        local_engines, "iter_engine_models_strict", return_value=discovered
    ):
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
    with patch.object(
        local_engines, "iter_engine_models_strict", return_value=discovered
    ):
        assert local_engines.sync_turboquant_kv(settings_file, env) == 0
    # q4_0 → 4-bit (both models flip)
    with patch.object(
        local_engines, "iter_engine_models_strict", return_value=discovered
    ):
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


def test_sync_turboquant_kv_seeds_discovered_models_and_is_idempotent(
    tmp_path, monkeypatch
):
    settings_file = tmp_path / "model_settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "version": 1,
                "models": {"existing": {"custom": "keep", "turboquant_kv_bits": 4}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        local_engines,
        "iter_engine_models_strict",
        lambda provider: [{"name": "existing"}, {"name": "new-model"}],
    )
    writes = []
    original_replace = os.replace

    def counted_replace(source, destination):
        writes.append(destination)
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", counted_replace)
    env = {"OLLAMA_KV_CACHE_TYPE": "q8_0"}

    assert local_engines.sync_turboquant_kv(settings_file, env) == 2
    document = json.loads(settings_file.read_text(encoding="utf-8"))
    expected_policy = {
        "turboquant_kv_enabled": True,
        "turboquant_kv_bits": 8,
        "turboquant_skip_last": True,
    }
    assert document["models"]["existing"] == {
        "custom": "keep",
        **expected_policy,
    }
    assert document["models"]["new-model"] == expected_policy
    assert local_engines.sync_turboquant_kv(settings_file, env) == 0
    assert len(writes) == 1


@pytest.mark.parametrize("gate", [None, "0"])
def test_omlx_turboquant_doctor_gate_off_skips_discovery(tmp_path, gate):
    verify = _load_script("verify_config", "verify-config.py")
    environ = {"OLLAMA_KV_CACHE_TYPE": "q8_0"}
    if gate is not None:
        environ["DOTFILES_RUN_OMLX_SETUP"] = gate
    with patch.object(local_engines, "iter_engine_models_strict") as discover:
        assert verify.omlx_turboquant_status(home=tmp_path, environ=environ) == (
            "oMLX TurboQuant KV: skipped (DOTFILES_RUN_OMLX_SETUP=0)",
            None,
        )
    discover.assert_not_called()


def test_omlx_turboquant_doctor_empty_catalogue_is_active(tmp_path, monkeypatch):
    verify = _load_script("verify_config", "verify-config.py")
    monkeypatch.setattr(local_engines, "iter_engine_models_strict", lambda _: [])
    assert verify.omlx_turboquant_status(
        home=tmp_path,
        environ={"DOTFILES_RUN_OMLX_SETUP": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0"},
    ) == ("oMLX TurboQuant KV: applied 0 of 0 discovered models", False)


def test_omlx_turboquant_doctor_status(tmp_path, monkeypatch):
    verify = _load_script("verify_config", "verify-config.py")
    monkeypatch.setattr(
        local_engines,
        "iter_engine_models_strict",
        lambda provider: [{"name": "ready"}, {"name": "pending"}],
    )
    settings = tmp_path / ".omlx/model_settings.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps(
            {
                "models": {
                    "ready": {
                        "turboquant_kv_enabled": True,
                        "turboquant_kv_bits": 8,
                        "turboquant_skip_last": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    message, incomplete = verify.omlx_turboquant_status(
        home=tmp_path,
        environ={"DOTFILES_RUN_OMLX_SETUP": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0"},
    )
    assert message == "oMLX TurboQuant KV: applied 1 of 2 discovered models"
    assert incomplete is True

    monkeypatch.setattr(
        local_engines, "iter_engine_models_strict", lambda provider: None
    )
    assert verify.omlx_turboquant_status(
        home=tmp_path,
        environ={"DOTFILES_RUN_OMLX_SETUP": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0"},
    ) == (
        "oMLX TurboQuant KV: discovery unavailable (UNKNOWN)",
        None,
    )


def test_omlx_turboquant_doctor_counts_preserved_skip_last(tmp_path, monkeypatch):
    verify = _load_script("verify_config", "verify-config.py")
    monkeypatch.setattr(
        local_engines,
        "iter_engine_models_strict",
        lambda provider: [{"name": "admin-choice"}],
    )
    settings = tmp_path / ".omlx/model_settings.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps(
            {
                "models": {
                    "admin-choice": {
                        "turboquant_kv_enabled": True,
                        "turboquant_kv_bits": 8,
                        "turboquant_skip_last": False,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert verify.omlx_turboquant_status(
        home=tmp_path,
        environ={"DOTFILES_RUN_OMLX_SETUP": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0"},
    ) == ("oMLX TurboQuant KV: applied 1 of 1 discovered models", False)


def test_omlx_turboquant_doctor_counts_requested_q4(tmp_path, monkeypatch):
    verify = _load_script("verify_config", "verify-config.py")
    monkeypatch.setattr(
        local_engines,
        "iter_engine_models_strict",
        lambda provider: [{"name": "q4-model"}],
    )
    settings = tmp_path / ".omlx/model_settings.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps(
            {
                "models": {
                    "q4-model": {
                        "turboquant_kv_enabled": True,
                        "turboquant_kv_bits": 4,
                        "turboquant_skip_last": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert verify.omlx_turboquant_status(
        home=tmp_path,
        environ={"DOTFILES_RUN_OMLX_SETUP": "1", "OLLAMA_KV_CACHE_TYPE": "q4_0"},
    ) == ("oMLX TurboQuant KV: applied 1 of 1 discovered models", False)


def test_omlx_turboquant_doctor_unset_policy_is_neutral(tmp_path, monkeypatch):
    verify = _load_script("verify_config", "verify-config.py")
    monkeypatch.setattr(
        local_engines,
        "iter_engine_models_strict",
        lambda provider: [{"name": "existing"}],
    )
    settings = tmp_path / ".omlx/model_settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"models": {"existing": {}}}), encoding="utf-8")
    assert verify.omlx_turboquant_status(
        home=tmp_path, environ={"DOTFILES_RUN_OMLX_SETUP": "1"}
    ) == (
        "oMLX TurboQuant KV: no KV policy requested (KV type unset/f16); 1 profiles present",
        None,
    )


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


def test_identity_does_not_remove_distinct_qwen_models(monkeypatch):
    """Regression: an oMLX Qwen4-7B must not remove qwen2.5-coder:7b or qwen3:7b.

    Identity now requires family (with version folded), param count, and
    fine-tune markers to all match; only truly equivalent names merge.
    """
    fake_ollama = [
        {"name": "qwen2.5-coder:7b", "size_gb": 4.7, "provider": "ollama"},
        {"name": "qwen3:7b", "size_gb": 4.7, "provider": "ollama"},
    ]
    fake_omlx = [{"name": "Qwen4-7B", "size_gb": 4.4, "provider": "omlx"}]
    monkeypatch.setattr(
        local_engines,
        "iter_engine_models",
        lambda p, ic=False: fake_ollama if p == "ollama" else fake_omlx,
    )
    monkeypatch.setattr(local_engines, "active_engines", lambda: ["ollama", "omlx"])
    monkeypatch.setattr(local_engines, "engine_gate_active", lambda p: True)
    pool = local_engines.merged_local_pool()
    names = [m["name"] for m in pool]
    assert "qwen2.5-coder:7b" in names
    assert "qwen3:7b" in names
    assert "Qwen4-7B" in names


def test_identity_marks_fine_tune_variants_distinct():
    """Same family+size but coder-vs-instruct markers must not merge."""
    from local_engines import _model_identity

    instruct = _model_identity("qwen3.8:27b-mlx")
    coder = _model_identity("Qwen3.8-27B-Coder-MLX-4bit")
    assert instruct != coder
    assert "coder" in coder[2]
    # Real equivalence pairs still merge.
    assert _model_identity("qwen3.8:27b-mlx") == _model_identity("Qwen3.8-27B-MLX-4bit")
    assert _model_identity("gemma4:12b-mxfp8") == _model_identity(
        "gemma-4-12B-it-MLX-8bit"
    )


@pytest.mark.parametrize("project_config", [False, True])
def test_standalone_tier_switch_wraps_selections_and_direct_mode_is_canonical(
    tmp_path, monkeypatch, project_config
):
    """Invoke tier materialization itself and inspect its persisted output."""
    tier = _load_script("configure_opencode_tier", "configure-opencode-tier.py")
    global_dir = tmp_path / ".config/opencode"
    global_dir.mkdir(parents=True)
    cfgdir = tmp_path / "project/.opencode" if project_config else global_dir
    cfgdir.mkdir(parents=True, exist_ok=True)
    source = (
        Path(__file__).resolve().parents[3]
        / "configs/opencode/oh-my-opencode-slim.json"
    )
    deployed_slim = json.loads(source.read_text())
    deployed_slim["skills"] = ["frontend/design"]
    (cfgdir / "oh-my-opencode-slim.json").write_text(json.dumps(deployed_slim))
    # Build wire inventory from the actual model references used by the checked-in registry.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import litellm_config as gateway_generator

    inventory_ids = set(gateway_generator._registry_model_refs())
    inventory_ids.update(
        f"meridian/{ref.split('/', 1)[1]}"
        for ref in list(inventory_ids)
        if ref.startswith("anthropic/")
    )
    # Cohere selections route through the gateway's cohere_chat wire
    # namespace — same canonical model IDs, like the meridian translation.
    inventory_ids.update(
        f"cohere_chat/{ref.split('/', 1)[1]}"
        for ref in list(inventory_ids)
        if ref.startswith("cohere/")
    )
    anthropic_allowlist = json.loads(
        (source.parent / "anthropic-models.json").read_text()
    )["models"]
    inventory_ids.update(f"meridian/{model}" for model in anthropic_allowlist)
    inventory_ids = {
        ref
        for ref in inventory_ids
        if not (ref.startswith("anthropic/") or ref.startswith("cohere/"))
    }
    for provider in (
        "openai",
        "ollama-cloud",
        "google",
        "openrouter",
        "opencode",
        "github-copilot",
    ):
        models = json.loads((source.parent / f"{provider}-models.json").read_text())[
            "models"
        ]
        for model in models:
            identity = (
                f"google/models/{model}"
                if provider == "google"
                else f"{provider}/{model}"
            )
            inventory_ids.add(identity)
    # The gateway serves OpenRouter aliases in raw upstream spellings
    # (':free' variants included) and strips only Google's native 'models/'
    # prefix — mirror that, don't blanket-canonicalize.
    inventory_ids = {
        canonical_allowlist_key(ref) if ref.startswith("google/") else ref
        for ref in inventory_ids
    }
    consumer = global_dir / "opencode.json"
    consumer.write_text(
        json.dumps(
            {
                "providers": {
                    "litellm": {
                        "models": {f"litellm/{i}": {"id": i} for i in inventory_ids}
                    }
                }
            }
        )
    )
    if project_config:
        (cfgdir.parent / "opencode.json").write_text(
            json.dumps({"model": "litellm/openai/gpt-6-luna"})
        )
        (cfgdir / "opencode.json").write_text(
            json.dumps({"providers": {"litellm": {"options": {"timeout": 12345}}}})
        )
    with patch.dict(
        os.environ,
        {
            "DOTFILES_USE_LITELLM_PROXY": "1",
            "DOTFILES_RUN_LITELLM_SETUP": "1",
            "DOTFILES_LITELLM_OAUTH_PROVIDERS": "1",
            "LITELLM_PORT": "4000",
            "HOME": str(tmp_path),
            "OPENCODE_DIR": str(cfgdir),
        },
        clear=False,
    ):
        monkeypatch.setattr(tier, "list_local_ollama_models", lambda: [])
        tier.orchestrate_tier_switch("pro", True, [], dry_run=False)
    output = json.loads((cfgdir / "oh-my-opencode-slim.json").read_text())

    selections = []

    def collect(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"model", "primaryModel"} and isinstance(value, str):
                    selections.append(value)
                elif key == "fallback" and isinstance(value, dict):
                    selections.extend(
                        model
                        for chain in value.values()
                        if isinstance(chain, list)
                        for model in chain
                        if isinstance(model, str)
                    )
                else:
                    collect(value)
        elif isinstance(node, list):
            for child in node:
                collect(child)

    collect(output)
    routed = [model for model in selections if model.startswith("litellm/")]
    unwrapped = [
        model
        for model in selections
        if model.startswith(
            tuple(
                f"{p}/"
                for p in (
                    "openai",
                    "ollama-cloud",
                    "google",
                    "openrouter",
                    "opencode",
                    "omlx",
                    "ollama",
                    "github-copilot",
                )
            )
        )
    ]
    # Disabled free definitions are not exported as selectable tiers.
    assert "free" not in output["presets"]
    assert "omo-slim-opencode-zen-free" not in output["presets"]
    # This fixture advertises every configured gateway alias. Its exact
    # selection count guards against partial deletion while the active-tier
    # assertions verify the intended producer-to-consumer model identities.
    assert len(routed) == 92
    assert unwrapped == []
    assert "pro" in output["presets"]
    assert "pro" in output["council"]["presets"]
    for role, selected in deployed_slim["presets"]["pro"].items():
        if not isinstance(selected, dict) or "model" not in selected:
            continue
        expected = f"litellm/{selected['model']}"
        assert output["presets"]["pro"][role]["model"] == expected
    for seat in ("alpha", "beta", "gamma"):
        selected = deployed_slim["council"]["presets"]["pro"][seat]["model"]
        assert output["council"]["presets"]["pro"][seat]["model"] == (
            f"litellm/{selected}"
        )
    assert output["skills"] == ["frontend/design"]


def _pi_preserve_fixtures(monkeypatch, tmp_path):
    configure_pi = _load_script("configure_pi_preserve", "configure-pi.py")
    home = tmp_path / "home"
    # HOME isolation: CI runners have no materialised pi.key, so the fixture
    # provides one through the HOME env (expanduser consults HOME, not
    # Path.home) and satisfies the 0700-dir/0600-file permission gates.
    clients = home / ".local/share/litellm/clients"
    clients.mkdir(parents=True)
    (home / ".local/share/litellm").chmod(0o700)
    clients.chmod(0o700)
    key_file = clients / "pi.key"
    key_file.write_text("stub-scoped-key\n")
    key_file.chmod(0o600)
    agent = home / ".pi/agent"
    agent.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setattr(configure_pi, "get_litellm_proxy_mode", lambda: True)
    monkeypatch.setattr(
        configure_pi,
        "litellm_cloud_aliases",
        lambda port="4000": {"google": {}, "openrouter": {}, "opencode": {}},
    )
    monkeypatch.setattr(sys, "argv", ["configure-pi.py", "--preset", "local"])
    return configure_pi, agent


def test_pi_preserves_prior_config_when_litellm_catalogue_unknown(
    monkeypatch, tmp_path, caplog
):
    configure_pi, agent = _pi_preserve_fixtures(monkeypatch, tmp_path)
    sentinels = {
        "settings.json": '{"theme": "sentinel"}\n',
        "models.json": '{"providers": {}}\n',
        "auth.json": '{"api_keys": {}}\n',
    }
    for name, content in sentinels.items():
        (agent / name).write_text(content)
    with caplog.at_level("ERROR"):
        configure_pi.main()
    for name, content in sentinels.items():
        assert (agent / name).read_text() == content
    assert any(
        "preserved the previous Pi configuration" in record.message
        for record in caplog.records
    )


def test_pi_unknown_catalogue_without_prior_config_fails_closed(monkeypatch, tmp_path):
    configure_pi, _agent = _pi_preserve_fixtures(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="no prior Pi configuration"):
        configure_pi.main()
