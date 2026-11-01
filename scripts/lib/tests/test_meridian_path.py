import os
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

import meridian_path

PLUGIN = Path("@rynfar/meridian/plugin/meridian.ts")
ROOT = Path(__file__).resolve().parents[3]
MERIDIAN_SPEC = importlib.util.spec_from_file_location(
    "configure_meridian_test", ROOT / "scripts/configure-meridian.py"
)
assert MERIDIAN_SPEC and MERIDIAN_SPEC.loader
configure_meridian = importlib.util.module_from_spec(MERIDIAN_SPEC)
MERIDIAN_SPEC.loader.exec_module(configure_meridian)


def run_configure_meridian(monkeypatch, tmp_path, host, gate="1"):
    home = tmp_path / "home"
    config_dir = home / ".config/opencode"
    config_dir.mkdir(parents=True)
    config = config_dir / "opencode.json"
    initial = {"plugin": ["user-plugin"]}
    config.write_text(json.dumps(initial))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENCODE_DIR", str(config_dir))
    monkeypatch.setenv("DOTFILES_RUN_MERIDIAN_SETUP", gate)
    monkeypatch.setattr(
        configure_meridian.opencode_host,
        "resolve_opencode_host",
        lambda: host,
    )
    monkeypatch.setattr(
        configure_meridian, "resolve_meridian_plugin_path", lambda: "/meridian.ts"
    )
    monkeypatch.setattr(
        configure_meridian.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(OSError()),
    )
    return config, initial


def test_meridian_v1_preserves_legacy_plugin_key_and_is_idempotent(
    monkeypatch, tmp_path
):
    config, initial = run_configure_meridian(monkeypatch, tmp_path, ("known", 1))
    del initial  # helper's contract: pre-existing entries preserved verbatim
    with mock.patch.object(sys, "argv", ["configure-meridian.py"]):
        configure_meridian.main()
    result = json.loads(config.read_text())
    assert result["plugin"] == ["user-plugin", "/meridian.ts"]
    assert "plugins" not in result
    with mock.patch.object(sys, "argv", ["configure-meridian.py"]):
        configure_meridian.main()
    assert json.loads(config.read_text()) == result


def test_meridian_unprobeable_fails_closed_or_skips_when_gate_off(
    monkeypatch, tmp_path
):
    config, initial = run_configure_meridian(monkeypatch, tmp_path, ("unknown", None))
    with pytest.raises(SystemExit) as error:
        with mock.patch.object(sys, "argv", ["configure-meridian.py"]):
            configure_meridian.main()
    assert error.value.code == 1
    assert json.loads(config.read_text()) == initial

    config, initial = run_configure_meridian(
        monkeypatch, tmp_path / "off", ("unknown", None), gate="0"
    )
    with mock.patch.object(sys, "argv", ["configure-meridian.py"]):
        configure_meridian.main()
    assert json.loads(config.read_text()) == initial


def test_meridian_v2_qualification_exact_membership(monkeypatch):
    # Hermetic: the desktop rule is explicitly darwin-arm64; the CLI rule is
    # platform-independent. Force the platform so CI (ubuntu) sees the same
    # matrix as macOS.
    monkeypatch.setattr(sys, "platform", "darwin")
    import platform as _platform

    monkeypatch.setattr(_platform, "machine", lambda: "arm64")
    # CLI form: only the qualified CLI versions count, regardless of host
    # platform.
    assert meridian_path.meridian_v2_qualification("2.0.16") is True
    assert meridian_path.meridian_v2_qualification("2.0.21") is True
    assert meridian_path.meridian_v2_qualification("2.0.31") is True
    assert meridian_path.meridian_v2_qualification("2.2.0") is False
    assert meridian_path.meridian_v2_qualification("2.2.5") is False
    assert meridian_path.meridian_v2_qualification("unknown") is False
    # Desktop form: the darwin-arm64 2.2.0 rule applies only there.
    assert (
        meridian_path.meridian_v2_qualification("2.2.0", binary_form="desktop") is True
    )
    assert (
        meridian_path.meridian_v2_qualification("2.0.21", binary_form="desktop")
        is False
    )


def test_meridian_v2_qualification_is_platform_hermetic(monkeypatch):
    # Regression guard for CI: a linux runner must see 2.2.0 as
    # unqualified for both forms while 2.0.21 stays qualified for the CLI.
    monkeypatch.setattr(sys, "platform", "linux")
    import platform as _platform

    monkeypatch.setattr(_platform, "machine", lambda: "x86_64")
    assert meridian_path.meridian_v2_qualification("2.0.21") is True
    assert meridian_path.meridian_v2_qualification("2.2.0") is False
    # The darwin-arm64 desktop rule needs darwin+arm64: linux stays False.
    assert (
        meridian_path.meridian_v2_qualification("2.2.0", binary_form="desktop") is False
    )


def make_root(tmp_path, relative):
    path = tmp_path / relative / PLUGIN
    path.parent.mkdir(parents=True)
    path.write_text("plugin")
    return path


def test_plugin_suffix_and_npm_root_resolution(tmp_path, monkeypatch):
    plugin = make_root(tmp_path, "node_modules")
    monkeypatch.setattr(meridian_path, "_global_npm_root", lambda: plugin.parents[3])
    monkeypatch.setattr(meridian_path, "_nvm_roots", lambda: [])
    monkeypatch.setenv("MERIDIAN_PLUGIN_PATH", "")
    assert meridian_path.resolve_meridian_plugin_path() == str(plugin)


def test_explicit_path_must_have_expected_suffix(tmp_path, monkeypatch):
    arbitrary = tmp_path / "hosts"
    arbitrary.write_text("not a plugin")
    monkeypatch.setenv("MERIDIAN_PLUGIN_PATH", str(arbitrary))
    monkeypatch.setattr(meridian_path, "_roots", lambda: [])
    assert meridian_path.resolve_meridian_plugin_path() is None


def test_binary_paths_support_lib_and_plain_node_modules(tmp_path, monkeypatch):
    lib_root = tmp_path / "version/lib/node_modules"
    plain_root = tmp_path / "prefix/node_modules"
    for root in (lib_root, plain_root):
        binary = root.parent / "bin/meridian"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
    monkeypatch.setattr(meridian_path, "_global_npm_root", lambda: lib_root)
    monkeypatch.setattr(meridian_path, "_nvm_roots", lambda: [])
    monkeypatch.setattr(meridian_path.shutil, "which", lambda name: None)
    assert meridian_path.resolve_meridian_binary() == str(
        lib_root.parent / "bin/meridian"
    )
    monkeypatch.setattr(meridian_path, "_global_npm_root", lambda: plain_root)
    assert meridian_path.resolve_meridian_binary() == str(
        plain_root.parent / "bin/meridian"
    )


def test_active_path_binary_precedes_nvm_fallback(tmp_path, monkeypatch):
    path_binary = tmp_path / "path/meridian"
    path_binary.parent.mkdir()
    path_binary.write_text("#!/bin/sh\n")
    path_binary.chmod(0o755)
    nvm_root = tmp_path / "version/lib/node_modules"
    nvm_binary = nvm_root.parent / "bin/meridian"
    nvm_binary.parent.mkdir(parents=True)
    nvm_binary.write_text("#!/bin/sh\n")
    nvm_binary.chmod(0o755)
    monkeypatch.setattr(
        meridian_path, "_global_npm_root", lambda: tmp_path / "empty/node_modules"
    )
    monkeypatch.setattr(meridian_path, "_nvm_roots", lambda: [nvm_root.parent.parent])
    monkeypatch.setattr(meridian_path.shutil, "which", lambda name: str(path_binary))
    assert meridian_path.resolve_meridian_binary() == str(path_binary)


def test_nvm_versions_sort_semantically_and_default_first(tmp_path, monkeypatch):
    versions = tmp_path / "versions/node"
    for version in ("v10.20.0", "v20.3.0", "v9.9.9"):
        (versions / version / "bin").mkdir(parents=True)
        (versions / version / "bin/node").write_text("")
    alias = versions.parent.parent / "alias"
    alias.mkdir()
    (alias / "default").write_text("v10.20.0\n")
    monkeypatch.setenv("NVM_DIR", str(tmp_path))
    roots = meridian_path._nvm_roots()
    assert [root.name for root in roots] == ["v10.20.0", "v20.3.0", "v9.9.9"]


def test_cli_stdout_is_only_resolved_path(tmp_path):
    plugin = make_root(tmp_path, "node_modules")
    env = dict(os.environ, MERIDIAN_PLUGIN_PATH=str(plugin), PYTHONPATH="scripts/lib")
    result = subprocess.run(
        [sys.executable, "-m", "meridian_path"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == f"{plugin}\n"
