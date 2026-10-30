"""Regression coverage for optimized dynamic-free tier guards."""

from pathlib import Path


def test_dynamic_free_guards_are_runtime_validations():
    source = (
        Path(__file__).resolve().parents[2] / "configure-opencode-tier.py"
    ).read_text(encoding="utf-8")
    assert "assert dynamic_source_registry is not None" not in source
    assert "assert dynamic_audit is not None" not in source
    assert "if dynamic_source_registry is None:" in source
    assert "if dynamic_audit is None:" in source
    assert 'raise RuntimeError("Dynamic free source registry is unavailable")' in source
    assert '"Dynamic free tier gateway audit is unavailable"' in source
