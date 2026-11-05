"""Xcode MCP is opt-in per client and only on qualified macOS hosts."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "scripts/lib/xcode_mcp.py"
SPEC = importlib.util.spec_from_file_location("xcode_mcp_tested", LIB)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_mirrored_template_and_explicit_gate_for_every_registered_client():
    mcp = ROOT / "configs/mcp"
    reg = json.loads((mcp / "global-mcps.json").read_text())
    template = json.loads((mcp / "xcode.json").read_text())
    assert template == json.loads((mcp / "templates/xcode.json").read_text())
    assert template["command"] == "xcrun"
    assert template["args"] == ["mcpbridge"]
    for name, client in reg["tools"].items():
        entries = client["mcp_servers"]
        assert sum(e.get("template") == "xcode" for e in entries) == 1, name
        assert "xcode" not in module.enabled_entries(entries, {})
        assert "xcode" in module.enabled_entries(entries, {module.GATE: "1"})


def test_unapproved_nonmac_and_missing_cli_refuse():
    with pytest.raises(ValueError, match="explicit"):
        module.require_available({}, "darwin", lambda _: "/bin/xcrun", Mock())
    with pytest.raises(ValueError, match="macOS"):
        module.require_available({module.GATE: "1"}, "linux", Mock(), Mock())
    with pytest.raises(ValueError, match="installed"):
        module.require_available({module.GATE: "1"}, "darwin", lambda _: None, Mock())


def test_read_only_xcrun_probe_uses_fixed_argv():
    commands = []

    def fake_run(argv, **_kwargs):
        commands.append(argv)
        return Mock(
            stdout="/bin/sh\n",
            returncode=0,
        )

    assert (
        module.require_available(
            {module.GATE: "1"},
            "darwin",
            lambda _: "/usr/bin/xcrun",
            fake_run,
        )
        == "/usr/bin/xcrun"
    )
    assert commands == [["/usr/bin/xcrun", "--find", "mcpbridge"]]


def test_broken_xcrun_output_is_not_qualified():
    with pytest.raises(ValueError, match="unavailable"):
        module.require_available(
            {module.GATE: "1"},
            "darwin",
            lambda _: "/usr/bin/xcrun",
            lambda *_args, **_kwargs: Mock(stdout="relative/path", returncode=0),
        )


def test_missing_absolute_bridge_path_is_rejected():
    with pytest.raises(ValueError, match="unavailable"):
        module.require_available(
            {module.GATE: "1"},
            "darwin",
            lambda _: "/usr/bin/xcrun",
            lambda *_args, **_kwargs: Mock(
                stdout="/missing/does-not-exist-mcpbridge",
                returncode=0,
            ),
        )
