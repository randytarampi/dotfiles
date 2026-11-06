"""Credential-safe, owner-gated Paseo config edits with native validation.

Paseo's config-set CLI accepts values only as argv, so whole provider maps
containing user keys must instead be staged privately and validated with
`daemon config get` before an atomic live file edit and `daemon reload`.
This does not solve cross-process lost updates (issue #173): quiesce writers.
"""

import json
import os
from pathlib import Path
import stat
import subprocess  # nosec B404 - fixed Paseo CLI, never passes secret values.
import tempfile


def _run(paseo, operation, home, timeout):
    """Run a native read/refresh command without secret-bearing argv or output."""
    return subprocess.run(  # nosec B603 - fixed native command, absolute CLI.
        [paseo, "daemon", *operation, "--home", str(home)],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def apply_document(
    paseo, home, source, original, expected, decode_config, same_config, *, timeout=30
):
    """Validate a private candidate, atomically save, reload and verify readback.

    Any reload refusal remains a failure even when the new file is already
    saved. Never restore a stale snapshot or print native output, which may
    contain credentials. Caller must ensure other config writers are quiesced.
    """
    home, source = Path(home), Path(source)
    if source.is_symlink():
        raise ValueError("refusing symlink Paseo config")
    before_stat = source.stat()
    if not stat.S_ISREG(before_stat.st_mode) or before_stat.st_nlink != 1:
        raise ValueError("Paseo config must be a single regular file")
    if stat.S_IMODE(before_stat.st_mode) & 0o077:
        raise ValueError("Paseo config must be private")
    before = source.read_bytes()
    if not same_config(decode_config(before.decode("utf-8")), original):
        raise ValueError("Paseo config changed before preflight")

    payload = (
        json.dumps(expected, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")
    with tempfile.TemporaryDirectory(prefix="dotfiles-paseo-preflight-") as scratch:
        staged_home = Path(scratch)
        staged_file = staged_home / "config.json"
        staged_file.write_bytes(payload)
        staged_file.chmod(0o600)
        try:
            check = _run(paseo, ["config", "get"], staged_home, timeout)
        except subprocess.TimeoutExpired:
            raise ValueError(
                "Paseo staged config validation timed out; no live write"
            ) from None
        if check.returncode:
            raise ValueError(
                f"Paseo staged config validation failed (exit {check.returncode}); "
                "native output suppressed; no live write"
            )
        if not same_config(
            decode_config(staged_file.read_text(encoding="utf-8")), expected
        ):
            raise ValueError("Paseo staged config changed unexpectedly; no live write")

    current_stat = source.stat()
    if (
        source.is_symlink()
        or current_stat.st_ino != before_stat.st_ino
        or current_stat.st_dev != before_stat.st_dev
        or source.read_bytes() != before
    ):
        raise ValueError("Paseo config changed during preflight; no live write")
    fd, temporary = tempfile.mkstemp(prefix=".config.json-dotfiles-", dir=home)
    try:
        os.fchmod(fd, stat.S_IMODE(before_stat.st_mode))
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, source)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    if not same_config(decode_config(source.read_text(encoding="utf-8")), expected):
        raise ValueError(
            "Paseo saved config differs from candidate; no rollback attempted"
        )
    try:
        result = _run(paseo, ["reload"], home, timeout)
    except subprocess.TimeoutExpired:
        raise ValueError(
            "Paseo config saved; daemon reload timed out (status UNKNOWN); "
            "no rollback attempted"
        ) from None
    if not same_config(decode_config(source.read_text(encoding="utf-8")), expected):
        raise ValueError("Paseo config changed during reload; no rollback attempted")
    if result.returncode:
        raise ValueError(
            f"Paseo config saved; daemon reload failed (exit {result.returncode}); "
            "native output suppressed; no rollback attempted"
        )
