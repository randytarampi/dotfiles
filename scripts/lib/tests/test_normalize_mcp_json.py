"""Regression checks for safe MCP configuration normalization."""

import json
from pathlib import Path

import pytest

import normalize_mcp_json
import opencode_host
from opencode_host import resolve_opencode_host


@pytest.fixture(autouse=True)
def legacy_v1_host(monkeypatch):
    """Existing flattening regression tests pin a v1 host; native-direction
    tests override to v2 in their body (later monkeypatch wins)."""
    monkeypatch.setattr(
        opencode_host, "resolve_opencode_host", lambda binary="opencode": ("known", 1)
    )


@pytest.mark.parametrize(
    "config", [None, [], "text", 1, True, {}, {"mcp": []}, {"mcp": {"servers": []}}]
)
def test_invalid_shapes_are_unchanged(tmp_path, config):
    path = tmp_path / "opencode.json"
    original = json.dumps(config)
    path.write_text(original)
    assert normalize_mcp_json.normalize(str(path)) is False
    assert path.read_text() == original


def test_normalization_is_atomic_and_idempotent(tmp_path, monkeypatch):
    path = tmp_path / "opencode.json"
    config = {
        "mcp": {
            "existing": {"type": "remote", "url": "https://example.com"},
            "servers": {
                "existing": {"type": "local", "command": ["ignored"]},
                "codegraph": {
                    "type": "local",
                    "command": ["codegraph"],
                    "disabled": True,
                    "codemode": True,
                },
            },
        }
    }
    original = json.dumps(config)
    path.write_text(original)
    replace = normalize_mcp_json.os.replace
    replacements = []

    def checked_replace(source, destination):
        assert Path(source).parent == path.parent
        assert path.read_text() == original
        assert "servers" not in json.loads(Path(source).read_text())["mcp"]
        replacements.append(destination)
        replace(source, destination)

    monkeypatch.setattr(normalize_mcp_json.os, "replace", checked_replace)
    assert normalize_mcp_json.normalize(str(path)) is True
    assert replacements == [str(path)]
    assert json.loads(path.read_text()) == {
        "mcp": {
            "existing": config["mcp"]["existing"],
            "codegraph": {"type": "local", "command": ["codegraph"], "enabled": False},
        }
    }
    assert normalize_mcp_json.normalize(str(path)) is False
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("entry", ["unexpected", {"command": ["unknown"]}])
def test_unsupported_non_colliding_server_preserves_wrapper(tmp_path, entry):
    path = tmp_path / "opencode.json"
    original = json.dumps({"mcp": {"servers": {"future-server": entry}}})
    path.write_text(original)

    assert normalize_mcp_json.normalize(str(path)) is False
    assert path.read_text() == original


def test_malformed_colliding_server_does_not_override_flat_entry(tmp_path):
    path = tmp_path / "opencode.json"
    flat_entry = {"type": "remote", "url": "https://example.com"}
    config = {
        "mcp": {
            "existing": flat_entry,
            "servers": {"existing": "unsupported nested entry"},
        }
    }
    path.write_text(json.dumps(config))

    assert normalize_mcp_json.normalize(str(path)) is True
    assert json.loads(path.read_text()) == {"mcp": {"existing": flat_entry}}


@pytest.mark.parametrize("relative_target", [False, True])
def test_normalization_preserves_symlink_and_updates_target(
    tmp_path, monkeypatch, relative_target
):
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = target_dir / "opencode.json"
    original = '{"mcp": {"servers": {"codegraph": {"type": "local"}}}}'
    target.write_text(original)
    link_dir = tmp_path / "config"
    link_dir.mkdir()
    link = link_dir / "opencode.json"
    link_target = Path("../target/opencode.json") if relative_target else target
    link.symlink_to(link_target)
    replace = normalize_mcp_json.os.replace
    replacements = []

    def checked_replace(source, destination):
        assert Path(source).parent == target_dir
        assert destination == str(target)
        assert target.read_text() == original
        replacements.append(destination)
        replace(source, destination)

    monkeypatch.setattr(normalize_mcp_json.os, "replace", checked_replace)
    assert normalize_mcp_json.normalize(str(link)) is True
    assert replacements == [str(target)]
    assert link.is_symlink()
    assert link.readlink() == link_target
    assert json.loads(target.read_text()) == {
        "mcp": {"codegraph": {"type": "local", "enabled": True}}
    }
    assert link.read_text() == target.read_text()
    assert normalize_mcp_json.normalize(str(link)) is False
    assert list(target_dir.iterdir()) == [target]
    assert list(link_dir.iterdir()) == [link]


@pytest.mark.parametrize(
    "stage,error", [("dump", OSError), ("dump", TypeError), ("replace", OSError)]
)
def test_write_failures_preserve_original_and_remove_temporary_file(
    tmp_path, monkeypatch, stage, error
):
    path = tmp_path / "opencode.json"
    original = '{"mcp": {"servers": {"codegraph": {"type": "local"}}}}'
    path.write_text(original)

    def fail(*args, **kwargs):
        if stage == "dump":
            args[1].write('{"partial":')
        raise error("simulated write failure")

    target = normalize_mcp_json.json if stage == "dump" else normalize_mcp_json.os
    monkeypatch.setattr(target, stage, fail)
    assert normalize_mcp_json.normalize(str(path)) is False
    assert path.read_text() == original
    assert list(tmp_path.iterdir()) == [path]


def test_native_direction_promotes_flat_entries_into_wrapper(tmp_path, monkeypatch):
    """On a v2 host the nested mcp.servers wrapper is the managed shape: flat
    legacy entries are promoted with enabled inverted to disabled and all
    fields (including v2 timeout objects and snake_case oauth members)
    survive verbatim."""
    monkeypatch.setattr(
        opencode_host, "resolve_opencode_host", lambda binary="opencode": ("known", 2)
    )
    path = tmp_path / "opencode.json"
    path.write_text(
        json.dumps(
            {
                "model": "litellm/x",
                "mcp": {
                    "sentry": {
                        "type": "remote",
                        "url": "https://sentry",
                        "enabled": True,
                        "client_id": "abc",
                    }
                },
            }
        )
    )
    assert normalize_mcp_json.normalize(str(path)) is True
    assert json.loads(path.read_text()) == {
        "model": "litellm/x",
        "mcp": {
            "servers": {
                "sentry": {
                    "type": "remote",
                    "url": "https://sentry",
                    "disabled": False,
                    "client_id": "abc",
                }
            }
        },
    }
    assert normalize_mcp_json.normalize(str(path)) is False


def test_native_direction_leaves_wrapper_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(
        opencode_host, "resolve_opencode_host", lambda binary="opencode": ("known", 2)
    )
    path = tmp_path / "opencode.json"
    native = json.dumps(
        {
            "mcp": {
                "servers": {
                    "codegraph": {
                        "type": "local",
                        "command": ["codegraph"],
                        "disabled": False,
                        "timeout": {"catalog": 30, "execution": 90},
                    }
                }
            }
        }
    )
    path.write_text(native)
    assert normalize_mcp_json.normalize(str(path)) is False
    assert path.read_text() == native


def test_native_direction_is_idempotent_for_v2_host_with_flat_and_wrapper(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        opencode_host, "resolve_opencode_host", lambda binary="opencode": ("known", 2)
    )
    path = tmp_path / "opencode.json"
    path.write_text(
        json.dumps(
            {
                "mcp": {
                    "legacy": {"type": "local", "command": ["x"], "enabled": False},
                    "servers": {
                        "codegraph": {"type": "local", "command": ["codegraph"]}
                    },
                }
            }
        )
    )
    assert normalize_mcp_json.normalize(str(path)) is True
    assert json.loads(path.read_text()) == {
        "mcp": {
            "servers": {
                "codegraph": {"type": "local", "command": ["codegraph"]},
                "legacy": {"type": "local", "command": ["x"], "disabled": True},
            }
        }
    }
    assert normalize_mcp_json.normalize(str(path)) is False
