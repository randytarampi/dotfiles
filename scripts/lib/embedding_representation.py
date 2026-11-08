"""Pure, fail-closed H1 comparison of known embedding representations.

Not an index migrator. No database, file, gateway, or network access.
"""

import re

_SHA256 = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z")

REQUIRED_FIELDS = (
    "engine",
    "model",
    "revision",
    "digest",
    "dimensions",
    "pooling",
    "normalization",
    "task_prefix",
    "template",
    "fixed_vector_sha256",
)


def _valid(value, key):
    if key == "dimensions":
        return type(value) is int and value > 0
    if key in ("digest", "fixed_vector_sha256"):
        return isinstance(value, str) and bool(_SHA256.fullmatch(value))
    if key in ("task_prefix", "template"):
        # Explicit empty values differ from missing/unknown settings.
        return isinstance(value, str)
    return isinstance(value, str) and bool(value.strip())


def compare_representations(expected, observed):
    """Report field names only, never vector values or sensitive index contents.

    A matching dimension or model name cannot establish representation parity.
    """
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        return {
            "status": "UNKNOWN",
            "unknown_fields": list(REQUIRED_FIELDS),
            "different_fields": [],
        }
    unknown = sorted(
        field
        for field in REQUIRED_FIELDS
        if not _valid(expected.get(field), field)
        or not _valid(observed.get(field), field)
    )
    if unknown:
        return {
            "status": "UNKNOWN",
            "unknown_fields": unknown,
            "different_fields": [],
        }
    different = sorted(
        field for field in REQUIRED_FIELDS if expected[field] != observed[field]
    )
    return {
        "status": "MISMATCH" if different else "MATCH",
        "unknown_fields": [],
        "different_fields": different,
    }
