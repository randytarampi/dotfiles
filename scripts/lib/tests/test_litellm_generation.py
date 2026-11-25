"""Launch-generation regression tests: no live gateway or inference."""

import json
import os
import sys
from types import SimpleNamespace

import pytest

import litellm_generation as generation

pytestmark = pytest.mark.skipif(os.name != "posix", reason="managed Unix gateway")


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path.resolve()
    config = path / "config.yaml"
    config.write_text(
        'model_list:\n  - model_name: "openai/gpt-A"\n'
        '    litellm_params:\n      model: "chatgpt/gpt-A"\n',
        encoding="utf-8",
    )
    projection = path / "free-route-config.json"
    projection.write_text(
        json.dumps(
            {
                "routes": [
                    {
                        "model_name": "openai/gpt-A",
                        "litellm_params": {"model": "chatgpt/gpt-A"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(Process=lambda: SimpleNamespace(create_time=lambda: 123.0)),
    )
    argv = []
    monkeypatch.setattr(generation.os, "execv", lambda _path, args: argv.extend(args))
    generation.launch(path, "4000")
    monkeypatch.setattr(generation, "_process_matches", lambda *_: True)
    return path, config, projection, argv


def _check(home):
    path, _, projection, _ = home
    return generation.check(path, "4000", generation._digest(projection.read_bytes()))


def test_launch_publishes_private_snapshot_and_process_receipt(home):
    path, config, _, argv = home
    receipt = path / ".loaded-generation"
    data = json.loads(receipt.read_bytes())
    assert data["pid"] == os.getpid()
    assert data["created"] == 123.0
    assert data["port"] == 4000
    snapshot = generation._snapshot(path, data["sha256"])
    assert snapshot.read_bytes() == config.read_bytes()
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert snapshot.stat().st_mode & 0o777 == 0o400
    assert argv == generation._command(path, snapshot, 4000)
    assert _check(home) is None


def test_stale_current_config_refuses(home):
    _, config, _, _ = home
    config.write_bytes(config.read_bytes() + b"\n")
    assert _check(home) == generation.STALE


def test_projection_cannot_launder_paid_yaml(home, monkeypatch):
    path, config, _, _ = home
    config.write_bytes(config.read_bytes().replace(b"chatgpt/", b"openai/"))
    generation.launch(path, "4000")
    monkeypatch.setattr(
        generation,
        "_process_matches",
        lambda *_: pytest.fail("unexpected process check"),
    )
    assert _check(home) == generation.MISMATCH


def test_changed_projection_after_authorization_refuses(home):
    path, _, projection, _ = home
    old_digest = generation._digest(projection.read_bytes())
    projection.write_bytes(projection.read_bytes() + b"\n")
    assert generation.check(path, "4000", old_digest) == generation.MISMATCH


def test_missing_config_fails_closed(home):
    path, config, projection, _ = home
    config.unlink()
    assert (
        generation.main(
            ["check", str(path), "4000", generation._digest(projection.read_bytes())]
        )
        == 1
    )


def test_foreign_port_refuses(home):
    path, _, projection, _ = home
    assert (
        generation.check(path, "5000", generation._digest(projection.read_bytes()))
        == generation.FOREIGN
    )


def test_missing_listener_refuses(home, monkeypatch):
    monkeypatch.setattr(generation, "_process_matches", lambda *_: False)
    assert _check(home) == generation.PROCESS


def test_unsafe_receipt_is_not_followed(home):
    path, _, projection, _ = home
    receipt = path / ".loaded-generation"
    foreign = path / "foreign"
    receipt.rename(foreign)
    receipt.symlink_to(foreign)
    before = foreign.read_bytes()
    assert (
        generation.main(
            ["check", str(path), "4000", generation._digest(projection.read_bytes())]
        )
        == 1
    )
    assert foreign.read_bytes() == before


def test_duplicate_yaml_keys_refused(home):
    path, config, projection, _ = home
    config.write_bytes(config.read_bytes() + b"model_list: []\n")
    generation.launch(path, "4000")
    assert (
        generation.main(
            ["check", str(path), "4000", generation._digest(projection.read_bytes())]
        )
        == 1
    )


def test_process_requires_exact_identity_command_and_listener(tmp_path, monkeypatch):
    home = tmp_path
    snapshot = home / "snapshot.yaml"
    command = generation._command(home, snapshot, 4000)
    connection = SimpleNamespace(
        status="LISTEN", laddr=SimpleNamespace(ip="127.0.0.1", port=4000)
    )
    process = SimpleNamespace(
        create_time=lambda: 123.0,
        cmdline=lambda: ["/usr/bin/python3", *command],
        net_connections=lambda **_: [connection],
        is_running=lambda: True,
    )
    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(Process=lambda _pid: process, CONN_LISTEN="LISTEN"),
    )
    receipt = {"pid": 123, "created": 123.0}
    assert generation._process_matches(home, snapshot, 4000, receipt)
    receipt["created"] = 122.0
    assert not generation._process_matches(home, snapshot, 4000, receipt)
    receipt["created"] = 123.0
    process.cmdline = lambda: ["unrelated"]
    assert not generation._process_matches(home, snapshot, 4000, receipt)
    process.cmdline = lambda: command
    connection.laddr.port = 5000
    assert not generation._process_matches(home, snapshot, 4000, receipt)


def test_managed_templates_use_launcher_for_supervisor_restarts():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    template = (root / ".chezmoiscripts/run_onchange_15-litellm.sh.tmpl").read_text()
    assert template.count('launch "$LITELLM_HOME" $PORT') == 2
    assert template.count(' -I "$LITELLM_HOME/launch-generation.py"') == 2
    assert "systemctl --user restart litellm.service" in template
