import importlib.util
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "check-model-drift.py"
SPEC = importlib.util.spec_from_file_location("junie_model_drift", SCRIPT)
DRIFT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIFT)
import model_catalogues as CATALOGUES  # noqa: E402  # script bootstraps scripts/lib.


@pytest.fixture(autouse=True)
def isolated_home_path(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    models = tmp_path / ".junie/models"
    models.mkdir(parents=True)
    monkeypatch.setenv("JUNIE_MODELS_DIR", str(models))
    monkeypatch.setenv("JUNIE_LOCAL_GROUPS", str(tmp_path / "no-local-groups.json"))
    monkeypatch.delenv("JUNIE_DRIFT_SECRET_ONE", raising=False)
    monkeypatch.delenv("JUNIE_DRIFT_SECRET_TWO", raising=False)
    monkeypatch.delenv("JUNIE_DRIFT_SECRET_THREE", raising=False)
    DRIFT.DRIFT_STATS.update(checked=0, skipped=0)
    return models


def write_profile(models, name, providers, groups):
    path = models / name
    path.write_text(json.dumps({"providers": providers, "groups": groups}))
    return path


def test_same_url_credentials_are_checked_separately_and_unset_does_not_mask(
    isolated_home_path, monkeypatch
):
    path = write_profile(
        isolated_home_path,
        "groups.json",
        {
            "valid": {
                "baseUrl": "http://127.0.0.1:4000/v1",
                "apiKey": "${JUNIE_DRIFT_SECRET_ONE}",
            },
            "unset": {
                "baseUrl": "http://127.0.0.1:4000/v1",
                "apiKey": "${JUNIE_DRIFT_SECRET_TWO}",
            },
            "second-valid": {
                "baseUrl": "http://127.0.0.1:4000/v1",
                "apiKey": "${JUNIE_DRIFT_SECRET_THREE}",
            },
        },
        {
            "primary": {"provider": "valid", "primaryModel": "google/gemini-3.8-flash"},
            "secondary": {"provider": "unset", "primaryModel": "other/present"},
            "tertiary": {"provider": "second-valid", "primaryModel": "third/missing"},
        },
    )
    monkeypatch.setenv("JUNIE_DRIFT_SECRET_ONE", "secret-one-value")
    monkeypatch.setenv("JUNIE_DRIFT_SECRET_THREE", "secret-three-value")
    calls = []

    def fetch(url, key):
        calls.append((url, key))
        return {"data": [{"id": "other/gemini-3.8-flash"}]}

    monkeypatch.setattr(DRIFT, "get_catalogue", fetch)
    audit, violations = DRIFT.audit_junie_profiles()
    assert len(calls) == 2
    assert {key for _, key in calls} == {"secret-one-value", "secret-three-value"}
    assert any("google/gemini-3.8-flash" in item for item in violations)
    assert audit["complete"] is False
    assert {item["outcome"] for item in audit["results"]} == {"MISSING", "UNKNOWN"}
    assert all("secret-one-value" not in json.dumps(item) for item in audit["results"])
    assert all("secret-two" not in json.dumps(item) for item in audit["results"])
    assert "secret-three-value" not in json.dumps(audit)
    assert str(path) in json.dumps(audit)


def test_group_faster_provider_gets_its_own_endpoint_and_wire_id(
    isolated_home_path, monkeypatch
):
    write_profile(
        isolated_home_path,
        "faster.json",
        {
            "primary": {"baseUrl": "https://primary.example/v1", "apiKey": "p"},
            "fast": {"baseUrl": "https://fast.example/v1", "apiKey": "f"},
        },
        {
            "tier": {
                "provider": "primary",
                "primaryModel": "anthropic/primary",
                "fasterModel": "google/gemini-3.8-flash",
                "fasterProvider": "fast",
            }
        },
    )
    calls = []

    def fetch(url, key):
        calls.append((url, key))
        return {"data": [{"id": "other/gemini-3.8-flash"}]}

    monkeypatch.setattr(DRIFT, "get_catalogue", fetch)
    audit, violations = DRIFT.audit_junie_profiles()
    assert {key for _, key in calls} == {"p", "f"}
    faster = next(item for item in audit["results"] if item["field"] == "fasterModel")
    assert "fast.example" in faster["endpoint_namespace"]
    assert faster["wire_model_id"] == "google/gemini-3.8-flash"
    assert faster["outcome"] == "MISSING"
    assert violations


@pytest.mark.parametrize("status", [401, 403])
def test_http_auth_unknown_keeps_another_endpoint_mismatch(
    isolated_home_path, monkeypatch, status
):
    write_profile(
        isolated_home_path,
        "mix.json",
        {
            "denied": {
                "baseUrl": "https://denied.example/v1",
                "apiKey": "secret-denied",
            },
            "valid": {"baseUrl": "https://valid.example/v1", "apiKey": "secret-valid"},
        },
        {
            "auth": {"provider": "denied", "primaryModel": "vendor/unknown"},
            "missing": {"provider": "valid", "primaryModel": "google/gemini-3.8-flash"},
        },
    )

    def fetch(url, key):
        if "denied" in url:
            raise HTTPError(url, status, "denied", {}, None)
        return {"data": [{"id": "other/gemini-3.8-flash"}]}

    monkeypatch.setattr(DRIFT, "get_catalogue", fetch)
    audit, violations = DRIFT.audit_junie_profiles()
    assert audit["complete"] is False
    assert any(
        item["http_status"] == status and item["outcome"] == "UNKNOWN"
        for item in audit["results"]
    )
    assert any(item["outcome"] == "MISSING" for item in audit["results"])
    assert violations
    assert "secret-denied" not in json.dumps(audit)
    assert "secret-valid" not in json.dumps(audit)


def test_direct_google_models_prefix_is_only_normalized_for_google_host(
    isolated_home_path, monkeypatch
):
    write_profile(
        isolated_home_path,
        "google.json",
        {
            "direct": {
                "baseUrl": "https://generativelanguage.googleapis.com/v1beta",
                "apiKey": "${JUNIE_DRIFT_SECRET_ONE}",
            }
        },
        {"tier": {"provider": "direct", "primaryModel": "models/gemini-3.8-flash"}},
    )
    monkeypatch.setenv("JUNIE_DRIFT_SECRET_ONE", "not-reported")
    monkeypatch.setattr(
        DRIFT,
        "get_catalogue",
        lambda *_: {"data": [{"id": "models/gemini-3.8-flash"}]},
    )
    audit, violations = DRIFT.audit_junie_profiles()
    assert audit["complete"] is True
    assert audit["results"][0]["outcome"] == "MATCH"
    assert not violations


@pytest.mark.parametrize("payload", [None, [], {"data": [{}]}, {"not_data": []}])
def test_malformed_catalogues_unknown_but_empty_data_is_missing(
    isolated_home_path, monkeypatch, payload
):
    write_profile(
        isolated_home_path,
        "one.json",
        {"p": {"baseUrl": "https://models.example/v1", "apiKey": "value"}},
        {"g": {"provider": "p", "primaryModel": "vendor/model"}},
    )
    monkeypatch.setattr(DRIFT, "get_catalogue", lambda *_: payload)
    audit, violations = DRIFT.audit_junie_profiles()
    assert audit["complete"] is False
    assert audit["results"][0]["outcome"] == "UNKNOWN"
    assert not violations


def test_empty_successful_data_is_known_missing(isolated_home_path, monkeypatch):
    write_profile(
        isolated_home_path,
        "empty.json",
        {"p": {"baseUrl": "https://models.example/v1", "apiKey": "value"}},
        {"g": {"provider": "p", "primaryModel": "vendor/model"}},
    )
    monkeypatch.setattr(DRIFT, "get_catalogue", lambda *_: {"data": []})
    audit, violations = DRIFT.audit_junie_profiles()
    assert audit["complete"] is True
    assert audit["results"][0]["outcome"] == "MISSING"
    assert violations


def test_transport_failure_is_unknown_and_profile_shell_syntax_is_never_run(
    isolated_home_path, monkeypatch
):
    write_profile(
        isolated_home_path,
        "unsafe.json",
        {
            "p": {"baseUrl": "https://models.example/v1", "apiKey": "!cat ~/.secret"},
            "safe": {"baseUrl": "https://transport.example/v1", "apiKey": "safe-value"},
        },
        {
            "g": {"provider": "p", "primaryModel": "vendor/model"},
            "network": {"provider": "safe", "primaryModel": "vendor/network"},
        },
    )
    calls = []

    def fetch(*args):
        calls.append(args)
        raise OSError("network unavailable")

    monkeypatch.setattr(DRIFT, "get_catalogue", fetch)
    audit, violations = DRIFT.audit_junie_profiles()
    assert len(calls) == 1
    assert audit["complete"] is False
    assert audit["results"][0]["outcome"] == "UNKNOWN"
    assert "!cat" not in json.dumps(audit)
    assert not violations


def test_json_cli_reports_safe_provenance_and_require_complete(monkeypatch, capsys):
    monkeypatch.setattr(DRIFT, "load_env", lambda: None)
    monkeypatch.setattr(DRIFT, "check_slim", lambda _: [])
    monkeypatch.setattr(DRIFT, "check_local_engine_models", lambda: [])
    monkeypatch.setattr(DRIFT, "is_stale", lambda: (False, 0))
    audit = {
        "complete": False,
        "results": [
            {
                "outcome": "UNKNOWN",
                "endpoint_namespace": "litellm@https://proxy.example/v1",
                "credential_label": "env:SAFE_KEY_NAME",
                "reference_path": "/tmp/profile.json",
                "field": "primaryModel",
                "wire_model_id": "google/gemini-3.8-flash",
                "http_status": 403,
                "reason": "HTTP 403",
            }
        ],
    }
    monkeypatch.setattr(DRIFT, "audit_junie_profiles", lambda: (audit, []))
    monkeypatch.setattr("sys.argv", [str(SCRIPT), "--json", "--require-complete"])
    assert DRIFT.main() == 1
    output = json.loads(capsys.readouterr().out)
    assert output["junie_audit"] == audit
    assert "api-key-value" not in json.dumps(output)


def test_managed_litellm_port_audit_keeps_proxy_aliases_exact(
    isolated_home_path, monkeypatch
):
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.delenv("LITELLM_PORT", raising=False)
    write_profile(
        isolated_home_path,
        "litellm.json",
        {"litellm": {"baseUrl": "http://127.0.0.1:4000/v1", "apiKey": "litellm-key"}},
        {"primary": {"provider": "litellm", "primaryModel": "google/gemini-3.8-flash"}},
    )

    def validated_catalogue(url, _key):
        CATALOGUES._validate_catalogue_url(url)
        return {"data": [{"id": "google/models/gemini-3.8-flash"}]}

    monkeypatch.setattr(DRIFT, "get_catalogue", validated_catalogue)
    audit, violations = DRIFT.audit_junie_profiles()
    assert audit["complete"] is True
    assert audit["results"][0]["outcome"] == "MISSING"
    assert violations


def test_inline_primary_and_faster_credentials_keep_distinct_scopes(
    isolated_home_path, monkeypatch
):
    (isolated_home_path / "inline.json").write_text(
        json.dumps(
            {
                "baseUrl": "https://models.example/v1",
                "apiKey": "primary-private-value",
                "primaryModel": {"id": "provider/primary"},
                "fasterModel": {
                    "id": "provider/faster",
                    "apiKey": "faster-private-value",
                },
            }
        )
    )
    calls = []

    def fetch(url, key):
        calls.append(key)
        return {"data": [{"id": "provider/primary"}, {"id": "provider/faster"}]}

    monkeypatch.setattr(DRIFT, "get_catalogue", fetch)
    audit, violations = DRIFT.audit_junie_profiles()
    assert set(calls) == {"primary-private-value", "faster-private-value"}
    assert len({item["credential_label"] for item in audit["results"]}) == 2
    assert "primary-private-value" not in json.dumps(audit)
    assert "faster-private-value" not in json.dumps(audit)
    assert not violations


def test_default_text_output_warns_on_unknown_without_key_material(monkeypatch, caplog):
    monkeypatch.setattr(DRIFT, "load_env", lambda: None)
    monkeypatch.setattr(DRIFT, "check_slim", lambda _: [])
    monkeypatch.setattr(DRIFT, "check_local_engine_models", lambda: [])
    monkeypatch.setattr(DRIFT, "is_stale", lambda: (False, 0))
    monkeypatch.setattr(
        DRIFT,
        "audit_junie_profiles",
        lambda: (
            {
                "complete": False,
                "results": [
                    {
                        "outcome": "UNKNOWN",
                        "endpoint_namespace": "http://127.0.0.1:4000/v1/models",
                        "credential_label": "env:LITELLM_KEY",
                        "reason": "catalogue request failed",
                    }
                ],
            },
            [],
        ),
    )
    monkeypatch.setattr("sys.argv", [str(SCRIPT)])
    assert DRIFT.main() == 0
    assert "UNKNOWN" in caplog.text
    assert "env:LITELLM_KEY" in caplog.text
    assert "catalogue request failed" in caplog.text
    assert "secret" not in caplog.text
