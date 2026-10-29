import json
import socket
import threading

VERSION = 1
MAX_MESSAGE = 65536


class ProtocolError(Exception):
    pass


def reject_constant(value):
    raise ValueError(f"Non-finite JSON constant: {value}")


class JsonChannel:
    def __init__(self, connection):
        self.socket = connection
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.reader = connection.makefile("rb")
        self.lock = threading.Lock()

    def send(self, message):
        encoded = (json.dumps(message, allow_nan=False, separators=(",", ":")) + "\n").encode()
        if len(encoded) > MAX_MESSAGE:
            raise ProtocolError("Control message is too large.")
        with self.lock:
            self.socket.sendall(encoded)

    def receive(self):
        raw = self.reader.readline(MAX_MESSAGE + 1)
        if not raw:
            raise EOFError("TCP peer disconnected.")
        if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
            raise ProtocolError("Invalid control frame length.")
        try:
            message = json.loads(raw, parse_constant=reject_constant)
        except (ValueError, UnicodeError) as error:
            raise ProtocolError("Invalid JSON control message.") from error
        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            raise ProtocolError("Control message must have a string type.")
        return message

    def close(self):
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.socket.close()

    def finish_reader(self):
        self.reader.close()
