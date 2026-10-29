from dataclasses import asdict, dataclass
from math import isfinite


@dataclass(frozen=True)
class ProbeConfig:
    host_udp_port: int = 5002
    phone_udp_port: int = 5003
    direction: str = "both"
    mode: str = "auto"
    min_mbps: float = 1.0
    start_mbps: float = 10.0
    max_mbps: float = 200.0
    duration: float = 3.0
    payload_size: int = 1200
    loss_limit: float = 1.0
    max_steps: int = 10
    drain_ms: int = 400
    tolerance: float = 0.05

    def validate(self):
        for name in ("host_udp_port", "phone_udp_port"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1024 <= value <= 65535:
                raise ValueError(f"{name} must be an integer between 1024 and 65535.")
        for name in ("min_mbps", "start_mbps", "max_mbps", "duration", "loss_limit", "tolerance"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
            ):
                raise ValueError(f"{name} must be a finite number.")
        if not 0.01 <= self.min_mbps <= self.start_mbps <= self.max_mbps <= 1000:
            raise ValueError("Rates must satisfy 0.01 <= minimum <= start <= maximum <= 1000 Mbps.")
        if not 0.5 <= self.duration <= 30:
            raise ValueError("Trial duration must be between 0.5 and 30 seconds.")
        for name, lower, upper in (
            ("payload_size", 128, 1400),
            ("max_steps", 1, 30),
            ("drain_ms", 100, 3000),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
                raise ValueError(f"{name} must be an integer between {lower} and {upper}.")
        if not 0 <= self.loss_limit <= 20 or not 0.01 <= self.tolerance <= 0.25:
            raise ValueError("Loss must be 0-20 percent; search tolerance must be 0.01-0.25.")
        if self.direction not in {"downlink", "uplink", "both"}:
            raise ValueError("Invalid direction.")
        if self.mode not in {"auto", "fixed"}:
            raise ValueError("Invalid probe mode.")
        return self

    def to_dict(self):
        return asdict(self)
