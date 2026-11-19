"""Tests for the read-only release drift inventory."""

from __future__ import annotations

import re
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "release-drift-inventory.py"
_SPEC = spec_from_file_location("release_drift_inventory", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
DRIFT = module_from_spec(_SPEC)
_SPEC.loader.exec_module(DRIFT)


def test_classify_current():
    assert DRIFT.classify("1.2.3", "1.2.3") == "CURRENT"


def test_classify_behind_and_ahead():
    assert DRIFT.classify("1.2.3", "1.10.0") == "BEHIND"
    assert DRIFT.classify("1.10.0", "1.2.3") == "AHEAD"


def test_classify_unknown_is_not_a_guess():
    assert DRIFT.classify("UNKNOWN", "1.0.0") == "UNKNOWN"
    assert DRIFT.classify("1.0.0", "UNKNOWN") == "UNKNOWN"


def test_prerelease_sorts_below_its_release():
    # 1.2.0-rc1 must not be reported as newer than 1.2.0.
    assert DRIFT.classify("1.2.0-rc1", "1.2.0") == "BEHIND"


def test_pinned_version_reads_the_template_default(tmp_path: Path):
    template = tmp_path / "t.tmpl"
    template.write_text('VERSION="${DOTFILES_LITELLM_VERSION:-1.108.11}"\n')
    pattern = re.compile(r"\$\{DOTFILES_LITELLM_VERSION:-([0-9][0-9A-Za-z.\-]*)\}")
    assert DRIFT.pinned_version(template, pattern) == "1.108.11"


def test_pinned_version_missing_template_is_unknown(tmp_path: Path):
    pattern = re.compile(r"nothing")
    assert DRIFT.pinned_version(tmp_path / "absent", pattern) == "UNKNOWN"


def test_render_includes_status_and_source():
    rows = [
        {
            "package": "litellm",
            "pinned": "1.0.0",
            "latest": "1.1.0",
            "status": "BEHIND",
            "source": "https://example.test",
        }
    ]
    rendered = DRIFT.render(rows)
    assert "| litellm | 1.0.0 | 1.1.0 | BEHIND |" in rendered
