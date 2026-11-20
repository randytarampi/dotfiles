"""Basic Memory privacy and owner-state regression fixtures.

These are local, network-free contract tests, NOT proof of actual upstream
Umami/Logfire/OTel egress or of live semantic search in a user session.
"""

import importlib.util
import json
from pathlib import Path
import stat

import pytest

from basic_memory_ownership import ensure_basic_memory_ownership

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "configs/mcp/basic-memory.json"
RENDER_TEMPLATE = ROOT / "configs/mcp/templates/basic-memory.json"


def load_actual_writer():
    script = ROOT / "scripts/configure-mcp-tool.py"
    spec = importlib.util.spec_from_file_location("mcp_writer_under_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PRIVACY = {
    "BASIC_MEMORY_NO_PROMOS": "1",
    "BASIC_MEMORY_LOGFIRE_ENABLED": "false",
    "BASIC_MEMORY_LOGFIRE_SEND_TO_LOGFIRE": "false",
    "OTEL_SDK_DISABLED": "true",
    "BASIC_MEMORY_FORCE_LOCAL": "true",
    "BASIC_MEMORY_AUTO_UPDATE": "false",
}


def test_templates_pin_private_stdio_runtime():
    canonical = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    rendered = json.loads(RENDER_TEMPLATE.read_text(encoding="utf-8"))
    assert canonical == rendered
    assert canonical["type"] == "command"
    assert canonical["command"] == "uvx"
    assert canonical["args"] == [
        "--prerelease=allow",
        "--from",
        "basic-memory==1.0.0",
        "basic-memory",
        "mcp",
    ]
    for key, expected in PRIVACY.items():
        assert canonical["env"][key] == expected
    assert canonical["env"]["BASIC_MEMORY_RERANKER_PROVIDER"] == "fastembed"
    assert canonical["env"]["BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER"] == "fastembed"


@pytest.mark.parametrize(
    "modification",
    [
        {"env": {"BASIC_MEMORY_NO_PROMOS": "0"}},
        {"env": {"OTEL_SDK_DISABLED": "false"}},
        {"command": "network-memory"},
        {"cwd": "/external/private-vault"},
        {"disabled_tools": ["search"]},
        {"timeout": 999},
    ],
)
def test_private_memory_collision_rejected_without_filesystem_mutation(
    tmp_path, modification
):
    """Conflicting user config must refuse before any owner data is touched."""
    managed = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    expected = {
        "command": managed["command"],
        "args": managed["args"],
        "env": managed["env"],
    }
    custom = {**expected, **modification}
    # Test a private home containing all three user-owned data locations.
    files = [
        tmp_path / "basic-memory" / "notes.md",
        tmp_path / ".basic-memory" / "index.db",
        tmp_path / ".opencode-mem" / "data" / "legacy.txt",
    ]
    for file in files:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"private-user-data")
    before = [(file.read_bytes(), file.stat().st_mtime_ns) for file in files]
    existing = {"mcpServers": {"basic-memory": custom, "external": {"command": "keep"}}}
    generated = {"mcpServers": {"basic-memory": expected}}
    # Exercise the real MCP writer, not just its pure ownership validator.
    config_path = tmp_path / "client-mcps.json"
    config_path.write_text(json.dumps(existing), encoding="utf-8")
    config_path.chmod(0o600)
    config_before = (
        config_path.read_bytes(),
        config_path.stat().st_mtime_ns,
        stat.S_IMODE(config_path.stat().st_mode),
    )
    with pytest.raises(ValueError, match="refusing to overwrite"):
        load_actual_writer().merge_configs_to_file(
            "json-mcpServers-merge", config_path, json.dumps(generated)
        )
    assert json.loads(config_path.read_text())["mcpServers"]["external"] == {
        "command": "keep"
    }
    assert (
        config_path.read_bytes(),
        config_path.stat().st_mtime_ns,
        stat.S_IMODE(config_path.stat().st_mode),
    ) == config_before
    assert [(file.read_bytes(), file.stat().st_mtime_ns) for file in files] == before


def test_identical_memory_registration_is_idempotent():
    canonical = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    record = {
        "command": canonical["command"],
        "args": canonical["args"],
        "env": canonical["env"],
    }
    previous = {"mcpServers": {"basic-memory": record}}
    generated = {"mcpServers": {"basic-memory": dict(record)}}
    assert ensure_basic_memory_ownership(previous, generated, "mcpServers") is None
    assert ensure_basic_memory_ownership(previous, generated, "mcpServers") is None


def test_wildcard_tool_filter_is_equivalent_to_no_filter():
    canonical = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    record = {
        "command": canonical["command"],
        "args": canonical["args"],
        "env": canonical["env"],
    }
    previous = {"mcpServers": {"basic-memory": {**record, "tools": ["*"]}}}
    generated = {"mcpServers": {"basic-memory": dict(record)}}
    # Copilot explicitly defaults its tool list to all tools.
    assert (
        ensure_basic_memory_ownership(
            previous, generated, "mcpServers", copilot_wildcard_tools=True
        )
        is None
    )
    # Never silently extend the equivalence to other client schemas.
    with pytest.raises(ValueError, match="refusing to overwrite"):
        ensure_basic_memory_ownership(previous, generated, "mcpServers")


def test_restrictive_tool_filter_still_rejects_divergence():
    canonical = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    record = {
        "command": canonical["command"],
        "args": canonical["args"],
        "env": canonical["env"],
    }
    previous = {"mcpServers": {"basic-memory": {**record, "tools": ["read"]}}}
    generated = {"mcpServers": {"basic-memory": dict(record)}}
    with pytest.raises(ValueError, match="refusing to overwrite"):
        ensure_basic_memory_ownership(previous, generated, "mcpServers")


def test_absent_memory_entry_does_not_trigger_collision_validator():
    """A pure guard may inspect an absent name; no gate-off behaviour is inferred."""
    existing = {"mcpServers": {"private-memory": {"command": "keep"}}}
    generated = {"mcpServers": {"codegraph": {"command": "codegraph"}}}
    assert ensure_basic_memory_ownership(existing, generated, "mcpServers") is None
    assert existing == {"mcpServers": {"private-memory": {"command": "keep"}}}


@pytest.mark.parametrize(
    ("format_type", "is_copilot"),
    [
        ("json-mcpServers-http", True),
        ("json-mcpServers-merge", False),
        ("json-mcpServers", False),
        ("json-mcpServers-antigravity", False),
    ],
)
def test_real_writer_normalizes_wildcard_only_for_copilot(
    tmp_path, format_type, is_copilot
):
    """The actual format dispatcher must enforce the consumer boundary."""
    canonical = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    managed = {
        "command": canonical["command"],
        "args": canonical["args"],
        "env": canonical["env"],
    }
    previous = {"mcpServers": {"basic-memory": {**managed, "tools": ["*"]}}}
    generated = {"mcpServers": {"basic-memory": managed}}
    path = tmp_path / "mcp-config.json"
    path.write_text(json.dumps(previous), encoding="utf-8")
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    writer = load_actual_writer()
    if is_copilot:
        writer.merge_configs_to_file(format_type, path, json.dumps(generated))
        assert json.loads(path.read_text(encoding="utf-8")) == generated
        # A second run has no ownership mismatch after the client rewrite.
        path.write_text(json.dumps(previous), encoding="utf-8")
        writer.merge_configs_to_file(format_type, path, json.dumps(generated))
        assert json.loads(path.read_text(encoding="utf-8")) == generated
    else:
        with pytest.raises(ValueError, match="refusing to overwrite"):
            writer.merge_configs_to_file(format_type, path, json.dumps(generated))
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize(
    "filter_value",
    [[], ["read"], ["*", "read"], "*", None],
)
def test_copilot_writer_rejects_other_tool_filters(tmp_path, filter_value):
    canonical = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    managed = {
        "command": canonical["command"],
        "args": canonical["args"],
        "env": canonical["env"],
    }
    previous = {"mcpServers": {"basic-memory": {**managed, "tools": filter_value}}}
    generated = {"mcpServers": {"basic-memory": managed}}
    path = tmp_path / "copilot.json"
    path.write_text(json.dumps(previous), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="refusing to overwrite"):
        load_actual_writer().merge_configs_to_file(
            "json-mcpServers-http", path, json.dumps(generated)
        )
    assert path.read_bytes() == before


def test_copilot_wildcard_does_not_mask_changed_private_environment(tmp_path):
    canonical = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    managed = {
        "command": canonical["command"],
        "args": canonical["args"],
        "env": canonical["env"],
    }
    changed = {
        **managed,
        "env": {**managed["env"], "BASIC_MEMORY_FORCE_LOCAL": "false"},
        "tools": ["*"],
    }
    previous = {"mcpServers": {"basic-memory": changed}}
    generated = {"mcpServers": {"basic-memory": managed}}
    path = tmp_path / "copilot.json"
    path.write_text(json.dumps(previous), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="refusing to overwrite"):
        load_actual_writer().merge_configs_to_file(
            "json-mcpServers-http", path, json.dumps(generated)
        )
    assert path.read_bytes() == before
