#!/usr/bin/env python3
import argparse
import glob as globlib
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger  # noqa: E402 -- scripts/lib bootstrap.
from cli_helpers import add_common_args  # noqa: E402 -- scripts/lib bootstrap.
from env import load_env  # noqa: E402 -- scripts/lib bootstrap.

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
    arrive bare for OpenWebUI, plus the 'gemini/', 'cohere_chat/',
    'cerebras/', 'huggingface/' namespaces), daemon cloud stubs
    ('x:cloud', 'x-cloud', with or without the ollama/ namespace), and
    historical naked spellings.

    Namespaced NON-stub ollama rows ('ollama/<local-id>') are local
    hardware routes: they are returned verbatim so they can never match
    the Cloud-catalogue keys and be repriced as paid spend.
    """
    if not isinstance(model, str):
        return None
    bare = model
    if bare.startswith(("meridian/", "anthropic/")):
        bare = bare.split("/", 1)[1]
    for prefix in (
        "openai/",
        "chatgpt/",
        "ollama-cloud/",
        "gemini/",
        "cohere_chat/",
        "cerebras/",
        "huggingface/",
    ):
        if bare.startswith(prefix):
            bare = bare[len(prefix) :]
            break
    else:
        if bare.startswith("ollama/"):
            body = bare[len("ollama/") :]
            for suffix in STUB_SUFFIXES:
                if body.endswith(suffix) and body[: -len(suffix)]:
                    # Daemon cloud stub via the local namespace: priced.
                    return body[: -len(suffix)]
            return model
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
        # Serialized columns can arrive as strings (json default=str); a
        # silently-skipped unparsable row would hide drift from the report.
        try:
            current = float(current)
        except (TypeError, ValueError):
            continue
        if abs(current - expected) <= SPEND_TOLERANCE:
            continue
        plan.append((str(request_id), current, expected))
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

CHUNK_SIZE = 500
plan = json.load(sys.stdin)

async def _main():
    client = Prisma()
    await client.connect()
    applied = 0
    chunk_index = 0
    for start in range(0, len(plan), CHUNK_SIZE):
        batch = plan[start : start + CHUNK_SIZE]
        chunk_index += 1
        try:
            # Chunked interactive transactions: a failure commits nothing in
            # the failing chunk, and progress stays visible per chunk.
            async with client.tx() as tx:
                for request_id, spend in batch:
                    await tx.query_raw(
                        'UPDATE "LiteLLM_SpendLogs" SET spend = $1 WHERE request_id = $2',
                        spend, request_id,
                    )
        except Exception:
            # Partial-progress contract: the wrapper reports how far we got.
            print(json.dumps({"applied": applied, "failed_at_chunk": chunk_index}))
            raise
        applied += len(batch)
        print(json.dumps({"applied": applied, "chunk": chunk_index}))
    await client.disconnect()
    print(json.dumps({"applied": applied, "chunks": chunk_index}))

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
    import subprocess  # nosec B404 -- required to run the prisma helper under the managed venv.

    proc = subprocess.Popen(  # nosec B603 -- invocation is fully controlled: venv interpreter + internal code strings.
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
        # payload out of diagnostics and surface only the failure class — plus
        # the partially-applied count when the apply step already made progress.
        tail = (err.decode(errors="replace").strip().splitlines() or ["?"])[-1]
        detail = ""
        try:
            progress = json.loads(
                [line for line in out.decode().splitlines() if line.strip()][-1]
            )
            if isinstance(progress, dict) and "applied" in progress:
                detail = f"; rows applied before failure: {progress['applied']}"
        except (json.JSONDecodeError, IndexError):
            pass
        raise RuntimeError(f"spend backfill prisma step failed{detail}: {tail[:200]}")
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
    cache_hits = sum(
        1
        for row in rows
        if isinstance(row, dict) and str(row.get("cache_hit") or "").lower() == "true"
    )
    unpriced = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("cache_hit") or "").lower() == "true":
            continue
        if expected_spend(row, rates) is None:
            continue
        # Serialized token columns can arrive as strings; coerce defensively
        # so the row-count gate never concatenates strings or raises.
        try:
            tokens = int(row.get("prompt_tokens") or 0) + int(
                row.get("completion_tokens") or 0
            )
        except (TypeError, ValueError):
            continue
        if tokens > 0:
            unpriced.append(row)
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
            "Backfill dry-run: %d row(s) would update, %d cache-served stay, "
            "%d unpriced row(s) stay, %d rows total",
            len(capped),
            cache_hits,
            len(unpriced),
            len(rows),
        )
        # Cap contract (idempotent rerun continues the remainder): never hide
        # that corrections were left unprocessed.
        if len(plan) > MAX_UPDATES:
            logger.warning(
                "Backfill plan capped at %d: %d correction(s) need a follow-up run",
                MAX_UPDATES,
                len(plan) - MAX_UPDATES,
            )
        return 0
    if not capped:
        logger.info(
            "Backfill: nothing to correct (%d rows checked, %d cache-served stay, "
            "%d unpriced stay)",
            len(rows),
            cache_hits,
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
        "Backfill complete: %s row(s) repriced, %d cache-served stay, "
        "%d unpriced stay, %d rows total",
        applied,
        cache_hits,
        len(unpriced),
        len(rows),
    )
    if len(plan) > MAX_UPDATES:
        logger.warning(
            "Backfill plan capped at %d: %d correction(s) need a follow-up run",
            MAX_UPDATES,
            len(plan) - MAX_UPDATES,
        )
    return 0


def safe_error_message(error):
    """Error text without service-URL payloads (DATABASE_URL/keys scrubbed)."""
    import re

    text = str(error)
    text = re.sub(r"\w+://\S+", "[REDACTED-URL]", text)
    return text


if __name__ == "__main__":
    sys.exit(main())
