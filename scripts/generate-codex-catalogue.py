#!/usr/bin/env python3
"""Owner-run Codex model catalogue from exact LiteLLM-served aliases.

This CLI never changes the Codex default provider, Ollama launch files, or
private API keys. A served model is only a *candidate*, not Responses-qualified.
"""

import argparse
import http.client
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from urllib.parse import urlsplit

MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+/-]{0,199}\Z")
MANAGED = "dotfiles-litellm-codex-catalogue-v1"


def validate_url(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("localhost", "127.0.0.1")
        or parsed.port is None
        or parsed.port < 1
        or parsed.port > 65535
        or parsed.path != "/v1/models"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("model inventory endpoint must be loopback /v1/models")
    return url


def read_key(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Codex scoped LiteLLM key file missing or unsafe")
    info = path.stat()
    if (
        info.st_uid != os.getuid()
        or info.st_nlink != 1
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ValueError("Codex key must be private, owner-owned mode 0600")
    value = path.read_text(encoding="utf-8").strip()
    if not value or any(char.isspace() for char in value):
        raise ValueError("Codex key is malformed")
    return value


def fetch_served(url, token):
    """Connect only to IPv4 loopback and never follow HTTP redirects with a key."""
    parsed = urlsplit(validate_url(url))
    # localhost is accepted as a user-facing alias, but the socket always
    # connects to literal 127.0.0.1 (not DNS or a redirect target).
    connection = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=8)
    try:
        connection.request(
            "GET", "/v1/models", headers={"Authorization": "Bearer " + token}
        )
        response = connection.getresponse()
        if response.status != 200:
            # 3xx is refused, not followed; never send a scoped key elsewhere.
            raise ValueError("LiteLLM model inventory unavailable or key denied")
        data = response.read(1048577)
    except (OSError, http.client.HTTPException):
        # Never expose low-level request details or credentials.
        raise ValueError("LiteLLM model inventory unavailable or key denied") from None
    finally:
        connection.close()
    if len(data) > 1048576:
        raise ValueError("LiteLLM model inventory exceeds size limit")
    try:
        return json.loads(data)
    except ValueError:
        raise ValueError("LiteLLM model inventory is not valid JSON") from None


def generate_catalogue(payload, model_ids, context_window):
    """Use exact served IDs; never derive slugs from a local Ollama name."""
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("data"), list)
        or not payload["data"]
    ):
        raise ValueError("missing served LiteLLM model inventory")
    if not isinstance(model_ids, (list, tuple)) or not model_ids or len(model_ids) > 20:
        raise ValueError("specify one to twenty exact model IDs")
    if (
        not isinstance(context_window, int)
        or isinstance(context_window, bool)
        or not (8192 <= context_window <= 1048576)
    ):
        raise ValueError("set an explicit qualified context-window budget")
    served = set()
    for row in payload["data"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError("malformed served LiteLLM model row")
        served.add(row["id"])
    if len(set(model_ids)) != len(model_ids):
        raise ValueError("duplicate requested model IDs")
    for model_id in model_ids:
        if not isinstance(model_id, str) or not MODEL_RE.fullmatch(model_id):
            raise ValueError("unsafe model ID")
        if model_id not in served:
            raise ValueError("requested model ID is not in the served catalogue")
    return {
        "_dotfiles_managed": MANAGED,
        "models": [
            {
                "slug": model_id,
                "display_name": model_id,
                "description": "LiteLLM served candidate; Responses acceptance not run",
                "visibility": "list",
                "supported_in_api": True,
                "priority": 50 + index,
                "context_window": context_window,
                "max_context_window": context_window,
                "input_modalities": ["text"],
                "supported_reasoning_levels": [],
                "default_reasoning_level": None,
                "supports_parallel_tool_calls": False,
                "supports_reasoning_summaries": False,
                "shell_type": "unified_exec",
            }
            for index, model_id in enumerate(model_ids)
        ],
    }


def _create_catalogue_without_clobber(path, text):
    """Publish complete bytes atomically and never expose a partial new file."""
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".dotfiles-codex-")
    try:
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(text.encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        # Same-directory hard link is atomic and fails if another actor has
        # created the target. Unlike os.replace, it cannot clobber that actor.
        os.link(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_catalogue(path, text, *, replace):
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("refusing symlinked Codex catalogue path")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o022:
        raise ValueError("Codex catalogue directory is not privately owned")
    if path.exists():
        info = path.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("existing Codex catalogue file has unsafe ownership")
        old = path.read_bytes()
        if old == text.encode("utf-8"):
            return False
        try:
            managed = json.loads(old).get("_dotfiles_managed") == MANAGED
        except (ValueError, AttributeError):
            managed = False
        if not managed or not replace:
            raise ValueError(
                "existing catalogue differs; explicitly allow managed replacement"
            )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = path.with_name(path.name + "." + stamp + ".bak")
        fd = os.open(str(backup), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(old)
            output.flush()
            os.fsync(output.fileno())
        if path.read_bytes() != old:
            raise ValueError("catalogue changed during backup; refusing replacement")
        fd, temporary = tempfile.mkstemp(
            dir=str(path.parent), prefix=".dotfiles-codex-"
        )
        try:
            with os.fdopen(fd, "wb") as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(text.encode("utf-8"))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return True
    _create_catalogue_without_clobber(path, text)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", required=True, dest="models")
    parser.add_argument("--context-window", type=int, required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:4000/v1/models")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.replace and not args.apply:
            raise ValueError("--replace requires --apply")
        url = validate_url(args.endpoint)
        key_path = Path.home() / ".local/share/litellm/clients/codex.key"
        data = fetch_served(url, read_key(key_path))
        result = generate_catalogue(data, args.models, args.context_window)
        if args.apply:
            if (
                os.environ.get("DOTFILES_RUN_CODEX_SETUP") != "1"
                or os.environ.get("DOTFILES_USE_LITELLM_PROXY") != "1"
            ):
                raise ValueError("Codex and LiteLLM setup gates must both be enabled")
            output = Path.home() / ".codex/dotfiles-litellm-models.json"
            serialized = json.dumps(result, indent=2) + "\n"
            changed = save_catalogue(output, serialized, replace=args.replace)
            print("Managed Codex catalogue " + ("updated" if changed else "unchanged"))
        else:
            print("Read-only catalogue preview: " + ", ".join(args.models))
            print("No Codex configuration or catalogue file changed")
        return 0
    except (ValueError, OSError) as exc:
        print("Codex catalogue generation refused: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
