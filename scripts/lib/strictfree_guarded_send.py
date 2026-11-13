"""First synchronous gateway-bound pre-send gate for one LiteLLM consumer.

This does not claim universal interception; the B1 inventory's BYPASSABLE and
NOT-INTERCEPTABLE verdicts still hold. The evidence collector does not attest
charging policy, so its snapshots can only deny, never prove free entitlement.
No true account-billing/entitlement source is wired on this build.
"owner_verified_account_record" is a designated future source supplied by the
owner; until one exists, every gated gateway send is denied before egress.
"""

from datetime import timedelta
import hashlib
from pathlib import Path

import strict_free_admission as admission
from strict_free_executor import StrictFreeDenied
from strictfree_evidence import (
    EntitlementSnapshot,
    Policy,
    State,
    _read_private_file,
    collect_from_litellm,
    snapshot_is_fresh,
)


def build_route(provider, model_alias, endpoint, credential_identity, privacy_class):
    """Bind a route to the digest of its protected, scoped LiteLLM client key."""
    path = Path.home() / ".local/share/litellm/clients" / (credential_identity + ".key")
    secret = _read_private_file(path).strip()
    if not secret.startswith("sk-") or len(secret) < 6:
        raise StrictFreeDenied("invalid-credential-binding")
    return admission.Route(
        provider,
        endpoint,
        model_alias,
        "unknown",
        credential_identity,
        hashlib.sha256(secret.encode("utf-8")).hexdigest(),
        privacy_class,
    )


def evidence_from_snapshot(snapshot, *, tokens_budget):
    """Fail closed: collector observations are not account billing evidence."""
    if not isinstance(snapshot, EntitlementSnapshot):
        return None
    if snapshot.provenance not in admission.SOURCES:
        return None
    if (
        getattr(snapshot.remaining_quota, "value", None) != "FREE"
        or getattr(snapshot.charging_policy, "value", None) != "FREE"
        or getattr(snapshot.privacy_training, "value", None) != "FREE"
    ):
        return None
    return admission.Evidence(
        route=None,
        provenance=snapshot.provenance,
        reference=snapshot.backend_identity,
        issued_at=snapshot.timestamp,
        expires_at=snapshot.timestamp + snapshot.ttl,
        entitlement="recurring_account_free",
        account_status="active",
        max_billable_usd="0",
        remaining_requests=1,
        remaining_tokens=tokens_budget,
        allows_paid_overage=False,
    )


def guarded_send(
    *,
    provider,
    model_alias,
    endpoint,
    credential_identity,
    privacy_class,
    privacy_classes,
    tokens,
    now,
    send,
    evidence_fetch=None,
    ttl=timedelta(minutes=5),
    root=None,
    trusted_evidence=None,
):
    """Collect exact-route metadata, authorize, then invoke one sender once."""
    if trusted_evidence is not None:
        if not isinstance(trusted_evidence, admission.Evidence):
            raise StrictFreeDenied("malformed-account-evidence")
        route = trusted_evidence.route
        if (
            route.provider != provider
            or route.endpoint != endpoint
            or route.model != model_alias
            or route.account_id != credential_identity
            or route.privacy_class != privacy_class
        ):
            raise StrictFreeDenied("route-or-credential-drift")
        decision = admission.admit(
            route,
            trusted_evidence,
            now=now,
            tokens=tokens,
            privacy_classes=privacy_classes,
        )
        if not decision.allowed:
            raise StrictFreeDenied(decision.reason)
        return send(route)

    rows = collect_from_litellm(
        provider=provider,
        model_alias=model_alias,
        endpoint=endpoint,
        now=now,
        root=root,
        fetch=evidence_fetch,
    )
    snapshot = next(
        (row for row in rows if row.credential_identity == credential_identity), None
    )
    if snapshot is None:
        raise StrictFreeDenied("missing-account-evidence")
    if snapshot.reason:
        raise StrictFreeDenied(snapshot.reason)
    if not snapshot_is_fresh(snapshot, now):
        raise StrictFreeDenied("stale-or-future-evidence")
    if snapshot.provenance not in admission.SOURCES:
        raise StrictFreeDenied("snapshot-not-account-qualified")
    if (
        getattr(snapshot.remaining_quota, "value", None) != "FREE"
        or getattr(snapshot.charging_policy, "value", None) != "FREE"
        or getattr(snapshot.privacy_training, "value", None) != "FREE"
    ):
        raise StrictFreeDenied("snapshot-not-account-qualified")
    if ttl <= timedelta(0) or snapshot.ttl > ttl:
        raise StrictFreeDenied("stale-or-future-evidence")
    evidence = evidence_from_snapshot(snapshot, tokens_budget=tokens)
    if evidence is None:
        raise StrictFreeDenied("snapshot-not-account-qualified")
    try:
        route = build_route(
            provider, model_alias, endpoint, credential_identity, privacy_class
        )
    except (OSError, ValueError):
        raise StrictFreeDenied("invalid-credential-binding") from None
    evidence = admission.Evidence(
        route,
        evidence.provenance,
        evidence.reference,
        evidence.issued_at,
        evidence.expires_at,
        evidence.entitlement,
        evidence.account_status,
        evidence.max_billable_usd,
        evidence.remaining_requests,
        evidence.remaining_tokens,
        evidence.allows_paid_overage,
    )
    decision = admission.admit(
        route, evidence, now=now, tokens=tokens, privacy_classes=privacy_classes
    )
    if not decision.allowed:
        raise StrictFreeDenied(decision.reason)
    return send(route)


__all__ = ["StrictFreeDenied", "build_route", "evidence_from_snapshot", "guarded_send"]
