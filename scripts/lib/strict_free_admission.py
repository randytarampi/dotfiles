"""Pure strict-free admission predicate. NOT wired to a request executor.

Only a trusted account-authorised collector may populate this evidence.
A caller-provided evidence object is not proof that the provider issued it.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re

FRESHNESS = timedelta(minutes=5)
SOURCES = frozenset({"account_billing_api", "owner_verified_account_record"})


@dataclass(frozen=True)
class Route:
    provider: str
    endpoint: str
    model: str
    backend_id: str
    account_id: str
    credential_fingerprint: str
    privacy_class: str


@dataclass(frozen=True)
class Evidence:
    route: Route
    provenance: str
    reference: str
    issued_at: datetime
    expires_at: datetime
    entitlement: str
    account_status: str
    max_billable_usd: str
    remaining_requests: int
    remaining_tokens: int
    allows_paid_overage: bool


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


def _time_ok(value):
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def admit(route, evidence, *, now, tokens, privacy_classes):
    """Check one exact backend before calling it; no network or side effects."""
    if not isinstance(route, Route):
        return Decision(False, "invalid-route-identity")
    if not isinstance(privacy_classes, (frozenset, set, tuple, list)):
        return Decision(False, "invalid-privacy-policy")
    if not _time_ok(now):
        return Decision(False, "invalid-clock")
    if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens <= 0:
        return Decision(False, "invalid-request-budget")
    identity = (
        route.provider,
        route.endpoint,
        route.model,
        route.backend_id,
        route.account_id,
        route.privacy_class,
    )
    if any(not isinstance(x, str) or not x or x != x.strip() for x in identity):
        return Decision(False, "invalid-route-identity")
    if not isinstance(route.credential_fingerprint, str) or not re.fullmatch(
        r"[0-9a-f]{64}", route.credential_fingerprint
    ):
        return Decision(False, "invalid-credential-binding")
    if route.privacy_class not in privacy_classes:
        return Decision(False, "privacy-boundary")
    if evidence is None:
        return Decision(False, "missing-account-evidence")
    if not isinstance(evidence, Evidence):
        return Decision(False, "malformed-account-evidence")
    if evidence.route != route:
        return Decision(False, "route-or-credential-drift")
    if (
        not isinstance(evidence.provenance, str)
        or evidence.provenance not in SOURCES
        or not isinstance(evidence.reference, str)
        or not evidence.reference.strip()
    ):
        return Decision(False, "untrusted-provenance")
    if not _time_ok(evidence.issued_at) or not _time_ok(evidence.expires_at):
        return Decision(False, "invalid-evidence-time")
    issued = evidence.issued_at.astimezone(timezone.utc)
    expires = evidence.expires_at.astimezone(timezone.utc)
    current = now.astimezone(timezone.utc)
    if issued > current or expires <= current or expires - issued > FRESHNESS:
        return Decision(False, "stale-or-future-evidence")
    if evidence.account_status != "active":
        return Decision(False, "inactive-account")
    if evidence.entitlement not in (
        "recurring_account_free",
        "account_included_zero_marginal",
    ):
        return Decision(False, "paid-trial-or-unknown-entitlement")
    if not isinstance(evidence.max_billable_usd, str):
        return Decision(False, "unknown-maximum-charge")
    try:
        price = Decimal(evidence.max_billable_usd)
    except (InvalidOperation, ValueError, TypeError):
        return Decision(False, "unknown-maximum-charge")
    if not price.is_finite() or price != 0:
        return Decision(False, "unknown-maximum-charge")
    if evidence.allows_paid_overage is not False:
        return Decision(False, "paid-overage-possible")
    quotas = (evidence.remaining_requests, evidence.remaining_tokens)
    if any(not isinstance(v, int) or isinstance(v, bool) for v in quotas):
        return Decision(False, "unknown-quota")
    if evidence.remaining_requests < 1 or evidence.remaining_tokens < tokens:
        return Decision(False, "exhausted-quota")
    return Decision(True, "account-qualified")


def admit_chain(routes, evidence, *, now, tokens, privacy_classes):
    """Deny if any possible fallback/child backend is unknown or ineligible."""
    if (
        not isinstance(routes, (tuple, list))
        or not isinstance(evidence, (tuple, list))
        or not routes
        or len(routes) != len(evidence)
    ):
        return Decision(False, "unknown-fallback-backends")
    for index, (route, proof) in enumerate(zip(routes, evidence)):
        decision = admit(
            route, proof, now=now, tokens=tokens, privacy_classes=privacy_classes
        )
        if not decision.allowed:
            return Decision(False, f"backend-{index}:{decision.reason}")
    return Decision(True, "every-backend-qualified")
