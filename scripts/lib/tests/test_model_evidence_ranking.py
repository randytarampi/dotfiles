"""Synthetic source-neutral quality ranking tests; no live network requests."""

import json
from datetime import datetime, timedelta, timezone

from model_evidence import load_catalogues, load_private_mappings, rank_qualified


def candidate(name, caps=("completion", "thinking", "tools")):
    return {"name": name, "provider": "omlx", "capabilities": list(caps)}


def fixture():
    source = {
        "source": "whatmodel",
        "metric": "overall",
        "scale": "whatmodel-g-v1",
        "models": {
            "one": {"creator_id": "maker", "score": 18.0},
            "two": {"creator_id": "maker", "score": 73.0},
        },
    }
    refs = {}
    for name, model_id in (("slow", "one"), ("fast", "two")):
        refs["omlx/" + name] = {
            "whatmodel": {
                "model_id": model_id,
                "creator_id": "maker",
                "scale": "whatmodel-g-v1",
            }
        }
    return source, refs


def test_only_comparable_slots_swap():
    source, refs = fixture()
    unknown = candidate("unknown")
    entries = [candidate("slow"), unknown, candidate("fast")]
    count = rank_qualified({"code-gen": entries}, catalogues=[source], mappings=refs)
    assert count == 1
    assert [item["name"] for item in entries] == ["fast", "unknown", "slow"]
    assert entries[1] is unknown


def test_invalid_identity_and_unknown_capabilities_do_not_promote():
    source, refs = fixture()
    entries = [candidate("slow"), candidate("fast", caps=())]
    assert (
        rank_qualified({"code-gen": entries}, catalogues=[source], mappings=refs) == 0
    )
    entries[1]["capabilities"] = ["completion", "tools"]
    refs["omlx/fast"]["whatmodel"]["scale"] = "not-this-score"
    assert (
        rank_qualified({"code-gen": entries}, catalogues=[source], mappings=refs) == 0
    )


def test_aa_intelligence_never_becomes_coding_score(tmp_path):
    aa = {
        "manifest": {"index_version": "5"},
        "models": [
            {"id": "one", "creator_id": "maker", "intelligence": "20"},
            {"id": "two", "creator_id": "maker", "intelligence": "80"},
        ],
    }
    sources = load_catalogues(tmp_path, aa_evidence=aa)
    assert sources[0]["metric"] == "intelligence"
    refs = {}
    for route, ident in (("slow", "one"), ("fast", "two")):
        refs["omlx/" + route] = {
            "artificial-analysis": {
                "model_id": ident,
                "creator_id": "maker",
                "scale": "aa-intelligence-5",
            }
        }
    code = [candidate("slow"), candidate("fast")]
    reasoning = [candidate("slow"), candidate("fast")]
    n = rank_qualified(
        {"code-gen": code, "reasoning": reasoning},
        catalogues=sources,
        mappings=refs,
    )
    assert n == 1
    assert [m["name"] for m in code] == ["slow", "fast"]
    assert [m["name"] for m in reasoning] == ["fast", "slow"]


def test_quiet_remains_memory_first():
    source, refs = fixture()
    entries = [candidate("slow"), candidate("fast")]
    n = rank_qualified(
        {"code-gen": entries},
        catalogues=[source],
        mappings=refs,
        profile="quiet",
    )
    assert n == 0


def test_private_map_rejects_glob_and_symlink(tmp_path):
    _, refs = fixture()
    p = tmp_path / "map.json"
    p.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mappings": {
                    "omlx/slow": refs["omlx/slow"],
                    "omlx/*": refs["omlx/fast"],
                },
            }
        ),
        encoding="utf-8",
    )
    assert list(load_private_mappings(p)) == ["omlx/slow"]
    p.unlink()
    p.symlink_to(tmp_path / "missing")
    assert load_private_mappings(p) == {}


def test_stale_and_self_reported_rows_are_excluded(tmp_path):
    now = datetime.now(timezone.utc)
    payload = {
        "schema_version": 1,
        "source": "local-eval",
        "metric": "coding",
        "scale": "local-coding-v1",
        "observed_at": now.isoformat(),
        "models": [
            {
                "id": "lab",
                "creator_id": "maker",
                "score": 99,
                "n": 5,
                "evidence": "lab",
            },
            {
                "id": "independent",
                "creator_id": "maker",
                "score": 77,
                "n": 6,
                "evidence": "independent",
            },
        ],
    }
    p = tmp_path / "local-eval.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    source = load_catalogues(tmp_path, aa_evidence={}, now=now)
    assert list(source[0]["models"]) == ["independent"]
    payload["observed_at"] = (now - timedelta(days=31)).isoformat()
    p.write_text(json.dumps(payload), encoding="utf-8")
    assert load_catalogues(tmp_path, aa_evidence={}, now=now) == []


def test_actual_tier_consumer_calls_opt_in_ranker(monkeypatch):
    from unittest.mock import patch

    import tier_resolve

    fleet = [
        {"name": "coder-a:7b", "provider": "ollama", "param_count": 7},
        {"name": "coder-b:8b", "provider": "ollama", "param_count": 8},
    ]

    def details(name, provider="ollama"):
        return {
            "param_count": 8 if "coder-b" in name else 7,
            "capabilities": ["completion", "thinking", "tools"],
            "is_moe": False,
        }

    monkeypatch.setenv("DOTFILES_USE_MODEL_EVIDENCE_RANKING", "1")
    with patch.object(tier_resolve, "get_model_details", side_effect=details):
        baseline = tier_resolve.resolve_roles_from_list(fleet)

        def reorder(classified, *, profile=None, audit=None):
            assert profile is None
            classified["code-gen"].reverse()
            return 1

        with patch("model_evidence.rank_qualified", side_effect=reorder):
            changed = tier_resolve.resolve_roles_from_list(fleet, evidence_mode="apply")
    assert changed["code-gen"] != baseline["code-gen"]


def test_process_environment_cannot_rerank_shared_voice_or_local_winner(monkeypatch):
    from unittest.mock import patch

    import tier_resolve

    monkeypatch.setenv("DOTFILES_USE_MODEL_EVIDENCE_RANKING", "1")
    fleet = [
        {"name": "coder-a:7b", "provider": "ollama"},
        {"name": "coder-b:8b", "provider": "ollama"},
    ]
    details = lambda name: {
        "capabilities": ["completion", "thinking", "tools"],
        "param_count": 7 if "coder-a" in name else 8,
        "is_moe": False,
    }
    with patch.object(tier_resolve, "get_model_details", side_effect=details):
        with patch(
            "model_evidence.rank_qualified",
            side_effect=AssertionError("unscoped resolver must not rank"),
        ):
            output = tier_resolve.resolve_roles_from_list(fleet)
    assert output["code-gen"] == "ollama/coder-b:8b"


def test_shadow_audit_explains_stale_scale_and_keeps_original_order():
    source, mappings = fixture()
    mappings["omlx/slow"]["whatmodel"]["scale"] = "retired-scale"
    models = [candidate("slow"), candidate("fast")]
    audit = {}
    assert (
        rank_qualified(
            {"code-gen": models},
            catalogues=[source],
            mappings=mappings,
            audit=audit,
        )
        == 0
    )
    assert audit["sources"] == 1
    assert audit["scale_mismatches"] >= 1
    assert audit["comparable"] == 1
    assert models[0]["name"] == "slow"
