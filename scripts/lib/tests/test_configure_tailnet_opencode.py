import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "configure-tailnet-opencode.py"
SPEC = importlib.util.spec_from_file_location("configure_tailnet_opencode", SCRIPT)
assert SPEC and SPEC.loader
access = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(access)


def config(proxy="http://127.0.0.1:4096"):
    return {
        "TCP": {"8443": {"HTTPS": True}},
        "Web": {"host.ts.net:8443": {"Handlers": {"/": {"Proxy": proxy}}}},
    }


@pytest.mark.parametrize(
    "state,expected",
    [
        ({}, "absent"),
        (config(), "ours"),
        (config("http://127.0.0.1:9999"), "conflict"),
        ({"TCP": {"8443": {"HTTP": True}}}, "conflict"),
        ({**config(), "AllowFunnel": {"8443": True}}, "conflict"),
        ({**config(), "AllowFunnel": {"host.ts.net:8443": True}}, "conflict"),
        ({**config(), "AllowFunnel": {"host.ts.net:8443": False}}, "ours"),
        ({**config(), "AllowFunnel": {"8443": False}}, "ours"),
        ({"AllowFunnel": {"8443": True}}, "conflict"),
        ({"TCP": []}, None),
        ({"Unknown": {}}, None),
        ({"Services": {"svc:other": {}}}, "conflict"),
        ({"Foreground": {"other-session": {}}}, "conflict"),
    ],
)
def test_route_classification(state, expected):
    if expected is None:
        with pytest.raises(ValueError):
            access.route_state(state, 8443, "http://127.0.0.1:4096")
    else:
        assert access.route_state(state, 8443, "http://127.0.0.1:4096") == expected


@pytest.mark.parametrize("value", ["8443; malicious", "0", "080", "65536", "-1", " "])
def test_bad_port(value):
    with pytest.raises(ValueError):
        access.port(value)


def test_read_only_default_never_calls_serve_apply(monkeypatch, capsys):
    monkeypatch.setattr(access.shutil, "which", lambda _: "/bin/tailscale")
    calls = []

    def fake_run(binary, *args):
        calls.append(args)
        return {"BackendState": "Running"} if args == ("status", "--json") else {}

    monkeypatch.setattr(access, "run_json", fake_run)
    monkeypatch.setattr(
        access.subprocess, "run", lambda *a, **k: pytest.fail("mutation must not run")
    )
    assert access.main([]) == 0
    assert len(calls) == 2
    assert "absent" in capsys.readouterr().out


def test_apply_requires_gate_password_and_real_health(monkeypatch):
    monkeypatch.setattr(access.shutil, "which", lambda _: "/bin/tailscale")
    monkeypatch.setattr(
        access,
        "run_json",
        lambda *args: {"BackendState": "Running"} if args[1] == "status" else {},
    )
    mutation = Mock(side_effect=AssertionError("must not publish"))
    monkeypatch.setattr(access.subprocess, "run", mutation)
    monkeypatch.delenv(access.GATE, raising=False)
    assert access.main(["--apply"]) == 1
    monkeypatch.setenv(access.GATE, "1")
    monkeypatch.delenv("OPENCODE_PASSWORD", raising=False)
    monkeypatch.delenv("OPENCODE_SERVER_PASSWORD", raising=False)
    assert access.main(["--apply"]) == 1
    monkeypatch.setenv("OPENCODE_PASSWORD", "secret")
    monkeypatch.setattr(
        access, "verify_opencode", Mock(side_effect=ValueError("unhealthy"))
    )
    assert access.main(["--apply"]) == 1
    assert not mutation.called


def test_apply_checks_health_and_readback(monkeypatch):
    monkeypatch.setattr(access.shutil, "which", lambda _: "/bin/tailscale")
    states = iter([{}, {}, config()])
    monkeypatch.setattr(
        access,
        "run_json",
        lambda *args: (
            {"BackendState": "Running"} if args[1] == "status" else next(states)
        ),
    )
    probe = Mock()
    mutation = Mock()
    monkeypatch.setattr(access, "verify_opencode", probe)
    monkeypatch.setattr(access.subprocess, "run", mutation)
    monkeypatch.setenv(access.GATE, "1")
    monkeypatch.setenv("OPENCODE_PASSWORD", "redacted")
    assert access.main(["--apply"]) == 0
    probe.assert_called_once_with(4096, "redacted")
    assert mutation.call_args.args[0] == [
        "/bin/tailscale",
        "serve",
        "--bg",
        "--https=8443",
        "http://127.0.0.1:4096",
    ]


def test_verify_open_code_rejects_public_health(monkeypatch):
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    fake = Mock()
    fake.open.return_value = FakeResponse()
    monkeypatch.setattr(access.urllib.request, "build_opener", lambda *args: fake)
    with pytest.raises(ValueError, match="without authentication"):
        access.verify_opencode(4096, "redacted")
    assert fake.open.call_count == 1


def test_apply_refuses_serve_change_during_health_probe(monkeypatch):
    monkeypatch.setattr(access.shutil, "which", lambda _: "/bin/tailscale")
    statuses = iter([{}, {"Services": {"svc:foreign": {}}}])
    monkeypatch.setattr(
        access,
        "run_json",
        lambda *a: {"BackendState": "Running"} if a[1] == "status" else next(statuses),
    )
    monkeypatch.setattr(access, "verify_opencode", Mock())
    mutation = Mock()
    monkeypatch.setattr(access.subprocess, "run", mutation)
    monkeypatch.setenv(access.GATE, "1")
    monkeypatch.setenv("OPENCODE_PASSWORD", "redacted")
    assert access.main(["--apply"]) == 1
    mutation.assert_not_called()


def test_dry_run_never_mutates_when_apply_flag_present(monkeypatch):
    monkeypatch.setattr(access.shutil, "which", lambda _: "/bin/tailscale")
    monkeypatch.setattr(
        access,
        "run_json",
        lambda *a: {"BackendState": "Running"} if a[1] == "status" else {},
    )
    monkeypatch.setattr(
        access.subprocess,
        "run",
        lambda *a, **k: pytest.fail("dry-run must not mutate"),
    )
    assert access.main(["--apply", "--dry-run"]) == 0
