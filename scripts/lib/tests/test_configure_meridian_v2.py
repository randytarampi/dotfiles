import importlib.util
import json
import sys

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
    monkeypatch.setattr(
        configure_meridian.opencode_host,
        "resolve_opencode_host",
        lambda: ("known", 2),
    )
    monkeypatch.setattr(configure_meridian.shutil, "which", lambda _: "/bin/opencode")
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


def test_unqualified_v2_skips_without_touching_config(tmp_path, monkeypatch, caplog):
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
    monkeypatch.setattr(sys, "argv", [SCRIPT])
    configure_meridian.main()
    assert config.read_bytes() == original
    assert "2.2.5" in caplog.text


def test_v1_keeps_legacy_plugin_path_flow(tmp_path, monkeypatch):
    config = tmp_path / "opencode.json"
    config.write_text("{}")
    plugin = tmp_path / "meridian.ts"
    plugin.write_text("plugin")
    monkeypatch.setenv("OPENCODE_DIR", str(tmp_path))
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
