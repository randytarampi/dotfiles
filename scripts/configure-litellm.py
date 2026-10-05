#!/usr/bin/env python3
"""Generate the gated, loopback-only LiteLLM proxy configuration."""

import argparse
import os
import json
import shlex
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger  # noqa: E402 (sys.path must be set up first)
from cli_helpers import add_common_args  # noqa: E402
from env import load_env  # noqa: E402
from litellm_config import (  # noqa: E402
    APP_KEYS,
    LiveCatalogueError,
    compute_model_list,
    write_config,
)

GATE_ENV = "DOTFILES_RUN_LITELLM_SETUP"


def _service_env_value(name, path):
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                values = shlex.split(line.split("=", 1)[1])
                return values[0] if values else ""
    except (OSError, ValueError):
        pass
    return ""


def _database_url_configured():
    return bool(
        os.environ.get("LITELLM_DATABASE_URL", "").strip()
        or os.environ.get("DATABASE_URL", "").strip()
    )


def _request_json(url, method, master_key, payload=None, timeout=5):
    parsed_url = urllib.parse.urlsplit(url)
    if parsed_url.scheme != "http" or parsed_url.hostname != "127.0.0.1":
        raise ValueError(
            "LiteLLM provisioning requests are restricted to loopback HTTP"
        )
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {master_key}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    # B310: URL scheme and host are constrained above to loopback HTTP.
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        return json.loads(response.read().decode("utf-8"))


def _persist_app_keys(path: Path, keys: dict[str, str]) -> None:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                name, value = line.split("=", 1)
                values[name] = value
    values.update({name: shlex.quote(value) for name, value in keys.items()})
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.writelines(f"{name}={value}\n" for name, value in values.items())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _write_client_key(service_env_path: Path, client, env_name, dry_run=False) -> None:
    """Materialize a per-client app key without sourcing service.env."""
    key = _service_env_value(env_name, service_env_path)
    if not key:
        return
    directory = service_env_path.parent
    target_dir = directory / "clients"
    target = target_dir / f"{client}.key"
    try:
        if (
            service_env_path.is_symlink()
            or directory.is_symlink()
            or target_dir.is_symlink()
            or target.is_symlink()
        ):
            raise OSError("unsafe symlink in LiteLLM client-key path")
        if dry_run:
            return
        directory.mkdir(parents=True, exist_ok=True)
        target_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(target_dir, 0o700)
        if target.exists() and target.read_text(encoding="utf-8") == key:
            os.chmod(target, 0o600)
            return
        fd, temp_path = tempfile.mkstemp(dir=target_dir, prefix=f".{client}.key.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(key)
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, target)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
    except OSError as error:
        logger.warning("Could not safely write %s LiteLLM key file: %s", client, error)


def _write_opencode_key(service_env_path: Path, dry_run=False) -> None:
    """Materialize the OpenCode app key without sourcing service.env."""
    _write_client_key(service_env_path, "opencode", "LITELLM_OPENCODE_KEY", dry_run)


def _write_pi_key(service_env_path: Path, dry_run=False) -> None:
    """Materialize the Pi app key without sourcing service.env."""
    _write_client_key(service_env_path, "pi", "LITELLM_PI_KEY", dry_run)


def _write_junie_key(service_env_path: Path, dry_run=False) -> None:
    """Materialize the Junie app key without sourcing service.env."""
    _write_client_key(service_env_path, "junie", "LITELLM_JUNIE_KEY", dry_run)


def provision_app_keys(master_key, service_env_path=None, api_base=None):
    """Idempotently provision per-client virtual keys after LiteLLM is healthy."""
    if not master_key:
        logger.warning("LiteLLM master key unavailable; skipping app-key provisioning")
        return
    api_base = (
        api_base or f"http://127.0.0.1:{os.environ.get('LITELLM_PORT', '4000')}"
    ).rstrip("/")
    service_env_path = Path(
        service_env_path or "~/.local/share/litellm/service.env"
    ).expanduser()
    try:
        _request_json(f"{api_base}/health/liveliness", "GET", master_key)
        # Default /key/list responses carry opaque hash strings; only
        # return_full_object=true exposes key_alias for idempotency.
        listing = _request_json(
            f"{api_base}/key/list?return_full_object=true", "GET", master_key
        )
        records = listing.get("keys", []) if isinstance(listing, dict) else []
        aliases = {
            item.get("key_alias")
            for item in records
            if isinstance(item, dict) and item.get("key_alias")
        }
        found = {}
        for alias, env_name in APP_KEYS.items():
            if alias in aliases:
                # The key-list endpoint intentionally does not reveal existing
                # key material; preserve any locally stored key on disk.
                continue
            try:
                generated = _request_json(
                    f"{api_base}/key/generate",
                    "POST",
                    master_key,
                    {"key_alias": alias, "duration": None},
                )
            except OSError as error:
                # Some proxy versions reject duplicate aliases with 400 even
                # when the list response hides key_alias — treat those as
                # already-provisioned and keep the loop idempotent.
                status = getattr(error, "code", None)
                if status == 400:
                    logger.info("LiteLLM key alias %s already exists; skipping", alias)
                    continue
                raise
            key = generated.get("key") if isinstance(generated, dict) else None
            if key:
                found[env_name] = key
                aliases.add(alias)
            else:
                logger.warning("LiteLLM did not return a key for alias %s", alias)
        if found:
            _persist_app_keys(service_env_path, found)
            logger.info("Provisioned %d LiteLLM app key(s)", len(found))
        _write_opencode_key(service_env_path)
        _write_pi_key(service_env_path)
        _write_junie_key(service_env_path)
    except (OSError, ValueError) as error:
        logger.warning("LiteLLM app-key provisioning deferred: %s", error)


def main():
    parser = argparse.ArgumentParser(
        description="Configure the loopback LiteLLM gateway.", allow_abbrev=False
    )
    add_common_args(parser)
    args = parser.parse_args()
    if not load_env():
        logger.warning("~/.env not found")
    if os.environ.get(GATE_ENV, "0") != "1":
        logger.info("%s is not enabled; skipping LiteLLM setup", GATE_ENV)
        return 0
    if not _database_url_configured():
        logger.error("LITELLM_DATABASE_URL is required when LiteLLM is enabled")
        return 1
    config_path = os.path.expanduser("~/.local/share/litellm/config.yaml")
    try:
        # Compute the snapshot once: write_config reuses it instead of
        # re-enumerating provider catalogues a second time per deploy.
        entries = compute_model_list()
        if args.dry_run:
            master_key_set = bool(os.environ.get("LITELLM_MASTER_KEY", "").strip())
            logger.info(
                "LiteLLM dry-run: models=%d master_key_set=%s config=%s",
                len(entries),
                master_key_set,
                config_path,
            )
            return 0
        changed = write_config(config_path, entries=entries)
        logger.info(
            "LiteLLM config %s (%d model entries)",
            "updated" if changed else "unchanged",
            len(entries),
        )
        service_env_path = Path("~/.local/share/litellm/service.env").expanduser()
        master_key = os.environ.get("LITELLM_MASTER_KEY", "").strip()
        if not master_key:
            master_key = _service_env_value("LITELLM_MASTER_KEY", service_env_path)
        provision_app_keys(master_key, service_env_path)
        return 0
    except LiveCatalogueError as error:
        logger.error(
            "LiteLLM live catalogue enumeration failed; keeping existing config: %s",
            error,
        )
        return 1
    except Exception as error:
        logger.error("LiteLLM configuration failed: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
