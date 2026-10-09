#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger  # noqa: E402
from cli_helpers import add_common_args  # noqa: E402
from env import load_env  # noqa: E402
from model_catalogues import open_same_origin  # noqa: E402
import litellm_cost  # noqa: E402

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.local/share/litellm/config.yaml")
DEFAULT_DELETE_PATH = "/model/delete"


def _parser():
    parser = argparse.ArgumentParser(
        description="Prune stale rows from LiteLLM model tables to match the generated config."
    )
    add_common_args(parser)
    parser.add_argument("--endpoint")
    parser.add_argument("--config")
    return parser


def served_model_names(config_path):
    """Return the set of model_name values the generated config actually serves."""
    try:
        with open(config_path, encoding="utf-8") as stream:
            text = stream.read()
    except OSError as error:
        raise RuntimeError(f"cannot read LiteLLM config {config_path}: {error}")
    names = _served_names_from_text(text)
    if not names:
        raise RuntimeError(
            f"no model_name entries parsed from {config_path}; refusing to operate "
            "against an empty served set"
        )
    return names


def _served_names_regex(text):
    """Dash-form model_name lines only — the no-PyYAML fallback parse."""
    names = set()
    pattern = re.compile(r'^\s*-\s*model_name:\s*"?([^"\n]+?)"?\s*(?:#.*)?$')
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            names.add(match.group(1).strip().strip('"'))
    return names - {""}


def _served_names_from_text(text):
    """Collect top-level model_list[*].model_name, preferring a real YAML parse."""
    try:
        import yaml
    except ModuleNotFoundError:
        # The macOS system Python used by the doctor also lacks PyYAML; the
        # regex fallback keeps the CLI runnable there (same policy as
        # litellm_routing.py). Only list items carry top-level model names —
        # nested litellm_params.model_name keys have no dash and are excluded.
        return _served_names_regex(text)
    parsed = yaml.safe_load(text)
    if parsed is None:
        return set()
    if not isinstance(parsed, dict) or "model_list" not in parsed:
        return set()
    return {
        str(entry["model_name"])
        for entry in parsed["model_list"]
        if isinstance(entry, dict) and entry.get("model_name")
    }


def model_info_rows(base_url, master_key):
    payload = litellm_cost._request(base_url, "/model/info", master_key)
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("malformed /model/info response: expected a data list")
    return rows


def plan_deletions(rows, served):
    """Return rows whose model_name is not served by the generated config."""
    stale = []
    for row in rows:
        name = row.get("model_name") if isinstance(row, dict) else None
        if name and name not in served:
            stale.append(row)
    return stale


def delete_row(base_url, master_key, row_id):
    """Delete one stale model row via the admin API; returns (ok, safe_message)."""
    body = json.dumps({"id": row_id}).encode("utf-8")
    url = f"{base_url.rstrip('/')}{DEFAULT_DELETE_PATH}"
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {master_key}",
            "Content-Type": "application/json",
            "User-Agent": "dotfiles-catalogue/1.0",
        },
        method="POST",
    )
    try:
        with open_same_origin(request, timeout=15) as response:
            status = getattr(response, "status", None)
            if status is None:
                code = getattr(response, "getcode", lambda: None)()
                status = code
            if status is None or not 200 <= status < 300:
                return False, f"DELETE returned HTTP status {status}"
    except Exception as error:  # noqa: BLE001 - single safe log line
        return False, litellm_cost.safe_error_message(error, master_key)
    return True, ""


def _is_loopback(base_url):
    """Mirror litellm_cost._request's loopback guard for usage validation."""
    parsed = urllib.parse.urlsplit(base_url)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}


def main():
    parser = _parser()
    args = parser.parse_args()
    load_env()
    base_url = args.endpoint or litellm_cost.resolve_base_url(os.environ)
    if not _is_loopback(base_url):
        parser.error(
            f"refusing non-loopback endpoint {base_url}; pruning targets the local gateway"
        )
    config_path = args.config or DEFAULT_CONFIG_PATH

    try:
        master_key = litellm_cost.resolve_master_key(os.environ)
        litellm_cost.validate_master_key(master_key)
    except ValueError as error:
        logger.error(str(error))
        return 1

    try:
        served = served_model_names(config_path)
        rows = model_info_rows(base_url, master_key)
    except RuntimeError as error:
        logger.error(litellm_cost.safe_error_message(error, master_key))
        return 1
    except Exception as error:  # noqa: BLE001
        logger.error(litellm_cost.safe_error_message(error, master_key))
        return 1

    stale = plan_deletions(rows, served)

    if args.dry_run:
        if not stale:
            print(
                f"Dry-run: would delete 0 stale row(s) of {len(rows)} candidates "
                f"(served {len(served)})"
            )
            return 0
        lines = [
            f"Would delete stale row {index + 1}/{len(stale)}: "
            f"{row.get('model_name') if isinstance(row, dict) else None}"
            f" (id {row.get('id') if isinstance(row, dict) else None})"
            for index, row in enumerate(stale)
        ]
        lines.append(
            f"Dry-run: would delete {len(stale)} stale row(s) of {len(stale)} "
            f"candidates (served {len(served)}, rows {len(rows)}) via "
            f"{base_url}{DEFAULT_DELETE_PATH}"
        )
        print("\n".join(lines))
        return 0

    if not stale:
        print(
            f"Prune complete: 0 stale of {len(rows)} rows; config serves {len(served)}"
        )
        return 0

    deleted = 0
    failures = []
    for row in stale:
        if not isinstance(row, dict):
            logger.warning("skipping malformed model-info row without a name")
            continue
        row_id = row.get("id")
        name = row.get("model_name")
        if not row_id:
            # Config-derived rows carry no DB id; /model/delete only manages
            # DB-stored deployments. Report and continue — a service restart
            # to reload the regenerated config resolves this class.
            logger.warning(
                f"not DB-managed (no model-info id); stale row {name} resolves "
                "after service restart"
            )
            continue
        ok, message = delete_row(base_url, master_key, row_id)
        if ok:
            deleted += 1
            print(f"Deleted stale model row: {name} (id {row_id})")
        else:
            failures.append(name)
            logger.error(f"Failed to delete {name}: {message}")

    summary = [
        f"Prune complete: deleted {deleted} stale row(s) of {len(stale)} candidates "
        f"(served {len(served)}, rows {len(rows)})."
    ]
    if failures:
        summary.append(f"Failed to delete: {', '.join(str(name) for name in failures)}")
    print("\n".join(summary))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
