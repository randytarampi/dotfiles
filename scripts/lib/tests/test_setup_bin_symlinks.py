"""Hermetic parity tests for setup-bin-symlinks.py."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def setup_script():
    spec = importlib.util.spec_from_file_location(
        "setup_bin_symlinks", ROOT / "setup-bin-symlinks.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source_dir(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "alpha.py").write_text("print('alpha')\n")
    (source / "beta.sh").write_text("#!/bin/sh\n")
    return source


def test_creates_scripts_and_command_symlinks(
    monkeypatch, tmp_path, source_dir, setup_script
):
    monkeypatch.setattr(
        setup_script.Path, "home", staticmethod(lambda: tmp_path / "home")
    )

    assert setup_script.setup_bin_symlinks(source_dir) == 0

    dotfiles = tmp_path / "home" / ".dotfiles"
    assert (dotfiles / "scripts").is_symlink()
    assert (dotfiles / "scripts").resolve() == source_dir
    assert (dotfiles / "bin" / "_dot--alpha").resolve() == source_dir / "alpha.py"
    assert (dotfiles / "bin" / "_dot--beta").resolve() == source_dir / "beta.sh"


def test_rerun_is_idempotent(setup_script, monkeypatch, tmp_path, source_dir):
    home = tmp_path / "home"
    monkeypatch.setattr(setup_script.Path, "home", staticmethod(lambda: home))

    setup_script.setup_bin_symlinks(source_dir)
    links_before = {
        path.name: path.readlink() for path in (home / ".dotfiles" / "bin").iterdir()
    }
    assert setup_script.setup_bin_symlinks(source_dir) == 0
    links_after = {
        path.name: path.readlink() for path in (home / ".dotfiles" / "bin").iterdir()
    }
    assert links_after == links_before


def test_existing_symlink_is_skipped_or_updated(
    monkeypatch, tmp_path, source_dir, setup_script
):
    home = tmp_path / "home"
    monkeypatch.setattr(setup_script.Path, "home", staticmethod(lambda: home))
    bin_dir = home / ".dotfiles" / "bin"
    bin_dir.mkdir(parents=True)
    same = bin_dir / "_dot--alpha"
    same.symlink_to(source_dir / "alpha.py")
    changed = bin_dir / "_dot--beta"
    changed.symlink_to(source_dir / "alpha.py")

    setup_script.setup_bin_symlinks(source_dir)

    assert same.resolve() == source_dir / "alpha.py"
    assert changed.resolve() == source_dir / "beta.sh"


def test_existing_file_matches_shell_failure(
    monkeypatch, tmp_path, source_dir, setup_script
):
    home = tmp_path / "home"
    monkeypatch.setattr(setup_script.Path, "home", staticmethod(lambda: home))
    bin_dir = home / ".dotfiles" / "bin"
    bin_dir.mkdir(parents=True)
    existing = bin_dir / "_dot--alpha"
    existing.write_text("not a symlink\n")

    with pytest.raises(FileExistsError):
        setup_script.setup_bin_symlinks(source_dir)
    assert existing.read_text() == "not a symlink\n"


def test_dry_run_writes_nothing(monkeypatch, tmp_path, source_dir, setup_script):
    home = tmp_path / "home"
    monkeypatch.setattr(setup_script.Path, "home", staticmethod(lambda: home))

    assert setup_script.setup_bin_symlinks(source_dir, dry_run=True) == 0
    assert not home.exists()
