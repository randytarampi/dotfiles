"""Read-only LiteLLM account-evidence collector.

This is observational evidence, not a billing attestation: account-level
charging guarantees are UNKNOWN. A key with ``models: []`` is unrestricted,
so its scope is UNKNOWN rather than proof of entitlement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
import hashlib
import http.client
import json
import os
import re
import stat
from urllib.parse import urlsplit


class State(str, Enum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    EXHAUSTED = "EXHAUSTED"


class Policy(str, Enum):
    CHARGED = "CHARGED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class EntitlementSnapshot:
    provider: str
    credential_identity: str
    endpoint: str
    model_alias: str
    timestamp: datetime
    ttl: timedelta
    provenance: str
    remaining_quota: State
    charging_policy: Policy
    privacy_training: State
    backend_identity: str
    reason: str = ""


def _unknown(
    provider="",
    credential_identity="",
    endpoint="",
    model_alias="",
    now=None,
    ttl=timedelta(0),
    reason="unresolvable",
):
    return EntitlementSnapshot(
        provider,
        credential_identity,
        endpoint,
        model_alias,
        now or datetime.now(timezone.utc),
        ttl,
        "unverified",
        State.UNKNOWN,
        Policy.UNKNOWN,
        State.UNKNOWN,
        "",
        reason,
    )


def _safe_identity(identity, secret):
    if isinstance(identity, str) and identity:
        return identity
    return f"{secret[:6]}…" if isinstance(secret, str) else "unknown"


def collect_snapshot(
    *,
    provider,
    credential_identity,
    key_info,
    served_models,
    model_alias,
    endpoint,
    now,
    ttl=timedelta(minutes=5),
):
    """Build one fail-closed snapshot from injected LiteLLM JSON responses.

    ``key_info`` and ``served_models`` are the decoded responses from the
    read-only admin endpoints. Data defects yield UNKNOWN, never an exception.
    """
    base = (provider, credential_identity, endpoint, model_alias)
    unknown_kwargs = {
        "now": now if isinstance(now, datetime) else None,
        "ttl": ttl if isinstance(ttl, timedelta) else timedelta(0),
    }
    if not _contract_ok(base, now, ttl, key_info, served_models):
        return _unknown(*base, reason="malformed", **unknown_kwargs)
    observed = (
        key_info.get("expires")
        or key_info.get("updated_at")
        or key_info.get("created_at")
    )
    fresh = _fresh_observed(observed, now, ttl)
    if not isinstance(fresh, datetime):
        return _unknown(*base, reason=fresh, **unknown_kwargs)
    scope_or_reason = _allowed_models(key_info, model_alias)
    if not isinstance(scope_or_reason, list):
        return _unknown(*base, reason=scope_or_reason, **unknown_kwargs)
    backend_or_reason = _backend_identity(served_models, model_alias)
    if not isinstance(backend_or_reason, str):
        return _unknown(*base, reason="unresolvable", **unknown_kwargs)
    return EntitlementSnapshot(
        provider,
        credential_identity,
        endpoint,
        model_alias,
        fresh,
        ttl,
        "unverified",
        _quota_state(key_info.get("remaining_quota")),
        Policy.UNKNOWN,
        State.UNKNOWN,
        backend_or_reason,
        "",
    )


def _contract_ok(base, now, ttl, key_info, served_models):
    return bool(
        all(isinstance(v, str) and v.strip() for v in base)
        and isinstance(now, datetime)
        and now.tzinfo is not None
        and isinstance(ttl, timedelta)
        and ttl > timedelta(0)
        and isinstance(key_info, dict)
        and isinstance(served_models, dict)
    )


def _fresh_observed(observed, now, ttl):
    if not isinstance(observed, str):
        return "absent"
    try:
        timestamp = datetime.fromisoformat(observed.replace("Z", "+00:00"))
    except ValueError:
        return "malformed"
    if timestamp.tzinfo is None or now - timestamp > ttl or timestamp > now:
        return "stale"
    return timestamp


def _allowed_models(key_info, model_alias):
    models = key_info.get("models")
    if not isinstance(models, list) or any(not isinstance(x, str) for x in models):
        return "malformed"
    if not models:
        return "unrestricted-key-scope-unknown"
    if model_alias not in models:
        return "restricted"
    return models


def _backend_identity(served_models, model_alias):
    rows = served_models.get("data", served_models.get("models"))
    if not isinstance(rows, list):
        return None
    matches = [r for r in rows if isinstance(r, dict) and r.get("id") == model_alias]
    if len(matches) != 1:
        return None
    backend = (
        matches[0].get("litellm_params", {}).get("model")
        if isinstance(matches[0].get("litellm_params"), dict)
        else None
    )
    return backend if isinstance(backend, str) and backend.strip() else None


def _quota_state(quota):
    if isinstance(quota, (int, float)) and not isinstance(quota, bool):
        return State.EXHAUSTED if quota <= 0 else State.KNOWN
    return State.UNKNOWN


def redact_error(message, secret, identity="unknown"):
    """Replace credential material in errors with a short identity label."""
    text = str(message)
    if isinstance(secret, str) and len(secret) >= 6:
        text = text.replace(secret, f"{secret[:6]}…[{identity}]")
    return text


def collect_from_litellm(
    *, provider, model_alias, endpoint, now, root=None, fetch=None
):
    """Load local service credentials and make only GET requests when fetch is injected."""
    root = Path(root or Path.home() / ".local/share/litellm")
    try:
        env = (root / "service.env").read_text(encoding="utf-8")
        master = next(
            (
                line.partition("=")[2].strip().strip("'\"")
                for line in env.splitlines()
                if line.startswith("LITELLM_MASTER_KEY=")
            ),
            "",
        )
        if not master:
            return [
                _unknown(
                    provider=provider,
                    endpoint=endpoint,
                    model_alias=model_alias,
                    now=now,
                    reason="absent",
                )
            ]
        get = fetch or _http_get
        output = []
        for path in sorted((root / "clients").glob("*.key")):
            secret = path.read_text(encoding="utf-8").strip()
            identity = path.stem
            if not secret:
                output.append(
                    _unknown(
                        provider,
                        identity,
                        endpoint,
                        model_alias,
                        now=now,
                        reason="absent",
                    )
                )
                continue
            info = get(
                endpoint.rstrip("/") + "/key/info?key=" + urllib.parse.quote(secret),
                master,
            )
            catalog = get(endpoint.rstrip("/") + "/v1/models", master)
            output.append(
                collect_snapshot(
                    provider=provider,
                    credential_identity=identity,
                    key_info=info,
                    served_models=catalog,
                    model_alias=model_alias,
                    endpoint=endpoint,
                    now=now,
                )
            )
        return output
    except (OSError, ValueError, KeyError):
        return [
            _unknown(
                provider=provider,
                endpoint=endpoint,
                model_alias=model_alias,
                now=now,
                reason="unresolvable",
            )
        ]


def _http_get(url, master_key):
    """Authenticated GET to a fixed loopback API path; never follow redirects."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or not parsed.port
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("LiteLLM metadata request must use literal IPv4 loopback")
    target = parsed.path
    if target == "/key/info":
        if not re.fullmatch(r"key=[0-9a-f]{64}", parsed.query):
            raise ValueError("key metadata lookup requires a SHA-256 key hash")
        target += "?" + parsed.query
    elif target != "/v1/models" or parsed.query:
        raise ValueError("only two read-only LiteLLM metadata routes are allowed")
    connection = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=5)
    try:
        connection.request(
            "GET", target, headers={"Authorization": "Bearer " + master_key}
        )
        response = connection.getresponse()
        if response.status != 200:
            # Redirects and all non-200 statuses are hard refusals.
            raise ValueError("LiteLLM metadata unavailable")
        payload = response.read(1048577)
    except (OSError, http.client.HTTPException):
        raise ValueError("LiteLLM metadata unavailable") from None
    finally:
        connection.close()
    if len(payload) > 1048576:
        raise ValueError("LiteLLM metadata exceeds size limit")
    try:
        return json.loads(payload)
    except ValueError:
        raise ValueError("malformed LiteLLM metadata JSON") from None
