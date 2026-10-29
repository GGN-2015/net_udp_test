import socket
import time
from dataclasses import replace

import pytest
from conftest import free_port
from phone_peer import PhonePeer

from udp_probe.config import ProbeConfig
from udp_probe.protocol import JsonChannel
from udp_probe.udp import UdpEndpoint


def configuration(**changes):
    host_port = free_port(socket.SOCK_DGRAM)
    phone_port = free_port(socket.SOCK_DGRAM)
    while host_port == phone_port:
        phone_port = free_port(socket.SOCK_DGRAM)
    return replace(
        ProbeConfig(),
        host_udp_port=host_port,
        phone_udp_port=phone_port,
        min_mbps=0.1,
        start_mbps=0.5,
        max_mbps=1,
        duration=0.5,
        mode="fixed",
        drain_ms=100,
        **changes,
    )


def test_fixed_both_directions_and_repeat(host):
    server, events, port = host
    with PhonePeer(port):
        events.wait("connected")
        config = configuration()
        for _ in range(2):
            server.start_probe(config)
            result = events.wait("finished")
            assert result["outcome"] == "completed"
            assert [record["direction"] for record in result["records"]] == ["downlink", "uplink"]
            assert all(record["passed"] for record in result["records"])


@pytest.mark.parametrize("from_phone", [False, True])
def test_stop_and_restart(host, from_phone):
    server, events, port = host
    with PhonePeer(port) as phone:
        events.wait("connected")
        config = configuration()
        server.start_probe(replace(config, duration=10))
        events.wait("trial")
        started = time.monotonic()
        phone.request_stop() if from_phone else server.stop_probe()
        result = events.wait("finished")
        assert result["outcome"] == "stopped"
        assert time.monotonic() - started < 3
        server.start_probe(replace(config, direction="downlink"))
        assert events.wait("finished")["outcome"] == "completed"


def test_auto_verifies_passing_candidate(host):
    server, events, port = host
    with PhonePeer(port):
        events.wait("connected")
        server.start_probe(replace(configuration(), mode="auto", direction="downlink", max_steps=2))
        result = events.wait("finished")
        assert result["outcome"] == "completed"
        assert result["records"][-1]["phase"] == "verify"
        assert result["recommendations"]["downlink"]["passed"]


def test_auto_rejects_five_percent_or_higher_loss(host, monkeypatch):
    original_snapshot = UdpEndpoint.snapshot

    def snapshot_with_loss(endpoint):
        snapshot = original_snapshot(endpoint)
        snapshot["received"] = snapshot["received"] * 95 // 100
        return snapshot

    monkeypatch.setattr(UdpEndpoint, "snapshot", snapshot_with_loss)
    server, events, port = host
    with PhonePeer(port) as phone:
        events.wait("connected")
        server.start_probe(replace(configuration(), mode="auto", max_steps=1))
        result = events.wait("finished")
        assert result["outcome"] == "completed"
        assert result["config"]["loss_limit"] == phone.config["loss_limit"] == 5
        assert result["recommendations"] == {"downlink": None, "uplink": None}
        assert len(result["records"]) == 2
        for record in result["records"]:
            assert record["loss_percent"] >= 5
            assert record["status"] == "LOSS"
            assert not record["passed"]


def test_disconnect_during_trial_and_reconnect(host):
    server, events, port = host
    phone = PhonePeer(port)
    events.wait("connected")
    server.start_probe(replace(configuration(), duration=10))
    events.wait("trial")
    phone.close()
    assert events.wait("finished")["outcome"] != "completed"
    with PhonePeer(port):
        events.wait("connected")
        server.start_probe(replace(configuration(), direction="downlink"))
        assert events.wait("finished")["outcome"] == "completed"


def test_wrong_protocol_is_rejected(host):
    _, events, port = host
    channel = JsonChannel(socket.create_connection(("127.0.0.1", port)))
    try:
        channel.send({"type": "hello", "version": 99})
        with pytest.raises(EOFError):
            channel.receive()
        events.wait("disconnected")
    finally:
        channel.close()
        channel.finish_reader()


def test_udp_bind_conflict_is_reported_without_disconnect(host):
    server, events, port = host
    with PhonePeer(port), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as occupied:
        events.wait("connected")
        occupied.bind(("127.0.0.1", 0))
        config = replace(configuration(), host_udp_port=occupied.getsockname()[1])
        server.start_probe(config)
        assert events.wait("finished")["outcome"] == "error"
        assert server.peer is not None and not server.peer.closed.is_set()
