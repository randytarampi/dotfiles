import importlib.util
import os
import subprocess
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


def test_empty_prefix_id_current_entries_do_not_crash():
    current = [_entry("", "https://api.openai.com/v1", key="seeded-key")]
    desired = {
        "openai": [_entry("dw-openai", "https://api.openai.com/v1")],
        "ollama": [],
    }
    result = openwebui.reconcile([], current, desired)
    assert result.status == "clean"
    actions = {
        item["entry"]["config"].get("prefix_id"): item["action"]
        for item in result.plan.entries
    }
    assert actions[""] == "keep"
    assert actions["dw-openai"] == "add"


def test_null_prefix_id_current_entry_does_not_crash():
    current = [_entry("", "https://api.openai.com/v1", key="seeded-key")]
    current[0]["config"]["prefix_id"] = None
    result = openwebui.reconcile(
        current,
        [],
        {"openai": [], "ollama": []},
    )
    assert result.status == "clean"
    assert result.plan.entries[0]["action"] == "keep"


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


def test_signin_fallback_retries_with_session_token(monkeypatch):
    import io
    import json
    import urllib.error
    import urllib.request

    calls = []

    class FakeResponse:
        def __init__(self, body):
            self._body = json.dumps(body).encode()

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        calls.append((request.full_url, dict(request.headers).get("Authorization", "")))
        if "/api/v1/auths/signin" in request.full_url:
            return FakeResponse({"token": "jwt-token"})
        auth = dict(request.headers).get("Authorization", "")
        if auth == "Bearer jwt-token":
            return FakeResponse({"ok": True})
        raise urllib.error.HTTPError(
            request.full_url, 401, "unauthorized", {}, io.BytesIO(b"")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = openwebui.OpenWebUIClient(
        "http://x",
        "sk-stale",
        admin_credentials={"email": "a@b", "password": "pw"},
    )
    assert client.get_openai_config() == {"ok": True}
    signin_calls = [c for c in calls if "/api/v1/auths/signin" in c[0]]
    assert len(signin_calls) == 1
    # credentials must never be sent as headers
    assert all("a@b" not in (c[1] or "") for c in calls)


def test_signin_fallback_absent_credentials_fails_closed(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(
            request.full_url, 401, "unauthorized", {}, io.BytesIO(b"")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = openwebui.OpenWebUIClient("http://x", "sk-stale")
    with pytest.raises(openwebui.AuthError):
        client.get_openai_config()
    # no signin attempted without credentials; no retry loop
    assert all("/api/v1/auths/signin" not in url for url in calls)


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


def test_terminal_helpers_error_paths_are_hermetic(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "openwebui_service.sh"
    env_file = tmp_path / "terminal.env"
    sync_script = (
        f"source {helper!s}; openssl() {{ return 1; }}; "
        f"openwebui_terminal_service_env_sync {env_file!s}"
    )
    sync = subprocess.run(["bash", "-c", sync_script], capture_output=True)
    assert sync.returncode == 1
    start_script = f"HOME={tmp_path!s}; export HOME; source {helper!s}; openwebui_terminal_service_start"
    start = subprocess.run(["bash", "-c", start_script], capture_output=True)
    assert start.returncode == 1


def test_terminal_registration_collision_and_shape_fail_closed(monkeypatch, tmp_path):
    module = _script()
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_TERMINAL_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(openwebui.OpenWebUIError):
        module._reconcile_terminal(
            type(
                "Client",
                (),
                {
                    "get_terminal_servers_config": lambda self: {
                        "TERMINAL_SERVER_CONNECTIONS": [
                            {
                                "id": "dotfiles-open-terminal",
                                "url": "http://wrong",
                                "auth_type": "bearer",
                            }
                        ]
                    }
                },
            )()
        )
    with pytest.raises(openwebui.OpenWebUIError):
        module._reconcile_terminal(
            type("Client", (), {"get_terminal_servers_config": lambda self: []})()
        )


def test_terminal_registration_absence_and_snapshot_change(monkeypatch, tmp_path):
    module = _script()
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_TERMINAL_SETUP", "0")
    monkeypatch.setenv("HOME", str(tmp_path))
    env_path = tmp_path / ".local/share/openwebui/terminal.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("OPEN_TERMINAL_API_KEY='terminal-key'\n")

    class Client:
        def __init__(self):
            self.connections = [
                {
                    "id": "dotfiles-open-terminal",
                    "url": "http://127.0.0.1:8123",
                    "auth_type": "bearer",
                }
            ]
            self.writes = 0

        def get_terminal_servers_config(self):
            return {"TERMINAL_SERVER_CONNECTIONS": self.connections}

        def update_terminal_servers_config(self, payload):
            self.connections = payload["TERMINAL_SERVER_CONNECTIONS"]
            self.writes += 1

    client = Client()
    assert module._reconcile_terminal(client) == 0
    assert client.connections == []
    assert client.writes == 1

    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_TERMINAL_SETUP", "1")
    client.connections = []
    client.writes = 0
    calls = iter(
        [
            {"TERMINAL_SERVER_CONNECTIONS": []},
            {"TERMINAL_SERVER_CONNECTIONS": [{"id": "other"}]},
        ]
    )
    client.get_terminal_servers_config = lambda: next(calls)
    with pytest.raises(openwebui.OpenWebUIError):
        module._reconcile_terminal(client)


def test_service_env_fallback_reads_admin_credentials(monkeypatch, tmp_path):
    module = _script()
    service_env = tmp_path / "service.env"
    service_env.write_text(
        "OPENWEBUI_API_KEY='service-key'\nWEBUI_ADMIN_EMAIL='admin@example'\nWEBUI_ADMIN_PASSWORD='pw'\n"
    )
    monkeypatch.setattr(module.os.path, "expanduser", lambda _: str(service_env))
    monkeypatch.delenv("OPENWEBUI_API_KEY", raising=False)
    assert module._openwebui_service_value("OPENWEBUI_API_KEY") == "service-key"
    assert module._openwebui_service_value("WEBUI_ADMIN_EMAIL") == "admin@example"


def test_mcp_registry_selects_http_and_excludes_stdio_sse(tmp_path):
    module = _script()
    (tmp_path / "configs/mcp").mkdir(parents=True)
    (tmp_path / "configs/mcp/http.json").write_text(
        '{"name":"http","type":"url","url":"https://example.test/mcp"}'
    )
    (tmp_path / "configs/mcp/stdio.json").write_text(
        '{"name":"stdio","type":"command","command":"tool"}'
    )
    (tmp_path / "configs/mcp/sse.json").write_text(
        '{"name":"sse","type":"url","url":"sse://example.test"}'
    )
    selected = module._streamable_mcp_connections(tmp_path)
    assert [item["info"]["id"] for item in selected] == ["dotfiles-mcp-http"]


def test_mcp_registry_resolves_or_skips_header_placeholders(tmp_path, monkeypatch):
    root = tmp_path / "configs/mcp"
    root.mkdir(parents=True)
    (root / "missing.json").write_text(
        '{"name":"missing","type":"url","url":"https://missing.test/mcp","headers":{"Authorization":"Bearer ${MISSING_MCP_TOKEN}"}}'
    )
    (root / "present.json").write_text(
        '{"name":"present","type":"url","url":"https://present.test/mcp","headers":{"Authorization":"Bearer ${GH_TOKEN}"}}'
    )
    monkeypatch.setenv("GH_TOKEN", "token")
    module = _script()
    selected = module._streamable_mcp_connections(tmp_path)
    assert [item["info"]["id"] for item in selected] == ["dotfiles-mcp-present"]
    assert selected[0]["headers"]["Authorization"] == "Bearer token"


def test_mcp_registration_collision_and_clean_preservation(monkeypatch, tmp_path):
    module = _script()
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.setenv("OPENWEBUI_API_KEY", "key")
    monkeypatch.setenv("HOME", str(tmp_path))
    desired = module._streamable_mcp_connections(Path(__file__).resolve().parents[3])
    assert desired

    class Client:
        def __init__(self, current):
            self.current, self.writes = current, 0

        def get_tool_servers_config(self):
            return {"TOOL_SERVER_CONNECTIONS": self.current}

        def update_tool_servers_config(self, payload):
            self.current = payload["TOOL_SERVER_CONNECTIONS"]
            self.writes += 1

    collision = dict(desired[0], url="https://wrong.example")
    with pytest.raises(openwebui.OpenWebUIError):
        module._reconcile_mcp(Client([collision]), dry_run=False)
    unmanaged = {"info": {"id": "admin"}, "url": "https://admin.example"}
    client = Client([unmanaged] + desired)
    assert module._reconcile_mcp(client) == 0
    assert client.writes == 0


def test_mcp_removed_registry_state_is_deleted(monkeypatch, tmp_path):
    module = _script()
    monkeypatch.setenv("HOME", str(tmp_path))
    state = tmp_path / ".local/share/openwebui/data"
    state.mkdir(parents=True)
    (state / "managed-mcp.json").write_text(
        '{"connections":{"dotfiles-mcp-old":"https://old.example"}}'
    )

    class Client:
        def __init__(self):
            self.current = [
                {"info": {"id": "dotfiles-mcp-old"}, "url": "https://old.example"}
            ]
            self.writes = 0

        def get_tool_servers_config(self):
            return {"TOOL_SERVER_CONNECTIONS": self.current}

        def update_tool_servers_config(self, payload):
            self.current = payload["TOOL_SERVER_CONNECTIONS"]
            self.writes += 1

    monkeypatch.setattr(module, "_streamable_mcp_connections", lambda: [])
    client = Client()
    assert module._reconcile_mcp(client) == 0
    assert client.current == [] and client.writes == 1


def test_catalogue_reconciliation_is_merge_only(monkeypatch, tmp_path):
    module = _script()
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    curated = ["openai/gpt-x", "anthropic/claude-y"]

    monkeypatch.setattr(module, "_curated_cloud_models", lambda: curated)
    live = {"data": [{"id": mid} for mid in curated]}

    class Client:
        def __init__(self, current):
            self.current, self.writes = current, 0

        def get_models(self):
            return live

        def get_models_config(self):
            return dict(self.current)

        def update_models_config(self, config):
            self.current = dict(config)
            self.writes += 1

    user_default = "user-favorite-model"
    user_pinned = "user-pinned-model,another-pin"
    user_order = ["user-first", "user-second"]
    client = Client(
        {
            "DEFAULT_MODELS": user_default,
            "DEFAULT_PINNED_MODELS": user_pinned,
            "MODEL_ORDER_LIST": list(user_order),
        }
    )
    assert module._reconcile_catalogue(client) == 0
    assert client.writes == 1
    # user entries preserved verbatim and FIRST; managed entries appended
    # (order within the appended set is the reconciler's, not the caller's)
    assert client.current["DEFAULT_MODELS"].split(",")[0] == user_default
    assert set(client.current["DEFAULT_MODELS"].split(",")[1:]) == set(curated)
    assert client.current["DEFAULT_PINNED_MODELS"].split(",")[0] == "user-pinned-model"
    assert user_order[0] == client.current["MODEL_ORDER_LIST"][0]
    assert set(curated).issubset(set(client.current["MODEL_ORDER_LIST"]))
    # idempotent second run: no write
    assert module._reconcile_catalogue(client) == 0
    assert client.writes == 1
    monkeypatch.setattr(module, "_curated_cloud_models", lambda: [])
    assert module._reconcile_catalogue(client) == 0
    assert client.writes == 2
    assert client.current["DEFAULT_MODELS"] == user_default
    assert client.current["MODEL_ORDER_LIST"] == user_order


def test_computer_helpers_env_shape_and_missing_plist(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "openwebui_service.sh"
    env_path = tmp_path / "cptr" / "service.env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("CPTR_DATA_DIR=/tmp\nOPENWEBUI_COMPUTER_PORT=8124\n")
    script = (
        f"HOME={tmp_path!s}; export HOME; source {helper!s}; "
        f"openwebui_computer_service_env_sync {env_path!s}; "
        f"test -s {env_path!s}; grep -q '^CPTR_DATA_DIR=' {env_path!s}; "
        "openwebui_computer_service_start"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True)
    assert result.returncode == 1
    assert "CPTR_DATA_DIR=" in env_path.read_text()
    assert "OPENWEBUI_COMPUTER_PORT=8124" in env_path.read_text()


def test_configure_all_subgate_cleanup_blocks_are_siblings():
    source = (
        Path(__file__).resolve().parents[3] / "scripts/configure-all.sh"
    ).read_text()
    terminal_start = source.index('if [[ "${DOTFILES_RUN_OPENWEBUI_TERMINAL_SETUP')
    computer_start = source.index(
        'if [[ "${DOTFILES_RUN_OPENWEBUI_COMPUTER_SETUP', terminal_start
    )
    terminal_close = source.rfind("\n  fi", terminal_start, computer_start)
    assert terminal_close > terminal_start
    assert computer_start > terminal_close
    assert "openwebui_terminal_service_stop" in source[terminal_start:computer_start]
    assert "openwebui_computer_service_stop" in source[computer_start:]
    assert {(False, False), (False, True), (True, False), (True, True)} == {
        (False, False),
        (False, True),
        (True, False),
        (True, True),
    }
