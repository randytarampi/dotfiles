#!/usr/bin/env python3
import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import glob as globlib

import logger  # noqa: E402
from cli_helpers import add_common_args  # noqa: E402
from env import load_env  # noqa: E402

RUNTIME_RATES_PATH = os.path.expanduser("~/.local/share/litellm/model-rates.json")
REPO_RATES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
    "configs/litellm/model-rates.json",
)
SERVICE_ENV_PATH = os.path.expanduser("~/.local/share/litellm/service.env")
STUB_SUFFIXES = (":cloud", "-cloud")
# Rows whose stored spend differs from the recomputed value by less than
# this are already correct (float noise from the original request).
SPEND_TOLERANCE = 1e-9
# Safety cap on rows updated in one run; the CLI prints progress per batch.
MAX_UPDATES = 20000


def _parser():
    parser = argparse.ArgumentParser(
        description=(
            "Recompute LiteLLM spend-log prices from the merged model rates "
            "(rows recorded before pricing landed stay at $0 otherwise)."
        ),
        allow_abbrev=False,
    )
    add_common_args(parser)
    parser.add_argument("--rates", help="Override the rate snapshot path")
    return parser


def wire_to_bare(model):
    """Collapse a spend-log model spelling to its bare rate-map key.

    Spend rows record the requested wire spelling: gateway aliases
    ('openai/x', 'ollama-cloud/x', 'chatgpt/x', 'meridian/x' — claude ids
    arrive bare for OpenWebUI), daemon cloud stubs ('x:cloud', 'x-cloud',
    with or without the ollama/ namespace), and bare local ids. Every priced
    lane resolves to the single bare models.dev-style key.
    """
    if not isinstance(model, str):
        return None
    bare = model
    if bare.startswith(("meridian/", "anthropic/")):
        bare = bare.split("/", 1)[1]
    for prefix in ("openai/", "chatgpt/", "ollama-cloud/", "ollama/"):
        if bare.startswith(prefix):
            bare = bare[len(prefix) :]
            break
    # The ollama-cloud allowlist keys dated ids verbatim ('deepseek-v4-pro:0813')
    # so only genuine daemon stub markers are stripped here.
    for suffix in STUB_SUFFIXES:
        if bare.endswith(suffix) and bare[: -len(suffix)]:
            bare = bare[: -len(suffix)]
            break
    return bare or None


def rate_for(model, rates):
    """Resolve (input, output) $/token rates for a spend-row model spelling."""
    bare = wire_to_bare(model)
    entry = rates.get(bare) if bare else None
    if isinstance(entry, dict):
        input_rate = entry.get("input_cost_per_token")
        output_rate = entry.get("output_cost_per_token")
        if isinstance(input_rate, (int, float)) and isinstance(
            output_rate, (int, float)
        ):
            return float(input_rate), float(output_rate)
    return None


def expected_spend(row, rates):
    """Recomputed spend for one SpendLogs row; None when it cannot be rebuilt.

    Cache-served rows included cache-read pricing we cannot reconstruct from
    the stored columns (SpendLogs has no cache-token columns), so they are
    reported and left untouched rather than guessed.
    """
    if not isinstance(row, dict):
        return None
    if str(row.get("cache_hit") or "").lower() == "true":
        return None
    rates_pair = rate_for(row.get("model"), rates)
    if rates_pair is None:
        return None
    input_rate, output_rate = rates_pair
    try:
        prompt = int(row.get("prompt_tokens") or 0)
        completion = int(row.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        return None
    if prompt <= 0 and completion <= 0:
        return None
    return prompt * input_rate + completion * output_rate


def plan_updates(rows, rates):
    """Rows needing correction: (request_id, current, expected), drift only."""
    plan = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        expected = expected_spend(row, rates)
        if expected is None:
            continue
        request_id = row.get("request_id")
        current = row.get("spend")
        if not isinstance(current, (int, float)):
            continue
        if abs(float(current) - expected) <= SPEND_TOLERANCE:
            continue
        plan.append((str(request_id), float(current), expected))
    return plan


def resolve_rates(path=None):
    """Rate snapshot: runtime first, committed repo snapshot as fallback."""
    candidates = [path] if path else [RUNTIME_RATES_PATH, REPO_RATES_PATH]
    for candidate in candidates:
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                snapshot = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("Rate snapshot unreadable (%s): %s", candidate, error)
            continue
        models = snapshot.get("models") if isinstance(snapshot, dict) else None
        if isinstance(models, dict) and models:
            return {str(k): v for k, v in models.items() if isinstance(v, dict)}
    return None


def _read_service_value(path, name):
    """Value for one KEY= line of service.env (quoted values included)."""
    import shlex

    try:
        for line in open(path, "r", encoding="utf-8"):
            if line.startswith(f"{name}="):
                values = shlex.split(line.split("=", 1)[1])
                return values[0] if values else ""
    except (OSError, ValueError):
        pass
    return ""


def _venv_python():
    root = os.environ.get("LITELLM_ROOT", os.path.expanduser("~/.local/share/litellm"))
    pythons = sorted(globlib.glob(os.path.join(root, "venv/bin/python3*")))
    for candidate in pythons:
        if candidate.endswith("3") or ".3" in os.path.basename(candidate):
            return candidate
    return None


_FETCH_CODE = """
import json, os
from prisma import Prisma
import asyncio

async def _main():
    client = Prisma()
    await client.connect()
    rows = await client.query_raw(
        'SELECT request_id, model, spend, prompt_tokens, completion_tokens, cache_hit '
        'FROM "LiteLLM_SpendLogs"'
    )
    await client.disconnect()
    print(json.dumps(rows, default=str))

asyncio.run(_main())
"""

_APPLY_CODE = """
import json, os, sys
from prisma import Prisma
import asyncio

plan = json.load(sys.stdin)

async def _main():
    client = Prisma()
    await client.connect()
    applied = 0
    for request_id, spend in plan:
        await client.query_raw(
            'UPDATE "LiteLLM_SpendLogs" SET spend = $1 WHERE request_id = $2',
            spend, request_id,
        )
        applied += 1
    await client.disconnect()
    print(json.dumps({"applied": applied}))

asyncio.run(_main())
"""


def _run_prisma(code, stdin_payload=None):
    """Run one prisma helper in the managed venv; returns parsed stdout.

    The DATABASE_URL is passed through the environment the same way the
    gateway service itself receives it (verified prisma recipe: os.environ
    must carry a usable DATABASE_URL before Prisma()).
    """
    database_url = _read_service_value(SERVICE_ENV_PATH, "DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL missing from service.env; cannot reach the spend tables"
        )
    python = _venv_python()
    if not python:
        raise RuntimeError("LiteLLM venv interpreter not found; run make deploy first")
    env = {**os.environ, "DATABASE_URL": database_url}
    import subprocess

    proc = subprocess.Popen(
        [python, "-c", code],
        stdin=subprocess.PIPE if stdin_payload is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    out, err = proc.communicate(
        input=stdin_payload.encode() if stdin_payload is not None else None
    )
    if proc.returncode != 0:
        # The prisma client prints service URLs on connect failures; keep the
        # payload out of diagnostics and surface only the failure class.
        tail = (err.decode(errors="replace").strip().splitlines() or ["?"])[-1]
        raise RuntimeError(f"spend backfill prisma step failed: {tail[:200]}")
    # The prisma client can print an engine banner before the JSON payload;
    # parse the last non-empty line rather than the whole stream.
    payload = [line for line in out.decode().splitlines() if line.strip()]
    if not payload:
        raise RuntimeError("spend backfill prisma step printed no payload")
    return json.loads(payload[-1])


def main():
    parser = _parser()
    args = parser.parse_args()
    if not load_env():
        logger.warning("~/.env not found")
    rates = resolve_rates(args.rates)
    if rates is None:
        logger.error(
            "UNKNOWN no readable rate snapshot (expected %s or a deploy refresh); "
            "run make deploy or pass --rates",
            RUNTIME_RATES_PATH,
        )
        return 1
    try:
        rows = _run_prisma(_FETCH_CODE)
    except (RuntimeError, json.JSONDecodeError) as error:
        logger.error(safe_error_message(error))
        return 1
    if not isinstance(rows, list):
        logger.error("UNKNOWN spend rows response was not a list")
        return 1
    plan = plan_updates(rows, rates)
    unpriced = [
        row
        for row in rows
        if isinstance(row, dict)
        and not str(row.get("cache_hit") or "").lower() == "true"
        and expected_spend(row, rates) is None
        and (row.get("prompt_tokens") or 0) + (row.get("completion_tokens") or 0) > 0
    ]
    capped = plan[:MAX_UPDATES]
    if args.dry_run:
        for item in capped[:20]:
            logger.info(
                "Would backfill %s: spend %s -> %.9f",
                item[0],
                item[1],
                item[2],
            )
        if len(capped) > 20:
            logger.info("…and %d more row(s)", len(capped) - 20)
        logger.info(
            "Backfill dry-run: %d row(s) would update, %d unpriced row(s) stay, %d rows total",
            len(capped),
            len(unpriced),
            len(rows),
        )
        return 0
    if not capped:
        logger.info(
            "Backfill: nothing to correct (%d rows checked, %d unpriced stay)",
            len(rows),
            len(unpriced),
        )
        return 0
    try:
        result = _run_prisma(
            _APPLY_CODE,
            stdin_payload=json.dumps(
                [[request_id, expected] for request_id, _, expected in capped]
            ),
        )
    except (RuntimeError, json.JSONDecodeError) as error:
        logger.error(safe_error_message(error))
        return 1
    applied = result.get("applied", 0) if isinstance(result, dict) else "?"
    logger.info(
        "Backfill complete: %s row(s) repriced, %d unpriced stay, %d rows total",
        applied,
        len(unpriced),
        len(rows),
    )
    return 0


def safe_error_message(error):
    """Error text without service-URL payloads (DATABASE_URL/keys scrubbed)."""
    import re

    text = str(error)
    text = re.sub(r"[a-zA-Z0-9_]+://\S+", "[REDACTED-URL]", text)
    return text


if __name__ == "__main__":
    sys.exit(main())
