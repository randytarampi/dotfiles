"""Private whatmodel.sh overall-quality snapshot (CC BY-SA 4.0).

The upstream public leaderboard is independent of local runtime eligibility.
Do not transpose scores onto a model family, quantization, or route by name.
"""

import json
import math
import os
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from artificial_analysis import SameOriginRedirectHandler
from model_evidence import _normalize_source, sources_dir

SOURCE_URL = "https://whatmodel.sh/?format=json&n=2000&thin=1&retired=0"
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 2000
LICENCE = "CC BY-SA 4.0"
SOURCE_ID = "whatmodel"
SCALE = "whatmodel-g-v1"
ATTRIBUTION = "whatmodel.sh / Merlinsbeard Pty Ltd; underlying notices apply"


class WhatmodelError(ValueError):
    """Upstream input failed validation; preserve the prior snapshot."""


def normalize_response(payload, *, now=None):
    """Strictly verify published JSON shape before extracting comparable rows."""
    if not isinstance(payload, dict):
        raise WhatmodelError("invalid leaderboard response")
    if (
        payload.get("category") != "llm"
        or payload.get("score_mode") != "overall"
        or payload.get("comparison_set") is not None
        or payload.get("task") is not None
        or not isinstance(payload.get("data_license"), dict)
        or payload["data_license"].get("name") != LICENCE
        or not isinstance(payload.get("models"), list)
    ):
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
    if not 1 <= len(incoming) <= MAX_ROWS:
        raise WhatmodelError("empty or oversized leaderboard")
    if payload.get("coverage") == "no reviewed rankings available":
        raise WhatmodelError("no reviewed scores")
    rows, seen = [], set()
    for item in incoming:
        if not isinstance(item, dict):
            raise WhatmodelError("non-object model row")
        ident = item.get("id")
        maker = item.get("provider")
        if (
            not isinstance(ident, str)
            or not ident.strip()
            or not isinstance(maker, str)
            or not maker.strip()
            or ident in seen
        ):
            raise WhatmodelError("invalid or duplicate upstream identity")
        seen.add(ident)
        count = item.get("n")
        score = item.get("score_full")
        if (
            type(count) is not int
            or count < 3
            or type(score) not in (float, int)
            or not math.isfinite(score)
            or item.get("maker_only") is not False
            or score < 0
            or score > 100
        ):
            continue
        rows.append(
            {
                "id": ident,
                "creator_id": maker,
                "score": score,
                "n": count,
                "evidence": "mixed",
            }
        )
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
            return json.loads(payload)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
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
