"""Unit checks for the Docker network-isolation negative control."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CHECKER = ROOT / "tests/bootstrap/assert_no_egress.py"


def _checker():
    spec = importlib.util.spec_from_file_location("bootstrap_network_check", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_ipv4_route_is_rejected():
    route = (
        "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
        "eth0 00000000 01010101 0003 0 0 0 00000000 0 0 0\n"
    )
    assert _checker()._has_ipv4_default_route(route)


def test_loopback_route_is_not_a_default_route():
    route = (
        "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
        "lo 0000007F 00000000 0001 0 0 0 00FFFFFF 0 0 0\n"
    )
    assert not _checker()._has_ipv4_default_route(route)


def test_bad_route_flags_fail_closed():
    route = (
        "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
        "eth0 00000000 01010101 invalid 0 0 0 00000000 0 0 0\n"
    )
    with pytest.raises(RuntimeError, match="Malformed"):
        _checker()._has_ipv4_default_route(route)


def test_socket_probe_requires_isolated_container():
    source = CHECKER.read_text(encoding="utf-8")
    assert 'socket.create_connection(("1.1.1.1", 443), timeout=2)' in source
    assert 'Path("/proc/net/route")' in source
    assert "errno.ENETUNREACH" in source


def test_required_ci_runs_a_nonroot_networkless_container():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    job = workflow.split("  bootstrap-networkless:", 1)[1].split("  finish:", 1)[0]
    assert "runs-on: ubuntu-24.04" in job
    assert "uses: actions/checkout@v7" in job
    assert "persist-credentials: false" in job
    assert "docker build --platform linux/amd64" in job
    assert "docker run --rm --network none --platform linux/amd64" in job
    assert "python3 tests/bootstrap/assert_no_egress.py" in job
    assert "bash tests/bootstrap/smoke.sh" in job
    assert "continue-on-error" not in job
