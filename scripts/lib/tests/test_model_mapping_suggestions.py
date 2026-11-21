"""Adversarial offline onboarding, approval, backup and identity tests."""

import json
import os

import pytest

from model_mapping_suggestions import (
    MappingSuggestionError,
    approved_patch,
    preview_merge,
    qualified_local_models,
    suggestions_for,
)


def fleet():
    return qualified_local_models(
        [
            {
                "name": "Ornith-1.5-35B-A3B-MLX-4bit",
                "provider": "omlx",
                "capabilities": ["completion", "thinking", "tools"],
                "quantization": "4bit",
            },
            {
                "name": "inkling-2-mini",
                "provider": "ollama",
                "capabilities": ["completion", "tools"],
            },
            {
                "name": "Audio-1B-ASR",
                "provider": "omlx",
                "model_type": "audio_stt",
                "capabilities": ["audio"],
            },
        ]
    )


def source():
    return {
        "source": "benchlm-coding",
        "metric": "coding",
        "scale": "benchalign-v5-method6-coding",
        "models": {
            "ornith-1.5-35b-a3b": {
                "creator_id": "maker",
                "score": 91.0,
            },
            "inkling-2-small": {
                "creator_id": "maker",
                "score": 82.0,
            },
        },
    }


def test_installed_suggestions_identify_quantized_name_without_silently_mapping():
    qualified = fleet()
    assert len(qualified) == 2
    report = suggestions_for(qualified, [source()])
    ornith = next(row for row in report if "Ornith" in row["route"])
    mini = next(row for row in report if "inkling" in row["route"])
    assert ornith["candidates"][0]["model_id"] == "ornith-1.5-35b-a3b"
    assert ornith["candidates"][0]["match"] == "identical-normalized-tokens"
    assert "quantization" in ornith["caveat"]
    assert "benchlm.ai/data/access" in ornith["candidates"][0]["provenance"]
    assert "UNVERIFIED" in ornith["candidates"][0]["provenance"]
    assert mini["candidates"] == []
    assert "score" not in str(report)


def test_acceptance_previews_without_writing(tmp_path):
    approved = approved_patch(
        ["omlx/Ornith-1.5-35B-A3B-MLX-4bit=benchlm-coding:ornith-1.5-35b-a3b"],
        fleet(),
        [source()],
    )
    path = tmp_path / "model-evidence-map.json"
    result = preview_merge(approved, path=path)
    assert result["mappings"]["omlx/Ornith-1.5-35B-A3B-MLX-4bit"]
    assert not path.exists()
    assert not (tmp_path / "model-evidence-map.json.bak").exists()

    path.write_text(json.dumps(result), encoding="utf-8")
    path.chmod(0o600)
    before = path.read_bytes()
    assert preview_merge(approved, path=path) == result
    assert path.read_bytes() == before
    assert not (tmp_path / "model-evidence-map.json.bak").exists()


def test_owner_map_conflict_symlink_and_bad_permissions_are_refused(tmp_path):
    approved = approved_patch(
        ["omlx/Ornith-1.5-35B-A3B-MLX-4bit=benchlm-coding:ornith-1.5-35b-a3b"],
        fleet(),
        [source()],
    )
    path = tmp_path / "private-map.json"
    path.write_text(json.dumps(preview_merge(approved, path=path)), encoding="utf-8")
    path.chmod(0o600)
    before = path.read_bytes()

    different = {
        "omlx/Ornith-1.5-35B-A3B-MLX-4bit": {
            "benchlm-coding": {
                "model_id": "inkling-2-small",
                "creator_id": "maker",
                "scale": source()["scale"],
            }
        }
    }
    with pytest.raises(MappingSuggestionError, match="conflict"):
        preview_merge(different, path=path)
    assert path.read_bytes() == before

    path.chmod(0o644)
    with pytest.raises(MappingSuggestionError, match="owned private regular file"):
        preview_merge(approved, path=path)

    path.unlink()
    path.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(MappingSuggestionError, match="symlink"):
        preview_merge(approved, path=path)


def test_malformed_existing_map_is_refused_read_only(tmp_path):
    approved = approved_patch(
        ["omlx/Ornith-1.5-35B-A3B-MLX-4bit=benchlm-coding:ornith-1.5-35b-a3b"],
        fleet(),
        [source()],
    )
    path = tmp_path / "map.json"
    path.write_text("{not json", encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(MappingSuggestionError, match="unreadable or malformed"):
        preview_merge(approved, path=path)
    assert path.read_text(encoding="utf-8") == "{not json"


def test_no_automatic_approval_and_unknown_model_refused():
    with pytest.raises(MappingSuggestionError, match="route or source"):
        approved_patch(
            ["omlx/nonexistent=benchlm-coding:ornith-1.5-35b-a3b"],
            fleet(),
            [source()],
        )
    with pytest.raises(MappingSuggestionError, match="not found"):
        approved_patch(
            ["omlx/Ornith-1.5-35B-A3B-MLX-4bit=benchlm-coding:not-a-model"],
            fleet(),
            [source()],
        )


def test_cli_rejects_apply_before_any_discovery():
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "suggest-model-evidence-mappings.py"
    result = subprocess.run(
        [sys.executable, str(script), "--apply"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr


def test_non_aa_source_cannot_borrow_aa_display_labels():
    aa = {"models": [{"id": "same-id", "slug": "unique-aa-name", "name": "Unique AA"}]}
    generic = {
        "source": "generic",
        "metric": "overall",
        "scale": "generic-v1",
        "models": {"same-id": {"creator_id": "maker", "score": 10}},
    }
    local = [
        {
            "route": "omlx/unique-aa-name",
            "capabilities": ["completion", "tools"],
            "quantization": None,
        }
    ]
    report = suggestions_for(local, [generic], aa_snapshot=aa, maximum=1)
    assert report[0]["candidates"] == []


def test_preview_merge_refuses_platforms_without_posix_ownership(monkeypatch, tmp_path):
    monkeypatch.delattr(os, "getuid")
    with pytest.raises(MappingSuggestionError, match="POSIX"):
        preview_merge({}, path=tmp_path / "map.json")
