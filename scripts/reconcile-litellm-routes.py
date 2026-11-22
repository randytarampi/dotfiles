#!/usr/bin/env python3
"""Prove the running LiteLLM proxy serves this client's routes, or say so.

Cheap by design: ``GET /model/info`` answers from router state loaded at
startup and contacts no upstream provider, so this works air-gapped.

Exit codes:
  0  qualified, or no subscription route is requested
  3  not qualified — a repair is warranted, reason on stdout
  1  cannot prove anything (no client config yet, unusable inputs)

The caller performs any repair. This script never writes client state and never
restarts the service. See #361.
"""

import argparse
import json
import os
import sys

LIB_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from litellm_route_guard import (  # noqa: E402 -- scripts/lib bootstrap.
    NO_ROUTES_REQUESTED,
    QUALIFIED,
    assert_gateway_routes,
)

CONFIG_FILE = os.path.join(
    os.path.expanduser("~"), ".config", "opencode", "opencode.json"
)
KEY_FILE = os.path.join(
    os.path.expanduser("~"), ".local", "share", "litellm", "clients", "opencode.key"
)


def requested_gateway_models(config_file=CONFIG_FILE):
    """Return the model identities this client selects through the gateway.

    A missing file means no gateway-only client exists yet, which is nothing to
    enforce. An unreadable or malformed file is UNKNOWN and returns ``None``.
    """
    if not os.path.exists(config_file):
        return []
    try:
        with open(config_file, "r", encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, ValueError):
        return None
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return []
    gateway = providers.get("litellm")
    if not isinstance(gateway, dict):
        return []
    models = gateway.get("models")
    if not isinstance(models, dict):
        return []
    return list(models)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-file", default=CONFIG_FILE)
    parser.add_argument("--key-file", default=KEY_FILE)
    parser.add_argument(
        "--base-url",
        default=f"http://127.0.0.1:{os.environ.get('LITELLM_PORT', '4000')}/v1",
    )
    args = parser.parse_args(argv)

    model_refs = requested_gateway_models(args.config_file)
    if model_refs is None:
        print(f"cannot read client configuration: {args.config_file}")
        return 1
    if not model_refs:
        print("no gateway routes requested")
        return 0
    try:
        with open(args.key_file, "r", encoding="utf-8") as handle:
            api_key = handle.read().strip()
    except OSError as error:
        print(f"cannot read gateway client key: {error}")
        return 1
    if not api_key:
        print("gateway client key is empty")
        return 1

    ok, reason = assert_gateway_routes(
        os.environ, model_refs, base_url=args.base_url, api_key=api_key
    )
    if ok:
        if reason != QUALIFIED and reason != NO_ROUTES_REQUESTED:
            print(f"qualified: {reason}")
        return 0
    print(reason)
    return 3


if __name__ == "__main__":
    sys.exit(main())
