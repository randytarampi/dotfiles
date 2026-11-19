"""Sentinel tests for the LiteLLM diagnostic renderer.

`AGENTS.md` records two real incidents (2026-11-02) where credential values
leaked from generated state during troubleshooting. The lesson there was a
discipline one — never print unredacted tool-profile or DB state — and the
reconciliation review's instruction for this workstream was to prove the
boundary before generalising a redaction framework: if no committed path can
actually emit a credential, adding machinery would be more code than it
safeguards.

These tests are that proof. They drive the real `diagnose()` function with a
credential-bearing base URL and a sentinel master key, and assert neither
reaches the summary on any branch.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "litellm-diagnose.py"
_SPEC = spec_from_file_location("litellm_diagnose", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
DIAGNOSE_MOD = module_from_spec(_SPEC)
_SPEC.loader.exec_module(DIAGNOSE_MOD)

SENTINEL = "SENTINEL-do-not-log-7c1e"


@pytest.fixture
def gate_on(monkeypatch, tmp_path):
    monkeypatch.setenv(DIAGNOSE_MOD.GATE_ENV, "1")
    monkeypatch.setattr(DIAGNOSE_MOD, "_service_env", lambda root: {})
    monkeypatch.setattr(DIAGNOSE_MOD, "_loaded", lambda: True)
    return monkeypatch


def test_credential_bearing_base_url_is_never_echoed(gate_on):
    gate_on.setenv("LITELLM_BASE_URL", f"https://user:{SENTINEL}@proxy.example/v1")
    code, summary = DIAGNOSE_MOD.diagnose("unused")
    assert code == 1
    assert SENTINEL not in summary
    assert "PROXY-UNAVAILABLE" in summary


def test_master_key_never_reaches_the_summary(gate_on):
    gate_on.setattr(
        DIAGNOSE_MOD,
        "_request",
        lambda url, key=None, timeout=None: (
            (200, {"ok": True}, "") if "health" in url else (200, {"data": []}, "")
        ),
    )
    code, summary = DIAGNOSE_MOD.diagnose(SENTINEL)
    assert SENTINEL not in summary
    assert code == 0, "the test must reach the healthy authenticated path"


def test_gate_off_summary_is_closed_vocabulary(monkeypatch):
    monkeypatch.setenv(DIAGNOSE_MOD.GATE_ENV, "0")
    monkeypatch.setattr(DIAGNOSE_MOD, "_service_env", lambda root: {})
    code, summary = DIAGNOSE_MOD.diagnose(SENTINEL)
    assert code == 0
    assert "GATE-OFF" in summary
    assert SENTINEL not in summary


def test_summary_has_no_environment_interpolation():
    """Static guard: the renderer must compose literals, not config values."""
    source = _SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("env.items()", "dict(env)", "os.environ.items()"):
        assert forbidden not in source
