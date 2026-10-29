import queue
import socket
import time

import pytest

from udp_probe.host import HostServer


def free_port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, kind) as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


class EventLog:
    def __init__(self):
        self.events = queue.Queue()

    def emit(self, kind, data):
        self.events.put((kind, data))

    def wait(self, expected, timeout=12):
        deadline = time.monotonic() + timeout
        seen = []
        while time.monotonic() < deadline:
            kind, data = self.events.get(timeout=max(0.001, deadline - time.monotonic()))
            seen.append((kind, data))
            if kind == expected:
                return data
            if kind == "error" and expected != "finished":
                pytest.fail(f"Host failed: {data}; events: {seen}")
        pytest.fail(f"No {expected} event; events: {seen}")


@pytest.fixture
def host():
    events = EventLog()
    server = HostServer(events.emit)
    port = free_port()
    server.listen("127.0.0.1", port)
    yield server, events, port
    server.close()
    if server.worker is not None:
        server.worker.join(timeout=6)
