import socket
import time
from dataclasses import replace
from statistics import median

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


def assert_complete_groups(report):
    assert report["repetitions_per_rate"] == 3
    assert len(report["samples"]) == len(report["records"]) * 3
    assert len({sample["trial"] for sample in report["samples"]}) == len(report["samples"])
    for record in report["records"]:
        samples = [sample for sample in report["samples"] if sample["group"] == record["group"]]
        assert [sample["repetition"] for sample in samples] == [1, 2, 3]
        assert all(sample["repetitions"] == 3 for sample in samples)
        assert record["sample_count"] == 3
        assert record["sample_trials"] == [sample["trial"] for sample in samples]
        assert record["loss_percent"] == median(sample["loss_percent"] for sample in samples)
        for field in ("direction", "phase", "target_mbps"):
            assert all(sample[field] == record[field] for sample in samples)


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
            assert_complete_groups(result)


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
        assert_complete_groups(result)


def test_auto_uses_median_instead_of_last_trial_for_search_and_verification(host, monkeypatch):
    original_snapshot = UdpEndpoint.snapshot
    snapshots = 0

    def snapshot_with_one_outlier(endpoint):
        nonlocal snapshots
        snapshot = original_snapshot(endpoint)
        snapshots += 1
        if snapshots % 3 == 0:
            snapshot["received"] = 0
        return snapshot

    monkeypatch.setattr(UdpEndpoint, "snapshot", snapshot_with_one_outlier)
    server, events, port = host
    with PhonePeer(port):
        events.wait("connected")
        server.start_probe(replace(configuration(), mode="auto", direction="downlink", max_steps=2))
        result = events.wait("finished")
        assert result["outcome"] == "completed"
        assert_complete_groups(result)
        assert [record["target_mbps"] for record in result["records"]] == [0.5, 1, 1]
        assert [record["phase"] for record in result["records"]] == ["search", "search", "verify"]
        assert all(record["passed"] for record in result["records"])
        assert all(sample["loss_percent"] == 100 for sample in result["samples"][2::3])
        assert result["recommendations"]["downlink"]["target_mbps"] == 1


def test_stop_retains_completed_samples_without_evaluating_incomplete_group(host):
    server, events, port = host
    with PhonePeer(port):
        events.wait("connected")
        server.start_probe(replace(configuration(), direction="downlink"))
        sample = events.wait("sample")
        server.stop_probe()
        result = events.wait("finished")
        assert result["outcome"] == "stopped"
        assert result["samples"][0] == sample
        assert 1 <= len(result["samples"]) < 3
        assert result["records"] == []
        assert result["recommendations"] == {}


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
        assert_complete_groups(result)
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
