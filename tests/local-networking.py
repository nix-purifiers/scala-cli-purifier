#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Assert local TCP/UDP works and external traffic has the required denial."""
import errno
import socket
import sys

with socket.socket() as server:
    server.settimeout(2)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    with socket.create_connection(server.getsockname(), timeout=2) as client:
        peer, address = server.accept()
        with peer:
            peer.settimeout(2)
            assert address[0] == "127.0.0.1"
            peer.sendall(b"locked-loopback")
            assert client.recv(64) == b"locked-loopback"
            client.sendall(b"ack")
            assert peer.recv(64) == b"ack"

with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
    server.settimeout(2)
    client.settimeout(2)
    server.bind(("127.0.0.1", 0))
    client.bind(("127.0.0.1", 0))
    client.sendto(b"locked-loopback", server.getsockname())
    message, address = server.recvfrom(64)
    assert message == b"locked-loopback" and address[0] == "127.0.0.1"
    server.sendto(b"ack", address)
    assert client.recvfrom(64)[0] == b"ack"


def require_denial(transport, operation):
    try:
        result = operation()
    except OSError as error:
        expected = (errno.EPERM, errno.EACCES)
        if sys.platform != "darwin":
            expected += (errno.ENETUNREACH, errno.EHOSTUNREACH)
        assert error.errno in expected, (transport, error)
        print(f"external {transport} errno: {error.errno}", file=sys.stderr)
    else:
        if hasattr(result, "close"):
            result.close()
        raise AssertionError(f"the sandbox unexpectedly permits external {transport} traffic")


require_denial("TCP", lambda: socket.create_connection(("198.51.100.1", 443), timeout=2))
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as outbound:
    outbound.settimeout(2)
    require_denial("UDP", lambda: outbound.sendto(b"denial-probe", ("198.51.100.1", 443)))
if sys.platform == "darwin":
    print("TCP/UDP loopback passed; external TCP/UDP rejected by sandbox policy")
else:
    print("TCP/UDP loopback passed; external TCP/UDP unavailable in the network namespace")
