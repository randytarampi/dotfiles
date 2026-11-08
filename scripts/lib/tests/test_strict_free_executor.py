"""Synthetic paid-route spies for an unconnected, pure dispatch adapter."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from strict_free_admission import Evidence, Route
from strict_free_executor import StrictFreeDenied, guarded_choice, guarded_request

NOW = datetime(2026, 11, 7, 20, 0, tzinfo=timezone.utc)
R1 = Route(
    "provider", "https://example.invalid", "a", "primary", "acct", "a" * 64, "private"
)
R2 = replace(R1, model="b", backend_id="fallback")
E1 = Evidence(
    R1,
    "account_billing_api",
    "account-ledger",
    NOW - timedelta(seconds=5),
    NOW + timedelta(seconds=50),
    "recurring_account_free",
    "active",
    "0",
    2,
    1000,
    False,
)
E2 = replace(E1, route=R2)
KW = {"now": NOW, "tokens": 100, "privacy_classes": frozenset({"private"})}


def test_missing_entitlement_denies_before_any_send():
    calls = []
    with pytest.raises(StrictFreeDenied, match="missing-account-evidence"):
        guarded_request(R1, None, send=lambda route: calls.append(route), **KW)
    assert calls == []


def test_paid_backend_never_reaches_spy():
    calls = []
    with pytest.raises(StrictFreeDenied, match="unknown-maximum-charge"):
        guarded_request(
            R1,
            replace(E1, max_billable_usd="0.01"),
            send=lambda route: calls.append(route),
            **KW,
        )
    assert calls == []


def test_unevidenced_fallback_denies_before_primary_send():
    calls = []
    with pytest.raises(StrictFreeDenied, match="missing-account-evidence"):
        guarded_choice(
            (R1, R2),
            (E1, None),
            selected_index=0,
            send=lambda route: calls.append(route),
            **KW,
        )
    assert calls == []


def test_paid_fallback_denies_before_primary_send():
    calls = []
    with pytest.raises(StrictFreeDenied, match="unknown-maximum-charge"):
        guarded_choice(
            (R1, R2),
            (E1, replace(E2, max_billable_usd="0.01")),
            selected_index=0,
            send=lambda route: calls.append(route),
            **KW,
        )
    assert calls == []


def test_qualified_backend_must_be_selected_explicitly():
    calls = []
    assert (
        guarded_choice(
            (R1, R2),
            (E1, E2),
            selected_index=1,
            send=lambda route: (calls.append(route), "accepted")[1],
            **KW,
        )
        == "accepted"
    )
    assert calls == [R2]


@pytest.mark.parametrize("index", [-1, 2, True, None])
def test_invalid_backend_selection_denied(index):
    calls = []
    with pytest.raises(StrictFreeDenied, match="invalid-backend-selection"):
        guarded_choice(
            (R1, R2),
            (E1, E2),
            selected_index=index,
            send=lambda route: calls.append(route),
            **KW,
        )
    assert calls == []


def test_429_stops_without_using_stale_fallback_evidence():
    class RateLimitError(RuntimeError):
        pass

    calls = []

    def send(route):
        calls.append(route)
        raise RateLimitError("429 quota changed")

    with pytest.raises(RateLimitError, match="quota changed"):
        guarded_choice((R1, R2), (E1, E2), selected_index=0, send=send, **KW)
    assert calls == [R1]


def test_backend_identity_change_denies_without_calling_it():
    calls = []
    with pytest.raises(StrictFreeDenied, match="route-or-credential-drift"):
        guarded_request(
            replace(R1, credential_fingerprint="b" * 64),
            E1,
            send=lambda route: calls.append(route),
            **KW,
        )
    assert not calls
