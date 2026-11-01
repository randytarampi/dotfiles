import importlib.util
import json
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parents[2]
SPEC = importlib.util.spec_from_file_location(
    "configure_pi_vibeguard", ROOT / "scripts/configure-pi.py"
)
configure_pi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure_pi)


def test_settings_packages_include_vibeguard_once():
    source = (ROOT / "scripts/configure-pi.py").read_text(encoding="utf-8")
    assert source.count('"npm:@xzzpig/pi-vibeguard"') == 1


def test_private_atomic_config_copy_and_backup(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_text('{"enabled":true}\n', encoding="utf-8")
    target = tmp_path / "agent/vibeguard.config.json"
    target.parent.mkdir()
    target.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(configure_pi, "VIBEGUARD_CONFIG", source)

    configure_pi._copy_vibeguard_config(target, no_backup=False)

    assert target.read_bytes() == source.read_bytes()
    assert target.stat().st_mode & 0o777 == 0o600
    assert Path(f"{target}.bak").read_text(encoding="utf-8") == "old\n"


def test_dry_run_logs_copy_intent_without_writing(tmp_path, monkeypatch, caplog):
    target = tmp_path / "agent/vibeguard.config.json"
    monkeypatch.setattr(configure_pi, "VIBEGUARD_CONFIG", tmp_path / "source.json")

    configure_pi._copy_vibeguard_config(target, dry_run=True)

    assert not target.exists()
    assert "Would copy" in caplog.text


def test_global_and_project_lookup_targets(tmp_path):
    agent_dir = tmp_path / "home/.pi/agent"
    assert configure_pi._vibeguard_config_target("global", agent_dir) == (
        agent_dir / "vibeguard.config.json"
    )
    assert configure_pi._vibeguard_config_target("project", agent_dir, tmp_path) == (
        tmp_path / ".pi/vibeguard.config.json"
    )


def test_project_root_config_wins_and_is_preserved(tmp_path, monkeypatch, caplog):
    root_config = tmp_path / "vibeguard.config.json"
    root_config.write_text("user config\n", encoding="utf-8")
    target = configure_pi._vibeguard_config_target(
        "project", tmp_path / ".pi/agent", tmp_path
    )
    assert target is None
    assert "lookup position 2" in caplog.text
    assert root_config.read_text(encoding="utf-8") == "user config\n"


def test_environment_override_skips_managed_write(tmp_path, monkeypatch, caplog):
    override = str(tmp_path / "custom.json")
    monkeypatch.setenv("PI_VIBEGUARD_CONFIG", override)
    assert configure_pi._vibeguard_config_target("global", tmp_path / "agent") is None
    assert configure_pi._vibeguard_config_target("project", tmp_path / "agent") is None
    assert override in caplog.text
    assert "lookup position 1" in caplog.text


def test_copy_is_idempotent_with_private_mode_and_backup(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_text('{"enabled":true}\n', encoding="utf-8")
    target = tmp_path / ".pi/vibeguard.config.json"
    monkeypatch.setattr(configure_pi, "VIBEGUARD_CONFIG", source)
    configure_pi._copy_vibeguard_config(target)
    first = target.read_bytes()
    configure_pi._copy_vibeguard_config(target)
    assert target.read_bytes() == first == source.read_bytes()
    assert target.stat().st_mode & 0o777 == 0o600
    assert Path(f"{target}.bak").read_bytes() == first


def test_committed_config_has_no_keywords_and_is_enabled():
    config = json.loads((ROOT / "configs/pi/vibeguard.config.json").read_text())
    assert config["enabled"] is True
    assert config["patterns"].get("keywords", []) == []
    assert len(config["patterns"]["regex"]) == 15
    assert config["patterns"]["builtin"] == ["email", "ipv4", "ipv6", "uuid"]
