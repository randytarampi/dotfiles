from datetime import datetime, timedelta, timezone

from strictfree_evidence import Policy, State, collect_snapshot, redact_error

NOW = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
KEY = {
    "models": ["free-alias"],
    "updated_at": "2026-11-12T11:59:00Z",
    "remaining_quota": 5,
}
CATALOGUE = {
    "data": [
        {"id": "free-alias", "litellm_params": {"model": "ollama/exact-registered-id"}}
    ]
}


def snapshot(key=KEY, catalog=CATALOGUE, now=NOW):
    return collect_snapshot(
        provider="litellm",
        credential_identity="client-a",
        key_info=key,
        served_models=catalog,
        model_alias="free-alias",
        endpoint="https://gateway.invalid",
        now=now,
    )


def test_valid_snapshot_preserves_backend_but_charging_and_privacy_unknown():
    result = snapshot()
    assert result.remaining_quota is State.KNOWN
    assert result.charging_policy is Policy.UNKNOWN
    assert result.privacy_training is State.UNKNOWN
    assert result.backend_identity == "ollama/exact-registered-id"
    assert result.provenance == "unverified"


def test_absent_restricted_and_unrestricted_fail_closed():
    assert snapshot({}).reason == "absent"
    assert snapshot({**KEY, "models": []}).reason == "unrestricted-key-scope-unknown"
    assert snapshot({**KEY, "models": ["other"]}).reason == "restricted"


def test_stale_mixed_backend_and_exhausted_states():
    assert snapshot(KEY, now=NOW + timedelta(minutes=10)).reason == "stale"
    mixed = {"data": CATALOGUE["data"] * 2}
    assert snapshot(catalog=mixed).reason == "unresolvable"
    assert snapshot({**KEY, "remaining_quota": 0}).remaining_quota is State.EXHAUSTED


def test_malformed_and_secret_redaction():
    assert snapshot({**KEY, "models": "bad"}).remaining_quota is State.UNKNOWN
    secret = "sk-" + "x" * 40
    message = redact_error("failed " + secret, secret, "client-a")
    assert secret not in message
    assert "sk-xxx" in message
    assert not any(len(value) >= 25 and "x" * 25 in value for value in [message])
