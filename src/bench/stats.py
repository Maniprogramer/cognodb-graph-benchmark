"""Latency statistics.

Percentiles are computed with the "nearest-rank" method rather than numpy's
default linear interpolation. Nearest-rank returns an actually-observed
measurement, which is what you want for latency reporting: an interpolated p95
is a number the system never produced.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile. `pct` is 0-100."""
    if not values:
        raise ValueError("percentile() of empty sequence")
    if not 0 < pct <= 100:
        raise ValueError(f"pct must be in (0, 100], got {pct}")
    ordered = sorted(values)
    rank = math.ceil(pct / 100 * len(ordered))
    return ordered[rank - 1]


def stdev(values: list[float]) -> float:
    """Sample standard deviation. Returns 0.0 for fewer than two values."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


@dataclass
class LatencySummary:
    """Summary of one workload's latency samples. All times in milliseconds."""

    workload: str
    iterations: int
    p50: float
    p95: float
    p99: float
    mean: float
    min: float
    max: float
    stdev: float
    errors: int = 0
    error_samples: list[str] = field(default_factory=list)

    @classmethod
    def from_samples(
        cls,
        workload: str,
        samples_ms: list[float],
        errors: int = 0,
        error_samples: list[str] | None = None,
    ) -> "LatencySummary":
        if not samples_ms:
            # A workload that failed entirely still has to appear in the results
            # matrix -- reporting it as absent would hide a failed run.
            return cls(
                workload=workload,
                iterations=0,
                p50=float("nan"),
                p95=float("nan"),
                p99=float("nan"),
                mean=float("nan"),
                min=float("nan"),
                max=float("nan"),
                stdev=float("nan"),
                errors=errors,
                error_samples=(error_samples or [])[:3],
            )
        return cls(
            workload=workload,
            iterations=len(samples_ms),
            p50=percentile(samples_ms, 50),
            p95=percentile(samples_ms, 95),
            p99=percentile(samples_ms, 99),
            mean=sum(samples_ms) / len(samples_ms),
            min=min(samples_ms),
            max=max(samples_ms),
            stdev=stdev(samples_ms),
            errors=errors,
            error_samples=(error_samples or [])[:3],
        )

    def to_dict(self) -> dict:
        return asdict(self)
