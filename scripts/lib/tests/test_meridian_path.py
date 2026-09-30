import os
import subprocess
import sys
from pathlib import Path

import meridian_path

PLUGIN = Path("@rynfar/meridian/plugin/meridian.ts")


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
