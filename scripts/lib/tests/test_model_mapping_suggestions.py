"""Adversarial offline onboarding, approval, backup and identity tests."""

import json
import os
import stat

import pytest

from model_mapping_suggestions import (
    MappingSuggestionError,
    approved_patch,
    merge_approved,
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
                "creator_id": "maker", "score": 91.0,
            },
            "inkling-2-small": {
                "creator_id": "maker", "score": 82.0,
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
    assert ornith["candidates"][0]["match"] == "canonical-name"
    assert "quantization" in ornith["caveat"]
    assert mini["candidates"] == []
    assert "score" not in str(report)


def test_acceptance_requires_installed_exact_source_identity_and_preview(tmp_path):
    approved = approved_patch(
        ["omlx/Ornith-1.5-35B-A3B-MLX-4bit=benchlm-coding:ornith-1.5-35b-a3b"],
        fleet(), [source()],
    )
    path = tmp_path / "model-evidence-map.json"
    result = merge_approved(approved, path=path)
    assert result["mappings"]["omlx/Ornith-1.5-35B-A3B-MLX-4bit"]
    assert not path.exists()
    written = merge_approved(approved, path=path, apply=True)
    assert json.loads(path.read_text()) == written
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert merge_approved(approved, path=path, apply=True) == result
    assert (tmp_path / "model-evidence-map.json.bak").exists()


def test_owner_map_conflict_and_symlink_cannot_be_replaced(tmp_path):
    approved = approved_patch(
        ["omlx/Ornith-1.5-35B-A3B-MLX-4bit=benchlm-coding:ornith-1.5-35b-a3b"],
        fleet(), [source()],
    )
    path = tmp_path / "private-map.json"
    merge_approved(approved, path=path, apply=True)
    different = {"omlx/Ornith-1.5-35B-A3B-MLX-4bit": {
        "benchlm-coding": {
            "model_id": "inkling-2-small",
            "creator_id": "maker",
            "scale": source()["scale"],
        }
    }}
    with pytest.raises(MappingSuggestionError, match="conflict"):
        merge_approved(different, path=path, apply=True)
    path.unlink()
    path.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(MappingSuggestionError, match="symlink"):
        merge_approved(approved, path=path, apply=True)


def test_no_automatic_approval_and_unknown_model_refused():
    with pytest.raises(MappingSuggestionError, match="route or source"):
        approved_patch(
            ["omlx/nonexistent=benchlm-coding:ornith-1.5-35b-a3b"],
            fleet(), [source()],
        )
    with pytest.raises(MappingSuggestionError, match="not found"):
        approved_patch(
            ["omlx/Ornith-1.5-35B-A3B-MLX-4bit=benchlm-coding:not-a-model"],
            fleet(), [source()],
        )
