#!/usr/bin/env python3
"""Generate the gated, loopback-only LiteLLM proxy configuration."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger  # noqa: E402 (sys.path must be set up first)
from cli_helpers import add_common_args  # noqa: E402
from env import load_env  # noqa: E402
from litellm_config import (  # noqa: E402
    LiveCatalogueError,
    compute_model_list,
    write_config,
)

GATE_ENV = "DOTFILES_RUN_LITELLM_SETUP"


def _loopback_base():
    base = os.environ.get("LITELLM_BASE_URL", "") or (
        f"http://127.0.0.1:{os.environ.get('LITELLM_PORT', '4000')}"
    )
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("provision-key refuses a non-loopback LiteLLM URL")
    return base.rstrip("/")


def _service_values():
    values = {}
    path = os.path.expanduser("~/.local/share/litellm/service.env")
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("'\"")
    return values


def provision_key(client, preset):
    """Idempotently provision one client alias without persisting its key."""
    env = _service_values()
    master_key = (
        os.environ.get("LITELLM_MASTER_KEY", "").strip()
        or env.get("LITELLM_MASTER_KEY", "").strip()
    )
    if not master_key:
        raise ValueError("LITELLM_MASTER_KEY is required (service.env or environment)")
    alias = f"key_{client}_{preset}"
    base = _loopback_base()
    headers = {
        "Authorization": f"Bearer {master_key}",
        "Content-Type": "application/json",
    }

    def request(path, method="GET", payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request_obj = urllib.request.Request(
            f"{base}{path}", data=body, headers=headers, method=method
        )
        with urllib.request.urlopen(request_obj, timeout=10) as response:  # nosec B310
            return json.loads(response.read().decode() or "{}")

    listing = request("/key/list")
    keys = (
        listing.get("keys", listing.get("data", []))
        if isinstance(listing, dict)
        else []
    )
    existing = next(
        (
            item
            for item in keys
            if isinstance(item, dict) and item.get("key_alias") == alias
        ),
        None,
    )
    existing_key = existing.get("token") or existing.get("key") if existing else None
    if existing_key:
        request("/key/update", "POST", {"key": existing_key, "key_alias": alias})
        return "updated"
    request("/key/generate", "POST", {"key_alias": alias})
    return "created"


def main():
    parser = argparse.ArgumentParser(
        description="Configure the loopback LiteLLM gateway.", allow_abbrev=False
    )
    add_common_args(parser)
    parser.add_argument(
        "--provision-key", action="store_true", help="Provision a client virtual key"
    )
    parser.add_argument("--client", choices=["openwebui", "opencode", "pi"])
    parser.add_argument(
        "--preset", default=os.environ.get("DOTFILES_OPENCODE_TIER", "default")
    )
    args = parser.parse_args()
    if not load_env():
        logger.warning("~/.env not found")
    if os.environ.get(GATE_ENV, "0") != "1":
        logger.info("%s is not enabled; skipping LiteLLM setup", GATE_ENV)
        return 0
    if args.provision_key:
        if not args.client:
            parser.error("--provision-key requires --client")
        try:
            result = provision_key(args.client, args.preset)
            logger.info("LiteLLM key %s for %s/%s", result, args.client, args.preset)
            return 0
        except (
            OSError,
            ValueError,
            urllib.error.HTTPError,
            json.JSONDecodeError,
        ) as error:
            logger.error("LiteLLM key provisioning failed: %s", error)
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
