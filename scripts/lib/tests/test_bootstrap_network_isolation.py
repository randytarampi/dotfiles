"""Unit checks for the Docker network-isolation negative control."""

import importlib.util
import errno
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


def test_truncated_ipv4_route_fails_closed():
    with pytest.raises(RuntimeError, match="Malformed IPv4 route record"):
        _checker()._has_ipv4_default_route(
            "Iface Destination Gateway Flags\neth0 00000000\n"
        )


def test_empty_ipv4_route_record_is_ignored():
    route = "Iface Destination Gateway Flags\n\nlo 0000007F 00000000 0001\n"
    assert not _checker()._has_ipv4_default_route(route)


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


@pytest.mark.parametrize(
    "error",
    (errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EADDRNOTAVAIL),
)
def test_ipv4_denial_accepts_only_qualified_network_errors(monkeypatch, error):
    checker = _checker()

    def deny(address, timeout):
        assert address == ("1.1.1.1", 443)
        assert timeout == 2
        raise OSError(error, "network unavailable")

    monkeypatch.setattr(checker.socket, "create_connection", deny)
    checker._probe_ipv4_egress()


def test_ipv4_permission_error_is_not_network_isolation(monkeypatch):
    checker = _checker()

    def deny(_address, timeout):
        assert timeout == 2
        raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(checker.socket, "create_connection", deny)
    with pytest.raises(RuntimeError, match="unqualified reason"):
        checker._probe_ipv4_egress()


def test_ipv4_reachable_socket_must_fail(monkeypatch):
    checker = _checker()

    class Connected:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        checker.socket, "create_connection", lambda *_args, **_kw: Connected()
    )
    with pytest.raises(RuntimeError, match="Unexpected outbound TCP"):
        checker._probe_ipv4_egress()


def test_default_ipv6_route_is_rejected():
    route = (
        f"{'0' * 32} 00 {'0' * 32} 00 "
        "00000000000000000000000000000000 00000000 00000000 "
        "00000000 00000001 eth0\n"
    )
    assert _checker()._has_ipv6_default_route(route)


def test_linux_kernel_ipv6_reject_default_does_not_imply_egress():
    # Linux exposes this reject-only ::/0 fallback on lo in a networkless
    # Docker namespace; the genuine AF_INET6 socket probe still runs.
    route = (
        f"{'0' * 32} 00 {'0' * 32} 00 "
        "00000000000000000000000000000000 ffffffff 00000001 "
        "00000000 00200200 lo\n"
    )
    assert not _checker()._has_ipv6_default_route(route)


def test_malformed_ipv6_default_flags_fail_closed():
    route = (
        f"{'0' * 32} 00 {'0' * 32} 00 "
        "00000000000000000000000000000000 00000000 00000000 "
        "00000000 broken eth0\n"
    )
    with pytest.raises(RuntimeError, match="Malformed IPv6 route flags"):
        _checker()._has_ipv6_default_route(route)


def test_ipv6_loopback_is_not_a_default_route():
    route = (
        f"{'0' * 31}1 80 {'0' * 32} 00 "
        "00000000000000000000000000000000 00000000 00000000 "
        "00000000 00000001 lo\n"
    )
    assert not _checker()._has_ipv6_default_route(route)


def test_bad_ipv6_route_record_fails_closed():
    with pytest.raises(RuntimeError, match="Malformed IPv6 route"):
        _checker()._has_ipv6_default_route("incomplete record")


def test_ipv6_probe_denies_valid_network_errors(monkeypatch):
    checker = _checker()

    class UnreachableSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def settimeout(self, seconds):
            assert seconds == 2

        def connect(self, address):
            assert address == ("2606:4700:4700::1111", 443)
            raise OSError(errno.ENETUNREACH, "network is unreachable")

    monkeypatch.setattr(checker.socket, "socket", lambda *_args: UnreachableSocket())
    checker._probe_ipv6_egress()


def test_ipv6_probe_fails_on_unexpected_connection(monkeypatch):
    checker = _checker()

    class ReachableSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def settimeout(self, seconds):
            assert seconds == 2

        def connect(self, address):
            assert address == ("2606:4700:4700::1111", 443)
            return None

    monkeypatch.setattr(checker.socket, "socket", lambda *_args: ReachableSocket())
    with pytest.raises(RuntimeError, match="Unexpected outbound IPv6"):
        checker._probe_ipv6_egress()


def test_socket_probe_requires_isolated_container():
    source = CHECKER.read_text(encoding="utf-8")
    assert 'socket.create_connection(("1.1.1.1", 443), timeout=2)' in source
    assert 'Path("/proc/net/route")' in source
    assert "errno.ENETUNREACH" in source


def test_required_ci_runs_four_real_native_networkless_ubuntu_containers():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    job = workflow.split("  bootstrap-networkless:", 1)[1].split("  finish:", 1)[0]
    for runner, version, cpu, platform in (
        ("ubuntu-24.04", "24.04", "x86_64", "linux/amd64"),
        ("ubuntu-24.04-arm", "24.04", "aarch64", "linux/arm64"),
        ("ubuntu-26.04", "26.04", "x86_64", "linux/amd64"),
        ("ubuntu-26.04-arm", "26.04", "aarch64", "linux/arm64"),
    ):
        assert f"          - os: {runner}\n" in job
        row = job.split(f"          - os: {runner}\n", 1)[1].split(
            "\n          - os:", 1
        )[0]
        assert f'release: "{version}"' in row
        assert f"architecture: {cpu}" in row
        assert f"platform: {platform}" in row
    assert "fail-fast: false" in job
    assert r"runs-on: ${{ matrix.os }}" in job
    assert "uses: actions/checkout@v7" in job
    assert "persist-credentials: false" in job
    assert 'test "$(uname -m)" = "$EXPECTED_ARCH"' in job
    assert 'test "$VERSION_ID" = "$EXPECTED_RELEASE"' in job
    assert 'docker build --platform "$TARGET_PLATFORM"' in job
    assert "docker run --rm --network none --cap-drop=ALL" in job
    assert '--platform "$TARGET_PLATFORM" --pull never' in job
    assert "--security-opt no-new-privileges" in job
    assert "timeout-minutes: 25" in job
    assert 'test "$(id -u)" -eq 10001' in job
    assert "python3 tests/bootstrap/assert_no_egress.py" in job
    assert "bash tests/bootstrap/smoke.sh" in job
    assert "continue-on-error" not in job


def test_networkless_matrix_is_part_of_the_fail_closed_required_status():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    required = workflow.split("  ci-required:", 1)[1]
    assert "name: ci/required" in required
    assert "needs: [verify, deploy, bootstrap-smoke, bootstrap-networkless]" in required
    assert "NETWORK_RESULT: ${{ needs.bootstrap-networkless.result }}" in required
    assert '"$NETWORK_RESULT"' in required
    assert '"$result" != "success"' in required
