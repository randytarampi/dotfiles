"""BenchLM Free current-ranking adapter for private, offline model evidence.

The Data REST API requires BENCHLM_DATA_KEY; score rows are internal-use only.
Every published score requires an explicit BenchAlign method, Supported evidenceStatus,
and an owner-reviewed local-to-upstream identity map. No runtime network calls.
"""

import json
import math
import os
import re
import stat
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from artificial_analysis import SameOriginRedirectHandler
from model_evidence import _benchlm_bundle_valid, _normalize_source, sources_dir

API_URL = "https://data.benchlm.ai/v1/rankings/current"
SURFACES = ("reasoning", "coding", "agentic", "overall")
PAGE_LIMIT = 100
MAX_PAGES = 20
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_REQUEST_BUDGET = 12
FREE_WINDOW_ROWS = 100  # Explicit, incomplete Free top-100 window after 5 December.
FIRST_100_PAGE_LIMIT = 25  # Post-transition Free page size.
DEFAULT_FIRST_100_BUDGET = len(SURFACES) * math.ceil(
    FREE_WINDOW_ROWS / FIRST_100_PAGE_LIMIT
)
# From this date Free no longer advertises a complete surface. Complete mode
# must fail closed rather than trust a capped transport total.
FREE_CAP_EFFECTIVE = date(2026, 12, 5)
MIN_REQUEST_INTERVAL = 2.1  # Conservative spacing below 30 requests/minute.
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
    """Supported evidence status is not model maturity.

    Live BenchLM exposes evidenceStatus=supported alongside maturity=developing;
    conflating these axes discarded every real row in the original adapter.
    """
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
    status = item.get("evidenceStatus")
    if status is None:
        status = item.get("evidenceLabel")  # Legacy synthetic snapshots only.
    if not isinstance(status, str) or status.casefold() != "supported":
        return None
    maturity = item.get("maturity")
    if maturity is not None and (
        not isinstance(maturity, str) or len(maturity) > 80 or not maturity.strip()
    ):
        raise BenchlmError("BenchLM model maturity malformed")
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
        "benchlm_evidence_status": "supported",
        "benchlm_maturity": maturity or "unknown",
    }


def _pagination(rows, page, offset, coverage_mode):
    """Enforce complete data, or a deliberately bounded first-100 Free window."""
    if not isinstance(rows, list) or not isinstance(page, dict):
        raise BenchlmError("BenchLM ranking or pagination missing")
    if not rows or len(rows) > PAGE_LIMIT:
        raise BenchlmError("BenchLM empty or oversized page")
    if type(page.get("offset")) is not int or page["offset"] != offset:
        raise BenchlmError("BenchLM pagination offset mismatch")
    total, next_offset = page.get("total"), page.get("nextOffset")
    if type(total) is not int or total < len(rows) or total > MAX_PAGES * PAGE_LIMIT:
        raise BenchlmError("BenchLM ranking total outside supported bounds")
    window = min(total, FREE_WINDOW_ROWS) if coverage_mode == "first-100" else total
    end = offset + len(rows)
    if end > window:
        raise BenchlmError("BenchLM returned rows outside selected coverage window")
    if next_offset is None:
        if end < window:
            raise BenchlmError("BenchLM truncated final page")
    elif type(next_offset) is not int or next_offset != end or next_offset > total:
        raise BenchlmError("BenchLM pagination cursor invalid")
    if coverage_mode == "first-100" and end == window:
        return total, None
    return total, next_offset


def _parse_page(payload, *, surface, offset, reference=None, coverage_mode="complete"):
    if not isinstance(payload, dict):
        raise BenchlmError("BenchLM response is not an object")
    if payload.get("surface") != surface:
        raise BenchlmError("BenchLM ranking surface mismatch")
    available = payload.get("availableSurfaces")
    if available is not None:
        if not isinstance(available, list):
            raise BenchlmError("BenchLM availability malformed")
        advertised = []
        for entry in available:
            # Live Free responses list objects with a `surface` key; tolerate
            # the documented string form as well.
            if isinstance(entry, str):
                advertised.append(entry)
            elif isinstance(entry, dict) and isinstance(entry.get("surface"), str):
                advertised.append(entry["surface"])
            else:
                raise BenchlmError("BenchLM availability malformed")
        if surface not in advertised:
            raise BenchlmError("BenchLM surface absent from advertised availability")
    notice = payload.get("planNotice")
    if notice is not None and (not isinstance(notice, str) or len(notice) > 2048):
        raise BenchlmError("BenchLM plan notice malformed")
    date = _timestamp(payload.get("dataAsOf"))
    version = _version(payload)
    mode = payload.get("rankingMode")
    if not isinstance(mode, str) or not mode.strip() or len(mode) > 96:
        raise BenchlmError("BenchLM ranking mode missing or invalid")
    if any(ord(char) < 33 for char in mode):
        raise BenchlmError("BenchLM ranking mode invalid")
    rows = payload.get("items")
    total, next_offset = _pagination(
        rows, payload.get("pagination"), offset, coverage_mode
    )
    if (
        coverage_mode == "complete"
        and total > FREE_WINDOW_ROWS
        and isinstance(notice, str)
        and "first 100" in notice.lower()
        and datetime.now(timezone.utc).date() >= datetime(2026, 12, 5).date()
    ):
        raise BenchlmError(
            "BenchLM Free announces a first-100 cap; request --coverage first-100"
        )
    if reference is not None and (date, version, mode, total) != reference:
        raise BenchlmError("BenchLM pagination mixed scoring revisions")
    return rows, next_offset, (date, version, mode, total)


def _read_page(surface, offset, key, *, opener, page_limit=PAGE_LIMIT):
    query = urllib.parse.urlencode(
        {"surface": surface, "offset": offset, "limit": page_limit, "compact": "true"}
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
    """Read one consistent surface within the caller's approved coverage."""
    collected, seen = [], set()
    cursor, reference, observed_rows = 0, None, 0
    for page_index in range(max_pages):
        payload = request_page(surface, cursor)
        raw, next_cursor, reference = _parse_page(
            payload,
            surface=surface,
            offset=cursor,
            reference=reference,
            coverage_mode=coverage_mode,
        )
        total = reference[3]
        window = min(total, FREE_WINDOW_ROWS) if coverage_mode == "first-100" else total
        if page_index == 0 and math.ceil(window / len(raw)) > max_pages:
            raise BenchlmError("BenchLM window exceeds max-pages; no cache written")
        observed_rows += len(raw)
        for item in raw:
            parsed = _parse_row(item, seen)
            if parsed is not None:
                collected.append(parsed)
        if next_cursor is None:
            break
        cursor = next_cursor
    else:
        raise BenchlmError("BenchLM window exceeded max-pages; no cache written")
    if observed_rows != window:
        raise BenchlmError("BenchLM incomplete coverage window")
    if len(collected) < 2:
        raise BenchlmError("BenchLM ranking lacks two Supported exact identities")
    return collected, reference, observed_rows


def _snapshot_for_surface(surface, bundle, request_page, max_pages, coverage_mode):
    rows, reference, observed_rows = _collect_surface_rows(
        surface, request_page, max_pages, coverage_mode
    )
    date, version, mode, total = reference
    source = SOURCE_PREFIX + "-" + surface
    snapshot = {
        "schema_version": 1,
        "bundle": bundle,
        "source": source,
        "metric": surface,
        "scale": (
            "benchalign-"
            + mode
            + "-"
            + version
            + "-"
            + surface
            # A bounded prefix must never share a scale with a complete set,
            # or a map authored against complete data would silently match it.
            + ("-top100" if coverage_mode == "first-100" else "")
        ),
        "ranking_mode": mode,
        "methodology_version": version,
        "observed_at": date,
        "attribution": ATTRIBUTION,
        "source_url": "https://benchlm.ai/data/access",
        "usage_terms": "https://benchlm.ai/terms#data-access",
        # Partial mode is a coverage *policy*, not an inference of completeness
        # from a total field that the Free API may itself cap at 100.
        "coverage": "first-100-subset" if coverage_mode == "first-100" else "complete",
        "retrieved_rows": observed_rows,
        "population_total": total,
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
    payload = _read_page(surface, 0, key, opener=opener, page_limit=25)
    rows, cursor, _ = _parse_page(
        payload, surface=surface, offset=0, coverage_mode="first-100"
    )
    seen = set()
    usable = [x for item in rows if (x := _parse_row(item, seen)) is not None]
    if len(usable) < 2:
        raise BenchlmError("BenchLM first page has insufficient Supported rows")
    return {
        "surface": surface,
        "supported": len(usable),
        "more": cursor is not None,
        "ranking_mode": payload["rankingMode"],
        "total_rows": payload["pagination"]["total"],
        "plan_notice_present": bool(payload.get("planNotice")),
    }


def fetch_snapshots(
    api_key=None,
    *,
    opener=None,
    max_requests=None,
    max_pages=MAX_PAGES,
    request_interval=MIN_REQUEST_INTERVAL,
    clock=None,
    sleeper=None,
    coverage_mode="complete",
    today=None,
):
    """Paced, capped, current-only refresh; fail without writing partial data."""
    key = _validated_key(api_key)
    if coverage_mode not in {"complete", "first-100"}:
        raise BenchlmError("invalid BenchLM coverage mode")
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES:
        raise BenchlmError("invalid BenchLM max-pages cap")
    if max_requests is None:
        max_requests = (
            DEFAULT_FIRST_100_BUDGET
            if coverage_mode == "first-100"
            else DEFAULT_REQUEST_BUDGET
        )
    if type(max_requests) is not int or not len(SURFACES) <= max_requests <= 80:
        raise BenchlmError("invalid BenchLM request budget")
    if coverage_mode == "first-100":
        needed = len(SURFACES) * math.ceil(FREE_WINDOW_ROWS / FIRST_100_PAGE_LIMIT)
        if max_pages * FIRST_100_PAGE_LIMIT < FREE_WINDOW_ROWS:
            raise BenchlmError(
                "first-100 coverage needs at least "
                + str(math.ceil(FREE_WINDOW_ROWS / FIRST_100_PAGE_LIMIT))
                + " pages per surface"
            )
        if max_requests < needed:
            raise BenchlmError(
                "first-100 coverage needs a request budget of at least " + str(needed)
            )
    elif (today or datetime.now(timezone.utc).date()) >= FREE_CAP_EFFECTIVE:
        raise BenchlmError(
            "BenchLM Free no longer advertises complete coverage; "
            "use --coverage first-100 and treat the result as incomplete"
        )
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
        return _read_page(
            surface,
            offset,
            key,
            opener=opener,
            page_limit=(
                FIRST_100_PAGE_LIMIT if coverage_mode == "first-100" else PAGE_LIMIT
            ),
        )

    bundle = {
        "id": datetime.now(timezone.utc).isoformat(),
        "members": sorted(SOURCE_PREFIX + "-" + s for s in SURFACES),
    }
    return [
        _snapshot_for_surface(surface, bundle, request_page, max_pages, coverage_mode)
        for surface in SURFACES
    ]


def _atomic_private(path, payload):
    descriptor, temporary = tempfile.mkstemp(prefix=".benchlm-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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
    normalized = []
    for row in snapshots:
        item = _normalize_source(row, row["source"], now=now)
        if item is None:
            raise BenchlmError("BenchLM snapshot invalid or stale")
        normalized.append(item)
    if not _benchlm_bundle_valid(normalized, manifest_bundle=batches[0]):
        raise BenchlmError("BenchLM snapshot bundle inconsistent before publish")
    if directory.exists() and not directory.is_dir():
        raise BenchlmError("refusing non-directory evidence cache")
    created = not directory.exists()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Never chmod a pre-existing caller-selected directory; refuse it instead.
    if created:
        os.chmod(directory, 0o700)
    if stat.S_IMODE(directory.stat().st_mode) & 0o077:
        raise BenchlmError("evidence cache directory must be private (0700)")
    generation = re.sub(r"[^A-Za-z0-9._-]", "-", str(batches[0].get("id", "")))
    generation = generation[:120].strip(".-") or "generation"
    generations = directory / "generations"
    if generations.is_symlink():
        raise BenchlmError("refusing symlink generation root")
    generations.mkdir(mode=0o700, exist_ok=True)
    if stat.S_IMODE(generations.stat().st_mode) & 0o077:
        raise BenchlmError("generation root must be private (0700)")
    root = generations / generation
    if root.exists() or root.is_symlink():
        raise BenchlmError("refusing existing BenchLM generation directory")
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    written = []
    try:
        for snapshot in snapshots:
            destination = root / (snapshot["source"] + ".json")
            _atomic_private(
                destination,
                (json.dumps(snapshot, sort_keys=True, indent=2) + "\n").encode(),
            )
            written.append(destination)
        manifest = {
            "schema_version": 1,
            "generation": generation,
            "bundle": batches[0],
            "sources": [path.name for path in written],
        }
        # Manifest last: an interrupted publish keeps the previous generation
        # readable instead of invalidating the whole cache set.
        _atomic_private(
            directory / "manifest.json",
            (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode(),
        )
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        try:
            root.rmdir()
        except OSError:
            pass
        raise


def offline_sources(directory=None):
    """Return one coherent, validated BenchLM generation and its coverage."""
    from model_evidence import load_catalogues

    expected = {SOURCE_PREFIX + "-" + surface for surface in SURFACES}
    catalogues = [
        row
        for row in load_catalogues(directory, aa_evidence={})
        if row["source"].startswith(SOURCE_PREFIX + "-")
    ]
    by_source = {row["source"]: row for row in catalogues}
    if set(by_source) != expected:
        raise BenchlmError("BenchLM private cache incomplete or stale")
    bundles = [row.get("bundle") for row in by_source.values()]
    if any(not isinstance(bundle, dict) for bundle in bundles) or any(
        bundle != bundles[0] for bundle in bundles
    ):
        raise BenchlmError("BenchLM private cache is not one coherent generation")
    if set(bundles[0].get("members") or []) != expected:
        raise BenchlmError("BenchLM private cache bundle membership mismatch")
    coverages = {row.get("coverage") for row in by_source.values()}
    if len(coverages) != 1 or None in coverages:
        raise BenchlmError("BenchLM private cache mixes coverage policies")
    return catalogues
