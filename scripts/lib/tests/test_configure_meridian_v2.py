import importlib.util
import json
import sys

import pytest

SCRIPT = "scripts/configure-meridian.py"
spec = importlib.util.spec_from_file_location("configure_meridian", SCRIPT)
assert spec is not None and spec.loader is not None
configure_meridian = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = configure_meridian
spec.loader.exec_module(configure_meridian)


def test_v2_setup_invokes_meridian_with_resolved_binary(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("DOTFILES_RUN_MERIDIAN_SETUP", "1")
    monkeypatch.setenv("OPENCODE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        configure_meridian.opencode_host,
        "resolve_opencode_host",
        lambda: ("known", 2),
    )
    monkeypatch.setattr(
        configure_meridian.opencode_host,
        "host_binary",
        lambda _binary="opencode": "/bin/opencode",
    )
    monkeypatch.setattr(
        configure_meridian.opencode_host,
        "host_version_string",
        lambda _binary="opencode": "2.0.21",
    )
    monkeypatch.setattr(
        configure_meridian.subprocess,
        "run",
        lambda args, **kwargs: (
            calls.append(args)
            or type("Result", (), {"stdout": "", "stderr": "", "returncode": 0})()
        ),
    )
    monkeypatch.setattr(
        configure_meridian, "resolve_meridian_binary", lambda: "/bin/meridian"
    )
    monkeypatch.setattr(sys, "argv", [SCRIPT, "--no-backup"])
    configure_meridian.main()
    assert calls == [
        ["/bin/meridian", "setup", "--v2", "--opencode-bin", "/bin/opencode"],
    ]
    assert not (tmp_path / "opencode.json").exists()
    features = tmp_path / ".config" / "meridian" / "sdk-features.json"
    assert json.loads(features.read_text()) == {
        "opencode": {"codeSystemPrompt": False, "clientSystemPrompt": True}
    }


def test_v2_setup_failure_does_not_reconcile_features(tmp_path, monkeypatch):
    features = tmp_path / "sdk-features.json"
    features.write_text('{"keep":true}')
    before = features.read_bytes()
    monkeypatch.setenv("DOTFILES_RUN_MERIDIAN_SETUP", "1")
    monkeypatch.setenv("OPENCODE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        configure_meridian.opencode_host, "resolve_opencode_host", lambda: ("known", 2)
    )
    monkeypatch.setattr(
        configure_meridian.opencode_host, "host_version_string", lambda *_: "2.0.21"
    )
    monkeypatch.setattr(
        configure_meridian.opencode_host,
        "host_binary",
        lambda _binary="opencode": "/bin/opencode",
    )
    monkeypatch.setattr(
        configure_meridian, "resolve_meridian_binary", lambda: "/bin/meridian"
    )
    monkeypatch.setattr(
        configure_meridian.subprocess,
        "run",
        lambda *a, **k: type("Result", (), {"returncode": 1})(),
    )
    monkeypatch.setattr(sys, "argv", [SCRIPT])
    with pytest.raises(SystemExit):
        configure_meridian.main()
    assert features.read_bytes() == before


def test_sdk_feature_reconciliation_preserves_unrelated_values(tmp_path):
    path = tmp_path / "sdk-features.json"
    path.write_text('{"other":{"enabled":true},"opencode":{"custom":7}}')
    configure_meridian.reconcile_sdk_features(str(path))
    actual = json.loads(path.read_text())
    assert actual == {
        "other": {"enabled": True},
        "opencode": {
            "custom": 7,
            "codeSystemPrompt": False,
            "clientSystemPrompt": True,
        },
    }
    before = path.read_bytes()
    configure_meridian.reconcile_sdk_features(str(path))
    assert path.read_bytes() == before


def test_sdk_feature_dry_run_and_invalid_json(tmp_path):
    path = tmp_path / "sdk-features.json"
    path.write_text('{"unrelated":true}')
    before = path.read_bytes()
    configure_meridian.reconcile_sdk_features(str(path), dry_run=True)
    assert path.read_bytes() == before
    path.write_text("{")
    with pytest.raises(json.JSONDecodeError):
        configure_meridian.reconcile_sdk_features(str(path))


def test_unqualified_v2_hard_fails_without_touching_config(
    tmp_path, monkeypatch, caplog
):
    """Fail-closed per the migration plan: an unqualified OpenCode CLI stops
    deployment rather than degrading to warn-and-continue."""
    config = tmp_path / "opencode.json"
    config.write_text(json.dumps({"unchanged": True}))
    original = config.read_bytes()
    monkeypatch.setenv("DOTFILES_RUN_MERIDIAN_SETUP", "1")
    monkeypatch.setenv("OPENCODE_DIR", str(tmp_path))
    monkeypatch.setattr(
        configure_meridian.opencode_host,
        "resolve_opencode_host",
        lambda: ("known", 2),
    )
    monkeypatch.setattr(
        configure_meridian.opencode_host,
        "host_version_string",
        lambda _binary="opencode": "2.2.5",
    )
    # CI runners have no real opencode on PATH; the resolvability check must
    # pass so the qualification hard-failure is what is exercised.
    monkeypatch.setattr(
        configure_meridian.opencode_host,
        "host_binary",
        lambda _binary="opencode": "/bin/opencode",
    )
    monkeypatch.setattr(sys, "argv", [SCRIPT])
    with pytest.raises(SystemExit) as excinfo:
        configure_meridian.main()
    assert excinfo.value.code == 1
    assert config.read_bytes() == original
    assert "2.2.5" in caplog.text
    assert "failed hard" in caplog.text


def test_unprobeable_host_with_setup_gate_fails_before_writes(tmp_path, monkeypatch):
    config = tmp_path / "opencode.json"
    config.write_text('{"before":true}')
    before = config.read_bytes()
    monkeypatch.setenv("DOTFILES_RUN_MERIDIAN_SETUP", "1")
    monkeypatch.setenv("OPENCODE_DIR", str(tmp_path))
    monkeypatch.setattr(
        configure_meridian.opencode_host,
        "resolve_opencode_host",
        lambda: ("unknown", None),
    )
    monkeypatch.setattr(sys, "argv", [SCRIPT])
    with pytest.raises(SystemExit) as exc:
        configure_meridian.main()
    assert exc.value.code == 1
    assert config.read_bytes() == before


def test_gate_off_v2_flow_skips_without_setup(tmp_path, monkeypatch, caplog):
    config = tmp_path / "opencode.json"
    config.write_text(json.dumps({"unchanged": True}))
    original = config.read_bytes()
    monkeypatch.delenv("DOTFILES_RUN_MERIDIAN_SETUP", raising=False)
    monkeypatch.setenv("OPENCODE_DIR", str(tmp_path))
    monkeypatch.setattr(
        configure_meridian.opencode_host,
        "resolve_opencode_host",
        lambda: ("known", 2),
    )
    monkeypatch.setattr(sys, "argv", [SCRIPT])
    configure_meridian.main()
    assert config.read_bytes() == original
    assert "skipping Meridian v2 setup" in caplog.text


def test_v1_keeps_legacy_plugin_path_flow(tmp_path, monkeypatch):
    config = tmp_path / "opencode.json"
    config.write_text("{}")
    plugin = tmp_path / "meridian.ts"
    plugin.write_text("plugin")
    monkeypatch.setenv("OPENCODE_DIR", str(tmp_path))
    monkeypatch.delenv("DOTFILES_RUN_MERIDIAN_SETUP", raising=False)
    monkeypatch.setattr(
        configure_meridian.opencode_host,
        "resolve_opencode_host",
        lambda: ("known", 1),
    )
    monkeypatch.setattr(
        configure_meridian, "resolve_meridian_plugin_path", lambda: str(plugin)
    )
    monkeypatch.setattr(
        configure_meridian.urllib.request, "urlopen", lambda *a, **k: None
    )
    monkeypatch.setattr(sys, "argv", [SCRIPT, "--dry-run"])
    configure_meridian.main()
    assert json.loads(config.read_text()) == {}
