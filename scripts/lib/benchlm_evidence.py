"""BenchLM Free current-ranking adapter for private, offline model evidence.

The Data REST API requires BENCHLM_DATA_KEY; score rows are internal-use only.
Every published score requires an explicit BenchAlign method, supported maturity,
and an owner-reviewed local-to-upstream identity map. No runtime network calls.
"""

import json
import math
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from artificial_analysis import SameOriginRedirectHandler
from model_evidence import _normalize_source, sources_dir

API_URL = "https://data.benchlm.ai/v1/rankings/current"
SURFACES = ("reasoning", "coding", "agentic", "overall")
PAGE_LIMIT = 100
MAX_PAGES = 20
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
SOURCE_PREFIX = "benchlm"
ATTRIBUTION = "BenchLM.ai, BenchAlign current rankings; internal analysis only"


class BenchlmError(ValueError):
    """Reject unavailable, incomplete or unverified current ranking evidence."""


def _timestamp(value):
    """Normalise an explicitly published UTC date/date-time to an ISO instant."""
    if not isinstance(value, str) or not value:
        raise BenchlmError("BenchLM date missing")
    try:
        recorded = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise BenchlmError("BenchLM date invalid") from None
    if recorded.tzinfo is None:
        if len(value) != 10:
            raise BenchlmError("BenchLM timestamp missing timezone")
        recorded = recorded.replace(tzinfo=timezone.utc)
    recorded = recorded.astimezone(timezone.utc)
    if recorded > datetime.now(timezone.utc):
        raise BenchlmError("BenchLM date is in the future")
    return recorded.isoformat()


def _version(page):
    """Only an explicit scoring-method version may establish a score scale."""
    candidate = page.get("methodologyVersion")
    if not candidate and isinstance(page.get("methodology"), dict):
        candidate = page["methodology"].get("version")
    if not isinstance(candidate, str) or not candidate.strip():
        raise BenchlmError("BenchLM methodology version missing")
    if len(candidate) > 96 or any(ord(ch) < 33 for ch in candidate):
        raise BenchlmError("BenchLM methodology version invalid")
    return candidate


def _field(item, *names):
    """Reject ambiguous identity and measurement aliases instead of guessing."""
    selected = [item[name] for name in names if item.get(name) is not None]
    if not selected or any(value != selected[0] for value in selected):
        return None
    return selected[0]


def _parse_row(item, seen):
    if not isinstance(item, dict):
        raise BenchlmError("BenchLM row is not an object")
    key = _field(item, "modelKey", "modelId", "key")
    creator = _field(item, "creator", "creatorName")
    if (
        not isinstance(key, str)
        or not key.strip()
        or not isinstance(creator, str)
        or not creator.strip()
    ):
        raise BenchlmError("BenchLM exact model identity or creator missing")
    if key in seen:
        raise BenchlmError("BenchLM duplicate model identity")
    seen.add(key)
    maturity = _field(item, "evidenceLabel", "evidenceStatus", "maturity")
    if not isinstance(maturity, str) or maturity.lower() != "supported":
        return None
    raw_score = _field(item, "score", "finalScore")
    if type(raw_score) not in (float, int) or not math.isfinite(raw_score):
        return None
    if not 0 <= raw_score <= 100:
        return None
    return {
        "id": key,
        "creator_id": creator,
        "score": raw_score,
        "evidence": "mixed",
        "benchlm_maturity": "Supported",
    }


def _pagination(rows, page, offset):
    """Check the exact bounded cursor contract before following any next page."""
    if not isinstance(rows, list) or not isinstance(page, dict):
        raise BenchlmError("BenchLM ranking or pagination missing")
    if not rows or len(rows) > PAGE_LIMIT:
        raise BenchlmError("BenchLM empty or oversized page")
    if type(page.get("offset")) is not int or page["offset"] != offset:
        raise BenchlmError("BenchLM pagination offset mismatch")
    total, next_offset = page.get("total"), page.get("nextOffset")
    if type(total) is not int or total < len(rows) or total > MAX_PAGES * PAGE_LIMIT:
        raise BenchlmError("BenchLM ranking total outside supported bounds")
    if next_offset is None:
        if offset + len(rows) < total:
            raise BenchlmError("BenchLM truncated final page")
    elif (
        type(next_offset) is not int
        or next_offset != offset + len(rows)
        or next_offset > total
    ):
        raise BenchlmError("BenchLM pagination cursor invalid")
    return total, next_offset


def _parse_page(payload, *, surface, offset, reference=None):
    if not isinstance(payload, dict):
        raise BenchlmError("BenchLM response is not an object")
    if payload.get("surface") != surface:
        raise BenchlmError("BenchLM ranking surface mismatch")
    date = _timestamp(payload.get("dataAsOf"))
    version = _version(payload)
    rows = payload.get("items")
    total, next_offset = _pagination(rows, payload.get("pagination"), offset)
    if reference is not None and (date, version, total) != reference:
        raise BenchlmError("BenchLM pagination mixed scoring revisions")
    return rows, next_offset, (date, version, total)

def _read_page(surface, offset, key, *, opener):
    query = urllib.parse.urlencode(
        {"surface": surface, "offset": offset, "limit": PAGE_LIMIT, "compact": "true"}
    )
    request = urllib.request.Request(
        API_URL + "?" + query,
        headers={
            "Authorization": "Bearer " + key,
            "Accept": "application/json",
            "User-Agent": "dotfiles-benchlm-free/1",
        },
    )
    try:
        with opener.open(request, timeout=15) as response:
            if response.status != 200:
                raise BenchlmError("BenchLM Free ranking unavailable")
            data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise BenchlmError("BenchLM Free page exceeds bounded size")
            return json.loads(data)
    except (OSError, ValueError):
        raise BenchlmError("BenchLM Free ranking request or decoding failed") from None


def _collect_surface_rows(surface, key, opener):
    """Read one complete consistent surface, fail if any page is ambiguous."""
    collected, seen = [], set()
    cursor, reference = 0, None
    for _ in range(MAX_PAGES):
        payload = _read_page(surface, cursor, key, opener=opener)
        raw, next_cursor, reference = _parse_page(
            payload, surface=surface, offset=cursor, reference=reference
        )
        for item in raw:
            parsed = _parse_row(item, seen)
            if parsed is not None:
                collected.append(parsed)
        if next_cursor is None:
            break
        cursor = next_cursor
    else:
        raise BenchlmError("BenchLM ranking exceeded page limit")
    if len(collected) < 2:
        raise BenchlmError("BenchLM ranking lacks two supported exact identities")
    return collected, reference


def _snapshot_for_surface(surface, key, opener, bundle):
    rows, reference = _collect_surface_rows(surface, key, opener)
    date, version, _ = reference
    source = SOURCE_PREFIX + "-" + surface
    snapshot = {
        "schema_version": 1,
        "bundle": bundle,
        "source": source,
        "metric": surface,
        "scale": "benchalign-" + version + "-" + surface,
        "observed_at": date,
        "attribution": ATTRIBUTION,
        "source_url": "https://benchlm.ai/data/access",
        "usage_terms": "https://benchlm.ai/terms#data-access",
        "models": sorted(rows, key=lambda row: row["id"]),
    }
    if _normalize_source(snapshot, source, now=datetime.now(timezone.utc)) is None:
        raise BenchlmError("BenchLM source fails freshness or provenance validation")
    return snapshot


def fetch_snapshots(api_key=None, *, opener=None):
    """Refresh all four Free current surfaces, never consume historical APIs."""
    key = api_key if api_key is not None else os.environ.get("BENCHLM_DATA_KEY", "")
    if not isinstance(key, str) or not key.strip() or "\n" in key or "\r" in key:
        raise BenchlmError("BENCHLM_DATA_KEY missing or malformed")
    opener = opener or urllib.request.build_opener(SameOriginRedirectHandler())
    bundle = {
        "id": datetime.now(timezone.utc).isoformat(),
        "members": sorted(SOURCE_PREFIX + "-" + s for s in SURFACES),
    }
    return [_snapshot_for_surface(s, key, opener, bundle) for s in SURFACES]

def store_snapshots(snapshots, directory=None):
    """Validate all sources before mutating the last-good local snapshots."""
    directory = Path(directory or sources_dir())
    if directory.is_symlink():
        raise BenchlmError("refusing symlink evidence cache")
    if not isinstance(snapshots, list) or len(snapshots) != len(SURFACES):
        raise BenchlmError("partial BenchLM snapshot set")
    expected = {SOURCE_PREFIX + "-" + s for s in SURFACES}
    if any(not isinstance(row, dict) for row in snapshots):
        raise BenchlmError("BenchLM snapshot is not an object")
    if {row.get("source") for row in snapshots} != expected:
        raise BenchlmError("BenchLM snapshot source set mismatch")
    batches = [row.get("bundle") for row in snapshots]
    if any(not isinstance(value, dict) for value in batches):
        raise BenchlmError("BenchLM missing batch metadata")
    if any(value != batches[0] for value in batches):
        raise BenchlmError("BenchLM mixed snapshot batches")
    members = batches[0].get("members")
    if not isinstance(members, list) or any(not isinstance(m, str) for m in members):
        raise BenchlmError("BenchLM invalid batch member list")
    if set(members) != expected:
        raise BenchlmError("BenchLM batch member mismatch")
    now = datetime.now(timezone.utc)
    for row in snapshots:
        if _normalize_source(row, row["source"], now=now) is None:
            raise BenchlmError("BenchLM snapshot invalid or stale")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    temporary = []
    try:
        for snapshot in snapshots:
            dest = directory / (snapshot["source"] + ".json")
            if dest.is_symlink():
                raise BenchlmError("refusing symlink snapshot")
            fd, name = tempfile.mkstemp(prefix=".benchlm-", dir=directory)
            temporary.append((name, dest))
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(snapshot, handle, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        for name, dest in temporary:
            os.replace(name, dest)
    finally:
        for name, _ in temporary:
            if os.path.exists(name):
                os.unlink(name)


def offline_sources(directory=None):
    """Return only currently validated, same-generation BenchLM snapshots."""
    from model_evidence import load_catalogues

    catalogues = [
        row
        for row in load_catalogues(directory, aa_evidence={})
        if row["source"].startswith(SOURCE_PREFIX + "-")
    ]
    if {row["source"] for row in catalogues} != {
        SOURCE_PREFIX + "-" + s for s in SURFACES
    }:
        raise BenchlmError("BenchLM private cache incomplete or stale")
    return catalogues
