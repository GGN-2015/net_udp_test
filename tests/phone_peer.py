import contextlib
import socket
import threading
import time

from udp_probe.protocol import JsonChannel
from udp_probe.udp import UdpEndpoint


class PhonePeer:
    def __init__(self, port):
        self.channel = JsonChannel(socket.create_connection(("127.0.0.1", port)))
        self.channel.send({"type": "hello", "version": 1, "name": "Python test phone"})
        self.session = self.channel.receive()["session"]
        self.endpoint = None
        self.cancel = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        try:
            while True:
                message = self.channel.receive()
                kind = message["type"]
                if kind == "ping":
                    self.channel.send({"type": "pong"})
                elif kind == "configure":
                    if self.endpoint:
                        self.endpoint.close()
                    self.config = message
                    self.cancel = threading.Event()
                    self.endpoint = UdpEndpoint(
                        "127.0.0.1",
                        message["phone_udp_port"],
                        "127.0.0.1",
                        message["host_udp_port"],
                        self.session,
                    )
                    self.channel.send({"type": "configured", "run": message["run"]})
                elif kind == "trial_begin":
                    self.trial = message
                    self.endpoint.begin(
                        message["trial"],
                        message["target_mbps"],
                        message["duration"],
                        self.config["payload_size"],
                    )
                    self.channel.send(
                        {"type": "trial_ready", "run": message["run"], "trial": message["trial"]}
                    )
                elif kind in {"trial_end", "send"}:
                    threading.Thread(target=self._work, args=(message,), daemon=True).start()
                elif kind == "stop":
                    self.cancel.set()
                    if self.endpoint:
                        self.endpoint.close()
                        self.endpoint = None
                    self.channel.send({"type": "stopped", "run": message["run"]})
        except (OSError, EOFError):
            pass
        finally:
            self.channel.finish_reader()

    def _work(self, message):
        endpoint = self.endpoint
        cancel = self.cancel
        try:
            if message["type"] == "send":
                report = endpoint.send(
                    self.trial["trial"],
                    self.trial["target_mbps"],
                    self.trial["duration"],
                    self.config["payload_size"],
                    cancel,
                )
                report["type"] = "sender_report"
            else:
                if cancel.wait(self.config["drain_ms"] / 1000):
                    return
                report = endpoint.snapshot()
                report["type"] = "receiver_report"
            if not cancel.is_set():
                self.channel.send({**report, "run": message["run"], "trial": message["trial"]})
        except OSError:
            pass

    def close(self):
        self.cancel.set()
        if self.endpoint:
            self.endpoint.close()
        self.channel.close()
        self.thread.join(timeout=2)
        time.sleep(0.01)

    def request_stop(self):
        self.channel.send({"type": "stop_request"})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        with contextlib.suppress(OSError):
            self.close()
