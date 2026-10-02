import importlib.util
import json
import urllib.error
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "check-model-drift.py"
SPEC = importlib.util.spec_from_file_location("model_drift", SCRIPT)
drift = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drift)


def client_home(tmp_path, monkeypatch, client="pi"):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_OPENCODE_USE_LITELLM", "0")
    monkeypatch.setenv("DOTFILES_PI_USE_LITELLM", "0")
    keys = tmp_path / ".local/share/litellm/clients"
    keys.mkdir(parents=True)
    key = keys / f"{client}.key"
    key.write_text("fake-key-never-reported")
    key.chmod(0o600)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("LITELLM_PORT", "4000")
    monkeypatch.setenv(f"DOTFILES_{client.upper()}_USE_LITELLM", "1")
    monkeypatch.setattr(drift, "_configured_litellm_port", lambda: 4000)
    return key


def write_pi(tmp_path, data):
    path = tmp_path / ".pi/agent/models.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def write_pi_settings(tmp_path, data):
    path = tmp_path / ".pi/agent/settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def pi_provider(tmp_path, provider, model):
    return {
        "baseUrl": "http://127.0.0.1:4000/v1",
        "apiKey": f"!cat {tmp_path}/.local/share/litellm/clients/pi.key",
        "models": [{"id": model}],
    }


def test_scoped_same_url_and_cross_provider_collision(tmp_path, monkeypatch):
    client_home(tmp_path, monkeypatch)
    write_pi(
        tmp_path,
        {
            "providers": {
                "ollama": {
                    "baseUrl": "http://127.0.0.1:4000/v1",
                    "apiKey": f"!cat {tmp_path}/.local/share/litellm/clients/pi.key",
                    "models": [{"id": "shared"}],
                },
                "omlx": {
                    "baseUrl": "http://127.0.0.1:4000/v1",
                    "apiKey": f"!cat {tmp_path}/.local/share/litellm/clients/pi.key",
                    "models": [{"id": "shared"}],
                },
            }
        },
    )
    calls = []
    monkeypatch.setattr(
        drift,
        "get_catalogue",
        lambda url, key: (
            calls.append((url, key)) or {"data": [{"id": "ollama/shared"}]}
        ),
    )
    report, violations = drift.audit_client_providers()
    assert len(calls) == 2
    assert all(call[1] == "fake-key-never-reported" for call in calls)
    assert [
        entry["outcome"] for entry in report["results"] if entry.get("wire_model_id")
    ] == ["MISSING", "MISSING"]
    assert len(violations) == 2
    assert "fake-key-never-reported" not in json.dumps(report)


def test_known_missing_survives_auth_unknown(tmp_path, monkeypatch):
    client_home(tmp_path, monkeypatch)
    write_pi(
        tmp_path,
        {
            "providers": {
                "ollama": {
                    "baseUrl": "http://127.0.0.1:4000/v1",
                    "apiKey": f"!cat {tmp_path}/.local/share/litellm/clients/pi.key",
                    "models": [{"id": "known-missing"}],
                },
                "omlx": {
                    "baseUrl": "http://127.0.0.1:4000/v1",
                    "apiKey": f"!cat {tmp_path}/.local/share/litellm/clients/pi.key",
                    "models": [{"id": "unknown"}],
                },
            }
        },
    )

    def fetch(url, key):
        if len(getattr(fetch, "calls", [])):
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
        fetch.calls = [1]
        return {"data": []}

    monkeypatch.setattr(drift, "get_catalogue", fetch)
    report, violations = drift.audit_client_providers()
    assert "MISSING" in [item["outcome"] for item in report["results"]]
    assert "UNKNOWN" in [item["outcome"] for item in report["results"]]
    assert violations
    assert report["complete"] is False


def test_unsupported_key_expression_never_executed(tmp_path, monkeypatch):
    client_home(tmp_path, monkeypatch)
    write_pi(
        tmp_path,
        {
            "providers": {
                "ollama": {
                    "baseUrl": "http://127.0.0.1:4000/v1",
                    "apiKey": "!cat /tmp/evil; touch /tmp/ran",
                    "models": [{"id": "x"}],
                }
            }
        },
    )
    monkeypatch.setattr(
        drift, "get_catalogue", lambda *_: pytest.fail("must not fetch")
    )
    report, _ = drift.audit_client_providers()
    assert not report["complete"]
    assert any(item["outcome"] == "UNKNOWN" for item in report["results"])


def test_direct_route_is_explicitly_not_proxied(tmp_path, monkeypatch):
    client_home(tmp_path, monkeypatch)
    write_pi(
        tmp_path,
        {
            "providers": {
                "google": {
                    "baseUrl": "https://generativelanguage.googleapis.com/v1",
                    "models": [{"id": "gemini"}],
                }
            }
        },
    )
    report, _ = drift.audit_client_providers()
    assert any(item["reason"] == "DIRECT_NOT_PROXIED" for item in report["results"])
    assert all(item["outcome"] != "MATCH" for item in report["results"])


def test_key_symlink_rejected(tmp_path, monkeypatch):
    key = client_home(tmp_path, monkeypatch)
    other = tmp_path / "other.key"
    other.write_text("fake")
    key.unlink()
    key.symlink_to(other)
    write_pi(
        tmp_path,
        {
            "providers": {
                "ollama": {
                    "baseUrl": "http://127.0.0.1:4000/v1",
                    "apiKey": f"!cat {key}",
                    "models": [{"id": "x"}],
                }
            }
        },
    )
    report, _ = drift.audit_client_providers()
    assert not report["complete"]
    assert "fake" not in json.dumps(report)


def test_pi_role_override_inventory_mismatch(tmp_path, monkeypatch):
    client_home(tmp_path, monkeypatch)
    write_pi(
        tmp_path,
        {
            "providers": {
                "ollama": {
                    "baseUrl": "http://127.0.0.1:4000/v1",
                    "apiKey": f"!cat {tmp_path}/.local/share/litellm/clients/pi.key",
                    "models": [{"id": "listed"}],
                }
            }
        },
    )
    write_pi_settings(
        tmp_path,
        {
            "defaultProvider": "ollama",
            "subagents": {"agentOverrides": {"coder": {"model": "not-listed"}}},
        },
    )
    monkeypatch.setattr(drift, "get_catalogue", lambda *_: {"data": [{"id": "listed"}]})
    report, violations = drift.audit_client_providers()
    assert any("MISSING" in item["outcome"] for item in report["results"])
    assert any(
        item["field"] == "subagents.agentOverrides.coder.model"
        and item["outcome"] == "MISSING"
        for item in report["results"]
    )


def test_opencode_provider_key_alias_and_disabled_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_OPENCODE_USE_LITELLM", "1")
    monkeypatch.setenv("DOTFILES_PI_USE_LITELLM", "0")
    key_dir = tmp_path / ".local/share/litellm/clients"
    key_dir.mkdir(parents=True)
    key = key_dir / "opencode.key"
    key.write_text("fake-opencode-key")
    key.chmod(0o600)
    monkeypatch.setattr(drift, "_configured_litellm_port", lambda: 4000)
    config = tmp_path / ".config/opencode/opencode.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "provider": {
                    "ollama": {
                        "options": {
                            "baseURL": "http://127.0.0.1:4000/v1",
                            "apiKey": f"{{file:{key}}}",
                        },
                        "models": {"ollama/model": {}},
                    },
                    "unused": {"disabled": True, "models": {"x": {}}},
                }
            }
        )
    )
    monkeypatch.setattr(
        drift, "get_catalogue", lambda *_: {"data": [{"id": "ollama/model"}]}
    )
    report, violations = drift.audit_client_providers()
    assert not violations
    assert {item["outcome"] for item in report["results"]} == {
        "MATCH",
        "SKIPPED_INACTIVE",
    }
    assert "fake-opencode-key" not in json.dumps(report)


@pytest.mark.parametrize("bad_first", [True, False])
def test_each_routed_provider_key_reference_is_validated(
    tmp_path, monkeypatch, bad_first
):
    client_home(tmp_path, monkeypatch)
    providers = {
        "ollama": pi_provider(tmp_path, "ollama", "bad-ref"),
        "omlx": pi_provider(tmp_path, "omlx", "known-missing"),
    }
    providers["ollama"]["apiKey"] = "!cat /tmp/not-authorized.key"
    if not bad_first:
        providers = dict(reversed(list(providers.items())))
    write_pi(tmp_path, {"providers": providers})
    monkeypatch.setattr(drift, "get_catalogue", lambda *_: {"data": []})
    report, violations = drift.audit_client_providers()
    by_id = {item["wire_model_id"]: item["outcome"] for item in report["results"]}
    assert by_id["bad-ref"] == "UNKNOWN"
    assert by_id["known-missing"] == "MISSING"
    assert any("known-missing" in item for item in violations)


def test_pi_settings_selections_use_matching_provider_and_strip_one_prefix(
    tmp_path, monkeypatch
):
    client_home(tmp_path, monkeypatch)
    write_pi(
        tmp_path,
        {
            "providers": {
                "ollama": pi_provider(tmp_path, "ollama", "ollama/inner/name"),
                "omlx": pi_provider(tmp_path, "omlx", "omlx/inner/name"),
            }
        },
    )
    write_pi_settings(
        tmp_path,
        {
            "defaultProvider": "ollama",
            "defaultModel": "ollama/ollama/inner/name",
            "subagents": {
                "defaultModel": "ollama/ollama/inner/name",
                "agentOverrides": {
                    "coder": {"provider": "omlx", "model": "omlx/not-listed"},
                },
            },
        },
    )
    monkeypatch.setattr(
        drift,
        "get_catalogue",
        lambda *_: {"data": [{"id": "ollama/inner/name"}, {"id": "omlx/inner/name"}]},
    )
    report, violations = drift.audit_client_providers()
    assert any(
        item["field"] == "subagents.agentOverrides.coder.model"
        and item["outcome"] == "MISSING"
        for item in report["results"]
    )
    assert any(item["outcome"] == "MISSING" for item in report["results"])


def test_opencode_disabled_providers_and_enabled_missing_provider(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_OPENCODE_USE_LITELLM", "1")
    monkeypatch.setenv("DOTFILES_PI_USE_LITELLM", "0")
    monkeypatch.setattr(drift, "_configured_litellm_port", lambda: 4000)
    key_dir = tmp_path / ".local/share/litellm/clients"
    key_dir.mkdir(parents=True)
    key = key_dir / "opencode.key"
    key.write_text("fake")
    key.chmod(0o600)
    config = tmp_path / ".config/opencode/opencode.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "disabled_providers": ["ollama-cloud"],
                "provider": {
                    "ollama-cloud": {
                        "options": {"baseURL": "https://remote.invalid/v1"},
                        "models": {"x": {}},
                    },
                    "ollama": {
                        "options": {"baseURL": "http://127.0.0.1:4000/v1"},
                        "models": {"x": {}},
                    },
                },
            }
        )
    )
    monkeypatch.setattr(
        drift, "get_catalogue", lambda *_: pytest.fail("no enabled provider key")
    )
    report, _ = drift.audit_client_providers()
    assert any(
        item["field"] == "provider.ollama-cloud"
        and item["outcome"] == "SKIPPED_INACTIVE"
        for item in report["results"]
    )
    assert any(
        item["field"] == "provider.ollama.models.x" and item["outcome"] == "UNKNOWN"
        for item in report["results"]
    )


@pytest.mark.parametrize("client", ["opencode", "pi"])
def test_missing_enabled_config_is_incomplete(tmp_path, monkeypatch, client):
    client_home(tmp_path, monkeypatch, client)
    monkeypatch.setenv(f"DOTFILES_{client.upper()}_USE_LITELLM", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setattr(drift, "_configured_litellm_port", lambda: 4000)
    report, _ = drift.audit_client_providers()
    assert not report["complete"]
    assert any(item["outcome"] == "UNKNOWN" for item in report["results"])


def test_gate_off_missing_clients_are_explicitly_inactive(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(drift, "_configured_litellm_port", lambda: None)
    report, _ = drift.audit_client_providers()
    assert report["complete"]
    assert {item["outcome"] for item in report["results"]} == {"SKIPPED_INACTIVE"}


def test_missing_pi_settings_is_unknown_when_pi_routing_enabled(tmp_path, monkeypatch):
    client_home(tmp_path, monkeypatch)
    write_pi(tmp_path, {"providers": {"ollama": pi_provider(tmp_path, "ollama", "id")}})
    monkeypatch.setattr(drift, "get_catalogue", lambda *_: {"data": [{"id": "id"}]})
    report, _ = drift.audit_client_providers()
    assert any(
        item.get("reason") == "enabled Pi settings are missing"
        for item in report["results"]
    )
    assert not report["complete"]


@pytest.mark.parametrize("url", ["http://127.0.0.1:broken/v1", "http://[broken/v1"])
def test_malformed_proxy_url_is_sanitized_unknown_and_other_missing_survives(
    tmp_path, monkeypatch, url
):
    client_home(tmp_path, monkeypatch)
    write_pi(
        tmp_path,
        {
            "providers": {
                "ollama": {**pi_provider(tmp_path, "ollama", "bad"), "baseUrl": url},
                "omlx": pi_provider(tmp_path, "omlx", "missing"),
            }
        },
    )
    monkeypatch.setattr(drift, "get_catalogue", lambda *_: {"data": []})
    report, violations = drift.audit_client_providers()
    assert "MISSING" in [item["outcome"] for item in report["results"]]
    assert "UNKNOWN" in [item["outcome"] for item in report["results"]]
    assert violations
    assert url not in json.dumps(report)
