import math

import pytest

from bench.stats import LatencySummary, percentile, stdev


class TestPercentile:
    def test_nearest_rank_returns_observed_value(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        # Every percentile must be a value that actually appears in the input.
        for pct in (1, 25, 50, 75, 95, 99, 100):
            assert percentile(values, pct) in values

    def test_known_percentiles(self):
        values = list(range(1, 101))  # 1..100
        assert percentile(values, 50) == 50
        assert percentile(values, 95) == 95
        assert percentile(values, 99) == 99
        assert percentile(values, 100) == 100

    def test_unsorted_input(self):
        assert percentile([5.0, 1.0, 3.0, 2.0, 4.0], 50) == 3.0

    def test_single_value(self):
        assert percentile([7.0], 50) == 7.0
        assert percentile([7.0], 95) == 7.0

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            percentile([], 50)

    @pytest.mark.parametrize("bad", [0, -1, 101])
    def test_out_of_range_raises(self, bad):
        with pytest.raises(ValueError):
            percentile([1.0], bad)


class TestStdev:
    def test_known_value(self):
        # Sample stdev of 2,4,4,4,5,5,7,9 is 2.13809...
        assert stdev([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(2.13809, rel=1e-4)

    def test_identical_values_is_zero(self):
        assert stdev([3.0] * 10) == 0.0

    def test_fewer_than_two_is_zero(self):
        assert stdev([]) == 0.0
        assert stdev([1.0]) == 0.0


class TestLatencySummary:
    def test_from_samples(self):
        s = LatencySummary.from_samples("1-hop", [float(v) for v in range(1, 101)])
        assert s.iterations == 100
        assert s.p50 == 50.0
        assert s.p95 == 95.0
        assert s.min == 1.0
        assert s.max == 100.0
        assert s.errors == 0

    def test_empty_samples_reports_nan_not_absence(self):
        # A totally failed workload must still appear, with errors recorded.
        s = LatencySummary.from_samples("3-hop", [], errors=100, error_samples=["timeout"])
        assert s.iterations == 0
        assert math.isnan(s.p50)
        assert s.errors == 100
        assert s.error_samples == ["timeout"]

    def test_error_samples_truncated(self):
        s = LatencySummary.from_samples("x", [1.0], errors=9, error_samples=["e"] * 9)
        assert len(s.error_samples) == 3

    def test_to_dict_roundtrip(self):
        s = LatencySummary.from_samples("lookup", [1.0, 2.0, 3.0])
        d = s.to_dict()
        assert d["workload"] == "lookup"
        assert d["iterations"] == 3
