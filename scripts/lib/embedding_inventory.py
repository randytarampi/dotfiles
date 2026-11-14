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

import datetime
import json
import os
import stat as stat_module
from pathlib import Path

from embedding_representation import REQUIRED_FIELDS

NO_AUTHORITATIVE_INTERFACE = "no-authoritative-interface"
NO_AUTHORITATIVE_PROBE = "no-authoritative-probe-contract"
NO_COLLECTION_LISTING = "no authoritative read-only collection listing on this build"


def _fetch_config(config_provider):
    """Call the injected config getter; return (config, error_reason)."""
    if not callable(config_provider):
        return None, "config getter unavailable"
    try:
        config = config_provider()
    except Exception:
        return None, "config getter unavailable"
    if not isinstance(config, dict):
        return None, "config getter returned an unhealthy shape"
    return config, None


def _fetch_receipt(receipt_reader):
    """Call the injected receipt reader; return (receipt, error_reason)."""
    if not callable(receipt_reader):
        return None, "receipt unavailable"
    try:
        receipt = receipt_reader()
    except Exception:
        return None, "receipt unavailable or invalid"
    if not isinstance(receipt, dict) or receipt.get("state") not in {
        "absent",
        "present",
    }:
        return None, "receipt reader returned an unhealthy shape"
    return dict(receipt), None


def _provenance_report(config):
    customized = config.get("customized")
    override = config.get("provenance") == "override" or customized is True
    if override:
        status = "OVERRIDE"
    elif customized is False:
        status = "DEFAULT"
    else:
        status = "UNKNOWN"
    return {"status": status, "reason": "config-provenance" if override else None}


def _adoption_report(config, receipt, config_error, receipt_error):
    customized = config.get("customized") if config is not None else None
    if config is None:
        return {"status": "UNKNOWN", "reason": "config evidence unavailable"}
    if custom_provenance(config):
        return {"status": "REFUSED", "reason": "pinned user override"}
    if (
        customized is False
        and receipt is not None
        and receipt.get("index_empty") is True
    ):
        return {"status": "PERMITTED", "reason": None}
    return {
        "status": "UNKNOWN",
        "reason": receipt_error or "defaults and empty index not both proven",
    }


def custom_provenance(config):
    """True only for an explicit pinned user override on the config."""
    return config.get("provenance") == "override" or config.get("customized") is True


def collect_inventory(config_provider, receipt_reader, *, now=None):
    """Collect config and receipt evidence without probing or mutating services."""
    timestamp = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    config, config_error = _fetch_config(config_provider)
    receipt, receipt_error = _fetch_receipt(receipt_reader)
    usable_receipt = (
        receipt
        if receipt is not None and receipt.get("state") in {"absent", "present"}
        else None
    )
    report = build_inventory(config or {}, state=usable_receipt)
    if config is None:
        report["webui_config_identity"] = {"status": "UNKNOWN", "reason": config_error}
    report["config_provenance"] = (
        {"status": "UNKNOWN", "reason": config_error}
        if config is None
        else _provenance_report(config)
    )
    report["adoption"] = _adoption_report(config, receipt, config_error, receipt_error)
    report["collected_at"] = timestamp
    report["collections"] = {
        "count": "UNKNOWN",
        "routes": [
            {
                "route_id": "embedding",
                "status": "UNAVAILABLE",
                "reason": NO_COLLECTION_LISTING,
            }
        ],
        "identity": _collection_identity_report(),
        "reason": NO_COLLECTION_LISTING,
    }
    if receipt_error:
        report["state"] = {"state": "UNKNOWN", "reason": receipt_error}
    return report


def read_state(path: Path):
    """Validate private ledger existence without disclosing its contents."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"state": "absent"}
    if stat_module.S_ISLNK(info.st_mode):
        raise ValueError("embedding state path is a symlink")
    if not stat_module.S_ISREG(info.st_mode):
        raise ValueError("embedding state file is not a regular file")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("embedding state no-follow reads unsupported")
    try:
        fd = os.open(path, os.O_RDONLY | nofollow | os.O_NONBLOCK)
    except OSError as error:
        raise ValueError("embedding state cannot be opened safely") from error
    try:
        with os.fdopen(fd, "rb") as source:
            opened = os.fstat(source.fileno())
            if (
                not stat_module.S_ISREG(opened.st_mode)
                or opened.st_uid != os.getuid()
                or opened.st_nlink != 1
                or (stat_module.S_IMODE(opened.st_mode) & 0o077)
            ):
                raise ValueError("embedding state file has unsafe permissions")
            if opened.st_size > 1024 * 1024:
                raise ValueError("embedding state file exceeds read-only limit")
            raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("embedding state file exceeds read-only limit")
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("corrupt embedding state file") from error
    if not isinstance(data, dict):
        raise ValueError("corrupt embedding state file: not a JSON object")
    return {"state": "present"}


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
    presence receipt from :func:`read_state` when the caller has one. The
    report contains no vectors, credentials or user content.
    """
    if not isinstance(config, dict):
        raise ValueError("embedding configuration must be a mapping")
    engine = config.get("RAG_EMBEDDING_ENGINE", "")
    model = config.get("RAG_EMBEDDING_MODEL", "")
    if not isinstance(engine, str) or not isinstance(model, str):
        raise ValueError("embedding configuration identifiers must be strings")
    if state is not None and state.get("state") not in {"absent", "present"}:
        raise ValueError("unrecognised embedding ledger state")
    return {
        "writes": "none",
        "state": {"state": (state or {"state": "absent"})["state"]},
        "webui_config_identity": {
            "engine": engine,
            "model": model,
        },
        "collections": {
            "count": "UNKNOWN",
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
