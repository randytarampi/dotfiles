"""Track when model assignments were last synchronized."""

from __future__ import annotations

import os
import time
from pathlib import Path

STAMP_PATH = Path.home() / ".local" / "share" / "dotfiles" / "model-sync-stamp"


def read_stamp() -> float | None:
    """Return the recorded epoch timestamp, or None if it is invalid."""
    try:
        value = float(STAMP_PATH.read_text(encoding="utf-8").strip())
        return value if value >= 0 else None
    except (OSError, ValueError):
        return None


def write_stamp() -> None:
    """Atomically replace the synchronization timestamp."""
    STAMP_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STAMP_PATH.with_name(f".{STAMP_PATH.name}.{os.getpid()}.tmp")
    tmp_path.write_text(f"{time.time():.6f}\n", encoding="utf-8")
    os.replace(tmp_path, STAMP_PATH)


def is_stale(max_age_days: int = 14) -> tuple[bool, float | None]:
    """Return whether the stamp is missing or older than max_age_days."""
    stamp = read_stamp()
    if stamp is None:
        return True, None
    age_days = max(0.0, (time.time() - stamp) / 86400)
    return age_days > max_age_days, age_days


def notice_message() -> str | None:
    """Return a deploy reminder when model assignments have gone stale."""
    stale, age = is_stale()
    if not stale:
        return None
    if age is None:
        return "NOTE: model assignments never synced — run configure scripts after reviewing docs/MODEL_UPDATES.md"
    return f"NOTE: model assignments last synced {age:.0f} days ago — consider re-running configure scripts (docs/MODEL_UPDATES.md)"
