"""Tests for portable review prompt generation and divergence detection."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def generator():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "generate_review_bundles", root / "generate-review-bundles.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_profiles_include_canonical_method_and_all_references(generator):
    outputs = generator.generated()

    assert set(outputs) == {
        generator.PROMPT,
        generator.GITHUB,
        generator.OPENCODE,
        generator.COMPACT,
    }
    assert len(outputs[generator.COMPACT]) <= generator.COMPACT_LIMIT
    assert "Engineering review" in outputs[generator.COMPACT]
    assert "Mentoring and review-method development" not in outputs[generator.COMPACT]
    assert "Claims audit" not in outputs[generator.COMPACT]
    for name in generator.FACETS:
        reference = (generator.REFERENCES / f"{name}.md").read_text().strip()
        for target in (generator.PROMPT, generator.GITHUB, generator.OPENCODE):
            assert reference in outputs[target]


def test_missing_canonical_method_marker_is_rejected(generator, tmp_path, monkeypatch):
    source = tmp_path / "SKILL.md"
    source.write_text("# Not a review method\n")
    monkeypatch.setattr(generator, "SOURCE", source)

    with pytest.raises(ValueError, match="one common method marker"):
        generator.generated()


def test_check_bundles_catches_missing_stale_and_oversize(
    generator, tmp_path, monkeypatch
):
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(generator, "COMPACT_LIMIT", 5)
    output = {generator.COMPACT: "too many characters", "existing.md": "new"}
    (tmp_path / "existing.md").write_text("old")

    errors = generator.check_bundles(output, {"existing.md": "bad"})

    assert any("exceeds" in error for error in errors)
    assert any("out of sync" in error for error in errors)
    assert any("Manifest" in error for error in errors)


def test_write_bundles_keeps_unrelated_manifest_entries(
    generator, tmp_path, monkeypatch
):
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(generator, "MANIFEST", manifest)
    data = {"assets": {"known.md": "old", "unrelated.md": "preserve"}}

    generator.write_bundles({"known.md": "new", "untracked.md": "other"}, data)

    assert (tmp_path / "known.md").read_text() == "new"
    assert (tmp_path / "untracked.md").read_text() == "other"
    stored = json.loads(manifest.read_text())
    assert stored["assets"]["unrelated.md"] == "preserve"
    assert stored["assets"]["known.md"] != "old"
    assert "untracked.md" not in stored["assets"]
    assert generator.check_bundles({"known.md": "new"}, stored["assets"]) == []


def test_check_mode_returns_failure_on_divergence(generator, tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"assets": {"out.md": "incorrect"}}')
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(generator, "MANIFEST", manifest)
    monkeypatch.setattr(generator, "generated", lambda: {"out.md": "expected"})
    monkeypatch.setattr(sys, "argv", ["generator", "--check"])

    assert generator.main() == 1


def test_write_mode_regenerates_content(generator, tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"assets": {"out.md": "old"}}')
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(generator, "MANIFEST", manifest)
    monkeypatch.setattr(generator, "generated", lambda: {"out.md": "expected"})
    monkeypatch.setattr(sys, "argv", ["generator", "--write"])

    assert generator.main() == 0
    assert (tmp_path / "out.md").read_text() == "expected"
    assert json.loads(manifest.read_text())["assets"]["out.md"] != "old"


def test_dry_run_previews_without_writing(generator, tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"assets": {"out.md": "old"}}')
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(generator, "MANIFEST", manifest)
    monkeypatch.setattr(generator, "generated", lambda: {"out.md": "expected"})
    monkeypatch.setattr(sys, "argv", ["generator", "--write", "--dry-run"])

    assert generator.main() == 0
    assert not (tmp_path / "out.md").exists()
    assert json.loads(manifest.read_text())["assets"]["out.md"] == "old"
