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
    """Observe metadata without claiming free-tier or privacy eligibility.

    LiteLLM v1.106.6 returns {"key": <hash>, "info": {<key row>}}.
    A key's expires/updated_at/created_at fields are NOT observation times.
    """
    base = (provider, credential_identity, endpoint, model_alias)
    unknown_kwargs = {
        "now": now if isinstance(now, datetime) else None,
        "ttl": ttl if isinstance(ttl, timedelta) else timedelta(0),
    }
    if not _contract_ok(base, now, ttl, key_info, served_models):
        return _unknown(*base, reason="malformed", **unknown_kwargs)
    info = key_info.get("info")
    if not isinstance(info, dict):
        return _unknown(*base, reason="malformed-key-info", **unknown_kwargs)
    status_reason = _key_status(info, now)
    if status_reason:
        return _unknown(*base, reason=status_reason, **unknown_kwargs)
    allowed = _allowed_models(info, model_alias)
    if not isinstance(allowed, list):
        return _unknown(*base, reason=allowed, **unknown_kwargs)
    backend = _backend_identity(served_models, model_alias)
    if not backend:
        # LiteLLM's public /v1/models typically omits backend details:
        # a served ID is not proof of provider, cost or fallback identity.
        return _unknown(*base, reason="backend-identity-unknown", **unknown_kwargs)
    return EntitlementSnapshot(
        provider,
        credential_identity,
        endpoint,
        model_alias,
        now,
        ttl,
        "unverified",
        _quota_state(info),
        Policy.UNKNOWN,
        State.UNKNOWN,
        backend,
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


def _key_status(info, now):
    """Distinguish authentication expiry from evidence-collection freshness."""
    if info.get("status") != "active" or info.get("blocked") is True:
        return "inactive-or-unknown-status"
    expiry = info.get("expires")
    if expiry is None:
        return ""
    if not isinstance(expiry, str):
        return "malformed-expiry"
    try:
        date = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    except ValueError:
        return "malformed-expiry"
    if date.tzinfo is None:
        return "malformed-expiry"
    return "expired" if date <= now else ""


def snapshot_is_fresh(snapshot, now):
    """A saved observation expires using its collection timestamp and TTL."""
    return (
        isinstance(snapshot, EntitlementSnapshot)
        and isinstance(now, datetime)
        and now.tzinfo is not None
        and snapshot.timestamp.tzinfo is not None
        and isinstance(snapshot.ttl, timedelta)
        and snapshot.ttl > timedelta(0)
        and timedelta(0) <= now - snapshot.timestamp < snapshot.ttl
    )


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


def _quota_state(info):
    """Only a definite exhausted key budget is conclusive."""
    limit, spend = info.get("max_budget"), info.get("spend")
    numeric = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)
    if numeric(limit) and numeric(spend) and limit >= 0 and spend >= limit:
        return State.EXHAUSTED
    # Key headroom cannot prove overall user/team/provider quota.
    return State.UNKNOWN


def redact_error(message, secret, identity="unknown"):
    """Replace credential material in errors with a short identity label."""
    text = str(message)
    if isinstance(secret, str) and len(secret) >= 6:
        text = text.replace(secret, f"{secret[:6]}…[{identity}]")
    return text


def _gateway_origin(endpoint):
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("127.0.0.1", "localhost")
        or not parsed.port
        or parsed.path not in ("", "/")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("LiteLLM metadata gateway must be a loopback HTTP origin")
    return "http://127.0.0.1:" + str(parsed.port)


def _read_private_file(path):
    """Read only regular owner-owned mode 0600 files, without symlinks."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("missing or unsafe LiteLLM credential file")
    info = path.stat()
    if (
        info.st_uid != os.getuid()
        or info.st_nlink != 1
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ValueError("LiteLLM credential file must be owner-only mode 0600")
    return path.read_text(encoding="utf-8")


def collect_from_litellm(
    *, provider, model_alias, endpoint, now, root=None, fetch=None
):
    """Observe key scopes via hashed lookups; never attest charging or egress."""
    root = Path(root or Path.home() / ".local/share/litellm")
    try:
        origin = _gateway_origin(endpoint)
        env = _read_private_file(root / "service.env")
        master = next(
            (
                line.partition("=")[2].strip().strip("'\"")
                for line in env.splitlines()
                if line.startswith("LITELLM_MASTER_KEY=")
            ),
            "",
        )
        if not master or any(char.isspace() for char in master):
            raise ValueError("LiteLLM master credential unavailable")
        clients = root / "clients"
        if clients.is_symlink() or not clients.is_dir():
            raise ValueError("LiteLLM client registry unavailable")
        get = fetch or _http_get
        catalogue = get(origin + "/v1/models", master)
        paths = sorted(clients.glob("*.key"))
        if not paths:
            raise ValueError("no scoped LiteLLM keys found")
        output = []
        for path in paths:
            identity = path.stem
            try:
                secret = _read_private_file(path).strip()
                if not secret.startswith("sk-") or len(secret) < 6:
                    raise ValueError("unsupported virtual key format")
                # LiteLLM v1.106.6 accepts sha256; never put a plaintext
                # virtual key in the query string or an access log.
                key_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
                key_info = get(origin + "/key/info?key=" + key_hash, master)
                if not isinstance(key_info, dict) or key_info.get("key") != key_hash:
                    raise ValueError("LiteLLM key lookup identity mismatch")
                output.append(
                    collect_snapshot(
                        provider=provider,
                        credential_identity=identity,
                        key_info=key_info,
                        served_models=catalogue,
                        model_alias=model_alias,
                        endpoint=origin,
                        now=now,
                    )
                )
            except (OSError, ValueError, KeyError, TypeError):
                output.append(
                    _unknown(
                        provider,
                        identity,
                        origin,
                        model_alias,
                        now=now,
                        reason="unresolvable",
                    )
                )
        return output
    except (OSError, ValueError, KeyError, TypeError):
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
