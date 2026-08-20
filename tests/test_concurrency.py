import threading
import time

import pytest

from bench.concurrency import ConcurrencyResult, run_mixed_workload, run_sweep


class FakePlan:
    start_nodes = [1, 2, 3]
    write_pairs = [(1, 2), (2, 3)]


class CountingAdapter:
    """Adapter double that records reads and writes across all instances."""

    lock = threading.Lock()
    reads = 0
    writes = 0
    instances = 0
    connect_should_fail = False

    def __init__(self):
        with CountingAdapter.lock:
            CountingAdapter.instances += 1

    def connect(self):
        if CountingAdapter.connect_should_fail:
            raise ConnectionError("simulated connect failure")

    def close(self):
        pass

    def one_hop(self, node):
        with CountingAdapter.lock:
            CountingAdapter.reads += 1
        time.sleep(0.001)
        return 1

    def insert_edge(self, src, dst):
        with CountingAdapter.lock:
            CountingAdapter.writes += 1
        time.sleep(0.001)

    def delete_benchmark_edges(self):
        with CountingAdapter.lock:
            removed, CountingAdapter.writes = CountingAdapter.writes, 0
        return removed

    @classmethod
    def reset_counters(cls):
        cls.reads = cls.writes = cls.instances = 0
        cls.connect_should_fail = False


@pytest.fixture(autouse=True)
def _reset():
    CountingAdapter.reset_counters()
    yield
    CountingAdapter.reset_counters()


class TestMixedWorkload:
    def test_each_worker_gets_its_own_adapter(self):
        run_mixed_workload(CountingAdapter, FakePlan(), clients=4, duration_seconds=0.3)
        assert CountingAdapter.instances == 4

    def test_operations_recorded(self):
        r = run_mixed_workload(CountingAdapter, FakePlan(), clients=2, duration_seconds=0.4)
        assert r.total_operations > 0
        assert r.total_operations == r.reads + r.writes
        assert r.throughput_qps > 0

    def test_read_ratio_respected(self):
        r = run_mixed_workload(
            CountingAdapter, FakePlan(), clients=2, duration_seconds=0.6, read_ratio=1.0
        )
        assert r.writes == 0
        assert r.reads > 0

    def test_write_only_ratio(self):
        r = run_mixed_workload(
            CountingAdapter, FakePlan(), clients=2, duration_seconds=0.4, read_ratio=0.0
        )
        assert r.reads == 0
        assert r.writes > 0

    def test_connect_failure_reported_not_raised(self):
        CountingAdapter.connect_should_fail = True
        r = run_mixed_workload(CountingAdapter, FakePlan(), clients=2, duration_seconds=0.2)
        assert r.errors == 2
        assert r.total_operations == 0
        assert any("connect" in e for e in r.error_samples)

    def test_percentiles_present(self):
        r = run_mixed_workload(CountingAdapter, FakePlan(), clients=2, duration_seconds=0.4)
        assert r.p50_ms > 0
        assert r.p95_ms >= r.p50_ms
        assert r.p99_ms >= r.p95_ms

    def test_to_dict_serialisable(self):
        r = run_mixed_workload(CountingAdapter, FakePlan(), clients=1, duration_seconds=0.2)
        d = r.to_dict()
        assert d["clients"] == 1
        assert "throughput_qps" in d


class TestSweep:
    def test_runs_each_level(self):
        cleanup = CountingAdapter()
        results = run_sweep(
            CountingAdapter, FakePlan(), [1, 2], 0.2, 0.8, cleanup_adapter=cleanup
        )
        assert [r.clients for r in results] == [1, 2]

    def test_cleanup_runs_between_levels(self):
        cleanup = CountingAdapter()
        run_sweep(CountingAdapter, FakePlan(), [1, 2], 0.3, 0.5, cleanup_adapter=cleanup)
        # Counter is zeroed by delete_benchmark_edges after the final level.
        assert CountingAdapter.writes == 0

    def test_cleanup_mismatch_is_surfaced(self):
        class LyingCleanup(CountingAdapter):
            def delete_benchmark_edges(self):
                return 0  # claims nothing removed regardless of writes

        results = run_sweep(
            CountingAdapter, FakePlan(), [2], 0.4, 0.0, cleanup_adapter=LyingCleanup()
        )
        assert any("cleanup mismatch" in s for s in results[0].error_samples)


class TestHangResistance:
    """A benchmark harness must never hang; a recorded timeout beats a stuck run."""

    def test_stuck_worker_is_abandoned_and_reported(self):
        class HangingAdapter:
            def __init__(self):
                pass

            def connect(self):
                pass

            def close(self):
                pass

            def one_hop(self, node):
                time.sleep(30)  # simulates a response that never arrives
                return 1

            def insert_edge(self, src, dst):
                time.sleep(30)

        started = time.perf_counter()
        result = run_mixed_workload(
            HangingAdapter, FakePlan(), clients=2, duration_seconds=0.5,
            join_grace_seconds=0.5,
        )
        elapsed = time.perf_counter() - started

        # Must return promptly rather than waiting on the stuck workers.
        assert elapsed < 10, f"harness hung for {elapsed:.1f}s"
        assert result.errors >= 1
        assert any("did not finish" in e for e in result.error_samples)

    def test_deadline_starts_after_workers_connect(self):
        """Slow connects must not consume the measurement window."""

        class SlowConnectAdapter(CountingAdapter):
            def connect(self):
                time.sleep(0.4)

        result = run_mixed_workload(
            SlowConnectAdapter, FakePlan(), clients=2, duration_seconds=0.5
        )
        # With the deadline set before connecting, the window would already be
        # spent and no operations would run at all.
        assert result.total_operations > 0
