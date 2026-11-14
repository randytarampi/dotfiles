#!/usr/bin/env python3
"""Fail closed unless the container has no usable default route or egress.

Executed *inside* a runtime created with Docker --network none. This probes
ordinary sockets, not a stubbed curl or package manager. It does not test
image build-time networking, which is allowed for toolchain prerequisites.
"""

import errno
from pathlib import Path
import socket

NETWORK_DENIED = {
    errno.ENETDOWN,
    errno.ENETUNREACH,
    errno.EHOSTUNREACH,
    errno.EADDRNOTAVAIL,
}


def _has_ipv4_default_route(route_text: str) -> bool:
    for line in route_text.splitlines()[1:]:
        cols = line.split()
        if not cols:
            continue
        if len(cols) < 4:
            raise RuntimeError("Malformed IPv4 route record")
        if cols[1] == "00000000":
            try:
                if int(cols[3], 16) & 1:
                    return True
            except ValueError:
                raise RuntimeError("Malformed IPv4 route flags")
    return False


def _has_ipv6_default_route(route_text: str) -> bool:
    # /proc/net/ipv6_route stores a 32-hex-digit destination and a hex
    # prefix length. Only an actual ::/0 record is a default route.
    for line in route_text.splitlines():
        columns = line.split()
        if len(columns) < 10:
            raise RuntimeError("Malformed IPv6 route record")
        if columns[0] == "0" * 32 and columns[1] == "00":
            try:
                flags = int(columns[8], 16)
            except ValueError as exc:
                raise RuntimeError("Malformed IPv6 route flags") from exc
            # Linux installs a local ::/0 RTF_REJECT (0x0200) fallback
            # on loopback even in a Docker --network none namespace.
            # A rejecting route cannot carry outbound packets.
            if not flags & 0x0200:
                return True
    return False


def _probe_ipv4_egress() -> None:
    # An explicit numeric destination avoids DNS, proxies and external
    # services. An unexpected success or an unqualified errno must fail.
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=2):
            raise RuntimeError("Unexpected outbound TCP connection succeeded")
    except OSError as exc:
        if exc.errno not in NETWORK_DENIED:
            raise RuntimeError(
                f"Outbound TCP failed for an unqualified reason: {exc.__class__.__name__}"
            ) from exc


def _probe_ipv6_egress() -> None:
    # Use an explicit AF_INET6 socket: no resolver or proxy can turn a
    # numeric IPv6 test into an accidental IPv4 request.
    allowed_denials = NETWORK_DENIED | {
        errno.EAFNOSUPPORT,
        errno.EPROTONOSUPPORT,
    }
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(("2606:4700:4700::1111", 443))
    except OSError as exc:
        if exc.errno not in allowed_denials:
            raise RuntimeError(
                f"IPv6 socket failed for an unqualified reason: {exc.__class__.__name__}"
            ) from exc
    else:
        raise RuntimeError("Unexpected outbound IPv6 TCP connection succeeded")


def assert_network_isolated() -> None:
    route_file = Path("/proc/net/route")
    if not route_file.is_file():
        raise RuntimeError("Cannot establish egress isolation without route data")
    if _has_ipv4_default_route(route_file.read_text(encoding="ascii")):
        raise RuntimeError("Default IPv4 route is present; --network none missing")
    ipv6_routes = Path("/proc/net/ipv6_route")
    if ipv6_routes.is_file() and _has_ipv6_default_route(
        ipv6_routes.read_text(encoding="ascii")
    ):
        raise RuntimeError("Default IPv6 route is present; --network none missing")

    _probe_ipv4_egress()
    _probe_ipv6_egress()


if __name__ == "__main__":
    assert_network_isolated()
    print("No IPv4/IPv6 default route; direct non-stubbed TCP egress denied")
