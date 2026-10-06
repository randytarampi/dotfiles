"""Read-only LiteLLM spend queries."""

import datetime as dt
import json
import os
import re
import shlex
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from litellm_config import APP_KEYS
from model_catalogues import open_same_origin


def resolve_base_url(environ):
    return "http://127.0.0.1:%s" % environ.get("LITELLM_PORT", "4000")


def _read_service_key(path):
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.startswith("LITELLM_MASTER_KEY="):
                values = shlex.split(line.split("=", 1)[1])
                return values[0] if values else ""
    except (OSError, ValueError):
        pass
    return ""


def resolve_master_key(environ):
    return environ.get("LITELLM_MASTER_KEY") or _read_service_key(
        Path.home() / ".local/share/litellm/service.env"
    )


def _request(base_url, path, master_key, params=None):
    validate_master_key(master_key)
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("LiteLLM endpoint must use loopback HTTP")
    url = base_url.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    request = urllib.request.Request(
        url, headers={"Authorization": "Bearer " + master_key}
    )
    with open_same_origin(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _rows(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for name in ("data", "results", "spend_logs", "keys"):
            if isinstance(payload.get(name), list):
                return payload[name]
    return []


def _client_alias(alias):
    value = str(alias or "").lower()
    for client, env_name in APP_KEYS.items():
        if value == env_name.lower() or value in {client, "litellm-" + client}:
            return client
    return None


def fetch_key_aliases(base_url, master_key):
    payload = _request(
        base_url, "/key/list", master_key, {"return_full_object": "true"}
    )
    result = {}
    for item in _rows(payload):
        if not isinstance(item, dict):
            continue
        client = _client_alias(item.get("key_alias"))
        key = item.get("token") or item.get("api_key") or item.get("key")
        if client and key:
            result[key] = client
    return result


def fetch_spend(base_url, master_key, start, end):
    logs = []
    page = 1
    total = None
    pages = None
    while page <= 200:
        payload = _request(
            base_url,
            "/spend/logs/v2",
            master_key,
            {
                "start_date": start,
                "end_date": end,
                "page": page,
                "page_size": 1000,
            },
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise RuntimeError("Malformed spend logs pagination response")
        try:
            current_page = int(payload["page"])
            total_pages = int(payload["total_pages"])
            current_total = int(payload["total"])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("Malformed spend logs pagination metadata") from None
        if current_page != page or total_pages < 0 or current_total < 0:
            raise RuntimeError("Malformed spend logs pagination metadata")
        if total is not None and (total != current_total or total_pages != pages):
            raise RuntimeError("Inconsistent spend logs pagination metadata")
        total, pages = current_total, total_pages
        logs.extend(payload["data"])
        if page >= total_pages:
            if len(logs) != total:
                raise RuntimeError(
                    "Incomplete spend logs response: received %s rows, expected %s"
                    % (len(logs), total)
                )
            return {"logs": logs, "total": total}
        page += 1
    raise RuntimeError("Spend logs pagination exceeded 200 pages")


def validate_master_key(key):
    if not key or re.search(r"[\s\x00-\x1f\x7f]", key):
        raise ValueError(
            "LiteLLM master key contains invalid whitespace or control characters"
        )


def safe_error_message(exc, master_key=""):
    if isinstance(exc, urllib.error.HTTPError):
        message = "HTTP %s %s" % (exc.code, exc.reason)
    elif isinstance(exc, urllib.error.URLError):
        message = str(exc.reason)
    else:
        message = str(exc)
    if master_key:
        message = message.replace(master_key, "[REDACTED]")
    return re.sub(r"Bearer\s+[^\s'\"]+", "Bearer [REDACTED]", message, flags=re.I)


def format_endpoint_date(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def group_rows(rows, by, aliases=None):
    aliases = aliases or {}
    totals = {}
    for row in rows:
        model = str(row.get("model") or "unknown")
        if by == "client":
            key = aliases.get(row.get("api_key"), "unknown")
        elif by == "provider":
            key = model.split("/", 1)[0] if "/" in model else "unknown"
        else:
            key = model
        total = totals.setdefault(key, {"spend": 0.0, "tokens": 0})
        total["spend"] += float(row.get("spend") or 0)
        total["tokens"] += int(row.get("total_tokens", row.get("tokens", 0)) or 0)
    return totals


def parse_window(value):
    amount, unit = int(value[:-1]), value[-1:]
    if amount <= 0 or unit not in {"d", "h", "m"}:
        raise ValueError("window must be a positive duration such as 7d, 24h, or 30m")
    end = dt.datetime.now(dt.timezone.utc)
    start = end - (
        dt.timedelta(days=amount)
        if unit == "d"
        else (
            dt.timedelta(hours=amount) if unit == "h" else dt.timedelta(minutes=amount)
        )
    )
    fmt = lambda value: value.isoformat(timespec="seconds")
    return fmt(start), fmt(end)
