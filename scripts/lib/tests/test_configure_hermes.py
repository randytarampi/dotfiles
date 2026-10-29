import importlib.util
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[2] / "configure-hermes.py"
SPEC = importlib.util.spec_from_file_location("configure_hermes", SCRIPT)
assert SPEC and SPEC.loader
hermes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hermes)


def test_unmanaged_nested_providers_flow_is_preserved():
    source = "settings:\n  providers: {custom: [one, two]}\n"
    updated = hermes.reconcile(source, "served-alias", "http://127.0.0.1:4000/v1")
    assert updated.startswith(source)
    assert yaml.safe_load(updated)["settings"] == {
        "providers": {"custom": ["one", "two"]}
    }


def test_disabled_gate_is_no_write(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.delenv(hermes.GATE, raising=False)
    assert hermes.main(["--model", "served-alias"]) == 0
    assert not (tmp_path / "hermes/config.yaml").exists()


def test_scaffold_preserves_config_and_is_idempotent(monkeypatch, tmp_path):
    root = tmp_path / "hermes"
    root.mkdir()
    path = root / "config.yaml"
    original = "model:\n  provider: openrouter\nproviders:\n  custom:\n    name: User\nsettings:\n  keep: yes\n"
    path.write_text(original)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv(hermes.GATE, "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    assert (
        hermes.main(
            ["--model", "served-alias", "--endpoint", "http://127.0.0.1:4000/v1"]
        )
        == 0
    )
    written = path.read_text()
    parsed = yaml.safe_load(written)
    assert parsed["model"]["provider"] == "openrouter"
    assert parsed["providers"]["custom"]["name"] == "User"
    assert parsed["providers"][hermes.PROVIDER] == {
        "name": "dotfiles-litellm",
        "api": "http://127.0.0.1:4000/v1",
        "transport": "chat_completions",
        "key_env": "LITELLM_HERMES_KEY",
        "default_model": "served-alias",
    }
    assert "settings:\n  keep: yes\n" in written
    assert (
        hermes.main(
            ["--model", "served-alias", "--endpoint", "http://127.0.0.1:4000/v1"]
        )
        == 0
    )
    assert path.read_text() == written


@pytest.mark.parametrize(
    "source,model,endpoint",
    [
        ("[broken", "alias", "http://127.0.0.1:4000/v1"),
        ("", "", "http://127.0.0.1:4000/v1"),
        ("", "alias", "https://example.com/v1"),
        (
            "providers:\n  dotfiles-litellm:\n    name: User\n",
            "alias",
            "http://127.0.0.1:4000/v1",
        ),
    ],
)
def test_reconcile_rejects_unsafe_or_collision(source, model, endpoint):
    with pytest.raises(ValueError):
        hermes.reconcile(source, model, endpoint)


def test_dry_run_does_not_write(monkeypatch, tmp_path):
    root = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv(hermes.GATE, "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    assert hermes.main(["--model", "alias", "--dry-run"]) == 0
    assert not root.exists()


@pytest.mark.parametrize(
    "source",
    [
        "providers: {}\n",
        "providers: null\n",
        "providers: [one, two]\n",
        "providers:\n  dotfiles-litellm: null\n",
        "providers: &p\n  x: {}\nalias: *p\n",
        "providers:\n  x: {name: X}\n",
        "providers:\n  x: &x\n    name: X\n  y: *x\n",
        "providers:\n  x:\n    name: X\nproviders:\n  y: {}\n",
    ],
)
def test_unsupported_yaml_forms_refuse(source):
    with pytest.raises(ValueError):
        hermes.reconcile(source, "alias", "http://127.0.0.1:4000/v1")


def test_provider_insert_before_following_root_and_semantics():
    original = "model:\n  provider: openrouter\nproviders:\n  custom:\n    name: User\nsettings:\n  keep: value\n"
    updated = hermes.reconcile(original, "alias", "http://127.0.0.1:4000/v1")
    parsed = yaml.safe_load(updated)
    assert parsed["settings"] == {"keep": "value"}
    assert parsed["providers"]["dotfiles-litellm"]["name"] == "dotfiles-litellm"
    assert "settings:\n  keep: value\n" in updated


def test_benign_root_flow_collection_is_preserved():
    original = "fallback_providers: []\r\nmodel: alias\r\n"
    updated = hermes.reconcile(original, "served", "http://127.0.0.1:4000/v1")
    assert "fallback_providers: []\r\n" in updated


def test_parse_error_is_redacted():
    with pytest.raises(ValueError) as error:
        hermes.reconcile("private: 'DO_NOT_LEAK\n", "alias", "http://127.0.0.1:4000/v1")
    assert "DO_NOT_LEAK" not in str(error.value)


@pytest.mark.parametrize("gate", [hermes.GATE, "DOTFILES_USE_LITELLM_PROXY"])
def test_each_gate_independently_disables_writes(monkeypatch, tmp_path, gate):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv(hermes.GATE, "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.delenv(gate, raising=False)
    assert hermes.main(["--model", "alias"]) == 0
    assert not (tmp_path / "hermes").exists()


def test_no_backup_is_honoured(monkeypatch, tmp_path):
    root = tmp_path / "hermes"
    root.mkdir()
    path = root / "config.yaml"
    path.write_text("credential: dummy-secret\n")
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv(hermes.GATE, "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    assert hermes.main(["--model", "alias", "--no-backup"]) == 0
    assert not path.with_name("config.yaml.bak").exists()
    assert "dummy-secret" in path.read_text()


def test_symlink_config_refuses_without_target_read(monkeypatch, tmp_path):
    target = tmp_path / "private"
    target.write_text("private: DO_NOT_READ\n")
    root = tmp_path / "hermes"
    root.mkdir()
    (root / "config.yaml").symlink_to(target)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv(hermes.GATE, "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    assert hermes.main(["--model", "alias"]) == 1
    assert target.read_text() == "private: DO_NOT_READ\n"


def test_backup_private_and_conflicting_backup_preserved(monkeypatch, tmp_path):
    root = tmp_path / "hermes"
    root.mkdir()
    path = root / "config.yaml"
    original = "credential: dummy-secret\n"
    path.write_text(original)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv(hermes.GATE, "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    assert hermes.main(["--model", "alias"]) == 0
    backup = path.with_name("config.yaml.bak")
    assert backup.read_text() == original
    assert backup.stat().st_mode & 0o777 == 0o600
    path.write_text("credential: changed\n")
    before = path.read_bytes()
    assert hermes.main(["--model", "other"]) == 1
    assert path.read_bytes() == before
    assert backup.read_text() == original


def test_mixed_newlines_refuse_without_normalizing_user_bytes(monkeypatch, tmp_path):
    root = tmp_path / "hermes"
    root.mkdir()
    path = root / "config.yaml"
    original = b"model: alias\r\nfallback_providers: []\n"
    path.write_bytes(original)
    before = path.stat()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv(hermes.GATE, "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    assert hermes.main(["--model", "served"]) == 1
    assert path.read_bytes() == original
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert not path.with_name("config.yaml.bak").exists()


def test_failed_replace_cleans_temporary_and_keeps_original(monkeypatch, tmp_path):
    root = tmp_path / "hermes"
    root.mkdir()
    path = root / "config.yaml"
    original = "model:\n  provider: openrouter\n"
    path.write_text(original)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv(hermes.GATE, "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    monkeypatch.setattr(
        hermes.os,
        "replace",
        lambda *_: (_ for _ in ()).throw(OSError("replace failed")),
    )
    assert hermes.main(["--model", "alias"]) == 1
    assert path.read_text() == original
    assert sorted(p.name for p in root.iterdir()) == ["config.yaml", "config.yaml.bak"]


def test_successful_replacement_preserves_mode(monkeypatch, tmp_path):
    root = tmp_path / "hermes"
    root.mkdir()
    path = root / "config.yaml"
    path.write_text("model:\n  provider: openrouter\n")
    path.chmod(0o640)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv(hermes.GATE, "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    assert hermes.main(["--model", "alias"]) == 0
    assert path.stat().st_mode & 0o777 == 0o640


def test_cli_backup_and_updated_config_preserve_crlf_bytes(monkeypatch, tmp_path):
    root = tmp_path / "hermes"
    root.mkdir()
    path = root / "config.yaml"
    original = b"fallback_providers: []\r\nmodel: alias\r\n"
    path.write_bytes(original)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv(hermes.GATE, "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    assert hermes.main(["--model", "served"]) == 0
    backup = path.with_name("config.yaml.bak")
    assert backup.read_bytes() == original
    assert b"fallback_providers: []\r\n" in path.read_bytes()
    assert b"\n" not in path.read_bytes().replace(b"\r\n", b"")
    assert backup.stat().st_mode & 0o777 == 0o600
    stable = (path.read_bytes(), path.stat().st_mtime_ns)
    assert hermes.main(["--model", "served"]) == 0
    assert (path.read_bytes(), path.stat().st_mtime_ns) == stable
    assert backup.read_bytes() == original


def test_cli_refuses_non_utf8_without_creating_backup(monkeypatch, tmp_path):
    root = tmp_path / "hermes"
    root.mkdir()
    path = root / "config.yaml"
    original = b"private:\xff\r\n"
    path.write_bytes(original)
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setenv(hermes.GATE, "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    assert hermes.main(["--model", "alias"]) == 1
    assert path.read_bytes() == original
    assert not path.with_name("config.yaml.bak").exists()


def test_empty_and_null_existing_provider_map_refused():
    for source in ("providers: {}\n", "providers: null\n"):
        with pytest.raises(ValueError):
            hermes.reconcile(source, "alias", "http://127.0.0.1:4000/v1")


def test_symlink_ancestor_refused(monkeypatch, tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("HERMES_HOME", str(link / "nested"))
    monkeypatch.setenv(hermes.GATE, "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    assert hermes.main(["--model", "alias"]) == 1
    assert list(target.iterdir()) == []


def test_concurrent_change_preserved(monkeypatch, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("model: original\n")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv(hermes.GATE, "1")
    monkeypatch.setenv("DOTFILES_USE_LITELLM_PROXY", "1")
    original_fsync = hermes.os.fsync

    def changed(fd):
        original_fsync(fd)
        path.write_text("model: concurrent\n")

    monkeypatch.setattr(hermes.os, "fsync", changed)
    assert hermes.main(["--model", "alias"]) == 1
    assert path.read_text() == "model: concurrent\n"
    assert (tmp_path / "config.yaml.bak").read_text() == "model: original\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "config.yaml",
        "config.yaml.bak",
    ]
