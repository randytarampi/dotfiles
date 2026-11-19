"""Tests for host-acceptance receipt provenance.

A receipt's schema proves shape, not that anything ran. `--record` merges
stage outcomes supplied by an external driver, so an all-pass receipt can be
hand-authored — which means a schema-valid `pass` must never be cited as host
qualification on its own.

`--require-driver` is the gate: a stage claiming `pass` needs a reference to
the run that produced it.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "collect-bootstrap-acceptance.py"
_SPEC = spec_from_file_location("collect_bootstrap_acceptance", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
COLLECT = module_from_spec(_SPEC)
_SPEC.loader.exec_module(COLLECT)

RUN_URL = "https://github.com/randytarampi/dotfiles/actions/runs/123456789"


def _receipt():
    return COLLECT.inspect("unknown", ".", None, [])


def _all_pass(receipt):
    receipt["stages"] = {
        name: {
            "outcome": "pass",
            "exit_code": 0,
            "source": "driver",
            "elapsed_seconds": 1.0,
        }
        for name in COLLECT.STAGES
    }
    return receipt


def test_schema_accepts_a_hand_authored_all_pass_receipt():
    """Demonstrates the gap this gate closes."""
    receipt = _all_pass(_receipt())
    COLLECT.validate(receipt)  # shape is fine; that is the point


def test_all_pass_without_provenance_is_rejected():
    receipt = _all_pass(_receipt())
    with pytest.raises(COLLECT.ReceiptError, match="without execution provenance"):
        COLLECT.validate_driver_evidence(receipt)


def test_all_pass_with_run_url_is_accepted():
    receipt = _all_pass(_receipt())
    receipt["provenance"]["run_url"] = RUN_URL
    COLLECT.validate_driver_evidence(receipt)


def test_not_run_receipt_needs_no_provenance():
    receipt = _receipt()
    COLLECT.validate_driver_evidence(receipt)


def test_stale_sha_is_still_rejected_with_provenance():
    receipt = _all_pass(_receipt())
    receipt["source_sha"] = "a" * 40
    receipt["provenance"]["run_url"] = RUN_URL
    with pytest.raises(COLLECT.ReceiptError, match="stale receipt"):
        COLLECT.validate(receipt, "b" * 40)
