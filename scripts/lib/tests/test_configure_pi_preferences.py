import contextlib
import importlib.util
import json
import os
import sys
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import local_engines
import pi_profiles
import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(TESTS_DIR, "..", "..", ".."))

PI_SPEC = importlib.util.spec_from_file_location(
    "configure_pi",
    os.path.join(REPO_ROOT, "scripts", "configure-pi.py"),
)
assert PI_SPEC is not None and PI_SPEC.loader is not None
configure_pi = importlib.util.module_from_spec(PI_SPEC)
PI_SPEC.loader.exec_module(configure_pi)


@pytest.mark.parametrize(
    "document",
    [
        "[]",
        "null",
        '{"subagents": []}',
        '{"subagents": null}',
        '{"subagents": {"agentOverrides": []}}',
        '{"subagents": {"agentOverrides": null}}',
    ],
)
def test_invalid_previous_settings_refuse_before_side_effects(
    tmp_path, document, monkeypatch
):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(document, encoding="utf-8")
    settings_path.chmod(0o640)
    before = (
        settings_path.read_bytes(),
        settings_path.stat().st_mtime_ns,
        settings_path.stat().st_mode,
    )

    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["configure-pi.py", "--preset", "pro-plus"])
    inventory = sorted(tmp_path.rglob("*"))
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(configure_pi, "load_env", lambda: None))
        probes = [
            stack.enter_context(
                patch.object(configure_pi, name, side_effect=AssertionError(name))
            )
            for name in (
                "list_local_ollama_models",
                "_ensure_packages",
                "_cleanup_generated_agents",
                "write_text_file",
                "backup_file",
                "seed_plugin_configs",
            )
        ]
        assert configure_pi.main() == 1

    for probe in probes:
        probe.assert_not_called()
    assert sorted(tmp_path.rglob("*")) == inventory
    assert (
        settings_path.read_bytes(),
        settings_path.stat().st_mtime_ns,
        settings_path.stat().st_mode,
    ) == before


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("nested", [False, True])
def test_invalid_json_constants_refuse_main_before_side_effects(
    tmp_path, constant, nested, monkeypatch
):
    document = f'{{"nested": {constant}}}' if nested else constant
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(document, encoding="utf-8")
    settings_path.chmod(0o640)
    before = (
        settings_path.read_bytes(),
        settings_path.stat().st_mtime_ns,
        settings_path.stat().st_mode,
    )
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["configure-pi.py", "--preset", "pro-plus"])
    inventory = sorted(tmp_path.rglob("*"))
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(configure_pi, "load_env", lambda: None))
        probes = [
            stack.enter_context(
                patch.object(configure_pi, name, side_effect=AssertionError(name))
            )
            for name in (
                "list_local_ollama_models",
                "_ensure_packages",
                "_cleanup_generated_agents",
                "write_text_file",
                "backup_file",
                "seed_plugin_configs",
            )
        ]
        assert configure_pi.main() == 1
    for probe in probes:
        probe.assert_not_called()
    assert sorted(tmp_path.rglob("*")) == inventory
    assert (
        settings_path.read_bytes(),
        settings_path.stat().st_mtime_ns,
        settings_path.stat().st_mode,
    ) == before


def test_previous_settings_accepts_legacy_missing_nested_fields(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    assert configure_pi._load_previous_settings(settings_path) == {}


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("nested", [False, True])
def test_previous_settings_rejects_nonstandard_json_constants(
    tmp_path, constant, nested
):
    value = f'{{"nested": {constant}}}' if nested else constant
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(value, encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed settings.json"):
        configure_pi._load_previous_settings(settings_path)


@pytest.mark.parametrize(
    "change", ["replace", "delete", "create", "initial-error", "prewrite-error"]
)
def test_pi_main_refuses_settings_changed_during_discovery_before_any_writes(
    tmp_path, monkeypatch, change
):
    agent_dir = tmp_path / "pi-agent"
    agent_dir.mkdir()
    settings_path = agent_dir / "settings.json"
    initial = '{"tuiMode": "split"}'
    latest = '{"tuiMode": "user-saved"}'
    if change != "create":
        settings_path.write_text(initial, encoding="utf-8")
    initial_state = (
        (
            settings_path.read_bytes(),
            settings_path.stat().st_mtime_ns,
            settings_path.stat().st_mode,
        )
        if settings_path.exists()
        else None
    )
    if change in ("initial-error", "prewrite-error"):
        original_snapshot = configure_pi._settings_snapshot
        calls = []

        def snapshot_with_error(path):
            calls.append(path)
            if change == "initial-error" or len(calls) == 2:
                raise PermissionError("fixture read failure")
            return original_snapshot(path)

        monkeypatch.setattr(configure_pi, "_settings_snapshot", snapshot_with_error)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent_dir))
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "0")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    monkeypatch.setattr(sys, "argv", ["configure-pi.py", "--preset", "free"])

    intervening_state = []

    def save_during_discovery():
        if change == "delete":
            settings_path.unlink()
        elif change == "replace":
            replacement = agent_dir / "replacement.json"
            replacement.write_text(latest, encoding="utf-8")
            replacement.replace(settings_path)
        elif change == "prewrite-error":
            pass
        else:
            settings_path.write_text(latest, encoding="utf-8")
        intervening_state.append(
            (
                (
                    settings_path.read_bytes(),
                    settings_path.stat().st_mtime_ns,
                    settings_path.stat().st_mode,
                )
                if settings_path.exists()
                else None
            )
        )
        return []

    monkeypatch.setattr(configure_pi, "list_local_ollama_models", save_during_discovery)
    monkeypatch.setattr(configure_pi, "load_env", lambda: None)
    side_effects = [
        patch.object(configure_pi, name, side_effect=AssertionError(name))
        for name in (
            "write_text_file",
            "backup_file",
            "_cleanup_generated_agents",
            "seed_plugin_configs",
            "_ensure_packages",
        )
    ]
    with contextlib.ExitStack() as stack:
        probes = [stack.enter_context(effect) for effect in side_effects]
        assert configure_pi.main() == 1
    for probe in probes:
        probe.assert_not_called()
    assert settings_path.exists() == (change != "delete")
    if settings_path.exists():
        assert (
            settings_path.read_bytes(),
            settings_path.stat().st_mtime_ns,
            settings_path.stat().st_mode,
        ) == (intervening_state[0] if intervening_state else initial_state)
    assert sorted(path.name for path in agent_dir.iterdir()) == (
        [] if change == "delete" else ["settings.json"]
    )


@pytest.mark.parametrize("active", [None, b"[]", b'{"custom": true}'])
def test_profiles_only_never_touches_active_state(tmp_path, monkeypatch, active):
    agent = tmp_path / "agent"
    agent.mkdir()
    settings = agent / "settings.json"
    if active is not None:
        settings.write_bytes(active)
        settings.chmod(0o640)
    before = (settings.read_bytes(), settings.stat()) if settings.exists() else None
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))

    def forbidden(*args, **kwargs):
        raise AssertionError("active pipeline reached")

    for name in (
        "load_env",
        "list_local_ollama_models",
        "_ensure_packages",
        "seed_plugin_configs",
        "write_text_file",
        "backup_file",
    ):
        monkeypatch.setattr(configure_pi, name, forbidden)
    monkeypatch.setattr(
        sys, "argv", ["configure-pi.py", "--preset", "anthropic", "--profiles-only"]
    )
    assert configure_pi.main() == 0
    target = agent / "profiles/pi-subagents/anthropic.json"
    first = (target.read_bytes(), target.stat().st_mtime_ns)
    assert target.stat().st_mode & 0o777 == 0o600
    assert configure_pi.main() == 0
    assert (target.read_bytes(), target.stat().st_mtime_ns) == first
    after = (settings.read_bytes(), settings.stat()) if settings.exists() else None
    assert after == before
    assert not (agent / "models.json").exists()
    assert not (agent / "auth.json").exists()


def test_profiles_only_dry_run_and_manual_collision(tmp_path, monkeypatch):
    agent = tmp_path / "agent"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    argv = ["configure-pi.py", "--preset", "anthropic", "--profiles-only"]
    monkeypatch.setattr(sys, "argv", argv + ["--dry-run"])
    assert configure_pi.main() == 0
    assert not agent.exists()
    target = agent / "profiles/pi-subagents/anthropic.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"manual")
    monkeypatch.setattr(sys, "argv", argv)
    assert configure_pi.main() == 1
    assert target.read_bytes() == b"manual"


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--skip", "mcps"),
        ("--ollama-base-url", "http://localhost:11434"),
        ("--min-reasoning-embedding", "1"),
        ("--no-local-fallbacks", None),
        ("--local-fallback-preset", "free"),
        ("--local-fallback-role", "worker=x/y"),
        ("--local-fallback-placeholder", "worker=x/y"),
        ("--no-backup", None),
    ],
)
def test_profiles_only_rejects_unused_flags_before_registry_or_file_access(
    tmp_path, monkeypatch, flag, value
):
    agent = tmp_path / "agent"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    monkeypatch.setattr(
        sys,
        "argv",
        ["configure-pi.py", "--preset", "anthropic", "--profiles-only", flag]
        + ([value] if value is not None else []),
    )
    monkeypatch.setattr(
        configure_pi.tier_registry,
        "load_registry",
        lambda *_: pytest.fail("registry read"),
    )
    with pytest.raises(SystemExit) as result:
        configure_pi.main()
    assert result.value.code == 2
    assert not agent.exists()


@pytest.mark.parametrize("mode", [0o755, 0o700])
def test_profile_planning_matches_execution_directory_validation(tmp_path, mode):
    agent = tmp_path / "agent"
    target_leaf = agent / "profiles/pi-subagents"
    target_leaf.mkdir(parents=True)
    target_leaf.chmod(mode)
    profile = {"subagents": {"agentOverrides": {"worker": {"model": "x/y"}}}}
    plan = pi_profiles.plan_profile(agent, "test", profile, "source")
    expected = "GENERATE_ONLY" if mode == 0o700 else "REFUSED_OWNERSHIP"
    assert plan["disposition"] == expected
    if mode == 0o700:
        assert pi_profiles.create_profile(plan) == expected


def test_absent_profile_directories_are_feasible_in_read_only_planning(tmp_path):
    agent = tmp_path / "agent"
    profile = {"subagents": {"agentOverrides": {"worker": {"model": "x/y"}}}}
    plan = pi_profiles.plan_profile(agent, "test", profile, "source")
    assert plan["disposition"] == "GENERATE_ONLY"
    assert not agent.exists()


def test_profile_provenance_and_competing_creation(tmp_path):
    from pi_profiles import create_profile, plan_profile

    profile = {"subagents": {"agentOverrides": {"worker": {"model": "x/y"}}}}
    for name in ("first", "second"):
        plan = plan_profile(tmp_path, name, profile, "source")
        assert create_profile(plan) == "GENERATE_ONLY"
        assert (
            plan_profile(tmp_path, name, profile, "source")["disposition"]
            == "UNCHANGED"
        )
        assert plan["manifest"].stat().st_mode & 0o777 == 0o600
    plan["target"].write_bytes(b"manual edit")
    assert (
        plan_profile(tmp_path, "second", profile, "source")["disposition"]
        == "REFUSED_OWNERSHIP"
    )
    raced = plan_profile(tmp_path, "third", profile, "source")
    raced["target"].write_bytes(b"competing writer")
    assert create_profile(raced) == "REFUSED_CONCURRENCY_UNPROVEN"
    assert raced["target"].read_bytes() == b"competing writer"
    assert not raced["manifest"].exists()


def test_profiles_only_refuses_unresolved_local_models(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setattr(
        sys, "argv", ["configure-pi.py", "--preset", "local", "--profiles-only"]
    )
    assert configure_pi.main() == 1
    assert not (tmp_path / "agent").exists()


def test_previous_settings_subprocess_exits_nonzero_for_non_object(tmp_path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("[]", encoding="utf-8")
    home = tmp_path / "home"
    pi_dir = home / ".pi" / "agent"
    pi_dir.mkdir(parents=True)
    (pi_dir / "settings.json").write_text("[]", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO_ROOT, "scripts", "configure-pi.py"),
            "--preset",
            "pro-plus",
            "--no-local-fallbacks",
        ],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(home), "PI_CODING_AGENT_DIR": str(pi_dir)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Malformed settings.json" in result.stderr
    assert (pi_dir / "settings.json").read_text(encoding="utf-8") == "[]"


@pytest.mark.parametrize("suffix", [":cloud", "-cloud"])
def test_ollama_cloud_context_uses_native_window_and_original_model_id(suffix):
    model_id = f"provider/model{suffix}"
    configure_pi._model_details_cache.clear()
    with (
        patch.dict(os.environ, {"OLLAMA_CONTEXT_LENGTH": "32768"}),
        patch.object(
            configure_pi, "_native_context", return_value=65536
        ) as native_context,
    ):
        assert configure_pi._model_context_window(model_id, local=False) == 65536
    native_context.assert_called_once_with(model_id)


def test_non_cloud_model_context_remains_capped():
    with (
        patch.dict(os.environ, {"OLLAMA_CONTEXT_LENGTH": "32768"}),
        patch.object(configure_pi, "_native_context") as native_context,
    ):
        assert (
            configure_pi._model_context_window("provider/model", local=False) == 32768
        )
    native_context.assert_not_called()


def test_local_model_context_remains_capped_by_native_window():
    with (
        patch.dict(os.environ, {"OLLAMA_CONTEXT_LENGTH": "32768"}),
        patch.object(
            configure_pi, "_native_context", return_value=65536
        ) as native_context,
    ):
        assert configure_pi._model_context_window("provider/model", local=True) == 32768
    native_context.assert_called_once_with("provider/model", "ollama")


@pytest.mark.parametrize(
    ("native", "backend", "expected"),
    [
        (131072, "omlx", 131072),
        (None, "omlx", 32768),
        (200000, "ollama", 131072),
    ],
)
def test_effective_context_window(native, backend, expected):
    assert configure_pi.resolve_effective_context_window(native, backend) == expected


@pytest.mark.parametrize(("window", "expected"), [(32768, 24576), (131072, 32768)])
def test_model_entry_max_tokens_reserve(window, expected, monkeypatch):
    monkeypatch.setattr(configure_pi, "_model_context_window", lambda *_args: window)
    assert configure_pi.model_entry("model")["maxTokens"] == expected


def codified_settings():
    """Fresh-install settings matching the codified defaults in configure-pi.py."""
    return {
        "theme": "light/dark",
        "tuiMode": "fullscreen",
        "markdown": {"mermaid": "final"},
        "followUpMode": "all",
        "steeringMode": "all",
        "terminal": {"showTerminalProgress": True},
        "showHardwareCursor": True,
        "lastChangelogVersion": "0.85.1",  # must be popped by the helper
    }


class ApplyPreservedPreferencesTest(unittest.TestCase):
    def test_prev_scalar_wins_over_codified_default(self):
        settings = codified_settings()
        prev = {"tuiMode": "split", "followUpMode": "context"}
        configure_pi.apply_preserved_preferences(settings, prev)
        self.assertEqual(settings["tuiMode"], "split")
        self.assertEqual(settings["followUpMode"], "context")
        self.assertEqual(settings["steeringMode"], "all")
        self.assertEqual(settings["showHardwareCursor"], True)

    def test_fresh_install_yields_codified_defaults(self):
        settings = codified_settings()
        configure_pi.apply_preserved_preferences(settings, {})
        self.assertEqual(settings["tuiMode"], "fullscreen")
        self.assertEqual(settings["markdown"], {"mermaid": "final"})
        self.assertEqual(settings["terminal"], {"showTerminalProgress": True})
        self.assertEqual(settings["followUpMode"], "all")
        self.assertEqual(settings["steeringMode"], "all")
        self.assertEqual(settings["showHardwareCursor"], True)
        self.assertNotIn("lastChangelogVersion", settings)

    def test_markdown_shallow_merge_keeps_prev_subkeys(self):
        settings = codified_settings()
        prev = {"markdown": {"mermaid": "stream", "codeBlocks": "copy"}}
        configure_pi.apply_preserved_preferences(settings, prev)
        self.assertEqual(
            settings["markdown"], {"mermaid": "stream", "codeBlocks": "copy"}
        )
        self.assertEqual(settings["markdown"]["mermaid"], "stream")

    def test_last_changelog_version_carried_over_when_present(self):
        settings = codified_settings()
        configure_pi.apply_preserved_preferences(settings, {})
        self.assertNotIn("lastChangelogVersion", settings)
        settings = codified_settings()
        prev = {"lastChangelogVersion": "0.86.0"}
        configure_pi.apply_preserved_preferences(settings, prev)
        self.assertEqual(settings["lastChangelogVersion"], "0.86.0")


class BuildLocalProviderTest(unittest.TestCase):
    def _build_omlx_provider(self):
        return configure_pi.build_local_provider("omlx", ["chat"])

    def _hermetic(self):
        """Patch registry endpoint + model metadata so no network is touched.

        build_local_provider passes model ids through model_entry, whose
        metadata lookup would query a live oMLX instance for an unstubbed
        engine; a stubbed model_entry keeps the test hermetic and focused on
        the apiKey branches under review.
        """
        patched_engine = {
            "base_url": lambda: "http://omlx:8000",
            "health_check": lambda: (True, "HTTP 200"),
        }
        return (
            patch.dict(
                local_engines.LOCAL_ENGINES["omlx"],
                patched_engine,
            ),
            patch.object(
                configure_pi,
                "model_entry",
                lambda model_id, local=True, provider=None: {"id": model_id},
            ),
        )

    def test_omlx_provider_uses_api_key_env_reference_when_configured(self):
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.dict(
                    os.environ,
                    {"DOTFILES_RUN_OMLX_SETUP": "1", "OMLX_API_KEY": "secret"},
                    clear=False,
                )
            )
            for ctx in self._hermetic():
                stack.enter_context(ctx)
            provider = self._build_omlx_provider()

        self.assertEqual(provider["apiKey"], "$OMLX_API_KEY")

    def test_omlx_provider_uses_literal_placeholder_without_api_key(self):
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.dict(
                    os.environ,
                    {"DOTFILES_RUN_OMLX_SETUP": "1", "OMLX_API_KEY": ""},
                    clear=False,
                )
            )
            for ctx in self._hermetic():
                stack.enter_context(ctx)
            provider = self._build_omlx_provider()

        self.assertEqual(provider["apiKey"], "omlx")

    def test_required_key_engine_without_key_is_omitted(self):
        # An engine that requires its API key (api_key_optional unset) and has
        # none configured keeps the pre-existing behaviour: no apiKey field,
        # so pi treats the provider as unauthenticated.
        with (
            patch.dict(
                os.environ,
                {"DOTFILES_RUN_OMLX_SETUP": "1"},
                clear=False,
            ),
            patch.dict(
                local_engines.LOCAL_ENGINES["omlx"],
                {
                    "base_url": lambda: "http://omlx:8000",
                    "health_check": lambda: (True, "HTTP 200"),
                    "api_key_optional": False,
                },
            ),
            patch.object(
                configure_pi,
                "model_entry",
                lambda model_id, local=True, provider=None: {"id": model_id},
            ),
        ):
            os.environ.pop("OMLX_API_KEY", None)
            provider = self._build_omlx_provider()

        self.assertNotIn("apiKey", provider)


def test_pi_litellm_gate_repoints_openai_and_local_providers():
    providers = {
        name: {"baseUrl": f"http://{name}", "apiKey": "old", "models": ["kept"]}
        for name in ("openai", "ollama", "omlx")
    }
    with tempfile.TemporaryDirectory() as home:
        key_file = Path(home) / ".local/share/litellm/clients/pi.key"
        key_file.parent.mkdir(parents=True)
        key_file.write_text("dummy-key", encoding="utf-8")
        key_file.parent.chmod(0o700)
        key_file.chmod(0o600)
        with patch.dict(
            os.environ,
            {
                "DOTFILES_RUN_LITELLM_SETUP": "1",
                "DOTFILES_USE_LITELLM_PROXY": "1",
                "HOME": home,
            },
            clear=False,
        ):
            configure_pi.apply_litellm_provider_overrides(providers, "4002")
        for provider in providers.values():
            assert provider["baseUrl"] == "http://127.0.0.1:4002/v1"
            assert provider["apiKey"] == f"!cat {shlex.quote(str(key_file))}"
            assert provider["models"] == ["kept"]


def test_pi_cloud_aliases_keep_nested_wire_ids_and_fail_closed(tmp_path, monkeypatch):
    key_file = tmp_path / ".local/share/litellm/clients/pi.key"
    key_file.parent.mkdir(parents=True)
    key_file.parent.chmod(0o700)
    key_file.write_text("never-log-this", encoding="utf-8")
    key_file.chmod(0o600)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        configure_pi,
        "get_catalogue",
        lambda url, key: {
            "data": [
                {"id": "google/models/gemini-3.8-flash"},
                {"id": "openrouter/nvidia/nemotron-3.5-lightning:free"},
            ]
        },
    )
    routes = configure_pi.litellm_cloud_aliases()
    assert (
        routes["google"]["gemini-3.8-flash"] == "litellm/google/models/gemini-3.8-flash"
    )
    assert (
        routes["openrouter"]["nvidia/nemotron-3.5-lightning:free"]
        == "litellm/openrouter/nvidia/nemotron-3.5-lightning:free"
    )
    monkeypatch.setattr(configure_pi, "get_catalogue", lambda *_args: {"data": "bad"})
    assert configure_pi.litellm_cloud_aliases() == {
        "google": {},
        "openrouter": {},
        "opencode": {},
    }


def _run_pi_main(home, catalogue, monkeypatch, *, canary=True, key=True):
    agent_dir = Path(home) / "pi-agent"
    agent_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent_dir))
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1" if canary else "0")
    monkeypatch.setenv("DOTFILES_RUN_PI_SETUP", "0")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1" if canary else "0")
    monkeypatch.setenv("GEMINI_API_KEY", "direct-google-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "direct-openrouter-key")
    key_file = Path(home) / ".local/share/litellm/clients/pi.key"
    if key:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.parent.chmod(0o700)
        key_file.write_text("fixture-secret", encoding="utf-8")
        key_file.chmod(0o600)
    monkeypatch.setattr(configure_pi, "get_catalogue", lambda *_args: catalogue)
    monkeypatch.setattr(configure_pi, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(
        configure_pi, "_ensure_packages", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        sys, "argv", ["configure-pi.py", "--preset", "free", "--no-local-fallbacks"]
    )
    configure_pi.main()
    return agent_dir


def test_pi_main_emits_catalogued_nested_cloud_routes(tmp_path, monkeypatch):
    """Verify catalogued cloud routes reach Pi settings through LiteLLM."""
    catalogue = {
        "data": [
            {"id": "google/models/gemini-3.8-flash"},
            {"id": "google/models/gemini-3.5-flash-lite"},
            {"id": "opencode/nemotron-3.5-lightning-free"},
        ]
    }
    out = _run_pi_main(tmp_path, catalogue, monkeypatch)
    providers = json.loads((out / "models.json").read_text())["providers"]
    settings = json.loads((out / "settings.json").read_text())
    auth = json.loads((out / "auth.json").read_text())
    proxy = providers["litellm"]
    assert proxy["baseUrl"] == "http://127.0.0.1:4000/v1"
    assert proxy["apiKey"] == f"!cat {tmp_path / '.local/share/litellm/clients/pi.key'}"
    entries = {entry["id"]: entry for entry in proxy["models"]}
    assert "google/models/gemini-3.8-flash" in entries
    assert entries["google/models/gemini-3.8-flash"]["compat"] == {
        "supportsStore": False
    }
    assert "compat" not in entries["opencode/nemotron-3.5-lightning-free"]
    assert "google" not in providers and "openrouter" not in providers
    assert "google" not in auth and "openrouter" not in auth
    overrides = settings["subagents"]["agentOverrides"]
    assert (
        overrides["researcher"]["model"]
        == "litellm/google/models/gemini-3.5-flash-lite"
    )
    assert overrides["scout"]["model"] == "litellm/opencode/nemotron-3.5-lightning-free"
    assert settings["defaultProvider"] + "/" + settings["defaultModel"] in {
        f"{provider}/{entry['id']}"
        for provider, config in providers.items()
        for entry in config.get("models", [])
    }
    assert "litellm/google/models/gemini-3.8-flash" in settings["enabledModels"]
    assert "litellm/opencode/nemotron-3.5-lightning-free" in settings["enabledModels"]
    assert "fixture-secret" not in json.dumps([providers, settings, auth])


def test_pi_main_lists_every_advertised_gateway_identity(tmp_path, monkeypatch):
    unselected = "openrouter/poolside/laguna-s-2.1:free"
    catalogue = {
        "data": [
            {"id": "google/models/gemini-3.8-flash"},
            {"id": "openrouter/nvidia/nemotron-3.5-lightning:free"},
            {"id": unselected},
        ]
    }
    out = _run_pi_main(tmp_path, catalogue, monkeypatch)
    entries = {
        entry["id"]: entry
        for entry in json.loads((out / "models.json").read_text())["providers"][
            "litellm"
        ]["models"]
    }
    assert unselected in entries


def test_pi_valid_partial_settings_preserve_custom_overrides_twice(
    tmp_path, monkeypatch
):
    agent_dir = tmp_path / "pi-agent"
    agent_dir.mkdir()
    custom = {"model": "custom/model"}
    (agent_dir / "settings.json").write_text(
        json.dumps({"subagents": {"agentOverrides": {"my-agent": custom}}})
    )
    catalogue = {"data": [{"id": "google/models/gemini-3.8-flash"}]}
    _run_pi_main(tmp_path, catalogue, monkeypatch)
    first = {
        name: (agent_dir / name).read_bytes()
        for name in ("settings.json", "auth.json", "models.json")
    }
    _run_pi_main(tmp_path, catalogue, monkeypatch)
    assert {name: (agent_dir / name).read_bytes() for name in first} == first
    assert (
        json.loads(first["settings.json"])["subagents"]["agentOverrides"]["my-agent"]
        == custom
    )


def test_pi_main_partial_catalogue_and_gate_off_are_fail_closed_or_direct(
    tmp_path, monkeypatch
):
    canary_home = tmp_path / "canary"
    canary_home.mkdir()
    out = _run_pi_main(
        canary_home, {"data": [{"id": "google/models/gemini-3.8-flash"}]}, monkeypatch
    )
    providers = json.loads((out / "models.json").read_text())["providers"]
    auth = json.loads((out / "auth.json").read_text())
    settings = json.loads((out / "settings.json").read_text())
    assert "google" not in providers and "openrouter" not in providers
    assert "google" not in auth and "openrouter" not in auth
    assert "scout" not in settings["subagents"]["agentOverrides"]
    assert settings["defaultProvider"] + "/" + settings["defaultModel"] in {
        f"{provider}/{entry['id']}"
        for provider, config in providers.items()
        for entry in config.get("models", [])
    }
    direct_home = tmp_path / "direct"
    direct_home.mkdir()
    direct = _run_pi_main(
        direct_home, {"data": []}, monkeypatch, canary=False, key=False
    )
    direct_providers = json.loads((direct / "models.json").read_text())["providers"]
    direct_auth = json.loads((direct / "auth.json").read_text())
    assert "google" in direct_providers and "openrouter" in direct_providers
    assert "google" in direct_auth and "openrouter" in direct_auth


def test_pi_anthropic_selection_resolves_to_catalogued_meridian_alias(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        configure_pi.tier_registry,
        "materialize_role_models",
        lambda *_args, **_kwargs: {"orchestrator": "anthropic/claude-sonnet-5-5"},
    )
    out = _run_pi_main(
        tmp_path,
        {"data": [{"id": "meridian/claude-sonnet-5-5"}]},
        monkeypatch,
    )
    providers = json.loads((out / "models.json").read_text())["providers"]
    settings = json.loads((out / "settings.json").read_text())
    assert settings["defaultProvider"] == "litellm"
    assert settings["defaultModel"] == "meridian/claude-sonnet-5-5"
    assert "meridian/claude-sonnet-5-5" in {
        entry["id"] for entry in providers["litellm"]["models"]
    }


def test_pi_missing_default_model_raises_without_phantom_inventory(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        configure_pi.tier_registry,
        "materialize_role_models",
        lambda *_args, **_kwargs: {"orchestrator": None},
    )
    with (
        patch("sys.stderr"),
        unittest.TestCase().assertRaisesRegex(
            RuntimeError, "default model has no advertised"
        ),
    ):
        _run_pi_main(tmp_path, {"data": []}, monkeypatch)
    output = tmp_path / "pi-agent" / "models.json"
    assert not output.exists()


def test_pi_main_rejects_missing_or_insecure_client_key_before_output(
    tmp_path, monkeypatch
):
    for case in ("missing", "directory-mode", "symlink"):
        home = tmp_path / case
        home.mkdir()
        key_file = home / ".local/share/litellm/clients/pi.key"
        if case != "missing":
            key_file.parent.mkdir(parents=True)
            if case == "symlink":
                outside = home / "outside.key"
                outside.write_text("fixture-secret", encoding="utf-8")
                outside.chmod(0o600)
                key_file.symlink_to(outside)
                key_file.parent.chmod(0o700)
            else:
                key_file.write_text("fixture-secret", encoding="utf-8")
                key_file.chmod(0o600)
                key_file.parent.chmod(0o755)
        with patch("sys.stderr"), unittest.TestCase().assertRaises(RuntimeError):
            _run_pi_main(home, {"data": []}, monkeypatch, key=False)
        output = home / "pi-agent"
        assert not (output / "models.json").exists()
        assert not (output / "settings.json").exists()
        assert not (output / "auth.json").exists()


def test_pi_manifest_creation_failure_preserves_private_orphan_and_refuses_adoption(
    tmp_path, monkeypatch
):
    agent_dir = tmp_path / "agent"
    plan = pi_profiles.plan_profile(agent_dir, "free", {"subagents": {}}, "source")
    original_mkdir = Path.mkdir

    def fail_manifest_directory(path, *args, **kwargs):
        if path == plan["manifest"].parent:
            raise OSError("deterministic manifest directory failure")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_manifest_directory)
    assert pi_profiles.create_profile(plan) == "REFUSED_CONCURRENCY_UNPROVEN"
    orphan = plan["target"]
    assert orphan.read_bytes() == plan["content"]
    assert orphan.stat().st_mode & 0o777 == 0o600
    manifest_dir = plan["manifest"].parent
    monkeypatch.setattr(Path, "mkdir", original_mkdir)
    manifest_dir.mkdir(parents=True)
    manifest_dir.chmod(0o700)
    assert pi_profiles.plan_profile(agent_dir, "free", {"subagents": {}}, "source") == {
        "disposition": "REFUSED_OWNERSHIP",
        "reason": "unowned artifact",
    }


@pytest.mark.parametrize("umask", [0o000, 0o077])
def test_pi_profile_directory_created_private_independent_of_umask(
    tmp_path, monkeypatch, umask
):
    agent_dir = tmp_path / "new-agent"
    plan = pi_profiles.plan_profile(agent_dir, "free", {"subagents": {}}, "source")
    previous = os.umask(umask)
    try:
        assert pi_profiles.create_profile(plan) == "GENERATE_ONLY"
    finally:
        os.umask(previous)
    assert (agent_dir / "profiles" / "pi-subagents").stat().st_mode & 0o777 == 0o700


def test_pi_profile_refuses_preexisting_unsafe_directory_permissions(tmp_path):
    agent_dir = tmp_path / "agent"
    profile_dir = agent_dir / "profiles" / "pi-subagents"
    profile_dir.mkdir(parents=True)
    profile_dir.chmod(0o755)
    plan = pi_profiles.plan_profile(agent_dir, "free", {"subagents": {}}, "source")
    assert pi_profiles.create_profile(plan) == "REFUSED_OWNERSHIP"
    assert not plan["target"].exists()


def test_pi_malformed_settings_json_refuses_write_and_preserves_user_overrides(
    tmp_path, monkeypatch
):
    """Malformed settings.json must not cause silent loss of agentOverrides.

    Previously the broad ``except Exception: pass`` at configure-pi.py:1150
    silently swallowed ``json.JSONDecodeError``, leaving ``previous`` as ``{}``
    and discarding every user-added agent override on the next regeneration.
    The function must now detect malformed JSON, refuse the write, and leave
    the existing settings.json untouched so the user's overrides survive.
    """
    agent_dir = tmp_path / "pi-agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    settings = agent_dir / "settings.json"
    # Deliberately malformed: truncated JSON, trailing garbage.
    settings.write_text(
        '{"subagents": {"agentOverrides": {"custom-agent": {"model": "ollama/foo"}},'
        ' "theme": "light",}\n',
        encoding="utf-8",
    )

    key_file = tmp_path / ".local/share/litellm/clients/pi.key"
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.parent.chmod(0o700)
    key_file.write_text("fixture-secret", encoding="utf-8")
    key_file.chmod(0o600)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent_dir))
    monkeypatch.setenv("DOTFILES_RUN_PI_SETUP", "0")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setattr(
        configure_pi,
        "get_catalogue",
        lambda *_args: {
            "data": [
                {"id": "google/models/gemini-3.8-flash"},
                {"id": "google/models/gemini-3.5-flash-lite"},
                {"id": "opencode/nemotron-3.5-lightning-free"},
            ]
        },
    )
    monkeypatch.setattr(configure_pi, "list_local_ollama_models", lambda: [])
    monkeypatch.setattr(configure_pi, "_ensure_packages", lambda *_a, **_k: None)
    monkeypatch.setattr(sys, "argv", ["configure-pi.py", "--preset", "free"])
    configure_pi.main()

    # The malformed file must be left untouched — no overwrite with a
    # clean (override-stripped) version.
    assert settings.read_text(encoding="utf-8").startswith(
        '{"subagents": {"agentOverrides": {"custom-agent": {"model": "ollama/foo"}}'
    )
    # And the custom override must still be readable (proving it was not lost).
    raw = settings.read_text(encoding="utf-8")
    assert '"custom-agent"' in raw
    assert '"ollama/foo"' in raw


if __name__ == "__main__":
    unittest.main()
