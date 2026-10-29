import math


class RateSearch:
    def __init__(self, config):
        self.config = config
        self.low = None
        self.high = None
        self.current = config.start_mbps
        self.steps = 0

    def observe(self, passed):
        self.steps += 1
        if passed:
            self.low = self.current
        else:
            self.high = self.current
        if self.steps >= self.config.max_steps:
            return None
        if self.low is not None and self.high is not None:
            if self.high - self.low <= max(self.low, self.config.min_mbps) * self.config.tolerance:
                return None
            candidate = (self.low + self.high) / 2
        elif self.low is not None:
            candidate = min(self.config.max_mbps, self.current * 2)
        else:
            candidate = max(self.config.min_mbps, self.current / 2)
        if math.isclose(candidate, self.current):
            return None
        self.current = candidate
        return candidate


class PacketTracker:
    def __init__(self, max_packets):
        self.seen = bytearray((max_packets + 7) // 8)
        self.max_packets = max_packets
        self.received = 0
        self.duplicates = 0
        self.reordered = 0
        self.highest = -1
        self.jitter_ns = 0.0
        self.last_transit = None

    def record(self, sequence, sent_ns, received_ns):
        if not 0 <= sequence < self.max_packets:
            return
        index, bit = divmod(sequence, 8)
        mask = 1 << bit
        if self.seen[index] & mask:
            self.duplicates += 1
            return
        self.seen[index] |= mask
        self.received += 1
        if sequence < self.highest:
            self.reordered += 1
        self.highest = max(self.highest, sequence)
        transit = received_ns - sent_ns
        if self.last_transit is not None:
            self.jitter_ns += (abs(transit - self.last_transit) - self.jitter_ns) / 16
        self.last_transit = transit

    def snapshot(self):
        return {
            "received": self.received,
            "duplicates": self.duplicates,
            "reordered": self.reordered,
            "jitter_ms": self.jitter_ns / 1_000_000,
        }


def evaluate_trial(sender, receiver, target_mbps, payload_size, loss_limit):
    sent = int(sender["sent"])
    received = int(receiver["received"])
    elapsed = float(sender["elapsed"])
    errors = int(sender["errors"])
    duplicates = int(receiver["duplicates"])
    reordered = int(receiver["reordered"])
    jitter = float(receiver["jitter_ms"])
    if sent < 0 or not 0 <= received <= sent or not math.isfinite(elapsed) or elapsed <= 0:
        raise ValueError("Invalid packet counts or trial duration from peer.")
    if errors < 0 or duplicates < 0 or not 0 <= reordered <= received:
        raise ValueError("Invalid error, duplicate, or reordered count from peer.")
    if not math.isfinite(jitter) or jitter < 0:
        raise ValueError("Invalid jitter from peer.")
    loss = 100 * (sent - received) / sent if sent else 100.0
    offered = sent * payload_size * 8 / elapsed / 1_000_000
    goodput = received * payload_size * 8 / elapsed / 1_000_000
    quantization = payload_size * 8 / elapsed / 1_000_000
    pacing_ok = abs(offered - target_mbps) <= max(target_mbps * 0.1, quantization) and errors == 0
    status = "PASS" if received and loss < loss_limit and pacing_ok else "LOSS"
    if not pacing_ok:
        status = "SENDER LIMITED"
    return {
        "target_mbps": target_mbps,
        "offered_mbps": offered,
        "goodput_mbps": goodput,
        "loss_percent": loss,
        "sent": sent,
        "received": received,
        "send_errors": errors,
        "elapsed": elapsed,
        "duplicates": duplicates,
        "reordered": reordered,
        "jitter_ms": jitter,
        "status": status,
        "passed": status == "PASS",
    }
