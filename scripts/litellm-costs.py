#!/usr/bin/env python3
import argparse
import json
import os
import re
import datetime as dt
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LIB_DIR = os.path.join(SCRIPT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import logger  # noqa: E402
from cli_helpers import add_common_args  # noqa: E402
from env import load_env  # noqa: E402
import litellm_cost  # noqa: E402


def _parser():
    parser = argparse.ArgumentParser(
        description="Query read-only LiteLLM spend reports."
    )
    add_common_args(parser)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--window", default="7d")
    mode.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--by", action="append", default=[])
    parser.add_argument("--client", choices=("opencode", "pi", "openwebui", "junie"))
    parser.add_argument("--model")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--endpoint")
    return parser


def main():
    parser = _parser()
    args = parser.parse_args()
    load_env()
    base = args.endpoint or litellm_cost.resolve_base_url(os.environ)
    try:
        if not re.fullmatch(r"\d+[dhm]", args.window):
            parser.error("--window must match a duration such as 7d, 24h, or 30m")
        if args.start or args.end:
            if not args.start or not args.end:
                parser.error("--start and --end must be supplied together")
            date_pattern = r"(?:\d{4}-\d{2}-\d{2}|\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
            if not re.fullmatch(date_pattern, args.start) or not re.fullmatch(
                date_pattern, args.end
            ):
                parser.error(
                    "--start and --end must be YYYY-MM-DD or YYYY-MM-DD HH:MM:SS"
                )
            start, end = args.start, args.end
        else:
            try:
                start, end = litellm_cost.parse_window(args.window)
            except ValueError as error:
                parser.error(str(error))
        try:
            start_dt = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_dt = dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=dt.timezone.utc)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            parser.error("--start and --end must be valid calendar dates and times")
        if start_dt >= end_dt:
            parser.error("--start must be earlier than --end")
        start, end = litellm_cost.format_endpoint_date(
            start
        ), litellm_cost.format_endpoint_date(end)
        parsed = __import__("urllib.parse", fromlist=["urlsplit"]).urlsplit(base)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            parser.error("--endpoint must use loopback HTTP")
        dimensions = []
        for value in args.by or ["client"]:
            dimensions.extend(value.split(","))
        if any(
            value not in {"client", "model", "provider", "day"} for value in dimensions
        ):
            parser.error("--by must be client, model, provider, or day")
        key = litellm_cost.resolve_master_key(os.environ)
        if not key:
            raise RuntimeError(
                "LiteLLM master key not found; configure LITELLM_MASTER_KEY or service.env"
            )
        try:
            litellm_cost.validate_master_key(key)
        except ValueError as error:
            parser.error(litellm_cost.safe_error_message(error, key))
        if args.dry_run:
            import urllib.parse

            logger.info(
                "Would request %s/key/list?return_full_object=true\nWould request %s/spend/logs/v2?%s&page=1&page_size=1000 (paginated)\nMaster key source: %s",
                base.rstrip("/"),
                base.rstrip("/"),
                urllib.parse.urlencode({"start_date": start, "end_date": end}),
                (
                    "environment"
                    if os.environ.get("LITELLM_MASTER_KEY")
                    else "service.env"
                ),
            )
            return 0
        aliases = litellm_cost.fetch_key_aliases(base, key)
        data = litellm_cost.fetch_spend(
            base,
            key,
            start,
            end,
        )
        rows = data["logs"] if isinstance(data.get("logs"), list) else None
        if rows is None:
            logger.error(
                "UNKNOWN malformed spend response (missing logs array); no rows rendered"
            )
            return 1
        if args.client:
            rows = [
                row for row in rows if aliases.get(row.get("api_key")) == args.client
            ]
        if args.model:
            rows = [row for row in rows if row.get("model") == args.model]
        output = {
            by: litellm_cost.group_rows(rows, by, aliases)
            for by in dimensions
            if by != "day"
        }
        if "day" in dimensions:
            days = {}
            for row in rows:
                timestamp = row.get("startTime", row.get("start_time"))
                if not isinstance(timestamp, str) or len(timestamp) < 10:
                    raise RuntimeError(
                        "Spend row is missing a usable startTime timestamp"
                    )
                key_day = timestamp[:10]
                try:
                    dt.date.fromisoformat(key_day)
                except ValueError:
                    raise RuntimeError(
                        "Spend row has an invalid startTime date"
                    ) from None
                totals = days.setdefault(key_day, {"spend": 0.0, "tokens": 0})
                totals["spend"] += float(row.get("spend") or 0)
                totals["tokens"] += int(row.get("total_tokens", 0) or 0)
            output["day"] = days
        if args.json:
            print(
                json.dumps({**output, "total": data["total"]}, indent=2, sort_keys=True)
            )
        else:
            lines = ["LiteLLM spend (%s to %s)" % (start, end)]
            for by, groups in output.items():
                lines.append("%s:" % by)
                lines.extend(
                    "  %s: spend %.6f, tokens %d"
                    % (name, values["spend"], values["tokens"])
                    for name, values in sorted(groups.items())
                )
            lines.append("Total rows: %s" % data["total"])
            logger.info("\n".join(lines))
        return 0
    except (
        OSError,
        ValueError,
        RuntimeError,
        __import__("urllib.error", fromlist=["URLError"]).URLError,
    ) as error:
        logger.error(
            "LiteLLM spend query failed: %s",
            litellm_cost.safe_error_message(error, locals().get("key", "")),
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
