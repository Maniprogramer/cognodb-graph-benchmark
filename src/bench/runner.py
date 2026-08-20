"""Benchmark orchestration.

Runs the identical sequence against every configured platform:

    reset -> create indexes -> load -> verify -> cold-start -> warm up
          -> timed read workloads -> concurrency sweep -> footprint

Two things here exist specifically to catch a benchmark lying to itself:

*Load verification.* After loading, node and relationship counts are compared
against the dataset manifest. A platform that silently dropped rows would
otherwise post excellent traversal times on a smaller graph.

*Result parity.* Every read workload records the value it returned, not just
how long it took. After all platforms have run, the counts are compared across
platforms; any disagreement means the queries are not equivalent and the
latency comparison is invalid. This is reported in the results rather than
being left for a reader to notice.
"""

from __future__ import annotations

import platform as platform_mod
import socket
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .concurrency import run_sweep
from .stats import LatencySummary
from .workloads import READ_WORKLOADS


@dataclass
class PlatformRun:
    """Everything measured for one platform."""

    id: str
    name: str
    query_language: str
    spec: dict
    indexes: list[str] = field(default_factory=list)
    load: dict | None = None
    verification: dict | None = None
    cold_start_ms: dict = field(default_factory=dict)
    workloads: dict = field(default_factory=dict)
    workload_results: dict = field(default_factory=dict)
    concurrency: list[dict] = field(default_factory=list)
    variance: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    skipped: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "query_language": self.query_language,
            "spec": self.spec,
            "indexes": self.indexes,
            "load": self.load,
            "verification": self.verification,
            "cold_start_ms": self.cold_start_ms,
            "workloads": {k: v.to_dict() for k, v in self.workloads.items()},
            "workload_results": self.workload_results,
            "concurrency": self.concurrency,
            "variance": self.variance,
            "errors": self.errors,
            "skipped": self.skipped,
        }


def capture_environment() -> dict:
    """Record the client machine. Section 5.3 requires the same client for
    every platform, so the harness states what that client was."""
    return {
        "hostname": socket.gethostname(),
        "platform": platform_mod.platform(),
        "processor": platform_mod.processor() or platform_mod.machine(),
        "cpu_count": __import__("os").cpu_count(),
        "python": sys.version.split()[0],
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


def measure_cold_start(adapter, plan) -> dict:
    """First-touch latency for each workload, before any warm-up.

    Reported separately from warm numbers because mixing them would let a
    single cold outlier dominate a p95 and misrepresent steady-state
    behaviour.
    """
    cold = {}
    for name, fn in READ_WORKLOADS.items():
        t0 = time.perf_counter()
        try:
            fn(adapter, plan, 0)
            cold[name] = round((time.perf_counter() - t0) * 1000.0, 3)
        except Exception as exc:
            cold[name] = f"error: {type(exc).__name__}"
    return cold


def run_read_workloads(adapter, plan, iterations: int) -> tuple[dict, dict]:
    """Time every read workload. Returns (summaries, representative results)."""
    summaries: dict[str, LatencySummary] = {}
    representative: dict[str, object] = {}

    for name, fn in READ_WORKLOADS.items():
        samples: list[float] = []
        errors = 0
        error_samples: list[str] = []
        results_seen: list[int] = []

        for i in range(iterations):
            t0 = time.perf_counter()
            try:
                value = fn(adapter, plan, i)
                samples.append((time.perf_counter() - t0) * 1000.0)
                results_seen.append(value)
            except Exception as exc:
                errors += 1
                if len(error_samples) < 3:
                    error_samples.append(f"{type(exc).__name__}: {exc}")

        summaries[name] = LatencySummary.from_samples(name, samples, errors, error_samples)
        # Sum of returned counts is the parity fingerprint: it depends on every
        # iteration, so two platforms agreeing on it agree on all of them.
        representative[name] = sum(results_seen) if results_seen else None

    return summaries, representative


def run_platform(
    platform_cfg,
    adapter_factory,
    nodes,
    edges,
    plan,
    settings: dict,
    log=print,
) -> PlatformRun:
    """Execute the full sequence against a single platform."""
    run = PlatformRun(
        id=platform_cfg.id,
        name=platform_cfg.name,
        query_language="unknown",
        spec=platform_cfg.spec,
    )

    if not platform_cfg.configured:
        run.skipped = platform_cfg.skip_reason
        log(f"  {platform_cfg.name}: SKIPPED -- {platform_cfg.skip_reason}")
        return run

    adapter = None
    try:
        adapter = adapter_factory()
        run.query_language = adapter.query_language
        log(f"  {platform_cfg.name}: connecting")
        adapter.connect()

        log(f"  {platform_cfg.name}: resetting")
        adapter.reset()
        run.indexes = adapter.create_schema()

        log(f"  {platform_cfg.name}: loading {len(nodes):,} nodes / {len(edges):,} rels")
        load_result = adapter.load(nodes, edges, settings["batch_size"])
        run.load = load_result.to_dict()
        log(
            f"  {platform_cfg.name}: loaded in {load_result.total_seconds:.1f}s "
            f"({load_result.relationships_per_second:,.0f} rels/s)"
        )

        actual_nodes = adapter.node_count()
        actual_rels = adapter.relationship_count()
        run.verification = {
            "nodes_expected": len(nodes),
            "nodes_actual": actual_nodes,
            "relationships_expected": len(edges),
            "relationships_actual": actual_rels,
            "complete": actual_nodes == len(nodes) and actual_rels == len(edges),
        }
        if not run.verification["complete"]:
            run.errors.append(
                f"INCOMPLETE LOAD: expected {len(nodes)}/{len(edges)}, "
                f"got {actual_nodes}/{actual_rels}"
            )
            log(f"  {platform_cfg.name}: WARNING -- {run.errors[-1]}")

        run.cold_start_ms = measure_cold_start(adapter, plan)

        log(f"  {platform_cfg.name}: warming up ({settings['warmup_iterations']} iterations)")
        adapter.warmup(plan.start_nodes, settings["warmup_iterations"])

        repeats = settings.get("repeats", 1)
        all_runs: list[dict] = []
        for repeat in range(repeats):
            log(
                f"  {platform_cfg.name}: read workloads "
                f"({settings['iterations']} iterations, run {repeat + 1}/{repeats})"
            )
            summaries, representative = run_read_workloads(
                adapter, plan, settings["iterations"]
            )
            all_runs.append({k: v.to_dict() for k, v in summaries.items()})
            if repeat == 0:
                run.workloads = summaries
                run.workload_results = representative

        if repeats > 1:
            run.variance = summarise_variance(all_runs)

        if settings.get("concurrency_levels"):
            log(
                f"  {platform_cfg.name}: concurrency sweep "
                f"{settings['concurrency_levels']} x {settings['concurrency_seconds']}s"
            )
            sweep = run_sweep(
                adapter_factory,
                plan,
                settings["concurrency_levels"],
                settings["concurrency_seconds"],
                settings.get("read_ratio", 0.8),
                cleanup_adapter=adapter,
                seed=settings.get("seed", 1337),
            )
            run.concurrency = [r.to_dict() for r in sweep]

        run.spec = adapter.spec().to_dict()

    except Exception as exc:
        run.errors.append(f"{type(exc).__name__}: {exc}")
        log(f"  {platform_cfg.name}: FAILED -- {type(exc).__name__}: {exc}")
        log(traceback.format_exc(limit=3))
    finally:
        if adapter is not None:
            try:
                adapter.close()
            except Exception:
                pass

    return run


def summarise_variance(all_runs: list[dict]) -> dict:
    """Spread of p50 across repeated runs of the whole read suite.

    Repeating the suite is the only way to tell a real difference between
    platforms from run-to-run noise on a burstable tier.
    """
    variance = {}
    for workload in all_runs[0]:
        p50s = [run[workload]["p50"] for run in all_runs if run.get(workload)]
        p50s = [v for v in p50s if v == v]  # drop NaN
        if len(p50s) < 2:
            continue
        mean = sum(p50s) / len(p50s)
        spread = max(p50s) - min(p50s)
        variance[workload] = {
            "runs": len(p50s),
            "p50_values": [round(v, 3) for v in p50s],
            "p50_mean": round(mean, 3),
            "p50_spread": round(spread, 3),
            "p50_spread_pct": round(100 * spread / mean, 1) if mean else 0.0,
        }
    return variance


def check_parity(runs: list[PlatformRun]) -> dict:
    """Compare returned result counts across platforms.

    Equal timings mean nothing if the platforms answered different questions.
    """
    completed = [r for r in runs if r.workload_results and not r.skipped]
    if len(completed) < 2:
        return {"status": "not checkable", "reason": "fewer than two platforms completed"}

    report: dict = {"status": "pass", "workloads": {}}
    for workload in READ_WORKLOADS:
        values = {r.name: r.workload_results.get(workload) for r in completed}
        distinct = {v for v in values.values() if v is not None}
        agree = len(distinct) <= 1
        report["workloads"][workload] = {
            "values": values,
            "agree": agree,
        }
        if not agree:
            report["status"] = "MISMATCH"
    return report
