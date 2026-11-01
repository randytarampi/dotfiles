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
        "DOTFILES_RUN_CLAUDE_SETUP",
        "DOTFILES_RUN_JETBRAINS_SETUP",
        "DOTFILES_RUN_VSCODE_SETUP",
        "DOTFILES_RUN_OPENWEBUI_SETUP",
        "DOTFILES_RUN_JUNIE_CLI_SETUP",
        "DOTFILES_RUN_CODEX_SETUP",
        "DOTFILES_RUN_COPILOT_SETUP",
        "DOTFILES_USE_LITELLM_PROXY",
        "LITELLM_CLIENT_KEY_FILE",
        "DOTFILES_RUN_CODEX_SETUP",
        "DOTFILES_RUN_CLAUDE_SETUP",
        "LITELLM_OPENCODE_KEY",
        "LITELLM_PI_KEY",
        "LITELLM_JUNIE_KEY",
        "LITELLM_OPENWEBUI_KEY",
        "LITELLM_CODEX_KEY",
        "LITELLM_CLAUDE_KEY",
        "LITELLM_COPILOT_CLI_KEY",
        "LITELLM_JETBRAINS_KEY",
        "LITELLM_VSCODE_KEY",
    ):
        monkeypatch.delenv(gate, raising=False)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch, provisioning_write_boundary):
    monkeypatch.setenv("HOME", str(tmp_path))


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
            return {"key": "synthetic-generated-key"}
        if url.endswith("/v1/models"):
            return {"data": []}
        return {}

    monkeypatch.setattr(configure_litellm, "_request_json", request)
    return calls


def test_classification_only_rows_never_generate_keys(monkeypatch, tmp_path):
    calls = _fake_gateway(monkeypatch)
    _enabled(monkeypatch, "DOTFILES_RUN_LITELLM_SETUP")
    monkeypatch.setenv("DOTFILES_RUN_CLAUDE_SETUP", "0")
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
    monkeypatch.setenv("DOTFILES_RUN_CLAUDE_SETUP", "0")
    assert configure_litellm.provision_app_keys("master", tmp_path / "service.env")
    generated = [
        call[2]["key_alias"] for call in calls if call[0].endswith("/key/generate")
    ]
    assert generated == []


def test_codex_requires_shared_litellm_setup_gate(monkeypatch, tmp_path):
    calls = _fake_gateway(monkeypatch)
    monkeypatch.setattr(configure_litellm.shutil, "which", lambda _name: "/bin/client")
    _enabled(monkeypatch, "DOTFILES_RUN_CODEX_SETUP", "DOTFILES_USE_LITELLM_PROXY")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "0")

    assert configure_litellm.provision_app_keys("master", tmp_path / "service.env")
    assert [call for call in calls if call[0].endswith("/key/generate")] == []

    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    assert configure_litellm.provision_app_keys("master", tmp_path / "service.env")
    assert [
        call[2]["key_alias"] for call in calls if call[0].endswith("/key/generate")
    ] == ["codex"]


def test_registry_truth_table_and_pending_rows_are_not_eligible(monkeypatch):
    rows = {row["client"]: row for row in litellm_config.CLIENT_REGISTRY}
    assert set(rows["pi"]["setup_gates"]) == {
        "DOTFILES_USE_LITELLM_PROXY=1",
        "DOTFILES_RUN_LITELLM_SETUP=1",
    }
    assert all(
        rows[name]["credential_policy"] == "no-credential"
        for name in ("cursor", "copilot")
    )
    assert rows["claude"]["classification"] == "eligible"
    assert rows["claude"]["credential_policy"] == "provision"
    assert rows["claude"]["setup_gates"] == [
        "DOTFILES_RUN_CLAUDE_SETUP=1",
        "DOTFILES_RUN_LITELLM_SETUP=1",
    ]
    assert rows["copilot-cli"]["credential_policy"] == "provision"
    assert rows["copilot-cli"]["transports"][:1] == ["OpenAI-compatible"]


@pytest.mark.parametrize(
    "setup,proxy,litellm,installed,expected",
    [
        (0, 1, 1, 1, False),
        (1, 0, 1, 1, False),
        (1, 1, 0, 1, False),
        (1, 1, 1, 0, False),
        (1, 1, 1, 1, True),
    ],
)
def test_copilot_cli_provisioning_requires_all_gates_and_install(
    monkeypatch, tmp_path, setup, proxy, litellm, installed, expected
):
    calls = _fake_gateway(monkeypatch)
    monkeypatch.setenv("DOTFILES_RUN_COPILOT_SETUP", "1" if setup else "0")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1" if proxy else "0")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1" if litellm else "0")
    monkeypatch.setattr(
        configure_litellm.shutil,
        "which",
        lambda name: "/bin/copilot" if name == "copilot" and installed else None,
    )
    assert configure_litellm.provision_app_keys("master", tmp_path / "service.env")
    aliases = [
        call[2]["key_alias"] for call in calls if call[0].endswith("/key/generate")
    ]
    assert ("copilot-cli" in aliases) is expected


def test_copilot_cli_writer_materializes_private_key(tmp_path):
    service = tmp_path / "service.env"
    service.write_text("LITELLM_COPILOT_CLI_KEY='copilot-secret'\n")
    configure_litellm._write_provisioned_client_keys(
        [
            next(
                row
                for row in litellm_config.CLIENT_REGISTRY
                if row["client"] == "copilot-cli"
            )
        ],
        service,
        dry_run=False,
    )
    key = tmp_path / "clients/copilot-cli.key"
    assert key.read_text() == "copilot-secret"
    assert key.stat().st_mode & 0o777 == 0o600


def test_client_key_writer_refuses_symlinked_ancestor_without_touching_target(
    tmp_path,
):
    outside = tmp_path / "outside"
    outside.mkdir()
    service = tmp_path / "linked" / "service.env"
    service.parent.symlink_to(outside, target_is_directory=True)
    service_file = outside / "service.env"
    service_file.write_text("LITELLM_CODEX_KEY='secret'\n")
    before = service_file.read_bytes()

    configure_litellm._write_codex_key(service)

    assert not (outside / "clients").exists()
    assert service_file.read_bytes() == before


def test_client_key_writer_refuses_destination_symlink_without_touching_target(
    tmp_path,
):
    service = tmp_path / "service.env"
    service.write_text("LITELLM_CODEX_KEY='secret'\n")
    outside = tmp_path / "outside.key"
    outside.write_bytes(b"preserve me")
    clients = tmp_path / "clients"
    clients.mkdir()
    (clients / "codex.key").symlink_to(outside)

    configure_litellm._write_codex_key(service)

    assert outside.read_bytes() == b"preserve me"
    assert (clients / "codex.key").is_symlink()


def test_client_key_writer_repeat_and_rotation_match_canonical_service_key(tmp_path):
    service = tmp_path / "service.env"
    key = tmp_path / "clients" / "codex.key"
    service.write_text("LITELLM_CODEX_KEY='first-secret'\n")

    configure_litellm._write_codex_key(service)
    first_bytes = key.read_bytes()
    configure_litellm._write_codex_key(service)
    assert key.read_bytes() == first_bytes

    service.write_text("LITELLM_CODEX_KEY='rotated-secret'\n")
    configure_litellm._write_codex_key(service)
    assert key.read_text(encoding="utf-8") == "rotated-secret"
    assert key.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "disabled_client,disabled_mode,expected",
    [
        (None, None, {"codex", "claude", "copilot-cli"}),
        ("codex", "gate", {"claude", "copilot-cli"}),
        ("claude", "gate", {"codex", "copilot-cli"}),
        ("copilot-cli", "gate", {"codex", "claude"}),
        ("codex", "absent", {"claude", "copilot-cli"}),
        ("claude", "absent", {"codex", "copilot-cli"}),
        ("copilot-cli", "absent", {"codex", "claude"}),
    ],
)
def test_codex_claude_copilot_gate_overlap_provisions_only_enabled_installed(
    monkeypatch, tmp_path, disabled_client, disabled_mode, expected
):
    calls = _fake_gateway(monkeypatch)
    _enabled(monkeypatch, "DOTFILES_RUN_LITELLM_SETUP", "DOTFILES_USE_LITELLM_PROXY")
    client_gates = {
        "codex": "DOTFILES_RUN_CODEX_SETUP",
        "claude": "DOTFILES_RUN_CLAUDE_SETUP",
        "copilot-cli": "DOTFILES_RUN_COPILOT_SETUP",
    }
    for gate in client_gates.values():
        monkeypatch.setenv(gate, "1")
    for gate in (
        "DOTFILES_RUN_OPENCODE_SETUP",
        "DOTFILES_RUN_PI_SETUP",
        "DOTFILES_RUN_OPENWEBUI_SETUP",
        "DOTFILES_RUN_JUNIE_CLI_SETUP",
    ):
        monkeypatch.setenv(gate, "0")
    if disabled_client and disabled_mode == "gate":
        monkeypatch.setenv(client_gates[disabled_client], "0")
    installed = {"codex": "codex", "claude": "claude", "copilot-cli": "copilot"}

    def which(binary):
        return (
            f"/fake-bin/{binary}"
            if any(
                client != disabled_client or disabled_mode != "absent"
                for client, name in installed.items()
                if name == binary
            )
            else None
        )

    monkeypatch.setattr(configure_litellm.shutil, "which", which)
    service = tmp_path / "service.env"
    assert configure_litellm.provision_app_keys("master", service)

    aliases = {
        call[2]["key_alias"] for call in calls if call[0].endswith("/key/generate")
    }
    assert aliases == expected
    for client in installed:
        key_file = tmp_path / "clients" / f"{client}.key"
        assert key_file.is_file() is (client in expected)


@pytest.mark.parametrize(
    "client,env_name",
    [
        ("opencode", "LITELLM_OPENCODE_KEY"),
        ("pi", "LITELLM_PI_KEY"),
        ("openwebui", "LITELLM_OPENWEBUI_KEY"),
        ("junie", "LITELLM_JUNIE_KEY"),
        ("codex", "LITELLM_CODEX_KEY"),
        ("claude", "LITELLM_CLAUDE_KEY"),
        ("copilot-cli", "LITELLM_COPILOT_CLI_KEY"),
    ],
)
def test_all_provisionable_clients_write_exact_private_key_path(
    tmp_path, client, env_name
):
    service = tmp_path / "service.env"
    service.write_text(f"{env_name}='client-secret'\n")
    row = next(row for row in litellm_config.CLIENT_REGISTRY if row["client"] == client)

    configure_litellm._write_provisioned_client_keys([row], service, dry_run=False)

    key = tmp_path / "clients" / f"{client}.key"
    assert key.read_text(encoding="utf-8") == "client-secret"
    assert key.stat().st_mode & 0o777 == 0o600
    configure_litellm._write_provisioned_client_keys([row], service, dry_run=False)
    assert key.read_bytes() == b"client-secret"
    service.write_text(f"{env_name}='rotated-secret'\n")
    configure_litellm._write_provisioned_client_keys([row], service, dry_run=False)
    assert key.read_bytes() == b"rotated-secret"
    assert key.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "client,binary,enable_gates",
    [
        ("codex", "codex", ("DOTFILES_RUN_CODEX_SETUP", "DOTFILES_USE_LITELLM_PROXY")),
        (
            "claude",
            "claude",
            ("DOTFILES_RUN_CLAUDE_SETUP", "DOTFILES_USE_LITELLM_PROXY"),
        ),
        (
            "copilot-cli",
            "copilot",
            ("DOTFILES_RUN_COPILOT_SETUP", "DOTFILES_USE_LITELLM_PROXY"),
        ),
        ("opencode", "opencode", ("DOTFILES_RUN_OPENCODE_SETUP",)),
        ("pi", "pi", ("DOTFILES_RUN_PI_SETUP", "DOTFILES_USE_LITELLM_PROXY")),
    ],
)
@pytest.mark.parametrize("disabled", ["none", "setup", "enable", "proxy", "installed"])
def test_cli_provisioning_requires_gates_and_installed_binary(
    monkeypatch, tmp_path, client, binary, enable_gates, disabled
):
    calls = _fake_gateway(monkeypatch)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    for gate in enable_gates:
        monkeypatch.setenv(gate, "1")
    installed = disabled != "installed"
    if disabled == "setup":
        monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "0")
    elif disabled == "enable":
        monkeypatch.setenv(enable_gates[0], "0")
    elif disabled == "proxy" and "DOTFILES_USE_LITELLM_PROXY" in enable_gates:
        monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    elif disabled == "proxy":
        monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "0")
    elif disabled == "installed":
        installed = False

    monkeypatch.setattr(
        configure_litellm.shutil,
        "which",
        lambda name: f"/fake-bin/{name}" if installed and name == binary else None,
    )
    assert configure_litellm.provision_app_keys("master", tmp_path / "service.env")
    aliases = [
        call[2]["key_alias"] for call in calls if call[0].endswith("/key/generate")
    ]
    expected = disabled == "none" or (
        disabled == "proxy" and client in {"opencode", "claude"}
    )
    assert (client in aliases) is expected


@pytest.mark.parametrize("enabled", [False, True])
def test_registry_gate_is_enforced_independently(monkeypatch, enabled):
    row = dict(
        next(r for r in litellm_config.CLIENT_REGISTRY if r["client"] == "codex")
    )
    row["setup_gates"] = ["TEST_REGISTRY_ONLY=1"]
    monkeypatch.setenv("TEST_REGISTRY_ONLY", "1" if enabled else "0")
    for gate in configure_litellm._CLIENT_ENABLE_GATES["codex"]:
        monkeypatch.setenv(gate, "1")
    monkeypatch.setattr(configure_litellm.shutil, "which", lambda _: "/fake/codex")
    accepted, reason = configure_litellm._client_provisioning_state(row)
    assert accepted is enabled
    assert reason == ("eligible" if enabled else "setup gate disabled")


def test_provisioning_authorities_have_exact_nine_client_parity():
    provisioned = {
        row["client"]
        for row in litellm_config.CLIENT_REGISTRY
        if row["credential_policy"].startswith("provision")
    }
    expected = {
        "opencode",
        "pi",
        "openwebui",
        "junie",
        "codex",
        "claude",
        "copilot-cli",
        "jetbrains",
        "vscode",
        "xcode",
    }
    assert provisioned == expected
    assert set(litellm_config.APP_KEYS) == expected
    assert set(configure_litellm.APP_KEYS) == expected
    assert set(configure_litellm.PROVISIONED_CLIENT_KEY_WRITERS) == expected
    assert set(configure_litellm._CLIENT_ENABLE_GATES) == expected
    assert {
        row["client"]
        for row in litellm_config.CLIENT_REGISTRY
        if row["client"] in expected
    } == expected
    for row in litellm_config.CLIENT_REGISTRY:
        if row["client"] in expected:
            assert row["env_name"] == litellm_config.APP_KEYS[row["client"]]
            assert configure_litellm.PROVISIONED_CLIENT_KEY_WRITERS[row["client"]]


@pytest.mark.parametrize(
    "client,app_name,bundle_id,gate",
    [
        (
            "jetbrains",
            "JetBrains Toolbox.app",
            "com.jetbrains.toolbox",
            "DOTFILES_RUN_JETBRAINS_SETUP",
        ),
        (
            "vscode",
            "Visual Studio Code.app",
            "com.microsoft.VSCode",
            "DOTFILES_RUN_VSCODE_SETUP",
        ),
        ("xcode", "Xcode.app", "com.apple.dt.Xcode", "DOTFILES_RUN_XCODE_SETUP"),
    ],
)
@pytest.mark.parametrize(
    "proxy,setup,installed",
    [(1, 1, True), (0, 1, True), (1, 0, True), (1, 1, False)],
)
def test_ide_keys_require_all_gates_and_verified_app_bundle(
    monkeypatch, tmp_path, client, app_name, bundle_id, gate, proxy, setup, installed
):
    import plistlib

    calls = _fake_gateway(monkeypatch)
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", str(proxy))
    monkeypatch.setenv(gate, str(setup))
    monkeypatch.setattr(
        configure_litellm,
        "_APPLICATION_ROOTS",
        (tmp_path / "System Applications", tmp_path / "Applications"),
    )
    bundle = tmp_path / "Applications" / app_name / "Contents"
    bundle.mkdir(parents=True)
    (bundle / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": bundle_id if installed else "wrong.id"})
    )
    monkeypatch.setenv("HOME", str(tmp_path))

    assert configure_litellm.provision_app_keys("master", tmp_path / "service.env")
    aliases = [
        call[2]["key_alias"] for call in calls if call[0].endswith("/key/generate")
    ]
    eligible = bool(proxy and setup and installed)
    assert (client in aliases) is eligible
    assert (tmp_path / "clients" / f"{client}.key").exists() is eligible


@pytest.mark.parametrize(
    "client,env_name",
    [
        ("jetbrains", "LITELLM_JETBRAINS_KEY"),
        ("vscode", "LITELLM_VSCODE_KEY"),
        ("xcode", "LITELLM_XCODE_KEY"),
    ],
)
def test_ide_writer_materializes_private_rotatable_key(tmp_path, client, env_name):
    service = tmp_path / "service.env"
    service.write_text(f"{env_name}='first-key'\n")
    writer = configure_litellm.PROVISIONED_CLIENT_KEY_WRITERS[client]
    writer(service)
    key = tmp_path / "clients" / f"{client}.key"
    assert key.read_text() == "first-key"
    assert key.stat().st_mode & 0o777 == 0o600
    service.write_text(f"{env_name}='rotated-key'\n")
    writer(service)
    assert key.read_text() == "rotated-key"
    assert key.stat().st_mode & 0o777 == 0o600


def test_ide_bundle_requires_directory_and_mapping_plist(monkeypatch, tmp_path):
    import plistlib

    app = tmp_path / "Visual Studio Code.app"
    contents = app / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps(["not", "a", "mapping"]))
    monkeypatch.setattr(configure_litellm, "_APPLICATION_ROOTS", (tmp_path,))
    assert not configure_litellm._ide_app_installed("vscode")


def test_junie_file_consumer_remains_materialized(monkeypatch, tmp_path):
    assert (
        next(row for row in litellm_config.CLIENT_REGISTRY if row["client"] == "junie")[
            "consumer_read_path"
        ]
        == "~/.local/share/litellm/clients/junie.key"
    )


def test_claude_provisions_only_with_gateway_and_client_gates(monkeypatch, tmp_path):
    calls = _fake_gateway(monkeypatch)
    monkeypatch.setattr(configure_litellm.shutil, "which", lambda _name: "/bin/claude")
    _enabled(monkeypatch, "DOTFILES_RUN_LITELLM_SETUP")
    monkeypatch.setenv("DOTFILES_RUN_CLAUDE_SETUP", "0")
    assert configure_litellm.provision_app_keys("master", tmp_path / "service.env")
    assert not any(
        call[0].endswith("/key/generate") and call[2]["key_alias"] == "claude"
        for call in calls
    )

    calls.clear()
    _enabled(monkeypatch, "DOTFILES_RUN_LITELLM_SETUP", "DOTFILES_RUN_CLAUDE_SETUP")
    service = tmp_path / "service.env"
    assert configure_litellm.provision_app_keys("master", service)
    assert any(
        call[0].endswith("/key/generate") and call[2]["key_alias"] == "claude"
        for call in calls
    )
    key_file = service.parent / "clients/claude.key"
    assert key_file.is_file() and key_file.stat().st_mode & 0o777 == 0o600
    assert key_file.read_text().strip()


def test_claude_shared_gateway_gate_is_independently_required(monkeypatch, tmp_path):
    calls = _fake_gateway(monkeypatch)
    monkeypatch.setattr(configure_litellm.shutil, "which", lambda _name: "/bin/claude")
    _enabled(monkeypatch, "DOTFILES_RUN_CLAUDE_SETUP", "DOTFILES_USE_LITELLM_PROXY")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "0")

    assert configure_litellm.provision_app_keys("master", tmp_path / "service.env")
    assert [call for call in calls if call[0].endswith("/key/generate")] == []

    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    assert configure_litellm.provision_app_keys("master", tmp_path / "service.env")
    assert [
        call[2]["key_alias"] for call in calls if call[0].endswith("/key/generate")
    ] == ["claude"]


def test_strict_provisioning_bypasses_install_probe_but_not_client_gates(
    monkeypatch, tmp_path
):
    calls = _fake_gateway(monkeypatch)
    _enabled(monkeypatch, "DOTFILES_RUN_LITELLM_SETUP", "DOTFILES_RUN_CODEX_SETUP")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setattr(configure_litellm.shutil, "which", lambda _name: None)

    assert configure_litellm.provision_app_keys(
        "master", tmp_path / "service.env", strict=True
    )
    assert [
        call[2]["key_alias"] for call in calls if call[0].endswith("/key/generate")
    ] == ["codex"]

    calls.clear()
    monkeypatch.setenv("DOTFILES_RUN_CODEX_SETUP", "0")
    assert configure_litellm.provision_app_keys(
        "master", tmp_path / "service.env", strict=True
    )
    assert [call for call in calls if call[0].endswith("/key/generate")] == []


def test_claude_strict_key_check_uses_attribution_and_labels_unqualified_messages(
    monkeypatch, tmp_path, caplog
):
    calls = []

    def request(url, method, key, payload=None):
        calls.append((url, method, key))
        return {"data": []}

    monkeypatch.setattr(configure_litellm, "_request_json", request)
    row = next(
        row for row in litellm_config.CLIENT_REGISTRY if row["client"] == "claude"
    )
    key_path = tmp_path / "clients/claude.key"
    key_path.parent.mkdir()
    key_path.write_text("dummy-key")
    key_path.chmod(0o600)
    assert configure_litellm._verify_provisioned_keys(
        [row], "http://gateway", tmp_path / "service.env"
    )
    assert calls == [("http://gateway/v1/models", "GET", "dummy-key")]
    assert "Messages transport remains unqualified" in caplog.text


def test_claude_strict_authentication_failure_fails_readiness(monkeypatch, tmp_path):
    calls = []

    def request(url, method, key, payload=None):
        calls.append(url)
        if key == "bad-key":
            raise OSError("401")
        return {"data": []}

    monkeypatch.setattr(configure_litellm, "_request_json", request)
    row = next(
        row for row in litellm_config.CLIENT_REGISTRY if row["client"] == "claude"
    )
    key_path = tmp_path / "clients/claude.key"
    key_path.parent.mkdir()
    key_path.write_text("bad-key")
    monkeypatch.setenv("LITELLM_CLAUDE_KEY", "bad-key")
    assert not configure_litellm._verify_provisioned_keys(
        [row], "http://gateway", tmp_path / "service.env"
    )
    assert calls == ["http://gateway/v1/models"]


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
            assert payload is not None
            return {"key": f"key-for-{payload['key_alias']}"}
        if url.endswith("/v1/models"):
            expected = key.rsplit("-", 1)[-1]
            if expected == "claude":
                return {"data": []}
            if expected not in ("pi", "opencode"):
                raise AssertionError("unexpected client identity")
            return {"data": [], "authenticated_as": expected}
        return {}

    monkeypatch.setattr(configure_litellm, "_request_json", request)
    monkeypatch.setattr(configure_litellm.shutil, "which", lambda _name: "/bin/client")
    monkeypatch.setenv("DOTFILES_RUN_CLAUDE_SETUP", "0")
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
    monkeypatch.setenv("DOTFILES_RUN_CLAUDE_SETUP", "0")
    monkeypatch.setenv("LITELLM_OPENCODE_KEY", "existing-key")
    client_path = tmp_path / "clients" / "opencode.key"
    client_path.parent.mkdir(mode=0o700)
    client_path.write_text("existing-key", encoding="utf-8")
    os.chmod(client_path, 0o600)
    service = tmp_path / "service.env"
    service.write_text(
        "\n".join(
            ("LITELLM_OPENCODE_KEY='existing-key'", "LITELLM_PI_KEY='existing-pi-key'")
        )
        + "\n",
        encoding="utf-8",
    )
    records = ["opencode", "pi"]
    original_request = configure_litellm._request_json

    def existing_aliases(url, method, key, payload=None):
        if url.endswith("/key/list?return_full_object=true"):
            return {"keys": [{"key_alias": alias} for alias in records]}
        return original_request(url, method, key, payload)

    monkeypatch.setattr(configure_litellm, "_request_json", existing_aliases)
    assert configure_litellm.provision_app_keys("master", service)
    before = client_path.read_bytes()
    pi_path = tmp_path / "clients/pi.key"
    assert pi_path.read_text(encoding="utf-8") == "existing-pi-key"
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
