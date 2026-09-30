"""Best-effort process lock for configuration generators."""

from __future__ import annotations

import functools
import os
import sys
import time
from pathlib import Path

try:
    import fcntl as _fcntl

    _LOCK_BACKEND = "fcntl"
except ImportError:  # pragma: no cover - exercised on Windows CI
    try:
        import msvcrt as _msvcrt

        _LOCK_BACKEND = "msvcrt"
    except ImportError as error:  # pragma: no cover
        raise RuntimeError("No portable file-lock backend is available") from error

import logger


def with_generation_lock(name: str):
    lock_path = Path.home() / ".cache" / "dotfiles" / "generation.lock"

    def decorate(function):
        @functools.wraps(function)
        def locked(*args, **kwargs):
            if "--help" in sys.argv or "--dry-run" in sys.argv:
                return function(*args, **kwargs)
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            source_root = Path(function.__globals__["SCRIPT_DIR"]).resolve().parent
            with lock_path.open("a+", encoding="utf-8") as handle:
                try:
                    handle.seek(0)
                    if _LOCK_BACKEND == "fcntl":
                        _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                    else:
                        _msvcrt.locking(handle.fileno(), _msvcrt.LK_NBLCK, 1)
                except (BlockingIOError, OSError):
                    logger.critical(
                        "Generation lock %s is held by another process (pid unknown); refusing concurrent writes.",
                        lock_path,
                    )
                    return 1
                try:
                    handle.seek(0)
                    previous = handle.read()
                    for line in previous.splitlines():
                        if (
                            line.startswith("source_root=")
                            and line != f"source_root={source_root}"
                        ):
                            logger.critical(
                                "Generation denied: another checkout last wrote deployed state (%s). "
                                "Re-run from that source root or reconcile it first.",
                                line.removeprefix("source_root="),
                            )
                            return 1
                    result = function(*args, **kwargs)
                    if result in (None, 0):
                        handle.seek(0)
                        handle.truncate()
                        handle.write(
                            f"pid={os.getpid()}\nsource_root={source_root}\n"
                            f"timestamp={time.time()}\n"
                        )
                        handle.flush()
                    return result
                finally:
                    handle.seek(0)
                    if _LOCK_BACKEND == "fcntl":
                        _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
                    else:
                        _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)

        return locked

    return decorate
