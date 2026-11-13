"""Strict-free guarded-send tests; all gateway metadata is synthetic."""

from datetime import timedelta, timezone
from datetime import datetime
import hashlib
from urllib.parse import urlsplit

import pytest

import strict_free_admission as admission
import strictfree_guarded_send as guarded
from strictfree_evidence import collect_snapshot

NOW = datetime.now(timezone.utc)
ENDPOINT = "http://127.0.0.1:4000"
ALIAS = "free-alias"


def private_tree(tmp_path, key):
    root = tmp_path / "litellm"
    clients = root / "clients"
    clients.mkdir(parents=True)
    env = root / "service.env"
    env.write_text("LITELLM_MASTER_KEY='synthetic-master-value'\n")
    env.chmod(0o600)
    key_file = clients / "client-a.key"
    key_file.write_text(key + "\n")
    key_file.chmod(0o600)
    return root, hashlib.sha256(key.encode()).hexdigest()


def fetch_factory(calls, *, active=True, models=None):
    def fetch(url, _master):
        calls.append(url)
        if url.endswith("/v1/models"):
            return {
                "data": [{"id": ALIAS, "litellm_params": {"model": "backend/model"}}]
            }
        return {
            "key": hashlib.sha256("sk-synthetic-client-key".encode()).hexdigest(),
            "info": {
                "status": "active" if active else "revoked",
                "models": [ALIAS] if models is None else models,
                "spend": 0,
                "max_budget": 0,
            },
        }

    return fetch


def run_guard(tmp_path, *, calls=None, send_calls=None, now=NOW, **kwargs):
    calls = calls if calls is not None else []
    send_calls = send_calls if send_calls is not None else []
    root, _ = private_tree(tmp_path, "sk-synthetic-client-key")
    try:
        guarded.guarded_send(
            provider="litellm",
            model_alias=ALIAS,
            endpoint=ENDPOINT,
            credential_identity="client-a",
            privacy_class="audio-probe",
            privacy_classes={"audio-probe"},
            tokens=1,
            now=now,
            root=root,
            evidence_fetch=fetch_factory(calls, **kwargs),
            send=lambda route: send_calls.append(route) or "sent",
        )
    except guarded.StrictFreeDenied as error:
        return error
    pytest.fail("collector observations must fail closed")


def test_collector_deny_is_loopback_only_and_never_sends(tmp_path):
    calls, sends = [], []
    error = run_guard(tmp_path, calls=calls, send_calls=sends)
    assert str(error) == "snapshot-not-account-qualified"
    assert sends == []
    assert len(calls) == 2
    assert all(urlsplit(url).hostname == "127.0.0.1" for url in calls)
    assert {urlsplit(url).path for url in calls} == {"/v1/models", "/key/info"}


def test_send_never_runs_for_stale_collector_result(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        guarded,
        "collect_from_litellm",
        lambda **_kwargs: [
            type(
                "Snapshot",
                (),
                {
                    "credential_identity": "client-a",
                    "timestamp": NOW - timedelta(minutes=6),
                    "ttl": timedelta(minutes=5),
                    "reason": "",
                    "provenance": "unverified",
                    "remaining_quota": None,
                    "charging_policy": None,
                    "privacy_training": None,
                },
            )()
        ],
    )
    with pytest.raises(guarded.StrictFreeDenied, match="stale-or-future-evidence"):
        guarded.guarded_send(
            provider="litellm",
            model_alias=ALIAS,
            endpoint=ENDPOINT,
            credential_identity="client-a",
            privacy_class="audio-probe",
            privacy_classes={"audio-probe"},
            tokens=1,
            now=NOW,
            send=lambda route: calls.append(route),
        )
    assert calls == []


@pytest.mark.parametrize(
    "kwargs,now,reason",
    [
        ({"active": False}, NOW, "inactive-or-unknown-status"),
        ({"models": []}, NOW, "unrestricted-key-scope-unknown"),
    ],
)
def test_collector_denials_never_send(tmp_path, kwargs, now, reason):
    calls, sends = [], []
    error = run_guard(tmp_path, calls=calls, send_calls=sends, now=now, **kwargs)
    assert str(error) == reason
    assert sends == []
    assert all(urlsplit(url).hostname == "127.0.0.1" for url in calls)


def test_snapshot_mapping_rejects_unverified_and_unknown_states():
    snapshot = collect_snapshot(
        provider="litellm",
        credential_identity="client-a",
        key_info={"info": {"status": "active", "models": [ALIAS]}},
        served_models={"data": [{"id": ALIAS, "litellm_params": {"model": "x"}}]},
        model_alias=ALIAS,
        endpoint=ENDPOINT,
        now=NOW,
    )
    assert guarded.evidence_from_snapshot(snapshot, tokens_budget=1) is None


def test_snapshot_mapping_emits_only_zero_charge_evidence_when_states_are_free():
    snapshot = collect_snapshot(
        provider="litellm",
        credential_identity="client-a",
        key_info={"info": {"status": "active", "models": [ALIAS]}},
        served_models={"data": [{"id": ALIAS, "litellm_params": {"model": "x"}}]},
        model_alias=ALIAS,
        endpoint=ENDPOINT,
        now=NOW,
    )
    object.__setattr__(snapshot, "provenance", "owner_verified_account_record")
    object.__setattr__(
        snapshot, "remaining_quota", type("Free", (), {"value": "FREE"})()
    )
    object.__setattr__(
        snapshot, "charging_policy", type("Free", (), {"value": "FREE"})()
    )
    object.__setattr__(
        snapshot, "privacy_training", type("Free", (), {"value": "FREE"})()
    )
    evidence = guarded.evidence_from_snapshot(snapshot, tokens_budget=1)
    assert evidence is not None
    assert evidence.max_billable_usd == "0"


def test_snapshot_mapping_does_not_construct_nonzero_charge_evidence():
    snapshot = collect_snapshot(
        provider="litellm",
        credential_identity="client-a",
        key_info={"info": {"status": "active", "models": [ALIAS]}},
        served_models={"data": [{"id": ALIAS, "litellm_params": {"model": "x"}}]},
        model_alias=ALIAS,
        endpoint=ENDPOINT,
        now=NOW,
    )
    evidence = guarded.evidence_from_snapshot(snapshot, tokens_budget=1)
    assert evidence is None


def test_trusted_evidence_is_admitted_before_one_send(tmp_path):
    root, digest = private_tree(tmp_path, "sk-synthetic-client-key")
    route = admission.Route(
        "litellm", ENDPOINT, ALIAS, "backend/model", "client-a", digest, "audio-probe"
    )
    evidence = admission.Evidence(
        route,
        "owner_verified_account_record",
        "synthetic-owner-record",
        NOW,
        NOW + timedelta(minutes=1),
        "recurring_account_free",
        "active",
        "0",
        1,
        1,
        False,
    )
    calls = []

    def send(actual_route):
        calls.append(actual_route)
        return "sent"

    assert (
        guarded.guarded_send(
            provider="litellm",
            model_alias=ALIAS,
            endpoint=ENDPOINT,
            credential_identity="client-a",
            privacy_class="audio-probe",
            privacy_classes={"audio-probe"},
            tokens=1,
            now=NOW,
            send=send,
            trusted_evidence=evidence,
            root=root,
        )
        == "sent"
    )
    assert calls == [route]


def test_trusted_evidence_respects_privacy_boundary_without_send(tmp_path):
    root, digest = private_tree(tmp_path, "sk-synthetic-client-key")
    route = admission.Route(
        "litellm", ENDPOINT, ALIAS, "backend/model", "client-a", digest, "private"
    )
    evidence = admission.Evidence(
        route,
        "owner_verified_account_record",
        "owner-record",
        NOW,
        NOW + timedelta(minutes=1),
        "recurring_account_free",
        "active",
        "0",
        1,
        1,
        False,
    )
    sends = []
    with pytest.raises(guarded.StrictFreeDenied, match="privacy-boundary"):
        guarded.guarded_send(
            provider="litellm",
            model_alias=ALIAS,
            endpoint=ENDPOINT,
            credential_identity="client-a",
            privacy_class="private",
            privacy_classes={"public"},
            tokens=1,
            now=NOW,
            send=lambda value: sends.append(value),
            trusted_evidence=evidence,
            root=root,
        )
    assert sends == []


def test_trusted_evidence_must_match_actual_private_key_bytes(tmp_path):
    root, digest = private_tree(tmp_path, "sk-synthetic-client-key")
    route = admission.Route(
        "litellm",
        ENDPOINT,
        ALIAS,
        "backend/model",
        "client-a",
        "a" * 64,
        "audio-probe",
    )
    evidence = admission.Evidence(
        route,
        "owner_verified_account_record",
        "synthetic-record",
        NOW,
        NOW + timedelta(minutes=1),
        "recurring_account_free",
        "active",
        "0",
        1,
        1,
        False,
    )
    calls = []
    with pytest.raises(guarded.StrictFreeDenied, match="route-or-credential-drift"):
        guarded.guarded_send(
            provider="litellm",
            model_alias=ALIAS,
            endpoint=ENDPOINT,
            credential_identity="client-a",
            privacy_class="audio-probe",
            privacy_classes={"audio-probe"},
            tokens=1,
            now=NOW,
            send=lambda value: calls.append(value),
            trusted_evidence=evidence,
            root=root,
        )
    assert digest != route.credential_fingerprint
    assert calls == []


def test_gate_requires_canonical_litellm_wire_and_maps_env_key_reference(
    monkeypatch,
):
    import route_probes

    monkeypatch.setenv("DOTFILES_STRICTFREE_GATE", "1")
    calls = []

    def stub_guard(**kwargs):
        calls.append(kwargs)
        return 200, {}, b'{"text": "synthetic"}', None

    monkeypatch.setattr(guarded, "guarded_send", stub_guard)
    monkeypatch.setattr(
        route_probes, "_strictfree_wire_key_matches", lambda _key: True
    )
    result = route_probes.probe_stt(
        "http://127.0.0.1:4000/v1",
        ALIAS,
        "LITELLM_OPENCODE_KEY",
        key_value="synthetic-not-sent",
    )
    assert result.status == "ok"
    assert len(calls) == 1
    assert calls[0]["credential_identity"] == "opencode"
    calls.clear()
    for base in ("https://evil.example/v1", "http://localhost:4000/v1"):
        result = route_probes.probe_stt(
            base, ALIAS, "LITELLM_OPENCODE_KEY", key_value="synthetic-not-sent"
        )
        assert result.status == "unavailable"
        assert "strict-free-unqualified-route" in result.notes
    assert calls == []


def test_wire_bearer_must_match_the_private_scoped_key(tmp_path):
    import route_probes

    key = tmp_path / "opencode.key"
    key.write_text("sk-synthetic-good\n", encoding="utf-8")
    key.chmod(0o600)
    assert route_probes._strictfree_wire_key_matches(
        "sk-synthetic-good", scoped_path=key
    )
    assert not route_probes._strictfree_wire_key_matches(
        "sk-synthetic-other", scoped_path=key
    )
    assert not route_probes._strictfree_wire_key_matches(None, scoped_path=key)
    key.chmod(0o644)
    assert not route_probes._strictfree_wire_key_matches(
        "sk-synthetic-good", scoped_path=key
    )
    key.chmod(0o600)
    alias = tmp_path / "alias.key"
    alias.symlink_to(key)
    assert not route_probes._strictfree_wire_key_matches(
        "sk-synthetic-good", scoped_path=alias
    )
