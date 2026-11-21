"""BenchLM Free current-ranking adapter for private, offline model evidence.

The Data REST API requires BENCHLM_DATA_KEY; score rows are internal-use only.
Every published score requires an explicit BenchAlign method and Supported
evidence status; maturity is a separate informational provider field,
and an owner-reviewed local-to-upstream identity map. No runtime network calls.
"""

import json
import math
import os
import tempfile
import time
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
DEFAULT_REQUEST_BUDGET = 12
MIN_REQUEST_INTERVAL = 2.1  # Conservative spacing below 30 requests/minute.
SOURCE_PREFIX = "benchlm"
ATTRIBUTION = "BenchLM.ai, BenchAlign current rankings; internal analysis only"
FIRST_PAGE_COVERAGE = 100


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
    # Live Free API reports "supported" evidenceStatus alongside
    # "developing"/"preliminary" maturity. They are NOT aliases.
    status = item.get("evidenceStatus")
    if status is None:
        status = item.get("evidenceLabel")  # backward-compatible synthetic path
    if not isinstance(status, str) or status.lower() != "supported":
        return None
    maturity = item.get("maturity")
    if maturity is not None and not isinstance(maturity, str):
        raise BenchlmError("BenchLM maturity metadata invalid")
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
        "evidence_status": "Supported",
        "benchlm_maturity": maturity or "unknown",
    }


def _ranking_mode(payload):
    mode = payload.get("rankingMode")
    if mode is None:
        return None
    if (
        not isinstance(mode, str)
        or not mode.strip()
        or len(mode) > 96
        or any(ord(char) < 33 for char in mode)
    ):
        raise BenchlmError("BenchLM ranking mode invalid")
    return mode


def _pagination(rows, page, offset, *, coverage_mode="complete"):
    """Require full source totals unless a labelled top-100 window is chosen."""
    if not isinstance(rows, list) or not isinstance(page, dict):
        raise BenchlmError("BenchLM ranking or pagination missing")
    if not rows or len(rows) > PAGE_LIMIT:
        raise BenchlmError("BenchLM empty or oversized page")
    if type(page.get("offset")) is not int or page["offset"] != offset:
        raise BenchlmError("BenchLM pagination offset mismatch")
    total, next_offset = page.get("total"), page.get("nextOffset")
    reached = offset + len(rows)
    if type(total) is not int or not reached <= total <= MAX_PAGES * PAGE_LIMIT:
        raise BenchlmError("BenchLM ranking total outside supported bounds")
    truncated_at_cap = (
        coverage_mode == "first-100"
        and reached == FIRST_PAGE_COVERAGE
        and total > reached
    )
    if next_offset is None:
        if reached < total and not truncated_at_cap:
            raise BenchlmError("BenchLM truncated final page")
    elif (
        type(next_offset) is not int
        or next_offset != reached
        or next_offset > total
    ):
        raise BenchlmError("BenchLM pagination cursor invalid")
    return total, next_offset


def _parse_page(
    payload, *, surface, offset, reference=None, coverage_mode="complete"
):
    if not isinstance(payload, dict):
        raise BenchlmError("BenchLM response is not an object")
    if payload.get("surface") != surface:
        raise BenchlmError("BenchLM ranking surface mismatch")
    available = payload.get("availableSurfaces")
    if available is not None and (
        not isinstance(available, list)
        or surface not in available
        or len(available) > 100
        or not all(isinstance(name, str) for name in available)
    ):
        raise BenchlmError("BenchLM available surfaces inconsistent")
    notice = payload.get("planNotice")
    if notice is not None and not isinstance(notice, str):
        raise BenchlmError("BenchLM plan notice malformed")
    date = _timestamp(payload.get("dataAsOf"))
    version = _version(payload)
    mode = _ranking_mode(payload)
    rows = payload.get("items")
    total, next_offset = _pagination(
        rows, payload.get("pagination"), offset, coverage_mode=coverage_mode
    )
    selected = (date, version, total, mode)
    if reference is not None and selected != reference:
        raise BenchlmError("BenchLM pagination mixed scoring revisions")
    return rows, next_offset, selected


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
    except BenchlmError:
        raise
    except urllib.error.HTTPError as error:
        if error.code == 429:
            retry = error.headers.get("Retry-After") if error.headers else None
            retry_text = (
                " Retry after " + retry + " seconds."
                if isinstance(retry, str) and retry.isdecimal() and len(retry) <= 5
                else " Check the account's Retry-After response."
            )
            raise BenchlmError(
                "BenchLM Free rate limited (HTTP 429)." + retry_text
            ) from None
        if error.code in (401, 403):
            raise BenchlmError(
                "BenchLM credential or plan rejected (HTTP " + str(error.code) + ")"
            ) from None
        raise BenchlmError("BenchLM HTTP error " + str(error.code)) from None
    except (OSError, ValueError):
        raise BenchlmError("BenchLM Free ranking request or decoding failed") from None


def _collect_surface_rows(surface, request_page, max_pages, coverage_mode):
    """Read one complete consistent surface within the caller's request cap."""
    collected, seen = [], set()
    cursor, reference = 0, None
    for page_index in range(max_pages):
        payload = request_page(surface, cursor)
        raw, next_cursor, reference = _parse_page(
            payload,
            surface=surface,
            offset=cursor,
            reference=reference,
            coverage_mode=coverage_mode,
        )
        if page_index == 0 and math.ceil(reference[2] / PAGE_LIMIT) > max_pages:
            raise BenchlmError("BenchLM surface exceeds max-pages; no cache written")
        for item in raw:
            parsed = _parse_row(item, seen)
            if parsed is not None:
                collected.append(parsed)
        reached = cursor + len(raw)
        if coverage_mode == "first-100" and reached >= min(
            reference[2], FIRST_PAGE_COVERAGE
        ):
            if reached != min(reference[2], FIRST_PAGE_COVERAGE):
                raise BenchlmError("BenchLM first-100 boundary misaligned")
            break
        if next_cursor is None:
            break
        cursor = next_cursor
    else:
        raise BenchlmError("BenchLM surface exceeded max-pages; no cache written")
    if not collected:
        raise BenchlmError("BenchLM ranking has no Supported exact identities")
    return collected, reference, cursor + len(raw)


def _snapshot_for_surface(
    surface, bundle, request_page, max_pages, coverage_mode
):
    rows, reference, retrieved = _collect_surface_rows(
        surface, request_page, max_pages, coverage_mode
    )
    date, version, total, mode = reference
    source = SOURCE_PREFIX + "-" + surface
    snapshot = {
        "schema_version": 1,
        "bundle": bundle,
        "source": source,
        "metric": surface,
        "scale": (
            "benchalign-" + version + ("-" + mode if mode else "")
            + "-" + surface
            + ("-top100" if coverage_mode == "first-100" else "")
        ),
        "ranking_mode": mode or "unreported",
        "coverage": {
            "mode": coverage_mode,
            "retrieved": retrieved,
            "total": total,
        },
        "observed_at": date,
        "attribution": ATTRIBUTION,
        "source_url": "https://benchlm.ai/data/access",
        "usage_terms": "https://benchlm.ai/terms#data-access",
        "models": sorted(rows, key=lambda row: row["id"]),
    }
    if _normalize_source(snapshot, source, now=datetime.now(timezone.utc)) is None:
        raise BenchlmError("BenchLM source fails freshness or provenance validation")
    return snapshot


def _validated_key(api_key):
    key = api_key if api_key is not None else os.environ.get("BENCHLM_DATA_KEY", "")
    if not isinstance(key, str) or not key.strip() or "\n" in key or "\r" in key:
        raise BenchlmError("BENCHLM_DATA_KEY missing or malformed")
    return key


def probe_surface(api_key=None, *, surface="coding", opener=None):
    """One request, no writes, no score output: contract smoke test only."""
    if surface not in SURFACES:
        raise BenchlmError("invalid BenchLM surface")
    key = _validated_key(api_key)
    opener = opener or urllib.request.build_opener(SameOriginRedirectHandler())
    payload = _read_page(surface, 0, key, opener=opener)
    rows, cursor, _ = _parse_page(payload, surface=surface, offset=0)
    seen = set()
    usable = [x for item in rows if (x := _parse_row(item, seen)) is not None]
    return {
        "surface": surface,
        "supported": len(usable),
        "more": cursor is not None,
        "total": payload["pagination"]["total"],
        "ranking_mode": _ranking_mode(payload) or "unreported",
        "plan_notice_present": bool(payload.get("planNotice")),
        "available_surfaces": len(payload.get("availableSurfaces") or SURFACES),
    }


def fetch_snapshots(
    api_key=None,
    *,
    opener=None,
    max_requests=DEFAULT_REQUEST_BUDGET,
    max_pages=MAX_PAGES,
    request_interval=MIN_REQUEST_INTERVAL,
    clock=None,
    sleeper=None,
    coverage_mode="complete",
):
    """Paced, capped, current-only refresh; fail without writing partial data."""
    key = _validated_key(api_key)
    if coverage_mode not in ("complete", "first-100"):
        raise BenchlmError("invalid BenchLM coverage mode")
    if coverage_mode == "first-100" and max_requests < 16:
        raise BenchlmError("first-100 requires a deliberate budget of at least 16 reads")
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES:
        raise BenchlmError("invalid BenchLM max-pages cap")
    if type(max_requests) is not int or not len(SURFACES) <= max_requests <= 80:
        raise BenchlmError("invalid BenchLM request budget")
    if not isinstance(request_interval, (int, float)) or request_interval < 0:
        raise BenchlmError("invalid BenchLM request pacing")
    if opener is None and request_interval < MIN_REQUEST_INTERVAL:
        raise BenchlmError("BenchLM real HTTP pacing cannot be disabled")
    opener = opener or urllib.request.build_opener(SameOriginRedirectHandler())
    clock = clock or time.monotonic
    sleeper = sleeper or time.sleep
    state = {"used": 0, "last": None}

    def request_page(surface, offset):
        if state["used"] >= max_requests:
            raise BenchlmError("BenchLM request budget exceeded; no cache written")
        current = clock()
        if state["last"] is not None:
            delay = request_interval - (current - state["last"])
            if delay > 0:
                sleeper(delay)
        state["last"] = clock()
        state["used"] += 1
        return _read_page(surface, offset, key, opener=opener)

    bundle = {
        "id": datetime.now(timezone.utc).isoformat(),
        "members": sorted(SOURCE_PREFIX + "-" + s for s in SURFACES),
    }
    return [
        _snapshot_for_surface(surface, bundle, request_page, max_pages, coverage_mode)
        for surface in SURFACES
    ]


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
