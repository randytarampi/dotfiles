import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "configure_opencode_dcp", ROOT / "scripts/configure-opencode-dcp.py"
)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

# Vendored relevant v2.0.15 cli.json schema subset (packages/cli/src/config/schema.ts
# plus the TUI Config.Info theme/keybind/plugin fields).  The published schema is
# intentionally broad; these are the fields this writer migrates.
V2_CLI_SCHEMA_SUBSET = {
    "theme": {"type": "object", "required": {"name", "mode"}},
    "keybinds": {"type": "object", "key_type": "string"},
    "plugins": {"type": "array", "item_types": {"string", "object"}},
}


def packages(config):
    return {
        entry["package"]: entry
        for entry in config["plugins"]
        if isinstance(entry, dict) and "package" in entry
    }


def test_build_config_is_schema_valid_and_idempotent():
    existing = {
        "theme": "dark",
        "keybinds": {
            "session_new": "n",
            "session_rename": "r",
            "session_quick_switch_1": "1",
        },
        "plugins": [
            "@scope/unversioned",
            "-opencode.notifications",
            ["example/tuple@1.0.0", {"enabled": True}],
            {"package": "example/object@2.0.0", "options": {"x": 1}},
            {"package": "@renjfk/opencode-voice@0.6.0"},
        ],
    }
    result = MODULE.build_config(existing)
    assert result["$schema"] == MODULE.SCHEMA
    assert result["theme"] == {"name": "dark"}
    assert result["keybinds"] == {
        "session.new": "n",
        "session.rename": "r",
        "session.quick_switch.1": "1",
    }
    assert "plugin" not in result
    assert "-opencode.notifications" in result["plugins"]
    assert packages(result)["example/tuple@1.0.0"]["options"] == {"enabled": True}
    assert "@scope/unversioned" in packages(result)
    assert MODULE.build_config(result) == result


def test_voice_detection_is_precise():
    assert MODULE.is_voice_entry("@renjfk/opencode-voice@0.6.0")
    assert MODULE.is_voice_entry({"package": "@renjfk/opencode-voice"})
    assert not MODULE.is_voice_entry(
        {"package": "example/plugin", "options": {"session_rename": "r"}}
    )


def test_cli_json_takes_precedence_over_legacy_tui(tmp_path):
    (tmp_path / "tui.json").write_text('{"theme":"light"}')
    (tmp_path / "cli.json").write_text('{"theme":"dark"}')
    _, source = MODULE.load_source(str(tmp_path))
    assert source == str(tmp_path / "cli.json")


@pytest.mark.parametrize("content", ["not json", "[]"])
def test_malformed_source_fails_closed(tmp_path, monkeypatch, content):
    (tmp_path / "cli.json").write_text(content)
    monkeypatch.setenv("OPENCODE_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["configure-opencode-dcp.py"])
    with pytest.raises(SystemExit) as error:
        MODULE.main()
    assert error.value.code == 1
    assert not (tmp_path / "opencode-quota" / "quota-toast.jsonc").exists()


def test_main_generates_quota_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["configure-opencode-dcp.py", "--no-backup"])
    MODULE.main()
    sidecar = tmp_path / "opencode-quota" / "quota-toast.jsonc"
    assert json.loads(sidecar.read_text()) == {"enabledProviders": "auto"}


def test_quota_sidecar_preserves_custom_settings(tmp_path):
    path = tmp_path / "quota-toast.jsonc"
    path.write_text(json.dumps({"position": "bottom", "enabledProviders": ["old"]}))
    result = MODULE.build_quota_config(str(path))
    assert result == {"position": "bottom", "enabledProviders": "auto"}


def test_missing_quota_sidecar_defaults_to_auto(tmp_path):
    result = MODULE.build_quota_config(str(tmp_path / "missing.jsonc"))
    assert result == {"enabledProviders": "auto"}


def test_quota_sidecar_accepts_jsonc_and_merges(tmp_path):
    path = tmp_path / "quota-toast.jsonc"
    path.write_text(
        '{\n  // keep the user\'s placement\n  "position": "bottom",\n  "custom": true,\n}\n'
    )
    assert MODULE.build_quota_config(str(path)) == {
        "position": "bottom",
        "custom": True,
        "enabledProviders": "auto",
    }


def test_malformed_jsonc_fails_closed(tmp_path):
    path = tmp_path / "quota-toast.jsonc"
    path.write_text('{"position": [}')
    with pytest.raises(MODULE.ConfigError):
        MODULE.build_quota_config(str(path))


def test_keybinds_fail_closed_for_unmapped_legacy_key():
    with pytest.raises(MODULE.ConfigError, match="unmapped_key"):
        MODULE._dotted_keybinds({"unmapped_key": "x"})


def test_cli_v2_subset_shape():
    """Validate the migrated fields against the v2.0.15 schema subset."""
    result = MODULE.build_config(
        {"theme": "dark", "keybinds": {"app_toggle_file_context": "x"}}
    )
    theme_schema = V2_CLI_SCHEMA_SUBSET["theme"]
    assert theme_schema["type"] == "object"
    assert set(result["theme"]) <= theme_schema["required"]
    assert result["theme"]["name"] == "dark"
    assert V2_CLI_SCHEMA_SUBSET["keybinds"]["key_type"] == "string"
    assert set(result["keybinds"]) == {"app.toggle.file_context"}
