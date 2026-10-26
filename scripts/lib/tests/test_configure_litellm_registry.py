import importlib.util
import shutil
from pathlib import Path
from typing import cast
from types import ModuleType
import os
import ast

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
        "DOTFILES_RUN_CODEX_SETUP",
        "DOTFILES_USE_LITELLM_PROXY",
        "LITELLM_CLIENT_KEY_FILE",
    ):
        monkeypatch.delenv(gate, raising=False)


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
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
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


def test_pi_auth_precedence_is_file_first_env_consistent_or_env_only(
    monkeypatch, tmp_path
):
    """Pi follows canonical file-primary auth; env is fallback or must match."""
    key_file = tmp_path / "pi.key"
    monkeypatch.setenv("LITELLM_PI_KEY", "same-key")
    key_file.write_text("same-key", encoding="utf-8")
    assert (
        configure_litellm.resolve_client_auth("LITELLM_PI_KEY", key_file).state
        == "accepted"
    )

    key_file.write_text("file-key", encoding="utf-8")
    monkeypatch.setenv("LITELLM_PI_KEY", "env-key")
    result = configure_litellm.resolve_client_auth("LITELLM_PI_KEY", key_file)
    assert result.state == "conflict"
    assert result.detail == "env and file credentials differ"

    key_file.unlink()
    monkeypatch.setenv("LITELLM_PI_KEY", "env-only")
    result = configure_litellm.resolve_client_auth("LITELLM_PI_KEY", key_file)
    assert result.state == "accepted"
    assert result.value == "env-only"


def test_main_provision_calls_use_registry_enforcement_default():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    calls = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "provision_app_keys"
    ]
    assert len(calls) == 2
    assert all(
        not any(keyword.arg == "enforce_registry_gates" for keyword in call.keywords)
        for call in calls
    )


def test_strict_auth_checks_client_identity(monkeypatch, tmp_path):
    calls = []

    def request(url, method, key, payload=None):
        calls.append((url, method, key, payload))
        if url.endswith("/key/list?return_full_object=true"):
            return {"keys": []}
        if url.endswith("/key/generate"):
            return {"key": f"key-for-{payload['key_alias']}"}
        if url.endswith("/v1/models"):
            expected = key.rsplit("-", 1)[-1]
            if expected not in ("pi", "opencode"):
                raise AssertionError("unexpected client identity")
            return {"data": [], "authenticated_as": expected}
        return {}

    monkeypatch.setattr(configure_litellm, "_request_json", request)
    monkeypatch.setattr(configure_litellm.shutil, "which", lambda _name: "/bin/client")
    _enabled(
        monkeypatch,
        "DOTFILES_RUN_LITELLM_SETUP",
        "DOTFILES_RUN_PI_SETUP",
        "DOTFILES_USE_LITELLM_PROXY",
        "DOTFILES_RUN_OPENCODE_SETUP",
    )
    service = tmp_path / "service.env"
    assert configure_litellm.provision_app_keys("master", service, strict=True)
    model_keys = [
        key for url, method, key, _payload in calls if url.endswith("/v1/models")
    ]
    assert model_keys == ["key-for-opencode", "key-for-pi"]


def test_strict_preflight_refuses_second_row_before_any_generation(
    monkeypatch, tmp_path
):
    calls = _fake_gateway(monkeypatch)
    _enabled(
        monkeypatch,
        "DOTFILES_RUN_LITELLM_SETUP",
        "DOTFILES_RUN_OPENCODE_SETUP",
        "DOTFILES_RUN_PI_SETUP",
        "DOTFILES_USE_LITELLM_PROXY",
    )
    monkeypatch.setattr(configure_litellm.shutil, "which", lambda _name: "/bin/client")
    monkeypatch.setenv("LITELLM_OPENCODE_KEY", "opencode-key")
    monkeypatch.setenv("LITELLM_PI_KEY", "pi-env")
    clients = tmp_path / "clients"
    clients.mkdir(mode=0o700)
    (clients / "opencode.key").write_text("opencode-key", encoding="utf-8")
    (clients / "pi.key").write_text("pi-env", encoding="utf-8")
    pi_path = clients / "pi.key"
    pi_path.write_text("pi-file", encoding="utf-8")
    before_opencode = (clients / "opencode.key").read_bytes()
    result = configure_litellm.provision_app_keys(
        "master", tmp_path / "service.env", strict=True
    )
    assert result is False
    assert [call for call in calls if call[0].endswith("/key/generate")] == []
    assert (clients / "opencode.key").read_bytes() == before_opencode


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
