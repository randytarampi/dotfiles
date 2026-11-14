"""Read-only embedding inventory tests (H1): absent state, refusals, UNKNOWN.

The inventory reports per-collection identity as UNKNOWN with a stable
reason because no authoritative per-collection interface or probe contract
exists; the fixed-text digest stays NOT_RUN on hosts. Absent ledger state is
distinct and non-error.
"""

import json

import pytest

from embedding_inventory import (
    NO_AUTHORITATIVE_INTERFACE,
    NO_AUTHORITATIVE_PROBE,
    build_inventory,
    collect_inventory,
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


def test_inventory_never_discloses_private_ledger_data(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        '{"api_key": "secret-canary", "note": "private-canary", '
        '"vectors": ["vector-canary"]}',
        encoding="utf-8",
    )
    path.chmod(0o600)
    state = read_state(path)
    assert state == {"state": "present"}
    inventory = build_inventory(CONFIG, state=state)
    payload = json.dumps(inventory)
    assert "secret-canary" not in payload
    assert "private-canary" not in payload
    assert "vector-canary" not in payload
    assert inventory["webui_config_identity"]["model"] == "some-model"
    assert inventory["collections"]["count"] == "UNKNOWN"


def test_nonregular_ledger_refuses(tmp_path):
    with pytest.raises(ValueError, match="not a regular file"):
        read_state(tmp_path)


def test_oversize_ledger_refuses(tmp_path):
    path = tmp_path / "state.json"
    path.write_bytes(b"{" + b" " * (1024 * 1024) + b"}")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="limit"):
        read_state(path)


def test_nonstring_config_identifiers_refuse():
    with pytest.raises(ValueError, match="identifiers"):
        build_inventory({"RAG_EMBEDDING_MODEL": {"private": "secret-canary"}})


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
    assert report["status"] == "MISMATCH"
    assert report["different_fields"] == ["fixed_vector_sha256", "model"]
    assert report["unknown_fields"] == []
    same_width = dict(expected, fixed_vector_sha256="2" * 64)
    assert compare_representations(expected, same_width)["different_fields"] == [
        "fixed_vector_sha256"
    ]


def test_collector_preserves_route_and_reports_unavailable_listing():
    report = collect_inventory(
        lambda: dict(CONFIG, customized=False),
        lambda: {"state": "present", "index_empty": True},
        now="fixed",
    )
    assert report["collected_at"] == "fixed"
    assert report["collections"]["routes"] == [
        {
            "route_id": "embedding",
            "status": "UNAVAILABLE",
            "reason": "no authoritative read-only collection listing on this build",
        }
    ]
    assert report["adoption"]["status"] == "PERMITTED"
    assert report["writes"] == "none"


def test_collector_unknown_receipt_and_override_refuse_adoption():
    unknown = collect_inventory(lambda: dict(CONFIG, customized=False), None)
    assert unknown["adoption"]["status"] == "UNKNOWN"
    override = collect_inventory(
        lambda: dict(CONFIG, customized=True), lambda: {"state": "absent"}
    )
    assert override["config_provenance"]["status"] == "OVERRIDE"
    assert override["adoption"]["status"] == "REFUSED"


def test_cli_gate_off_and_on_use_only_injected_dependencies(capsys, tmp_path):
    import importlib.util
    from pathlib import Path

    script = Path(__file__).parents[2] / "detect-embedding-inventory.py"
    spec = importlib.util.spec_from_file_location("detect_inventory", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(environ={}) == 0
    assert "disabled" in capsys.readouterr().out
    assert (
        module.main(
            ["--dry-run"],
            config_provider=lambda: dict(CONFIG, customized=False),
            receipt_reader=lambda: {"state": "present", "index_empty": True},
            environ={"DOTFILES_RUN_OPENWEBUI_EMBEDDING_INVENTORY": "1"},
        )
        == 0
    )
    output = capsys.readouterr().out
    assert '"status": "UNAVAILABLE"' in output
    assert "secret" not in output
