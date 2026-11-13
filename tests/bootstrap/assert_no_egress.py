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
        if len(cols) >= 4 and cols[1] == "00000000":
            try:
                if int(cols[3], 16) & 1:
                    return True
            except ValueError:
                raise RuntimeError("Malformed IPv4 route flags")
    return False


def assert_network_isolated() -> None:
    route_file = Path("/proc/net/route")
    if not route_file.is_file():
        raise RuntimeError("Cannot establish egress isolation without route data")
    if _has_ipv4_default_route(route_file.read_text(encoding="ascii")):
        raise RuntimeError("Default IPv4 route is present; --network none missing")

    # A fixed numeric destination avoids DNS, proxies and external services.
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=2):
            raise RuntimeError("Unexpected outbound TCP connection succeeded")
    except OSError as exc:
        if exc.errno not in NETWORK_DENIED:
            raise RuntimeError(
                f"Outbound TCP failed for an unqualified reason: {exc.__class__.__name__}"
            ) from exc


if __name__ == "__main__":
    assert_network_isolated()
    print("No IPv4 default route; direct non-stubbed TCP egress denied")
