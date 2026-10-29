import math
import socket
import struct
import threading
import time

from .metrics import PacketTracker

HEADER = struct.Struct("!4sB3xQQIQ")
MAGIC = b"URP1"
BUFFER_SIZE = 4 * 1024 * 1024


class UdpEndpoint:
    def __init__(self, bind_ip, local_port, peer_ip, peer_port, session):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, BUFFER_SIZE)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, BUFFER_SIZE)
            self.socket.bind((bind_ip, local_port))
            self.socket.settimeout(0.1)
        except BaseException:
            self.socket.close()
            raise
        self.peer = (peer_ip, peer_port)
        self.session = session
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.trial = None
        self.tracker = None
        self.payload_size = 0
        self.thread = threading.Thread(target=self._receive, name="udp-receiver", daemon=True)
        self.thread.start()

    def begin(self, trial, target_mbps, duration, payload_size):
        capacity = math.ceil(target_mbps * 1_000_000 / 8 / payload_size * duration * 1.5) + 4096
        with self.lock:
            self.tracker = PacketTracker(capacity)
            self.payload_size = payload_size
            self.trial = trial

    def snapshot(self):
        with self.lock:
            return self.tracker.snapshot()

    def _receive(self):
        buffer = bytearray(65536)
        while not self.closed.is_set():
            try:
                length, address = self.socket.recvfrom_into(buffer)
                received_ns = time.monotonic_ns()
            except TimeoutError:
                continue
            except OSError:
                return
            if address != self.peer or length < HEADER.size:
                continue
            magic, version, session, trial, sequence, sent_ns = HEADER.unpack_from(buffer)
            if magic != MAGIC or version != 1 or session != self.session:
                continue
            with self.lock:
                if trial == self.trial and length == self.payload_size:
                    self.tracker.record(sequence, sent_ns, received_ns)

    def send(self, trial, target_mbps, duration, payload_size, cancel):
        packet = bytearray(payload_size)
        packets_per_second = target_mbps * 1_000_000 / 8 / payload_size
        start = time.monotonic()
        sent = 0
        errors = 0
        while not cancel.is_set() and not self.closed.is_set():
            now = time.monotonic()
            if now - start >= duration:
                break
            due = min(int((now - start) * packets_per_second) + 1, sent + 64)
            if due <= sent:
                wait = (sent / packets_per_second) - (now - start)
                cancel.wait(min(max(wait, 0), 0.001))
                continue
            for _ in range(due - sent):
                if cancel.is_set() or time.monotonic() - start >= duration:
                    break
                HEADER.pack_into(
                    packet, 0, MAGIC, 1, self.session, trial, sent, time.monotonic_ns()
                )
                try:
                    self.socket.sendto(packet, self.peer)
                except OSError:
                    if cancel.is_set() or self.closed.is_set():
                        break
                    errors += 1
                    cancel.wait(0.001)
                    break
                sent += 1
        return {"sent": sent, "errors": errors, "elapsed": max(time.monotonic() - start, 0.000001)}

    def close(self):
        self.closed.set()
        self.socket.close()
        if threading.current_thread() is not self.thread:
            self.thread.join(timeout=1)
