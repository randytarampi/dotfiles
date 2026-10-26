import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch

import tier_resolve

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
                "groups": {},
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
