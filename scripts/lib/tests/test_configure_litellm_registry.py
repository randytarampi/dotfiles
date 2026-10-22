import importlib.util
import shutil
from pathlib import Path
from typing import cast
from types import ModuleType

import pytest

import litellm_config

SCRIPT = Path(__file__).resolve().parents[2] / "configure-litellm.py"
SPEC = importlib.util.spec_from_file_location("configure_litellm", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
configure_litellm = cast(ModuleType, importlib.util.module_from_spec(SPEC))
configure_litellm.__dict__["shutil"] = shutil
SPEC.loader.exec_module(configure_litellm)
configure_litellm._TEST_PROVISIONING_ELIGIBILITY = True


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
    monkeypatch.setattr(configure_litellm.shutil, "which", lambda _name: "/bin/client")
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
    assert not any(
        call[2] and call[2].get("key_alias") == "pi"
        for call in calls
        if call[0].endswith("/key/generate")
    )


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


def test_backed_row_provisions_and_preserves_key_on_second_apply(monkeypatch, tmp_path):
    calls = _fake_gateway(monkeypatch)
    monkeypatch.setattr(configure_litellm.shutil, "which", lambda _name: "/bin/client")
    _enabled(monkeypatch, "DOTFILES_RUN_LITELLM_SETUP", "DOTFILES_RUN_OPENCODE_SETUP")
    service = tmp_path / "service.env"
    assert configure_litellm.provision_app_keys("master", service)
    assert configure_litellm.provision_app_keys("master", service)
    generated = [call for call in calls if call[0].endswith("/key/generate")]
    assert sum(call[2]["key_alias"] == "opencode" for call in generated) == 1
    assert (
        tmp_path / "clients" / "opencode.key"
    ).read_text().strip() == "generated-secret"


@pytest.mark.parametrize(
    "error,status",
    [(OSError("unauthorized"), 401), (ValueError("invalid config"), None)],
)
def test_negative_failures_remain_distinct(monkeypatch, tmp_path, error, status):
    def request(url, *_args, **_kwargs):
        if url.endswith("/health/liveliness"):
            raise error
        return {}

    monkeypatch.setattr(configure_litellm, "_request_json", request)
    # At least one eligible row is required so the strict flow reaches the
    # liveliness/credential phase that must fail distinctly.
    _enabled(monkeypatch, "DOTFILES_RUN_LITELLM_SETUP", "DOTFILES_RUN_OPENCODE_SETUP")
    monkeypatch.setattr(configure_litellm.shutil, "which", lambda _name: "/bin/client")
    if status:
        error.code = status
    assert (
        configure_litellm.provision_app_keys(
            "master", tmp_path / "service.env", strict=True
        )
        is False
    )
