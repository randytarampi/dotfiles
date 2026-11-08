"""Read-only embedding inventory (H1): authoritative identity, honest UNKNOWN.

Collects Open WebUI embedding configuration and per-collection index
representation state without writes. The delivered WebUI integration exposes
an embedding-config getter but no authoritative per-collection Chroma
index/metadata interface or deterministic vector-probe contract, so
per-collection identity fields are UNKNOWN with a stable machine-readable
reason rather than synthesized values. The fixed-text digest machinery stays
NOT_RUN on hosts until such a contract exists; it is implemented and
unit-tested on synthetic fixtures. No adoption, migration, reset or probe is
performed here.
"""

from __future__ import annotations

import json
import os
import stat as stat_module
from pathlib import Path

from embedding_representation import REQUIRED_FIELDS

NO_AUTHORITATIVE_INTERFACE = "no-authoritative-interface"
NO_AUTHORITATIVE_PROBE = "no-authoritative-probe-contract"


def read_state(path: Path):
    """Read an embedding ledger state file with strict refusal semantics.

    Absent is a distinct non-error state: current installations normally have
    no managed ledger. Refuse symlinked, unreadable, unsafe-permission or
    corrupt JSON state; never follow user content into the report.
    """
    if path.is_symlink():
        raise ValueError("embedding state path is a symlink")
    if not path.is_file():
        return {"state": "absent"}
    info = path.stat()
    if (
        info.st_uid != os.getuid()
        or info.st_nlink != 1
        or (stat_module.S_IMODE(info.st_mode) & 0o077)
    ):
        raise ValueError("embedding state file has unsafe permissions")
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"corrupt embedding state file: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("corrupt embedding state file: not a JSON object")
    return {"state": "present", "data": data}


def _collection_identity_report():
    return {
        field: {"status": "UNKNOWN", "reason": NO_AUTHORITATIVE_INTERFACE}
        for field in REQUIRED_FIELDS
        if field != "fixed_vector_sha256"
    }


def build_inventory(config, *, state: dict | None = None):
    """Build the read-only H1 inventory report for a WebUI embedding config.

    ``config`` is the mapping returned by the WebUI embedding-config getter
    (already fetched read-only by the caller). ``state`` is the parsed ledger
    state from :func:`read_state` when the caller has one to attach. The
    report contains no vectors, credentials or user content.
    """
    return {
        "writes": "none",
        "state": (state or {"state": "absent"}),
        "webui_config_identity": {
            "engine": config.get("RAG_EMBEDDING_ENGINE", ""),
            "model": config.get("RAG_EMBEDDING_MODEL", ""),
        },
        "collections": {
            "identity": _collection_identity_report(),
            "reason": NO_AUTHORITATIVE_INTERFACE,
        },
        "fixed_text_digest": {
            "status": "NOT_RUN",
            "reason": NO_AUTHORITATIVE_PROBE,
        },
        "representation_comparison": (
            "compare_representations(expected, observed) from "
            "embedding_representation.py demonstrates same-width/"
            "different-representation as MISMATCH; no observed representation "
            "is available without an authoritative probe contract"
        ),
    }
