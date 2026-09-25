import importlib.util
import os
from pathlib import Path

import pytest

import constants
import openwebui


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch):
    prefixes = (
        "DOTFILES_",
        "OPENAI_",
        "ANTHROPIC_",
        "OLLAMA_",
        "OMLX_",
        "MERIDIAN_",
        "OPENROUTER_",
        "GOOGLE_",
        "GEMINI_",
        "OPENWEBUI_",
    )
    for name in list(os.environ):
        if name.startswith(prefixes):
            monkeypatch.delenv(name, raising=False)


def _entry(prefix, url, kind="openai", collection="openai", key="secret-key", **config):
    return {
        "url": url,
        "key": key,
        "config": {
            "prefix_id": prefix,
            "connection_type": kind,
            "enable": True,
            **config,
        },
        "collection": collection,
    }


def _script():
    path = Path(__file__).resolve().parents[3] / "scripts" / "configure-openwebui.py"
    spec = importlib.util.spec_from_file_location("configure_openwebui_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_native_and_cloud_endpoints(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://openai.local/v1")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://anthropic.local/v1")
    monkeypatch.setattr(constants, "check_ollama_daemon", lambda: (True, False))
    state = openwebui.compute_desired_state()
    by_prefix = {
        item["config"]["prefix_id"]: item
        for item in state["openai"] + state["anthropic"]
    }
    assert state["ollama"][0]["url"] == "http://localhost:11434"
    assert by_prefix["dw-openai"]["url"] == "http://openai.local/v1"
    assert by_prefix["dw-anthropic"]["url"] == "http://anthropic.local"
    assert by_prefix["dw-ollama-cloud"]["url"] == "https://ollama.com/v1"
    monkeypatch.setattr(constants, "check_ollama_daemon", lambda: (True, True))
    proxied = openwebui.compute_desired_state()
    assert {item["config"]["prefix_id"]: item for item in proxied["openai"]}[
        "dw-ollama-cloud"
    ]["url"] == "http://localhost:11434/v1"


def test_dual_protocol_and_stable_ownership(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setattr(constants, "is_meridian_configured", lambda: True)
    state = openwebui.compute_desired_state()
    prefixes = {
        item["config"]["prefix_id"] for item in state["openai"] + state["anthropic"]
    }
    assert {"dw-omlx-openai", "dw-omlx-anthropic"} <= prefixes
    emitted = openwebui.emit_env(state)
    assert "dw-omlx-openai" in emitted
    assert "dw-omlx-anthropic" in emitted
    assert "dw-meridian" in emitted
    assert "dw-anthropic" in emitted
    result = openwebui.reconcile([], [], state)
    planned = {item["entry"]["config"]["prefix_id"] for item in result.plan.entries}
    assert {
        "dw-omlx-openai",
        "dw-omlx-anthropic",
        "dw-meridian",
        "dw-anthropic",
    } <= planned
    assert "dw-omlx-openai" in openwebui.ownership_catalogue()
    monkeypatch.setenv("DOTFILES_OPENWEBUI_DISABLED_ENGINES", "omlx")
    assert "dw-omlx-openai" in openwebui.ownership_catalogue()
    assert not any(
        item["config"]["prefix_id"].startswith("dw-omlx")
        for item in openwebui.compute_desired_state()["openai"]
        + openwebui.compute_desired_state()["anthropic"]
    )


def test_disabled_engine_and_meridian(monkeypatch):
    monkeypatch.setenv("DOTFILES_RUN_OMLX_SETUP", "1")
    monkeypatch.setenv("DOTFILES_OPENWEBUI_DISABLED_ENGINES", "oMlX")
    monkeypatch.setattr(constants, "is_meridian_configured", lambda: False)
    assert not any(
        item["config"]["prefix_id"].startswith("dw-omlx")
        for item in openwebui.compute_desired_state()["openai"]
        + openwebui.compute_desired_state()["anthropic"]
    )
    monkeypatch.setattr(constants, "is_meridian_configured", lambda: True)
    monkeypatch.setattr(
        constants, "get_meridian_base_url", lambda: "http://meridian:3456/v1"
    )
    item = next(
        item
        for item in openwebui.compute_desired_state()["openai"]
        + openwebui.compute_desired_state()["anthropic"]
        if item["config"]["prefix_id"] == "dw-meridian"
    )
    assert item["url"] == "http://meridian:3456/v1"


def test_collision_wrong_endpoint_is_fail_closed():
    desired = {
        "openai": [_entry("dw-openai", "https://api.openai.com/v1")],
        "ollama": [],
    }
    current = [_entry("dw-openai", "http://wrong/v1")]
    result = openwebui.reconcile(current, [], desired)
    assert result.status == "collision"
    assert not any(
        item["action"] in {"update", "delete"} for item in result.plan.entries
    )


def test_key_rotation_enable_flip_and_dual_protocol_updates():
    url = openwebui.ownership_catalogue()["dw-omlx-openai"]["url"]
    desired = {
        "openai": [
            _entry("dw-omlx-openai", url, key="new-key"),
            _entry("dw-omlx-anthropic", url, "anthropic", key="new-key"),
        ],
        "ollama": [],
    }
    current = [
        _entry("dw-omlx-openai", url, key="old-key"),
        _entry(
            "dw-omlx-anthropic",
            url,
            "anthropic",
            key="new-key",
            enable=False,
        ),
    ]
    result = openwebui.reconcile(current, [], desired)
    assert [item["action"] for item in result.plan.entries] == ["update", "update"]


def test_removal_uses_catalogue_without_managed_marker():
    desired = {"openai": [], "ollama": []}
    result = openwebui.reconcile(
        [_entry("dw-openai", "https://api.openai.com/v1")], [], desired
    )
    assert result.status == "clean"
    assert result.plan.entries[0]["action"] == "delete"


def test_unmanaged_and_envelope_preserved_and_only_changed_collection_written():
    class Client:
        def __init__(self):
            unmanaged = _entry("user", "http://user/v1")
            unmanaged["admin_metadata"] = {"model_order": ["user-model"]}
            self.openai = {
                "connections": [
                    unmanaged,
                    _entry("dw-openai", "https://api.openai.com/v1"),
                ]
            }
            self.ollama = {
                "items": [
                    _entry("dw-ollama", "http://localhost:11434", "ollama", "ollama")
                ]
            }
            self.writes = []

        def get_openai_config(self):
            return self.openai

        def get_ollama_config(self):
            return self.ollama

        def update_openai_config(self, payload):
            self.writes.append(("openai", payload))
            self.openai = payload

        def update_ollama_config(self, payload):
            self.writes.append(("ollama", payload))
            self.ollama = payload

    client = Client()
    desired = {
        "openai": [_entry("dw-openai", "https://api.openai.com/v1", key="rotated")],
        "ollama": [_entry("dw-ollama", "http://localhost:11434", "ollama", "ollama")],
    }
    openwebui.reconcile_via_api(client, desired)
    assert [name for name, _ in client.writes] == ["openai"]
    assert client.writes[0][1].keys() == {"connections"}
    preserved = client.writes[0][1]["connections"][0]
    assert preserved["config"]["prefix_id"] == "user"
    assert preserved["collection"] == "openai"
    assert preserved["admin_metadata"] == {"model_order": ["user-model"]}


def test_proxy_ownership_add_update_and_second_run_is_idempotent(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-key")
    monkeypatch.setattr(constants, "should_use_ollama_cloud_proxy", lambda: True)
    monkeypatch.setattr(constants, "check_ollama_daemon", lambda: (True, True))
    desired = openwebui.compute_desired_state()

    class Client:
        def __init__(self):
            self.openai, self.ollama = [], []
            self.writes = 0

        def get_openai_config(self):
            return self.openai

        def get_ollama_config(self):
            return self.ollama

        def update_openai_config(self, payload):
            self.openai = payload
            self.writes += 1

        def update_ollama_config(self, payload):
            self.ollama = payload
            self.writes += 1

    client = Client()
    first = openwebui.reconcile_via_api(client, desired)
    assert first.status == "clean"
    assert any(item["action"] == "add" for item in first.plan.entries)
    writes = client.writes
    rotated = {key: list(value) for key, value in desired.items()}
    rotated["openai"] = [dict(item, key="rotated-key") for item in rotated["openai"]]
    updated = openwebui.reconcile_via_api(client, rotated)
    assert any(item["action"] == "update" for item in updated.plan.entries)
    writes = client.writes
    second = openwebui.reconcile_via_api(client, rotated)
    assert second.status == "clean"
    assert not any(item["action"] != "keep" for item in second.plan.entries)
    assert client.writes == writes


def test_wrong_collection_is_collision():
    desired = {
        "openai": [_entry("dw-openai", "https://api.openai.com/v1")],
        "ollama": [],
    }
    current = [_entry("dw-openai", "https://api.openai.com/v1", collection="ollama")]
    result = openwebui.reconcile([], current, desired)
    assert result.status == "collision"


def test_normalization_fails_closed_for_unknown_shapes():
    with pytest.raises(openwebui.OpenWebUIError):
        openwebui.OpenWebUIClient.normalize({"unexpected": {}}, "openai")
    with pytest.raises(openwebui.OpenWebUIError):
        openwebui.OpenWebUIClient.normalize("not-json-object", "openai")


def test_live_0114_config_shapes_normalize_and_round_trip():
    openai = {
        "ENABLE_OPENAI_API": True,
        "OPENAI_API_BASE_URLS": ["http://openai/v1"],
        "OPENAI_API_KEYS": ["key"],
        "OPENAI_API_CONFIGS": {"0": {"prefix_id": "dw-openai", "enable": True}},
    }
    ollama = {
        "ENABLE_OLLAMA_API": True,
        "OLLAMA_BASE_URLS": ["http://ollama"],
        "OLLAMA_API_CONFIGS": {"0": {"key": "key", "prefix_id": "dw-ollama"}},
    }
    openai_entries, _ = openwebui.OpenWebUIClient.normalize(openai, "openai")
    ollama_entries, _ = openwebui.OpenWebUIClient.normalize(ollama, "ollama")
    assert openai_entries[0]["key"] == "key"
    assert ollama_entries[0]["config"] == {"prefix_id": "dw-ollama"}
    assert openwebui.OpenWebUIClient.payload(openai, openai_entries) == openai
    assert openwebui.OpenWebUIClient.payload(ollama, ollama_entries) == ollama


def test_desired_state_does_not_probe_without_ollama_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    probe = pytest.fail
    monkeypatch.setattr(
        constants, "check_ollama_daemon", lambda: probe("unexpected probe")
    )
    openwebui.compute_desired_state()


def test_ollama_probe_runs_at_most_once_per_compute(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-key")
    calls = []
    monkeypatch.setattr(constants, "should_use_ollama_cloud_proxy", lambda: True)
    monkeypatch.setattr(
        constants,
        "check_ollama_daemon",
        lambda: (calls.append("probe") or (True, False)),
    )
    openwebui.compute_desired_state()
    assert len(calls) <= 1


def test_snapshot_change_fails_closed():
    class Client:
        def __init__(self):
            self.calls = 0

        def get_openai_config(self):
            self.calls += 1
            return [] if self.calls == 1 else [_entry("user", "http://changed")]

        def get_ollama_config(self):
            return []

        def update_openai_config(self, payload):
            raise AssertionError("must not write")

        def update_ollama_config(self, payload):
            raise AssertionError("must not write")

    with pytest.raises(openwebui.OpenWebUIError):
        openwebui.reconcile_via_api(
            Client(),
            {
                "openai": [_entry("dw-openai", "https://api.openai.com/v1")],
                "ollama": [],
            },
        )


def test_mask_secret_and_env_emission():
    assert openwebui.mask_secret("short") == "<set>"
    assert openwebui.mask_secret("") == "<unset>"
    assert openwebui.mask_secret(None) == "<unset>"
    secret = "sk-super-secret-abcd"
    rendered = openwebui.emit_env(
        {"openai": [_entry("dw-openai", "http://x", key=secret)], "ollama": []},
        masked=True,
    )
    assert secret not in rendered and "sk…abcd" in rendered


def test_cloud_connections_require_keys(monkeypatch):
    assert not any(
        item["config"]["prefix_id"] == "dw-openai"
        for item in openwebui.compute_desired_state()["openai"]
    )
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    assert any(
        item["config"]["prefix_id"] == "dw-openai"
        for item in openwebui.compute_desired_state()["openai"]
    )


def test_cli_exit_codes(monkeypatch):
    module = _script()
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.setenv("OPENWEBUI_API_KEY", "key")
    monkeypatch.setattr(module, "load_env", lambda: True)
    monkeypatch.setattr(module.OpenWebUIClient, "health_check", lambda self: True)
    monkeypatch.setattr(
        module, "compute_desired_state", lambda: {"openai": [], "ollama": []}
    )
    monkeypatch.setattr(module.OpenWebUIClient, "get_openai_config", lambda self: [])
    monkeypatch.setattr(module.OpenWebUIClient, "get_ollama_config", lambda self: [])
    assert module.main.__name__ == "main"
    monkeypatch.setattr(module.sys, "argv", ["configure-openwebui.py", "--check"])
    assert module.main() == 0
    monkeypatch.setattr(
        module,
        "compute_desired_state",
        lambda: {
            "openai": [_entry("dw-openai", "https://api.openai.com/v1")],
            "ollama": [],
        },
    )
    assert module.main() == 1
    monkeypatch.setattr(module.sys, "argv", ["configure-openwebui.py", "--dry-run"])
    assert module.main() == 0
    monkeypatch.setattr(module.sys, "argv", ["configure-openwebui.py", "--invalid"])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
