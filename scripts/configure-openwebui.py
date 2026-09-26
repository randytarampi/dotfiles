#!/usr/bin/env python3
"""Configure and reconcile the opt-in Open WebUI deployment."""

import argparse
import importlib.util
import json
import os
import re
import shlex
import sys
from pathlib import Path
from urllib.parse import urlsplit

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

# configure-mcp-tool.py (the canonical MCP lane) lives in scripts/ with a
# hyphenated filename, so it cannot be imported by module name — load it by
# path and reuse its registry parsing + env-var resolution instead of a
# second implementation.
_mcp_tool_spec = importlib.util.spec_from_file_location(
    "configure_mcp_tool", os.path.join(SCRIPT_DIR, "configure-mcp-tool.py")
)
_mcp_tool = importlib.util.module_from_spec(_mcp_tool_spec)
_mcp_tool_spec.loader.exec_module(_mcp_tool)
resolve_env_vars = _mcp_tool.resolve_env_vars

import logger  # noqa: E402 (sys.path must be set up first)
from cli_helpers import add_common_args  # noqa: E402
from env import load_env  # noqa: E402
from openwebui import (  # noqa: E402
    OpenWebUIClient,
    OpenWebUIError,
    compute_desired_state,
    emit_env,
    reconcile,
    reconcile_via_api,
)
from provider_endpoints import (  # noqa: E402
    PROVIDER_ENDPOINTS,
    provider_models,
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
    mode.add_argument("--reconcile-mcp", action="store_true")
    mode.add_argument("--reconcile-catalogue", action="store_true")
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


def _admin_credentials():
    return {
        "email": os.environ.get("WEBUI_ADMIN_EMAIL", "")
        or _openwebui_service_value("WEBUI_ADMIN_EMAIL"),
        "password": os.environ.get("WEBUI_ADMIN_PASSWORD", "")
        or _openwebui_service_value("WEBUI_ADMIN_PASSWORD"),
    }


def _streamable_mcp_connections(repo_root=None):
    """MCP inventory from the canonical registry (configs/mcp/global-mcps.json
    -> tools.<tool>.mcp_servers[].template), NOT a directory glob — a JSON
    file dropped into configs/mcp/ for any other purpose must not silently
    acquire prompt-driven tool-execution capability in the chat UI.

    Only Streamable-HTTP servers qualify (type: url, http/https). Header
    credentials resolve through the shared resolve_env_vars helper
    (configure-mcp-tool.py); servers whose required ${VAR} credentials are
    absent from ~/.env are skipped (name-only warning)."""
    base = Path(repo_root) if repo_root else Path(SCRIPT_DIR).parent
    registry_path = base / "configs" / "mcp" / "global-mcps.json"
    templates_dir = base / "configs" / "mcp"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        logger.warning("MCP registry unreadable (%s): %s", registry_path, error)
        return []

    # Collect distinct template names across every tool's mcp_servers entries.
    template_names = []
    for tool_config in (registry.get("tools") or {}).values():
        for server in tool_config.get("mcp_servers") or []:
            template = server.get("template")
            if template and template not in template_names:
                template_names.append(template)

    connections = []
    for template_name in template_names:
        tpl_path = templates_dir / f"{template_name}.json"
        if not tpl_path.is_file():
            logger.warning("MCP template not found: %s", tpl_path)
            continue
        try:
            data = json.loads(tpl_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("MCP template unreadable (%s): %s", tpl_path, error)
            continue
        url = data.get("url")
        if (
            data.get("type") != "url"
            or not isinstance(url, str)
            or urlsplit(url).scheme not in {"http", "https"}
        ):
            continue
        parsed = urlsplit(url)
        headers = data.get("headers") or {}
        required = re.findall(r"\$\{(\w+)\}", json.dumps(headers))
        missing = [v for v in required if not os.environ.get(v, "").strip()]
        if missing:
            logger.warning(
                "Skipping MCP server with unresolved credentials: %s",
                data.get("name", template_name),
            )
            continue
        # Resolved credentials persist server-side in Open WebUI's config-table DB.
        resolved_headers = {
            key: resolve_env_vars(str(value)) for key, value in headers.items()
        }
        connections.append(
            {
                "url": f"{parsed.scheme}://{parsed.netloc}",
                "path": parsed.path or "/",
                "type": "mcp",
                "auth_type": ("bearer" if headers.get("Authorization") else "none"),
                "forward_cookies": False,
                "headers": resolved_headers or None,
                "key": None,
                # Upstream 0.11.4 lifespan does `'access_control' in c.get('config',
                # {})` over tool_server.connections — an EXPLICIT null defeats the
                # .get() default and crashes startup. Always write an empty mapping.
                "config": {},
                "info": {"id": f"dotfiles-mcp-{data.get('name', template_name)}"},
            }
        )
    return connections


def _mcp_state_path(migrate=True):
    path = Path(os.path.expanduser("~/.local/share/openwebui/data/managed-mcp.json"))
    old = path.parent.parent / "managed-mcp.json"
    # Legacy-state migration is a filesystem WRITE: skip it during dry-run.
    if migrate and old.is_file() and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(old, path)
    return path


def _write_mcp_state(connections):
    path = _mcp_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps({"connections": connections}, indent=2) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == rendered:
        return
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(rendered, encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def _reconcile_mcp(client, dry_run=False):
    response = client.get_tool_servers_config()
    if not isinstance(response, dict):
        raise OpenWebUIError("Open WebUI MCP configuration has an invalid shape")
    current = response.get("TOOL_SERVER_CONNECTIONS", [])
    if not isinstance(current, list) or not all(
        isinstance(item, dict) for item in current
    ):
        raise OpenWebUIError("Open WebUI MCP configuration has an invalid shape")
    desired = _streamable_mcp_connections()
    desired_by_id = {item["info"]["id"]: item for item in desired}
    state_path = _mcp_state_path(migrate=not dry_run)
    if state_path.is_file():
        owned = json.loads(state_path.read_text(encoding="utf-8")).get(
            "connections", {}
        )
    else:
        # Conservative first-run seeding: claim ONLY current entries whose
        # url matches the desired payload (what the reconciler is about to
        # deploy). A same-ID entry with a different url is NOT owned — it is
        # an unmanaged collision, never silently adopted.
        owned = {
            (item.get("info") or {}).get("id"): item.get("url")
            for item in current
            if (item.get("info") or {}).get("id", "").startswith("dotfiles-mcp-")
            and (item.get("info") or {}).get("id") in desired_by_id
            and item.get("url")
            == desired_by_id[(item.get("info") or {}).get("id")]["url"]
        }
    managed_ids = set(owned) | set(desired_by_id)
    for item in current:
        item_id = (item.get("info") or {}).get("id")
        if item_id in owned and item.get("url") != owned[item_id]:
            raise OpenWebUIError(f"MCP registration collision for {item_id}")
        if item_id in desired_by_id and item_id not in owned:
            raise OpenWebUIError(f"MCP registration collision for unmanaged {item_id}")
        # Owned entries (id in state AND current url matches the recorded url)
        # may UPDATE to a new desired endpoint/path — legitimate migrations.
        # Collision handling above is reserved for unowned same-ID entries and
        # ownership-identity drift.
    merged = [
        item
        for item in current
        if (item.get("info") or {}).get("id") not in managed_ids
    ] + desired

    def managed_fields(item):
        return {
            key: item.get(key)
            for key in (
                "url",
                "path",
                "type",
                "auth_type",
                "headers",
                "key",
                "config",
                "info",
            )
        }

    current_projection = {
        (item.get("info") or {}).get("id"): managed_fields(item)
        for item in current
        if (item.get("info") or {}).get("id") in managed_ids
    }
    merged_projection = {
        (item.get("info") or {}).get("id"): managed_fields(item)
        for item in merged
        if (item.get("info") or {}).get("id") in managed_ids
    }
    if merged_projection == current_projection:
        logger.info("MCP registration is clean")
        if not dry_run:
            _write_mcp_state({item["info"]["id"]: item["url"] for item in desired})
        return 0
    if dry_run:
        logger.info("MCP registration dry-run: desired_streamable=%d", len(desired))
        return 0
    latest = client.get_tool_servers_config()
    if not isinstance(latest, dict) or latest.get("TOOL_SERVER_CONNECTIONS") != current:
        raise OpenWebUIError("Open WebUI MCP configuration changed before write")
    client.update_tool_servers_config({"TOOL_SERVER_CONNECTIONS": merged})
    verified_response = client.get_tool_servers_config()
    verified = (
        verified_response.get("TOOL_SERVER_CONNECTIONS")
        if isinstance(verified_response, dict)
        else None
    )
    unmanaged = [
        item
        for item in current
        if (item.get("info") or {}).get("id") not in managed_ids
    ]
    verified_managed = {
        (item.get("info") or {}).get("id"): managed_fields(item)
        for item in verified or []
        if (item.get("info") or {}).get("id") in desired_by_id
    }
    expected_managed = {item["info"]["id"]: managed_fields(item) for item in desired}
    if (
        not isinstance(verified, list)
        or [
            item
            for item in verified
            if (item.get("info") or {}).get("id") not in managed_ids
        ]
        != unmanaged
        or verified_managed != expected_managed
    ):
        raise OpenWebUIError(
            "Open WebUI MCP unmanaged preservation verification failed"
        )
    _write_mcp_state({item["info"]["id"]: item["url"] for item in desired})
    return 0


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
            "config": {},
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


def _curated_cloud_models():
    providers = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": PROVIDER_ENDPOINTS["google"]["apiKeyEnv"],
        "openrouter": PROVIDER_ENDPOINTS["openrouter"]["apiKeyEnv"],
        "opencode": PROVIDER_ENDPOINTS["opencode"]["apiKeyEnv"],
    }
    models = []
    for provider, key_env in providers.items():
        if not os.environ.get(key_env, "").strip():
            continue
        if provider in PROVIDER_ENDPOINTS:
            ids = provider_models(provider)
        else:
            allowlist = (
                Path(SCRIPT_DIR).parent
                / "configs"
                / "opencode"
                / f"{provider}-models.json"
            )
            ids = sorted(
                json.loads(allowlist.read_text(encoding="utf-8")).get("models", {})
            )
        models.extend(f"{provider}/{model}" for model in ids)
    return sorted(set(models))


def _merge_catalogue_key(current_value, managed_ids):
    """Merge-only update: managed IDs are updated/removed/appended; user
    entries and their relative order are preserved verbatim. Never clears
    the key."""
    if isinstance(current_value, str):
        current_entries = [e for e in current_value.split(",") if e]
    else:
        current_entries = list(current_value or [])
    unmanaged = [e for e in current_entries if e not in set(managed_ids)]
    kept_managed = [e for e in current_entries if e in set(managed_ids)]
    appended = [m for m in managed_ids if m not in set(current_entries)]
    merged = unmanaged + kept_managed + appended
    return merged


MANAGED_CATALOGUE_PREFIXES = (
    "openai/",
    "anthropic/",
    "google/",
    "openrouter/",
    "opencode/",
)


def _catalogue_state_path(migrate=True):
    path = Path(os.path.expanduser("~/.local/share/openwebui/data/managed-models.json"))
    old = path.parent.parent / "managed-models.json"
    # Legacy-state migration is a filesystem WRITE: skip it during dry-run.
    if migrate and old.is_file() and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(old, path)
    return path


def _load_catalogue_state(desired_ids, migrate=True):
    path = _catalogue_state_path(migrate)
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("managed_ids", []))
    # First-run adoption is conservative: only models this run explicitly
    # desires become managed; provider-shaped user entries remain unmanaged.
    return set(desired_ids)


def _write_catalogue_state(managed_ids):
    path = _catalogue_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps({"managed_ids": sorted(managed_ids)}, indent=2) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == rendered:
        return
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(rendered, encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def _reconcile_catalogue(client, dry_run=False):
    response = client.get_models_config()
    if not isinstance(response, dict):
        raise OpenWebUIError("Open WebUI model catalogue has an invalid shape")
    curated = _curated_cloud_models()
    if curated:
        # Validate curated IDs against the LIVE /api/models listing: any
        # curated ID the running service does not know is dropped (never
        # guess ID formats). Service down -> skip without writing.
        try:
            live_ids = {m.get("id") for m in client.get_models().get("data", [])}
        except OpenWebUIError as error:
            logger.warning(
                "Skipping catalogue reconciliation; live model IDs unavailable: %s",
                error,
            )
            return 0
        missing = [m for m in curated if m not in live_ids]
        if missing:
            logger.warning(
                "Dropping %d curated model IDs absent from live /api/models",
                len(missing),
            )
            curated = [m for m in curated if m in live_ids]
    owned = _load_catalogue_state(set(curated), migrate=not dry_run)
    managed_ids = owned | set(curated)
    desired = dict(response)
    for key in ("DEFAULT_MODELS", "DEFAULT_PINNED_MODELS", "MODEL_ORDER_LIST"):
        current_value = response.get(
            key, ",".join(curated) if key == "MODEL_ORDER_LIST" else ""
        )
        merged = _merge_catalogue_key(current_value, managed_ids)
        merged = [
            entry for entry in merged if entry not in (managed_ids - set(curated))
        ]
        merged.extend(item for item in curated if item not in merged)
        if key == "MODEL_ORDER_LIST":
            desired[key] = merged
        else:
            desired[key] = ",".join(merged)
    if desired == response:
        logger.info("Curated model catalogue is clean")
        if not dry_run:
            _write_catalogue_state(set(curated))
        return 0
    if dry_run:
        logger.info("Curated model catalogue dry-run: models=%d", len(curated))
        return 0
    if client.get_models_config() != response:
        raise OpenWebUIError("Open WebUI model catalogue changed before write")
    client.update_models_config(desired)
    if client.get_models_config() != desired:
        raise OpenWebUIError("Open WebUI model catalogue verification failed")
    _write_catalogue_state(set(curated))
    logger.info("Curated cloud model catalogue reconciled: models=%d", len(curated))
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

    if args.reconcile_mcp:
        api_key = os.environ.get(
            "OPENWEBUI_API_KEY", ""
        ).strip() or _openwebui_service_value("OPENWEBUI_API_KEY")
        if not api_key:
            logger.warning("OPENWEBUI_API_KEY is absent; skipping MCP registration")
            return 0
        base_url = f"http://127.0.0.1:{os.environ.get('OPENWEBUI_PORT', '8080')}"
        client = OpenWebUIClient(
            base_url, api_key, admin_credentials=_admin_credentials()
        )
        if not client.health_check():
            logger.warning("Open WebUI is not healthy; skipping MCP registration")
            return 0
        try:
            return _reconcile_mcp(client, args.dry_run)
        except OpenWebUIError as error:
            logger.error("MCP registration failed: %s", error)
            return 1

    if args.reconcile_catalogue:
        api_key = os.environ.get(
            "OPENWEBUI_API_KEY", ""
        ).strip() or _openwebui_service_value("OPENWEBUI_API_KEY")
        if not api_key:
            logger.warning(
                "OPENWEBUI_API_KEY is absent; skipping catalogue reconciliation"
            )
            return 0
        client = OpenWebUIClient(
            f"http://127.0.0.1:{os.environ.get('OPENWEBUI_PORT', '8080')}",
            api_key,
            admin_credentials=_admin_credentials(),
        )
        if not client.health_check():
            logger.warning(
                "Open WebUI is not healthy; skipping catalogue reconciliation"
            )
            return 0
        try:
            return _reconcile_catalogue(client, args.dry_run)
        except (OpenWebUIError, OSError, json.JSONDecodeError) as error:
            logger.error("Catalogue reconciliation failed: %s", error)
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
