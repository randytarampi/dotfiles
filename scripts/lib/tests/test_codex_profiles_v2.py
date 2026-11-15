import importlib.util
import hashlib
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "configure-codex.py"
SPEC = importlib.util.spec_from_file_location("configure_codex_v2_test", SCRIPT)
configure_codex = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure_codex)


def invoke(home, monkeypatch, enabled):
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DOTFILES_RUN_CODEX_SETUP", "1")
    monkeypatch.setenv("DOTFILES_CODEX_PROFILES_V2", "1" if enabled else "0")
    monkeypatch.delenv("DOTFILES_USE_LITELLM_PROXY", raising=False)
    monkeypatch.setattr(configure_codex, "load_env", lambda: None)
    monkeypatch.setattr(
        configure_codex, "resolve_ollama_cloud_model", lambda: ("model", "test")
    )
    monkeypatch.setattr(configure_codex, "resolve_local_model", lambda: "")


def test_gate_off_keeps_legacy_block(tmp_path, monkeypatch):
    invoke(tmp_path, monkeypatch, False)
    monkeypatch.setattr("sys.argv", ["configure-codex.py"])
    configure_codex.main()
    assert (
        "# BEGIN DOTFILES MANAGED PROFILES"
        in (tmp_path / ".codex/config.toml").read_text()
    )


def test_v2_sidecars_keep_unowned_config_unchanged(tmp_path, monkeypatch):
    config_dir = tmp_path / ".codex"
    config_dir.mkdir()
    original = 'model = "owner-model"\nmodel_provider = "owner-provider"\n[profiles.mine]\nmodel = "keep"\n'
    (config_dir / "config.toml").write_text(original)
    invoke(tmp_path, monkeypatch, True)
    monkeypatch.setattr("sys.argv", ["configure-codex.py"])
    configure_codex.main()
    updated = (config_dir / "config.toml").read_text()
    assert original in updated
    assert "DOTFILES MANAGED PROFILES" not in updated
    assert (config_dir / "meridian.config.toml").stat().st_mode & 0o777 == 0o600
    assert (
        'model_reasoning_effort = "high"'
        in (config_dir / "meridian.config.toml").read_text()
    )


def test_v2_refuses_foreign_sidecar(tmp_path, monkeypatch):
    config_dir = tmp_path / ".codex"
    config_dir.mkdir()
    foreign = config_dir / "meridian.config.toml"
    foreign.write_text("manual")
    invoke(tmp_path, monkeypatch, True)
    monkeypatch.setattr("sys.argv", ["configure-codex.py"])
    with pytest.raises(SystemExit):
        configure_codex.main()
    assert foreign.read_text() == "manual"


def test_v2_manifest_is_private_atomic_ownership_record(tmp_path, monkeypatch):
    invoke(tmp_path, monkeypatch, True)
    monkeypatch.setattr("sys.argv", ["configure-codex.py"])
    configure_codex.main()
    config_dir = tmp_path / ".codex"
    manifest = config_dir / configure_codex.V2_MANIFEST
    data = json.loads(manifest.read_text())
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert set(data["profiles"]) == {"meridian", "ollama-cloud", "copilot", "local"}
    for name, digest in data["profiles"].items():
        assert (
            hashlib.sha256(
                (config_dir / f"{name}.config.toml").read_bytes()
            ).hexdigest()
            == digest
        )


def test_v2_removes_only_digest_owned_stale_sidecars_with_private_backup(
    tmp_path, monkeypatch
):
    invoke(tmp_path, monkeypatch, True)
    monkeypatch.setattr("sys.argv", ["configure-codex.py"])
    configure_codex.main()
    config_dir = tmp_path / ".codex"
    stale = config_dir / "copilot.config.toml"
    content = stale.read_bytes()
    manifest = config_dir / configure_codex.V2_MANIFEST
    data = json.loads(manifest.read_text())
    previous = data["profiles"].copy()
    data["profiles"].pop("copilot")
    manifest.write_text(json.dumps(data))
    manifest.chmod(0o600)
    configure_codex._reconcile_v2_profiles(config_dir, {}, previous, False)
    assert not stale.exists()
    backups = list(config_dir.glob(".copilot.config.toml.backup-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == content
    assert backups[0].stat().st_mode & 0o777 == 0o600


def test_v2_modified_stale_sidecar_is_preserved(tmp_path):
    config_dir = tmp_path / ".codex"
    config_dir.mkdir()
    stale = config_dir / "copilot.config.toml"
    stale.write_text("manual edit")
    with pytest.raises(ValueError, match="refusing to remove modified"):
        configure_codex._reconcile_v2_profiles(
            config_dir, {}, {"copilot": "0" * 64}, False
        )
    assert stale.read_text() == "manual edit"



def test_v2_foreign_sidecar_rejects_before_root_or_other_profile_changes(
    tmp_path, monkeypatch
):
    directory = tmp_path / ".codex"
    directory.mkdir()
    root = directory / "config.toml"
    root.write_text('model = "owner-model"\n[profiles.owner]\nmodel = "owner"\n')
    original_root = root.read_bytes()
    foreign = directory / "local.config.toml"
    foreign.write_text("owner-kept")
    invoke(tmp_path, monkeypatch, True)
    monkeypatch.setattr("sys.argv", ["configure-codex.py"])
    with pytest.raises(SystemExit):
        configure_codex.main()
    assert root.read_bytes() == original_root
    assert foreign.read_text() == "owner-kept"
    assert not (directory / "meridian.config.toml").exists()
    assert not (directory / configure_codex.V2_MANIFEST).exists()


def test_v2_desired_model_change_updates_sidecar_not_root(tmp_path, monkeypatch):
    invoke(tmp_path, monkeypatch, True)
    monkeypatch.setattr("sys.argv", ["configure-codex.py"])
    configure_codex.main()
    directory = tmp_path / ".codex"
    root = directory / "config.toml"
    root_before = root.read_bytes()
    root_mtime = root.stat().st_mtime_ns
    manifest = directory / configure_codex.V2_MANIFEST
    original_manifest = json.loads(manifest.read_text())
    cloud = directory / "ollama-cloud.config.toml"
    original_cloud = cloud.read_bytes()
    monkeypatch.setattr(
        configure_codex, "resolve_ollama_cloud_model", lambda: ("new-model", "test")
    )
    configure_codex.main()
    assert root.read_bytes() == root_before
    assert root.stat().st_mtime_ns == root_mtime
    assert b"new-model" in cloud.read_bytes()
    assert cloud.read_bytes() != original_cloud
    assert json.loads(manifest.read_text())["profiles"]["ollama-cloud"] != (
        original_manifest["profiles"]["ollama-cloud"]
    )
    backups = list(directory.glob(".ollama-cloud.config.toml.backup-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original_cloud
    before = manifest.stat().st_mtime_ns
    configure_codex.main()
    assert len(list(directory.glob(".ollama-cloud.config.toml.backup-*"))) == 1
    assert manifest.stat().st_mtime_ns == before


def test_v2_late_foreign_sidecar_blocks_all_updates(tmp_path, monkeypatch):
    invoke(tmp_path, monkeypatch, True)
    monkeypatch.setattr("sys.argv", ["configure-codex.py"])
    configure_codex.main()
    directory = tmp_path / ".codex"
    root_before = (directory / "config.toml").read_bytes()
    manifest = directory / configure_codex.V2_MANIFEST
    manifest_before = manifest.read_bytes()
    cloud = directory / "ollama-cloud.config.toml"
    cloud_before = cloud.read_bytes()
    local = directory / "local.config.toml"
    local.write_text("owner-modified")
    monkeypatch.setattr(
        configure_codex, "resolve_ollama_cloud_model", lambda: ("new-model", "test")
    )
    with pytest.raises(SystemExit):
        configure_codex.main()
    assert (directory / "config.toml").read_bytes() == root_before
    assert manifest.read_bytes() == manifest_before
    assert cloud.read_bytes() == cloud_before
    assert local.read_text() == "owner-modified"
    assert not list(directory.glob(".ollama-cloud.config.toml.backup-*"))


def test_v2_dry_run_fails_on_foreign_state_without_writing(tmp_path, monkeypatch):
    directory = tmp_path / ".codex"
    directory.mkdir()
    root = directory / "config.toml"
    root.write_text('model = "owner"\n')
    foreign = directory / "meridian.config.toml"
    foreign.write_text("owner")
    invoke(tmp_path, monkeypatch, True)
    monkeypatch.setattr("sys.argv", ["configure-codex.py", "--dry-run"])
    with pytest.raises(SystemExit):
        configure_codex.main()
    assert root.read_text() == 'model = "owner"\n'
    assert foreign.read_text() == "owner"
    assert not (directory / configure_codex.V2_MANIFEST).exists()


def test_v2_rejects_unsafe_manifest_and_sidecar_modes(tmp_path, monkeypatch):
    invoke(tmp_path, monkeypatch, True)
    monkeypatch.setattr("sys.argv", ["configure-codex.py"])
    configure_codex.main()
    directory = tmp_path / ".codex"
    sidecar = directory / "meridian.config.toml"
    sidecar.chmod(0o644)
    with pytest.raises(SystemExit):
        configure_codex.main()
    sidecar.chmod(0o600)
    manifest = directory / configure_codex.V2_MANIFEST
    manifest.write_text("[]")
    manifest.chmod(0o600)
    with pytest.raises(SystemExit):
        configure_codex.main()
    assert sidecar.exists()
    assert manifest.read_text() == "[]"


def test_v2_private_backup_refuses_existing_destination(tmp_path):
    target = tmp_path / ".sidecar.backup"
    target.write_text("unmanaged backup")
    with pytest.raises(FileExistsError):
        configure_codex._atomic_private_write(
            target, b"replacement", create_only=True
        )
    assert target.read_text() == "unmanaged backup"
    assert list(tmp_path.glob("..sidecar.backup.*")) == []
