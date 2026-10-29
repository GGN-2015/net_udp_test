import math
import socket
import threading
import time
from dataclasses import replace

import pytest

from udp_probe.config import ProbeConfig
from udp_probe.metrics import PacketTracker, RateSearch, aggregate_trials, evaluate_trial
from udp_probe.protocol import JsonChannel, ProtocolError
from udp_probe.udp import HEADER, MAGIC, UdpEndpoint


@pytest.mark.parametrize(
    "changes",
    [
        {"host_udp_port": 80},
        {"phone_udp_port": 65536},
        {"payload_size": 1401},
        {"duration": 0.1},
        {"start_mbps": math.nan},
        {"max_mbps": math.inf},
        {"min_mbps": 20},
        {"loss_limit": -1},
        {"loss_limit": 1},
        {"loss_limit": 20},
        {"max_steps": 0},
        {"direction": "invalid"},
        {"mode": "invalid"},
        {"host_udp_port": 5002.0},
        {"drain_ms": True},
        {"tolerance": 0},
    ],
)
def test_config_rejects_invalid_values(changes):
    with pytest.raises(ValueError):
        replace(ProbeConfig(), **changes).validate()


def test_search_brackets_and_converges():
    search = RateSearch(replace(ProbeConfig(), start_mbps=10, max_mbps=100, max_steps=30))
    rates = []
    while True:
        rates.append(search.current)
        if search.observe(search.current <= 35) is None:
            break
    assert rates[:3] == [10, 20, 40]
    assert 33.25 <= search.low <= 35
    assert search.high > 35
    assert len(rates) <= 30


@pytest.mark.parametrize("passed,expected", [(True, 200), (False, None)])
def test_search_stops_at_limits(passed, expected):
    search = RateSearch(replace(ProbeConfig(), max_steps=30))
    for _ in range(30):
        if search.observe(passed) is None:
            break
    assert search.low == expected
    assert search.current == (200 if passed else 1)


def test_tracking_deduplicates_and_counts_reordering():
    tracker = PacketTracker(8)
    tracker.record(0, 100, 1000)
    tracker.record(2, 200, 1200)
    tracker.record(1, 150, 1300)
    tracker.record(1, 150, 1400)
    tracker.record(8, 300, 1500)
    tracker.record(-1, 300, 1600)
    result = tracker.snapshot()
    assert result["received"] == 3
    assert result["duplicates"] == 1
    assert result["reordered"] == 1
    assert result["jitter_ms"] > 0


def report(received=100):
    return {"received": received, "duplicates": 0, "reordered": 0, "jitter_ms": 0.1}


def test_measurement_distinguishes_loss_and_sender_limit():
    sender = {"sent": 100, "elapsed": 1, "errors": 0}
    threshold = ProbeConfig().loss_limit
    assert threshold == 5
    assert evaluate_trial(sender, report(), 0.96, 1200, threshold)["status"] == "PASS"
    assert evaluate_trial(sender, report(94), 0.96, 1200, threshold)["status"] == "LOSS"
    limited = evaluate_trial(sender, report(), 10, 1200, threshold)
    assert limited["status"] == "SENDER LIMITED"
    assert limited["goodput_mbps"] == 0.96
    assert evaluate_trial(sender, report(96), 0.96, 1200, threshold)["passed"]


@pytest.mark.parametrize(
    ("received", "expected_status"),
    [(10000, "PASS"), (9501, "PASS"), (9500, "LOSS"), (9499, "LOSS"), (0, "LOSS")],
    ids=["no-loss", "4.99-percent", "5-percent", "5.01-percent", "total-loss"],
)
def test_loss_at_or_above_five_percent_fails(received, expected_status):
    result = evaluate_trial(
        {"sent": 10000, "elapsed": 1, "errors": 0},
        report(received),
        96,
        1200,
        ProbeConfig().loss_limit,
    )
    assert result["status"] == expected_status
    assert result["passed"] == (expected_status == "PASS")
    assert result["offered_mbps"] == 96
    assert result["goodput_mbps"] == received * 1200 * 8 / 1_000_000


def test_zero_packets_and_invalid_remote_metrics():
    assert not evaluate_trial({"sent": 0, "elapsed": 1, "errors": 0}, report(0), 1, 1200, 1)[
        "passed"
    ]
    with pytest.raises(ValueError):
        evaluate_trial({"sent": 5, "elapsed": 1, "errors": 0}, report(6), 1, 1200, 1)
    with pytest.raises(ValueError):
        evaluate_trial(
            {"sent": 100, "elapsed": 1, "errors": 0},
            {**report(), "jitter_ms": math.nan},
            1,
            1200,
            1,
        )


def measurement(trial, loss_percent, sent=10000, errors=0):
    result = evaluate_trial(
        {"sent": sent, "elapsed": sent / 10000, "errors": errors},
        report(round(sent * (1 - loss_percent / 100))),
        96,
        1200,
        ProbeConfig().loss_limit,
    )
    return {
        **result,
        "trial": trial,
        "group": 123,
        "direction": "downlink",
        "phase": "search",
        "repetition": trial,
        "repetitions": 3,
    }


@pytest.mark.parametrize(
    ("losses", "expected", "status"),
    [
        ([0, 4.99, 100], 4.99, "PASS"),
        ([100, 4.99, 0], 4.99, "PASS"),
        ([0, 5, 100], 5, "LOSS"),
        ([5.01, 0, 100], 5.01, "LOSS"),
        ([4, 4, 100], 4, "PASS"),
        ([0, 100, 100], 100, "LOSS"),
    ],
)
def test_rate_test_uses_median_of_three_losses(losses, expected, status):
    samples = [measurement(index, loss) for index, loss in enumerate(losses, 1)]
    result = aggregate_trials(samples)
    assert result["loss_percent"] == pytest.approx(expected)
    assert result["status"] == status
    assert result["passed"] == (status == "PASS")
    assert result["sample_count"] == 3
    assert result["aggregation"] == "median_loss"
    assert result["sample_trials"] == [1, 2, 3]
    assert "repetition" not in result
    representative = next(sample for sample in samples if sample["loss_percent"] == expected)
    assert result["representative_trial"] == result["trial"] == representative["trial"]
    for field in ("offered_mbps", "goodput_mbps", "sent", "received", "elapsed", "jitter_ms"):
        assert result[field] == representative[field]
    assert all("repetition" in sample for sample in samples)


def test_median_loss_is_not_pooled_or_weighted_by_packet_count():
    samples = [measurement(1, 0), measurement(2, 4, sent=20000), measurement(3, 100, sent=100000)]
    result = aggregate_trials(samples)
    assert result["loss_percent"] == 4
    assert result["passed"]
    assert result["sent"] == 20000
    assert result["elapsed"] == 2
    assert result["goodput_mbps"] == pytest.approx(92.16)


def test_all_three_senders_must_meet_pacing_and_error_requirements():
    samples = [measurement(1, 0), measurement(2, 4), measurement(3, 100, errors=1)]
    result = aggregate_trials(samples)
    assert result["loss_percent"] == 4
    assert result["status"] == "SENDER LIMITED"
    assert not result["passed"]


@pytest.mark.parametrize("count", [0, 1, 2, 4])
def test_rate_test_rejects_incomplete_or_extra_repetitions(count):
    with pytest.raises(ValueError, match="exactly 3"):
        aggregate_trials([measurement(index, 0) for index in range(count)])


@pytest.mark.parametrize(
    ("field", "value"),
    [("group", 456), ("direction", "uplink"), ("phase", "verify"), ("target_mbps", 1)],
)
def test_rate_test_rejects_unrelated_samples(field, value):
    samples = [measurement(index, 0) for index in range(1, 4)]
    samples[1][field] = value
    with pytest.raises(ValueError, match="same rate test"):
        aggregate_trials(samples)


def test_rate_test_requires_distinct_trial_ids():
    with pytest.raises(ValueError, match="distinct trial ID"):
        aggregate_trials([measurement(1, 0)] * 3)


def tcp_pair():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        client = socket.create_connection(listener.getsockname())
        connection, _ = listener.accept()
        return client, connection


@pytest.mark.parametrize(
    "raw",
    [b"[]\n", b"{broken\n", b'{"type": 3}\n', b"x" * 65537],
    ids=["array", "invalid-json", "invalid-type", "oversized"],
)
def test_control_rejects_invalid_frames(raw):
    client, connection = tcp_pair()
    channel = JsonChannel(connection)
    try:
        client.sendall(raw)
        with pytest.raises(ProtocolError):
            channel.receive()
    finally:
        channel.close()
        channel.finish_reader()
        client.close()


def test_control_handles_split_and_combined_frames():
    client, connection = tcp_pair()
    channel = JsonChannel(connection)
    try:
        client.sendall(b'{"type":"he')
        client.sendall(b'llo"}\n{"type":"ping"}\n')
        assert channel.receive()["type"] == "hello"
        assert channel.receive()["type"] == "ping"
    finally:
        channel.close()
        channel.finish_reader()
        client.close()


def test_udp_rejects_foreign_session_trial_and_counts_duplicates():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.bind(("127.0.0.1", 0))
        endpoint = UdpEndpoint("127.0.0.1", 0, "127.0.0.1", sender.getsockname()[1], 123)
        try:
            endpoint.begin(456, 1, 1, 128)
            target = endpoint.socket.getsockname()
            for session, trial, sequence in [
                (0, 456, 0),
                (123, 0, 0),
                (123, 456, 0),
                (123, 456, 2),
                (123, 456, 1),
                (123, 456, 1),
            ]:
                packet = HEADER.pack(MAGIC, 1, session, trial, sequence, time.monotonic_ns())
                sender.sendto(packet + bytes(128 - HEADER.size), target)
            time.sleep(0.15)
            assert endpoint.snapshot()["received"] == 3
            assert endpoint.snapshot()["duplicates"] == 1
            assert endpoint.snapshot()["reordered"] == 1
        finally:
            endpoint.close()


def test_sender_cancel_is_prompt():
    endpoint = UdpEndpoint("127.0.0.1", 0, "127.0.0.1", 9, 1)
    cancel = threading.Event()
    cancel.set()
    try:
        started = time.monotonic()
        result = endpoint.send(1, 1, 10, 1200, cancel)
        assert result["sent"] == 0
        assert time.monotonic() - started < 0.5
    finally:
        endpoint.close()
