"""Synthetic evidence: no live admin credentials, host reads or inference."""

from datetime import datetime, timedelta, timezone
import hashlib

import pytest

import strictfree_evidence as evidence
from strictfree_evidence import Policy, State, collect_snapshot, snapshot_is_fresh

NOW = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
KEY = {
    "key": "synthetic-hash",
    "info": {
        "status": "active",
        "models": ["free-alias"],
        "updated_at": "2025-01-01T00:00:00Z",
        "expires": "2026-12-12T12:00:00Z",
        "spend": 2.0,
        "max_budget": 10.0,
    },
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
        endpoint="http://127.0.0.1:4000",
        now=now,
    )


def with_info(**changes):
    return {**KEY, "info": {**KEY["info"], **changes}}


def test_nested_live_schema_preserves_only_observable_metadata():
    result = snapshot()
    assert result.timestamp == NOW
    # A key may have positive headroom but unknown user/team/provider limits.
    assert result.remaining_quota is State.UNKNOWN
    assert result.charging_policy is Policy.UNKNOWN
    assert result.privacy_training is State.UNKNOWN
    assert result.backend_identity == "ollama/exact-registered-id"
    assert result.provenance == "unverified"
    assert snapshot_is_fresh(result, NOW + timedelta(minutes=4))
    assert not snapshot_is_fresh(result, NOW + timedelta(minutes=5))


def test_key_created_or_updated_long_ago_does_not_make_new_observation_stale():
    assert snapshot().reason == ""
    assert snapshot(with_info(updated_at="2020-01-01T00:00:00Z")).reason == ""


def test_absent_restricted_unrestricted_and_inactive_fail_closed():
    assert snapshot({}).reason == "malformed-key-info"
    assert snapshot(with_info(models=[])).reason == "unrestricted-key-scope-unknown"
    assert snapshot(with_info(models=["other"])).reason == "restricted"
    assert snapshot(with_info(status="revoked")).reason == "inactive-or-unknown-status"
    assert snapshot(with_info(blocked=True)).reason == "inactive-or-unknown-status"
    assert snapshot(with_info(expires="2026-11-11T12:00:00Z")).reason == "expired"
    assert snapshot(with_info(expires="not-a-date")).reason == "malformed-expiry"


def test_quota_and_backend_proof_refuse_optimistic_inference():
    assert snapshot(with_info(spend=10.0)).remaining_quota is State.EXHAUSTED
    assert snapshot(with_info(spend=11.0)).remaining_quota is State.EXHAUSTED
    assert snapshot(with_info(max_budget=None)).remaining_quota is State.UNKNOWN
    assert snapshot(with_info(remaining_quota=10.0)).remaining_quota is State.UNKNOWN
    mixed = {"data": CATALOGUE["data"] * 2}
    assert snapshot(catalog=mixed).reason == "backend-identity-unknown"
    public_models = {"data": [{"id": "free-alias", "object": "model"}]}
    assert snapshot(catalog=public_models).reason == "backend-identity-unknown"
    assert snapshot(catalog={"data": []}).reason == "backend-identity-unknown"


def test_malformed_and_redaction():
    assert snapshot(with_info(models="bad")).remaining_quota is State.UNKNOWN
    secret = "sk-" + "x" * 40
    message = evidence.redact_error("failed " + secret, secret, "client-a")
    assert secret not in message
    assert "[redacted-key]" in message
    assert "sk-" not in message


def test_collector_hashes_key_in_query_and_reads_only_two_get_routes(tmp_path):
    root = tmp_path / "litellm"
    (root / "clients").mkdir(parents=True)
    key = "sk-synthetic-virtual-not-for-use"
    (root / "service.env").write_text(
        "LITELLM_MASTER_KEY='sk-synthetic-master-not-for-use'\n", encoding="utf-8"
    )
    (root / "service.env").chmod(0o600)
    (root / "clients" / "gemini.key").write_text(key + "\n", encoding="utf-8")
    (root / "clients" / "gemini.key").chmod(0o600)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    calls = []

    def fake_get(url, master):
        assert master == "sk-synthetic-master-not-for-use"
        calls.append(url)
        if url.endswith("/v1/models"):
            return CATALOGUE
        assert url == "http://127.0.0.1:4000/key/info?key=" + digest
        return {**KEY, "key": digest}

    rows = evidence.collect_from_litellm(
        provider="litellm",
        model_alias="free-alias",
        endpoint="http://localhost:4000",
        now=NOW,
        root=root,
        fetch=fake_get,
    )
    assert len(rows) == 1
    assert rows[0].credential_identity == "gemini"
    assert rows[0].reason == ""
    assert calls == [
        "http://127.0.0.1:4000/v1/models",
        "http://127.0.0.1:4000/key/info?key=" + digest,
    ]
    assert all(key not in url for url in calls)


def test_collector_missing_key_or_nonprivate_file_never_sends_key(tmp_path):
    root = tmp_path / "litellm"
    (root / "clients").mkdir(parents=True)
    env = root / "service.env"
    env.write_text("LITELLM_MASTER_KEY='sk-dummy'\n", encoding="utf-8")
    env.chmod(0o644)
    calls = []
    rows = evidence.collect_from_litellm(
        provider="litellm",
        model_alias="alias",
        endpoint="http://127.0.0.1:4000",
        now=NOW,
        root=root,
        fetch=lambda url, master: calls.append(url),
    )
    assert len(rows) == 1 and rows[0].reason == "unresolvable"
    assert calls == []


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.invalid:4000/key/info?key=" + "a" * 64,
        "http://127.0.0.1.example.com:4000/v1/models",
        "http://user:pass@127.0.0.1:4000/v1/models",
        "http://127.0.0.1:4000/v1/models?x=1",
        "http://127.0.0.1:4000/key/info?key=sk-plaintext",
        "http://127.0.0.1:4000/model/info",
        "file:///etc/passwd",
    ],
)
def test_authenticated_metadata_get_refuses_unsafe_urls(url):
    with pytest.raises(ValueError):
        evidence._http_get(url, "synthetic-master")


def test_master_key_never_follows_redirect(monkeypatch):
    calls = []

    class FakeConnection:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def request(self, method, target, headers):
            calls.append(("request", method, target, headers["Authorization"]))

        def getresponse(self):
            class Redirect:
                status = 302

            return Redirect()

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(evidence.http.client, "HTTPConnection", FakeConnection)
    url = "http://127.0.0.1:4000/key/info?key=" + "a" * 64
    with pytest.raises(ValueError, match="unavailable"):
        evidence._http_get(url, "synthetic-master")
    assert calls == [
        ("connect", "127.0.0.1", 4000, 5),
        ("request", "GET", "/key/info?key=" + "a" * 64, "Bearer synthetic-master"),
        ("close",),
    ]
