import importlib.util
import shutil
from pathlib import Path
from typing import cast
from types import ModuleType
import os

import pytest

import litellm_config

SCRIPT = Path(__file__).resolve().parents[2] / "configure-litellm.py"
SPEC = importlib.util.spec_from_file_location("configure_litellm", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
configure_litellm = cast(ModuleType, importlib.util.module_from_spec(SPEC))
configure_litellm.__dict__["shutil"] = shutil
SPEC.loader.exec_module(configure_litellm)


def _enabled(monkeypatch, *gates):
    for gate in gates:
        monkeypatch.setenv(gate, "1")


@pytest.fixture(autouse=True)
def _isolate_client_gates(monkeypatch):
    """Host shells export real DOTFILES gate flags; tests start from a clean slate."""
    for gate in (
        "DOTFILES_RUN_LITELLM_SETUP",
        "DOTFILES_RUN_OPENCODE_SETUP",
        "DOTFILES_RUN_PI_SETUP",
        "DOTFILES_RUN_OPENWEBUI_SETUP",
        "DOTFILES_RUN_JUNIE_CLI_SETUP",
        "DOTFILES_USE_LITELLM_PROXY",
        "LITELLM_CLIENT_KEY_FILE",
    ):
        monkeypatch.delenv(gate, raising=False)
    monkeypatch.setattr(configure_litellm, "_LEGACY_PROVISIONING_CALL", False)


def _fake_gateway(monkeypatch):
    calls = []
    records = []

    def request(url, method, key, payload=None):
        calls.append((url, method, payload))
        if url.endswith("/key/list?return_full_object=true"):
            return {"keys": list(records)}
        if url.endswith("/key/generate"):
            assert payload is not None
            records.append({"key_alias": payload["key_alias"]})
            return {"key": "generated-secret"}
        if url.endswith("/v1/models"):
            return {"data": []}
        return {}

    monkeypatch.setattr(configure_litellm, "_request_json", request)
    return calls


def test_classification_only_rows_never_generate_keys(monkeypatch, tmp_path):
    calls = _fake_gateway(monkeypatch)
    _enabled(monkeypatch, "DOTFILES_RUN_LITELLM_SETUP")
    monkeypatch.setattr(configure_litellm.shutil, "which", lambda _name: None)
    assert configure_litellm.provision_app_keys(
        "master", tmp_path / "service.env", strict=True
    )
    generated = [call for call in calls if call[0].endswith("/key/generate")]
    assert generated == []


def test_pi_requires_both_registry_gates(monkeypatch, tmp_path):
    calls = _fake_gateway(monkeypatch)
    monkeypatch.setattr(configure_litellm.shutil, "which", lambda _name: "/bin/client")
    _enabled(monkeypatch, "DOTFILES_RUN_LITELLM_SETUP", "DOTFILES_RUN_PI_SETUP")
    assert configure_litellm.provision_app_keys("master", tmp_path / "service.env")
    generated = [
        call[2]["key_alias"] for call in calls if call[0].endswith("/key/generate")
    ]
    assert generated == []


def test_registry_truth_table_and_pending_rows_are_not_eligible(monkeypatch):
    rows = {row["client"]: row for row in litellm_config.CLIENT_REGISTRY}
    assert set(rows["pi"]["setup_gates"]) == {
        "DOTFILES_USE_LITELLM_PROXY=1",
        "DOTFILES_RUN_LITELLM_SETUP=1",
    }
    assert all(
        rows[name]["credential_policy"] == "no-credential"
        for name in ("cursor", "copilot", "vscode")
    )


def test_junie_file_consumer_remains_materialized(monkeypatch, tmp_path):
    assert (
        next(row for row in litellm_config.CLIENT_REGISTRY if row["client"] == "junie")[
            "consumer_read_path"
        ]
        == "~/.local/share/litellm/clients/junie.key"
    )


@pytest.mark.parametrize(
    "env_value,file_value,state",
    [
        ("env-key", "file-key", "conflict"),
        ("env-key", "", "accepted"),
        ("", "", "absent"),
    ],
)
def test_canonical_auth_source_states_are_distinct(
    monkeypatch, tmp_path, env_value, file_value, state
):
    key_path = tmp_path / "opencode.key"
    if file_value:
        key_path.write_text(file_value, encoding="utf-8")
    monkeypatch.setenv("LITELLM_OPENCODE_KEY", env_value)
    result = configure_litellm.resolve_client_auth("LITELLM_OPENCODE_KEY", key_path)
    assert result.state == state
    if state == "conflict":
        assert result.detail == "env and file credentials differ"
        assert "env-key" not in result.detail and "file-key" not in result.detail


def test_backed_row_provisions_and_preserves_key_on_second_apply(monkeypatch, tmp_path):
    calls = _fake_gateway(monkeypatch)
    monkeypatch.setattr(configure_litellm.shutil, "which", lambda _name: "/bin/client")
    _enabled(monkeypatch, "DOTFILES_RUN_LITELLM_SETUP", "DOTFILES_RUN_OPENCODE_SETUP")
    _enabled(monkeypatch, "DOTFILES_RUN_PI_SETUP", "DOTFILES_USE_LITELLM_PROXY")
    monkeypatch.setenv("LITELLM_OPENCODE_KEY", "existing-key")
    client_path = tmp_path / "clients" / "opencode.key"
    client_path.parent.mkdir(mode=0o700)
    client_path.write_text("existing-key", encoding="utf-8")
    os.chmod(client_path, 0o600)
    service = tmp_path / "service.env"
    service.write_text("LITELLM_OPENCODE_KEY='existing-key'\n", encoding="utf-8")
    records = ["opencode", "pi"]
    original_request = configure_litellm._request_json

    def existing_aliases(url, method, key, payload=None):
        if url.endswith("/key/list?return_full_object=true"):
            return {"keys": [{"key_alias": alias} for alias in records]}
        return original_request(url, method, key, payload)

    monkeypatch.setattr(configure_litellm, "_request_json", existing_aliases)
    assert configure_litellm.provision_app_keys("master", service)
    before = client_path.read_bytes()
    assert configure_litellm.provision_app_keys("master", service)
    generated = [call for call in calls if call[0].endswith("/key/generate")]
    assert sum(call[2]["key_alias"] == "opencode" for call in generated) == 0
    assert client_path.read_bytes() == before


@pytest.mark.parametrize(
    "error,status",
    [(OSError("unauthorized"), 401), (ValueError("invalid config"), None)],
)
def test_negative_failures_remain_distinct(monkeypatch, tmp_path, error, status):
    def request(url, *_args, **_kwargs):
        if url.endswith("/health/readiness"):
            raise error
        return {}

    monkeypatch.setattr(configure_litellm, "_request_json", request)
    if status:
        error.code = status
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    _enabled(monkeypatch, "DOTFILES_RUN_OPENCODE_SETUP")
    monkeypatch.setattr(configure_litellm.shutil, "which", lambda _name: "/bin/client")
    assert (
        configure_litellm.provision_app_keys(
            "master", tmp_path / "service.env", strict=True
        )
        is False
    )
