#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Assert local TCP works and external TCP has the platform's denial result."""
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

try:
    connection = socket.create_connection(("198.51.100.1", 443), timeout=2)
except OSError as error:
    if sys.platform == "darwin":
        assert error.errno in (errno.EPERM, errno.EACCES), error
        print("TCP loopback passed; external TCP rejected by sandbox policy")
    else:
        assert error.errno in (errno.EPERM, errno.EACCES, errno.ENETUNREACH, errno.EHOSTUNREACH), error
        print("TCP loopback passed; external TCP unavailable in the network namespace")
    print(f"external TCP errno: {error.errno}", file=sys.stderr)
else:
    connection.close()
    raise AssertionError("the sandbox unexpectedly permits external TCP traffic")
