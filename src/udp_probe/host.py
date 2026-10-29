import contextlib
import queue
import secrets
import socket
import threading
import time
from datetime import datetime, timezone

from .config import TRIALS_PER_RATE
from .metrics import RateSearch, aggregate_trials, evaluate_trial
from .protocol import VERSION, JsonChannel, ProtocolError
from .udp import UdpEndpoint


class ProbeCancelled(Exception):
    pass


class Peer:
    def __init__(self, connection, address):
        self.channel = JsonChannel(connection)
        self.address = address
        self.session = secrets.randbits(63) or 1
        self.messages = queue.Queue(maxsize=256)
        self.closed = threading.Event()
        self.ready = False
        self.last_received = time.monotonic()

    def close(self):
        self.closed.set()
        self.channel.close()


class HostServer:
    def __init__(self, emit):
        self.emit = emit
        self.lock = threading.RLock()
        self.listener = None
        self.peer = None
        self.bind_ip = "0.0.0.0"
        self.cancel = threading.Event()
        self.busy = False
        self.run_id = None
        self.worker = None

    def listen(self, bind_ip, tcp_port):
        socket.inet_pton(socket.AF_INET, bind_ip)
        if not 1024 <= tcp_port <= 65535:
            raise ValueError("TCP port must be between 1024 and 65535.")
        with self.lock:
            if self.listener is not None:
                raise ValueError("The host is already listening.")
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((bind_ip, tcp_port))
                listener.listen(4)
                listener.settimeout(0.5)
            except BaseException:
                listener.close()
                raise
            self.listener = listener
            self.bind_ip = bind_ip
        threading.Thread(target=self._accept, args=(listener,), daemon=True).start()
        self.emit("listening", {"bind_ip": bind_ip, "tcp_port": tcp_port})

    def _accept(self, listener):
        while self.listener is listener:
            try:
                connection, address = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with self.lock:
                if self.peer is not None or self.busy or self.listener is not listener:
                    connection.close()
                    continue
                connection.settimeout(5)
                peer = Peer(connection, address)
                self.peer = peer
            threading.Thread(target=self._read_peer, args=(peer,), daemon=True).start()

    def _read_peer(self, peer):
        try:
            hello = peer.channel.receive()
            if hello.get("type") != "hello" or hello.get("version") != VERSION:
                raise ProtocolError("Unsupported client protocol version.")
            peer.channel.socket.settimeout(None)
            peer.channel.send({"type": "welcome", "version": VERSION, "session": peer.session})
            peer.ready = True
            self.emit(
                "connected", {"ip": peer.address[0], "name": str(hello.get("name", "Android"))}
            )
            threading.Thread(target=self._heartbeat, args=(peer,), daemon=True).start()
            while not peer.closed.is_set():
                message = peer.channel.receive()
                peer.last_received = time.monotonic()
                if message["type"] == "pong":
                    continue
                if message["type"] == "stop_request":
                    self.stop_probe()
                    continue
                peer.messages.put_nowait(message)
        except (OSError, EOFError, ProtocolError, queue.Full) as error:
            if not peer.closed.is_set():
                self.emit("log", {"message": str(error)})
        finally:
            peer.close()
            peer.channel.finish_reader()
            with self.lock:
                if self.peer is peer:
                    self.cancel.set()
                    self.peer = None
                    self.emit("disconnected", {})

    def _heartbeat(self, peer):
        while not peer.closed.wait(2):
            if time.monotonic() - peer.last_received > 15:
                self.emit("log", {"message": "TCP heartbeat timed out."})
                peer.close()
                return
            try:
                peer.channel.send({"type": "ping"})
            except OSError:
                peer.close()
                return

    def start_probe(self, config):
        config.validate()
        with self.lock:
            if self.peer is None or not self.peer.ready or self.peer.closed.is_set():
                raise ValueError("Connect the Android app first.")
            if self.busy:
                raise ValueError("A probe is already running.")
            self.busy = True
            self.cancel = threading.Event()
            self.run_id = secrets.randbits(63) or 1
            peer = self.peer
            self.worker = threading.Thread(target=self._run, args=(peer, config), daemon=True)
            self.worker.start()

    def stop_probe(self):
        with self.lock:
            self.cancel.set()
            if self.busy and self.peer is not None:
                with contextlib.suppress(OSError):
                    self.peer.channel.send({"type": "stop", "run": self.run_id})

    def disconnect(self):
        with self.lock:
            self.cancel.set()
            if self.peer is not None:
                self.peer.close()

    def close(self):
        self.disconnect()
        with self.lock:
            listener, self.listener = self.listener, None
            if listener is not None:
                listener.close()

    def _wait(self, peer, kind, timeout, trial=None, allow_cancel=False):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not allow_cancel and self.cancel.is_set():
                raise ProbeCancelled()
            if peer.closed.is_set():
                raise EOFError("Phone disconnected during the probe.")
            try:
                message = peer.messages.get(
                    timeout=min(0.1, max(0.001, deadline - time.monotonic()))
                )
            except queue.Empty:
                continue
            if message.get("run") != self.run_id:
                continue
            if message["type"] == "error":
                raise ProtocolError(str(message.get("message", "Phone reported an error.")))
            if message["type"] == kind and (trial is None or message.get("trial") == trial):
                return message
        raise TimeoutError(f"Timed out waiting for {kind}; check UDP ports and phone status.")

    def _trial(self, peer, endpoint, config, direction, rate, phase, group, repetition):
        if self.cancel.is_set():
            raise ProbeCancelled()
        trial = secrets.randbits(63) or 1
        duration = min(30, max(3, config.duration * 1.5)) if phase == "verify" else config.duration
        endpoint.begin(trial, rate, duration, config.payload_size)
        self.emit(
            "trial",
            {
                "direction": direction,
                "rate": rate,
                "phase": phase,
                "duration": duration,
                "group": group,
                "repetition": repetition,
                "repetitions": TRIALS_PER_RATE,
            },
        )
        peer.channel.send(
            {
                "type": "trial_begin",
                "run": self.run_id,
                "trial": trial,
                "direction": direction,
                "target_mbps": rate,
                "duration": duration,
                "phase": phase,
                "group": group,
                "repetition": repetition,
                "repetitions": TRIALS_PER_RATE,
            }
        )
        self._wait(peer, "trial_ready", 5, trial)
        if direction == "downlink":
            sender = endpoint.send(trial, rate, duration, config.payload_size, self.cancel)
            if self.cancel.is_set():
                raise ProbeCancelled()
            peer.channel.send({"type": "trial_end", "run": self.run_id, "trial": trial})
            receiver = self._wait(peer, "receiver_report", 5 + config.drain_ms / 1000, trial)
        else:
            peer.channel.send({"type": "send", "run": self.run_id, "trial": trial})
            sender = self._wait(peer, "sender_report", duration + 8, trial)
            if self.cancel.wait(config.drain_ms / 1000):
                raise ProbeCancelled()
            receiver = endpoint.snapshot()
        result = evaluate_trial(sender, receiver, rate, config.payload_size, config.loss_limit)
        result.update(
            direction=direction,
            phase=phase,
            trial=trial,
            group=group,
            repetition=repetition,
            repetitions=TRIALS_PER_RATE,
        )
        return result

    def _rate_test(self, peer, endpoint, config, direction, rate, phase, records, samples):
        group = secrets.randbits(63) or 1
        group_samples = []
        for repetition in range(1, TRIALS_PER_RATE + 1):
            sample = self._trial(peer, endpoint, config, direction, rate, phase, group, repetition)
            samples.append(sample)
            group_samples.append(sample)
            self.emit("sample", sample)
        result = aggregate_trials(group_samples)
        records.append(result)
        self.emit("result", result)
        peer.channel.send({"type": "result", "run": self.run_id, **result})
        return result

    def _run(self, peer, config):
        endpoint = None
        records = []
        samples = []
        recommendations = {}
        outcome = "completed"
        self.emit("running", {"config": config.to_dict(), "peer": peer.address[0]})
        try:
            endpoint = UdpEndpoint(
                self.bind_ip,
                config.host_udp_port,
                peer.address[0],
                config.phone_udp_port,
                peer.session,
            )
            peer.channel.send({"type": "configure", "run": self.run_id, **config.to_dict()})
            self._wait(peer, "configured", 5)
            directions = (
                ("downlink", "uplink") if config.direction == "both" else (config.direction,)
            )
            for direction in directions:
                if config.mode == "fixed":
                    self._rate_test(
                        peer,
                        endpoint,
                        config,
                        direction,
                        config.start_mbps,
                        "fixed",
                        records,
                        samples,
                    )
                    continue
                search = RateSearch(config)
                candidates = []
                while True:
                    result = self._rate_test(
                        peer,
                        endpoint,
                        config,
                        direction,
                        search.current,
                        "search",
                        records,
                        samples,
                    )
                    if result["passed"]:
                        candidates.append(result["target_mbps"])
                    if search.observe(result["passed"]) is None:
                        break
                recommendations[direction] = None
                for rate in sorted(candidates, reverse=True)[:3]:
                    confirmed = self._rate_test(
                        peer, endpoint, config, direction, rate, "verify", records, samples
                    )
                    if confirmed["passed"]:
                        recommendations[direction] = confirmed
                        break
            peer.channel.send(
                {"type": "complete", "run": self.run_id, "recommendations": recommendations}
            )
        except ProbeCancelled:
            outcome = "stopped"
        except (
            OSError,
            EOFError,
            ProtocolError,
            ValueError,
            KeyError,
            TypeError,
            TimeoutError,
        ) as error:
            outcome = "error"
            self.emit("error", {"message": str(error)})
        finally:
            if endpoint is not None:
                endpoint.close()
            if not peer.closed.is_set():
                try:
                    peer.channel.send({"type": "stop", "run": self.run_id})
                    self._wait(peer, "stopped", 4, allow_cancel=True)
                except (OSError, EOFError, ProtocolError, TimeoutError):
                    peer.close()
            with self.lock:
                self.busy = False
            self.emit(
                "finished",
                {
                    "outcome": outcome,
                    "records": records,
                    "samples": samples,
                    "repetitions_per_rate": TRIALS_PER_RATE,
                    "recommendations": recommendations,
                    "config": config.to_dict(),
                    "peer": peer.address[0],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
