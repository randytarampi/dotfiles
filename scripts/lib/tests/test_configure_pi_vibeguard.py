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


def test_committed_config_has_no_keywords_and_is_enabled():
    config = json.loads((ROOT / "configs/pi/vibeguard.config.json").read_text())
    assert config["enabled"] is True
    assert config["patterns"].get("keywords", []) == []
    assert len(config["patterns"]["regex"]) == 15
    assert config["patterns"]["builtin"] == ["email", "ipv4", "ipv6", "uuid"]
