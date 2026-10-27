import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import venv

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/configure-litellm.py"
SPEC = importlib.util.spec_from_file_location("configure_litellm_readiness", SCRIPT)
assert SPEC and SPEC.loader
CONFIGURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIGURE)

_CLIENT_ENV = (
    "DOTFILES_RUN_LITELLM_SETUP",
    "DOTFILES_RUN_OPENCODE_SETUP",
    "DOTFILES_RUN_PI_SETUP",
    "DOTFILES_RUN_JUNIE_CLI_SETUP",
    "DOTFILES_RUN_OPENWEBUI_SETUP",
    "DOTFILES_RUN_CODEX_SETUP",
    "DOTFILES_RUN_CLAUDE_SETUP",
    "DOTFILES_RUN_COPILOT_SETUP",
    "DOTFILES_USE_LITELLM_PROXY",
    "LITELLM_CLIENT_KEY_FILE",
    "LITELLM_OPENCODE_KEY",
    "LITELLM_PI_KEY",
    "LITELLM_JUNIE_KEY",
    "LITELLM_OPENWEBUI_KEY",
    "LITELLM_CODEX_KEY",
    "LITELLM_CLAUDE_KEY",
    "LITELLM_COPILOT_CLI_KEY",
)


@pytest.fixture(autouse=True)
def isolate_client_environment(tmp_path, monkeypatch, provisioning_write_boundary):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in _CLIENT_ENV:
        monkeypatch.delenv(name, raising=False)


def test_writer_dispatch_patches_captured_registry_callable(monkeypatch, tmp_path):
    called = []
    monkeypatch.setitem(
        CONFIGURE.PROVISIONED_CLIENT_KEY_WRITERS,
        "opencode",
        lambda path, dry_run=False: called.append((path, dry_run)),
    )
    monkeypatch.setattr(CONFIGURE, "_write_opencode_key", lambda *_a, **_k: None)
    CONFIGURE._write_provisioned_client_keys(
        [{"client": "opencode"}], tmp_path / "service.env", False
    )
    assert called == [(tmp_path / "service.env", False)]


def test_misplaced_real_writer_is_blocked_before_write(tmp_path, monkeypatch):
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"unchanged")
    before = sentinel.stat()
    service_env = tmp_path / "service.env"
    service_env.write_text("LITELLM_OPENCODE_KEY='synthetic-fixture-key'\n")
    original_mkstemp = CONFIGURE.tempfile.mkstemp

    def misplaced_temp(*args, **kwargs):
        (tmp_path.parent / "forbidden-client-key").write_bytes(b"forbidden")
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr(CONFIGURE.tempfile, "mkstemp", misplaced_temp)
    with pytest.raises(AssertionError, match="outside provisioning fixture"):
        CONFIGURE._write_provisioned_client_keys(
            [{"client": "opencode"}], tmp_path / "service.env", False
        )
    assert sentinel.read_bytes() == b"unchanged"
    assert sentinel.stat() == before
    assert not (tmp_path.parent / "forbidden-client-key").exists()


@pytest.mark.parametrize("preserves_tier", [True, False])
def test_service_lane_validation_uses_managed_gateway_installation(
    tmp_path, monkeypatch, caplog, preserves_tier
):
    monkeypatch.setenv("HOME", str(tmp_path))
    gateway_venv = tmp_path / ".local/share/litellm/venv"
    venv.EnvBuilder(with_pip=False).create(gateway_venv)
    site_packages = next(gateway_venv.glob("lib/python*/site-packages"))
    package = site_packages / "litellm"
    package.mkdir()
    (package / "__init__.py").write_text(
        'def get_model_info(model):\n    return {"model": model}\n'
    )
    (package / "utils.py").write_text(
        "def get_optional_params(**kwargs):\n"
        '    assert kwargs["drop_params"] is True\n'
        + (
            '    return {"service_tier": "flex"}\n'
            if preserves_tier
            else "    return {}\n"
        )
    )
    metadata = site_packages / "litellm-1.102.1.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: litellm\nVersion: 1.102.1\n")
    # Neither the caller's modules nor PYTHONPATH may replace the gateway package.
    monkeypatch.setitem(sys.modules, "litellm", None)
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "litellm.py").write_text('raise RuntimeError("wrong installation")\n')
    monkeypatch.setenv("PYTHONPATH", str(shadow))

    assert CONFIGURE.validate_service_lane_runtime() is preserves_tier
    if preserves_tier:
        assert "VERIFIED: version=1.102.1" in caplog.text
        assert "pricing lookup uses top-level model_info" in caplog.text
    else:
        assert "UNVERIFIED" in caplog.text
        assert "service_tier was dropped" in caplog.text


def test_service_lane_validation_missing_gateway_is_unverified(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert CONFIGURE.validate_service_lane_runtime() is False
    assert "UNVERIFIED" in caplog.text
    assert str(tmp_path / ".local/share/litellm/venv/bin/python") in caplog.text


def test_service_lane_validation_timeout_is_unverified(monkeypatch, caplog):
    def timeout(command, **kwargs):
        assert kwargs["timeout"] == 30
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(CONFIGURE.subprocess, "run", timeout)
    assert CONFIGURE.validate_service_lane_runtime() is False
    assert "UNVERIFIED" in caplog.text
    assert "timed out" in caplog.text


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    ("gate", "lane", "runtime_valid", "expected_status"),
    [
        ("1", "flex", False, 1),
        ("1", "fast", False, 1),
        ("1", "flex", True, 0),
        ("0", "flex", False, 0),
        ("1", None, False, 0),
    ],
)
def test_generation_requires_valid_runtime_only_when_emitting_enabled_lanes(
    tmp_path, monkeypatch, dry_run, gate, lane, runtime_valid, expected_status
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_OPENAI_SERVICE_LANES", gate)
    monkeypatch.setattr(CONFIGURE, "load_env", lambda: True)
    monkeypatch.setattr(CONFIGURE, "_database_url_configured", lambda: True)
    monkeypatch.setattr(
        CONFIGURE, "validate_service_lane_runtime", lambda: runtime_valid
    )
    entry = {
        "model_name": "openai/gpt-6.1-sol" + (f"-{lane}" if lane else ""),
        "litellm_params": {"model": "openai/gpt-6.1-sol"},
    }
    if lane:
        entry["litellm_params"]["service_tier"] = lane
    previous_entries_expected = [{"model_name": "existing/model"}]
    monkeypatch.setattr(
        CONFIGURE.litellm_config,
        "read_previous_entries",
        lambda path: previous_entries_expected,
    )

    def compute_model_list(*, previous_entries=None):
        assert previous_entries == previous_entries_expected
        return [entry]

    monkeypatch.setattr(CONFIGURE, "compute_model_list", compute_model_list)
    monkeypatch.setattr(CONFIGURE.litellm_config, "last_generation_notes", [])
    monkeypatch.setattr(CONFIGURE, "_persist_generation_notes", lambda *_: None)
    monkeypatch.setattr(
        CONFIGURE, "_sync_runtime_rates_snapshot", lambda *_, **__: "unchanged"
    )
    monkeypatch.setattr(CONFIGURE, "provision_app_keys", lambda *_, **__: True)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)] + (["--dry-run"] if dry_run else []))
    config = tmp_path / ".local/share/litellm/config.yaml"
    config.parent.mkdir(parents=True)
    original = b"existing configuration\n"
    config.write_bytes(original)

    assert CONFIGURE.main() == expected_status
    if expected_status or dry_run:
        assert config.read_bytes() == original
        assert list(config.parent.iterdir()) == [config]
    else:
        assert config.read_text() == CONFIGURE.litellm_config.render_config(
            entries=[entry]
        )


def test_strict_provisioning_requires_enabled_key_authentication(tmp_path, monkeypatch):
    service_env = tmp_path / "service.env"
    service_env.write_text(
        "LITELLM_OPENCODE_KEY='secret-key'\n"
        "LITELLM_PI_KEY='pi-key'\nLITELLM_JUNIE_KEY='junie-key'\n"
        "LITELLM_OPENWEBUI_KEY='webui-key'\n"
    )
    monkeypatch.setenv("LITELLM_OPENCODE_KEY", "secret-key")
    monkeypatch.setenv("LITELLM_PI_KEY", "pi-key")
    monkeypatch.setenv("LITELLM_JUNIE_KEY", "junie-key")
    monkeypatch.setenv("LITELLM_OPENWEBUI_KEY", "webui-key")
    key_path = tmp_path / "clients/opencode.key"
    key_path.parent.mkdir()
    key_path.write_text("secret-key")
    monkeypatch.setenv("DOTFILES_RUN_OPENCODE_SETUP", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_RUN_PI_SETUP", "0")
    monkeypatch.setenv("DOTFILES_RUN_JUNIE_CLI_SETUP", "0")
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "0")
    monkeypatch.setenv("DOTFILES_RUN_CODEX_SETUP", "0")
    monkeypatch.setenv("DOTFILES_RUN_CLAUDE_SETUP", "0")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    calls = []

    def request(url, method, key, payload=None, timeout=30):
        calls.append((url, method))
        if "/key/list" in url:
            return {
                "keys": [
                    {"key_alias": alias}
                    for alias in ("opencode", "pi", "junie", "openwebui")
                ]
            }
        if url.endswith("/v1/models"):
            assert key == "secret-key"
            return {"data": []}
        return {"healthy": True}

    monkeypatch.setattr(CONFIGURE, "_request_json", request)
    monkeypatch.setattr(CONFIGURE, "_verify_app_key_hash", lambda *args, **kwargs: None)
    for client in ("opencode", "pi", "junie", "openwebui"):
        monkeypatch.setitem(
            CONFIGURE.PROVISIONED_CLIENT_KEY_WRITERS,
            client,
            lambda *args, **kwargs: None,
        )
    assert CONFIGURE.provision_app_keys("master-key", service_env, strict=True) is True
    urls = [url for url, _method in calls]
    assert (
        urls.index("http://127.0.0.1:4000/health/readiness")
        < urls.index("http://127.0.0.1:4000/key/list?return_full_object=true")
        < urls.index("http://127.0.0.1:4000/v1/models")
    )
    assert all(method == "GET" for _url, method in calls)


def test_strict_provisioning_fails_on_client_auth_failure(tmp_path, monkeypatch):
    service_env = tmp_path / "service.env"
    service_env.write_text("LITELLM_OPENCODE_KEY='secret-key'\n")
    key_path = tmp_path / "clients/opencode.key"
    key_path.parent.mkdir()
    key_path.write_text("secret-key")
    for gate in ("OPENCODE", "PI", "JUNIE_CLI", "OPENWEBUI"):
        monkeypatch.setenv(
            f"DOTFILES_RUN_{gate}_SETUP", "1" if gate == "OPENCODE" else "0"
        )
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")

    def request(url, method, key, payload=None, timeout=30):
        if "/key/list" in url:
            return {"keys": [{"key_alias": "opencode", "token": "hash"}]}
        if url.endswith("/v1/models") and key == "secret-key":
            raise OSError("unauthorized")
        return {"healthy": True}

    monkeypatch.setattr(CONFIGURE, "_request_json", request)
    monkeypatch.setattr(CONFIGURE, "_verify_app_key_hash", lambda *args, **kwargs: None)
    assert CONFIGURE.provision_app_keys("master-key", service_env, strict=True) is False


def test_strict_provisioning_fails_closed_on_readiness_transport_error(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DOTFILES_RUN_OPENCODE_SETUP", "1")
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_RUN_PI_SETUP", "0")
    monkeypatch.setenv("DOTFILES_RUN_JUNIE_CLI_SETUP", "0")
    monkeypatch.setenv("DOTFILES_RUN_OPENWEBUI_SETUP", "0")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")

    def request(url, method, key, payload=None, timeout=30):
        if url.endswith("/health/readiness"):
            raise TimeoutError("readiness timed out")
        raise AssertionError(f"unexpected request after readiness failure: {url}")

    monkeypatch.setattr(CONFIGURE, "_request_json", request)
    assert (
        CONFIGURE.provision_app_keys(
            "master-key", tmp_path / "service.env", strict=True
        )
        is False
    )


def test_require_ready_flag_is_registered():
    manifest = json.loads((ROOT / "scripts/lib/cli-contract.json").read_text())
    entry = next(
        item
        for item in manifest["scripts"]
        if item["path"] == "scripts/configure-litellm.py"
    )
    assert "--require-ready" in entry["accepted_flags"]


def test_require_ready_dry_run_is_rejected_before_any_work(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--require-ready", "--dry-run"],
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "DOTFILES_RUN_LITELLM_SETUP": "1",
            "LITELLM_DATABASE_URL": "postgresql://invalid/unused",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "cannot be combined" in result.stderr
    assert not list(tmp_path.rglob("*"))


def test_orchestration_order_and_failure_guards_are_present():
    wrapper = (ROOT / "scripts/configure-all.sh").read_text()
    template = (ROOT / ".chezmoiscripts/run_onchange_15-litellm.sh.tmpl").read_text()
    assert wrapper.index("health/readiness") < wrapper.index(
        '"$SCRIPT_DIR/configure-opencode.py"'
    )
    wrapper_lifecycle = wrapper[
        wrapper.index("# 2.5. Start and provision LiteLLM") : wrapper.index(
            "# Track OpenCode success"
        )
    ]
    assert wrapper_lifecycle.index(
        "configure-litellm-venv.py"
    ) < wrapper_lifecycle.index("litellm_service_restart")
    assert wrapper_lifecycle.index("--require-ready") < wrapper.index(
        '"$SCRIPT_DIR/configure-opencode.py"'
    )
    assert template.index("health/readiness") < template.index(
        '"$CONFIGURE_SCRIPT" --require-ready'
    )
    assert template.count('"$CONFIGURE_SCRIPT" --require-ready') == 1
    assert "step_skipped litellm" in wrapper_lifecycle
    assert 'if [[ "$COMMON_DRY_RUN" == "1" ]]' in wrapper_lifecycle
    assert "systemctl --user restart litellm.service" in wrapper_lifecycle
    assert template.count("for _ in {1..30}") == 2


def test_provisioning_second_pass_is_byte_stable(tmp_path, monkeypatch):
    service_env = tmp_path / "service.env"
    service_env.write_text("LITELLM_MASTER_KEY='master-key'\n")
    for alias, variable in CONFIGURE.APP_KEYS.items():
        service_env.write_text(service_env.read_text() + f"{variable}='{alias}-key'\n")
    client_dir = tmp_path / "clients"
    client_dir.mkdir()
    for alias in CONFIGURE.APP_KEYS:
        (client_dir / f"{alias}.key").write_text(f"{alias}-key")
    for gate in ("OPENCODE", "PI", "JUNIE_CLI", "OPENWEBUI"):
        monkeypatch.setenv(f"DOTFILES_RUN_{gate}_SETUP", "1")
    monkeypatch.setenv("DOTFILES_RUN_CLAUDE_SETUP", "0")
    monkeypatch.setattr(
        CONFIGURE,
        "_request_json",
        lambda url, method, key, payload=None, timeout=30: (
            {"keys": [{"key_alias": alias} for alias in CONFIGURE.APP_KEYS]}
            if "/key/list" in url
            else {"data": []}
        ),
    )
    monkeypatch.setenv("DOTFILES_RUN_LITELLM_SETUP", "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setattr(CONFIGURE, "_verify_app_key_hash", lambda *args, **kwargs: None)
    before = {
        path.name: path.read_bytes() for path in [service_env, *client_dir.iterdir()]
    }
    for _ in range(2):
        assert CONFIGURE.provision_app_keys("master-key", service_env, strict=True)
    after = {
        path.name: path.read_bytes() for path in [service_env, *client_dir.iterdir()]
    }
    assert after == before
