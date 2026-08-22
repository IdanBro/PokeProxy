from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EndpointStats:
    request_count: int = 0
    error_count: int = 0
    total_response_time: float = 0.0
    bytes_received: int = 0
    bytes_sent: int = 0

    def record_request(self, *, is_error: bool) -> None:
        self.request_count += 1
        if is_error:
            self.error_count += 1

    def record_response_time(self, elapsed: float) -> None:
        self.total_response_time += elapsed

    @property
    def avg_response_time(self) -> float:
        return (
            self.total_response_time / self.request_count if self.request_count else 0.0
        )

    @property
    def error_rate(self) -> float:
        return self.error_count / self.request_count if self.request_count else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_count": self.request_count,
            "error_count": self.error_count,
            "error_rate": round(self.error_rate, 4),
            "bytes_received": self.bytes_received,
            "bytes_sent": self.bytes_sent,
        }


@dataclass
class StatsRegistry:
    endpoints: dict[str, EndpointStats] = field(default_factory=dict)
    outcomes: dict[str, int] = field(default_factory=dict)

    def get(self, url: str) -> EndpointStats:
        return self.endpoints.setdefault(url, EndpointStats())

    def record_outcome(self, outcome: str) -> None:
        self.outcomes[outcome] = self.outcomes.get(outcome, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoints": {url: stats.to_dict() for url, stats in self.endpoints.items()},
            "outcomes": dict(self.outcomes),
        }
