import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "onboard-agentic-review.py"
SPEC = importlib.util.spec_from_file_location("onboard_agentic_review", SCRIPT)
ONBOARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ONBOARD)


def test_onboarding_uses_the_requested_sha_for_copilot_default_and_fallback():
    trusted_sha = "0123456789abcdef0123456789abcdef01234567"
    workflow = ONBOARD.build_copilot_workflow(trusted_sha)

    assert workflow.count(trusted_sha) == 2
    assert f"default: {trusted_sha}" in workflow
    assert f"inputs.trusted_ref || '{trusted_sha}'" in workflow


def test_onboarding_rejects_mismatched_copilot_fallback(monkeypatch, tmp_path):
    default_sha = "a" * 40
    other_sha = "b" * 40
    source = tmp_path / "copilot-setup-steps.yml"
    source.write_text(
        f"default: {default_sha}\n"
        f"TRUSTED_REF: ${{{{ inputs.trusted_ref || '{other_sha}' }}}}\n"
        f"# note {default_sha}\n"
    )
    monkeypatch.setattr(ONBOARD, "COPILOT_SOURCE", source)
    with pytest.raises(ValueError, match="pinned trusted_ref default"):
        ONBOARD.build_copilot_workflow("c" * 40)
