"""Private whatmodel.sh overall-quality snapshot (CC BY-SA 4.0).

The upstream public leaderboard is independent of local runtime eligibility.
Do not transpose scores onto a model family, quantization, or route by name.
"""

import json
import math
import os
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from artificial_analysis import SameOriginRedirectHandler
from model_evidence import _normalize_source, sources_dir

SOURCE_URL = "https://whatmodel.sh/?format=json"
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 2000
# The public JSON API may expose only a reviewed subset (40 rows in the
# 2026-11-21 live review); do not infer a full-catalogue population.
MIN_EXPECTED_ROWS = 2
LICENCE = "CC BY-SA 4.0"
SOURCE_ID = "whatmodel"
SCALE = "whatmodel-g-v1"
ATTRIBUTION = "whatmodel.sh / Merlinsbeard Pty Ltd; underlying notices apply"


class WhatmodelError(ValueError):
    """Upstream input failed validation; preserve the prior snapshot."""


def _valid_envelope(payload):
    if not isinstance(payload, dict):
        return False
    if payload.get("category") != "llm":
        return False
    if payload.get("score_mode") != "overall":
        return False
    if payload.get("comparison_set") is not None:
        return False
    if payload.get("task") is not None:
        return False
    licence = payload.get("data_license")
    if not isinstance(licence, dict):
        return False
    if licence.get("name") != LICENCE:
        return False
    return isinstance(payload.get("models"), list)


def _validated_row(item, seen):
    """Reject ambiguous identities, skip unreviewed and sparse scores."""
    if not isinstance(item, dict):
        raise WhatmodelError("non-object model row")
    ident = item.get("id")
    maker = item.get("provider")
    if not isinstance(ident, str) or not ident.strip():
        raise WhatmodelError("invalid model identity")
    if not isinstance(maker, str) or not maker.strip():
        raise WhatmodelError("invalid creator identity")
    if ident in seen:
        raise WhatmodelError("duplicate upstream model identity")
    seen.add(ident)
    count = item.get("n")
    score = item.get("score_full")
    if type(count) is not int or count < 3:
        return None
    if type(score) not in (float, int):
        return None
    if not math.isfinite(score):
        return None
    if not 0 <= score <= 100:
        return None
    if item.get("maker_only") is not False:
        return None
    return {
        "id": ident,
        "creator_id": maker,
        "score": score,
        "n": count,
        "evidence": "mixed",
    }


def normalize_response(payload, *, now=None):
    """Verify public overall-quality schema before extracting comparable rows."""
    if not _valid_envelope(payload):
        raise WhatmodelError("unexpected category, score scale or licence")
    source_updated = payload.get("updated")
    if not isinstance(source_updated, str):
        raise WhatmodelError("missing upstream update timestamp")
    try:
        updated = datetime.fromisoformat(source_updated.replace("Z", "+00:00"))
        if updated.tzinfo is None:
            raise ValueError("timezone required")
    except ValueError:
        raise WhatmodelError("invalid update timestamp") from None
    now = now or datetime.now(timezone.utc)
    if updated > now:
        raise WhatmodelError("upstream update timestamp is in the future")
    incoming = payload["models"]
    if not MIN_EXPECTED_ROWS <= len(incoming) <= MAX_ROWS:
        raise WhatmodelError("whatmodel returned fewer than two or too many rows")
    if payload.get("coverage") == "no reviewed rankings available":
        raise WhatmodelError("no reviewed scores")
    seen = set()
    rows = []
    for item in incoming:
        row = _validated_row(item, seen)
        if row is not None:
            rows.append(row)
    if len(rows) < 2:
        raise WhatmodelError("insufficient independently reviewed model evidence")
    return {
        "schema_version": 1,
        "source": SOURCE_ID,
        "metric": "overall",
        "scale": SCALE,
        "observed_at": source_updated,
        "attribution": ATTRIBUTION,
        "licence": LICENCE,
        "licence_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "source_url": "https://whatmodel.sh/",
        "retrieved_rows": len(incoming),
        "coverage": (
            payload.get("coverage")
            if isinstance(payload.get("coverage"), str)
            else "unknown; incomplete"
        ),
        "models": sorted(rows, key=lambda row: row["id"]),
    }


def fetch_response(*, opener=None):
    request = urllib.request.Request(
        SOURCE_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "dotfiles-model-evidence/1",
        },
    )
    opener = opener or urllib.request.build_opener(SameOriginRedirectHandler())
    try:
        with opener.open(request, timeout=15) as response:
            if response.status != 200:
                raise WhatmodelError("whatmodel returned non-200")
            payload = response.read(MAX_BYTES + 1)
            if len(payload) > MAX_BYTES:
                raise WhatmodelError("whatmodel response too large")
            try:
                return json.loads(payload)
            except (ValueError, UnicodeError):
                raise WhatmodelError(
                    "whatmodel served non-JSON to this client; no UA impersonation"
                ) from None
    except WhatmodelError:
        raise
    except (OSError, ValueError):
        raise WhatmodelError("whatmodel unavailable or response invalid") from None


def target_path(directory=None):
    return Path(directory or sources_dir()) / (SOURCE_ID + ".json")


def store_snapshot(snapshot, directory=None):
    """Atomic, private file replacement. Incomplete fetches keep the old file."""
    directory = Path(directory or sources_dir())
    if directory.is_symlink():
        raise WhatmodelError("refusing symlink cache directory")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    target = target_path(directory)
    if target.is_symlink():
        raise WhatmodelError("refusing symlink snapshot")
    validated = _normalize_source(snapshot, SOURCE_ID, now=datetime.now(timezone.utc))
    if validated is None:
        raise WhatmodelError("source schema or freshness failed")
    contents = (json.dumps(snapshot, sort_keys=True, indent=2) + "\n").encode()
    descriptor, temp = tempfile.mkstemp(prefix=".whatmodel-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return target
