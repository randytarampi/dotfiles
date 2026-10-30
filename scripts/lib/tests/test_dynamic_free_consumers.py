import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "scripts/lib"
sys.path.insert(0, str(LIB))

from free_preset_runtime import prepare_dynamic_free_registry  # noqa: E402
from free_route_manifest import build_manifest  # noqa: E402
from free_preset_runtime import load_gateway_free_evidence  # noqa: E402

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)
PRESETS = ("free", "omo-slim-opencode-zen-free")
GATEWAY_PRESETS = ("free", "omo-slim-opencode-zen-free")
OPENCODE_GATEWAY_ALIASES = ("zen", "opencode/vendor-free")


@pytest.fixture(autouse=True)
def isolated_process(monkeypatch):
    for name in tuple(os.environ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(configure_pi, "load_env", lambda: None)
    monkeypatch.setattr(junie, "load_env", lambda: None)
    monkeypatch.setattr(configure_pi, "_ensure_packages", lambda *a, **k: None)
    monkeypatch.setattr(configure_pi, "_native_context", lambda *a, **k: None)


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


configure_pi = load_script("dynamic_free_configure_pi", "configure-pi.py")
junie = load_script("dynamic_free_junie", "generate-jetbrains-profiles.py")
configure_opencode = load_script(
    "dynamic_free_configure_opencode", "configure-opencode.py"
)


def runtime(monkeypatch, *, model="big-pickle", available=True):
    snapshot = {
        "source": "synthetic",
        "observed_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "models": {
            "opencode": {
                "models": {
                    model: {
                        "reasoning": True,
                        "tool_call": True,
                        "modalities": {"input": ["text", "image"], "output": ["text"]},
                    }
                }
            }
        },
    }
    calls = []

    def route_fetch(provider, key, base):
        calls.append((provider, key, base))
        return [model] if available else []

    def prepare(registry, requested, environ, **kwargs):
        kwargs.pop("consumer", None)
        return prepare_dynamic_free_registry(
            registry,
            requested,
            environ,
            metadata_fetch=lambda: snapshot,
            route_fetch=route_fetch,
            clock=lambda: NOW,
            **kwargs,
        )

    monkeypatch.setattr(configure_pi, "prepare_dynamic_free_registry", prepare)
    monkeypatch.setattr(junie, "prepare_dynamic_free_registry", prepare)
    return calls


def opencode_runtime(monkeypatch, *, gateway_evidence=None):
    calls = []
    materialized = {}
    capabilities = {
        "reasoning": True,
        "tool_call": True,
        "modalities": {"input": ["text", "image"], "output": ["text"]},
    }

    def prepare(registry, requested, environ, **kwargs):
        calls.append((requested, environ.get("DOTFILES_USE_LITELLM_PROXY")))
        if gateway_evidence is not None:
            kwargs["gateway_evidence"] = gateway_evidence
            kwargs["consumer"] = "opencode"
        result = prepare_dynamic_free_registry(
            registry,
            requested,
            environ,
            metadata_fetch=lambda: {
                "source": "synthetic",
                "observed_at": NOW.isoformat(),
                "expires_at": (NOW + timedelta(hours=1)).isoformat(),
                "models": {
                    "opencode": {
                        "models": {
                            "big-pickle": capabilities,
                            "big-pickle-free": {
                                **capabilities,
                                "modalities": {
                                    "input": ["text", "image"],
                                    "output": ["text"],
                                },
                            },
                            "nemotron-3.5-lightning-free": capabilities,
                            "mimo-v2.6-flash-free": capabilities,
                            "muse-spark-1.3-contributor-free": capabilities,
                        }
                    }
                },
            },
            route_fetch=lambda provider, key, base: [
                "big-pickle",
                "nemotron-3.5-lightning-free",
                "mimo-v2.6-flash-free",
                "muse-spark-1.3-contributor-free",
            ],
            clock=lambda: NOW,
            **kwargs,
        )
        materialized.update(result[0])
        return result

    monkeypatch.setattr(configure_opencode, "prepare_dynamic_free_registry", prepare)
    for provider, key in (
        ("OPENCODE_API_KEY", "synthetic-opencode"),
        ("OPENROUTER_API_KEY", "synthetic-openrouter"),
    ):
        monkeypatch.setenv(provider, key)
    return calls, materialized


def healthy_gateway(tmp_path, monkeypatch, alias="zen"):
    root = tmp_path / "litellm"
    clients = root / "clients"
    clients.mkdir(parents=True)
    routes = [
        {
            "model_name": alias,
            "litellm_params": {
                "model": "opencode/big-pickle-free",
                "api_base": "https://opencode.ai/zen/v1",
            },
            "_route_provenance": {
                "upstream_host": "opencode",
                "upstream_model_id": "big-pickle-free",
                "metadata_model_id": "big-pickle-free",
                "metadata_provider": "opencode",
                "source_kind": "live-catalogue",
                "observed_at": NOW.isoformat(),
                "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
            },
        }
    ]
    manifest = build_manifest(routes, now=NOW)
    binding = manifest["config_binding_sha256"]
    (root / "config.yaml").write_text(
        "model_list:\n"
        f"  - model_name: {alias}\n"
        "    litellm_params:\n"
        "      model: opencode/big-pickle-free\n"
        "      api_base: https://opencode.ai/zen/v1\n"
        "      api_key: synthetic\n"
    )
    (root / "free-route-manifest.json").write_text(json.dumps(manifest))
    (root / "free-route-config.json").write_text(
        json.dumps({"config_binding_sha256": binding, "routes": routes})
    )
    key = clients / "opencode.key"
    key.write_text("synthetic")
    key.chmod(0o600)
    monkeypatch.setattr(
        "model_catalogues.get_catalogue",
        lambda *a, **k: {"data": [{"id": alias}]},
    )
    evidence = load_gateway_free_evidence(
        {"LITELLM_ROOT": str(root), "LITELLM_OPENCODE_KEY": "synthetic"}
    )
    evidence["gateway_models"].update(
        observed_at=NOW.isoformat(),
        expires_at=(NOW + timedelta(minutes=5)).isoformat(),
    )
    return evidence


@pytest.mark.parametrize("preset", GATEWAY_PRESETS)
def test_pi_gateway_dynamic_alias_is_preserved_end_to_end(
    tmp_path, monkeypatch, preset
):
    evidence = healthy_gateway(tmp_path, monkeypatch)
    snapshot = {
        "source": "synthetic",
        "observed_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "models": {
            "opencode": {
                "models": {
                    "big-pickle-free": {
                        "reasoning": True,
                        "tool_call": True,
                        "modalities": {"input": ["text", "image"], "output": ["text"]},
                    }
                }
            }
        },
    }

    def prepare(registry, requested, environ, **kwargs):
        kwargs.pop("consumer", None)
        return prepare_dynamic_free_registry(
            registry,
            requested,
            environ,
            metadata_fetch=lambda: snapshot,
            clock=lambda: NOW,
            gateway_evidence=evidence,
            consumer="pi",
            **kwargs,
        )

    monkeypatch.setattr(configure_pi, "prepare_dynamic_free_registry", prepare)
    home = tmp_path / "home"
    home.mkdir()
    agent = home / ".pi/agent"
    agent.mkdir(parents=True)
    clients = home / ".local/share/litellm/clients"
    clients.mkdir(parents=True)
    clients.chmod(0o700)
    key_file = home / ".local/share/litellm/clients/pi.key"
    key_file.write_text("synthetic")
    key_file.chmod(0o600)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setattr(
        configure_pi,
        "litellm_cloud_aliases",
        lambda _port: {
            "opencode": {},
            "_available": {
                "zen": "zen",
                "ollama/no-model-available": "ollama/no-model-available",
            },
        },
    )
    monkeypatch.setattr(
        configure_pi,
        "resolve_alias",
        lambda identity, *a, **k: {"api_key": "synthetic", "model": identity},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["configure-pi.py", "--preset", preset, "--no-local-fallbacks"],
    )

    configure_pi.main()

    settings = json.loads((agent / "settings.json").read_text())
    models = json.loads((agent / "models.json").read_text())
    assert settings["defaultProvider"] == "litellm"
    assert settings["defaultModel"] == "zen"
    assert settings["subagents"]["defaultModel"] == "litellm/zen"
    assert all(
        override["model"] == "litellm/zen"
        for override in settings["subagents"]["agentOverrides"].values()
    )
    assert "zen" in json.dumps(models)


def test_gateway_candidate_flows_through_runtime_only(tmp_path, monkeypatch):
    evidence = healthy_gateway(tmp_path, monkeypatch)
    snapshot = {
        "source": "synthetic",
        "observed_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "models": {
            "opencode": {
                "models": {
                    "big-pickle-free": {
                        "reasoning": True,
                        "tool_call": True,
                        "modalities": {"input": ["text", "image"], "output": ["text"]},
                    }
                }
            }
        },
    }
    registry = json.loads(
        (ROOT / "configs/opencode/oh-my-opencode-slim.json").read_text()
    )
    transformed, _ = prepare_dynamic_free_registry(
        registry,
        ["free"],
        {"DOTFILES_USE_LITELLM_PROXY": "1"},
        metadata_fetch=lambda: snapshot,
        clock=lambda: NOW,
        gateway_evidence=evidence,
        consumer="opencode",
    )
    emitted = transformed["presets"]["free"]["observer"]["model"]
    assert emitted == "zen"
    assert emitted in evidence["gateway_models"]["models"]
    assert (
        evidence["expected_config_routes"][0]["litellm_params"]["model"]
        == "opencode/big-pickle-free"
    )


@pytest.mark.parametrize("mode", ["global", "project"])
@pytest.mark.parametrize("alias", OPENCODE_GATEWAY_ALIASES)
@pytest.mark.parametrize(
    "preset,proxy",
    [(preset, proxy) for preset in PRESETS for proxy in (False, True)],
)
def test_opencode_main_resolves_dynamic_preset_and_materializes_provider(
    tmp_path, monkeypatch, mode, alias, preset, proxy
):
    evidence = healthy_gateway(tmp_path, monkeypatch, alias) if proxy else None
    calls, materialized = opencode_runtime(monkeypatch, gateway_evidence=evidence)
    home = tmp_path / "home"
    home.mkdir()
    config_dir = (
        home / ".config/opencode"
        if mode == "global"
        else tmp_path / "project/.opencode"
    )
    config_dir.mkdir(parents=True)
    config_path = config_dir / "opencode.json"
    config_path.write_text(json.dumps({"mcp": {"custom": {"keep": True}}}))
    slim_path = config_dir / "oh-my-opencode-slim.json"
    if mode == "global":
        slim_path.write_text("{}")

    configs = tmp_path / "configs"
    opencode_configs = configs / "opencode"
    opencode_configs.mkdir(parents=True)
    registry = json.loads(
        (ROOT / "configs/opencode/oh-my-opencode-slim.json").read_text()
    )
    (opencode_configs / "oh-my-opencode-slim.json").write_text(json.dumps(registry))
    for filename in (
        "openai-models.json",
        "ollama-cloud-models.json",
        "anthropic-models.json",
        "opencode-models.json",
        "github-copilot-models.json",
        "google-models.json",
        "openrouter-models.json",
    ):
        (opencode_configs / filename).write_text('{"models": {}}')
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    monkeypatch.setattr(configure_opencode, "SCRIPT_DIR", str(scripts))
    monkeypatch.setattr(configure_opencode, "CONFIGS_DIR", str(configs), raising=False)
    monkeypatch.setattr(configure_opencode, "load_env", lambda *args: False)
    monkeypatch.setattr(configure_opencode, "fetch_models_dev", lambda: {})
    monkeypatch.setattr(configure_opencode, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(
        configure_opencode, "build_opencode_server_config", lambda: None
    )
    monkeypatch.setattr(
        configure_opencode,
        "active_plugin_specs",
        lambda: ["@plannotator/opencode@test", "plugin-a"],
    )
    monkeypatch.setattr(
        configure_opencode,
        "plugin_specs",
        lambda: ["@plannotator/opencode@test", "plugin-a"],
    )
    monkeypatch.setattr(configure_opencode, "get_preset_providers", lambda *args: set())
    monkeypatch.setattr(configure_opencode, "get_litellm_proxy_mode", lambda: proxy)
    monkeypatch.setattr(
        configure_opencode,
        "get_slim_config_path",
        lambda: str(opencode_configs / "oh-my-opencode-slim.json"),
    )
    monkeypatch.setattr(
        configure_opencode, "build_model_entry", lambda name, *args: {"name": name}
    )

    def fake_child(args, **kwargs):
        return type("Result", (), {"stdout": "{}", "returncode": 0, "stderr": ""})()

    monkeypatch.setattr(configure_opencode.subprocess, "run", fake_child)
    real_tier_prepare = configure_opencode.prepare_tier_candidate

    tier_sources = []

    def tier_prepare(config_dir, args, consumer_config, base_slim=None, **kwargs):
        assert base_slim is not None
        tier_sources.append(json.loads(json.dumps(base_slim)))

        def run_tier(command, *, check, env):
            Path(env["OPENCODE_DIR"], "oh-my-opencode-slim.json").write_text(
                json.dumps(base_slim)
            )

        monkeypatch.setattr(configure_opencode.subprocess, "run", run_tier)
        return real_tier_prepare(
            config_dir, args, consumer_config, base_slim=base_slim, **kwargs
        )

    monkeypatch.setattr(configure_opencode, "prepare_tier_candidate", tier_prepare)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "configure-opencode.py",
            "--mode",
            mode,
            "--preset",
            preset,
            "--skip",
            "mcps,acp-agents,voice,dcp",
        ],
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    monkeypatch.setenv("DOTFILES_USE_LOCAL_OLLAMA", "0")
    expected_reference = f"litellm/{alias}"
    if proxy:
        clients = home / ".local/share/litellm/clients"
        clients.mkdir(parents=True)
        clients.chmod(0o700)
        key = clients / "opencode.key"
        key.write_text("synthetic")
        key.chmod(0o600)
        monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
        monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
        monkeypatch.setenv("LITELLM_ROOT", str(tmp_path / "litellm"))

    configure_opencode.main.__wrapped__()

    output = json.loads(config_path.read_text())
    if mode == "global":
        assert output["mcp"]["custom"] == {"keep": True}
    else:
        assert "mcp" not in output
    if proxy:
        assert output["provider"]["litellm"]["models"][alias]
        assert calls == [([preset], "1")]
        assert materialized["presets"][preset]["orchestrator"]["model"] == alias
    else:
        models = output["provider"]["opencode"]["models"]
        assert calls == [([preset], None)]
        assert "big-pickle" in models
    if mode == "global":
        assert len(tier_sources) == 1
        source = tier_sources[0]
        if proxy:
            assert (
                source["presets"][preset]["orchestrator"]["model"] == expected_reference
            )
            assert source["_tiers"][preset]["council"]["default_preset"] == preset
            assert (
                json.loads(slim_path.read_text())["presets"][preset]
                == source["presets"][preset]
            )
            return
        expected = materialized["presets"][preset]
        assert source["presets"][preset] == expected
        assert source["_tiers"][preset] == materialized["_tiers"][preset]
        assert (
            source["council"]["presets"][preset]
            == materialized["council"]["presets"][preset]
        )
        assert source["council"]["default_preset"] == preset
        installed = json.loads(slim_path.read_text())
        selected = installed["presets"][preset]
        assert installed["council"]["default_preset"] == preset
        models = output["provider"]["opencode"]["models"]
        for role in selected.values():
            ref = role.get("model") if isinstance(role, dict) else None
            if isinstance(ref, str) and ref.startswith("opencode/"):
                assert ref.split("/", 1)[1] in models


@pytest.mark.parametrize("preset", PRESETS)
def test_pi_entrypoint_resolves_dynamic_presets_and_preserves_custom_settings(
    tmp_path, monkeypatch, preset
):
    runtime(monkeypatch)
    agent = tmp_path / "pi-agent"
    agent.mkdir()
    settings = agent / "settings.json"
    settings.write_text(json.dumps({"custom": {"keep": True}}))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", os.environ["PATH"])
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("OPENCODE_API_KEY", "synthetic")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setattr(
        sys,
        "argv",
        ["configure-pi.py", "--preset", preset, "--no-local-fallbacks"],
    )

    assert configure_pi.main() is None
    output = json.loads(settings.read_text())
    assert output["custom"] == {"keep": True}
    assert "big-pickle" in json.dumps(output)


@pytest.mark.parametrize("source", ["flag", "env"])
def test_pi_dynamic_free_role_override_is_usage_error_before_discovery(
    monkeypatch, source
):
    args = ["configure-pi.py", "--preset", "free", "--no-local-fallbacks"]
    if source == "flag":
        args += ["--role-model", "orchestrator=paid/model"]
    elif source == "env":
        monkeypatch.setenv("DOTFILES_ROLE_MODELS", "orchestrator=paid/model")
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(
        configure_pi,
        "prepare_dynamic_free_registry",
        lambda *a, **k: pytest.fail("evidence must not be fetched"),
    )
    monkeypatch.setenv("DOTFILES_CATEGORY_MODELS", "")
    monkeypatch.setattr(
        configure_pi.tier_registry,
        "load_registry",
        lambda *a: json.loads(
            (ROOT / "configs/opencode/oh-my-opencode-slim.json").read_text()
        ),
    )

    with pytest.raises(SystemExit) as error:
        configure_pi.main()

    assert error.value.code == 2


def test_pi_implicit_dynamic_free_preset_override_fails_before_evidence(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["configure-pi.py", "--preset", "free"])
    monkeypatch.setattr(configure_pi, "load_env", lambda: None)
    monkeypatch.setattr(
        configure_pi.tier_registry,
        "load_registry",
        lambda *a: {
            "preset": "free",
            "presets": {"free": {"orchestrator": {"model": "free-model"}}},
            "_tiers": {"free": {}},
        },
    )
    monkeypatch.setenv("DOTFILES_ROLE_MODELS", "unknown-role=paid/model")
    monkeypatch.setattr(
        configure_pi,
        "prepare_dynamic_free_registry",
        lambda *a, **k: pytest.fail("evidence must not be fetched"),
    )

    with pytest.raises(SystemExit) as error:
        configure_pi.main()

    assert error.value.code == 2


@pytest.mark.parametrize("preset", PRESETS)
def test_junie_entrypoint_resolves_shared_dynamic_preset(tmp_path, monkeypatch, preset):
    calls = runtime(monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    groups = tmp_path / "groups.json"
    groups.write_text(
        json.dumps(
            {
                "sharedPresets": {preset: preset},
                "groups": {preset: {}},
                "providers": {
                    "opencode": {
                        "baseUrl": "https://opencode.ai/zen/v1",
                        "apiType": "OpenAICompletion",
                        "apiKeyEnv": "OPENCODE_API_KEY",
                    }
                },
            }
        )
    )
    target = tmp_path / "models"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", os.environ["PATH"])
    monkeypatch.setenv("OPENCODE_API_KEY", "synthetic")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setattr(
        junie.tier_registry,
        "load_registry",
        lambda *a: json.loads(
            (ROOT / "configs/opencode/oh-my-opencode-slim.json").read_text()
        ),
    )
    monkeypatch.setattr(junie, "read_junie_key", lambda: "synthetic-junie-key")
    monkeypatch.setattr(junie, "litellm_catalogue_models", lambda *a, **k: {"zen"})
    monkeypatch.setattr(junie, "parse_local_models", lambda *_: [])
    monkeypatch.setattr(junie, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate-jetbrains-profiles.py",
            "--groups-json",
            str(groups),
            "--target-dir",
            str(target),
        ],
    )

    junie.main()
    assert calls and calls[0][0] == "opencode"
    assert target.is_dir()
    assert "big-pickle" in " ".join(path.read_text() for path in target.glob("*.json"))


@pytest.mark.parametrize("preset", GATEWAY_PRESETS)
def test_junie_entrypoint_materializes_healthy_gateway_alias(
    tmp_path, monkeypatch, preset
):
    evidence = healthy_gateway(tmp_path, monkeypatch)
    snapshot = {
        "source": "synthetic",
        "observed_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "models": {
            "opencode": {
                "models": {
                    "big-pickle-free": {
                        "reasoning": True,
                        "tool_call": True,
                        "modalities": {"input": ["text", "image"], "output": ["text"]},
                    }
                }
            }
        },
    }

    def prepare(registry, requested, environ, **kwargs):
        kwargs.pop("consumer", None)
        return prepare_dynamic_free_registry(
            registry,
            requested,
            environ,
            metadata_fetch=lambda: snapshot,
            clock=lambda: NOW,
            gateway_evidence=evidence,
            consumer="junie",
            **kwargs,
        )

    monkeypatch.setattr(junie, "prepare_dynamic_free_registry", prepare)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".ai").mkdir()
    (home / ".junie").symlink_to(home / ".ai", target_is_directory=True)
    target = home / ".junie" / "models"
    groups = tmp_path / "groups.json"
    groups.write_text(
        json.dumps(
            {
                "sharedPresets": {preset: preset},
                "groups": {preset: {}},
                "providers": {
                    "litellm": {
                        "baseUrl": "http://127.0.0.1:4000/v1",
                        "apiType": "OpenAICompletion",
                        "apiKeyEnv": "LITELLM_JUNIE_KEY",
                    }
                },
            }
        )
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("LITELLM_JUNIE_KEY", "synthetic-private-key")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setattr(
        junie.tier_registry,
        "load_registry",
        lambda: json.loads(
            (ROOT / "configs/opencode/oh-my-opencode-slim.json").read_text()
        ),
    )
    monkeypatch.setattr(junie, "read_junie_key", lambda: "synthetic-private-key")
    monkeypatch.setattr(junie, "litellm_catalogue_models", lambda *a, **k: {"zen"})
    monkeypatch.setattr(junie, "parse_local_models", lambda *_: [])
    monkeypatch.setattr(junie, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate-jetbrains-profiles.py",
            "--groups-json",
            str(groups),
            "--target-dir",
            str(target),
        ],
    )

    junie.main()

    generated = json.loads((target / f"{preset}.json").read_text())
    assert generated["primaryModel"]["id"] == "zen"
    assert generated["fasterModel"]["id"] == "zen"
    assert generated["baseUrl"] == "http://127.0.0.1:4000/v1/chat/completions"
    assert (target / f"{preset}.json").stat().st_mode & 0o777 == 0o600
    assert "big-pickle-free" not in json.dumps(generated)


def test_junie_optional_unavailable_free_group_does_not_block_other_group(
    tmp_path, monkeypatch
):
    calls = runtime(monkeypatch, available=False)
    home = tmp_path / "home"
    home.mkdir()
    target = home / "models"
    groups = tmp_path / "groups.json"
    groups.write_text(
        json.dumps(
            {
                "sharedPresets": {"free": "free"},
                "groups": {
                    "ordinary": {"provider": "openai", "primaryModel": "ordinary-model"}
                },
                "providers": {
                    "openai": {
                        "baseUrl": "https://api.openai.com/v1",
                        "apiType": "OpenAIResponses",
                        "apiKeyEnv": "OPENCODE_API_KEY",
                    }
                },
            }
        )
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    monkeypatch.setenv("OPENCODE_API_KEY", "synthetic")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setattr(
        junie.tier_registry,
        "load_registry",
        lambda *a: json.loads(
            (ROOT / "configs/opencode/oh-my-opencode-slim.json").read_text()
        ),
    )
    monkeypatch.setattr(junie, "read_junie_key", lambda: "synthetic")
    monkeypatch.setattr(
        junie, "litellm_catalogue_models", lambda *a, **k: {"ordinary-model"}
    )
    monkeypatch.setattr(junie, "parse_local_models", lambda *_: [])
    monkeypatch.setattr(junie, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate-jetbrains-profiles.py",
            "--groups-json",
            str(groups),
            "--target-dir",
            str(target),
        ],
    )

    junie.main()

    assert (target / "ordinary.json").exists()
    assert not (target / "free.json").exists()
    assert len(calls) == 1


def test_junie_override_skips_optional_dynamic_group_and_generates_ordinary(
    tmp_path, monkeypatch
):
    runtime(monkeypatch, available=False)
    home = tmp_path / "home"
    home.mkdir()
    target = home / "models"
    groups = tmp_path / "groups.json"
    groups.write_text(
        json.dumps(
            {
                "sharedPresets": {"free": "free"},
                "groups": {
                    "ordinary": {"provider": "openai", "primaryModel": "ordinary-model"}
                },
                "providers": {
                    "openai": {
                        "baseUrl": "https://api.openai.com/v1",
                        "apiType": "OpenAIResponses",
                        "apiKeyEnv": "OPENCODE_API_KEY",
                    }
                },
            }
        )
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    monkeypatch.setenv("OPENCODE_API_KEY", "synthetic")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setattr(
        junie.tier_registry,
        "load_registry",
        lambda *a: json.loads(
            (ROOT / "configs/opencode/oh-my-opencode-slim.json").read_text()
        ),
    )
    monkeypatch.setattr(junie, "read_junie_key", lambda: "synthetic")
    monkeypatch.setattr(
        junie, "litellm_catalogue_models", lambda *a, **k: {"ordinary-model"}
    )
    monkeypatch.setattr(junie, "parse_local_models", lambda *_: [])
    monkeypatch.setattr(junie, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate-jetbrains-profiles.py",
            "--groups-json",
            str(groups),
            "--target-dir",
            str(target),
            "--role-model",
            "observer=openai/override-model",
        ],
    )

    junie.main()

    generated = json.loads((target / "ordinary.json").read_text())
    assert generated["primaryModel"]["id"] == "ordinary-model"
    assert not (target / "free.json").exists()


def test_junie_required_dynamic_group_with_override_fails_before_writes(
    tmp_path, monkeypatch
):
    runtime(monkeypatch, available=False)
    home = tmp_path / "home"
    home.mkdir()
    target = home / "models"
    target.mkdir()
    sentinel = target / "manual.json"
    sentinel.write_text('{"manual": true}')
    before = {path: path.read_bytes() for path in target.iterdir()}
    groups = tmp_path / "groups.json"
    groups.write_text(
        json.dumps(
            {
                "sharedPresets": {"free": "free"},
                "groups": {
                    "ordinary": {"provider": "openai", "primaryModel": "ordinary-model"}
                },
                "requiredGroups": ["free"],
            }
        )
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENCODE_API_KEY", "synthetic")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setattr(
        junie.tier_registry,
        "load_registry",
        lambda *a: json.loads(
            (ROOT / "configs/opencode/oh-my-opencode-slim.json").read_text()
        ),
    )
    monkeypatch.setattr(junie, "parse_local_models", lambda *_: [])
    monkeypatch.setattr(junie, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate-jetbrains-profiles.py",
            "--groups-json",
            str(groups),
            "--target-dir",
            str(target),
            "--role-model",
            "observer=openai/override-model",
        ],
    )

    with pytest.raises(SystemExit, match="1"):
        junie.main()

    assert {path: path.read_bytes() for path in target.iterdir()} == before


def test_junie_required_unavailable_free_group_fails_before_writes(
    tmp_path, monkeypatch
):
    runtime(monkeypatch, available=False)
    home = tmp_path / "home"
    home.mkdir()
    target = home / "models"
    target.mkdir()
    sentinel = target / "manual.json"
    sentinel.write_text('{"manual": true}')
    before = {path: path.read_bytes() for path in target.iterdir()}
    groups = tmp_path / "groups.json"
    groups.write_text(
        json.dumps(
            {
                "sharedPresets": {"free": "free"},
                "groups": {
                    "ordinary": {"provider": "openai", "primaryModel": "ordinary-model"}
                },
                "requiredGroups": ["free"],
            }
        )
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENCODE_API_KEY", "synthetic")
    monkeypatch.setenv("OPENCODE_API_KEY", "synthetic")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setattr(
        junie.tier_registry,
        "load_registry",
        lambda *a: json.loads(
            (ROOT / "configs/opencode/oh-my-opencode-slim.json").read_text()
        ),
    )
    monkeypatch.setattr(junie, "read_junie_key", lambda: "synthetic")
    monkeypatch.setattr(junie, "parse_local_models", lambda *_: [])
    monkeypatch.setattr(junie, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate-jetbrains-profiles.py",
            "--groups-json",
            str(groups),
            "--target-dir",
            str(target),
        ],
    )

    with pytest.raises(SystemExit, match="1"):
        junie.main()

    assert {path: path.read_bytes() for path in target.iterdir()} == before


@pytest.mark.parametrize("existing", [False, True])
def test_junie_required_unresolvable_group_leaves_all_output_untouched(
    tmp_path, monkeypatch, existing
):
    runtime(monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    target = home / "models"
    if existing:
        target.mkdir()
        sentinel = target / "manual.json"
        sentinel.write_text('{"manual": true}')
    before = (
        {
            path: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
            for path in target.iterdir()
        }
        if existing
        else {}
    )
    groups = tmp_path / "groups.json"
    groups.write_text(
        json.dumps(
            {
                "sharedPresets": {"free": "free"},
                "groups": {
                    "ordinary": {
                        "provider": "unknown",
                        "primaryModel": "ordinary-model",
                    }
                },
                "requiredGroups": ["free", "ordinary"],
            }
        )
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    monkeypatch.setenv("OPENCODE_API_KEY", "synthetic")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setattr(
        junie.tier_registry,
        "load_registry",
        lambda *a: json.loads(
            (ROOT / "configs/opencode/oh-my-opencode-slim.json").read_text()
        ),
    )
    monkeypatch.setattr(
        junie,
        "prepare_dynamic_free_registry",
        lambda registry, *_a, **_k: (registry, {}),
    )
    monkeypatch.setattr(junie, "parse_local_models", lambda *_: [])
    monkeypatch.setattr(junie, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate-jetbrains-profiles.py",
            "--groups-json",
            str(groups),
            "--target-dir",
            str(target),
        ],
    )

    with pytest.raises(ValueError, match="Required Junie group unavailable"):
        junie.main()

    assert target.exists() is existing
    if existing:
        assert {
            path: (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
            for path in target.iterdir()
        } == before


def test_junie_required_healthy_compatibility_group_is_preserved(tmp_path, monkeypatch):
    runtime(monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    groups = tmp_path / "groups.json"
    groups.write_text(
        json.dumps(
            {
                "sharedPresets": {"free": "free"},
                "groups": {},
                "compatibility": {"free-legacy": "free"},
                "requiredGroups": ["free-legacy"],
            }
        )
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    monkeypatch.setenv("OPENCODE_API_KEY", "synthetic")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setattr(
        junie.tier_registry,
        "load_registry",
        lambda *a: json.loads(
            (ROOT / "configs/opencode/oh-my-opencode-slim.json").read_text()
        ),
    )
    monkeypatch.setattr(junie, "read_junie_key", lambda: "synthetic")
    monkeypatch.setattr(
        junie,
        "build_provider_configs",
        lambda *_args: {
            "google": {
                "baseUrl": "https://example.test/v1",
                "apiType": "OpenAICompletion",
            },
            "opencode": {
                "baseUrl": "https://opencode.ai/zen/v1",
                "apiType": "OpenAICompletion",
            },
        },
    )
    monkeypatch.setattr(
        junie,
        "prepare_dynamic_free_registry",
        lambda registry, requested, *_args, **_kwargs: (
            registry,
            {"presets": {requested[0]: {"available": True}}},
        ),
    )
    monkeypatch.setattr(
        junie,
        "litellm_catalogue_models",
        lambda *a, **k: {"google/models/gemini-3.5-flash-lite"},
    )
    monkeypatch.setattr(junie, "parse_local_models", lambda *_: [])
    monkeypatch.setattr(junie, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate-jetbrains-profiles.py",
            "--groups-json",
            str(groups),
            "--target-dir",
            str(home / "models"),
        ],
    )

    junie.main()

    assert (home / "models/free.json").is_file()
    assert (home / "models/free-legacy.json").is_file()


def test_junie_optional_compatibility_to_unavailable_free_group_is_omitted(
    tmp_path, monkeypatch
):
    runtime(monkeypatch, available=False)
    home = tmp_path / "home"
    home.mkdir()
    groups = tmp_path / "groups.json"
    groups.write_text(
        json.dumps(
            {
                "sharedPresets": {"free": "free"},
                "groups": {
                    "ordinary": {"provider": "openai", "primaryModel": "ordinary-model"}
                },
                "compatibility": {"free-legacy": "free"},
                "providers": {
                    "openai": {
                        "baseUrl": "https://api.openai.com/v1",
                        "apiType": "OpenAIResponses",
                        "apiKeyEnv": "OPENAI_API_KEY",
                    }
                },
            }
        )
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setattr(
        junie.tier_registry,
        "load_registry",
        lambda *a: json.loads(
            (ROOT / "configs/opencode/oh-my-opencode-slim.json").read_text()
        ),
    )
    monkeypatch.setattr(junie, "read_junie_key", lambda: "synthetic")
    monkeypatch.setattr(junie, "parse_local_models", lambda *_: [])
    monkeypatch.setattr(junie, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate-jetbrains-profiles.py",
            "--groups-json",
            str(groups),
            "--target-dir",
            str(home / "models"),
        ],
    )

    junie.main()

    assert (home / "models/ordinary.json").is_file()
    assert not (home / "models/free-legacy.json").exists()


@pytest.mark.parametrize(
    "module,argv",
    [
        ("pi", lambda p: ["configure-pi.py", "--preset", p, "--no-local-fallbacks"]),
        (
            "junie",
            lambda p: [
                "generate-jetbrains-profiles.py",
                "--groups-json",
                "GROUPS",
                "--target-dir",
                "TARGET",
            ],
        ),
    ],
)
@pytest.mark.parametrize("preset", PRESETS)
def test_unknown_dynamic_route_fails_before_consumer_writes(
    tmp_path, monkeypatch, module, argv, preset
):
    runtime(monkeypatch, available=False)
    home = tmp_path / "home"
    home.mkdir()
    sentinel = home / "sentinel"
    sentinel.write_text("untouched")
    before = {
        p: (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
        for p in home.rglob("*")
        if p.is_file()
    }
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(home / "pi"))
    monkeypatch.setenv("OPENCODE_API_KEY", "synthetic")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    if module == "pi":
        entry = configure_pi
        args = argv(preset)
    else:
        entry = junie
        groups = tmp_path / "groups.json"
        groups.write_text(
            json.dumps(
                {
                    "sharedPresets": {preset: preset},
                    "groups": {preset: {}},
                    "requiredGroups": [preset],
                }
            )
        )
        args = argv(preset)
        args[args.index("GROUPS")] = str(groups)
        args[args.index("TARGET")] = str(home / "models")
        monkeypatch.setattr(
            junie.tier_registry,
            "load_registry",
            lambda *a: json.loads(
                (ROOT / "configs/opencode/oh-my-opencode-slim.json").read_text()
            ),
        )
        monkeypatch.setattr(junie, "read_junie_key", lambda: "synthetic")
        monkeypatch.setattr(junie, "litellm_catalogue_models", lambda *a, **k: set())
        monkeypatch.setattr(junie, "parse_local_models", lambda *_: [])
        monkeypatch.setattr(junie, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(sys, "argv", args)
    if module == "junie":
        with pytest.raises(SystemExit, match="1"):
            entry.main()
    else:
        assert entry.main() == 1
    after = {
        p: (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
        for p in home.rglob("*")
        if p.is_file()
    }
    assert after == before
