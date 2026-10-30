import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import tier_resolve
import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[2]
SCRIPT = REPO_ROOT / "scripts/configure-opencode-tier.py"
SPEC = importlib.util.spec_from_file_location("configure_opencode_tier", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
configure_tier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure_tier)

JETBRAINS_SCRIPT = REPO_ROOT / "scripts/generate-jetbrains-profiles.py"
JETBRAINS_SPEC = importlib.util.spec_from_file_location(
    "generate_jetbrains_profiles", JETBRAINS_SCRIPT
)
assert JETBRAINS_SPEC is not None and JETBRAINS_SPEC.loader is not None
jetbrains_profiles = importlib.util.module_from_spec(JETBRAINS_SPEC)
JETBRAINS_SPEC.loader.exec_module(jetbrains_profiles)


@pytest.mark.parametrize("preset", ["free", "omo-slim-opencode-zen-free"])
def test_dynamic_tier_uses_runtime_and_preserves_installed_custom_state(
    tmp_path, preset
):
    from free_preset_runtime import prepare_dynamic_free_registry

    now = datetime(2026, 10, 30, tzinfo=timezone.utc)
    snapshot = {
        "source": "fixture",
        "observed_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "models": {
            "opencode": {
                "models": {
                    "big-pickle": {
                        "reasoning": True,
                        "tool_call": True,
                        "modalities": {"input": ["text", "image"], "output": ["text"]},
                    }
                }
            }
        },
    }

    def resolve(registry, requested, environ, **kwargs):
        return prepare_dynamic_free_registry(
            registry,
            requested,
            environ,
            metadata_fetch=lambda: snapshot,
            route_fetch=lambda *_: ["big-pickle"],
            clock=lambda: now,
            **kwargs,
        )

    target = tmp_path / "oh-my-opencode-slim.json"
    target.write_text(json.dumps({"preset": preset, "custom": {"keep": 7}}))
    with (
        patch.dict(
            os.environ,
            {
                "HOME": str(tmp_path),
                "OPENCODE_DIR": str(tmp_path),
                "OPENCODE_API_KEY": "synthetic",
                "DOTFILES_USE_LITELLM_PROXY": "0",
            },
            clear=True,
        ),
        patch.object(configure_tier, "prepare_dynamic_free_registry", resolve),
        patch.object(configure_tier, "list_local_ollama_models", return_value=[]),
    ):
        configure_tier.orchestrate_tier_switch(preset, False, [], dry_run=False)
    result = json.loads(target.read_text())
    assert result["custom"] == {"keep": 7}
    assert result["presets"][preset]["observer"]["model"] == "opencode/big-pickle"
    assert (
        result["council"]["presets"][preset]["beta"]["model"] == "opencode/big-pickle"
    )


def test_real_dynamic_tier_child_routes_validated_gateway_alias(tmp_path, monkeypatch):
    from free_route_manifest import build_manifest

    alias = "zen"
    route = {
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
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=5)
            ).isoformat(),
        },
    }
    manifest = build_manifest([route])
    evidence_dir = tmp_path / "litellm"
    (evidence_dir / "clients").mkdir(parents=True)
    (evidence_dir / "config.yaml").write_text(
        "model_list:\n  - model_name: zen\n    litellm_params:\n      model: opencode/big-pickle-free\n      api_base: https://opencode.ai/zen/v1\n"
    )
    (evidence_dir / "free-route-manifest.json").write_text(json.dumps(manifest))
    (evidence_dir / "free-route-config.json").write_text(
        json.dumps(
            {
                "config_binding_sha256": manifest["config_binding_sha256"],
                "routes": [route],
            }
        )
    )
    key_file = evidence_dir / "clients/opencode.key"
    key_file.write_text("synthetic")
    key_file.chmod(0o600)
    target = tmp_path / "opencode"
    target.mkdir()
    (target / "opencode.json").write_text("{}")
    target_file = target / "oh-my-opencode-slim.json"
    target_file.write_text("{}")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENCODE_DIR", str(target))
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("LITELLM_ROOT", str(evidence_dir))
    key_path = tmp_path / ".local/share/litellm/clients"
    key_path.mkdir(parents=True)
    client_key = key_path / "opencode.key"
    client_key.write_text("synthetic")
    client_key.chmod(0o600)
    monkeypatch.setattr(configure_tier, "get_litellm_proxy_mode", lambda: True)
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        "model_catalogues.get_catalogue",
        lambda *args, **kwargs: {"data": [{"id": "zen"}]},
    )
    real_prepare = configure_tier.prepare_dynamic_free_registry

    def prepare(registry, requested, environ):
        snapshot = {
            "source": "models.dev",
            "observed_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
            "models": {
                "opencode": {
                    "models": {
                        "big-pickle-free": {
                            "reasoning": True,
                            "tool_call": True,
                            "modalities": {
                                "input": ["text", "image"],
                                "output": ["text"],
                            },
                        }
                    }
                }
            },
        }
        return real_prepare(
            registry,
            requested,
            environ,
            metadata_fetch=lambda: snapshot,
            route_fetch=lambda *_: ["big-pickle-free"],
            clock=lambda: now,
            gateway_evidence={
                "manifest": manifest,
                "expected_config_routes": [route],
                "gateway_models": {
                    "source": "fixture",
                    "observed_at": now.isoformat(),
                    "expires_at": (now + timedelta(minutes=5)).isoformat(),
                    "models": ["zen"],
                },
                "trusted_endpoints": {"https://opencode.ai/zen/v1": "opencode"},
                "trusted_native": {},
            },
        )

    monkeypatch.setattr(configure_tier, "prepare_dynamic_free_registry", prepare)
    monkeypatch.setattr(configure_tier, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(
        configure_tier, "load_effective_opencode_config", lambda _dir: {}
    )
    original_spec = configure_tier.importlib.util.spec_from_file_location
    monkeypatch.setattr(
        configure_tier.importlib.util,
        "spec_from_file_location",
        lambda name, path: original_spec(name, path),
    )
    helper_spec = importlib.util.spec_from_file_location(
        "configure_opencode_transport", REPO_ROOT / "scripts/configure-opencode.py"
    )
    assert helper_spec is not None and helper_spec.loader is not None
    helper = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    monkeypatch.setattr(helper, "apply_litellm_slim_gate", lambda slim, *_: slim)
    configure_tier.orchestrate_tier_switch(
        "omo-slim-opencode-zen-free", True, [], dry_run=False
    )
    output = json.loads(target_file.read_text())
    assert (
        output["presets"]["omo-slim-opencode-zen-free"]["observer"]["model"]
        == "litellm/zen"
    )


@pytest.mark.parametrize("preset", ["free", "omo-slim-opencode-zen-free"])
@pytest.mark.parametrize("existing", [False, True])
def test_dynamic_tier_evidence_refusal_preserves_directory_snapshot(
    tmp_path, preset, existing
):
    from free_preset_runtime import DynamicFreeRegistryError

    directory = tmp_path / "config"
    target = directory / "oh-my-opencode-slim.json"
    if existing:
        directory.mkdir()
        target.write_bytes(b'{"custom":"preserve"}\r\n')
        target.chmod(0o640)
    before = (
        (target.read_bytes(), target.stat().st_mode, target.stat().st_mtime_ns)
        if existing
        else None
    )
    with (
        patch.dict(
            os.environ,
            {
                "HOME": str(tmp_path),
                "OPENCODE_DIR": str(directory),
                "DOTFILES_USE_LITELLM_PROXY": "0",
            },
            clear=True,
        ),
        patch.object(
            configure_tier,
            "prepare_dynamic_free_registry",
            side_effect=DynamicFreeRegistryError("unknown evidence"),
        ),
        patch.object(configure_tier, "list_local_ollama_models") as discovery,
    ):
        with pytest.raises(SystemExit, match="1"):
            configure_tier.orchestrate_tier_switch(preset, False, [], dry_run=False)
    discovery.assert_not_called()
    if existing:
        assert (
            target.read_bytes(),
            target.stat().st_mode,
            target.stat().st_mtime_ns,
        ) == before
        assert list(directory.iterdir()) == [target]
    else:
        assert not directory.exists()


def test_council_null_preset_values_are_ignored_without_crashing(tmp_path):
    config = {
        "preset": "local",
        "providers": {},
        "agents": {},
        "_tiers": {
            name: {
                "council": {"presets": {name: None}},
                "profile": {"name": "balanced"},
            }
            for name in ("local", "local-quiet", "local-loud")
        },
        "presets": {
            name: {
                "orchestrator": {"model": "_local:code-gen"},
                "council": {"model": "_local:reasoning"},
            }
            for name in ("local", "local-quiet", "local-loud")
        },
    }
    source = tmp_path / "source.json"
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = target_dir / "oh-my-opencode-slim.json"
    source.write_text(json.dumps(config), encoding="utf-8")
    target.write_text(json.dumps(config), encoding="utf-8")
    with (
        patch.dict(os.environ, {"OPENCODE_DIR": str(target_dir)}),
        patch.object(
            configure_tier.tier_registry, "load_registry", return_value=config
        ),
        patch.object(
            configure_tier,
            "list_local_ollama_models",
            return_value=["mellum-small", "ornith-large"],
        ),
        patch.object(configure_tier, "logger") as logger,
    ):
        for tier in ("local-quiet", "local", "local-loud"):
            configure_tier.orchestrate_tier_switch(tier, False, [], dry_run=True)
    assert logger.info.called


def test_null_council_diversity_entry_is_ignored(tmp_path):
    config = {
        "preset": "local",
        "presets": {"local": {"orchestrator": {"model": "_local:code-gen"}}},
        "_tiers": {
            "local": {
                "council": {"presets": {"local": None}},
                "profile": {"name": "balanced"},
            }
        },
    }
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "oh-my-opencode-slim.json").write_text("{}", encoding="utf-8")
    with (
        patch.dict(os.environ, {"OPENCODE_DIR": str(target_dir)}),
        patch.object(
            configure_tier.tier_registry, "load_registry", return_value=config
        ),
        patch.object(
            configure_tier, "list_local_ollama_models", return_value=["mellum-small"]
        ),
        patch.object(configure_tier, "logger"),
    ):
        configure_tier.orchestrate_tier_switch("local", False, [], dry_run=True)


def test_jetbrains_dry_run_materializes_profile_tiers(tmp_path, monkeypatch, caplog):
    # Isolate HOME: the canonical jetbrains-profiles manifest lives under
    # Path.home(); a real machine ledger records the real target directory,
    # refusing this test's tmp target ("target directory mismatch").
    monkeypatch.setenv("HOME", str(tmp_path))
    groups = tmp_path / "groups.json"
    groups.write_text(
        json.dumps(
            {
                "groups": {"local": {"provider": "ollama", "primaryModel": "model"}},
                "providers": {"ollama": {"baseUrl": "http://localhost:11434/v1"}},
            }
        ),
        encoding="utf-8",
    )
    registry = configure_tier.tier_registry.load_registry()
    monkeypatch.setattr(
        jetbrains_profiles.tier_registry, "load_registry", lambda: registry
    )
    monkeypatch.setattr(
        jetbrains_profiles,
        "build_provider_configs",
        lambda *_: {"ollama": {"baseUrl": "http://localhost:11434/v1"}},
    )
    monkeypatch.setattr(jetbrains_profiles, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(jetbrains_profiles, "engine_gate_active", lambda *_: False)
    monkeypatch.setattr(
        jetbrains_profiles, "append_pool_profile_specs", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(jetbrains_profiles, "get_litellm_proxy_mode", lambda: False)
    monkeypatch.setattr(
        "sys.argv",
        [
            "generate-jetbrains-profiles.py",
            "--groups-json",
            str(groups),
            "--target-dir",
            str(tmp_path / "profiles"),
            "--local-models",
            "mellum-small ornith-large gemma4:12b qwen3:8b",
            "--dry-run",
        ],
    )
    with (
        patch.object(
            jetbrains_profiles,
            "parse_local_models",
            return_value=["mellum-small", "ornith-large", "gemma4:12b", "qwen3:8b"],
        ),
        patch.object(
            tier_resolve,
            "get_model_details",
            return_value={"param_count": 8, "capabilities": ["completion", "tools"]},
        ),
        caplog.at_level("INFO"),
    ):
        jetbrains_profiles.main()
    assert "Would configure: local-quiet" in caplog.text
    assert "Would configure: local →" in caplog.text
    assert "Would configure: local-loud" in caplog.text


def test_get_available_tiers_includes_deprecated_aliases():
    import opencode_config
    from tier_registry import DEPRECATED_TIER_ALIASES

    tiers = opencode_config.get_available_tiers()
    for alias, canonical in DEPRECATED_TIER_ALIASES.items():
        assert alias in tiers
        assert canonical in tiers


def test_dynamic_free_unavailable_refused_before_writes(tmp_path, monkeypatch):
    target = tmp_path / "oh-my-opencode-slim.json"

    def refuse(*args, **kwargs):
        raise ValueError("no evidence")

    with (
        patch.dict(os.environ, {"OPENCODE_DIR": str(tmp_path)}),
        patch.object(configure_tier, "prepare_dynamic_free_registry", refuse),
        patch.object(configure_tier, "list_local_ollama_models") as discovery,
        patch.object(configure_tier.os, "makedirs") as create_dir,
    ):
        try:
            configure_tier.orchestrate_tier_switch("free", False, [])
        except SystemExit as error:
            assert error.code == 1
        else:
            raise AssertionError("unresolved dynamic free preset was accepted")
    discovery.assert_not_called()
    create_dir.assert_not_called()
    assert not target.exists()


def test_dynamic_free_merge_preserves_custom_settings_and_original_snapshot():
    target = {"custom": {"sentinel": True}, "presets": {"local": {}}}
    resolved = {
        "presets": {"free": {"orchestrator": {"model": "poolside/x"}}},
        "_tiers": {"free": {"fallback": {}}},
        "council": {"default_preset": "local"},
    }
    merged = configure_tier._merge_dynamic_free_registry(target, resolved, "free")
    assert merged["custom"] == {"sentinel": True}
    assert target == {"custom": {"sentinel": True}, "presets": {"local": {}}}
    assert merged["presets"]["free"]["orchestrator"]["model"] == "poolside/x"


def test_disabled_fallback_loaded_from_home_env_rejected_before_writes(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text(
        "DOTFILES_LOCAL_FALLBACK_PRESET=free\n", encoding="utf-8"
    )
    target = home / ".config/opencode/oh-my-opencode-slim.json"
    with (
        patch.dict(os.environ, {"HOME": str(home)}, clear=True),
        patch.object(configure_tier, "list_local_ollama_models") as discovery,
        patch.object(configure_tier.os, "makedirs") as create_dir,
        patch.object(configure_tier.sys, "argv", [str(SCRIPT), "--preset", "local"]),
    ):
        try:
            configure_tier.main()
        except SystemExit as error:
            assert error.code == 1
        else:
            raise AssertionError("disabled loaded fallback was accepted")
    discovery.assert_not_called()
    create_dir.assert_not_called()
    assert not target.exists()


def test_min_reasoning_embedding_default_comes_from_home_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("DOTFILES_MIN_REASONING_EMBEDDING=37\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DOTFILES_MIN_REASONING_EMBEDDING", raising=False)
    observed = {}

    def capture(*args, **kwargs):
        observed["min_reasoning_embedding"] = kwargs["min_reasoning_embedding"]

    monkeypatch.setattr(configure_tier, "orchestrate_tier_switch", capture)
    monkeypatch.setattr(configure_tier.sys, "argv", [str(SCRIPT), "--preset", "local"])
    with patch.dict(os.environ):
        configure_tier.main()
    assert observed["min_reasoning_embedding"] == 37


def test_explicit_min_reasoning_embedding_overrides_environment(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("DOTFILES_MIN_REASONING_EMBEDDING=37\n")
    monkeypatch.setenv("HOME", str(home))
    observed = {}
    monkeypatch.setattr(
        configure_tier,
        "orchestrate_tier_switch",
        lambda *a, **kwargs: observed.update(kwargs),
    )
    monkeypatch.setattr(
        configure_tier.sys,
        "argv",
        [str(SCRIPT), "--preset", "local", "--min-reasoning-embedding", "19"],
    )
    with patch.dict(os.environ):
        configure_tier.main()
    assert observed["min_reasoning_embedding"] == 19


def test_malformed_reasoning_embedding_has_usage_diagnostic(
    tmp_path, monkeypatch, capsys
):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text("DOTFILES_MIN_REASONING_EMBEDDING=bad\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(configure_tier.sys, "argv", [str(SCRIPT), "--preset", "local"])
    try:
        with patch.dict(os.environ):
            configure_tier.main()
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("malformed environment value was accepted")
    assert "Invalid DOTFILES_MIN_REASONING_EMBEDDING" in capsys.readouterr().err


def test_tier_script_accepts_deprecated_alias_preset_with_isolated_environment(
    tmp_path,
):
    home = tmp_path / "home"
    home.mkdir()
    environment = {"HOME": str(home), "PATH": os.environ.get("PATH", "")}
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--preset", "local-pro", "--dry-run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env=environment,
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_jetbrains_disabled_fallback_loaded_from_home_env_refused(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".env").write_text(
        "DOTFILES_LOCAL_FALLBACK_PRESET=omo-slim-opencode-zen-free\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("DOTFILES_LOCAL_FALLBACK_PRESET", raising=False)
    groups = tmp_path / "groups.json"
    groups.write_text("{}", encoding="utf-8")
    output = tmp_path / "profiles"
    monkeypatch.setattr(
        "sys.argv",
        [
            "generate-jetbrains-profiles.py",
            "--groups-json",
            str(groups),
            "--target-dir",
            str(output),
        ],
    )
    with (
        patch.dict(os.environ),
        patch.object(jetbrains_profiles, "list_local_ollama_models") as discovery,
    ):
        try:
            jetbrains_profiles.main()
        except SystemExit as error:
            assert error.code == 2
        else:
            raise AssertionError("disabled loaded fallback was accepted")
    discovery.assert_not_called()
    assert not output.exists()


def test_disabled_slim_export_drops_presets_tiers_and_councils():
    from unavailable_presets import without_disabled_presets

    source = json.loads(
        (REPO_ROOT / "configs/opencode/oh-my-opencode-slim.json").read_text()
    )
    clean = without_disabled_presets(source)
    for key in ("presets", "_tiers", "agents"):
        assert not ({"free", "omo-slim-opencode-zen-free"} & set(clean.get(key, {})))
    for tier in clean.get("_tiers", {}).values():
        assert not (
            {"free", "omo-slim-opencode-zen-free"}
            & set(tier.get("council", {}).get("presets", {}))
        )


def test_disabled_slim_rejects_active_council_default_and_strips_top_level_choices():
    from unavailable_presets import without_disabled_presets

    source = json.loads(
        (REPO_ROOT / "configs/opencode/oh-my-opencode-slim.json").read_text()
    )
    source["council"] = {"default_preset": "free", "presets": {"free": {}}}
    try:
        without_disabled_presets(source)
    except ValueError as error:
        assert "council preset 'free'" in str(error)
    else:
        raise AssertionError("disabled active council default was accepted")

    source["council"] = {
        "default_preset": "local",
        "presets": {"local": {}, "free": {}},
    }
    clean = without_disabled_presets(source)
    assert set(clean["council"]["presets"]) == {"local"}
    assert "free" not in clean["council"]["presets"]
    source["council"] = {
        "default_preset": "local",
        "presets": {"local": {}, "free": {}},
    }
    assert set(without_disabled_presets(source)["council"]["presets"]) == {"local"}
    for name, tier in clean["_tiers"].items():
        assert tier["council"]["default_preset"] not in {
            "free",
            "omo-slim-opencode-zen-free",
        }


def test_sanitizer_rejects_unavailable_tier_council_default():
    from unavailable_presets import without_disabled_presets

    source = json.loads(
        (REPO_ROOT / "configs/opencode/oh-my-opencode-slim.json").read_text()
    )
    source["_tiers"]["local"]["council"]["default_preset"] = "free"
    try:
        without_disabled_presets(source)
    except ValueError as error:
        assert "Tier 'local'" in str(error)
    else:
        raise AssertionError("unavailable council default remained selectable")


def test_tier_script_accepts_deprecated_alias_preset():
    import subprocess
    import sys

    script = REPO_ROOT / "scripts" / "configure-opencode-tier.py"
    result = subprocess.run(
        [sys.executable, str(script), "--preset", "local-pro", "--dry-run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_dynamic_free_production_guards_survive_optimized_python():
    """Validation must not disappear under python -O/PYTHONOPTIMIZE."""
    import ast

    module = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "orchestrate_tier_switch"
    )
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(function))
    for name, expected_count in (
        ("dynamic_source_registry", 1),
        ("dynamic_audit", 2),
    ):
        guards = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == name
            and len(node.test.ops) == 1
            and isinstance(node.test.ops[0], ast.Is)
            and len(node.test.comparators) == 1
            and isinstance(node.test.comparators[0], ast.Constant)
            and node.test.comparators[0].value is None
        ]
        assert len(guards) == expected_count
        for guard in guards:
            block = ast.fix_missing_locations(ast.Module(body=[guard], type_ignores=[]))
            optimized = compile(block, str(SCRIPT), "exec", optimize=2)
            with pytest.raises(RuntimeError, match="Dynamic free"):
                exec(optimized, {name: None})
            exec(optimized, {name: object()})
