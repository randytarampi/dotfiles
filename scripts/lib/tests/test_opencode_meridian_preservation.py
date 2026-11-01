"""Meridian registration persistence across generator writes.

`meridian setup --v2` owns its OpenCode registration outside the plugin
manifest; configure-opencode.py's wholesale plugins rebuild must preserve
the on-disk entry (string path or {package} object) instead of erasing it,
while never synthesizing an entry the owner removed or disabled.
"""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "scripts/configure-opencode.py"
SPEC = importlib.util.spec_from_file_location(
    "configure_opencode_preserve", SCRIPT_PATH
)
assert SPEC is not None and SPEC.loader is not None
configure_opencode = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = configure_opencode
SPEC.loader.exec_module(configure_opencode)

MERIDIAN_PATH_STRING = "/n/store/@rynfar/meridian/plugin/meridian.ts"


def test_preserves_string_meridian_entry(tmp_path):
    entries = ["/elsewhere/stuff", MERIDIAN_PATH_STRING]
    (tmp_path / "opencode.json").write_text(json.dumps({"plugins": entries}))
    assert configure_opencode.meridian_preserved_entries(str(tmp_path)) == [
        MERIDIAN_PATH_STRING
    ]


def test_preserves_package_object_meridian_entry(tmp_path):
    entry = {"package": MERIDIAN_PATH_STRING, "options": {"x": 1}}
    (tmp_path / "opencode.json").write_text(json.dumps({"plugins": [entry]}))
    assert configure_opencode.meridian_preserved_entries(str(tmp_path)) == [entry]


def test_absent_meridian_is_never_synthesized(tmp_path):
    (tmp_path / "opencode.json").write_text(json.dumps({"plugins": ["unrelated"]}))
    assert configure_opencode.meridian_preserved_entries(str(tmp_path)) == []


def test_disable_directive_is_never_synthesized(tmp_path):
    directive = "-@rynfar/meridian"
    (tmp_path / "opencode.json").write_text(json.dumps({"plugins": [directive]}))
    assert configure_opencode.meridian_preserved_entries(str(tmp_path)) == [
        "-@rynfar/meridian"
    ]


def test_unreadable_config_is_fail_open(tmp_path):
    (tmp_path / "opencode.json").write_text("{ not json")
    assert configure_opencode.meridian_preserved_entries(str(tmp_path)) == []
    missing = tmp_path / "sub"
    missing.mkdir()
    assert configure_opencode.meridian_preserved_entries(str(missing)) == []


def test_malformed_plugins_value_is_fail_open(tmp_path):
    (tmp_path / "opencode.json").write_text(json.dumps({"plugins": "not-a-list"}))
    assert configure_opencode.meridian_preserved_entries(str(tmp_path)) == []
