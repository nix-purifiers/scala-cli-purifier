#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Assert the mirror's local socket works and external outbound traffic fails."""
import socket

with socket.socket() as server:
    server.settimeout(2)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    with socket.create_connection(server.getsockname(), timeout=2) as client:
        peer, address = server.accept()
        with peer:
            assert address[0] == "127.0.0.1"
            peer.sendall(b"locked-loopback")
            assert client.recv(64) == b"locked-loopback"

try:
    connection = socket.create_connection(("1.1.1.1", 443), timeout=2)
except OSError:
    print("loopback exchange passed; external outbound connection denied")
else:
    connection.close()
    raise AssertionError("the sandbox unexpectedly permits external outbound traffic")
