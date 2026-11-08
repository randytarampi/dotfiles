"""Account-bound admission fixtures: no real credentials, network or files."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pytest

from strict_free_admission import Decision, Evidence, Route, admit, admit_chain

NOW = datetime(2026, 11, 7, 20, tzinfo=timezone.utc)
ROUTE = Route(
    "provider",
    "https://example.invalid/api",
    "served-model",
    "backend-a",
    "account-a",
    "a" * 64,
    "private",
)
PROOF = Evidence(
    ROUTE,
    "account_billing_api",
    "account-specific-record",
    NOW - timedelta(seconds=15),
    NOW + timedelta(seconds=60),
    "recurring_account_free",
    "active",
    "0",
    3,
    4096,
    False,
)


def check(route=ROUTE, proof=PROOF, tokens=256, privacy=frozenset({"private"})):
    return admit(route, proof, now=NOW, tokens=tokens, privacy_classes=privacy)


def test_accepts_only_current_account_bound_zero_charge():
    assert check() == Decision(True, "account-qualified")
    assert check(
        proof=replace(PROOF, entitlement="account_included_zero_marginal")
    ).allowed


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("provenance", "public_price_list", "untrusted-provenance"),
        ("entitlement", "trial_credit", "paid-trial-or-unknown-entitlement"),
        ("account_status", "revoked", "inactive-account"),
        ("max_billable_usd", "0.0001", "unknown-maximum-charge"),
        ("max_billable_usd", "NaN", "unknown-maximum-charge"),
        ("allows_paid_overage", True, "paid-overage-possible"),
        ("remaining_requests", 0, "exhausted-quota"),
        ("remaining_tokens", None, "unknown-quota"),
        ("remaining_tokens", 1, "exhausted-quota"),
        ("issued_at", NOW + timedelta(seconds=1), "stale-or-future-evidence"),
        ("expires_at", NOW - timedelta(seconds=1), "stale-or-future-evidence"),
        ("expires_at", NOW + timedelta(minutes=10), "stale-or-future-evidence"),
    ],
)
def test_denies_unproven_or_paid_account(field, value, reason):
    assert check(proof=replace(PROOF, **{field: value})).reason == reason


@pytest.mark.parametrize(
    "changed",
    [
        {"provider": "different"},
        {"endpoint": "https://other.invalid"},
        {"model": "same-name-other-model"},
        {"backend_id": "backend-b"},
        {"account_id": "account-b"},
        {"credential_fingerprint": "b" * 64},
    ],
)
def test_exact_identity_required(changed):
    assert check(route=replace(ROUTE, **changed)).reason == "route-or-credential-drift"


def test_privacy_and_fallback_guards():
    assert check(privacy=frozenset({"public"})).reason == "privacy-boundary"
    assert check(proof=None).reason == "missing-account-evidence"
    other = replace(ROUTE, backend_id="backend-b")
    assert not admit_chain(
        (ROUTE, other),
        (PROOF, None),
        now=NOW,
        tokens=1,
        privacy_classes=frozenset({"private"}),
    ).allowed
    assert admit_chain(
        (ROUTE, other),
        (PROOF, replace(PROOF, route=other)),
        now=NOW,
        tokens=1,
        privacy_classes=frozenset({"private"}),
    ).allowed


def test_unknown_budget_and_boolean_quota_fail_closed():
    assert check(tokens=0).reason == "invalid-request-budget"
    assert (
        check(proof=replace(PROOF, remaining_requests=True)).reason == "unknown-quota"
    )


@pytest.mark.parametrize(
    ("route", "evidence", "privacy", "reason"),
    [
        (None, PROOF, frozenset({"private"}), "invalid-route-identity"),
        (ROUTE, object(), frozenset({"private"}), "malformed-account-evidence"),
        (ROUTE, PROOF, None, "invalid-privacy-policy"),
        (ROUTE, replace(PROOF, provenance=[]), frozenset({"private"}), "untrusted-provenance"),
        (ROUTE, replace(PROOF, reference=[]), frozenset({"private"}), "untrusted-provenance"),
        (ROUTE, replace(PROOF, max_billable_usd=0), frozenset({"private"}), "unknown-maximum-charge"),
        (ROUTE, replace(PROOF, allows_paid_overage=None), frozenset({"private"}), "paid-overage-possible"),
    ],
)
def test_malformed_route_and_policy_evidence_denied_without_exceptions(
    route, evidence, privacy, reason
):
    assert check(route=route, proof=evidence, privacy=privacy).reason == reason


@pytest.mark.parametrize("routes, evidence", [(None, None), ((), ()), ((ROUTE,), None)])
def test_incomplete_chain_fails_closed(routes, evidence):
    assert admit_chain(
        routes,
        evidence,
        now=NOW,
        tokens=256,
        privacy_classes=frozenset({"private"}),
    ).reason == "unknown-fallback-backends"
