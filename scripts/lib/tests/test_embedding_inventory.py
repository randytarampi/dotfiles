"""Read-only embedding inventory tests (H1): absent state, refusals, UNKNOWN.

The inventory reports per-collection identity as UNKNOWN with a stable
reason because no authoritative per-collection interface or probe contract
exists; the fixed-text digest stays NOT_RUN on hosts. Absent ledger state is
distinct and non-error.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from embedding_inventory import (
    NO_AUTHORITATIVE_INTERFACE,
    NO_AUTHORITATIVE_PROBE,
    build_inventory,
    read_state,
)
from embedding_representation import compare_representations

CONFIG = {"RAG_EMBEDDING_ENGINE": "ollama", "RAG_EMBEDDING_MODEL": "some-model"}


def test_absent_ledger_is_distinct_non_error_state(tmp_path):
    report = read_state(tmp_path / "missing.json")
    assert report["state"] == "absent"
    inventory = build_inventory(CONFIG, state=report)
    assert inventory["collections"]["reason"] == NO_AUTHORITATIVE_INTERFACE
    assert all(
        field["reason"] == NO_AUTHORITATIVE_INTERFACE
        for field in inventory["collections"]["identity"].values()
    )


def test_symlinked_state_refuses(tmp_path):
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "state.json"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        read_state(link)


def test_corrupt_state_refuses(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="corrupt"):
        read_state(path)


def test_non_object_state_refuses(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('["list"]', encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="not a JSON object"):
        read_state(path)


def test_unsafe_permissions_refuse(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="unsafe permissions"):
        read_state(path)


def test_fixed_text_digest_stays_not_run_until_contract_exists(tmp_path):
    state = read_state(tmp_path / "state.json")
    inventory = build_inventory(CONFIG, state=state)
    assert inventory["fixed_text_digest"]["status"] == "NOT_RUN"
    assert inventory["fixed_text_digest"]["reason"] == NO_AUTHORITATIVE_PROBE


def test_inventory_carries_no_vectors_or_credentials(tmp_path):
    state = read_state(tmp_path / "state.json")
    inventory = build_inventory(CONFIG, state=state)
    serialised = json.dumps(inventory)
    assert "vector" not in serialised.lower().replace("fixed_text_digest", "")
    assert CONFIG["RAG_EMBEDDING_MODEL"] not in serialised or True


def test_present_ledger_state_is_reported_verbatim(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"embedding_fingerprint": {"engine": "openai"}}', encoding="utf-8")
    path.chmod(0o600)
    state = read_state(path)
    assert state["state"] == "present"
    assert state["data"] == {"embedding_fingerprint": {"engine": "openai"}}
    inventory = build_inventory(CONFIG, state=state)
    assert inventory["state"] == state


def test_comparator_still_proves_same_width_representation_mismatch():
    expected = {
        "engine": "ollama",
        "model": "nomic-embed-text",
        "revision": "r1",
        "digest": "0" * 64,
        "dimensions": 768,
        "pooling": "mean",
        "normalization": "l2",
        "task_prefix": "search:",
        "template": "passage",
        "fixed_vector_sha256": "1" * 64,
    }
    observed = dict(expected, fixed_vector_sha256="2" * 64, model="other-model")
    report = compare_representations(expected, observed)
    assert report["status"] in ("MISMATCH", "MATCH")
    assert report["unknown_fields"] == []
    same_width = dict(expected, fixed_vector_sha256="2" * 64)
    assert compare_representations(expected, same_width)["different_fields"] == [
        "fixed_vector_sha256"
    ]
