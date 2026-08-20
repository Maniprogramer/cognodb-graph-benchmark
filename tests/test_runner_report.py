"""Tests for the runner's self-checks and the report renderer.

A fake adapter stands in for a database so the guard rails -- load
verification, parity detection, variance -- can be tested against known-bad
inputs that a real database would not conveniently produce.
"""

import json
import time
import math

import pytest

from bench.report import _fmt, _table, table_parity, write_report
from bench.runner import (
    PlatformRun,
    check_parity,
    measure_cold_start,
    run_read_workloads,
    summarise_variance,
)


class FakeAdapter:
    """Adapter double with controllable results and failures."""

    query_language = "Fake"

    def __init__(self, hop_result=5, fail_on=None):
        self.hop_result = hop_result
        self.fail_on = fail_on or set()
        self.calls = 0

    def _maybe_fail(self, name):
        self.calls += 1
        if name in self.fail_on:
            raise RuntimeError(f"simulated {name} failure")

    def one_hop(self, n): self._maybe_fail("1-hop"); return self.hop_result
    def two_hop(self, n): self._maybe_fail("2-hop"); return self.hop_result * 2
    def three_hop(self, n): self._maybe_fail("3-hop"); return self.hop_result * 3
    def point_lookup(self, n): self._maybe_fail("point-lookup"); return 1
    def filtered_lookup(self, d, y): self._maybe_fail("filtered-lookup"); return 7
    def aggregation(self): self._maybe_fail("aggregation"); return 11


class FakePlan:
    start_nodes = [1, 2, 3]
    point_lookup_ids = [1, 2, 3]
    filtered_min_degree = 5
    filtered_year = 2001


class TestRunReadWorkloads:
    def test_all_workloads_measured(self):
        summaries, results = run_read_workloads(FakeAdapter(), FakePlan(), iterations=5)
        assert set(summaries) == {
            "1-hop", "2-hop", "3-hop", "point-lookup", "filtered-lookup", "aggregation",
        }
        assert all(s.iterations == 5 for s in summaries.values())

    def test_failures_counted_not_swallowed(self):
        adapter = FakeAdapter(fail_on={"3-hop"})
        summaries, results = run_read_workloads(adapter, FakePlan(), iterations=5)
        assert summaries["3-hop"].errors == 5
        assert summaries["3-hop"].iterations == 0
        assert math.isnan(summaries["3-hop"].p50)
        assert results["3-hop"] is None
        # Other workloads still succeed.
        assert summaries["1-hop"].errors == 0

    def test_result_fingerprint_is_sum_of_all_iterations(self):
        _, results = run_read_workloads(FakeAdapter(hop_result=4), FakePlan(), iterations=10)
        assert results["1-hop"] == 40
        assert results["2-hop"] == 80


class TestColdStart:
    def test_records_every_workload(self):
        cold = measure_cold_start(FakeAdapter(), FakePlan())
        assert len(cold) == 6
        assert all(isinstance(v, float) for v in cold.values())

    def test_error_recorded_as_string(self):
        cold = measure_cold_start(FakeAdapter(fail_on={"aggregation"}), FakePlan())
        assert isinstance(cold["aggregation"], str)
        assert "error" in cold["aggregation"]


class TestParityCheck:
    def _run(self, name, results):
        r = PlatformRun(id=name.lower(), name=name, query_language="x", spec={})
        r.workload_results = results
        r.workloads = {"1-hop": object()}
        return r

    def test_agreement_passes(self):
        a = self._run("A", {"1-hop": 100, "2-hop": 200})
        b = self._run("B", {"1-hop": 100, "2-hop": 200})
        report = check_parity([a, b])
        assert report["status"] == "pass"

    def test_disagreement_flagged(self):
        a = self._run("A", {"1-hop": 100})
        b = self._run("B", {"1-hop": 999})
        report = check_parity([a, b])
        assert report["status"] == "MISMATCH"
        assert report["workloads"]["1-hop"]["agree"] is False

    def test_none_values_ignored_for_agreement(self):
        # A platform that failed a workload should not be treated as disagreeing.
        a = self._run("A", {"1-hop": 100})
        b = self._run("B", {"1-hop": None})
        assert check_parity([a, b])["workloads"]["1-hop"]["agree"] is True

    def test_single_platform_not_checkable(self):
        assert check_parity([self._run("A", {"1-hop": 1})])["status"] == "not checkable"


class TestVariance:
    def test_spread_computed(self):
        runs = [
            {"1-hop": {"p50": 10.0}},
            {"1-hop": {"p50": 12.0}},
            {"1-hop": {"p50": 11.0}},
        ]
        v = summarise_variance(runs)["1-hop"]
        assert v["runs"] == 3
        assert v["p50_spread"] == pytest.approx(2.0)
        assert v["p50_spread_pct"] == pytest.approx(18.2, abs=0.1)

    def test_nan_runs_dropped(self):
        runs = [{"1-hop": {"p50": float("nan")}}, {"1-hop": {"p50": 5.0}}]
        assert "1-hop" not in summarise_variance(runs)


class TestFormatting:
    def test_nan_renders_as_dash(self):
        assert _fmt(float("nan")) == "—"

    def test_none_renders_as_dash(self):
        assert _fmt(None) == "—"

    def test_thousands_separator(self):
        assert _fmt(1234.5) == "1,234.50"
        assert _fmt(1234) == "1,234"

    def test_empty_table_states_no_data(self):
        assert "_No data._" in _table(["a"], [])


@pytest.fixture
def sample_results():
    return {
        "run_id": "TEST",
        "environment": {
            "platform": "test-os", "cpu_count": 4, "python": "3.13.0",
            "timestamp_utc": "2026-01-01T00:00:00Z",
        },
        "parity_tier": {"vcpu": "0.5"},
        "settings": {
            "iterations": 10, "warmup_iterations": 2, "repeats": 1,
            "batch_size": 100, "concurrency_levels": [1], "read_ratio": 0.8,
        },
        "dataset": {
            "name": "D", "source_url": "http://x", "node_count": 10,
            "relationship_count": 20, "sampled": False,
            "nodes_with_known_year": 5, "year_coverage_pct": 50.0,
            "nodes_csv_sha256": "a" * 64, "edges_csv_sha256": "b" * 64,
        },
        "plan": {},
        "parity_check": {
            "status": "pass",
            "workloads": {"1-hop": {"values": {"A": 5}, "agree": True}},
        },
        "platforms": [
            {
                "id": "a", "name": "A", "query_language": "Cypher",
                "spec": {"tier": "Free", "vcpu": "0.5", "ram": "256 MB",
                         "storage": "1 GB", "observable": {"status": "not observable"}},
                "indexes": ["X.y"],
                "load": {"nodes_per_second": 100.0, "relationships_per_second": 200.0,
                         "total_seconds": 1.0, "method": "driver"},
                "verification": {"nodes_actual": 10, "relationships_actual": 20,
                                 "complete": True},
                "cold_start_ms": {"1-hop": 5.0},
                "workloads": {"1-hop": {"p50": 1.0, "p95": 2.0}},
                "workload_results": {"1-hop": 5},
                "concurrency": [{"clients": 1, "throughput_qps": 50.0, "p50_ms": 1.0,
                                 "p95_ms": 2.0, "p99_ms": 3.0, "reads": 40,
                                 "writes": 10, "errors": 0}],
                "variance": {}, "errors": [], "skipped": None,
            },
            {
                "id": "b", "name": "B", "query_language": "unknown", "spec": {},
                "indexes": [], "load": None, "verification": None,
                "cold_start_ms": {}, "workloads": {}, "workload_results": {},
                "concurrency": [], "variance": [], "errors": [],
                "skipped": "not configured (missing: uri)",
            },
        ],
    }


class TestWriteReport:
    def test_report_written_with_sections(self, sample_results, tmp_path):
        path = write_report(sample_results, tmp_path)
        text = path.read_text()
        for heading in ("# Benchmark results", "## Environment", "## Dataset",
                        "## Result parity check", "## Ingest throughput",
                        "## Traversal latency", "## Footprint"):
            assert heading in text

    def test_skipped_platform_appears_in_report(self, sample_results, tmp_path):
        text = write_report(sample_results, tmp_path).read_text()
        assert "not configured (missing: uri)" in text
        assert "Errors and skipped platforms" in text

    def test_charts_rendered(self, sample_results, tmp_path):
        write_report(sample_results, tmp_path)
        charts = list((tmp_path / "charts").glob("*.png"))
        assert {c.name for c in charts} >= {"ingest_throughput.png", "traversal_p50.png"}

    def test_incomplete_load_is_flagged_loudly(self, sample_results, tmp_path):
        sample_results["platforms"][0]["verification"]["complete"] = False
        text = write_report(sample_results, tmp_path).read_text()
        assert "**NO**" in text

    def test_parity_mismatch_flagged_loudly(self, sample_results):
        sample_results["parity_check"]["workloads"]["1-hop"]["agree"] = False
        assert "**NO**" in table_parity(sample_results)


class TestReadmeInjection:
    """The README's results matrix is generated, so it cannot go stale."""

    def test_injects_between_markers(self, sample_results, tmp_path):
        from bench.report import README_END, README_START, inject_readme

        readme = tmp_path / "README.md"
        readme.write_text(f"# Title\n\n{README_START}\n\nplaceholder\n\n{README_END}\n\n## After\n")
        assert inject_readme(sample_results, readme) is True

        text = readme.read_text()
        assert "placeholder" not in text
        assert "### Result parity" in text
        # Content outside the markers must survive untouched.
        assert text.startswith("# Title")
        assert text.rstrip().endswith("## After")

    def test_missing_markers_is_a_no_op(self, sample_results, tmp_path):
        from bench.report import inject_readme

        readme = tmp_path / "README.md"
        readme.write_text("# No markers here\n")
        assert inject_readme(sample_results, readme) is False
        assert readme.read_text() == "# No markers here\n"

    def test_missing_file_is_a_no_op(self, sample_results, tmp_path):
        from bench.report import inject_readme

        assert inject_readme(sample_results, tmp_path / "absent.md") is False

    def test_reinjection_is_idempotent(self, sample_results, tmp_path):
        from bench.report import README_END, README_START, inject_readme

        readme = tmp_path / "README.md"
        readme.write_text(f"# T\n\n{README_START}\n\nold\n\n{README_END}\n\n## Tail\n")
        inject_readme(sample_results, readme)
        first = readme.read_text()
        inject_readme(sample_results, readme)
        assert readme.read_text() == first

    def test_parity_mismatch_warns_in_readme_section(self, sample_results):
        from bench.report import build_results_section

        sample_results["parity_check"]["status"] = "MISMATCH"
        assert "Result parity FAILED" in build_results_section(sample_results)


class TestWarmup:
    """Warming a subset of workloads biases the comparison, so all six run."""

    class RecordingAdapter:
        def __init__(self):
            self.touched = set()

        def one_hop(self, n): self.touched.add("1-hop"); return 1
        def two_hop(self, n): self.touched.add("2-hop"); return 1
        def three_hop(self, n): self.touched.add("3-hop"); return 1
        def point_lookup(self, n): self.touched.add("point-lookup"); return 1
        def filtered_lookup(self, d, y): self.touched.add("filtered-lookup"); return 1
        def aggregation(self): self.touched.add("aggregation"); return 1

    def _adapter(self):
        from bench.adapters.base import GraphAdapter

        adapter = self.RecordingAdapter()
        adapter.warmup = GraphAdapter.warmup.__get__(adapter)
        return adapter

    def test_every_measured_workload_is_warmed(self):
        from bench.workloads import READ_WORKLOADS

        adapter = self._adapter()
        adapter.warmup([1, 2, 3], iterations=2, plan=FakePlan())
        assert adapter.touched == set(READ_WORKLOADS)

    def test_expensive_traversal_included(self):
        adapter = self._adapter()
        adapter.warmup([1], iterations=1, plan=FakePlan())
        assert "3-hop" in adapter.touched

    def test_works_without_a_plan(self):
        # filtered_lookup needs plan constants; the rest must still warm.
        adapter = self._adapter()
        adapter.warmup([1], iterations=1, plan=None)
        assert "3-hop" in adapter.touched
        assert "filtered-lookup" not in adapter.touched


class TestBaselineRtt:
    """The transport floor is what makes a remote/local comparison readable."""

    class NoopAdapter:
        def __init__(self, delay=0.0):
            self.delay = delay
            self.calls = 0

        def _noop_query(self):
            self.calls += 1
            if self.delay:
                time.sleep(self.delay)

    def _adapter(self, delay=0.0):
        from bench.adapters.base import GraphAdapter

        a = self.NoopAdapter(delay)
        a.timed = GraphAdapter.timed.__get__(a)
        a.baseline_rtt_ms = GraphAdapter.baseline_rtt_ms.__get__(a)
        return a

    def test_reports_percentiles(self):
        a = self._adapter()
        result = a.baseline_rtt_ms(iterations=5)
        assert result["iterations"] == 5
        assert a.calls == 5
        assert result["p95"] >= result["p50"] >= result["min"]

    def test_measures_actual_delay(self):
        a = self._adapter(delay=0.02)
        result = a.baseline_rtt_ms(iterations=3)
        assert result["p50"] >= 15  # ~20ms, allowing scheduler slack

    def test_every_adapter_implements_noop(self):
        from bench.adapters.arango import ArangoAdapter
        from bench.adapters.bolt import BoltAdapter
        from bench.adapters.falkor import FalkorAdapter

        for cls in (BoltAdapter, ArangoAdapter, FalkorAdapter):
            assert "_noop_query" in cls.__dict__, f"{cls.__name__} missing _noop_query"


class TestBaselineTable:
    def test_shows_transport_share_of_latency(self, sample_results):
        from bench.report import table_baseline

        sample_results["platforms"][0]["baseline_rtt"] = {"p50": 0.5, "p95": 0.8, "min": 0.4}
        sample_results["platforms"][0]["workloads"]["1-hop"]["p50"] = 1.0
        table = table_baseline(sample_results)
        assert "50%" in table  # 0.5ms of a 1.0ms query is transport

    def test_error_surfaced(self, sample_results):
        from bench.report import table_baseline

        sample_results["platforms"][0]["baseline_rtt"] = {"error": "TimeoutError: x"}
        assert "TimeoutError" in table_baseline(sample_results)


class TestBaselineShareRendering:
    """A floor above the measured workload means 'unmeasurable', not '158%'."""

    def test_share_at_floor_when_baseline_exceeds_workload(self, sample_results):
        from bench.report import table_baseline

        sample_results["platforms"][0]["baseline_rtt"] = {"p50": 0.67, "p95": 2.7, "min": 0.6}
        sample_results["platforms"][0]["workloads"]["1-hop"]["p50"] = 0.42
        table = table_baseline(sample_results)
        assert "at floor" in table
        assert "%" not in table.split("at floor")[0].split("|")[-2]

    def test_normal_share_still_percentage(self, sample_results):
        from bench.report import table_baseline

        sample_results["platforms"][0]["baseline_rtt"] = {"p50": 0.5, "p95": 0.8, "min": 0.4}
        sample_results["platforms"][0]["workloads"]["1-hop"]["p50"] = 2.0
        assert "25%" in table_baseline(sample_results)


class TestSmokeRunIsolation:
    """--quick must never overwrite a reporting run's results or README."""

    def test_readme_not_injected_from_a_scratch_subdirectory(self, sample_results, tmp_path):
        from bench.report import README_END, README_START, write_report

        # Layout mimicking repo/results/smoke/ -- README lives two levels up.
        repo = tmp_path
        (repo / "src" / "bench").mkdir(parents=True)
        readme = repo / "README.md"
        original = f"# T\n\n{README_START}\n\nPUBLISHED\n\n{README_END}\n"
        readme.write_text(original)

        write_report(sample_results, repo / "results" / "smoke")
        assert readme.read_text() == original, "smoke run clobbered the published README"

    def test_readme_injected_from_canonical_results_dir(self, sample_results, tmp_path):
        from bench.report import README_END, README_START, write_report

        repo = tmp_path
        (repo / "src" / "bench").mkdir(parents=True)
        readme = repo / "README.md"
        readme.write_text(f"# T\n\n{README_START}\n\nPLACEHOLDER\n\n{README_END}\n")

        write_report(sample_results, repo / "results")
        assert "PLACEHOLDER" not in readme.read_text()
