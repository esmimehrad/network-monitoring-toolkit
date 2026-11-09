"""Shared pytest fixtures, available to every test file automatically.

These fixtures create real TCP endpoints on 127.0.0.1 (this computer only),
so network tests never depend on external servers.
"""

import socket
from collections.abc import Iterator

import pytest


@pytest.fixture
def listening_port() -> Iterator[int]:
    """Open a TCP server on 127.0.0.1 and provide its port number.

    The operating system completes the TCP handshake for us because the socket
    is listening, so the test server needs no accept() loop.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        yield server.getsockname()[1]


@pytest.fixture
def closed_port() -> int:
    """Provide a 127.0.0.1 port number that nothing is listening on.

    Binding to port 0 makes the OS pick a free port; closing the socket
    immediately leaves that port with no listener, so connections are refused.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
