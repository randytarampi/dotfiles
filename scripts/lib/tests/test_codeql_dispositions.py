"""Sentinel tests for the LiteLLM OAuth readiness diagnostic.

Six CodeQL alerts (`py/clear-text-logging-sensitive-data`) point at
diagnostics that name environment variables. CodeQL classifies any
identifier matching `key|token|secret|password|authorization` as a
"password", so `"OPENROUTER_API_KEY is unset"` reads as a credential leak to
the query while disclosing only the variable's name.

Dismissals must not rest on that argument alone. These tests assert the
property that actually matters: no *value* from the environment or the OAuth
cache may reach the rendered rows, on any path.

If a future change makes one of these fail, the corresponding dismissal is
no longer valid and the alert must be treated as real.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import litellm_config

_SCRIPT = Path(__file__).resolve().parents[2] / "verify-config.py"
_SPEC = spec_from_file_location("verify_config", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_VERIFY = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VERIFY)

SENTINEL = "SENTINEL-do-not-log-4f3a9c"


def _environ(**overrides: str) -> dict[str, str]:
    base = {
        "OPENAI_API_KEY": SENTINEL,
        "ANTHROPIC_API_KEY": SENTINEL,
        "GITHUB_COPILOT_API_KEY_FILE": SENTINEL,
        "CHATGPT_AUTH_FILE": SENTINEL,
        "CHATGPT_TOKEN_DIR": SENTINEL,
        "GITHUB_COPILOT_TOKEN_DIR": SENTINEL,
        "DOTFILES_LITELLM_OAUTH_PROVIDERS": "1",
    }
    base.update(overrides)
    return base


def _render(monkeypatch, environ: dict[str, str], expiry: float | None) -> str:
    monkeypatch.setattr(
        litellm_config,
        "oauth_cache_expiry",
        lambda provider, env=None: expiry,
    )
    monkeypatch.setattr(
        litellm_config,
        "chatgpt_verified_openai_models",
        lambda env=None: [],
    )
    rows = _VERIFY.litellm_oauth_readiness(environ)
    return "\n".join(
        f"{provider} {status} {detail}" for provider, status, detail in rows
    )


def test_no_environment_value_in_any_row(monkeypatch):
    rendered = _render(monkeypatch, _environ(), expiry=None)
    assert (
        SENTINEL not in rendered
    ), "a credential value reached the OAuth readiness diagnostic"


def test_no_environment_value_when_cache_present(monkeypatch):
    rendered = _render(monkeypatch, _environ(), expiry=4102444800.0)
    assert SENTINEL not in rendered


def test_no_environment_value_when_oauth_withheld(monkeypatch):
    rendered = _render(
        monkeypatch, _environ(DOTFILES_LITELLM_OAUTH_PROVIDERS="0"), expiry=None
    )
    assert SENTINEL not in rendered


def test_operator_still_sees_variable_names(monkeypatch):
    """The diagnostic's value is naming the variable; redaction must not eat it."""
    rendered = _render(monkeypatch, _environ(), expiry=None)
    assert "OPENAI_API_KEY" in rendered


def test_alert_sink_sites_log_names_only():
    """Guard the specific expressions CodeQL flagged, by shape not by name."""
    verify_source = _SCRIPT.read_text(encoding="utf-8")
    assert "litellm_oauth_readiness()" in verify_source
    # The sink interpolates the tuple returned above; nothing else flows in.
    assert "for provider, status, detail in litellm_oauth_readiness()" in verify_source
