"""Mixed read/write workload under concurrent clients.

Each worker builds and connects **its own adapter**. Sharing one connection
across threads would measure driver-level lock contention rather than the
database's concurrency behaviour, and the three drivers here differ in their
thread-safety guarantees -- python-arango in particular is not safe to share
freely. Giving every worker its own client is both realistic (that is what N
concurrent clients means) and uniform across platforms.

Writes create relationships tagged ``benchmark:true`` so they can be removed
afterwards. Without that cleanup the graph would grow during the sweep and
later concurrency levels would query a larger database than earlier ones --
the results would drift for reasons unrelated to concurrency.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass

from .stats import LatencySummary, percentile


@dataclass
class ConcurrencyResult:
    clients: int
    duration_seconds: float
    read_ratio: float
    total_operations: int
    reads: int
    writes: int
    errors: int
    throughput_qps: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    error_samples: list[str]

    def to_dict(self) -> dict:
        return {
            "clients": self.clients,
            "duration_seconds": round(self.duration_seconds, 2),
            "read_ratio": self.read_ratio,
            "total_operations": self.total_operations,
            "reads": self.reads,
            "writes": self.writes,
            "errors": self.errors,
            "throughput_qps": round(self.throughput_qps, 1),
            "p50_ms": round(self.p50_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
            "p99_ms": round(self.p99_ms, 2),
            "error_samples": self.error_samples[:3],
        }


def run_mixed_workload(
    adapter_factory,
    plan,
    clients: int,
    duration_seconds: float,
    read_ratio: float = 0.8,
    seed: int = 1337,
    join_grace_seconds: float | None = None,
) -> ConcurrencyResult:
    """Drive `clients` workers for `duration_seconds` against one platform."""
    latencies: list[float] = []
    errors: list[str] = []
    counters = {"reads": 0, "writes": 0}
    lock = threading.Lock()
    # The measurement window opens when the barrier releases, not when the
    # threads are created: connecting 40 clients can take longer than the window
    # itself, and a deadline started before that would expire during setup and
    # record zero operations. The barrier's action runs in whichever thread
    # arrives last, before any thread is released, so the deadline is always set
    # exactly once and is visible to every worker the moment it wakes.
    deadline: list[float] = []

    def start_clock() -> None:
        deadline.append(time.perf_counter() + duration_seconds)

    barrier = threading.Barrier(clients + 1, action=start_clock)

    def worker(worker_id: int) -> None:
        rng = random.Random(seed + worker_id)
        local_lat: list[float] = []
        local_err: list[str] = []
        local_reads = local_writes = 0
        adapter = None
        try:
            adapter = adapter_factory()
            adapter.connect()
        except Exception as exc:  # a worker that cannot connect still reports
            local_err.append(f"connect: {type(exc).__name__}: {exc}")
            barrier.wait()
            with lock:
                errors.extend(local_err)
            return

        barrier.wait()  # all workers start measuring together
        stop_at = deadline[0]
        while time.perf_counter() < stop_at:
            is_read = rng.random() < read_ratio
            t0 = time.perf_counter()
            try:
                if is_read:
                    node = rng.choice(plan.start_nodes)
                    adapter.one_hop(node)
                    local_reads += 1
                else:
                    src, dst = rng.choice(plan.write_pairs)
                    adapter.insert_edge(src, dst)
                    local_writes += 1
                local_lat.append((time.perf_counter() - t0) * 1000.0)
            except Exception as exc:
                local_err.append(f"{type(exc).__name__}: {exc}")

        try:
            adapter.close()
        except Exception:
            pass

        with lock:
            latencies.extend(local_lat)
            errors.extend(local_err)
            counters["reads"] += local_reads
            counters["writes"] += local_writes

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(clients)]
    for t in threads:
        t.start()

    barrier.wait()
    started = deadline[0] - duration_seconds

    # Bounded joins. A worker stuck on an unresponsive server would otherwise
    # hang the entire benchmark; the grace period is generous enough that a
    # slow-but-live query still finishes, and anything past it is reported as a
    # stuck worker rather than silently waited on. Threads are daemons, so a
    # straggler cannot keep the process alive.
    grace = join_grace_seconds if join_grace_seconds is not None else max(30.0, duration_seconds)
    stuck = 0
    for t in threads:
        remaining = (started + duration_seconds + grace) - time.perf_counter()
        t.join(timeout=max(remaining, 1.0))
        if t.is_alive():
            stuck += 1
    elapsed = time.perf_counter() - started

    if stuck:
        with lock:
            errors.append(
                f"{stuck} of {clients} workers did not finish within "
                f"{duration_seconds + grace:.0f}s and were abandoned"
            )

    total = counters["reads"] + counters["writes"]
    return ConcurrencyResult(
        clients=clients,
        duration_seconds=elapsed,
        read_ratio=read_ratio,
        total_operations=total,
        reads=counters["reads"],
        writes=counters["writes"],
        errors=len(errors),
        throughput_qps=total / elapsed if elapsed > 0 else 0.0,
        p50_ms=percentile(latencies, 50) if latencies else float("nan"),
        p95_ms=percentile(latencies, 95) if latencies else float("nan"),
        p99_ms=percentile(latencies, 99) if latencies else float("nan"),
        error_samples=errors[:3],
    )


def run_sweep(
    adapter_factory,
    plan,
    client_levels: list[int],
    duration_seconds: float,
    read_ratio: float,
    cleanup_adapter,
    seed: int = 1337,
) -> list[ConcurrencyResult]:
    """Run the mixed workload at each concurrency level, cleaning up between.

    A single concurrency number is nearly meaningless in isolation: it cannot
    distinguish a database that scales from one that was already saturated at
    one client. Sweeping shows the shape of the curve.
    """
    results = []
    for level in client_levels:
        result = run_mixed_workload(
            adapter_factory, plan, level, duration_seconds, read_ratio, seed
        )
        results.append(result)
        removed = cleanup_adapter.delete_benchmark_edges()
        result.error_samples = result.error_samples[:3]
        if removed != result.writes:
            # Surfaced rather than swallowed: a mismatch means writes were lost
            # or cleanup was incomplete, either of which taints later levels.
            result.error_samples.append(
                f"cleanup mismatch: {result.writes} writes vs {removed} removed"
            )
    return results
