#!/usr/bin/env python3
"""Configure and reconcile the opt-in Open WebUI deployment."""

import argparse
import os
import shlex
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger
from cli_helpers import add_common_args
from env import load_env
from openwebui import (
    OpenWebUIClient,
    OpenWebUIError,
    compute_desired_state,
    emit_env,
    reconcile,
    reconcile_via_api,
)

GATE_ENV = "DOTFILES_RUN_OPENWEBUI_SETUP"


def _parser():
    parser = argparse.ArgumentParser(description="Configure Open WebUI connections.")
    add_common_args(parser)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--emit-env", action="store_true")
    mode.add_argument("--reconcile", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--reconcile-terminal", action="store_true")
    return parser


def _service_env_value(path, name):
    try:
        with open(path, encoding="utf-8") as env_file:
            for line in env_file:
                if line.startswith(f"{name}="):
                    return shlex.split(line.rstrip("\n").split("=", 1)[1])[0]
    except (OSError, ValueError, IndexError):
        return ""
    return ""


def _openwebui_service_value(name):
    return _service_env_value(
        os.path.expanduser("~/.local/share/openwebui/service.env"), name
    )


def _reconcile_terminal(client, dry_run=False):
    main_gate = os.environ.get("DOTFILES_RUN_OPENWEBUI_SETUP", "0") == "1"
    terminal_gate = os.environ.get("DOTFILES_RUN_OPENWEBUI_TERMINAL_SETUP", "0") == "1"
    want_terminal = main_gate and terminal_gate
    service_env = os.path.expanduser("~/.local/share/openwebui/terminal.env")
    response = client.get_terminal_servers_config()
    if not isinstance(response, dict):
        raise OpenWebUIError("Open WebUI terminal configuration has an invalid shape")
    current = response.get("TERMINAL_SERVER_CONNECTIONS", [])
    if not isinstance(current, list) or not all(
        isinstance(item, dict) for item in current
    ):
        raise OpenWebUIError("Open WebUI terminal configuration has an invalid shape")
    port = os.environ.get("OPENWEBUI_TERMINAL_PORT", "8123")
    managed_id = "dotfiles-open-terminal"
    expected_url = f"http://127.0.0.1:{port}"
    if want_terminal:
        # The collision check guards ADOPTION/UPDATE: a same-id entry with an
        # unexpected endpoint/auth shape is never silently rewritten. On the
        # desired-ABSENCE path (want_terminal False) a stale same-id entry is
        # exactly what we are removing, so it must remain removable.
        for item in current:
            if item.get("id") == managed_id and (
                item.get("url") != expected_url or item.get("auth_type") != "bearer"
            ):
                raise OpenWebUIError(
                    "Open Terminal registration collision; refusing to adopt existing entry"
                )
    desired = None
    if want_terminal:
        terminal_key = _service_env_value(service_env, "OPEN_TERMINAL_API_KEY")
        if not terminal_key:
            logger.warning("Open Terminal service key is absent; skipping registration")
            return 0
        desired = {
            "id": managed_id,
            "name": "Open Terminal",
            "enabled": True,
            "url": expected_url,
            "path": "/openapi.json",
            "key": terminal_key,
            "auth_type": "bearer",
            "forward_cookies": False,
            "config": None,
        }
    merged = [item for item in current if item.get("id") != managed_id]
    if desired is not None:
        merged.append(desired)

    def _managed_fields(entry):
        return {
            name: entry.get(name)
            for name in (
                "id",
                "name",
                "enabled",
                "url",
                "path",
                "key",
                "auth_type",
                "forward_cookies",
                "config",
            )
        }

    def _by_id(entries):
        return {entry.get("id"): entry for entry in entries}

    # Server may reorder or normalize stored entries; compare managed fields
    # id-keyed (order-insensitive) instead of whole-list equality.
    if _by_id([_managed_fields(item) for item in merged]) == _by_id(
        [_managed_fields(item) for item in current]
    ):
        logger.info("Open Terminal registration is clean")
        return 0
    logger.info("Open Terminal registration requires an update (key masked)")
    if dry_run:
        return 0
    latest_response = client.get_terminal_servers_config()
    if (
        not isinstance(latest_response, dict)
        or latest_response.get("TERMINAL_SERVER_CONNECTIONS") != current
    ):
        raise OpenWebUIError("Open WebUI terminal configuration changed before write")
    payload = {"TERMINAL_SERVER_CONNECTIONS": merged}
    client.update_terminal_servers_config(payload)
    verified_response = client.get_terminal_servers_config()
    if not isinstance(verified_response, dict):
        raise OpenWebUIError("Open WebUI terminal verification has an invalid shape")
    verified = verified_response.get("TERMINAL_SERVER_CONNECTIONS")
    if not isinstance(verified, list) or not all(
        isinstance(item, dict) for item in verified
    ):
        raise OpenWebUIError("Open WebUI terminal verification has an invalid shape")
    unmanaged = [item for item in current if item.get("id") != managed_id]
    verified_unmanaged = [item for item in verified if item.get("id") != managed_id]
    managed_verified = [
        _managed_fields(item) for item in verified if item.get("id") == managed_id
    ]
    expected_managed = [_managed_fields(desired)] if desired is not None else []
    if verified_unmanaged != unmanaged or (
        _by_id(managed_verified) != _by_id(expected_managed)
    ):
        raise OpenWebUIError("Open Terminal registration write verification failed")
    logger.info("Open Terminal registered through the Open WebUI admin API")
    return 0


def main():
    args = _parser().parse_args()
    terminal_gate_override = os.environ.get("DOTFILES_RUN_OPENWEBUI_TERMINAL_SETUP")
    if not load_env():
        logger.warning("~/.env not found")
    if args.reconcile_terminal and terminal_gate_override is not None:
        os.environ["DOTFILES_RUN_OPENWEBUI_TERMINAL_SETUP"] = terminal_gate_override
    if os.environ.get(GATE_ENV, "0") != "1" and not args.reconcile_terminal:
        logger.info("%s is not enabled; skipping Open WebUI setup", GATE_ENV)
        return 0

    if args.reconcile_terminal:
        api_key = os.environ.get(
            "OPENWEBUI_API_KEY", ""
        ).strip() or _openwebui_service_value("OPENWEBUI_API_KEY")
        if not api_key:
            logger.warning(
                "OPENWEBUI_API_KEY is absent; skipping terminal registration"
            )
            return 0
        base_url = f"http://127.0.0.1:{os.environ.get('OPENWEBUI_PORT', '8080')}"
        client = OpenWebUIClient(
            base_url,
            api_key,
            admin_credentials={
                "email": os.environ.get("WEBUI_ADMIN_EMAIL", "")
                or _openwebui_service_value("WEBUI_ADMIN_EMAIL"),
                "password": os.environ.get("WEBUI_ADMIN_PASSWORD", "")
                or _openwebui_service_value("WEBUI_ADMIN_PASSWORD"),
            },
        )
        if not client.health_check():
            logger.warning("Open WebUI is not healthy; skipping terminal registration")
            return 0
        try:
            return _reconcile_terminal(client, args.dry_run)
        except OpenWebUIError as error:
            logger.error("Open Terminal registration failed: %s", error)
            return 1

    desired = compute_desired_state()
    if args.emit_env:
        logger.info("%s", emit_env(desired, masked=True))
        logger.info("WEBUI_SECRET_KEY is supplied by the LaunchAgent environment")
        return 0

    base_url = f"http://127.0.0.1:{os.environ.get('OPENWEBUI_PORT', '8080')}"
    if not (args.reconcile or args.check):
        args.reconcile = True
    api_key = os.environ.get(
        "OPENWEBUI_API_KEY", ""
    ).strip() or _openwebui_service_value("OPENWEBUI_API_KEY")
    if not api_key:
        logger.warning("OPENWEBUI_API_KEY is absent; skipping Open WebUI API operation")
        return 0
    client = OpenWebUIClient(
        base_url,
        api_key,
        admin_credentials={
            "email": os.environ.get("WEBUI_ADMIN_EMAIL", "")
            or _openwebui_service_value("WEBUI_ADMIN_EMAIL"),
            "password": os.environ.get("WEBUI_ADMIN_PASSWORD", "")
            or _openwebui_service_value("WEBUI_ADMIN_PASSWORD"),
        },
    )
    if not client.health_check():
        logger.warning("Open WebUI is not healthy at %s; skipping", base_url)
        return 0
    try:
        if args.check or args.dry_run:
            result = reconcile(
                client.get_openai_config(), client.get_ollama_config(), desired
            )
            logger.info(result.summary())
            drift = result.status != "clean" or any(
                item["action"] != "keep" for item in result.plan.entries
            )
            if args.dry_run:
                return 1 if result.status == "collision" else 0
            return 1 if drift else 0
        result = reconcile_via_api(client, desired)
        logger.info(result.summary())
        return 0
    except OpenWebUIError as error:
        logger.error("Open WebUI operation failed: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
