import contextlib
import importlib.util
import json
import os
import unittest
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


if __name__ == "__main__":
    unittest.main()
