"""Private, Free-tier Artificial Analysis evidence snapshots.

The API data is licensed for internal use, not public redistribution.
https://artificialanalysis.ai/data-api/docs
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENDPOINT = "https://artificialanalysis.ai/api/v2/language/models/free"
SCHEMA_VERSION = 1
MAX_PAGES = 20
COLUMNS = (
    "id",
    "slug",
    "name",
    "creator_id",
    "creator_name",
    "intelligence",
    "coding",
    "agentic",
)


class EvidenceError(ValueError):
    """Evidence is unavailable or cannot be safely used."""


def cache_dir():
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "dotfiles" / "artificial-analysis"


def _numeric(value):
    """Missing index != index 0. Keep the API's null distinction."""
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceError("invalid index value")
    if not math.isfinite(value):
        raise EvidenceError("nonfinite index value")
    return str(value)


def _string(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise EvidenceError("invalid model identity")
    if any(ord(char) < 32 for char in value):
        raise EvidenceError("control character in model identity")
    return value


def normalize_pages(pages):
    """Validate every page and produce a small, deterministic CSV payload."""
    if not pages or len(pages) > MAX_PAGES:
        raise EvidenceError("missing or oversized pagination")
    rows, seen = [], set()
    total, version = None, None
    for number, envelope in enumerate(pages, 1):
        if not isinstance(envelope, dict) or envelope.get("tier") not in {
            "free",
            "pro",
            "commercial",
        }:
            raise EvidenceError("malformed Free API envelope")
        pagination = envelope.get("pagination")
        if not isinstance(pagination, dict):
            raise EvidenceError("missing pagination")
        page = pagination.get("page")
        count = pagination.get("total_pages")
        if (
            type(page) is not int
            or type(count) is not int
            or page != number
            or not 1 <= count <= MAX_PAGES
            or (number < count) != pagination.get("has_more")
        ):
            raise EvidenceError("inconsistent pagination")
        if total is None:
            total = count
        if count != total:
            raise EvidenceError("page count changed")
        candidate_version = envelope.get("intelligence_index_version")
        if (
            isinstance(candidate_version, bool)
            or not isinstance(candidate_version, (float, int))
            or not math.isfinite(candidate_version)
            or candidate_version <= 0
        ):
            raise EvidenceError("missing or invalid Intelligence Index version")
        if version is None:
            version = candidate_version
        if candidate_version != version:
            raise EvidenceError("Index version changed during pagination")
        records = envelope.get("data")
        if not isinstance(records, list) or not records:
            raise EvidenceError("empty or malformed page")
        for record in records:
            if not isinstance(record, dict):
                raise EvidenceError("malformed model")
            creator = record.get("model_creator")
            metrics = record.get("evaluations")
            if not isinstance(creator, dict) or not isinstance(metrics, dict):
                raise EvidenceError("missing model creator or metrics")
            model_id = _string(record.get("id"))
            if model_id in seen:
                raise EvidenceError("duplicate model ID")
            seen.add(model_id)
            rows.append(
                {
                    "id": model_id,
                    "slug": _string(record.get("slug")),
                    "name": _string(record.get("name")),
                    "creator_id": _string(creator.get("id")),
                    "creator_name": _string(creator.get("name")),
                    "intelligence": _numeric(
                        metrics.get("artificial_analysis_intelligence_index")
                    ),
                    "coding": _numeric(metrics.get("artificial_analysis_coding_index")),
                    "agentic": _numeric(
                        metrics.get("artificial_analysis_agentic_index")
                    ),
                }
            )
    if len(pages) != total:
        raise EvidenceError("incomplete pagination")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(sorted(rows, key=lambda item: item["id"]))
    return output.getvalue().encode("utf-8"), str(version), len(rows)


def _request_page(page, api_key):
    url = ENDPOINT + "?" + urllib.parse.urlencode({"page": page})
    request = urllib.request.Request(
        url, headers={"x-api-key": api_key, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status != 200:
                raise EvidenceError("Artificial Analysis returned non-200")
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Never echo request headers, URL query strings, credentials, or body.
        raise EvidenceError(
            "Artificial Analysis rate limited"
            if exc.code == 429
            else "Artificial Analysis request failed"
        ) from None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        raise EvidenceError("Artificial Analysis network or JSON failure") from None


def fetch_free(api_key, *, get_page=None):
    if not api_key:
        raise EvidenceError("ARTIFICIAL_ANALYSIS_API_KEY is missing")
    fetch = get_page or (lambda page: _request_page(page, api_key))
    pages = [fetch(1)]
    pagination = pages[0].get("pagination") if isinstance(pages[0], dict) else {}
    total = pagination.get("total_pages") if isinstance(pagination, dict) else None
    if type(total) is not int or not 1 <= total <= MAX_PAGES:
        raise EvidenceError("invalid Free API page count")
    for page in range(2, total + 1):
        pages.append(fetch(page))
    return normalize_pages(pages)


def _private_directory(path):
    if path.is_symlink():
        raise EvidenceError("refusing symlink cache directory")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def _atomic_private(path, payload):
    descriptor, tmp = tempfile.mkstemp(prefix=".aa-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_snapshot(directory, csv_bytes, version, count, *, now=None):
    """Publish the manifest last: interruptions leave old data usable."""
    directory = Path(directory)
    _private_directory(directory)
    snapshots = directory / "snapshots"
    _private_directory(snapshots)
    digest = hashlib.sha256(csv_bytes).hexdigest()
    filename = digest + ".csv"
    target = snapshots / filename
    if target.is_symlink():
        raise EvidenceError("refusing symlink snapshot")
    if not target.exists():
        _atomic_private(target, csv_bytes)
    elif hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise EvidenceError("existing snapshot checksum mismatch")
    instant = now or datetime.now(timezone.utc)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source": ENDPOINT,
        "tier": "free-shape",
        "observed_at": instant.isoformat(),
        "index_version": version,
        "model_count": count,
        "snapshot": filename,
        "sha256": digest,
    }
    if (directory / "manifest.json").is_symlink():
        raise EvidenceError("refusing symlink manifest")
    _atomic_private(
        directory / "manifest.json",
        (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )
    return manifest


def load_snapshot(directory=None, *, max_age_days=30, now=None):
    """Return trustworthy local evidence, or None; never makes network calls."""
    directory = Path(directory or cache_dir())
    manifest_path = directory / "manifest.json"
    if directory.is_symlink() or manifest_path.is_symlink():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != SCHEMA_VERSION:
            return None
        if manifest.get("source") != ENDPOINT:
            return None
        digest = manifest["sha256"]
        if not isinstance(digest, str) or len(digest) != 64:
            return None
        if any(char not in "0123456789abcdef" for char in digest):
            return None
        if manifest.get("snapshot") != digest + ".csv":
            return None
        observed = datetime.fromisoformat(manifest["observed_at"])
        if observed.tzinfo is None:
            return None
        age = (now or datetime.now(timezone.utc)) - observed
        if age < timedelta(0) or age > timedelta(days=max_age_days):
            return None
        file_path = directory / "snapshots" / manifest["snapshot"]
        if file_path.is_symlink() or not stat.S_ISREG(file_path.stat().st_mode):
            return None
        payload = file_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            return None
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8"), newline=""))
        if reader.fieldnames != list(COLUMNS):
            return None
        rows = list(reader)
        if not rows or len(rows) != manifest.get("model_count"):
            return None
        ids = set()
        for row in rows:
            if not row["id"] or row["id"] in ids:
                return None
            ids.add(row["id"])
            for metric in ("intelligence", "coding", "agentic"):
                if row[metric]:
                    _numeric(float(row[metric]))
        return {"manifest": manifest, "models": rows, "source": "aa_cache"}
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, OverflowError):
        return None
