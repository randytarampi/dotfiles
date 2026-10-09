import contextlib
import importlib.util
import json
import os
import sys
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import local_engines

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(TESTS_DIR, "..", "..", ".."))

PI_SPEC = importlib.util.spec_from_file_location(
    "configure_pi",
    os.path.join(REPO_ROOT, "scripts", "configure-pi.py"),
)
assert PI_SPEC is not None and PI_SPEC.loader is not None
configure_pi = importlib.util.module_from_spec(PI_SPEC)
PI_SPEC.loader.exec_module(configure_pi)


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
                {"id": "openrouter/inclusionai/ling-3.0-flash-sante"},
            ]
        },
    )
    routes = configure_pi.litellm_cloud_aliases()
    assert (
        routes["google"]["gemini-3.8-flash"] == "litellm/google/models/gemini-3.8-flash"
    )
    assert (
        routes["openrouter"]["inclusionai/ling-3.0-flash-sante"]
        == "litellm/openrouter/inclusionai/ling-3.0-flash-sante"
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
    catalogue = {
        "data": [
            {"id": "google/models/gemini-3.8-flash"},
            {"id": "google/models/gemini-3.5-flash-lite"},
            {"id": "openrouter/inclusionai/ling-3.0-flash-sante"},
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
    assert "compat" not in entries["openrouter/inclusionai/ling-3.0-flash-sante"]
    assert "google" not in providers and "openrouter" not in providers
    assert "google" not in auth and "openrouter" not in auth
    overrides = settings["subagents"]["agentOverrides"]
    assert (
        overrides["researcher"]["model"]
        == "litellm/google/models/gemini-3.5-flash-lite"
    )
    assert (
        overrides["scout"]["model"]
        == "litellm/openrouter/inclusionai/ling-3.0-flash-sante"
    )
    assert settings["defaultProvider"] + "/" + settings["defaultModel"] in {
        f"{provider}/{entry['id']}"
        for provider, config in providers.items()
        for entry in config.get("models", [])
    }
    assert "litellm/google/models/gemini-3.8-flash" in settings["enabledModels"]
    assert (
        "litellm/openrouter/inclusionai/ling-3.0-flash-sante"
        in settings["enabledModels"]
    )
    assert "fixture-secret" not in json.dumps([providers, settings, auth])


def test_pi_main_lists_every_advertised_gateway_identity(tmp_path, monkeypatch):
    unselected = "openrouter/poolside/laguna-s-2.1:free"
    catalogue = {
        "data": [
            {"id": "google/models/gemini-3.8-flash"},
            {"id": "openrouter/inclusionai/ling-3.0-flash-sante"},
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


if __name__ == "__main__":
    unittest.main()
